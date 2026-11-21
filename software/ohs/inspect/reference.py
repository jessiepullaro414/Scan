"""A scan compared with a reference model of the part (FEATURE_PLAN I2, the engine).

The user has the part as it should be (an STL, OBJ or PLY from CAD, or a scan of a good part) and
a scan of the part as it is. The answer is "93 % within ± 0.5 mm", and where the rest is.

1. **Reading the reference** (:func:`read_reference`, :func:`reference_from_arrays`). Through
   :func:`ohs.track.export.load_mesh`; whatever cannot be compared is refused with a sentence: a
   missing file, another format (a STEP file must be exported as STL from CAD first), a file that
   cannot be read, a point cloud (no faces), points that are not numbers, faces without area, a
   model far too small (under :data:`SIZE_MIN_MM` across) or too large (over
   :data:`SIZE_MAX_MM`). Faces without area are dropped, with a note.

   **Units.** Mesh files carry no unit. Given the scan, the reference's size is compared with the
   size of the scan's part (the table set aside as :func:`ohs.align.pairwise.scan_cloud` does,
   each measured along its own principal axes): a ratio near one of :data:`UNITS` (inch, cm, m,
   µm) is reported as a :class:`UnitsFinding` with status ``"pending"``. Nothing is scaled
   silently: :meth:`ReferenceModel.apply_units` scales it, :meth:`ReferenceModel.keep_units` keeps
   it, and until one of them is called the alignments refuse. A scan may show only some of the part,
   so a factor is matched when the scan is 0.3 to 1.5 times the reference's size in that unit;
   where cm and inch both fit, the nearer is offered and the other named.

   **Winding.** A normal's sign says which side is outside, and the deviation's sign comes from it
   (+ = the scan lies outside the reference: material where there should be none). An STL from
   CAD is usually wound outward, but not always, so it is not assumed: faces joined by an edge are
   turned to agree with their neighbours (``mixed`` winding), and each closed piece is turned round
   when its volume comes out negative (``inside out``). An open piece (a surface model, a sheet)
   has no inside: its outside is taken, when it is compared, as the side the scan sees it from (the
   scan's normals point to where it was seen from). A piece that cannot be wound one way (a
   self-touching surface) is left as it is, with a note. ``ReferenceModel.orientation`` says what
   was done.

2. **Aligning the scan to the reference**; each gives a :class:`ReferenceAlignment` that says
   which way it was made (``method``):

   * ``"shape"`` (:func:`align_by_shape`): a best fit of the shapes by the alignment of two scans
     (:func:`ohs.align.pairwise.align_pair`: the scan's table set aside, first guesses from
     features, ICP, the checks), against points spread evenly over the reference's surface (its
     own support plane step turned off: a reference has no table). Its numbers (overlap, RMS, the
     agreeing guesses, the directions pinned, whether it is sure, the poses it had in mind) are
     passed on in ``pair``. The engine's own guards (a table only when it is one, stray bits
     dropped, the pose tried again turned round, "not sure" when the evidence is not there;
     MAINTENANCE_PLAN F11) do the rest, with two settings of the comparison's own
     (:data:`DEFAULT_PARAMS`): :data:`RANSAC_SEEDS` first guesses instead of 8, and
     :data:`REFERENCE_RIVAL` for the margin. Then a last least-squares step
     (:func:`_least_squares`), since the engine's robust weights keep an overall error at one end
     of the part.
   * ``"points"`` / ``"points+shape"`` (:func:`align_by_points`): three or more pairs of points
     picked on the scan and on the reference (:func:`ohs.align.pairwise.pose_from_point_pairs`),
     used as they are or refined on the shapes from there (with the least-squares step).
   * ``"datums"`` (:func:`align_by_datums`): the scan's part frame (:class:`ohs.measure.frame.
     PartFrame`, built from fitted features) put onto the reference's own axes; the reference is
     taken to be modelled in the part's axes (or a frame built on the reference itself is given).
     No best fit at all.
   * ``"given"`` (:func:`given_alignment`): a pose kept from before.

   A best fit hides what an alignment on datums shows: a part 0.5 mm too long reads a quarter of a
   millimetre at each end when fitted, and half a millimetre at the far end when held at the near
   one. So both are offered, and a best fit made with the datums at hand reports how far it lies
   from the datum alignment (``vs_datums``).

3. **The deviation** (:func:`deviation_from_reference`): each scan vertex's signed distance from
   the reference surface (:func:`ohs.mesh.offset.surface_offset`), in the reference's frame, with
   the scan's own sigma beside it. Filled faces (made up, not measured) and vertices without a
   usable sigma are left out. **Not on the reference** (the table the part stands on, a clamp,
   surface far off): a measured vertex is compared only when it lies within ``near_mm`` (3 mm) of
   the reference surface, inside the reference's box grown by ``near_mm``, and, unless it is
   within ``snap_mm`` (0.3 mm: at an edge the normals cannot agree and need not), its normal is
   within ``normal_deg`` (70°) of the reference's there (at a reference edge or corner, of any
   face there). A table touching the part's foot is within 3 mm of its side faces but faces up
   where they face sideways. In a concave corner the nearest reference face can be the wrong one
   (a boss 1 mm short: its top, near the rim, is nearer the boss's wall than its top): a vertex
   that faces away from its nearest face is measured instead along its own normal to reference
   surface facing its way within ``near_mm`` (``along_normal``), and is not on the reference if
   there is none. A face is compared when its three vertices are. At an edge the nearest-point
   distance reads less than a face's move (the rim of a boss 1 mm short lies on the reference's
   wall: 0 mm). **Not scanned:** points spread evenly over the reference count as
   covered when a compared scan vertex lies within ``near_mm``, at most ``cover_mm`` to the side
   (three median scan edges, at least 1 mm), facing the same way; the rest is the share of the
   reference's area the scan did not cover. (Faces buried inside the model, where a reference is
   made of overlapping solids, count there too.)

4. **The answer for a tolerance** (:func:`tolerance_summary`): by area, not by vertex count (a
   mesh is finer where it has detail): each compared face carries the mean of its three vertices'
   deviations, and the shares within ± the tolerance, above it (+) and below it (-) are shares of
   the compared area. Robust statistics (median, 1.4826 times the median absolute deviation, 5th
   and 95th percentiles) are area-weighted too. Faces outside the tolerance that share an edge form
   a region (above and below apart), as :func:`ohs.mesh.check.weak_areas` groups weak faces, each
   with its centre, size, mean and worst deviation. **The scan's own uncertainty:** a deviation
   smaller than about ``k`` (2) times the scan's sigma there is no evidence of a real difference;
   each region says whether its mean exceeds ``k`` times its median sigma, and what share of it
   exceeds ``k`` times the sigma face by face; ``share_uncertain`` is the compared area where ``k``
   sigma is wider than the tolerance itself.

5. **Records** with ``to_dict()``: :class:`ReferenceModel`, :class:`UnitsFinding`,
   :class:`ReferenceAlignment`, :class:`Deviation`, :class:`DeviationRegion`,
   :class:`ToleranceSummary`, :class:`Comparison` (:func:`compare_with_reference`: all three steps).

Every length is in mm; the deviation and the regions are in the reference's frame.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from ohs.align.pairwise import AlignParams, align_pair, pose_from_point_pairs, scan_cloud
from ohs.geometry import invert_se3, rotation_angle_deg
from ohs.mesh.check import MIN_AREA_FACES, MIN_AREA_FLOOR_MM2, _face_groups
from ohs.mesh.confidence import SIGMA_NONE_MM
from ohs.mesh.offset import surface_offset
from ohs.mesh.topology import as_mesh, edge_table, face_areas, face_normals, rows, sorted_unique, vertex_normals

__all__ = ["ReferenceModel", "UnitsFinding", "ScanSurface", "ReferenceAlignment", "Deviation", "DeviationRegion",
           "ToleranceSummary", "Comparison", "read_reference", "reference_from_arrays", "check_units", "align_by_shape",
           "align_by_points", "align_by_datums", "given_alignment", "deviation_from_reference", "tolerance_summary",
           "compare_with_reference", "SUFFIXES", "UNITS", "SIZE_MIN_MM", "SIZE_MAX_MM", "NEAR_MM", "SNAP_MM",
           "NORMAL_DEG", "FORMAT_VERSION"]

FORMAT_VERSION = 1  # of the records' to_dict()
SUFFIXES = (".stl", ".obj", ".ply")
#: units a reference may be in: (name, the words for it, mm per unit)
UNITS = (("inch", "inches", 25.4), ("cm", "centimetres", 10.0), ("m", "metres", 1000.0), ("µm", "micrometres", 0.001))
UNIT_MATCH = (0.3, 1.5)  # the scan's size over the reference's in a unit: a partial scan is smaller, a stray bit larger
UNIT_LIKELY = 0.85  # where in that window a scan of most of the part lies: picks between two units that fit
UNIT_FAR = 4.0  # sizes this many times apart, and no unit explains it: asked about
SIZE_MIN_MM = 1e-3  # a reference smaller than this across is refused (no unit makes a part of it)
SIZE_MAX_MM = 1e7  # ... and larger than 10 km
NEAR_MM = 3.0  # module docstring, 3.
SNAP_MM = 0.3
NORMAL_DEG = 70.0
COVER_EDGES = 3.0  # cover_mm: this many median scan edges, at least COVER_MIN_MM
COVER_MIN_MM = 1.0
SAMPLES_MAX = 2_000_000  # points spread over the reference, at most
VOTE_POINTS = 50_000  # scan vertices that decide an open reference piece's outside
#: first guesses from the shape: more than for two scans. A complete reference has more places for
#: a one-sided view to fit; on the simulated bracket (640 x 400) against its true shape 1 of 25
#: starts reached the true pose with 8 guesses ("not sure"), 3 of 33 with 24 (sure)
RANSAC_SEEDS = 24
#: another pose with this share of the best's score makes the comparison "not sure". Wider than
#: the engine's 0.9 for two scans: the reference is exact and whole, so a pose turned round that
#: misses only the small features still fits it well (the test block, told apart only by its hole
#: and boss, scored 0.89-0.92 turned against 1.0), while a ball or a plain plate scores 0.93-1.0
REFERENCE_RIVAL = 0.97
DEFAULT_PARAMS = AlignParams(ransac_seeds=RANSAC_SEEDS, rival_share=REFERENCE_RIVAL)


# --------------------------------------------------------------------------- small helpers
def _f(x) -> float | None:
    """A plain float, or None for None and NaN."""
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def _rigid(T, what: str = "the pose") -> np.ndarray:
    T = np.asarray(T, dtype=np.float64)
    if T.shape != (4, 4) or not np.all(np.isfinite(T)):
        raise ValueError(f"{what} must be a finite 4 x 4 matrix")
    R = T[:3, :3]
    if np.abs(R @ R.T - np.eye(3)).max() > 1e-6 or abs(np.linalg.det(R) - 1.0) > 1e-6 or np.any(np.abs(T[3] - [0, 0, 0, 1]) > 1e-9):
        raise ValueError(f"{what} must be a rotation and a translation")
    return T.copy()


def _moved(P: np.ndarray, T: np.ndarray) -> np.ndarray:
    return P @ T[:3, :3].T + T[:3, 3]


def _wquantile(x: np.ndarray, w: np.ndarray, q) -> np.ndarray:
    """Weighted quantiles ``q`` (0-1) of ``x``."""
    o = np.argsort(x, kind="stable")
    xs, ws = x[o], w[o]
    cw = np.cumsum(ws)
    return np.interp(np.asarray(q, dtype=np.float64) * cw[-1], cw - 0.5 * ws, xs)


def _extents(P: np.ndarray, w: np.ndarray | None = None, Q: np.ndarray | None = None, trim: float = 0.0) -> np.ndarray:
    """The sides (largest first) of the box around ``Q`` (default ``P``) along the principal axes of
    ``P`` (weights ``w``); ``trim`` percent left out at each end (stray bits of a scan)."""
    w = np.ones(len(P)) if w is None else np.asarray(w, dtype=np.float64)
    c = np.average(P, axis=0, weights=w)
    X = P - c
    _, U = np.linalg.eigh((X * w[:, None]).T @ X)
    Y = ((P if Q is None else Q) - c) @ U
    lo, hi = (np.percentile(Y, [trim, 100.0 - trim], axis=0) if trim > 0 else (Y.min(axis=0), Y.max(axis=0)))
    return np.sort(hi - lo)[::-1]


def _sample_surface(V: np.ndarray, F: np.ndarray, spacing: float, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Points spread evenly over ``(V, F)``, about one per ``spacing`` squared, at most
    :data:`SAMPLES_MAX`, and the face each lies on."""
    area = face_areas(V, F)
    total = float(area.sum())
    spacing = max(float(spacing), math.sqrt(total / SAMPLES_MAX))
    rng = np.random.default_rng(seed)
    want = area / spacing**2
    n = np.floor(want + rng.random(len(F))).astype(np.int64)
    face = np.repeat(np.arange(len(F)), n)
    u, v = rng.random(len(face)), rng.random(len(face))
    flip = u + v > 1.0
    u[flip], v[flip] = 1.0 - u[flip], 1.0 - v[flip]
    a, b, c = rows(V, F[face, 0]), rows(V, F[face, 1]), rows(V, F[face, 2])
    return a + u[:, None] * (b - a) + v[:, None] * (c - a), face


