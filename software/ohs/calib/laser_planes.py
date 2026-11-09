"""Laser-plane calibration from views of the ChArUco board crossed by the line lasers.

Everything here lives in the rectified left camera frame ``C`` (ideal pinhole ``K = K_rect``, no
distortion, millimetres). One *observation* is a rectified left image of the board plus the laser
line pixels found in it (sub-pixel ridge centres from the vision module, passed in by the caller):

1. :func:`make_observation` detects the board, estimates ``T_cb`` and the board polygon in the
   image (shrunk by ``margin_mm`` to stay clear of the board edges);
2. :func:`board_points_from_line` keeps the line pixels inside that polygon and intersects their
   viewing rays with the board plane, giving 3D points on the laser sheet;
3. :func:`fit_laser_planes` pools the points of all observations per laser and fits
   ``n . X + d = 0`` with RANSAC + least squares (:func:`ohs.geometry.fit_plane_ransac`).

A plane is only determined if the points span two directions, i.e. the line was seen on the board
at clearly different poses (distances / tilts). The fit therefore requires
:data:`MIN_OBSERVATIONS` observations from at least :data:`MIN_DISTINCT_POSES` distinct board
poses and an in-plane spread of the (inlier) points of at least :data:`MIN_SPREAD_MM` in the
second principal direction; otherwise the laser is reported as degenerate and *no* plane is
returned for it. :func:`check_laser_planes` measures how far new observations lie from existing
planes ("is the calibration still good?").

**Regions** (``region`` argument of :func:`make_observation`, :func:`board_points_from_line`,
:func:`fit_laser_planes`, :func:`check_laser_planes`):

* ``"board"`` - line pixels on the chessboard pattern (inside the outline shrunk by ``margin_mm``);
* ``"margin"`` - line pixels on the blank white paper *around* the pattern, as printed by
  :func:`ohs.calib.board.render_charuco_page` (:class:`~ohs.calib.board.CharucoPageLayout`):
  at least ``margin_mm`` away from the pattern, from the scale bar / label text and from the paper
  edge. The page is glued to glass, so the margin is coplanar with the board;
* ``"both"`` - the union.

Where the stripe crosses black/white ink its sub-pixel centre is pulled toward the white side
(by up to ~1 px; ink is ~20x darker than paper). On simulated board views (8 poses, 200-390 mm)
board points lie 0.14-0.17 mm rms off the true sheet and give plane tilts of 0.01-0.08 deg and
offsets up to 0.3 mm, while margin points lie 0.02 mm rms off it and give < 0.006 deg and
< 0.02 mm (laser-point errors over 150-450 mm about 10x smaller; VALIDATION.md). The
calibration flow (``ohs.app.calib_flow.lasers_from_recording``) therefore uses ``"margin"`` by
default. The low-level default stays ``"board"`` for backward compatibility (observations built
by hand know nothing about the page); an observation without a page layout only supports
``"board"``.

Reports are plain JSON-serialisable dicts.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from ..geometry import fit_plane_ransac, pixels_to_rays, ray_plane_intersect, rotation_angle_deg
from ..types import LaserPlane
from .board import (
    CharucoPageLayout,
    CharucoSpec,
    board_outline_uv,
    charuco_page_layout,
    detect_charuco,
    estimate_board_pose,
    reprojection_errors,
)

__all__ = [
    "LaserBoardObservation",
    "make_observation",
    "board_points_from_line",
    "fit_laser_planes",
    "check_laser_planes",
    "REGIONS",
    "MIN_OBSERVATIONS",
    "MIN_DISTINCT_POSES",
    "MIN_SPREAD_MM",
]

REGIONS = ("board", "margin", "both")

log = logging.getLogger(__name__)

MIN_OBSERVATIONS = 3  # observations that contain the line on the board
MIN_DISTINCT_POSES = 3  # of which this many must be clearly different board poses
MIN_SPREAD_MM = 5.0  # min std of the inlier points along their 2nd principal axis
MIN_POINTS_PER_OBS = 10
DISTINCT_ROT_DEG = 2.0  # two board poses closer than this (and DISTINCT_TRANS_MM) count as one
DISTINCT_TRANS_MM = 10.0
CHECK_OK_MEDIAN_MM = 0.5  # check_laser_planes: median |residual| must stay below this
CHECK_INLIER_MM = 1.0


@dataclass
class LaserBoardObservation:
    """One board view with laser lines, in the rectified frame C.

    ``T_cb``: board -> C (4x4, mm). ``board_polygon_uv``: (4, 2) image polygon of the usable board
    area. ``line_uv``: laser name -> (N, 2) line pixel centres in the rectified left image.
    ``region``: default region for :func:`board_points_from_line` (see the module docstring);
    ``page``: layout of the printed page (needed for ``"margin"`` / ``"both"``); ``margin_mm``:
    clearance from the pattern, ink and paper edges used by the margin region.
    """

    T_cb: np.ndarray
    board_polygon_uv: np.ndarray
    line_uv: dict[str, np.ndarray] = field(default_factory=dict)
    n_corners: int = 0
    reproj_rms_px: float = float("nan")
    region: str = "board"
    page: CharucoPageLayout | None = None
    margin_mm: float = 5.0


def _check_region(region: str) -> str:
    if region not in REGIONS:
        raise ValueError(f"region must be one of {REGIONS}, got {region!r}")
    return region


# --------------------------------------------------------------------------- observations
def make_observation(
    left_rect: np.ndarray,
    K: np.ndarray,
    spec: CharucoSpec,
    line_uv: Mapping[str, np.ndarray],
    margin_mm: float = 5.0,
    *,
    region: str = "board",
    page: CharucoPageLayout | None = None,
    paper: str = "letter",
) -> LaserBoardObservation | None:
    """Detect the board in a rectified left image and bundle it with the given line pixels.

    ``region`` sets the observation's default point region (``"board"`` | ``"margin"`` |
    ``"both"``). ``page`` is the printed page layout; by default the page
    :func:`~ohs.calib.board.render_charuco_page` prints for ``spec`` on ``paper``. Returns ``None``
    (and logs why) if the board or its pose cannot be found.
    """
    _check_region(region)
    K = np.asarray(K, dtype=np.float64)
    corners, ids = detect_charuco(left_rect, spec)
    ok, T_cb = estimate_board_pose(corners, ids, spec, K)
    if not ok or T_cb is None:
        log.info("laser observation skipped: board not found or no pose (%d corners)", len(ids))
        return None
    polygon = board_outline_uv(T_cb, spec, K, margin_mm)
    if not np.all(np.isfinite(polygon)):
        log.info("laser observation skipped: board outline not in front of the camera")
        return None
    if page is None:
        try:
            page = charuco_page_layout(spec, paper)
        except ValueError as exc:  # the board does not fit on that paper: no printed margin known
            if region != "board":
                raise ValueError(f"region {region!r} needs the printed page layout: {exc}") from exc
            page = None
    err = np.linalg.norm(reprojection_errors(T_cb, corners, ids, spec, K), axis=1)
    kept = err <= max(1.0, 4.0 * float(np.median(err)))  # same rule as the pose estimator's outlier step
    rms = float(np.sqrt(np.mean(err[kept] ** 2)))
    lines = {str(k): np.asarray(v, dtype=np.float64).reshape(-1, 2) for k, v in line_uv.items()}
    return LaserBoardObservation(T_cb, polygon, lines, int(len(ids)), rms, region, page, float(margin_mm))


def _inside_convex_polygon(uv: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Boolean mask of points (N, 2) strictly inside a convex polygon (M, 2), any vertex order."""
    a = np.asarray(poly, dtype=np.float64).reshape(-1, 2)
    b = np.roll(a, -1, axis=0)
    e = b - a  # (M, 2)
    rel = uv[:, None, :] - a[None, :, :]  # (N, M, 2)
    cross = e[None, :, 0] * rel[:, :, 1] - e[None, :, 1] * rel[:, :, 0]
    area2 = float(np.sum(a[:, 0] * b[:, 1] - b[:, 0] * a[:, 1]))
    sign = 1.0 if area2 >= 0 else -1.0
    return np.all(sign * cross > 0, axis=1)


