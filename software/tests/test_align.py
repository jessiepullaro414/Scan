"""ohs.align: aligning two scans of one part and merging them from their recordings.

The made-up shapes check the method exactly (a known motion, found again). The simulated scans
check it on what the engine really produces: two scans of the bracket taken from two sides, fused
at their true poses, so that each scan's world is exact and the true motion between them known.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import geometry as G  # noqa: E402
from ohs.align import AlignParams, ScanInput, align_pair, merge_scans, scan_cloud, scan_cloud_from_mesh  # noqa: E402
from ohs.app.pipeline import PipelineParams  # noqa: E402
from ohs.app.process import ProcessParams, process_recording  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.sim.nominal import nominal_calibration  # noqa: E402
from ohs.sim.scene import make_scene  # noqa: E402
from ohs.sim.trajectory import HandheldTrajectory  # noqa: E402
from ohs.track.export import load_mesh  # noqa: E402
from ohs.track.posegraph import read_trajectory  # noqa: E402
from recordings import scan_recording  # noqa: E402

SIZE = (640, 400)
N_FRAMES = 60


# --------------------------------------------------------------------------- made-up shapes
def _box(rng, lo, hi, step=1.0, faces="xXyYZ"):
    """Points with outward normals on faces of the box ``lo``..``hi`` (``Z``: top; no bottom)."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    P, N = [], []
    for f in faces:
        ax = "xyz".index(f.lower())
        u, v = [k for k in range(3) if k != ax]
        gu, gv = np.meshgrid(np.arange(lo[u], hi[u] + 1e-9, step), np.arange(lo[v], hi[v] + 1e-9, step))
        X = np.zeros((gu.size, 3))
        X[:, u], X[:, v] = gu.ravel(), gv.ravel()
        X[:, ax] = hi[ax] if f.isupper() else lo[ax]
        n = np.zeros(3)
        n[ax] = 1.0 if f.isupper() else -1.0
        P.append(X + rng.normal(0.0, 0.03, X.shape))
        N.append(np.tile(n, (len(X), 1)))
    return np.vstack(P), np.vstack(N)


def _part(rng, table=True, symmetric=False):
    """An L-shaped block with a boss and a rib on a table (z = 0), or a square block (symmetric)."""
    if symmetric:
        boxes = [((-20, -20, 0), (20, 20, 15))]
    else:
        boxes = [((0, 0, 0), (60, 30, 12)), ((0, 0, 12), (14, 30, 40)), ((35, 8, 12), (47, 20, 22)), ((20, 26, 12), (60, 30, 20))]
    P, N = zip(*[_box(rng, lo, hi) for lo, hi in boxes])
    P, N = np.vstack(P), np.vstack(N)
    if table:
        g = np.arange(-150.0, 210.0, 1.0)
        gx, gy = np.meshgrid(g, g)
        T = np.stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)], axis=1) + rng.normal(0.0, 0.03, (gx.size, 3))
        P, N = np.vstack([P, T]), np.vstack([N, np.tile([0.0, 0.0, 1.0], (len(T), 1))])
    return P, N


def _moved(P, N, T):
    return P @ T[:3, :3].T + T[:3, 3], N @ T[:3, :3].T


def _motion(deg, axis, t):
    return G.make_se3(G.so3_exp(np.radians(deg) * np.asarray(axis, float) / np.linalg.norm(axis)), np.asarray(t, float))


def _error(T, T_true):
    e = G.pose_error(T_true, T)
    return e[1], e[0]


def test_a_known_motion_is_found_again():
    """The same part on its table, seen in two worlds that differ by a large motion: found from
    the shape alone, to a few hundredths of a millimetre, with and without the table's help."""
    rng = np.random.default_rng(0)
    P, N = _part(rng)
    T_true = _motion(100.0, (0.3, 0.2, 1.0), (180.0, -40.0, 60.0))  # the source's world in the target's
    Ti = G.invert_se3(T_true)
    Ps, Ns = _moved(*_part(np.random.default_rng(1)), Ti)
    dst, src = scan_cloud(P, N), scan_cloud(Ps, Ns)
    assert dst.support is not None and dst.support_share > 0.9 and src.support_share > 0.9
    assert 2000 < dst.n_part < 0.2 * dst.full.n  # the table is set aside: the part is what is matched
    assert abs(abs(dst.support[0][2]) - 1.0) < 1e-3 and abs(dst.support[1]) < 0.1
    for same in (False, True):
        res = align_pair(src, dst, same_setup=same)
        mm, deg = _error(res.T, T_true)
        assert res.accepted and res.reason is None, res.verdict
        assert mm < 0.1 and deg < 0.05, (same, mm, deg)
        assert res.overlap > 0.9 and res.rmse_mm < 0.1 and res.fit_mm < 0.1 and res.dof == 6 and res.used_support == same
        assert res.start == "features" and res.agree >= 0.5 * res.candidates >= 2
        assert res.verdict.startswith("Aligned: ") and "starting guesses agree" in res.verdict
        d = res.as_dict()
        assert d["accepted"] is True and d["agree"] == res.agree and np.asarray(d["T"]).shape == (4, 4)


