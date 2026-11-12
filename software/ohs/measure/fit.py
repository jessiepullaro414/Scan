"""Shapes fitted to measured points, with honest ± values: planes, cylinders and circles.

The first step of reverse engineering (M5, ``RESEARCH_PLAN.md`` 4.8.3): a fit turns a patch of
the scan into numbers a part can be made from, "hole Ø 8.02 ± 0.03 mm" instead of "8.02 mm".
Everything is in mm. Each fit takes points ``P`` (N,3) and optional per-point standard deviations
``sigma`` (N,) (each point weighs ``1 / sigma^2``; None = all weigh the same), and returns a small
dataclass whose ``to_dict()`` gives plain JSON-able numbers:

* :func:`fit_plane`: weighted total least squares (through the weighted centroid, the normal
  along the least spread), with flatness;
* :func:`fit_cylinder`: a start axis from the surface normals (or a given axis, or a robust
  guess), then Levenberg-Marquardt on the true geometric distance ``|distance to the axis| - r``.
  It works on a partial arc (a scan often sees only 90-180° of a boss or a bore) and on a short
  cylinder;
* :func:`fit_circle`: a circle in 3D (the edge of a hole): its plane (given or fitted), then the
  same geometric fit in that plane.

**The ± values.** At the solution the parameter covariance is the inverse of the weighted normal
matrix ``(J^T W J)^-1`` (``J``: the derivatives of the residuals by the parameters, ``W``: the
weights), times a scale. With ``sigma`` given, the scale is the reduced chi-square
``sum(w r^2) / (N - p)`` (``p`` parameters) when that is larger than 1: the points scatter more
than their sigma says, so the ± values grow with them. Else it is 1: a right sigma is not
inflated, and a pessimistic one is kept. Without ``sigma`` the noise is estimated from the
residuals: the scale is the reduced chi-square itself. All ± values are 1σ:

* a radius or an offset: its standard deviation;
* a direction (normal, axis) or a position across one (a cylinder's axis point, a circle's centre
  in its plane) has two components: its ± is the RMS of the total error (the square root of the
  trace of their 2 x 2 covariance), so the error stays within twice that 95 % (one component
  dominates) to 98 % (both alike) of the time.

These are statistical errors only: calibration and scale errors come on top (the artefact test,
``RESEARCH_PLAN.md`` 4.9.6, measures those). They also assume every point belongs to the shape:
a fillet, an outlier or a filled face in the region biases the fit, and shows in the residual RMS.

Bad input (too few points, points all at one place or on a straight line, an arc or a patch that
does not fix the shape) raises ValueError with a plain message.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Callable

import numpy as np

__all__ = ["PlaneFit", "CylinderFit", "CircleFit", "fit_plane", "fit_cylinder", "fit_circle"]

MIN_ROUNDNESS = 3.0  # a fitted radius must be at least this many sigma: its curvature told from a straight line
NORMAL_NEIGHBOURS = 12  # points per local plane when fit_cylinder estimates normals itself
SCREEN_ITERATIONS = 6  # fit_cylinder tries each start axis this long before the best one runs on

_NO_CYLINDER = ("the points do not fix a cylinder: they are nearly flat, or too little of its round or "
                "its length was scanned")
_NO_CIRCLE = "the points do not fix a circle: the arc is too short or too straight for its noise"


# --------------------------------------------------------------------------- results
def _plain(fit) -> dict:
    """The dataclass's fields as plain floats, lists of floats and ints."""
    out = {}
    for f in fields(fit):
        v = getattr(fit, f.name)
        if isinstance(v, np.ndarray):
            out[f.name] = [float(x) for x in v]
        elif isinstance(v, (int, np.integer)):
            out[f.name] = int(v)
        else:
            out[f.name] = float(v)
    return out


@dataclass
class PlaneFit:
    """A plane ``normal . (X - point) = 0`` fitted to points (mm); see :func:`fit_plane`."""

    point: np.ndarray  # (3,) the points' weighted centroid, on the plane
    normal: np.ndarray  # (3,) unit
    rms_mm: float  # weighted RMS of the signed distances
    flatness_mm: float  # largest minus smallest signed distance
    n: int  # points used
    normal_sigma_deg: float  # 1σ of the normal's direction (RMS angle)
    offset_sigma_mm: float  # 1σ of the plane's position along the normal, at ``point``

    def to_dict(self) -> dict:
        return {"kind": "plane", **_plain(self)}


