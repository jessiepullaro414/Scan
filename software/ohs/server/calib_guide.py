"""Live board detection and capture guidance for the calibration screens (APP_API.md, M2).

:class:`BoardDetector` turns one frame into a :class:`Detection`, per calibration kind:

* ``stereo``: ChArUco corners on the raw left and right images. Distance and tilt come from the
  board pose on the raw left image (the target's own lens model when its image size matches,
  else a nominal 83 deg lens, like ``ohs calibrate``).
* ``lasers`` / ``check``: the left image rectified with the *target's* stereo calibration, the
  board pose (``K_rect``, no distortion), both laser lines, the page outline (the paper of
  :func:`ohs.calib.board.charuco_page_layout`) and, per line, the number of laser points on the
  **blank page margin**: back-projected onto the board plane and inside
  :meth:`CharucoPageLayout.margin_mask` (5 mm clear of ink and of the paper edge), exactly the
  points the laser-plane fit uses.
* ``imu``: the board pose on the rectified left image.

Capture rules (all in **frame time**, ``t_host``, never wall time, so a simulator on a fast
virtual clock behaves like a real camera):

* **usable**: stereo needs >= 8 corners seen by both cameras; lasers / check need a board pose and
  >= 15 margin points on each laser line;
* **steady** (:class:`SteadyTracker`): the corners moved less than 1 px (90th percentile of the
  common corners, both cameras for stereo) between processed frames, for 0.5 s;
* **new** (:func:`novelty`): compared with every captured view, the board centre moved more than
  12 % of the image width, or its distance changed more than 10 %, or its normal turned more
  than 8 deg.

Coverage (:func:`coverage`) counts captured views per cell of the engine's 8 x 8 image grid,
per camera. Diversity (:func:`diversity`) bins the captured views: ``flat`` (tilt < 15 deg) or the
tilt direction, i.e. where the printed face points (``left`` = turned toward the image's left
side, ``up`` = tipped back, its top edge farther away), and near / mid / far per kind.
:func:`next_hint` says what to do next: stereo fills the 3 x 3 image regions (emptiest first),
then the missing tilt directions, then the distances; lasers first fixes a line that misses the
margin, then distances, then tilts; check only asks for distances (and the lines).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

__all__ = ["Detection", "BoardDetector", "SteadyTracker", "novelty", "usable_reason", "coverage", "diversity",
           "next_hint", "region_of", "finish_label", "MIN_COMMON", "LINE_MIN_POINTS", "STEADY_PX", "STEADY_S",
           "COOLDOWN_S", "DISTANCE_RANGE", "DISTANCE_BINS", "TILT_NAMES", "GRID"]

log = logging.getLogger(__name__)

GRID = 8  # the engine's coverage grid (ohs.calib.stereo.COVERAGE_GRID)
MIN_COMMON = 8  # stereo: corners seen by both cameras (ohs.app.capture.MIN_STEREO_CORNERS)
LINE_MIN_POINTS = 15  # lasers / check: margin points per laser line (ohs.app.capture.LINE_MIN_POINTS)
MARGIN_CLEARANCE_MM = 5.0  # the laser fit's margin clearance (calib_flow lasers_from_recording margin_mm)
STEADY_PX = 1.0
STEADY_S = 0.5
COOLDOWN_S = 1.0
NEW_SHIFT_FRAC = 0.12
NEW_DIST_FRAC = 0.10
NEW_TILT_DEG = 8.0
FLAT_DEG = 15.0
TILT_NAMES = ("left", "right", "up", "down")
DISTANCE_RANGE = {"stereo": (200.0, 500.0), "lasers": (150.0, 450.0), "check": (150.0, 450.0), "imu": (150.0, 600.0)}
DISTANCE_BINS = {"stereo": (300.0, 400.0), "lasers": (220.0, 330.0), "check": (220.0, 330.0)}  # near < a <= mid < b <= far
# 3 x 3 regions (col, row) in the order a tie is broken: the centre first, corners, then edges
REGION_ORDER = ((1, 1), (0, 0), (2, 0), (0, 2), (2, 2), (1, 0), (1, 2), (0, 1), (2, 1))
REGION_NAMES = {(1, 1): "the middle", (0, 0): "the top-left corner", (2, 0): "the top-right corner",
                (0, 2): "the bottom-left corner", (2, 2): "the bottom-right corner", (1, 0): "the top edge",
                (1, 2): "the bottom edge", (0, 1): "the left edge", (2, 1): "the right edge"}


def finish_label(kind: str) -> str:
    """The UI's label of the button that ends the capture: "Finish & check" for the check, else "Finish & calibrate"."""
    return "Finish & check" if kind == "check" else "Finish & calibrate"


