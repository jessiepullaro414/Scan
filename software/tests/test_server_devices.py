"""DeviceManager + Studio with the camera source: a fake camera and the real Pico firmware logic.

``FakeCamera`` and ``SimPico`` come from test_app_live.py (``SimPico`` runs ``firmware/main.py``
behind an in-memory serial port, so the host client speaks the real protocol). The camera sends
a rendered simulator frame over and over, with that recording's calibration as the configured
one. Covered: emitters follow the scan (scan profile on start, off on pause, user levels kept
for resume, off on stop / disconnect / shutdown / camera loss), connecting without a Pico (a
warning, no emitters, no IMU), the trigger (short press: start / pause / resume, long press:
stop) and the replay IMU/event feed of looped recordings.
"""
from __future__ import annotations

import csv
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("open3d")

from test_app_live import FakeCamera, SimPico  # noqa: E402

from ohs.app.config import Config  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.server.devices import ReplayFeed  # noqa: E402
from ohs.server.protocol import ApiError  # noqa: E402
from ohs.server.studio import Studio  # noqa: E402
from ohs.sim.datasets import make_scan_recording  # noqa: E402


@pytest.fixture(scope="module")
def sim_rec(tmp_path_factory) -> Path:
    return make_scan_recording(tmp_path_factory.mktemp("devrec") / "rec", n_frames=3, image_size=(640, 400), seed=5,
                               depth_every=0)


class DyingCamera(FakeCamera):
    """Stops working after ``die_after`` frames, like an unplugged camera."""

    def __init__(self, *a, die_after: int = 10**9, **kw) -> None:
        super().__init__(*a, **kw)
        self.die_after = die_after

    def read(self, timeout: float = 1.0):
        if self.n_read >= self.die_after:
            raise RuntimeError("camera 0 delivered no frame for 3.0 s")
        return super().read(timeout)


def _studio(tmp_path: Path, sim_rec: Path, pico: SimPico | None = None, die_after: int = 10**9):
    cfg = Config()
    cfg.paths.calibration = str(sim_rec / "calibration.json")
    f = RecordingReader(sim_rec).frame(0)
    cam = DyingCamera(size=(640, 400), fps=30.0, images=(f.left, f.right), die_after=die_after)

    def pico_factory(_cfg):
        if pico is None:
            raise ConnectionError("no Pico found (USB VID 0x2E8A); set PicoParams.port")
        return pico.pico()

    st = Studio(cfg, projects_dir=tmp_path / "projects", demo_dir=tmp_path / "demo", static_dir=tmp_path / "static",
                camera_factory=lambda _cfg: cam, pico_factory=pico_factory)
    st.startup()
    return st, cam


