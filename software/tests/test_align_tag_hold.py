"""The tag pose held where two scans share little (ENGINE_PLAN X19; ``AlignParams.hold_tags``, off by default).

Made-up scans of an L-shaped block: one sees its faces towards -x, -y and its top, the other those towards +x, +y and
its top, so they share only the top faces (about 46 % of the smaller scan's part). The second scan's top faces read
0.6 mm high, so the shapes alone fit 0.6 mm away from the true motion; four tags on the table round the block give the
true motion exactly. With the hold off (the default) every alignment is as before, bit for bit; with it on:

* little shared, 3 or more tags agreeing: the tag pose is the answer, the shapes only check it;
* a tag moved 2 mm between the scans is left out, never followed;
* a tag map wrong as a whole (all moved 9 mm, a table moved with its tags) is overruled by the shapes, with its words;
* a pose refused stays refused; tags on the table do not hold a part that was moved;
* fewer than 3 shared tags: as without the hold, with its words;
* 0.70 shared or more: as without the hold, bit for bit;
* several scans: a held link's place in the chain and its weight come from the tags.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import geometry as G  # noqa: E402
from ohs.align import AlignParams, TagMap, align_many, align_pair, scan_cloud  # noqa: E402
from ohs.align.tags import align_with_tags  # noqa: E402
from test_align import _box, _motion  # noqa: E402
from test_align_tags import _square  # noqa: E402

BOXES = [((0, 0, 0), (60, 30, 12)), ((0, 0, 12), (14, 30, 40)), ((35, 8, 12), (47, 20, 22)), ((20, 26, 12), (60, 30, 20))]
T_TRUE = _motion(150.0, (0.1, 0.2, 1.0), (40.0, -30.0, 10.0))  # the source scan's world in the target's
TAGS = [(-30, -30, 0), (90, -30, 0), (90, 60, 0), (-30, 60, 0)]
ON = AlignParams(hold_tags=True)


def _part(seed: int, faces: str, top_bias: float = 0.0):
    rng = np.random.default_rng(seed)
    P, N = zip(*[_box(rng, lo, hi, faces=faces) for lo, hi in BOXES])
    P, N = np.vstack(P), np.vstack(N)
    return P + top_bias * (N[:, 2:3] > 0.9) * np.array([0.0, 0.0, 1.0]), N


def _scans(faces_a: str = "xyZ", faces_b: str = "XYZ", bias: float = 0.6):
    Ti = G.invert_se3(T_TRUE)
    Pa, Na = _part(0, faces_a)
    Pb, Nb = _part(1, faces_b, bias)
    return scan_cloud(Pb @ Ti[:3, :3].T + Ti[:3, 3], Nb @ Ti[:3, :3].T), scan_cloud(Pa, Na)


def _tags(n: int = 4, moved: dict | None = None):
    dst = TagMap({i: _square(c, turn=15.0 * i) for i, c in enumerate(TAGS[:n])})
    Ti = G.invert_se3(T_TRUE)
    src = TagMap({i: X @ Ti[:3, :3].T + Ti[:3, 3] for i, X in dst.corners.items()})
    for i, d in (moved or {}).items():
        src.corners[i] = src.corners[i] + np.asarray(d, float)
    return src, dst


def _same(a, b) -> bool:
    """Two alignments alike, bit for bit (their time taken aside)."""
    da, db = a.as_dict(), b.as_dict()
    da.pop("ms"), db.pop("ms")
    return bool(np.array_equal(a.T, b.T)) and da == db


def _moves(src, T):
    P = src.part.fine.pts
    D = P @ (T[:3, :3] - T_TRUE[:3, :3]).T + (T[:3, 3] - T_TRUE[:3, 3])
    return float(np.sqrt(np.mean(np.sum(D * D, axis=1))))


@pytest.fixture(scope="module")
def pair():
    return _scans()


def test_the_hold_is_off_by_default_and_off_changes_nothing(pair):
    src, dst = pair
    ts, td = _tags()
    assert AlignParams().hold_tags is False
    a, ra = align_with_tags(src, dst, ts, td)
    b, rb = align_with_tags(src, dst, ts, td, replace(AlignParams(), hold_tags=False, overrule_mm=1.0, hold_min_tags=1))
    assert _same(a, b) and ra == rb
    assert not any(k in a.as_dict() for k in ("held_by_tags", "tag_rms_mm", "tags_used", "shape_moves_mm", "overruled",
                                               "tag_pose_sigma_mm"))
    assert a.accepted and a.overlap < 0.70 and a.little_shared and 0.3 < _moves(src, a.T) < 1.0  # the shapes' own minimum


def test_little_shared_and_agreeing_tags_hold_the_tag_pose(pair):
    src, dst = pair
    ts, td = _tags()
    res, row = align_with_tags(src, dst, ts, td, ON)
    d = res.as_dict()
    assert res.accepted and d["held_by_tags"] is True and d["overruled"] is None, res.verdict
    assert _moves(src, res.T) < 1e-6  # the tags' pose, not the shapes'
    assert d["tags_used"] == [0, 1, 2, 3] and d["tag_rms_mm"] < 1e-6 and d["tag_pose_sigma_mm"] >= 0
    assert 0.3 < d["shape_moves_mm"] < 1.0 and d["overlap_at_shape"] > d["overlap"] - 1e-9
    assert res.verdict.startswith("Aligned from the 4 tags both scans saw") and "so the tags decide the position" in res.verdict
    assert res.little_shared and res.caution.startswith("The scans share little, but the tags hold their position")
    assert row["start"] == "held" and row["used"] == [0, 1, 2, 3]


def test_a_tag_moved_2_mm_is_left_out_not_followed(pair):
    src, dst = pair
    ts, td = _tags(moved={2: (2.0, 0.0, 0.0)})
    res, row = align_with_tags(src, dst, ts, td, ON)
    d = res.as_dict()
    assert d["held_by_tags"] is True and d["tags_used"] == [0, 1, 3] and row["left_out"] == [2], res.verdict
    assert _moves(src, res.T) < 1e-6


def test_a_tag_map_wrong_as_a_whole_is_overruled_in_words():
    # a pair the shapes alone place for sure (78 % shared), judged with the caution's limit raised to 0.80 so that the
    # hold is weighed: what is tested is the overrule, which needs the shapes' own pose
    src, dst = _scans("xyZX", "XYZx")
    on = replace(ON, caution_overlap=0.80)
    alone = align_pair(src, dst, on)
    assert alone.accepted and alone.overlap < 0.80
    ts, td = _tags(moved={i: (9.0, 0.0, 0.0) for i in range(4)})  # the table moved 9 mm with all its tags
    res, row = align_with_tags(src, dst, ts, td, on)
    d = res.as_dict()
    assert d["held_by_tags"] is False and d["overruled"] and d["shape_moves_mm"] > 8.0, res.verdict
    assert "The tags and the shapes disagree by" in res.verdict and "Aligned from the shapes" in res.verdict
    assert np.allclose(res.T, alone.T, atol=1e-6) and res.accepted  # where the shapes put it, not where the wrong tags did
    assert res.caution.startswith("Little is shared") and row["start"] == "overruled"


def test_fewer_than_3_shared_tags_is_todays_alignment_with_its_words(pair):
    src, dst = pair
    ts, td = _tags(n=2)
    off, _ = align_with_tags(src, dst, ts, td)
    res, row = align_with_tags(src, dst, ts, td, ON)
    d = res.as_dict()
    assert np.array_equal(res.T, off.T) and d["held_by_tags"] is False and d["overruled"] is None
    assert "Only 2 tags were seen by both scans, too few to hold the position" in res.verdict
    assert res.verdict.startswith(off.verdict)


def test_much_shared_is_unchanged_bit_for_bit():
    src, dst = _scans("xXyYZ", "xXyYZ", bias=0.2)
    ts, td = _tags()
    a, ra = align_with_tags(src, dst, ts, td)
    b, rb = align_with_tags(src, dst, ts, td, ON)
    assert a.overlap >= 0.70
    assert _same(a, b) and ra == rb


def test_several_scans_place_a_held_link_by_its_tags(pair):
    src, dst = pair
    ts, td = _tags()
    off = align_many([dst, src], same_setup=False, tags=[td, ts])
    on = align_many([dst, src], ON, same_setup=False, tags=[td, ts])
    assert _moves(src, on.T[1]) < 1e-6 and _moves(src, off.T[1]) > 0.3
    row = on.pairs[0]
    assert row["held_by_tags"] is True and row["link_weight"] == "tags" and row["used"] == "chain"
    assert "link_weight" not in off.pairs[0] and "held_by_tags" not in off.pairs[0]


def test_the_hold_never_turns_a_refusal_into_an_acceptance():
    """Scans too noisy to fit within 0.7 mm: refused from the tags and from the shapes alike, the hold on or off."""
    Ti = G.invert_se3(T_TRUE)
    Pa, Na = _part(0, "xyZ")
    Pb, Nb = _part(1, "XYZ", 0.6)
    Pb = Pb + np.random.default_rng(5).normal(0.0, 0.8, Pb.shape)
    src, dst = scan_cloud(Pb @ Ti[:3, :3].T + Ti[:3, 3], Nb @ Ti[:3, :3].T), scan_cloud(Pa, Na)
    ts, td = _tags()
    off, ro = align_with_tags(src, dst, ts, td)
    on, rn = align_with_tags(src, dst, ts, td, ON)
    assert not off.accepted, off.verdict
    # as without the hold (the shapes' own guesses then decide; Open3D's RANSAC may count one start more or less)
    assert not on.accepted and on.tag_hold is None and on.verdict == off.verdict and ro == rn, on.verdict


def _plate_on_table(seed: int, x0: float):
    """A plain 60 x 40 x 4 mm plate on its table (z = 0), its corner at x0 along x; seen on every side."""
    rng = np.random.default_rng(seed)
    P, N = _box(rng, (x0, -20, 0), (x0 + 60, 20, 4), faces="xXyYZ")
    g = np.arange(-150.0, 150.0, 1.0)
    gx, gy = np.meshgrid(g, g)
    tab = np.stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)], axis=1) + rng.normal(0.0, 0.03, (gx.size, 3))
    return scan_cloud(np.vstack([P, tab]), np.vstack([N, np.tile([0.0, 0.0, 1.0], (len(tab), 1))]))


def test_tags_on_the_table_do_not_hold_a_part_that_was_moved():
    """The plate slid 40 mm along the table between the scans, the tags on the table stayed where they were (both scans'
    worlds the table's): the tags' pose is the table's, not the part's. Told the part was moved (same_setup False), the
    hold does not act on tags lying on the table, and the result is the one without the hold."""
    dst, src = _plate_on_table(0, -30.0), _plate_on_table(1, 10.0)
    tags = TagMap({i: _square(c) for i, c in enumerate([(-100, -100, 0), (100, -100, 0), (100, 100, 0), (-100, 100, 0)])})
    off, ro = align_with_tags(src, dst, tags, tags, same_setup=False)
    on, rn = align_with_tags(src, dst, tags, tags, ON, same_setup=False)
    assert off.overlap < 0.70  # today: accepted at the table's pose, as before
    d = on.as_dict()
    assert d["held_by_tags"] is False and np.allclose(on.T, off.T, atol=1e-9) and on.accepted == off.accepted
    assert "the tags lie on the table and the part was moved" in on.verdict, on.verdict
    assert d["not_held"].startswith("the tags lie on the table")


def test_a_held_links_information_is_in_the_same_frame_as_a_shape_links():
    """many._tag_information and many._information give the same matrix for the same points, far from the origin."""
    from types import SimpleNamespace

    from ohs.align.many import _information, _tag_information

    T = _motion(70.0, (0.3, 1.0, 0.2), (400.0, -250.0, 300.0))  # source into target, far out
    ts, td = TagMap(), TagMap()
    for i, c in enumerate([(380, -260, 290), (470, -240, 310), (420, -170, 300), (360, -210, 330)]):
        td.corners[i] = _square(c, turn=20.0 * i)
        Ti = G.invert_se3(T)
        ts.corners[i] = td.corners[i] @ Ti[:3, :3].T + Ti[:3, 3]

    def cloud(X):
        return SimpleNamespace(part=SimpleNamespace(fine=SimpleNamespace(pts=X, nrm=np.tile([0.0, 0.0, 1.0], (len(X), 1)))))

    S = np.concatenate([ts.corners[i] for i in range(4)])
    D = np.concatenate([td.corners[i] for i in range(4)])
    tag_info = _tag_information(ts, td, [0, 1, 2, 3], 1.0, T)
    shape_info = _information(cloud(S), cloud(D), T, 6.0)
    np.testing.assert_allclose(tag_info, shape_info, rtol=1e-9, atol=1e-6)
