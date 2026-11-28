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


def five_pwms():
    return [FakePWM() for _ in range(5)]


def test_emit_with_a_level_per_laser():
    """The 4-laser build (1.2.0): EMIT with five levels sets each laser on its own channel; with three
    the second lasers follow V and H, as before (an older host, or a board wiring each pair together)."""
    f, out, _, pwms = make_fw(pwms=five_pwms())
    assert duties(pwms) == [0] * 5  # all off at boot
    f.feed("EMIT 40 50 0\n")
    assert out.take() == ["OK EMIT 40 50 0"]
    assert duties(pwms) == [d * 65535 // 100 for d in (40, 50, 0, 40, 50)]  # the second lasers follow
    f.feed("STATUS\n")
    assert out.take()[0].startswith("S emit=40,50,0 imu=0 ")  # unchanged while they follow
    f.feed("EMIT 0 100 0 80 -3\n")
    assert out.take() == ["OK EMIT 0 100 0 80 0"]  # clamped like the others
    assert duties(pwms) == [0, 65535, 0, 80 * 65535 // 100, 0]
    assert f.levels == [0, 100, 0] and f.levels2 == [80, 0]
    f.feed("STATUS\n")
    assert out.take()[0].startswith("S emit=0,100,0,80,0 imu=0 ")
    f.feed("EMIT 1 2 3 4\nEMIT 1 2 3 4 x\nEMIT 1 2 3 4 5 6\n")
    lines = out.take()
    assert len(lines) == 3 and all(l.startswith("E EMIT bad args (EMIT <v> <h> <d> [<v2> <h2>]") for l in lines)
    assert f.levels == [0, 100, 0] and f.levels2 == [80, 0]  # unchanged by bad commands
    f.feed("EMIT 0 0 0 0 0\n")
    assert out.take() == ["OK EMIT 0 0 0 0 0"] and not f.emitters_on()


def test_a_second_laser_alone_keeps_the_watchdog_and_the_safety_paths():
    f, out, clock, pwms = make_fw(pwms=five_pwms())
    f.feed("EMIT 0 0 0 0 60\n")
    out.take()
    assert f.emitters_on()  # only H2 is on, and that counts
    clock.advance(1_500_001)
    f.process("")
    assert duties(pwms) == [0] * 5 and f.levels2 == [0, 0]
    assert out.take() == ["L emitters-off watchdog"]
    f.feed("EMIT 0 0 0 70 0\n")
    out.take()
    f.all_off()
    assert duties(pwms) == [0] * 5 and not f.emitters_on()


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
    assert sorted(p.pin for p in pwms) == [0, 1, 2, 3, 6]  # V, H, dots, and the 4-laser build's own V2 / H2 channels
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


def test_run_on_an_esp32_uses_its_pin_map(fw_on_path):
    # Waveshare ESP32-S3-Pico: the same physical pins as on the Pico (4, 5, 9, 20, 6, 7), other GPIO numbers
    clock = SimClock(0)
    stdin = StubStdin("HELLO\nEMIT 10 20 30\n")
    machine, time_mod, select_mod, pins, pwms, i2cs = make_stubs(clock, stdin)
    out = Out()
    f = fw.run(machine, time_mod, select_mod, stdin, types.SimpleNamespace(write=out), max_iterations=300,
               platform="esp32")
    assert [p.pin for p in pwms] == [13, 14, 17, 11, 12]  # V laser, H laser, dots, second V and H lasers
    assert pins[40]["mode"] == machine.Pin.IN and pins[40]["pull"] == machine.Pin.PULL_UP
    assert i2cs == [dict(id=0, sda=15, scl=16, freq=400_000)]
    assert out.lines[0] == "L boot ohs-fw %s imu=LSM6DSOX" % fw.FW_VERSION  # the same first line as on a Pico
    assert out.lines[1] == "L board esp32"
    assert "OK HELLO ohs-fw %s imu=LSM6DSOX" % fw.FW_VERSION in out.lines and "OK EMIT 10 20 30" in out.lines
    assert f.levels == [0, 0, 0] and all(p.duty == 0 for p in pwms)
    assert fw.pin_map("rp2") == fw.pin_map("win32")  # anything that is not an ESP32 gets the Pico's pins


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


# --------------------------------------------------------------------------- emitters in turn (ALT, 1.3.0)
class LogPWM(FakePWM):
    """A PWM that records every duty write with the simulated time: (t_us, duty)."""

    def __init__(self, clock: SimClock, fail_nonzero: bool = False) -> None:
        super().__init__(fail_nonzero=fail_nonzero)
        self.clock = clock
        self.writes: list[tuple[int, int]] = []

    def duty_u16(self, d=None):
        if d is None:
            return self.duty
        if d and self.fail_nonzero:
            raise RuntimeError("pwm fault")
        self.writes.append((self.clock.t, d))
        self.duty = d


def make_alt_fw(channels: int = 3, t0: int = 1_000_000, imu=None):
    clock = SimClock(t0)
    pwms = [LogPWM(clock) for _ in range(channels)]
    f, out, _, _ = make_fw(clock=clock, pwms=pwms, imu=imu)
    return f, out, clock, pwms


def lit(pwms) -> tuple[bool, bool]:
    """(dots on, any laser on) from the PWM duties (dots are channel 2, lasers all the others)."""
    dots = bool(pwms[2].duty)
    lasers = any(p.duty for i, p in enumerate(pwms) if i != 2)
    return dots, lasers


def alt_reply(line: str, word: str = "ALT") -> tuple[int, int, int, int]:
    """(cycle start in ns, period_ns, dots_us, lasers_us) from ``OK <word> <us> <ns> <p> <d> <l>``."""
    parts = line.split()
    assert parts[:2] == ["OK", word], line
    assert len(parts) == 7, line
    us, ns, p, d, la = (int(x) for x in parts[2:])
    assert 0 <= ns < 1000
    return us * 1000 + ns, p, d, la


def drive(f, clock, t_end: int, step: int = 5, hb_every: int = 500_000):
    """Run the main loop in ``step`` us steps until ``t_end``, feeding the watchdog. Checks that the
    dots and the lasers are never on together and returns the switches: [(t_us, "dots"|"lasers", on)]."""
    events = []
    state = lit(f.emitters)
    next_hb = clock.t + hb_every
    while clock.t < t_end:
        clock.advance(step)
        if clock.t >= next_hb:
            f.feed("HB\n")
            next_hb = clock.t + hb_every
        f.process("")
        now = lit(f.emitters)
        assert not (now[0] and now[1]), "dots and lasers on together at %d" % clock.t
        if now[0] != state[0]:
            events.append((clock.t, "dots", now[0]))
        if now[1] != state[1]:
            events.append((clock.t, "lasers", now[1]))
        state = now
    return events


def expected_edges(start_ns: int, period_ns: int, dots_us: int, lasers_us: int, cycles: int):
    """Exact switch times in ns for ``cycles`` cycles: [(t_ns, group, on)]."""
    out = []
    for c in range(cycles):
        a = start_ns + 2 * c * period_ns
        b = a + period_ns
        if dots_us:
            out += [(a, "dots", True), (a + dots_us * 1000, "dots", False)]
        if lasers_us:
            out += [(b, "lasers", True), (b + lasers_us * 1000, "lasers", False)]
    return out


def check_edges(events, edges, tol_us: float) -> None:
    assert [(g, on) for _, g, on in events] == [(g, on) for _, g, on in edges]
    for (t, g, on), (e_ns, _, _) in zip(events, edges):
        err = t - e_ns / 1000.0
        assert -1e-9 <= err < tol_us, (t, g, on, e_ns, err)  # never early, at most tol_us late


def test_alt_windows_hold_their_place_over_ten_thousand_cycles():
    """A period that is not a whole number of microseconds (60 fps): after 10 000 cycles (333 s) every
    switch is still within 2 us plus the loop's lateness of the exact time. Rounding the period to a
    whole microsecond would be 3.3 ms off by then."""
    f, out, clock, pwms = make_alt_fw(channels=3)
    f.feed("EMIT 40 50 60\n")
    assert out.take() == ["OK EMIT 40 50 60"]
    f.feed("ALT 16666667 3000 2500\n")
    start_ns, p, d, la = alt_reply(out.take()[0])
    assert (p, d, la) == (16666667, 3000, 2500)
    assert start_ns == clock.t * 1000  # the first cycle starts when the command arrives
    rng = random.Random(7)
    cycles = 10_000
    edges = expected_edges(start_ns, p, d, la, cycles)
    events = []
    worst = 0
    state = lit(pwms)
    for e_ns, group, on in edges:
        ideal = -(-e_ns // 1000)  # the first whole microsecond at or after the edge
        if ideal - 1 > clock.t:
            clock.t = ideal - 1  # just before the edge: nothing may have switched yet
            f.process("")
            assert lit(pwms) == state, (clock.t, group, on)
        jitter = rng.randrange(0, 4)  # the loop comes round 0..3 us late
        worst = max(worst, jitter)
        clock.t = max(clock.t, ideal + jitter)
        if group == "dots" and on:
            f.feed("HB\n")  # keep the watchdog fed, once a cycle
        f.process("")
        now = lit(pwms)
        assert not (now[0] and now[1])
        if group == "dots":
            assert now[0] == on and not now[1]
            if on:
                assert pwms[2].duty == 60 * 65535 // 100 and pwms[0].duty == 0 and pwms[1].duty == 0
        else:
            assert now[1] == on and not now[0]
            if on:
                assert pwms[0].duty == 40 * 65535 // 100 and pwms[1].duty == 50 * 65535 // 100
        events.append((clock.t, group, on))
        state = now
    check_edges(events, edges, tol_us=2 + worst)
    last = events[-1][0] - edges[-1][0] / 1000.0
    assert last < 2 + worst  # the last switch, 333 s on, is as exact as the first
    f.feed("STATUS\n")
    st = out.take()[0]
    assert " alt=16666667,3000,2500 " in st
    late = [int(x) for x in st.split(" late=")[1].split()[0].split(",")]
    assert 0 <= late[0] <= late[1] <= worst + 1
    assert " sw=%d " % (4 * cycles) in st and st.endswith(" miss=0")
    f.feed("STATUS\n")
    st = out.take()[0]
    assert " late=0,0 sw=0 miss=0" in st  # counted afresh after each STATUS


def test_alt_status_line_and_steady_status_unchanged():
    f, out, clock, _ = make_alt_fw()
    f.feed("EMIT 10 0 20\nSTATUS\n")
    lines = out.take()
    assert lines[1] == "S emit=10,0,20 imu=0 btn=0 up=0.000"  # nothing new without ALT
    f.feed("ALT 10000000 2000 1000\n")
    out.take()
    drive(f, clock, clock.t + 99_000, step=7)  # five cycles, the sixth not yet begun
    f.feed("STATUS\n")
    st = out.take()[0]
    assert st.startswith("S emit=10,0,20 imu=0 btn=0 up=0.099 alt=10000000,2000,1000 late=")
    assert st.endswith(" sw=20 miss=0")  # 5 cycles, 4 switches each
    f.feed("ALT 0\nSTATUS\n")
    assert out.take() == ["OK ALT 0", "S emit=10,0,20 imu=0 btn=0 up=0.099"]


def test_alt_stop_goes_back_to_steady_levels():
    f, out, clock, pwms = make_alt_fw(channels=5)
    f.feed("EMIT 10 20 30 40 50\n")
    out.take()
    f.feed("ALT 10000000 2000 2000\n")
    out.take()
    drive(f, clock, clock.t + 1_000, step=5)
    assert lit(pwms) == (True, False)  # inside the dots window
    assert [p.duty for p in pwms] == [0, 0, 30 * 65535 // 100, 0, 0]
    f.feed("ALT 0\n")
    assert out.take() == ["OK ALT 0"]
    assert [p.duty for p in pwms] == [x * 65535 // 100 for x in (10, 20, 30, 40, 50)]
    for _ in range(10_000):  # 50 ms: steady again, no more switching
        clock.advance(5)
        f.process("")
        assert [p.duty for p in pwms] == [x * 65535 // 100 for x in (10, 20, 30, 40, 50)]
    f.feed("ALT 0\n")
    assert out.take() == ["OK ALT 0"]  # stopping twice is fine


def test_alt_nudge_and_new_period_keep_the_phase_otherwise():
    """ALTN and ALTP take effect at the next cycle start and answer with it; every later window keeps
    its place on the shifted (or re-timed) grid, and the cycle running meanwhile is not disturbed."""
    f, out, clock, pwms = make_alt_fw()
    f.feed("EMIT 30 30 30\n")
    out.take()
    p = 10_000_000
    f.feed("ALT %d 2000 1500\n" % p)
    s0, _, _, _ = alt_reply(out.take()[0])
    ev = [(s0 // 1000, "dots", True)]  # the first window opens with the command itself
    ev += drive(f, clock, s0 // 1000 + 1_000, step=3)  # into the first dots window
    f.feed("ALTN 500\n")
    s1, p1, d1, l1 = alt_reply(out.take()[0], "ALTN")
    assert s1 == s0 + 2 * p + 500_000 and (p1, d1, l1) == (p, 2000, 1500)
    f.feed("ALTN -200\n")  # nudges before the boundary add up
    s1b, _, _, _ = alt_reply(out.take()[0], "ALTN")
    assert s1b == s0 + 2 * p + 300_000
    # into the dark gap after cycle 2's lasers window: cycle 3 is set up but has not begun
    ev += drive(f, clock, s1b // 1000 + 2 * p // 1000 + 12_000, step=3)
    f.feed("ALTP 12000001\n")
    s3, p3, _, _ = alt_reply(out.take()[0], "ALTP")
    assert s3 == s1b + 2 * 2 * p and p3 == 12000001  # cycle 3 starts where it would have; new period after
    ev += drive(f, clock, s3 // 1000 + 3 * 2 * p3 // 1000 - 1000, step=3)  # the gap before cycle 6
    f.feed("ALTN 250\n")
    s6, _, _, _ = alt_reply(out.take()[0], "ALTN")
    assert s6 == s3 + 3 * 2 * p3 + 250_000  # the very next cycle start moves
    ev += drive(f, clock, s6 // 1000 + 1000, step=3)  # inside cycle 6's dots window
    f.feed("ALTP 9000000\n")
    s7, p7, _, _ = alt_reply(out.take()[0], "ALTP")
    assert s7 == s6 + 2 * p3 and p7 == 9_000_000  # cycle 6 keeps its period, cycle 7 on the new one
    ev += drive(f, clock, s7 // 1000 + 2 * 2 * p7 // 1000 - 1000, step=3)
    want = expected_edges(s0, p, 2000, 1500, 1) + expected_edges(s1b, p, 2000, 1500, 2) + expected_edges(
        s3, p3, 2000, 1500, 3) + expected_edges(s6, p3, 2000, 1500, 1) + expected_edges(s7, p7, 2000, 1500, 2)
    check_edges(ev, want, tol_us=2 + 3)


def test_alt_period_change_and_nudge_need_a_running_switching():
    f, out, _, _ = make_alt_fw()
    f.feed("ALTN 100\nALTP 10000000\n")
    lines = out.take()
    assert lines[0].startswith("E ALTN not running") and lines[1].startswith("E ALTP not running")


def test_alt_bad_args_are_refused():
    f, out, clock, pwms = make_alt_fw()
    f.feed("EMIT 10 10 10\n")
    out.take()
    for cmd in ("ALT", "ALT 1", "ALT x 1 2", "ALT 10000000 1 2 3", "ALT 10000000 -1 0", "ALT 10000000 0 -5",
                "ALT 1999999 0 0", "ALT 100000001 0 0", "ALT 10000000 10000 0", "ALT 10000000 0 10001"):
        f.feed(cmd + "\n")
        lines = out.take()
        assert len(lines) == 1 and lines[0].startswith("E ALT "), (cmd, lines)
    assert not f.alt and [p.duty for p in pwms] == [6553] * 3  # nothing changed
    f.feed("ALT 2000000 1999 0\nALT 100000000 0 99999\n")  # the limits themselves are fine
    lines = out.take()
    assert lines[0].startswith("OK ALT ") and lines[1].startswith("OK ALT ")
    f.feed("ALT 10000000 3000 3000\n")
    s0, _, _, _ = alt_reply(out.take()[0])
    for cmd in ("ALTN", "ALTN x", "ALTN 1 2", "ALTN 10001", "ALTN -10001", "ALTP", "ALTP 1999999",
                "ALTP 100000001", "ALTP 3000000", "ALTP a"):
        f.feed(cmd + "\n")
        lines = out.take()
        word = cmd.split()[0]
        assert len(lines) == 1 and lines[0].startswith("E %s " % word), (cmd, lines)
    f.feed("ALTN 0\n")
    s1, p1, _, _ = alt_reply(out.take()[0], "ALTN")
    assert s1 == s0 + 2 * 10_000_000 and p1 == 10_000_000  # the refused commands changed nothing


def test_alt_emit_levels_are_used_inside_the_windows():
    """EMIT while switching changes the levels inside the windows; five levels drive the 4-laser build's
    own channels in the laser window; the dots window lights only the dots."""
    f, out, clock, pwms = make_alt_fw(channels=5)
    f.feed("ALT 10000000 4000 4000\n")
    s0, _, _, _ = alt_reply(out.take()[0])
    t = s0 // 1000
    drive(f, clock, t + 1000)
    assert [p.duty for p in pwms] == [0] * 5  # levels 0: dark windows, but the switching runs
    f.feed("EMIT 10 20 30 40 50\n")
    assert out.take() == ["OK EMIT 10 20 30 40 50"]
    assert [p.duty for p in pwms] == [0, 0, 30 * 65535 // 100, 0, 0]  # at once, inside the dots window
    drive(f, clock, t + 10_000 + 1000)
    assert [p.duty for p in pwms] == [x * 65535 // 100 for x in (10, 20, 0, 40, 50)]  # the laser window
    f.feed("EMIT 70 0 80\n")
    out.take()
    assert [p.duty for p in pwms] == [x * 65535 // 100 for x in (70, 0, 0, 70, 0)]  # V2, H2 follow V, H
    drive(f, clock, t + 10_000 + 6000)
    assert [p.duty for p in pwms] == [0] * 5  # between windows everything is off
    drive(f, clock, t + 20_000 + 1000)
    assert [p.duty for p in pwms] == [0, 0, 80 * 65535 // 100, 0, 0]
    f.feed("STATUS\n")
    assert out.take()[0].startswith("S emit=70,0,80 imu=0 ")  # the levels as set, not the momentary duty


def test_alt_never_lights_dots_and_lasers_together_even_when_late():
    """A loop that comes round late (here up to 7 ms at a time) shortens or misses windows but never
    lets the two overlap, and the grid stays where it was."""
    f, out, clock, pwms = make_alt_fw(channels=5)
    f.feed("EMIT 50 50 50\n")
    out.take()
    f.feed("ALT 8333333 3000 3000\n")
    s0, p, _, _ = alt_reply(out.take()[0])
    rng = random.Random(3)
    t_end = clock.t + 2_000_000
    nxt = clock.t + 400_000
    while clock.t < t_end:
        clock.advance(rng.choice((5, 50, 500, 2500, 7000)))
        if clock.t >= nxt:
            f.feed("HB\n")
            nxt = clock.t + 400_000
        f.process("")
        d, la = lit(pwms)
        assert not (d and la)
    # a long stall: 1.2 s without a loop iteration (the host still talks)
    clock.advance(1_200_000)
    f.feed("HB\n")
    f.process("")
    assert not all(lit(pwms))
    f.feed("STATUS\n")
    st = out.take()[0]
    miss = int(st.split(" miss=")[1])
    assert miss > 100  # the windows the stall swallowed are counted
    # afterwards the windows land on the original grid again
    ev = drive(f, clock, clock.t + 100_000, step=3)
    on = [t for t, g, o in ev if g == "dots" and o]
    assert on
    for t in on:
        k = (t * 1000 - s0) / (2 * p)
        assert abs(k - round(k)) * 2 * p < 4_000  # within 4 us of a cycle start of the first grid


def test_alt_watchdog_and_exceptions_stop_switching_with_everything_off():
    f, out, clock, pwms = make_alt_fw(channels=5)
    f.feed("EMIT 20 20 20\nALT 10000000 3000 3000\n")
    out.take()
    t = clock.t
    drive(f, clock, t + 1_000_000, step=50)  # fed by heartbeats
    assert f.alt
    out.take()
    while clock.t < t + 1_000_000 + 1_600_000:  # the host goes silent
        clock.advance(50)
        f.process("")
        assert not all(lit(pwms))
    assert out.take() == ["L emitters-off watchdog"]
    assert not f.alt and [p.duty for p in pwms] == [0] * 5 and f.levels == [0, 0, 0]
    for _ in range(1000):
        clock.advance(50)
        f.process("")
    assert [p.duty for p in pwms] == [0] * 5  # stays off: no window opens again
    f.feed("STATUS\n")
    assert " alt=" not in out.take()[0]
    # with zero levels the switching is still stopped by the watchdog (the host learns it stopped)
    f.feed("ALT 10000000 3000 3000\n")
    out.take()
    clock.advance(1_600_000)
    f.process("")
    assert out.take() == ["L emitters-off watchdog"] and not f.alt
    # an exception while switching: everything off, switching stopped, the host is told
    f2, out2, clock2, pwms2 = make_alt_fw(channels=3)
    f2.feed("EMIT 10 10 10\nALT 10000000 3000 3000\n")
    out2.take()
    drive(f2, clock2, clock2.t + 5000)  # past the dots window
    pwms2[2].fail_nonzero = True
    clock2.advance(16_000)  # into the next dots window (20 to 23 ms)
    f2.process("")  # which fails to switch on
    lines = out2.take()
    assert lines[0].startswith("E internal RuntimeError") and lines[1] == "L emitters-off exception"
    assert not f2.alt and [p.duty for p in pwms2] == [0, 0, 0]


def _session_without_alt() -> str:
    """A scripted session with no ALT command: every output line and every PWM write, as text."""
    clock = SimClock(2_000_000)
    pwms = [LogPWM(clock) for _ in range(5)]
    pin = FakePin(1)
    bus = FakeLsmBus(clock)
    bus.regs[0x20:0x22] = struct.pack("<h", 1600)
    out = Out()
    f = fw.Firmware(pwms, pin, lsm.LSM6DSOX(bus), clock, out)
    regs_after_reset = bytes(bus.regs)
    bus.regs[0x20:0x22] = struct.pack("<h", 1600)

    def run_for(us: int, step: int = 40) -> None:
        t_end = clock.t + us
        while clock.t < t_end:
            clock.advance(step)
            f.process("")

    f.feed("HELLO\nSTATUS\nEMIT 10 20 30\nEMIT 1 2 3 4 5\nEMIT 1 2\nFOO\n")
    run_for(10_000)
    f.feed("IMU 104\n")
    run_for(60_000)
    pin.level = 0
    run_for(40_000)
    f.feed("STATUS\nEMIT 0 0 0 0 60\n")
    run_for(1_600_000, step=400)  # the watchdog fires
    pin.level = 1
    run_for(40_000)
    f.feed("TIME\nIMU 0\nSTATUS\n" + "X" * 80 + "\n")
    run_for(5_000)
    lines = [ln.replace(fw.FW_VERSION, "<ver>") for ln in out.lines]
    writes = ["%d %d %d" % (i, t, d) for i, p in enumerate(pwms) for t, d in p.writes]
    return "\n".join(lines + writes + [regs_after_reset.hex()])


def test_without_alt_the_firmware_behaves_exactly_as_before():
    """Recorded on firmware 1.2.0, before ALT existed: the same session gives the same bytes."""
    import hashlib

    text = _session_without_alt()
    digest = hashlib.sha256(text.encode("ascii")).hexdigest()
    assert digest == "03439fa1c6b95bfdd192774b49f660ebc917458c6ddf89820c4b82543f80bae8", (digest, len(text.splitlines()))


class TimedStdin(StubStdin):
    """Serves each (t_us, text) once the simulated clock has reached t_us."""

    def __init__(self, clock: SimClock, items) -> None:
        super().__init__("")
        self.clock = clock
        self.items = list(items)

    def pending(self) -> bool:
        while self.items and self.clock.t >= self.items[0][0]:
            self.chars += list(self.items.pop(0)[1])
        return bool(self.chars)


def test_run_loop_switches_on_time_while_the_imu_streams(fw_on_path, monkeypatch):
    """The real main loop with the I2C costs of the fake IMU (90 / 330 us per read) and a 3 ms garbage
    collection: the IMU reads and the collection give way to a switch that is due soon, so the
    switches stay within about a tenth of a millisecond, while the IMU still delivers its samples."""
    import gc

    clock = SimClock(10_000_000)
    t0 = clock.t
    script = [(t0, "EMIT 10 20 30\nIMU 833\n"), (t0 + 20_000, "ALT 8333333 2000 2000\n")]
    script += [(t0 + 20_000 + k * 200_000, "HB\n") for k in range(1, 10)]
    script += [(t0 + 1_900_000, "STATUS\n")]
    stdin = TimedStdin(clock, script)
    machine, time_mod, select_mod, _, pwms, _ = make_stubs(clock, stdin)
    overlaps = []

    class CheckedPWM(machine.PWM):
        """Checks at every switch-on once ALT has arrived that the dots (the third PWM made) and a laser
        are never on together. (Before it, the steady EMIT lights both, as it should.)"""

        def duty_u16(self, d=None):
            r = super().duty_u16(d)
            if d and clock.t >= 10_020_000 and len(pwms) == 5 and pwms[2].duty and any(
                    p.duty for i, p in enumerate(pwms) if i != 2):
                overlaps.append(clock.t)
            return r

    machine.PWM = CheckedPWM
    collects = []
    calls = [0]

    def mem_free():  # now and then the heap runs low
        calls[0] += 1
        return 0 if calls[0] % 50 == 0 else 1 << 20

    def slow_collect():
        collects.append(clock.t)
        clock.advance(3000)

    monkeypatch.setattr(gc, "mem_free", mem_free, raising=False)
    monkeypatch.setattr(gc, "collect", slow_collect)
    out = Out()
    f = fw.run(machine, time_mod, select_mod, stdin, types.SimpleNamespace(write=out), max_iterations=30_000)
    assert clock.t > 12_000_000 and not overlaps
    st = [ln for ln in out.lines if ln.startswith("S ")][0]
    late = [int(x) for x in st.split(" late=")[1].split()[0].split(",")]
    sw = int(st.split(" sw=")[1].split()[0])
    assert sw >= 4 * 100  # about 1.9 s at 60 cycles a second, four switches each
    assert late[1] < 150, st  # worst lateness in us
    assert int(st.split(" miss=")[1]) == 0
    t_status = int(st.split(" up=")[1].split()[0].replace(".", ""))
    assert t_status > 1_800  # ms: STATUS came after ~1.9 s
    n_imu = sum(1 for ln in out.lines if ln.startswith("I ") and int(ln.split()[1]) < t0 + 1_800_000)
    assert n_imu > 0.6 * 833 * 1.8  # the IMU still streams
    assert len(collects) > 10  # and the garbage is still collected, in the slack
    assert all(p.duty == 0 for p in pwms) and not f.alt  # loop exit: off


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
