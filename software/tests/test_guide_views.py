"""Where to scan a weak area from (``ohs.guide.views``; FEATURE_PLAN N1), on made-up meshes.

* An open patch on a plate: the scanner square on to it at the working distance, its cameras'
  centre line through the patch's centre, seeing all of it.
* A roof over the patch, open on one side (10 mm and 0.5 mm thick): straight up is blocked, the
  pose leans out from under it and still sees most of the patch.
* The patch inside a closed box: "cannot be seen from outside the part", the part in the way.
* A normal that points into the part: every candidate would look from behind; no pose.
* An edge area: no pose (another pass does not lower that term).
* Thin parts: a sheet 0.06 mm above a point blocks its shadow ray, one 0.04 mm above does not (the
  ray starts 0.05 mm off the surface); a 0.5 mm plate's top face is seen from above, its bottom
  face faces away, and the table beside it is blocked from a camera low on the plate's far side.
"""
from __future__ import annotations

import numpy as np
import pytest

from mesh_shapes import box, grid
from ohs.guide import TILTS_DEG, scanner_pose, suggest_view, suggest_views
from ohs.mesh.check import RAY_OFFSET_MM, STEEP_COS, ScanViews, _ray_scene, _unblocked, weak_areas

pytest.importorskip("open3d")

K = np.array([[720.0, 0.0, 639.5], [0.0, 720.0, 399.5], [0.0, 0.0, 1.0]])
VIEWS = ScanViews(np.eye(4)[None], K, (1280, 800), 150.0, 450.0, 254.0, 60.0)
PATCH = (40.0, 60.0, 20.0, 40.0)


def _plate():
    V, F = grid(101, 61, 1.0)
    S = np.full(len(V), 0.02)
    m = (V[:, 0] >= PATCH[0]) & (V[:, 0] <= PATCH[1]) & (V[:, 1] >= PATCH[2]) & (V[:, 1] <= PATCH[3])
    S[m] = 0.2
    return V, F, S


def _add_box(V, F, size, centre):
    Vb, Fb = box(1.0, 4)
    return np.vstack([V, Vb * np.asarray(size, float) + np.asarray(centre, float)]), np.vstack([F, Fb + len(V)])


def _patch_area(V, F, S):
    r = weak_areas(V, F, S, tolerance_mm=0.1)
    a = [a for a in r["areas"] if abs(a["center"][0] - 50.0) < 2 and abs(a["center"][1] - 30.0) < 2]
    assert len(a) == 1
    return a[0]


def test_an_open_patch_is_scanned_square_on_at_the_working_distance():
    V, F, S = _plate()
    a = _patch_area(V, F, S)
    s = suggest_view(V, F, a, VIEWS)
    assert s["ok"] and s["share_seen"] > 0.99 and s["candidates"] == 1 + 8 * (len(TILTS_DEG) - 1)
    p = s["pose"]
    assert p["tilt_deg"] == 0.0 and p["distance_mm"] == 254.0
    assert np.allclose(p["look_dir"], [0.0, 0.0, -1.0], atol=1e-9)
    assert np.allclose(p["position_mm"], np.asarray(a["center"]) + [0.0, 0.0, 254.0], atol=1e-6)
    # the area's centre in camera C: on the cameras' centre line (x = baseline / 2), at the working distance
    T = np.asarray(p["T_wc"])
    c = (np.asarray(a["center"]) - T[:3, 3]) @ T[:3, :3]
    assert np.allclose(c, [30.0, 0.0, 254.0], atol=1e-6)
    assert np.allclose(T[:3, :3].T @ T[:3, :3], np.eye(3), atol=1e-12) and np.linalg.det(T[:3, :3]) > 0
    assert "254 mm" in s["text"] and "100 %" in s["text"]
    # the page's numbers are plain lists
    assert all(isinstance(p[k], list) and len(p[k]) == 3 for k in ("position_mm", "look_dir", "up", "target_mm"))


