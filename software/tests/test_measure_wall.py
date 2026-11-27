"""``ohs.measure.wall`` (ENGINE_PLAN X16): a thin wall's thickness read from the camera frames, on a synthetic wall
seen from both sides (exact depth plus noise rendered analytically; no simulator, no scan)."""
from __future__ import annotations

import numpy as np
import pytest

from ohs.measure.wall import WallRefused, read_wall_thickness, wall_at_click, wall_words

L, H = 40.0, 25.0  # the wall's length (x) and height (z), centred at the origin; its faces at y = +-t/2
K = np.array([[700.0, 0.0, 320.0], [0.0, 700.0, 200.0], [0.0, 0.0, 1.0]])
SIZE = (640, 400)


def _grid(origin, a, b, na: int, nb: int):
    i, j = np.meshgrid(np.arange(na + 1), np.arange(nb + 1), indexing="ij")
    V = origin + i.reshape(-1, 1) / na * a + j.reshape(-1, 1) / nb * b
    idx = i * (nb + 1) + j
    F = []
    for p in range(na):
        for q in range(nb):
            F += [[idx[p, q], idx[p + 1, q], idx[p + 1, q + 1]], [idx[p, q], idx[p + 1, q + 1], idx[p, q + 1]]]
    return V, np.array(F)


def box_mesh(t_read: float):
    """A closed box 40 x t_read x 25 (a thin wall as the 1 mm mesh reads it: thicker than it is)."""
    hx, hy, hz = L / 2, t_read / 2, H / 2
    ex, ey, ez = np.array([2 * hx, 0, 0]), np.array([0, 2 * hy, 0]), np.array([0, 0, 2 * hz])
    faces = [(np.array([-hx, hy, -hz]), ez, ex), (np.array([-hx, -hy, -hz]), ex, ez),
             (np.array([-hx, -hy, hz]), ex, ey), (np.array([-hx, -hy, -hz]), ey, ex),
             (np.array([hx, -hy, -hz]), ey, ez), (np.array([-hx, -hy, -hz]), ez, ey)]
    Vs, Fs, off = [], [], 0
    for o, a, b in faces:
        V, F = _grid(o, a, b, max(1, int(round(np.linalg.norm(a)))), max(1, int(round(np.linalg.norm(b)))))
        Vs.append(V)
        Fs.append(F + off)
        off += len(V)
    return np.concatenate(Vs), np.concatenate(Fs)


def look_at(C, target=(0.0, 0.0, 0.0)):
    z = np.asarray(target, float) - C
    z /= np.linalg.norm(z)
    x = np.cross(z, [0.0, 0.0, 1.0])
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    T = np.eye(4)
    T[:3, :3] = np.column_stack([x, y, z])
    T[:3, 3] = C
    return T


def cameras(sides=(1, -1), n=6, dist=240.0):
    poses, k = {}, 0
    for sgn in sides:
        for a in np.linspace(-35, 35, n):
            r = np.radians(a)
            C = np.array([dist * np.sin(r) * 0.8, sgn * dist * np.cos(r) * 0.7, 120.0])
            poses[k] = look_at(C)
            k += 1
    return poses


def render_depth(T, t: float, noise: float, seed: int) -> np.ndarray:
    """Depth of the wall's face the camera faces (its two faces at y = +-t/2), exact plus Gaussian noise."""
    w, h = SIZE
    v, u = np.mgrid[0:h, 0:w]
    d = np.stack([(u - K[0, 2]) / K[0, 0], (v - K[1, 2]) / K[1, 1], np.ones_like(u, float)], -1) @ T[:3, :3].T
    C = T[:3, 3]
    yf = t / 2 if C[1] > 0 else -t / 2
    with np.errstate(divide="ignore", invalid="ignore"):
        s = (yf - C[1]) / d[..., 1]
    P = C + s[..., None] * d
    ok = (s > 0) & (np.abs(P[..., 0]) <= L / 2) & (np.abs(P[..., 2]) <= H / 2)
    z = np.where(ok, s, 0.0)  # d has z = 1 in the camera: s is the depth along z
    rng = np.random.default_rng(seed)
    return np.where(ok, z + rng.normal(0.0, noise, z.shape), 0.0).astype(np.float32)


def reading(t: float, t_read: float, poses, noise=0.2):
    V, F = box_mesh(t_read)
    depth = {i: render_depth(T, t, noise, i) for i, T in poses.items()}
    return read_wall_thickness(V, F, (0.0, t_read / 2, 0.0), poses=poses, K=K, image_size=SIZE, depth_range=(100, 600),
                               depth_of=lambda i: depth[i], boot=60)


@pytest.mark.parametrize("t", [0.5, 1.0, 2.0])
def test_a_wall_seen_from_both_sides_reads_its_thickness_and_at_most_holds_it(t):
    r = reading(t, t + 1.0, cameras())
    assert abs(r.reading_mm - t) < 0.05
    assert r.at_most_mm >= t and r.at_most_mm - t < 0.2
    assert r.frames[0] >= 3 and r.frames[1] >= 3 and abs(r.mesh_mm - (t + 1.0)) < 0.05
    assert r.words[0].startswith(f"At most {r.at_most_mm:.2f} mm thick here")
    assert "up to about 1 mm thinner" in r.words[1] and "calipers" in r.words[2]
    assert "±" not in " ".join(r.words)


def test_one_side_not_seen_is_refused():
    with pytest.raises(WallRefused, match="Only one side of this wall"):
        reading(1.0, 2.0, cameras(sides=(1,)))


def test_too_few_frames_on_a_side_is_refused():
    poses = cameras(n=6)
    poses = {k: T for k, T in poses.items() if k < 6 or k == 6}  # one frame on the second side
    with pytest.raises(WallRefused, match="too few camera frames"):
        reading(1.0, 2.0, poses)


def test_a_thick_wall_and_a_click_off_any_wall_are_refused_in_words():
    V, F = box_mesh(6.0)
    with pytest.raises(WallRefused, match="thicker than this reading is for"):
        wall_at_click(V, F, (0.0, 3.0, 0.0))
    V, F = box_mesh(2.0)
    with pytest.raises(WallRefused, match="not on the mesh"):
        wall_at_click(V, F, (0.0, 30.0, 0.0))


def test_the_words_are_one_sided():
    w = wall_words(1.23, (8, 9), 1.9)
    assert w[0] == "At most 1.23 mm thick here (read from 17 camera frames: 8 on one side, 9 on the other)."
    assert all("±" not in s for s in w) and "1.90 mm" in w[3]
