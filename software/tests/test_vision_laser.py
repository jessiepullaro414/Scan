"""Tests for ohs.vision.laser: sub-pixel "+" extraction, triangulation and the stereo check.

Synthetic images: lines with exactly known centre curves (pixel-area-integrated Gaussian
cross-section, optionally saturated, tilted, curved and gapped) over ~10k random dots and noise.
"""
import zlib
from dataclasses import dataclass, field, replace

import numpy as np
import pytest
from scipy.special import erf

from ohs import geometry as G
from ohs.types import LaserPlane
from ohs.vision.laser import (
    LaserLines,
    LaserParams,
    depth_check,
    draw_overlay,
    extract_laser_lines,
    is_line_name,
    laser_points,
    line_family,
    stereo_check,
    triangulate_plane,
)

W, H = 1280, 800
F, B = 720.0, 60.0
K_FULL = np.array([[F, 0, 639.5], [0, F, 399.5], [0, 0, 1.0]])
V_PLANE = LaserPlane("vertical", np.array([0.9473, 0.0, 0.3205]), -109.8)
H_PLANE = LaserPlane("horizontal", np.array([0.0, -0.9594, 0.2822]), -71.67)


# --------------------------------------------------------------------------- synthetic images
@dataclass
class Line:
    """Centre curve ``c(t) = c0 + tan(tilt) (t - t0) + curv/2 (t - t0)^2``.

    Vertical line: ``t`` = row v, ``c`` = column u. Horizontal line: ``t`` = u, ``c`` = v.
    """

    c0: float
    tilt_deg: float = 0.0
    curv: float = 0.0
    t0: float = 0.0
    sigma: float = 1.5  # perpendicular Gaussian sigma (px)
    peak: float = 220.0  # DN above the background (saturates when > ~235)
    extent: tuple = (-1e9, 1e9)
    gaps: list = field(default_factory=list)

    def c(self, t):
        dt = np.asarray(t, float) - self.t0
        return self.c0 + np.tan(np.radians(self.tilt_deg)) * dt + 0.5 * self.curv * dt * dt

    def slope(self, t):
        return np.tan(np.radians(self.tilt_deg)) + self.curv * (np.asarray(t, float) - self.t0)

    def present(self, t):
        t = np.asarray(t, float)
        ok = (t >= self.extent[0]) & (t <= self.extent[1])
        for a, b in self.gaps:
            ok &= ~((t >= a) & (t <= b))
        return ok

    def scaled(self, s):
        sc = lambda x: (x + 0.5) * s - 0.5  # noqa: E731  pixel-centre convention
        return Line(sc(self.c0), self.tilt_deg, self.curv / s, sc(self.t0), self.sigma * s, self.peak,
                    tuple(e * s for e in self.extent), [(a * s, b * s) for a, b in self.gaps])


def line_image(line: Line, n_t: int, n_c: int) -> np.ndarray:
    t = np.arange(n_t, dtype=float)
    c = line.c(t)[:, None]
    sig_c = line.sigma * np.sqrt(1.0 + line.slope(t) ** 2)[:, None]
    x = np.arange(n_c, dtype=float)[None, :]
    frac = 0.5 * (erf((x + 0.5 - c) / (np.sqrt(2) * sig_c)) - erf((x - 0.5 - c) / (np.sqrt(2) * sig_c)))
    return line.peak * sig_c * np.sqrt(2 * np.pi) * frac * line.present(t)[:, None]


def render_dots(w, h, rng, n=10000, sigma=(0.6, 0.9), amp=(80.0, 200.0)):
    img = np.zeros((h, w))
    u, v = rng.uniform(-3, w + 3, n), rng.uniform(-3, h + 3, n)
    s, a = rng.uniform(*sigma, n), rng.uniform(*amp, n)
    ui, vi, s2 = np.floor(u).astype(int), np.floor(v).astype(int), np.sqrt(2.0) * s
    for dy in range(-3, 4):
        y = vi + dy
        gy = 0.5 * (erf((y + 0.5 - v) / s2) - erf((y - 0.5 - v) / s2))
        for dx in range(-3, 4):
            x = ui + dx
            ok = (x >= 0) & (x < w) & (y >= 0) & (y < h)
            gx = 0.5 * (erf((x + 0.5 - u) / s2) - erf((x - 0.5 - u) / s2))
            np.add.at(img, (y[ok], x[ok]), (a * 2 * np.pi * s * s * gx * gy)[ok])
    return img


