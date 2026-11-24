"""A full brick pool is said, not just logged (ENGINE_PLAN R2-5).

When the confidence TSDF's pool is at ``max_bricks`` it fuses no new surface. The model keeps a
capacity status (:meth:`ConfidenceTsdf.capacity_status`), ``process_recording`` saves it as the
result's ``mesh_capacity`` block, and when any surface was refused the result's ``warnings`` say so
in plain words. The usable geometry is kept: this warns, it does not stop.
"""
from __future__ import annotations

import numpy as np
import pytest

from ohs import compute
from ohs.track.recon_tsdf import ConfidenceTsdf
from test_track_recon_tsdf import H, K, W, plane_depth, poses

BACKENDS = ["cpu", "gpu"]


def _volume(backend: str, **kw) -> ConfidenceTsdf:
    if backend == "gpu" and compute.get_device() is None:
        pytest.skip(f"no GPU: {compute.status()}")
    return ConfidenceTsdf(K, (W, H), voxel_mm=2.0, trunc_mm=8.0, depth_min_mm=150.0, depth_max_mm=450.0,
                          backend=backend, **kw)


def _fuse(v: ConfidenceTsdf, Ts) -> None:
    for T in Ts:
        v.integrate(plane_depth(T), np.full((H, W), 0.4, np.float32), T)


@pytest.mark.parametrize("backend", BACKENDS)
def test_the_status_counts_what_a_full_pool_refused(backend):
    Ts = poses(4, 40.0)
    free = _volume(backend)
    _fuse(free, Ts[:1])
    first = free.n_bricks
    _fuse(free, Ts[1:])
    reached = free.n_bricks
    st = free.capacity_status()
    assert st["full"] is False and st["bricks_refused"] == 0 and st["first_refused_call"] is None
    assert st["calls_refused"] == 0 and st["bricks"] == reached
    free.close()

    limit = first + 50  # room for the first frame, not for all four
    assert first < limit < reached
    small = _volume(backend, initial_bricks=64, max_bricks=limit)
    _fuse(small, Ts)
    st = small.capacity_status()
    assert st["full"] is True and st["bricks"] == limit == st["max_bricks"]
    assert st["bricks_refused"] == reached - limit  # every brick the frames reached and that got no room
    assert st["first_refused_call"] == 1 and st["calls_refused"] == 3  # frames 2, 3, 4 (calls 1..3) lost surface
    assert st["requests_refused"] >= st["bricks_refused"]  # a refused brick is asked for again by the next frame
    assert st["max_mb"] == pytest.approx(small.memory_mb, abs=0.05) and st["mb"] == pytest.approx(small.memory_mb, abs=0.05)
    assert st["voxel_mm"] == 2.0
    before = dict(st)
    _fuse(small, Ts[3:])  # the same frame again: nothing new refused, one more call that lost surface
    st = small.capacity_status()
    assert st["bricks_refused"] == before["bricks_refused"] and st["calls_refused"] == 4
    assert st["requests_refused"] > before["requests_refused"]
    small.reset()  # a new scan in the same model: a fresh status
    st = small.capacity_status()
    assert st["full"] is False and st["bricks_refused"] == st["requests_refused"] == st["calls_refused"] == 0
    assert st["first_refused_call"] is None and st["bricks"] == 0
    _fuse(small, Ts[:1])
    assert small.capacity_status()["full"] is False
    small.close()


# --------------------------------------------------------------------------- the saved result
N = 8


@pytest.fixture(scope="module")
def rec(tmp_path_factory):
    pytest.importorskip("open3d")
    from recordings import scan_recording  # noqa: PLC0415

    return scan_recording(tmp_path_factory.mktemp("pool") / "rec", n_frames=N, image_size=(640, 400),
                          realistic=True, seed=4, depth_every=0)


def _process(rec, out, monkeypatch=None, max_bricks=None):
    from ohs.app import pipeline as pl  # noqa: PLC0415
    from ohs.app.pipeline import PipelineParams  # noqa: PLC0415
    from ohs.app.process import ProcessParams, process_recording  # noqa: PLC0415

    if max_bricks is not None:
        base = pl.ConfidenceTsdf

        class Small(base):  # the pipeline's model with its pool forced small
            def __init__(self, *a, **kw):
                super().__init__(*a, **{**kw, "initial_bricks": 64, "max_bricks": max_bricks})

        monkeypatch.setattr(pl, "ConfidenceTsdf", Small)
    return process_recording(rec, out, params=ProcessParams(pipeline=PipelineParams(recon="confidence")))


def test_a_full_pool_is_in_the_saved_result_and_its_warnings(rec, tmp_path, monkeypatch):
    import json

    rep = _process(rec, tmp_path / "out", monkeypatch, max_bricks=200)
    cap = rep["mesh_capacity"]
    assert cap["full"] is True and cap["max_bricks"] == 200 and cap["bricks"] == 200 and cap["bricks_refused"] > 0
    assert cap["first_refused_frame"] in range(N) and cap["calls_refused"] >= 1
    said = [w for w in rep["warnings"] if "ran out of room" in w]
    assert len(said) == 1, rep["warnings"]
    w = said[0]
    assert f"frame {cap['first_refused_frame']}" in w and f"{cap['bricks_refused']:,}" in w
    assert f"{cap['bricks'] + cap['bricks_refused']:,}" in w and "incomplete" in w and "coarser" in w
    assert rep["mesh"]["n_faces"] > 0  # what was fused is kept
    saved = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert saved["mesh_capacity"] == cap and w in saved["warnings"]


def test_a_normal_run_says_the_pool_was_not_full(rec, tmp_path):
    rep = _process(rec, tmp_path / "out")
    cap = rep["mesh_capacity"]
    assert cap["full"] is False and cap["bricks_refused"] == 0 and cap["first_refused_frame"] is None
    assert cap["bricks"] > 200 and not any("ran out of room" in w for w in rep["warnings"])


def test_the_open3d_model_has_no_capacity_block(rec, tmp_path):
    """The Open3D model (the CPU path's default mesh model) keeps no such status: the block is None."""
    from ohs.app.process import process_recording  # noqa: PLC0415

    rep = process_recording(rec, tmp_path / "out")
    if "Open3D" not in rep["mesh"]["reconstruction"]:
        pytest.skip("the mesh model here is the confidence TSDF")
    assert rep["mesh_capacity"] is None
