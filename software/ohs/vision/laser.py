"""Laser "+" extraction and triangulation on rectified images (frame C).

The scanner projects two laser sheets: ``"vertical"`` (seen as a near-vertical line, laser to
the right of the cameras) and ``"horizontal"`` (near-horizontal line, laser above). Each image
row is crossed by the vertical line at most once, and each column by the horizontal line, so the
vertical line is extracted as one sub-pixel centre per row and the horizontal line as one per
column. The horizontal line uses exactly the same code on the transposed image.

A laser line's name starts with its *family*, how its line runs in the image: ``"vertical"`` or
``"horizontal"``, optionally followed by ``_`` and anything (``"vertical_2"``; :func:`line_family`).
The 4-laser variant has two lines of each family. Extraction works per family (``LaserLines``
holds all lines of a family together), and :func:`laser_points` decides per point which plane of
its family it lies on (see "Several lines per family" below). With one line per family
(``lines_per_family=1``, the default) everything below is exactly the 2-laser design.

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

**Several lines per family** (``lines_per_family`` = 2 with four lasers; the pipeline sets it from
the calibrated planes):

* Step 2 takes up to ``lines_per_family`` candidates per row: local maxima of the response over
  ``+-family_sep / 2`` px, strongest first.
* Step 4 links them into tracks instead (:func:`_link_tracks`): each track predicts its next
  centre from its slope over its last 8 points and takes the candidate it predicts best. Where
  two tracks seen side by side come closer than ``family_sep`` (10 px at full resolution), the
  two profiles overlap and then merge into one, whose centre is biased: those points are dropped,
  and both tracks are carried on along their predictions until they are apart again. Points
  closer than ``family_sep`` to a point of another track in the same row are dropped too. Later
  steps only split tracks into segments (never join them), so a segment holds one line.
* Step 7 measures the distance to each line of the other family separately, the line's
  segments chained across gaps (up to ``4 * cross_radius``).
* :func:`laser_points` triangulates each point of a family on every plane of that family; the
  stereo check (and the depth check, with a dense depth map) decide which plane it is on. Where
  several pass, the smallest stereo residual wins, if their 3D points agree within
  ``family_agree_mm`` (near the line where the sheets intersect); otherwise the point is dropped.
  The stereo check of a horizontal line hardly depends on the depth (the line runs along the
  epipolar rows), so with a dense depth map the plane must also agree with it: the point needs
  dense depth around it, and where its own pixel has a value, the plane whose point is closest
  to it.

On the tests' synthetic images (two lines per family, tilted, curved, gapped and crossing each
other at 11-13 deg) the centre error is 0.03-0.06 px rms, as for one line per family; on a
simulated 4-laser frame with the dense depth every kept point is on its true plane. Extraction
takes about three times as long as with one line per family (tracking is a Python loop).

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
(family) within ``stereo_tol_px``.
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
    "LINE_NAMES",
    "line_family",
    "is_line_name",
    "lines_per_family",
    "extract_laser_lines",
    "stereo_residual",
    "assign_planes",
    "triangulate_plane",
    "stereo_check",
    "depth_check",
    "laser_points",
    "draw_overlay",
]

log = logging.getLogger(__name__)

#: the two line families: how a line runs in the image
LINE_NAMES = ("vertical", "horizontal")


def line_family(name: str) -> str:
    """The family of laser line ``name``: ``"vertical"`` or ``"horizontal"``.

    A line's name is its family, optionally followed by ``_`` and anything (``"vertical_2"``).
    Raises ``KeyError`` for any other name.
    """
    for fam in LINE_NAMES:
        if name == fam or (isinstance(name, str) and name.startswith(fam + "_")):
            return fam
    raise KeyError(f"unknown laser line {name!r}; a line's name starts with one of {LINE_NAMES}")


def lines_per_family(names) -> int:
    """The most laser lines of one family among ``names`` (plane names; others ignored), at least 1:
    the :attr:`LaserParams.lines_per_family` a calibration with these planes needs."""
    fams = [line_family(n) for n in names if is_line_name(n)]
    return max([fams.count(f) for f in LINE_NAMES] + [1])


def is_line_name(name: str) -> bool:
    """True when ``name`` names a laser line (see :func:`line_family`)."""
    try:
        line_family(name)
    except KeyError:
        return False
    return True


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
    # several lasers per family (module docstring, "Several lines per family")
    lines_per_family: int = 1  # lines of one family that can cross an image row/column (2 with four lasers)
    family_sep: float = 10.0  # px; where two lines of one family come closer than this, both are dropped
    family_agree_mm: float = 1.0  # laser_points: a point several planes of its family take must agree this well
    # triangulation and validation (used by laser_points)
    min_depth_mm: float = 100.0
    max_depth_mm: float = 600.0
    stereo_check: bool = True
    stereo_tol_px: float = 1.5
    # agreement with the dense depth (laser_points with a depth map): |z - depth| <= max(min, k sigma)
    depth_check: bool = True
    depth_check_k: float = 4.0
    depth_check_min_mm: float = 1.0

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
            family_sep=max(4.0, self.family_sep * f),
            stereo_tol_px=max(1.0, self.stereo_tol_px * f),
        )


@dataclass
class LaserLines:
    """Sub-pixel laser-line centres in one rectified image (pixel coordinates ``(u, v)``).

    ``vertical`` has one point per image row (integer ``v``) and line, ``horizontal`` one per
    column (integer ``u``) and line. Strengths are peak-above-background contrasts in DN.
    ``*_segment`` hold segment labels (consecutive points with the same label form a polyline;
    a segment is on one line).
    """

    # every line of the family (four lasers: two); their segments tell the lines apart
    vertical: np.ndarray
    horizontal: np.ndarray
    vertical_strength: np.ndarray
    horizontal_strength: np.ndarray
    vertical_segment: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int32))
    horizontal_segment: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int32))

    def get(self, name: str) -> np.ndarray:
        """(N,2) centres of the family of line ``name`` (``"vertical_2"`` gives the vertical ones)."""
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
        return a if line_family(name) == "vertical" else b

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


def _split(t: np.ndarray, c: np.ndarray, seg: np.ndarray, max_step: float, max_gap: int) -> np.ndarray:
    """Segment labels for points laid out as contiguous runs of ``seg`` (each sorted by ``t``):
    the runs, split where :func:`_link` would break them. Runs are never joined."""
    if len(t) == 0:
        return np.zeros(0, np.int64)
    dt = np.diff(t)
    brk = (seg[1:] != seg[:-1]) | (dt > max_gap + 1) | (np.abs(np.diff(c)) > max_step * np.abs(dt) + 0.3)
    return np.concatenate([[0], np.cumsum(brk)])


def _nms_radius(p: "LaserParams") -> int:
    """Candidates of one row closer than this (px) are one ridge (several lines per family)."""
    return max(2, int(math.ceil(0.5 * float(p.family_sep))))


def _row_candidates(resp: np.ndarray, thr: float, n: int, radius: int) -> tuple[np.ndarray, np.ndarray]:
    """Up to ``n`` ridge candidates per row: local maxima of ``resp`` over ``+-radius`` columns,
    at least ``thr``, strongest first. Returns ``(row, column)`` sorted by row, then column."""
    mx = cv2.dilate(resp, np.ones((1, 2 * radius + 1), np.uint8), borderType=cv2.BORDER_REPLICATE)
    rr, cc = np.nonzero((resp >= mx) & (resp >= thr))
    if len(rr) == 0:
        return rr.astype(np.int64), cc.astype(np.int64)
    # a flat top gives equal neighbours: keep the first of a row's candidates within the radius
    dup = np.r_[False, (rr[1:] == rr[:-1]) & (cc[1:] - cc[:-1] <= radius)]
    rr, cc = rr[~dup], cc[~dup]
    val = resp[rr, cc]
    order = np.lexsort((-val, rr))
    rr, cc = rr[order], cc[order]
    first = np.r_[0, np.flatnonzero(rr[1:] != rr[:-1]) + 1]
    rank = np.arange(len(rr)) - np.repeat(first, np.diff(np.r_[first, len(rr)]))
    keep = rank < n
    rr, cc = rr[keep], cc[keep]
    order = np.lexsort((cc, rr))
    return rr[order].astype(np.int64), cc[order].astype(np.int64)


def _link_tracks(t: np.ndarray, c: np.ndarray, p: "LaserParams") -> np.ndarray:
    """Track labels for candidates with several per row (``t`` sorted); -1 = dropped.

    Each track predicts its next centre from its slope over its last 8 points. A candidate goes
    to the track that predicts it best within ``max_step * dt + 0.3`` px; the others start new
    tracks. Two established tracks (8 points or more) seen side by side in 3 rows or more are two
    lines; where their predictions come closer than ``family_sep``, the profiles overlap or merge
    into one, whose centre is biased: every candidate within ``family_sep`` of either prediction
    is dropped, and both tracks carry on along their predictions (``max_gap`` does not end them
    there) until they are apart again. Finally, points closer than ``family_sep`` to a point of
    another track of ``min_segment`` points or more in the same row are dropped too."""
    n = len(t)
    lab = np.full(n, -1, np.int64)
    if n == 0:
        return lab
    tl, cl = t.tolist(), c.tolist()
    sep = float(p.family_sep)
    gap = int(p.max_gap) + 1
    step = float(p.max_step)
    hist = 8  # points behind the slope estimate; also the length of an established track
    # one ridge: a row's candidates closer than the NMS radius (re-centring can bring two together)
    rad = _nms_radius(p)
    drop = [False] * n
    for k in range(1, n):
        if tl[k] == tl[k - 1] and abs(cl[k] - cl[k - 1]) < rad and not drop[k - 1]:
            drop[k] = True
    # a track: [last t, last c, slope, label, recent (t, c) points, real points]
    tracks: list[list] = []
    n_tracks = 0
    labs = [-1] * n
    together: dict[tuple[int, int], list[int]] = {}  # pair of labels -> [last row with both, rows with both]
    merging: set[tuple[int, int]] = set()  # pairs carried on through a merge
    ends = np.r_[np.flatnonzero(np.diff(t)) + 1, n].tolist()
    i = 0
    for k1 in ends:
        row = tl[i]
        cands = [k for k in range(i, k1) if not drop[k]]
        i = k1
        tracks = [tr for tr in tracks if row - tr[0] <= gap]
        pred = [tr[1] + tr[2] * (row - tr[0]) for tr in tracks]
        busy_t: set[int] = set()
        busy_c: set[int] = set()
        # two lines closer than the separation: drop what is near them, carry both on
        still: set[tuple[int, int]] = set()
        nt = len(tracks)
        for a in range(nt):
            ta = tracks[a]
            for bb in range(a + 1, nt):
                if abs(pred[a] - pred[bb]) >= sep:
                    continue
                tb = tracks[bb]
                key = (ta[3], tb[3]) if ta[3] < tb[3] else (tb[3], ta[3])
                if key not in merging:
                    if ta[5] < hist or tb[5] < hist:
                        continue
                    tg = together.get(key)
                    if tg is None or row - tg[0] > gap or tg[1] < 3:
                        continue
                still.add(key)
                for q in (a, bb):
                    busy_t.add(q)
                    for k in cands:
                        if abs(cl[k] - pred[q]) < sep:
                            busy_c.add(k)
        for q in busy_t:
            tracks[q][1], tracks[q][0] = pred[q], row  # coast
        merging = still
        # the rest: best prediction first
        pairs = []
        for k in cands:
            if k in busy_c:
                continue
            ck = cl[k]
            for q in range(nt):
                if q not in busy_t:
                    e = abs(ck - pred[q])
                    if e <= step * (row - tracks[q][0]) + 0.3:
                        pairs.append((e, q, k))
        if len(pairs) > 1:
            pairs.sort()
        for _, q, k in pairs:
            if q in busy_t or k in busy_c:
                continue
            busy_t.add(q)
            busy_c.add(k)
            tr = tracks[q]
            rec = tr[4]
            rec.append((row, cl[k]))
            if len(rec) > hist:
                del rec[0]
            t0, c0 = rec[0]
            if row - t0 >= 3:
                tr[2] = (cl[k] - c0) / (row - t0)
            tr[0], tr[1] = row, cl[k]
            tr[5] += 1
            labs[k] = tr[3]
        here = []
        for k in cands:
            if k not in busy_c:
                tracks.append([row, cl[k], 0.0, n_tracks, [(row, cl[k])], 1])
                labs[k] = n_tracks
                n_tracks += 1
            if labs[k] >= 0:
                here.append(labs[k])
        if len(here) > 1:
            here.sort()
            for a in range(len(here)):
                for bb in range(a + 1, len(here)):
                    tg = together.setdefault((here[a], here[bb]), [row, 0])
                    tg[0], tg[1] = row, tg[1] + 1
    lab = np.asarray(labs, np.int64)
    # overlapping profiles: a point of another long track in the same row closer than the separation
    long_ = np.zeros(n, bool)
    ok = lab >= 0
    long_[ok] = np.bincount(lab[ok])[lab[ok]] >= p.min_segment
    same = (t[1:] == t[:-1]) & (np.abs(np.diff(c)) < sep) & long_[1:] & long_[:-1] & (lab[1:] != lab[:-1])
    close = np.r_[same, False] | np.r_[False, same]
    lab[close] = -1
    return lab


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
    t0 = t[np.maximum.accumulate(np.where(seg_first, np.arange(len(t)), 0))]  # each run's first t
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
    multi = int(p.lines_per_family) > 1
    # adaptive floor from a robust (median/MAD) noise estimate on ~5k samples of the response
    sub = inner[:: max(1, h // 60), :: max(1, (w - 2 * b) // 80)]
    med = float(np.median(sub))
    noise = 1.4826 * float(np.median(np.abs(sub - med)))
    thr = max(p.min_response, med + p.noise_k * noise)
    if multi:
        t, j = _row_candidates(inner, thr, int(p.lines_per_family), _nms_radius(p))
        j = j + b
    else:
        j = np.argmax(inner, axis=1)
        rows = np.arange(h)
        r = inner[rows, j]
        j = j + b
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
    seg = np.zeros(0, np.int64)
    if multi:
        # several candidates per row: link them into tracks, drop where two lines merge
        seg = _link_tracks(t, c, p)
        keep = seg >= 0
        t, c, con, wid, seg = t[keep], c[keep], con[keep], wid[keep], seg[keep]
        order = np.lexsort((t, seg))  # contiguous runs, one per track, along t
        t, c, con, wid, seg = t[order], c[order], con[order], wid[order], seg[order]
    else:
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
            if multi:
                seg = seg[mask]
        seg = _split(t, c, seg, p.max_step, p.max_gap) if multi else _link(t, c, p.max_step, p.max_gap)
        long_ = _seg_sizes(seg) >= p.min_segment
        t, c, con, wid, seg, slope = t[long_], c[long_], con[long_], wid[long_], seg[long_], slope[long_]
        if len(seg):
            seg = np.unique(seg, return_inverse=True)[1]
        return len(t) > 0

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


def _chains(t: np.ndarray, c: np.ndarray, slope: np.ndarray, seg: np.ndarray, max_bridge: float) -> list[np.ndarray]:
    """Point indices of each line of one family (several lines): segments chained across gaps.

    Segments are contiguous runs sorted by ``t``. A segment's end is joined to the start of the
    segment that continues it best, at most ``max_bridge`` further along ``t`` and within
    ``max(3, 0.05 * gap)`` px of the end's straight continuation (each start is joined once)."""
    if len(seg) == 0:
        return []
    first = np.r_[0, np.flatnonzero(seg[1:] != seg[:-1]) + 1]
    last = np.r_[first[1:] - 1, len(seg) - 1]
    n = len(first)
    links = []
    for a in range(n):
        ea = last[a]
        for b in range(n):
            sb = first[b]
            dt = t[sb] - t[ea]
            if b == a or dt <= 0 or dt > max_bridge:
                continue
            miss = abs(c[sb] - (c[ea] + slope[ea] * dt))
            if miss <= max(3.0, 0.05 * dt):
                links.append((miss, a, b))
    nxt = [-1] * n
    has_prev = [False] * n
    for _, a, b in sorted(links):
        if nxt[a] < 0 and not has_prev[b]:
            nxt[a], has_prev[b] = b, True
    out = []
    for a in range(n):
        if has_prev[a]:
            continue
        idx, s = [], a
        while s >= 0:
            idx.append(np.arange(first[s], last[s] + 1))
            s = nxt[s]
        out.append(np.concatenate(idx))
    return out


