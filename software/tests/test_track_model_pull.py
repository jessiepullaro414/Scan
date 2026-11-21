"""The Open3D tracking model's pull along a table (ENGINE_PLAN X0a, "No GPU: mend the tracking model's pull").

Without a GPU the camera path slid along the table on every scene: started at the true pose, ICP against the
Open3D model moved the camera 3.4 to 6.6 mm, against 0.4 to 1.2 mm with the GPU model. Three settings of the
Open3D model (the GPU model ignores them), each off unless set:

* ``FusionParams.model_normals = "vertex_map"``: the model's normals from its refined vertex map, not from the
  TSDF's gradients. At 2 mm voxels a 0.5 mm step blurs into a ramp several voxels wide whose gradient normals
  tilt; with exact depth of a 0.5 mm plate on a table, a frame against its own one-frame model moved 8 mm.
* ``FusionParams.raycast_border_mm``: the GPU model's border rule (``ohs.track.volume_gpu``, rule (iv)) on the
  Open3D ray cast: a hit is kept only when the observed surface extends that far around it.
* ``FusionParams.velocity_from_rest``: with the velocity guard, the first velocity after the start or a lost
  frame is bounded from rest (the guard's acceleration times the frame time), not by ``VELOCITY_MAX_SPEED``.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import geometry as G  # noqa: E402
from ohs.geometry import make_se3  # noqa: E402
from ohs.track.markerless import VELOCITY_MAX_ACCEL, FusionParams, MarkerlessTracker  # noqa: E402

W, H, FX = 640, 400, 360.0
K = np.array([[FX, 0, W / 2 - 0.5], [0, FX, H / 2 - 0.5], [0, 0, 1.0]])
PLATE = ((-40.0, 40.0), (-25.0, 25.0), 0.5)  # x range, y range, thickness (mm): a 0.5 mm plate on the table z = 0
TABLE_HALF = 400.0
DT = 1.0 / 30.0


def look_at(eye, target) -> np.ndarray:
    z = np.asarray(target, float) - np.asarray(eye, float)
    z = z / np.linalg.norm(z)
    up = np.array([0.0, 1.0, 0.0]) if abs(z[2]) > 0.99 else np.array([0.0, 0.0, 1.0])
    x = np.cross(z, up)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return G.make_se3(np.stack([x, y, z], axis=1), np.asarray(eye, float))


def plate_depth(T: np.ndarray) -> np.ndarray:
    """Exact depth (mm, 0 = none) of the plate on the table seen from ``T`` (camera to scene)."""
    vs, us = np.mgrid[0:H, 0:W].astype(np.float64)
    rays = np.stack([(us - K[0, 2]) / FX, (vs - K[1, 2]) / FX, np.ones_like(us)], -1).reshape(-1, 3)
    d = rays @ T[:3, :3].T
    o = T[:3, 3]
    (x0, x1), (y0, y1), th = PLATE
    with np.errstate(divide="ignore", invalid="ignore"):
        t_table = -o[2] / d[:, 2]
        t_top = (th - o[2]) / d[:, 2]
        top = o + t_top[:, None] * d
        on = (top[:, 0] > x0) & (top[:, 0] < x1) & (top[:, 1] > y0) & (top[:, 1] < y1) & (t_top > 0)
        t = np.where(on, t_top, t_table)
        hit = o + t_table[:, None] * d
        t[~on & ((np.abs(hit[:, 0]) > TABLE_HALF) | (np.abs(hit[:, 1]) > TABLE_HALF))] = 0.0
    z = t.reshape(H, W)
    z[~np.isfinite(z) | (z < 150.0) | (z > 450.0)] = 0.0
    return z.astype(np.float32)


def _tracker(**kw) -> MarkerlessTracker:
    return MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend="open3d", **kw))


def _self_move(T: np.ndarray, **kw) -> np.ndarray:
    """The frame at ``T`` against its own one-frame model, ICP started at the true pose: the move (scene frame, mm)."""
    tr = _tracker(**kw)
    d = plate_depth(T)
    tr.integrate(d, None, np.eye(4))
    view = tr._raycast(np.eye(4))
    res = tr._icp(tr._source_points(tr._clean_depth(d)), view, 1.0, np.radians(tr.params.gyro_sigma_deg), False)
    return T[:3, :3] @ res.T_delta[:3, 3]


def test_vertex_map_normals_end_the_pull_of_a_thin_plate_on_its_own_model():
    T = look_at((30.0, -200.0, 170.0), (0.0, 0.0, 0.0))
    before = _self_move(T)
    assert np.array_equal(before, _self_move(T, model_normals="gradient"))  # None is the gradients, as before
    after = _self_move(T, model_normals="vertex_map")
    # measured 2026-11-20: 7.96 mm along the table with the gradients, 0.50 with the vertex map
    assert np.hypot(*after[:2]) < 1.0 and np.hypot(*after[:2]) < 0.2 * np.hypot(*before[:2]), (before, after)
    with pytest.raises(ValueError):
        FusionParams(model_normals="sideways")


def test_vertex_map_normals_face_the_camera_and_match_a_plane():
    T = look_at((30.0, -200.0, 170.0), (0.0, 0.0, 0.0))
    tr = _tracker(model_normals="vertex_map")
    tr.integrate(plate_depth(T), None, np.eye(4))
    view = tr._raycast(np.eye(4))
    V, N = view.V[view.valid], view.N[view.valid]
    up_c = T[:3, :3].T @ np.array([0.0, 0.0, 1.0])  # the table's normal in the camera frame (towards the camera)
    assert np.all(np.einsum("ij,ij->i", N, V) < 0)  # towards the camera
    Xs = V @ T[:3, :3].T + T[:3, 3]
    (x0, x1), (y0, y1), _ = PLATE
    far = (np.abs(Xs[:, 0]) > x1 + 20) | (np.abs(Xs[:, 1]) > y1 + 20)  # table, away from the plate's edges
    ang = np.degrees(np.arccos(np.clip(N[far] @ up_c, -1, 1)))
    assert far.sum() > 1000 and np.median(ang) < 1.0, np.median(ang)


def _footprint_distance(T: np.ndarray, X: np.ndarray) -> np.ndarray:
    """In-plane distance (mm) of scene points X to the edge of the table the camera at ``T`` (looking straight
    down) saw: the image's footprint on the table."""
    corners = np.array([[-0.5, -0.5], [W - 0.5, -0.5], [W - 0.5, H - 0.5], [-0.5, H - 0.5]])
    rays = np.c_[(corners[:, 0] - K[0, 2]) / FX, (corners[:, 1] - K[1, 2]) / FX, np.ones(4)] @ T[:3, :3].T
    o = T[:3, 3]
    P = o[:2] + (-o[2] / rays[:, 2])[:, None] * rays[:, :2]
    lo, hi = P.min(axis=0), P.max(axis=0)  # an axis-aligned rectangle for a camera looking straight down
    return np.minimum.reduce([X[:, 0] - lo[0], hi[0] - X[:, 0], X[:, 1] - lo[1], hi[1] - X[:, 1]])