def test_a_given_start_is_refined_and_the_part_may_have_moved():
    """Turned over on the table, the support is another surface in each scan: with
    ``same_setup=False`` only the part counts. A rough pose given (tags, picked points) is used."""
    rng = np.random.default_rng(2)
    P, N = _part(rng, table=False)
    T_true = _motion(170.0, (1.0, 0.1, 0.0), (10.0, 80.0, 30.0))
    Ps, Ns = _moved(*_part(np.random.default_rng(3), table=False), G.invert_se3(T_true))
    # each scan has its own table, under the part as it lay then
    g = np.arange(-120.0, 180.0, 1.0)
    gx, gy = np.meshgrid(g, g)
    tab = np.stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)], axis=1)
    up = np.tile([0.0, 0.0, 1.0], (len(tab), 1))
    dst = scan_cloud(np.vstack([P, tab]), np.vstack([N, up]))
    src = scan_cloud(np.vstack([Ps, tab]), np.vstack([Ns, up]))  # the same table plane in the source's own world
    res = align_pair(src, dst, same_setup=False)
    assert res.accepted, res.verdict
    mm, deg = _error(res.T, T_true)
    assert mm < 0.1 and deg < 0.05
    # told that the setup is the same, the step finds the two tables far apart and leaves them out
    res = align_pair(src, dst, same_setup=True)
    assert res.accepted and not res.used_support, res.verdict
    mm, deg = _error(res.T, T_true)
    assert mm < 0.1 and deg < 0.05
    rough = T_true @ _motion(4.0, (0.2, 1.0, 0.4), (3.0, -2.0, 2.5))
    res = align_pair(src, dst, init=rough)
    assert res.accepted and res.start == "given" and res.candidates == 1 and "given starting pose" in res.verdict
    mm, deg = _error(res.T, T_true)
    assert mm < 0.1 and deg < 0.05


def test_a_symmetric_part_and_a_bare_table_are_refused_in_words():
    rng = np.random.default_rng(4)
    P, N = _part(rng, symmetric=True)
    T_true = _motion(40.0, (0.0, 0.0, 1.0), (30.0, 10.0, 0.0))
    Ps, Ns = _moved(*_part(np.random.default_rng(5), symmetric=True), G.invert_se3(T_true))
    res = align_pair(scan_cloud(Ps, Ns), scan_cloud(P, N), same_setup=True)
    assert not res.accepted and "more than one position" in res.reason and res.verdict.startswith("Not aligned: ")
    # with a rough pose the same pair aligns (the user says which of the positions it is)
    res = align_pair(scan_cloud(Ps, Ns), scan_cloud(P, N), init=T_true @ _motion(3.0, (0, 0, 1), (2.0, 1.0, 0.0)), same_setup=True)
    assert res.accepted
    mm, deg = _error(res.T, T_true)
    assert mm < 0.15 and deg < 0.1
    # a table with nothing on it
    g = np.arange(-60.0, 60.0, 1.0)
    gx, gy = np.meshgrid(g, g)
    tab = np.stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)], axis=1)
    bare = scan_cloud(tab, np.tile([0.0, 0.0, 1.0], (len(tab), 1)))
    res = align_pair(bare, scan_cloud(P, N))
    assert not res.accepted and "too little of the part" in res.reason
    with pytest.raises(ValueError):
        scan_cloud(np.zeros((0, 3)), np.zeros((0, 3)))
    with pytest.raises(ValueError):
        AlignParams(match_mm=0.0)
    with pytest.raises(ValueError):
        AlignParams(support_min_share=0.0)


