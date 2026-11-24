"""``tools/large_part_study.py`` (ENGINE_PLAN X8): its scene and its end-to-end, bow and slide readings, on the true
surface and true poses with known errors put in (no rendering, no scan)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SOFTWARE = Path(__file__).resolve().parents[1]
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))

from ohs import geometry as G  # noqa: E402
from tools import large_part_study as L  # noqa: E402


def _poses(every: int = 3) -> dict:
    from ohs.sim.nominal import nominal_calibration

    calib = nominal_calibration((1280, 800), realistic=True)
    n = L.n_frames()
    t = 100.0 + calib.imu.time_offset + np.arange(n) / L.FPS
    P = L.sweep(0).poses(t)
    return {i: P[i] for i in range(0, n, every)}


def test_the_scene_is_the_one_fixed_beforehand():
    sc = L.panel_scene(0)
    a, b = L.true_hole(sc, 0), L.true_hole(sc, 1)
    assert abs(float(np.linalg.norm(a["centre"] - b["centre"])) - 1240.0) < 0.01
    assert abs(a["diameter"] - 10.0) < 0.05 and 13.5 < a["centre"][2] < 15.0  # near the table, not over a far floor
    corner = L.to_world(sc, L.surface_local(L.HALF_L, L.HALF_W))[0]
    assert abs(corner[2] - L.CORNER_Z) < 1e-9


def test_the_tags_lie_on_the_table_beside_the_panel_and_leave_the_untagged_scene_as_it_was():
    plain = L.panel_scene(0)
    assert not plain.markers and plain.meta["tags"] is None
    assert type(plain.primitives[0].albedo).__name__ == "NoiseAlbedo"  # step 1's scene, as rendered then
    sc = L.panel_scene(0, "tags")
    assert sorted(sc.markers) == list(range(20))
    for i, X in sc.markers.items():
        x, y = L.TAG_LAYOUTS["tags"][i]
        assert np.allclose(X[:, 2], 0.0) and np.allclose(X.mean(axis=0)[:2], [x, y], atol=1e-6)
        side = np.linalg.norm(X - np.roll(X, -1, axis=0), axis=1)
        assert np.allclose(side, L.TAG_MM, atol=0.2)
        assert np.all((np.abs(X[:, 0]) > L.HALF_L + 5) | (np.abs(X[:, 1]) > L.HALF_W + 5))  # never under the panel
    # the camera at a tag sees its face: the table's decal reads the right way up from above (not mirrored)
    on_panel = L.panel_scene(0, "panel")
    assert sorted(on_panel.markers) == list(range(12)) and all(X[:, 2].min() > 10 for X in on_panel.markers.values())


def test_step_3s_row_lies_along_the_near_side_150_mm_apart_and_keeps_the_ends():
    sc = L.panel_scene(0, "row150")
    places = L.TAG_LAYOUTS["row150"]
    assert sorted(sc.markers) == list(range(19))
    side = [q for q in places if abs(q[0]) <= L.HALF_L]
    assert len(side) == 9 and all(y == -230.0 for _, y in side)  # one long side only: the near one
    assert np.allclose(np.diff([x for x, _ in side]), 150.0) and side[0][0] == -600.0 and side[-1][0] == 600.0
    assert places[9:] == L.TAG_LAYOUTS["tags"][10:]  # the same ends as step 2
    for X in sc.markers.values():
        assert np.allclose(X[:, 2], 0.0) and np.all((np.abs(X[:, 0]) > L.HALF_L + 5) | (np.abs(X[:, 1]) > L.HALF_W + 5))


def test_the_share_of_frames_seeing_tags_spaced_along_the_part():
    places = L.TAG_LAYOUTS["row150"]
    det = {1: {0: None, 1: None}, 2: {0: None}, 3: {9: None, 10: None}, 4: {}, 6: {2: None, 3: None, 4: None}}
    s = L.spaced_share(det, [1, 2, 3, 4, 5, 6], places)
    # frame 3 sees two tags of one end: two tags, but not spaced along the panel
    assert (s["one"], s["two"], s["spaced"]) == (4, 3, 2) and abs(s["share_spaced"] - 2 / 6) < 1e-12


def test_the_harm_cases_change_only_what_they_say():
    from ohs.track.posegraph import StraightenParams
    from ohs.track.tag_anchors import TagView

    gt = {f: G.make_se3(G.so3_exp(np.array([0.3, 0.0, 0.0])), [0.0, 0.0, 0.0]) for f in range(10)}
    X = np.array([[0.0, 0.0, 300.0], [25.0, 0.0, 300.0], [25.0, 25.0, 300.0], [0.0, 25.0, 300.0]])
    views = ([TagView(k, 0, X.copy()) for k in range(10)] + [TagView(k, 12, X.copy()) for k in range(0, 10, 2)]
             + [TagView(k, 3, X.copy()) for k in range(3, 9)])  # tag 0 the most seen; 0 and 3 on the row, 12 an end's
    p, frames = StraightenParams(), list(range(10))
    same, _ = L._harm("none", views, frames, gt, "row150", p, 720.0, 60.0)
    assert len(same) == len(views)
    mv, info = L._harm("moved5", views, frames, gt, "row150", p, 720.0, 60.0)
    assert info["bad_tags"] == [0]
    for a, b in zip(views, mv):
        d = np.linalg.norm(b.X - a.X, axis=1)
        assert np.allclose(d, 5.0 if (a.tag == 0 and a.k >= 5) else 0.0)
    sl, info = L._harm("slip3", views, frames, gt, "row150", p, 720.0, 60.0)
    assert info["bad_tags"] == list(range(9))  # the row's tags, not the ends'
    assert all(np.allclose(np.linalg.norm(b.X - a.X, axis=1), 3.0 if (a.tag < 9 and a.k >= 5) else 0.0)
               for a, b in zip(views, sl))
    hf, _ = L._harm("half", views, frames, gt, "row150", p, 720.0, 60.0)
    assert 0.25 * len(views) < len(hf) < 0.75 * len(views)
    nz, _ = L._harm("noise3", views, frames, gt, "row150", p, 720.0, 60.0)
    assert all(not np.allclose(a.X, b.X) for a, b in zip(views, nz))


def test_a_limit_is_safe_only_when_no_harm_case_comes_out_worse(tmp_path):
    def row(acted, e2e, slide, bad=False):
        return {"acted": acted, "end_to_end_pct": e2e, "slide_worst_rms": slide, "bad_left_out": bad}

    (tmp_path / "limit").mkdir()
    for h in L.HARMS[1:]:
        lim = {str(x): row(False, 0.1, 2.0) for x in L.LIMITS}
        if h == "moved5":
            lim["0.9"] = row(True, 0.3, 1.0)  # worse than without tags: unsafe
            lim["1.0"] = row(True, 0.3, 1.0, bad=True)  # worse, but the moved tag was left out: safe
        L.write_json(tmp_path / "limit" / f"s2-row150-{h}.json", {"no_tags": row(False, 0.1, 2.0), "limits": lim})
    lim = {str(x): row(x != 0.85, 0.02 if x != 0.85 else 0.126, 1.4) for x in L.LIMITS}
    L.write_json(tmp_path / "limit" / "s2-row150-none.json", {"no_tags": row(False, 0.126, 2.35), "limits": lim})
    v = L.limit_verdicts(tmp_path, ["s2-row150"])
    assert v["0.85"] == {"safe": True, "worth": False, "unsafe_cases": []}
    assert v["0.9"]["safe"] is False and v["0.9"]["worth"] is True
    assert v["1.0"]["safe"] is True and v["1.0"]["worth"] is True


def test_the_path_reads_a_stretch_along_the_length_and_nothing_at_the_truth():
    sc = L.panel_scene(0)
    gt = _poses()
    same = L.path_metrics(sc, gt, gt, L.FPS)
    assert abs(same["end_to_end"]["length_err_mm"]) < 1e-9 and same["error_fitted"]["max"] < 1e-9
    trk = {}
    for i, T in gt.items():
        T2 = T.copy()
        T2[0, 3] *= 1.001  # 0.1 % longer along the panel
        trk[i] = T2
    p = L.path_metrics(sc, trk, gt, L.FPS)["end_to_end"]
    assert abs(p["length_err_pct"] - 0.1) < 0.005


def test_a_slide_of_one_pass_is_read_at_the_revisits():
    from ohs.sim.nominal import nominal_calibration

    sc = L.panel_scene(0)
    gt = _poses()
    fps = L.FPS
    st = nominal_calibration((1280, 800), realistic=True).stereo
    trk = {i: G.make_se3(np.eye(3), [2.0 if L.pass_of(i, fps) == 1 else 0.0, 0.0, 0.0]) @ T for i, T in gt.items()}
    r = L.revisits(sc, trk, gt, np.asarray(st.K_rect), tuple(st.image_size), fps)
    assert abs(r["1-2"]["along_rms"] - 2.0) < 0.05 and r["1-2"]["gap_rms"] < 0.25
    assert r["1-3"]["along_rms"] < 1e-9


def test_holes_and_edges_read_a_mesh_longer_by_a_tenth_of_a_percent():
    sc = L.panel_scene(0)
    truth = L.truth_of(sc)
    V = truth.V.copy()
    V[:, 0] *= 1.001
    M = G.make_se3(G.so3_exp(np.radians([1.0, -2.0, 30.0])), [10.0, -20.0, 5.0])  # the mesh's own frame
    Vm = G.transform_points(M, V)
    h = L.hole_tool(sc, truth, Vm, truth.F, None, None, G.invert_se3(M))
    assert h["A"]["answered"] == 3 and h["B"]["answered"] == 3
    assert abs(h["err_pct"] - 0.1) < 0.002
    T, _ = L.SC.place_part(Vm, G.invert_se3(M), truth)
    loc = L.to_local(sc, G.transform_points(T, Vm))
    ea, eb = L.end_edge(loc, -1.0), L.end_edge(loc, 1.0)
    pa, pb = (G.transform_points(G.invert_se3(T), L.to_world(sc, q))[0] for q in (ea, eb))
    assert abs(float(np.linalg.norm(pa - pb)) - 1301.3) < 0.01
    assert abs(L.sideways_bow(L.long_edges(loc))["bow"]) < 0.01
