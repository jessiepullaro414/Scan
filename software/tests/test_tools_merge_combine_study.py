"""``tools/merge_combine_study.py`` (ENGINE_PLAN E-D3 item 4): the rule that combines two scans' readings, the
merged sigma, the reading of a mesh along the true normal, the wall reading, the verdicts and the emulated tag
map, on synthetic surfaces with known errors and sigmas. Nothing is rendered; numpy only, so both compute paths
run it alike."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import merge_combine_study as mc  # noqa: E402


def test_weights_per_rule():
    a, b = mc.weights(np.array([0.1]), np.array([0.1]), "ivw")
    assert a[0] == pytest.approx(0.5) and b[0] == pytest.approx(0.5)
    a, _ = mc.weights(np.array([0.1]), np.array([0.3]), "ivw")
    assert a[0] == pytest.approx(0.9)
    a, _ = mc.weights(np.array([0.1]), np.array([0.3]), "equal")
    assert a[0] == pytest.approx(0.5)
    a, _ = mc.weights(np.array([0.1, 0.1, 0.2]), np.array([0.1, 0.2, 0.1]), "soft")
    assert a[0] == pytest.approx(0.5)
    assert a[1] == pytest.approx(0.5 + 0.45 * np.tanh(1.0))  # sigma ratio 2: the better view takes 84 %
    assert a[2] == pytest.approx(0.5 - 0.45 * np.tanh(1.0))
    a, _ = mc.weights(np.array([0.1, 0.1]), np.array([0.15, 1.0]), "capped")
    assert a[0] == pytest.approx(0.0225 / 0.0325)  # within the cap: as 1/sigma^2
    assert a[1] == pytest.approx(0.8)  # 1/sigma^2 would give 0.99: held at 4 times the other
    with pytest.raises(ValueError):
        mc.weights(np.array([0.1]), np.array([0.1]), "max")


def test_nothing_seen_once_is_lost():
    eA = np.array([0.2, np.nan, 0.1, np.nan])
    eB = np.array([np.nan, -0.3, 0.3, np.nan])
    s = np.full(4, 0.1)
    for rule in mc.RULES:
        e, sm, si = mc.combine(eA, s, eB, s, rule)
        assert e[0] == pytest.approx(0.2) and e[1] == pytest.approx(-0.3)  # one scan alone: taken as it is
        assert np.isnan(e[3]) and np.isnan(sm[3])
        assert e[2] == pytest.approx(0.2)  # equal sigmas: every rule averages
    _, sm, si = mc.combine(eA, s, eB, s, "ivw")
    assert sm[2] == pytest.approx(np.sqrt(0.01 + 0.25 * 0.04))  # the mixture holds the disagreement
    assert si[2] == pytest.approx(0.1 / np.sqrt(2))


def test_inverse_variance_follows_the_better_view_and_its_sigma_stays_honest():
    rng = np.random.default_rng(1)
    n = 40000
    sA = np.where(rng.random(n) < 0.5, 0.05, 0.4)  # where A saw well B saw poorly, and the other way round
    sB = np.where(sA == 0.05, 0.4, 0.05)
    eA, eB = rng.normal(0, sA), rng.normal(0, sB)
    err = {}
    for rule in mc.RULES:
        e, sm, si = mc.combine(eA, sA, eB, sB, rule)
        err[rule] = np.sqrt(np.mean(e ** 2))
        assert mc.two_sigma_cover(e, sm) >= 0.95  # the mixture sigma covers twice over
    assert err["ivw"] < err["soft"] < err["equal"]
    assert err["ivw"] < err["capped"] < err["equal"]
    assert err["ivw"] == pytest.approx(np.sqrt(np.mean(1 / (1 / sA ** 2 + 1 / sB ** 2))), rel=0.05)
    # a bias both scans share is not seen by the naive inverse-variance sigma; the mixture's is no smaller than a scan's
    e, sm, si = mc.combine(eA + 0.1, sA, eB + 0.1, sB, "ivw")
    assert mc.two_sigma_cover(e, si) < 0.9
    assert np.all(sm >= np.minimum(sA, sB) - 1e-12)


def test_read_surface_along_the_normal_keeps_the_faces_apart():
    g = np.linspace(-5, 5, 41)
    xx, yy = np.meshgrid(g, g)
    top = np.c_[xx.ravel(), yy.ravel(), np.full(xx.size, 0.5 + 0.2)]  # the top face of a 1 mm wall, read 0.2 out
    bot = np.c_[xx.ravel(), yy.ravel(), np.full(xx.size, -0.5 - 0.1)]  # its bottom face, 0.1 out
    P = np.vstack([top, bot])
    N = np.vstack([np.tile([0, 0, 1.0], (len(top), 1)), np.tile([0, 0, -1.0], (len(bot), 1))])
    S = np.r_[np.full(len(top), 0.1), np.full(len(bot), 0.2)]
    X = np.array([[0, 0, 0.5], [1, 1, -0.5], [20, 0, 0.5]])
    Xn = np.array([[0, 0, 1.0], [0, 0, -1.0], [0, 0, 1.0]])
    e, s = mc.read_surface(P, N, S, X, Xn)
    assert e[0] == pytest.approx(0.2) and s[0] == pytest.approx(0.1)
    assert e[1] == pytest.approx(0.1) and s[1] == pytest.approx(0.2)  # the bottom face, not the nearer top one
    assert np.isnan(e[2])


def test_wall_errors_from_the_two_faces():
    n = 200
    Xn = np.r_[np.tile([1.0, 0, 0], (n, 1)), np.tile([-1.0, 0, 0], (n, 1)), np.tile([0, 0, 1.0], (n, 1))]
    e = np.r_[np.full(n, 0.3), np.full(n, 0.2), np.full(n, 9.0)]
    prim = np.zeros(3 * n, int)
    walls = [{"name": "rib", "axis": [1.0, 0, 0], "true_mm": 1.0}]
    assert mc.wall_errors(prim, Xn, e, walls)["rib"] == pytest.approx(0.5)  # 0.5 mm thicker
    e2 = e.copy()
    e2[n:2 * n] = np.nan  # one face unseen: no reading
    assert mc.wall_errors(prim, Xn, e2, walls)["rib"] is None


def test_the_two_rules():
    rng = np.random.default_rng(2)
    n = 4000
    inA = np.r_[np.zeros(1000, bool), np.ones(2 * n - 1000, bool)]  # three quarters seen by both
    inB = np.r_[np.ones(2 * n - 1000, bool), np.zeros(1000, bool)]
    eA = np.where(inA, rng.normal(0, 0.4, 2 * n), np.nan)  # each alone 79 % within 0.5 mm; averaged 92 %;
    eB = np.where(inB, rng.normal(0, 0.4, 2 * n), np.nan)  # "either within 0.5 mm" counts 95.5 %
    s = np.full(2 * n, 0.4)
    e, sm, _ = mc.combine(eA, s, eB, s, "ivw")
    worse = np.where(np.isfinite(e), e + 0.4, np.nan)
    res = mc.judge_case(eA, s, eB, s, {"ivw": (e, sm), "worse": (worse, sm)}, inA, inB)
    r = res["rows"]["ivw"]
    assert r["E-D4 (1) on S_A"] and r["E-D4 (1) on S_B"] and r["E-D4 (3) more than the larger scan"]
    assert r["X7 beats the better single"]
    assert not r["X7 reaches the union"]  # the union is an oracle: an average of two noisy scans falls under it
    w = res["rows"]["worse"]
    assert not w["E-D4 (1) on S_A"] and not w["X7 beats the better single"]


def test_emulated_tag_map_without_noise_gives_the_tracked_world():
    tags = mc.table_tags()
    K = np.array([[1000.0, 0, 640], [0, 1000.0, 400], [0, 0, 1]])
    poses = {}
    for k, a in enumerate(np.linspace(0, 2 * np.pi, 24, endpoint=False)):
        c = np.array([250 * np.cos(a), 250 * np.sin(a), 200.0])
        z = -c / np.linalg.norm(c)
        x = np.cross(z, [0, 0, 1.0])
        x /= np.linalg.norm(x)
        T = np.eye(4)
        T[:3, :3] = np.c_[x, np.cross(z, x), z]
        T[:3, 3] = c
        poses[k] = T
    shift = np.eye(4)
    shift[:3, 3] = [1.0, -2.0, 0.5]  # a tracked world off the true one
    tracked = {k: shift @ T for k, T in poses.items()}
    m, seen = mc.emulated_tag_map(tags, poses, tracked, K, (1280, 800), noise_mm=(0, 0, 0))
    assert len(m) >= 4 and all(v >= 2 for v in m.views.values())
    for i, X in m.corners.items():
        np.testing.assert_allclose(X, tags[i] + shift[:3, 3], atol=1e-6)
    from ohs.align.tags import pose_from_tags

    ref = mc.emulated_tag_map(tags, poses, poses, K, (1280, 800), noise_mm=(0, 0, 0))[0]
    tp = pose_from_tags(m, ref)
    np.testing.assert_allclose(tp.T, np.linalg.inv(shift), atol=1e-6)
    noisy = mc.emulated_tag_map(tags, poses, poses, K, (1280, 800), rng=np.random.default_rng(0))[0]
    off = [np.abs(noisy.corners[i] - tags[i]).max() for i in noisy.corners]
    assert 0 < max(off) < 1.0  # the median of a few sightings at 0.15-0.36 mm


def test_keep_apart_averages_one_surface_and_keeps_a_walls_two_faces():
    z = np.linspace(-3, 3, 61).astype(np.float32)
    n = np.full(z.shape, 10.0, np.float32)
    s = np.full(z.shape, 0.1, np.float32)
    up = np.tile([0, 0, 1.0], (len(z), 1))
    # one surface seen by both, 0.2 mm apart: the mean, its zero crossing in the middle
    t, cnt, sig, opp = mc.keep_apart(z - 0.0, n, s, z - 0.2, n, s, up, up)
    assert not opp.any() and cnt[0] == 20
    assert np.interp(0.0, t, z) == pytest.approx(0.1, abs=1e-3)
    assert sig[30] == pytest.approx(np.sqrt(0.01 + 0.25 * 0.04), rel=1e-4)  # the disagreement is in the sigma
    # a 1 mm wall: A sees its top face from above, B its bottom face from below (truncated at 4 mm)
    tA, tB = np.clip(z - 0.5, -4, 4), np.clip(-(z + 0.5), -4, 4)
    t, _, _, opp = mc.keep_apart(tA, n, s, tB, n, s, up, -up)
    assert opp.all()
    pos = t >= 0
    faces = z[np.flatnonzero(np.diff(pos.astype(int)) != 0)]
    assert len(faces) == 2 and abs(faces[0] + 0.5) < 0.11 and abs(faces[1] - 0.5) < 0.11  # each face where its scan put it
    mean = 0.5 * (tA + tB)  # what an average would do: the wall's inside reaches past both faces
    assert (mean < 0).sum() > (t < 0).sum()
    plain, _, _, none = mc.keep_apart(tA, n, s, tB, n, s, up, -up, keep_faces=False)  # the control: no keeping apart
    assert not none.any()
    np.testing.assert_allclose(plain, mean)
    # a scan that barely saw a voxel does not count there; nothing seen by one scan only is lost
    few = np.full(z.shape, 2.0, np.float32)
    t, _, _, _ = mc.keep_apart(z - 0.0, n, s, z - 1.0, few, s, up, up)
    np.testing.assert_allclose(t, z)


def test_union_bricks_and_the_brick_gradient():
    kA = np.array([[0, 0, 0], [1, 2, 3], [-5, 0, 7]])
    kB = np.array([[1, 2, 3], [9, -9, 0]])
    keys, ia, ib = mc.union_bricks(kA, kB)
    assert len(keys) == 4
    for k, i, j in zip(keys, ia, ib):
        assert (i < 0 or (kA[i] == k).all()) and (j < 0 or (kB[j] == k).all())
        assert i >= 0 or j >= 0
    zz, yy, xx = np.meshgrid(np.arange(8), np.arange(8), np.arange(8), indexing="ij")
    t = (2.0 * xx - yy + 0.5 * zz)[None].astype(np.float32)  # bricks are [z, y, x]
    g = mc.brick_gradient(t)
    np.testing.assert_allclose(g[0, 4, 4, 4], [2.0, -1.0, 0.5], atol=1e-6)


def test_mesh_volume_puts_the_surface_where_the_values_cross():
    pytest.importorskip("open3d")
    keys = np.array([[x, y, z] for x in range(2) for y in range(2) for z in range(-1, 1)])
    zz = (np.arange(8)[:, None, None] + 8 * keys[:, 2][:, None, None, None]).astype(np.float32)  # voxel z, 1 mm voxels
    t = np.clip(zz - 0.3, -4, 4) * np.ones((len(keys), 8, 8, 8), np.float32)
    vol = {"keys": keys, "t": t, "n": np.full(t.shape, 10.0, np.float32), "s": np.full(t.shape, 0.1, np.float32),
           "nlook": np.full(t.shape, 5.0, np.float32), "wsum": np.full(t.shape, 10.0, np.float32), "voxel_mm": 1.0,
           "trunc_mm": 4.0, "n_integrated": 20}
    V, F, N, S, L = mc.mesh_volume(vol)
    assert len(F) > 0
    np.testing.assert_allclose(np.median(V[:, 2]), 0.3, atol=0.02)
    assert np.all(S >= 0.1 - 1e-6) and np.median(L) == pytest.approx(5.0, abs=0.01)


def test_the_tag_runs_follow_the_first_tests_order():
    assert mc.TAG_RUNS[:3] == ((0, False), (1, False), (2, False)) and mc.TAG_RUNS[3:] == ((0, True), (1, True), (2, True))
    assert [mc.tag_run_name(*r) for r in mc.TAG_RUNS] == ["tag0", "tag1", "tag2", "tag0c", "tag1c", "tag2c"]


def test_face_decals_paint_one_face_only():
    from ohs.sim.scene import Decal, apriltag_texture

    up = Decal(apriltag_texture(0, 20.0), 10, (0.0, 0.0), margin_mm=2.5, tag_id=0, flip_y=True)
    alb = mc._FaceDecals(0.7, [up], [1])
    p = np.array([[0.0, 0.0, 4.0], [0.0, 0.0, -4.0], [30.0, 0.0, 4.0]])
    a = alb(p, np.zeros_like(p), np.zeros(3))
    assert a[1] == pytest.approx(0.7) and a[2] == pytest.approx(0.7)  # the other face and beside the tag: the base
    p2 = np.array([[x, y, 4.0] for x in np.linspace(-9, 9, 7) for y in np.linspace(-9, 9, 7)])
    assert np.any(np.abs(alb(p2, np.zeros_like(p2), np.zeros(len(p2))) - 0.7) > 0.1)  # on the tag: its pattern


def test_the_error_split_of_a_turn_about_the_rows():
    from ohs import geometry as G

    corners = {0: np.array([[-40.0, 0, 0], [-20, 0, 0], [-20, 20, 0], [-40, 20, 0]]),
               1: np.array([[20.0, 0, 0], [40, 0, 0], [40, 20, 0], [20, 20, 0]])}
    T = np.eye(4)
    T[:3, :3] = G.so3_exp(np.radians(0.5) * np.array([1.0, 0.0, 0.0]))
    s = mc.x19t_error_split(T, np.eye(4), np.array([[0.0, 10.0, 0.0]]), corners)
    assert s["turn deg"] == pytest.approx(0.5, abs=1e-6)
    assert abs(s["turn about the rows deg"]) == pytest.approx(0.5, abs=1e-6)
    assert abs(s["turn about the face normal deg"]) < 1e-6


def test_the_turned_part_keeps_its_prop_clear_of_the_upright_and_the_tags_on_the_outer_face():
    sc = mc.x19t_scene("bracket240tpt-s1")
    base = next(p for p in sc.primitives if p.name == "base")
    up = next(p for p in sc.primitives if p.name == "upright")
    prop = next(p for p in sc.primitives if p.name == "floor")
    y = lambda p: float((p.t - base.t) @ base.R[:, 1])  # noqa: E731
    assert y(up) < -20 and y(prop) > 25  # the prop under the base's free edge, the upright at the other
    assert len(sc.markers) == len(mc.X19T_ROWS["+"]) == 5
    rows = {round(float(((X.mean(0) - up.t) @ up.R[:, 1]))) for X in sc.markers.values()}
    assert rows == {-13, 13}  # two rows, not one line


def test_the_studys_recordings_stay_out_of_the_scorecards_sets():
    """A study's tagged recordings are registered as scenes so the judge finds their truth; they are in no set, so
    a scorecard run in the same process (the whole test suite) never takes them for its own."""
    from tools import scorecard as SC

    before = {n: [s.name for s in SC.scenes_for(n)] for n in SC.SETS}
    mc.x19_register(SC, ["bracket240t-s0", "bracket240tm-s1", "sheett-s2"])
    mc.x19t_register(SC, ["bracket240tpt-s1", "bracket240tp-s4"])
    assert SC.SCENES["bracket240tpt-s1"].group == "bracket240"  # the judge's truth is the untagged scene's
    assert {n: [s.name for s in SC.scenes_for(n)] for n in SC.SETS} == before
    assert [s.name for s in SC.scenes_for("core", ["bracket240"], [0, 1, 2])] == [f"bracket240-s{k}" for k in range(3)]
