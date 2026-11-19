"""Limits for the signs of a stale calibration (FEATURE_PLAN H2), for the status of H3.

Every scan writes down what it saw of its calibration (``report["health"]``,
:mod:`ohs.calib.scan_health`, H1). Which of those numbers really show a calibration that went stale
was measured on the simulator (``tools/calib_health_study.py``; ``VALIDATION.md``, "Which signs show a
stale calibration"): recordings rendered with the true calibration, processed with one that is slightly
wrong, one fault at a time, and compared with the simulator's true shapes. The rule a sign had to pass:
it moves clearly before the scan's products (the mesh, fitted measurements, the laser cloud, the mesh
after Refine with lasers) are 0.1 mm off, and does not move as much on a hard but well-calibrated
scene (a dark part, strong speckle and noise standing in for a shiny one, a 0.5 mm plate).

This module holds the limits only. Joining them into Valid / Check recommended / Recalibration
required, with the part and the reason in words, is H3. All of it is provisional until the real
scanner has been measured over days and temperatures (H5).

**Kept** (:data:`LIMITS`; each value is read as its absolute value):

* **Cameras: the row error** (``row_error.median_px``, full-resolution pixels). True calibration:
  0.046-0.066 px on six scenes, 0.126 px on the speckled bracket. The cameras turned about the
  baseline (pitch) put the products 0.1 mm off at 0.025-0.040 deg on the bracket, where it reads
  0.31-0.52 px, and at about 0.012 deg on the dark bracket, where it reads 0.20 px. "Check" 0.15 px is
  above every true scene and below the dark bracket's crossing; "recalibrate" 0.3 px is at the
  earliest crossing on a normal scene. The launch check's 0.6 px (:func:`ohs.calib.drift.drift_check`)
  comes late: the products are then 0.12-0.17 mm off, and at 1.3 px (0.1 deg) tracking failed on
  every scene. The row error does not see the yaw (about the up axis) or the baseline at all, and
  sees a roll (about the optical axis) only late (0.09-0.14 px where it costs 0.1 mm).
* **Lasers: each line's points against the finished mesh** (``laser_mesh.per_line.<line>.median_mm``,
  signed median, mm). True calibration: within 0.016 mm on all seven scenes, the hard ones too, and
  for both lines alike (the horizontal line's six-fold scatter is in the depth gap before the drop,
  not in this median). A laser plane's tilt or offset that puts the laser cloud or the refined mesh
  0.1 mm off reads 0.16-0.30 mm; the cameras' yaw and the baseline at their crossings read 0.11-0.21
  mm on both lines at once. "Check" 0.05 mm is three times the hardest true scene; "recalibrate" 0.10
  mm is just under the smallest reading at a crossing (0.106 mm). The limits are per laser family
  (:func:`laser_family`) although they came out the same, so that the four-laser build and the real
  scanner can set them apart.
* **IMU: the tracker's rotation against the gyro's** (``tracking.gyro_jump_deg.median``, degrees per
  frame). True calibration: 0.0023-0.0074 deg, 0.0099 on the speckled bracket. The IMU turned 1 deg
  reads 0.013-0.015, 2 deg 0.026-0.029, 5 deg 0.066-0.070; its clock 10 ms off 0.012-0.014, 20 ms
  0.021. No IMU fault up to 5 deg or 20 ms put the products 0.1 mm off on the bracket (the tracker's
  ICP holds the pose), so these limits are not set by the products: "check" 0.015 deg is 1.5 times the
  hardest true scene, "recalibrate" 0.03 deg is half again over the gyro prior's own sigma (0.02 deg
  per frame, ``FusionParams.gyro_sigma_deg``): beyond it the prior pulls the wrong way rather than
  helps, which matters on a part whose shape does not hold the pose by itself (not measured here). It
  grows with how fast the scanner turns (the simulated sweeps turn 12-22 deg/s).

**How a fault shows** (for H3 to name the part): a camera fault can move the laser lines too, so the
row error is read first. The yaw and the baseline move both lines' gaps the same way and by about
the same amount (their ratio 0.7-1.3); a laser fault moves its own line only. A pitch moves the
horizontal line's gap as well (0.13 mm at 0.02 deg). **A roll is not told apart:** it moves the
vertical line's gap alone (0.16-0.23 mm where it costs 0.1 mm) while the row error is still under
its check limit, so it reads as a vertical laser fault.

**The GPU path only** (the default where there is a GPU). On the CPU path (``OHS_COMPUTE=cpu``, or a
machine without a GPU; ``report["params"]["compute"]["tracking_model"]`` is not ``"gpu"``) the tracking
is less accurate and does not repeat from run to run (``MAINTENANCE_PLAN.md``, F13): on the bracket
the true calibration's mesh was 0.67 mm (median) from the true shape against 0.32 mm on the GPU, its
tracker turned 0.036 deg a frame away from the gyro (over "recalibrate" here) and the horizontal
line's gap read 0.08 and 0.13 mm (two scans). The row error does not depend on the path (it is
measured on the rectified images: the same 0.062 px on both) and still caught the pitch. So on the
CPU path only :data:`CPU_PATH` holds; the other limits would call a good calibration stale there.

**Dropped from the status** (:data:`DROPPED`; they stay in the report). **Not judged here:** the
temperature (the simulator has no warm-up, knocks or loose screws: H5, real hardware) and
Straighten's correction (only the server's stages fill it in; not measured).

**The status (H3)**: :func:`status` joins the launch check, the last 2-minute board check and the
last scans' health blocks into ``"valid"`` / ``"check"`` / ``"recalibrate"`` (Valid, Check
recommended, Recalibration required), with the part (cameras, lasers, IMU, or None for the
calibration as a whole), one sentence and the wizard step to do next. :func:`scan_status` is the
same for one scan alone, from its own block (written into its ``report.json``). Both are plain
functions of plain data: no file, no clock (``now`` is given), no server. The app warns with it, it
never blocks a scan. The rules, in the order they are applied:

1. **One scan is read** (:func:`read_scan`) **row error first**. Over its "check" limit it names the
   cameras, and the laser lines are not judged in that scan (a pitch moves the horizontal line, and
   at 0.03-0.05 deg it loses its points). Below it: both laser lines off the mesh the same way and
   by about the same amount (:data:`BOTH_LINES_RATIO`) name the cameras too (the yaw and the
   baseline do that, H2); one line off, or the two off in different ways, names the lasers, with the
   cameras as what to try next (a camera roll reads exactly as a vertical-laser fault). One line
   without usable points while another has them is a laser sign of its own ("check" level); all
   lines without points means the scan had no laser data, which says nothing. The IMU is read on
   its own. On the CPU path (:data:`CPU_PATH`), and on a scan shorter than :data:`MIN_FRAMES`
   processed frames (the limits were measured on 90-120-frame scans; short pieces of a speckled part
   read past the laser limits with a right calibration), only the row error is read: the lasers and
   the IMU are "not judged", neither stale nor fine.
2. **The last scans must agree** (:data:`SCANS_WINDOW`, :data:`SCANS_AGREE`): a part moves the
   status only when at least 2 of the last 3 scans with this calibration that judged it reach that
   level. One hard surface can read high with a right calibration (the speckled bracket's row error
   is 0.126 px against a "check" limit of 0.15; the CPU path's horizontal-line gap read 0.08 and
   0.13 mm in two runs of one recording), so one scan alone does not move the status; its own
   report still says what it saw. A row-error "check" from scans made before a 2-minute check that
   found the rows in order (it passed, or failed on other parts only) is answered by that check (it
   measures the same rows on the board), and drops.
3. **The launch check** (rows on 3 idle frames at each connect, :func:`ohs.calib.drift.drift_check`)
   counts only when it ran after the calibration was last saved and not on a replay. Its own
   ``"drift"`` (over 0.6 px, the alert as it was) is "Recalibration required" for the cameras; from
   :data:`LAUNCH_CHECK_PX` (0.3 px) it is "Check recommended" (the scans' 0.15 px cannot judge it: a
   right calibration reads up to 0.148 px there). Both are to confirm with the 2-minute check, and a
   2-minute check after it that found the rows in order answers it.
4. **The last 2-minute check**, if it ran after the last save: failed is "Recalibration required"
   for the parts it failed (``last_check["parts"]``; unknown for a check from before that key).
   Its age: when neither it nor a calibration step happened in :data:`BOARD_CHECK_MAX_AGE_DAYS`
   days, "Check recommended" for the calibration as a whole.
5. **The worst wins**: Recalibration required over Check recommended over Valid; on a tie the
   cameras first (redoing them redoes the rest), then the lasers, then the IMU. Every finding
   that is not Valid is listed in ``reasons``.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

__all__ = ["PARTS", "Limit", "LIMITS", "CPU_PATH", "DROPPED", "laser_family", "values",
           "STATES", "STATE_LABELS", "STEPS", "BOARD_CHECK_MAX_AGE_DAYS", "SCANS_WINDOW", "SCANS_AGREE",
           "BOTH_LINES_RATIO", "LAUNCH_CHECK_PX", "MIN_FRAMES", "STATUS_VERSION", "read_scan", "scan_status", "status",
           "scan_item", "trim_block", "calibration_key", "lines_of_report"]

PARTS = ("cameras", "lasers", "imu")


@dataclass(frozen=True)
class Limit:
    """One kept sign: where it is in ``report["health"]`` (``path``; ``"{line}"`` stands for each
    laser line of ``family``), the part it speaks for, and its two limits on its absolute value."""

    part: str  # "cameras", "lasers" or "imu"
    path: tuple[str, ...]
    check: float  # from here: "Check recommended"
    recalibrate: float  # from here: "Recalibration required"
    unit: str
    family: str | None = None  # the laser family of a per-line sign


LIMITS: dict[str, Limit] = {
    "row_error": Limit("cameras", ("row_error", "median_px"), check=0.15, recalibrate=0.3, unit="px"),
    "laser_mesh_gap:vertical": Limit("lasers", ("laser_mesh", "per_line", "{line}", "median_mm"), check=0.05,
                                     recalibrate=0.10, unit="mm", family="vertical"),
    "laser_mesh_gap:horizontal": Limit("lasers", ("laser_mesh", "per_line", "{line}", "median_mm"), check=0.05,
                                       recalibrate=0.10, unit="mm", family="horizontal"),
    "gyro_jump": Limit("imu", ("tracking", "gyro_jump_deg", "median"), check=0.015, recalibrate=0.03, unit="deg"),
}

#: the limits that also hold for a scan processed on the CPU path (module docstring, "The GPU path only")
CPU_PATH: tuple[str, ...] = ("row_error",)

#: the signs of the health block that are not used for the status, and why (measured, H2)
DROPPED: dict[str, str] = {
    "row_error_p90": "moves with the median and scatters more; nothing it adds",
    "row_error_signed": "a pitch shows in it as in the median, a roll by where in the image the matches lie: "
                        "depends on the scene",
    "laser_depth": "each line against the stereo depth before the drop: the horizontal line's median reads "
                   "-0.13 mm on the speckled bracket with the true calibration and only 0.2-0.4 mm where a fault "
                   "costs 0.1 mm; the vertical line's is as good as its mesh gap but says the same",
    "laser_depth_rejected": "the depth check's drops rise only at gross faults (a 1 % baseline)",
    "laser_points_per_frame": "falls only at gross faults (a 1 % baseline, a 0.2 deg laser tilt) and with the scene",
    "laser_mesh_all": "both lines together: 0.05-0.12 mm with the true calibration, by scene; the per-line "
                      "medians are quieter and say which line",
    "tracking_rmse": "0.11-0.59 mm with the true calibration, by scene; moves only at faults that cost more "
                     "than 0.1 mm",
    "gyro_jump_p95": "follows the median, noisier",
    "temperature": "not judged: the simulator has no warm-up, knocks or loose screws (H5, real hardware)",
    "straighten": "not measured: only the server's stages fill it in",
}


def laser_family(line: str) -> str:
    """``"vertical"`` or ``"horizontal"`` for a laser line's name (``"vertical_2"`` -> ``"vertical"``)."""
    return "vertical" if str(line).startswith("vertical") else "horizontal"


