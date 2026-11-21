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

**Any set of line lasers** (config ``[lasers] lines``, ``names`` of :class:`BoardDetector`): the
extraction looks for as many lines per family as the set has. A family with one laser is counted
as above (with the two standard lasers everything is exactly as described). A family with several
lasers (the 4-laser build: ``vertical`` + ``vertical_2``, ``horizontal`` + ``horizontal_2``): its
margin points are split into lines by straight-line grouping in the image; when the calibration
in use already has the family's planes, each point is named after the nearest plane
(:func:`ohs.calib.laser_planes.family_labels`, within 5 mm), else the lines are only counted
(``lines_found``; which line is which is only known after the fit). The two sheets of such a
family can meet at some distance. Where that distance lies inside the range of the laser views
(:func:`meets_in_range`; the 4-laser build's vertical pair, at about 254 mm), the two lines run
into each other on a board held there and are not measured where they do. The session reports
the distance (``meet_mm``) so the app can say so: it comes from the calibrated planes, or is
estimated from the captured views (the gap between the two lines on the board shrinks to zero
there, :func:`meet_distance`). The fit does not need views on both sides of that distance
(measured: VALIDATION.md, "Calibration of four planes"), so :func:`next_hint` does not ask for
them; its accuracy follows the spread of distances, which the hints ask for anyway.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

__all__ = ["Detection", "BoardDetector", "SteadyTracker", "novelty", "usable_reason", "coverage", "diversity",
           "next_hint", "region_of", "finish_label", "line_label", "families", "line_problems", "meet_distance",
           "planes_meet_mm", "meets_in_range", "MIN_COMMON", "LINE_MIN_POINTS", "STEADY_PX", "STEADY_S", "COOLDOWN_S", "DISTANCE_RANGE",
           "DISTANCE_BINS", "TILT_NAMES", "GRID", "STANDARD_LINES"]

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
STANDARD_LINES = ("vertical", "horizontal")  # the config's default [lasers] lines
LINE_GROUP_PX = 3.0  # several lines per family: segments within this of one image line are one line (1280 px wide)
LINE_PLANE_TOL_MM = 5.0  # several lines per family: a margin point is named after a calibrated plane this close
MEET_MIN_GAP_MM = 3.0  # lines closer than this on the board are not measured (they may be one merged line)
_ORDINALS = {2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth"}
_MARGIN_WHERE = {"vertical": "above or below the pattern", "horizontal": "left or right of the pattern"}


def finish_label(kind: str) -> str:
    """The UI's label of the button that ends the capture: "Finish & check" for the check, else "Finish & calibrate"."""
    return "Finish & check" if kind == "check" else "Finish & calibrate"


def _family(name: str) -> str:
    from ohs.vision.laser import line_family

    return line_family(name)


def families(names: Sequence[str]) -> dict[str, list[str]]:
    """Laser names grouped by family (``"vertical"`` / ``"horizontal"``), in the given order."""
    out: dict[str, list[str]] = {}
    for n in names:
        out.setdefault(_family(n), []).append(n)
    return out


def line_label(name: str) -> str:
    """A laser's name in plain words: ``"vertical"``, ``"second vertical"`` (``vertical_2``), ``"vertical (left)"``."""
    fam = _family(name)
    if name == fam:
        return fam
    suffix = name[len(fam) + 1:]
    if suffix.isdigit() and int(suffix) in _ORDINALS:
        return f"{_ORDINALS[int(suffix)]} {fam}"
    return f"{fam} ({suffix.replace('_', ' ')})"


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
    lines: dict | None = None  # {"vertical": (N, 2), "horizontal": (M, 2)} in the left image (every line of a family)
    margin: dict | None = None  # {"vertical": n, "horizontal": n} points on the blank margin, per family
    names: tuple = STANDARD_LINES  # the configured lasers
    line_points: dict | None = None  # {laser: n or None} margin points per laser; None: not named yet
    lines_found: dict | None = None  # {family: [n, ...]} margin points per line found, largest first
    line_uv: dict | None = None  # {laser: (N, 2)} image points per named laser of a family with several
    line_sep: dict | None = None  # {family: mm} gap between a family's two lines on the board (its middle)
    meet_mm: dict | None = None  # {family: mm} where a family's sheets meet, from the calibrated planes

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

    def __init__(self, kind: str, spec: Any, calib: Any = None, paper: str = "letter",
                 names: Sequence[str] | None = None) -> None:
        if kind != "stereo" and calib is None:
            raise ValueError(f"the {kind} calibration needs the camera calibration")
        self.kind, self.spec, self.calib, self.paper = kind, spec, calib, paper
        self.names = tuple(names) if names else STANDARD_LINES
        self._fams = families(self.names)
        self._rect = None
        self._lp = None
        self._layout = None
        self._sheets: dict[str, list] = {}  # family -> the calibrated planes of its lasers (several per family)
        self._meet: dict[str, float] = {}
        self._raw: tuple[tuple[int, int], np.ndarray, np.ndarray | None] | None = None
        if kind != "stereo":
            from ohs.vision.rectify import Rectifier

            self._rect = Rectifier(calib.stereo, 1.0)
        if kind in ("lasers", "check"):
            from dataclasses import replace

            from ohs.calib.board import charuco_page_layout
            from ohs.vision.laser import LaserParams, lines_per_family

            w = int(calib.stereo.image_size[0])
            self._lp = LaserParams() if w == 1280 else LaserParams().scaled(w / 1280.0)
            n = lines_per_family(self.names)
            if n > 1:
                self._lp = replace(self._lp, lines_per_family=n)
            try:
                self._layout = charuco_page_layout(spec, paper)
            except ValueError as e:  # the board does not fit the paper: count on the board margin instead
                log.warning("no page layout for %s on %s: %s", spec.describe(), paper, e)
            planes = getattr(calib, "lasers", None) or {}
            for fam, members in self._fams.items():
                if len(members) > 1 and all(m in planes for m in members):
                    self._sheets[fam] = [(np.asarray(planes[m].normal, np.float64), float(planes[m].d)) for m in members]
            self._meet = planes_meet_mm(planes, self.names)

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
            d.names = self.names
            d.line_points = dict.fromkeys(self.names, 0)
            d.lines_found = {fam: [] for fam in self._fams}
            d.line_uv, d.line_sep, d.meet_mm = {}, {}, dict(self._meet)
            if d.T_cb is not None:
                d.page = self._page_outline(d.T_cb, K)
                for fam in ("vertical", "horizontal"):
                    members = self._fams.get(fam, [])
                    if len(members) > 1:
                        self._count_family(d, fam, members, np.asarray(lines.segments(fam)).reshape(-1), K)
                        continue
                    n = self._margin_count(d.lines[fam], d.T_cb, K)
                    d.margin[fam] = n
                    for m in members:
                        d.line_points[m] = n
                        d.lines_found[fam] = [n]
            else:
                for members in self._fams.values():
                    if len(members) > 1 and not self._sheets.get(_family(members[0])):
                        for m in members:
                            d.line_points[m] = None
        return d

    def _count_family(self, d: Detection, fam: str, members: list[str], seg: np.ndarray, K: np.ndarray) -> None:
        """A family with several lasers: its margin points split into lines (module docstring)."""
        uv = d.lines[fam]
        mask, X, xy = self._margin_mask(uv, d.T_cb, K)
        d.margin[fam] = int(mask.sum())
        seg = seg if len(seg) == len(uv) else np.zeros(len(uv), np.int64)
        tol = LINE_GROUP_PX * float(d.image_size[0]) / 1280.0
        # whole segments are grouped (a long baseline), then only their margin points are counted
        groups = sorted(((g & mask) for g in _group_lines(uv, seg, fam == "vertical", tol)), key=lambda g: -int(g.sum()))
        sizes = [int(g.sum()) for g in groups]
        d.lines_found[fam] = [s for s in sizes[: len(members)] if s > 0]
        if len(groups) >= 2 and min(sizes[:2]) >= LINE_MIN_POINTS:
            gap = _board_gap(xy[groups[0]], xy[groups[1]], self.spec)
            if gap is not None:
                d.line_sep[fam] = gap
        sheets = self._sheets.get(fam)
        if not sheets:
            for m in members:
                d.line_points[m] = None
            return
        from ohs.calib.laser_planes import family_labels

        ok = np.isfinite(X).all(axis=1)
        lab = np.full(len(uv), -1, np.int64)
        if ok.any():  # every point of the family on the board plane: the overlay shows whole lines
            P = X[ok]
            near = np.abs(np.stack([P @ n + dd for n, dd in sheets], axis=1)).min(axis=1) <= LINE_PLANE_TOL_MM
            lab[ok] = np.where(near, family_labels(P, sheets), -1)
        for j, m in enumerate(members):
            d.line_points[m] = int(((lab == j) & mask).sum())
            if (lab == j).any():
                d.line_uv[m] = uv[lab == j]

    def _margin_mask(self, uv: np.ndarray, T: np.ndarray, K: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """``(on the margin (N,), board-plane points in frame C (N, 3), board coordinates (N, 2))``."""
        from ohs.geometry import pixels_to_rays, ray_plane_intersect

        uv = np.asarray(uv, np.float64).reshape(-1, 2)
        if len(uv) == 0:
            return np.zeros(0, bool), np.zeros((0, 3)), np.zeros((0, 2))
        R, t = T[:3, :3], T[:3, 3]
        n = R[:, 2]
        X = ray_plane_intersect(pixels_to_rays(uv, K), n, -float(n @ t))
        ok = np.isfinite(X).all(axis=1)
        xy = np.full((len(uv), 2), np.nan)
        xy[ok] = ((X[ok] - t) @ R)[:, :2]
        mask = np.zeros(len(uv), bool)
        if self._layout is None:  # no layout: the 12 mm band around the board pattern
            bw, bh = self.spec.size_mm
            m = 12.0
            p = xy[ok]
            inside = (p[:, 0] > -m) & (p[:, 0] < bw + m) & (p[:, 1] > -m) & (p[:, 1] < bh + m)
            board = (p[:, 0] > 0) & (p[:, 0] < bw) & (p[:, 1] > 0) & (p[:, 1] < bh)
            mask[ok] = inside & ~board
        elif ok.any():
            mask[ok] = np.asarray(self._layout.margin_mask(xy[ok], clearance_mm=MARGIN_CLEARANCE_MM), bool)
        return mask, X, xy

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


def _group_lines(uv: np.ndarray, seg: np.ndarray, vertical: bool, tol_px: float) -> list[np.ndarray]:
    """Point masks of the image lines of one family: segments (each on one line) whose points lie
    within ``tol_px`` of a bigger segment's straight line join it (a laser line on the flat page is
    straight in the rectified image). Largest first."""
    if len(uv) == 0:
        return []
    t, c = (uv[:, 1], uv[:, 0]) if vertical else (uv[:, 0], uv[:, 1])
    ids, sizes = np.unique(seg, return_counts=True)
    groups: list[tuple[np.ndarray, np.ndarray]] = []  # (point mask, line coefficients)

    def fit(m: np.ndarray, old: np.ndarray | None = None) -> np.ndarray:
        if m.sum() >= 2 and np.ptp(t[m]) > 0:
            return np.polyfit(t[m], c[m], 1)
        return old if old is not None else np.array([0.0, float(c[m].mean())])

    for s in ids[np.argsort(-sizes, kind="stable")]:
        m = seg == s
        for k, (gm, coef) in enumerate(groups):
            if np.median(np.abs(c[m] - np.polyval(coef, t[m]))) < tol_px:
                gm = gm | m
                groups[k] = (gm, fit(gm, coef))
                break
        else:
            groups.append((m, fit(m)))
    return sorted((gm for gm, _ in groups), key=lambda g: -int(g.sum()))


def _board_gap(a: np.ndarray, b: np.ndarray, spec: Any) -> float | None:
    """Distance (mm) between two lines on the board, each given by its points in board coordinates,
    measured through the middle of the board."""
    a, b = a[np.isfinite(a).all(axis=1)], b[np.isfinite(b).all(axis=1)]
    if len(a) < 2 or len(b) < 2:
        return None
    centre = 0.5 * np.asarray(spec.size_mm, np.float64)

    def offset(p: np.ndarray, ref: np.ndarray | None) -> tuple[float, np.ndarray]:
        m = p.mean(axis=0)
        u = np.linalg.svd(p - m, full_matrices=False)[2][0]
        n = np.array([-u[1], u[0]])
        if ref is not None and n @ ref < 0:
            n = -n
        return float(n @ (centre - m)), n

    sa, na = offset(a, None)
    sb, _ = offset(b, na)
    gap = abs(sa - sb)
    return round(gap, 2) if gap >= MEET_MIN_GAP_MM else None


def planes_meet_mm(planes: Any, names: Sequence[str]) -> dict[str, float]:
    """``{family: mm}``: for each family with two lasers among ``names`` whose planes are in
    ``planes``, how far ahead of the camera their sheets meet (vertical sheets in the camera's
    horizontal mid-plane, horizontal ones in its vertical mid-plane). Only distances in front of
    the camera, within 2 m."""
    out: dict[str, float] = {}
    planes = planes or {}
    for fam, members in families(names).items():
        if len(members) != 2 or not all(m in planes for m in members):
            continue
        (n1, d1), (n2, d2) = ((np.asarray(planes[m].normal, np.float64), float(planes[m].d)) for m in members)
        k = 0 if fam == "vertical" else 1  # the other in-plane coordinate is 0
        A = np.array([[n1[k], n1[2]], [n2[k], n2[2]]])
        if abs(np.linalg.det(A)) < 1e-9:
            continue
        z = float(np.linalg.solve(A, -np.array([d1, d2]))[1])
        if 0.0 < z < 2000.0:
            out[fam] = round(z, 1)
    return out


def meets_in_range(meet_mm: Any) -> dict[str, float]:
    """The entries of ``{family: mm}`` that lie inside the range of the laser views
    (:data:`DISTANCE_RANGE`): only there can the board be held where the lines meet."""
    lo, hi = DISTANCE_RANGE["lasers"]
    return {f: float(z) for f, z in (meet_mm or {}).items() if lo < float(z) < hi}


def meet_distance(views: Sequence[Any], fam: str) -> float | None:
    """Where the two lines of family ``fam`` meet (mm from the scanner), estimated from captured
    views: the gap between the lines on the board (``line_sep``) grows in proportion to the
    distance from that point, on either side. Only the gap's size is known, not its side, so two
    views are not enough (the point could lie beyond them or between them): None with fewer than
    three views that measured the gap, over less than 20 mm of distance, or when the estimate is
    not between 50 mm and 1 m."""
    pts = [(float(v.distance_mm), float(v.line_sep[fam])) for v in views
           if getattr(v, "distance_mm", None) is not None and fam in (getattr(v, "line_sep", None) or {})]
    if len(pts) < 3:
        return None
    z = np.array([p[0] for p in pts])
    s = np.array([p[1] for p in pts])
    if np.ptp(z) < 20.0:
        return None
    z0 = np.arange(50.0, 1000.0, 0.5)  # gap = a |z - z0|: the best a for each z0, then the best z0
    w = np.abs(z[None, :] - z0[:, None])
    ww = np.maximum((w * w).sum(axis=1), 1e-12)
    a = (w @ s) / ww
    err = ((s[None, :] - a[:, None] * w) ** 2).sum(axis=1)
    err[a <= 0] = np.inf
    k = int(np.argmin(err))
    if not np.isfinite(err[k]) or k == 0 or k == len(z0) - 1:
        return None
    return round(float(z0[k]), 1)


# --------------------------------------------------------------------------- rules
def line_problems(d: Detection) -> list[str]:
    """What is missing on the margin of one laser view (plain words), empty when every line crosses it.

    A laser that can be named needs :data:`LINE_MIN_POINTS` margin points; a family whose lasers
    cannot be named yet needs as many lines found as it has lasers, each with that many points.
    """
    names = tuple(d.names or STANDARD_LINES)
    pts = d.line_points or {}
    out: list[str] = []
    for fam, members in families(names).items():
        if len(members) == 1 or all(pts.get(m) is not None for m in members):
            for m in members:
                n = int(pts[m]) if pts.get(m) is not None else int((d.margin or {}).get(fam, 0))
                if n < LINE_MIN_POINTS:
                    out.append(f"the {line_label(m)} laser line misses the white page margin "
                               f"({n} point{'s' if n != 1 else ''}, need {LINE_MIN_POINTS})")
        else:
            found = sum(c >= LINE_MIN_POINTS for c in (d.lines_found or {}).get(fam, []))
            if found < len(members):
                out.append(f"only {found} of the {len(members)} {fam} laser lines "
                           f"cross{'es' if found == 1 else ''} the white page margin (need {LINE_MIN_POINTS} points on each)")
    return out


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
        problems = line_problems(d)
        if problems:
            return problems[0]
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


def _line_hint(d: Detection) -> dict | None:
    """The first laser line that misses the margin: what to do about it."""
    names = tuple(d.names or STANDARD_LINES)
    pts = d.line_points or {}
    for fam, members in families(names).items():
        where = _MARGIN_WHERE[fam]
        if len(members) == 1 or all(pts.get(m) is not None for m in members):
            for m in members:
                n = int(pts[m]) if pts.get(m) is not None else int((d.margin or {}).get(fam, 0))
                if n < LINE_MIN_POINTS:
                    return _hint(f"Move or turn the board so the {line_label(m)} laser line crosses the white margin {where}")
        else:
            found = sum(c >= LINE_MIN_POINTS for c in (d.lines_found or {}).get(fam, []))
            if found < len(members):
                return _hint(f"Only {found} of the {len(members)} {fam} laser lines cross{'es' if found == 1 else ''} "
                             f"the white margin: move or turn the board so each one crosses it {where}")
    return None


def next_hint(kind: str, d: Detection | None, views: Sequence[Any], image_size: tuple[int, int],
              target_views: tuple[int, int]) -> dict | None:
    """What to do next (CalibHint), or None for the IMU."""
    if kind == "imu":
        return None
    div = diversity(kind, views)
    if kind in ("lasers", "check") and d is not None and d.T_cb is not None:
        h = _line_hint(d)
        if h is not None:
            return h
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
