"""Simulator datasets: recordings and calibration sets are readable and agree with their truth."""
import cv2
import numpy as np
import pytest

from ohs.calib.board import CharucoSpec, charuco_page_layout
from ohs.geometry import integrate_gyro, pixels_to_rays, ray_plane_intersect, rotation_angle_deg
from ohs.io.recording import RecordingReader
from ohs.sim.datasets import (
    board_corners,
    laser_calib_board_poses,
    make_laser_calib_set,
    make_scan_recording,
    make_stereo_calib_set,
    stereo_calib_board_poses,
)
from ohs.sim.nominal import nominal_calibration
from ohs.sim.render import Renderer, RenderParams, project_to_raw
from ohs.sim.scene import sphere_on_plane


def _ridge(img, min_peak, valid, half=6):
    """Sub-pixel centre of a single bright line inside ``valid``: scan rows or columns, whichever
    crosses the line more steeply (line direction from PCA of the bright pixels), centroid over
    +-half px of the unmasked image; rows whose window leaves ``valid`` are skipped."""
    img = img.astype(np.float64)
    ys, xs = np.nonzero((img >= min_peak) & valid)
    if len(xs) < 2:
        return np.zeros((0, 2))
    ev = np.linalg.eigh(np.cov(np.vstack([xs, ys])))[1][:, -1]
    by_rows = abs(ev[1]) >= abs(ev[0])  # line runs mostly vertically -> scan each row
    I, V = (img, valid) if by_rows else (img.T, valid.T)
    out = []
    for k, (line, ok) in enumerate(zip(I, V)):
        j = int(np.argmax(np.where(ok, line, 0.0)))
        if line[j] < min_peak or j < 3 * half or j > len(line) - 3 * half - 1 or not ok[j - half : j + half + 1].all():
            continue
        w = np.clip(line[j - half : j + half + 1] - np.median(line[j - 3 * half : j + 3 * half + 1]), 0, None)
        c = j - half + (w * np.arange(2 * half + 1)).sum() / w.sum()
        out.append((c, k) if by_rows else (k, c))
    return np.array(out)


def test_scan_recording_roundtrip(tmp_path):
    out = make_scan_recording(tmp_path / "rec", n_frames=6, image_size=(640, 400), seed=2)
    r = RecordingReader(out)
    assert len(r) == 6 and r.meta["source"] == "sim" and r.meta["image_size"] == [640, 400]
    assert r.meta["scene"] == "sphere_on_plane" and r.meta["n_frames"] == 6
    f = r.frame(3)
    assert f.left.shape == f.right.shape == (400, 640) and f.left.dtype == np.uint8
    assert 5 < f.left.mean() < 120 and not np.array_equal(f.left, f.right)
    t = r.times()
    assert np.allclose(np.diff(t), 1 / 30, atol=1.5e-3)
    # truth calibration, incl. the deliberately non-trivial IMU extrinsics
    cal = r.calibration()
    truth = nominal_calibration((640, 400))
    assert np.allclose(cal.imu.R_ci, truth.imu.R_ci) and cal.imu.time_offset == pytest.approx(0.030)
    assert np.allclose(cal.lasers["vertical"].normal, truth.lasers["vertical"].normal)
    # trigger brackets the frames; IMU covers them (in IMU host time = camera time - offset)
    ev = r.events()
    assert [(e.kind, e.value) for e in ev] == [("button", "down"), ("button", "up")]
    assert ev[0].t_host < t[0] and ev[1].t_host > t[-1]
    imu = r.imu_arrays()
    n_exp = (5 / 30 + 1.0) * 416
    assert abs(len(imu["t_host"]) - n_exp) <= 2 and (np.diff(imu["t_host"]) > 0).all()
    assert imu["t_host"][0] < t[0] - cal.imu.time_offset - 0.4 and imu["t_host"][-1] > t[-1] - cal.imu.time_offset + 0.4
    assert np.linalg.norm(imu["accel"], axis=1).mean() == pytest.approx(9.81, abs=0.5)
    # ground truth: poses at the frame stamps, depth maps of the rectified left camera
    gt = r.ground_truth_poses()
    assert sorted(gt) == list(range(6))
    d0 = r.ground_truth_depth(0)
    assert d0.shape == (400, 640) and (d0 > 0).mean() > 0.95 and 150 < np.median(d0[d0 > 0]) < 400
    with Renderer(cal, None, sphere_on_plane(seed=2), RenderParams()) as rend:
        # poses.csv stores 9 significant digits -> re-rendered depth agrees to ~1e-6 mm
        assert np.allclose(rend.render(gt[0])[2], d0, atol=1e-3, rtol=0)
    # recorded gyro agrees with the ground-truth trajectory (frames 0 -> 5)
    s0, s1 = t[0] - cal.imu.time_offset, t[5] - cal.imu.time_offset
    R_i = integrate_gyro(imu["t_host"], imu["gyro"], s0, s1, bias=cal.imu.gyro_bias)
    R_c = cal.imu.R_ci @ R_i @ cal.imu.R_ci.T
    R_true = gt[0][:3, :3].T @ gt[5][:3, :3]
    assert rotation_angle_deg(R_true) > 1.0 and rotation_angle_deg(R_c.T @ R_true) < 0.1
    with pytest.raises(FileExistsError):
        make_scan_recording(out, n_frames=1, image_size=(160, 100))


@pytest.mark.parametrize("scene,realistic", [("car_panel", True), ("bracket", False)])
def test_other_scenes_record(tmp_path, scene, realistic):
    out = make_scan_recording(tmp_path / scene, scene=scene, n_frames=2, image_size=(320, 200), seed=1,
                              realistic=realistic, depth_every=2)
    r = RecordingReader(out)
    assert len(r) == 2 and r.ground_truth_depth(1) is None
    d = r.ground_truth_depth(0)
    assert (d > 0).mean() > 0.9 and np.isfinite(d).all()
    assert r.calibration().stereo.D1[0] != 0 if realistic else r.calibration().stereo.D1[0] == 0


