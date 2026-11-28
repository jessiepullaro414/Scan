"""What light a camera frame holds, told from its pictures alone: the projector's dots, the laser lines,
both, a mix of the two or none (the kinds of :data:`ohs.io.recording.FRAME_KINDS`).

The scanner can light its frames in turn, a frame with the dots only and then one with the laser lines
only. The emitters are switched on their own clock with no wire to the camera, so the PC can not know
which light a frame got, and a frame whose exposure the switch fell inside holds some of each (a mix).
:func:`frame_kind` tells it from the frame's pictures; :class:`KindSteadier` (optional) uses the frames
before to steady the answer.

How (per picture, on a copy shrunk to ``work_width`` pixels, so 640 and 1280 px pictures look alike; ``laser.py``'s
line finding is not used: it is built to place lines finely, this only has to tell whether there are any):

1. **Height and contrast.** A dot or a laser line is brighter than the pixels ``side_px`` to both of
   its sides, across it or along it; the edge of a print (an AprilTag sticker) or a broad bright area
   is not (one of its sides is as bright). So each pixel's *side level* is the brighter of its two
   neighbours ``side_px`` away, taken in the direction (across or along) where that is lower, and its
   *height* ``d`` is how far it stands above that level (0 where it does not). Its *contrast* is
   ``d / max(bg, floor_dn)``, ``bg`` the side level's 9 x 9 mean: the extra light on it in units of the
   room's light on the part, the same for a dark or a bright part and for a short or a long exposure
   (it is a ratio). The part's own texture changes the pixel and its sides alike and gives little
   contrast.
2. **Bright marks.** Pixels with contrast above ``mark_contrast`` are grouped into connected marks.
   Two dots never join: the pixels between them have dots at their sides and so no height.
3. **Laser lines** are the marks at least ``line_min_px`` long (the longer side of their bounding
   box) and thin (area at most ``line_max_width`` times their length) of which at least half the
   pixels have contrast above ``line_contrast``. The laser reading is the 75th percentile of the
   contrast on them, if their total length is at least ``lines_min_len`` of the picture's height
   (shorter: scaled down by the part found).
4. **Dots** are the local maxima of the height off those lines that stand above the picture's noise
   (``noise_k`` times the noise, estimated from the picture itself) with contrast above
   ``dot_contrast``. The dot reading is the 90th percentile of their contrast, if there are at least
   ``dots_min_density`` of them per thousand lit pixels (fewer: scaled down by the share found).
5. Each reading is divided by its value for full light (``dots_full`` / ``lasers_full``, from the
   simulator's scenes) after taking off what a picture without that light reads (``dots_none`` /
   ``lasers_none``): about 1 for full light, about 0 for none, in between for a share of the
   exposure. These are :attr:`FrameLight.dots` and :attr:`FrameLight.lasers`.
6. The kind: a light is present when its number is at least ``present_dots`` / ``present_lasers``;
   ``both`` when both are present at ``full`` or more and in proportion (``both_ratio``), ``mixed``
   when both are present otherwise.

The left picture is measured first; the right one too when the left one's numbers are near a threshold
(``second_eye_margin``; always with ``both_eyes``), and the two pictures' readings are averaged. Time,
measured on the owner's mini PC (Ryzen 7 7735U) on the processor: about 2.5 ms per picture with OpenCV
on one thread, 3.5-4 ms per frame on average (the right picture is needed for some frames); with
OpenCV's default 16 threads on these small pictures, about twice that.

Limits (measured in the simulator only): a light at a tenth of the exposure is not found (a 0.1 / 0.9
mix reads as a laser frame, a 0.9 / 0.1 one as a dot frame); an equal mix (0.5 / 0.5) can read as
``both``; the laser lines' contrast saturates, so a frame with the lasers at 0.3 and the dots at 0.7
reads as ``both`` on some scenes (the sphere on its plate); the full-light values depend on the room's
light against the emitters' (the simulator's ambient light), so on the real scanner they are to be
checked, and :class:`KindSteadier` learns them from the frames. A part with strong fine texture (a
print or paint speckle of +-35 % at sub-millimetre scale) can hide a weak laser line (0.3 of the
exposure).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace

import cv2
import numpy as np

__all__ = ["FrameKindParams", "FrameLight", "frame_kind", "measure_light", "KindSteadier"]

_K3 = np.ones((3, 3), np.uint8)


@dataclass(frozen=True)
class FrameKindParams:
    """Settings of :func:`frame_kind` (module docstring, the steps). Sizes in pixels of the shrunk copy."""

    work_width: int = 640  # pictures wider than this are shrunk to it first
    floor_dn: float = 6.0  # the smallest background a contrast is taken against (DN)
    side_px: int = 2  # step 1: a dot or a line is brighter than the pixels this far to both sides
    mark_contrast: float = 0.4  # step 2
    line_min_px: int = 16  # step 3: a mark this long (its bounding box's longer side) or longer
    line_max_width: float = 5.0  # step 3: area / length
    line_contrast: float = 0.6  # step 3: half a line's pixels above this
    lines_min_len: float = 0.15  # step 3: total line length, share of the picture's height
    noise_k: float = 4.0  # step 4
    dot_contrast: float = 0.25  # step 4
    dots_min_density: float = 3.0  # step 4: dots per thousand lit pixels
    dots_none: float = 0.0  # step 5: the dot reading of a picture without dots
    dots_full: float = 1.9  # step 5: ... with all the dots
    lasers_none: float = 0.0
    lasers_full: float = 1.6
    present_dots: float = 0.15  # step 6: the dots are present from this on
    present_lasers: float = 0.15  # step 6: the laser lines are present from this on
    full: float = 0.64  # step 6: both lights at this or more ...
    both_ratio: tuple[float, float] = (0.6, 1.6)  # ... and dots / lasers within this: ``both``
    both_eyes: bool = False  # measure the right picture always (True) or only when the left one is unsure
    second_eye_margin: float = 0.3  # the left picture is unsure within this share of a threshold


@dataclass(frozen=True)
class FrameLight:
    """What light a frame holds: ``kind`` (``both``, ``dots``, ``lasers``, ``mixed`` or ``dark``) and how strongly
    the dots and the laser lines are present (about 0 = none, 1 = for the whole exposure)."""

    kind: str
    dots: float
    lasers: float


def _gray(img: np.ndarray, name: str) -> np.ndarray:
    a = np.asarray(img)
    if a.dtype != np.uint8 or a.ndim != 2:
        raise ValueError(f"{name} must be a uint8 grayscale picture (h, w), got {a.dtype} {a.shape}")
    return a


def measure_light(img: np.ndarray, params: FrameKindParams | None = None) -> tuple[float, float]:
    """``(dot reading, laser reading)`` of one uint8 grayscale picture (module docstring, steps 1-4): the
    contrasts before step 5's scaling."""
    p = params or FrameKindParams()
    img = _gray(img, "img")
    h, w = img.shape
    if w > p.work_width:
        img = cv2.resize(img, (p.work_width, max(1, round(h * p.work_width / w))), interpolation=cv2.INTER_AREA)
    h, w = img.shape
    f8 = cv2.GaussianBlur(img, (3, 3), 0.6)
    r = int(p.side_px)
    kh = np.zeros((1, 2 * r + 1), np.uint8)
    kh[0, [0, -1]] = 1
    # the brighter of the two pixels r to either side, across and along: a dot or a line is brighter than both
    # of them in at least one of the two directions; an edge of a print or a broad bright area is not
    side = cv2.min(cv2.dilate(f8, kh), cv2.dilate(f8, kh.T))
    d8 = cv2.subtract(f8, side)  # how far a pixel stands above its sides (0 where it does not)
    # what a contrast is taken against, at least floor_dn; the contrast thresholds as pictures of DN (uint8
    # compares are several times cheaper than float ones; the contrast itself is worked out only where needed)
    bg = cv2.blur(side, (9, 9))
    den = cv2.max(bg, round(p.floor_dn))

    def above(t: float) -> np.ndarray:
        return cv2.compare(d8, cv2.convertScaleAbs(den, alpha=t), cv2.CMP_GT)

    # step 2: bright marks
    marks = above(p.mark_contrast)
    n, lab, st, _ = cv2.connectedComponentsWithStats(marks, connectivity=8)
    length = np.maximum(st[:, 2], st[:, 3])
    cand = (length >= p.line_min_px) & (st[:, 4] <= p.line_max_width * length)
    cand[0] = False
    # step 3: laser lines
    lasers = 0.0
    on_line = None
    if cand.any():
        line = cand & (np.bincount(lab[cv2.bitwise_and(above(p.line_contrast), marks) > 0], minlength=n)
                       >= 0.5 * length)
        if line.any():
            lut = np.zeros(n, np.uint8)
            lut[line] = 255
            on_line = lut[lab]
            sel = on_line > 0
            line_len = int(length[line].sum())
            c_line = d8[sel].astype(np.float32) / den[sel]
            lasers = float(np.percentile(c_line, 75)) * min(1.0, line_len / (p.lines_min_len * h))
    # step 4: dots
    sub = img[::4, ::4].astype(np.float32)  # the noise from a sixteenth of the pixels: plenty, and cheap
    noise = max(1.4826 * float(np.median(np.abs(sub - cv2.blur(sub, (3, 3))))), 0.5)
    peak = cv2.bitwise_and(cv2.compare(d8, cv2.dilate(d8, _K3), cv2.CMP_GE), above(p.dot_contrast))
    peak = cv2.bitwise_and(peak, cv2.compare(d8, p.noise_k * noise, cv2.CMP_GT))
    if on_line is not None:
        peak = cv2.bitwise_and(peak, cv2.bitwise_not(on_line))
    sel = peak > 0
    cp = d8[sel].astype(np.float32) / den[sel]
    lit = max(cv2.countNonZero(cv2.compare(bg, p.floor_dn * 0.5, cv2.CMP_GT)), 1)  # pixels under some light
    dots = 0.0
    if cp.size >= 10:
        dots = float(np.percentile(cp, 90)) * min(1.0, 1000.0 * cp.size / lit / p.dots_min_density)
    return dots, lasers


