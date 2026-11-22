"""Thin walls, thin plates lying flat and small round features in a finished mesh, and whether the preset
"Thin sheet metal and small parts" is worth suggesting (ENGINE_PLAN X5, step 1).

It reads the mesh and changes nothing: :func:`find_thin_parts` returns what it found, with numbers,
and :func:`hint_words` the sentences the app shows. The app only suggests; no setting changes by
itself. What "thin" and "small" mean comes from the measurements in ``VALIDATION.md`` ("Presets:
which settings matter, measured", "The scorecard's baseline", "Thin walls seen from opposite
sides"), as multiples of the mesh detail ``voxel_mm`` the mesh was made at:

* **A thin wall** (seen from both sides): a ray cast from a vertex into the part, against its
  normal, meets the mesh's other side, facing the other way (normals within about 37° of opposite,
  ``OPPOSITE_COS``), within :data:`WALL_READS_VOXELS` (2.7) times the mesh detail. At mesh detail
  1 mm, tracked, on the graphics card's path, simulated walls 0.5 to 2 mm thick read 1.66 to 2.66 mm
  (the standing walls 1.68 to 2.66, the sheet's 1 mm rib 1.66 to 2.21 and 2 mm flange 2.25 to 2.61);
  the sheet's 3 mm rib reads 3.03 to 3.72. So a reading of at most 2.7 mm is a wall of 2 mm or
  less, where 0.5 mm mesh detail was measured to help (the walls 0.23 to 0.67 mm nearer the truth,
  on all three recordings), and a 3 mm wall is not one. The walls found must cover at least
  :data:`WALL_MIN_AREA_VOXELS2` (100) square voxels of surface (both faces; 100 mm² at 1 mm): on the
  36 recordings of the scorecard the parts without a thin wall had at most 50 mm² of such readings
  in all (the speckled brackets 228 and 240 mm², in pieces; see ``min_area``), the standing walls
  1,794 to 4,109 mm² and the sheet part about 7,000 mm².
* **Its thickness:** the median reading of each wall found and its 10th to 90th percentile; at mesh
  detail 1 mm with the thin-wall rule on, an estimate of the true thickness from the measured
  excess (``WALL_EXCESS_MM``: walls of 0.5 to 2 mm read 0.1 to 1.3 mm too thick, tracked or at true
  poses), so ``reading - 1.3`` to ``reading - 0.1`` mm.
* **A thin plate lying flat:** what "Remove the table" (:func:`ohs.mesh.support.find_support`) leaves
  of the scan is a plateau: 99 % of its area at most :data:`PLATE_MAX_VOXELS` (2.7) mesh detail above
  the table, its top (faces within 30° of the table's normal) at least :data:`PLATE_MIN_SHARE` (half)
  of it and at least :data:`PLATE_MIN_AREA_VOXELS2` (400) square voxels, its median height above the
  table finder's own limit (2.5 times the table's scatter). Its thickness is that median height, its
  ± the top's scatter. The same limit as a wall's: a sheet that thin would read too thick standing.
  Lying flat, the 0.5 mm plates read 0.49 to 0.50 mm at every mesh detail from 0.5 to 2 mm, so the
  words say the preset was not measured to help a plate lying flat.
* **A small round hole** (or bore) whose wall the scan saw: a ray out of the surface along its normal
  meets the mesh facing back within 3 to :data:`ROUND_MAX_VOXELS` (16) mesh detail; such wall points,
  joined into clusters (cubes of 3 mesh detail), that lie round one axis (their normals avoid one
  direction: spread along it at most a fifth of across, ``HOLE_AXIS_RATIO``; a slot or the gap of a
  seam leaves two), on at least 60 % of a circle's turn, typically within 20 % of its radius. The
  diameter is the circle's, within about one mesh detail. Why 16 and why holes: mesh detail moves a
  hole's reading (the car panel's Ø 25 hole by 0.6 to 0.8 mm at 0.5 mm, and 1.4 to 2.4 mm at 1.5 to
  2 mm, on all three recordings), and holes of Ø 6 and Ø 10 read worst of all (no answer over open
  space); the Ø 25 holes are left out (near a floor they read within their scan-to-scan spread).
  Bosses are not looked for: the bracket's Ø 14 bosses read 0.3 to 0.5 mm small at mesh detail 1 mm
  but right (-0.02 mm) with true depth, so the error is the depth's, not the mesh detail's. A hole the
  stereo depth filled in (no wall seen) is not found: on the scorecard the Ø 6 and Ø 10 holes over a
  far floor are dents of 0.5 to 0.8 mm, and the hole plates are found by their Ø 10 bore.

Nothing is suggested when the mesh was made with the thin preset or at 0.5 mm mesh detail or finer
(``suggest`` False, ``why_not`` "made_fine"; nothing is looked for then), nor when nothing was found.
**The preset is suggested only for thin walls**, the one case where 0.5 mm was measured to help. A thin
plate or small holes alone are recorded (``found``, ``plates``, ``holes``, ``bricks``) but not suggested
(``why_not`` "no_measured_gain"): a plate lying flat reads right at every mesh detail, and small holes were
not shown nearer the truth at 0.5 mm beyond the scan-to-scan spread. With thin walls, a plate or holes found
too are said in the hint after the walls, with "not measured to help".
The flagged places are also given as bricks of 8 mesh detail (``bricks``), for fusing finer there
(ENGINE_PLAN X5, step 2).
"""
from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

