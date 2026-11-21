"""What a project takes on disk (FEATURE_PLAN.md R1; APP_API.md "Disk use of a project").

``GET /api/projects/{pid}/disk`` walks the project folder once and splits it, per scan, into

* ``recording``: ``recording/`` (``frames``: its ``frames/`` folder alone, ``n_frame_files`` PNGs);
* ``stages``: each folder under ``process/`` (``track``, ``straighten``, ``fuse``, ``spacetime``,
  ``refine``; any other folder there is listed under its own name too), ``stages_bytes`` their sum;
* ``undo``: ``process/cache`` (the cached steps) and ``process/selections`` (the faces of the
  delete / keep edits, needed to replay them);
* ``result``: ``result/``;
* ``other``: everything else in the scan folder (``process/state.json``, the thumbnail, the kept
  features, the comparison, ...). The parts add up to the scan's ``bytes``. While a Make mesh runs, the
  result it is building (``.remake-<job id>/``, UX_PLAN R2: the old result stays until the new one is
  done) is counted here too, until it takes the old one's place.

Sizes are the files' sizes in bytes (``st_size``), not the space the file system allocates.
Links and junctions are not followed. The trash (``<projects>/.trash``) is reported on its own and
is not part of any project.

**Cost.** One ``os.scandir`` walk (on Windows the sizes come with the directory listing, no
``stat`` per file): about 50 ms for 4,700 files (6.3 GB) with a warm file cache. The route is a
plain function, so FastAPI runs it in its thread pool and the event loop never waits; the project
page asks for it after it has painted. Nothing is cached, so the numbers are never stale.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from ohs.server.projects import ProjectStore

__all__ = ["folder_bytes", "folder_use", "project_disk_use", "scan_disk_use"]

UNDO_DIRS = ("cache", "selections")


def _is_link(entry: os.DirEntry) -> bool:
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)  # Python 3.12+
    return bool(is_junction and is_junction())


def _walk(path: Path) -> tuple[int, int]:
    """``(bytes, files)`` of every file under ``path``; ``(0, 0)`` when it is missing. Links are not followed."""
    total = files = 0
    stack = [str(path)]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:  # missing, removed while walking, or not readable
            continue
        with it:
            for e in it:
                try:
                    if _is_link(e):
                        continue
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        total += e.stat(follow_symlinks=False).st_size
                        files += 1
                except OSError:  # removed while walking
                    continue
    return total, files


def folder_bytes(path: str | Path) -> int:
    """Bytes of every file under ``path`` (0 when it does not exist)."""
    return _walk(Path(path))[0]


def folder_use(path: str | Path) -> tuple[int, int]:
    """``(bytes, files)`` under ``path``, counted as :func:`folder_bytes` counts them (for R2's plans)."""
    return _walk(Path(path))


def scan_disk_use(scan_dir: str | Path) -> dict[str, Any]:
    """The parts of one scan folder; see the module docstring."""
    sd = Path(scan_dir)
    total = folder_bytes(sd)
    recording = folder_bytes(sd / "recording")
    frames, n_frame_files = _walk(sd / "recording" / "frames")
    stages: dict[str, int] = {}
    undo = 0
    proc = sd / "process"
    try:
        with os.scandir(proc) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError:
        entries = []
    for e in entries:
        try:
            if _is_link(e) or not e.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        b = folder_bytes(e.path)
        if e.name in UNDO_DIRS:
            undo += b
        else:
            stages[e.name] = b
    stages_bytes = sum(stages.values())
    result = folder_bytes(sd / "result")
    return {"bytes": total, "recording": recording, "frames": frames, "n_frame_files": n_frame_files,
            "stages": stages, "stages_bytes": stages_bytes, "undo": undo, "result": result,
            "other": max(0, total - recording - stages_bytes - undo - result)}


def project_disk_use(store: ProjectStore, pid: str) -> dict[str, Any]:
    """Disk use of a project, its scans (in ``project.json`` order) and the trash; 404 for an unknown project."""
    t0 = time.perf_counter()
    entries = [dict(e) for e in store.get(pid)["scans"]]  # validates the id, 404 when unknown
    pdir = store.project_dir(pid)
    total = folder_bytes(pdir)
    scans = []
    for e in entries:
        s = scan_disk_use(store.scan_dir(pid, e["id"]))
        scans.append({"id": e["id"], "name": e.get("name", e["id"]), "n_frames": int(e.get("n_frames", 0) or 0),
                      "duration_s": float(e.get("duration_s", 0.0) or 0.0), **s})
    references = folder_bytes(pdir / "references")
    parts = {"recording": sum(s["recording"] for s in scans), "stages": sum(s["stages_bytes"] for s in scans),
             "undo": sum(s["undo"] for s in scans), "result": sum(s["result"] for s in scans)}
    trash_dir = store.root / ".trash"
    n_trash = n_trash_scans = 0
    try:  # the trash's projects and scans (ohs.server.trash): a scan's folder carries trashed-scan.json
        for e in os.scandir(trash_dir):
            if e.is_dir(follow_symlinks=False):
                if os.path.isfile(os.path.join(e.path, "trashed-scan.json")):
                    n_trash_scans += 1
                else:
                    n_trash += 1
    except OSError:
        pass
    return {
        "project_id": pid,
        "bytes": total,
        "scans": scans,
        "parts": parts,
        "references": references,
        "other": max(0, total - sum(s["bytes"] for s in scans) - references),
        "trash": {"bytes": folder_bytes(trash_dir), "projects": n_trash, "scans": n_trash_scans},
        "seconds": round(time.perf_counter() - t0, 4),
    }
