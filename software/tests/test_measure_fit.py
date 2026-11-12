"""ohs.measure.fit: planes, cylinders and circles with honest ± values, on points with known truth.

Every shape is tilted in space; its points carry Gaussian noise of known sigma (0.05 mm, about the
scanner's) in all three directions. Every random generator is seeded.

* Accuracy: each fit finds its shape within 4σ of the truth, on full shapes, partial arcs
  (cylinders over 180° and 90°, circles over 120° and 180°) and short cylinders, with and without
  normals; the residual RMS is the noise; the results are plain JSON.
* Monte Carlo, 200 trials per case: ±2σ covers the truth 90-99.5 % of the time for the radius,
  the direction (normal, axis), the position and the plane's offset, and the mean of
  ``(error / σ)^2`` is about 1 (the ± values are neither too small nor too large).
* The scale: a sigma 5x too small is caught by the reduced chi-square, a right one is not
  inflated, a too large one is kept; without sigma the noise comes from the residuals.
* Points with a large sigma pull the fit less.
* Bad input raises ValueError: too few points, points at one place or on a line, a flat patch, a
  ring with no length, too short an arc, malformed arrays.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from ohs.measure import fit_circle, fit_cylinder, fit_plane

NOISE = 0.05  # mm, each coordinate
TRIALS = 200


def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v)


NORMAL = unit([0.2, -0.35, 0.9])  # the planes' and circles' normal
AXIS = unit([0.3, -0.5, 0.8])  # the cylinders' axis
ORIGIN = np.array([12.0, -30.0, 45.0])  # on every shape: the plane's point, the circle's centre, on the axis


def across(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two unit vectors perpendicular to ``a`` and to each other."""
    u = unit(np.cross(a, [1.0, 0.0, 0.0] if abs(a[0]) < 0.9 else [0.0, 1.0, 0.0]))
    return u, np.cross(a, u)


def angle_deg(a, b) -> float:
    """Angle between two lines (their signs ignored), degrees."""
    return math.degrees(math.atan2(float(np.linalg.norm(np.cross(a, b))), abs(float(np.dot(a, b)))))


def off_line(p, point, axis) -> float:
    """Distance of ``p`` from the line ``point + t * axis``."""
    d = np.asarray(p) - point
    return float(np.linalg.norm(d - (d @ axis) * axis))


def noisy(rng, n: int, noise) -> np.ndarray:
    """Gaussian noise (n,3), ``noise`` a sigma or one per point."""
    return rng.normal(size=(n, 3)) * np.reshape(noise, (-1, 1))


def plane_points(rng, n=150, size=40.0, noise=NOISE) -> np.ndarray:
    """A ``size`` mm square of the plane through ORIGIN with normal NORMAL."""
    u, v = across(NORMAL)
    s, t = rng.uniform(-size / 2, size / 2, (2, n))
    return ORIGIN + s[:, None] * u + t[:, None] * v + noisy(rng, n, noise)


def cylinder_points(rng, r, arc_deg, length, n=300, noise=NOISE) -> tuple[np.ndarray, np.ndarray]:
    """``arc_deg`` of the round and ``length`` mm of a cylinder around ORIGIN + t * AXIS, and the
    normals (with 2° of noise, like a mesh's)."""
    u, v = across(AXIS)
    phi = 0.4 + rng.uniform(0.0, math.radians(arc_deg), n)
    t = rng.uniform(-length / 2, length / 2, n)
    radial = np.cos(phi)[:, None] * u + np.sin(phi)[:, None] * v
    P = ORIGIN + t[:, None] * AXIS + r * radial + noisy(rng, n, noise)
    return P, radial + rng.normal(0.0, math.radians(2.0), (n, 3))


def circle_points(rng, r, arc_deg, n=60, noise=NOISE) -> np.ndarray:
    """``arc_deg`` of a circle around ORIGIN in the plane with normal NORMAL."""
    u, v = across(NORMAL)
    phi = 0.4 + rng.uniform(0.0, math.radians(arc_deg), n)
    return ORIGIN + r * (np.cos(phi)[:, None] * u + np.sin(phi)[:, None] * v) + noisy(rng, n, noise)


