"""The newest frames held out of the tracking model (ENGINE_PLAN X4c; ``FusionParams.fuse_lag``, off by default).

Bracket seed 1 drifts at frames 151 to 166 although nothing per frame looks unusual: each frame lands a few tenths of
a mm off along the weakly held direction, and where the model there is young (new surface coming into view) the newest
frame is much of it, so the next frame registers against that frame's own error (frame 152: 1.63 mm off against the
real model, 0.73 against the same model without frame 151; ``ohs.track.markerless``, "The newest frame held out of the
model"). With the setting a frame goes into the tracking model only once the next ``fuse_lag`` frames have been
registered; its pose stays what ICP found.
"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import geometry as G  # noqa: E402
from ohs.track.markerless import FusionParams, MarkerlessTracker  # noqa: E402
from test_track_markerless import FPS, H, K, W, arc_trajectory, imu_predictor, render_depth  # noqa: E402

N = 8


@pytest.fixture(scope="module")
def scene():
    rng = np.random.default_rng(5)
    poses = arc_trajectory(36)[:N]
    gp = imu_predictor(poses, rng)
    depths = [render_depth(T, rng, box=True) for T in poses]
    rots = [None] + [gp.relative_rotation((k - 1) / FPS, k / FPS) for k in range(1, N)]
    truth = [G.invert_se3(poses[0]) @ T for T in poses]
    return depths, rots, truth


def _track(scene, params, detail=True):
    depths, rots, _ = scene
    tr = MarkerlessTracker(K, (W, H), params)
    tr.detail_mode = detail
    out = []
    for k in range(N):
        res = tr.track(depths[k], None, k / FPS, rots[k])
        assert res.ok
        out.append(res)
    return tr, out


@pytest.mark.parametrize("backend", ["open3d", "auto"])
@pytest.mark.parametrize("lag", [1, 2])
def test_a_frame_is_registered_against_the_model_without_the_newest_frames(scene, backend, lag):
    depths, rots, truth = scene
    tr, out = _track(scene, FusionParams(voxel_mm=2.0, model_backend=backend, fuse_lag=lag))
    assert tr.n_integrated == N - lag and len(tr._pending) == lag
    assert all(r.info["fuse_lag"] == min(lag, k) for k, r in enumerate(out) if k)
    # every frame's pose is what ICP finds from its prediction against the frames up to `lag` before it
    ref = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend=backend))
    for k in range(2, N):
        ref.rebuild([(depths[j], out[j].T_wc) for j in range(max(k - lag, 1))])
        T, info = ref.register(depths[k], out[k].info["T_pred"])
        assert T is not None and np.allclose(T, out[k].T_wc, atol=1e-6), (k, info)
    for k in range(N):
        assert np.linalg.norm(out[k].T_wc[:3, 3] - truth[k][:3, 3]) < 1.5
    # what is held is fused when the model is read out or a pose is given
    tr.extract_points()
    assert tr.n_integrated == N and not tr._pending
    tr2, _ = _track(scene, FusionParams(voxel_mm=2.0, model_backend=backend, fuse_lag=lag))
    tr2.integrate(depths[0], None, out[-1].T_wc)
    assert tr2.n_integrated == N + 1 and not tr2._pending
    tr2.rebuild([(depths[0], np.eye(4))])  # a rebuilt model forgets what was held
    assert tr2.n_integrated == 1 and not tr2._pending


@pytest.mark.parametrize("backend", ["open3d", "auto"])
def test_off_and_outside_detail_mode_a_frame_is_fused_as_it_is_tracked(scene, backend):
    _, off = _track(scene, FusionParams(voxel_mm=2.0, model_backend=backend))
    tr, smooth = _track(scene, FusionParams(voxel_mm=2.0, model_backend=backend, fuse_lag=1), detail=False)
    assert tr.n_integrated == N and not tr._pending
    for a, b in zip(off, smooth):
        assert np.array_equal(a.T_wc, b.T_wc) and "fuse_lag" not in b.info
    # without fuse_lag_detail_only it holds the newest frame back on a smooth part too
    tr, every = _track(scene, FusionParams(voxel_mm=2.0, model_backend=backend, fuse_lag=1, fuse_lag_detail_only=False),
                       detail=False)
    assert tr.n_integrated == N - 1 and every[-1].info["fuse_lag"] == 1


def test_fuse_lag_is_off_by_default_and_checked():
    p = FusionParams()
    assert p.fuse_lag == 0 and p.fuse_lag_detail_only is True
    for bad in (-1, 1.5, True):
        with pytest.raises(ValueError):
            FusionParams(fuse_lag=bad)
