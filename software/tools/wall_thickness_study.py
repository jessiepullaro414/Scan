"""A thin wall's thickness read from the raw frames, not from the fused mesh (ENGINE_PLAN E-D3 item 2, the
study's experiment 2; a study tool, nothing in the engine uses it).

Why: on the scorecard's standing walls and sheet part the matcher's own points, each face a plane fitted to
the frames that see it, read every wall of 0.5 to 3 mm within 0.06 mm at true poses, while the 1 mm mesh reads
them 0.03 to 0.84 mm too thick there: the fusion's floor goes, what tracking adds stays (the design note
``D:/OpenHandScan-work/scratch/ed3/thin-walls.md``, looks 3 and 3b).

The reading at a click (:func:`read_wall`), as the app would make it:

1. **The wall at the click** comes from the mesh (:func:`mesh_wall_ray`, the default): a ray from the clicked vertex
   into the part must meet the mesh's other face, facing back, within :data:`WALL_MAX_MM`; the two faces are the
   vertices within :data:`PATCH_MM` of the click whose normals agree with the clicked face's or its opposite
   (:data:`NORMAL_COS`), each near its own face. A plane is fitted to each face's vertices; their mean normal is the
   wall's axis, the middle between them its origin. Refused when the click is not on a wall seen from both sides, or
   the mesh has only one face there. Experiment 2 ran with :func:`mesh_wall` instead (``wall_test="finder"``: the
   vertices :func:`ohs.mesh.thin_parts.find_thin_walls` flags), which refused a 3 mm rib (the finding's 2.7 mesh
   detail limit) and a click between its sparse flagged vertices.
2. **The raw points:** each frame's depth back-projected at its pose, every :data:`PIXEL_STEP` px, with a normal
   from central differences over 2 px. A point belongs to a face when it lies within :data:`PATCH_MM` of the
   origin along the wall, within the mesh's half reading plus :data:`BAND_MM` across it, and its normal agrees
   with that face's (:data:`NORMAL_COS`). The two faces are told apart by their normals only, never by a cut at
   the middle, so a wall thinner than the depth's scatter is not thinned by the selection.
3. **Each face** a plane ``s = a + b u + c v`` across the wall, trimmed three times at 2.5 robust sigma; the
   thickness is the difference of the two intercepts at the click. Refused when a face has fewer than
   :data:`MIN_FRAMES` frames with points or :data:`MIN_POINTS` points (one side not seen).
4. **Its ±** is one standard deviation over :data:`BOOT` resamples of the frames (a frame's points are not
   independent of each other). It holds the depth's scatter and the frames' scatter; it cannot hold an error
   that all frames of one side share against the other side's, which is what tracking leaves (the study: +0.3 to
   +0.6 mm on these walls). A reading whose 2 ± reaches zero says it cannot tell how thin the wall is.

Inputs are the scorecard's saved results, read only: the recording's true poses and calibration
(``<scorecard>/recs/<rec>``), the app chain's tracked and true-pose meshes and trajectories
(``<scorecard>/runs/gpu/app-default/<rec>/<poses>/proc``) and the matcher's cached depth (``depth/gpu/<rec>``, or
the folders given with ``--depth``). ``region truth`` reads the same faces with the region taken from the
simulated part instead of the mesh, for the study's kill rule (the two must agree within 0.1 mm).

The scene variants for the study's experiment 1 (a tie seen from both sides) are :func:`fins_ball_scene` and
:func:`fins_tags_scene`; they are not in the simulator's scenes.

Usage (from ``software``)::

    python tools/wall_thickness_study.py run --rec fins-s0 --poses tracked --region mesh --out D:/x/walls
    python tools/wall_thickness_study.py table D:/x/walls
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

SOFTWARE = Path(__file__).resolve().parents[1]
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))

SCORECARD = Path("D:/OpenHandScan-work/scorecard")
EXTRA_DEPTH = (Path("D:/OpenHandScan-work/scratch/x3/depth"),)  # the sheet's depth, cached by X3

PATCH_MM = 10.0  # the region round the click, along the wall
BAND_MM = 1.0  # beyond the mesh's face, across the wall
NORMAL_COS = 0.7  # a point's normal against its face's
PIXEL_STEP = 2
MIN_FRAMES = 3
MIN_POINTS = 300
MIN_FRAME_POINTS = 20  # a frame counts for a face with at least this many points on it
TRIM_SIGMA = 2.5
BOOT = 200
TILT_WARN_DEG = 3.0
FRAMES_WANTED = 40
WALL_MAX_MM = 4.0  # the tool's own wall test: the other face within this (a 3 mm wall reads up to 3.7 at 1 mm)
CLICK_MM = 3.0  # the click at most this far from the mesh
CLICK_TRIES = 50
AXIS_ROUNDS = 2  # the wall's axis refined from the raw faces' planes
MAX_FACE_SD_MM = 0.7  # a face's points scattering more than this: refused (on the scorecard 0.29 to 0.60 mm)  # the vertices nearest the click tried in turn for the wall test  # every n-th processed frame, so that about this many are read


# --------------------------------------------------------------------------- the reading
def unit(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    return x / np.linalg.norm(x)


def wall_basis(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(n, e1, e2)``: the wall's axis (unit) and two directions along the wall."""
    n = unit(axis)
    a = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = unit(np.cross(n, a))
    return n, e1, np.cross(n, e1)


