"""A hole read from the raw frames: the rim found in the camera images (ENGINE_PLAN E-D3, X15's first test).

**What it reports:** the diameter of the hole's *entrance rim in the face's plane* (where the clicked face ends).
That is not the bore below it, not the outer edge of a chamfer or countersink, and not the smallest clearance
through the hole. The circle is fitted robustly, so a burr, a nick or an out-of-round rim is passed over, not
reported: the answer says nothing about them.

A study tool. It changes no scan and no mesh. Today's Hole tool reads the fused mesh, where the matcher carries the
surface into a hole over open space (``VALIDATION.md``, "Holes filled in"). This reads the rim where the images show
it. Around a click:

1. **Frames:** every ``every``-th frame of the recording in which the click is in the left image, with the face
   turned towards the camera.
2. **The face's plane, per frame:** from that frame's own matcher depth (frame C, the rectified left camera).
   - First pass: the pixels within 25 mm of the click whose matcher sigma is under 2 mm (fill has a sigma of tens of mm).
   - Second pass, once the hole is seeded: an annulus outside the rim, beyond the fill's reach.
   - Each pass is a plane trimmed of its strays.
3. **The seed:** the left image is sampled on that plane in a disk round the click and median-filtered, so the
   projector's dots do not count, then split in two by Otsu's threshold. The dark patch nearest the click gives a
   centre and a radius. Over open space, a hole shows the far background, which is dimmer.
4. **The rim:** radial lines in the plane, both raw cameras (their lens distortion applied), in sectors of
   ``SECTOR_DEG``.
   - Per sector, a robust step (a change point under L1), then the 50 % crossing of the sector's median profile near
     it (:func:`sector_edge`).
5. **Top rim or bottom rim:** a plate has a wall. Where a camera sees the wall (its centre lies on the hole's side of
   the wall's tangent plane at that point of the rim), the strongest step is the wall's foot against the background.
   Back on the top plane that lands ``t * tan(view angle)`` inside the rim. So the circle is fitted to the sectors
   where the wall is hidden, where the top rim is the occluding corner. The other sectors are reported apart, as the
   bias they would bring.
6. **One circle per frame**, in that frame's camera coordinates. The diameter therefore does not depend on the pose.
   The centre goes into the world with the frame's pose. Over frames: the median diameter with a ± from the
   frames' scatter, and the median centre. Refusals and warnings are said in words.

**Step 2** (``run2``; the reading above is step 1, ``run``): one model of the hole per frame instead of one edge a
line. It keeps up to three steps on each sector line, of either sign, and fits the entrance circle and the exit circle
a wall ``t`` below along the face's normal (``t`` unknown, 0 to 10 mm). Each camera sees the exit circle projected on
the face and only inside the top rim. A frame whose two cameras disagree on the top rim is refused
(:func:`fit_hole_model`).

The ± (:func:`combine_model`) is the diameter's: the frames' scatter, plus a shared 0.1 mm that more frames, and both
cameras together, do not shrink. It widens when little of the rim is covered, and the reading refuses below 120°.

``render-off`` and ``scenes`` make the study frames without the projector's dots (``RenderParams.dots_on=False``) and
the study plates (1 and 5 mm thick, a bright floor). A frame without dots is read alone. Its plane comes from its two
neighbours' depth, and its tracked pose is taken halfway between theirs.

**Step 3** (``run3``; :class:`Step3`; each change can be switched off for a comparison):
- **The seed** takes a patch of either sign.
- **The top rim** counts only steps that both cameras put on the face's plane (:func:`stereo_paired`).
- **The thickness** can be given (``t`` held), and a frame is refused when two wall heights fit with diameters more
  than 0.2 mm apart.
- **The centres:** the reading refuses when the frames put the hole's centre more than 3 mm apart, or more than
  1 mm apart while their diameters also scatter. With diameters that agree, it gives the diameter and calls the
  position unsure.

``track-study`` records the study scenes whole and tracks them on the processor. ``track-drop`` tracks a recording
again with its dots-off frames left out.

Run from ``software/`` (CPU only; it reads PNG frames and the scorecard's cached matcher depth)::

    python tools/hole_rim_study.py run [--recs holes-high-s0 ...] [--poses true tracked] [--out rows.jsonl]

The cache is the scorecard's (``--cache``, as ``tools/scorecard.py``). Tracked poses and today's Hole tool readings
come from ``runs/gpu/<settings>/<rec>/tracked``.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SOFTWARE = Path(__file__).resolve().parents[1]
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))

SECTOR_DEG = 6.0  # the rim's sectors
STEP_DEG = 0.5  # radial lines this far apart
BIN_MM = 0.05  # the sector's profile, in bins this wide
CROSS_MM = 1.0  # the 50 % crossing is looked for this close to the change point
SEED_DISK_MM = 30.0  # the seed is searched in this disk round the click ...
SEED_CELL_MM = 0.25  # ... on a grid this fine ...
SEED_MEDIAN_MM = 2.25  # ... median-filtered over this
SEED_REACH_MM = 4.0  # the dark patch must come this close to the click
PLANE_DISK_MM = 25.0  # the plane's first pass: depth within this of the click ...
PLANE_SIGMA_MM = 2.0  # ... whose matcher sigma is below this
RING_MM = (2.0, 12.0)  # the plane's second pass: this annulus outside the seed's radius
MIN_POINTS = 20  # a frame's circle needs this many sector edges ...
MIN_ARC_DEG = 120.0  # ... spread over at least this much of the round
MIN_FRAMES = 5  # the reading refuses with fewer frames ...
MAX_D_SCATTER_MM = 1.0  # ... or when the frames' diameters scatter more than this (robust sd)
WARN_CENTRE_SCATTER_MM = 0.5  # the frames' centres scattering more than this: a warning


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


def stereo_cameras(cal: dict) -> tuple[Camera, Camera]:
    """The raw left and right cameras in frame C (the rectified left camera) from a ``calibration.json``'s
    ``stereo``. ``R1`` turns raw left rays into frame C; ``x_right = R x_left + T``."""
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
    """A face's plane in frame C: a point ``o``, in-plane axes ``u``, ``v`` and the normal ``n`` (towards the
    camera)."""

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
    """The frame-C points of the depth pixels (``depth`` z in mm; ``K_rect`` the rectified intrinsics) in a window
    of 35 mm round the frame-C point ``near``, those with a matcher sigma under :data:`PLANE_SIGMA_MM`."""
    z0 = float(near[2])
    if z0 <= 1.0:
        return np.zeros((0, 3))
    fx, fy, cx, cy = K_rect[0, 0], K_rect[1, 1], K_rect[0, 2], K_rect[1, 2]
    u0, v0 = fx * near[0] / z0 + cx, fy * near[1] / z0 + cy
    hw = int(math.ceil(35.0 * fx / z0))
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
    """The trimmed plane through the points ``X`` (frame C) that ``select(X)`` keeps; its origin is ``near``
    dropped onto it, its normal towards the camera."""
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


def plane_from_depth(depth: np.ndarray, sigma: np.ndarray | None, K_rect: np.ndarray, near: np.ndarray,
                     select) -> Plane | None:
    """:func:`plane_from_points` on :func:`depth_points` round ``near``."""
    return plane_from_points(depth_points(depth, sigma, K_rect, near), near, select)


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


def find_seed(img: np.ndarray, cam: Camera, plane: Plane, click_xy: np.ndarray,
              either: bool = False) -> tuple[np.ndarray, float] | str:
    """``(centre (2,) in the plane, radius)`` of the dark patch nearest the click, or the reason there is none.

    With ``either`` (step 3) a patch brighter than the face counts too (a lit background seen through the hole),
    found the same way above the face's median, and of the closed patches of either kind within reach of the click
    the largest is taken."""
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
    # the laser lines saturate: clipped at the face's median, the histogram holds the face and the dark only
    med = float(np.median(M[inside]))
    masks = [(M < _otsu(np.minimum(M[inside], med))) & inside]
    if either:
        masks.append((M > _otsu(np.maximum(M[inside], med))) & inside)
    edge = (gx ** 2 + gy ** 2) > (R - 1.0) ** 2
    d_click = np.hypot(gx, gy)
    best = None
    for mask in masks:
        lab, n = ndimage.label(mask)
        on_edge = set(np.unique(lab[edge & mask]).tolist())
        for j in range(1, n + 1):
            m = lab == j
            area = m.sum() * SEED_CELL_MM ** 2
            if area < math.pi * 1.0 ** 2 or j in on_edge:
                continue
            dmin = float(d_click[m].min())
            if dmin > SEED_REACH_MM:
                continue
            key = -area if either else dmin
            if best is None or key < best[0]:
                best = (key, m, area)
    if best is None:
        return "no closed " + ("patch" if either else "dark patch") + " within reach of the click"
    m = best[1]
    centre = np.array([gx[m].mean(), gy[m].mean()]) + click_xy
    return centre, math.sqrt(best[2] / math.pi)


# --------------------------------------------------------------------------- the rim
def profile_edges(P: np.ndarray, rs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(change points, 50 % crossings)`` (NaN where none) of radial profiles ``P`` (sectors x radii ``rs``,
    each the median over its sector's lines, so that a projector dot does not count), dark inside (small radius),
    bright outside.

    The change point: the split with the least L1 cost about each side's median, the outside brighter. The
    crossing: where the profile crosses half-way between the medians of the inside (more than 0.5 mm inside the
    change point) and the outside (more than 0.5 mm out), nearest the change point within :data:`CROSS_MM`."""
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


def sector_edge(r: np.ndarray, I: np.ndarray) -> tuple[float | None, float | None]:
    """:func:`profile_edges` for one sector's scattered samples (radius ``r``, intensity ``I``): the profile is the
    median in bins of :data:`BIN_MM`."""
    ok = np.isfinite(I)
    r, I = r[ok], I[ok]
    if len(r) < 40:
        return None, None
    bins = np.floor((r - r.min()) / BIN_MM).astype(int)
    rc = r.min() + (np.arange(bins.max() + 1) + 0.5) * BIN_MM
    prof = np.array([np.median(I[bins == k]) if np.any(bins == k) else np.nan for k in range(len(rc))])
    good = np.isfinite(prof)
    cp, half = profile_edges(prof[good][None], rc[good])
    return (None if np.isnan(cp[0]) else float(cp[0])), (None if np.isnan(half[0]) else float(half[0]))


def fit_circle(xy: np.ndarray) -> tuple[np.ndarray, float, float, np.ndarray] | None:
    """``(centre, radius, rms, kept)``: an algebraic circle to start, then a geometric one weighted by Tukey's
    biweight (scale: the residuals' robust sd, at least 0.02 mm), so that a sector a projector dot spoiled does not
    pull it; ``kept``: the points with weight."""
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


def rim_edges(imgs, cams, plane: Plane, centre: np.ndarray, r_seed: float):
    """Edge points round the seed, one row a sector and camera: ``(angle_deg, camera, wall_seen, crossing,
    change point, k)``, the crossing and the change point as radii from ``centre`` (plane coordinates; NaN where
    there is none); ``k``: where the wall is seen, how far its foot would land inside the rim on the top plane per
    mm of wall (the tangent of the view into the wall), else 0."""
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
            # how far the wall's foot lands inside the rim on the top plane, per mm of wall: (C - P).w / height
            k = float(max(0.0, -((C - P) @ w3) / max((C - P) @ plane.n, 1e-6)))
            rows.append(((si + 0.5) * SECTOR_DEG, ci, wall_seen, half, cp, k))
    return np.array(rows, dtype=float).reshape(-1, 6)


def edge_xy(E: np.ndarray, centre: np.ndarray, col: int) -> np.ndarray:
    """The plane points of :func:`rim_edges`' rows for the crossing (``col`` 3) or the change point (4)."""
    a = np.radians(E[:, 0])
    return np.column_stack([centre[0] + E[:, col] * np.cos(a), centre[1] + E[:, col] * np.sin(a)])