class _Closest:
    """Closest points on a triangle mesh (Open3D's ``RaycastingScene``), built once. The winding
    does not change them, so one serves a reference however its pieces are turned."""

    def __init__(self, V: np.ndarray, F: np.ndarray) -> None:
        import open3d as o3d  # noqa: PLC0415 (optional dependency)

        self._o3c = o3d.core
        self.scene = o3d.t.geometry.RaycastingScene()
        self.scene.add_triangles(o3d.core.Tensor(np.ascontiguousarray(V, dtype=np.float32)),
                                 o3d.core.Tensor(np.ascontiguousarray(F, dtype=np.uint32)))

    def query(self, P: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """``(closest points (n,3), face index (n,), barycentric (u, v) on it (n,2))``."""
        if len(P) == 0:
            return np.zeros((0, 3)), np.zeros(0, dtype=np.int64), np.zeros((0, 2))
        ans = self.scene.compute_closest_points(self._o3c.Tensor(np.ascontiguousarray(P, dtype=np.float32)))
        return (ans["points"].numpy().astype(np.float64), ans["primitive_ids"].numpy().astype(np.int64),
                ans["primitive_uvs"].numpy().astype(np.float64))

    def cast(self, O: np.ndarray, D: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Rays from ``O`` along the unit directions ``D``: ``(distance to the first hit, inf where
        none; face index, -1 where none)``."""
        if len(O) == 0:
            return np.zeros(0), np.zeros(0, dtype=np.int64)
        rays = np.ascontiguousarray(np.hstack([O, D]), dtype=np.float32)
        ans = self.scene.cast_rays(self._o3c.Tensor(rays))
        t = ans["t_hit"].numpy().astype(np.float64)
        hit = np.isfinite(t)
        return np.where(hit, t, np.inf), np.where(hit, ans["primitive_ids"].numpy().astype(np.int64), -1)


# --------------------------------------------------------------------------- winding
def _orient(V: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, dict, np.ndarray, np.ndarray]:
    """The faces wound consistently and, where a piece is closed, outward (module docstring, 1.):
    ``(F, what was done, piece of each face, closed per piece)``."""
    from scipy.sparse import coo_matrix  # noqa: PLC0415
    from scipy.sparse.csgraph import connected_components  # noqa: PLC0415

    m = len(F)
    et = edge_table(F, len(V))
    two = np.flatnonzero(et.counts == 2)
    h1 = et.he_order[et.edge_start[two]]
    h2 = et.he_order[et.edge_start[two] + 1]
    f1, f2 = h1 // 3, h2 // 3
    flat = F.reshape(-1)
    agree = flat[h1] != flat[h2]  # neighbours wound the same way run along their shared edge in opposite directions

    def graph(a, b, n):
        A = coo_matrix((np.ones(len(a), dtype=np.int8), (a, b)), shape=(n, n)).tocsr()
        return connected_components(A, directed=False)[1]

    piece = graph(f1, f2, m) if m else np.zeros(0, dtype=np.int64)
    n_piece = int(piece.max()) + 1 if m else 0
    # each face twice (as it is: f, turned: f + m); agreeing neighbours join as they are, others crosswise
    lab = graph(np.concatenate([f1, f1 + m]), np.concatenate([np.where(agree, f2, f2 + m), np.where(agree, f2 + m, f2)]), 2 * m)
    twisted = lab[:m] == lab[m:]  # a face that meets itself turned: the piece cannot be wound one way
    bad_piece = np.zeros(n_piece, dtype=bool)
    bad_piece[piece[twisted]] = True
    root = np.full(n_piece, m, dtype=np.int64)
    np.minimum.at(root, piece, np.arange(m))
    turn = (lab[:m] != lab[root[piece]]) & ~bad_piece[piece]
    # open pieces: a boundary edge (used by one face)
    one = np.flatnonzero(et.counts == 1)
    closed = np.ones(n_piece, dtype=bool)
    closed[piece[et.he_order[et.edge_start[one]] // 3]] = False
    closed &= ~bad_piece
    Ft = F.copy()
    Ft[turn] = Ft[turn][:, ::-1]
    a, b, c = rows(V, Ft[:, 0]), rows(V, Ft[:, 1]), rows(V, Ft[:, 2])
    vol = np.bincount(piece, weights=np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0, minlength=n_piece)
    inside_out = closed & (vol < 0)
    area = face_areas(V, F)
    turned_area = np.bincount(piece, weights=np.where(turn, area, 0.0), minlength=n_piece)
    piece_area = np.bincount(piece, weights=area, minlength=n_piece)
    keep_most = ~closed & ~bad_piece & (turned_area > 0.5 * piece_area)  # an open piece keeps most of its winding
    turn ^= (inside_out | keep_most)[piece]
    Fo = F.copy()
    Fo[turn] = Fo[turn][:, ::-1]
    n_turned = int(turn.sum())
    notes = []
    if n_turned == m and m and closed.all():
        notes.append(f"the CAD model was inside out (its faces wound inward): all {m} faces were turned round")
    elif n_turned:
        notes.append(f"mixed winding: {n_turned} of {m} faces were turned to agree with their neighbours")
        if inside_out.any():
            notes.append(f"{int(inside_out.sum())} closed piece(s) were inside out and turned round")
    n_open = int((~closed & ~bad_piece).sum())
    if n_open:
        notes.append(f"{n_open} open piece(s) (a surface with no inside): the outside is taken as the side the scan sees "
                     "them from")
    if bad_piece.any():
        notes.append(f"{int(bad_piece.sum())} piece(s) cannot be wound one way (a surface that touches itself turned): "
                     "left as they are, so the sign of the deviation on them may be wrong")
    info = {"turned": n_turned, "pieces": n_piece, "open_pieces": n_open, "not_orientable_pieces": int(bad_piece.sum()),
            "closed": bool(n_piece > 0 and closed.all()),
            "note": "; ".join(notes) if notes else "wound consistently" + (" and outward" if closed.all() else "")}
    return Fo, info, piece.astype(np.int64), closed


# --------------------------------------------------------------------------- units
@dataclass
class UnitsFinding:
    """What the sizes of the reference and of the scan say about the reference's unit (module
    docstring, 1.). ``status``: ``"same"`` (both in mm, nothing to confirm), ``"pending"`` (the
    caller must confirm: apply ``factor`` or keep it), ``"applied"`` or ``"kept"`` (confirmed)."""

    status: str
    ratio: float  # the scan's size over the reference's, as read
    factor: float | None  # mm per unit of the reference (multiply its numbers by this), when a unit fits
    unit: str | None
    candidates: list[dict]  # every unit that fits: {"unit", "factor"}
    reference_size_mm: float
    scan_size_mm: float
    message: str

    def to_dict(self) -> dict:
        return {"status": self.status, "ratio": _f(self.ratio), "factor": _f(self.factor), "unit": self.unit,
                "candidates": [dict(c) for c in self.candidates], "reference_size_mm": _f(self.reference_size_mm),
                "scan_size_mm": _f(self.scan_size_mm), "message": self.message}


def check_units(reference_size_mm: float, scan_size_mm: float) -> UnitsFinding:
    """The :class:`UnitsFinding` for a reference of ``reference_size_mm`` across (as read, in its
    own numbers) and a scan whose part is ``scan_size_mm`` across (module docstring, 1.)."""
    ref, scan = float(reference_size_mm), float(scan_size_mm)
    if not (ref > 0 and scan > 0 and math.isfinite(ref) and math.isfinite(scan)):
        raise ValueError("the sizes of the CAD model and of the scan must be positive numbers")
    r = scan / ref
    lo, hi = UNIT_MATCH

    def fits(f):
        return lo <= r / f <= hi

    if fits(1.0):
        msg = f"The CAD model and the scan's part are of a size ({ref:.3g} and {scan:.3g} mm across): both taken as mm."
        if r > 1.0 / UNIT_LIKELY:
            msg += (f" The scan is {r:.2g} times the CAD model's size: is there more than the part in it (a clamp, "
                    "a second part)?")
        return UnitsFinding("same", r, None, None, [], ref, scan, msg)
    cands = [{"unit": u, "factor": f} for u, _, f in UNITS if fits(f)]
    if cands:
        best = min(cands, key=lambda c: abs(math.log(r / c["factor"] / UNIT_LIKELY)))
        words = {u: w for u, w, _ in UNITS}
        f = best["factor"]
        if r >= 1.0:
            msg = f"The scan's part is {r:.3g} times the size of the CAD model: the CAD model looks to be in {words[best['unit']]}"
        else:
            msg = f"The CAD model is {1.0 / r:.3g} times the size of the scan's part: the CAD model looks to be in {words[best['unit']]}"
        others = [words[c["unit"]] for c in cands if c is not best]
        if others:
            msg += f" (or {', '.join(others)}, if the scan covers less of the part)"
        msg += (f" (1 {best['unit']} = {f:g} mm). Confirm to scale it by {f:g}, or keep it as it is"
                + (" if the scan covers only a small part of it" if r < 1.0 else "") + ".")
        return UnitsFinding("pending", r, f, best["unit"], cands, ref, scan, msg)
    if r > UNIT_FAR or r < 1.0 / UNIT_FAR:
        big, small = ("scan's part", "CAD model") if r > 1 else ("CAD model", "scan's part")
        msg = (f"The {big} is {max(r, 1.0 / r):.3g} times the size of the {small}, and no unit explains it: check that "
               "this is the right CAD model (or that the scan covers only a small part of it), then confirm to compare "
               "it as it is.")
        return UnitsFinding("pending", r, None, None, [], ref, scan, msg)
    msg = (f"The CAD model and the scan's part are {ref:.3g} and {scan:.3g} mm across: taken as mm (the scan covers part "
           "of the CAD model, or holds more than the part).")
    return UnitsFinding("same", r, None, None, [], ref, scan, msg)


# --------------------------------------------------------------------------- the scan
@dataclass
class ScanSurface:
    """The scan as it is compared: its mesh in its own world, each vertex's sigma (mm; the PLY
    ``quality``) and each face's *filled* flag when known, and its vertex normals (computed from the
    faces when not given)."""

    V: np.ndarray
    F: np.ndarray
    sigma: np.ndarray | None = None
    filled: np.ndarray | None = None
    N: np.ndarray | None = None
    name: str = "scan"
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.V, self.F = as_mesh(self.V, self.F)
        if len(self.F) == 0:
            raise ValueError("the scan has no faces: a mesh is needed")
        if self.sigma is not None:
            self.sigma = np.asarray(self.sigma, dtype=np.float64).reshape(-1)
            if len(self.sigma) != len(self.V):
                raise ValueError(f"{len(self.sigma)} sigma values for {len(self.V)} vertices")
        if self.filled is not None:
            self.filled = np.asarray(self.filled, dtype=bool).reshape(-1)
            if len(self.filled) != len(self.F):
                raise ValueError(f"{len(self.filled)} filled flags for {len(self.F)} faces")
        if self.N is not None:
            self.N = np.asarray(self.N, dtype=np.float64).reshape(-1, 3)
            if len(self.N) != len(self.V):
                self.N = None

    @classmethod
    def from_file(cls, path: str | Path) -> ScanSurface:
        """A processed scan's ``mesh.ply`` (with its sigma and filled flags), or any mesh file."""
        from ohs.track.export import load_mesh, load_ply  # noqa: PLC0415

        path = Path(path)
        if path.suffix.lower() == ".ply":
            d = load_ply(path)
            F = d["faces"] if d["faces"] is not None else np.zeros((0, 3), dtype=np.int64)
            return cls(d["vertices"], F, sigma=d["quality"], filled=d["filled"], N=d["normals"], name=path.name)
        V, F, N = load_mesh(path)
        return cls(V, F, N=N, name=path.name)

    def normals(self) -> np.ndarray:
        """Unit vertex normals (n,3), pointing to where the surface was seen from."""
        if "normals" not in self._cache:
            N = self.N
            if N is None:
                N = vertex_normals(self.V, self.F)
            else:
                ln = np.linalg.norm(N, axis=1, keepdims=True)
                N = np.divide(N, ln, out=np.zeros_like(N), where=ln > 0)
            self._cache["normals"] = N
        return self._cache["normals"]

    def cloud(self, params: AlignParams, seed: int = 0):
        """:func:`ohs.align.pairwise.scan_cloud` of the scan, made once per settings."""
        key = ("cloud", params, seed)
        if key not in self._cache:
            self._cache[key] = scan_cloud(self.V, self.normals(), params, seed)
        return self._cache[key]

    def part_cloud(self, params: AlignParams, seed: int = 0) -> tuple[Any, str | None]:
        """The scan's alignment cloud and, when there is something to say, a note: a large plane
        kept as a face of the part (not a table), stray bits left out. Both are the alignment
        engine's own guards (:func:`ohs.align.pairwise.scan_cloud`, MAINTENANCE_PLAN F11)."""
        c = self.cloud(params, seed)
        notes = [c.support_note] if c.support_note else []
        if c.stray:
            notes.append(f"{c.stray} stray point(s) of the scan away from the part (bits left on the table) were left out "
                         "of the alignment")
        return c, "; ".join(notes) or None

    def part_size_mm(self, params: AlignParams | None = None) -> float:
        """How large the scan's part is across: the diagonal of its box along its principal axes,
        its table (when it has one) set aside, half a percent left out at each end."""
        c, _ = self.part_cloud(params if params is not None else DEFAULT_PARAMS)
        P = c.part.fine.pts if c.part is not None and c.part.n >= 10 else c.full.fine.pts
        return float(np.linalg.norm(_extents(P, trim=0.5)))


