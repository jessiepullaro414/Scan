"""What light each frame of a recording holds (``RecordingWriter(kinds=True)``, ``write_frame(frame, kind=...)``,
``RecordingReader.kinds()``): the kinds round-trip, a recording without them reads as all ``both`` and is written
byte for byte as before, an incomplete recording still reads."""
from __future__ import annotations

import json

import numpy as np
import pytest

from ohs.io import recording as rec_mod
from ohs.io.recording import FRAME_KINDS, RecordingReader, RecordingWriter
from ohs.types import Event, ImuSample, StereoFrame


def _frame(i: int, w: int = 16, h: int = 8) -> StereoFrame:
    rng = np.random.default_rng(i)
    return StereoFrame(i, 1.0 + i / 60.0, rng.integers(0, 255, (h, w), dtype=np.uint8),
                       rng.integers(0, 255, (h, w), dtype=np.uint8))


def _write(path, ks=None, n=4, **kw):
    with RecordingWriter(path, workers=2, **kw) as w:
        w.write_imu([ImuSample(1.0, 2.0, np.zeros(3), np.array([0.0, 0.0, 9.81]))])
        w.write_event(Event(0.5, "button", "down"))
        for i in range(n):
            if ks is None:
                w.write_frame(_frame(i))
            else:
                w.write_frame(_frame(i), kind=ks[i])
    return path


def test_without_kinds_files_as_before(tmp_path):
    """Written as today: frames.csv has exactly its three columns, meta.json has no new key."""
    p = _write(tmp_path / "a")
    assert (p / "frames.csv").read_bytes() == (
        b"index,t_host,file\r\n0,1.000000,000000.png\r\n1,1.016667,000001.png\r\n2,1.033333,000002.png\r\n"
        b"3,1.050000,000003.png\r\n")
    meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
    assert sorted(meta) == ["created", "format_version", "image_size", "n_frames", "source"]
    assert sorted(x.name for x in p.iterdir()) == ["events.csv", "frames", "frames.csv", "imu.csv", "meta.json"]
    r = RecordingReader(p)
    assert not r.has_kinds and list(r.kinds()) == ["both"] * 4
    # a frame handed with the default kind to a writer without kinds is written the same way
    q = tmp_path / "b"
    with RecordingWriter(q, workers=2) as w:
        for i in range(4):
            w.write_frame(_frame(i), kind="both")
    assert (q / "frames.csv").read_bytes() == (p / "frames.csv").read_bytes()


def test_kinds_round_trip(tmp_path):
    kinds = ["dots", "lasers", "mixed", "dark", "both", None]
    p = _write(tmp_path / "k", ks=kinds, n=6, kinds=True)
    r = RecordingReader(p)
    assert r.has_kinds
    got = r.kinds()
    assert isinstance(got, np.ndarray) and list(got) == ["dots", "lasers", "mixed", "dark", "both", "both"]
    got[0] = "dark"  # a copy: the reader's own stays
    assert r.kinds()[0] == "dots"
    assert list(r.indices) == list(range(6)) and r.frame(1).index == 1
    assert r.meta["frame_kinds"] == {"both": 2, "dark": 1, "dots": 1, "lasers": 1, "mixed": 1}
    assert set(FRAME_KINDS) == {"both", "dots", "lasers", "mixed", "dark"}
    # code that ignores the kinds reads the frames as before
    assert [f.index for f in r] == list(range(6))
    assert np.array_equal(r.frame(2).left, _frame(2).left)


def test_bad_kinds_refused(tmp_path):
    with RecordingWriter(tmp_path / "x", workers=1) as w:
        with pytest.raises(ValueError):
            w.write_frame(_frame(0), kind="dots")  # this writer keeps no kinds: it would be lost
    with RecordingWriter(tmp_path / "y", workers=1, kinds=True) as w:
        with pytest.raises(ValueError):
            w.write_frame(_frame(0), kind="laser")
        w.write_frame(_frame(1), kind="lasers")
    assert list(RecordingReader(tmp_path / "y").kinds()) == ["lasers"]


def test_old_recording_reads_as_both(tmp_path):
    """A folder in the old layout, written by hand (no writer of today involved)."""
    p = _write(tmp_path / "old")
    (p / "meta.json").write_text(json.dumps({"format_version": 1, "n_frames": 4}), encoding="utf-8")
    r = RecordingReader(p)
    assert list(r.kinds()) == ["both"] * 4 and r.kinds().dtype.kind == "U"
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "frames.csv").write_text("index,t_host,file\n", encoding="utf-8")
    assert RecordingReader(empty).kinds().shape == (0,)


def test_incomplete_recording_with_kinds(tmp_path, monkeypatch):
    """A crash cut the last row short, and one frame file could not be written: the rows that are whole read with
    their kinds, the failed frame is listed with its kind."""
    real = rec_mod._imwrite

    def flaky(path, img, params):
        if path.endswith("000002.png"):
            raise OSError("disk full")
        real(path, img, params)

    monkeypatch.setattr(rec_mod, "_imwrite", flaky)
    p = tmp_path / "inc"
    with pytest.raises(OSError):
        _write(p, ks=["dots", "lasers", "dots", "lasers", "dots"], n=5, kinds=True)
    r = RecordingReader(p)
    assert r.incomplete and list(r.indices) == [0, 1, 3, 4]
    assert list(r.kinds()) == ["dots", "lasers", "lasers", "dots"]
    failed = r.failed_frames()
    assert [f["index"] for f in failed] == [2] and failed[0]["error"] == "disk full" and failed[0]["kind"] == "dots"
    # a crash mid-row: the cut last line is not read, the others keep their kinds
    raw = (p / "frames.csv").read_bytes()
    (p / "frames.csv").write_bytes(raw[:-9])
    r = RecordingReader(p)
    assert list(r.indices) == [0, 1, 3] and list(r.kinds()) == ["dots", "lasers", "lasers"]
