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
