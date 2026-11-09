# OpenHandScan Pico firmware

MicroPython firmware for the scanner's Raspberry Pi Pico (RP2040) or Pico 2 (RP2350). It drives
the two line lasers and the dot projector (PWM), reads the trigger button and streams the
LSM6DSOX IMU to the PC over the Pico's USB serial port.

| File | Purpose |
|---|---|
| `main.py` | Serial protocol, emitter safety (watchdog), trigger debounce, IMU streaming. Runs at power-up. |
| `lsm6dsox.py` | Minimal LSM6DSOX I2C driver (+-1000 dps, +-8 g, 104-833 Hz). |

Requires MicroPython 1.20 or newer. The PC side is `software/ohs/devices/pico.py`.

## 1. Flash MicroPython (once)

1. Download the MicroPython `.uf2` for your board from micropython.org:
   `RPI_PICO` for a Pico, `RPI_PICO2` for a Pico 2 (the `_W` builds work too).
2. Hold the **BOOTSEL** button and plug the Pico into USB. A drive called `RPI-RP2` (Pico) or
   `RP2350` (Pico 2) appears.
3. Copy the `.uf2` file onto that drive. The Pico reboots into MicroPython and shows up as a USB
   serial port (`COMx` on Windows, `/dev/ttyACM0` on Linux).

## 2. Copy the firmware files

**mpremote** (`pip install mpremote`), from the repository root:

```
mpremote connect auto cp firmware/lsm6dsox.py :lsm6dsox.py
mpremote connect auto cp firmware/main.py :main.py
mpremote connect auto reset
```

Use `connect COM5` (Windows) or `connect /dev/ttyACM0` (Linux) instead of `auto` if several
boards are attached. mpremote interrupts a running `main.py` with Ctrl-C by itself, and the
emitters switch off when that happens.

**Thonny**: *Tools > Options > Interpreter*: "MicroPython (Raspberry Pi Pico)" and the port. Press
**Stop** to interrupt the running program. Open each file, then *File > Save as... > Raspberry Pi
Pico*, keeping the names `main.py` and `lsm6dsox.py`. Unplug and replug (or press Stop/Restart)
to start it.

`main.py` starts automatically at every power-up. It only starts when it runs as the main
program, never when imported.

## 3. Quick manual test with a serial terminal

Close Thonny, mpremote and the `ohs` app first: only one program can hold the port.

```
python -m serial.tools.miniterm COM5 115200 --eol LF -e
```

(`-e` echoes what you type. Linux: `/dev/ttyACM0`, and your user must be in the `dialout` group.)
Then type:

```
HELLO          -> OK HELLO ohs-fw 1.1.0 imu=LSM6DSOX
STATUS         -> S emit=0,0,0 imu=0 btn=0 up=42.137
EMIT 0 0 20    -> OK EMIT 0 0 20      dot projector at 20 %; it switches off 1.5 s later (watchdog)
IMU 104        -> OK IMU 104 6.1086524e-04 2.3928226e-03, then a stream of "I <t_us> gx gy gz ax ay az"
                  and once a second "C <t_us> 2934" (the IMU chip at 29.34 degC)
STATUS         -> S emit=0,0,0 imu=104 btn=0 up=58.410 temp=29.34
IMU 0          -> OK IMU 0 ...        stream stops
```

