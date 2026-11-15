"""Simulator nominal geometry: calibration truth, emitter layout, lens model."""
import itertools

import cv2
import numpy as np
import pytest

from ohs.geometry import rotation_angle_deg
from ohs.sim.nominal import nominal_calibration, nominal_emitters, nominal_imu_position
from ohs.sim.render import distort_normalized, project_to_raw, raw_pixel_rays, undistort_normalized
from ohs.types import ScannerCalibration


def test_laser_planes_match_architecture_section7():
    cal = nominal_calibration()
    v, h = cal.lasers["vertical"], cal.lasers["horizontal"]
    assert np.allclose(v.normal, [0.9473, 0.0, 0.3205], atol=2e-4) and v.d == pytest.approx(-109.8, abs=0.05)
    assert np.allclose(h.normal, [0.0, -0.9594, 0.2822], atol=2e-4) and h.d == pytest.approx(-71.67, abs=0.05)
    # both sheets cross the stereo midline 254 mm in front of the left camera
    z_v = (-v.d - v.normal[0] * 30.0) / v.normal[2]
    z_h = -h.d / h.normal[2]
    assert z_v == pytest.approx(254.0, abs=0.05) and z_h == pytest.approx(254.0, abs=0.05)


def test_emitters_consistent_with_planes():
    cal = nominal_calibration()
    em = nominal_emitters()
    assert np.allclose(em["dots"]["origin"], [30.0, -42.0, -2.0])
    assert np.allclose(em["dots"]["direction"], [0, 0, 1]) and tuple(em["dots"]["fov_deg"]) == (80.0, 64.4)
    assert em["dots"]["n_dots"] == 10000
    expected_origin = {"vertical": [116.4, 0.0, -1.4], "horizontal": [30.0, -75.1, -1.2]}
    for name, plane in cal.lasers.items():
        e = em[name]
        assert np.allclose(e["origin"], expected_origin[name], atol=0.1)
        assert abs(plane.distance(e["origin"][None])[0]) < 1e-9
        assert abs(e["direction"] @ plane.normal) < 1e-12 and abs(e["fan_axis"] @ plane.normal) < 1e-12
        assert np.linalg.norm(e["direction"]) == pytest.approx(1.0) and e["fan_deg"] == 60.0
    # fan axes: vertical laser spreads along camera y, horizontal along camera x
    assert np.allclose(em["vertical"]["fan_axis"], [0, 1, 0]) and np.allclose(em["horizontal"]["fan_axis"], [1, 0, 0])
    # a different baseline shifts everything with the left camera
    em62 = nominal_emitters(baseline=62.0)
    assert np.allclose(em62["dots"]["origin"] - em["dots"]["origin"], [1.0, 0, 0])
    assert np.allclose(nominal_imu_position(60.0), [0.0, 0.0, -31.0])


def test_four_laser_layout_adds_the_mirrored_pair():
    """lasers=4: the 2-laser planes unchanged, plus vertical_2 (left, mirror of vertical) and
    horizontal_2 (below the cameras, beside the yoke); all four sheets cross the centre line at 254 mm."""
    two, four = nominal_calibration(), nominal_calibration(lasers=4)
    assert list(two.lasers) == ["vertical", "horizontal"]
    assert list(four.lasers) == ["vertical", "horizontal", "vertical_2", "horizontal_2"]
    for name in two.lasers:
        assert np.array_equal(four.lasers[name].normal, two.lasers[name].normal) and four.lasers[name].d == two.lasers[name].d
    v, v2, h, h2 = (four.lasers[n] for n in ("vertical", "vertical_2", "horizontal", "horizontal_2"))
    # vertical_2 is the mirror of vertical about the stereo midline x = 30 mm
    assert np.allclose(v2.normal * [-1, 1, 1], v.normal, atol=1e-12) or np.allclose(-v2.normal * [-1, 1, 1], v.normal, atol=1e-12)
    for pl in (v, v2):
        z = (-pl.d - pl.normal[0] * 30.0) / pl.normal[2]
        assert z == pytest.approx(254.0, abs=0.05)
    for pl in (h, h2):
        assert -pl.d / pl.normal[2] == pytest.approx(254.0, abs=0.05) and abs(pl.normal[0]) < 1e-12
    em = nominal_emitters(lasers=4)
    assert set(em) == {"dots", "vertical", "horizontal", "vertical_2", "horizontal_2"}
    # module centres of hardware/scanner.scad (lasers = 4): left vertical at x = -92, low horizontal at
    # x = 58, 56 mm below the cameras; emission points 17.5 mm ahead of the module centres
    assert np.allclose(em["vertical_2"]["origin"], [60.0 - 116.4, 0.0, -1.4], atol=0.1)
    a = np.arctan(56.0 / 272.0)
    assert np.allclose(em["horizontal_2"]["origin"], [30.0 + 58.0, 56.0 - 17.5 * np.sin(a), -18.0 + 17.5 * np.cos(a)], atol=1e-9)
    assert em["vertical_2"]["direction"][0] > 0 and em["horizontal_2"]["direction"][1] < 0  # aim right / up
    assert np.allclose(em["vertical_2"]["fan_axis"], [0, 1, 0]) and np.allclose(em["horizontal_2"]["fan_axis"], [1, 0, 0])
    for name, plane in four.lasers.items():
        e = em[name]
        assert abs(plane.distance(e["origin"][None])[0]) < 1e-9 and plane.d <= 0
        assert abs(e["direction"] @ plane.normal) < 1e-12 and abs(e["fan_axis"] @ plane.normal) < 1e-12
    # the 2-laser emitters are unchanged by the option
    two_em = nominal_emitters(lasers=2)
    assert set(two_em) == {"dots", "vertical", "horizontal"}
    for name in ("vertical", "horizontal"):
        for k, val in two_em[name].items():
            assert np.array_equal(np.asarray(em[name][k]), np.asarray(val))
    with pytest.raises(ValueError):
        nominal_calibration(lasers=3)


