"""Interactive calibration capture (``ohs calibrate stereo|lasers|imu|check``) and calibration merging.

A capture writes a recording whose ``meta.json`` has ``purpose = "calib_<kind>"``. The flows in
:mod:`ohs.app.calib_flow` turn that recording into a calibration (:func:`run_flow`), and
:func:`merge_calibration` / :func:`save_calibration` update ``calibration.json``.

* **stereo** (all emitters off): SPACE or the trigger stores the current frame as one view when
  both cameras see the board (>= 8 common ChArUco corners). The live view shows the detected
  corners on both raw images, an 8 x 8 coverage map of the views captured so far, and the board
  distance and tilt (from the old calibration, or a nominal 83 deg lens when there is none).
* **lasers** (line lasers on, dots off): needs the stereo calibration. The rectified left view
  shows the corners, the laser lines and the page outline. A pose is stored when the board pose is
  found; the HUD counts laser points on the page and warns when a line misses it.
* **imu** (all emitters off): continuous. SPACE or the trigger starts recording (every frame plus
  IMU); the HUD says HOLD STILL for 2 s, then ROTATE with a timer; SPACE or the trigger stops it
  (auto-stop after ``seconds``, default 62 s). Needs the Pico's IMU and the stereo calibration.
* **check** (line lasers on, dots off): a few views like the laser capture; F2's flow reports
  whether the calibration still holds, and nothing is saved.

Snapshot recordings (stereo, lasers, check) hold exactly the captured views, renumbered 0..N-1.
Lasers/imu/check recordings include the calibration in use as ``calibration.json``. Keys: SPACE
capture (imu: start/stop), q finish and calibrate, ESC abort, +/- exposure.
"""
from __future__ import annotations

import inspect
import json
import logging
import math
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ohs.app import live
from ohs.app.live import (
    CYAN,
    GREEN,
    GREY,
    RED,
    WHITE,
    YELLOW,
    LiveSession,
    Processed,
    draw_hud,
    fit_width,
    fmt_time,
    gray_to_bgr,
    side_by_side,
    waiting_image,
)
from ohs.types import ScannerCalibration, StereoFrame

__all__ = [
    "KINDS",
    "GUIDANCE",
    "PROFILE",
    "CoverageMap",
    "BoardView",
    "BoardProcessor",
    "CaptureSession",
    "run_capture",
    "run_flow",
    "merge_calibration",
    "save_calibration",
    "format_report",
    "write_report",
]

log = logging.getLogger(__name__)

KINDS = ("stereo", "lasers", "imu", "check")
#: Emitter profile (config ``emitters.profiles``) per capture kind.
PROFILE = {"stereo": "stereo", "lasers": "lasers", "imu": "imu", "check": "check"}
GUIDANCE = {
    "stereo": "15-25 views, 200-500 mm away, tilt the board 20-45 deg in different directions, cover the image corners and edges",
    "lasers": "6-10 poses, 150-450 mm away, tilt up to +-30 deg, both laser lines crossing the white page margin",
    "imu": "hold still 2 s, then rotate about all axes for 20-60 s, keeping the board in view",
    "check": "3-5 views at different distances, both laser lines on the page",
}
TARGET_VIEWS = {"stereo": (15, 25), "lasers": (6, 10), "check": (3, 5)}
MIN_VIEWS = {"stereo": 4, "lasers": 3, "check": 1}
MIN_STEREO_CORNERS = 8
DISTANCE_MM = {"stereo": (200.0, 500.0), "lasers": (150.0, 450.0), "check": (150.0, 450.0), "imu": (150.0, 600.0)}
IMU_STILL_S = 2.0
IMU_ROTATE_S = (20.0, 60.0)
PAGE_MARGIN_MM = 12.0  # white paper around the printed board that still counts as "on the page"
LINE_MIN_POINTS = 15
COVERAGE_GRID = 8  # same grid as ohs.calib.stereo's coverage report


