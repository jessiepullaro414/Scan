"""IMU-camera calibration: gyro bias, time offset and the rotation ``R_ci``.

Inputs are camera orientations at camera timestamps, e.g. from ChArUco board poses with the board as
world (``R_wc = R_cb^T``), and raw gyro samples (rad/s, IMU frame) at IMU host timestamps.
Frames without a board pose are simply left out (gaps are handled).

Conventions (ARCHITECTURE.md section 2): ``v_c = R_ci @ v_i``; camera timestamp = IMU host
timestamp + ``time_offset``; the gyro bias (IMU frame) is subtracted from raw readings.

Pipeline of :func:`calibrate_imu_camera`:

0. **Pose outliers.** Camera orientations that deviate from the geodesic interpolation of their
   neighbours by more than max(1.5 deg, 6 robust sigma) (e.g. flipped board poses) are dropped.
1. **Gyro bias.** Look for a static segment (>= 0.5 s where every gyro axis has a std below
   0.01 rad/s and ``|mean| < 0.1 rad/s``). If one exists (and the camera, where it overlaps,
   confirms the device was still), the bias is its mean gyro and stays fixed. Otherwise the bias
   is estimated jointly: initialised from the translation term of the Kabsch fit (step 3),
   ``w_c = R w_raw + t  =>  bias = -R^T t``, and refined in step 4.
2. **Time offset.** Normalised cross-correlation of the angular-speed magnitudes (rotation
   invariant, so ``R_ci`` is not needed): camera speed from finite differences of consecutive
   orientations, gyro speed box-filtered over one frame interval, both resampled at 200 Hz;
   parabolic sub-sample peak within +-``max_offset_s``.
3. **R_ci.** Kabsch on per-frame-interval angular-velocity vectors (camera vs gyro integrated over
   the same, offset-corrected interval) after a magnitude-consistency outlier filter.
4. **Joint refinement** (``scipy.optimize.least_squares``, soft-L1 loss) of ``R_ci``, the time offset
   and (if not from a static segment) the bias, minimising the rotation residuals
   ``log(R_cam_ab^T R_ci R_imu_ab R_ci^T)`` over short (~0.1 s) intervals, with the gyro integrated
   exactly (trapezoidal rate, vectorised) over the time-shifted interval.

The report states whether the motion excited all axes, which ``R_ci`` needs: rotate the scanner
in front of the board about all three axes (+-20-40 deg, 0.5-2 Hz) for 20-60 s, ideally after
holding it still for 1-2 s.
"""
from __future__ import annotations

import logging
import math

import numpy as np
from scipy.optimize import least_squares

from ..geometry import kabsch, rotation_angle_deg, so3_exp, so3_log
from ..types import ImuCalibration

__all__ = ["calibrate_imu_camera", "find_static_segment"]

log = logging.getLogger(__name__)

RESAMPLE_HZ = 200.0
REFINE_INTERVAL_S = 0.1
STATIC_WINDOW_S = 0.5
STATIC_STD = 0.01  # rad/s, per axis
STATIC_MAX_BIAS = 0.1  # rad/s
STATIC_CAM_RATE = 0.01  # rad/s: the camera must agree the device was still
MIN_EXCITATION = 0.2  # rad/s rms angular velocity about the 2nd principal axis


# --------------------------------------------------------------------------- batched SO(3)
def _skew_batch(w: np.ndarray) -> np.ndarray:
    S = np.zeros(w.shape[:-1] + (3, 3))
    S[..., 0, 1], S[..., 0, 2] = -w[..., 2], w[..., 1]
    S[..., 1, 0], S[..., 1, 2] = w[..., 2], -w[..., 0]
    S[..., 2, 0], S[..., 2, 1] = -w[..., 1], w[..., 0]
    return S


def _exp_batch(w: np.ndarray) -> np.ndarray:
    """Rodrigues for (N, 3) rotation vectors -> (N, 3, 3)."""
    th = np.linalg.norm(w, axis=-1)
    small = th < 1e-6
    ths = np.where(small, 1.0, th)
    A = np.where(small, 1.0 - th**2 / 6.0, np.sin(ths) / ths)
    B = np.where(small, 0.5 - th**2 / 24.0, (1.0 - np.cos(ths)) / ths**2)
    S = _skew_batch(w)
    return np.eye(3) + A[..., None, None] * S + B[..., None, None] * (S @ S)


