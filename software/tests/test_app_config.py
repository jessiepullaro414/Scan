"""Configuration: defaults, TOML merge, unknown keys, validation, template round trip, ``ohs config``."""
from __future__ import annotations

import logging
import tomllib
from pathlib import Path

import pytest

from ohs.app import cli
from ohs.app.config import (
    DEFAULT_PROFILES,
    Config,
    ConfigError,
    default_config_path,
    load_config,
    merge_into,
    render_template,
    write_template,
)
from ohs.calib.board import CharucoSpec
from ohs.devices.camera import CameraParams
from ohs.devices.pico import PicoParams


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OHS_CONFIG", raising=False)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults():
    cfg = Config()
    cfg.validate()
    assert cfg.camera == CameraParams() and cfg.pico == PicoParams()
    assert cfg.board.spec() == CharucoSpec() and cfg.markers.tag_mm == 20.0
    assert (cfg.live.scale, cfg.live.depth_min_mm, cfg.live.depth_max_mm, cfg.live.voxel_mm) == (0.5, 150.0, 450.0, None)
    assert cfg.paths.calibration_path == Path("~/.ohs/calibration.json").expanduser()
    assert cfg.paths.scans_path == Path("~/OpenHandScan/scans").expanduser()
    assert cfg.process.export == ["ply", "stl"] and cfg.process.voxel_mm is None
    em = cfg.emitters
    assert em.markers_dot_level == 25.0 and set(DEFAULT_PROFILES) <= set(em.profiles)
    assert em.levels("stereo") == (0, 0, 0) and em.levels("lasers") == (100, 100, 0) and em.levels("check") == (100, 100, 0)
    assert em.levels("imu") == (0, 0, 0)
    assert em.scan_levels("markerless") == (100, 100, 100)
    assert em.scan_levels("markers") == (100, 100, 25) and em.scan_levels("markers", live=False) == (100, 100, 100)
    with pytest.raises(ConfigError, match="unknown emitter profile"):
        em.levels("nope")


def test_missing_file_gives_defaults_unless_required(tmp_path, monkeypatch):
    assert load_config(tmp_path / "none.toml") == Config()
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "none.toml", must_exist=True)
    monkeypatch.setenv("OHS_CONFIG", str(tmp_path / "env.toml"))
    assert default_config_path() == tmp_path / "env.toml"
    monkeypatch.delenv("OHS_CONFIG")
    assert default_config_path() == Path("~/.ohs/config.toml").expanduser()


def test_merge_over_defaults(tmp_path):
    p = _write(tmp_path / "c.toml", """
[paths]
calibration = "D:/scanner/calibration.json"
[camera]
index = 2
backend = "dshow"
exposure = -6
swap_lr = true
[pico]
port = "COM7"
[emitters]
markers_dot_level = 30
[emitters.profiles]
scan = [80, 80, 60]
dim = [10, 10, 10]
[live]
voxel_mm = 3
[process]
export = ["ply", "stl", "obj"]
[board]
paper = "a4"
square_mm = 25
marker_mm = 18
""")
    cfg = load_config(p)
    assert cfg.source == p and cfg.warnings == []
    assert (cfg.camera.index, cfg.camera.backend, cfg.camera.exposure, cfg.camera.swap_lr) == (2, "dshow", -6.0, True)
    assert isinstance(cfg.camera.exposure, float) and cfg.camera.width == 2560  # untouched keys keep the defaults
    assert cfg.pico.port == "COM7" and cfg.pico.imu_rate_hz == 416
    assert cfg.emitters.levels("scan") == (80, 80, 60) and cfg.emitters.levels("dim") == (10, 10, 10)
    assert cfg.emitters.levels("lasers") == (100, 100, 0)  # built-in profiles stay
    assert cfg.emitters.scan_levels("markers") == (80, 80, 30)
    assert cfg.live.voxel_mm == 3.0 and isinstance(cfg.live.voxel_mm, float)
    assert cfg.paths.calibration_path == Path("D:/scanner/calibration.json")
    assert cfg.board.spec() == CharucoSpec(square_mm=25.0, marker_mm=18.0) and cfg.board.paper == "a4"
    assert cfg.process.export == ["ply", "stl", "obj"]


