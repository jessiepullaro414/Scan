"""Host Pico client tests: ``ohs.devices.pico.Pico`` talks to the REAL firmware state machine.

``SimPico`` loads ``firmware/main.py`` on CPython, injects fake PWM/pin/IMU hardware and a device
crystal with its own offset and drift (30-bit ticks that wrap during the test, unwrapped by the
firmware's own ``UnwrappedClock``), and exposes a pyserial-like in-memory port. Host writes are
processed synchronously (the real firmware polls stdin at ~5 kHz); a device thread runs the
periodic work (watchdog, debounce, IMU) every ~1 ms.
"""
import collections
import logging
import math
import threading
import time
import types
from pathlib import Path

import numpy as np
import pytest
import serial

from ohs.devices.pico import Pico, PicoError, PicoParams, find_pico_port

FW_DIR = Path(__file__).resolve().parents[2] / "firmware"
MASK30 = (1 << 30) - 1
GYRO_LSB = math.radians(0.035)  # rad/s per LSB at +-1000 dps
ACCEL_LSB = 0.244e-3 * 9.80665  # m/s^2 per LSB at +-8 g


def _load(name: str, filename: str) -> types.ModuleType:
    """Import a firmware file as a plain module (no __pycache__ next to the device files)."""
    path = FW_DIR / filename
    mod = types.ModuleType(name)
    mod.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)
    return mod


FW = _load("ohs_fw_main_p", "main.py")
LSM = _load("ohs_fw_lsm6dsox_p", "lsm6dsox.py")


def mp_ticks_diff(a: int, b: int) -> int:
    return ((a - b + (1 << 29)) & MASK30) - (1 << 29)


# --------------------------------------------------------------------------- simulated device
class DeviceTime:
    """The Pico's crystal: runs ``1 + drift`` times as fast as perf_counter, 30-bit ticks."""

    def __init__(self, drift_ppm: float, start_ticks: int) -> None:
        self.k = 1.0 + drift_ppm * 1e-6
        self.start_ticks = start_ticks
        self.t0: float | None = None

    def ticks_us(self) -> int:
        now = time.perf_counter()
        if self.t0 is None:
            self.t0 = now
        return (self.start_ticks + int((now - self.t0) * self.k * 1e6)) & MASK30

    def host_time(self, t_dev_s, start_us: int) -> np.ndarray:
        """True host time of device time(s) ``t_dev_s`` (seconds of the unwrapped clock)."""
        return self.t0 + (np.asarray(t_dev_s, dtype=np.float64) * 1e6 - start_us) / (self.k * 1e6)


class FakePWM:
    def __init__(self) -> None:
        self.duty = None

    def duty_u16(self, d: int) -> None:
        self.duty = d


class FakePin:
    def __init__(self) -> None:
        self.level = 1  # released (pull-up)

    def value(self) -> int:
        return self.level


class FakeImu:
    """LSM6DSOX stand-in at the driver interface: ODR-paced samples with fixed raw counts."""

    name = "LSM6DSOX"
    gyro_scale = LSM.GYRO_SCALE
    accel_scale = LSM.ACCEL_SCALE
    RAW = (1000, -2000, 3000, -4096, 0, 4096)

    def __init__(self, clock) -> None:
        self.clock = clock
        self.rate = 0
        self.t_start = 0
        self.consumed = -1

    def set_rate(self, rate: int) -> None:
        self.rate, self.t_start, self.consumed = rate, self.clock(), -1

    def _latest(self) -> int:
        return (self.clock() - self.t_start) * self.rate // 1_000_000 - 1

    def data_ready(self) -> bool:
        return self.rate > 0 and self._latest() > self.consumed

    def read_raw(self) -> tuple:
        self.consumed = self._latest()
        return self.RAW


