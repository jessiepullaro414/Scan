"""The tests' own recording cache (``tests/recordings.py``): a cached recording is what a fresh render
writes, a hit renders nothing, and what a recording is made from decides the key."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

import recordings
from ohs.sim.datasets import make_scan_recording
from ohs.sim.scene import make_scene
from ohs.sim.trajectory import HandheldTrajectory

TINY = dict(n_frames=2, image_size=(160, 100), seed=5, depth_every=1)


def files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("OHS_TEST_RECORDINGS", str(tmp_path / "cache"))
    monkeypatch.setattr(recordings, "cache_stats", {"hits": 0, "renders": 0, "uncached": 0})
    return tmp_path / "cache"


def test_a_cached_recording_is_a_fresh_one_byte_for_byte(tmp_path, cache, monkeypatch):
    fresh = files(make_scan_recording(tmp_path / "fresh", **TINY))
    first = recordings.scan_recording(tmp_path / "first", **TINY)  # rendered into the cache, then copied
    assert recordings.cache_stats == {"hits": 0, "renders": 1, "uncached": 0}

    def no_render(*a, **k):
        raise AssertionError("a hit must not render")

    monkeypatch.setattr(recordings, "make_scan_recording", no_render)
    again = recordings.scan_recording(tmp_path / "again", **TINY)
    assert recordings.cache_stats == {"hits": 1, "renders": 1, "uncached": 0}
    assert len(fresh) >= 8 and {"meta.json", "frames.csv", "calibration.json", "imu.csv"} <= set(fresh)
    for got in (files(first), files(again)):
        assert set(got) == set(fresh)
        for name, data in fresh.items():
            if name == "meta.json":  # the same but for the time of the render
                a, b = json.loads(data), json.loads(got[name])
                assert a.pop("created") and b.pop("created") and a == b
            else:
                assert got[name] == data, name
    # a test may change its copy: the cache keeps the recording as rendered
    (again / "frames.csv").write_text("changed", encoding="utf-8")
    third = recordings.scan_recording(tmp_path / "third", **TINY)
    assert files(third)["frames.csv"] == fresh["frames.csv"]
    # where a recording already is: an error, unless overwrite is asked for
    with pytest.raises(FileExistsError):
        recordings.scan_recording(again, **TINY)
    assert files(recordings.scan_recording(again, overwrite=True, **TINY))["frames.csv"] == fresh["frames.csv"]


def test_the_key_is_the_arguments_and_the_renderer(tmp_path, cache, monkeypatch):
    calls = []
    real = recordings.make_scan_recording

    def counted(out, **k):
        calls.append(k)
        return real(out, **k)

    monkeypatch.setattr(recordings, "make_scan_recording", counted)
    recordings.scan_recording(tmp_path / "a", **TINY)
    recordings.scan_recording(tmp_path / "b", **{**TINY, "seed": 6})  # another argument: another recording
    assert len(calls) == 2
    # a trajectory (a dataclass with arrays) is part of the key by its fields
    path = HandheldTrajectory(target=make_scene("sphere_on_plane", 5).target, t0=100.0, duration=1.0, seed=3)
    recordings.scan_recording(tmp_path / "c", trajectory=path, **TINY)
    recordings.scan_recording(tmp_path / "d", trajectory=path, **TINY)
    assert len(calls) == 3 and recordings.cache_stats["hits"] == 1
    # a Scene object cannot be described by the key: rendered every time
    scene = make_scene("sphere_on_plane", 5)
    recordings.scan_recording(tmp_path / "e", scene=scene, **TINY)
    recordings.scan_recording(tmp_path / "f", scene=scene, **TINY)
    assert len(calls) == 5 and recordings.cache_stats["uncached"] == 2
    # another renderer version: nothing of the old one is used
    version = recordings.renderer_version()
    assert len(version) == 12 and (cache / f"v{version}").is_dir()
    monkeypatch.setattr(recordings, "_version", "0" * 12)
    recordings.scan_recording(tmp_path / "g", **TINY)
    assert len(calls) == 6 and (cache / ("v" + "0" * 12)).is_dir()


def test_the_renderer_version_follows_the_simulator_source(tmp_path, monkeypatch):
    src = tmp_path / "ohs" / "sim"
    src.mkdir(parents=True)
    (src / "render.py").write_text("A = 1\n", encoding="utf-8")
    monkeypatch.setattr(recordings, "_SOFTWARE", tmp_path)
    monkeypatch.setattr(recordings, "_SOURCES", ("ohs/sim",))
    monkeypatch.setattr(recordings, "_version", None)
    v1 = recordings.renderer_version()
    (src / "render.py").write_bytes(b"A = 1\r\n")  # line endings are not a change
    monkeypatch.setattr(recordings, "_version", None)
    assert recordings.renderer_version() == v1
    (src / "render.py").write_text("A = 2\n", encoding="utf-8")
    monkeypatch.setattr(recordings, "_version", None)
    assert recordings.renderer_version() != v1


def test_the_cache_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setenv("OHS_TEST_RECORDINGS", "off")
    monkeypatch.setattr(recordings, "cache_stats", {"hits": 0, "renders": 0, "uncached": 0})
    assert recordings.cache_dir() is None
    recordings.scan_recording(tmp_path / "a", **TINY)
    recordings.scan_recording(tmp_path / "b", **TINY)
    assert recordings.cache_stats == {"hits": 0, "renders": 0, "uncached": 2}
    monkeypatch.delenv("OHS_TEST_RECORDINGS")
    assert recordings.cache_dir().name == "ohs-test-recordings"


def test_arguments_count_by_name_however_they_are_passed(tmp_path, cache, monkeypatch):
    calls = []
    real = recordings.make_scan_recording
    monkeypatch.setattr(recordings, "make_scan_recording", lambda out, **k: (calls.append(k), real(out, **k))[1])
    recordings.scan_recording(tmp_path / "a", "sphere_on_plane", **TINY)  # the scene by position
    recordings.scan_recording(tmp_path / "b", scene="sphere_on_plane", **TINY)  # and by name: the same recording
    assert len(calls) == 1 and calls[0]["scene"] == "sphere_on_plane" and recordings.cache_stats["hits"] == 1


def _views(n: int, seed: int = 0, tag: object = None) -> tuple:
    _views.calls += 1
    return [np.arange(n) + seed, np.full((2, 2), seed, np.uint8)], {"seed": seed}


def _write_folder(out: Path, text: str = "x", n: int = 2) -> str:
    _write_folder.calls += 1
    out.mkdir(parents=True)
    for i in range(n):
        (out / f"{i}.txt").write_text(text * (i + 1), encoding="utf-8")
    return "not kept"


def test_results_and_other_recordings_are_cached_too(tmp_path, cache):
    _views.calls = _write_folder.calls = 0
    a = recordings.cached_result(_views, n=4, seed=3)
    b = recordings.cached_result(_views, n=4, seed=3)
    assert _views.calls == 1 and np.array_equal(a[0][0], b[0][0]) and a[0][1].dtype == b[0][1].dtype == np.uint8 and a[1] == b[1]
    b[0][0][:] = -1  # every call gets its own copy
    assert np.array_equal(recordings.cached_result(_views, n=4, seed=3)[0][0], np.arange(4) + 3)
    recordings.cached_result(_views, n=4, seed=4)
    assert _views.calls == 2
    assert recordings.cached_result(_views, n=3, tag=object()) and _views.calls == 3  # not describable: computed
    recordings.cached_result(_views, n=3, tag=object())
    assert _views.calls == 4 and recordings.cache_stats["uncached"] == 2
    # a function that writes a folder; the key holds its own source, as it is not the simulator's
    first = recordings.cached_recording(tmp_path / "f1", _write_folder, text="ab", n=3)
    second = recordings.cached_recording(tmp_path / "f2", _write_folder, text="ab", n=3)
    assert _write_folder.calls == 1 and files(first) == files(second) and len(files(first)) == 3
    recordings.cached_recording(tmp_path / "f3", _write_folder, text="ab", n=2)
    assert _write_folder.calls == 2
    names = sorted(p.name for p in (cache / f"v{recordings.renderer_version()}").iterdir())
    assert sum(n.startswith("_views-") for n in names) == 2 and sum(n.startswith("_write_folder-") for n in names) == 2
