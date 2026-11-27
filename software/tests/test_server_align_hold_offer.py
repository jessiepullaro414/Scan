"""The tag hold offered per alignment (ENGINE_PLAN X19; the owner's decision of 2026-11-27: ``[process] align_hold_tags``
stays off, and the Align page offers the hold for one alignment at a time).

``hold_tags`` in the body of ``align`` and ``align_all`` overrides the setting for that alignment, both ways. Both
results carry a ``hold`` block the server writes (``ohs.server.aligning.hold_block``): ``offered`` where holding could
act, ``held`` with a caution when the shapes would move the scan more than ``HOLD_CAUTION_MM``, ``not_held`` when it
was asked and not applied, and null where there is nothing to say. Without the field and with the setting off every
other field is as before.

The scene is test_server_align_tag_hold.py's: the simulated bracket's top and back, which share little, with six tags
on the table that agree with the true motion (so a part said to be moved is not held by them).
"""
from __future__ import annotations

import copy
import json

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
pytest.importorskip("requests")

from ohs import geometry as G  # noqa: E402
from ohs.server import aligning  # noqa: E402
from ohs.server.aligning import HOLD_CAUTION_MM, _tags_on_table, hold_block  # noqa: E402
import test_server_align_tag_hold as tag_hold  # noqa: E402
from test_server_process_studio import post, run_job  # noqa: E402

env = tag_hold.env  # the module's server and scans (its own, for this module)
HOLD_FIELDS = tag_hold.HOLD_FIELDS

OFFER = ("Where two scans share little, these tags can place the scan more exactly than the shapes, as long as no tag "
         "moved against the part between the two scans (tags on the part, or tags on the table and the part not "
         "touched).")


def _align(env, **body):
    return run_job(env, post(env["base"] + "/align", {"target": env["a"], "source": env["b"], **body}), "align")["result"]


def _align_all(env, **body):
    return run_job(env, post(env["base"] + "/align_all", {"scans": [env["a"], env["b"]], **body}), "align_all")["result"]


def _without_ms(d):
    d = copy.deepcopy(d)
    d.pop("ms", None)
    for p in d.get("pairs") or []:
        p.pop("ms", None)
    return d


# ------------------------------------------------------------------ through the server (slow)
@pytest.mark.slow
def test_left_out_and_setting_off_the_response_is_as_before_with_an_offer(env):
    st = env["studio"]
    assert st.cfg.process.align_hold_tags is False
    res = _align(env)
    assert res["start"] == "tags" and res["accepted"] and res["little_shared"], res["verdict"]
    assert not any(k in res for k in HOLD_FIELDS) and res["tags"]["start"] == "accepted"
    h = res["hold"]
    used = res["tags"]["used"]
    assert h["state"] == "offered" and h["asked"] is None and h["caution"] is None, h
    assert h["words"] == f"Both scans saw tags {', '.join(map(str, used[:-1]))} and {used[-1]}. {OFFER}", h["words"]
    assert h["links"] == [{"a": env["a"], "b": env["b"], "tags": used, "shape_moves_mm": None}]
    # asked off explicitly: the same answer, field for field
    off = _align(env, hold_tags=False)
    assert _without_ms(off) == {**_without_ms(res), "hold": {**h, "asked": False}}


@pytest.mark.slow
def test_asked_on_the_tags_hold_with_the_setting_off(env):
    st = env["studio"]
    assert st.cfg.process.align_hold_tags is False
    res = _align(env, hold_tags=True)
    assert res["held_by_tags"] is True and res["tags"]["start"] == "held", res["verdict"]
    deg, mm = G.pose_error(env["T_true"], np.asarray(res["T"]))
    assert mm < 1e-3 and deg < 1e-4
    h = res["hold"]
    assert h["state"] == "held" and h["asked"] is True, h
    assert h["words"] == "The tags hold \"Back\" on \"Top\", as asked for this alignment.", h["words"]
    assert h["links"] == [{"a": env["a"], "b": env["b"], "tags": res["tags_used"], "shape_moves_mm": res["shape_moves_mm"]}]
    assert (h["caution"] is None) == (res["shape_moves_mm"] <= HOLD_CAUTION_MM)
    # a merge at the held pose records it, as with the setting on
    m = run_job(env, post(env["base"] + "/merge", {"target": env["a"], "source": env["b"], "T": res["T"],
                                                   "name": "Held for this alignment"}), "merge")["result"]
    assert [(x["a"], x["b"]) for x in m["held_by_tags"]] == [(env["a"], env["b"])]