def _board_plane(T_cb: np.ndarray) -> tuple[np.ndarray, float]:
    """Board plane ``n . X + d = 0`` in C (n = board z axis)."""
    n = np.asarray(T_cb[:3, 2], dtype=np.float64)
    return n, -float(n @ T_cb[:3, 3])


def board_points_from_line(obs: LaserBoardObservation, K: np.ndarray, name: str, region: str | None = None) -> np.ndarray:
    """3D points (N, 3) in C of the laser line ``name`` on the board plane of one observation.

    ``region`` (default ``obs.region``): ``"board"`` uses the pixels inside
    ``obs.board_polygon_uv``; ``"margin"`` those whose board-plane point lies on the blank printed
    margin (``obs.page.margin_mask`` with ``obs.margin_mm``); ``"both"`` the union. The rays are
    intersected with the board plane. Returns an empty (0, 3) array if the line is missing or not
    in the region.
    """
    region = _check_region(obs.region if region is None else region)
    uv = obs.line_uv.get(name)
    if uv is None or len(uv) == 0:
        return np.zeros((0, 3))
    uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
    uv = uv[np.all(np.isfinite(uv), axis=1)]
    n, d = _board_plane(np.asarray(obs.T_cb, dtype=np.float64))
    K = np.asarray(K, dtype=np.float64)
    if region == "board":
        uv = uv[_inside_convex_polygon(uv, obs.board_polygon_uv)]
        if len(uv) == 0:
            return np.zeros((0, 3))
        P = ray_plane_intersect(pixels_to_rays(uv, K), n, d)
        return P[np.all(np.isfinite(P), axis=1)]
    if obs.page is None:
        raise ValueError(f"region {region!r} needs the printed page layout (LaserBoardObservation.page)")
    if len(uv) == 0:
        return np.zeros((0, 3))
    P = ray_plane_intersect(pixels_to_rays(uv, K), n, d)
    finite = np.all(np.isfinite(P), axis=1)
    T = np.asarray(obs.T_cb, dtype=np.float64)
    xy_b = ((np.where(finite[:, None], P, 0.0) - T[:3, 3]) @ T[:3, :3])[:, :2]
    keep = finite & obs.page.margin_mask(xy_b, obs.margin_mm)
    if region == "both":
        keep |= finite & _inside_convex_polygon(uv, obs.board_polygon_uv)
    return P[keep]


