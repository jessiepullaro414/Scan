"""Launch-time drift check: does a stereo calibration still hold? No board needed.

In a good calibration every scene point lands on the same row in the rectified left and right
images. A knock or a temperature change that moves one camera shows up as a *row error*
(vertical disparity) ``dv`` between matched features, of roughly ``f * angle`` for a pitch change
between the cameras (0.3 deg is about 4 px at 1280x800).

* :func:`row_error` measures it on one rectified pair. ORB features (FAST corners with rotated
  BRIEF descriptors) are matched by Hamming distance, restricted to a band of rows and to the
  allowed disparities, and kept when they are mutual nearest neighbours (cross-check). ORB
  keypoints sit on the pixel grid of their pyramid level, which alone would quantise ``dv`` to
  whole pixels, so each match is refined with pyramidal Lucas-Kanade from the left keypoint into
  the right image (started at the ORB match, with a forward-backward consistency check).
* :func:`drift_check` rectifies a few frames with :class:`~ohs.vision.rectify.Rectifier` at
  ``scale`` (0.5 by default), pools the matches whose disparity is plausible (depth 120-3000 mm)
  and whose ``|dv|`` is below ``max_dv_px`` (gross mismatches), and reports the median ``|dv|``
  and its 90th percentile in full-resolution pixels. The state is ``"unknown"`` with fewer than
  ``min_matches`` matches (e.g. a blank wall), ``"drift"`` when the median exceeds
  ``threshold_px`` (0.6 px), else ``"ok"``.

Conventions: ``dv = v_left - v_right`` (as :func:`ohs.calib.stereo.rectification_check`) and
``disparity = u_left - u_right``, both in pixels of the images passed in.

Speed: about 40 ms per 1280x800 frame at ``scale=0.5`` (0.13 s for three). ORB would initialise
OpenCL on its first call in a process (about 0.8 s), so :func:`row_error` switches OpenCL off for
the calling thread (``cv2.ocl.setUseOpenCL`` is per thread; nothing in OpenHandScan uses OpenCL).
"""
from __future__ import annotations

import logging
from typing import Sequence

import cv2
import numpy as np

from ohs.types import StereoCalibration, StereoFrame
from ohs.vision.rectify import Rectifier

__all__ = ["drift_check", "row_error"]

log = logging.getLogger(__name__)

DEPTH_RANGE_MM = (120.0, 3000.0)  # plausible scene depths for the disparity filter
MAX_HAMMING = 64  # of 256 descriptor bits
LK_WIN = (15, 15)
LK_FB_PX = 0.25  # forward-backward disagreement allowed after the Lucas-Kanade refinement
LK_MOVE_PX = 2.0  # the refinement may move a match this far from the ORB keypoint
_LK_CRITERIA = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 30, 0.005)