def fit_face(Q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A plane ``s = a + b u + c v`` through points ``Q`` (columns u, v, s), trimmed three times at
    ``TRIM_SIGMA`` robust sigma: ``(coef, kept)``."""
    A = np.column_stack([np.ones(len(Q)), Q[:, 0], Q[:, 1]])
    m = np.ones(len(Q), dtype=bool)
    coef = np.zeros(3)
    for _ in range(3):
        if m.sum() < 3:
            break
        coef, *_ = np.linalg.lstsq(A[m], Q[m, 2], rcond=None)
        r = Q[:, 2] - A @ coef
        med = np.median(r[m])
        sd = 1.4826 * np.median(np.abs(r[m] - med)) + 1e-9
        m = np.abs(r - med) < TRIM_SIGMA * sd
    return coef, m


def select_faces(frames: list[tuple[np.ndarray, np.ndarray]], origin: np.ndarray, axis: np.ndarray, half_mm: float,
                 patch_mm: float = PATCH_MM, band_mm: float = BAND_MM, rect: tuple[float, float] | None = None
                 ) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Each frame's points ``(P, N)`` (world) on face A (normal along ``axis``) and on face B (against it), in the
    wall's coordinates ``(u, v, s)``: within ``patch_mm`` of ``origin`` along the wall (or the rectangle ``rect`` of
    half sizes along ``e1, e2``), within ``half_mm + band_mm`` of it across, normals agreeing (``NORMAL_COS``)."""
    n, e1, e2 = wall_basis(axis)
    A_out, B_out = [], []
    lim = half_mm + band_mm
    for P, N in frames:
        d = P - origin
        u, v, s = d @ e1, d @ e2, d @ n
        if rect is None:
            near = u * u + v * v <= patch_mm * patch_mm
        else:
            near = (np.abs(u) <= rect[0]) & (np.abs(v) <= rect[1])
        near &= np.abs(s) <= lim
        c = N @ n
        Q = np.column_stack([u, v, s])
        A_out.append(Q[near & (c > NORMAL_COS)])
        B_out.append(Q[near & (c < -NORMAL_COS)])
    return A_out, B_out


def thickness_of(A: list[np.ndarray], B: list[np.ndarray]) -> tuple[float, dict]:
    """The distance between the two faces' planes at the origin, and the fits' details."""
    QA, QB = np.concatenate(A), np.concatenate(B)
    ca, ma = fit_face(QA)
    cb, mb = fit_face(QB)
    tilt = math.degrees(math.atan(math.hypot(ca[1] - cb[1], ca[2] - cb[2])))
    sd = [float(1.4826 * np.median(np.abs(Q[m, 2] - np.median(Q[m, 2])))) for Q, m in ((QA, ma), (QB, mb))]
    return float(ca[0] - cb[0]), {"tilt_deg": tilt, "kept": [int(ma.sum()), int(mb.sum())], "face_sd_mm": sd}


def read_faces(A: list[np.ndarray], B: list[np.ndarray], *, boot: int = BOOT, seed: int = 0) -> dict:
    """The reading from each frame's face points (:func:`select_faces`): ``{refused, why, mm, pm_mm, frames,
    points, tilt_deg, ...}``. Refused when a face was seen by fewer than ``MIN_FRAMES`` frames or holds fewer than
    ``MIN_POINTS`` points; ``pm_mm`` is one standard deviation over ``boot`` resamples of the frames."""
    fa = [k for k, q in enumerate(A) if len(q) >= MIN_FRAME_POINTS]
    fb = [k for k, q in enumerate(B) if len(q) >= MIN_FRAME_POINTS]
    na, nb = sum(len(A[k]) for k in fa), sum(len(B[k]) for k in fb)
    out: dict[str, Any] = {"frames": [len(fa), len(fb)], "points": [na, nb]}
    for name, f, npts in (("A", fa, na), ("B", fb, nb)):
        if len(f) < MIN_FRAMES or npts < MIN_POINTS:
            out.update(refused=True, why=f"face {name} seen by {len(f)} frames with {npts} points: one side not seen")
            return out
    t, det = thickness_of([A[k] for k in fa], [B[k] for k in fb])
    rng = np.random.default_rng(seed)
    bs = []
    for _ in range(boot):
        sa = rng.choice(fa, size=len(fa), replace=True)
        sb = rng.choice(fb, size=len(fb), replace=True)
        bs.append(thickness_of([A[k] for k in sa], [B[k] for k in sb])[0])
    pm = float(np.std(bs, ddof=1)) if boot > 1 else float("nan")
    out.update(refused=False, why=None, mm=t, pm_mm=pm, **det)
    out["cannot_tell_how_thin"] = bool(t - 2.0 * pm <= 0.0)
    out["tilted"] = bool(det["tilt_deg"] > TILT_WARN_DEG)
    return out


# --------------------------------------------------------------------------- the wall at a click, from the mesh
def mesh_wall(V: np.ndarray, F: np.ndarray, click: np.ndarray, *, voxel_mm: float = 1.0, patch_mm: float = PATCH_MM,
              walls: tuple[dict, np.ndarray] | None = None) -> dict:
    """The wall the mesh shows at ``click`` (module docstring, 1): ``{refused, why, origin, axis, half_mm,
    mesh_mm, ...}``; ``walls`` is :func:`ohs.mesh.thin_parts.find_thin_walls`'s answer when already known."""
    from ohs.mesh.thin_parts import find_thin_walls  # noqa: PLC0415
    from ohs.mesh.topology import vertex_normals  # noqa: PLC0415

    if walls is None:
        walls = find_thin_walls(V, F, voxel_mm=voxel_mm)
    _, idx = walls
    if len(idx) == 0:
        return {"refused": True, "why": "the mesh shows no wall seen from both sides"}
    Nv = vertex_normals(V, F)
    P = V[idx]
    d = np.linalg.norm(P - click, axis=1)
    k = int(np.argmin(d))
    if d[k] > 3.0 * voxel_mm:
        return {"refused": True, "why": f"no wall seen from both sides at the click (nearest {d[k]:.1f} mm away)"}
    c = P[k]
    n0 = Nv[idx[k]]
    near = np.linalg.norm(P - c, axis=1) <= patch_mm
    cos = Nv[idx] @ n0
    fa, fb = near & (cos > NORMAL_COS), near & (cos < -NORMAL_COS)
    if fa.sum() < 20 or fb.sum() < 20:
        return {"refused": True, "why": f"the mesh has one face here ({int(fa.sum())} and {int(fb.sum())} vertices)"}
    axis = unit(Nv[idx[fa]].mean(axis=0) - Nv[idx[fb]].mean(axis=0))
    n, e1, e2 = wall_basis(axis)
    QA = np.column_stack([(P[fa] - c) @ e1, (P[fa] - c) @ e2, (P[fa] - c) @ n])
    QB = np.column_stack([(P[fb] - c) @ e1, (P[fb] - c) @ e2, (P[fb] - c) @ n])
    ca, _ = fit_face(QA)
    cb, _ = fit_face(QB)
    mid = 0.5 * (ca[0] + cb[0])
    return {"refused": False, "why": None, "origin": c + mid * n, "axis": n, "half_mm": 0.5 * float(ca[0] - cb[0]),
            "mesh_mm": float(ca[0] - cb[0]), "vertices": [int(fa.sum()), int(fb.sum())], "click_off_mm": float(d[k])}


def mesh_wall_ray(V: np.ndarray, F: np.ndarray, click: np.ndarray, *, patch_mm: float = PATCH_MM,
                  wall_max_mm: float = WALL_MAX_MM, cast=None) -> dict:
    """The wall at ``click`` by the tool's own test (the default since 2026-11-23, after experiment 2): the mesh vertex
    nearest the click (at most :data:`CLICK_MM` away), a ray from it into the part against its normal, which must meet
    the mesh's other face, facing the other way, within ``wall_max_mm`` (4 mm: a 3 mm wall reads up to 3.7 mm at 1 mm
    mesh detail). The two faces are then the vertices within ``patch_mm`` of the click whose normals agree with the
    clicked face's or its opposite, each within 1 mm of its own face. Unlike :func:`mesh_wall` it does not depend on
    the thin-parts finding's sample of rays (which flags every n-th vertex only, so a click can fall between its
    flagged vertices) or on its 2.7 mesh detail limit. ``cast``: a caster already built for this mesh."""
    from ohs.mesh.thin_parts import OPPOSITE_COS, _caster  # noqa: PLC0415
    from ohs.mesh.topology import vertex_normals  # noqa: PLC0415

    Nv = vertex_normals(V, F)
    d = np.linalg.norm(V - click, axis=1)
    order = np.argsort(d)[:CLICK_TRIES]
    order = order[d[order] <= CLICK_MM]
    if len(order) == 0:
        return {"refused": True, "why": f"no wall seen from both sides at the click (the mesh is {d.min():.1f} mm away)"}
    cast = cast or _caster(V, F)
    # the vertices nearest the click, in turn: the first whose ray meets the other face (a click can land on a
    # vertex at an edge or a pinhole, whose ray finds nothing)
    Nk = np.array([unit(Nv[i]) if np.linalg.norm(Nv[i]) > 0 else np.zeros(3) for i in order])
    ts, dots = cast(V[order] - 0.02 * Nk, -Nk)
    ts = ts + 0.02
    ok = np.isfinite(ts) & (ts <= wall_max_mm) & (dots > -OPPOSITE_COS)
    if not ok.any():
        t0 = ts[0]
        far = "nothing" if not math.isfinite(t0) else f"{t0:.1f} mm"
        return {"refused": True, "why": f"no wall seen from both sides at the click (behind it: {far}, the limit "
                                        f"{wall_max_mm:g} mm, facing the other way)"}
    j = int(np.argmax(ok))
    k = int(order[j])
    c, n0, t = V[k], Nk[j], float(ts[j])
    near = d <= patch_mm
    s = (V - c) @ n0
    cos = Nv @ n0
    fa = near & (cos > NORMAL_COS) & (np.abs(s) <= 1.0)
    fb = near & (cos < -NORMAL_COS) & (np.abs(s + t) <= 1.0)
    if fa.sum() < 20 or fb.sum() < 20:
        return {"refused": True, "why": f"the mesh has one face here ({int(fa.sum())} and {int(fb.sum())} vertices)"}
    axis = unit(Nv[fa].mean(axis=0) - Nv[fb].mean(axis=0))
    n, e1, e2 = wall_basis(axis)
    QA = np.column_stack([(V[fa] - c) @ e1, (V[fa] - c) @ e2, (V[fa] - c) @ n])
    QB = np.column_stack([(V[fb] - c) @ e1, (V[fb] - c) @ e2, (V[fb] - c) @ n])
    ca, _ = fit_face(QA)
    cb, _ = fit_face(QB)
    mid = 0.5 * (ca[0] + cb[0])
    return {"refused": False, "why": None, "origin": c + mid * n, "axis": n, "half_mm": 0.5 * float(ca[0] - cb[0]),
            "mesh_mm": float(ca[0] - cb[0]), "vertices": [int(fa.sum()), int(fb.sum())], "click_off_mm": float(d[k]),
            "ray_mm": t}


def read_wall(frames: list[tuple[np.ndarray, np.ndarray]], V: np.ndarray, F: np.ndarray, click: np.ndarray, *,
              voxel_mm: float = 1.0, walls: tuple[dict, np.ndarray] | None = None, boot: int = BOOT,
              wall_test: str = "ray", cast=None) -> dict:
    """The reading at a click (module docstring): the wall from the mesh, the faces from the raw frames.
    ``wall_test``: "ray" (:func:`mesh_wall_ray`, the default) or "finder" (:func:`mesh_wall`, as experiment 2 ran)."""
    w = (mesh_wall_ray(V, F, click, cast=cast) if wall_test == "ray"
         else mesh_wall(V, F, click, voxel_mm=voxel_mm, walls=walls))
    if w["refused"]:
        return {"refused": True, "why": w["why"]}
    origin, axis = w["origin"], w["axis"]
    for _ in range(AXIS_ROUNDS):  # the axis from the raw faces themselves, not from the mesh's few vertices
        A, B = select_faces(frames, origin, axis, w["half_mm"])
        nA, nB = sum(map(len, A)), sum(map(len, B))
        if nA < MIN_POINTS or nB < MIN_POINTS:
            break
        ca, _ = fit_face(np.concatenate(A))
        cb, _ = fit_face(np.concatenate(B))
        n, e1, e2 = wall_basis(axis)
        tilt = -0.5 * ((ca[1] + cb[1]) * e1 + (ca[2] + cb[2]) * e2)
        origin = origin + 0.5 * (ca[0] + cb[0]) * n
        axis = unit(n + tilt)
    A, B = select_faces(frames, origin, axis, w["half_mm"])
    r = read_faces(A, B, boot=boot)
    if not r.get("refused") and max(r["face_sd_mm"]) > MAX_FACE_SD_MM:
        r.update(refused=True, why=f"the faces' points scatter {max(r['face_sd_mm']):.2f} mm (limit {MAX_FACE_SD_MM:g}): "
                                   f"cannot tell this wall's thickness here", unsure_mm=r.pop("mm"))
    r["mesh_at_click_mm"] = w["mesh_mm"]
    r["mesh_vertices"] = w["vertices"]
    return r


# --------------------------------------------------------------------------- frames
def frame_points(depth: np.ndarray, K: np.ndarray, T_wc: np.ndarray, step: int = PIXEL_STEP
                 ) -> tuple[np.ndarray, np.ndarray]:
    """A frame's depth (z in the rectified left camera, mm; 0 = none) back-projected to world points with normals
    (central differences over 2 px, facing the camera), every ``step`` px."""
    D = np.asarray(depth, dtype=np.float64)
    H, W = D.shape
    v, u = np.mgrid[0:H, 0:W]
    X = np.stack([(u - K[0, 2]) / K[0, 0] * D, (v - K[1, 2]) / K[1, 1] * D, D], -1)
    N = np.zeros_like(X)
    N[2:-2, 2:-2] = np.cross(X[2:-2, 4:] - X[2:-2, :-4], X[4:, 2:-2] - X[:-4, 2:-2])
    nn = np.linalg.norm(N, axis=-1)
    N = N / np.maximum(nn, 1e-12)[..., None]
    N[N[..., 2] > 0] *= -1.0
    ok = (D > 0) & (nn > 0)
    # all four neighbours 2 px away measured too, so a normal never spans a missing pixel
    for dy, dx in ((0, 2), (2, 0), (0, -2), (-2, 0)):
        ok &= np.roll(D, (dy, dx), axis=(0, 1)) > 0
    sel = np.zeros_like(ok)
    sel[::step, ::step] = True
    ok &= sel
    R, t = T_wc[:3, :3], T_wc[:3, 3]
    return X[ok] @ R.T + t, N[ok] @ R.T


def read_poses_csv(path: Path) -> dict[int, np.ndarray]:
    """``index -> T_wc`` from a ``trajectory.csv`` (frames with ``ok`` 0 left out) or a ground-truth ``poses.csv``."""
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("ok", "1").strip() not in ("1", "True", "true"):
                continue
            out[int(r["index"])] = np.array([float(r[f"T{a}{b}"]) for a in range(4) for b in range(4)]).reshape(4, 4)
    return out


def rigid_fit(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """The rigid ``T`` with ``T A ~ B`` (Kabsch, no scale)."""
    ma, mb = A.mean(axis=0), B.mean(axis=0)
    U, _, Vt = np.linalg.svd((A - ma).T @ (B - mb))
    D = np.diag([1.0, 1.0, np.sign(np.linalg.det(Vt.T @ U.T))])
    R = Vt.T @ D @ U.T
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, mb - R @ ma
    return T


# --------------------------------------------------------------------------- the scorecard's thin scenes
def truth_walls(rec: str) -> dict[str, dict]:
    """The simulated walls of a recording: ``name -> {centre, axis, t, rect, key}`` (simulator world; ``rect``
    the half sizes of the core along the wall, as the scorecard's judge and the study's look 3 used; ``key`` the
    scorecard's metric for the mesh's reading)."""
    if rec.startswith("fins"):
        from tools.preset_study import FIN_BASE, FIN_HALF, FIN_THICKNESS, FIN_X  # noqa: PLC0415

        out = {}
        for x, t in zip(FIN_X, FIN_THICKNESS):
            out[f"wall{t:g}"] = {"centre": np.array([x, 0.0, FIN_BASE + FIN_HALF[1]]), "axis": np.array([0.0, 1.0, 0.0]),
                                 "t": t, "rect": (FIN_HALF[0] - 4.0, FIN_HALF[1] - 3.5), "key": f"wall{t:g}_err",
                                 "basis": (np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0]))}
        return out
    if rec.startswith("sheet"):
        from tools.scorecard import SHEET_WALLS  # noqa: PLC0415

        keys = {"flange_2mm": "sheet_flange2mm_err", "rib_1mm": "sheet_rib1mm_err", "rib_3mm": "sheet_rib3mm_err"}
        out = {}
        for name, (c, h, ax) in SHEET_WALLS.items():
            o = [i for i in range(3) if i != ax]
            axis = np.zeros(3)
            axis[ax] = 1.0
            e = [np.eye(3)[i] for i in o]
            out[name] = {"centre": np.array(c, dtype=np.float64), "axis": axis, "t": 2.0 * h[ax],
                         "rect": (h[o[0]] - 4.0, h[o[1]] - 4.0), "key": keys[name], "basis": (e[0], e[1])}
        return out
    return {}