def _at(d: Any, keys: tuple[str, ...]) -> Any:
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def values(block: dict, limit: Limit) -> dict[str, float | None]:
    """The sign of ``limit`` in a health block: ``{"": value}``, or ``{line: value}`` for each laser
    line of its family. None where the scan has no value (too few matches, no gyro, no line)."""
    if "{line}" not in limit.path:
        v = _at(block, limit.path)
        return {"": None if v is None else float(v)}
    k = limit.path.index("{line}")
    lines = _at(block, limit.path[:k]) or {}
    out = {}
    for line in sorted(lines):
        if laser_family(line) == limit.family:
            v = _at(lines[line], limit.path[k + 1:])
            out[line] = None if v is None else float(v)
    return out


# =========================================================================== the status (H3)
STATUS_VERSION = 1
STATES = ("valid", "check", "recalibrate")  # index = level: 0, 1, 2
STATE_LABELS = {"valid": "Valid", "check": "Check recommended", "recalibrate": "Recalibration required"}
PART_LABELS = {"cameras": "Cameras", "lasers": "Lasers", "imu": "IMU"}
_PART_RANK = {"cameras": 0, "lasers": 1, "imu": 2, None: 3}
_SOURCE_RANK = {"board_check": 0, "scans": 1, "launch_check": 2, "board_check_age": 3}

