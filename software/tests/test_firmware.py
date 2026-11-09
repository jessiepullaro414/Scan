"""Firmware tests: firmware/main.py and firmware/lsm6dsox.py run on CPython with injected fakes.

Time is fully simulated (``SimClock``), so the tests are deterministic and fast. The register-level
LSM6DSOX fake advances the clock by the duration of every I2C transaction at 400 kHz, so the
firmware's IMU timestamping is exercised under realistic poll timing.
"""
import ast
import math
import random
import struct
import sys
import types
from pathlib import Path

import pytest

FW_DIR = Path(__file__).resolve().parents[2] / "firmware"


def _load(name: str, filename: str) -> types.ModuleType:
    """Import a firmware file as a plain module (no __pycache__ next to the device files)."""
    path = FW_DIR / filename
    mod = types.ModuleType(name)
    mod.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)
    return mod


fw = _load("ohs_fw_main_t", "main.py")
lsm = _load("ohs_fw_lsm6dsox_t", "lsm6dsox.py")

MASK30 = (1 << 30) - 1


def mp_ticks_diff(a: int, b: int) -> int:
    """MicroPython time.ticks_diff for a 2**30 tick period."""
    return ((a - b + (1 << 29)) & MASK30) - (1 << 29)


# --------------------------------------------------------------------------- fakes
class SimClock:
    """Unwrapped microsecond clock that only moves when told to."""

    def __init__(self, t: int = 0) -> None:
        self.t = int(t)

    def __call__(self) -> int:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += int(dt)


class FakePWM:
    def __init__(self, pin=None, fail_nonzero: bool = False) -> None:
        self.pin = pin
        self.duty = None
        self.f = None
        self.fail_nonzero = fail_nonzero

    def duty_u16(self, d=None):
        if d is None:
            return self.duty
        if d and self.fail_nonzero:
            raise RuntimeError("pwm fault")
        self.duty = d

    def freq(self, f=None):
        if f is None:
            return self.f
        self.f = f


class FakePin:
    def __init__(self, level: int = 1) -> None:
        self.level = level

    def value(self, v=None):
        if v is None:
            return self.level
        self.level = v


