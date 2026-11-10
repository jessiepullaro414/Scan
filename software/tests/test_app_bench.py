"""``ohs bench`` (ohs.app.bench): the cached recording, the runs, the report and the CLI command.

Everything runs on a tiny bench recording (640x400, 6 frames, true depth every 3rd frame) in a
temporary bench folder. The GPU runs happen only when ``ohs.compute`` finds a GPU; the CPU-only
behaviour is checked with ``OHS_COMPUTE=cpu``.
"""
from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("open3d")

from ohs import compute  # noqa: E402
from ohs.app import bench, cli  # noqa: E402

TINY = bench.BenchSpec(n_frames=6, image_size=(640, 400), depth_every=3)


@pytest.fixture(scope="module")
def bench_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("bench")
    bench.ensure_recording(TINY, root)
    return root


@pytest.fixture(autouse=True)
def _restore_compute():
    yield
    compute.reset()


@pytest.fixture(autouse=True)
def _no_engine_start(monkeypatch):
    """run_bench starts the app in a new process once; only test_engine_start does that for real."""
    monkeypatch.setattr(bench, "measure_engine_start",
                        lambda *a, **k: {"ok": True, "listening_s": 0.5, "first_status_s": 0.6, "port": 1})


def _check_run(run: dict, quality: str, device: str) -> None:
    assert run["quality"] == quality and run["device"] == device
    assert run["frames"] == 6 and run["tracked"] >= 5
    t = run["timings_ms"]
    assert {"rectify", "depth", "track", "total"} <= set(t)
    assert all(t[k]["n"] == 6 for k in ("rectify", "depth", "track", "total"))
    assert all(s["n"] >= 5 and s["mean"] >= 0 and s["median"] >= 0 for s in t.values())  # no ICP on frame 0
    assert run["fps"] == pytest.approx(1000.0 / t["total"]["mean"])
    assert run["scan_fps"] == bench.CAMERA_FPS
    assert run["wait_per_minute_min"] == pytest.approx(t["total"]["mean"] * bench.CAMERA_FPS / 1000.0)
    assert run["trajectory"]["n"] >= 5 and run["trajectory"]["trans_mm"]["max"] < 5.0
    de = run["depth_error"]
    assert de["frames"] == 2 and 0 < de["median_mm"] < 2.0 and 0.5 < de["valid_fraction"] <= 1.0
    assert run["image_size"] == ([640, 400] if quality == "full" else [320, 200])


def test_wait_per_minute_of_scanning_arithmetic():
    from ohs.devices.camera import CameraParams

    assert bench.CAMERA_FPS == CameraParams().fps == 60.0
    # 60 s of recording = 3600 frames; at 155 ms per frame that is 558 s = 9.3 min
    assert bench.wait_per_minute_min(155.0, 60.0) == pytest.approx(9.3)
    assert bench.wait_per_minute_min(1000.0 / 60.0, 60.0) == pytest.approx(1.0)  # real time: one minute per minute
    assert bench.wait_per_minute_min(0.0, 60.0) == 0.0


def test_wait_per_minute_in_report():
    run = {"device": "cpu", "quality": "full", "image_size": [640, 400], "voxel_mm": 1.0, "depth_backend": "cpu",
           "tracking_backend": None, "frames": 6, "tracked": 6, "fps": 1000.0 / 155.0,
           "timings_ms": {"total": {"mean": 155.0, "median": 120.0, "max": 200.0, "n": 6}},
           "trajectory": {}, "depth_error": {}, "scan_fps": 60.0, "wait_per_minute_min": 9.3}
    report = {"bench_version": 1, "recording": {"spec": {"image_size": [640, 400]}, "frames_used": 6},
              "machine": {"cpu": "x", "logical_cpus": 1, "os": "y", "python": "3", "packages": {}},
              "runs": [run], "speedup": {}}
    text = bench.format_report(report)
    assert "CPU: wait per minute of scanning at 60 fps: 9.3 min (mean frame time, every frame processed)" in text