def render(vline, hline, rng, scale=1.0, dots=True, noise=2.5, bg=20.0, extra=None):
    w, h = int(W * scale), int(H * scale)
    img = np.full((h, w), bg)
    if dots:
        img += render_dots(w, h, rng, sigma=(0.6 * scale, 0.9 * scale) if scale < 1 else (0.6, 0.9))
    if vline is not None:
        img += line_image(vline, h, w)
    if hline is not None:
        img += line_image(hline, w, h).T
    if extra is not None:
        img += extra
    if noise > 0:
        img += rng.normal(0, noise, img.shape)
    return np.clip(np.round(img), 0, 255).astype(np.uint8)


def crossing(vline, hline):
    v = hline.c(vline.c0)
    for _ in range(100):
        v = hline.c(vline.c(v))
    return float(vline.c(v)), float(v)


def errors(pts, line, vertical):
    """Row-wise (vertical) / column-wise (horizontal) centre errors and the 'line present' mask."""
    t, c = (pts[:, 1], pts[:, 0]) if vertical else (pts[:, 0], pts[:, 1])
    return c - line.c(t), line.present(np.round(t))


def n_visible(line, vertical, scale=1.0, margin=12):
    """Number of rows (vertical) / columns (horizontal) where the line is present inside the image."""
    n_t, n_c = (int(H * scale), int(W * scale)) if vertical else (int(W * scale), int(H * scale))
    t = np.arange(n_t)
    c = line.c(t)
    return int(np.sum(line.present(t) & (c >= margin) & (c <= n_c - 1 - margin)))


def check_line(pts, line, vertical, max_rms, min_found, n_expected, false_tol=1.0, max_false=0.01):
    assert len(pts) >= min_found * n_expected, f"found {len(pts)} of ~{n_expected}"
    e, present = errors(pts, line, vertical)
    false = ~present | (np.abs(e) > false_tol)
    assert false.mean() < max_false, f"false-point rate {false.mean():.4f}"
    rms = float(np.sqrt(np.mean(e[~false] ** 2)))
    assert rms < max_rms, f"rms {rms:.3f} px"
    return rms, false.mean()


def dist_to_line(pts, line, vertical):
    t, c = (pts[:, 1], pts[:, 0]) if vertical else (pts[:, 0], pts[:, 1])
    return np.abs(c - line.c(t)) / np.sqrt(1.0 + line.slope(t) ** 2)


# --------------------------------------------------------------------------- extraction accuracy
@pytest.mark.parametrize("tilt", [0.0, 15.0, -30.0])
@pytest.mark.parametrize("curv", [0.0, 1 / 2500.0])
def test_clean_lines_subpixel(tilt, curv):
    rng = np.random.default_rng(1)
    vl = Line(c0=700.37, tilt_deg=tilt, curv=curv, t0=400, sigma=1.5, peak=200)
    hl = Line(c0=350.81, tilt_deg=-tilt, curv=-curv, t0=640, sigma=1.2, peak=180)
    img = render(vl, None, rng, dots=False, noise=0.0)
    lines = extract_laser_lines(img)
    rms, _ = check_line(lines.vertical, vl, True, max_rms=0.1, min_found=0.95, n_expected=n_visible(vl, True))
    assert rms < 0.02
    assert len(lines.horizontal) == 0
    img = render(None, hl, rng, dots=False, noise=0.0)
    lines = extract_laser_lines(img)
    rms, _ = check_line(lines.horizontal, hl, False, max_rms=0.1, min_found=0.95, n_expected=n_visible(hl, False))
    assert rms < 0.02
    assert len(lines.vertical) == 0


def test_steep_curved_line_is_split_between_families_without_duplicates():
    """A line sweeping from 17 to 58 deg off vertical: each point belongs to exactly one family."""
    rng = np.random.default_rng(2)
    ln = Line(c0=300.0, tilt_deg=np.degrees(np.arctan(0.3)), curv=1.3 / 799, t0=0.0, sigma=1.5, peak=400)
    lines = extract_laser_lines(render(ln, None, rng))
    v, h = lines.vertical, lines.horizontal
    assert len(v) > 300 and len(h) > 150
    for pts in (v, h):
        dperp = np.abs(pts[:, 0] - ln.c(pts[:, 1])) / np.sqrt(1 + ln.slope(pts[:, 1]) ** 2)
        assert np.sqrt(np.mean(dperp**2)) < 0.15 and dperp.max() < 1.0
    assert np.all(np.abs(ln.slope(v[:, 1])) < 1.1) and np.all(np.abs(ln.slope(h[:, 1])) > 0.9)
    gap = np.hypot(*(v[:, None, :] - h[None, :, :]).transpose(2, 0, 1)).min()
    assert gap > 1.5, "no point may be reported by both families"
    rows = np.union1d(np.round(v[:, 1]), np.round(h[:, 1]))
    assert len(rows) > 0.85 * 760  # no big hole where the families meet