#: a board check (or a calibration step) older than this: "Check recommended". Provisional and
#: cautious: the simulator has no warm-up, knocks or loose screws, so it cannot say how fast a real
#: scanner goes stale (H5). The 2-minute check costs two minutes; a fortnight is short enough to
#: catch a housing that creeps with the seasons before many scans are made with it.
BOARD_CHECK_MAX_AGE_DAYS = 14
#: how many of the last scans with this calibration are looked at, and how many must agree before a
#: part moves the status (module docstring, rule 2)
SCANS_WINDOW = 3
SCANS_AGREE = 2
#: both laser lines off the mesh the same way, the larger at most this many times the smaller: the
#: cameras (yaw, baseline), not two lasers. H2 measured 0.7-1.3 for the yaw and the baseline.
BOTH_LINES_RATIO = 2.0
#: the launch check's value (full-resolution px, like a scan's row error) from which it recommends a
#: check. Measured on H2's seven scenes (3 idle frames, 6 triplets each): a right calibration reads
#: 0.054-0.115 px (median per scene), up to 0.148 px for one triplet on the speckled bracket, so the
#: scans' "check" limit of 0.15 px cannot judge it; a pitch of 0.02 deg reads 0.26-0.31 px on every
#: scene, 0.05 deg 0.67-0.71 px (the existing "drift" alert, unchanged). So the scans' "recalibrate"
#: limit is used, as a "check": one look at three idle frames, confirmed by the 2-minute check.
LAUNCH_CHECK_PX = LIMITS["row_error"].recalibrate
#: the fewest processed frames for a scan's laser and IMU signs to be judged. H2's limits were
#: measured on 90-120-frame scans; on 12-frame pieces of the speckled bracket the true calibration's
#: horizontal line read 0.04-0.11 mm off the mesh (30 frames: 0.03-0.08; 90 frames: 0.016), past both
#: laser limits. The row error held on every piece (0.12-0.13 px) and is judged on any scan.
MIN_FRAMES = 90