def _rect_faces(frames, w: dict, origin: np.ndarray, R: np.ndarray):
    """Look 3's selection: the core rectangle of the true wall, ``|s| < t/2 + 1.5``, normals agreeing. ``R``, the
    rotation from the simulator's world into the frames' world (identity at true poses)."""
    n = R @ w["axis"]
    e1, e2 = (R @ b for b in w["basis"])
    A_out, B_out = [], []
    for P, N in frames:
        d = P - origin
        u, v, s = d @ e1, d @ e2, d @ n
        near = (np.abs(u) < w["rect"][0]) & (np.abs(v) < w["rect"][1]) & (np.abs(s) < w["t"] / 2 + 1.5)
        c = N @ n
        Q = np.column_stack([u, v, s])
        A_out.append(Q[near & (c > NORMAL_COS)])
        B_out.append(Q[near & (c < -NORMAL_COS)])
    return A_out, B_out


def depth_dir(rec: str, scorecard: Path, extra: tuple[Path, ...]) -> Path:
    for d in (scorecard / "depth" / "gpu" / rec, *[e / rec for e in extra]):
        if d.is_dir():
            return d
    raise FileNotFoundError(f"no cached depth for {rec} under {scorecard / 'depth' / 'gpu'} or {extra}")


def run_one(rec: str, poses: str, region: str, *, scorecard: Path = SCORECARD, extra: tuple[Path, ...] = EXTRA_DEPTH,
            frames_from: int = 0, clicks: dict[str, np.ndarray] | None = None, boot: int = BOOT,
            rec_dir: Path | None = None, run: Path | None = None, depth: Path | None = None,
            wall_test: str = "ray", traj: Path | None = None) -> dict:
    """One recording, one pose source (``tracked`` or ``true``), one region (``mesh`` or ``truth``): every wall's
    reading, the mesh's beside it. ``frames_from``: only processed frames from this index on (a one-sided case).
    ``clicks``: world points (simulator world) to read at instead of the true walls' centres (refusal cases).
    ``rec_dir``, ``run`` (the folder holding ``result.json`` and ``proc``) and ``depth``: other places than the
    scorecard's (experiment 1's own recordings)."""
    from ohs.track.export import load_ply  # noqa: PLC0415

    t0 = time.perf_counter()
    rdir = rec_dir or scorecard / "recs" / rec
    cal = json.loads((rdir / "calibration.json").read_text(encoding="utf-8"))
    K = np.array(cal["stereo"]["P1"], dtype=np.float64)[:3, :3]
    gt = read_poses_csv(rdir / "ground_truth" / "poses.csv")
    run = run or scorecard / "runs" / "gpu" / "app-default" / rec / poses
    proc = run / "proc"
    tr = read_poses_csv(traj or proc / "trajectory.csv")
    metrics = json.loads((run / "result.json").read_text(encoding="utf-8")).get("metrics", {})
    dd = depth or depth_dir(rec, scorecard, extra)
    have = {int(p.stem) for p in dd.glob("*.npz")}
    idx = sorted(set(gt) & set(tr) & have)
    # the frames' world (the tracker's first camera, or the simulator's at true poses) against the simulator's
    Pl = rigid_fit(np.array([tr[i][:3, 3] for i in idx]), np.array([gt[i][:3, 3] for i in idx]))
    to_frames = np.linalg.inv(Pl)
    every = max(1, len(idx) // FRAMES_WANTED)
    use = [i for i in idx[::every] if i >= frames_from]
    frames = []
    for i in use:
        with np.load(dd / f"{i:06d}.npz") as z:
            frames.append(frame_points(z["depth"], K, tr[i]))
    out: dict[str, Any] = {"rec": rec, "poses": poses, "region": region, "frames_read": len(use), "every": every,
                           "frames_from": frames_from, "walls": {}}
    walls = truth_walls(rec)
    # the click: on the wall's +axis face, as a user clicks a face (with the ray test; the finding's test, as
    # experiment 2 ran, took the wall's middle and the nearest flagged vertex)
    if clicks is not None:
        targets = clicks
    elif region == "mesh" and wall_test == "ray":
        targets = {k: w["centre"] + 0.5 * w["t"] * w["axis"] for k, w in walls.items()}
    else:
        targets = {k: w["centre"] for k, w in walls.items()}
    mesh = None
    if region == "mesh":
        from ohs.mesh.thin_parts import find_thin_walls  # noqa: PLC0415

        d = load_ply(proc / "mesh.ply")
        V, F = np.asarray(d["vertices"], dtype=np.float64), np.asarray(d["faces"], dtype=np.int64)
        from ohs.mesh.thin_parts import _caster  # noqa: PLC0415

        mesh = (V, F, find_thin_walls(V, F, voxel_mm=1.0) if wall_test == "finder" else None, _caster(V, F))
        if mesh[2] is not None:
            out["mesh_walls"] = {k: mesh[2][0].get(k) for k in ("found", "area_mm2", "n_regions", "reads_mm")}
        out["wall_test"] = wall_test
    for name, p in targets.items():
        w = walls.get(name)
        click = to_frames[:3, :3] @ np.asarray(p, dtype=np.float64) + to_frames[:3, 3]
        if region == "mesh":
            r = read_wall(frames, mesh[0], mesh[1], click, walls=mesh[2], boot=boot, wall_test=wall_test, cast=mesh[3])
        else:
            c0 = to_frames[:3, :3] @ w["centre"] + to_frames[:3, 3]
            A, B = _rect_faces(frames, w, c0, to_frames[:3, :3])
            r = read_faces(A, B, boot=boot)
        if w is not None:
            r["truth_mm"] = w["t"]
            if not r.get("refused"):
                r["err_mm"] = r["mm"] - w["t"]
                r["covered_2pm"] = bool(abs(r["err_mm"]) <= 2.0 * r["pm_mm"])
            m = metrics.get(w["key"])
            r["mesh_err_mm"] = None if m is None else float(m)
        out["walls"][name] = r
    out["seconds"] = round(time.perf_counter() - t0, 1)
    return out


# --------------------------------------------------------------------------- experiment 1's scenes (not run here)
BALL_D_MM = 40.0
BALL_AT = {"front": (95.0, 0.0), "behind": (-105.0, 0.0)}  # beyond the walls' +x end / -x end, on the table
TAG_MM = 25.0  # tag36h11 with a 6 mm white margin, as the X8 study's
# six tags on the table round the walls' base plate (160 x 50 mm at the origin; the block at y 28 to 52): one beyond
# each end of the row and two along each long side, each seen from both sides of the walls (over them)
TAG_AT = ((105.0, 0.0), (-105.0, 0.0), (-55.0, 55.0), (55.0, 55.0), (-55.0, -55.0), (55.0, -55.0))


def fins_ball_scene(seed: int = 0, at: str = "front", diameter_mm: float = BALL_D_MM):
    """The standing walls (:func:`tools.preset_study.fins_scene`) with a matte ball on the table beside them, seen
    from both sides: the study's tie by smooth surface (the design note, candidate A). Same hand path. ``at``: a key
    of :data:`BALL_AT`, or "both" (two balls, one beyond each end of the row: one ball ties the sides' position near
    it, two tie the turn about the vertical too)."""
    from ohs.sim.scene import NoiseAlbedo, Scene, Sphere  # noqa: PLC0415
    from tools.preset_study import fins_scene  # noqa: PLC0415

    base = fins_scene(seed)
    r = diameter_mm / 2.0
    where = list(BALL_AT) if at == "both" else [at]
    balls = [Sphere(np.array([BALL_AT[w][0], BALL_AT[w][1], r]), r,
                    NoiseAlbedo(mean=0.62, contrast=0.03, scale_mm=2.0, seed=seed + 7 + k), name=f"tie_ball_{w}")
             for k, w in enumerate(where)]
    meta = dict(base.meta, tie={"kind": "ball", "at": [list(BALL_AT[w]) for w in where], "diameter_mm": diameter_mm})
    return Scene(list(base.primitives) + balls, name="fins_ball", target=base.target, radius=base.radius, meta=meta)


def fins_tags_scene(seed: int = 0, tag_mm: float = TAG_MM, at: tuple[tuple[float, float], ...] = TAG_AT):
    """The standing walls with AprilTags lying on the table round them (each seen from both sides of the walls),
    for Straighten's tags as anchors (``StraightenParams.tags``): the study's tie by tags."""
    from ohs.sim.scene import Decal, DecalAlbedo, Scene, apriltag_texture, marker_corners_world  # noqa: PLC0415
    from tools.preset_study import fins_scene  # noqa: PLC0415

    base = fins_scene(seed)
    table = base.primitives[0]
    # as tools/large_part_study.py puts them on its table: the table's own frame has y the other way round
    decals = [Decal(apriltag_texture(m, tag_mm), 10, (x, -y), angle=0.3 * m, margin_mm=tag_mm / 4, tag_id=m)
              for m, (x, y) in enumerate(at)]
    table.albedo = DecalAlbedo(table.albedo, decals)
    markers = {dc.tag_id: marker_corners_world(table, dc) for dc in decals}
    meta = dict(base.meta, tie={"kind": "tags", "at": [list(a) for a in at], "tag_mm": tag_mm})
    return Scene(list(base.primitives), name="fins_tags", target=base.target, radius=base.radius, markers=markers,
                 meta=meta)


def sheet_tags_scene(seed: int = 0, tag_mm: float = TAG_MM, at: tuple[tuple[float, float], ...] = TAG_AT):
    """The scorecard's sheet part (:func:`tools.scorecard.sheet_scene`) with the same six tags on its table as
    :func:`fins_tags_scene` (clear of its foot, y -5 to 35, and its flange and ribs)."""
    from ohs.sim.scene import Decal, DecalAlbedo, Scene, apriltag_texture, marker_corners_world  # noqa: PLC0415
    from tools.scorecard import sheet_scene  # noqa: PLC0415

    base = sheet_scene(seed)
    table = base.primitives[0]
    decals = [Decal(apriltag_texture(m, tag_mm), 10, (x, -y), angle=0.3 * m, margin_mm=tag_mm / 4, tag_id=m)
              for m, (x, y) in enumerate(at)]
    table.albedo = DecalAlbedo(table.albedo, decals)
    markers = {dc.tag_id: marker_corners_world(table, dc) for dc in decals}
    meta = dict(base.meta, tie={"kind": "tags", "at": [list(a) for a in at], "tag_mm": tag_mm})
    return Scene(list(base.primitives), name="sheet_tags", target=base.target, radius=base.radius, markers=markers,
                 meta=meta)


VARIANTS = ("ball", "ball-behind", "balls2", "tags", "reversed", "sheet-tags")


def variant_scene(variant: str, seed: int):
    if variant == "ball":
        return fins_ball_scene(seed, "front")
    if variant == "ball-behind":
        return fins_ball_scene(seed, "behind")
    if variant == "balls2":
        return fins_ball_scene(seed, "both")
    if variant == "tags":
        return fins_tags_scene(seed)
    if variant == "sheet-tags":
        return sheet_tags_scene(seed)
    if variant == "reversed":  # experiment 0 d1: no tie, the sweep the other way round (the same seed's wander)
        from tools.preset_study import fins_scene  # noqa: PLC0415

        sc = fins_scene(seed)
        tr = dict(sc.meta["trajectory"])
        tr["sweep_deg_s"] = -float(tr["sweep_deg_s"])
        sc.meta = dict(sc.meta, trajectory=tr, tie={"kind": "none", "reversed": True})
        return sc
    raise ValueError(f"unknown variant {variant!r} (one of {VARIANTS})")


def exp1_render(work: Path, variant: str, seed: int) -> Path:
    """Experiment 1's recording: the scorecard's standing walls recording ``fins-s<seed>`` (120 frames at 30 fps, the
    scene's own hand path, the same seed) with the tie added. Heavy (the processor renders)."""
    from ohs.sim.datasets import make_scan_recording  # noqa: PLC0415
    from tools.scorecard import IMAGE_SIZE, SCENES  # noqa: PLC0415

    path = work / "recs" / f"{variant}-s{seed}"
    if (path / "done").is_file():
        return path
    if variant.startswith("sheet"):  # the scorecard's sheet maker: 240 frames at 10 fps, a 400 degree orbit
        from ohs.sim.nominal import NOMINAL_TIME_OFFSET, nominal_calibration  # noqa: PLC0415
        from ohs.sim.trajectory import HandheldTrajectory  # noqa: PLC0415
        from tools.scorecard import SHEET_TARGET  # noqa: PLC0415

        spec = SCENES[f"sheet-s{seed}"]
        calib = nominal_calibration(IMAGE_SIZE, realistic=True)
        dur = spec.frames / 10.0
        traj = HandheldTrajectory(target=np.array(SHEET_TARGET), t0=100.0 + NOMINAL_TIME_OFFSET, duration=dur, seed=seed,
                                  baseline=calib.stereo.baseline, distance=(200.0, 280.0), elevation_deg=(35.0, 65.0),
                                  sweep_deg_s=400.0 / dur)
        make_scan_recording(path, scene=variant_scene(variant, seed), n_frames=spec.frames, fps=10.0, trajectory=traj,
                            image_size=IMAGE_SIZE, realistic=True, depth_every=0, seed=seed, overwrite=True)
    else:
        spec = SCENES[f"fins-s{seed}"]
        make_scan_recording(path, scene=variant_scene(variant, seed), n_frames=spec.frames, fps=float(spec.build["fps"]),
                            image_size=IMAGE_SIZE, realistic=True, depth_every=0, seed=seed, overwrite=True)
    (path / "variant.json").write_text(json.dumps({"variant": variant, "seed": seed}), encoding="utf-8")
    (path / "done").write_text("", encoding="utf-8")
    return path


def exp1_process(rec: Path, out: Path, poses: str, tags: bool, straighten: dict | None = None,
                 mode: str = "markerless") -> dict:
    """What Make mesh runs (the scorecard's app chain) on an experiment-1 recording, with Straighten's tags as anchors
    on or off, the matcher's depth kept in ``out/depth`` (for the raw reading), judged by the scorecard's judge on the
    variant scene (a ball counts as part of the truth; tags change no shape). ``straighten``: other
    ``StraightenParams`` fields, in this process only (a diagnostic, not the engine's Make mesh). Heavy (the graphics
    card tracks)."""
    import ohs.track.posegraph as PG  # noqa: PLC0415
    import tools.scorecard as SC  # noqa: PLC0415
    from ohs.io.recording import RecordingReader  # noqa: PLC0415

    v = json.loads((rec / "variant.json").read_text(encoding="utf-8"))
    kind = "sheet" if v["variant"].startswith("sheet") else "fins"
    spec = SC.SCENES[f"{kind}-s{int(v['seed'])}"]
    sc = variant_scene(v["variant"], int(v["seed"]))
    if mode != "markerless":  # the app's Markers mode, with the tags' printed size in the config (as a user sets it)
        from ohs.app.config import Config  # noqa: PLC0415
        from ohs.io.recording import RecordingReader as _RR  # noqa: PLC0415
        from ohs.server.processing import stage_params  # noqa: PLC0415

        cfg = Config()
        cfg.markers.tag_mm = float((sc.meta.get("tie") or {}).get("tag_mm", TAG_MM))

        def app_params(spec_, params, rec_):
            import dataclasses  # noqa: PLC0415

            size = _RR(rec_).meta.get("image_size") or [1280, 800]
            start, stop = SC.stretch(spec_)
            pp = stage_params(cfg, mode, "standard", int(size[0]))
            return SC.apply_params(dataclasses.replace(pp, start=int(start), stop=stop), params)

        SC.app_params = app_params
    SC.scene_of = lambda spec_, models=None: sc  # this process only: the judge scores the variant scene
    orig = PG.straighten_trajectory

    def wrapped(rec_, calib, traj, **k):
        import dataclasses  # noqa: PLC0415

        k["params"] = dataclasses.replace(k.get("params") or PG.StraightenParams(), tags=bool(tags), **(straighten or {}))
        return orig(rec_, calib, traj, **k)

    PG.straighten_trajectory = wrapped
    restore = SC._install_depth_hook("capture", out / "depth")
    try:
        gt = RecordingReader(rec).ground_truth_poses() if poses == "true" else None
        proc = out / "proc"
        proc.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        rep, stages = SC.app_chain(rec, proc, spec, {}, gt)
        res = SC.judge(spec, rec, proc, rep)
        res["info"]["chain"] = SC._plain(stages)
        res["info"]["process_s"] = round(time.perf_counter() - t0, 1)
        res["info"]["variant"], res["info"]["tags"], res["info"]["straighten_overrides"] = v, bool(tags), straighten or {}
        res["info"]["mode"] = mode
    finally:
        restore()
        PG.straighten_trajectory = orig
    (out / "result.json").write_text(json.dumps(_jsonable(res), indent=1), encoding="utf-8")
    return res


# --------------------------------------------------------------------------- the command line
def _jsonable(x):
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (np.floating, np.integer, np.bool_)):
        return x.item()
    return x