@dataclass
class FrameReading:
    ok: bool
    why: str = ""
    centre_C: np.ndarray | None = None  # the hidden-half circle's centre, frame C
    d: float = float("nan")  # its diameter
    d_all: float = float("nan")  # every sector's circle
    d_cp: float = float("nan")  # the hidden half with the change point instead of the crossing
    rms: float = float("nan")
    n: int = 0
    wall_seen_offset: float = float("nan")  # median radial offset of the wall-seen edges from the hidden circle
    hidden_offset: float = float("nan")
    t_app: float = float("nan")  # the wall-seen edges' offset per unit k: the wall height they behave as (mm)
    k_seen: float = float("nan")  # the median k of the wall-seen edges


def read_frame(imgs, cams, depth, sigma, K_rect, click_C: np.ndarray) -> FrameReading:
    """One frame's circle round the frame-C click (module docstring, steps 2 to 6)."""
    p1 = plane_from_depth(depth, sigma, K_rect, click_C,
                          lambda X: np.linalg.norm(X - click_C, axis=1) < PLANE_DISK_MM)
    if p1 is None:
        return FrameReading(False, "no plane in the depth round the click")
    if p1.n @ (-p1.o) / np.linalg.norm(p1.o) < 0.3:
        return FrameReading(False, "the face is seen too steeply")
    seed = find_seed(imgs[0], cams[0], p1, np.zeros(2))
    if isinstance(seed, str):
        return FrameReading(False, seed)
    c_s, r_s = seed
    c3 = p1.world(c_s[None])[0]

    def ring(X):
        d = X - c3
        d -= np.outer(d @ p1.n, p1.n)
        rr = np.linalg.norm(d, axis=1)
        return (rr > r_s + RING_MM[0]) & (rr < r_s + RING_MM[1])

    pl = plane_from_depth(depth, sigma, K_rect, c3, ring) or p1
    # the seed's centre in the second plane's coordinates (its origin is the seed's centre dropped onto it)
    z = np.zeros(2)
    E = rim_edges(imgs, cams, pl, z, r_s)
    if not len(E):
        return FrameReading(False, "no edge found round the seed")
    has = np.isfinite(E[:, 3])
    hid = (E[:, 2] == 0) & has
    if hid.sum() < MIN_POINTS or _arc_deg(E[hid, 0]) < MIN_ARC_DEG:
        return FrameReading(False, "too little of the rim where the wall is hidden")
    xy = edge_xy(E, z, 3)
    f = fit_circle(xy[hid])
    if f is None:
        return FrameReading(False, "no circle")
    c, rad, rms, keep = f
    fa = fit_circle(xy[has])
    hcp = E[:, 2] == 0
    fcp = fit_circle(edge_xy(E, z, 4)[hcp])
    res = np.hypot(*(xy - c).T) - rad
    seen = (E[:, 2] == 1) & has
    # the apparent wall height: the wall-seen edges' offset against k (offset = -t k if the foot is what is found)
    t_app = float("nan")
    if seen.sum() >= 10 and np.ptp(E[seen, 5]) > 0.02:
        kk, rr = E[seen, 5], res[seen]
        good = np.abs(rr - np.median(rr)) < 4.0 * 1.4826 * np.median(np.abs(rr - np.median(rr))) + 0.05
        t_app = float(-(kk[good] @ rr[good]) / max(kk[good] @ kk[good], 1e-9))
    return FrameReading(True, "", pl.world(c[None])[0], 2 * rad, 2 * fa[1] if fa else float("nan"),
                        2 * fcp[1] if fcp else float("nan"), rms, int(keep.sum()),
                        float(np.median(res[seen])) if seen.any() else float("nan"), float(np.median(res[hid])),
                        t_app, float(np.median(E[seen, 5])) if seen.any() else float("nan"))


def combine(frames: list[FrameReading], T_wc: list[np.ndarray]) -> dict:
    """The reading over frames (module docstring, step 6): diameter, its ±, centre (world), scatter, refusals."""
    ok = [(f, T) for f, T in zip(frames, T_wc) if f.ok]
    why: dict[str, int] = {}
    for f in frames:
        if not f.ok:
            why[f.why] = why.get(f.why, 0) + 1
    out: dict = {"frames": len(frames), "frames_used": len(ok), "frame_refusals": why, "warnings": []}
    if len(ok) < MIN_FRAMES:
        out["refused"] = f"only {len(ok)} frames show the rim (at least {MIN_FRAMES} needed)"
        return out
    d = np.array([f.d for f, _ in ok])
    sd = 1.4826 * float(np.median(np.abs(d - np.median(d))))
    out.update(d=float(np.median(d)), d_sd=sd, pm=1.2533 * sd / math.sqrt(len(d)),
               d_all=float(np.nanmedian([f.d_all for f, _ in ok])), d_cp=float(np.nanmedian([f.d_cp for f, _ in ok])),
               points=int(sum(f.n for f, _ in ok)),
               wall_seen_offset=float(np.nanmedian([f.wall_seen_offset for f, _ in ok])),
               hidden_offset=float(np.nanmedian([f.hidden_offset for f, _ in ok])),
               t_app=float(np.nanmedian([f.t_app for f, _ in ok])) if any(np.isfinite(f.t_app) for f, _ in ok) else None,
               k_seen=float(np.nanmedian([f.k_seen for f, _ in ok])) if any(np.isfinite(f.k_seen) for f, _ in ok) else None)
    Cw = np.array([T[:3, :3] @ f.centre_C + T[:3, 3] for f, T in ok])
    c = np.median(Cw, axis=0)
    out["centre"] = c.tolist()
    out["centre_scatter"] = 1.4826 * float(np.median(np.linalg.norm(Cw - c, axis=1)))
    if sd > MAX_D_SCATTER_MM:
        out["refused"] = f"the frames disagree on the diameter by {sd:.1f} mm"
    if out["centre_scatter"] > WARN_CENTRE_SCATTER_MM:
        out["warnings"].append(f"the frames put the hole's centre {out['centre_scatter']:.1f} mm apart: "
                               "the camera path may be off, and the position with it")
    return out


# --------------------------------------------------------------------------- the study
RECS = ([f"holes-high-s{s}" for s in range(3)] + [f"holes-low-s{s}" for s in range(3)]
        + [f"panel200-s{s}" for s in range(5)])


def holes_of(rec: Path) -> list[dict]:
    """The recording's holes: name, true diameter and centre (world), the three clicks (the scorecard's)."""
    from ohs import geometry as G
    from tools import scorecard as S

    spec = S.SceneSpec(**json.loads((rec / "scorecard_spec.json").read_text(encoding="utf-8")))
    if spec.kind == "holes":
        T = S.holes_plate_T(spec.build["gap"])
        out = []
        for k, (hx, hy, r) in S.HP_HOLES.items():
            clicks = [G.transform_points(T, np.array([[hx + (r + 1.5) * math.cos(math.radians(a) + 0.3),
                                                       hy + (r + 1.5) * math.sin(math.radians(a) + 0.3), S.HP_T]]))[0]
                      for a in (0.0, 120.0, 240.0)]
            out.append({"hole": k, "d_true": 2 * r,
                        "centre_true": G.transform_points(T, np.array([[hx, hy, S.HP_T]]))[0], "clicks": clicks})
        return out
    sc = S.scene_of(spec)
    h = S.panel_hole_world(sc)
    panel = sc.primitives[sc.object_id("panel")]
    hx, hy, r = h["local"]
    clicks = []
    for a in (0.0, 120.0, 240.0):
        x, y = hx + (r + 1.5) * math.cos(math.radians(a)), hy + (r + 1.5) * math.sin(math.radians(a))
        z = float(panel.height(np.array([x]), np.array([y]))[0][0])
        clicks.append(G.transform_points(panel.T_wo, np.array([[x, y, z]]))[0])
    return [{"hole": "h25", "d_true": h["diameter"], "centre_true": np.asarray(h["centre"]), "clicks": clicks}]


def poses_of(cache: Path, rec: Path, source: str, settings: str) -> tuple[dict[int, np.ndarray], dict]:
    """World-from-frame-C poses for ``true`` (the ground truth) or ``tracked`` (the run's final trajectory, placed
    in the world with its placement), and what the run says (path check, today's Hole tool)."""
    from ohs.io.recording import RecordingReader
    from ohs.track.posegraph import read_trajectory

    run = cache / "runs" / "gpu" / settings / rec.name / source
    info: dict = {}
    res = run / "result.json"
    if res.is_file():
        r = json.loads(res.read_text(encoding="utf-8"))
        info["today"] = {k: v for k, v in r["metrics"].items() if k.endswith(("_d_err", "_centre_err", "_answered"))}
    rep = run / "proc" / "stages" / "track" / "report.json"
    if rep.is_file():
        pc = json.loads(rep.read_text(encoding="utf-8")).get("path_check") or {}
        info["path_check"] = pc.get("state")
    if source == "true":
        return RecordingReader(rec).ground_truth_poses(), info
    T = np.asarray(json.loads(res.read_text(encoding="utf-8"))["info"]["T"], float)
    return {int(i): T @ np.asarray(Tm, float) for i, _, ok, Tm in read_trajectory(run / "proc" / "trajectory.csv") if ok}, info


class Frames:
    """The recording's frames and cached depth, loaded once each."""

    def __init__(self, rec: Path, depth_dir: Path) -> None:
        self.rec, self.depth_dir = rec, depth_dir
        self._img: dict[int, tuple] = {}
        self._dep: dict[int, tuple] = {}

    def images(self, i: int):
        import cv2

        if i not in self._img:
            sbs = cv2.imread(str(self.rec / "frames" / f"{i:06d}.png"), cv2.IMREAD_GRAYSCALE)
            w = sbs.shape[1] // 2
            self._img[i] = (np.ascontiguousarray(sbs[:, :w], np.float32), np.ascontiguousarray(sbs[:, w:], np.float32))
        return self._img[i]

    def depth(self, i: int):
        if i not in self._dep:
            z = np.load(self.depth_dir / f"{i:06d}.npz")
            self._dep[i] = (z["depth"], z["sigma"])
        return self._dep[i]


def read_click(fr: Frames, cams, K_rect, poses: dict, click_w: np.ndarray, every: int) -> dict:
    frames, Ts = [], []
    for i in sorted(poses):
        if i % every:
            continue
        T = poses[i]
        cC = T[:3, :3].T @ (click_w - T[:3, 3])
        uv, front = cams[0].project(cC[None])
        if not front[0] or not (60 <= uv[0, 0] <= 1280 - 60 and 60 <= uv[0, 1] <= 800 - 60):
            continue
        dep, sig = fr.depth(i)
        frames.append(read_frame(fr.images(i), cams, dep, sig, K_rect, cC))
        Ts.append(T)
    return combine(frames, Ts)


def run(cache: Path, recs: list[str], sources: list[str], out: Path, settings: str, every: int) -> None:
    for name in recs:
        rec = cache / "recs" / name
        cal = json.loads((rec / "calibration.json").read_text(encoding="utf-8"))["stereo"]
        cams = stereo_cameras(cal)
        K_rect = np.asarray(cal["P1"], float)[:3, :3]
        fr = Frames(rec, cache / "depth" / "gpu" / name)
        holes = holes_of(rec)
        for src in sources:
            poses, info = poses_of(cache, rec, src, settings)
            for h in holes:
                for k, click in enumerate(h["clicks"]):
                    t0 = time.perf_counter()
                    r = read_click(fr, cams, K_rect, poses, np.asarray(click, float), every)
                    if info.get("path_check") == "warn":
                        r["warnings"].append("the scan's camera path check warns")
                    row = {"rec": name, "poses": src, "hole": h["hole"], "click": k, "d_true": h["d_true"],
                           "path_check": info.get("path_check"), "today": info.get("today"),
                           "s": round(time.perf_counter() - t0, 1), **r}
                    if "d" in r:
                        row["d_err"] = r["d"] - h["d_true"]
                        row["d_all_err"] = r["d_all"] - h["d_true"]
                        row["d_cp_err"] = r["d_cp"] - h["d_true"]
                        row["centre_err"] = float(np.linalg.norm(np.asarray(r["centre"]) - h["centre_true"]))
                    with out.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(row) + "\n")
                    print(f"{name} {src} {h['hole']} click {k}: "
                          + (f"d_err {row['d_err']:+.3f} ±{r['pm']:.3f} centre {row['centre_err']:.2f} "
                             f"frames {r['frames_used']}/{r['frames']}" if "d_err" in row else f"no d ({r.get('refused')})")
                          + (f" REFUSED: {r['refused']}" if "refused" in r and "d_err" in row else "")
                          + f" [{row['s']} s]", flush=True)


