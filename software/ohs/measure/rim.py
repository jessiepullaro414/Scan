"""A hole read from the scan's own camera frames at a click (ENGINE_PLAN X15; the owner's decision of 2026-11-24).

Why: over open space the fused mesh carries the matcher's fill into a hole, so the Hole tool's circle on the mesh reads
a Ø25 hole 1 to 7 mm small and gives no reading at all for Ø6 and Ø10 (``VALIDATION.md``, "Holes filled in"). The
rim itself is plain in the camera images. Read there, every simulated hole of Ø6, Ø10 and Ø25 over open space and near
a floor came out within 0.14 mm at true poses, the projector's dots on (``tools/hole_rim_study.py``, round 6, on
data not used to set any limit).

**What it reports:** the diameter of the hole's *entrance rim in the face's plane* (where the clicked face ends): not
the bore below it, not the outer edge of a chamfer or countersink, not the smallest clearance through the hole. The
circle is fitted robustly, so a burr, a nick or an out-of-round rim is passed over, not reported.

**The reading** (:func:`read_hole`), every number fixed by the study (its NOTES and ``VALIDATION.md``):

1. **Frames:** the recorded frames in which the click is in the left image (60 px from the border), at most
   :data:`MAX_FRAMES` spread over the scan, or the frames the caller names.
2. **The face's plane, per frame,** from that frame's matcher depth round the click (frame C, the rectified left
   camera): the points within :data:`PLANE_DISK_MM` with a matcher sigma under :data:`PLANE_SIGMA_MM` (the fill in a
   hole has tens of mm), a trimmed plane; then again from an annulus outside the hole's seed (:data:`RING_MM`).
3. **The seed:** the left image sampled on that plane round the click and median-filtered (the dots go), split by
   Otsu's threshold below and above the face's median: a patch darker *or* brighter than the face. Of the closed
   patches within :data:`SEED_REACH_MM` of the click the one nearest it is taken, the larger only on a tie within
   0.5 mm (holes side by side: the clicked one, not the biggest one).
4. **The rim, both raw cameras:** radial lines on the plane in sectors of :data:`SECTOR_DEG`; each sector's median
   profile (a projector dot does not count). The hidden-half circle (where neither camera sees the wall) is the
   start; then up to :data:`N_CAND` steps a line, and **one model of the hole**: the entrance circle, the exit circle
   a wall ``t`` below along the face's normal (``t`` free, 0 to :data:`T_MAX_MM`; each camera sees the exit circle
   projected on the face, only inside the top rim), the steps assigned to either and fitted robustly
   (:func:`fit_hole_model`). A frame is refused when its two cameras put the top rim more than
   :data:`CAM_AGREE_MM` apart, or when another wall height fits nearly as well with a diameter :data:`ALT_D_MM`
   different.
5. **Over frames** (:func:`combine_frames`): the median diameter; its ± (1σ) the frames' scatter (1.25 × robust
   sd / √frames) and a part :data:`B_SHARED_MM` the frames share (lighting, rim, plane, calibration: more frames
   do not shrink it), widened when less than :data:`COVER_FULL_DEG` of the rim is found. The centre: the median of
   the frames' centres through their poses. The diameter does not depend on the poses; the position does.

**Refused, in words** (:attr:`RimReading.refused`): fewer than :data:`MIN_FRAMES` frames show the rim; the top rim
found on less than :data:`COVER_MIN_DEG` of the round; the frames' diameters scatter more than
:data:`MAX_D_SCATTER_MM`; their centres more than :data:`CENTRE_REFUSE_MM` apart, or more than :data:`CENTRE_WARN_MM`
while the diameters scatter more than :data:`D_SCATTER_PATH_MM`; the ± above :data:`PM_LIMIT_MM`; the click more
than :data:`CLICK_RIM_MM` outside the read circle (another hole's rim). **Warned:** the frames' centres more than
:data:`CENTRE_WARN_MM` apart with diameters that agree (the camera path may be off, and the position with it); the
scan's camera-path check warns (the caller says so).

**What it needs:** the click and the face's normal there (in the frame of the poses), the poses (frame index ->
``T_wc`` of frame C), the stereo calibration (``K1 D1 K2 D2 R T R1 P1``, a ``StereoCalibration`` or its dict), the
frames' raw images (left, right) and their matcher depth near the click: ``(depth, sigma)`` maps on frame C (the
sigma may be ``None``: then every depth pixel counts for the plane). A caller with points of its own may pass
``points_of`` instead.

**Speed:** about 0.6 s a frame on this mini PC's processor (Ryzen 7 7735U): 12 s for 20 frames, most of it the
images sampled along the radial lines (twice: the start and the model) and the change-point search of each sector.

**In words** (what the app's Hole tool shows, ``POST .../hole-images``): :func:`reading_words` for an answer (the
diameter, which diameter it is, and when the position may be off), :func:`refusal_hint` for what to do after a refusal.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import numpy as np

__all__ = ["RimReading", "FrameReading", "read_hole", "combine_frames", "stereo_cameras", "Camera", "Plane",
           "PM_LIMIT_MM", "CLICK_RIM_MM", "MAX_FRAMES", "reading_words", "refusal_hint", "WHICH_DIAMETER"]

MAX_FRAMES = 20  # frames read at most, spread over the scan
BORDER_PX = 60  # the click must fall this far inside the left image
SECTOR_DEG = 6.0  # the rim's sectors
STEP_DEG = 0.5  # radial lines this far apart
CROSS_MM = 1.0  # the 50 % crossing is looked for this close to the change point
SEED_DISK_MM = 30.0  # the seed is searched in this disk round the click ...
SEED_CELL_MM = 0.25  # ... on a grid this fine ...
SEED_MEDIAN_MM = 2.25  # ... median-filtered over this
SEED_REACH_MM = 4.0  # the patch must come this close to the click
PLANE_DISK_MM = 25.0  # the plane's first pass: depth within this of the click ...
PLANE_SIGMA_MM = 2.0  # ... whose matcher sigma is below this
WINDOW_MM = 35.0  # depth pixels are taken in a window of this round the click
RING_MM = (2.0, 12.0)  # the plane's second pass: this annulus outside the seed's radius
MIN_POINTS = 20  # a frame's circle needs this many steps on it
MIN_FRAMES = 5  # the reading refuses with fewer frames ...
MAX_D_SCATTER_MM = 1.0  # ... or when the frames' diameters scatter more than this (robust sd)
B_SHARED_MM = 0.1  # the ±'s shared part (the frames share the lighting, the rim, the plane, the calibration)
COVER_MIN_DEG = 120.0  # the top rim must be found over at least this much of the round (median over frames)
COVER_FULL_DEG = 270.0  # below this the ± widens by sqrt(COVER_FULL_DEG / covered)
CENTRE_WARN_MM = 1.0  # the frames' centres scattering more than this: a warning
CENTRE_REFUSE_MM = 3.0  # ... more than this: refused
D_SCATTER_PATH_MM = 0.3  # centres over CENTRE_WARN_MM apart and diameters scattering more than this: refused
CAM_AGREE_MM = 0.15  # the two cameras' top-rim radii must agree this well, or the frame is refused
GATE_MM = 0.3  # a step counts for a circle within this of it
T_MAX_MM = 10.0  # the wall's height is looked for between 0 and this
N_CAND = 3  # steps kept per sector line
ALT_T_MM = 1.0  # another wall height at least this far from the one fitted ...
ALT_SHARE = 0.9  # ... that explains at least this share as many steps ...
ALT_D_MM = 0.2  # ... and gives a diameter this much different: two readings fit, the frame is refused
PM_LIMIT_MM = 0.20  # the reading refuses above this ± (1σ)
CLICK_RIM_MM = 3.0  # the click must lie inside the read circle or within this of its rim


# --------------------------------------------------------------------------- cameras and sampling
@dataclass
class Camera:
    """A raw camera in frame-C coordinates: ``x_cam = R @ x_C + t``, then ``K`` and the distortion ``D``."""

    K: np.ndarray
    D: np.ndarray
    R: np.ndarray
    t: np.ndarray

    @property
    def centre(self) -> np.ndarray:
        return -self.R.T @ self.t

    def project(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """``(uv (N, 2), in front (N,))`` for frame-C points ``X`` (N, 3)."""
        import cv2

        Xc = X @ self.R.T + self.t
        front = Xc[:, 2] > 1.0
        rv, _ = cv2.Rodrigues(self.R)
        uv, _ = cv2.projectPoints(np.ascontiguousarray(X, np.float64).reshape(-1, 1, 3), rv,
                                  np.asarray(self.t, np.float64), self.K, self.D)
        return uv.reshape(-1, 2), front


def _cal(calib: Any) -> dict:
    if isinstance(calib, dict):
        return calib
    st = getattr(calib, "stereo", calib)
    return {k: np.asarray(getattr(st, k)) for k in ("K1", "D1", "K2", "D2", "R", "T", "R1", "P1")} | {
        "image_size": list(getattr(st, "image_size", (1280, 800)))}


def stereo_cameras(cal: Any) -> tuple[Camera, Camera]:
    """The raw left and right cameras in frame C (the rectified left camera) from a stereo calibration (or a
    ``calibration.json``'s ``stereo``). ``R1`` turns raw left rays into frame C; ``x_right = R x_left + T``."""
    cal = _cal(cal)
    R1 = np.asarray(cal["R1"], float)
    R = np.asarray(cal["R"], float)
    T = np.asarray(cal["T"], float).reshape(3)
    left = Camera(np.asarray(cal["K1"], float), np.asarray(cal["D1"], float), R1.T, np.zeros(3))
    right = Camera(np.asarray(cal["K2"], float), np.asarray(cal["D2"], float), R @ R1.T, T)
    return left, right


def sample(img: np.ndarray, cam: Camera, X: np.ndarray) -> np.ndarray:
    """The image at frame-C points ``X`` (bilinear; NaN outside the image or behind the camera)."""
    from scipy.ndimage import map_coordinates

    uv, front = cam.project(X)
    h, w = img.shape
    ok = front & (uv[:, 0] >= 1) & (uv[:, 0] <= w - 2) & (uv[:, 1] >= 1) & (uv[:, 1] <= h - 2)
    out = np.full(len(X), np.nan)
    if ok.any():
        out[ok] = map_coordinates(img, [uv[ok, 1], uv[ok, 0]], order=1, mode="nearest")
    return out


# --------------------------------------------------------------------------- the plane
@dataclass
class Plane:
    """A face's plane in frame C: a point ``o``, in-plane axes ``u``, ``v`` and the normal ``n`` (towards the camera)."""

    o: np.ndarray
    u: np.ndarray
    v: np.ndarray
    n: np.ndarray

    def world(self, xy: np.ndarray) -> np.ndarray:
        return self.o + xy[..., :1] * self.u + xy[..., 1:2] * self.v

    def local(self, X: np.ndarray) -> np.ndarray:
        d = X - self.o
        return np.stack([d @ self.u, d @ self.v], axis=-1)


def _trimmed_plane(P: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    for _ in range(4):
        if len(P) < 30:
            return None
        c = P.mean(axis=0)
        n = np.linalg.svd(P - c, full_matrices=False)[2][2]
        r = (P - c) @ n
        mad = 1.4826 * np.median(np.abs(r - np.median(r)))
        keep = np.abs(r) < max(3.0 * mad, 0.2)
        if keep.all():
            break
        P = P[keep]
    return c, n


def depth_points(depth: np.ndarray, sigma: np.ndarray | None, K_rect: np.ndarray, near: np.ndarray) -> np.ndarray:
    """The frame-C points of the depth pixels (``depth`` z in mm, 0 = none; ``K_rect`` the rectified intrinsics) in a
    window of :data:`WINDOW_MM` round the frame-C point ``near``, those with a matcher sigma under
    :data:`PLANE_SIGMA_MM` (every one when ``sigma`` is ``None``)."""
    z0 = float(near[2])
    if z0 <= 1.0:
        return np.zeros((0, 3))
    fx, fy, cx, cy = K_rect[0, 0], K_rect[1, 1], K_rect[0, 2], K_rect[1, 2]
    u0, v0 = fx * near[0] / z0 + cx, fy * near[1] / z0 + cy
    hw = int(math.ceil(WINDOW_MM * fx / z0))
    h, w = depth.shape
    a, b = max(0, int(v0) - hw), min(h, int(v0) + hw + 1)
    c0, c1 = max(0, int(u0) - hw), min(w, int(u0) + hw + 1)
    if a >= b or c0 >= c1:
        return np.zeros((0, 3))
    Z = depth[a:b, c0:c1].astype(np.float64)
    vv, uu = np.mgrid[a:b, c0:c1]
    ok = Z > 0
    if sigma is not None:
        ok &= sigma[a:b, c0:c1] < PLANE_SIGMA_MM
    return np.column_stack([(uu[ok] - cx) / fx * Z[ok], (vv[ok] - cy) / fy * Z[ok], Z[ok]])


def plane_from_points(X: np.ndarray, near: np.ndarray, select) -> Plane | None:
    """The trimmed plane through the points ``X`` (frame C) that ``select(X)`` keeps; its origin is ``near`` dropped
    onto it, its normal towards the camera."""
    X = X[select(X)] if len(X) else X
    fit = _trimmed_plane(X)
    if fit is None:
        return None
    c, n = fit
    if n @ (-c) < 0:
        n = -n
    o = near - ((near - c) @ n) * n
    u = np.cross(n, [0.0, 1.0, 0.0] if abs(n[1]) < 0.9 else [1.0, 0.0, 0.0])
    u /= np.linalg.norm(u)
    return Plane(o, u, np.cross(n, u), n)


# --------------------------------------------------------------------------- the seed
def _otsu(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    hist, edges = np.histogram(x, bins=128)
    mids = 0.5 * (edges[1:] + edges[:-1])
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * mids) / np.maximum(w0, 1)
    m1 = (np.sum(hist * mids) - np.cumsum(hist * mids)) / np.maximum(w1, 1)
    between = w0 * w1 * (m0 - m1) ** 2
    return float(mids[int(np.argmax(between))])


def find_seed(img: np.ndarray, cam: Camera, plane: Plane, click_xy: np.ndarray) -> tuple[np.ndarray, float] | str:
    """``(centre (2,) in the plane, radius)`` of the hole's patch at the click (module docstring, step 3), or the
    reason there is none."""
    from scipy import ndimage

    R = SEED_DISK_MM
    g = np.arange(-R, R + 1e-9, SEED_CELL_MM)
    gx, gy = np.meshgrid(g, g, indexing="xy")
    xy = np.stack([gx + click_xy[0], gy + click_xy[1]], axis=-1)
    I = sample(img, cam, plane.world(xy.reshape(-1, 2))).reshape(gx.shape)
    inside = (gx ** 2 + gy ** 2) <= R * R
    if np.mean(np.isfinite(I[inside])) < 0.9:
        return "the hole's surroundings leave the image"
    fill = np.where(np.isfinite(I), I, np.nanmedian(I))
    k = int(round(SEED_MEDIAN_MM / SEED_CELL_MM)) | 1
    M = ndimage.median_filter(fill, size=k)
    # the laser lines saturate: clipped at the face's median, the histogram holds the face and one side only
    med = float(np.median(M[inside]))
    masks = [(M < _otsu(np.minimum(M[inside], med))) & inside, (M > _otsu(np.maximum(M[inside], med))) & inside]
    edge = (gx ** 2 + gy ** 2) > (R - 1.0) ** 2
    d_click = np.hypot(gx, gy)
    cands = []
    for mask in masks:
        lab, n = ndimage.label(mask)
        on_edge = set(np.unique(lab[edge & mask]).tolist())
        for j in range(1, n + 1):
            m = lab == j
            area = m.sum() * SEED_CELL_MM ** 2
            if area < math.pi * 1.0 ** 2 or j in on_edge:
                continue
            dmin = float(d_click[m].min())
            if dmin <= SEED_REACH_MM:
                cands.append((dmin, area, m))
    if not cands:
        return "no closed patch within reach of the click"
    d0 = min(c[0] for c in cands)
    _, area, m = max((c for c in cands if c[0] <= d0 + 0.5), key=lambda c: c[1])
    centre = np.array([gx[m].mean(), gy[m].mean()]) + click_xy
    return centre, math.sqrt(area / math.pi)


# --------------------------------------------------------------------------- the rim
def profile_edges(P: np.ndarray, rs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(change points, 50 % crossings)`` (NaN where none) of radial profiles ``P`` (sectors x radii ``rs``, each the
    median over its sector's lines), dark inside (small radius), bright outside: the split with the least L1 cost
    about each side's median, then where the profile crosses half-way between the medians of the inside (0.5 mm
    inside the change point) and the outside (0.5 mm out), nearest the change point within :data:`CROSS_MM`."""
    S, n = P.shape
    best = np.full(S, np.inf)
    cp = np.full(S, np.nan)
    for k in range(max(2, int(0.1 * n)), int(0.9 * n)):
        a, b = P[:, :k], P[:, k:]
        ma, mb = np.median(a, axis=1), np.median(b, axis=1)
        c = np.abs(a - ma[:, None]).sum(axis=1) + np.abs(b - mb[:, None]).sum(axis=1)
        upd = (mb > ma) & (c < best)
        best[upd] = c[upd]
        cp[upd] = 0.5 * (rs[k - 1] + rs[k])
    half = np.full(S, np.nan)
    for s_ in np.flatnonzero(np.isfinite(cp)):
        e, p = cp[s_], P[s_]
        lo, hi = rs < e - 0.5, rs > e + 0.5
        if not (lo.any() and hi.any()):
            continue
        a, b = float(np.median(p[lo])), float(np.median(p[hi]))
        if b <= a:
            continue
        h = 0.5 * (a + b)
        j = np.flatnonzero(((p[:-1] - h) * (p[1:] - h) <= 0) & (p[:-1] != p[1:]))
        if not len(j):
            continue
        x = rs[j] + (h - p[j]) / (p[j + 1] - p[j]) * (rs[j + 1] - rs[j])
        x = x[np.abs(x - e) <= CROSS_MM]
        if len(x):
            half[s_] = float(x[np.argmin(np.abs(x - e))])
    return cp, half


def fit_circle(xy: np.ndarray) -> tuple[np.ndarray, float, float, np.ndarray] | None:
    """``(centre, radius, rms, kept)``: an algebraic circle to start, then a geometric one weighted by Tukey's
    biweight (scale: the residuals' robust sd, at least 0.02 mm), so that a sector a projector dot spoiled does not
    pull it."""
    if len(xy) < 5:
        return None
    A = np.column_stack([xy, np.ones(len(xy))])
    s, *_ = np.linalg.lstsq(A, (xy ** 2).sum(axis=1), rcond=None)
    c = s[:2] / 2.0
    rad = math.sqrt(max(s[2] + c @ c, 0.0))
    w = np.ones(len(xy))
    for _ in range(15):
        d = xy - c
        dist = np.maximum(np.hypot(*d.T), 1e-9)
        res = dist - rad
        sc = max(1.4826 * float(np.median(np.abs(res - np.median(res)))), 0.02)
        u = res / (4.685 * sc)
        w = np.where(np.abs(u) < 1, (1 - u * u) ** 2, 0.0)
        if w.sum() < 5:
            return None
        J = np.column_stack([-d[:, 0] / dist, -d[:, 1] / dist, -np.ones(len(xy))])
        step, *_ = np.linalg.lstsq(J * np.sqrt(w)[:, None], -res * np.sqrt(w), rcond=None)
        c, rad = c + step[:2], rad + step[2]
        if np.abs(step).max() < 1e-5:
            break
    res = np.hypot(*(xy - c).T) - rad
    keep = w > 0
    return c, float(rad), float(np.sqrt(np.average(res ** 2, weights=np.maximum(w, 1e-12)))), keep


def _arc_deg(angles: np.ndarray) -> float:
    a = np.sort(np.mod(angles, 360.0))
    if len(a) < 2:
        return 0.0
    gaps = np.diff(np.r_[a, a[0] + 360.0])
    return float(360.0 - gaps.max())


def rim_edges(imgs, cams, plane: Plane, centre: np.ndarray, r_seed: float) -> np.ndarray:
    """One step a sector and camera round the seed, rows ``(angle_deg, camera, wall_seen, crossing, change point,
    k)``: radii from ``centre`` (NaN where none); ``wall_seen`` where the camera sees the hole's wall there; ``k`` how
    far the wall's foot would land inside the rim per mm of wall."""
    r_lo = max(0.3 * r_seed, r_seed - 3.0)
    rs = np.arange(r_lo, r_seed + 3.0, 0.04)
    phis = np.radians(np.arange(0.0, 360.0, STEP_DEG))
    PH, RR = np.meshgrid(phis, rs, indexing="ij")
    xy = np.stack([centre[0] + RR * np.cos(PH), centre[1] + RR * np.sin(PH)], axis=-1)
    X = plane.world(xy.reshape(-1, 2))
    rows = []
    ns = int(round(SECTOR_DEG / STEP_DEG))
    for ci, (img, cam) in enumerate(zip(imgs, cams)):
        I = sample(img, cam, X).reshape(PH.shape)  # lines x radii
        C = cam.centre
        Ps = I.reshape(-1, ns, len(rs))  # sectors x lines x radii
        okS = np.isnan(Ps).mean(axis=(1, 2)) <= 0.2
        prof = np.nanmedian(np.where(okS[:, None, None], Ps, 0.0), axis=1)
        okS &= np.isfinite(prof).all(axis=1)
        if not okS.any():
            continue
        cps, halves = np.full(len(okS), np.nan), np.full(len(okS), np.nan)
        cps[okS], halves[okS] = profile_edges(prof[okS], rs)
        for si in np.flatnonzero(okS & np.isfinite(cps)):
            cp, half = cps[si], halves[si]
            ph = math.radians((si + 0.5) * SECTOR_DEG)
            w2 = np.array([math.cos(ph), math.sin(ph)])
            P = plane.world((centre + cp * w2)[None])[0]
            w3 = w2[0] * plane.u + w2[1] * plane.v
            wall_seen = bool((C - P) @ (-w3) > 0)
            k = float(max(0.0, -((C - P) @ w3) / max((C - P) @ plane.n, 1e-6)))
            rows.append(((si + 0.5) * SECTOR_DEG, ci, wall_seen, half, cp, k))
    return np.array(rows, dtype=float).reshape(-1, 6)


def edge_xy(E: np.ndarray, centre: np.ndarray, col: int) -> np.ndarray:
    """The plane points of :func:`rim_edges`' rows for the crossing (``col`` 3) or the change point (4)."""
    a = np.radians(E[:, 0])
    return np.column_stack([centre[0] + E[:, col] * np.cos(a), centre[1] + E[:, col] * np.sin(a)])


def candidate_steps(prof: np.ndarray, rs: np.ndarray, n: int = N_CAND) -> list[list[tuple[float, float, float]]]:
    """Per sector profile: up to ``n`` steps ``(radius, sign, strength)``, the strongest local peaks of the smoothed
    profile's slope, of either sign, above 4 robust sd of the slope, placed to a fraction of a sample by a parabola."""
    from scipy.ndimage import gaussian_filter1d

    dr = float(rs[1] - rs[0])
    g = np.gradient(gaussian_filter1d(prof, 2.0, axis=1), dr, axis=1)
    a = np.abs(g)
    out = []
    for s in range(len(prof)):
        row = a[s]
        noise = 1.4826 * float(np.median(np.abs(row - np.median(row)))) + 1e-9
        pk = np.flatnonzero((row[1:-1] > row[:-2]) & (row[1:-1] >= row[2:])) + 1
        pk = pk[row[pk] > 4.0 * noise]
        pk = pk[np.argsort(-row[pk])][:n]
        c = []
        for j in pk:
            y0, y1, y2 = row[j - 1], row[j], row[j + 1]
            den = y0 - 2.0 * y1 + y2
            off = 0.5 * (y0 - y2) / den if den < 0 else 0.0
            c.append((float(rs[j] + off * dr), float(np.sign(g[s, j])), float(row[j])))
        out.append(c)
    return out


def model_candidates(imgs, cams, plane: Plane, r_seed: float) -> np.ndarray:
    """Candidate steps round the plane's origin (the seed's centre): rows ``(x, y, camera, strength, sector, radius)``."""
    r_lo = max(0.3 * r_seed, r_seed - 4.0)
    rs = np.arange(r_lo, r_seed + 3.0, 0.04)
    phis = np.radians(np.arange(0.0, 360.0, STEP_DEG))
    PH, RR = np.meshgrid(phis, rs, indexing="ij")
    X = plane.world(np.stack([RR * np.cos(PH), RR * np.sin(PH)], axis=-1).reshape(-1, 2))
    ns = int(round(SECTOR_DEG / STEP_DEG))
    rows = []
    for ci, (img, cam) in enumerate(zip(imgs, cams)):
        Ps = sample(img, cam, X).reshape(-1, ns, len(rs))
        okS = np.isnan(Ps).mean(axis=(1, 2)) <= 0.2
        prof = np.nanmedian(np.where(okS[:, None, None], Ps, 0.0), axis=1)
        okS &= np.isfinite(prof).all(axis=1)
        if not okS.any():
            continue
        idx = np.flatnonzero(okS)
        for si, cands in zip(idx, candidate_steps(prof[okS], rs)):
            ph = math.radians((si + 0.5) * SECTOR_DEG)
            for r, _sgn, st in cands:
                rows.append((r * math.cos(ph), r * math.sin(ph), ci, st, si, r))
    return np.array(rows, dtype=float).reshape(-1, 6)


# --------------------------------------------------------------------------- one model of the hole
def exit_circles(p: np.ndarray, Cip: np.ndarray, h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The exit circle (``p = (cx, cy, R, t)``: a wall ``t`` below, the hole square to the face) as each camera sees it
    on the face's plane: a camera ``h`` above the face at ``Cip`` (in the plane) sees it moved towards itself by
    ``s = t / (h + t)`` of the way and shrunk by ``1 - s``."""
    c, R, t = p[:2], p[2], p[3]
    s = t / (h + t)
    return c[None] + s[:, None] * (Cip - c[None]), R * (1.0 - s)


def assign_steps(p, xy, cam, Cip, h, gate):
    """Each step's circle (0 top, 1 exit, -1 neither) and its residual; the exit circle counts only inside the top
    rim (elsewhere the plate hides it), and only when ``t`` is not about 0."""
    c, R = p[:2], p[2]
    rt = np.hypot(*(xy - c).T) - R
    ce, Re = exit_circles(p, Cip, h)
    re = np.hypot(*(xy - ce[cam]).T) - Re[cam]
    lab = np.full(len(xy), -1)
    res = np.full(len(xy), np.nan)
    top = np.abs(rt) <= gate
    ex = (np.abs(re) <= gate) & (rt < -gate) & (p[3] > 0.05)
    use_exit = ex & (~top | (np.abs(re) < np.abs(rt)))
    lab[top & ~use_exit], res[top & ~use_exit] = 0, rt[top & ~use_exit]
    lab[use_exit], res[use_exit] = 1, re[use_exit]
    return lab, res


@dataclass
class FrameReading:
    """One frame's circle (frame C) or the reason there is none."""

    ok: bool
    why: str = ""
    centre_C: np.ndarray | None = None
    d: float = float("nan")
    t: float = float("nan")  # the wall's height found (NaN when fewer than 10 steps lie on the exit circle)
    cover_deg: float = 0.0  # how much of the round the top rim was found on
    n_top: int = 0
    n_exit: int = 0
    cams_mm: float = float("nan")  # the two cameras' top-rim radii apart
    centre_xy: np.ndarray | None = None  # in the plane
    normal_C: np.ndarray | None = None  # the face's normal, frame C


def _fit_from(xy, cam, Cip, h, c0, R0, t_start):
    """Rounds of assignment and a robust fit of ``(centre, R, t)`` from ``t_start``: ``(p, lab, gate)`` or why not."""
    from scipy.optimize import least_squares

    p = np.array([c0[0], c0[1], R0, t_start])
    gate = GATE_MM
    for _ in range(4):
        lab, _ = assign_steps(p, xy, cam, Cip, h, gate)
        if (lab == 0).sum() < MIN_POINTS:
            return "too few steps on one circle"
        free_t = bool((lab == 1).sum() >= 10)
        t_held = float(p[3])

        def resid(q, lab=lab, free_t=free_t, t_held=t_held):
            q = q if free_t else np.r_[q, t_held]
            out = np.hypot(*(xy[lab == 0] - q[:2]).T) - q[2]
            m = lab == 1
            if m.any():
                ce, Re = exit_circles(q, Cip, h)
                out = np.r_[out, np.hypot(*(xy[m] - ce[cam[m]]).T) - Re[cam[m]]]
            return out

        lo = np.array([-np.inf, -np.inf, 0.5] + ([0.0] if free_t else []))
        hi = np.array([np.inf, np.inf, np.inf] + ([T_MAX_MM] if free_t else []))
        x0 = np.clip(p if free_t else p[:3], lo + 1e-9, hi - 1e-9)
        sol = least_squares(resid, x0, bounds=(lo, hi), loss="soft_l1", f_scale=0.05)
        p = sol.x if free_t else np.r_[sol.x, t_held]
        r = resid(sol.x)
        gate = max(0.1, min(GATE_MM, 3.0 * 1.4826 * float(np.median(np.abs(r - np.median(r))))))
    lab, _ = assign_steps(p, xy, cam, Cip, h, gate)
    return p, lab, gate


def fit_hole_model(cand: np.ndarray, Cip: np.ndarray, h: np.ndarray, c0: np.ndarray, R0: float) -> FrameReading | str:
    """One model of the hole fitted to the steps of both cameras (module docstring, step 4): ``t`` starts from a
    search over 0 to :data:`T_MAX_MM` for the height that explains the most steps (the smaller on a tie), then rounds
    of assignment and a robust fit of ``(centre, R, t)``. Refused: another ``t`` at least :data:`ALT_T_MM` away that
    explains :data:`ALT_SHARE` as many steps and fits a diameter :data:`ALT_D_MM` different (two readings fit);
    either camera with fewer than 10 top-rim steps; the cameras' top rims more than :data:`CAM_AGREE_MM` apart."""
    xy, cam = cand[:, :2], cand[:, 2].astype(int)
    ts = np.arange(0.0, T_MAX_MM + 1e-9, 0.1)
    counts = np.zeros(len(ts), int)
    p = np.array([c0[0], c0[1], R0, 0.0])
    for k, t in enumerate(ts):
        p[3] = t
        lab, _ = assign_steps(p, xy, cam, Cip, h, GATE_MM)
        counts[k] = int((lab >= 0).sum())
    kb = int(np.argmax(counts))  # the first maximum: the smaller t on a tie
    f = _fit_from(xy, cam, Cip, h, c0, R0, float(ts[kb]))
    if isinstance(f, str):
        return f
    p, lab, gate = f
    far = np.abs(ts - p[3]) >= ALT_T_MM
    if far.any():
        ka = int(np.flatnonzero(far)[np.argmax(counts[far])])
        if counts[ka] >= ALT_SHARE * counts[kb]:
            g = _fit_from(xy, cam, Cip, h, c0, R0, float(ts[ka]))
            if not isinstance(g, str) and abs(2 * g[0][2] - 2 * p[2]) > ALT_D_MM:
                return (f"two readings of the rim fit: Ø{2 * p[2]:.2f} with a wall of {p[3]:.1f} mm and "
                        f"Ø{2 * g[0][2]:.2f} with {g[0][3]:.1f} mm")
    top = lab == 0
    radii = []
    for ci in (0, 1):
        m = top & (cam == ci)
        if m.sum() < 10:
            return f"camera {ci} finds too little of the top rim"
        radii.append(float(np.median(np.hypot(*(xy[m] - p[:2]).T))))
    if abs(radii[0] - radii[1]) > CAM_AGREE_MM:
        return f"the two cameras put the rim {abs(radii[0] - radii[1]):.2f} mm apart"
    d = xy[top] - p[:2]
    n_exit = int((lab == 1).sum())
    return FrameReading(True, "", None, 2.0 * float(p[2]), float(p[3]) if n_exit >= 10 else float("nan"),
                        _arc_deg(np.degrees(np.arctan2(d[:, 1], d[:, 0]))), int(top.sum()), n_exit,
                        abs(radii[0] - radii[1]), np.asarray(p[:2], float))


def frame_plane_seed(img0, cam0, X: np.ndarray, click_C: np.ndarray) -> tuple[Plane, float] | str:
    """The face's plane and the hole's seed in one frame from depth points ``X`` (frame C) round the click: ``(plane
    with its origin at the seed's centre, seed radius)``, or the reason there is none."""
    p1 = plane_from_points(X, click_C, lambda Y: np.linalg.norm(Y - click_C, axis=1) < PLANE_DISK_MM)
    if p1 is None:
        return "no plane in the depth round the click"
    if p1.n @ (-p1.o) / np.linalg.norm(p1.o) < 0.3:
        return "the face is seen too steeply"
    seed = find_seed(img0, cam0, p1, np.zeros(2))
    if isinstance(seed, str):
        return seed
    c_s, r_s = seed
    c3 = p1.world(c_s[None])[0]

    def ring(Y):
        d = Y - c3
        d -= np.outer(d @ p1.n, p1.n)
        rr = np.linalg.norm(d, axis=1)
        return (rr > r_s + RING_MM[0]) & (rr < r_s + RING_MM[1])

    pl = plane_from_points(X, c3, ring)
    return (pl if pl is not None else Plane(c3, p1.u, p1.v, p1.n)), r_s


def read_frame(imgs, cams, X: np.ndarray, click_C: np.ndarray) -> FrameReading:
    """One frame: plane and seed (:func:`frame_plane_seed`), the hidden-half circle as the start, both cameras' steps,
    then :func:`fit_hole_model`."""
    ps = frame_plane_seed(imgs[0], cams[0], X, click_C)
    if isinstance(ps, str):
        return FrameReading(False, ps)
    pl, r_s = ps
    z = np.zeros(2)
    c0, R0 = z, r_s
    E = rim_edges(imgs, cams, pl, z, r_s)
    if len(E):
        hid = (E[:, 2] == 0) & np.isfinite(E[:, 3])
        if hid.sum() >= MIN_POINTS:
            f = fit_circle(edge_xy(E, z, 3)[hid])
            if f is not None:
                c0, R0 = f[0], f[1]
    cand = model_candidates(imgs, cams, pl, max(R0, r_s))
    if len(cand) < MIN_POINTS:
        return FrameReading(False, "no steps round the seed")
    Cip = np.array([pl.local(c.centre[None])[0] for c in cams])
    h = np.array([float((c.centre - pl.o) @ pl.n) for c in cams])
    m = fit_hole_model(cand, Cip, h, np.asarray(c0, float), float(R0))
    if isinstance(m, str):
        return FrameReading(False, m)
    m.centre_C = pl.world(m.centre_xy[None])[0]
    m.normal_C = pl.n
    return m


# --------------------------------------------------------------------------- over frames
@dataclass
class RimReading:
    """The reading at a click: ``refused`` (words) or ``None``; the numbers, also when refused, where there are any."""

    refused: str | None
    diameter_mm: float | None = None
    pm_mm: float | None = None  # 1σ: the frames' scatter and the shared part
    centre: np.ndarray | None = None  # in the frame of the poses
    normal: np.ndarray | None = None
    frames_used: int = 0
    frames: int = 0
    covered_deg: float | None = None
    warnings: list[str] = field(default_factory=list)
    position_unsure: bool = False
    d_sd: float | None = None  # the frames' diameters, robust sd
    pm_stat: float | None = None  # the scatter's part of the ±
    centre_scatter: float | None = None
    wall_mm: float | None = None  # the wall height the model found (median over frames; None when never seen)
    frame_refusals: dict[str, int] = field(default_factory=dict)
    frame_readings: list[tuple[int, FrameReading]] = field(default_factory=list, repr=False)
    seconds: float = 0.0

    def to_dict(self) -> dict:
        """Plain JSON: the refusal's words with the frames counted, or the reading."""
        if self.refused:
            return {"refused": self.refused, "frames_used": self.frames_used, "frames": self.frames}
        return {"diameter_mm": self.diameter_mm, "pm_mm": self.pm_mm, "centre": [float(x) for x in self.centre],
                "normal": [float(x) for x in self.normal] if self.normal is not None else None,
                "frames_used": self.frames_used, "frames": self.frames, "covered_deg": self.covered_deg,
                "warnings": list(self.warnings), "position_unsure": self.position_unsure}


def combine_frames(readings: Sequence[FrameReading], T_wc: Sequence[np.ndarray], click: np.ndarray,
                   indices: Sequence[int] | None = None) -> RimReading:
    """The reading over frames (module docstring, step 5) at the click (in the frame of the poses ``T_wc``, one a
    reading), with every refusal and warning in words."""
    ok = [(f, T) for f, T in zip(readings, T_wc) if f.ok]
    why: dict[str, int] = {}
    for f in readings:
        if not f.ok:
            key = f.why.split(":")[0] if f.why.startswith("two readings") else (
                f.why.split(" mm apart")[0].rsplit(" ", 1)[0] if "apart" in f.why else f.why)
            why[key] = why.get(key, 0) + 1
    res = RimReading(None, frames_used=len(ok), frames=len(readings), frame_refusals=why,
                     frame_readings=list(zip(indices if indices is not None else range(len(readings)), readings)))
    if len(ok) < MIN_FRAMES:
        res.refused = f"only {len(ok)} frames show the rim (at least {MIN_FRAMES} needed)"
        return res
    d = np.array([f.d for f, _ in ok])
    sd = 1.4826 * float(np.median(np.abs(d - np.median(d))))
    cover = float(np.median([f.cover_deg for f, _ in ok]))
    stat = 1.2533 * sd / math.sqrt(len(d))
    pm = math.sqrt(stat ** 2 + B_SHARED_MM ** 2) * max(1.0, math.sqrt(COVER_FULL_DEG / max(cover, 1.0)))
    ts = [f.t for f, _ in ok if np.isfinite(f.t)]
    res.diameter_mm, res.d_sd, res.pm_mm, res.pm_stat, res.covered_deg = float(np.median(d)), sd, pm, stat, cover
    res.wall_mm = float(np.median(ts)) if ts else None
    Cw = np.array([T[:3, :3] @ f.centre_C + T[:3, 3] for f, T in ok])
    c = np.median(Cw, axis=0)
    res.centre = c
    nw = [T[:3, :3] @ f.normal_C for f, T in ok if f.normal_C is not None]
    if nw:
        nn = np.median(np.array(nw), axis=0)
        res.normal = nn / np.linalg.norm(nn)
    cs = 1.4826 * float(np.median(np.linalg.norm(Cw - c, axis=1)))
    res.centre_scatter = cs
    if cover < COVER_MIN_DEG:
        res.refused = f"the top rim is found on only {cover:.0f} degrees of the round"
    elif sd > MAX_D_SCATTER_MM:
        res.refused = f"the frames disagree on the diameter by {sd:.1f} mm"
    elif cs > CENTRE_REFUSE_MM:
        res.refused = f"the frames put the hole's centre {cs:.1f} mm apart: this is not one hole's rim"
    elif cs > CENTRE_WARN_MM and sd > D_SCATTER_PATH_MM:
        res.refused = (f"the frames put the hole's centre {cs:.1f} mm apart and its diameter {sd:.2f} mm apart: "
                       "the rim is not read the same in every frame")
    if cs > CENTRE_WARN_MM:
        res.warnings.append(f"the frames put the hole's centre {cs:.1f} mm apart: the camera path may be off, "
                            "and the position with it (the diameter agrees between frames)")
        res.position_unsure = True
    if res.refused is None:
        if pm > PM_LIMIT_MM:
            res.refused = f"the frames do not agree well enough on this rim: ± {pm:.2f} mm"
        else:
            n = res.normal if res.normal is not None else np.array([0.0, 0.0, 1.0])
            v = np.asarray(click, float) - c
            v = v - (v @ n) * n
            out = float(np.linalg.norm(v)) - res.diameter_mm / 2.0
            if out > CLICK_RIM_MM:
                res.refused = f"the rim read in the images is {out:.0f} mm from the click: it is another hole's"
    return res


def frames_for(click: np.ndarray, normal: np.ndarray | None, poses: dict[int, np.ndarray], cams, image_size) -> list[int]:
    """The frames that see the click (in the left image, :data:`BORDER_PX` inside it; the face not seen edge-on), at
    most :data:`MAX_FRAMES` spread evenly over them."""
    w, h = image_size
    out = []
    for i in sorted(poses):
        T = np.asarray(poses[i], float)
        cC = T[:3, :3].T @ (np.asarray(click, float) - T[:3, 3])
        uv, front = cams[0].project(cC[None])
        if not front[0] or not (BORDER_PX <= uv[0, 0] <= w - BORDER_PX and BORDER_PX <= uv[0, 1] <= h - BORDER_PX):
            continue
        if normal is not None:
            ray = T[:3, 3] - np.asarray(click, float)
            if abs(float(np.asarray(normal, float) @ ray)) < 0.3 * float(np.linalg.norm(ray)):
                continue
        out.append(i)
    if len(out) > MAX_FRAMES:
        out = [out[int(round(k))] for k in np.linspace(0, len(out) - 1, MAX_FRAMES)]
    return out


def read_hole(click: Sequence[float], normal: Sequence[float] | None, *, poses: dict[int, np.ndarray], calib: Any,
              images: Callable[[int], tuple[np.ndarray, np.ndarray] | None],
              depth: Callable[[int], tuple[np.ndarray, np.ndarray | None] | None] | None = None,
              points_of: Callable[[int, np.ndarray, np.ndarray], np.ndarray] | None = None,
              frames: Sequence[int] | None = None, path_warns: bool = False) -> RimReading:
    """The hole at ``click`` (and the face's ``normal`` there, both in the frame of ``poses``: frame index -> ``T_wc``
    of frame C, the scan's final camera path), read from the frames' raw images (``images(index)``: left and right,
    grey, as recorded) and their matcher depth near the click (``depth(index)``: ``(depth, sigma)`` on frame C, or
    ``None`` when there is none; or ``points_of(index, click in frame C, T_wc)``: frame-C points). ``frames`` names
    the frames to read (default :func:`frames_for`). ``path_warns``: the scan's camera-path check warns. Never raises
    for what it cannot read: the result is refused in words."""
    t0 = time.perf_counter()
    cal = _cal(calib)
    cams = stereo_cameras(cal)
    K_rect = np.asarray(cal["P1"], float)[:3, :3]
    w, h = (int(x) for x in cal.get("image_size", (1280, 800)))
    click = np.asarray(click, float)
    if frames is None:
        frames = frames_for(click, normal, poses, cams, (w, h))
    readings, Ts, used = [], [], []
    for i in frames:
        T = poses.get(i)
        if T is None:
            continue
        T = np.asarray(T, float)
        cC = T[:3, :3].T @ (click - T[:3, 3])
        uv, front = cams[0].project(cC[None])
        if not front[0] or not (BORDER_PX <= uv[0, 0] <= w - BORDER_PX and BORDER_PX <= uv[0, 1] <= h - BORDER_PX):
            continue
        imgs = images(i)
        if imgs is None:
            continue
        if points_of is not None:
            X = points_of(i, cC, T)
        else:
            dz = depth(i) if depth is not None else None
            X = depth_points(dz[0], dz[1], K_rect, cC) if dz is not None else np.zeros((0, 3))
        imgs = tuple(np.asarray(x, np.float32) for x in imgs)
        readings.append(read_frame(imgs, cams, X, cC))
        Ts.append(T)
        used.append(i)
    res = combine_frames(readings, Ts, click, used)
    if path_warns:
        res.warnings.append(PATH_WARNS)
    res.seconds = time.perf_counter() - t0
    return res


# --------------------------------------------------------------------------- the reading in words (the app shows them)
PATH_WARNS = "the scan's camera path check warns"
WHICH_DIAMETER = ("This is the hole's edge in the clicked face, where the face ends: not the bore further down, not "
                  "the outer edge of a chamfer or countersink, not the smallest gap through the hole.")
#: what to do after a refusal, by the refusal's words (the first that matches); else :data:`HINT_DEFAULT`
REFUSAL_HINTS = (
    ("frames show the rim", "Click on the flat face right beside the hole, within a few millimetres of its edge. The "
                            "hole must be seen from above, its whole edge in view, in at least 5 camera frames: if it "
                            "was not, scan it again looking down into it from several sides."),
    ("top rim is found on only", "Only part of the hole's edge shows in the camera frames: scan it again looking down "
                                 "into it from several sides, then make the mesh again."),
    ("another hole's", "Click on the face right beside the hole you mean, within a few millimetres of its edge."),
    ("not one hole's rim", "Click on the face right beside the hole you mean, not between two holes."),
)
HINT_DEFAULT = ("The camera frames do not read this edge the same way (a shiny, worn or chamfered edge, or something "
                "close behind the hole): measure it with calipers or a pin gauge.")


def refusal_hint(refused: str) -> str:
    """What to do after the refusal ``refused`` (:attr:`RimReading.refused`), in plain words."""
    return next((h for needle, h in REFUSAL_HINTS if needle in refused), HINT_DEFAULT)


def reading_words(res: RimReading) -> list[str]:
    """An answered reading's sentences, in the order shown: the diameter with its ± and the frames, which diameter it
    is, and, when the camera path check warns or the frames place the centre apart, that the position may be off
    while the diameter does not depend on the path."""
    out = [f"Ø {res.diameter_mm:.2f} mm ± {res.pm_mm:.2f} from the camera images (the rim where the face ends, read "
           f"in {res.frames_used} frames).", WHICH_DIAMETER]
    why = []
    if PATH_WARNS in res.warnings:
        why.append("the scan's camera path check warns")
    if res.position_unsure and res.centre_scatter is not None:
        why.append(f"the camera frames place its centre {res.centre_scatter:.1f} mm apart")
    elif res.position_unsure:
        why.append("the camera frames place its centre apart")
    if why:
        out.append(f"The hole's position may be off ({' and '.join(why)}); its diameter does not depend on the "
                   "camera path.")
    return out
