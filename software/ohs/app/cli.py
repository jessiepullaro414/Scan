"""``ohs``: the OpenHandScan command-line tool (argparse sub-commands).

Every command has ``--help``. The global options work before or after the command:
``--config PATH`` (TOML settings; default ``~/.ohs/config.toml`` or ``$OHS_CONFIG``),
``--calib PATH`` (calibration file; default ``paths.calibration``) and ``-v``/``-vv`` (more log output).

Exit codes: 0 ok, 1 error (or a failed self-test / calibration check), 2 usage error, 130 interrupted.

The heavy or optional parts (the live and offline pipelines, Open3D, the calibration flows) are
imported inside the command handlers. ``ohs --help`` and ``ohs config`` therefore stay fast, and a
missing module only affects the commands that need it.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from ohs import __version__
from ohs.app.config import ConfigError, default_config_path, load_config, render_template, write_template

__all__ = ["main", "build_parser", "CliError", "ProgressBar", "COMMANDS"]

log = logging.getLogger("ohs.app.cli")

COMMANDS = ("devices", "selftest", "config", "printables", "preview", "record", "calibrate", "scan", "process", "sim")
MODES = ("markerless", "markers")
SCENES = ("sphere_on_plane", "car_panel", "bracket")
CALIB_KINDS = ("stereo", "lasers", "imu", "check")

DESCRIPTION = "OpenHandScan: handheld stereo + laser 3D scanner."
EPILOG = """\
typical workflow:
  ohs devices                    find the camera and the Pico
  ohs selftest                   check camera, IMU, clock, emitters and trigger
  ohs printables print/          calibration board, AprilTag stickers, LED projector mask
  ohs calibrate stereo           then: ohs calibrate lasers, ohs calibrate imu
  ohs scan --process             scan (trigger starts/stops), then build the final mesh
  ohs process <recording>        (re)process a recording at full quality

no hardware yet?
  ohs sim demo                   simulated scan recording
  ohs process demo               final mesh from it
  ohs scan --replay demo         live view on the replayed recording

run `ohs <command> --help` for the options of a command."""

GUIDE_STEREO = "15-25 views, 200-500 mm, tilts 20-45 deg, cover corners and edges"
GUIDE_LASERS = "6-10 poses, 150-450 mm, +-30 deg tilt, both lines crossing the white page margin"
GUIDE_IMU = "hold still 2 s, then rotate about all axes for 20-60 s"


class CliError(Exception):
    """A user-facing error: printed without a traceback, exit code 1."""


class _Fmt(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=30)


# --------------------------------------------------------------------------- output helpers
def _say(msg: str = "") -> None:
    print(msg, flush=True)


def _warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr, flush=True)


class _StderrHandler(logging.Handler):
    """Logging to whatever ``sys.stderr`` is at the time (pytest swaps it per test)."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            print(msg, file=sys.stderr, flush=True)
        except Exception:  # pragma: no cover
            self.handleError(record)


class _Formatter(logging.Formatter):
    def __init__(self, verbose: int) -> None:
        super().__init__()
        self.verbose = verbose

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if record.exc_info and self.verbose:
            msg += "\n" + self.formatException(record.exc_info)
        if self.verbose >= 1:
            return f"{record.levelname.lower()}: {record.name}: {msg}"
        return f"{record.levelname.lower()}: {msg}"


def _install_logging(verbose: int) -> tuple[logging.Handler, int]:
    level = logging.WARNING if verbose <= 0 else logging.INFO if verbose == 1 else logging.DEBUG
    logger = logging.getLogger("ohs")
    handler = _StderrHandler(level)
    handler.setFormatter(_Formatter(verbose))
    old = logger.level
    logger.addHandler(handler)
    logger.setLevel(level)
    return handler, old


def _remove_logging(state: tuple[logging.Handler, int]) -> None:
    handler, old = state
    logger = logging.getLogger("ohs")
    logger.removeHandler(handler)
    logger.setLevel(old)


def _mmss(seconds: float) -> str:
    m, s = divmod(int(max(0, seconds)), 60)
    return f"{m}:{s:02d}"


def _result_note(obj: Any) -> str | None:
    """Short status of a per-frame result passed to a progress callback (e.g. a FrameResult)."""
    track = getattr(obj, "track", None)
    if track is None or getattr(track, "ok", True):
        return None
    return f"lost: {getattr(track, 'info', {}).get('reason', '?')}"


def _parse_progress(args: tuple, kwargs: dict) -> tuple[float, float | None, str | None]:
    """Accepts ``(done, total)``, ``(done, total, msg | result)``, ``(fraction,)``, a dict, or keywords."""
    vals = list(args)
    kw = dict(kwargs)
    if len(vals) == 1 and isinstance(vals[0], dict):
        kw = {**vals.pop(0), **kw}
    if len(vals) >= 3 and not isinstance(vals[2], (str, int, float)):
        note = _result_note(vals.pop(2))
        if note and "message" not in kw:
            kw["message"] = note

    def pick(*names: str) -> Any:
        for n in names:
            if kw.get(n) is not None:
                return kw[n]
        return None

    done = pick("done", "index", "i", "frame", "current", "n_done")
    total = pick("total", "n", "n_frames", "count")
    msg = pick("message", "msg", "stage", "status")
    nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    strs = [v for v in vals if isinstance(v, str)]
    if done is None and nums:
        done = nums[0]
    if total is None and len(nums) > 1:
        total = nums[1]
    if msg is None and strs:
        msg = strs[0]
    if done is None:
        raise ValueError("no progress value")
    if total is None and isinstance(done, float) and 0.0 <= done <= 1.0:
        return 100.0 * done, 100.0, msg
    return float(done), (float(total) if total else None), msg


