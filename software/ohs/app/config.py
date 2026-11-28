"""Configuration for the ``ohs`` app: a dataclass tree with defaults, TOML loading and a commented template.

* :class:`Config` holds :class:`~ohs.devices.camera.CameraParams` and
  :class:`~ohs.devices.pico.PicoParams` unchanged, plus the app's own sections: emitter levels per
  profile, live (preview-quality) processing, offline processing defaults, the ChArUco board, the
  AprilTag size, the default paths (including the Studio projects folder), ``[app]`` (the
  host and port of ``ohs app``) and ``[lasers]`` (the build's line lasers, :class:`LasersConfig`).
* :func:`load_config` reads ``~/.ohs/config.toml`` (or ``$OHS_CONFIG``, or an explicit path) with
  :mod:`tomllib` and merges it over the defaults. Every key is optional. Unknown keys produce a
  warning with a "did you mean" hint and are ignored. Values of the wrong type raise
  :class:`ConfigError`, which names the key. Integers are accepted where floats are expected.
* :func:`render_template` produces a commented TOML file holding every default
  (``ohs config --init``). Values that default to ``None`` appear as commented-out examples.
  Loading the template gives exactly the defaults.
* :func:`set_config_values` changes a few keys of the config file in place (OpenHandScan Studio's
  settings): a line-based edit of ``key = value`` under ``[section]`` that keeps every comment
  and every other line, checked by parsing the result before it replaces the file atomically.

TOML has no null value. For an optional setting, write ``"auto"`` (or ``"none"``) or leave the key
out. Emitter profiles are ``[vertical, horizontal, dots]`` levels in percent. Profiles from the file
are added to the built-in ones or replace them; the built-in profile names always exist.
"""
from __future__ import annotations

import dataclasses
import difflib
import json
import logging
import math
import os
import re
import time
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ohs.devices.params import CameraParams, PicoParams

try:  # Python >= 3.11
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ModuleNotFoundError:
        _toml = None  # type: ignore[assignment]

__all__ = [
    "CONFIG_ENV",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_PROFILES",
    "EMITTER_NAMES",
    "ConfigError",
    "PathsConfig",
    "AppConfig",
    "EmitterConfig",
    "LiveConfig",
    "ProcessConfig",
    "BoardConfig",
    "MarkersConfig",
    "LasersConfig",
    "LASER_FAMILIES",
    "Config",
    "default_config_path",
    "load_config",
    "merge_into",
    "render_template",
    "write_template",
    "set_config_values",
]

log = logging.getLogger(__name__)

CONFIG_ENV = "OHS_CONFIG"
DEFAULT_CONFIG_PATH = "~/.ohs/config.toml"
EMITTER_NAMES = ("vertical", "horizontal", "dots")
CAMERA_BACKENDS = ("auto", "dshow", "msmf", "v4l2", "any")
IMU_RATES = (104, 208, 416, 833)
EXPORT_FORMATS = ("ply", "stl", "obj")
PAPERS = ("letter", "a4")
THIN_WALLS = ("auto", "on", "off")  # as ohs.app.pipeline.THIN_WALLS
#: the laser line families, as ``ohs.vision.laser.LINE_NAMES`` (not imported here: it pulls in OpenCV)
LASER_FAMILIES = ("vertical", "horizontal")

#: Built-in emitter profiles: (vertical line laser, horizontal line laser, dot projector), percent.
DEFAULT_PROFILES: dict[str, tuple[float, float, float]] = {
    "off": (0.0, 0.0, 0.0),
    "scan": (100.0, 100.0, 100.0),  # ohs scan / ohs record default: dots for stereo depth, lines for laser points
    "lines": (100.0, 100.0, 0.0),
    "dots": (0.0, 0.0, 100.0),
    "stereo": (0.0, 0.0, 0.0),  # ohs calibrate stereo: ambient light only
    "lasers": (100.0, 100.0, 0.0),  # ohs calibrate lasers: lines on, dots off
    "imu": (0.0, 0.0, 0.0),  # ohs calibrate imu
    "check": (100.0, 100.0, 0.0),  # ohs calibrate check
}

_NONE_WORDS = {"none", "auto", "null", ""}
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


class ConfigError(ValueError):
    """Invalid configuration file or value (the message names the key)."""


# --------------------------------------------------------------------------- sections
@dataclass
class PathsConfig:
    calibration: str = "~/.ohs/calibration.json"
    scans_dir: str = "~/OpenHandScan/scans"
    projects_dir: str = "~/OpenHandScan/projects"
    data_dir: str = "~/.ohs/studio"
    printables_dir: str = "~/OpenHandScan/printables"

    @property
    def calibration_path(self) -> Path:
        return Path(self.calibration).expanduser()

    @property
    def scans_path(self) -> Path:
        return Path(self.scans_dir).expanduser()

    @property
    def projects_path(self) -> Path:
        return Path(self.projects_dir).expanduser()

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser()

    @property
    def printables_path(self) -> Path:
        return Path(self.printables_dir).expanduser()


@dataclass
class AppConfig:
    """OpenHandScan Studio (``ohs app``): where its local web server listens."""

    host: str = "127.0.0.1"
    port: int = 8765


