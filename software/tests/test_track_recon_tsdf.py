"""Tests for ohs.track.recon_tsdf.ConfidenceTsdf (synthetic depth of a plane).

* It averages like Open3D: with no sigmas its mesh is Open3D's, vertex for vertex.
* GPU and numpy twins agree (the GPU part is skipped without a GPU).
* The vertex sigma is honest: a scanner that doesn't move gets one independent look, however many
  frames it takes; one that moves gets one per dot spacing, and 2 sigma then covers the true error.
* The sigma model's parts: the scatter along the normal, observations next to pixels without depth
  (the distance to them, the same on the GPU and in numpy), scatter beyond the prediction, and the
  mesh's curvature and open border.
* The gap guard: depth next to a patch without depth is not fused (the matcher carries a surface a
  few pixels into a hole), the same on the GPU and in numpy; single missing pixels do not count.
* The mesh's sigma reaches a PLY file and back.
"""
import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import compute  # noqa: E402
from ohs.geometry import make_se3  # noqa: E402
from ohs.track.export import load_ply, save_mesh  # noqa: E402
from ohs.track.markerless import FusionParams, MarkerlessTracker  # noqa: E402
from ohs.track.recon_tsdf import (A_BORDER, A_CURV, A_GAP, A_SCATTER, BORDER_MM, C_NOISE,  # noqa: E402
                                  GUARD_PX_1280, ConfidenceTsdf, bias_scale, gap_guard_mask, mesh_sigma_terms,
                                  voxel_sigma)

W, H, FX = 320, 200, 180.0
K = np.array([[FX, 0.0, (W - 1) / 2], [0.0, FX, (H - 1) / 2], [0.0, 0.0, 1.0]])
Z0 = 300.0  # the world plane z = Z0, seen from cameras looking along +z


def plane_depth(T_wc: np.ndarray, noise: float = 0.0, rng=None) -> np.ndarray:
    vs, us = np.mgrid[0:H, 0:W].astype(np.float64)
    r = np.stack([(us - K[0, 2]) / FX, (vs - K[1, 2]) / FX, np.ones_like(us)], axis=-1)
    d = r @ T_wc[:3, :3].T
    lam = (Z0 - T_wc[2, 3]) / d[..., 2]  # camera z of the hit (rays have z = 1)
    if noise:
        lam = lam + rng.normal(0.0, noise, lam.shape)
    return lam.astype(np.float32)


def poses(n: int, step_mm: float) -> list[np.ndarray]:
    return [make_se3(np.eye(3), np.array([k * step_mm, 0.0, 0.0])) for k in range(n)]


def volume(backend: str, **kw) -> ConfidenceTsdf:
    return ConfidenceTsdf(K, (W, H), voxel_mm=2.0, trunc_mm=8.0, depth_min_mm=150.0, depth_max_mm=450.0, backend=backend,
                          **kw)


def test_without_sigmas_it_meshes_like_open3d():
    rng = np.random.default_rng(0)
    frames = [(plane_depth(T, 0.3, rng), T) for T in poses(6, 4.0)]
    ours = volume("cpu")
    o3 = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, depth_min_mm=150.0, depth_max_mm=450.0,
                                                   model_backend="open3d"))
    for d, T in frames:
        ours.integrate(d, None, T)
        o3.integrate(d, None, T)
    V, F, _ = ours.extract_mesh()
    Vo, Fo, _ = o3.extract_mesh()
    assert len(V) > 1000 and abs(len(V) - len(Vo)) <= 0.01 * len(Vo)
    from scipy.spatial import cKDTree

    dist, _ = cKDTree(Vo).query(V)
    # the same vertices, apart from the rim of the scanned area (the two allocate bricks a little differently)
    assert np.median(dist) < 1e-6 and np.mean(dist > 1e-3) < 0.03 and np.percentile(dist, 99.9) < 3 * 2.0  # 3 voxels


