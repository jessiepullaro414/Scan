"""ohs.measure.region: a fit's ± counts that neighbouring points err together.

A fused scan's errors come in patches a few mm across (VALIDATION.md, "The fits' ± on today's
engine"): a fit that takes its points as independent is too sure by the square root of the number
of points in a patch. The region measures that from its own residuals (``correlated_points``) and
widens the ± by its square root.

* Independent noise: the factor stays near 1, and the ± near the plain fit's.
* Noise in patches (white noise smoothed over about 2 mm): the ± grows to the real spread of the
  fitted offset, normal, radius and axis over many noise draws, and ±2σ covers it.
* A hole's answer has no factor; the result stays plain JSON.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from ohs.measure import fit_at
from ohs.measure.fit import fit_plane

H = 0.5  # mm, the grid's spacing
NOISE = 0.05  # mm, the noise's RMS (and the sigma given)
PATCH_PX = 2.0  # the smoothing's standard deviation in grid cells (1 mm): patches about 2 mm across
DRAWS = 24


def sheet(n: int, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """An ``n x n`` grid at spacing ``H`` with heights ``z`` (n, n), two triangles a cell."""
    xs = (np.arange(n) - (n - 1) / 2) * H
    X, Y = np.meshgrid(xs, xs, indexing="ij")
    V = np.stack([X, Y, z], axis=-1).reshape(-1, 3)
    idx = np.arange(n * n).reshape(n, n)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel(), idx[:-1, 1:].ravel()
    return V, np.concatenate([np.stack([a, b, c], axis=1), np.stack([a, c, d], axis=1)])


def field(shape, seed: int, patchy: bool) -> np.ndarray:
    """Noise of RMS ``NOISE``: independent per vertex, or smoothed into patches."""
    w = np.random.default_rng(seed).normal(size=shape)
    if patchy:
        w = gaussian_filter(w, PATCH_PX, mode="wrap")
        w /= w.std()
    return NOISE * w


def centre_click(V: np.ndarray, F: np.ndarray) -> tuple[int, np.ndarray]:
    c = V[F].mean(axis=1)
    f = int(np.argmin(np.linalg.norm(c[:, :2], axis=1)))
    return f, c[f]


def angle_deg(a, b) -> float:
    return math.degrees(math.atan2(float(np.linalg.norm(np.cross(a, b))), abs(float(np.dot(a, b)))))


def test_independent_noise_keeps_its_plus_minus():
    n = 81
    z = field((n, n), 0, patchy=False)
    V, F = sheet(n, z)
    S = np.full(len(V), NOISE)
    face, point = centre_click(V, F)
    r = fit_at(V, F, face, point, "plane", sigma=S)
    assert r.correlated_points is not None and 1.0 <= r.correlated_points < 2.0
    plain = fit_plane(V[np.unique(F[r.faces])], S[np.unique(F[r.faces])])
    k = r.fit.offset_sigma_mm / plain.offset_sigma_mm
    assert k == pytest.approx(math.sqrt(r.correlated_points), rel=1e-9)
    assert r.fit.normal_sigma_deg / plain.normal_sigma_deg == pytest.approx(k, rel=1e-9)


def test_noise_in_patches_widens_a_plane_to_its_real_spread():
    """Over many draws of patchy noise on a flat 40 mm sheet (the tolerance given, so that the region
    is the whole sheet every time): the plain ± would be about 7 times too small; the region's ±
    matches the spread of the fitted offset and normal, and ±2σ covers it."""
    n = 81
    off, off_s, nrm, nrm_s, ncs = [], [], [], [], []
    for seed in range(DRAWS):
        V, F = sheet(n, field((n, n), seed, patchy=True))
        S = np.full(len(V), NOISE)
        face, point = centre_click(V, F)
        r = fit_at(V, F, face, point, "plane", sigma=S, tolerance_mm=5 * NOISE)  # the whole sheet, every draw
        assert len(r.faces) >= 0.95 * len(F)
        off.append(float(r.fit.point[2] + (r.fit.normal[:2] @ r.fit.point[:2]) / r.fit.normal[2]))  # the plane's height at x = y = 0
        off_s.append(r.fit.offset_sigma_mm)
        nrm.append(angle_deg(r.fit.normal, (0.0, 0.0, 1.0)))
        nrm_s.append(r.fit.normal_sigma_deg)
        ncs.append(r.correlated_points)
    off, off_s, nrm, nrm_s = map(np.asarray, (off, off_s, nrm, nrm_s))
    assert np.median(ncs) > 20  # patches of about 2 mm at 4 points a mm²
    spread = float(np.sqrt(np.mean(off ** 2)))
    assert 0.6 < np.median(off_s) / spread < 1.7
    assert np.mean(np.abs(off) <= 2 * off_s) >= 0.85
    assert np.mean(nrm <= 2 * nrm_s) >= 0.85  # the normal's ± is an RMS angle, so 2 of them cover 95-98 %
    assert 0.6 < np.median(nrm_s) / float(np.sqrt(np.mean(nrm ** 2))) < 1.7


def tube(r: float, length: float, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A half tube (180° of the round) of radius ``r`` along x, ``z`` (k, m) radial offsets on a grid
    of spacing ``H`` along the length and round the arc."""
    k, m = z.shape
    x = (np.arange(k) - (k - 1) / 2) * H
    a = (np.arange(m) - (m - 1) / 2) * (H / r)
    X, A = np.meshgrid(x, a, indexing="ij")
    R = r + z
    V = np.stack([X, R * np.sin(A), R * np.cos(A)], axis=-1).reshape(-1, 3)
    idx = np.arange(k * m).reshape(k, m)
    p, q, s, t = idx[:-1, :-1].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel(), idx[:-1, 1:].ravel()
    return V, np.concatenate([np.stack([p, s, q], axis=1), np.stack([p, t, s], axis=1)])


