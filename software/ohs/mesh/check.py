"""Which parts of a mesh need another pass: the areas whose confidence misses a tolerance.

:func:`weak_areas` takes the mesh, each vertex's sigma (:mod:`ohs.mesh.confidence`, mm) and the
faces hole filling made up, and answers "this share of the surface is good to the tolerance, these
areas are not", largest first. It is pure numpy / scipy, a few seconds on millions of triangles.

* A face **fails** when it is *filled* (kind ``not_measured``: the surface was made up, nothing
  measured it), when one of its vertices has no usable sigma (NaN, zero or negative, or
  :data:`~ohs.mesh.confidence.SIGMA_NONE_MM` and above: also ``not_measured``), or when ``k`` times
  the mean sigma of its three vertices exceeds the tolerance (``uncertain``: with ``k = 2`` the
  scan's own 2-sigma band there is wider than the part may be off). The sigma is statistical: a
  calibration or scale error is not in it.
* Failing faces that share an **edge** form an area (two patches touching at one vertex stay two).
  An area is ``not_measured`` when most of its surface is, else ``uncertain``.
* Areas under a minimum size are left out of the list (single noisy faces would bury the real
  ones) but still count in the shares.

**Why an area is weak** (FEATURE_PLAN N1). Every listed area has a ``normal`` (the mean of its
vertices' normals: the side it was seen from) and a ``reason``, the first of these that holds for it,
with ``reasons`` all that do and ``evidence`` the numbers behind them:

* ``not_seen``: nothing measured it (kind ``not_measured``: a hole filled in, or no sigma), or most
  of it lies next to the mesh's open border, where the scan saw no more surface (a shadow, or where
  the scan ended: within twice the length over which the model's border term falls off, 3 mm at 1
  mm voxels; the border of a hole of up to :data:`PINHOLE_MM` round, times the voxel size, does not
  count: a gap between the dots, not a shadow). ``evidence["outward"]`` is then the way out of the mesh past that border, when its
  border agrees on one (:data:`OUTWARD_MIN`); :func:`ohs.guide.views.suggest_view` leans towards it.
* ``steep``: the frames that saw it all saw it at a steep angle: even the squarest look met the
  surface more than :data:`STEEP_DEG` off its normal. Needs the scan's camera path (:class:`ScanViews`).
* ``far``: even the nearest look that saw it was farther than :data:`FAR_RATIO` times the working
  distance (camera depth). Needs the camera path.
* ``few_looks``: it had fewer than :data:`FEW_LOOKS_SHARE` of the independent looks that the
  surface that passes had (the median of each). Needs each vertex's looks (the PLY ``looks``; a
  mesh of Open3D's model on the CPU path has none).
* ``edge``: the mesh's own curvature term of the sigma (:func:`ohs.track.recon_tsdf.mesh_sigma_terms`)
  alone is above the tolerance: an edge, a tight curve, or a surface the mesh shows as uneven. It
  comes after the reasons a second pass can act on, so an area seen steeply *and* on a curve says
  "steep" and gets a pose; where it is the only reason, another pass does not lower that term and no
  pose is the answer (at 0.1 mm on a 1 mm-voxel mesh that is most of a part: VALIDATION.md). Needs
  the voxel size the mesh was made at.
* ``unknown``: none of these holds, or the data that would tell are not there (``unknown_because``
  says which). Never a guess.

A rule holds for an area when it holds for at least :data:`REASON_MAJORITY` of its vertices (at most
:data:`REASON_SAMPLES` of them, evenly spread in the order of their coordinates (:func:`even_sample`), for
the camera path's rules: the answer does not depend on how the mesh numbers its vertices or faces). What a frame "saw": the
vertex lies in its image and its depth range, faces it, and the ray from the vertex to the camera
hits nothing on the mesh on its way (a shadow ray, started :data:`RAY_OFFSET_MM` off the surface).
The camera path's rules look at :data:`MAX_VIEW_FRAMES` frames at most, evenly spread over the scan
(a scan at 30 frames a second sees a surface from nearly the same place in neighbouring frames), so
the time does not grow with the length of the scan; the result's ``reasons`` says how many were looked
at. A vertex whose count of looks is 0 or missing (hole filling made it: :func:`ohs.mesh.carry_looks`)
has no looks to count: it is left out of the looks of its area and of the surface that passes.

**One mesh, many checks.** :class:`CheckMesh` holds what every check of one mesh shares (its normals,
the ray-casting scene, its open border, the curvature term): pass it as ``context`` and each check
of that mesh (another tolerance, the poses of :mod:`ohs.guide.views`) does only its own part.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from ohs.mesh.confidence import SIGMA_NONE_MM
from ohs.mesh.topology import as_mesh, edge_table, face_areas, rows, sorted_unique, vertex_normals

__all__ = ["weak_areas", "MIN_AREA_FLOOR_MM2", "MIN_AREA_FACES", "ScanViews", "working_distance", "view_statistics",
           "REASONS", "STEEP_SIGMA_FACTOR", "STEEP_COS", "STEEP_DEG", "FAR_SIGMA_FACTOR", "FAR_RATIO", "FEW_LOOKS_SHARE",
           "REASON_MAJORITY", "REASON_SAMPLES", "RAY_OFFSET_MM", "steep_cos", "MAX_VIEW_FRAMES", "spread_views",
           "CheckMesh", "add_reasons"]

#: the smallest area worth listing, whatever the mesh (mm^2): a 2 x 2 mm spot
MIN_AREA_FLOOR_MM2 = 4.0
#: ... and never fewer than this many median faces, so a coarse mesh does not list single faces
MIN_AREA_FACES = 10

#: the reasons an area can have, in the order they are tried (module docstring)
REASONS = ("not_seen", "steep", "far", "few_looks", "edge", "unknown")
#: "steep": a look is steep where the matcher's sigma is this many times what it is square on (the
#: incidence factor of :class:`ohs.vision.depth.SigmaModel`, calibrated against true depth)
STEEP_SIGMA_FACTOR = 2.0
#: "far": a look is from too far where its depth noise is this many times that at the working
#: distance; stereo depth noise grows with the square of the depth (z^2 sigma_d / (f B))
FAR_SIGMA_FACTOR = 2.0
#: ... so farther than this times the working distance: sqrt(2), 354 mm at the 250 mm the lasers cross
#: at, the far end of the 200-350 mm the README tells the user to hold the scanner at
FAR_RATIO = math.sqrt(FAR_SIGMA_FACTOR)
#: "few looks": under this share of the looks of the surface that passes; sigma's noise part goes
#: with 1 / sqrt(looks), so half the looks make it sqrt(2) times as large
FEW_LOOKS_SHARE = 0.5
#: a rule holds for an area when it holds for at least this share of its vertices (a majority)
REASON_MAJORITY = 0.5
#: vertices of an area looked at for the camera path's rules (evenly by index; enough for a share to 5 %)
REASON_SAMPLES = 400
#: an area's open border counts as one way out (``outward``) when its border's directions out of the
#: mesh agree this much on average (the length of their mean; 0 when they cancel all round)
OUTWARD_MIN = 0.3
#: open border shorter than this (mm, at 1 mm voxels; :func:`pinhole_mm`) is a small hole, not unseen
#: surface: the largest hole the app's "sheet metal" recipe fills (``ohs/server/processing.py``)
PINHOLE_MM = 15.0
#: shadow rays start this far (mm) off the surface along its normal so as not to hit their own
#: triangle (Open3D's ray cast is float32: about 1e-4 mm at 1 m). Something closer to the point than
#: this is not seen as in the way; a sheet of any thickness farther off is (tests/test_guide_views.py)
RAY_OFFSET_MM = 0.05
#: the camera path's rules look at this many frames at most, evenly spread (:func:`spread_views`): 4
#: seconds of a scan at 30 frames a second, or one frame in 25 of a 2-minute scan
MAX_VIEW_FRAMES = 120


def steep_cos(model: Any = None, factor: float = STEEP_SIGMA_FACTOR) -> float:
    """The cosine of the incidence angle at which ``model``'s incidence factor ``1 + inc_amp
    exp(-(cos / inc_cos)^2)`` reaches ``factor`` (default: the GPU matcher's
    :data:`ohs.vision.depth.SIGMA_MODELS` and :data:`STEEP_SIGMA_FACTOR`)."""
    if model is None:
        from ohs.vision.depth import SIGMA_MODELS  # noqa: PLC0415

        model = SIGMA_MODELS["gpu"]
    return float(model.inc_cos * math.sqrt(math.log(model.inc_amp / (float(factor) - 1.0))))


#: the cosine and angle beyond which a look is steep: 0.506, 59.6 degrees (the matcher's sigma doubled)
STEEP_COS = steep_cos()
STEEP_DEG = math.degrees(math.acos(STEEP_COS))


def working_distance(calib: Any, near_mm: float, far_mm: float) -> tuple[float, str]:
    """The scanner's working distance (mm, camera depth) from its calibration: where its line
    lasers' sheets cross the cameras' centre line (``x = baseline / 2, y = 0`` in frame C), the
    median of the crossings inside ``[near_mm, far_mm]``; without such a crossing, the middle of
    the range. ``(mm, how it was found)``."""
    near, far = float(near_mm), float(far_mm)
    zs = []
    lasers = getattr(calib, "lasers", None) or {}
    base = float(calib.stereo.baseline) if getattr(calib, "stereo", None) is not None else 0.0
    for pl in lasers.values():
        n, d = np.asarray(pl.normal, dtype=np.float64), float(pl.d)
        if abs(n[2]) < 1e-9:
            continue
        z = -(d + n[0] * 0.5 * base) / n[2]
        if near <= z <= far:
            zs.append(z)
    if zs:
        return float(np.median(zs)), f"where the calibrated laser sheets cross the centre line ({len(zs)} of {len(lasers)})"
    return 0.5 * (near + far), "the middle of the depth range (no calibrated laser sheet crosses the centre line in it)"


@dataclass
class ScanViews:
    """Where a scan's frames looked from, in the frame of its mesh.

    ``T_wc`` (n,4,4): camera C (the rectified left camera) of every frame that has a pose; ``K``
    and ``image_size``: the rectified camera (any resolution; only the field of view matters);
    ``near_mm`` / ``far_mm``: the depth range the scan measured (the matcher's,
    ``DepthParams.min_depth_mm`` / ``max_depth_mm``); ``working_mm``: the working distance
    (:func:`working_distance`); ``baseline_mm``: camera C to the right camera along its x axis.
    ``up``: the mean "up" of the scanner over the scan (image up, ``-y`` of the cameras), for a
    suggested pose's roll. A merged scan has several: pass a list."""

    T_wc: np.ndarray
    K: np.ndarray
    image_size: tuple[int, int]
    near_mm: float = 150.0
    far_mm: float = 450.0
    working_mm: float = 250.0
    baseline_mm: float = 60.0
    working_source: str = "given"
    up: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 1.0]))

    def __post_init__(self) -> None:
        self.T_wc = np.asarray(self.T_wc, dtype=np.float64).reshape(-1, 4, 4)
        self.K = np.asarray(self.K, dtype=np.float64).reshape(3, 3)
        self.image_size = (int(self.image_size[0]), int(self.image_size[1]))
        if not 0 < self.near_mm < self.far_mm:
            raise ValueError(f"the depth range must be 0 < near < far, got {self.near_mm}, {self.far_mm}")
        if len(self.T_wc):
            u = -self.T_wc[:, :3, 1].mean(axis=0)
            if np.linalg.norm(u) > 1e-6:
                self.up = u / np.linalg.norm(u)

    @classmethod
    def from_calibration(cls, T_wc: np.ndarray | Sequence, calib: Any, depth_range: tuple[float, float] = (150.0, 450.0),
                         T: np.ndarray | None = None) -> ScanViews:
        """From the frames' poses (``T_wc`` (n,4,4), or trajectory rows ``(index, t, ok, T_wc)``
        as :func:`ohs.track.posegraph.read_trajectory` gives them, or that file's path) and the
        calibration the scan was processed with (a :class:`~ohs.types.ScannerCalibration`).
        ``T``: the scan's world in the mesh's (a merge), identity by default."""
        from ohs.track.posegraph import read_trajectory  # noqa: PLC0415

        if isinstance(T_wc, (str, bytes)) or hasattr(T_wc, "__fspath__"):
            T_wc = read_trajectory(T_wc)
        if (not isinstance(T_wc, np.ndarray) and len(T_wc) and isinstance(T_wc[0], (tuple, list))
                and len(T_wc[0]) == 4 and np.ndim(T_wc[0][0]) == 0):  # trajectory rows
            T_wc = [np.asarray(r[3], dtype=np.float64).reshape(4, 4) for r in T_wc if r[2]]
        P = np.asarray(T_wc, dtype=np.float64).reshape(-1, 4, 4)
        P = P[np.all(np.isfinite(P.reshape(len(P), -1)), axis=1)]
        if T is not None:
            P = np.asarray(T, dtype=np.float64).reshape(4, 4)[None] @ P
        near, far = float(depth_range[0]), float(depth_range[1])
        wd, src = working_distance(calib, near, far)
        return cls(P, calib.stereo.K_rect, tuple(calib.stereo.image_size), near, far, wd, float(calib.stereo.baseline), src)