@dataclass
class CylinderFit:
    """A cylinder of radius ``radius_mm`` around the line ``point + t * axis`` (mm); see :func:`fit_cylinder`."""

    point: np.ndarray  # (3,) on the axis, nearest the points' weighted centroid
    axis: np.ndarray  # (3,) unit
    radius_mm: float
    rms_mm: float  # weighted RMS of the distances to the surface
    n: int  # points used
    radius_sigma_mm: float  # 1σ
    axis_sigma_deg: float  # 1σ of the axis direction (RMS angle)
    point_sigma_mm: float  # 1σ of ``point`` across the axis (RMS distance)

    @property
    def diameter_mm(self) -> float:
        return 2.0 * self.radius_mm

    @property
    def diameter_sigma_mm(self) -> float:
        return 2.0 * self.radius_sigma_mm

    def to_dict(self) -> dict:
        return {"kind": "cylinder", **_plain(self), "diameter_mm": float(self.diameter_mm),
                "diameter_sigma_mm": float(self.diameter_sigma_mm)}


@dataclass
class CircleFit:
    """A circle of radius ``radius_mm`` around ``center`` in the plane through it with ``normal`` (mm);
    see :func:`fit_circle`."""

    center: np.ndarray  # (3,)
    normal: np.ndarray  # (3,) unit, the normal of the circle's plane
    radius_mm: float
    rms_mm: float  # weighted RMS of the radial distances in the circle's plane
    n: int  # points used
    radius_sigma_mm: float  # 1σ
    center_sigma_mm: float  # 1σ of the centre in the circle's plane (RMS distance)

    @property
    def diameter_mm(self) -> float:
        return 2.0 * self.radius_mm

    @property
    def diameter_sigma_mm(self) -> float:
        return 2.0 * self.radius_sigma_mm

    def to_dict(self) -> dict:
        return {"kind": "circle", **_plain(self), "diameter_mm": float(self.diameter_mm),
                "diameter_sigma_mm": float(self.diameter_sigma_mm)}


# --------------------------------------------------------------------------- shared helpers
def _points_and_weights(P, sigma, n_min: int, shape: str) -> tuple[np.ndarray, np.ndarray]:
    """Checked ``P`` (N,3) float64 and the weights ``1 / sigma^2`` (ones without ``sigma``)."""
    P = np.asarray(P, dtype=np.float64)
    if P.ndim != 2 or P.shape[1] != 3:
        raise ValueError(f"the points must be an (N, 3) array, got shape {P.shape}")
    if len(P) < n_min:
        raise ValueError(f"a {shape} fit needs at least {n_min} points, got {len(P)}")
    if not np.all(np.isfinite(P)):
        raise ValueError("the points contain NaN or infinite values")
    if sigma is None:
        return P, np.ones(len(P))
    s = np.asarray(sigma, dtype=np.float64)
    if s.ndim == 0:
        s = np.full(len(P), float(s))
    s = s.reshape(-1)
    if len(s) != len(P):
        raise ValueError(f"sigma must have one value per point ({len(P)}), got {len(s)}")
    if not np.all(np.isfinite(s)) or np.any(s <= 0):
        raise ValueError("sigma must be positive and finite")
    return P, 1.0 / (s * s)


def _unit(v, name: str) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64).reshape(-1)
    if v.shape != (3,) or not np.all(np.isfinite(v)):
        raise ValueError(f"{name} must be a finite 3-vector")
    norm = float(np.linalg.norm(v))
    if norm == 0.0:
        raise ValueError(f"{name} must not be zero")
    return v / norm


def _canonical(v: np.ndarray) -> np.ndarray:
    """``v`` or ``-v``, whichever has its largest component positive (points cannot tell the sign)."""
    return -v if v[np.argmax(np.abs(v))] < 0 else v