def table(folder: Path) -> str:
    rows = ["| recording | poses | region | wall | raw reading - truth | ± | 2 ± covers | mesh - truth | frames A/B | "
            "points A/B | refused |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for f in sorted(folder.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for name, r in d["walls"].items():
            def g(k, fmt="{:+.3f}"):
                v = r.get(k)
                return "" if v is None else fmt.format(v)
            rows.append(f"| {d['rec']} | {d['poses']} | {d['region']}{'' if not d.get('frames_from') else ' from ' + str(d['frames_from'])} "
                        f"| {name} | {g('err_mm')} | {g('pm_mm', '{:.3f}')} | {'' if r.get('covered_2pm') is None else ('yes' if r['covered_2pm'] else 'no')} "
                        f"| {g('mesh_err_mm')} | {'/'.join(map(str, r.get('frames', [])))} | {'/'.join(map(str, r.get('points', [])))} "
                        f"| {r.get('why') or ''} |")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="read the walls of one recording")
    r.add_argument("--rec", required=True)
    r.add_argument("--poses", choices=("tracked", "true"), default="tracked")
    r.add_argument("--region", choices=("mesh", "truth"), default="mesh")
    r.add_argument("--frames-from", type=int, default=0)
    r.add_argument("--click", action="append", default=[], help="name=x,y,z in the simulator's world (refusal cases)")
    r.add_argument("--scorecard", type=Path, default=SCORECARD)
    r.add_argument("--depth", type=Path, action="append", default=[])
    r.add_argument("--boot", type=int, default=BOOT)
    r.add_argument("--wall-test", choices=("ray", "finder"), default="ray",
                   help="the tool's own ray (default) or the thin-parts finding, as experiment 2 ran")
    r.add_argument("--rec-dir", type=Path, default=None, help="a recording elsewhere (experiment 1)")
    r.add_argument("--run-dir", type=Path, default=None, help="its processed run (result.json, proc/)")
    r.add_argument("--depth-dir", type=Path, default=None, help="its cached depth")
    r.add_argument("--traj", type=Path, default=None, help="another trajectory.csv (e.g. before Straighten)")
    r.add_argument("--out", type=Path, required=True)
    t = sub.add_parser("table", help="the table of a folder of runs")
    t.add_argument("folder", type=Path)
    e = sub.add_parser("exp1-render", help="render experiment 1's recording (heavy: the processor)")
    e.add_argument("--work", type=Path, required=True)
    e.add_argument("--variant", choices=VARIANTS, required=True)
    e.add_argument("--seed", type=int, required=True)
    p = sub.add_parser("exp1-process", help="Make mesh on it and judge it (heavy: the graphics card)")
    p.add_argument("--rec-dir", type=Path, required=True)
    p.add_argument("--poses", choices=("tracked", "true"), default="tracked")
    p.add_argument("--tags", choices=("on", "off"), default="off")
    p.add_argument("--straighten", action="append", default=[], help="KEY=VALUE: a StraightenParams field (diagnostic)")
    p.add_argument("--mode", choices=("markerless", "markers"), default="markerless", help="the scan's tracking mode")
    p.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "table":
        print(table(a.folder))
        return 0
    if a.cmd == "exp1-render":
        print(exp1_render(a.work, a.variant, a.seed))
        return 0
    if a.cmd == "exp1-process":
        over = {kv.split("=")[0]: json.loads(kv.split("=", 1)[1]) for kv in a.straighten}
        res = exp1_process(a.rec_dir, a.out, a.poses, a.tags == "on", over, a.mode)
        print({k: v for k, v in res.get("metrics", {}).items() if k.startswith(("wall", "sheet_", "traj", "frames"))})
        return 0
    clicks = None
    if a.click:
        clicks = {c.split("=")[0]: np.array([float(v) for v in c.split("=")[1].split(",")]) for c in a.click}
    res = run_one(a.rec, a.poses, a.region, scorecard=a.scorecard, extra=tuple(a.depth) or EXTRA_DEPTH,
                  frames_from=a.frames_from, clicks=clicks, boot=a.boot, rec_dir=a.rec_dir, run=a.run_dir,
                  depth=a.depth_dir, wall_test=a.wall_test, traj=a.traj)
    a.out.mkdir(parents=True, exist_ok=True)
    tag = f"{a.rec}-{a.poses}-{a.region}" + ("-finder" if a.region == "mesh" and a.wall_test == "finder" else "") + (f"-from{a.frames_from}" if a.frames_from else "") + ("-clicks" if clicks else "")
    (a.out / f"{tag}.json").write_text(json.dumps(_jsonable(res), indent=1), encoding="utf-8")
    for name, w in res["walls"].items():
        print(name, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in w.items()})
    print("seconds", res["seconds"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
