"""End-to-end calibration on simulated recordings: stereo, laser planes, IMU, health check (area F2).

Simulated calibration sets (``ohs.sim.datasets``) and a rendered IMU sequence are written as
ordinary recording folders and run through ``ohs.app.calib_flow``. Errors are measured against
the simulator's true calibration. Fast tests use fewer views / lower resolution with thresholds
a few times the measured error; ``@pytest.mark.slow`` tests are the full runs behind the
VALIDATION.md table (they print ``METRICS {...}`` lines; run with ``-s`` to see them).

Comparing frames: estimated planes and ``R_ci`` live in the frame C of the calibration that
rectified the images. With an *estimated* stereo calibration that frame differs from the true C
by the rectification rotation, so those comparisons are made in the raw left camera frame
(``n_raw = R1^T n``, ``R_raw_i = R1^T R_ci``), which is the physical camera both calibrations
describe.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from ohs import geometry as G
from ohs.app.calib_flow import check_from_recording, imu_from_recording, lasers_from_recording, stereo_from_recording
from ohs.calib.board import CharucoSpec
from ohs.io.recording import RecordingWriter
from ohs.sim.datasets import make_laser_calib_set, make_stereo_calib_set
from ohs.sim.imu import ImuSimParams, synthesize_imu
from ohs.sim.nominal import nominal_calibration, nominal_emitters
from ohs.sim.render import Renderer, RenderParams
from ohs.sim.scene import charuco_board
from ohs.sim.trajectory import RotatingTrajectory, look_at
from ohs.types import Event, ScannerCalibration, StereoCalibration, StereoFrame

SPEC = CharucoSpec()


# --------------------------------------------------------------------------- helpers
def metrics(name: str, **values: object) -> None:
    print("METRICS " + json.dumps({"test": name, **values}, default=float))


def write_pairs(path: Path, pairs, fps: float = 5.0, purpose: str = "calibration", calib: ScannerCalibration | None = None) -> Path:
    with RecordingWriter(path, source="sim", purpose=purpose) as w:
        for i, (left, right) in enumerate(pairs):
            w.write_frame(StereoFrame(i, 10.0 + i / fps, left, right))
        if calib is not None:
            w.write_calibration(calib)
    return path


def stereo_errors(est: StereoCalibration, true: StereoCalibration) -> dict:
    """Intrinsic / extrinsic errors and the post-rectification row error on a grid of 3D points."""
    out = {
        "fx_err_px": float(est.K1[0, 0] - true.K1[0, 0]),
        "fx_right_err_px": float(est.K2[0, 0] - true.K2[0, 0]),
        "cx_err_px": float(est.K1[0, 2] - true.K1[0, 2]),
        "cy_err_px": float(est.K1[1, 2] - true.K1[1, 2]),
        "baseline_err_mm": float(np.linalg.norm(est.T) - np.linalg.norm(true.T)),
        "rel_rotation_err_deg": G.rotation_angle_deg(est.R @ true.R.T),
    }
    # true 3D points (true frame C) 200-450 mm away -> raw pixels -> estimated rectification
    import cv2

    from ohs.sim.render import project_to_raw

    rng = np.random.default_rng(0)
    z = rng.uniform(200, 450, 4000)
    uv = rng.uniform([40, 40], [true.image_size[0] - 40, true.image_size[1] - 40], (4000, 2))
    P = G.pixels_to_rays(uv, true.K_rect) * z[:, None]
    raw_l, raw_r = project_to_raw(P, true, "left"), project_to_raw(P, true, "right")
    ok = np.isfinite(raw_l).all(1) & np.isfinite(raw_r).all(1)
    rl = cv2.undistortPoints(raw_l[ok].reshape(-1, 1, 2), est.K1, est.D1, R=est.R1, P=est.P1).reshape(-1, 2)
    rr = cv2.undistortPoints(raw_r[ok].reshape(-1, 1, 2), est.K2, est.D2, R=est.R2, P=est.P2).reshape(-1, 2)
    dv = np.abs(rl[:, 1] - rr[:, 1])
    out.update(row_err_median_px=float(np.median(dv)), row_err_p95_px=float(np.percentile(dv, 95)))
    # depth error of stereo triangulation with the estimated rectification (true points)
    disp = rl[:, 0] - rr[:, 0] - (est.P1[0, 2] - est.P2[0, 2])
    z_est = est.P1[0, 0] * est.baseline / disp
    out["depth_scale_err_pct"] = float(np.median(z_est / z[ok] - 1.0) * 100.0)
    return out


def plane_errors(est: dict, true: dict, R1_est: np.ndarray | None = None, R1_true: np.ndarray | None = None,
                 K: np.ndarray | None = None) -> dict:
    """Per laser: normal-angle and offset errors (optionally in the raw left camera frame) and the
    resulting laser-point error: rays through a pixel grid of the 1280x800 rectified camera, hit
    with the true and the estimated plane, depths 150-450 mm (median / max distance, mm)."""
    K = np.array([[720.0, 0, 639.5], [0, 720.0, 399.5], [0, 0, 1]]) if K is None else K
    uu, vv = np.meshgrid(np.arange(0, 1280, 16) + 0.5, np.arange(0, 800, 16) + 0.5)
    rays = G.pixels_to_rays(np.column_stack([uu.ravel(), vv.ravel()]), K)
    out = {}
    for name, t in true.items():
        if name not in est:
            out[name] = None
            continue
        n_e, n_t, d_e = est[name].normal, t.normal, est[name].d
        if R1_est is not None:
            n_e, n_t = R1_est.T @ n_e, R1_true.T @ n_t
        X_t = G.ray_plane_intersect(rays, n_t, t.d)
        X_e = G.ray_plane_intersect(rays, n_e, d_e)
        ok = np.isfinite(X_t).all(1) & np.isfinite(X_e).all(1) & (X_t[:, 2] > 150) & (X_t[:, 2] < 450)
        dist = np.linalg.norm(X_e[ok] - X_t[ok], axis=1)
        out[name] = {"angle_deg": float(np.degrees(np.arccos(np.clip(abs(n_e @ n_t), 0, 1)))),
                     "d_err_mm": float(d_e - t.d),
                     "point_err_median_mm": float(np.median(dist)), "point_err_max_mm": float(dist.max())}
    return out


def make_imu_recording(path: Path, image_size=(1280, 800), spec: CharucoSpec = SPEC, seconds: float = 7.0,
                       fps: float = 30.0, distance: float = 330.0, seed: int = 0, realistic: bool = True) -> tuple[Path, ScannerCalibration]:
    """Board on a table, scanner held still and then rotated; frames + 416 Hz IMU as a recording."""
    calib = nominal_calibration(image_size, realistic=realistic)
    scene = charuco_board(None, **asdict(spec))
    to = float(calib.imu.time_offset)
    t_exp = 50.0 + np.arange(int(seconds * fps)) / fps
    t_cam = t_exp + to
    T0 = look_at([10.0, -60.0, distance], [0.0, 0.0, 0.0], up=(0.0, 1.0, 0.0))
    traj = RotatingTrajectory(T0, float(t_cam[0]), seed=seed)
    poses = traj.poses(t_cam)
    imu = synthesize_imu(traj, float(t_exp[0]) - 0.5, float(t_exp[-1]) + 0.5, calib.imu.R_ci, to,
                         ImuSimParams(gyro_bias=calib.imu.gyro_bias.copy()), seed=seed)
    jitter = np.random.default_rng(seed + 5).normal(0, 2e-4, len(t_cam))
    rp = RenderParams(ambient=150.0, dots_on=False, lines_on=False, seed=seed)
    with Renderer(calib, nominal_emitters(baseline=calib.stereo.baseline), scene, rp) as r, \
            RecordingWriter(path, source="sim", purpose="imu_calibration", png_compression=1) as w:
        w.write_imu(imu)
        w.write_event(Event(float(t_exp[0]) - 0.1, "button", "down"))
        for i, T in enumerate(poses):
            left, right, _ = r.render(T)
            w.write_frame(StereoFrame(i, float(t_cam[i] + jitter[i]), left, right))
    return path, calib


def imu_errors(est, true, R1_est=None, R1_true=None) -> dict:
    R_e, R_t = est.R_ci, true.R_ci
    if R1_est is not None:
        R_e, R_t = R1_est.T @ R_e, R1_true.T @ R_t
    return {"R_ci_err_deg": G.rotation_angle_deg(R_e.T @ R_t),
            "time_offset_err_ms": 1e3 * float(est.time_offset - true.time_offset),
            "gyro_bias_err_mrad_s": 1e3 * float(np.linalg.norm(est.gyro_bias - true.gyro_bias))}


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def stereo_small(tmp_path_factory):
    pairs, calib, spec = make_stereo_calib_set(n_views=10, realistic=True, seed=3)
    rec = write_pairs(tmp_path_factory.mktemp("st") / "rec", pairs, purpose="stereo_calibration")
    return rec, calib, CharucoSpec(**spec)


@pytest.fixture(scope="module")
def laser_small(tmp_path_factory):
    pairs, calib, poses, spec = make_laser_calib_set(n_views=6, realistic=False, seed=1)
    rec = write_pairs(tmp_path_factory.mktemp("la") / "rec", pairs, purpose="laser_calibration")
    return rec, calib, CharucoSpec(**spec)


# --------------------------------------------------------------------------- fast tests
def test_stereo_from_recording(stereo_small):
    rec, true, spec = stereo_small
    calib, rep = stereo_from_recording(rec, spec)
    e = stereo_errors(calib, true.stereo)
    metrics("stereo_fast", views=rep["views_used"], rms=rep["rms"], **e)
    # 10-12 views: measured fx error ~0.2 px, baseline ~0.001 mm, rows p95 ~0.03 px
    assert rep["views_used"] >= 8 and rep["rms"] < 0.3
    assert abs(e["fx_err_px"]) < 3.0 and abs(e["cx_err_px"]) < 3.0 and abs(e["cy_err_px"]) < 3.0
    assert abs(e["baseline_err_mm"]) < 0.3 and e["rel_rotation_err_deg"] < 0.1
    assert e["row_err_p95_px"] < 0.3
    assert rep["rectification_check"]["ok"] and rep["frame_indices"] == list(range(len(rep["frame_indices"])))
    # thinning a long recording keeps at most max_views frames
    calib2, rep2 = stereo_from_recording(rec, spec, max_views=6)
    assert len(rep2["frame_indices"]) == 6 and rep2["frames_screened_usable"] >= 8


def test_stereo_from_recording_too_few_views(stereo_small, tmp_path):
    rec, _, spec = stereo_small
    from ohs.io.recording import RecordingReader

    r = RecordingReader(rec)
    few = write_pairs(tmp_path / "few", [(r.frame(i).left, r.frame(i).right) for i in range(3)])
    with pytest.raises(ValueError):
        stereo_from_recording(few, spec)


def test_lasers_margin_beats_board(laser_small):
    rec, true, spec = laser_small
    res = {}
    for region in ("margin", "board"):
        planes, rep = lasers_from_recording(rec, true, spec, region=region)
        res[region] = plane_errors(planes, true.lasers)
        assert rep["frames_with_board"] == 6
    metrics("lasers_fast", **{f"{r}_{n}_{k}": v for r, d in res.items() for n, e in d.items() if e for k, v in e.items()})
    for name in ("vertical", "horizontal"):
        m = res["margin"][name]
        # measured: 0.004-0.006 deg, ~0.01 mm; laser-point error median ~0.01 mm, max ~0.15 mm (450 mm, grazing)
        assert m is not None and m["angle_deg"] < 0.03 and abs(m["d_err_mm"]) < 0.1, (name, m)
        assert m["point_err_median_mm"] < 0.05 and m["point_err_max_mm"] < 0.4, (name, m)
        # the ink-biased board stripe gives larger laser-point errors than the margin
        b = res["board"][name]
        assert b is None or b["point_err_max_mm"] > m["point_err_max_mm"], (name, b, m)


def test_lasers_report_and_check(laser_small):
    rec, true, spec = laser_small
    planes, rep = lasers_from_recording(rec, true, spec)
    assert rep["ok"] and rep["region_used"] == {"vertical": "margin", "horizontal": "margin"}
    for name in ("vertical", "horizontal"):
        assert rep["residuals"][name]["median_abs_mm"] < 0.05
        assert rep["change_vs_input"][name]["angle_deg"] < 0.03
        assert rep["lasers"][name]["n_observations"] >= 5
    # per-frame counts: lines confirmed by the right image and split by region
    row = rep["per_frame"][0]
    assert row["board"] and set(row["points"]["vertical"]) == {"board", "margin"}
    # health check on the same recording with the true calibration: everything good
    chk = check_from_recording(rec, true, spec)
    assert chk["ok"], chk["verdict"]
    assert chk["lasers"]["lasers"]["vertical"]["median_abs_mm"] < 0.1
    # a laser that moved by 1 deg is flagged
    moved = ScannerCalibration(true.stereo, dict(true.lasers), true.imu)
    pv = moved.lasers["vertical"]
    from ohs.types import LaserPlane

    moved.lasers["vertical"] = LaserPlane("vertical", G.so3_exp([0, np.radians(1.0), 0]) @ pv.normal, pv.d)
    bad = check_from_recording(rec, moved, spec)
    assert not bad["ok"] and "lasers" in bad["verdict"]


def test_imu_from_recording_fast(tmp_path):
    spec = CharucoSpec(7, 5, 30.0, 22.0)  # coarser board: decodable at 640x400
    rec, true = make_imu_recording(tmp_path / "imu", image_size=(640, 400), spec=spec, seconds=4.5, fps=20.0,
                                   distance=300.0, realistic=False)
    est, rep = imu_from_recording(rec, true, spec)
    e = imu_errors(est, true.imu)
    metrics("imu_fast", frames_with_pose=rep["frames_with_pose"], **e)
    assert rep["frames_with_pose"] >= 0.9 * rep["frames"]
    # measured ~0.1 deg / 0.3 ms / 0.1 mrad/s at 640x400
    assert e["R_ci_err_deg"] < 0.5 and abs(e["time_offset_err_ms"]) < 2.0 and e["gyro_bias_err_mrad_s"] < 1.0
    assert rep["bias_source"] == "static"
    chk = check_from_recording(rec, ScannerCalibration(true.stereo, {}, est), spec)
    assert chk["imu"]["ok"], chk["imu"]


def test_flows_reject_mismatched_recordings(laser_small, tmp_path):
    rec, true, spec = laser_small
    half = nominal_calibration((640, 400))
    with pytest.raises(ValueError):
        lasers_from_recording(rec, half, spec)
    with pytest.raises(ValueError):  # no IMU samples in a laser recording
        imu_from_recording(rec, true, spec)


# --------------------------------------------------------------------------- full runs (VALIDATION.md)
@pytest.fixture(scope="module")
def full_chain(tmp_path_factory):
    """(a) stereo -> (b) lasers -> (c) IMU, each from its own simulated recording of one device."""
    root = tmp_path_factory.mktemp("chain")
    pairs, true, spec = make_stereo_calib_set(n_views=20, realistic=True, seed=0)
    spec = CharucoSpec(**spec)
    st_rec = write_pairs(root / "stereo", pairs, purpose="stereo_calibration")
    del pairs
    stereo, st_rep = stereo_from_recording(st_rec, spec)
    est = ScannerCalibration(stereo, {}, None)
    lpairs, _, _, _ = make_laser_calib_set(n_views=8, realistic=True, seed=0)
    la_rec = write_pairs(root / "lasers", lpairs, purpose="laser_calibration")
    del lpairs
    planes, la_rep = lasers_from_recording(la_rec, est, spec)
    est = ScannerCalibration(stereo, planes, None)
    imu_rec, _ = make_imu_recording(root / "imu", image_size=(1280, 800), spec=spec, seconds=7.0, realistic=True)
    imu, imu_rep = imu_from_recording(imu_rec, est, spec)
    est = ScannerCalibration(stereo, planes, imu)
    yield {"root": root, "true": true, "est": est, "spec": spec, "rec": {"stereo": st_rec, "lasers": la_rec, "imu": imu_rec},
           "reports": {"stereo": st_rep, "lasers": la_rep, "imu": imu_rep}}
    shutil.rmtree(root, ignore_errors=True)  # ~0.5 GB of full-resolution frames


@pytest.mark.slow
def test_full_stereo(full_chain):
    true, est = full_chain["true"], full_chain["est"]
    rep = full_chain["reports"]["stereo"]
    e = stereo_errors(est.stereo, true.stereo)
    metrics("stereo_full", views=rep["views_used"], rms=rep["rms"], coverage=rep["coverage_left"],
            rect_check_dv=rep["rectification_check"]["mean_abs_dv_px"], **e)
    assert abs(e["fx_err_px"]) < 2.0 and abs(e["cx_err_px"]) < 2.0 and abs(e["cy_err_px"]) < 2.0
    assert abs(e["baseline_err_mm"]) < 0.2 and e["rel_rotation_err_deg"] < 0.05 and e["row_err_p95_px"] < 0.2


@pytest.mark.slow
def test_full_lasers(full_chain):
    """Laser planes on the realistic rig: true vs estimated stereo calibration, board vs margin."""
    true, est, spec, rec = full_chain["true"], full_chain["est"], full_chain["spec"], full_chain["rec"]["lasers"]
    out = {}
    for calib_name, cal in (("true_stereo", true), ("est_stereo", ScannerCalibration(est.stereo, {}, None))):
        for region in ("margin", "board", "both"):
            planes, rep = lasers_from_recording(rec, cal, spec, region=region)
            if calib_name == "true_stereo":
                err = plane_errors(planes, true.lasers)
            else:
                err = plane_errors(planes, true.lasers, cal.stereo.R1, true.stereo.R1)
            out[f"{calib_name}/{region}"] = err
    metrics("lasers_full", **{k: v for k, v in out.items()})
    # with the estimated stereo calibration the plane absorbs its small intrinsic errors (the
    # same camera model triangulates the scan later, so they largely cancel end to end: see the
    # full-chain scan); the comparison with the physical plane is therefore looser
    for key, med, mx in (("true_stereo/margin", 0.05, 0.4), ("est_stereo/margin", 0.25, 1.0)):
        for name in ("vertical", "horizontal"):
            e = out[key][name]
            assert e is not None and e["angle_deg"] < 0.05 and abs(e["d_err_mm"]) < 0.15, (key, name, e)
            assert e["point_err_median_mm"] < med and e["point_err_max_mm"] < mx, (key, name, e)
    # the margin default beats the board region (laser-point error over the working range)
    for name in ("vertical", "horizontal"):
        b = out["true_stereo/board"][name]
        assert b is None or b["point_err_max_mm"] > out["true_stereo/margin"][name]["point_err_max_mm"], (name, b)


@pytest.mark.slow
def test_full_imu(full_chain):
    true, est, spec = full_chain["true"], full_chain["est"], full_chain["spec"]
    rep = full_chain["reports"]["imu"]
    e_est = imu_errors(est.imu, true.imu, est.stereo.R1, true.stereo.R1)
    imu_true_stereo, rep_t = imu_from_recording(full_chain["rec"]["imu"], true, spec)
    e_true = imu_errors(imu_true_stereo, true.imu)
    metrics("imu_full", frames=rep["frames"], frames_with_pose=rep["frames_with_pose"], residual_rms_deg=rep["residual_rms_deg"],
            est_stereo=e_est, true_stereo=e_true)
    for e in (e_est, e_true):
        assert e["R_ci_err_deg"] < 0.3 and abs(e["time_offset_err_ms"]) < 1.0 and e["gyro_bias_err_mrad_s"] < 0.5
    chk = check_from_recording(full_chain["rec"]["imu"], full_chain["est"], spec)
    assert chk["stereo"]["ok"] and chk["imu"]["ok"], chk


@pytest.mark.slow
def test_full_chain_scan(full_chain):
    """(g) the sphere scan processed with the calibration estimated in (a)-(c)."""
    from test_e2e_scan import evaluate_scan, scan_recording  # noqa: PLC0415 (shared helpers)

    from ohs.app.process import ProcessParams, process_recording

    rec = scan_recording(full_chain["root"] / "scan", "sphere_on_plane", 36, (1280, 800))
    rep = process_recording(rec, full_chain["root"] / "out_est", calib=full_chain["est"], params=ProcessParams())
    m = evaluate_scan(rec, rep, full_chain["root"] / "out_est", calib=full_chain["est"])
    metrics("full_chain_scan", **m)
    assert m["tracked_fraction"] > 0.9
    assert m["traj_max_trans_mm"] < 3.0 and m["traj_max_rot_deg"] < 1.0
    assert m["mesh_median_mm"] < 0.4 and m["laser_median_mm"] < 0.3