@pytest.mark.parametrize("thickness", [10.0, 0.5])
def test_a_roof_over_the_patch_makes_the_pose_lean_out_from_under_it(thickness):
    V, F, S = _plate()
    # a roof 25 mm over the plate, from x = 35 to 120 (the patch spans 40..60): open towards -x
    V2, F2 = _add_box(V, F, (85.0, 100.0, thickness), (77.5, 30.0, 25.0 + thickness / 2))
    S2 = np.concatenate([S, np.full(len(V2) - len(V), 0.02)])
    a = _patch_area(V2, F2, S2)
    s = suggest_view(V2, F2, a, VIEWS)
    assert s["ok"] and s["pose"]["tilt_deg"] > 0 and s["pose"]["look_dir"][0] > 0.3  # from -x, looking +x
    assert s["share_seen"] > 0.5, s
    # straight up is blocked by the roof
    from ohs.guide.views import _judge

    T = scanner_pose(np.asarray(a["center"]), np.array([0.0, 0.0, 1.0]), VIEWS)
    P = V2[np.unique(F2[a["faces"]])]
    j = _judge(T, P, np.tile([0.0, 0.0, 1.0], (len(P), 1)), VIEWS, _ray_scene(V2, F2))
    assert j["blocked"].mean() > 0.9


def test_a_patch_inside_a_closed_box_cannot_be_seen_from_outside():
    V, F, S = _plate()
    V2, F2 = _add_box(V, F, (40.0, 40.0, 20.0), (50.0, 30.0, 10.02))
    S2 = np.concatenate([S, np.full(len(V2) - len(V), 0.02)])
    a = _patch_area(V2, F2, S2)
    s = suggest_view(V2, F2, a, VIEWS)
    assert not s["ok"] and s["pose"] is None and s["share_seen"] == 0.0
    assert s["reason"].startswith("cannot be seen from outside the part") and "in the way" in s["reason"]
    assert s["missed"]["blocked"] > 0.9


def test_a_normal_into_the_part_gives_no_pose():
    V, F, S = _plate()
    a = dict(_patch_area(V, F, S), normal=[0.0, 0.0, -1.0])  # as if the area faced down, into the plate
    s = suggest_view(V, F, a, VIEWS)
    assert not s["ok"] and "faces into the part" in s["reason"] and s["missed"]["facing_away"] > 0.9


def test_an_edge_area_gets_no_pose_and_a_list_keeps_its_order():
    V, F, S = _plate()
    a = _patch_area(V, F, S)
    s = suggest_view(V, F, dict(a, reason="edge"), VIEWS)
    assert not s["ok"] and s["pose"] is None and "edge" in s["reason"]
    r = weak_areas(V, F, S, tolerance_mm=0.1)
    out = suggest_views(V, F, r, VIEWS)
    assert len(out) == r["n_areas"] and out[0]["ok"]
    assert suggest_views(V, F, [], VIEWS) == []


def test_a_small_fin_beside_the_patch_blocks_only_its_shadow():
    """A fin 1 mm thick and 10 mm tall along the patch's +x side: square on it is out of the way; from
    55 degrees towards +x it shades the strip of the patch within 10 * tan(55) = 14 mm of it."""
    from ohs.guide.views import _judge

    V, F, S = _plate()
    V2, F2 = _add_box(V, F, (1.0, 30.0, 10.0), (61.0, 30.0, 5.01))
    S2 = np.concatenate([S, np.full(len(V2) - len(V), 0.02)])
    a = _patch_area(V2, F2, S2)
    P = V2[np.unique(F2[a["faces"]])]
    Np = np.tile([0.0, 0.0, 1.0], (len(P), 1))
    scene = _ray_scene(V2, F2)
    j0 = _judge(scanner_pose(np.asarray(a["center"]), np.array([0.0, 0.0, 1.0]), VIEWS), P, Np, VIEWS, scene)
    # square on, only the strip right at its foot is in its shadow: the two cameras are 30 mm either side of
    # the centre line, about 7 degrees off the vertical, so 10 * tan(7 deg) = 1.2 mm of the patch
    assert not j0["blocked"][P[:, 0] < 60.5 - 1.5].any() and j0["blocked"][P[:, 0] > 60.5 - 1.0].all()
    d = np.array([np.sin(np.radians(55.0)), 0.0, np.cos(np.radians(55.0))])
    level = ScanViews(np.eye(4)[None], K, (1280, 800), 150.0, 450.0, 254.0, 60.0)
    level.up = np.array([0.0, 0.0, 1.0])  # the scanner held level: its two cameras side by side, along y here
    j = _judge(scanner_pose(np.asarray(a["center"]), d, level), P, Np, level, scene)
    shade = (P[:, 0] > 60.5 - 10.0 * np.tan(np.radians(55.0)) + 2.5) & (np.abs(P[:, 1] - 30.0) < 12.0)
    assert j["blocked"][shade].all()  # well inside the shadow (2.5 mm from its edge; the fin is 30 mm long)
    assert not j["blocked"][P[:, 0] < 60.5 - 10.0 * np.tan(np.radians(55.0)) - 2.5].any()