def _scale(v: float, none: float, full: float) -> float:
    return max(0.0, (v - none) / max(full - none, 1e-6))


def _kind(dots: float, lasers: float, p: FrameKindParams) -> str:
    d_on, l_on = dots >= p.present_dots, lasers >= p.present_lasers
    if d_on and l_on:
        lo, hi = p.both_ratio
        return "both" if dots >= p.full and lasers >= p.full and lo <= dots / lasers <= hi else "mixed"
    return "dots" if d_on else "lasers" if l_on else "dark"


def frame_kind(left: np.ndarray, right: np.ndarray | None = None, params: FrameKindParams | None = None) -> FrameLight:
    """What light a frame holds, from its left and right uint8 grayscale pictures (``StereoFrame.left`` /
    ``.right``; ``right`` may be None). Needs no other frame. Module docstring: how."""
    p = params or FrameKindParams()
    left = _gray(left, "left")
    right = None if right is None else _gray(right, "right")
    readings = [measure_light(left, p)]
    if right is not None and (p.both_eyes or _unsure(*_scaled(readings[0], p), p)):
        readings.append(measure_light(right, p))
    dots, lasers = _scaled(tuple(np.mean(readings, axis=0)), p)
    return FrameLight(_kind(dots, lasers, p), dots, lasers)


