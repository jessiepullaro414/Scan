"""A scan's kept features as the shapes a CAD file holds (FEATURE_PLAN C1).

:func:`cad_shapes` takes the feature records as ``features.json`` holds them
(:mod:`ohs.measure.features`: ``id``, ``name``, ``kind``, ``shape``, ``extent``) and gives back
finished shapes with every number a writer needs, in mm:

* a plane: a flat face (:class:`PlaneFace`), its point and normal as fitted, bounded by its
  extent's rectangle (four clean edges, the default), or with ``boundary="outline"`` by its
  outline's largest outer loop and the real holes in it, simplified (below);
* a cylinder: a cylindrical face over its measured span and arc (:class:`CylinderFace`);
* a hole: a circle on its plane, its centre, normal and diameter (:class:`HoleCircle`);
* a point (a feature of kind ``"point"`` with ``shape`` ``{"kind": "point", "point": [x, y, z]}``):
  :class:`PointMark`;
* with the part's frame (:class:`ohs.measure.frame.PartFrame`, or its stored dict), the part's
  axes (:class:`PartAxes`).

**In the part's frame.** With a frame every shape is turned and moved into it
(``PartFrame.fit_to_part`` for the shapes, the same rotation for the extents), so the CAD
program's origin and axes are the part's. Without one the shapes stay in the scan's own frame,
which is wherever the scanner started, and a note says so.

**The numbers are the shape dicts as given.** A later step (C3) puts approved nominal sizes into
the shapes before calling this; nothing here goes back to the mesh. The extent only says where on
the shape the region lies: a plane's boundary is projected onto the given plane, a cylinder's span
is measured from the given point along the given axis, with the given radius.

**No extent** (a fit kept before extents existed): the shape is still written, at a stated size
(a plane as a :data:`DEFAULT_PLANE_SIDE_MM` square round its fitted point, a cylinder as a full
round :data:`DEFAULT_CYLINDER_LENGTH_MM` long centred on it), with a note. A hole needs no extent.

**The outline** (:func:`simplify_outline`). A scan's plane outline holds tens of thousands of
points, stepped at the size of a mesh edge (about 1 mm at the default 1 mm voxels), in dozens of
pieces with hundreds of pits; written raw it would be an unusable face. What is kept, in order,
each step with a note when it leaves something out:

* the largest outer loop (a region in pieces has several). Where it passes through the same point
  twice (two parts of the region meeting at one mesh vertex) it is cut there and its largest
  piece kept;
* the holes inside it that are holes in the part, not pits of the scan: at least
  :data:`HOLE_MIN_SHARE` (3 %) of the outline's own size across (both as circles of the same
  area) and at least :data:`HOLE_MIN_DIAMETER_MM`, at most the :data:`HOLE_MAX_COUNT` (50)
  largest. On a scanned table (an outline 501 mm across) that is 15 mm: 49 of its 684 holes, the
  ball standing on it the largest (146 mm); the rest are pits of 2-15 mm. On a 200 x 120 mm plate
  it is 5.2 mm, so a Ø 6 mm hole stays;
* the loops simplified with Douglas-Peucker: every point of a loop stays within the tolerance of
  the simplified one, so the area changes by at most the tolerance times the loop's length. The
  default, :data:`OUTLINE_TOLERANCE_MM` = 0.5 mm, is half a mesh edge: the outline itself is only
  good to about a mesh edge (``ohs.measure.region``, "Extent"), so this takes out the stair steps
  and keeps the shape. A face holds at most :data:`OUTLINE_MAX_POINTS` (5,000) points: while the
  loops have more, the tolerance doubles, but never beyond :data:`OUTLINE_TOLERANCE_CAP` (4) times
  the default, 2 mm, a shape still true to two mesh edges; then the smallest holes are left out,
  and when the outer loop alone does not fit, the face is the rectangle. The scanned table needs
  1 mm (2,039 points on its outer loop, 4,607 in all), the plate 0.5 mm;
* a valid face: every loop simple (no edge crosses or comes within :data:`MIN_GAP_MM` of another,
  none folds back), every hole strictly inside the outer loop, no two holes crossing, touching or
  one inside the other (:func:`face_problems`). Douglas-Peucker can break that where a loop runs
  close to itself (a narrow slot, an inlet): there the loop's own points are put back until it
  holds (:func:`_simple_loop`). A hole that still crosses or touches something is left out; an
  outer loop that cannot be made simple gives the rectangle. Every face is checked before it is
  given out, the rectangle and the default square too.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

__all__ = ["PlaneFace", "CylinderFace", "HoleCircle", "PointMark", "PartAxes", "CadShapes", "cad_shapes",
           "simplify_loop", "simplify_outline", "loop_problem", "face_problems", "DEFAULT_PLANE_SIDE_MM",
           "DEFAULT_CYLINDER_LENGTH_MM", "OUTLINE_TOLERANCE_MM", "OUTLINE_MAX_POINTS", "OUTLINE_TOLERANCE_CAP",
           "HOLE_MIN_DIAMETER_MM", "HOLE_MIN_SHARE", "HOLE_MAX_COUNT", "MIN_GAP_MM"]

DEFAULT_PLANE_SIDE_MM = 20.0  # a plane with no extent: a square this wide round its fitted point
DEFAULT_CYLINDER_LENGTH_MM = 20.0  # a cylinder with no extent: a full round this long, centred on its point
OUTLINE_TOLERANCE_MM = 0.5  # the outline's simplification: half a mesh edge (module docstring)
OUTLINE_MAX_POINTS = 5000  #a face's loops hold at most this many points together ...
OUTLINE_TOLERANCE_CAP = 4.0  # ... the tolerance doubling for that up to this many times the default, no further
HOLE_MIN_DIAMETER_MM = 2.0  # a hole in an outline counts when its area is a circle at least this wide ...
HOLE_MIN_SHARE = 0.03  # ... and at least this share of the outline's own size (the same measure)
HOLE_MAX_COUNT = 50  # at most this many holes, the largest
MIN_GAP_MM = 0.001  # the loops of a face may not cross or come closer than this, nor any loop to itself
FULL_ROUND_DEG = 360.0 - 1e-9  # an arc this long is the whole round
AXES_MIN_MM = 10.0  # the part's axes: at least this long ...
AXES_SHARE = 0.25  # ... else this share of the shapes' largest size, rounded up to 5 mm


# --------------------------------------------------------------------------- the shapes
@dataclass
class PlaneFace:
    """A flat face: the plane through ``point`` with ``normal``; ``ref`` a unit direction in it.

    ``outer`` (N,3) is its boundary, counter-clockwise seen from the normal's side; ``holes`` loops
    inside it, clockwise. All points lie on the plane."""

    id: str
    name: str
    point: np.ndarray
    normal: np.ndarray
    ref: np.ndarray
    outer: np.ndarray
    holes: list = field(default_factory=list)
    note: str | None = None


@dataclass
class CylinderFace:
    """A cylindrical face of ``radius`` round the axis through ``point`` along ``axis``, from
    ``along[0]`` to ``along[1]`` mm along it, covering the arc from ``start_deg`` through
    ``arc_deg`` (360: the whole round), angles from ``zero`` turning right-handed about the axis."""

    id: str
    name: str
    point: np.ndarray
    axis: np.ndarray
    zero: np.ndarray
    radius: float
    along: tuple
    start_deg: float
    arc_deg: float
    note: str | None = None

    @property
    def full(self) -> bool:
        return self.arc_deg >= FULL_ROUND_DEG


@dataclass
class HoleCircle:
    """A hole's circle (its edge as fitted) and its axis, a line ``axis_half_mm`` each way from the centre."""

    id: str
    name: str
    center: np.ndarray
    normal: np.ndarray
    radius: float
    axis_half_mm: float
    note: str | None = None