def _ray_scene(V: np.ndarray, F: np.ndarray) -> Any:
    import open3d as o3d  # noqa: PLC0415

    rs = o3d.t.geometry.RaycastingScene()
    rs.add_triangles(o3d.core.Tensor(np.ascontiguousarray(V, dtype=np.float32)),
                     o3d.core.Tensor(np.ascontiguousarray(F, dtype=np.uint32)))
    return rs


def _unblocked(scene: Any, P: np.ndarray, Nrm: np.ndarray, C: np.ndarray) -> np.ndarray:
    """True where the shadow ray from ``P`` (started :data:`RAY_OFFSET_MM` along ``Nrm``) to ``C`` (one
    point (3,) or one per point) hits nothing of the mesh before it."""
    import open3d as o3d  # noqa: PLC0415

    if len(P) == 0:
        return np.zeros(0, dtype=bool)
    O = P + RAY_OFFSET_MM * Nrm
    D = np.broadcast_to(C, P.shape) - O
    dist = np.linalg.norm(D, axis=1)
    D = D / np.maximum(dist, 1e-12)[:, None]
    rays = np.concatenate([O, D], axis=1).astype(np.float32)
    t = scene.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy()
    return ~(t < dist)  # inf (no hit) or a hit beyond the camera


def spread_views(views: ScanViews | Sequence[ScanViews], cap: int | None = MAX_VIEW_FRAMES
                 ) -> tuple[list[ScanViews], int, int]:
    """At most ``cap`` of the frames of ``views`` (one :class:`ScanViews` or a list, a merge), evenly
    spread: each scan keeps its share of them (at least one), picked evenly along its path, first and
    last included. Each keeps its own ``up`` (the mean over all of its frames). ``(views, frames before,
    frames kept)``; ``cap`` None or at least the number of frames: all of them, the same objects."""
    vs = [views] if isinstance(views, ScanViews) else list(views)
    total = int(sum(len(v.T_wc) for v in vs))
    if cap is None or total <= int(cap):
        return vs, total, total
    cap = max(int(cap), len(vs))
    out = []
    for v in vs:
        n = len(v.T_wc)
        m = min(n, max(1, int(round(cap * n / total)))) if n else 0
        pick = np.unique(np.round(np.linspace(0, n - 1, m)).astype(np.int64)) if m else np.zeros(0, np.int64)
        s = ScanViews(v.T_wc[pick], v.K, v.image_size, v.near_mm, v.far_mm, v.working_mm, v.baseline_mm, v.working_source)
        s.up = np.array(v.up, dtype=np.float64)
        out.append(s)
    return out, total, int(sum(len(v.T_wc) for v in out))


