"""What a scan keeps on disk (FEATURE_PLAN.md R2; APP_API.md "What a scan keeps").

Three modes, the lightest last; the user moves a scan to a lighter one, per scan or for a whole
project, after the app has shown what is freed and what can no longer be done. Nothing here runs on
a timer or as a side effect of anything else, and new scans stay ``full`` (the owner's default, D4).

* ``full``: everything, as recorded and processed.
* ``engineering``: the recording and the result. Removed: every folder under ``process/`` but the
  cache (the stage folders ``track``, ``straighten``, ``fuse``, ``spacetime``, ``refine``, and any
  left-over ``*.tmp-*`` / ``*.old-*`` folder), ``process/selections/`` and ``process/cache/`` (the
  current step's file stays only when ``result/mesh.ply`` does not hold that step bit for bit). Make mesh (or Track) rebuilds them from the recording.
* ``standard``: the result only. Removed: what ``engineering`` removes, and ``recording/`` but its
  ``calibration.json`` and ``meta.json`` (the frames, ``frames.csv``, ``imu.csv``, ``events.csv``,
  ``ground_truth/``, ...). This cannot be undone: the request must say ``confirm: true``.

Kept in both: ``result/`` (``mesh.ply`` with each vertex's sigma, ``mesh.stl``, ``mesh.obj``,
``laser_points.ply``, ``trajectory.csv``, ``report.json``, ``marker_map.json``),
``process/state.json``, ``process/straighten.json``, the current mesh as the base new edits start from
(:meth:`ProcessManager.keep_current_only <ohs.server.processing.ProcessManager.keep_current_only>`; it
is read from ``result/mesh.ply``, which holds it bit for bit, so no cache file needs to stay; undo stops there),
``thumbnail.jpg``, the kept features (``features.json``, ``features/``) and every other file.
Links and junctions are neither followed nor counted.

**Order**, so that a failure half way leaves a scan that opens: the current step becomes the base
(``state.json`` is written whole), the mode goes into ``project.json``, then the recording goes
(``frames.csv`` first: from that moment the scan reads as "no recording"), then the undo files,
then the stage folders. A file held open (Windows) is retried for :data:`RETRY_S`, then left; the
request then answers 409 with what was freed, and freeing again removes the rest.

**Numbers.** A plan writes nothing: no processing state is made or taken over for a scan that has
none, and a missing cache file is not rebuilt. Its ``frees_bytes`` is the net drop of the scan folder
(the number R1 shows on the row, less what the scan takes afterwards): what goes (``remove``, gross per
part, counted as R1 counts, :mod:`ohs.server.disk`) less what the move writes (``writes``: ``state.json``
written again without the history). The move measures the folder before anything and after; the two
are equal unless something else wrote to the scan meanwhile (nothing can: the move holds
``Studio._lock`` and the scan lock). A net of zero or less is refused ("nothing to free").

**Busy.** A scan that is recording, processing, or named by any queued or running job (its own
processing, an align, a merge) is not moved, and its cache is not touched by the plan.

A scan moved to a lighter mode and processed again has stage folders and undo history again: the
mode then says what was removed and when (``freed``); what is on disk now is R1's report and the
scan's ``has_recording``. Freeing again removes what came back.

**Only the frames Standard uses** (FEATURE_PLAN R3, ``thin: true``, with ``engineering`` only): what
``engineering`` removes, and the camera frames Standard quality skips (every 2nd of a 60 frames a second
recording at the default 30). The frames that stay keep their own index, time stamp and file name; the
motion sensor (``imu.csv``), the events, the calibration and the ground truth stay as they are, so
Standard quality picks the same frames from the thinner recording (its rate is now about 30, so its step
is 1) and gives the same mesh, every value bit for bit (VALIDATION.md, "Keeping only the frames
Standard uses"). Best quality still runs, from the frames that stay: it then gives the Standard mesh.
``frames.csv`` is written again with the rows of the frames that stay (the same bytes per row),
``meta.json`` gets ``thinned`` (``fps``, ``fps_before``, ``step``, ``frames``, ``frames_before``) and its
``n_frames``, the scan entry ``thinned`` (the same, and ``when``) and ``n_frames``. It cannot be undone:
the request must say ``confirm: true``. Refused, in plain words, when Standard quality would pick other
frames from the thinner recording: frames picked by motion (``process.frame_motion_deg`` > 0), a
``process.frame_step`` other than 1, no rate limit (``process.standard_frame_rate`` = 0), or a recording
it does not slow down (30 frames a second or fewer: nothing to remove).

*Order:* the processing state as for ``engineering``, the mode and ``thinned`` into ``project.json``,
the shorter ``frames.csv`` (temporary file + rename: from then on the recording holds the frames that stay
and nothing else reads the others), ``meta.json`` the same way, then the frame files, the undo files and
the stage folders. A frame file left behind (held open) is not listed in ``frames.csv``; freeing again
with ``thin: true`` removes it.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import stat
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ohs.io.recording import effective_frame_step
from ohs.server.disk import UNDO_DIRS, _is_link, folder_bytes, folder_use
from ohs.server.processing import json_file_size
from ohs.server.projects import KEEP_MODES
from ohs.server.protocol import ApiError, jsonable, now_iso, sentence
from ohs.server.util import safe_call

__all__ = ["KeepManager", "MODES", "RECORDING_KEPT"]

log = logging.getLogger(__name__)

MODES = KEEP_MODES
_RANK = {m: i for i, m in enumerate(MODES)}
#: what Standard keeps of the recording: the calibration the scan was made with, and how it was recorded
RECORDING_KEPT = ("calibration.json", "meta.json")
RETRY_S = 1.0  # a file another program holds open is tried again for this long, then left


def _unlink(path: Path) -> None:
    os.unlink(path)


def _fmt_bytes(n: int) -> str:
    """As the app shows sizes (``ui/src/lib/format.ts`` ``fmtBytes``: 1 MB = 1024 * 1024 bytes)."""
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024:.0f} KB" if n >= 1024 else f"{n} B"


def _entries(d: Path) -> list[os.DirEntry]:
    try:
        with os.scandir(d) as it:
            return sorted(it, key=lambda e: e.name)
    except OSError:
        return []


def _is_dir(e: os.DirEntry) -> bool:
    try:
        return e.is_dir(follow_symlinks=False)
    except OSError:
        return False


def _remove_file(path: Path, errors: list[str]) -> None:
    deadline = time.monotonic() + RETRY_S
    chmod = True
    while True:
        try:
            _unlink(path)
            return
        except FileNotFoundError:
            return
        except PermissionError as e:
            if chmod:  # a read-only file (Windows) can be removed once it is writable
                chmod = False
                try:
                    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                    continue
                except OSError:
                    pass
            if time.monotonic() >= deadline:
                errors.append(f"{path.name}: {e.strerror or e}")
                return
            time.sleep(0.05)
        except OSError as e:
            errors.append(f"{path.name}: {e.strerror or e}")
            return


def _remove(path: Path, is_dir: bool, errors: list[str]) -> None:
    """Remove a file, or a folder with everything in it. A link inside is removed itself, never followed."""
    if not is_dir:
        _remove_file(path, errors)
        return
    n = len(errors)
    for e in _entries(path):
        p = Path(e.path)
        if _is_link(e):
            try:
                os.unlink(p)
            except OSError:
                try:
                    os.rmdir(p)  # a junction or a link to a folder: the link goes, not what it points at
                except OSError as ex:
                    errors.append(f"{p.name}: {ex.strerror or ex}")
        elif _is_dir(e):
            _remove(p, True, errors)
        else:
            _remove_file(p, errors)
    try:
        os.rmdir(path)
    except FileNotFoundError:
        pass
    except OSError as ex:
        if len(errors) == n:  # not just a folder something inside of which could not be removed
            errors.append(f"{path.name}: {ex.strerror or ex}")


def _mode(mode: Any) -> str:
    if mode not in MODES:
        raise ApiError(422, "The app does not know this choice of what a scan keeps.",
                       f"Pick {_ENGINEERING}, {_THIN} or {_STANDARD}.")
    return str(mode)


def _thin_arg(mode: str, thin: Any) -> bool:
    thin = bool(thin)
    if thin and mode != "engineering":
        raise ApiError(422, "Keeping half the frames goes with keeping the recording and the result.",
                       f"Pick {_THIN}.")
    return thin


def _ordinal(n: int) -> str:
    return {2: "2nd", 3: "3rd"}.get(n, f"{n}th")


def _write_bytes(path: Path, data: bytes) -> None:
    """Replace a file with ``data`` (a temporary file next to it, then a rename: never half a file)."""
    tmp = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


#: the Free space dialog's three choices, by their titles (the only names the app shows for them, UX_PLAN U37)
_ENGINEERING = "\u201cKeep the recording and the result\u201d"
_THIN = "\u201cKeep half the frames and the result\u201d"
_STANDARD = "\u201cKeep the result only\u201d"

#: what keeping only the frames Standard uses can not promise, and what to do instead
_SAME_MESH = "so the thinner recording would not give the same mesh"
_THIN_HINT = f"Pick {_ENGINEERING} instead."


def _thinning(sd: Path, entry: dict, cfg: Any) -> dict:
    """R3, read-only: what keeping only the frames Standard uses would do to the scan's recording.

    ``{"refusal": (detail, hint) | None, "files": [frame files to remove], "frames_csv": bytes | None,
    "meta": bytes | None, "writes": [...], "info": {...} | None}``. Reads only."""
    out: dict[str, Any] = {"refusal": None, "files": [], "frames_csv": None, "meta": None, "writes": [], "info": None}
    rec = sd / "recording"
    fcsv = rec / "frames.csv"
    if not fcsv.is_file():
        return out  # no recording: the engineering refusal says so
    p = cfg.process
    if float(p.frame_motion_deg) > 0:
        out["refusal"] = (f"Standard quality is set here to pick frames by how far the scanner turned, {_SAME_MESH}",
                          _THIN_HINT)
        return out
    if int(p.frame_step) != 1:
        out["refusal"] = (f"Standard quality is set here to use every {_ordinal(int(p.frame_step))} frame, {_SAME_MESH}",
                          _THIN_HINT)
        return out
    rate = float(p.standard_frame_rate)
    if not rate > 0:
        out["refusal"] = (f"Standard quality is set here to use every frame: it skips none, {_SAME_MESH} if any were "
                          "removed", _THIN_HINT)
        return out
    raw = fcsv.read_bytes()
    lines = raw.splitlines(keepends=True)
    if lines and not lines[-1].endswith((b"\n", b"\r")):
        lines.pop()  # cut by a crash: the reader does not read it either
    header, rows = lines[:1], lines[1:]
    parsed = list(csv.DictReader(io.StringIO(b"".join(lines).decode("utf-8"), newline="")))
    if len(parsed) != len(rows) or not all(r.get("file") and r.get("t_host") for r in parsed):
        out["refusal"] = ("This scan's list of frames has lines the app does not read as frames, so it cannot be "
                          "thinned safely", _THIN_HINT)
        return out
    times = [float(r["t_host"]) for r in parsed]
    files = [r["file"] for r in parsed]
    n = len(files)
    step, fps = effective_frame_step(times, 1, rate)
    thinned = entry.get("thinned") if isinstance(entry.get("thinned"), dict) else None
    listed = set(files)
    # a frame file a failure half way left behind: no longer listed, so nothing reads it
    orphans = [e.name for e in _entries(rec / "frames") if thinned and not _is_link(e) and not _is_dir(e)
               and e.name not in listed]
    meta_path = rec / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    except (OSError, ValueError):
        meta = None
    if step > 1:
        kept_times = times[::step]
        step_after, fps_after = effective_frame_step(kept_times, 1, rate)
        if step_after != 1:
            out["refusal"] = ("Standard quality would not pick the same frames from the thinner recording, "
                              f"{_SAME_MESH}", _THIN_HINT)
            return out
        info = {"fps": round(float(fps_after or 0.0), 3), "fps_before": round(float(fps or 0.0), 3), "step": int(step),
                "frames": len(kept_times), "frames_before": n}
        out["files"] = [f for k, f in enumerate(files) if k % step] + orphans
        out["frames_csv"] = b"".join(header + rows[::step])
    elif thinned is None:
        out["refusal"] = (f"This recording has {fps or 0:.0f} frames a second: Standard quality uses every one of them, so "
                          "there are no frames to remove", f"Pick {_ENGINEERING} or {_STANDARD} instead.")
        return out
    else:  # thinned before: what a failure half way left
        info = {k: thinned[k] for k in ("fps", "fps_before", "step", "frames", "frames_before") if k in thinned}
        out["files"] = orphans
    if meta is not None and (step > 1 or "thinned" not in meta):
        meta = {**meta, "n_frames": int(info.get("frames", n)), "thinned": info}
        out["meta"] = json.dumps(meta, indent=2, default=str).encode("utf-8")
    out["info"] = info
    if out["frames_csv"] is not None:
        out["writes"].append({"what": "frames_list", "bytes": len(out["frames_csv"]) - len(raw)})
    if out["meta"] is not None:
        out["writes"].append({"what": "meta", "bytes": len(out["meta"]) - _file_use(meta_path)[0]})
    return out


class KeepManager:
    """``GET`` / ``POST /api/projects/{pid}/scans/{sid}/keep`` and ``/api/projects/{pid}/keep``."""

    def __init__(self, studio: Any) -> None:
        self.st = studio

    # ------------------------------------------------------------------ checks
    def _busy(self, pid: str, sid: str, entry: dict) -> tuple[str, str] | None:
        s = self.st.session
        if s is not None and s.active and s.project_id == pid and s.scan_id == sid:
            return "This scan is busy: it is still recording", "Stop the scan first, then free its space."
        for job in self.st.jobs.active():
            m = job.meta or {}
            if m.get("project_id") != pid:
                continue
            if sid in (m.get("scan_id"), m.get("target"), m.get("source")) or sid in (m.get("scans") or []):
                state = "waiting to run" if job.state == "queued" else "running"
                return (f"This scan is busy: a task that uses it is {state}",
                        "Wait for it to finish (see Jobs), or cancel it, then free its space.")
        if entry.get("status") in ("recording", "processing"):
            doing = "recorded" if entry["status"] == "recording" else "processed"
            return f"This scan is busy: it is being {doing}", "Wait until it has finished, then free its space."
        return None

    @staticmethod
    def _items(sd: Path, mode: str, kept: Path | None) -> list[tuple[Path, str, bool]]:
        """What goes, in the order it goes: ``(path, part, is a folder)``."""
        out: list[tuple[Path, str, bool]] = []
        if mode == "full":
            return out
        if mode == "standard":
            rec = [e for e in _entries(sd / "recording") if e.name not in RECORDING_KEPT and not _is_link(e)]
            rec.sort(key=lambda e: (e.name != "frames.csv", e.name))  # frames.csv first: "no recording" at once
            out += [(Path(e.path), "recording", _is_dir(e)) for e in rec]
        proc = sd / "process"
        for e in _entries(proc / "cache"):
            if not _is_link(e) and not (kept is not None and e.name == kept.name):
                out.append((Path(e.path), "undo", _is_dir(e)))
        dirs = [e for e in _entries(proc) if not _is_link(e) and _is_dir(e)]
        out += [(Path(e.path), "undo", True) for e in dirs if e.name in UNDO_DIRS and e.name != "cache"]
        out += [(Path(e.path), "stages", True) for e in dirs if e.name not in UNDO_DIRS]
        return out

    def _plan(self, pid: str, sid: str, mode: str, thin: bool = False) -> tuple[dict, list[tuple[Path, str, bool]], bool, dict]:
        """``(plan, what goes, the base stays in result/, the thinning)``. Reads only: no file is written, no
        processing state is made or taken over, and a missing cache file is not rebuilt."""
        entry = self.st.projects.scan_entry(pid, sid)  # 404 for an unknown project or scan
        sd = self.st.projects.scan_dir(pid, sid)
        cur = entry.get("keep") if entry.get("keep") in MODES else "full"
        has_rec = (sd / "recording" / "frames.csv").is_file()
        has_mesh = (sd / "result" / "mesh.ply").is_file()
        refusal = self._busy(pid, sid, entry)
        items: list[tuple[Path, str, bool]] = []
        writes: list[dict] = []
        th: dict = {"refusal": None, "files": [], "frames_csv": None, "meta": None, "writes": [], "info": None}
        had_edits, in_result, kept_bytes = False, False, 0
        if refusal is None:  # a busy scan's cache is left alone, even by the plan
            sp = self.st.processing.peek(pid, sid)  # None: no processing state (nothing to keep of it)
            kept: Path | None = None
            if sp is not None:
                with sp.lock:
                    ks = self.st.processing.kept_step(sp)
                    if ks is None and sp.state["base"] is not None:
                        log.warning("scan %s/%s: neither result/mesh.ply nor its cache file is there", pid, sid)
                        refusal = ("This scan's current mesh cannot be found on disk",
                                   "Press Make mesh again, or run its last step again.")
                    if ks is not None:
                        kept, in_result = ks
                        kept_bytes = _file_use(kept)[0] if kept is not None else 0
                    new_state = self.st.processing.collapsed_state(sp, in_result) if mode != "full" and ks else None
                    if new_state is not None:  # state.json is written again, without the history
                        old = _file_use(sp.state_path)[0]
                        writes.append({"what": "state", "bytes": json_file_size(new_state) - old})
                    had_edits = int(sp.state["cursor"]) > 0 or len(sp.state["edits"]) > 0
            items = self._items(sd, mode, kept)
            if thin and cur != "standard":
                th = _thinning(sd, entry, self.st.cfg)
                # the frames go first, after the shorter frames list: nothing reads them from then on
                items = [(sd / "recording" / "frames" / f, "frames", False) for f in th["files"]] + items
                writes += th["writes"]
        parts: dict[str, list[int]] = {}
        for path, part, is_dir in items:
            b, n = folder_use(path) if is_dir else _file_use(path)
            parts.setdefault(part, [0, 0])
            parts[part][0] += b
            parts[part][1] += n
        remove = [{"part": p, "bytes": parts[p][0], "files": parts[p][1]} for p in ("recording", "frames", "undo", "stages")
                  if p in parts and parts[p][0] > 0]
        writes = [w for w in writes if w["bytes"] != 0]
        # the net drop of the scan folder: what goes, less what the move writes (R1's number now minus after)
        frees = sum(r["bytes"] for r in remove) - sum(w["bytes"] for w in writes)
        if refusal is None:
            refusal = self._refusal(entry, cur, mode, has_rec, has_mesh, frees, th["refusal"])
        frames = "frames" in parts and parts["frames"][0] > 0
        loses: list[str] = []
        if had_edits:
            loses.append("undo")
        stages = "stages" in parts and parts["stages"][0] > 0
        if mode == "engineering" and stages:
            loses.append("stages")
        if frames and th["frames_csv"] is not None:
            loses.append("best")  # Best quality then works from the frames that stay
        if mode == "standard":
            if has_rec:
                loses += ["remake", "merge", "frames"]
            if has_rec or stages:
                loses.append("refine")
        res = sd / "result"
        keeps = [k for k, ok in (("recording", has_rec and mode != "standard"), ("result", has_mesh),
                                 ("laser_points", (res / "laser_points.ply").is_file()),
                                 ("trajectory", (res / "trajectory.csv").is_file()),
                                 ("report", (res / "report.json").is_file()),
                                 ("calibration", (sd / "recording" / "calibration.json").is_file()),
                                 ("features", (sd / "features.json").is_file())) if ok]
        ok = refusal is None
        plan = {"scan_id": sid, "name": entry.get("name", sid), "keep": cur, "to": mode, "ok": ok,
                "refusal": None if ok else {"detail": sentence(refusal[0]), "hint": sentence(refusal[1])},
                "bytes": folder_bytes(sd), "frees_bytes": frees if ok else 0,
                "remove": remove if ok else [], "writes": writes if ok else [],
                "kept_step_bytes": kept_bytes if ok else 0, "loses": loses, "keeps": keeps,
                "confirm": (mode == "standard" and "recording" in parts) or frames,
                "thin": thin, "thinned": th["info"] if ok and thin else None}
        return jsonable(plan), items, in_result, th

    @staticmethod
    def _refusal(entry: dict, cur: str, mode: str, has_rec: bool, has_mesh: bool, frees: int,
                 thin_refusal: tuple[str, str] | None = None) -> tuple[str, str] | None:
        if mode == "full":
            if cur == "full":
                return ("This scan keeps everything already",
                        f"Choose what to remove: {_ENGINEERING} or {_STANDARD}.")
            hint = ("The stage files come back when you make the mesh again." if cur == "engineering" else
                    "The recording is gone for good: scan the part again to have one.")
            return "What was removed to free space cannot be brought back from here", hint
        if _RANK[mode] < _RANK[cur]:
            return ("This scan cannot keep its recording again: it was removed to free space",
                    "It keeps its result only, and can still be edited, measured and exported.")
        if mode == "engineering" and not has_rec:
            why = "of its own (it was merged from other scans)" if entry.get("merged_from") else "on disk"
            return (f"This scan has no recording {why}, so it cannot keep one",
                    f"Pick {_STANDARD} to free its processing files.")
        if mode == "standard" and not has_mesh:
            return ("This scan has no mesh yet: keeping its result only would leave nothing of it",
                    f"Make its mesh first, or pick {_ENGINEERING}.")
        if thin_refusal is not None:
            return thin_refusal
        if frees <= 0:
            return ("There is nothing to free: this scan would not take less space afterwards",
                    "It has none of what this choice removes on disk.")
        return None

    # ------------------------------------------------------------------ one scan
    def plan(self, pid: str, sid: str, mode: Any, thin: Any = False) -> dict:
        """``GET .../scans/{sid}/keep?mode=&thin=``: what moving the scan would free and what it would cost.
        Changes nothing on disk."""
        mode = _mode(mode)
        return self._plan(pid, sid, mode, _thin_arg(mode, thin))[0]

    def apply(self, pid: str, sid: str, mode: Any, confirm: bool = False, thin: Any = False) -> dict:
        """``POST .../scans/{sid}/keep``: move the scan; see the module docstring."""
        mode = _mode(mode)
        thin = _thin_arg(mode, thin)
        errors: list[str] = []
        sd = self.st.projects.scan_dir(pid, sid)
        with self.st._lock:  # no job starts on the scan meanwhile
            self.st.projects.scan_entry(pid, sid)  # 404
            loaded = self.st.processing.peek(pid, sid) is not None
            # its processing state (loaded, never taken over: a scan without one has nothing to keep of it)
            sp = self.st.processing.scan(pid, sid) if loaded else None
            lock = sp.lock if sp is not None else threading.RLock()
            with lock:  # fits, checks and undo wait for the move
                before = folder_bytes(sd)  # before anything: the number the row shows
                plan, items, in_result, th = self._plan(pid, sid, mode, thin)
                if plan["refusal"] is not None:
                    raise ApiError(409, plan["refusal"]["detail"], plan["refusal"]["hint"])
                if plan["confirm"] and not confirm:
                    what = "half the frames" if mode == "engineering" else "this scan's recording"
                    raise ApiError(409, f"Removing {what} cannot be undone.", "Confirm it once more to go ahead.")
                if sp is not None:
                    self.st.processing.keep_current_only(sp, in_result)
                entry = self.st.projects.scan_entry(pid, sid)
                fields: dict[str, Any] = {"keep": mode}
                if th["frames_csv"] is not None:  # R3: say what the recording holds before it holds less
                    fields.update(thinned={"when": now_iso(), **th["info"]}, n_frames=int(th["info"]["frames"]))
                self.st.projects.update_scan(pid, sid, **fields)
                rec = sd / "recording"
                listed = th["frames_csv"] is None
                try:
                    if th["frames_csv"] is not None:
                        _write_bytes(rec / "frames.csv", th["frames_csv"])
                        listed = True
                    if th["meta"] is not None:
                        _write_bytes(rec / "meta.json", th["meta"])
                except OSError as e:
                    errors.append(f"{e.filename and Path(e.filename).name or 'frames.csv'}: {e.strerror or e}")
                    if not listed:  # the recording holds every frame still: the scan says so, and the frames stay
                        self.st.projects.update_scan(pid, sid, thinned=entry.get("thinned"), n_frames=entry.get("n_frames"))
                        items = [it for it in items if it[1] != "frames"]
                for path, _, is_dir in items:
                    _remove(path, is_dir, errors)
                freed = max(0, before - folder_bytes(sd))
                record = {"when": now_iso(), "keep": mode, "bytes": freed, **({"thin": True} if thin else {})}
                scan = self.st.projects.update_scan(pid, sid, keep=mode, freed=[*(entry.get("freed") or []), record])
        if sp is not None:
            safe_call("publishing the process state", lambda: self.st.processing.publish(sp))
        log.info("scan %s/%s now keeps %s: freed %d bytes (%d could not be removed)", pid, sid, mode, freed, len(errors))
        if errors:
            n = len(errors)
            log.warning("scan %s/%s: %d files could not be removed, the first: %s", pid, sid, n, errors[0])
            raise ApiError(409, f"{n} file{'s' if n != 1 else ''} could not be removed; {_fmt_bytes(freed)} were "
                                "freed and the scan still opens.",
                           "Close any program that may have a file of this scan open, then free its space again.")
        return {"scan": scan, "freed_bytes": freed, "planned_bytes": plan["frees_bytes"]}

    # ------------------------------------------------------------------ a whole project
    def project_plan(self, pid: str, mode: Any, thin: Any = False) -> dict:
        """``GET /api/projects/{pid}/keep?mode=&thin=``: the plan of every scan; the totals count the scans that
        would move."""
        mode = _mode(mode)
        thin = _thin_arg(mode, thin)
        scans = [self._plan(pid, e["id"], mode, thin)[0] for e in self.st.projects.get(pid)["scans"]]
        ok = [p for p in scans if p["ok"]]
        return {"project_id": pid, "to": mode, "thin": thin, "scans": scans, "frees_bytes": sum(p["frees_bytes"] for p in ok),
                "n_scans": sum(1 for p in ok if p["frees_bytes"] > 0), "confirm": any(p["confirm"] for p in ok)}

    def project_apply(self, pid: str, mode: Any, confirm: bool = False, scans: list[str] | None = None,
                      thin: Any = False) -> dict:
        """``POST /api/projects/{pid}/keep``: move every scan that can move (or those of ``scans``); a scan that
        cannot is left as it is, with the reason."""
        mode = _mode(mode)
        thin = _thin_arg(mode, thin)
        plan = self.project_plan(pid, mode, thin)
        if plan["confirm"] and not confirm:
            what = "half the frames" if thin else "these scans' recordings"
            raise ApiError(409, f"Removing {what} cannot be undone.", "Confirm it once more to go ahead.")
        want = None if scans is None else set(scans)
        results, freed = [], 0
        for p in plan["scans"]:
            if want is not None and p["scan_id"] not in want:
                continue
            row: dict[str, Any] = {"scan_id": p["scan_id"], "name": p["name"]}
            if not p["ok"] or p["frees_bytes"] == 0 and p["keep"] == mode:
                results.append({**row, "ok": False, "freed_bytes": 0,
                                "error": p["refusal"] or {"detail": "There is nothing to free in this scan.",
                                                          "hint": "Nothing needs doing."}})
                continue
            try:
                out = self.apply(pid, p["scan_id"], mode, confirm=True, thin=thin)
                results.append({**row, "ok": True, "freed_bytes": out["freed_bytes"]})
                freed += out["freed_bytes"]
            except ApiError as e:
                results.append({**row, "ok": False, "freed_bytes": 0, "error": {"detail": e.detail, "hint": e.hint}})
        return {"project_id": pid, "to": mode, "thin": thin, "results": results, "freed_bytes": freed}


def _file_use(path: Path) -> tuple[int, int]:
    try:
        return os.stat(path, follow_symlinks=False).st_size, 1
    except OSError:
        return 0, 0