@dataclass
class PointMark:
    id: str
    name: str
    point: np.ndarray
    note: str | None = None


@dataclass
class PartAxes:
    """The part's coordinate system: ``origin`` and its X, Y and Z directions (rows of ``axes``),
    drawn as lines ``length_mm`` long named ``names``."""

    origin: np.ndarray
    axes: np.ndarray
    length_mm: float
    names: tuple = ("X axis", "Y axis", "Z axis")
    origin_name: str = "Part origin"


@dataclass
class CadShapes:
    """What :func:`cad_shapes` gives: the shapes in the order of the features, the part's axes
    (None without a frame), whether they are in the part's frame, and the notes (each shape's,
    then the export's own)."""

    shapes: list
    axes: PartAxes | None
    in_part_frame: bool
    notes: list


# --------------------------------------------------------------------------- small helpers
def _vec(x, what: str) -> np.ndarray:
    try:
        a = np.asarray(x, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError):
        a = np.zeros(0)
    if a.shape != (3,) or not np.all(np.isfinite(a)):
        raise ValueError(f"{what} must be three finite numbers, got {x!r}")
    return a


def _unit(x, what: str) -> np.ndarray:
    a = _vec(x, what)
    n = float(np.linalg.norm(a))
    if n == 0.0:
        raise ValueError(f"{what} must not be zero")
    return a / n


def _positive(x, what: str) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        v = math.nan
    if not (math.isfinite(v) and v > 0):
        raise ValueError(f"{what} must be a positive number, got {x!r}")
    return v


def _square_to(d: np.ndarray, hint=None) -> np.ndarray:
    """A unit vector square to the unit ``d``: ``hint`` with its part along ``d`` taken off when it
    is usable, else from the coordinate axis least along ``d``."""
    if hint is not None:
        h = np.asarray(hint, dtype=np.float64)
        h = h - float(h @ d) * d
        n = float(np.linalg.norm(h))
        if n > 1e-9:
            return h / n
    e = np.zeros(3)
    e[int(np.argmin(np.abs(d)))] = 1.0
    h = e - float(e @ d) * d
    return h / np.linalg.norm(h)


def _signed_area(xy: np.ndarray) -> float:
    x, y = xy[:, 0], xy[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - y * np.roll(x, -1)))