def test_gpu_and_numpy_agree():
    if compute.get_device() is None:
        pytest.skip(f"no GPU: {compute.status()}")
    rng = np.random.default_rng(1)
    frames = [(plane_depth(T, 0.3, rng), np.full((H, W), 0.4, np.float32), T) for T in poses(5, 5.0)]
    vols = [volume("cpu"), volume("gpu")]
    assert vols[1].backend == "gpu"
    for d, s, T in frames:
        for v in vols:
            v.integrate(d, s, T)
    (kc, c), (kg, g) = vols[0].download(), vols[1].download()
    assert np.array_equal(kc, kg)
    same = c["nobs"] == g["nobs"]
    assert same.mean() > 0.999  # a voxel can round to the neighbouring pixel in float32
    both = same & (c["nobs"] > 0)
    for ch in ("tsdf", "snh2", "nlook", "xc", "mxc", "hnear"):
        close = np.abs(c[ch] - g[ch])[both] <= 1e-3 * np.maximum(1.0, np.abs(c[ch][both]))
        assert close.mean() > 0.99, ch  # the rest sampled the neighbouring pixel once
    Vc, _, _, Sc = vols[0].extract_mesh(with_sigma=True)
    Vg, _, _, Sg = vols[1].extract_mesh(with_sigma=True)
    assert abs(len(Vc) - len(Vg)) <= 0.002 * len(Vc) and abs(np.median(Sc) - np.median(Sg)) < 1e-3


def gappy_frames(rng, n: int = 5) -> list:
    """Frames of the plane seen at 35 degrees, with what the sigma model looks at: single pixels and a
    block without depth, a strip out of range, and sigmas that vary, some of them NaN."""
    from scipy.spatial.transform import Rotation

    R = Rotation.from_euler("y", 35.0, degrees=True).as_matrix()
    out = []
    for k in range(n):
        T = make_se3(R, np.array([-200.0 + 5.0 * k, 0.0, 40.0]))
        d = plane_depth(T, 0.3, rng)
        d[rng.random(d.shape) < 0.02] = np.nan
        d[60:90, 120 + k:160 + k] = 0.0
        d[150:, :25] = 900.0
        s = (0.2 + 0.4 * rng.random(d.shape)).astype(np.float32)
        s[rng.random(d.shape) < 0.01] = np.nan
        out.append((d, s, T))
    return out


def test_gpu_and_numpy_agree_on_the_sigma_channels():
    if compute.get_device() is None:
        pytest.skip(f"no GPU: {compute.status()}")
    frames = gappy_frames(np.random.default_rng(4))
    vols = [volume("cpu"), volume("gpu")]
    for d, s, T in frames:
        for v in vols:
            v.integrate(d, s, T)
    (kc, c), (kg, g) = vols[0].download(), vols[1].download()
    assert np.array_equal(kc, kg)
    both = (c["nobs"] == g["nobs"]) & (c["nobs"] > 0)
    assert both.sum() > 20000 and (c["hnear"][both] > 0).mean() > 0.05  # the gaps are seen
    for ch in ("tsdf", "m2", "snh2", "nlook", "xc", "mxc", "hnear"):
        close = np.abs(c[ch] - g[ch])[both] <= 1e-3 * np.maximum(1.0, np.abs(c[ch][both]))
        assert close.mean() > 0.99, ch
    Pc, _, Sc = vols[0].extract_points(with_sigma=True)
    Pg, _, Sg = vols[1].extract_points(with_sigma=True)
    assert abs(len(Pc) - len(Pg)) <= 0.002 * len(Pc) and abs(np.median(Sc) - np.median(Sg)) < 2e-3
    assert abs(np.percentile(Sc, 90) - np.percentile(Sg, 90)) < 0.02 * np.percentile(Sc, 90)


