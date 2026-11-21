"""The app's own folders, opened from the pages, and the app's log (UX_PLAN U39, U12, U34; APP_API.md "Folders and
the log").

**Opening a folder** (``POST /api/open-folder``): the page says *which* folder, never where: ``"settings"`` (the
folder of the settings file), ``"logs"`` (the app's log, below) or ``"project"`` with a project's id (checked as every
id is, and the project must exist). The folder is shown in the system file manager the way the printables folder is
(:func:`ohs.server.printables.open_folder`). A path sent by the page is never used.

**The log** (:class:`AppLog`): while the app runs, everything the engine logs at "info" and above also goes to
``<data_dir>/logs/studio.log`` (2 MB, then ``studio.log.1`` ... ``.3``). Nothing else changes: what the terminal
shows is set by its own handler (``ohs app -v``). ``GET /api/log/file`` hands it to the page to save for a bug
report, with a few lines about the app and the computer at the top.
"""
from __future__ import annotations

import logging
import logging.handlers
import platform
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ohs import __version__
from ohs.server.protocol import ApiError

__all__ = ["AppLog", "FolderManager", "LOG_NAME", "FOLDERS"]

log = logging.getLogger(__name__)

LOG_NAME = "studio.log"
LOG_BYTES = 2 * 2**20
LOG_BACKUPS = 3
#: what the bug-report file holds at most of the log (the newest part)
REPORT_BYTES = 4 * 2**20
FOLDERS = ("settings", "logs", "project")


class AppLog:
    """The log file of a running app (module docstring). :meth:`start` and :meth:`stop` add and remove its handler
    on the ``ohs`` logger (and set that logger to "info" while it runs, as it was afterwards)."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self._handler: logging.Handler | None = None
        self._level: int | None = None
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self.folder / LOG_NAME

    def start(self) -> None:
        with self._lock:
            if self._handler is not None:
                return
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                h = logging.handlers.RotatingFileHandler(self.path, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS,
                                                         encoding="utf-8", delay=True)
            except OSError as e:  # a log must never stop the app
                log.warning("the app's log file could not be opened in %s: %s", self.folder, e)
                return
            h.setLevel(logging.INFO)
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            lg = logging.getLogger("ohs")
            self._level = lg.level
            if lg.level == logging.NOTSET or lg.level > logging.INFO:
                lg.setLevel(logging.INFO)
            lg.addHandler(h)
            self._handler = h
        log.info("OpenHandScan Studio %s started; this log is %s", __version__, self.path)

    def stop(self) -> None:
        with self._lock:
            h, self._handler = self._handler, None
            if h is None:
                return
            lg = logging.getLogger("ohs")
            lg.removeHandler(h)
            if self._level is not None:
                lg.setLevel(self._level)
            h.close()

    def report(self) -> str:
        """The log for a bug report: a header (the app, the computer) and the newest part of the log files."""
        with self._lock:
            if self._handler is not None:
                self._handler.flush()
        head = [f"OpenHandScan Studio {__version__}: the app's log, saved {datetime.now().isoformat(timespec='seconds')}",
                f"Computer: {platform.platform()}; Python {platform.python_version()}"]
        try:
            from ohs import compute

            head.append(f"Compute: {compute.describe()}")
        except Exception as e:  # noqa: BLE001 - the header is a help, not a must
            log.debug("compute.describe failed: %s", e)
        parts: list[str] = []
        files = [self.folder / f"{LOG_NAME}.{k}" for k in range(LOG_BACKUPS, 0, -1)] + [self.path]
        for f in files:
            try:
                parts.append(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
        body = "".join(parts)
        if len(body) > REPORT_BYTES:
            body = "(the oldest part of the log is left out)\n" + body[-REPORT_BYTES:]
        return "\n".join(head) + "\n\n" + (body or "Nothing was logged yet.\n")


class FolderManager:
    """``POST /api/open-folder`` (module docstring)."""

    def __init__(self, st: Any) -> None:
        self.st = st

    def folder(self, what: Any, project_id: Any = None) -> Path:
        if what not in FOLDERS:
            raise ApiError(422, "The app does not know this folder.",
                           "Open the settings folder, the log folder or a project's folder.")
        if what == "settings":
            return Path(self.st.config_path).parent
        if what == "logs":
            return self.st.app_log.folder
        if not isinstance(project_id, str) or not project_id:
            raise ApiError(422, "The page did not say which project's folder to open.", "Open the project, then try again.")
        self.st.projects.get(project_id)  # checks the id; 404 for a project that is not there
        return self.st.projects.project_dir(project_id)

    def open(self, what: Any, project_id: Any = None) -> dict:
        from ohs.server import printables

        folder = self.folder(what, project_id)
        if not printables.open_folder(folder):
            raise ApiError(409, "The folder could not be opened.",
                           "Your computer did not open a file manager for it. Try again in a moment.")
        return {"opened": True, "what": what}