def honesty(err, sig) -> tuple[float, float]:
    """``(share of |err| <= 2 sig, mean of (err / sig)^2)`` over trials."""
    z = np.abs(np.asarray(err)) / np.asarray(sig)
    return float(np.mean(z <= 2.0)), float(np.mean(z * z))


TWO_D = {"normal", "axis", "point", "center"}  # a direction or a position across one: σ is the RMS of the total error


def assert_honest(name: str, quantity: str, err, sig) -> None:
    """±2σ covers the truth 90-99 % of the time (95.4 % expected) for a radius or an offset; for a
    direction or a position across one (σ: the RMS of a 2D error) 95-98 % is expected, so 99.5 or
    100 % of 200 trials happens by chance and only the lower limit applies. The mean of
    ``(err / σ)^2`` (1 expected for both) catches ± values too large or too small."""
    cover, z2 = honesty(err, sig)
    top = 1.0 if quantity in TWO_D else 0.99
    assert 0.90 <= cover <= top and 0.7 <= z2 <= 1.35, \
        f"{name}: {quantity}: ±2σ covers {cover:.1%}, mean (err/σ)^2 {z2:.2f}"


# --------------------------------------------------------------------------- Monte Carlo cases
def plane_trials(trials=TRIALS, seed=100, weighted=True) -> dict:
    """Planes with noise from 0.02 to 0.1 mm per point (sigma given), or 0.05 mm (sigma not given)."""
    rng = np.random.default_rng(seed)
    out = {"normal": ([], []), "offset": ([], [])}
    for _ in range(trials):
        s = rng.uniform(0.02, 0.1, 150) if weighted else np.full(150, NOISE)
        fit = fit_plane(plane_points(rng, noise=s), s if weighted else None)
        out["normal"][0].append(angle_deg(fit.normal, NORMAL))
        out["normal"][1].append(fit.normal_sigma_deg)
        out["offset"][0].append((fit.point - ORIGIN) @ NORMAL)
        out["offset"][1].append(fit.offset_sigma_mm)
    return out


def cylinder_trials(r, arc_deg, length, with_normals, weighted, trials=TRIALS, seed=200) -> dict:
    """Cylinders with noise from 0.02 to 0.1 mm per point (sigma given), or 0.05 mm (not given)."""
    rng = np.random.default_rng(seed)
    out = {"radius": ([], []), "axis": ([], []), "point": ([], [])}
    for _ in range(trials):
        s = rng.uniform(0.02, 0.1, 200) if weighted else np.full(200, NOISE)
        P, N = cylinder_points(rng, r, arc_deg, length, n=200, noise=s)
        fit = fit_cylinder(P, s if weighted else None, normals=N if with_normals else None)
        out["radius"][0].append(fit.radius_mm - r)
        out["radius"][1].append(fit.radius_sigma_mm)
        out["axis"][0].append(angle_deg(fit.axis, AXIS))
        out["axis"][1].append(fit.axis_sigma_deg)
        out["point"][0].append(off_line(fit.point, ORIGIN, AXIS))
        out["point"][1].append(fit.point_sigma_mm)
    return out


def circle_trials(r, arc_deg, given_normal, weighted, trials=TRIALS, seed=300) -> dict:
    """Hole edges (60 points) with noise from 0.02 to 0.1 mm (sigma given), or 0.05 mm (not given)."""
    rng = np.random.default_rng(seed)
    out = {"radius": ([], []), "center": ([], [])}
    for _ in range(trials):
        s = rng.uniform(0.02, 0.1, 60) if weighted else np.full(60, NOISE)
        fit = fit_circle(circle_points(rng, r, arc_deg, noise=s), s if weighted else None,
                         normal=NORMAL if given_normal else None)
        d = fit.center - ORIGIN
        out["radius"][0].append(fit.radius_mm - r)
        out["radius"][1].append(fit.radius_sigma_mm)
        out["center"][0].append(float(np.linalg.norm(d - (d @ NORMAL) * NORMAL)))  # in the plane
        out["center"][1].append(fit.center_sigma_mm)
    return out


MONTE_CARLO = {
    "plane, sigma per point": lambda: plane_trials(weighted=True),
    "plane, no sigma": lambda: plane_trials(weighted=False, seed=101),
    "cylinder r 8, 180°, normals, sigma per point": lambda: cylinder_trials(8.0, 180, 20.0, True, True),
    "cylinder r 25, 90°, no normals, no sigma": lambda: cylinder_trials(25.0, 90, 30.0, False, False, seed=201),
    "circle r 4, full, normal given, sigma per point": lambda: circle_trials(4.0, 360, True, True),
    "circle r 12.5, 120°, plane fitted, no sigma": lambda: circle_trials(12.5, 120, False, False, seed=301),
}