class ProgressBar:
    """Console progress bar, usable directly as a ``progress`` callback.

    With ``zero_based`` (the convention of ``process_recording`` and the calibration flows:
    ``progress(k, n, ...)`` after item ``k`` counting from 0) a positional count is shown as ``k + 1``.
    """

    def __init__(self, label: str = "processing", stream: Any = None, width: int = 28, zero_based: bool = True) -> None:
        self.label, self.stream, self.width = label, stream, width
        self.zero_based = zero_based
        self.done: float = 0.0
        self.total: float | None = None
        self._t0 = time.perf_counter()
        self._last = 0.0
        self._decile = -1
        self._shown = False
        self._len = 0

    @property
    def out(self) -> Any:
        return self.stream if self.stream is not None else sys.stderr

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        try:
            done, total, msg = _parse_progress(args, kwargs)
        except Exception:
            return
        if self.zero_based and args and isinstance(args[0], int) and not isinstance(args[0], bool) and total:
            done = min(done + 1, total)
        self.update(done, total, msg)

    def update(self, done: float, total: float | None = None, msg: str | None = None) -> None:
        now = time.perf_counter()
        self.done = float(done)
        if total:
            self.total = float(total)
        final = self.total is not None and self.done >= self.total
        tty = bool(getattr(self.out, "isatty", lambda: False)())
        if tty:
            if now - self._last < 0.1 and not final:
                return
        else:
            decile = int(10 * self.done / self.total) if self.total else int(now - self._t0) // 10
            if decile == self._decile and not final:
                return
            self._decile = decile
        line = self._line(now, msg)
        if tty:
            self.out.write("\r" + line.ljust(self._len))
            self._len = len(line)
        else:
            self.out.write(line + "\n")
        self.out.flush()
        self._shown, self._last = True, now

    def _line(self, now: float, msg: str | None) -> str:
        el = now - self._t0
        if self.total:
            frac = min(1.0, max(0.0, self.done / self.total))
            n = int(round(frac * self.width))
            rate = self.done / el if el > 0 else 0.0
            eta = (self.total - self.done) / rate if rate > 0 else 0.0
            s = (f"{self.label} [{'#' * n}{'.' * (self.width - n)}] {self.done:.0f}/{self.total:.0f} "
                 f"{100 * frac:3.0f}%  {rate:.1f}/s  ETA {_mmss(eta)}")
        else:
            s = f"{self.label}: {self.done:.0f} done, {el:.0f} s"
        return s + (f"  {msg}" if msg else "")

    def close(self) -> None:
        if self._shown and getattr(self.out, "isatty", lambda: False)():
            self.out.write("\n")
            self.out.flush()