def test_a_plan_gives_one_pose_for_what_one_sees_and_more_directions_to_fill_up():
    from ohs.guide import plan_views

    V, F, S = _plate()
    m2 = (V[:, 0] >= 44) & (V[:, 0] <= 46) & (V[:, 1] >= 10) & (V[:, 1] <= 14)  # a small second patch near the first
    S[m2] = 0.2
    r = weak_areas(V, F, S, tolerance_mm=0.1)
    assert r["n_areas"] == 2
    plan = plan_views(V, F, r, VIEWS, 3)
    assert len(plan) == 3 and plan[0]["area"] == 0 and plan[0]["kind"] == "area"
    assert all(p["kind"] == "more" for p in plan[1:])  # the small patch is seen by the first pose already
    assert plan[0]["also_sees"].get(1, 0.0) >= 0.5
    dirs = [np.asarray(p["pose"]["look_dir"]) for p in plan]
    assert all(float(dirs[a] @ dirs[b]) < 0.999 for a in range(3) for b in range(a))  # three different directions
    assert plan_views(V, F, r, VIEWS, 0) == [] and plan_views(V, F, r, VIEWS, 2, select=[]) == []


def _sheet(h: float, half: float = 2.0):
    """Two triangles at z = h over the origin."""
    V = np.array([[-half, -half, h], [half, -half, h], [half, half, h], [-half, half, h]])
    return V, np.array([[0, 1, 2], [0, 2, 3]])


def test_the_thin_part_limit_of_the_shadow_ray():
    """The measured limit: an occluder 0.06 mm over the point blocks, one 0.04 mm over does not."""
    P, N, C = np.zeros((1, 3)), np.array([[0.0, 0.0, 1.0]]), np.array([0.0, 0.0, 250.0])
    assert RAY_OFFSET_MM == 0.05
    for h, blocked in ((0.5, True), (0.1, True), (0.06, True), (0.04, False), (0.01, False)):
        V, F = _sheet(h)
        assert _unblocked(_ray_scene(V, F), P, N, C)[0] == (not blocked), h


def test_a_half_millimetre_plate_on_its_table():
    """A plate 0.5 mm thick (a closed box) lying on a table: its top face is seen from above, its
    bottom face faces away from a camera above, and a strip of table right beside its edge is blocked
    for a camera low on the plate's far side but seen from above."""
    from ohs.guide.views import _judge

    V, F = grid(121, 81, 1.0)
    V = V - np.array([60.0, 40.0, 0.0])
    t = 0.5
    V2, F2 = _add_box(V, F, (40.0, 30.0, t), (0.0, 0.0, t / 2 + 0.001))
    scene = _ray_scene(V2, F2)
    top = np.array([[0.0, 0.0, t + 0.001]])
    bottom = np.array([[0.0, 0.0, 0.001]])
    beside = np.array([[20.3, 0.0, 0.0]])  # table, 0.3 mm beyond the plate's +x edge
    up = np.array([[0.0, 0.0, 1.0]])
    above = scanner_pose(np.zeros(3), np.array([0.0, 0.0, 1.0]), VIEWS)
    j = _judge(above, np.vstack([top, beside]), np.vstack([up, up]), VIEWS, scene)
    assert j["seen"].all()
    j = _judge(above, bottom, -up, VIEWS, scene)
    assert j["away"].all()
    # a camera from the far (-x) side, 55 degrees off the normal: its ray to the table beside the +x edge
    # passes 0.3 mm * tan(35 deg) ~ 0.2 mm over the table at the edge, below the plate's top: blocked
    d = np.array([-np.sin(np.radians(55.0)), 0.0, np.cos(np.radians(55.0))])
    low = scanner_pose(beside[0], d, VIEWS)
    j = _judge(low, beside, up, VIEWS, scene)
    assert j["blocked"].all()
    assert np.cos(np.radians(55.0)) > STEEP_COS  # not steep: blocked is the only reason here
