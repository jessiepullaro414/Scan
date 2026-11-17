"""One click on the mesh: grow the region that matches a shape, and fit it (M5, the viewer's Fit tool).

:func:`fit_at` takes the clicked face and point, the kind ("plane", "cylinder" or "hole"), each
vertex's sigma and each face's *filled* flag, and returns a :class:`RegionFit`: the shape from
:mod:`ohs.measure.fit` with its ± values, the region's faces (for the highlight) and plain-language
warnings. Mesh ``(V, F)`` as in :mod:`ohs.mesh.topology`, in mm.

**Measured faces.** A face is left out (never in a region, never fitted) when it is flagged filled
(made by hole filling) or one of its vertices has no sigma (NaN, ``<= 0``, or
:data:`ohs.mesh.confidence.SIGMA_NONE_MM` and above, the mark of a filled vertex). A region that
touches such faces is warned about.

**Plane and cylinder: growing.** From the clicked face a *seed* grows over faces sharing a vertex
whose normals stay within :data:`SEED_ANGLE_DEG` of the normal at the click (so it does not cross an
edge), until it has :data:`SEED_POINTS` points. A cylinder seed starts from the vertex normals
(``fit_cylinder(..., normals=...)``); while it does not fix a cylinder clearly (its radius must be
:data:`SEED_ROUNDNESS` sigma: with the axis free, a 3σ curvature turns up in the noise of a flat
patch) it doubles, up to :data:`SEED_TRIES` times. Where the sigma is pessimistic (the seed's
points scatter less than their sigma says, as on a scan's small round features), a seed that bends
through :data:`SEED_ARC_DEG` of the round or more is judged by its scatter instead: such an arc is
not noise (on simulated scans, flat faces' seeds cover up to 36 degrees of the cylinder fitted to
them, real cylinders' 60 and more). The seed keeps only its faces that agree with
its own fit (and the clicked face). Then the region grows over faces sharing a vertex: a face
joins when

* its three vertices lie within the *tolerance* of the current fit (the plane, or the cylinder's
  surface), and
* its normal agrees with the fit's there (plane: the plane's normal; cylinder: the radial
  direction, outward for a boss, inward for a bore, as the seed says) within the *angle*.

The fit is redone on the region's vertices (with their sigma; at most :data:`GROW_FIT_POINTS` of
them, the same ones as it grows, while growing) each time the region has grown fourfold
(:data:`REFIT_GROWTH`) and when nothing more joins; the faces turned down are then tried again with
the new fit, until a round adds less than :data:`ROUND_GROWTH` of the region. A last fit on all
the region's vertices is the answer.
The tolerance and the angle are fixed at the seed: a region must not loosen its own test as it
takes in worse points.

**Weights.** A plane's fit weighs each vertex by ``1 / sigma^2``. A cylinder's weighs all the
vertices of its region the same, and uses their median sigma for the ± values: a scan's sigma
holds bias terms (curvature, the mesh border, gaps) that are large and very uneven on a small
round feature, and weighted by them the fit was left to a narrow band of the wall. On the
simulated bracket's 14 mm bosses that doubled the radius error with tracking and tilted the
axis by half as much again (``VALIDATION.md``, "Fitted shapes against the truth").

**The ± values.** A fused scan's errors come in patches: on simulated scans the residuals of
vertices 1-2 mm apart correlate 0.5-0.7, and :mod:`ohs.measure.fit` takes the points as
independent, so its ± were several times too small. The region measures the correlation of its
own residuals (the distances of its points from the answer) by distance, in bins of
:data:`CORR_BIN_EDGES` of the mesh's median edge length, and sums it over a point's neighbours:
through :data:`CORR_MIN_LAG_EDGES` edge lengths in any case (a mesh's own pattern makes the nearest
bins jump between near 0 and 0.6 at true poses), then while it stays above 0, up to
:data:`CORR_MAX_LAG_EDGES`. That is ``correlated_points``, ``N_c``: about the number of points in
a patch (1 when the points err independently). The fit's ± values are widened by ``sqrt(N_c)``,
as if the region had ``n / N_c`` independent points. Measured on at most :data:`CORR_POINTS` points
picked by id (so the same click gives the same answer), each of the first :data:`CORR_ANCHORS`
against all of them: 15-80 ms a click. On the simulated bracket and car panel (``VALIDATION.md``,
"The fits' ± on today's engine") the widened ± covers the spread from scan to scan at ±2σ for
62-100 % of the fits (cylinders 100 %), against 18-98 % before. What it cannot see is an error
shared by the whole region (a bias of the depth, the fusion or the tracking, the same on every
scan: small bosses read 0.15-0.25 mm small in radius); the ± is not the accuracy. A hole's circle is
not widened.

**A cylinder must stay the seed's cylinder.** The refits follow the region, so a region can drift
into another shape: from a shallow bump out over a flat, noisy plate, where the radius goes to
metres, or along a rim that curls ever tighter, where there is no one radius. When the answer's
radius is more than :data:`SEED_DRIFT` times the seed's, or less than the seed's divided by it,
the answer is an error that gives both radii: no guess. (On real cylinders of simulated scans the
two stay within 15 %.)

**The default tolerance** is ``max(3 x the seed's median sigma, 3 x the seed fit's RMS, 0.02 mm)``,
and with sigma each vertex may also stray 3 times its own sigma when that is more; but sigma counts
only while it agrees with the seed's measured scatter (median sigma at most :data:`SIGMA_TRUST`
times the RMS): a pessimistic sigma would let a plane run over half the part, so then the scatter
alone sets the tolerance and sigma is only the weight. A good point
strays from the true surface by about its sigma, and 3σ keeps 99.7 % of them, so noise does not
punch holes in the region (nor drop the poorly measured points first); the seed's own scatter
stands in when sigma is missing or optimistic; the floor keeps rounding on an exact (simulated)
mesh from stopping the growth. A given ``tolerance_mm`` is used as it is, for every vertex. **The
default angle** is 3 times the seed faces' RMS normal scatter about its fit (their noise), between
:data:`ANGLE_MIN_DEG` and :data:`ANGLE_MAX_DEG`.

What stops a region: at a sharp edge both tests; where a flat face bends away smoothly (a fillet,
a rounded edge, a gentle curve) the tolerance stops it once the surface has left the fit by that
much, about ``sqrt(2 R tol)`` into a bend of radius ``R``, and the angle once the surface has turned
by it, ``R sin(angle)``: with the default tolerance the distance usually binds first, with a loose
one the angle does. Over a surface curved everywhere, a plane takes the strip within the
tolerance (``sqrt(12 R tol)`` wide) and a warning says the points scatter more than their sigma.

**Hole.** The boundary loops near the click are found in a window around it (faces with a vertex
within :data:`HOLE_WINDOW_MM` of the click, filled and unmeasured faces left out, so a hole that was
filled is found again by its edge), with :func:`ohs.mesh.holes.find_holes`: outer borders are not
holes. The loop nearest the click counts if it comes within :data:`HOLE_REACH_MM`; a loop that
leaves the window widens it (doubling, up to the whole mesh). The plane comes from the faces in a
ring :data:`RING_MM` wide around the loop (``fit_plane`` with sigma), then ``fit_circle`` on the
loop's vertices with that normal. Warnings: an
edge with a gap round its centre (incomplete), an edge that is not round, a hole that borders
filled or unmeasured faces, few points on the edge. The edge's points are the mesh's own: on a
fused mesh, small holes come out rounded and shrunk, a bias step 5 measures in the simulator.

**A hole with a wall.** Where the mesh runs on from the flat face down the hole's wall (a bore, a
hole in a thick plate, a hole with a floor close under it), the mesh has no edge at the rim: the
click finds no loop within reach, or the open far end of the wall, whose ring is not flat. The hole
is then read from the flat face around the click:

* the plane region grows from the clicked face as for a plane. Patches it turned down that stay
  within the *drop* of its plane (bumps, dents, noise) count as the face;
* the region's edges are searched for a hole exactly as the mesh's own are (its outer border is no
  hole). This only finds the hole: the region stops where its own tolerance says, which differs
  from click to click;
* what lies beyond that edge must go *down* from the face (against the faces' normal). Where it
  rises by :data:`RIM_DEPTH_DROPS` drops or more, the edge is the foot of a boss or a step up, and
  the answer is an error; a lower bump counts as the face and the search goes on;
* the hole's edge is the line inside that edge where the surface is the drop below the face: the
  points where the mesh's edges cross that level, with a circle fitted to them (one round without
  the strays) and its centre put back on the face. The drop is :data:`RIM_DROP_MM`, or
  :data:`RIM_DROP_TOLS` tolerances of the face when that is more. A fixed level makes the answer
  the same from every click (within 0.02 mm on scans); a wall straight down gives the same circle at
  any level.

The answer carries a warning that says so. Inside that line the surface must go on down, to at
least :data:`RIM_DEPTH_DROPS` drops below the face: a shallower dip is a dent, or a hole that the
scan has closed (small holes over a far, dark background fuse shut, with a dent left where they
are), and the answer is an error that says so. When the clicked face is not flat, its region has no
hole edge within reach, or the surface never reaches the level, the error is the
first search's: a loop too far away, the scan's outer border, or (the open end of a bore's wall)
"use Cylinder on its wall". On a fused scan the rim is a rounded shoulder, not a corner, so the
reading depends on the level: on simulated plates with a floor 10 mm under them a Ø 25 mm hole read
26.1 mm at 0.3 mm, 25.6 mm at 0.5 mm and 24.9 mm at 1 mm (``VALIDATION.md``, "The Hole tool on
holes with a wall").

**Speed** (dev PC, CPU, numpy/scipy; the slow test in ``tests/test_measure_region.py``). On
1M-triangle meshes: a hole 0.1-0.2 s (it looks only in a window); a plane on one side of a box
(170k faces) 0.2 s; the worst cases, a region that is the whole mesh: a plane over the whole plate
0.8 s, a cylinder over a whole tube 1.2-1.4 s (0.4 s of it the final fit on its 500k points).
Growing costs about 1 µs a face; indexing the mesh (the faces of each vertex) about 50 ms a call.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ohs.measure.fit import CircleFit, CylinderFit, PlaneFit, fit_circle, fit_cylinder, fit_plane
from ohs.mesh.confidence import SIGMA_NONE_MM
from ohs.mesh.holes import find_holes
from ohs.mesh.topology import as_mesh, compact, components, rows, sorted_unique

__all__ = ["RegionFit", "fit_at", "KINDS"]

KINDS = ("plane", "cylinder", "hole")
TOL_SIGMAS = 3.0  # the default tolerance: this many times the seed's sigma or scatter ...
TOL_FLOOR_MM = 0.02  # ... and at least this
SIGMA_TRUST = 2.0  # sigma sets the tolerance only while the seed's median sigma is at most this times its RMS
ANGLE_SCATTERS = 3.0  # the default angle: this many times the seed's normal scatter ...
ANGLE_MIN_DEG = 10.0  # ... between these
ANGLE_MAX_DEG = 45.0  # (a fine, noisy mesh's face normals can scatter 15° and more; 45° still stops at an edge)
SEED_POINTS = {"plane": 60, "cylinder": 150}  # points in the first seed
SEED_ANGLE_DEG = {"plane": 30.0, "cylinder": 60.0}  # seed faces' normals within this of the click's
SEED_TRIES = 4  # a cylinder seed that does not fix a cylinder doubles this many times
SEED_ROUNDNESS = 10.0  # a cylinder seed's radius must be this many sigma: its curvature clear, not noise
SEED_ARC_DEG = 45.0  # a seed that covers this much of the round may count its scatter where sigma is pessimistic
SEED_DRIFT = 1.5  # a cylinder's final radius must stay within this factor of its seed's (module docstring)
REFIT_GROWTH = 4.0  # refit each time the region has grown this many times
GROW_FIT_POINTS = 20000  # refits while growing use at most this many of the region's points (picked by id)
MAX_ROUNDS = 20  # rounds of growing, each with a new fit ...
ROUND_GROWTH = 0.01  # ... while a round adds more than this share to the region
SMALL_REGION_POINTS = 50  # fewer points than this: a warning
SCATTER_WARNING = 1.3  # points scattering this many times their sigma (weighted RMS): a warning
#   (a flat region with the right sigma scatters 1 ± 0.1 times at 50 points, less with more)
HOLE_REACH_MM = 10.0  # the hole's edge must come this close to the click
PIN_HOLE_POINTS = 6  # a loop with fewer edge points is a pin hole: passed over when a larger hole is in reach
HOLE_WINDOW_MM = 40.0  # the first window around the click searched for holes
RING_MM = 3.0  # width of the ring of faces around a hole that gives its plane
RING_FLAT_DEG = 30.0  # ring faces turned more than this from the ring's plane ...
RING_NOT_FLAT = 0.25  # ... over more than this share of its area: not a flat face (a bore's wall?)
EDGE_GAP_DEG = 45.0  # a hole edge with a gap wider than this round its centre is incomplete
ROUND_RMS = 0.05  # a hole edge straying more than this times its radius is not round
RIM_DROP_MM = 0.5  # a hole with a wall: its edge is where the surface is this far below the flat face ...
RIM_DROP_TOLS = 3.0  # ... and at least this many of the face's tolerances (a coarse or noisy mesh)
RIM_DEPTH_DROPS = 3.0  # ... and the surface inside must go this many drops below the face: less is a dent
CORR_BIN_EDGES = 0.5  # the residuals' correlation by distance, in bins of this many edge lengths ...
CORR_MIN_LAG_EDGES = 2.0  # ... summed at least this far (the mesh's own pattern makes the nearest bins jump) ...
CORR_MAX_LAG_EDGES = 12.0  # ... then while it stays above 0, at most this far
CORR_POINTS = 50000  # measured on at most this many of the region's points (picked by id) ...
CORR_ANCHORS = 3000  # ... each of the first this many against all of them


@dataclass
class RegionFit:
    """The answer to one click: the fitted shape, its region, and warnings; see :func:`fit_at`."""

    kind: str  # "plane", "cylinder" or "hole"
    fit: PlaneFit | CylinderFit | CircleFit  # a hole's is a CircleFit
    faces: np.ndarray  # sorted face indices of the region (a hole: the ring of faces around its edge)
    edge: np.ndarray  # a hole: its edge's vertex indices, in order round the loop; else empty
    warnings: list[str]
    tolerance_mm: float | None  # plane, cylinder: how far a face's vertices may stray from the fit
    angle_tol_deg: float | None  # plane, cylinder: how far a face's normal may turn from the fit's
    mean_sigma_mm: float | None  # mean sigma of the fitted points (None without sigma)
    # plane, cylinder: how many of the region's points err together with each one (1 = independent);
    # the fit's ± are widened by its square root (module docstring, "The ± values"). None for a hole
    correlated_points: float | None = None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "fit": self.fit.to_dict(), "faces": [int(f) for f in self.faces],
                "edge": [int(v) for v in self.edge], "warnings": list(self.warnings),
                "tolerance_mm": None if self.tolerance_mm is None else float(self.tolerance_mm),
                "angle_tol_deg": None if self.angle_tol_deg is None else float(self.angle_tol_deg),
                "mean_sigma_mm": None if self.mean_sigma_mm is None else float(self.mean_sigma_mm),
                "correlated_points": None if self.correlated_points is None else float(self.correlated_points)}


# --------------------------------------------------------------------------- the mesh, indexed
class _Surface:
    """The mesh with what growing needs: the faces of each vertex, the measured faces, the sigmas."""

    def __init__(self, V: np.ndarray, F: np.ndarray, S: np.ndarray | None, measured: np.ndarray) -> None:
        self.V, self.F, self.S, self.measured = V, F, S, measured
        flat = F.reshape(-1)
        self.vf = np.argsort(flat, kind="stable") // 3  # face ids, vertex after vertex
        self.ptr = np.zeros(len(V) + 1, dtype=np.int64)
        np.cumsum(np.bincount(flat, minlength=len(V)), out=self.ptr[1:])

    def faces_of(self, verts: np.ndarray) -> np.ndarray:
        """Faces using any of ``verts`` (sorted, distinct)."""
        start = self.ptr[verts]
        count = self.ptr[verts + 1] - start
        offset = np.arange(int(count.sum())) - np.repeat(np.cumsum(count) - count, count)
        return sorted_unique(self.vf[np.repeat(start, count) + offset])

    def neighbours(self, faces: np.ndarray) -> np.ndarray:
        """Faces sharing a vertex with ``faces`` (those included; sorted, distinct)."""
        return self.faces_of(sorted_unique(self.F[faces]))

    def corners(self, faces: np.ndarray) -> np.ndarray:
        """``(k, 3, 3)``: each face's three vertex positions."""
        return rows(self.V, self.F[faces].reshape(-1)).reshape(-1, 3, 3)


