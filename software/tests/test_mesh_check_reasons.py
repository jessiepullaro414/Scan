"""Why an area is weak (``ohs.mesh.weak_areas`` with looks, camera path and voxel size; FEATURE_PLAN N1).

Made-up meshes and camera paths whose answer is known:

* A 100 x 60 mm plate (1 mm grid) with one patch of sigma 0.2 (fails 0.1 mm), seen by cameras
  square on at the working distance: no reason holds ("unknown"); seen only from 70 degrees off
  its normal: "steep"; only from 400 mm: "far"; with a third of the looks of the rest: "few_looks";
  steep and few at once: "steep" first, both listed.
* A wall between the cameras and the patch (also a sheet 0.5 mm thick) blocks the square looks, so
  the steep ones that remain decide; outside every image: "unknown", with the share in view.
* A filled hole is "not_seen"; a sharp fold whose own curvature term is above the tolerance is
  "edge"; with nothing given every area is "unknown" and says what was missing.
* The limits: the steep angle from the matcher's sigma model (59.6 degrees), the working distance
  from the calibrated lasers (254 mm on the nominal scanner), "far" at sqrt(2) times that.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from mesh_shapes import grid
from ohs.mesh import fill_holes
from ohs.mesh.check import (FAR_RATIO, STEEP_COS, STEEP_DEG, ScanViews, steep_cos, weak_areas,
                            working_distance)
from ohs.sim.trajectory import look_at

pytest.importorskip("open3d")

NX, NY = 101, 61
K = np.array([[720.0, 0.0, 639.5], [0.0, 720.0, 399.5], [0.0, 0.0, 1.0]])
SIZE = (1280, 800)
PATCH = (40.0, 60.0, 20.0, 40.0)  # x0, x1, y0, y1 of the weak patch (mm)
TARGET = np.array([50.0, 30.0, 0.0])


def _plate():
    V, F = grid(NX, NY, 1.0)
    S = np.full(len(V), 0.02)
    m = (V[:, 0] >= PATCH[0]) & (V[:, 0] <= PATCH[1]) & (V[:, 1] >= PATCH[2]) & (V[:, 1] <= PATCH[3])
    S[m] = 0.2
    return V, F, S, m


def _ring(off_normal_deg: float, dist: float, n: int = 12, target=TARGET, spread_deg: float = 40.0) -> np.ndarray:
    """Cameras ``dist`` from ``target``, ``off_normal_deg`` from +z, over ``spread_deg`` of azimuth."""
    out = []
    for a in np.radians(np.linspace(-spread_deg / 2, spread_deg / 2, n)):
        th = math.radians(off_normal_deg)
        d = np.array([math.sin(th) * math.cos(a), math.sin(th) * math.sin(a), math.cos(th)])
        up = (0.0, 0.0, 1.0) if off_normal_deg > 5 else (0.0, 1.0, 0.0)
        out.append(look_at(target + dist * d, target, up=up))
    return np.stack(out)


def _views(T: np.ndarray, working: float = 250.0) -> ScanViews:
    return ScanViews(T, K, SIZE, 150.0, 450.0, working, 60.0)


def _area(r: dict) -> dict:
    assert r["n_areas"] == 1, [(a["kind"], a["area_mm2"]) for a in r["areas"]]
    return r["areas"][0]


def test_the_limits_come_from_the_matcher_and_the_calibration():
    from ohs.sim.nominal import nominal_calibration
    from ohs.vision.depth import SIGMA_MODELS

    m = SIGMA_MODELS["gpu"]
    assert STEEP_COS == pytest.approx(steep_cos(m)) and STEEP_DEG == pytest.approx(59.6, abs=0.1)
    # at the steep cosine the matcher's incidence factor is exactly 2
    assert 1.0 + m.inc_amp * math.exp(-(STEEP_COS / m.inc_cos) ** 2) == pytest.approx(2.0)
    cal = nominal_calibration((1280, 800))
    wd, src = working_distance(cal, 150.0, 450.0)
    assert wd == pytest.approx(254.0, abs=1.0) and "laser" in src
    cal.lasers = {}
    wd, src = working_distance(cal, 150.0, 450.0)
    assert wd == 300.0 and "middle" in src
    assert FAR_RATIO * 254.0 == pytest.approx(359.2, abs=0.1)
    v = ScanViews.from_calibration([(0, 0.0, True, np.eye(4)), (1, 0.1, False, np.eye(4))], nominal_calibration((640, 400)))
    assert len(v.T_wc) == 1 and v.working_mm == pytest.approx(254.0, abs=1.0) and v.image_size == (640, 400)


def test_square_looks_at_the_working_distance_leave_no_reason():
    V, F, S, _ = _plate()
    a = _area(weak_areas(V, F, S, tolerance_mm=0.1, views=_views(_ring(15.0, 250.0))))
    assert a["reason"] == "unknown" and a["reasons"] == []
    assert a["evidence"]["in_view_share"] == 1.0 and a["evidence"]["view_angle_deg"] < 25.0
    assert 240.0 < a["evidence"]["distance_mm"] < 250.0
    assert np.allclose(a["normal"], [0.0, 0.0, 1.0])
    assert any("few_looks" in u for u in a["unknown_because"]) and any("edge" in u for u in a["unknown_because"])


def test_seen_only_at_a_steep_angle():
    V, F, S, _ = _plate()
    r = weak_areas(V, F, S, tolerance_mm=0.1, views=_views(_ring(70.0, 250.0)))
    a = _area(r)
    assert a["reason"] == "steep" and a["reasons"] == ["steep"]
    assert 65.0 < a["evidence"]["view_angle_deg"] < 75.0 and "steep" in a["reason_text"]
    assert r["reasons"]["views"] and r["reasons"]["steep_deg"] == pytest.approx(STEEP_DEG)
    # 50 degrees off the normal is not steep
    assert _area(weak_areas(V, F, S, tolerance_mm=0.1, views=_views(_ring(50.0, 250.0))))["reason"] == "unknown"


def test_seen_from_too_far():
    V, F, S, _ = _plate()
    a = _area(weak_areas(V, F, S, tolerance_mm=0.1, views=_views(_ring(10.0, 400.0))))
    assert a["reason"] == "far" and a["evidence"]["distance_mm"] > FAR_RATIO * 250.0
    assert "400 mm" in a["reason_text"] or "39" in a["reason_text"]
    assert _area(weak_areas(V, F, S, tolerance_mm=0.1, views=_views(_ring(10.0, 330.0))))["reason"] == "unknown"


def test_seen_too_few_times():
    V, F, S, m = _plate()
    L = np.full(len(V), 12.0)
    L[m] = 4.0
    a = _area(weak_areas(V, F, S, tolerance_mm=0.1, looks=L))
    assert a["reason"] == "few_looks" and a["evidence"]["looks"] == 4.0 and a["evidence"]["looks_passing"] == 12.0
    assert "4 independent looks" in a["reason_text"] and "12" in a["reason_text"]
    L[m] = 8.0  # two thirds of the rest's: not too few
    assert _area(weak_areas(V, F, S, tolerance_mm=0.1, looks=L))["reason"] == "unknown"
    # steep and few at once: steep first, both listed
    L[m] = 4.0
    a = _area(weak_areas(V, F, S, tolerance_mm=0.1, looks=L, views=_views(_ring(70.0, 250.0))))
    assert a["reason"] == "steep" and a["reasons"] == ["steep", "few_looks"]
    with pytest.raises(ValueError, match="looks"):
        weak_areas(V, F, S, tolerance_mm=0.1, looks=L[:-1])


def _wall(V, F, x: float, thickness: float, height: float = 60.0):
    """A wall standing on the plate along y at ``x`` (two faces ``thickness`` apart, a closed box)."""
    from mesh_shapes import box

    Vb, Fb = box(1.0, 4)
    Vb = Vb * np.array([thickness, 80.0, height]) + np.array([x, 30.0, height / 2 + 0.01])
    return np.vstack([V, Vb]), np.vstack([F, Fb + len(V)]), len(Vb)


@pytest.mark.parametrize("thickness", [10.0, 0.5])
def test_a_wall_in_the_way_blocks_the_square_looks(thickness):
    V, F, S, _ = _plate()
    V2, F2, nb = _wall(V, F, 72.0, thickness)
    S2 = np.concatenate([S, np.full(nb, 0.02)])
    # square-on cameras leaning towards +x see the patch only past the wall's top... they are blocked
    square = _ring(25.0, 250.0, spread_deg=20.0)  # azimuth around +x: their rays to the patch cross the wall
    steep = _ring(70.0, 250.0)
    steep[:, :3, 3] = TARGET + (steep[:, :3, 3] - TARGET) * np.array([-1.0, 1.0, 1.0])  # from -x, the open side
    for T in steep:
        T[:3, :3] = look_at(T[:3, 3], TARGET)[:3, :3]
    a0 = _area(weak_areas(V, F, S, tolerance_mm=0.1, views=_views(np.concatenate([square, steep]))))
    assert a0["reason"] == "unknown"  # without the wall the square looks count
    a = _area(weak_areas(V2, F2, S2, tolerance_mm=0.1, views=_views(np.concatenate([square, steep]))))
    assert a["reason"] == "steep", a["evidence"]


def test_out_of_every_image_is_unknown():
    V, F, S, _ = _plate()
    T = _ring(10.0, 250.0, target=TARGET + np.array([400.0, 0.0, 0.0]))  # looking at something far away
    a = _area(weak_areas(V, F, S, tolerance_mm=0.1, views=_views(T)))
    assert a["reason"] == "unknown" and a["evidence"]["in_view_share"] == 0.0
    assert any("in view" in u for u in a["unknown_because"])


def test_a_filled_hole_is_not_seen_and_nothing_given_is_unknown():
    from mesh_shapes import punch

    V, F = grid(NX, NY, 1.0)
    V, F = punch(V, F, [(50.0, 30.0, 0.0)], [6.0])
    Vf, Ff = fill_holes(V, F, 200.0)
    filled = np.arange(len(Ff)) >= len(F)
    S = np.full(len(Vf), 0.02)
    r = weak_areas(Vf, Ff, S, filled, tolerance_mm=0.1, views=_views(_ring(15.0, 250.0)), looks=np.full(len(Vf), 9.0))
    a = _area(r)
    assert a["kind"] == "not_measured" and a["reason"] == "not_seen" and a["reasons"] == ["not_seen"]
    V, F, S, _ = _plate()
    r = weak_areas(V, F, S, tolerance_mm=0.1)
    a = _area(r)
    assert a["reason"] == "unknown" and len(a["unknown_because"]) == 3
    assert r["reasons"]["looks"] is False and r["reasons"]["views"] is False and r["reasons"]["edges"] is False


def test_an_edge_whose_own_term_is_above_the_tolerance():
    """A plate folded by 90 degrees along x = 50: at 1 mm voxels the curvature term there is about
    0.55 mm, so at 0.5 mm the fold is an edge; at 2 mm nothing fails for it."""
    V, F = grid(NX, NY, 1.0)
    x = V[:, 0].copy()
    V[:, 0] = np.minimum(x, 50.0)
    V[:, 2] = np.maximum(x - 50.0, 0.0)
    from ohs.mesh.topology import vertex_normals
    from ohs.track.recon_tsdf import mesh_sigma_terms

    N = vertex_normals(V, F)
    # the model's sigma with only its curvature term (the open border's term would make the plate's rim fail too)
    S = np.sqrt(0.05 ** 2 + mesh_sigma_terms(V, F, N, voxel_mm=1.0, parts=True)[0]).astype(np.float32)
    r = weak_areas(V, F, S, tolerance_mm=0.5, voxel_mm=1.0, normals=N)
    assert r["n_areas"] >= 1
    fold = [a for a in r["areas"] if abs(a["center"][0] - 50.0) < 3.0]
    assert fold and all(a["reason"] == "edge" for a in fold) and fold[0]["evidence"]["edge_share"] >= 0.5
    assert r["reasons"]["edges"]
    # the same areas without the voxel size: the data cannot tell
    r2 = weak_areas(V, F, S, tolerance_mm=0.5, normals=N)
    assert all(a["reason"] == "unknown" for a in r2["areas"])


def test_a_reason_a_second_pass_can_act_on_comes_before_the_edge():
    """The same fold, seen only from directions more than 59.6 degrees off both of its faces: "steep"
    comes first and the area gets a pose; "edge" is listed too. (Found in review on a real-size scan at
    0.1 mm: its largest area was steep, far and on the curvature term, and was told "no pose".)"""
    from ohs.guide.views import suggest_view
    from ohs.mesh.topology import vertex_normals
    from ohs.track.recon_tsdf import mesh_sigma_terms

    V, F = grid(NX, NY, 1.0)
    x = V[:, 0].copy()
    V[:, 0] = np.minimum(x, 50.0)
    V[:, 2] = np.maximum(x - 50.0, 0.0)
    N = vertex_normals(V, F)
    S = np.sqrt(0.05 ** 2 + mesh_sigma_terms(V, F, N, voxel_mm=1.0, parts=True)[0]).astype(np.float32)
    target = np.array([50.0, 30.0, 0.0])
    # 63 degrees off the flat face's normal (+z) and off the standing face's (-x), from both sides in y
    T = np.stack([look_at(target + 250.0 * np.array([-0.45, sy * 0.77, 0.45]), target) for sy in (-1.0, 1.0)])
    views = _views(T)
    r = weak_areas(V, F, S, tolerance_mm=0.5, voxel_mm=1.0, normals=N, views=views)
    fold = [a for a in r["areas"] if abs(a["center"][0] - 50.0) < 3.0]
    assert fold, [(a["center"], a["reason"]) for a in r["areas"]]
    a = fold[0]
    assert a["reason"] == "steep" and a["reasons"] == ["steep", "edge"], (a["reasons"], a["evidence"])
    s = suggest_view(V, F, a, views, normals=N)
    assert s["ok"] and s["pose"] is not None and s["share_seen"] > 0.5, s["text"]
    # with the edge alone (no camera path), still no pose
    r0 = weak_areas(V, F, S, tolerance_mm=0.5, voxel_mm=1.0, normals=N)
    a0 = [a for a in r0["areas"] if abs(a["center"][0] - 50.0) < 3.0][0]
    assert a0["reason"] == "edge" and not suggest_view(V, F, a0, views, normals=N)["ok"]


def test_without_why_the_answer_is_the_areas_alone():
    """``why=False`` (the Check route until it asks for reasons): the same areas, no reason keys."""
    V, F, S, _ = _plate()
    r = weak_areas(V, F, S, tolerance_mm=0.1)
    r0 = weak_areas(V, F, S, tolerance_mm=0.1, why=False)
    assert r0["reasons"] is None and "reason" not in r0["areas"][0] and "evidence" not in r0["areas"][0]
    skip = {"normal", "reason", "reasons", "reason_text", "evidence", "unknown_because", "faces"}
    assert {k: v for k, v in r["areas"][0].items() if k not in skip} == {k: v for k, v in r0["areas"][0].items() if k != "faces"}
    assert np.array_equal(r["areas"][0]["faces"], r0["areas"][0]["faces"])
    assert {k: v for k, v in r.items() if k not in ("areas", "reasons")} == {k: v for k, v in r0.items() if k not in ("areas", "reasons")}
