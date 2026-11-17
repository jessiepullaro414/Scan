"""Relations between two fits: the angles and distances the viewer shows, each with a ± from both fits.

:func:`relate` gives what ``relate()`` in ``ui/src/lib/fits.ts`` gives, the same numbers (the shared
fixture ``tests/data/relations.json`` holds both to 1e-6), with the same labels and notes, so that a
report or a CAD export says what the viewer says:

* two planes: the angle between them (0° parallel, 90° square); when they are parallel (within
  :data:`PARALLEL_DEG`), the distance between them along their mean normal, at the centres of their
  regions (from one fit's point to the other's);
* two axes (cylinders, and holes: a hole's axis is its circle's normal through its centre): the
  angle between them; when parallel, the distance between them (their points' offset across their
  mean direction), else where they pass closest (along the line square to both, 0 where they cross);
  for two holes also the distance between their centres;
* a plane and an axis: the axis's angle to the plane (90° square to it, 0° along it); for a hole,
  its centre's distance from the plane; for a cylinder along the plane (within
  :data:`PARALLEL_DEG`), its axis's distance from the plane at the fit's point.

Each value has a 1σ (``sigma``), unlike the viewer's, which gives one for parallel planes only.

**What the ± is.** The spread of the value from scan to scan, as far as the two fits' own ± values
tell it: the statistical errors of the fits (with their widening for points that err together,
``ohs.measure.region``), the two fits taken as independent. It is not the accuracy: calibration,
scale and the errors a whole region shares (``VALIDATION.md``) come on top, as for the fits.

**How it is made.** A fit gives the ± of a direction (a normal, an axis) and of a position across
one (a cylinder's point, a hole's centre in its plane) as the RMS of a two-component error; which
way it is larger is not kept. Here each is taken as the same both ways: each component has
``σ² / 2``. Each value is then a function of the fits' small moves (each fit's direction tilted
about its point two ways, its position moved two ways across its direction, a plane's offset along
its normal), and its variance is the sum of ``(d value / d move)² · σ_move²`` (first order, the
derivatives taken numerically on the same formula that gives the value). In plain terms:

* angle: the two directions' tilts, ``σ² = (σ_a² + σ_b²) / 2`` (radians, then degrees). Near 0°
  (and, for a plane and an axis, near 90°) the angle is the length of a two-component difference, so
  the tilts across it count too: ``σ² = c² (1 + exp(-θ² / (8 c²)))``, ``c² = (σ_a² + σ_b²) / 2``,
  ``θ`` the angle between the two lines. On simulated trials that keeps ±2σ at 95-98 % from 0 up
  (the first-order ``c`` alone covers only 87 % at 0°);
* distance between parallel planes: both offsets, and the tilt of the mean normal times how far
  the second plane's point lies to the side of the first's (``|w_side|² (σ_a² + σ_b²) / 8``). With
  the points straight across from each other that is the viewer's root-sum-square of the offsets;
  with them apart the tilt counts, which the viewer's ± leaves out;
* distance between parallel axes: both points across their axes (``σ_point² / 2`` each), and the
  tilt of the mean direction times how far apart the points are along it. The distance is the length
  of a two-component offset, so near 0 (coaxial) the component across counts as for the angle;
* closest distance of skew axes, an axis's and a hole centre's distance from a plane, the distance
  between two hole centres: first order in every move above.

**What it leaves out.** Besides calibration, scale and shared errors:

* a hole's axis direction has no ± (``fit_circle`` takes the normal as given, or fits it without
  keeping its ±): an angle with a hole's axis has no ± (``sigma`` None), and in the distances its
  tilt is left out (it matters only through the mean direction of two parallel axes, or the
  closest distance of skew ones);
* a hole's centre has no ± along its axis (``CircleFit.center_sigma_mm`` is in its plane): a hole
  centre's distance from a plane parallel to the hole's face, and the distance of two centres one
  above the other, then get only the other fit's ±. Those are common (a hole's depth below a face),
  so their ± is too small; a ``normal_sigma_deg`` or an offset ± on the circle would fix it
  (``ohs.measure.fit``);
* which way a direction's or a position's error is larger (taken as the same both ways), and the
  correlation between a fit's own direction and position errors. A square patch of a plane and a
  whole round of a cylinder are alike both ways; a long strip of a plane or a cylinder seen over part
  of its round is not. On skew axes seen over 180° of their round the ± came out too large (±2σ
  covered 99.5 % of 200 trials), over 90° too small (89.5 %): within a factor 1.5 of the spread
  either way. The fits would have to keep their 2 x 2 covariances (``ohs.measure.fit``) to do better.

Measured (``tests/test_measure_relate.py``, 200 seeded trials each, fits on noisy points, whole
rounds and square patches): ±2σ covered the true value 94.5-98 % of the time for every relation
above, the mean of ``(error / σ)²`` 0.84-1.21.

Inputs: :class:`ohs.measure.fit.PlaneFit`, ``CylinderFit``, ``CircleFit``, a
:class:`ohs.measure.region.RegionFit`, or the plain dicts their ``to_dict()`` give (saved features
are dicts), or the viewer's ``{kind, shape}``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

__all__ = ["PARALLEL_DEG", "Quantity", "Relation", "relate"]

PARALLEL_DEG = 2.0  # directions closer than this count as parallel (the viewer's PARALLEL_DEG)
TILT_STEP = 1e-4  # radians: the tilt by which a derivative is taken
SHIFT_STEP = 1e-3  # mm: the move by which a derivative is taken (the values are linear in it)


@dataclass
class Quantity:
    """One value of a relation: an angle (``unit`` "deg") or a distance ("mm") and its 1σ."""

    name: str  # "angle", "distance" or "centre_distance"
    label: str  # the viewer's words: "Angle (parallel)", "Distance between the axes", ...
    value: float
    sigma: float | None  # 1σ, the spread from scan to scan (module docstring); None where not known
    unit: str

    def to_dict(self) -> dict:
        return {"name": self.name, "label": self.label, "value": float(self.value),
                "sigma": None if self.sigma is None else float(self.sigma), "unit": self.unit}


@dataclass
class Relation:
    """What applies between two fits; see :func:`relate`."""

    kinds: tuple[str, str]  # each "plane", "cylinder" or "hole", in the order given
    parallel: bool  # two planes or two axes parallel, or an axis along the plane (within PARALLEL_DEG)
    values: list[Quantity] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # why a distance is missing, in the viewer's words

    def get(self, name: str) -> Quantity | None:
        """The value called ``name`` ("angle", "distance", "centre_distance"), or None."""
        return next((q for q in self.values if q.name == name), None)

    def to_dict(self) -> dict:
        return {"kinds": list(self.kinds), "parallel": bool(self.parallel), "values": [q.to_dict() for q in self.values],
                "notes": list(self.notes)}


# --------------------------------------------------------------------------- the two fits
@dataclass
class _Part:
    """A fit as a relation sees it: a direction through a position, and the moves its ± allow."""

    kind: str  # "plane", "cylinder" or "hole" (the viewer's FitKind)
    shape: str  # "plane", "cylinder" or "circle"
    pos: np.ndarray  # a plane's or a cylinder's point, a circle's centre
    dir: np.ndarray  # unit: a plane's or a circle's normal, a cylinder's axis
    dir_sigma: float | None  # rad, the RMS of the direction's two-component error; None: not known
    pos_sigma: float  # mm, the RMS of the position's two-component error across the direction (0 for a plane)
    offset_sigma: float  # mm, a plane's offset along its normal (0 for the others)

    def moves(self) -> list[tuple[str, np.ndarray, float]]:
        """``(what, unit vector, 1σ)``: tilts of the direction, moves of the position."""
        u, v = _across(self.dir)
        tilt = 0.0 if self.dir_sigma is None else self.dir_sigma / math.sqrt(2.0)
        out = [("tilt", u, tilt), ("tilt", v, tilt)]
        if self.pos_sigma > 0:
            s = self.pos_sigma / math.sqrt(2.0)
            out += [("shift", u, s), ("shift", v, s)]
        if self.offset_sigma > 0:
            out.append(("shift", self.dir, self.offset_sigma))
        return out

    def moved(self, what: str, e: np.ndarray, h: float) -> tuple[np.ndarray, np.ndarray]:
        """``(pos, dir)`` after a move by ``h`` (rad or mm) along ``e``."""
        if what == "tilt":
            d = self.dir + h * e
            return self.pos, d / np.linalg.norm(d)
        return self.pos + h * e, self.dir


def _across(d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two unit vectors square to ``d`` and to each other."""
    u = np.cross(d, [1.0, 0.0, 0.0] if abs(d[0]) < 0.9 else [0.0, 1.0, 0.0])
    u = u / np.linalg.norm(u)
    return u, np.cross(d, u)


