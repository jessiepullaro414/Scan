"""ohs.align: an alignment from the shape is accepted only when it is sure (MAINTENANCE_PLAN F11).

We cannot know what the parts will be. The alignment should find the pose from whatever angle the
scans were taken when enough of the part is shared, and when it is not sure (a pose turned round
fits nearly as well, the guesses disagree, too little is shared) say so, carry the poses it had
in mind, and leave the choice to the person: never a guess presented as an answer.
"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import geometry as G  # noqa: E402
from ohs.align import AlignParams, align_pair, pose_from_point_pairs, scan_cloud  # noqa: E402
from test_align import _box, _error, _motion, _moved, _part  # noqa: E402


def _random_motion(rng) -> np.ndarray:
    """Any turn at all (not only small ones) and a shift."""
    q = rng.normal(size=4)
    q /= np.linalg.norm(q)
    w, x, y, z = q
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    return G.make_se3(R, rng.uniform(-150.0, 150.0, 3))


def _plate(rng, table=False):
    """A plain plate, 80 x 50 x 6 mm: it fits itself turned half round about each of its axes."""
    P, N = _box(rng, (0, 0, 0), (80, 50, 6), faces="xXyYzZ")
    if table:
        g = np.arange(-120.0, 200.0, 1.0)
        gx, gy = np.meshgrid(g, g)
        T = np.stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)], axis=1)
        T = T[~((T[:, 0] >= 0) & (T[:, 0] <= 80) & (T[:, 1] >= 0) & (T[:, 1] <= 50))]
        P, N = np.vstack([P, T]), np.vstack([N, np.tile([0.0, 0.0, 1.0], (len(T), 1))])
    return P, N


def _right(res, T_true, P, mm=0.3, deg=0.2) -> bool:
    e_mm, e_deg = _error(res.T, T_true)
    return e_mm < mm and e_deg < deg


# --------------------------------------------------------------------------- the table
def test_a_parts_own_face_is_not_taken_for_a_table():
    """A block scanned all round without a table: its largest face holds about a third of the
    surface, but the rest of the block lies behind it, so it is the part's own face and stays."""
    P, N = _box(np.random.default_rng(0), (0, 0, 0), (60, 40, 12), faces="xXyYzZ")
    c = scan_cloud(P, N)
    assert c.support is None and c.n_part > 0.95 * c.full.n, (c.support, c.n_part, c.full.n)
    assert c.support_note and "kept as part of the part" in c.support_note
    # aligned to itself moved: the whole block takes part
    T_true = _motion(75.0, (0.3, 1.0, 0.2), (40.0, -20.0, 15.0))
    Ps, Ns = _moved(*_box(np.random.default_rng(1), (0, 0, 0), (60, 40, 12), faces="xXyYzZ"), G.invert_se3(T_true))
    res = align_pair(scan_cloud(Ps, Ns), c)
    assert res.n_matched > 1000  # not "too little of the part": the part is there


def test_a_noisy_table_is_still_the_table():
    """A table whose mesh is noisy (0.8 mm, as Open3D's mesh of a short scan) has many points just
    outside its band behind it; only what lies well behind it counts against it."""
    rng = np.random.default_rng(2)
    P, N = _part(rng, table=True)
    on = np.abs(P[:, 2]) < 1e-6
    P[on, 2] += rng.normal(0.0, 0.8, int(on.sum()))
    c = scan_cloud(P, N)
    assert c.support is not None and c.support_share > 0.8 and c.n_part > 2000


def test_stray_bits_left_on_the_table_are_dropped():
    rng = np.random.default_rng(3)
    P, N = _part(rng, table=True)
    bits = np.vstack([np.array([x, y, 3.0]) + rng.normal(0.0, 0.5, (12, 3)) for x, y in ((-120, -100), (180, 150), (-100, 170))])
    c = scan_cloud(np.vstack([P, bits]), np.vstack([N, np.tile([0.0, 0.0, 1.0], (len(bits), 1))]))
    assert c.stray > 0
    far = np.linalg.norm(c.part.fine.pts[:, :2] - np.array([30.0, 15.0]), axis=1) > 100.0
    assert not far.any()


# --------------------------------------------------------------------------- sure or not
@pytest.mark.slow
def test_a_part_with_features_is_found_from_any_angle():
    """The L-shaped block with a boss and a rib, on its table and without one, turned by random
    poses of any angle: found every time, and sure of it."""
    rng = np.random.default_rng(4)
    found, unsure = 0, 0
    for k in range(8):
        table = k % 2 == 0
        T_true = _random_motion(rng)
        P, N = _part(np.random.default_rng(10 + k), table=table)
        Ps, Ns = _moved(*_part(np.random.default_rng(30 + k), table=table), G.invert_se3(T_true))
        res = align_pair(scan_cloud(Ps, Ns), scan_cloud(P, N))
        if res.accepted:
            assert _right(res, T_true, Ps), (k, _error(res.T, T_true), res.verdict)  # never a wrong pose accepted
            assert res.sure and res.doubt is None
            found += 1
        else:
            unsure += 1
            assert not res.sure and res.doubt and res.poses
    assert found == 8, (found, unsure)