def test_tail_and_engine_start_in_report():
    tail = {"mesh": 500.0, "clean": 100.0, "export_ply": 200.0, "export_stl": 50.0, "laser_points": 150.0,
            "total": 1000.0, "faces": 1234}
    run = {"device": "gpu", "quality": "full", "image_size": [640, 400], "voxel_mm": 1.0, "depth_backend": "gpu",
           "tracking_backend": None, "frames": 6, "tracked": 6, "fps": 10.0,
           "timings_ms": {"total": {"mean": 100.0, "median": 100.0, "max": 100.0, "n": 6}},
           "trajectory": {}, "depth_error": {}, "tail_ms": tail}
    report = {"bench_version": 1, "recording": {"spec": {"image_size": [640, 400]}, "frames_used": 6},
              "machine": {"cpu": "x", "logical_cpus": 1, "os": "y", "python": "3", "packages": {}},
              "runs": [run], "speedup": {},
              "engine_start": {"ok": True, "listening_s": 0.8, "first_status_s": 0.9, "port": 1}}
    text = bench.format_report(report)
    assert "GPU: fixed tail after the last frame: 1.00 s (mesh extraction to files, after 6 frames, 1234 faces" in text
    for line in ("mesh extraction: 0.50 s", "cleaning: 0.10 s", "export ply: 0.20 s", "export stl: 0.05 s",
                 "laser points file: 0.15 s"):
        assert "      " + line in text
    assert "0.80 s until the app accepts a connection, 0.90 s until its first status message" in text
    report["engine_start"] = {"ok": False, "error": "boom"}
    assert "Engine start: not measured. boom" in bench.format_report(report)


def test_tail_of_a_run(bench_dir):
    report = bench.run_bench(frames=4, qualities=("preview", "full"), devices=("cpu",), bench_dir=bench_dir, spec=TINY,
                             engine_start=False)
    assert "engine_start" not in report
    for r in report["runs"]:
        t = r["tail_ms"]
        assert "error" not in t, t
        steps = [k for k in t if k not in ("total", "faces")]
        assert steps == ["mesh", "clean", "export_ply", "export_stl", "laser_points"]  # Poisson is off in both qualities
        assert all(t[k] >= 0 for k in steps) and t["total"] == pytest.approx(sum(t[k] for k in steps))
        assert t["faces"] > 0
    text = bench.format_report(report)
    assert text.count("fixed tail after the last frame:") == 2


def test_tail_keeps_going_when_a_step_fails():
    class Pipe:
        def mesh_with_sigma(self):
            raise RuntimeError("no mesh")

    from types import SimpleNamespace

    t = bench._tail(Pipe(), SimpleNamespace(poisson=False))
    assert t["error"] == "RuntimeError: no mesh" and "mesh" in t and t["total"] == pytest.approx(t["mesh"])


def test_engine_start(monkeypatch, tmp_path):
    monkeypatch.undo()  # the real one: ohs app in a new process, built-in settings, a free port
    r = bench.measure_engine_start(timeout=120)
    assert r["ok"], r
    assert 0 < r["listening_s"] <= r["first_status_s"] and r["port"] > 0
    # failures are reported, not raised
    bad = bench.measure_engine_start(timeout=5, python=str(tmp_path / "no-such-python"))
    assert bad["ok"] is False and bad["error"]


def test_recording_is_cached_and_versioned(bench_dir):
    path = bench.ensure_recording(TINY, bench_dir)
    assert path.parent == bench_dir and (path / "frames.csv").is_file()
    stamp = (path / "frames.csv").stat().st_mtime_ns
    assert bench.ensure_recording(TINY, bench_dir) == path and (path / "frames.csv").stat().st_mtime_ns == stamp
    other = bench.BenchSpec(n_frames=6, image_size=(640, 400), depth_every=3, seed=1)
    assert other.folder_name() != TINY.folder_name() and f"v{bench.BENCH_VERSION}" in TINY.folder_name()
    spec = json.loads((path / "bench_spec.json").read_text(encoding="utf-8"))
    assert spec["bench_version"] == bench.BENCH_VERSION and spec["spec"]["n_frames"] == 6


