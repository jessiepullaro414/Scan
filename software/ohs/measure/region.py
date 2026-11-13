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
patch) it doubles, up to :data:`SEED_TRIES` times. The seed keeps only its faces that agree with
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
loop's vertices with that normal. When the ring is not flat (the edge is the open end of a scanned
bore's wall), the answer is an error that says to use Cylinder on the wall: no guess. Warnings: an
edge with a gap round its centre (incomplete), an edge that is not round, a hole that borders
filled or unmeasured faces, few points on the edge. The edge's points are the mesh's own: on a
fused mesh, small holes come out rounded and shrunk, a bias step 5 measures in the simulator.

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
from ohs.mesh.topology import as_mesh, compact, rows, sorted_unique

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

    def to_dict(self) -> dict:
        return {"kind": self.kind, "fit": self.fit.to_dict(), "faces": [int(f) for f in self.faces],
                "edge": [int(v) for v in self.edge], "warnings": list(self.warnings),
                "tolerance_mm": None if self.tolerance_mm is None else float(self.tolerance_mm),
                "angle_tol_deg": None if self.angle_tol_deg is None else float(self.angle_tol_deg),
                "mean_sigma_mm": None if self.mean_sigma_mm is None else float(self.mean_sigma_mm)}


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
    """The plane or cylinder through vertices ``verts``, weighted by their sigma; a cylinder starts
    from ``normals`` or the previous fit's axis. A refit (``prev`` given) that fails says so plainly."""
    P = rows(m.V, verts)
    s = None if m.S is None else m.S[verts]
    try:
        if kind == "plane":
            return fit_plane(P, s)
        return fit_cylinder(P, s, normals=normals, axis0=None if prev is None else prev.axis)
    except ValueError as e:
        if prev is None:
            raise
        raise ValueError(f"the region around the click does not fix a {kind}: {e}") from None


def _vertex_normals(m: _Surface, faces: np.ndarray, verts: np.ndarray) -> np.ndarray:
    """Area-weighted normals of ``verts`` (sorted) from ``faces`` only (the seed's)."""
    c = _cross_normals(m.corners(faces))
    local = np.searchsorted(verts, m.F[faces]).reshape(-1)
    out = np.stack([np.bincount(local, weights=np.repeat(c[:, k], 3), minlength=len(verts)) for k in range(3)], 1)
    zero = np.linalg.norm(out, axis=1) == 0
    out[zero] = c.sum(axis=0)  # a vertex of degenerate faces only: the seed's mean normal
    return out


# --------------------------------------------------------------------------- plane and cylinder
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
            if kind == "plane" or fit.radius_mm >= SEED_ROUNDNESS * fit.radius_sigma_mm:
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
                     mean_sigma_mm=None if m.S is None else float(np.mean(m.S[verts])))


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


def _fit_hole(V: np.ndarray, F: np.ndarray, S: np.ndarray | None, measured: np.ndarray, point: np.ndarray) -> RegionFit:
    win, (Vw, Fw, kept), loop = _hole_loop(V, F, measured, point)
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
    warnings = []
    # the edge's coverage round its centre
    q = Vw[loop] - circle.center
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
        if touched[kept[loop]].any():
            warnings.append("the hole borders filled or not measured faces (left out): its edge is where "
                            "the measured surface ends")
    if len(loop) < SMALL_REGION_POINTS / 2:
        warnings.append(f"small hole: {len(loop)} points on its edge")
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
        return _fit_hole(V, F, S, measured, point)
    return _grow(_Surface(V, F, S, measured), face, kind, tolerance_mm, angle_tol_deg)
