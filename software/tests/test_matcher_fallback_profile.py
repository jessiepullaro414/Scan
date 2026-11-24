"""The noise profile after the matcher falls back from the graphics card to the processor (REVIEW F7).

Each matcher has its own sigma model (``ohs.vision.depth.SIGMA_MODELS``); the confidence TSDF takes the
matcher's factor next to missing depth (``hole_amp``, ``hole_r_px``) out of a frame's sigma again. A frame
is now fused with the factor of the matcher that made its depth (``DepthEstimator.sigma_profile``), what was
fused before stays as it was, and the switch is saved: in the frame's row (``gpu_fallback``), in the
result's compute block (``fallbacks``) and as a warning.

No graphics card is needed: a stand-in "graphics-card matcher" (the processor's matcher with the graphics
card's sigma model) is installed where the estimator looks for one, and made to fail at a chosen frame.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs.vision import depth as depth_mod  # noqa: E402
from ohs.vision.depth import SIGMA_MODELS, DepthEstimator  # noqa: E402

GPU = (SIGMA_MODELS["gpu"].hole_amp, SIGMA_MODELS["gpu"].hole_r_px)
CPU = (SIGMA_MODELS["cpu"].hole_amp, SIGMA_MODELS["cpu"].hole_r_px)
N = 8


class FakeGpuMatcher:
    """What DepthEstimator calls on its GPU matcher, done by a processor twin with the graphics card's sigma
    model; every call from the ``fail_at``-th one on raises, as a lost device does."""

    device_name = "stand-in card"
    dev = None

    def __init__(self, est: DepthEstimator, fail_at: int | None):
        self.cpu = DepthEstimator(est.K, est.baseline, replace(est.params, backend="cpu"))
        self.cpu.backend = "gpu"  # sigma_z then uses the graphics card's model
        self.fail_at, self.calls = fail_at, 0

    def _call(self) -> None:
        self.calls += 1
        if self.fail_at is not None and self.calls > self.fail_at:
            raise RuntimeError("simulated device loss")

    def depth_sigma(self, ml, mr, left_r, right_r):
        self._call()
        disp, sig = self.cpu.disparity_with_sigma(left_r, right_r, sigma=True)
        depth = self.cpu.depth_from_disparity(disp)
        return None, depth, self.cpu.sigma_z(depth, sig)

    def depth(self, ml, mr, left_r, right_r):
        self._call()
        return self.cpu.depth_from_disparity(self.cpu.disparity(left_r, right_r))

    def disparity_sigma(self, ml, mr, left_r, right_r):
        self._call()
        return self.cpu.disparity_with_sigma(left_r, right_r, sigma=True)

    def disparity(self, ml, mr, left_r, right_r):
        self._call()
        return self.cpu.disparity(left_r, right_r)

    def device_depth(self):
        return None


def install_fake_gpu(monkeypatch, fail_at: int | None) -> None:
    """Every DepthEstimator made from now on (with a backend other than "cpu") gets the stand-in matcher."""
    def start(self):
        return FakeGpuMatcher(self, fail_at), "GPU: stand-in card"

    monkeypatch.setattr(DepthEstimator, "_start_gpu", start)


@pytest.fixture(scope="module")
def rec(tmp_path_factory):
    from recordings import scan_recording  # noqa: PLC0415

    return scan_recording(tmp_path_factory.mktemp("fb") / "rec", n_frames=N, image_size=(640, 400),
                          realistic=True, seed=4, depth_every=0)


def _pipeline_params():
    from ohs.app.pipeline import PipelineParams  # noqa: PLC0415

    return PipelineParams(recon="confidence")


def test_each_frame_is_fused_with_its_own_matchers_factor(rec, monkeypatch):
    from ohs.app.pipeline import FramePipeline  # noqa: PLC0415
    from ohs.io.recording import RecordingReader  # noqa: PLC0415

    install_fake_gpu(monkeypatch, fail_at=3)
    reader = RecordingReader(rec)
    with FramePipeline(reader.calibration(), _pipeline_params()) as pipe:
        m = pipe.markerless
        assert pipe.depth_estimator.backend == "gpu" and (m.hole_amp, m.hole_r_px) == GPU
        used = []
        real = m._integrate_cpu

        def spy(*a, **kw):
            used.append((m.hole_amp, m.hole_r_px))
            return real(*a, **kw)

        monkeypatch.setattr(m, "_integrate_cpu", spy)
        pipe.add_imu(reader.imu_samples())
        res = [pipe.process(reader.frame(i)) for i in range(N)]
        assert pipe.depth_estimator.backend == "cpu" and pipe.gpu_fallbacks == {"depth": "simulated device loss"}
    assert all(r.track.ok for r in res)
    assert res[3].extra["gpu_fallback"] == {"depth": "simulated device loss"}
    assert [r.extra.get("depth_profile") for r in res] == ["gpu"] * 3 + ["cpu"] * (N - 3)
    assert used == [GPU] * 3 + [CPU] * (N - 3), used  # the frame's own matcher's factor


def test_the_switch_is_in_the_saved_result(rec, tmp_path, monkeypatch):
    import json

    from ohs.app.process import ProcessParams, process_recording  # noqa: PLC0415

    install_fake_gpu(monkeypatch, fail_at=3)
    rep = process_recording(rec, tmp_path / "out", params=ProcessParams(pipeline=_pipeline_params()))
    rows = rep["per_frame"]
    assert rows[3]["gpu_fallback"] == {"depth": "simulated device loss"}
    assert all("gpu_fallback" not in r for k, r in enumerate(rows) if k != 3)
    fb = rep["params"]["compute"]["fallbacks"]
    assert fb == [{"frame": rows[3]["index"], "stage": "depth", "from": "graphics card (stand-in card)",
                   "to": "processor", "reason": "simulated device loss"}]
    assert rep["params"]["compute"]["depth"] == "cpu"
    said = [w for w in rep["warnings"] if "computed on the processor" in w]
    assert len(said) == 1 and f"frame {rows[3]['index']}" in said[0] and "simulated device loss" in said[0]
    assert "noise model" in said[0]
    saved = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert saved["params"]["compute"]["fallbacks"] == fb and said[0] in saved["warnings"]


def test_without_a_fallback_nothing_is_said(rec, tmp_path, monkeypatch):
    from ohs.app.process import ProcessParams, process_recording  # noqa: PLC0415

    install_fake_gpu(monkeypatch, fail_at=None)
    rep = process_recording(rec, tmp_path / "out", params=ProcessParams(pipeline=_pipeline_params()))
    assert rep["params"]["compute"]["fallbacks"] == [] and rep["params"]["compute"]["depth"] == "gpu"
    assert not any("gpu_fallback" in r for r in rep["per_frame"])
    assert not any("computed on the processor" in w for w in rep["warnings"])


def test_a_frames_factor_fuses_as_a_model_made_with_it():
    """``integrate(..., hole_factor=f)`` fuses the frame exactly as a model made with ``f`` does; the model's
    own factor given again changes nothing."""
    from test_track_recon_tsdf import H, W, plane_depth, poses, volume  # noqa: PLC0415

    rng = np.random.default_rng(5)
    T0, T1 = poses(2, 6.0)
    d0, d1 = plane_depth(T0, 0.3, rng), plane_depth(T1, 0.3, rng)
    d1[60:90, 100:160] = 0.0  # a hole, so the factor matters
    s = np.full((H, W), 0.4, np.float32)
    a = volume("cpu", hole_factor=GPU)
    a.integrate(d0, s, T0)
    a.integrate(d1, s, T1, hole_factor=CPU)
    b = volume("cpu", hole_factor=GPU)
    b.integrate(d0, s, T0)
    b.set_hole_factor(*CPU)
    b.integrate(d1, s, T1)
    one = volume("cpu", hole_factor=CPU)  # the second frame alone, in a model made for the processor's matcher
    one.integrate(d1, s, T1)
    alone = volume("cpu", hole_factor=GPU)
    alone.integrate(d1, s, T1, hole_factor=CPU)
    for x, y in ((a, b), (one, alone)):
        (kx, cx), (ky, cy) = x.download(), y.download()
        assert np.array_equal(kx, ky) and all(cx[c].tobytes() == cy[c].tobytes() for c in cx)
    c = volume("cpu", hole_factor=GPU)
    c.integrate(d1, s, T1, hole_factor=GPU)
    e = volume("cpu", hole_factor=GPU)
    e.integrate(d1, s, T1)
    (kc, cc), (ke, ce) = c.download(), e.download()
    assert np.array_equal(kc, ke) and all(cc[k].tobytes() == ce[k].tobytes() for k in cc)
    assert depth_mod.SIGMA_MODELS["cpu"].hole_r_px != depth_mod.SIGMA_MODELS["gpu"].hole_r_px
