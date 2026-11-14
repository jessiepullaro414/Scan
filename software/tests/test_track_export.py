"""Tests for ohs.track.export (writers, readers, Poisson, cleanup) and ohs.track.fusion.LaserCloud."""
import numpy as np
import pytest

from ohs import geometry as G
from ohs.track.export import (
    clean_mesh,
    face_normals,
    load_mesh,
    load_ply,
    poisson_mesh,
    save_mesh,
    save_points_ply,
)
from ohs.track.fusion import LaserCloud, voxel_average


def grid_mesh(n: int = 20, size: float = 50.0, z: float = 0.0, offset=(0.0, 0.0)):
    """(n x n) quads split into triangles, counter-clockwise seen from +z."""
    xs = np.linspace(0, size, n + 1)
    X, Y = np.meshgrid(xs + offset[0], xs + offset[1], indexing="xy")
    V = np.stack([X.ravel(), Y.ravel(), np.full(X.size, z)], axis=1)
    idx = np.arange((n + 1) ** 2).reshape(n + 1, n + 1)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    F = np.concatenate([np.stack([a, b, c], 1), np.stack([a, c, d], 1)])
    return V, F


def fibonacci_sphere(n: int, r: float, center=(0.0, 0.0, 0.0)):
    k = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * k / n)
    theta = np.pi * (1 + 5 ** 0.5) * k
    U = np.stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)], axis=1)
    return np.asarray(center) + r * U, U


def _sample_mesh():
    V, F = grid_mesh(6, 30.0)
    V[:, 2] = 0.01 * (V[:, 0] - 15) ** 2  # gently curved so normals vary
    N = np.zeros_like(V)
    np.add.at(N, F.ravel(), np.repeat(face_normals(V, F), 3, axis=0))
    return V, F, N / np.linalg.norm(N, axis=1, keepdims=True)


# --------------------------------------------------------------------------- writers / readers
def test_ply_mesh_roundtrip(tmp_path):
    V, F, N = _sample_mesh()
    save_mesh(tmp_path / "m.ply", V, F, N)
    d = load_ply(tmp_path / "m.ply")
    assert np.allclose(d["vertices"], V, atol=1e-5)
    assert np.array_equal(d["faces"], F)
    assert np.allclose(d["normals"], N, atol=1e-6)
    V2, F2, N2 = load_mesh(tmp_path / "m.ply")
    assert np.array_equal(F2, F) and np.allclose(N2, N, atol=1e-6)