def content_order(P: np.ndarray) -> np.ndarray:
    """The order of the points ``P`` (n,3) by their coordinates (x, then y, then z): the same however the mesh
    numbers its vertices (the GPU path writes the same mesh in another vertex order from run to run)."""
    P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
    return np.lexsort((P[:, 2], P[:, 1], P[:, 0]))


def even_sample(idx: np.ndarray, P: np.ndarray, n: int) -> np.ndarray:
    """Positions into ``idx`` (points ``P``, one row each) of at most ``n`` of them, evenly spread in
    :func:`content_order` (all of them, in that order, when there are no more than ``n``)."""
    o = content_order(P)
    if len(o) > n:
        o = o[np.linspace(0, len(o) - 1, n).astype(np.int64)]
    return o


def _unit_normals(normals: np.ndarray | None, V: np.ndarray, F: np.ndarray) -> np.ndarray:
    """``normals`` (N,3) at unit length (0 where they have none), or the mesh's own when not given or
    not one per vertex."""
    if normals is not None:
        N = np.asarray(normals, dtype=np.float64).reshape(-1, 3)
        if len(N) == len(V):
            ln = np.linalg.norm(N, axis=1, keepdims=True)
            return np.where(ln > 1e-12, N / np.maximum(ln, 1e-12), 0.0)
    return vertex_normals(V, F)


class CheckMesh:
    """What every check of one mesh shares (module docstring), each part made on first use and kept:
    the unit vertex normals ``N`` (``normals`` as given, else the mesh's own), the ray-casting scene,
    the open border (per smallest hole length kept) and the curvature term (per voxel size). Safe to
    share between threads. The mesh must not change while it is in use."""

    def __init__(self, V: np.ndarray, F: np.ndarray, normals: np.ndarray | None = None) -> None:
        self.V, self.F = as_mesh(V, F)
        self.N = _unit_normals(normals, self.V, self.F)
        self._meta = threading.Lock()
        self._locks: dict[Any, threading.Lock] = {}
        self._parts: dict[Any, Any] = {}

    def fits(self, V: np.ndarray, F: np.ndarray) -> bool:
        """Whether ``(V, F)`` is this mesh (by its sizes: the caller keeps one per mesh version)."""
        return len(V) == len(self.V) and len(F) == len(self.F)

    def _part(self, key: Any, make: Any) -> Any:
        """The part ``key``, made once (one lock per part: different parts are made at the same time)."""
        with self._meta:
            if key in self._parts:
                return self._parts[key]
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            with self._meta:
                if key in self._parts:
                    return self._parts[key]
            got = make()
            with self._meta:
                self._parts[key] = got
            return got

    def scene(self) -> Any:
        """The ray-casting scene, its search tree built (Open3D builds it on the first cast)."""
        def make() -> Any:
            import open3d as o3d  # noqa: PLC0415

            s = _ray_scene(self.V, self.F)
            s.cast_rays(o3d.core.Tensor(np.array([[0.0, 0.0, 0.0, 0.0, 0.0, 1.0]], dtype=np.float32)))
            return s

        return self._part("scene", make)

    def border(self, min_loop_mm: float) -> tuple[np.ndarray, np.ndarray, Any]:
        """The open border's vertices, their way out of the mesh (:func:`_open_border`) and a KD-tree of
        them (None without a border)."""
        def make() -> tuple[np.ndarray, np.ndarray, Any]:
            bv, bout = _open_border(self.V, self.F, float(min_loop_mm))
            tree = None
            if len(bv):
                from scipy.spatial import cKDTree  # noqa: PLC0415

                tree = cKDTree(rows(self.V, bv))
            return bv, bout, tree

        return self._part(("border", round(float(min_loop_mm), 9)), make)

    def curvature(self, voxel_mm: float) -> np.ndarray:
        """Each vertex's curvature term of the sigma (mm, :func:`ohs.track.recon_tsdf.mesh_sigma_terms`)."""
        def make() -> np.ndarray:
            from ohs.track.recon_tsdf import mesh_sigma_terms  # noqa: PLC0415

            return np.sqrt(mesh_sigma_terms(self.V, self.F, self.N, voxel_mm=float(voxel_mm), parts=True)[0])

        return self._part(("curvature", float(voxel_mm)), make)

    def prepare(self, voxel_mm: float | None = None, scene: bool = True) -> None:
        """Make the parts a check of this mesh needs at once, side by side in threads (the scene's
        search tree, the open border, the curvature term at ``voxel_mm``); errors are raised here."""
        jobs = [lambda: self.border(pinhole_mm(voxel_mm))]
        if scene:
            jobs.append(self.scene)
        if voxel_mm is not None:
            jobs.append(lambda: self.curvature(voxel_mm))
        errors: list[BaseException] = []

        def run(fn: Any) -> None:
            try:
                fn()
            except BaseException as e:  # noqa: BLE001 - raised again below
                errors.append(e)

        threads = [threading.Thread(target=run, args=(fn,), daemon=True) for fn in jobs[1:]]
        for t in threads:
            t.start()
        run(jobs[0])
        for t in threads:
            t.join()
        if errors:
            raise errors[0]