def test_noise_in_patches_widens_a_cylinder_to_its_real_spread():
    """A Ø 16 mm half shaft, 30 mm long, with patchy radial noise: radius and axis."""
    r, length = 8.0, 30.0
    k, m = int(length / H) + 1, int(math.pi * r / H) + 1
    rad, rad_s, ax, ax_s = [], [], [], []
    for seed in range(DRAWS):
        V, F = tube(r, length, field((k, m), 100 + seed, patchy=True))
        S = np.full(len(V), NOISE)
        c = V[F].mean(axis=1)
        face = int(np.argmin(np.linalg.norm(c - (0.0, 0.0, r), axis=1)))
        res = fit_at(V, F, face, c[face], "cylinder", sigma=S, tolerance_mm=5 * NOISE)
        assert res.correlated_points > 10
        rad.append(res.fit.radius_mm - r)
        rad_s.append(res.fit.radius_sigma_mm)
        ax.append(angle_deg(res.fit.axis, (1.0, 0.0, 0.0)))
        ax_s.append(res.fit.axis_sigma_deg)
    rad, rad_s, ax, ax_s = map(np.asarray, (rad, rad_s, ax, ax_s))
    assert 0.6 < np.median(rad_s) / float(np.sqrt(np.mean(rad ** 2))) < 1.7
    assert np.mean(np.abs(rad) <= 2 * rad_s) >= 0.85
    assert np.mean(ax <= 2 * ax_s) >= 0.85


def test_the_factor_is_the_same_on_every_call():
    n = 61
    V, F = sheet(n, field((n, n), 7, patchy=True))
    S = np.full(len(V), NOISE)
    face, point = centre_click(V, F)
    a = fit_at(V, F, face, point, "plane", sigma=S)
    b = fit_at(V, F, face, point, "plane", sigma=S)
    assert a.correlated_points == b.correlated_points and a.fit.offset_sigma_mm == b.fit.offset_sigma_mm


def test_a_hole_has_no_factor_and_the_answer_is_plain_json():
    import json

    from test_measure_region import nearest_face, noisy, plate

    V, F, _ = plate(80.0, 80.0, 0.5, circles=[(0.0, 0.0, 12.5)])
    Vn, S = noisy(V, 3)
    face, point = nearest_face(Vn, F, (13.5, 0.0, 0.0))
    r = fit_at(Vn, F, face, point, "hole", sigma=S)
    assert r.correlated_points is None
    d = r.to_dict()
    assert json.loads(json.dumps(d)) == d and d["correlated_points"] is None
    face, point = nearest_face(Vn, F, (25.0, 25.0, 0.0))
    d = fit_at(Vn, F, face, point, "plane", sigma=S).to_dict()
    assert isinstance(d["correlated_points"], float) and d["correlated_points"] >= 1.0