def _wait(cond, timeout: float = 20.0, what: str = "condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def _last_emit(sp: SimPico) -> str:
    return sp.emits()[-1] if sp.emits() else ""


def test_emitters_follow_the_scan_and_go_off(tmp_path, sim_rec):
    sp = SimPico()
    st, cam = _studio(tmp_path, sim_rec, pico=sp)
    try:
        code, dev = st.connect("camera")
        assert code == 200 and dev["source"] == "camera" and dev["camera"]["connected"]
        pico = dev["pico"]
        assert pico["connected"] and pico["firmware"].startswith("ohs-fw") and pico["imu"] == "LSM6DSOX"
        assert pico["emitters"] == {"vertical": 0.0, "horizontal": 0.0, "dots": 0.0}
        assert dev["calibration"]["loaded"] and dev["calibration"]["image_size"] == [640, 400] and dev["imu_stream"]
        dev = st.set_emitters({"vertical": 40})
        assert dev["pico"]["emitters"]["vertical"] == 40.0 and _last_emit(sp) == "EMIT 40 0 0"
        pid = st.projects.create("Camera scan")["id"]
        assert st.start_scan(pid)["state"] == "scanning"
        assert _last_emit(sp) == "EMIT 100 100 100"  # the [emitters] scan profile
        s = st.session
        _wait(lambda: s.n_recorded > 15 and s.n_processed > 3, what="recorded and processed frames")
        assert st.status_message()["emitters"] == {"vertical": 100.0, "horizontal": 100.0, "dots": 100.0}
        st.pause_scan()
        assert _last_emit(sp) == "EMIT 0 0 0" and st.session_state()["state"] == "paused"
        n = s.n_recorded
        time.sleep(0.3)
        assert s.n_recorded == n  # paused: nothing recorded
        st.set_emitters({"dots": 50})  # stored for the resume, not switched on while paused
        assert _last_emit(sp) == "EMIT 0 0 0"
        st.resume_scan()
        assert _last_emit(sp) == "EMIT 100 100 50"
        _wait(lambda: s.n_recorded > n + 5, what="frames after resume")
        scan = st.stop_scan()["scan"]
        assert _last_emit(sp) == "EMIT 0 0 0" and scan["status"] == "recorded" and scan["n_frames"] > 20
        rec = st.projects.recording_dir(pid, scan["id"])
        with open(rec / "events.csv", newline="", encoding="utf-8") as fh:
            events = [(r["kind"], r["value"]) for r in csv.DictReader(fh)]
        assert ("session", "pause") in events and ("session", "resume") in events
        assert len(RecordingReader(rec).imu_arrays()["t_host"]) > 100  # the Pico's IMU was recorded
        # the next scan starts from the profile again; shutting down while scanning saves it, emitters off
        st.start_scan(pid)
        assert _last_emit(sp) == "EMIT 100 100 100"
        _wait(lambda: st.session is not None and st.session.n_recorded > 5, what="second scan frames")
        sid = st.session.scan_id
        st.shutdown()
        assert _last_emit(sp) == "EMIT 0 0 0" and all(d == 0 for d in sp.duties())
        assert st.projects.get_scan(pid, sid)["status"] == "recorded" and cam.closed
    finally:
        st.shutdown()
        sp.close()


def test_camera_without_pico_connects_with_a_warning(tmp_path, sim_rec):
    st, _ = _studio(tmp_path, sim_rec, pico=None)
    try:
        code, dev = st.connect("camera")
        assert code == 200 and dev["connected"] and dev["pico"]["connected"] is False and dev["pico"]["firmware"] is None
        assert any("No Pico" in w and "firmware" in w for w in dev["warnings"])
        assert dev["imu_stream"] is False and dev["pico"]["error"]
        assert st.set_emitters({"vertical": 30})["pico"]["emitters"]["vertical"] == 0.0  # ignored, no error
        pid = st.projects.create("No Pico")["id"]
        st.start_scan(pid)
        assert st.session.use_imu is False
        _wait(lambda: st.session.n_processed > 2, what="processed frames")
        assert st.stop_scan()["scan"]["n_frames"] > 0
        with pytest.raises(ApiError) as e:
            st.connect("usb")
        assert e.value.status == 400 and e.value.hint
    finally:
        st.shutdown()


def test_camera_loss_stops_and_saves_the_scan(tmp_path, sim_rec):
    sp = SimPico()
    st, cam = _studio(tmp_path, sim_rec, pico=sp, die_after=120)  # ~4 s at 30 fps: the scan is running by then
    try:
        st.connect("camera")
        pid = st.projects.create("Unplugged")["id"]
        sid = st.start_scan(pid)["scan_id"]
        assert _last_emit(sp) == "EMIT 100 100 100"
        _wait(lambda: not st.devices.connected, timeout=20, what="the device to drop")
        _wait(lambda: st.session is None, timeout=10, what="the scan to stop")
        scan = st.projects.get_scan(pid, sid)
        assert scan["status"] == "recorded" and scan["n_frames"] > 0
        assert _last_emit(sp) == "EMIT 0 0 0" and "camera" in (st.devices.last_error or "")
        assert st.devices.state()["error"] and st.session_state()["state"] == "idle"
    finally:
        st.shutdown()
        sp.close()


def test_trigger_short_and_long_presses(tmp_path, sim_rec):
    sp = SimPico()
    st, _ = _studio(tmp_path, sim_rec, pico=sp)
    try:
        st.connect("camera")
        pid = st.projects.create("Trigger")["id"]
        sp.press(0.08)  # no project used yet: only a hint
        time.sleep(0.4)
        assert st.session is None
        st.last_project = pid
        sp.press(0.08)  # short: start in the last project
        _wait(lambda: st.session is not None and st.session.state == "scanning", what="trigger start")
        _wait(lambda: st.session.n_recorded > 5, what="frames")
        sp.press(0.08)  # short: pause
        _wait(lambda: st.session is not None and st.session.state == "paused", what="trigger pause")
        sp.press(0.08)  # short: resume
        _wait(lambda: st.session is not None and st.session.state == "scanning", what="trigger resume")
        sp.press(1.2)  # long: stop
        _wait(lambda: st.session is None, what="trigger stop")
        scans = st.projects.get(pid)["scans"]
        assert len(scans) == 1 and scans[0]["status"] == "recorded" and _last_emit(sp) == "EMIT 0 0 0"
    finally:
        st.shutdown()
        sp.close()


def test_replay_feed_tiles_loop_passes(sim_rec):
    reader = RecordingReader(sim_rec)
    times = reader.times()
    feed = ReplayFeed(reader, loop=True)
    span = feed.span
    assert span == pytest.approx(times[-1] - times[0] + np.median(np.diff(times)))
    per_pass = len(feed.imu_t)
    assert per_pass > 0 and feed.imu_t.min() >= times[0] and feed.imu_t.max() < times[0] + span
    samples, events = feed.take(times[0] + 3 * span - 1e-9)
    t = np.array([s.t_host for s in samples])
    assert len(t) == 3 * per_pass and np.all(np.diff(t) > 0)  # three passes, no overlap, monotonic
    assert events == []  # the trigger events of a sim recording lie outside the looped period
    more, _ = feed.take(times[0] + 3 * span + 0.01)
    assert all(s.t_host > t[-1] for s in more)
    once = ReplayFeed(reader, loop=False)
    s1, e1 = once.take(1e12)
    assert len(s1) == len(reader.imu_arrays()["t_host"]) and [e.value for e in e1] == ["down", "up"]
    assert once.take(1e12) == ([], [])
