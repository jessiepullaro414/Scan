"""A recording without frame kinds (every recording made before them) gives the same result files and report as
before the frame pass learnt frames in turn (dot frames and laser frames; ``ohs.app.process``, "Frames in turn").

The reference digests below were taken on the code before frames in turn existed (2026-11-27, on the owner's mini
PC, processor path: ``OHS_COMPUTE=cpu``). They depend on the exact floating point of numpy, OpenCV and Open3D; if
they differ on another machine or on the graphics card's path, check first that the unchanged code gives other
digests there before anything else. Timings are left out of the report's digest (they differ from run to run).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

pytest.importorskip("open3d")

from ohs.app.process import ProcessParams, process_recording  # noqa: E402
from recordings import scan_recording  # noqa: E402

N = 8
#: report keys that are timings or paths (left out of the report's digest)
_VOLATILE = {"wall_time_s", "timings_ms", "out_dir", "recording", "files", "total_ms", "extract_ms", "ms"}

#: what the unchanged code wrote (module docstring), processor path
REFERENCE = {
    "default": {
        "trajectory_rows": 8, "trajectory.csv": "a66e8faa2e7c1cd50b7f1274d4fa9de0cdee0d5f799b0501e721d89e12353c86",
        "laser_points": 4687, "laser_points.ply": "4a865a9e2e1acbff4dbf2045541db05cdca20c2f9270700f34bb29a76549ceba",
        "mesh_vertices": 80221, "mesh.ply": "3856a7677c0a6033e6873498c3423708a1be34ed35aa87b720c7bcf5b4d36acd",
        "mesh.stl": "3856a7677c0a6033e6873498c3423708a1be34ed35aa87b720c7bcf5b4d36acd", "tracked": 8,
        "report": "82ff7a71dec23d27f7810097862fb5214acec071382da995e051a551dc026b66",
        "files": ["laser_points.ply", "mesh.ply", "mesh.stl", "report.json", "trajectory.csv"],
    },
    "every_2nd_from_1": {
        "trajectory_rows": 4, "trajectory.csv": "538e2544f05df6eb0d453b23381ed100e5de9cfa79988710dc1cd176295674ca",
        "laser_points": 2327, "laser_points.ply": "ea9039f60e8bd0ddfed8b71fca703ab0cfab16334b7c8ce8b6fbd2b6b191a75e",
        "mesh_vertices": 71618, "mesh.ply": "7c41852b7bb3249d6269445caea8a9ff1b3223d16e20c017ead6b8c09b34ffa4",
        "mesh.stl": None, "tracked": 4, "report": "b2a206e5d95967ec97ccb7ffa9b17f2904b38bc5de8e19dcd46c2bff0a504883",
        "files": ["laser_points.ply", "mesh.ply", "report.json", "trajectory.csv"],
    },
}


def _drop_volatile(x):
    if isinstance(x, dict):
        return {k: _drop_volatile(v) for k, v in x.items() if k not in _VOLATILE}
    if isinstance(x, list):
        return [_drop_volatile(v) for v in x]
    return x


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mesh_digest(path: Path) -> str:
    """A digest of a mesh that does not depend on the order of its vertices and faces (Open3D's marching cubes lists
    them in another order from run to run): the vertices sorted, each face as the sorted ranks of its vertices."""
    import numpy as np  # noqa: PLC0415
    import open3d as o3d  # noqa: PLC0415

    m = o3d.io.read_triangle_mesh(str(path))
    V = np.asarray(m.vertices, dtype=np.float64)
    F = np.asarray(m.triangles, dtype=np.int64)
    # sorted distinct vertices (an STL file repeats each vertex in every face it is in)
    U, rank = np.unique(V, axis=0, return_inverse=True)
    G = np.sort(rank.reshape(-1)[F], axis=1)
    G = G[np.lexsort(G.T[::-1])]
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(U).tobytes())
    h.update(np.ascontiguousarray(G).tobytes())
    return h.hexdigest()


def fingerprint(out: Path, rep: dict) -> dict:
    """What must not change: the trajectory and the laser points byte for byte, the meshes vertex for vertex and face
    for face (in any order), and the report's numbers without its timings."""
    traj = (out / "trajectory.csv").read_text(encoding="utf-8").splitlines()
    on_disk = json.loads((out / "report.json").read_text(encoding="utf-8"))
    return {
        "trajectory_rows": len(traj) - 1,
        "trajectory.csv": _sha(out / "trajectory.csv"),
        "laser_points": int(rep["lasers"]["n_points_exported"]),
        "laser_points.ply": _sha(out / "laser_points.ply"),
        "mesh_vertices": int(rep["mesh"]["n_vertices"]),
        "mesh.ply": mesh_digest(out / "mesh.ply"),
        "mesh.stl": mesh_digest(out / "mesh.stl") if (out / "mesh.stl").exists() else None,
        "tracked": int(rep["frames"]["tracked"]),
        "report": hashlib.sha256(json.dumps(_drop_volatile(on_disk), sort_keys=True).encode()).hexdigest(),
        "files": sorted(p.name for p in out.iterdir()),
    }


@pytest.fixture(scope="module")
def rec(tmp_path_factory) -> Path:
    return scan_recording(tmp_path_factory.mktemp("unchanged") / "rec", n_frames=N, image_size=(640, 400),
                          realistic=True, seed=4, depth_every=0)


CASES = {"default": ProcessParams(), "every_2nd_from_1": ProcessParams(frame_step=2, start=1, export=("ply",))}


def _run(rec: Path, out: Path, case: str) -> dict:
    return fingerprint(out, process_recording(rec, out, params=CASES[case]))


@pytest.mark.skipif(os.environ.get("OHS_COMPUTE", "").lower() != "cpu",
                    reason="the reference digests are the processor path's (OHS_COMPUTE=cpu)")
@pytest.mark.parametrize("case", sorted(CASES))
def test_a_recording_without_kinds_gives_the_same_results(rec, tmp_path, case):
    got = _run(rec, tmp_path / "out", case)
    assert REFERENCE[case] is not None, f"no reference yet; this run wrote {got}"
    assert got == REFERENCE[case]


@pytest.mark.skipif(os.environ.get("OHS_COMPUTE", "").lower() != "cpu",
                    reason="the reference digests are the processor path's (OHS_COMPUTE=cpu)")
def test_a_kinds_column_of_both_only_changes_nothing(rec, tmp_path):
    """A recording written with kinds whose every frame holds both lights is a recording of today."""
    from ohs.io.recording import RecordingReader  # noqa: PLC0415

    both = tmp_path / "both"
    shutil.copytree(rec, both)
    lines = (both / "frames.csv").read_text(encoding="utf-8").splitlines()
    (both / "frames.csv").write_text("\n".join([lines[0] + ",kind"] + [ln + ",both" for ln in lines[1:]]) + "\n",
                                     encoding="utf-8")
    r = RecordingReader(both)
    assert r.has_kinds and set(r.kinds()) == {"both"}
    got = _run(both, tmp_path / "out", "default")
    ref = REFERENCE["default"]
    assert ref is not None
    # the report names the recording's folder nowhere in its digest, so everything is the same
    assert got == ref