#: the wizard step of each action (``SetupState.steps`` ids) and its words
STEPS: dict[str, str] = {
    "check": "Run the 2-minute check",
    "stereo": "Redo the camera calibration, then the lasers and the IMU (a new camera calibration needs them again)",
    "lasers": "Redo the laser calibration",
    "imu": "Redo the IMU calibration",
}
_PART_STEP = {"cameras": "stereo", "lasers": "lasers", "imu": "imu"}


def _action(step: str | None, text: str | None = None) -> dict | None:
    return None if step is None else {"step": step, "text": text or STEPS[step]}


def _level(value: float | None, lim: Limit) -> int | None:
    if value is None or not math.isfinite(float(value)):
        return None
    a = abs(float(value))
    return 2 if a >= lim.recalibrate else 1 if a >= lim.check else 0


def _line_limit(line: str) -> Limit:
    return LIMITS[f"laser_mesh_gap:{laser_family(line)}"]


def _line_name(line: str) -> str:
    return str(line).replace("_", " ")


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# --------------------------------------------------------------------------- one scan
def read_scan(block: dict | None, *, tracking_model: str | None = None, lines: Sequence[str] | None = None,
              frames: int | None = None) -> dict:
    """What one scan's health block says, part by part (module docstring, rule 1).

    ``tracking_model``: ``report["params"]["compute"]["tracking_model"]`` (``"gpu"``: every kept sign
    holds; anything else: the row error only). ``lines``: the laser lines the scan should have
    (default: those in its ``laser_mesh.per_line``). ``frames``: the frames it processed
    (``report["frames"]["processed"]``); under :data:`MIN_FRAMES` the lasers and the IMU are not
    judged (None: not known, judged). Returns ``{"gpu", "short", "frames", "signs", "parts"}``: ``signs``
    each kept sign's ``{value, state, unit, check, recalibrate}`` (``missing`` for a laser line
    without a value; ``state`` None where the sign is not judged); ``parts`` per part None (not
    judged in this scan) or ``{level 0-2, kind, value, ...}``, where ``kind`` is ``"rows"`` or
    ``"both_lines"`` (cameras), ``"line"``, ``"lines"`` or ``"line_missing"`` (lasers), ``"gyro"`` (IMU)."""
    block = block if isinstance(block, dict) else {}
    gpu = tracking_model == "gpu"
    short = frames is not None and int(frames) < MIN_FRAMES
    full = gpu and not short  # the laser and IMU signs hold
    signs: dict[str, dict] = {}
    parts: dict[str, dict | None] = {p: None for p in PARTS}

    def sign(name: str, lim: Limit, v: float | None, judged: bool, **extra: Any) -> None:
        lv = _level(v, lim) if judged else None
        signs[name] = {"value": None if v is None else round(float(v), 4), "unit": lim.unit, "check": lim.check,
                       "recalibrate": lim.recalibrate, "state": None if lv is None else STATES[lv], **extra}

    row_lim = LIMITS["row_error"]
    row = _num(values(block, row_lim)[""])
    row_lv = _level(row, row_lim)
    sign("row_error", row_lim, row, True)
    if row_lv is not None:
        parts["cameras"] = {"level": row_lv, "kind": "rows", "value": row}

    per_line = _at(block, ("laser_mesh", "per_line"))
    per_line = per_line if isinstance(per_line, dict) else {}
    expected = sorted(set(lines) if lines is not None else set(per_line))
    line_vals = {ln: _num(_at(per_line.get(ln), ("median_mm",))) for ln in expected}
    for ln, v in line_vals.items():
        sign(f"laser:{ln}", _line_limit(ln), v, full, missing=v is None)
    gyro_lim = LIMITS["gyro_jump"]
    gyro = _num(values(block, gyro_lim)[""])
    sign("gyro_jump", gyro_lim, gyro, full)

    if full:
        present = {ln: v for ln, v in line_vals.items() if v is not None}
        missing = [ln for ln, v in line_vals.items() if v is None]
        if present and not (row_lv is not None and row_lv >= 1):
            # the cameras' rows are in line (or unknown): the laser lines can speak
            lv = {ln: _level(v, _line_limit(ln)) or 0 for ln, v in present.items()}
            moved = [ln for ln in present if lv[ln] >= 1]
            fams = {laser_family(ln) for ln in moved}
            mags = [abs(present[ln]) for ln in moved]
            alike = (bool(moved) and len(moved) == len(present) and fams == {"vertical", "horizontal"}
                     and len({present[ln] > 0 for ln in moved}) == 1 and max(mags) <= BOTH_LINES_RATIO * min(mags))
            if alike:  # the yaw or the baseline (H2): both lines the same way; the lasers are not judged
                parts["cameras"] = {"level": min(lv[ln] for ln in moved), "kind": "both_lines",
                                    "value": max(present.values(), key=abs), "values": dict(present), "row": row}
            else:
                f: dict = {"level": 0, "kind": "line", "lines": [], "values": dict(present), "missing": missing}
                if moved:
                    worst = max(moved, key=lambda ln: abs(present[ln]))
                    f.update(level=max(lv[ln] for ln in moved), kind="line" if len(moved) == 1 else "lines",
                             lines=sorted(moved), value=present[worst], line=worst)
                if missing and f["level"] < 1:
                    f.update(level=1, kind="line_missing", lines=list(missing), line=missing[0], value=None)
                parts["lasers"] = f
        gyro_lv = _level(gyro, gyro_lim)
        if gyro_lv is not None:
            parts["imu"] = {"level": gyro_lv, "kind": "gyro", "value": gyro}
    return {"gpu": gpu, "short": short, "frames": frames, "tracking_model": tracking_model, "signs": signs,
            "parts": parts}