def test_optional_values_accept_auto(tmp_path):
    p = _write(tmp_path / "c.toml", '[camera]\nexposure = "auto"\ngain = 3\n[pico]\nport = "auto"\n[live]\nvoxel_mm = "auto"\n')
    cfg = load_config(p)
    assert cfg.camera.exposure is None and cfg.camera.gain == 3.0 and cfg.pico.port is None and cfg.live.voxel_mm is None


def test_unknown_keys_warn_and_are_ignored(tmp_path, caplog):
    p = _write(tmp_path / "c.toml", "[camera]\nexposre = -7\nindex = 1\n[scanner]\nspeed = 3\n[live]\ndepth_mn_mm = 100\n")
    with caplog.at_level(logging.WARNING, logger="ohs.app.config"):
        cfg = load_config(p)
    assert cfg.camera.index == 1 and cfg.camera.exposure is None
    text = "\n".join(cfg.warnings)
    assert "camera.exposre" in text and "did you mean 'exposure'" in text
    assert "'scanner'" in text and "live.depth_mn_mm" in text and "depth_min_mm" in text
    assert "unknown config key 'camera.exposre'" in caplog.text


@pytest.mark.parametrize("text,match", [
    ('[camera]\nindex = "zero"\n', "camera.index: expected an integer"),
    ("[camera]\nswap_lr = 1\n", "camera.swap_lr: expected true or false"),
    ("[emitters.profiles]\nscan = [1, 2]\n", r"emitters.profiles.scan: expected 3 values"),
    ("[emitters.profiles]\nscan = [1, 2, 300]\n", "0..100"),
    ("[live]\nscale = 2.0\n", r"live.scale must be in \(0, 1\]"),
    ("[live]\ndepth_min_mm = 500\n", "depth_min_mm"),
    ("[pico]\nimu_rate_hz = 100\n", "imu_rate_hz"),
    ("[camera]\nbackend = \"gstreamer\"\n", "camera.backend"),
    ("[board]\nmarker_mm = 25\n", "board:"),
    ("camera = 3\n", r"\[camera\] must be a table"),
    ("[camera\nindex = 1\n", "invalid TOML"),
])
def test_bad_values_raise_with_the_key(tmp_path, text, match):
    p = _write(tmp_path / "c.toml", text)
    with pytest.raises(ConfigError, match=match):
        load_config(p)


def test_template_round_trip(tmp_path):
    p = write_template(tmp_path / "sub" / "config.toml")
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# OpenHandScan configuration") and "# exposure = -7" in text and "[emitters.profiles]" in text
    assert "markers_dot_level = 25.0" in text and "Class 3R" in text
    assert load_config(p) == Config()  # the commented template is exactly the defaults
    with pytest.raises(FileExistsError):
        write_template(p)
    write_template(p, force=True)
    # non-default values (including optional ones) survive render -> parse -> merge
    cfg = Config()
    cfg.camera.exposure = -9.0
    cfg.camera.second_index = 1
    cfg.pico.port = "COM 9"
    cfg.emitters.profiles["my profile"] = (1.0, 2.0, 3.5)
    cfg.live.voxel_mm = 2.5
    cfg.paths.scans_dir = "C:\\scans\\with \"quotes\""
    for comments in (True, False):
        back = Config()
        assert merge_into(back, tomllib.loads(render_template(cfg, comments=comments))) == []
        assert back == cfg


