"""Plain-language calibration results (``CalibResult``, APP_API.md) from the engine's reports.

:func:`build_result` turns ``capture.run_flow``'s ``(value, report)`` into a grade
(excellent / good / usable / poor / failed), a headline, a one- or two-sentence verdict, metric
rows with their targets and a "what this means" note, advice items with an action for the UI's
buttons, and ``per_view`` errors mapped back to the session's view indices (``bad`` marks the
outliers). Grades:

* **stereo**: excellent = rms < 0.3 px, coverage > 60 % in both cameras, rectification row error
  < 0.2 px and scale error < 0.3 %, >= 12 views and a tilt of >= 20 deg; good = rms < 0.5 px,
  coverage > 60 % and a passing rectification check (< 0.3 px, < 0.5 %); usable = rms < 1 px,
  coverage > 40 %, row error < 0.5 px; else poor. A baseline with the wrong sign (OpenCV
  ``x_right = R x_left + T``: a left camera on the left has ``T_x < 0``) means left and right are
  swapped: grade failed, with the advice to turn on "swap left/right".
* **lasers**: excellent = both planes from the white margin, median residual < 0.05 mm, >= 6
  views each; good = both, < 0.15 mm, margin only; usable = both, < 0.3 mm (or a plane that needed
  the printed pattern too); poor = one plane only, or worse residuals; failed = none.
* **imu**: excellent = the engine's verdict good, residual < 0.1 deg and the board seen in >= 90 %
  of the frames; good = verdict good; usable = residual < 0.5 deg and < 20 % outliers; else poor.
* **check**: good (excellent with wide margins) when the engine says the calibration holds,
  else poor. A check is never saved. On the simulator the engine's IMU part is ignored: the
  simulator's check moves the board in front of a still scanner, and that part assumes a still
  board. (A check's snapshot recording holds no IMU samples anyway, like ``ohs calibrate check``.)

On the simulator, :func:`sim_truth` compares the estimate with the virtual scanner's true
calibration (adapted from ``tests/test_e2e_calibration.py``): planes and ``R_ci`` are compared in
the *raw* left camera frame (``n_raw = R1^T n``, ``R_raw = R1^T R_ci``), because an estimated
stereo calibration rectifies into a slightly different frame C than the true one.
``sim_truth_values`` (extra) holds the numbers behind the rows.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any, Sequence

import numpy as np

__all__ = ["build_result", "failed_result", "plain_error", "swapped", "sim_truth", "part_summary", "REPORT_URL",
           "KIND_NAMES"]

log = logging.getLogger(__name__)

REPORT_URL = "/api/calibration/report.json"
KIND_NAMES = {"stereo": "Stereo calibration", "lasers": "Laser calibration", "imu": "IMU calibration",
              "check": "Calibration check"}
_SAVEABLE = ("excellent", "good", "usable", "poor")


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def metric(label: str, value: str, ok: bool | None, help: str) -> dict:
    return {"label": label, "value": value, "ok": ok, "help": help}


def advice(text: str, action: str | None = None) -> dict:
    return {"text": text, "action": action}


def _result(kind: str, grade: str, headline: str, verdict: str, metrics: list, adv: list, per_view: list | None = None,
            truth: tuple[list, dict] | None = None) -> dict:
    ok = grade in ("excellent", "good")
    return {"kind": kind, "ok": ok, "grade": grade, "headline": headline, "verdict": verdict, "metrics": metrics,
            "advice": adv, "per_view": per_view or [], "sim_truth": truth[0] if truth else None,
            "sim_truth_values": truth[1] if truth else None,
            "can_save": kind != "check" and grade in _SAVEABLE, "report_url": REPORT_URL}


def plain_error(e: BaseException | str) -> str:
    """An engine error message in plain words (capitalised, no ">=", degrees as deg -> °)."""
    s = str(e).strip() or type(e).__name__ if isinstance(e, BaseException) else str(e)
    s = s.replace(">=", "at least").replace("deg ", "° ").replace("`ohs calibrate stereo`", "the stereo calibration")
    s = re.sub(r"\(15-25 recommended\)", "(15–25 recommended)", s)
    return s[:1].upper() + s[1:] if s else "The calibration failed"


_FAIL_HINT = {
    "stereo": "Add views where both cameras see the whole board, then press Finish & calibrate again.",
    "lasers": "Add views where both laser lines cross the white margin around the pattern, then try again.",
    "imu": "Record again: hold still for 2 s, then rotate slowly about all three axes with the board in view.",
    "check": "Add views at different distances with both laser lines on the page, then try again.",
}


def failed_result(kind: str, reason: str, hint: str | None = None) -> dict:
    hint = hint or _FAIL_HINT.get(kind, "")
    action = "start_over" if kind == "imu" else "add_views"
    verdict = reason.rstrip(".") + "."
    return _result(kind, "failed", f"{KIND_NAMES.get(kind, 'Calibration')} failed", verdict, [],
                   [advice(hint, action)] if hint else [])


def swapped(stereo: Any) -> bool:
    """True if the right camera is on the left (``T_x > 0``): the images are swapped."""
    try:
        return float(np.asarray(stereo.T, np.float64).reshape(3)[0]) > 0.0
    except Exception:
        return False


# --------------------------------------------------------------------------- per view
def _stereo_per_view(report: dict, view_ids: Sequence[int]) -> list[dict]:
    frames = list(report.get("frame_indices") or [])
    by_pair = {}
    for row in report.get("per_view_errors") or []:
        p = int(row.get("pair", -1))
        if 0 <= p < len(frames):
            by_pair[int(frames[p])] = max(float(row.get("left", 0.0)), float(row.get("right", 0.0)))
    rejected = set()
    for row in report.get("views_rejected") or []:
        p = int(row.get("pair", -1))
        if 0 <= p < len(frames):
            rejected.add(int(frames[p]))
    errs = [e for e in by_pair.values() if math.isfinite(e)]
    med = float(np.median(errs)) if errs else 0.0
    limit = max(2.0 * med, 0.5)
    out = []
    for vid in view_ids:
        e = by_pair.get(int(vid))
        bad = int(vid) in rejected or (e is not None and e > limit)
        if e is None and not bad:
            continue
        out.append({"index": int(vid), "error_px": None if e is None else round(e, 3), "bad": bool(bad)})
    return out


def _laser_per_view(report: dict, view_ids: Sequence[int]) -> list[dict]:
    rows = list(report.get("per_frame") or [])
    obs_frame = [int(r["index"]) for r in rows if r.get("board")]
    weak: set[int] = set()
    for entry in (report.get("lasers") or {}).values():
        for o in entry.get("per_observation") or []:
            i = int(o.get("index", -1))
            n, k = int(o.get("n_points", 0)), int(o.get("n_inliers", 0))
            if 0 <= i < len(obs_frame) and n > 0 and k < 0.5 * n:
                weak.add(obs_frame[i])
    ids = set(int(v) for v in view_ids)
    out = []
    for r in rows:
        idx = int(r.get("index", -1))
        if idx not in ids:
            continue
        e = _num(r.get("reproj_rms_px"))
        bad = (not r.get("board")) or idx in weak
        out.append({"index": idx, "error_px": None if e is None else round(e, 3), "bad": bool(bad)})
    return out


# --------------------------------------------------------------------------- stereo
def _stereo(value: Any, report: dict, view_ids: Sequence[int], truth: tuple | None) -> dict:
    rms = float(report.get("rms", float("nan")))
    cov_l, cov_r = float(report.get("coverage_left", 0.0)), float(report.get("coverage_right", 0.0))
    used, total = int(report.get("views_used", 0)), int(report.get("views_total", len(view_ids)))
    rejected = len({int(r.get("pair", -1)) for r in report.get("views_rejected") or []})
    tilt = float(report.get("max_tilt_deg", 0.0))
    base = float(report.get("baseline_mm", np.linalg.norm(np.asarray(value.T))))
    rc = report.get("rectification_check") or {}
    dv, scale = _num(rc.get("mean_abs_dv_px")), _num(rc.get("scale_error_pct"))
    per_view = _stereo_per_view(report, view_ids)
    n_bad = sum(v["bad"] for v in per_view)
    cov = min(cov_l, cov_r)
    rect_ok = dv is not None and dv < 0.3 and (scale is None or abs(scale) < 0.5)
    metrics = [
        metric("Reprojection error", f"{rms:.2f} px (want < 0.5 px)", rms < 0.5,
               "How far the detected corners are from where the calibration puts them. Lower is better."),
        metric("Coverage, left camera", f"{cov_l:.0%} of the image (want > 60 %)", cov_l > 0.6,
               "How much of the picture the board corners reached. The image corners and edges pin down the lens."),
        metric("Coverage, right camera", f"{cov_r:.0%} of the image (want > 60 %)", cov_r > 0.6,
               "The same for the right camera."),
        metric("Views used", f"{used} of {total} (aim for 15–25)", used >= 12,
               "Views where both cameras saw enough of the board."),
        metric("Views rejected", f"{rejected}", rejected == 0,
               "Views the solver dropped because they did not fit the others (blur, glare or a bent board)."),
        metric("Largest tilt", f"{tilt:.0f}° (want 20–45°)", tilt >= 20.0,
               "Tilted views fix the focal length; flat views alone cannot."),
        metric("Baseline", f"{base:.2f} mm", None,
               "The distance between the two cameras (about 60 mm on the OpenHandScan head)."),
        metric("Row error after rectification", "not measured" if dv is None else f"{dv:.2f} px (want < 0.3 px)",
               None if dv is None else dv < 0.3,
               "After rectification the same point must sit on the same row in both pictures."),
        metric("Scale error", "not measured" if scale is None else f"{scale:+.2f} % (want within ±0.5 %)",
               None if scale is None else abs(scale) < 0.5,
               "The board size measured by stereo against its printed size: checks the baseline and the print."),
    ]
    adv: list[dict] = []
    if swapped(value):
        grade = "failed"
        headline = "Left and right cameras are swapped"
        verdict = ("The picture of the right camera arrives as the left one, so this calibration is unusable. Turn on "
                   "“swap left/right” in the camera settings, connect again and start over.")
        adv.append(advice("Turn on “swap left/right” in the camera settings, connect again, then start the stereo "
                          "calibration over.", "start_over"))
        return _result("stereo", grade, headline, verdict, metrics, adv, per_view, truth)
    if rms < 0.3 and cov > 0.6 and dv is not None and dv < 0.2 and (scale is None or abs(scale) < 0.3) \
            and used >= 12 and tilt >= 20.0:
        grade = "excellent"
    elif rms < 0.5 and cov > 0.6 and rect_ok:
        grade = "good"
    elif rms < 1.0 and cov > 0.4 and (dv is None or dv < 0.5):
        grade = "usable"
    else:
        grade = "poor"
    if n_bad:
        adv.append(advice(f"Drop the {n_bad} worst view{'s' if n_bad != 1 else ''} and calibrate again.", "remove_bad_views"))
    if cov <= 0.6:
        side = "left" if cov_l <= cov_r else "right"
        adv.append(advice(f"Add views with the board near the corners and edges of the {side} picture.", "add_views"))
    if tilt < 20.0:
        adv.append(advice("Add views with the board tilted 20–45° in different directions.", "add_views"))
    if used < 12:
        adv.append(advice(f"Add views: {used} were used, 15–25 give the best result.", "add_views"))
    if rms >= 0.5 and not n_bad:
        adv.append(advice("Hold the board still, check the focus, and make sure the board is flat (glued to glass).",
                          "check_board"))
    if scale is not None and abs(scale) >= 0.5:
        adv.append(advice("The measured board size is off: check the printed 100 mm bar (print check).", "reprint"))
    if grade in ("excellent", "good"):
        headline = f"{grade.capitalize()} stereo calibration"
        verdict = (f"The two cameras are calibrated to {rms:.2f} px from {used} views"
                   + (f", and rows match to {dv:.2f} px after rectification" if dv is not None else "") + ". "
                   + ("Depth from this calibration will be accurate." if grade == "excellent" else
                      "Good enough for accurate scans."))
    elif grade == "usable":
        headline = "Usable stereo calibration"
        verdict = (f"It works ({rms:.2f} px from {used} views), but scans will be less accurate than they could be. "
                   + (adv[0]["text"] if adv else "Add a few more varied views."))
    else:
        headline = "Poor stereo calibration"
        verdict = (f"The error is {rms:.2f} px from {used} views"
                   + (f" and rows differ by {dv:.2f} px" if dv is not None and dv >= 0.3 else "")
                   + ": scans would be inaccurate. " + (adv[0]["text"] if adv else "Start over with 15–25 views."))
    return _result("stereo", grade, headline, verdict, metrics, adv, per_view, truth)


# --------------------------------------------------------------------------- lasers
def _lasers(planes: dict, report: dict, view_ids: Sequence[int], truth: tuple | None) -> dict:
    lasers = report.get("lasers") or {}
    res = report.get("residuals") or {}
    region = report.get("region_used") or {}
    names = ("vertical", "horizontal")
    fitted = [n for n in names if n in (planes or {})]
    metrics = []
    for n in names:
        e = lasers.get(n) or {}
        title = n.capitalize()
        if n not in fitted:
            metrics.append(metric(f"{title} laser", "not calibrated", False,
                                  plain_error(e.get("reason") or f"the {n} line was not found often enough")))
            continue
        med = _num((res.get(n) or {}).get("median_abs_mm"))
        p95 = _num((res.get(n) or {}).get("p95_abs_mm"))
        metrics.append(metric(f"{title} laser: fit", "n/a" if med is None else
                              f"{med:.3f} mm median, {p95 or 0:.2f} mm (95 %) (want < 0.15 mm)",
                              None if med is None else med < 0.15,
                              "How far the laser points lie from the fitted plane."))
        n_obs, n_in = int(e.get("n_observations", 0)), int(e.get("n_inliers", e.get("n_points", 0)))
        metrics.append(metric(f"{title} laser: points", f"{n_in} points from {n_obs} views (aim for 6–10 views)",
                              n_obs >= 5, "Laser points used for the fit, and from how many board poses."))
        std_deg, std_mm = _num(e.get("normal_std_deg")), _num(e.get("d_std_mm"))
        if std_deg is not None:
            metrics.append(metric(f"{title} laser: uncertainty", f"±{std_deg:.3f}° and ±{(std_mm or 0):.3f} mm",
                                  std_deg < 0.1, "The statistical uncertainty of the plane's angle and offset."))
        on_margin = region.get(n) == "margin"
        metrics.append(metric(f"{title} laser: where", "white margin" if on_margin else
                              "margin and printed pattern (the margin alone had too few points)", on_margin,
                              "Laser stripes are measured best on the blank paper around the pattern."))
    frames = int(report.get("frames", len(view_ids)))
    with_board = int(report.get("frames_with_board", 0))
    metrics.append(metric("Views with the board", f"{with_board} of {frames}", with_board >= frames,
                          "Views where the board pose was found in the rectified picture."))
    per_view = _laser_per_view(report, view_ids)
    adv: list[dict] = []
    if not fitted:
        reasons = "; ".join(plain_error((lasers.get(n) or {}).get("reason") or n) for n in names)
        r = failed_result("lasers", f"Neither laser line could be calibrated: {reasons}")
        r["metrics"], r["per_view"] = metrics, per_view
        return r
    meds = [_num((res.get(n) or {}).get("median_abs_mm")) for n in fitted]
    worst = max((m for m in meds if m is not None), default=float("inf"))
    fallback = [n for n in fitted if region.get(n) != "margin"]
    n_obs = min(int((lasers.get(n) or {}).get("n_observations", 0)) for n in fitted)
    both = len(fitted) == 2
    if both and not fallback and worst < 0.05 and n_obs >= 6:
        grade = "excellent"
    elif both and not fallback and worst < 0.15:
        grade = "good"
    elif both and worst < 0.3:
        grade = "usable"
    else:
        grade = "poor"
    for n in names:
        if n not in fitted:
            adv.append(advice(f"Add views where the {n} line crosses the white margin "
                              f"{'above or below' if n == 'vertical' else 'left or right of'} the pattern.", "add_views"))
    for n in fallback:
        adv.append(advice(f"Hold the board so the {n} line crosses the white margin, not just the pattern.", "add_views"))
    if n_obs < 6:
        adv.append(advice("Add views at more distances (150–450 mm) and tilts.", "add_views"))
    bad = sum(v["bad"] for v in per_view)
    if bad:
        adv.append(advice(f"Drop the {bad} view{'s' if bad != 1 else ''} that did not fit and calibrate again.",
                          "remove_bad_views"))
    if worst >= 0.3 and worst != float("inf"):
        adv.append(advice("If the fit stays poor, redo the stereo calibration first.", "recalibrate_stereo"))
    fit = ", ".join(f"{n} {m:.2f} mm" for n, m in zip(fitted, meds) if m is not None)
    if grade in ("excellent", "good"):
        headline = f"{grade.capitalize()} laser calibration"
        verdict = f"Both laser planes are calibrated: the laser points fit them within {fit} (median)."
    elif grade == "usable":
        headline = "Usable laser calibration"
        verdict = (f"Both planes are calibrated ({fit} median), "
                   + (f"but the {' and '.join(fallback)} line needed the printed pattern too, which is less accurate."
                      if fallback else "but the fit is looser than it should be."))
    else:
        headline = "Poor laser calibration"
        if not both:
            missing = next(n for n in names if n not in fitted)
            verdict = f"Only the {fitted[0]} laser could be calibrated; the {missing} line was not found often enough."
        else:
            verdict = f"The laser points scatter around the planes ({fit} median): the laser points would be inaccurate."
    return _result("lasers", grade, headline, verdict, metrics, adv, per_view, truth)


# --------------------------------------------------------------------------- imu
def _imu(value: Any, report: dict, truth: tuple | None) -> dict:
    from ohs.calib.imu import MIN_EXCITATION

    rms = _num(report.get("residual_rms_deg"))
    off = _num(report.get("time_offset_s"))
    bias = np.asarray(report.get("gyro_bias") or [0, 0, 0], np.float64)
    peak = _num(report.get("xcorr_peak"))
    outl = _num(report.get("outlier_fraction")) or 0.0
    exc = [float(v) for v in report.get("excitation_rad_s") or []]
    frames, with_pose = int(report.get("frames", 0)), int(report.get("frames_with_pose", 0))
    board_pct = 100.0 * with_pose / frames if frames else 0.0
    ok_engine = bool(report.get("ok"))

    def pm(key: str, fmt: str) -> str:
        v = _num(report.get(key))
        return "" if v is None else " ±" + fmt.format(v)

    metrics = [
        metric("Rotation fit", "n/a" if rms is None else f"{rms:.3f}° rms (want < 0.3°)",
               None if rms is None else rms < 0.3,
               "How well the gyro's rotations match the rotations seen on the board."),
        metric("Time offset", "n/a" if off is None else f"{off * 1e3:+.1f} ms{pm('time_offset_std_s', '{:.4f} s')}",
               None if off is None else abs(off) < 0.19, "The delay between the camera clock and the IMU clock."),
        metric("Gyro bias", f"{np.degrees(np.linalg.norm(bias)):.2f} °/s{pm('gyro_bias_std', '{:.4f} rad/s')}",
               bool(np.degrees(np.linalg.norm(bias)) < 3.0), "The gyro's reading at rest, removed from every sample."),
        metric("Camera–IMU rotation", f"{_num(report.get('R_ci_angle_deg')) or 0:.1f}° from the IMU's axes"
               + pm("R_ci_std_deg", "{:.3f}°"), None, "How the IMU board is mounted relative to the camera."),
        metric("Board seen", f"{board_pct:.0f} % of {frames} frames (want > 80 %)", board_pct >= 80.0,
               "Frames where the board pose was found while you rotated."),
    ]
    if len(exc) >= 2:
        metrics.append(metric("Rotation about all axes", f"weakest axis {exc[1]:.2f} rad/s", exc[1] >= MIN_EXCITATION,
                              "Rotating about all three axes is needed to find the mounting rotation."))
    if peak is not None:
        metrics.append(metric("Camera–gyro agreement", f"{peak:.2f} (want > 0.6)", peak >= 0.6,
                              "How strongly the camera and gyro motions correlate."))
    if rms is not None and ok_engine and rms < 0.1 and board_pct >= 90.0:
        grade = "excellent"
    elif ok_engine:
        grade = "good"
    elif rms is not None and rms < 0.5 and outl < 0.2:
        grade = "usable"
    else:
        grade = "poor"
    adv = []
    if board_pct < 80.0:
        adv.append(advice("Keep the whole board in view while you rotate, then record again.", "start_over"))
    if len(exc) >= 2 and exc[1] < MIN_EXCITATION:
        adv.append(advice("Rotate about all three axes (tilt, turn and roll), 20–40° each way, then record again.",
                          "start_over"))
    if peak is not None and peak < 0.6:
        adv.append(advice("Rotate a bit faster and more, and record again.", "start_over"))
    if grade in ("excellent", "good"):
        headline = f"{grade.capitalize()} IMU calibration"
        verdict = (f"The IMU is aligned with the camera: the gyro and the board agree to {rms:.2f}°, and the clocks "
                   f"to {abs(off or 0) * 1e3:.1f} ms of offset.")
    else:
        headline = f"{grade.capitalize()} IMU calibration"
        verdict = ((f"The gyro and the board disagree by {rms:.2f}° rms. " if rms is not None else "")
                   + (adv[0]["text"] if adv else plain_error(report.get("verdict") or "Record again.")))
    return _result("imu", grade, headline, verdict, metrics, adv, [], truth)


# --------------------------------------------------------------------------- check
def _check(report: dict, truth: tuple | None, sim: bool = False) -> dict:
    st = report.get("stereo") or {}
    la = report.get("lasers") or {}
    imu = report.get("imu") or {}
    if sim and imu.get("ok") is not None:
        # the simulator's check moves the board in front of a still scanner, but the engine's IMU check
        # assumes a still board: on the simulator it must not decide the check
        imu = {"ok": None, "verdict": "not checked on the simulator (its check moves the board, not the scanner)"}
        report = dict(report, imu=imu, ok=bool(st.get("ok")) and la.get("ok") is not False)
        if report["ok"]:
            report["verdict"] = "good"
    dv, scale = _num(st.get("mean_abs_dv_px")), _num(st.get("scale_error_pct"))
    metrics = [
        metric("Stereo: row error", "not measured" if dv is None else f"{dv:.2f} px (want < 0.3 px)",
               None if dv is None else dv < 0.3, "Matching board corners must sit on the same row in both pictures."),
        metric("Stereo: scale", "not measured" if scale is None else f"{scale:+.2f} % (want within ±0.5 %)",
               None if scale is None else abs(scale) < 0.5, "The board size measured by stereo against its real size."),
    ]
    adv = []
    meds = []
    if isinstance(la.get("lasers"), dict):
        for n, e in la["lasers"].items():
            med = _num(e.get("median_abs_mm"))
            if med is not None:
                meds.append(med)
            metrics.append(metric(f"{n.capitalize()} laser", "not seen" if med is None else
                                  f"{med:.2f} mm from its plane (want < 0.5 mm)", None if med is None else bool(e.get("ok")),
                                  "How far the laser points on the board lie from the calibrated plane."))
            if e.get("ok") is False:
                adv.append(advice(f"Recalibrate the lasers (step 5): the {n} plane no longer matches.", None))
    else:
        metrics.append(metric("Lasers", "not checked", None, plain_error(la.get("verdict") or "no laser planes")))
    if imu.get("ok") is None:
        metrics.append(metric("IMU", "not checked", None, plain_error(imu.get("verdict") or "no IMU data")))
    else:
        med = _num(imu.get("median_residual_deg"))
        metrics.append(metric("IMU", "n/a" if med is None else f"{med:.3f}° per frame (want < 0.15°)", bool(imu.get("ok")),
                              "How well the gyro still matches the camera's motion."))
        if imu.get("ok") is False:
            adv.append(advice("Recalibrate the IMU (step 6).", None))
    if st.get("ok") is False:
        adv.insert(0, advice("Recalibrate the stereo pair (step 4): the cameras may have moved.", "recalibrate_stereo"))
        if scale is not None and abs(scale) >= 0.5 and (dv is None or dv < 0.3):
            adv.append(advice("Check the printed 100 mm bar: the board size is off.", "check_board"))
    ok = bool(report.get("ok"))
    if ok:
        wide = (dv is None or dv < 0.15) and all(m < 0.2 for m in meds)
        grade = "excellent" if wide else "good"
        headline = "Your calibration still holds"
        verdict = ("The check passed" + (f": rows agree to {dv:.2f} px" if dv is not None else "")
                   + (f" and the laser points lie within {max(meds):.2f} mm of their planes" if meds else "") + ".")
    else:
        grade = "poor"
        headline = "The calibration needs attention"
        verdict = plain_error(report.get("verdict") or "the check failed") + "."
    r = _result("check", grade, headline, verdict, metrics, adv, [], truth)
    r["can_save"] = False
    return r


# --------------------------------------------------------------------------- simulator truth
def _stereo_errors(est: Any, true: Any) -> dict:
    import cv2

    from ohs import geometry as G
    from ohs.sim.render import project_to_raw

    out = {"fx_err_px": float(est.K1[0, 0] - true.K1[0, 0]), "cx_err_px": float(est.K1[0, 2] - true.K1[0, 2]),
           "cy_err_px": float(est.K1[1, 2] - true.K1[1, 2]),
           "baseline_err_mm": float(np.linalg.norm(est.T) - np.linalg.norm(true.T)),
           "rel_rotation_err_deg": float(G.rotation_angle_deg(np.asarray(est.R) @ np.asarray(true.R).T))}
    w, h = (int(v) for v in true.image_size)
    rng = np.random.default_rng(0)
    z = rng.uniform(200, 450, 4000)
    m = 0.03 * w
    uv = rng.uniform([m, m], [w - m, h - m], (4000, 2))
    P = G.pixels_to_rays(uv, true.K_rect) * z[:, None]
    raw_l, raw_r = project_to_raw(P, true, "left"), project_to_raw(P, true, "right")
    ok = np.isfinite(raw_l).all(1) & np.isfinite(raw_r).all(1)
    rl = cv2.undistortPoints(raw_l[ok].reshape(-1, 1, 2), est.K1, est.D1, R=est.R1, P=est.P1).reshape(-1, 2)
    rr = cv2.undistortPoints(raw_r[ok].reshape(-1, 1, 2), est.K2, est.D2, R=est.R2, P=est.P2).reshape(-1, 2)
    dv = np.abs(rl[:, 1] - rr[:, 1])
    out.update(row_err_median_px=float(np.median(dv)), row_err_p95_px=float(np.percentile(dv, 95)))
    disp = rl[:, 0] - rr[:, 0] - (est.P1[0, 2] - est.P2[0, 2])
    with np.errstate(divide="ignore", invalid="ignore"):
        z_est = est.P1[0, 0] * est.baseline / disp
    good = np.isfinite(z_est)
    out["depth_scale_err_pct"] = float(np.median(z_est[good] / z[ok][good] - 1.0) * 100.0) if good.any() else float("nan")
    return out


def _plane_errors(est: dict, true: dict, R1_est: np.ndarray, R1_true: np.ndarray, K: np.ndarray,
                  size: tuple[int, int]) -> dict:
    from ohs import geometry as G

    w, h = size
    step = max(4, int(w // 80))
    uu, vv = np.meshgrid(np.arange(0, w, step) + 0.5, np.arange(0, h, step) + 0.5)
    rays = G.pixels_to_rays(np.column_stack([uu.ravel(), vv.ravel()]), K)
    out: dict = {}
    for name, t in true.items():
        if name not in est:
            continue
        n_e, n_t = np.asarray(R1_est).T @ est[name].normal, np.asarray(R1_true).T @ t.normal
        X_t = G.ray_plane_intersect(rays, n_t, t.d)
        X_e = G.ray_plane_intersect(rays, n_e, est[name].d)
        ok = np.isfinite(X_t).all(1) & np.isfinite(X_e).all(1) & (X_t[:, 2] > 150) & (X_t[:, 2] < 450)
        dist = np.linalg.norm(X_e[ok] - X_t[ok], axis=1) if ok.any() else np.array([np.nan])
        out[name] = {"angle_deg": float(np.degrees(np.arccos(np.clip(abs(float(n_e @ n_t)), 0, 1)))),
                     "d_err_mm": float(est[name].d - t.d), "point_err_median_mm": float(np.median(dist)),
                     "point_err_max_mm": float(np.max(dist))}
    return out


def _imu_errors(est: Any, true: Any, R1_est: np.ndarray, R1_true: np.ndarray) -> dict:
    from ohs import geometry as G

    R_e, R_t = np.asarray(R1_est).T @ np.asarray(est.R_ci), np.asarray(R1_true).T @ np.asarray(true.R_ci)
    return {"R_ci_err_deg": float(G.rotation_angle_deg(R_e.T @ R_t)),
            "time_offset_err_ms": 1e3 * float(est.time_offset - true.time_offset),
            "gyro_bias_err_mrad_s": 1e3 * float(np.linalg.norm(np.asarray(est.gyro_bias) - np.asarray(true.gyro_bias)))}


def sim_truth(kind: str, value: Any, target: Any, truth: Any) -> tuple[list, dict] | None:
    """``(metric rows, numbers)`` of the estimate against the simulator's true calibration."""
    if truth is None:
        return None
    rows: list[dict] = []
    nums: dict = {}
    try:
        stereo = value if kind == "stereo" else (target.stereo if target is not None else None)
        if kind in ("stereo", "check") and stereo is not None:
            e = _stereo_errors(stereo, truth.stereo)
            nums["stereo"] = e
            rows += [
                metric("Baseline vs truth", f"{e['baseline_err_mm']:+.3f} mm", abs(e["baseline_err_mm"]) < 0.3,
                       "Estimated minus true distance between the cameras."),
                metric("Focal length vs truth", f"{e['fx_err_px']:+.2f} px", abs(e["fx_err_px"]) < 3.0,
                       "Estimated minus true focal length of the left camera."),
                metric("Principal point vs truth", f"{max(abs(e['cx_err_px']), abs(e['cy_err_px'])):.2f} px",
                       max(abs(e["cx_err_px"]), abs(e["cy_err_px"])) < 3.0, "Where the optical axis meets the image."),
                metric("Camera rotation vs truth", f"{e['rel_rotation_err_deg']:.3f}°", e["rel_rotation_err_deg"] < 0.1,
                       "Error of the rotation between the two cameras."),
                metric("Row error on true points", f"{e['row_err_p95_px']:.3f} px (95 %)", e["row_err_p95_px"] < 0.3,
                       "True 3D points through the estimated rectification: rows must match."),
                metric("Depth scale vs truth", f"{e['depth_scale_err_pct']:+.3f} %", abs(e["depth_scale_err_pct"]) < 0.3,
                       "Median depth error of stereo triangulation with this calibration."),
            ]
        planes = value if kind == "lasers" else (target.lasers if (kind == "check" and target is not None) else None)
        if planes and target is not None:
            size = tuple(int(v) for v in truth.stereo.image_size)
            e = _plane_errors(planes, truth.lasers, target.stereo.R1, truth.stereo.R1, truth.stereo.K_rect, size)
            nums["lasers"] = e
            for n, x in e.items():
                rows += [
                    metric(f"{n.capitalize()} plane angle vs truth", f"{x['angle_deg']:.3f}°", x["angle_deg"] < 0.2,
                           "Angle between the estimated and the true laser plane (raw camera frame)."),
                    metric(f"{n.capitalize()} plane offset vs truth", f"{x['d_err_mm']:+.3f} mm", abs(x["d_err_mm"]) < 0.3,
                           "Estimated minus true distance of the plane from the camera."),
                    metric(f"{n.capitalize()} laser points vs truth",
                           f"{x['point_err_median_mm']:.3f} mm median, {x['point_err_max_mm']:.2f} mm max",
                           x["point_err_median_mm"] < 0.25, "Laser points over 150–450 mm with the estimated plane."),
                ]
        imu = value if kind == "imu" else (target.imu if (kind == "check" and target is not None) else None)
        if imu is not None and truth.imu is not None and target is not None:
            e = _imu_errors(imu, truth.imu, target.stereo.R1, truth.stereo.R1)
            nums["imu"] = e
            rows += [
                metric("Camera–IMU rotation vs truth", f"{e['R_ci_err_deg']:.3f}°", e["R_ci_err_deg"] < 0.5,
                       "Error of the mounting rotation (raw camera frame)."),
                metric("Time offset vs truth", f"{e['time_offset_err_ms']:+.2f} ms", abs(e["time_offset_err_ms"]) < 2.0,
                       "Error of the camera–IMU clock offset."),
                metric("Gyro bias vs truth", f"{e['gyro_bias_err_mrad_s']:.2f} mrad/s", e["gyro_bias_err_mrad_s"] < 1.0,
                       "Error of the gyro bias."),
            ]
    except Exception as e:  # a comparison only
        log.exception("simulator truth comparison failed")
        rows.append(metric("Comparison with the truth", "not available", None, f"It could not be computed: {e}"))
    return rows, nums


