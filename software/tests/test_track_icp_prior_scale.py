"""The ICP's pose priors are weighed against the data with the residual sigma once (REVIEW F3).

``MarkerlessTracker._icp`` minimises, per Gauss-Newton step, the normalised objective

    sum_i w_i r_i^2 / icp_sigma^2  +  |rot|^2 / rot_sigma^2  +  |t|^2 / trans_sigma^2

(``w_i`` the Huber weights). It used to divide the data by ``icp_sigma^2`` and also multiply the
priors by it, which weighed the priors by ``icp_sigma^4`` instead of ``icp_sigma^2``; the default
sigma of 1 mm hid it.

The case: 121 source points on an 11 x 11 grid symmetric about the optical axis, 1 mm behind a
plane facing the camera, a translation prior of 1 mm. Only the translation along the normal is
seen; its closed form is ``t = -n / (n + icp_sigma^2 / trans_sigma^2)``. The real ``_icp`` runs on
a hand-made ray-cast view of the plane (``_ModelView``): the tracker's ICP needs nothing else.
"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs.track.markerless import FusionParams, MarkerlessTracker, _ModelView  # noqa: E402

W, H, F = 64, 48, 60.0
K = np.array([[F, 0.0, W / 2 - 0.5], [0.0, F, H / 2 - 0.5], [0.0, 0.0, 1.0]])
PLANE_Z = 300.0


def _plane_view() -> _ModelView:
    vs, us = np.mgrid[0:H, 0:W]
    rays = np.stack([(us.ravel() - K[0, 2]) / F, (vs.ravel() - K[1, 2]) / F, np.ones(W * H)], axis=1)
    V = rays * PLANE_Z
    N = np.tile([0.0, 0.0, -1.0], (W * H, 1))
    return _ModelView(T_wc=np.eye(4), valid=np.ones(W * H, dtype=bool), K=K, size=(W, H), dense=(V, N))


def _points_behind_the_plane(gap_mm: float = 1.0) -> list[tuple[np.ndarray, np.ndarray]]:
    g = np.arange(-25.0, 25.1, 5.0)
    x, y = np.meshgrid(g, g)
    P = np.stack([x.ravel(), y.ravel(), np.full(x.size, PLANE_Z + gap_mm)], axis=1)
    return [(P, np.tile([0.0, 0.0, -1.0], (len(P), 1)))]


@pytest.mark.parametrize("sigma", [0.5, 1.0, 2.0])
def test_the_translation_prior_is_weighed_by_sigma_squared(sigma):
    src = _points_behind_the_plane()
    n = len(src[0][0])
    assert n == 121
    tr = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend="open3d", device="cpu",
                                                   icp_sigma_mm=sigma, trans_sigma_mm=1.0))
    try:
        out = tr._icp(src, _plane_view(), 1.0, np.radians(5.0), extra_coarse=False)
    finally:
        tr.close()
    want = -n / (n + sigma ** 2 / 1.0 ** 2)  # -0.997938 at 0.5, -0.991803 at 1, -0.968000 at 2
    T = out.T_delta
    assert out.ok_numeric and out.n_inliers == n
    assert T[2, 3] == pytest.approx(want, abs=1e-9), (T[2, 3], want)
    assert np.abs(T[:2, 3]).max() < 1e-12 and np.abs(T[:3, :3] - np.eye(3)).max() < 1e-12