# --------------------------------------------------------------------------- the reference
@dataclass
class ReferenceModel:
    """The reference mesh, read and checked (module docstring, 1.): ``V`` and ``F`` in mm (after
    :meth:`apply_units`) and wound as ``orientation`` says."""

    V: np.ndarray
    F: np.ndarray
    name: str
    path: str | None
    area_mm2: float
    extent_mm: list[float]  # the sides of its box along its principal axes, largest first
    bounds: tuple[list[float], list[float]]  # its box in its own frame (the "near the reference" test)
    orientation: dict
    face_piece: np.ndarray  # (m,) the connected piece of each face
    piece_closed: np.ndarray  # (pieces,) whether each piece is closed
    units: UnitsFinding | None = None
    scale: float = 1.0  # what its numbers were multiplied by
    dropped_faces: int = 0
    warnings: list[str] = field(default_factory=list)
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def size_mm(self) -> float:
        """The diagonal of its box along its principal axes."""
        return float(np.linalg.norm(self.extent_mm))

    @property
    def ready(self) -> bool:
        """False while a units finding waits for the caller's answer."""
        return self.units is None or self.units.status != "pending"

    def apply_units(self, factor: float | None = None) -> ReferenceModel:
        """A copy scaled by ``factor`` (default: the units finding's), marked as confirmed."""
        f = factor if factor is not None else (self.units.factor if self.units is not None else None)
        if f is None:
            raise ValueError("there is no unit factor to apply: give one")
        f = float(f)
        if not (math.isfinite(f) and f > 0):
            raise ValueError(f"the unit factor must be a positive number, got {factor!r}")
        unit = self.units.unit if self.units is not None and self.units.factor == f else None
        msg = f"Scaled by {f:g}" + (f" ({unit} to mm)" if unit else "") + " as confirmed."
        units = (replace(self.units, status="applied", factor=f, unit=unit, message=msg) if self.units is not None
                 else UnitsFinding("applied", float("nan"), f, unit, [], self.size_mm, float("nan"), msg))
        lo, hi = self.bounds
        return replace(self, V=self.V * f, area_mm2=self.area_mm2 * f * f, extent_mm=[e * f for e in self.extent_mm],
                       bounds=([x * f for x in lo], [x * f for x in hi]), units=units, scale=self.scale * f, _cache={})

    def keep_units(self) -> ReferenceModel:
        """A copy whose size is confirmed as it is (in mm)."""
        msg = "Kept as it is (in mm), as confirmed."
        units = (replace(self.units, status="kept", message=msg) if self.units is not None
                 else UnitsFinding("kept", float("nan"), None, None, [], self.size_mm, float("nan"), msg))
        return replace(self, units=units, _cache={})

    def closest(self) -> _Closest:
        if "closest" not in self._cache:
            self._cache["closest"] = _Closest(self.V, self.F)
        return self._cache["closest"]

    def facing(self, F: np.ndarray, prim: np.ndarray, uv: np.ndarray, N: np.ndarray, cos: float) -> np.ndarray:
        """Whether the directions ``N`` agree (within ``cos``) with the reference's face normal at
        closest points on faces ``prim`` (barycentric ``uv``), the faces wound as ``F``. A closest
        point on an edge or a corner belongs to every face there, so any of them may agree: a
        surface standing above a rim faces like the wall below it, not like the top."""
        fn = face_normals(self.V, F)[0]
        ok = np.einsum("ij,ij->i", N, fn[prim]) >= cos
        w = np.column_stack([1.0 - uv[:, 0] - uv[:, 1], uv[:, 0], uv[:, 1]])
        small = w < 1e-4
        ns = small.sum(axis=1)
        if "topology" not in self._cache:
            flat = self.F.reshape(-1)
            order = np.argsort(flat, kind="stable")
            ptr = np.searchsorted(flat[order], np.arange(len(self.V) + 1))
            self._cache["topology"] = (edge_table(self.F, len(self.V)), order // 3, ptr)
        et, vfaces, ptr = self._cache["topology"]
        q = np.flatnonzero(~ok & (ns == 1))  # on an edge: the face across it
        if len(q):
            k = (np.argmax(small[q], axis=1) + 1) % 3  # the edge from corner k to k + 1 lies opposite the small one
            e = et.he_edge[3 * prim[q] + k]
            s = et.edge_start[e]
            fa = et.he_order[s] // 3
            fb = et.he_order[np.minimum(s + 1, len(et.he_order) - 1)] // 3
            other = np.where(fa == prim[q], fb, fa)
            ok[q] = (et.counts[e] == 2) & (np.einsum("ij,ij->i", N[q], fn[other]) >= cos)
        q = np.flatnonzero(~ok & (ns >= 2))  # at a corner: every face around it
        if len(q):
            vert = self.F[prim[q], np.argmax(w[q], axis=1)]
            n = ptr[vert + 1] - ptr[vert]
            which = np.repeat(np.arange(len(q)), n)
            at = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n) + np.repeat(ptr[vert], n)
            agree = np.einsum("ij,ij->i", N[q][which], fn[vfaces[at]]) >= cos
            ok[q] = np.bincount(which, weights=agree.astype(np.float64), minlength=len(q)) > 0
        return ok

    def cloud(self, params: AlignParams, seed: int = 0, turn_open: bool = False):
        """Points spread over the surface as an alignment cloud (no support plane is set aside: a
        reference has no table). ``turn_open``: open pieces seen from their other side."""
        key = ("cloud", params, seed, turn_open)
        if key not in self._cache:
            F = self.F
            if turn_open:
                F = F.copy()
                t = ~self.piece_closed[self.face_piece]
                F[t] = F[t][:, ::-1]
            P, face = _sample_surface(self.V, F, 0.5 * float(params.voxel_mm), seed)
            N = face_normals(self.V, F)[0][face]
            self._cache[key] = scan_cloud(P, N, replace(params, support_min_share=2.0), seed)
        return self._cache[key]

    def to_dict(self) -> dict:
        return {"format_version": FORMAT_VERSION, "name": self.name, "path": self.path, "n_vertices": int(len(self.V)),
                "n_faces": int(len(self.F)), "area_mm2": _f(self.area_mm2), "size_mm": _f(self.size_mm),
                "extent_mm": [_f(e) for e in self.extent_mm],
                "bounds": [[float(x) for x in self.bounds[0]], [float(x) for x in self.bounds[1]]],
                "orientation": dict(self.orientation), "units": None if self.units is None else self.units.to_dict(),
                "ready": self.ready, "scale": float(self.scale), "dropped_faces": int(self.dropped_faces),
                "warnings": list(self.warnings)}