@dataclass
class EmitterConfig:
    markers_dot_level: float = 25.0
    profiles: dict[str, tuple[float, float, float]] = field(default_factory=lambda: dict(DEFAULT_PROFILES))

    def levels(self, profile: str) -> tuple[float, float, float]:
        """Levels of a profile, or :class:`ConfigError` naming the known profiles."""
        try:
            v, h, d = self.profiles[profile]
        except KeyError:
            raise ConfigError(f"unknown emitter profile {profile!r}; known: {', '.join(sorted(self.profiles))}") from None
        return float(v), float(h), float(d)

    def scan_levels(self, mode: str, live: bool = True) -> tuple[float, float, float]:
        """Levels for ``ohs scan``: the "scan" profile, with the dots dimmed in live marker mode."""
        v, h, d = self.levels("scan")
        if mode == "markers" and live:
            d = min(d, float(self.markers_dot_level))
        return v, h, d


@dataclass
class LiveConfig:
    scale: float = 0.5
    depth_min_mm: float = 150.0
    depth_max_mm: float = 450.0
    voxel_mm: float | None = None  # None = pipeline default for the resolution (2 mm at 640x400)
    # ENGINE_PLAN R2-8: the recorder's budget of frames waiting for the disk (ohs.app.live.RECORD_MAX_BYTES / _SECONDS)
    max_waiting_mb: int = 1024
    max_waiting_seconds: float = 20.0


#: the smallest recorder budget the settings allow (``[live] max_waiting_mb`` / ``max_waiting_seconds``): 64 MB is
#: 32 full 1280 x 800 grey stereo pairs, 1 s is 60 frames at 60 fps, so no recorder refuses its first frames
MIN_WAITING_MB = 64
MIN_WAITING_SECONDS = 1.0


@dataclass
class ProcessConfig:
    voxel_mm: float | None = None  # None = pipeline default for the resolution (1 mm at 1280x800)
    frame_step: int = 1
    standard_frame_rate: float = 30.0  # frames per second of scanning that Standard quality processes (0 = every frame)
    frame_motion_deg: float = 0.0  # 0 = off: pick frames by the gyro's rotation (ohs.app.process)
    frame_max_gap: int = 4
    thin_walls: str = "auto"  # the mesh model's short band behind the surface: "auto" | "on" | "off"
    poisson: bool = False
    export: list[str] = field(default_factory=lambda: ["ply", "stl"])
    repeatable_edits: bool = False  # MAINTENANCE_PLAN F15: the app's mesh edits break ties by position
    refine_from_images: bool = False  # ENGINE_PLAN X6: Make mesh runs "Refine from the images" (GPU only); off: as before
    # ENGINE_PLAN X5 step 2: Make mesh makes only the thin walls finer (GPU, 1 mm) for scans without a choice of their own
    fine_walls: bool = False
    # ENGINE_PLAN X8 step 2: Straighten holds the scan to the AprilTags in view (ohs.track.posegraph.StraightenParams.tags)
    straighten_tags: bool = False
    # ENGINE_PLAN X19: an alignment from tags where two scans share little keeps the tags' pose (ohs.align.AlignParams.hold_tags)
    align_hold_tags: bool = False
    # ENGINE_PLAN X16: the viewer's wall-thickness reading from the camera frames (ohs.measure.wall)
    wall_reading: bool = True
    # ENGINE_PLAN X15 step 2: the Hole tool's second answer, the hole's edge read from the camera images (ohs.measure.rim)
    hole_from_images: bool = True


@dataclass
class BoardConfig:
    squares_x: int = 11
    squares_y: int = 8
    square_mm: float = 20.0
    marker_mm: float = 15.0
    dictionary: str = "DICT_5X5_100"
    paper: str = "letter"

    def spec(self):  # -> ohs.calib.board.CharucoSpec (imported lazily: cv2 + Pillow)
        from ohs.calib.board import CharucoSpec

        return CharucoSpec(int(self.squares_x), int(self.squares_y), float(self.square_mm), float(self.marker_mm),
                           str(self.dictionary))


@dataclass
class MarkersConfig:
    tag_mm: float = 20.0


@dataclass
class LasersConfig:
    """The build's line lasers (laser-plane calibration).

    ``lines``: one name per laser. A name is its family, ``"vertical"`` or ``"horizontal"`` (how
    its line runs in the image), optionally followed by ``_`` and anything (``"vertical_2"``).
    Within a family the order is the order of the lasers: vertical lines from right to left (the
    largest x of the camera frame first), horizontal lines from top to bottom. The calibration
    names the planes it finds that way.

    ``own_channels``: each laser on its own channel of the Pico (firmware 1.2.0: the 4-laser build's
    second lasers on GP0 and GP1), so that each can have its own brightness and the calibration can
    light one at a time; false when each pair is wired to one channel. ``brightness``: a laser's
    level in percent of its family's (``[emitters]`` levels); a laser left out gets 100. On a pair
    that shares a channel only the first laser's counts.
    """

    lines: list[str] = field(default_factory=lambda: ["vertical", "horizontal"])
    own_channels: bool = True
    brightness: dict[str, float] = field(default_factory=dict)

    def channel_of(self, name: str) -> str | None:
        """The Pico channel that drives laser ``name``: "v", "h" (the first of each family), "v2",
        "h2" (the second), or None (not a configured laser, or a third one of its family)."""
        for fam, ch in (("vertical", "v"), ("horizontal", "h")):
            members = [n for n in self.lines if n == fam or n.startswith(fam + "_")]
            if name in members:
                i = members.index(name)
                return ch if i == 0 else (ch + "2" if i == 1 else None)
        return None

    def separate(self) -> bool:
        """Can the lasers of a family be lit on their own (own channels, and a family has two)?"""
        return bool(self.own_channels) and any(self.channel_of(n) in ("v2", "h2") for n in self.lines)

    def channel_scales(self) -> tuple[float, float, float, float]:
        """Each channel's share of its family's level, for :class:`ohs.devices.pico.Pico`:
        (V, H, V2, H2). A channel no laser uses follows its family's first one."""
        by_ch = {self.channel_of(n): n for n in self.lines}
        share = {ch: min(100.0, max(0.0, float(self.brightness.get(n, 100.0)))) / 100.0 for ch, n in by_ch.items() if ch}
        v, h = share.get("v", 1.0), share.get("h", 1.0)
        if not self.own_channels:
            return v, h, v, h
        return v, h, share.get("v2", v), share.get("h2", h)

    def problems(self) -> list[str]:
        """What is wrong with ``lines`` and ``brightness`` (empty when it is a valid laser set)."""
        errs: list[str] = []
        lines = list(self.lines)
        bad = [n for n in lines if not any(n == f or n.startswith(f + "_") for f in LASER_FAMILIES)]
        if bad:
            errs.append(f"lasers.lines: {bad} do not start with a laser family ({', '.join(LASER_FAMILIES)}, "
                        'optionally followed by "_" and a suffix, e.g. "vertical_2")')
        twice = sorted({n for n in lines if lines.count(n) > 1})
        if twice:
            errs.append(f"lasers.lines: {twice} named twice")
        for fam in LASER_FAMILIES:
            if not any(n == fam or n.startswith(fam + "_") for n in lines):
                errs.append(f"lasers.lines needs at least one {fam} line (got {lines})")
        unknown = sorted(set(self.brightness) - set(lines))
        if unknown:
            errs.append(f"lasers.brightness: {unknown} are not in lasers.lines")
        for n, v in self.brightness.items():
            try:
                ok = 0.0 <= float(v) <= 100.0
            except (TypeError, ValueError):
                ok = False
            if not ok:
                errs.append(f"lasers.brightness.{n} must be a percent in 0..100 (got {v!r})")
        return errs