SCENES = {
    # name: (vertical line, horizontal line)
    "tilt0": (Line(700.3, 0.0, t0=400, sigma=2.0, peak=600, gaps=[(150, 200), (500, 505)]),
              Line(300.7, 0.0, t0=640, sigma=1.8, peak=400, gaps=[(100, 160)])),
    "tilt15": (Line(690.6, -15.0, t0=400, sigma=1.5, peak=450, gaps=[(600, 650)]),
               Line(420.2, 15.0, t0=640, sigma=2.2, peak=300, gaps=[(900, 980)])),
    "tilt30": (Line(705.1, 30.0, t0=400, sigma=1.2, peak=300, gaps=[(60, 120)]),
               Line(380.4, -30.0, t0=640, sigma=1.6, peak=500, gaps=[(1000, 1050)])),
    "curved_wide_saturated": (Line(620.0, 10.0, curv=1 / 1500.0, t0=400, sigma=1.2, peak=150),
                              Line(420.0, -20.0, curv=-1 / 2500.0, t0=640, sigma=2.5, peak=900)),
}


@pytest.mark.parametrize("scale", [1.0, 0.5])
@pytest.mark.parametrize("scene", sorted(SCENES))
def test_lines_with_dots_noise_saturation(scene, scale):
    vl, hl = (ln.scaled(scale) for ln in SCENES[scene])
    rng = np.random.default_rng(zlib.crc32(scene.encode()) + int(scale * 10))
    img = render(vl, hl, rng, scale=scale)
    assert (img == 255).sum() > 100  # saturated cores present
    params = LaserParams() if scale == 1.0 else LaserParams().scaled(scale)
    lines = extract_laser_lines(img, params)
    n_v = n_visible(vl, True, scale, margin=int(12 * scale))
    n_h = n_visible(hl, False, scale, margin=int(12 * scale))
    check_line(lines.vertical, vl, True, max_rms=0.2, min_found=0.8, n_expected=n_v)
    check_line(lines.horizontal, hl, False, max_rms=0.2, min_found=0.8, n_expected=n_h)
    assert lines.vertical_strength.shape == (len(lines.vertical),)
    assert np.all(lines.vertical_strength >= params.min_contrast)
    # crossing zone excluded: nothing within 2/3 of the exclusion radius of the other line
    # wherever that line is actually present
    zone = 2.0 / 3.0 * params.cross_radius
    near_v = (dist_to_line(lines.vertical, hl, False) < zone) & hl.present(np.round(lines.vertical[:, 0]))
    near_h = (dist_to_line(lines.horizontal, vl, True) < zone) & vl.present(np.round(lines.horizontal[:, 1]))
    assert not near_v.any() and not near_h.any()
    # no points inside the gaps (1 px tolerance at the gap ends)
    for line, pts, vert in ((vl, lines.vertical, True), (hl, lines.horizontal, False)):
        t = pts[:, 1] if vert else pts[:, 0]
        for a, b in line.gaps:
            assert not np.any((t > a + 1) & (t < b - 1))


def test_crossing_zone_points_excluded_explicitly():
    rng = np.random.default_rng(7)
    vl = Line(640.2, 5.0, t0=400, sigma=1.5, peak=500)
    hl = Line(400.6, -8.0, t0=640, sigma=1.5, peak=500)
    img = render(vl, hl, rng)
    uc, vc = crossing(vl, hl)
    p = LaserParams()
    lines = extract_laser_lines(img, p)
    for pts in (lines.vertical, lines.horizontal):
        d = np.hypot(pts[:, 0] - uc, pts[:, 1] - vc)
        assert d.min() > 0.9 * p.cross_radius
    # without the exclusion the contaminated points come back (so the test is meaningful)
    raw = extract_laser_lines(img, LaserParams(cross_radius=0.0))
    d = np.hypot(raw.vertical[:, 0] - uc, raw.vertical[:, 1] - vc)
    assert np.sum(d < 0.9 * p.cross_radius) > 5