def _near_other_lines(pts: np.ndarray, dirs: np.ndarray, other: np.ndarray, other_dirs: np.ndarray,
                      other_slope: np.ndarray, other_seg: np.ndarray, other_vertical: bool, radius: float) -> np.ndarray:
    """:func:`_near_other_line` for a family holding several lines: line by line (:func:`_chains`)."""
    near = np.zeros(len(pts), bool)
    if len(pts) == 0 or len(other) == 0:
        return near
    ot = other[:, 1] if other_vertical else other[:, 0]
    oc = other[:, 0] if other_vertical else other[:, 1]
    for idx in _chains(ot, oc, other_slope, other_seg, 4.0 * radius):
        near |= _near_other_line(pts, dirs, other[idx], other_dirs[idx], other_vertical, radius)
    return near


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
            if int(p.lines_per_family) > 1:
                keep_v &= ~_near_other_lines(vert, dir_v, horz, dir_h, slh, segh, other_vertical=False, radius=p.cross_radius)
                keep_h &= ~_near_other_lines(horz, dir_h, vert, dir_v, slv, segv, other_vertical=True, radius=p.cross_radius)
            else:
                keep_v &= ~_near_other_line(vert, dir_v, horz, dir_h, other_vertical=False, radius=p.cross_radius)
                keep_h &= ~_near_other_line(horz, dir_h, vert, dir_v, other_vertical=True, radius=p.cross_radius)
        vert, sv, segv = _revalidate(vert, sv, keep_v, p, vertical=True, seg=segv)
        horz, sh, segh = _revalidate(horz, sh, keep_h, p, vertical=False, seg=segh)
    log.debug("extract_laser_lines: %d vertical, %d horizontal points", len(vert), len(horz))
    return LaserLines(vert, horz, sv, sh, segv.astype(np.int32), segh.astype(np.int32))