def reference_from_arrays(V, F, *, name: str = "reference", path: str | None = None,
                          scan: ScanSurface | None = None, params: AlignParams | None = None) -> ReferenceModel:
    """A :class:`ReferenceModel` from vertices and triangles (mm, or another unit: see ``scan``),
    checked as :func:`read_reference` says. With ``scan``, the units finding is made."""
    V = np.asarray(V, dtype=np.float64)
    F = np.asarray(F)
    if F.size == 0:
        raise ValueError(f"{name} has no faces: " + ("it is a point cloud, not a mesh" if V.size else "it holds no triangles"))
    if V.ndim != 2 or V.shape[1] != 3:
        raise ValueError(f"{name}: the vertices must be (n, 3) points")
    if not np.all(np.isfinite(V)):
        raise ValueError(f"{name} has points that are not a number (NaN or infinite)")
    try:
        V, F = as_mesh(V, F)
    except ValueError as e:
        raise ValueError(f"{name} is not a usable mesh: {e}") from None
    # one vertex per position: files that repeat each corner per face (most PLY / OBJ exports, STL
    # always) would otherwise have no neighbours to wind them by
    V, inv = np.unique(V, axis=0, return_inverse=True)
    F = inv.reshape(-1)[F]
    used = sorted_unique(F)
    span = float(np.linalg.norm(V[used].max(axis=0) - V[used].min(axis=0)))
    area = face_areas(V, F)
    good = area > 1e-12 * span * span
    dropped = int((~good).sum())
    if not good.any():
        raise ValueError(f"{name}: its faces have no area (every triangle is a line or a point)")
    F, area = F[good], area[good]
    warnings = [f"{dropped} face(s) without area were left out"] if dropped else []
    a, b, c = rows(V, F[:, 0]), rows(V, F[:, 1]), rows(V, F[:, 2])
    ext = _extents((a + b + c) / 3.0, area, Q=V[sorted_unique(F)])
    size = float(np.linalg.norm(ext))
    if size < SIZE_MIN_MM:
        raise ValueError(f"{name} is far too small ({size:.3g} across, in its own numbers): no unit makes a part of it")
    if size > SIZE_MAX_MM:
        raise ValueError(f"{name} is far too large ({size:.3g} across, in its own numbers): no unit makes a part of it")
    if scan is None and not (1.0 <= size <= 5000.0):
        warnings.append(f"it is {size:.3g} across in its own numbers: if that is not mm, give the scan to check its units")
    Fo, info, piece, closed = _orient(V, F)
    used = sorted_unique(Fo)
    units = check_units(size, scan.part_size_mm(params)) if scan is not None else None
    return ReferenceModel(V=V, F=Fo, name=str(name), path=path, area_mm2=float(area.sum()), extent_mm=[float(e) for e in ext],
                          bounds=(V[used].min(axis=0).tolist(), V[used].max(axis=0).tolist()), orientation=info,
                          face_piece=piece, piece_closed=closed, units=units, dropped_faces=dropped, warnings=warnings)


