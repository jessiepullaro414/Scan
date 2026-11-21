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

**Any set of line lasers.** A laser's name starts with its family, ``"vertical"`` or
``"horizontal"`` (:func:`ohs.vision.laser.line_family`), and the extraction gives the pixels of all
lines of a family together (``LaserLines.as_dict()``: keys ``"vertical"`` and ``"horizontal"``).
With one name per family (the two standard lasers) each name is fitted on its own pixels, exactly
as before. With several names in a family (the 4-laser variant: ``"vertical"`` and
``"vertical_2"``), :func:`fit_laser_planes` separates the sheets without any design data:

* it pools the family's board points over all observations, fits the best plane (RANSAC), takes
  its inliers out and fits the next, as many planes as the family has names. A plane counts only
  when its inliers come from at least :data:`MIN_DISTINCT_POSES` distinct board poses (a single
  view's points all lie on that view's board plane); with fewer planes than names none of the
  family is fitted, because which laser a lone plane belongs to cannot be told;
* each plane is named by where its sheet starts (:func:`plane_intercept`): the vertical family by
  the x at which the plane meets the camera's x axis (``y = z = 0``), largest first; the
  horizontal family by the y at which it meets the y axis, smallest first (camera y points down,
  so the top laser comes first). The names of a family are taken in the order they are given:
  ``("vertical", "vertical_2")`` makes the laser right of the cameras ``"vertical"`` (``[lasers]
  lines`` in the configuration follows the same order);
* every point of the family then goes to the nearest of these planes, except points closer than
  :data:`FAMILY_CLEAR_MM` to another of them, and each plane is fitted and checked on its points
  as a single laser is (counts, distinct poses, spread, RANSAC).

**One laser at a time.** With a channel per laser (the 4-laser build, firmware 1.2.0) the
calibration lights one laser at a time for every view, and each observation says which
(``LaserBoardObservation.lit``). A family whose observations are lit that way is fitted without the
separation above: each laser on the family's points of the observations that lit it, as a single
laser is. Observations that lit another laser do not count for a laser; observations with ``lit``
None (all lasers on) count as before.

Where the two lines of a family meet on the board the extraction drops them, and where they lie
on top of each other over a whole view (the board near the depth at which the sheets meet, square
to them) the merged stripe is left out by that clearance, so views near that depth simply have
fewer points; the report gives each laser's views and points.
:func:`check_laser_planes` and :func:`points_by_laser` measure a family with several planes the
same way: each point on the nearest plane of its family, none within :data:`FAMILY_CLEAR_MM` of
another.

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
from ..vision.laser import LINE_NAMES, is_line_name, line_family
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
    "family_points",
    "family_labels",
    "points_by_laser",
    "plane_intercept",
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
# several lasers per family: a point closer than this to another sheet of its family is not used for
# the fit. There the two lines lie within a few pixels of each other (2 mm is 6 px at 250 mm) and
# merge into one stripe whose centre lies between them, which the extraction cannot always drop
# (it drops lines closer than 10 px only once it has seen them apart).
FAMILY_CLEAR_MM = 2.0


@dataclass
class LaserBoardObservation:
    """One board view with laser lines, in the rectified frame C.

    ``T_cb``: board -> C (4x4, mm). ``board_polygon_uv``: (4, 2) image polygon of the usable board
    area. ``line_uv``: laser name -> (N, 2) line pixel centres in the rectified left image.
    ``region``: default region for :func:`board_points_from_line` (see the module docstring);
    ``page``: layout of the printed page (needed for ``"margin"`` / ``"both"``); ``margin_mm``:
    clearance from the pattern, ink and paper edges used by the margin region. ``lit``: the one
    laser that was on in this view (None: all of them); see the module docstring.
    """

    T_cb: np.ndarray
    board_polygon_uv: np.ndarray
    line_uv: dict[str, np.ndarray] = field(default_factory=dict)
    n_corners: int = 0
    reproj_rms_px: float = float("nan")
    region: str = "board"
    page: CharucoPageLayout | None = None
    margin_mm: float = 5.0
    lit: str | None = None


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
    return _board_points(obs, K, uv, region)