def _log_batch(R: np.ndarray) -> np.ndarray:
    """Rotation vectors (N, 3) of (N, 3, 3) rotations (exact also near 180 deg)."""
    cos = np.clip((np.trace(R, axis1=-2, axis2=-1) - 1.0) / 2.0, -1.0, 1.0)
    th = np.arccos(cos)
    v = np.stack([R[..., 2, 1] - R[..., 1, 2], R[..., 0, 2] - R[..., 2, 0], R[..., 1, 0] - R[..., 0, 1]], axis=-1)
    small = th < 1e-6
    sin = np.sin(np.where(small, 1.0, th))
    scale = np.where(small, 0.5 + th**2 / 12.0, th / (2.0 * sin))
    out = scale[..., None] * v
    near_pi = th > math.pi - 1e-3
    for i in np.flatnonzero(near_pi):  # rare: fall back to the careful scalar version
        out[i] = so3_log(R[i])
    return out


def _cumprod_rot(dR: np.ndarray) -> np.ndarray:
    """Inclusive prefix products ``C[k] = dR[0] @ ... @ dR[k]`` (log-depth Hillis-Steele scan)."""
    C = dR.copy()
    s = 1
    while s < len(C):
        C[s:] = C[:-s] @ C[s:]  # the right side is evaluated before assignment (old values)
        s *= 2
    return C


def _orthonormalise(R: np.ndarray) -> np.ndarray:
    U, _, Vt = np.linalg.svd(R)
    D = np.ones(R.shape[:-1])
    D[..., -1] = np.sign(np.linalg.det(U @ Vt))
    return (U * D[..., None, :]) @ Vt


class _GyroIntegrator:
    """Body orientation ``Q(t)`` (relative to the first sample) from gyro samples, for a given bias.

    The rate is linearly interpolated between samples and integrated with the trapezoid rule, as
    in :func:`ohs.geometry.integrate_gyro`, but vectorised over many query times.
    """

    def __init__(self, t: np.ndarray, gyro: np.ndarray) -> None:
        self.t, self.gyro = t, gyro
        self._bias: np.ndarray | None = None
        self._Q = self._w = None

    def _prepare(self, bias: np.ndarray) -> None:
        if self._bias is not None and np.array_equal(bias, self._bias):
            return
        w = self.gyro - bias
        dt = np.diff(self.t)
        C = _cumprod_rot(_exp_batch(0.5 * (w[:-1] + w[1:]) * dt[:, None]))
        self._Q = np.concatenate([np.eye(3)[None], C])
        self._w, self._bias = w, np.array(bias, dtype=np.float64)

    def orientation(self, tq: np.ndarray, bias: np.ndarray) -> np.ndarray:
        self._prepare(bias)
        t, w = self.t, self._w
        j = np.clip(np.searchsorted(t, tq, side="right") - 1, 0, len(t) - 2)
        tau = tq - t[j]
        wq = w[j] + (w[j + 1] - w[j]) * (tau / (t[j + 1] - t[j]))[:, None]
        return self._Q[j] @ _exp_batch(0.5 * (w[j] + wq) * tau[:, None])

    def relative(self, ta: np.ndarray, tb: np.ndarray, bias: np.ndarray) -> np.ndarray:
        """``R_ab`` with ``v_a = R_ab @ v_b`` (body at ``tb`` relative to body at ``ta``)."""
        Qa, Qb = self.orientation(ta, bias), self.orientation(tb, bias)
        return np.swapaxes(Qa, -1, -2) @ Qb