@dataclass
class ComputeConfig:
    """Where the heavy steps run (ohs.compute): "auto", "cpu", or part of a GPU's name."""

    device: str = "auto"


@dataclass
class Config:
    """The whole configuration. ``source`` and ``warnings`` describe the last load (not saved)."""

    paths: PathsConfig = field(default_factory=PathsConfig)
    camera: CameraParams = field(default_factory=CameraParams)
    pico: PicoParams = field(default_factory=PicoParams)
    emitters: EmitterConfig = field(default_factory=EmitterConfig)
    live: LiveConfig = field(default_factory=LiveConfig)
    process: ProcessConfig = field(default_factory=ProcessConfig)
    board: BoardConfig = field(default_factory=BoardConfig)
    markers: MarkersConfig = field(default_factory=MarkersConfig)
    app: AppConfig = field(default_factory=AppConfig)
    compute: ComputeConfig = field(default_factory=ComputeConfig)
    lasers: LasersConfig = field(default_factory=LasersConfig)
    source: Path | None = field(default=None, compare=False, repr=False, metadata={"toml": False})
    warnings: list[str] = field(default_factory=list, compare=False, repr=False, metadata={"toml": False})

    def validate(self) -> None:
        """Range checks across sections; raises :class:`ConfigError` listing every problem."""
        errs: list[str] = []
        c = self.camera
        if str(c.backend).lower() not in CAMERA_BACKENDS:
            errs.append(f"camera.backend must be one of {', '.join(CAMERA_BACKENDS)} (got {c.backend!r})")
        if c.width <= 0 or c.height <= 0 or c.fps <= 0:
            errs.append("camera.width, camera.height and camera.fps must be positive")
        if c.fourcc and len(c.fourcc) != 4:
            errs.append(f"camera.fourcc must have 4 characters (got {c.fourcc!r})")
        if int(self.pico.imu_rate_hz) not in IMU_RATES:
            errs.append(f"pico.imu_rate_hz must be one of {IMU_RATES} (got {self.pico.imu_rate_hz})")
        for name, lv in self.emitters.profiles.items():
            if len(lv) != 3 or any(not (0.0 <= float(x) <= 100.0) for x in lv):
                errs.append(f"emitters.profiles.{name} must be 3 levels in 0..100 (got {list(lv)})")
        if not 0.0 <= self.emitters.markers_dot_level <= 100.0:
            errs.append("emitters.markers_dot_level must be in 0..100")
        lv = self.live
        if not 0.0 < lv.scale <= 1.0:
            errs.append("live.scale must be in (0, 1]")
        if not 0.0 < lv.depth_min_mm < lv.depth_max_mm:
            errs.append("live.depth_min_mm must be positive and below live.depth_max_mm")
        if any(v is not None and v <= 0 for v in (lv.voxel_mm, self.process.voxel_mm)):
            errs.append("voxel sizes must be positive (or \"auto\")")
        if not (math.isfinite(lv.max_waiting_mb) and lv.max_waiting_mb >= MIN_WAITING_MB):
            errs.append(f"live.max_waiting_mb must be a number of MB, at least {MIN_WAITING_MB:g} (got {lv.max_waiting_mb})")
        if not (math.isfinite(lv.max_waiting_seconds) and lv.max_waiting_seconds >= MIN_WAITING_SECONDS):
            errs.append(f"live.max_waiting_seconds must be a number of seconds, at least {MIN_WAITING_SECONDS:g} "
                        f"(got {lv.max_waiting_seconds})")
        if self.process.frame_step < 1:
            errs.append("process.frame_step must be >= 1")
        if not (math.isfinite(self.process.frame_motion_deg) and self.process.frame_motion_deg >= 0):
            errs.append("process.frame_motion_deg must be >= 0 (0 = off)")
        if not (math.isfinite(self.process.standard_frame_rate) and self.process.standard_frame_rate >= 0):
            errs.append("process.standard_frame_rate must be >= 0 (0 = every frame)")
        if self.process.frame_max_gap < 1:
            errs.append("process.frame_max_gap must be >= 1")
        if self.process.thin_walls not in THIN_WALLS:
            errs.append("process.thin_walls must be \"auto\", \"on\" or \"off\"")
        bad = [e for e in self.process.export if e.lower() not in EXPORT_FORMATS]
        if bad:
            errs.append(f"process.export: unknown format(s) {bad}; use {', '.join(EXPORT_FORMATS)}")
        if self.board.paper.lower() not in PAPERS:
            errs.append(f"board.paper must be one of {', '.join(PAPERS)}")
        try:
            self.board.spec()
        except ValueError as e:
            errs.append(f"board: {e}")
        errs += self.lasers.problems()
        if self.markers.tag_mm <= 0:
            errs.append("markers.tag_mm must be positive")
        if not str(self.app.host).strip():
            errs.append('app.host must not be empty (e.g. "127.0.0.1")')
        if not 1 <= int(self.app.port) <= 65535:
            errs.append(f"app.port must be in 1..65535 (got {self.app.port})")
        if errs:
            where = f" in {self.source}" if self.source else ""
            raise ConfigError(f"invalid configuration{where}: " + "; ".join(errs))