def test_stl_roundtrip(tmp_path):
    V, F, _ = _sample_mesh()
    save_mesh(tmp_path / "m.stl", V, F)
    raw = (tmp_path / "m.stl").read_bytes()
    assert len(raw) == 84 + 50 * len(F)
    V2, F2, N2 = load_mesh(tmp_path / "m.stl")
    assert N2 is None and len(V2) == len(V) and len(F2) == len(F)
    # same triangles (as coordinate triples), independent of vertex order
    tri_a = np.sort(np.round(V[F].reshape(len(F), -1), 4), axis=0)
    tri_b = np.sort(np.round(V2[F2].reshape(len(F2), -1), 4), axis=0)
    assert np.allclose(tri_a, tri_b, atol=1e-4)
    # stored facet normals are the right-hand-rule normals (orientation preserved)
    stored = np.frombuffer(raw, dtype=np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")]), offset=84)
    assert np.allclose(stored["n"], face_normals(V, F), atol=1e-6)


def test_one_stl_writer_for_the_export_and_the_app(tmp_path):
    """The export and the app's ``mesh.stl`` come from one writer (P6): the vertices as float32,
    the facet normals computed in float64 from the vertices as given and then stored as float32,
    zero for a face without area."""
    import ohs.server.processing as processing
    import ohs.track.export as export

    assert not hasattr(processing, "_write_stl") and not hasattr(processing, "_STL")  # the app has no writer of its own
    rng = np.random.default_rng(1)
    V = rng.normal(0.0, 50.0, (3000, 3))
    F = rng.integers(0, len(V), (7000, 3))
    F[:20, 1] = F[:20, 0]  # faces without area
    export.write_stl(tmp_path / "a.stl", V, F)
    save_mesh(tmp_path / "b.stl", V, F)
    raw = (tmp_path / "a.stl").read_bytes()
    assert raw == (tmp_path / "b.stl").read_bytes()
    assert raw[:34] == b"OpenHandScan binary STL, units: mm" and int(np.frombuffer(raw, "<u4", 1, 80)[0]) == len(F)
    stored = np.frombuffer(raw, dtype=np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")]), offset=84)
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, ln, out=np.zeros_like(n), where=ln > 0)
    assert np.array_equal(stored["n"], n.astype("<f4")) and not stored["n"][:20].any()
    assert np.array_equal(stored["v"], V[F].astype("<f4")) and not stored["a"].any()
    # float32 vertices (as a mesh read back from a PLY has them) give the same file as their float64 copy
    export.write_stl(tmp_path / "c.stl", V.astype(np.float32), F)
    export.write_stl(tmp_path / "d.stl", V.astype(np.float32).astype(np.float64), F)
    assert (tmp_path / "c.stl").read_bytes() == (tmp_path / "d.stl").read_bytes()


def test_obj_roundtrip(tmp_path):
    V, F, N = _sample_mesh()
    save_mesh(tmp_path / "m.obj", V, F, N)
    V2, F2, N2 = load_mesh(tmp_path / "m.obj")
    assert np.allclose(V2, V, atol=1e-6) and np.array_equal(F2, F) and np.allclose(N2, N, atol=1e-6)
    save_mesh(tmp_path / "plain.obj", V, F)
    V3, F3, N3 = load_mesh(tmp_path / "plain.obj")
    assert N3 is None and np.array_equal(F3, F)
    with pytest.raises(ValueError):
        save_mesh(tmp_path / "m.xyz", V, F)
    with pytest.raises(ValueError):
        save_mesh(tmp_path / "bad.ply", V, F + len(V))


def test_points_ply_and_ascii_reader(tmp_path):
    rng = np.random.default_rng(0)
    P = rng.normal(size=(500, 3)) * 30
    N = rng.normal(size=(500, 3))
    N /= np.linalg.norm(N, axis=1, keepdims=True)
    gray = rng.uniform(0, 1, 500)
    save_points_ply(tmp_path / "p.ply", P, N, gray)
    d = load_ply(tmp_path / "p.ply")
    assert d["faces"] is None
    assert np.allclose(d["vertices"], P, atol=1e-4) and np.allclose(d["normals"], N, atol=1e-6)
    assert d["colors"].dtype == np.uint8 and np.array_equal(d["colors"][:, 0], np.round(gray * 255).astype(np.uint8))
    save_points_ply(tmp_path / "q.ply", P)
    assert load_ply(tmp_path / "q.ply")["normals"] is None
    # hand-written ASCII PLY with a quad (fan-triangulated)
    (tmp_path / "a.ply").write_text(
        "ply\nformat ascii 1.0\nelement vertex 4\nproperty float x\nproperty float y\nproperty float z\n"
        "element face 1\nproperty list uchar int vertex_indices\nend_header\n"
        "0 0 0\n1 0 0\n1 1 0\n0 1 0\n4 0 1 2 3\n", encoding="ascii")
    a = load_ply(tmp_path / "a.ply")
    assert a["vertices"].shape == (4, 3) and np.array_equal(a["faces"], [[0, 1, 2], [0, 2, 3]])


# --------------------------------------------------------------------------- processing
def test_clean_mesh_drops_small_components():
    V1, F1 = grid_mesh(20, 50.0)                       # 800 faces
    V2, F2 = grid_mesh(1, 2.0, z=5.0, offset=(80, 80))  # 2 faces, far away
    V = np.vstack([V1, V2, [[999.0, 999.0, 999.0]]])   # plus an unreferenced vertex
    F = np.vstack([F1, F2 + len(V1), [[0, 0, 1]]])      # plus a degenerate face
    Vc, Fc, idx = clean_mesh(V, F, min_component_fraction=0.01, return_index=True)
    assert len(Fc) == len(F1) and len(Vc) == len(V1)
    assert np.allclose(Vc, V[idx]) and np.allclose(Vc[Fc], V1[F1])
    Vk, Fk = clean_mesh(V, F, min_component_fraction=0.0)
    assert len(Fk) == len(F1) + len(F2) and len(Vk) == len(V1) + len(V2)


@pytest.mark.slow
@pytest.mark.parametrize("with_normals", [True, False])
def test_poisson_sphere(with_normals):
    rng = np.random.default_rng(1)
    c = np.array([10.0, -5.0, 250.0])
    P, U = fibonacci_sphere(20000, 40.0, c)
    P = P + rng.normal(scale=0.03, size=P.shape)
    V, F, N = poisson_mesh(P, U if with_normals else None, depth=9, density_quantile=0.02)
    assert len(F) > 1000
    r = np.linalg.norm(V - c, axis=1)
    assert np.abs(r - 40.0).max() < 0.5, np.abs(r - 40.0).max()
    outward = np.sum(N * (V - c) / r[:, None], axis=1)
    assert np.median(outward) > 0.99  # normals point outwards in both modes


# --------------------------------------------------------------------------- LaserCloud
def test_laser_cloud_accumulates_in_world_frame(tmp_path):
    rng = np.random.default_rng(2)
    world = rng.uniform(-40, 40, size=(300, 3)) + [0, 0, 250]
    lc = LaserCloud()
    for k in range(6):
        T_wc = G.make_se3(G.so3_exp(rng.normal(scale=0.2, size=3)), rng.normal(scale=20, size=3))
        pts_c = G.transform_points(G.invert_se3(T_wc), world) + rng.normal(scale=0.02, size=world.shape)
        pts_c[::50] = np.nan  # invalid triangulations are skipped
        name = "vertical" if k % 2 == 0 else "horizontal"
        assert lc.add(pts_c, T_wc, name) == 300 - 6
    assert len(lc) == 6 * 294 and sorted(lc.names) == ["horizontal", "vertical"] and lc.n_frames == 6
    assert len(lc.raw_points(["vertical"])) == 3 * 294
    assert list(lc.raw_counts().items()) == [(n, len(lc.raw_points([n]))) for n in lc.names]  # same order too
    # voxel averaging merges the 6 noisy observations of each point (points are > 1 mm apart)
    merged = lc.points(voxel_mm=0.5)
    raw = lc.raw_points()
    assert len(merged) < len(raw)
    d = np.linalg.norm(merged[:, None, :] - world[None], axis=2).min(axis=1)
    assert np.median(d) < 0.05
    out = lc.save_ply(tmp_path / "laser.ply", voxel_mm=0.5)
    back = load_ply(out)["vertices"]
    assert np.allclose(np.sort(back, axis=0), np.sort(merged, axis=0), atol=1e-4)
    assert len(lc.points(voxel_mm=None)) == len(raw)


def test_voxel_average_counts():
    P = np.array([[0.05, 0.05, 0.05], [0.15, 0.1, 0.1], [1.05, 0.0, 0.0]])
    out, counts = voxel_average(P, 0.2)
    assert len(out) == 2 and sorted(counts.tolist()) == [1, 2]
    assert np.allclose(out[np.argmax(counts)], [0.1, 0.075, 0.075])
    out2, _ = voxel_average(P, 0.2, min_count=2)
    assert len(out2) == 1
