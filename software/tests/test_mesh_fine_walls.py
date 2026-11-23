"""Finer mesh detail on thin walls only (ENGINE_PLAN X5 step 2): the regions, the finer field, the conforming split,
the move onto the finer surface and its limits, a model limited to given bricks, the kept depth and the whole step."""
from __future__ import annotations

import json

import numpy as np
import pytest

from ohs.mesh.fine_walls import FineField, fine_bricks, in_bricks, refine_onto, region_bricks, subdivide
from ohs.mesh.topology import boundary_loops, face_normals


# --------------------------------------------------------------------------- helpers
def _sheet(x: float, outward: float, half: float = 15.0, step: float = 1.0, y0: float = 0.0, z0: float = 20.0):
    """A square sheet at ``x`` (a grid of ``step`` mm), its faces turned so that their normals point along
    ``outward`` (+1 or -1) on the x axis."""
    n = int(round(2 * half / step)) + 1
    ys, zs = np.meshgrid(np.linspace(y0 - half, y0 + half, n), np.linspace(z0 - half, z0 + half, n), indexing="ij")
    V = np.stack([np.full(ys.size, x), ys.ravel(), zs.ravel()], axis=1)
    idx = np.arange(n * n).reshape(n, n)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel(), idx[:-1, 1:].ravel()
    F = np.concatenate([np.stack([a, b, c], 1), np.stack([a, c, d], 1)])
    fn = face_normals(V, F)[0]
    if np.mean(fn[:, 0]) * outward < 0:
        F = F[:, [0, 2, 1]]
    return V, F


def _wall_mesh(half_reading: float, step: float = 1.0):
    """Both faces of a wall standing in the y-z plane, ``2 * half_reading`` apart, normals pointing out of it."""
    V1, F1 = _sheet(+half_reading, +1.0, step=step)
    V2, F2 = _sheet(-half_reading, -1.0, step=step)
    return np.concatenate([V1, V2]), np.concatenate([F1, F2 + len(V1)])


def _field(fn, lo: np.ndarray, hi: np.ndarray, voxel: float = 0.5, nobs: float = 10.0, sigma: float = 0.1) -> FineField:
    """A :class:`FineField` of the signed distance ``fn`` (positive outside the part) on every brick from ``lo`` to
    ``hi`` (mm)."""
    b = 8 * voxel
    k0, k1 = np.floor(np.asarray(lo) / b).astype(int), np.floor(np.asarray(hi) / b).astype(int)
    keys = np.stack(np.meshgrid(*[np.arange(k0[i], k1[i] + 1) for i in range(3)], indexing="ij"), -1).reshape(-1, 3)
    z, y, x = np.meshgrid(np.arange(8), np.arange(8), np.arange(8), indexing="ij")
    loc = np.stack([x, y, z], -1)  # [z, y, x] -> (x, y, z)
    P = (keys[:, None, None, None, :] * 8 + loc[None]) * voxel
    vals = fn(P.reshape(-1, 3)).reshape(len(keys), 8, 8, 8).astype(np.float32)
    ones = np.full(vals.shape, nobs, np.float32)
    return FineField(keys, vals, ones, voxel, 3.0, sigma=np.full(vals.shape, sigma, np.float32), looks=ones)


def _slab(t: float):
    return lambda P: np.abs(P[:, 0]) - t / 2.0


# --------------------------------------------------------------------------- the regions
def test_the_regions_are_the_flagged_bricks_grown_by_one_and_the_finer_bricks_tile_them():
    P = np.array([[0.5, 0.5, 0.5], [0.6, 0.4, 0.2], [20.1, 0.0, 0.0]])
    R0 = region_bricks(P, 8.0, grow=0)
    assert R0.tolist() == [[0, 0, 0], [2, 0, 0]]
    R1 = region_bricks(P, 8.0, grow=1)
    assert len(R1) == 27 + 27 - 9 and [0, 0, 0] in R1.tolist() and [-1, -1, -1] in R1.tolist()
    fb = fine_bricks(np.array([[0, 0, 0], [-1, 2, 3]]), 8.0, 4.0)
    assert len(fb) == 16 and {tuple(k) for k in fb} >= {(0, 0, 0), (1, 1, 1), (-2, 4, 6), (-1, 5, 7)}
    assert in_bricks(np.array([[7.9, 0.1, 0.1], [8.1, 0.1, 0.1]]), R0, 8.0).tolist() == [True, False]