def _board_points(obs: LaserBoardObservation, K: np.ndarray, uv: np.ndarray, region: str) -> np.ndarray:
    """:func:`board_points_from_line` for the pixels ``uv`` (N, 2) and a checked ``region``."""
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


def family_points(obs: LaserBoardObservation, K: np.ndarray, family: str, region: str | None = None) -> np.ndarray:
    """3D points (N, 3) in C of every line of ``family`` (``"vertical"`` / ``"horizontal"``) on the
    board of one observation: the pixels of all ``obs.line_uv`` keys of that family, as
    :func:`board_points_from_line`."""
    region = _check_region(obs.region if region is None else region)
    uvs = [np.asarray(v, dtype=np.float64).reshape(-1, 2) for k, v in obs.line_uv.items()
           if is_line_name(k) and line_family(k) == family and v is not None and len(v)]
    if not uvs:
        return np.zeros((0, 3))
    return _board_points(obs, K, np.vstack(uvs), region)


def plane_intercept(plane: LaserPlane | tuple[np.ndarray, float], family: str) -> float:
    """Where a sheet of ``family`` starts (mm): the x at which the plane meets the camera's x axis
    (vertical family) or the y at which it meets the y axis (horizontal family). The lasers of a
    family are named in this order: vertical largest x first, horizontal smallest y first."""
    if isinstance(plane, LaserPlane):
        n, d = plane.normal, float(plane.d)
    else:
        n, d = np.asarray(plane[0], dtype=np.float64), float(plane[1])
    c = float(n[0] if family == "vertical" else n[1])
    if abs(c) < 1e-12:  # the sheet is parallel to that axis
        return 0.0 if d == 0 else math.copysign(math.inf, -d if c >= 0 else d)
    return -d / c


def _families(names: Sequence[str]) -> dict[str, list[str]]:
    """Names grouped by family, in the given order; a name that is not a laser line is its own group."""
    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(line_family(name) if is_line_name(name) else name, []).append(name)
    return groups


def _nearest(P: np.ndarray, planes: Sequence[tuple[np.ndarray, float]]) -> np.ndarray:
    """Index (N,) of the nearest plane for each point."""
    if len(P) == 0:
        return np.zeros(0, dtype=np.int64)
    dist = np.abs(np.stack([P @ n + d for n, d in planes], axis=1))
    return np.argmin(dist, axis=1)


def points_by_laser(obs: LaserBoardObservation, K: np.ndarray, planes: Mapping[str, LaserPlane],
                    region: str | None = None, clear_mm: float | None = None) -> dict[str, np.ndarray]:
    """3D board points (N, 3) of one observation per laser of ``planes``.

    A family with one plane gets the points of its line (:func:`board_points_from_line` for the
    plane's name when ``obs.line_uv`` has it, else all lines of the family). A family with several
    planes gets the points of all its lines, each point given to the nearest of them; points
    closer than ``clear_mm`` (default :data:`FAMILY_CLEAR_MM`) to another plane of the family are
    left out, as in the fit.
    """
    clear = FAMILY_CLEAR_MM if clear_mm is None else float(clear_mm)
    out: dict[str, np.ndarray] = {}
    for fam, names in _families(list(planes)).items():
        if len(names) == 1:
            name = names[0]
            if name in obs.line_uv or fam not in LINE_NAMES:
                out[name] = board_points_from_line(obs, K, name, region)
            else:
                out[name] = family_points(obs, K, fam, region)
            continue
        P = family_points(obs, K, fam, region)
        lab = family_labels(P, [(planes[n].normal, planes[n].d) for n in names], clear)
        for j, name in enumerate(names):
            out[name] = P[lab == j]
    return out


