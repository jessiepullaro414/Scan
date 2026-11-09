"""The setup wizard's state (APP_API.md, "M2: setup and calibration"): ``GET /api/setup`` and friends.

**Targets.** The wizard calibrates the ``"device"`` (the configured calibration file,
``paths.calibration``) or, while the simulator is connected, the ``"sim"`` sandbox
(``<data_dir>/sim/calibration.json``). :meth:`SetupManager.target` picks it from the device.

**Persistence.** :class:`SetupStore` keeps ``<data_dir>/setup.json``: one section per target
(the dismissed flag, when the scanner / simulator was last connected, the last self-test, the
print check and the board scale (device only), what Studio recorded when it saved each
calibration part (:data:`PartInfo`), the last 2-minute check and the last launch check) plus the
manifest of the last generated printables. Every change is written at once (temporary file +
rename), so a crash never leaves half a file; an unreadable file is set aside as
``setup.json.bad-<time>`` and the wizard starts over.

**Steps** (always all seven, in order; :meth:`SetupManager.state`):

* ``connect``: done once a camera (device target) or the simulator (sim target) connected;
* ``selftest``: done on PASS, warn on WARN or FAIL;
* ``print``: device: done when the print check is within 0.3 %, warn otherwise; sim: optional;
* ``stereo`` / ``lasers`` / ``imu``: from the target's calibration file;
* ``check``: done when the last check passed after the last save, warn when it failed.

**Print check** (:meth:`SetupManager.print_check`): the 100 mm bar as measured with calipers.
Within 0.3 % it is exact (scale 1). Up to 4 % the printer scaled the page: reprint at 100 %, or
``apply`` the measurement, which stores ``scale = measured / 100``. Beyond 4 %: measure again.
The scale multiplies ``square_mm`` and ``marker_mm`` only in the spec used for *calibration*
(:meth:`SetupManager.calib_spec`), never for printing.

**Devices probe** (:meth:`SetupManager.devices_probe`): ``list_cameras`` plus ``find_pico_port``.
The index the connected camera holds is not opened again (that could disturb it): its entry comes
from the running camera.

**Jobs**: :meth:`SetupManager.start_selftest` (kind ``selftest``, :mod:`ohs.server.selftest`)
and :meth:`SetupManager.start_printables` (kind ``printables``, :mod:`ohs.server.printables`;
a paper change is saved to ``board.paper`` first, with :func:`ohs.app.config.set_config_values`).

Locking: the store has its own lock and is only ever taken on its own (never while holding
another lock that a store caller might need). :class:`SetupManager` takes ``Studio._lock`` for
the self-test only: to refuse it during a scan or a calibration, and around marking the device
busy, disconnecting and reconnecting the camera.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ohs.server.protocol import ApiError, jsonable, now_iso

__all__ = ["SetupStore", "SetupManager", "STEP_IDS", "TARGETS", "ago", "parse_iso", "PRINT_OK_PCT", "PRINT_APPLY_PCT"]

log = logging.getLogger(__name__)

STEP_IDS = ("connect", "selftest", "print", "stereo", "lasers", "imu", "check")
TARGETS = ("device", "sim")
PARTS = ("stereo", "lasers", "imu")
PRINT_OK_PCT = 0.3  # |error| within this: the print is exact
PRINT_APPLY_PCT = 4.0  # up to this: a printer's scaling, the measurement can be used
BAR_MM = 100.0
MAX_CAMERA_INDEX = 8
FORMAT_VERSION = 1


# --------------------------------------------------------------------------- small helpers
def parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def ago(when: str | None, now: datetime | None = None) -> str:
    """"today", "yesterday", "3 days ago" or "on 2026-10-01" (local dates)."""
    dt = parse_iso(when)
    if dt is None:
        return "some time ago"
    now = now or datetime.now(timezone.utc)
    days = (now.astimezone().date() - dt.astimezone().date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    return f"on {dt.astimezone().date().isoformat()}"


def _mtime_iso(p: Path) -> str | None:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return None


def _later(a: str | None, b: str | None) -> str | None:
    da, db = parse_iso(a), parse_iso(b)
    if da is None:
        return b
    if db is None:
        return a
    return a if da >= db else b


def _replace(tmp: Path, dst: Path) -> None:
    for attempt in range(50):  # Windows: a reader may hold the file for a moment
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.02)


# --------------------------------------------------------------------------- persistence
def _empty_target() -> dict:
    return {"dismissed": False, "connect": None, "selftest": None, "print_check": None, "board_scale": 1.0,
            "parts": {k: None for k in PARTS}, "last_check": None, "launch_check": None}


class SetupStore:
    """``setup.json``: per-target setup progress plus the printables manifest. Thread-safe."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict:
        data: dict = {}
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("not a JSON object")
            except (OSError, ValueError) as e:
                bad = self.path.with_name(f"{self.path.name}.bad-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
                log.warning("cannot read %s (%s); setting it aside as %s and starting over", self.path, e, bad.name)
                try:
                    self.path.rename(bad)
                except OSError:
                    pass
                data = {}
        targets = data.get("targets") if isinstance(data.get("targets"), dict) else {}
        out = {"format_version": FORMAT_VERSION, "targets": {}, "printables": data.get("printables")}
        for t in TARGETS:
            sec = _empty_target()
            got = targets.get(t)
            if isinstance(got, dict):
                sec.update({k: v for k, v in got.items() if k in sec})
                parts = got.get("parts") if isinstance(got.get("parts"), dict) else {}
                sec["parts"] = {k: parts.get(k) for k in PARTS}
            out["targets"][t] = sec
        return out

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        tmp.write_text(json.dumps(jsonable(self._data), indent=2), encoding="utf-8")
        _replace(tmp, self.path)

    def target(self, name: str) -> dict:
        with self._lock:
            return json.loads(json.dumps(jsonable(self._data["targets"][name])))

    def update(self, name: str, **fields: Any) -> None:
        with self._lock:
            sec = self._data["targets"][name]
            for k, v in fields.items():
                if k not in sec:
                    raise KeyError(k)
                sec[k] = v
            self._save()

    def set_part(self, name: str, part: str, info: dict | None) -> None:
        with self._lock:
            self._data["targets"][name]["parts"][part] = info
            self._save()

    def printables(self) -> dict | None:
        with self._lock:
            p = self._data.get("printables")
            return dict(p) if isinstance(p, dict) else None

    def set_printables(self, manifest: dict | None) -> None:
        with self._lock:
            self._data["printables"] = manifest
            self._save()


# --------------------------------------------------------------------------- the manager
class SetupManager:
    """Setup state, print check and devices probe for the :class:`~ohs.server.studio.Studio`."""

    def __init__(self, studio: Any) -> None:
        from ohs.server import selftest

        self.st = studio
        self.store = SetupStore(Path(studio.data_dir) / "setup.json")
        self.selftest_seconds = selftest.CAMERA_SECONDS  # run_selftest's measurement and trigger windows
        self.stream_seconds = selftest.STREAM_SECONDS  # the simulator / replay stream checks

    # ------------------------------------------------------------------ targets
    def target(self) -> str:
        return "sim" if self.st.devices.source == "sim" else "device"

    def calibration_path(self, target: str) -> Path:
        return self.st.sim_calib_path if target == "sim" else self.st.cfg.paths.calibration_path

    def load_calibration(self, target: str) -> Any:
        """The target's calibration, or None (missing or unreadable file)."""
        p = self.calibration_path(target)
        if not p.is_file():
            return None
        try:
            from ohs.types import ScannerCalibration

            return ScannerCalibration.load(p)
        except Exception as e:
            log.warning("cannot read the calibration %s: %s", p, e)
            return None

    def board_scale(self, target: str) -> float:
        if target == "sim":
            return 1.0
        try:
            s = float(self.store.target("device").get("board_scale") or 1.0)
        except (TypeError, ValueError):
            s = 1.0
        return s if 0.9 <= s <= 1.1 else 1.0

    def calib_spec(self, target: str) -> Any:
        """The board spec for calibration: the printed board, scaled by the measured print scale."""
        spec = self.st.cfg.board.spec()
        s = self.board_scale(target)
        if abs(s - 1.0) < 1e-9:
            return spec
        return dataclasses.replace(spec, square_mm=spec.square_mm * s, marker_mm=spec.marker_mm * s)

    def board_info(self, target: str) -> dict:
        b = self.st.cfg.board
        scale = self.board_scale(target)
        paper = {"letter": "Letter", "a4": "A4"}.get(str(b.paper).lower(), str(b.paper))
        text = (f"{b.squares_x} x {b.squares_y} squares of {b.square_mm:g} mm (markers {b.marker_mm:g} mm, "
                f"{b.dictionary}), printed on {paper}")
        if abs(scale - 1.0) > 1e-9:
            text += (f"; calibrated with your measured print scale of {scale * 100:.1f} % "
                     f"(squares of {b.square_mm * scale:.2f} mm)")
        return {"squares_x": int(b.squares_x), "squares_y": int(b.squares_y), "square_mm": float(b.square_mm),
                "marker_mm": float(b.marker_mm), "dictionary": str(b.dictionary), "paper": str(b.paper).lower(),
                "scale": float(scale), "describe": text}

    # ------------------------------------------------------------------ the state
    def state(self, target: str | None = None) -> dict:
        """``SetupState`` of ``target`` (default: the current one)."""
        target = target or self.target()
        sec = self.store.target(target)
        path = self.calibration_path(target)
        exists = path.is_file()
        calib = self.load_calibration(target) if exists else None
        modified = _mtime_iso(path) if exists else None
        backup = path.with_name(path.name + ".bak")
        lasers = sorted((calib.lasers or {}).keys()) if calib is not None else []
        parts = {k: None for k in PARTS}
        if calib is not None:
            have = {"stereo": True, "lasers": bool(lasers), "imu": calib.imu is not None}
            parts = {k: (sec["parts"].get(k) if have[k] else None) for k in PARTS}
        cal = {
            "path": str(path), "exists": exists, "modified": modified,
            "image_size": [int(v) for v in calib.stereo.image_size] if calib is not None else None,
            "baseline_mm": round(float(calib.stereo.baseline), 3) if calib is not None else None,
            "stereo": calib is not None, "lasers": lasers, "imu": bool(calib is not None and calib.imu is not None),
            "parts": parts, "backup": str(backup) if backup.is_file() else None,
        }
        dismissed = bool(sec.get("dismissed"))
        return {
            "target": target,
            "first_run": calib is None and not dismissed,
            "dismissed": dismissed,
            "steps": self._steps(target, sec, calib, cal),
            "calibration": cal,
            "selftest": sec.get("selftest"),
            "print_check": sec.get("print_check") if target == "device" else None,
            "board": self.board_info(target),
            "launch_check": sec.get("launch_check"),
            "last_check": sec.get("last_check"),
        }

    def _steps(self, target: str, sec: dict, calib: Any, cal: dict) -> list[dict]:
        sim = target == "sim"

        def step(sid: str, state: str, summary: str, when: str | None = None) -> dict:
            return {"id": sid, "state": state, "summary": summary, "when": when}

        out = []
        con = sec.get("connect")
        if isinstance(con, dict) and con.get("when"):
            out.append(step("connect", "done", f"{con.get('summary') or 'Connected'}, {ago(con['when'])}", con["when"]))
        else:
            out.append(step("connect", "todo", "Connect the simulator" if sim else
                            "Connect the scanner (or try the simulator)"))
        st = sec.get("selftest")
        if isinstance(st, dict) and st.get("overall"):
            checks = [c for c in st.get("checks", []) if isinstance(c, dict)]
            n = sum(c.get("status") in ("PASS", "WARN", "FAIL") for c in checks)
            warn = [c.get("name", "?") for c in checks if c.get("status") == "WARN"]
            fail = [c.get("name", "?") for c in checks if c.get("status") == "FAIL"]
            when = st.get("when")
            if st["overall"] == "PASS":
                out.append(step("selftest", "done", f"All {n} checks passed, {ago(when)}", when))
            elif fail:
                out.append(step("selftest", "warn", f"Failed: {', '.join(fail[:3])} ({ago(when)})", when))
            else:
                w = f"{len(warn)} warning{'s' if len(warn) != 1 else ''}"
                out.append(step("selftest", "warn", f"{w}: {', '.join(warn[:3])} ({ago(when)})", when))
        else:
            out.append(step("selftest", "todo", "Check the simulated camera and IMU" if sim else
                            "Test the camera, the IMU, the emitters and the trigger (1 minute)"))
        if sim:
            out.append(step("print", "optional", "Not needed: the simulator's board is virtual and exact"))
        else:
            pc = sec.get("print_check")
            if isinstance(pc, dict) and pc.get("when"):
                pct = 100.0 + float(pc.get("error_pct") or 0.0)
                if pc.get("ok"):
                    out.append(step("print", "done", f"Print scale exact ({pc.get('measured_mm'):.1f} mm bar), "
                                                     f"{ago(pc['when'])}", pc["when"]))
                elif abs(float(pc.get("scale") or 1.0) - 1.0) > 1e-9:
                    out.append(step("print", "warn", f"Printed at {pct:.1f} %: calibrating with your measurement",
                                    pc["when"]))
                else:
                    out.append(step("print", "warn", f"Printed at {pct:.1f} %: reprint at 100 % or use your measurement",
                                    pc["when"]))
            else:
                out.append(step("print", "todo", "Print the board, then measure its 100 mm bar"))
        parts = cal["parts"]
        modified = cal["modified"]

        def part_summary(kind: str, fallback: str) -> tuple[str, str | None]:
            info = parts.get(kind)
            if isinstance(info, dict) and info.get("when"):
                return f"{info.get('summary') or 'Calibrated'}, {ago(info['when'])}", info["when"]
            return f"{fallback}, {ago(modified)}" if modified else fallback, modified

        if calib is None:
            out.append(step("stereo", "todo", "Calibrate the two cameras with the board (about 5 minutes)"))
            out.append(step("lasers", "todo", "Calibrate the cameras first, then the laser lines"))
            out.append(step("imu", "todo", "Calibrate the cameras first, then the IMU"))
        else:
            rms = calib.stereo.rms
            text = f"Calibrated ({rms:.2f} px)" if rms == rms else "Calibrated"
            s, w = part_summary("stereo", text)
            out.append(step("stereo", "done", s, w))
            lasers = cal["lasers"]
            if {"vertical", "horizontal"} <= set(lasers):
                s, w = part_summary("lasers", "Both laser lines calibrated")
                out.append(step("lasers", "done", s, w))
            elif lasers:
                missing = "horizontal" if "vertical" in lasers else "vertical"
                out.append(step("lasers", "warn", f"Only the {lasers[0]} laser is calibrated: calibrate the "
                                                  f"{missing} one too", modified))
            else:
                out.append(step("lasers", "todo", "Calibrate the laser lines with the board (about 3 minutes)"))
            if calib.imu is not None:
                s, w = part_summary("imu", "IMU calibrated")
                out.append(step("imu", "done", s, w))
            else:
                out.append(step("imu", "todo", "Calibrate the IMU: hold still, then rotate (about 2 minutes)"))
        last = sec.get("last_check")
        saved = None
        for k in PARTS:
            info = parts.get(k)
            if isinstance(info, dict):
                saved = _later(saved, info.get("when"))
        t_saved = parse_iso(saved or modified)
        t_check = parse_iso(last.get("when")) if isinstance(last, dict) else None
        if calib is None:
            out.append(step("check", "todo", "Check the calibration once it is done (2 minutes)"))
        elif t_check is not None and (t_saved is None or t_check >= t_saved):
            if last.get("ok"):
                out.append(step("check", "done", f"Calibration verified, {ago(last['when'])}", last["when"]))
            else:
                out.append(step("check", "warn", f"The last check failed ({ago(last['when'])}): "
                                                 f"{last.get('verdict') or 'recalibrate'}", last["when"]))
        else:
            out.append(step("check", "todo", "Run the 2-minute check of the calibration"))
        return out

    def publish(self) -> None:
        self.st.hub.publish_json({"type": "setup", **self.state()})

    # ------------------------------------------------------------------ recording facts
    def dismiss(self, dismissed: bool) -> dict:
        self.store.update(self.target(), dismissed=bool(dismissed))
        self.publish()
        return self.state()

    def record_connect(self, source: str) -> None:
        """A camera or the simulator connected (``DeviceManager.on_connected``; replays do not count)."""
        if source not in ("camera", "sim"):
            return
        if source == "sim":
            summary = "Simulator connected"
            target = "sim"
        else:
            target = "device"
            dev = self.st.devices.state()
            info = (dev.get("camera") or {}).get("info", {})
            w, h = info.get("width"), info.get("height")
            size = f" ({w}x{h})" if w and h else ""
            pico = dev.get("pico") or {}
            if pico.get("connected"):
                fw = f" (firmware {pico['firmware']})" if pico.get("firmware") else ""
                summary = f"Camera{size} and Pico{fw} connected"
            else:
                summary = f"Camera{size} connected, but no Pico"
        self.store.update(target, connect={"when": now_iso(), "summary": summary})
        self.publish()

    def record_selftest(self, target: str, result: dict) -> None:
        self.store.update(target, selftest=result)
        self.publish()

    def record_launch_check(self, source: str, lc: dict) -> None:
        self.store.update("sim" if source == "sim" else "device", launch_check=lc)
        self.publish()

    def record_last_check(self, target: str, ok: bool, verdict: str) -> None:
        self.store.update(target, last_check={"when": now_iso(), "ok": bool(ok), "verdict": str(verdict)})

    def record_part(self, target: str, part: str, summary: str, session: str | None) -> None:
        self.store.set_part(target, part, {"when": now_iso(), "summary": summary, "session": session})

    def drop_part(self, target: str, part: str) -> None:
        self.store.set_part(target, part, None)

    # ------------------------------------------------------------------ print check
    def print_check(self, measured_mm: float, apply: bool = False) -> dict:
        """Check the printed 100 mm bar (stored for the device: the print is physical)."""
        m = float(measured_mm)
        if not (m > 0 and m < 1000):
            raise ApiError(400, f"{measured_mm!r} mm is not a plausible length for the 100 mm bar",
                           "measure the bar between its outer edges with calipers and type the reading in mm")
        err = (m - BAR_MM) / BAR_MM * 100.0
        pct = m / BAR_MM * 100.0
        square = float(self.st.cfg.board.square_mm)
        scale, ok, can_apply = 1.0, False, False
        if abs(err) <= PRINT_OK_PCT:
            ok = True
            verdict = (f"Your print is accurate: the bar measures {m:.2f} mm ({err:+.2f} %). "
                       "The board is ready for calibration.")
        elif abs(err) <= PRINT_APPLY_PCT:
            can_apply = True
            base = (f"Your printer scaled the page by {err:+.1f} % (it printed at {pct:.1f} %: the 100 mm bar "
                    f"measures {m:.2f} mm).")
            if apply:
                scale = m / BAR_MM
                verdict = (base + f" Using your measurement: the app calibrates with squares of {square * scale:.2f} mm. "
                                  "For the best result, reprint at 100 % / Actual size.")
            else:
                verdict = (base + " Reprint it at 100 % / Actual size (turn off “Fit to page”), or use your "
                                  "measurement and the app calibrates with the measured size.")
        else:
            verdict = (f"The bar measures {m:.2f} mm, {abs(err):.1f} % off: more than a printer scales a page. Measure "
                       "again between the outer edges of the bar; if it really is that far off, reprint at 100 % / "
                       "Actual size.")
        pc = {"measured_mm": round(m, 3), "error_pct": round(err, 3), "ok": ok, "scale": round(scale, 6),
              "verdict": verdict, "can_apply": can_apply, "when": now_iso()}
        self.store.update("device", print_check=pc, board_scale=float(scale))
        log.info("print check: %.2f mm (%+.2f %%), scale %.4f", m, err, scale)
        self.publish()
        return pc

    # ------------------------------------------------------------------ self-test job
    def start_selftest(self) -> Any:
        """The ``selftest`` job (:mod:`ohs.server.selftest`): camera path or stream checks by source."""
        from ohs.server import selftest

        st = self.st
        with st._lock:
            if st.session is not None and st.session.active:
                raise ApiError(409, "a scan is running", "stop the scan before running the self-test")
            if st.calibration.active:
                raise ApiError(409, "a calibration is running", "finish or cancel the calibration first")
            if st.jobs.active("selftest"):
                raise ApiError(409, "the self-test is already running", "wait for it to finish")
            if st.devices.busy:
                raise ApiError(409, f"the device is busy: {st.devices.busy}", "wait until it finishes")
            source = st.devices.source
        target = "sim" if source == "sim" else "device"

        def run(ctx: Any) -> dict:
            def message(text: str) -> None:
                ctx.progress(None, text, force=True)

            def progress(f: float) -> None:
                ctx.progress(f)

            if source in ("sim", "replay"):
                checks = selftest.run_stream_selftest(st.devices, source, message, progress, lambda: ctx.cancelled,
                                                      seconds=float(self.stream_seconds))
                res = selftest.result_dict(checks, source)
            else:
                with st._lock:
                    if (st.session is not None and st.session.active) or st.calibration.active:
                        raise ApiError(409, "a scan or a calibration started meanwhile", "run the self-test again later")
                    was_connected = st.devices.source == "camera"
                    st.devices.set_busy("Running the self-test…")
                    try:
                        if was_connected:
                            st.devices.disconnect("running the self-test")
                    except BaseException:
                        st.devices.set_busy(None)
                        raise
                try:
                    checks = selftest.run_camera_selftest(st.cfg, st.devices.camera_factory, st.devices.pico_factory,
                                                          message, progress, seconds=float(self.selftest_seconds))
                finally:
                    with st._lock:
                        st.devices.set_busy(None)
                        if was_connected:
                            message("Connecting the camera again…")
                            try:
                                st.devices.connect("camera")
                            except ApiError as e:
                                st.notify("error", f"The camera could not be connected again after the self-test: {e}")
                res = selftest.result_dict(checks, "camera")
            self.record_selftest(target, {k: v for k, v in res.items() if k != "message"})
            return res

        def done(job: Any) -> None:
            if job.state == "done" and job.result:
                level = {"PASS": "info", "WARN": "warn", "FAIL": "error"}.get(job.result.get("overall"), "info")
                st.notify(level, job.result.get("message", "Self-test finished"))
            elif job.state == "failed":
                st.notify("error", f"The self-test could not run: {job.error}")

        return st.jobs.submit("selftest", run, message="Starting the self-test…", on_done=done)

    # ------------------------------------------------------------------ printables
    def printables_state(self) -> dict:
        from ohs.server import printables

        cfg = self.st.cfg
        return printables.listing(self.store.printables(), cfg.paths.printables_path, cfg.board.paper)

    def start_printables(self, paper: str | None = None) -> Any:
        """The ``printables`` job; a paper change is saved to ``board.paper`` first."""
        from ohs.app.config import ConfigError, set_config_values
        from ohs.server import printables

        st = self.st
        cfg = st.cfg
        if st.jobs.active("printables"):
            raise ApiError(409, "the printables are already being generated", "wait for the job to finish")
        paper = str(paper or cfg.board.paper).lower()
        if paper not in ("letter", "a4"):
            raise ApiError(400, f"unknown paper {paper!r}", "use letter or a4")
        if paper != str(cfg.board.paper).lower():
            try:
                set_config_values({"board": {"paper": paper}}, st.config_path, cfg=cfg)
            except (ConfigError, OSError) as e:
                raise ApiError(500, f"the paper size could not be saved to {st.config_path}: {e}",
                               "check that the config file is valid TOML and writable") from e
        spec = cfg.board.spec()  # as configured: a measured print scale never changes the print
        out = cfg.paths.printables_path
        tag_mm = float(cfg.markers.tag_mm)
        dpi = int(st.printables_mask_dpi)

        def run(ctx: Any) -> dict:
            def progress(f: float, msg: str) -> None:
                ctx.check()
                ctx.progress(f, msg, force=True)

            try:
                manifest = printables.generate(out, spec, paper, tag_mm=tag_mm, mask_dpi=dpi, progress=progress)
            except OSError as e:
                raise ApiError(500, f"the printables could not be written to {out}: {e}",
                               "check that the folder is writable and the disk is not full") from e
            self.store.set_printables(manifest)
            listing = printables.listing(manifest, out, paper)
            return {**listing, "message": f"Printables ready: {len(listing['files'])} files in {out}"}

        return st.jobs.submit("printables", run, message="Preparing the printables…")

    def printable_file(self, name: str) -> tuple[Path, str]:
        from ohs.server import printables

        return printables.file_path(self.store.printables(), name)

    def open_printables(self) -> dict:
        from ohs.server import printables

        state = self.printables_state()
        folder = Path(state["dir"])
        return {"opened": bool(printables.open_folder(folder)), "dir": str(folder)}

    # ------------------------------------------------------------------ devices probe
    def devices_probe(self) -> dict:
        cfg = self.st.cfg
        dev = self.st.devices
        in_use = dev.camera_index()
        backend = str(cfg.camera.backend)
        cams: list[dict] = []
        try:
            if in_use is None:
                from ohs.devices.camera import list_cameras

                found = list_cameras(MAX_CAMERA_INDEX, backend)
            else:
                found = probe_cameras([i for i in range(MAX_CAMERA_INDEX) if i != in_use], backend)
        except Exception as e:
            log.warning("probing the cameras failed: %s", e)
            found = []
        if in_use is not None:
            info = dev.camera_info()
            found = [e for e in found if int(e.get("index", -1)) != in_use]
            found.append({"index": in_use, "width": info.get("width", 0), "height": info.get("height", 0),
                          "fps": info.get("fps", 0.0), "fourcc": info.get("fourcc", ""), "in_use": True})
        for e in sorted(found, key=lambda x: int(x.get("index", 0))):
            w, h = int(e.get("width") or 0), int(e.get("height") or 0)
            idx = int(e.get("index", 0))
            cams.append({"index": idx, "width": w, "height": h, "fps": float(e.get("fps") or 0.0),
                         "fourcc": str(e.get("fourcc") or ""), "name": e.get("name"),
                         "stereo_like": bool(h > 0 and w / h >= 2.5), "configured": idx == int(cfg.camera.index),
                         "in_use": bool(e.get("in_use", False))})
        port = None
        hello_port = dev.pico_hello().get("port")
        try:
            from ohs.devices.pico import find_pico_port

            port = find_pico_port()
        except Exception as e:
            log.warning("looking for the Pico failed: %s", e)
        port = port or hello_port
        notes = []
        if not cams:
            notes.append("No camera found: plug the scanner's USB cable into a USB 3 port on the PC and press Refresh. "
                         "No hardware yet? Connect the simulator.")
        elif not any(c["stereo_like"] for c in cams):
            notes.append("None of the cameras looks like the side-by-side stereo camera (2560x800): check that the "
                         "scanner is plugged in, then pick it by its index.")
        if port is None:
            notes.append("The Pico was not found: connect its data USB cable (some cables only charge) and flash the "
                         "firmware (firmware/README.md).")
        return {"cameras": cams, "pico_port": port, "configured_index": int(cfg.camera.index),
                "note": " ".join(notes) or None}


def probe_cameras(indices: list[int], backend: str = "auto") -> list[dict]:
    """``list_cameras`` for the given indices only (the one in use is never opened twice)."""
    import cv2

    from ohs.devices import camera as cam

    api = cam._backend_candidates(backend)[0]
    found: list[dict] = []
    with cam._quiet_opencv():
        for i in indices:
            cap = cv2.VideoCapture(int(i), api)
            try:
                if not cap.isOpened():
                    continue
                entry: dict[str, Any] = {
                    "index": int(i), "backend": cam._backend_name(cap, api),
                    "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    "fps": float(cap.get(cv2.CAP_PROP_FPS)), "fourcc": cam._decode_fourcc(cap.get(cv2.CAP_PROP_FOURCC)),
                }
                name = cam._v4l2_device_name(int(i))
                if name:
                    entry["name"] = name
                found.append(entry)
            finally:
                cap.release()
    return found
