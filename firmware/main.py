"""OpenHandScan Pico firmware (MicroPython 1.2x on Raspberry Pi Pico RP2040 / Pico 2 RP2350).

Also runs unchanged on an ESP32 with MicroPython (written for the Waveshare ESP32-S3-Pico): the
pin map is picked by ``sys.platform`` (see PINS). Not yet tried on a real ESP32.

Serial protocol (software/ARCHITECTURE.md section 5A), ASCII lines over USB CDC (stdin/stdout):

    HELLO             -> OK HELLO ohs-fw <ver> imu=<LSM6DSOX|NONE>
    EMIT <v> <h> <d>  -> OK EMIT <v> <h> <d>     percent PWM, clamped to 0..100; the second
                         lasers (V2, H2) follow V and H, as on a board that wires each pair together
    EMIT <v> <h> <d> <v2> <h2>
                      -> OK EMIT <v> <h> <d> <v2> <h2>   each laser on its own (1.2.0+; the 4-laser
                         build with a channel per laser)
    IMU <rate_hz>     -> OK IMU <rate> <gyro_rad_per_lsb> <accel_ms2_per_lsb>   (0 = stop;
                         104/208/416/833 Hz)
    TIME              -> T <t_us>
    HB                -> (no reply; only keeps the emitter watchdog alive)
    ALT <period_ns> <dots_us> <lasers_us>
                      -> OK ALT <start_us> <start_ns> <period_ns> <dots_us> <lasers_us>   (1.3.0+) the
                         dots and the lasers in turn, timed by the Pico's clock (see below); the
                         reply's start is the first cycle's, which begins at once
    ALT 0             -> OK ALT 0     stop switching: back to the steady EMIT levels
    ALTN <shift_us>   -> OK ALTN <start_us> <start_ns> <period_ns> <dots_us> <lasers_us>   shift the
                         phase once (signed, at most one period) from the next cycle start on
    ALTP <period_ns>  -> OK ALTP <start_us> <start_ns> <period_ns> <dots_us> <lasers_us>   a new period
                         from the next cycle start on, which stays where it was
    STATUS            -> S emit=<v>,<h>,<d>[,<v2>,<h2>] imu=<rate> btn=<0|1> up=<s> [temp=<degC>]
                         [alt=<period_ns>,<dots_us>,<lasers_us> late=<mean_us>,<worst_us> sw=<n> miss=<n>]
                         (v2, h2 only while they differ from v, h; alt= and after only while switching)
    other             -> E unknown <cmd>
    async             -> I <t_us> <gx> <gy> <gz> <ax> <ay> <az>   raw int16 counts, IMU frame
                         B <t_us> <0|1>                            debounced trigger, 1 = pressed
                         C <t_us> <centi_degC>                     IMU chip temperature (1.1.0+)
                         E <msg>  /  L <msg>                       error / log

Choices where the contract leaves room (all documented for the host client):
* Commands are case-insensitive. ``\\n``, ``\\r`` and ``\\r\\n`` all end a line. Lines longer than
  64 characters are dropped with ``E line too long``.
* An error caused by a command names that command as the second word (``E EMIT bad args ...``,
  ``E IMU unsupported rate 100 ...``), so the host can match it to its request.
* ``IMU 0`` always succeeds, even without an IMU (``OK IMU 0 0 0``).
* Whenever the firmware itself turns emitters off for safety it logs ``L emitters-off <reason>``
  (reason ``watchdog`` or ``exception``), so the host learns its emitter state is stale.
* ``t_us`` is a 64-bit microsecond counter that never wraps (see UnwrappedClock). It is already
  unwrapped on the device, so the host never needs to unwrap it.
* ``C`` lines carry the IMU chip's temperature in hundredths of a degree (``C 1234 3125`` =
  31.25 degC), so recordings can relate drift to heat. They come only while the IMU streams (the
  sensor is off otherwise): right after the first sample, then about once a second, each read
  in the slack after a sample. ``STATUS`` adds ``temp=`` once a temperature has been read. A
  failed temperature read sends ``E temp-read ...`` once and stops temperature reads; the IMU
  and the emitters carry on.
* The dots and the lasers in turn (``ALT``, 1.3.0): the camera has no sync output, so the Pico
  keeps the camera's frame period on its own microsecond clock and the host steers the phase
  (``ALTN``) and the period (``ALTP``) from what the pictures show. A cycle is two periods: in the
  first the dots are on for ``dots_us`` from the period's start, in the second the lasers (all four
  channels) for ``lasers_us``; outside those windows everything is off, so the two are never on
  together. The levels are the ``EMIT`` levels, and ``EMIT`` while switching changes them. The
  period is in nanoseconds (2 to 100 ms) and every period start keeps its sub-microsecond part, so
  no rounding error builds up; a switch falls on the first whole microsecond at or after its exact
  time, plus the main loop's lateness. Each window must be shorter than the period. A cycle start is
  ``start_us + start_ns / 1000`` on the clock ``TIME`` reports; cycle n begins ``2 n period_ns``
  later. ``ALTN`` and ``ALTP`` take hold at the next cycle start, which they answer with: when a
  cycle is running, the one after it (that cycle is not disturbed); in the dark gap after the
  lasers window, the cycle about to begin. Several of them before that start add up (``ALTN``) or
  the last one counts (``ALTP``). ``ALT`` while switching starts afresh. ``ALTN``/``ALTP`` without
  a running switching answer ``E ALTN not running``.
* While switching, the main loop leaves out the IMU read and the temperature read when a switch is
  due within ALT_GUARD_US, and ``run()`` leaves out the garbage collection when one is due within
  ALT_GC_GUARD_US, so neither delays a switch. The deferred IMU sample waits in the sensor; its
  time stamp can then be up to about half that guard further off. A window the loop came round
  for only after its end is skipped whole (counted as ``miss``); a late loop never shifts the grid.
  ``STATUS`` reports the mean and the worst lateness of the switches since the last ``STATUS``
  (``late=``), their number (``sw=``) and the windows missed (``miss=``), then counts afresh.

Safety: emitters (PWM ~10 kHz on GP2 V-laser, GP3 H-laser, GP6 dot projector, and the 4-laser
build's GP0 second V-laser and GP1 second H-laser) are off at boot,
off on any exception, off when the main loop exits (including Ctrl-C), and off after 1.5 s
without a complete host line (watchdog). Each of these also stops the switching in turn (and the
watchdog stops a running switching even with every level at 0, logging ``L emitters-off watchdog``).

Structure: ``Firmware`` holds all protocol/state logic with the hardware injected (PWM-like
objects with ``duty_u16``, a Pin-like button, an LSM6DSOX-like IMU, a clock callable and a write
callable). It never imports MicroPython modules, so tests can drive it on CPython
(software/tests/test_firmware.py). ``run()`` builds the real hardware with lazy imports of
``machine``/``select``/``time`` and loops. It only starts automatically when this file runs as
the main program (``main.py`` at boot, ``mpremote run``, Thonny "Run"), never on import.
"""
import sys