def test_one_guess_alone_is_not_trusted_and_a_detail_scan_counts_by_its_own_share():
    rng = np.random.default_rng(8)
    P, N = _part(rng)
    T_true = _motion(60.0, (0.1, 0.3, 1.0), (40.0, 20.0, 10.0))
    Ps, Ns = _moved(*_part(np.random.default_rng(9)), G.invert_se3(T_true))
    dst, src = scan_cloud(P, N), scan_cloud(Ps, Ns)
    res = align_pair(src, dst, replace(AlignParams(), ransac_seeds=0))  # the FGR guess only
    assert not res.accepted and res.candidates <= 1 and ("only one" in res.reason or "no starting guess" in res.reason)
    # a scan of one end of the part against a scan of all of it: all of the small scan overlaps
    Q, M = _part(np.random.default_rng(10), table=False)
    end = Q[:, 0] < 30.0  # the upright and half the base
    Qs, Ms = _moved(Q[end], M[end], G.invert_se3(T_true))
    res = align_pair(scan_cloud(Qs, Ms), scan_cloud(*_part(np.random.default_rng(11), table=False)),
                     init=T_true @ _motion(3.0, (1.0, 0.0, 0.2), (2.0, 2.0, -1.0)))
    assert res.accepted and res.overlap > 0.9, res.verdict  # of the smaller scan, not of the larger
    mm, deg = _error(res.T, T_true)
    assert mm < 0.15 and deg < 0.1


def test_two_shapes_that_do_not_belong_together_are_refused():
    rng = np.random.default_rng(6)
    P, N = _part(rng)
    Q, M = _part(np.random.default_rng(7), symmetric=True)
    res = align_pair(scan_cloud(Q, M), scan_cloud(P, N), replace(AlignParams(), ransac_seeds=4))
    assert not res.accepted and res.reason


# --------------------------------------------------------------------------- simulated scans
def _scan(tmp: Path, scene: str, azimuth: float, seed: int) -> dict:
    """A simulated scan of ``scene`` starting at ``azimuth``, fused at its true poses."""
    sc = make_scene(scene, 0)
    cal = nominal_calibration(SIZE, realistic=True)
    traj = HandheldTrajectory(target=sc.target, t0=100.0 + float(cal.imu.time_offset), duration=N_FRAMES / 30.0, seed=seed,
                              baseline=cal.stereo.baseline, azimuth0_deg=azimuth, **dict(sc.meta.get("trajectory", {})))
    rec = scan_recording(tmp / f"rec_{scene}_{azimuth:.0f}", scene=scene, n_frames=N_FRAMES, image_size=SIZE, realistic=True,
                         depth_every=0, seed=seed, trajectory=traj)
    gt = RecordingReader(rec).ground_truth_poses()
    first = min(gt)
    out = tmp / f"out_{scene}_{azimuth:.0f}"
    T0inv = G.invert_se3(gt[first])
    process_recording(rec, out, params=ProcessParams(pipeline=PipelineParams(), export=("ply",)),
                      poses={i: T0inv @ T for i, T in gt.items()})
    return {"rec": rec, "out": out, "world": gt[first]}  # world: the scan's world in the simulator's


@pytest.fixture(scope="module")
def bracket_scans(tmp_path_factory) -> list[dict]:
    tmp = tmp_path_factory.mktemp("align")
    return [_scan(tmp, "bracket", 20.0, 0), _scan(tmp, "bracket", 110.0, 1)]


@pytest.mark.slow
def test_two_scans_of_the_bracket_from_two_sides(bracket_scans):
    a, b = bracket_scans
    T_true = G.invert_se3(a["world"]) @ b["world"]
    assert G.rotation_angle_deg(T_true[:3, :3]) > 60.0  # really two sides
    ca, cb = scan_cloud_from_mesh(a["out"] / "mesh.ply"), scan_cloud_from_mesh(b["out"] / "mesh.ply")
    assert ca.support_share > 0.8 and cb.support_share > 0.8  # a table scan is mostly table
    for c in (ca, cb):  # nothing of the part lies behind the table (a mesh's fringe at the table's rim does)
        assert np.all(c.part.fine.pts @ c.support[0] + c.support[1] > 0)
    P = cb.part.fine.pts
    for same, max_mm, max_deg in ((False, 2.5, 0.8), (True, 2.0, 0.5)):
        res = align_pair(cb, ca, same_setup=same)
        assert res.accepted, res.verdict
        off = np.linalg.norm(P @ (res.T[:3, :3] - T_true[:3, :3]).T + (res.T[:3, 3] - T_true[:3, 3]), axis=1)
        mm, deg = _error(res.T, T_true)
        print(f"bracket, same setup {same}: {res.verdict} Against the truth: {mm:.2f} mm, {deg:.3f} deg; the part's points "
              f"land {np.median(off):.2f} mm (median), {off.max():.2f} mm (largest) off")
        assert np.median(off) < max_mm and deg < max_deg
        assert res.agree >= 3 and res.fit_mm < 0.5 and res.used_support == same