def _basis(n: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two unit vectors ``(u, v)`` with ``(u, v, n)`` orthonormal and right-handed: ``u = n x e``
    for the coordinate axis ``e`` least along ``n``, ``v = n x u``.

    Plain arithmetic: np.cross's per-call overhead dominates on single vectors, and the fits call
    this twice per iteration.
    """
    x, y, z = float(n[0]), float(n[1]), float(n[2])
    if abs(x) <= abs(y) and abs(x) <= abs(z):
        u = (0.0, z, -y)
    elif abs(y) <= abs(z):
        u = (-z, 0.0, x)
    else:
        u = (y, -x, 0.0)
    k = 1.0 / math.sqrt(u[0] * u[0] + u[1] * u[1] + u[2] * u[2])
    u = (u[0] * k, u[1] * k, u[2] * k)
    return np.array(u), np.array([y * u[2] - z * u[1], z * u[0] - x * u[2], x * u[1] - y * u[0]])


def _spread(P: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """``(centroid, eigenvalues, eigenvectors, size)`` of the points' weighted scatter (ascending).

    ``size`` is the RMS spread along the longest direction (mm). Raises when the points are all at
    one place or on a straight line: no plane, circle or cylinder goes through them.
    """
    W = float(w.sum())
    c = w @ P / W
    Q = P - c
    evals, evecs = np.linalg.eigh((Q * w[:, None]).T @ Q)
    size = math.sqrt(max(float(evals[2]), 0.0) / W)
    if size <= 1e-12 * max(1.0, float(np.abs(c).max())):
        raise ValueError("all the points are at one place")
    if evals[1] <= 1e-12 * evals[2]:
        raise ValueError("the points lie on a straight line")
    return c, evals, evecs, size


def _wrms(r: np.ndarray, w: np.ndarray) -> float:
    return math.sqrt(float(w @ (r * r)) / float(w.sum()))


def _covariance(J: np.ndarray, w: np.ndarray, r: np.ndarray, sigma_given: bool, col_scale: np.ndarray,
                message: str) -> np.ndarray:
    """Parameter covariance ``scale * (J^T W J)^-1`` at a solution with residuals ``r``.

    ``scale``: the reduced chi-square ``sum(w r^2) / (N - p)`` when it is above 1 with ``sigma``
    given (else 1), the reduced chi-square itself without (the noise estimated from the
    residuals). ``col_scale`` makes the columns of ``J`` unitless (a tilt in radians times the
    points' size in mm) so that a nearly singular matrix, a shape the points do not fix, is told
    apart from parameters that merely have different units; it raises ``ValueError(message)``.
    """
    Js = J / col_scale
    A = Js.T @ (Js * w[:, None])
    if not np.all(np.isfinite(A)):
        raise ValueError(message)
    ev = np.linalg.eigvalsh(A)
    if ev[0] <= 1e-12 * ev[-1]:
        raise ValueError(message)
    C = np.linalg.inv(A) / np.outer(col_scale, col_scale)
    chi2_red = float(w @ (r * r)) / (len(r) - J.shape[1])
    return C * (max(1.0, chi2_red) if sigma_given else chi2_red)


def _levenberg_marquardt(evaluate: Callable, step: Callable, x, w: np.ndarray, max_iter: int = 100):
    """Minimise ``sum(w * f^2)``: ``evaluate(x) -> (f, J)``, ``step(x, dx) -> x``. Returns ``(x, f, J)``.

    Marquardt's damping on the diagonal of the normal matrix; stops when a step no longer lowers
    the cost by more than a relative 1e-12 (predicted, or found), or no step lowers it at all.
    """
    f, J = evaluate(x)
    cost = float(w @ (f * f))
    lam = 1e-3
    for _ in range(max_iter):
        A = J.T @ (J * w[:, None])
        g = J.T @ (w * f)
        diag = np.diag(A)
        D = np.diag(np.maximum(diag, 1e-12 * max(float(diag.max()), 1e-300)))
        while True:
            try:
                dx = np.linalg.solve(A + lam * D, -g)
            except np.linalg.LinAlgError:
                dx = None
            if dx is not None and -float(g @ dx) <= 1e-12 * cost:
                return x, f, J  # the step would gain nothing (``-g . dx`` is its predicted gain): converged
            if dx is not None and np.all(np.isfinite(dx)):
                x_new = step(x, dx)
                f_new, J_new = evaluate(x_new)
                cost_new = float(w @ (f_new * f_new))
                if np.isfinite(cost_new) and cost_new <= cost:
                    break
            lam *= 10.0
            if lam > 1e12:
                return x, f, J  # no step lowers the cost: at the minimum
        small = cost - cost_new <= 1e-12 * cost
        x, f, J, cost = x_new, f_new, J_new, cost_new
        lam = max(lam * 0.1, 1e-12)
        if small:
            break
    return x, f, J


def _kasa(x: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple[float, float, float] | None:
    """Algebraic circle ``x^2 + y^2 + D x + E y + F = 0`` (Kasa), weighted: ``(cx, cy, r)``, or None.

    Biased towards a small radius on a short arc, but a good start for the geometric fit.
    """
    mx, my = float(w @ x / w.sum()), float(w @ y / w.sum())
    xs, ys = x - mx, y - my
    sw = np.sqrt(w)
    A = np.column_stack([xs, ys, np.ones_like(xs)]) * sw[:, None]
    (D, E, F), *_ = np.linalg.lstsq(A, -(xs * xs + ys * ys) * sw, rcond=None)
    cx, cy = -0.5 * D, -0.5 * E
    r2 = cx * cx + cy * cy - F
    if not np.isfinite(r2) or r2 <= 0:
        return None
    return cx + mx, cy + my, math.sqrt(r2)


def _check_round(r: float, r_sigma: float, message: str) -> None:
    """A radius must be finite, positive and at least :data:`MIN_ROUNDNESS` sigma (its curvature
    is then told apart from a straight line, or a flat patch)."""
    if not (np.isfinite(r) and np.isfinite(r_sigma) and r > 0 and r >= MIN_ROUNDNESS * r_sigma):
        raise ValueError(message)


# --------------------------------------------------------------------------- plane
def fit_plane(P, sigma=None) -> PlaneFit:
    """Weighted total-least-squares plane through points ``P`` (N,3) in mm.

    The plane goes through the points' weighted centroid, its normal along the direction of least
    weighted spread (the smallest eigenvector of the weighted scatter matrix): this minimises the
    weighted sum of squared distances to the plane exactly. ``sigma`` (N,): each point's standard
    deviation in mm (weight ``1 / sigma^2``); None = all weigh the same, the noise then estimated
    from the residuals. Points cannot tell the normal's sign: its largest component is positive.

    ``rms_mm`` is the weighted RMS of the signed distances, ``flatness_mm`` their range (largest
    minus smallest). The ± values (1σ, see the module docstring) come from the plane's tilt and
    offset about the centroid: ``normal_sigma_deg`` (RMS angle of the normal) and
    ``offset_sigma_mm`` (along the normal, at ``point``). They are statistical errors only:
    calibration and scale errors come on top.

    Raises ValueError for fewer than 4 points, or points all at one place or on a straight line.
    """
    P, w = _points_and_weights(P, sigma, 4, "plane")
    c, _, evecs, size = _spread(P, w)
    normal = _canonical(evecs[:, 0])
    u, v = evecs[:, 2], evecs[:, 1]
    Q = P - c
    d = Q @ normal
    # residual derivatives by the tilt towards u and v (radians) and the offset along the normal
    J = np.column_stack([Q @ u, Q @ v, -np.ones(len(P))])
    C = _covariance(J, w, d, sigma is not None, np.array([size, size, 1.0]), "the points do not fix a plane")
    return PlaneFit(point=c, normal=normal, rms_mm=_wrms(d, w), flatness_mm=float(d.max() - d.min()), n=len(P),
                    normal_sigma_deg=math.degrees(math.sqrt(C[0, 0] + C[1, 1])),
                    offset_sigma_mm=math.sqrt(C[2, 2]))


# --------------------------------------------------------------------------- cylinder
def _axis_from_normals(N: np.ndarray) -> np.ndarray:
    """The direction least spread in the normals (N,3): a cylinder's normals all cross its axis."""
    N = N / np.linalg.norm(N, axis=1, keepdims=True)
    _, vecs = np.linalg.eigh(N.T @ N)
    return vecs[:, 0]


def _estimate_normals(P: np.ndarray) -> np.ndarray:
    """Each point's normal from a plane through its :data:`NORMAL_NEIGHBOURS` nearest points (sign arbitrary)."""
    from scipy.spatial import cKDTree

    k = min(NORMAL_NEIGHBOURS, len(P))
    _, idx = cKDTree(P).query(P, k=k)
    nb = P[idx]
    nb = nb - nb.mean(axis=1, keepdims=True)
    _, vecs = np.linalg.eigh(np.einsum("nki,nkj->nij", nb, nb))
    return vecs[:, :, 0]


def _cylinder_residuals(P: np.ndarray, state) -> tuple[np.ndarray, np.ndarray]:
    """Distances to the surface ``rho - r`` and their derivatives by ``(tilt u, tilt v, shift u,
    shift v, r)``: the axis tilts about ``point`` towards ``u`` and ``v`` (radians), ``point``
    shifts along them (mm)."""
    axis, point, r = state
    u, v = _basis(axis)
    q = P - point
    t = q @ axis
    h = q - t[:, None] * axis
    rho = np.linalg.norm(h, axis=1)
    e = h / np.maximum(rho, 1e-300)[:, None]  # unit vector from the axis to the point
    eu, ev = e @ u, e @ v
    J = np.column_stack([-t * eu, -t * ev, -eu, -ev, -np.ones(len(P))])
    return rho - r, J


def _cylinder_step(state, dx: np.ndarray, centroid: np.ndarray):
    """Apply a step of :func:`_cylinder_residuals`'s parameters; ``point`` moves back to the axis
    point nearest ``centroid`` (sliding along the axis changes nothing)."""
    axis, point, r = state
    u, v = _basis(axis)
    a = axis + dx[0] * u + dx[1] * v
    a /= np.linalg.norm(a)
    p = point + dx[2] * u + dx[3] * v
    p = p + ((centroid - p) @ a) * a
    return a, p, r + dx[4]


def _best_cylinder(P: np.ndarray, w: np.ndarray, c: np.ndarray, axes: list[np.ndarray]):
    """Fit from the best of the start axes; returns ``((axis, point, r), f, J)``, or None when no
    start led to a cylinder.

    Each start gets its centre and radius from a circle fit across the axis and
    :data:`SCREEN_ITERATIONS` Levenberg-Marquardt iterations (a good start has converged by then,
    a wrong one costs far more); the lowest cost then runs to convergence.
    """
    def evaluate(s):
        return _cylinder_residuals(P, s)

    def step(s, dx):
        return _cylinder_step(s, dx, c)

    best, best_cost = None, math.inf
    Q = P - c
    for axis in axes:
        u, v = _basis(axis)
        start = _kasa(Q @ u, Q @ v, w)
        if start is None:
            continue
        cx, cy, r = start
        state, f, _ = _levenberg_marquardt(evaluate, step, (axis, c + cx * u + cy * v, r), w, SCREEN_ITERATIONS)
        cost = float(w @ (f * f))
        if np.isfinite(cost) and np.isfinite(state[2]) and state[2] > 0 and cost < best_cost:
            best, best_cost = state, cost
    if best is None:
        return None
    state, f, J = _levenberg_marquardt(evaluate, step, best, w)
    return (state, f, J) if np.isfinite(state[2]) and state[2] > 0 else None


def fit_cylinder(P, sigma=None, normals=None, axis0=None) -> CylinderFit:
    """Cylinder through points ``P`` (N,3) in mm, by the true geometric distance.

    **Start axis:** from ``normals`` (N,3) when given (the direction least spread in them: a
    cylinder's normals all cross its axis), and/or ``axis0``. Without either, or when they lead
    nowhere, a robust guess: normals estimated from each point's neighbours, and the three
    principal directions of the points (the axis of a long cylinder is the longest, of a short
    one the shortest, of a partial arc often the middle one). From each start, a circle fit across
    the axis gives the centre and radius, then Levenberg-Marquardt minimises
    ``sum(w * (|distance to the axis| - r)^2)`` over the axis direction, its position and the
    radius: each start for a few iterations, the lowest cost then to convergence. Works on a
    partial arc (90-180° of a boss or a bore) and on a short cylinder. ``sigma`` (N,): each
    point's standard deviation in mm (weight ``1 / sigma^2``); None = all weigh the same, the
    noise then estimated from the residuals.

    ``point`` is the axis point nearest the points' weighted centroid; the axis's sign follows
    ``axis0`` when given, else its largest component is positive. The ± values (1σ, see the module
    docstring): ``radius_sigma_mm``, ``axis_sigma_deg`` (RMS angle), ``point_sigma_mm`` (across
    the axis, at ``point``). They are statistical errors only: calibration and scale errors come
    on top.

    Raises ValueError for fewer than 6 points, points all at one place or on a straight line, and
    points that do not fix a cylinder (nearly flat, a ring with no length, too short an arc for
    the noise: the radius must be at least :data:`MIN_ROUNDNESS` sigma).
    """
    P, w = _points_and_weights(P, sigma, 6, "cylinder")
    c, _, evecs, size = _spread(P, w)
    starts = []
    if normals is not None:
        N = np.asarray(normals, dtype=np.float64)
        if N.shape != P.shape:
            raise ValueError(f"normals must be an (N, 3) array like the points {P.shape}, got shape {N.shape}")
        if not np.all(np.isfinite(N)) or np.any(np.linalg.norm(N, axis=1) == 0):
            raise ValueError("normals must be finite and non-zero")
        starts.append(_axis_from_normals(N))
    if axis0 is not None:
        starts.append(_unit(axis0, "axis0"))
    best = _best_cylinder(P, w, c, starts)
    if best is None:  # no start given, or none led to a cylinder: the robust guess
        guesses = [_axis_from_normals(_estimate_normals(P))] + [evecs[:, k] for k in (2, 1, 0)]
        best = _best_cylinder(P, w, c, guesses)
    if best is None:
        raise ValueError(_NO_CYLINDER)
    (axis, point, r), f, J = best
    C = _covariance(J, w, f, sigma is not None, np.array([size, size, 1.0, 1.0, 1.0]), _NO_CYLINDER)
    radius_sigma = math.sqrt(C[4, 4])
    _check_round(r, radius_sigma, _NO_CYLINDER)
    if axis0 is not None:
        axis = axis if axis @ _unit(axis0, "axis0") >= 0 else -axis
    else:
        axis = _canonical(axis)
    return CylinderFit(point=point, axis=axis, radius_mm=float(r), rms_mm=_wrms(f, w), n=len(P),
                       radius_sigma_mm=radius_sigma, axis_sigma_deg=math.degrees(math.sqrt(C[0, 0] + C[1, 1])),
                       point_sigma_mm=math.sqrt(C[2, 2] + C[3, 3]))


# --------------------------------------------------------------------------- circle
def _circle_residuals(x: np.ndarray, y: np.ndarray, c: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Radial distances ``rho - r`` to the circle ``c = (cx, cy, r)`` and their derivatives."""
    dx, dy = x - c[0], y - c[1]
    rho = np.hypot(dx, dy)
    rr = np.maximum(rho, 1e-300)
    return rho - c[2], np.column_stack([-dx / rr, -dy / rr, -np.ones(len(x))])


def fit_circle(P, sigma=None, normal=None) -> CircleFit:
    """Circle in 3D through points ``P`` (N,3) in mm, e.g. along the edge of a hole.

    The circle's plane has ``normal`` when given (the face around a hole), through the points'
    weighted centroid; else it is fitted to the points (:func:`fit_plane`'s method). The points
    are projected onto it, an algebraic circle fit gives the start, and Levenberg-Marquardt
    minimises the weighted squared radial distances ``|distance to the centre| - r`` in the plane.
    Works on a partial arc (120° and more is comfortable). ``sigma`` (N,): each point's standard
    deviation in mm (weight ``1 / sigma^2``); None = all weigh the same, the noise then estimated
    from the residuals. A given normal keeps its sign; a fitted one has its largest component
    positive.

    ``rms_mm`` is the weighted RMS of the radial distances in the plane (how far the points stray
    across the plane is the plane's business). The ± values (1σ, see the module docstring):
    ``radius_sigma_mm`` and ``center_sigma_mm`` (RMS distance in the plane). They are statistical
    errors only: calibration and scale errors come on top.

    Raises ValueError for fewer than 4 points, points all at one place or on a straight line (also
    seen along the given normal), and an arc that does not fix a circle (the radius must be at
    least :data:`MIN_ROUNDNESS` sigma).
    """
    P, w = _points_and_weights(P, sigma, 4, "circle")
    c, _, evecs, _ = _spread(P, w)
    n = _canonical(evecs[:, 0]) if normal is None else _unit(normal, "normal")
    u, v = _basis(n)
    Q = P - c
    x, y = Q @ u, Q @ v
    ev = np.linalg.eigvalsh(np.array([[w @ (x * x), w @ (x * y)], [w @ (x * y), w @ (y * y)]]))
    if ev[0] <= 1e-12 * ev[1]:
        raise ValueError("the points lie on a straight line in the circle's plane")
    start = _kasa(x, y, w)
    if start is None:
        raise ValueError(_NO_CIRCLE)
    sol, f, J = _levenberg_marquardt(lambda s: _circle_residuals(x, y, s), lambda s, dx: s + dx,
                                     np.array(start), w)
    C = _covariance(J, w, f, sigma is not None, np.ones(3), _NO_CIRCLE)
    radius_sigma = math.sqrt(C[2, 2])
    _check_round(float(sol[2]), radius_sigma, _NO_CIRCLE)
    return CircleFit(center=c + sol[0] * u + sol[1] * v, normal=n, radius_mm=float(sol[2]), rms_mm=_wrms(f, w),
                     n=len(P), radius_sigma_mm=radius_sigma, center_sigma_mm=math.sqrt(C[0, 0] + C[1, 1]))
