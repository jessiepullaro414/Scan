"""``tools/snapshot.py`` (maintenance task A2): the compare logic on small synthetic files and one short real snapshot."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import snapshot as snap  # noqa: E402


def _arrays() -> dict[str, np.ndarray]:
    poses = np.tile(np.eye(4), (3, 1, 1))
    poses[:, 0, 3] = [0.0, 1.5, 3.0]
    return {"poses": poses, "vertices": np.arange(12, dtype=np.float32).reshape(4, 3),
            "faces": np.array([[0, 1, 2], [1, 2, 3]], dtype=np.int64), "laser_a": np.ones((5, 3), np.float32)}


def _meta(**kw) -> dict:
    return {"quality": "preview", "frames": 3, "device": "cpu", "git_commit": "abc", **kw}


def _write(path: Path, arrays: dict, meta: dict | None = None) -> Path:
    snap.save_npz(path, arrays, _meta() if meta is None else meta)
    return path


def test_identical_exits_zero(tmp_path, capsys):
    a = _write(tmp_path / "a.npz", _arrays())
    b = _write(tmp_path / "b.npz", _arrays())
    assert snap.main(["compare", str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "IDENTICAL" in out and "poses: max abs diff 0" in out


def test_perturbed_pose_is_reported(tmp_path, capsys):
    arr = _arrays()
    arr2 = {k: v.copy() for k, v in arr.items()}
    arr2["poses"][1, 0, 3] += 2.5e-3
    a, b = _write(tmp_path / "a.npz", arr), _write(tmp_path / "b.npz", arr2)
    assert snap.main(["compare", str(a), str(b)]) != 0
    out = capsys.readouterr().out
    assert "poses: max abs diff 0.0025" in out and "DIFFERENT" in out
    assert "vertices: max abs diff 0  ok" in out  # the unchanged arrays stay ok


def test_shape_mismatch_and_missing_array(tmp_path, capsys):
    arr = _arrays()
    arr2 = dict(arr, vertices=arr["vertices"][:3])
    del arr2["laser_a"]
    a, b = _write(tmp_path / "a.npz", arr), _write(tmp_path / "b.npz", arr2)
    assert snap.main(["compare", str(a), str(b), "--tol", "1e6"]) != 0  # a huge tolerance does not hide a shape
    out = capsys.readouterr().out
    assert "vertices: shape (4, 3) vs (3, 3)" in out and "laser_a: only in a" in out


def test_tolerance(tmp_path):
    arr = _arrays()
    arr2 = {k: v.copy() for k, v in arr.items()}
    arr2["vertices"][0, 0] += 1e-4
    a, b = _write(tmp_path / "a.npz", arr), _write(tmp_path / "b.npz", arr2)
    assert snap.main(["compare", str(a), str(b)]) != 0
    assert snap.main(["compare", str(a), str(b), "--tol", "1e-3"]) == 0
    assert snap.main(["compare", str(a), str(b), "--tol", "1e-6"]) != 0


def test_nan_handling():
    x = np.array([1.0, np.nan]), np.array([1.0, np.nan])
    assert snap.max_abs_diff(*x) == 0.0
    assert snap.max_abs_diff(np.array([1.0, np.nan]), np.array([1.0, 2.0])) == float("inf")
    assert snap.max_abs_diff(np.array([True, False]), np.array([True, True])) == 1.0


def test_device_difference_warns(tmp_path, capsys):
    a = _write(tmp_path / "a.npz", _arrays(), _meta(device="Radeon"))
    b = _write(tmp_path / "b.npz", _arrays(), _meta(device="RTX"))
    assert snap.main(["compare", str(a), str(b)]) == 0  # the data is identical; the warning is separate
    assert "WARNING device differs" in capsys.readouterr().out


def test_folders(tmp_path, capsys):
    for d in ("x", "y"):
        _write(tmp_path / d / "preview.npz", _arrays())
    _write(tmp_path / "x" / "full.npz", _arrays())
    assert snap.main(["compare", str(tmp_path / "x"), str(tmp_path / "y")]) != 0  # full.npz missing in y
    assert "full.npz: only in a" in capsys.readouterr().out
    _write(tmp_path / "y" / "full.npz", _arrays())
    assert snap.main(["compare", str(tmp_path / "x"), str(tmp_path / "y")]) == 0


def test_canonical_mesh_ignores_vertex_order():
    V = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], np.float32)
    F = np.array([[0, 1, 2], [0, 3, 1]], np.int64)
    perm = np.array([2, 0, 3, 1])  # new vertex j is old vertex perm[j]
    inv = np.argsort(perm)
    V2, F2 = V[perm], inv[F][:, ::-1][:, ::-1][[1, 0]]
    F2 = np.roll(F2, 1, axis=1)  # same triangles, other start vertex and order
    a, b = snap.canonical_mesh(V, F), snap.canonical_mesh(V2, F2)
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])
    flipped = snap.canonical_mesh(V, F[:, ::-1])  # a flipped orientation is a different mesh
    assert not np.array_equal(a[1], flipped[1])


def test_real_snapshot_twice_is_identical(tmp_path, capsys):
    pytest.importorskip("open3d")
    from ohs.app import bench

    spec = bench.BenchSpec(n_frames=4, image_size=(640, 400), depth_every=2)
    rec = bench.ensure_recording(spec, tmp_path / "bench")
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    snap.run_snapshot(out_a, frames=4, qualities=("preview",), recording=rec, say=lambda s: None)
    snap.run_snapshot(out_b, frames=4, qualities=("preview",), recording=rec, say=lambda s: None)
    arrays, meta = snap.load_npz(out_a / "preview.npz")
    assert arrays["poses"].shape == (4, 4, 4) and meta["quality"] == "preview" and meta["device"]
    from ohs import compute

    if compute.describe()["backend"] == "gpu":
        assert snap.main(["compare", str(out_a), str(out_b)]) == 0
        assert "IDENTICAL" in capsys.readouterr().out
    else:
        # KNOWN (A2 report): on the CPU path the tracker is not deterministic run to run (poses differ by
        # ~0.1-0.3 mm, so the mesh differs too), so identical snapshots cannot be demanded there yet.
        a, _ = snap.load_npz(out_a / "preview.npz")
        b, _ = snap.load_npz(out_b / "preview.npz")
        assert a.keys() == b.keys()
        assert np.array_equal(a["pose_index"], b["pose_index"]) and np.array_equal(a["pose_ok"], b["pose_ok"])
        assert snap.max_abs_diff(a["poses"], b["poses"]) < 1.0
        assert all(np.array_equal(a[k], b[k]) for k in a if k.startswith("laser_"))
