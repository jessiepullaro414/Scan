"""Two helpers of the line extraction for several lines per family, as they were before they
were made faster (commit dfa371b). ``tests/test_vision_laser_speed.py`` checks that the faster
ones in ``ohs/vision/laser.py`` give exactly the same output. Do not optimise this file."""
from __future__ import annotations

import cv2
import numpy as np

from ohs.vision.laser import _chains, _near_other_line


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