FW_NAME = "ohs-fw"
FW_VERSION = "1.3.0"  # 1.1.0: IMU chip temperature (C lines, STATUS temp=); 1.2.0: a channel per laser (EMIT with 5 levels);
# 1.3.0: the dots and the lasers in turn, timed by the Pico's own clock (ALT, ALTN, ALTP)

# Pin map (README.md wiring section): GPIO numbers on the Raspberry Pi Pico
PIN_LASER_V = 2  # vertical-line laser, NPN low-side switch via 1 kOhm
PIN_LASER_H = 3  # horizontal-line laser
PIN_LASER_V2 = 0  # 4-laser build: the second vertical-line laser (left), own channel
PIN_LASER_H2 = 1  # 4-laser build: the second horizontal-line laser (low), own channel
PIN_DOTS = 6  # random-dot projector
PIN_TRIGGER = 15  # trigger button to GND, internal pull-up
PIN_SDA = 4  # I2C0 SDA (LSM6DSOX)
PIN_SCL = 5  # I2C0 SCL
I2C_ID = 0
# Per board: (V laser, H laser, dots, trigger, SDA, SCL), chosen by sys.platform.
# "esp32" is the Waveshare ESP32-S3-Pico, a board in the Pico's shape. Its map uses the SAME
# physical pins as the Pico (4, 5, 9, 20, 6, 7), so the wiring diagram is the same for both;
# only the GPIO numbers behind those pins differ. For another ESP32 board, edit this line.
PINS = {
    "rp2": (PIN_LASER_V, PIN_LASER_H, PIN_DOTS, PIN_TRIGGER, PIN_SDA, PIN_SCL),
    "esp32": (13, 14, 17, 40, 15, 16),
}
# The 4-laser build's second lasers, each on its own channel: (V2 laser, H2 laser). Pins 1 and 2 on
# both boards (on a 2-laser build they stay unconnected and do nothing).
PINS_LASER2 = {
    "rp2": (PIN_LASER_V2, PIN_LASER_H2),
    "esp32": (11, 12),
}
I2C_FREQ = 400000
PWM_FREQ_HZ = 10000

