"""Rewind rebuild cost: a 2-minute scan's live model is rebuilt within the ~3 s budget.

A synthetic 2-minute markerless scan (1200 tracked frames at 10 fps, a table with a bump, the
scanner sweeping 1 m) is put into a :class:`~ohs.server.session.ScanSession`'s rewind buffer and
rebuilt into a real 640x400 ``FramePipeline`` (the live configuration at 1280x800). Also checks
that the TSDF weight rescaling after a subsampled rebuild really writes into Open3D's grid.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs.app.config import Config  # noqa: E402
from ohs.app.pipeline import FramePipeline, PipelineParams  # noqa: E402
from ohs.geometry import make_se3, so3_exp  # noqa: E402
from ohs.server.hub import Hub  # noqa: E402
from ohs.server.session import REWIND_BUDGET_S, ScanSession, _Kept, _scale_weights  # noqa: E402
from ohs.sim.nominal import nominal_calibration  # noqa: E402


def _depth(K: np.ndarray, w: int, h: int) -> np.ndarray:
    """A table 250 mm away with a 40 mm bump in the middle (mm, float32)."""
    vs, us = np.mgrid[0:h, 0:w].astype(np.float64)
    r = np.hypot(us - w / 2, vs - h / 2) / w
    return (250.0 - 40.0 * np.exp(-(r / 0.12) ** 2)).astype(np.float32)


@pytest.fixture(scope="module")
def pipe():
    p = FramePipeline(nominal_calibration((1280, 800)), PipelineParams(scale=0.5, use_imu=False, use_lasers=False))
    yield p
    p.close()


def test_rebuild_of_a_two_minute_scan_fits_the_budget(pipe, tmp_path):
    w, h = pipe.image_size
    small = _depth(pipe.K, w, h)[::2, ::2].astype(np.float16)
    sess = ScanSession(calib=pipe.calib, mode="markerless", rec_dir=tmp_path / "rec", project_id="p", scan_id="s",
                       cfg=Config(), hub=Hub(), use_imu=False, use_lasers=False)
    keep = [_Kept(100.0 + 0.1 * i, make_se3(so3_exp(np.array([0.0, 2e-4 * i, 0.0])), np.array([0.8 * i, 0.0, 0.0])),
                  np.zeros(0, np.int64), small, 0) for i in range(1200)]
    t0 = time.perf_counter()
    n = sess._rebuild(pipe, keep)
    dt = time.perf_counter() - t0
    assert 10 <= n <= int(REWIND_BUDGET_S / 0.012) + 1
    assert dt < 3.5, f"rebuild took {dt:.2f} s for {n} keyframes"
    V, F, N = pipe.mesh()
    assert len(F) > 5000  # rebuilt (and, with the rescaled weights, above the meshing threshold)
    assert pipe.tracker.n_integrated == pipe.markerless.n_integrated == n
    # the measured cost is kept for the next rewind, so the budget adapts to this PC
    assert sess._rebuild_cost_s == pytest.approx(dt / n, rel=0.5)


def test_weight_rescaling_writes_into_the_grid(pipe):
    tr = pipe.markerless
    tr.reset()
    w, h = pipe.image_size
    d = _depth(pipe.K, w, h)
    for _ in range(3):
        tr.integrate(d, None, np.eye(4))
    weights = tr.voxel_grid.attribute("weight").numpy()
    before = float(weights.max())
    _scale_weights(tr, 2.5)
    after = float(tr.voxel_grid.attribute("weight").numpy().max())
    assert before == pytest.approx(3.0) and after == pytest.approx(7.5)
