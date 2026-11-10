"""``ohs`` CLI: parsing and help, printables, sim, devices, process, calibrate --from, selftest.

F2's modules (``ohs.app.pipeline`` / ``process`` / ``calib_flow``) are replaced by stubs here;
test_app_live.py has the smoke test against the real ones.
"""
from __future__ import annotations

import io
import threading
import time
import types
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

from ohs.app import capture, cli, selftest
from ohs.devices.pico import Pico
from ohs.io.recording import RecordingReader, RecordingWriter
from ohs.sim.nominal import nominal_calibration
from ohs.types import ImuCalibration, LaserPlane, ScannerCalibration, StereoFrame, TrackResult
from test_app_live import FakeCamera, SimPico, make_stub_pipeline, stub_modules


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OHS_CONFIG", str(tmp_path / "home" / "no-config.toml"))


def _tiny_recording(path: Path, calib: ScannerCalibration | None = None, n: int = 3, meta: dict | None = None) -> Path:
    img = np.zeros((100, 160), np.uint8)
    with RecordingWriter(path, source="camera", **(meta or {})) as w:
        if calib is not None:
            w.write_calibration(calib)
        for i in range(n):
            w.write_frame(StereoFrame(i, 1.0 + i / 30, img, img))
    return path


# --------------------------------------------------------------------------- parsing and help
@pytest.mark.parametrize("command", cli.COMMANDS)
def test_help_for_every_command(command, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main([command, "--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith(f"usage: ohs {command}") and "--config PATH" in out and "--calib PATH" in out


def test_top_level_help_version_and_no_command(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert all(c in out for c in cli.COMMANDS) and "ohs sim demo" in out
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "ohs 0." in capsys.readouterr().out
    assert cli.main([]) == 2


def test_help_texts_carry_the_guidance(capsys):
    for cmd, needles in {
        "calibrate": ["15-25 views, 200-500 mm, tilts 20-45 deg", "6-10 poses, 150-450 mm", "hold still 2 s", "--keep"],
        "preview": ["1/2/3", "+/- exposure", "swap L/R", "q quit"],
        "printables": ["100%", "100 mm scale bar", "--mask-dpi", "--mask-dot-um", "--no-mask"],
        "scan": ["--mode", "--replay", "--no-pico", "--no-gui", "--max-frames", "--process", "200-350 mm"],
        "selftest": ["--seconds", "PASS/WARN/FAIL", "Class 3R"],
    }.items():
        with pytest.raises(SystemExit):
            cli.main([cmd, "--help"])
        out = " ".join(capsys.readouterr().out.split())
        for n in needles:
            assert " ".join(n.split()) in out, (cmd, n)


def test_parse_every_command():
    p = cli.build_parser()
    a = p.parse_args(["scan", "--mode", "markers", "--out", "o", "--process", "--replay", "r", "--no-pico", "--no-gui",
                      "--max-frames", "5", "--seconds", "2.5", "--start-now", "--calib", "c.json"])
    assert (a.mode, a.out, a.process, a.replay, a.no_pico, a.no_gui, a.max_frames, a.seconds, a.start_now, a.calib) == (
        "markers", "o", True, "r", True, True, 5, 2.5, True, "c.json")
    a = p.parse_args(["--config", "x.toml", "-v", "process", "rec", "--mode", "markerless", "--out", "o", "--voxel", "0.5",
                      "--step", "2", "--poisson", "--start", "3", "--stop", "9"])
    assert (a.config, a.verbose, a.recording, a.voxel, a.step, a.poisson, a.start, a.stop) == ("x.toml", 1, "rec", 0.5, 2, True, 3, 9)
    a = p.parse_args(["sim", "out", "--scene", "car_panel", "--frames", "3", "--size", "320x200", "--seed", "4", "--realistic"])
    assert (a.scene, a.frames, a.size, a.seed, a.realistic, a.gt_depth_every) == ("car_panel", 3, (320, 200), 4, True, 0)
    a = p.parse_args(["calibrate", "imu", "--from", "rec", "--keep", "--views", "6", "--seconds", "40", "--step", "2", "--no-save"])
    assert (a.kind, a.from_rec, a.keep, a.views, a.seconds, a.step, a.no_save) == ("imu", "rec", True, 6, 40.0, 2, True)
    a = p.parse_args(["record", "out", "--profile", "lines", "--replay", "r", "--max-frames", "9"])
    assert (a.out, a.profile, a.replay, a.max_frames) == ("out", "lines", "r", 9)
    a = p.parse_args(["preview", "--raw", "--depth", "--no-pico"])
    assert a.raw and a.depth and a.no_pico and a.replay is None
    a = p.parse_args(["printables", "out", "--paper", "a4", "--dpi", "150", "--tag-mm", "25", "--tags", "12",
                      "--mask-dpi", "600", "--mask-dot-um", "50", "--no-mask"])
    assert (a.paper, a.dpi, a.tag_mm, a.tags, a.mask_dpi, a.mask_dot_um, a.no_mask) == ("a4", 150, 25.0, 12, 600, 50.0, True)
    a = p.parse_args(["printables", "out"])
    assert (a.mask_dpi, a.mask_dot_um, a.no_mask) == (1200, 42.0, False)
    a = p.parse_args(["selftest", "--seconds", "1", "--emitter-seconds", "0.5", "--no-emitters"])
    assert (a.seconds, a.emitter_seconds, a.no_emitters, a.no_camera, a.no_pico) == (1.0, 0.5, True, False, False)
    a = p.parse_args(["devices", "--max-index", "3", "--backend", "msmf"])
    assert (a.max_index, a.backend) == (3, "msmf")
    a = p.parse_args(["config", "--show"])
    assert a.show and not a.init and a.config is None and a.verbose == 0


@pytest.mark.parametrize("argv", [
    ["sim", "out", "--size", "320"], ["sim", "out", "--size", "31x20"], ["sim", "out", "--frames", "0"],
    ["calibrate", "foo"], ["config"], ["config", "--init", "--show"], ["scan", "--mode", "fast"], ["process"],
    ["scan", "--max-frames", "-1"], ["printables"], ["process", "rec", "--voxel", "0"],
])
def test_parse_errors_exit_2(argv, capsys):
    with pytest.raises(SystemExit) as e:
        cli.build_parser().parse_args(argv)
    assert e.value.code == 2


# --------------------------------------------------------------------------- printables / sim / devices
@pytest.fixture
def mask_stub(monkeypatch):
    import ohs.projector.mask as mask

    calls: list = []

    def write_mask_files(out_dir, spec=None, dpi=1200, paper="letter", film_spec=None):
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        files = {k: out / n for k, n in (("sheet_png", "projector_mask_sheet.png"), ("sheet_pdf", "projector_mask_sheet.pdf"),
                                         ("gerber", "projector_mask_film.gbr"), ("svg", "projector_mask_film.svg"))}
        for f in files.values():
            f.write_text("x")
        calls.append({"out_dir": out, "spec": spec, "dpi": dpi, "paper": paper, "film_spec": film_spec})
        return {**files, "n_dots": 2072, "fill": 0.155, "fov_deg": (75.8, 60.5), "dot_mm_at_250": 2.87,
                "film_dot_mm_at_250": 1.71}

    monkeypatch.setattr(mask, "write_mask_files", write_mask_files)
    return calls


def test_printables_writes_board_tags_and_mask(tmp_path, mask_stub, capsys):
    out = tmp_path / "print"
    assert cli.main(["printables", str(out), "--dpi", "100", "--tags", "6"]) == 0
    for name in ("charuco_board.pdf", "charuco_board.png", "apriltags.pdf", "apriltags.png"):
        assert (out / name).exists() and (out / name).stat().st_size > 1000, name
    (call,) = mask_stub
    assert call["out_dir"] == out / "projector_mask" and call["dpi"] == 1200 and call["paper"] == "letter"
    assert call["spec"].dot_um == 42.0 and call["film_spec"] is None  # the film mask keeps the module's default
    text = capsys.readouterr().out
    assert "Print at 100% / actual size" in text and "100 mm scale bar" in text and "glass" in text
    assert ("LED projector mask: print projector_mask_sheet.pdf at 100% on laser transparency film (1200 dpi), "
            "or send projector_mask_film.gbr to a photoplot service") in text
    assert "2072 dots" in text and "76 x 60 deg" in text and "film mask: ~1.7 mm" in text


def test_printables_mask_options(tmp_path, mask_stub, capsys):
    assert cli.main(["printables", str(tmp_path / "a"), "--dpi", "100", "--tags", "2", "--no-mask"]) == 0
    assert mask_stub == [] and "LED projector mask" not in capsys.readouterr().out
    assert cli.main(["printables", str(tmp_path / "b"), "--dpi", "100", "--tags", "2", "--paper", "a4", "--mask-dpi", "600",
                     "--mask-dot-um", "50"]) == 0
    (call,) = mask_stub
    assert (call["dpi"], call["paper"], call["spec"].dot_um) == (600, "a4", 50.0)
    assert "transparency film (600 dpi)" in capsys.readouterr().out


@pytest.mark.slow
def test_printables_real_mask_generator(tmp_path):
    """The real LED mask generator through the CLI (~20 s: 48 Poisson-disk masks, independent of dpi)."""
    out = tmp_path / "p"
    assert cli.main(["printables", str(out), "--dpi", "100", "--tags", "2", "--mask-dpi", "300"]) == 0
    folder = out / "projector_mask"
    assert (folder / "projector_mask_sheet.pdf").stat().st_size > 0 and (folder / "projector_mask_sheet.png").exists()
    for pattern in ("*.gbr", "*.svg"):
        assert [p for p in folder.glob(pattern) if p.stat().st_size > 0], pattern


def test_sim_makes_a_tiny_recording(tmp_path, capsys):
    out = tmp_path / "sim"
    assert cli.main(["sim", str(out), "--frames", "3", "--size", "160x100", "--scene", "bracket"]) == 0
    r = RecordingReader(out)
    assert len(r) == 3 and r.meta["source"] == "sim" and r.meta["scene"] == "bracket" and r.meta["image_size"] == [160, 100]
    assert r.calibration() is not None and len(r.imu_arrays()["t_host"]) > 0
    assert not (out / "ground_truth" / "depth").exists()  # --gt-depth-every 0 by default
    assert "ohs process" in capsys.readouterr().out
    assert cli.main(["sim", str(out), "--frames", "1", "--size", "160x100"]) == 1
    assert "not empty" in capsys.readouterr().err


def test_devices_lists_cameras_and_pico(monkeypatch, capsys):
    import ohs.devices.camera as cam
    import ohs.devices.pico as pico
    from serial.tools import list_ports

    seen: dict = {}

    def fake_list(max_index=8, backend="auto"):
        seen.update(max_index=max_index, backend=backend)
        return [{"index": 0, "backend": "DSHOW", "width": 2560, "height": 800, "fps": 60.0, "fourcc": "MJPG"},
                {"index": 1, "backend": "DSHOW", "width": 640, "height": 480, "fps": 30.0, "fourcc": "YUY2"}]

    monkeypatch.setattr(cam, "list_cameras", fake_list)
    monkeypatch.setattr(pico, "find_pico_port", lambda: "COM7")
    monkeypatch.setattr(list_ports, "comports", lambda: [])
    assert cli.main(["devices", "--max-index", "2", "--backend", "dshow"]) == 0
    out = capsys.readouterr().out
    assert seen == {"max_index": 2, "backend": "dshow"}
    assert "0: DSHOW 2560x800 @ 60 fps MJPG  <- side-by-side stereo?  (camera.index)" in out and "Pico: COM7" in out
    monkeypatch.setattr(cam, "list_cameras", lambda max_index=8, backend="auto": [])
    monkeypatch.setattr(pico, "find_pico_port", lambda: None)
    assert cli.main(["devices"]) == 0
    out = capsys.readouterr().out
    assert "none found" in out and "firmware/README.md" in out


# --------------------------------------------------------------------------- process (stubbed)
def _process_stub(calls: list) -> types.ModuleType:
    mod = types.ModuleType("ohs.app.process")

    @dataclass
    class ProcessParams:
        pipeline: object = None
        frame_step: int = 1
        start: int = 0
        stop: int | None = None
        export: tuple = ("ply", "stl")
        poisson: bool = False
        clean_min_component: float = 0.01
        frame_motion_deg: float = 0.0
        frame_max_gap: int = 4
        max_frame_rate: float = 0.0

    def process_recording(rec_path, out_dir, calib=None, params=None, progress=None):
        calls.append({"rec": Path(rec_path), "out": Path(out_dir), "calib": calib, "params": params})
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        for k in range(4):
            progress(k, 4, types.SimpleNamespace(track=TrackResult(np.eye(4), k != 2, {"reason": "jump"})))
        files = {n: str(out / n) for n in ("mesh.ply", "mesh.stl", "laser_points.ply", "trajectory.csv", "report.json")}
        for f in files.values():
            Path(f).write_text("x")
        return {"frames": {"processed": 4, "tracked": 3, "tracked_fraction": 0.75}, "failures": {"jump": 1},
                "mesh": {"n_vertices": 10, "n_faces": 12, "method": "tsdf", "voxel_mm": 1.0}, "files": files,
                "lasers": {"n_points_exported": 99}}

    mod.ProcessParams, mod.process_recording = ProcessParams, process_recording
    return mod


def test_process_calls_process_recording(tmp_path, capsys):
    calls: list = []
    rec = _tiny_recording(tmp_path / "rec", nominal_calibration((160, 100)), meta={"mode": "markers"})
    with stub_modules(pipeline=make_stub_pipeline(), process=_process_stub(calls)):
        assert cli.main(["process", str(rec)]) == 0
        assert cli.main(["process", str(rec), "--mode", "markerless", "--voxel", "0.5", "--step", "2", "--poisson",
                         "--out", str(tmp_path / "o2"), "--start", "1", "--stop", "3"]) == 0
    first, second = calls
    assert first["rec"] == rec and first["out"] == rec / "result" and first["calib"] is None  # the recording's own calibration
    p = first["params"]
    assert p.pipeline.mode == "markers" and p.pipeline.scale == 1.0 and p.pipeline.fusion is None  # mode from the meta
    assert p.frame_step == 1 and p.export == ("ply", "stl") and p.poisson is False and p.pipeline.markers.tag_mm == 20.0
    p = second["params"]
    assert (p.pipeline.mode, p.pipeline.fusion.voxel_mm, p.frame_step, p.poisson, p.start, p.stop) == ("markerless", 0.5, 2, True, 1, 3)
    cap = capsys.readouterr()
    assert "mesh.stl" in cap.out and "frames: 4 processed, 3 tracked (75%)" in cap.out and "laser points: 99" in cap.out
    assert "4/4 100%" in cap.err and "lost: jump" in cap.err  # progress bar, zero-based k shown as k + 1


def test_process_frames_by_motion_options(tmp_path, capsys):
    a = cli.build_parser().parse_args(["process", "rec", "--motion", "0.5", "--max-gap", "6"])
    assert (a.motion, a.max_gap) == (0.5, 6)
    for bad in (["--motion", "-1"], ["--max-gap", "0"]):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["process", "rec", *bad])
    capsys.readouterr()
    calls: list = []
    rec = _tiny_recording(tmp_path / "rec", nominal_calibration((160, 100)))
    cfg = tmp_path / "c.toml"
    cfg.write_text("[process]\nframe_motion_deg = 0.4\nframe_max_gap = 5\n", encoding="utf-8")
    with stub_modules(pipeline=make_stub_pipeline(), process=_process_stub(calls)):
        assert cli.main(["process", str(rec)]) == 0  # defaults: off
        assert cli.main(["--config", str(cfg), "process", str(rec)]) == 0  # from the config
        assert cli.main(["--config", str(cfg), "process", str(rec), "--motion", "0"]) == 0  # the flag turns it off
        assert cli.main(["process", str(rec), "--motion", "0.8", "--max-gap", "3", "--step", "2"]) == 0
    got = [(c["params"].frame_motion_deg, c["params"].frame_max_gap, c["params"].frame_step) for c in calls]
    assert got == [(0.0, 4, 1), (0.4, 5, 1), (0.0, 5, 1), (0.8, 3, 2)]
    assert "frames picked by motion: 0.8 deg or every 3. frame, at least every 2." in capsys.readouterr().out


def test_process_quality_option(tmp_path, capsys):
    p = cli.build_parser()
    assert p.parse_args(["process", "rec"]).quality == "standard"  # the default follows the app
    assert p.parse_args(["process", "rec", "--quality", "best"]).quality == "best"
    with pytest.raises(SystemExit):
        p.parse_args(["process", "rec", "--quality", "full"])
    capsys.readouterr()
    rec = tmp_path / "rec60"
    img = np.zeros((100, 160), np.uint8)
    with RecordingWriter(rec, source="camera") as w:
        w.write_calibration(nominal_calibration((160, 100)))
        for i in range(6):
            w.write_frame(StereoFrame(i, 1.0 + i / 60, img, img))
    calls: list = []
    with stub_modules(pipeline=make_stub_pipeline(), process=_process_stub(calls)):
        assert cli.main(["process", str(rec)]) == 0
        out_std = capsys.readouterr().out
        assert cli.main(["process", str(rec), "--quality", "best", "--out", str(tmp_path / "o2")]) == 0
        out_best = capsys.readouterr().out
        assert cli.main(["process", str(rec), "--step", "3", "--out", str(tmp_path / "o3")]) == 0
        out_step = capsys.readouterr().out
    assert [(c["params"].max_frame_rate, c["params"].frame_step) for c in calls] == [(30.0, 1), (0.0, 1), (30.0, 3)]
    assert "standard quality" in out_std and "every 2nd frame (60 fps recording, 30 a second)" in out_std
    assert "best quality" in out_best and "every frame (60 fps recording)" in out_best
    assert "every 3rd frame (60 fps recording, 20 a second)" in out_step  # --step still sets the smallest step


def test_process_needs_a_calibration(tmp_path, capsys):
    rec = _tiny_recording(tmp_path / "rec")  # no calibration.json inside
    calib = tmp_path / "c.json"
    nominal_calibration((160, 100)).save(calib)
    calls: list = []
    with stub_modules(pipeline=make_stub_pipeline(), process=_process_stub(calls)):
        assert cli.main(["process", str(rec)]) == 1
        assert "pass --calib" in capsys.readouterr().err and calls == []
        assert cli.main(["--calib", str(calib), "process", str(rec)]) == 0
    assert isinstance(calls[0]["calib"], ScannerCalibration)
    assert cli.main(["process", str(tmp_path / "nope")]) == 1
    assert "not a recording" in capsys.readouterr().err


# --------------------------------------------------------------------------- calibrate --from (stubbed flows)
def _flow_stub(log: list, *, stereo=None, planes=None, imu=None, check=None) -> types.ModuleType:
    mod = types.ModuleType("ohs.app.calib_flow")

    def stereo_from_recording(rec_path, spec, step=1, progress=None):
        log.append(("stereo", Path(rec_path), spec, step))
        if progress is not None:
            progress(0, 2)
            progress(1, 2)
        return stereo, {"rms": 0.21, "quality_ok": True, "verdict": "good",
                        "per_view_errors": [{"pair": i, "left": 0.2, "right": 0.2} for i in range(20)]}

    def lasers_from_recording(rec_path, calib, spec, paper="letter"):
        log.append(("lasers", Path(rec_path), calib, paper))
        return planes, {"ok": True, "lasers": {n: {"rms_mm": 0.05} for n in planes}}

    def imu_from_recording(rec_path, calib, spec):
        log.append(("imu", Path(rec_path), calib))
        return imu, {"time_offset_s": imu.time_offset, "verdict": "good"}

    def check_from_recording(rec_path, calib, spec):
        log.append(("check", Path(rec_path), calib))
        return check

    mod.stereo_from_recording = stereo_from_recording
    mod.lasers_from_recording = lasers_from_recording
    mod.imu_from_recording = imu_from_recording
    mod.check_from_recording = check_from_recording
    return mod


@pytest.fixture
def calibrated(tmp_path):
    """A calibration file with stereo (60 mm), both laser planes and the IMU, plus a board recording."""
    path = tmp_path / "home" / "calibration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    nominal_calibration((160, 100)).save(path)
    return path, _tiny_recording(tmp_path / "board", meta={"purpose": "calib_stereo"})


def test_calibrate_stereo_from_drops_lasers_and_imu(tmp_path, calibrated, capsys):
    path, rec = calibrated
    log: list = []
    new = nominal_calibration((160, 100), baseline=63.0).stereo
    with stub_modules(calib_flow=_flow_stub(log, stereo=new)):
        assert cli.main(["--calib", str(path), "calibrate", "stereo", "--from", str(rec), "--step", "3"]) == 0
    (_, rec_seen, spec, step), = log
    assert rec_seen == rec and step == 3 and spec.squares_x == 11
    c = ScannerCalibration.load(path)
    assert c.stereo.baseline == pytest.approx(63.0) and c.lasers == {} and c.imu is None
    old = ScannerCalibration.load(path.with_name(path.name + ".bak"))
    assert old.stereo.baseline == pytest.approx(60.0) and set(old.lasers) == {"vertical", "horizontal"}
    cap = capsys.readouterr()
    assert "invalidates the laser planes and the IMU calibration: dropped" in cap.err and "--keep" in cap.err
    assert "verdict: good" in cap.out and "per_view_errors: 20 entries" in cap.out and "next: ohs calibrate lasers" in cap.out
    assert (rec / "calib_report_stereo.json").exists()


def test_calibrate_stereo_keep_and_no_save(tmp_path, calibrated, capsys):
    path, rec = calibrated
    new = nominal_calibration((160, 100), baseline=62.0).stereo
    with stub_modules(calib_flow=_flow_stub([], stereo=new)):
        assert cli.main(["--calib", str(path), "calibrate", "stereo", "--from", str(rec), "--keep"]) == 0
        c = ScannerCalibration.load(path)
        assert c.stereo.baseline == pytest.approx(62.0) and set(c.lasers) == {"vertical", "horizontal"} and c.imu is not None
        before = path.read_text(encoding="utf-8")
        assert cli.main(["--calib", str(path), "calibrate", "stereo", "--from", str(rec), "--no-save"]) == 0
    assert path.read_text(encoding="utf-8") == before and "--no-save" in capsys.readouterr().out


def test_calibrate_lasers_imu_and_check_from(tmp_path, calibrated, capsys):
    path, rec = calibrated
    old = ScannerCalibration.load(path)
    vert = LaserPlane("vertical", np.array([0.95, 0.0, 0.31]), -110.0, rms=0.04, n_points=900)
    log: list = []
    flows = _flow_stub(log, planes={"vertical": vert}, imu=ImuCalibration(np.eye(3), 0.042, np.zeros(3)),
                       check={"ok": True, "verdict": "good"})
    cfg = tmp_path / "c.toml"
    cfg.write_text('[board]\npaper = "a4"\n', encoding="utf-8")
    with stub_modules(calib_flow=flows):
        assert cli.main(["--config", str(cfg), "--calib", str(path), "calibrate", "lasers", "--from", str(rec)]) == 0
        c = ScannerCalibration.load(path)
        assert c.lasers["vertical"].d == pytest.approx(vert.d) and vert.d != pytest.approx(old.lasers["vertical"].d)
        assert c.lasers["horizontal"].d == pytest.approx(old.lasers["horizontal"].d)  # not fitted: kept
        assert np.allclose(c.stereo.P1, old.stereo.P1) and c.imu is not None
        assert log[-1][0] == "lasers" and log[-1][3] == "a4"  # the printed paper size reaches the flow
        assert "horizontal plane was not fitted: kept the previous one" in capsys.readouterr().err

        assert cli.main(["--calib", str(path), "calibrate", "imu", "--from", str(rec)]) == 0
        c = ScannerCalibration.load(path)
        assert c.imu.time_offset == pytest.approx(0.042) and c.lasers["vertical"].d == pytest.approx(vert.d)

        before = path.read_text(encoding="utf-8")
        assert cli.main(["--calib", str(path), "calibrate", "check", "--from", str(rec)]) == 0
        flows.check_from_recording = lambda rec_path, calib, spec: {"ok": False, "verdict": "stereo: scale off by 2%"}
        assert cli.main(["--calib", str(path), "calibrate", "check", "--from", str(rec)]) == 1
        assert path.read_text(encoding="utf-8") == before  # check never writes
    assert "the calibration check failed" in capsys.readouterr().err


def test_calibrate_needs_stereo_first_and_a_recording(tmp_path, capsys):
    rec = _tiny_recording(tmp_path / "rec")
    assert cli.main(["--calib", str(tmp_path / "none.json"), "calibrate", "lasers", "--from", str(rec)]) == 1
    assert "run `ohs calibrate stereo` first" in capsys.readouterr().err
    assert cli.main(["--calib", str(tmp_path / "none.json"), "calibrate", "stereo", "--from", str(tmp_path / "x")]) == 1
    assert "not a recording" in capsys.readouterr().err


def test_merge_calibration_rules():
    base = nominal_calibration((160, 100))
    new_st = nominal_calibration((160, 100), baseline=64.0).stereo
    c, notes = capture.merge_calibration(None, "stereo", new_st)
    assert c.lasers == {} and c.imu is None and notes == []
    c, notes = capture.merge_calibration(base, "stereo", new_st)
    assert c.lasers == {} and c.imu is None and "dropped" in notes[0]
    c, notes = capture.merge_calibration(base, "stereo", new_st, keep=True)
    assert set(c.lasers) == {"vertical", "horizontal"} and c.imu is base.imu and "kept" in notes[0]
    c, notes = capture.merge_calibration(base, "lasers", {})
    assert set(c.lasers) == {"vertical", "horizontal"} and len(notes) == 2
    with pytest.raises(ValueError, match="stereo calibration first"):
        capture.merge_calibration(None, "imu", ImuCalibration())
    text = capture.format_report({"verdict": "good", "rms": 0.123456, "nested": {"a": [1, 2, 3]}, "ok": np.bool_(True)})
    assert text.splitlines()[0].strip() == "verdict: good" and "rms: 0.1235" in text and "a: [1, 2, 3]" in text


# --------------------------------------------------------------------------- selftest
def test_selftest_with_fake_camera_and_pico(tmp_path, monkeypatch, capsys):
    sim = SimPico()
    cam = FakeCamera(size=(160, 100), fps=60, brightness=lambda: sum(d or 0 for d in sim.duties()) * 40 // 65535)
    monkeypatch.setattr(selftest, "StereoCamera", lambda params, **kw: cam)
    monkeypatch.setattr(selftest, "Pico", lambda params: sim.pico(params))
    stop = threading.Event()

    def presser() -> None:
        while not stop.is_set():
            time.sleep(0.15)
            sim.press(0.06)

    th = threading.Thread(target=presser, daemon=True)
    th.start()
    cfg = tmp_path / "c.toml"
    cfg.write_text("[camera]\nwidth = 320\nheight = 100\nfps = 60\n", encoding="utf-8")
    try:
        rc = cli.main(["--config", str(cfg), "selftest", "--seconds", "0.6", "--emitter-seconds", "0.3"])
    finally:
        stop.set()
        th.join(2)
        sim.close()
    out = capsys.readouterr().out
    summary = out[out.index("Self-test summary"):]
    assert rc == 0, out
    for line in ("PASS  camera ", "PASS  pico ", "PASS  imu values", "PASS  trigger",
                 "PASS  vertical line laser", "PASS  horizontal line laser", "PASS  dot projector"):
        assert line in summary, line
    # rates and the clock round trip depend on the machine's load: PASS or WARN, never FAIL
    assert "FAIL" not in summary and "imu rate" in summary and "camera fps" in summary and "clock sync" in summary
    assert "confirm you saw it" in summary and "Class 3R" in out
    assert sim.duties() == [0, 0, 0] and cam.closed  # everything off and closed afterwards
    assert [e for e in sim.emits() if e != "EMIT 0 0 0"] == ["EMIT 100 0 0", "EMIT 0 100 0", "EMIT 0 0 100"]
    assert sim.emits()[-1] == "EMIT 0 0 0"


def test_selftest_reports_missing_devices(monkeypatch, capsys):
    class DeadCamera:
        def open(self):
            raise RuntimeError("could not open camera 0 (backends tried: DSHOW, MSMF)")

        def close(self):
            pass

    class DeadPico:
        def open(self):
            raise ConnectionError("no Pico found (USB VID 0x2E8A); set PicoParams.port")

        def close(self):
            pass

    monkeypatch.setattr(selftest, "StereoCamera", lambda params, **kw: DeadCamera())
    monkeypatch.setattr(selftest, "Pico", lambda params: DeadPico())
    assert cli.main(["selftest", "--seconds", "0.2"]) == 1
    out = capsys.readouterr().out
    assert "FAIL  camera" in out and "FAIL  pico" in out and "Overall: FAIL" in out
    assert cli.main(["selftest", "--no-camera", "--no-pico"]) == 0
    assert "SKIP  camera" in capsys.readouterr().out


# --------------------------------------------------------------------------- progress bar
def test_progress_bar_accepts_the_common_callback_shapes():
    s = io.StringIO()
    bar = cli.ProgressBar("work", stream=s)
    for k in range(10):
        bar(k, 10)
    bar.close()
    lines = s.getvalue().splitlines()
    assert lines[-1].startswith("work [") and "10/10 100%" in lines[-1] and len(lines) <= 11
    s = io.StringIO()
    bar = cli.ProgressBar("x", stream=s, zero_based=False)
    bar(0.5)
    bar({"done": 3, "total": 4, "message": "hi"})
    bar(done=4, total=4)
    bar("garbage")  # ignored
    out = s.getvalue()
    assert "50/100" in out and "3/4" in out and "hi" in out and "4/4 100%" in out
