"""The trash: deleted projects and scans, restored or deleted for good (UX_PLAN U7; APP_API.md "The trash").

Deleting a project or a scan never deletes anything: its folder is moved into ``<projects>/.trash`` (a project by
:meth:`ProjectStore.delete <ohs.server.projects.ProjectStore.delete>`, a scan by
:meth:`ProjectStore.trash_scan <ohs.server.projects.ProjectStore.trash_scan>`)::

    <projects>/.trash/<project_id>-<time>/              a project: its folder as it was (project.json inside)
    <projects>/.trash/<project_id>--<scan_id>-<time>/   a scan: its folder as it was, and trashed-scan.json
                                                        (its project, the project's name, its entry, when)

An item's id is its folder's name. Ids only: a name from the page is checked against the ids rule and must be a
folder directly inside the trash that holds a project or a scan; nothing else is ever touched (a folder the user put
there is not listed, restored or deleted).

**Restore** puts the folder back where it was. A project whose id is taken gets a new id; a scan whose id is taken
in its project gets a new id; a name that is taken gets " (restored)" (then " (restored 2)" ...). Nothing is ever
overwritten. A scan whose project is not there (in the trash too, or gone) is refused: restore the project first.

**Empty the trash** deletes for good, so it needs ``confirm`` and the ids the page showed: an item moved to the trash
after the page listed it stays.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ohs.server.disk import folder_bytes
from ohs.server.projects import _SAFE_ID, NAME_MAX, ProjectStore, _slug
from ohs.server.protocol import ApiError, jsonable, now_iso

__all__ = ["TrashManager", "SCAN_MARK", "restored_name"]

log = logging.getLogger(__name__)

#: the file a trashed scan's folder carries: where it came from
SCAN_MARK = "trashed-scan.json"
_STAMP = re.compile(r"-(\d{8})-(\d{6})(?:-\d+)?$")


def restored_name(name: str, taken: set[str]) -> str:
    """``name``, or "<name> (restored)", "<name> (restored 2)" ... when it is taken (within the length a name has)."""
    if name not in taken:
        return name
    k = 1
    while True:
        tail = " (restored)" if k == 1 else f" (restored {k})"
        n = name[: NAME_MAX - len(tail)].rstrip() + tail
        if n not in taken:
            return n
        k += 1


def _when_of(folder_name: str) -> str | None:
    m = _STAMP.search(folder_name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").astimezone().isoformat(timespec="seconds")
    except ValueError:
        return None


def _move(src: Path, dst: Path) -> None:
    """Move a folder (retried: on Windows a file of it may be open for a moment)."""
    last: Exception | None = None
    for _ in range(20):
        try:
            shutil.move(str(src), str(dst))
            return
        except OSError as e:
            last = e
            time.sleep(0.1)
    log.warning("could not move %s to %s: %s", src, dst, last)
    raise ApiError(409, "The folder could not be moved.",
                   "Close any program that has a file of it open, then try again.")


class TrashManager:
    """The trash of a :class:`ProjectStore` (module docstring). Thread-safe through the store's lock."""

    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    @property
    def root(self) -> Path:
        return self.store.root / ".trash"

    # ------------------------------------------------------------------ reading
    def _item_dir(self, item_id: str) -> Path:
        if not isinstance(item_id, str) or not _SAFE_ID.match(item_id) or ".." in item_id:
            raise ApiError(404, "There is no such item in the trash: it may have been restored or deleted.",
                           "Open the trash again to see what it holds.")
        d = self.root / item_id
        if not d.is_dir() or d.is_symlink() or self._kind(d) is None:
            raise ApiError(404, "There is no such item in the trash: it may have been restored or deleted.",
                           "Open the trash again to see what it holds.")
        return d

    @staticmethod
    def _kind(d: Path) -> str | None:
        if (d / SCAN_MARK).is_file():
            return "scan"
        if (d / "project.json").is_file():
            return "project"
        return None

    @staticmethod
    def _read(p: Path) -> dict:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _item(self, d: Path) -> dict | None:
        kind = self._kind(d)
        if kind is None:
            return None
        if kind == "project":
            pj = self._read(d / "project.json")
            return {"id": d.name, "kind": "project", "name": str(pj.get("name") or "A project"),
                    "deleted": _when_of(d.name), "bytes": folder_bytes(d), "n_scans": len(pj.get("scans") or []),
                    "project_id": None, "project_name": None, "project_exists": None}
        mark = self._read(d / SCAN_MARK)
        entry = mark.get("entry") if isinstance(mark.get("entry"), dict) else {}
        pid = mark.get("project_id")
        exists = isinstance(pid, str) and bool(_SAFE_ID.match(pid)) and (self.store.root / pid / "project.json").is_file()
        return {"id": d.name, "kind": "scan", "name": str(entry.get("name") or "A scan"),
                "deleted": mark.get("deleted") or _when_of(d.name), "bytes": folder_bytes(d), "n_scans": None,
                "project_id": pid if isinstance(pid, str) else None, "project_name": mark.get("project_name"),
                "project_exists": exists}

    def list(self) -> dict:
        """``GET /api/trash``: ``{items: [TrashItem], bytes}``, the newest first."""
        items: list[dict] = []
        if self.root.is_dir():
            with self.store._lock:
                for e in os.scandir(self.root):
                    if not e.is_dir(follow_symlinks=False) or not _SAFE_ID.match(e.name):
                        continue
                    it = self._item(Path(e.path))
                    if it is not None:
                        items.append(it)
        items.sort(key=lambda i: (i.get("deleted") or "", i["id"]), reverse=True)
        return jsonable({"items": items, "bytes": sum(i["bytes"] for i in items)})

    # ------------------------------------------------------------------ restore
    def restore(self, item_id: str) -> dict:
        """``POST /api/trash/{item}/restore``: ``{kind, project_id, scan_id, name, renamed}``."""
        st = self.store
        with st._lock:
            d = self._item_dir(item_id)
            return self._restore_scan(d) if self._kind(d) == "scan" else self._restore_project(d)

    def _restore_project(self, d: Path) -> dict:
        st = self.store
        pj = self._read(d / "project.json")
        name = str(pj.get("name") or "Restored project")
        pid = pj.get("id") if isinstance(pj.get("id"), str) and _SAFE_ID.match(pj["id"]) and ".." not in pj["id"] else None
        if pid is None:
            m = _STAMP.search(d.name)
            pid = d.name[: m.start()] if m else None
            if pid is not None and (not _SAFE_ID.match(pid) or ".." in pid):
                pid = None
        taken = {p["name"] for p in st.list_projects()}
        new_name = restored_name(name, taken)
        new_id = pid
        while new_id is None or (st.root / new_id).exists():
            new_id = f"{_slug(new_name)}-{secrets.token_hex(2)}"
        st.root.mkdir(parents=True, exist_ok=True)
        _move(d, st.root / new_id)
        if new_id != pid or new_name != name:
            data = st._load(new_id)
            data["name"] = new_name
            st._save(new_id, data)
        log.info("restored project %s from the trash as %s (%s)", d.name, new_id, new_name)
        return {"kind": "project", "project_id": new_id, "scan_id": None, "name": new_name,
                "renamed": new_id != pid or new_name != name}

    def _restore_scan(self, d: Path) -> dict:
        st = self.store
        mark = self._read(d / SCAN_MARK)
        pid = mark.get("project_id")
        entry = dict(mark.get("entry")) if isinstance(mark.get("entry"), dict) else {}
        if not isinstance(pid, str) or not _SAFE_ID.match(pid) or ".." in pid or not (st.root / pid / "project.json").is_file():
            raise ApiError(409, "The project this scan was in is not there: it is in the trash too, or it was deleted.",
                           "Restore the project first, then the scan.")
        data = st._load(pid)
        sid = entry.get("id") if isinstance(entry.get("id"), str) and _SAFE_ID.match(entry["id"]) else None
        ids = {e.get("id") for e in data["scans"]}

        def free(c: str) -> bool:
            return bool(_SAFE_ID.match(c)) and c not in ids and not (st.root / pid / "scans" / c).exists()

        new_sid = sid
        if new_sid is None or not free(new_sid):
            base, k = sid or f"scan-{datetime.now().strftime('%Y%m%d-%H%M%S')}", 1
            while not free(new_sid := (f"{base}-r" if k == 1 else f"{base}-r{k}")):
                k += 1
        name = str(entry.get("name") or "Restored scan")
        new_name = restored_name(name, {str(e.get("name")) for e in data["scans"]})
        (st.root / pid / "scans").mkdir(parents=True, exist_ok=True)
        dst = st.root / pid / "scans" / new_sid
        _move(d, dst)
        try:
            (dst / SCAN_MARK).unlink()
        except OSError as e:  # it only says where the scan came from
            log.warning("could not remove %s: %s", dst / SCAN_MARK, e)
        entry.update(id=new_sid, name=new_name)
        if entry.get("status") in ("recording", "processing"):  # it was not, when it went; be safe
            entry["status"] = "processed" if (dst / "result" / "mesh.ply").is_file() else "recorded"
        # back in its old place, not last: the scans are listed by their own creation time (the project page's order),
        # so it goes before the first one made after it; an entry without a time goes last, as before
        created = entry.get("created")
        at = len(data["scans"])
        if isinstance(created, str) and created:
            at = next((k for k, e in enumerate(data["scans"]) if isinstance(e.get("created"), str) and e["created"] > created), at)
        data["scans"].insert(at, entry)
        st._touch(data)
        try:
            st._save(pid, data)
        except OSError:
            _move(dst, d)  # back to the trash, as it was
            raise
        log.info("restored scan %s into %s as %s (%s)", d.name, pid, new_sid, new_name)
        return {"kind": "scan", "project_id": pid, "scan_id": new_sid, "name": new_name,
                "renamed": new_sid != sid or new_name != name}

    # ------------------------------------------------------------------ for good
    def empty(self, confirm: Any, items: Any) -> dict:
        """``POST /api/trash/empty``: deletes for good the listed items (only those still in the trash and holding a
        project or a scan). ``{removed: [ids], left: [ids that could not be deleted], bytes}``."""
        if confirm is not True:
            raise ApiError(400, "Emptying the trash deletes its items for good, so it needs your confirmation.",
                           "Press Empty the trash again and confirm it.")
        if not isinstance(items, list) or not items:
            raise ApiError(400, "The page did not say which items to delete for good.",
                           "Open the trash again and press Empty the trash.")
        removed: list[str] = []
        left: list[str] = []
        freed = 0
        with self.store._lock:
            for item_id in dict.fromkeys(i for i in items if isinstance(i, str)):
                try:
                    d = self._item_dir(item_id)
                except ApiError:
                    continue  # not an item of the trash (restored, already gone, or not ours): nothing is touched
                size = folder_bytes(d)
                shutil.rmtree(d, ignore_errors=True)
                if d.exists():
                    left.append(item_id)
                    log.warning("trash item %s could not be deleted completely", item_id)
                else:
                    removed.append(item_id)
                    freed += size
                    log.info("deleted %s from the trash for good (%d bytes) at %s", item_id, size, now_iso())
        return {"removed": removed, "left": left, "bytes": freed}