def _get(fit, name: str):
    return fit.get(name) if isinstance(fit, dict) else getattr(fit, name, None)


def _vec(fit, name: str) -> np.ndarray:
    v = np.asarray(_get(fit, name), dtype=np.float64).reshape(-1)
    if v.shape != (3,) or not np.all(np.isfinite(v)):
        raise ValueError(f"the fit's {name} must be a finite 3D vector")
    return v


def _num(fit, name: str, default: float | None = 0.0) -> float | None:
    x = _get(fit, name)
    return default if x is None else float(x)


def _part(x) -> _Part:
    """A fit, a RegionFit, or one of their dicts (or the viewer's ``{kind, shape}``) as a :class:`_Part`."""
    kind = None
    fit = x
    if isinstance(x, dict) and isinstance(x.get("fit"), dict):
        kind, fit = x.get("kind"), x["fit"]
    elif isinstance(x, dict) and isinstance(x.get("shape"), dict):
        kind, fit = x.get("kind"), x["shape"]
    elif not isinstance(x, dict) and hasattr(x, "fit") and hasattr(x, "faces"):
        kind, fit = x.kind, x.fit
    if isinstance(fit, dict):
        shape = fit.get("kind")
    else:
        shape = {"PlaneFit": "plane", "CylinderFit": "cylinder", "CircleFit": "circle"}.get(type(fit).__name__)
    if shape not in ("plane", "cylinder", "circle"):
        raise ValueError(f"not a fitted shape (a plane, a cylinder or a hole): {shape!r}")
    if kind not in ("plane", "cylinder", "hole"):
        kind = "hole" if shape == "circle" else shape
    if shape == "plane":
        d = _vec(fit, "normal")
        return _Part(kind, shape, _vec(fit, "point"), d / np.linalg.norm(d), math.radians(_num(fit, "normal_sigma_deg")),
                     0.0, _num(fit, "offset_sigma_mm"))
    if shape == "cylinder":
        d = _vec(fit, "axis")
        return _Part(kind, shape, _vec(fit, "point"), d / np.linalg.norm(d), math.radians(_num(fit, "axis_sigma_deg")),
                     _num(fit, "point_sigma_mm"), 0.0)
    d = _vec(fit, "normal")
    tilt = _num(fit, "normal_sigma_deg", None)  # a circle keeps none today (module docstring)
    return _Part(kind, shape, _vec(fit, "center"), d / np.linalg.norm(d), None if tilt is None else math.radians(tilt),
                 _num(fit, "center_sigma_mm"), 0.0)