def test_orientation_separates_the_lines():
    """A 30-deg-tilted line belongs to exactly one family, even when it is the only line."""
    rng = np.random.default_rng(3)
    img = render(Line(600.0, 30.0, t0=400, sigma=1.5, peak=400), None, rng)
    lines = extract_laser_lines(img)
    assert len(lines.vertical) > 600 and len(lines.horizontal) == 0
    img = render(None, Line(400.0, -30.0, t0=640, sigma=1.5, peak=400), rng)
    lines = extract_laser_lines(img)
    assert len(lines.horizontal) > 900 and len(lines.vertical) == 0


def test_dot_pattern_alone_gives_no_lines():
    rng = np.random.default_rng(4)
    for scale in (1.0, 0.5):
        img = render(None, None, rng, scale=scale, noise=3.0)
        lines = extract_laser_lines(img, LaserParams().scaled(scale))
        assert len(lines) == 0


# --------------------------------------------------------------------------- two lines per family (four lasers)
PAIR_SCENES = {
    # name: ((vertical lines), (horizontal lines)); each family's two lines run apart (tilted, curved,
    # gapped) or cross each other at a shallow angle
    "apart": ((Line(470.3, 8.0, t0=400, sigma=1.5, peak=450, gaps=[(150, 190)]),
               Line(820.6, -5.0, curv=1 / 3000.0, t0=400, sigma=1.8, peak=350)),
              (Line(230.4, 3.0, t0=640, sigma=1.6, peak=400, gaps=[(900, 960)]),
               Line(590.2, -6.0, curv=-1 / 4000.0, t0=640, sigma=1.3, peak=500))),
    "crossing": ((Line(640.2, 7.0, t0=380, sigma=1.5, peak=450), Line(640.7, -6.0, t0=380, sigma=1.6, peak=380)),
                 (Line(300.4, 5.0, t0=900, sigma=1.5, peak=420), Line(300.1, -5.0, curv=1 / 5000.0, t0=900, sigma=1.4, peak=480))),
}


def render_pairs(vls, hls, rng, scale=1.0):
    w, h = int(W * scale), int(H * scale)
    extra = line_image(vls[1], h, w) + line_image(hls[1], w, h).T
    return render(vls[0], hls[0], rng, scale=scale, extra=extra)


def pair_check(pts, seg, lines, others, vertical, p, scale):
    """Both lines of a family found with single-line accuracy; nothing where they merge or near the
    other family's lines; every segment on one line only. Returns the rms per line."""
    t, c = (pts[:, 1], pts[:, 0]) if vertical else (pts[:, 0], pts[:, 1])
    e = np.stack([c - ln.c(t) for ln in lines])  # row-wise error against each line
    which = np.argmin(np.abs(e), axis=0)
    sep = np.abs(lines[0].c(t) - lines[1].c(t))
    # merged stretches are dropped: no point where the two lines are closer than the separation
    assert not np.any(sep < 0.8 * p.family_sep), f"{np.sum(sep < 0.8 * p.family_sep)} points in merged stretches"
    # no point of one line in the other's segment
    for s in np.unique(seg):
        assert len(np.unique(which[seg == s])) == 1, f"segment {s} holds points of both lines"
    # crossing zones with the other family excluded
    zone = 2.0 / 3.0 * p.cross_radius
    for o in others:
        near = (dist_to_line(pts, o, not vertical) < zone) & o.present(np.round(pts[:, 0] if vertical else pts[:, 1]))
        assert not near.any()
    n_t = int(H * scale) if vertical else int(W * scale)
    n_c = int(W * scale) if vertical else int(H * scale)
    tt = np.arange(n_t, dtype=float)
    rms = []
    for k, ln in enumerate(lines):
        mine = which == k
        ek = e[k, mine]
        present = ln.present(np.round(t[mine]))
        false = ~present | (np.abs(ek) > 1.0)
        assert false.mean() < 0.01, f"line {k}: false-point rate {false.mean():.4f}"
        r = float(np.sqrt(np.mean(ek[~false] ** 2)))
        assert r < 0.2, f"line {k}: rms {r:.3f} px"
        rms.append(r)
        # found: at least 85 % of the rows where the line is present, inside the image, clear of the
        # other line of its family and of the other family's lines
        ck = ln.c(tt)
        expect = ln.present(tt) & (ck >= 12 * scale) & (ck <= n_c - 1 - 12 * scale)
        expect &= np.abs(ck - lines[1 - k].c(tt)) > 1.5 * p.family_sep
        pts_t = np.stack([ck, tt], 1) if vertical else np.stack([tt, ck], 1)
        for o in others:
            expect &= dist_to_line(pts_t, o, not vertical) > 1.5 * p.cross_radius
        assert mine.sum() >= 0.85 * expect.sum(), f"line {k}: found {mine.sum()} of ~{expect.sum()}"
    return rms