def _inside(pts: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Which 2D points lie inside the polygon (even-odd rule)."""
    x0, y0 = poly[:, 0], poly[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    px, py = pts[:, 0:1], pts[:, 1:2]
    crosses = ((y0 > py) != (y1 > py)) & (px < x0 + (py - y0) * (x1 - x0) / np.where(y1 == y0, 1.0, y1 - y0))
    return crosses.sum(axis=1) % 2 == 1


# --------------------------------------------------------------------------- outline simplification
def _dp_keep(P: np.ndarray, tolerance_mm: float) -> np.ndarray:
    """Which points of the closed loop ``P`` (N,2, N > 3, no repeated last point) Douglas-Peucker
    keeps at ``tolerance_mm``: a mask, the first point always among them."""
    n = len(P)
    keep = np.zeros(n + 1, dtype=bool)  # index n is the first point again, closing the loop
    # split the loop at its first point and the point farthest from it, then simplify both chains
    far = int(np.argmax(np.linalg.norm(P - P[0], axis=1)))
    if far == 0:
        keep[0] = True
        return keep[:n]
    keep[[0, far, n]] = True
    Q = np.vstack([P, P[:1]])
    stack = [(0, far), (far, n)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = Q[b] - Q[a]
        w = Q[a + 1: b] - Q[a]
        L2 = float(seg[0] * seg[0] + seg[1] * seg[1])
        if L2 == 0.0:
            d2 = w[:, 0] * w[:, 0] + w[:, 1] * w[:, 1]
        else:
            t = (w @ seg) / L2
            np.clip(t, 0.0, 1.0, out=t)
            ex, ey = w[:, 0] - t * seg[0], w[:, 1] - t * seg[1]
            d2 = ex * ex + ey * ey
        k = int(np.argmax(d2))
        if d2[k] > tolerance_mm * tolerance_mm:
            m = a + 1 + k
            keep[m] = True
            stack += [(a, m), (m, b)]
    keep = keep[:n]
    if np.count_nonzero(keep) < 3:  # a sliver: keep the farthest point from the line of the two as well
        rest = np.flatnonzero(~keep)
        seg = P[far] - P[0]
        w = P[rest] - P[0]
        keep[rest[int(np.argmax(np.abs(w[:, 0] * seg[1] - w[:, 1] * seg[0])))]] = True
    return keep


def simplify_loop(xy, tolerance_mm: float) -> np.ndarray:
    """A closed 2D loop (N,2) simplified by Douglas-Peucker: every point of the loop stays within
    ``tolerance_mm`` of the simplified loop, which keeps a subset of its points, in order. A
    repeated last point (the first again) is dropped first. At least 3 points are kept. The
    answer may cross or touch itself where the loop runs close to itself; :func:`simplify_outline`
    repairs that."""
    P = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    if len(P) > 1 and np.array_equal(P[0], P[-1]):
        P = P[:-1]
    if len(P) <= 3:
        return P.copy()
    return P[_dp_keep(P, tolerance_mm)]


# --------------------------------------------------------------------------- is a face valid?
def _point_segment(P: np.ndarray, A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """Distances from points ``P`` (..., 2) to segments ``A``-``B`` (..., 2), broadcast."""
    d = B - A
    L2 = np.einsum("...k,...k->...", d, d)
    t = np.clip(np.einsum("...k,...k->...", P - A, d) / np.where(L2 > 0, L2, 1.0), 0.0, 1.0)
    q = A + t[..., None] * d - P
    return np.sqrt(np.einsum("...k,...k->...", q, q))


def _pairwise(A0, A1, B0, B1, gap: float) -> np.ndarray:
    """For matched segments (segment k of A against segment k of B, each (k, 2)): whether they
    cross or come within ``gap`` of each other."""
    def side(p, q, r):  # the sign of the turn p -> q -> r
        return np.sign((q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0]))

    cross = (side(A0, A1, B0) * side(A0, A1, B1) < 0) & (side(B0, B1, A0) * side(B0, B1, A1) < 0)
    near = np.minimum(np.minimum(_point_segment(B0, A0, A1), _point_segment(B1, A0, A1)),
                      np.minimum(_point_segment(A0, B0, B1), _point_segment(A1, B0, B1)))
    return cross | (near < gap)


def _close_pairs(A0, A1, B0, B1, gap: float) -> np.ndarray:
    """:func:`_pairwise` for every segment of A (n,2) against every one of B (m,2): an (n, m) table."""
    return _pairwise(A0[:, None], A1[:, None], B0[None], B1[None], gap)


def _segments(loop: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return loop, np.roll(loop, -1, axis=0)


def _bad_edges(P: np.ndarray, gap: float) -> tuple[np.ndarray, str | None]:
    """Which edges of the closed loop ``P`` (N,2, N >= 3; edge i runs from point i to i + 1) are
    shorter than ``gap``, fold back onto their neighbour, or cross or come within ``gap`` of an
    edge further round the loop; and the first of those reasons in words (None when none is)."""
    n = len(P)
    A, B = _segments(P)
    bad = np.linalg.norm(B - A, axis=1) < gap
    reason = "two of its points coincide" if bad.any() else None
    # a neighbour folding back: the next edge's end near this edge, or this edge's start near the next
    C = np.roll(P, -2, axis=0)
    fold = (_point_segment(C, A, B) < gap) | (_point_segment(A, B, C) < gap)
    if fold.any():
        bad |= fold | np.roll(fold, 1)
        reason = reason or "it folds back on itself"
    if n == 3:
        return bad, reason
    # every other pair of edges, in blocks, tested only where their boxes meet
    lo, hi = np.minimum(A, B) - gap, np.maximum(A, B) + gap
    idx = np.arange(n)
    for s in range(0, n, 512):
        rows = idx[s: s + 512]
        box = ((lo[rows, None, 0] <= hi[None, :, 0]) & (lo[None, :, 0] <= hi[rows, None, 0])
               & (lo[rows, None, 1] <= hi[None, :, 1]) & (lo[None, :, 1] <= hi[rows, None, 1]))
        apart = (idx[None, :] - rows[:, None]) % n
        box &= (apart > 1) & (apart < n - 1)
        r, c = np.nonzero(box)
        if len(r):
            hit = _pairwise(A[rows[r]], B[rows[r]], A[c], B[c], gap)
            if hit.any():
                bad[rows[r[hit]]] = True
                bad[c[hit]] = True
                reason = reason or "it crosses or touches itself"
    return bad, reason


def loop_problem(loop, gap_mm: float = MIN_GAP_MM) -> str | None:
    """Why a closed 2D loop (N,2) cannot bound a face, or None when it can: it needs 3 points or
    more, edges of at least ``gap_mm``, and no edge may cross or come within ``gap_mm`` of another
    (nor fold back onto its neighbour)."""
    P = np.asarray(loop, dtype=np.float64).reshape(-1, 2)
    if len(P) < 3:
        return "it has fewer than 3 points"
    return _bad_edges(P, gap_mm)[1]


def _loops_meet(P: np.ndarray, Q: np.ndarray, gap: float) -> bool:
    """Whether two closed loops cross or come within ``gap`` of each other anywhere."""
    lo, hi = Q.min(axis=0) - gap, Q.max(axis=0) + gap
    A, B = _segments(P)
    near_p = np.all((np.maximum(A, B) >= lo) & (np.minimum(A, B) <= hi), axis=1)
    if not near_p.any():
        return False
    C, D = _segments(Q)
    lo, hi = P.min(axis=0) - gap, P.max(axis=0) + gap
    near_q = np.all((np.maximum(C, D) >= lo) & (np.minimum(C, D) <= hi), axis=1)
    if not near_q.any():
        return False
    A, B, C, D = A[near_p], B[near_p], C[near_q], D[near_q]
    for s in range(0, len(A), 256):
        if _close_pairs(A[s: s + 256], B[s: s + 256], C, D, gap).any():
            return True
    return False


def face_problems(outer, holes, gap_mm: float = MIN_GAP_MM) -> list[str]:
    """Why a face bounded by ``outer`` (counter-clockwise) and ``holes`` (clockwise) would not be
    valid, in words; empty when it is. Each loop must be simple (:func:`loop_problem`), each hole
    strictly inside the outer loop, and no two loops may cross, touch or lie one inside the other
    (apart from the holes in the outer one), all with ``gap_mm`` to spare."""
    out = []
    outer = np.asarray(outer, dtype=np.float64).reshape(-1, 2)
    why = loop_problem(outer, gap_mm)
    if why:
        out.append(f"the outer loop: {why}")
    elif _signed_area(outer) <= 0:
        out.append("the outer loop does not turn counter-clockwise")
    holes = [np.asarray(h, dtype=np.float64).reshape(-1, 2) for h in holes]
    boxes = [_box(h) for h in holes]
    outer_box = _box(outer)
    for i, h in enumerate(holes):
        why = loop_problem(h, gap_mm)
        if why:
            out.append(f"hole {i + 1}: {why}")
            continue
        if _signed_area(h) >= 0:
            out.append(f"hole {i + 1} does not turn clockwise")
        if _loops_meet(h, outer, gap_mm):
            out.append(f"hole {i + 1} crosses or touches the outer loop")
        elif not _holds(outer, outer_box, h[0]):
            out.append(f"hole {i + 1} lies outside the outer loop")
        for j in range(i):
            g = holes[j]
            if _loops_meet(h, g, gap_mm):
                out.append(f"holes {j + 1} and {i + 1} cross or touch")
            elif _holds(g, boxes[j], h[0]) or _holds(h, boxes[i], g[0]):
                out.append(f"holes {j + 1} and {i + 1} lie one inside the other")
    return out


# --------------------------------------------------------------------------- the outline as a face
def _split_pinches(loop: np.ndarray) -> tuple[np.ndarray, int]:
    """A loop that passes through the same point more than once (where a region's border pinches,
    two of its parts meeting at one mesh vertex) cut there into pieces; returns ``(the piece of
    the largest area, turning as the loop does, how many pinches there were)``."""
    if len(loop) > 1 and np.array_equal(loop[0], loop[-1]):
        loop = loop[:-1]
    keys = [tuple(p) for p in loop.tolist()]
    stack: list[int] = []
    where: dict = {}
    pieces = []
    for i, k in enumerate(keys):
        if k in where:  # back at a point of the path: the stretch since then is a piece of its own
            j = where[k]
            piece = stack[j:]
            for m in piece[1:]:
                del where[keys[m]]
            del stack[j + 1:]
            pieces.append(piece)
        else:
            where[k] = len(stack)
            stack.append(i)
    pieces.append(stack)
    if len(pieces) == 1:
        return loop, 0
    sign = 1.0 if _signed_area(loop) >= 0 else -1.0
    best = max((loop[p] for p in pieces if len(p) >= 3), key=lambda x: sign * _signed_area(x))
    return best, len(pieces) - 1


def _without_doubles(P: np.ndarray, gap: float) -> np.ndarray:
    """The closed loop ``P`` without the points that lie within ``gap`` of the point before them."""
    step = np.linalg.norm(P - np.roll(P, 1, axis=0), axis=1)
    if len(P) < 2 or not np.any(step < gap):
        return P
    keep = [0]
    g2 = gap * gap
    xs, ys = P[:, 0].tolist(), P[:, 1].tolist()
    lx, ly = xs[0], ys[0]
    for i in range(1, len(P)):
        if (xs[i] - lx) ** 2 + (ys[i] - ly) ** 2 >= g2:
            keep.append(i)
            lx, ly = xs[i], ys[i]
    if len(keep) > 1 and (lx - xs[0]) ** 2 + (ly - ys[0]) ** 2 < g2:
        keep.pop()  # the last point meets the first
    return P[keep]


def _dp_mask(R: np.ndarray, tol: float) -> np.ndarray:
    return _dp_keep(R, tol) if len(R) > 3 else np.ones(len(R), dtype=bool)


def _simple_loop(R: np.ndarray, keep: np.ndarray, gap: float) -> np.ndarray | None:
    """The loop ``R`` (its points closer than ``gap`` merged, :func:`_without_doubles`) at the points
    ``keep`` (Douglas-Peucker's), made simple; None when it cannot be.

    Douglas-Peucker can make a loop cross or touch itself where the loop runs close to itself (a
    narrow inlet, the two sides of a slot). Where it does, the loop's own points of those stretches
    are put back, and that is repeated until no edge is bad: a stretch put back whole is the
    measured border itself. So the answer is the loop at the tolerance everywhere but at those
    places, where it follows the loop more closely. None when the loop itself crosses or touches
    itself."""
    if len(R) < 3:
        return None
    keep = keep.copy()
    for _ in range(64):
        S = R[keep]
        bad = _bad_edges(S, gap)[0] if len(S) >= 3 else np.ones(len(S), dtype=bool)
        if not bad.any():
            return S
        idx = np.flatnonzero(keep)
        before = len(idx)
        for k in np.flatnonzero(bad).tolist():
            if k + 1 < len(idx):
                keep[idx[k]: idx[k + 1]] = True
            else:  # the last edge, back to the first point
                keep[idx[k]:] = True
        if int(np.count_nonzero(keep)) == before:
            return None  # the bad edges are the loop's own
    return None


def _holes(n: int) -> str:
    return "1 hole was" if n == 1 else f"{n} holes were"


def _box(P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return P.min(axis=0), P.max(axis=0)


def _holds(outer: np.ndarray, box, p: np.ndarray) -> bool:
    """Whether the loop ``outer`` (its bounding ``box``) holds the point ``p`` (2,)."""
    lo, hi = box
    return bool(np.all(p >= lo) and np.all(p <= hi) and _inside(p[None], outer)[0])


def simplify_outline(outer_loops, holes, tolerance_mm: float = OUTLINE_TOLERANCE_MM,
                     max_points: int = OUTLINE_MAX_POINTS, min_hole_mm: float = HOLE_MIN_DIAMETER_MM,
                     gap_mm: float = MIN_GAP_MM) -> tuple[np.ndarray | None, list, float, list]:
    """A plane's outline (2D loops) as one valid face's boundary (module docstring, "The outline").

    ``outer_loops`` and ``holes`` as :func:`ohs.measure.region.region_extent` gives them (2D).
    Returns ``(outer (N,2) counter-clockwise, holes (each clockwise, largest first), the tolerance
    used, notes)``. ``outer`` is None, with a note saying why, when no valid face can be made from
    the outline within the tolerance's cap and ``max_points`` (the caller then uses the
    rectangle). Whatever is returned has passed :func:`face_problems`."""
    loops = [np.asarray(x, dtype=np.float64).reshape(-1, 2) for x in outer_loops]
    loops = [x for x in loops if len(x) >= 3]
    if not loops:
        raise ValueError("the outline has no outer loop")
    areas = [abs(_signed_area(x)) for x in loops]
    outer = loops[int(np.argmax(areas))]
    if _signed_area(outer) < 0:
        outer = outer[::-1]
    notes: list[str] = []
    if len(loops) > 1:
        notes.append(f"only the largest of its {len(loops)} outer loops is kept (the region is in pieces)")
    outer, pinches = _split_pinches(outer)
    if pinches:
        notes.append(f"its outer loop touched itself at {pinches} points (parts of the region meeting at a corner): "
                     "cut there, and its largest piece kept")
    # the holes that count: large enough to be a hole in the part, not a pit of the scan, the largest first
    size = 2.0 * math.sqrt(max(areas) / math.pi)
    smallest = max(float(min_hole_mm), HOLE_MIN_SHARE * size)
    holes = [np.asarray(h, dtype=np.float64).reshape(-1, 2) for h in holes]
    holes = [h for h in holes if len(h) >= 3]
    sized = sorted(((abs(_signed_area(h)), k) for k, h in enumerate(holes)), key=lambda x: (-x[0], x[1]))
    big = [holes[k] for a, k in sized if 2.0 * math.sqrt(a / math.pi) >= smallest]
    n_pits = len(holes) - len(big)
    if big:  # inside the outer loop (most of up to 16 of each one's points), all at once
        probes = [h[:: max(1, len(h) // 16)] for h in big]
        votes = np.add.reduceat(_inside(np.concatenate(probes), outer).astype(np.int64),
                                np.cumsum([0] + [len(p) for p in probes[:-1]]))
        n_out = len(big)
        big = [h for h, v, p in zip(big, votes, probes) if v * 2 > len(p)]
        n_pits += n_out - len(big)
    n_many = max(0, len(big) - HOLE_MAX_COUNT)
    cand = [_split_pinches(h if _signed_area(h) < 0 else h[::-1])[0] for h in big[:HOLE_MAX_COUNT]]
    if n_pits:
        notes.append(f"{n_pits} of its {len(holes)} holes are smaller than {smallest:.3g} mm across "
                     f"({HOLE_MIN_SHARE:.0%} of the outline's size, and at least {min_hole_mm:g} mm), or outside "
                     "its largest piece: left out as pits of the scan")
    if n_many:
        notes.append(f"only its {HOLE_MAX_COUNT} largest holes are kept ({_holes(n_many)} left out)")
    # the tolerance: the default, doubled while the loops would hold more than max_points, up to its cap
    R_out = _without_doubles(outer, gap_mm)
    R_holes = [_without_doubles(h, gap_mm) for h in cand]
    cap = OUTLINE_TOLERANCE_CAP * float(tolerance_mm)
    tol = float(tolerance_mm)
    while True:
        k_out = _dp_mask(R_out, tol)
        k_holes = [_dp_mask(R, tol) for R in R_holes]
        total = int(np.count_nonzero(k_out)) + sum(int(np.count_nonzero(k)) for k in k_holes)
        if total <= max_points or tol * 2.0 > cap * (1 + 1e-9):
            break
        tol *= 2.0
    if tol != tolerance_mm:
        notes.append(f"the outline was simplified to {tol:g} mm (not {tolerance_mm:g} mm) to keep within "
                     f"{max_points} points")
    out = _simple_loop(R_out, k_out, gap_mm)
    if out is None:
        notes.append("its outer loop crosses or touches itself where it was measured")
        return None, [], tol, notes
    if len(out) > max_points:
        notes.append(f"its outer loop needs {len(out)} points at {tol:g} mm, more than {max_points}")
        return None, [], tol, notes
    # each hole, the largest first, while the points last: simple, strictly inside the outer loop,
    # and apart from the holes kept before it
    out_box = _box(out)
    good, boxes, n_bad, n_budget, total = [], [], 0, 0, len(out)
    for R, k in zip(R_holes, k_holes):
        if total + int(np.count_nonzero(k)) > max_points:
            n_budget += 1
            continue
        h = _simple_loop(R, k, gap_mm)
        if h is None or total + len(h) > max_points:
            n_bad += h is None
            n_budget += h is not None
            continue
        hb = _box(h)
        if (_loops_meet(h, out, gap_mm) or not _holds(out, out_box, h[0])
                or any(_loops_meet(h, g, gap_mm) or _holds(g, gb, h[0]) or _holds(h, hb, g[0])
                       for g, gb in zip(good, boxes))):
            n_bad += 1
            continue
        good.append(h)
        boxes.append(hb)
        total += len(h)
    if n_budget:
        notes.append(f"{_holes(n_budget)} left out, the smallest, to keep within {max_points} points")
    if n_bad:
        notes.append(f"{_holes(n_bad)} left out because it crosses or touches itself, the outer loop or another "
                     "hole" if n_bad == 1 else
                     f"{_holes(n_bad)} left out because they cross or touch themselves, the outer loop or another "
                     "hole")
    problems = face_problems(out, good, gap_mm)
    if problems:  # not expected after the steps above; never written unchecked
        notes.append("the simplified outline is not a valid face (" + "; ".join(problems) + ")")
        return None, [], tol, notes
    return out, good, tol, notes


# --------------------------------------------------------------------------- the frame
def _as_frame(frame):
    if frame is None:
        return None
    from ohs.measure.frame import PartFrame

    return frame if isinstance(frame, PartFrame) else PartFrame.from_dict(frame)


class _Mover:
    """Points and directions into the output frame (the part's, or left as they are)."""

    def __init__(self, frame):
        self.frame = frame

    def point(self, p) -> np.ndarray:
        p = np.asarray(p, dtype=np.float64)
        return p if self.frame is None else self.frame.points_to_part(p)

    def direction(self, d) -> np.ndarray:
        d = np.asarray(d, dtype=np.float64)
        return d if self.frame is None else self.frame.directions_to_part(d)

    def shape(self, s: dict) -> dict:
        return dict(s) if self.frame is None else self.frame.fit_to_part(s)


# --------------------------------------------------------------------------- each kind
def _plane(f: dict, shape: dict, extent, outline, mv: _Mover, boundary: str, tolerance_mm: float,
           max_points: int, notes: list) -> PlaneFace:
    name = f["name"]
    p = _vec(shape.get("point"), f"{name}: the plane's point")
    n = _unit(shape.get("normal"), f"{name}: the plane's normal")
    hint = None if not extent else mv.direction(_vec(extent.get("u"), f"{name}: the extent's u"))
    ref = _square_to(n, hint)
    side = np.cross(n, ref)

    def flat(P3):  # 3D points onto the plane, as 2D in (ref, side)
        Q = np.asarray(P3, dtype=np.float64).reshape(-1, 3) - p
        return np.c_[Q @ ref, Q @ side]

    def lift(xy):
        return p + xy[:, :1] * ref + xy[:, 1:2] * side

    note = None
    outer2, holes2 = None, []
    if boundary == "outline" and extent:
        if outline and outline.get("kind") == "plane" and outline.get("outer"):
            o = _vec(outline["origin"], f"{name}: the outline's origin")
            u = _vec(outline["u"], f"{name}: the outline's u")
            v = _vec(outline["v"], f"{name}: the outline's v")

            def to3(loop):
                xy = np.asarray(loop, dtype=np.float64).reshape(-1, 2)
                return mv.point(o + xy[:, :1] * u + xy[:, 1:2] * v)

            outer2, holes2, _, more = simplify_outline([flat(to3(x)) for x in outline["outer"]],
                                                       [flat(to3(x)) for x in outline.get("holes", [])],
                                                       tolerance_mm, max_points)
            notes.extend(f"{name}: {m}" for m in more)
            if outer2 is None:
                notes.append(f"{name}: no valid face could be made from its outline: written with its rectangle")
        else:
            notes.append(f"{name}: its outline was asked for but is not there: written with its rectangle")
    if outer2 is None and extent:
        rect = extent.get("rectangle") or {}
        c = mv.point(_vec(rect.get("center"), f"{name}: the rectangle's center"))
        axes = rect.get("axes") or []
        half = rect.get("half_sizes_mm") or []
        if len(axes) == 2 and len(half) == 2 and min(float(h) for h in half) > 0:
            a0, a1 = (mv.direction(_vec(a, f"{name}: the rectangle's axes")) for a in axes)
            h0, h1 = float(half[0]), float(half[1])
            outer2 = flat(np.array([c - h0 * a0 - h1 * a1, c + h0 * a0 - h1 * a1, c + h0 * a0 + h1 * a1,
                                    c - h0 * a0 + h1 * a1]))
            if _signed_area(outer2) < 0:
                outer2 = outer2[::-1]
            if face_problems(outer2, []):  # a sliver, or a rectangle seen edge-on from this plane
                outer2 = None
        if outer2 is None:
            note = (f"{name}: its measured rectangle has no area: written as a {DEFAULT_PLANE_SIDE_MM:g} mm square "
                    "round its fitted point")
    if outer2 is None:
        if not extent:
            note = (f"{name}: kept with no measured size (before extents existed): written as a "
                    f"{DEFAULT_PLANE_SIDE_MM:g} mm square round its fitted point; fit it again for its real size")
        h = DEFAULT_PLANE_SIDE_MM / 2.0
        outer2 = np.array([[-h, -h], [h, -h], [h, h], [-h, h]])
    if _signed_area(outer2) < 0:
        outer2 = outer2[::-1]
    holes2 = [h if _signed_area(h) < 0 else h[::-1] for h in holes2]
    problems = face_problems(outer2, holes2)
    if problems:  # never write a face that has not passed the check
        raise ValueError(f"{name}: the face would not be valid: {'; '.join(problems)}")
    if note:
        notes.append(note)
    return PlaneFace(id=f["id"], name=name, point=p, normal=n, ref=ref, outer=lift(outer2),
                     holes=[lift(h) for h in holes2], note=note)


def _cylinder(f: dict, shape: dict, extent, mv: _Mover, notes: list) -> CylinderFace:
    name = f["name"]
    p = _vec(shape.get("point"), f"{name}: the cylinder's point")
    a = _unit(shape.get("axis"), f"{name}: the cylinder's axis")
    r = _positive(shape.get("radius_mm"), f"{name}: the cylinder's radius_mm")
    note = None
    if extent:
        lo, hi = (float(x) for x in extent.get("along_mm"))
        zero = _square_to(a, mv.direction(_vec(extent.get("zero"), f"{name}: the extent's zero")))
        start, arc = float(extent.get("start_deg", 0.0)), float(extent.get("arc_deg", 360.0))
        if not (math.isfinite(lo) and math.isfinite(hi) and hi > lo):
            lo = hi = None
            note = (f"{name}: its measured span has no length: written {DEFAULT_CYLINDER_LENGTH_MM:g} mm long, "
                    "centred on its fitted point")
        if not (math.isfinite(start) and math.isfinite(arc) and arc > 0):
            start, arc = 0.0, 360.0
            note = f"{name}: its measured arc is empty: written as the whole round"
    else:
        lo = hi = None
        zero, start, arc = _square_to(a), 0.0, 360.0
        note = (f"{name}: kept with no measured size (before extents existed): written as a full round "
                f"{DEFAULT_CYLINDER_LENGTH_MM:g} mm long, centred on its fitted point; fit it again for its real size")
    if lo is None:
        lo, hi = -DEFAULT_CYLINDER_LENGTH_MM / 2.0, DEFAULT_CYLINDER_LENGTH_MM / 2.0
    if arc >= FULL_ROUND_DEG:
        arc = 360.0
    start %= 360.0
    if note:
        notes.append(note)
    return CylinderFace(id=f["id"], name=name, point=p, axis=a, zero=zero, radius=r, along=(lo, hi),
                        start_deg=start, arc_deg=arc, note=note)


def _hole(f: dict, shape: dict) -> HoleCircle:
    name = f["name"]
    c = _vec(shape.get("center"), f"{name}: the hole's center")
    n = _unit(shape.get("normal"), f"{name}: the hole's normal")
    r = _positive(shape.get("radius_mm"), f"{name}: the hole's radius_mm")
    return HoleCircle(id=f["id"], name=name, center=c, normal=n, radius=r, axis_half_mm=r)


def _axes_length(shapes: list) -> float:
    pts = [np.zeros((1, 3))]
    for s in shapes:
        if isinstance(s, PlaneFace):
            pts.append(s.outer)
        elif isinstance(s, CylinderFace):
            pts.append(np.array([s.point + s.along[0] * s.axis, s.point + s.along[1] * s.axis]))
        elif isinstance(s, HoleCircle):
            pts.append(s.center[None])
        elif isinstance(s, PointMark):
            pts.append(s.point[None])
    P = np.concatenate(pts)
    size = float(np.max(P.max(axis=0) - P.min(axis=0)))
    return max(AXES_MIN_MM, 5.0 * math.ceil(AXES_SHARE * size / 5.0))


# --------------------------------------------------------------------------- all of them
def cad_shapes(features, outlines=None, frame=None, *, boundary: str = "rectangle",
               tolerance_mm: float = OUTLINE_TOLERANCE_MM, max_points: int = OUTLINE_MAX_POINTS,
               axis_length_mm: float | None = None) -> CadShapes:
    """The kept features as shapes to export (module docstring).

    ``features``: the records of ``features.json`` (``id``, ``name``, ``kind``, ``shape``,
    ``extent``; other keys are not used), in the order to write them. ``outlines``: a feature id
    -> its outline as :func:`ohs.measure.features.load_outline` gives it, used with
    ``boundary="outline"`` (a plane without one is written with its rectangle, with a note).
    ``frame``: the part's frame (a :class:`~ohs.measure.frame.PartFrame` or its stored dict) or
    None. ``tolerance_mm`` and ``max_points``: the outline's simplification. ``axis_length_mm``:
    the part's axes' length (default: a quarter of the shapes' largest size, at least 10 mm).

    Raises ValueError with a plain message for a kind it does not know or numbers that are missing
    or not finite."""
    if boundary not in ("rectangle", "outline"):
        raise ValueError(f"boundary must be 'rectangle' or 'outline', got {boundary!r}")
    pf = _as_frame(frame)
    mv = _Mover(pf)
    outlines = outlines or {}
    notes: list[str] = []
    out = []
    for i, f in enumerate(features):
        if not isinstance(f, dict) or not isinstance(f.get("shape"), dict):
            raise ValueError(f"features[{i}] must be a record with a shape")
        f = {**f, "id": str(f.get("id", f"feature-{i + 1}")), "name": str(f.get("name") or f.get("id"))}
        kind = f.get("kind")
        shape = mv.shape(f["shape"])
        extent = f.get("extent")
        if kind == "plane":
            out.append(_plane(f, shape, extent, outlines.get(f["id"]), mv, boundary, tolerance_mm, max_points, notes))
        elif kind == "cylinder":
            out.append(_cylinder(f, shape, extent, mv, notes))
        elif kind == "hole":
            out.append(_hole(f, shape))
        elif kind == "point":
            out.append(PointMark(id=f["id"], name=f["name"], point=_vec(shape.get("point"), f"{f['name']}: the point")))
        else:
            raise ValueError(f"features[{i}]: kind must be plane, cylinder, hole or point, got {kind!r}")
    axes = None
    if pf is not None:
        length = float(axis_length_mm) if axis_length_mm is not None else _axes_length(out)
        axes = PartAxes(origin=np.zeros(3), axes=np.eye(3), length_mm=length)
    else:
        notes.append("the part's axes are not set: the shapes are in the scan's own axes (wherever the scanner "
                     "started), and no part axes are written")
    return CadShapes(shapes=out, axes=axes, in_part_frame=pf is not None, notes=notes)