# --------------------------------------------------------------------------- entry points
def build_result(kind: str, value: Any, report: dict, *, view_ids: Sequence[int] = (), target: Any = None,
                 truth: Any = None, sim: bool | None = None) -> dict:
    """``CalibResult`` of a successful flow (``value``, ``report`` from ``capture.run_flow``).

    ``sim`` (default: a ``truth`` was given) marks a simulator session: its check ignores the
    engine's IMU part, which assumes a still board.
    """
    tr = sim_truth(kind, value, target, truth)
    if kind == "stereo":
        return _stereo(value, report, view_ids, tr)
    if kind == "lasers":
        return _lasers(value or {}, report, view_ids, tr)
    if kind == "imu":
        return _imu(value, report, tr)
    if kind == "check":
        return _check(report, tr, truth is not None if sim is None else bool(sim))
    raise ValueError(f"unknown calibration kind {kind!r}")


def part_summary(kind: str, report: dict, value: Any) -> str:
    """Short text for the setup step after saving ("0.21 px from 18 views")."""
    if kind == "stereo":
        return f"{float(report.get('rms', float('nan'))):.2f} px from {int(report.get('views_used', 0))} views"
    if kind == "lasers":
        res = report.get("residuals") or {}
        parts = [f"{n} {float(res[n]['median_abs_mm']):.2f} mm" for n in ("vertical", "horizontal")
                 if n in (value or {}) and n in res and res[n].get("median_abs_mm") is not None]
        return ("Both laser lines" if len(parts) == 2 else "One laser line") + (f" ({', '.join(parts)})" if parts else "")
    if kind == "imu":
        rms, off = _num(report.get("residual_rms_deg")), _num(report.get("time_offset_s"))
        return (f"{rms:.2f}° fit" if rms is not None else "IMU calibrated") + (
            f", offset {off * 1e3:.0f} ms" if off is not None else "")
    return "Calibration checked"