def _cross_normals(P: np.ndarray) -> np.ndarray:
    """``(b - a) x (c - a)`` of triangles ``P`` (k,3,3): twice the area along the normal (written
    out: np.cross's overhead shows on the many small calls of growing)."""
    e1, e2 = P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]
    return np.stack([e1[:, 1] * e2[:, 2] - e1[:, 2] * e2[:, 1], e1[:, 2] * e2[:, 0] - e1[:, 0] * e2[:, 2],
                     e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]], axis=1)


def _unit_normals(P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(unit normals (k,3), areas (k,))`` of triangles ``P`` (k,3,3); degenerate ones get a zero normal."""
    c = _cross_normals(P)
    nrm = np.sqrt(np.einsum("ij,ij->i", c, c))
    return np.divide(c, nrm[:, None], out=np.zeros_like(c), where=nrm[:, None] > 0), 0.5 * nrm


def _measured(V: np.ndarray, F: np.ndarray, sigma, filled) -> tuple[np.ndarray | None, np.ndarray]:
    """``(sigma per vertex or None, measured flag per face)``; see the module docstring."""
    measured = np.ones(len(F), dtype=bool)
    if filled is not None:
        fl = np.asarray(filled, dtype=bool).reshape(-1)
        if len(fl) != len(F):
            raise ValueError(f"filled must have one flag per face ({len(F)}), got {len(fl)}")
        measured &= ~fl
    if sigma is None:
        return None, measured
    S = np.asarray(sigma, dtype=np.float64).reshape(-1)
    if len(S) != len(V):
        raise ValueError(f"sigma must have one sigma per vertex ({len(V)}), got {len(S)}")
    ok = np.isfinite(S) & (S > 0) & (S < SIGMA_NONE_MM)
    if not ok.all():
        measured &= ok[F[:, 0]] & ok[F[:, 1]] & ok[F[:, 2]]
    return S, measured


# --------------------------------------------------------------------------- shapes: distance and normal
def _distance(kind: str, fit, X: np.ndarray) -> np.ndarray:
    """Signed distance of points ``X`` (..., 3) from the fitted plane or cylinder surface."""
    if kind == "plane":
        return (X - fit.point) @ fit.normal
    q = X - fit.point
    h = q - (q @ fit.axis)[..., None] * fit.axis
    return np.sqrt(np.einsum("...i,...i->...", h, h)) - fit.radius_mm


def _direction(kind: str, fit, X: np.ndarray) -> np.ndarray:
    """The unit normal the fit expects at points ``X`` (k,3), up to sign: the plane's normal, or
    the cylinder's radial direction (outward)."""
    if kind == "plane":
        return np.broadcast_to(fit.normal, X.shape)
    q = X - fit.point
    h = q - (q @ fit.axis)[:, None] * fit.axis
    return h / np.maximum(np.linalg.norm(h, axis=1), 1e-300)[:, None]


def _side(m: _Surface, faces: np.ndarray, kind: str, fit) -> float:
    """+1 or -1: the sign that turns the fit's direction into the faces' own normals (a plane's
    normal towards the scanned side; a cylinder outward for a boss, inward for a bore)."""
    P = m.corners(faces)
    n, a = _unit_normals(P)
    return 1.0 if float(a @ np.einsum("ij,ij->i", n, _direction(kind, fit, P.mean(axis=1)))) >= 0 else -1.0


def _agrees(m: _Surface, faces: np.ndarray, kind: str, fit, side: float, tol: float, cos_tol: float,
            own_sigma: bool) -> np.ndarray:
    """Which ``faces`` have their three vertices within ``tol`` of the fit (or, with
    ``own_sigma``, within :data:`TOL_SIGMAS` times their own sigma when that is more) and their
    normal within the angle of the fit's (see the module docstring)."""
    P = m.corners(faces)
    d = np.abs(_distance(kind, fit, P))
    if own_sigma:
        d = d / np.maximum(tol, TOL_SIGMAS * m.S[m.F[faces]])
        tol = 1.0
    ok = (d[:, 0] <= tol) & (d[:, 1] <= tol) & (d[:, 2] <= tol)  # column by column: faster than all(axis=1)
    c = _cross_normals(P)
    centre = (P[:, 0] + P[:, 1] + P[:, 2]) / 3.0
    along = side * np.einsum("ij,ij->i", c, _direction(kind, fit, centre))
    return ok & (along > 0) & (along * along >= cos_tol * cos_tol * np.einsum("ij,ij->i", c, c))


def _fit_vertices(m: _Surface, kind: str, verts: np.ndarray, prev=None, normals: np.ndarray | None = None):
    """The plane or cylinder through vertices ``verts``; a cylinder starts from ``normals`` or the
    previous fit's axis. A plane weighs each vertex by its sigma; a cylinder weighs them all the
    same, with their median sigma (module docstring, "Weights"). A refit (``prev`` given) that
    fails says so plainly."""
    P = rows(m.V, verts)
    s = None if m.S is None else m.S[verts]
    if s is not None and kind == "cylinder":
        s = np.full(len(s), float(np.median(s)))
    try:
        if kind == "plane":
            return fit_plane(P, s)
        return fit_cylinder(P, s, normals=normals, axis0=None if prev is None else prev.axis)
    except ValueError as e:
        if prev is None:
            raise
        raise ValueError(f"the region around the click does not fix a {kind}: {e}") from None


def correlated_points(verts: np.ndarray, P: np.ndarray, r: np.ndarray, edge_mm: float) -> float:
    """How many points err together with each one: ``N_c = 1 + sum of the correlation of the
    residuals ``r`` over a point's neighbours`` (module docstring, "The ± values"). ``verts``: the
    points' vertex ids (they pick the sample, so the answer is the same on every call), ``P`` (N,3)
    their positions, ``edge_mm`` the mesh's median edge length (it sets the bins and the reach)."""
    from scipy.spatial import cKDTree

    n = len(r)
    if n < 2 or not edge_mm > 0:
        return 1.0
    rc = r - r.mean()
    var = float(rc @ rc) / n
    if not var > 0:
        return 1.0
    h = (np.asarray(verts).astype(np.uint64) * np.uint64(2654435761)) % np.uint64(2**32)  # as _grow's refits
    q = np.argsort(h, kind="stable")[:CORR_POINTS]
    Q, rq = P[q], rc[q]
    na = min(len(q), CORR_ANCHORS)
    step = CORR_BIN_EDGES * edge_mm
    reach = CORR_MAX_LAG_EDGES * edge_mm
    D = cKDTree(Q[:na]).sparse_distance_matrix(cKDTree(Q), reach, output_type="ndarray")
    i, j, d = D["i"], D["j"], D["v"]
    other = i != j
    i, j, d = i[other], j[other], d[other]
    nb = int(round(CORR_MAX_LAG_EDGES / CORR_BIN_EDGES))
    b = np.minimum((d / step).astype(np.int64), nb - 1)
    cnt = np.bincount(b, minlength=nb)
    s = np.bincount(b, weights=rq[i] * rq[j], minlength=nb)
    per_point = s / var / na * (n / len(q))  # the correlation summed over a point's neighbours, bin by bin
    n_c = 1.0
    for k in range(nb):
        if not cnt[k]:
            continue
        if (k + 1) * CORR_BIN_EDGES > CORR_MIN_LAG_EDGES and s[k] <= 0.0:
            break
        n_c += float(per_point[k])
    return max(n_c, 1.0)


def _widened(fit, k: float):
    """``fit`` with its ± values times ``k``."""
    from dataclasses import replace

    if isinstance(fit, PlaneFit):
        return replace(fit, normal_sigma_deg=fit.normal_sigma_deg * k, offset_sigma_mm=fit.offset_sigma_mm * k)
    return replace(fit, radius_sigma_mm=fit.radius_sigma_mm * k, axis_sigma_deg=fit.axis_sigma_deg * k,
                   point_sigma_mm=fit.point_sigma_mm * k)


def _vertex_normals(m: _Surface, faces: np.ndarray, verts: np.ndarray) -> np.ndarray:
    """Area-weighted normals of ``verts`` (sorted) from ``faces`` only (the seed's)."""
    c = _cross_normals(m.corners(faces))
    local = np.searchsorted(verts, m.F[faces]).reshape(-1)
    out = np.stack([np.bincount(local, weights=np.repeat(c[:, k], 3), minlength=len(verts)) for k in range(3)], 1)
    zero = np.linalg.norm(out, axis=1) == 0
    out[zero] = c.sum(axis=0)  # a vertex of degenerate faces only: the seed's mean normal
    return out


# --------------------------------------------------------------------------- plane and cylinder
def _clearly_round(m: _Surface, verts: np.ndarray, fit) -> bool:
    """Is a cylinder seed's curvature clear: its radius :data:`SEED_ROUNDNESS` sigma, or as many
    times its scatter when the sigma is pessimistic and the seed covers a wide arc (module
    docstring)."""
    if fit.radius_mm >= SEED_ROUNDNESS * fit.radius_sigma_mm:
        return True
    if m.S is None:
        return False  # without sigma the fit's ± comes from the scatter already
    s_med = float(np.median(m.S[verts]))
    if not 0.0 < fit.rms_mm < s_med or fit.radius_mm < SEED_ROUNDNESS * fit.radius_sigma_mm * fit.rms_mm / s_med:
        return False
    q = rows(m.V, verts) - fit.point
    radial = q - np.outer(q @ fit.axis, fit.axis)
    mid = radial.mean(axis=0)
    if not np.linalg.norm(mid) > 0:
        return True  # all the way round
    mid = mid / np.linalg.norm(mid)
    angle = np.degrees(np.arctan2(radial @ np.cross(fit.axis, mid), radial @ mid))
    lo, hi = np.percentile(angle, [2.5, 97.5])
    return bool(hi - lo >= SEED_ARC_DEG)


def _seed(m: _Surface, face: int, kind: str):
    """``(seed faces, their fit)``: see the module docstring."""
    ring = m.neighbours(np.array([face]))
    n, a = _unit_normals(m.corners(ring))
    ref = (n * a[:, None]).sum(axis=0)
    ref = ref / (np.linalg.norm(ref) or 1.0)
    cos_seed = math.cos(math.radians(SEED_ANGLE_DEG[kind]))
    seen = np.zeros(len(m.F), dtype=bool)
    seen[face] = True
    parts = [np.array([face])]
    frontier = parts[0]
    target = SEED_POINTS[kind]
    error = None
    for _ in range(SEED_TRIES):
        while len(frontier) and len(sorted_unique(m.F[np.concatenate(parts)])) < target:
            nb = m.neighbours(frontier)
            nb = nb[~seen[nb]]
            seen[nb] = True
            nb = nb[m.measured[nb]]
            nb = nb[_unit_normals(m.corners(nb))[0] @ ref >= cos_seed]
            parts.append(nb)
            frontier = nb
        faces = np.sort(np.concatenate(parts))
        verts = sorted_unique(m.F[faces])
        try:
            normals = _vertex_normals(m, faces, verts) if kind == "cylinder" else None
            fit = _fit_vertices(m, kind, verts, normals=normals)
            if kind == "plane" or _clearly_round(m, verts, fit):
                return faces, fit
            # a free axis can find a 3σ curvature in noise on a flat patch: ask for a clear one
            error = f"its curvature is not clear (radius {fit.radius_mm:.0f} ± {fit.radius_sigma_mm:.0f} mm)"
        except ValueError as e:
            error = str(e)
        if kind != "cylinder" or not len(frontier):
            break
        target *= 2
    raise ValueError(f"the surface around the click does not fix a {kind}: {error}")


def _grow(m: _Surface, face: int, kind: str, tolerance_mm, angle_tol_deg) -> RegionFit:
    """The plane or cylinder region grown from ``face`` (see the module docstring)."""
    seed, fit = _seed(m, face, kind)
    seed_radius = fit.radius_mm if kind == "cylinder" else None
    side = _side(m, seed, kind, fit)
    # sigma sets the tolerance only while it agrees with the seed's measured scatter: a scan's sigma
    # can be far too pessimistic (a 10-frame simulated scan claims ~3 mm where its table scatters
    # 0.4 mm), and a tolerance taken from it lets a plane run over half the part. Then the scatter
    # alone sets it, and sigma stays only the weight.
    s_med = 0.0 if m.S is None else float(np.median(m.S[sorted_unique(m.F[seed])]))
    sigma_agrees = m.S is not None and s_med <= SIGMA_TRUST * max(fit.rms_mm, TOL_FLOOR_MM)
    if tolerance_mm is None:
        tol = max(TOL_SIGMAS * (s_med if sigma_agrees else 0.0), TOL_SIGMAS * fit.rms_mm, TOL_FLOOR_MM)
    else:
        tol = float(tolerance_mm)
    if angle_tol_deg is None:
        P = m.corners(seed)
        c = side * np.einsum("ij,ij->i", _unit_normals(P)[0], _direction(kind, fit, P.mean(axis=1)))
        scatter = math.degrees(math.sqrt(float(np.mean(np.arccos(np.clip(c, -1.0, 1.0)) ** 2))))
        angle = min(max(ANGLE_SCATTERS * scatter, ANGLE_MIN_DEG), ANGLE_MAX_DEG)
    else:
        angle = float(angle_tol_deg)
    cos_tol = math.cos(math.radians(angle))
    own = tolerance_mm is None and sigma_agrees  # by default each vertex may also stray 3 times its own sigma
    keep = _agrees(m, seed, kind, fit, side, tol, cos_tol, own)  # the seed keeps the faces that agree with it
    if not keep[np.searchsorted(seed, face)]:
        keep[np.searchsorted(seed, face)] = True  # the clicked face stays: the region must contain it
    if not keep.all():
        seed = seed[keep]
        fit = _fit_vertices(m, kind, sorted_unique(m.F[seed]), prev=fit)
        side = _side(m, seed, kind, fit)

    M = len(m.F)
    in_region = np.zeros(M, dtype=bool)
    in_region[seed] = True
    in_vertex = np.zeros(len(m.V), dtype=bool)
    in_vertex[m.F[seed]] = True
    def growing_fit(prev):
        """Growing needs a good fit, not the last digit: at most GROW_FIT_POINTS of the region's
        points, picked by a hash of the vertex id, so the same points stay picked as the region
        grows and the fit settles instead of jittering."""
        verts = np.flatnonzero(in_vertex)
        if len(verts) > GROW_FIT_POINTS:
            h = (verts.astype(np.uint64) * np.uint64(2654435761)) % np.uint64(2**32)  # Knuth's multiplicative hash
            verts = verts[h < np.uint64(int(GROW_FIT_POINTS / len(verts) * 2**32))]
        return _fit_vertices(m, kind, verts, prev=prev)

    count, count_at_fit = len(seed), len(seed)
    touched_unmeasured = False
    seen = np.zeros(M, dtype=bool)
    frontier = m.neighbours(seed)
    frontier = frontier[~in_region[frontier]]
    still_growing = True
    for _ in range(MAX_ROUNDS):
        seen[:] = False
        seen[frontier] = True
        turned_down = []
        while len(frontier):
            measured = m.measured[frontier]
            if not measured.all():
                touched_unmeasured = True
                frontier = frontier[measured]
            ok = _agrees(m, frontier, kind, fit, side, tol, cos_tol, own)
            joined = frontier[ok]
            turned_down.append(frontier[~ok])
            if not len(joined):
                break
            in_region[joined] = True
            in_vertex[m.F[joined]] = True
            count += len(joined)
            if count >= REFIT_GROWTH * count_at_fit:
                fit = growing_fit(fit)
                side = _side(m, seed, kind, fit)
                count_at_fit = count
            nb = m.neighbours(joined)
            nb = nb[~in_region[nb] & ~seen[nb]]
            seen[nb] = True
            frontier = nb
        if count < (1.0 + ROUND_GROWTH) * count_at_fit:  # (nearly) nothing joined since the last fit: done
            still_growing = False
            break
        fit = growing_fit(fit)
        side = _side(m, seed, kind, fit)
        count_at_fit = count
        frontier = sorted_unique(np.concatenate(turned_down)) if turned_down else np.zeros(0, dtype=np.int64)
        frontier = frontier[~in_region[frontier]]
        if not len(frontier):
            still_growing = False
            break

    faces = np.flatnonzero(in_region)
    verts = np.flatnonzero(in_vertex)
    fit = _fit_vertices(m, kind, verts, prev=fit)  # the answer: all the region's points
    if seed_radius is not None and not seed_radius / SEED_DRIFT <= fit.radius_mm <= seed_radius * SEED_DRIFT:
        raise ValueError(f"the surface around the click is not one cylinder: its radius is {seed_radius:.0f} mm at the "
                         f"click and {fit.radius_mm:.0f} mm over the region that grew from it (a flat surface, or one "
                         "whose curve changes?)")
    P = rows(m.V, verts)
    e = m.corners(faces[:: max(1, len(faces) // 20000)])
    edge = float(np.median(np.linalg.norm(e[:, 1] - e[:, 0], axis=1)))
    n_c = correlated_points(verts, P, _distance(kind, fit, P), edge)
    fit = _widened(fit, math.sqrt(n_c))
    warnings = []
    if fit.n < SMALL_REGION_POINTS:
        warnings.append(f"small region: {fit.n} points")
    if touched_unmeasured:
        warnings.append("the region touches filled or not measured faces: they are left out")
    if m.S is not None:
        ratio = fit.rms_mm * math.sqrt(float(np.mean(1.0 / m.S[verts] ** 2)))  # sqrt(reduced chi-square), about
        if ratio > SCATTER_WARNING:
            warnings.append(f"the points scatter {ratio:.1f} times more than their sigma "
                            f"(RMS {fit.rms_mm:.3f} mm): a curved or rough surface?")
    if still_growing:
        warnings.append(f"the region was still growing after {MAX_ROUNDS} rounds")
    return RegionFit(kind=kind, fit=fit, faces=faces, edge=np.zeros(0, dtype=np.int64), warnings=warnings,
                     tolerance_mm=tol, angle_tol_deg=angle,
                     mean_sigma_mm=None if m.S is None else float(np.mean(m.S[verts])), correlated_points=n_c)


# --------------------------------------------------------------------------- hole
def _hole_loop(V: np.ndarray, F: np.ndarray, measured: np.ndarray, point: np.ndarray):
    """The hole loop nearest ``point`` in a window around it: ``(window faces, window (Vw, Fw,
    kept vertex ids), loop vertices in the window's numbering)``; see the module docstring.

    A loop wholly inside the window (with room for the ring) is a true boundary of the mesh, and
    its class (hole or outer border) is the window's: the window's own cut edge is the longest
    loop of a cut piece. A loop within reach that leaves the window widens it.
    """
    dist = np.linalg.norm(V - point, axis=1)
    D = HOLE_WINDOW_MM
    whole = float(dist.max())
    while True:
        near = dist < D
        win = np.flatnonzero(measured & (near[F[:, 0]] | near[F[:, 1]] | near[F[:, 2]]))
        Vw, Fw, kept = compact(V, F[win])
        loops, is_border, _ = find_holes(Vw, Fw)
        d_loop = (np.minimum.reduceat(dist[kept[loops.start]], loops.ptr[:-1]) if len(loops)
                  else np.zeros(0))
        reach = np.flatnonzero(d_loop <= HOLE_REACH_MM)
        if not len(reach):
            far = f" (the nearest edge is {d_loop.min():.0f} mm away)" if len(loops) else ""
            raise ValueError(f"no hole edge within {HOLE_REACH_MM:g} mm of the click{far}; "
                             "to measure a bore from its wall, use Cylinder")
        inside = np.array([loops.closed[k] and float(dist[kept[loops.verts(k)]].max()) < D - 2 * RING_MM
                           for k in reach])
        holes = reach[inside & ~is_border[reach]]
        if len(holes):
            # a pin hole (a missing triangle or two, often one that hole filling closed: filled faces
            # are left out here) must not hide the hole meant: it is passed over when a real one is in reach
            real = np.array([len(loops.verts(k)) >= PIN_HOLE_POINTS for k in holes])
            if real.any():
                holes = holes[real]
            k = int(holes[np.argmin(d_loop[holes])])
            return win, (Vw, Fw, kept), loops.verts(k)
        if inside.all() or D > whole:
            raise ValueError("the edge near the click is the scan's outer border, not a hole: "
                             "scan all round the hole")
        D *= 2.0


def _fit_hole(V: np.ndarray, F: np.ndarray, S: np.ndarray | None, measured: np.ndarray, face: int,
              point: np.ndarray) -> RegionFit:
    """The hole at the click: the mesh's own edge, or else the edge of the flat face (module docstring)."""
    try:
        win, window, loop = _hole_loop(V, F, measured, point)
        return _fit_edge(V, F, S, measured, win, window, loop)
    except ValueError:
        rim = _fit_rim(V, F, S, measured, face, point)
        if rim is None:
            raise
        return rim


def _fit_rim(V: np.ndarray, F: np.ndarray, S: np.ndarray | None, measured: np.ndarray, face: int,
             point: np.ndarray) -> RegionFit | None:
    """A hole with a wall: read from the flat face around the click (module docstring). ``None``
    when the clicked face is not flat or its region has no hole edge within reach."""
    try:
        flat = _grow(_Surface(V, F, S, measured), face, "plane", None, None)
    except ValueError:
        return None
    drop = _rim_drop(flat)
    n, a = _unit_normals(rows(V, F[flat.faces[:GROW_FIT_POINTS]].reshape(-1)).reshape(-1, 3, 3))
    up = flat.fit.normal if float(a @ (n @ flat.fit.normal)) >= 0 else -flat.fit.normal
    on_flat = np.zeros(len(F), dtype=bool)
    on_flat[flat.faces] = True
    # the pieces of the mesh that are not the flat face, and how far each gets from its plane
    piece = np.full(len(F), -1, dtype=np.int64)
    far = np.zeros(0)
    rest = np.flatnonzero(measured & ~on_flat)
    if len(rest):
        Vr, Fr, _ = compact(V, F[rest])
        n_pieces, piece_of_vertex, piece[rest] = components(Fr, len(Vr))
        far = np.zeros(n_pieces)
        np.maximum.at(far, piece_of_vertex, np.abs((Vr - flat.fit.point) @ flat.fit.normal))
        # pockets: patches the region's test turned down that stay within the drop of the plane are the face's too
        on_flat[rest[far[piece[rest]] <= drop]] = True
    for _ in range(4):
        try:
            _, window, loop = _hole_loop(V, F, on_flat, point)
        except ValueError:
            return None
        # what lies beyond the edge: the measured faces at its vertices that are not the flat face's
        at_edge = np.zeros(len(V), dtype=bool)
        at_edge[window[2][loop]] = True
        beyond = np.flatnonzero(measured & ~on_flat & (at_edge[F[:, 0]] | at_edge[F[:, 1]] | at_edge[F[:, 2]]))
        P = rows(V, F[beyond].reshape(-1)).reshape(-1, 3, 3)
        if not len(beyond) or float(_unit_normals(P)[1] @ ((P.mean(axis=1) - flat.fit.point) @ up)) <= 0:
            return _fit_contour(V, F, S, measured, flat, up, window[2][loop])
        # the surface rises here. A low bump (a blob, a doubled surface) is passed over: it counts as the face
        # and the search goes on; a boss or a step is an answer
        bumps = np.unique(piece[beyond])
        if float(far[bumps].max()) >= RIM_DEPTH_DROPS * drop:
            raise ValueError("the surface rises from the flat face at this edge (the foot of a boss, or a step up), "
                             "so it is not a hole; to measure a boss, use Cylinder on its wall") from None
        on_flat[np.isin(piece, bumps)] = True
    return None


def _rim_drop(flat: RegionFit) -> float:
    """How far below a flat face its hole's edge is read (mm); see the module docstring."""
    return max(RIM_DROP_MM, RIM_DROP_TOLS * float(flat.tolerance_mm))


def _inside(xy: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Which 2D points ``xy`` (k,2) lie inside the closed polygon ``poly`` (m,2) (even-odd rule)."""
    x, y = xy[:, 0][:, None], xy[:, 1][:, None]
    x0, y0 = poly[:, 0][None], poly[:, 1][None]
    x1, y1 = np.roll(poly[:, 0], -1)[None], np.roll(poly[:, 1], -1)[None]
    with np.errstate(divide="ignore", invalid="ignore"):
        cross = ((y0 > y) != (y1 > y)) & (x < x0 + (y - y0) * (x1 - x0) / (y1 - y0))
    return (np.count_nonzero(cross, axis=1) % 2) == 1


def _fit_contour(V: np.ndarray, F: np.ndarray, S: np.ndarray | None, measured: np.ndarray, flat: RegionFit,
                 up: np.ndarray, loop: np.ndarray) -> RegionFit | None:
    """The hole inside ``loop`` (vertex ids round the flat region's edge) from the line where the
    surface is :func:`_rim_drop` below the flat face (module docstring); ``None`` when there is no
    such line (a shallow dent)."""
    plane = flat.fit
    drop = _rim_drop(flat)
    u = np.cross(up, [1.0, 0.0, 0.0] if abs(up[0]) < 0.9 else [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(up, u)
    L = V[loop] - plane.point
    poly = np.c_[L @ u, L @ v]
    centre, reach = poly.mean(axis=0), float(np.linalg.norm(poly - poly.mean(axis=0), axis=1).max())
    # the measured mesh edges near the hole that cross the level: one end on the face's side, one below
    Q = V - plane.point
    near = np.hypot(Q @ u - centre[0], Q @ v - centre[1]) <= reach + 1e-9
    faces = np.flatnonzero(measured & (near[F[:, 0]] | near[F[:, 1]] | near[F[:, 2]]))
    if not len(faces):
        return None
    E = np.unique(np.sort(np.concatenate([F[faces][:, [0, 1]], F[faces][:, [1, 2]], F[faces][:, [2, 0]]]), axis=1), axis=0)
    h = Q[E] @ up
    crossing = (h.max(axis=1) >= -drop) & (h.min(axis=1) < -drop)
    E, h = E[crossing], h[crossing]
    high = np.where(h[:, 0] >= h[:, 1], E[:, 0], E[:, 1])  # the end on the face's side
    low = np.where(h[:, 0] >= h[:, 1], E[:, 1], E[:, 0])
    hh, hl = (Q[high] @ up), (Q[low] @ up)
    t = (hh + drop) / (hh - hl)
    P = V[high] + t[:, None] * (V[low] - V[high])
    # inside the flat region's edge. A wall straight down projects onto that edge, within the noise: the
    # edge is taken a tolerance wider (scaled about its middle)
    xy = np.c_[(P - plane.point) @ u, (P - plane.point) @ v]
    size = float(np.median(np.linalg.norm(poly - centre, axis=1)))
    keep = _inside(xy, centre + (poly - centre) * (1.0 + float(flat.tolerance_mm) / max(size, 1e-9)))
    P, high, low = P[keep], high[keep], low[keep]
    if len(P) < 2 * PIN_HOLE_POINTS:
        return None
    # how deep the surface goes inside that line: the pieces of the mesh below the level that the line borders
    # (their deep end, not their one deepest vertex: that is noise)
    deep = Q[low] @ up
    under = faces[((Q[F[faces]] @ up) < -drop).all(axis=1)]
    if len(under):
        Vu, Fu, kept_u = compact(V, F[under])
        _, piece_of_vertex, _ = components(Fu, len(Vu))
        at_line = np.zeros(len(V), dtype=bool)
        at_line[low] = True
        mine = np.isin(piece_of_vertex, np.unique(piece_of_vertex[at_line[kept_u]]))
        deep = np.concatenate([deep, (Vu[mine] - plane.point) @ up])
    depth = float(-np.percentile(deep, 10.0))
    if depth < RIM_DEPTH_DROPS * drop:
        raise ValueError(f"the surface dips only {depth:.1f} mm below the flat face here: a dent, or a hole that the "
                         "scan has closed; it cannot be measured as a hole") from None
    sig = None if S is None else S[high]
    try:
        circle = fit_circle(P, sig, normal=up)
        # one round without the strays (another edge inside the region's edge, a floater)
        q = P - circle.center
        r = np.linalg.norm(q - np.outer(q @ up, up), axis=1) - circle.radius_mm
        ok = np.abs(r) <= max(3.0 * 1.4826 * float(np.median(np.abs(r - np.median(r)))), float(flat.tolerance_mm))
        if 2 * PIN_HOLE_POINTS <= int(ok.sum()) < len(P):
            P, high, sig = P[ok], high[ok], None if sig is None else sig[ok]
            circle = fit_circle(P, sig, normal=up)
    except ValueError:
        return None
    circle.center = circle.center + drop * up  # the level line lies ``drop`` below the face: back onto it
    # the edge as vertices (for the outline): the crossing edges' ends on the face's side, in order round the centre
    q = V[high] - circle.center
    e1 = np.cross(up, q[0])
    e1 = e1 / (np.linalg.norm(e1) or 1.0)
    order = np.argsort(np.arctan2(q @ np.cross(up, e1), q @ e1), kind="stable")
    edge = high[order][np.sort(np.unique(high[order], return_index=True)[1])]
    # the ring: the flat face's own faces within RING_MM of the edge
    c = V[F[flat.faces]].mean(axis=1) - circle.center
    rho = np.linalg.norm(c - np.outer(c @ up, up), axis=1)
    ring = flat.faces[(rho <= circle.radius_mm + RING_MM) & (rho >= circle.radius_mm - RING_MM)]
    warnings = ["this hole has a wall: its edge is taken where the surface leaves the flat face around it "
                f"({drop:.2f} mm below it)"]
    warnings += _edge_warnings(circle, P, V, F, measured, edge)
    return RegionFit(kind="hole", fit=circle, faces=np.sort(ring), edge=edge, warnings=warnings,
                     tolerance_mm=None, angle_tol_deg=None,
                     mean_sigma_mm=None if sig is None else float(np.mean(sig)))


def _edge_warnings(circle: CircleFit, P: np.ndarray, V: np.ndarray, F: np.ndarray, measured: np.ndarray,
                   edge: np.ndarray) -> list[str]:
    """Warnings about a hole's edge: ``P`` the fitted points, ``edge`` the edge's vertex ids."""
    warnings = []
    # the edge's coverage round its centre
    q = P - circle.center
    q = q - np.outer(q @ circle.normal, circle.normal)
    u = q[0] / (np.linalg.norm(q[0]) or 1.0)
    ang = np.sort(np.arctan2(q @ np.cross(circle.normal, u), q @ u))
    gap = math.degrees(float(np.max(np.diff(np.append(ang, ang[0] + 2 * math.pi)))))
    if gap > EDGE_GAP_DEG:
        warnings.append(f"the hole's edge is incomplete: {gap:.0f}° of its round has no points")
    if circle.rms_mm > ROUND_RMS * circle.radius_mm:
        warnings.append(f"the hole's edge is not round (RMS {circle.rms_mm:.2f} mm): a slot or a damaged edge?")
    if not measured.all():
        touched = np.zeros(len(V), dtype=bool)
        touched[F[~measured]] = True
        if touched[edge].any():
            warnings.append("the hole borders filled or not measured faces (left out): its edge is where "
                            "the measured surface ends")
    if len(P) < SMALL_REGION_POINTS / 2:
        warnings.append(f"small hole: {len(P)} points on its edge")
    return warnings


def _fit_edge(V: np.ndarray, F: np.ndarray, S: np.ndarray | None, measured: np.ndarray, win: np.ndarray,
              window: tuple, loop: np.ndarray) -> RegionFit:
    """Fit the hole whose edge is ``loop`` in the window's mesh (the three results of :func:`_hole_loop`)."""
    Vw, Fw, kept = window
    w = _Surface(Vw, Fw, None, np.ones(len(Fw), dtype=bool))
    # the ring: faces within RING_MM of the edge, in layers of faces from it
    edge_len = float(np.mean(np.linalg.norm(Vw[np.roll(loop, -1)] - Vw[loop], axis=1)))
    ring = w.faces_of(np.sort(loop))
    for _ in range(max(1, math.ceil(RING_MM / max(edge_len, 1e-9)) - 1)):
        ring = w.neighbours(ring)
    verts = sorted_unique(Fw[ring])
    sig = None if S is None else S[kept]
    plane = fit_plane(Vw[verts], None if sig is None else sig[verts])
    n, a = _unit_normals(w.corners(ring))
    turned = np.abs(n @ plane.normal) < math.cos(math.radians(RING_FLAT_DEG))
    if float(a[turned].sum()) > RING_NOT_FLAT * float(a.sum()):
        raise ValueError("the faces around this hole's edge are not flat (the open end of a bore's wall?): "
                         "to measure the bore, use Cylinder on its wall")
    normal = plane.normal if float(a @ (n @ plane.normal)) >= 0 else -plane.normal
    circle = fit_circle(Vw[loop], None if sig is None else sig[loop], normal=normal)
    warnings = _edge_warnings(circle, Vw[loop], V, F, measured, kept[loop])
    return RegionFit(kind="hole", fit=circle, faces=np.sort(win[ring]), edge=kept[loop], warnings=warnings,
                     tolerance_mm=None, angle_tol_deg=None,
                     mean_sigma_mm=None if sig is None else float(np.mean(sig[loop])))


# --------------------------------------------------------------------------- the click
def fit_at(V, F, face: int, point, kind: str, sigma=None, filled=None, tolerance_mm: float | None = None,
           angle_tol_deg: float | None = None) -> RegionFit:
    """Grow the region of ``kind`` from a click and fit it (see the module docstring).

    ``face``: the clicked face's index; ``point``: the clicked 3D point on it (mm, the mesh's
    frame; it picks the hole). ``kind``: "plane", "cylinder" or "hole". ``sigma`` (N,): each
    vertex's sigma in mm (the mesh's ``quality``; NaN, ``<= 0`` or ``SIGMA_NONE_MM`` = not
    measured); ``filled`` (M,): each face's filled flag. ``tolerance_mm`` and ``angle_tol_deg``
    (plane, cylinder; the angle at most 90°) replace the defaults from the seed.

    Returns a :class:`RegionFit`. Raises ValueError with a plain message for bad input, a click on
    a filled face, a region that does not fix the shape, or no hole near the click.
    """
    V, F = as_mesh(V, F)
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}, got {kind!r}")
    if len(F) == 0:
        raise ValueError("the mesh has no faces")
    face = int(face)
    if not 0 <= face < len(F):
        raise ValueError(f"face {face} is not in the mesh ({len(F)} faces)")
    point = np.asarray(point, dtype=np.float64).reshape(-1)
    if point.shape != (3,) or not np.all(np.isfinite(point)):
        raise ValueError("point must be a finite 3D point")
    if tolerance_mm is not None and not (np.isfinite(tolerance_mm) and tolerance_mm > 0):
        raise ValueError(f"tolerance_mm must be positive, got {tolerance_mm}")
    if angle_tol_deg is not None and not (np.isfinite(angle_tol_deg) and 0 < angle_tol_deg <= 90):
        raise ValueError(f"angle_tol_deg must be more than 0 and at most 90, got {angle_tol_deg}")
    S, measured = _measured(V, F, sigma, filled)
    if not measured[face]:
        raise ValueError("the clicked face is filled or not measured: click on the scanned surface")
    if kind == "hole":
        return _fit_hole(V, F, S, measured, face, point)
    return _grow(_Surface(V, F, S, measured), face, kind, tolerance_mm, angle_tol_deg)