# --------------------------------------------------------------------------- type coercion / merge
def _type_name(tp: Any) -> str:
    return getattr(tp, "__name__", None) or str(tp).replace("typing.", "")


def _coerce(value: Any, tp: Any, path: str) -> Any:
    """Check/convert a TOML value against a type hint; raises ConfigError naming ``path``."""
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if tp is Any:
        return value
    if origin in (typing.Union, types.UnionType):
        options = [a for a in args if a is not type(None)]
        if type(None) in args and (value is None or (isinstance(value, str) and value.strip().lower() in _NONE_WORDS)):
            return None
        for a in options:
            try:
                return _coerce(value, a, path)
            except ConfigError:
                pass
        names = " or ".join(_type_name(a) for a in options)
        if type(None) in args:
            names += ' (or "auto")'
        raise ConfigError(f"{path}: expected {names}, got {value!r}")
    if tp is bool:
        if isinstance(value, bool):
            return value
        raise ConfigError(f"{path}: expected true or false, got {value!r}")
    if tp is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        raise ConfigError(f"{path}: expected an integer, got {value!r}")
    if tp is float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        raise ConfigError(f"{path}: expected a number, got {value!r}")
    if tp is str:
        if isinstance(value, str):
            return value
        raise ConfigError(f"{path}: expected a string, got {value!r}")
    if tp is Path:
        if isinstance(value, str):
            return Path(value)
        raise ConfigError(f"{path}: expected a path string, got {value!r}")
    if origin in (list, tuple):
        if not isinstance(value, (list, tuple)):
            raise ConfigError(f"{path}: expected a list, got {value!r}")
        if origin is tuple and args and args[-1] is not Ellipsis:
            if len(value) != len(args):
                raise ConfigError(f"{path}: expected {len(args)} values, got {len(value)}")
            return tuple(_coerce(v, a, f"{path}[{i}]") for i, (v, a) in enumerate(zip(value, args)))
        item = args[0] if args else Any
        out = [_coerce(v, item, f"{path}[{i}]") for i, v in enumerate(value)]
        return tuple(out) if origin is tuple else out
    if origin is dict:
        if not isinstance(value, dict):
            raise ConfigError(f"{path}: expected a table, got {value!r}")
        _, vt = args if args else (str, Any)
        return {str(k): _coerce(v, vt, f"{path}.{k}") for k, v in value.items()}
    return value


def _toml_fields(obj: Any) -> list[dataclasses.Field]:
    return [f for f in dataclasses.fields(obj) if f.metadata.get("toml", True)]


def merge_into(obj: Any, data: dict, path: str = "", warnings: list[str] | None = None) -> list[str]:
    """Merge a parsed TOML table into the dataclass ``obj`` in place; returns the warnings."""
    warnings = [] if warnings is None else warnings
    hints = typing.get_type_hints(type(obj))
    fields = {f.name: f for f in _toml_fields(obj)}
    for key, value in data.items():
        p = f"{path}.{key}" if path else key
        if key not in fields:
            msg = f"unknown config key '{p}' (ignored)"
            close = difflib.get_close_matches(key, list(fields), n=1, cutoff=0.6)
            if close:
                msg += f"; did you mean '{close[0]}'?"
            warnings.append(msg)
            continue
        current = getattr(obj, key)
        tp = hints[key]
        if dataclasses.is_dataclass(current):
            if not isinstance(value, dict):
                raise ConfigError(f"[{p}] must be a table, got {value!r}")
            merge_into(current, value, p, warnings)
        elif typing.get_origin(tp) is dict and isinstance(current, dict) and p not in INLINE_TABLES:
            merged = dict(current)
            merged.update(_coerce(value, tp, p))
            setattr(obj, key, merged)
        else:
            setattr(obj, key, _coerce(value, tp, p))
    return warnings


def default_config_path() -> Path:
    """``$OHS_CONFIG`` if set, else ``~/.ohs/config.toml``."""
    env = os.environ.get(CONFIG_ENV)
    return Path(env).expanduser() if env else Path(DEFAULT_CONFIG_PATH).expanduser()


def _parse_toml(text: str, where: str) -> dict:
    if _toml is None:  # pragma: no cover
        raise ConfigError("reading TOML needs Python >= 3.11 (tomllib) or the 'tomli' package")
    try:
        return _toml.loads(text)
    except _toml.TOMLDecodeError as e:
        raise ConfigError(f"{where}: invalid TOML: {e}") from None


