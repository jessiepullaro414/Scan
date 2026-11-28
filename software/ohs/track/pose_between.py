"""The pose of a laser frame, placed between the dot frames beside it in time (frames in turn).

When the dots and the laser lines are lit in turn, a frame with the laser lines only (a *laser frame*) has no stereo
depth of its own, so it cannot be tracked. Its pose is taken from the tracked dot frames before and after it
(:mod:`ohs.app.process`, "Frames in turn"):

* **cubic** (``"cubic"``): through the four tracked dot frames around it, two before and two after, when they follow
  one another with nothing between them (no cut, no frame lost) and are spaced evenly enough: the longest of their
  three gaps at most ``even_ratio`` (2) times the shortest. A hand's path bends; a cubic follows the bend where a
  straight line cuts the corner.
* **straight** (``"straight"``) between the two beside it otherwise: the position on the line between theirs, the
  rotation turned the same share of the way (the shortest turn).

Both are the same polynomial in time, through two or four poses: the positions as they are, the rotations as the
turn from the dot frame just before (``log(R_a^T R_k)``, a small vector for a hand's motion), and the polynomial's
turn put back on ``R_a``. Between two poses that is exactly the straight line and the even turn.

On the simulator's hand paths (fins, bracket, sphere, thin plate; three seeds; the dot frames at their true poses;
``tests/test_track_pose_between.py``) at 30 dot frames a second the straight line put the camera a median of 0.017 to
0.027 mm and 0.008 deg off its true pose, the cubic 0.008 to 0.012 mm and 0.0045 deg; at 50 dot frames a second
0.006 to 0.010 mm against 0.0013 to 0.0019 mm. (The study before, on the scorecard's recordings, found 0.07 to 0.08 mm
for the straight line at 30 and the cubic about half of that: VALIDATION.md "Dot frames and laser frames in turn".)

:func:`place_laser_frames` decides for every laser frame whether it can be placed at all: the dot frames on both
sides must have been processed and tracked and nothing may separate them (a cut interval, a frame lost between them,
or a gap longer than ``max_gap_s``). Otherwise the frame is left out, with the reason.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

from ohs.geometry import so3_exp, so3_log

__all__ = ["PlacingParams", "Placed", "LEFT_OUT_REASONS", "pose_between", "place_laser_frames"]

#: why a laser frame is left out: no dot frame on one side (the scan's ends), a neighbour not tracked, a cut between
#: its neighbours, or a gap between them longer than ``max_gap_s``
LEFT_OUT_REASONS = ("at_the_ends", "untracked", "cut", "gap")


@dataclass(frozen=True)
class PlacingParams:
    """How a laser frame's pose is placed (module docstring)."""

    cubic: bool = True  # False: always straight between the two beside it
    even_ratio: float = 2.0  # the cubic's four dot frames: longest gap / shortest gap at most this


@dataclass
class Placed:
    """One laser frame: its pose (``T_wc``) and how it was placed, or None and why it was left out."""

    index: int
    t: float
    T_wc: np.ndarray | None
    method: str | None  # "cubic" | "straight" | None
    reason: str | None = None  # one of LEFT_OUT_REASONS when T_wc is None
    neighbours: tuple[int, ...] = ()  # the frame indices of the dot frames used


def _lagrange(ts: np.ndarray, ys: np.ndarray, t: float) -> np.ndarray:
    """The polynomial through ``(ts[k], ys[k])`` at ``t`` (``ys`` (k, d))."""
    out = np.zeros(ys.shape[1], dtype=np.float64)
    for k in range(len(ts)):
        w = 1.0
        for j in range(len(ts)):
            if j != k:
                w *= (t - ts[j]) / (ts[k] - ts[j])
        out += w * ys[k]
    return out


def pose_between(t: float, times: Sequence[float], poses: Sequence[np.ndarray], base: int | None = None) -> np.ndarray:
    """The pose at ``t`` of the polynomial in time through ``poses`` (4x4 ``T_wc``) at ``times`` (two: the straight
    line and the even turn; four: the cubic). The rotations are taken as turns from ``poses[base]`` (default: the
    last one at or before ``t``)."""
    ts = np.asarray(times, dtype=np.float64).reshape(-1)
    Ts = [np.asarray(T, dtype=np.float64).reshape(4, 4) for T in poses]
    if len(ts) != len(Ts) or len(ts) < 2:
        raise ValueError("pose_between needs two or more poses, one time each")
    if np.any(np.diff(ts) <= 0):
        raise ValueError("the poses' times must increase")
    if base is None:
        base = max(int(np.searchsorted(ts, float(t), side="right")) - 1, 0)
    R0 = Ts[base][:3, :3]
    r = np.stack([so3_log(R0.T @ T[:3, :3]) for T in Ts])
    c = np.stack([T[:3, 3] for T in Ts])
    out = np.eye(4)
    out[:3, :3] = R0 @ so3_exp(_lagrange(ts, r, float(t)))
    out[:3, 3] = _lagrange(ts, c, float(t))
    return out


def place_laser_frames(
    laser: Iterable[tuple[int, float]],
    dots: Sequence[tuple[int, float, bool, np.ndarray]],
    *,
    breaks: Iterable[int] = (),
    max_gap_s: float = float("inf"),
    params: PlacingParams | None = None,
) -> list[Placed]:
    """Place each laser frame ``(index, t)`` between the processed dot frames ``dots`` (``(index, t, tracked, T_wc)``
    in time order, as in ``trajectory.csv``).

    ``breaks``: positions ``k`` in ``dots`` where something separates ``dots[k - 1]`` from ``dots[k]`` (a cut interval
    lies between them): a laser frame between them is left out ("cut"), and a cubic does not reach across one.
    ``max_gap_s``: a laser frame whose two neighbours are farther apart than this is left out ("gap"); the cubic's
    outer gaps obey it too. Returns one :class:`Placed` per laser frame, in the order given."""
    p = params if params is not None else PlacingParams()
    dts = np.asarray([float(d[1]) for d in dots], dtype=np.float64)
    if np.any(np.diff(dts) <= 0):
        raise ValueError("the dot frames must be in time order")
    brk = {int(k) for k in breaks}
    out: list[Placed] = []
    for index, t in laser:
        t = float(t)
        kb = int(np.searchsorted(dts, t, side="right"))  # the first dot frame after t
        ka = kb - 1
        if ka < 0 or kb >= len(dots) or dts[ka] == t:
            out.append(Placed(int(index), t, None, None, "at_the_ends"))
            continue
        if kb in brk:
            out.append(Placed(int(index), t, None, None, "cut"))
            continue
        if not (dots[ka][2] and dots[kb][2]):
            out.append(Placed(int(index), t, None, None, "untracked"))
            continue
        if dts[kb] - dts[ka] > max_gap_s:
            out.append(Placed(int(index), t, None, None, "gap"))
            continue
        ks = [ka, kb]
        if p.cubic and ka - 1 >= 0 and kb + 1 < len(dots):
            four = [ka - 1, ka, kb, kb + 1]
            gaps = np.diff(dts[four])
            if (all(dots[k][2] for k in four) and ka not in brk and kb + 1 not in brk
                    and float(gaps.max()) <= max_gap_s and float(gaps.max()) <= float(p.even_ratio) * float(gaps.min())):
                ks = four
        T = pose_between(t, dts[ks], [dots[k][3] for k in ks], base=ks.index(ka))
        out.append(Placed(int(index), t, T, "cubic" if len(ks) == 4 else "straight",
                          neighbours=tuple(int(dots[k][0]) for k in ks)))
    return out
