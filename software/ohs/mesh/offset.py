"""How far an edited mesh lies from the measured one (the deviation map).

:func:`surface_offset` gives, for each vertex of the current mesh, its signed distance in mm to a
reference surface: the nearest point on it (Open3D's ``RaycastingScene``, exact on triangles), with
the sign of the reference face's normal there (+ = moved outward, along the normal; - = inward).
The sign comes from the face, not from an inside/outside test, so it also works on open meshes
(a scan is rarely closed). Vertices that only filled faces use were not measured and have nothing
to compare with: they get NaN.

:func:`offset_stats` sums the map up for a legend (per vertex, not per area).
"""
from __future__ import annotations

import numpy as np

from ohs.mesh.topology import as_mesh

__all__ = ["surface_offset", "offset_stats", "MOVED_MM"]

#: a vertex counts as moved beyond this distance (mm): well above float32 rounding, below any real edit
MOVED_MM = 0.01


def surface_offset(V: np.ndarray, V_ref: np.ndarray, F_ref: np.ndarray, *, F: np.ndarray | None = None,
                   filled: np.ndarray | None = None) -> np.ndarray:
    """Signed distance (mm, float32 (N,)) of each vertex of ``V`` to the surface ``(V_ref, F_ref)``.

    ``F`` and ``filled`` (one flag per face of the current mesh): vertices used by no measured
    (not filled) face get NaN. Without a reference surface every value is NaN."""
    V = np.asarray(V, dtype=np.float64).reshape(-1, 3)
    out = np.full(len(V), np.nan, dtype=np.float32)
    V_ref, F_ref = as_mesh(V_ref, F_ref)
    if (F is None) != (filled is None):
        raise ValueError("give both the faces and their filled flags, or neither")
    measured = None
    if F is not None:
        F = np.asarray(F).reshape(-1, 3)
        filled = np.asarray(filled, dtype=bool).reshape(-1)
        if len(filled) != len(F):
            raise ValueError(f"{len(filled)} filled flags for {len(F)} faces")
        measured = np.zeros(len(V), dtype=bool)
        measured[F[~filled]] = True
    if len(V) == 0 or len(F_ref) == 0:
        return out
    import open3d as o3d  # noqa: PLC0415 (optional dependency)

    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.core.Tensor(np.ascontiguousarray(V_ref, dtype=np.float32)),
                        o3d.core.Tensor(np.ascontiguousarray(F_ref, dtype=np.uint32)))
    idx = np.arange(len(V)) if measured is None else np.flatnonzero(measured)
    if len(idx) == 0:
        return out
    P = V[idx]
    ans = scene.compute_closest_points(o3d.core.Tensor(np.ascontiguousarray(P, dtype=np.float32)))
    Q = ans["points"].numpy().astype(np.float64)
    tri = F_ref[ans["primitive_ids"].numpy().astype(np.int64)]
    a = V_ref[tri[:, 0]]
    n = np.cross(V_ref[tri[:, 1]] - a, V_ref[tri[:, 2]] - a)
    d = P - Q
    dist = np.linalg.norm(d, axis=1)
    side = np.einsum("ij,ij->i", d, n)
    out[idx] = np.where(side < 0, -dist, dist).astype(np.float32)
    return out


def offset_stats(offset: np.ndarray) -> dict:
    """The map in numbers: ``n`` compared vertices, ``new`` ones without a value (filled), and over
    the compared ones the ``rms_mm``, the signed ``mean_mm``, the 50th / 95th / 99th percentile and
    the maximum of the absolute move, and the share ``moved`` beyond :data:`MOVED_MM`. The
    numbers are None when nothing could be compared."""
    d = np.asarray(offset, dtype=np.float64).reshape(-1)
    ok = np.isfinite(d)
    n = int(ok.sum())
    out: dict = {"n": n, "new": int(len(d) - n)}
    if n == 0:
        out.update({"rms_mm": None, "mean_mm": None, "p50_mm": None, "p95_mm": None, "p99_mm": None, "max_mm": None,
                    "moved": None})
        return out
    d = d[ok]
    a = np.abs(d)
    p50, p95, p99 = np.percentile(a, [50, 95, 99])
    out.update({"rms_mm": round(float(np.sqrt(np.mean(d * d))), 4), "mean_mm": round(float(d.mean()), 4),
                "p50_mm": round(float(p50), 4), "p95_mm": round(float(p95), 4), "p99_mm": round(float(p99), 4),
                "max_mm": round(float(a.max()), 4), "moved": round(float(np.mean(a > MOVED_MM)), 4)})
    return out