# =========================================================================== step 2: one model of the hole
B_SHARED_MM = 0.1  # the ±'s shared part (an edge bias the frames share: lighting, rim, plane, calibration), simulation
COVER_MIN_DEG = 120.0  # a reading needs the top rim found over at least this much of the round (median over frames)
COVER_FULL_DEG = 270.0  # below this the ± widens by sqrt(COVER_FULL_DEG / covered)
CENTRE_WARN_MM = 1.0  # the frames' centres scattering more than this: a warning (1.5 x the largest at true poses, run 1)
CAM_AGREE_MM = 0.15  # the two cameras' top-rim radii must agree this well, or the frame is refused
GATE_MM = 0.3  # a candidate step counts for a circle within this of it
T_MAX_MM = 10.0  # the wall's height is looked for between 0 and this
N_CAND = 3  # candidate steps kept per sector line
POINTS_MM = 45.0  # depth points are taken within this of the click
# step 3 (fixed before its runs from step 2's measured scatter; NOTES.md)
PAIR_MM = 0.15  # a top-rim step must have a step on the same line in the other camera within this
ALT_T_MM = 1.0  # another wall height at least this far from the one fitted ...
ALT_SHARE = 0.9  # ... that explains at least this share as many steps ...
ALT_D_MM = 0.2  # ... and gives a diameter this much different: two readings fit, the frame is refused
CENTRE_REFUSE_MM = 3.0  # centres this far apart: refused (1.5 x the largest of a good reading, tracked, 1.94)
D_SCATTER_PATH_MM = 0.3  # centres more than CENTRE_WARN_MM apart and diameters scattering more than this: refused


def candidate_steps(prof: np.ndarray, rs: np.ndarray, n: int = N_CAND) -> list[list[tuple[float, float, float]]]:
    """Per sector profile (rows of ``prof`` over the radii ``rs``): up to ``n`` steps ``(radius, sign, strength)``,
    the strongest local peaks of the smoothed profile's slope, of either sign, above 4 robust sd of the slope.
    The position is the peak's (a symmetric blur leaves it in place), to a fraction of a sample by a parabola."""
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
    """Candidate steps round the plane's origin (the seed's centre): rows ``(x, y, camera, strength, sector,
    radius)``."""
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


