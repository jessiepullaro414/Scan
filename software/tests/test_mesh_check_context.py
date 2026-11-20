"""The Check's reasons and poses made faster without changing them (FEATURE_PLAN N1, the server's route).

* A :class:`ohs.mesh.check.CheckMesh` shared by several checks of one mesh gives each the same answer
  as a check on its own (reasons, poses, plan), for two tolerances in a row.
* The shadow rays of many poses cast at once give each pose the same answer as one pose at a time.
* The camera path's rules look at :data:`MAX_VIEW_FRAMES` frames at most, evenly spread (each scan of a
  merge its share), and the answer says how many; on a path whose frames repeat, the reasons are the
  same as with every frame.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from mesh_shapes import box, grid
from ohs.guide import plan_views, suggest_views
from ohs.guide.views import _judge, _judge_many, scanner_pose
from ohs.mesh.check import MAX_VIEW_FRAMES, CheckMesh, ScanViews, _ray_scene, spread_views, weak_areas
from ohs.sim.trajectory import look_at

pytest.importorskip("open3d")

K = np.array([[720.0, 0.0, 639.5], [0.0, 720.0, 399.5], [0.0, 0.0, 1.0]])
TARGET = np.array([50.0, 30.0, 0.0])


def _scene():
    """The plate with two weak patches, a roof over one of them and a fin: blocked, steep and open areas."""
    V, F = grid(101, 61, 1.0)
    S = np.full(len(V), 0.02)
    for x0, x1, y0, y1 in ((40, 60, 20, 40), (10, 18, 5, 12)):
        S[(V[:, 0] >= x0) & (V[:, 0] <= x1) & (V[:, 1] >= y0) & (V[:, 1] <= y1)] = 0.2
    Vb, Fb = box(1.0, 4)
    V2 = np.vstack([V, Vb * np.array([85.0, 100.0, 0.5]) + np.array([77.5, 30.0, 25.25]),
                    Vb * np.array([1.0, 10.0, 8.0]) + np.array([20.0, 8.0, 4.01])])
    F2 = np.vstack([F, Fb + len(V), Fb + len(V) + len(Vb)])
    S2 = np.concatenate([S, np.full(len(V2) - len(V), 0.02)])
    L = np.concatenate([np.full(len(V), 10.0), np.full(len(V2) - len(V), 10.0)])
    return V2, F2, S2, L


def _ring(off_deg: float, dist: float, n: int) -> np.ndarray:
    out = []
    for a in np.radians(np.linspace(-60.0, 60.0, n)):
        th = math.radians(off_deg)
        d = np.array([math.sin(th) * math.cos(a), math.sin(th) * math.sin(a), math.cos(th)])
        out.append(look_at(TARGET + dist * d, TARGET, up=(0.0, 0.0, 1.0)))
    return np.stack(out)


def _views(T: np.ndarray) -> ScanViews:
    return ScanViews(T, K, (1280, 800), 150.0, 450.0, 254.0, 60.0)


def _same(a, b) -> bool:
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, np.ndarray):
        return np.array_equal(a, b)
    return a == b or (isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b))


def test_a_shared_check_mesh_gives_the_same_answers():
    V, F, S, L = _scene()
    views = _views(np.concatenate([_ring(30.0, 250.0, 9), _ring(65.0, 300.0, 9)]))
    ctx = CheckMesh(V, F)
    for tol in (0.1, 0.05):  # a second tolerance on the same mesh: its parts are kept
        alone = weak_areas(V, F, S, tolerance_mm=tol, looks=L, views=views, voxel_mm=1.0)
        shared = weak_areas(V, F, S, tolerance_mm=tol, looks=L, views=views, voxel_mm=1.0, context=ctx)
        assert alone["n_areas"] >= 2 and _same(alone, shared)
        s0 = suggest_views(V, F, alone, views)
        s1 = suggest_views(V, F, shared, views, context=ctx)
        assert _same(s0, s1) and any(s["ok"] for s in s0)
        assert _same(plan_views(V, F, alone, views, 3, suggestions=s0), plan_views(V, F, shared, views, 3, suggestions=s1,
                                                                                   context=ctx))
    Vo, Fo = grid(11, 11, 1.0)
    with pytest.raises(ValueError, match="another mesh"):
        weak_areas(Vo, Fo, np.full(len(Vo), 0.2), tolerance_mm=0.1, context=ctx)


def _close(a, b, path="") -> list[str]:
    """Where two answers differ (floats to 1e-6, relative or absolute; ``faces`` left out)."""
    if isinstance(a, dict):
        if a.keys() != b.keys():
            return [f"{path}: keys {sorted(a)} vs {sorted(b)}"]
        return [x for k in a if k != "faces" for x in _close(a[k], b[k], f"{path}.{k}")]
    if isinstance(a, (list, tuple)):
        if len(a) != len(b):
            return [f"{path}: {len(a)} vs {len(b)} items"]
        return [x for i, (u, w) in enumerate(zip(a, b)) for x in _close(u, w, f"{path}[{i}]")]
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None or not math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-6):
            return [f"{path}: {a} vs {b}"]
        return []
    return [] if a == b else [f"{path}: {a!r} vs {b!r}"]


def test_the_answer_does_not_depend_on_how_the_mesh_numbers_its_vertices_and_faces():
    """The GPU path writes the same mesh in another vertex order from run to run: the areas, every reason
    (and its numbers), every pose and the plan must not change with it. The weak patch has more vertices
    than the samples the reasons (400) and the poses (300) take, the roof and the fin hide part of it."""
    V, F, S, L = _scene()
    m = (V[:, 0] >= 20) & (V[:, 0] <= 35) & (V[:, 1] >= 40) & (V[:, 1] <= 58) & (V[:, 2] == 0)
    S[m] = 0.2  # a third patch, open, by the plate's edge
    views = _views(np.concatenate([_ring(30.0, 250.0, 9), _ring(65.0, 300.0, 9)]))

    def answer(V, F, S, L):
        r = weak_areas(V, F, S, tolerance_mm=0.1, looks=L, views=views, voxel_mm=1.0)
        s = suggest_views(V, F, r, views)
        p = plan_views(V, F, r, views, 3, suggestions=s)
        content = [sorted(tuple(sorted(tuple(V[v]) for v in F[f])) for f in a["faces"]) for a in r["areas"]]
        return r, s, p, content

    r0, s0, p0, c0 = answer(V, F, S, L)
    assert r0["n_areas"] >= 3 and max(len(np.unique(F[a["faces"]])) for a in r0["areas"]) > 400
    rng = np.random.default_rng(11)
    for _ in range(2):
        perm = rng.permutation(len(V))  # new vertex k is old vertex perm[k]
        inv = np.empty(len(V), dtype=np.int64)
        inv[perm] = np.arange(len(V))
        F2 = inv[F][rng.permutation(len(F))]
        F2 = np.take_along_axis(F2, (rng.integers(0, 3, len(F2))[:, None] + np.arange(3)[None]) % 3, axis=1)  # same turn
        r1, s1, p1, c1 = answer(V[perm], F2, S[perm], L[perm])
        assert c1 == c0  # the same areas (by what their faces are), in the same order
        diff = _close(r1, r0) + _close(s1, s0, "poses") + _close(p1, p0, "plan")
        assert not diff, diff[:8]


def test_many_poses_at_once_give_each_its_own_answer():
    V, F, S, _ = _scene()
    scene = _ray_scene(V, F)
    P = V[(V[:, 2] == 0.0) & (V[:, 0] > 30) & (V[:, 0] < 70)]
    Np = np.tile([0.0, 0.0, 1.0], (len(P), 1))
    views = _views(np.eye(4)[None])
    Ts = [scanner_pose(np.asarray(TARGET), d / np.linalg.norm(d), views)
          for d in (np.array([0.0, 0.0, 1.0]), np.array([0.6, 0.0, 0.8]), np.array([-0.7, 0.2, 0.7]), np.array([1.0, 0.0, 0.05]))]
    many = _judge_many(Ts, P, Np, views, scene)
    for T, j in zip(Ts, many):
        assert _same(j, _judge(T, P, Np, views, scene))
    assert any(j["blocked"].any() for j in many) and any(j["seen"].any() for j in many)


def test_the_camera_path_is_looked_at_in_at_most_so_many_frames():
    T = _ring(30.0, 250.0, 500)
    v = _views(T)
    (s,), before, kept = spread_views(v)
    assert before == 500 and kept == len(s.T_wc) == MAX_VIEW_FRAMES == 120
    assert np.array_equal(s.T_wc[0], T[0]) and np.array_equal(s.T_wc[-1], T[-1])
    assert np.array_equal(s.up, v.up)  # the scanner's up stays that of the whole path
    gaps = np.diff([int(np.flatnonzero(np.all(T == x, axis=(1, 2)))[0]) for x in s.T_wc])
    assert gaps.min() >= 4 and gaps.max() <= 5  # evenly: one frame in 500 / 120
    # a merge: each scan its share of the frames, at least one
    a, b, c = _views(T[:300]), _views(T[300:]), _views(T[:2])
    out, before, kept = spread_views([a, b, c], 120)
    assert before == 502 and [len(x.T_wc) for x in out] == [72, 48, 1] and kept == 121
    # fewer frames than the cap: all of them, as given
    out, before, kept = spread_views([a], 1000)
    assert out[0] is a and before == kept == 300
    # the answer says it
    V, F, S, L = _scene()
    r = weak_areas(V, F, S, tolerance_mm=0.1, views=v)
    assert (r["reasons"]["frames_total"], r["reasons"]["frames_used"], r["reasons"]["frames_cap"]) == (500, 120, 120)
    r_all = weak_areas(V, F, S, tolerance_mm=0.1, views=v, max_frames=None)
    assert r_all["reasons"]["frames_used"] == 500 and r_all["reasons"]["frames_cap"] is None
    # frames that repeat (a scanner held still): the reasons are those of every frame
    still = _views(np.repeat(_ring(65.0, 300.0, 6), 50, axis=0))
    r1 = weak_areas(V, F, S, tolerance_mm=0.1, views=still)
    r2 = weak_areas(V, F, S, tolerance_mm=0.1, views=still, max_frames=None)
    assert r1["reasons"]["frames_used"] == 120
    assert [(a["reason"], a["reasons"]) for a in r1["areas"]] == [(a["reason"], a["reasons"]) for a in r2["areas"]]
    assert any(a["reason"] == "steep" for a in r1["areas"])
