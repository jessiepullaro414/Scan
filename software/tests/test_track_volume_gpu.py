"""Tests for ohs.track.volume_gpu (the GPU tracking TSDF) against its CPU twin, the Open3D model.

Synthetic depth (640x400, fx = 360, the half-resolution stream) of analytic scenes: tilted planes
and a sphere in front of a plane. The same frames are fused into a GPU-backed and an
Open3D-backed :class:`~ohs.track.markerless.MarkerlessTracker`; their model views (the ray-cast
maps ICP uses) are compared with each other and with the analytic truth rendered at the ray-cast
pixel centres (with the GPU ray cast set to Open3D's hit selection, so the same pixels compare).
Also covered: the default hit selection (border and weight rules), the rolling window (a shift
keeps the surface that stays inside and drops what leaves; a long sweep keeps tracking),
integration values and weights, extraction through an Open3D copy, and the backend choice. GPU
tests are skipped when ``ohs.compute`` finds no GPU (e.g. ``OHS_COMPUTE=cpu``), so CPU-only
machines pass.
"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import compute  # noqa: E402
from ohs import geometry as G  # noqa: E402
from ohs.track.markerless import FusionParams, MarkerlessTracker  # noqa: E402

W, H, FX = 640, 400, 360.0
K = np.array([[FX, 0, W / 2 - 0.5], [0, FX, H / 2 - 0.5], [0, 0, 1.0]])
SPHERE_C, SPHERE_R = np.array([20.0, -10.0, 230.0]), 45.0  # world: z forward, y down
ROW = [(np.array([x, 15.0 * (-1) ** (i % 2), 245.0]), 28.0) for i, x in enumerate(range(-200, 480, 70))]

gpu = pytest.mark.skipif(compute.get_device() is None, reason=f"no GPU: {compute.status()}")


def look_at(eye: np.ndarray, target: np.ndarray, up=(0.0, -1.0, 0.0)) -> np.ndarray:
    """T_wc of a camera at ``eye`` looking at ``target`` (OpenCV axes; ``up`` = world direction of -y)."""
    z = np.asarray(target, float) - eye
    z /= np.linalg.norm(z)
    x = np.cross(-np.asarray(up, float), z)
    x /= np.linalg.norm(x)
    return G.make_se3(np.stack([x, np.cross(z, x), z], axis=1), eye)


def render(T_wc: np.ndarray, Kc: np.ndarray, size: tuple[int, int], scene: str, rng=None) -> np.ndarray:
    """Depth (mm, 0 = none) at pixel centres of ``Kc``: a tilted plane ("plane"), with a sphere in
    front of it ("sphere") or with a row of small spheres along x ("row")."""
    w, h = size
    vs, us = np.mgrid[0:h, 0:w].astype(np.float64)
    yn = (vs - Kc[1, 2]) / Kc[1, 1]
    xn = (us - Kc[0, 2] - Kc[0, 1] * yn) / Kc[0, 0]
    d = np.stack([xn, yn, np.ones_like(xn)], -1).reshape(-1, 3) @ T_wc[:3, :3].T  # world dirs, z_cam = 1
    o = T_wc[:3, 3]
    n = np.array([0.25, -0.15, -1.0]) if scene != "row" else np.array([0.0, -0.2, -1.0])
    n /= np.linalg.norm(n)
    p0 = np.array([0.0, 0.0, 300.0])
    spheres = {"sphere": [(SPHERE_C, SPHERE_R)], "row": ROW}.get(scene, [])
    with np.errstate(divide="ignore", invalid="ignore"):
        t = ((p0 - o) @ n) / (d @ n)
        t[~(t > 0)] = np.inf
        a = np.einsum("ij,ij->i", d, d)
        for c, r in spheres:
            oc = o - c
            b = d @ oc
            disc = b * b - a * (oc @ oc - r ** 2)
            ts = (-b - np.sqrt(np.maximum(disc, 0.0))) / a
            ts[(disc < 0) | (ts <= 0)] = np.inf
            t = np.minimum(t, ts)
    z = t.reshape(h, w)
    z[~np.isfinite(z)] = 0.0
    if rng is not None:
        z = z + (z > 0) * rng.normal(scale=0.15, size=z.shape)
        z[rng.random(z.shape) < 0.01] = 0.0
    z[(z < 150.0) | (z > 450.0)] = 0.0
    return z.astype(np.float32)


def poses(n: int) -> list[np.ndarray]:
    """A short sweep around the sphere, ~2.5 deg and ~10 mm per frame (world-frame ``T_wc``)."""
    out = []
    for k in range(n):
        a = np.radians(-8 + 16 * k / max(n - 1, 1))
        eye = SPHERE_C + 230.0 * np.array([np.sin(a), 0.15 * np.sin(2 * a), -np.cos(a)])
        out.append(look_at(eye, SPHERE_C + np.array([0.0, 5.0 * np.sin(a), 0.0])))
    return out


O3D_RULE = {"gpu_border_mm": 0.0, "gpu_weight_threshold": 3.0}  # the GPU ray cast with Open3D's hit selection


def fused_pair(scene: str, n: int, noise: bool, seed: int = 0, **gpu_kw) -> tuple[MarkerlessTracker, MarkerlessTracker, list]:
    rng = np.random.default_rng(seed) if noise else None
    Ts = poses(n + 1)
    trs = [MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend="gpu", **gpu_kw)),
           MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend="open3d"))]
    for k in range(n):
        d = render(Ts[k], K, (W, H), scene, rng)
        for tr in trs:
            tr.integrate(d, None, Ts[k])
    return trs[0], trs[1], Ts


def views_and_truth(tg: MarkerlessTracker, to: MarkerlessTracker, T: np.ndarray, scene: str):
    vg, vo = tg._raycast(T), to._raycast(T)
    w, h = tg._ray_size
    truth = render(T, tg._K_ray, (w, h), scene).reshape(-1)
    gy, gx = np.gradient(truth.reshape(h, w))
    smooth = (truth > 0) & (np.hypot(gx, gy).reshape(-1) < 2.0)  # away from occlusion edges
    return vg, vo, truth, smooth


# --------------------------------------------------------------------------- ray cast vs Open3D
@gpu
@pytest.mark.parametrize("scene,noise", [("plane", False), ("plane", True), ("sphere", False), ("sphere", True)])
def test_raycast_matches_open3d_and_truth(scene, noise):
    """Same frames into both backends; the GPU ray cast with Open3D's hit selection must give
    the same surface, normals and frames, at least Open3D's coverage, and be closer to the truth."""
    tg, to, Ts = fused_pair(scene, 6, noise, **O3D_RULE)
    assert tg.model_backend == "gpu" and to.model_backend == "open3d"
    for T in (Ts[5], Ts[6]):  # the last fused pose and the next (unseen) one
        vg, vo, truth, smooth = views_and_truth(tg, to, T, scene)
        both = vg.valid & vo.valid & smooth
        assert both.sum() > 0.5 * smooth.sum(), (both.sum(), smooth.sum())
        assert vg.valid[smooth].mean() >= vo.valid[smooth].mean() - 0.02  # at least Open3D's coverage
        dz = vg.V[both, 2] - vo.V[both, 2]
        assert np.median(np.abs(dz)) < 0.5 and abs(np.mean(dz)) < 0.1, (np.median(np.abs(dz)), np.mean(dz))
        cos = np.einsum("ij,ij->i", vg.N[both], vo.N[both])
        assert np.median(np.degrees(np.arccos(np.clip(cos, -1, 1)))) < 2.0
        # same frames and units: camera-frame vertices, unit normals towards the camera
        assert np.allclose(np.linalg.norm(vg.N[vg.valid], axis=1), 1.0, atol=1e-5)
        assert np.all(np.einsum("ij,ij->i", vg.N[vg.valid], vg.V[vg.valid]) < 0)
        assert vg.K is tg._K_ray and vg.size == tg._ray_size and vg.V.dtype == np.float64
        # against the analytic surface: unbiased, and at least as close as Open3D's refined maps
        mg, mo = vg.valid & smooth, vo.valid & smooth
        eg, eo = vg.V[mg, 2] - truth[mg], vo.V[mo, 2] - truth[mo]
        assert abs(np.mean(eg)) < 0.05 and np.median(np.abs(eg)) < (0.12 if noise else 0.06), (np.mean(eg), np.median(np.abs(eg)))
        assert np.median(np.abs(eg)) <= np.median(np.abs(eo)) + 0.01