# --------------------------------------------------------------------------- argument types
def _size(text: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*[xX*,]\s*(\d+)\s*", text)
    if not m:
        raise argparse.ArgumentTypeError(f"expected WIDTHxHEIGHT such as 1280x800, got {text!r}")
    w, h = int(m[1]), int(m[2])
    if w < 32 or h < 32 or w % 2 or h % 2:
        raise argparse.ArgumentTypeError("width and height must be even and at least 32")
    return w, h


def _pos_int(text: str) -> int:
    try:
        v = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {text!r}") from None
    if v < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return v


def _nonneg_int(text: str) -> int:
    try:
        v = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {text!r}") from None
    if v < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return v


def _pos_float(text: str) -> float:
    try:
        v = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {text!r}") from None
    if not v > 0:
        raise argparse.ArgumentTypeError("must be positive")
    return v


# --------------------------------------------------------------------------- parser
def _common(p: argparse.ArgumentParser, sub: bool) -> None:
    d = argparse.SUPPRESS if sub else None
    g = p.add_argument_group("global options")
    g.add_argument("--config", metavar="PATH", default=d,
                   help="settings file (default: $OHS_CONFIG or ~/.ohs/config.toml)")
    g.add_argument("--calib", metavar="PATH", default=d, help="calibration file (default: paths.calibration in the config)")
    g.add_argument("-v", "--verbose", action="count", default=argparse.SUPPRESS if sub else 0,
                   help="more log output (-vv: debug)")


def _live_options(p: argparse.ArgumentParser, replay: bool = True, frames_help: str = "stop after N recorded frames") -> None:
    if replay:
        p.add_argument("--replay", metavar="REC", help="play a recording instead of the camera (no Pico; IMU comes from the recording)")
    p.add_argument("--no-pico", action="store_true", help="run without the Pico (no emitters, IMU or trigger)")
    p.add_argument("--no-gui", action="store_true", help="no window: print a status line every second")
    p.add_argument("--max-frames", type=_pos_int, metavar="N", help=frames_help)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ohs", description=DESCRIPTION, epilog=EPILOG, formatter_class=_Fmt)
    parser.add_argument("--version", action="version", version=f"ohs {__version__}")
    _common(parser, sub=False)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", title="commands")

    def add(name: str, help: str, description: str | None = None, epilog: str | None = None) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help, description=description or help, epilog=epilog, formatter_class=_Fmt)
        _common(sp, sub=True)
        return sp

    p = add("devices", "list cameras and the Pico's serial port",
            epilog="Camera sizes are each device's default mode; ohs asks for camera.width x camera.height (2560x800).")
    p.add_argument("--max-index", type=_pos_int, default=8, metavar="N", help="probe camera indices 0..N-1 (default: 8)")
    p.add_argument("--backend", choices=("auto", "dshow", "msmf", "v4l2", "any"), help="camera backend (default: camera.backend)")
    p.set_defaults(func=cmd_devices)

    p = add("selftest", "check the camera, Pico, IMU, clock sync, emitters and trigger",
            description="Hardware self-test: camera resolution and measured fps; Pico HELLO, IMU rate and clock-sync\n"
                        "round trip; each emitter switched on for 1 s (confirm visually); a trigger press. Prints a\n"
                        "PASS/WARN/FAIL summary and exits with 1 if anything failed.",
            epilog="Point the scanner at a wall 20-40 cm away. The lasers are Class 3R: never look into them.")
    p.add_argument("--seconds", type=_pos_float, default=3.0, metavar="N",
                   help="measuring time for the camera/IMU and for the trigger watch (default: 3)")
    p.add_argument("--emitter-seconds", type=_pos_float, default=1.0, metavar="S", help="on-time per emitter (default: 1)")
    p.add_argument("--no-camera", action="store_true", help="skip the camera checks")
    p.add_argument("--no-pico", action="store_true", help="skip the Pico checks (IMU, clock, emitters, trigger)")
    p.add_argument("--no-emitters", action="store_true", help="do not switch the emitters on")
    p.set_defaults(func=cmd_selftest)

    p = add("config", "write a commented settings template, or show the settings in use",
            epilog=f"The file is TOML; every key is optional. Default location: {default_config_path()}")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--init", action="store_true", help="write the commented template to the config path")
    g.add_argument("--show", action="store_true", help="print the merged settings (defaults + file)")
    p.add_argument("--force", action="store_true", help="with --init: overwrite an existing file")
    p.set_defaults(func=cmd_config)

    p = add("printables", "write the ChArUco board, AprilTag stickers and LED projector mask to print",
            epilog="Print at 100% / actual size (no 'fit to page') and check the 100 mm scale bar.\n"
                   "Glue the board flat to glass; print the tags on matte removable label paper.")
    p.add_argument("out_dir", help="output folder")
    p.add_argument("--paper", choices=("letter", "a4"), help="paper size (default: board.paper)")
    p.add_argument("--dpi", type=_pos_int, default=300, help="board/tag resolution (default: 300)")
    p.add_argument("--tag-mm", type=_pos_float, help="AprilTag size in mm (default: markers.tag_mm)")
    p.add_argument("--tags", type=_pos_int, default=48, metavar="N", help="number of AprilTags, ids 0..N-1 (default: 48)")
    p.add_argument("--mask-dpi", type=_pos_int, default=1200, help="LED projector mask sheet resolution (default: 1200)")
    p.add_argument("--mask-dot-um", type=_pos_float, default=42.0, metavar="UM",
                   help="dot size of the laser-printed LED projector mask in micrometres (default: 42)")
    p.add_argument("--no-mask", action="store_true", help="skip the LED projector mask files")
    p.set_defaults(func=cmd_printables)

    p = add("preview", "live view: raw or rectified, laser overlay, depth",
            epilog="keys: 1/2/3 toggle vertical laser / horizontal laser / dots, SPACE all emitters, +/- exposure,\n"
                   "s swap L/R, l laser overlay, d depth colour map, r raw/rectified, g guide grid, q quit.")
    p.add_argument("--raw", action="store_true", help="start with raw images even if a calibration exists")
    p.add_argument("--depth", action="store_true", help="start with the depth colour map (needs a calibration)")
    _live_options(p, frames_help="stop after N frames (for testing)")
    p.set_defaults(func=cmd_preview)

    p = add("record", "raw recording (frames + IMU + events); the trigger or SPACE starts and stops it")
    p.add_argument("out", help="recording folder (must be new or empty)")
    p.add_argument("--profile", default="scan", help="emitter profile while recording (config emitters.profiles; default: scan)")
    p.add_argument("--seconds", type=_pos_float, metavar="S", help="stop after S seconds of recording")
    p.add_argument("--start-now", action="store_true", help="start immediately instead of waiting for the trigger")
    _live_options(p)
    p.set_defaults(func=cmd_record)

    p = add("calibrate", "calibrate the stereo pair, the laser planes or the IMU, or check the calibration",
            description="Interactive calibration capture with a live overlay, then the calibration itself.\n"
                        "  stereo  (emitters off)  " + GUIDE_STEREO + "\n"
                        "  lasers  (lines on)      " + GUIDE_LASERS + "\n"
                        "  imu     (emitters off)  " + GUIDE_IMU + "\n"
                        "  check   (lines on)      3-5 views; reports whether the calibration still holds",
            epilog="SPACE or the trigger captures a view (imu: starts/stops the recording); q finishes and calibrates;\n"
                   "ESC aborts. A new stereo calibration drops the laser planes and the IMU calibration unless --keep.")
    p.add_argument("kind", choices=CALIB_KINDS, help="what to calibrate")
    p.add_argument("--from", dest="from_rec", metavar="REC", help="use an existing capture recording (skip the capture)")
    p.add_argument("--keep", action="store_true", help="stereo: keep the laser planes and IMU calibration")
    p.add_argument("--out", metavar="DIR", help="capture recording folder (default: <scans_dir>/calibration/<kind>-<time>)")
    p.add_argument("--views", type=_pos_int, metavar="N", help="finish automatically after N views")
    p.add_argument("--seconds", type=_pos_float, metavar="S", help="imu: stop after S seconds (default: 62)")
    p.add_argument("--step", type=_pos_int, default=1, metavar="N",
                   help="use every N-th frame of the recording (e.g. 2 halves the IMU calibration time)")
    p.add_argument("--no-save", action="store_true", help="print the result but do not write the calibration")
    p.add_argument("--no-pico", action="store_true", help="run without the Pico (not for lasers/imu: no emitters, no IMU)")
    p.add_argument("--no-gui", action="store_true", help="no window: capture with the trigger, finish with --views N")
    p.set_defaults(func=cmd_calibrate)

    p = add("scan", "scan: live preview and tracking, always recording the raw stream",
            epilog="The trigger or SPACE starts and stops the scan; q stops and quits. Hold the scanner 200-350 mm\n"
                   "from the surface and sweep slowly with overlap. The live model is preview quality: the final\n"
                   "model comes from `ohs process` (or --process).")
    p.add_argument("--mode", choices=MODES, default="markerless", help="tracking mode (default: markerless)")
    p.add_argument("--out", metavar="DIR", help="recording folder (default: <scans_dir>/scan-<time>)")
    p.add_argument("--process", action="store_true", help="run the full-quality processing afterwards")
    p.add_argument("--seconds", type=_pos_float, metavar="S", help="stop after S seconds of scanning")
    p.add_argument("--start-now", action="store_true", help="start immediately instead of waiting for the trigger")
    _live_options(p)
    p.set_defaults(func=cmd_scan)

    p = add("process", "full-quality processing of a recording: mesh (PLY/STL, mm), laser points, trajectory")
    p.add_argument("recording", help="recording folder")
    p.add_argument("--mode", choices=MODES, help="tracking mode (default: the recording's mode, else markerless)")
    p.add_argument("--out", metavar="DIR", help="output folder (default: <recording>/result)")
    p.add_argument("--voxel", type=_pos_float, metavar="MM",
                   help="TSDF voxel size in mm (default: process.voxel_mm, else 1 mm at 1280x800)")
    p.add_argument("--step", type=_pos_int, metavar="N", help="use every N-th frame (default: process.frame_step)")
    p.add_argument("--poisson", action="store_true", help="also build a watertight Poisson mesh")
    p.add_argument("--start", type=_nonneg_int, default=0, metavar="I", help="first frame (default: 0)")
    p.add_argument("--stop", type=_pos_int, metavar="I", help="stop before this frame")
    p.set_defaults(func=cmd_process)

    p = add("sim", "simulate a scan recording (try everything without hardware)",
            epilog="About 1 s per 1280x800 frame. Then: ohs process <out_dir>, or ohs scan --replay <out_dir>.")
    p.add_argument("out_dir", help="recording folder to create")
    p.add_argument("--scene", choices=SCENES, default="sphere_on_plane", help="scene (default: sphere_on_plane)")
    p.add_argument("--frames", type=_pos_int, default=60, metavar="N", help="number of frames (default: 60)")
    p.add_argument("--size", type=_size, default=(1280, 800), metavar="WxH", help="image size per camera (default: 1280x800)")
    p.add_argument("--fps", type=_pos_float, default=30.0, help="frame rate (default: 30)")
    p.add_argument("--seed", type=int, default=0, help="random seed (default: 0)")
    p.add_argument("--realistic", action="store_true", help="lens distortion and imperfect stereo geometry")
    p.add_argument("--gt-depth-every", type=_nonneg_int, default=0, metavar="N",
                   help="keep ground-truth depth maps for every N-th frame (default: 0 = none; 4 MB each)")
    p.add_argument("--overwrite", action="store_true", help="write into a non-empty folder")
    p.set_defaults(func=cmd_sim)
    return parser