# --------------------------------------------------------------------------- detection
@dataclass
class Detection:
    """What one processed frame shows (pixel coordinates of ``left`` / ``right``)."""

    kind: str
    index: int
    t: float
    image_size: tuple[int, int]
    left: np.ndarray
    right: np.ndarray | None = None
    corners_l: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), np.float32))
    ids_l: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int32))
    corners_r: np.ndarray | None = None
    ids_r: np.ndarray | None = None
    T_cb: np.ndarray | None = None
    distance_mm: float | None = None
    tilt_deg: float | None = None
    face: np.ndarray | None = None  # unit normal of the printed face, toward the camera (camera frame)
    center_uv: np.ndarray | None = None  # board centre in the left image
    outline: np.ndarray | None = None  # (4, 2) board rectangle in the left image
    page: np.ndarray | None = None  # (4, 2) paper outline (lasers / check)
    lines: dict | None = None  # {"vertical": (N, 2), "horizontal": (M, 2)} in the left image
    margin: dict | None = None  # {"vertical": n, "horizontal": n} points on the blank margin

    @property
    def common(self) -> int:
        if self.ids_r is None:
            return 0
        return int(len(np.intersect1d(self.ids_l, self.ids_r)))

    @property
    def board_found(self) -> bool:
        if self.kind == "stereo":
            return len(self.ids_l) > 0 or (self.ids_r is not None and len(self.ids_r) > 0)
        return self.T_cb is not None


def _nominal_K(w: int, h: int) -> np.ndarray:
    f = 720.0 * w / 1280.0  # the 83 deg lens (ARCHITECTURE.md section 7)
    return np.array([[f, 0.0, (w - 1) / 2.0], [0.0, f, (h - 1) / 2.0], [0.0, 0.0, 1.0]])


def _project(P: np.ndarray, K: np.ndarray, D: np.ndarray | None) -> np.ndarray:
    import cv2

    P = np.asarray(P, np.float64).reshape(-1, 3)
    if D is None or not np.any(D):
        from ohs.geometry import project_points

        return project_points(P, K)
    if np.any(P[:, 2] <= 0):
        return np.full((len(P), 2), np.nan)
    uv, _ = cv2.projectPoints(P, np.zeros(3), np.zeros(3), np.asarray(K, np.float64), np.asarray(D, np.float64))
    return uv.reshape(-1, 2)