WATCHDOG_US = 1500000  # emitters off after 1.5 s without a host line
DEBOUNCE_US = 20000  # button state must be stable for 20 ms
IMU_RATES = (104, 208, 416, 833)
TEMP_PERIOD_US = 1000000  # IMU chip temperature at most once a second while the IMU streams
MAX_LINE = 64  # longest accepted command line (characters)
MAX_RX_PER_STEP = 32  # characters consumed per loop iteration (bounds command latency)
GC_MIN_FREE = 16384  # run gc.collect() in the slack after an IMU sample below this many bytes
TICKS_PERIOD = 1 << 30  # rp2 ticks_us() wraps at 2**30
ALT_PERIOD_MIN_NS = 2000000  # ALT: the shortest period accepted (2 ms, 500 frames a second)
ALT_PERIOD_MAX_NS = 100000000  # ALT: the longest (100 ms, 10 frames a second)
ALT_GUARD_US = 600  # while switching: no IMU or temperature read when a switch is due sooner than this
ALT_GC_GUARD_US = 4000  # while switching: no garbage collection when a switch is due sooner than this


def _parse_int(text):
    """int from '42', '-3' or '12.7' (truncated); None if it is not a number."""
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return int(float(text))
    except (ValueError, OverflowError):
        return None


def _clamp_pct(value):
    if value < 0:
        return 0
    if value > 100:
        return 100
    return value


