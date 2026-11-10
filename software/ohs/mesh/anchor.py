"""Laser anchoring: pull the dense surface onto the laser points with a smooth correction
(research plan 4.2.4, "Refine with lasers").

The laser lines are triangulated on calibrated planes, independently of the dense stereo, so
they are free of the dense depth's slow biases (calibration, real optics). This module measures
the mesh against the laser cloud and moves the vertices along their normals by a smooth field
that explains the disagreement:

1. **Residuals.** Each laser point is paired with its nearest vertex; its residual is its signed
   distance along that vertex's normal (+ = the laser sees the surface further out). Points that
   project off the mesh (an edge, a hole) are ignored; points more than ``gate_mm`` away are
   outliers against the dense surface (glints, reflections).
2. **A smooth field** by normalized convolution on the mesh: the weighted residuals and the
   weights are both blurred by heat diffusion (a random walk on the mesh graph, so the two sides
   of a thin wall never mix) with a Gaussian scale ``smooth_mm``, and divided. Where the laser
   coverage thins out (below ``coverage`` of the typical density), the field fades to zero.
3. **Robust re-weighting** (Tukey) drops the points the field can't explain, ``iterations``
   times.
4. **Only significant corrections:** the laser cloud has its own correlated errors (one line
   per frame shares its plane and its centre biases). A correction under ``min_mm`` is taken as
   that error and not applied; from ``2 * min_mm`` it is applied in full (a smooth step between).

On the simulator (``VALIDATION.md``, "Refine with lasers"): a 0.25 mm smooth depth bias near the
laser lines drops to 0.05-0.06 mm (median error), and without a bias the mesh is unchanged
within 1 %. Each vertex keeps its sigma (the correction is smooth; the random error it carries
stays).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from ohs.mesh.topology import as_mesh, edge_table, rows, vertex_normals

__all__ = ["AnchorParams", "anchor_to_points"]

Progress = Callable[[float, str], None]


@dataclass(frozen=True)
class AnchorParams:
    smooth_mm: float = 5.0  # Gaussian scale of the correction field
    min_mm: float = 0.1  # corrections below are the lasers' own error; in full from twice this
    gate_mm: float = 2.0  # laser points farther from the surface are outliers
    lateral: float = 1.5  # x the median edge: points projecting farther from their vertex are off the mesh
    coverage: float = 0.1  # full strength where the laser density is at least this share of the typical
    iterations: int = 3  # robust re-weighting rounds


def _stats(x: np.ndarray) -> dict:
    if not len(x):
        return {"median": None, "p95": None, "mean": None}
    return {"median": round(float(np.median(np.abs(x))), 4), "p95": round(float(np.percentile(np.abs(x), 95)), 4),
            "mean": round(float(np.mean(x)), 4)}


def _smoothstep(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def anchor_to_points(V: np.ndarray, F: np.ndarray, P: np.ndarray, params: AnchorParams | None = None, *,
                     N: np.ndarray | None = None, progress: Progress | None = None
                     ) -> tuple[np.ndarray, np.ndarray, dict]:
    """Pull the mesh ``(V, F)`` onto the laser points ``P`` (M,3), all in mm in one frame.

    Returns ``(V2, delta, report)``: the moved vertices, each vertex's offset along its normal
    (mm), and what happened: ``points``, ``on_surface`` (projecting onto the mesh), ``outliers``
    (beyond ``gate_mm``, or rejected by the robust fit), ``used``; ``residual_before`` /
    ``residual_after`` (``median`` and ``p95`` of the absolute residuals of the used points,
    ``mean`` signed, mm); ``moved_share`` (vertices moved by at least 0.01 mm) and ``moved``
    (``median``, ``p95``, ``max`` of their offsets, mm); ``ms``.
    """
    from scipy.sparse import coo_matrix, diags, identity  # noqa: PLC0415
    from scipy.spatial import cKDTree  # noqa: PLC0415

    p = params or AnchorParams()
    t0 = time.perf_counter()
    say = progress or (lambda f, m: None)
    V, F = as_mesh(V, F)
    P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
    P = P[np.all(np.isfinite(P), axis=1)]
    n = len(V)
    report: dict = {"points": int(len(P)), "on_surface": 0, "outliers": 0, "used": 0,
                    "residual_before": _stats(np.zeros(0)), "residual_after": _stats(np.zeros(0)),
                    "moved_share": 0.0, "moved": {"median": None, "p95": None, "max": None}, "ms": 0.0}
    delta = np.zeros(n)
    if n == 0 or len(F) == 0 or len(P) == 0:
        report["ms"] = round(1e3 * (time.perf_counter() - t0), 1)
        return V.copy(), delta, report

    # 1. residuals along the normals
    say(0.05, "matching the laser points to the surface")
    if N is None:
        N = vertex_normals(V, F)
    et = edge_table(F, n)
    h = float(np.median(np.linalg.norm(rows(V, et.v0) - rows(V, et.v1), axis=1)))
    _, j = cKDTree(V).query(P, workers=-1)
    Nj = rows(N, j)
    rv = P - rows(V, j)
    r = np.einsum("ij,ij->i", rv, Nj)
    lateral = np.linalg.norm(rv - r[:, None] * Nj, axis=1)
    on = lateral <= p.lateral * h
    ok = on & (np.abs(r) <= p.gate_mm)
    report["on_surface"] = int(on.sum())
    if not ok.any():
        report["outliers"] = int(on.sum())
        report["ms"] = round(1e3 * (time.perf_counter() - t0), 1)
        return V.copy(), delta, report

    # 2. the region the field can reach, and the diffusion on it
    say(0.2, "building the correction field")
    reach, _ = cKDTree(P[ok]).query(V, distance_upper_bound=3.0 * p.smooth_mm, workers=-1)
    U = np.flatnonzero(np.isfinite(reach))
    pos = np.full(n, -1, dtype=np.int64)
    pos[U] = np.arange(len(U))
    m = len(U)
    inside = (pos[et.v0] >= 0) & (pos[et.v1] >= 0)
    a0, a1 = pos[et.v0[inside]], pos[et.v1[inside]]
    A = coo_matrix((np.ones(2 * len(a0)), (np.r_[a0, a1], np.r_[a1, a0])), shape=(m, m)).tocsr()
    deg = np.asarray(A.sum(axis=1)).ravel()
    deg[deg == 0] = 1.0
    alpha = 0.8  # a lazy random walk: no oscillation on near-bipartite patches
    S = ((1.0 - alpha) * identity(m, format="csr") + alpha * (diags(1.0 / deg) @ A)).tocsr()
    steps = max(1, int(round(2.0 * p.smooth_mm ** 2 / (alpha * h * h))))  # variance per step: alpha h^2 / 2 per axis

    def blur(x: np.ndarray) -> np.ndarray:
        for _ in range(steps):
            x = S @ x
        return x

    jU = pos[j]
    w = (ok & (jU >= 0)).astype(np.float64)
    field = np.zeros(m)
    for it in range(max(1, int(p.iterations))):
        say(0.25 + 0.65 * it / max(1, p.iterations), "fitting the correction field")
        sel = w > 0
        bw = blur(np.bincount(jU[sel], weights=w[sel], minlength=m))
        br = blur(np.bincount(jU[sel], weights=w[sel] * r[sel], minlength=m))
        typical = float(np.percentile(bw[bw > 0], 90)) if np.any(bw > 0) else 1.0
        q = _smoothstep((bw / typical - 0.1 * p.coverage) / (0.9 * p.coverage))  # fades where the lasers thin out
        field = np.divide(br, bw, out=np.zeros(m), where=bw > 0) * q
        e = r - np.where(jU >= 0, field[np.maximum(jU, 0)], 0.0)
        scale = 1.4826 * float(np.median(np.abs(e[sel]))) if sel.any() else 0.0
        c = 4.685 * max(scale, 0.02)
        u = e / c
        w = np.where(ok & (jU >= 0) & (np.abs(u) < 1.0), (1.0 - u * u) ** 2, 0.0)

    # 3. only the significant part
    say(0.92, "moving the surface")
    delta[U] = field * _smoothstep(np.abs(field) / p.min_mm - 1.0) if p.min_mm > 0 else field
    V2 = V + delta[:, None] * N
    used = w > 0
    moved = np.abs(delta) >= 0.01
    report.update({
        "outliers": int(report["on_surface"] - used.sum()), "used": int(used.sum()),
        "residual_before": _stats(r[used]), "residual_after": _stats((r - delta[j])[used]),
        "moved_share": round(float(moved.mean()), 4),
        "moved": {"median": round(float(np.median(np.abs(delta[moved]))), 4),
                  "p95": round(float(np.percentile(np.abs(delta[moved]), 95)), 4),
                  "max": round(float(np.abs(delta[moved]).max()), 4)} if moved.any() else report["moved"],
        "smooth_mm": p.smooth_mm, "min_mm": p.min_mm, "ms": round(1e3 * (time.perf_counter() - t0), 1),
    })
    return V2, delta, report
