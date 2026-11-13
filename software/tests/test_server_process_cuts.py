"""``process_recording`` skips the frames inside ``cut`` events (written by the app's live rewind).

A ``cut`` event is an ``events.csv`` row ``t_host,cut,"t0,t1"``; every frame with
``t0 <= t_host <= t1`` is skipped. Malformed values are ignored with a warning, other event kinds
are not cuts, and a recording whose selected frames are all cut is an error.
"""
from __future__ import annotations

import csv
import logging
import shutil
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs.app.pipeline import PipelineParams  # noqa: E402
from ohs.app.process import ProcessParams, cut_intervals, process_recording  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from recordings import scan_recording  # noqa: E402

PARAMS = ProcessParams(pipeline=PipelineParams(scale=0.5), export=("ply",))


@pytest.fixture(scope="module")
def rec(tmp_path_factory) -> Path:
    return scan_recording(tmp_path_factory.mktemp("cuts") / "rec", n_frames=8, image_size=(640, 400), seed=3,
                               depth_every=0)


def _with_events(src: Path, dst: Path, rows: list[tuple[float, str, str]]) -> Path:
    shutil.copytree(src, dst)
    with open(dst / "events.csv", "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        for t, kind, value in rows:
            w.writerow([f"{t:.6f}", kind, value])
    return dst


def test_process_recording_skips_cut_frames(rec, tmp_path, caplog):
    times = RecordingReader(rec).times()
    t0, t1 = float(times[2]) - 1e-4, float(times[4]) + 1e-4
    r = _with_events(rec, tmp_path / "rec", [(t1, "cut", f"{t0:.6f},{t1:.6f}"), (t1, "cut", "not,a,cut"),
                                             (t1, "button", "down")])
    with caplog.at_level(logging.WARNING, logger="ohs.app.process"):
        report = process_recording(r, tmp_path / "out", params=PARAMS)
    assert "malformed cut" in caplog.text
    fr = report["frames"]
    assert fr["in_recording"] == 8 and fr["processed"] == 5 and fr["cut"] == 3
    assert [row["index"] for row in report["per_frame"]] == [0, 1, 5, 6, 7]
    assert np.allclose(report["cuts"], [[t0, t1]], atol=1e-5)
    with open(tmp_path / "out" / "trajectory.csv", newline="", encoding="utf-8") as fh:
        assert [int(row["index"]) for row in csv.DictReader(fh)] == [0, 1, 5, 6, 7]
    assert fr["tracked"] >= 4 and (tmp_path / "out" / "mesh.ply").is_file()


def test_frames_by_motion_restart_after_a_cut(rec, tmp_path):
    """Picking frames by motion: the first frame after a cut is always processed and the gap restarts there."""
    times = RecordingReader(rec).times()
    t0, t1 = float(times[2]) - 1e-4, float(times[4]) + 1e-4
    r = _with_events(rec, tmp_path / "rec_motion", [(t1, "cut", f"{t0:.6f},{t1:.6f}")])
    params = ProcessParams(pipeline=PipelineParams(scale=0.5), export=("ply",), frame_motion_deg=90.0, frame_max_gap=2)
    report = process_recording(r, tmp_path / "out", params=params)
    # candidates 0, 1, 5, 6, 7: 0 first, 5 after the cut, 7 by the cap (a gap of 2 from 5, not from 1)
    assert [row["index"] for row in report["per_frame"]] == [0, 5, 7] and report["frames"]["cut"] == 3
    sel = report["frames"]["selection"]
    assert (sel["picked_first"], sel["picked_by_cap"], sel["picked_by_rotation"], sel["gaps"]) == (2, 1, 0, {"2": 1})


def test_no_cuts_means_nothing_skipped_and_all_cut_is_an_error(rec, tmp_path):
    rep = process_recording(rec, tmp_path / "plain", params=ProcessParams(pipeline=PipelineParams(scale=0.5),
                                                                          export=("ply",), stop=3))
    assert rep["frames"]["processed"] == 3 and rep["frames"]["cut"] == 0 and rep["cuts"] == []
    times = RecordingReader(rec).times()
    r = _with_events(rec, tmp_path / "rec_all", [(float(times[-1]), "cut", f"{times[0] - 1:.6f},{times[-1] + 1:.6f}")])
    with pytest.raises(ValueError, match="cut"):
        process_recording(r, tmp_path / "out_all", params=PARAMS)


def test_cut_intervals_parsing(rec, tmp_path):
    r = _with_events(rec, tmp_path / "rec_parse", [(5.0, "cut", "3.5,1.25"), (6.0, "cut", "nan,2"), (7.0, "cut", " 1 , 2 "),
                                                   (8.0, "session", "pause")])
    assert cut_intervals(RecordingReader(r)) == [(1.25, 3.5), (1.0, 2.0)]
