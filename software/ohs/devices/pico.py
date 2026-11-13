"""Host client for the OpenHandScan Pico firmware (serial protocol: ARCHITECTURE.md section 5A).

The firmware (``firmware/main.py``) drives the emitters, debounces the trigger and streams the
IMU. This module talks to it over USB CDC with ASCII lines.

Threads
-------
* **reader** (``pico-reader``) parses every incoming line: replies are handed to the waiting
  command, ``I`` lines go to the IMU queue, ``B`` (trigger) and ``C`` (IMU chip temperature,
  firmware 1.1+) lines to the event queue, ``E``/``L`` are logged.
  The ``OK IMU`` scale factors are taken by the reader itself, so samples that follow the reply
  immediately are already converted correctly.
* **maintenance** (``pico-maint``) sends ``HB`` every ``heartbeat_s`` while any emitter is
  non-zero (the firmware turns emitters off 1.5 s after the last host line) and re-syncs the clock
  every ``resync_s``.

Commands are request/reply with one outstanding command at a time. A firmware ``E`` line that
names the pending command (``E EMIT bad args``, ``E unknown FOO``) raises :class:`PicoError`.
Timeouts raise ``TimeoutError``. A lost connection (read/write error, unplugged cable) is logged,
marks the client disconnected and makes every later command raise ``ConnectionError``. A waiting
command is woken immediately, so nothing hangs.

Clock model
-----------
``sync_clock`` sends ``TIME`` n times and keeps the reply with the smallest round-trip time
(RTT). Its device time is paired with the midpoint of the request/reply host times, so the error
is at most RTT/2. Sync points feed a linear model ``t_host = a * t_dev + b`` (weighted least
squares over the last 64 points, weight 1/sigma^2 with sigma = RTT/(2*sqrt(3)), plus a Gaussian
prior ``a ~ N(1, drift_prior_ppm)``). That follows the crystal drift between host and Pico (tens
of ppm) without letting a short baseline produce a wild slope. A point that is off by more than
``max(2 ms, 2*RTT)``, or a device clock going backwards (firmware reboot), restarts the model.
IMU samples and button events are converted when they are *drained* (``read_imu``/``read_events``),
so they use the newest model.

Transport
---------
``Pico(params, transport=...)`` accepts any serial-like object with ``read(n) -> bytes`` (blocking
for at most its own timeout), ``write(bytes)`` and ``close()``; ``in_waiting`` and
``reset_input_buffer()`` are used when present. Without a transport, :meth:`Pico.open` opens a
pyserial port (``params.port`` or :func:`find_pico_port`) and closes it again in :meth:`Pico.close`.
An injected transport is never closed by ``Pico``: the caller owns it.

Deviations from ARCHITECTURE.md: none in the contract API. Extras are ``PicoParams.resync_s``,
``sync_samples``, ``timeout_s`` and ``drift_prior_ppm``; ``Pico.status()``,
``device_to_host()``, and the ``hello``, ``connected``, ``emitters``, ``imu_scales``,
``clock_model``, ``temperature_c`` and ``stats`` properties; ``PicoError``; ``read_events()``
also returns ``temperature`` events. The firmware already sends unwrapped
64-bit ``t_us`` values (see firmware/main.py), so the host does not unwrap them.
"""
from __future__ import annotations

import collections
import logging
import math
import queue
import threading
import time
from typing import Any

import numpy as np

from ohs.devices.params import PicoParams
from ohs.types import Event, ImuSample

__all__ = ["PicoParams", "Pico", "PicoError", "find_pico_port"]

log = logging.getLogger(__name__)

RPI_USB_VID = 0x2E8A
ESPRESSIF_USB_VID = 0x303A  # the ESP32 variant (same firmware, native USB)
MICROPYTHON_USB_PID = 0x0005
_IMU_QUEUE_MAX = 1 << 16  # ~80 s at 833 Hz; the oldest samples are dropped (and counted) beyond
_EVENT_QUEUE_MAX = 1024
_STAT_KEYS = ("lines", "imu", "imu_gaps", "imu_dropped", "imu_unscaled", "events", "temps", "errors", "unparsed",
              "syncs")


