"""Stereo calibration on synthetic ChArUco views of a known rig (area C)."""
import numpy as np
import pytest

from ohs import geometry as G
from ohs.calib.board import BoardViewRenderer, CharucoSpec
from ohs.calib.stereo import calibrate_stereo, rectification_check

SIZE = (1280, 800)
SPEC = CharucoSpec()


def true_rig():
    K1 = np.array([[720.0, 0, 641.3], [0, 720.0, 398.2], [0, 0, 1]])
    K2 = np.array([[718.6, 0, 636.4], [0, 718.6, 401.7], [0, 0, 1]])
    D1 = np.array([-0.05, 0.0, 0.0, 0.0, 0.0])
    D2 = np.array([-0.048, 0.0, 0.0, 0.0, 0.0])
    axis = np.array([0.3, 1.0, 0.2]) / np.linalg.norm([0.3, 1.0, 0.2])
    R = G.so3_exp(axis * np.radians(0.3))
    T = -R @ np.array([60.0, 0.0, 0.0])  # right camera centre at (60, 0, 0) in the left frame
    return K1, D1, K2, D2, R, T


def board_pose(K, u, v, Z, rx, ry, rz):
    """Board centre at pixel (u, v) and depth Z, tilted by rx/ry/rz degrees about its centre."""
    P = Z * np.linalg.inv(K) @ np.array([u, v, 1.0])
    R = G.so3_exp([0, 0, np.radians(rz)]) @ G.so3_exp([0, np.radians(ry), 0]) @ G.so3_exp([np.radians(rx), 0, 0])
    c = np.array([SPEC.size_mm[0] / 2, SPEC.size_mm[1] / 2, 0.0])
    return G.make_se3(R, P - R @ c)


# (u, v, Z, rx, ry, rz): 3 x 3 grid of image positions, close and far views, strong tilts
VIEWS = [
    (300, 200, 380, 25, -25, 5), (640, 190, 400, 30, 10, -5), (980, 200, 380, 20, 30, 8),
    (290, 400, 390, -10, -35, -6), (640, 400, 350, 0, 0, 3), (990, 400, 390, 10, 35, 5),
    (300, 600, 380, -30, -20, 4), (640, 610, 400, -30, 5, -8), (980, 600, 380, -25, 25, -3),
    (640, 400, 260, 15, -20, 2), (600, 380, 250, -20, 25, -4), (700, 420, 270, 30, 0, 10),
    (420, 300, 520, 10, 40, 0), (860, 520, 540, -15, -40, 12), (640, 400, 600, 40, -10, -10),
    (560, 330, 320, -40, 15, 6), (760, 470, 330, 35, -30, -12), (640, 400, 450, -5, 45, 0),
]


@pytest.fixture(scope="module")
def stereo_data():
    K1, D1, K2, D2, R, T = true_rig()
    T_rl = G.make_se3(R, T)
    rl = BoardViewRenderer(SPEC, K1, SIZE, D1)
    rr = BoardViewRenderer(SPEC, K2, SIZE, D2)
    rng = np.random.default_rng(7)
    pairs = []
    for v in VIEWS:
        T_cb = board_pose(K1, *v)
        pairs.append((rl.render(T_cb, rng=rng), rr.render(T_rl @ T_cb, rng=rng)))
    return (K1, D1, K2, D2, R, T), pairs


def test_stereo_calibration_recovers_rig(stereo_data):
    (K1, D1, K2, D2, R, T), pairs = stereo_data
    blank = np.full((SIZE[1], SIZE[0]), 90, np.uint8)
    calib, rep = calibrate_stereo(pairs + [(blank, blank)], SPEC, SIZE)

    assert abs(calib.K1[0, 0] / K1[0, 0] - 1) < 0.005
    assert abs(calib.K2[0, 0] / K2[0, 0] - 1) < 0.005
    assert abs(np.linalg.norm(calib.T) - 60.0) < 0.3
    assert abs(calib.baseline - 60.0) < 0.3
    assert G.rotation_angle_deg(calib.R @ R.T) < 0.05
    assert rep["rms"] < 0.3
    assert calib.rms == pytest.approx(rep["rms"])
    # report contents
    assert rep["views_used"] >= 15 and rep["views_total"] == len(pairs) + 1
    assert {r["pair"] for r in rep["views_rejected"]} >= {len(pairs)}  # the blank pair
    assert rep["coverage_left"] > 0.6 and rep["coverage_right"] > 0.6
    assert rep["verdict"] == "good" and rep["quality_ok"]
    assert len(rep["per_view_errors"]) == rep["views_used"]
    assert rep["rms_left"] < 0.3 and rep["rms_right"] < 0.3

    chk = rectification_check(calib, pairs[:8], SPEC)
    assert chk["mean_abs_dv_px"] < 0.2
    assert abs(chk["scale_error_pct"]) < 0.3
    assert chk["ok"] and chk["verdict"] == "good"


def test_rectification_check_flags_bad_calibration(stereo_data):
    (K1, D1, K2, D2, R, T), pairs = stereo_data
    from ohs.types import StereoCalibration

    bad_R = G.so3_exp([np.radians(0.5), 0, 0]) @ R  # 0.5 deg pitch error between the cameras
    bad = StereoCalibration.build(SIZE, K1, D1, K2, D2, bad_R, T)
    chk = rectification_check(bad, pairs[:4], SPEC)
    assert chk["mean_abs_dv_px"] > 2.0 and not chk["ok"]
    assert "recalibrate" in chk["verdict"]


def test_calibrate_stereo_rejects_too_few_views(stereo_data):
    _, pairs = stereo_data
    with pytest.raises(ValueError):
        calibrate_stereo(pairs[:3], SPEC, SIZE)
