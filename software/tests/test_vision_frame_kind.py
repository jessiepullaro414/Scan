"""Telling from a frame's pictures what light it holds (``ohs.vision.frame_kind``): dots only, lasers only, both,
a mix (the switch fell inside the exposure) or none, on in-turn recordings of the simulator.

Scenes: the speckled bracket (its table's and metal's own texture), the standing thin walls of the validation
(``tools/preset_study.py fins_scene``) and the car panel. 640 px wide here; 1280 px (the camera's size) is slow."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

SOFTWARE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE))

from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.sim.datasets import make_scan_recording  # noqa: E402
from ohs.vision.frame_kind import FrameKindParams, FrameLight, KindSteadier, frame_kind  # noqa: E402


def _scene(name: str, seed: int):
    if name == "fins":
        from tools.preset_study import fins_scene  # noqa: PLC0415

        return fins_scene(seed)
    return name


SCENES = ("bracket", "fins", "car_panel")
#: (dot share, laser share) per frame: two frames in turn, today's frame, two mixes, a dark frame
SHARES = [(1.0, 0.0), (0.0, 1.0), (1.0, 1.0), (0.3, 0.7), (0.7, 0.3), (0.0, 0.0)]


def _check(tmp_path, scene: str, size: tuple[int, int], seed: int) -> None:
    rec = make_scan_recording(tmp_path / f"{scene}-{size[0]}", scene=_scene(scene, seed), n_frames=len(SHARES),
                              image_size=size, seed=seed, light=SHARES, depth_every=0)
    r = RecordingReader(rec)
    truth = list(r.kinds())
    assert truth == ["dots", "lasers", "both", "mixed", "mixed", "dark"]
    got = [frame_kind(f.left, f.right) for f in r]
    assert [g.kind for g in got] == truth, [(g.kind, round(g.dots, 2), round(g.lasers, 2)) for g in got]
    dots, lasers = np.array([g.dots for g in got]), np.array([g.lasers for g in got])
    # the shares order correctly: more dots in the 0.7 / 0.3 mix than in the 0.3 / 0.7 one, fewer lasers
    assert dots[4] > dots[3] and lasers[3] > lasers[4]
    assert dots[0] > dots[4] and lasers[1] > lasers[3]
    # in-turn frames: the other light is (nearly) absent
    assert lasers[0] < 0.1 and dots[1] < 0.1 and dots[5] < 0.1 and lasers[5] < 0.1


@pytest.mark.parametrize("scene", SCENES)
def test_kinds_640(tmp_path, scene):
    _check(tmp_path, scene, (640, 400), seed=1)


@pytest.mark.slow
@pytest.mark.parametrize("scene", SCENES)
def test_kinds_1280(tmp_path, scene):
    _check(tmp_path, scene, (1280, 800), seed=2)


def test_in_turn_recording_640(tmp_path):
    """Every frame of an in-turn recording (no mixes) is told right."""
    rec = make_scan_recording(tmp_path / "turn", scene="bracket", n_frames=6, image_size=(640, 400), seed=4,
                              light="in_turn", depth_every=0)
    r = RecordingReader(rec)
    assert [frame_kind(f.left, f.right).kind for f in r] == list(r.kinds())


def test_brightness_and_texture(tmp_path):
    """Darker or brighter pictures (exposure, gain) and a strongly textured part do not change the answer."""
    from ohs.sim.scene import NoiseAlbedo, make_scene  # noqa: PLC0415

    scene = make_scene("bracket", 0)
    for p in scene.primitives:  # a printed or painted speckle, ten times the bead-blasted metal's contrast
        p.albedo = NoiseAlbedo(mean=0.55, contrast=0.35, scale_mm=0.6, seed=9)
    scene.primitives[0].albedo = NoiseAlbedo(mean=0.45, contrast=0.35, scale_mm=0.8, seed=8)
    rec = make_scan_recording(tmp_path / "tex", scene=scene, n_frames=4, image_size=(640, 400), seed=3,
                              light=[(1.0, 0.0), (0.0, 1.0), (1.0, 1.0), (0.0, 0.0)], depth_every=0)
    r = RecordingReader(rec)
    for f, truth in zip(r, r.kinds()):
        for gain in (0.5, 1.0, 1.6):
            left = np.clip(f.left.astype(np.float32) * gain, 0, 255).astype(np.uint8)
            right = np.clip(f.right.astype(np.float32) * gain, 0, 255).astype(np.uint8)
            got = frame_kind(left, right)
            assert got.kind == truth, (truth, gain, got)


def test_api_and_steadier():
    rng = np.random.default_rng(0)
    flat = np.full((400, 640), 30, np.uint8) + rng.integers(0, 3, (400, 640), dtype=np.uint8)
    got = frame_kind(flat, flat)
    assert isinstance(got, FrameLight) and got.kind == "dark" and got.dots < 0.1 and got.lasers < 0.1
    assert frame_kind(flat, None).kind == "dark"  # one picture is enough
    with pytest.raises(ValueError):
        frame_kind(flat.astype(np.float32), flat)
    assert FrameKindParams().work_width > 0
    # the steadier learns the full-light readings from frames in turn and decides later frames against them
    st = KindSteadier()
    for k in ["dots", "lasers"] * 3:  # a room darker than the simulator's: every reading at 0.5 of full
        got = st.update(FrameLight(k, 0.5 if k == "dots" else 0.0, 0.5 if k == "lasers" else 0.0))
        assert got.kind == k
    assert st.expected() == "dots"  # the alternation predicts the next frame
    # both lights at about the learnt full readings: both, though frame_kind alone, against the simulator's full
    # readings, said mixed
    assert st.update(FrameLight("mixed", 0.5, 0.48)).kind == "both"
    assert st.update(FrameLight("mixed", 0.15, 0.45)).kind == "mixed"  # a real mix stays a mix
    assert st.expected() is None