def read_reference(path: str | Path, *, scan: ScanSurface | None = None, name: str | None = None,
                   params: AlignParams | None = None) -> ReferenceModel:
    """Read a reference model (STL, OBJ or PLY) and check it (module docstring, 1.). With ``scan``
    its size is compared with the scan's part, and a likely other unit is reported (``units``) for
    the caller to confirm. Raises ValueError with a sentence for what cannot be compared."""
    from ohs.track.export import load_mesh  # noqa: PLC0415

    path = Path(path)
    label = name or path.name
    ext = path.suffix.lower()
    if ext not in SUFFIXES:
        hint = " (export a STEP or other CAD file as STL first)" if ext in (".step", ".stp", ".iges", ".igs") else ""
        raise ValueError(f"a CAD model must be an STL, OBJ or PLY mesh; {label} is {ext or 'without an extension'}{hint}")
    if not path.is_file():
        raise ValueError(f"the CAD model's file {label} was not found")
    try:
        V, F, _ = load_mesh(path)
    except Exception as e:  # noqa: BLE001 (a broken file can fail anywhere in a reader)
        raise ValueError(f"{label} could not be read as {ext[1:].upper()}: {e}") from None
    return reference_from_arrays(V, F, name=label, path=str(path), scan=scan, params=params)


def _require_ready(ref: ReferenceModel) -> None:
    if not ref.ready:
        raise ValueError(f"the CAD model's units are not settled: {ref.units.message}")


# --------------------------------------------------------------------------- aligning
@dataclass
class ReferenceAlignment:
    """How the scan was put onto the reference: ``T`` takes the scan's points into the
    reference's frame; ``method`` says which way (module docstring, 2.)."""

    T: np.ndarray
    method: str  # "shape", "points", "points+shape", "datums", "given"
    accepted: bool
    verdict: str
    pair: dict | None = None  # the alignment engine's own numbers (PairAlignment.as_dict), when it ran
    points_rms_mm: float | None = None  # how well the picked pairs agree
    n_points: int = 0
    frame: dict | None = None  # the scan's part frame, for "datums"
    fit: dict | None = None  # the least-squares step after the engine's fit: its scale and how far it moved
    turn_check: dict | None = None  # the pose from the shape against itself turned 180° (_turn_check)
    vs_datums: dict | None = None  # a best fit's distance from the datum alignment
    warnings: list[str] = field(default_factory=list)
    ms: float = 0.0

    def describe(self) -> str:
        """A few words for a report: how it was aligned."""
        return {"shape": "best fit on the shapes", "points": f"from {self.n_points} picked point pairs",
                "points+shape": f"from {self.n_points} picked point pairs, refined on the shapes",
                "datums": "on the part's datums, no best fit", "given": "at a given pose"}.get(self.method, self.method)

    def to_dict(self) -> dict:
        return {"format_version": FORMAT_VERSION, "method": self.method, "described": self.describe(),
                "accepted": bool(self.accepted), "verdict": self.verdict, "T": np.asarray(self.T).tolist(),
                "pair": None if self.pair is None else {k: (_f(v) if isinstance(v, float) else v) for k, v in self.pair.items()},
                "points_rms_mm": _f(self.points_rms_mm), "n_points": int(self.n_points), "frame": self.frame,
                "fit": None if self.fit is None else dict(self.fit),
                "turn_check": None if self.turn_check is None else dict(self.turn_check),
                "vs_datums": None if self.vs_datums is None else dict(self.vs_datums), "warnings": list(self.warnings),
                "ms": float(self.ms)}


def _frame(datums):
    from ohs.measure.frame import PartFrame  # noqa: PLC0415

    if isinstance(datums, PartFrame):
        return datums
    if isinstance(datums, dict):
        return PartFrame.from_dict(datums)
    raise ValueError(f"the datums must be a part frame (PartFrame or its to_dict()), got {type(datums).__name__}")


def _datum_T(datums, reference_frame=None) -> np.ndarray:
    """Scan -> reference by the datums: the scan's part frame, then (when the reference has its
    own) back out of the reference's."""
    T = _frame(datums).matrix
    if reference_frame is not None:
        T = invert_se3(_frame(reference_frame).matrix) @ T
    return np.asarray(T, dtype=np.float64)


def _versus(T: np.ndarray, T_datums: np.ndarray, P: np.ndarray) -> dict:
    """How far the pose ``T`` lies from ``T_datums`` over the scan's points ``P``."""
    D = _moved(P, T) - _moved(P, T_datums)
    dist = np.linalg.norm(D, axis=1)
    c = P.mean(axis=0)
    shift = float(np.linalg.norm(_moved(c[None], T) - _moved(c[None], T_datums)))
    angle = rotation_angle_deg(T[:3, :3] @ T_datums[:3, :3].T)
    return {"rms_mm": float(np.sqrt(np.mean(dist**2))), "max_mm": float(dist.max()), "shift_mm": shift,
            "angle_deg": float(angle),
            "note": (f"The best fit lies typically {np.sqrt(np.mean(dist ** 2)):.2f} mm (at most {dist.max():.2f} mm) from the "
                     f"alignment on the datums: {shift:.2f} mm at the scan's centre and {angle:.2f}° turned. A best fit "
                     "spreads an overall error (a part too long, bent or shifted) over the whole part; on the datums it "
                     "shows where it is.")}


def _scan_points(c) -> np.ndarray:
    """The part's points of a scan cloud (all of them when no part was told apart)."""
    return c.part.fine.pts if c.part is not None and c.part.n else c.full.fine.pts


def _pair(scan: ScanSurface, ref: ReferenceModel, p: AlignParams, seed: int, init=None):
    """:func:`align_pair` of the scan onto the reference; an open reference that does not align is
    tried once more seen from its other side. ``(result, the scan's cloud, the reference's cloud,
    notes)``."""
    kw = {} if init is None else {"init": init, "init_from": "given"}
    src, note = scan.part_cloud(p, seed)
    notes = [note] if note else []
    dst = ref.cloud(p, seed)
    res = align_pair(src, dst, p, **kw)
    if not res.accepted and ref.orientation.get("open_pieces"):
        dst2 = ref.cloud(p, seed, turn_open=True)
        res2 = align_pair(src, dst2, p, **kw)
        if res2.accepted:
            res, dst = res2, dst2
            notes.append("the CAD model's open surface was matched from its other side")
    # what the engine's own check of the pose turned round found (MAINTENANCE_PLAN F11)
    found_turned = bool(res.poses) and res.poses[0]["from"] == ["turned round"]
    check = {"turned": int(res.turned), "reached": int(res.reached), "doubt": res.doubt, "poses": len(res.poses),
             "taken": 0 if found_turned else None, "note": None}
    if found_turned:
        check["note"] = ("the first guesses ended at a pose that fits worse than the same turned round, and the turned pose "
                         "was taken")
        notes.append(check["note"])
    return res, src, dst, notes, check


def _least_squares(src, dst, T: np.ndarray, fit_mm: float | None) -> tuple[np.ndarray, dict | None]:
    """The engine's pose made a least-squares best fit (module docstring, 2.): the same ICP with
    its Tukey scale widened to ``fit_mm``. The engine's 0.5 mm scale is right for telling a match
    from a mismatch, but it gives a residual of 0.4 mm almost no weight, so the fit keeps an overall
    error wherever it started: a block 0.5 mm too long came out 0.07 and 0.43 mm at its two ends,
    and 0.24 and 0.26 after this step. A step that moves the scan further than ``fit_mm`` is not
    taken (it would be another pose, not a refinement)."""
    if fit_mm is None or src.part is None or dst.part is None:
        return T, None
    from ohs.track import posegraph as pg  # noqa: PLC0415

    T2 = pg._icp(src.part, dst.part, T, ((False, float(fit_mm), 30),), 1e-9, float(fit_mm))
    P = src.part.fine.pts
    moved = float(np.sqrt(np.mean(np.sum((_moved(P, T2) - _moved(P, T)) ** 2, axis=1)))) if np.all(np.isfinite(T2)) else math.inf
    if moved > fit_mm:
        return T, {"robust_mm": float(fit_mm), "moved_mm": _f(moved), "taken": False,
                   "note": f"the least-squares step would have moved the scan {moved:.2f} mm: not taken"}
    return T2, {"robust_mm": float(fit_mm), "moved_mm": moved, "taken": True,
                "note": f"made a least-squares fit (residuals up to {fit_mm:g} mm count fully): moved {moved:.3f} mm"}


def _pair_verdict(res, sc, p: AlignParams, what: str, turned: bool = False) -> str:
    table = (f" The scan's support plane ({100 * sc.support_share:.0f} % of it, a table) was set aside."
             if sc.support is not None else "")
    if res.accepted:
        sure = ("turned round from where the first guesses ended, where it fits better" if turned
                else f"{res.agree} of {res.candidates} starting guesses agree" if res.start == "features"
                else "from the picked points")
        loose = "" if res.dof >= 6 else f" The surfaces leave {6 - res.dof} direction(s) of motion loose."
        return (f"Aligned {what}: {100 * res.overlap:.0f} % of the matched surface lies within {p.match_mm:g} mm, "
                f"typically {res.rmse_mm:.2f} mm; {sure}.{loose}{table}")
    hint = ("" if res.start != "features" else
            " Pick three or more matching points on the scan and on the CAD model, or cut away what is not the part "
            "(a table, clamps) first.")
    return f"Not aligned {what}: {res.reason}.{table}{hint}"