# --------------------------------------------------------------------------- detection
class CoverageMap:
    """Which cells of a ``grid x grid`` image grid hold at least one captured corner."""

    def __init__(self, image_size: tuple[int, int], grid: int = COVERAGE_GRID) -> None:
        self.w, self.h = int(image_size[0]), int(image_size[1])
        self.grid = int(grid)
        self.cells = np.zeros((self.grid, self.grid), bool)

    def add(self, uv: np.ndarray | None) -> None:
        if uv is None or len(uv) == 0:
            return
        uv = np.asarray(uv, np.float64).reshape(-1, 2)
        cx = np.clip(np.floor(uv[:, 0] / self.w * self.grid), 0, self.grid - 1).astype(int)
        cy = np.clip(np.floor(uv[:, 1] / self.h * self.grid), 0, self.grid - 1).astype(int)
        self.cells[cy, cx] = True

    @property
    def fraction(self) -> float:
        return float(self.cells.mean())

    def draw(self, img: np.ndarray) -> None:
        """Tint covered cells green and draw the grid, in place (``img`` has the image size)."""
        h, w = img.shape[:2]
        overlay = img.copy()
        for j, i in zip(*np.nonzero(self.cells)):
            x0, x1 = int(i * w / self.grid), int((i + 1) * w / self.grid)
            y0, y1 = int(j * h / self.grid), int((j + 1) * h / self.grid)
            cv2.rectangle(overlay, (x0, y0), (x1 - 1, y1 - 1), GREEN, -1)
        cv2.addWeighted(overlay, 0.3, img, 0.7, 0.0, dst=img)
        for k in range(1, self.grid):
            cv2.line(img, (int(k * w / self.grid), 0), (int(k * w / self.grid), h - 1), GREY, 1)
            cv2.line(img, (0, int(k * h / self.grid)), (w - 1, int(k * h / self.grid)), GREY, 1)


@dataclass
class BoardView:
    """Board detection on one frame (pixel coordinates of ``image_l`` / ``image_r``)."""

    image_l: np.ndarray
    image_r: np.ndarray | None
    corners_l: np.ndarray
    ids_l: np.ndarray
    corners_r: np.ndarray | None = None
    ids_r: np.ndarray | None = None
    T_cb: np.ndarray | None = None
    distance_mm: float = float("nan")
    tilt_deg: float = float("nan")
    lines: Any = None  # LaserLines in image_l (lasers / check)
    line_counts: dict = field(default_factory=dict)  # points per line on the page
    page_uv: np.ndarray | None = None  # page outline polygon (4, 2)

    @property
    def common(self) -> int:
        if self.ids_r is None:
            return 0
        return int(len(np.intersect1d(self.ids_l, self.ids_r)))


def _nominal_K(w: int, h: int) -> np.ndarray:
    f = 720.0 * w / 1280.0  # the 83 deg lens (ARCHITECTURE.md section 7)
    return np.array([[f, 0.0, (w - 1) / 2.0], [0.0, f, (h - 1) / 2.0], [0.0, 0.0, 1.0]])


def _inside_convex(uv: np.ndarray, poly: np.ndarray) -> np.ndarray:
    uv = np.asarray(uv, np.float64).reshape(-1, 2)
    if len(uv) == 0 or poly is None or not np.all(np.isfinite(poly)):
        return np.zeros(len(uv), bool)
    a, b = poly, np.roll(poly, -1, axis=0)
    cross = (b[None, :, 0] - a[None, :, 0]) * (uv[:, None, 1] - a[None, :, 1]) - (b[None, :, 1] - a[None, :, 1]) * (uv[:, None, 0] - a[None, :, 0])
    return np.all(cross >= 0, axis=1) | np.all(cross <= 0, axis=1)


