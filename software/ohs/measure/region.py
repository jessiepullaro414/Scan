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

**Boss or bore.** A cylinder's answer says which it is (:attr:`RegionFit.form`): "boss" when its
surface faces away from the axis (material inside: a shaft, a pin), "bore" when it faces the axis
(a hole's wall). The bias of a size depends on it (``ohs.cad.nominal``). The seed picks a side to
grow with, but the answer is decided again from the region itself, on the final fit
(:func:`cylinder_form`): each face's normal against the direction from the axis to it, weighed by
its area, and :data:`FORM_MAJORITY` of the area must agree. When it does not (a mesh folded on
itself, a surface scanned from both sides), the form is None and a warning says why. It is not in
``to_dict()`` unless asked (``with_form``), so today's answers keep their keys; it changes no fit.

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

**Extent.** A fit alone has no size: a plane is endless, a cylinder has no length. The answer also
says where its region lies on it (:attr:`RegionFit.extent`, from :func:`region_extent`), so that a
report or a CAD export can give the numbers the viewer draws. Plain JSON, in mm:

* plane: the region's boundary loops projected onto the fitted plane, as 2D points ``(x, y)`` in
  the basis ``origin`` (the fit's point), ``u``, ``v`` (``u``, ``v`` and the fit's normal
  orthonormal and right-handed; a point is ``origin + x u + y v``). ``outer`` holds the outer loops
  (counter-clockwise seen from the normal's side; one, unless the region is in pieces that touch
  at a corner), ``holes`` the loops inside them (clockwise). Loops smaller than a circle
  :data:`OUTLINE_SPECK_EDGES` mesh edges in radius are left out: they are specks, a few faces in
  the region that its tests turned down in the noise (a noisy plate's region has a hundred), not
  holes in the part. ``rectangle``: the smallest rectangle round the outline in the plane, its
  ``center``, its ``axes`` (the longer side's first) and its ``half_sizes_mm``. ``area_mm2``: the
  outline's area (the outer loops' less the holes');
* cylinder: ``along_mm`` ``[low, high]``, the region's vertices measured along the axis from the
  fit's point (the viewer's ``summarizeRegion`` measures the same), and ``length_mm``; the arc
  the region covers round the axis, from ``start_deg`` through ``arc_deg``, angles taken from the
  unit vector ``zero`` (square to the axis) turning right-handed about the axis. The arc is all
  of the round but its widest gap between vertices; a region whose faces bridge that gap goes all
  the way round (``start_deg`` 0, ``arc_deg`` 360). So a fillet or a boss seen from one side says
  how much of its round it is;
* hole: ``edge``, its edge's points in order round the loop (the last joins the first).

It is measured on the mesh's own vertices, so it is good to about a mesh edge. It costs a click
little (dev PC, 1M-triangle meshes): 20 ms for a plane on one side of a box, 50 ms for a plane
that is the whole plate, 100 ms for a cylinder that is the whole tube (90 ms for 120° of a noisy
one), nothing for a hole. A scanned table (a 400k-face plane region on an 800k-face scan, 2,800
loops of which 722 are not specks) takes 0.12-0.13 s, a tenth of its fit: a scan's region is full
of pits, and everything done with its loops is done for all of them at once (:func:`_loops`,
:func:`_nesting`); testing each loop against each other one took 23 s there.

**Speed** (dev PC, CPU, numpy/scipy; the slow test in ``tests/test_measure_region.py``). On
1M-triangle meshes: a hole 0.1-0.2 s (it looks only in a window); a plane on one side of a box
(170k faces) 0.2 s; the worst cases, a region that is the whole mesh: a plane over the whole plate
0.8 s, a cylinder over a whole tube 1.2-1.4 s (0.4 s of it the final fit on its 500k points).
Growing costs about 1 µs a face; indexing the mesh (the faces of each vertex) about 50 ms a call.
"""
from __future__ import annotations

import functools
import math
from dataclasses import dataclass

import numpy as np

from ohs.measure.fit import CircleFit, CylinderFit, PlaneFit, _basis, fit_circle, fit_cylinder, fit_plane
from ohs.mesh.confidence import SIGMA_NONE_MM
from ohs.mesh.holes import find_holes
from ohs.mesh.topology import as_mesh, compact, components, rows, sorted_unique

__all__ = ["RegionFit", "fit_at", "region_extent", "cylinder_form", "KINDS"]

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
OUTLINE_SPECK_EDGES = 2.0  # a plane's outline leaves out loops smaller than a circle this many mesh edges in radius
FORM_MAJORITY = 0.8  # a cylinder is a boss (a bore) when this share of its region's area faces away from (towards) its axis
FORM_FACES = 20000  # ... judged on at most this many of the region's faces (every k-th)


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
    # where the region lies on its shape, as plain JSON (:func:`region_extent`); :func:`fit_at` sets it
    extent: dict | None = None
    # a cylinder: "boss" (its surface faces away from the axis: a shaft, a pin) or "bore" (a hole's
    # wall), from its region's faces (:func:`cylinder_form`); None when they do not say clearly, and
    # for a plane or a hole
    form: str | None = None

    def to_dict(self, with_extent: bool = False, with_form: bool = False) -> dict:
        """Plain JSON. The extent only ``with_extent``, the form only ``with_form``: without them
        the answer is as it was before they existed."""
        out = {"kind": self.kind, "fit": self.fit.to_dict(), "faces": [int(f) for f in self.faces],
               "edge": [int(v) for v in self.edge], "warnings": list(self.warnings),
               "tolerance_mm": None if self.tolerance_mm is None else float(self.tolerance_mm),
               "angle_tol_deg": None if self.angle_tol_deg is None else float(self.angle_tol_deg),
               "mean_sigma_mm": None if self.mean_sigma_mm is None else float(self.mean_sigma_mm),
               "correlated_points": None if self.correlated_points is None else float(self.correlated_points)}
        if with_extent:
            out["extent"] = self.extent
        if with_form:
            out["form"] = self.form
        return out


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


def cylinder_form(V: np.ndarray, F: np.ndarray, faces: np.ndarray, fit: CylinderFit) -> tuple[str | None, str | None]:
    """Is the cylinder ``fit`` of the region ``faces`` a boss or a bore: ``(form, why not)``.

    Each face of the region votes with its area: its normal (from the order of its corners, as the
    mesh gives it: a scan's faces face out of the material) against the direction from the axis to
    its centre. Away from the axis is a boss (material inside: a shaft, a pin), towards it a bore (a
    hole's wall). It is the region's own geometry, decided once on the final fit, not the side the
    seed chose while growing. When at least :data:`FORM_MAJORITY` of the area agrees the answer is
    ``(form, None)``; otherwise ``(None, a sentence saying so)``: a region that faces both ways (a
    mesh folded on itself, a surface scanned from both sides) is neither, and no guess is made.
    At most :data:`FORM_FACES` faces vote (every k-th, so the same click gives the same answer):
    a few milliseconds on a whole 1M-triangle tube."""
    faces = np.asarray(faces, dtype=np.int64).reshape(-1)
    faces = faces[:: max(1, len(faces) // FORM_FACES)]
    P = rows(V, F[faces].reshape(-1)).reshape(-1, 3, 3)
    c = _cross_normals(P)
    along = np.einsum("ij,ij->i", c, _direction("cylinder", fit, (P[:, 0] + P[:, 1] + P[:, 2]) / 3.0))
    area = np.sqrt(np.einsum("ij,ij->i", c, c))
    away, towards = float(area[along > 0].sum()), float(area[along < 0].sum())
    if not away + towards > 0:
        return None, "whether this cylinder is a boss or a bore is not known: its region has no area to tell"
    share = away / (away + towards)
    if share >= FORM_MAJORITY:
        return "boss", None
    if 1.0 - share >= FORM_MAJORITY:
        return "bore", None
    return None, (f"whether this cylinder is a boss or a bore is not clear: {100 * share:.0f} % of its region faces "
                  f"away from its axis and {100 * (1 - share):.0f} % towards it")


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
    form, unclear = cylinder_form(m.V, m.F, faces, fit) if kind == "cylinder" else (None, None)
    warnings = []
    if fit.n < SMALL_REGION_POINTS:
        warnings.append(f"small region: {fit.n} points")
    if touched_unmeasured:
        warnings.append("the region touches filled or not measured faces: they are left out")
    if m.S is not None:
        ratio = fit.rms_mm * math.sqrt(float(np.mean(1.0 / m.S[verts] ** 2)))  # sqrt(reduced chi-square), about
        if ratio > SCATTER_WARNING:
            warnings.append(f"the points scatter {ratio:.1f} times more than the scan expects "
                            f"(typically {fit.rms_mm:.3f} mm off the shape): a curved or rough surface?")
    if still_growing:
        warnings.append(f"the region was still growing after {MAX_ROUNDS} rounds")
    if unclear:
        warnings.append(unclear)
    return RegionFit(kind=kind, fit=fit, faces=faces, edge=np.zeros(0, dtype=np.int64), warnings=warnings,
                     tolerance_mm=tol, angle_tol_deg=angle,
                     mean_sigma_mm=None if m.S is None else float(np.mean(m.S[verts])), correlated_points=n_c,
                     form=form)


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
        warnings.append(f"the hole's edge is not round (typically {circle.rms_mm:.2f} mm off a circle): a slot or a damaged edge?")
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


# --------------------------------------------------------------------------- extent
@functools.lru_cache(maxsize=2)
def _vertex_weights(n: int) -> np.ndarray:
    """Each vertex's weight for the border test of :func:`_border_loops`: its id and a hash of it in
    one number (kept for the next click on the same mesh; read-only)."""
    ids = np.arange(n, dtype=np.int64)
    # both sums in one number: the id times 2^20 plus a 20-bit hash of it (exact up to 8M vertices; beyond,
    # rounding can only put an inside vertex on the border, which widens the band and changes nothing).
    # The hash must not be linear: on a grid the ids alone cancel at some border vertices (a vertex with
    # two faces on a test plate: 23468 - 23308 + 23470 - 23630 = 0), and a linear hash cancels with them
    # (a multiplicative hash is linear too, in its top bits). Random numbers drawn from a fixed seed are not
    hashed = np.random.default_rng(20261117).integers(0, 1048576, n, dtype=np.int64)
    out = (ids * 1048576 + hashed).astype(np.float64)
    out.setflags(write=False)
    return out


def _border_loops(V: np.ndarray, Fr: np.ndarray, to_2d=None, min_area: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """The boundary loops of the faces ``Fr`` (a region) with 3 points or more: ``(vertex ids loop
    after loop, in order; each loop's length)``. With ``to_2d`` (positions (k,3) -> (k,2)), the
    loops that enclose less than ``min_area`` there are left out early (:func:`_loops`).

    :func:`ohs.mesh.topology.boundary_loops` sorts every edge (0.2 s on a 1M-face region); here only
    the border's are. A vertex inside the region has its faces closed round it: each neighbour comes
    once after it and once before it (in the faces' order), so the sums of the neighbours' ids after
    and before agree; at a border they nearly always do not. The ids alone can agree by chance (on a
    grid they do, a few times a mesh); a second sum, of a hash of the ids, makes that a one in a
    million chance, and then the loop through that vertex is left open. A border edge joins two
    border vertices, so only those edges are counted (:func:`_loops`), in the faces with a border
    vertex (the band along the border, which the walks round a bowtie vertex need)."""
    n = len(V)
    Fc = np.ascontiguousarray(Fr.T)  # corner by corner (bincount is faster on contiguous columns)
    W = _vertex_weights(n)[Fc]
    balance = np.zeros(n)
    for j in range(3):
        balance += np.bincount(Fc[j], weights=W[(j + 1) % 3] - W[(j + 2) % 3], minlength=n)
    border = np.abs(balance) > 0.5
    band = Fr[border[Fc[0]] | border[Fc[1]] | border[Fc[2]]]
    if not len(band):
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    xy = None
    if to_2d is not None:  # only the border's vertices need a place in the plane
        at = np.flatnonzero(border)
        xy = np.zeros((n, 2))
        xy[at] = to_2d(rows(V, at))
    seq, size = _loops(band, n, xy, min_area, border)
    start = np.cumsum(size) - size
    ok = (size >= 3) & border[seq[start]] if len(size) else np.zeros(0, dtype=bool)
    return seq[np.repeat(ok, size)], size[ok]


def _loops(F: np.ndarray, n_vertices: int, xy: np.ndarray | None = None, min_area: float = 0.0,
           starts: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """``(vertices loop after loop, each loop's length)``: the same loops, in the same order and from
    the same first vertex, as :func:`ohs.mesh.topology.boundary_loops`, which walks them one
    half-edge at a time in Python (0.16 s for the 141k border half-edges of a scanned table's band).
    Here a loop that is a plain cycle (each of its half-edges has one successor and one
    predecessor) is put in order all at once: from its first half-edge, each one's distance to the
    end, by pointer doubling. Anything else (an open chain, two half-edges sent to one at a bowtie)
    is walked as there.

    With ``starts`` (a flag per vertex), only the loops along flagged vertices are looked for: only
    the edges between two of them are counted. With ``xy`` too (each vertex's 2D position), plain
    cycles that enclose less than ``min_area`` or whose first vertex is not flagged are left out
    before they are put in order (most loops of a scan's region are specks); when none is large
    enough, all stay, so that the caller can pick the largest."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    from ohs.mesh.topology import _fan_partner, edge_table

    if not len(F):
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    # the border half-edges: those whose edge only one face has. With ``starts``, only the edges
    # between two flagged vertices can be (a border edge's ends are border vertices, and so are those
    # of its twin): only those are counted
    a, b = F.reshape(-1), F[:, [1, 2, 0]].reshape(-1)
    he = np.arange(len(a)) if starts is None else np.flatnonzero(starts[a] & starts[b])
    key = np.minimum(a[he], b[he]) * n_vertices + np.maximum(a[he], b[he])
    o = np.argsort(key, kind="stable")
    k = key[o]
    alone = np.ones(len(k), dtype=bool)
    if len(k) > 1:
        same = k[1:] == k[:-1]
        alone[1:] &= ~same
        alone[:-1] &= ~same
    bh = np.sort(he[o[alone]])
    if not len(bh):
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    # each border half-edge's successor, exactly as boundary_loops finds it
    start = a[bh]
    end = b[bh]
    order = np.argsort(start, kind="stable")
    s_sorted = start[order]
    lo = np.searchsorted(s_sorted, end, "left")
    cnt = np.searchsorted(s_sorted, end, "right") - lo
    nxt = np.full(len(bh), -1, dtype=np.int64)
    one = cnt == 1
    nxt[one] = order[lo[one]]
    taken: set[int] = set()
    where = np.full(3 * len(F), -1, dtype=np.int64)  # half-edge -> its place in bh
    where[bh] = np.arange(len(bh))
    multi = np.flatnonzero(cnt > 1)
    found = np.zeros(0, dtype=np.int64)
    if len(multi):
        # the walks round a bowtie vertex pass only faces that hold it: an edge table of those faces
        # gives the same walks as one of all the faces
        bow = np.zeros(n_vertices, dtype=bool)
        bow[end[multi]] = True
        rows_ = np.flatnonzero(bow[F[:, 0]] | bow[F[:, 1]] | bow[F[:, 2]])
        local = np.full(len(F), -1, dtype=np.int64)
        local[rows_] = np.arange(len(rows_))
        Fs = F[rows_]
        et = edge_table(Fs, n_vertices)
        hs = bh[multi]
        outs = np.array([_fan_partner(h, Fs, et, max_steps=64) for h in (3 * local[hs // 3] + hs % 3).tolist()],
                        dtype=np.int64)
        found = where[3 * rows_[np.maximum(outs, 0) // 3] + np.maximum(outs, 0) % 3]
        found[outs < 0] = -1
    for i, lo_i, cnt_i, pos in zip(multi.tolist(), lo[multi].tolist(), cnt[multi].tolist(), found.tolist()):
        cands = order[lo_i: lo_i + cnt_i].tolist()  # bowtie vertices: paired through the face fans
        if pos < 0 or pos not in cands or pos in taken:
            free = [c for c in cands if c not in taken]
            pos = free[0] if free else cands[0]
        taken.add(pos)
        nxt[i] = pos
    # the loops as pieces of the successor graph; a piece is a plain cycle when every half-edge in it
    # has a successor and is the successor of exactly one
    m = len(bh)
    has = nxt >= 0
    n_pieces, piece = connected_components(coo_matrix((np.ones(int(has.sum())), (np.flatnonzero(has), nxt[has])),
                                                      shape=(m, m)), directed=False)
    pred = np.bincount(nxt[has], minlength=m)
    plain = np.ones(n_pieces, dtype=bool)
    plain[piece[~has | (pred != 1)]] = False
    head = np.unique(piece, return_index=True)[1]  # each piece's first half-edge
    keep = plain.copy()
    if xy is not None:
        twice = np.bincount(piece, weights=xy[start, 0] * xy[end, 1] - xy[start, 1] * xy[end, 0], minlength=n_pieces)
        able = np.bincount(piece, minlength=n_pieces) >= 3
        if starts is not None:
            able &= starts[start[head]]
        big = 0.5 * np.abs(twice) >= min_area
        keep &= able & big if (big & able & plain).any() else able  # none large enough: the caller picks
    # plain cycles: the distance of each half-edge to the one before the head, by pointer doubling
    idx = np.flatnonzero(keep[piece])
    local = np.full(m, -1, dtype=np.int64)  # the doubling runs on the plain cycles' half-edges only
    local[idx] = np.arange(len(idx))
    succ = local[nxt[idx]]
    end = nxt[idx] == head[piece[idx]]
    succ[end] = np.flatnonzero(end)
    d = (~end).astype(np.int64)
    while True:
        s2 = succ[succ]
        if np.array_equal(s2, succ):
            break
        d = d + d[succ]
        succ = s2
    dist = np.zeros(m, dtype=np.int64)
    dist[idx] = d
    # each half-edge's loop (named by the half-edge it starts from) and its place in it
    key = head[piece]
    place = -dist
    # the rest: walked one half-edge at a time, as boundary_loops does
    seen = np.zeros(m, dtype=bool)
    seen[idx] = True
    dropped = ~keep[piece] & plain[piece]
    seen[dropped] = True
    key[dropped] = -1
    nxt_l = nxt.tolist()
    for s in np.flatnonzero(~seen).tolist():
        if seen[s]:
            continue
        i, k = s, 0
        while i != -1 and not seen[i]:
            seen[i] = True
            key[i], place[i] = s, k
            k += 1
            i = nxt_l[i]
    hs = np.flatnonzero(key >= 0)
    hs = hs[np.lexsort((place[hs], key[hs]))]  # loop after loop, as boundary_loops orders them (by their first half-edge)
    k = key[hs]
    first = np.r_[True, k[1:] != k[:-1]] if len(k) else np.zeros(0, dtype=bool)
    return start[hs], np.diff(np.append(np.flatnonzero(first), len(hs)))


def _plane_extent(V: np.ndarray, F: np.ndarray, fit: PlaneFit, faces: np.ndarray) -> dict:
    """A plane region's outline, oriented rectangle and area (module docstring, "Extent")."""
    u, v = _basis(fit.normal)
    o = fit.point
    # specks are no outline: a few faces inside the region that its tests turned down in the noise, a
    # sliver at its edge. Left out before anything else is done with the loops
    e = rows(V, F[faces[:: max(1, len(faces) // 20000)]].reshape(-1)).reshape(-1, 3, 3)
    speck = math.pi * (OUTLINE_SPECK_EDGES * float(np.median(np.linalg.norm(e[:, 1] - e[:, 0], axis=1)))) ** 2
    seq, size = _border_loops(V, rows(F, faces), lambda P: np.c_[(P - o) @ u, (P - o) @ v],
                              0.99 * speck)  # a little less: the exact test follows
    # every loop's signed area at once (a real scan's region has thousands of loops, most of them specks)
    Q = rows(V, seq) - o
    x, y = Q @ u, Q @ v
    start = np.cumsum(size) - size
    if len(size):
        nxt = np.arange(len(x)) + 1
        nxt[start + size - 1] = start  # each loop's last point joins its first
        signed = 0.5 * np.add.reduceat(x * y[nxt] - y * x[nxt], start)
    else:
        signed = np.zeros(0)
    keep = np.flatnonzero(np.abs(signed) >= speck)
    if not len(keep) and len(size):
        keep = np.array([int(np.argmax(np.abs(signed)))])
    XY = np.c_[x, y]
    rings = [XY[a:b] for a, b in zip(start[keep].tolist(), (start + size)[keep].tolist())]
    boxes = (np.minimum.reduceat(XY, start)[keep], np.maximum.reduceat(XY, start)[keep]) if len(keep) else None
    signed = [float(signed[k]) for k in keep]
    depth = _nesting(rings, signed, boxes)
    outer, holes = [], []
    area = 0.0
    for k in np.argsort([-abs(a) for a in signed], kind="stable"):
        ring = rings[k] if (signed[k] >= 0) == (depth[k] % 2 == 0) else rings[k][::-1]  # outer ccw, holes cw
        (holes if depth[k] % 2 else outer).append(ring)
        area += -abs(signed[k]) if depth[k] % 2 else abs(signed[k])
    return {"kind": "plane", "origin": [float(x) for x in o], "u": [float(x) for x in u], "v": [float(x) for x in v],
            "outer": [r.tolist() for r in outer], "holes": [r.tolist() for r in holes],
            "rectangle": _rectangle(np.concatenate(outer) if outer else np.zeros((0, 2)), o, u, v),
            "area_mm2": area}


def _nesting(rings: list[np.ndarray], signed: list[float], boxes: tuple | None = None) -> np.ndarray:
    """How many larger loops each of the 2D ``rings`` lies inside (odd: a hole). A loop is judged by
    most of up to 16 of its points, so that a vertex shared with the loop around it does not decide.

    The rule and the answers are those of testing each loop's points against every larger loop
    with :func:`_inside`, which on a real scan's table (722 loops) took 23 s. A point outside a
    loop's bounding box is outside the loop, so a pair is tested only when most of the smaller
    loop's points lie in the larger one's box (on that table: the pits in the 33 outer loops); then
    each larger loop is tested once, with the points of all the loops it may hold
    (:func:`_inside_many`). ``boxes``: the loops' bounding boxes ``(lower corners, upper corners)``
    when known."""
    n = len(rings)
    depth = np.zeros(n, dtype=np.int64)
    if n < 2:
        return depth
    big = np.abs(np.asarray(signed))
    rank = np.empty(n, dtype=np.int64)
    rank[np.argsort(-big, kind="stable")] = np.arange(n)  # larger first; equal ones in their order
    probes = [r[:: max(1, len(r) // 16)] for r in rings]
    n_probes = np.array([len(q) for q in probes])
    Q = np.concatenate(probes)
    q_first = np.cumsum(n_probes) - n_probes
    if boxes is None:
        boxes = (np.array([r.min(axis=0) for r in rings]), np.array([r.max(axis=0) for r in rings]))
    box_lo, box_hi = boxes
    # the pairs (larger j, smaller k) whose boxes meet, then those with most of k's points in j's box
    (x0, y0), (x1, y1) = box_lo.T, box_hi.T
    meet = (x0[:, None] <= x1) & (y0[:, None] <= y1) & (x1[:, None] >= x0) & (y1[:, None] >= y0)
    J, K = np.nonzero(meet & (rank[:, None] < rank))
    pair = np.repeat(np.arange(len(J)), n_probes[K])
    q = np.repeat(q_first[K], n_probes[K]) + np.arange(len(pair)) - np.repeat(np.cumsum(n_probes[K]) - n_probes[K], n_probes[K])
    in_box = np.all((Q[q] >= box_lo[J[pair]]) & (Q[q] <= box_hi[J[pair]]), axis=1)
    likely = np.bincount(pair, weights=in_box, minlength=len(J)) * 2 > n_probes[K]
    J, K = J[likely], K[likely]
    for j in np.unique(J).tolist():
        held = K[J == j]
        pts = np.concatenate([probes[k] for k in held])
        owner = np.repeat(np.arange(len(held)), n_probes[held])
        votes = np.bincount(owner, weights=_inside_many(pts, rings[j]), minlength=len(held))
        depth[held] += votes * 2 > n_probes[held]
    return depth


def _inside_many(xy: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """:func:`_inside` (the same rule, the same answers) for many points against a long polygon:
    each polygon edge meets only the points whose height it spans, found by sorting them by height,
    so the work grows with the points and the edges, not with their product."""
    order = np.argsort(xy[:, 1], kind="stable")
    xs, ys = xy[order, 0], xy[order, 1]
    x0, y0 = poly[:, 0], poly[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    # an edge counts for the points with min(y0, y1) <= y < max(y0, y1), as (y0 > y) != (y1 > y) says
    first = np.searchsorted(ys, np.minimum(y0, y1), side="left")
    count = np.searchsorted(ys, np.maximum(y0, y1), side="left") - first
    some = np.flatnonzero(count)
    first, count = first[some], count[some]
    x0, y0, dx, dy = x0[some], y0[some], x1[some] - x0[some], y1[some] - y0[some]
    edge = np.repeat(np.arange(len(some)), count)
    at = np.arange(len(edge)) - np.repeat(np.cumsum(count) - count - first, count)  # the point, in height order
    crosses = xs[at] < x0[edge] + (ys[at] - y0[edge]) * dx[edge] / dy[edge]
    out = np.zeros(len(xy), dtype=bool)
    out[order] = np.bincount(at[crosses], minlength=len(xy)) % 2 == 1
    return out


def _rectangle(xy: np.ndarray, o: np.ndarray, u: np.ndarray, v: np.ndarray) -> dict:
    """The smallest rectangle round 2D points ``xy`` (one of its sides lies along an edge of their
    convex hull), in 3D: centre, axes (the longer side first) and half sizes."""
    from scipy.spatial import ConvexHull, QhullError

    try:
        P = xy[ConvexHull(xy).vertices]
    except (QhullError, ValueError):
        P = xy  # a sliver: its points are all there is
    if not len(P):
        return {"center": [float(x) for x in o], "axes": [[float(x) for x in u], [float(x) for x in v]],
                "half_sizes_mm": [0.0, 0.0]}
    e = np.roll(P, -1, axis=0) - P
    e = e[np.linalg.norm(e, axis=1) > 0]
    e = e / np.linalg.norm(e, axis=1)[:, None] if len(e) else np.array([[1.0, 0.0]])
    n = np.c_[-e[:, 1], e[:, 0]]
    a, b = P @ e.T, P @ n.T
    k = int(np.argmin((a.max(0) - a.min(0)) * (b.max(0) - b.min(0))))
    sides = [(e[k], a[:, k]), (n[k], b[:, k])]
    sides.sort(key=lambda s: -float(np.ptp(s[1])))
    centre = sum(d * 0.5 * (float(t.min()) + float(t.max())) for d, t in sides)
    axes = []
    for d, _ in sides:
        d = -d if d[0] < 0 or (d[0] == 0 and d[1] < 0) else d  # a fixed sign: x towards +u
        axes.append([float(x) for x in d[0] * u + d[1] * v])
    return {"center": [float(x) for x in o + centre[0] * u + centre[1] * v], "axes": axes,
            "half_sizes_mm": [0.5 * float(np.ptp(t)) for _, t in sides]}


def _cylinder_extent(V: np.ndarray, F: np.ndarray, fit: CylinderFit, faces: np.ndarray) -> dict:
    """A cylinder region's span along the axis and the arc it covers (module docstring, "Extent")."""
    Fr = F[faces]
    used = np.zeros(len(V), dtype=bool)
    used[Fr] = True
    verts = np.flatnonzero(used)
    q = rows(V, verts) - fit.point
    a = fit.axis / np.linalg.norm(fit.axis)
    t = q @ a  # as the viewer's summarizeRegion: every vertex of the region, from the fit's point
    lo, hi = float(t.min()), float(t.max())
    zero, side = _basis(a)
    ang = np.arctan2(q @ side, q @ zero) % (2 * math.pi)
    s = np.sort(ang)
    gaps = np.diff(np.append(s, s[0] + 2 * math.pi))
    k = int(np.argmax(gaps))
    # the widest gap between the vertices' angles is where the region is not; unless a face of the
    # region bridges it, with a corner on each side of it (a whole round: its widest gap is one face wide)
    r = (ang - (s[k] + 0.5 * gaps[k]) + math.pi) % (2 * math.pi) - math.pi
    near = np.zeros(len(V), dtype=np.int8)
    near[verts] = np.where(r < 0, -1, 1) * (np.abs(r) < 0.5 * math.pi)
    c = near[Fr]
    if len(s) < 2 or bool(np.any((c.min(axis=1) < 0) & (c.max(axis=1) > 0))):
        start, arc = 0.0, 360.0
    else:
        start, arc = math.degrees(float(s[(k + 1) % len(s)])), math.degrees(2 * math.pi - float(gaps[k]))
    return {"kind": "cylinder", "along_mm": [lo, hi], "length_mm": hi - lo, "zero": [float(x) for x in zero],
            "start_deg": start, "arc_deg": arc}


def region_extent(V, F, kind: str, fit, faces, edge=None) -> dict:
    """Where a region lies on its fitted shape, as plain JSON (module docstring, "Extent").

    ``V``, ``F``: the mesh; ``kind``: "plane", "cylinder" or "hole"; ``fit``: the region's
    :class:`PlaneFit`, :class:`CylinderFit` or :class:`CircleFit`; ``faces``: the region's face
    indices; ``edge``: a hole's edge vertices in order. :func:`fit_at` puts the answer in
    :attr:`RegionFit.extent`.
    """
    V, F = as_mesh(V, F)
    return _extent(V, F, kind, fit, np.asarray(faces, dtype=np.int64).reshape(-1),
                   None if edge is None else np.asarray(edge, dtype=np.int64).reshape(-1))


def _extent(V: np.ndarray, F: np.ndarray, kind: str, fit, faces: np.ndarray, edge: np.ndarray | None) -> dict:
    if kind == "plane":
        return _plane_extent(V, F, fit, faces)
    if kind == "cylinder":
        return _cylinder_extent(V, F, fit, faces)
    if kind == "hole":
        return {"kind": "hole", "edge": rows(V, edge).tolist() if edge is not None else []}
    raise ValueError(f"kind must be one of {', '.join(KINDS)}, got {kind!r}")


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
        r = _fit_hole(V, F, S, measured, face, point)
    else:
        r = _grow(_Surface(V, F, S, measured), face, kind, tolerance_mm, angle_tol_deg)
    r.extent = _extent(V, F, kind, r.fit, r.faces, r.edge)
    return r