def exit_circles(p: np.ndarray, Cip: np.ndarray, h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The exit circle (``p = (cx, cy, R, t)``: a wall ``t`` below, the hole square to the face) as each camera sees
    it on the face's plane: centres (cameras, 2) and radii. Seen from a camera ``h`` above the face at ``Cip`` (in
    the plane), a circle ``t`` below projects to the circle moved towards the camera by ``s = t / (h + t)`` of the
    way and shrunk by ``1 - s``."""
    c, R, t = p[:2], p[2], p[3]
    s = t / (h + t)
    return c[None] + s[:, None] * (Cip - c[None]), R * (1.0 - s)


def assign_steps(p, xy, cam, Cip, h, gate, top_ok=None):
    """Each candidate's circle (0 top, 1 exit, -1 neither) and its residual. The exit circle counts only inside
    the top rim (elsewhere the plate hides it), and only when ``t`` is not about 0. With ``top_ok`` (step 3) only
    the candidates it marks may be the top rim (those both cameras put on the face's plane)."""
    c, R = p[:2], p[2]
    rt = np.hypot(*(xy - c).T) - R
    ce, Re = exit_circles(p, Cip, h)
    re = np.hypot(*(xy - ce[cam]).T) - Re[cam]
    lab = np.full(len(xy), -1)
    res = np.full(len(xy), np.nan)
    top = np.abs(rt) <= gate
    if top_ok is not None:
        top &= top_ok
    ex = (np.abs(re) <= gate) & (rt < -gate) & (p[3] > 0.05)
    use_exit = ex & (~top | (np.abs(re) < np.abs(rt)))
    lab[top & ~use_exit], res[top & ~use_exit] = 0, rt[top & ~use_exit]
    lab[use_exit], res[use_exit] = 1, re[use_exit]
    return lab, res


@dataclass
class ModelReading:
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
    bg_n: int = 0  # round 4: the frame's depth points inside the read circle ...
    bg_near: float = float("nan")  # ... the share of them 6 to 50 mm below the face ...
    bg_depth: float = float("nan")  # ... and their median depth below it (mm)


def _fit_from(xy, cam, Cip, h, c0, R0, t_start, t_known, top_ok):
    """Rounds of assignment and a robust fit of ``(centre, R, t)`` from ``t_start`` (``t`` held at ``t_known``
    when given): ``(p, lab, gate)`` or the reason there is none."""
    from scipy.optimize import least_squares

    p = np.array([c0[0], c0[1], R0, t_start])
    gate = GATE_MM
    for _ in range(4):
        lab, _ = assign_steps(p, xy, cam, Cip, h, gate, top_ok)
        if (lab == 0).sum() < MIN_POINTS:
            return "too few steps on one circle"
        free_t = t_known is None and bool((lab == 1).sum() >= 10)
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
    lab, _ = assign_steps(p, xy, cam, Cip, h, gate, top_ok)
    return p, lab, gate


def fit_hole_model(cand: np.ndarray, Cip: np.ndarray, h: np.ndarray, c0: np.ndarray, R0: float,
                   t_known: float | None = None, top_ok: np.ndarray | None = None,
                   two_hyp: bool = False) -> ModelReading | str:
    """One model of the hole fitted to the candidate steps of both cameras: the entrance circle, the exit circle
    ``t`` below along the face's normal, the wall between (:func:`exit_circles`, each camera its own view of the
    exit circle, seen only inside the top rim). ``t`` starts from a search over 0 to :data:`T_MAX_MM` for the
    height that explains the most candidates (the smaller on a tie); then rounds of assignment and a robust fit
    of ``(centre, R, t)``, ``t`` held when fewer than 10 steps lie on the exit circle. A frame whose two cameras
    put the top rim more than :data:`CAM_AGREE_MM` apart, or where either finds fewer than 10 top-rim steps, is
    refused: only a rim both cameras agree on is read.

    Step 3: ``t_known`` (the plate's thickness, when the user gives it or the mesh shows it) holds ``t`` there;
    ``top_ok`` limits the top rim to the candidates both cameras put on the face's plane (:func:`stereo_paired`);
    ``two_hyp``: when another ``t``, at least :data:`ALT_T_MM` away, explains nearly as many candidates
    (:data:`ALT_SHARE`) and its fit gives a diameter more than :data:`ALT_D_MM` different, the frame is refused:
    two readings of the rim fit."""
    xy, cam = cand[:, :2], cand[:, 2].astype(int)
    ts = np.array([t_known]) if t_known is not None else np.arange(0.0, T_MAX_MM + 1e-9, 0.1)
    counts = np.zeros(len(ts), int)
    p = np.array([c0[0], c0[1], R0, 0.0])
    for k, t in enumerate(ts):
        p[3] = t
        lab, _ = assign_steps(p, xy, cam, Cip, h, GATE_MM, top_ok)
        counts[k] = int((lab >= 0).sum())
    kb = int(np.argmax(counts))  # the first maximum: the smaller t on a tie
    f = _fit_from(xy, cam, Cip, h, c0, R0, float(ts[kb]), t_known, top_ok)
    if isinstance(f, str):
        return f
    p, lab, gate = f
    if two_hyp and t_known is None:
        far = np.abs(ts - p[3]) >= ALT_T_MM
        if far.any():
            ka = int(np.flatnonzero(far)[np.argmax(counts[far])])
            if counts[ka] >= ALT_SHARE * counts[kb]:
                g = _fit_from(xy, cam, Cip, h, c0, R0, float(ts[ka]), None, top_ok)
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
    t_out = float(p[3]) if (t_known is not None or n_exit >= 10) else float("nan")
    return ModelReading(True, "", None, 2.0 * float(p[2]), t_out, _arc_deg(np.degrees(np.arctan2(d[:, 1], d[:, 0]))),
                        int(top.sum()), n_exit, abs(radii[0] - radii[1]), np.asarray(p[:2], float))


def stereo_paired(cand: np.ndarray, tol: float = PAIR_MM) -> np.ndarray:
    """For candidate rows ``(x, y, camera, strength, sector, radius)``: True where the other camera has a step on the
    same sector line within ``tol`` of the same radius. Both cameras' lines are drawn on the face's plane, so a step
    on the plane (the entrance rim) lands at the same radius in both, while one below or above it (a dot or a
    shadow on the floor, the exit rim) lands apart by the baseline's parallax: about 2.3 mm for a floor 10 mm under
    a face 250 mm away, less where the line runs across the baseline."""
    ok = np.zeros(len(cand), bool)
    key = cand[:, 4].astype(int)
    for s in np.unique(key):
        m = np.flatnonzero(key == s)
        a, b = m[cand[m, 2] == 0], m[cand[m, 2] == 1]
        if not len(a) or not len(b):
            continue
        dd = np.abs(cand[a, 5][:, None] - cand[b, 5][None, :])
        ok[a] = dd.min(axis=1) <= tol
        ok[b] = dd.min(axis=0) <= tol
    return ok


def frame_plane_seed(img0, cam0, X: np.ndarray, click_C: np.ndarray, either: bool = False):
    """The face's plane and the hole's seed in one frame from depth points ``X`` (frame C) round the click:
    ``(plane with its origin at the seed's centre, seed radius)``, or the reason there is none."""
    p1 = plane_from_points(X, click_C, lambda Y: np.linalg.norm(Y - click_C, axis=1) < PLANE_DISK_MM)
    if p1 is None:
        return "no plane in the depth round the click"
    if p1.n @ (-p1.o) / np.linalg.norm(p1.o) < 0.3:
        return "the face is seen too steeply"
    seed = find_seed(img0, cam0, p1, np.zeros(2), either=either)
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


@dataclass
class Step3:
    """Step 3's changes, each one that can be switched off for a comparison."""

    either: bool = True  # a seed of either sign
    stereo: bool = True  # the top rim only from steps both cameras put on the face's plane
    two_hyp: bool = True  # refuse a frame where two readings of the rim fit with diameters 0.2 mm apart
    centre_rule: bool = True  # refusals by the centres' scatter (combine_model)


STEP2 = Step3(False, False, False, False)


def read_frame_model(imgs, cams, X: np.ndarray, click_C: np.ndarray, t_known: float | None = None,
                     cfg: Step3 = STEP2) -> ModelReading:
    """One frame read with the model of the hole (step 2; with ``cfg`` and ``t_known``, step 3)."""
    return read_frame_models(imgs, cams, X, click_C, [t_known], cfg)[0]


def read_frame_models(imgs, cams, X: np.ndarray, click_C: np.ndarray, t_list: list, cfg: Step3 = STEP2,
                      X_bg: np.ndarray | None = None) -> list:
    """One frame read with the model of the hole for each ``t`` of ``t_list`` (``None``: left free): plane and seed
    (:func:`frame_plane_seed`), the hidden-half circle as the start, both cameras' candidate steps, then
    :func:`fit_hole_model` once for each. The image work is done once."""
    ps = frame_plane_seed(imgs[0], cams[0], X, click_C, either=cfg.either)
    if isinstance(ps, str):
        return [ModelReading(False, ps) for _ in t_list]
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
        return [ModelReading(False, "no steps round the seed") for _ in t_list]
    Cip = np.array([pl.local(c.centre[None])[0] for c in cams])
    h = np.array([float((c.centre - pl.o) @ pl.n) for c in cams])
    top_ok = stereo_paired(cand) if cfg.stereo else None
    out = []
    for t_known in t_list:
        m = fit_hole_model(cand, Cip, h, np.asarray(c0, float), float(R0), t_known, top_ok, cfg.two_hyp)
        if isinstance(m, str):
            out.append(ModelReading(False, m))
            continue
        m.centre_C = pl.world(m.centre_xy[None])[0]
        m.bg_n, m.bg_near, m.bg_depth = background_stats(X if X_bg is None else X_bg, pl.o, pl.n, m.centre_C,
                                                         m.d / 2.0, depth=True)
        out.append(m)
    return out


def combine_model(frames: list[ModelReading], T_wc: list[np.ndarray], centre_rule: bool = False) -> dict:
    """The model reading over frames. Diameter: the median. Its ± (1 sigma): the frames' scatter
    (1.2533 x robust sd / sqrt(frames)) and a shared part :data:`B_SHARED_MM` that more frames do not shrink, as
    sqrt(scatter^2 + b^2), times sqrt(:data:`COVER_FULL_DEG` / covered) below that coverage. Refused below
    :data:`COVER_MIN_DEG` of covered rim, with fewer than :data:`MIN_FRAMES` frames, or when the frames'
    diameters scatter more than :data:`MAX_D_SCATTER_MM`; a warning when their centres scatter more than
    :data:`CENTRE_WARN_MM`.

    ``centre_rule`` (step 3), on the frames' centres in the world, which carry both the reading's own error and
    the camera path's: refused above :data:`CENTRE_REFUSE_MM` whatever else; refused above
    :data:`CENTRE_WARN_MM` when the frames' diameters also scatter more than :data:`D_SCATTER_PATH_MM` (a rim
    read in different places by different frames: the diameter is not to be trusted either); above
    :data:`CENTRE_WARN_MM` with diameters that agree, the diameter is given and the position is said to be
    unsure (the frames agree on the hole and disagree on where the camera was)."""
    ok = [(f, T) for f, T in zip(frames, T_wc) if f.ok]
    why: dict[str, int] = {}
    for f in frames:
        if not f.ok:
            key = f.why.split(":")[0] if f.why.startswith("two readings") else (
                f.why.split(" mm apart")[0].rsplit(" ", 1)[0] if "apart" in f.why else f.why)
            why[key] = why.get(key, 0) + 1
    out: dict = {"frames": len(frames), "frames_used": len(ok), "frame_refusals": why, "warnings": []}
    if len(ok) < MIN_FRAMES:
        out["refused"] = f"only {len(ok)} frames show the rim (at least {MIN_FRAMES} needed)"
        return out
    d = np.array([f.d for f, _ in ok])
    sd = 1.4826 * float(np.median(np.abs(d - np.median(d))))
    cover = float(np.median([f.cover_deg for f, _ in ok]))
    stat = 1.2533 * sd / math.sqrt(len(d))
    pm = math.sqrt(stat ** 2 + B_SHARED_MM ** 2) * max(1.0, math.sqrt(COVER_FULL_DEG / max(cover, 1.0)))
    ts = [f.t for f, _ in ok if np.isfinite(f.t)]
    out.update(d=float(np.median(d)), d_sd=sd, pm=pm, pm_stat=stat, cover_deg=cover,
               t=float(np.median(ts)) if ts else None, t_frames=len(ts),
               points=int(sum(f.n_top + f.n_exit for f, _ in ok)), n_exit=int(sum(f.n_exit for f, _ in ok)),
               cams_mm=float(np.median([f.cams_mm for f, _ in ok])))
    Cw = np.array([T[:3, :3] @ f.centre_C + T[:3, 3] for f, T in ok])
    c = np.median(Cw, axis=0)
    out["centre"] = c.tolist()
    cs = 1.4826 * float(np.median(np.linalg.norm(Cw - c, axis=1)))
    out["centre_scatter"] = cs
    if cover < COVER_MIN_DEG:
        out["refused"] = f"the top rim is found on only {cover:.0f} degrees of the round"
    elif sd > MAX_D_SCATTER_MM:
        out["refused"] = f"the frames disagree on the diameter by {sd:.1f} mm"
    elif centre_rule and cs > CENTRE_REFUSE_MM:
        out["refused"] = f"the frames put the hole's centre {cs:.1f} mm apart: this is not one hole's rim"
    elif centre_rule and cs > CENTRE_WARN_MM and sd > D_SCATTER_PATH_MM:
        out["refused"] = (f"the frames put the hole's centre {cs:.1f} mm apart and its diameter {sd:.2f} mm apart: "
                          "the rim is not read the same in every frame")
    if cs > CENTRE_WARN_MM:
        out["warnings"].append(f"the frames put the hole's centre {cs:.1f} mm apart: the camera path may be off, "
                               "and the position with it" + (" (the diameter agrees between frames)" if centre_rule else ""))
        out["position_unsure"] = True
    return out


def interp_pose(Ta: np.ndarray, Tb: np.ndarray, f: float = 0.5) -> np.ndarray:
    """The pose ``f`` of the way from ``Ta`` to ``Tb``: rotation along the shortest arc, translation straight."""
    from scipy.spatial.transform import Rotation, Slerp

    s = Slerp([0.0, 1.0], Rotation.from_matrix(np.stack([Ta[:3, :3], Tb[:3, :3]])))
    T = np.eye(4)
    T[:3, :3] = s([f]).as_matrix()[0]
    T[:3, 3] = (1 - f) * Ta[:3, 3] + f * Tb[:3, 3]
    return T


# --------------------------------------------------------------------------- step 2's recordings
OFF_EVERY = 5  # in a recording "with dots-off frames", every 5th frame has the projector off ...
OFF_PHASE = 2  # ... the frames with index % 5 == 2; their neighbours have the dots on
READ_EVERY = 10  # the rim is read on every 10th frame: dots on, index % 10 == 0; dots off, index % 10 == 2


def wait_hold(hold: Path | None) -> None:
    """While ``hold`` exists, another heavy job has the PC: wait, checking once a minute (at most 3 hours)."""
    if hold is None:
        return
    for _ in range(180):
        if not hold.exists():
            return
        print(f"waiting: {hold} exists", flush=True)
        time.sleep(60)
    raise SystemExit(f"{hold} still there after 3 hours")


def cal_dict(stereo) -> dict:
    return {k: np.asarray(getattr(stereo, k)).tolist() for k in ("K1", "D1", "K2", "D2", "R", "T", "R1", "R2", "P1", "P2")}


def plate_scene(thickness: float, gap: float, floor: str = "dark", seed: int = 0):
    """A study plate like the scorecard's hole plate (160 x 90 mm, holes Ø25, Ø10, Ø6, turned 15°), ``thickness``
    thick, ``gap`` above the floor, without the block. ``floor``: ``"dark"`` (the scorecard's, albedo 0.3) or
    ``"bright"`` (albedo 0.85 with a coarse texture of contrast 0.3, brighter than the plate)."""
    from ohs import geometry as G
    from ohs.sim.scene import Cylinder, HeightField, NoiseAlbedo, Rect, Scene, _table
    from tools import scorecard as S

    T = G.make_se3(G.so3_exp(np.array([0.0, 0.0, S.HP_YAW])), [0.0, 0.0, float(gap)])
    alb = NoiseAlbedo(mean=0.6, contrast=0.03, scale_mm=2.0, seed=seed + 3)
    fl = (NoiseAlbedo(mean=0.3, contrast=0.08, scale_mm=5.0, seed=seed + 1) if floor == "dark"
          else NoiseAlbedo(mean=0.85, contrast=0.3, scale_mm=3.0, seed=seed + 1))

    def flat(hh):
        def f(x, y):
            return np.full_like(x, hh), np.zeros_like(x), np.zeros_like(x)
        return f

    t = float(thickness)
    px, py = S.HP_PX, S.HP_PY
    holes = list(S.HP_HOLES.values())
    prims = [_table(0.0, 800.0, fl, name="floor"),
             HeightField(T, flat(t), (-px, px, -py, py), (t, t), holes, alb, name="plate_top"),
             HeightField(T, flat(0.0), (-px, px, -py, py), (0.0, 0.0), holes, alb, name="plate_bottom")]
    for k, (hx, hy, r) in S.HP_HOLES.items():
        prims.append(Cylinder(T @ G.make_se3(np.eye(3), [hx, hy, t / 2]), r, t / 2, alb, name=f"wall_{k}", caps=False))
    Rx = np.column_stack([[0, 1.0, 0], [0, 0, 1.0], [1.0, 0, 0]])
    Ry = np.column_stack([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])
    prims += [Rect(T @ G.make_se3(Rx, [x, 0, 0]), (-py, py, 0, t), alb, name=f"side_x{x:+.0f}") for x in (px, -px)]
    prims += [Rect(T @ G.make_se3(Ry, [0, y, 0]), (-px, px, 0, t), alb, name=f"side_y{y:+.0f}") for y in (py, -py)]
    target = (T @ np.array([-10.0, 0.0, t, 1.0]))[:3]
    holes_w = []
    for k, (hx, hy, r) in S.HP_HOLES.items():
        clicks = [(T @ np.array([hx + (r + 1.5) * math.cos(math.radians(a) + 0.3),
                                 hy + (r + 1.5) * math.sin(math.radians(a) + 0.3), t, 1.0]))[:3].tolist()
                  for a in (0.0, 120.0, 240.0)]
        holes_w.append({"hole": k, "d_true": 2 * r, "centre_true": (T @ np.array([hx, hy, t, 1.0]))[:3].tolist(),
                        "clicks": clicks, "t_true": t})
    return Scene(prims, name=f"plate_t{t:g}_g{gap:g}_{floor}", target=target, radius=100.0), holes_w


STUDY_SCENES = {"t1-g10": (1.0, 10.0, "dark"), "t1-g120": (1.0, 120.0, "dark"), "t5-g10": (5.0, 10.0, "dark"),
                "t5-g120": (5.0, 120.0, "dark"), "t3-g120-bright": (3.0, 120.0, "bright")}


def _renderer(scene, rp_meta: dict, dots: bool, seed: int):
    import dataclasses

    from ohs.sim.nominal import nominal_calibration
    from ohs.sim.render import Renderer, RenderParams

    names = {f.name for f in dataclasses.fields(RenderParams)}
    rp = {k: v for k, v in rp_meta.items() if k in names}
    rp.update(dots_on=dots, seed=int(seed))
    return Renderer(nominal_calibration((1280, 800), realistic=True), scene=scene, params=RenderParams(**rp))


def _write_sbs(path: Path, L: np.ndarray, R: np.ndarray) -> None:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.hstack([L, R]))


def render_off(cache: Path, recs: list[str], out: Path, hold: Path | None) -> None:
    """For each scorecard recording, the frames that a recording "with dots-off frames" would have with the
    projector off and that are read (index % 10 == 2), rendered at their true poses with the dots off and
    everything else as the recording's own render (``out/<rec>-off/frames``)."""
    from ohs.io.recording import RecordingReader
    from tools import scorecard as S

    for name in recs:
        wait_hold(hold)
        rec = cache / "recs" / name
        dst = out / f"{name}-off" / "frames"
        meta = json.loads((rec / "meta.json").read_text(encoding="utf-8"))
        spec = S.SceneSpec(**json.loads((rec / "scorecard_spec.json").read_text(encoding="utf-8")))
        gt = RecordingReader(rec).ground_truth_poses()
        todo = [i for i in sorted(gt) if i % READ_EVERY == OFF_PHASE and not (dst / f"{i:06d}.png").is_file()]
        if not todo:
            continue
        t0 = time.perf_counter()
        rend = _renderer(S.scene_of(spec), meta["render_params"], False, meta["render_params"].get("seed", 0))
        for i in todo:
            L, R, _ = rend.render(gt[i])
            _write_sbs(dst / f"{i:06d}.png", L, R)
        if hasattr(rend, "close"):
            rend.close()
        print(f"{name}: {len(todo)} dots-off frames in {time.perf_counter() - t0:.0f} s", flush=True)


