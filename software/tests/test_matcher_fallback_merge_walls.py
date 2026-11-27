"""The matcher's fallback from the graphics card to the processor in a merge and in "Make only the thin walls finer"
(ENGINE_PLAN R2-7, the two places tests/test_matcher_fallback_profile.py left open).

- **A merge** (``ohs.align.merge.merge_scans``) computes each scan's depth again through a ``FramePipeline`` and fuses
  it into one confidence model: each frame is fused with the factor of the matcher that made its depth, and the
  merge's report saves the fallback (``compute.fallbacks``: the scan, the frame, the stage, why) with a warning that
  names the scan. Without a fallback the report has no ``compute`` block and no such warning, as before.
- **The thin walls' step** (``ohs.app.fine_walls``) fuses the depth Follow the camera kept: a kept frame made after the
  fallback carries its matcher's factor (``hole_factor`` in its entry of the spool's index, only when it is not the
  model's own), and the finer model fuses it with that factor. A spool without the field (written before) loads
  and fuses as it does today.

No graphics card is needed: the stand-in "graphics-card matcher" of tests/test_matcher_fallback_profile.py.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

pytest.importorskip("open3d")

from test_matcher_fallback_profile import CPU, GPU, install_fake_gpu  # noqa: E402

N = 6


@pytest.fixture(scope="module")
def rec(tmp_path_factory):
    from recordings import scan_recording  # noqa: PLC0415

    return scan_recording(tmp_path_factory.mktemp("fbm") / "rec", n_frames=N, image_size=(640, 400),
                          realistic=True, seed=4, depth_every=0)


def _spy_factor(monkeypatch, cls) -> list:
    """The factor each ``integrate`` of ``cls`` fused with (the model's after the call: a frame's own is kept)."""
    used: list = []
    real = cls.integrate

    def integrate(self, *a, **kw):
        real(self, *a, **kw)
        used.append((self.hole_amp, self.hole_r_px))

    monkeypatch.setattr(cls, "integrate", integrate)
    return used


def _merge(rec, out, monkeypatch, fail_at):
    from ohs.align.merge import ScanInput, merge_scans  # noqa: PLC0415
    from ohs.app import pipeline as pl  # noqa: PLC0415
    from ohs.app.pipeline import PipelineParams  # noqa: PLC0415
    from ohs.app.process import ProcessParams  # noqa: PLC0415
    from ohs.io.recording import RecordingReader  # noqa: PLC0415

    install_fake_gpu(monkeypatch, fail_at=fail_at)
    used = _spy_factor(monkeypatch, pl.ConfidenceTsdf)
    gt = RecordingReader(rec).ground_truth_poses()
    rows = [(int(i), 0.0, True, T) for i, T in sorted(gt.items())]
    params = ProcessParams(pipeline=PipelineParams(recon="confidence"), export=("ply",))
    rep = merge_scans([ScanInput(rec, rows, name="Front"), ScanInput(rec, rows, name="Back")], out, params)
    return rep, used


def test_a_merge_fuses_each_frame_with_its_own_matchers_factor_and_says_the_fallback(rec, tmp_path, monkeypatch):
    from ohs.app.process import matcher_fallback_warning  # noqa: PLC0415
    from ohs.io.recording import RecordingReader  # noqa: PLC0415

    rep, used = _merge(rec, tmp_path / "out", monkeypatch, fail_at=3)
    assert used == [GPU] * 3 + [CPU] * (2 * N - 3), used
    idx = int(RecordingReader(rec).indices[3])
    fb = (rep.get("compute") or {}).get("fallbacks")
    assert fb == [{"scan": "Front", "frame": idx, "stage": "depth", "from": "graphics card (stand-in card)",
                   "to": "processor", "reason": "simulated device loss"}], rep.get("compute")
    said = [w for w in rep["warnings"] if "computed on the processor" in w]
    assert said == [matcher_fallback_warning({"frame": f'{idx} of "Front"', "reason": "simulated device loss"})]
    assert f'frame {idx} of "Front"' in said[0] and "noise model" in said[0]
    saved = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert saved["compute"]["fallbacks"] == fb and said[0] in saved["warnings"]


def test_a_merge_without_a_fallback_says_nothing(rec, tmp_path, monkeypatch):
    rep, used = _merge(rec, tmp_path / "out", monkeypatch, fail_at=None)
    assert used == [GPU] * (2 * N)
    assert "compute" not in rep and not any("computed on the processor" in w for w in rep["warnings"])


# --- the thin walls' step -------------------------------------------------------------------------------------------

def test_the_kept_depth_carries_the_factor_of_the_matcher_that_made_it(rec, tmp_path, monkeypatch):
    from ohs.app.fine_walls import FusedDepthSpool, read_spool  # noqa: PLC0415
    from ohs.app.pipeline import FramePipeline, PipelineParams  # noqa: PLC0415
    from ohs.io.recording import RecordingReader  # noqa: PLC0415

    for fail_at in (3, None):
        install_fake_gpu(monkeypatch, fail_at=fail_at)
        reader = RecordingReader(rec)
        folder = tmp_path / f"spool{fail_at}"
        with FramePipeline(reader.calibration(), PipelineParams(recon="confidence")) as pipe:
            spool = pipe.fused_spool = FusedDepthSpool(folder, pipe.markerless)
            pipe.add_imu(reader.imu_samples())
            for i in range(N):
                pipe.process(reader.frame(i))
            pipe.finish_start()  # the frames held back at the start are fused now, as process_recording does
            pipe.fused_spool = None
            spool.close([int(i) for i in reader.indices])
        head = read_spool(folder)
        assert head["model"]["hole_factor"] == list(GPU)
        got = [e.get("hole_factor") for e in sorted(head["entries"], key=lambda e: e["pos"])]
        assert len(got) == N
        if fail_at is None:  # as today: no field at all
            assert got == [None] * N and all(set(e) == {"pos", "sigma", "seen", "T", "index"} for e in head["entries"])
        else:
            assert got == [None] * 3 + [list(CPU)] * (N - 3), got


def _stage(tmp_path, holes):
    """A stage with a 1 mm wall (as tests/test_fine_walls_capacity.py makes it), its kept frames added with the
    factors ``holes`` (None: ``add`` called as before this change)."""
    from test_mesh_fine_walls import H, K, W, _views, _wall_mesh  # noqa: PLC0415

    from ohs.app.fine_walls import FusedDepthSpool  # noqa: PLC0415
    from ohs.track.export import save_mesh  # noqa: PLC0415
    from ohs.track.posegraph import write_trajectory  # noqa: PLC0415
    from ohs.track.recon_tsdf import ConfidenceTsdf  # noqa: PLC0415

    coarse = ConfidenceTsdf(K, (W, H), voxel_mm=1.0, trunc_mm=4.0, depth_min_mm=150.0, depth_max_mm=450.0,
                            backend="cpu", back_mm=2.0, opposite_mm=1.0, hole_factor=GPU)
    stage = tmp_path / "stage"
    spool = FusedDepthSpool(stage / "fused_depth", coarse)
    rows = []
    for k, (T, d) in enumerate(_views(1.0)):
        s = np.where(d > 0, 0.2, 0.0).astype(np.float32)
        if holes is None:
            spool.add(k, d, s, None, T)
        else:
            spool.add(k, d, s, None, T, hole=holes[k])
        rows.append((100 + k, 0.1 * k, True, T))
    spool.close([r[0] for r in rows])
    coarse.close()
    V, F = _wall_mesh(0.85)
    save_mesh(stage / "mesh.ply", V, F, None, quality=np.full(len(V), 0.2, np.float32),
              looks=np.full(len(V), 3.0, np.float32))
    (stage / "report.json").write_text(json.dumps({"mesh": {"voxel_mm": 1.0},
                                                   "params": {"reconstruction": {"opposite_mm": 1.0}}}))
    write_trajectory(stage / "trajectory.csv", rows)
    return stage, len(rows)


def test_the_finer_model_fuses_each_kept_frame_with_its_own_factor(tmp_path, monkeypatch):
    from ohs.app.fine_walls import make_fine_walls, read_spool  # noqa: PLC0415
    from ohs.track.recon_tsdf import ConfidenceTsdf  # noqa: PLC0415

    holes = [GPU, None] + [CPU] * 6  # None: a frame whose factor is not known (the model's)
    stage, n = _stage(tmp_path, holes)
    head = read_spool(stage / "fused_depth")
    assert [e.get("hole_factor") for e in head["entries"]] == [None, None] + [list(CPU)] * (n - 2)
    used = _spy_factor(monkeypatch, ConfidenceTsdf)
    rep = make_fine_walls(stage, stage / "fused_depth", out_dir=tmp_path / "fine", backend="cpu")
    assert rep["frames"]["fused"] == n
    assert used == [GPU] * 2 + [CPU] * (n - 2), used


def test_a_spool_without_the_field_fuses_as_today(tmp_path, monkeypatch):
    """A spool written before (no ``hole_factor`` in its entries; ``add`` without ``hole``) and one whose frames all
    carry the model's own factor are the same spool, and the finer model fuses every frame with the model's factor."""
    from ohs.app.fine_walls import make_fine_walls  # noqa: PLC0415
    from ohs.track.recon_tsdf import ConfidenceTsdf  # noqa: PLC0415

    old, n = _stage(tmp_path / "old", None)
    same, _ = _stage(tmp_path / "same", [GPU] * n)
    a = (old / "fused_depth" / "index.json").read_bytes()
    assert a == (same / "fused_depth" / "index.json").read_bytes()
    used = _spy_factor(monkeypatch, ConfidenceTsdf)
    rep = make_fine_walls(old, old / "fused_depth", out_dir=tmp_path / "fine", backend="cpu")
    assert rep["acted"] and used == [GPU] * n