def test_the_border_rule_drops_hits_near_where_the_model_ends_and_keeps_the_thin_plate():
    T = look_at((0.0, 0.0, 250.0), (0.0, 0.0, 0.0))
    d = plate_depth(T)
    views = {}
    for b in (None, 8.0):
        tr = _tracker(raycast_border_mm=b)
        tr.integrate(d, None, np.eye(4))
        views[b] = tr._raycast(np.eye(4))
    base, cut = views[None], views[8.0]
    assert not np.any(cut.valid & ~base.valid)  # it only drops hits
    X = base.V[base.valid] @ T[:3, :3].T + T[:3, 3]
    dist = _footprint_distance(T, X)
    kept = cut.valid[base.valid]
    # within a voxel and a half of 8 mm either way (the probes take the nearest voxel)
    assert kept[dist > 8.0 + 3.0].all() and not kept[dist < 8.0 - 3.0].any()
    (x0, x1), (y0, y1), th = PLATE
    plate = (X[:, 2] > 0.25) & (X[:, 0] > x0) & (X[:, 0] < x1) & (X[:, 1] > y0) & (X[:, 1] < y1)
    assert plate.sum() > 1000 and kept[plate].all()  # the 0.5 mm plate's edges are no border: all of its top stays
    with pytest.raises(ValueError):
        FusionParams(raycast_border_mm=-1.0)