class BoardProcessor:
    """ChArUco detection (+ board pose, + laser lines for lasers/check) in the processing thread."""

    def __init__(self, kind: str, spec: Any, calib: ScannerCalibration | None) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown capture kind {kind!r}")
        if kind in ("lasers", "check", "imu") and calib is None:
            raise ValueError(f"`ohs calibrate {kind}` needs a stereo calibration (run `ohs calibrate stereo`)")
        self.kind, self.spec, self.calib = kind, spec, calib
        self._rect = None
        self._laser_params = None

    def start(self) -> None:
        if self.kind in ("lasers", "check"):
            from ohs.vision.laser import LaserParams
            from ohs.vision.rectify import Rectifier

            self._rect = Rectifier(self.calib.stereo, 1.0)
            self._laser_params = LaserParams()

    def _camera_model(self, img: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
        h, w = img.shape[:2]
        if self._rect is not None:
            return self._rect.K, None
        if self.calib is not None and tuple(self.calib.stereo.image_size) == (w, h):
            return self.calib.stereo.K1, self.calib.stereo.D1
        return _nominal_K(w, h), None

    def idle(self, frame: StereoFrame) -> BoardView:
        return self.process(frame)

    def process(self, frame: StereoFrame) -> BoardView:
        from ohs.calib.board import MIN_POSE_CORNERS, board_outline_uv, detect_charuco, estimate_board_pose

        img = self._rect.rectify_left(frame.left) if self._rect is not None else frame.left
        cl, il = detect_charuco(img, self.spec)
        view = BoardView(img, None, cl, il)
        if self.kind == "stereo":
            view.image_r = frame.right
            view.corners_r, view.ids_r = detect_charuco(frame.right, self.spec)
        K, D = self._camera_model(img)
        if len(il) >= MIN_POSE_CORNERS:
            ok, T = estimate_board_pose(cl, il, self.spec, K, D)
            if ok and T is not None:
                view.T_cb = T
                w_mm, h_mm = self.spec.size_mm
                centre = T[:3, :3] @ np.array([w_mm / 2, h_mm / 2, 0.0]) + T[:3, 3]
                view.distance_mm = float(np.linalg.norm(centre))
                view.tilt_deg = float(np.degrees(np.arccos(np.clip(abs(T[2, 2]), 0.0, 1.0))))
        if self.kind in ("lasers", "check"):
            from ohs.vision.laser import extract_laser_lines

            view.lines = extract_laser_lines(img, self._laser_params)
            if view.T_cb is not None:
                poly = board_outline_uv(view.T_cb, self.spec, K, margin_mm=-PAGE_MARGIN_MM)
                view.page_uv = poly
                view.line_counts = {n: int(_inside_convex(view.lines.get(n), poly).sum()) for n in ("vertical", "horizontal")}
        return view

    def add_imu(self, samples: list) -> None:
        pass

    def reset(self) -> None:
        pass

    def close(self) -> None:
        pass


def _draw_corners(img: np.ndarray, uv: np.ndarray | None, color=GREEN) -> None:
    if uv is None:
        return
    r = max(2, int(round(img.shape[1] / 320)))
    for u, v in np.asarray(uv, np.float64).reshape(-1, 2):
        cv2.circle(img, (int(round(u)), int(round(v))), r, color, max(1, r // 2), cv2.LINE_AA)


# --------------------------------------------------------------------------- the capture session
class CaptureSession(LiveSession):
    """Live calibration capture for one ``kind`` (see the module docstring)."""

    def __init__(self, cfg: Any, camera: Any, kind: str, spec: Any, out_dir: str | Path, *, calib: ScannerCalibration | None = None,
                 views: int | None = None, seconds: float | None = None, **kw: Any) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown calibration kind {kind!r}")
        if kind == "imu" and seconds is None:
            seconds = IMU_STILL_S + IMU_ROTATE_S[1]
        super().__init__(cfg, camera, calib=calib, max_seconds=seconds if kind == "imu" else None, **kw)
        self.kind = kind
        self.spec = spec
        self.out_dir = Path(out_dir)
        self.title = f"OpenHandScan calibrate {kind}"
        self.record_imu = kind == "imu"
        self.require_calib_size = kind in ("lasers", "check", "imu")
        self.profile_levels = cfg.emitters.levels(PROFILE[kind])
        self.views_target = None if views is None else int(views)
        self.n_views = 0
        self.state = "armed" if kind == "imu" else "capturing"
        self._last_capture_index: int | None = None
        self.cov: tuple[CoverageMap, CoverageMap] | None = None
        self.n_board = 0
        self.n_seen = 0
        if kind == "imu":
            self.keys_help = "SPACE/trigger: start/stop   q: finish   ESC: abort   +/- exposure"
        else:
            self.keys_help = "SPACE/trigger: capture   q: finish and calibrate   ESC: abort   +/- exposure"

    # ------------------------------------------------------------------ hooks
    def make_processor(self) -> BoardProcessor:
        return BoardProcessor(self.kind, self.spec, self.calib)

    def recording_meta(self) -> dict:
        board = {"squares_x": self.spec.squares_x, "squares_y": self.spec.squares_y, "square_mm": self.spec.square_mm,
                 "marker_mm": self.spec.marker_mm, "dictionary": self.spec.dictionary}
        return self.base_meta(f"calib_{self.kind}", board=board, guidance=GUIDANCE[self.kind])

    def on_start(self) -> None:
        if self.kind == "imu" and not self.imu_live:
            raise RuntimeError("IMU calibration needs the Pico's IMU, but the Pico reports none (imu=NONE)")
        if any(self.profile_levels):
            self.set_emitters(self.profile_levels)
            self.echo("Line lasers are ON (Class 3R): never look into the beams.")
        how = "SPACE or the trigger" if self.gui else "the trigger"
        if self.kind == "imu":
            self.echo(f"IMU calibration: {GUIDANCE['imu']}. Press {how} to start and again to stop.")
        else:
            lo, hi = TARGET_VIEWS[self.kind]
            if self.views_target is not None:
                done = f"it finishes after {self.views_target} views"
            elif self.gui:
                done = "q when done"
            else:
                done = "pass --views N to finish after N views (Ctrl-C aborts)"
            self.echo(f"{self.kind} capture: {GUIDANCE[self.kind]}. Press {how} for each view ({lo}-{hi} views); {done}.")

    def observe(self, value: Any, active: bool) -> None:
        if self.recording:
            self.n_seen += 1
            self.n_board += int(getattr(value, "T_cb", None) is not None)

    def on_action(self, source: str) -> None:
        if self.kind == "imu":
            self._imu_action()
        else:
            self._capture()

    def on_quit(self, reason: str) -> None:
        if reason == "esc":
            self.result.aborted = True
            self.finish("aborted (ESC)")
        else:
            self.finish(f"finished ({reason})")

    def on_limit(self, reason: str) -> None:
        if self.kind == "imu" and self.state == "recording":
            self.echo(f"IMU capture stopped after {self.recording_elapsed:.0f} s")
        self.state = "done"
        self.finish(reason)

    # ------------------------------------------------------------------ actions
    def _imu_action(self) -> None:
        if self.state == "armed":
            self.start_recording(self.out_dir, meta=self.recording_meta(), calib=self.calib, levels=self.profile_levels)
            self.state = "recording"
            self.echo("Recording: HOLD STILL for 2 s, then rotate about all axes (20-60 s); press again to stop.")
        elif self.state == "recording":
            t = self.recording_elapsed
            if t < IMU_STILL_S + IMU_ROTATE_S[0]:
                self.echo(f"warning: only {t:.0f} s recorded; 20-60 s of rotation give a better calibration")
            self.state = "done"
            self.finish("stopped")

    def _capture(self) -> None:
        proc = self._result
        if proc is None:
            self.flash("no camera image yet")
            return
        bv: BoardView = proc.value
        if self.kind == "stereo":
            if bv.common < MIN_STEREO_CORNERS:
                self.flash(f"not captured: the board must be seen by both cameras (>= {MIN_STEREO_CORNERS} common corners, "
                           f"now {bv.common})")
                return
        elif bv.T_cb is None:
            self.flash("not captured: board not found (whole board in view, no glare?)")
            return
        if proc.frame.index == self._last_capture_index:
            self.flash("already captured this frame: move the board to a new pose")
            return
        if self._rec is None:
            calib = None if self.kind == "stereo" else self.calib
            self.start_recording(self.out_dir, meta=self.recording_meta(), calib=calib, continuous=False)
        k = self.snapshot(proc.frame)
        self._last_capture_index = proc.frame.index
        self.n_views = k + 1
        notes = []
        if self.kind == "stereo":
            if self.cov is None:
                self.cov = (CoverageMap(bv.image_l.shape[1::-1]), CoverageMap(bv.image_r.shape[1::-1]))
            self.cov[0].add(bv.corners_l)
            self.cov[1].add(bv.corners_r)
            notes.append(f"coverage L {self.cov[0].fraction:.0%} R {self.cov[1].fraction:.0%}")
        else:
            missing = [n for n in ("vertical", "horizontal") if bv.line_counts.get(n, 0) < LINE_MIN_POINTS]
            if missing:
                notes.append(f"warning: {' and '.join(missing)} line not on the page in this view")
        lo, hi = DISTANCE_MM[self.kind]
        if math.isfinite(bv.distance_mm) and not lo <= bv.distance_mm <= hi:
            notes.append(f"board at {bv.distance_mm:.0f} mm (aim for {lo:.0f}-{hi:.0f})")
        msg = f"view {self.n_views} captured" + (": " + ", ".join(notes) if notes else "")
        self.flash(msg)
        if self.gui:
            self.echo(msg)
        if self.views_target is not None and self.n_views >= self.views_target:
            self.finish(f"{self.n_views} views captured")

    # ------------------------------------------------------------------ display
    def render(self, proc: Processed | None) -> np.ndarray | None:
        if proc is None:
            return waiting_image()
        bv: BoardView = proc.value
        if self.kind == "stereo":
            img = self._render_stereo(bv)
        else:
            img = self._render_single(bv)
        bottom = [(f"guide: {GUIDANCE[self.kind]}", CYAN)]
        msg = self.flash_text()
        if msg:
            bottom.append((msg, YELLOW))
        bottom.append((self.keys_help, GREY))
        draw_hud(img, self._hud(bv), bottom)
        return img

    def _render_stereo(self, bv: BoardView) -> np.ndarray:
        left, right = gray_to_bgr(bv.image_l), gray_to_bgr(bv.image_r)
        if self.cov is not None:
            self.cov[0].draw(left)
            self.cov[1].draw(right)
        _draw_corners(left, bv.corners_l)
        _draw_corners(right, bv.corners_r)
        return fit_width(side_by_side(left, right), 1280)

    def _render_single(self, bv: BoardView) -> np.ndarray:
        if bv.lines is not None:
            from ohs.vision.laser import draw_overlay

            img = draw_overlay(bv.image_l, bv.lines, 2)
        else:
            img = gray_to_bgr(bv.image_l)
        _draw_corners(img, bv.corners_l)
        if bv.page_uv is not None and np.all(np.isfinite(bv.page_uv)):
            cv2.polylines(img, [np.round(bv.page_uv).astype(np.int32).reshape(-1, 1, 2)], True, CYAN, 2, cv2.LINE_AA)
        return fit_width(img, 960)

    def _pose_text(self, bv: BoardView) -> tuple[str, tuple]:
        if bv.T_cb is None:
            return "board pose: not found", YELLOW
        lo, hi = DISTANCE_MM[self.kind]
        ok = lo <= bv.distance_mm <= hi
        return f"board {bv.distance_mm:.0f} mm (want {lo:.0f}-{hi:.0f})  tilt {bv.tilt_deg:.0f} deg", GREEN if ok else YELLOW

    def _hud(self, bv: BoardView) -> list[tuple[str, tuple]]:
        if self.kind == "stereo":
            lo, hi = TARGET_VIEWS["stereo"]
            cov = "" if self.cov is None else f"  coverage L {self.cov[0].fraction:.0%} R {self.cov[1].fraction:.0%} (want > 60%)"
            return [(f"STEREO CALIBRATION  views {self.n_views} (aim for {lo}-{hi}){cov}", WHITE),
                    (f"corners L {len(bv.ids_l)}  R {0 if bv.ids_r is None else len(bv.ids_r)}  common {bv.common}",
                     GREEN if bv.common >= MIN_STEREO_CORNERS else YELLOW),
                    self._pose_text(bv)]
        if self.kind == "imu":
            return self._imu_hud(bv)
        lo, hi = TARGET_VIEWS[self.kind]
        name = "LASER CALIBRATION" if self.kind == "lasers" else "CALIBRATION CHECK"
        counts = bv.line_counts or {}
        nv, nh = counts.get("vertical", 0), counts.get("horizontal", 0)
        lines_ok = nv >= LINE_MIN_POINTS and nh >= LINE_MIN_POINTS
        return [(f"{name}  views {self.n_views} (aim for {lo}-{hi})", WHITE),
                self._pose_text(bv),
                (f"laser points on the page: vertical {nv}  horizontal {nh}", GREEN if lines_ok else YELLOW),
                (live.emitter_text(self.levels, self.pico is not None), WHITE)]

    def _imu_hud(self, bv: BoardView) -> list[tuple[str, tuple]]:
        out: list[tuple[str, tuple]] = []
        if self.state == "armed":
            out.append(("IMU CALIBRATION: press SPACE or the trigger to start, then HOLD STILL", YELLOW))
        else:
            t = self.recording_elapsed
            if t < IMU_STILL_S:
                out.append((f"HOLD STILL  {IMU_STILL_S - t:.1f} s", YELLOW))
            else:
                r = t - IMU_STILL_S
                done = r >= IMU_ROTATE_S[0]
                out.append((f"ROTATE about all axes  {r:.0f} s  (20-60 s, then SPACE/trigger)", GREEN if done else WHITE))
            out.append((f"REC {fmt_time(t)}  {self.n_recorded} frames", RED))
        w = 0.0 if self.last_imu is None else float(np.degrees(np.linalg.norm(self.last_imu.gyro)))
        seen = "" if not self.n_seen else f"  (board in {self.n_board / self.n_seen:.0%} of frames)"
        out.append((f"gyro {w:.0f} deg/s   board {'seen' if bv.T_cb is not None else 'NOT seen'}{seen}",
                    GREEN if bv.T_cb is not None else YELLOW))
        return out

    def status_text(self) -> str:
        proc = self._result
        bv = proc.value if proc is not None else None
        if self.kind == "imu":
            state = "ready" if self.state == "armed" else f"REC {fmt_time(self.recording_elapsed)} {self.n_recorded} frames"
            return f"imu: {state}, board {'seen' if bv is not None and bv.T_cb is not None else 'not seen'}"
        if bv is None:
            return f"{self.kind}: {self.n_views} views, waiting for the camera"
        board = f"common corners {bv.common}" if self.kind == "stereo" else ("board found" if bv.T_cb is not None else "no board")
        return f"{self.kind}: {self.n_views} views captured, {board}"


# --------------------------------------------------------------------------- entry points
def run_capture(cfg: Any, kind: str, out_dir: str | Path, *, spec: Any, calib: ScannerCalibration | None = None,
                use_pico: bool = True, gui: bool = True, views: int | None = None, seconds: float | None = None,
                echo: Any = print) -> live.SessionResult:
    """Run the interactive capture; the result's ``recording`` is the capture recording (or None)."""
    if kind in ("lasers", "imu", "check") and calib is None:
        raise ValueError(f"`ohs calibrate {kind}` needs a stereo calibration first (run `ohs calibrate stereo`)")
    if kind == "imu" and not use_pico:
        raise ValueError("IMU calibration needs the Pico's IMU: drop --no-pico")
    if kind in ("lasers", "check") and not use_pico:
        echo("warning: without the Pico the line lasers stay off; they must be switched on some other way")
    live.check_output_dir(out_dir)
    camera = live.make_camera(cfg)
    pico = live.make_pico(cfg) if use_pico else None
    s = CaptureSession(cfg, camera, kind, spec, out_dir, pico=pico, calib=calib, gui=gui, views=views, seconds=seconds, echo=echo)
    res = s.run()
    res.stats["views"] = s.n_views if kind != "imu" else res.n_frames
    return res


def _call(fn: Any, *args: Any, **optional: Any) -> Any:
    """Call ``fn(*args)`` plus those keyword arguments of ``optional`` that ``fn`` accepts."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins
        params = {}
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        kw = optional
    else:
        kw = {k: v for k, v in optional.items() if k in params}
    return fn(*args, **kw)


def run_flow(kind: str, rec_path: str | Path, spec: Any, calib: ScannerCalibration | None = None, step: int = 1, *,
             paper: str = "letter", progress: Any = None) -> tuple[Any, dict]:
    """Call the matching ``ohs.app.calib_flow`` function: ``(result, report)`` (``check``: ``(None, report)``).

    ``step``, ``paper`` (the printed paper size: the laser fit uses the page margin) and
    ``progress`` are passed when the flow accepts them.
    """
    if kind == "stereo":
        from ohs.app.calib_flow import stereo_from_recording

        return _call(stereo_from_recording, rec_path, spec, step=step, progress=progress)
    if calib is None:
        raise ValueError(f"`ohs calibrate {kind}` needs a stereo calibration first (run `ohs calibrate stereo`)")
    if kind == "lasers":
        from ohs.app.calib_flow import lasers_from_recording

        return _call(lasers_from_recording, rec_path, calib, spec, step=step, paper=paper, progress=progress)
    if kind == "imu":
        from ohs.app.calib_flow import imu_from_recording

        return _call(imu_from_recording, rec_path, calib, spec, step=step, progress=progress)
    if kind == "check":
        from ohs.app.calib_flow import check_from_recording

        return None, _call(check_from_recording, rec_path, calib, spec, step=step, paper=paper, progress=progress)
    raise ValueError(f"unknown calibration kind {kind!r}")


def merge_calibration(existing: ScannerCalibration | None, kind: str, value: Any, keep: bool = False) -> tuple[ScannerCalibration, list[str]]:
    """New calibration after a ``kind`` result, plus notes for the user.

    stereo: replaces the stereo part; the laser planes and the IMU calibration are dropped (they
    are expressed in the old rectified frame) unless ``keep``. lasers: the fitted planes replace
    the ones with the same name; a plane that was not fitted keeps its old value. imu: replaces
    the IMU calibration.
    """
    notes: list[str] = []
    if kind == "stereo":
        lasers: dict = {}
        imu = None
        if existing is not None and (existing.lasers or existing.imu is not None):
            parts = (["laser planes"] if existing.lasers else []) + (["IMU calibration"] if existing.imu is not None else [])
            if keep:
                lasers, imu = dict(existing.lasers), existing.imu
                notes.append(f"kept the {' and the '.join(parts)} (--keep); verify them with `ohs calibrate check`")
            else:
                notes.append(f"a new stereo calibration invalidates the {' and the '.join(parts)}: dropped; re-run "
                             "`ohs calibrate lasers` and `ohs calibrate imu` (or pass --keep)")
        return ScannerCalibration(stereo=value, lasers=lasers, imu=imu), notes
    if existing is None:
        raise ValueError(f"`ohs calibrate {kind}` needs a stereo calibration first (run `ohs calibrate stereo`)")
    if kind == "lasers":
        planes = dict(existing.lasers)
        planes.update(value or {})
        for name in ("vertical", "horizontal"):
            if name not in (value or {}):
                notes.append(f"the {name} plane was not fitted: " + ("kept the previous one" if name in existing.lasers
                                                                       else "there is no plane for it"))
        return ScannerCalibration(existing.stereo, planes, existing.imu), notes
    if kind == "imu":
        return ScannerCalibration(existing.stereo, dict(existing.lasers), value), notes
    raise ValueError(f"cannot merge a {kind!r} result into a calibration")


def save_calibration(calib: ScannerCalibration, path: str | Path) -> Path | None:
    """Write ``calib`` to ``path`` atomically; returns the backup of the previous file (``.bak``) if any."""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if p.exists():
        backup = p.with_name(p.name + ".bak")
        shutil.copy2(p, backup)
    tmp = p.with_name(p.name + ".tmp")
    calib.save(tmp)
    os.replace(tmp, p)
    return backup


# --------------------------------------------------------------------------- reports
def _json_default(o: Any) -> Any:
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, Path):
        return str(o)
    if hasattr(o, "to_dict"):
        return o.to_dict()
    return str(o)


def _fmt(v: Any) -> str:
    if isinstance(v, (bool, np.bool_)):
        return "yes" if v else "no"
    if isinstance(v, (float, np.floating)):
        return f"{float(v):.4g}"
    if isinstance(v, np.ndarray):
        v = v.tolist()
    if isinstance(v, (list, tuple)):
        items = [_fmt(x) for x in v[:6]]
        return "[" + ", ".join(items) + (", ..." if len(v) > 6 else "") + "]"
    return str(v)


def format_report(report: dict, max_items: int = 6) -> str:
    """Readable multi-line text of a (nested) report dict; long lists of rows are summarised."""
    lines: list[str] = []
    first = [k for k in ("verdict", "quality_ok", "ok", "rms") if k in report]

    def walk(d: dict, depth: int, order: list) -> None:
        pad = "  " * depth
        for k in order + [k for k in d if k not in order]:
            v = d[k]
            if isinstance(v, dict):
                lines.append(f"{pad}{k}:")
                walk(v, depth + 1, [])
            elif isinstance(v, (list, tuple)) and v and isinstance(v[0], dict):
                if len(v) > max_items:
                    lines.append(f"{pad}{k}: {len(v)} entries (see the JSON report)")
                else:
                    lines.append(f"{pad}{k}:")
                    for row in v:
                        lines.append(f"{pad}  - " + ", ".join(f"{rk}={_fmt(rv)}" for rk, rv in row.items()))
            else:
                lines.append(f"{pad}{k}: {_fmt(v)}")

    walk(report or {}, 1, first)
    return "\n".join(lines)


def write_report(report: dict, path: str | Path) -> Path | None:
    """Save the full report as JSON (``None`` if the folder is not writable)."""
    p = Path(path)
    try:
        p.write_text(json.dumps(report, indent=2, default=_json_default), encoding="utf-8")
    except OSError as e:
        log.warning("could not write the report %s: %s", p, e)
        return None
    return p