def make_study_scene(key: str, seed: int, out: Path, rp_meta: dict) -> Path:
    """A study recording of :data:`STUDY_SCENES` ``key``: the scorecard hole plates' hand-held orbit (200 frames
    at 10 fps), of which only what is read is rendered: frames index % 10 == 2 with the dots on and off, their
    neighbours with the dots on; the CPU matcher's depth of every dots-on frame (``depth/``)."""
    from ohs.sim.nominal import NOMINAL_TIME_OFFSET, nominal_calibration
    from ohs.sim.trajectory import HandheldTrajectory
    from ohs.vision.depth import DepthEstimator
    from ohs.vision.rectify import Rectifier
    from ohs.app.pipeline import default_depth_params

    t, gap, floor = STUDY_SCENES[key]
    d = out / f"{key}-s{seed}"
    if (d / "done.json").is_file():
        return d
    sc, holes = plate_scene(t, gap, floor, seed)
    calib = nominal_calibration((1280, 800), realistic=True)
    n, fps = 200, 10.0
    traj = HandheldTrajectory(target=sc.target, t0=100.0 + NOMINAL_TIME_OFFSET, duration=n / fps, seed=seed,
                              baseline=calib.stereo.baseline, distance=(230.0, 300.0), elevation_deg=(55.0, 75.0),
                              sweep_deg_s=400.0 / (n / fps))
    t_cam = 100.0 + np.arange(n) / fps + NOMINAL_TIME_OFFSET
    P = traj.poses(t_cam)
    reads = [i for i in range(n) if i % READ_EVERY == OFF_PHASE and 0 < i < n - 1]
    on_frames = sorted({j for i in reads for j in (i - 1, i, i + 1)})
    rect = Rectifier(calib.stereo)
    est = DepthEstimator(rect.K, rect.baseline, default_depth_params(1.0))
    t0 = time.perf_counter()
    rend = _renderer(sc, rp_meta, True, seed)
    for j in on_frames:
        if (d / "depth" / f"{j:06d}.npz").is_file():
            continue
        L, R, _ = rend.render(P[j])
        if j in reads:
            _write_sbs(d / "on" / f"{j:06d}.png", L, R)
        Lr, Rr = rect.rectify(L, R)
        z, s = est.compute_with_sigma(Lr, Rr)
        (d / "depth").mkdir(parents=True, exist_ok=True)
        np.savez_compressed(d / "depth" / f"{j:06d}.npz", depth=np.asarray(z, np.float32), sigma=np.asarray(s, np.float32))
    if hasattr(rend, "close"):
        rend.close()
    rend = _renderer(sc, rp_meta, False, seed)
    for i in reads:
        if not (d / "off" / f"{i:06d}.png").is_file():
            L, R, _ = rend.render(P[i])
            _write_sbs(d / "off" / f"{i:06d}.png", L, R)
    if hasattr(rend, "close"):
        rend.close()
    write = {"calibration": cal_dict(calib.stereo), "poses": {int(i): P[i].tolist() for i in on_frames},
             "holes": holes, "scene": key, "seed": seed}
    (d / "study.json").write_text(json.dumps(write), encoding="utf-8")
    (d / "done.json").write_text(json.dumps({"s": round(time.perf_counter() - t0, 1)}), encoding="utf-8")
    return d


class Source:
    """What a step-2 reading needs from one recording and pose source: the cameras, the frames to read with their
    images and poses, and depth points round a frame-C point (from that frame's own depth with the dots on; from
    its two neighbours' with the dots off, moved into the frame with the same pose source)."""

    def __init__(self, cams, K_rect, poses: dict, images, depth, reads: list[int], off: bool, holes: list[dict],
                 info: dict) -> None:
        self.cams, self.K_rect, self.poses, self._images, self._depth = cams, K_rect, poses, images, depth
        self.reads, self.off, self.holes, self.info = reads, off, holes, info

    def pose(self, i: int) -> np.ndarray | None:
        if not self.off or self.info.get("poses") == "true":
            return self.poses.get(i)
        if i - 1 in self.poses and i + 1 in self.poses:  # a frame without dots is not tracked: between its neighbours
            return interp_pose(self.poses[i - 1], self.poses[i + 1])
        return None

    def points(self, i: int, near_C: np.ndarray, T_i: np.ndarray, all_valid: bool = False) -> np.ndarray:
        """Depth points round ``near_C`` (frame C of frame ``i``); with ``all_valid`` (round 5's near-or-far gate)
        every valid depth pixel, without the plane's sigma filter."""
        if not self.off:
            z, s = self._depth(i)
            return depth_points(z, None if all_valid else s, self.K_rect, near_C) if z is not None else np.zeros((0, 3))
        out = []
        for j in (i - 1, i + 1):
            Tj = self.poses.get(j)
            z, s = self._depth(j)
            if Tj is None or z is None:
                continue
            M = np.linalg.solve(T_i, Tj)  # frame C_j -> frame C_i
            near_j = np.linalg.solve(Tj, T_i @ np.r_[near_C, 1.0])[:3]
            X = depth_points(z, None if all_valid else s, self.K_rect, near_j)
            out.append(X @ M[:3, :3].T + M[:3, 3])
        return np.vstack(out) if out else np.zeros((0, 3))

    def images(self, i: int):
        return self._images(i)

    def release(self) -> None:
        """Drop the frames and depth kept in memory (round 5: round 4 kept every source's at once, 12.8 GB)."""
        fr = getattr(self, "frames", None)
        if fr is not None:
            fr._img.clear()
            fr._dep.clear()


def _png_pair(path: Path):
    import cv2

    sbs = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if sbs is None:
        return None
    w = sbs.shape[1] // 2
    return np.ascontiguousarray(sbs[:, :w], np.float32), np.ascontiguousarray(sbs[:, w:], np.float32)


def scorecard_source(cache: Path, name: str, src: str, settings: str, off: bool, off_dir: Path) -> Source:
    rec = cache / "recs" / name
    cal = json.loads((rec / "calibration.json").read_text(encoding="utf-8"))["stereo"]
    fr = Frames(rec, cache / "depth" / "gpu" / name)
    poses, info = poses_of(cache, rec, src, settings)
    info["poses"] = src
    gt = poses if src == "true" else poses_of(cache, rec, "true", settings)[0]
    reads = [i for i in sorted(gt) if i % READ_EVERY == (OFF_PHASE if off else 0)]
    imgs = (lambda i: _png_pair(off_dir / f"{name}-off" / "frames" / f"{i:06d}.png")) if off else fr.images

    def depth(i):
        try:
            return fr.depth(i)
        except FileNotFoundError:
            return None, None

    S_ = Source(stereo_cameras(cal), np.asarray(cal["P1"], float)[:3, :3], poses, imgs, depth, reads, off,
                holes_of(rec), info)
    S_.frames = fr
    return S_


def study_source(d: Path, off: bool) -> Source:
    st = json.loads((d / "study.json").read_text(encoding="utf-8"))
    poses = {int(k): np.asarray(v, float) for k, v in st["poses"].items()}
    reads = sorted(i for i in poses if i % READ_EVERY == OFF_PHASE)

    def depth(i):
        f = d / "depth" / f"{i:06d}.npz"
        if not f.is_file():
            return None, None
        z = np.load(f)
        return z["depth"], z["sigma"]

    holes = [{**h, "centre_true": np.asarray(h["centre_true"]), "clicks": [np.asarray(c) for c in h["clicks"]]}
             for h in st["holes"]]
    cal = st["calibration"]
    return Source(stereo_cameras(cal), np.asarray(cal["P1"], float)[:3, :3], poses,
                  lambda i: _png_pair(d / ("off" if off else "on") / f"{i:06d}.png"), depth, reads, off, holes,
                  {"poses": "true"})


def read_click_model(S_: Source, click_w: np.ndarray) -> dict:
    frames, Ts = [], []
    for i in S_.reads:
        T = S_.pose(i)
        if T is None:
            continue
        cC = T[:3, :3].T @ (click_w - T[:3, 3])
        uv, front = S_.cams[0].project(cC[None])
        if not front[0] or not (60 <= uv[0, 0] <= 1280 - 60 and 60 <= uv[0, 1] <= 800 - 60):
            continue
        imgs = S_.images(i)
        if imgs is None:
            continue
        frames.append(read_frame_model(imgs, S_.cams, S_.points(i, cC, T), cC))
        Ts.append(T)
    return combine_model(frames, Ts)


def run2(sources: list[tuple[str, str, str, Source]], out: Path, hold: Path | None) -> None:
    """Read every hole of each ``(recording, poses, dots, Source)`` at its three clicks; one JSON row a click."""
    for name, src, dots, S_ in sources:
        wait_hold(hold)
        for h in S_.holes:
            for k, click in enumerate(h["clicks"]):
                t0 = time.perf_counter()
                r = read_click_model(S_, np.asarray(click, float))
                if S_.info.get("path_check") == "warn":
                    r["warnings"].append("the scan's camera path check warns")
                row = {"rec": name, "poses": src, "dots": dots, "hole": h["hole"], "click": k, "d_true": h["d_true"],
                       "t_true": h.get("t_true"), "path_check": S_.info.get("path_check"), "today": S_.info.get("today"),
                       "s": round(time.perf_counter() - t0, 1), **r}
                if "d" in r:
                    row["d_err"] = r["d"] - h["d_true"]
                    row["centre_err"] = float(np.linalg.norm(np.asarray(r["centre"]) - np.asarray(h["centre_true"])))
                with out.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
                print(f"{name} {src} dots {dots} {h['hole']} click {k}: "
                      + (f"d_err {row['d_err']:+.3f} ±{r['pm']:.3f} t {r['t']} cover {r['cover_deg']:.0f} "
                         f"frames {r['frames_used']}/{r['frames']}" if "d_err" in row else "no d")
                      + (f" REFUSED: {r['refused']}" if "refused" in r else "") + f" [{row['s']} s]", flush=True)


