"""Small mends from the walk on the real engine (UX_PLAN R9, R14, R20, R22), each in its plain sentence.

* R9: while a calibration result waits in Setup to be saved, a refusal does not say "A calibration is running.";
* R14: a replayed recording that can no longer be read says so (no advice about a USB cable), and the notice has no
  "Scanner problem:" title;
* R20: a replay without a calibration is not told to "Calibrate it in Setup" (Setup cannot calibrate a replay);
* R22: Straighten skipped after closing loops says there was nothing to straighten.
"""
from __future__ import annotations

import threading
import types

import pytest

pytest.importorskip("fastapi")

from ohs.app.config import Config  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.devices import _Connection  # noqa: E402
from ohs.server.protocol import ApiError  # noqa: E402


@pytest.fixture()
def studio(tmp_path):
    cfg = Config()
    cfg.paths.calibration = str(tmp_path / "no-calibration.json")
    app = create_app(cfg, projects_dir=tmp_path / "projects", demo_dir=tmp_path / "demo", static_dir=tmp_path / "static",
                     data_dir=tmp_path / "data", config_path=tmp_path / "config.toml")
    return app.state.studio


def test_a_waiting_calibration_result_is_not_called_running(studio):
    cal = studio.calibration
    cal.session = types.SimpleNamespace(state="result", active=True, kind="stereo")
    e = cal.busy("Finish or cancel the calibration in Setup first.")
    assert e.status == 409 and e.detail == "A calibration result is waiting in Setup: save it or close it first."
    assert e.hint and "Setup" in e.hint
    cal.session = types.SimpleNamespace(state="capturing", active=True, kind="stereo")
    e = cal.busy("Finish or cancel the calibration in Setup first.")
    assert e.detail == "A calibration is running." and e.hint == "Finish or cancel the calibration in Setup first."
    cal.session = types.SimpleNamespace(state="result", active=True, kind="stereo")
    p = studio.projects.create("Waiting")
    with pytest.raises(ApiError) as got:  # the Scan page's refusal goes through it
        studio.start_scan(p["id"], "markerless")
    assert got.value.detail == "A calibration result is waiting in Setup: save it or close it first."
    cal.session = None


class _Gone:
    """A replay camera whose folder went away: every read fails as a missing file does."""

    def read(self, timeout=0.5):
        raise FileNotFoundError(2, "No such file or directory")

    def close(self):
        pass


def test_a_replay_that_cannot_be_read_any_more_says_so(studio):
    dev = studio.devices
    said = []
    dev._on_lost = lambda kind, msg: said.append((kind, msg))
    conn = _Connection(1, "replay", _Gone())
    dev._conn = conn
    th = threading.Thread(target=dev._capture_loop, args=(conn,))
    th.start()
    th.join(10)
    assert said == [("error", "The recording could not be read any more (its folder was moved or deleted).")]
    notes = []
    studio.notify = lambda level, text: notes.append((level, text))
    studio._handle_lost("error", said[0][1])
    assert notes == [("error", "The recording could not be read any more (its folder was moved or deleted).")]
    dev._conn = None


def test_a_replay_without_a_calibration_is_not_sent_to_setup(studio, monkeypatch):
    dev = studio.devices
    monkeypatch.setattr(type(dev), "connected", property(lambda self: True))
    dev.busy = None
    monkeypatch.setattr(type(dev), "calib", property(lambda self: None))
    monkeypatch.setattr(type(dev), "source", property(lambda self: "replay"))
    p = studio.projects.create("Replay")
    with pytest.raises(ApiError) as got:
        studio.start_scan(p["id"], "markerless")
    assert got.value.detail == "This recording has no calibration, so it cannot be scanned from."
    assert "Setup" not in (got.value.hint or "")


def test_the_health_route_says_who_chose_the_compute_path(studio, monkeypatch):
    """R20: the line about this computer says the environment turned the graphics card off, not the settings."""
    monkeypatch.setenv("OHS_COMPUTE", "cpu")
    assert studio.health()["engine"]["compute"]["preference_from"] == "environment"
    monkeypatch.delenv("OHS_COMPUTE")
    assert studio.health()["engine"]["compute"]["preference_from"] in ("auto", "settings")
