"""``tools/memory_peaks.py`` (FEATURE_PLAN R5): a process's memory read with Windows' own calls, sampled
with a deadline, and the peaks per label (a peak between two samples is not lost)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import memory_peaks as mp  # noqa: E402

windows = pytest.mark.skipif(sys.platform != "win32", reason="reads Windows' process counters")


@windows
def test_reads_this_process_and_sees_an_allocation():
    m0 = mp.process_memory(os.getpid())
    assert m0 and m0["ws"] > 0 and m0["peak_ws"] >= m0["ws"] and m0["peak_commit"] >= m0["commit"] > 0
    x = np.ones(200_000_000 // 8)  # 200 MB, touched
    m1 = mp.process_memory(os.getpid())
    assert m1["commit"] - m0["commit"] > 180e6 and m1["ws"] - m0["ws"] > 180e6
    del x
    assert mp.process_memory(10 ** 9) is None  # no such process


_CHILD = """
import sys
print("ready", flush=True)
sys.stdin.readline()
b = bytearray(300_000_000)
b[::4096] = b"\\x01" * len(b[::4096])  # every page touched
del b  # gone before the next sample: only Windows' own peak saw it
print("done", flush=True)
sys.stdin.readline()
"""


@windows
def test_sampler_logs_labels_and_their_peaks(tmp_path):
    """A process of its own is watched, as the tool is used. (Watching this test process failed in review
    whenever an earlier test had used more memory: Windows' peak counter is for the process's whole life,
    so a peak under an earlier one is seen only if a sample falls on it.)"""
    import subprocess

    # the real interpreter, not the venv's launcher, whose own memory says nothing
    exe = getattr(sys, "_base_executable", None) or sys.executable
    child = subprocess.Popen([exe, "-I", "-c", _CHILD], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    log = tmp_path / "m.csv"
    try:
        assert child.stdout.readline().strip() == "ready"
        with mp.Sampler(child.pid, log, every=0.05, seconds=30) as s:
            s.mark("before")
            time.sleep(0.2)
            s.mark("big")
            child.stdin.write("go\n")
            child.stdin.flush()
            assert child.stdout.readline().strip() == "done"
            s.mark("after")
            time.sleep(0.2)
    finally:
        child.kill()
        child.wait(10)
    rows = mp.read_log(log)
    assert len(rows) >= 4 and set(mp.FIELDS) <= set(rows[0])
    summ = mp.summarize(rows)
    assert summ["big"]["peak_ws"] - summ["before"]["peak_ws"] > 250e6  # the peak between samples
    assert summ["after"]["ws"] < summ["big"]["peak_ws"] - 250e6  # and it came back
    assert summ["after"]["peak_ws"] < summ["big"]["peak_ws"]  # not counted again for the next label
    assert summ["before"]["avail_min"] > 0


def test_summary_of_rows_without_windows_counters():
    rows = [{"t": "1", "label": "a", "ws": "10", "peak_ws": "10", "commit": "5", "peak_commit": "5", "avail": ""},
            {"t": "2", "label": "b", "ws": "20", "peak_ws": "90", "commit": "6", "peak_commit": "5", "avail": "7"},
            {"t": "3", "label": "c", "ws": "15", "peak_ws": "90", "commit": "6", "peak_commit": "5", "avail": "8"}]
    s = mp.summarize(rows)
    assert s["b"]["peak_ws"] == 90 and s["c"]["peak_ws"] == 15 and s["b"]["peak_commit"] == 6
    assert "avail_min" not in s["a"] and s["c"]["avail_min"] == 8 and s["b"]["seconds"] == 0