def align_by_shape(scan: ScanSurface, ref: ReferenceModel, params: AlignParams | None = None, *, datums=None,
                   reference_frame=None, fit_mm: float | None = NEAR_MM, seed: int = 0) -> ReferenceAlignment:
    """A best fit of the scan onto the reference from their shapes alone (module docstring, 2.).
    With ``datums`` (the scan's part frame) the result also says how far it lies from the
    alignment on the datums (``vs_datums``). ``fit_mm``: the scale of the last, least-squares
    step (:func:`_least_squares`; None leaves the engine's robust pose as it is)."""
    _require_ready(ref)
    p = params if params is not None else DEFAULT_PARAMS
    t0 = time.perf_counter()
    res, src, dst, notes, check = _pair(scan, ref, p, seed)
    T, fit = _least_squares(src, dst, res.T, fit_mm) if res.accepted else (res.T, None)
    turned = check is not None and check.get("taken") is not None
    out = ReferenceAlignment(T=T.copy(), method="shape", accepted=bool(res.accepted),
                             verdict=_pair_verdict(res, src, p, "on the shapes (best fit)", turned), pair=res.as_dict(),
                             fit=fit, turn_check=check, warnings=notes)
    if datums is not None:
        out.vs_datums = _versus(out.T, _datum_T(datums, reference_frame), _scan_points(src))
    out.ms = round(1e3 * (time.perf_counter() - t0), 1)
    return out


def align_by_points(scan: ScanSurface, ref: ReferenceModel, scan_points, reference_points, *, refine: bool = True,
                    params: AlignParams | None = None, datums=None, reference_frame=None, fit_mm: float | None = NEAR_MM,
                    seed: int = 0) -> ReferenceAlignment:
    """The scan onto the reference from three or more pairs of points picked on both
    (``scan_points[i]`` on the scan matches ``reference_points[i]``), refined on the shapes from
    there (a best fit, as :func:`align_by_shape`) or not (``refine``). ValueError for fewer than
    three pairs or pairs in one line."""
    _require_ready(ref)
    p = params if params is not None else DEFAULT_PARAMS
    t0 = time.perf_counter()
    T0, rms = pose_from_point_pairs(scan_points, reference_points)
    n = int(np.asarray(scan_points).reshape(-1, 3).shape[0])
    warnings = []
    if rms > 2.0:
        warnings.append(f"the picked pairs typically agree only to {rms:.1f} mm: a pair may be wrong or in the wrong order")
    if not refine:
        out = ReferenceAlignment(T=T0, method="points", accepted=True, n_points=n, points_rms_mm=rms, warnings=warnings,
                                 verdict=(f"Aligned from {n} picked pairs: they typically agree to {rms:.2f} mm; not refined on "
                                          "the shapes, so the result is as good as the picks."))
    else:
        res, src, dst, notes, _ = _pair(scan, ref, p, seed, init=T0)
        T, fit = _least_squares(src, dst, res.T, fit_mm) if res.accepted else (res.T, None)
        out = ReferenceAlignment(T=T.copy(), method="points+shape", accepted=bool(res.accepted), n_points=n,
                                 points_rms_mm=rms, pair=res.as_dict(), fit=fit, warnings=warnings + notes,
                                 verdict=_pair_verdict(res, src, p, f"from {n} picked pairs, refined on the shapes"))
    if datums is not None:
        out.vs_datums = _versus(out.T, _datum_T(datums, reference_frame), _scan_points(scan.part_cloud(p, seed)[0]))
    out.ms = round(1e3 * (time.perf_counter() - t0), 1)
    return out


def align_by_datums(datums, *, reference_frame=None) -> ReferenceAlignment:
    """The scan onto the reference by the part's datums (module docstring, 2.): ``datums`` is the
    scan's part frame (:class:`ohs.measure.frame.PartFrame` or its ``to_dict()``); the reference is
    taken to be modelled in the part's axes, or ``reference_frame`` is the same frame built on the
    reference. No best fit: an overall error of the part shows where it is."""
    fr = _frame(datums)
    T = _datum_T(fr, reference_frame)
    names = [f"{b.get('name') or b.get('kind')}" for b in fr.built_from]
    sig = fr.sigma or {}
    warnings = list(fr.warnings)
    if sig.get("angle_deg") is not None and sig.get("origin_mm") is not None:
        warnings.append(f"the datums' own ± (1σ): {sig['angle_deg']:.3f}° and {sig['origin_mm']:.3f} mm at the origin; a "
                        f"point L mm from the origin moves about L x {math.radians(sig['angle_deg']):.2g} mm with the turn")
    onto = "the part's axes set on the CAD model" if reference_frame is not None else "the CAD model's own axes"
    verdict = (f"Aligned on the datums: the part's axes on the scan (from {', '.join(names)}) put onto {onto}; no best fit, "
               "so an overall error of the part shows where it is.")
    return ReferenceAlignment(T=T, method="datums", accepted=True, verdict=verdict, frame=fr.to_dict(), warnings=warnings)


def given_alignment(T, note: str = "") -> ReferenceAlignment:
    """A pose kept from before (scan -> reference), used as it is."""
    T = _rigid(T)
    return ReferenceAlignment(T=T, method="given", accepted=True,
                              verdict="Aligned at a given pose" + (f" ({note})" if note else "") + ".")


# --------------------------------------------------------------------------- the deviation
@dataclass
class Deviation:
    """Each scan vertex's signed distance from the reference (module docstring, 3.). Arrays are per
    scan vertex (``offset``, ``sigma``, ``state``) or face (``face_compared``, ``face_off_reference``);
    ``V`` is the scan in the reference's frame."""

    V: np.ndarray
    F: np.ndarray
    offset: np.ndarray  # float32 (n,): + outside the reference, - inside; NaN where not compared
    sigma: np.ndarray | None
    state: np.ndarray  # int8 (n,): 0 compared, 1 not measured, 2 not on the reference
    along_normal: np.ndarray  # bool (n,): measured along the vertex's normal, not to the nearest point (a concave corner)
    face_compared: np.ndarray
    face_off_reference: np.ndarray
    scan_area_mm2: float
    compared_area_mm2: float
    off_reference_area_mm2: float
    not_measured_area_mm2: float
    reference_area_mm2: float
    uncovered_share: float | None  # of the reference's area: not covered by compared scan surface
    near_mm: float
    snap_mm: float
    normal_deg: float
    cover_mm: float
    method: str  # how the scan was aligned
    notes: list[str] = field(default_factory=list)
    ms: dict = field(default_factory=dict)

    COMPARED, NOT_MEASURED, OFF_REFERENCE = 0, 1, 2

    @property
    def off_reference_share(self) -> float:
        """Of the scan's area: measured surface that is not on the reference (a table, a clamp)."""
        return self.off_reference_area_mm2 / self.scan_area_mm2 if self.scan_area_mm2 > 0 else 0.0

    @property
    def not_measured_share(self) -> float:
        return self.not_measured_area_mm2 / self.scan_area_mm2 if self.scan_area_mm2 > 0 else 0.0

    @property
    def uncovered_area_mm2(self) -> float | None:
        return None if self.uncovered_share is None else self.uncovered_share * self.reference_area_mm2

    @property
    def rule(self) -> str:
        return (f"A measured scan vertex (not on a filled face, its ± under {SIGMA_NONE_MM:g} mm) is compared when it "
                f"lies within {self.near_mm:g} mm of the CAD model's surface, inside the CAD model's box grown by "
                f"{self.near_mm:g} mm, and, unless it is within {self.snap_mm:g} mm, faces within {self.normal_deg:g}° "
                "of the way the CAD model faces there (or, in a concave corner, finds CAD model surface facing its way "
                f"within {self.near_mm:g} mm along its own normal, measured there); any other measured vertex is not on "
                "the CAD model (a table, a "
                f"clamp, or surface more than {self.near_mm:g} mm off). A face is compared when its three vertices are. "
                f"The CAD model's surface counts as scanned where a compared vertex lies within {self.near_mm:g} mm of it, at "
                f"most {self.cover_mm:.2g} mm to the side, facing the same way.")

    def to_dict(self) -> dict:
        ok = np.isfinite(self.offset)
        return {"format_version": FORMAT_VERSION, "method": self.method, "n_vertices": int(len(self.V)),
                "n_compared": int(ok.sum()), "n_not_measured": int((self.state == self.NOT_MEASURED).sum()),
                "n_off_reference": int((self.state == self.OFF_REFERENCE).sum()),
                "n_along_normal": int(self.along_normal.sum()),
                "scan_area_mm2": _f(self.scan_area_mm2), "compared_area_mm2": _f(self.compared_area_mm2),
                "off_reference_area_mm2": _f(self.off_reference_area_mm2), "off_reference_share": _f(self.off_reference_share),
                "not_measured_area_mm2": _f(self.not_measured_area_mm2), "not_measured_share": _f(self.not_measured_share),
                "reference_area_mm2": _f(self.reference_area_mm2), "uncovered_share": _f(self.uncovered_share),
                "uncovered_area_mm2": _f(self.uncovered_area_mm2), "near_mm": float(self.near_mm),
                "snap_mm": float(self.snap_mm), "normal_deg": float(self.normal_deg), "cover_mm": _f(self.cover_mm),
                "has_sigma": self.sigma is not None, "rule": self.rule, "notes": list(self.notes),
                "ms": {k: float(v) for k, v in self.ms.items()}}