def test_gap_distance_is_euclidean_and_the_same_on_the_gpu():
    """The squared distance (px) to the nearest pixel without depth, at most (R + 1)^2: numpy's equals
    a Euclidean distance transform, and the GPU's two kernels give the same numbers."""
    from scipy.ndimage import distance_transform_edt

    d, _, _ = gappy_frames(np.random.default_rng(5), 1)[0]
    d[20:25, 300:] = np.nan  # a gap at the image border
    v = volume("cpu")
    dz = np.where(np.isfinite(d) & (d >= 150.0) & (d <= 450.0), d, 0.0).astype(np.float32)
    g2 = v._gap2(dz)
    cap = (v._gap_r + 1) ** 2
    assert v._gap_r >= 15 and g2.dtype == np.float32
    assert np.array_equal(g2, np.minimum(np.rint(distance_transform_edt(dz > 0) ** 2), cap))
    full = np.full((H, W), 300.0, np.float32)  # no gap anywhere: the cap
    assert np.all(v._gap2(full) == cap)
    if compute.get_device() is None:
        return
    vg = volume("gpu", gap_guard_px=0.0)  # the distance alone, without the gap guard's marks
    for img, ref in ((d, g2), (full, v._gap2(full))):
        hold = vg._upload_images(np.ascontiguousarray(img), None)
        vg._queue_gaps(img.shape)
        got = np.empty((H, W), np.float32)
        vg.cl.enqueue_copy(vg.queue, got, vg._img_bufs[4], is_blocking=True)
        del hold
        assert np.array_equal(got, ref)
    vg.close()


def test_gap_guard_mask_and_its_gpu_twin():
    """The guard's rule, against an independent computation: a pixel without depth is part of a patch
    when at least 13 of the 5x5 around it (the border repeated) have none; a pixel with depth within
    the radius (Euclidean) of a patch is dropped. The GPU marks the same pixels."""
    from scipy.ndimage import convolve, distance_transform_edt

    rng = np.random.default_rng(8)
    d = np.full((H, W), 300.0, np.float32)
    d[rng.random(d.shape) < 0.03] = 0.0  # single gaps between dots: no patch
    d[40:70, 60:95] = 0.0  # a hole
    d[100:103, 200:203] = 0.0  # 3x3: too small to hold a patch pixel
    d[150:156, 250:256] = 0.0  # 6x6: its middle is a patch
    d[:9, 300:] = 0.0  # a patch in the image corner
    gap = ~(d > 0)
    seed = gap & (convolve(gap.astype(np.int32), np.ones((5, 5), np.int32), mode="nearest") >= 13)
    assert seed[55, 75] and seed[152:154, 252:254].all() and not seed[100:103, 200:203].any() and seed[2, 310]
    dist = distance_transform_edt(~seed)
    for r in (2.0, 2.5, 4.0, 8.0):
        m = gap_guard_mask(d, r)
        assert m.dtype == bool and np.array_equal(m, ~gap & (dist <= r)), r
    assert m[55, 52] and not m[55, 51] and not m[101, 198]  # 8 px from the hole; 9 px; next to the 3x3
    assert not gap_guard_mask(d, 0.0).any()
    lone = np.full((H, W), 300.0, np.float32)
    lone[rng.random(lone.shape) < 0.03] = 0.0
    assert not gap_guard_mask(lone, 8.0).any()  # no patch anywhere
    # off unless asked for; None asks for the measured radius, 8 px at 1280 px width
    assert GUARD_PX_1280 == 8.0 and volume("cpu").gap_guard_px == 0.0
    assert volume("cpu", gap_guard_px=None).gap_guard_px == pytest.approx(8.0 * W / 1280.0)
    if compute.get_device() is None:
        return
    for r in (2.0, 2.5, 8.0, 0.0):
        vg = volume("gpu", gap_guard_px=r)
        for img in (d, lone):
            hold = vg._upload_images(np.ascontiguousarray(img), None)
            vg._queue_gaps(img.shape)
            raw, got = np.empty((H, W), np.float32), np.empty((H, W), np.float32)
            vg.cl.enqueue_copy(vg.queue, raw, vg._img_bufs[4], is_blocking=True)
            del hold
            assert np.array_equal(raw, vg._gap2(img)), r  # the distance on the depth as it came
            if r > 0:  # and with what the guard drops counted as missing: 0 at the dropped pixels
                vg.cl.enqueue_copy(vg.queue, got, vg._img_bufs[7], is_blocking=True)
                mask = gap_guard_mask(img, r)
                assert np.array_equal(got, vg._gap2(np.where(mask, np.float32(0.0), img))), r
                assert np.array_equal((got == 0) & (img > 0), mask), r
        vg.close()


