"""Straighten's tags in the app (ENGINE_PLAN X8 step 2): the setting, the stage's decision to fuse, the words.

* ``[process] straighten_tags`` exists, is documented, and its default is the engine's (``StraightenParams.tags``);
  Make mesh's Straighten passes it on.
* A Straighten whose loops changed nothing but whose tags did fuses at the new poses (``straighten_looped``).
* What the tags' step says follows the rules of the server's words (``tests/test_server_error_words.py``): plain
  sentences, no em dash, no command line or route; and the whole verdict, the loops' part and the tags', is true as
  one text in every combination.
* The report and the stage say what changed the poses (the loops, the tags, or both).
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # test_server_error_words, imported by a test below

import numpy as np

from ohs.app.config import _KEY_DOCS, Config
from ohs.server import processing as SP
from ohs.track import posegraph as pg
from ohs.track.tag_anchors import TagFit


def test_the_setting_is_documented_and_its_default_is_the_engines():
    assert Config().process.straighten_tags is pg.StraightenParams().tags
    assert "process.straighten_tags" in _KEY_DOCS and "Off: as before" in _KEY_DOCS["process.straighten_tags"]
    src = inspect.getsource(SP.ProcessManager._do_straighten)
    assert "straighten_tags" in src and "params=sparams" in src


def test_tags_that_changed_the_poses_make_straighten_fuse():
    poses = {0: np.eye(4)}
    assert SP.straighten_looped({"loops_accepted": 0, "changed": True, "tags": {"acted": True}}, poses) == (True, 0)
    assert SP.straighten_looped({"loops_accepted": 0, "changed": False, "tags": {"acted": False}}, poses) == (False, 0)
    assert SP.straighten_looped({"loops_accepted": 3, "changed": True, "tags": {"acted": False}}, poses) == (True, 3)
    assert SP.straighten_looped({"loops_accepted": 0, "changed": True}, poses) == (False, 0)  # a report from before


FORBIDDEN = [re.compile("—"), re.compile(r"\bohs\s"), re.compile(r"/api/"), re.compile(r"\b(GET|POST) "),
             re.compile(r"Error|Exception|Traceback")]


def test_the_tags_words_are_plain():
    left = {3: "it disagreed with the other tags (moved during the scan, or misread)",
            7: "no other tag confirms where it is (without it the scan would lie up to 4.2 mm elsewhere): it may have "
               "moved during the scan", 9: "seen in only 2 of the frames looked at"}
    for reason in ("anchored", "agree", "not_better", "bends_too_much", "too_few", "no_tags"):
        for loops_acted in (False, True):
            fit = TagFit(None, reason, used=[0, 1, 2], left_out=left, dropped_views=2)
            s = pg._tags_sentence(fit, 4, 3.25, "the end of the scan", loops_acted)
            assert s and (s[0].isupper() or s[0].isdigit()) and s.endswith("."), s
            assert not any(f.search(s) for f in FORBIDDEN), s
            assert "Tag 3 was left out" in s and "2 single views of a tag did not fit and were left out." in s
    assert pg._tags_sentence(TagFit(None, "anchored", used=[0, 1, 2]), 3, 3.25, "the end of the scan") == \
        "3 tags held the scan: the end of the scan moved 3.2 mm into place."


# the loops' own verdicts, word for word as _straighten_loops writes them
LOOPS_STRAIGHTENED = "Closed 40 loops: the end of the scan moved 2.0 mm into place."
LOOPS_NOT_BETTER = ("Found 48 loops, but closing them would not make the passes line up clearly better, so the scan is "
                    "unchanged.")
LOOPS_ALREADY = "Closed 46 loops: the passes already line up (within 0.14 mm), so the scan is unchanged."
LOOPS_NOT_VERIFIED = ("Found 3 places where the scan passes over an earlier part, but none lined up reliably (too little "
                      "overlap or shape to match), so the scan is unchanged.")
LOOPS_NONE = "No loops found: to let Studio straighten a scan, pass over an earlier part again."


def _verdict(loops_verdict: str, loops_reason: str, fit: TagFit, moved: float | None = 7.94) -> str:
    return pg._straighten_verdict(loops_verdict, loops_reason, fit, len(fit.used) or 4, moved, "the end of the scan")


def test_the_verdict_is_true_as_one_text_in_every_combination():
    """ENGINE_PLAN X8 step 2: when the tags change a scan the loops left alone, the loops' part says the loops
    changed nothing (not that the scan is unchanged); every combination reads as one true text, in plain words."""
    acted = TagFit(None, "anchored", used=list(range(17)))
    assert _verdict(LOOPS_NOT_BETTER, "not_better", acted) == (
        "Found 48 loops, but closing them would not make the passes line up clearly better, so the loops changed "
        "nothing. 17 tags held the scan: the end of the scan moved 7.9 mm into place.")
    assert _verdict(LOOPS_ALREADY, "already_straight", acted) == (
        "Closed 46 loops: the passes already line up (within 0.14 mm), so the loops changed nothing. 17 tags held the "
        "scan: the end of the scan moved 7.9 mm into place.")
    assert _verdict(LOOPS_NOT_VERIFIED, "not_verified", acted).startswith(
        "Found 3 places where the scan passes over an earlier part, but none lined up reliably (too little overlap or "
        "shape to match), so the loops changed nothing. 17 tags held")
    assert _verdict(LOOPS_NONE, "no_candidates", acted) == (
        "No loops found. 17 tags held the scan: the end of the scan moved 7.9 mm into place.")
    # loops and tags both acted: the tags' move comes on top of the loops'
    assert _verdict(LOOPS_STRAIGHTENED, "straightened", acted) == (
        "Closed 40 loops: the end of the scan moved 2.0 mm into place. Then 17 tags held the scan: the end of the scan "
        "moved a further 7.9 mm into place.")
    # tags seen but not acted: the loops' words stay as they were (they are true), the tags' reason follows
    for reason, words in (("not_better", "17 tags seen, but a check on frames set aside showed that holding the scan to "
                                         "them would not clearly help, so they changed nothing."),
                          ("agree", "17 tags seen, and the scan already agrees with them, so they changed nothing."),
                          ("bends_too_much", "17 tags seen, but holding the scan to them would bend it more than the "
                                             "tracking can have drifted, so they changed nothing.")):
        fit = TagFit(None, reason, used=list(range(17)))
        assert _verdict(LOOPS_NOT_BETTER, "not_better", fit, None) == f"{LOOPS_NOT_BETTER} {words}"
        assert _verdict(LOOPS_STRAIGHTENED, "straightened", fit, None) == f"{LOOPS_STRAIGHTENED} {words}"
    few = TagFit(None, "too_few", used=[4], left_out={9: "seen in only 2 of the frames looked at"})
    assert pg._straighten_verdict(LOOPS_ALREADY, "already_straight", few, 2, None, "the scan") == (
        f"{LOOPS_ALREADY} 2 tags seen, but too few of them in enough frames to hold the scan, so they changed nothing. "
        "Tag 9 was left out: seen in only 2 of the frames looked at.")
    for loops in (LOOPS_STRAIGHTENED, LOOPS_NOT_BETTER, LOOPS_ALREADY, LOOPS_NOT_VERIFIED, LOOPS_NONE):
        for fit in (acted, few, TagFit(None, "not_better", used=[0, 1])):
            s = _verdict(loops, "x", fit)
            assert not any(f.search(s) for f in FORBIDDEN) and "  " not in s and ".." not in s, s


def test_the_words_follow_the_servers_rules():
    """The joined verdicts against every rule of ``tests/test_server_error_words.py`` (its forbidden texts, the
    retired names, the engine's rules and the program words)."""
    import test_server_error_words as W

    rules = ([rx.pattern for rx, _ in W.FORBIDDEN] + [r if isinstance(r, str) else r.pattern for r, _ in W.RETIRED]
             + [r for r, _ in W.ENGINE_RULES] + [r for r, _ in W.PROGRAM_WORDS])
    left = {3: "it disagreed with the other tags (moved during the scan, or misread)"}
    for loops, reason in ((LOOPS_STRAIGHTENED, "straightened"), (LOOPS_NOT_BETTER, "not_better"),
                          (LOOPS_ALREADY, "already_straight"), (LOOPS_NONE, "no_candidates")):
        for tr in ("anchored", "agree", "not_better", "bends_too_much", "too_few", "no_tags"):
            s = _verdict(loops, reason, TagFit(None, tr, used=[0, 1, 2], left_out=left, dropped_views=1))
            assert not [r for r in rules if re.search(r, s)], s


def test_the_report_says_what_changed_the_poses():
    """``reason`` stays the loops' own; ``tags`` says what the tags did; ``changed`` is "the poses changed, by the
    loops or the tags", and the health block (``straighten_summary``) carries the tags' outcome beside it."""
    from ohs.calib.scan_health import straighten_summary

    rep = {"reason": "not_better", "changed": True, "loops_accepted": 0, "correction_mm": 7.9, "correction_deg": 0.4,
           "tags": {"enabled": True, "seen": 17, "acted": True, "reason": "anchored", "used": list(range(17)),
                    "moved_mm": 7.9}}
    h = straighten_summary(rep)
    assert h["reason"] == "not_better" and h["changed"] is True
    assert h["tags"] == {"acted": True, "reason": "anchored", "used": 17, "moved_mm": 7.9}
    assert "tags" not in straighten_summary({"reason": "not_better", "changed": False})  # a report from before
    assert "by the loops or the tags" in pg.__doc__


def test_the_stage_says_how_many_tags_held_the_scan():
    """The stage card can tell a move by the tags from one by the loops: ``tags`` (how many held the scan; None when
    they did not act), next to ``loops`` and ``correction_mm``."""
    assert SP._empty_state("p", "s")["stages"]["straighten"]["tags"] is None
    assert SP.straighten_tags_used({"tags": {"acted": True, "used": [1, 2, 3]}}) == 3
    assert SP.straighten_tags_used({"tags": {"acted": False, "used": [1, 2, 3]}}) is None
    assert SP.straighten_tags_used({}) is None
    src = inspect.getsource(SP.ProcessManager._do_straighten)
    assert "tags=straighten_tags_used(rep)" in src
