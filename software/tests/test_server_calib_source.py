"""Which calibration the connected device uses (UX_PLAN U21, V2; APP_API.md ``CalibrationInfo.source``).

Checked on the real server (2026-11-20): with the simulator's demo connected, the device's calibration is the demo
recording's own true calibration (``<demo_dir>/sim-demo/calibration.json``), loaded, while Setup's target is the
simulator's own calibration (``<data_dir>/sim/calibration.json``), not made yet. The chip said "Calibration loaded"
beside a rail that said "Not calibrated yet". ``source`` tells the pages which one is in use, so the chip says
"Demo calibration" for the demo's own.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.demo import DemoParams, demo_path, generate_demo  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402

T = 30


def test_the_calibration_says_where_it_comes_from(tmp_path):
    generate_demo(demo_path(tmp_path / "demo"), DemoParams(n_frames=8, image_size=(320, 200)))
    cfg = Config()
    cfg.paths.calibration = str(tmp_path / "no-calibration.json")
    app = create_app(cfg, projects_dir=tmp_path / "projects", demo_dir=tmp_path / "demo", static_dir=tmp_path / "static",
                     data_dir=tmp_path / "data", config_path=tmp_path / "config.toml")
    with ServerThread(app) as srv:
        d = requests.get(srv.url + "/api/device", timeout=T).json()
        assert d["connected"] is False and d["calibration"]["source"] == "scanner"
        assert requests.post(srv.url + "/api/device/connect", json={"source": "sim"}, timeout=T).status_code == 200
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not d.get("connected"):
            time.sleep(0.2)
            d = requests.get(srv.url + "/api/device", timeout=T).json()
        assert d["connected"] and d["sim_scene"] == "demo"
        cal = d["calibration"]
        assert cal["loaded"] is True and cal["source"] == "demo"
        setup = requests.get(srv.url + "/api/setup", timeout=T).json()
        assert setup["target"] == "sim" and not any((setup["calibration"].get("parts") or {}).values())