class BoardDetector:
    """Per-kind board detection for the calibration session (one processing thread only)."""

    def __init__(self, kind: str, spec: Any, calib: Any = None, paper: str = "letter") -> None:
        if kind != "stereo" and calib is None:
            raise ValueError(f"the {kind} calibration needs the stereo calibration")
        self.kind, self.spec, self.calib, self.paper = kind, spec, calib, paper
        self._rect = None
        self._lp = None
        self._layout = None
        self._raw: tuple[tuple[int, int], np.ndarray, np.ndarray | None] | None = None
        if kind != "stereo":
            from ohs.vision.rectify import Rectifier

            self._rect = Rectifier(calib.stereo, 1.0)
        if kind in ("lasers", "check"):
            from ohs.calib.board import charuco_page_layout
            from ohs.vision.laser import LaserParams

            w = int(calib.stereo.image_size[0])
            self._lp = LaserParams() if w == 1280 else LaserParams().scaled(w / 1280.0)
            try:
                self._layout = charuco_page_layout(spec, paper)
            except ValueError as e:  # the board does not fit the paper: count on the board margin instead
                log.warning("no page layout for %s on %s: %s", spec.describe(), paper, e)

    def _raw_model(self, w: int, h: int) -> tuple[np.ndarray, np.ndarray | None]:
        if self._raw is None or self._raw[0] != (w, h):
            c = self.calib
            if c is not None and tuple(int(v) for v in c.stereo.image_size) == (w, h):
                self._raw = ((w, h), np.asarray(c.stereo.K1, np.float64), np.asarray(c.stereo.D1, np.float64))
            else:
                self._raw = ((w, h), _nominal_K(w, h), None)
        return self._raw[1], self._raw[2]

    def detect(self, frame: Any) -> Detection:
        from ohs.calib.board import MIN_POSE_CORNERS, detect_charuco, estimate_board_pose

        w, h = (int(v) for v in frame.image_size)
        spec = self.spec
        if self.kind == "stereo":
            left, right = frame.left, frame.right
            K, D = self._raw_model(w, h)
        else:
            left, right = self._rect.rectify_left(frame.left), None
            K, D = self._rect.K, None
        cl, il = detect_charuco(left, spec)
        d = Detection(self.kind, int(frame.index), float(frame.t_host), (w, h), left, right, cl, il)
        if self.kind == "stereo":
            d.corners_r, d.ids_r = detect_charuco(right, spec)
        if len(il) >= MIN_POSE_CORNERS:
            ok, T = estimate_board_pose(cl, il, spec, K, D)
            if ok and T is not None:
                bw, bh = spec.size_mm
                R, t = T[:3, :3], T[:3, 3]
                centre = R @ np.array([bw / 2, bh / 2, 0.0]) + t
                d.T_cb = T
                d.distance_mm = float(np.linalg.norm(centre))
                d.tilt_deg = float(np.degrees(np.arccos(np.clip(abs(R[2, 2]), 0.0, 1.0))))
                face = -R[:, 2]
                d.face = face / max(float(np.linalg.norm(face)), 1e-12)
                corners = np.array([[0, 0, 0], [bw, 0, 0], [bw, bh, 0], [0, bh, 0]], np.float64) @ R.T + t
                d.outline = _project(corners, K, D)
                d.center_uv = _project(centre[None], K, D)[0]
        if d.center_uv is None and len(il):
            d.center_uv = np.asarray(cl, np.float64).reshape(-1, 2).mean(axis=0)
        if self.kind in ("lasers", "check"):
            from ohs.vision.laser import extract_laser_lines

            lines = extract_laser_lines(left, self._lp)
            d.lines = {"vertical": np.asarray(lines.get("vertical"), np.float64).reshape(-1, 2),
                       "horizontal": np.asarray(lines.get("horizontal"), np.float64).reshape(-1, 2)}
            d.margin = {"vertical": 0, "horizontal": 0}
            if d.T_cb is not None:
                d.page = self._page_outline(d.T_cb, K)
                d.margin = {n: self._margin_count(d.lines[n], d.T_cb, K) for n in ("vertical", "horizontal")}
        return d

    def _page_outline(self, T: np.ndarray, K: np.ndarray) -> np.ndarray | None:
        from ohs.calib.board import board_outline_uv

        if self._layout is None:
            return board_outline_uv(T, self.spec, K, margin_mm=-12.0)
        x0, x1, y0, y1 = self._layout.paper_bounds_mm
        P = np.array([[x0, y0, 0], [x1, y0, 0], [x1, y1, 0], [x0, y1, 0]], np.float64) @ T[:3, :3].T + T[:3, 3]
        return _project(P, K, None)

    def _margin_count(self, uv: np.ndarray, T: np.ndarray, K: np.ndarray) -> int:
        from ohs.geometry import pixels_to_rays, ray_plane_intersect

        if len(uv) == 0:
            return 0
        R, t = T[:3, :3], T[:3, 3]
        n = R[:, 2]
        X = ray_plane_intersect(pixels_to_rays(uv, K), n, -float(n @ t))
        ok = np.isfinite(X).all(axis=1)
        xy = ((X[ok] - t) @ R)[:, :2]
        if self._layout is None:  # no layout: the 12 mm band around the board pattern
            bw, bh = self.spec.size_mm
            m = 12.0
            inside = (xy[:, 0] > -m) & (xy[:, 0] < bw + m) & (xy[:, 1] > -m) & (xy[:, 1] < bh + m)
            board = (xy[:, 0] > 0) & (xy[:, 0] < bw) & (xy[:, 1] > 0) & (xy[:, 1] < bh)
            return int((inside & ~board).sum())
        return int(self._layout.margin_mask(xy, clearance_mm=MARGIN_CLEARANCE_MM).sum())


