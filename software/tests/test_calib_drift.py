"""Launch-time drift check (``ohs.calib.drift``) on simulated frames of a textured scene.

A short scan recording (dots on, realistic distorted lenses) is checked with its true calibration
(no row error), with deliberately perturbed calibrations (a pitch between the cameras, a shifted
principal point) and with frames that have no texture.
"""
from __future__ import annotations

import time

import cv2
import numpy as np
import pytest

from ohs.calib.drift import drift_check, row_error
from ohs.geometry import so3_exp
from ohs.io.recording import RecordingReader
from ohs.sim.datasets import make_scan_recording
from ohs.types import StereoCalibration, StereoFrame


@pytest.fixture(scope="module")
def scan(tmp_path_factory):
    rec = make_scan_recording(tmp_path_factory.mktemp("drift") / "rec", n_frames=3, realistic=True, lines=False,
                              depth_every=0, seed=1)
    reader = RecordingReader(rec)
    return [reader.frame(i) for i in range(len(reader))], reader.calibration().stereo


def rebuilt(st: StereoCalibration, **changes: np.ndarray) -> StereoCalibration:
    """The calibration with some raw parameters replaced and the rectification recomputed."""
    p = {k: getattr(st, k) for k in ("K1", "D1", "K2", "D2", "R", "T")}
    p.update(changes)
    return StereoCalibration.build(st.image_size, p["K1"], p["D1"], p["K2"], p["D2"], p["R"], p["T"])


def test_true_calibration_is_ok(scan):
    frames, st = scan
    res = drift_check(frames, st)
    assert set(res) >= {"state", "dv_px", "p90_px", "matches", "frames", "message"}
    # measured: 0.055 px over ~1600 matches
    assert res["state"] == "ok" and res["dv_px"] < 0.2 and res["p90_px"] < 0.5, res
    assert res["matches"] >= 200 and res["frames"] == 3
    assert "still holds" in res["message"]
    t = time.perf_counter()
    drift_check(frames, st)
    assert time.perf_counter() - t < 0.5  # measured ~0.13 s


def test_pitch_between_cameras_is_drift(scan):
    frames, st = scan
    res = drift_check(frames, rebuilt(st, R=so3_exp([np.radians(0.3), 0.0, 0.0]) @ st.R))
    # 0.3 deg about the baseline moves the right rows by about f * 0.3 deg = 3.8 px
    assert res["state"] == "drift" and 3.0 < res["dv_px"] < 4.6, res
    assert "Run the 2-minute check" in res["message"]


def test_shifted_principal_point_is_drift(scan):
    frames, st = scan
    K2 = st.K2.copy()
    K2[1, 2] += 2.0
    res = drift_check(frames, rebuilt(st, K2=K2))
    assert res["state"] == "drift" and 1.6 < res["dv_px"] < 2.4, res
    K2[1, 2] = st.K2[1, 2] + 0.3  # below the threshold: measured, but no alarm
    res = drift_check(frames, rebuilt(st, K2=K2))
    assert res["state"] == "ok" and 0.15 < res["dv_px"] < 0.45, res


def test_no_texture_is_unknown(scan):
    _, st = scan
    rng = np.random.default_rng(0)
    blank = [StereoFrame(i, float(i), np.full((800, 1280), 90, np.uint8), np.full((800, 1280), 90, np.uint8)) for i in range(3)]
    noise = [StereoFrame(i, float(i), np.clip(rng.normal(90, 2, (800, 1280)), 0, 255).astype(np.uint8),
                         np.clip(rng.normal(90, 2, (800, 1280)), 0, 255).astype(np.uint8)) for i in range(3)]
    for frames in (blank, noise):
        res = drift_check(frames, st)
        assert res["state"] == "unknown" and res["dv_px"] is None and res["p90_px"] is None and res["matches"] < 40, res
        assert res["frames"] == 3 and res["message"]
    res = drift_check([], st)
    assert res["state"] == "unknown" and res["frames"] == 0
    small = StereoFrame(0, 0.0, np.zeros((400, 640), np.uint8), np.zeros((400, 640), np.uint8))
    res = drift_check([small], st)  # a camera mode the calibration is not for
    assert res["state"] == "unknown" and res["frames"] == 0 and "640x400" in res["message"]


def test_row_error_is_subpixel():
    """A known vertical offset and disparity between two views of a texture, recovered to 0.05 px."""
    rng = np.random.default_rng(3)
    img = np.zeros((400, 640), np.float32)
    ys, xs = rng.integers(10, 390, 2500), rng.integers(10, 630, 2500)
    img[ys, xs] = rng.uniform(150, 255, 2500)
    img = cv2.GaussianBlur(img, (0, 0), 1.3) * 6.0 + 40.0 + 10.0 * cv2.GaussianBlur(rng.standard_normal((400, 640)).astype(np.float32), (0, 0), 8)
    left = np.clip(img, 0, 255).astype(np.uint8)
    d, dv = 12.0, 0.4  # right(x, y) = left(x + d, y + dv): left (u, v) appears at (u - d, v - dv)
    M = np.array([[1.0, 0.0, d], [0.0, 1.0, dv]])
    right = cv2.warpAffine(img, M, (640, 400), flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)
    right = np.clip(right, 0, 255).astype(np.uint8)
    dvs, disp = row_error(left, right)
    assert len(dvs) >= 100
    assert np.median(dvs) == pytest.approx(dv, abs=0.05) and np.median(disp) == pytest.approx(d, abs=0.05)
    dvs, disp = row_error(left, right, min_disparity=20.0)  # the true matches are outside the allowed disparities
    assert np.all(disp > 20.0)
