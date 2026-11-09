"""Stereo calibration of the raw (distorted, unrectified) camera pair from ChArUco views.

:func:`calibrate_stereo` pipeline:

1. detect ChArUco corners in every raw left and right image (:func:`ohs.calib.board.detect_charuco`);
   a view needs ``min_corners`` non-collinear corners;
2. calibrate each camera's intrinsics from *all* of its own views (pinhole + 5 distortion
   coefficients k1 k2 p1 p2 k3); drop outlier corners (reprojection error > max(0.75 px,
   5 x median): in distant views OpenCV's sub-pixel refinement can lock onto a nearby marker
   corner) and outlier views (rms > max(3 x median, 1 px)), then recalibrate until stable;
3. ``cv2.stereoCalibrate`` with ``CALIB_FIX_INTRINSIC`` on the corners seen by both cameras, then a
   joint refinement of intrinsics and extrinsics (``CALIB_USE_INTRINSIC_GUESS`` +
   ``CALIB_USE_EXTRINSIC_GUESS``); the joint result is kept unless it is non-finite, worse, or moves
   a focal length by more than 5 % (then the fixed-intrinsics solution is used and the report says so);
4. ``StereoCalibration.build(..., alpha=0)`` computes the rectification.

:func:`rectification_check` maps detected corners of (new) raw pairs through the rectification and
measures the vertical disparity ``|v_left - v_right|`` (should be ~0) and the metric scale of the
board reconstructed by stereo, the two key health metrics of a stereo calibration.

Conventions: OpenCV ``x_right = R @ x_left + T`` (mm), pixel-centre image coordinates.
Reports are plain JSON-serialisable dicts.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from ..geometry import rotation_angle_deg
from ..types import StereoCalibration
from .board import CharucoSpec, board_corner_points, detect_charuco, make_charuco_board

__all__ = ["calibrate_stereo", "rectification_check", "COVERAGE_GRID"]

log = logging.getLogger(__name__)

COVERAGE_GRID = 8  # image coverage is measured on a COVERAGE_GRID x COVERAGE_GRID grid
GOOD_RMS_PX = 0.5
GOOD_COVERAGE = 0.6
MIN_VIEWS = 4  # absolute minimum per camera / for the stereo step (15-25 recommended)
CORNER_OUTLIER_PX = 0.75  # corners with a larger reprojection error (and > 5 x median) are dropped
_CRITERIA = (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 100, 1e-10)
_UNDISTORT_CRITERIA = (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 40, 1e-10)


@dataclass
class _View:
    pair: int
    ids: np.ndarray  # (N,) int
    uv: np.ndarray  # (N, 2) float32


# --------------------------------------------------------------------------- helpers
def _check_image(img: np.ndarray, image_size: tuple[int, int], what: str) -> None:
    h, w = np.asarray(img).shape[:2]
    if (w, h) != tuple(image_size):
        raise ValueError(f"{what} is {w}x{h}, expected {image_size[0]}x{image_size[1]}")


def _usable(ids: np.ndarray, spec: CharucoSpec, min_corners: int) -> str | None:
    """None if a detection is usable, else the reason."""
    if len(ids) < min_corners:
        return f"only {len(ids)} corners (< {min_corners})"
    if make_charuco_board(spec).checkCharucoCornersCollinear(np.asarray(ids, np.int32).reshape(-1, 1)):
        return "corners are collinear"
    return None


def _cv_points(views: Sequence[_View], spec: CharucoSpec) -> tuple[list[np.ndarray], list[np.ndarray]]:
    corners3d = board_corner_points(spec).astype(np.float32)
    obj = [corners3d[v.ids].reshape(-1, 1, 3) for v in views]
    img = [np.asarray(v.uv, np.float32).reshape(-1, 1, 2) for v in views]
    return obj, img


def _coverage(views: Sequence[_View], image_size: tuple[int, int], grid: int = COVERAGE_GRID) -> float:
    """Fraction of the grid x grid image cells that contain at least one used corner."""
    if not views:
        return 0.0
    uv = np.vstack([v.uv for v in views]).astype(np.float64)
    w, h = image_size
    cx = np.clip(np.floor(uv[:, 0] / w * grid), 0, grid - 1).astype(int)
    cy = np.clip(np.floor(uv[:, 1] / h * grid), 0, grid - 1).astype(int)
    return float(len(np.unique(cy * grid + cx)) / grid**2)


def _tilts_deg(rvecs: Sequence[np.ndarray]) -> np.ndarray:
    """Angle between each board normal and the optical axis (deg)."""
    out = []
    for r in rvecs:
        R, _ = cv2.Rodrigues(np.asarray(r, np.float64))
        out.append(np.degrees(np.arccos(np.clip(abs(R[2, 2]), 0.0, 1.0))))
    return np.asarray(out)


def _calibrate_camera(
    views: list[_View], spec: CharucoSpec, image_size: tuple[int, int], name: str, min_corners: int
) -> tuple[dict, list[_View], list[dict]]:
    """Intrinsics of one camera from its views, with corner- and view-level outlier rejection.

    Each round: calibrate, drop corners whose reprojection error exceeds
    max(:data:`CORNER_OUTLIER_PX`, 5 x median) (e.g. a corner refined onto a nearby marker corner),
    then views whose remaining rms exceeds max(3 x median, 1 px); repeat until nothing changes.
    Returns (result dict with K, D, rms, per_view, rvecs, n_corners_rejected; used views; rejected).
    """
    rejected: list[dict] = []
    used = list(views)
    n_corner_rej = 0
    for round_ in range(4):
        if len(used) < MIN_VIEWS:
            raise ValueError(f"{name} camera: only {len(used)} usable views, need at least {MIN_VIEWS} (15-25 recommended)")
        obj, img = _cv_points(used, spec)
        rms, K, D, rvecs, tvecs, _, _, per_view = cv2.calibrateCameraExtended(
            obj, img, tuple(image_size), None, None, flags=0, criteria=_CRITERIA
        )
        per_view = np.asarray(per_view, dtype=np.float64).reshape(-1)
        if round_ == 3:
            break
        res = [
            np.linalg.norm(i.reshape(-1, 2) - cv2.projectPoints(o, r, t, K, D)[0].reshape(-1, 2), axis=1)
            for o, i, r, t in zip(obj, img, rvecs, tvecs)
        ]
        corner_limit = max(CORNER_OUTLIER_PX, 5.0 * float(np.median(np.concatenate(res))))
        kept_rms = np.array([np.sqrt(np.mean(r[r <= corner_limit] ** 2)) if (r <= corner_limit).any() else np.inf
                             for r in res])
        view_limit = max(3.0 * float(np.median(kept_rms)), 1.0)
        new_used: list[_View] = []
        round_rejected: list[dict] = []
        round_corners = 0
        for v, r, e in zip(used, res, kept_rms):
            if e > view_limit:
                reason = f"view rms {e:.2f} px > {view_limit:.2f} px"
                round_rejected.append({"pair": v.pair, "camera": name, "reason": reason})
                continue
            keep = r <= corner_limit
            if not keep.all():
                round_corners += int((~keep).sum())
                v = _View(v.pair, v.ids[keep], v.uv[keep])
                reason = _usable(v.ids, spec, min_corners)
                if reason is not None:
                    reason = f"after dropping outlier corners: {reason}"
                    round_rejected.append({"pair": v.pair, "camera": name, "reason": reason})
                    continue
            new_used.append(v)
        if (not round_rejected and round_corners == 0) or len(new_used) < MIN_VIEWS:
            break
        log.info("%s camera: round %d dropped %d outlier corner(s) and %d view(s), recalibrating",
                 name, round_ + 1, round_corners, len(round_rejected))
        rejected += round_rejected
        n_corner_rej += round_corners
        used = new_used
    log.info("%s camera: rms %.3f px from %d views, fx %.2f fy %.2f", name, rms, len(used), K[0, 0], K[1, 1])
    result = {"K": K, "D": np.asarray(D, np.float64).reshape(-1), "rms": float(rms), "per_view": per_view,
              "rvecs": rvecs, "n_corners_rejected": n_corner_rej}
    return result, used, rejected


def _stereo_step(
    obj: list[np.ndarray], img1: list[np.ndarray], img2: list[np.ndarray],
    K1: np.ndarray, D1: np.ndarray, K2: np.ndarray, D2: np.ndarray, image_size: tuple[int, int],
    R: np.ndarray, T: np.ndarray, flags: int,
) -> tuple[float, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    out = cv2.stereoCalibrateExtended(
        obj, img1, img2, K1.copy(), D1.reshape(1, -1).copy(), K2.copy(), D2.reshape(1, -1).copy(),
        tuple(image_size), R.copy(), T.reshape(3, 1).copy(), flags=flags, criteria=_CRITERIA,
    )
    rms, K1o, D1o, K2o, D2o, Ro, To = out[:7]
    per_view = np.asarray(out[-1], dtype=np.float64).reshape(-1, 2)
    D1o, D2o, To = np.asarray(D1o).reshape(-1), np.asarray(D2o).reshape(-1), np.asarray(To).reshape(3)
    return float(rms), K1o, D1o, K2o, D2o, Ro, To, per_view


def _verdict(rms: float, cov_l: float, cov_r: float, n_stereo: int, max_tilt: float) -> tuple[bool, str]:
    ok = rms < GOOD_RMS_PX and cov_l > GOOD_COVERAGE and cov_r > GOOD_COVERAGE
    if ok:
        return True, "good"
    advice = []
    if rms >= GOOD_RMS_PX:
        advice.append(
            f"reprojection error {rms:.2f} px is high (want < {GOOD_RMS_PX} px): hold the board still or use a "
            "shorter exposure (motion blur), check focus, make sure the board is flat (glued to glass) and "
            "that the printed square size matches the spec"
        )
    for name, cov in (("left", cov_l), ("right", cov_r)):
        if cov <= GOOD_COVERAGE:
            advice.append(
                f"corners cover only {cov:.0%} of the {name} image (want > {GOOD_COVERAGE:.0%}): add views with the "
                "board near the image corners and edges"
            )
    if n_stereo < 12:
        advice.append(f"only {n_stereo} stereo views were used: capture 15-25 views")
    if max_tilt < 20.0:
        advice.append("the board was nearly fronto-parallel in all views: tilt it 20-45 deg in different directions")
    return False, "; ".join(advice)


# --------------------------------------------------------------------------- public API
def calibrate_stereo(
    pairs: Sequence[tuple[np.ndarray, np.ndarray]],
    spec: CharucoSpec,
    image_size: tuple[int, int],
    min_corners: int = 8,
) -> tuple[StereoCalibration, dict]:
    """Calibrate the stereo pair from raw ``(left, right)`` grey images of a ChArUco board.

    ``image_size`` is ``(w, h)`` of ONE camera. Returns ``(StereoCalibration, report)``; the report
    holds the stereo rms (``rms``), per-camera rms, per-view errors, views used/rejected, the image
    coverage of an 8 x 8 grid per camera and a plain-language ``verdict`` ("good" or advice).
    Raises ``ValueError`` if there are too few usable views.
    """
    size = (int(image_size[0]), int(image_size[1]))
    rejected: list[dict] = []
    left_views: list[_View] = []
    right_views: list[_View] = []
    for k, (left, right) in enumerate(pairs):
        _check_image(left, size, f"pair {k} left image")
        _check_image(right, size, f"pair {k} right image")
        for img, name, bucket in ((left, "left", left_views), (right, "right", right_views)):
            uv, ids = detect_charuco(img, spec)
            reason = _usable(ids, spec, min_corners)
            if reason is None:
                bucket.append(_View(k, ids.astype(int), uv))
            else:
                rejected.append({"pair": k, "camera": name, "reason": reason})
    log.info("ChArUco detected in %d left and %d right images of %d pairs", len(left_views), len(right_views), len(pairs))

    cam_l, used_l, rej_l = _calibrate_camera(left_views, spec, size, "left", min_corners)
    cam_r, used_r, rej_r = _calibrate_camera(right_views, spec, size, "right", min_corners)
    rejected += rej_l + rej_r

    # stereo views: corners seen by both cameras in pairs used by both intrinsic calibrations
    by_pair_r = {v.pair: v for v in used_r}
    stereo_pairs: list[int] = []
    obj, img1, img2 = [], [], []
    corners3d = board_corner_points(spec).astype(np.float32)
    for vl in used_l:
        vr = by_pair_r.get(vl.pair)
        if vr is None:
            continue
        common, il, ir = np.intersect1d(vl.ids, vr.ids, assume_unique=True, return_indices=True)
        reason = _usable(common, spec, min_corners)
        if reason is not None:
            rejected.append({"pair": vl.pair, "camera": "stereo", "reason": f"common corners: {reason}"})
            continue
        stereo_pairs.append(vl.pair)
        obj.append(corners3d[common].reshape(-1, 1, 3))
        img1.append(vl.uv[il].astype(np.float32).reshape(-1, 1, 2))
        img2.append(vr.uv[ir].astype(np.float32).reshape(-1, 1, 2))
    if len(stereo_pairs) < MIN_VIEWS:
        raise ValueError(
            f"only {len(stereo_pairs)} stereo views with >= {min_corners} common corners, need at least {MIN_VIEWS}"
        )

    K1, D1, K2, D2 = cam_l["K"], cam_l["D"], cam_r["K"], cam_r["D"]
    rms_fixed, _, _, _, _, R, T, pv_fixed = _stereo_step(
        obj, img1, img2, K1, D1, K2, D2, size, np.eye(3), np.zeros(3), cv2.CALIB_FIX_INTRINSIC
    )
    log.info("stereo (fixed intrinsics): rms %.3f px, |T| %.3f mm", rms_fixed, float(np.linalg.norm(T)))
    joint = _stereo_step(
        obj, img1, img2, K1, D1, K2, D2, size, R, T, cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_USE_EXTRINSIC_GUESS
    )
    rms_j, K1j, D1j, K2j, D2j, Rj, Tj, pv_j = joint
    finite = all(np.all(np.isfinite(a)) for a in (K1j, D1j, K2j, D2j, Rj, Tj)) and np.isfinite(rms_j)
    focal_shift = max(abs(K1j[0, 0] / K1[0, 0] - 1.0), abs(K2j[0, 0] / K2[0, 0] - 1.0)) if finite else np.inf
    if finite and rms_j <= rms_fixed * 1.02 + 1e-6 and focal_shift < 0.05:
        K1, D1, K2, D2, R, T, rms, per_view = K1j, D1j, K2j, D2j, Rj, Tj, rms_j, pv_j
        joint_status = "applied"
    else:
        rms, per_view = rms_fixed, pv_fixed
        joint_status = f"rejected (rms {rms_j:.3f} px vs {rms_fixed:.3f} px, focal change {focal_shift:.1%})"
        log.warning("joint stereo refinement %s; keeping the fixed-intrinsics solution", joint_status)
    log.info("stereo: rms %.3f px, baseline %.3f mm, joint refinement %s", rms, float(np.linalg.norm(T)), joint_status)

    calib = StereoCalibration.build(size, K1, D1, K2, D2, R, T, rms=rms, alpha=0.0)

    cov_l, cov_r = _coverage(used_l, size), _coverage(used_r, size)
    tilts = _tilts_deg(list(cam_l["rvecs"]) + list(cam_r["rvecs"]))
    max_tilt = float(tilts.max()) if len(tilts) else 0.0
    ok, verdict = _verdict(rms, cov_l, cov_r, len(stereo_pairs), max_tilt)
    report = {
        "rms": float(rms),
        "rms_fixed_intrinsics": float(rms_fixed),
        "rms_left": cam_l["rms"],
        "rms_right": cam_r["rms"],
        "joint_refinement": joint_status,
        "per_view_errors": [
            {"pair": int(p), "left": float(e[0]), "right": float(e[1])} for p, e in zip(stereo_pairs, per_view)
        ],
        "per_view_errors_left": [{"pair": v.pair, "rms": float(e)} for v, e in zip(used_l, cam_l["per_view"])],
        "per_view_errors_right": [{"pair": v.pair, "rms": float(e)} for v, e in zip(used_r, cam_r["per_view"])],
        "views_total": len(pairs),
        "views_used": len(stereo_pairs),
        "views_used_left": len(used_l),
        "views_used_right": len(used_r),
        "views_rejected": rejected,
        "corners_rejected_left": cam_l["n_corners_rejected"],
        "corners_rejected_right": cam_r["n_corners_rejected"],
        "coverage_left": cov_l,
        "coverage_right": cov_r,
        "max_tilt_deg": max_tilt,
        "median_tilt_deg": float(np.median(tilts)) if len(tilts) else 0.0,
        "baseline_mm": float(np.linalg.norm(T)),
        "relative_rotation_deg": rotation_angle_deg(np.asarray(R)),
        "fx_left": float(K1[0, 0]),
        "fx_right": float(K2[0, 0]),
        "quality_ok": ok,
        "verdict": verdict,
    }
    log.info("stereo calibration verdict: %s", verdict)
    return calib, report


def rectification_check(
    calib: StereoCalibration, pairs: Sequence[tuple[np.ndarray, np.ndarray]], spec: CharucoSpec, min_corners: int = 8
) -> dict:
    """Health check of a stereo calibration on raw ``(left, right)`` board images.

    ChArUco corners are detected in the raw images and mapped through undistortion + rectification
    (``cv2.undistortPoints`` with ``R1/P1`` and ``R2/P2``). Reports the vertical disparity of matched
    corners (``mean_abs_dv_px``, ideally < 0.2-0.3 px) and the board square size reconstructed by
    stereo triangulation versus the printed size (``scale_error_pct``, checks the baseline).
    """
    K_rect = calib.K_rect
    f, cx, cy, B = K_rect[0, 0], K_rect[0, 2], K_rect[1, 2], calib.baseline
    cx2 = calib.P2[0, 2]
    corners3d = board_corner_points(spec)
    dv_all: list[np.ndarray] = []
    per_pair: list[dict] = []
    scale_ratios: list[float] = []
    for k, (left, right) in enumerate(pairs):
        uv_l, ids_l = detect_charuco(left, spec)
        uv_r, ids_r = detect_charuco(right, spec)
        common, il, ir = np.intersect1d(ids_l, ids_r, assume_unique=True, return_indices=True)
        if len(common) < min_corners:
            per_pair.append({"pair": k, "n_points": int(len(common)), "mean_abs_dv_px": None})
            continue
        rl = cv2.undistortPoints(uv_l[il].reshape(-1, 1, 2).astype(np.float64), calib.K1, calib.D1,
                                 R=calib.R1, P=calib.P1, criteria=_UNDISTORT_CRITERIA).reshape(-1, 2)
        rr = cv2.undistortPoints(uv_r[ir].reshape(-1, 1, 2).astype(np.float64), calib.K2, calib.D2,
                                 R=calib.R2, P=calib.P2, criteria=_UNDISTORT_CRITERIA).reshape(-1, 2)
        dv = rl[:, 1] - rr[:, 1]
        dv_all.append(dv)
        # stereo triangulation in the rectified frame, then compare all corner-to-corner distances
        disp = (rl[:, 0] - cx) - (rr[:, 0] - cx2)
        valid = disp > 1e-3
        if valid.sum() >= 3:
            Z = f * B / disp[valid]
            X = np.stack([(rl[valid, 0] - cx) * Z / f, (0.5 * (rl[valid, 1] + rr[valid, 1]) - cy) * Z / f, Z], axis=1)
            Pb = corners3d[common[valid]]
            iu, ju = np.triu_indices(len(X), k=1)
            d_true = np.linalg.norm(Pb[iu] - Pb[ju], axis=1)
            d_est = np.linalg.norm(X[iu] - X[ju], axis=1)
            sel = d_true >= 2.0 * spec.square_mm
            if sel.any():
                scale_ratios.append(float(np.median(d_est[sel] / d_true[sel])))
        per_pair.append({"pair": k, "n_points": int(len(common)), "mean_abs_dv_px": float(np.mean(np.abs(dv)))})
    if not dv_all:
        return {"n_pairs": 0, "n_points": 0, "mean_abs_dv_px": None, "ok": False,
                "verdict": "the board was not found in both images of any pair", "per_pair": per_pair}
    dv = np.concatenate(dv_all)
    mean_abs = float(np.mean(np.abs(dv)))
    scale_err = float((np.median(scale_ratios) - 1.0) * 100.0) if scale_ratios else None
    ok = mean_abs < 0.3 and (scale_err is None or abs(scale_err) < 0.5)
    if ok:
        verdict = "good"
    elif mean_abs >= 0.3:
        verdict = (f"vertical disparity {mean_abs:.2f} px is too large (want < 0.3 px): the cameras may have moved "
                   "or the calibration belongs to another device/resolution; recalibrate")
    else:
        verdict = (f"stereo scale is off by {scale_err:.2f} %: check the printed square size (print at 100 %) "
                   "and recalibrate")
    report = {
        "n_pairs": len(dv_all),
        "n_points": int(len(dv)),
        "mean_abs_dv_px": mean_abs,
        "median_abs_dv_px": float(np.median(np.abs(dv))),
        "p95_abs_dv_px": float(np.percentile(np.abs(dv), 95)),
        "max_abs_dv_px": float(np.max(np.abs(dv))),
        "mean_dv_px": float(np.mean(dv)),
        "scale_error_pct": scale_err,
        "ok": ok,
        "verdict": verdict,
        "per_pair": per_pair,
    }
    log.info("rectification check: mean |dv| %.3f px over %d corners, scale error %s %%", mean_abs, len(dv),
             "n/a" if scale_err is None else f"{scale_err:.3f}")
    return report
