"""Gyro cover across a loss (REVIEW F4).

While the tracker is lost it multiplies the frames' gyro rotations onto the last good pose, and a
frame with a gyro rotation relocalises around that product with the gyro's tight sigma
("gyro_reloc"). If a frame in between had no gyro rotation (the pipeline found no IMU cover for
its interval), the product misses that interval: it is not a prior. Such a frame recovers as one
without a gyro does ("last_good", ``rot_sigma_deg``), says ``gyro_prior: False`` and carries
``gyro_gap`` (the intervals without cover since the last good pose, and their seconds).

The scene and the IMU are those of test_track_markerless (sphere on a plane with a box, 16 frames
of the arc); "lost" frames get an empty depth map. CPU only (Open3D model).
"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import geometry as G  # noqa: E402
from ohs.track.markerless import FusionParams, MarkerlessTracker  # noqa: E402
from test_track_markerless import FPS, H, K, W, arc_trajectory, imu_predictor, render_depth  # noqa: E402

N = 16


def _run(no_depth: set[int], no_gyro: set[int], n: int = N, reset_at: int | None = None):
    """Track the arc; returns (results, the rot_sigma (rad) of each frame's first ICP or None, truth T_wc)."""
    rng = np.random.default_rng(1)
    poses = arc_trajectory(n)
    gp = imu_predictor(poses, rng)
    tr = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend="open3d", device="cpu"))
    icp, sigmas, res = tr._icp, [], []

    def spy(src, view, widen, rot_sigma_rad, *a, **kw):
        if sigmas[-1] is None:
            sigmas[-1] = float(rot_sigma_rad)
        return icp(src, view, widen, rot_sigma_rad, *a, **kw)

    tr._icp = spy
    try:
        for k, T in enumerate(poses):
            sigmas.append(None)
            R = gp.relative_rotation((k - 1) / FPS, k / FPS) if (k and k not in no_gyro) else None
            d = render_depth(T, rng, box=True)
            if k in no_depth:
                d = np.zeros_like(d)
            res.append(tr.track(d, None, k / FPS, R))
            if reset_at == k:
                tr.reset()
        truth = [G.invert_se3(poses[0]) @ T for T in poses]
        return res, sigmas, truth, tr
    finally:
        tr.close()


def _gyro_sigma(lost: int) -> float:
    p = FusionParams()
    return float(np.radians(p.gyro_sigma_deg * np.sqrt(lost + 1)))


def _wide_sigma() -> float:
    return float(np.radians(FusionParams().rot_sigma_deg))


def _close(res, truth, k, mm=3.0, deg=1.0):
    r, t = G.pose_error(res[k].T_wc, truth[k])
    assert t < mm and r < deg, (k, r, t)


def test_a_covered_loss_relocalises_with_the_gyro():
    """The control: every interval of the loss has its gyro rotation."""
    res, sig, truth, _ = _run(no_depth={5, 6}, no_gyro=set())
    assert [r.ok for r in res[4:9]] == [True, False, False, True, True]
    i = res[7].info
    assert i["candidate"] == "gyro_reloc" and i["gyro_prior"] is True and i["lost_frames"] == 2
    assert "gyro_gap" not in i and sig[7] == _gyro_sigma(2)
    _close(res, truth, 7)


@pytest.mark.parametrize("gap", [5, 6, 7], ids=["at-the-start", "in-the-middle", "at-the-end"])
def test_a_gap_in_the_cover_is_not_a_gyro_prior(gap):
    """Depth lost at frames 5, 6 and 7, no gyro rotation for one of the intervals 4-5, 5-6, 6-7; frame 8 has
    depth and a good gyro rotation for 7-8, yet the rotation since frame 4 is not known."""
    res, sig, truth, _ = _run(no_depth={5, 6, 7}, no_gyro={gap})
    assert [r.ok for r in res[4:9]] == [True, False, False, False, True]
    i = res[8].info
    assert i["candidate"] == "last_good" and i["gyro_prior"] is False and i["lost_frames"] == 3
    assert i["gyro_gap"] == {"intervals": 1, "s": pytest.approx(1.0 / FPS, abs=1e-6)}  # rounded to the us
    assert sig[8] == _wide_sigma()  # the no-gyro recovery's sigma, not the gyro's
    _close(res, truth, 8)  # it recovers all the same
    assert res[9].ok and res[9].info["candidate"] == "motion_model" and res[9].info["gyro_prior"] is True


def test_the_recovering_frame_without_a_gyro_rotation_is_as_before():
    """No gyro rotation for the recovering frame itself: the no-gyro recovery, as it always was (no gap reported:
    there is no gyro rotation that went unused)."""
    res, sig, _, _ = _run(no_depth={5, 6}, no_gyro={7})
    i = res[7].info
    assert res[7].ok and i["candidate"] == "last_good" and i["gyro_prior"] is False and "gyro_gap" not in i
    assert sig[7] == _wide_sigma()


def test_a_good_pose_after_a_gap_restores_the_gyro():
    """A gap, recovery without the gyro, then a new loss with full cover: the gyro relocalises it (the gap was
    forgotten at the good pose)."""
    res, sig, truth, _ = _run(no_depth={5, 6, 10}, no_gyro={5})
    assert res[7].ok and res[7].info["candidate"] == "last_good" and res[7].info["gyro_gap"]["intervals"] == 1
    assert [r.ok for r in res[8:12]] == [True, True, False, True]
    i = res[11].info
    assert i["candidate"] == "gyro_reloc" and i["gyro_prior"] is True and "gyro_gap" not in i
    assert sig[11] == _gyro_sigma(1)
    _close(res, truth, 11)


def test_a_reset_forgets_the_gap():
    res, _, _, tr = _run(no_depth={5, 6}, no_gyro={5}, n=7, reset_at=6)
    assert not res[6].ok and tr._uncovered == 0 and tr._uncovered_s == 0.0