# --------------------------------------------------------------------------- rules
def usable_reason(d: Detection | None) -> str | None:
    """None when a view would be accepted, else why not (plain words, for "Not captured: ...")."""
    if d is None:
        return "there is no camera image yet"
    if d.kind == "stereo":
        if d.common < MIN_COMMON:
            return (f"both cameras must see the board ({d.common} common corner{'s' if d.common != 1 else ''}, "
                    f"need {MIN_COMMON})")
        return None
    if d.T_cb is None:
        return "the board was not found (show the whole board, avoid glare)"
    if d.kind in ("lasers", "check"):
        m = d.margin or {}
        for name in ("vertical", "horizontal"):
            n = int(m.get(name, 0))
            if n < LINE_MIN_POINTS:
                return (f"the {name} laser line misses the white page margin ({n} point{'s' if n != 1 else ''}, "
                        f"need {LINE_MIN_POINTS})")
    return None


def _angle_deg(a: np.ndarray | None, b: np.ndarray | None) -> float | None:
    if a is None or b is None:
        return None
    return float(np.degrees(np.arccos(np.clip(float(np.dot(a, b)), -1.0, 1.0))))


def novelty(d: Detection, views: Sequence[Any]) -> tuple[bool, Any]:
    """(True, None) if ``d`` differs from every captured view, else (False, the closest similar view).

    A view counts as different when the board centre moved more than 12 % of the image width, or
    the distance changed by more than 10 %, or the board normal turned more than 8 deg.
    """
    if d.center_uv is None:
        return False, None
    w = float(d.image_size[0])
    best, best_shift = None, math.inf
    for v in views:
        c = getattr(v, "center_uv", None)
        if c is None:
            continue
        shift = float(np.linalg.norm(np.asarray(d.center_uv) - np.asarray(c))) / w
        if shift > NEW_SHIFT_FRAC:
            continue
        dv, vd = d.distance_mm, getattr(v, "distance_mm", None)
        if dv is not None and vd is not None and vd > 0 and abs(dv - vd) / vd > NEW_DIST_FRAC:
            continue
        ang = _angle_deg(d.face, getattr(v, "face", None))
        if ang is not None and ang > NEW_TILT_DEG:
            continue
        if shift < best_shift:
            best, best_shift = v, shift
    return best is None, best


def _corner_motion(prev_uv: np.ndarray, prev_ids: np.ndarray, uv: np.ndarray, ids: np.ndarray) -> float | None:
    common, ia, ib = np.intersect1d(prev_ids, ids, assume_unique=False, return_indices=True)
    if len(common) < 4:
        return None
    dist = np.linalg.norm(np.asarray(uv, np.float64)[ib] - np.asarray(prev_uv, np.float64)[ia], axis=1)
    return float(np.percentile(dist, 90))