class HostPort:
    """pyserial-like host end of the link. Device output can be delayed; byte order is kept."""

    def __init__(self, dev: "SimPico") -> None:
        self.dev = dev
        self.timeout = 0.05
        self._cond = threading.Condition()
        self._chunks: collections.deque = collections.deque()  # (t_available, bytes)
        self._buf = bytearray()
        self.broken = False
        self.closed = False

    def push(self, data: bytes, delay: float = 0.0) -> None:
        with self._cond:
            self._chunks.append((time.perf_counter() + delay, data))
            self._cond.notify_all()

    def _move_due(self) -> None:
        now = time.perf_counter()
        while self._chunks and self._chunks[0][0] <= now:
            self._buf += self._chunks.popleft()[1]

    @property
    def in_waiting(self) -> int:
        with self._cond:
            if self.broken:
                raise serial.SerialException("ClearCommError failed (device unplugged)")
            self._move_due()
            return len(self._buf)

    def read(self, size: int = 1) -> bytes:
        deadline = time.perf_counter() + self.timeout
        while True:
            with self._cond:
                if self.broken:
                    raise serial.SerialException("ReadFile failed (device unplugged)")
                self._move_due()
                if self._buf:
                    out = bytes(self._buf[:size])
                    del self._buf[:size]
                    return out
                now = time.perf_counter()
                if now >= deadline:
                    return b""
                if not self._chunks:
                    self._cond.wait(deadline - now)
                    continue
            time.sleep(0.0002)  # delayed data pending (timed waits are ~16 ms on Windows)

    def write(self, data: bytes) -> int:
        if self.broken:
            raise serial.SerialException("WriteFile failed (device unplugged)")
        self.dev.host_write(bytes(data))
        return len(data)

    def reset_input_buffer(self) -> None:
        with self._cond:
            self._chunks.clear()
            self._buf.clear()

    def close(self) -> None:
        self.closed = True

    def unplug(self) -> None:
        with self._cond:
            self.broken = True
            self._cond.notify_all()


class SimPico:
    """firmware/main.py ``Firmware`` + fake hardware + device clock, behind a ``HostPort``."""

    START_US = 3600 * 1_000_000 + 12_345  # the device has been up for an hour

    def __init__(self, drift_ppm: float = 0.0, start_ticks: int = MASK30 - 400_000, with_imu: bool = True,
                 delay_time_replies: bool = False) -> None:
        self.devtime = DeviceTime(drift_ppm, start_ticks)  # ticks wrap 0.4 s after start
        self.clock = FW.UnwrappedClock(self.devtime.ticks_us, mp_ticks_diff, self.START_US)
        self.pwms = [FakePWM() for _ in range(3)]
        self.button = FakePin()
        self.imu = FakeImu(self.clock) if with_imu else None
        self.lock = threading.Lock()
        self.port = HostPort(self)
        self.received: list[str] = []
        self.drop_host_tx = False
        self.delay_time_replies = delay_time_replies
        self._n_time = 0
        self._rx = ""
        self.fw = FW.Firmware(self.pwms, self.button, self.imu, self.clock, self._device_write)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="sim-pico", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self.lock:
                self.fw.process("")
            time.sleep(0.0003)

    def host_write(self, data: bytes) -> None:
        if self.drop_host_tx:
            return
        text = data.decode("ascii")
        with self.lock:
            self._rx += text
            while "\n" in self._rx:
                line, self._rx = self._rx.split("\n", 1)
                if line.strip():
                    self.received.append(line.strip())
            self.fw.process(text)

    def _device_write(self, s: str) -> None:
        delay = 0.0
        if self.delay_time_replies and s.startswith("T "):
            self._n_time += 1
            if self._n_time % 4:
                delay = 0.004  # 3 of 4 TIME replies are held up 4+ ms (asymmetric delay)
        self.port.push(s.replace("\n", "\r\n").encode("ascii"), delay)  # MicroPython sends CRLF

    def duties(self) -> list:
        return [p.duty for p in self.pwms]

    def device_now_s(self) -> tuple[float, float]:
        """(device time in s, host perf_counter) sampled together."""
        with self.lock:
            return self.clock() * 1e-6, time.perf_counter()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)


@pytest.fixture
def sim():
    s = SimPico()
    yield s
    s.close()


def wait_until(cond, timeout: float = 3.0) -> float:
    t0 = time.perf_counter()
    while not cond():
        if time.perf_counter() - t0 > timeout:
            raise AssertionError("condition not reached")
        time.sleep(0.005)
    return time.perf_counter() - t0


