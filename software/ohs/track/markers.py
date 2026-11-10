"""AprilTag (tag36h11) marker tracking with a stereo-triangulated tag map.

:class:`MarkerTracker` detects tags in both *rectified* images, triangulates the corners of every
tag seen in both (same id, rows agree within ``max_dv_px``, positive disparity, plausible depth,
edges and diagonals within ``size_tol`` of the true tag shape), and estimates the camera pose:

* the map is initialised from the first frame with at least ``min_tags`` stereo tags, and that
  camera frame becomes the world frame (``T_wc = I``);
* Kabsch (3D-3D) between observed corners (frame ``C``) and map corners (world ``W``), with RANSAC
  over tag pairs when three or more known tags are seen (a tag is an inlier if all four corners
  agree within ``ransac_thresh_mm``); the inlier corner RMS must be below ``max_rms_mm``;
* the pose is then refined by Gauss-Newton on the reprojection error of all known tags detected
  in *either* image (tags seen in one image only still help; with few stereo tags a two-tag Kabsch
  pose is weak about the axis joining them); the RMS must be below ``max_reproj_px``;
* every stereo observation is stored; a tag's map entry is the mean of its largest set of
  mutually consistent observations (``merge_thresh_mm``), so a bad first triangulation is
  outvoted later and a knocked tag stays at its old place until the new one has more support.
  :meth:`MarkerTracker.refine_map` runs alternating least squares (poses <-> tag corners, first
  frame fixed) over the consistent observations;
* if fewer than ``min_tags`` known tags are seen in stereo, a single-image fallback runs PnP on the
  left image (``SOLVEPNP_IPPE_SQUARE`` for one tag, keeping the planar-ambiguity solution closest
  to the last pose; ``SOLVEPNP_SQPNP`` + LM for more), followed by the same refinement.

Corner order is OpenCV's (clockwise from the tag's top-left, as drawn by
``cv2.aruco.generateImageMarker``). ``tag_mm`` is the edge of the black square (the detected
outline), i.e. the AprilTag "tag size". Rectified images use the ideal pinhole ``K`` for both
cameras and ``x_right = x_left - baseline`` (ARCHITECTURE.md section 2).

**Projected dots.** With the dot projector on (10k dots of 2-3 px, ~130 DN, over ~30-40 DN of tag
contrast) plain ``cv2.aruco`` finds almost nothing. Detection therefore runs on a prefiltered image
(``prefilter="open"``: grey-scale opening with a disc of ~5 px at full resolution, 3 px at half)
with detector settings tuned for damaged low-contrast tags (``error_correction_rate``,
``adaptive_thresh_constant``); the corners are then refined on the *unfiltered* image with a dot
mask (:meth:`MarkerTracker.dot_mask`) excluding contaminated samples. Synthetic result at
1280x800: every frame tracked, pose error 0.03-0.2 deg / 0.15-0.8 mm, map error <= 0.4 mm. At
640x400 tag cells are ~3.5 px and the dots are 4x denser per pixel; tracking is *not* reliable
there (most frames fail safely with ``ok=False``): run marker mode at full resolution or dim the dots.

Corner refinement (``MarkerParams.corner_refine``; errors on supersampled synthetic renders with
OpenCV 5.0): the default ``"edges"`` fits the four edges of the black square
(:func:`refine_square_corners`) starting from the detector's unrefined corners: median ~0.03 px
at both 1280x800 and 640x400, a few ms per image; tags whose fit fails are dropped. OpenCV's
``"subpix"`` (``cornerSubPix``) pulls corners ~0.2 px (~1 %) inward and degrades on small tags
(median 0.46 px, max 2 px at 640x400); ``"apriltag"`` is accurate (~0.13 px) but ~300 ms per
1280x800 image and reports corners in AprilTag's pixel-corner convention (shifted by -0.5 px here).
"""
from __future__ import annotations

import itertools
import logging
import warnings
from dataclasses import dataclass

import cv2
import numpy as np

from ohs.geometry import invert_se3, kabsch, make_se3, rotation_angle_deg, so3_exp, transform_points
from ohs.types import TrackResult

__all__ = ["MarkerParams", "MarkerTracker", "tag_corners_local", "refine_square_corners"]

log = logging.getLogger(__name__)

_REFINE = {
    "edges": cv2.aruco.CORNER_REFINE_NONE,  # detector corners, then refine_square_corners()
    "none": cv2.aruco.CORNER_REFINE_NONE,
    "subpix": cv2.aruco.CORNER_REFINE_SUBPIX,
    "contour": cv2.aruco.CORNER_REFINE_CONTOUR,
    "apriltag": cv2.aruco.CORNER_REFINE_APRILTAG,
}