# --------------------------------------------------------------------------- geometry (as fits.ts)
def _unit(a: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(a))
    return a / (n or 1.0)


def _line_angle_deg(u: np.ndarray, v: np.ndarray) -> float:
    """Angle between two lines or normals (no sign), 0..90°; exact near 0 too."""
    a = math.degrees(math.atan2(float(np.linalg.norm(np.cross(u, v))), abs(float(u @ v))))
    return min(90.0, max(0.0, a))


def _mean_direction(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """The mean of two (nearly) parallel directions, the second flipped to agree with the first."""
    a, b = _unit(u), _unit(v)
    return _unit(a + (-1.0 if float(a @ b) < 0 else 1.0) * b)


def _plane_plane(p1, n1, p2, n2) -> float:
    """Signed distance between two (nearly) parallel planes: between their points, along their mean normal."""
    return float(_mean_direction(n1, n2) @ (p2 - p1))


def _across_parallel(p1, a1, p2, a2) -> np.ndarray:
    """The offset of two (nearly) parallel lines' points across their mean direction."""
    a = _mean_direction(a1, a2)
    w = p2 - p1
    return w - float(w @ a) * a


def _skew(p1, a1, p2, a2) -> float:
    """Signed shortest distance between two lines (along their common perpendicular)."""
    c = np.cross(_unit(a1), _unit(a2))
    return float((p2 - p1) @ c) / float(np.linalg.norm(c))


def _point_plane(q, p, n) -> float:
    return float(_unit(n) @ (q - p))


# --------------------------------------------------------------------------- the ±
def _variance(f, a: _Part, b: _Part) -> float:
    """First-order variance of ``f(pos_a, dir_a, pos_b, dir_b)`` over both fits' moves (module docstring)."""
    var = 0.0
    for part, first in ((a, True), (b, False)):
        for what, e, s in part.moves():
            if s <= 0:
                continue
            h = TILT_STEP if what == "tilt" else SHIFT_STEP
            vals = []
            for sign in (1.0, -1.0):
                p, d = part.moved(what, e, sign * h)
                vals.append(f(p, d, b.pos, b.dir) if first else f(a.pos, a.dir, p, d))
            var += ((vals[0] - vals[1]) / (2 * h) * s) ** 2
    return var


def _two_component(value: float, along: float, across: float) -> float:
    """1σ of the length ``value`` of a two-component quantity whose components have variances
    ``along`` (in its direction) and ``across``: near 0 the component across counts too (module
    docstring)."""
    if across <= 0:
        return math.sqrt(along)
    return math.sqrt(along + across * math.exp(-value * value / (8.0 * across)))


def _angle_sigma(a: _Part, b: _Part, lines_deg: float) -> float | None:
    """1σ (degrees) of an angle between two directions, ``lines_deg`` apart as lines."""
    if a.dir_sigma is None or b.dir_sigma is None:
        return None
    c2 = (a.dir_sigma ** 2 + b.dir_sigma ** 2) / 2.0
    return math.degrees(_two_component(math.radians(lines_deg), c2, c2))


# --------------------------------------------------------------------------- relations
def _planes(a: _Part, b: _Part) -> Relation:
    deg = _line_angle_deg(a.dir, b.dir)
    parallel = deg <= PARALLEL_DEG
    r = Relation((a.kind, b.kind), parallel)
    r.values.append(Quantity("angle", "Angle (parallel)" if parallel else "Angle", deg, _angle_sigma(a, b, deg), "deg"))
    if parallel:
        d = _plane_plane(a.pos, a.dir, b.pos, b.dir)
        r.values.append(Quantity("distance", "Distance", abs(d), math.sqrt(_variance(_plane_plane, a, b)), "mm"))
    else:
        r.notes.append(f"Not parallel (more than {PARALLEL_DEG:g}° apart): there is no one distance between them.")
    return r


def _axes(a: _Part, b: _Part) -> Relation:
    deg = _line_angle_deg(a.dir, b.dir)
    parallel = deg <= PARALLEL_DEG
    r = Relation((a.kind, b.kind), parallel)
    label = "Angle of the axes (parallel)" if parallel else "Angle of the axes"
    r.values.append(Quantity("angle", label, deg, _angle_sigma(a, b, deg), "deg"))
    if parallel:
        w = _across_parallel(a.pos, a.dir, b.pos, b.dir)
        d = float(np.linalg.norm(w))
        e = w / d if d > 0 else _across(_mean_direction(a.dir, b.dir))[0]
        e2 = np.cross(_mean_direction(a.dir, b.dir), e)
        along = _variance(lambda p1, a1, p2, a2: float(_across_parallel(p1, a1, p2, a2) @ e), a, b)
        across = _variance(lambda p1, a1, p2, a2: float(_across_parallel(p1, a1, p2, a2) @ e2), a, b)
        r.values.append(Quantity("distance", "Distance between the axes", d, _two_component(d, along, across), "mm"))
    else:
        d = _skew(a.pos, a.dir, b.pos, b.dir)
        r.values.append(Quantity("distance", "Closest distance of the axes", abs(d), math.sqrt(_variance(_skew, a, b)), "mm"))
    if a.shape == "circle" and b.shape == "circle":
        d = float(np.linalg.norm(b.pos - a.pos))
        e = (b.pos - a.pos) / d if d > 0 else None
        if e is None:
            sigma = math.sqrt((a.pos_sigma ** 2 + b.pos_sigma ** 2) / 2.0)
        else:
            sigma = math.sqrt(_variance(lambda p1, a1, p2, a2: float((p2 - p1) @ e), a, b))
        r.values.append(Quantity("centre_distance", "Centre to centre", d, sigma, "mm"))
    return r


def _plane_axis(plane: _Part, other: _Part, kinds: tuple[str, str]) -> Relation:
    lines = _line_angle_deg(plane.dir, other.dir)
    deg = 90.0 - lines
    what = "the hole's axis" if other.kind == "hole" else "the axis"
    r = Relation(kinds, deg <= PARALLEL_DEG)
    r.values.append(Quantity("angle", f"Angle of {what} to the plane", deg, _angle_sigma(plane, other, lines), "deg"))

    def dist(p1, n1, p2, a2):
        return _point_plane(p2, p1, n1)

    if other.shape == "circle" or deg <= PARALLEL_DEG:
        label = "Hole centre to plane" if other.shape == "circle" else "Axis to plane"
        d = dist(plane.pos, plane.dir, other.pos, other.dir)
        r.values.append(Quantity("distance", label, abs(d), math.sqrt(_variance(dist, plane, other)), "mm"))
    else:
        r.notes.append(f"The axis is not parallel to the plane (more than {PARALLEL_DEG:g}°): there is no one distance "
                       "between them.")
    return r


def relate(a, b) -> Relation:
    """What applies between two fits: angles, and the distances that have one meaning, each with a
    1σ from both fits (see the module docstring). ``a``, ``b``: fits, RegionFits, or their dicts.
    Raises ValueError for something that is not a fitted shape."""
    pa, pb = _part(a), _part(b)
    if pa.shape == "plane" and pb.shape == "plane":
        return _planes(pa, pb)
    if pa.shape == "plane":
        return _plane_axis(pa, pb, (pa.kind, pb.kind))
    if pb.shape == "plane":
        return _plane_axis(pb, pa, (pa.kind, pb.kind))
    return _axes(pa, pb)