# --------------------------------------------------------------------------- tests
def test_hello_and_known_state_on_open(sim):
    with sim.lock:  # a previous session left everything running
        sim.fw.set_levels(30, 30, 30)
        sim.fw.start_imu(416)
    with Pico(PicoParams(), transport=sim.port) as pico:
        info = pico.hello
        assert (info["firmware"], info["version"], info["imu"]) == ("ohs-fw", FW.FW_VERSION, "LSM6DSOX")
        assert sim.duties() == [0, 0, 0] and sim.fw.imu_rate == 0
        assert pico.read_imu() == []  # the stale stream is not delivered
        st = pico.status()
        assert st["emit"] == (0, 0, 0) and st["imu"] == 0 and st["btn"] == 0
        assert 0.0 <= st["up"] < 5.0  # seconds since the firmware started
        assert pico.connected and pico.clock_model is not None
    assert not pico.connected
    assert sim.port.closed is False  # an injected transport belongs to the caller


def test_emitters_clamping_heartbeat_and_close(sim):
    pico = Pico(PicoParams(heartbeat_s=0.2), transport=sim.port)
    pico.open()
    try:
        pico.set_emitters(12.4, 150, -3)
        assert pico.emitters == (12, 100, 0)
        assert sim.duties() == [12 * 65535 // 100, 65535, 0]
        n0 = sim.received.count("HB")
        time.sleep(1.0)
        assert 2 <= sim.received.count("HB") - n0 <= 7  # ~every 0.2 s while an emitter is on
        pico.all_off()
        assert sim.duties() == [0, 0, 0] and pico.emitters == (0, 0, 0)
        n1 = sim.received.count("HB")
        time.sleep(0.5)
        assert sim.received.count("HB") == n1  # no heartbeat while everything is off
        pico.set_emitters(0, 0, 55)
        assert sim.duties()[2] == 55 * 65535 // 100
        with pytest.raises(ValueError):
            pico.set_emitters(float("nan"), 0, 0)
    finally:
        pico.close()
    assert sim.duties() == [0, 0, 0]  # close() turned the projector off
    assert [c for c in sim.received if c != "HB"][-2:] == ["EMIT 0 0 0", "IMU 0"]


def test_imu_streaming_si_conversion(sim):
    with Pico(PicoParams(imu_rate_hz=416), transport=sim.port) as pico:
        pico.start_imu()
        assert pico.imu_scales == pytest.approx((GYRO_LSB, ACCEL_LSB), rel=1e-6)
        time.sleep(0.5)
        pico.stop_imu()
        samples = pico.read_imu()
        gaps = pico.stats["imu_gaps"]
    assert len(samples) >= 20
    g = np.array([s.gyro for s in samples])
    a = np.array([s.accel for s in samples])
    assert np.allclose(g, np.array([1000, -2000, 3000]) * GYRO_LSB, rtol=1e-6)  # rad/s
    assert np.allclose(a, np.array([-4096, 0, 4096]) * ACCEL_LSB, rtol=1e-6)  # m/s^2
    assert a[0, 2] == pytest.approx(9.80, abs=0.01)
    td = np.array([s.t_device for s in samples])
    th = np.array([s.t_host for s in samples])
    assert td[0] > 3600.0 and np.all(np.diff(td) > 0) and np.all(np.diff(th) > 0)
    truth = sim.devtime.host_time(td, SimPico.START_US)
    assert np.max(np.abs(th - truth)) < 0.5e-3
    # 416 Hz stream: every period is either delivered or counted as a gap. Gaps only appear when
    # the OS starves the simulated device loop; a real sensor also overwrites unread samples.
    periods = (td[-1] - td[0]) * 416
    assert periods == pytest.approx(0.5 * 416, rel=0.4)
    assert len(samples) - 1 + gaps == pytest.approx(periods, abs=0.05 * periods + 3)


def test_button_events(sim):
    with Pico(PicoParams(), transport=sim.port) as pico:
        t_press = time.perf_counter()
        sim.button.level = 0
        time.sleep(0.1)
        t_release = time.perf_counter()
        sim.button.level = 1
        time.sleep(0.1)
        events = pico.read_events()
        assert pico.read_events() == []  # drained
    assert [(e.kind, e.value) for e in events] == [("button", "down"), ("button", "up")]
    # mapped to host time; the slack is only the simulated device loop's scheduling latency
    # (exact edge-time stamping, rather than end of debounce, is checked in test_firmware.py)
    assert -0.5e-3 < events[0].t_host - t_press < 0.05
    assert -0.5e-3 < events[1].t_host - t_release < 0.05


def test_clock_sync_offset_and_drift_within_half_ms():
    sim = SimPico(drift_ppm=400.0, delay_time_replies=True)
    try:
        pico = Pico(PicoParams(resync_s=0.25), transport=sim.port)
        pico.open()
        try:
            offset, rtt = pico.sync_clock(16)
            assert rtt < 2e-3  # min-RTT picked an undelayed reply (3 of 4 are held up 4 ms)
            t_dev, t_host = sim.device_now_s()
            assert offset == pytest.approx(t_host - t_dev, abs=0.01)
            assert abs(pico.device_to_host(t_dev) - t_host) < 0.5e-3
            pico.start_imu(416)
            time.sleep(2.5)  # background re-syncs every 0.25 s
            pico.stop_imu()
            samples = pico.read_imu()  # all converted with the final model
            a, _ = pico.clock_model
            assert pico.stats["syncs"] >= 6
        finally:
            pico.close()
    finally:
        sim.close()
    assert a == pytest.approx(1.0 / sim.devtime.k, abs=40e-6)
    td = np.array([s.t_device for s in samples])
    th = np.array([s.t_host for s in samples])
    truth = sim.devtime.host_time(td, SimPico.START_US)
    assert np.max(np.abs(th - truth)) < 0.5e-3
    assert len(samples) > 100 and td[-1] - td[0] > 2.0
    # the test is sensitive to drift: an offset-only model anchored at the end is off by > 0.6 ms
    naive = truth[-1] + (td - td[-1])
    assert np.max(np.abs(naive - truth)) > 0.6e-3


def test_watchdog_turns_emitters_off_when_host_goes_silent(sim, caplog):
    caplog.set_level(logging.INFO, logger="ohs.devices.pico")
    with Pico(PicoParams(), transport=sim.port) as pico:
        pico.set_emitters(40, 40, 40)
        time.sleep(1.8)
        assert sim.duties() == [26214] * 3  # heartbeats keep them on past 1.5 s
        sim.drop_host_tx = True  # host hangs: nothing reaches the device any more
        dt = wait_until(lambda: sim.duties() == [0, 0, 0], timeout=3.0)
        assert 0.5 < dt < 2.5  # 1.5 s after the last line that arrived (<= 1 heartbeat ago)
        wait_until(lambda: pico.emitters == (0, 0, 0), timeout=1.0)  # learned from "L emitters-off"
        assert "emitters off by itself (emitters-off watchdog)" in caplog.text
        sim.drop_host_tx = False
        pico.set_emitters(10, 0, 0)
        assert sim.duties()[0] == 6553


def test_disconnect_is_clean(sim, caplog):
    pico = Pico(PicoParams(), transport=sim.port)
    pico.open()
    pico.set_emitters(20, 0, 0)
    sim.drop_host_tx = True  # a command is waiting for a reply when the cable goes
    result: dict = {}

    def call() -> None:
        t = time.perf_counter()
        try:
            pico.set_emitters(1, 1, 1)
        except Exception as e:
            result["exc"] = e
        result["dt"] = time.perf_counter() - t

    th = threading.Thread(target=call)
    th.start()
    time.sleep(0.1)
    sim.port.unplug()
    th.join(timeout=2.0)
    assert isinstance(result["exc"], ConnectionError)
    assert result["dt"] < 0.4  # woken by the disconnect, not by the 0.5 s timeout
    wait_until(lambda: not pico.connected, timeout=1.0)
    assert isinstance(pico.error, serial.SerialException)
    with pytest.raises(ConnectionError):
        pico.set_emitters(0, 0, 0)
    with pytest.raises(ConnectionError):
        pico.sync_clock()
    t0 = time.perf_counter()
    pico.close()
    assert time.perf_counter() - t0 < 1.0
    assert not pico._reader.is_alive() and not pico._maint.is_alive()
    assert "Pico connection lost" in caplog.text
    # the device's own watchdog is the safety net once the host is gone
    wait_until(lambda: sim.duties() == [0, 0, 0], timeout=2.5)


def test_error_replies_raise_pico_error(sim):
    with Pico(PicoParams(), transport=sim.port) as pico:
        with pytest.raises(PicoError, match="unsupported rate 100"):
            pico.start_imu(100)
        with pytest.raises(PicoError, match="unknown FROB"):
            pico._request("FROB 1", "OK FROB")
        pico.start_imu(208)  # still usable afterwards
        pico.stop_imu()
    nimu = SimPico(with_imu=False)
    try:
        with Pico(PicoParams(), transport=nimu.port) as pico:
            assert pico.hello["imu"] == "NONE"
            with pytest.raises(PicoError, match="no imu"):
                pico.start_imu()
    finally:
        nimu.close()


def test_reader_tolerates_noise_and_logs(sim, caplog):
    caplog.set_level(logging.INFO, logger="ohs.devices.pico")
    with Pico(PicoParams(), transport=sim.port) as pico:
        before = pico.stats
        sim.port.push(b"MicroPython v1.24.1 on 2024-11-29\r\nL hello from fw\r\nE imu-read OSError: [Errno 5] EIO\r\n"
                      b"I 12 x\r\nB 5 7\r\nOK EMIT 1 2 3\r\n")
        wait_until(lambda: pico.stats["lines"] >= before["lines"] + 6, timeout=1.0)
        st = pico.stats
        assert st["unparsed"] - before["unparsed"] == 3  # banner, bad I, bad B
        assert st["errors"] - before["errors"] == 1 and pico.last_error_line.startswith("E imu-read")
        assert pico.emitters == (0, 0, 0)  # an unsolicited OK is ignored
        assert pico.status()["emit"] == (0, 0, 0)  # and the link still works
    assert "Pico: hello from fw" in caplog.text and "Pico error: imu-read" in caplog.text


def test_find_pico_port(monkeypatch):
    from serial.tools import list_ports, list_ports_common

    def port(dev: str, vid: int, pid: int):
        p = list_ports_common.ListPortInfo(dev, skip_link_detection=True)
        p.vid, p.pid = vid, pid
        return p

    monkeypatch.setattr(list_ports, "comports",
                        lambda: [port("COM3", 0x1234, 1), port("COM9", 0x2E8A, 0x000A), port("COM7", 0x2E8A, 0x0005)])
    assert find_pico_port() == "COM7"  # MicroPython PID preferred
    monkeypatch.setattr(list_ports, "comports", lambda: [port("COM3", 0x1234, 1)])
    assert find_pico_port() is None


def test_open_fails_cleanly_without_firmware():
    class Silent:  # a port where nothing answers (e.g. the Pico sits at the REPL with no main.py)
        timeout = 0.05

        def read(self, n: int = 1) -> bytes:
            time.sleep(0.01)
            return b""

        def write(self, data: bytes) -> int:
            return len(data)

        def close(self) -> None:
            pass

    pico = Pico(PicoParams(timeout_s=0.1), transport=Silent())
    with pytest.raises(ConnectionError, match="no HELLO reply"):
        pico.open()
    assert not pico.connected and not pico._reader.is_alive()


@pytest.mark.hardware
def test_real_pico_roundtrip():
    port = find_pico_port()
    if port is None:
        pytest.skip("no Pico connected")
    with Pico(PicoParams(port=port)) as pico:
        assert pico.hello["firmware"] == "ohs-fw"
        _, rtt = pico.sync_clock()
        assert rtt < 5e-3
        if pico.hello["imu"] != "NONE":
            pico.start_imu(416)
            time.sleep(1.0)
            pico.stop_imu()
            s = pico.read_imu()
            assert 350 < len(s) < 480
            g = np.linalg.norm(np.mean([x.accel for x in s], axis=0))
            assert 9.0 < g < 10.6  # gravity, if the scanner is resting