def load_config(path: str | Path | None = None, *, must_exist: bool = False) -> Config:
    """Defaults merged with the TOML file at ``path`` (default :func:`default_config_path`).

    A missing file gives the defaults, unless ``must_exist``. Unknown keys are logged as warnings
    and kept in ``Config.warnings``.
    """
    p = Path(path).expanduser() if path is not None else default_config_path()
    cfg = Config()
    if not p.exists():
        if must_exist:
            raise ConfigError(f"config file {p} not found (create it with: ohs --config \"{p}\" config --init)")
        log.debug("no config file at %s; using defaults", p)
        cfg.validate()
        return cfg
    data = _parse_toml(p.read_text(encoding="utf-8"), str(p))
    cfg.source = p
    cfg.warnings = merge_into(cfg, data)
    for w in cfg.warnings:
        log.warning("%s: %s", p, w)
    cfg.validate()
    return cfg


# --------------------------------------------------------------------------- template
_SECTION_DOCS = {
    "paths": "Files and folders (~ is your home folder).",
    "camera": (
        "Stereo camera (ELP dual OV9281, one USB device sending side-by-side frames).\n"
        "`ohs devices` lists the camera indices."
    ),
    "pico": "Pico microcontroller: emitters, trigger and IMU (firmware/README.md).",
    "emitters": "Emitter levels in percent (0-100). Lasers are Class 3R: never look into the beams.",
    "emitters.profiles": (
        "[vertical line laser, horizontal line laser, dot projector] per profile. The dot projector is\n"
        "either option (laser + DOE, or LED + printed mask); both use the Pico's dots channel (GP6).\n"
        "scan: ohs scan and ohs record (pick another one with `ohs record --profile NAME`);\n"
        "stereo / lasers / imu / check: the matching `ohs calibrate` step. Add your own profiles here."
    ),
    "live": "Live (preview-quality) processing during `ohs scan`. The recording is always at full resolution.",
    "process": "Offline processing of the final mesh (`ohs process`, `ohs scan --process`, the app's Make mesh).",
    "board": "ChArUco calibration board. `ohs printables` prints it; it must match the printed board.",
    "markers": "AprilTag stickers (tag36h11) for marker mode.",
    "app": "OpenHandScan Studio (`ohs app`): the local web server behind the app window.",
    "compute": "Where the heavy processing runs. A GPU (NVIDIA, AMD or Intel) is used when there is one.",
    "lasers": "The scanner's line lasers, for the laser-plane calibration (`ohs calibrate lasers`).",
}

