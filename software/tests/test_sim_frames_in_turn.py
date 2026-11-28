"""The simulator's frames in turn (``make_scan_recording(light=...)``, ``Renderer.render(dot_share=, line_share=)``):
dot frames and laser frames in turn, mixed and dark frames, their kinds, and the defaults unchanged byte for byte.

The reference digests below were taken on the code before ``light`` / the shares existed (2026-11-27, on the
owner's mini PC): a recording or a render made with the new arguments left at their defaults must give exactly
these pictures, files and ground truth. They depend on the exact floating point of numpy and OpenCV; if they
differ on another machine, check first that the same code gives other digests there before anything else."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from ohs.io.recording import RecordingReader
from ohs.sim.datasets import make_scan_recording
from ohs.sim.nominal import nominal_calibration
from ohs.sim.render import Renderer, RenderParams
from ohs.sim.scene import make_scene
from ohs.sim.trajectory import HandheldTrajectory

SMALL = (320, 200)

#: digests of ``make_scan_recording(scene, n_frames=3, image_size=(320, 200), seed=5, lasers=...)`` before the change
REFERENCE = {
    ("bracket", 2): {
        "pictures": "d26a6e6c2a47bb0ab86efc2666358a4d016ebdfcb006a4807d63bce6286a1b11",
        "frames.csv": "f1739ff3874318826b051e55999fac9d6ed0d77dc341a3c4901a337d6d6da6c5",
        "imu.csv": "c4402444ca57f25d25428e53855754ccb84b9af379d8579db1c75ebb9f856796",
        "events.csv": "fc91078a928304193c49fea9f6fd25b091a2a3a90bfde796720d1deee5e8dead",
        "ground_truth/poses.csv": "50017fe20599ebc592fa41dd169050bb96dcdd0ca388c1b87486f240212ce101",
        "calibration.json": "38c6058f12fd8d7aa1d7843dbe0599504cdf083348eff54631857e3e8cc4328d",
        "depth": "ab597c66f477b229b3055915be4fa1ddf55c2e762b5a0a03ff6507d551cfd0f9",
        "meta": "8a0999a3ccff41c58c2e69097aaad351d9ec3aa05b35cda7f9f61298b85ff97c",
    },
    ("sphere_on_plane", 4): {
        "pictures": "e7fbc61511be35484e2bd48dabbb45067944be80a0fb39c26e550c4271445b57",
        "frames.csv": "f1739ff3874318826b051e55999fac9d6ed0d77dc341a3c4901a337d6d6da6c5",
        "imu.csv": "fced12cf1d397396ee478b40af9315351d50abfaada7fa0c2e0953ceaa1c2c38",
        "events.csv": "fc91078a928304193c49fea9f6fd25b091a2a3a90bfde796720d1deee5e8dead",
        "ground_truth/poses.csv": "4742a98ad874727880ef9fde5ff88185feb17827fab9533f7f2d1dbbaeabbf7f",
        "calibration.json": "a27ad38f0d953ba4cd09143e7c83ead463b231e83f205a9ff11bc27fbea1c359",
        "depth": "b272966037b3d904a0e9701618f8d96118d566fd0989b6c887b4a11d3e792022",
        "meta": "42012c3ec0c89111551251f972370825e1872a1d11ed55fa50dead82df549fd0",
    },
}
#: digests of direct renders (realistic calibration; raw, rectified and the aux layers) before the change
RENDER_REFERENCE = {"render": "d7c44b57ba52fa1efcb42b75d47c2e6f019cec4c7d54a2d7c25939ece9c78755",
                    "aux": "eccc064c3717f80f2762fcec11d67b94276dc187218e5bca54e4d9ddc89a23b7"}


def _digests(rec: Path) -> dict:
    r = RecordingReader(rec)
    out = {}
    h = hashlib.sha256()
    for f in r:
        h.update(np.ascontiguousarray(f.left).tobytes())
        h.update(np.ascontiguousarray(f.right).tobytes())
    out["pictures"] = h.hexdigest()
    for name in ("frames.csv", "imu.csv", "events.csv", "ground_truth/poses.csv", "calibration.json"):
        out[name] = hashlib.sha256((rec / name).read_bytes()).hexdigest()
    h = hashlib.sha256()
    for p in sorted((rec / "ground_truth" / "depth").glob("*.npy")):
        h.update(np.load(p).tobytes())
    out["depth"] = h.hexdigest()
    meta = dict(r.meta)
    meta.pop("created", None)
    out["meta"] = hashlib.sha256(json.dumps(meta, sort_keys=True).encode()).hexdigest()
    return out


@pytest.mark.parametrize("scene,lasers", sorted(REFERENCE))
def test_default_recording_unchanged(tmp_path, scene, lasers):
    rec = make_scan_recording(tmp_path / "rec", scene=scene, n_frames=3, image_size=SMALL, seed=5, lasers=lasers)
    assert _digests(rec) == REFERENCE[(scene, lasers)]
    assert not RecordingReader(rec).has_kinds  # no kinds written: frames.csv as before


def test_default_render_unchanged():
    calib = nominal_calibration(SMALL, realistic=True)
    sc = make_scene("bracket", 3)
    traj = HandheldTrajectory(target=sc.target, t0=0.0, duration=1.0, seed=3, baseline=calib.stereo.baseline,
                              **sc.meta["trajectory"])
    T = traj.poses(np.array([0.3]))[0]
    out = {}
    with Renderer(calib, None, sc, RenderParams(seed=3)) as r:
        h = hashlib.sha256()
        for rect in (False, True, False):
            for a in r.render(T, rectified=rect):
                h.update(np.ascontiguousarray(a).tobytes())
        out["render"] = h.hexdigest()
        aux = r.render_aux(T)
        h = hashlib.sha256()
        for k in sorted(aux):
            h.update(k.encode())
            h.update(np.ascontiguousarray(aux[k]).tobytes())
        out["aux"] = h.hexdigest()
    assert out == RENDER_REFERENCE


def test_light_kind():
    from ohs.sim.datasets import light_kind  # noqa: PLC0415

    assert light_kind(1.0, 1.0) == "both"
    assert light_kind(1.0, 0.0) == "dots" and light_kind(0.4, 0.0) == "dots"
    assert light_kind(0.0, 1.0) == "lasers" and light_kind(0.0, 0.2) == "lasers"
    assert light_kind(0.0, 0.0) == "dark"
    assert light_kind(0.3, 0.7) == "mixed" and light_kind(1.0, 0.5) == "mixed"


def _frames(rec: Path) -> list:
    return list(RecordingReader(rec))


def test_in_turn_recording(tmp_path):
    """Even frames dots only, odd frames lasers only, kinds written; the pictures are exactly those of the same scan
    rendered with the lasers off (even frames) or the dots off (odd frames), and the ground truth is today's."""
    kw = dict(scene="bracket", n_frames=4, image_size=SMALL, seed=5)
    turn = make_scan_recording(tmp_path / "turn", light="in_turn", **kw)
    today = make_scan_recording(tmp_path / "today", **kw)
    dots_only = make_scan_recording(tmp_path / "dots", lines=False, **kw)
    lasers_only = make_scan_recording(tmp_path / "lasers", dots=False, **kw)
    r = RecordingReader(turn)
    assert r.has_kinds and list(r.kinds()) == ["dots", "lasers", "dots", "lasers"]
    assert r.meta["light"] == "in_turn" and r.meta["frame_kinds"] == {"dots": 2, "lasers": 2}
    ft, fd, fl, f0 = _frames(turn), _frames(dots_only), _frames(lasers_only), _frames(today)
    for i in range(4):
        ref = fd[i] if i % 2 == 0 else fl[i]
        assert np.array_equal(ft[i].left, ref.left) and np.array_equal(ft[i].right, ref.right), i
        assert not np.array_equal(ft[i].left, f0[i].left)
    # the same scan: frame times, poses, depth, IMU, events and calibration are today's
    for name in ("imu.csv", "events.csv", "ground_truth/poses.csv", "calibration.json"):
        assert (turn / name).read_bytes() == (today / name).read_bytes(), name
    assert np.array_equal(r.times(), RecordingReader(today).times())
    for i in range(4):
        assert np.array_equal(r.ground_truth_depth(i), RecordingReader(today).ground_truth_depth(i))