@gpu
def test_normals_follow_the_analytic_surface():
    """Model normals on the sphere, away from its silhouette (the 5x5 box average mixes in the
    plane behind it there, on both backends), against the analytic normals; and vs Open3D's."""
    import cv2

    tg, to, Ts = fused_pair("sphere", 6, noise=False, **O3D_RULE)
    T = Ts[5]
    w, h = tg._ray_size
    n_pl = np.array([0.25, -0.15, -1.0])
    n_pl /= np.linalg.norm(n_pl)
    errs = {}
    for name, tr in (("gpu", tg), ("open3d", to)):
        v = tr._raycast(T)
        P_w = v.V @ T[:3, :3].T + T[:3, 3]
        N_w = v.N @ T[:3, :3].T
        on_s = v.valid & (np.abs(np.linalg.norm(P_w - SPHERE_C, axis=1) - SPHERE_R) < 0.5)
        on_p = v.valid & (np.abs((P_w - np.array([0.0, 0.0, 300.0])) @ n_pl) < 0.5)
        for surf, on, truth in (("sphere", on_s, lambda P: (P - SPHERE_C) / SPHERE_R), ("plane", on_p, lambda P: n_pl)):
            inner = cv2.erode(on.reshape(h, w).astype(np.uint8), np.ones((7, 7), np.uint8)).reshape(-1).astype(bool)
            cos = np.einsum("ij,ij->i", N_w[inner], np.broadcast_to(truth(P_w[inner]), N_w[inner].shape))
            errs[name, surf] = np.degrees(np.arccos(np.clip(cos, -1, 1)))
    # measured: sphere 1.9 deg (Open3D 2.0; the stencil and box average on a 45 mm sphere), plane 0.12 (0.19)
    assert len(errs["gpu", "sphere"]) > 2000 and np.median(errs["gpu", "sphere"]) < 2.5
    assert len(errs["gpu", "plane"]) > 20000 and np.median(errs["gpu", "plane"]) < 0.3
    for surf in ("sphere", "plane"):
        assert np.median(errs["gpu", surf]) <= np.median(errs["open3d", surf]) + 0.1, surf
        assert len(errs["gpu", surf]) >= len(errs["open3d", surf])