def family_labels(P: np.ndarray, sheets: Sequence[tuple[np.ndarray, float]], clear_mm: float = FAMILY_CLEAR_MM) -> np.ndarray:
    """Index (N,) of the nearest sheet ``(normal, d)`` for each point (N, 3), -1 for a point closer
    than ``clear_mm`` to another sheet (there the lines of the family merge)."""
    lab = _nearest(P, sheets)
    if len(P) == 0 or len(sheets) < 2 or clear_mm <= 0:
        return lab
    dist = np.abs(np.stack([P @ n + d for n, d in sheets], axis=1))
    dist[np.arange(len(P)), lab] = np.inf
    return np.where(dist.min(axis=1) < clear_mm, -1, lab)


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

    ``names``: the lasers, any number per family; within a family they are named, in the order
    given, by where each sheet starts (module docstring, "Any set of line lasers"). ``region``
    (``"board"`` | ``"margin"`` | ``"both"``; default: each observation's own) selects which line
    pixels are used. Returns ``(planes, report)``, ``planes`` in the order of ``names``; it only
    holds the lasers that could be fitted. For every requested name ``report["lasers"][name]``
    gives ``ok``, ``degenerate``, ``reason`` (when not ok), point/observation counts, ``rms_mm``,
    the plane, rough uncertainties and ``intercept_mm`` (:func:`plane_intercept`).
    ``report["families"][family]`` gives its ``names`` and ``planes_found`` (with several names
    also ``n_points`` and the separated sheets' ``intercepts_mm``). ``report["ok"]`` is True when
    all requested lasers were fitted.
    """
    K = np.asarray(K, dtype=np.float64)
    if region is not None:
        _check_region(region)
    names = tuple(names)
    planes: dict[str, LaserPlane] = {}
    used = sorted({o.region for o in observations}) if region is None else [region]
    report: dict = {"n_observations": len(observations), "region": "+".join(used) if used else region,
                    "lasers": {}, "families": {}}
    fitted: dict[str, LaserPlane | None] = {}
    for fam, fam_names in _families(names).items():
        lit_each = len(fam_names) > 1 and any(o.lit in fam_names for o in observations)
        if len(fam_names) == 1 or lit_each:
            group = {}
            for name in fam_names:
                # an observation counts for a laser when all lasers were on or this one was
                use = [i for i, o in enumerate(observations) if o.lit is None or o.lit == name]
                if lit_each:
                    use = [i for i in use if observations[i].lit == name]
                per_obs = [(i, board_points_from_line(observations[i], K, name, region)) for i in use]
                if fam in LINE_NAMES and name != fam and not any(len(P) for _, P in per_obs):
                    # a laser of the family under another name: the family's pixels are its line
                    per_obs = [(i, family_points(observations[i], K, fam, region)) for i in use]
                group[name] = _fit_one(name, per_obs, observations, ransac_thresh_mm)
            if fam in LINE_NAMES:
                report["families"][fam] = {"names": list(fam_names),
                                           "planes_found": int(sum(pl is not None for pl, _ in group.values()))}
                if lit_each:
                    report["families"][fam]["one_at_a_time"] = True
        else:
            group = _fit_family(fam, fam_names, observations, K, region, ransac_thresh_mm, report)
        for name, (plane, entry) in group.items():
            if plane is not None and fam in LINE_NAMES:
                entry["intercept_mm"] = plane_intercept(plane, fam)
            report["lasers"][name] = entry
            fitted[name] = plane
    for name in names:
        plane, entry = fitted[name], report["lasers"][name]
        if plane is None:
            log.warning("laser plane %r not fitted: %s", name, entry.get("reason"))
            continue
        planes[name] = plane
        log.info("laser plane %r: n=(%.4f, %.4f, %.4f) d=%.2f mm, rms %.3f mm, %d inliers from %d observations",
                 name, *plane.normal, plane.d, plane.rms, plane.n_points, entry["n_observations"])
    report["ok"] = len(planes) == len(names)
    return planes, report


def _fit_family(fam: str, names: Sequence[str], observations: Sequence[LaserBoardObservation], K: np.ndarray,
                region: str | None, thresh: float, report: dict) -> dict[str, tuple[LaserPlane | None, dict]]:
    """Separate, name and fit the sheets of a family with several lasers (module docstring)."""
    per_obs = [(i, family_points(o, K, fam, region)) for i, o in enumerate(observations)]
    sheets: list[tuple[np.ndarray, float]] = []
    remaining = [P for _, P in per_obs]
    for j in range(len(names)):
        use = [P for P in remaining if len(P) >= MIN_POINTS_PER_OBS]
        if not use:
            break
        try:
            n, d, _, _ = fit_plane_ransac(np.vstack(use), thresh=thresh, iters=1000, seed=j,
                                          min_inliers=3 * MIN_POINTS_PER_OBS)
        except ValueError:
            break
        inl = [np.abs(P @ n + d) < thresh for P in remaining]
        support = [observations[i].T_cb for (i, _), m in zip(per_obs, inl) if m.sum() >= MIN_POINTS_PER_OBS]
        if _count_distinct_poses(support) < MIN_DISTINCT_POSES:  # one view's board plane, not a sheet
            break
        sheets.append((n, float(d)))
        remaining = [P[~m] for P, m in zip(remaining, inl)]
    n_total = int(sum(len(P) for _, P in per_obs))
    fam_rep: dict = {"names": list(names), "planes_found": len(sheets), "n_points": n_total}
    report["families"][fam] = fam_rep
    if len(sheets) < len(names):
        why = (f"found {len(sheets)} of {len(names)} {fam} laser lines in the {n_total} points of the {fam} "
               f"lines, so which laser is which cannot be told: every {fam} line ({', '.join(names)}) must "
               "cross the board in several views at different distances and tilts")
        return {name: (None, {"ok": False, "degenerate": False, "n_observations": 0, "n_points": 0, "reason": why})
                for name in names}
    # name the sheets by where they start, give each point to the nearest sheet, fit each on its points
    sheets.sort(key=lambda s: plane_intercept(s, fam), reverse=(fam == "vertical"))
    fam_rep["intercepts_mm"] = [plane_intercept(s, fam) for s in sheets]
    labels = [family_labels(P, sheets, FAMILY_CLEAR_MM) for _, P in per_obs]
    fam_rep["n_points_near_other_sheet"] = int(sum((lab < 0).sum() for lab in labels))
    return {name: _fit_one(name, [(i, P[lab == j]) for (i, P), lab in zip(per_obs, labels)], observations, thresh)
            for j, name in enumerate(names)}


def check_laser_planes(
    planes: Mapping[str, LaserPlane], observations: Sequence[LaserBoardObservation], K: np.ndarray,
    region: str | None = None,
) -> dict:
    """Residuals (mm) of new board observations against existing laser planes.

    Per laser: point count, signed mean, rms, median / p95 / max absolute distance, the fraction
    within 1 mm and per-observation statistics (the points of a family with several planes each
    on the nearest of them, none near another, :func:`points_by_laser`); ``ok`` when the median |distance| is below
    :data:`CHECK_OK_MEDIAN_MM`. ``report["ok"]`` is False if any laser fails or has no points.
    ``region`` as in :func:`fit_laser_planes`.
    """
    K = np.asarray(K, dtype=np.float64)
    if region is not None:
        _check_region(region)
    used = sorted({o.region for o in observations}) if region is None else [region]
    report: dict = {"n_observations": len(observations), "region": "+".join(used) if used else region, "lasers": {}}
    all_ok = True
    by_obs = [points_by_laser(o, K, planes, region) for o in observations]
    for name, plane in planes.items():
        rows, dists = [], []
        for i, o in enumerate(observations):
            P = by_obs[i][name]
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
                "the laser may have moved; recalibrate the laser lines"
            ),
        }
        log.info("laser check %r: median |d| %.3f mm over %d points (%s)", name, med, len(dd), "ok" if ok else "BAD")
    report["ok"] = bool(all_ok and len(planes) > 0)
    return report