# --------------------------------------------------------------------------- shared helpers
def _config(args: argparse.Namespace):
    path = getattr(args, "config", None)
    return load_config(path, must_exist=path is not None)


def _calib_path(args: argparse.Namespace, cfg: Any) -> Path:
    c = getattr(args, "calib", None)
    return Path(c).expanduser() if c else cfg.paths.calibration_path


def _load_calib(path: Path):
    from ohs.types import ScannerCalibration

    try:
        return ScannerCalibration.load(path)
    except (OSError, ValueError, KeyError) as e:
        raise CliError(f"cannot read the calibration {path}: {e}") from None


def _recording(path: str | Path, what: str) -> Path:
    p = Path(path).expanduser()
    if not (p / "frames.csv").exists():
        raise CliError(f"{what} {p} is not a recording folder (no frames.csv)")
    return p


def _optional_calib(args: argparse.Namespace, cfg: Any, replay: Path | None):
    """Calibration for display purposes: --calib, the replayed recording's, the configured one, or None."""
    if getattr(args, "calib", None):
        return _load_calib(_calib_path(args, cfg))
    if replay is not None and (replay / "calibration.json").exists():
        return _load_calib(replay / "calibration.json")
    p = cfg.paths.calibration_path
    return _load_calib(p) if p.exists() else None