@pytest.mark.slow
def test_merging_two_scans_from_their_recordings(bracket_scans, tmp_path):
    """Both scans' frames fused into one model at the aligned poses: more of the part than either
    scan alone, and where both saw it, one surface."""
    a, b = bracket_scans
    T_true = G.invert_se3(a["world"]) @ b["world"]
    scans = [ScanInput(a["rec"], a["out"] / "trajectory.csv"), ScanInput(b["rec"], read_trajectory(b["out"] / "trajectory.csv"), T_true)]
    seen = []
    rep = merge_scans(scans, tmp_path / "merged", ProcessParams(pipeline=PipelineParams(), export=("ply",)),
                      progress=lambda k, n: seen.append((k, n)))
    assert rep["frames"]["fused"] == 2 * N_FRAMES and seen[0] == (0, 2 * N_FRAMES) and seen[-1] == (2 * N_FRAMES - 1, 2 * N_FRAMES)
    assert [s["frames"] for s in rep["scans"]] == [N_FRAMES, N_FRAMES] and rep["scans"][1]["T"] == T_true.tolist()
    assert (tmp_path / "merged" / "report.json").is_file() and (tmp_path / "merged" / "laser_points.ply").is_file()
    V, F, _ = load_mesh(tmp_path / "merged" / "mesh.ply")
    assert rep["mesh"]["n_vertices"] == len(V) and len(F) > 0

    def part_points(path):
        c = scan_cloud_from_mesh(path)
        return c.n_part

    n_a, n_b, n_m = part_points(a["out"] / "mesh.ply"), part_points(b["out"] / "mesh.ply"), part_points(tmp_path / "merged" / "mesh.ply")
    print(f"part surface (points at 1 mm): scan A {n_a}, scan B {n_b}, merged {n_m}")
    assert n_m > 1.03 * max(n_a, n_b)  # more of the part than either scan
    assert n_m < 0.95 * (n_a + n_b)  # and the shared surface once, not twice
    # the merged part is where each scan has it: scan A's as it is, scan B's moved by the given pose
    cm = scan_cloud_from_mesh(tmp_path / "merged" / "mesh.ply")
    for scan, T in ((a, np.eye(4)), (b, T_true)):
        pts = scan_cloud_from_mesh(scan["out"] / "mesh.ply").part.fine.pts
        d, _ = cm.part.fine.tree.query(pts @ T[:3, :3].T + T[:3, 3])
        assert np.percentile(d, 80) < 1.5, np.percentile(d, [50, 80, 95])
    # with a wrong pose for scan B its surface is somewhere else: the check above can fail
    wrong = T_true @ _motion(0.0, (0, 0, 1), (25.0, 0.0, 0.0))
    d, _ = cm.part.fine.tree.query(scan_cloud_from_mesh(b["out"] / "mesh.ply").part.fine.pts @ wrong[:3, :3].T + wrong[:3, 3])
    assert np.percentile(d, 80) > 5.0

    with pytest.raises(ValueError, match="no scans"):
        merge_scans([], tmp_path / "none")
    with pytest.raises(ValueError, match="none of its frames has a pose"):
        merge_scans([ScanInput(a["rec"], [(0, 0.0, True, np.full((4, 4), np.nan))])], tmp_path / "none")
    with pytest.raises(ValueError, match="Poisson"):
        merge_scans(scans, tmp_path / "none", ProcessParams(poisson=True))


def test_merge_refuses_scans_of_different_calibrations(tmp_path):
    recs = [scan_recording(tmp_path / f"r{k}", n_frames=2, image_size=size, realistic=False, seed=0, depth_every=0)
            for k, size in enumerate([(640, 400), (320, 200)])]
    rows = [[(int(i), 0.0, True, np.eye(4)) for i in RecordingReader(r).indices] for r in recs]
    with pytest.raises(ValueError, match="another calibration"):
        merge_scans([ScanInput(recs[0], rows[0]), ScanInput(recs[1], rows[1])], tmp_path / "out")