from ohs.mesh.confidence import SIGMA_NONE_MM
from ohs.mesh.topology import as_mesh, face_areas, vertex_normals

__all__ = ["THIN_PRESET_ID", "THIN_PRESET_VOXEL_MM", "WALL_READS_VOXELS", "WALL_MIN_AREA_VOXELS2", "PLATE_MAX_VOXELS",
           "PLATE_MIN_SHARE", "ROUND_MAX_VOXELS", "find_thin_parts", "hint_words"]

VERSION = 1
#: the built-in preset this suggests (``ohs.app.presets``) and the mesh detail it sets
THIN_PRESET_ID = "thin-sheet-small-parts"
THIN_PRESET_NAME = "Thin sheet metal and small parts"
THIN_PRESET_VOXEL_MM = 0.5
#: a wall seen from both sides is thin when it reads at most this many times the mesh detail (module docstring)
WALL_READS_VOXELS = 2.7
#: ... over at least this many square voxels of surface, both faces counted
WALL_MIN_AREA_VOXELS2 = 100.0
#: the other side faces the other way: its normal's dot with the vertex normal is below this (37° from opposite)
OPPOSITE_COS = -0.8
#: measured at mesh detail 1 mm with the thin-wall rule: walls of 0.5 to 2 mm read this much too thick (mm)
WALL_EXCESS_MM = (0.1, 1.3)
#: a plate lying flat is thin when it stands at most this many times the mesh detail above the table ...
PLATE_MAX_VOXELS = 2.7
#: ... and its top is at least this share of the part's surface (the mesh less the table) ...
PLATE_MIN_SHARE = 0.5
#: ... and at least this many square voxels (20 x 20 mm at 1 mm: the size from which the table finder keeps a thin part)
PLATE_MIN_AREA_VOXELS2 = 400.0
#: a round hole is small up to this diameter, in mesh detail (and from ROUND_MIN_VOXELS: smaller is a pinhole)
ROUND_MAX_VOXELS = 16.0
ROUND_MIN_VOXELS = 3.0
HOLE_MIN_POINTS = 20  # wall points of one hole
HOLE_COVER = 0.6  # share of the circle's turn with wall points on it (a hole's wall is seen from above, not all round)
HOLE_REL_RMS = 0.2  # typical distance from the circle, as a share of its radius
HOLE_AXIS_RATIO = 0.2  # the wall normals' spread along the axis against across it: a round wall has one free direction
BRICK_VOXELS = 8  # the bricks of step 2, in mesh detail
MAX_RAYS = 2_000_000
MAX_LISTED = 10
MAX_BRICKS = 20_000

