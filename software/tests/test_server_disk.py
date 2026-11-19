"""What a project takes on disk (FEATURE_PLAN.md R1; APP_API.md "Disk use of a project").

``GET /api/projects/{pid}/disk`` reports, per scan, the bytes of the recording (and its frame
files), each stage folder under ``process/``, the undo cache (``process/cache`` and
``process/selections``), the result and everything else, plus the project's totals and the
trash. The guard: every number equals the sizes of the files on disk, counted here
independently with ``Path.rglob``, and the parts add up to the folder.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402

TIMEOUT = 30


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("disk")
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        yield {"url": srv.url, "root": root, "store": app.state.studio.projects}


def size_of(d: Path) -> int:
    """Bytes of every file under ``d`` (0 when it does not exist)."""
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file()) if d.is_dir() else 0


def put(path: Path, n: int, seed: int = 0) -> None:
    """A file of exactly ``n`` bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes((seed + i) % 251 for i in range(n)))


def fill_scan(scan: Path, k: int) -> None:
    """A scan folder laid out like a processed one (processing.py "Files"), with odd sizes."""
    for i in range(5 + k):
        put(scan / "recording" / "frames" / f"{i:06d}.png", 1000 + 37 * i + k, seed=i)
    for name, n in (("frames.csv", 211), ("imu.csv", 3001 + k), ("events.csv", 19), ("meta.json", 512),
                    ("calibration.json", 777)):
        put(scan / "recording" / name, n)
    put(scan / "recording" / "ground_truth" / "poses.csv", 333)
    for stage, n in (("track", 9001), ("straighten", 7007 + k)):
        put(scan / "process" / stage / "mesh.ply", n)
        put(scan / "process" / stage / "report.json", 101)
    put(scan / "process" / "state.json", 4900)
    put(scan / "process" / "straighten.json", 215)
    put(scan / "process" / "cache" / "base.npz", 18001)
    put(scan / "process" / "cache" / "e-1.npz", 17003 + k)
    put(scan / "process" / "selections" / "e-1.npy", 208)
    for name, n in (("mesh.ply", 22001), ("mesh.stl", 40003), ("report.json", 1515), ("trajectory.csv", 1289)):
        put(scan / "result" / name, n + k)
    put(scan / "thumbnail.jpg", 6041)
    put(scan / "features.json", 1928)
    put(scan / "features" / "f-1.npy", 15746)