def _revalidate(pts: np.ndarray, strength: np.ndarray, keep: np.ndarray, p: LaserParams, vertical: bool,
                seg: np.ndarray | None = None):
    """Apply ``keep``, re-link and drop segments that became shorter than ``min_segment``.

    With several lines per family the segments ``seg`` are only split, never joined."""
    pts, strength = pts[keep], strength[keep]
    if len(pts) == 0:
        return pts, strength, np.zeros(0, np.int64)
    t, c = (pts[:, 1], pts[:, 0]) if vertical else (pts[:, 0], pts[:, 1])
    if int(p.lines_per_family) > 1 and seg is not None:
        seg = _split(t, c, np.asarray(seg)[keep], p.max_step, p.max_gap)
    else:
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
    laser line extracted from the right rectified image (of its family, with several lasers per
    family). A point passes when a ridge point lies within ``tol_px``.
    """
    return np.isfinite(stereo_residual(points_c, right_uv, K, baseline_mm, tol_px))


def stereo_residual(points_c: np.ndarray, right_uv: np.ndarray, K: np.ndarray, baseline_mm: float,
                    tol_px: float = 1.5) -> np.ndarray:
    """Distance (px) from each point's predicted right-image position to the nearest right ridge
    point, ``inf`` beyond ``tol_px`` (or for invalid points): what :func:`stereo_check` tests."""
    P = np.asarray(points_c, dtype=np.float64).reshape(-1, 3)
    out = np.full(len(P), np.inf)
    right_uv = np.asarray(right_uv, dtype=np.float64).reshape(-1, 2)
    if len(P) == 0 or len(right_uv) == 0:
        return out
    finite = np.all(np.isfinite(P), axis=1) & (P[:, 2] > 0)
    if not finite.any():
        return out
    Q = P[finite]
    f, cx, cy, s = K[0, 0], K[0, 2], K[1, 2], K[0, 1]
    x, y = Q[:, 0] / Q[:, 2], Q[:, 1] / Q[:, 2]
    v = K[1, 1] * y + cy
    u_r = f * x + s * y + cx - f * float(baseline_mm) / Q[:, 2]
    d, _ = cKDTree(right_uv).query(np.stack([u_r, v], axis=1), k=1, distance_upper_bound=float(tol_px))
    out[np.nonzero(finite)[0]] = d
    return out


#: the dense depth's standard deviation when no sigma map is given, as a share of the depth
#: (about the median of the calibrated sigma at full resolution: 0.55-0.7 mm at 250-300 mm)
DEPTH_SIGMA_REL = 0.0025


def depth_check(points_c: np.ndarray, depth: np.ndarray, K: np.ndarray, sigma: np.ndarray | None = None,
                k: float = 4.0, min_mm: float = 1.0, offset_px: int | None = None) -> np.ndarray:
    """Boolean mask of laser points (frame C, (N,3)) that agree with the dense depth around their pixel.

    The dense depth is read at the point's pixel and ``offset_px`` to each side (default 6 px at
    1280 px width): on the line itself it can be wrong, because a horizontal line runs along
    the epipolar rows and gives the matcher nothing to hold on to there. A point passes when one
    of these samples is within ``max(min_mm, k * sigma)`` (``sigma`` the dense depth's standard
    deviation there, or :data:`DEPTH_SIGMA_REL` times the depth), and also where the dense depth
    has no value (nothing to disagree with). Reflections and glints are triangulated on the
    laser plane at the wrong depth, millimetres to centimetres off the dense surface all round.
    ``depth`` (mm, 0 = none) must be the image the lines were found in, so that ``K`` fits it.
    """
    P = np.asarray(points_c, dtype=np.float64).reshape(-1, 3)
    ok = np.ones(len(P), bool)
    if len(P) == 0:
        return ok
    h, w = depth.shape[:2]
    o = int(offset_px) if offset_px is not None else max(2, int(round(6.0 * w / 1280.0)))
    z = P[:, 2]
    good = np.isfinite(P).all(axis=1) & (z > 0)
    zs = np.where(good, z, 1.0)
    u = np.rint(K[0, 0] * P[:, 0] / zs + K[0, 1] * P[:, 1] / zs + K[0, 2]).astype(np.int64)
    v = np.rint(K[1, 1] * P[:, 1] / zs + K[1, 2]).astype(np.int64)
    seen = np.zeros(len(P), bool)  # some dense depth around the point
    agree = np.zeros(len(P), bool)
    for du, dv in ((0, 0), (0, -o), (0, o), (-o, 0), (o, 0)):
        uu, vv = u + du, v + dv
        idx = np.flatnonzero(good & (uu >= 0) & (uu < w) & (vv >= 0) & (vv < h))
        d = depth[vv[idx], uu[idx]].astype(np.float64)
        has = d > 0
        s = sigma[vv[idx], uu[idx]].astype(np.float64) if sigma is not None else DEPTH_SIGMA_REL * d
        tol = np.maximum(float(min_mm), float(k) * s)
        seen[idx[has]] = True
        agree[idx[has]] |= np.abs(z[idx[has]] - d[has]) <= tol[has]
    ok[seen] = agree[seen]
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
    depth: np.ndarray | None = None,
    depth_sigma: np.ndarray | None = None,
    rejected: dict[str, int] | None = None,
) -> dict[str, np.ndarray]:
    """Validated laser points (mm, frame C) per calibrated plane: ``{name: (N,3)}``.

    Extracts the lines in the left image (unless ``lines_left`` is given), triangulates them on
    their planes, keeps depths within ``[min_depth_mm, max_depth_mm]`` and, if
    ``params.stereo_check`` is set and the right image (or ``lines_right``) is available,
    applies :func:`stereo_check`. With a dense ``depth`` map of the left image (and its
    ``depth_sigma``) and ``params.depth_check``, also :func:`depth_check`; ``rejected`` (when
    given) counts the points it drops per plane. Plane names that are not laser lines (see
    :func:`line_family`) are ignored.

    Several planes of one family (four lasers): each point of the family is triangulated on
    every plane of that family and goes to the plane its checks pass on. Several pass (near
    where two sheets intersect, where their 3D points agree anyway): the one with the smallest
    stereo residual (:func:`stereo_residual`), else the one closest to the dense depth. None
    passes, or several pass with 3D points further apart than ``family_agree_mm`` (the checks
    cannot tell the lines apart there: a horizontal line runs along the epipolar rows, so
    without the dense depth its stereo check hardly depends on the plane): dropped. A point
    the depth check drops on every plane it otherwise passes on counts as rejected on the plane
    with the best stereo residual there.
    """
    p = params if params is not None else LaserParams()
    if lines_left is None:
        lines_left = extract_laser_lines(left_r, p)
    do_check = p.stereo_check and (lines_right is not None or right_r is not None)
    if do_check and lines_right is None:
        lines_right = extract_laser_lines(right_r, p)
    use_depth = (p.depth_check and depth is not None and left_r is not None
                 and depth.shape[:2] == np.asarray(left_r).shape[:2])
    families: dict[str, list[str]] = {}
    for name in planes:
        if not is_line_name(name):
            log.warning("laser_points: ignoring plane %r (a laser line's name starts with one of %s)", name, LINE_NAMES)
            continue
        families.setdefault(line_family(name), []).append(name)
    multi: dict[str, np.ndarray] = {}
    for fam, names in families.items():
        if len(names) > 1:
            multi.update(_assign_planes(lines_left.get(fam), [planes[n] for n in names], names, K, baseline, p,
                                        lines_right.get(fam) if do_check else None,
                                        depth if use_depth else None, depth_sigma, rejected))
    out: dict[str, np.ndarray] = {}
    for name, plane in planes.items():
        if not is_line_name(name):
            continue
        if name in multi:
            out[name] = multi[name]
            continue
        P = triangulate_plane(lines_left.get(name), K, plane)
        z = P[:, 2]
        ok = np.isfinite(z) & (z >= p.min_depth_mm) & (z <= p.max_depth_mm)
        if do_check and ok.any():
            ok &= stereo_check(P, lines_right.get(name), K, baseline, p.stereo_tol_px)
        if use_depth and ok.any():
            agree = depth_check(P[ok], depth, K, depth_sigma, p.depth_check_k, p.depth_check_min_mm)
            if rejected is not None:
                rejected[name] = rejected.get(name, 0) + int((~agree).sum())
            keep = np.flatnonzero(ok)
            ok[keep[~agree]] = False
        out[name] = P[ok]
    return out


def _assign_planes(uv: np.ndarray, planes: list[LaserPlane], names: list[str], K: np.ndarray, baseline: float,
                   p: LaserParams, right_uv: np.ndarray | None, depth: np.ndarray | None,
                   depth_sigma: np.ndarray | None, rejected: dict[str, int] | None) -> dict[str, np.ndarray]:
    """The points of one family split between its planes (see :func:`laser_points`): ``{name: (N,3)}``."""
    P, label, lost, blame = _plane_labels(uv, planes, K, baseline, p, right_uv, depth, depth_sigma)
    if rejected is not None and depth is not None:
        for k, name in enumerate(names):
            rejected[name] = rejected.get(name, 0) + int((lost & (blame == k)).sum())
    return {name: P[k][label == k] for k, name in enumerate(names)}


def assign_planes(uv: np.ndarray, planes: list[LaserPlane], K: np.ndarray, baseline: float,
                  params: LaserParams | None = None, right_uv: np.ndarray | None = None,
                  depth: np.ndarray | None = None, depth_sigma: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Which of the planes ``planes`` (one family's) each left-image point ``uv`` (N,2) lies on.

    The rule of :func:`laser_points`: ``right_uv`` are the family's right-image ridge points
    (``None``: no stereo check), ``depth`` / ``depth_sigma`` the dense depth (``None``: no depth
    check). Returns ``(P, label)``: ``P`` (M,N,3) the points triangulated on every plane, ``label``
    (N,) the index of the plane each point goes to, -1 for none."""
    P, label, _, _ = _plane_labels(np.asarray(uv, dtype=np.float64).reshape(-1, 2), list(planes), K, baseline,
                                   params if params is not None else LaserParams(), right_uv, depth, depth_sigma)
    return P, label


