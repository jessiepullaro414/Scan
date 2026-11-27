"""ENGINE_PLAN R2-8: the live recorder's budget comes from the settings, ``[live] max_waiting_mb`` and
``[live] max_waiting_seconds``.

The defaults give exactly today's budget (``RECORD_MAX_BYTES`` = 1 GiB = 1024 MiB, ``RECORD_MAX_SECONDS`` =
20 s); a changed setting reaches the :class:`~ohs.app.live.Recorder` that ``ohs record`` / ``ohs scan``
(``LiveSession.start_recording``), the app's scan and the app's motion-sensor calibration build; a bad value
in the settings file is refused when it is read (as every other range check), and a bad value set in code
is clamped, so no recorder refuses its first frame or never stops."""
from __future__ import annotations

import math
import time

import pytest

from ohs.app import live
from ohs.app.config import Config, ConfigError, load_config, render_template, set_config_values

MIB = 1 << 20


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- the defaults
def test_the_defaults_are_todays_budget():
    lv = Config().live
    assert lv.max_waiting_mb == 1024 and lv.max_waiting_seconds == 20.0
    assert lv.max_waiting_mb * MIB == live.RECORD_MAX_BYTES and lv.max_waiting_seconds == live.RECORD_MAX_SECONDS
    b = live.recorder_budget(Config())
    rec_bytes = live.RECORD_MAX_BYTES if b["max_bytes"] is None else b["max_bytes"]
    rec_seconds = live.RECORD_MAX_SECONDS if b["max_seconds"] is None else b["max_seconds"]
    assert (rec_bytes, rec_seconds) == (1 << 30, 20.0)


def test_the_template_lists_both_keys_and_loads_as_the_defaults(tmp_path):
    text = render_template()
    assert "max_waiting_mb = 1024" in text and "max_waiting_seconds = 20.0" in text
    assert load_config(_write(tmp_path / "c.toml", text)) == Config()


def test_a_file_sets_them(tmp_path):
    cfg = load_config(_write(tmp_path / "c.toml", "[live]\nmax_waiting_mb = 256\nmax_waiting_seconds = 5\n"))
    assert cfg.live.max_waiting_mb == 256 and cfg.live.max_waiting_seconds == 5.0
    assert live.recorder_budget(cfg) == {"max_bytes": 256 * MIB, "max_seconds": 5.0}


# --------------------------------------------------------------------------- bad values
@pytest.mark.parametrize("text", [
    "max_waiting_mb = 0", "max_waiting_mb = -5", "max_waiting_mb = 10", "max_waiting_mb = nan", "max_waiting_mb = inf",
    "max_waiting_seconds = 0", "max_waiting_seconds = -1", "max_waiting_seconds = 0.5", "max_waiting_seconds = nan",
    "max_waiting_seconds = inf",
])
def test_a_bad_value_in_the_file_is_refused_naming_the_key(tmp_path, text):
    key = text.split(" ")[0]
    with pytest.raises(ConfigError, match=f"live.{key}"):
        load_config(_write(tmp_path / "c.toml", f"[live]\n{text}\n"))


@pytest.mark.parametrize("text", ['max_waiting_mb = "lots"', "max_waiting_seconds = true"])
def test_a_value_that_is_not_a_number_is_refused(tmp_path, text):
    with pytest.raises(ConfigError, match="live.max_waiting_"):
        load_config(_write(tmp_path / "c.toml", f"[live]\n{text}\n"))


def test_the_settings_route_cannot_save_a_bad_value(tmp_path):
    path = tmp_path / "c.toml"
    with pytest.raises(ConfigError):
        set_config_values({"live": {"max_waiting_mb": 0}}, path)
    with pytest.raises(ConfigError):
        set_config_values({"live": {"max_waiting_seconds": -3}}, path)


@pytest.mark.parametrize("mb, seconds", [(0, 0), (-1, -1), (1, 0.01), (float("nan"), float("nan")), ("x", "y"),
                                         (float("inf"), float("inf")), (None, None)])
def test_a_bad_value_set_in_code_is_clamped(mb, seconds):
    cfg = Config()
    cfg.live.max_waiting_mb, cfg.live.max_waiting_seconds = mb, seconds
    b = live.recorder_budget(cfg)
    max_bytes = live.RECORD_MAX_BYTES if b["max_bytes"] is None else b["max_bytes"]
    max_seconds = live.RECORD_MAX_SECONDS if b["max_seconds"] is None else b["max_seconds"]
    assert 64 * MIB <= max_bytes < math.inf and 1.0 <= max_seconds < math.inf


