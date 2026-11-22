"""A frame that slides into another fit (ENGINE_PLAN X4c; ``FusionParams.slip_check_mm``, off by default).

On the hole plate 120 mm above its floor (seed 0) frame 52's ICP slid 6.7 mm from its prediction along the weakly
held direction and stopped 5.1 mm off the truth, while from the truth, from the last pose without the carried step or
from the prediction without the coarsest level it stays within 0.2 to 0.9 mm, where the model agrees better with
the frame (``ohs.track.markerless``, "A frame that slides into another fit"). Here the slide is made by hand: the
first registration of one frame is moved along its weakest translation, as ICP moved frame 52.
"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("open3d")

import ohs.track.markerless as M  # noqa: E402
from ohs import geometry as G  # noqa: E402
from ohs.track.markerless import FusionParams, MarkerlessTracker  # noqa: E402
from test_track_markerless import FPS, H, K, W, arc_trajectory, imu_predictor, render_depth  # noqa: E402

N = 8  # frames tracked before the one that slides


@pytest.fixture(scope="module")
def scene():
    rng = np.random.default_rng(3)
    poses = arc_trajectory(36)[:N + 1]
    gp = imu_predictor(poses, rng)
    depths = [render_depth(T, rng, box=True) for T in poses]
    rots = [None] + [gp.relative_rotation((k - 1) / FPS, k / FPS) for k in range(1, N + 1)]
    truth = [G.invert_se3(poses[0]) @ T for T in poses]
    return depths, rots, truth


def _run(scene, params, slide_mm=0.0):
    """Track the scene; the last frame's first registration moved ``slide_mm`` along its weakest translation."""
    depths, rots, truth = scene
    tr = MarkerlessTracker(K, (W, H), params)
    for k in range(N):
        assert tr.track(depths[k], None, k / FPS, rots[k]).ok
    calls = []
    orig = tr._icp

    def icp(*a, **kw):
        out = orig(*a, **kw)
        calls.append(kw.get("skip_coarse", 0))
        if len(calls) == 1 and slide_mm and out.trans_axis is not None:
            out.T_delta = out.T_delta.copy()
            out.T_delta[:3, 3] += slide_mm * out.trans_axis
        return out

    tr._icp = icp
    res = tr.track(depths[N], None, N / FPS, rots[N])
    assert res.ok
    assert np.array_equal(tr._T_last, res.T_wc)  # the kept pose is what the next prediction starts from
    return res, float(np.linalg.norm(res.T_wc[:3, 3] - truth[N][:3, 3])), calls


@pytest.mark.parametrize("backend", ["open3d", "auto"])
def test_a_frame_that_slid_is_registered_again_and_the_fit_the_model_agrees_with_kept(scene, backend):
    on = FusionParams(voxel_mm=2.0, model_backend=backend, slip_check_mm=3.0)
    off = FusionParams(voxel_mm=2.0, model_backend=backend)
    res_off, err_off, calls_off = _run(scene, off, slide_mm=6.0)
    assert err_off > 4.0 and "slip_check" not in res_off.info and len(calls_off) == 1  # off: the slide stays
    res, err, calls = _run(scene, on, slide_mm=6.0)
    s = res.info["slip_check"]
    assert abs(s["along_mm"]) > 3.0 and s["kept"] in ("rest", "fine") and s["moved_mm"] > 3.0
    assert set(s["costs"]) == {"first", "rest", "fine"} and s["costs"][s["kept"]] < s["costs"]["first"] - 0.05
    assert err < 1.5 and calls == [0, 0, 1]  # registered again from the rest start and from the prediction, finer
    one = FusionParams(voxel_mm=2.0, model_backend=backend, slip_check_mm=3.0, slip_starts=("rest",))
    res1, err1, calls1 = _run(scene, one, slide_mm=6.0)
    assert res1.info["slip_check"]["kept"] == "rest" and err1 < 1.5 and calls1 == [0, 0]


@pytest.mark.parametrize("backend", ["open3d", "auto"])
def test_a_frame_that_did_not_slide_that_far_is_tracked_as_without_the_check(scene, backend):
    on = FusionParams(voxel_mm=2.0, model_backend=backend, slip_check_mm=3.0)
    off = FusionParams(voxel_mm=2.0, model_backend=backend)
    a, _, calls_a = _run(scene, on)
    b, _, _ = _run(scene, off)
    assert "slip_check" not in a.info and calls_a == [0] and np.array_equal(a.T_wc, b.T_wc)
    # a slide within the limit is left alone too
    a, _, calls_a = _run(scene, FusionParams(voxel_mm=2.0, model_backend=backend, slip_check_mm=8.0), slide_mm=6.0)
    b, _, _ = _run(scene, off, slide_mm=6.0)
    assert "slip_check" not in a.info and calls_a == [0] and np.array_equal(a.T_wc, b.T_wc)


def test_a_second_fit_the_model_agrees_with_no_better_leaves_the_first(scene, monkeypatch):
    """The other starts' results are kept only when the model agrees with them by more than slip_margin."""
    on = FusionParams(voxel_mm=2.0, model_backend="open3d", slip_check_mm=3.0, slip_margin=1e9)
    res, err, calls = _run(scene, on, slide_mm=6.0)
    assert res.info["slip_check"]["kept"] == "first" and err > 4.0 and len(calls) == 3


def test_slip_check_is_off_by_default_and_its_settings_are_checked():
    p = FusionParams()
    assert p.slip_check_mm is None and p.slip_starts == ("rest", "fine") and M.SLIP_STARTS == ("rest", "fine")
    assert FusionParams(slip_starts="rest").slip_starts == ("rest",)
    for bad in ({"slip_check_mm": 0.0}, {"slip_check_mm": -1.0}, {"slip_margin": -0.1}, {"slip_starts": ()},
                {"slip_starts": ("rest", "zero")}):
        with pytest.raises(ValueError):
            FusionParams(**bad)