# --------------------------------------------------------------------------- fitting
def _count_distinct_poses(poses: Sequence[np.ndarray]) -> int:
    reps: list[np.ndarray] = []
    for T in poses:
        same = any(
            rotation_angle_deg(Tr[:3, :3].T @ T[:3, :3]) < DISTINCT_ROT_DEG
            and np.linalg.norm(Tr[:3, 3] - T[:3, 3]) < DISTINCT_TRANS_MM
            for Tr in reps
        )
        if not same:
            reps.append(np.asarray(T, dtype=np.float64))
    return len(reps)


def _spread(P: np.ndarray) -> np.ndarray:
    """Standard deviations (3,) of points along their principal axes, largest first."""
    if len(P) < 3:
        return np.zeros(3)
    return np.linalg.svd(P - P.mean(axis=0), compute_uv=False) / math.sqrt(len(P))


def _fit_one(name: str, per_obs: list[tuple[int, np.ndarray]], observations: Sequence[LaserBoardObservation],
             thresh: float) -> tuple[LaserPlane | None, dict]:
    used = [(i, P) for i, P in per_obs if len(P) >= MIN_POINTS_PER_OBS]
    n_points = int(sum(len(P) for _, P in used))
    entry: dict = {"ok": False, "degenerate": False, "n_observations": len(used), "n_points": n_points}
    if len(used) < MIN_OBSERVATIONS:
        entry["reason"] = (f"the {name} line was found on the board in only {len(used)} observation(s); "
                           f"need >= {MIN_OBSERVATIONS} with different board poses")
        return None, entry
    n_distinct = _count_distinct_poses([observations[i].T_cb for i, _ in used])
    P = np.vstack([P for _, P in used])
    spread = _spread(P)
    entry.update(n_distinct_poses=n_distinct, spread_mm=[float(s) for s in spread])
    if n_distinct < MIN_DISTINCT_POSES or spread[1] < MIN_SPREAD_MM:
        entry["degenerate"] = True
        entry["reason"] = (
            f"degenerate geometry: {n_distinct} distinct board pose(s), in-plane spread {spread[1]:.2f} mm "
            f"(need >= {MIN_DISTINCT_POSES} poses and >= {MIN_SPREAD_MM:.0f} mm): the points are nearly collinear, "
            "so the plane is not determined. Move the board to different distances and tilts."
        )
        return None, entry
    try:
        n, d, mask, rms = fit_plane_ransac(P, thresh=thresh, iters=1000, seed=0, min_inliers=3 * MIN_POINTS_PER_OBS)
    except ValueError as exc:
        entry["reason"] = f"RANSAC failed: {exc}"
        return None, entry
    inl = P[mask]
    spread_in = _spread(inl)
    # per-observation inlier statistics
    counts, offsets, per_obs_rep = [], 0, []
    for i, Pi in used:
        m = mask[offsets : offsets + len(Pi)]
        offsets += len(Pi)
        counts.append(int(m.sum()))
        per_obs_rep.append({"index": int(i), "n_points": int(len(Pi)), "n_inliers": int(m.sum()),
                            "rms_mm": float(np.sqrt(np.mean((Pi[m] @ n + d) ** 2))) if m.any() else None})
    n_obs_inl = int(sum(c >= MIN_POINTS_PER_OBS for c in counts))
    entry.update(per_observation=per_obs_rep, inlier_spread_mm=[float(s) for s in spread_in])
    if n_obs_inl < MIN_OBSERVATIONS or spread_in[1] < MIN_SPREAD_MM:
        entry["degenerate"] = True
        entry["reason"] = (
            f"degenerate geometry after outlier removal: {n_obs_inl} observation(s) agree, inlier spread "
            f"{spread_in[1]:.2f} mm. Capture more board poses at different distances and tilts."
        )
        return None, entry
    plane = LaserPlane(name, n, d, rms=float(rms), n_points=int(mask.sum()))
    # rough statistical uncertainty: tilt about the weakest in-plane direction, lever arm to the origin
    dtheta = float(rms) / (spread_in[1] * math.sqrt(len(inl)))
    entry.update(
        ok=True,
        normal=plane.normal.tolist(),
        d=float(plane.d),
        rms_mm=float(rms),
        n_inliers=int(mask.sum()),
        inlier_fraction=float(mask.mean()),
        normal_std_deg=float(np.degrees(dtheta)),
        d_std_mm=float(math.hypot(rms / math.sqrt(len(inl)), np.linalg.norm(inl.mean(axis=0)) * dtheta)),
    )
    return plane, entry