def test_frames_by_motion_settings(tmp_path):
    cfg = Config()
    assert (cfg.process.frame_motion_deg, cfg.process.frame_max_gap) == (0.0, 4)  # off by default
    cfg = load_config(_write(tmp_path / "c.toml", "[process]\nframe_motion_deg = 1\nframe_max_gap = 6\n"))
    assert (cfg.process.frame_motion_deg, cfg.process.frame_max_gap) == (1.0, 6)
    assert isinstance(cfg.process.frame_motion_deg, float)
    for text, match in (("frame_motion_deg = -0.5", "process.frame_motion_deg"), ("frame_max_gap = 0", "process.frame_max_gap")):
        with pytest.raises(ConfigError, match=match):
            load_config(_write(tmp_path / "bad.toml", f"[process]\n{text}\n"))
    text = render_template(Config())
    assert "\nframe_motion_deg = 0.0\n" in text and "\nframe_max_gap = 4\n" in text and "gyro has turned" in text


def test_laser_channels_and_brightness(tmp_path):
    """[lasers] own_channels and brightness: each laser's Pico channel, its share of its family's
    level, validation, and a round trip through the file (an inline table)."""
    from ohs.app.config import LasersConfig, set_config_values

    two = LasersConfig()
    assert two.own_channels and two.brightness == {} and not two.separate()
    assert two.channel_scales() == (1.0, 1.0, 1.0, 1.0)  # the second channels follow: the old 3-level EMIT
    assert [two.channel_of(n) for n in two.lines] == ["v", "h"]
    four = LasersConfig(lines=["vertical", "horizontal", "vertical_2", "horizontal_2"],
                        brightness={"vertical_2": 80.0, "horizontal": 50.0})
    assert four.separate() and [four.channel_of(n) for n in four.lines] == ["v", "h", "v2", "h2"]
    assert four.channel_of("vertical_3") is None and four.channel_of("nope") is None
    assert four.channel_scales() == (1.0, 0.5, 0.8, 1.0)  # horizontal_2 left out: 100 %
    shared = LasersConfig(lines=four.lines, own_channels=False, brightness=dict(four.brightness))
    assert not shared.separate() and shared.channel_scales() == (1.0, 0.5, 1.0, 0.5)  # a pair follows its first laser
    assert four.problems() == []
    assert any("not in lasers.lines" in p for p in LasersConfig(brightness={"vertical_2": 50}).problems())
    assert any("0..100" in p for p in LasersConfig(brightness={"vertical": 120}).problems())
    assert any("0..100" in p for p in LasersConfig(brightness={"vertical": "bright"}).problems())
    path = _write(tmp_path / "c.toml", '[lasers]\nlines = ["vertical", "horizontal", "vertical_2", "horizontal_2"]\n')
    cfg = load_config(path)
    set_config_values({"lasers": {"brightness": {"vertical_2": 85.0}, "own_channels": False}}, path, cfg=cfg)
    assert "brightness = { vertical_2 = 85.0 }" in path.read_text(encoding="utf-8")
    back = load_config(path)
    assert back.lasers.brightness == {"vertical_2": 85.0} and back.lasers.own_channels is False
    with pytest.raises(ConfigError, match="lasers.brightness"):
        set_config_values({"lasers": {"brightness": {"horizontal_3": 10.0}}}, path, cfg=back)
    assert load_config(path).lasers.brightness == {"vertical_2": 85.0}  # a refused value is not written
    clean = Config()
    assert merge_into(clean, tomllib.loads(render_template(back))) == [] and clean.lasers == back.lasers
    # a file made from the template (``ohs config --init``) takes a new brightness without a second table
    made = tmp_path / "made.toml"
    write_template(made)
    assert "\nbrightness = {}\n" in made.read_text(encoding="utf-8") and "[lasers.brightness]" not in made.read_text(encoding="utf-8")
    set_config_values({"lasers": {"lines": four.lines, "brightness": {"vertical_2": 60.0}}}, made, cfg=load_config(made))
    got = load_config(made)
    assert got.lasers.brightness == {"vertical_2": 60.0}
    # a brightness in a file replaces the whole map (a laser left out is back at 100 %)
    set_config_values({"lasers": {"brightness": {"horizontal": 70.0}}}, made, cfg=got)
    assert load_config(made).lasers.brightness == {"horizontal": 70.0}


