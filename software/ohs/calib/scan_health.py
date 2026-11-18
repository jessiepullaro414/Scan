"""What a scan saw of its calibration: the ``health`` block of ``report.json`` (FEATURE_PLAN H1).

A calibration goes stale with a knock, a loose screw or the heat. A scan cannot tell that by
itself, but several of its own numbers move when it happens. :func:`ohs.app.process.process_recording`
writes them down while it processes, so the live scan pays nothing, and a later step (H2, H3)
decides which of them to trust. The block (all lengths mm, angles degrees, pixels full-resolution):

* ``calibration``: which calibration the scan was processed with (``id``, ``created``,
  ``temperature_c``, ``steps``: the optional keys of :class:`~ohs.types.ScannerCalibration`, None
  for a file from before them) and ``source`` (``"recording"``: its own copy, or ``"argument"``).
* ``temperature_c``: the IMU chip during the scan (``scan``: the recording's ``meta.json`` summary,
  ``first`` / ``last`` / ``min`` / ``max`` / ``n``), at the calibration, and ``change`` (the scan's
  ``min`` and ``max`` minus the calibration's).
* ``row_error``: left and right features of a rectified pair should lie on the same row
  (:func:`ohs.calib.drift.row_error`); measured on every ``every``-th processed frame (counted from
  the first), in a thread beside the processing. ``median_px`` / ``p90_px`` of ``|dv|`` and the
  signed median ``signed_px`` over all matches, and per measured frame.
* ``laser_depth``: per laser line, the signed distance in depth between its points and the dense
  stereo depth, before the depth check drops any (:func:`ohs.vision.laser.depth_gap`; + = the
  laser point lies farther): ``points`` measured, ``with_depth`` (those with dense depth around
  them), ``median_mm``, ``abs_median_mm``, ``p05_mm`` / ``p95_mm``, ``frames`` and the depth
  check's ``rejected`` count (``lasers.n_rejected_by_depth``). The two measure the same surface
  through different parts of the calibration (camera pair against laser plane).
* ``laser_mesh``: the laser cloud against the finished mesh, ``all`` (the report of
  :func:`ohs.mesh.anchor.laser_agreement`) and ``per_line``
  (:func:`ohs.mesh.anchor.laser_agreement_by_line`).
* ``tracking``: the tracked frames' ICP residual (``rmse_mm``) and, where the gyro gave the
  prior, how far the tracker's rotation ended from the gyro's (``gyro_jump_deg``, the
  ``jump_deg`` of each ``per_frame`` row): each ``{n, median, p95, max}``.
* ``straighten``: what Straighten had to correct (:func:`straighten_summary` of
  ``straighten.json``); None here, since Straighten is a later stage: the server fills it in.
* ``ms``: what the block cost the processing (the main thread's share).
"""
from __future__ import annotations

import logging
import math
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import numpy as np

from ohs.types import ScannerCalibration

__all__ = ["HEALTH_VERSION", "ROW_ERROR_EVERY", "ScanHealth", "straighten_summary", "calibration_info"]

log = logging.getLogger(__name__)

HEALTH_VERSION = 1
ROW_ERROR_EVERY = 30  # processed frames between two row-error measurements
ROW_ERROR_MAX_DV_HALF = 8.0  # the gross-mismatch limit of the launch check (px at half resolution)


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None or not math.isfinite(float(x)) else round(float(x), nd)


def _summary(values: list[float] | np.ndarray) -> dict:
    a = np.asarray(values, dtype=np.float64)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return {"n": 0}
    return {"n": int(a.size), "median": _r(np.median(a)), "p95": _r(np.percentile(a, 95)), "max": _r(a.max())}


def calibration_info(calib: ScannerCalibration, source: str) -> dict:
    """``health["calibration"]``: which calibration a scan was processed with."""
    return {"id": calib.id, "created": calib.created, "temperature_c": calib.temperature_c,
            "steps": calib.steps, "source": source}


