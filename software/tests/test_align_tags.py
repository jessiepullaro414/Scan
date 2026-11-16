"""Tags as a start for aligning scans (ohs.align.tags).

* A pose from the tags two maps share: a known motion found again; a tag that was moved is left
  out; no tag in common, no pose; a marker-mode scan's map file read back.
* Two simulated scans of the sphere on its table with six tags around it, from 150 degrees apart:
  the tags each scan finds in its recording lie where the simulator put them; the shape alone
  cannot say how a sphere on a plane is turned, the tags can, and the scans are placed from them.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import geometry as G  # noqa: E402
from ohs.align import TagMap, align_many, align_pair, load_tag_map, pose_from_tags, scan_cloud_from_mesh, tag_map  # noqa: E402
from ohs.app.pipeline import PipelineParams  # noqa: E402
from ohs.app.process import ProcessParams, process_recording  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.sim.nominal import nominal_calibration  # noqa: E402
from ohs.sim.scene import make_scene  # noqa: E402
from ohs.sim.trajectory import HandheldTrajectory  # noqa: E402
from recordings import scan_recording  # noqa: E402

SIZE = (640, 400)
N_FRAMES = 60


def _motion(deg: float, axis, t) -> np.ndarray:
    a = np.asarray(axis, dtype=np.float64)
    return G.make_se3(G.so3_exp(np.radians(deg) * a / np.linalg.norm(a)), np.asarray(t, dtype=np.float64))


def _square(c, tag_mm: float = 20.0, turn: float = 0.0) -> np.ndarray:
    s = tag_mm / 2.0
    X = np.array([[-s, s, 0.0], [s, s, 0.0], [s, -s, 0.0], [-s, -s, 0.0]])
    return X @ G.so3_exp(np.array([0.0, 0.0, np.radians(turn)])).T + np.asarray(c, dtype=np.float64)


def _maps(T: np.ndarray, noise: float = 0.0, seed: int = 0) -> tuple[TagMap, TagMap]:
    """Four tags in the target world, and the same tags in a source world that ``T`` takes into it."""
    rng = np.random.default_rng(seed)
    dst = TagMap({i: _square(c, turn=15.0 * i) for i, c in enumerate([(0, 0, 300), (90, 10, 310), (-40, 80, 290), (30, -70, 305)])})
    Ti = G.invert_se3(T)
    src = TagMap({i: X @ Ti[:3, :3].T + Ti[:3, 3] + rng.normal(0.0, noise, X.shape) for i, X in dst.corners.items()})
    return src, dst


def test_a_known_motion_from_the_tags():
    T = _motion(140.0, (0.2, 1.0, 0.1), (60.0, -20.0, 35.0))
    src, dst = _maps(T, noise=0.15)
    tp = pose_from_tags(src, dst)
    assert tp is not None and tp.ids == [0, 1, 2, 3] and tp.left_out == [] and tp.rms_mm < 0.3
    off = np.linalg.norm(np.concatenate(list(src.corners.values())) @ (tp.T[:3, :3] - T[:3, :3]).T + (tp.T[:3, 3] - T[:3, 3]), axis=1)
    assert off.max() < 0.3
    one = pose_from_tags(TagMap({0: src.corners[0]}), dst)  # one tag is four points: a pose, a rougher one
    assert one is not None and one.ids == [0]
    assert G.rotation_angle_deg(one.T[:3, :3] @ T[:3, :3].T) < 3.0


def test_a_moved_tag_is_left_out_and_no_shared_tag_gives_no_pose():
    T = _motion(30.0, (0, 0, 1), (10.0, 5.0, 0.0))
    src, dst = _maps(T)
    src.corners[2] = src.corners[2] + np.array([12.0, -4.0, 0.0])  # this tag was moved between the scans
    tp = pose_from_tags(src, dst)
    assert tp is not None and tp.left_out == [2] and tp.ids == [0, 1, 3] and tp.rms_mm < 1e-6
    assert pose_from_tags(TagMap({7: src.corners[0]}), dst) is None
    assert pose_from_tags(TagMap(), dst) is None


def test_a_marker_mode_scans_map_is_read(tmp_path):
    corners = {"3": _square((0, 0, 300)).tolist(), "8": _square((50, 0, 300)).tolist()}
    p = tmp_path / "marker_map.json"
    p.write_text(json.dumps({"dictionary": "DICT_APRILTAG_36h11", "tag_mm": 20.0, "corners": corners,
                             "n_observations": {"3": 12, "8": 5}}), encoding="utf-8")
    m = load_tag_map(p)
    assert sorted(m.corners) == [3, 8] and m.views == {3: 12, 8: 5}
    assert np.allclose(m.corners[8], corners["8"])
    with pytest.raises(ValueError, match="maps for"):
        align_many([None, None], tags=[m])


# --------------------------------------------------------------------------- simulated scans
def _scan(tmp: Path, azimuth: float, seed: int) -> dict:
    """A simulated scan of the sphere on its tagged table, fused at its true poses."""
    sc = make_scene("sphere_on_plane", 0)
    cal = nominal_calibration(SIZE, realistic=True)
    traj = HandheldTrajectory(target=sc.target, t0=100.0 + float(cal.imu.time_offset), duration=N_FRAMES / 30.0, seed=seed,
                              baseline=cal.stereo.baseline, azimuth0_deg=azimuth, **dict(sc.meta.get("trajectory", {})))
    # the recording's seed is also the scene's, and the scene's seed places the tags: one scene for both scans
    rec = scan_recording(tmp / f"rec_{azimuth:.0f}", scene="sphere_on_plane", n_frames=N_FRAMES, image_size=SIZE,
                         realistic=True, depth_every=0, seed=0, trajectory=traj)
    gt = RecordingReader(rec).ground_truth_poses()
    first = min(gt)
    out = tmp / f"out_{azimuth:.0f}"
    T0inv = G.invert_se3(gt[first])
    process_recording(rec, out, params=ProcessParams(pipeline=PipelineParams(), export=("ply",)),
                      poses={i: T0inv @ T for i, T in gt.items()})
    return {"rec": rec, "out": out, "world": gt[first], "scene": sc}


@pytest.fixture(scope="module")
def sphere_scans(tmp_path_factory) -> list[dict]:
    tmp = tmp_path_factory.mktemp("tags")
    return [_scan(tmp, 0.0, 0), _scan(tmp, 150.0, 1)]


@pytest.mark.slow
def test_the_tags_a_scan_saw_lie_where_they_are(sphere_scans):
    for s in sphere_scans:
        m = tag_map(s["rec"], s["out"] / "trajectory.csv")
        W = G.invert_se3(s["world"])  # the simulator's world into the scan's
        err = {i: float(np.linalg.norm(X - (s["scene"].markers[i] @ W[:3, :3].T + W[:3, 3]), axis=1).max())
               for i, X in m.corners.items()}
        print(f"scan at {s['out'].name}: tags {sorted(m.corners)}, views {m.views}, worst corner off "
              f"{ {i: round(e, 2) for i, e in err.items()} } mm")
        assert len(m) >= 3 and max(err.values()) < 1.0
        s["tags"] = m


@pytest.mark.slow
def test_scans_of_a_sphere_are_placed_from_their_tags(sphere_scans):
    """A sphere on a plane fits itself turned any way about the vertical: the shape alone gives no
    trusted pose (or a wrong one), the tags do, and the alignment refines it."""
    a, b = sphere_scans
    T_true = G.invert_se3(a["world"]) @ b["world"]
    ma = a.get("tags") or tag_map(a["rec"], a["out"] / "trajectory.csv")
    mb = b.get("tags") or tag_map(b["rec"], b["out"] / "trajectory.csv")
    ca, cb = scan_cloud_from_mesh(a["out"] / "mesh.ply"), scan_cloud_from_mesh(b["out"] / "mesh.ply")
    tp = pose_from_tags(mb, ma)
    assert tp is not None and len(tp.ids) >= 2
    probe = np.concatenate(list(mb.corners.values()))  # points around the part, where a turn shows
    off_tags = np.linalg.norm(probe @ (tp.T[:3, :3] - T_true[:3, :3]).T + (tp.T[:3, 3] - T_true[:3, 3]), axis=1)
    shape = align_pair(cb, ca, same_setup=True)
    off_shape = np.linalg.norm(probe @ (shape.T[:3, :3] - T_true[:3, :3]).T + (shape.T[:3, 3] - T_true[:3, 3]), axis=1)
    print(f"tags {tp.ids} (rms {tp.rms_mm:.2f} mm): the tags' corners land {off_tags.max():.2f} mm off; "
          f"from the shape alone: {shape.verdict} The tags' corners land {off_shape.max():.1f} mm off")
    assert off_tags.max() < 1.5
    assert not shape.accepted or off_shape.max() > 5.0  # the shape alone does not get it right
    res = align_many([ca, cb], tags=[ma, mb])
    print("with the tags:", res.verdict, res.pairs[0].get("tags"), res.pairs[0]["verdict"])
    assert res.accepted and res.pairs[0]["start"] == "tags" and "from the tags" in res.pairs[0]["verdict"]
    off = np.linalg.norm(probe @ (res.T[1][:3, :3] - T_true[:3, :3]).T + (res.T[1][:3, 3] - T_true[:3, 3]), axis=1)
    print(f"placed from the tags: the tags' corners land {off.max():.2f} mm off")
    assert off.max() < 1.5