class SteadyTracker:
    """How long the board has been held still (frame time); see the module docstring."""

    def __init__(self, hold_s: float = STEADY_S, max_px: float = STEADY_PX) -> None:
        self.hold_s, self.max_px = float(hold_s), float(max_px)
        self._prev: Detection | None = None
        self._since: float | None = None

    def reset(self) -> None:
        self._prev, self._since = None, None

    def update(self, d: Detection) -> float:
        """Progress 0..1 of the hold after this detection."""
        prev, self._prev = self._prev, d
        if len(d.ids_l) < 4 or (d.kind == "stereo" and d.common < 4):
            self._since = None
            return 0.0
        moved = prev is None or d.t < prev.t
        if not moved:
            m = _corner_motion(prev.corners_l, prev.ids_l, d.corners_l, d.ids_l)
            if m is None or m >= self.max_px:
                moved = True
            elif d.kind == "stereo" and prev.ids_r is not None and d.ids_r is not None:
                mr = _corner_motion(prev.corners_r, prev.ids_r, d.corners_r, d.ids_r)
                moved = mr is None or mr >= self.max_px
        if moved or self._since is None:
            self._since = d.t
        return float(np.clip((d.t - self._since) / self.hold_s, 0.0, 1.0)) if self.hold_s > 0 else 1.0


# --------------------------------------------------------------------------- coverage / diversity
def _cells(uv: np.ndarray | None, w: int, h: int, grid: int = GRID) -> np.ndarray:
    if uv is None or len(uv) == 0:
        return np.zeros(0, np.int64)
    uv = np.asarray(uv, np.float64).reshape(-1, 2)
    cx = np.clip(np.floor(uv[:, 0] / w * grid), 0, grid - 1).astype(np.int64)
    cy = np.clip(np.floor(uv[:, 1] / h * grid), 0, grid - 1).astype(np.int64)
    return np.unique(cy * grid + cx)


def coverage(views: Sequence[Any], image_size: tuple[int, int], stereo: bool) -> dict:
    """``{cols, rows, left, right, left_pct, right_pct}``: captured views per 8 x 8 cell, row-major."""
    w, h = (int(v) for v in image_size)
    left = np.zeros(GRID * GRID, np.int64)
    right = np.zeros(GRID * GRID, np.int64) if stereo else None
    for v in views:
        left[_cells(getattr(v, "corners_l", None), w, h)] += 1
        if right is not None:
            right[_cells(getattr(v, "corners_r", None), w, h)] += 1
    return {"cols": GRID, "rows": GRID, "left": left.tolist(), "right": None if right is None else right.tolist(),
            "left_pct": round(100.0 * float((left > 0).mean()), 1),
            "right_pct": None if right is None else round(100.0 * float((right > 0).mean()), 1)}


def tilt_bin(face: np.ndarray | None, tilt_deg: float | None) -> str | None:
    """"flat" or the direction the printed face points ("left" / "right" / "up" / "down")."""
    if face is None or tilt_deg is None:
        return None
    if tilt_deg < FLAT_DEG:
        return "flat"
    fx, fy = float(face[0]), float(face[1])
    if abs(fx) >= abs(fy):
        return "right" if fx > 0 else "left"
    return "down" if fy > 0 else "up"


def distance_bin(kind: str, distance_mm: float | None) -> str | None:
    edges = DISTANCE_BINS.get(kind)
    if edges is None or distance_mm is None:
        return None
    return "near" if distance_mm < edges[0] else "mid" if distance_mm < edges[1] else "far"


def diversity(kind: str, views: Sequence[Any]) -> dict:
    out = {"flat": 0, "tilt_left": 0, "tilt_right": 0, "tilt_up": 0, "tilt_down": 0, "near": 0, "mid": 0, "far": 0}
    for v in views:
        t = tilt_bin(getattr(v, "face", None), getattr(v, "tilt_deg", None))
        if t == "flat":
            out["flat"] += 1
        elif t is not None:
            out[f"tilt_{t}"] += 1
        b = distance_bin(kind, getattr(v, "distance_mm", None))
        if b is not None:
            out[b] += 1
    return out