def _gray(img: np.ndarray) -> np.ndarray:
    a = np.asarray(img)
    if a.ndim == 3:
        a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    if a.dtype != np.uint8:
        a = np.clip(a, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(a)


def _match_in_band(kl: np.ndarray, dl: np.ndarray, kr: np.ndarray, dr: np.ndarray, max_dv: float,
                   min_disp: float, max_disp: float) -> tuple[np.ndarray, np.ndarray]:
    """Mutual nearest-neighbour ORB matches among pairs on nearby rows with an allowed disparity.

    Returns index arrays ``(il, ir)`` into the left and right keypoints.
    """
    dv = kl[:, None, 1] - kr[None, :, 1]
    disp = kl[:, None, 0] - kr[None, :, 0]
    mask = ((np.abs(dv) < max_dv) & (disp > min_disp) & (disp < max_disp)).astype(np.uint8)
    if not mask.any():
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    fwd = {m.queryIdx: m.trainIdx for m in bf.match(dl, dr, mask) if m.distance <= MAX_HAMMING}
    bwd = {m.queryIdx: m.trainIdx for m in bf.match(dr, dl, np.ascontiguousarray(mask.T))}
    pairs = [(i, j) for i, j in fwd.items() if bwd.get(j) == i]
    if not pairs:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    il, ir = (np.asarray(v, dtype=np.int64) for v in zip(*pairs))
    return il, ir


def row_error(left_r: np.ndarray, right_r: np.ndarray, *, max_features: int = 2000, max_dv_px: float = 8.0,
              min_disparity: float = 0.0, max_disparity: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Per-match ``(dv, disparity)`` for one rectified pair, in the pixels of the given images.

    ``dv = v_left - v_right``, ``disparity = u_left - u_right``. Only matches with ``|dv| <
    max_dv_px`` and a disparity in ``(min_disparity, max_disparity)`` are returned (``None``: up to
    the image width). Positions are refined to sub-pixel precision (module docstring). Switches
    OpenCV's OpenCL off for the calling thread.
    """
    left, right = _gray(left_r), _gray(right_r)
    empty = np.zeros(0), np.zeros(0)
    cv2.ocl.setUseOpenCL(False)  # per thread; spares ORB's ~0.8 s OpenCL start-up (module docstring)
    orb = cv2.ORB_create(nfeatures=int(max_features))
    kpl, dl = orb.detectAndCompute(left, None)
    kpr, dr = orb.detectAndCompute(right, None)
    if dl is None or dr is None or len(kpl) == 0 or len(kpr) == 0:
        return empty
    kl = np.array([k.pt for k in kpl], dtype=np.float32)
    kr = np.array([k.pt for k in kpr], dtype=np.float32)
    max_disp = float(left.shape[1]) if max_disparity is None else float(max_disparity)
    # the band is a little wider than max_dv_px: the refinement may still move a match into it
    il, ir = _match_in_band(kl, dl, kr, dr, float(max_dv_px) + LK_MOVE_PX, float(min_disparity) - LK_MOVE_PX,
                            max_disp + LK_MOVE_PX)
    if len(il) == 0:
        return empty
    p0 = kl[il].reshape(-1, 1, 2)
    p1, st1, _ = cv2.calcOpticalFlowPyrLK(left, right, p0, kr[ir].reshape(-1, 1, 2).copy(), winSize=LK_WIN,
                                          maxLevel=1, criteria=_LK_CRITERIA, flags=cv2.OPTFLOW_USE_INITIAL_FLOW)
    back, st2, _ = cv2.calcOpticalFlowPyrLK(right, left, p1, p0.copy(), winSize=LK_WIN, maxLevel=1,
                                            criteria=_LK_CRITERIA, flags=cv2.OPTFLOW_USE_INITIAL_FLOW)
    p0, p1, back = p0.reshape(-1, 2), p1.reshape(-1, 2), back.reshape(-1, 2)
    dv = (p0[:, 1] - p1[:, 1]).astype(np.float64)
    disp = (p0[:, 0] - p1[:, 0]).astype(np.float64)
    ok = (st1.ravel() == 1) & (st2.ravel() == 1)
    ok &= np.linalg.norm(back - p0, axis=1) < LK_FB_PX
    ok &= np.linalg.norm(p1 - kr[ir], axis=1) < LK_MOVE_PX
    ok &= (np.abs(dv) < max_dv_px) & (disp > min_disparity) & (disp < max_disp)
    return dv[ok], disp[ok]


def _px(v: float) -> str:
    return f"{v:.2f}" if v < 1.0 else f"{v:.1f}"


def drift_check(frames: Sequence[StereoFrame], stereo: StereoCalibration, *, scale: float = 0.5,
                threshold_px: float = 0.6, min_matches: int = 40, max_dv_px: float = 8.0,
                max_features: int = 2000) -> dict:
    """Board-free check of a stereo calibration on a few raw frames (module docstring).

    Returns ``{"state": "ok" | "drift" | "unknown", "dv_px": median |row error| in FULL-resolution
    px or None, "p90_px": its 90th percentile or None, "matches": int, "frames": int (frames
    used), "message": one plain sentence for the user}`` plus ``"dv_signed_px"`` (median signed
    ``dv``, full resolution, or None). ``max_dv_px`` (gross-mismatch limit) is in pixels of the
    rectified images at ``scale``. Frames whose size does not match the calibration are skipped.
    """
    rect = Rectifier(stereo, scale)
    f = float(rect.K[0, 0])
    p1, p2 = rect.calib.P1, rect.calib.P2
    d_inf = float(p1[0, 2] - p2[0, 2])  # disparity of a point at infinity (0 with zero-disparity rectification)
    d_lo = d_inf + f * rect.baseline / DEPTH_RANGE_MM[1]
    d_hi = d_inf + f * rect.baseline / DEPTH_RANGE_MM[0]
    w, h = rect.raw_image_size
    pooled: list[np.ndarray] = []
    n_frames = 0
    wrong_size = None
    for fr in frames:
        if tuple(fr.left.shape[:2]) != (h, w) or tuple(fr.right.shape[:2]) != (h, w):
            wrong_size = (int(fr.left.shape[1]), int(fr.left.shape[0]))
            continue
        left_r, right_r = rect.rectify(_gray(fr.left), _gray(fr.right))
        dv, _ = row_error(left_r, right_r, max_features=max_features, max_dv_px=max_dv_px, min_disparity=d_lo,
                          max_disparity=d_hi)
        pooled.append(dv)
        n_frames += 1
    dv = np.concatenate(pooled) / float(scale) if pooled else np.zeros(0)
    out: dict = {"state": "unknown", "dv_px": None, "p90_px": None, "dv_signed_px": None, "matches": int(len(dv)),
                 "frames": n_frames}
    if n_frames == 0:
        if wrong_size is not None:
            out["message"] = (f"The camera images are {wrong_size[0]}x{wrong_size[1]} but the calibration is for "
                              f"{w}x{h}, so it could not be checked.")
        else:
            out["message"] = "There were no camera frames to check the calibration with."
        return out
    if len(dv) < min_matches:
        out["message"] = (f"Too little texture in view to check the calibration ({len(dv)} matched features, need "
                          f"{min_matches}); the check needs a scene with some detail.")
        log.info("drift check: unknown (%d matches in %d frames)", len(dv), n_frames)
        return out
    a = np.abs(dv)
    med, p90 = float(np.median(a)), float(np.percentile(a, 90))
    out.update(dv_px=med, p90_px=p90, dv_signed_px=float(np.median(dv)))
    if med > threshold_px:
        out["state"] = "drift"
        out["message"] = (f"Left and right rows disagree by {_px(med)} px: the calibration may have drifted (a knock "
                          "or a temperature change). Run the 2-minute check.")
    else:
        out["state"] = "ok"
        out["message"] = f"Left and right rows agree to {_px(med)} px: the calibration still holds."
    log.info("drift check: %s, median |dv| %.3f px (p90 %.3f) over %d matches in %d frames", out["state"], med, p90,
             len(dv), n_frames)
    return out