@pytest.mark.slow
def test_the_caution_above_its_limit_and_not_below(env, monkeypatch):
    res = _align(env, hold_tags=True)
    moves = res["shape_moves_mm"]
    assert moves is not None and moves > 0
    monkeypatch.setattr(aligning, "HOLD_CAUTION_MM", moves - 0.01)
    hi = _align(env, hold_tags=True)["hold"]
    assert hi["state"] == "held" and hi["caution"] == (
        f"The shapes, starting at the tags, would move \"Back\" {moves:.1f} mm from where the tags put it. A small "
        "disagreement is normal where two scans share little, but it is also what a part nudged against tags on the "
        "table looks like. If the tags are on the table and the part may have moved, let the shapes decide."), hi
    monkeypatch.setattr(aligning, "HOLD_CAUTION_MM", moves + 0.01)
    lo = _align(env, hold_tags=True)["hold"]
    assert lo["state"] == "held" and lo["caution"] is None, lo


@pytest.mark.slow
def test_asked_off_with_the_setting_on_is_the_answer_without_the_hold(env):
    st = env["studio"]
    base = _align(env)
    st.cfg.process.align_hold_tags = True
    try:
        on = _align(env)
        assert on["held_by_tags"] is True and on["hold"]["state"] == "held" and on["hold"]["asked"] is None
        assert on["hold"]["words"] == "The tags hold \"Back\" on \"Top\", as the settings ask.", on["hold"]["words"]
        off = _align(env, hold_tags=False)
        assert not any(k in off for k in HOLD_FIELDS)
        assert _without_ms(off) == {**_without_ms(base), "hold": {**base["hold"], "asked": False}}
    finally:
        st.cfg.process.align_hold_tags = False


@pytest.mark.slow
def test_asked_but_not_held_says_so_and_offers_nothing(env):
    # the back scan saw only two of the tags: too few to hold, and too few to offer
    p = env["studio"].projects.scan_dir(env["pid"], env["b"]) / "result" / "marker_map.json"
    full = p.read_text(encoding="utf-8")
    m = json.loads(full)
    m["corners"] = {k: v for k, v in m["corners"].items() if k in ("0", "1")}
    p.write_text(json.dumps(m), encoding="utf-8")
    try:
        res = _align(env, hold_tags=True)
        assert res["start"] == "tags" and res["held_by_tags"] is False and res["not_held"], res
        h = res["hold"]
        assert h["state"] == "not_held" and h["asked"] is True and h["caution"] is None, h
        assert h["words"] == ("Holding the tags' position was asked for, but it was not applied: only 2 tags were seen "
                              "by both scans, too few to hold the position."), h["words"]
        plain = _align(env)
        assert plain["accepted"] and plain["start"] == "tags" and plain["little_shared"] and plain["hold"] is None, plain
        nh = _align_all(env, hold_tags=True)["hold"]
        assert nh["state"] == "not_held" and nh["words"] == (
            "Holding the tags' position was asked for, but it was not applied: for \"Back\" on \"Top\", only 2 tags "
            "were seen by both scans, too few to hold the position."), nh
        assert _align_all(env)["hold"] is None
    finally:
        p.write_text(full, encoding="utf-8")


@pytest.mark.slow
def test_picked_points_and_no_tags_have_nothing_to_say(env):
    T = np.asarray(env["T_true"])
    pts = np.array([[0.0, 0.0, 0.0], [40.0, 0.0, 5.0], [0.0, 40.0, 10.0], [30.0, 30.0, 0.0]])
    pairs = [{"source": p.tolist(), "target": (T[:3, :3] @ p + T[:3, 3]).tolist()} for p in pts]
    res = _align(env, pairs=pairs, hold_tags=True)
    assert res["start"] == "given" and res["hold"] is None and not any(k in res for k in HOLD_FIELDS)
    res = _align(env, use_tags=False)
    assert res["start"] != "tags" and res["hold"] is None