def _scaled(reading: tuple[float, float], p: FrameKindParams) -> tuple[float, float]:
    return _scale(float(reading[0]), p.dots_none, p.dots_full), _scale(float(reading[1]), p.lasers_none, p.lasers_full)


def _unsure(dots: float, lasers: float, p: FrameKindParams) -> bool:
    """A reading near a threshold of :func:`_kind` (within ``second_eye_margin`` of it, relatively)."""
    m = p.second_eye_margin
    near = [(dots, p.present_dots), (lasers, p.present_lasers)]
    if dots >= p.present_dots * (1 - m) and lasers >= p.present_lasers * (1 - m):
        near += [(dots, p.full), (lasers, p.full)]
        if lasers > 0:
            near += [(dots / lasers, t) for t in p.both_ratio]
    return any(abs(v - t) < m * t for v, t in near)


class KindSteadier:
    """Steadies :func:`frame_kind`'s answers over a short history, for live frames.

    It learns the full-light readings from the frames themselves: the median ``dots`` of recent frames
    with the dots alone and the median ``lasers`` of recent frames with the lasers alone (or of ``both``
    frames). A later frame's two numbers are divided by them before its kind is decided again, so a room
    brighter or darker than the simulator's (which moves the full-light readings) does not turn every
    ``both`` frame into ``mixed``. Absent lights stay absent and a mix stays a mix: it never invents a kind
    from the frames' order. :meth:`expected` says which kind the alternation of the last frames predicts.
    """

    def __init__(self, params: FrameKindParams | None = None, history: int = 16) -> None:
        self.params = params or FrameKindParams()
        self._dots: deque[float] = deque(maxlen=history)
        self._lasers: deque[float] = deque(maxlen=history)
        self._kinds: deque[str] = deque(maxlen=4)

    def update(self, light: FrameLight) -> FrameLight:
        """The frame's light with its kind decided against the learnt full-light readings."""
        p = self.params
        d_ref = float(np.median(self._dots)) if len(self._dots) >= 3 else 1.0
        l_ref = float(np.median(self._lasers)) if len(self._lasers) >= 3 else 1.0
        dots = light.dots / max(d_ref, 0.3)
        lasers = light.lasers / max(l_ref, 0.3)
        out = FrameLight(_kind(dots, lasers, p), dots, lasers) if light.kind in ("both", "mixed") else \
            replace(light, dots=dots, lasers=lasers)
        if light.kind in ("dots", "both"):
            self._dots.append(light.dots)
        if light.kind in ("lasers", "both"):
            self._lasers.append(light.lasers)
        self._kinds.append(out.kind)
        return out

    def expected(self) -> str | None:
        """``dots`` or ``lasers`` when the last frames came in turn (dots, lasers, dots, ...), else None."""
        k = list(self._kinds)
        if len(k) >= 2 and all(x in ("dots", "lasers") for x in k) and all(a != b for a, b in zip(k, k[1:])):
            return "lasers" if k[-1] == "dots" else "dots"
        return None