def holed_frames(rng, fill_mm: float, n: int = 10) -> list:
    """Frames of the plane with a hole of radius 25 mm around world (40, 0), of which every frame
    "sees" the first ``fill_mm`` as plane: what the matcher's windows do at an edge with nothing
    behind it."""
    vs, us = np.mgrid[0:H, 0:W].astype(np.float64)
    out = []
    for T in poses(n, 6.0):
        d = plane_depth(T, 0.1, rng)
        ray = np.stack([(us - K[0, 2]) / FX, (vs - K[1, 2]) / FX, np.ones_like(us)], axis=-1) @ T[:3, :3].T
        X = T[:3, 3] + ray * d[..., None]
        d[np.hypot(X[..., 0] - 40.0, X[..., 1]) < 25.0 - fill_mm] = 0.0
        out.append((d, np.full((H, W), 0.2, np.float32), T))
    return out


@pytest.mark.parametrize("backend", ["cpu", "gpu"])
def test_gap_guard_keeps_the_fill_out_of_the_mesh(backend):
    """A hole whose first 2 mm every frame fills in: without the guard the mesh reaches into the
    hole; with it (2 px = 3.3 mm here) the mesh stops outside the hole's true edge."""
    if backend == "gpu" and compute.get_device() is None:
        pytest.skip(f"no GPU: {compute.status()}")
    frames = holed_frames(np.random.default_rng(9), fill_mm=2.0)
    reach = {}
    for px in (None, 0.0):
        v = volume(backend, gap_guard_px=px)
        for d, sg, T in frames:
            v.integrate(d, sg, T)
        V, _, _ = v.extract_mesh()
        reach[px] = np.hypot(V[:, 0] - 40.0, V[:, 1]).min()
        assert len(V) > 5000
        v.close()
    assert reach[0.0] < 24.0  # the fill is in the mesh
    assert 25.0 < reach[None] < 29.0  # the hole's true edge is at 25 mm: no fill, and not much of the plane lost