def _temperatures(meta: dict, calib: ScannerCalibration) -> dict:
    t = meta.get("temperature_c")
    scan = None
    if isinstance(t, dict) and all(isinstance(t.get(k), (int, float)) for k in ("first", "last", "min", "max")):
        scan = {k: t[k] for k in ("first", "last", "min", "max")}
        scan["n"] = int(t.get("n") or 0)
    cal = calib.temperature_c
    change = None
    if scan is not None and cal is not None:
        change = {"min": _r(scan["min"] - cal, 2), "max": _r(scan["max"] - cal, 2)}
    return {"scan": scan, "calibration": cal, "change": change}


def straighten_summary(rep: dict | None) -> dict | None:
    """What Straighten had to correct, from its ``straighten.json`` (``ohs.track.posegraph``, with
    the laser crossings' ``laser_curves``): the reason and whether the poses changed, the loops,
    the largest change of a camera position / orientation, the loops' residual before and after,
    the drift scale, and the crossings' miss and move. None for no report."""
    if not isinstance(rep, dict):
        return None
    res = rep.get("residual_mm") if isinstance(rep.get("residual_mm"), dict) else None
    lc = rep.get("laser_curves") if isinstance(rep.get("laser_curves"), dict) else None
    return {
        "reason": rep.get("reason"), "changed": rep.get("changed"),
        "keyframes": rep.get("keyframes"), "loop_candidates": rep.get("loop_candidates"),
        "loops_accepted": rep.get("loops_accepted"),
        "correction_mm": rep.get("correction_mm"), "correction_deg": rep.get("correction_deg"),
        "residual_mm": None if res is None else {"before_max": res.get("before_max"), "after_max": res.get("after_max")},
        "drift_scale": rep.get("drift_scale"),
        "laser_curves": None if lc is None else {
            "changed": lc.get("changed"), "reason": lc.get("reason"), "crossings": lc.get("crossings"),
            "miss_mm": lc.get("miss_mm"), "moved_mm": lc.get("moved_mm"), "moved_deg": lc.get("moved_deg")},
    }


def _row_error_job(left_r: np.ndarray, right_r: np.ndarray, band: tuple[float, float], max_dv: float,
                   to_full: float) -> np.ndarray:
    from ohs.calib.drift import row_error  # noqa: PLC0415

    dv, _ = row_error(left_r, right_r, max_dv_px=max_dv, min_disparity=band[0], max_disparity=band[1])
    return dv * to_full