def view_statistics(P: np.ndarray, Nrm: np.ndarray, views: ScanViews | Sequence[ScanViews], scene: Any,
                    chunk_rays: int = 1 << 21) -> dict[str, np.ndarray]:
    """For points ``P`` (n,3) with unit normals ``Nrm`` on the mesh of ``scene`` (Open3D ray
    casting scene): over the frames that saw each (module docstring), ``frames`` (count), ``best_cos``
    (the squarest look: largest cosine between the normal and the way to the camera; -1 when none) and
    ``near_mm`` (the nearest camera depth; inf when none)."""
    P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
    Nrm = np.asarray(Nrm, dtype=np.float64).reshape(-1, 3)
    n = len(P)
    frames = np.zeros(n, dtype=np.int64)
    best = np.full(n, -1.0)
    near = np.full(n, np.inf)
    vs = [views] if isinstance(views, ScanViews) else list(views)
    for v in vs:
        w, h = v.image_size
        K = v.K
        pend: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = []
        size = 0

        def flush() -> None:
            nonlocal pend, size
            if not pend:
                return
            j = np.concatenate([p[0] for p in pend])
            C = np.concatenate([p[1] for p in pend])
            cs = np.concatenate([p[2] for p in pend])
            z = np.concatenate([p[3] for p in pend])
            ok = _unblocked(scene, P[j], Nrm[j], C)
            j, cs, z = j[ok], cs[ok], z[ok]
            np.add.at(frames, j, 1)
            np.maximum.at(best, j, cs)
            np.minimum.at(near, j, z)
            pend, size = [], 0

        for T in v.T_wc:
            R, t = T[:3, :3], T[:3, 3]
            Pc = (P - t) @ R
            z = Pc[:, 2]
            with np.errstate(divide="ignore", invalid="ignore"):
                u = K[0, 0] * Pc[:, 0] / z + K[0, 1] * Pc[:, 1] / z + K[0, 2]
                vv = K[1, 1] * Pc[:, 1] / z + K[1, 2]
            ok = (z >= v.near_mm) & (z <= v.far_mm) & (u >= -0.5) & (u < w - 0.5) & (vv >= -0.5) & (vv < h - 0.5)
            ray = t[None] - P
            cs = np.einsum("ij,ij->i", Nrm, ray) / np.maximum(np.linalg.norm(ray, axis=1), 1e-12)
            ok &= cs > 0
            j = np.flatnonzero(ok)
            if len(j):
                pend.append((j, np.broadcast_to(t, (len(j), 3)), cs[j], z[j]))
                size += len(j)
                if size >= chunk_rays:
                    flush()
        flush()
    return {"frames": frames, "best_cos": best, "near_mm": near}


def _result(has_sigma: bool, tolerance_mm: float, k: float, min_area: float, total: float, ok, unc, nm,
            n_areas: int, areas: list, reasons: dict | None = None) -> dict:
    return {"has_sigma": has_sigma, "tolerance_mm": float(tolerance_mm), "k": float(k),
            "min_area_mm2": float(min_area), "area_mm2": float(total), "share_ok": ok, "share_uncertain": unc,
            "share_not_measured": nm, "n_areas": int(n_areas), "areas": areas, "reasons": reasons}


