"""ohs.align: an accepted pose of two scans that share little carries a caution (pairwise module docstring, 7.).

ENGINE_PLAN X7 found merges of two scans that share little (46 % and 40 % of the smaller scan's part) far worse
than either scan, while the alignment said "Aligned" and nothing more: where little is shared the fit is good
where the scans meet and can be off elsewhere. The caution says so and what helps; nothing else about the
alignment changes (a pair that was accepted stays accepted, at the same pose). A merge is given poses only, so it
measures the same share itself (``shared_at_poses``).
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import geometry as G  # noqa: E402
from ohs.align import AlignParams, align_many, align_pair, scan_cloud, shared_at_poses, shared_overlap  # noqa: E402
from ohs.align.pairwise import CAUTION_OVERLAP  # noqa: E402
from test_align import _box, _motion, _moved, _part  # noqa: E402

NO_CAUTION = AlignParams(caution_overlap=0.0)  # the alignment as it was before the caution


def _l_part(rng, faces):
    """The L-shaped block of test_align with a boss and a rib, only the faces in ``faces`` (no table)."""
    boxes = [((0, 0, 0), (60, 30, 12)), ((0, 0, 12), (14, 30, 40)), ((35, 8, 12), (47, 20, 22)), ((20, 26, 12), (60, 30, 20))]
    P, N = zip(*[_box(rng, lo, hi, faces=faces) for lo, hi in boxes])
    return np.vstack(P), np.vstack(N)


def _same(a, b) -> None:
    """Everything of the alignment but the caution is the same."""
    assert a.accepted == b.accepted and a.sure == b.sure and a.doubt == b.doubt and a.verdict == b.verdict
    assert np.array_equal(a.T, b.T) and a.overlap == b.overlap and a.rmse_mm == b.rmse_mm and a.fit_mm == b.fit_mm
    assert a.dof == b.dof and a.n_matched == b.n_matched and a.agree == b.agree


def _views():
    """The L-part seen from two sides: the target from -x, -y and above, the source from +x, +y and above. Only the
    top faces are shared; a start from picked points, a few millimetres and degrees off."""
    T_true = _motion(150.0, (0.2, 0.3, 1.0), (40.0, -60.0, 20.0))
    P, N = _l_part(np.random.default_rng(1), "xyZ")
    Ps, Ns = _moved(*_l_part(np.random.default_rng(2), "XYZ"), G.invert_se3(T_true))
    rough = T_true @ _motion(3.0, (0.3, 1.0, 0.2), (2.0, -1.5, 1.0))
    return scan_cloud(Ps, Ns), scan_cloud(P, N), T_true, rough


def test_little_shared_gives_a_caution_and_changes_nothing_else():
    src, dst, T_true, rough = _views()
    res = align_pair(src, dst, init=rough, same_setup=True)
    assert res.accepted and res.overlap < CAUTION_OVERLAP, (res.overlap, res.verdict)
    assert res.little_shared is True and isinstance(res.caution, str)
    print(res.verdict, "|", res.caution)
    c = res.caution
    assert c.startswith(f"Little is shared: {100 * res.overlap:.0f} % of the smaller scan's part")
    for words in ("fit where they meet", "a millimetre or more off", "worse than either scan", "Merging is still possible",
                  "one more pass", "tags on the part"):
        assert words in c, words
    assert res.verdict.startswith("Aligned: ") and "Little" not in res.verdict  # the verdict is as before
    d = res.as_dict()
    assert d["little_shared"] is True and d["caution"] == c
    _same(res, align_pair(src, dst, NO_CAUTION, init=rough, same_setup=True))  # without the caution: the same alignment
    # the merge's own measure at the same pose is the alignment's overlap
    assert shared_overlap(src, dst, res.T) == pytest.approx(res.overlap, abs=1e-12)


def test_much_shared_gives_no_caution():
    rng = np.random.default_rng(0)
    P, N = _part(rng)
    T_true = _motion(100.0, (0.3, 0.2, 1.0), (180.0, -40.0, 60.0))
    Ps, Ns = _moved(*_part(np.random.default_rng(1)), G.invert_se3(T_true))
    src, dst = scan_cloud(Ps, Ns), scan_cloud(P, N)
    res = align_pair(src, dst, init=T_true @ _motion(2.0, (0, 1, 0), (1.0, 1.0, 0.0)), same_setup=True)
    assert res.accepted and res.overlap > 0.9 and res.little_shared is False and res.caution is None
    assert res.as_dict()["caution"] is None
    _same(res, align_pair(src, dst, NO_CAUTION, init=T_true @ _motion(2.0, (0, 1, 0), (1.0, 1.0, 0.0)), same_setup=True))


def test_a_pair_that_is_not_accepted_has_no_caution():
    """Not accepted: the verdict already says why; the caution is for a pose the app would merge at."""
    rng = np.random.default_rng(11)
    P, N = _part(rng, table=False)
    end = P[:, 0] > 50.0
    res = align_pair(scan_cloud(P[end][::4], N[end][::4]), scan_cloud(*_part(np.random.default_rng(12), table=False)))
    assert not res.accepted and res.little_shared is False and res.caution is None


def test_a_thin_plate_scanned_from_both_faces():
    """THIN PARTS: a 4 mm plate (80 x 50, a hole and a notch) scanned from above and, turned over, from below. Its two
    faces point away from each other, so only its edges are shared: little is shared, naturally. Nothing is
    removed or thinned; the words are true there (they fit at the edges, the wall can come out too thick or too
    thin) and say what helps a thin part (a pass with it standing on edge)."""
    rng = np.random.default_rng(5)

    def plate(rng, top: bool):
        P, N = _box(rng, (0, 0, 0), (80, 50, 4), faces="xXyY" + ("Z" if top else "z"))
        keep = (np.hypot(P[:, 0] - 20.0, P[:, 1] - 25.0) > 6.0) & ~((P[:, 0] > 60.0) & (P[:, 1] > 35.0))  # a hole, a notch
        return P[keep], N[keep]

    T_true = _motion(180.0, (1.0, 0.0, 0.0), (0.0, 50.0, 4.0)) @ _motion(10.0, (0, 0, 1), (5.0, 3.0, 0.0))
    P, N = plate(rng, True)
    Ps, Ns = _moved(*plate(np.random.default_rng(6), False), G.invert_se3(T_true))
    src, dst = scan_cloud(Ps, Ns, replace(AlignParams(), support_min_share=2.0)), scan_cloud(P, N, replace(AlignParams(), support_min_share=2.0))
    p = replace(AlignParams(), support_min_share=2.0)
    rough = T_true @ _motion(2.0, (0.2, 1.0, 0.1), (1.0, -1.0, 0.5))
    res = align_pair(src, dst, p, init=rough)  # from picked points
    print(res.verdict, "|", res.caution)
    if res.accepted:  # with only the edges shared the shapes may fit better elsewhere; accepted, it carries the caution
        assert res.little_shared and "too thick or too thin" in res.caution and "standing on edge" in res.caution
    else:  # the alignment's own words say what to do, as before; no caution for a pose that was not accepted
        assert res.caution is None and res.little_shared is False and res.doubt in ("points", "overlap", "fit")
    # from tags (a few tenths of a millimetre off, kept where the shapes pin weakly): accepted, with the caution
    res = align_pair(src, dst, p, init=T_true @ _motion(0.1, (0.2, 1.0, 0.1), (0.2, -0.1, 0.1)), init_from="tags")
    print(res.verdict, "|", res.caution)
    assert res.accepted and res.overlap < CAUTION_OVERLAP and res.little_shared, res.verdict
    for words in ("fit where they meet", "too thick or too thin", "standing on edge", "tags on the part"):
        assert words in res.caution, words
    # the merge's measure says the same at the true pose: little shared, a warning naming both scans
    sh = shared_at_poses([dst, src], [np.eye(4), T_true], names=["Top", "Bottom"])
    assert sh["pairs"][0]["overlap"] < CAUTION_OVERLAP and len(sh["warnings"]) == 1
    w = sh["warnings"][0]
    assert w.startswith("\"Top\" and \"Bottom\" share little where they were merged") and "too thick or too thin" in w
    assert "standing on edge" in w and "worse than either of them" in w


def test_a_merge_measures_how_much_the_scans_share_at_its_poses():
    src, dst, T_true, _ = _views()
    sh = shared_at_poses([dst, src], [np.eye(4), T_true])
    ov = sh["pairs"][0]["overlap"]
    assert ov < CAUTION_OVERLAP and sh["little_shared"] == [{"i": 0, "j": 1, "overlap": ov}] and sh["chain"] == [[0, 1]]
    assert sh["limit"] == CAUTION_OVERLAP and sh["warnings"][0].startswith("\"scan 1\" and \"scan 2\" share little")
    assert "merge all three" in sh["warnings"][0]
    # much shared: nothing said
    P, N = _part(np.random.default_rng(0))
    Tm = _motion(100.0, (0.3, 0.2, 1.0), (180.0, -40.0, 60.0))
    full = [scan_cloud(P, N), scan_cloud(*_moved(*_part(np.random.default_rng(1)), G.invert_se3(Tm)))]
    sh = shared_at_poses(full, [np.eye(4), Tm])
    assert sh["little_shared"] == [] and sh["warnings"] == [] and sh["pairs"][0]["overlap"] > 0.9
    # three scans: the two sides share little, but each is held by a third that shares much with both: no warning
    whole = scan_cloud(*_moved(*_l_part(np.random.default_rng(3), "xXyYZ"), np.eye(4)))
    sh = shared_at_poses([dst, src, whole], [np.eye(4), T_true, np.eye(4)])
    by = {(r["i"], r["j"]): r["overlap"] for r in sh["pairs"]}
    assert by[(0, 1)] < CAUTION_OVERLAP and by[(0, 2)] > CAUTION_OVERLAP and by[(1, 2)] > CAUTION_OVERLAP, by
    assert sh["chain"] == [[0, 2], [1, 2]] and sh["warnings"] == []
    with pytest.raises(ValueError):
        shared_at_poses([dst, src], [np.eye(4)])


def test_several_scans_name_the_links_that_share_little():
    src, dst, T_true, rough = _views()
    res = align_many([dst, src], picked={(0, 1): (G.transform_points(G.invert_se3(T_true), np.array(
        [[0.0, 0.0, 12.0], [60.0, 30.0, 20.0], [14.0, 30.0, 40.0], [47.0, 8.0, 22.0]])), np.array(
        [[0.0, 0.0, 12.0], [60.0, 30.0, 20.0], [14.0, 30.0, 40.0], [47.0, 8.0, 22.0]]))})
    assert res.accepted, res.verdict
    row = res.pairs[0]
    assert row["little_shared"] is True and row["caution"].startswith("Little is shared")
    assert res.little_shared == [(0, 1)] and "Little is shared between scan 2 and scan 1 (" in res.verdict
    assert res.caution and "Merging is still possible" in res.caution and "align them all again" in res.caution
    d = res.as_dict()
    assert d["little_shared"] == [[0, 1]] and d["caution"] == res.caution
    # much shared: nothing added
    P, N = _part(np.random.default_rng(0))
    Tm = _motion(100.0, (0.3, 0.2, 1.0), (180.0, -40.0, 60.0))
    res = align_many([scan_cloud(P, N), scan_cloud(*_moved(*_part(np.random.default_rng(1)), G.invert_se3(Tm)))])
    assert res.accepted and res.little_shared == [] and res.caution is None and "Little" not in res.verdict
