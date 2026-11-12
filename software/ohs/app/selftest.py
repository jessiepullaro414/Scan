"""Hardware self-test (``ohs selftest``): camera, Pico, IMU, clock sync, emitters and trigger.

Checks, in order (each gives PASS / WARN / FAIL, SKIP when not run, INFO for notes):

1. **camera**: opens ``StereoCamera(cfg.camera)``, then reads frames for ``seconds``. It reports
   the delivered resolution against the configured one, the measured frame rate (from the frame
   timestamps and the grab counter), black, saturated and "left == right" images.
2. **pico**: ``Pico.open()`` (HELLO, emitters and IMU off, clock sync). FAIL if it does not
   answer or the firmware is not ``ohs-fw``.
3. **imu**: streams at ``pico.imu_rate_hz`` while the camera is measured. It reports the rate
   measured from the device timestamps, gaps, ``|accel|`` (about 9.8 m/s^2 at rest) and gyro noise.
4. **clock sync**: a fresh ``sync_clock(16)``. PASS when the best round trip is under 2 ms
   (timestamp error under 1 ms), WARN up to 10 ms.
5. **emitters**: vertical laser, horizontal laser and dot projector each on for
   ``emitter_seconds`` (1 s), one after the other. The user confirms visually. When the camera
   is open, it also compares an image taken with the emitter on against one taken with it off.
6. **trigger**: watches the button for ``seconds``; PASS when a press and a release arrive.

The overall result is FAIL if any check failed, else WARN if any warned, else PASS.
``run_selftest`` never raises for device problems; they become FAIL lines.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from ohs.devices.camera import StereoCamera
from ohs.devices.pico import Pico

__all__ = ["Check", "run_selftest", "overall", "format_summary", "image_check", "gravity_ok"]

log = logging.getLogger(__name__)

STATUSES = ("PASS", "WARN", "FAIL", "SKIP", "INFO")
G_REST = (9.3, 10.3)  # |accel| that passes as gravity at rest (m/s^2)
_IMAGE_HINTS = {"black": ": lens cap, or exposure too short?", "saturated": ": shorten the exposure",
                "identical": ": is this the stereo camera?"}


def image_check(stats: list, hints: dict[str, str] | None = None) -> tuple[str, str]:
    """The "camera image" check of the hardware test and of the app's stream test: per-frame
    ``(mean DN, saturated fraction, mean |left - right| DN)`` -> black, saturated, left == right,
    else PASS. ``hints`` adds the caller's advice to a warning (keys "black", "saturated", "identical")."""
    h = hints or {}
    mean = float(np.mean([s[0] for s in stats])) if stats else 0.0
    sat = float(np.mean([s[1] for s in stats])) if stats else 0.0
    diff = float(np.mean([s[2] for s in stats])) if stats else 0.0
    if mean < 3.0:
        return "WARN", f"the image is black (mean {mean:.1f} DN)" + h.get("black", "")
    if sat > 0.2:
        return "WARN", f"{sat:.0%} of the pixels are saturated" + h.get("saturated", "")
    if diff < 0.5:
        return "WARN", "left and right images are identical" + h.get("identical", "")
    return "PASS", f"mean {mean:.0f} DN, {sat:.1%} saturated"


def gravity_ok(g: float) -> bool:
    """Whether ``|accel|`` ``g`` (m/s^2) is gravity. Each test computes ``g`` its own way: the hardware
    test as the norm of the mean (the scanner is held still, so a turn shows as a smaller value), the
    stream test as the mean of the norms (a replayed scan moves)."""
    return G_REST[0] <= g <= G_REST[1]
EMITTERS = (("vertical line laser", 0), ("horizontal line laser", 1), ("dot projector", 2))
CHANGED_DN = 25  # an emitter "shows" in the camera if this many DN brighter ...
CHANGED_FRACTION = 2e-4  # ... in at least this fraction of pixels (a thin line is ~0.3 %)


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


def overall(checks: list[Check]) -> str:
    st = {c.status for c in checks}
    return "FAIL" if "FAIL" in st else "WARN" if "WARN" in st else "PASS"