def test_mixed_and_dark_frames(tmp_path):
    kw = dict(scene="sphere_on_plane", n_frames=4, image_size=SMALL, seed=2, depth_every=0)
    shares = [(1.0, 1.0), (0.3, 0.7), (0.0, 0.0), (0.7, 0.3)]
    rec = make_scan_recording(tmp_path / "mix", light=shares, **kw)
    r = RecordingReader(rec)
    assert list(r.kinds()) == ["both", "mixed", "dark", "mixed"]
    assert r.meta["light"] == [list(s) for s in shares]
    f = _frames(rec)
    # the first frame is a frame of today (the same random numbers drawn so far)
    today = _frames(make_scan_recording(tmp_path / "today", **kw))
    assert np.array_equal(f[0].left, today[0].left) and np.array_equal(f[0].right, today[0].right)
    # a dark frame: emitters off, only the room's light (darker than every lit frame)
    assert f[2].left.mean() < min(f[k].left.mean() for k in (0, 1, 3))
    off = _frames(make_scan_recording(tmp_path / "off", dots=False, lines=False, **kw))
    assert abs(float(f[2].left.mean()) - float(off[2].left.mean())) < 0.5
    with pytest.raises(ValueError):
        make_scan_recording(tmp_path / "bad", light=[(1.0, 1.0)], **kw)  # one pair per frame
    with pytest.raises(ValueError):
        make_scan_recording(tmp_path / "bad2", light="sometimes", **kw)


def test_render_shares():
    calib = nominal_calibration(SMALL)
    sc = make_scene("sphere_on_plane", 0)
    traj = HandheldTrajectory(target=sc.target, t0=0.0, duration=1.0, seed=0, baseline=calib.stereo.baseline,
                              **sc.meta["trajectory"])
    T = traj.poses(np.array([0.5]))[0]
    p = RenderParams(seed=1, noise_sigma=0.0, shot_noise=0.0, speckle=0.0)
    with Renderer(calib, None, sc, p) as r:
        full = r.render(T)[0].astype(float)
        same = r.render(T, dot_share=1.0, line_share=1.0)[0].astype(float)
        none = r.render(T, dot_share=0.0, line_share=0.0)[0].astype(float)
        half = r.render(T, dot_share=0.5, line_share=0.5)[0].astype(float)
    with Renderer(calib, None, sc, RenderParams(**{**p.__dict__, "dots_on": False, "lines_on": False})) as r:
        off = r.render(T)[0].astype(float)
    assert np.array_equal(full, same) and np.array_equal(none, off)
    lit = full > none + 20
    assert lit.mean() > 0.02
    # half the emitters' light: about half way between dark and full where nothing saturates
    ok = lit & (full < 250)
    assert np.median((half[ok] - none[ok]) / (full[ok] - none[ok])) == pytest.approx(0.5, abs=0.06)
