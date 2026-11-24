"""A scan in the app whose recorder stops at its budget (the disk cannot keep up) stops by itself and says so.

The real server on the simulator source (as tests/test_server_live.py), a recorder budget of a few frames and a
disk that writes about three PNGs a second: the scan stops through the studio's normal stop path, the saved scan
has the frames that were written and the recorder's sentence as its error, the user is told at once (one notice,
level error: the level the app shows as a toast) and the status message carries ``record_stopped``. Pressing
Stop afterwards finds no scan. A normal scan is unchanged."""
from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

import cv2  # noqa: E402
from websockets.sync.client import connect as ws_connect  # noqa: E402

from ohs.app import live  # noqa: E402
from ohs.app.config import Config  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.demo import DemoParams, demo_path, generate_demo  # noqa: E402
from ohs.server.testing import ServerThread, recv_until  # noqa: E402

T = 10
PAIR = 2 * 640 * 400  # one 640 x 400 grey stereo pair


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("recstop")
    generate_demo(demo_path(root / "demo"), DemoParams(n_frames=12, image_size=(640, 400)))
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        r = requests.post(srv.url + "/api/device/connect", json={"source": "sim"}, timeout=60)
        assert r.status_code == 200, r.text
        yield {"srv": srv, "url": srv.url, "studio": app.state.studio}


def _post(url, path, body=None):
    return requests.post(url + path, json=body, timeout=60)


def _slow_disk(monkeypatch, seconds: float) -> None:
    lock = threading.Lock()
    real = cv2.imwrite

    def slow(*a, **k):
        with lock:
            time.sleep(seconds)
            return real(*a, **k)

    monkeypatch.setattr(cv2, "imwrite", slow)


def _scan_entry(url, pid, sid) -> dict:
    scans = requests.get(url + f"/api/projects/{pid}", timeout=T).json()["scans"]
    return next(s for s in scans if s["id"] == sid)


def test_a_scan_stops_by_itself_when_the_disk_cannot_keep_up(env, monkeypatch):
    url, studio = env["url"], env["studio"]
    monkeypatch.setattr(live, "RECORD_MAX_BYTES", 4 * PAIR)
    _slow_disk(monkeypatch, 0.3)
    pid = _post(url, "/api/projects", {"name": "Slow disk"}).json()["id"]
    with ws_connect(env["srv"].ws_url, max_size=None, open_timeout=5) as ws:
        r = _post(url, "/api/scan/start", {"project_id": pid, "mode": "markerless", "name": "Slow"})
        assert r.status_code == 200, r.text
        sid = r.json()["scan_id"]
        notices: list = []
        stopped_fields: list = []
        seen = {"idle": False}

        statuses: list = []

        def until(m, p, raw):
            if m["type"] == "log" and "Recording stopped" in m["message"]:
                notices.append(m)
            if m["type"] == "status":
                statuses.append(m)
                if m.get("record_stopped"):
                    stopped_fields.append(m["record_stopped"])
            if m["type"] == "session" and m["state"] == "scanning":
                seen["scanning"] = True
            if m["type"] == "session" and m["state"] == "idle" and seen.get("scanning") and not seen["idle"]:
                seen["idle"] = True
                seen["t"] = time.monotonic()
            return seen["idle"] and time.monotonic() - seen["t"] > 1.5  # and a little longer: nothing more is sent

        try:
            recv_until(ws, until, timeout=30)
        except (AssertionError, TimeoutError):
            state = requests.get(url + "/api/scan", timeout=T).json()
            st = studio.session
            rec = getattr(st, "_recorder", None)
            raise AssertionError(f"the scan did not stop by itself: state {state['state']}, {state.get('frames')} frames "
                                 f"counted as recorded, the recorder stopped: {getattr(rec, 'stopped', None)!r}, "
                                 f"{getattr(rec, 'n_refused', None)} frames refused, notices {notices}") from None
        finally:
            if requests.get(url + "/api/scan", timeout=T).json()["state"] != "idle":
                _post(url, "/api/scan/stop")
    assert all("record_stopped" in m for m in statuses)
    assert len(notices) == 1, notices
    text = notices[0]["message"]
    assert text.startswith("Recording stopped: the disk could not keep up (") and text.endswith("are kept.")
    assert notices[0]["level"] == "error"  # shown as a toast (ui/src/lib/notices.ts shows errors only)
    assert stopped_fields and set(stopped_fields) == {text}
    assert requests.get(url + "/api/scan", timeout=T).json()["state"] == "idle"
    s = studio.last_session
    assert s.scan_id == sid
    rec = RecordingReader(s.rec_dir)
    entry = _scan_entry(url, pid, sid)
    assert entry["status"] == "recorded" and entry["n_frames"] == len(rec) > 0
    assert entry["error"] == f"recording problem: {text}"
    assert rec.meta["recording_stopped"]["frames_kept"] == len(rec)
    assert s.n_recorded == len(rec)  # frames refused after the stop are not counted as recorded
    again = _post(url, "/api/scan/stop")  # pressing Stop afterwards: no scan, nothing changed
    assert again.status_code == 409
    assert _scan_entry(url, pid, sid) == entry


def test_a_normal_scan_is_unchanged(env):
    url = env["url"]
    pid = _post(url, "/api/projects", {"name": "Normal"}).json()["id"]
    with ws_connect(env["srv"].ws_url, max_size=None, open_timeout=5) as ws:
        r = _post(url, "/api/scan/start", {"project_id": pid, "mode": "markerless"})
        assert r.status_code == 200, r.text
        sid = r.json()["scan_id"]
        statuses: list = []

        def until(m, p, raw):
            if m["type"] == "status" and m["session"]["state"] == "scanning":
                statuses.append(m)
            return len(statuses) >= 4

        recv_until(ws, until, timeout=30)
        assert all(m["record_stopped"] is None for m in statuses)
        assert _post(url, "/api/scan/stop").status_code == 200
    entry = _scan_entry(url, pid, sid)
    assert entry["status"] == "recorded" and entry["n_frames"] > 0 and not entry.get("error")