def format_summary(checks: list[Check]) -> str:
    width = max([len(c.name) for c in checks] + [8])
    lines = ["Self-test summary:"]
    lines += [f"  {c.status:<4}  {c.name:<{width}}  {c.detail}" for c in checks]
    res = overall(checks)
    n_warn = sum(c.status == "WARN" for c in checks)
    n_fail = sum(c.status == "FAIL" for c in checks)
    lines.append(f"Overall: {res}" + (f" ({n_fail} failed, {n_warn} warnings)" if res != "PASS" else ""))
    return "\n".join(lines)


class _SelfTest:
    def __init__(self, cfg: Any, seconds: float, emitter_seconds: float, camera: Any, pico: Any, emitters: bool,
                 echo: Callable[[str], None]) -> None:
        self.cfg = cfg
        self.seconds = max(0.2, float(seconds))
        self.emitter_seconds = max(0.1, float(emitter_seconds))
        self.camera = camera
        self.pico = pico
        self.test_emitters = emitters
        self.echo = echo
        self.checks: list[Check] = []
        self.cam_ok = False
        self.pico_ok = False
        self.has_imu = False

    def add(self, name: str, status: str, detail: str = "") -> None:
        c = Check(name, status, detail)
        self.checks.append(c)
        self.echo(f"  {status:<4}  {name}: {detail}")

    # ------------------------------------------------------------------ steps
    def open_camera(self) -> None:
        if self.camera is None:
            self.add("camera", "SKIP", "not tested (--no-camera)")
            return
        p = self.cfg.camera
        self.echo(f"Opening camera {p.index} (backend {p.backend}, {p.width}x{p.height} @ {p.fps:g} fps {p.fourcc})...")
        try:
            self.camera.open()
            self.cam_ok = True
        except Exception as e:
            self.add("camera", "FAIL", f"could not open camera {p.index}: {e}. Run `ohs devices` and set camera.index")
            self.camera = None

    def open_pico(self) -> None:
        if self.pico is None:
            self.add("pico", "SKIP", "not tested (--no-pico)")
            return
        self.echo("Connecting to the Pico...")
        try:
            hello = self.pico.open()
        except Exception as e:
            self.add("pico", "FAIL", f"{e} (firmware flashed? see firmware/README.md; port in pico.port)")
            self.pico = None
            return
        self.pico_ok = True
        fw, ver, imu = hello.get("firmware", "?"), hello.get("version", "?"), str(hello.get("imu", "NONE"))
        self.has_imu = imu.upper() != "NONE"
        status = "PASS" if fw == "ohs-fw" else "WARN"
        self.add("pico", status, f"HELLO {fw} {ver} on {hello.get('port')}, imu={imu}")
        if not self.has_imu:
            self.add("imu", "FAIL", "no IMU found by the firmware (check the LSM6DSOX wiring: SDA GP4, SCL GP5, 3V3, GND)")

    def measure(self) -> None:
        """Camera frames and the IMU stream, measured together for ``seconds``."""
        frames: list[tuple[int, float]] = []
        stats: list[tuple[float, float, float]] = []
        imu: list = []
        if self.pico_ok and self.has_imu:
            try:
                self.pico.start_imu()
            except Exception as e:
                self.add("imu", "FAIL", f"could not start the IMU stream: {e}")
                self.has_imu = False
        if not self.cam_ok and not (self.pico_ok and self.has_imu):
            return
        self.echo(f"Measuring for {self.seconds:g} s (keep the scanner still)...")
        t_end = time.perf_counter() + self.seconds
        last = None
        while time.perf_counter() < t_end:
            if self.cam_ok:
                try:
                    f = self.camera.read(timeout=1.0)
                except TimeoutError:
                    continue
                except Exception as e:
                    self.add("camera", "FAIL", f"camera stopped delivering frames: {e}")
                    self.cam_ok = False
                    continue
                frames.append((f.index, f.t_host))
                last = f
                if len(frames) % 5 == 1:
                    left = f.left
                    stats.append((float(left.mean()), float((left >= 250).mean()),
                                  float(np.abs(left.astype(np.int16) - f.right.astype(np.int16)).mean())))
            else:
                time.sleep(0.01)
            if self.pico_ok and self.has_imu:
                imu += self.pico.read_imu()
        if self.pico_ok and self.has_imu:
            try:
                self.pico.stop_imu()
            except Exception as e:
                log.warning("stopping the IMU failed: %s", e)
            imu += self.pico.read_imu()
        if self.cam_ok:
            self._camera_checks(frames, stats, last)
        if self.pico_ok and self.has_imu:
            self._imu_checks(imu)

    def _camera_checks(self, frames: list, stats: list, last: Any) -> None:
        p = self.cfg.camera
        info = dict(getattr(self.camera, "info", {}) or {})
        if last is None or len(frames) < 2:
            self.add("camera", "FAIL", "opened, but delivered fewer than 2 frames")
            return
        w, h = last.image_size
        mode = f"{2 * w}x{h} (2 x {w}x{h}) {info.get('fourcc', '')} via {info.get('backend', '?')}"
        if (2 * w, h) == (p.width, p.height):
            self.add("camera", "PASS", mode)
        else:
            self.add("camera", "WARN", f"{mode}; the config asks for {p.width}x{p.height} (camera.width/height, fourcc MJPG?)")
        (i0, t0), (i1, t1) = frames[0], frames[-1]
        span = t1 - t0
        read_fps = (len(frames) - 1) / span if span > 0 else 0.0
        grab_fps = (i1 - i0) / span if span > 0 else 0.0
        fps = max(read_fps, grab_fps)
        detail = f"{fps:.1f} fps (requested {p.fps:g}; {len(frames)} frames read in {span:.2f} s)"
        if fps >= 0.9 * p.fps:
            self.add("camera fps", "PASS", detail)
        else:
            self.add("camera fps", "WARN", detail + ". USB 2 port, exposure too long, or no MJPG?")
        self.add("camera image", *image_check(stats, _IMAGE_HINTS))

    def _imu_checks(self, samples: list) -> None:
        rate = int(self.cfg.pico.imu_rate_hz)
        if len(samples) < 10:
            self.add("imu", "FAIL", f"only {len(samples)} IMU samples in {self.seconds:g} s")
            return
        td = np.array([s.t_device for s in samples])
        span = td[-1] - td[0]
        measured = (len(td) - 1) / span if span > 0 else 0.0
        gaps = int(self.pico.stats.get("imu_gaps", 0))
        detail = f"{measured:.0f} Hz (requested {rate}), {len(samples)} samples, {gaps} gaps"
        self.add("imu rate", "PASS" if abs(measured - rate) <= 0.05 * rate else "WARN", detail)
        acc = np.array([s.accel for s in samples])
        gyr = np.array([s.gyro for s in samples])
        g = float(np.linalg.norm(acc.mean(axis=0)))  # norm of the mean: see gravity_ok
        noise = float(np.degrees(gyr.std(axis=0).max()))
        bias = float(np.degrees(np.abs(gyr.mean(axis=0)).max()))
        detail = f"|accel| {g:.2f} m/s^2, gyro noise {noise:.2f} deg/s, offset {bias:.2f} deg/s"
        if gravity_ok(g):
            self.add("imu values", "PASS", detail)
        else:
            self.add("imu values", "WARN", detail + " (expected ~9.81 m/s^2 at rest: was the scanner moving?)")

    def clock(self) -> None:
        if not self.pico_ok:
            return
        try:
            offset, rtt = self.pico.sync_clock(16)
        except Exception as e:
            self.add("clock sync", "FAIL", str(e))
            return
        model = self.pico.clock_model
        drift = "" if model is None else f", drift {(model[0] - 1.0) * 1e6:+.0f} ppm"
        detail = f"best round trip {rtt * 1e3:.2f} ms (timestamp error < {rtt * 5e2:.2f} ms), offset {offset:.3f} s{drift}"
        self.add("clock sync", "PASS" if rtt < 2e-3 else "WARN" if rtt < 10e-3 else "FAIL", detail)

    def _grab_latest(self, settle: float) -> np.ndarray | None:
        """Newest left image after ``settle`` seconds (None without a camera)."""
        if not self.cam_ok:
            time.sleep(settle)
            return None
        t_end = time.perf_counter() + settle
        img = None
        while True:
            try:
                img = self.camera.read(timeout=1.0).left
            except Exception:
                return img
            if time.perf_counter() >= t_end:
                return img

    def emitters(self) -> None:
        if not self.pico_ok:
            return
        if not self.test_emitters:
            self.add("emitters", "SKIP", "not tested (--no-emitters)")
            return
        scan = self.cfg.emitters.levels("scan")
        self.echo("Switching each emitter on for "
                  f"{self.emitter_seconds:g} s. Point the scanner at a wall 20-40 cm away. "
                  "The lasers are Class 3R: never look into them.")
        for name, i in EMITTERS:
            level = scan[i] if scan[i] > 0 else 100.0
            levels = [0.0, 0.0, 0.0]
            levels[i] = level
            off_img = self._grab_latest(0.15)
            try:
                self.pico.set_emitters(*levels)
            except Exception as e:
                self.add(name, "FAIL", f"could not switch it on: {e}")
                continue
            on_img = self._grab_latest(self.emitter_seconds)
            try:
                self.pico.all_off()
            except Exception as e:
                self.add(name, "FAIL", f"could not switch it off: {e}")
                continue
            note = f"on at {level:.0f}% for {self.emitter_seconds:g} s: confirm you saw it"
            if off_img is None or on_img is None or off_img.shape != on_img.shape:
                self.add(name, "INFO", note)
                continue
            changed = float(((on_img.astype(np.int16) - off_img.astype(np.int16)) > CHANGED_DN).mean())
            if changed >= CHANGED_FRACTION:
                self.add(name, "PASS", f"{note} (the camera saw {changed:.2%} of the pixels light up)")
            else:
                self.add(name, "WARN", f"{note}; the camera saw no change: laser kill switch on? wiring? "
                                       "aimed at a surface 20-40 cm away?")

    def trigger(self) -> None:
        if not self.pico_ok:
            return
        self.pico.read_events()  # discard older presses
        self.echo(f"Press and release the trigger now (within {self.seconds:g} s)...")
        seen: list[str] = []
        t_end = time.perf_counter() + self.seconds
        while time.perf_counter() < t_end:
            seen += [e.value for e in self.pico.read_events() if e.kind == "button"]
            if "down" in seen and "up" in seen[seen.index("down"):]:
                break
            time.sleep(0.01)
        if "down" in seen and "up" in seen[seen.index("down"):]:
            self.add("trigger", "PASS", "press and release seen")
        elif seen:
            self.add("trigger", "WARN", f"only saw: {', '.join(seen)}")
        else:
            self.add("trigger", "WARN", f"no press within {self.seconds:g} s (run again and press it; wiring GP15 to GND)")

    def close(self) -> None:
        if self.pico is not None:
            try:
                self.pico.close()  # emitters off first
            except Exception as e:
                log.warning("closing the Pico failed: %s", e)
        if self.camera is not None:
            try:
                self.camera.close()
            except Exception as e:
                log.warning("closing the camera failed: %s", e)


def run_selftest(cfg: Any, *, seconds: float = 3.0, emitter_seconds: float = 1.0, camera: Any = None, pico: Any = None,
                 use_camera: bool = True, use_pico: bool = True, emitters: bool = True,
                 echo: Callable[[str], None] = print) -> list[Check]:
    """Run every check and print the summary. ``camera`` / ``pico`` default to the configured devices."""
    if use_camera and camera is None:
        camera = StereoCamera(cfg.camera)
    if use_pico and pico is None:
        pico = Pico(cfg.pico)
    t = _SelfTest(cfg, seconds, emitter_seconds, camera if use_camera else None, pico if use_pico else None, emitters, echo)
    try:
        t.open_camera()
        t.open_pico()
        t.measure()
        t.clock()
        t.emitters()
        t.trigger()
    finally:
        t.close()
    echo("")
    echo(format_summary(t.checks))
    return t.checks
