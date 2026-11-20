"""The memory a process takes, sampled with a deadline (FEATURE_PLAN R5, "Projects larger than memory").

    python tools/memory_peaks.py watch <pid> <log.csv> [--seconds 600] [--every 0.5]
    python tools/memory_peaks.py summary <log.csv> [<log.csv> ...]

**watch** samples one process every ``--every`` seconds until it ends or ``--seconds`` have passed,
and writes one row per sample: its working set and its commit (private bytes) now, the peaks
Windows keeps for the process (``PeakWorkingSetSize`` and ``PeakPagefileUsage``, so a peak between
two samples is not missed), the graphics memory it holds (``GPU Process Memory``, shared and
dedicated, summed over its adapters: on a laptop or mini PC with integrated graphics the shared
part is ordinary RAM), and the system's available memory, hard page reads per second ("Pages
Input/sec": paging back in, the sign of swapping) and commit charge. A column ``label`` holds
whatever :meth:`Sampler.mark` last set, so a driver can cut one server's life into its jobs.

**summary** prints, per label, the largest working set, commit and GPU memory seen, the least
available memory and the most pages read in per second.

Nothing but ``ctypes`` and Windows' own ``psapi`` and ``pdh`` (no new dependency); on another
system :func:`process_memory` answers ``None`` and the GPU and system columns stay empty.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

FIELDS = ("t", "label", "ws", "peak_ws", "commit", "peak_commit", "gpu_shared", "gpu_dedicated",
          "avail", "pages_in_s", "committed")
_WINDOWS = sys.platform == "win32"


class _PMC(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS_EX
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


def _handle(pid: int) -> Any:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x1000 | 0x0010, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ
    return h or None


def _close(h: Any) -> None:
    if h:
        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(h))


def _alive(h: Any) -> bool:
    code = ctypes.c_ulong()
    ok = ctypes.WinDLL("kernel32").GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def _read(h: Any) -> dict | None:
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    if not ctypes.WinDLL("psapi").GetProcessMemoryInfo(ctypes.c_void_p(h), ctypes.byref(pmc), pmc.cb):
        return None
    return {"ws": int(pmc.WorkingSetSize), "peak_ws": int(pmc.PeakWorkingSetSize), "commit": int(pmc.PrivateUsage),
            "peak_commit": int(pmc.PeakPagefileUsage)}


def process_memory(pid: int) -> dict | None:
    """``{ws, peak_ws, commit, peak_commit}`` in bytes for a running process (None if it cannot be read)."""
    if not _WINDOWS:
        return None
    h = _handle(pid)
    if not h:
        return None
    try:
        return _read(h)
    finally:
        _close(h)


class _ItemValue(ctypes.Structure):  # PDH_FMT_COUNTERVALUE with a double
    _fields_ = [("CStatus", ctypes.c_ulong), ("doubleValue", ctypes.c_double)]


class _Item(ctypes.Structure):  # PDH_FMT_COUNTERVALUE_ITEM_W
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _ItemValue)]


class Counters:
    """Windows performance counters through ``pdh.dll``: the GPU memory of one process and three
    system figures. Every value is ``None`` where a counter is missing (another system, an old Windows)."""

    _PATHS = {"gpu_shared": r"\GPU Process Memory(*)\Shared Usage",
              "gpu_dedicated": r"\GPU Process Memory(*)\Dedicated Usage",
              "avail": r"\Memory\Available Bytes", "pages_in_s": r"\Memory\Pages Input/sec",
              "committed": r"\Memory\Committed Bytes"}

    def __init__(self, pid: int) -> None:
        self.prefix = f"pid_{int(pid)}_"
        self.query = ctypes.c_void_p()
        self.counters: dict[str, ctypes.c_void_p] = {}
        if not _WINDOWS:
            return
        self.pdh = ctypes.WinDLL("pdh")
        if self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)) != 0:
            return
        for key, path in self._PATHS.items():
            c = ctypes.c_void_p()
            if self.pdh.PdhAddEnglishCounterW(self.query, path, None, ctypes.byref(c)) == 0:
                self.counters[key] = c
        self.pdh.PdhCollectQueryData(self.query)  # a rate needs two collections

    def read(self) -> dict:
        out: dict[str, float | None] = {k: None for k in self._PATHS}
        if not self.counters or self.pdh.PdhCollectQueryData(self.query) != 0:
            return out
        for key, c in self.counters.items():
            size, count = ctypes.c_ulong(0), ctypes.c_ulong(0)
            self.pdh.PdhGetFormattedCounterArrayW(c, 0x200, ctypes.byref(size), ctypes.byref(count), None)
            if not size.value:
                continue
            buf = ctypes.create_string_buffer(size.value)
            if self.pdh.PdhGetFormattedCounterArrayW(c, 0x200, ctypes.byref(size), ctypes.byref(count), buf) != 0:
                continue
            items = ctypes.cast(buf, ctypes.POINTER(_Item))
            if key.startswith("gpu_"):  # the process's own instances (one per adapter and segment kind)
                vals = [items[i].FmtValue.doubleValue for i in range(count.value)
                        if (items[i].szName or "").startswith(self.prefix) and items[i].FmtValue.CStatus in (0, 1)]
                out[key] = float(sum(vals)) if vals else 0.0
            elif count.value:
                out[key] = float(items[0].FmtValue.doubleValue)
        return out

    def close(self) -> None:
        if self.counters:
            self.pdh.PdhCloseQuery(self.query)
            self.counters = {}


class Sampler:
    """``with Sampler(pid, log) as s: s.mark("fuse"); ...``: samples ``pid`` in a thread every ``every`` s
    until it ends, ``seconds`` pass or the block ends; :attr:`rows` holds every sample."""

    def __init__(self, pid: int, log: str | Path | None = None, *, every: float = 0.5, seconds: float = 7200.0) -> None:
        self.pid, self.log, self.every, self.seconds = int(pid), Path(log) if log else None, float(every), float(seconds)
        self.label = ""
        self.rows: list[dict] = []
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="memory-peaks", daemon=True)

    def mark(self, label: str) -> dict | None:
        """Close the current label with one sample (a peak since the last sample counts for it), set
        ``label`` for the next samples and return one sample taken under it."""
        self._sample()
        self.label = str(label)
        return self._sample()

    def _sample(self) -> dict | None:
        with self._lock:  # the thread and mark() share the counters and the log
            label = self.label  # the label before the reading: a sample never counts for a later label
            m = process_memory(self.pid)
            if m is None:
                return None
            row = {"t": round(time.time(), 3), "label": label, **m, **self._counters.read()}
            self.rows.append(row)
            if self._writer is not None:
                self._writer.writerow(row)
                self._file.flush()
            return row

    def _run(self) -> None:
        deadline = time.monotonic() + self.seconds
        h = _handle(self.pid) if _WINDOWS else None
        try:
            while not self._stop.is_set() and time.monotonic() < deadline and h and _alive(h):
                self._sample()
                self._stop.wait(self.every)
        finally:
            _close(h)

    def __enter__(self) -> "Sampler":
        self._counters = Counters(self.pid)
        self._file = open(self.log, "a", newline="", encoding="utf-8") if self.log else None  # noqa: SIM115
        self._writer = csv.DictWriter(self._file, FIELDS) if self._file else None
        if self._writer is not None and self._file.tell() == 0:
            self._writer.writeheader()
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(10.0)
        self._sample()
        self._counters.close()
        if self._file:
            self._file.close()


def summarize(rows: list[dict]) -> dict[str, dict]:
    """Per label: the largest ``ws``, ``commit``, ``gpu_shared``, ``gpu_dedicated`` and ``pages_in_s``,
    the least ``avail``, the number of samples and the seconds they span.

    ``peak_ws`` and ``peak_commit`` are the label's true peaks: the largest sample, or Windows' own
    peak of the process when that rose while the label was set (then the peak was reached in it,
    also between two samples)."""
    out: dict[str, dict] = {}

    def num(v: Any) -> float | None:
        try:
            return None if v in (None, "") else float(v)
        except (TypeError, ValueError):
            return None

    before = {"peak_ws": 0.0, "peak_commit": 0.0}  # Windows' peaks when the label was set
    last = dict(before)
    prev_label = None
    for r in rows:
        label = str(r.get("label", ""))
        if label != prev_label:
            before, prev_label = dict(last), label
        s = out.setdefault(label, {"n": 0, "t0": None, "t1": None})
        for k, cur in (("peak_ws", "ws"), ("peak_commit", "commit")):
            p, c = num(r.get(k)), num(r.get(cur))
            if p is not None:
                last[k] = p
            cand = max(c or 0.0, p if p is not None and p > before[k] else 0.0)
            s[f"true_{k}"] = max(s.get(f"true_{k}", 0.0), cand)
        s["n"] += 1
        t = num(r.get("t"))
        if t is not None:
            s["t0"] = t if s["t0"] is None else min(s["t0"], t)
            s["t1"] = t if s["t1"] is None else max(s["t1"], t)
        for k in ("ws", "commit", "gpu_shared", "gpu_dedicated", "pages_in_s"):
            v = num(r.get(k))
            if v is not None:
                s[k] = max(s.get(k, v), v)
        v = num(r.get("avail"))
        if v is not None:
            s["avail_min"] = min(s.get("avail_min", v), v)
    for s in out.values():
        s["seconds"] = (s["t1"] - s["t0"]) if s["t0"] is not None else 0.0
        s["peak_ws"], s["peak_commit"] = s.pop("true_peak_ws", 0.0), s.pop("true_peak_commit", 0.0)
    return out


def read_log(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _gb(v: Any) -> str:
    return "-" if v is None else f"{float(v) / 1e9:.2f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch", help="sample a process until it ends")
    w.add_argument("pid", type=int)
    w.add_argument("log")
    w.add_argument("--seconds", type=float, default=600.0)
    w.add_argument("--every", type=float, default=0.5)
    s = sub.add_parser("summary", help="the peaks of one or more logs, per label")
    s.add_argument("logs", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "watch":
        if process_memory(a.pid) is None:
            print(f"process {a.pid} cannot be read", file=sys.stderr)
            return 1
        with Sampler(a.pid, a.log, every=a.every, seconds=a.seconds) as smp:
            smp._thread.join(a.seconds + 5.0)
        print(f"{len(smp.rows)} samples in {a.log}")
        return 0
    print(f"{'label':40s} {'ws GB':>7s} {'commit GB':>9s} {'gpu sh GB':>9s} {'avail min':>9s} {'pages in/s':>10s} {'s':>7s}")
    for log in a.logs:
        for label, v in summarize(read_log(log)).items():
            print(f"{label[:40]:40s} {_gb(v.get('peak_ws')):>7s} {_gb(v.get('peak_commit')):>9s} "
                  f"{_gb(v.get('gpu_shared')):>9s} {_gb(v.get('avail_min')):>9s} {v.get('pages_in_s', 0):>10.0f} "
                  f"{v['seconds']:>7.1f}")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.exit(main())