_KEY_DOCS = {
    "paths.calibration": "Calibration file written by `ohs calibrate` and used by `ohs scan` / `ohs process`.",
    "paths.scans_dir": "Default folder for new scan and calibration recordings.",
    "paths.projects_dir": "Projects of OpenHandScan Studio (`ohs app`): one folder per project.",
    "paths.data_dir": "OpenHandScan Studio's own data: the setup progress and the simulator's sandbox calibration.",
    "paths.printables_dir": "Where OpenHandScan Studio writes the printables (board, AprilTags, LED projector mask).",
    "app.host": (
        'Address the server listens on. "127.0.0.1" = this PC only; "0.0.0.0" also serves the UI to\n'
        "phones and tablets on your network (no password: only on a network you trust)."
    ),
    "app.port": "TCP port (`ohs app --port` overrides it).",
    "compute.device": (
        '"auto" = the best GPU (a discrete one first), else the CPU; "cpu" = never the GPU; or part of a\n'
        'GPU\'s name, e.g. "RTX" or "Radeon". The environment variable OHS_COMPUTE overrides it.'
    ),
    "lasers.lines": (
        'One name per line laser. A name starts with its family: "vertical" (its line runs up and down in\n'
        'the image) or "horizontal", optionally followed by "_" and a suffix. The standard build has\n'
        '["vertical", "horizontal"]; the 4-laser build ["vertical", "horizontal", "vertical_2", "horizontal_2"].\n'
        "Within a family, list the lasers in this order: vertical ones from right to left (seen from\n"
        "behind the scanner: the largest x of the camera frame first), horizontal ones from top to\n"
        "bottom. The calibration finds the planes without knowing where the lasers sit and names them\n"
        "in that order."
    ),
    "lasers.own_channels": (
        "true: each laser has its own channel on the Pico (the 4-laser build's second lasers on GP0 and\n"
        "GP1, firmware 1.2.0 or later), so each can have its own brightness and the laser calibration\n"
        "lights them one at a time. false: each pair of lasers is wired to one channel (GP2 / GP3)."
    ),
    "lasers.brightness": (
        'A laser\'s level in percent of its family\'s level, e.g. { vertical_2 = 85 }. Lasers left out\n'
        "get 100. Use it to even out lasers of different strength; 0 switches a laser off."
    ),
    "camera.index": "OpenCV index of the stereo camera.",
    "camera.backend": (
        '"auto" (Windows: Media Foundation then DirectShow; Linux: V4L2), "dshow", "msmf", "v4l2" or "any".\n'
        "Media Foundation gives the camera's own frame times; DirectShow frames are timed on arrival."
    ),
    "camera.width": "Side-by-side frame size (both sensors together) and frame rate requested from the camera.",
    "camera.fourcc": "Pixel format. The 2560x800 modes need MJPG over USB 2.",
    "camera.exposure": (
        "Manual exposure in backend units; leave it out (or \"auto\") to keep the camera's own setting.\n"
        "DirectShow / MSMF: log2 of seconds, -7 = 1/128 s, -10 = ~1 ms. V4L2: 100 us units, 50 = 5 ms.\n"
        "`ohs preview` keys +/- try values live."
    ),
    "camera.gain": "Raw driver gain value, or leave it out.",
    "camera.swap_lr": "true if the camera sends right|left. Check with `ohs preview` (key s).",
    "camera.second_index": "Fallback only: right camera of two separate UVC cameras (NOT synchronised).",
    "pico.port": 'Serial port, e.g. "COM5" or "/dev/ttyACM0". Leave it out to auto-detect (USB VID 0x2E8A).',
    "pico.imu_rate_hz": "IMU rate: 104, 208, 416 or 833 Hz.",
    "pico.heartbeat_s": (
        "Advanced: heartbeat period while an emitter is on (the firmware switches emitters off\n"
        "1.5 s after the last host line), clock re-sync period, TIME round trips per sync,\n"
        "reply timeout and the clock-drift prior."
    ),
    "emitters.markers_dot_level": (
        "Dot projector level during LIVE marker-mode scans. AprilTags are not detected at the\n"
        "live 640x400 resolution under full-brightness dots; offline full-resolution processing copes."
    ),
    "live.scale": "Processing resolution relative to the camera (0.5 = 640x400 for 1280x800 sensors).",
    "live.depth_min_mm": "Stereo depth range (mm); the sweet spot is 200-350 mm.",
    "live.voxel_mm": (
        "Voxel size of the live model (mm). Leave it out (\"auto\") for the pipeline default for the\n"
        "processed resolution: 2 mm at 640x400, coarser for smaller (e.g. simulated) images."
    ),
    "live.max_waiting_mb": (
        "Recording: frames waiting to be written to the disk may take at most this much memory (MB,\n"
        "1024 = 1 GB, at least 64). Past it the recording stops and keeps every frame written so far."
    ),
    "live.max_waiting_seconds": "The same in seconds of frames waiting to be written (at least 1).",
    "process.voxel_mm": (
        "Voxel size of the final model (mm); `ohs process --voxel` overrides it. Leave it out (\"auto\")\n"
        "for 1 mm at 1280x800 (coarser for smaller images). Smaller is finer, slower and needs more RAM."
    ),
    "process.frame_step": "Use every n-th frame (the smallest step, at both Standard and Best quality).",
    "process.standard_frame_rate": (
        "Frames per second of scanning that Standard quality processes: 30 = every 2nd frame of the\n"
        "60 fps camera, every frame of a 30 fps recording. Best quality processes every frame (about\n"
        "twice the wait; less noise on smooth surfaces). 0 = Standard processes every frame too."
    ),
    "process.frame_motion_deg": (
        "Pick frames by motion (0 = off, every frame_step-th frame): process the next frame once the\n"
        "gyro has turned this many degrees since the last processed one, or once frame_max_gap\n"
        "recorded frames have passed. A gyro sees rotation only: sliding without turning is caught by\n"
        "the gap alone. Faster, but fewer frames average less noise away (VALIDATION.md). Only for\n"
        "Standard and Best quality, not the preview; `ohs process --motion` overrides it."
    ),
    "process.frame_max_gap": "Most recorded frames between two processed frames when frames are picked by motion.",
    "process.thin_walls": (
        "Keep thin walls and edges from swelling in the mesh: \"auto\" (on for scans without markers,\n"
        "off in marker mode), \"on\" or \"off\". A surface then counts as solid only 2 voxels deep\n"
        "behind where it was seen, so the two sides of a thin wall do not write through it\n"
        "(VALIDATION.md). Needs a GPU. If a mesh shows dents or many more pinholes, set \"off\"."
    ),
    "process.poisson": "Also build a watertight Poisson mesh.",
    "process.export": 'Mesh formats to write: "ply", "stl", "obj" (all in mm).',
    "process.repeatable_edits": (
        "The app's mesh edits (remove loose pieces, fill holes, smooth, simplify, remove the table)\n"
        "give the same result for the same mesh, whatever order it lists its points in: where two\n"
        "choices are equally good (which way to split a hole into triangles), the points' positions\n"
        "decide, not their numbers. Nothing measured changes. Each edit records the setting it ran\n"
        "with, so undo, redo and a step rebuilt later give the same triangles. Off: as before."
    ),
    "process.fine_walls": (
        "Make mesh also makes only the thin walls finer (ENGINE_PLAN X5 step 2), for every scan that has\n"
        "no choice of its own (the thin-parts hint sets one per scan): Follow the camera keeps each\n"
        "frame's depth on disk (about 7.8 MB a frame, removed afterwards), and after Straighten the\n"
        "thin walls found are made at mesh detail 0.5 mm. Needs a graphics card and mesh detail 1 mm;\n"
        "else Make mesh goes on without it and says why. Not at Preview quality. Off: as before."
    ),
    "process.refine_from_images": (
        "Make mesh also runs \"Refine from the images\" (a trial, ENGINE_PLAN X6), after Straighten and\n"
        "before Refine with lasers: the surface is measured against up to 120 frames of the recording.\n"
        "Needs a graphics card; without one Make mesh leaves the mesh as it is and says so. Not at\n"
        "Preview quality. Off: as before."
    ),
    "process.wall_reading": (
        "The viewer offers a wall's thickness read from the scan's camera frames at a click (ENGINE_PLAN\n"
        "X16): each side of a thin wall a plane fitted to the depth of the frames that see it. It says\n"
        "\"at most\": scans place a thin wall's two sides slightly too far apart, so the wall may be up to\n"
        "about 1 mm thinner. Needs a graphics card; not for scans tracked by markers. Nothing in the scan\n"
        "changes. Off: not offered."
    ),
    "process.hole_from_images": (
        "The viewer's Hole tool also reads the hole's edge from the scan's camera images at the click\n"
        "(ENGINE_PLAN X15): the diameter of the hole's entrance in the clicked face, read in up to 20\n"
        "frames, in 5 to 15 seconds, beside the mesh's own answer (which reads holes over open space\n"
        "too small, or not at all). Needs a graphics card; not for merged scans, scans tracked by\n"
        "markers or made at Preview quality. Nothing in the scan changes. Off: not offered."
    ),
    "process.align_hold_tags": (
        "Aligning two scans that share little of the part (under 70 %) from the AprilTags both saw keeps\n"
        "the tags' position instead of letting the shapes move it (ENGINE_PLAN X19): three or more tags that\n"
        "agree within 0.5 mm decide, the shapes only check them: when the shapes, starting at the tags, move\n"
        "the scan more than 2 mm, the tags are not held (a tag map wrong as a whole, as when the part was\n"
        "nudged against tags on the table). Scans that share more are aligned exactly as before.\n"
        "The Align page offers the hold for each alignment either way; this setting only makes it the\n"
        "default. Off: as before."
    ),
    "process.straighten_tags": (
        "Straighten also holds the scan to the AprilTags it sees (ENGINE_PLAN X8 step 2): tags on the\n"
        "table along a long part and across its ends keep the passes from sliding along it. Their printed\n"
        "size need not be known. A tag the others do not confirm is left out and said; a scan without\n"
        "tags comes out exactly as before. Off: as before."
    ),
    "board.squares_x": "Chessboard squares, square and marker size (mm) and ArUco dictionary of the board.",
    "board.paper": '"letter" or "a4" (used by `ohs printables`).',
    "markers.tag_mm": "Side of the black square of the printed tags (mm).",
}

