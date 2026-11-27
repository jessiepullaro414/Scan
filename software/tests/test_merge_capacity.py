"""A merge says when its mesh model ran out of room (ENGINE_PLAN R2-5), as ``process_recording`` does.

``merge_scans`` builds its own pipeline and frame loop: with the confidence model's pool forced small (as
tests/test_recon_pool_full.py does) the merge saves the ``mesh_capacity`` block, with the scan and the frame at which
the pool first had no room, and exactly one warning, and still writes its mesh; a normal merge has no warning.
Processor path, a small simulated recording merged with itself."""
from __future__ import annotations

import json

import pytest

N = 6


@pytest.fixture(scope="module")
def rec(tmp_path_factory):
    pytest.importorskip("open3d")
    from recordings import scan_recording  # noqa: PLC0415

    return scan_recording(tmp_path_factory.mktemp("mergepool") / "rec", n_frames=N, image_size=(640, 400),
                          realistic=True, seed=4, depth_every=0)


def _merge(rec, out, monkeypatch=None, max_bricks=None) -> dict:
    from ohs.align.merge import ScanInput, merge_scans  # noqa: PLC0415
    from ohs.app import pipeline as pl  # noqa: PLC0415
    from ohs.app.pipeline import PipelineParams  # noqa: PLC0415
    from ohs.app.process import ProcessParams  # noqa: PLC0415
    from ohs.io.recording import RecordingReader  # noqa: PLC0415

    if max_bricks is not None:
        base = pl.ConfidenceTsdf

        class Small(base):  # the pipeline's model with its pool forced small
            def __init__(self, *a, **kw):
                super().__init__(*a, **{**kw, "initial_bricks": 64, "max_bricks": max_bricks})

        monkeypatch.setattr(pl, "ConfidenceTsdf", Small)
    gt = RecordingReader(rec).ground_truth_poses()
    rows = [(int(i), 0.0, True, T) for i, T in sorted(gt.items())]
    params = ProcessParams(pipeline=PipelineParams(recon="confidence"), export=("ply",))
    return merge_scans([ScanInput(rec, rows, name="Front"), ScanInput(rec, rows, name="Back")], out, params)


def test_a_merge_whose_model_ran_out_of_room_says_so_once_and_keeps_its_mesh(rec, tmp_path, monkeypatch):
    from ohs.io.recording import RecordingReader  # noqa: PLC0415

    rep = _merge(rec, tmp_path / "out", monkeypatch, max_bricks=200)
    cap = rep.get("mesh_capacity")
    assert cap is not None, "the merge saved no mesh_capacity block"
    assert cap["full"] is True and cap["max_bricks"] == 200 and cap["bricks"] == 200 and cap["bricks_refused"] > 0
    assert cap["first_refused_scan"] == "Front" and cap["first_refused_frame"] in set(RecordingReader(rec).indices.tolist())
    said = [w for w in rep["warnings"] if "ran out of room" in w]
    assert len(said) == 1, rep["warnings"]
    w = said[0]
    assert f'after frame {cap["first_refused_frame"]} of "Front"' in w and f"{cap['bricks_refused']:,}" in w
    assert "incomplete" in w and "coarser" in w
    assert rep["mesh"]["n_faces"] > 0 and (tmp_path / "out" / "mesh.ply").is_file()  # what was fused is kept
    saved = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert saved["mesh_capacity"] == cap and w in saved["warnings"]


def test_a_normal_merge_has_no_capacity_warning(rec, tmp_path):
    rep = _merge(rec, tmp_path / "out")
    cap = rep["mesh_capacity"]
    assert cap["full"] is False and cap["bricks_refused"] == 0 and cap["first_refused_frame"] is None
    assert cap["first_refused_scan"] is None and not any("ran out of room" in w for w in rep["warnings"])