# --------------------------------------------------------------------------- the split
def _closed_box():
    V = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], float) * 10.0
    F = np.array([[0, 2, 3], [0, 3, 1], [4, 5, 7], [4, 7, 6], [0, 1, 5], [0, 5, 4], [2, 6, 7], [2, 7, 3],
                  [0, 4, 6], [0, 6, 2], [1, 3, 7], [1, 7, 5]])
    return V, F


@pytest.mark.parametrize("which", [[0], [0, 1], [0, 2, 5], list(range(12))])
def test_the_split_keeps_the_mesh_closed_and_every_face_turned_as_before(which):
    V, F = _closed_box()
    split = np.zeros(len(F), bool)
    split[which] = True
    F2, edges, parent = subdivide(F, len(V), split, V)
    V2 = np.concatenate([V, 0.5 * (V[edges[:, 0]] + V[edges[:, 1]])])
    assert len(boundary_loops(V2, F2).perimeter) == 0  # no crack, no T-junction
    he = np.concatenate([F2[:, [0, 1]], F2[:, [1, 2]], F2[:, [2, 0]]])
    assert len(np.unique(he, axis=0)) == len(he)  # every directed edge once: no face turned over, none doubled
    assert np.isclose(face_normals(V2, F2)[1].sum(), face_normals(V, F)[1].sum())
    n0, _ = face_normals(V, F)
    n2, _ = face_normals(V2, F2)
    assert np.all((n2 * n0[parent]).sum(1) > 0.999)
    assert np.sum(split) * 4 <= len(F2) and len(edges) >= 3 * len(which) / 2
    whole = np.bincount(parent, minlength=len(F)) == 1  # faces that keep their three corners: first, as they were
    assert np.array_equal(F2[: int(whole.sum())], F[whole])