def _report_session(res: Any, what: str) -> int:
    rc = 0
    if res.error:
        print(f"ohs: error: {res.error}", file=sys.stderr, flush=True)
        rc = 1
    if res.recording is not None and res.n_frames > 0:
        _say(f"Recorded {res.n_frames} frames ({res.duration_s:.1f} s) to {res.recording}")
        st = res.stats
        if st.get("camera_index_gaps"):
            _say(f"  note: the camera skipped {st['camera_index_gaps']} frames (CPU or USB too slow?)")
        if "live_tracked" in st:
            _say(f"  live tracking: {st['live_tracked']} frames ok, {st['live_lost']} lost, "
                 f"{st['live_laser_points']} laser points")
    elif not res.error:
        _say("Nothing was recorded.")
    if res.interrupted:
        _say(f"{what} interrupted; the recording was closed properly.")
        rc = 130
    return rc


def _find_paths(obj: Any, out: list[str]) -> list[str]:
    if isinstance(obj, dict):
        for v in obj.values():
            _find_paths(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _find_paths(v, out)
    elif isinstance(obj, (str, Path)):
        s = str(obj)
        if Path(s).suffix.lower() in (".ply", ".stl", ".obj", ".csv", ".json", ".npz", ".txt") and len(s) < 1024:
            out.append(s)
    return out


def _run_process(cfg: Any, rec: Path, out: Path, *, mode: str, calib: Any = None, voxel: float | None = None,
                 step: int | None = None, poisson: bool = False, start: int = 0, stop: int | None = None) -> int:
    from ohs.app.pipeline import PipelineParams
    from ohs.app.process import ProcessParams, process_recording
    from ohs.track.markerless import FusionParams
    from ohs.track.markers import MarkerParams

    v = voxel if voxel is not None else cfg.process.voxel_mm  # None: the pipeline's default for the resolution
    pp = PipelineParams(mode=mode, scale=1.0, fusion=None if v is None else FusionParams(voxel_mm=float(v)),
                        markers=MarkerParams(tag_mm=float(cfg.markers.tag_mm)))
    params = ProcessParams(pipeline=pp, frame_step=int(step or cfg.process.frame_step), start=int(start), stop=stop,
                           export=tuple(cfg.process.export), poisson=bool(poisson or cfg.process.poisson))
    vox = "auto" if v is None else f"{float(v):g} mm"
    _say(f"Processing {rec} ({mode}, voxel {vox}, every {params.frame_step}. frame) -> {out}")
    bar = ProgressBar("processing")
    t0 = time.perf_counter()
    try:
        report = process_recording(rec, out, calib=calib, params=params, progress=bar)
    finally:
        bar.close()
    dt = time.perf_counter() - t0
    _say(f"Done in {dt:.0f} s. Output (millimetres) in {out}:")
    report = report if isinstance(report, dict) else {}
    files = report.get("files")
    paths = list(files.values()) if isinstance(files, dict) else list(dict.fromkeys(_find_paths(report, [])))
    if not paths and Path(out).is_dir():
        paths = [str(p) for p in sorted(Path(out).iterdir()) if p.is_file()]
    for p in paths:
        _say(f"  {p}")
    for line in _summary_lines(report):
        _say(f"  {line}")
    return 0


def _summary_lines(report: dict) -> list[str]:
    """Key numbers of a ``process_recording`` report (sections that are missing are skipped)."""
    out: list[str] = []
    fr = report.get("frames")
    if isinstance(fr, dict) and "processed" in fr:
        frac = fr.get("tracked_fraction")
        out.append(f"frames: {fr.get('processed')} processed, {fr.get('tracked')} tracked"
                   + (f" ({100 * frac:.0f}%)" if isinstance(frac, (int, float)) else ""))
    fail = report.get("failures")
    if isinstance(fail, dict) and fail:
        out.append("tracking failures: " + ", ".join(f"{k} {v}" for k, v in fail.items()))
    mesh = report.get("mesh")
    if isinstance(mesh, dict) and "n_faces" in mesh:
        out.append(f"mesh: {mesh.get('n_vertices')} vertices, {mesh.get('n_faces')} faces "
                   f"({mesh.get('method', '?')}, voxel {mesh.get('voxel_mm', '?')} mm)")
        if mesh.get("empty"):
            out.append("warning: the mesh is empty (no frame was tracked?)")
    las = report.get("lasers")
    if isinstance(las, dict) and "n_points_exported" in las:
        out.append(f"laser points: {las['n_points_exported']}")
    gt = report.get("ground_truth")
    if isinstance(gt, dict) and gt.get("n"):
        tr = gt.get("trans_mm", {})
        out.append(f"vs. ground truth: rms {gt.get('rms_trans_mm', float('nan')):.2f} mm, max {tr.get('max', float('nan')):.2f} mm, "
                   f"final {gt.get('final_trans_mm', float('nan')):.2f} mm / {gt.get('final_rot_deg', float('nan')):.2f} deg")
    return out


# --------------------------------------------------------------------------- commands
def cmd_devices(args: argparse.Namespace) -> int:
    cfg = _config(args)
    from ohs.devices.camera import list_cameras
    from ohs.devices.pico import find_pico_port

    backend = args.backend or cfg.camera.backend
    _say(f"Cameras (backend {backend}, indices 0-{args.max_index - 1}):")
    cams = list_cameras(args.max_index, backend=backend)
    for c in cams:
        w, h = int(c.get("width", 0)), int(c.get("height", 0))
        hint = "  <- side-by-side stereo?" if h > 0 and w / h >= 2.5 else ""
        mark = "  (camera.index)" if c.get("index") == cfg.camera.index else ""
        name = f" {c['name']}" if c.get("name") else ""
        _say(f"  {c.get('index')}: {c.get('backend', '?')} {w}x{h} @ {float(c.get('fps', 0)):.0f} fps "
             f"{c.get('fourcc', '')}{name}{hint}{mark}")
    if not cams:
        _say("  none found (plugged in? used by another program?)")
    else:
        _say("  (each camera's default mode; ohs asks for camera.width x camera.height)")
    port = find_pico_port()
    if port:
        _say(f"Pico: {port}")
    else:
        _say("Pico: not found (USB VID 0x2E8A). Flash MicroPython and the firmware: see firmware/README.md")
    if cfg.pico.port:
        _say(f"  pico.port in the config: {cfg.pico.port}")
    try:
        from serial.tools import list_ports

        ports = list(list_ports.comports())
    except Exception:  # pragma: no cover
        ports = []
    if ports:
        _say("Serial ports:")
        for sp in ports:
            ids = f" [{sp.vid:04X}:{sp.pid:04X}]" if sp.vid is not None and sp.pid is not None else ""
            _say(f"  {sp.device}: {sp.description}{ids}")
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    cfg = _config(args)
    from ohs.app.selftest import overall, run_selftest

    checks = run_selftest(cfg, seconds=args.seconds, emitter_seconds=args.emitter_seconds, use_camera=not args.no_camera,
                          use_pico=not args.no_pico, emitters=not args.no_emitters, echo=_say)
    return 1 if overall(checks) == "FAIL" else 0


def cmd_config(args: argparse.Namespace) -> int:
    explicit = getattr(args, "config", None)
    path = Path(explicit).expanduser() if explicit else default_config_path()
    if args.init:
        p = write_template(path, force=args.force)
        _say(f"Wrote {p}")
        _say("Edit it (every setting is optional), then check it with: ohs config --show")
        return 0
    cfg = load_config(path, must_exist=explicit is not None)
    src = cfg.source if cfg.source is not None else f"built-in defaults (no file at {path})"
    _say(f"# settings from {src}")
    print(render_template(cfg, comments=False), end="", flush=True)
    return 0


def cmd_printables(args: argparse.Namespace) -> int:
    cfg = _config(args)
    from ohs.calib.board import render_apriltag_sheet, render_charuco_page

    out = Path(args.out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    paper = args.paper or cfg.board.paper
    spec = cfg.board.spec()
    pdf, png = render_charuco_page(spec, out / "charuco_board", paper=paper, dpi=args.dpi)
    _say(f"ChArUco calibration board ({spec.describe()}, {paper}):")
    _say(f"  {pdf}\n  {png}")
    tag_mm = float(args.tag_mm or cfg.markers.tag_mm)
    tpdf, tpngs = render_apriltag_sheet(out / "apriltags", tag_mm=tag_mm, ids=range(args.tags), paper=paper, dpi=args.dpi)
    _say(f"AprilTag stickers (tag36h11, {tag_mm:g} mm, ids 0-{args.tags - 1}):")
    _say("\n".join(f"  {p}" for p in [tpdf, *tpngs]))
    if not args.no_mask:
        from ohs.projector.mask import MaskSpec, write_mask_files

        _say(f"LED projector mask ({args.mask_dot_um:g} um dots, {args.mask_dpi} dpi): generating the sheet, "
             "this can take ~20 s...")
        info = write_mask_files(out / "projector_mask", spec=MaskSpec(dot_um=float(args.mask_dot_um)), dpi=args.mask_dpi,
                                paper=paper)
        _say("\n".join(f"  {info[k]}" for k in ("sheet_pdf", "sheet_png", "gerber", "svg") if k in info))
        fov = info.get("fov_deg", (float("nan"), float("nan")))
        film = info.get("film_dot_mm_at_250")
        _say(f"  {info.get('n_dots', '?')} dots, {float(info.get('fill', 0.0)):.0%} fill, {fov[0]:.0f} x {fov[1]:.0f} deg field, "
             f"~{float(info.get('dot_mm_at_250', 0.0)):.1f} mm dots at 250 mm"
             + (f" (film mask: ~{float(film):.1f} mm)" if film is not None else ""))
        sheet = Path(info.get("sheet_pdf", "projector_mask_sheet.pdf")).name
        gerber = Path(info.get("gerber", "projector_mask.gbr")).name
        _say(f"LED projector mask: print {sheet} at 100% on laser transparency film ({args.mask_dpi} dpi), "
             f"or send {gerber} to a photoplot service")
    _say("")
    _say("Print at 100% / actual size (no 'fit to page'), then check the 100 mm scale bar with a ruler or calipers.")
    _say("Glue the ChArUco page flat to glass (e.g. a picture frame). Print the AprilTags on matte removable label paper.")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    cfg = _config(args)
    from ohs.app import live

    replay = _recording(args.replay, "--replay") if args.replay else None
    calib = _optional_calib(args, cfg, replay)
    res = live.run_preview(cfg, calib=calib, replay=replay, use_pico=not args.no_pico, gui=not args.no_gui, raw=args.raw,
                           depth=args.depth, max_frames=args.max_frames, echo=_say)
    if res.error:
        raise CliError(res.error)
    return 130 if res.interrupted else 0


def cmd_record(args: argparse.Namespace) -> int:
    cfg = _config(args)
    from ohs.app import live

    cfg.emitters.levels(args.profile)  # unknown profile -> error before opening anything
    replay = _recording(args.replay, "--replay") if args.replay else None
    calib = _optional_calib(args, cfg, replay)
    out = Path(args.out).expanduser()
    live.check_output_dir(out)
    res = live.run_record(cfg, out, profile=args.profile, calib=calib, replay=replay, use_pico=not args.no_pico,
                          gui=not args.no_gui, max_frames=args.max_frames, max_seconds=args.seconds, start_now=args.start_now,
                          echo=_say)
    return _report_session(res, "recording")


def _scan_calibration(args: argparse.Namespace, cfg: Any, replay: Path | None):
    if getattr(args, "calib", None):
        p = _calib_path(args, cfg)
        return _load_calib(p), str(p)
    if replay is not None and (replay / "calibration.json").exists():
        p = replay / "calibration.json"
        return _load_calib(p), str(p)
    p = cfg.paths.calibration_path
    if not p.exists():
        raise CliError(f"no calibration at {p}: run `ohs calibrate stereo` (then lasers and imu) first, "
                       "or try `ohs sim demo` and `ohs scan --replay demo`")
    return _load_calib(p), str(p)


def cmd_scan(args: argparse.Namespace) -> int:
    cfg = _config(args)
    from ohs.app import live

    replay = _recording(args.replay, "--replay") if args.replay else None
    calib, src = _scan_calibration(args, cfg, replay)
    out = Path(args.out).expanduser() if args.out else live.new_recording_dir(cfg.paths.scans_path, "scan")
    live.check_output_dir(out)
    _say(f"Scan ({args.mode}) -> {out}   [calibration: {src}]")
    if args.mode == "markers" and not replay:
        _say(f"Marker mode: live dot projector at {cfg.emitters.markers_dot_level:g}% (emitters.markers_dot_level).")
    res = live.run_scan(cfg, calib, out, mode=args.mode, replay=replay, use_pico=not args.no_pico, gui=not args.no_gui,
                        max_frames=args.max_frames, max_seconds=args.seconds, start_now=args.start_now, calib_source=src,
                        echo=_say)
    rc = _report_session(res, "scan")
    if res.recording is not None and res.n_frames > 0:
        if args.process and rc == 0:
            rc = _run_process(cfg, res.recording, res.recording / "result", mode=args.mode)
        else:
            _say(f'Final model: ohs process "{res.recording}"')
    return rc


def cmd_process(args: argparse.Namespace) -> int:
    cfg = _config(args)
    from ohs.io.recording import RecordingReader

    rec = _recording(args.recording, "recording")
    meta = RecordingReader(rec).meta
    mode = args.mode or (meta.get("mode") if meta.get("mode") in MODES else "markerless")
    calib = None  # None: process_recording uses the recording's own calibration.json
    if getattr(args, "calib", None):
        calib = _load_calib(_calib_path(args, cfg))
    elif not (rec / "calibration.json").exists():
        p = cfg.paths.calibration_path
        if not p.exists():
            raise CliError(f"{rec} has no calibration.json and there is no calibration at {p}; pass --calib")
        calib = _load_calib(p)
        _say(f"The recording has no calibration.json: using {p}")
    out = Path(args.out).expanduser() if args.out else rec / "result"
    return _run_process(cfg, rec, out, mode=mode, calib=calib, voxel=args.voxel, step=args.step, poisson=args.poisson,
                        start=args.start, stop=args.stop)


def cmd_calibrate(args: argparse.Namespace) -> int:
    cfg = _config(args)
    from ohs.app import capture, live

    kind = args.kind
    spec = cfg.board.spec()
    calib_path = _calib_path(args, cfg)
    existing = _load_calib(calib_path) if calib_path.exists() else None
    if kind != "stereo" and existing is None:
        raise CliError(f"no calibration at {calib_path}: run `ohs calibrate stereo` first")
    if args.from_rec:
        rec = _recording(args.from_rec, "--from")
        _say(f"Using the capture recording {rec}")
    else:
        out = Path(args.out).expanduser() if args.out else live.new_recording_dir(cfg.paths.scans_path / "calibration", kind)
        live.check_output_dir(out)
        _say(f"Calibration capture ({kind}) -> {out}")
        _say(f"Board: {spec.describe()}")
        res = capture.run_capture(cfg, kind, out, spec=spec, calib=existing, use_pico=not args.no_pico, gui=not args.no_gui,
                                  views=args.views, seconds=args.seconds, echo=_say)
        if res.error:
            raise CliError(res.error)
        if res.interrupted or res.aborted:
            kept = f" (capture recording kept at {res.recording})" if res.recording is not None and res.n_frames else ""
            _say(f"Capture {'interrupted' if res.interrupted else 'aborted'}: nothing calibrated{kept}.")
            return 130 if res.interrupted else 1
        if res.recording is None or res.n_frames == 0:
            raise CliError("nothing was captured")
        rec = res.recording
        need = capture.MIN_VIEWS.get(kind)
        if need is not None and res.n_frames < need:
            raise CliError(f"only {res.n_frames} views captured, at least {need} are needed ({capture.GUIDANCE[kind]}); "
                           f"the recording is kept at {rec}")
        _say(f"Captured {res.n_frames} {'frames' if kind == 'imu' else 'views'}.")
    _say(f"Running the {kind} calibration...")
    bar = ProgressBar(f"calibrating {kind}")
    try:
        value, report = capture.run_flow(kind, rec, spec, existing, step=args.step, paper=cfg.board.paper, progress=bar)
    finally:
        bar.close()
    _say(f"{kind} report:")
    _say(capture.format_report(report if isinstance(report, dict) else {"report": report}))
    rp = capture.write_report(report if isinstance(report, dict) else {"report": report}, rec / f"calib_report_{kind}.json")
    if rp is not None:
        _say(f"Full report: {rp}")
    if kind == "check":
        ok = None
        if isinstance(report, dict):
            ok = next((report[k] for k in ("ok", "passed", "quality_ok") if isinstance(report.get(k), bool)), None)
        if ok is False:
            _warn("the calibration check failed: re-run `ohs calibrate stereo` and/or `ohs calibrate lasers`")
            return 1
        return 0
    new, notes = capture.merge_calibration(existing, kind, value, keep=args.keep)
    for n in notes:
        (_warn if "dropped" in n or "not fitted" in n else _say)(n)
    if args.no_save:
        _say("--no-save: the calibration was not written.")
        return 0
    backup = capture.save_calibration(new, calib_path)
    _say(f"Saved {calib_path}" + (f" (previous version: {backup})" if backup else ""))
    nxt = {"stereo": "next: ohs calibrate lasers, then ohs calibrate imu", "lasers": "next: ohs calibrate imu (if not done)",
           "imu": "ready to scan: ohs scan"}
    _say(nxt[kind])
    return 0


def cmd_sim(args: argparse.Namespace) -> int:
    from ohs.sim.datasets import make_scan_recording

    w, h = args.size
    out = Path(args.out_dir).expanduser()
    if out.exists() and any(out.iterdir()) and not args.overwrite:
        raise CliError(f"{out} is not empty (choose another folder or pass --overwrite)")
    est = args.frames * w * h / (1280 * 800) * 1.0
    _say(f"Simulating {args.frames} frames of '{args.scene}' at {w}x{h}, {args.fps:g} fps (about {max(1.0, est):.0f} s)...")
    t0 = time.perf_counter()
    path = make_scan_recording(out, scene=args.scene, n_frames=args.frames, fps=args.fps, image_size=(w, h), seed=args.seed,
                               realistic=args.realistic, depth_every=args.gt_depth_every, overwrite=args.overwrite)
    _say(f"Wrote {path} in {time.perf_counter() - t0:.0f} s: frames, IMU, trigger events, calibration.json, ground truth.")
    _say("Try:")
    _say(f'  ohs process "{path}"           final mesh (STL/PLY in mm)')
    _say(f'  ohs scan --replay "{path}"     live view on the replayed scan')
    _say(f'  ohs preview --replay "{path}"  raw / rectified view, laser overlay, depth')
    return 0


# --------------------------------------------------------------------------- main
def _explain(e: BaseException) -> str:
    msg = str(e) or type(e).__name__
    if isinstance(e, ImportError):
        name = getattr(e, "name", "") or ""
        if name.startswith("open3d") or "open3d" in msg:
            return f"{msg}: this needs Open3D (pip install -e .[fusion])"
        if name.startswith("ohs.app."):
            return f"{msg}: the processing modules of this installation are missing or broken"
    if isinstance(e, ConnectionError) and "Pico" in msg:
        return f"{msg} (or use --no-pico)"
    return msg


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    state = _install_logging(int(getattr(args, "verbose", 0) or 0))
    try:
        func = getattr(args, "func", None)
        if func is None:
            parser.print_help()
            return 2
        return int(func(args) or 0)
    except KeyboardInterrupt:
        print("\nohs: interrupted", file=sys.stderr, flush=True)
        return 130
    except CliError as e:
        print(f"ohs: error: {e}", file=sys.stderr, flush=True)
        return 1
    except (ConfigError, OSError, ConnectionError, TimeoutError, RuntimeError, ValueError, KeyError, ImportError) as e:
        if getattr(args, "verbose", 0):
            log.exception("command failed")
        print(f"ohs: error: {_explain(e)}", file=sys.stderr, flush=True)
        return 1
    finally:
        _remove_logging(state)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
