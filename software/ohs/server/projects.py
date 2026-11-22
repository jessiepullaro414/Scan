"""Projects on disk: one folder per project, readable without the app (APP_API.md, "Project folders").

```
<projects_dir>/<project_id>/project.json
                            scans/<scan_id>/recording/     raw recording (ARCHITECTURE.md section 6)
                            scans/<scan_id>/result/        process_recording output (mesh.ply, mesh.stl, ...)
                            scans/<scan_id>/thumbnail.jpg  left camera view at the end of the scan
                            references/                    reference models of the part (ohs.server.reference)
<projects_dir>/.trash/<project_id>-<time>/                 deleted projects (never hard-deleted)
<projects_dir>/.trash/<project_id>--<scan_id>-<time>/      deleted scans, with trashed-scan.json (ohs.server.trash)
```

``project.json`` holds ``{id, name, created, modified, scans: [{id, name, created, mode, status}]}``
as the contract says, plus ``format_version``, ``ohs_version`` (the engine that wrote it) and per
scan ``n_frames``, ``duration_s``, ``quality`` (of the last processing), ``error`` (of a failed
processing) and ``report`` (a compact summary of ``result/report.json``, :func:`report_summary`; since H4 with
the scan's own calibration status in short, ``health_status: {state, part, reason}``); since R2 ``keep``
(``engineering`` or ``standard`` once the user freed the scan's space; absent means ``full``) and ``freed``
(``[{when, keep, bytes}]``, each time it was freed; ``thin: true`` when the recording was thinned); since R3
``thinned`` (``{when, fps, fps_before, step, frames, frames_before}``: the recording holds only the frames
Standard quality uses, ``fps`` of them a second). It is written
atomically (temporary file + rename), so a crash never leaves half a file.

Ids are filesystem-safe: projects ``<slug of the name>-<4 hex>``, scans ``scan-<local time>``.
Every id from a URL is validated before it touches the filesystem, and result files are served
from a fixed list of names only.

Scans that were ``recording`` or ``processing`` when the app stopped (a crash) are set back to
``recorded`` (or ``processed`` if a result exists) by :meth:`ProjectStore.recover_stale`; the raw
recording is on disk either way.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from ohs import __version__
from ohs.server.protocol import ApiError, jsonable, now_iso, quality_or_none, sentence

__all__ = ["KEEP_MODES", "ProjectStore", "RESULT_FILES", "SCAN_STATUSES", "free_name", "path_check_short", "report_summary",
           "user_warnings", "user_words"]

log = logging.getLogger(__name__)

FORMAT_VERSION = 1
SCAN_STATUSES = ("recording", "recorded", "processing", "processed", "failed")
#: what a scan keeps on disk (FEATURE_PLAN R2, :mod:`ohs.server.keep`), lightest last; absent = "full"
KEEP_MODES = ("full", "engineering", "standard")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
#: files served by GET .../files/{name}: name -> (folder inside the scan, media type)
RESULT_FILES: dict[str, tuple[str, str]] = {
    "mesh.ply": ("result", "application/octet-stream"),
    "mesh.stl": ("result", "model/stl"),
    "mesh.obj": ("result", "text/plain"),
    "laser_points.ply": ("result", "application/octet-stream"),
    "report.json": ("result", "application/json"),
    "trajectory.csv": ("result", "text/csv"),
    "thumbnail.jpg": ("", "image/jpeg"),
}
NAME_MAX = 120


def _slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40].strip("-")
    return s or "project"


def _clean_name(name: Any, what: str) -> str:
    n = " ".join(str(name or "").split())
    if not n:
        raise ApiError(400, f"The {what} name is empty.", f"Type a name for the {what}.")
    if len(n) > NAME_MAX:
        raise ApiError(400, f"The {what} name is too long ({len(n)} characters).", f"Use at most {NAME_MAX} characters.")
    return n


def _next_scan_name(scans: list[dict]) -> str:
    """"Scan <n>" for a new scan: the count plus one as before, or the next number no scan of the project has (a scan
    moved to the trash or renamed leaves a gap; U50: no two scans "Scan 3")."""
    used = set()
    for e in scans:
        m = re.fullmatch(r"Scan (\d+)", str(e.get("name") or ""))
        if m:
            used.add(int(m.group(1)))
    k = len(scans) + 1
    while k in used:
        k += 1
    return f"Scan {k}"


def _same_name(a: Any, b: Any) -> bool:
    return " ".join(str(a or "").split()).casefold() == " ".join(str(b or "").split()).casefold()


def refuse_taken_name(name: str, taken: list[str]) -> None:
    """UX_PLAN R21: two scans of one project never share a name. A name one of ``taken`` has is refused in plain
    words (409) with a ``suggestion``, the next free one; used by a rename and by a merge whose name was typed."""
    if any(_same_name(name, o) for o in taken):
        suggestion = free_name(name, taken)
        raise ApiError(409, f"Another scan of this project is already called “{name}”.",
                       f"Pick another name, for example “{suggestion}”.", extra={"suggestion": suggestion})


def free_name(wanted: str, taken: list[str]) -> str:
    """``wanted``, or the first of "wanted (2)", "wanted (3)", ... that none of ``taken`` is (UX_PLAN R21: names compared
    without case and extra spaces)."""
    if not any(_same_name(wanted, t) for t in taken):
        return wanted
    k = 2
    while any(_same_name(f"{wanted} ({k})", t) for t in taken):
        k += 1
    return f"{wanted} ({k})"


def check_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.match(value) or ".." in value:
        raise ApiError(404, f"There is no such {what}: it may have been deleted.", "Go back to the projects list.")
    return value


def report_summary(report: dict) -> dict:
    """The parts of a ``process_recording`` report that the UI shows (no per-frame rows)."""
    out: dict[str, Any] = {}
    for key in ("mode", "scale", "image_size", "frames", "failures", "mesh", "lasers", "wall_time_s", "source", "scene",
                "cuts", "quality"):
        if key in report:
            out[key] = report[key]
    imu = report.get("imu")
    if isinstance(imu, dict):
        out["imu"] = imu
    gt = report.get("ground_truth")
    if isinstance(gt, dict):
        out["ground_truth"] = {k: v for k, v in gt.items() if k != "per_frame"}
    hs = report.get("health_status")  # the scan's own calibration status (H3), in short for its card (H4)
    if isinstance(hs, dict) and hs.get("state") in ("valid", "check", "recalibrate"):
        out["health_status"] = {"state": hs["state"], "part": hs.get("part"), "reason": hs.get("reason")}
    # the camera path's check for a slide (ENGINE_PLAN X0a): absent for a scan processed before it
    if "warnings" in report or "path_check" in report:
        out["warnings"] = user_warnings(report)
    pc = path_check_short(report)
    if pc is not None:
        out["path_check"] = pc
    return jsonable(out)


#: the engine's words for a graphics card, and the app's (UX_PLAN U5: "graphics card" only). The engine says "graphics
#: card" itself since UX_PLAN U51, but the report.json of a scan processed before keeps "GPU": these turn it round
_GPU_WORDS = ((re.compile(r"\bwithout a GPU\b"), "without a graphics card"), (re.compile(r"\ba GPU\b"), "a graphics card"),
              (re.compile(r"\bthe GPU\b"), "the graphics card"), (re.compile(r"\bGPU\b"), "graphics card"))


def user_words(text: str) -> str:
    """An engine sentence in the app's words: "graphics card" for "GPU" (UX_PLAN U5)."""
    for rx, word in _GPU_WORDS:
        text = rx.sub(word, text)
    return sentence(text)


