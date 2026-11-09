"""Live virtual calibration scanner: the simulated scanner looking at the printed ChArUco page.

:class:`SimBoardCamera` renders raw stereo frames on the fly (nothing on disk) behind the camera
interface of :class:`~ohs.devices.camera.ReplayCamera` (``open`` / ``read`` / ``info`` / ``close``),
and its :class:`SimFeed` delivers IMU samples in step with the frames (the interface of
``ohs.server.devices.ReplayFeed``). The whole calibration wizard (stereo, lasers, IMU, check) can
therefore run end to end without hardware, and every result can be compared with
``SimBoardCamera.true_calibration``.

**Scripts** (:data:`SCRIPTS`, one per instance, looped by default; one pass lasts ``period_s``):

* ``"stereo"``: a tour of 16 board poses (:func:`~ohs.sim.datasets.stereo_calib_board_poses`). The
  board dwells ``dwell_s`` (1.6 s) at each pose and moves to the next in ``move_s`` (0.7 s), with
  minimum jerk on the board centre and slerp on the rotation. The last pose moves back to the first,
  so the tour loops seamlessly. The scanner stands still: the gyro shows only noise and bias.
* ``"lasers"``: the same with the 8 poses of :func:`~ohs.sim.datasets.laser_calib_board_poses`
  and both laser lines painted on the page. ``"check"``: 4 laser poses (``seed + 1``), lines painted.
* ``"imu"``: the board lies still on a table. The scanner holds still for 2.5 s, rotates about all
  three axes for 25 s, eases back to the start pose in 1.5 s and holds still for 2 s, so the pass
  loops seamlessly. The rotation is a :class:`~ohs.sim.trajectory.RotatingTrajectory` about a pivot
  just in front of the board (the scanner orbits it, so the board stays in view), scaled so that the
  rotation vector peaks at :data:`IMU_PEAK_DEG` about the camera x / y / z axes.

**Rendering**, per raw camera (the method of :class:`ohs.calib.board.BoardViewRenderer`): the full
printed page (:func:`~ohs.calib.board.render_charuco_page_image`: board, white margin, scale bar
and label; 5 texels per mm) is warped with the plane homography of the ideal pinhole camera at 3x
supersampling, only over the page's bounding box, onto a backdrop plane, and area-averaged down.
The backdrop is a wall 1.15 m away (stereo, lasers, check) or the table under the board (imu): a
low-contrast texture of about 45-80 DN, seen in true stereo. The image is then remapped through
the lens distortion (maps from ``cv2.undistortPoints``), the laser stripes are added, and it is
blurred (0.7 px) and gets Gaussian noise (1 DN). Paper is 220 DN and ink 25 DN.

**Laser stripes** (lasers, check): each true laser plane (frame C) is intersected with the page
plane; the line is clipped to the paper and to the 60 deg fan of
:func:`~ohs.sim.nominal.nominal_emitters`, sampled every 0.2 mm and projected into both raw images
with :func:`~ohs.sim.render.project_to_raw`. A Gaussian profile (sigma 1.2 px) centred on the
projected line, :data:`LASER_DN` on paper at 250 mm (slowly weaker with range and incidence), is
scaled by the local page brightness, so it is about 9x dimmer on ink, and clips at 255. These
scripts use a shorter exposure (:data:`LASER_EXPOSURE`: paper 77 DN, ink 9 DN), as a laser
calibration on the real scanner would: on 220 DN paper a stripe clips at 255 with only 35 DN of
contrast, weaker than the white bars of the printed pattern, and the laser extractor's per-row
maximum then lands on the pattern instead of the stripe, so no margin points are found.

**Frames:** board poses are ``T_cb`` in frame C, the rectified left camera of
``true_calibration``. The raw left camera is ``x_C = R1 x_rawL``; the raw right camera is
``x_rawR = R x_rawL + T`` (OpenCV ``stereoCalibrate`` convention).

**Timing** (the conventions of :func:`ohs.sim.datasets.make_scan_recording`): frame ``k`` is
exposed at host time ``t_exp = t0 + k / fps`` of a virtual clock and stamped
``t_host = t_exp + time_offset`` (the true ``imu.time_offset``) plus about 0.2 ms of jitter. IMU
samples carry host time; a sample at host time ``s`` sees the motion at camera time
``s + time_offset``. The script time of frame ``k`` is ``(k - k_restart) / fps`` (modulo
``period_s`` when looping): :meth:`SimBoardCamera.restart` moves ``k_restart`` to the next frame,
and the virtual clock keeps running. With ``realtime`` the frames are paced to the wall clock
(frame ``k`` is due ``k / fps`` after the first read); a reader that falls more than a second
behind is paced from then on instead of getting a burst of frames, so the virtual clock then runs
slower than the wall clock. The renderers take about 1 s to build (:meth:`SimBoardCamera.open`);
a 1280x800 stereo frame renders in about 20 ms (stereo, imu) to 30 ms (lasers, check).
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import asdict
from typing import Sequence

import cv2
import numpy as np

from ohs.calib.board import CharucoPageLayout, CharucoSpec, render_charuco_page_image
from ohs.geometry import invert_se3, make_se3, project_points, so3_exp, so3_log
from ohs.sim.datasets import laser_calib_board_poses, stereo_calib_board_poses
from ohs.sim.imu import ImuSimParams, so3_log_batch, synthesize_imu_arrays
from ohs.sim.nominal import nominal_calibration, nominal_emitters, nominal_imu_calibration
from ohs.sim.render import project_to_raw
from ohs.sim.trajectory import RotatingTrajectory, StaticTrajectory, look_at
from ohs.types import Event, ImuSample, LaserPlane, ScannerCalibration, StereoCalibration, StereoFrame

__all__ = [
    "SCRIPTS",
    "DWELL_S",
    "MOVE_S",
    "IMU_PEAK_DEG",
    "LASER_DN",
    "LASER_EXPOSURE",
    "SimFeed",
    "SimBoardCamera",
]

log = logging.getLogger(__name__)

SCRIPTS = ("stereo", "lasers", "imu", "check")
LINE_NAMES = ("vertical", "horizontal")

DWELL_S = 1.6  # board tours: time at each pose
MOVE_S = 0.7  # board tours: time to move to the next pose
TOUR_POSES = {"stereo": 16, "lasers": 8, "check": 4}
CHECK_SEED_OFFSET = 1  # the check poses are laser_calib_board_poses(..., seed + 1)
IMU_STILL_S = 2.5  # imu script: still, rotate, return, still
IMU_ROTATE_S = 25.0
IMU_RETURN_S = 1.5
IMU_END_STILL_S = 2.0
IMU_PEAK_DEG = (30.0, 35.0, 40.0)  # rotation peaks about the camera x / y / z axes
IMU_EYE_MM = (10.0, -50.0, 340.0)  # camera above the board (sim world, Z up, board centred at 0)
IMU_PIVOT = 0.8  # the scanner orbits a point at this fraction of the distance to the board

SUPERSAMPLE = 3
TEXTURE_PX_PER_MM = 5.0  # page texture at 1280 px image width (scaled with the width)
BACKDROP_PX_PER_MM = 0.5
PAPER_DN = 220.0
INK_DN = 25.0
BACKDROP_DN = (62.0, 4.0, 8.0)  # mean, noise std, half-range of the gradient
LASER_EXPOSURE = 0.35  # relative exposure of the lasers / check scripts (paper 77 DN)
LASER_DN = 180.0  # stripe peak on paper at 250 mm, before blur and clipping
LASER_SIGMA_PX = 1.2
LASER_SAMPLE_MM = 0.2
BLUR_PX = 0.7
NOISE_DN = 1.0
JITTER_S = 2e-4
RESYNC_S = 1.0  # realtime: a reader this far behind is paced from now on
WALL_POSE = (np.radians([3.0, -6.0, 0.0]), (0.0, 0.0, 1150.0))  # backdrop wall in frame C (rotvec, mm)
WALL_SIZE_MM = (3600.0, 2600.0)
TABLE_SIZE_MM = (2600.0, 2600.0)
_STATIC_T_WC = look_at([0.0, 0.0, 300.0], [0.0, 1000.0, 300.0])  # board tours: scanner level, looking along +Y
_MAX_SAMPLES_PER_TAKE = 100000


# --------------------------------------------------------------------------- IMU feed
class SimFeed:
    """IMU samples (and no events) in step with the frames: the interface of ``ohs.server.devices.ReplayFeed``.

    One pass of the script's IMU data is synthesised once (:func:`~ohs.sim.imu.synthesize_imu`)
    for a script starting at camera time 0 (host times ``[-time_offset, period_s - time_offset)``)
    and repeated per loop pass with a time shift, like ``ReplayFeed`` with ``loop``. A restart of the
    camera starts a new segment at the restart frame. Returned samples are strictly increasing in
    ``t_host`` (after a restart, samples older than the last one returned are skipped).
    """

    has_imu = True

    def __init__(self, imu: dict[str, np.ndarray], period_s: float, time_offset: float, start_cam_t: float,
                 loop: bool) -> None:
        order = np.argsort(imu["t_host"], kind="stable")
        self._t = np.asarray(imu["t_host"], dtype=np.float64)[order]
        self._dev = np.asarray(imu["t_device"], dtype=np.float64)[order]
        self._gyro = np.asarray(imu["gyro"], dtype=np.float64)[order]
        self._accel = np.asarray(imu["accel"], dtype=np.float64)[order]
        self.period_s = float(period_s)
        self.loop = bool(loop)
        self._to = float(time_offset)
        self._lock = threading.Lock()
        self._start = float(start_cam_t)  # camera time of script time 0 in the current segment
        self._pass = 0
        self._i = 0
        self._last = -math.inf
        self._restarts: deque[float] = deque()

    def take(self, until: float) -> tuple[list[ImuSample], list[Event]]:
        """Samples with ``t_host <= until`` not returned before (events: always ``[]``)."""
        samples: list[ImuSample] = []
        n = len(self._t)
        with self._lock:
            while n and len(samples) < _MAX_SAMPLES_PER_TAKE:
                if self._i >= n and self.loop:
                    self._i, self._pass = 0, self._pass + 1
                shift = self._start + self._pass * self.period_s
                t = float(self._t[self._i]) + shift if self._i < n else math.inf
                if self._restarts and t + self._to >= self._restarts[0]:
                    self._start, self._pass, self._i = self._restarts.popleft(), 0, 0
                    continue
                if t > until:
                    break
                if t > self._last:
                    i = self._i
                    samples.append(ImuSample(t, float(self._dev[i]) + shift, self._gyro[i].copy(), self._accel[i].copy()))
                    self._last = t
                self._i += 1
        return samples, []

    def _restart(self, start_cam_t: float) -> None:
        """The script restarts at camera time ``start_cam_t`` (called by the camera)."""
        with self._lock:
            self._restarts.append(float(start_cam_t))


# --------------------------------------------------------------------------- textures
def _smooth_noise(rng: np.random.Generator, shape: tuple[int, int], sigma: float) -> np.ndarray:
    """Zero-mean, unit-std Gaussian-filtered noise (float32)."""
    g = cv2.GaussianBlur(rng.standard_normal(shape, dtype=np.float32), (0, 0), float(sigma))
    g -= g.mean()
    return g / max(float(g.std()), 1e-6)


class _Page:
    """The printed page as a uint8 texture in DN, and where it lies in the board frame."""

    def __init__(self, spec: CharucoSpec, paper: str, px_per_mm: float, ink_dn: float, paper_dn: float) -> None:
        img, self.layout = render_charuco_page_image(spec, paper, float(px_per_mm) * 25.4)
        self.texture = np.where(img >= 128, np.uint8(round(paper_dn)), np.uint8(round(ink_dn)))
        self.paper_dn = float(paper_dn)
        bx, by = self.layout.board_origin_mm
        s = 1.0 / float(px_per_mm)
        self.A = np.array([[s, 0.0, 0.5 * s - bx], [0.0, s, 0.5 * s - by], [0.0, 0.0, 1.0]])  # texel -> board mm
        x0, x1, y0, y1 = self.layout.paper_bounds_mm
        self.corners_b = np.array([[x0, y0, 0.0], [x1, y0, 0.0], [x1, y1, 0.0], [x0, y1, 0.0]])


class _Backdrop:
    """A large low-contrast plane behind the page (a wall, or the table under the board)."""

    def __init__(self, rng: np.random.Generator, size_mm: Sequence[float], centre_mm: Sequence[float], scale: float,
                 px_per_mm: float = BACKDROP_PX_PER_MM) -> None:
        w, h = int(size_mm[0] * px_per_mm), int(size_mm[1] * px_per_mm)
        mean, noise, gradient = (v * scale for v in BACKDROP_DN)
        tex = 0.8 * _smooth_noise(rng, (h, w), 10.0) + 0.45 * _smooth_noise(rng, (h, w), 2.5)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        ramp = 1.2 * (xx / (w - 1) - 0.5) + 0.8 * (yy / (h - 1) - 0.5)  # within +-1
        self.texture = np.clip(np.rint(mean + noise * tex + gradient * ramp), 0, 255).astype(np.uint8)
        self.far_dn = float(round(mean))
        s = 1.0 / float(px_per_mm)
        x0, y0 = centre_mm[0] - 0.5 * size_mm[0], centre_mm[1] - 0.5 * size_mm[1]
        self.A = np.array([[s, 0.0, 0.5 * s + x0], [0.0, s, 0.5 * s + y0], [0.0, 0.0, 1.0]])  # texel -> plane mm


# --------------------------------------------------------------------------- one raw camera
def _plane_matrix(T_xp: np.ndarray) -> np.ndarray:
    """``[r1 r2 t]``: plane point ``(x, y, 1)`` of frame p's ``z = 0`` plane -> camera x coordinates."""
    return np.column_stack([T_xp[:3, 0], T_xp[:3, 1], T_xp[:3, 3]])


