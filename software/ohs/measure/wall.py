"""A thin wall's thickness read from the scan's own camera frames at a click (ENGINE_PLAN X16).

Why: the fused mesh holds a thin wall too thick by its own make-up (on simulated walls of 0.5 to 2 mm the 1 mm mesh
reads 0.16 to 0.84 mm too thick even at true camera poses), while the stereo depth itself places each face right:
each face a plane fitted to the raw depth points of the frames that see it reads every simulated wall of 0.5 to 3 mm
within 0.07 mm at true poses. What tracking adds stays: the frames of a wall's two sides are placed slightly too far
apart, by +0.29 to +0.99 mm on the simulated standing walls and sheet part (``VALIDATION.md``, X16). So the reading
is one-sided: **at most** this thick, possibly up to about 1 mm thinner. It never thins or removes anything; it reads.

The reading (:func:`read_wall_thickness`), every number fixed by the study (``tools/wall_thickness_study.py``):

1. **The wall at the click, from the mesh** (:func:`wall_at_click`): of the mesh vertices nearest the click (up to
   :data:`CLICK_TRIES` within :data:`CLICK_MM`), the first whose ray into the part meets the mesh's other face,
   facing the other way, within :data:`WALL_MAX_MM` (4 mm: a 3 mm wall reads up to 3.7 mm at 1 mm mesh detail). Its
   two faces are the vertices within :data:`PATCH_MM` whose normals agree with the clicked face's or its opposite
   (:data:`NORMAL_COS`). A plane is fitted to each; their mean normal is the wall's axis, the middle its origin.
2. **The frames that see it** (:func:`frames_seeing`): every tracked frame in whose image the click falls (not too
   near the border, inside the depth range) and that sees one of the two faces less steeply than
   :data:`MIN_VIEW_COS`; at most :data:`MAX_PER_SIDE` per side, spread over the scan.
3. **Their depth:** the stereo matcher run again on those frames (:class:`ohs.measure.frame_depth.FrameDepth`), at full
   resolution, with the pipeline's settings. Recomputed rather than kept from Make mesh: keeping every frame's depth costs about 7.8 MB a
   frame on disk (0.9 to 1.9 GB for a scan of 120 to 240 frames, kept for as long as the scan), while 20 to 30 frames
   take a few seconds with a graphics card and well under a minute without one.
4. **The two faces from the raw points** (:func:`select_faces`): points within ``PATCH_MM`` of the origin along the
   wall, within the mesh's half reading plus :data:`BAND_MM` across it, normals agreeing with a face's
   (``NORMAL_COS``). The faces are told apart by their normals only, never by a cut at the middle, so a wall thinner
   than the depth's scatter is not thinned by the selection. The axis is taken again twice from the faces' own
   planes (:data:`AXIS_ROUNDS`).
5. **Each face a plane**, trimmed three times at 2.5 robust sigma; the thickness is the distance between them at the
   click; its scatter one standard deviation over :data:`BOOT` resamples of the frames (:func:`read_faces`).

**Refused, in words** (:class:`WallRefused`): no wall seen from both sides at the click; one side not seen by any
frame; too few frames (:data:`MIN_FRAMES`) or points (:data:`MIN_POINTS`) on a side; the two faces not parallel
enough to be one wall (more than :data:`MAX_TILT_DEG`); a face's points scattering more than :data:`MAX_FACE_SD_MM`
(cannot tell). A scan tracked by its markers is refused by the caller (the server): there the two sides came out
0.10 to 0.47 mm too CLOSE on the simulated walls, so "at most" would be false.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

import numpy as np

from ohs.measure.frame_depth import frame_points, visible_from

__all__ = ["WallRefused", "WallReading", "WallAtClick", "wall_at_click", "frames_seeing", "select_faces",
           "read_faces", "read_wall_thickness", "wall_words", "PATCH_MM", "WALL_MAX_MM", "TRACKING_EXCESS_MM",
           "MARKERS_WORDS"]

PATCH_MM = 10.0  # the region round the click, along the wall
BAND_MM = 1.0  # beyond the mesh's face, across the wall
NORMAL_COS = 0.7  # a point's normal against its face's
OPPOSITE_COS = 0.8  # the far face must face the other way within about 37 degrees
WALL_MAX_MM = 4.0  # the other face within this: walls up to about 3 mm (a 3 mm wall reads up to 3.7 mm in the mesh)
CLICK_MM = 3.0  # the click at most this far from the mesh
CLICK_TRIES = 50  # the vertices nearest the click tried in turn
PIXEL_STEP = 2
MIN_FRAMES = 3
MIN_POINTS = 300
MIN_FRAME_POINTS = 20  # a frame counts for a face with at least this many points on it
TRIM_SIGMA = 2.5
BOOT = 200
AXIS_ROUNDS = 2
MAX_TILT_DEG = 5.0  # the two faces' planes further apart than this are not one wall (simulated walls: 0.0 to 1.4)
MAX_FACE_SD_MM = 0.7  # a face's points scattering more than this: cannot tell (simulated faces: 0.29 to 0.60 mm)
MAX_PER_SIDE = 15
MIN_VIEW_COS = 0.2  # a frame sees a face less steeply than about 78 degrees
BORDER_PX = 40
#: what tracking added on the simulated standing walls and sheet part (raw reading at tracked poses minus truth):
#: +0.29 to +0.99 mm (one recording's flange -0.01); so "up to about 1 mm thinner"
TRACKING_EXCESS_MM = (0.29, 0.99)
MARKERS_WORDS = ("Not offered for a scan tracked by its markers: on simulated thin walls that tracking placed the "
                 "wall's two sides up to 0.5 mm too close together, so the reading could show a wall thinner than it is.")


class WallRefused(ValueError):
    """No reading at this click, and why (plain words); ``hint``: what to do."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