@pytest.mark.parametrize("case", list(MONTE_CARLO))
def test_two_sigma_covers_the_truth_about_95_percent_of_the_time(case):
    for quantity, (err, sig) in MONTE_CARLO[case]().items():
        assert_honest(case, quantity, err, sig)


# --------------------------------------------------------------------------- accuracy
def test_plane_finds_a_tilted_plane():
    rng = np.random.default_rng(1)
    P = plane_points(rng, n=200)
    fit = fit_plane(P, np.full(len(P), NOISE))
    assert angle_deg(fit.normal, NORMAL) < 4 * fit.normal_sigma_deg
    assert abs((fit.point - ORIGIN) @ NORMAL) < 4 * fit.offset_sigma_mm
    # the ± values as expected: a 40 mm square of 200 points tilts by NOISE / sqrt(sum of squares)
    # per direction (0.018°), and shifts by NOISE / sqrt(200)
    tilt = math.degrees(NOISE / math.sqrt(200 * 40.0**2 / 12))
    assert fit.normal_sigma_deg == pytest.approx(math.sqrt(2) * tilt, rel=0.2)
    assert fit.offset_sigma_mm == pytest.approx(NOISE / math.sqrt(200), rel=0.2)
    assert fit.rms_mm == pytest.approx(NOISE, rel=0.15)
    assert 4 * NOISE < fit.flatness_mm < 8 * NOISE
    assert fit.n == 200 and fit.normal[np.argmax(np.abs(fit.normal))] > 0


CYLINDERS = [  # radius, arc of the round (°), length (mm)
    (4.0, 360, 30.0),  # a thin pin
    (8.0, 180, 20.0),  # half a shaft
    (25.0, 90, 30.0),  # a quarter of a boss
    (50.0, 90, 40.0),  # a quarter of a large bore
    (25.0, 360, 5.0),  # short: a ring 5 mm high
    (10.0, 180, 4.0),  # short and half
]


@pytest.mark.parametrize("with_normals", [True, False], ids=["normals", "no normals"])
@pytest.mark.parametrize("r, arc, length", CYLINDERS)
def test_cylinder_finds_full_partial_and_short_cylinders(r, arc, length, with_normals):
    rng = np.random.default_rng(int(10 * r + arc + length))
    P, N = cylinder_points(rng, r, arc, length)
    fit = fit_cylinder(P, np.full(len(P), NOISE), normals=N if with_normals else None)
    assert abs(fit.radius_mm - r) < 4 * fit.radius_sigma_mm
    assert angle_deg(fit.axis, AXIS) < 4 * fit.axis_sigma_deg
    assert off_line(fit.point, ORIGIN, AXIS) < 4 * fit.point_sigma_mm
    # small at 0.05 mm noise and 300 points: the radius within 0.05 mm, the axis within 0.5° (a
    # short cylinder's is the least sure: about 0.25° 1σ at 4-5 mm long), the axis within 0.1 mm
    assert abs(fit.radius_mm - r) < 0.05 and angle_deg(fit.axis, AXIS) < 0.5
    assert off_line(fit.point, ORIGIN, AXIS) < 0.1
    assert fit.rms_mm == pytest.approx(NOISE, rel=0.15)
    # point: on the axis, nearest the centroid
    assert abs((P.mean(axis=0) - fit.point) @ fit.axis) < 1e-6


def test_cylinder_from_a_given_axis_and_its_sign():
    rng = np.random.default_rng(3)
    P, _ = cylinder_points(rng, 7.0, 180, 15.0)
    for start in (AXIS + [0.1, 0.05, 0.0], -AXIS):  # 7° off; and the other way round
        fit = fit_cylinder(P, axis0=start)
        assert abs(fit.radius_mm - 7.0) < 4 * fit.radius_sigma_mm
        assert angle_deg(fit.axis, AXIS) < 4 * fit.axis_sigma_deg
        assert fit.axis @ start > 0  # the sign follows axis0