def _fit_lines(pts: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Weighted total-least-squares lines through point sets.

    ``pts`` (..., m, 2), ``w`` (..., m) weights (0 = unused). Returns ``(centroid, direction,
    residuals)`` with shapes (..., 2), (..., 2), (..., m).
    """
    ws = np.maximum(w.sum(axis=-1, keepdims=True), 1e-12)
    c = (pts * w[..., None]).sum(axis=-2) / ws
    d = pts - c[..., None, :]
    sxx = (w * d[..., 0] ** 2).sum(axis=-1)
    syy = (w * d[..., 1] ** 2).sum(axis=-1)
    sxy = (w * d[..., 0] * d[..., 1]).sum(axis=-1)
    th = 0.5 * np.arctan2(2.0 * sxy, sxx - syy)  # principal axis of the 2x2 scatter matrix
    u = np.stack([np.cos(th), np.sin(th)], axis=-1)
    res = d[..., 0] * -u[..., None, 1] + d[..., 1] * u[..., None, 0]
    return c, u, res


def refine_square_corners(img: np.ndarray, corners: np.ndarray, cells: int = 8, n_samples: int = 24,
                          n_steps: int = 15, min_contrast: float = 15.0, passes: int = 2,
                          mask: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Sub-pixel corners of dark squares on a bright surround by fitting their four edges.

    For every edge, ``n_samples`` intensity profiles are taken along the outward normal (+-0.9
    of a tag cell, so they stay inside the black border and the white margin). The edge's dark
    and bright levels are the medians of the inner and outer thirds of its profiles; each edge
    point is the dark-to-bright crossing of the mid level nearest the current edge estimate. A
    robust total least-squares line is fitted per edge and adjacent lines are intersected; two
    passes recentre the profiles. Unlike ``cornerSubPix`` this has no inward bias on convex
    corners and keeps working on small tags (median error ~0.03 px on 20-30 px tags in synthetic
    tests).

    ``mask`` (optional, image-sized, non-zero = contaminated, e.g. projector dots) excludes
    samples from the level estimates and rejects edge points whose crossing touches it, so the
    edges can be fitted on the unfiltered image.

    ``corners`` (T,4,2) in pixel-centre coordinates; ``cells`` = cells across the black square
    (8 for tag36h11 with a 1-bit border). Returns the refined corners (T,4,2) and a per-tag
    success flag (tags that fail keep their input corners).
    """
    g = np.ascontiguousarray(img, dtype=np.float32)
    mk = None if mask is None else np.ascontiguousarray(mask, dtype=np.float32)
    C = np.asarray(corners, dtype=np.float64).reshape(-1, 4, 2)
    ok_any = np.zeros(len(C), dtype=bool)
    if len(C) == 0:
        return C.copy(), ok_any
    s = np.linspace(0.1, 0.9, n_samples)  # stay away from the corners
    base = np.linspace(-0.9, 0.9, n_steps)
    centre = (n_steps - 1) / 2.0
    third = max(2, n_steps // 3)
    for _ in range(passes):
        a, b = C, np.roll(C, -1, axis=1)  # edge k runs from corner k to corner k+1
        L = np.linalg.norm(b - a, axis=2)
        u = (b - a) / np.maximum(L, 1e-9)[..., None]
        n = np.stack([u[..., 1], -u[..., 0]], axis=-1)
        n *= np.sign(np.sum(n * (0.5 * (a + b) - C.mean(axis=1, keepdims=True)), axis=-1, keepdims=True))
        cell = L.mean(axis=1) / cells
        taus = base[None, :] * cell[:, None]  # (T,t)
        P = a[:, :, None, :] + (s[None, None, :, None] * L[:, :, None, None]) * u[:, :, None, :]
        Q = P[:, :, :, None, :] + taus[:, None, None, :, None] * n[:, :, None, None, :]
        q = Q.reshape(-1, n_steps, 2).astype(np.float32)
        prof = cv2.remap(g, q[..., 0], q[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        prof = prof.astype(np.float64)
        if mk is None:
            dirty = np.zeros(prof.shape, dtype=bool)
        else:
            dirty = cv2.remap(mk, q[..., 0], q[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE) > 0
        # per-edge dark / bright levels from the clean inner / outer thirds of all its profiles
        per_edge = (-1, n_samples * third)
        inner = np.where(dirty[:, :third], np.nan, prof[:, :third]).reshape(per_edge)
        outer = np.where(dirty[:, -third:], np.nan, prof[:, -third:]).reshape(per_edge)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN edges are handled below
            lo_e, hi_e = np.nanmedian(inner, axis=1), np.nanmedian(outer, axis=1)
        lo, hi = np.repeat(lo_e, n_samples), np.repeat(hi_e, n_samples)
        thr = 0.5 * (lo + hi)
        bright = prof > thr[:, None]
        up = ~bright[:, :-1] & bright[:, 1:]
        dist = np.where(up, np.abs(np.arange(n_steps - 1) + 0.5 - centre), np.inf)
        j = np.argmin(dist, axis=1)
        r = np.arange(len(j))
        clean = ~(dirty[r, j] | dirty[r, j + 1])  # the two samples that define the crossing
        valid = np.isfinite(thr) & ((hi - lo) > min_contrast) & np.isfinite(dist[r, j]) & clean
        p0, p1 = prof[r, j], prof[r, j + 1]
        frac = np.clip((thr - p0) / np.where(p1 != p0, p1 - p0, 1.0), 0.0, 1.0)
        tt = np.repeat(taus, 4 * n_samples, axis=0)
        tau = tt[r, j] + frac * (tt[r, j + 1] - tt[r, j])
        pts = (P.reshape(-1, 2) + tau[:, None] * np.repeat(n.reshape(-1, 2), n_samples, axis=0)).reshape(-1, 4, n_samples, 2)
        w = valid.reshape(-1, 4, n_samples).astype(np.float64)
        c, d, res = _fit_lines(pts, w)
        sd = np.sqrt((w * res ** 2).sum(axis=-1) / np.maximum(w.sum(axis=-1), 1.0))
        w *= np.abs(res) < np.maximum(0.25, 3.0 * sd)[..., None]  # one robust re-fit
        c, d, _ = _fit_lines(pts, w)
        good = np.all(w.sum(axis=-1) >= 4, axis=1)
        # corner k = intersection of edge k-1 and edge k (Cramer's rule)
        c1, d1 = np.roll(c, 1, axis=1), np.roll(d, 1, axis=1)
        det = d1[..., 0] * -d[..., 1] + d[..., 0] * d1[..., 1]
        rhs = c - c1
        t1 = (rhs[..., 0] * -d[..., 1] + d[..., 0] * rhs[..., 1]) / np.where(np.abs(det) > 1e-3, det, 1.0)
        new = c1 + t1[..., None] * d1
        good &= np.all(np.abs(det) > 1e-3, axis=1)
        good &= np.linalg.norm(new - C, axis=2).max(axis=1) < cell  # did not run away
        C = np.where(good[:, None, None], new, C)
        ok_any |= good
    return C, ok_any


@dataclass
class MarkerParams:
    """Settings for :class:`MarkerTracker` (lengths in mm, image quantities in pixels)."""

    dictionary: str = "DICT_APRILTAG_36h11"
    tag_mm: float = 20.0  # edge length of the black square
    min_tags: int = 2  # stereo tags needed to initialise the map and for a stereo pose
    corner_refine: str = "edges"  # "edges" | "subpix" | "apriltag" | "contour" | "none"
    # projected-dot suppression before detection: "open" (grey-scale opening, removes small bright
    # blobs, keeps edges in place), "median", or "none"; kernel 0 = auto (5 px at fx ~720, 3 px at ~360)
    prefilter: str = "open"
    prefilter_px: int = 0
    # top-hat (image - opening) above this many DN marks a projector dot; the dilated dot mask keeps
    # dots out of the corner refinement, which then runs on the unfiltered image (0 = no mask)
    dot_min_dn: float = 40.0
    # cv2.aruco detector settings tuned for low-contrast tags partly covered by dots
    # (OpenCV defaults: 0.6 and 7.0)
    error_correction_rate: float = 1.0
    adaptive_thresh_constant: float = 3.0
    max_dv_px: float = 2.0  # rectified rows of matching corners must agree
    min_depth_mm: float = 50.0
    max_depth_mm: float = 1500.0
    size_tol: float = 0.06  # triangulated edges (and diagonals / sqrt 2) within +-6 % of tag_mm
    ransac_thresh_mm: float = 2.0
    ransac_max_pairs: int = 60
    max_rms_mm: float = 0.6  # inlier corner RMS gate of the stereo pose
    merge_thresh_mm: float = 2.0  # observations of a tag within this distance form its consensus
    max_obs_per_tag: int = 200  # newest world-frame observations kept per tag
    pnp_fallback: bool = True
    max_reproj_px: float = 1.0  # RMS reprojection gate of the PnP fallback

    def __post_init__(self) -> None:
        if self.corner_refine not in _REFINE:
            raise ValueError(f"corner_refine must be one of {sorted(_REFINE)}")
        if self.prefilter not in ("open", "median", "none"):
            raise ValueError("prefilter must be 'open', 'median' or 'none'")
        if not hasattr(cv2.aruco, self.dictionary):
            raise ValueError(f"unknown cv2.aruco dictionary {self.dictionary!r}")


def tag_corners_local(tag_mm: float) -> np.ndarray:
    """Corners (4,3) of a tag in its own frame (x right, y up, z out of the tag), OpenCV order.

    This is the object-point layout ``cv2.SOLVEPNP_IPPE_SQUARE`` expects.
    """
    s = tag_mm / 2.0
    return np.array([[-s, s, 0.0], [s, s, 0.0], [s, -s, 0.0], [-s, -s, 0.0]])


class MarkerTracker:
    """Stereo AprilTag tracker with an incrementally built tag map (world = first map frame).

    Parameters
    ----------
    K:
        3x3 rectified intrinsics (shared by both rectified images).
    baseline_mm:
        Rectified baseline (positive, mm).
    params:
        :class:`MarkerParams` (defaults if ``None``).
    """

    def __init__(self, K: np.ndarray, baseline_mm: float, params: MarkerParams | None = None) -> None:
        self.params = params if params is not None else MarkerParams()
        self.K = np.asarray(K, dtype=np.float64).reshape(3, 3).copy()
        self.baseline = float(baseline_mm)
        if self.baseline <= 0:
            raise ValueError("baseline_mm must be positive")
        p = self.params
        dp = cv2.aruco.DetectorParameters()
        dp.cornerRefinementMethod = _REFINE[p.corner_refine]
        dp.errorCorrectionRate = float(p.error_correction_rate)
        dp.adaptiveThreshConstant = float(p.adaptive_thresh_constant)
        if p.corner_refine == "subpix":
            dp.cornerRefinementMinAccuracy = 0.01
            dp.cornerRefinementMaxIterations = 50
        dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, p.dictionary))
        self._detector = cv2.aruco.ArucoDetector(dictionary, dp)
        self._cells = int(dictionary.markerSize) + 2 * int(dp.markerBorderBits)  # cells across the black square
        self._corner_offset = -0.5 if p.corner_refine == "apriltag" else 0.0
        scale = self.K[0, 0] / 720.0  # 1 at full resolution of the nominal camera
        k = int(p.prefilter_px) or max(3, int(round(5.0 * scale)))
        self.prefilter_px = k + 1 if k % 2 == 0 else k  # odd kernel
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (self.prefilter_px, self.prefilter_px))
        # a dot (2-3 px FWHM at full resolution) incl. its tails (< ~5 DN) fits in a ~3 px disc
        r = max(2, int(round(3.0 * scale)))
        self._dilate = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        # annulus just outside a dot core: a dot is brighter than nearly all of it, while the
        # annulus around tag structure (cells, the white margin strip) crosses many bright pixels
        yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
        dist = np.hypot(xx, yy)
        ring = (dist > r - 1.5) & (dist <= r + 0.5)
        self._ring_dy, self._ring_dx = yy[ring], xx[ring]
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self) -> None:
        """Forget the map, the stored observations and the trajectory."""
        self._obs_w: dict[int, list[np.ndarray]] = {}  # world-frame observations per tag
        self._map: dict[int, np.ndarray] = {}  # consensus corners per tag
        self._count: dict[int, int] = {}  # observations in the consensus
        self._frames: list[dict] = []  # {"T_wc": (4,4), "obs": {id: (4,3) camera-frame corners}}
        self.trajectory: list[tuple[float, np.ndarray, bool]] = []
        self._T_last = np.eye(4)
        self._initialized = False
        self.last_result: TrackResult | None = None

    @property
    def map(self) -> dict[int, np.ndarray]:
        """Tag id -> (4,3) world corners (mm): mean of the tag's largest consistent set of observations."""
        return {i: X.copy() for i, X in self._map.items()}

    @property
    def n_observations(self) -> dict[int, int]:
        """Tag id -> number of observations in its consensus."""
        return dict(self._count)

    @property
    def initialized(self) -> bool:
        return self._initialized

    # ------------------------------------------------------------------ detection
    @staticmethod
    def _gray(img: np.ndarray) -> np.ndarray:
        g = np.asarray(img)
        if g.ndim == 3:
            g = cv2.cvtColor(g, cv2.COLOR_BGR2GRAY)
        if g.dtype != np.uint8:
            g = np.clip(g, 0, 255).astype(np.uint8)
        return g

    def prefilter_image(self, img: np.ndarray) -> np.ndarray:
        """Grey uint8 image with the projected dot pattern suppressed (``MarkerParams.prefilter``).

        The dots only *add* light in blobs of 2-3 px (full resolution), smaller than a tag cell
        (~7 px for 20 mm tags at 250 mm), so a grey-scale opening with a slightly larger disc
        removes their cores while straight edges stay exactly where they were (the dots' faint
        tails survive, which is why the corners are refined on the unfiltered image instead).
        """
        g = self._gray(img)
        if self.params.prefilter == "open":
            return cv2.morphologyEx(g, cv2.MORPH_OPEN, self._kernel)
        if self.params.prefilter == "median":
            return cv2.medianBlur(g, self.prefilter_px)
        return g

    def dot_mask(self, img: np.ndarray) -> np.ndarray:
        """uint8 mask (1 = projector dot incl. its tails).

        Candidates are local maxima of the top-hat (image - opening) above ``dot_min_dn``. A
        candidate is a dot only if at most a third of an annulus around it (radius ~3.5 px at full
        resolution) is within ``dot_min_dn`` of its brightness: a neighbouring dot covers a small
        arc, tag structure of any contrast (white cells, the white margin strip) a large part.
        Accepted cores are dilated to the dot footprint.
        """
        g = self._gray(img)
        top = cv2.morphologyEx(g, cv2.MORPH_TOPHAT, self._kernel)
        peak = (top > self.params.dot_min_dn) & (top >= cv2.dilate(top, np.ones((3, 3), np.uint8)))
        cy, cx = np.nonzero(peak)
        seeds = np.zeros(g.shape, dtype=np.uint8)
        if len(cy):
            gi = g.astype(np.int16)
            level = gi[cy, cx] - int(self.params.dot_min_dn)
            h, w = g.shape
            bright = np.zeros(len(cy), dtype=np.int32)
            for dy, dx in zip(self._ring_dy, self._ring_dx):
                bright += gi[np.clip(cy + dy, 0, h - 1), np.clip(cx + dx, 0, w - 1)] > level
            isolated = bright <= len(self._ring_dy) // 3
            seeds[cy[isolated], cx[isolated]] = 1
        return cv2.dilate(seeds, self._dilate)

    def detect(self, img: np.ndarray) -> dict[int, np.ndarray]:
        """Tag id -> (4,2) corner pixels (OpenCV pixel-centre convention) in one image."""
        g = self._gray(img)
        corners, ids, _ = self._detector.detectMarkers(self.prefilter_image(g))
        out: dict[int, np.ndarray] = {}
        if ids is None or len(corners) == 0:
            return out
        C = np.array([np.asarray(c, dtype=np.float64).reshape(4, 2) for c in corners]) + self._corner_offset
        ids = np.asarray(ids).ravel()
        keep = np.ones(len(ids), dtype=bool)
        if self.params.corner_refine == "edges":
            mask = self.dot_mask(g) if self.params.dot_min_dn > 0 else None
            C, keep = refine_square_corners(g, C, cells=self._cells, mask=mask)
            if not keep.all():  # detector corners are 1-2.5 px off under dots: too poor to use
                log.debug("edge refinement failed for tags %s (dropped)", ids[~keep].tolist())
        dup = {int(i) for i in ids if np.count_nonzero(ids == i) > 1}
        if dup:  # the same id twice is ambiguous: drop it
            log.debug("tags detected more than once (ignored): %s", sorted(dup))
        for c, i, k in zip(C, ids, keep):
            if k and int(i) not in dup:
                out[int(i)] = c
        return out

    def triangulate(self, left: dict[int, np.ndarray], right: dict[int, np.ndarray]) -> dict[int, np.ndarray]:
        """Camera-frame corners (4,3) of tags detected in both rectified images that pass the checks."""
        p = self.params
        fx, fy, cx, cy = self.K[0, 0], self.K[1, 1], self.K[0, 2], self.K[1, 2]
        out: dict[int, np.ndarray] = {}
        for i in sorted(set(left) & set(right)):
            cl, cr = left[i], right[i]
            if np.any(np.abs(cl[:, 1] - cr[:, 1]) > p.max_dv_px):
                continue
            disp = cl[:, 0] - cr[:, 0]
            if np.any(disp <= 0):
                continue
            Z = fx * self.baseline / disp
            if np.any(Z < p.min_depth_mm) or np.any(Z > p.max_depth_mm):
                continue
            v = 0.5 * (cl[:, 1] + cr[:, 1])
            X = np.stack([(cl[:, 0] - cx) / fx * Z, (v - cy) / fy * Z, Z], axis=1)
            edges = np.linalg.norm(X - np.roll(X, -1, axis=0), axis=1)
            diags = np.linalg.norm(X[:2] - X[2:], axis=1) / np.sqrt(2.0)
            if np.any(np.abs(np.concatenate([edges, diags]) / p.tag_mm - 1.0) > p.size_tol):
                log.debug("tag %d rejected: edges %s, diagonals/sqrt2 %s mm", i, np.round(edges, 2), np.round(diags, 2))
                continue
            out[i] = X
        return out

    # ------------------------------------------------------------------ pose estimation
    def _kabsch_tags(self, obs: dict[int, np.ndarray], ids: list[int], world: dict[int, np.ndarray]) -> np.ndarray:
        """``T_wc`` mapping the observed corners of tags ``ids`` onto their map corners."""
        A = np.concatenate([obs[i] for i in ids])
        B = np.concatenate([world[i] for i in ids])
        R, t = kabsch(A, B)
        return make_se3(R, t)

    @staticmethod
    def _tag_errors(T: np.ndarray, obs: dict[int, np.ndarray], world: dict[int, np.ndarray], ids) -> dict[int, float]:
        """Largest corner distance (mm) per tag after mapping observations with ``T``."""
        return {i: float(np.max(np.linalg.norm(transform_points(T, obs[i]) - world[i], axis=1))) for i in ids}

    def _stereo_pose(self, obs: dict[int, np.ndarray],
                     world: dict[int, np.ndarray]) -> tuple[np.ndarray, list[int], float] | None:
        """RANSAC-over-tags Kabsch; returns (T_wc, inlier ids, rms) or None."""
        p = self.params
        ids = sorted(set(obs) & set(world))
        if len(ids) < max(p.min_tags, 1):
            return None
        inliers = ids
        if len(ids) >= 3:
            pairs = list(itertools.combinations(ids, 2))
            if len(pairs) > p.ransac_max_pairs:
                rng = np.random.default_rng(len(self._frames))
                pairs = [pairs[k] for k in rng.choice(len(pairs), p.ransac_max_pairs, replace=False)]
            best_key = None
            for pair in pairs:
                T = self._kabsch_tags(obs, list(pair), world)
                err = self._tag_errors(T, obs, world, ids)
                inl = [i for i in ids if err[i] < p.ransac_thresh_mm]
                key = (len(inl), -sum(err[i] for i in inl))
                if best_key is None or key > best_key:
                    best_key, inliers = key, inl
        if len(inliers) < max(p.min_tags, 1):
            return None
        T = self._kabsch_tags(obs, inliers, world)
        res = np.concatenate([transform_points(T, obs[i]) - world[i] for i in inliers])
        rms = float(np.sqrt(np.mean(np.sum(res ** 2, axis=1))))
        return T, inliers, rms

    def _refine_reprojection(self, T_wc: np.ndarray, left: dict[int, np.ndarray], right: dict[int, np.ndarray],
                             world: dict[int, np.ndarray], exclude: frozenset[int] | set[int] = frozenset(),
                             iters: int = 10) -> tuple[np.ndarray, float, int] | None:
        """Gauss-Newton on the reprojection error of all known tags in *both* rectified images.

        Uses tags seen in only one image too (with few stereo tags, e.g. under the dot pattern,
        a two-tag Kabsch pose is weak about the axis joining the tags). Huber weights (1 px);
        tags reprojecting worse than ``outlier_px`` are dropped once the pose has settled.
        Returns ``(T_wc, rms_px, n_tags_used)`` or ``None``.
        """
        rows = [(world[i], c, 0.0, i) for i, c in left.items() if i in world and i not in exclude]
        rows += [(world[i], c, 1.0, i) for i, c in right.items() if i in world and i not in exclude]
        if not rows:
            return None
        Xw = np.concatenate([r[0] for r in rows])
        uv = np.concatenate([r[1] for r in rows])
        side = np.repeat([r[2] for r in rows], 4)
        key = np.repeat(np.arange(len(rows)), 4)
        fx, fy, cx, cy = self.K[0, 0], self.K[1, 1], self.K[0, 2], self.K[1, 2]
        b = np.array([self.baseline, 0.0, 0.0])
        outlier_px = max(1.5, 3.0 * fx / 720.0)
        T_cw = invert_se3(T_wc)
        R, t = T_cw[:3, :3].copy(), T_cw[:3, 3].copy()
        use = np.ones(len(Xw), dtype=bool)
        for it in range(iters):
            Pc = Xw @ R.T + t
            P = Pc - side[:, None] * b  # right-camera rows: x_right = x_left - baseline
            z = P[:, 2]
            if np.any(z[use] <= 1.0):
                return None
            u, v = fx * P[:, 0] / z + cx, fy * P[:, 1] / z + cy
            res = np.stack([u - uv[:, 0], v - uv[:, 1]], axis=1)
            if it in (3, 6):  # drop tags that do not fit once the pose has settled
                err = np.linalg.norm(res, axis=1)
                bad = {k for k in np.unique(key) if err[key == k].max() > outlier_px}
                use = ~np.isin(key, list(bad))
            if use.sum() < 4:
                return None
            Ju = np.stack([fx / z, np.zeros_like(z), -fx * P[:, 0] / z ** 2], axis=1)
            Jv = np.stack([np.zeros_like(z), fy / z, -fy * P[:, 1] / z ** 2], axis=1)
            J = np.concatenate([np.concatenate([np.cross(Pc, Ju), Ju], axis=1),
                                np.concatenate([np.cross(Pc, Jv), Jv], axis=1)])[np.tile(use, 2)]
            r = np.concatenate([res[:, 0], res[:, 1]])[np.tile(use, 2)]
            nrm = np.tile(np.linalg.norm(res, axis=1), 2)[np.tile(use, 2)]
            w = np.where(nrm <= 1.0, 1.0, 1.0 / np.maximum(nrm, 1e-9))  # Huber, delta = 1 px
            H = (J * w[:, None]).T @ J
            xi = -np.linalg.solve(H + 1e-9 * np.trace(H) * np.eye(6), (J * w[:, None]).T @ r)
            dR = so3_exp(xi[:3])
            R, t = dR @ R, dR @ t + xi[3:]
            if np.linalg.norm(xi[:3]) < 1e-8 and np.linalg.norm(xi[3:]) < 1e-6 and it >= 6:
                break
        Pc = Xw @ R.T + t
        P = Pc - side[:, None] * b
        res = np.stack([fx * P[:, 0] / P[:, 2] + cx - uv[:, 0], fy * P[:, 1] / P[:, 2] + cy - uv[:, 1]], axis=1)
        rms = float(np.sqrt(np.mean(np.sum(res[use] ** 2, axis=1))))
        n_tags = len({rows[k][3] for k in np.unique(key[use])})
        return invert_se3(make_se3(R, t)), rms, n_tags

    def _pnp_pose(self, left: dict[int, np.ndarray],
                  world: dict[int, np.ndarray]) -> tuple[np.ndarray, list[int], float] | None:
        """Single-image pose from known tags in the left image; returns (T_wc, ids, reproj_rms) or None."""
        p = self.params
        ids = sorted(set(left) & set(world))
        if not ids:
            return None
        if len(ids) == 1:
            i = ids[0]
            # IPPE returns both solutions of the planar pose ambiguity; small, frontal tags flip
            # easily under noise, so the one closest to the last pose is kept
            n_sol, rvecs, tvecs, _ = cv2.solvePnPGeneric(tag_corners_local(p.tag_mm), left[i], self.K, None,
                                                         flags=cv2.SOLVEPNP_IPPE_SQUARE)
            if not n_sol:
                return None
            R, t = kabsch(tag_corners_local(p.tag_mm), world[i])  # tag pose in the world (map corners)
            T_wt = make_se3(R, t)
            cands = [T_wt @ invert_se3(make_se3(cv2.Rodrigues(rv)[0], tv.ravel())) for rv, tv in zip(rvecs, tvecs)]
            T_wc = min(cands, key=lambda T: rotation_angle_deg(self._T_last[:3, :3].T @ T[:3, :3]))
        else:
            obj = np.concatenate([world[i] for i in ids])
            img = np.concatenate([left[i] for i in ids])
            ok, rvec, tvec = cv2.solvePnP(obj, img, self.K, None, flags=cv2.SOLVEPNP_SQPNP)
            if not ok:
                return None
            rvec, tvec = cv2.solvePnPRefineLM(obj, img, self.K, None, rvec, tvec)
            T_wc = invert_se3(make_se3(cv2.Rodrigues(rvec)[0], tvec.ravel()))
        T_cw = invert_se3(T_wc)
        obj = np.concatenate([world[i] for i in ids])
        img = np.concatenate([left[i] for i in ids])
        proj, _ = cv2.projectPoints(obj, cv2.Rodrigues(T_cw[:3, :3])[0], T_cw[:3, 3], self.K, None)
        rms = float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - img) ** 2, axis=1))))
        return T_wc, ids, rms

    # ------------------------------------------------------------------ map
    @staticmethod
    def _consensus(obs: list[np.ndarray], thr: float) -> tuple[np.ndarray, np.ndarray]:
        """Mean of the largest set of observations (n,4,3) within ``thr`` of one of them.

        Returns ``(corners (4,3), member mask (n,))``. Ties go to the older observation, so with
        two disagreeing observations the first is kept until a third one decides.
        """
        A = np.stack(obs)
        n = len(A)
        close = np.empty((n, n), dtype=bool)
        for r0 in range(0, n, 64):  # in blocks of rows: the (n,n,4,3) difference never exists whole
            # max corner distance of a block of rows against all observations
            D = np.linalg.norm(A[r0:r0 + 64, None] - A[None, :], axis=3).max(axis=2)
            close[r0:r0 + 64] = D < thr
        best = int(np.argmax(close.sum(axis=1)))  # argmax returns the first (oldest) on ties
        members = close[best]
        return A[members].mean(axis=0), members

    def _add_observation(self, i: int, X_w: np.ndarray) -> None:
        """Store a world-frame observation of tag ``i`` and update its consensus map entry."""
        lst = self._obs_w.setdefault(i, [])
        lst.append(np.asarray(X_w, dtype=np.float64).copy())
        if len(lst) > self.params.max_obs_per_tag:
            del lst[0]
        self._map[i], members = self._consensus(lst, self.params.merge_thresh_mm)
        self._count[i] = int(members.sum())

    def refine_map(self, iterations: int = 20, tol_mm: float = 1e-5) -> dict:
        """Alternating least squares over the stored stereo observations.

        Repeats (1) tag corners = consensus mean of their world-mapped observations and (2)
        per-frame poses = Kabsch of the frame's consensus observations onto the map, with the
        first (map-defining) frame held at its pose to fix the gauge. Observations outside a
        tag's consensus (moved tags, bad triangulations) are ignored. Updates :attr:`map` and the
        stored poses and returns ``{"rms_mm", "iterations", "n_frames", "n_tags"}``.
        """
        thr = self.params.merge_thresh_mm
        if not self._frames:
            return {"rms_mm": float("nan"), "iterations": 0, "n_frames": 0, "n_tags": 0}
        prev = None
        it, rms = 0, float("nan")
        world: dict[int, np.ndarray] = {}
        for it in range(1, iterations + 1):
            per_tag: dict[int, list[np.ndarray]] = {}
            for f in self._frames:
                for i, X in f["obs"].items():
                    per_tag.setdefault(i, []).append(transform_points(f["T_wc"], X))
            world = {i: self._consensus(v, thr)[0] for i, v in per_tag.items()}
            for f in self._frames[1:]:
                ids = [i for i, X in f["obs"].items()
                       if np.max(np.linalg.norm(transform_points(f["T_wc"], X) - world[i], axis=1)) < thr]
                if ids:
                    f["T_wc"] = self._kabsch_tags(f["obs"], ids, world)
            res = [transform_points(f["T_wc"], X) - world[i] for f in self._frames for i, X in f["obs"].items()]
            res = np.concatenate([r for r in res if np.max(np.linalg.norm(r, axis=1)) < thr] or [np.zeros((1, 3))])
            rms = float(np.sqrt(np.mean(np.sum(res ** 2, axis=1))))
            if prev is not None and abs(prev - rms) < tol_mm:
                break
            prev = rms
        self._obs_w = {}
        for f in self._frames:  # rebuild the per-tag observations with the refined poses
            for i, X in f["obs"].items():
                self._obs_w.setdefault(i, []).append(transform_points(f["T_wc"], X))
        for i, lst in self._obs_w.items():
            del lst[: max(0, len(lst) - self.params.max_obs_per_tag)]
            self._map[i], members = self._consensus(lst, thr)
            self._count[i] = int(members.sum())
        self._T_last = self._frames[-1]["T_wc"].copy()
        log.info("refine_map: %d frames, %d tags, rms %.3f mm after %d iterations",
                 len(self._frames), len(world), rms, it)
        return {"rms_mm": rms, "iterations": it, "n_frames": len(self._frames), "n_tags": len(world)}

    # ------------------------------------------------------------------ tracking
    def track(self, left_r: np.ndarray, right_r: np.ndarray, t: float | None = None) -> TrackResult:
        """Pose of the rectified left camera from the tags in a rectified stereo pair."""
        p = self.params
        left = self.detect(left_r)
        right = self.detect(right_r)
        obs = self.triangulate(left, right)
        info: dict = {"n_left": len(left), "n_right": len(right), "n_stereo": len(obs), "ids": sorted(obs)}
        if not self._initialized:
            if len(obs) < p.min_tags:
                return self._finish(False, self._T_last, info, t, "not_initialized")
            for i, X in obs.items():
                self._add_observation(i, X)
            self._frames.append({"T_wc": np.eye(4), "obs": {i: X.copy() for i, X in obs.items()}})
            self._initialized = True
            info.update({"method": "init", "n_tags": len(obs), "rms_mm": 0.0, "new_tags": sorted(obs)})
            log.info("marker map initialised with %d tags", len(obs))
            return self._finish(True, np.eye(4), info, t)

        world = self.map
        sol = self._stereo_pose(obs, world)
        if sol is not None:
            T, inliers, rms = sol
            info.update({"method": "stereo", "n_tags": len(inliers), "rms_mm": rms, "inliers": inliers})
            if rms > p.max_rms_mm:
                return self._finish(False, self._T_last, info, t, "high_residual")
            outliers = {i for i in obs if i in world and i not in inliers}
            ref = self._refine_reprojection(T, left, right, world, exclude=outliers)
            if ref is not None:
                T, rms_px, n_used = ref
                info.update({"reproj_px": rms_px, "n_tags_reproj": n_used})
                if rms_px > p.max_reproj_px:
                    return self._finish(False, self._T_last, info, t, "high_reprojection")
            # grow the map; every stereo observation is kept and each tag's consensus decides
            # (outliers such as a knocked tag or a bad first triangulation are outvoted)
            for i, X in obs.items():
                self._add_observation(i, transform_points(T, X))
            self._frames.append({"T_wc": T.copy(), "obs": {i: X.copy() for i, X in obs.items()}})
            info["new_tags"] = sorted(i for i in obs if i not in world)
            return self._finish(True, T, info, t)

        if p.pnp_fallback:
            sol_pnp = self._pnp_pose(left, world)
            if sol_pnp is not None:
                T, ids, rms_px = sol_pnp
                info.update({"method": "pnp", "n_tags": len(ids), "reproj_px": rms_px})
                ref = self._refine_reprojection(T, left, right, world)  # adds right-image tags if any
                if ref is not None:
                    T, rms_px, n_used = ref
                    info.update({"reproj_px": rms_px, "n_tags_reproj": n_used})
                if rms_px <= p.max_reproj_px:
                    return self._finish(True, T, info, t)
                return self._finish(False, self._T_last, info, t, "high_reprojection")
        reason = "no_tags" if not left else "few_tags"
        return self._finish(False, self._T_last, info, t, reason)

    def _finish(self, ok: bool, T_wc: np.ndarray, info: dict, t: float | None, reason: str | None = None) -> TrackResult:
        if reason is not None:
            info["reason"] = reason
            log.debug("marker tracking failed: %s (%s)", reason, {k: info[k] for k in ("n_left", "n_stereo")})
        if ok:
            self._T_last = T_wc.copy()
        info["map_size"] = len(self._map)
        res = TrackResult(np.asarray(T_wc, dtype=np.float64).copy(), bool(ok), info)
        self.trajectory.append((float("nan") if t is None else float(t), res.T_wc.copy(), bool(ok)))
        self.last_result = res
        return res