def deviation_from_reference(scan: ScanSurface, ref: ReferenceModel, alignment: ReferenceAlignment | np.ndarray, *,
                             near_mm: float = NEAR_MM, snap_mm: float = SNAP_MM, normal_deg: float = NORMAL_DEG,
                             cover_mm: float | None = None, seed: int = 0) -> Deviation:
    """The scan's deviation from the reference at ``alignment`` (or a scan -> reference matrix),
    with the rule for what is not on the reference and the share of the reference not scanned
    (module docstring, 3.)."""
    _require_ready(ref)
    for name, v in (("near_mm", near_mm), ("normal_deg", normal_deg)):
        if not (math.isfinite(v) and v > 0):
            raise ValueError(f"{name} must be above 0, got {v}")
    if not (math.isfinite(snap_mm) and 0 <= snap_mm <= near_mm):
        raise ValueError(f"snap_mm must be between 0 and near_mm, got {snap_mm}")
    if isinstance(alignment, ReferenceAlignment):
        T, method = _rigid(alignment.T, "the alignment"), alignment.method
    else:
        T, method = _rigid(alignment, "the alignment"), "given"
    ms: dict = {}
    t = time.perf_counter()
    V = _moved(scan.V, T)
    N = scan.normals() @ T[:3, :3].T
    F = scan.F
    filled = scan.filled if scan.filled is not None else np.zeros(len(F), dtype=bool)
    measured_v = np.zeros(len(V), dtype=bool)
    measured_v[F[~filled]] = True
    if scan.sigma is not None:
        s = scan.sigma
        measured_v &= np.isfinite(s) & (s > 0) & (s < SIGMA_NONE_MM)
    notes: list[str] = []
    closest = ref.closest()
    # open pieces: outward is the side the scan sees them from
    Fr = ref.F
    if not ref.piece_closed.all() and measured_v.any():
        idx = np.flatnonzero(measured_v)
        if len(idx) > VOTE_POINTS:
            idx = np.sort(np.random.default_rng(seed).choice(idx, VOTE_POINTS, replace=False))
        Q, prim, _ = closest.query(V[idx])
        near = np.linalg.norm(V[idx] - Q, axis=1) <= near_mm
        fn = face_normals(ref.V, Fr)[0][prim]
        vote = np.sign(np.einsum("ij,ij->i", N[idx], fn))
        tally = np.bincount(ref.face_piece[prim[near]], weights=vote[near], minlength=len(ref.piece_closed))
        turn = ~ref.piece_closed & (tally < 0)
        if turn.any():
            Fr = Fr.copy()
            t_faces = turn[ref.face_piece]
            Fr[t_faces] = Fr[t_faces][:, ::-1]
        n_open = int((~ref.piece_closed).sum())
        notes.append(f"{n_open} open piece(s) of the CAD model: their outside is the side the scan sees them from"
                     + (f" ({int(turn.sum())} turned round for it)" if turn.any() else ""))
    ms["prepare"] = round(1e3 * (time.perf_counter() - t), 1)
    t = time.perf_counter()
    d = surface_offset(V, ref.V, Fr, F=F, filled=filled).astype(np.float64)
    d[~measured_v] = np.nan
    ms["offset"] = round(1e3 * (time.perf_counter() - t), 1)
    t = time.perf_counter()
    has = np.flatnonzero(np.isfinite(d))
    cand = np.zeros(len(V), dtype=bool)
    lo, hi = np.asarray(ref.bounds[0]) - near_mm, np.asarray(ref.bounds[1]) + near_mm
    inside = np.all((V[has] >= lo) & (V[has] <= hi), axis=1)
    close = np.abs(d[has]) <= near_mm
    pick = has[inside & close]
    cos = math.cos(math.radians(normal_deg))
    _, prim, uv = closest.query(V[pick])  # surface_offset keeps which face it found to itself: asked again here
    ok = ref.facing(Fr, prim, uv, N[pick], cos) | (np.abs(d[pick]) <= snap_mm)
    # facing away from the nearest reference face: in a concave corner (a floor sunk next to a wall)
    # the wall is nearer than the floor the surface belongs to. Along the vertex's own normal, the
    # reference surface that faces the same way within near_mm is its counterpart, and the
    # distance is taken there; a table beside the part's foot finds none.
    q = pick[~ok]
    along = np.zeros(len(V), dtype=bool)
    if len(q):
        fn = face_normals(ref.V, Fr)[0]
        best = np.full(len(q), np.inf)
        val = np.full(len(q), np.nan)
        for sign in (-1.0, 1.0):  # behind the vertex: it lies outside (+); in front: inside (-)
            t_hit, f_hit = closest.cast(V[q], sign * N[q])
            good = (t_hit <= near_mm) & (f_hit >= 0)
            good[good] &= np.einsum("ij,ij->i", N[q][good], fn[f_hit[good]]) >= cos
            better = good & (t_hit < best)
            best[better] = t_hit[better]
            val[better] = -sign * t_hit[better]
        found = np.isfinite(best)
        d[q[found]] = val[found]
        along[q[found]] = True
        ok[~ok] = found
    cand[pick] = ok
    if along.any():
        notes.append(f"{int(along.sum())} vertices face away from the nearest face of the CAD model (a concave corner): "
                     "measured along their own normal to the CAD model's surface that faces their way")
    state = np.where(cand, Deviation.COMPARED, np.where(measured_v, Deviation.OFF_REFERENCE, Deviation.NOT_MEASURED)).astype(np.int8)
    measured_f = ~filled & measured_v[F[:, 0]] & measured_v[F[:, 1]] & measured_v[F[:, 2]]
    compared_f = measured_f & cand[F[:, 0]] & cand[F[:, 1]] & cand[F[:, 2]]
    off_f = measured_f & ~compared_f
    offset = np.where(cand, d, np.nan).astype(np.float32)
    area = face_areas(V, F)
    ms["classify"] = round(1e3 * (time.perf_counter() - t), 1)
    # how much of the reference the compared surface covers
    t = time.perf_counter()
    cv = sorted_unique(F[compared_f]) if compared_f.any() else np.zeros(0, dtype=np.int64)
    if cover_mm is None:
        if compared_f.any():
            Fc = F[compared_f]
            e = np.linalg.norm(V[Fc[:, 0]] - V[Fc[:, 1]], axis=1)
            cover_mm = max(COVER_MIN_MM, COVER_EDGES * float(np.median(e)))
        else:
            cover_mm = COVER_MIN_MM
    uncovered = None
    if len(cv):
        from scipy.spatial import cKDTree  # noqa: PLC0415

        spacing = max(0.5, math.sqrt(ref.area_mm2 / 300_000.0))
        P, face = _sample_surface(ref.V, Fr, spacing, seed)
        PN = face_normals(ref.V, Fr)[0][face]
        dist, j = cKDTree(V[cv]).query(P, k=1, distance_upper_bound=near_mm, workers=-1)
        ok = np.isfinite(dist)
        jj = cv[j[ok]]
        gap = V[jj] - P[ok]
        across = np.einsum("ij,ij->i", gap, PN[ok])  # off the reference surface; the rest is to the side
        side = np.sqrt(np.maximum(np.einsum("ij,ij->i", gap, gap) - across**2, 0.0))
        agree = np.einsum("ij,ij->i", N[jj], PN[ok]) >= math.cos(math.radians(normal_deg))
        covered = np.zeros(len(P), dtype=bool)
        covered[np.flatnonzero(ok)] = agree & (side <= cover_mm)
        uncovered = float(1.0 - covered.mean()) if len(P) else None
    elif compared_f.size:
        uncovered = 1.0
    ms["coverage"] = round(1e3 * (time.perf_counter() - t), 1)
    if not compared_f.any():
        notes.append("nothing of the scan lies on the CAD model: check the alignment")
    return Deviation(V=V, F=F, offset=offset, sigma=scan.sigma, state=state, along_normal=along, face_compared=compared_f,
                     face_off_reference=off_f, scan_area_mm2=float(area.sum()), compared_area_mm2=float(area[compared_f].sum()),
                     off_reference_area_mm2=float(area[off_f].sum()), not_measured_area_mm2=float(area[~measured_f].sum()),
                     reference_area_mm2=float(ref.area_mm2), uncovered_share=uncovered, near_mm=float(near_mm),
                     snap_mm=float(snap_mm), normal_deg=float(normal_deg), cover_mm=float(cover_mm), method=method,
                     notes=notes, ms=ms)


