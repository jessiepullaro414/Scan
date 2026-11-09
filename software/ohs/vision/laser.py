"""Laser "+" extraction and triangulation on rectified images (frame C).

The scanner projects two laser sheets: ``"vertical"`` (seen as a near-vertical line, laser to
the right of the cameras) and ``"horizontal"`` (near-horizontal line, laser above). Each image
row is crossed by the vertical line at most once, and each column by the horizontal line, so the
vertical line is extracted as one sub-pixel centre per row and the horizontal line as one per
column. The horizontal line uses exactly the same code on the transposed image.

Per line (described for the vertical one, where ``t`` = row and ``c`` = column):

1. **Ridge detection with dot suppression.** A separable filter: across the line, the
   scale-normalised negative second derivative of a Gaussian (``ridge_sigma``); along the
   expected line direction, a Gaussian (``along_sigma``). The projected dots (2-3 px blobs) are
   isotropic, so the along-line smoothing attenuates them several-fold while ridges survive. The
   response is normalised so that a Gaussian line of amplitude A matched in width gives about A.
2. **One candidate per row**: the argmax of the response, kept if it exceeds an adaptive
   threshold, ``max(min_response, noise_k * robust std)`` of the response image.
3. **Sub-pixel centre on the raw profile.** It is the midpoint of the two crossings of the
   half-level between the local background (the larger of the left and right minima in the
   window) and the peak, found by linear interpolation. This is unbiased for any symmetric
   profile and handles saturated plateaus naturally: the half-level then falls on the plateau
   edges, so the plateau centre is returned. The window is re-centred once on the first estimate,
   because on wide plateaus the ridge response can peak off-centre. The peak-to-background
   difference is the point's *strength* (DN); points below ``min_contrast`` are dropped.
4. **Linking.** Consecutive rows link into segments while ``|dc| <= max_step * dt`` and gaps are
   at most ``max_gap`` rows. Isolated single-row jumps (dots) are removed first. ``max_step`` (1.2
   px/row) rejects the *other* line, whose slope is 1/tan(30 deg) = 1.7 px/row or more. Finally
   each point is assigned to exactly one family by the local slope of the along-line fit
   (``|slope| <= max_slope = 1``, i.e. within 45 deg of nominal). Orientation is how the two lines
   are told apart, so a line beyond 45 deg is reported in the other family (and is then
   triangulated on the wrong plane; :func:`stereo_check` rejects such points).
5. **Validation per segment.** The profile width must agree with the segment median (a dot
   is narrower and a dot merged with the line is wider), the segment must be at least
   ``rel_contrast`` of the strongest segment and ``min_segment`` points long, and ``end_trim``
   points are dropped at both ends (partial occlusions, dots absorbed at segment ends).
6. **Robust along-line fit.** A local-linear fit with Gaussian weights over ``+-smooth_half``
   rows, run twice; points whose residual exceeds ``outlier_px`` are rejected. A local-linear
   (not local-constant) fit is unbiased for tilted lines, even at segment ends. With
   ``smooth=True`` the centres are replaced by the fit, which roughly halves the noise.
7. **Crossing exclusion.** Near the crossing, each line's profile is contaminated by the other
   line. Points closer than ``cross_radius`` to the other line are dropped, with distance taken
   perpendicular to the other line's polyline and interpolated across its gaps up to
   ``4 * cross_radius`` (the other line is itself missing at the crossing). Only pairs whose
   directions differ by more than 20 deg count as a crossing. Segments are then re-validated.

Pixel parameters default to full resolution (1280x800). Use :meth:`LaserParams.scaled`
(for example ``LaserParams().scaled(0.5)``) for the half-resolution live pipeline. Intensities are
8-bit DN.

Accuracy on synthetic images (see ``tests/test_vision_laser.py``): RMS centre error below
0.01 px for clean lines and about 0.04-0.08 px (full resolution) with the dot pattern, noise and
saturation, with under 0.2% false points. Lines tilted 0-30 deg, curved, gapped and crossing were
tested.

Triangulation intersects the rays of the ideal pinhole ``K`` with the calibrated laser plane.
:func:`stereo_check` validates each 3D point by predicting its position in the right rectified
image (same row, ``u_r = u_l - f*B/z``) and requiring a right-image ridge point of the same line
within ``stereo_tol_px``.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

import cv2
import numpy as np
from scipy.spatial import cKDTree

from ohs.geometry import pixels_to_rays, ray_plane_intersect
from ohs.types import LaserPlane

__all__ = [
    "LaserParams",
    "LaserLines",
    "extract_laser_lines",
    "triangulate_plane",
    "stereo_check",
    "laser_points",
    "draw_overlay",
]

log = logging.getLogger(__name__)

LINE_NAMES = ("vertical", "horizontal")


# --------------------------------------------------------------------------- parameters / result
@dataclass
class LaserParams:
    """Laser extraction parameters. Pixel quantities refer to the processed image (default 1280x800)."""

    ridge_sigma: float = 2.0  # px; cross-line detector scale (handles saturated plateaus up to ~7 px)
    along_sigma: float = 3.0  # px; smoothing along the expected line direction (dot suppression)
    window: int = 9  # px; half-width of the profile window for the sub-pixel centre
    min_response: float = 12.0  # ridge response floor (about DN for a width-matched line)
    noise_k: float = 6.0  # adaptive floor: noise_k * robust std of the ridge response
    min_contrast: float = 20.0  # DN; peak above the local background
    rel_contrast: float = 0.15  # drop segments weaker than this fraction of the strongest one
    width_tol: float = 0.4  # allowed relative deviation of the profile width (0 disables)
    max_step: float = 1.2  # px of centre change per row/column when linking
    max_slope: float = 1.0  # family assignment: |local slope| <= this (1.0 = within 45 deg of nominal)
    max_gap: int = 3  # rows/columns that may be missing inside a segment
    min_segment: int = 20  # minimum points per segment
    smooth_half: int = 6  # half-window (points) of the robust along-line fit (0 disables)
    smooth: bool = True  # replace centres by the robust fit (False: only reject outliers)
    outlier_px: float = 0.35  # residual (px) beyond which a point is rejected
    end_trim: int = 2  # points dropped at each segment end
    cross_radius: float = 15.0  # px; exclusion distance from the other line
    border: int = 2  # px; ignore detections this close to the image border
    # triangulation and validation (used by laser_points)
    min_depth_mm: float = 100.0
    max_depth_mm: float = 600.0
    stereo_check: bool = True
    stereo_tol_px: float = 1.5

    def scaled(self, factor: float) -> "LaserParams":
        """Parameters for images resized by ``factor`` (e.g. 0.5 for 640x400)."""
        f = float(factor)
        if f <= 0:
            raise ValueError("scale factor must be positive")
        r = lambda x, lo: max(lo, int(math.floor(x * f + 0.5)))  # round half up  # noqa: E731
        return replace(
            self,
            ridge_sigma=max(0.8, self.ridge_sigma * f),
            along_sigma=max(1.0, self.along_sigma * f),
            window=max(3, int(math.ceil(self.window * f - 1e-9))),  # never narrower than needed
            max_gap=r(self.max_gap, 1),
            min_segment=r(self.min_segment, 5),
            smooth_half=r(self.smooth_half, 2) if self.smooth_half > 0 else 0,
            end_trim=r(self.end_trim, 1) if self.end_trim > 0 else 0,
            cross_radius=max(3.0, self.cross_radius * f),
            border=r(self.border, 1),
            stereo_tol_px=max(1.0, self.stereo_tol_px * f),
        )


@dataclass
class LaserLines:
    """Sub-pixel laser-line centres in one rectified image (pixel coordinates ``(u, v)``).

    ``vertical`` has one point per image row (integer ``v``), ``horizontal`` one per column
    (integer ``u``). Strengths are peak-above-background contrasts in DN. ``*_segment`` hold
    segment labels (consecutive points with the same label form a polyline).
    """

    vertical: np.ndarray
    horizontal: np.ndarray
    vertical_strength: np.ndarray
    horizontal_strength: np.ndarray
    vertical_segment: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int32))
    horizontal_segment: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int32))

    def get(self, name: str) -> np.ndarray:
        """(N,2) centres of line ``name`` ("vertical" | "horizontal")."""
        return self._pick(name, self.vertical, self.horizontal)

    def strength(self, name: str) -> np.ndarray:
        return self._pick(name, self.vertical_strength, self.horizontal_strength)

    def segments(self, name: str) -> np.ndarray:
        return self._pick(name, self.vertical_segment, self.horizontal_segment)

    def as_dict(self) -> dict[str, np.ndarray]:
        """``{"vertical": (N,2), "horizontal": (M,2)}`` (e.g. ``LaserBoardObservation.line_uv``)."""
        return {"vertical": self.vertical, "horizontal": self.horizontal}

    @staticmethod
    def _pick(name: str, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        if name == "vertical":
            return a
        if name == "horizontal":
            return b
        raise KeyError(f"unknown laser line {name!r}; expected one of {LINE_NAMES}")

    def __len__(self) -> int:
        return int(len(self.vertical) + len(self.horizontal))


# --------------------------------------------------------------------------- kernels
def _ridge_kernel(sigma: float) -> np.ndarray:
    """Zero-mean ``-sigma^2 g''`` kernel with unit response to a width-matched Gaussian line."""
    r = max(1, int(np.ceil(3.5 * sigma)))
    x = np.arange(-r, r + 1, dtype=np.float64)
    g = np.exp(-0.5 * (x / sigma) ** 2)
    k = (1.0 - (x / sigma) ** 2) * g
    k -= k.mean()
    k /= np.sum(k * g)
    return k.astype(np.float32)


def _gauss_kernel(sigma: float) -> np.ndarray:
    if sigma <= 0:
        return np.ones(1, np.float32)
    r = max(1, int(np.ceil(3.0 * sigma)))
    x = np.arange(-r, r + 1, dtype=np.float64)
    g = np.exp(-0.5 * (x / sigma) ** 2)
    return (g / g.sum()).astype(np.float32)


# --------------------------------------------------------------------------- segment helpers (vectorised)
def _link(t: np.ndarray, c: np.ndarray, max_step: float, max_gap: int) -> np.ndarray:
    """Segment labels 0..K-1 for points sorted by ``t`` (one per t)."""
    if len(t) == 0:
        return np.zeros(0, np.int64)
    dt = np.diff(t)
    brk = (dt > max_gap + 1) | (np.abs(np.diff(c)) > max_step * dt + 0.3)
    return np.concatenate([[0], np.cumsum(brk)])


def _seg_sizes(seg: np.ndarray) -> np.ndarray:
    """Size of each point's segment."""
    return np.bincount(seg)[seg] if len(seg) else np.zeros(0, np.int64)


