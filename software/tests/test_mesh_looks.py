"""Each vertex's count of independent looks in ``mesh.ply`` (FEATURE_PLAN N1, decision D6).

* The property ``looks`` (float32, per vertex) is *added*: a file written with it holds every other
  value byte for byte as a file written without it (the header gains two lines, each vertex record
  four bytes at its end), and a file without it is byte for byte what it was before.
* Every reader copes with files that have it and files that do not: the Python PLY reader and
  ``load_mesh``, the server's reader of a stage's mesh (edits, undo), a reference comparison's,
  the STL export, and the app's TypeScript reader (run with Node when it is installed).
* The confidence model gives it (``extract_mesh(with_looks=True)``) without changing the mesh or
  its sigma; a processed scan and a merge carry it on the GPU path, and on the CPU path (Open3D's
  model, which counts no looks) the file simply has none.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from ohs.track.export import load_mesh, load_ply, save_mesh, write_stl

UI_PLY = Path(__file__).resolve().parents[1] / "ui" / "src" / "three" / "plyParse.ts"


def _mesh(rng: np.random.Generator, n: int = 40):
    V = rng.normal(0.0, 10.0, (n, 3))
    F = np.stack([np.arange(n - 2), np.arange(1, n - 1), np.arange(2, n)], axis=1)
    N = rng.normal(0.0, 1.0, (n, 3))
    N /= np.linalg.norm(N, axis=1, keepdims=True)
    S = rng.uniform(0.01, 2.0, n).astype(np.float32)
    L = rng.uniform(1.0, 60.0, n).astype(np.float32)
    filled = rng.random(len(F)) < 0.3
    return V, F, N, S, L, filled


def _split(raw: bytes) -> tuple[list[str], bytes]:
    end = raw.index(b"end_header\n") + len(b"end_header\n")
    return raw[:end].decode("ascii").splitlines(), raw[end:]


def test_looks_is_an_added_property_and_every_other_value_is_unchanged(tmp_path):
    rng = np.random.default_rng(0)
    V, F, N, S, L, filled = _mesh(rng)
    save_mesh(tmp_path / "old.ply", V, F, N, quality=S, filled=filled)
    save_mesh(tmp_path / "new.ply", V, F, N, quality=S, filled=filled, looks=L)
    h_old, b_old = _split((tmp_path / "old.ply").read_bytes())
    h_new, b_new = _split((tmp_path / "new.ply").read_bytes())
    # the header gains the property (and a comment saying what it is) right after quality, nothing else
    added = [x for x in h_new if x not in h_old]
    assert added == ["comment looks: the vertex's count of independent looks (OpenHandScan depth confidence)",
                     "property float looks"]
    assert [x for x in h_new if x in h_old] == h_old
    assert h_new.index("property float looks") == h_new.index("property float quality") + 2  # its comment between
    # each vertex record is the old one plus four bytes; the faces are byte for byte the same
    nv, stride = len(V), 3 * 4 + 3 * 4 + 4
    old_v = np.frombuffer(b_old[:nv * stride], np.uint8).reshape(nv, stride)
    new_v = np.frombuffer(b_new[:nv * (stride + 4)], np.uint8).reshape(nv, stride + 4)
    assert np.array_equal(new_v[:, :stride], old_v)
    assert np.array_equal(new_v[:, stride:].copy().view("<f4").ravel(), L)
    assert b_new[nv * (stride + 4):] == b_old[nv * stride:]
    # read back: every value as without the property, and the looks exactly
    a, b = load_ply(tmp_path / "old.ply"), load_ply(tmp_path / "new.ply")
    for k in ("vertices", "normals", "quality", "faces", "filled"):
        assert a[k].dtype == b[k].dtype and np.array_equal(a[k], b[k]), k
    assert a["looks"] is None and b["looks"].dtype == np.float64 and np.array_equal(b["looks"], L.astype(np.float64))
    with pytest.raises(ValueError, match="looks"):
        save_mesh(tmp_path / "bad.ply", V, F, looks=L[:-1])


def test_a_file_without_looks_is_written_as_before(tmp_path):
    """Without the property the writer's bytes are what they were: the header and records as before
    this change (the layout is spelled out here from the format, not from the writer)."""
    rng = np.random.default_rng(1)
    V, F, N, S, _, filled = _mesh(rng, 6)
    save_mesh(tmp_path / "m.ply", V, F, N, quality=S, filled=filled)
    head, body = _split((tmp_path / "m.ply").read_bytes())
    assert head == ["ply", "format binary_little_endian 1.0", "comment OpenHandScan export, units: mm",
                    f"element vertex {len(V)}", "property float x", "property float y", "property float z",
                    "property float nx", "property float ny", "property float nz",
                    "comment quality: the vertex's standard deviation in mm (OpenHandScan depth confidence)",
                    "property float quality", f"element face {len(F)}", "property list uchar int vertex_indices",
                    "comment filled: 1 = made by hole filling, not measured (OpenHandScan)", "property uchar filled",
                    "end_header"]
    vt = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("nx", "<f4"), ("ny", "<f4"), ("nz", "<f4"), ("q", "<f4")])
    ft = np.dtype([("n", "u1"), ("i", "<i4", (3,)), ("f", "u1")])
    v = np.zeros(len(V), vt)
    v["x"], v["y"], v["z"], v["nx"], v["ny"], v["nz"], v["q"] = *V.T, *N.T, S
    f = np.zeros(len(F), ft)
    f["n"], f["i"], f["f"] = 3, F, filled
    assert body == v.tobytes() + f.tobytes()
    d = load_ply(tmp_path / "m.ply")
    assert d["looks"] is None


def test_the_python_readers_take_files_with_and_without_looks(tmp_path):
    from ohs.inspect.reference import ScanSurface
    from ohs.server.processing import _read_stage_mesh

    rng = np.random.default_rng(2)
    V, F, N, S, L, filled = _mesh(rng)
    save_mesh(tmp_path / "old.ply", V, F, N, quality=S, filled=filled)
    save_mesh(tmp_path / "new.ply", V, F, N, quality=S, filled=filled, looks=L)
    got = {}
    for name in ("old", "new"):
        p = tmp_path / f"{name}.ply"
        Vm, Fm, Nm = load_mesh(p)
        stage = _read_stage_mesh(p)  # the server's edits and undo read a stage's mesh this way
        surf = ScanSurface.from_file(p)  # the comparison with a reference model
        write_stl(tmp_path / f"{name}.stl", Vm, Fm)  # the STL export of what was read
        got[name] = (Vm, Fm, Nm, *stage, surf.V, surf.F, surf.sigma, surf.filled, (tmp_path / f"{name}.stl").read_bytes())
    for x, y in zip(got["old"], got["new"]):
        assert (x == y) if isinstance(x, bytes) else np.array_equal(x, y)


_NODE_SCRIPT = r"""
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const { parsePly } = await import(pathToFileURL(process.argv[2]).href);
const out = {};
for (const f of process.argv.slice(3)) {
  const b = readFileSync(f);
  const m = parsePly(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength));
  out[f] = m === null ? null : {positions: Array.from(m.positions), normals: m.normals && Array.from(m.normals),
    quality: m.quality && Array.from(m.quality), filled: m.filled && Array.from(m.filled),
    indices: Array.from(m.indices), faceCount: m.faceCount};
}
console.log(JSON.stringify(out));
"""


def test_the_app_reader_takes_files_with_and_without_looks(tmp_path):
    """The TypeScript reader of the app (``ui/src/three/plyParse.ts``, read here, not changed) gives the
    same mesh for a file with the property as for one without; run with Node (type stripping)."""
    node = shutil.which("node")
    if node is None or not UI_PLY.is_file():
        pytest.skip("Node or the app's PLY reader is not here")
    rng = np.random.default_rng(3)
    V, F, N, S, L, filled = _mesh(rng)
    files = []
    for name, kw in (("old", {}), ("new", {"looks": L}), ("plain_new", None)):
        p = tmp_path / f"{name}.ply"
        if kw is None:  # positions and looks only: a record of 16 bytes
            save_mesh(p, V, F, looks=L)
        else:
            save_mesh(p, V, F, N, quality=S, filled=filled, **kw)
        files.append(str(p))
    (tmp_path / "run.mjs").write_text(_NODE_SCRIPT, encoding="utf-8")
    r = subprocess.run([node, "--no-warnings", str(tmp_path / "run.mjs"), str(UI_PLY), *files], capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    old, new, plain = (out[f] for f in files)
    assert old is not None and new == old
    assert np.allclose(old["positions"], V.astype(np.float32).ravel(), rtol=0, atol=0)
    assert np.array_equal(old["indices"], F.ravel()) and np.array_equal(old["quality"], S)
    assert plain is not None and plain["positions"] == old["positions"] and plain["indices"] == old["indices"]
    assert plain["normals"] is None and plain["quality"] is None


# --------------------------------------------------------------------------- the model
W, H, FX = 320, 200, 180.0
K = np.array([[FX, 0.0, (W - 1) / 2], [0.0, FX, (H - 1) / 2], [0.0, 0.0, 1.0]])
Z0 = 300.0


def _plane_depth(T_wc: np.ndarray, rng, noise: float = 0.3) -> np.ndarray:
    vs, us = np.mgrid[0:H, 0:W].astype(np.float64)
    r = np.stack([(us - K[0, 2]) / FX, (vs - K[1, 2]) / FX, np.ones_like(us)], axis=-1)
    d = r @ T_wc[:3, :3].T
    lam = (Z0 - T_wc[2, 3]) / d[..., 2] + rng.normal(0.0, noise, us.shape)
    return lam.astype(np.float32)


def _canon(V, F, N, *per_vertex):
    """The mesh in a fixed order (vertices sorted, faces rotated to their smallest index and sorted),
    with the per-vertex arrays in the same order."""
    order = np.lexsort((V[:, 2], V[:, 1], V[:, 0]))
    inv = np.empty(len(V), dtype=np.int64)
    inv[order] = np.arange(len(V))
    F = inv[F]
    F = np.take_along_axis(F, (np.argmin(F, axis=1)[:, None] + np.arange(3)[None, :]) % 3, axis=1)
    F = F[np.lexsort((F[:, 2], F[:, 1], F[:, 0]))]
    return (V[order], F, N[order], *(a[order] for a in per_vertex))


def _backends() -> list[str]:
    from ohs import compute

    return ["cpu"] + (["gpu"] if compute.get_device() is not None else [])


@pytest.mark.parametrize("backend", ["cpu", "gpu"])
@pytest.mark.parametrize("step_mm", [0.0, 6.0])
def test_the_model_gives_each_vertex_its_looks_and_nothing_else_changes(backend, step_mm):
    pytest.importorskip("open3d")
    from ohs.geometry import make_se3
    from ohs.track.recon_tsdf import ConfidenceTsdf

    if backend not in _backends():
        pytest.skip("no GPU")
    rng = np.random.default_rng(4)
    vol = ConfidenceTsdf(K, (W, H), voxel_mm=2.0, trunc_mm=8.0, depth_min_mm=150.0, depth_max_mm=450.0, backend=backend)
    n = 10
    for k in range(n):
        T = make_se3(np.eye(3), np.array([k * step_mm, 0.0, 0.0]))
        vol.integrate(_plane_depth(T, rng), np.full((H, W), 0.3, np.float32), T)
    # Open3D's marching cubes gives the same mesh in another vertex order each time: compared in a fixed order
    V, F, N, S = _canon(*vol.extract_mesh(with_sigma=True))
    V2, F2, N2, S2, L = _canon(*vol.extract_mesh(with_sigma=True, with_looks=True))
    for a, b in ((V, V2), (F, F2), (N, N2), (S, S2)):
        assert np.array_equal(a, b)
    V3, F3, N3, L3 = _canon(*vol.extract_mesh(with_looks=True))
    assert np.array_equal(V3, V) and np.array_equal(F3, F) and np.array_equal(L3, L)
    assert L.dtype == np.float32 and L.shape == (len(V),)
    inner = np.abs(V[:, 0] - 0.5 * (n - 1) * step_mm) < 20.0  # seen by every frame
    if step_mm == 0:  # a scanner that stands still: one look however many frames
        assert np.allclose(L[inner], 1.0)
    else:  # 6 mm a frame is more than the dot spacing: a look per frame
        assert np.median(L[inner]) >= n - 2
    assert L.min() >= 1.0 - 1e-4 and L.max() <= n + 1e-4
    vol.close()


def test_a_processed_scan_carries_the_looks_on_the_confidence_model_only(tmp_path):
    """GPU path: ``mesh.ply`` has ``looks`` beside ``quality``; CPU path (Open3D's model): neither.
    A merge of two scans the same."""
    pytest.importorskip("open3d")
    from ohs.align import ScanInput, merge_scans
    from ohs.app.pipeline import PipelineParams
    from ohs.app.process import ProcessParams, process_recording
    from ohs.io.recording import RecordingReader
    from recordings import scan_recording

    rec = scan_recording(tmp_path / "rec", scene="bracket", n_frames=6, image_size=(640, 400), realistic=False, seed=0,
                         depth_every=0)
    gt = RecordingReader(rec).ground_truth_poses()
    params = ProcessParams(pipeline=PipelineParams(), export=("ply",))
    rep = process_recording(rec, tmp_path / "out", params=params, poses=gt)
    d = load_ply(tmp_path / "out" / "mesh.ply")
    confidence = "confidence" in rep["mesh"]["reconstruction"]
    assert (d["quality"] is not None) == confidence and (d["looks"] is not None) == confidence
    if confidence:
        assert len(d["looks"]) == len(d["vertices"]) and d["looks"].min() >= 1.0 - 1e-4
        assert np.median(d["looks"]) > 1.0
    rows = [(i, 0.0, True, T) for i, T in sorted(gt.items())]
    merge_scans([ScanInput(rec, rows), ScanInput(rec, rows, name="again")], tmp_path / "merged", params)
    m = load_ply(tmp_path / "merged" / "mesh.ply")
    assert (m["quality"] is not None) == confidence and (m["looks"] is not None) == confidence
    if confidence:
        assert len(m["looks"]) == len(m["vertices"])