@pytest.mark.parametrize("given_normal", [True, False], ids=["normal given", "plane fitted"])
@pytest.mark.parametrize("r, arc", [(4.0, 360), (12.5, 360), (4.0, 120), (12.5, 120), (40.0, 180)])
def test_circle_finds_full_and_partial_holes(r, arc, given_normal):
    rng = np.random.default_rng(int(10 * r + arc))
    P = circle_points(rng, r, arc)
    fit = fit_circle(P, np.full(len(P), NOISE), normal=NORMAL if given_normal else None)
    d = fit.center - ORIGIN
    assert abs(fit.radius_mm - r) < 4 * fit.radius_sigma_mm
    assert np.linalg.norm(d - (d @ NORMAL) * NORMAL) < 4 * fit.center_sigma_mm
    assert angle_deg(fit.normal, NORMAL) < 1.0
    # small at 0.05 mm noise and 60 points: the radius within 0.06 mm, the centre within 0.1 mm
    assert abs(fit.radius_mm - r) < 0.06 and np.linalg.norm(d - (d @ NORMAL) * NORMAL) < 0.1
    assert fit.rms_mm == pytest.approx(NOISE, rel=0.3)
    assert fit.diameter_mm == 2 * fit.radius_mm and fit.diameter_sigma_mm == 2 * fit.radius_sigma_mm
    if given_normal:
        assert np.allclose(fit.normal, NORMAL)


def test_results_are_plain_json():
    rng = np.random.default_rng(4)
    P, N = cylinder_points(rng, 6.0, 360, 10.0)
    fits = {"plane": fit_plane(plane_points(rng)), "cylinder": fit_cylinder(P, normals=N),
            "circle": fit_circle(circle_points(rng, 5.0, 360))}
    keys = {
        "plane": {"point", "normal", "rms_mm", "flatness_mm", "n", "normal_sigma_deg", "offset_sigma_mm"},
        "cylinder": {"point", "axis", "radius_mm", "rms_mm", "n", "radius_sigma_mm", "axis_sigma_deg",
                     "point_sigma_mm", "diameter_mm", "diameter_sigma_mm"},
        "circle": {"center", "normal", "radius_mm", "rms_mm", "n", "radius_sigma_mm", "center_sigma_mm",
                   "diameter_mm", "diameter_sigma_mm"},
    }
    for kind, fit in fits.items():
        d = fit.to_dict()
        assert d["kind"] == kind and set(d) == keys[kind] | {"kind"}
        assert json.loads(json.dumps(d)) == d
        assert all(type(v) in (str, int, float, list) for v in d.values())
        assert type(d["n"]) is int and all(type(x) is float for x in d.get("normal", d.get("axis")))


# --------------------------------------------------------------------------- the scale, the weights
def test_scale_catches_a_too_small_sigma_and_keeps_a_right_or_large_one():
    rng = np.random.default_rng(7)
    P, N = cylinder_points(rng, 8.0, 180, 20.0, noise=0.1)
    fits = {s: fit_cylinder(P, np.full(len(P), s), normals=N) for s in (0.02, 0.1, 0.3)}
    estimated = fit_cylinder(P, normals=N)
    assert all(f.radius_mm == pytest.approx(estimated.radius_mm, abs=1e-9) for f in fits.values())
    raw = fits[0.3].radius_sigma_mm / 3  # 0.3 is too large: kept as it is (scale 1), so this is 0.1 unscaled
    for name in ("radius_sigma_mm", "axis_sigma_deg", "point_sigma_mm"):
        right = getattr(fits[0.1], name)
        assert getattr(fits[0.02], name) == pytest.approx(right, rel=0.1), name  # 5x too small: scaled up
        assert getattr(estimated, name) == pytest.approx(right, rel=0.1), name  # from the residuals
        assert getattr(fits[0.3], name) == pytest.approx(3 * right, rel=0.12), name  # too large: kept
    assert raw <= fits[0.1].radius_sigma_mm <= 1.15 * raw  # a right sigma is not inflated