# =========================================================================== step 3: the run
def umeyama_rigid(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """The rigid motion (4x4) that best takes the points ``A`` onto ``B`` (least squares)."""
    ca, cb = A.mean(axis=0), B.mean(axis=0)
    U, _, Vt = np.linalg.svd((B - cb).T @ (A - ca))
    D = np.diag([1.0, 1.0, np.sign(np.linalg.det(U @ Vt))])
    R = U @ D @ Vt
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, cb - R @ ca
    return T


def tracked_in_world(traj_csv: Path, gt: dict[int, np.ndarray], with_motion: bool = False):
    """A trajectory from ``process_recording`` (in its first camera's frame) put into the world by the rigid motion
    that best fits its camera positions to the true ones (the path's own error stays in it); with ``with_motion``
    also that motion (the scan's mesh moves with it)."""
    from ohs.track.posegraph import read_trajectory

    tr = {int(i): np.asarray(T, float) for i, _, ok, T in read_trajectory(traj_csv) if ok}
    common = [i for i in tr if i in gt]
    M = umeyama_rigid(np.array([tr[i][:3, 3] for i in common]), np.array([gt[i][:3, 3] for i in common]))
    out = {i: M @ T for i, T in tr.items()}
    return (out, M) if with_motion else out


def path_errors(poses: dict[int, np.ndarray], gt: dict[int, np.ndarray]) -> dict:
    e = np.array([np.linalg.norm(poses[i][:3, 3] - gt[i][:3, 3]) for i in poses if i in gt])
    return {"median": float(np.median(e)), "max": float(e.max()), "n": int(len(e))}


def study_tracked_source(work: Path, key: str, off: bool) -> Source:
    """A study scene read with the poses of its own processor-path tracking run (:func:`track_study`)."""
    d = work / "study" / key
    S_ = study_source(d, off)
    run = work / "tracked" / key
    gt = {int(k): np.asarray(v, float) for k, v in json.loads((run / "gt.json").read_text(encoding="utf-8")).items()}
    S_.poses = tracked_in_world(run / "proc" / "trajectory.csv", gt)
    rep = json.loads((run / "proc" / "report.json").read_text(encoding="utf-8"))
    S_.info = {"poses": "tracked", "path_check": (rep.get("path_check") or {}).get("state"),
               "path": path_errors(S_.poses, gt)}
    return S_


def custom_tracked_source(cache: Path, name: str, run: Path, off: bool, work: Path) -> Source:
    """A scorecard recording read with the poses of one of this study's processor-path tracking runs."""
    S_ = scorecard_source(cache, name, "true", "app-default", off, work)
    gt = S_.poses
    S_.poses = tracked_in_world(run / "proc" / "trajectory.csv", gt)
    rep = json.loads((run / "proc" / "report.json").read_text(encoding="utf-8"))
    S_.info = {"poses": "tracked", "path_check": (rep.get("path_check") or {}).get("state"),
               "path": path_errors(S_.poses, gt)}
    return S_


def _process_cpu(rec: Path, out: Path) -> dict:
    """Make mesh's frame pass on the processor (the app's Standard, markerless), as the scorecard's ``frame`` chain."""
    import dataclasses

    from ohs.app.config import Config
    from ohs.app.process import process_recording
    from ohs.server.studio import process_params

    pp = dataclasses.replace(process_params(Config(), "markerless", "standard", 1280), export=("ply",))
    return process_recording(rec, out / "proc", params=pp)


def track_study(work: Path, keys: list[str], hold: Path | None) -> None:
    """For each study scene ``key-sN``: the whole 200-frame recording, as the scorecard renders its hole plates
    (dots on, same orbit), then tracked on the processor path. Kept in ``work/tracked/key-sN``."""
    from ohs.sim.datasets import make_scan_recording
    from ohs.sim.nominal import NOMINAL_TIME_OFFSET, nominal_calibration
    from ohs.sim.trajectory import HandheldTrajectory

    for key in keys:
        wait_hold(hold)
        out = work / "tracked" / key
        if (out / "proc" / "report.json").is_file():
            continue
        name, seed = key.rsplit("-s", 1)
        seed = int(seed)
        t, gap, floor = STUDY_SCENES[name]
        sc, _ = plate_scene(t, gap, floor, seed)
        calib = nominal_calibration((1280, 800), realistic=True)
        traj = HandheldTrajectory(target=sc.target, t0=100.0 + NOMINAL_TIME_OFFSET, duration=20.0, seed=seed,
                                  baseline=calib.stereo.baseline, distance=(230.0, 300.0), elevation_deg=(55.0, 75.0),
                                  sweep_deg_s=400.0 / 20.0)
        rec = out / "rec"
        t0 = time.perf_counter()
        if not (rec / "frames.csv").is_file() or len((rec / "frames.csv").read_text().splitlines()) < 201:
            make_scan_recording(rec, scene=sc, n_frames=200, fps=10.0, trajectory=traj, overwrite=True,
                                image_size=(1280, 800), realistic=True, depth_every=0, seed=seed)
        t1 = time.perf_counter()
        from ohs.io.recording import RecordingReader

        gt = RecordingReader(rec).ground_truth_poses()
        (out / "gt.json").write_text(json.dumps({int(i): T.tolist() for i, T in gt.items()}), encoding="utf-8")
        _process_cpu(rec, out)
        print(f"{key}: rendered in {t1 - t0:.0f} s, tracked in {time.perf_counter() - t1:.0f} s", flush=True)


def track_drop(cache: Path, recs: list[str], work: Path, hold: Path | None) -> None:
    """For each scorecard recording: tracked on the processor path with every frame, and again with the frames a
    recording "with dots-off frames" would have dark (index % 5 == 2) left out, as frames without depth would
    be on the real scanner (``work/drop/<rec>/all`` and ``.../without``)."""
    import shutil

    for name in recs:
        src = cache / "recs" / name
        for kind in ("all", "without"):
            wait_hold(hold)
            out = work / "drop" / name / kind
            if (out / "proc" / "report.json").is_file():
                continue
            rec = src
            if kind == "without":
                rec = out / "rec"
                if not (rec / "frames.csv").is_file():
                    (rec / "frames").mkdir(parents=True, exist_ok=True)
                    (rec / "ground_truth").mkdir(exist_ok=True)
                    for f in ("meta.json", "calibration.json", "imu.csv", "events.csv"):
                        if (src / f).is_file():
                            shutil.copy2(src / f, rec / f)
                    shutil.copy2(src / "ground_truth" / "poses.csv", rec / "ground_truth" / "poses.csv")
                    lines = (src / "frames.csv").read_text(encoding="utf-8").splitlines()
                    keep = [lines[0]]
                    for ln in lines[1:]:
                        i = int(ln.split(",")[0])
                        if i % OFF_EVERY != OFF_PHASE:
                            keep.append(ln)
                            shutil.copy2(src / "frames" / f"{i:06d}.png", rec / "frames" / f"{i:06d}.png")
                    (rec / "frames.csv").write_text("\n".join(keep) + "\n", encoding="utf-8")
            t0 = time.perf_counter()
            _process_cpu(rec, out)
            print(f"{name} {kind}: tracked in {time.perf_counter() - t0:.0f} s", flush=True)


def read_click3(S_: Source, click_w: np.ndarray, t_true: float | None, cfg: Step3) -> list[dict]:
    """One click read with step 3, ``t`` free and (when ``t_true`` is given) held at the true thickness, from the
    same frames: ``[free, given]``."""
    t_list = [None] + ([t_true] if t_true is not None else [])
    per = [[] for _ in t_list]
    Ts = []
    for i in S_.reads:
        T = S_.pose(i)
        if T is None:
            continue
        cC = T[:3, :3].T @ (click_w - T[:3, 3])
        uv, front = S_.cams[0].project(cC[None])
        if not front[0] or not (60 <= uv[0, 0] <= 1280 - 60 and 60 <= uv[0, 1] <= 800 - 60):
            continue
        imgs = S_.images(i)
        if imgs is None:
            continue
        for k, m in enumerate(read_frame_models(imgs, S_.cams, S_.points(i, cC, T), cC, t_list, cfg)):
            per[k].append(m)
        Ts.append(T)
    return [combine_model(fr, Ts, cfg.centre_rule) for fr in per]


def run3(sources: list[tuple[str, str, str, Source]], out: Path, hold: Path | None, cfg: Step3, tag: str) -> None:
    """Read every hole of each ``(recording, poses, dots, Source)`` at its three clicks with step 3; one JSON row a
    click and thickness mode (``t`` free, ``t`` given)."""
    for name, src, dots, S_ in sources:
        wait_hold(hold)
        for h in S_.holes:
            t_true = h.get("t_true")
            if t_true is None:
                t_true = HP_T_DEFAULT if name.startswith("holes") else 0.0
            for k, click in enumerate(h["clicks"]):
                t0 = time.perf_counter()
                res = read_click3(S_, np.asarray(click, float), t_true, cfg)
                for mode, r in zip(("free", "given"), res):
                    if S_.info.get("path_check") == "warn":
                        r["warnings"].append("the scan's camera path check warns")
                    row = {"tag": tag, "rec": name, "poses": src, "dots": dots, "t_mode": mode, "hole": h["hole"],
                           "click": k, "d_true": h["d_true"], "t_true": t_true, "path_check": S_.info.get("path_check"),
                           "path": S_.info.get("path"), "today": S_.info.get("today"),
                           "s": round(time.perf_counter() - t0, 1), **r}
                    if "d" in r:
                        row["d_err"] = r["d"] - h["d_true"]
                        row["centre_err"] = float(np.linalg.norm(np.asarray(r["centre"]) - np.asarray(h["centre_true"])))
                    with out.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(row) + "\n")
                    print(f"[{tag}] {name} {src} dots {dots} t {mode} {h['hole']} click {k}: "
                          + (f"d_err {row['d_err']:+.3f} ±{r['pm']:.3f} cover {r['cover_deg']:.0f} "
                             f"frames {r['frames_used']}/{r['frames']}" if "d_err" in row else "no d")
                          + (f" REFUSED: {r['refused']}" if "refused" in r else "") + f" [{row['s']} s]", flush=True)


HP_T_DEFAULT = 3.0  # the scorecard's hole plates are 3 mm thick


# =========================================================================== round 4: "dots on, gated"
MESH_DISK_MM = 30.0  # the mesh's hole is looked for among the vertices within this of the click ...
MESH_CELL_MM = 1.0  # ... on cells this size in the face's plane ...
MESH_FACE_MM = 0.3  # ... a cell is face when a vertex lies this close to the face's surface ...
MESH_REACH_MM = 10.0  # ... and the hole must come this close to the click (the Hole tool's reach)
RIM_CLICK_MM = 5.0  # the read rim must pass this close to the click
CENTRE_MATCH_MM = 2.0  # the read centre must lie within max(this, CENTRE_MATCH_R x R) of the mesh hole's centre
CENTRE_MATCH_R = 0.3
BG_BELOW_MM = (6.0, 50.0)  # a near background: depth points this far below the face inside the hole ...
BG_RADIUS = 0.8  # ... within this share of the read radius of its axis ...
BG_NEAR_SHARE = 0.15  # ... making up at least this share of the points inside (median over frames)
BG_MIN_POINTS = 30  # fewer depth points inside the hole than this (median over frames): unknown


def background_stats(X: np.ndarray, o: np.ndarray, n: np.ndarray, centre: np.ndarray, R: float, depth: bool = False):
    """Depth points ``X`` (frame C) inside a read hole (within :data:`BG_RADIUS` R of the axis through ``centre``
    along the face's normal ``n``, which points at the camera): ``(count, share 6 to 50 mm below the face)``, and
    with ``depth`` the median depth below the face of that share (NaN when none). ``o`` is a point of the face."""
    if not len(X):
        return (0, float("nan"), float("nan")) if depth else (0, float("nan"))
    d = X - centre
    radial = np.linalg.norm(d - np.outer(d @ n, n), axis=1)
    inside = radial < BG_RADIUS * R
    below = (o - X[inside]) @ n
    near = (below >= BG_BELOW_MM[0]) & (below <= BG_BELOW_MM[1])
    cnt = int(inside.sum())
    share = float(near.mean()) if cnt else float("nan")
    if depth:
        return cnt, share, float(np.median(below[near])) if near.any() else float("nan")
    return cnt, share


def background_refusal(frames: list) -> str | None:
    """The near-or-far gate of the dots-on reading over its answered frames (NOTES.md, round 4): ``None`` when the
    background is far, else the refusal's words."""
    ok = [f for f in frames if f.ok]
    if not ok:
        return None
    if float(np.median([f.bg_n for f in ok])) < BG_MIN_POINTS:
        return "too little depth through this hole to tell what lies behind it"
    share = float(np.nanmedian([f.bg_near for f in ok])) if any(np.isfinite(f.bg_near) for f in ok) else 0.0
    if share >= BG_NEAR_SHARE:
        dd = [f.bg_depth for f in ok if np.isfinite(f.bg_depth)]
        return (f"a surface lies about {np.median(dd) if dd else BG_BELOW_MM[0]:.0f} mm below the face inside this hole: "
                "with the projector's dots on, its dots and their shadows can look like the rim")
    return None


