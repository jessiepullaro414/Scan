"""ohs.track.spacetime: "Refine from the images" (world-space spacetime stereo, a trial build).

* patches on a plane get its normal and no curvature, and none sits on the mesh's edge;
* vertices move onto the patches' surfaces where patches are, and stay where none are;
* the GPU kernels and their numpy twins agree (texture test and cost);
* on a simulated scan at the true poses, the refined mesh is closer to the true surfaces than
  the fused one where it was refined.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from mesh_shapes import grid
from ohs import geometry as G
from ohs.app.process import ProcessParams, process_recording
from ohs.compute import get_device
from ohs.io.recording import RecordingReader
from ohs.mesh.topology import vertex_normals
from ohs.sim.datasets import make_scan_recording
from ohs.track.export import load_ply
from ohs.track.spacetime import (Patches, SpacetimeParams, frames_from_recording, make_patches, patch_costs, patch_views,
                                 project_onto_patches, refine_from_images, resolve_params)
from test_e2e_scan import surface_distance


def test_patches_on_a_plane_have_its_normal_and_stay_off_the_edge():
    V, F = grid(61, 61, 1.0, z=lambda x, y: 0.002 * x)  # a 60 mm sheet, slightly tilted
    pa = make_patches(V, F, vertex_normals(V, F), spacing_mm=2.0, half_mm=2.0)
    n_true = np.array([-0.002, 0.0, 1.0]) / np.linalg.norm([-0.002, 0.0, 1.0])
    assert len(pa) > 400
    assert np.all(np.abs(pa.normal @ n_true) > 0.9999)
    assert np.abs(pa.geo[:, 15:18]).max() < 1e-6 and np.abs(pa.geo[:, 12]).max() < 1e-6  # flat, through the vertex
    P = pa.P
    assert P[:, 0].min() >= 2.0 and P[:, 0].max() <= 58.0 and P[:, 1].min() >= 2.0  # no patch hangs over the edge
    assert np.allclose(pa.point(np.full(len(pa), 0.3)) - P, 0.3 * pa.normal)


def test_vertices_move_onto_the_patches_and_nowhere_else():
    rng = np.random.default_rng(1)
    V, F = grid(81, 81, 1.0, z=lambda x, y: np.zeros_like(x))
    V[:, 2] += rng.normal(0.0, 0.05, len(V))  # a noisy fused surface around z = 0
    N = vertex_normals(V, F)
    # patches: the plane z = 0.2 over the left half only
    xs, ys = np.meshgrid(np.arange(4.0, 38.0, 2.0), np.arange(4.0, 77.0, 2.0))
    P = np.stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)], axis=1)
    geo = np.zeros((len(P), 18))
    geo[:, 0:3], geo[:, 3:6], geo[:, 6:9], geo[:, 9:12] = P, [1, 0, 0], [0, 1, 0], [0, 0, 1]
    move, cover = project_onto_patches(V, N, Patches(np.zeros(len(P), dtype=np.int64), geo), np.full(len(P), 0.2),
                                       half_mm=3.0, sigma_mm=2.0)
    V2 = V + move
    inside = (V[:, 0] > 8) & (V[:, 0] < 34) & (V[:, 1] > 8) & (V[:, 1] < 72)
    assert np.abs(V2[inside, 2] - 0.2).max() < 0.01 and cover[inside].min() > 0.999
    outside = V[:, 0] > 44
    assert np.all(move[outside] == 0.0) and np.all(cover[outside] == 0.0)
    assert np.allclose(V2[:, :2], V[:, :2])


@pytest.fixture(scope="module")
def scan(tmp_path_factory):
    """A 16-frame full-resolution simulated scan of the sphere on the table, fused at the true poses."""
    root = tmp_path_factory.mktemp("spacetime")
    rec = make_scan_recording(root / "rec", scene="sphere_on_plane", n_frames=16, image_size=(1280, 800), seed=4,
                              depth_every=0)
    reader = RecordingReader(rec)
    gt = reader.ground_truth_poses()
    first = min(gt)
    poses = {i: G.invert_se3(gt[first]) @ T for i, T in gt.items()}  # the mesh frame: the first camera
    process_recording(rec, root / "out", params=ProcessParams(export=("ply",), poisson=False), poses=poses)
    d = load_ply(root / "out" / "mesh.ply")
    L, R, T, K, B = frames_from_recording(str(rec), reader.calibration(), poses)
    return {"rec": rec, "reader": reader, "to_sim": gt[first], "V": d["vertices"].astype(np.float64),
            "F": d["faces"].astype(np.int64), "L": L, "R": R, "T": T, "K": K, "B": B}


def test_frames_from_recording_spreads_and_rectifies(scan):
    reader = scan["reader"]
    gt = reader.ground_truth_poses()
    first = min(gt)
    poses = {i: G.invert_se3(gt[first]) @ T for i, T in gt.items()}
    L, R, T, K, B = frames_from_recording(str(scan["rec"]), reader.calibration(), poses, max_frames=5)
    assert len(L) == len(R) == len(T) == 5 and L[0].shape == (800, 1280) and L[0].dtype == np.uint8
    assert np.allclose(T[0], poses[min(poses)]) and np.allclose(T[-1], poses[max(poses)])
    assert K[0, 0] > 500 and 55.0 < B < 65.0


@pytest.mark.skipif(get_device() is None, reason="no OpenCL GPU")
def test_the_gpu_and_the_cpu_agree(scan):
    V, F, L, R, T, K, B = (scan[k] for k in ("V", "F", "L", "R", "T", "K", "B"))
    size = (L[0].shape[1], L[0].shape[0])
    p = resolve_params(None, V, T, K, size)
    pa = make_patches(V, F, vertex_normals(V, F), p.spacing_mm, p.half_mm, fit=p.fit)
    vis = patch_views(pa, T, K, B, size, V, F, p)
    sel = np.flatnonzero(vis.sum(axis=1) >= 12)[:: 40][:150]
    sub = Patches(pa.vid[sel], pa.geo[sel])
    deltas = np.tile(np.linspace(-0.3, 0.3, 7), (len(sel), 1))
    t_cpu, C_cpu, W_cpu = patch_costs(sub, deltas, L, R, T, K, B, vis[sel], p, device="cpu")
    t_gpu, C_gpu, W_gpu = patch_costs(sub, deltas, L, R, T, K, B, vis[sel], p, device="gpu")
    assert t_cpu.sum() > 100
    assert np.mean(t_cpu != t_gpu) < 0.005  # a texture test right at the threshold may differ in float32
    same = np.all(t_cpu == t_gpu, axis=1)
    assert same.sum() > 0.9 * len(sel) and np.array_equal(W_cpu[same], W_gpu[same])
    m_cpu = C_cpu[same] / np.maximum(W_cpu[same], 1)
    m_gpu = C_gpu[same] / np.maximum(W_gpu[same], 1)
    assert np.abs(m_cpu - m_gpu).max() < 2e-3


def test_the_refined_mesh_is_closer_to_the_truth(scan):
    V, F, L, R, T, K, B = (scan[k] for k in ("V", "F", "L", "R", "T", "K", "B"))
    p = replace(SpacetimeParams(), min_views=8)
    gpu = get_device() is not None
    if not gpu:  # the CPU twin is slow: the middle of the scan only
        from ohs.mesh.topology import compact

        c = np.median(V, axis=0)
        inside = np.all(np.abs(V - c) < 30.0, axis=1)
        V, F, _ = compact(V, F[np.all(inside[F], axis=1)])
    V2, delta, rep = refine_from_images(V, F, L, R, T, K, B, p)
    assert rep["kept"] > (2000 if gpu else 30) and rep["frames"] == 16 and rep["covered_share"] > 0.15
    assert np.allclose(V2[np.abs(delta) == 0], V[np.abs(delta) == 0])
    toS = lambda X: X @ scan["to_sim"][:3, :3].T + scan["to_sim"][:3, 3]
    meta = scan["reader"].meta
    d0, _ = surface_distance(meta["scene"], toS(V), seed=int(meta.get("seed", 0)))
    d1, _ = surface_distance(meta["scene"], toS(V2), seed=int(meta.get("seed", 0)))
    moved = np.linalg.norm(V2 - V, axis=1) > 0.005
    assert np.median(d1[moved]) < 0.75 * np.median(d0[moved])
    assert np.percentile(d1[moved], 95) < np.percentile(d0[moved], 95)
    assert rep["moved"]["p95"] < 0.3


def test_projection_does_not_depend_on_the_chunk_size():
    rng = np.random.default_rng(2)
    V, F = grid(81, 81, 1.0, z=lambda x, y: 0.05 * np.sin(x / 7.0))
    V[:, 2] += rng.normal(0.0, 0.05, len(V))
    N = vertex_normals(V, F)
    xs, ys = np.meshgrid(np.arange(4.0, 60.0, 2.0), np.arange(4.0, 77.0, 2.0))
    P = np.stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)], axis=1)
    geo = np.zeros((len(P), 18))
    geo[:, 0:3], geo[:, 3:6], geo[:, 6:9], geo[:, 9:12] = P, [1, 0, 0], [0, 1, 0], [0, 0, 1]
    geo[:, 12:18] = rng.normal(0.0, 0.02, (len(P), 6))  # curved patches, so every term of the quadric counts
    patches = Patches(np.zeros(len(P), dtype=np.int64), geo)
    off = rng.normal(0.2, 0.05, len(P))
    assert len(V) > 2000
    ref_move, ref_cover = project_onto_patches(V, N, patches, off, half_mm=3.0, sigma_mm=2.0)
    assert np.any(ref_move != 0.0) and np.any(ref_cover == 0.0)
    for chunk in (1, 777, 2000, 25_000):
        move, cover = project_onto_patches(V, N, patches, off, half_mm=3.0, sigma_mm=2.0, chunk=chunk)
        assert np.array_equal(move, ref_move) and np.array_equal(cover, ref_cover)