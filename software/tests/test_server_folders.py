"""Opening the app's folders and the app's log (UX_PLAN U39, U12, U34; APP_API.md "Folders and the log").

``POST /api/open-folder {what, project_id?}`` opens one of the folders the app owns: the settings folder, the log
folder, a project's folder. The page names the folder by what it is, never by a path. ``GET /api/log/file`` is the
app's log, saved for a bug report. Opening a real file manager is replaced here by a recorder.
"""
from __future__ import annotations

import logging

import pytest

pytest.importorskip("fastapi")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402

T = 30


@pytest.fixture()
def env(tmp_path, monkeypatch):
    opened: list = []
    monkeypatch.setattr("ohs.server.printables.open_folder", lambda p: opened.append(p) or True)
    cfg = Config()
    cfg.paths.calibration = str(tmp_path / "no-calibration.json")
    app = create_app(cfg, projects_dir=tmp_path / "projects", demo_dir=tmp_path / "demo", static_dir=tmp_path / "static",
                     data_dir=tmp_path / "data", config_path=tmp_path / "conf" / "config.toml")
    with ServerThread(app) as srv:
        yield {"url": srv.url, "root": tmp_path, "opened": opened, "studio": app.state.studio}


def _plain(r, status: int) -> dict:
    assert r.status_code == status, r.text
    body = r.json()
    for k in ("detail", "hint"):
        assert body.get(k) and body[k][0].isupper() and body[k].endswith("."), body
        assert "/api" not in body[k] and "\\" not in body[k] and "project_id" not in body[k], body
    return body


def test_open_the_settings_the_log_and_a_project_folder(env):
    url = env["url"] + "/api/open-folder"
    for what in ("settings", "logs"):
        r = requests.post(url, json={"what": what}, timeout=T)
        assert r.status_code == 200, r.text
        assert r.json() == {"opened": True, "what": what}
    pid = requests.post(env["url"] + "/api/projects", json={"name": "Door"}, timeout=T).json()["id"]
    r = requests.post(url, json={"what": "project", "project_id": pid}, timeout=T)
    assert r.status_code == 200 and r.json() == {"opened": True, "what": "project"}
    root = env["root"]
    assert [str(p) for p in env["opened"]] == [str(root / "conf"), str(root / "data" / "logs"), str(root / "projects" / pid)]


def test_only_the_apps_own_folders_never_a_path(env):
    url = env["url"] + "/api/open-folder"
    _plain(requests.post(url, json={"what": "C:\\Windows"}, timeout=T), 422)
    _plain(requests.post(url, json={"what": "project"}, timeout=T), 422)
    for bad in ("..", "../data", "a..b", "C:\\x", "/etc"):
        _plain(requests.post(url, json={"what": "project", "project_id": bad}, timeout=T), 404)
    _plain(requests.post(url, json={"what": "project", "project_id": "no-such-1234"}, timeout=T), 404)
    r = requests.post(url, json={"what": "settings", "path": "C:\\Windows"}, timeout=T)  # a path is ignored
    assert r.status_code == 200
    assert all("Windows" not in str(p) for p in env["opened"])


def test_a_folder_that_does_not_open_says_so(env, monkeypatch):
    monkeypatch.setattr("ohs.server.printables.open_folder", lambda p: False)
    body = _plain(requests.post(env["url"] + "/api/open-folder", json={"what": "logs"}, timeout=T), 409)
    assert "could not be opened" in body["detail"]


def test_the_log_for_a_bug_report(env):
    logging.getLogger("ohs.server.test").info("a line for the bug report")
    logging.getLogger("ohs.server.test").warning("a warning for the bug report")
    r = requests.get(env["url"] + "/api/log/file", timeout=T)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/plain")
    assert "attachment" in r.headers.get("content-disposition", "") and "openhandscan-log-" in r.headers["content-disposition"]
    text = r.text
    assert "OpenHandScan Studio" in text and "a line for the bug report" in text and "a warning for the bug report" in text
    assert (env["root"] / "data" / "logs" / "studio.log").is_file()


def test_the_log_handler_goes_with_the_app(tmp_path):
    """A second app (or the end of the first) leaves no handler of the first behind."""
    before = list(logging.getLogger("ohs").handlers)
    level = logging.getLogger("ohs").level
    cfg = Config()
    cfg.paths.calibration = str(tmp_path / "no-calibration.json")
    app = create_app(cfg, projects_dir=tmp_path / "p", demo_dir=tmp_path / "d", static_dir=tmp_path / "s",
                     data_dir=tmp_path / "data", config_path=tmp_path / "c.toml")
    with ServerThread(app):
        assert len(logging.getLogger("ohs").handlers) == len(before) + 1
    assert logging.getLogger("ohs").handlers == before and logging.getLogger("ohs").level == level
