"""The laser frames of a recording whose frames come in turn, for the frame pass (:mod:`ohs.app.process`, "Frames in
turn"): their laser points, the check against the depth of the dot frames beside them, their placed poses and
``report["frames_in_turn"]``.

:class:`LaserFrames` is fed in recording order: :meth:`LaserFrames.laser_frame` for a frame that holds the laser
lines only (its points in its own camera frame, found by :meth:`ohs.app.pipeline.FramePipeline.laser_frame`), and
:meth:`LaserFrames.dot_frame` after each processed dot frame. When the whole pass is done, :meth:`LaserFrames.finish`
places every laser frame between the dot frames' final poses (:func:`ohs.track.pose_between.place_laser_frames`),
adds the points of the frames it could place to the scan's laser cloud and says what it did.

**The depth check.** In a frame that holds both lights, the laser stage drops the points that disagree with the
frame's own stereo depth around their pixel by more than ``max(1 mm, 4 sigma)`` (:func:`ohs.vision.laser.depth_check`):
reflections and glints, triangulated on the laser plane at the wrong depth, millimetres to centimetres off. A point
where that depth has no value passes. A laser frame has no depth of its own. What it gets instead: its points are
taken to the dot frames before and after it (at the pose straight between theirs, at their poses as tracked then)
and checked against their depth in the same way; a point that one of them has depth around and neither agrees with
is what the check would drop. That is counted (``depth_check.would_drop`` per line) and, by default, NOT applied: the
neighbours see the part from a little elsewhere, and on a thin wall's edge or a thin part their depth may hold
nothing or another surface, so the check could thin a part the mesh does not hold (a part can be a fraction of a
millimetre thick, and no default may silently remove it). ``apply_check=True``
(``ProcessParams.laser_frame_depth_check``) drops those points.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any

import numpy as np

from ohs.geometry import invert_se3, pose_error
from ohs.track.pose_between import LEFT_OUT_REASONS, PlacingParams, place_laser_frames, pose_between
from ohs.vision.laser import depth_check, depth_gap

__all__ = ["DOT_KINDS", "GAP_FACTOR", "LaserFrames"]

#: the kinds of frame that hold the dots: the frames the pass selects, tracks and fuses
DOT_KINDS = ("both", "dots")
#: a laser frame is left out when its two dot frames are farther apart than this many times the usual gap (the
#: recorded dot frames' median interval times the selection's step, or its cap when frames are picked by motion):
#: one dot frame missing (twice the usual) is still bridged, two (three times) are not
GAP_FACTOR = 2.5


@dataclass
class _Pending:
    index: int
    t: float
    pts: dict[str, np.ndarray]
    before: Any = None  # (t, T_wc, ok, depth, sigma) of the dot frame before it
    drop: dict[str, np.ndarray] | None = None  # what the depth check would drop, per line (None: not checked)


def _stats(x) -> dict:
    a = np.asarray(list(x), dtype=np.float64)
    if a.size == 0:
        return {"n": 0}
    return {"n": int(a.size), "mean": float(a.mean()), "median": float(np.median(a)),
            "p90": float(np.percentile(a, 90)), "p95": float(np.percentile(a, 95)), "max": float(a.max())}


class LaserFrames:
    """The laser frames of one pass (module docstring)."""

    def __init__(self, pipe: Any, *, apply_check: bool = False, placing: PlacingParams | None = None) -> None:
        self.pipe = pipe
        self.apply_check = bool(apply_check)
        self.placing = placing if placing is not None else PlacingParams()
        self._pending: list[_Pending] = []
        self._waiting: list[_Pending] = []  # laser frames since the last dot frame
        self._last: Any = None  # the last dot frame: (t, T_wc, ok, depth, sigma)
        self._timings: dict[str, list[float]] = {}
        self._checked = 0
        self._no_depth = 0
        self._would: Counter = Counter()

    # ------------------------------------------------------------------ during the pass
    def laser_frame(self, frame: Any) -> None:
        """A frame that holds the laser lines only: its points (camera frame), kept until :meth:`finish`."""
        r = self.pipe.laser_frame(frame)
        for k, v in r.timings_ms.items():
            self._timings.setdefault(k, []).append(float(v))
        e = _Pending(int(r.index), float(r.t), r.laser_points, before=self._last)
        self._pending.append(e)
        self._waiting.append(e)

    def dot_frame(self, res: Any) -> None:
        """After a processed dot frame (``FrameResult``): the laser frames since the one before are checked against
        the depth of both (module docstring, "The depth check")."""
        now = (float(res.t), np.asarray(res.track.T_wc, dtype=np.float64), bool(res.track.ok), res.depth, res.sigma)
        for e in self._waiting:
            self._check(e, e.before, now)
            e.before = None  # let go of that dot frame's depth: held to the end, a long scan would keep them all
        self._waiting = []
        self._last = now

    def _check(self, e: _Pending, a: Any, b: Any) -> None:
        if a is None or not (a[2] and b[2]) or a[3] is None or b[3] is None or not b[0] > a[0]:
            return  # nothing to check against (it will be left out anyway, or there is no depth)
        p = self.pipe.laser_params
        T_l = pose_between(e.t, [a[0], b[0]], [a[1], b[1]])
        e.drop = {}
        for name, P in e.pts.items():
            P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
            seen = np.zeros(len(P), bool)
            agree = np.zeros(len(P), bool)
            for t_n, T_n, _, depth, sigma in (a, b):
                M = invert_se3(T_n) @ T_l  # laser frame -> this dot frame's camera
                Q = P @ M[:3, :3].T + M[:3, 3]
                s = np.isfinite(depth_gap(Q, depth, self.pipe.K))
                ok = depth_check(Q, depth, self.pipe.K, sigma, p.depth_check_k, p.depth_check_min_mm)
                seen |= s
                agree |= ok & s
            e.drop[name] = seen & ~agree
            self._checked += len(P)
            self._no_depth += int((~seen).sum())
            self._would[name] += int(e.drop[name].sum())

    # ------------------------------------------------------------------ at the end
    def finish(self, traj_rows: list, cuts: list[tuple[float, float]], max_gap_s: float,
               gt: dict[int, np.ndarray] | None) -> tuple[list, dict]:
        """Place the laser frames between the dot frames ``traj_rows`` (``(index, t, ok, T_wc)``, the final poses),
        add the points of those placed to the laser cloud. Returns ``(rows of the used laser frames, report
        part)``."""
        dots = sorted(traj_rows, key=lambda r: float(r[1]))
        keep, last_t = [], -math.inf
        for r in dots:  # strictly increasing times (two dot frames at the same time: the first)
            if float(r[1]) > last_t:
                keep.append(r)
                last_t = float(r[1])
        dots = keep
        dt = [float(r[1]) for r in dots]
        breaks = {k for k in range(1, len(dots)) if any(a <= dt[k] and b >= dt[k - 1] for a, b in cuts)}
        placed = place_laser_frames([(e.index, e.t) for e in self._pending], dots, breaks=breaks,
                                    max_gap_s=max_gap_s, params=self.placing)
        rows: list = []
        methods: Counter = Counter()
        left: Counter = Counter()
        raw: Counter = Counter()
        n_with = 0
        dropped = 0
        for e, pl in zip(self._pending, placed):
            if pl.T_wc is None:
                left[pl.reason] += 1
                continue
            pts = e.pts
            if self.apply_check and e.drop is not None:
                pts = {name: np.asarray(P)[~e.drop[name]] for name, P in pts.items()}
                dropped += sum(int(m.sum()) for m in e.drop.values())
            n = self.pipe.add_laser_frame(pts, pl.T_wc)
            n_with += int(n > 0)
            for name, P in pts.items():  # as the laser cloud counts them (rows without NaN)
                m = int(np.isfinite(np.asarray(P, dtype=np.float64).reshape(-1, 3)).all(axis=1).sum())
                if m:
                    raw[name] += m
            methods[pl.method] += 1
            rows.append((pl.index, pl.t, True, pl.T_wc))
        part: dict[str, Any] = {
            "used": len(rows),
            "left_out": {r: left[r] for r in LEFT_OUT_REASONS if left[r]},
            "placing": {"rule": "cubic through the four tracked dot frames around it when they follow one another "
                                "and are evenly spaced, else straight between the two beside it",
                        "cubic": methods["cubic"], "straight": methods["straight"], "max_gap_s": max_gap_s,
                        "even_ratio": float(self.placing.even_ratio), "cubic_on": bool(self.placing.cubic)},
            "lasers": {
                "n_points_raw": {name: int(raw[name]) for name in sorted(raw)},
                "frames_with_points": n_with,
                "depth_check": {
                    "by": "the depth of the dot frames before and after it", "applied": self.apply_check,
                    "checked": int(self._checked), "without_depth_around": int(self._no_depth),
                    "would_drop": {name: int(self._would[name]) for name in sorted(self._would)},
                    "dropped": int(dropped)},
            },
            "timings_ms": {k: _stats(v) for k, v in self._timings.items()},
        }
        if gt:
            part["ground_truth"] = self._truth(gt, traj_rows, rows)
        return rows, part

    @staticmethod
    def _truth(gt: dict[int, np.ndarray], traj_rows: list, rows: list) -> dict:
        """The placed poses against the truth, the estimated world put on the true one at the first tracked dot frame
        (as :func:`ohs.app.process.trajectory_errors` does for the dot frames)."""
        first = next(((i, T) for i, _, ok, T in traj_rows if ok and i in gt), None)
        pairs = [(i, T) for i, _, _, T in rows if i in gt]
        if first is None or not pairs:
            return {"n": 0}
        A_inv = invert_se3(gt[first[0]] @ invert_se3(first[1]))
        err = [pose_error(A_inv @ gt[i], T) for i, T in pairs]
        return {"n": len(err), "aligned_at_index": int(first[0]),
                "rot_deg": _stats(e[0] for e in err), "trans_mm": _stats(e[1] for e in err)}