def test_the_gpu_model_ignores_the_open3d_settings():
    T = look_at((30.0, -200.0, 170.0), (0.0, 0.0, 0.0))
    d = plate_depth(T)
    maps = []
    for kw in ({}, {"model_normals": "vertex_map", "raycast_border_mm": 8.0, "velocity_from_rest": True}):
        try:
            tr = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend="gpu", **kw))
        except RuntimeError:
            pytest.skip("no GPU here")
        tr.integrate(d, None, np.eye(4))
        v = tr._raycast(np.eye(4))
        maps.append(np.array(v.maps, copy=True))
        tr.close()
    assert np.array_equal(maps[0], maps[1])


def _steps(tracker, xs_mm):
    tracker._accept(np.eye(4), 0.0, None)
    out = []
    for k, x in enumerate(xs_mm, start=1):
        tracker._accept(make_se3(np.eye(3), np.array([x, 0.0, 0.0])), k * DT, True)
        out.append(float(tracker._last_step[1][0]))
    return out


def test_a_first_velocity_from_rest():
    """The 0.5 mm plate's first step without a GPU: 6.9 mm from a prediction of zero (206 mm/s), let through."""
    step = VELOCITY_MAX_ACCEL * 1000.0 * DT * DT  # 2.2 mm: what the guard lets a velocity change in one frame
    tr = _tracker(velocity_max_accel=VELOCITY_MAX_ACCEL, velocity_from_rest=True)
    assert _steps(tr, [6.9, 13.8]) == [pytest.approx(step), pytest.approx(2 * step)]
    # off (the default): the first velocity is bounded by VELOCITY_MAX_SPEED only (6.9 mm a frame passes)
    assert _steps(_tracker(velocity_max_accel=VELOCITY_MAX_ACCEL), [6.9])[0] == pytest.approx(6.9)
    # after a lost frame it starts from rest again; a hand-held 1.4 mm a frame passes from the second frame on
    tr._accept(np.eye(4), 1.0, False)
    assert tr._v_w is None
    assert _steps(tr, [1.4, 2.8, 4.2]) == [pytest.approx(1.4)] * 3
    # without the guard the setting does nothing
    assert _steps(_tracker(velocity_from_rest=True), [6.9])[0] == pytest.approx(6.9)


def test_the_pipeline_passes_the_settings_to_both_tracking_models():
    from ohs.app.pipeline import FramePipeline, PipelineParams, default_depth_params, default_fusion_params
    from ohs.sim.nominal import nominal_calibration

    kw = {"model_normals": "vertex_map", "raycast_border_mm": 8.0, "velocity_from_rest": True}
    p = PipelineParams(scale=1.0, use_lasers=False, depth=replace(default_depth_params(1.0), backend="cpu"),
                       fusion=replace(default_fusion_params(1.0), model_backend="open3d", **kw))
    with FramePipeline(nominal_calibration(), p) as pipe:
        for k, v in kw.items():
            assert getattr(pipe.track_fusion_params, k) == v and getattr(pipe.tracker.params, k) == v
    # nothing set: off on every path (the defaults keep today's results until judged)
    with FramePipeline(nominal_calibration(), replace(p, fusion=replace(default_fusion_params(1.0),
                                                                        model_backend="open3d"))) as pipe:
        assert all(getattr(pipe.track_fusion_params, k) in (None, False, "gradient", 0) for k in kw)


