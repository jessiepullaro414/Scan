""""Make only the thin walls finer" says how full its finer mesh model got (ENGINE_PLAN R2-5).

``make_fine_walls`` builds its own ``ConfidenceTsdf`` for the kept depth: what it returns carries that model's
``capacity_status()`` as ``mesh_capacity`` (with ``first_refused_frame``, the kept frame's index at which the pool
first had no room), so that the server can warn as for a scan. Processor path, the whole step of
tests/test_mesh_fine_walls.py on a 1 mm wall; once with the pool forced small."""
from __future__ import annotations

import json

import numpy as np
import pytest

from test_mesh_fine_walls import H, K, W, _sheet, _views, _wall_mesh  # noqa: F401


def _stage(tmp_path):
    from ohs.app.fine_walls import FusedDepthSpool  # noqa: PLC0415
    from ohs.track.export import save_mesh  # noqa: PLC0415
    from ohs.track.posegraph import write_trajectory  # noqa: PLC0415
    from ohs.track.recon_tsdf import ConfidenceTsdf  # noqa: PLC0415

    coarse = ConfidenceTsdf(K, (W, H), voxel_mm=1.0, trunc_mm=4.0, depth_min_mm=150.0, depth_max_mm=450.0,
                            backend="cpu", back_mm=2.0, opposite_mm=1.0)
    stage = tmp_path / "stage"
    spool = FusedDepthSpool(stage / "fused_depth", coarse)
    rows = []
    for k, (T, d) in enumerate(_views(1.0)):
        spool.add(k, d, np.where(d > 0, 0.2, 0.0).astype(np.float32), None, T)
        rows.append((100 + k, 0.1 * k, True, T))
    spool.close([r[0] for r in rows])
    coarse.close()
    V, F = _wall_mesh(0.85)
    save_mesh(stage / "mesh.ply", V, F, None, quality=np.full(len(V), 0.2, np.float32),
              looks=np.full(len(V), 3.0, np.float32))
    (stage / "report.json").write_text(json.dumps({"mesh": {"voxel_mm": 1.0},
                                                   "params": {"reconstruction": {"opposite_mm": 1.0}}}))
    write_trajectory(stage / "trajectory.csv", rows)
    return stage, [r[0] for r in rows]


def test_the_step_returns_its_models_capacity_status(tmp_path):
    pytest.importorskip("open3d")
    from ohs.app.fine_walls import make_fine_walls  # noqa: PLC0415

    stage, _ = _stage(tmp_path)
    rep = make_fine_walls(stage, stage / "fused_depth", out_dir=tmp_path / "fine", backend="cpu")
    cap = rep.get("mesh_capacity")
    assert cap is not None, "make_fine_walls returned no mesh_capacity"
    assert cap["full"] is False and cap["bricks_refused"] == 0 and cap["first_refused_frame"] is None
    assert cap["bricks"] > 64 and cap["voxel_mm"] == 0.5 and rep["acted"]  # more than the smallest pool holds


def test_a_full_finer_model_is_counted(tmp_path, monkeypatch):
    pytest.importorskip("open3d")
    import ohs.track.recon_tsdf as RT  # noqa: PLC0415
    from ohs.app.fine_walls import make_fine_walls  # noqa: PLC0415

    stage, indices = _stage(tmp_path)
    base = RT.ConfidenceTsdf

    class Small(base):  # the finer model with its pool forced small
        def __init__(self, *a, **kw):
            super().__init__(*a, **{**kw, "initial_bricks": 64, "max_bricks": 64})  # 64: the smallest pool

    monkeypatch.setattr(RT, "ConfidenceTsdf", Small)
    rep = make_fine_walls(stage, stage / "fused_depth", out_dir=tmp_path / "fine", backend="cpu")
    cap = rep["mesh_capacity"]
    assert cap["full"] is True and cap["max_bricks"] == 64 and cap["bricks"] == 64 and cap["bricks_refused"] > 0
    assert cap["first_refused_frame"] in indices