@pytest.mark.parametrize("scale", [1.0, 0.5])
@pytest.mark.parametrize("scene", sorted(PAIR_SCENES))
def test_two_lines_per_family(scene, scale):
    """Four lasers: two lines of each family in the image, with the dots, noise and saturation."""
    vls, hls = ([ln.scaled(scale) for ln in fam] for fam in PAIR_SCENES[scene])
    rng = np.random.default_rng(zlib.crc32(scene.encode()) + 7 + int(scale * 10))
    img = render_pairs(vls, hls, rng, scale)
    base = LaserParams() if scale == 1.0 else LaserParams().scaled(scale)
    p = replace(base, lines_per_family=2)
    lines = extract_laser_lines(img, p)
    rms_v = pair_check(lines.vertical, lines.vertical_segment, vls, hls, True, p, scale)
    rms_h = pair_check(lines.horizontal, lines.horizontal_segment, hls, vls, False, p, scale)
    assert lines.vertical_strength.shape == (len(lines.vertical),) and np.all(lines.vertical_strength >= p.min_contrast)
    # segments are contiguous runs, so the polylines of draw_overlay are right
    for seg in (lines.vertical_segment, lines.horizontal_segment):
        starts = np.r_[True, seg[1:] != seg[:-1]]
        assert len(np.unique(seg)) == starts.sum()
    # no points inside the gaps
    for fam, pts, vert in ((vls, lines.vertical, True), (hls, lines.horizontal, False)):
        t = pts[:, 1] if vert else pts[:, 0]
        c = pts[:, 0] if vert else pts[:, 1]
        for ln in fam:
            on = np.abs(c - ln.c(t)) < 1.0
            for a, b in ln.gaps:
                assert not np.any(on & (t > a + 1) & (t < b - 1))
    print(f"{scene} x{scale}: rms vertical {rms_v}, horizontal {rms_h}")
    # one line per family (the default) finds only one of each pair in most rows: the test needs the option
    single = extract_laser_lines(img, base)
    assert len(single.vertical) < 0.75 * len(lines.vertical)


def test_line_family_and_family_lookup():
    """A line's name starts with its family ("vertical" / "horizontal"), optionally followed by "_..."."""
    assert line_family("vertical") == "vertical" and line_family("horizontal_2") == "horizontal"
    assert line_family("vertical_left") == "vertical"
    for bad in ("verticals", "dots", "vertical-2", "", "Horizontal"):
        with pytest.raises(KeyError):
            line_family(bad)
    assert is_line_name("vertical_2") and not is_line_name("dots")
    lines = LaserLines(np.ones((3, 2)), np.zeros((2, 2)), np.ones(3), np.ones(2))
    assert lines.get("vertical_2") is lines.vertical and lines.get("horizontal_7") is lines.horizontal
    assert lines.segments("vertical_2") is lines.vertical_segment
    assert LaserParams().lines_per_family == 1 and LaserParams().scaled(0.5).lines_per_family == 1


def test_params_scaled():
    half = LaserParams().scaled(0.5)
    assert half.ridge_sigma == pytest.approx(1.0) and half.window == 5 and half.min_segment == 10
    assert half.cross_radius == pytest.approx(7.5) and half.max_step == LaserParams().max_step
    with pytest.raises(ValueError):
        LaserParams().scaled(0)


