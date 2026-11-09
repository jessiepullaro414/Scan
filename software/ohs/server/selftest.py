"""The setup wizard's self-test job (``POST /api/setup/selftest``, job kind ``selftest``).

Two paths, chosen by the device source:

* **camera** (or nothing connected: the configured camera and Pico): the device is marked busy
  ("Running the self-test…") and the camera disconnected, then
  :func:`ohs.app.selftest.run_selftest` opens the camera and the Pico itself
  (``camera_factory`` / ``pico_factory``, as the device panel would). Its ``echo`` lines become
  the job's ``message``: the user prompts are rewritten as plain instructions ("Press and release
  the trigger now", "Point the scanner at a wall 20–40 cm away"). ``run_selftest`` uses one
  duration for the measurement and the trigger window, :data:`CAMERA_SECONDS` (8 s), so the
  trigger step gets about 8 s. The camera is **always** reconnected afterwards when it was
  connected before, even when the test failed.
* **sim / replay**: checks on the live stream for :data:`STREAM_SECONDS` through a frame tap
  (:meth:`~ohs.server.devices.DeviceManager.add_tap`): frame rate, image statistics, the IMU
  rate and ``|accel|`` from the feed. Pico, clock sync, emitters and trigger are ``SKIP`` with
  the reason.

Every check gets a plain ``fix`` (what to do) from :func:`fix_for`, and a readable name. The
result is a ``SelfTestResult`` (APP_API.md) that the job returns and the setup state records
for the target.

Threads: the job runs in the job pool; a small ticker thread moves the progress bar along during
the long steps (the engine reports nothing while it measures). The device lock rules of
:mod:`ohs.server.studio` apply: busy / disconnect / reconnect happen under ``Studio._lock``.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable

import numpy as np

from ohs.server.protocol import ApiError, now_iso

__all__ = ["CAMERA_SECONDS", "STREAM_SECONDS", "fix_for", "plain_prompt", "label", "run_camera_selftest",
           "run_stream_selftest", "result_dict", "overall"]

log = logging.getLogger(__name__)

CAMERA_SECONDS = 8.0
STREAM_SECONDS = 3.0
_LABELS = {
    "camera": "Camera", "camera fps": "Camera frame rate", "camera image": "Camera image", "pico": "Pico",
    "imu": "IMU", "imu rate": "IMU rate", "imu values": "IMU values", "clock sync": "Clock sync",
    "vertical line laser": "Vertical laser", "horizontal line laser": "Horizontal laser",
    "dot projector": "Dot projector", "emitters": "Emitters", "trigger": "Trigger",
}
_RESULT_LINE = re.compile(r"^\s{2}(PASS|WARN|FAIL|SKIP|INFO)\s+([^:]+):\s?(.*)$")


def label(name: str) -> str:
    return _LABELS.get(name, name[:1].upper() + name[1:])


def overall(checks: list[dict]) -> str:
    st = {c["status"] for c in checks}
    return "FAIL" if "FAIL" in st else "WARN" if "WARN" in st else "PASS"


def fix_for(name: str, status: str, detail: str = "") -> str | None:
    """What to do about a check, in plain words (None when there is nothing to do)."""
    if status in ("PASS", "SKIP"):
        return None
    d = detail.lower()
    if name == "camera":
        if status == "FAIL":
            return ("Plug the scanner's USB cable straight into a USB 3 port, close other programs that use the camera, "
                    "and pick the stereo camera in step 1 (Connect).")
        return ("The camera runs another mode than the settings ask for: use the 2560x800 MJPG mode, or pick the "
                "stereo camera in step 1 (Connect).")
    if name == "camera fps":
        if status == "FAIL":
            return "The camera stopped sending pictures: check the USB cable and run the self-test again."
        return ("Use a USB 3 port directly on the PC (no hub), keep the MJPG format, and shorten the exposure if the "
                "room is dark.")
    if name == "camera image":
        if "black" in d:
            return "Take off the lens caps and point the scanner at a lit scene; raise the exposure if it stays dark."
        if "saturated" in d:
            return "Lower the exposure, or point the scanner away from bright lights and windows."
        if "identical" in d:
            return "This camera sends the same picture twice: pick the side-by-side stereo camera in step 1 (Connect)."
        return "Check the camera picture in the Scan page preview, then run the self-test again."
    if name == "pico":
        if status == "FAIL":
            return ("Connect the Pico with a data USB cable (some cables only charge) and flash the OpenHandScan firmware "
                    "(firmware/README.md); if it still is not found, set its serial port in the settings.")
        return "Flash the OpenHandScan firmware onto the Pico (firmware/README.md)."
    if name == "imu":
        if "wiring" in d or "no imu" in d:
            return "Check the IMU wiring to the Pico: SDA to GP4, SCL to GP5, plus 3V3 and GND."
        return "Unplug the Pico, plug it in again and run the self-test again; check the IMU wiring if it keeps failing."
    if name == "imu rate":
        return "Plug the Pico straight into the PC (no hub) and close programs that keep the USB bus busy."
    if name == "imu values":
        return "Keep the scanner still on the table while it measures, then run the self-test again."
    if name == "clock sync":
        return "Plug the Pico straight into the PC (no hub) and close programs that keep the USB bus busy."
    if name in ("vertical line laser", "horizontal line laser", "dot projector"):
        if status == "FAIL":
            return "Check the Pico's USB cable and the emitter wiring (firmware/README.md), then run the self-test again."
        return ("If you did not see it light up: check the laser key switch and the wiring, and aim at a light wall "
                "20–40 cm away. Never look into the beams.")
    if name == "trigger":
        return ("Run the self-test again and press the trigger when asked; if nothing registers, check the button "
                "wiring (GP15 to GND).")
    if status == "INFO":
        return None
    return "Run the self-test again; if it keeps failing, see the log (ohs -v app)."


def plain_prompt(line: str) -> str | None:
    """A ``run_selftest`` echo line as an instruction for the user (None: not worth showing)."""
    s = line.strip()
    if not s:
        return None
    if s.startswith("Opening camera"):
        return "Opening the camera…"
    if s.startswith("Connecting to the Pico"):
        return "Connecting to the Pico…"
    m = re.match(r"Measuring for ([\d.]+) s", s)
    if m:
        return f"Keep the scanner still for {float(m.group(1)):g} s while the camera and the IMU are measured"
    if s.startswith("Switching each emitter on"):
        return ("Point the scanner at a light wall 20–40 cm away: each laser and the dot projector switch on for 1 s "
                "in turn. Never look into the lasers.")
    if s.startswith("Press and release the trigger"):
        return "Press and release the trigger now"
    m = _RESULT_LINE.match(line)
    if m:
        word = {"PASS": "passed", "WARN": "warning", "FAIL": "failed", "SKIP": "skipped", "INFO": "done"}[m.group(1)]
        return f"{label(m.group(2).strip())}: {word}"
    return None


def _check(name: str, status: str, detail: str) -> dict:
    return {"name": label(name), "status": status, "detail": detail, "fix": fix_for(name, status, detail), "id": name}


def result_dict(checks: list[dict], source: str) -> dict:
    """``SelfTestResult`` (plus ``message``, the job's final message)."""
    res = overall(checks)
    n_warn = sum(c["status"] == "WARN" for c in checks)
    n_fail = sum(c["status"] == "FAIL" for c in checks)
    if res == "PASS":
        msg = "Self-test passed"
    elif res == "FAIL":
        msg = f"Self-test failed: {n_fail} check{'s' if n_fail != 1 else ''} failed"
    else:
        msg = f"Self-test passed with {n_warn} warning{'s' if n_warn != 1 else ''}"
    return {"overall": res, "when": now_iso(), "source": source, "checks": checks, "message": msg}


class _Ticker:
    """Moves the progress bar through the expected duration of the running step."""

    def __init__(self, progress: Callable[[float], None]) -> None:
        self._progress = progress
        self._span: tuple[float, float, float, float] | None = None  # (t0, duration, p0, p1)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="ohs-selftest-ticker", daemon=True)
        self._thread.start()

    def phase(self, p0: float, p1: float, duration: float) -> None:
        with self._lock:
            self._span = (time.monotonic(), max(0.1, duration), p0, p1)
        self._progress(p0)

    def _run(self) -> None:
        while not self._stop.wait(0.25):
            with self._lock:
                span = self._span
            if span is None:
                continue
            t0, dur, p0, p1 = span
            f = min(1.0, (time.monotonic() - t0) / dur)
            self._progress(p0 + (p1 - p0) * f)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(1.0)


# --------------------------------------------------------------------------- camera path
def _phases(seconds: float) -> tuple:
    """echo prefix -> (progress from, to, expected seconds)"""
    return (("Opening camera", (0.02, 0.08, 3.0)),
            ("Connecting to the Pico", (0.08, 0.12, 2.0)),
            ("Measuring for", (0.12, 0.5, seconds)),
            ("Switching each emitter on", (0.5, 0.7, 4.5)),
            ("Press and release the trigger", (0.7, 0.98, seconds)))


def run_camera_selftest(cfg: Any, camera_factory: Callable[[Any], Any], pico_factory: Callable[[Any], Any],
                        message: Callable[[str], None], progress: Callable[[float], None],
                        seconds: float = CAMERA_SECONDS) -> list[dict]:
    """``run_selftest`` on the configured camera and Pico; factory failures become FAIL checks."""
    from ohs.app.selftest import run_selftest

    phases = _phases(float(seconds))
    camera = pico = None
    cam_error = pico_error = None
    try:
        camera = camera_factory(cfg)
    except Exception as e:
        cam_error = str(e)
    try:
        pico = pico_factory(cfg)
    except Exception as e:
        pico_error = str(e)
    ticker = _Ticker(progress)

    def echo(line: str) -> None:
        for prefix, (p0, p1, dur) in phases:
            if line.strip().startswith(prefix):
                ticker.phase(p0, p1, dur)
                break
        text = plain_prompt(line)
        if text:
            message(text)
        log.debug("selftest: %s", line)

    try:
        checks = run_selftest(cfg, camera=camera, pico=pico, seconds=seconds, use_camera=camera is not None,
                              use_pico=pico is not None, echo=echo)
    finally:
        ticker.close()
    out = []
    for c in checks:
        status, detail = c.status, c.detail
        if c.name == "camera" and status == "SKIP" and cam_error is not None:
            status, detail = "FAIL", f"the camera could not be set up: {cam_error}"
        elif c.name == "pico" and status == "SKIP" and pico_error is not None:
            status, detail = "FAIL", f"no Pico: {pico_error}"
        out.append(_check(c.name, status, detail))
    return out


# --------------------------------------------------------------------------- stream path (sim / replay)
class _StreamTap:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.frames: list[tuple[int, float]] = []
        self.stats: list[tuple[float, float, float]] = []
        self.size: tuple[int, int] | None = None
        self.t_dev: list[float] = []
        self.accel: list[np.ndarray] = []

    def on_frame(self, f: Any) -> None:
        with self.lock:
            self.frames.append((int(f.index), float(f.t_host)))
            self.size = tuple(int(v) for v in f.image_size)  # type: ignore[assignment]
            if len(self.frames) % 5 == 1:
                left = np.asarray(f.left)
                self.stats.append((float(left.mean()), float((left >= 250).mean()),
                                   float(np.abs(left.astype(np.int16) - np.asarray(f.right).astype(np.int16)).mean())))

    def on_imu(self, samples: list) -> None:
        with self.lock:
            for s in samples:
                self.t_dev.append(float(s.t_device))
                self.accel.append(np.asarray(s.accel, dtype=np.float64).reshape(3))


def run_stream_selftest(devices: Any, source: str, message: Callable[[str], None], progress: Callable[[float], None],
                        cancelled: Callable[[], bool], seconds: float = STREAM_SECONDS) -> list[dict]:
    """Checks on the live frames and IMU of the simulator or a replay."""
    what = "the simulator" if source == "sim" else "the replayed recording"
    tap = _StreamTap()
    message(f"Measuring {what} for {seconds:g} s…")
    devices.add_tap(tap)
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < seconds:
            if cancelled():
                raise InterruptedError("self-test cancelled")
            if not devices.connected:
                raise ApiError(409, "the device disconnected during the self-test", "connect it again and run the self-test")
            progress(0.05 + 0.85 * (time.monotonic() - t0) / seconds)
            time.sleep(0.1)
    finally:
        devices.remove_tap(tap)
    info = devices.camera_info()
    with tap.lock:
        frames, stats, size = list(tap.frames), list(tap.stats), tap.size
        t_dev, accel = np.asarray(tap.t_dev), (np.vstack(tap.accel) if tap.accel else np.zeros((0, 3)))
    checks: list[dict] = []
    if size is None or len(frames) < 2:
        checks.append(_check("camera", "FAIL", f"{what} delivered fewer than 2 frames in {seconds:g} s"))
    else:
        w, h = size
        checks.append(_check("camera", "PASS", f"{2 * w}x{h} (2 x {w}x{h}) from {what}"))
        span = frames[-1][1] - frames[0][1]
        fps = (len(frames) - 1) / span if span > 0 else 0.0
        want = float(info.get("fps") or 0.0)
        detail = f"{fps:.1f} fps ({len(frames)} frames in {span:.1f} s" + (f"; {what} runs at {want:.0f} fps)" if want else ")")
        if want and fps < 0.9 * want:
            checks.append(_check("camera fps", "WARN", detail + ": the PC is busy"))
        else:
            checks.append(_check("camera fps", "PASS", detail))
        mean = float(np.mean([s[0] for s in stats])) if stats else 0.0
        sat = float(np.mean([s[1] for s in stats])) if stats else 0.0
        diff = float(np.mean([s[2] for s in stats])) if stats else 0.0
        if mean < 3.0:
            checks.append(_check("camera image", "WARN", f"the image is black (mean {mean:.1f} DN)"))
        elif sat > 0.2:
            checks.append(_check("camera image", "WARN", f"{sat:.0%} of the pixels are saturated"))
        elif diff < 0.5:
            checks.append(_check("camera image", "WARN", "left and right images are identical"))
        else:
            checks.append(_check("camera image", "PASS", f"mean {mean:.0f} DN, {sat:.1%} saturated"))
    if not devices.imu_available():
        checks.append(_check("imu", "SKIP", f"{what} has no IMU data"))
    elif len(t_dev) < 10:
        checks.append(_check("imu", "FAIL", f"only {len(t_dev)} IMU samples in {seconds:g} s"))
    else:
        t = np.sort(t_dev)
        span = float(t[-1] - t[0])
        rate = (len(t) - 1) / span if span > 0 else 0.0
        dt = np.diff(t)
        nominal = 1.0 / float(np.median(dt)) if len(dt) and np.median(dt) > 0 else rate
        gaps = int((dt > 1.5 / nominal).sum()) if nominal > 0 else 0
        detail = f"{rate:.0f} Hz, {len(t)} samples, {gaps} gap{'s' if gaps != 1 else ''}"
        checks.append(_check("imu rate", "PASS" if rate >= 0.95 * nominal else "WARN", detail))
        g = float(np.linalg.norm(accel, axis=1).mean())
        detail = f"|accel| {g:.2f} m/s^2"
        checks.append(_check("imu values", "PASS" if 9.3 <= g <= 10.3 else "WARN",
                             detail if 9.3 <= g <= 10.3 else detail + " (expected about 9.81 m/s^2)"))
    no_pico = "the simulator has no Pico" if source == "sim" else "a replayed recording has no Pico"
    checks.append(_check("pico", "SKIP", no_pico))
    checks.append(_check("clock sync", "SKIP", f"{no_pico}: there is no clock to synchronise"))
    checks.append(_check("emitters", "SKIP", "the simulator's lasers and dot projector are virtual" if source == "sim"
                         else "a recording cannot switch emitters"))
    checks.append(_check("trigger", "SKIP", f"{no_pico}: use the buttons in the app"))
    progress(0.98)
    return checks