#: P1's measured numbers the words cite (VALIDATION.md "Presets: which settings matter, measured"; FEATURE_PLAN R5)
P1 = {"walls_nearer_mm": (0.23, 0.67), "walls_at_1": (1.7, 2.7), "walls_at_05": (1.0, 2.4), "walls_true": (0.5, 2.0),
      "time_x": (1.2, 2.4), "triangles_x": 4.0, "mem_gb": (7.0, 11.9), "shared_gb": (2.2, 3.0), "mem_at_mtri": (3.34, 6.4),  # R5; X5 2026-11-22
      "pc": "a mini PC with built-in graphics and 20 GB of memory"}


def _r(x: float, n: int = 2) -> float:
    return round(float(x), n)


def _unit_rows(N: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(N, axis=1, keepdims=True)
    return np.divide(N, n, out=np.zeros_like(N), where=n > 0)


def _vertex_areas(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    A = face_areas(V, F) / 3.0
    return np.bincount(F.reshape(-1), weights=np.repeat(A, 3), minlength=len(V))


def _cells(P: np.ndarray, cell: float) -> tuple[np.ndarray, np.ndarray]:
    """Group points into connected clusters of occupied cubes (26 neighbours): ``(label per point, n)``."""
    from scipy.sparse import coo_matrix  # noqa: PLC0415
    from scipy.sparse.csgraph import connected_components  # noqa: PLC0415

    if len(P) == 0:
        return np.zeros(0, np.int64), 0
    K = np.floor(P / cell).astype(np.int64)
    K -= K.min(axis=0) - 1
    m = int(K.max()) + 2
    code = (K[:, 0] * m + K[:, 1]) * m + K[:, 2]
    u, inv = np.unique(code, return_inverse=True)
    ki = np.stack([u // (m * m), (u // m) % m, u % m], axis=1)
    rows, cols = [], []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if (dx, dy, dz) <= (0, 0, 0):
                    continue
                c2 = ((ki[:, 0] + dx) * m + (ki[:, 1] + dy)) * m + (ki[:, 2] + dz)
                j = np.searchsorted(u, c2)
                ok = (j < len(u)) & (u[np.minimum(j, len(u) - 1)] == c2)
                rows.append(np.flatnonzero(ok))
                cols.append(j[ok])
    r = np.concatenate(rows) if rows else np.zeros(0, np.int64)
    c = np.concatenate(cols) if cols else np.zeros(0, np.int64)
    g = coo_matrix((np.ones(len(r)), (r, c)), shape=(len(u), len(u)))
    n, lab = connected_components(g, directed=False)
    return lab[inv], int(n)


def _bricks(P: np.ndarray, size: float) -> list[list[int]]:
    if len(P) == 0:
        return []
    return np.unique(np.floor(P / size).astype(np.int64), axis=0).tolist()


# --------------------------------------------------------------------------- thin walls
def _walls(cast, V: np.ndarray, N: np.ndarray, va: np.ndarray, measured: np.ndarray, idx: np.ndarray, step: int,
           v: float, opposite_sides: bool | None) -> tuple[dict, np.ndarray]:
    """Walls seen from both sides (module docstring): a ray from just behind the surface, into the part, meets the
    mesh's other side facing the other way within ``WALL_READS_VOXELS`` mesh detail."""
    limit = WALL_READS_VOXELS * v
    eps = 0.02 * v
    t, dot = cast(V[idx] - eps * N[idx], -N[idx])  # the ray's direction is -N: the other side faces along it
    t = t + eps
    opp = np.isfinite(t) & (dot > -OPPOSITE_COS) & (t <= limit)
    hit = idx[opp]
    reads = t[opp]
    w = va[hit] * step
    min_area = WALL_MIN_AREA_VOXELS2 * v * v
    lab, n = _cells(V[hit], 3.0 * v)  # the wall's two faces lie within 2.7 voxels: one cluster
    regions = []
    keep = np.zeros(len(hit), dtype=bool)
    for k in range(n):
        s = lab == k
        a = float(w[s].sum())
        if a < min_area:
            continue
        keep |= s
        P = V[hit[s]]
        q = np.quantile(reads[s], [0.1, 0.5, 0.9])
        regions.append({"centre": [_r(x, 1) for x in P.mean(axis=0)], "size_mm": _r(np.linalg.norm(np.ptp(P, axis=0)), 1),
                        "area_mm2": _r(a, 0), "reads_mm": _r(q[1]), "reads_range_mm": [_r(q[0]), _r(q[2])],
                        "estimate_mm": _estimate(float(q[1]), v, opposite_sides)})
    regions.sort(key=lambda r: -r["area_mm2"])
    total = float(va[measured].sum())
    area = float(w[keep].sum())
    rd = reads[keep]
    q = np.quantile(rd, [0.1, 0.5, 0.9]) if len(rd) else None
    out = {"found": bool(regions), "area_mm2": _r(area, 0), "share": _r(area / total, 4) if total > 0 else 0.0,
           "reads_mm": _r(q[1]) if q is not None else None,
           "reads_range_mm": [_r(q[0]), _r(q[2])] if q is not None else None,
           "estimate_mm": _estimate(float(q[1]), v, opposite_sides) if q is not None else None,
           "n_regions": len(regions), "regions": regions[:MAX_LISTED],
           "all_readings_mm2": _r(float(w.sum()), 0), "rays": int(len(idx))}
    return out, hit[keep]


def _estimate(reads: float, v: float, opposite_sides: bool | None) -> list[float] | None:
    """The true thickness from a reading (module docstring), where it was measured: mesh detail 1 mm with the
    thin-wall rule on; else None."""
    if not opposite_sides or not 0.9 <= v <= 1.1:
        return None
    lo, hi = WALL_EXCESS_MM
    return [_r(max(reads - hi, 0.0), 1), _r(max(reads - lo, 0.0), 1)]


# --------------------------------------------------------------------------- thin plates lying flat
def _wq(x: np.ndarray, w: np.ndarray, q: float) -> float:
    o = np.argsort(x, kind="stable")
    c = np.cumsum(w[o])
    return float(x[o][min(int(np.searchsorted(c, q * c[-1])), len(o) - 1)])


def _plates(V: np.ndarray, F: np.ndarray, N: np.ndarray, sigma: np.ndarray | None, v: float) -> dict:
    """A thin plate lying flat (module docstring): what "Remove the table" leaves of the scan is a plateau, nearly
    all of it (99 % of its area) at most ``PLATE_MAX_VOXELS`` mesh detail above the table, its top (faces within 30°
    of the table's normal) at least ``PLATE_MIN_SHARE`` of it and at least ``PLATE_MIN_AREA_VOXELS2``, standing
    clearly above the table's scatter (the finder's own limit)."""
    from ohs.mesh.support import SupportRefused, find_support  # noqa: PLC0415
    from ohs.mesh.topology import face_normals  # noqa: PLC0415

    try:
        f = find_support(V, F, normals=N, sigma=sigma)
    except SupportRefused:
        return {"found": False, "table": False, "plates": []}
    K = np.asarray(f.kept, dtype=np.int64)
    if len(K) == 0:
        return {"found": False, "table": True, "plates": []}
    n = np.asarray(f.normal, dtype=np.float64)
    C = V[F[K]].mean(axis=1)
    h = C @ n + float(f.d)
    fn, a = face_normals(V, F[K])
    up = fn @ n > math.cos(math.radians(30.0))
    part, top = float(a.sum()), float(a[up].sum())
    info = {"part_area_mm2": _r(part, 0), "top_area_mm2": _r(top, 0),
            "h99_mm": _r(_wq(h, a, 0.99), 3) if part > 0 else None}
    if top < PLATE_MIN_AREA_VOXELS2 * v * v or top < PLATE_MIN_SHARE * part or not up.any():
        return {"found": False, "table": True, "plates": [], **info}
    if info["h99_mm"] is None or info["h99_mm"] > PLATE_MAX_VOXELS * v:
        return {"found": False, "table": True, "plates": [], **info}
    t = _wq(h[up], a[up], 0.5)
    limit = float(f.thin_limit_mm) if math.isfinite(float(f.thin_limit_mm)) else 0.0
    if not t > max(limit, 0.0):
        return {"found": False, "table": True, "plates": [], **info}
    top_f = up & (h >= 0.5 * t)  # the plateau itself: crumbs of table left at its foot are not its top
    lab, n_cl = _cells(C[top_f], 3.0 * v)  # ... and its largest piece: stray faces far out at the scan's border neither
    if n_cl > 1:
        big = int(np.argmax(np.bincount(lab, weights=a[top_f], minlength=n_cl)))
        top_f[np.flatnonzero(top_f)[lab != big]] = False
    ht, at = h[top_f], a[top_f]
    t = _wq(ht, at, 0.5)
    scatter = 1.4826 * _wq(np.abs(ht - t), at, 0.5)
    P = C[top_f]
    Q = P - (P @ n)[:, None] * n
    span = np.percentile(Q, 98, axis=0) - np.percentile(Q, 2, axis=0)  # across, corner to corner (stray faces left out)
    plate = {"centre": [_r(x, 1) for x in P.mean(axis=0)], "size_mm": _r(float(np.linalg.norm(span)), 0),
             "area_mm2": _r(float(at.sum()), 0), "height_mm": _r(t, 3), "scatter_mm": _r(scatter, 3),
             "share_of_part": _r(top / part, 3), "table_limit_mm": _r(limit, 3)}
    return {"found": True, "table": True, "plates": [plate], **info}


# --------------------------------------------------------------------------- small round holes
def _holes(cast, V: np.ndarray, N: np.ndarray, idx: np.ndarray, v: float) -> tuple[dict, np.ndarray]:
    """Round holes and bores whose walls the scan saw: a ray out of the surface along its normal meets the
    mesh facing back (``OPPOSITE_COS``) within the hole's size; such wall points, joined into clusters, that lie
    round an axis their normals avoid (module docstring)."""
    t, dot = cast(V[idx] + 0.02 * v * N[idx], N[idx])
    t = t + 0.02 * v
    lo, hi = ROUND_MIN_VOXELS * v, ROUND_MAX_VOXELS * v
    wall = idx[np.isfinite(t) & (dot < OPPOSITE_COS) & (t >= lo) & (t <= hi)]
    lab, n = _cells(V[wall], 3.0 * v)
    found: list[dict] = []
    pts: list[np.ndarray] = []
    for k in range(n):
        s = wall[lab == k]
        if len(s) < HOLE_MIN_POINTS:
            continue
        fit = _axis_circle(V[s], N[s])
        if fit is None:
            continue
        d, rel, cover, axis_ratio, centre = fit
        if not (lo <= d <= hi) or rel > HOLE_REL_RMS or cover < HOLE_COVER or axis_ratio > HOLE_AXIS_RATIO:
            continue
        found.append({"centre": [_r(x, 1) for x in centre], "diameter_mm": _r(d, 1), "pm_mm": _r(v, 2),
                      "points": int(len(s))})
        pts.append(s)
    order = np.argsort([f["diameter_mm"] for f in found], kind="stable") if found else []
    feats = [found[k] for k in order]
    out = {"found": bool(feats), "count": len(feats),
           "smallest_mm": feats[0]["diameter_mm"] if feats else None,
           "largest_mm": feats[-1]["diameter_mm"] if feats else None,
           "features": feats[:MAX_LISTED], "wall_points": int(len(wall))}
    return out, (np.concatenate(pts) if pts else np.zeros(0, np.int64))


def _axis_circle(P: np.ndarray, N: np.ndarray) -> tuple[float, float, float, float, np.ndarray] | None:
    """A circle round the axis the wall normals ``N`` avoid (the least eigenvector of their scatter), Kasa's fit of
    the points seen along it: ``(diameter, typical distance / radius, share of the turn with points, the normals'
    spread along the axis against across it, centre)``. A slot or a seam has two such directions and no circle."""
    w, U = np.linalg.eigh(N.T @ N / len(N))
    ax, u = U[:, 0], U[:, 2]
    v = np.cross(ax, u)
    axis_ratio = float(w[0] / max(w[2], 1e-12))
    c0 = P.mean(axis=0)
    Q = P - c0
    x, y = Q @ u, Q @ v
    A = np.c_[2 * x, 2 * y, np.ones_like(x)]
    sol, *_ = np.linalg.lstsq(A, x * x + y * y, rcond=None)
    cx, cy = float(sol[0]), float(sol[1])
    r2 = float(sol[2]) + cx * cx + cy * cy
    if not r2 > 0:
        return None
    r = math.sqrt(r2)
    rr = np.hypot(x - cx, y - cy)
    rel = float(np.sqrt(np.mean((rr - r) ** 2))) / r
    ang = np.arctan2(y - cy, x - cx)
    cover = len(np.unique(np.floor((ang + np.pi) / (2 * np.pi) * 36).astype(np.int64) % 36)) / 36.0
    return 2.0 * r, rel, cover, axis_ratio, c0 + cx * u + cy * v


def _caster(V: np.ndarray, F: np.ndarray):
    """``cast(origins, directions) -> (t, dot)``: the distance to the first triangle hit (inf for none) and the dot of
    its normal with the direction's (Open3D's ``RaycastingScene``, built once)."""
    import open3d as o3d  # noqa: PLC0415

    o = V.mean(axis=0)
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.core.Tensor((V - o).astype(np.float32)), o3d.core.Tensor(F.astype(np.uint32)))

    def cast(P: np.ndarray, D: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if len(P) == 0:
            return np.zeros(0), np.zeros(0)
        ans = scene.cast_rays(o3d.core.Tensor(np.concatenate([P - o, D], axis=1).astype(np.float32)))
        t = ans["t_hit"].numpy().astype(np.float64)
        pn = ans["primitive_normals"].numpy().astype(np.float64)
        return t, np.einsum("ij,ij->i", pn, D)

    return cast


# --------------------------------------------------------------------------- the finding
def find_thin_parts(V: np.ndarray, F: np.ndarray, *, voxel_mm: float, normals: np.ndarray | None = None,
                    sigma: np.ndarray | None = None, preset_id: str | None = None,
                    opposite_sides: bool | None = None) -> dict[str, Any]:
    """What of the finished mesh ``(V, F)`` a finer mesh detail could help (module docstring). ``voxel_mm``: the
    mesh detail it was made at; ``sigma``: each vertex's confidence (made-up vertices, the fill of Fill holes, are
    left out); ``preset_id``: the scan's preset; ``opposite_sides``: the thin-wall rule was on (``report.json``'s
    ``params.reconstruction.opposite_mm`` not None), for the thickness estimate. Reads only; returns plain data."""
    t0 = time.perf_counter()
    v = float(voxel_mm)
    if not (math.isfinite(v) and v > 0):
        raise ValueError(f"voxel_mm must be a positive number, got {voxel_mm!r}")
    limits = {"wall_mm": _r(WALL_READS_VOXELS * v), "wall_min_area_mm2": _r(WALL_MIN_AREA_VOXELS2 * v * v, 0),
              "plate_mm": _r(PLATE_MAX_VOXELS * v), "plate_min_share": PLATE_MIN_SHARE,
              "round_mm": [_r(ROUND_MIN_VOXELS * v, 1), _r(ROUND_MAX_VOXELS * v, 1)]}
    out: dict[str, Any] = {"version": VERSION, "voxel_mm": v, "preset": preset_id, "limits": limits,
                           "suggest_preset": THIN_PRESET_ID, "suggest_voxel_mm": THIN_PRESET_VOXEL_MM}
    if preset_id == THIN_PRESET_ID or v <= THIN_PRESET_VOXEL_MM + 1e-9:
        out.update(found=None, suggest=False, why_not="made_fine", walls=None, plates=None, holes=None, bricks=None,
                   ms=_r(1e3 * (time.perf_counter() - t0), 0))
        return out
    V, F = as_mesh(V, F)
    if len(F) == 0:
        out.update(found=False, suggest=False, why_not="empty", walls=None, plates=None, holes=None, bricks=None,
                   ms=_r(1e3 * (time.perf_counter() - t0), 0))
        return out
    N = _unit_rows(np.asarray(normals, dtype=np.float64)) if normals is not None and len(normals) == len(V) \
        else vertex_normals(V, F)
    S = np.asarray(sigma, dtype=np.float64) if sigma is not None and len(sigma) == len(V) else None
    used = np.zeros(len(V), dtype=bool)
    used[F.reshape(-1)] = True
    measured = used & (S < SIGMA_NONE_MM if S is not None else True)
    va = _vertex_areas(V, F)
    idx = np.flatnonzero(measured & (np.linalg.norm(N, axis=1) > 0.5))
    step = max(1, int(math.ceil(len(idx) / MAX_RAYS)))
    idx = idx[::step]
    cast = _caster(V, F)
    walls, wall_v = _walls(cast, V, N, va, measured, idx, step, v, opposite_sides)
    holes, hole_v = _holes(cast, V, N, idx, v)
    plates = _plates(V, F, N, S, v)
    found = bool(walls["found"] or plates["found"] or holes["found"])
    # suggested only where a gain was measured: thin walls seen from both sides (P1). A thin plate or small holes alone
    # are recorded but not suggested: a plate lying flat reads right at every mesh detail, small holes were not shown
    # nearer the truth at 0.5 mm beyond the scan-to-scan spread
    suggest = bool(walls["found"])
    why_not = None if suggest else "no_measured_gain" if found else "nothing_found"
    size = BRICK_VOXELS * v
    flagged = np.concatenate([wall_v, hole_v])
    keys = _bricks(V[flagged], size)
    out.update(found=found, suggest=suggest, why_not=why_not, walls=walls, plates=plates,
               holes=holes, bricks={"size_mm": _r(size, 2), "n": len(keys), "keys": keys[:MAX_BRICKS],
                                    "complete": len(keys) <= MAX_BRICKS},
               ms=_r(1e3 * (time.perf_counter() - t0), 0))
    return out


# --------------------------------------------------------------------------- the words
def _mm(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".") if abs(x) < 10 else f"{x:.1f}".rstrip("0").rstrip(".")


def _range(a: float, b: float, unit: str = "mm") -> str:
    return f"{_mm(a)} {unit}" if _mm(a) == _mm(b) else f"{_mm(a)} to {_mm(b)} {unit}"


def _minutes(s: float) -> str:
    return f"{s:.0f} s" if s < 90 else f"{s / 60:.0f} min" if s >= 600 else f"{s / 60:.1f} min"


def hint_words(finding: dict | None, *, seconds: float | None = None, triangles: int | None = None,
               graphics_card: bool | None = True) -> dict | None:
    """The hint in the app's words, or None when there is nothing to suggest: ``{title, found: [...], gain: [...],
    cost: [...], measured, action, preset_id, preset_name}``. ``seconds``: how long the Make mesh took;
    ``triangles``: the mesh's; ``graphics_card``: the scan was made with one (the numbers were)."""
    if not isinstance(finding, dict) or not finding.get("suggest"):
        return None
    v = float(finding.get("voxel_mm") or 1.0)
    walls = finding.get("walls") or {}
    plates = (finding.get("plates") or {}).get("plates") or []
    rnd = finding.get("holes") or {}
    found: list[str] = []
    gain: list[str] = []
    if walls.get("found"):
        est = walls.get("estimate_mm")
        lo, hi = walls.get("reads_range_mm") or [walls["reads_mm"], walls["reads_mm"]]
        line = (f"Thin walls: {walls['area_mm2']:,.0f} mm² of the surface ({100 * walls['share']:.1f} %) lies on walls "
                f"seen from both sides that read {_mm(walls['reads_mm'])} mm thick (most of it {_range(lo, hi)})")
        line += ("." if not est else f", probably up to {_mm(est[1])} mm in truth." if est[0] <= 0.05
                 else f", probably {_range(est[0], est[1])} in truth.")
        found.append(line)
        a, b = P1["walls_nearer_mm"]
        gain.append(f"Walls seen from both sides read {_range(a, b)} nearer their true thickness at mesh detail 0.5 mm: "
                    f"simulated walls {_range(*P1['walls_true'])} thick read {_range(*P1['walls_at_05'])} instead of "
                    f"{_range(*P1['walls_at_1'])}.")
    if not walls.get("found"):  # suggested only for thin walls (find_thin_parts)
        return None
    if plates:
        p = plates[0]
        found.append(f"A thin plate lying flat: {_mm(p['height_mm'])} mm thick (± {_mm(max(p['scatter_mm'], 0.01))} mm), "
                     f"{p['size_mm']:,.0f} mm across.")
        gain.append("A plate lying flat already reads its thickness within 0.01 mm at mesh detail 1 mm; the finer "
                    "detail was not measured to change that.")
    if rnd.get("found"):
        n = int(rnd["count"])
        found.append(f"{'A small round hole or bore' if n == 1 else f'{n:,} small round holes or bores'}, "
                     + (f"Ø {_range(rnd['smallest_mm'], rnd['largest_mm'])}" if n > 1 else f"Ø {_mm(rnd['smallest_mm'])} mm")
                     + f" (± {_mm(v)} mm). Holes this small often read too small in a scan, or closed.")
        gain.append("Holes: the finer detail changes how a hole reads (a simulated Ø 25 mm hole by 0.6 to 0.8 mm), "
                    "but was not measured to bring small holes nearer the truth beyond the scan-to-scan spread. Holes "
                    "over open space are mostly filled in by the cameras, which the finer detail does not mend.")
    title = "Thin walls found"  # the reason for the hint; a plate or holes found too are said after the walls
    t0, t1 = P1["time_x"]
    cost = []
    if seconds and seconds > 0:
        cost.append(f"Time: {t0:g} to {t1:g} times as long. This mesh took {_minutes(seconds)}; with the preset about "
                    f"{_minutes(t0 * seconds)} to {_minutes(t1 * seconds)}.")
    else:
        cost.append(f"Time: {t0:g} to {t1:g} times as long.")
    if triangles:
        cost.append(f"Triangles: about {P1['triangles_x']:g} times as many, about {P1['triangles_x'] * triangles / 1e6:,.1f} "
                    f"million instead of {triangles / 1e6:,.2f} million.")
    else:
        cost.append(f"Triangles: about {P1['triangles_x']:g} times as many.")
    cost.append(f"Memory: scans made at mesh detail 0.5 mm with {P1['mem_at_mtri'][0]:g} to {P1['mem_at_mtri'][1]:g} "
                f"million triangles took {P1['mem_gb'][0]:g} to {P1['mem_gb'][1]:g} GB of memory and "
                f"{P1['shared_gb'][0]:g} to {P1['shared_gb'][1]:g} GB of the graphics card's shared memory.")
    measured = (f"Measured on simulated scans, on {P1['pc']}."
                + ("" if graphics_card else " This scan was made without a graphics card, where the finer detail "
                                           "was not measured."))
    return {"title": title, "intro": f"The preset “{THIN_PRESET_NAME}” makes the mesh at mesh detail "
                                     f"{_mm(THIN_PRESET_VOXEL_MM)} mm instead of {_mm(v)} mm, and changes nothing else.",
            "found": found, "gain": gain, "cost": cost, "measured": measured,
            "action": "Make the mesh again with this preset", "preset_id": THIN_PRESET_ID, "preset_name": THIN_PRESET_NAME}