# --------------------------------------------------------------------------- triangulation
def test_triangulate_plane_roundtrip():
    rng = np.random.default_rng(5)
    for plane in (V_PLANE, H_PLANE):
        # random points on the plane in front of the camera
        n = plane.normal
        a = np.cross(n, [0, 0, 1.0])
        a /= np.linalg.norm(a)
        b = np.cross(n, a)
        p0 = -plane.d * n
        X = p0 + rng.uniform(-150, 150, (300, 1)) * a + rng.uniform(-150, 150, (300, 1)) * b
        X = X[(X[:, 2] > 120) & (X[:, 2] < 600)]
        uv = G.project_points(X, K_FULL)
        P = triangulate_plane(uv, K_FULL, plane)
        assert np.allclose(P, X, atol=1e-6)
        assert np.allclose(plane.distance(P), 0, atol=1e-9)
    # rays parallel to the vertical plane / hitting it behind the camera -> NaN
    u_par = K_FULL[0, 2] - K_FULL[0, 0] * V_PLANE.normal[2] / V_PLANE.normal[0]
    P = triangulate_plane(np.array([[u_par, 400.0], [u_par - 50.0, 400.0], [800.0, 300.0]]), K_FULL, V_PLANE)
    assert np.isnan(P[0]).all() and np.isnan(P[1]).all() and np.isfinite(P[2]).all()
    assert triangulate_plane(np.zeros((0, 2)), K_FULL, V_PLANE).shape == (0, 3)


# --------------------------------------------------------------------------- stereo check
def project_right(X, K, baseline):
    uv = G.project_points(X, K)
    uv[:, 0] -= K[0, 0] * baseline / X[:, 2]
    return uv


def laser_on_surface(plane, surf_n, surf_d):
    """Two 3D points on the intersection line of a laser plane and a surface plane."""
    direction = np.cross(plane.normal, surf_n)
    A = np.stack([plane.normal, surf_n, direction])
    p0 = np.linalg.solve(A, [-plane.d, -surf_d, 0.0])
    return p0, direction / np.linalg.norm(direction)


def image_line(p0, direction, K, right=False, vertical=True):
    """Image line (Line params) of a 3D line, in the left or right rectified image."""
    X = p0 + np.array([-80.0, 80.0])[:, None] * direction
    uv = project_right(X, K, B) if right else G.project_points(X, K)
    (u0, v0), (u1, v1) = uv
    if vertical:
        return Line(c0=u0, tilt_deg=np.degrees(np.arctan((u1 - u0) / (v1 - v0))), t0=v0)
    return Line(c0=v0, tilt_deg=np.degrees(np.arctan((v1 - v0) / (u1 - u0))), t0=u0)


def test_stereo_check_rejects_left_only_line():
    rng = np.random.default_rng(6)
    surf_n = np.array([0.0, -0.3, 1.0]) / np.linalg.norm([0.0, -0.3, 1.0])  # slanted wall ~250 mm away
    surf_d = -250.0
    p0, dirv = laser_on_surface(V_PLANE, surf_n, surf_d)
    real_l = image_line(p0, dirv, K_FULL)
    real_r = image_line(p0, dirv, K_FULL, right=True)
    for ln in (real_l, real_r):
        ln.sigma, ln.peak, ln.extent = 1.6, 500.0, (0, 420)  # laser hits the wall in the upper half only
    fake = Line(c0=real_l.c(600.0) - 60.0, tilt_deg=real_l.tilt_deg, t0=600.0, sigma=1.6, peak=500.0, extent=(470, 790))
    left = render(real_l, None, rng, extra=line_image(fake, H, W))
    right = render(real_r, None, rng)
    planes = {"vertical": V_PLANE}

    p = LaserParams()
    unchecked = laser_points(left, right, K_FULL, B, planes, LaserParams(stereo_check=False))["vertical"]
    checked = laser_points(left, right, K_FULL, B, planes, p)["vertical"]
    v_unc = G.project_points(unchecked, K_FULL)[:, 1]
    v_chk = G.project_points(checked, K_FULL)[:, 1]
    assert np.sum(v_unc > 460) > 200, "the fake line must be detected without the stereo check"
    assert np.sum(v_chk > 440) == 0, "fake (left-only) line must be rejected"
    assert np.sum(v_chk < 420) > 0.85 * np.sum(v_unc < 420), "real line points must survive"
    # the surviving points lie on the wall (sub-mm)
    dist = np.abs(checked @ surf_n + surf_d)
    assert np.median(dist) < 0.1 and dist.max() < 0.5

    # explicit API: predicted right positions on / off the right ridge
    lines_r = extract_laser_lines(right, p)
    assert stereo_check(checked, lines_r.vertical, K_FULL, B).all()
    shifted = checked * np.array([1.0, 1.0, 1.05])  # wrong depth -> wrong disparity
    assert not stereo_check(shifted, lines_r.vertical, K_FULL, B).any()
    assert not stereo_check(np.full((3, 3), np.nan), lines_r.vertical, K_FULL, B).any()
    # precomputed lines give the same result
    lines_l = extract_laser_lines(left, p)
    again = laser_points(None, None, K_FULL, B, planes, p, lines_left=lines_l, lines_right=lines_r)["vertical"]
    assert np.allclose(again, checked)