_EXAMPLES = {"camera.exposure": -7, "camera.gain": 0, "camera.second_index": 1, "pico.port": "COM5",
             "live.voxel_mm": 2.0, "process.voxel_mm": 1.0}

_HEADER = """\
# OpenHandScan configuration (TOML), written by `ohs config --init`.
# Every setting is optional: delete a line to use the built-in default.
# Units: millimetres, seconds, emitter levels in percent (0-100).
# `ohs config --show` prints the settings actually in use.
"""


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
        r = repr(v)
        return r if any(ch in r for ch in ".en") else r + ".0"
    if isinstance(v, (str, Path)):
        return json.dumps(str(v), ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):  # an inline table on one line, e.g. lasers.brightness
        return "{ " + ", ".join(f"{_toml_key(str(k))} = {_toml_value(x)}" for k, x in v.items()) + " }" if v else "{}"
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def _toml_key(k: str) -> str:
    return k if _BARE_KEY.match(k) else json.dumps(k)


def _comment(lines: list[str], text: str | None) -> None:
    if text:
        lines.extend("# " + ln if ln else "#" for ln in text.splitlines())


#: dict fields written as one inline table (``brightness = { vertical_2 = 85.0 }``) and replaced as
#: a whole when a file sets them; other dict fields (``emitters.profiles``) are tables of their own
#: whose keys a file adds to the defaults
INLINE_TABLES = {"lasers.brightness"}


def _emit_table(obj: Any, path: str, lines: list[str], comments: bool) -> None:
    subtables: list[tuple[str, Any]] = []
    for f in _toml_fields(obj):
        v = getattr(obj, f.name)
        p = f"{path}.{f.name}"
        if dataclasses.is_dataclass(v) or (isinstance(v, dict) and p not in INLINE_TABLES):
            subtables.append((f.name, v))
            continue
        if comments:
            _comment(lines, _KEY_DOCS.get(p))
        if v is None:
            if comments and p in _EXAMPLES:
                lines.append(f"# {_toml_key(f.name)} = {_toml_value(_EXAMPLES[p])}")
            else:
                lines.append(f'# {_toml_key(f.name)} = "auto"  (not set)')
        else:
            lines.append(f"{_toml_key(f.name)} = {_toml_value(v)}")
    for name, v in subtables:
        p = f"{path}.{name}"
        lines.append("")
        if comments:
            _comment(lines, _SECTION_DOCS.get(p))
        lines.append(f"[{p}]")
        if isinstance(v, dict):
            for k, item in v.items():
                lines.append(f"{_toml_key(str(k))} = {_toml_value(item)}")
        else:
            _emit_table(v, p, lines, comments)


def render_template(cfg: Config | None = None, comments: bool = True) -> str:
    """TOML text of ``cfg`` (default: the built-in defaults), commented unless ``comments=False``."""
    cfg = cfg if cfg is not None else Config()
    lines: list[str] = [_HEADER.rstrip("\n")] if comments else []
    for f in _toml_fields(cfg):
        lines.append("")
        if comments:
            _comment(lines, _SECTION_DOCS.get(f.name))
        lines.append(f"[{f.name}]")
        _emit_table(getattr(cfg, f.name), f.name, lines, comments)
    return "\n".join(lines).lstrip("\n") + "\n"


def write_template(path: str | Path | None = None, force: bool = False) -> Path:
    """Write the commented default configuration to ``path`` (default :func:`default_config_path`)."""
    p = Path(path).expanduser() if path is not None else default_config_path()
    if p.exists() and not force:
        raise FileExistsError(f"{p} already exists (use --force to overwrite it)")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_template(), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- in-place writer
_HEADER_LINE = re.compile(r"^\s*\[(?!\[)\s*([^\[\]#]+?)\s*\](?!\])\s*(#.*)?$")


