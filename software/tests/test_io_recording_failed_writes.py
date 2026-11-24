"""A frame whose file could not be written is not listed as a frame, and meta.json is replaced
whole or not at all (REVIEW F6).

The failure is still raised by ``RecordingWriter.close()``. The failed frames are kept apart, in
``frames_failed.csv`` (index, t_host, file, error), written when the failure is found, and
``meta.json`` says ``incomplete``; the indices and timestamps of the other frames are as they were,
so the gap shows.
"""
from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from ohs.io import recording as rec_mod
from ohs.io.recording import RecordingReader, RecordingWriter
from ohs.types import StereoFrame

N = 8


def _frames(seed: int = 0) -> list[StereoFrame]:
    rng = np.random.default_rng(seed)
    return [StereoFrame(i, 10.0 + i / 30, rng.integers(0, 255, (40, 64), dtype=np.uint8),
                        rng.integers(0, 255, (40, 64), dtype=np.uint8)) for i in range(N)]


def _fail_frames(monkeypatch, bad: set[int]) -> None:
    """cv2.imwrite returns False (as on a full disk) for the frame files of the indices in ``bad``."""
    real = cv2.imwrite

    def flaky(path, img, params=()):
        if int(str(path)[-10:-4]) in bad:
            return False
        return real(path, img, params)

    monkeypatch.setattr(cv2, "imwrite", flaky)


def _record(path, frames, bad: set[int]) -> None:
    with pytest.raises(OSError, match="could not write frame file"):
        with RecordingWriter(path, source="sim", workers=2) as w:
            for f in frames:
                w.write_frame(f)


@pytest.mark.parametrize("bad", [{3}, {0}, {2, 5}], ids=["middle", "first", "two"])
def test_a_failed_frame_is_not_listed_and_its_gap_is_kept(tmp_path, monkeypatch, bad):
    frames = _frames()
    _fail_frames(monkeypatch, bad)
    _record(tmp_path / "rec", frames, bad)
    monkeypatch.undo()
    r = RecordingReader(tmp_path / "rec")
    good = [f for f in frames if f.index not in bad]
    assert len(r) == len(good) and list(r.indices) == [f.index for f in good]  # not renumbered
    assert r.times().tolist() == pytest.approx([f.t_host for f in good], abs=1e-6)
    for i, f in enumerate(good):  # every listed frame reads
        g = r.frame(i)
        assert g.index == f.index and np.array_equal(g.left, f.left) and np.array_equal(g.right, f.right)
    failed = r.failed_frames()
    assert [d["index"] for d in failed] == sorted(bad)
    for d in failed:
        f = frames[d["index"]]
        assert d["t_host"] == pytest.approx(f.t_host, abs=1e-6) and d["file"] == f"{f.index:06d}.png"
        assert "could not write frame file" in d["error"]
    assert r.incomplete and r.meta["incomplete"] is True and r.meta["n_frames_failed"] == len(bad)
    assert r.meta["n_frames"] == N and r.meta["image_size"] == [64, 40] and not r.meta_damaged


def test_a_complete_recording_is_as_before(tmp_path):
    with RecordingWriter(tmp_path / "rec", source="sim") as w:
        for f in _frames():
            w.write_frame(f)
    assert not (tmp_path / "rec" / "frames_failed.csv").exists()
    assert not (tmp_path / "rec" / "meta.json.tmp").exists()
    r = RecordingReader(tmp_path / "rec")
    assert len(r) == N and r.failed_frames() == [] and not r.incomplete and not r.meta_damaged
    assert "incomplete" not in r.meta and "n_frames_failed" not in r.meta and r.meta["n_frames"] == N


def test_a_cut_meta_json_does_not_stop_the_reader(tmp_path, caplog):
    with RecordingWriter(tmp_path / "rec", source="sim") as w:
        for f in _frames():
            w.write_frame(f)
    (tmp_path / "rec" / "meta.json").write_text("{", encoding="utf-8")
    with caplog.at_level("WARNING", logger="ohs.io.recording"):
        r = RecordingReader(tmp_path / "rec")
    assert r.meta_damaged and r.meta == {} and "meta.json" in caplog.text
    assert len(r) == N and r.frame(N - 1).index == N - 1  # the frames read as before
    (tmp_path / "rec" / "meta.json").write_bytes(b"\xff\xfe[1, 2")  # not text either
    assert RecordingReader(tmp_path / "rec").meta_damaged
    (tmp_path / "rec" / "meta.json").write_text("[1, 2]", encoding="utf-8")  # JSON, but not an object
    assert RecordingReader(tmp_path / "rec").meta_damaged
    (tmp_path / "rec" / "meta.json").unlink()  # missing: empty, as before, and not damaged
    r = RecordingReader(tmp_path / "rec")
    assert r.meta == {} and not r.meta_damaged


def test_an_interrupted_meta_write_leaves_the_old_meta_whole(tmp_path, monkeypatch):
    """As if the program stopped after the temporary file was written and before it replaced meta.json."""
    w = RecordingWriter(tmp_path / "rec", source="sim")
    for f in _frames():
        w.write_frame(f)
    before = (tmp_path / "rec" / "meta.json").read_bytes()

    def stopped(src, dst):
        raise KeyboardInterrupt

    monkeypatch.setattr(rec_mod.os, "replace", stopped)
    with pytest.raises(KeyboardInterrupt):
        w.close()
    monkeypatch.undo()
    assert (tmp_path / "rec" / "meta.json").read_bytes() == before
    meta = json.loads(before.decode("utf-8"))
    assert meta["n_frames"] == 0 and meta["source"] == "sim"  # the one written when the recording began
    r = RecordingReader(tmp_path / "rec")
    assert not r.meta_damaged and len(r) == N
