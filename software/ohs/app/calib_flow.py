"""Calibration flows that run on recordings of the ChArUco board (``ohs calibrate ...``).

Every flow reads an ordinary recording folder (:mod:`ohs.io.recording`: raw side-by-side frames,
IMU, optional ``calibration.json``); ``meta["purpose"]`` is informational only. The board is the
one :func:`ohs.calib.board.render_charuco_page` prints, glued flat to glass.

* :func:`stereo_from_recording` - raw frames -> :func:`ohs.calib.stereo.calibrate_stereo`, plus a
  :func:`~ohs.calib.stereo.rectification_check` on the same views. Long recordings are thinned
  to at most ``max_views`` frames (evenly spread in time among frames that show the board).
* :func:`lasers_from_recording` - per frame: rectify with the given stereo calibration, extract
  the laser lines (left and right), detect the board in the rectified left image, keep line
  pixels confirmed by the right image (predicted on the board plane), then
  :func:`~ohs.calib.laser_planes.fit_laser_planes`. Default ``region="margin"``: only the blank
  printed margin around the pattern is used, because stripe centres are biased where they cross
  ink (see :mod:`ohs.calib.laser_planes`; on simulated views the margin gives ~20x smaller plane
  errors). A laser that cannot be fitted from the margin alone is refitted with ``"both"`` and
  the report says so.
* :func:`imu_from_recording` - board pose on every rectified left frame (``K_rect``, no
  distortion; the board is the world, ``R_wc = R_cb^T``, frame time ``t_host``) and the raw gyro
  -> :func:`~ohs.calib.imu.calibrate_imu_camera`.
* :func:`check_from_recording` - "is the calibration still good?": stereo rectification check,
  laser-plane residuals on new board views and gyro-vs-board rotation residuals.

Each returns plain JSON-serialisable reports; ``progress(k, n)`` callbacks are optional.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

from ohs.calib.board import CharucoSpec, detect_charuco, estimate_board_pose
from ohs.calib.imu import calibrate_imu_camera
from ohs.calib.laser_planes import (
    LaserBoardObservation,
    board_points_from_line,
    check_laser_planes,
    fit_laser_planes,
    make_observation,
)
from ohs.calib.stereo import calibrate_stereo, rectification_check
from ohs.geometry import pixels_to_rays, ray_plane_intersect, rotation_angle_deg
from ohs.io.recording import RecordingReader
from ohs.track.imu_prior import GyroPredictor
from ohs.types import ImuCalibration, LaserPlane, ScannerCalibration, StereoCalibration
from ohs.vision.laser import LINE_NAMES, LaserParams, extract_laser_lines, stereo_check
from ohs.vision.rectify import Rectifier

__all__ = [
    "stereo_from_recording",
    "lasers_from_recording",
    "imu_from_recording",
    "check_from_recording",
    "laser_observations_from_recording",
]

log = logging.getLogger(__name__)

ProgressFn = Callable[[int, int], None]
IMU_CHECK_MAX_DT_S = 0.25  # gyro-vs-board check: compare consecutive board poses this close in time
IMU_CHECK_OK_DEG = 0.15


# --------------------------------------------------------------------------- helpers
def _reader(rec_path: str | Path) -> RecordingReader:
    reader = RecordingReader(rec_path)
    if len(reader) == 0:
        raise ValueError(f"recording {rec_path} has no frames")
    return reader


def _image_size(reader: RecordingReader) -> tuple[int, int]:
    size = reader.meta.get("image_size")
    if not size:
        size = reader.frame(0).image_size
    return int(size[0]), int(size[1])


def _check_size(reader: RecordingReader, stereo: StereoCalibration) -> None:
    size = _image_size(reader)
    if size != tuple(int(v) for v in stereo.image_size):
        raise ValueError(f"the calibration is for {stereo.image_size[0]}x{stereo.image_size[1]} images but the "
                         f"recording {reader.path} has {size[0]}x{size[1]}")


def _positions(n: int, step: int, max_n: int | None = None) -> list[int]:
    if int(step) < 1:
        raise ValueError("step must be >= 1")
    pos = list(range(0, n, int(step)))
    if max_n is not None and len(pos) > max_n:
        pick = np.unique(np.round(np.linspace(0, len(pos) - 1, int(max_n))).astype(int))
        pos = [pos[i] for i in pick]
    return pos


def _thin(items: Sequence[int], max_n: int) -> list[int]:
    if len(items) <= max_n:
        return list(items)
    pick = np.unique(np.round(np.linspace(0, len(items) - 1, int(max_n))).astype(int))
    return [items[i] for i in pick]


def _default_laser_params(image_size: tuple[int, int]) -> LaserParams:
    s = image_size[0] / 1280.0
    return LaserParams() if abs(s - 1.0) < 1e-9 else LaserParams().scaled(s)


def _plane_change(a: LaserPlane, b: LaserPlane) -> dict:
    return {"angle_deg": float(np.degrees(np.arccos(np.clip(abs(float(a.normal @ b.normal)), 0.0, 1.0)))),
            "d_mm": float(b.d - a.d)}


# --------------------------------------------------------------------------- stereo
def stereo_from_recording(
    rec_path: str | Path,
    spec: CharucoSpec,
    step: int = 1,
    *,
    max_views: int = 60,
    min_corners: int = 8,
    progress: ProgressFn | None = None,
) -> tuple[StereoCalibration, dict]:
    """Stereo calibration from a recording of the board moved around in front of the scanner.

    Uses every ``step``-th frame; if that is more than ``max_views`` frames, the left images are
    screened for the board first and ``max_views`` of the usable frames are taken, evenly spread
    in time. Returns ``(StereoCalibration, report)``: the :func:`calibrate_stereo` report plus
    ``rectification_check``, ``frame_indices`` (recording index of each calibration pair) and the
    frame counts. Raises ``ValueError`` with fewer than 4 usable views.
    """
    reader = _reader(rec_path)
    size = _image_size(reader)
    positions = _positions(len(reader), step)
    screened = None
    if len(positions) > max_views:
        usable = []
        for k, pos in enumerate(positions):
            _, ids = detect_charuco(reader.frame(pos).left, spec)
            if len(ids) >= min_corners:
                usable.append(pos)
            if progress is not None:
                progress(k, len(positions))
        screened = len(usable)
        positions = _thin(usable, max_views)
    frames = [reader.frame(pos) for pos in positions]
    pairs = [(f.left, f.right) for f in frames]
    log.info("stereo calibration from %s: %d frames", reader.path, len(pairs))
    calib, report = calibrate_stereo(pairs, spec, size, min_corners=min_corners)
    check = rectification_check(calib, pairs, spec, min_corners=min_corners)
    report["rectification_check"] = {k: v for k, v in check.items() if k != "per_pair"}
    report.update(
        recording=str(reader.path), image_size=list(size), frames_in_recording=len(reader),
        frames_screened_usable=screened, frame_indices=[int(f.index) for f in frames], board=spec.describe(),
    )
    return calib, report


# --------------------------------------------------------------------------- lasers
def _stereo_filter(obs: LaserBoardObservation, lines_r, K: np.ndarray, baseline: float, tol_px: float) -> dict[str, tuple[int, int]]:
    """Keep only line pixels whose board-plane point has a right-image ridge at the predicted spot."""
    counts = {}
    T = np.asarray(obs.T_cb, dtype=np.float64)
    n = T[:3, 2]
    d = -float(n @ T[:3, 3])
    for name, uv in obs.line_uv.items():
        if name not in LINE_NAMES or len(uv) == 0:
            counts[name] = (int(len(uv)), int(len(uv)))
            continue
        P = ray_plane_intersect(pixels_to_rays(uv, K), n, d)
        ok = stereo_check(P, lines_r.get(name), K, baseline, tol_px)
        counts[name] = (int(len(uv)), int(ok.sum()))
        obs.line_uv[name] = uv[ok]
    return counts


def laser_observations_from_recording(
    rec_path: str | Path,
    calib: ScannerCalibration,
    spec: CharucoSpec,
    *,
    step: int = 1,
    region: str = "margin",
    paper: str = "letter",
    margin_mm: float = 5.0,
    stereo_validate: bool = True,
    laser_params: LaserParams | None = None,
    max_frames: int | None = None,
    progress: ProgressFn | None = None,
) -> tuple[list[LaserBoardObservation], list[dict], np.ndarray]:
    """Board observations with laser lines from a recording: ``(observations, per_frame, K)``."""
    reader = _reader(rec_path)
    _check_size(reader, calib.stereo)
    rect = Rectifier(calib.stereo, 1.0)
    K = rect.K
    lp = laser_params if laser_params is not None else _default_laser_params(rect.image_size)
    positions = _positions(len(reader), step, max_frames)
    observations: list[LaserBoardObservation] = []
    per_frame: list[dict] = []
    for k, pos in enumerate(positions):
        f = reader.frame(pos)
        left_r, right_r = rect.rectify(f.left, f.right)
        lines = extract_laser_lines(left_r, lp)
        row: dict = {"index": int(f.index), "board": False}
        obs = make_observation(left_r, K, spec, lines.as_dict(), margin_mm, region=region, paper=paper)
        if obs is not None:
            row.update(board=True, n_corners=obs.n_corners, reproj_rms_px=obs.reproj_rms_px)
            if stereo_validate:
                lines_r = extract_laser_lines(right_r, lp)
                row["stereo_kept"] = {n: list(c) for n, c in _stereo_filter(obs, lines_r, K, rect.baseline, lp.stereo_tol_px).items()}
            row["points"] = {
                name: {r: int(len(board_points_from_line(obs, K, name, r))) for r in ("board", "margin")}
                for name in LINE_NAMES
            } if obs.page is not None else {name: {"board": int(len(board_points_from_line(obs, K, name, "board")))} for name in LINE_NAMES}
            observations.append(obs)
        per_frame.append(row)
        if progress is not None:
            progress(k, len(positions))
    return observations, per_frame, K


def lasers_from_recording(
    rec_path: str | Path,
    calib: ScannerCalibration,
    spec: CharucoSpec,
    *,
    step: int = 1,
    region: str = "margin",
    paper: str = "letter",
    margin_mm: float = 5.0,
    stereo_validate: bool = True,
    laser_params: LaserParams | None = None,
    names: Sequence[str] = LINE_NAMES,
    ransac_thresh_mm: float = 0.5,
    progress: ProgressFn | None = None,
) -> tuple[dict[str, LaserPlane], dict]:
    """Laser-plane calibration from a recording of the board crossed by the laser lines.

    ``calib.stereo`` must be the calibration of this camera (frames are rectified with it; the
    planes come out in its frame C). Hold the board at several distances (200-400 mm) and tilts
    so that both lines cross the *white paper margin* in every view. ``region`` (``"margin"``
    default, ``"board"``, ``"both"``) picks the line pixels used; ``paper`` must match how the
    board was printed. Returns ``(planes, report)``: only fitted lasers are in ``planes``; the
    report holds the :func:`fit_laser_planes` report, ``region_used`` per laser, in-sample
    residuals, the change versus ``calib.lasers`` (if any) and per-frame counts.
    """
    observations, per_frame, K = laser_observations_from_recording(
        rec_path, calib, spec, step=step, region=region, paper=paper, margin_mm=margin_mm,
        stereo_validate=stereo_validate, laser_params=laser_params, progress=progress,
    )
    log.info("laser calibration: board found in %d of %d frames", len(observations), len(per_frame))
    planes, report = fit_laser_planes(observations, K, names=tuple(names), ransac_thresh_mm=ransac_thresh_mm, region=region)
    region_used = {name: region for name in planes}
    missing = [n for n in names if n not in planes]
    if region == "margin" and missing and observations and observations[0].page is not None:
        extra, rep2 = fit_laser_planes(observations, K, names=tuple(missing), ransac_thresh_mm=ransac_thresh_mm, region="both")
        for name, plane in extra.items():
            log.warning("laser %r: too few margin points (%s); fitted on board + margin instead",
                        name, report["lasers"][name].get("reason"))
            rep2["lasers"][name]["fallback_from_margin"] = report["lasers"][name].get("reason")
            report["lasers"][name] = rep2["lasers"][name]
            planes[name] = plane
            region_used[name] = "both"
        report["ok"] = len(planes) == len(tuple(names))
    for name in names:
        report["lasers"][name]["region_used"] = region_used.get(name)
    report["region_used"] = region_used
    if planes:
        res = {}
        for name, plane in planes.items():
            r = check_laser_planes({name: plane}, observations, K, region=region_used[name])
            res[name] = {k: v for k, v in r["lasers"][name].items() if k != "per_observation"}
        report["residuals"] = res
    if calib.lasers:
        report["change_vs_input"] = {n: _plane_change(calib.lasers[n], planes[n]) for n in planes if n in calib.lasers}
    report.update(recording=str(rec_path), frames=len(per_frame), frames_with_board=len(observations),
                  board=spec.describe(), paper=paper, margin_mm=float(margin_mm), per_frame=per_frame)
    return planes, report


# --------------------------------------------------------------------------- IMU
def _board_orientations(reader: RecordingReader, rect: Rectifier, spec: CharucoSpec, positions: Sequence[int],
                        progress: ProgressFn | None = None) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """Camera timestamps and orientations ``R_wc = R_cb^T`` (board = world) of frames with a board pose."""
    K = rect.K
    ts, Rs, idx = [], [], []
    for k, pos in enumerate(positions):
        f = reader.frame(pos)
        left_r = rect.rectify_left(f.left)
        corners, ids = detect_charuco(left_r, spec)
        ok, T_cb = estimate_board_pose(corners, ids, spec, K, None)
        if ok:
            ts.append(float(f.t_host))
            Rs.append(T_cb[:3, :3].T)
            idx.append(int(f.index))
        if progress is not None:
            progress(k, len(positions))
    return np.asarray(ts, dtype=np.float64), np.asarray(Rs, dtype=np.float64).reshape(-1, 3, 3), idx


def imu_from_recording(
    rec_path: str | Path,
    calib: ScannerCalibration,
    spec: CharucoSpec,
    *,
    step: int = 1,
    max_offset_s: float = 0.2,
    progress: ProgressFn | None = None,
) -> tuple[ImuCalibration, dict]:
    """IMU-camera calibration (``R_ci``, time offset, gyro bias) from a recording.

    Record the board while holding the scanner still for 1-2 s, then rotating it about all three
    axes (+-20-40 deg, 0.5-2 Hz) for 10-60 s with the board in view. ``calib.stereo`` rectifies
    the left frames, so ``R_ci`` refers to its frame C. Returns ``(ImuCalibration, report)``.
    """
    reader = _reader(rec_path)
    _check_size(reader, calib.stereo)
    imu = reader.imu_arrays()
    if len(imu["t_host"]) < 200:
        raise ValueError(f"recording {reader.path} has only {len(imu['t_host'])} IMU samples; record with the IMU on")
    rect = Rectifier(calib.stereo, 1.0)
    positions = _positions(len(reader), step)
    cam_t, R_wc, idx = _board_orientations(reader, rect, spec, positions, progress)
    log.info("IMU calibration: board pose in %d of %d frames, %d gyro samples", len(cam_t), len(positions), len(imu["t_host"]))
    if len(cam_t) < 30:
        raise ValueError(f"the board was found in only {len(cam_t)} frames (need >= 30): keep it in view while rotating")
    imu_calib, report = calibrate_imu_camera(cam_t, R_wc, imu["t_host"], imu["gyro"], max_offset_s=max_offset_s)
    report.update(recording=str(reader.path), frames=len(positions), frames_with_pose=len(cam_t),
                  duration_s=float(cam_t[-1] - cam_t[0]), board=spec.describe())
    return imu_calib, report


def _imu_check(reader: RecordingReader, imu_calib: ImuCalibration, cam_t: np.ndarray, R_wc: np.ndarray) -> dict:
    """Rotation residuals between the board-derived camera motion and the calibrated gyro."""
    imu = reader.imu_arrays()
    if len(imu["t_host"]) < 50 or len(cam_t) < 3:
        return {"ok": None, "verdict": "no IMU data or too few board poses"}
    order = np.argsort(imu["t_host"], kind="stable")
    t_i, g_i = imu["t_host"][order], imu["gyro"][order]
    gp = GyroPredictor(imu_calib)
    res, motion = [], []
    j = 0
    for a in range(len(cam_t) - 1):
        b = a + 1
        if cam_t[b] - cam_t[a] > IMU_CHECK_MAX_DT_S:
            continue
        j_new = int(np.searchsorted(t_i, cam_t[b] - imu_calib.time_offset + 0.5, side="right"))
        if j_new > j:
            gp.add_arrays(t_i[j:j_new], g_i[j:j_new])
            j = j_new
        if not gp.covers(cam_t[a], cam_t[b]):
            continue
        R_cam = R_wc[a].T @ R_wc[b]
        R_gyr = gp.relative_rotation(cam_t[a], cam_t[b])
        res.append(rotation_angle_deg(R_cam.T @ R_gyr))
        motion.append(rotation_angle_deg(R_cam))
    if len(res) < 10:
        return {"ok": None, "n_intervals": len(res), "verdict": "too few board poses covered by IMU data"}
    res_a, mot = np.asarray(res), np.asarray(motion)
    med = float(np.median(res_a))
    ok = med < IMU_CHECK_OK_DEG
    return {
        "n_intervals": len(res), "median_residual_deg": med, "p95_residual_deg": float(np.percentile(res_a, 95)),
        "median_motion_deg": float(np.median(mot)), "ok": bool(ok),
        "verdict": "good" if ok else (f"gyro and board rotations disagree by {med:.2f} deg per frame (want < "
                                      f"{IMU_CHECK_OK_DEG} deg): recalibrate the IMU"),
    }


# --------------------------------------------------------------------------- health check
def check_from_recording(
    rec_path: str | Path,
    calib: ScannerCalibration,
    spec: CharucoSpec,
    *,
    step: int = 1,
    max_frames: int = 60,
    paper: str = "letter",
    region: str = "margin",
    progress: ProgressFn | None = None,
) -> dict:
    """Check an existing calibration on a new recording of the board.

    * ``stereo``: :func:`rectification_check` (vertical disparity, metric scale) on up to
      ``max_frames`` frames;
    * ``lasers`` (if the calibration has planes and the lines cross the board):
      :func:`check_laser_planes` on the same frames (``region``, default the white margin);
    * ``imu`` (if the calibration and the recording have IMU data): per-frame rotation residual
      between the board poses and the calibrated gyro (meaningful when the scanner moved).

    ``report["ok"]`` is False if any available check fails; ``verdict`` summarises.
    """
    reader = _reader(rec_path)
    _check_size(reader, calib.stereo)
    positions = _positions(len(reader), step, max_frames)
    report: dict = {"recording": str(reader.path), "frames": len(positions), "board": spec.describe()}
    pairs = []
    for pos in positions:
        f = reader.frame(pos)
        pairs.append((f.left, f.right))
    st = rectification_check(calib.stereo, pairs, spec)
    report["stereo"] = {k: v for k, v in st.items() if k != "per_pair"}
    del pairs
    verdicts = [] if st.get("ok") else [f"stereo: {st.get('verdict')}"]

    if calib.lasers:
        obs, _, K = laser_observations_from_recording(rec_path, calib, spec, step=step, region=region, paper=paper,
                                                      max_frames=max_frames)
        n_pts = sum(len(board_points_from_line(o, K, n)) for o in obs for n in calib.lasers if n in LINE_NAMES)
        if n_pts == 0:
            report["lasers"] = {"ok": None, "verdict": "the laser lines were not seen on the board's white margin"}
        else:
            lr = check_laser_planes(calib.lasers, obs, K, region=region)
            for e in lr["lasers"].values():
                e.pop("per_observation", None)
            report["lasers"] = lr
            if not lr["ok"]:
                bad = [f"{n}: {e.get('verdict')}" for n, e in lr["lasers"].items() if not e.get("ok")]
                verdicts.append("lasers: " + "; ".join(bad))
    else:
        report["lasers"] = {"ok": None, "verdict": "no laser planes in the calibration"}

    if calib.imu is not None and len(reader.imu_arrays()["t_host"]):
        rect = Rectifier(calib.stereo, 1.0)
        cam_t, R_wc, _ = _board_orientations(reader, rect, spec, _positions(len(reader), step, 600), progress)
        report["imu"] = _imu_check(reader, calib.imu, cam_t, R_wc)
        if report["imu"].get("ok") is False:
            verdicts.append("imu: " + report["imu"]["verdict"])
    else:
        report["imu"] = {"ok": None, "verdict": "no IMU calibration or no IMU samples"}

    checks = [report["stereo"].get("ok"), report["lasers"].get("ok"), report["imu"].get("ok")]
    report["ok"] = bool(all(c is not False for c in checks) and report["stereo"].get("ok"))
    report["verdict"] = "good" if report["ok"] else "; ".join(verdicts) or "stereo check failed"
    return report