def _value_end(text: str) -> int:
    """Index just past the TOML value at the start of ``text`` (single line), or -1 if it continues."""
    i, n = 0, len(text)
    if text.startswith(('"""', "'''")):
        close = text.find(text[:3], 3)
        return -1 if close < 0 else close + 3
    depth = 0
    while i < n:
        ch = text[i]
        if ch in "\"'":
            j = i + 1
            while j < n and text[j] != ch:
                j += 2 if (ch == '"' and text[j] == "\\") else 1
            if j >= n:
                return -1
            i = j + 1
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "#" and depth <= 0:
            return i
        i += 1
    return n if depth <= 0 else -1


def _key_line(line: str, key: str) -> bool:
    m = re.match(r'^\s*("([^"\\]|\\.)*"|\'[^\']*\'|[A-Za-z0-9_-]+)\s*=', line)
    if not m:
        return False
    k = m.group(1)
    if k[0] in "\"'":
        try:
            k = json.loads(k) if k[0] == '"' else k[1:-1]
        except ValueError:
            return False
    return k == key


def _set_key(lines: list[str], section: str, key: str, value: Any) -> list[str]:
    """Lines with ``key = value`` set in ``[section]`` (replaced, inserted, or a new section appended)."""
    new = f"{_toml_key(key)} = {_toml_value(value)}"
    start = next((i for i, ln in enumerate(lines) if (m := _HEADER_LINE.match(ln)) and m.group(1).strip() == section), None)
    if start is None:
        while lines and not lines[-1].strip():
            lines = lines[:-1]
        return lines + ["", f"[{section}]", new]
    end = next((i for i in range(start + 1, len(lines))
                if _HEADER_LINE.match(lines[i]) or lines[i].lstrip().startswith("[[")), len(lines))
    for j in range(start + 1, end):
        if not _key_line(lines[j], key):
            continue
        indent = lines[j][: len(lines[j]) - len(lines[j].lstrip())]
        rest = lines[j].split("=", 1)[1].lstrip()
        stop = _value_end(rest)
        k = j
        if stop < 0:  # a value continued on the next lines (multi-line array or string): replace them all
            joined = rest
            while stop < 0 and k + 1 < end:
                k += 1
                joined += "\n" + lines[k]
                stop = _value_end(joined)
            rest = joined if stop >= 0 else ""
            stop = max(stop, 0)
        comment = rest[stop:].strip() if "\n" not in rest[stop:] else ""
        repl = indent + new + (f"  {comment}" if comment.startswith("#") else "")
        return lines[:j] + [repl] + lines[k + 1:]
    last = start
    for j in range(start + 1, end):
        s = lines[j].strip()
        if s and not s.startswith("#"):
            last = j
    return lines[: last + 1] + [new] + lines[last + 1:]


def _nested(updates: dict[str, dict[str, Any]]) -> dict:
    """``{"emitters.profiles": {...}}`` -> ``{"emitters": {"profiles": {...}}}`` (for :func:`merge_into`)."""
    out: dict = {}
    for section, values in updates.items():
        node = out
        for part in section.split("."):
            node = node.setdefault(part, {})
        node.update(values)
    return out


def set_config_values(updates: dict[str, dict[str, Any]], path: str | Path | None = None, *,
                      cfg: Config | None = None) -> Path:
    """Set ``[section] key = value`` entries in the config file ``path`` (default :func:`default_config_path`).

    A line-based edit: an existing ``key = ...`` line of the section is replaced (its trailing
    comment is kept), a missing key is inserted after the section's last setting, and a missing
    section is appended. Comments and all other lines stay as they are. Without a file, the
    commented template (:func:`render_template`) is written first. The result is parsed and
    validated before it replaces the file atomically; :class:`ConfigError` (nothing written) if it
    is invalid or names an unknown key. ``cfg`` (e.g. the running app's configuration) is updated
    to the new values as well. Returns the path written.
    """
    p = Path(path).expanduser() if path is not None else default_config_path()
    for section, values in updates.items():
        if not section or not all(_BARE_KEY.match(part) for part in section.split(".")):
            raise ConfigError(f"invalid config section name {section!r}")
        if not isinstance(values, dict):
            raise ConfigError(f"[{section}]: expected a table of values")
    exists = p.exists()
    raw = p.read_bytes().decode("utf-8") if exists else render_template()
    newline = "\r\n" if "\r\n" in raw else ("\n" if exists else os.linesep)
    lines = raw.replace("\r\n", "\n").split("\n")
    trailing = bool(lines) and lines[-1] == ""
    if trailing:
        lines = lines[:-1]
    for section, values in updates.items():
        for key, value in values.items():
            lines = _set_key(lines, section, str(key), value)
    text = "\n".join(lines) + ("\n" if trailing or not exists else "")
    data = _parse_toml(text, str(p))
    check = Config()
    unknown = [w for w in merge_into(check, data) if any(f"'{s}.{k}'" in w for s, vals in updates.items() for k in vals)]
    if unknown:
        raise ConfigError("; ".join(unknown))
    check.source = p
    check.validate()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text.replace("\n", newline))
    for attempt in range(50):  # Windows: an editor or a virus scanner may hold the file for a moment
        try:
            os.replace(tmp, p)
            break
        except PermissionError:
            if attempt == 49:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.02)
    if cfg is not None:
        merge_into(cfg, _nested(updates))
        if cfg.source is None:
            cfg.source = p
    log.info("config %s: set %s", p, ", ".join(f"{s}.{k}" for s, vals in updates.items() for k in vals))
    return p