def _subject(k: int, n: int) -> tuple[str, str]:
    """``(subject, verb ending)``: "This scan" / "The last 3 scans" / "2 of the last 3 scans"."""
    if n <= 1:
        return "This scan", "s"
    if k >= n:
        return f"The last {n} scans", ""
    return f"{k} of the last {n} scans", ""


def _scan_words(part: str, f: dict, subj: str, s: str) -> tuple[str, dict | None, dict | None]:
    """``(sentence, action, then)`` of a scan finding at level 1 or 2."""
    lv = f["level"]
    if part == "cameras" and f["kind"] == "rows":
        lim = LIMITS["row_error"]
        if lv >= 2:
            return (f"{subj} show{s} the two cameras out of line: matching points sit {f['value']:.2f} px apart in "
                    f"height (want under {lim.check:.2f} px), enough to put measurements 0.1 mm off.",
                    _action("stereo"), None)
        return (f"{subj} show{s} the two cameras slightly out of line: matching points sit {f['value']:.2f} px apart "
                f"in height (want under {lim.check:.2f} px).", _action("check"),
                _action("stereo", "If the check does not pass, redo the camera calibration"))
    if part == "cameras":  # both lines alike
        return (f"{subj} show{s} both laser lines about {abs(f['value']):.2f} mm off the surface in the same "
                "direction while the rows still match: that points at the distance or angle between the cameras, "
                "not at the lasers.", _action("stereo"), None)
    if part == "lasers":
        if f["kind"] == "line_missing":
            name = _line_name(f["line"])
            return (f"{subj} {'has' if s else 'have'} no usable points from the {name} laser line while the other "
                    "line has them.",
                    _action("lasers", f"Check that the {name} laser lights up, then redo the laser calibration"),
                    _action("stereo", "If it lights up and later scans still lack it, redo the camera calibration"))
        if f["kind"] == "lines":
            each = ", ".join(f"the {_line_name(ln)} line {abs(f['values'][ln]):.2f} mm" for ln in f["lines"])
            return (f"{subj} show{s} the laser lines off the surface in different ways ({each}).", _action("lasers"),
                    _action("stereo", "If later scans still show it, redo the camera calibration"))
        lim = _line_limit(f["line"])
        name = _line_name(f["line"])
        why = ("a small turn of one camera looks just the same" if laser_family(f["line"]) == "vertical"
               else "a small tilt between the cameras moves this line too")
        return (f"{subj} show{s} the {name} laser line {abs(f['value']):.2f} mm off the surface (want within "
                f"{lim.check:.2f} mm) while the cameras look in line.", _action("lasers"),
                _action("stereo", f"If later scans still show it, redo the camera calibration: {why}"))
    lim = LIMITS["gyro_jump"]
    tail = "; it may start to mislead the tracking." if lv >= 2 else "."
    return (f"{subj} show{s} the motion sensor (IMU) turning {f['value']:.3f}° a frame away from what the cameras "
            f"see (want under {lim.check:.3f}°){tail}", _action("imu"), None)


