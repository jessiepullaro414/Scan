"""The mesh model's rule for voxels seen from opposite sides (``ohs.track.recon_tsdf``, "Seen from opposite
sides"; VALIDATION.md, "Thin walls seen from opposite sides"): a thin wall keeps its thickness, a surface seen from
one side is left alone bit for bit, frames that disagree are fused as without the rule, the first mean is untouched,
and the GPU and numpy agree."""
import numpy as np
import pytest

from ohs import compute
from ohs.track.recon_tsdf import BACK_VOXELS, ConfidenceTsdf

W, H, F_PX = 1280, 800, 713.0
K = np.array([[F_PX, 0, (W - 1) / 2], [0, F_PX, (H - 1) / 2], [0, 0, 1.0]])
TARGET = np.array([0.0, 0.0, 20.0])
gpu = pytest.mark.skipif(compute.get_device() is None, reason="no GPU")
EXTRA = ("w2", "t2", "npos", "dx", "dy", "dz")  # the channels the rule adds


def look_at(c):
    z = TARGET - c
    z /= np.linalg.norm(z)
    x = np.cross(z, [0.0, 0.0, 1.0])
    x /= np.linalg.norm(x)
    T = np.eye(4)
    T[:3, :3] = np.column_stack([x, np.cross(z, x), z])
    T[:3, 3] = c
    return T


def wall_pose(a_deg):
    a = np.radians(a_deg)
    e = np.radians(45.0)
    return look_at(TARGET + 250.0 * np.array([np.sin(a) * np.cos(e), np.cos(a) * np.cos(e), np.sin(e)]))


def wall_depth(T, wall_mm):
    """Exact depth of a wall (80 x wall_mm x 50 mm) standing on the table z = 0; without a wall: the table."""
    lo, hi = np.array([-40.0, -wall_mm / 2, 0.0]), np.array([40.0, wall_mm / 2, 50.0])
    vs, us = np.mgrid[0:H, 0:W]
    rays = np.stack([(us - K[0, 2]) / F_PX, (vs - K[1, 2]) / F_PX, np.ones((H, W))], -1).reshape(-1, 3)
    D, o = rays @ T[:3, :3].T, T[:3, 3]
    with np.errstate(divide="ignore", invalid="ignore"):
        tp = -o[2] / D[:, 2]
        tp[~(tp > 0)] = np.inf
        t = tp
        if wall_mm > 0:
            t1, t2 = (lo - o) / D, (hi - o) / D
            tn, tf = np.minimum(t1, t2).max(axis=1), np.maximum(t1, t2).min(axis=1)
            t = np.minimum(tp, np.where((tn < tf) & (tn > 0), tn, np.inf))
    return np.where(np.isfinite(t), t, 0.0).reshape(H, W).astype(np.float32)


def model(backend="gpu", opposite=1.0, **kw):
    return ConfidenceTsdf(K, (W, H), voxel_mm=1.0, trunc_mm=4.0, backend=backend, back_mm=BACK_VOXELS * 1.0,
                          opposite_mm=opposite, **kw)


def thickness(vol, wall_mm):
    V, _, N = (np.asarray(a, np.float64) for a in vol.extract_mesh()[:3])
    inside = (np.abs(V[:, 0]) < 34) & (V[:, 2] > 6) & (V[:, 2] < 44) & (np.abs(V[:, 1]) < wall_mm / 2 + 3)
    out = 0.0
    for sgn in (1.0, -1.0):
        m = inside & (sgn * N[:, 1] > 0.7)
        assert m.sum() > 1000
        out += float((sgn * V[m, 1]).mean())
    return out


@gpu
@pytest.mark.parametrize("wall_mm, today_over, limit", [(1.0, 1.9, 1.2), (2.0, 2.3, 2.15)])
def test_a_wall_seen_from_both_sides_keeps_its_thickness(wall_mm, today_over, limit):
    """Exact depth, true poses, all round: with the cut at 2 voxels alone a 1 mm wall reads 2.1 mm and a 2 mm
    wall 2.4 mm; with the second mean they read 0.9 and 2.0 mm."""
    read = {}
    for opp in (None, 1.0):
        vol = model(opposite=opp)
        for a in np.arange(0.0, 360.0, 10.0):
            vol.integrate(wall_depth(wall_pose(a), wall_mm), None, wall_pose(a))
        read[opp] = thickness(vol, wall_mm)
        vol.close()
    print(f"wall {wall_mm} mm: {read[None]:.2f} mm without, {read[1.0]:.2f} mm with the rule")
    assert read[None] > today_over
    assert wall_mm - 0.25 < read[1.0] < limit


@gpu
def test_a_surface_seen_from_one_side_is_left_alone():
    """Noisy depth of the table alone (0.5 mm, correlated over 7 px as the matcher's, a fresh draw per view): no
    voxel is seen from opposite sides, so every value the mesh is made from is the one without the rule, bit for bit."""
    import cv2

    rng = np.random.default_rng(3)
    frames = []
    for a in np.arange(0.0, 360.0, 30.0):
        d = wall_depth(wall_pose(a), 0.0)
        e = cv2.blur(rng.normal(0.0, 1.0, d.shape).astype(np.float32), (7, 7)) * np.float32(7.0 * 0.5)
        frames.append((np.where(d > 0, d + e, 0.0).astype(np.float32), wall_pose(a)))
    vol = model(opposite=1.0)
    for d, T in frames:
        vol.integrate(d, np.full((H, W), 0.4, np.float32), T)
    _, ch = vol.download()
    seen = ch["nobs"] > 3
    f = vol.opposite_share(ch)
    print(f"largest share on a noisy plane: {f[seen].max():.3f} (the rule starts at {vol.opposite_shares[0]})")
    assert f[seen].max() < vol.opposite_shares[0]
    assert np.array_equal(vol.fused_tsdf(ch), ch["tsdf"])
    vol.close()