@pytest.mark.slow
def test_align_all_offer_held_and_caution(env, monkeypatch):
    st = env["studio"]
    assert st.cfg.process.align_hold_tags is False
    res = _align_all(env)
    h = res["hold"]
    used = res["pairs"][0]["tags"]["used"]
    assert h["state"] == "offered" and h["asked"] is None, h
    assert h["words"] == (f"\"Top\" and \"Back\" both saw tags {', '.join(map(str, used[:-1]))} and {used[-1]}. "
                          + OFFER.replace("place the scan", "place the scans").replace("the two scans", "the scans")), h
    assert not any(k in res["pairs"][0] for k in HOLD_FIELDS + ("link_weight",))
    off = _align_all(env, hold_tags=False)
    assert _without_ms(off) == {**_without_ms(res), "hold": {**h, "asked": False}}
    held = _align_all(env, hold_tags=True)
    p = held["pairs"][0]
    assert p["held_by_tags"] is True and p["link_weight"] == "tags"
    hh = held["hold"]
    assert hh["state"] == "held" and hh["asked"] is True
    assert hh["words"] == "The tags hold \"Back\" on \"Top\", as asked for this alignment.", hh["words"]
    assert hh["links"] == [{"a": env["a"], "b": env["b"], "tags": p["tags_used"], "shape_moves_mm": p["shape_moves_mm"]}]
    monkeypatch.setattr(aligning, "HOLD_CAUTION_MM", p["shape_moves_mm"] - 0.01)
    c = _align_all(env, hold_tags=True)["hold"]["caution"]
    assert c.startswith(f"The shapes, starting at the tags, would move \"Back\" {p['shape_moves_mm']:.1f} mm from where "
                        "the tags put it."), c


# ------------------------------------------------------------------ the block's rules (fast)
def _link(res, tags=(0, 1, 2, 3), on_table=False, a="Top", b="Back"):
    return {"a": "a", "b": "b", "a_name": a, "b_name": b, "res": res,
            "tags": None if tags is None else {"shared": list(tags), "used": list(tags), "left_out": []},
            "on_table": on_table}


OK = {"accepted": True, "start": "tags", "little_shared": True}


def test_the_offer_only_in_its_state():
    assert hold_block([_link(OK)], None, False)["state"] == "offered"
    assert hold_block([_link(OK)], False, False)["state"] == "offered"
    assert hold_block([_link({**OK, "accepted": False})], None, False) is None
    assert hold_block([_link({**OK, "little_shared": False})], None, False) is None
    assert hold_block([_link({**OK, "start": "given"})], None, False) is None
    assert hold_block([_link({**OK, "start": "features"})], None, False) is None
    assert hold_block([_link(OK, tags=(0, 1))], None, False) is None
    assert hold_block([_link(OK, tags=None)], None, False) is None
    assert hold_block([_link(OK, on_table=True)], None, False) is None
    assert hold_block([], None, False) is None
    one = hold_block([_link(OK, tags=(4, 0, 2))], None, False)["words"]
    assert one.startswith("Both scans saw tags 4, 0 and 2. Where two scans share little")
    # the hold on and not weighed (it shares 70 % or more, or no start from tags): nothing to say
    assert hold_block([_link({**OK, "little_shared": False})], True, True) is None