def _from_scans(readings: list[tuple[dict, dict]], window: int, agree: int) -> tuple[list[dict], dict, int]:
    """The scans' findings (rule 2): ``(findings, {part: level or None}, scans used)``.

    ``readings``: ``(item, read_scan(...))``, most recent first. A part counts when at least
    ``agree`` of the last ``window`` scans judged it; its level is the highest that ``agree`` of
    them reach; the words come from the most recent of those."""
    use = readings[:window]
    findings: list[dict] = []
    levels: dict[str, int | None] = {p: None for p in PARTS}
    for part in PARTS:
        judged = [r["parts"][part] for _, r in use if r["parts"][part] is not None]
        if len(judged) < agree:
            continue
        levels[part] = 0
        for lv in (2, 1):
            hit = [f for f in judged if f["level"] >= lv]
            if len(hit) >= agree:
                levels[part] = lv
                f = dict(hit[0], level=lv)
                subj, s = _subject(len(hit), len(judged))
                text, act, then = _scan_words(part, f, subj, s)
                findings.append({"source": "scans", "level": lv, "part": part, "reason": text, "action": act,
                                 "then": then, "kind": f["kind"], "value": f.get("value"),
                                 "scans": {"agree": len(hit), "of": len(judged)}})
                break
    return findings, levels, len(use)