def _quadric_surface(P: np.ndarray):
    """A trimmed plane through ``P`` and a trimmed quadric height over it: ``(c, u, v, n, height(xy))``."""
    fit = _trimmed_plane(P)
    if fit is None:
        return None
    c, n = fit
    u = np.cross(n, [0.0, 1.0, 0.0] if abs(n[1]) < 0.9 else [1.0, 0.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    d = P - c
    x, y, z = d @ u, d @ v, d @ n

    def basis(x, y):
        return np.column_stack([np.ones_like(x), x, y, x * x, x * y, y * y])

    keep = np.abs(z) < 2.0
    coef = np.zeros(6)
    for _ in range(4):
        if keep.sum() < 30:
            return None
        coef, *_ = np.linalg.lstsq(basis(x[keep], y[keep]), z[keep], rcond=None)
        r = z - basis(x, y) @ coef
        mad = 1.4826 * float(np.median(np.abs(r[keep] - np.median(r[keep]))))
        keep = np.abs(r) < max(3.0 * mad, 0.1)
    return c, u, v, n, (lambda xx, yy: basis(xx, yy) @ coef)


def mesh_hole_at(V: np.ndarray, click: np.ndarray, tree=None, F: np.ndarray | None = None) -> dict | str:
    """The scan's own hole at a click, from the mesh's vertices ``V`` alone (NOTES.md, round 4): the face round the
    click as a trimmed quadric surface, cells of :data:`MESH_CELL_MM` that hold no face (empty, or every vertex
    :data:`MESH_FACE_MM` or more below the face), and of their closed patches the one nearest the click within
    :data:`MESH_REACH_MM`. ``{"centre", "n", "radius", "reach"}`` (centre and normal in ``V``'s frame; the radius
    from the patch's area), or the reason there is none.

    With the triangles ``F`` (round 5) the cells are tested on the triangles drawn into them (10 points a triangle:
    corners, thirds of the edges, centroid) instead of on the vertices alone: a fused mesh's vertices lie about a
    voxel apart, so on 1 mm cells the face had empty cells, and specks of them joined a hole to the disk's edge."""
    from scipy import ndimage
    from scipy.spatial import cKDTree

    tree = tree if tree is not None else cKDTree(V)
    idx = tree.query_ball_point(click, MESH_DISK_MM)
    P = V[np.asarray(idx, int)]
    if len(P) < 100:
        return "the scan shows no hole within 10 mm of the click (too little mesh round it)"
    surf = _quadric_surface(P)
    if surf is None:
        return "the scan shows no hole within 10 mm of the click (no face round it)"
    c, u, v, n, height = surf
    if F is not None:
        near = np.zeros(len(V), bool)
        near[np.asarray(idx, int)] = True
        T3 = V[F[near[F].any(axis=1)]]  # triangles with a corner within the disk
        W = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [2, 1, 0], [1, 2, 0], [0, 2, 1], [0, 1, 2], [1, 0, 2],
                      [2, 0, 1], [1, 1, 1]], float)
        W /= W.sum(axis=1, keepdims=True)
        P = np.einsum("kj,tjd->tkd", W, T3).reshape(-1, 3)
    d = P - c
    x, y = d @ u, d @ v
    hgt = d @ n - height(x, y)
    k = (click - c)
    cx, cy = k @ u, k @ v
    R = MESH_DISK_MM
    nc = int(math.ceil(2 * R / MESH_CELL_MM))
    ix = np.floor((x - (cx - R)) / MESH_CELL_MM).astype(int)
    iy = np.floor((y - (cy - R)) / MESH_CELL_MM).astype(int)
    ok = (ix >= 0) & (ix < nc) & (iy >= 0) & (iy < nc)
    face = np.zeros((nc, nc), bool)
    above = np.zeros((nc, nc), bool)
    on = ok & (np.abs(hgt) <= MESH_FACE_MM)
    face[iy[on], ix[on]] = True
    up = ok & (hgt > MESH_FACE_MM)
    above[iy[up], ix[up]] = True
    face = ndimage.binary_dilation(face, iterations=1)
    gx = cx - R + (np.arange(nc) + 0.5) * MESH_CELL_MM
    GX, GY = np.meshgrid(gx, cy - R + (np.arange(nc) + 0.5) * MESH_CELL_MM)
    rr = np.hypot(GX - cx, GY - cy)
    disk = rr <= R - MESH_CELL_MM
    hole = disk & ~face & ~above
    lab, nl = ndimage.label(hole)
    edge = disk & (rr > R - 2.5 * MESH_CELL_MM)
    on_edge = set(np.unique(lab[edge & hole]).tolist())
    best = None
    for j in range(1, nl + 1):
        m = lab == j
        area = m.sum() * MESH_CELL_MM ** 2
        if j in on_edge or area < math.pi * 1.0 ** 2:
            continue
        reach = float(rr[m].min())
        if reach <= MESH_REACH_MM and (best is None or reach < best[0]):
            best = (reach, m, area)
    if best is None:
        return "the scan shows no hole within 10 mm of the click"
    m = best[1]
    hx, hy = float(GX[m].mean()), float(GY[m].mean())
    centre = c + hx * u + hy * v + float(height(np.array([hx]), np.array([hy]))[0]) * n
    return {"centre": centre, "n": n, "radius": math.sqrt(best[2] / math.pi), "reach": best[0]}


def mesh_check(centre: np.ndarray, R: float, click: np.ndarray, hole: dict | str) -> str | None:
    """The read circle (``centre``, radius ``R``, in the mesh's frame) against the scan's own hole at the click
    (:func:`mesh_hole_at`): ``None`` when it is that hole, else the refusal's words (NOTES.md, round 4)."""
    if isinstance(hole, str):
        return hole
    n = hole["n"]

    def flat(a):
        return a - (a @ n) * n

    rim = abs(float(np.linalg.norm(flat(click - centre))) - R)
    if rim > RIM_CLICK_MM:
        return f"the rim read in the images is {rim:.0f} mm from the click: it is another hole's"
    off = float(np.linalg.norm(flat(centre - hole["centre"])))
    if off > max(CENTRE_MATCH_MM, CENTRE_MATCH_R * R):
        return (f"the hole read in the images is not the one the scan shows at the click "
                f"(their centres are {off:.1f} mm apart)")
    return None