class ScanHealth:
    """Collects the health block while a :class:`~ohs.app.pipeline.FramePipeline` processes a scan.

    Made before the first frame (it switches on the pipeline's laser depth gaps), fed every frame
    result with :meth:`frame`, finished with :meth:`block`; :meth:`close` always. Reads only: the
    pipeline's results are the same without it.
    """

    def __init__(self, pipe: Any, every: int = ROW_ERROR_EVERY) -> None:
        from ohs.calib.drift import disparity_band  # noqa: PLC0415

        if int(every) < 1:
            raise ValueError("row_error_every must be >= 1")
        self.every = int(every)
        self.pipe = pipe
        rect = pipe.rectifier
        self._to_full = float(pipe.calib.stereo.image_size[0]) / float(rect.image_size[0])
        self._band = disparity_band(rect)
        self._max_dv = ROW_ERROR_MAX_DV_HALF * 2.0 / self._to_full
        self._pool: ThreadPoolExecutor | None = None
        self._rows: list[tuple[int, Future]] = []
        self._ms = 0.0
        if pipe.lasers_enabled:
            pipe.laser_depth_gaps = {}

    def frame(self, k: int, res: Any) -> None:
        """The ``k``-th processed frame's result (``k`` from 0)."""
        if k % self.every:
            return
        t0 = time.perf_counter()
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ohs-health")
        self._rows.append((int(res.index), self._pool.submit(_row_error_job, res.left_r, res.right_r, self._band,
                                                             self._max_dv, self._to_full)))
        self._ms += 1e3 * (time.perf_counter() - t0)

    def _row_error(self) -> dict:
        per, pooled = [], []
        for index, fut in self._rows:
            try:
                dv = fut.result()
            except Exception as exc:  # noqa: BLE001 - a measurement must not stop the scan
                log.warning("row error of frame %d failed: %s", index, exc)
                dv = np.zeros(0)
            pooled.append(dv)
            per.append({"index": index, "matches": int(len(dv)),
                        "median_px": _r(np.median(np.abs(dv))) if len(dv) else None,
                        "signed_px": _r(np.median(dv)) if len(dv) else None})
        dv = np.concatenate(pooled) if pooled else np.zeros(0)
        a = np.abs(dv)
        return {"every": self.every, "frames": len(per), "matches": int(len(dv)),
                "median_px": _r(np.median(a)) if len(a) else None, "p90_px": _r(np.percentile(a, 90)) if len(a) else None,
                "signed_px": _r(np.median(dv)) if len(dv) else None, "per_frame": per}

    def _laser_depth(self) -> dict:
        per: dict[str, dict] = {}
        gaps = self.pipe.laser_depth_gaps or {}
        rejected = self.pipe.laser_rejected
        for name in sorted(set(gaps) | set(rejected)):
            arrs = gaps.get(name) or []
            g = np.concatenate(arrs) if arrs else np.zeros(0, np.float32)
            ok = g[np.isfinite(g)].astype(np.float64)
            s: dict[str, Any] = {"frames": len(arrs), "points": int(len(g)), "with_depth": int(len(ok)),
                                 "median_mm": None, "abs_median_mm": None, "p05_mm": None, "p95_mm": None,
                                 "rejected": int(rejected.get(name, 0))}
            if len(ok):
                p05, p50, p95 = np.percentile(ok, [5, 50, 95])
                s.update(median_mm=_r(p50), abs_median_mm=_r(np.median(np.abs(ok))), p05_mm=_r(p05), p95_mm=_r(p95))
            per[name] = s
        return {"sign": "+ = the laser point lies farther from the camera than the stereo depth", "per_line": per}

    def _laser_mesh(self, V: np.ndarray, F: np.ndarray, N: np.ndarray | None, laser_pts: np.ndarray,
                    voxel_mm: float) -> dict:
        from ohs.mesh.anchor import laser_agreement, laser_agreement_by_line  # noqa: PLC0415

        Nv = N if N is not None and len(N) == len(V) else None
        _, all_rep = laser_agreement(V, F, laser_pts, N=Nv)
        cloud = self.pipe.laser_cloud()
        lines = {name: cloud.points(float(voxel_mm), names=[name]) for name in sorted(cloud.names)}
        return {"all": all_rep, "per_line": laser_agreement_by_line(V, F, lines, N=Nv)}

    @staticmethod
    def _tracking(rows: list[dict]) -> dict:
        ok = [r for r in rows if r.get("ok")]
        return {"frames": len(ok),
                "rmse_mm": _summary([r["rmse_mm"] for r in ok if isinstance(r.get("rmse_mm"), (int, float))]),
                "gyro_jump_deg": _summary([r["jump_deg"] for r in ok
                                           if r.get("gyro_prior") and isinstance(r.get("jump_deg"), (int, float))])}

    def block(self, *, calib: ScannerCalibration, calib_source: str, meta: dict, rows: list[dict],
              V: np.ndarray, F: np.ndarray, N: np.ndarray | None, laser_pts: np.ndarray, laser_voxel_mm: float) -> dict:
        """The health block (module docstring), after the last frame and the mesh."""
        t0 = time.perf_counter()
        out = {
            "version": HEALTH_VERSION,
            "calibration": calibration_info(calib, calib_source),
            "temperature_c": _temperatures(meta, calib),
            "row_error": self._row_error(),
            "laser_depth": self._laser_depth(),
            "laser_mesh": self._laser_mesh(V, F, N, laser_pts, laser_voxel_mm),
            "tracking": self._tracking(rows),
            "straighten": None,
        }
        self._ms += 1e3 * (time.perf_counter() - t0)
        out["ms"] = round(self._ms, 1)
        return out

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
            self._pool = None
        self.pipe.laser_depth_gaps = None