# --------------------------------------------------------------------------- dates
def _time(x: Any) -> datetime | None:
    if isinstance(x, datetime):
        return x if x.tzinfo is not None else x.replace(tzinfo=timezone.utc)
    if not x:
        return None
    try:
        dt = datetime.fromisoformat(str(x))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _ago(when: datetime | None, now: datetime) -> str:
    if when is None:
        return "some time ago"
    days = (now.astimezone().date() - when.astimezone().date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    return f"on {when.astimezone().date().isoformat()}"


# --------------------------------------------------------------------------- the joined status
def _launch_finding(lc: dict | None, saved: datetime | None, passed: datetime | None) -> tuple[dict | None, bool]:
    """``(finding or None, whether it speaks for the calibration)`` from the launch check (rule 3)."""
    if not isinstance(lc, dict):
        return None, False
    when = _time(lc.get("when"))
    if when is None or (saved is not None and when < saved):
        return None, False  # it checked the calibration before the last save
    state = lc.get("state")
    dv = _num(lc.get("dv_px"))
    answered = passed is not None and passed >= when  # a 2-minute check after it found the rows in order
    if state == "ok" and dv is not None and dv >= LAUNCH_CHECK_PX and not answered:
        return ({"source": "launch_check", "level": 1, "part": "cameras",
                 "reason": (f"When the scanner connected, its left and right images disagreed by {dv:.2f} px in "
                            f"height (want under {LAUNCH_CHECK_PX:.1f} px): the cameras may have moved a little."),
                 "action": _action("check"),
                 "then": _action("stereo", "If the check does not pass, redo the camera calibration"),
                 "kind": "launch_rows", "value": dv}, False)
    if state == "ok":
        return None, not (dv is not None and dv >= LAUNCH_CHECK_PX)
    if state != "drift" or answered:
        return None, False  # unknown, running, or answered by a 2-minute check after it
    rows = f" by {dv:.2f} px" if dv is not None else ""
    return ({"source": "launch_check", "level": 2, "part": "cameras",
             "reason": (f"When the scanner connected, its left and right images disagreed{rows} in height (want under "
                        "0.6 px): the cameras may have moved."),
             "action": _action("check", "Run the 2-minute check to confirm"),
             "then": _action("stereo", "If the check does not pass, redo the camera calibration"),
             "kind": "launch_drift", "value": dv}, False)


def _board_findings(last: dict | None, saved: datetime | None, now: datetime,
                    max_age_days: float) -> tuple[list[dict], datetime | None, datetime | None]:
    """``(findings, when a check passed after the last save or None, when a check after the last save
    found the cameras' rows in order or None)`` (rule 4). A failed check whose parts are known and
    leave out the cameras still found the rows in order."""
    out: list[dict] = []
    when = _time(last.get("when")) if isinstance(last, dict) else None
    current = when is not None and (saved is None or when >= saved)
    passed = when if current and last.get("ok") else None
    rows_ok = passed
    if current and last.get("ok") is False:
        parts = [p for p in (last.get("parts") or []) if p in PARTS]
        if isinstance(last.get("parts"), list) and "cameras" not in parts:
            rows_ok = when
        verdict = str(last.get("verdict") or "it found the calibration off").strip().rstrip(".")
        part = min(parts, key=lambda p: _PART_RANK[p]) if parts else None
        act = (_action(_PART_STEP[part]) if part else
               _action("check", "Run the 2-minute check again and follow what it says"))
        out.append({"source": "board_check", "level": 2, "part": part,
                    "reason": f"The last 2-minute check did not pass ({_ago(when, now)}): {verdict}.",
                    "action": act, "then": None, "kind": "board_check_failed", "value": parts or None})
    # its age: from the later of the last passed check and the last calibration step
    fresh = max([t for t in (passed, saved) if t is not None], default=None)
    if fresh is not None:
        days = (now - fresh).total_seconds() / 86400.0
        if days > max_age_days:
            what = (f"The last 2-minute check was {int(days)} days ago" if passed is not None and fresh == passed
                    else f"The calibration has not been checked since it was saved {int(days)} days ago")
            out.append({"source": "board_check_age", "level": 1, "part": None,
                        "reason": f"{what}; a check every {max_age_days:g} days or so catches a slow drift early.",
                        "action": _action("check"), "then": None, "kind": "age", "value": round(days, 1)})
    return out, passed, rows_ok


def _answered(reading: dict, scan_time: datetime | None, passed: datetime | None) -> dict:
    """A row-error "check" from a scan made before a 2-minute check that passed is answered by it (rule 2)."""
    cam = reading["parts"].get("cameras")
    if (passed is None or cam is None or cam["kind"] != "rows" or cam["level"] != 1 or scan_time is None
            or scan_time > passed):
        return reading
    return dict(reading, parts=dict(reading["parts"], cameras=dict(cam, level=0, answered=True)))


def _join(findings: list[dict], levels: dict[str, int | None], valid_reason: str, note: str | None,
          extra: dict) -> dict:
    findings = sorted(findings, key=lambda f: (-f["level"], _PART_RANK[f["part"]], _SOURCE_RANK.get(f["source"], 9)))
    top = findings[0] if findings else None
    parts_state: dict[str, str | None] = {}
    for p in PARTS:
        lv = levels.get(p)
        for f in findings:
            if f["part"] == p:
                lv = max(lv or 0, f["level"])
        parts_state[p] = None if lv is None else STATES[lv]
    state = STATES[top["level"]] if top else "valid"
    out = {
        "version": STATUS_VERSION, "state": state, "label": STATE_LABELS[state],
        "part": top["part"] if top else None,
        "part_label": PART_LABELS.get(top["part"]) if top and top["part"] else None,
        "reason": top["reason"] if top else valid_reason,
        "action": top["action"] if top else None, "then": top["then"] if top else None,
        "reasons": [{"state": STATES[f["level"]], **{k: f[k] for k in ("source", "part", "reason", "action", "then",
                                                                       "kind", "value")}} for f in findings],
        "parts": parts_state,
        "not_judged": [p for p in PARTS if parts_state[p] is None],
        "note": note,
        "provisional": True,
    }
    out.update(extra)
    return out


def _cpu_note(readings: list[dict]) -> str | None:
    """Why the lasers and the IMU were not judged, when no scan could judge them: processed on the CPU
    path (``CPU_PATH``), or too short (:data:`MIN_FRAMES`). Not judged is neither stale nor fine."""
    if not readings or any(r["gpu"] and not r["short"] for r in readings):
        return None
    one = len(readings) == 1
    if not any(r["gpu"] for r in readings):
        what = "This scan was" if one else "These scans were"
        return (f"{what} processed without a GPU, so only the cameras could be judged: the lasers and the IMU cannot "
                "be judged on this computer.")
    if one:
        what = f"This scan is short ({readings[0]['frames']} frames)"
    else:
        what = "These scans are short"
    return (f"{what}, so only the cameras could be judged: the lasers and the IMU need a scan of at least "
            f"{MIN_FRAMES} processed frames.")


def scan_status(block: dict | None, *, tracking_model: str | None = None, lines: Sequence[str] | None = None,
                frames: int | None = None) -> dict:
    """The status of one scan, from its own health block alone (written into its ``report.json`` as
    ``health_status``): what this scan saw, with no launch check, board check or other scan, so one
    scan is enough here. The same shape as :func:`status`, plus ``signs`` (what each kept sign read)."""
    r = read_scan(block, tracking_model=tracking_model, lines=lines, frames=frames)
    findings, levels, _ = _from_scans([({}, r)], 1, 1)
    judged = [PART_LABELS[p] if p == "imu" else PART_LABELS[p].lower() for p in PARTS if levels[p] is not None]
    if judged:
        valid = f"This scan shows nothing off for the {' and the '.join(judged)}."
    else:
        valid = "This scan has too little to judge the calibration by."
    note = _cpu_note([r])
    return _join(findings, levels, valid, note, {"source": "scan", "signs": r["signs"]})


def status(*, saved: Any = None, launch_check: dict | None = None, last_check: dict | None = None,
           scans: Sequence[dict] = (), now: Any = None, max_age_days: float = BOARD_CHECK_MAX_AGE_DAYS,
           window: int = SCANS_WINDOW, agree: int = SCANS_AGREE) -> dict:
    """The calibration's status (module docstring, rules 1-5), for the setup state.

    ``saved``: when the calibration was last saved (its newest step; ISO text or datetime): checks
    from before it judged another calibration and do not count. ``launch_check``: the setup state's
    (``{state, dv_px, matches, message, when}``). ``last_check``: the last 2-minute check
    (``{when, ok, verdict, parts?}``). ``scans``: the scans made with this calibration, each
    ``{"health": block, "tracking_model", "lines", "when", "scan"}`` (:func:`scan_item`), in any
    order (sorted here by ``when``, newest first). ``now``: the time to measure ages against.

    Returns ``{version, state ("valid" | "check" | "recalibrate"), label, part ("cameras" | "lasers"
    | "imu" | None: the calibration as a whole, or nothing), part_label, reason (one sentence),
    action ({step, text} or None), then (what to try if that does not mend it, or None), reasons
    (every finding that is not valid, worst first), parts ({part: state, or None when nothing judged
    it}), not_judged, note (why something could not be judged, or None), provisional, source, scans
    ({used, window, agree})}``."""
    now_t = _time(now) or datetime.now(timezone.utc)
    saved_t = _time(saved)
    board, passed, rows_ok = _board_findings(last_check, saved_t, now_t, max_age_days)
    launch, launch_ok = _launch_finding(launch_check, saved_t, rows_ok)
    items = sorted((s for s in scans if isinstance(s, dict)),
                   key=lambda s: _time(s.get("when")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    readings = []
    for it in items:
        r = read_scan(it.get("health"), tracking_model=it.get("tracking_model"), lines=it.get("lines"),
                      frames=it.get("frames"))
        readings.append((it, _answered(r, _time(it.get("when")), rows_ok)))
    scan_f, levels, used = _from_scans(readings, window, agree)
    findings = board + scan_f + ([launch] if launch else [])
    if (launch_ok or rows_ok is not None) and levels["cameras"] is None:
        levels["cameras"] = 0
    evidence = []
    if launch_ok:
        evidence.append(f"the launch check ({_ago(_time(launch_check.get('when')), now_t)})")
    if used:
        evidence.append("the last scan" if used == 1 else f"the last {used} scans")
    if passed is not None:
        evidence.append(f"the 2-minute check ({_ago(passed, now_t)})")
    judged = [p for p in PARTS if levels[p] is not None or any(f["part"] == p for f in findings)]
    if evidence:
        joined = evidence[0] if len(evidence) == 1 else ", ".join(evidence[:-1]) + " and " + evidence[-1]
        if len(judged) == len(PARTS) or not judged:
            valid = f"Nothing looks off: {joined} found the calibration in order."
        else:  # say which parts were looked at, never that the others are fine
            names = " and the ".join("IMU" if p == "imu" else p for p in judged)
            valid = f"Nothing looks off for the {names}: {joined} found nothing wrong."
    else:
        valid = "Nothing speaks against the calibration yet: no check or scan has looked at it since it was saved."
    note = _cpu_note([r for _, r in readings[:window]])
    return _join(findings, levels, valid, note,
                 {"source": "setup", "scans": {"used": used, "window": window, "agree": agree}})


# --------------------------------------------------------------------------- what is kept of a scan
def trim_block(block: dict | None) -> dict | None:
    """The part of a health block the status reads (the kept signs and which calibration), small
    enough to keep the last scans in ``setup.json``. None for no block or a failed one."""
    if not isinstance(block, dict) or "error" in block:
        return None
    per_line = _at(block, ("laser_mesh", "per_line"))
    per_line = per_line if isinstance(per_line, dict) else {}
    return {
        "calibration": {"id": _at(block, ("calibration", "id"))},
        "row_error": {"median_px": _at(block, ("row_error", "median_px"))},
        "laser_mesh": {"per_line": {ln: {"median_mm": _at(s, ("median_mm",))} for ln, s in per_line.items()}},
        "tracking": {"gyro_jump_deg": {"median": _at(block, ("tracking", "gyro_jump_deg", "median"))}},
    }


def lines_of_report(report: dict) -> list[str]:
    """The laser lines a processed scan should have: the calibration's planes when its lasers were on."""
    if _at(report, ("params", "pipeline", "use_lasers")) is False:
        return []
    planes = _at(report, ("lasers", "planes"))
    return sorted(str(p) for p in planes) if isinstance(planes, list) else []


def scan_item(report: dict, *, when: str | None = None, scan: dict | None = None,
              calibration: str | None = None) -> dict | None:
    """One scan as :func:`status` takes it, from its processing report; None without a usable block.
    ``calibration``: :func:`calibration_key` of the calibration it was processed with."""
    block = trim_block(report.get("health") if isinstance(report, dict) else None)
    if block is None:
        return None
    return {"health": block, "tracking_model": _at(report, ("params", "compute", "tracking_model")),
            "lines": lines_of_report(report), "frames": _at(report, ("frames", "processed")), "when": when,
            "scan": scan, "calibration": calibration}


def _rounded(x: Any) -> Any:
    if isinstance(x, float):
        return float(f"{x:.9g}")
    if isinstance(x, dict):
        return {k: _rounded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_rounded(v) for v in x]
    return x


def calibration_key(calib: Any) -> str | None:
    """Which calibration this is, to match scans to it: its ``id``, or for a file from before the
    ids a short hash of its numbers (rounded to 9 digits, so a copy read back matches). None for none."""
    if calib is None:
        return None
    cid = getattr(calib, "id", None)
    if cid:
        return str(cid)
    d = calib.to_dict()
    for k in ("id", "created", "temperature_c", "steps"):
        d.pop(k, None)
    text = json.dumps(_rounded(d), sort_keys=True)
    return "content-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