def test_laser_points_both_planes_and_ranges():
    rng = np.random.default_rng(8)
    surf_n, surf_d = np.array([0.0, 0.0, 1.0]), -260.0  # fronto-parallel wall
    pv, dv = laser_on_surface(V_PLANE, surf_n, surf_d)
    ph, dh = laser_on_surface(H_PLANE, surf_n, surf_d)
    lines = {}
    for side in ("left", "right"):
        r = side == "right"
        lv, lh = image_line(pv, dv, K_FULL, r, True), image_line(ph, dh, K_FULL, r, False)
        lv.sigma = lh.sigma = 1.5
        lv.peak = lh.peak = 400.0
        lines[side] = (lv, lh)
    left, right = render(*lines["left"], rng), render(*lines["right"], rng)
    planes = {"vertical": V_PLANE, "horizontal": H_PLANE, "dots": V_PLANE}
    out = laser_points(left, right, K_FULL, B, planes)
    assert set(out) == {"vertical", "horizontal"}
    for name, P in out.items():
        assert len(P) > 500
        assert np.abs(P[:, 2] - 260.0).max() < 0.5  # on the wall
        assert np.abs(planes[name].distance(P)).max() < 1e-6  # on the laser plane
    out = laser_points(left, right, K_FULL, B, planes, LaserParams(max_depth_mm=250.0))
    assert all(len(P) == 0 for P in out.values())


def _rendered_frame(lasers):
    """A rendered (rectified, ideal lenses) sphere-on-plane frame of the simulator with 2 or 4
    lasers, its true depth and which laser lights each pixel."""
    from ohs.sim.nominal import nominal_calibration
    from ohs.sim.render import Renderer, RenderParams
    from ohs.sim.scene import sphere_on_plane
    from ohs.sim.trajectory import look_at

    cal = nominal_calibration(lasers=lasers)
    T = look_at([40.0, -150.0, 220.0], [0.0, 0.0, 30.0], up=(0.0, 0.0, 1.0))
    T[:3, 3] -= 30.0 * T[:3, 0]
    with Renderer(cal, None, sphere_on_plane(tags=False), RenderParams(seed=3)) as r:
        left, right, depth = r.render(T, rectified=True)
        aux = r.render_aux(T)
    return cal, left, right, depth, aux


def _truth_errors(out, cal, depth, aux):
    """Per kept point: whether the plane it was given lights its pixel (+-1 px), and its 3D distance
    to the true surface point on that ray."""
    K = cal.stereo.K_rect
    right_name, err = [], []
    for name, P in out.items():
        uv = G.project_points(P, K)
        ui, vi = np.rint(uv[:, 0]).astype(int), np.rint(uv[:, 1]).astype(int)
        lit = aux[f"lit_{name}"]
        hit = np.zeros(len(P), bool)
        for du in (-1, 0, 1):
            for dv in (-1, 0, 1):
                hit |= lit[np.clip(vi + dv, 0, H - 1), np.clip(ui + du, 0, W - 1)]
        right_name.append(hit)
        z = depth[np.clip(vi, 0, H - 1), np.clip(ui, 0, W - 1)].astype(float)
        err.append(np.abs(P[:, 2] - z) * np.linalg.norm(P, axis=1) / np.maximum(P[:, 2], 1e-9))
    return np.concatenate(right_name), np.concatenate(err)