@pytest.mark.slow
def test_a_symmetric_part_is_never_accepted_at_a_wrong_pose():
    """A plain plate turned by random poses: when the engine is not sure it says so and carries
    the poses it had in mind, the right one among them; when it does accept, it is right."""
    rng = np.random.default_rng(5)
    unsure = 0
    for k in range(6):
        T_true = _random_motion(rng)
        P, N = _plate(np.random.default_rng(50 + k))
        Ps, Ns = _moved(*_plate(np.random.default_rng(70 + k)), G.invert_se3(T_true))
        res = align_pair(scan_cloud(Ps, Ns), scan_cloud(P, N))
        if res.accepted:
            assert _right(res, T_true, Ps), (k, _error(res.T, T_true))
        else:
            unsure += 1
            assert res.sure is False and res.doubt in ("turned", "rival", "one_guess"), res.doubt
            assert res.verdict.startswith("Not aligned: not sure") and "pick" in res.verdict.lower()
            assert 2 <= len(res.poses) <= 6 and res.poses[0]["chosen"] and not any(p["chosen"] for p in res.poses[1:])
            assert any(_right(type(res)(T=np.asarray(p["T"])), T_true, Ps) for p in res.poses), "the right pose among them"
            d = res.as_dict()
            assert d["sure"] is False and len(d["poses"]) == len(res.poses) and np.asarray(d["poses"][0]["T"]).shape == (4, 4)
    assert unsure >= 5  # a plate's turned pose fits exactly as well: it must say so


def test_a_plain_plate_turned_half_round_is_not_sure():
    """Today's check misses this when every first guess falls on the same side: the plate turned
    half round fits exactly as well, so the answer is "not sure", with both poses."""
    T_true = _motion(180.0, (0.0, 0.0, 1.0), (80.0, 50.0, 0.0)) @ _motion(20.0, (0.2, 1.0, 0.1), (5.0, -3.0, 2.0))
    P, N = _plate(np.random.default_rng(8))
    Ps, Ns = _moved(*_plate(np.random.default_rng(9)), G.invert_se3(T_true))
    res = align_pair(scan_cloud(Ps, Ns), scan_cloud(P, N), AlignParams(ransac_seeds=2))
    assert not res.accepted and res.sure is False and res.doubt in ("turned", "rival"), res.verdict
    assert len(res.poses) >= 2 and res.poses[1]["pick"] >= 0.95 * res.poses[0]["pick"]


def test_too_little_shared_says_so():
    rng = np.random.default_rng(11)
    P, N = _part(rng, table=False)
    end = P[:, 0] > 50.0  # a sliver of one end
    res = align_pair(scan_cloud(P[end][::4], N[end][::4]), scan_cloud(*_part(np.random.default_rng(12), table=False)))
    assert not res.accepted and res.sure is False and res.doubt and res.verdict.startswith("Not aligned: ")


@pytest.mark.slow
def test_the_bracket_against_its_true_shape_with_stray_bits_on_the_table(tmp_path_factory):
    """The failure that started F11: the simulated bracket scan (its table set aside, a few bits
    left on it) against the bracket's true shape. Today 8 of 9 first guesses agree on a pose
    turned 180°, which is accepted. It must come out at the true pose, or not sure."""
    from dataclasses import replace

    from ohs.align import scan_cloud_from_mesh
    from ohs.inspect.reference import _sample_surface
    from ohs.mesh.topology import face_normals
    from test_inspect_reference import _bracket_reference, _bracket_scan

    scan = _bracket_scan(tmp_path_factory.mktemp("f11"))
    V, F = _bracket_reference()
    Q, face = _sample_surface(V, F, 0.5, 0)
    ref = scan_cloud(Q, face_normals(V, F)[0][face], replace(AlignParams(), support_min_share=2.0))
    src = scan_cloud_from_mesh(scan["mesh"])
    res = align_pair(src, ref)
    P = src.part.fine.pts
    D = P @ (res.T[:3, :3] - scan["world"][:3, :3]).T + (res.T[:3, 3] - scan["world"][:3, 3])
    off = float(np.sqrt(np.mean(np.sum(D * D, axis=1))))
    print(res.verdict, f"{off:.2f} mm from the true pose")
    if res.accepted:
        assert off < 2.0, (off, res.verdict)
    else:
        assert res.sure is False and res.poses


# --------------------------------------------------------------------------- picked points
def test_picked_points_are_checked_against_the_shapes():
    """Points picked well give a sure pose. Two picks swapped put the scan somewhere the shapes do
    not fit; the shapes fit clearly better elsewhere, and the result says the points disagree."""
    rng = np.random.default_rng(13)
    T_true = _motion(140.0, (0.2, 1.0, 0.3), (60.0, -20.0, 35.0))
    P, N = _part(rng)
    Ps, Ns = _moved(*_part(np.random.default_rng(14)), G.invert_se3(T_true))
    src, dst = scan_cloud(Ps, Ns), scan_cloud(P, N)
    on_dst = np.array([[0.0, 0.0, 40.0], [60.0, 0.0, 12.0], [14.0, 30.0, 40.0], [47.0, 20.0, 22.0]])
    on_src = (on_dst - T_true[:3, 3]) @ T_true[:3, :3] + rng.normal(0.0, 1.0, on_dst.shape)
    good, _ = pose_from_point_pairs(on_src, on_dst)
    res = align_pair(src, dst, init=good)
    assert res.accepted and res.sure and res.start == "given" and _right(res, T_true, Ps), res.verdict
    bad, _ = pose_from_point_pairs(on_src[[0, 2, 1, 3]], on_dst)  # two picks swapped
    res = align_pair(src, dst, init=bad)
    assert not res.accepted and res.doubt == "points", res.verdict
    assert "your points" in res.verdict and len(res.poses) >= 2
    assert any(_right(type(res)(T=np.asarray(p["T"])), T_true, Ps) for p in res.poses)