def _unit(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    return x / np.linalg.norm(x)


def wall_basis(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(n, e1, e2)``: the wall's axis (unit) and two directions along the wall."""
    n = _unit(axis)
    a = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = _unit(np.cross(n, a))
    return n, e1, np.cross(n, e1)


def fit_face(Q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A plane ``s = a + b u + c v`` through points ``Q`` (columns u, v, s), trimmed three times at ``TRIM_SIGMA``
    robust sigma: ``(coef, kept)``."""
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


# --------------------------------------------------------------------------- the wall at the click, from the mesh
@dataclass
class WallAtClick:
    """The wall the mesh shows at a click."""

    origin: np.ndarray  # the middle between the mesh's two faces at the click
    axis: np.ndarray  # unit, from the clicked face's side to the other
    half_mm: float  # half the mesh's reading
    mesh_mm: float  # the mesh's own thickness there
    vertices: tuple[int, int]  # mesh vertices on each face
    clicked: np.ndarray  # the clicked vertex


def _caster(V: np.ndarray, F: np.ndarray) -> Callable[[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]:
    import open3d as o3d  # noqa: PLC0415

    o = V.mean(axis=0)
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.core.Tensor((V - o).astype(np.float32)), o3d.core.Tensor(np.asarray(F).astype(np.uint32)))

    def cast(P: np.ndarray, D: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        ans = scene.cast_rays(o3d.core.Tensor(np.concatenate([P - o, D], axis=1).astype(np.float32)))
        t = ans["t_hit"].numpy().astype(np.float64)
        pn = ans["primitive_normals"].numpy().astype(np.float64)
        return t, np.einsum("ij,ij->i", pn, D)

    return cast


def wall_at_click(V: np.ndarray, F: np.ndarray, click: Sequence[float], *, normals: np.ndarray | None = None,
                  cast: Callable | None = None) -> WallAtClick:
    """The wall at ``click`` (module docstring, 1). Raises :class:`WallRefused`."""
    from ohs.mesh.topology import vertex_normals  # noqa: PLC0415

    V = np.asarray(V, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64)
    click = np.asarray(click, dtype=np.float64).reshape(3)
    Nv = vertex_normals(V, F) if normals is None else np.asarray(normals, dtype=np.float64)
    d = np.linalg.norm(V - click, axis=1)
    order = np.argsort(d)[:CLICK_TRIES]
    order = order[d[order] <= CLICK_MM]
    if len(order) == 0:
        raise WallRefused("The click is not on the mesh.", "Click on one face of the wall.")
    Nk = np.array([_unit(Nv[i]) if np.linalg.norm(Nv[i]) > 0 else np.zeros(3) for i in order])
    cast = cast or _caster(V, F)
    ts, dots = cast(V[order] - 0.02 * Nk, -Nk)
    ts = ts + 0.02
    ok = np.isfinite(ts) & (ts <= WALL_MAX_MM) & (dots > OPPOSITE_COS)
    if not ok.any():
        t0 = float(ts[0])
        if math.isfinite(t0) and t0 > WALL_MAX_MM and dots[0] > OPPOSITE_COS:
            raise WallRefused(f"The wall here is thicker than this reading is for: its other face is {t0:.1f} mm away in "
                              f"the mesh, and the reading is made for thin walls up to about 3 mm (the mesh reads "
                              f"them up to {WALL_MAX_MM:g} mm).", "Measure a thicker wall with two planes (the Fit tool).")
        raise WallRefused("No wall seen from both sides at the click: behind this face the mesh has no second face "
                          "facing the other way.", "Click on one face of a thin wall whose both sides were scanned.")
    j = int(np.argmax(ok))
    k = int(order[j])
    c, n0, t = V[k], Nk[j], float(ts[j])
    near = d <= PATCH_MM
    s = (V - c) @ n0
    cos = Nv @ n0
    fa = near & (cos > NORMAL_COS) & (np.abs(s) <= 1.0)
    fb = near & (cos < -NORMAL_COS) & (np.abs(s + t) <= 1.0)
    if fa.sum() < 20 or fb.sum() < 20:
        raise WallRefused("The mesh shows only one face of the wall here.", "Click where both sides were scanned.")
    axis = _unit(Nv[fa].mean(axis=0) - Nv[fb].mean(axis=0))
    n, e1, e2 = wall_basis(axis)
    QA = np.column_stack([(V[fa] - c) @ e1, (V[fa] - c) @ e2, (V[fa] - c) @ n])
    QB = np.column_stack([(V[fb] - c) @ e1, (V[fb] - c) @ e2, (V[fb] - c) @ n])
    ca, _ = fit_face(QA)
    cb, _ = fit_face(QB)
    mid = 0.5 * (ca[0] + cb[0])
    return WallAtClick(origin=c + mid * n, axis=n, half_mm=0.5 * float(ca[0] - cb[0]),
                       mesh_mm=float(ca[0] - cb[0]), vertices=(int(fa.sum()), int(fb.sum())), clicked=c)


# --------------------------------------------------------------------------- the frames and their points
def frames_seeing(poses: dict[int, np.ndarray], K: np.ndarray, size: tuple[int, int], wall: WallAtClick,
                  depth_range: tuple[float, float], max_per_side: int = MAX_PER_SIDE) -> tuple[list[int], list[int]]:
    """The frame indices that see each face of ``wall`` (face A, on the axis's side, and face B), at most
    ``max_per_side`` each, spread evenly over the scan (module docstring, 2)."""
    w, h = int(size[0]), int(size[1])
    a, b = [], []
    for i in sorted(poses):
        T = np.asarray(poses[i], dtype=np.float64)
        if not np.all(np.isfinite(T)):
            continue
        if not visible_from(T, K, (w, h), wall.origin, depth_range, BORDER_PX):
            continue
        view = _unit(wall.origin - T[:3, 3])
        cos = float(-view @ wall.axis)  # > 0: the camera on the axis's side, sees face A
        if cos > MIN_VIEW_COS:
            a.append(i)
        elif cos < -MIN_VIEW_COS:
            b.append(i)

    def spread(x: list[int]) -> list[int]:
        if len(x) <= max_per_side:
            return x
        return [x[int(round(k))] for k in np.linspace(0, len(x) - 1, max_per_side)]

    return spread(a), spread(b)


def select_faces(frames: Iterable[tuple[np.ndarray, np.ndarray]], origin: np.ndarray, axis: np.ndarray, half_mm: float,
                 patch_mm: float = PATCH_MM, band_mm: float = BAND_MM) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Each frame's points ``(P, N)`` on face A (normal along ``axis``) and on face B (against it), in the wall's
    coordinates ``(u, v, s)`` (module docstring, 4)."""
    n, e1, e2 = wall_basis(axis)
    A_out, B_out = [], []
    lim = half_mm + band_mm
    for P, N in frames:
        d = P - origin
        u, v, s = d @ e1, d @ e2, d @ n
        near = (u * u + v * v <= patch_mm * patch_mm) & (np.abs(s) <= lim)
        c = N @ n
        Q = np.column_stack([u, v, s])
        A_out.append(Q[near & (c > NORMAL_COS)])
        B_out.append(Q[near & (c < -NORMAL_COS)])
    return A_out, B_out


def _thickness(A: list[np.ndarray], B: list[np.ndarray]) -> tuple[float, dict]:
    QA, QB = np.concatenate(A), np.concatenate(B)
    ca, ma = fit_face(QA)
    cb, mb = fit_face(QB)
    tilt = math.degrees(math.atan(math.hypot(ca[1] - cb[1], ca[2] - cb[2])))
    sd = [float(1.4826 * np.median(np.abs(Q[m, 2] - np.median(Q[m, 2])))) for Q, m in ((QA, ma), (QB, mb))]
    return float(ca[0] - cb[0]), {"tilt_deg": tilt, "face_sd_mm": sd, "coef": (ca, cb)}


def read_faces(A: list[np.ndarray], B: list[np.ndarray], *, boot: int = BOOT, seed: int = 0) -> dict:
    """The thickness from each frame's face points: ``{mm, pm_mm, frames, points, tilt_deg, face_sd_mm}``; one standard
    deviation over ``boot`` resamples of the frames. Raises :class:`WallRefused` when a side has too few frames or
    points, the faces are not parallel enough, or a face scatters too much."""
    fa = [k for k, q in enumerate(A) if len(q) >= MIN_FRAME_POINTS]
    fb = [k for k, q in enumerate(B) if len(q) >= MIN_FRAME_POINTS]
    na, nb = sum(len(A[k]) for k in fa), sum(len(B[k]) for k in fb)
    for f, npts in ((fa, na), (fb, nb)):
        if len(f) < MIN_FRAMES or npts < MIN_POINTS:
            raise WallRefused(f"One side of the wall was seen by too few camera frames here ({len(f)} frames, {npts:,} "
                              f"points; at least {MIN_FRAMES} frames and {MIN_POINTS} points are needed).",
                              "Click where both sides were scanned from several directions.")
    t, det = _thickness([A[k] for k in fa], [B[k] for k in fb])
    if det["tilt_deg"] > MAX_TILT_DEG:
        raise WallRefused(f"The two sides here are not parallel enough to be one wall ({det['tilt_deg']:.1f} degrees "
                          f"apart; at most {MAX_TILT_DEG:g}).", "Click on a flat part of the wall.")
    if max(det["face_sd_mm"]) > MAX_FACE_SD_MM:
        raise WallRefused(f"The camera frames' points on one side scatter too much here ({max(det['face_sd_mm']):.2f} mm;"
                          f" at most {MAX_FACE_SD_MM:g}): the reading cannot tell this wall's thickness.",
                          "Click on a flat part of the wall, away from its edges.")
    rng = np.random.default_rng(seed)
    bs = []
    for _ in range(boot):
        sa = rng.choice(fa, size=len(fa), replace=True)
        sb = rng.choice(fb, size=len(fb), replace=True)
        bs.append(_thickness([A[k] for k in sa], [B[k] for k in sb])[0])
    pm = float(np.std(bs, ddof=1)) if boot > 1 else 0.0
    return {"mm": t, "pm_mm": pm, "frames": (len(fa), len(fb)), "points": (na, nb), "tilt_deg": det["tilt_deg"],
            "face_sd_mm": det["face_sd_mm"]}


# --------------------------------------------------------------------------- the whole reading
@dataclass
class WallReading:
    """A wall's thickness read from the camera frames at a click. ``at_most_mm`` is what the user is told."""

    at_most_mm: float  # the reading plus twice its scatter (at least 0.05 mm), rounded up to 0.01 mm
    reading_mm: float
    pm_mm: float
    mesh_mm: float
    frames: tuple[int, int]
    points: tuple[int, int]
    tilt_deg: float
    seconds: float
    words: list[str] = field(default_factory=list)
    timings_s: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"at_most_mm": self.at_most_mm, "reading_mm": round(self.reading_mm, 4), "pm_mm": round(self.pm_mm, 4),
                "mesh_mm": round(self.mesh_mm, 3), "frames": list(self.frames), "points": list(self.points),
                "tilt_deg": round(self.tilt_deg, 2), "seconds": round(self.seconds, 1), "words": list(self.words),
                "timings_s": {k: round(v, 2) for k, v in self.timings_s.items()}}


def wall_words(at_most_mm: float, frames: tuple[int, int], mesh_mm: float | None = None) -> list[str]:
    """The reading's sentences: one-sided, never "t ± s"."""
    n = int(frames[0]) + int(frames[1])
    out = [f"At most {at_most_mm:.2f} mm thick here (read from {n} camera frames: {frames[0]} on one side, "
           f"{frames[1]} on the other).",
           f"The scan cannot rule out that the wall is up to about {math.ceil(TRACKING_EXCESS_MM[1]):g} mm thinner: "
           f"scans place a thin wall's two sides slightly too far apart (on simulated walls by "
           f"{TRACKING_EXCESS_MM[0]:.1f} to {TRACKING_EXCESS_MM[1]:.1f} mm).",
           "For the real thickness, lay the part flat and scan it from above, or measure it with calipers."]
    if mesh_mm is not None:
        out.append(f"The mesh reads {mesh_mm:.2f} mm here; the mesh holds thin walls thicker than the frames do.")
    return out


def read_wall_thickness(V: np.ndarray, F: np.ndarray, click: Sequence[float], *, poses: dict[int, np.ndarray],
                        K: np.ndarray, image_size: tuple[int, int], depth_range: tuple[float, float],
                        depth_of: Callable[[int], np.ndarray], boot: int = BOOT, cast: Callable | None = None) -> WallReading:
    """The reading at ``click`` on the mesh ``(V, F)``, in the frame of ``poses`` (frame index -> ``T_wc`` of the
    rectified left camera, the scan's final camera path): ``K`` and ``image_size`` that camera's, ``depth_range`` the
    scan's, ``depth_of(index)`` a frame's depth map (mm, 0 = none) on that camera. Raises :class:`WallRefused`."""
    t0 = time.perf_counter()
    tm: dict[str, float] = {}
    wall = wall_at_click(V, F, click, cast=cast)
    tm["wall"] = time.perf_counter() - t0
    fa, fb = frames_seeing(poses, K, image_size, wall, depth_range)
    if not fa or not fb:
        raise WallRefused("Only one side of this wall was seen by the scan's camera frames here.",
                          "Scan the other side of the wall too, then make the mesh again.")
    t1 = time.perf_counter()
    reach = PATCH_MM + wall.half_mm + BAND_MM + 1.0
    frames = []
    for i in fa + fb:
        frames.append(frame_points(depth_of(i), K, np.asarray(poses[i], dtype=np.float64), near=(wall.origin, reach)))
    tm["depth"] = time.perf_counter() - t1
    t2 = time.perf_counter()
    origin, axis = wall.origin, wall.axis
    for _ in range(AXIS_ROUNDS):  # the axis from the raw faces themselves, not from the mesh's few vertices
        A, B = select_faces(frames, origin, axis, wall.half_mm)
        if sum(map(len, A)) < MIN_POINTS or sum(map(len, B)) < MIN_POINTS:
            break
        ca, _ = fit_face(np.concatenate(A))
        cb, _ = fit_face(np.concatenate(B))
        n, e1, e2 = wall_basis(axis)
        origin = origin + 0.5 * (ca[0] + cb[0]) * n
        axis = _unit(n - 0.5 * ((ca[1] + cb[1]) * e1 + (ca[2] + cb[2]) * e2))
    A, B = select_faces(frames, origin, axis, wall.half_mm)
    r = read_faces(A, B, boot=boot)
    tm["fit"] = time.perf_counter() - t2
    at_most = math.ceil((r["mm"] + max(2.0 * r["pm_mm"], 0.05)) * 100.0 - 1e-9) / 100.0
    if at_most <= 0.0:
        raise WallRefused("The wall here is thinner than the camera frames can tell apart.",
                          "Measure it with calipers, or lay the part flat and scan it from above.")
    words = wall_words(at_most, r["frames"], wall.mesh_mm)
    return WallReading(at_most_mm=at_most, reading_mm=r["mm"], pm_mm=r["pm_mm"], mesh_mm=wall.mesh_mm,
                       frames=r["frames"], points=r["points"], tilt_deg=r["tilt_deg"],
                       seconds=time.perf_counter() - t0, words=words, timings_s=tm)
