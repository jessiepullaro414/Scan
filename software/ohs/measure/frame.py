"""The part's own coordinate system, built from fitted features by the 3-2-1 rule (K3).

A scan's numbers are in the scan's own frame, which is wherever the scanner happened to start. An
inspector or a CAD user wants them in the part's axes: "the hole is 7.00 mm from the side face",
not "the hole is at (112.4, -38.1, 251.9)". :func:`part_frame` builds that frame from three fitted
features (:mod:`ohs.measure.fit`), and the :class:`PartFrame` it returns turns points, directions,
fits and meshes into it and back. Everything is in mm; the frame is right-handed, a pure rotation
and translation (no scale).

**The three slots.** The axis names are a parameter, ``axes`` (default ``"ZX"``: the primary gives
Z, the secondary X, and Y completes a right-handed frame). Below, "Z" and "X" stand for the first
and second names.

* **Primary: a plane** (the face the part sits on, or its main flat face). Its normal is Z, and
  the plane is Z = 0. It fixes three degrees of freedom: the tilt about X and Y, and Z.
* **Secondary: a plane, a cylinder or a hole**. It fixes the turn about Z:

  - a plane: its normal, turned into the primary plane (taken off its part along Z), is X. It must
    stand at least :data:`SECONDARY_MIN_DEG` from the primary; nearly parallel faces cannot tell
    the turn and are refused;
  - a cylinder's axis or a hole's direction (the normal of its edge's circle) that lies along the
    primary face (more than :data:`ACROSS_DEG` from Z): its direction, turned into the primary
    plane, is X;
  - a cylinder or a hole that stands across the primary face (within :data:`ACROSS_DEG` of Z: a
    hole drilled into it, a pin standing on it) has no direction in the plane: X runs from the
    origin to where it meets the primary plane (a hole's centre is moved along Z onto the plane;
    a cylinder's axis is cut with it). This needs a hole, a cylinder or a point as the origin, at
    least :data:`BASE_MIN_MM` away ("plane, hole, hole", the usual alignment of a part with two
    dowel holes).
* **Origin: a hole, a cylinder, a plane or a point**:

  - a hole's centre, the point where a cylinder's axis meets the primary plane (it must cross it
    at :data:`CROSS_MIN_DEG` or more), or a point: moved along Z onto the primary plane. It sets
    X = 0 and Y = 0, so the hole's centre is the origin. This is the alignment CMM software calls
    "level, rotate, origin": the primary levels (3 degrees of freedom), the secondary only turns
    (1), the hole sets the origin in the plane (2);
  - with ``secondary_zero=True`` the count is 3-2-1 in the strict sense: a plane secondary also
    sets X = 0 (the plane), an axis secondary Y = 0 (the axis, seen along Z), and the origin
    feature only sets the coordinate left over (its centre's Y, or X);
  - a plane: the origin is where the three meet. A plane secondary sets X = 0, the third plane
    Y = 0 (strict 3-2-1, a corner); an axis secondary sets Y = 0 and the third plane X = 0. The
    third plane must cross the line where the first two meet at :data:`CROSS_MIN_DEG` or more.

**Signs.** A fit's normal or axis could point either way (the points cannot tell), so the frame
never uses their sign; the same features always give the same frame:

* Z points from the primary plane towards the part: towards the secondary feature's own point (a
  plane's centroid, the point on a cylinder's axis, a hole's centre), or, when that lies in the
  primary plane (within :data:`SIDE_MIN_MM`), towards the origin feature's. On the face the part
  sits on, Z points up through the part. When both lie in the plane (plane, hole, hole, all on
  one face) nothing tells: Z then has its largest component in the scan's frame positive, and a
  warning says so;
* a plane secondary's X points from that plane towards the part: towards the primary plane's
  centroid, else the origin feature's point. Datum faces as a fixture holds the part: the part
  lies on the + side of each;
* an axis secondary's X points from the origin feature towards the secondary feature; when they
  are level along the axis, the largest component in the scan's frame is positive, with a warning;
* the third axis completes a right-handed frame, wherever that puts the part.

``flip_primary`` and ``flip_secondary`` turn the first or second axis round (and the third with
it, so the frame stays right-handed), for a user who wants Z out of the face instead.

**The ± values** (``PartFrame.sigma``, 1σ). Each fit's ± is a small random error of its
parameters: a plane's normal tilts (``normal_sigma_deg``, split evenly over its two directions)
and its offset moves along the normal (``offset_sigma_mm``); a cylinder's axis tilts about its
point (``axis_sigma_deg``) and moves across itself (``point_sigma_mm``); a hole's centre moves in
its plane (``center_sigma_mm``) and, taken as the same per direction, along its normal (the
circle's plane is fitted on more points than its centre, so this errs on the large side); a point
moves by its ``sigma_mm``. The frame is built again with each parameter moved a little (central
differences), which gives its derivatives ``J``; the covariance of the frame's small rotation
``w`` (in the part's axes, radians) and of its origin's displacement ``d`` (mm) is
``sum_k J_k J_k^T sigma_k^2``, the fits taken as independent of each other. Reported:
``angle_deg`` (the RMS of the whole rotation, ``sqrt(trace cov w)``), ``tilt_deg`` (its part
about the two in-plane axes) and ``turn_deg`` (about the first axis), ``origin_mm`` (the RMS of
the origin's displacement) and ``origin_axes_mm`` (per part axis, X, Y, Z). A turn error moves a
point ``L`` mm from the origin by about ``L x angle``. A value that depends on a parameter with no
± is ``None``, with a warning: a hole's direction (only its centre has a ±) used as the secondary,
a point given without ``sigma_mm``.

What the ± covers: the spread from scan to scan that the fits' own ± covers (``ohs.measure.region``
widens those for the correlation of a scan's errors). On fits to noisy points with honest ± (the
seeded trials of ``tests/test_measure_frame.py``: plane, side face, hole; plane, hole, hole; three
planes; strict 3-2-1; 200 each) ±2σ covers the true frame's whole angle 98.5-100 %, its turn
94.5-97 % and its origin 99-100 % of the time (a 3D error at twice its RMS: 95-99.3 % expected),
and the mean of ``(error / σ)^2`` is 0.76-1.13 (1 expected). What it leaves out:
accuracy (an error shared by a whole scan: a hole's edge read small, a bias of the fusion) and the
calibration's scale, which moves every distance from the origin in proportion.

Bad input (a wrong kind of feature for a slot, a secondary that cannot fix the turn, an origin
that cannot be found, malformed numbers) raises ValueError with a plain message.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, is_dataclass, replace

import numpy as np

from ohs.measure.fit import CircleFit, CylinderFit, PlaneFit

__all__ = ["PartFrame", "part_frame", "FORMAT_VERSION"]

FORMAT_VERSION = 1  # of PartFrame.to_dict()
SECONDARY_MIN_DEG = 20.0  # a plane secondary must stand at least this far from the primary
ACROSS_DEG = 45.0  # an axis within this of the primary's normal stands across the face (else it lies along it)
CROSS_MIN_DEG = 20.0  # an origin cylinder or a third plane must cross what it is cut with at least this steeply
SIDE_MIN_MM = 0.5  # a point this far from a plane or more tells on which side the part is
BASE_MIN_MM = 1.0  # a secondary across the face must be this far from the origin to set the turn
STEP = 1e-6  # finite-difference step for the ± values (radians, mm)
ROTATION_TOL = 1e-9  # PartFrame.from_dict: how far from orthonormal a stored rotation may be

_KINDS = {
    "primary": (("plane",), "the primary must be a plane (the face the part sits on, or its main flat face)"),
    "secondary": (("plane", "cylinder", "circle"),
                  "the secondary must be a plane, a cylinder or a hole (it sets the turn about the first axis)"),
    "origin": (("circle", "cylinder", "plane", "point"), "the origin must be a hole, a cylinder, a plane or a point"),
}
_NAMES = {"plane": "plane", "cylinder": "cylinder", "circle": "hole", "point": "point"}


# --------------------------------------------------------------------------- the record
@dataclass
class PartFrame:
    """The part's coordinate system; see :func:`part_frame` and the module docstring.

    ``matrix`` (4,4) takes a point in the scan's frame to the part's: ``x_part = R x_scan + t``.
    """

    matrix: np.ndarray  # (4,4) scan -> part, a rotation and a translation, mm
    axes: tuple[str, str, str]  # the names of the primary's, the secondary's and the third axis, e.g. ("Z", "X", "Y")
    built_from: list[dict]  # per slot: role, id, name, kind, what it sets, the fit as a plain dict
    sigma: dict  # angle_deg, tilt_deg, turn_deg, origin_mm, origin_axes_mm (1σ; None where unknown)
    warnings: list[str] = field(default_factory=list)
    flip_primary: bool = False
    flip_secondary: bool = False
    secondary_zero: bool = False

    @property
    def rotation(self) -> np.ndarray:
        """(3,3): its rows are the part's X, Y and Z axes in the scan's frame."""
        return self.matrix[:3, :3]

    @property
    def origin(self) -> np.ndarray:
        """The part's origin in the scan's frame."""
        return -self.matrix[:3, :3].T @ self.matrix[:3, 3]

    # ----- points, directions, meshes
    def points_to_part(self, X) -> np.ndarray:
        """Points (...,3) in the scan's frame -> the part's."""
        return np.asarray(X, dtype=np.float64) @ self.matrix[:3, :3].T + self.matrix[:3, 3]

    def points_from_part(self, X) -> np.ndarray:
        """Points (...,3) in the part's frame -> the scan's."""
        return (np.asarray(X, dtype=np.float64) - self.matrix[:3, 3]) @ self.matrix[:3, :3]

    def directions_to_part(self, D) -> np.ndarray:
        """Directions or normals (...,3) in the scan's frame -> the part's (turned only; lengths kept)."""
        return np.asarray(D, dtype=np.float64) @ self.matrix[:3, :3].T

    def directions_from_part(self, D) -> np.ndarray:
        """Directions or normals (...,3) in the part's frame -> the scan's."""
        return np.asarray(D, dtype=np.float64) @ self.matrix[:3, :3]

    def mesh_to_part(self, V, N=None) -> tuple[np.ndarray, np.ndarray | None]:
        """A mesh's vertices (N,3) and optional vertex normals in the part's frame (faces need nothing)."""
        return self.points_to_part(V), None if N is None else self.directions_to_part(N)

    def mesh_from_part(self, V, N=None) -> tuple[np.ndarray, np.ndarray | None]:
        """The way back of :meth:`mesh_to_part`."""
        return self.points_from_part(V), None if N is None else self.directions_from_part(N)

    # ----- fits
    def fit_to_part(self, fit):
        """A fit in the part's frame: a :class:`PlaneFit`, :class:`CylinderFit` or :class:`CircleFit`
        (a new one of the same type), its ``to_dict()`` or a point ``{"kind": "point", ...}`` (a
        new dict), or anything holding one under ``fit`` (a :class:`~ohs.measure.region.RegionFit`,
        its dict, a kept feature). Positions (``point``, ``center``) are moved, directions
        (``normal``, ``axis``) turned as they are (their sign is kept, not chosen again); sizes and
        ± values do not change. The input is left alone."""
        return self._turn_fit(fit, self.points_to_part, self.directions_to_part)

    def fit_from_part(self, fit):
        """The way back of :meth:`fit_to_part`."""
        return self._turn_fit(fit, self.points_from_part, self.directions_from_part)

    @staticmethod
    def _turn_fit(fit, move, turn):
        if isinstance(fit, (PlaneFit, CylinderFit, CircleFit)):
            new = {k: move(getattr(fit, k)) for k in ("point", "center") if hasattr(fit, k)}
            new.update({k: turn(getattr(fit, k)) for k in ("normal", "axis") if hasattr(fit, k)})
            return replace(fit, **new)
        if isinstance(fit, dict):
            out = dict(fit)
            if isinstance(fit.get("fit"), dict):
                out["fit"] = PartFrame._turn_fit(fit["fit"], move, turn)
                return out
            for keys, how in ((("point", "center"), move), (("normal", "axis"), turn)):
                for k in keys:
                    if k in fit:
                        out[k] = [float(x) for x in how(_vector(fit[k], k, "fit"))]
            return out
        if is_dataclass(fit) and hasattr(fit, "fit"):
            return replace(fit, fit=PartFrame._turn_fit(fit.fit, move, turn))
        raise ValueError(f"cannot turn a {type(fit).__name__} into the part's axes: give a fit or its to_dict()")

    # ----- storing
    def to_dict(self) -> dict:
        """A plain JSON object (stored under ``frame`` in a scan's ``features.json``)."""
        return {
            "format_version": FORMAT_VERSION,
            "units": "mm",
            "matrix": [[float(x) for x in row] for row in self.matrix],
            "axes": {"primary": self.axes[0], "secondary": self.axes[1], "third": self.axes[2]},
            "flip_primary": bool(self.flip_primary),
            "flip_secondary": bool(self.flip_secondary),
            "secondary_zero": bool(self.secondary_zero),
            "built_from": _json(self.built_from),
            "sigma": _json(self.sigma),
            "warnings": [str(w) for w in self.warnings],
        }

    @classmethod
    def from_dict(cls, d: dict) -> PartFrame:
        """Read :meth:`to_dict`'s object back; raises ValueError for an unknown ``format_version``
        or a matrix that is not a rotation and a translation."""
        version = d.get("format_version")
        if version != FORMAT_VERSION:
            raise ValueError(f"the part's axes have format_version {version!r}; this version reads {FORMAT_VERSION}")
        M = np.asarray(d.get("matrix"), dtype=np.float64)
        if M.shape != (4, 4) or not np.all(np.isfinite(M)):
            raise ValueError("the part's axes need a finite 4 x 4 matrix")
        R = M[:3, :3]
        if (np.abs(R @ R.T - np.eye(3)).max() > ROTATION_TOL or abs(np.linalg.det(R) - 1.0) > ROTATION_TOL
                or np.any(M[3] != [0.0, 0.0, 0.0, 1.0])):
            raise ValueError("the matrix of the part's axes is not a rotation and a translation")
        a = d.get("axes") or {}
        axes = (a.get("primary"), a.get("secondary"), a.get("third"))
        if sorted(str(x) for x in axes) != ["X", "Y", "Z"]:
            raise ValueError(f"the part's axes must name X, Y and Z once each, got {axes}")
        return cls(matrix=M, axes=axes, built_from=_json(d.get("built_from", [])), sigma=_json(d.get("sigma", {})),
                   warnings=list(d.get("warnings", [])), flip_primary=bool(d.get("flip_primary", False)),
                   flip_secondary=bool(d.get("flip_secondary", False)),
                   secondary_zero=bool(d.get("secondary_zero", False)))


# --------------------------------------------------------------------------- the features
@dataclass
class _Feature:
    """One slot's feature, read from a fit or a dict: its point and direction and their ± parameters."""

    role: str
    kind: str  # plane, cylinder, circle, point
    point: np.ndarray  # plane: its point; cylinder: the point on its axis; circle: its centre; point
    direction: np.ndarray | None  # plane: normal; cylinder: axis; circle: normal; point: None
    params: list  # (variance or None, what it moves: "tilt"/"shift"/"along", unit vector)
    plain: dict
    id: str | None
    name: str | None


def _json(x):
    """``x`` with numpy arrays and numbers made plain (lists, floats, ints), dicts and lists copied."""
    if isinstance(x, dict):
        return {str(k): _json(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, np.ndarray)):
        return [_json(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x)
    return x


def _vector(v, key: str, role: str) -> np.ndarray:
    try:
        a = np.asarray(v, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError):
        a = np.zeros(0)
    if a.shape != (3,) or not np.all(np.isfinite(a)):
        raise ValueError(f"the {role}'s {key} must be a finite 3-vector")
    return a


def _unit(v, key: str, role: str) -> np.ndarray:
    a = _vector(v, key, role)
    n = float(np.linalg.norm(a))
    if n == 0.0:
        raise ValueError(f"the {role}'s {key} must not be zero")
    return a / n


def _across(d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two unit vectors perpendicular to ``d`` and to each other."""
    u = np.cross(d, [1.0, 0.0, 0.0] if abs(d[0]) < 0.9 else [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    return u, np.cross(d, u)


def _sigma(plain: dict, key: str, role: str) -> float | None:
    v = plain.get(key)
    if v is None:
        return None
    v = float(v)
    if not (math.isfinite(v) and v >= 0):
        raise ValueError(f"the {role}'s {key} must be a finite value of 0 or more")
    return v


def _feature(x, role: str, given_id) -> _Feature:
    """Read a slot's feature: a fit dataclass, its dict, a wrapping of one (``fit``), or a 3-vector point."""
    fid, name = given_id, None
    if not isinstance(x, (dict, PlaneFit, CylinderFit, CircleFit)) and is_dataclass(x) and hasattr(x, "fit"):
        x = x.fit  # a RegionFit
    if isinstance(x, dict) and isinstance(x.get("fit"), dict):  # a region's dict, a kept feature
        fid = fid if fid is not None else x.get("id")
        name = x.get("name")
        x = x["fit"]
    if isinstance(x, (PlaneFit, CylinderFit, CircleFit)):
        plain = x.to_dict()
    elif isinstance(x, dict):
        plain = _json(x)
    else:
        plain = {"kind": "point", "point": [float(v) for v in _vector(x, "point", role)]}
    kind = {"hole": "circle"}.get(plain.get("kind"), plain.get("kind"))
    allowed, message = _KINDS[role]
    if kind not in allowed:
        raise ValueError(f"{message}, got {'a ' + _NAMES[kind] if kind in _NAMES else repr(plain.get('kind'))}")
    def deg2(s):
        """A direction's ± (degrees, the RMS of two components) as the variance of each (radians^2)."""
        return None if s is None else math.radians(s) ** 2 / 2.0

    if kind == "plane":
        p, d = _vector(plain.get("point"), "point", role), _unit(plain.get("normal"), "normal", role)
        u, v = _across(d)
        tilt, off = deg2(_sigma(plain, "normal_sigma_deg", role)), _sigma(plain, "offset_sigma_mm", role)
        params = [(tilt, "tilt", u), (tilt, "tilt", v), (None if off is None else off * off, "shift", d)]
    elif kind == "cylinder":
        p, d = _vector(plain.get("point"), "point", role), _unit(plain.get("axis"), "axis", role)
        u, v = _across(d)
        tilt, s = deg2(_sigma(plain, "axis_sigma_deg", role)), _sigma(plain, "point_sigma_mm", role)
        shift = None if s is None else s * s / 2.0
        params = [(tilt, "tilt", u), (tilt, "tilt", v), (shift, "shift", u), (shift, "shift", v)]
    elif kind == "circle":
        p, d = _vector(plain.get("center"), "center", role), _unit(plain.get("normal"), "normal", role)
        u, v = _across(d)
        s = _sigma(plain, "center_sigma_mm", role)
        shift = None if s is None else s * s / 2.0
        # a hole's direction has no ± of its own: its tilts are unknown
        params = [(shift, "shift", u), (shift, "shift", v), (shift, "shift", d), (None, "tilt", u), (None, "tilt", v)]
    else:
        p, d = _vector(plain.get("point"), "point", role), None
        s = _sigma(plain, "sigma_mm", role)
        shift = None if s is None else s * s / 3.0
        params = [(shift, "shift", e) for e in np.eye(3)]
    return _Feature(role=role, kind=kind, point=p, direction=d, params=params, plain=plain,
                    id=None if fid is None else str(fid), name=None if name is None else str(name))


# --------------------------------------------------------------------------- building
@dataclass
class _Plan:
    """The choices made once from the nominal features: modes, signs, axis order."""

    order: tuple[int, int, int]  # row of the primary's, the secondary's, the third axis
    third_sign: float  # +1 or -1: keeps the frame right-handed
    kinds: tuple[str, str, str]
    mode: str  # secondary: "plane", "along" (an axis along the face) or "through" (across it, from the origin)
    s1: float = 1.0  # the primary's normal times this is the first axis (before a flip)
    s2: float = 1.0  # the secondary's direction in the plane times this is the second axis (before a flip)
    flip1: bool = False
    flip2: bool = False
    zero: bool = False  # secondary_zero


def _angle_to(d: np.ndarray, n: np.ndarray) -> float:
    """Angle between the lines along ``d`` and ``n`` (0-90°)."""
    return math.degrees(math.atan2(float(np.linalg.norm(np.cross(d, n))), abs(float(d @ n))))


def _on_plane(kind: str, p: np.ndarray, d, a1: np.ndarray, p1: np.ndarray) -> np.ndarray:
    """Where a point-like feature meets the primary plane: a cylinder's axis cut with it, anything
    else moved along the first axis onto it."""
    if kind == "cylinder":
        return p + (float(a1 @ (p1 - p)) / float(a1 @ d)) * d
    return p - float(a1 @ (p - p1)) * a1


def _build(plan: _Plan, geo) -> tuple[np.ndarray, np.ndarray]:
    """``(R, O)``: the part's axes as the rows of ``R`` and its origin in the scan's frame, from
    the three features' ``(point, direction)`` with the plan's choices."""
    (p1, n1), (p2, d2), (p3, d3) = geo
    k2, k3 = plan.kinds[1], plan.kinds[2]
    a1 = plan.s1 * n1
    if plan.mode == "through":
        O = _on_plane(k3, p3, d3, a1, p1)
        w = _on_plane(k2, p2, d2, a1, p1) - O
    else:
        w = d2
    w = w - float(w @ a1) * a1
    a2 = (1.0 if plan.mode == "through" else plan.s2) * w / np.linalg.norm(w)
    if plan.flip1:
        a1 = -a1
    if plan.flip2:
        a2 = -a2
    a3 = np.cross(a1, a2)
    if plan.mode != "through":
        if k3 == "plane" or plan.zero:
            # three planes through the origin: the primary, the secondary's zero, the origin feature's
            second = (d2, p2) if plan.mode == "plane" else (a3, p2)
            if k3 == "plane":
                third = (d3, p3)
            else:  # strict 3-2-1: the origin feature sets only the coordinate left over
                third = (a3 if plan.mode == "plane" else a2, _on_plane(k3, p3, d3, a1, p1))
            A = np.array([a1, second[0], third[0]])
            O = np.linalg.solve(A, [a1 @ p1, second[0] @ second[1], third[0] @ third[1]])
        else:
            O = _on_plane(k3, p3, d3, a1, p1)
    R = np.zeros((3, 3))
    R[plan.order[0]], R[plan.order[1]], R[plan.order[2]] = a1, a2, plan.third_sign * a3
    return R, O


def _parse_axes(axes) -> tuple[tuple[int, int, int], float, tuple[str, str, str]]:
    s = str(axes).upper()
    if len(s) != 2 or s[0] == s[1] or any(c not in "XYZ" for c in s):
        raise ValueError(f"The axes must be two different letters of X, Y and Z, the primary's axis first and then "
                         f"the secondary's (for example ZX), not {axes}")
    third = next(c for c in "XYZ" if c not in s)
    order = ("XYZ".index(s[0]), "XYZ".index(s[1]), "XYZ".index(third))
    sign = 1.0 if order in ((0, 1, 2), (1, 2, 0), (2, 0, 1)) else -1.0  # an odd order would mirror the frame
    return order, sign, (s[0], s[1], third)


def _canonical_sign(v: np.ndarray) -> float:
    """+1 or -1: whichever makes ``v``'s largest component positive (the scan's own axes decide)."""
    return -1.0 if v[int(np.argmax(np.abs(v)))] < 0 else 1.0


def _plan(f1: _Feature, f2: _Feature, f3: _Feature, order, third_sign, names, flip1, flip2, zero,
          warnings: list[str]) -> _Plan:
    """Decide the secondary's mode and the signs, and refuse what cannot make a frame (module docstring)."""
    Z, X = names[0], names[1]
    n1, p1 = f1.direction, f1.point
    plan = _Plan(order=order, third_sign=third_sign, kinds=(f1.kind, f2.kind, f3.kind), mode="plane",
                 flip1=flip1, flip2=flip2, zero=zero)
    # the secondary
    tilt = _angle_to(f2.direction, n1)
    if f2.kind == "plane":
        if tilt < SECONDARY_MIN_DEG:
            raise ValueError(f"the secondary face is nearly parallel to the primary ({tilt:.1f}° between them; it "
                             f"needs {SECONDARY_MIN_DEG:g}° or more), so it cannot set the turn about {Z}: pick a face "
                             "that stands across the primary, such as a side face, or a hole or a cylinder")
    else:
        plan.mode = "through" if tilt < ACROSS_DEG else "along"
    if plan.mode == "through":
        if f3.kind == "plane":
            raise ValueError(f"a {_NAMES[f2.kind]} across the primary face sets the turn only with a hole, a "
                             f"cylinder or a point as the origin (the {X} axis runs from the origin to it): pick one "
                             "as the origin, or a side face as the secondary")
        if zero:
            raise ValueError(f"Strict 3-2-1 needs a plane, or a cylinder or a hole lying along the primary face, as "
                             f"the secondary; a {_NAMES[f2.kind]} across the face only sets the turn")
    # the origin
    if f3.kind == "cylinder" and 90.0 - _angle_to(f3.direction, n1) < CROSS_MIN_DEG:
        raise ValueError(f"the origin cylinder does not cross the primary plane (its axis is "
                         f"{90.0 - _angle_to(f3.direction, n1):.1f}° from it; it needs {CROSS_MIN_DEG:g}° or more): "
                         "pick a hole, or a cylinder that stands across the primary face")
    if f3.kind == "plane":
        line = np.cross(n1, f2.direction if plan.mode == "plane" else np.cross(n1, f2.direction))
        line /= np.linalg.norm(line)
        crossing = 90.0 - _angle_to(f3.direction, line)  # between the third plane and that line
        if crossing < CROSS_MIN_DEG:
            seen = "secondary face" if plan.mode == "plane" else f"secondary {_NAMES[f2.kind]}'s axis"
            raise ValueError(f"the origin face does not fix the origin: it is nearly parallel to the line where the "
                             f"primary and the {seen} meet ({crossing:.1f}° from it; it needs "
                             f"{CROSS_MIN_DEG:g}° or more): pick a face across that line, or a hole")
    # Z: from the primary plane towards the part
    for f in (f2, f3):
        h = float(n1 @ (f.point - p1))
        if abs(h) >= SIDE_MIN_MM:
            plan.s1 = 1.0 if h > 0 else -1.0
            break
    else:
        plan.s1 = _canonical_sign(n1)
        warnings.append(f"the features all lie in the primary face, so nothing tells on which side of it the part "
                        f"is: the {Z} axis was set by the scan's own axes; flip it if it points out of the part")
    a1 = plan.s1 * n1
    # X: a plane's into the part; an axis's from the origin towards it; across the face, from the origin to it
    if plan.mode == "through":
        O = _on_plane(f3.kind, f3.point, f3.direction, a1, p1)
        base = float(np.linalg.norm(_on_plane(f2.kind, f2.point, f2.direction, a1, p1) - O))
        if base < BASE_MIN_MM:
            raise ValueError(f"the secondary {_NAMES[f2.kind]} is too close to the origin ({base:.2f} mm; it needs "
                             f"{BASE_MIN_MM:g} mm or more) to set the turn about {Z}: pick another hole farther "
                             "away, or a side face")
        return plan
    w = f2.direction - float(f2.direction @ a1) * a1
    w /= np.linalg.norm(w)
    if plan.mode == "plane":
        tests = [float(f2.direction @ (f.point - f2.point)) for f in (f1, f3)]  # (w leans the normal's way)
    else:
        tests = [float(w @ (f2.point - f3.point))]
    for h in tests:
        if abs(h) >= SIDE_MIN_MM:
            plan.s2 = 1.0 if h > 0 else -1.0
            break
    else:
        plan.s2 = _canonical_sign(w)
        warnings.append(f"nothing tells which way the {X} axis should run along the secondary "
                        f"{_NAMES[f2.kind]}: it was set by the scan's own axes; flip it if needed")
    return plan


def _moved(f: _Feature, k: int, eps: float) -> tuple[np.ndarray, np.ndarray | None]:
    """``f``'s ``(point, direction)`` with its ``k``-th ± parameter moved by ``eps``."""
    _, what, e = f.params[k]
    p, d = f.point, f.direction
    if what == "tilt":
        d = d + eps * e
        d = d / np.linalg.norm(d)
    else:
        p = p + eps * e
    return p, d


def _vee(S: np.ndarray) -> np.ndarray:
    return np.array([S[2, 1], S[0, 2], S[1, 0]])


def _uncertainty(plan: _Plan, feats: list[_Feature], R: np.ndarray, warnings: list[str]) -> dict:
    """The frame's 1σ from the fits' (module docstring, "The ± values")."""
    geo = [(f.point, f.direction) for f in feats]
    cov_w, cov_d = np.zeros((3, 3)), np.zeros((3, 3))
    unknown_w, unknown_d = np.zeros(3, dtype=bool), np.zeros(3, dtype=bool)
    unknown_from = []
    for i, f in enumerate(feats):
        for k, (var, what, _) in enumerate(f.params):
            ends = []
            for eps in (STEP, -STEP):
                g = list(geo)
                g[i] = _moved(f, k, eps)
                ends.append(_build(plan, g))
            (Ra, Oa), (Rb, Ob) = ends
            jw = _vee((Ra - Rb) @ R.T) / (2.0 * STEP)  # the small rotation, in the part's axes
            jd = R @ (Oa - Ob) / (2.0 * STEP)  # the origin's displacement, in the part's axes
            if var is None:
                hit_w, hit_d = np.abs(jw) > 1e-6, np.abs(jd) > 1e-6
                if hit_w.any() or hit_d.any():
                    unknown_w |= hit_w
                    unknown_d |= hit_d
                    hole_direction = f.kind == "circle" and what == "tilt"
                    if (f.role, hole_direction) not in unknown_from:
                        unknown_from.append((f.role, hole_direction))
                continue
            cov_w += var * np.outer(jw, jw)
            cov_d += var * np.outer(jd, jd)
    for role, hole_direction in unknown_from:
        if hole_direction:
            warnings.append(f"the {role} hole's direction has no ± (only its centre has): the frame's ± leaves it "
                            "out; for a ±, fit a cylinder on the hole's wall instead, or use a face")
        else:
            kind = feats[["primary", "secondary", "origin"].index(role)].kind
            warnings.append(f"the {role} {_NAMES[kind]} has no ± values: the frame's ± leaves it out")
    first = plan.order[0]
    var_w = np.clip(np.diag(cov_w), 0.0, None)
    var_d = np.clip(np.diag(cov_d), 0.0, None)
    in_plane = [j for j in range(3) if j != first]

    def deg(idx):
        return None if unknown_w[idx].any() else math.degrees(math.sqrt(float(var_w[idx].sum())))

    return {
        "angle_deg": deg([0, 1, 2]),
        "tilt_deg": deg(in_plane),
        "turn_deg": deg([first]),
        "origin_mm": None if unknown_d.any() else math.sqrt(float(var_d.sum())),
        "origin_axes_mm": [None if unknown_d[j] else math.sqrt(float(var_d[j])) for j in range(3)],
    }


def _sets(plan: _Plan, names, f2: _Feature, f3: _Feature) -> tuple[str, str, str]:
    """In words, what each slot's feature sets."""
    Z, X, Y = names
    primary = f"the {Z} axis (its normal) and {Z} = 0 (the plane)"
    if plan.mode == "plane":
        secondary = f"the {X} axis (its normal, turned into the primary plane)"
        secondary += f" and {X} = 0 (the plane)" if plan.zero or f3.kind == "plane" else ""
    elif plan.mode == "along":
        what = "axis" if f2.kind == "cylinder" else "direction"
        secondary = f"the {X} axis (its {what}, turned into the primary plane)"
        secondary += f" and {Y} = 0 (its axis, seen along {Z})" if plan.zero or f3.kind == "plane" else ""
    else:
        secondary = f"the {X} axis (from the origin to where it meets the primary plane)"
    left = Y if plan.mode == "plane" else X  # the coordinate a third plane or strict 3-2-1 leaves to the origin
    if f3.kind == "plane":
        origin = f"{left} = 0 (the plane); the origin is where the three meet"
    else:
        where = {"circle": "its centre", "point": "the point",
                 "cylinder": "where its axis meets the primary plane"}[f3.kind]
        origin = f"{left} = 0 ({where})" if plan.zero else f"{X} = 0 and {Y} = 0 ({where}, on the primary plane)"
    return primary, secondary, origin


def part_frame(primary, secondary, origin, *, axes: str = "ZX", flip_primary: bool = False,
               flip_secondary: bool = False, secondary_zero: bool = False, ids=(None, None, None)) -> PartFrame:
    """The part's coordinate system from three fitted features by the 3-2-1 rule (module docstring).

    ``primary``: a plane; ``secondary``: a plane, a cylinder or a hole; ``origin``: a hole, a
    cylinder, a plane or a point. Each may be a :class:`PlaneFit`, :class:`CylinderFit` or
    :class:`CircleFit` (a hole), its ``to_dict()``, a :class:`~ohs.measure.region.RegionFit` or
    its dict, a kept feature ``{"id": ..., "name": ..., "fit": {...}}``, a point as
    ``{"kind": "point", "point": [x, y, z], "sigma_mm": s}`` or a bare 3-vector, all in the scan's
    frame (mm).

    ``axes``: the names of the primary's and the secondary's axes ("ZX": Z and X, Y completes a
    right-handed frame). ``flip_primary`` / ``flip_secondary`` turn that axis round, and the third
    with it. ``secondary_zero``: strict 3-2-1 with a hole, cylinder or point as the origin (the
    secondary also sets its own zero, the origin feature only the coordinate left over). ``ids``:
    the features' ids for the record (else a kept feature's own ``id``).

    Returns a :class:`PartFrame`. Raises ValueError with a plain message for a wrong kind of
    feature in a slot, a secondary that cannot set the turn, an origin that cannot be found, or
    malformed input.
    """
    order, third_sign, names = _parse_axes(axes)
    ids = tuple(ids) + (None,) * (3 - len(tuple(ids)))
    feats = [_feature(x, role, i) for x, role, i in zip((primary, secondary, origin),
                                                        ("primary", "secondary", "origin"), ids)]
    warnings: list[str] = []
    plan = _plan(*feats, order, third_sign, names, bool(flip_primary), bool(flip_secondary), bool(secondary_zero),
                 warnings)
    R, O = _build(plan, [(f.point, f.direction) for f in feats])
    M = np.eye(4)
    M[:3, :3] = R
    M[:3, 3] = -R @ O
    sigma = _uncertainty(plan, feats, R, warnings)
    sets = _sets(plan, names, feats[1], feats[2])
    built_from = [{"role": f.role, "id": f.id, "name": f.name, "kind": f.kind, "sets": s, "fit": f.plain}
                  for f, s in zip(feats, sets)]
    return PartFrame(matrix=M, axes=names, built_from=built_from, sigma=sigma, warnings=warnings,
                     flip_primary=bool(flip_primary), flip_secondary=bool(flip_secondary),
                     secondary_zero=bool(secondary_zero))