def _seg_median(values: np.ndarray, seg: np.ndarray) -> np.ndarray:
    """Per-point median of ``values`` over its segment (segments are contiguous label runs)."""
    order = np.lexsort((values, seg))
    counts = np.bincount(seg)
    starts = np.cumsum(counts) - counts
    sv = values[order]
    lo = sv[starts + (counts - 1) // 2]
    hi = sv[starts + counts // 2]
    return (0.5 * (lo + hi))[seg]


def _seg_max(values: np.ndarray, seg: np.ndarray) -> np.ndarray:
    out = np.full(int(seg.max()) + 1, -np.inf)
    np.maximum.at(out, seg, values)
    return out


def _local_linear_fit(t: np.ndarray, c: np.ndarray, w: np.ndarray, seg: np.ndarray, half: int) -> tuple[np.ndarray, np.ndarray]:
    """Local-linear fit of ``c(t)`` (Gaussian weights, +-half samples): ``(value, slope)`` per point.

    Segments are laid out on one padded 1-D grid, so a single convolution per moment serves all
    of them without mixing neighbouring segments. ``w`` are per-point weights (0 = ignore).
    """
    t = t.astype(np.int64)
    seg_first = np.r_[True, seg[1:] != seg[:-1]]
    t0 = np.maximum.accumulate(np.where(seg_first, t, np.iinfo(np.int64).min))
    # per-segment span and offset in the padded grid
    span_end = np.zeros(int(seg.max()) + 1, np.int64)
    np.maximum.at(span_end, seg, t - t0)
    pad = half + 1
    offsets = np.concatenate([[pad], pad + np.cumsum(span_end + 1 + pad)])[: len(span_end)]
    pos = offsets[seg] + (t - t0)
    n = int(offsets[-1] + span_end[-1] + 1 + pad)
    wd = np.zeros(n)
    cd = np.zeros(n)
    wd[pos] = w
    cd[pos] = c * w
    d = np.arange(-half, half + 1, dtype=np.float64)
    g = np.exp(-0.5 * (d / max(half / 2.0, 1.0)) ** 2)
    # np.convolve(x, k)[i] = sum_d x[i - d] k(d): neighbour offset (j - i) = -d
    S0 = np.convolve(wd, g, "same")
    S1 = np.convolve(wd, -d * g, "same")
    S2 = np.convolve(wd, d * d * g, "same")
    T0 = np.convolve(cd, g, "same")
    T1 = np.convolve(cd, -d * g, "same")
    S0, S1, S2, T0, T1 = S0[pos], S1[pos], S2[pos], T0[pos], T1[pos]
    det = S0 * S2 - S1 * S1
    ok = det > 1e-9 * np.maximum(S0, 1e-12) ** 2
    with np.errstate(divide="ignore", invalid="ignore"):
        lin = (S2 * T0 - S1 * T1) / det
        slope = (S0 * T1 - S1 * T0) / det
        const = T0 / S0
    value = np.where(S0 > 0, np.where(ok, lin, const), c)
    return value, np.where(ok, slope, 0.0)


def _trim_ends(seg: np.ndarray, n: int) -> np.ndarray:
    """Mask dropping ``n`` points at both ends of every segment."""
    idx = np.arange(len(seg))
    first = np.r_[True, seg[1:] != seg[:-1]]
    last = np.r_[seg[1:] != seg[:-1], True]
    start = np.maximum.accumulate(np.where(first, idx, 0))
    end = np.minimum.accumulate(np.where(last, idx, len(seg))[::-1])[::-1]
    return (idx - start >= n) & (end - idx >= n)


# --------------------------------------------------------------------------- per-axis extraction
def _half_level_centres(img: np.ndarray, t: np.ndarray, j: np.ndarray, W: int):
    """Half-level crossing midpoints in windows ``img[t, j-W..j+W]``.

    Returns ``(centre, contrast, width, ok)``; ``centre`` is in column units.
    """
    n = len(t)
    idx = np.arange(n)
    ar = np.arange(2 * W + 1)
    P = img[t[:, None], j[:, None] + (ar - W)[None, :]].astype(np.float32)
    k0 = np.argmax(P[:, W - 1 : W + 2], axis=1) + (W - 1)
    pk = P[idx, k0]
    big = np.float32(3.0e38)
    m_left = np.where(ar[None, :] < k0[:, None], P, big).min(1)
    m_right = np.where(ar[None, :] > k0[:, None], P, big).min(1)
    bkg = np.maximum(m_left, m_right)
    level = bkg + 0.5 * (pk - bkg)
    above = P >= level[:, None]
    # contiguous run of samples >= level around k0
    left_run = np.cumprod(np.where(ar[None, :] <= k0[:, None], above, True)[:, ::-1], axis=1).sum(1) - (2 * W - k0)
    right_run = np.cumprod(np.where(ar[None, :] >= k0[:, None], above, True), axis=1).sum(1) - k0
    a = k0 - left_run  # last sample below the level on the left
    b = k0 + right_run  # first sample below the level on the right
    ok = (a >= 0) & (b <= 2 * W) & (pk > bkg)
    a = np.clip(a, 0, 2 * W - 1)
    b = np.clip(b, 1, 2 * W)
    pa, pa1 = P[idx, a], P[idx, a + 1]
    pb, pb1 = P[idx, b], P[idx, b - 1]
    xl = a + (level - pa) / np.maximum(pa1 - pa, 1e-6)
    xr = b - (level - pb) / np.maximum(pb1 - pb, 1e-6)
    centre = j - W + 0.5 * (xl + xr)
    return centre.astype(np.float64), (pk - bkg).astype(np.float64), (xr - xl).astype(np.float64), ok


def _extract_axis(img: np.ndarray, p: LaserParams) -> tuple[np.ndarray, ...]:
    """Near-vertical ridges of ``img`` (uint8/uint16/float32).

    Returns ``(t, c, strength, segment, slope)``: ``t`` is the row index, ``c`` the sub-pixel
    column of the centre and ``slope`` the local ``dc/dt``.
    """
    h, w = img.shape
    W = int(p.window)
    empty = (np.zeros(0), np.zeros(0), np.zeros(0), np.zeros(0, np.int64), np.zeros(0))
    b = W + max(1, int(p.border))
    if w <= 2 * b + 2 or h < max(3, p.min_segment):
        return empty
    resp = cv2.sepFilter2D(img, cv2.CV_32F, _ridge_kernel(p.ridge_sigma), _gauss_kernel(p.along_sigma),
                           borderType=cv2.BORDER_REPLICATE)
    inner = resp[:, b : w - b]
    j = np.argmax(inner, axis=1)
    rows = np.arange(h)
    r = inner[rows, j]
    j = j + b
    # adaptive floor from a robust (median/MAD) noise estimate on ~5k samples of the response
    sub = inner[:: max(1, h // 60), :: max(1, (w - 2 * b) // 80)]
    med = float(np.median(sub))
    noise = 1.4826 * float(np.median(np.abs(sub - med)))
    thr = max(p.min_response, med + p.noise_k * noise)
    sel = r >= thr
    t, j = rows[sel], j[sel]
    if len(t) < 2:
        return empty
    # sub-pixel centre, re-centred once on the first estimate (only rows whose window moves)
    c, con, wid, ok = _half_level_centres(img, t, j, W)
    j2 = np.clip(np.rint(np.where(ok, c, j)).astype(np.int64), b, w - b - 1)
    redo = j2 != j
    if redo.any():
        c[redo], con[redo], wid[redo], ok[redo] = _half_level_centres(img, t[redo], j2[redo], W)
    sel = ok & (con >= p.min_contrast) & (np.abs(c - j2) <= W - 1)
    t, c, con, wid = t[sel], c[sel], con[sel], wid[sel]
    if len(t) < 2:
        return empty
    # isolated single-row jumps (dots) before linking
    jump = 2.0 * p.max_step
    dprev = np.abs(np.diff(c, prepend=np.nan))
    dnext = np.abs(np.diff(c, append=np.nan))
    keep = ~((dprev > jump) & (dnext > jump))
    t, c, con, wid = t[keep], c[keep], con[keep], wid[keep]

    slope = np.zeros(len(t))

    def relink(mask: np.ndarray | None = None):
        nonlocal t, c, con, wid, seg, slope
        if mask is not None:
            t, c, con, wid, slope = t[mask], c[mask], con[mask], wid[mask], slope[mask]
        seg = _link(t, c, p.max_step, p.max_gap)
        long_ = _seg_sizes(seg) >= p.min_segment
        t, c, con, wid, seg, slope = t[long_], c[long_], con[long_], wid[long_], seg[long_], slope[long_]
        if len(seg):
            seg = np.unique(seg, return_inverse=True)[1]
        return len(t) > 0

    seg = np.zeros(0, np.int64)
    if not relink():
        return empty
    if p.width_tol > 0:
        med_w = _seg_median(wid, seg)
        if not relink(np.abs(wid - med_w) <= np.maximum(p.width_tol * med_w, 1.0)):
            return empty
    if p.rel_contrast > 0:
        seg_strength = _seg_median(con, seg)
        if not relink(seg_strength >= p.rel_contrast * seg_strength.max()):
            return empty
    # robust along-line fit; its local slope also assigns each point to exactly one family
    # (|slope| <= max_slope), so a line near 45 deg is never reported as both lines
    half = int(p.smooth_half) if p.smooth_half > 0 else 3
    wts = np.ones(len(t))
    for _ in range(2 if p.smooth_half > 0 else 1):
        fit, slope = _local_linear_fit(t, c, wts, seg, half)
        if p.smooth_half > 0:
            wts = (np.abs(c - fit) < p.outlier_px).astype(np.float64)
    good = (wts > 0) & (np.abs(slope) <= p.max_slope)
    if p.smooth_half > 0 and p.smooth:
        c = fit
    if not relink(good):
        return empty
    if p.end_trim > 0:
        if not relink(_trim_ends(seg, int(p.end_trim))):
            return empty
    return t.astype(np.float64), c, con, seg, slope


_SIN_CROSS = np.sin(np.radians(20.0))  # lines closer than 20 deg in direction do not "cross"


def _unit(dx: np.ndarray, dy: np.ndarray) -> np.ndarray:
    d = np.stack([dx, dy], axis=1).astype(np.float64)
    return d / np.linalg.norm(d, axis=1, keepdims=True)


def _cross2(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """z component of the cross product of (N,2) direction arrays."""
    return a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]


def _near_other_line(pts: np.ndarray, dirs: np.ndarray, other: np.ndarray, other_dirs: np.ndarray,
                     other_vertical: bool, radius: float) -> np.ndarray:
    """Mask of ``pts`` within ``radius`` of the other line where the two lines actually cross.

    The other line is a function ``c(t)`` (``u(v)`` if vertical, ``v(u)`` otherwise), linearly
    interpolated between consecutive points, including across gaps up to ``4 * radius`` (it is
    usually missing right at the crossing). Distance is measured perpendicular to that chord.
    Nearly collinear pairs (direction difference under 20 deg, e.g. one physical line split between
    the families at 45 deg) are not treated as a crossing.
    """
    near = np.zeros(len(pts), bool)
    if len(pts) == 0 or len(other) == 0:
        return near
    d, k = cKDTree(other).query(pts, k=1, distance_upper_bound=radius)
    hit = np.isfinite(d)
    if hit.any():
        near[np.nonzero(hit)[0]] = np.abs(_cross2(dirs[hit], other_dirs[k[hit]])) >= _SIN_CROSS
    if len(other) < 2:
        return near
    if other_vertical:
        ot, oc, qt, qc = other[:, 1], other[:, 0], pts[:, 1], pts[:, 0]
    else:
        ot, oc, qt, qc = other[:, 0], other[:, 1], pts[:, 0], pts[:, 1]
    order = np.argsort(ot, kind="stable")
    ot, oc = ot[order], oc[order]
    i = np.clip(np.searchsorted(ot, qt), 1, len(ot) - 1)
    t0, t1, c0, c1 = ot[i - 1], ot[i], oc[i - 1], oc[i]
    span = t1 - t0
    bridge = (qt >= t0) & (qt <= t1) & (span > 0) & (span <= 4.0 * radius)
    slope = (c1 - c0) / np.where(span > 0, span, 1.0)
    dperp = np.abs(qc - (c0 + slope * (qt - t0))) / np.sqrt(1.0 + slope * slope)
    chord = _unit(slope, np.ones_like(slope)) if other_vertical else _unit(np.ones_like(slope), slope)
    crossing = np.abs(_cross2(dirs, chord)) >= _SIN_CROSS
    return near | (bridge & (dperp < radius) & crossing)


def _family_duplicates(vert: np.ndarray, slope_v: np.ndarray, horz: np.ndarray, slope_h: np.ndarray,
                       tol: float = 1.5) -> tuple[np.ndarray, np.ndarray]:
    """Points found by both families (one line near 45 deg): keep the family with the smaller slope."""
    dup_v = np.zeros(len(vert), bool)
    dup_h = np.zeros(len(horz), bool)
    d, k = cKDTree(horz).query(vert, k=1, distance_upper_bound=tol)
    i = np.nonzero(np.isfinite(d))[0]
    if len(i):
        j = k[i]
        v_loses = np.abs(slope_v[i]) > np.abs(slope_h[j])
        dup_v[i[v_loses]] = True
        dup_h[j[~v_loses]] = True
    return dup_v, dup_h


def extract_laser_lines(img_r: np.ndarray, params: LaserParams | None = None) -> LaserLines:
    """Extract the vertical and horizontal laser lines from a rectified grayscale image."""
    p = params if params is not None else LaserParams()
    img = np.asarray(img_r)
    if img.ndim != 2:
        raise ValueError(f"expected a grayscale image, got shape {img.shape}")
    if img.dtype not in (np.uint8, np.uint16, np.float32):
        img = img.astype(np.float32)
    img = np.ascontiguousarray(img)
    tv, cv_, sv, segv, slv = _extract_axis(img, p)
    th, ch, sh, segh, slh = _extract_axis(cv2.transpose(img), p)
    vert = np.stack([cv_, tv], axis=1) if len(tv) else np.zeros((0, 2))
    horz = np.stack([th, ch], axis=1) if len(th) else np.zeros((0, 2))
    if len(vert) and len(horz):
        keep_v, keep_h = np.ones(len(vert), bool), np.ones(len(horz), bool)
        dup_v, dup_h = _family_duplicates(vert, slv, horz, slh)
        keep_v &= ~dup_v
        keep_h &= ~dup_h
        if p.cross_radius > 0:
            dir_v = _unit(slv, np.ones_like(slv))  # (du, dv) of the vertical line
            dir_h = _unit(np.ones_like(slh), slh)
            keep_v &= ~_near_other_line(vert, dir_v, horz, dir_h, other_vertical=False, radius=p.cross_radius)
            keep_h &= ~_near_other_line(horz, dir_h, vert, dir_v, other_vertical=True, radius=p.cross_radius)
        vert, sv, segv = _revalidate(vert, sv, keep_v, p, vertical=True)
        horz, sh, segh = _revalidate(horz, sh, keep_h, p, vertical=False)
    log.debug("extract_laser_lines: %d vertical, %d horizontal points", len(vert), len(horz))
    return LaserLines(vert, horz, sv, sh, segv.astype(np.int32), segh.astype(np.int32))


def _revalidate(pts: np.ndarray, strength: np.ndarray, keep: np.ndarray, p: LaserParams, vertical: bool):
    """Apply ``keep``, re-link and drop segments that became shorter than ``min_segment``."""
    pts, strength = pts[keep], strength[keep]
    if len(pts) == 0:
        return pts, strength, np.zeros(0, np.int64)
    t, c = (pts[:, 1], pts[:, 0]) if vertical else (pts[:, 0], pts[:, 1])
    seg = _link(t, c, p.max_step, p.max_gap)
    long_ = _seg_sizes(seg) >= p.min_segment
    pts, strength, seg = pts[long_], strength[long_], seg[long_]
    if len(seg):
        seg = np.unique(seg, return_inverse=True)[1]
    return pts, strength, seg


# --------------------------------------------------------------------------- triangulation / validation
def triangulate_plane(uv: np.ndarray, K: np.ndarray, plane: LaserPlane) -> np.ndarray:
    """Intersect the rays of rectified pixels ``uv`` (N,2) with a laser plane -> (N,3) mm in frame C.

    Rows are NaN where the ray is parallel to the plane or the intersection is behind the camera.
    """
    uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
    if len(uv) == 0:
        return np.zeros((0, 3))
    return ray_plane_intersect(pixels_to_rays(uv, K), plane.normal, plane.d)


def stereo_check(points_c: np.ndarray, right_uv: np.ndarray, K: np.ndarray, baseline_mm: float,
                 tol_px: float = 1.5) -> np.ndarray:
    """Boolean mask of 3D points whose predicted right-image position lies on a right laser ridge.

    ``points_c`` (N,3) in frame C (NaN allowed). In the rectified right image the point appears
    on the same row at ``u_r = u_l - f*B/z``. ``right_uv`` (M,2) are the ridge points of the *same*
    laser line extracted from the right rectified image. A point passes when a ridge point lies
    within ``tol_px``.
    """
    P = np.asarray(points_c, dtype=np.float64).reshape(-1, 3)
    ok = np.zeros(len(P), bool)
    right_uv = np.asarray(right_uv, dtype=np.float64).reshape(-1, 2)
    if len(P) == 0 or len(right_uv) == 0:
        return ok
    finite = np.all(np.isfinite(P), axis=1) & (P[:, 2] > 0)
    if not finite.any():
        return ok
    Q = P[finite]
    f, cx, cy, s = K[0, 0], K[0, 2], K[1, 2], K[0, 1]
    x, y = Q[:, 0] / Q[:, 2], Q[:, 1] / Q[:, 2]
    v = K[1, 1] * y + cy
    u_r = f * x + s * y + cx - f * float(baseline_mm) / Q[:, 2]
    d, _ = cKDTree(right_uv).query(np.stack([u_r, v], axis=1), k=1, distance_upper_bound=float(tol_px))
    ok[np.nonzero(finite)[0]] = np.isfinite(d)
    return ok


def laser_points(
    left_r: np.ndarray,
    right_r: np.ndarray | None,
    K: np.ndarray,
    baseline: float,
    planes: Mapping[str, LaserPlane],
    params: LaserParams | None = None,
    *,
    lines_left: LaserLines | None = None,
    lines_right: LaserLines | None = None,
) -> dict[str, np.ndarray]:
    """Validated laser points (mm, frame C) per calibrated plane: ``{name: (N,3)}``.

    Extracts the lines in the left image (unless ``lines_left`` is given), triangulates them on
    their planes, keeps depths within ``[min_depth_mm, max_depth_mm]`` and, if
    ``params.stereo_check`` is set and the right image (or ``lines_right``) is available,
    applies :func:`stereo_check`. Plane names other than "vertical"/"horizontal" are ignored.
    """
    p = params if params is not None else LaserParams()
    if lines_left is None:
        lines_left = extract_laser_lines(left_r, p)
    do_check = p.stereo_check and (lines_right is not None or right_r is not None)
    if do_check and lines_right is None:
        lines_right = extract_laser_lines(right_r, p)
    out: dict[str, np.ndarray] = {}
    for name, plane in planes.items():
        if name not in LINE_NAMES:
            log.warning("laser_points: ignoring plane %r (expected %s)", name, LINE_NAMES)
            continue
        P = triangulate_plane(lines_left.get(name), K, plane)
        z = P[:, 2]
        ok = np.isfinite(z) & (z >= p.min_depth_mm) & (z <= p.max_depth_mm)
        if do_check and ok.any():
            ok &= stereo_check(P, lines_right.get(name), K, baseline, p.stereo_tol_px)
        out[name] = P[ok]
    return out


# --------------------------------------------------------------------------- live preview
_COLOURS = {"vertical": (60, 60, 255), "horizontal": (60, 230, 60)}  # BGR: red / green


def draw_overlay(img_gray: np.ndarray, lines: LaserLines, thickness: int = 1) -> np.ndarray:
    """BGR preview image with the vertical (red) and horizontal (green) centre polylines."""
    img = np.asarray(img_gray)
    if img.ndim == 2:
        out = cv2.cvtColor(img if img.dtype == np.uint8 else cv2.convertScaleAbs(img), cv2.COLOR_GRAY2BGR)
    else:
        out = img.copy()
    shift = 4
    h, w = out.shape[:2]
    for name in LINE_NAMES:
        uv = np.asarray(lines.get(name), dtype=np.float64).reshape(-1, 2)
        if len(uv) == 0:
            continue
        seg = np.asarray(lines.segments(name))
        if len(seg) == len(uv):
            pts = np.rint(uv * (1 << shift)).astype(np.int32)
            cuts = np.nonzero(np.diff(seg))[0] + 1
            polys = [q.reshape(-1, 1, 2) for q in np.split(pts, cuts)]
            cv2.polylines(out, polys, False, _COLOURS[name], thickness, cv2.LINE_AA, shift)
        else:  # no segment labels: paint the centre pixels
            ui, vi = np.rint(uv[:, 0]).astype(int), np.rint(uv[:, 1]).astype(int)
            inside = (ui >= 0) & (ui < w) & (vi >= 0) & (vi < h)
            out[vi[inside], ui[inside]] = _COLOURS[name]
    return out