class PicoError(RuntimeError):
    """The firmware answered a command with an ``E ...`` error line."""


# --------------------------------------------------------------------------- clock model
class _ClockModel:
    """``t_host = a * t_dev + b`` from min-RTT sync points (see the module docstring)."""

    def __init__(self, window: int = 64, prior_ppm: float = 100.0) -> None:
        self._pts: collections.deque[tuple[float, float, float]] = collections.deque(maxlen=window)
        self._lam = 1.0 / (max(prior_ppm, 1e-3) * 1e-6) ** 2
        self._lock = threading.Lock()
        self._fit: tuple[float, float, float] | None = None  # (x0, y0, a): t_host = y0 + a (t_dev - x0)

    @property
    def valid(self) -> bool:
        return self._fit is not None

    @property
    def params(self) -> tuple[float, float] | None:
        """``(a, b)`` of ``t_host = a * t_dev + b``, or None before the first sync."""
        fit = self._fit
        if fit is None:
            return None
        x0, y0, a = fit
        return a, y0 - a * x0

    def add(self, t_dev: float, t_host: float, rtt: float) -> None:
        with self._lock:
            if self._fit is not None and self._pts:
                backwards = t_dev < self._pts[-1][0]
                resid = t_host - self._eval(self._fit, t_dev)
                if backwards or abs(resid) > max(2e-3, 2.0 * rtt):
                    log.warning(
                        "Pico clock discontinuity (%s); restarting the clock model",
                        "device time went backwards, firmware rebooted?" if backwards else f"residual {resid * 1e3:.3f} ms",
                    )
                    self._pts.clear()
            sigma = max(rtt, 1e-6) / (2.0 * math.sqrt(3.0))
            self._pts.append((float(t_dev), float(t_host), 1.0 / sigma**2))
            x, y, w = (np.array(c, dtype=np.float64) for c in zip(*self._pts))
            x0 = float(np.sum(w * x) / np.sum(w))
            y0 = float(np.sum(w * y) / np.sum(w))
            dx, dy = x - x0, y - y0
            a = (float(np.sum(w * dx * dy)) + self._lam) / (float(np.sum(w * dx * dx)) + self._lam)
            self._fit = (x0, y0, a)

    @staticmethod
    def _eval(fit: tuple[float, float, float], t_dev: Any) -> Any:
        x0, y0, a = fit
        return y0 + a * (np.asarray(t_dev, dtype=np.float64) - x0)

    def to_host(self, t_dev: Any) -> Any:
        fit = self._fit
        if fit is None:
            raise RuntimeError("Pico clock is not synchronised yet")
        return self._eval(fit, t_dev)


def _drain(dq: collections.deque) -> list:
    out = []
    try:
        while True:
            out.append(dq.popleft())
    except IndexError:
        return out


def _pct(value: float) -> int:
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f"emitter level must be finite, got {value!r}")
    return int(round(min(100.0, max(0.0, v))))


