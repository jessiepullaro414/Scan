"""The camera path's own check for a slide (ENGINE_PLAN X0a): a jump no hand makes, read from the tracked
path alone, warns in the report (it never changes the scan).

Without a GPU the 0.5 mm plate's recording slid 37 mm in its first 8 frames and the plate came out
0.11 mm thick with a third of its top, without a word. The path itself shows it: a velocity that
jumps and jumps back within a fraction of a second, which a smooth quadratic over 0.8 s cannot follow.
"""
from __future__ import annotations

import numpy as np
import pytest

from ohs import geometry as G
from ohs.track.path_check import PATH_CHECK_LIMIT_MM, PATH_CHECK_WINDOW_S, check_path


def hand_path(n: int = 60, fps: float = 30.0, seed: int = 0, speed=(-30.0, 30.0, -6.0), tremor: float = 0.08,
              noise: float = 0.3):
    """A smooth hand path (mm, s): a slow sweep with a gentle turn, a tremor and the tracker's frame-to-frame
    noise; rows as ``process_recording`` writes them: (index, t, ok, T_wc)."""
    rng = np.random.default_rng(seed)
    t = 100.0 + np.arange(n) / fps
    s = t - t[0]
    p = np.outer(s, speed) + np.outer(s * s, (8.0, -5.0, 2.0)) + tremor * np.sin(np.outer(s, (37.0, 41.0, 29.0)))
    p += rng.normal(0.0, noise, p.shape)
    rows = []
    for i in range(n):
        R = G.so3_exp(np.array([0.0, 0.0, 0.2 * s[i]]))
        rows.append((i, float(t[i]), True, G.make_se3(R, p[i])))
    return rows


def slid(rows, frames=range(1, 8), step=(-6.0, -1.0, 0.0)):
    """The X0a slide: a wrong velocity carried for a few frames, then the path goes on (offset)."""
    out, off = [], np.zeros(3)
    for i, t, ok, T in rows:
        if i in frames:
            off = off + np.asarray(step)
        T2 = T.copy()
        T2[:3, 3] = T[:3, 3] + off
        out.append((i, t, ok, T2))
    return out


@pytest.mark.parametrize("fps,n", [(30.0, 60), (10.0, 40), (60.0, 120)])
def test_a_smooth_hand_path_passes(fps, n):
    for seed in range(5):
        c = check_path(hand_path(n=n, fps=fps, seed=seed))
        assert c["state"] == "ok", c
        assert c["jump_mm"] < 2.0
        assert c["warning"] is None
        assert c["window_s"] == PATH_CHECK_WINDOW_S and c["limit_mm"] == PATH_CHECK_LIMIT_MM


def test_the_plate_slide_warns_where_it_happened():
    c = check_path(slid(hand_path()))
    assert c["state"] == "warn", c
    assert c["jump_mm"] > PATH_CHECK_LIMIT_MM
    assert 0 <= c["frame"] <= 10
    assert "slid" in c["warning"] and "thin" in c["warning"]


def test_a_slide_later_in_the_scan_warns_too_and_a_small_wobble_does_not():
    rows = hand_path(n=90)
    assert check_path(slid(rows, frames=range(40, 46), step=(0.0, 3.0, 0.0)))["state"] == "warn"
    assert check_path(slid(rows, frames=range(40, 42), step=(0.0, 0.5, 0.0)))["state"] == "ok"


def test_lost_frames_are_left_out_and_gaps_are_not_bridged():
    rows = hand_path(n=60)
    # a lost frame keeps the last good pose: it is not part of the path
    lost = [(i, t, i != 20, rows[19][3] if i == 20 else T) for i, t, ok, T in rows]
    assert check_path(lost)["state"] == "ok"
    # a cut (the live rewind): the path goes on 1 s after the last frame, from somewhere else; no window spans the gap
    later = [(i + 100, t + 3.0, ok, G.make_se3(T[:3, :3], T[:3, 3] + [40.0, 0.0, 0.0])) for i, t, ok, T in rows]
    assert check_path(rows + later)["state"] == "ok"


def test_too_short_a_path_is_not_judged():
    c = check_path(hand_path(n=8))
    assert c["state"] is None and c["jump_mm"] is None and c["warning"] is None
    assert check_path([])["state"] is None


def test_a_path_without_times_is_not_judged():
    rows = [(i, float("nan"), ok, T) for i, t, ok, T in hand_path()]
    assert check_path(rows)["state"] is None


def test_the_warning_names_the_path_without_a_gpu():
    c = check_path(slid(hand_path()), cpu_path=True)
    assert "without a graphics card" in c["warning"]
    assert "without a graphics card" not in check_path(slid(hand_path()))["warning"]