# --------------------------------------------------------------------------- steps
def find_static_segment(
    imu_t: np.ndarray, gyro: np.ndarray, min_duration_s: float = STATIC_WINDOW_S,
    std_thresh: float = STATIC_STD, max_bias: float = STATIC_MAX_BIAS,
) -> tuple[int, int] | None:
    """Longest run of samples ``[i0, i1)`` covered by still windows of ``min_duration_s``, or None.

    A window is still if every gyro axis has a std below ``std_thresh`` and ``|mean| < max_bias``.
    """
    n_s = len(imu_t)
    rate = 1.0 / float(np.median(np.diff(imu_t)))
    n = max(int(round(min_duration_s * rate)), 10)
    if n_s < n:
        return None
    c1 = np.concatenate([np.zeros((1, 3)), np.cumsum(gyro, axis=0)])
    c2 = np.concatenate([np.zeros((1, 3)), np.cumsum(gyro**2, axis=0)])
    mean = (c1[n:] - c1[:-n]) / n
    var = np.maximum((c2[n:] - c2[:-n]) / n - mean**2, 0.0)
    still = np.all(np.sqrt(var) < std_thresh, axis=1) & (np.linalg.norm(mean, axis=1) < max_bias)
    if not still.any():
        return None
    mark = np.zeros(n_s + 1)
    starts = np.flatnonzero(still)
    np.add.at(mark, starts, 1.0)
    np.add.at(mark, starts + n, -1.0)
    covered = np.cumsum(mark)[:n_s] > 0
    gaps = np.concatenate([[False], np.diff(imu_t) > 5.0 / rate])  # a data gap ends a run
    best, best_len, i = None, 0, 0
    while i < n_s:
        if covered[i]:
            j = i + 1
            while j < n_s and covered[j] and not gaps[j]:
                j += 1
            if j - i > best_len:
                best, best_len = (i, j), j - i
            i = j
        else:
            i += 1
    if best is None or imu_t[best[1] - 1] - imu_t[best[0]] < min_duration_s * 0.95:
        return None
    return best


def _orientation_outliers(cam_t: np.ndarray, R_wc: np.ndarray, frame_dt: float) -> np.ndarray:
    """Mask of camera orientations that disagree with their neighbours (e.g. flipped board poses).

    Each pose is compared with the geodesic interpolation of its two neighbours; poses deviating by
    more than max(1.5 deg, 6 robust sigma) that are a local maximum of the deviation are dropped,
    then the test is repeated on the remaining poses.
    """
    bad = np.zeros(len(cam_t), dtype=bool)
    for _ in range(4):
        idx = np.flatnonzero(~bad)
        if len(idx) < 5:
            break
        p, c, n = idx[:-2], idx[1:-1], idx[2:]
        close = (cam_t[n] - cam_t[p]) < 3.5 * frame_dt
        s = (cam_t[c] - cam_t[p]) / (cam_t[n] - cam_t[p])
        R_int = R_wc[p] @ _exp_batch(_log_batch(np.swapaxes(R_wc[p], -1, -2) @ R_wc[n]) * s[:, None])
        dev = np.linalg.norm(_log_batch(np.swapaxes(R_int, -1, -2) @ R_wc[c]), axis=1)
        dev[~close] = 0.0
        if close.sum() < 5:
            break
        thr = max(math.radians(1.5), 6.0 * 1.4826 * float(np.median(dev[close])))
        left = np.concatenate([[0.0], dev[:-1]])
        right = np.concatenate([dev[1:], [0.0]])
        peak = (dev > thr) & (dev >= left) & (dev >= right)
        if not peak.any():
            break
        bad[c[peak]] = True
    return bad


def _camera_rates(cam_t: np.ndarray, R_wc: np.ndarray, frame_dt: float) -> tuple[np.ndarray, np.ndarray]:
    """Body angular velocity (N-1, 3) over each frame interval and a validity mask (no gaps)."""
    dt = np.diff(cam_t)
    rel = np.swapaxes(R_wc[:-1], -1, -2) @ R_wc[1:]
    w = _log_batch(rel) / dt[:, None]
    return w, dt < 1.5 * frame_dt


