"""Tests for ohs.track.recon_tsdf.ConfidenceTsdf (synthetic depth of a plane).

* It averages like Open3D: with no sigmas its mesh is Open3D's, vertex for vertex.
* GPU and numpy twins agree (the GPU part is skipped without a GPU).
* The vertex sigma is honest: a scanner that doesn't move gets one independent look, however many
  frames it takes; one that moves gets one per dot spacing, and 2 sigma then covers the true error.
* The mesh's sigma reaches a PLY file and back.
"""
import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import compute  # noqa: E402
from ohs.geometry import make_se3  # noqa: E402
from ohs.track.export import load_ply, save_mesh  # noqa: E402
from ohs.track.markerless import FusionParams, MarkerlessTracker  # noqa: E402
from ohs.track.recon_tsdf import ConfidenceTsdf, voxel_sigma  # noqa: E402

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


def volume(backend: str) -> ConfidenceTsdf:
    return ConfidenceTsdf(K, (W, H), voxel_mm=2.0, trunc_mm=8.0, depth_min_mm=150.0, depth_max_mm=450.0, backend=backend)


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
    for ch in ("tsdf", "s2", "nlook"):
        close = np.abs(c[ch] - g[ch])[both] <= 1e-3 * np.maximum(1.0, np.abs(c[ch][both]))
        assert close.mean() > 0.99, ch  # the rest sampled the neighbouring pixel once
    Vc, _, _, Sc = vols[0].extract_mesh(with_sigma=True)
    Vg, _, _, Sg = vols[1].extract_mesh(with_sigma=True)
    assert abs(len(Vc) - len(Vg)) <= 0.002 * len(Vc) and abs(np.median(Sc) - np.median(Sg)) < 1e-3


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
    V, _, _, S = still.extract_mesh(with_sigma=True)
    assert 0.8 * noise < np.median(S) < 1.5 * noise
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
    # 4 observations, 2 independent looks, each sigma 1 mm, no scatter: var = 1 * 4 / 4^2 * 4 / 2 = 0.5
    s = voxel_sigma(np.array([4.0]), np.array([0.0]), np.array([4.0]), np.array([4.0]), np.array([2.0]))
    assert s[0] == pytest.approx(np.sqrt(0.5))
    # the observed scatter wins when it is larger; unobserved voxels get 0
    s = voxel_sigma(np.array([4.0, 0.0]), np.array([16.0, 0.0]), np.array([0.04, 0.0]), np.array([4.0, 0.0]),
                    np.array([1.0, 0.0]))
    assert s[0] == pytest.approx(2.0) and s[1] == 0.0


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