def test_layouts_of_the_second_pair():
    """The aims compared for the second pair: "4" is lasers=4 exactly, the others change only it,
    and the renderer draws the sheets of the layout a calibration has."""
    from ohs.sim.nominal import LASER_LAYOUTS
    from ohs.sim.render import _nominal_layout

    four = nominal_calibration(lasers=4)
    assert {"2", "4", "4-straight", "4-cross350", "4-cross450", "4-cross600", "4-apart"} <= set(LASER_LAYOUTS)
    # hybrids: the two new lasers aimed differently
    hybrids = {f"4-v{v}-h{h}" for v in ("250", "350", "450") for h in ("apart", "straight")} | {"4-vapart-h350"}
    assert hybrids <= set(LASER_LAYOUTS)
    em = nominal_emitters(layout="4-v350-hstraight")
    assert np.allclose(em["horizontal_2"]["direction"], [0, 0, 1])
    assert np.array_equal(em["vertical_2"]["normal"], nominal_emitters(layout="4-cross350")["vertical_2"]["normal"])
    assert np.array_equal(nominal_emitters(layout="4-vapart-h350")["vertical_2"]["normal"], nominal_emitters(layout="4-apart")["vertical_2"]["normal"])
    for name, pl in nominal_calibration(layout="4").lasers.items():
        assert np.array_equal(pl.normal, four.lasers[name].normal) and pl.d == four.lasers[name].d
    for layout in LASER_LAYOUTS:
        cal = nominal_calibration(layout=layout)
        em = nominal_emitters(layout=layout)
        assert set(cal.lasers) == set(LASER_LAYOUTS[layout])
        for name in ("vertical", "horizontal"):
            assert np.array_equal(cal.lasers[name].normal, four.lasers[name].normal)
        for name, pl in cal.lasers.items():
            assert abs(pl.distance(em[name]["origin"][None])[0]) < 1e-9 and pl.d <= 0
        assert _nominal_layout(cal) == layout
    straight = nominal_emitters(layout="4-straight")
    assert np.allclose(straight["vertical_2"]["direction"], [0, 0, 1]) and np.allclose(straight["horizontal_2"]["direction"], [0, 0, 1])
    # crossing the centre line 600 mm ahead of the faceplate (622 mm from the module centres)
    v2 = nominal_calibration(layout="4-cross600").lasers["vertical_2"]
    assert (-v2.d - v2.normal[0] * 30.0) / v2.normal[2] == pytest.approx(604.0, abs=0.05)
    with pytest.raises(ValueError):
        nominal_calibration(layout="4-nowhere")


def test_ideal_calibration_is_parallel_and_scales():
    cal = nominal_calibration()
    st = cal.stereo
    assert np.allclose(st.R1, np.eye(3), atol=1e-12) and np.allclose(st.K_rect, st.K1, atol=1e-9)
    assert st.K_rect[0, 0] == 720.0 and st.K_rect[0, 2] == 639.5 and st.baseline == pytest.approx(60.0)
    half = nominal_calibration((640, 400))
    assert np.allclose(half.stereo.P1, st.scaled(0.5).P1) and np.allclose(half.stereo.P2, st.scaled(0.5).P2)
    assert nominal_calibration(fx=700.0).stereo.K1[0, 0] == 700.0