@gpu
def test_default_selection_erodes_the_observed_borders():
    """Defaults: hits lie gpu_border_mm (8) inside the observed surface, on voxels fused often enough.

    The surface seen by the 6 frames ends at the image borders and at the sphere's occlusion
    shadow; hits near those borders (and on rarely seen voxels) are dropped, the interior stays
    and is unchanged.
    """
    rng = np.random.default_rng(5)
    Ts = poses(7)
    full = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend="gpu", **O3D_RULE))
    dflt = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend="gpu"))
    assert dflt.params.gpu_border_mm == 8.0 and dflt.params.gpu_weight_threshold == 8.0
    for k in range(6):
        d = render(Ts[k], K, (W, H), "sphere", rng)
        full.integrate(d, None, Ts[k])
        dflt.integrate(d, None, Ts[k])
    vf, vd = full._raycast(Ts[6]), dflt._raycast(Ts[6])
    assert 0.6 * vf.valid.sum() < vd.valid.sum() < vf.valid.sum()
    # where a pixel keeps its surface, the map is unchanged (a rejected first crossing can let the
    # ray go on to a surface behind, e.g. the plane next to the sphere's silhouette)
    both = vf.valid & vd.valid
    same = both & (np.abs(vd.V[:, 2] - vf.V[:, 2]) < 1e-3)
    assert same.sum() > 0.97 * both.sum() and not np.any(vd.valid & ~vf.valid)
    # normals: equal up to the box average, which now skips the dropped neighbours
    ang = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", vd.N[same], vf.N[same]), -1, 1)))
    assert np.median(ang) < 0.05 and np.percentile(ang, 99) < 3.0
    # what was dropped lies near the borders of the full coverage (within ~8 mm, i.e. a few pixels)
    import cv2

    w, h = full._ray_size
    dist = cv2.distanceTransform(vf.valid.reshape(h, w).astype(np.uint8), cv2.DIST_L2, 3).reshape(-1)
    dropped = vf.valid & ~vd.valid
    assert np.median(dist[dropped]) < 0.5 * np.median(dist[same])


