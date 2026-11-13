"""Quality levels of the final mesh (maintenance plan F2 step 3): Standard (30 processed frames per second
of scanning, the default), Best (every frame), Preview.

The effective step from the frame times (``ohs.io.recording.effective_frame_step``), the names
(``ohs.server.protocol.normalize_quality``: "full" is the old name of "standard"), the settings per level
(``ohs.server.studio.process_params``) and one short 60 fps recording end to end: Standard processes
exactly the frames of ``frame_step=2``, Best every frame, and a second pass at given poses fuses the
frames that have a pose (the limit does not apply there).
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from ohs.app.config import Config, ConfigError, load_config
from ohs.io.recording import effective_frame_step, recorded_frame_rate
from ohs.server.protocol import QUALITIES, normalize_quality, quality_or_none


def _times(fps: float, n: int = 30, jitter: float = 0.0, seed: int = 0) -> np.ndarray:
    t = 100.0 + np.arange(n) / fps
    return t + np.random.default_rng(seed).normal(0.0, jitter, n) if jitter else t


def test_effective_step_from_the_frame_times():
    assert effective_frame_step(_times(60), 1, 30.0) == (2, pytest.approx(60.0))
    assert effective_frame_step(_times(30), 1, 30.0) == (1, pytest.approx(30.0))
    assert effective_frame_step(_times(60), 1, 0.0) == (1, pytest.approx(60.0))  # off: frame_step
    assert effective_frame_step(_times(60), 3, 0.0)[0] == 3
    assert effective_frame_step(_times(60), 3, 30.0)[0] == 3  # a larger frame_step wins
    assert effective_frame_step(_times(120), 1, 30.0)[0] == 4
    assert effective_frame_step(_times(45), 1, 30.0)[0] == 2  # halves round up
    # a jittery 59.9 fps camera (0.2 ms on the frame times) and a few dropped frames: still every 2nd
    t = np.delete(_times(59.9, 200, jitter=2e-4), [17, 90, 91])
    step, rate = effective_frame_step(t, 1, 30.0)
    assert step == 2 and rate == pytest.approx(59.9, abs=0.2)
    # the median, not the mean: a long pause in the recording does not change the rate
    t = np.concatenate([_times(60, 100), 200.0 + np.arange(100) / 60])
    assert recorded_frame_rate(t) == pytest.approx(60.0)
    # no rate (fewer than 2 frames): frame_step
    assert effective_frame_step([100.0], 2, 30.0) == (2, None) and recorded_frame_rate([]) is None


def test_quality_names():
    assert QUALITIES == ("standard", "best", "preview")
    assert [normalize_quality(q) for q in ("standard", "best", "preview", "full", " Full ")] == [
        "standard", "best", "preview", "standard", "standard"]
    for bad in ("fast", "", None, "fulll"):
        with pytest.raises(ValueError):
            normalize_quality(bad)
    assert quality_or_none("full") == "standard" and quality_or_none("nonsense") is None and quality_or_none(None) is None


def test_process_params_per_level():
    pytest.importorskip("open3d")
    from ohs.server.studio import process_params

    cfg = Config()
    std = process_params(cfg, "markerless", "standard", 1280)
    best = process_params(cfg, "markerless", "best", 1280)
    old = process_params(cfg, "markerless", "full", 1280)
    prev = process_params(cfg, "markerless", "preview", 1280)
    assert std.max_frame_rate == 30.0 and best.max_frame_rate == 0.0 and prev.max_frame_rate == 0.0
    assert old == std  # the old name is Standard
    assert std.pipeline.scale == best.pipeline.scale == 1.0 and prev.pipeline.scale == 0.5
    assert std.frame_step == best.frame_step == 1 and prev.frame_step == 1
    # Standard and Best differ only in the rate limit
    from dataclasses import replace
    assert replace(std, max_frame_rate=0.0) == best
    cfg.process.standard_frame_rate, cfg.process.frame_step = 0.0, 2
    assert process_params(cfg, "markerless", "standard", 1280).max_frame_rate == 0.0
    assert process_params(cfg, "markerless", "best", 1280).frame_step == 2  # frame_step applies to both levels
    with pytest.raises(ValueError):
        process_params(cfg, "markerless", "ultra", 1280)


def test_standard_frame_rate_setting(tmp_path):
    assert Config().process.standard_frame_rate == 30.0
    p = tmp_path / "c.toml"
    p.write_text("[process]\nstandard_frame_rate = 20\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.process.standard_frame_rate == 20.0 and isinstance(cfg.process.standard_frame_rate, float)
    p.write_text("[process]\nstandard_frame_rate = -1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="process.standard_frame_rate"):
        load_config(p)


# --------------------------------------------------------------------------- end to end, 60 fps
@pytest.fixture(scope="module")
def rec60(tmp_path_factory) -> Path:
    pytest.importorskip("open3d")
    from recordings import scan_recording

    return scan_recording(tmp_path_factory.mktemp("q60") / "rec", n_frames=8, fps=60.0, image_size=(640, 400),
                               seed=2, depth_every=0)


def _poses(path: Path) -> dict[int, np.ndarray]:
    with open(path, newline="", encoding="utf-8") as fh:
        return {int(r["index"]): np.array([float(r[f"T{i}{j}"]) for i in range(4) for j in range(4)]).reshape(4, 4)
                for r in csv.DictReader(fh) if r["ok"] == "1"}


def test_standard_and_best_on_a_60_fps_recording(rec60, tmp_path):
    from dataclasses import replace

    from ohs.app.pipeline import PipelineParams
    from ohs.app.process import ProcessParams, process_recording

    base = ProcessParams(pipeline=PipelineParams(scale=0.5), export=("ply",))
    std = process_recording(rec60, tmp_path / "std", params=replace(base, max_frame_rate=30.0))
    step2 = process_recording(rec60, tmp_path / "s2", params=replace(base, frame_step=2))
    best = process_recording(rec60, tmp_path / "best", params=base)
    idx = lambda r: [x["index"] for x in r["per_frame"]]  # noqa: E731
    assert idx(std) == idx(step2) == [0, 2, 4, 6] and idx(best) == list(range(8))
    fr = std["frames"]
    assert fr["step"] == 2 and fr["max_frame_rate"] == 30.0 and fr["recorded_fps"] == pytest.approx(60.0, abs=0.5)
    assert best["frames"]["step"] == 1 and best["frames"]["max_frame_rate"] == 0.0
    assert std["params"]["frame_step"] == 1  # the setting; the report's frames.step is the effective one
    # the second pass (Straighten, Fuse) at the Standard settings fuses exactly the first pass's frames
    poses = _poses(tmp_path / "std" / "trajectory.csv")
    again = process_recording(rec60, tmp_path / "fuse", params=replace(base, max_frame_rate=30.0), poses=poses)
    assert idx(again) == sorted(poses) and again["frames"]["without_pose"] == 8 - len(poses)
    # ... and poses of every frame (a track made at Best, or with the old every-frame "full") are all fused
    all_poses = _poses(tmp_path / "best" / "trajectory.csv")
    again = process_recording(rec60, tmp_path / "fuse_all", params=replace(base, max_frame_rate=30.0), poses=all_poses)
    assert idx(again) == sorted(all_poses) and len(all_poses) == 8
    with pytest.raises(ValueError, match="max_frame_rate"):
        process_recording(rec60, tmp_path / "bad", params=replace(base, max_frame_rate=-1.0))
