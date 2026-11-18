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
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["PARTS", "Limit", "LIMITS", "CPU_PATH", "DROPPED", "laser_family", "values"]

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