def test_scatter_is_measured_along_the_normal_and_gaps_are_counted():
    """On a plane seen at an angle the scatter kept for the sigma is that of the distance along the
    normal: the camera-z scatter times the cosine of the ray with the normal, voxel by voxel. And the
    voxels next to a gap in the depth know it: all of their observations were next to a pixel without
    depth."""
    from scipy.spatial.transform import Rotation

    rng = np.random.default_rng(6)
    R = Rotation.from_euler("y", 35.0, degrees=True).as_matrix()
    v = volume("cpu", gap_guard_px=0.0)  # without the gap guard, which would not fuse what is counted here
    Ts = [make_se3(R, np.array([-180.0 + 6.0 * k, 0.0, 30.0])) for k in range(10)]
    vs, us = np.mgrid[0:H, 0:W].astype(np.float64)
    for T in Ts:
        d = plane_depth(T, 0.1, rng)
        ray = np.stack([(us - K[0, 2]) / FX, (vs - K[1, 2]) / FX, np.ones_like(us)], axis=-1) @ T[:3, :3].T
        X = T[:3, 3] + ray * d[..., None]
        d[np.abs(X[..., 0] - 30.0) < 12.0] = 0.0  # a strip of the plane gives no depth, in every frame
        # sigmas as the matcher gives them: 0.5 mm, times its factor next to missing depth
        g2 = v._gap2(np.where((d >= 150.0) & (d <= 450.0), d, 0.0).astype(np.float32))
        v.integrate(d, (0.5 * (1.0 + v.hole_amp * np.exp(-g2 / v.hole_r_px ** 2))).astype(np.float32), T)
    keys, ch = v.download()
    P = (keys[:, None, None, None, :] * 8 + np.stack(np.meshgrid(np.arange(8), np.arange(8), np.arange(8),
                                                               indexing="ij"), axis=-1)[None, ..., ::-1]) * v.voxel_mm
    x = P[..., 0]
    to_cam = P - 0.5 * (Ts[4][:3, 3] + Ts[5][:3, 3])
    cos_true = np.abs(to_cam[..., 2]) / np.linalg.norm(to_cam, axis=-1)
    surf = (ch["nobs"] >= 8) & (np.abs(ch["tsdf"]) < 2.0) & (np.abs(x - 30.0) > 40.0)
    ratio = np.sqrt(ch["mxc"][surf] / ch["m2"][surf])
    assert surf.sum() > 5000 and np.percentile(cos_true[surf], 5) < 0.65 < 0.9 < np.percentile(cos_true[surf], 95)
    assert np.median(np.abs(ratio - cos_true[surf])) < 0.03 and np.percentile(np.abs(ratio - cos_true[surf]), 95) < 0.1
    share = ch["hnear"] / np.maximum(ch["nobs"], 1.0)
    far = (ch["nobs"] >= 8) & (x < -10.0)  # well away from the strip, and from the far end of the depth range
    # on the surface, within 1 mm of the strip's edge (1.5 px, the model's "next to" here, are about 3 mm)
    near = (ch["nobs"] >= 3) & (np.abs(ch["tsdf"]) < 1.5) & (np.abs(np.abs(x - 30.0) - 12.0) < 1.0)
    assert far.sum() > 1000 and near.sum() > 100
    assert np.all(share[far] == 0.0) and np.median(share[near]) == 1.0
    Pt, _, S = v.extract_points(with_sigma=True)
    # the surface ends 1 to 2 mm before the strip; its last 2 mm are rated mostly by the gap term, and away
    # from the strip the factor is out of the sigmas again: no scatter beyond the prediction
    edge = np.abs(np.abs(Pt[:, 0] - 30.0) - 12.0) < 3.0
    assert edge.sum() > 200 and np.median(S[edge]) > 0.5 * A_GAP and np.percentile(S[Pt[:, 0] < -10.0], 95) < 0.2


def test_gpu_brick_search_finds_the_cpu_bricks():
    """The GPU's brick search gives the numpy search's bricks, in the same order; beyond its key
    range (512 bricks from the first camera) the volume searches on the CPU and still fuses."""
    if compute.get_device() is None:
        pytest.skip(f"no GPU: {compute.status()}")
    from scipy.spatial.transform import Rotation

    v = volume("gpu")
    rng = np.random.default_rng(2)
    for k, ang in enumerate((0.0, 12.0, -20.0, 35.0)):
        T = make_se3(Rotation.from_euler("xy", [ang, 0.5 * ang], degrees=True).as_matrix(), np.array([3.0 * k, -2.0 * k, 0.0]))
        d = plane_depth(T, 0.3, rng)
        d[40:60, 100:140] = np.nan  # holes and out-of-range depth are skipped like on the CPU
        d[150:, :30] = 900.0
        hold = v._upload_images(np.ascontiguousarray(d), None)
        gpu = v._frame_bricks_gpu(v._img_bufs[1], T, d.shape)
        v.queue.finish()
        del hold
        assert gpu is not None and len(gpu) > 50
        assert np.array_equal(gpu, v._frame_bricks(d, T)), ang
        v.integrate(d, None, T)
    far = make_se3(np.eye(3), np.array([0.0, 0.0, -2.0 * 512 * 8 * v.voxel_mm]))  # 2 x the key range behind
    d = plane_depth(far)
    d[:] = 300.0
    n0 = v.n_bricks
    v.integrate(d, None, far)
    assert v.n_bricks > n0  # fused, through the CPU search


