"""The camera path's warning (ENGINE_PLAN X0a) as the app shows it (APP_API.md, "The camera path's warning").

The engine writes ``report["path_check"]`` and ``report["warnings"]`` for every tracked scan
(:mod:`ohs.track.path_check`). The server carries them: on the track stage (``warnings``), on the scan's card
(``Scan.result.report.warnings`` and ``path_check`` in short), into every later stage's report (the mesh is built
from that path), and into the HTML report beside the calibration's status. It warns only. A scan processed before
the check has none of these, and nothing is shown for it.

The recordings here are too short for the engine's check to judge, so the check is replaced by one that warns.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.projects import report_summary, user_warnings  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402
from recordings import scan_recording  # noqa: E402

T = 30
SENTENCE = ("The camera path jumps by 37 mm near frame 4, more than a hand moves in 0.8 s: tracking may have slid "
            "there. Scan it again more slowly, with more of the part's edges in view. This scan was processed without "
            "a GPU, where tracking holds flat, plain scenes less well.")
SAID = SENTENCE.replace("without a GPU", "without a graphics card")


def _warn(rows, cpu_path=False):
    return {"version": 1, "window_s": 0.8, "limit_mm": 4.0, "frames": 10, "jump_mm": 37.2, "frame": 4,
            "state": "warn", "warning": SENTENCE}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("pathwarn")
    rec = scan_recording(root / "rec", n_frames=10, image_size=(640, 400), seed=3, depth_every=0)
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        yield {"url": srv.url, "root": root, "studio": app.state.studio, "rec": rec}


def _add_scan(env: dict, name: str) -> tuple[str, str]:
    st = env["studio"]
    pid = requests.post(env["url"] + "/api/projects", json={"name": name}, timeout=T).json()["id"]
    scan, rec_dir = st.projects.add_scan(pid, name, "markerless")
    shutil.copytree(env["rec"], rec_dir)
    n = len(RecordingReader(rec_dir))
    st.projects.update_scan(pid, scan["id"], status="recorded", n_frames=n, duration_s=n / 30.0)
    return pid, scan["id"]


def _run(env: dict, url: str, body: dict | None = None) -> dict:
    r = requests.post(url, json=body, timeout=T)
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        j = requests.get(f"{env['url']}/api/jobs/{jid}", timeout=T).json()
        if j["state"] not in ("queued", "running"):
            assert j["state"] == "done", j
            return j
        time.sleep(0.1)
    raise AssertionError("the job did not finish in time")


def _card(env: dict, pid: str, sid: str) -> dict:
    p = requests.get(f"{env['url']}/api/projects/{pid}", timeout=T).json()
    return next(s for s in p["scans"] if s["id"] == sid)


def _html(env: dict, pid: str, sid: str) -> str:
    r = requests.post(f"{env['url']}/api/projects/{pid}/scans/{sid}/report/report.html", json={}, timeout=T)
    assert r.status_code == 200, r.text
    return r.text


# --------------------------------------------------------------------------- the summary
def test_the_summary_keeps_the_warnings_in_the_words_of_the_app():
    rep = {"frames": {"processed": 10}, "warnings": [SENTENCE, "", 3],
           "path_check": {"version": 1, "state": "warn", "jump_mm": 37.2, "frame": 4, "warning": SENTENCE,
                          "window_s": 0.8, "limit_mm": 4.0, "frames": 10}}
    s = report_summary(rep)
    assert s["warnings"] == [SAID]
    assert s["path_check"] == {"state": "warn", "jump_mm": 37.2, "frame": 4}
    assert "GPU" not in json.dumps(s)
    ok = report_summary({"warnings": [], "path_check": {"state": "ok", "jump_mm": 1.2, "frame": 3}})
    assert ok["warnings"] == [] and ok["path_check"] == {"state": "ok", "jump_mm": 1.2, "frame": 3}
    old = report_summary({"frames": {"processed": 10}})  # processed before the check: nothing
    assert "warnings" not in old and "path_check" not in old
    assert user_warnings({"warnings": "not a list"}) == []


# --------------------------------------------------------------------------- through the server
def test_a_tracked_scan_carries_its_warning_everywhere(env, monkeypatch):
    monkeypatch.setattr("ohs.app.process.check_path", _warn)
    pid, sid = _add_scan(env, "Thin plate")
    base = f"{env['url']}/api/projects/{pid}/scans/{sid}"
    _run(env, base + "/process/track", {"quality": "preview"})
    st = requests.get(base + "/process", timeout=T).json()
    assert st["stages"]["track"]["warnings"] == [SAID]
    card = _card(env, pid, sid)
    assert card["result"]["report"]["warnings"] == [SAID]
    assert card["result"]["report"]["path_check"]["state"] == "warn"
    html = _html(env, pid, sid)
    assert "Camera path" in html and "tracking may have slid there" in html and "graphics card" in html
    assert "without a GPU" not in html

    # a later stage builds on the same path: its report, the card and the HTML report still say it
    _run(env, base + "/process/fuse", {})
    res = json.loads((env["studio"].projects.result_dir(pid, sid) / "report.json").read_text(encoding="utf-8"))
    assert res["path_check"]["state"] == "warn" and res["warnings"] == [SENTENCE]
    assert _card(env, pid, sid)["result"]["report"]["warnings"] == [SAID]
    assert "tracking may have slid there" in _html(env, pid, sid)
    st = requests.get(base + "/process", timeout=T).json()
    assert st["stages"]["track"]["warnings"] == [SAID]  # the track stage keeps its own


def test_a_path_that_held_says_so_quietly(env, monkeypatch):
    monkeypatch.setattr("ohs.app.process.check_path",
                        lambda rows, cpu_path=False: {**_warn(rows), "state": "ok", "jump_mm": 1.4, "warning": None})
    pid, sid = _add_scan(env, "Bracket")
    base = f"{env['url']}/api/projects/{pid}/scans/{sid}"
    _run(env, base + "/process/track", {"quality": "preview"})
    assert requests.get(base + "/process", timeout=T).json()["stages"]["track"]["warnings"] == []
    rep = _card(env, pid, sid)["result"]["report"]
    assert rep["warnings"] == [] and rep["path_check"]["state"] == "ok"
    html = _html(env, pid, sid)
    assert "Camera path" in html and "tracking may have slid" not in html


def test_an_older_scan_without_the_check_shows_nothing(env):
    pid, sid = _add_scan(env, "Old scan")
    st = env["studio"]
    res = st.projects.result_dir(pid, sid)
    res.mkdir(parents=True, exist_ok=True)
    # a result from before the check: a mesh and a report without path_check or warnings
    from ohs.track.export import save_mesh
    import numpy as np
    V = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10]], dtype=np.float32)
    F = np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]], dtype=np.int32)
    save_mesh(res / "mesh.ply", V, F)
    (res / "report.json").write_text(json.dumps({"frames": {"processed": 10, "tracked": 10}}), encoding="utf-8")
    st.projects.update_scan(pid, sid, status="processed")
    rep = _card(env, pid, sid)["result"]["report"]
    assert "warnings" not in rep and "path_check" not in rep
    base = f"{env['url']}/api/projects/{pid}/scans/{sid}"
    track = requests.get(base + "/process", timeout=T).json()["stages"]["track"]  # taken over: no warnings
    assert not track.get("warnings")
    assert "Camera path" not in _html(env, pid, sid)


def test_the_path_row_in_the_report_page():
    from ohs.report import build_html
    from ohs.report.data import prepare

    data = {"scan": "S", "calibration": {"path": {"state": "warn", "jump_mm": 37.2, "frame": 4, "warnings": [SAID]}}}
    html = build_html(data)
    assert "Camera path" in html and SAID.split(":")[0] in html
    assert any("tracking may have slid" in w.text for w in prepare(data).warnings)
    ok = build_html({"scan": "S", "calibration": {"path": {"state": "ok", "jump_mm": 1.4, "frame": 3, "warnings": []}}})
    assert "Camera path" in ok and "moved as a hand moves" in ok
    none = build_html({"scan": "S", "calibration": {"path": {"state": None, "warnings": []}}})
    assert "too short to judge" in none
    assert "Camera path" not in build_html({"scan": "S", "calibration": {"parts": {}}})


def test_the_report_words_pass_the_guards():
    """The new texts keep the server's rules (no retired names)."""
    import test_server_error_words as g
    for f in ("projects.py", "report.py"):
        src = (Path(g.SERVER) / f).read_text(encoding="utf-8")
        assert not g.retired_names(src, f)