def _beyond_horizon(M: np.ndarray, x0: int, y0: int, w: int, h: int) -> np.ndarray | None:
    """Mask of canvas pixels in ``[x0, x0 + w) x [y0, y0 + h)`` whose ray meets the plane of
    ``M = K [r1 r2 t]`` behind the camera, or None if every pixel sees the plane in front."""
    g = np.linalg.inv(M)[2]  # third row: canvas pixel -> 1 / depth of the plane point
    xs = np.array([x0, x0 + w - 1], dtype=np.float64)
    ys = np.array([y0, y0 + h - 1], dtype=np.float64)
    if (g[0] * xs[:, None] + g[1] * ys[None, :] + g[2]).min() > 0:
        return None
    ww = g[0] * np.arange(x0, x0 + w)[None, :] + g[1] * np.arange(y0, y0 + h)[:, None] + g[2]
    return ww <= 0


class _View:
    """One raw camera of the virtual scanner: pinhole ``K``, distortion ``D`` and its pose in frame C.

    Images are composed on an undistorted *canvas* (the ideal pinhole image, big enough to cover
    every raw pixel) and remapped to the raw image through the lens distortion at the end.
    """

    def __init__(self, side: str, stereo: StereoCalibration, supersample: int) -> None:
        self.side = side
        K, D = (stereo.K1, stereo.D1) if side == "left" else (stereo.K2, stereo.D2)
        self.K = np.asarray(K, dtype=np.float64).reshape(3, 3)
        self.D = np.asarray(D, dtype=np.float64).ravel()
        self.ss = int(supersample)
        T_lc = make_se3(np.asarray(stereo.R1, dtype=np.float64).T)  # x_rawL = R1^T x_C
        self.T_xc = T_lc if side == "left" else make_se3(stereo.R, stereo.T) @ T_lc  # x_rawR = R x_rawL + T
        w, h = (int(v) for v in stereo.image_size)
        uu, vv = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
        pts = np.stack([uu.ravel(), vv.ravel()], axis=1).reshape(-1, 1, 2)
        crit = (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 50, 1e-10)
        ideal = cv2.undistortPoints(pts, self.K, self.D, R=None, P=self.K, criteria=crit).reshape(h, w, 2)
        x0, y0 = int(np.floor(ideal[..., 0].min())) - 2, int(np.floor(ideal[..., 1].min())) - 2
        x1, y1 = int(np.ceil(ideal[..., 0].max())) + 2, int(np.ceil(ideal[..., 1].max())) + 2
        self.canvas_size = (x1 - x0 + 1, y1 - y0 + 1)
        self.Kc = self.K.copy()
        self.Kc[0, 2] -= x0
        self.Kc[1, 2] -= y0
        self.maps = cv2.convertMaps((ideal[..., 0] - x0).astype(np.float32), (ideal[..., 1] - y0).astype(np.float32),
                                    cv2.CV_16SC2)

    def backdrop(self, bd: _Backdrop, T_xp: np.ndarray, border: int) -> np.ndarray:
        """Canvas (uint8) of the backdrop plane ``z = 0`` of frame p (``T_xp``: p -> this camera)."""
        M = self.Kc @ _plane_matrix(T_xp)
        cw, ch = self.canvas_size
        img = cv2.warpPerspective(bd.texture, M @ bd.A, (cw, ch), flags=cv2.INTER_LINEAR, borderMode=border,
                                  borderValue=bd.far_dn)
        sky = _beyond_horizon(M, 0, 0, cw, ch)
        if sky is not None:
            img[sky] = np.uint8(bd.far_dn)
        return img

    def page(self, page: _Page, T_xb: np.ndarray, bg: np.ndarray) -> np.ndarray:
        """Canvas (uint8) with the page at board pose ``T_xb`` composited over ``bg``.

        The page is warped at ``ss`` x resolution onto an ``ss`` x copy of the background (only over
        the page's bounding box) and area-averaged down, which anti-aliases the paper edge too.
        """
        out = bg.copy()
        P = page.corners_b @ T_xb[:3, :3].T + T_xb[:3, 3]
        if np.any(P[:, 2] <= 1.0):
            log.debug("page (partly) behind the %s camera; not drawn", self.side)
            return out
        quad = project_points(P, self.Kc)
        cw, ch = self.canvas_size
        x0, x1 = max(int(np.floor(quad[:, 0].min())) - 1, 0), min(int(np.ceil(quad[:, 0].max())) + 2, cw)
        y0, y1 = max(int(np.floor(quad[:, 1].min())) - 1, 0), min(int(np.ceil(quad[:, 1].max())) + 2, ch)
        if x1 <= x0 or y1 <= y0:
            return out
        ss = self.ss
        M = self.Kc @ _plane_matrix(T_xb)
        S = np.array([[ss, 0.0, 0.5 * ss - 0.5 - ss * x0], [0.0, ss, 0.5 * ss - 0.5 - ss * y0], [0.0, 0.0, 1.0]])
        big = cv2.resize(bg[y0:y1, x0:x1], ((x1 - x0) * ss, (y1 - y0) * ss), interpolation=cv2.INTER_NEAREST)
        cv2.warpPerspective(page.texture, S @ M @ page.A, (big.shape[1], big.shape[0]), dst=big,
                            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_TRANSPARENT)
        roi = cv2.resize(big, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)
        sky = _beyond_horizon(M, x0, y0, x1 - x0, y1 - y0)  # the mirrored plane beyond its horizon
        if sky is not None:
            roi[sky] = bg[y0:y1, x0:x1][sky]
        out[y0:y1, x0:x1] = roi
        return out

    def to_raw(self, canvas: np.ndarray) -> np.ndarray:
        """Raw (distorted) float32 image from a canvas."""
        raw = cv2.remap(canvas, self.maps[0], self.maps[1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        return raw.astype(np.float32)


# --------------------------------------------------------------------------- laser stripes
def _stripe_points(plane: LaserPlane, emitter: dict, T_cb: np.ndarray, layout: CharucoPageLayout,
                   spacing_mm: float = LASER_SAMPLE_MM) -> tuple[np.ndarray, np.ndarray]:
    """Points (N, 3) in frame C of a laser line on the paper and their relative stripe amplitude (N,).

    The laser plane is intersected with the page plane (board ``z = 0``), clipped to the paper and
    to the laser's fan; the amplitude falls with range (``sqrt(250 / r)``), incidence and the fan edge.
    """
    empty = np.zeros((0, 3)), np.zeros(0)
    R, t = T_cb[:3, :3], T_cb[:3, 3]
    n_b = R.T @ plane.normal  # the laser plane in the board frame: n_b . x + d_b = 0
    d_b = float(plane.normal @ t + plane.d)
    nn = float(n_b[0] ** 2 + n_b[1] ** 2)
    if nn < 1e-12:  # the sheet is parallel to the page
        return empty
    p0 = -d_b * n_b[:2] / nn
    u = np.array([-n_b[1], n_b[0]]) / math.sqrt(nn)
    lo, hi = -math.inf, math.inf
    x0, x1, y0, y1 = layout.paper_bounds_mm
    for k, (a, b) in enumerate(((x0, x1), (y0, y1))):  # clip the line p0 + s u to the paper
        if abs(u[k]) < 1e-12:
            if not a <= p0[k] <= b:
                return empty
            continue
        s1, s2 = (a - p0[k]) / u[k], (b - p0[k]) / u[k]
        lo, hi = max(lo, min(s1, s2)), min(hi, max(s1, s2))
    if hi <= lo:
        return empty
    s = np.linspace(lo, hi, max(int(math.ceil((hi - lo) / spacing_mm)) + 1, 2))
    xy = p0[None, :] + s[:, None] * u[None, :]
    P = np.column_stack([xy, np.zeros(len(s))]) @ R.T + t
    w = P - np.asarray(emitter["origin"], dtype=np.float64)
    r = np.linalg.norm(w, axis=1)
    along = w @ np.asarray(emitter["direction"], dtype=np.float64)
    phi = np.arctan2(w @ np.asarray(emitter["fan_axis"], dtype=np.float64), along)
    half, edge = math.radians(float(emitter.get("fan_deg", 60.0))) / 2.0, math.radians(2.0)
    sf = np.clip(0.5 + (half - np.abs(phi)) / edge, 0.0, 1.0)  # soft fan ends, slightly brighter centre
    fan = (1.0 - 0.25 * (phi / half) ** 2) * sf * sf * (3.0 - 2.0 * sf)
    cos_inc = np.abs(w @ R[:, 2]) / r
    amp = np.sqrt(250.0 / r * cos_inc) * fan * (along > 0)
    keep = amp > 1e-3
    return P[keep], amp[keep]


def _stripe_pixels(uv: np.ndarray, amp: np.ndarray, sigma: float, shape: tuple[int, int]
                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pixels ``(rows, cols, values)`` of a Gaussian stripe (peak ``amp`` per sample, width ``sigma``
    px) centred on the densely sampled polyline ``uv`` in an image of ``shape``: one profile per row
    for a mostly vertical line, per column otherwise, with the distance taken perpendicular to the
    line. Every pixel appears at most once."""
    empty = np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, np.float32)
    ok = np.all(np.isfinite(uv), axis=1)
    uv, amp = uv[ok], amp[ok]
    if len(uv) < 2:
        return empty
    span = uv[-1] - uv[0]
    vertical = abs(span[1]) >= abs(span[0])
    t, c = (uv[:, 1], uv[:, 0]) if vertical else (uv[:, 0], uv[:, 1])
    n_t, n_c = shape if vertical else shape[::-1]
    if t[-1] < t[0]:
        t, c, amp = t[::-1], c[::-1], amp[::-1]
    rising = np.concatenate([[True], t[1:] > np.maximum.accumulate(t)[:-1]])  # strictly increasing t
    t, c, amp = t[rising], c[rising], amp[rising]
    if len(t) < 2:
        return empty
    lines = np.arange(max(math.ceil(t[0]), 0), min(math.floor(t[-1]), n_t - 1) + 1)
    if len(lines) == 0:
        return empty
    cc = np.interp(lines, t, c)
    aa = np.interp(lines, t, amp)
    cos = 1.0 / np.sqrt(1.0 + np.interp(lines, t, np.gradient(c, t)) ** 2)
    half = int(math.ceil(4.0 * sigma / max(float(cos.min()), 0.5)))
    cols = np.rint(cc).astype(np.int64)[:, None] + np.arange(-half, half + 1)[None, :]
    val = aa[:, None] * np.exp(-0.5 * ((cols - cc[:, None]) * cos[:, None] / sigma) ** 2)
    inside = (cols >= 0) & (cols < n_c)
    ll = np.broadcast_to(lines[:, None], cols.shape)[inside]
    cols, val = cols[inside], val[inside].astype(np.float32)
    return (ll, cols, val) if vertical else (cols, ll, val)


class _Renderers:
    """What :meth:`SimBoardCamera.open` builds: both views and the backdrop (about 1 s)."""

    def __init__(self, stereo: StereoCalibration, page: _Page, *, imu_scene: bool, board_centre: Sequence[float],
                 exposure: float, seed: int) -> None:
        t_start = time.perf_counter()
        self.stereo = stereo
        self.page = page
        self.imu_scene = imu_scene
        self.views = (_View("left", stereo, SUPERSAMPLE), _View("right", stereo, SUPERSAMPLE))
        rng = np.random.default_rng([seed, 77])
        self.static_bg: dict[str, np.ndarray] = {}
        if imu_scene:  # the table under the board, in the board plane
            self.backdrop = _Backdrop(rng, TABLE_SIZE_MM, board_centre, exposure)
        else:  # a wall behind the board, fixed to the static scanner
            self.backdrop = _Backdrop(rng, WALL_SIZE_MM, (0.0, 0.0), exposure)
            T_c_wall = make_se3(so3_exp(WALL_POSE[0]), WALL_POSE[1])
            self.static_bg = {v.side: v.backdrop(self.backdrop, v.T_xc @ T_c_wall, cv2.BORDER_REFLECT) for v in self.views}
        log.debug("board camera renderers built in %.2f s", time.perf_counter() - t_start)

    def render(self, view: _View, T_cb: np.ndarray, stripes: list[tuple[np.ndarray, np.ndarray]], seed: int) -> np.ndarray:
        """Raw uint8 image of one camera: page over backdrop, distortion, stripes, blur, noise."""
        T_xb = view.T_xc @ T_cb
        bg = view.backdrop(self.backdrop, T_xb, cv2.BORDER_CONSTANT) if self.imu_scene else self.static_bg[view.side]
        img = view.to_raw(view.page(self.page, T_xb, bg))
        # stripes scale with the local albedo (img / paper), so they are dim on ink; both increments
        # are taken from the stripe-free image, so the light simply adds where the lines cross
        adds = []
        for P, amp in stripes:
            if len(P):
                r, c, v = _stripe_pixels(project_to_raw(P, self.stereo, view.side), LASER_DN * amp, LASER_SIGMA_PX,
                                         img.shape)
                adds.append((r, c, v * img[r, c] / np.float32(self.page.paper_dn)))
        for r, c, inc in adds:
            img[r, c] += inc
        img = cv2.GaussianBlur(img, (0, 0), BLUR_PX)
        noise = np.empty(img.shape, np.float32)
        cv2.setRNGSeed(seed)  # OpenCV's RNG is per thread: deterministic, and much faster than numpy's
        cv2.randn(noise, 0.0, NOISE_DN)
        img += noise
        return cv2.convertScaleAbs(img)  # round and saturate to uint8 (the image is never negative here)


# --------------------------------------------------------------------------- script helpers
def _true_calibration(image_size: tuple[int, int]) -> ScannerCalibration:
    """``nominal_calibration(image_size, realistic=True)``, with both laser planes and the IMU.

    The nominal calibration already holds both planes (``ohs.sim.nominal``); a missing one would be
    filled from :func:`~ohs.sim.nominal.nominal_emitters`, the sheets the simulator's renderer draws.
    """
    calib = nominal_calibration(image_size, realistic=True)
    em = nominal_emitters(baseline=calib.stereo.baseline)
    lasers = dict(calib.lasers)
    for name in LINE_NAMES:
        if name not in lasers:
            log.warning("nominal calibration without the %s laser plane; using the emitter's sheet", name)
            lasers[name] = LaserPlane(name, em[name]["normal"], em[name]["d"], rms=0.0)
    return ScannerCalibration(calib.stereo, lasers, calib.imu if calib.imu is not None else nominal_imu_calibration())


def _blend_poses(Ta: np.ndarray, Tb: np.ndarray, x: float, centre_b: np.ndarray) -> np.ndarray:
    """Board pose a fraction ``x`` of the way from ``Ta`` to ``Tb``: linear in the board centre,
    slerp in the rotation."""
    ca = Ta[:3, :3] @ centre_b + Ta[:3, 3]
    cb = Tb[:3, :3] @ centre_b + Tb[:3, 3]
    R = Ta[:3, :3] @ so3_exp(x * so3_log(Ta[:3, :3].T @ Tb[:3, :3]))
    return make_se3(R, ca + x * (cb - ca) - R @ centre_b)


def _min_jerk(x: float) -> float:
    x = min(max(float(x), 0.0), 1.0)
    return x * x * x * (10.0 - 15.0 * x + 6.0 * x * x)


def _imu_trajectory(spec: CharucoSpec, seed: int) -> tuple[RotatingTrajectory, np.ndarray]:
    """``(trajectory T_wc in script time, T_wb)`` of the imu script: the board face up on the table
    (Z-up sim world, centred at the origin) and the scanner above it, orbiting a pivot near it."""
    W, H = spec.size_mm
    T_wb = make_se3(np.diag([1.0, -1.0, -1.0]), [-W / 2.0, H / 2.0, 0.0])
    eye = np.asarray(IMU_EYE_MM, dtype=np.float64)
    T0 = look_at(eye, [0.0, 0.0, 0.0], up=(0.0, 1.0, 0.0))
    pivot = (0.0, 0.0, IMU_PIVOT * float(np.linalg.norm(eye)))
    kw = {"pivot": pivot, "rotate_s": IMU_ROTATE_S, "ease_s": IMU_RETURN_S}
    # the rotation vector is linear in amp_deg per axis: measure the peaks for 1 deg, then scale
    probe = RotatingTrajectory(T0, 0.0, IMU_STILL_S, (1.0, 1.0, 1.0), seed, **kw)
    s = np.arange(IMU_STILL_S, IMU_STILL_S + IMU_ROTATE_S + IMU_RETURN_S, 0.002)
    th = so3_log_batch(T0[:3, :3].T[None] @ probe.poses(s)[:, :3, :3])
    amp = np.asarray(IMU_PEAK_DEG) / np.degrees(np.abs(th).max(axis=0))
    return RotatingTrajectory(T0, 0.0, IMU_STILL_S, tuple(float(a) for a in amp), seed, **kw), T_wb


# --------------------------------------------------------------------------- the camera
class SimBoardCamera:
    """A live virtual scanner looking at the printed ChArUco page (camera interface like ReplayCamera / StereoCamera).

    See the module docstring for the scripts, the rendering and the timing. ``spec`` / ``paper``
    describe the printed board (default ``CharucoSpec()`` on letter paper; ``ValueError`` if it does
    not fit). ``t0`` is the virtual host time of frame 0's exposure. Thread-safe for one reader plus
    :meth:`restart`, :meth:`phase`, :meth:`true_pose` and :meth:`close` from other threads.
    """

    def __init__(self, script: str, *, image_size: tuple[int, int] = (1280, 800), fps: float = 10.0,
                 spec: CharucoSpec | None = None, paper: str = "letter", seed: int = 0, realtime: bool = True,
                 loop: bool = True, dwell_s: float | None = None, move_s: float | None = None,
                 t0: float = 100.0) -> None:
        if script not in SCRIPTS:
            raise ValueError(f"unknown script {script!r}; choose one of {SCRIPTS}")
        if not fps > 0:
            raise ValueError("fps must be positive")
        self.script = script
        self.image_size = (int(image_size[0]), int(image_size[1]))
        self.fps = float(fps)
        self.spec = spec if spec is not None else CharucoSpec()
        self.paper = paper.lower().replace(" ", "")
        self.seed = int(seed)
        self.realtime = bool(realtime)
        self.loop = bool(loop)
        self.t0 = float(t0)
        self.dwell_s = DWELL_S if dwell_s is None else float(dwell_s)
        self.move_s = MOVE_S if move_s is None else float(move_s)
        if self.dwell_s <= 0 or self.move_s < 0:
            raise ValueError("dwell_s must be positive and move_s not negative")
        self.true_calibration = _true_calibration(self.image_size)
        self._emitters = nominal_emitters(baseline=self.true_calibration.stereo.baseline)
        self._lines = script in ("lasers", "check")
        self._exposure = LASER_EXPOSURE if self._lines else 1.0
        ppm = TEXTURE_PX_PER_MM * self.image_size[0] / 1280.0
        self._page = _Page(self.spec, self.paper, ppm, INK_DN * self._exposure, PAPER_DN * self._exposure)
        self._centre_b = np.array([0.5 * self.spec.size_mm[0], 0.5 * self.spec.size_mm[1], 0.0])
        self._poses: list[np.ndarray] = []
        if script == "imu":
            self._traj, self._T_wb = _imu_trajectory(self.spec, self.seed)
            self.period_s = IMU_STILL_S + IMU_ROTATE_S + IMU_RETURN_S + IMU_END_STILL_S
        else:
            spec_d = asdict(self.spec)
            if script == "stereo":
                self._poses = stereo_calib_board_poses(TOUR_POSES[script], self.true_calibration, spec_d, self.seed)
            else:
                seed_p = self.seed + (CHECK_SEED_OFFSET if script == "check" else 0)
                self._poses = laser_calib_board_poses(TOUR_POSES[script], self.true_calibration, spec_d, seed_p)
            self.period_s = len(self._poses) * (self.dwell_s + self.move_s)
        self.frames_per_pass = int(math.ceil(self.period_s * self.fps - 1e-6))
        self._time_offset = float(self.true_calibration.imu.time_offset)
        self.feed = SimFeed(self._synthesize_imu(), self.period_s, self._time_offset, self.t0 + self._time_offset,
                            self.loop)
        self._lock = threading.Lock()
        self._k = 0  # next frame index
        self._k_start = 0  # frame index at script time 0
        self._restart_pending = False
        self._pace: tuple[float, int] | None = None  # (wall time, frame index) the realtime pacing starts from
        self._latest: tuple[np.ndarray, str] | None = None
        self._renderers: _Renderers | None = None
        self._open = False
        log.debug("SimBoardCamera %s: %d poses, period %.1f s, %d frames per pass", script, len(self._poses),
                  self.period_s, self.frames_per_pass)

    # ------------------------------------------------------------------ camera interface
    def open(self) -> None:
        """Build the renderers (about 1 s). Idempotent."""
        if self._open:
            return
        self._ensure_renderers()
        self._open = True
        log.info("virtual board camera open: %s script, %dx%d, %.1f fps, %.1f s per pass", self.script,
                 *self.image_size, self.fps, self.period_s)

    def close(self) -> None:
        """Release the renderers. Idempotent; :meth:`open` again continues the virtual clock."""
        self._open = False
        self._pace = None
        self._renderers = None

    def __enter__(self) -> "SimBoardCamera":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def info(self) -> dict:
        w, h = self.image_size
        return {"backend": "sim", "script": self.script, "width": 2 * w, "height": h, "fps": self.fps, "fourcc": "",
                "synced": True, "open": self._open, "loop": self.loop, "realtime": self.realtime,
                "period_s": self.period_s}

    def set_exposure(self, value: float) -> None:
        log.debug("SimBoardCamera ignores set_exposure(%s)", value)

    def set_gain(self, value: float) -> None:
        log.debug("SimBoardCamera ignores set_gain(%s)", value)

    def read(self, timeout: float = 1.0) -> StereoFrame:
        """The next frame (index 0, 1, 2, ...; never the same frame twice).

        Raw, distorted, unrectified uint8 grey images. With ``realtime`` it is paced to ``fps``
        (``TimeoutError`` if it is not due within ``timeout``), else returned at once. After the last
        frame of a non-looping script: ``EOFError`` (until :meth:`restart`).
        """
        if not self._open:
            raise RuntimeError("SimBoardCamera is not open")
        k = self._k
        due = None
        if self.realtime:
            now = time.perf_counter()
            if self._pace is None:
                self._pace = (now, k)
            due = self._pace[0] + (k - self._pace[1]) / self.fps
            if due - now > timeout:
                time.sleep(max(0.0, float(timeout)))
                raise TimeoutError(f"no virtual frame due within {timeout:.3f} s")
            if now - due > RESYNC_S:
                self._pace, due = (now, k), now
        with self._lock:
            if self._restart_pending:
                self._restart_pending = False
                self._k_start = k
                self.feed._restart(self._t_exp(k) + self._time_offset)
            n = k - self._k_start
            if not self.loop and n >= self.frames_per_pass:
                raise EOFError(f"end of the {self.script} script")
            s = (n / self.fps) % self.period_s if self.loop else n / self.fps
            self._k = k + 1
        T_cb, phase = self._script_pose(s)
        frame = self._render(k, T_cb, self._t_exp(k) + self._time_offset)
        with self._lock:
            self._latest = (T_cb, phase)
        if due is not None:
            wait = due - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
        return frame

    def restart(self) -> None:
        """Start the script again at script time 0 with the next frame (the virtual clock keeps running)."""
        with self._lock:
            self._restart_pending = True

    def phase(self) -> str:
        """Phase at the latest frame: ``"dwell 3/16"`` / ``"move"`` (tours), ``"still"`` / ``"rotate"`` /
        ``"return"`` (imu). Before the first frame: the phase at script time 0."""
        with self._lock:
            latest = self._latest
        return self._script_pose(0.0)[1] if latest is None else latest[1]

    def true_pose(self) -> np.ndarray:
        """True board pose ``T_cb`` (frame C) of the latest frame (script time 0 before the first)."""
        with self._lock:
            latest = self._latest
        return self._script_pose(0.0)[0] if latest is None else latest[0].copy()

    # ------------------------------------------------------------------ deterministic access
    def frame_at(self, t: float, index: int = 0) -> StereoFrame:
        """Deterministic render at script time ``t`` (wrapped into one pass); no pacing.

        Noise and jitter come from ``(seed, index)`` and ``t_host = t0 + time_offset + t`` plus
        jitter, so ``frame_at(k / fps, k)`` equals frame ``k`` of a fresh camera's first pass.
        """
        self._ensure_renderers()
        T_cb, _ = self._script_pose(float(t) % self.period_s)
        return self._render(int(index), T_cb, self.t0 + self._time_offset + float(t))

    def pose_at(self, t: float) -> np.ndarray:
        """True board pose ``T_cb`` (frame C) at script time ``t``."""
        return self._script_pose(float(t) % self.period_s)[0]

    def phase_at(self, t: float) -> str:
        """Phase (as :meth:`phase`) at script time ``t``."""
        return self._script_pose(float(t) % self.period_s)[1]

    def dwell_times(self) -> list[float]:
        """Mid-dwell script times, one per board pose (stereo / lasers / check; ``[]`` for imu)."""
        seg = self.dwell_s + self.move_s
        return [i * seg + 0.5 * self.dwell_s for i in range(len(self._poses))]

    def laser_lines_at(self, t: float) -> dict[str, np.ndarray]:
        """Centre points (N, 3) in frame C of the laser stripes painted at script time ``t``
        (``{}`` for the stereo and imu scripts)."""
        if not self._lines:
            return {}
        T_cb = self.pose_at(t)
        return {name: self._stripe(name, T_cb)[0] for name in LINE_NAMES}

    # ------------------------------------------------------------------ internals
    def _t_exp(self, k: int) -> float:
        return self.t0 + k / self.fps

    def _script_pose(self, s: float) -> tuple[np.ndarray, str]:
        """``(T_cb, phase)`` at script time ``s`` in ``[0, period_s)``."""
        if self.script == "imu":
            T_cb = invert_se3(self._traj.pose(s)) @ self._T_wb
            if s < IMU_STILL_S:
                return T_cb, "still"
            if s < IMU_STILL_S + IMU_ROTATE_S:
                return T_cb, "rotate"
            return T_cb, "return" if s < IMU_STILL_S + IMU_ROTATE_S + IMU_RETURN_S else "still"
        n = len(self._poses)
        seg = self.dwell_s + self.move_s
        i = min(int(s // seg), n - 1)
        tau = s - i * seg
        if tau < self.dwell_s or self.move_s <= 0:
            return self._poses[i].copy(), f"dwell {i + 1}/{n}"
        x = _min_jerk((tau - self.dwell_s) / self.move_s)
        return _blend_poses(self._poses[i], self._poses[(i + 1) % n], x, self._centre_b), "move"

    def _stripe(self, name: str, T_cb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return _stripe_points(self.true_calibration.lasers[name], self._emitters[name], T_cb, self._page.layout)

    def _synthesize_imu(self) -> dict[str, np.ndarray]:
        """One pass of IMU data for a script starting at camera time 0 (host times
        ``[-time_offset, period_s - time_offset)``)."""
        imu = self.true_calibration.imu
        to = float(imu.time_offset)
        traj = self._traj if self.script == "imu" else StaticTrajectory(_STATIC_T_WC)
        params = ImuSimParams(gyro_bias=np.asarray(imu.gyro_bias, dtype=np.float64).copy())
        a = synthesize_imu_arrays(traj, -to - 0.05, self.period_s - to + 0.05, imu.R_ci, to, params, seed=self.seed)
        keep = (a["t_host"] >= -to) & (a["t_host"] < self.period_s - to)
        return {k: v[keep] for k, v in a.items()}

    def _ensure_renderers(self) -> None:
        if self._renderers is None:
            self._renderers = _Renderers(self.true_calibration.stereo, self._page, imu_scene=self.script == "imu",
                                         board_centre=self._centre_b[:2], exposure=self._exposure, seed=self.seed)

    def _render(self, index: int, T_cb: np.ndarray, t_cam: float) -> StereoFrame:
        r = self._renderers  # one reference: close() from another thread cannot pull it away mid-frame
        if r is None:
            raise RuntimeError("SimBoardCamera is closed")
        rng = np.random.default_rng([self.seed, int(index)])
        jitter = float(np.clip(rng.normal(0.0, JITTER_S), -3 * JITTER_S, 3 * JITTER_S))
        seeds = rng.integers(1, 2**31 - 1, size=2)
        stripes = [self._stripe(name, T_cb) for name in LINE_NAMES] if self._lines else []
        left, right = (r.render(view, T_cb, stripes, int(sd)) for view, sd in zip(r.views, seeds))
        return StereoFrame(int(index), float(t_cam + jitter), left, right)