@gpu
def test_the_first_mean_does_not_change_and_off_has_no_extra_channels():
    frames = [(wall_depth(wall_pose(a), 2.0), wall_pose(a)) for a in np.arange(0.0, 360.0, 45.0)]
    vols = [model(opposite=None), model(opposite=1.0)]
    for d, T in frames:
        for v in vols:
            v.integrate(d, None, T)
    (k0, c0), (k1, c1) = vols[0].download(), vols[1].download()
    assert np.array_equal(k0, k1)
    assert set(c1) - set(c0) == set(EXTRA)
    for name in c0:
        assert np.array_equal(c0[name], c1[name]), name
    assert vols[1].memory_mb == pytest.approx(vols[0].memory_mb * (10 + len(EXTRA)) / 10)
    for v in vols:
        v.close()


@gpu
def test_gpu_and_numpy_agree_on_the_second_mean():
    frames = [(wall_depth(wall_pose(a), 2.0), wall_pose(a)) for a in (0.0, 20.0, 40.0, 60.0, 320.0, 340.0, 180.0, 200.0, 220.0, 240.0, 140.0, 160.0)]
    vols = [model("cpu"), model("gpu")]
    for d, T in frames:
        for v in vols:
            v.integrate(d, None, T)
    (kc, c), (kg, g) = vols[0].download(), vols[1].download()
    # the two brick searches differ by a few bricks at the band's rim on this scene (4 of 19,386): compare the common ones
    enc = lambda k: (k[:, 0].astype(np.int64) << 42) + (k[:, 1].astype(np.int64) << 21) + k[:, 2]
    _, ic, ig = np.intersect1d(enc(kc), enc(kg), return_indices=True)
    assert len(ic) > 0.999 * max(len(kc), len(kg))
    c, g = {n: a[ic] for n, a in c.items()}, {n: a[ig] for n, a in g.items()}
    both = (c["nobs"] == g["nobs"]) & (c["nobs"] > 0)
    assert both.sum() > 20000
    for name in (*EXTRA, "tsdf"):
        close = np.abs(c[name] - g[name])[both] <= 1e-3 * np.maximum(1.0, np.abs(c[name][both]))
        assert close.mean() > 0.99, name
    fc, fg = vols[0].opposite_share(c), vols[1].opposite_share(g)
    assert (np.abs(fc - fg)[both] < 0.02).mean() > 0.99
    assert (fc[both] > vols[0].opposite_shares[0]).sum() > 500  # the air next to the wall is found
    for v in vols:
        v.close()


def rodrigues(r):
    a = np.linalg.norm(r)
    k = r / max(a, 1e-12)
    Kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(a) * Kx + (1 - np.cos(a)) * Kx @ Kx


@gpu
def test_frames_that_disagree_are_fused_as_without_the_rule():
    """A 7 mm wall on a table, every view fused at a pose that is off by 0.9 mm and 0.2 degrees (as in marker
    mode). A shorter cut everywhere sinks the table here (by 0.8 mm with the cut at 1 voxel); the rule must not: no
    pair of views saw the table's voxels from opposite sides."""
    rng = np.random.default_rng(0)
    frames = []
    for a in np.arange(0.0, 360.0, 10.0):
        T = wall_pose(a)
        E = np.eye(4)
        E[:3, :3] = rodrigues(rng.normal(0.0, 0.9 / 250.0, 3))
        E[:3, 3] = rng.normal(0.0, 0.9, 3)
        frames.append((wall_depth(T, 7.0), T @ E))
    from ohs.mesh.topology import boundary_loops, edge_table

    off, loops = {}, {}
    for opp in (None, 1.0):
        vol = model(opposite=opp)
        for d, T in frames:
            vol.integrate(d, None, T)
        V, F = (np.asarray(a) for a in vol.extract_mesh()[:2])
        tb = (np.abs(V[:, 0]) < 60) & (np.abs(V[:, 1]) > 15) & (np.abs(V[:, 1]) < 40) & (np.abs(V[:, 2]) < 3)
        off[opp] = float(V[tb, 2].mean())
        loops[opp] = len(boundary_loops(V, F, edge_table(F, len(V))))
        vol.close()
    print(f"table offset with frames 0.9 mm off: {off[None]:+.3f} mm without, {off[1.0]:+.3f} mm with the rule; "
          f"open loops {loops[None]} / {loops[1.0]}")
    assert abs(off[1.0] - off[None]) < 0.02
    assert loops[1.0] <= loops[None] + 2  # no pinholes from the rule


def test_the_settings_are_checked():
    with pytest.raises(ValueError, match="opposite_mm"):
        model("cpu", opposite=0.0)
    with pytest.raises(ValueError, match="opposite_shares"):
        model("cpu", opposite_shares=(0.3, 0.2))
    assert model("cpu", opposite=None).opposite_mm is None