# --------------------------------------------------------------------------- the answer for a tolerance
@dataclass
class DeviationRegion:
    """Compared faces outside the tolerance on one side, joined over shared edges (module
    docstring, 4.). ``faces`` index the scan's faces; positions are in the reference's frame."""

    side: str  # "above" (+, outside the reference) or "below" (-, inside it)
    area_mm2: float
    share: float  # of the compared area
    center: list[float]
    extent_mm: float  # the largest side of its box
    radius_mm: float  # its farthest vertex from the centre
    n_faces: int
    mean_mm: float  # area-weighted
    worst_mm: float  # the vertex furthest out on its side
    sigma_median_mm: float | None
    exceeds_uncertainty: bool | None  # |mean| above k times the median sigma
    share_beyond_uncertainty: float | None  # of its area, faces whose |deviation| exceeds k times their sigma
    faces: np.ndarray = field(repr=False)

    def to_dict(self, with_faces: bool = False) -> dict:
        d = {"side": self.side, "area_mm2": _f(self.area_mm2), "share": _f(self.share), "center": [float(x) for x in self.center],
             "extent_mm": _f(self.extent_mm), "radius_mm": _f(self.radius_mm), "n_faces": int(self.n_faces),
             "mean_mm": _f(self.mean_mm), "worst_mm": _f(self.worst_mm), "sigma_median_mm": _f(self.sigma_median_mm),
             "exceeds_uncertainty": self.exceeds_uncertainty, "share_beyond_uncertainty": _f(self.share_beyond_uncertainty)}
        if with_faces:
            d["faces"] = [int(f) for f in self.faces]
        return d


@dataclass
class ToleranceSummary:
    """The comparison at one tolerance (module docstring, 4.). Shares are of the compared area."""

    tolerance_mm: float
    k: float
    compared_area_mm2: float
    share_within: float | None
    share_above: float | None
    share_below: float | None
    share_uncertain: float | None  # compared area where k sigma is wider than the tolerance
    worst_above_mm: float | None
    worst_below_mm: float | None
    stats: dict
    min_area_mm2: float
    n_regions: int
    regions: list[DeviationRegion]
    sentence: str

    def to_dict(self, with_faces: bool = False) -> dict:
        return {"format_version": FORMAT_VERSION, "tolerance_mm": float(self.tolerance_mm), "k": float(self.k),
                "compared_area_mm2": _f(self.compared_area_mm2), "share_within": _f(self.share_within),
                "share_above": _f(self.share_above), "share_below": _f(self.share_below),
                "share_uncertain": _f(self.share_uncertain), "worst_above_mm": _f(self.worst_above_mm),
                "worst_below_mm": _f(self.worst_below_mm), "stats": {k: _f(v) for k, v in self.stats.items()},
                "min_area_mm2": float(self.min_area_mm2), "n_regions": int(self.n_regions),
                "regions": [r.to_dict(with_faces) for r in self.regions], "sentence": self.sentence}


def tolerance_summary(dev: Deviation, tolerance_mm: float, *, k: float = 2.0, min_area_mm2: float | None = None,
                      max_regions: int = 50) -> ToleranceSummary:
    """The deviation at ``tolerance_mm`` (module docstring, 4.): shares by area within ± the
    tolerance and outside it on each side, the worst values, robust statistics and the regions
    outside, each with whether it exceeds ``k`` times the scan's own sigma. ``min_area_mm2``
    (default as :func:`ohs.mesh.check.weak_areas`: 4 mm², or ten median faces) is the smallest
    region listed; smaller ones still count in the shares."""
    tol = float(tolerance_mm)
    if not (math.isfinite(tol) and tol > 0):
        raise ValueError(f"the tolerance must be above 0 mm, got {tolerance_mm}")
    if not (math.isfinite(k) and k > 0):
        raise ValueError(f"k must be above 0, got {k}")
    if max_regions < 0:
        raise ValueError(f"max_regions must be 0 or more, got {max_regions}")
    fidx = np.flatnonzero(dev.face_compared)
    Fc = dev.F[fidx]
    a = face_areas(dev.V, Fc)
    total = float(a.sum())
    if min_area_mm2 is None:
        min_area_mm2 = max(MIN_AREA_FLOOR_MM2, MIN_AREA_FACES * (float(np.median(a)) if len(a) else 0.0))
    if total <= 0:
        return ToleranceSummary(tol, float(k), 0.0, None, None, None, None, None, None, {}, float(min_area_mm2), 0, [],
                                "nothing of the scan could be compared with the CAD model")
    off = dev.offset.astype(np.float64)
    fd = (off[Fc[:, 0]] + off[Fc[:, 1]] + off[Fc[:, 2]]) / 3.0
    above, below = fd > tol, fd < -tol
    share_above, share_below = float(a[above].sum()) / total, float(a[below].sum()) / total
    share_within = max(0.0, 1.0 - share_above - share_below)
    S = dev.sigma
    fs = None
    share_unc = None
    if S is not None:
        fs = (S[Fc[:, 0]] + S[Fc[:, 1]] + S[Fc[:, 2]]) / 3.0
        share_unc = float(a[k * fs > tol].sum()) / total
    med = float(_wquantile(fd, a, 0.5))
    p05, p95 = (float(x) for x in _wquantile(fd, a, [0.05, 0.95]))
    stats = {"mean_mm": float(np.average(fd, weights=a)), "rms_mm": float(np.sqrt(np.average(fd * fd, weights=a))),
             "median_mm": med, "robust_sigma_mm": 1.4826 * float(_wquantile(np.abs(fd - med), a, 0.5)),
             "p05_mm": p05, "p95_mm": p95, "abs_p95_mm": float(_wquantile(np.abs(fd), a, 0.95))}
    cv = sorted_unique(Fc)
    worst_above = float(np.max(off[cv]))
    worst_below = float(np.min(off[cv]))
    regions: list[DeviationRegion] = []
    n_regions = 0
    for side, mask in (("above", above), ("below", below)):
        if not mask.any():
            continue
        sel = np.flatnonzero(mask)
        n_grp, label = _face_groups(Fc[sel], len(dev.V))
        g_area = np.bincount(label, weights=a[sel], minlength=n_grp)
        listed = np.flatnonzero(g_area >= min_area_mm2)
        n_regions += len(listed)
        order = np.argsort(label, kind="stable")
        start = np.concatenate([[0], np.cumsum(np.bincount(label, minlength=n_grp))])
        for g in listed.tolist():
            loc = sel[order[start[g]:start[g + 1]]]  # into Fc
            Fg, ag, dg = Fc[loc], a[loc], fd[loc]
            cen = (rows(dev.V, Fg[:, 0]) + rows(dev.V, Fg[:, 1]) + rows(dev.V, Fg[:, 2])) / 3.0
            center = (cen * ag[:, None]).sum(axis=0) / ag.sum()
            verts = sorted_unique(Fg)
            P = rows(dev.V, verts)
            mean = float(np.average(dg, weights=ag))
            worst = float(off[verts].max() if side == "above" else off[verts].min())
            smed = exceeds = beyond = None
            if S is not None:
                sv = S[verts]
                sv = sv[np.isfinite(sv)]
                if len(sv):
                    smed = float(np.median(sv))
                    exceeds = bool(abs(mean) > k * smed)
                beyond = float(ag[np.abs(dg) > k * fs[loc]].sum() / ag.sum())
            regions.append(DeviationRegion(side=side, area_mm2=float(ag.sum()), share=float(ag.sum()) / total,
                                           center=[float(x) for x in center],
                                           extent_mm=float((P.max(axis=0) - P.min(axis=0)).max()),
                                           radius_mm=float(np.linalg.norm(P - center, axis=1).max()), n_faces=int(len(loc)),
                                           mean_mm=mean, worst_mm=worst, sigma_median_mm=smed, exceeds_uncertainty=exceeds,
                                           share_beyond_uncertainty=beyond, faces=np.sort(fidx[loc])))
    regions.sort(key=lambda r: -r.area_mm2)
    sentence = f"{100 * share_within:.0f} % within ± {tol:g} mm"
    if share_above or share_below:
        sentence += f" ({100 * share_above:.1f} % above, {100 * share_below:.1f} % below)"
    return ToleranceSummary(tol, float(k), total, share_within, share_above, share_below, share_unc, worst_above, worst_below,
                            stats, float(min_area_mm2), n_regions, regions[:max_regions], sentence)


# --------------------------------------------------------------------------- all three
@dataclass
class Comparison:
    """A scan against a reference at one tolerance: how it was aligned, the deviation, the answer."""

    reference: ReferenceModel
    alignment: ReferenceAlignment
    deviation: Deviation
    tolerance: ToleranceSummary

    @property
    def summary(self) -> str:
        dev = self.deviation
        out = f"{self.tolerance.sentence} ({self.alignment.describe()})"
        out += f"; {100 * dev.off_reference_share:.0f} % of the scan is not on the CAD model"
        if dev.uncovered_share is not None:
            out += f"; {100 * dev.uncovered_share:.0f} % of the CAD model was not scanned"
        return out + "."

    def to_dict(self, with_faces: bool = False) -> dict:
        return {"format_version": FORMAT_VERSION, "summary": self.summary, "reference": self.reference.to_dict(),
                "alignment": self.alignment.to_dict(), "deviation": self.deviation.to_dict(),
                "tolerance": self.tolerance.to_dict(with_faces)}


def compare_with_reference(scan: ScanSurface, ref: ReferenceModel, alignment: ReferenceAlignment, tolerance_mm: float,
                           *, k: float = 2.0, **deviation_kw: Any) -> Comparison:
    """:func:`deviation_from_reference` and :func:`tolerance_summary` in one call."""
    dev = deviation_from_reference(scan, ref, alignment, **deviation_kw)
    return Comparison(reference=ref, alignment=alignment, deviation=dev, tolerance=tolerance_summary(dev, tolerance_mm, k=k))