def test_an_open_border_stays_the_same_loop():
    V, F = _sheet(0.0, 1.0, half=5.0)
    split = np.zeros(len(F), bool)
    split[: len(F) // 2] = True
    F2, edges, _ = subdivide(F, len(V), split, V)
    V2 = np.concatenate([V, 0.5 * (V[edges[:, 0]] + V[edges[:, 1]])])
    l0, l2 = boundary_loops(V, F), boundary_loops(V2, F2)
    assert len(l0.perimeter) == len(l2.perimeter) == 1
    assert np.isclose(l0.perimeter[0], l2.perimeter[0])


# --------------------------------------------------------------------------- the move onto the finer surface
@pytest.mark.parametrize("t", [0.5, 1.0, 2.0])
def test_a_thin_wall_read_too_thick_moves_onto_the_finer_surface_and_keeps_both_faces(t):
    reading = t + 0.7  # what 1 mm mesh detail reads (VALIDATION.md: 0.5 / 1 / 2 mm walls read 1.7 / 1.4 to 2.7 mm)
    V, F = _wall_mesh(reading / 2)
    S = np.full(len(V), 0.3, np.float32)
    L = np.full(len(V), 2.0, np.float32)
    field = _field(_slab(t), [-6, -20, 0], [6, 20, 40])
    region = np.ones(len(F), bool)
    (V2, F2, N2, S2, L2), rep = refine_onto(V, F, None, S, L, region, field, coarse_voxel_mm=1.0)
    used = np.unique(F2)
    x = V2[used, 0]
    front, back = x[x > 0], x[x < 0]
    assert len(front) and len(back)  # both faces stay
    assert np.allclose(front, t / 2, atol=0.02) and np.allclose(back, -t / 2, atol=0.02)
    assert rep["loops"]["before"] == rep["loops"]["after"] == 2
    assert len(F2) == 4 * len(F) and rep["vertices"]["moved"] == len(V2)
    assert np.allclose(np.abs(N2[used, 0]), 1.0, atol=1e-3) and np.all(N2[used, 0] * x > 0)
    from ohs.track.recon_tsdf import mesh_sigma_terms

    # the finer model's sigma with the mesh's own terms (here mostly the open border of the test's small sheets)
    assert np.allclose(S2[used], np.sqrt(0.1 ** 2 + mesh_sigma_terms(V2, F2, N2, voxel_mm=0.5)[used]), atol=1e-4)
    assert np.allclose(L2[used], 10.0)


def test_outside_the_region_nothing_changes_and_the_seam_has_no_gap():
    V, F = _wall_mesh(0.85)
    S = np.arange(len(V), dtype=np.float32) * 1e-3
    field = _field(_slab(1.0), [-6, -20, 0], [6, 20, 40])
    c = V[F].mean(1)
    region = c[:, 1] < 0  # half of each face
    (V2, F2, N2, S2, _), rep = refine_onto(V, F, None, S, None, region, field, coarse_voxel_mm=1.0)
    touched = np.zeros(len(V), bool)
    touched[np.unique(F[region])] = True
    assert np.array_equal(V2[: len(V)][~touched], V[~touched]) and np.array_equal(S2[: len(V)][~touched], S[~touched])
    n_out = int((~region).sum())
    far = ~region & ~np.isin(F, np.unique(F[region])).any(1)  # faces not even next to the region
    assert {tuple(f) for f in F[far]} <= {tuple(f) for f in F2[: int((~region).sum())]}
    assert rep["loops"]["before"] == rep["loops"]["after"] and n_out > 0
    he = np.concatenate([F2[:, [0, 1]], F2[:, [1, 2]], F2[:, [2, 0]]])
    assert len(np.unique(he, axis=0)) == len(he)
    assert rep["seam_mm"]["max"] <= 0.36  # the faces moved 0.35 mm in the region; one row of faces takes the step


def test_no_vertex_jumps_onto_the_other_face_of_a_wall():
    """A 0.6 mm wall (faces at -0.3 and +0.3), read as a sheet facing +x at x = -0.2, just inside its back face: the
    back face, 0.1 mm away, crosses from outside to inside in the sheet's direction and is never taken; it moves onto
    the face it faces, the front."""
    V, F = _sheet(-0.2, +1.0)
    field = _field(_slab(0.6), [-6, -20, 0], [6, 20, 40])
    (V2, *_), rep = refine_onto(V, F, None, None, None, np.ones(len(F), bool), field, coarse_voxel_mm=1.0)
    assert np.allclose(V2[:, 0], 0.3, atol=0.02)


def test_a_surface_farther_than_the_limit_or_unseen_or_no_wall_in_the_finer_model_keeps_its_place():
    V, F = _wall_mesh(1.8)  # 1.55 mm from the finer surface of a 0.5 mm wall: beyond the 1 mm limit
    every = np.ones(len(F), bool)
    (V2, *_), rep = refine_onto(V, F, None, None, None, every, _field(_slab(0.5), [-6, -20, 0], [6, 20, 40]),
                                coarse_voxel_mm=1.0)
    assert np.allclose(np.abs(V2[np.unique(F), 0]), 1.8) and rep["vertices"]["moved"] == 0
    V, F = _wall_mesh(0.8)
    unseen = _field(_slab(1.0), [-6, -20, 0], [6, 20, 40], nobs=1.0)  # every voxel below the threshold
    (V3, *_), rep = refine_onto(V, F, None, None, None, every, unseen, coarse_voxel_mm=1.0)
    assert rep["vertices"]["moved"] == 0 and rep["changed"] is False and np.array_equal(V3[: len(V)], V)
    # the finer model holds no wall there (nothing inside): the 1 mm faces stay, the wall is not taken away
    no_wall = _field(lambda P: np.abs(P[:, 0]) + 0.05, [-6, -20, 0], [6, 20, 40])
    (V4, *_), rep = refine_onto(V, F, None, None, None, every, no_wall, coarse_voxel_mm=1.0)
    assert rep["vertices"]["moved"] == 0 and np.allclose(np.abs(V4[np.unique(F), 0]), 0.8)


def test_a_thin_plate_lying_flat_moves_onto_its_finer_top_and_is_not_lost():
    """A 0.5 mm plate lying flat, its top read 0.8 mm high: it moves onto the finer top at 0.5 mm, and no lower."""
    V, F = _sheet(0.0, 1.0)
    V = V[:, [1, 2, 0]].copy()  # in the x-y plane
    V[:, 2] = 0.8
    if face_normals(V, F)[0][:, 2].mean() < 0:
        F = F[:, [0, 2, 1]]
    field = _field(lambda P: P[:, 2] - 0.5, [-20, -20, -4], [20, 40, 6])
    (V2, *_), rep = refine_onto(V, F, None, None, None, np.ones(len(F), bool), field, coarse_voxel_mm=1.0)
    assert np.allclose(V2[np.unique(F), 2], 0.5, atol=0.02)


# --------------------------------------------------------------------------- a model limited to given bricks
W, H, FX = 320, 200, 180.0
K = np.array([[FX, 0.0, (W - 1) / 2], [0.0, FX, (H - 1) / 2], [0.0, 0.0, 1.0]])


def _slab_view(T_wc: np.ndarray, x_face: float, half: float = 20.0, z0: float = 20.0) -> np.ndarray:
    """Depth (mm) of the plane ``x = x_face`` seen from ``T_wc``, within ``half`` of (y 0, z ``z0``); 0 elsewhere."""
    vs, us = np.mgrid[0:H, 0:W].astype(np.float64)
    r = np.stack([(us - K[0, 2]) / FX, (vs - K[1, 2]) / FX, np.ones_like(us)], axis=-1)
    d = r @ T_wc[:3, :3].T
    lam = (x_face - T_wc[0, 3]) / d[..., 0]
    p = T_wc[:3, 3] + lam[..., None] * d
    ok = (lam > 0) & (np.abs(p[..., 1]) < half) & (np.abs(p[..., 2] - z0) < half)
    return np.where(ok, lam, 0.0).astype(np.float32)


def _views(t: float, n: int = 4):
    """Cameras 300 mm either side of a wall ``t`` thick (in the y-z plane), looking at it; their depth maps."""
    out = []
    for k in range(n):
        for side in (+1.0, -1.0):
            zc = np.array([-side, 0.0, 0.0])
            xc = np.array([0.0, 1.0, 0.0]) * side
            yc = np.cross(zc, xc)
            T = np.eye(4)
            T[:3, :3] = np.stack([xc, yc, zc], axis=1)
            T[:3, 3] = [side * 300.0, 2.0 * k - 3.0, 20.0 + 1.5 * k]
            out.append((T, _slab_view(T, side * t / 2)))
    return out


def _backends() -> list[str]:
    from ohs import compute

    return ["cpu"] + (["gpu"] if compute.get_device() is not None else [])


@pytest.mark.parametrize("backend", ["cpu", "gpu"])
def test_a_model_limited_to_bricks_fuses_those_bricks_exactly_as_a_whole_model_and_no_other(backend):
    pytest.importorskip("open3d")
    from ohs.track.recon_tsdf import ConfidenceTsdf

    if backend not in _backends():
        pytest.skip("no GPU")
    kw = dict(voxel_mm=0.5, trunc_mm=2.0, depth_min_mm=150.0, depth_max_mm=450.0, backend=backend, back_mm=1.0,
              opposite_mm=0.5)
    whole, part = ConfidenceTsdf(K, (W, H), **kw), ConfidenceTsdf(K, (W, H), **kw)
    allowed = np.array([[0, 0, 4], [0, 1, 4], [-1, 0, 5], [-1, -1, 3]])
    part.limit_bricks(allowed)
    for T, d in _views(1.0):
        s = np.where(d > 0, 0.2, 0.0).astype(np.float32)
        whole.integrate(d, s, T)
        part.integrate(d, s, T)
    kw_, cw = whole.download()
    kp, cp = part.download()
    assert len(kp) and {tuple(k) for k in kp} <= {tuple(k) for k in allowed}
    pos = {tuple(k): i for i, k in enumerate(kw_)}
    for i, k in enumerate(kp):
        j = pos[tuple(k)]
        for c in cw:
            assert np.array_equal(cp[c][i], cw[c][j]), c
    part.limit_bricks(None)
    assert part._allowed is None
    whole.close()
    part.close()


# --------------------------------------------------------------------------- the kept depth and the whole step
@pytest.mark.parametrize("backend", ["cpu", "gpu"])
def test_the_step_moves_a_wall_read_too_thick_onto_the_finer_model_of_the_kept_depth(tmp_path, backend):
    """The whole step on a 1 mm wall seen from both sides: the stage's mesh reads it 1.7 mm thick; the kept depth fused
    at 0.5 mm in the wall's bricks alone puts both faces within 0.1 mm of 1 mm apart; the loops stay; a stage without a
    thin wall is not touched."""
    pytest.importorskip("open3d")
    from ohs.app.fine_walls import FusedDepthSpool, make_fine_walls, read_spool
    from ohs.track.export import save_mesh
    from ohs.track.posegraph import write_trajectory
    from ohs.track.recon_tsdf import ConfidenceTsdf

    if backend not in _backends():
        pytest.skip("no GPU")
    coarse = ConfidenceTsdf(K, (W, H), voxel_mm=1.0, trunc_mm=4.0, depth_min_mm=150.0, depth_max_mm=450.0,
                            backend=backend, back_mm=2.0, opposite_mm=1.0)
    views = _views(1.0)
    spool = FusedDepthSpool(tmp_path / "stage" / "fused_depth", coarse)
    rows = []
    for k, (T, d) in enumerate(views):
        spool.add(k, d, np.where(d > 0, 0.2, 0.0).astype(np.float32), None, T)
        rows.append((100 + k, 0.1 * k, True, T))
    kept = spool.close([r[0] for r in rows])
    coarse.close()
    assert kept["frames"] == len(views) and kept["mb"] > 0 and read_spool(tmp_path / "stage" / "fused_depth")
    stage = tmp_path / "stage"
    V, F = _wall_mesh(0.85)  # read 1.7 mm thick
    S = np.full(len(V), 0.2, np.float32)
    save_mesh(stage / "mesh.ply", V, F, None, quality=S, looks=np.full(len(V), 3.0, np.float32))
    (stage / "report.json").write_text(json.dumps({"mesh": {"voxel_mm": 1.0},
                                                   "params": {"reconstruction": {"opposite_mm": 1.0}}}))
    write_trajectory(stage / "trajectory.csv", rows)
    rep = make_fine_walls(stage, stage / "fused_depth", out_dir=tmp_path / "fine", backend=backend)
    assert rep["acted"], rep
    assert rep["walls"]["found"] and rep["bricks"]["flagged"] > 0 and rep["frames"]["fused"] == len(views)
    from ohs.track.export import load_ply

    d = load_ply(tmp_path / "fine" / "mesh.ply")
    V2, F2 = d["vertices"], d["faces"]
    mid = np.abs(V2[:, 1]) < 10.0
    x = V2[mid & (np.abs(V2[:, 2] - 20.0) < 10.0), 0]
    assert 0.9 <= np.median(x[x > 0]) - np.median(x[x < 0]) <= 1.1
    assert len(boundary_loops(V2, F2).perimeter) == len(boundary_loops(V, F).perimeter) == 2
    assert d["quality"] is not None and d["looks"] is not None and len(d["quality"]) == len(V2)
    assert rep["triangles_after"] > rep["triangles_before"]
    # a stage without a thin wall (one face only): nothing happens, the file is not written
    lone = tmp_path / "lone"
    lone.mkdir()
    Vs, Fs = _sheet(0.5, 1.0)
    save_mesh(lone / "mesh.ply", Vs, Fs, None, quality=np.full(len(Vs), 0.2, np.float32))
    (lone / "report.json").write_text(json.dumps({"mesh": {"voxel_mm": 1.0}}))
    write_trajectory(lone / "trajectory.csv", rows)
    before = (lone / "mesh.ply").read_bytes()
    rep2 = make_fine_walls(lone, stage / "fused_depth", backend=backend)
    assert not rep2["acted"] and rep2["why"] == "no_thin_walls" and (lone / "mesh.ply").read_bytes() == before
    # no kept depth: it says so and changes nothing; made without a graphics card (the stage's report says so): that
    rep3 = make_fine_walls(lone, tmp_path / "none", backend=backend)
    assert not rep3["acted"] and rep3["why"] == "no_kept_depth"
    (lone / "report.json").write_text(json.dumps({"mesh": {"voxel_mm": 1.0},
                                                  "fine_walls_depth": {"kept": False, "why": "no_graphics_card"}}))
    rep4 = make_fine_walls(lone, tmp_path / "none", backend=backend)
    assert not rep4["acted"] and rep4["why"] == "no_graphics_card" and (lone / "mesh.ply").read_bytes() == before


@pytest.mark.parametrize("backend", ["cpu", "gpu"])
def test_the_step_says_how_far_it_is_and_gives_the_same_mesh_with_a_progress_callback(tmp_path, backend):
    """``make_fine_walls(progress=...)``: called with a fraction that only grows, ending at 1; the mesh it writes is
    the one written without the callback, byte for byte."""
    pytest.importorskip("open3d")
    from ohs.app.fine_walls import FusedDepthSpool, make_fine_walls
    from ohs.track.export import save_mesh
    from ohs.track.posegraph import write_trajectory
    from ohs.track.recon_tsdf import ConfidenceTsdf

    if backend not in _backends():
        pytest.skip("no GPU")
    coarse = ConfidenceTsdf(K, (W, H), voxel_mm=1.0, trunc_mm=4.0, depth_min_mm=150.0, depth_max_mm=450.0,
                            backend=backend, back_mm=2.0, opposite_mm=1.0)
    stage = tmp_path / "stage"
    spool = FusedDepthSpool(stage / "fused_depth", coarse)
    rows = []
    for k, (T, d) in enumerate(_views(1.0)):
        spool.add(k, d, np.where(d > 0, 0.2, 0.0).astype(np.float32), None, T)
        rows.append((100 + k, 0.1 * k, True, T))
    spool.close([r[0] for r in rows])
    coarse.close()
    V, F = _wall_mesh(0.85)
    save_mesh(stage / "mesh.ply", V, F, None, quality=np.full(len(V), 0.2, np.float32), looks=np.full(len(V), 3.0, np.float32))
    (stage / "report.json").write_text(json.dumps({"mesh": {"voxel_mm": 1.0}, "params": {"reconstruction": {"opposite_mm": 1.0}}}))
    write_trajectory(stage / "trajectory.csv", rows)
    seen: list = []
    a = make_fine_walls(stage, stage / "fused_depth", out_dir=tmp_path / "a", backend=backend,
                        progress=lambda f, m: seen.append((float(f), m)))
    b = make_fine_walls(stage, stage / "fused_depth", out_dir=tmp_path / "b", backend=backend)
    assert a["acted"] and b["acted"]
    assert (tmp_path / "a" / "mesh.ply").read_bytes() == (tmp_path / "b" / "mesh.ply").read_bytes()
    fr = [f for f, _ in seen]
    assert len(fr) >= 3 and fr == sorted(fr) and fr[-1] == 1.0 and all(isinstance(m, str) and m for _, m in seen)


def test_the_kept_depth_is_estimated_before_the_pass_from_the_frames_it_will_process(tmp_path):
    """``kept_depth_estimate``: the frames Follow the camera will process (the frame selection's step, the rate limit,
    the start and stop; never fewer than it processes) times the bytes a kept frame takes (the depth and its sigma,
    4 bytes each a pixel of the processed image): what the pass then keeps, on the graphics card's path."""
    pytest.importorskip("open3d")
    import dataclasses

    from recordings import scan_recording

    from ohs.app.fine_walls import SPOOL_DIR, kept_depth_estimate, spool_size_mb
    from ohs.app.pipeline import PipelineParams
    from ohs.app.process import ProcessParams, process_recording

    rec = scan_recording(tmp_path / "rec", scene="bracket", n_frames=6, image_size=(640, 400), realistic=False, seed=0,
                         depth_every=0)
    base = ProcessParams(pipeline=PipelineParams(), export=("ply",), fine_walls=True)
    est = kept_depth_estimate(rec, base)
    assert est["bytes_per_frame"] == 640 * 400 * 4 * 2 and est["bytes"] == est["frames"] * est["bytes_per_frame"]
    on = process_recording(rec, tmp_path / "on", params=base)
    assert est["frames"] == on["frames"]["processed"] == 6
    if on["fine_walls_depth"].get("kept"):
        assert spool_size_mb(tmp_path / "on" / SPOOL_DIR) * 2 ** 20 == pytest.approx(est["bytes"])
    # every 2nd frame, from the 2nd, at half the width: fewer frames, a quarter of the bytes a frame
    half = dataclasses.replace(base, frame_step=2, start=1, pipeline=PipelineParams(scale=0.5))
    e2 = kept_depth_estimate(rec, half)
    assert e2["frames"] == 3 and e2["bytes_per_frame"] == 320 * 200 * 4 * 2
    assert kept_depth_estimate(rec, dataclasses.replace(base, stop=2))["frames"] == 2


def test_follow_the_camera_keeps_the_depth_on_the_graphics_card_only_and_its_mesh_is_the_same(tmp_path):
    """``ProcessParams.fine_walls``: on the graphics card's path every fused frame's depth is kept (as fused, with its
    pose) and the mesh is exactly the one made without it; without a graphics card nothing is kept and the report says
    why."""
    pytest.importorskip("open3d")
    import dataclasses

    from recordings import scan_recording
    from test_mesh_looks import _canon

    from ohs.app.fine_walls import SPOOL_DIR, read_spool
    from ohs.app.pipeline import PipelineParams
    from ohs.app.process import ProcessParams, process_recording
    from ohs.io.recording import RecordingReader
    from ohs.track.export import load_ply

    rec = scan_recording(tmp_path / "rec", scene="bracket", n_frames=6, image_size=(640, 400), realistic=False, seed=0,
                         depth_every=0)
    gt = RecordingReader(rec).ground_truth_poses()
    base = ProcessParams(pipeline=PipelineParams(), export=("ply",))
    off = process_recording(rec, tmp_path / "off", params=base, poses=gt)
    on = process_recording(rec, tmp_path / "on", params=dataclasses.replace(base, fine_walls=True), poses=gt)
    a, b = load_ply(tmp_path / "off" / "mesh.ply"), load_ply(tmp_path / "on" / "mesh.ply")
    ca = _canon(a["vertices"], a["faces"], a["normals"], *(x for x in (a["quality"], a["looks"]) if x is not None))
    cb = _canon(b["vertices"], b["faces"], b["normals"], *(x for x in (b["quality"], b["looks"]) if x is not None))
    assert len(ca) == len(cb) and all(np.array_equal(x, y) for x, y in zip(ca, cb))
    assert "fine_walls_depth" not in off
    head = read_spool(tmp_path / "on" / SPOOL_DIR)
    if "on the GPU" in on["mesh"]["reconstruction"]:
        assert on["fine_walls_depth"]["kept"] and head is not None and len(head["entries"]) == on["frames"]["processed"]
        assert {e["index"] for e in head["entries"]} == {int(i) for i in gt}
        e = head["entries"][0]
        assert np.allclose(np.asarray(e["T"]), gt[e["index"]])
    else:
        assert on["fine_walls_depth"] == {"kept": False, "why": "no_graphics_card"} and head is None