def _plane_labels(uv: np.ndarray, planes: list[LaserPlane], K: np.ndarray, baseline: float, p: LaserParams,
                  right_uv: np.ndarray | None, depth: np.ndarray | None, depth_sigma: np.ndarray | None):
    """``(P, label, lost, blame)``: :func:`assign_planes`, and the points the depth check dropped on
    every plane they otherwise passed on (``lost``) with the plane of the best stereo residual there."""
    n, m = len(uv), len(planes)
    P = np.stack([triangulate_plane(uv, K, pl) for pl in planes]) if n else np.zeros((m, 0, 3))
    z = P[:, :, 2]
    ok = np.isfinite(z) & (z >= p.min_depth_mm) & (z <= p.max_depth_mm)
    in_range = ok.copy()
    resid = np.zeros((m, n))
    if right_uv is not None:
        for k in range(m):
            resid[k] = stereo_residual(P[k], right_uv, K, baseline, p.stereo_tol_px) if ok[k].any() else np.inf
        ok &= np.isfinite(resid)
    passed = ok.copy()
    dz = np.zeros((m, n))
    if depth is not None:
        h, w = depth.shape[:2]
        for k in range(m):
            if not ok[k].any():
                continue
            i = np.flatnonzero(ok[k])
            passed[k, i] = depth_check(P[k, i], depth, K, depth_sigma, p.depth_check_k, p.depth_check_min_mm)
            Q = P[k, i]
            u = np.clip(np.rint(K[0, 0] * Q[:, 0] / Q[:, 2] + K[0, 1] * Q[:, 1] / Q[:, 2] + K[0, 2]).astype(np.int64), 0, w - 1)
            v = np.clip(np.rint(K[1, 1] * Q[:, 1] / Q[:, 2] + K[1, 2]).astype(np.int64), 0, h - 1)
            d = depth[v, u].astype(np.float64)
            dz[k, i] = np.where(d > 0, np.abs(Q[:, 2] - d), 0.0)
    if depth is not None and m > 1 and n:
        # the stereo check of a horizontal line hardly depends on the depth, so with several
        # planes the dense depth must confirm the plane: some dense depth around the point
        # (without it the depth check passes anything), and where the point's own pixel has dense
        # depth, the chosen plane's point is the one closest to it
        h, w = depth.shape[:2]
        ui = np.clip(np.rint(uv[:, 0]).astype(np.int64), 0, w - 1)
        vi = np.clip(np.rint(uv[:, 1]).astype(np.int64), 0, h - 1)
        o = max(2, int(round(6.0 * w / 1280.0)))  # the depth check's sample offsets
        seen = np.zeros(n, bool)
        for du, dv in ((0, 0), (0, -o), (0, o), (-o, 0), (o, 0)):
            seen |= depth[np.clip(vi + dv, 0, h - 1), np.clip(ui + du, 0, w - 1)] > 0
        here = depth[vi, ui].astype(np.float64)
        gapz = np.where(in_range, np.abs(z - here[None, :]), np.inf)
        closest = np.argmin(gapz, axis=0)
        passed &= seen[None, :]
        passed &= ~((here > 0)[None, :] & (np.arange(m)[:, None] != closest[None, :]))
    n_pass = passed.sum(axis=0)
    if m > 1 and n:
        # several planes take the point: only where their 3D points agree (where the sheets
        # intersect); elsewhere the checks cannot tell which line it is (a horizontal line runs
        # along the epipolar rows, so its stereo check hardly depends on the depth)
        Pz = np.where(passed[:, :, None], P, 0.0)
        mean = Pz.sum(axis=0) / np.maximum(n_pass, 1)[:, None]
        spread = np.where(passed, np.linalg.norm(Pz - mean[None], axis=2), 0.0).max(axis=0)
        unsure = (n_pass > 1) & ~(spread <= 0.5 * float(p.family_agree_mm))
        passed[:, unsure] = False
    if right_uv is not None:
        score = np.where(passed, resid, np.inf)
    elif depth is not None:
        score = np.where(passed, dz, np.inf)
    else:  # nothing to rank by (and where several planes take a point, they agree)
        score = np.where(passed, 0.0, np.inf)
    best = np.argmin(score, axis=0) if n else np.zeros(0, np.int64)
    take = np.isfinite(score[best, np.arange(n)]) if n else np.zeros(0, bool)
    label = np.where(take, best, -1)
    lost = (ok.any(axis=0) & (n_pass == 0)) if depth is not None else np.zeros(n, bool)
    if right_uv is not None:
        blame = np.argmin(np.where(ok, resid, np.inf), axis=0) if n else np.zeros(0, np.int64)
    else:
        blame = np.argmax(ok, axis=0) if n else np.zeros(0, np.int64)
    return P, label, lost, blame


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
