"""M2 setup through the real server: setup state, print check, printables, devices probe, settings,
self-test and the launch check, plus the config writer behind the settings.

Every test writes only into its tmp folder: the config file (``config_path``), Studio's data
(``data_dir``), the printables and the calibration paths all point there.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("fastapi")
requests = pytest.importorskip("requests")

from websockets.sync.client import connect as ws_connect  # noqa: E402

from ohs.app.config import Config, ConfigError, load_config, render_template, set_config_values  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.demo import DemoParams, demo_path, generate_demo  # noqa: E402
from ohs.server.testing import ServerThread, recv_until  # noqa: E402

T = 30


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("setup_demo")
    generate_demo(demo_path(d), DemoParams(n_frames=12, image_size=(640, 400)))
    return d


def _app(root: Path, demo_dir: Path, **kw):
    cfg = Config()
    cfg.paths.calibration = str(root / "device" / "calibration.json")
    cfg.paths.scans_dir = str(root / "scans")
    cfg.paths.printables_dir = str(root / "printables")
    return create_app(cfg, projects_dir=root / "projects", demo_dir=demo_dir, static_dir=root / "static",
                      data_dir=root / "data", config_path=root / "config.toml", **kw)


@pytest.fixture
def env(tmp_path, demo_dir):
    app = _app(tmp_path, demo_dir)
    with ServerThread(app) as srv:
        yield {"srv": srv, "url": srv.url, "root": tmp_path, "studio": app.state.studio, "demo": demo_dir}


def _get(url, path):
    return requests.get(url + path, timeout=T)


def _post(url, path, body=None):
    return requests.post(url + path, json=body, timeout=120)


def _ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _refused(r, status: int, needle: str = "") -> dict:
    assert r.status_code == status, r.text
    body = r.json()
    assert body["detail"] and body.get("hint"), body
    assert needle.lower() in body["detail"].lower(), body
    return body


def _wait(cond, timeout=30.0, what="condition", every=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = cond()
        if v:
            return v
        time.sleep(every)
    raise AssertionError(f"timed out waiting for {what}")


def _wait_job(url, jid, timeout=180.0) -> dict:
    return _wait(lambda: (j := _get(url, f"/api/jobs/{jid}").json())["state"] not in ("queued", "running") and j,
                 timeout, f"job {jid}", every=0.1)


def _steps(setup: dict) -> dict:
    return {s["id"]: s for s in setup["steps"]}


# --------------------------------------------------------------------------- setup state
def test_setup_state_defaults_first_run_and_dismiss(env):
    url, root = env["url"], env["root"]
    s = _ok(_get(url, "/api/setup"))
    assert s["target"] == "device" and s["first_run"] is True and s["dismissed"] is False
    assert [x["id"] for x in s["steps"]] == ["connect", "selftest", "print", "stereo", "lasers", "imu", "check"]
    assert all(x["state"] == "todo" and x["summary"] and x["when"] is None for x in s["steps"])
    cal = s["calibration"]
    assert cal["path"] == str(root / "device" / "calibration.json") and cal["exists"] is False and cal["stereo"] is False
    assert cal["parts"] == {"stereo": None, "lasers": None, "imu": None} and cal["backup"] is None
    assert s["selftest"] is None and s["print_check"] is None and s["launch_check"] is None and s["last_check"] is None
    b = s["board"]
    assert (b["squares_x"], b["squares_y"], b["square_mm"], b["marker_mm"], b["paper"], b["scale"]) == (11, 8, 20.0, 15.0,
                                                                                                         "letter", 1.0)
    assert "11 x 8 squares of 20 mm" in b["describe"]
    d = _ok(_post(url, "/api/setup/dismiss", {"dismissed": True}))
    assert d["dismissed"] is True and d["first_run"] is False
    data = json.loads((root / "data" / "setup.json").read_text(encoding="utf-8"))
    assert data["targets"]["device"]["dismissed"] is True and data["targets"]["sim"]["dismissed"] is False
    assert _ok(_post(url, "/api/setup/dismiss", {"dismissed": False}))["first_run"] is True
    _refused(_post(url, "/api/setup/dismiss", {}), 422)
    # the simulator is its own target
    dev = _ok(_post(url, "/api/device/connect", {"source": "sim"}))
    assert dev["sim_scene"] == "demo"
    s = _ok(_get(url, "/api/setup"))
    assert s["target"] == "sim" and s["calibration"]["path"] == str(root / "data" / "sim" / "calibration.json")
    st = _steps(s)
    assert st["connect"]["state"] == "done" and "today" in st["connect"]["summary"] and st["print"]["state"] == "optional"
    _ok(_post(url, "/api/device/disconnect"))
    assert _steps(_ok(_get(url, "/api/setup")))["connect"]["state"] == "todo"  # the device target: no camera yet


def test_print_check_every_branch(env):
    url, studio = env["url"], env["studio"]
    pc = _ok(_post(url, "/api/setup/print-check", {"measured_mm": 100.2}))
    assert pc["ok"] is True and pc["scale"] == 1.0 and pc["can_apply"] is False and "accurate" in pc["verdict"]
    assert pc["error_pct"] == pytest.approx(0.2) and pc["when"]
    s = _ok(_get(url, "/api/setup"))
    assert s["print_check"]["measured_mm"] == 100.2 and _steps(s)["print"]["state"] == "done"
    pc = _ok(_post(url, "/api/setup/print-check", {"measured_mm": 98.8}))
    assert pc["ok"] is False and pc["can_apply"] is True and pc["scale"] == 1.0
    assert "-1.2 %" in pc["verdict"] and "100 %" in pc["verdict"] and "measurement" in pc["verdict"]
    assert _steps(_ok(_get(url, "/api/setup")))["print"]["state"] == "warn"
    pc = _ok(_post(url, "/api/setup/print-check", {"measured_mm": 98.8, "apply": True}))
    assert pc["scale"] == pytest.approx(0.988) and "19.76 mm" in pc["verdict"]
    s = _ok(_get(url, "/api/setup"))
    assert s["board"]["scale"] == pytest.approx(0.988) and "98.8 %" in s["board"]["describe"]
    assert _steps(s)["print"]["state"] == "warn" and "your measurement" in _steps(s)["print"]["summary"]
    spec = studio.setup.calib_spec("device")  # the calibration spec is scaled ...
    assert spec.square_mm == pytest.approx(19.76) and spec.marker_mm == pytest.approx(14.82)
    assert studio.cfg.board.spec().square_mm == 20.0  # ... the printed one is not
    assert studio.setup.calib_spec("sim").square_mm == 20.0  # and the simulator's board is exact
    pc = _ok(_post(url, "/api/setup/print-check", {"measured_mm": 92.0, "apply": True}))
    assert pc["ok"] is False and pc["can_apply"] is False and pc["scale"] == 1.0 and "measure again" in pc["verdict"].lower()
    assert _ok(_get(url, "/api/setup"))["board"]["scale"] == 1.0
    pc = _ok(_post(url, "/api/setup/print-check", {"measured_mm": 103.9}))
    assert pc["can_apply"] is True and "+3.9 %" in pc["verdict"]
    _refused(_post(url, "/api/setup/print-check", {"measured_mm": 0}), 422)
    _refused(_post(url, "/api/setup/print-check", {"measured_mm": "x"}), 422)


# --------------------------------------------------------------------------- printables
@pytest.mark.slow
def test_printables_listed_served_and_whitelisted(env, monkeypatch):
    url, root, studio = env["url"], env["root"], env["studio"]
    studio.printables_mask_dpi = 300  # the 1200 dpi sheet alone takes ~20 s
    opened: list = []
    from ohs.server import printables

    monkeypatch.setattr(printables, "open_folder", lambda p: opened.append(Path(p)) or True)
    p = _ok(_get(url, "/api/setup/printables"))
    assert p["files"] == [] and p["generated"] is None and p["dir"] == str(root / "printables") and p["paper"] == "letter"
    _refused(_get(url, "/api/setup/printables/files/charuco_board.pdf"), 404, "no printable")
    job = _ok(_post(url, "/api/setup/printables", {"paper": "a4"}))
    assert job["kind"] == "printables"
    _refused(_post(url, "/api/setup/printables", {"paper": "a4"}), 409, "already")
    _refused(_post(url, "/api/setup/printables", {"paper": "legal"}), 422)
    j = _wait_job(url, job["id"])
    assert j["state"] == "done", j
    res = j["result"]
    assert res["paper"] == "a4" and res["generated"] and res["dir"] == str(root / "printables")
    # the paper change went to the (tmp) config file, comments and all
    text = (root / "config.toml").read_text(encoding="utf-8")
    assert 'paper = "a4"' in text and "# ChArUco calibration board" in text and load_config(root / "config.toml").board.paper == "a4"
    assert _ok(_get(url, "/api/settings"))["board"]["paper"] == "a4"
    files = {f["name"]: f for f in res["files"]}
    assert {"charuco_board.pdf", "charuco_board.png", "apriltags.pdf", "apriltags.png", "projector_mask_sheet.pdf",
            "projector_mask_sheet.png", "projector_mask_film.gbr", "projector_mask_film.svg"} <= set(files)
    assert "projector_mask_preview.png" not in files
    assert {f["kind"] for f in files.values()} == {"board", "tags", "mask"}
    for f in files.values():
        assert f["title"] and f["description"] and f["bytes"] > 0 and f["url"] == f"/api/setup/printables/files/{f['name']}"
        words = (f["title"] + " " + f["description"]).lower()
        assert "backup" not in words and "fallback" not in words, f
    assert "option B" in files["projector_mask_sheet.pdf"]["title"]
    assert files["charuco_board.pdf"]["preview_url"].endswith("/charuco_board.png")
    assert files["projector_mask_film.gbr"]["preview_url"].endswith("/projector_mask_preview.png")
    media = {".pdf": "application/pdf", ".png": "image/png", ".gbr": "application/vnd.gerber", ".svg": "image/svg+xml"}
    for name, f in files.items():
        r = _get(url, f["url"])
        assert r.status_code == 200 and r.headers["content-type"].startswith(media[Path(name).suffix]), name
        assert len(r.content) == f["bytes"]
    prev = _get(url, files["projector_mask_sheet.pdf"]["preview_url"])
    assert prev.status_code == 200 and prev.content[:4] == b"\x89PNG"
    assert _get(url, "/api/setup/printables/files/charuco_board.pdf").content[:4] == b"%PDF"
    (root / "printables" / "notes.txt").write_text("mine", encoding="utf-8")
    for bad in ("notes.txt", "setup.json", "config.toml", "..%2Fconfig.toml", "charuco_board.txt", "apriltags_p1.png"):
        _refused(_get(url, f"/api/setup/printables/files/{bad}"), 404)
    assert _ok(_get(url, "/api/setup/printables"))["files"] == res["files"]  # remembered
    o = _ok(_post(url, "/api/setup/printables/open"))
    assert o == {"opened": True, "dir": str(root / "printables")} and opened == [root / "printables"]
    monkeypatch.setattr(printables, "open_folder", lambda p: False)
    assert _ok(_post(url, "/api/setup/printables/open"))["opened"] is False
    assert (root / "printables" / "notes.txt").read_text(encoding="utf-8") == "mine"  # never touched


# --------------------------------------------------------------------------- settings / config writer
def test_camera_settings_are_saved_to_the_config_file(env):
    url, root, studio = env["url"], env["root"], env["studio"]
    s = _ok(_post(url, "/api/settings/camera", {"index": 2, "swap_lr": True}))
    assert s["camera"]["index"] == 2 and s["camera"]["swap_lr"] is True and s["effective"]["config_path"] == str(root / "config.toml")
    assert studio.cfg.camera.index == 2 and studio.cfg.camera.swap_lr is True
    text = (root / "config.toml").read_text(encoding="utf-8")
    assert "index = 2" in text and "swap_lr = true" in text and "# Stereo camera (ELP dual OV9281" in text
    c = load_config(root / "config.toml")
    assert (c.camera.index, c.camera.swap_lr) == (2, True)
    s = _ok(_post(url, "/api/settings/camera", {"swap_lr": False}))
    assert s["camera"]["index"] == 2 and s["camera"]["swap_lr"] is False
    _refused(_post(url, "/api/settings/camera", {"index": -1}), 422)
    assert _ok(_post(url, "/api/settings/camera", {}))["camera"]["index"] == 2  # nothing to change


def test_config_writer_keeps_comments_and_inserts(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("# my scanner\n[camera]\nindex = 1   # the ELP\nbackend = \"dshow\"\n\n# my board\n[board]\n"
                 "squares_x = 11\n\n[emitters.profiles]\nscan = [\n  100.0, 100.0,\n  50.0,\n]\n", encoding="utf-8")
    cfg = Config()
    set_config_values({"camera": {"index": 3, "swap_lr": True}, "board": {"paper": "a4"}, "app": {"port": 9001},
                       "emitters.profiles": {"scan": [90.0, 90.0, 40.0]}}, p, cfg=cfg)
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# my scanner\n[camera]\nindex = 3  # the ELP\nbackend = \"dshow\"\nswap_lr = true\n")
    assert "# my board\n[board]\nsquares_x = 11\npaper = \"a4\"\n" in text
    assert "scan = [90.0, 90.0, 40.0]" in text and "50.0," not in text and text.rstrip().endswith("[app]\nport = 9001")
    c = load_config(p)
    assert (c.camera.index, c.camera.swap_lr, c.camera.backend, c.board.paper, c.app.port) == (3, True, "dshow", "a4", 9001)
    assert c.emitters.profiles["scan"] == (90.0, 90.0, 40.0)
    assert (cfg.camera.index, cfg.camera.swap_lr, cfg.board.paper, cfg.source) == (3, True, "a4", p)
    with pytest.raises(ConfigError, match="did you mean"):
        set_config_values({"camera": {"indx": 1}}, p)
    with pytest.raises(ConfigError, match="camera.index"):
        set_config_values({"camera": {"index": "zero"}}, p)
    assert load_config(p).camera.index == 3  # a refused change writes nothing
    fresh = tmp_path / "sub" / "new.toml"
    set_config_values({"board": {"paper": "a4"}}, fresh)
    text = fresh.read_text(encoding="utf-8")
    assert len(text.splitlines()) == len(render_template().splitlines()) and 'paper = "a4"' in text
    assert "# OpenHandScan configuration" in text and load_config(fresh).board.paper == "a4"
    crlf = tmp_path / "crlf.toml"
    crlf.write_bytes(b"[camera]\r\nindex = 0\r\n")
    set_config_values({"camera": {"index": 5}}, crlf)
    assert crlf.read_bytes() == b"[camera]\r\nindex = 5\r\n"


# --------------------------------------------------------------------------- devices probe
def test_devices_probe(env, monkeypatch):
    from test_app_live import FakeCamera

    import ohs.devices.camera as camera_mod
    import ohs.devices.pico as pico_mod
    from ohs.server import setup as setup_mod

    url, studio = env["url"], env["studio"]
    found = [{"index": 0, "backend": "DSHOW", "width": 640, "height": 480, "fps": 30.0, "fourcc": "YUY2"},
             {"index": 1, "backend": "DSHOW", "width": 2560, "height": 800, "fps": 60.0, "fourcc": "MJPG", "name": "ELP"}]
    monkeypatch.setattr(camera_mod, "list_cameras", lambda max_index=8, backend="auto": list(found))
    monkeypatch.setattr(pico_mod, "find_pico_port", lambda: "COM7")
    p = _ok(_get(url, "/api/setup/devices"))
    assert p["configured_index"] == 0 and p["pico_port"] == "COM7" and p["note"] is None
    c0, c1 = p["cameras"]
    assert (c0["index"], c0["stereo_like"], c0["configured"], c0["in_use"], c0["name"]) == (0, False, True, False, None)
    assert (c1["index"], c1["stereo_like"], c1["configured"], c1["name"], c1["fourcc"]) == (1, True, False, "ELP", "MJPG")
    monkeypatch.setattr(camera_mod, "list_cameras", lambda max_index=8, backend="auto": [])
    monkeypatch.setattr(pico_mod, "find_pico_port", lambda: None)
    p = _ok(_get(url, "/api/setup/devices"))
    assert p["cameras"] == [] and p["pico_port"] is None
    assert "No camera found" in p["note"] and "simulator" in p["note"] and "Pico was not found" in p["note"]
    # the camera in use is never opened again: its entry comes from the running camera
    cam = FakeCamera(size=(1280, 800), fps=30.0)

    def no_pico(_c):
        raise ConnectionError("no Pico")

    studio.devices.camera_factory = lambda _c: cam
    studio.devices.pico_factory = no_pico
    probed: list = []
    monkeypatch.setattr(camera_mod, "list_cameras", lambda *a, **k: pytest.fail("the camera in use was probed"))
    monkeypatch.setattr(setup_mod, "probe_cameras", lambda idx, backend="auto": probed.append(list(idx)) or [found[0] | {"index": 3}])
    _ok(_post(url, "/api/device/connect", {"source": "camera"}))
    p = _ok(_get(url, "/api/setup/devices"))
    assert probed == [[1, 2, 3, 4, 5, 6, 7]]
    assert [(c["index"], c["in_use"], c["stereo_like"]) for c in p["cameras"]] == [(0, True, True), (3, False, False)]
    _ok(_post(url, "/api/device/disconnect"))


# --------------------------------------------------------------------------- self-test
def test_selftest_on_the_simulator_stream(env):
    url, studio = env["url"], env["studio"]
    studio.setup.stream_seconds = 1.5
    _ok(_post(url, "/api/device/connect", {"source": "sim"}))
    job = _ok(_post(url, "/api/setup/selftest"))
    assert job["kind"] == "selftest"
    _refused(_post(url, "/api/setup/selftest"), 409, "already running")
    j = _wait_job(url, job["id"])
    assert j["state"] == "done", j
    res = j["result"]
    assert res["source"] == "sim" and res["overall"] == "PASS" and res["when"], res
    checks = {c["id"]: c for c in res["checks"]}
    for name in ("camera", "camera fps", "camera image", "imu rate", "imu values"):
        assert checks[name]["status"] == "PASS" and checks[name]["fix"] is None, checks[name]
    for name in ("pico", "clock sync", "emitters", "trigger"):
        assert checks[name]["status"] == "SKIP" and "simulator" in checks[name]["detail"], checks[name]
    assert checks["camera fps"]["name"] == "Camera frame rate"
    s = _ok(_get(url, "/api/setup"))
    assert s["target"] == "sim" and s["selftest"]["overall"] == "PASS" and "message" not in s["selftest"]
    assert _steps(s)["selftest"]["state"] == "done" and "checks passed" in _steps(s)["selftest"]["summary"]
    assert _ok(_get(url, "/api/device"))["connected"]  # the stream test never disconnects


@pytest.mark.slow
def test_selftest_with_the_camera_reconnects_it(tmp_path, demo_dir):
    from test_app_live import FakeCamera, SimPico

    cam = FakeCamera(size=(320, 200), fps=30.0)
    sp = SimPico()
    app = _app(tmp_path, demo_dir, camera_factory=lambda _c: cam, pico_factory=lambda _c: sp.pico())
    studio = app.state.studio
    studio.cfg.camera.width, studio.cfg.camera.height, studio.cfg.camera.fps = 640, 200, 30.0
    studio.setup.selftest_seconds = 2.0
    try:
        with ServerThread(app) as srv:
            url = srv.url
            _ok(_post(url, "/api/device/connect", {"source": "camera"}))
            job = _ok(_post(url, "/api/setup/selftest"))
            seen = {"busy": False, "prompts": set()}

            def watch() -> None:
                deadline = time.monotonic() + 60
                pressed = False
                while time.monotonic() < deadline:
                    j = requests.get(url + f"/api/jobs/{job['id']}", timeout=T).json()
                    seen["prompts"].add(j["message"])
                    if studio.devices.busy == "Running the self-test…":
                        seen["busy"] = True
                    if j["message"] == "Press and release the trigger now" and not pressed:
                        sp.press(0.1)
                        pressed = True
                    if j["state"] not in ("queued", "running"):
                        return
                    time.sleep(0.03)

            th = threading.Thread(target=watch, daemon=True)
            th.start()
            j = _wait_job(url, job["id"], timeout=90)
            th.join(5)
            assert j["state"] == "done", j
            res = j["result"]
            checks = {c["id"]: c for c in res["checks"]}
            assert res["source"] == "camera" and set(checks) >= {"camera", "camera fps", "camera image", "pico", "imu rate",
                                                                 "imu values", "clock sync", "trigger"}
            assert checks["camera"]["status"] == "PASS" and checks["pico"]["status"] == "PASS"
            assert checks["trigger"]["status"] == "PASS" and checks["imu values"]["status"] == "PASS"
            for name in ("vertical line laser", "horizontal line laser", "dot projector"):  # the fake camera sees no light
                assert checks[name]["status"] == "WARN" and "key switch" in checks[name]["fix"], checks[name]
            assert res["overall"] == "WARN"
            assert seen["busy"] and "Press and release the trigger now" in seen["prompts"]
            assert any(m.startswith("Keep the scanner still for 2 s") for m in seen["prompts"])
            assert any(m.startswith("Point the scanner at a light wall 20–40 cm away") for m in seen["prompts"])
            dev = _ok(_get(url, "/api/device"))
            assert dev["source"] == "camera" and dev["connected"] and dev["busy"] is None and dev["pico"]["connected"]
            assert sp.emits()[-1] == "EMIT 0 0 0"
            s = _ok(_get(url, "/api/setup"))
            assert _steps(s)["selftest"]["state"] == "warn" and "warning" in _steps(s)["selftest"]["summary"]
            assert _steps(s)["connect"]["state"] == "done" and "Pico" in _steps(s)["connect"]["summary"]
    finally:
        sp.close()


def test_selftest_without_a_pico_says_what_to_do():
    from test_app_live import FakeCamera

    from ohs.server.selftest import result_dict, run_camera_selftest

    cfg = Config()
    cfg.camera.width, cfg.camera.height, cfg.camera.fps = 320, 100, 60.0
    messages: list[str] = []

    def no_pico(_c):
        raise ConnectionError("no Pico found (USB VID 0x2E8A)")

    checks = run_camera_selftest(cfg, lambda _c: FakeCamera(size=(160, 100), fps=60.0), no_pico, messages.append,
                                 lambda f: None, seconds=0.3)
    by = {c["id"]: c for c in checks}
    assert by["pico"]["status"] == "FAIL" and "no Pico found" in by["pico"]["detail"]
    assert "data USB cable" in by["pico"]["fix"] and by["camera"]["status"] == "PASS"
    assert "trigger" not in by and "Opening the camera…" in messages
    res = result_dict(checks, "camera")
    assert res["overall"] == "FAIL" and res["message"].startswith("Self-test failed")


# --------------------------------------------------------------------------- launch check
def test_launch_check_flags_a_perturbed_calibration(tmp_path, demo_dir):
    from ohs.geometry import so3_exp
    from ohs.types import ScannerCalibration, StereoCalibration

    good = demo_path(demo_dir)
    bad = tmp_path / "bad-rec"
    shutil.copytree(good, bad)
    cal = ScannerCalibration.load(bad / "calibration.json")
    s = cal.stereo
    moved = StereoCalibration.build(s.image_size, s.K1, s.D1, s.K2, s.D2, so3_exp(np.array([0.008, 0.0, 0.0])) @ s.R, s.T,
                                    rms=s.rms)
    ScannerCalibration(moved, cal.lasers, cal.imu).save(bad / "calibration.json")
    app = _app(tmp_path, demo_dir)
    with ServerThread(app) as srv, ws_connect(srv.ws_url, max_size=None, open_timeout=5) as ws:
        url = srv.url
        dev = _ok(_post(url, "/api/device/connect", {"source": "replay", "recording": str(good), "loop": True}))
        assert dev["launch_check"]["state"] in ("running", "ok")
        dev = _wait(lambda: (d := _get(url, "/api/device").json())["launch_check"]["state"] != "running" and d, 30,
                    "the launch check")
        lc = dev["launch_check"]
        assert lc["state"] == "ok" and lc["dv_px"] < 0.6 and lc["matches"] >= 40 and lc["when"], lc
        assert not any("2-minute check" in w for w in dev["warnings"])
        msgs = recv_until(ws, lambda m, p, raw: m["type"] == "setup" and m.get("launch_check"), timeout=10)
        assert not any(m["type"] == "log" and m["level"] == "warn" for m, _, _ in msgs)  # silent when fine
        _ok(_post(url, "/api/device/connect", {"source": "replay", "recording": str(bad), "loop": True}))
        dev = _wait(lambda: (d := _get(url, "/api/device").json())["launch_check"]["state"] != "running" and d, 30,
                    "the launch check")
        lc = dev["launch_check"]
        assert lc["state"] == "drift" and lc["dv_px"] > 0.6 and lc["message"], lc
        assert any("2-minute check" in w for w in dev["warnings"])
        msgs = recv_until(ws, lambda m, p, raw: m["type"] == "log" and m["level"] == "warn", timeout=10)
        assert "may have moved" in msgs[-1][0]["message"]
        setup = _ok(_get(url, "/api/setup"))
        assert setup["target"] == "device" and setup["launch_check"]["state"] == "drift"
