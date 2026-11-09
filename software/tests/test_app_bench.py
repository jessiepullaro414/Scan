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


def _check_run(run: dict, quality: str, device: str) -> None:
    assert run["quality"] == quality and run["device"] == device
    assert run["frames"] == 6 and run["tracked"] >= 5
    t = run["timings_ms"]
    assert {"rectify", "depth", "track", "total"} <= set(t)
    assert all(t[k]["n"] == 6 for k in ("rectify", "depth", "track", "total"))
    assert all(s["n"] >= 5 and s["mean"] >= 0 and s["median"] >= 0 for s in t.values())  # no ICP on frame 0
    assert run["fps"] == pytest.approx(1000.0 / t["total"]["mean"])
    assert run["trajectory"]["n"] >= 5 and run["trajectory"]["trans_mm"]["max"] < 5.0
    de = run["depth_error"]
    assert de["frames"] == 2 and 0 < de["median_mm"] < 2.0 and 0.5 < de["valid_fraction"] <= 1.0
    assert run["image_size"] == ([640, 400] if quality == "full" else [320, 200])


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