def _noisy_plate_depth(T: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """``plate_depth`` with stereo-like noise (0.25 mm at 250 mm, growing with z^2) and 2 % of pixels lost."""
    z = plate_depth(T).astype(np.float64)
    z = z + (z > 0) * rng.normal(size=z.shape) * 0.25 * (z / 250.0) ** 2
    z[rng.random(z.shape) < 0.02] = 0.0
    return z.astype(np.float32)


def test_unsmoothed_vertex_map_normals_hold_a_noisy_plate():
    """Box-averaged over 5 x 5 pixels (model_normal_window, today's), the vertex map's normals still pulled 1.5 to
    3.8 mm on noisy depth; unsmoothed (vertex_map_window=1) 0.2 to 0.8 mm (measured 2026-11-20)."""
    T = look_at((30.0, -200.0, 170.0), (0.0, 0.0, 0.0))
    moves = []
    for seed in range(4):
        rng = np.random.default_rng(seed)
        tr = _tracker(model_normals="vertex_map", vertex_map_window=1)
        tr.integrate(_noisy_plate_depth(T, rng), None, np.eye(4))
        d1 = _noisy_plate_depth(T, rng)
        res = tr._icp(tr._source_points(tr._clean_depth(d1)), tr._raycast(np.eye(4)), 1.0,
                      np.radians(tr.params.gyro_sigma_deg), False)
        moves.append(float(np.hypot(*(T[:3, :3] @ res.T_delta[:3, 3])[:2])))
    assert max(moves) < 1.5, moves
    # the window: None is model_normal_window; it applies to the vertex map's normals only
    a, b = _tracker(model_normals="vertex_map"), _tracker(model_normals="vertex_map", vertex_map_window=5)
    d = plate_depth(T)
    for tr in (a, b):
        tr.integrate(d, None, np.eye(4))
    assert np.array_equal(a._raycast(np.eye(4)).N, b._raycast(np.eye(4)).N)
    with pytest.raises(ValueError):
        FusionParams(vertex_map_window=0)


def test_the_open3d_tracking_model_may_truncate_closer_than_the_mesh_model():
    from ohs.app.pipeline import FramePipeline, PipelineParams, default_depth_params, default_fusion_params
    from ohs.sim.nominal import nominal_calibration

    p = PipelineParams(scale=1.0, use_lasers=False, depth=replace(default_depth_params(1.0), backend="cpu"),
                       fusion=replace(default_fusion_params(1.0), model_backend="open3d"), track_trunc_mult=2.0)
    with FramePipeline(nominal_calibration(), p) as pipe:
        assert pipe.tracker.params.trunc_mult == 2.0 and pipe.track_fusion_params.trunc_mult == 2.0
        assert pipe.fusion_params.trunc_mult == default_fusion_params(1.0).trunc_mult  # the mesh's model keeps its own
    with FramePipeline(nominal_calibration(), replace(p, track_trunc_mult=None)) as pipe:  # None: as before
        assert pipe.track_fusion_params.trunc_mult == default_fusion_params(1.0).trunc_mult
    # the GPU tracking model has its own (gpu_trunc_mult): the setting leaves it alone
    with FramePipeline(nominal_calibration(), PipelineParams(use_lasers=False, track_trunc_mult=2.0)) as pipe:
        if pipe.model_backend != "gpu":
            pytest.skip("no GPU here")
        assert pipe.track_fusion_params.trunc_mult == default_fusion_params(1.0).trunc_mult
    with pytest.raises(ValueError):
        PipelineParams(track_trunc_mult=0.5)


def test_detail_mode_may_leave_the_cpu_matchers_image_border_out_of_its_raw_depth():
    """The detail-mode probe tracks the CPU matcher's raw depth, which reads 0.13 mm off within 24 px of the image
    border (VALIDATION.md, X0a): ``PipelineParams.detail_border_px`` (px at 1280 px wide) leaves that band out."""
    from ohs.app.pipeline import FramePipeline, PipelineParams, default_depth_params, default_fusion_params
    from ohs.sim.nominal import nominal_calibration

    p = PipelineParams(scale=1.0, use_lasers=False, depth=replace(default_depth_params(1.0), backend="cpu"),
                       fusion=replace(default_fusion_params(1.0), model_backend="open3d"), detail_border_px=24)
    with FramePipeline(nominal_calibration(), p) as pipe:
        w, h = pipe.image_size
        raw = np.full((h, w), 300.0, np.float32)
        out = pipe._detail_raw(raw)
        b = int(round(24 * w / 1280))
        assert (out[:b] == 0).all() and (out[-b:] == 0).all() and (out[:, :b] == 0).all() and (out[:, -b:] == 0).all()
        assert (out[b:-b, b:-b] == 300.0).all()
    with FramePipeline(nominal_calibration(), replace(p, detail_border_px=None)) as pipe:  # None: as before
        raw = np.full((pipe.image_size[1], pipe.image_size[0]), 300.0, np.float32)
        assert (pipe._detail_raw(raw) == 300.0).all()
    with pytest.raises(ValueError):
        PipelineParams(detail_border_px=-1)
    # the GPU matcher's raw depth is used as it is
    with FramePipeline(nominal_calibration(), PipelineParams(use_lasers=False, detail_border_px=24)) as pipe:
        if pipe.depth_estimator.backend != "gpu":
            pytest.skip("no GPU here")
        raw = np.full((pipe.image_size[1], pipe.image_size[0]), 300.0, np.float32)
        assert pipe._detail_raw(raw) is raw