def region_of(uv: np.ndarray | None, image_size: tuple[int, int]) -> tuple[int, int] | None:
    if uv is None or not np.all(np.isfinite(uv)):
        return None
    w, h = image_size
    return (int(np.clip(uv[0] / w * 3, 0, 2)), int(np.clip(uv[1] / h * 3, 0, 2)))


# --------------------------------------------------------------------------- hints
def _hint(text: str, region: tuple[int, int] | None = None, tilt: str | None = None,
          distance: str | None = None) -> dict:
    box = None
    if region is not None:
        c, r = region
        box = [round(c / 3, 4), round(r / 3, 4), round((c + 1) / 3, 4), round((r + 1) / 3, 4)]
    return {"text": text, "region": box, "tilt": tilt, "distance": distance}


def _distance_hint(kind: str, d: Detection | None, div: dict) -> dict | None:
    edges = DISTANCE_BINS[kind]
    lo, hi = DISTANCE_RANGE[kind]
    targets = {"near": (lo + edges[0]) / 2, "mid": (edges[0] + edges[1]) / 2, "far": (edges[1] + hi) / 2}
    for b in ("near", "mid", "far") if kind == "stereo" else ("mid", "near", "far"):
        if div[b] > 0:
            continue
        want = targets[b]
        cur = d.distance_mm if d is not None else None
        way = None if cur is None else ("closer" if cur > want else "farther")
        verb = {"closer": "Move the board closer", "farther": "Move the board farther away", None: "Hold the board"}[way]
        return _hint(f"{verb}: about {want:.0f} mm from the scanner", distance=way)
    return None


def _tilt_hint(div: dict, deg: str = "20–45°") -> dict | None:
    words = {"left": "toward the left", "right": "toward the right", "up": "back, top edge away from you",
             "down": "forward, bottom edge away from you"}
    for t in TILT_NAMES:
        if div[f"tilt_{t}"] == 0:
            if t in ("left", "right"):
                return _hint(f"Turn the board {deg} so it faces {words[t]}", tilt=t)
            return _hint(f"Tip the board {deg} {words[t]}", tilt=t)
    return None


def next_hint(kind: str, d: Detection | None, views: Sequence[Any], image_size: tuple[int, int],
              target_views: tuple[int, int]) -> dict | None:
    """What to do next (CalibHint), or None for the IMU."""
    if kind == "imu":
        return None
    div = diversity(kind, views)
    if kind in ("lasers", "check") and d is not None and d.T_cb is not None:
        m = d.margin or {}
        for name in ("vertical", "horizontal"):
            if int(m.get(name, 0)) < LINE_MIN_POINTS:
                where = "above or below the pattern" if name == "vertical" else "left or right of the pattern"
                return _hint(f"Move or turn the board so the {name} laser line crosses the white margin {where}")
    if kind == "stereo":
        counts: dict[tuple[int, int], int] = {r: 0 for r in REGION_ORDER}
        for v in views:
            r = region_of(getattr(v, "center_uv", None), image_size)
            if r is not None:
                counts[r] += 1
        fewest = min(counts.values())
        if fewest == 0:
            r = min(REGION_ORDER, key=lambda k: (counts[k], REGION_ORDER.index(k)))
            return _hint(f"Move the board to {REGION_NAMES[r]} of the picture", region=r)
        h = _tilt_hint(div)
        if h is not None:
            return h
        h = _distance_hint(kind, d, div)
        if h is not None:
            return h
    elif kind == "lasers":
        h = _distance_hint(kind, d, div)
        if h is not None:
            return h
        h = _tilt_hint(div, "15–30°")
        if h is not None:
            return h
    else:  # check
        h = _distance_hint(kind, d, div)
        if h is not None:
            return h
    lo, hi = target_views
    finish = finish_label(kind)
    if len(views) >= hi:
        return _hint(f"Enough views: press {finish}")
    if len(views) >= lo:
        return _hint(f"Good coverage: add a few more varied views, or press {finish}")
    return _hint("Add more views: new places, distances and tilts")