# --------------------------------------------------------------------------- rolling window
@gpu
def test_rolling_shift_keeps_the_surface_and_drops_what_left():
    from ohs.track.volume_gpu import GpuTsdfVolume

    dev = compute.get_device()
    vol = GpuTsdfVolume(dev, 2.0, 8.0, 120.0, 500.0, edge_voxels=128, border_mm=0.0)  # 256 mm cube, 32 mm shifts
    T0 = np.eye(4)
    d0 = render(T0, K, (W, H), "plane")
    for _ in range(3):
        vol.integrate(d0, K, T0)
    o0 = vol.origin_voxels.copy()
    before = vol.raycast(T0, K, (W, H), 3.0)[:, 0]
    assert (before > 0).sum() > 20000
    T1 = G.make_se3(np.eye(3), np.array([70.0, 0.0, 0.0]))  # 70 mm to the right: 2 bricks
    assert vol.follow(T1) and vol.n_shifts == 1
    assert np.array_equal(vol.origin_voxels - o0, [32, 0, 0])
    after = vol.raycast(T0, K, (W, H), 3.0)[:, 0]
    kept = (before > 0) & (after > 0)
    # the surface still inside the window is untouched (no data moves in a shift; the march enters
    # the window elsewhere, so the refinement brackets differ by float32 rounding only) ...
    dz = np.abs(after[kept] - before[kept])
    assert kept.sum() > 0.5 * (before > 0).sum() and np.median(dz) < 1e-3 and dz.max() < 0.05, (np.median(dz), dz.max())
    # ... and what left the window (x < new origin) is gone
    xs = (np.arange(W)[None, :].repeat(H, 0).reshape(-1) - K[0, 2]) / FX * before
    lost = (before > 0) & (after == 0)
    x_cut = vol.origin_voxels[0] * 2.0
    assert lost.sum() > 1000 and np.all(xs[lost] < x_cut + 6.0) and np.all(xs[kept] > x_cut - 6.0)
    # the recycled slots hold no stale data: a fresh volume placed there sees the same model
    d1 = render(T1, K, (W, H), "plane")
    vol.integrate(d1, K, T1)
    ref = GpuTsdfVolume(dev, 2.0, 8.0, 120.0, 500.0, edge_voxels=128, border_mm=0.0)
    ref.follow(T1)
    assert np.array_equal(ref.origin_voxels, vol.origin_voxels)
    ref.integrate(d1, K, T1)
    ts_a, w_a = vol.download()
    ts_b, w_b = ref.download()
    new = np.zeros_like(w_a, dtype=bool)
    new[:, :, 128 - 32:] = True  # the slab that entered the window: only frame 1 there
    assert np.array_equal(w_a[new], w_b[new]) and np.allclose(ts_a[new], ts_b[new], atol=1e-4)
    assert (w_b[new] > 0).sum() > 1000


@gpu
def test_tracker_follows_a_long_sweep():
    """A 240 mm sideways sweep along a row of spheres with a 256 mm cube: the window rolls (and
    forgets what it left) while frame-to-model tracking stays accurate."""
    rng = np.random.default_rng(3)
    tr = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend="gpu", gpu_volume_voxels=128,
                                                   gyro_sigma_deg=0.02))
    T = [G.make_se3(G.so3_exp(np.array([0.0, 0.0015 * k, 0.0])), np.array([4.0 * k - 40.0, 0.0, 0.0]))
         for k in range(60)]
    for k, Tk in enumerate(T):
        R_prior = (T[k - 1][:3, :3].T @ Tk[:3, :3]) if k else None
        res = tr.track(render(Tk, K, (W, H), "row", rng), None, k / 30.0, R_prior)
        assert res.ok, (k, res.info.get("reason"))
    assert tr.gpu_volume.n_shifts >= 5
    T_est = T[0] @ res.T_wc  # the tracker's world is the first camera
    rot, trans = G.pose_error(T[-1], T_est)
    assert rot < 0.2 and trans < 1.5, (rot, trans)