def user_warnings(report: Any) -> list[str]:
    """A processing report's ``warnings`` (sentences for the user, ENGINE_PLAN X0a) in the app's words; [] when
    there are none or the report has none."""
    ws = report.get("warnings") if isinstance(report, dict) else None
    if not isinstance(ws, list):
        return []
    return [user_words(w) for w in ws if isinstance(w, str) and w.strip()]


def path_check_short(report: Any) -> dict | None:
    """The camera path's check in short, for the scan's card: ``{state, jump_mm, frame}``; None when the report has
    no check (processed before it, or at given poses without the track's)."""
    pc = report.get("path_check") if isinstance(report, dict) else None
    if not isinstance(pc, dict):
        return None
    state = pc.get("state") if pc.get("state") in ("ok", "warn") else None
    return {"state": state, "jump_mm": pc.get("jump_mm"), "frame": pc.get("frame")}


class ProjectStore:
    """Project folders under ``root``; see the module docstring. Thread-safe."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ paths
    def project_dir(self, pid: str) -> Path:
        return self.root / check_id(pid, "project")

    def scan_dir(self, pid: str, sid: str) -> Path:
        return self.project_dir(pid) / "scans" / check_id(sid, "scan")

    def recording_dir(self, pid: str, sid: str) -> Path:
        return self.scan_dir(pid, sid) / "recording"

    def result_dir(self, pid: str, sid: str) -> Path:
        return self.scan_dir(pid, sid) / "result"

    def references_dir(self, pid: str) -> Path:
        """The folder of the project's reference models (FEATURE_PLAN I2, :mod:`ohs.server.reference`); 404 for
        an unknown project. Not made here: the first reference added makes it."""
        with self._lock:
            self._load(pid)
        return self.project_dir(pid) / "references"

    def file_path(self, pid: str, sid: str, name: str) -> tuple[Path, str]:
        """``(path, media type)`` of a downloadable scan file; 404 if unknown or missing."""
        self._scan_entry(self._load(pid), sid)
        if name not in RESULT_FILES:
            raise ApiError(404, "A scan has no file of this name.", "Download one of the files the page lists.")
        sub, media = RESULT_FILES[name]
        base = self.scan_dir(pid, sid)
        path = (base / sub / name) if sub else (base / name)
        if not path.is_file():
            hint = ("Press Make mesh first." if sub == "result" else "The picture is made when a scan stops.")
            raise ApiError(404, f"This scan has no {name} yet.", hint)
        return path, media

    # ------------------------------------------------------------------ io
    def _json_path(self, pid: str) -> Path:
        return self.project_dir(pid) / "project.json"

    def _load(self, pid: str) -> dict:
        p = self._json_path(pid)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ApiError(404, "There is no such project: it may have been deleted.",
                           "Pick a project from the projects list, or make a new one.") from None
        except (OSError, ValueError) as e:
            log.warning("project %s cannot be read (%s): %s", pid, p, e)  # the path goes to the log, not to the page
            raise ApiError(500, "The project's file cannot be read.",
                           "Restore the project's file (project.json in the project's folder) from a backup; the scans "
                           "inside the project's folder are untouched.") from None
        data.setdefault("scans", [])
        data["id"] = pid
        return data

    def _save(self, pid: str, data: dict) -> None:
        p = self._json_path(pid)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"project.json.tmp-{os.getpid()}-{threading.get_ident()}")
        tmp.write_text(json.dumps(jsonable(data), indent=2), encoding="utf-8")
        for attempt in range(50):  # Windows: another reader may hold the file for a moment
            try:
                os.replace(tmp, p)
                return
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.02)

    def _touch(self, data: dict) -> None:
        data["modified"] = now_iso()
        data["ohs_version"] = __version__

    # ------------------------------------------------------------------ API views
    def _scan_view(self, pid: str, entry: dict) -> dict:
        sid = entry["id"]
        base = f"/api/projects/{pid}/scans/{sid}/files"
        res_dir = self.root / pid / "scans" / sid / "result"
        result = None
        if (res_dir / "mesh.ply").is_file():
            report = entry.get("report")
            if not isinstance(report, dict):
                report = {}
                try:
                    report = report_summary(json.loads((res_dir / "report.json").read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    pass
            if report.get("quality"):  # stored with the old name "full": reads as "standard"
                report = {**report, "quality": quality_or_none(report["quality"]) or report["quality"]}
            result = {
                "mesh_url": f"{base}/mesh.ply",
                "stl_url": f"{base}/mesh.stl",
                "laser_points_url": f"{base}/laser_points.ply" if (res_dir / "laser_points.ply").is_file() else None,
                "report": report,
            }
        out = {
            "id": sid, "name": entry.get("name", sid), "created": entry.get("created"),
            "mode": entry.get("mode", "markerless"), "n_frames": int(entry.get("n_frames", 0) or 0),
            "duration_s": float(entry.get("duration_s", 0.0) or 0.0), "status": entry.get("status", "recorded"),
            "result": result,
        }
        if entry.get("error"):
            out["error"] = entry["error"]
        if entry.get("quality"):
            out["quality"] = quality_or_none(entry["quality"]) or entry["quality"]
        if isinstance(entry.get("preset"), dict):  # FEATURE_PLAN P2: the scan's preset (none: made before presets)
            out["preset"] = entry["preset"]
        if entry.get("merged_from"):  # a scan merged from others (it has no recording of its own)
            out["merged_from"] = entry["merged_from"]
        if isinstance(entry.get("continues"), dict):  # FEATURE_PLAN N2: a second pass of another scan, and its steps
            out["continues"] = entry["continues"]
        # what the scan keeps on disk (FEATURE_PLAN R2): the mode says what the user removed; has_recording is the
        # truth on disk now
        out["keep"] = entry.get("keep") if entry.get("keep") in KEEP_MODES else "full"
        if entry.get("freed"):
            out["freed"] = entry["freed"]
        if entry.get("thinned"):  # R3: the recording holds only the frames Standard quality uses (at this rate)
            out["thinned"] = entry["thinned"]
        out["has_recording"] = (self.root / pid / "scans" / sid / "recording" / "frames.csv").is_file()
        thumb = self.root / pid / "scans" / sid / "thumbnail.jpg"
        out["thumbnail_url"] = f"{base}/thumbnail.jpg" if thumb.is_file() else None
        return out

    def _summary(self, data: dict) -> dict:
        pid = data["id"]
        thumb = None
        for entry in reversed(data.get("scans", [])):
            if (self.root / pid / "scans" / entry["id"] / "thumbnail.jpg").is_file():
                thumb = f"/api/projects/{pid}/scans/{entry['id']}/files/thumbnail.jpg"
                break
        return {"id": pid, "name": data.get("name", pid), "created": data.get("created"), "modified": data.get("modified"),
                "n_scans": len(data.get("scans", [])), "thumbnail_url": thumb}

    def _project_view(self, data: dict) -> dict:
        out = self._summary(data)
        out["scans"] = [self._scan_view(data["id"], e) for e in data.get("scans", [])]
        return out

    @staticmethod
    def _scan_entry(data: dict, sid: str) -> dict:
        for e in data.get("scans", []):
            if e.get("id") == sid:
                return e
        raise ApiError(404, "There is no such scan in this project: it may have been deleted.",
                       "Pick a scan from the project's list.")

    # ------------------------------------------------------------------ projects
    def list_projects(self) -> list[dict]:
        """``ProjectSummary`` of every project, newest (by creation) first."""
        out = []
        if not self.root.is_dir():
            return out
        with self._lock:
            for d in self.root.iterdir():
                if not d.is_dir() or d.name.startswith(".") or not _SAFE_ID.match(d.name):
                    continue
                if not (d / "project.json").is_file():
                    continue
                try:
                    out.append(self._summary(self._load(d.name)))
                except ApiError as e:
                    log.warning("skipping project folder %s: %s", d, e.detail)
        out.sort(key=lambda s: (s.get("created") or "", s["id"]), reverse=True)
        return out

    def create(self, name: str) -> dict:
        n = _clean_name(name, "project")
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            for _ in range(100):
                pid = f"{_slug(n)}-{secrets.token_hex(2)}"
                if not (self.root / pid).exists():
                    break
            else:  # pragma: no cover - 65536 collisions
                raise ApiError(500, "The project could not be made under this name.", "Try another name.")
            (self.root / pid / "scans").mkdir(parents=True)
            now = now_iso()
            data = {"format_version": FORMAT_VERSION, "id": pid, "name": n, "created": now, "modified": now,
                    "ohs_version": __version__, "scans": []}
            self._save(pid, data)
        log.info("created project %s (%s)", pid, n)
        return self._project_view(data)

    def get(self, pid: str) -> dict:
        with self._lock:
            return self._project_view(self._load(pid))

    def rename(self, pid: str, name: str) -> dict:
        n = _clean_name(name, "project")
        with self._lock:
            data = self._load(pid)
            data["name"] = n
            self._touch(data)
            self._save(pid, data)
            return self._project_view(data)

    def delete(self, pid: str) -> Path:
        """Move the project folder to ``.trash/`` (returns the new location)."""
        with self._lock:
            self._load(pid)
            src = self.project_dir(pid)
            trash = self.root / ".trash"
            trash.mkdir(parents=True, exist_ok=True)
            dst = trash / f"{pid}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            k = 2
            while dst.exists():
                dst = trash / f"{pid}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{k}"
                k += 1
            last: Exception | None = None
            for _ in range(20):
                try:
                    shutil.move(str(src), str(dst))
                    break
                except OSError as e:  # a file of the project is open somewhere (Windows)
                    last = e
                    time.sleep(0.1)
            else:
                log.warning("project %s could not be moved to the trash: %s", pid, last)
                raise ApiError(409, "The project could not be moved to the trash.",
                               "Close any program that has a file of this project open, then try again.")
        log.info("moved project %s to %s", pid, dst)
        return dst

    # ------------------------------------------------------------------ scans
    def add_scan(self, pid: str, name: str | None, mode: str) -> tuple[dict, Path]:
        """Register a new scan (status ``recording``); returns ``(Scan, recording folder)`` (not created)."""
        with self._lock:
            data = self._load(pid)
            n = _clean_name(name, "scan") if name else _next_scan_name(data["scans"])
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            sid, k = f"scan-{stamp}", 2
            ids = {e.get("id") for e in data["scans"]}
            while sid in ids or (self.project_dir(pid) / "scans" / sid).exists():
                sid, k = f"scan-{stamp}-{k}", k + 1
            entry = {"id": sid, "name": n, "created": now_iso(), "mode": mode, "status": "recording",
                     "n_frames": 0, "duration_s": 0.0}
            data["scans"].append(entry)
            self._touch(data)
            (self.scan_dir(pid, sid)).mkdir(parents=True, exist_ok=False)
            self._save(pid, data)
            return self._scan_view(pid, entry), self.recording_dir(pid, sid)

    def update_scan(self, pid: str, sid: str, **fields: Any) -> dict:
        with self._lock:
            data = self._load(pid)
            entry = self._scan_entry(data, sid)
            for k, v in fields.items():
                if v is None and k in ("error", "report"):
                    entry.pop(k, None)
                else:
                    entry[k] = v
            self._touch(data)
            self._save(pid, data)
            return self._scan_view(pid, entry)

    def rename_scan(self, pid: str, sid: str, name: str) -> dict:
        """``PATCH /api/projects/{pid}/scans/{sid}`` (UX_PLAN U7, U50): the scan's new name; its id and folder stay."""
        n = _clean_name(name, "scan")
        with self._lock:
            data = self._load(pid)
            entry = self._scan_entry(data, sid)
            others = [str(e.get("name") or "") for e in data["scans"] if e.get("id") != sid]
            refuse_taken_name(n, others)
            entry["name"] = n
            self._touch(data)
            self._save(pid, data)
            return self._scan_view(pid, entry)

    def scan_dependents(self, pid: str, sid: str) -> list[dict]:
        """The scans of the project that depend on scan ``sid``: merged from it (``merged_from``) or a second pass that
        continues it (``continues.scan_id``). ``[{id, name, how: "merged" | "continues"}]``."""
        with self._lock:
            data = self._load(pid)
        out = []
        for e in data["scans"]:
            if e.get("id") == sid:
                continue
            if any(isinstance(m, dict) and m.get("id") == sid for m in e.get("merged_from") or []):
                out.append({"id": e["id"], "name": e.get("name", e["id"]), "how": "merged"})
            elif isinstance(e.get("continues"), dict) and e["continues"].get("scan_id") == sid:
                out.append({"id": e["id"], "name": e.get("name", e["id"]), "how": "continues"})
        return out

    def trash_scan(self, pid: str, sid: str) -> str:
        """Move the scan's folder to ``.trash/`` with where it came from (:mod:`ohs.server.trash`) and take it off the
        project's list; returns the trash item's id. Nothing is deleted. The caller checks that the scan is not busy."""
        from ohs.server.trash import SCAN_MARK, _move

        with self._lock:
            data = self._load(pid)
            entry = dict(self._scan_entry(data, sid))
            src = self.scan_dir(pid, sid)
            trash = self.root / ".trash"
            trash.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dst, k = trash / f"{pid}--{sid}-{stamp}", 2
            while dst.exists():
                dst, k = trash / f"{pid}--{sid}-{stamp}-{k}", k + 1
            if src.is_dir():
                _move(src, dst)
            else:  # an entry without a folder: the mark alone keeps it
                dst.mkdir()
            mark = {"kind": "scan", "project_id": pid, "project_name": data.get("name"), "entry": entry,
                    "deleted": now_iso(), "format_version": FORMAT_VERSION}
            (dst / SCAN_MARK).write_text(json.dumps(jsonable(mark), indent=2), encoding="utf-8")
            data["scans"] = [e for e in data["scans"] if e.get("id") != sid]
            self._touch(data)
            try:
                self._save(pid, data)
            except OSError:
                (dst / SCAN_MARK).unlink(missing_ok=True)
                if not src.exists():
                    _move(dst, src)  # back, as it was
                raise
        log.info("moved scan %s/%s to %s", pid, sid, dst)
        return dst.name

    def remove_scan(self, pid: str, sid: str) -> None:
        """Forget a scan that never recorded anything (a scan start that failed); its folder is removed."""
        with self._lock:
            data = self._load(pid)
            data["scans"] = [e for e in data["scans"] if e.get("id") != sid]
            self._touch(data)
            self._save(pid, data)
            folder = self.scan_dir(pid, sid)
            frames = folder / "recording" / "frames"
            if not (frames.is_dir() and any(frames.iterdir())):
                shutil.rmtree(folder, ignore_errors=True)

    def get_scan(self, pid: str, sid: str) -> dict:
        with self._lock:
            data = self._load(pid)
            return self._scan_view(pid, self._scan_entry(data, sid))

    def scan_entry(self, pid: str, sid: str) -> dict:
        with self._lock:
            return dict(self._scan_entry(self._load(pid), sid))

    def recover_stale(self, active: set[tuple[str, str]] | None = None) -> int:
        """Reset ``recording`` / ``processing`` scans left by a crash; returns how many were fixed."""
        active = active or set()
        fixed = 0
        if not self.root.is_dir():
            return 0
        with self._lock:
            for d in self.root.iterdir():
                if not d.is_dir() or d.name.startswith(".") or not (d / "project.json").is_file():
                    continue
                try:
                    data = self._load(d.name)
                except ApiError:
                    continue
                changed = False
                for e in data["scans"]:
                    if e.get("status") not in ("recording", "processing") or (d.name, e.get("id")) in active:
                        continue
                    res = d / "scans" / e["id"] / "result" / "mesh.ply"
                    e["status"] = "processed" if (e.get("status") == "processing" and res.is_file()) else "recorded"
                    if not e.get("n_frames"):
                        e["n_frames"] = _count_rows(d / "scans" / e["id"] / "recording" / "frames.csv")
                    changed = True
                    fixed += 1
                    log.warning("scan %s/%s was interrupted (app stopped); marked %s", d.name, e["id"], e["status"])
                if changed:
                    self._touch(data)
                    self._save(d.name, data)
        return fixed


def _count_rows(csv_path: Path) -> int:
    """Data rows of a CSV file; a last line without a line end (cut by a crash) does not count."""
    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            return max(0, sum(1 for line in fh if line.strip() and line.endswith(("\n", "\r"))) - 1)
    except OSError:
        return 0