def test_laser_set_setting(tmp_path):
    """[lasers] lines: the build's line lasers, two by default; names start with a family, are
    unique, and each family has at least one."""
    from ohs.vision.laser import LINE_NAMES

    assert Config().lasers.lines == ["vertical", "horizontal"]
    text = render_template(Config())
    assert '\n[lasers]\n' in text and '\nlines = ["vertical", "horizontal"]\n' in text
    assert "vertical_2" in text and "largest" in text  # the template explains the order within a family
    four = ["vertical", "horizontal", "vertical_2", "horizontal_2"]
    cfg = load_config(_write(tmp_path / "c.toml", '[lasers]\nlines = ["vertical", "horizontal", "vertical_2", "horizontal_2"]\n'))
    assert cfg.lasers.lines == four and cfg.warnings == []
    back = Config()
    assert merge_into(back, tomllib.loads(render_template(cfg))) == [] and back == cfg
    for bad, match in ((["vertical"], "horizontal"), (["vertical", "horizontal", "vertical"], "twice"),
                       (["vertical", "horizontal", "diagonal"], "diagonal"), ([], "lasers.lines"),
                       (["vertical", "horizontal", "verticalx"], "verticalx")):
        with pytest.raises(ConfigError, match=match):
            load_config(_write(tmp_path / "bad.toml", f"[lasers]\nlines = {list(bad)!r}\n".replace("'", '"')))
    with pytest.raises(ConfigError, match="lasers.lines"):
        load_config(_write(tmp_path / "bad.toml", '[lasers]\nlines = "vertical"\n'))
    from ohs.app import config as config_mod

    assert config_mod.LASER_FAMILIES == LINE_NAMES  # the config checks names without importing the vision code


def test_frames_by_motion_reach_the_full_quality_only():
    from ohs.server.studio import process_params

    cfg = Config()
    cfg.process.frame_motion_deg, cfg.process.frame_max_gap, cfg.process.frame_step = 0.5, 6, 2
    full = process_params(cfg, "markerless", "full", 1280)
    assert (full.frame_motion_deg, full.frame_max_gap, full.frame_step) == (0.5, 6, 2)
    preview = process_params(cfg, "markerless", "preview", 1280)
    assert (preview.frame_motion_deg, preview.frame_step) == (0.0, 1)  # the preview stays every frame
    default = process_params(Config(), "markerless", "full", 1280)
    assert (default.frame_motion_deg, default.frame_max_gap) == (0.0, 4)


def test_cli_config_init_and_show(tmp_path, monkeypatch, capsys):
    p = tmp_path / "cfg" / "config.toml"
    assert cli.main(["--config", str(p), "config", "--init"]) == 0
    assert p.exists() and "Wrote" in capsys.readouterr().out
    assert cli.main(["config", "--init", "--config", str(p)]) == 1  # global option after the command, too
    assert "already exists" in capsys.readouterr().err
    assert cli.main(["--config", str(p), "config", "--init", "--force"]) == 0
    capsys.readouterr()
    p.write_text(p.read_text(encoding="utf-8").replace("index = 0", "index = 3"), encoding="utf-8")
    assert cli.main(["--config", str(p), "config", "--show"]) == 0
    shown = capsys.readouterr().out
    assert shown.startswith(f"# settings from {p}")
    back = Config()
    merge_into(back, tomllib.loads(shown))
    assert back.camera.index == 3 and back.camera.exposure is None
    # $OHS_CONFIG is the default location; a missing explicit --config is an error
    monkeypatch.setenv("OHS_CONFIG", str(tmp_path / "env.toml"))
    assert cli.main(["config", "--init"]) == 0 and (tmp_path / "env.toml").exists()
    assert cli.main(["--config", str(tmp_path / "missing.toml"), "config", "--show"]) == 1
    assert "not found" in capsys.readouterr().err


def test_cli_rejects_a_broken_config(tmp_path, capsys):
    p = _write(tmp_path / "c.toml", "[live]\nscale = 7\n")
    assert cli.main(["--config", str(p), "devices", "--max-index", "1"]) == 1
    assert "live.scale" in capsys.readouterr().err
