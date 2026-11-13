"""Settings of the camera and the Pico: plain dataclasses with no heavy import.

They live apart from :mod:`ohs.devices.camera` (OpenCV) and :mod:`ohs.devices.pico` (numpy) so that
:mod:`ohs.app.config`, and with it ``ohs --help`` and ``ohs config``, load neither library. Both
modules re-export their class, so ``from ohs.devices.camera import CameraParams`` works as before.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CameraParams", "PicoParams"]


@dataclass
class CameraParams:
    index: int = 0
    backend: str = "auto"  # "auto" | "dshow" | "msmf" | "v4l2" | "any"
    width: int = 2560  # full side-by-side frame
    height: int = 800
    fps: float = 60.0
    fourcc: str = "MJPG"
    exposure: float | None = None  # backend-native units; None = leave camera default
    gain: float | None = None
    swap_lr: bool = False
    second_index: int | None = None  # fallback: two separate (unsynced) UVC cameras


@dataclass
class PicoParams:
    """Pico connection settings. The first four fields are the ARCHITECTURE.md contract."""

    port: str | None = None  # None = auto-detect USB VID 0x2E8A
    baud: int = 115200
    imu_rate_hz: int = 416
    heartbeat_s: float = 0.4
    resync_s: float = 5.0  # background clock re-sync period
    sync_samples: int = 16  # TIME round trips per sync (the min-RTT one is used)
    timeout_s: float = 0.5  # reply timeout per command
    drift_prior_ppm: float = 100.0  # prior std-dev of the host/device clock-rate difference