def test_points_with_a_large_sigma_pull_less():
    rng = np.random.default_rng(11)
    u, v = across(NORMAL)
    # plane: 40 points 1 mm off in one corner
    bad = ORIGIN + 15 * u + 15 * v + rng.uniform(-3, 3, (40, 1)) * u + rng.uniform(-3, 3, (40, 1)) * v + NORMAL
    P = np.vstack([plane_points(rng, 200, noise=0.02), bad])
    s = np.r_[np.full(200, 0.02), np.full(40, 2.0)]
    weighted, equal = fit_plane(P, s), fit_plane(P)
    assert angle_deg(weighted.normal, NORMAL) < 0.1 * angle_deg(equal.normal, NORMAL)
    assert abs((weighted.point - ORIGIN) @ NORMAL) < 0.1 * abs((equal.point - ORIGIN) @ NORMAL)
    # cylinder: 60 points 0.5 mm outside (a fillet's start, say)
    good, N = cylinder_points(rng, 8.0, 180, 20.0, n=200, noise=0.02)
    out, N2 = cylinder_points(rng, 8.5, 180, 20.0, n=60, noise=0.02)
    P, N, s = np.vstack([good, out]), np.vstack([N, N2]), np.r_[np.full(200, 0.02), np.full(60, 1.0)]
    assert abs(fit_cylinder(P, s, normals=N).radius_mm - 8.0) < 0.1 * abs(fit_cylinder(P, normals=N).radius_mm - 8.0)
    # circle: 20 edge points 0.4 mm outside
    P = np.vstack([circle_points(rng, 4.0, 360, noise=0.02), circle_points(rng, 4.4, 360, n=20, noise=0.02)])
    s = np.r_[np.full(60, 0.02), np.full(20, 1.0)]
    assert abs(fit_circle(P, s).radius_mm - 4.0) < 0.1 * abs(fit_circle(P).radius_mm - 4.0)


# --------------------------------------------------------------------------- bad input
def _bad_cases():
    rng = np.random.default_rng(5)
    plane = plane_points(rng)
    line = ORIGIN + np.linspace(-10.0, 10.0, 20)[:, None] * AXIS
    at_one_place = np.tile(ORIGIN, (10, 1))
    u, v = across(AXIS)
    phi = np.linspace(0.0, 2 * np.pi, 60, endpoint=False)
    ring = ORIGIN + 10.0 * (np.cos(phi)[:, None] * u + np.sin(phi)[:, None] * v)  # a circle: a cylinder of no length
    sliver = circle_points(rng, 50.0, 1.0)  # 1° of a 50 mm circle: its 0.002 mm sagitta drowns in the noise
    nan = plane.copy()
    nan[3, 1] = np.nan
    return [
        ("too few plane points", lambda: fit_plane(plane[:3]), "at least 4 points"),
        ("too few cylinder points", lambda: fit_cylinder(plane[:5]), "at least 6 points"),
        ("too few circle points", lambda: fit_circle(plane[:3]), "at least 4 points"),
        ("plane on a line", lambda: fit_plane(line), "straight line"),
        ("cylinder on a line", lambda: fit_cylinder(line), "straight line"),
        ("circle on a line", lambda: fit_circle(line), "straight line"),
        ("all at one place", lambda: fit_plane(at_one_place), "one place"),
        ("cylinder on a flat patch", lambda: fit_cylinder(plane, NOISE), "do not fix a cylinder"),
        ("cylinder on a ring", lambda: fit_cylinder(ring), "do not fix a cylinder"),
        ("circle on a sliver of arc", lambda: fit_circle(sliver, NOISE), "do not fix a circle"),
        ("circle seen edge-on", lambda: fit_circle(ring, normal=u), "straight line in the circle's plane"),
        ("zero normal", lambda: fit_circle(ring, normal=[0, 0, 0]), "must not be zero"),
        ("2D points", lambda: fit_plane(plane[:, :2]), r"\(N, 3\)"),
        ("NaN", lambda: fit_plane(nan), "NaN"),
        ("negative sigma", lambda: fit_plane(plane, -np.ones(len(plane))), "positive"),
        ("sigma per point", lambda: fit_plane(plane, np.ones(5)), "one value per point"),
        ("normals per point", lambda: fit_cylinder(ring, normals=ring[:-1]), "normals must be"),
    ]


@pytest.mark.parametrize("name, call, message", _bad_cases(), ids=[c[0] for c in _bad_cases()])
def test_bad_input_raises_a_plain_value_error(name, call, message):
    with pytest.raises(ValueError, match=message):
        call()
