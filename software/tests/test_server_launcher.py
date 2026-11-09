"""``ohs app`` / ``python -m ohs.server``: start, window close, Ctrl-C, port in use, CLI options.

The pywebview window is replaced by a stub whose ``start()`` checks the server and returns, the
way ``webview.start()`` returns when the user closes the window. Closing it must stop the server
and save a running scan. Ctrl-C in ``--no-window`` mode is simulated with ``_thread.interrupt_main``.
"""
from __future__ import annotations

import _thread
import socket
import subprocess
import sys
import threading
import time
import types

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")
requests = pytest.importorskip("requests")

from ohs.app import cli  # noqa: E402
from ohs.app.config import Config  # noqa: E402
from ohs.server.demo import DemoParams, demo_path, generate_demo  # noqa: E402
from ohs.server.launcher import port_free, run_app  # noqa: E402
from ohs.server.testing import free_port  # noqa: E402


def _cfg(tmp_path):
    cfg = Config()
    cfg.paths.calibration = str(tmp_path / "no-calibration.json")
    return cfg


def _dirs(tmp_path):
    return {"projects_dir": tmp_path / "projects", "demo_dir": tmp_path / "demo", "static_dir": tmp_path / "static",
            "data_dir": tmp_path / "data", "config_path": tmp_path / "config.toml"}


class FakeWebview:
    """Stands in for the ``webview`` module; ``start()`` returns like a closed window."""

    def __init__(self, during=None) -> None:
        self.created = None
        self.during = during
        self.started = False
        self.result = None
        self.settings = {"ALLOW_DOWNLOADS": False}  # pywebview's default

    def create_window(self, title, url, **kw):
        self.created = (title, url, kw)
        return types.SimpleNamespace(destroy=lambda: None)

    def start(self, **kw):
        self.started = True
        self.result = self.during(self.created[1]) if self.during else None


def test_window_close_stops_everything_and_saves_the_scan(tmp_path):
    generate_demo(demo_path(tmp_path / "demo"), DemoParams(n_frames=6, image_size=(320, 200)))
    port = free_port()

    def user_session(url: str) -> dict:
        assert requests.get(url + "api/health", timeout=10).json()["app"] == "openhandscan"
        assert requests.post(url + "api/device/connect", json={"source": "sim"}, timeout=10).status_code == 200
        pid = requests.post(url + "api/projects", json={"name": "Closed"}, timeout=10).json()["id"]
        sid = requests.post(url + "api/scan/start", json={"project_id": pid}, timeout=60).json()["scan_id"]
        time.sleep(1.0)
        return {"pid": pid, "sid": sid}  # ... and the user closes the window mid-scan

    wv = FakeWebview(user_session)
    lines: list[str] = []
    rc = run_app(_cfg(tmp_path), port=port, window=True, echo=lines.append, webview_module=wv, **_dirs(tmp_path))
    assert rc == 0 and wv.started
    title, url, kw = wv.created
    assert title == "OpenHandScan Studio" and url == f"http://127.0.0.1:{port}/"
    assert (kw["width"], kw["height"], kw["resizable"]) == (1440, 900, True)
    assert wv.settings["ALLOW_DOWNLOADS"] is True  # the STL/PLY download buttons work in the window
    assert port_free("127.0.0.1", port)  # the server is gone
    assert any("running at" in s for s in lines) and any("stopped" in s for s in lines)
    import json

    pj = json.loads((tmp_path / "projects" / wv.result["pid"] / "project.json").read_text(encoding="utf-8"))
    scan = pj["scans"][0]
    assert scan["id"] == wv.result["sid"] and scan["status"] == "recorded" and scan["n_frames"] > 0


def test_no_window_runs_until_ctrl_c(tmp_path):
    port = free_port()
    seen: dict = {}

    def interrupter() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                seen["health"] = requests.get(f"http://127.0.0.1:{port}/api/health", timeout=2).json()
                break
            except requests.RequestException:
                time.sleep(0.1)
        time.sleep(0.3)
        _thread.interrupt_main()

    threading.Thread(target=interrupter, daemon=True).start()
    lines: list[str] = []
    rc = run_app(_cfg(tmp_path), port=port, window=False, echo=lines.append, **_dirs(tmp_path))
    assert rc == 130 and seen["health"]["app"] == "openhandscan"
    assert any("Ctrl-C" in s for s in lines) and port_free("127.0.0.1", port)


def test_port_in_use_is_explained(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert not port_free("127.0.0.1", port)
        with pytest.raises(RuntimeError, match="already in use.*--port"):
            run_app(_cfg(tmp_path), port=port, window=False, echo=lambda s: None, **_dirs(tmp_path))


def test_cli_app_options_and_python_m():
    a = cli.build_parser().parse_args(["app", "--host", "0.0.0.0", "--port", "9000", "--no-window", "--open-browser"])
    assert (a.host, a.port, a.no_window, a.open_browser, a.func) == ("0.0.0.0", 9000, True, True, cli.cmd_app)
    a = cli.build_parser().parse_args(["app"])
    assert (a.host, a.port, a.no_window, a.open_browser) == (None, None, False, False)
    out = subprocess.run([sys.executable, "-m", "ohs.server", "--help"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.startswith("usage: ohs app") and "--no-window" in out.stdout