def test_gpu_integration_needs_no_wait_after_a_frame():
    """Nothing has to be waited for after ``integrate``: the volume equals, bit for bit, the one that
    waits for the device after every frame; the caller's arrays are its own again once the call
    returns; and the pool can grow, and ``reset`` and ``close`` work, straight after a frame."""
    if compute.get_device() is None:
        pytest.skip(f"no GPU: {compute.status()}")
    rng = np.random.default_rng(3)
    # four frames side by side, then three over fresh ground (so the small pool has to grow there)
    Ts = poses(4, 5.0) + [make_se3(np.eye(3), np.array([600.0 + 5.0 * k, 0.0, 0.0])) for k in range(3)]
    frames = [(plane_depth(T, 0.3, rng), np.full((H, W), 0.4, np.float32), T) for T in Ts]

    def small() -> ConfidenceTsdf:
        return ConfidenceTsdf(K, (W, H), voxel_mm=2.0, trunc_mm=8.0, depth_min_mm=150.0, depth_max_mm=450.0,
                              backend="gpu", initial_bricks=64)

    waited, free = small(), small()
    caps = []
    for d, s, T in frames:
        waited.integrate(d, s, T)
        waited.queue.finish()
        d2, s2 = d.copy(), s.copy()
        free.integrate(d2, s2, T)
        d2[:] = 0.0
        s2[:] = 50.0
        caps.append(free.capacity)
    assert caps[-1] > caps[3]  # the pool grew straight after a frame
    (kw, cw), (kf, cf) = waited.download(), free.download()
    assert len(kw) > 500 and np.array_equal(kw, kf)
    for ch in cw:
        assert cw[ch].tobytes() == cf[ch].tobytes(), ch
    d, s, T = frames[0]
    free.integrate(d, s, T)
    free.reset()
    assert free.n_bricks == 0 and free.n_integrated == 0
    one = small()
    for v in (free, one):
        v.integrate(d, s, T)
    one.queue.finish()
    (kf, cf), (k1, c1) = free.download(), one.download()
    assert np.array_equal(kf, k1)
    for ch in c1:
        assert cf[ch].tobytes() == c1[ch].tobytes(), ch
    free.integrate(d, s, T)
    free.close()
    waited.close()
    one.close()


def test_sigma_counts_independent_looks_only():
    rng = np.random.default_rng(2)
    noise = 0.5
    sig = np.full((H, W), noise, np.float32)
    # a scanner that stands still: 10 frames are one look, the sigma stays that of one frame
    still = volume("cpu")
    for T in poses(10, 0.0):
        still.integrate(plane_depth(T, noise, rng), sig, T)
    _, ch = still.download()
    seen = ch["nobs"] >= 10
    assert np.all(ch["nlook"][seen] == 1)
    # the fused noise term: the model's share (C_NOISE) of one frame's scatter, not that over sqrt(10);
    # and 30 more frames from the same place do not lower it
    _, _, S = still.extract_points(with_sigma=True)
    assert 0.8 * C_NOISE * noise < np.median(S) < 1.2 * C_NOISE * noise
    for T in poses(30, 0.0):
        still.integrate(plane_depth(T, noise, rng), sig, T)
    _, _, S40 = still.extract_points(with_sigma=True)
    assert 0.9 * np.median(S) < np.median(S40) < 1.2 * C_NOISE * noise
    # one that moves 6 mm (3.6 px > the 2.5 px dot spacing at 320 px) per frame: a look per frame,
    # and 2 sigma covers the true error of the averaged surface
    moving = volume("cpu")
    for T in poses(10, 6.0):
        moving.integrate(plane_depth(T, noise, rng), sig, T)
    _, ch = moving.download()
    assert np.median(ch["nlook"][ch["nobs"] >= 8]) >= 7
    V, _, _, S = moving.extract_mesh(with_sigma=True)
    err = np.abs(V[:, 2] - Z0)
    inner = S < 0.3  # where many frames overlap
    assert inner.mean() > 0.3 and np.median(S[inner]) < 0.6 * noise
    assert 0.9 < np.mean(err[inner] < 2 * S[inner]) <= 1.0