# --------------------------------------------------------------------------- integration
@gpu
def test_integration_values_and_weights():
    from ohs.track.volume_gpu import GpuTsdfVolume

    dev = compute.get_device()
    vol = GpuTsdfVolume(dev, 2.0, 8.0, 120.0, 500.0, edge_voxels=128, max_weight=5)
    d = np.full((H, W), 251.0, np.float32)  # fronto-parallel plane
    d[:, :200] = 0.0  # invalid pixels: no update
    for _ in range(3):
        vol.integrate(d, K, np.eye(4))
    tsdf, wgt = vol.download()
    o = vol.origin_voxels
    # the voxel column through the image centre (x, y ~ 0)
    ix, iy = -o[0], -o[1]
    z = (o[2] + np.arange(vol.edge)) * 2.0  # world z of each voxel
    col_t, col_w = tsdf[:, iy, ix], wgt[:, iy, ix]
    sdf = 251.0 - z
    seen = (sdf >= -8.0) & (z > 0)
    assert np.all(col_w[seen] == 3) and np.all(col_w[~seen] == 0)
    assert np.allclose(col_t[seen], np.clip(sdf[seen], -8.0, 8.0) / 8.0, atol=2e-4)
    # voxels seen through the invalid left strip (at any depth of the column) were never observed
    u = K[0, 0] * ((o[0] + np.arange(vol.edge)) * 2.0) / 259.0 + K[0, 2]  # deepest seen voxel: 251 + trunc
    left = np.flatnonzero(u < 199.5 - 1.0)
    assert left.size > 10 and np.all(wgt[:, iy, left][seen] == 0)
    # weights saturate at max_weight; the running mean stays exact for a static scene
    for _ in range(4):
        vol.integrate(d, K, np.eye(4))
    _, wgt = vol.download()
    assert wgt.max() == 5 and np.all(wgt[:, iy, ix][seen] == 5)
    vol.scale_weights(0.5)
    _, wgt2 = vol.download()
    assert np.all(wgt2[:, iy, ix][seen] == 3)  # round(2.5)
    assert vol.n_observed_bricks() > 0
    vol.reset()
    assert vol.origin_voxels is None and vol.n_observed_bricks() == 0


@gpu
def test_extraction_through_open3d_copy():
    """extract_points / extract_mesh of a GPU model: an Open3D copy of the observed bricks."""
    tg, to, _ = fused_pair("sphere", 4, noise=False)
    for tr in (tg, to):
        P, N = tr.extract_points()
        r = np.abs(np.linalg.norm(P - SPHERE_C, axis=1) - SPHERE_R)
        near = r < 3.0
        assert near.sum() > 1000 and np.median(r[near]) < 0.2
    Vg, Fg, _ = tg.extract_mesh()
    Vo, Fo, _ = to.extract_mesh()
    assert len(Fg) > 0.8 * len(Fo) and Fg.max() < len(Vg)


# --------------------------------------------------------------------------- backend choice
def test_backend_choice_and_cpu_fallback(monkeypatch):
    with pytest.raises(ValueError):
        FusionParams(model_backend="cuda")
    with pytest.raises(ValueError):
        FusionParams(gpu_volume_voxels=100)
    assert MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend="open3d")).model_backend == "open3d"
    auto = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0))
    assert auto.model_backend == ("gpu" if compute.get_device() is not None else "open3d")
    monkeypatch.setenv(compute.ENV_VAR, "cpu")
    compute.reset()
    try:
        cpu = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0))
        assert cpu.model_backend == "open3d" and "CPU" in cpu.model_device and "turned off" in cpu.model_status
        with pytest.raises(RuntimeError, match="no GPU"):
            MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend="gpu"))
    finally:
        monkeypatch.undo()
        compute.reset()