def _face_groups(F_fail: np.ndarray, n_vertices: int) -> tuple[int, np.ndarray]:
    """Connected groups of the faces ``F_fail`` over shared edges: ``(count, label (m,))``."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    m = len(F_fail)
    et = edge_table(F_fail, n_vertices)
    pos = np.arange(len(et.he_order))
    group = et.he_edge[et.he_order]
    later = pos != et.edge_start[group]  # every half-edge of an edge but its first one
    first_face = et.he_order[et.edge_start[group[later]]] // 3
    other_face = et.he_order[later] // 3
    A = coo_matrix((np.ones(len(first_face), dtype=np.int8), (first_face, other_face)), shape=(m, m)).tocsr()
    return connected_components(A, directed=False)


def _face_states(F: np.ndarray, S: np.ndarray | None, fl: np.ndarray, tolerance_mm: float,
                 k: float) -> tuple[np.ndarray | None, np.ndarray, np.ndarray]:
    """``(measured per vertex (None without sigma), not measured per face, uncertain per face)``."""
    if S is None:
        return None, fl, np.zeros(len(F), dtype=bool)
    measured_v = np.isfinite(S) & (S > 0) & (S < SIGMA_NONE_MM)
    ok_v = [np.take(measured_v, F[:, j]) for j in range(3)]
    not_measured = fl | ~(ok_v[0] & ok_v[1] & ok_v[2])
    mean = (np.take(S, F[:, 0]) + np.take(S, F[:, 1]) + np.take(S, F[:, 2])) / 3.0
    return measured_v, not_measured, ~not_measured & (k * mean > tolerance_mm)


def add_reasons(V: np.ndarray, F: np.ndarray, sigma: np.ndarray | None, filled: np.ndarray | None, result: dict, *,
                normals: np.ndarray | None = None, looks: np.ndarray | None = None,
                views: ScanViews | Sequence[ScanViews] | None = None, voxel_mm: float | None = None,
                context: CheckMesh | None = None, max_frames: int | None = MAX_VIEW_FRAMES) -> dict:
    """Why each area of ``result`` is weak: ``result`` is :func:`weak_areas` of this mesh, sigma and
    filled flags with ``why=False`` (its areas' ``faces`` as the int arrays it gave); the answer is a
    new dict, what ``weak_areas(..., why=True)`` gives with the same arguments, without finding the
    areas again (the server's Check answers first without the reasons, then with them)."""
    V, F = as_mesh(V, F)
    S = None if sigma is None else np.asarray(sigma, dtype=np.float64).reshape(-1)
    if S is not None and len(S) != len(V):
        raise ValueError(f"{len(S)} sigma values for {len(V)} vertices")
    if looks is not None and np.asarray(looks).size != len(V):
        raise ValueError(f"{np.asarray(looks).size} looks for {len(V)} vertices")
    if context is not None and not context.fits(V, F):
        raise ValueError(f"the context is of another mesh ({len(context.V)} vertices, {len(context.F)} faces; "
                         f"this one has {len(V)}, {len(F)})")
    fl = np.zeros(len(F), dtype=bool) if filled is None else np.asarray(filled, dtype=bool).reshape(-1)
    if len(fl) != len(F):
        raise ValueError(f"{len(fl)} filled flags for {len(F)} faces")
    lst = result["areas"]
    if not lst:
        return {**result, "reasons": _Reasons.empty(looks, views, voxel_mm, max_frames).summary()}
    vlist = []
    for a in lst:
        faces = np.asarray(a["faces"], dtype=np.int64)
        if len(faces) and (faces.min() < 0 or faces.max() >= len(F)):
            raise ValueError("the answer's areas refer to faces this mesh does not have")
        vlist.append(sorted_unique(F[faces]))
    tol, k = float(result["tolerance_mm"]), float(result["k"])
    measured_v, not_measured, uncertain = _face_states(F, S, fl, tol, k)
    told = _Reasons(V, F, S, measured_v, not_measured | uncertain, face_areas(V, F), tol, k, normals, looks, views,
                    voxel_mm, context, max_frames)
    stats = told.view_stats(vlist)
    areas = [{**a, **told.area(a["kind"], verts, st)} for a, verts, st in zip(lst, vlist, stats)]
    return {**result, "areas": areas, "reasons": told.summary()}


def weak_areas(V: np.ndarray, F: np.ndarray, sigma: np.ndarray | None, filled: np.ndarray | None = None,
               tolerance_mm: float = 0.1, k: float = 2.0, min_area_mm2: float | None = None,
               max_areas: int = 50, *, normals: np.ndarray | None = None, looks: np.ndarray | None = None,
               views: ScanViews | Sequence[ScanViews] | None = None, voxel_mm: float | None = None,
               why: bool = True, context: CheckMesh | None = None, max_frames: int | None = MAX_VIEW_FRAMES) -> dict:
    """The areas of ``(V, F)`` that miss ``tolerance_mm`` (see the module docstring).

    Why each listed area is weak (module docstring, "Why an area is weak") comes from what is given:
    ``normals`` (N,3) the mesh's vertex normals (default: from the faces), ``looks`` (N,) each
    vertex's independent looks (the PLY ``looks``), ``views`` the scan's camera path
    (:class:`ScanViews`, or a list of them for a merge), ``voxel_mm`` the voxel size the mesh was
    made at (``report.json`` ``mesh.voxel_mm``). Each area then also has ``normal``, ``reason``,
    ``reasons``, ``reason_text``, ``evidence`` and ``unknown_because``; the result has ``reasons``
    (what was given and the limits used, with ``frames_total`` / ``frames_used`` / ``frames_cap``: the
    camera path's frames and how many of them were looked at, at most ``max_frames``). ``why=False``
    leaves all of that out (the result's ``reasons`` is None): the areas alone, in a third of the time
    on a million faces. ``context``: the :class:`CheckMesh` of this mesh, to share its parts with other
    checks (its normals are then the ones used).

    ``sigma`` is one value per vertex (mm); None for a mesh without confidence: then only the
    ``filled`` faces (one flag per face) fail and the result says ``has_sigma`` False.

    ``min_area_mm2`` defaults to the larger of :data:`MIN_AREA_FLOOR_MM2` (4 mm^2) and
    :data:`MIN_AREA_FACES` (10) times the mesh's median face area: a fixed size alone would list
    single faces of a coarse mesh, a face count alone would hide real spots on a fine one.

    Returns a dict: ``has_sigma``, ``tolerance_mm``, ``k``, ``min_area_mm2`` (as used), ``area_mm2``
    (whole mesh), ``share_ok`` / ``share_uncertain`` / ``share_not_measured`` (of the surface area;
    the first two are None without sigma, and all three None for an empty mesh), ``n_areas`` (all
    above the minimum) and ``areas``: at most ``max_areas``, largest first, each with ``kind``,
    ``area_mm2``, ``share`` (of the mesh), ``center`` (area-weighted), ``extent_mm`` (largest side
    of its bounding box), ``radius_mm`` (farthest vertex from the centre), ``n_faces``,
    ``sigma_median_mm`` / ``sigma_worst_mm`` (over its measured vertices, None when there are
    none) and ``faces`` (sorted int64 indices into ``F``).
    """
    V, F = as_mesh(V, F)
    nf = len(F)
    if not np.isfinite(tolerance_mm) or tolerance_mm <= 0:
        raise ValueError(f"the tolerance must be above 0 mm, got {tolerance_mm}")
    if not np.isfinite(k) or k <= 0:
        raise ValueError(f"k must be above 0, got {k}")
    if min_area_mm2 is not None and (not np.isfinite(min_area_mm2) or min_area_mm2 < 0):
        raise ValueError(f"the minimum area must be 0 mm^2 or more, got {min_area_mm2}")
    if max_areas < 0:
        raise ValueError(f"max_areas must be 0 or more, got {max_areas}")
    S = None
    if sigma is not None:
        S = np.asarray(sigma, dtype=np.float64).reshape(-1)
        if len(S) != len(V):
            raise ValueError(f"{len(S)} sigma values for {len(V)} vertices")
    if looks is not None and np.asarray(looks).size != len(V):
        raise ValueError(f"{np.asarray(looks).size} looks for {len(V)} vertices")
    fl = np.zeros(nf, dtype=bool)
    if filled is not None:
        fl = np.asarray(filled, dtype=bool).reshape(-1)
        if len(fl) != nf:
            raise ValueError(f"{len(fl)} filled flags for {nf} faces")

    if context is not None and not context.fits(V, F):
        raise ValueError(f"the context is of another mesh ({len(context.V)} vertices, {len(context.F)} faces; "
                         f"this one has {len(V)}, {nf})")
    given = _Reasons.empty(looks, views, voxel_mm, max_frames).summary() if why else None
    area = face_areas(V, F)
    total = float(area.sum())
    if min_area_mm2 is None:
        med = float(np.median(area)) if nf else 0.0
        min_area_mm2 = max(MIN_AREA_FLOOR_MM2, MIN_AREA_FACES * med)
    if nf == 0 or total <= 0:
        return _result(S is not None, tolerance_mm, k, min_area_mm2, total, None, None, None, 0, [], given)

    # per face: not measured / uncertain
    measured_v, not_measured, uncertain = _face_states(F, S, fl, tolerance_mm, k)
    fail = not_measured | uncertain
    nm_share = float(area[not_measured].sum()) / total
    if S is None:
        unc_share, ok_share = None, None
    else:
        unc_share = float(area[uncertain].sum()) / total
        ok_share = max(0.0, 1.0 - nm_share - unc_share)
    if not fail.any():
        return _result(S is not None, tolerance_mm, k, min_area_mm2, total, ok_share, unc_share, nm_share, 0, [], given)

    # areas: failing faces joined over shared edges
    fidx = np.flatnonzero(fail)
    n_grp, label = _face_groups(F[fidx], len(V))
    fa = area[fidx]
    g_area = np.bincount(label, weights=fa, minlength=n_grp)
    g_nm = np.bincount(label, weights=np.where(not_measured[fidx], fa, 0.0), minlength=n_grp)
    listed = np.flatnonzero(g_area >= min_area_mm2)
    # largest first; areas of the same size (to 1e-6 mm^2) by where they lie, never by how the mesh numbers its
    # faces (the GPU path writes the same mesh in another order from run to run)
    if len(listed) > 1:
        Fl_ = F[fidx]
        cen = (rows(V, Fl_[:, 0]) + rows(V, Fl_[:, 1]) + rows(V, Fl_[:, 2])) * (fa / 3.0)[:, None]
        g_c = np.stack([np.bincount(label, weights=cen[:, j], minlength=n_grp) for j in range(3)], axis=1)
        g_c = g_c[listed] / np.maximum(g_area[listed], 1e-300)[:, None]
        listed = listed[np.lexsort((np.round(g_c[:, 2], 6), np.round(g_c[:, 1], 6), np.round(g_c[:, 0], 6),
                                    -np.round(g_area[listed], 6)))]
    n_areas = len(listed)
    listed = listed[:max_areas]
    order = np.argsort(label, kind="stable")  # faces grouped by area, each group in face order
    start = np.concatenate([[0], np.cumsum(np.bincount(label, minlength=n_grp))])

    told = (_Reasons(V, F, S, measured_v, fail, area, tolerance_mm, k, normals, looks, views, voxel_mm, context,
                     max_frames) if why and len(listed) else None)
    areas = []
    vlist = []
    for g in listed.tolist():
        faces = fidx[order[start[g]:start[g + 1]]]
        Fg = F[faces]
        ag = area[faces]
        cen_f = (rows(V, Fg[:, 0]) + rows(V, Fg[:, 1]) + rows(V, Fg[:, 2])) / 3.0
        center = (cen_f * ag[:, None]).sum(axis=0) / ag.sum()
        verts = sorted_unique(Fg)
        P = rows(V, verts)
        radius = float(np.linalg.norm(P - center, axis=1).max())
        extent = float((P.max(axis=0) - P.min(axis=0)).max())
        med = worst = None
        if S is not None:
            good = S[verts[measured_v[verts]]]
            if len(good):
                med, worst = float(np.median(good)), float(good.max())
        kind = "not_measured" if 2.0 * g_nm[g] > g_area[g] else "uncertain"
        areas.append({"kind": kind,
                      "area_mm2": float(g_area[g]), "share": float(g_area[g] / total),
                      "center": [float(x) for x in center], "extent_mm": extent, "radius_mm": radius,
                      "n_faces": int(len(faces)), "sigma_median_mm": med, "sigma_worst_mm": worst, "faces": faces})
        vlist.append(verts)
    if told is not None:  # the camera path's rules for all areas at once, then each area's reason
        stats = told.view_stats(vlist)
        for a, verts, st in zip(areas, vlist, stats):
            a.update(told.area(a["kind"], verts, st))
    return _result(S is not None, tolerance_mm, k, min_area_mm2, total, ok_share, unc_share, nm_share, n_areas, areas,
                   told.summary() if told is not None else given)


_TEXT = {
    "not_seen": "Not seen at all: nothing measured this surface (a hole or a shadow).",
    "beside_unseen": ("Next to surface that was not seen at all (a shadow, or where the scan ended): come round to "
                      "where it goes on."),
    "edge": ("On an edge, a tight curve or an uneven surface: the mesh's own uncertainty there is above the "
             "tolerance, and another pass alone does not lower it."),
    "steep": "Seen only at a steep angle: even the squarest look was {angle:.0f}° off the surface's normal.",
    "far": "Seen from too far: the nearest look was {dist:.0f} mm away (hold the scanner at about {work:.0f} mm).",
    "few_looks": ("Seen too few times: {looks:.0f} independent looks, where the surface that passes had {ref:.0f} "
                  "(go over it again, more slowly)."),
    "unknown": "No reason the data can tell.",
}


class _Reasons:
    """Why each area of one :func:`weak_areas` call is weak (module docstring); the per-mesh work
    (curvature term, looks of the surface that passes, the ray-casting scene, the open border) is done
    once, or taken from the :class:`CheckMesh` given."""

    def __init__(self, V, F, S, measured_v, fail_f, area_f, tol, k, normals, looks, views, voxel_mm,
                 context: CheckMesh | None = None, max_frames: int | None = MAX_VIEW_FRAMES) -> None:
        self.V, self.F, self.tol, self.k = V, F, float(tol), float(k)
        self.N = context.N if context is not None else _unit_normals(normals, V, F)
        self.L = None
        self.L_has = None
        if looks is not None:
            L = np.asarray(looks, dtype=np.float64).reshape(-1)
            if len(L) != len(V):
                raise ValueError(f"{len(L)} looks for {len(V)} vertices")
            self.L = L
            self.L_has = np.isfinite(L) & (L > 0)  # 0: no looks here (hole filling made the vertex)
        self._set_views(views, max_frames)
        self.voxel_mm = None if voxel_mm is None else float(voxel_mm)
        self.curv = None
        if self.voxel_mm is not None:
            if context is not None:
                self.curv = context.curvature(self.voxel_mm)
            else:
                from ohs.track.recon_tsdf import mesh_sigma_terms  # noqa: PLC0415

                self.curv = np.sqrt(mesh_sigma_terms(V, F, self.N, voxel_mm=self.voxel_mm, parts=True)[0])
        self.ref_looks = None
        if self.L is not None:
            ok_f = ~fail_f
            if ok_f.any():  # each vertex of the passing surface, weighted by a third of its faces' area
                wv = np.bincount(F[ok_f].ravel(), weights=np.repeat(area_f[ok_f] / 3.0, 3), minlength=len(V))
                wv[~self.L_has] = 0.0
                self.ref_looks = _weighted_median(self.L, wv) if wv.sum() > 0 else None
        # the open border (edges one face uses): where the scan saw no more surface
        self.border_near = 2.0 * _border_mm(self.voxel_mm)
        if context is not None:
            self.bv, self.bout, self.btree = context.border(pinhole_mm(self.voxel_mm))
        else:
            self.bv, self.bout = _open_border(V, F, pinhole_mm(self.voxel_mm))
            self.btree = None
            if len(self.bv):
                from scipy.spatial import cKDTree  # noqa: PLC0415

                self.btree = cKDTree(rows(V, self.bv))
        self.scene = None
        self.no_scene = None
        if self.views is not None:
            try:
                self.scene = context.scene() if context is not None else _ray_scene(V, F)
            except ImportError as e:  # pragma: no cover - open3d is a dependency
                self.no_scene = f"no ray casting ({e})"

    def _set_views(self, views, max_frames) -> None:
        self.views = _view_list(views)
        self.frames_total = self.frames_used = 0
        self.frames_cap = None if max_frames is None else int(max_frames)
        if self.views is not None:
            self.views, self.frames_total, self.frames_used = spread_views(self.views, max_frames)

    @classmethod
    def empty(cls, looks, views, voxel_mm, max_frames: int | None = MAX_VIEW_FRAMES) -> _Reasons:
        """What was given, for a result without listed areas."""
        r = cls.__new__(cls)
        r.L, r.voxel_mm, r.ref_looks, r.no_scene = looks, voxel_mm, None, None
        r._set_views(views, max_frames)
        return r

    def summary(self) -> dict:
        v = self.views
        return {"looks": self.L is not None, "views": v is not None and self.no_scene is None,
                "edges": self.voxel_mm is not None, "looks_passing": self.ref_looks,
                "steep_deg": STEEP_DEG, "far_mm": None if not v else FAR_RATIO * float(v[0].working_mm),
                "working_mm": None if not v else float(v[0].working_mm),
                "working_source": None if not v else v[0].working_source,
                "few_looks_share": FEW_LOOKS_SHARE, "majority": REASON_MAJORITY,
                "border_mm": _border_mm(self.voxel_mm), "frames_total": self.frames_total,
                "frames_used": self.frames_used, "frames_cap": self.frames_cap}

    def view_stats(self, vlist: list[np.ndarray]) -> list[dict[str, np.ndarray] | None]:
        """:func:`view_statistics` of each area's sampled vertices (at most :data:`REASON_SAMPLES`,
        evenly by index), all areas in one pass over the camera path; None per area without one."""
        if self.views is None or self.scene is None:
            return [None] * len(vlist)
        samples = []
        for verts in vlist:  # evenly in the order of where they lie, not of their numbers
            samples.append(verts[even_sample(verts, rows(self.V, verts), REASON_SAMPLES)] if len(verts) > REASON_SAMPLES
                           else verts)
        allv = np.concatenate(samples) if samples else np.zeros(0, np.int64)
        st = view_statistics(rows(self.V, allv), rows(self.N, allv), self.views, self.scene)
        cut = np.cumsum([0] + [len(s) for s in samples])
        return [{k: v[cut[i]:cut[i + 1]] for k, v in st.items()} for i in range(len(samples))]

    def area(self, kind: str, verts: np.ndarray, st: dict[str, np.ndarray] | None = None) -> dict:
        nsum = rows(self.N, verts).sum(axis=0)
        ln = float(np.linalg.norm(nsum))
        normal = nsum / ln if ln > 1e-9 else np.zeros(3)
        ev: dict[str, Any] = {"looks": None, "looks_passing": self.ref_looks, "view_angle_deg": None,
                              "distance_mm": None, "in_view_share": None, "edge_share": None, "border_share": 0.0,
                              "outward": None}
        unknown: list[str] = []
        holds: list[str] = []
        measured = kind != "not_measured"
        if not measured:
            holds.append("not_seen")
        if self.btree is not None:
            d, j = self.btree.query(rows(self.V, verts), distance_upper_bound=self.border_near)
            near = np.isfinite(d)
            ev["border_share"] = float(near.mean())
            if near.any():  # the way out of the mesh past its border, where the surface was not seen
                jn = sorted_unique(j[near])
                o = self.bout[jn].sum(axis=0)
                lo = float(np.linalg.norm(o)) / max(int(len(jn)), 1)
                if lo >= OUTWARD_MIN:
                    ev["outward"] = [float(x) for x in o / np.linalg.norm(o)]
            if measured and ev["border_share"] >= REASON_MAJORITY:
                holds.append("not_seen")
        on_edge = False  # judged here, listed last: after the reasons a second pass can act on
        if self.curv is not None:
            e = float(np.mean(self.k * self.curv[verts] > self.tol))
            ev["edge_share"] = e
            on_edge = measured and e >= REASON_MAJORITY
        else:
            unknown.append("edge: the voxel size the mesh was made at is not known")
        if self.views is None or self.scene is None:
            unknown.append("steep, far: " + (self.no_scene or "the scan's camera path is not given"))
        else:
            if st is None:
                st = self.view_stats([verts])[0]
            seen = st["frames"] > 0
            ev["in_view_share"] = float(seen.mean())
            if seen.mean() >= REASON_MAJORITY:
                cs = st["best_cos"][seen]
                ev["view_angle_deg"] = float(np.degrees(np.arccos(np.clip(np.median(cs), -1.0, 1.0))))
                ev["distance_mm"] = float(np.median(st["near_mm"][seen]))
                if measured:
                    if np.mean(cs < STEEP_COS) >= REASON_MAJORITY:
                        holds.append("steep")
                    if np.mean(st["near_mm"][seen] > FAR_RATIO * float(self.views[0].working_mm)) >= REASON_MAJORITY:
                        holds.append("far")
            elif measured:
                unknown.append(f"steep, far: the camera path shows only {100 * seen.mean():.0f} % of it in view")
        if self.L is None:
            unknown.append("few_looks: the mesh has no count of looks (made by Open3D's model, or an older file)")
        else:
            lv = self.L[verts][self.L_has[verts]]
            if not len(lv):
                unknown.append("few_looks: no point of it has a count of looks (hole filling made it)")
            else:
                ev["looks"] = float(np.median(lv))
                if self.ref_looks is None:
                    unknown.append("few_looks: no surface passes to compare with")
                elif measured and ev["looks"] < FEW_LOOKS_SHARE * self.ref_looks:
                    holds.append("few_looks")
        if on_edge:
            holds.append("edge")
        reason = holds[0] if holds else "unknown"
        work = float(self.views[0].working_mm) if self.views else 250.0
        key = "beside_unseen" if reason == "not_seen" and measured else reason
        text = _TEXT[key].format(angle=ev["view_angle_deg"] or 0.0, dist=ev["distance_mm"] or 0.0, work=work,
                                 looks=ev["looks"] or 0.0, ref=ev["looks_passing"] or 0.0)
        return {"normal": [float(x) for x in normal], "reason": reason, "reasons": holds, "reason_text": text,
                "evidence": ev, "unknown_because": unknown}


def pinhole_mm(voxel_mm: float | None) -> float:
    """The border of a hole this long or shorter is not "where the scan saw no more surface": the
    15 mm the app's sheet-metal recipe fills holes up to, times the bias scale of the voxel size (a
    hole of a few voxels between the dots is a gap in the depth, not a shadow)."""
    from ohs.track.recon_tsdf import bias_scale  # noqa: PLC0415

    return PINHOLE_MM * bias_scale(1.0 if voxel_mm is None else voxel_mm)


def _border_mm(voxel_mm: float | None) -> float:
    """The length over which the model's open-border term falls off (``BORDER_MM`` times the bias
    scale of the voxel size; 1 mm voxels when it is not known)."""
    from ohs.track.recon_tsdf import BORDER_MM, bias_scale  # noqa: PLC0415

    return BORDER_MM * bias_scale(1.0 if voxel_mm is None else voxel_mm)


def _open_border(V: np.ndarray, F: np.ndarray, min_loop_mm: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """The vertices on the mesh's open border (edges one face uses) and, for each, the unit
    direction out of the mesh there (in its faces' planes, away from the face across the edge).
    ``min_loop_mm``: pieces of border no longer than this (small holes) are left out."""
    n = len(V)
    if len(F) == 0:
        return np.zeros(0, dtype=np.int64), np.zeros((0, 3))
    a = F.ravel()
    b = F[:, [1, 2, 0]].ravel()
    c = F[:, [2, 0, 1]].ravel()
    key = np.minimum(a, b) * np.int64(n) + np.maximum(a, b)
    # edges one face uses: a key that differs from both its neighbours once sorted (a sort, not np.unique:
    # ohs.mesh.topology's module docstring)
    order = np.argsort(key, kind="stable")
    ks = key[order]
    new = np.empty(len(ks) + 1, dtype=bool)
    new[0] = new[-1] = True
    new[1:-1] = ks[1:] != ks[:-1]
    once = np.zeros(len(key), dtype=bool)
    once[order] = new[:-1] & new[1:]
    if not once.any():
        return np.zeros(0, dtype=np.int64), np.zeros((0, 3))
    ea, eb, ec = a[once], b[once], c[once]
    Va, Vb = rows(V, ea), rows(V, eb)
    e = Vb - Va
    length = np.linalg.norm(e, axis=1)
    if min_loop_mm > 0:  # leave out the small holes: pieces of border joined by their vertices, by length
        from scipy.sparse import coo_matrix  # noqa: PLC0415
        from scipy.sparse.csgraph import connected_components  # noqa: PLC0415

        A = coo_matrix((np.ones(len(ea), dtype=np.int8), (ea, eb)), shape=(n, n))
        _, lab = connected_components(A, directed=False)
        per = np.bincount(lab[ea], weights=length, minlength=int(lab.max()) + 1)
        keep = per[lab[ea]] > float(min_loop_mm)
        if not keep.any():
            return np.zeros(0, dtype=np.int64), np.zeros((0, 3))
        ea, eb, ec, Va, Vb, e, length = ea[keep], eb[keep], ec[keep], Va[keep], Vb[keep], e[keep], length[keep]
    e /= np.maximum(length, 1e-12)[:, None]
    o = 0.5 * (Va + Vb) - rows(V, ec)
    o -= np.einsum("ij,ij->i", o, e)[:, None] * e
    o /= np.maximum(np.linalg.norm(o, axis=1, keepdims=True), 1e-12)
    acc = np.zeros((n, 3))
    np.add.at(acc, ea, o)
    np.add.at(acc, eb, o)
    bv = sorted_unique(np.concatenate([ea, eb]))
    out = acc[bv]
    out /= np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-12)
    return bv, out


def _view_list(views) -> list[ScanViews] | None:
    if views is None:
        return None
    vs = [views] if isinstance(views, ScanViews) else list(views)
    return vs if any(len(v.T_wc) for v in vs) else None


def _weighted_median(x: np.ndarray, w: np.ndarray) -> float:
    o = np.argsort(x, kind="stable")
    c = np.cumsum(w[o])
    return float(x[o][np.searchsorted(c, 0.5 * c[-1])])