def test_bench_cpu_and_gpu(bench_dir, capsys):
    env_before = os.environ.get(compute.ENV_VAR)
    report = bench.run_bench(bench_dir=bench_dir, spec=TINY, echo=print)
    assert os.environ.get(compute.ENV_VAR) == env_before  # the CPU runs' OHS_COMPUTE=cpu is undone
    runs = report["runs"]
    has_gpu = compute.get_device() is not None
    assert [(r["device"], r["quality"]) for r in runs] == (
        [("cpu", "full"), ("cpu", "preview"), ("gpu", "full"), ("gpu", "preview")] if has_gpu
        else [("cpu", "full"), ("cpu", "preview")])
    for r in runs:
        _check_run(r, r["quality"], r["device"])
    assert all(r["depth_backend"] == "cpu" for r in runs if r["device"] == "cpu")
    m = report["machine"]
    assert m["cpu"] and m["logical_cpus"] and m["python"] and m["packages"]["ohs"]
    text = bench.format_report(report)
    assert "Full quality (640x400" in text and "Preview quality (320x200" in text and "frames per second" in text
    assert text.count("wait per minute of scanning at 60 fps:") == len(runs)
    if has_gpu:
        assert all(r["depth_backend"] == "gpu" for r in runs if r["device"] == "gpu")
        assert set(report["speedup"]) == {"full", "preview"} and report["speedup"]["full"]["total"] > 0
        assert "GPU speed-up" in text and report["gpu"]["device"]["name"] in text
    else:
        assert "no_gpu_reason" in report and "GPU: none" in text


def test_bench_on_a_cpu_only_machine(bench_dir, monkeypatch):
    monkeypatch.setenv(compute.ENV_VAR, "cpu")
    compute.reset()
    report = bench.run_bench(frames=4, qualities=("preview",), bench_dir=bench_dir, spec=TINY)
    assert [(r["device"], r["quality"], r["frames"]) for r in report["runs"]] == [("cpu", "preview", 4)]
    assert "turned off" in report["no_gpu_reason"] and report["speedup"] == {}
    assert "GPU: none" in bench.format_report(report)
    assert os.environ[compute.ENV_VAR] == "cpu"  # the caller's setting is kept


def test_save_report(bench_dir, tmp_path):
    report = bench.run_bench(frames=3, qualities=("preview",), devices=("cpu",), bench_dir=bench_dir, spec=TINY)
    paths = bench.save_report(report, bench_dir, tmp_path / "out" / "mine.json")
    assert paths[0].parent == bench_dir / "results" and paths[0].name.startswith("bench-") and paths[1].is_file()
    data = json.loads(paths[1].read_text(encoding="utf-8"))
    assert data["bench_version"] == bench.BENCH_VERSION and data["runs"][0]["frames"] == 3
    assert data["recording"]["spec"]["image_size"] == [640, 400]


def test_bad_arguments(bench_dir):
    with pytest.raises(ValueError):
        bench.run_bench(qualities=("ultra",), bench_dir=bench_dir, spec=TINY)
    with pytest.raises(ValueError):
        bench.run_bench(devices=("tpu",), bench_dir=bench_dir, spec=TINY)


# --------------------------------------------------------------------------- CLI
@pytest.fixture
def tiny_standard(monkeypatch, tmp_path):
    monkeypatch.setattr(bench, "STANDARD", TINY)
    monkeypatch.setenv("OHS_CONFIG", str(tmp_path / "no-config.toml"))


def test_cli_bench(tiny_standard, bench_dir, tmp_path, capsys):
    out = tmp_path / "result.json"
    rc = cli.main(["bench", "--frames", "4", "--quality", "preview", "--device", "cpu", "--json", str(out),
                   "--dir", str(bench_dir)])
    text = capsys.readouterr().out
    assert rc == 0
    assert "Preview quality (320x200" in text and "frames per second" in text and "Results saved to" in text
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [(r["device"], r["quality"], r["frames"]) for r in data["runs"]] == [("cpu", "preview", 4)]
    assert any(p.name.startswith("bench-") for p in (bench_dir / "results").iterdir())


def test_cli_bench_gpu_without_gpu(tiny_standard, bench_dir, monkeypatch, capsys):
    monkeypatch.setenv(compute.ENV_VAR, "cpu")
    compute.reset()
    assert cli.main(["bench", "--device", "gpu", "--dir", str(bench_dir)]) == 1
    assert "no GPU to benchmark" in capsys.readouterr().err


def test_cli_bench_parse():
    a = cli.build_parser().parse_args(["bench", "--frames", "10", "--quality", "full", "--device", "gpu", "--json", "x.json",
                                       "--dir", "d"])
    assert (a.frames, a.quality, a.device, a.json, a.dir) == (10, "full", "gpu", "x.json", "d")
    a = cli.build_parser().parse_args(["bench"])
    assert (a.frames, a.quality, a.device, a.json, a.dir) == (None, "both", "both", None, None)
    for bad in (["bench", "--quality", "max"], ["bench", "--device", "tpu"], ["bench", "--frames", "0"]):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(bad)