def test_voxel_sigma_formula():
    four, zero = np.array([4.0]), np.array([0.0])
    # noise: 4 observations, 2 independent looks, normal-distance scatter 1.0 / 4 = 0.25 mm^2
    s = voxel_sigma(four, m2=zero, nobs=four, nlook=np.array([2.0]), mxc=np.array([1.0]), hnear=zero, snh2=zero)
    assert s[0] == pytest.approx(C_NOISE * np.sqrt(0.25 / 2))
    # half of the observations next to a gap: A_GAP * 0.5, added in quadrature
    s = voxel_sigma(four, m2=zero, nobs=four, nlook=np.array([2.0]), mxc=np.array([1.0]), hnear=np.array([2.0]),
                    snh2=zero)
    assert s[0] == pytest.approx(np.sqrt(C_NOISE ** 2 * 0.125 + (A_GAP * 0.5) ** 2))
    # the gap term is a length: it grows with the voxel size, and is never below the fitted 1 mm
    assert bias_scale(2.0) == 2.0 and bias_scale(1.0) == 1.0 and bias_scale(0.5) == 1.0
    s = voxel_sigma(four, m2=zero, nobs=four, nlook=np.array([2.0]), mxc=np.array([1.0]), hnear=np.array([2.0]),
                    snh2=zero, voxel_mm=2.0)
    assert s[0] == pytest.approx(np.sqrt(C_NOISE ** 2 * 0.125 + (A_GAP * 2.0 * 0.5) ** 2))
    # scatter beyond what the pixels' sigmas predict: observed 16 / 4 = 4, predicted 4 * 4 / 16 = 1
    s = voxel_sigma(four, m2=np.array([16.0]), nobs=four, nlook=four, mxc=zero, hnear=zero, snh2=four)
    assert s[0] == pytest.approx(A_SCATTER * np.sqrt(3.0))
    # scatter below the prediction adds nothing; an unobserved voxel gets 0
    s = voxel_sigma(np.array([4.0, 0.0]), m2=np.array([1.0, 0.0]), nobs=np.array([4.0, 0.0]), nlook=np.array([4.0, 0.0]),
                    mxc=np.array([0.0, 0.0]), hnear=np.array([0.0, 0.0]), snh2=np.array([4.0, 0.0]))
    assert s[0] == 0.0 and s[1] == 0.0


def _grid_mesh(nx: int, ny: int, fold_at: int | None = None) -> tuple:
    """A sheet of nx x ny vertices 1 mm apart (two triangles per cell); with ``fold_at``, bent by 90
    degrees along that column. Vertex normals: of the face they lie in, their mean on the fold."""
    ii, jj = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
    V = np.stack([ii, jj, np.zeros_like(ii)], axis=-1).astype(np.float64)
    N = np.zeros_like(V)
    N[..., 2] = 1.0
    if fold_at is not None:
        right = ii > fold_at
        V[right] = np.stack([np.full(right.sum(), fold_at), jj[right], -(ii[right] - fold_at)], axis=-1)
        N[right] = (1.0, 0.0, 0.0)
        N[ii == fold_at] = (np.sqrt(0.5), 0.0, np.sqrt(0.5))
    idx = (ii * ny + jj)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel(), idx[:-1, 1:].ravel()
    F = np.concatenate([np.stack([a, b, c], axis=1), np.stack([a, c, d], axis=1)])
    return V.reshape(-1, 3), F, N.reshape(-1, 3), ii.ravel(), jj.ravel()


