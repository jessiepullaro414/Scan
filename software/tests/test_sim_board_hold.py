"""The simulated operator holds the board still while the lasers take turns.

On the 4-laser build with a channel per laser, the app takes each laser calibration view as a
burst: the frame with every laser on, then one frame per laser with only that laser lit, and it
tells the person to hold the board still until the view is captured. The virtual scanner
(:class:`ohs.sim.board_camera.SimBoardCamera`) plays that person: while only some lasers are lit
its script clock stands still (the board stays where it is), for at most ``lit_hold_s`` of frame
time, and it goes on from the same place when every laser is lit again. Without that, the view
depended on how many frames the app could process before the scripted dwell ended, so a busy PC
lost views (MAINTENANCE_PLAN.md, F12).
"""
from __future__ import annotations

import numpy as np
import pytest

from ohs.sim.board_camera import LIT_HOLD_S, SimBoardCamera

SMALL = (320, 200)
KW = {"image_size": SMALL, "realtime": False, "fps": 5.0, "dwell_s": 0.6, "move_s": 0.4, "lasers": 4}


def _read(cam: SimBoardCamera, n: int) -> list[tuple[str, np.ndarray]]:
    out = []
    for _ in range(n):
        cam.read()
        out.append((cam.phase(), cam.true_pose()))
    return out


def test_board_held_still_while_the_lasers_take_turns():
    held = SimBoardCamera("lasers", **KW)
    free = SimBoardCamera("lasers", **KW, lit_hold_s=0.0)  # the old operator: leaves at the end of the dwell
    with held, free:
        a, b = _read(held, 2), _read(free, 2)  # frames 0, 1: the first pose (dwell of 3 frames)
        assert a[1][0] == b[1][0] == "dwell 1/8"
        held.light(["vertical"])
        free.light(["vertical"])
        a, b = _read(held, 6), _read(free, 6)  # far longer than the dwell
        assert all(ph == "dwell 1/8" and np.array_equal(T, a[0][1]) for ph, T in a), [ph for ph, _ in a]
        assert [ph for ph, _ in b][1:3] == ["move", "move"]  # the free one has gone on
        held.light(None)  # every laser on again: the operator moves on from where it stood
        a = _read(held, 3)
        assert [ph for ph, _ in a] == ["dwell 1/8", "move", "move"]
        assert held.lit == held.laser_names


def test_hold_has_a_limit_and_starts_again_with_the_next_turns():
    cam = SimBoardCamera("lasers", **KW, lit_hold_s=1.0)  # 5 frames at 5 fps
    with cam:
        _read(cam, 1)
        cam.light(["horizontal_2"])
        phases = [ph for ph, _ in _read(cam, 9)]
        assert phases[:5] == ["dwell 1/8"] * 5 and "move" in phases[5:]  # gave up after 1 s, moved on
        cam.light(None)
        for _ in range(10):  # on to the next pose
            if _read(cam, 1)[0][0] == "dwell 2/8":
                break
        cam.light(["vertical_2"])
        phases = [ph for ph, _ in _read(cam, 4)]
        assert phases == ["dwell 2/8"] * 4, phases  # a new stretch of turns: held again


def test_restart_and_defaults():
    cam = SimBoardCamera("lasers", **KW)
    assert cam.lit_hold_s == LIT_HOLD_S >= 5.0  # generous: a burst needs well under a second
    with cam:
        _read(cam, 1)
        cam.light(["vertical"])
        _read(cam, 4)
        cam.light(None)
        cam.restart()  # the script starts again at script time 0 with the next frame
        a = _read(cam, 1)
        assert a[0][0] == "dwell 1/8" and np.array_equal(a[0][1], cam.pose_at(0.0))
    with pytest.raises(ValueError, match="lit_hold_s"):
        SimBoardCamera("lasers", **KW, lit_hold_s=-1.0)