def test_laser_points_tell_the_planes_of_a_family_apart():
    """Four lasers: each point of a family goes to the plane its stereo and depth checks pass on."""
    out_n, errs = {}, {}
    for lasers in (2, 4):
        cal, left, right, depth, aux = _rendered_frame(lasers)
        K, B = cal.stereo.K_rect, cal.stereo.baseline
        p = replace(LaserParams(), lines_per_family=lasers // 2)
        out = laser_points(left, right, K, B, cal.lasers, p, depth=depth)
        assert set(out) == set(cal.lasers)
        right_name, err = _truth_errors(out, cal, depth, aux)
        assert right_name.mean() >= 0.99, f"{lasers} lasers: {right_name.mean():.4f} on the right plane"
        out_n[lasers] = {k: len(v) for k, v in out.items()}
        errs[lasers] = err
        print(lasers, "lasers:", out_n[lasers], f"right plane {right_name.mean():.4f}, 3D error median "
              f"{np.median(err):.3f} p95 {np.percentile(err, 95):.3f} mm")
        if lasers == 4:
            # the stereo check alone (no dense depth) tells the vertical lines apart; the horizontal
            # ones run along the epipolar rows, so it hardly can (measured: ~96 % of fewer points)
            alone = laser_points(left, right, K, B, cal.lasers, p)
            for fam in ("vertical", "horizontal"):
                part = {k: v for k, v in alone.items() if k.startswith(fam)}
                rn, _ = _truth_errors(part, cal, depth, aux)
                print("4 lasers, stereo check only:", {k: len(v) for k, v in part.items()}, f"right plane {rn.mean():.4f}")
                if fam == "vertical":
                    assert rn.mean() >= 0.99
    for name in ("vertical_2", "horizontal_2"):
        assert out_n[4][name] > 300
    assert sum(out_n[4].values()) > 1.5 * sum(out_n[2].values())
    # the 3D error is like the 2-laser case
    assert np.median(errs[4]) < 1.5 * np.median(errs[2]) + 0.02
    assert np.percentile(errs[4], 95) < 1.5 * np.percentile(errs[2], 95) + 0.05


def test_laser_points_one_plane_per_family_is_unchanged():
    """With one plane per family the family test changes nothing (same points as by name)."""
    cal, left, right, depth, _ = _rendered_frame(2)
    K, B = cal.stereo.K_rect, cal.stereo.baseline
    a = laser_points(left, right, K, B, cal.lasers, depth=depth)
    renamed = {"vertical_left": LaserPlane("vertical_left", cal.lasers["vertical"].normal, cal.lasers["vertical"].d),
               "horizontal": cal.lasers["horizontal"]}
    b = laser_points(left, right, K, B, renamed, depth=depth)
    assert np.array_equal(a["vertical"], b["vertical_left"]) and np.array_equal(a["horizontal"], b["horizontal"])


# --------------------------------------------------------------------------- preview
def test_draw_overlay():
    rng = np.random.default_rng(9)
    vl, hl = Line(500.0, 0.0, t0=400, sigma=1.5, peak=300), Line(300.0, 0.0, t0=640, sigma=1.5, peak=300)
    img = render(vl, hl, rng)
    lines = extract_laser_lines(img)
    bgr = draw_overlay(img, lines)
    assert bgr.shape == (H, W, 3) and bgr.dtype == np.uint8
    col = bgr[100:700, 498:503].reshape(-1, 3).astype(int)
    assert np.any((col[:, 2] > 200) & (col[:, 1] < 150))  # red vertical polyline drawn
    row = bgr[298:303, 100:1200].reshape(-1, 3).astype(int)
    assert np.any((row[:, 1] > 200) & (row[:, 2] < 150))  # green horizontal polyline drawn
    # points without segment labels are painted as pixels
    bare = LaserLines(lines.vertical, np.zeros((0, 2)), lines.vertical_strength, np.zeros(0))
    assert draw_overlay(img, bare).shape == (H, W, 3)
    with pytest.raises(KeyError):
        lines.get("diagonal")


def test_depth_check_drops_points_off_the_dense_surface():
    # a wall 300 mm away; the dense depth is wrong along row 400 (a horizontal laser line runs along the
    # epipolar rows) and missing in a patch around column 900
    depth = np.full((H, W), 300.0, np.float32)
    depth[398:403, :] = 310.0
    depth[:, 880:921] = 0.0
    sig = np.full((H, W), 0.6, np.float32)

    def at(u, v, z):
        return np.array([(u - K_FULL[0, 2]) * z / F, (v - K_FULL[1, 2]) * z / F, z])

    pts = np.array([at(200, 200, 300.4),  # on the surface: kept
                    at(300, 200, 307.0),  # a glint 7 mm off: dropped
                    at(500, 400, 300.2),  # on the line, whose own depth is wrong: kept (the depth beside it agrees)
                    at(900, 600, 330.0),  # no dense depth anywhere around it: kept
                    at(600, 600, 302.0)])  # 2 mm off, within 4 sigma (2.4 mm): kept
    assert depth_check(pts, depth, K_FULL, sig).tolist() == [True, False, True, True, True]
    # without a sigma map: 0.25 % of the depth (0.75 mm) times 4 = 3 mm
    assert depth_check(pts, depth, K_FULL).tolist() == [True, False, True, True, True]
    assert depth_check(pts[[1]], depth, K_FULL, sig, k=20.0).tolist() == [True]
