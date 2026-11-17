"""ohs.measure.relate: angles and distances between two fits, each with a ± from both fits.

* Known truth: planes, axes, a plane and an axis, two hole centres; the viewer's ``PARALLEL_DEG``
  rule decides which distance there is.
* The same numbers as the viewer: ``tests/data/relations.json`` holds shapes and the values
  ``relate()`` in ``ui/src/lib/fits.ts`` gives for them (``ui/src/lib/relate.test.ts`` checks the UI
  against the same file); the engine must agree to 1e-6, labels and notes included.
* Fits, the dicts their ``to_dict()`` give, a :class:`RegionFit` and its dict all go in.
* Monte Carlo, 200 seeded trials per case, fits on noisy points as in ``test_measure_fit``: ±2σ
  covers the true relation 90-99 % of the time (95.4 % expected) for a quantity of one component;
  for the magnitude of a two-component error (an angle near 0°, the distance of nearly coaxial
  axes) 95-98 % is expected, so only the lower limit applies.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from ohs.measure import PARALLEL_DEG, CircleFit, CylinderFit, PlaneFit, Relation, fit_circle, fit_cylinder, fit_plane, relate

FIXTURE = Path(__file__).parent / "data" / "relations.json"
TRIALS = 200
NOISE = 0.05


def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v)


def rot(axis, deg: float, v) -> np.ndarray:
    """``v`` turned by ``deg`` about ``axis`` (right hand)."""
    a, v, t = unit(axis), np.asarray(v, float), math.radians(deg)
    return v * math.cos(t) + np.cross(a, v) * math.sin(t) + a * (a @ v) * (1 - math.cos(t))


Z = unit([0.2, -0.35, 0.9])
X = unit(np.cross([0.0, 1.0, 0.0], Z))
Y = np.cross(Z, X)
O = np.array([12.0, -30.0, 45.0])


def plane(point, normal, so=0.01, sn=0.02) -> PlaneFit:
    return PlaneFit(point=np.asarray(point, float), normal=unit(normal), rms_mm=0.03, flatness_mm=0.2, n=500,
                    normal_sigma_deg=sn, offset_sigma_mm=so)


def cylinder(point, axis, r=7.0, sa=0.1, sp=0.02) -> CylinderFit:
    return CylinderFit(point=np.asarray(point, float), axis=unit(axis), radius_mm=r, rms_mm=0.03, n=500,
                       radius_sigma_mm=0.01, axis_sigma_deg=sa, point_sigma_mm=sp)


def circle(center, normal, r=4.0, sc=0.02) -> CircleFit:
    return CircleFit(center=np.asarray(center, float), normal=unit(normal), radius_mm=r, rms_mm=0.03, n=60,
                     radius_sigma_mm=0.01, center_sigma_mm=sc)


# --------------------------------------------------------------------------- known truth
def test_two_planes():
    square = relate(plane(O, Z), plane(O + 20 * X, X))
    assert square.get("angle").value == pytest.approx(90.0) and square.get("distance") is None
    assert not square.parallel and square.notes == ["Not parallel (more than 2° apart): there is no one distance between them."]
    tilted = relate(plane(O, Z), plane(O + 5 * Y, rot(X, 30, Z)))
    assert tilted.get("angle").value == pytest.approx(30.0)
    # parallel, opposite normals, the second plane's centre straight above the first's: the ± of the
    # distance is the root-sum-square of the offsets' ± (the tilts move nothing there)
    above = relate(plane(O, Z, 0.01), plane(O + 10 * Z, -Z, 0.02))
    d = above.get("distance")
    assert above.parallel and above.get("angle").label == "Angle (parallel)"
    assert d.value == pytest.approx(10.0) and d.sigma == pytest.approx(math.hypot(0.01, 0.02), rel=1e-6) and d.unit == "mm"
    # 30 mm to the side: the tilts move the distance measured there, so its ± grows
    aside = relate(plane(O, Z, 0.01, 0.2), plane(O + 10 * Z + 30 * X, -Z, 0.02, 0.3)).get("distance")
    tilt = math.radians(math.hypot(0.2, 0.3))  # the mean normal's tilt, along the 30 mm: 30^2 (σ_a² + σ_b²) / 8
    assert aside.value == pytest.approx(10.0) and aside.sigma == pytest.approx(math.sqrt(0.01 ** 2 + 0.02 ** 2 + 900 * tilt ** 2 / 8), rel=1e-6)
    # just inside and just outside the viewer's parallel rule
    assert relate(plane(O, Z), plane(O + 8 * Z, rot(Y, PARALLEL_DEG - 0.1, Z))).get("distance") is not None
    assert relate(plane(O, Z), plane(O + 8 * Z, rot(Y, PARALLEL_DEG + 0.1, Z))).get("distance") is None


def test_two_axes():
    par = relate(cylinder(O, X), cylinder(O + 25 * Y + 7 * X, -X, 4.0))
    assert par.parallel and par.get("angle").value == pytest.approx(0.0, abs=1e-6)
    assert par.get("distance").value == pytest.approx(25.0) and par.get("distance").label == "Distance between the axes"
    skew = relate(cylinder(O, X), cylinder(O + 15 * Z + 3 * X, rot(Z, 60, X)))
    assert skew.get("angle").value == pytest.approx(60.0) and skew.get("distance").value == pytest.approx(15.0)
    assert skew.get("distance").label == "Closest distance of the axes" and skew.get("centre_distance") is None
    coaxial = relate(cylinder(O, Z), cylinder(O + 12 * Z, Z, 3.0)).get("distance")
    assert coaxial.value == pytest.approx(0.0, abs=1e-9) and coaxial.sigma > 0


def test_two_hole_centres():
    r = relate(circle(O, Z, sc=0.03), circle(O + 30 * X + 40 * Y, Z, 2.5, sc=0.04))
    c = r.get("centre_distance")
    assert c.value == pytest.approx(50.0) and c.label == "Centre to centre" and c.unit == "mm"
    # both centres' ± lie in the face, along the line between them: half of each RMS 2D error's square
    assert c.sigma == pytest.approx(math.sqrt((0.03 ** 2 + 0.04 ** 2) / 2), rel=1e-6)
    # a circle's normal has no ± (fit_circle takes it as given or does not keep it): no ± for the angle
    assert r.get("angle").sigma is None and r.get("distance").sigma is not None


def test_a_plane_and_an_axis():
    along = relate(plane(O, Z), cylinder(O + 12 * Z + 30 * X, Y))
    assert along.parallel and along.get("angle").value == pytest.approx(0.0, abs=1e-6)
    assert along.get("distance").value == pytest.approx(12.0) and along.get("distance").label == "Axis to plane"
    steep = relate(cylinder(O + 12 * Z, rot(Y, 60, Z)), plane(O, Z))  # either order
    assert steep.get("angle").value == pytest.approx(30.0) and steep.get("distance") is None
    assert steep.notes == ["The axis is not parallel to the plane (more than 2°): there is no one distance between them."]
    hole = relate(plane(O, Z, 0.01), circle(O + 6 * Z, Z))
    assert hole.get("angle").label == "Angle of the hole's axis to the plane" and hole.get("angle").value == pytest.approx(90.0)
    assert hole.get("distance").value == pytest.approx(6.0) and hole.get("distance").label == "Hole centre to plane"
    # the hole's centre is straight above the plane's point and its ± lies in its own (parallel)
    # face: only the plane's offset counts (the centre's error along its axis is not known)
    assert hole.get("distance").sigma == pytest.approx(0.01, rel=1e-6)


def test_fits_dicts_and_region_fits_all_go_in():
    from ohs.measure import RegionFit

    a, b = plane(O, Z), cylinder(O + 12 * Z + 30 * X, rot(X, 1.0, Y))
    want = relate(a, b).to_dict()
    assert json.loads(json.dumps(want)) == want
    assert relate(a.to_dict(), b.to_dict()).to_dict() == want
    region = RegionFit(kind="cylinder", fit=b, faces=np.zeros(0, np.int64), edge=np.zeros(0, np.int64), warnings=[],
                       tolerance_mm=None, angle_tol_deg=None, mean_sigma_mm=None)
    assert relate(a, region).to_dict() == want and relate(a, region.to_dict()).to_dict() == want
    assert relate({"kind": "plane", "shape": a.to_dict()}, b).to_dict() == want  # the viewer's {kind, shape}
    assert set(want) == {"kinds", "parallel", "values", "notes"} and want["kinds"] == ["plane", "cylinder"]
    assert set(want["values"][0]) == {"name", "label", "value", "sigma", "unit"}
    with pytest.raises(ValueError, match="not a fitted shape"):
        relate(a, {"kind": "sphere"})


# --------------------------------------------------------------------------- the same numbers as the viewer
def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", load_fixture()["relations"], ids=lambda c: c["name"])
def test_the_same_numbers_as_the_viewer(case):
    for a, b in ((case["a"], case["b"]), (case["b"], case["a"])):
        r = relate(a, b)
        assert [q.label for q in r.values] == [row["label"] for row in case["rows"]]
        for q, row in zip(r.values, case["rows"]):
            assert abs(q.value - row["x"]) <= 1e-6, (q.label, q.value, row["x"])
        assert r.notes == case["notes"]


# --------------------------------------------------------------------------- Monte Carlo
def across(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    u = unit(np.cross(a, [1.0, 0.0, 0.0] if abs(a[0]) < 0.9 else [0.0, 1.0, 0.0]))
    return u, np.cross(a, u)


def noisy_plane(rng, point, normal, size=40.0, n=150) -> PlaneFit:
    u, v = across(normal)
    s, t = rng.uniform(-size / 2, size / 2, (2, n))
    sig = rng.uniform(0.02, 0.1, n)
    P = point + s[:, None] * u + t[:, None] * v + rng.normal(size=(n, 3)) * sig[:, None]
    return fit_plane(P, sig)


def noisy_cylinder(rng, point, axis, r=8.0, arc_deg=360.0, length=20.0, n=200) -> CylinderFit:
    u, v = across(axis)
    phi = rng.uniform(0.0, math.radians(arc_deg), n)
    t = rng.uniform(-length / 2, length / 2, n)
    radial = np.cos(phi)[:, None] * u + np.sin(phi)[:, None] * v
    P = point + t[:, None] * axis + r * radial + rng.normal(size=(n, 3)) * NOISE
    return fit_cylinder(P, np.full(n, NOISE), normals=radial)


def noisy_circle(rng, center, normal, r=5.0, n=60) -> CircleFit:
    u, v = across(normal)
    phi = rng.uniform(0.0, 2 * math.pi, n)
    P = center + r * (np.cos(phi)[:, None] * u + np.sin(phi)[:, None] * v) + rng.normal(size=(n, 3)) * NOISE
    return fit_circle(P, np.full(n, NOISE), normal=normal)


A_SIDE = unit(np.cross(Z, [0.3, 1.0, 0.1]))  # a direction square to Z
MONTE_CARLO = {
    # name: (make the two fits from rng, {relation name: (truth, two components?)})
    "parallel planes 10 mm apart, centres 30 mm aside": (
        lambda rng: (noisy_plane(rng, O, Z), noisy_plane(rng, O + 10 * Z + 30 * X, Z)),
        {"angle": (0.0, True), "distance": (10.0, False)}),
    "planes at 90 degrees": (
        lambda rng: (noisy_plane(rng, O, Z), noisy_plane(rng, O + 30 * X, X)), {"angle": (90.0, False)}),
    "planes at 40 degrees": (
        lambda rng: (noisy_plane(rng, O, Z), noisy_plane(rng, O + 30 * X, rot(Y, 40, Z))), {"angle": (40.0, False)}),
    "parallel axes 25 mm apart, 15 mm along": (
        lambda rng: (noisy_cylinder(rng, O, X), noisy_cylinder(rng, O + 25 * Y + 15 * X, X)),
        {"angle": (0.0, True), "distance": (25.0, False)}),
    "coaxial axes": (
        lambda rng: (noisy_cylinder(rng, O, X), noisy_cylinder(rng, O + 25 * X, X, r=5.0)), {"distance": (0.0, True)}),
    "skew axes at 60 degrees, 15 mm apart": (
        lambda rng: (noisy_cylinder(rng, O, X), noisy_cylinder(rng, O + 15 * Z + 8 * X + 6 * Y, rot(Z, 60, X))),
        {"angle": (60.0, False), "distance": (15.0, False)}),
    "an axis along a plane, 12 mm above it, 20 mm aside": (
        lambda rng: (noisy_plane(rng, O, Z), noisy_cylinder(rng, O + 12 * Z + 20 * X, Y)),
        {"angle": (0.0, False), "distance": (12.0, False)}),
    "an axis square to a plane": (
        lambda rng: (noisy_plane(rng, O, Z), noisy_cylinder(rng, O + 30 * Z, Z)), {"angle": (90.0, True)}),
    "two holes in one face, 40 mm apart": (
        lambda rng: (noisy_circle(rng, O, Z), noisy_circle(rng, O + 40 * X, Z, r=3.0)),
        {"centre_distance": (40.0, False), "distance": (40.0, False)}),
    "a hole in a side face, 9 mm above a plane": (
        lambda rng: (noisy_plane(rng, O, Z), noisy_circle(rng, O + 9 * Z + 25 * A_SIDE, A_SIDE)),
        {"distance": (9.0, False)}),
}


def trials(case: str, seed: int) -> dict:
    make, truth = MONTE_CARLO[case]
    rng = np.random.default_rng(seed)
    out = {name: ([], []) for name in truth}
    for _ in range(TRIALS):
        r = relate(*make(rng))
        for name in truth:
            q = r.get(name)
            out[name][0].append(q.value - truth[name][0])
            out[name][1].append(q.sigma)
    return out


@pytest.mark.parametrize("case", list(MONTE_CARLO))
def test_two_sigma_covers_the_true_relation_about_95_percent_of_the_time(case):
    seed = 400 + list(MONTE_CARLO).index(case)
    for name, (err, sig) in trials(case, seed).items():
        z = np.abs(np.asarray(err)) / np.asarray(sig)
        cover, z2 = float(np.mean(z <= 2.0)), float(np.mean(z * z))
        two = MONTE_CARLO[case][1][name][1]
        top = 1.0 if two else 0.99
        assert 0.90 <= cover <= top and 0.6 <= z2 <= 1.35, f"{case}: {name}: ±2σ covers {cover:.1%}, mean (err/σ)^2 {z2:.2f}"


@pytest.mark.parametrize("arc", [180.0, 90.0])
def test_on_a_partial_arc_the_plus_minus_is_within_half_again_of_the_spread(arc):
    """A cylinder seen over part of its round knows its point better one way than the other; its fit
    keeps only the RMS of the two, and the ± here takes them as alike. The skew axes above with both
    cylinders seen over 180° then get a ± too large (±2σ covers 99.5 %, mean (err/σ)^2 0.50), over 90°
    one too small (89.5 %, 1.46): within a factor 1.5 of the spread either way (module docstring of
    ohs.measure.relate). The trials above use whole rounds, where the two are alike."""
    rng = np.random.default_rng(500)
    err, sig = [], []
    for _ in range(TRIALS):
        q = relate(noisy_cylinder(rng, O, X, arc_deg=arc),
                   noisy_cylinder(rng, O + 15 * Z + 8 * X + 6 * Y, rot(Z, 60, X), arc_deg=arc)).get("distance")
        err.append(q.value - 15.0)
        sig.append(q.sigma)
    z2 = float(np.mean((np.asarray(err) / np.asarray(sig)) ** 2))
    assert 1 / 1.5 ** 2 <= z2 <= 1.5 ** 2, f"{arc}°: mean (err/σ)^2 {z2:.2f}"


def test_a_relation_is_a_relation():
    assert isinstance(relate(plane(O, Z), plane(O, X)), Relation)