def test_realistic_calibration_exercises_rectification():
    st = nominal_calibration(realistic=True).stereo
    assert st.D1[0] == pytest.approx(-0.05) and st.D1[1] == pytest.approx(0.02)
    assert rotation_angle_deg(st.R) == pytest.approx(0.30, abs=1e-9)
    assert np.linalg.norm(st.T) == pytest.approx(60.0) and st.baseline == pytest.approx(60.0)
    assert rotation_angle_deg(st.R1) > 0.1
    # rectification frames: R2 = R1 R^T and the right camera centre is (baseline, 0, 0) in C
    assert np.allclose(st.R2, st.R1 @ st.R.T, atol=1e-12)
    assert np.allclose(st.R1 @ (-st.R.T @ st.T), [st.baseline, 0, 0], atol=1e-9)


def test_imu_truth_is_permutation_plus_mounting_error(tmp_path):
    cal = nominal_calibration()
    R = cal.imu.R_ci
    assert np.allclose(R @ R.T, np.eye(3), atol=1e-12) and np.linalg.det(R) == pytest.approx(1.0)
    assert cal.imu.time_offset == pytest.approx(0.030)
    # angle to the closest signed axis permutation is "a few degrees"
    best = 180.0
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((-1, 1), repeat=3):
            P = np.zeros((3, 3))
            P[range(3), perm] = signs
            if np.linalg.det(P) > 0:
                best = min(best, rotation_angle_deg(P.T @ R))
    assert 2.0 < best < 6.0
    assert rotation_angle_deg(R) > 45.0  # far from the CAD nominal identity
    cal.save(tmp_path / "c.json")
    back = ScannerCalibration.load(tmp_path / "c.json")
    assert np.allclose(back.imu.R_ci, R) and np.allclose(back.stereo.D1, cal.stereo.D1)


def test_lens_model_matches_opencv():
    st = nominal_calibration(realistic=True).stereo
    rng = np.random.default_rng(0)
    P = np.column_stack([rng.uniform(-200, 200, 500), rng.uniform(-130, 130, 500), rng.uniform(150, 450, 500)])
    for side, K, D, R_cr, o in (
        ("left", st.K1, st.D1, st.R1, np.zeros(3)),
        ("right", st.K2, st.D2, st.R1 @ st.R.T, st.R1 @ (-st.R.T @ st.T)),
    ):
        uv = project_to_raw(P, st, side)
        Praw = (P - o) @ R_cr
        uv_cv, _ = cv2.projectPoints(Praw, np.zeros(3), np.zeros(3), K, D)
        assert np.abs(uv - uv_cv.reshape(-1, 2)).max() < 1e-9
        rays = raw_pixel_rays(uv, K, D)
        dirs = Praw / np.linalg.norm(Praw, axis=1, keepdims=True)
        assert np.linalg.norm(np.cross(rays, dirs), axis=1).max() < 1e-10
    xy = rng.uniform(-0.9, 0.9, (1000, 2))
    assert np.abs(undistort_normalized(distort_normalized(xy, st.D1), st.D1) - xy).max() < 1e-12


def test_raw_rays_agree_with_rectification_maps():
    """What vision.rectify does (initUndistortRectifyMap) must invert our raw rendering model."""
    st = nominal_calibration((640, 400), realistic=True).stereo
    for side, K, D, Rr, P in (("left", st.K1, st.D1, st.R1, st.P1), ("right", st.K2, st.D2, st.R2, st.P2)):
        mx, my = cv2.initUndistortRectifyMap(K, D, Rr, P, st.image_size, cv2.CV_32FC1)
        uv_rect = np.array([[5.0, 7.0], [320.0, 200.0], [630.0, 390.0], [100.0, 350.0]])
        Kr = np.asarray(P)[:3, :3]
        rays = np.column_stack([(uv_rect[:, 0] - Kr[0, 2]) / Kr[0, 0], (uv_rect[:, 1] - Kr[1, 2]) / Kr[1, 1], np.ones(4)])
        o = np.zeros(3) if side == "left" else np.array([st.baseline, 0.0, 0.0])
        uv_raw = project_to_raw(o + 300.0 * rays, st, side)
        ij = uv_rect.astype(int)
        expected = np.column_stack([mx[ij[:, 1], ij[:, 0]], my[ij[:, 1], ij[:, 0]]])
        assert np.abs(uv_raw - expected).max() < 1e-3
