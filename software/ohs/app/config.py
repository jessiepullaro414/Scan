"""Configuration for the ``ohs`` app: a dataclass tree with defaults, TOML loading and a commented template.

* :class:`Config` holds :class:`~ohs.devices.camera.CameraParams` and
  :class:`~ohs.devices.pico.PicoParams` unchanged, plus the app's own sections: emitter levels per
  profile, live (preview-quality) processing, offline processing defaults, the ChArUco board, the
  AprilTag size, the default paths (including the Studio projects folder) and ``[app]`` (the
  host and port of ``ohs app``).
* :func:`load_config` reads ``~/.ohs/config.toml`` (or ``$OHS_CONFIG``, or an explicit path) with
  :mod:`tomllib` and merges it over the defaults. Every key is optional. Unknown keys produce a
  warning with a "did you mean" hint and are ignored. Values of the wrong type raise
  :class:`ConfigError`, which names the key. Integers are accepted where floats are expected.
* :func:`render_template` produces a commented TOML file holding every default
  (``ohs config --init``). Values that default to ``None`` appear as commented-out examples.
  Loading the template gives exactly the defaults.

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
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ohs.devices.camera import CameraParams
from ohs.devices.pico import PicoParams

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
    "Config",
    "default_config_path",
    "load_config",
    "merge_into",
    "render_template",
    "write_template",
]

log = logging.getLogger(__name__)

CONFIG_ENV = "OHS_CONFIG"
DEFAULT_CONFIG_PATH = "~/.ohs/config.toml"
EMITTER_NAMES = ("vertical", "horizontal", "dots")
CAMERA_BACKENDS = ("auto", "dshow", "msmf", "v4l2", "any")
IMU_RATES = (104, 208, 416, 833)
EXPORT_FORMATS = ("ply", "stl", "obj")
PAPERS = ("letter", "a4")

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

    @property
    def calibration_path(self) -> Path:
        return Path(self.calibration).expanduser()

    @property
    def scans_path(self) -> Path:
        return Path(self.scans_dir).expanduser()

    @property
    def projects_path(self) -> Path:
        return Path(self.projects_dir).expanduser()


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


@dataclass
class ProcessConfig:
    voxel_mm: float | None = None  # None = pipeline default for the resolution (1 mm at 1280x800)
    frame_step: int = 1
    poisson: bool = False
    export: list[str] = field(default_factory=lambda: ["ply", "stl"])


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
        if self.process.frame_step < 1:
            errs.append("process.frame_step must be >= 1")
        bad = [e for e in self.process.export if e.lower() not in EXPORT_FORMATS]
        if bad:
            errs.append(f"process.export: unknown format(s) {bad}; use {', '.join(EXPORT_FORMATS)}")
        if self.board.paper.lower() not in PAPERS:
            errs.append(f"board.paper must be one of {', '.join(PAPERS)}")
        try:
            self.board.spec()
        except ValueError as e:
            errs.append(f"board: {e}")
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
        elif typing.get_origin(tp) is dict and isinstance(current, dict):
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
    "live": "Live (preview-quality) processing during `ohs scan`. The recording is always full quality.",
    "process": "Offline full-quality processing (`ohs process`, `ohs scan --process`).",
    "board": "ChArUco calibration board. `ohs printables` prints it; it must match the printed board.",
    "markers": "AprilTag stickers (tag36h11) for marker mode.",
    "app": "OpenHandScan Studio (`ohs app`): the local web server behind the app window.",
}

_KEY_DOCS = {
    "paths.calibration": "Calibration file written by `ohs calibrate` and used by `ohs scan` / `ohs process`.",
    "paths.scans_dir": "Default folder for new scan and calibration recordings.",
    "paths.projects_dir": "Projects of OpenHandScan Studio (`ohs app`): one folder per project.",
    "app.host": (
        'Address the server listens on. "127.0.0.1" = this PC only; "0.0.0.0" also serves the UI to\n'
        "phones and tablets on your network (no password: only on a network you trust)."
    ),
    "app.port": "TCP port (`ohs app --port` overrides it).",
    "camera.index": "OpenCV index of the stereo camera.",
    "camera.backend": (
        '"auto" (Windows: DirectShow then Media Foundation; Linux: V4L2), "dshow", "msmf", "v4l2" or "any".'
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
    "process.voxel_mm": (
        "Voxel size of the final model (mm); `ohs process --voxel` overrides it. Leave it out (\"auto\")\n"
        "for 1 mm at 1280x800 (coarser for smaller images). Smaller is finer, slower and needs more RAM."
    ),
    "process.frame_step": "Use every n-th frame.",
    "process.poisson": "Also build a watertight Poisson mesh.",
    "process.export": 'Mesh formats to write: "ply", "stl", "obj" (all in mm).',
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
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def _toml_key(k: str) -> str:
    return k if _BARE_KEY.match(k) else json.dumps(k)


def _comment(lines: list[str], text: str | None) -> None:
    if text:
        lines.extend("# " + ln if ln else "#" for ln in text.splitlines())


def _emit_table(obj: Any, path: str, lines: list[str], comments: bool) -> None:
    subtables: list[tuple[str, Any]] = []
    for f in _toml_fields(obj):
        v = getattr(obj, f.name)
        p = f"{path}.{f.name}"
        if dataclasses.is_dataclass(v) or isinstance(v, dict):
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