def test_sizes_equal_the_files(env):
    url, store = env["url"], env["store"]
    p = requests.post(url + "/api/projects", json={"name": "Disk use"}, timeout=TIMEOUT).json()
    pid = p["id"]
    a, rec_a = store.add_scan(pid, "A", "markerless")
    b, rec_b = store.add_scan(pid, "B", "markers")
    empty, _ = store.add_scan(pid, "Empty", "markerless")  # registered, nothing written yet
    store.update_scan(pid, a["id"], status="processed", n_frames=5, duration_s=0.5)
    store.update_scan(pid, b["id"], status="processed", n_frames=6, duration_s=0.6)
    fill_scan(rec_a.parent, 0)
    fill_scan(rec_b.parent, 1)
    pdir = store.project_dir(pid)
    put(pdir / "references" / "r-1" / "mesh.ply", 5555)
    put(pdir / "scans" / "scan-orphan" / "x.bin", 4444)  # a folder project.json does not list

    r = requests.get(url + f"/api/projects/{pid}/disk", timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["project_id"] == pid
    assert d["bytes"] == size_of(pdir)
    assert [s["id"] for s in d["scans"]] == [a["id"], b["id"], empty["id"]]
    for s in d["scans"]:
        sd = pdir / "scans" / s["id"]
        proc = sd / "process"
        assert s["bytes"] == size_of(sd), s["id"]
        assert s["recording"] == size_of(sd / "recording"), s["id"]
        assert s["frames"] == size_of(sd / "recording" / "frames"), s["id"]
        n_png = len(list((sd / "recording" / "frames").glob("*.png"))) if (sd / "recording").is_dir() else 0
        assert s["n_frame_files"] == n_png
        assert s["undo"] == size_of(proc / "cache") + size_of(proc / "selections"), s["id"]
        assert s["result"] == size_of(sd / "result"), s["id"]
        stage_dirs = [q for q in proc.iterdir() if q.is_dir() and q.name not in ("cache", "selections")] \
            if proc.is_dir() else []
        assert s["stages"] == {q.name: size_of(q) for q in stage_dirs}, s["id"]
        assert s["stages_bytes"] == sum(s["stages"].values())
        assert s["other"] == s["bytes"] - s["recording"] - s["stages_bytes"] - s["undo"] - s["result"]
        assert s["other"] >= 0
    sa = d["scans"][0]
    assert sa["stages"] == {"track": 9001 + 101, "straighten": 7007 + 101}
    assert sa["other"] == 4900 + 215 + 6041 + 1928 + 15746  # state.json, straighten.json, thumbnail, features
    assert sa["n_frames"] == 5 and sa["duration_s"] == 0.5
    assert d["scans"][2]["bytes"] == 0
    assert d["references"] == size_of(pdir / "references") == 5555
    assert d["other"] == d["bytes"] - sum(s["bytes"] for s in d["scans"]) - d["references"]
    assert d["other"] == 4444 + (pdir / "project.json").stat().st_size
    for key in ("recording", "stages", "undo", "result"):  # project totals of each part
        want = sum(s["stages_bytes"] if key == "stages" else s[key] for s in d["scans"])
        assert d["parts"][key] == want, key
    assert d["trash"] == {"bytes": 0, "projects": 0}

    # the project page still gets its data without walking any folder (the route is separate)
    assert "disk" not in requests.get(url + f"/api/projects/{pid}", timeout=TIMEOUT).json()


def test_trash_is_counted_on_its_own(env):
    url, root = env["url"], env["root"]
    gone = requests.post(url + "/api/projects", json={"name": "To delete"}, timeout=TIMEOUT).json()
    put(root / "projects" / gone["id"] / "scans" / "scan-x" / "recording" / "frames" / "000000.png", 12345)
    keep = requests.post(url + "/api/projects", json={"name": "Kept"}, timeout=TIMEOUT).json()
    assert requests.delete(url + f"/api/projects/{gone['id']}", timeout=TIMEOUT).status_code == 204
    d = requests.get(url + f"/api/projects/{keep['id']}/disk", timeout=TIMEOUT).json()
    trash = root / "projects" / ".trash"
    assert d["trash"] == {"bytes": size_of(trash), "projects": len([q for q in trash.iterdir() if q.is_dir()])}
    assert d["trash"]["bytes"] >= 12345
    assert d["bytes"] == size_of(root / "projects" / keep["id"])  # the trash is not part of the project


def test_unknown_project_and_symlinks(env, tmp_path):
    url, store = env["url"], env["store"]
    r = requests.get(url + "/api/projects/no-such-0000/disk", timeout=TIMEOUT)
    assert r.status_code == 404 and r.json()["hint"]
    r = requests.get(url + "/api/projects/..%2F..%2Fsecret/disk", timeout=TIMEOUT)
    assert r.status_code == 404
    # a link inside a project is not followed (it could point at a whole drive)
    p = requests.post(url + "/api/projects", json={"name": "Linked"}, timeout=TIMEOUT).json()
    outside = tmp_path / "outside"
    put(outside / "big.bin", 99999)
    link = store.project_dir(p["id"]) / "scans" / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        try:  # Windows without the symlink right: a junction needs none
            import _winapi
            _winapi.CreateJunction(str(outside), str(link))
        except (ImportError, AttributeError, OSError):
            pytest.skip("this account can make neither a symbolic link nor a junction")
    d = requests.get(url + f"/api/projects/{p['id']}/disk", timeout=TIMEOUT).json()
    assert d["bytes"] < 99999
    assert d["bytes"] == (store.project_dir(p["id"]) / "project.json").stat().st_size


def test_report_is_plain_json(env):
    """Sizes are integers (bytes), never floats, so the UI's sums are exact."""
    url = env["url"]
    pid = requests.post(url + "/api/projects", json={"name": "Ints"}, timeout=TIMEOUT).json()["id"]
    d = requests.get(url + f"/api/projects/{pid}/disk", timeout=TIMEOUT).json()
    assert isinstance(d["bytes"], int) and isinstance(d["references"], int) and isinstance(d["other"], int)
    assert isinstance(d["seconds"], float) and d["seconds"] >= 0
    json.dumps(d)
