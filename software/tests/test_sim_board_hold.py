"""The simulated operator holds the board still while the lasers take turns.

On the 4-laser build with a channel per laser, the app takes each laser calibration view as a
burst: the frame with every laser on, then one frame per laser with only that laser lit, and it
tells the person to hold the board still until the view is captured. The virtual scanner
(:class:`ohs.sim.board_camera.SimBoardCamera`) plays that person: while only some lasers are lit
its script clock stands still (the board stays where it is), for at most ``lit_hold_s`` of frame
time, and it goes on from the same place when every laser is lit again. Without that, the view
depended on how many frames the app could process before the scripted dwell ended, so a busy PC
lost views (MAINTENANCE_PLAN.md, F12).

The same operator can also hold each pose past its dwell until the app has taken or refused its
view (``view_hold_s``, which the calibration tests use; MAINTENANCE_PLAN.md, F10).
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


# ------------------------------------------------------------- holding each pose until the view is taken (F10)
def _answered(cam: SimBoardCamera, n: int, outcome: str = "wait") -> list[str]:
    """Read ``n`` frames, the app answering ``outcome`` for each; their phases."""
    out = []
    for _ in range(n):
        f = cam.read()
        out.append(cam.phase())
        cam.view_outcome(f.index, outcome)
    return out


def test_pose_held_until_the_app_takes_or_refuses_its_view():
    kw = {**KW, "lasers": 2, "view_hold_s": 2.0}  # 10 frames at 5 fps; a dwell is 3 frames, a move 2
    free = SimBoardCamera("lasers", **{**kw, "view_hold_s": 0.0})
    deaf = SimBoardCamera("lasers", **kw)  # nobody listens: as scripted
    cam = SimBoardCamera("lasers", **kw)
    assert cam.holds_views and not free.holds_views and not SimBoardCamera("imu", **kw).holds_views
    with free, deaf, cam:
        scripted = [ph for ph, _ in _read(free, 12)]
        assert [ph for ph, _ in _read(deaf, 12)] == scripted and deaf.views_held == 0
        cam.view_outcome(None, "wait")  # the app listens
        a = _answered(cam, 6)  # still "wait": the dwell (3 frames) is held on
        assert a == ["dwell 1/8"] * 6 and cam.views_held == 3
        a = _answered(cam, 1, "taken")  # taken: the operator moves on
        assert a == ["dwell 1/8"]
        assert _answered(cam, 2) == ["move", "move"]
        a = _answered(cam, 3)  # the next pose: its dwell as scripted, then held
        assert a == ["dwell 2/8"] * 3
        assert _answered(cam, 1, "refused") == ["dwell 2/8"]  # held one frame, then the app refused it
        assert _answered(cam, 1) == ["move"]


def test_view_hold_has_a_limit_and_ends_with_the_app():
    cam = SimBoardCamera("lasers", **{**KW, "lasers": 2, "view_hold_s": 1.0})  # 5 frames
    with cam:
        cam.view_outcome(None, "wait")
        phases = _answered(cam, 3 + 5 + 1)
        assert phases == ["dwell 1/8"] * 8 + ["move"], phases  # gave up after 5 held frames
        assert cam.views_held == 5
        _answered(cam, 4)  # move, then dwell 2 (3 frames)
        cam.view_outcome(None, "ended")  # the calibration stopped: nobody listens any more
        assert _answered(cam, 2) == ["move", "move"]
        with pytest.raises(ValueError, match="view outcome"):
            cam.view_outcome(0, "maybe")
    with pytest.raises(ValueError, match="view_hold_s"):
        SimBoardCamera("lasers", **KW, view_hold_s=-1.0)


def test_view_hold_waits_for_the_app_before_each_held_frame():
    """While it holds, a frame is rendered only while the app is at most ``VIEW_AHEAD_S`` of frame
    time behind (3 frames at 5 fps; at most ``VIEW_WAIT_S`` of waiting), so held frames are not
    rendered for nothing."""
    import threading
    import time

    from ohs.sim.board_camera import VIEW_AHEAD_S, VIEW_WAIT_S

    assert round(VIEW_AHEAD_S * KW["fps"]) == 3
    cam = SimBoardCamera("lasers", **{**KW, "lasers": 2, "view_hold_s": 2.0})
    with cam:
        cam.view_outcome(None, "wait")
        _answered(cam, 3)  # the dwell: frames 0-2
        t = time.perf_counter()
        for _ in range(3):  # frames 3 (the first held one), 4 and 5: the app is close enough behind
            cam.read()
        assert time.perf_counter() - t < 0.8 * VIEW_WAIT_S
        t = time.perf_counter()
        cam.read()  # frame 6: nobody answered frame 3, so it waits the most
        assert time.perf_counter() - t >= 0.8 * VIEW_WAIT_S
        threading.Timer(0.05, lambda: cam.view_outcome(4, "wait")).start()
        t = time.perf_counter()
        cam.read()  # frame 7: the answer on frame 4 comes after 50 ms
        assert time.perf_counter() - t < 0.8 * VIEW_WAIT_S
        assert cam.phase() == "dwell 1/8" and cam.views_held == 5


def test_operator_moves_on_once_the_view_is_taken():
    """A view taken within the dwell: the rest of the dwell is skipped, as a person moves on when
    the app says the view is captured."""
    cam = SimBoardCamera("lasers", **{**KW, "lasers": 2, "dwell_s": 1.0, "view_hold_s": 2.0})  # dwell: 5 frames
    with cam:
        cam.view_outcome(None, "wait")
        assert _answered(cam, 2) == ["dwell 1/8"] * 2
        assert _answered(cam, 1, "taken") == ["dwell 1/8"]  # frame 2 taken
        assert _answered(cam, 2) == ["move", "move"]  # frames 3, 4: dwell frames 3 and 4 skipped
        assert cam.views_skipped == 2 and cam.views_held == 0
        assert _answered(cam, 1) == ["dwell 2/8"]


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