def load_mesh(path: Path, T: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """A mesh's vertices (moved by ``T``) and triangles."""
    import open3d as o3d

    m = o3d.io.read_triangle_mesh(str(path))
    V = np.asarray(m.vertices, float)
    return (V if T is None else V @ T[:3, :3].T + T[:3, 3]), np.asarray(m.triangles, np.int64)


def load_vertices(path: Path, T: np.ndarray | None = None) -> np.ndarray:
    import open3d as o3d

    V = np.asarray(o3d.io.read_triangle_mesh(str(path)).vertices, float)
    return V if T is None else V @ T[:3, :3].T + T[:3, 3]


def mesh_click(S_: Source, click_true: np.ndarray, gt: dict, tree, V) -> np.ndarray:
    """Where the scan's own poses put the true click (the median over the frames that see it of
    T_scan_i · T_true_i^-1 · click), snapped to the nearest mesh vertex: where a user would click on this mesh."""
    qs = []
    for i in S_.reads:
        T, Tt = S_.pose(i), gt.get(i)
        if T is None or Tt is None:
            continue
        q = Tt[:3, :3].T @ (click_true - Tt[:3, 3])
        uv, front = S_.cams[0].project(q[None])
        if front[0] and 60 <= uv[0, 0] <= 1220 and 60 <= uv[0, 1] <= 740:
            qs.append(T[:3, :3] @ q + T[:3, 3])
    p = np.median(np.array(qs), axis=0) if qs else click_true
    return V[tree.query(p)[1]]


def read_click4(S_: Source, click: np.ndarray, cfg: Step3, gt: dict, holes: list[dict],
                bg_all: bool = False) -> tuple[dict, list]:
    """One click read for round 4 (t free): the combined reading (step 3's rules) and, for judging only, the true
    hole each answered frame's circle names when placed with the TRUE pose."""
    frames, Ts, names = [], [], []
    for i in S_.reads:
        T = S_.pose(i)
        if T is None:
            continue
        cC = T[:3, :3].T @ (click - T[:3, 3])
        uv, front = S_.cams[0].project(cC[None])
        if not front[0] or not (60 <= uv[0, 0] <= 1220 and 60 <= uv[0, 1] <= 740):
            continue
        imgs = S_.images(i)
        if imgs is None:
            continue
        X_bg = S_.points(i, cC, T, all_valid=True) if bg_all else None
        m = read_frame_models(imgs, S_.cams, S_.points(i, cC, T), cC, [None], cfg, X_bg)[0]
        frames.append(m)
        Ts.append(T)
        if m.ok and i in gt:
            cw = gt[i][:3, :3] @ m.centre_C + gt[i][:3, 3]
            h = min(holes, key=lambda h: np.linalg.norm(cw - np.asarray(h["centre_true"])))
            names.append(h["hole"] if np.linalg.norm(cw - np.asarray(h["centre_true"])) < h["d_true"] / 2 else "none")
    return combine_model(frames, Ts, cfg.centre_rule), frames, names


def run4(sources: list[tuple], out: Path, hold: Path | None, tag: str, round5: bool = False) -> None:
    """Round 4: each ``(name, poses, dots, Source, gt, mesh path, mesh motion, click kind)`` read at every hole's three
    clicks with the gated reading (dots on: near-or-far gate and mesh check; dots off: mesh check). One row a click."""
    from scipy.spatial import cKDTree

    cfg = Step3(either=True, stereo=False, two_hyp=True, centre_rule=True)
    for name, src, dots, S_, gt, mesh_path, M, click_kind in sources:
        wait_hold(hold)
        V, F = load_mesh(mesh_path, M)
        F = F if round5 else None  # round 5: the mesh's triangles drawn into the cells
        tree = cKDTree(V)
        for h in S_.holes:
            for k, click_t in enumerate(h["clicks"]):
                t0 = time.perf_counter()
                click_t = np.asarray(click_t, float)
                click = mesh_click(S_, click_t, gt, tree, V) if click_kind == "mesh" else click_t
                r, frames, names = read_click4(S_, click, cfg, gt, S_.holes, bg_all=round5)
                checks = {}
                if "d" in r:
                    if dots == "on":
                        checks["gate"] = background_refusal(frames)
                    checks["mesh"] = mesh_check(np.asarray(r["centre"]), r["d"] / 2.0, click, mesh_hole_at(V, click, tree, F))
                rule = r.get("refused")
                final = rule or checks.get("gate") or checks.get("mesh")
                named = max(set(names), key=names.count) if names else None
                row = {"tag": tag, "rec": name, "poses": src, "dots": dots, "click_kind": click_kind, "hole": h["hole"],
                       "click": k, "d_true": h["d_true"], "path_check": S_.info.get("path_check"),
                       "today": S_.info.get("today"), "rule_refusal": rule, "gate_refusal": checks.get("gate"),
                       "mesh_refusal": checks.get("mesh"), "named": named,
                       "named_share": (names.count(named) / len(names)) if names else None,
                       "click_moved": float(np.linalg.norm(click - click_t)), "s": round(time.perf_counter() - t0, 1),
                       **{kk: vv for kk, vv in r.items() if kk != "refused"}}
                if final:
                    row["refused"] = final
                if "d" in r:
                    row["d_err"] = r["d"] - h["d_true"]
                    row["centre_err"] = float(np.linalg.norm(np.asarray(r["centre"]) - np.asarray(h["centre_true"])))
                    row["bg_near"] = float(np.nanmedian([f.bg_near for f in frames if f.ok])) if any(
                        f.ok and np.isfinite(f.bg_near) for f in frames) else None
                    row["bg_n"] = float(np.median([f.bg_n for f in frames if f.ok]))
                with out.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
                print(f"[{tag}] {name} {src} dots {dots} {h['hole']} click {k}: "
                      + (f"d_err {row['d_err']:+.3f} named {named} " if "d_err" in row else "no d ")
                      + (f"REFUSED: {final}" if final else "answered") + f" [{row['s']} s]", flush=True)
        S_.release()
        del V, F, tree


def true_mesh(work: Path, keys: list[str], hold: Path | None) -> None:
    """The study scenes' meshes at the true poses, fused on the processor from the whole recordings
    (``work/truemesh/<key>/proc/mesh.ply``)."""
    import dataclasses

    from ohs.app.config import Config
    from ohs.app.process import process_recording
    from ohs.io.recording import RecordingReader
    from ohs.server.studio import process_params

    for key in keys:
        wait_hold(hold)
        out = work / "truemesh" / key
        if (out / "proc" / "mesh.ply").is_file():
            continue
        rec = work / "tracked" / key / "rec"
        gt = RecordingReader(rec).ground_truth_poses()
        pp = dataclasses.replace(process_params(Config(), "markerless", "standard", 1280), export=("ply",))
        t0 = time.perf_counter()
        process_recording(rec, out / "proc", params=pp, poses=gt)
        print(f"{key}: fused at true poses in {time.perf_counter() - t0:.0f} s", flush=True)


def gpu_track(work: Path, keys: list[str]) -> None:
    """The graphics-card phase's job for one or more study scenes: the whole recording tracked and meshed by what the
    app's Make mesh runs (the scorecard's ``app_chain``, default settings) on the graphics card
    (``work/gpu/<key>/proc``). Run each key through safe_run.py."""
    for key in keys:
        out = work / "gpu" / key
        if (out / "proc" / "report.json").is_file():
            continue
        rec = work / "tracked" / key / "rec"
        from ohs.io.recording import RecordingReader

        gt = RecordingReader(rec).ground_truth_poses()
        (out).mkdir(parents=True, exist_ok=True)
        (out / "gt.json").write_text(json.dumps({int(i): T.tolist() for i, T in gt.items()}), encoding="utf-8")
        from tools import scorecard as S

        seed = int(key.rsplit("-s", 1)[1])
        spec = S.SceneSpec(f"holes-high-s{seed}", "holes-high", "holes", seed, 200, {"maker": "holes", "gap": "high"})
        t0 = time.perf_counter()
        S.app_chain(rec, out / "proc", spec, {}, None)  # what the app's Make mesh runs: track, Straighten, Refine
        print(f"{key}: tracked and meshed in {time.perf_counter() - t0:.0f} s", flush=True)


def _study_sources4(work: Path, keys: list[str], kind: str, dots: str) -> list[tuple]:
    """Round 4's study sources: ``kind`` "true" (true poses, the true-pose mesh), "cpu" (processor tracking) or "gpu"."""
    out = []
    for key in keys:
        off = dots == "off"
        S_ = study_source(work / "study" / key, off)
        gt = dict(S_.poses)
        if kind == "true":
            out.append((key, "true", dots, S_, gt, work / "truemesh" / key / "proc" / "mesh.ply", None, "mesh"))
            continue
        run = work / ("tracked" if kind == "cpu" else "gpu") / key
        gt_all = {int(k): np.asarray(v, float) for k, v in json.loads((run.parent.parent / "tracked" / key / "gt.json")
                                                                       .read_text(encoding="utf-8")).items()}
        poses, M = tracked_in_world(run / "proc" / "trajectory.csv", gt_all, with_motion=True)
        S_.poses = poses
        rep = json.loads((run / "proc" / "report.json").read_text(encoding="utf-8"))
        S_.info = {"poses": "tracked", "path_check": (rep.get("path_check") or {}).get("state"),
                   "path": path_errors(poses, gt_all)}
        out.append((key, f"tracked-{kind}", dots, S_, gt_all, run / "proc" / "mesh.ply", M, "mesh"))
    return out


def _scorecard_sources4(cache: Path, work: Path, recs: list[str], poses: list[str], dots: str, settings: str) -> list:
    out = []
    for name in recs:
        gt = scorecard_source(cache, name, "true", settings, dots == "off", work).poses
        for src in poses:
            S_ = scorecard_source(cache, name, src, settings, dots == "off", work)
            run = cache / "runs" / "gpu" / settings / name / src
            M = None
            if src == "tracked":
                M = np.asarray(json.loads((run / "result.json").read_text(encoding="utf-8"))["info"]["T"], float)
            out.append((name, src, dots, S_, gt, run / "proc" / "mesh.ply", M, "mesh"))
    return out


def main(argv: list[str] | None = None) -> None:
    from tools.scorecard import DEFAULT_CACHE

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="step 1: read every hole of the recordings at each click (one edge a line)")
    p.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    p.add_argument("--recs", nargs="*", default=RECS)
    p.add_argument("--poses", nargs="*", default=["true", "tracked"])
    p.add_argument("--settings", default="app-default", help="the runs folder of the tracked poses")
    p.add_argument("--every", type=int, default=10, help="every n-th frame")
    p.add_argument("--out", type=Path, default=Path("hole_rim_rows.jsonl"))
    q = sub.add_parser("render-off", help="step 2: the read frames of each recording again with the dots off")
    q.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    q.add_argument("--recs", nargs="*", default=RECS)
    q.add_argument("--work", type=Path, required=True, help="the study's own folder")
    q.add_argument("--hold", type=Path, default=None, help="wait while this file exists")
    m = sub.add_parser("scenes", help="step 2: the study plates (thickness, floor), rendered where they are read")
    m.add_argument("--keys", nargs="*", default=list(STUDY_SCENES))
    m.add_argument("--seeds", nargs="*", type=int, default=[0])
    m.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="a hole recording's render settings are used")
    m.add_argument("--work", type=Path, required=True)
    m.add_argument("--hold", type=Path, default=None)
    r = sub.add_parser("run2", help="step 2: read with the model of the hole")
    r.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    r.add_argument("--recs", nargs="*", default=RECS)
    r.add_argument("--poses", nargs="*", default=["true", "tracked"])
    r.add_argument("--dots", nargs="*", default=["on", "off"])
    r.add_argument("--study", nargs="*", default=[], help="study recordings (folders under --work) to read too")
    r.add_argument("--settings", default="app-default")
    r.add_argument("--work", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--hold", type=Path, default=None)
    t = sub.add_parser("track-study", help="step 3: the study scenes recorded whole and tracked on the processor")
    t.add_argument("--keys", nargs="*", default=[f"{k}-s{s}" for s in range(3) for k in STUDY_SCENES])
    t.add_argument("--work", type=Path, required=True)
    t.add_argument("--hold", type=Path, default=None)
    u = sub.add_parser("track-drop", help="step 3: recordings tracked with and without the dots-off frames")
    u.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    u.add_argument("--recs", nargs="*", default=["holes-high-s2", "holes-low-s0"])
    u.add_argument("--work", type=Path, required=True)
    u.add_argument("--hold", type=Path, default=None)
    v = sub.add_parser("run3", help="step 3: read with the model of the hole and step 3's changes")
    v.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    v.add_argument("--recs", nargs="*", default=RECS)
    v.add_argument("--poses", nargs="*", default=["true", "tracked"])
    v.add_argument("--dots", nargs="*", default=["on", "off"])
    v.add_argument("--study", nargs="*", default=[], help="study scenes at true poses")
    v.add_argument("--study-tracked", nargs="*", default=[], help="study scenes with their processor-path tracking")
    v.add_argument("--drop", nargs="*", default=[], help="recordings read with track-drop's two runs")
    v.add_argument("--settings", default="app-default")
    v.add_argument("--work", type=Path, required=True)
    v.add_argument("--out", type=Path, required=True)
    v.add_argument("--hold", type=Path, default=None)
    v.add_argument("--tag", default="step3")
    for flag in ("either", "stereo", "two-hyp", "centre-rule"):
        v.add_argument(f"--no-{flag}", action="store_true")
    w = sub.add_parser("true-mesh", help="round 4: the study scenes' meshes at the true poses, on the processor")
    w.add_argument("--keys", nargs="*", default=[f"{k}-s{s}" for s in range(3) for k in STUDY_SCENES])
    w.add_argument("--work", type=Path, required=True)
    w.add_argument("--hold", type=Path, default=None)
    x = sub.add_parser("gpu-track", help="round 4: study scenes tracked and meshed by the app's frame pass (graphics card)")
    x.add_argument("--keys", nargs="+", required=True)
    x.add_argument("--work", type=Path, required=True)
    y = sub.add_parser("run4", help="round 4: the gated reading (dots on) and the dots-off arm, with the mesh check")
    y.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    y.add_argument("--recs", nargs="*", default=RECS)
    y.add_argument("--poses", nargs="*", default=["true", "tracked"])
    y.add_argument("--dots", nargs="*", default=["on", "off"])
    y.add_argument("--study-true", nargs="*", default=[])
    y.add_argument("--study-cpu", nargs="*", default=[])
    y.add_argument("--study-gpu", nargs="*", default=[])
    y.add_argument("--true-click", nargs="*", default=[], help="processor-tracked study scenes read at the TRUE click")
    y.add_argument("--settings", default="app-default")
    y.add_argument("--work", type=Path, required=True)
    y.add_argument("--out", type=Path, required=True)
    y.add_argument("--hold", type=Path, default=None)
    y.add_argument("--tag", default="round4")
    y.add_argument("--round5", action="store_true", help="round 5: the mesh's triangles in the finder, all depth in the gate")
    a = ap.parse_args(argv)
    if a.cmd == "true-mesh":
        true_mesh(a.work, a.keys, a.hold)
        return
    if a.cmd == "gpu-track":
        gpu_track(a.work, a.keys)
        return
    if a.cmd == "run4":
        srcs = []
        for dots in a.dots:
            srcs += _scorecard_sources4(a.cache, a.work, a.recs, a.poses, dots, a.settings)
            srcs += _study_sources4(a.work, a.study_true, "true", dots)
            srcs += _study_sources4(a.work, a.study_cpu, "cpu", dots)
            srcs += _study_sources4(a.work, a.study_gpu, "gpu", dots)
            srcs += [(n, sp, d, S_, gt, mp, M, "true") for n, sp, d, S_, gt, mp, M, _ in
                     _study_sources4(a.work, a.true_click, "cpu", dots)]
        run4(srcs, a.out, a.hold, a.tag, a.round5)
        return
    if a.cmd == "track-study":
        track_study(a.work, a.keys, a.hold)
        return
    if a.cmd == "track-drop":
        track_drop(a.cache, a.recs, a.work, a.hold)
        return
    if a.cmd == "run3":
        cfg = Step3(not a.no_either, not a.no_stereo, not a.no_two_hyp, not a.no_centre_rule)
        srcs = []
        for dots in a.dots:
            off = dots == "off"
            for name in a.recs:
                for src in a.poses:
                    srcs.append((name, src, dots, scorecard_source(a.cache, name, src, a.settings, off, a.work)))
            for st in a.study:
                srcs.append((st, "true", dots, study_source(a.work / "study" / st, off)))
            for st in a.study_tracked:
                srcs.append((st, "tracked-cpu", dots, study_tracked_source(a.work, st, off)))
            for name in a.drop:
                for kind in ("all", "without"):
                    srcs.append((name, f"cpu-{kind}", dots,
                                 custom_tracked_source(a.cache, name, a.work / "drop" / name / kind, off, a.work)))
        run3(srcs, a.out, a.hold, cfg, a.tag)
        return
    if a.cmd == "run":
        run(a.cache, a.recs, a.poses, a.out, a.settings, a.every)
    elif a.cmd == "render-off":
        render_off(a.cache, a.recs, a.work, a.hold)
    elif a.cmd == "scenes":
        rp = json.loads((a.cache / "recs" / "holes-high-s0" / "meta.json").read_text(encoding="utf-8"))["render_params"]
        for seed in a.seeds:
            for key in a.keys:
                wait_hold(a.hold)
                t0 = time.perf_counter()
                make_study_scene(key, seed, a.work / "study", rp)
                print(f"{key} seed {seed}: {time.perf_counter() - t0:.0f} s", flush=True)
    else:
        srcs = []
        for dots in a.dots:
            for name in a.recs:
                for src in a.poses:
                    srcs.append((name, src, dots, scorecard_source(a.cache, name, src, a.settings, dots == "off", a.work)))
            for st in a.study:
                srcs.append((st, "true", dots, study_source(a.work / "study" / st, dots == "off")))
        run2(srcs, a.out, a.hold)


if __name__ == "__main__":
    main()