def test_mesh_sigma_terms_border_and_curvature():
    # a flat sheet: no curvature; on the open border A_BORDER^2, falling off with the distance to it
    V, F, N, ii, jj = _grid_mesh(25, 25)
    t = mesh_sigma_terms(V, F, N)
    d = np.minimum.reduce([ii, jj, 24 - ii, 24 - jj]).astype(np.float64)
    assert np.allclose(t, (A_BORDER * np.exp(-d / BORDER_MM)) ** 2, atol=1e-12)
    t2 = mesh_sigma_terms(V, F, N, voxel_mm=2.0)  # at 2 mm voxels: twice the size, twice the reach
    assert np.allclose(t2, (2 * A_BORDER * np.exp(-d / (2 * BORDER_MM))) ** 2, atol=1e-12)
    # a sheet folded by 90 degrees: compare with the definition, vertex by vertex (sets, no matrices)
    V, F, N, ii, jj = _grid_mesh(25, 25, fold_at=12)
    t = mesh_sigma_terms(V, F, N)
    nb: list[set] = [set() for _ in V]
    for f in F:
        for k in range(3):
            nb[f[k]].update((f[(k + 1) % 3], f[(k + 2) % 3]))
    ring2 = [set().union(*(nb[j] for j in nb[i])) for i in range(len(V))]  # paths of two edges: i itself, 1-ring, 2-ring
    curv = np.zeros(len(V))
    for i, r in enumerate(ring2):
        m = N[sorted(r)].sum(0)
        curv[i] = 1.0 - N[i] @ (m / np.linalg.norm(m))
    c2 = np.array([max(curv[j] for j in r) for r in ring2])
    e = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
    u, n = np.unique(e, axis=0, return_counts=True)
    bv = np.unique(u[n == 1])
    dist = np.linalg.norm(V[:, None, :] - V[None, bv, :], axis=-1).min(1)
    assert np.allclose(t, A_CURV ** 2 * c2 + (A_BORDER * np.exp(-dist / BORDER_MM)) ** 2, atol=1e-9)
    mid = jj == 12
    assert t[mid & (ii == 12)].min() > 0.05 and t[mid & (np.abs(ii - 12) > 4) & (np.abs(ii - 12) < 8)].max() < 1e-3
    # nothing to do, and a vertex no face uses
    assert mesh_sigma_terms(np.zeros((0, 3)), np.zeros((0, 3), int), np.zeros((0, 3))).shape == (0,)
    V1 = np.vstack([V, [[99.0, 99.0, 99.0]]])
    t1 = mesh_sigma_terms(V1, F, np.vstack([N, [[0.0, 0.0, 1.0]]]))
    assert np.allclose(t1[:-1], t) and t1[-1] == pytest.approx(0.0, abs=1e-12)


def test_mesh_sigma_has_the_border_term():
    """``extract_mesh`` adds the mesh's own terms to the fused sigma: at the rim of the scanned patch
    the vertices are less sure than the points that only carry the fused part."""
    rng = np.random.default_rng(7)
    v = volume("cpu")
    for T in poses(10, 6.0):
        v.integrate(plane_depth(T, 0.2, rng), np.full((H, W), 0.2, np.float32), T)
    V, F, Nrm, S = v.extract_mesh(with_sigma=True)
    P, _, Sp = v.extract_points(with_sigma=True)
    terms = mesh_sigma_terms(V, F, Nrm, voxel_mm=v.voxel_mm)
    rim = terms > (0.9 * A_BORDER * bias_scale(v.voxel_mm)) ** 2
    assert rim.sum() > 100 and np.all(S[rim] >= 0.9 * A_BORDER * bias_scale(v.voxel_mm))
    assert np.all(S >= np.sqrt(terms) - 1e-6) and np.median(S) < 2.0 * np.median(Sp) + 0.05


def test_mesh_sigma_round_trips_through_ply(tmp_path):
    V = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0]])
    F = np.array([[0, 1, 2]])
    q = np.array([0.05, 0.2, 7.5], np.float32)
    save_mesh(tmp_path / "m.ply", V, F, quality=q)
    d = load_ply(tmp_path / "m.ply")
    assert np.allclose(d["quality"], q) and np.allclose(d["vertices"], V) and d["faces"].tolist() == [[0, 1, 2]]
    save_mesh(tmp_path / "plain.ply", V, F)
    assert load_ply(tmp_path / "plain.ply")["quality"] is None
    with pytest.raises(ValueError):
        save_mesh(tmp_path / "bad.ply", V, F, quality=q[:2])