class Out:
    """Collects firmware output and splits it into lines."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._buf = ""

    def __call__(self, s: str) -> None:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self.lines.append(line)

    def take(self) -> list[str]:
        lines, self.lines = self.lines, []
        return lines


class FakeLsmBus:
    """Register-level LSM6DSOX on a fake ``machine.I2C``.

    Samples are produced at the configured ODR times ``osc`` (sensor oscillator error); sample k
    becomes ready at ``t_start + (k + 1) * period`` and carries ``k`` in gx. Every transaction
    advances the clock by its duration; registers are latched 3/4 of the way through it.
    """

    STATUS_COST_US = 90
    DATA_COST_US = 330
    WRITE_COST_US = 60

    def __init__(self, clock: SimClock, address: int = 0x6A, who: int = 0x6C, osc: float = 1.0) -> None:
        self.clock = clock
        self.address = address
        self.who = who
        self.osc = osc
        self.writes: list[tuple[int, int]] = []
        self.true_times: dict[int, float] = {}
        self.fail_reads = False
        self._defaults()

    def _defaults(self) -> None:
        self.regs = bytearray(0x80)
        self.regs[0x0F] = self.who
        self.regs[0x12] = 0x04  # IF_INC
        self.regs[0x18] = 0xE0
        self.t_start = None
        self.period = 0.0
        self.consumed = -1

    def _latest(self, t: float) -> int:
        if self.t_start is None:
            return -1
        return int((t - self.t_start) // self.period) - 1

    def _check(self, addr: int) -> None:
        if addr != self.address:
            raise OSError(5, "EIO")  # NACK, like machine.I2C

    def writeto_mem(self, addr: int, reg: int, data) -> None:
        self._check(addr)
        self.clock.advance(self.WRITE_COST_US)
        for i, b in enumerate(bytes(data)):
            r = reg + i
            self.writes.append((r, b))
            if r == 0x12 and b & 0x01:  # SW_RESET (self-clearing)
                self._defaults()
                continue
            self.regs[r] = b
            if r == 0x11:  # CTRL2_G: (re)start the sample clock
                odr = {4: 104, 5: 208, 6: 416, 7: 833}.get(b >> 4, 0)
                self.consumed = -1
                if odr:
                    self.t_start, self.period = self.clock.t, 1e6 / odr * self.osc
                else:
                    self.t_start = None

    def readfrom_mem(self, addr: int, reg: int, n: int) -> bytes:
        buf = bytearray(n)
        self.readfrom_mem_into(addr, reg, buf)
        return bytes(buf)

    def readfrom_mem_into(self, addr: int, reg: int, buf) -> None:
        self._check(addr)
        n = len(buf)
        cost = self.DATA_COST_US if n > 1 else self.STATUS_COST_US
        t_latch = self.clock.t + 0.75 * cost
        self.clock.advance(cost)
        if self.fail_reads and reg in (0x1E, 0x22):
            raise OSError(5, "EIO")
        if reg == 0x1E:
            buf[0] = 0x03 if self._latest(t_latch) > self.consumed else 0x00
        elif reg == 0x22 and n == 12:
            k = self._latest(t_latch)
            self.consumed = k
            self.true_times[k] = self.t_start + (k + 1) * self.period
            struct.pack_into("<hhhhhh", buf, 0, k % 30000, -(k % 30000), -1234, -4096, 0, 4096)
        else:
            buf[:] = self.regs[reg : reg + n]


def make_fw(clock: SimClock | None = None, imu=None, button=None, pwms=None):
    clock = clock or SimClock(1_000_000)
    out = Out()
    pwms = pwms or [FakePWM(), FakePWM(), FakePWM()]
    f = fw.Firmware(pwms, button if button is not None else FakePin(1), imu, clock, out)
    return f, out, clock, pwms


def duties(pwms) -> list:
    return [p.duty for p in pwms]


# --------------------------------------------------------------------------- clock
def test_unwrapped_clock_survives_ticks_wraps():
    true = [MASK30 - 1000]  # start just before a wrap
    clk = fw.UnwrappedClock(lambda: true[0] & MASK30, mp_ticks_diff, start_us=5)
    rng = random.Random(1)
    total = 0
    for _ in range(3000):
        step = rng.randrange(0, 1 << 28)  # calls up to ~4.5 min apart
        true[0] += step
        total += step
        assert clk() == 5 + total
    assert total > 8 * (1 << 30)  # wrapped many times, never went backwards


def test_time_reply_is_unwrapped_64bit():
    true = [MASK30 - 300]
    clk = fw.UnwrappedClock(lambda: true[0] & MASK30, mp_ticks_diff, start_us=1 << 40)
    f, out, _, _ = make_fw(clock=clk)
    f.feed("TIME\n")
    true[0] += 1000  # crosses the 2**30 wrap
    f.feed("TIME\nSTATUS\n")
    t1, t2, status = out.take()
    assert t1 == "T %d" % (1 << 40) and t2 == "T %d" % ((1 << 40) + 1000)
    assert status.endswith("up=0.001")


# --------------------------------------------------------------------------- lsm6dsox driver
def test_lsm6dsox_probe_fallback_and_configuration():
    clock = SimClock()
    bus = FakeLsmBus(clock, address=0x6B)
    imu = lsm.LSM6DSOX(bus)
    assert imu.address == 0x6B  # 0x6A NACKed, fallback used
    assert bus.writes[0] == (0x12, 0x01)  # software reset first
    w = dict(bus.writes)
    assert w[0x12] == 0x44  # BDU | IF_INC (after the SW reset)
    assert w[0x18] == 0xE2  # I3C disabled, DEN defaults kept
    assert bus.regs[0x10] == 0x0C and bus.regs[0x11] == 0x08  # powered down, +-8 g, +-1000 dps
    for rate, code in ((104, 4), (208, 5), (416, 6), (833, 7)):
        imu.set_rate(rate)
        assert bus.regs[0x10] == (code << 4) | 0x0C and bus.regs[0x11] == (code << 4) | 0x08
    with pytest.raises(ValueError):
        imu.set_rate(100)
    assert imu.gyro_scale == pytest.approx(math.radians(0.035), rel=1e-12)
    assert imu.accel_scale == pytest.approx(0.244e-3 * 9.80665, rel=1e-12)


def test_lsm6dsox_rejects_wrong_chip():
    bus = FakeLsmBus(SimClock(), address=0x6A, who=0x69)  # e.g. an LSM6DS3
    with pytest.raises(OSError, match="0x6A:0x69"):
        lsm.LSM6DSOX(bus)


def test_lsm6dsox_raw_decoding():
    class Bus(FakeLsmBus):
        def readfrom_mem_into(self, addr, reg, buf):
            if reg == 0x22:
                buf[:] = struct.pack("<hhhhhh", -1, 32767, -32768, 1, 0, -2)
            else:
                super().readfrom_mem_into(addr, reg, buf)

    imu = lsm.LSM6DSOX(Bus(SimClock()))
    assert imu.read_raw() == (-1, 32767, -32768, 1, 0, -2)


def test_lsm6dsox_temperature_decoding():
    bus = FakeLsmBus(SimClock())
    imu = lsm.LSM6DSOX(bus)
    for raw in (0, 1, 127, 128, 256, -256, 1600, -7680, 32767, -32768):
        bus.regs[0x20:0x22] = struct.pack("<h", raw)
        expected = 2500 + math.floor(raw * 100 / 256 + 0.5)  # 256 LSB/degC, 0 LSB = 25 degC, rounded
        assert imu.read_temp_c100() == expected, raw
    assert fw._fmt_c100(3125) == "31.25" and fw._fmt_c100(-5) == "-0.05" and fw._fmt_c100(0) == "0.00"
    assert fw._fmt_c100(-500) == "-5.00" and fw._fmt_c100(12800) == "128.00"


# --------------------------------------------------------------------------- protocol
def test_hello_status_unknown_and_line_handling():
    f, out, clock, _ = make_fw(imu=lsm.LSM6DSOX(FakeLsmBus(SimClock())))
    f.feed("hello\n")
    assert out.take() == ["OK HELLO ohs-fw %s imu=LSM6DSOX" % fw.FW_VERSION]
    f.feed("HEL")
    f.feed("LO\r\n\n\r")  # split line, CRLF and empty lines
    assert out.take() == ["OK HELLO ohs-fw %s imu=LSM6DSOX" % fw.FW_VERSION]
    clock.advance(12_345_678)
    f.feed("STATUS\n")
    assert out.take() == ["S emit=0,0,0 imu=0 btn=0 up=12.345"]
    f.feed("FOO bar\n")
    assert out.take() == ["E unknown FOO"]
    f.feed("X" * 100 + "\nHB\nHELLO\n")
    assert out.take() == ["E line too long", "OK HELLO ohs-fw %s imu=LSM6DSOX" % fw.FW_VERSION]


def test_emit_clamping_pwm_duty_and_bad_args():
    f, out, _, pwms = make_fw()
    assert duties(pwms) == [0, 0, 0]  # off at boot
    f.feed("EMIT 150 -5 42\n")
    assert out.take() == ["OK EMIT 100 0 42"]
    assert duties(pwms) == [65535, 0, 42 * 65535 // 100]
    f.feed("emit 12.7 0 1\n")
    assert out.take() == ["OK EMIT 12 0 1"] and duties(pwms) == [12 * 65535 // 100, 0, 655]
    f.feed("EMIT 1 2\nEMIT a b c\n")
    lines = out.take()
    assert len(lines) == 2 and all(l.startswith("E EMIT bad args") for l in lines)
    assert f.levels == [12, 0, 1]  # unchanged by bad commands


def test_watchdog_turns_emitters_off_after_1500ms():
    f, out, clock, pwms = make_fw()
    f.feed("EMIT 50 60 70\n")
    out.take()
    clock.advance(1_400_000)
    f.process()
    assert f.levels == [50, 60, 70]
    f.feed("HB\n")  # heartbeat at t = 1.4 s
    clock.advance(1_490_000)
    f.process()
    assert f.levels == [50, 60, 70] and duties(pwms) != [0, 0, 0]
    clock.advance(20_000)  # 1.51 s after the last host line
    f.process()
    assert f.levels == [0, 0, 0] and duties(pwms) == [0, 0, 0]
    assert out.take() == ["L emitters-off watchdog"]
    clock.advance(5_000_000)
    f.process()
    assert out.take() == []  # logged once
    # any host line feeds the watchdog, not only HB
    f.feed("EMIT 10 0 0\n")
    clock.advance(1_000_000)
    f.feed("STATUS\n")
    clock.advance(1_400_000)
    f.process()
    assert f.levels == [10, 0, 0]
    clock.advance(200_000)
    f.process()
    assert f.levels == [0, 0, 0]


def test_button_debounce_and_event_timestamp():
    pin = FakePin(1)
    f, out, clock, _ = make_fw(button=pin)

    def run_for(us: int, step: int = 1000) -> None:
        for _ in range(us // step):
            clock.advance(step)
            f.process()

    for level, dur in ((0, 5000), (1, 3000), (0, 4000), (1, 2000)):  # contact bounce
        pin.level = level
        run_for(dur)
    assert out.take() == []
    pin.level = 0
    t_edge = clock.t + 1000  # first poll that sees the press
    run_for(19_000)
    assert out.take() == []  # stable for only 19 ms
    run_for(2000)
    assert out.take() == ["B %d 1" % t_edge]
    pin.level = 1  # 10 ms glitch while held: ignored
    run_for(10_000)
    pin.level = 0
    run_for(30_000)
    assert out.take() == []
    f.feed("STATUS\n")
    assert "btn=1" in out.take()[0]
    pin.level = 1
    t_rel = clock.t + 1000
    run_for(25_000)
    assert out.take() == ["B %d 0" % t_rel]


def test_imu_stream_timestamps_and_line_format():
    clock = SimClock(10_000_000)
    bus = FakeLsmBus(clock, osc=1.013)  # sensor oscillator 1.3 % slow: timestamps must follow it
    f, out, _, _ = make_fw(clock=clock, imu=lsm.LSM6DSOX(bus))
    f.feed("IMU 416\n")
    reply = out.take()[0].split()
    assert reply[:3] == ["OK", "IMU", "416"]
    assert float(reply[3]) == pytest.approx(math.radians(0.035), rel=1e-6)
    assert float(reply[4]) == pytest.approx(0.244e-3 * 9.80665, rel=1e-6)
    t_end = clock.t + 1_000_000
    while clock.t < t_end:
        clock.advance(40)  # loop overhead besides I2C
        f.process()
    lines = out.take()
    temps = [l for l in lines if l.startswith("C ")]
    lines = [l for l in lines if not l.startswith("C ")]
    assert len(temps) == 1 and temps[0].split()[2] == "2500"  # one second: only the first reading (25.00 degC)
    assert lines and all(l.startswith("I ") for l in lines)
    rows = [list(map(int, l.split()[1:])) for l in lines]
    seq = [r[1] for r in rows]
    assert seq == list(range(seq[0], seq[0] + len(seq)))  # no sample lost or repeated
    assert len(rows) == pytest.approx(1e6 / (1e6 / 416 * 1.013), abs=2)
    for r in rows:
        assert r[2] == -r[1] and r[3:] == [-1234, -4096, 0, 4096]
    err = [r[0] - bus.true_times[r[1]] for r in rows]
    assert max(abs(e) for e in err) < 350  # us: half a poll interval + register latch delay
    assert abs(sum(err) / len(err)) < 150
    f.feed("IMU 0\n")
    assert out.take()[0].startswith("OK IMU 0 ")
    for _ in range(100):
        clock.advance(100)
        f.process()
    assert out.take() == [] and bus.regs[0x11] >> 4 == 0


def test_imu_temperature_lines_and_status():
    clock = SimClock(5_000_000)
    bus = FakeLsmBus(clock)
    f, out, _, _ = make_fw(clock=clock, imu=lsm.LSM6DSOX(bus))
    bus.regs[0x20:0x22] = struct.pack("<h", 1600)  # 25 + 1600 / 256 = 31.25 degC (after the driver's reset)

    def run_for(us: int) -> list[str]:
        t_end = clock.t + us
        while clock.t < t_end:
            clock.advance(40)
            f.process()
        return out.take()

    f.feed("STATUS\n")
    assert "temp=" not in out.take()[0]  # nothing read before the IMU runs
    assert run_for(1_500_000) == []  # nor while it is off
    f.feed("IMU 208\n")
    out.take()
    lines = run_for(3_500_000)
    kinds = [l.split()[0] for l in lines]
    assert kinds[:2] == ["I", "C"]  # the first reading comes right after the first sample
    temps = [list(map(int, l.split()[1:])) for l in lines if l.startswith("C ")]
    assert len(temps) == 4 and all(c == 3125 for _, c in temps)  # then about once a second
    for (a, _), (b, _) in zip(temps, temps[1:]):
        assert 1_000_000 <= b - a < 1_000_000 + 1e6 / 208 + 1000  # the first sample after 1 s
    f.feed("STATUS\n")
    assert out.take()[0].endswith(" temp=31.25")
    bus.regs[0x20:0x22] = struct.pack("<h", -7680)  # -5 degC
    f.feed("IMU 0\n")
    out.take()
    assert run_for(2_000_000) == []  # stream stopped: no temperatures either
    f.feed("IMU 416\n")
    out.take()
    lines = run_for(10_000)
    assert [l for l in lines if l.startswith("C ")][0].split()[2] == "-500"  # a restart reads at once
    f.feed("STATUS\n")
    assert out.take()[0].endswith(" temp=-5.00")


def test_temperature_read_failure_is_not_fatal():
    class Bus(FakeLsmBus):
        def readfrom_mem_into(self, addr, reg, buf):
            if reg == 0x20:
                self.clock.advance(self.DATA_COST_US)
                raise OSError(5, "EIO")
            super().readfrom_mem_into(addr, reg, buf)

    clock = SimClock()
    f, out, _, pwms = make_fw(clock=clock, imu=lsm.LSM6DSOX(Bus(clock)))
    f.feed("EMIT 20 20 20\nIMU 416\n")
    out.take()
    t_end = clock.t + 1_200_000  # within the 1.5 s emitter watchdog
    while clock.t < t_end:
        clock.advance(40)
        f.process()
    lines = out.take()
    errors = [l for l in lines if l.startswith("E ")]
    assert errors == ["E temp-read OSError: [Errno 5] EIO"]  # reported once, then no more reads
    assert not any(l.startswith(("C ", "L ")) for l in lines)
    assert sum(l.startswith("I ") for l in lines) > 400  # the IMU carries on ...
    assert f.imu_rate == 416 and duties(pwms) == [20 * 65535 // 100] * 3  # ... and so do the emitters
    # an IMU driver without read_temp_c100 (older firmware files): no temperatures, no errors
    clock2 = SimClock()
    real = lsm.LSM6DSOX(FakeLsmBus(clock2))
    plain = types.SimpleNamespace(name=real.name, gyro_scale=real.gyro_scale, accel_scale=real.accel_scale,
                                  set_rate=real.set_rate, data_ready=real.data_ready, read_raw=real.read_raw)
    f2, out2, _, _ = make_fw(clock=clock2, imu=plain)
    f2.feed("IMU 416\n")
    out2.take()
    t_end = clock2.t + 1_200_000
    while clock2.t < t_end:
        clock2.advance(40)
        f2.process()
    lines = out2.take()
    assert lines and all(l.startswith("I ") for l in lines)


def test_imu_rate_validation_and_registers():
    bus = FakeLsmBus(SimClock())
    f, out, _, _ = make_fw(imu=lsm.LSM6DSOX(bus))
    f.feed("IMU 100\nIMU\nIMU fast\n")
    lines = out.take()
    assert lines[0].startswith("E IMU unsupported rate 100")
    assert lines[1].startswith("E IMU bad args") and lines[2].startswith("E IMU bad args")
    f.feed("IMU 833\n")
    assert out.take()[0].startswith("OK IMU 833 ")
    assert bus.regs[0x10] == 0x7C and bus.regs[0x11] == 0x78
    f.feed("IMU 104\nSTATUS\n")
    lines = out.take()
    assert lines[0].startswith("OK IMU 104 ") and "imu=104" in lines[1]
    assert bus.regs[0x10] == 0x4C and bus.regs[0x11] == 0x48


def test_no_imu():
    f, out, _, _ = make_fw(imu=None)
    f.feed("HELLO\nIMU 416\nIMU 0\n")
    assert out.take() == ["OK HELLO ohs-fw %s imu=NONE" % fw.FW_VERSION, "E IMU no imu", "OK IMU 0 0 0"]


def test_exceptions_turn_emitters_off():
    clock = SimClock()
    bus = FakeLsmBus(clock)
    f, out, _, pwms = make_fw(clock=clock, imu=lsm.LSM6DSOX(bus))
    f.feed("EMIT 30 30 30\nIMU 416\n")
    out.take()
    bus.fail_reads = True  # I2C dies mid-stream
    f.process()
    lines = out.take()
    assert lines[0].startswith("E imu-read OSError") and lines[1] == "L emitters-off exception"
    assert duties(pwms) == [0, 0, 0] and f.imu_rate == 0
    # exception inside a command handler: reply names the command, emitters off
    bad = [FakePWM(), FakePWM(fail_nonzero=True), FakePWM()]
    f2, out2, _, _ = make_fw(pwms=bad)
    f2.feed("EMIT 10 10 10\n")
    assert out2.take()[0].startswith("E EMIT RuntimeError") and duties(bad) == [0, 0, 0]
    # exception in the periodic work (button read)
    class BadPin:
        level = 1

        def value(self):
            if self.level == 0:
                raise RuntimeError("gpio")
            return 1

    pin = BadPin()
    f3, out3, _, pwms3 = make_fw(button=pin)
    f3.feed("EMIT 5 5 5\n")
    out3.take()
    pin.level = 0
    assert f3.process() is False  # never raises
    assert out3.take()[0].startswith("E internal RuntimeError") and duties(pwms3) == [0, 0, 0]


# --------------------------------------------------------------------------- run() with stub modules
class StubStdin:
    """Serves ``script``; with ``interrupt_after`` idle polls, then delivers Ctrl-C."""

    def __init__(self, script: str, interrupt_after: int | None = None) -> None:
        self.chars = list(script)
        self.interrupt_after = interrupt_after
        self._idle = 0

    def pending(self) -> bool:
        if self.chars:
            return True
        self._idle += 1
        return self.interrupt_after is not None and self._idle > self.interrupt_after

    def read(self, n: int = 1) -> str:
        if not self.chars:
            if self.interrupt_after is not None:
                raise KeyboardInterrupt
            return ""
        return self.chars.pop(0)


def make_stubs(clock: SimClock, stdin: StubStdin, imu_present: bool = True):
    pins: dict[int, dict] = {}
    pwms: list = []
    i2cs: list = []

    class Pin:
        IN, OUT, PULL_UP = 0, 1, 1

        def __init__(self, id, mode=-1, pull=-1, value=None):
            self.id = id
            cfg = pins.setdefault(id, {})
            if mode != -1:
                cfg.update(mode=mode, pull=pull, value=value)

        def value(self, v=None):
            return 1  # trigger released

    class PWM(FakePWM):
        def __init__(self, pin):
            super().__init__(pin.id)
            pwms.append(self)

    class I2C:
        def __init__(self, id, sda=None, scl=None, freq=None):
            i2cs.append(dict(id=id, sda=sda.id, scl=scl.id, freq=freq))
            self.bus = FakeLsmBus(clock, address=0x6A if imu_present else 0x55)

        def __getattr__(self, name):
            return getattr(self.bus, name)

    class Poll:
        def register(self, obj, mask):
            self.obj, self.mask = obj, mask

        def poll(self, timeout=-1):
            return [(self.obj, self.mask)] if stdin.pending() else []

    def ticks_us():
        clock.advance(20)  # every clock read costs a little loop time
        return clock.t & MASK30

    machine = types.SimpleNamespace(Pin=Pin, PWM=PWM, I2C=I2C)
    time_mod = types.SimpleNamespace(ticks_us=ticks_us, ticks_diff=mp_ticks_diff)
    select_mod = types.SimpleNamespace(POLLIN=1, poll=Poll)
    return machine, time_mod, select_mod, pins, pwms, i2cs


@pytest.fixture
def fw_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(FW_DIR))  # run() does "import lsm6dsox" like on the device
    monkeypatch.delitem(sys.modules, "lsm6dsox", raising=False)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    yield
    sys.modules.pop("lsm6dsox", None)


def test_run_loop_with_stub_modules(fw_on_path):
    clock = SimClock(MASK30 - 50_000)  # ticks wrap during the run
    stdin = StubStdin("HELLO\nEMIT 10 20 30\nSTATUS\nIMU 416\n")
    machine, time_mod, select_mod, pins, pwms, i2cs = make_stubs(clock, stdin)
    out = Out()
    stdout = types.SimpleNamespace(write=out)
    f = fw.run(machine, time_mod, select_mod, stdin, stdout, max_iterations=3000)
    assert sorted(p.pin for p in pwms) == [2, 3, 6]
    assert all(p.f == 10_000 for p in pwms)
    assert pins[15]["mode"] == machine.Pin.IN and pins[15]["pull"] == machine.Pin.PULL_UP
    assert i2cs == [dict(id=0, sda=4, scl=5, freq=400_000)]
    lines = out.lines
    assert lines[0] == "L boot ohs-fw %s imu=LSM6DSOX" % fw.FW_VERSION
    assert lines[1] == "OK HELLO ohs-fw %s imu=LSM6DSOX" % fw.FW_VERSION
    assert lines[2] == "OK EMIT 10 20 30" and lines[3].startswith("S emit=10,20,30 imu=0 btn=0 up=")
    assert lines[4].startswith("OK IMU 416 ")
    imu_t = [int(l.split()[1]) for l in lines[5:] if l.startswith("I ")]
    assert len(imu_t) > 50 and all(b > a for a, b in zip(imu_t, imu_t[1:]))  # monotonic across the wrap
    assert f.levels == [0, 0, 0] and all(p.duty == 0 for p in pwms)  # loop exit turns emitters off


def test_run_ctrl_c_turns_emitters_off_and_imu_optional(fw_on_path):
    clock = SimClock(0)
    stdin = StubStdin("EMIT 100 100 100\n", interrupt_after=5)
    machine, time_mod, select_mod, _, pwms, _ = make_stubs(clock, stdin, imu_present=False)
    out = Out()
    with pytest.raises(KeyboardInterrupt):
        fw.run(machine, time_mod, select_mod, stdin, types.SimpleNamespace(write=out))
    assert "OK EMIT 100 100 100" in out.lines
    assert out.lines[0] == "L boot ohs-fw %s imu=NONE" % fw.FW_VERSION
    assert out.lines[1].startswith("L imu init failed: no LSM6DSOX")
    assert all(p.duty == 0 for p in pwms)


# --------------------------------------------------------------------------- MicroPython compatibility lint
ALLOWED_IMPORTS = {"sys", "struct", "math", "machine", "select", "time", "gc", "lsm6dsox"}


@pytest.mark.parametrize("filename", ["main.py", "lsm6dsox.py"])
def test_sources_avoid_cpython_only_features(filename):
    """Guard against syntax/modules MicroPython 1.2x lacks (annotations, f-strings, typing, ...)."""
    tree = ast.parse((FW_DIR / filename).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        assert not isinstance(node, (ast.JoinedStr, ast.AnnAssign, ast.AsyncFunctionDef, ast.Match)), node
        if isinstance(node, ast.FunctionDef):
            args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            assert node.returns is None and all(a.annotation is None for a in args), node.name
        if isinstance(node, ast.Import):
            assert all(a.name in ALLOWED_IMPORTS for a in node.names), [a.name for a in node.names]
        if isinstance(node, ast.ImportFrom):
            assert node.module in ALLOWED_IMPORTS, node.module
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            assert not any(isinstance(e, ast.Starred) for e in node.elts), "star-unpacking in a display"