def _xcorr_offset(
    t_mid: np.ndarray, s_cam: np.ndarray, imu_t: np.ndarray, s_imu: np.ndarray, frame_dt: float, max_offset_s: float
) -> tuple[float, float, bool]:
    """Offset maximising the NCC of camera and gyro angular speed. Returns (offset, peak, at_edge)."""
    fs = RESAMPLE_HZ
    m = int(math.floor((t_mid[-1] - t_mid[0]) * fs)) + 1
    tg = t_mid[0] + np.arange(m) / fs
    cam = np.interp(tg, t_mid, s_cam)
    # grid points inside camera gaps carry no information
    k = np.clip(np.searchsorted(t_mid, tg), 1, len(t_mid) - 1)
    valid_cam = (t_mid[k] - t_mid[k - 1]) < 1.6 * frame_dt
    # gyro speed averaged over one frame interval (matches the camera finite difference)
    S = np.concatenate([[0.0], np.cumsum(0.5 * (s_imu[1:] + s_imu[:-1]) * np.diff(imu_t))])
    n_lag = int(round(max_offset_s * fs))
    tq = t_mid[0] + (np.arange(m + 2 * n_lag) - n_lag) / fs  # tq[i] = tg[i - n_lag]
    lo, hi = tq - 0.5 * frame_dt, tq + 0.5 * frame_dt
    box = (np.interp(hi, imu_t, S) - np.interp(lo, imu_t, S)) / frame_dt
    inside = (lo >= imu_t[0]) & (hi <= imu_t[-1])
    lags = np.arange(-n_lag, n_lag + 1)
    ncc = np.full(len(lags), -np.inf)
    for i, L in enumerate(lags):
        # camera time tg <-> IMU host time tg - L/fs, i.e. index (n_lag - L) in tq
        sl = slice(n_lag - L, n_lag - L + m)
        y, ok = box[sl], valid_cam & inside[sl]
        if ok.sum() < 50:
            continue
        a, b = cam[ok] - cam[ok].mean(), y[ok] - y[ok].mean()
        den = math.sqrt(float(a @ a) * float(b @ b))
        ncc[i] = float(a @ b) / den if den > 0 else -np.inf
    i = int(np.argmax(ncc))
    if not np.isfinite(ncc[i]):
        raise ValueError("camera and IMU data do not overlap in time")
    frac = 0.0
    if 0 < i < len(lags) - 1 and np.isfinite(ncc[i - 1]) and np.isfinite(ncc[i + 1]):
        den = ncc[i - 1] - 2 * ncc[i] + ncc[i + 1]
        if den < 0:
            frac = float(np.clip(0.5 * (ncc[i - 1] - ncc[i + 1]) / den, -0.5, 0.5))
    at_edge = i in (0, len(lags) - 1)
    return (lags[i] + frac) / fs, float(ncc[i]), at_edge