def _fmt_c100(c100):
    """'31.25' from 3125, '-0.05' from -5 (hundredths of a degree, no float formatting)."""
    sign = ""
    if c100 < 0:
        sign = "-"
        c100 = -c100
    return "%s%d.%02d" % (sign, c100 // 100, c100 % 100)


class UnwrappedClock:
    """Monotonic microsecond counter that never wraps, built from ``ticks_us``/``ticks_diff``.

    ``ticks_us()`` on rp2 wraps every 2**30 us (~17.9 min). ``ticks_diff`` is exact while it is
    called at least every 2**29 us (~9 min), and the main loop calls it thousands of times per
    second, so accumulating the differences gives an exact 64-bit count. After ~17.9 min of
    uptime the value leaves MicroPython's small-int range and becomes a heap long int. That
    costs a little garbage per call, which run() collects in the slack after IMU samples.
    """

    def __init__(self, ticks_us, ticks_diff, start_us=0):
        self._ticks_us = ticks_us
        self._ticks_diff = ticks_diff
        self._last = ticks_us()
        self._us = start_us

    def __call__(self):
        t = self._ticks_us()
        self._us += self._ticks_diff(t, self._last)
        self._last = t
        return self._us


class Firmware:
    """Protocol and safety state machine. Pure logic: every piece of hardware is injected.

    ``emitters``: objects with ``duty_u16(int)``, e.g. machine.PWM: (V-laser, H-laser, dots) and,
    for the 4-laser build, (second V-laser, second H-laser) after them.
    ``button``: object with ``value()`` returning 0 while pressed (pull-up), or None.
    ``imu``: LSM6DSOX-like (``name``, ``gyro_scale``, ``accel_scale``, ``set_rate(hz)``,
    ``data_ready()``, ``read_raw()``, optionally ``read_temp_c100()``) or None.
    ``clock``: callable returning the unwrapped time in integer microseconds.
    ``write``: callable taking one string (a full line including the trailing newline).
    """

    def __init__(self, emitters, button, imu, clock, write):
        self.emitters = list(emitters)
        self.button = button
        self.imu = imu
        self.now = clock
        self._out = write
        self.levels = [0, 0, 0]  # V, H, dots
        self.levels2 = [0, 0]  # the second V and H lasers (they follow V and H unless set on their own)
        self.imu_rate = 0
        self._period_us = 0
        self._t_poll = None  # time of the previous IMU status poll
        self._temp_read = getattr(imu, "read_temp_c100", None)  # None: no temperatures
        self._t_temp = None  # time of the last temperature read (None: read after the next sample)
        self.temp_c100 = None  # last IMU chip temperature, hundredths of a degree C
        self._rx = []
        self._rx_overflow = False
        # Emitters in turn (ALT). The period now running starts at _alt_us + _alt_ns / 1000 (the
        # nanoseconds kept apart, so no rounding error builds up); _alt_k is 0 in the dots period, 1 in
        # the lasers period; _alt_lit is True while that period's window is on; _alt_next is the time
        # (us) of the next switch. ALTN / ALTP changes wait in _alt_shift / _alt_new_period for the
        # next cycle start.
        self.alt = False
        self._alt_lit = False
        self._alt_k = 0
        self._alt_us = 0
        self._alt_ns = 0
        self._alt_next = 0
        self._alt_period = 0
        self._alt_win = [0, 0]  # window lengths in us: dots, lasers
        self._alt_shift = 0
        self._alt_new_period = 0
        self._alt_stats_reset()
        t = clock()
        self.t_boot = t
        self.last_rx = t
        self.btn = self._read_button()
        self._btn_raw = self.btn
        self._btn_t = t
        self.all_off()

    # ------------------------------------------------------------------ output
    def send(self, line):
        self._out(line + "\n")

    def log(self, msg):
        self.send("L " + msg)

    def imu_name(self):
        if self.imu is None:
            return "NONE"
        return getattr(self.imu, "name", "IMU")

    # ------------------------------------------------------------------ emitters
    def set_levels(self, v, h, d, v2=None, h2=None):
        """Percent levels; ``v2``/``h2`` (the second lasers) follow ``v``/``h`` when not given."""
        levels = [_clamp_pct(v), _clamp_pct(h), _clamp_pct(d)]
        levels2 = [levels[0] if v2 is None else _clamp_pct(v2), levels[1] if h2 is None else _clamp_pct(h2)]
        if self.alt:  # switching in turn: the levels are used inside the windows
            self.levels = levels
            self.levels2 = levels2
            self._alt_write()
            return
        for pwm, pct in zip(self.emitters, levels + levels2):
            pwm.duty_u16(pct * 65535 // 100)
        self.levels = levels
        self.levels2 = levels2

    def all_off(self):
        """Every emitter off (and the switching in turn stopped). This is the safety path, so it never raises."""
        self.alt = False
        self._alt_lit = False
        for pwm in self.emitters:
            try:
                pwm.duty_u16(0)
            except Exception:
                pass
        self.levels = [0, 0, 0]
        self.levels2 = [0, 0]

    def emitters_on(self):
        return self.levels[0] > 0 or self.levels[1] > 0 or self.levels[2] > 0 or self.levels2[0] > 0 or self.levels2[1] > 0

    def fault(self, context, exc):
        """Any unexpected exception: emitters off, report it, keep running."""
        was_on = self.alt or self.emitters_on()
        self.all_off()
        name = getattr(type(exc), "__name__", "Exception")
        self.send("E %s %s: %s" % (context, name, exc))
        if was_on:
            self.log("emitters-off exception")

    # ------------------------------------------------------------------ input
    def feed(self, chars):
        """Consume received characters; complete lines are executed immediately."""
        for c in chars:
            if c == "\n" or c == "\r":
                rx = self._rx
                self._rx = []
                if self._rx_overflow:
                    self._rx_overflow = False
                    self.send("E line too long")
                elif rx:
                    self.handle_line("".join(rx))
            elif self._rx_overflow:
                pass
            elif len(self._rx) >= MAX_LINE:
                self._rx = []
                self._rx_overflow = True
            else:
                self._rx.append(c)

    def handle_line(self, line):
        """Execute one command line (terminator already removed)."""
        parts = line.split()
        if not parts:
            return
        self.last_rx = self.now()  # any host line feeds the watchdog
        cmd = parts[0].upper()
        try:
            self._dispatch(cmd, parts)
        except Exception as e:
            self.fault(cmd, e)

    def _dispatch(self, cmd, parts):
        nargs = len(parts) - 1
        if cmd == "HB":
            return
        if cmd == "TIME":
            self.send("T %d" % self.now())
        elif cmd == "EMIT":
            vals = [_parse_int(p) for p in parts[1:]]
            if nargs not in (3, 5) or None in vals:
                self.send("E EMIT bad args (EMIT <v> <h> <d> [<v2> <h2>], percent)")
                return
            if nargs == 3:
                self.set_levels(vals[0], vals[1], vals[2])
                self.send("OK EMIT %d %d %d" % (self.levels[0], self.levels[1], self.levels[2]))
            else:
                self.set_levels(vals[0], vals[1], vals[2], vals[3], vals[4])
                self.send("OK EMIT %d %d %d %d %d" % (self.levels[0], self.levels[1], self.levels[2],
                                                      self.levels2[0], self.levels2[1]))
        elif cmd == "IMU":
            rate = _parse_int(parts[1]) if nargs == 1 else None
            if rate is None:
                self.send("E IMU bad args (IMU <rate_hz>)")
            elif rate != 0 and rate not in IMU_RATES:
                self.send("E IMU unsupported rate %d (use 0, 104, 208, 416 or 833)" % rate)
            elif self.imu is None:
                if rate == 0:
                    self.imu_rate = 0
                    self.send("OK IMU 0 0 0")
                else:
                    self.send("E IMU no imu")
            else:
                self.start_imu(rate)
                self.send("OK IMU %d %.7e %.7e" % (rate, self.imu.gyro_scale, self.imu.accel_scale))
        elif cmd == "STATUS":
            up_ms = (self.now() - self.t_boot) // 1000
            emit = "%d,%d,%d" % (self.levels[0], self.levels[1], self.levels[2])
            if self.levels2 != self.levels[:2]:
                emit += ",%d,%d" % (self.levels2[0], self.levels2[1])
            line = "S emit=%s imu=%d btn=%d up=%d.%03d" % (emit, self.imu_rate, self.btn, up_ms // 1000, up_ms % 1000)
            if self.temp_c100 is not None:
                line += " temp=" + _fmt_c100(self.temp_c100)
            if self.alt:
                n = self._alt_n
                line += " alt=%d,%d,%d late=%d,%d sw=%d miss=%d" % (
                    self._alt_period, self._alt_win[0], self._alt_win[1], self._alt_sum // n if n else 0,
                    self._alt_max, n, self._alt_miss)
                self._alt_stats_reset()
            self.send(line)
        elif cmd == "HELLO":
            self.send("OK HELLO %s %s imu=%s" % (FW_NAME, FW_VERSION, self.imu_name()))
        elif cmd == "ALT":
            self._cmd_alt(parts)
        elif cmd == "ALTN":
            self._cmd_altn(parts)
        elif cmd == "ALTP":
            self._cmd_altp(parts)
        else:
            self.send("E unknown %s" % parts[0])

    def start_imu(self, rate):
        self.imu_rate = 0  # stays 0 if set_rate raises
        self.imu.set_rate(rate)
        self.imu_rate = rate
        self._period_us = 1000000 // rate if rate else 0
        self._t_poll = None
        self._t_temp = None  # a fresh temperature right after the first sample

    # ------------------------------------------------------------------ emitters in turn (ALT)
    def _cmd_alt(self, parts):
        vals = [_parse_int(p) for p in parts[1:]]
        if None in vals or len(vals) not in (1, 3) or (len(vals) == 1 and vals[0] != 0):
            self.send("E ALT bad args (ALT <period_ns> <dots_us> <lasers_us>, or ALT 0)")
            return
        if len(vals) == 1:
            self.alt_stop()
            self.send("OK ALT 0")
            return
        period, dots, lasers = vals
        if period < ALT_PERIOD_MIN_NS or period > ALT_PERIOD_MAX_NS:
            self.send("E ALT period out of range (%d to %d ns)" % (ALT_PERIOD_MIN_NS, ALT_PERIOD_MAX_NS))
        elif dots < 0 or lasers < 0:
            self.send("E ALT bad window (0 or more us)")
        elif dots * 1000 >= period or lasers * 1000 >= period:
            self.send("E ALT window longer than the period")
        else:
            now = self.alt_start(period, dots, lasers)
            self.send("OK ALT %d 0 %d %d %d" % (now, period, dots, lasers))

    def _cmd_altn(self, parts):
        shift = _parse_int(parts[1]) if len(parts) == 2 else None
        if shift is None:
            self.send("E ALTN bad args (ALTN <shift_us>)")
        elif not self.alt:
            self.send("E ALTN not running")
        elif abs(shift) * 1000 > (self._alt_new_period or self._alt_period):
            self.send("E ALTN shift larger than a period")
        else:
            upcoming = self._alt_upcoming()
            if upcoming:
                self._alt_advance(shift * 1000)  # the next cycle is already set up: move it
            else:
                self._alt_shift += shift * 1000
            self._alt_reply("ALTN", upcoming)

    def _cmd_altp(self, parts):
        period = _parse_int(parts[1]) if len(parts) == 2 else None
        if period is None:
            self.send("E ALTP bad args (ALTP <period_ns>)")
        elif not self.alt:
            self.send("E ALTP not running")
        elif period < ALT_PERIOD_MIN_NS or period > ALT_PERIOD_MAX_NS:
            self.send("E ALTP period out of range (%d to %d ns)" % (ALT_PERIOD_MIN_NS, ALT_PERIOD_MAX_NS))
        elif self._alt_win[0] * 1000 >= period or self._alt_win[1] * 1000 >= period:
            self.send("E ALTP window longer than the period")
        else:
            upcoming = self._alt_upcoming()
            if upcoming:
                self._alt_period = period  # the next cycle is already set up: its periods change
            else:
                self._alt_new_period = period
            self._alt_reply("ALTP", upcoming)

    def _alt_upcoming(self):
        """True when the period set up is the dots period of a cycle that has not begun yet (the time
        between the end of a lasers window and the next cycle start). ALTN and ALTP then change that
        cycle; otherwise they wait for the end of the cycle running now."""
        return self._alt_k == 0 and not self._alt_lit and self.now() < self._alt_next

    def _alt_reply(self, word, upcoming):
        """``OK <word> <us> <ns> <period_ns> <dots_us> <lasers_us>``: the next cycle start, where the
        changes take hold, and the period from there on."""
        left = 0 if upcoming else 2 - self._alt_k  # periods left before that cycle start
        t = self._alt_ns + left * self._alt_period + self._alt_shift
        self.send("OK %s %d %d %d %d %d" % (word, self._alt_us + t // 1000, t % 1000,
                                            self._alt_new_period or self._alt_period, self._alt_win[0],
                                            self._alt_win[1]))

    def alt_start(self, period_ns, dots_us, lasers_us):
        """Start switching in turn; the first cycle (dots period first) starts now. Returns its start."""
        now = self.now()
        self._alt_period = period_ns
        self._alt_win = [dots_us, lasers_us]
        self._alt_us = now
        self._alt_ns = 0
        self._alt_k = 0
        self._alt_lit = False
        self._alt_next = now
        self._alt_shift = 0
        self._alt_new_period = 0
        self._alt_stats_reset()
        self.alt = True
        self._alt_write()  # everything off, then the first window opens at once
        self._alt_step(self.now())
        return now

    def alt_stop(self):
        """Stop switching: back to the steady EMIT levels."""
        if not self.alt:
            return
        self.alt = False
        self._alt_lit = False
        for pwm, pct in zip(self.emitters, self.levels + self.levels2):
            pwm.duty_u16(pct * 65535 // 100)

    def switch_due_within(self, us):
        """True while switching in turn and the next switch is due in less than ``us``."""
        return self.alt and self._alt_next - self.now() < us

    def _alt_stats_reset(self):
        self._alt_n = 0  # switches since the last STATUS
        self._alt_sum = 0  # their lateness added up (us)
        self._alt_max = 0
        self._alt_miss = 0  # windows missed whole (the loop came round after the window's end)

    def _alt_write(self):
        """Duties for the state of the switching: the group in its window at its EMIT levels, all the
        rest off. Everything that goes off is written first, so the dots and the lasers never overlap."""
        vals = self.levels + self.levels2
        lit = -1
        if self._alt_lit:
            lit = self._alt_k
        on = []
        for i, pwm in enumerate(self.emitters):
            group = 0 if i == 2 else 1
            if group == lit and i < 5 and vals[i]:
                on.append((pwm, vals[i]))
            else:
                pwm.duty_u16(0)
        for pwm, pct in on:
            pwm.duty_u16(pct * 65535 // 100)

    def _alt_done(self, late):
        self._alt_n += 1
        self._alt_sum += late
        if late > self._alt_max:
            self._alt_max = late

    def _alt_advance(self, ns):
        """Move the period start on by ``ns``; the next switch is the new period's first microsecond."""
        t = self._alt_ns + ns
        self._alt_us += t // 1000
        self._alt_ns = t % 1000
        self._alt_next = self._alt_us + (1 if self._alt_ns else 0)

    def _alt_period_end(self):
        """On to the next period. At the end of a cycle the waiting ALTN / ALTP changes take hold."""
        ns = self._alt_period
        if self._alt_k == 1:
            ns += self._alt_shift
            self._alt_shift = 0
            if self._alt_new_period:
                self._alt_period = self._alt_new_period
                self._alt_new_period = 0
        self._alt_k ^= 1
        self._alt_advance(ns)

    def _alt_step(self, now):
        """Every switch that is due by ``now``, in time order: a window that has started opens (unless
        it has already ended: then it is missed), a window that has ended closes."""
        while now >= self._alt_next:
            if self._alt_lit:  # this period's window ends
                self._alt_lit = False
                self._alt_write()
                self._alt_done(now - self._alt_next)
                self._alt_period_end()
                continue
            start = self._alt_next
            if self._alt_k == 0:
                behind = (now - self._alt_us) * 1000 - self._alt_ns
                cycle = 2 * self._alt_period
                if behind >= cycle:  # whole cycles went by without a loop iteration: skip them at once
                    n = behind // cycle
                    self._alt_miss += n * ((1 if self._alt_win[0] else 0) + (1 if self._alt_win[1] else 0))
                    self._alt_advance(n * cycle)
                    continue
            win = self._alt_win[self._alt_k]
            if win:
                if now < start + win:
                    self._alt_lit = True
                    self._alt_write()
                    self._alt_done(now - start)
                    self._alt_next = start + win
                    continue
                self._alt_miss += 1
            self._alt_period_end()  # no window in this period, or it was missed

    # ------------------------------------------------------------------ periodic work
    def process(self, chars=""):
        """One main-loop iteration: consume ``chars`` then poll the hardware.

        Returns True if an IMU sample was sent. Never raises ``Exception`` (KeyboardInterrupt is
        a BaseException and passes through so Ctrl-C still reaches the REPL).
        """
        try:
            if chars:
                self.feed(chars)
            return self.step()
        except Exception as e:
            try:
                self.fault("internal", e)
            except Exception:
                self.all_off()
            return False

    def step(self):
        """Watchdog, switching in turn, button, IMU and temperature polling. Returns True if an IMU
        sample was sent."""
        now = self.now()
        if (self.alt or self.emitters_on()) and now - self.last_rx >= WATCHDOG_US:
            self.all_off()
            self.log("emitters-off watchdog")
        if self.alt:
            self._alt_step(now)
        self._poll_button(now)
        if self.alt and self.switch_due_within(ALT_GUARD_US):
            return False  # a switch is due soon: the IMU sample waits in the sensor until after it
        if self._poll_imu():
            if not (self.alt and self.switch_due_within(ALT_GUARD_US)):
                self._poll_temp()  # in the slack until the next sample
            return True
        return False

    def _read_button(self):
        if self.button is None:
            return 0
        return 1 if self.button.value() == 0 else 0

    def _poll_button(self, now):
        """20 ms stable-state debounce. The event carries the time of the edge that started
        the stable period (not the time the debounce finished)."""
        raw = self._read_button()
        if raw != self._btn_raw:
            self._btn_raw = raw
            self._btn_t = now
        elif raw != self.btn and now - self._btn_t >= DEBOUNCE_US:
            self.btn = raw
            self.send("B %d %d" % (self._btn_t, raw))

    def _poll_imu(self):
        imu = self.imu
        if not self.imu_rate or imu is None:
            return False
        t = self.now()  # taken right before the STATUS_REG read
        try:
            if not imu.data_ready():
                self._t_poll = t
                return False
            raw = imu.read_raw()
        except Exception as e:
            self.imu_rate = 0
            self._t_poll = None
            self.fault("imu-read", e)
            return False
        # The sample became ready after the previous "not ready" poll (and at most one ODR
        # period ago, otherwise a newer sample would have replaced it): use the midpoint of
        # that window. Error <= half the poll interval; the constant I2C latency is calibrated
        # out by the IMU-camera time offset.
        prev = self._t_poll
        self._t_poll = t
        lo = t - self._period_us
        if prev is not None and prev > lo:
            lo = prev
        self.send("I %d %d %d %d %d %d %d" % ((lo + t) // 2, raw[0], raw[1], raw[2], raw[3], raw[4], raw[5]))
        return True

    def _poll_temp(self):
        """Send the IMU chip temperature (``C``) at most every TEMP_PERIOD_US. Called right after
        an IMU sample; the first read of a stream comes with its first sample."""
        read = self._temp_read
        if read is None or not self.imu_rate:
            return
        t = self.now()
        if self._t_temp is not None and t - self._t_temp < TEMP_PERIOD_US:
            return
        self._t_temp = t
        try:
            c100 = read()
        except Exception as e:
            # Not a safety problem: the IMU and the emitters carry on, only temperatures stop.
            self._temp_read = None
            self.send("E temp-read %s: %s" % (getattr(type(e), "__name__", "Exception"), e))
            return
        self.temp_c100 = c100
        self.send("C %d %d" % (t, c100))


def _readable(poller, ipoll, pollin):
    """True if stdin has a character waiting. ``ipoll`` is allocation-free on MicroPython."""
    events = ipoll(0) if ipoll is not None else poller.poll(0)
    for entry in events:
        return (entry[1] & pollin) != 0
    return False


def pin_map(platform):
    """(V laser, H laser, dots, trigger, SDA, SCL) for ``sys.platform``: the Pico's unless an ESP32."""
    return PINS.get(platform, PINS["rp2"])


def laser2_pins(platform):
    """(second V laser, second H laser) for ``sys.platform`` (the 4-laser build's own channels)."""
    return PINS_LASER2.get(platform, PINS_LASER2["rp2"])


def run(machine=None, time=None, select=None, stdin=None, stdout=None, max_iterations=None, platform=None):
    """Device entry point: set up the hardware, then run the main loop.

    The module arguments default to the real MicroPython modules (imported lazily). Tests pass
    stubs and ``max_iterations``; on the device the loop runs until Ctrl-C. ``platform``
    (default ``sys.platform``) picks the pin map. Returns the Firmware instance when the loop ends.
    """
    if platform is None:
        platform = sys.platform
    pin_v, pin_h, pin_dots, pin_trigger, pin_sda, pin_scl = pin_map(platform)
    pin_v2, pin_h2 = laser2_pins(platform)
    if machine is None:
        import machine
    if time is None:
        import time
    if select is None:
        import select
    if stdin is None:
        stdin = sys.stdin
    if stdout is None:
        stdout = sys.stdout
    import gc

    # 1. Emitters off before anything else can fail.
    pwms = []
    for pin in (pin_v, pin_h, pin_dots, pin_v2, pin_h2):
        machine.Pin(pin, machine.Pin.OUT, value=0)
        pwm = machine.PWM(machine.Pin(pin))
        pwm.duty_u16(0)
        pwm.freq(PWM_FREQ_HZ)
        pwm.duty_u16(0)
        pwms.append(pwm)
    button = machine.Pin(pin_trigger, machine.Pin.IN, machine.Pin.PULL_UP)
    clock = UnwrappedClock(time.ticks_us, time.ticks_diff)

    # 2. The IMU is optional: without it the firmware still drives emitters and the trigger.
    imu = None
    imu_err = None
    try:
        import lsm6dsox

        i2c = machine.I2C(I2C_ID, sda=machine.Pin(pin_sda), scl=machine.Pin(pin_scl), freq=I2C_FREQ)
        imu = lsm6dsox.LSM6DSOX(i2c)
    except Exception as e:
        imu_err = e

    fw = Firmware(pwms, button, imu, clock, stdout.write)
    fw.log("boot %s %s imu=%s" % (FW_NAME, FW_VERSION, fw.imu_name()))
    if platform == "esp32":
        fw.log("board esp32")
    if imu_err is not None:
        fw.log("imu init failed: %s" % (imu_err,))

    pollin = select.POLLIN
    poller = select.poll()
    poller.register(stdin, pollin)
    ipoll = getattr(poller, "ipoll", None)
    mem_free = getattr(gc, "mem_free", None)  # MicroPython only
    gc.collect()
    n = 0
    try:
        while True:
            if max_iterations is not None:
                if n >= max_iterations:
                    break
                n += 1
            chars = ""
            k = 0
            while k < MAX_RX_PER_STEP and _readable(poller, ipoll, pollin):
                c = stdin.read(1)
                if not c:
                    break
                chars += c
                k += 1
            sent = fw.process(chars)
            # Collect garbage right after a sample (slack until the next one) or when idle,
            # so an automatic GC never lands on a sample edge, nor (while switching in turn) on a switch.
            if mem_free is not None and (sent or not fw.imu_rate) and mem_free() < GC_MIN_FREE \
                    and not fw.switch_due_within(ALT_GC_GUARD_US):
                gc.collect()
    finally:
        fw.all_off()
    return fw


if __name__ == "__main__":
    run()