def fit_laser_planes(
    observations: Sequence[LaserBoardObservation],
    K: np.ndarray,
    names: Sequence[str] = ("vertical", "horizontal"),
    ransac_thresh_mm: float = 0.5,
    region: str | None = None,
) -> tuple[dict[str, LaserPlane], dict]:
    """Fit one plane per laser from board observations (RANSAC + least squares).

    ``region`` (``"board"`` | ``"margin"`` | ``"both"``; default: each observation's own) selects
    which line pixels are used (module docstring). Returns ``(planes, report)``. ``planes`` only
    holds the lasers that could be fitted; for every requested name ``report["lasers"][name]``
    gives ``ok``, ``degenerate``, ``reason`` (when not ok), point/observation counts, ``rms_mm``,
    the plane and rough uncertainties. ``report["ok"]`` is True when all requested lasers were fitted.
    """
    K = np.asarray(K, dtype=np.float64)
    if region is not None:
        _check_region(region)
    planes: dict[str, LaserPlane] = {}
    used = sorted({o.region for o in observations}) if region is None else [region]
    report: dict = {"n_observations": len(observations), "region": "+".join(used) if used else region, "lasers": {}}
    for name in names:
        per_obs = [(i, board_points_from_line(o, K, name, region)) for i, o in enumerate(observations)]
        plane, entry = _fit_one(name, per_obs, observations, ransac_thresh_mm)
        report["lasers"][name] = entry
        if plane is None:
            log.warning("laser plane %r not fitted: %s", name, entry.get("reason"))
            continue
        planes[name] = plane
        log.info("laser plane %r: n=(%.4f, %.4f, %.4f) d=%.2f mm, rms %.3f mm, %d inliers from %d observations",
                 name, *plane.normal, plane.d, plane.rms, plane.n_points, entry["n_observations"])
    report["ok"] = len(planes) == len(tuple(names))
    return planes, report