# --------------------------------------------------------------------------- client
class Pico:
    """Client for the OpenHandScan Pico firmware. Use as a context manager."""

    def __init__(self, params: PicoParams | None = None, transport: Any | None = None) -> None:
        self.params = params or PicoParams()
        self._transport = transport
        self._owns_transport = transport is None
        self._port_name: str | None = None if transport is None else "<injected>"
        self._write_lock = threading.Lock()
        self._cmd_lock = threading.Lock()
        self._sync_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._replies: queue.Queue = queue.Queue()
        self._pending: tuple[str, str] | None = None  # (reply prefix, command word)
        self._imu_q: collections.deque = collections.deque(maxlen=_IMU_QUEUE_MAX)
        self._evt_q: collections.deque = collections.deque(maxlen=_EVENT_QUEUE_MAX)
        self._clock = _ClockModel(prior_ppm=self.params.drift_prior_ppm)
        self._scales: tuple[float, float] | None = None
        self._imu_rate = 0
        self._last_t_imu: int | None = None
        self._temp_c: float | None = None
        self._levels: tuple[int, int, int] = (0, 0, 0)
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._maint: threading.Thread | None = None
        self._opened = False
        self._connected = False
        self._error: BaseException | None = None
        self._warned_unsynced = False
        self._stats = dict.fromkeys(_STAT_KEYS, 0)
        self.hello: dict = {}
        self.last_error_line: str | None = None

    # ------------------------------------------------------------------ lifecycle
    def open(self) -> dict:
        """Connect, check the firmware (HELLO), switch emitters and IMU off, sync the clock.

        Returns the HELLO info: ``{"firmware", "version", "imu", "port", "raw"}``.
        """
        if self._opened:
            return dict(self.hello)
        if self._transport is None:
            port = self.params.port or find_pico_port()
            if port is None:
                raise ConnectionError("no Pico found (USB VID 0x2E8A, or an ESP32's 0x303A); set PicoParams.port")
            import serial  # pyserial

            self._transport = serial.Serial(port, self.params.baud, timeout=0.05, write_timeout=1.0)
            self._owns_transport = True
            self._port_name = port
        reset = getattr(self._transport, "reset_input_buffer", None)
        if callable(reset):
            try:
                reset()
            except BaseException:
                self._shutdown()  # release a port we opened
                raise
        self._stop.clear()
        _drain(self._imu_q)
        _drain(self._evt_q)
        self._scales, self._imu_rate, self._levels, self._error = None, 0, (0, 0, 0), None
        self._temp_c = None
        self._connected = self._opened = True
        self._reader = threading.Thread(target=self._read_loop, name="pico-reader", daemon=True)
        self._reader.start()
        try:
            self._send("")  # terminate any partial line left in the firmware's input buffer
            self.hello = self._handshake()
            self.all_off()
            self.stop_imu()
            self.sync_clock(self.params.sync_samples)
        except BaseException:
            self._shutdown()
            raise
        self._maint = threading.Thread(target=self._maint_loop, name="pico-maint", daemon=True)
        self._maint.start()
        log.info("Pico on %s: %s %s, imu=%s", self._port_name, self.hello.get("firmware"),
                 self.hello.get("version"), self.hello.get("imu"))
        return dict(self.hello)

    def close(self) -> None:
        """Turn the emitters (and the IMU stream) off, stop the threads, release the port."""
        if not self._opened:
            return
        if self._connected:
            try:
                self.all_off()
            except Exception as e:  # the firmware watchdog still turns them off within 1.5 s
                log.warning("could not switch the emitters off on close: %s", e)
            try:
                self.stop_imu()
            except Exception as e:
                log.debug("could not stop the IMU on close: %s", e)
        self._shutdown()

    def __enter__(self) -> "Pico":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _shutdown(self) -> None:
        self._stop.set()
        self._replies.put(None)  # wake a waiting command
        for th in (self._maint, self._reader):
            if th is not None and th is not threading.current_thread():
                th.join(timeout=2.0)
                if th.is_alive():
                    log.warning("thread %s did not stop", th.name)
        if self._owns_transport and self._transport is not None:
            try:
                self._transport.close()
            except Exception as e:
                log.debug("closing the serial port failed: %s", e)
            self._transport = None
        with self._state_lock:
            self._connected = False
        self._opened = False

    def _on_disconnect(self, exc: BaseException) -> None:
        with self._state_lock:
            if not self._connected:
                return
            self._connected = False
            self._error = exc
        log.error("Pico connection lost (%s): %s", self._port_name, exc)
        self._stop.set()
        self._replies.put(None)

    def _handshake(self) -> dict:
        last: Exception | None = None
        for _ in range(3):
            try:
                line, _, _ = self._request("HELLO", "OK HELLO")
                break
            except TimeoutError as e:
                last = e
        else:
            raise ConnectionError(
                f"no HELLO reply from {self._port_name}; is the OpenHandScan firmware (firmware/main.py) running?"
            ) from last
        tok = line.split()
        info: dict[str, Any] = {
            "firmware": tok[2] if len(tok) > 2 else "",
            "version": tok[3] if len(tok) > 3 else "",
            "imu": "NONE",
            "port": self._port_name,
            "raw": line,
        }
        for t in tok[4:]:
            key, sep, val = t.partition("=")
            if sep:
                info[key] = val
        if info["firmware"] != "ohs-fw":
            log.warning("unexpected firmware %r on %s", info["firmware"], self._port_name)
        return info

    # ------------------------------------------------------------------ properties
    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def emitters(self) -> tuple[int, int, int]:
        """Last confirmed emitter levels (percent): (vertical, horizontal, dots)."""
        return self._levels

    @property
    def imu_scales(self) -> tuple[float, float] | None:
        """(rad/s per LSB, m/s^2 per LSB) from the last ``OK IMU`` reply."""
        return self._scales

    @property
    def clock_model(self) -> tuple[float, float] | None:
        """``(a, b)`` with ``t_host = a * t_dev + b`` (seconds), None before the first sync."""
        return self._clock.params

    @property
    def temperature_c(self) -> float | None:
        """The IMU chip's latest temperature in degC, or None. Firmware 1.1+ sends it about once a
        second while the IMU streams."""
        return self._temp_c

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    @property
    def error(self) -> BaseException | None:
        """The exception that ended the connection, if any."""
        return self._error

    # ------------------------------------------------------------------ commands
    def set_emitters(self, vertical: float, horizontal: float, dots: float) -> None:
        """Set the emitter PWM levels in percent (clamped to 0..100, rounded to integers)."""
        req = (_pct(vertical), _pct(horizontal), _pct(dots))
        line, _, _ = self._request("EMIT %d %d %d" % req, "OK EMIT")
        try:
            got = tuple(int(x) for x in line.split()[2:5])
        except ValueError:
            raise PicoError(f"malformed reply {line!r}") from None
        if got != req:
            log.warning("firmware set emitters to %s (requested %s)", got, req)
        self._levels = got  # type: ignore[assignment]

    def all_off(self) -> None:
        self.set_emitters(0, 0, 0)

    def start_imu(self, rate_hz: int | None = None) -> None:
        """Start streaming at ``rate_hz`` (104/208/416/833; default ``params.imu_rate_hz``)."""
        rate = int(self.params.imu_rate_hz if rate_hz is None else rate_hz)
        line, _, _ = self._request(f"IMU {rate}", "OK IMU")
        parts = line.split()
        if len(parts) < 5 or parts[2] != str(rate):
            raise PicoError(f"unexpected reply {line!r} to IMU {rate}")

    def stop_imu(self) -> None:
        self._request("IMU 0", "OK IMU")

    def status(self) -> dict:
        """``STATUS`` parsed: ``{"emit": (v, h, d), "imu": rate, "btn": 0|1, "up": s, "raw": line}``,
        plus ``"temp"`` (degC) once firmware 1.1+ has read the IMU chip's temperature."""
        line, _, _ = self._request("STATUS", "S ")
        out: dict[str, Any] = {"raw": line}
        for tok in line.split()[1:]:
            key, _, val = tok.partition("=")
            try:
                if key == "emit":
                    out[key] = tuple(int(x) for x in val.split(","))
                elif key in ("imu", "btn"):
                    out[key] = int(val)
                elif key in ("up", "temp"):
                    out[key] = float(val)
                else:
                    out[key] = val
            except ValueError:
                out[key] = val
        return out

    def sync_clock(self, n: int = 16) -> tuple[float, float]:
        """Measure the clock offset with ``n`` TIME round trips and update the clock model.

        Returns ``(offset_s, best_rtt_s)`` where ``offset_s = t_host - t_device`` at the min-RTT
        exchange. Runs automatically every ``params.resync_s`` in the background.
        """
        if n < 1:
            raise ValueError("n must be >= 1")
        with self._sync_lock:
            best: tuple[float, float, float] | None = None
            for _ in range(n):
                if self._stop.is_set():
                    break
                try:
                    line, t0, t1 = self._request("TIME", "T ")
                except TimeoutError:
                    # A late reply must not be paired with the next request: stop this burst.
                    log.warning("TIME request timed out during clock sync")
                    time.sleep(0.05)
                    break
                try:
                    t_dev = int(line.split()[1]) * 1e-6
                except (IndexError, ValueError):
                    log.warning("malformed TIME reply %r", line)
                    continue
                rtt = t1 - t0
                if best is None or rtt < best[0]:
                    best = (rtt, t_dev, 0.5 * (t0 + t1))
            if best is None:
                if not self._connected:
                    raise ConnectionError(self._closed_reason())
                raise TimeoutError("clock sync failed: no TIME reply")
            rtt, t_dev, t_host = best
            self._clock.add(t_dev, t_host, rtt)
            self._stats["syncs"] += 1
            log.debug("clock sync: offset %.6f s, rtt %.3f ms, model %s", t_host - t_dev, rtt * 1e3, self._clock.params)
            return t_host - t_dev, rtt

    def device_to_host(self, t_device: Any) -> Any:
        """Map device time(s) in seconds to host ``perf_counter`` time with the current model."""
        return self._clock.to_host(t_device)

    # ------------------------------------------------------------------ data
    def read_imu(self) -> list[ImuSample]:
        """Drain the IMU queue: gyro in rad/s, accel in m/s^2 (IMU frame), clock-synced ``t_host``."""
        items = _drain(self._imu_q)
        if not items:
            return []
        a = np.asarray(items, dtype=np.float64)  # t_us, t_rx, gx, gy, gz, ax, ay, az, gscale, ascale
        t_dev = a[:, 0] * 1e-6
        t_host = self._to_host(t_dev, a[:, 1])
        gyro = a[:, 2:5] * a[:, 8:9]
        accel = a[:, 5:8] * a[:, 9:10]
        return [ImuSample(float(th), float(td), g, ac) for th, td, g, ac in zip(t_host, t_dev, gyro, accel)]

    def read_events(self) -> list[Event]:
        """Drain the event queue, oldest first: ``Event(t_host, "button", "down" | "up")`` and
        ``Event(t_host, "temperature", "<degC>")`` (the IMU chip, e.g. ``"31.25"``; firmware 1.1+
        sends it about once a second while the IMU streams)."""
        items = _drain(self._evt_q)
        if not items:
            return []
        t_dev = np.array([it[0] for it in items], dtype=np.float64) * 1e-6
        t_rx = np.array([it[1] for it in items], dtype=np.float64)
        t_host = self._to_host(t_dev, t_rx)
        return [Event(float(t), it[2], it[3]) for t, it in zip(t_host, items)]

    def _to_host(self, t_dev: np.ndarray, t_rx: np.ndarray) -> np.ndarray:
        if self._clock.valid:
            return self._clock.to_host(t_dev)
        if not self._warned_unsynced:
            log.warning("clock not synchronised: using reception times as t_host")
            self._warned_unsynced = True
        return t_rx

    # ------------------------------------------------------------------ I/O internals
    def _closed_reason(self) -> str:
        if self._error is not None:
            return f"Pico disconnected: {self._error}"
        return "Pico is not open"

    def _send(self, line: str) -> float:
        """Write one line; returns ``perf_counter()`` taken right before the write."""
        data = (line + "\n").encode("ascii")
        with self._write_lock:
            if not self._connected:
                raise ConnectionError(self._closed_reason())
            t = time.perf_counter()
            try:
                self._transport.write(data)
            except Exception as e:  # serial.SerialException is an OSError
                self._on_disconnect(e)
                raise ConnectionError(f"Pico write failed: {e}") from e
        return t

    def _request(self, cmd: str, expect: str, timeout: float | None = None) -> tuple[str, float, float]:
        """Send ``cmd`` and wait for the reply starting with ``expect``: ``(line, t_send, t_rx)``."""
        timeout = self.params.timeout_s if timeout is None else timeout
        word = cmd.split()[0].upper()
        with self._cmd_lock:
            if not self._connected:
                raise ConnectionError(self._closed_reason())
            _drain_queue(self._replies)
            self._pending = (expect, word)
            try:
                t_send = self._send(cmd)
                try:
                    item = self._replies.get(timeout=timeout)
                except queue.Empty:
                    raise TimeoutError(f"no reply to {cmd!r} within {timeout:.2f} s") from None
            finally:
                self._pending = None
        if item is None:
            raise ConnectionError(self._closed_reason())
        line, t_rx = item
        if line.startswith("E"):
            raise PicoError(f"{cmd!r} failed: {line}")
        return line, t_send, t_rx

    def _offer_reply(self, line: str, t_rx: float) -> bool:
        pending = self._pending
        if pending is None:
            return False
        expect, word = pending
        if line.startswith(expect) or (line.startswith("E ") and word in line.upper().split()[1:3]):
            self._replies.put((line, t_rx))
            return True
        return False

    def _read_loop(self) -> None:
        tr = self._transport
        buf = b""
        while not self._stop.is_set():
            try:
                n = getattr(tr, "in_waiting", 0) or 1
                data = tr.read(n)
            except Exception as e:
                if not self._stop.is_set():
                    self._on_disconnect(e)
                return
            if not data:
                continue
            t_rx = time.perf_counter()
            buf += data
            if b"\n" not in buf:
                if len(buf) > 65536:
                    log.warning("discarding 64 KiB of unterminated serial data")
                    buf = b""
                continue
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                line = raw.decode("ascii", "replace").strip()
                if not line:
                    continue
                try:
                    self._dispatch(line, t_rx)
                except Exception:
                    log.exception("failed to handle Pico line %r", line)

    def _dispatch(self, line: str, t_rx: float) -> None:
        self._stats["lines"] += 1
        if line.startswith("I "):
            self._on_imu(line, t_rx)
        elif line.startswith("B "):
            parts = line.split()
            if len(parts) == 3 and parts[2] in ("0", "1") and parts[1].isdigit():
                self._evt_q.append((int(parts[1]), t_rx, "button", "down" if parts[2] == "1" else "up"))
                self._stats["events"] += 1
            else:
                self._unparsed(line)
        elif line.startswith("C "):
            self._on_temp(line, t_rx)
        elif line.startswith(("OK ", "T ", "S ")):
            if line.startswith("OK IMU "):
                self._on_imu_reply(line)
            if not self._offer_reply(line, t_rx):
                log.debug("unsolicited reply %r", line)
        elif line.startswith("E "):
            self.last_error_line = line
            self._stats["errors"] += 1
            if not self._offer_reply(line, t_rx):
                log.warning("Pico error: %s", line[2:])
        elif line.startswith("L "):
            msg = line[2:]
            if msg.startswith("emitters-off"):
                self._levels = (0, 0, 0)
                log.warning("Pico turned the emitters off by itself (%s)", msg)
            else:
                log.info("Pico: %s", msg)
        else:
            self._unparsed(line)

    def _unparsed(self, line: str) -> None:
        self._stats["unparsed"] += 1
        log.debug("unparsed Pico line %r", line)

    def _on_temp(self, line: str, t_rx: float) -> None:
        """``C <t_us> <centi_degC>``: the IMU chip's temperature, queued as a ``temperature`` event."""
        parts = line.split()
        if len(parts) != 3 or not parts[1].isdigit():
            self._unparsed(line)
            return
        try:
            celsius = int(parts[2]) / 100.0
        except ValueError:
            self._unparsed(line)
            return
        self._temp_c = celsius
        self._evt_q.append((int(parts[1]), t_rx, "temperature", f"{celsius:.2f}"))
        self._stats["temps"] += 1

    def _on_imu_reply(self, line: str) -> None:
        parts = line.split()
        try:
            rate, gs, acs = int(parts[2]), float(parts[3]), float(parts[4])
        except (IndexError, ValueError):
            return
        if gs > 0 and acs > 0:
            self._scales = (gs, acs)
        self._imu_rate = rate
        self._last_t_imu = None

    def _on_imu(self, line: str, t_rx: float) -> None:
        parts = line.split()
        try:
            if len(parts) != 8:
                raise ValueError
            vals = [int(p) for p in parts[1:]]
        except ValueError:
            self._unparsed(line)
            return
        scales = self._scales
        if scales is None:  # stream left running by an earlier session, before our OK IMU
            self._stats["imu_unscaled"] += 1
            return
        t_us = vals[0]
        last, rate = self._last_t_imu, self._imu_rate
        if last is not None and rate > 0:
            period = 1e6 / rate
            if t_us - last > 1.5 * period:
                self._stats["imu_gaps"] += int(round((t_us - last) / period)) - 1
        self._last_t_imu = t_us
        if len(self._imu_q) == self._imu_q.maxlen:
            self._stats["imu_dropped"] += 1
        self._imu_q.append((t_us, t_rx, vals[1], vals[2], vals[3], vals[4], vals[5], vals[6], scales[0], scales[1]))
        self._stats["imu"] += 1

    def _maint_loop(self) -> None:
        p = self.params
        next_hb = 0.0
        next_sync = time.perf_counter() + p.resync_s
        tick = max(0.005, min(0.05, p.heartbeat_s / 4.0))
        while not self._stop.is_set():
            now = time.perf_counter()
            try:
                if any(self._levels) and now >= next_hb:
                    self._send("HB")
                    next_hb = now + p.heartbeat_s
                if now >= next_sync:
                    try:
                        self.sync_clock(p.sync_samples)
                    except (TimeoutError, PicoError) as e:
                        log.warning("clock re-sync failed: %s", e)
                    next_sync = time.perf_counter() + p.resync_s
            except ConnectionError:
                return
            except Exception:  # keep heartbeats alive whatever happens; the watchdog is the backstop
                log.exception("Pico maintenance thread error")
                next_sync = time.perf_counter() + p.resync_s
            self._stop.wait(tick)


def _drain_queue(q: queue.Queue) -> None:
    try:
        while True:
            q.get_nowait()
    except queue.Empty:
        pass


def find_pico_port() -> str | None:
    """Serial port of the scanner's controller: the first Raspberry Pi USB device (VID 0x2E8A,
    MicroPython PID preferred), else an ESP32's native USB port (Espressif's VID 0x303A). A
    USB-serial bridge chip (CH34x, CP210x) is never picked: set ``PicoParams.port`` for those."""
    try:
        from serial.tools import list_ports
    except ImportError:  # pragma: no cover - pyserial is a hard dependency
        log.warning("pyserial is not installed; cannot look for the Pico")
        return None
    ports = [p for p in list_ports.comports() if p.vid in (RPI_USB_VID, ESPRESSIF_USB_VID)]
    if not ports:
        return None
    ports.sort(key=lambda p: (p.vid != RPI_USB_VID, p.pid != MICROPYTHON_USB_PID, p.device))
    if len(ports) > 1:
        log.info("several controller USB serial ports (%s); using %s", ", ".join(p.device for p in ports), ports[0].device)
    return ports[0].device