def test_stereo_calib_set_corners_match_truth():
    pairs, cal, spec = make_stereo_calib_set(n_views=3, image_size=(1280, 800), realistic=True, seed=0)
    assert set(spec) == {"squares_x", "squares_y", "square_mm", "marker_mm", "dictionary"}
    assert len(pairs) == 3 and pairs[0][0].shape == (800, 1280) and cal.stereo.D1[0] != 0
    poses = stereo_calib_board_poses(3, cal, spec, seed=0)
    board = cv2.aruco.CharucoBoard((spec["squares_x"], spec["squares_y"]), spec["square_mm"], spec["marker_mm"],
                                   cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, spec["dictionary"])))
    det = cv2.aruco.CharucoDetector(board)
    obj = board_corners(spec)
    errs = []
    for (left, right), T_cb in zip(pairs, poses):
        for side, img in (("left", left), ("right", right)):
            corners, ids, _, _ = det.detectBoard(img)
            assert ids is not None and len(ids) >= 20
            ids = ids.ravel()
            P = obj[ids] @ T_cb[:3, :3].T + T_cb[:3, 3]
            errs.append(np.linalg.norm(corners.reshape(-1, 2) - project_to_raw(P, cal.stereo, side), axis=1))
    e = np.concatenate(errs)
    assert np.median(e) < 0.15 and np.percentile(e, 95) < 0.5


def test_stereo_calib_poses_cover_the_image():
    cal = nominal_calibration(realistic=True)
    poses = stereo_calib_board_poses(20, cal, None, seed=0)
    centres = np.array([project_to_raw(T[:3, 3][None] + T[:3, :3] @ [110.0, 80.0, 0.0], cal.stereo, "left")[0] for T in poses])
    assert centres[:, 0].min() < 320 and centres[:, 0].max() > 960 and centres[:, 1].min() < 200 and centres[:, 1].max() > 600
    tilts = [np.degrees(np.arccos(T[2, 2])) for T in poses]
    assert max(tilts) > 25


def test_laser_calib_set_lines_lie_on_board():
    pairs, cal, poses, spec = make_laser_calib_set(n_views=3, image_size=(640, 400), seed=0)
    assert len(pairs) == len(poses) == 3
    assert [np.allclose(a, b) for a, b in zip(poses, laser_calib_board_poses(3, cal, spec, seed=0))] == [True] * 3
    off, _, _, _ = make_laser_calib_set(n_views=3, image_size=(640, 400), seed=0, lines=False)
    assert (pairs[0][0] >= 250).sum() > 50 and (off[0][0] >= 250).sum() == 0
    K = cal.stereo.K_rect
    W = spec["squares_x"] * spec["square_mm"]
    H = spec["squares_y"] * spec["square_mm"]
    depths = []
    for name, vertical in (("vertical", True), ("horizontal", False)):
        # one sheet at a time, so "brightest pixel per row/column" is unambiguous
        single, _, _, _ = make_laser_calib_set(n_views=3, image_size=(640, 400), seed=0, params=RenderParams(line_names=(name,)))
        for (left, _), (ambient_only, _), T_cb in zip(single, off, poses):
            # the page as printed by ohs.calib.board.render_charuco_page (letter): white paper around
            # the squares (29.7 mm left/right, 18.5 mm above, scale bar and label text below)
            x0, x1, y0, y1 = charuco_page_layout(CharucoSpec(**spec)).paper_bounds_mm
            quad = np.array([[x0, y0, 0], [x1, y0, 0], [x1, y1, 0], [x0, y1, 0]]) @ T_cb[:3, :3].T + T_cb[:3, 3]
            uvq = quad[:, :2] / quad[:, 2:] * [K[0, 0], K[1, 1]] + [K[0, 2], K[1, 2]]
            mask = np.zeros(left.shape, np.uint8)
            cv2.fillPoly(mask, [np.round(uvq).astype(np.int32)], 1)
            # Only white paper around the line: a stripe crossing a black/white ink edge has its
            # intensity centroid pulled toward the white side (real effect, ink is ~20x darker).
            white = cv2.erode((ambient_only > 50).astype(np.uint8) & mask, np.ones((5, 5), np.uint8)).astype(bool)
            uv = _ridge(left, 120.0, white)
            assert len(uv) > 20
            # truth: image of the 3D line where the TRUE laser plane meets the TRUE board plane
            plane = cal.lasers[name]
            n_b, o_b = T_cb[:3, 2], T_cb[:3, 3]
            direction = np.cross(n_b, plane.normal)
            A = np.vstack([n_b, plane.normal, direction])
            p0 = np.linalg.solve(A, [n_b @ o_b, -plane.d, 0.0])
            ends = np.array([p0 - 100 * direction, p0 + 100 * direction])
            e = ends[:, :2] / ends[:, 2:] * [K[0, 0], K[1, 1]] + [K[0, 2], K[1, 2]]
            t = (e[1] - e[0]) / np.linalg.norm(e[1] - e[0])
            err_px = np.abs((uv - e[0]) @ np.array([-t[1], t[0]]))
            assert np.median(err_px) < 0.15 and np.percentile(err_px, 95) < 0.4, f"{name}: {np.median(err_px):.3f} px"
            X = ray_plane_intersect(pixels_to_rays(uv, K), plane.normal, plane.d)
            assert np.isfinite(X).all()
            depths.append(np.median(X[:, 2]))
    assert max(depths) - min(depths) > 60  # views at different depths constrain the planes