def _procrustes(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """Rotation minimising sum ||R a - b||^2 without a translation (Kabsch on +-A / +-B)."""
    R, _ = kabsch(np.vstack([A, -A]), np.vstack([B, -B]))
    return R


# --------------------------------------------------------------------------- main entry
def calibrate_imu_camera(
    cam_t: np.ndarray,
    cam_R_wc: np.ndarray,
    imu_t: np.ndarray,
    gyro: np.ndarray,
    max_offset_s: float = 0.2,
) -> tuple[ImuCalibration, dict]:
    """Estimate ``ImuCalibration(R_ci, time_offset, gyro_bias)`` from camera orientations and gyro.

    ``cam_t`` (N,) camera timestamps, ``cam_R_wc`` (N, 3, 3) camera orientations in any fixed world
    frame (e.g. the board), ``imu_t`` (M,) IMU host timestamps, ``gyro`` (M, 3) raw gyro (rad/s).
    Returns ``(calibration, report)``; raises ``ValueError`` for unusable input.
    """
    cam_t = np.asarray(cam_t, dtype=np.float64).reshape(-1)
    R_wc = np.asarray(cam_R_wc, dtype=np.float64).reshape(-1, 3, 3)
    imu_t = np.asarray(imu_t, dtype=np.float64).reshape(-1)
    gyro = np.asarray(gyro, dtype=np.float64).reshape(-1, 3)
    if len(cam_t) != len(R_wc) or len(imu_t) != len(gyro):
        raise ValueError("timestamps and samples must have the same length")
    order = np.argsort(cam_t, kind="stable")
    cam_t, R_wc = cam_t[order], _orthonormalise(R_wc[order])
    keep = np.concatenate([[True], np.diff(cam_t) > 0])
    cam_t, R_wc = cam_t[keep], R_wc[keep]
    order = np.argsort(imu_t, kind="stable")
    imu_t, gyro = imu_t[order], gyro[order]
    keep = np.concatenate([[True], np.diff(imu_t) > 0])
    imu_t, gyro = imu_t[keep], gyro[keep]
    if len(cam_t) < 30 or len(imu_t) < 200:
        raise ValueError(f"too little data: {len(cam_t)} camera poses, {len(imu_t)} gyro samples")
    frame_dt = float(np.median(np.diff(cam_t)))
    bad_pose = _orientation_outliers(cam_t, R_wc, frame_dt)
    if bad_pose.any():
        log.info("dropped %d camera orientation outlier(s) (inconsistent with their neighbours)", int(bad_pose.sum()))
        cam_t, R_wc = cam_t[~bad_pose], R_wc[~bad_pose]

    # 1. static segment -> bias
    seg = find_static_segment(imu_t, gyro)
    bias0, static_info = np.zeros(3), None
    if seg is not None:
        i0, i1 = seg
        trim = (i1 - i0) // 10
        bias0 = gyro[i0 + trim : i1 - trim].mean(axis=0)
        static_info = [float(imu_t[i0]), float(imu_t[i1 - 1])]

    # 2. time offset from angular-speed cross-correlation
    w_cam, ok_int = _camera_rates(cam_t, R_wc, frame_dt)
    t_mid = 0.5 * (cam_t[:-1] + cam_t[1:])
    s_cam = np.clip(np.linalg.norm(w_cam, axis=1), 0.0, 20.0)
    s_imu = np.clip(np.linalg.norm(gyro - bias0, axis=1), 0.0, 20.0)
    offset0, peak, at_edge = _xcorr_offset(t_mid[ok_int], s_cam[ok_int], imu_t, s_imu, frame_dt, max_offset_s)
    log.info("time offset from cross-correlation: %.4f s (NCC peak %.3f)", offset0, peak)

    # 1b. the camera must agree that the static segment was still (where it overlaps)
    if static_info is not None:
        sel = np.flatnonzero((cam_t >= static_info[0] + offset0) & (cam_t <= static_info[1] + offset0))
        if len(sel) >= 5 and cam_t[sel[-1]] - cam_t[sel[0]] > 0.3:
            ang = np.linalg.norm(so3_log(R_wc[sel[0]].T @ R_wc[sel[-1]]))
            rate = ang / (cam_t[sel[-1]] - cam_t[sel[0]])
            if rate > STATIC_CAM_RATE:
                log.info("gyro-static segment rejected: the camera rotated at %.3f rad/s", rate)
                static_info, bias0 = None, np.zeros(3)
    joint_bias = static_info is None

    # 3. R_ci by Kabsch on interval angular velocities
    integ = _GyroIntegrator(imu_t, gyro)
    ta, tb = cam_t[:-1], cam_t[1:]
    use = ok_int & (ta - offset0 >= imu_t[0]) & (tb - offset0 <= imu_t[-1])
    if use.sum() < 20:
        raise ValueError("fewer than 20 camera intervals are covered by IMU data")
    dt = (tb - ta)[use]
    w_i = _log_batch(integ.relative(ta[use] - offset0, tb[use] - offset0, bias0)) / dt[:, None]
    w_c = w_cam[use]
    mag_ok = np.abs(np.linalg.norm(w_c, axis=1) - np.linalg.norm(w_i, axis=1)) < 0.15 + 0.15 * np.linalg.norm(w_i, axis=1)
    if mag_ok.sum() < 20:
        raise ValueError("camera and gyro angular speeds disagree; check timestamps and board poses")
    A, Bv = w_i[mag_ok], w_c[mag_ok]
    for _ in range(2):  # fit, drop vector outliers, refit
        if joint_bias:
            R0, tvec = kabsch(A, Bv)
        else:
            R0, tvec = _procrustes(A, Bv), np.zeros(3)
        r = np.linalg.norm(Bv - A @ R0.T - tvec, axis=1)
        inl = r < max(3.0 * 1.4826 * float(np.median(r)), 0.02)
        if inl.all() or inl.sum() < 20:
            break
        A, Bv = A[inl], Bv[inl]
    if joint_bias:
        bias0 = -R0.T @ tvec
    R_kabsch = R0.copy()
    excitation = np.linalg.svd(w_c, compute_uv=False) / math.sqrt(len(w_c))
    log.info("Kabsch R_ci: %.2f deg from identity, initial bias %s", rotation_angle_deg(R0), np.round(bias0, 4))

    # 4. joint refinement over short intervals
    m = max(1, int(round(REFINE_INTERVAL_S / frame_dt)))
    a_idx = np.arange(len(cam_t) - m)
    b_idx = a_idx + m
    lo = max(-max_offset_s, offset0 - 0.025)
    hi = min(max_offset_s, offset0 + 0.025)
    no_gap = (cam_t[b_idx] - cam_t[a_idx]) < 1.5 * m * frame_dt
    sel = no_gap & (cam_t[a_idx] - hi >= imu_t[0]) & (cam_t[b_idx] - lo <= imu_t[-1])
    a_idx, b_idx = a_idx[sel], b_idx[sel]
    if len(a_idx) < 20:
        raise ValueError("fewer than 20 refinement intervals are covered by IMU data")
    R_cam = np.swapaxes(R_wc[a_idx], -1, -2) @ R_wc[b_idx]
    t_a, t_b = cam_t[a_idx], cam_t[b_idx]
    lo, hi = min(lo, offset0 - 1e-6), max(hi, offset0 + 1e-6)

    def unpack(x: np.ndarray) -> tuple[np.ndarray, float, np.ndarray]:
        return so3_exp(x[:3]) @ R0, float(x[3]), (x[4:7] if joint_bias else bias0)

    def residuals(x: np.ndarray) -> np.ndarray:
        R_ci, delta, b = unpack(x)
        R_pred = R_ci @ integ.relative(t_a - delta, t_b - delta, b) @ R_ci.T
        return _log_batch(np.swapaxes(R_cam, -1, -2) @ R_pred).ravel()

    x0 = np.concatenate([np.zeros(3), [offset0], bias0 if joint_bias else []])
    r0 = residuals(x0)
    f_scale = max(3.0 * 1.4826 * float(np.median(np.abs(r0))), 1e-4)
    lb = np.full(len(x0), -np.inf)
    ub = np.full(len(x0), np.inf)
    lb[3], ub[3] = lo, hi
    sol = least_squares(residuals, x0, bounds=(lb, ub), loss="soft_l1", f_scale=f_scale, x_scale="jac",
                        diff_step=1e-6, max_nfev=200)
    R_ci, offset, bias = unpack(sol.x)
    res = _log_batch(np.swapaxes(R_cam, -1, -2) @ (R_ci @ integ.relative(t_a - offset, t_b - offset, bias) @ R_ci.T))
    res_deg = np.degrees(np.linalg.norm(res, axis=1))
    inlier = res_deg <= max(5.0 * 1.4826 * float(np.median(res_deg)), 0.05)
    rms_deg = float(np.sqrt(np.mean(res_deg[inlier] ** 2)))
    outlier_frac = float(1.0 - inlier.mean())
    log.info("refined: offset %.4f s, R_ci %.2f deg from identity, bias %s, inlier residual rms %.3f deg, "
             "%.1f %% outlier intervals (%s)", offset, rotation_angle_deg(R_ci), np.round(bias, 4), rms_deg,
             100 * outlier_frac, sol.message)

    # verdict
    advice = []
    if at_edge or abs(offset) > max_offset_s - 1e-3:
        advice.append(f"the time offset hit the search limit of +-{max_offset_s} s: check the clock sync")
    if peak < 0.6:
        advice.append(f"weak camera/gyro correlation ({peak:.2f}): rotate the scanner more vigorously in front of the board")
    if excitation[1] < MIN_EXCITATION:
        advice.append("the rotation was mostly about one axis, so R_ci is poorly determined: rotate about all three axes")
    if rms_deg > 0.3:
        advice.append(f"large rotation residuals ({rms_deg:.2f} deg rms): check board detection and timestamps")
    if outlier_frac > 0.1:
        advice.append(
            f"{outlier_frac:.0%} of the intervals are outliers: check board detection (blur, glare) and timestamps"
        )
    if not sol.success:
        advice.append(f"the refinement did not converge ({sol.message})")
    ok = not advice
    calib = ImuCalibration(R_ci=R_ci, time_offset=float(offset), gyro_bias=np.asarray(bias, dtype=np.float64))
    report = {
        "time_offset_s": float(offset),
        "time_offset_xcorr_s": float(offset0),
        "xcorr_peak": peak,
        "R_ci": R_ci.tolist(),
        "R_ci_angle_deg": rotation_angle_deg(R_ci),
        "kabsch_to_refined_deg": rotation_angle_deg(R_kabsch.T @ R_ci),
        "gyro_bias": np.asarray(bias).tolist(),
        "bias_source": "joint" if joint_bias else "static",
        "static_segment": static_info,  # [t0, t1] in IMU host time, or None
        "n_frames": int(len(cam_t)),
        "n_frames_rejected": int(bad_pose.sum()),
        "n_imu": int(len(imu_t)),
        "n_intervals": int(len(a_idx)),
        "interval_s": float(m * frame_dt),
        "residual_rms_deg": rms_deg,  # over inlier intervals
        "residual_median_deg": float(np.median(res_deg)),
        "outlier_fraction": outlier_frac,
        "excitation_rad_s": [float(e) for e in excitation],
        "ok": ok,
        "verdict": "good" if ok else "; ".join(advice),
    }
    return calib, report
