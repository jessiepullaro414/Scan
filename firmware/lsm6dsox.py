"""Minimal LSM6DSOX 6-axis IMU driver for MicroPython (I2C), used by main.py.

Wiring (README.md): I2C0, SDA GP4, SCL GP5, 400 kHz. 7-bit address 0x6A (SA0 low), with a
fallback to 0x6B (SA0 high, e.g. a breakout with its address jumper changed).

Configuration: gyro +-1000 dps (35 mdps/LSB), accel +-8 g (0.244 mg/LSB), both at the same ODR
(104/208/416/833 Hz, high-performance mode), block data update (BDU) and register address
auto-increment (IF_INC) on, I3C off.

Why data-ready polling and not the FIFO
---------------------------------------
The INT1/INT2 pins are not wired, so the firmware must poll either way. The FIFO path needs one
7-byte read per FIFO word (tag + data) plus a second clock model that maps the sensor's own
25 us timestamp counter (whose rate is off by up to a few percent, see FREQ_FINE) onto the Pico
clock. In MicroPython that is about 3x the I2C traffic per sample. With data-ready polling the main
loop reads STATUS_REG (one byte, ~0.1 ms) continuously and reads the 12 output bytes in one burst
when GDA is set. main.py timestamps a sample at the midpoint between the last "not ready" poll and
the "ready" poll, so the timestamp error stays below half a loop iteration (typically < 0.2 ms,
always < 1 ms). The constant part of the latency (I2C transfer time) is absorbed by the
IMU-camera ``time_offset`` calibration.

Temperature: the chip's own sensor (OUT_TEMP, 256 LSB/degC, 0 LSB = 25 degC) updates at 52 Hz
while the gyro or the accelerometer runs. main.py reads it about once a second, so recordings can
relate drift to heat.

The driver is hardware-agnostic: ``i2c`` only needs ``readfrom_mem``, ``readfrom_mem_into`` and
``writeto_mem`` (``machine.I2C`` or a test fake).
"""
import struct

try:
    from math import pi as _PI
except ImportError:  # pragma: no cover - every MicroPython port with floats has math
    _PI = 3.141592653589793

# Registers (LSM6DSOX datasheet DS12814)
WHO_AM_I = 0x0F
WHO_AM_I_VALUE = 0x6C
CTRL1_XL = 0x10
CTRL2_G = 0x11
CTRL3_C = 0x12
CTRL9_XL = 0x18
STATUS_REG = 0x1E
OUT_TEMP_L = 0x20  # OUT_TEMP_L, OUT_TEMP_H: int16, 256 LSB/degC, 0 LSB = 25 degC
OUTX_L_G = 0x22  # OUTX_L_G .. OUTZ_H_G (0x22-0x27), OUTX_L_A .. OUTZ_H_A (0x28-0x2D)

ADDRESSES = (0x6A, 0x6B)

# ODR_XL / ODR_G codes (bits 7:4 of CTRL1_XL / CTRL2_G)
ODR_CODES = {104: 0x4, 208: 0x5, 416: 0x6, 833: 0x7}
FS_XL_8G = 0x0C  # CTRL1_XL FS_XL[1:0] = 11 -> +-8 g (with XL_FS_MODE = 0)
FS_G_1000DPS = 0x08  # CTRL2_G FS_G[1:0] = 10 -> +-1000 dps, FS_125 = 0
CTRL3_BDU = 0x40
CTRL3_IF_INC = 0x04
CTRL3_SW_RESET = 0x01
CTRL9_I3C_DISABLE = 0x02
STATUS_XLDA = 0x01
STATUS_GDA = 0x02

# Sensitivities in SI units (datasheet table 2)
GYRO_SCALE = 35e-3 * _PI / 180.0  # rad/s per LSB at +-1000 dps (35 mdps/LSB)
ACCEL_SCALE = 0.244e-3 * 9.80665  # m/s^2 per LSB at +-8 g (0.244 mg/LSB)


class LSM6DSOX:
    """Register-level driver. Raises ``OSError`` if no LSM6DSOX answers."""

    name = "LSM6DSOX"
    gyro_scale = GYRO_SCALE
    accel_scale = ACCEL_SCALE

    def __init__(self, i2c, address=None):
        self.i2c = i2c
        self._b1 = bytearray(1)
        self._b2 = bytearray(2)
        self._buf = bytearray(12)
        self.rate_hz = 0
        self.address = self._probe(ADDRESSES if address is None else (address,))
        self.reset()

    def _probe(self, addresses):
        seen = []
        for addr in addresses:
            try:
                who = self.i2c.readfrom_mem(addr, WHO_AM_I, 1)[0]
            except OSError:
                continue
            if who == WHO_AM_I_VALUE:
                return addr
            seen.append("0x%02X:0x%02X" % (addr, who))
        raise OSError("no LSM6DSOX (WHO_AM_I 0x6C) at %s; answered: %s"
                      % ("/".join("0x%02X" % a for a in addresses), ", ".join(seen) or "none"))

    def _read(self, reg):
        self.i2c.readfrom_mem_into(self.address, reg, self._b1)
        return self._b1[0]

    def _write(self, reg, value):
        self._b1[0] = value & 0xFF
        self.i2c.writeto_mem(self.address, reg, self._b1)

    def reset(self):
        """Software reset, then BDU + IF_INC, I3C off, both sensors powered down."""
        self._write(CTRL3_C, CTRL3_SW_RESET)
        for _ in range(100):  # the reset takes ~50 us; each poll is ~0.1 ms of I2C
            if not self._read(CTRL3_C) & CTRL3_SW_RESET:
                break
        else:
            raise OSError("LSM6DSOX software reset did not complete")
        self._write(CTRL3_C, CTRL3_BDU | CTRL3_IF_INC)
        self._write(CTRL9_XL, self._read(CTRL9_XL) | CTRL9_I3C_DISABLE)
        self.set_rate(0)

    def set_rate(self, rate_hz):
        """Gyro and accel at ``rate_hz`` (104/208/416/833), or 0 to power both down."""
        if rate_hz == 0:
            code = 0
        elif rate_hz in ODR_CODES:
            code = ODR_CODES[rate_hz]
        else:
            raise ValueError("unsupported IMU rate %r" % (rate_hz,))
        self._write(CTRL1_XL, (code << 4) | FS_XL_8G)
        self._write(CTRL2_G, (code << 4) | FS_G_1000DPS)
        self.rate_hz = rate_hz

    def data_ready(self):
        """True when a new gyro sample is available (STATUS_REG.GDA). Allocation-free."""
        self.i2c.readfrom_mem_into(self.address, STATUS_REG, self._b1)
        return (self._b1[0] & STATUS_GDA) != 0

    def read_raw(self):
        """(gx, gy, gz, ax, ay, az) raw int16 counts, one 12-byte burst from OUTX_L_G."""
        self.i2c.readfrom_mem_into(self.address, OUTX_L_G, self._buf)
        return struct.unpack("<hhhhhh", self._buf)

    def read_temp_c100(self):
        """The chip temperature in hundredths of a degree Celsius (int, rounded).

        Only fresh while the gyro or the accelerometer runs (52 Hz); in power-down it keeps the
        last value. One 2-byte burst from OUT_TEMP_L (BDU keeps the two bytes together).
        """
        self.i2c.readfrom_mem_into(self.address, OUT_TEMP_L, self._b2)
        raw = struct.unpack("<h", self._b2)[0]
        return 2500 + (raw * 100 + 128) // 256