def check_laser_planes(
    planes: Mapping[str, LaserPlane], observations: Sequence[LaserBoardObservation], K: np.ndarray,
    region: str | None = None,
) -> dict:
    """Residuals (mm) of new board observations against existing laser planes.

    Per laser: point count, signed mean, rms, median / p95 / max absolute distance, the fraction
    within 1 mm and per-observation statistics; ``ok`` when the median |distance| is below
    :data:`CHECK_OK_MEDIAN_MM`. ``report["ok"]`` is False if any laser fails or has no points.
    ``region`` as in :func:`fit_laser_planes`.
    """
    K = np.asarray(K, dtype=np.float64)
    if region is not None:
        _check_region(region)
    used = sorted({o.region for o in observations}) if region is None else [region]
    report: dict = {"n_observations": len(observations), "region": "+".join(used) if used else region, "lasers": {}}
    all_ok = True
    for name, plane in planes.items():
        rows, dists = [], []
        for i, o in enumerate(observations):
            P = board_points_from_line(o, K, name, region)
            if len(P) == 0:
                continue
            dd = plane.distance(P)
            dists.append(dd)
            rows.append({"index": int(i), "n_points": int(len(P)), "mean_mm": float(dd.mean()),
                         "median_abs_mm": float(np.median(np.abs(dd)))})
        if not dists:
            report["lasers"][name] = {"n_points": 0, "ok": False, "verdict": f"the {name} line was not seen on the board"}
            all_ok = False
            continue
        dd = np.concatenate(dists)
        a = np.abs(dd)
        med = float(np.median(a))
        ok = med < CHECK_OK_MEDIAN_MM
        all_ok &= ok
        report["lasers"][name] = {
            "n_points": int(len(dd)),
            "n_observations": len(rows),
            "mean_mm": float(dd.mean()),
            "median_mm": float(np.median(dd)),  # robust systematic offset (signed)
            "rms_mm": float(np.sqrt(np.mean(dd**2))),
            "median_abs_mm": med,
            "p95_abs_mm": float(np.percentile(a, 95)),
            "max_abs_mm": float(a.max()),
            "inlier_fraction": float(np.mean(a < CHECK_INLIER_MM)),
            "per_observation": rows,
            "ok": ok,
            "verdict": "good" if ok else (
                f"median distance {med:.2f} mm to the calibrated {name} plane (want < {CHECK_OK_MEDIAN_MM} mm): "
                "the laser may have moved; recalibrate the laser planes"
            ),
        }
        log.info("laser check %r: median |d| %.3f mm over %d points (%s)", name, med, len(dd), "ok" if ok else "BAD")
    report["ok"] = bool(all_ok and len(planes) > 0)
    return report