Press the trigger: `B <t_us> 1` when pressed, `B <t_us> 0` when released. At rest, `az` should
read about +4100 counts (1 g, if the IMU's z axis points up).

The lasers are Class 3R. Never look into them, and point the scanner at a wall or a sheet of paper while testing.

## Pin map

| Function | GPIO | Pico pin | Notes |
|---|---|---|---|
| V-line laser | GP2 | 4 | PWM 10 kHz, 1 kOhm to the PN2222A base (low-side switch) |
| H-line laser | GP3 | 5 | same |
| Dot projector | GP6 | 9 | same |
| Trigger button | GP15 | 20 | to GND (pin 18), internal pull-up, 20 ms debounce |
| IMU SDA | GP4 | 6 | I2C0, 400 kHz, LSM6DSOX at 0x6A (fallback 0x6B) |
| IMU SCL | GP5 | 7 | |
| IMU VIN | 3V3(OUT) | 36 | |
| Laser supply | VBUS | 40 | through the kill toggle on the back cover |

## Protocol

ASCII lines, `\n` terminated (`\r\n` accepted), 115200 baud nominal (USB CDC ignores the baud rate).
Commands are case-insensitive.

| Host -> Pico | Reply |
|---|---|
| `HELLO` | `OK HELLO ohs-fw <ver> imu=<LSM6DSOX\|NONE>` |
| `EMIT <v> <h> <d>` (percent, clamped to 0..100) | `OK EMIT <v> <h> <d>` (the values actually applied) |
| `IMU <rate_hz>` (0 = stop; 104/208/416/833) | `OK IMU <rate> <gyro_rad_per_lsb> <accel_ms2_per_lsb>` |
| `TIME` | `T <t_us>` |
| `HB` | (no reply, keeps the emitters alive) |
| `STATUS` | `S emit=<v>,<h>,<d> imu=<rate> btn=<0\|1> up=<s>`, plus ` temp=<degC>` once a temperature was read |

Asynchronous lines: `I <t_us> <gx> <gy> <gz> <ax> <ay> <az>` (raw int16 counts, IMU frame),
`B <t_us> <0|1>`, `C <t_us> <centi_degC>`, `E <msg>`, `L <msg>`.

* `C` (firmware 1.1.0+) is the IMU chip's temperature in hundredths of a degree Celsius. It comes
  right after the first IMU sample of a stream, then about once a second, and only while the IMU
  streams (the sensor is off otherwise). Every recording stores it, so drift with heat can be
  modelled later. If a temperature read fails, the firmware sends `E temp-read ...` once and stops
  reading it; the IMU and the emitters carry on.

* `t_us` is a 64-bit microsecond counter that never wraps. The firmware unwraps the 30-bit
  `ticks_us()` itself, so it is always increasing until the next reset.
* Errors caused by a command name it: `E EMIT bad args ...`, `E IMU unsupported rate 100 ...`,
  `E IMU no imu`, `E unknown <cmd>`. Lines longer than 64 characters give `E line too long`.
* When the firmware switches the emitters off by itself it logs `L emitters-off watchdog` or
  `L emitters-off exception`.
* IMU timestamps use data-ready polling. Each sample is stamped at the midpoint between the last
  "not ready" poll and the "ready" poll, so the error is typically < 0.2 ms and always < 1 ms;
  the reasoning is in `lsm6dsox.py`. The PC maps `t_us` to its own clock with the `TIME`
  sync in `ohs/devices/pico.py`.

## Safety behaviour

* Emitters are off at boot, on any exception (reported as an `E` line), whenever the main loop
  exits (including Ctrl-C), and **1.5 s after the last complete line from the PC**. The PC client
  sends `HB` every 0.4 s while an emitter is on, so a crashed or unplugged PC turns them off.
* Values outside 0..100 % are clamped.
* The hardware kill toggle is still the primary safety. Optional hardening: a 10 kOhm resistor from
  each transistor base to GND keeps the emitters off while the Pico is held in reset or in BOOTSEL.

## Troubleshooting

* `imu=NONE`: check the wiring and scan the bus:
  `mpremote exec "import machine; print([hex(a) for a in machine.I2C(0, sda=machine.Pin(4), scl=machine.Pin(5)).scan()])"`
  should list `0x6a` or `0x6b`. The boot log line `L imu init failed: ...` gives the reason.
* No reply to `HELLO`: `main.py` is not running. Run `mpremote connect COM5 repl`, then press
  Ctrl-D (soft reset) to watch it start and see any traceback.
* The firmware logic is unit-tested on the PC with fake hardware:
  `python -m pytest software/tests/test_firmware.py software/tests/test_devices_pico.py -q`.