def test_held_and_not_held_words():
    held = {**OK, "held_by_tags": True, "tags_used": [0, 1, 2, 3], "shape_moves_mm": 0.4, "overruled": None, "not_held": None}
    b = hold_block([_link(held)], True, True)
    assert b["state"] == "held" and b["caution"] is None and b["links"][0]["shape_moves_mm"] == 0.4
    b = hold_block([_link({**held, "shape_moves_mm": HOLD_CAUTION_MM + 0.4})], True, True)
    assert b["caution"].startswith(f"The shapes, starting at the tags, would move \"Back\" {HOLD_CAUTION_MM + 0.4:.1f} mm")
    over = {**held, "held_by_tags": False, "shape_moves_mm": 2.6,
            "overruled": "the shapes, starting at the tags, moved the scan 2.6 mm from where the tags put it (more than 2.0 mm)"}
    b = hold_block([_link(over)], True, True)
    assert b["state"] == "not_held" and b["caution"] is None and b["words"] == (
        "Holding the tags' position was asked for, but it was not applied: the shapes, starting at the tags, moved the "
        "scan 2.6 mm from where the tags put it (more than 2.0 mm).")
    few = {**over, "overruled": None, "not_held": "only 2 tags were seen by both scans, too few to hold the position"}
    assert hold_block([_link(few)], True, True)["words"].endswith(": only 2 tags were seen by both scans, too few to hold "
                                                                  "the position.")


def test_tags_on_the_table_of_a_moved_part_are_not_offered():
    class Cloud:
        support = (np.array([0.0, 0.0, 1.0]), -2.0)  # the table: z = 2

    class Tags:
        corners = {0: np.full((4, 3), 2.5), 1: np.full((4, 3), 1.0), 2: np.full((4, 3), 30.0)}

    assert _tags_on_table(Cloud(), Tags(), [0, 1], same_setup=False)
    assert not _tags_on_table(Cloud(), Tags(), [0, 1], same_setup=True)  # the part not moved: they can hold it
    assert not _tags_on_table(Cloud(), Tags(), [0, 1, 2], same_setup=False)  # one on the part
    Cloud.support = None
    assert not _tags_on_table(Cloud(), Tags(), [0, 1], same_setup=False)  # no table found


def test_several_links_one_block():
    held = {**OK, "held_by_tags": True, "tags_used": [0, 1, 2], "shape_moves_mm": 1.4, "overruled": None, "not_held": None}
    links = [_link(held, b="Back"), _link({**held, "shape_moves_mm": 1.2}, a="Back", b="Side"),
             _link({**held, "shape_moves_mm": 0.3}, b="End")]
    b = hold_block(links, None, True)
    assert b["state"] == "held" and len(b["links"]) == 3
    assert b["words"] == "The tags hold \"Back\" on \"Top\", \"Side\" on \"Back\" and \"End\" on \"Top\", as the settings ask."
    assert b["caution"].startswith("The shapes, starting at the tags, would move \"Back\" 1.4 mm and \"Side\" 1.2 mm from "
                                   "where the tags put them. A small disagreement is normal")
    offers = hold_block([_link(OK), _link(OK, tags=(5, 6, 7), a="Back", b="Side"), _link(OK, tags=(1,), b="End")], None, False)
    assert offers["words"].startswith("\"Top\" and \"Back\" both saw tags 0, 1, 2 and 3. \"Back\" and \"Side\" both saw "
                                      "tags 5, 6 and 7. Where two scans share little, these tags can place the scans")
    assert [x["b"] for x in offers["links"]] == ["b", "b"]
    # links that saw the same tags: one sentence
    same = hold_block([_link(OK), _link(OK, a="Top", b="Side"), _link(OK, a="Back", b="Side")], None, False, several=True)
    assert same["words"].startswith("\"Top\" with \"Back\", \"Top\" with \"Side\" and \"Back\" with \"Side\" each saw tags "
                                    "0, 1, 2 and 3. Where two scans share little"), same["words"]


def test_aligning_again_without_the_hold_forgets_the_held_pose():
    """ "Let the shapes decide" after a held alignment: a later merge of the pair must not be recorded as placed by the
    tags because an older alignment of it was."""
    m = aligning.AlignManager(None)
    held = {"held_by_tags": True, "T": np.eye(4).tolist(), "tags_used": [0, 1, 2], "tag_rms_mm": 0.1,
            "tag_pose_sigma_mm": 0.1, "shape_moves_mm": 0.7}
    m._remember_held("p", "a", "b", held)
    m._remember_held("p", "a", "c", held)
    assert set(m._held) == {("p", "a", "b"), ("p", "a", "c")}
    m._remember_held("p", "a", "b", {"held_by_tags": False, "T": np.eye(4).tolist()})
    m._remember_held("p", "a", "c", {})
    assert m._held == {}