def test_a_recorder_with_the_smallest_budget_takes_its_first_frames(tmp_path):
    """The smallest budget the settings allow holds well over one full 1280 x 800 grey pair (2 MB)."""
    import numpy as np

    from ohs.types import StereoFrame

    cfg = Config()
    cfg.live.max_waiting_mb, cfg.live.max_waiting_seconds = 64, 1.0
    rec = live.Recorder(tmp_path / "r", **live.recorder_budget(cfg))
    try:
        img = np.zeros((800, 1280), np.uint8)
        for i in range(3):
            assert rec.put_frame(StereoFrame(index=i, t_host=0.01 * i, left=img, right=img))
        assert rec.stopped is None
    finally:
        rec.close()


# --------------------------------------------------------------------------- where the recorders are built
class _Spy:
    """Stands in for :class:`ohs.app.live.Recorder`: keeps the keyword arguments, then makes the real one."""

    def __init__(self, monkeypatch):
        self.calls: list[dict] = []
        real = live.Recorder

        def make(path, **kw):
            self.calls.append(kw)
            return real(path, **kw)

        monkeypatch.setattr(live, "Recorder", make)


def _small_cfg() -> Config:
    cfg = Config()
    cfg.live.max_waiting_mb, cfg.live.max_waiting_seconds = 100, 3.5
    return cfg


def test_the_cli_sessions_pass_the_setting(tmp_path, monkeypatch):
    """``ohs record`` / ``ohs scan`` and the CLI calibration captures all open their recording here."""
    spy = _Spy(monkeypatch)
    s = live.LiveSession(_small_cfg(), None, gui=False, echo=lambda *a, **k: None)
    rec = s.start_recording(tmp_path / "r", meta={"purpose": "test"})
    try:
        assert (rec.max_bytes, rec.max_seconds) == (100 * MIB, 3.5)
        assert spy.calls and spy.calls[-1]["max_bytes"] == 100 * MIB
    finally:
        rec.close()


def test_the_cli_sessions_keep_todays_budget_by_default(tmp_path):
    s = live.LiveSession(Config(), None, gui=False, echo=lambda *a, **k: None)
    rec = s.start_recording(tmp_path / "r", meta={"purpose": "test"})
    try:
        assert (rec.max_bytes, rec.max_seconds) == (1 << 30, 20.0)
    finally:
        rec.close()


def test_the_app_calibration_recording_passes_the_setting(tmp_path, monkeypatch):
    """The motion-sensor step's recording (``CalibrationSession.imu_start``) gets the configured budget."""
    from ohs.server import calibration

    seen: list[dict] = []

    def refuse(path, **kw):  # keep what it was given, then fail: imu_start answers 500 and builds nothing more
        seen.append(kw)
        raise OSError("not in this test")

    monkeypatch.setattr(live, "Recorder", refuse)
    s = calibration.CalibrationSession(kind="imu", target="", spec=None, calib=None, paper="letter", hub=None,
                                       rec_base=tmp_path, source_name="simulator",
                                       budget=live.recorder_budget(_small_cfg()))
    with pytest.raises(Exception):
        s.imu_start()
    assert seen and seen[-1]["max_bytes"] == 100 * MIB and seen[-1]["max_seconds"] == 3.5


def test_the_app_scan_passes_the_setting(tmp_path):
    """The real server on the simulator: a scan's recorder has the configured budget."""
    pytest.importorskip("fastapi")
    pytest.importorskip("open3d")
    requests = pytest.importorskip("requests")
    from ohs.server.app import create_app
    from ohs.server.demo import DemoParams, demo_path, generate_demo
    from ohs.server.testing import ServerThread

    generate_demo(demo_path(tmp_path / "demo"), DemoParams(n_frames=12, image_size=(640, 400)))
    cfg = _small_cfg()
    cfg.paths.calibration = str(tmp_path / "no-calibration.json")
    app = create_app(cfg, projects_dir=tmp_path / "projects", demo_dir=tmp_path / "demo", static_dir=tmp_path / "static",
                     data_dir=tmp_path / "data", config_path=tmp_path / "config.toml")
    with ServerThread(app) as srv:
        assert requests.post(srv.url + "/api/device/connect", json={"source": "sim"}, timeout=60).status_code == 200
        pid = requests.post(srv.url + "/api/projects", json={"name": "Budget"}, timeout=60).json()["id"]
        r = requests.post(srv.url + "/api/scan/start", json={"project_id": pid, "mode": "markerless"}, timeout=60)
        assert r.status_code == 200, r.text
        try:
            rec = None
            for _ in range(100):
                rec = getattr(app.state.studio.session, "_recorder", None)
                if rec is not None:
                    break
                time.sleep(0.05)
            assert rec is not None
            assert (rec.max_bytes, rec.max_seconds) == (100 * MIB, 3.5)
        finally:
            requests.post(srv.url + "/api/scan/stop", timeout=60)
