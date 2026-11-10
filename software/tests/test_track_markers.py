"""Tests for ohs.track.markers.MarkerTracker on synthetic rectified stereo pairs.

Planar tag36h11 tags (``cv2.aruco.generateImageMarker`` on white label paper) and textured
background planes are warped into both rectified images with homographies for known 3D poses,
rendered 3x supersampled and area-downsampled (anti-aliasing) with mild sensor noise.
"""
import cv2
import numpy as np
import pytest

from ohs import geometry as G
from ohs.track.markers import MarkerParams, MarkerTracker, tag_corners_local

W, H, FX, BASELINE = 1280, 800, 720.0, 60.0
K = np.array([[FX, 0, W / 2 - 0.5], [0, FX, H / 2 - 0.5], [0, 0, 1.0]])
TAG_MM = 20.0
SS = 3  # supersampling factor
DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
PPC = 16  # texture pixels per tag cell


def _plane_pose(origin, rot_x_deg=0.0, rot_z_deg=0.0) -> np.ndarray:
    R = G.so3_exp(np.array([np.radians(rot_x_deg), 0, 0])) @ G.so3_exp(np.array([0, 0, np.radians(rot_z_deg)]))
    return G.make_se3(R, np.asarray(origin, dtype=float))


def scene_tags() -> dict[int, np.ndarray]:
    """Tag id -> pose T_s_tag: six on the ground plane, three on a 35 deg ramp (3D structure)."""
    tags = {}
    for k, (x, y) in enumerate([(-50, -30), (0, -35), (50, -30), (-45, 20), (5, 15), (55, 25)]):
        tags[k] = _plane_pose((x, y, 0.0), rot_z_deg=12.0 * (k - 2.5))
    ramp = G.make_se3(G.so3_exp(np.array([np.radians(35.0), 0, 0])), [0, 60, 0])
    for j, x in enumerate((-40, 0, 40)):
        tags[10 + j] = ramp @ _plane_pose((x, 20.0, 0.0), rot_z_deg=-8.0 * j)
    return tags


def tag_texture(i: int) -> tuple[np.ndarray, float]:
    """Tag image with a 1-cell white margin and its physical edge length (mm)."""
    m = cv2.aruco.generateImageMarker(DICT, i, 8 * PPC, borderBits=1)
    return cv2.copyMakeBorder(m, PPC, PPC, PPC, PPC, cv2.BORDER_CONSTANT, value=255).astype(np.float32), TAG_MM * 10 / 8


def background_texture(seed: int, n: int = 512) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = cv2.GaussianBlur(rng.normal(size=(n, n)).astype(np.float32), (0, 0), 3.0)
    return (150 + 25 * noise / noise.std()).astype(np.float32)


def _warp_layer(img, tex, size_mm, T_c_plane, Kss):
    """Composite a square texture of edge ``size_mm`` centred on the plane origin into ``img``."""
    n = tex.shape[0]
    S = np.array([[size_mm / n, 0, 0.5 * size_mm / n - size_mm / 2],
                  [0, -size_mm / n, size_mm / 2 - 0.5 * size_mm / n], [0, 0, 1.0]])
    Hm = Kss @ np.column_stack([T_c_plane[:3, 0], T_c_plane[:3, 1], T_c_plane[:3, 3]]) @ S
    quad = np.array([[-0.5, -0.5, 1], [n - 0.5, -0.5, 1], [n - 0.5, n - 0.5, 1], [-0.5, n - 0.5, 1]]) @ Hm.T
    if np.any(quad[:, 2] <= 0):
        return
    q = quad[:, :2] / quad[:, 2:]
    x0, y0 = np.floor(q.min(0)).astype(int) - 2
    x1, y1 = np.ceil(q.max(0)).astype(int) + 2
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, img.shape[1]), min(y1, img.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    Hr = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1.0]]) @ Hm
    size = (x1 - x0, y1 - y0)
    col = cv2.warpPerspective(tex, Hr, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    cov = cv2.warpPerspective(np.ones_like(tex), Hr, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                              borderValue=0)
    roi = img[y0:y1, x0:x1]
    roi *= 1.0 - cov
    roi += col  # premultiplied: col is already tex * coverage at the boundary


def add_dots(img: np.ndarray, rng, n: int = 10000, amp=(100.0, 150.0), fwhm=(2.0, 3.0)) -> None:
    """Add ``n`` Gaussian dots (projector pattern) with random position, amplitude and FWHM (px), in place."""
    h, w = img.shape
    xy = rng.uniform([0, 0], [w, h], size=(n, 2))
    a = rng.uniform(*amp, size=n)
    sig = rng.uniform(*fwhm, size=n) / 2.355
    off = np.arange(-3, 4)
    ix = np.floor(xy[:, 0]).astype(int)[:, None, None] + off[None, None, :]
    iy = np.floor(xy[:, 1]).astype(int)[:, None, None] + off[None, :, None]
    val = a[:, None, None] * np.exp(-((ix - xy[:, 0, None, None]) ** 2 + (iy - xy[:, 1, None, None]) ** 2)
                                    / (2 * sig[:, None, None] ** 2))
    ix, iy = np.broadcast_arrays(ix, iy)
    inside = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
    np.add.at(img, (iy[inside], ix[inside]), val[inside].astype(img.dtype))


def render_view(T_sc: np.ndarray, tags: dict[int, np.ndarray], rng, hide=(), K: np.ndarray = K,
                size: tuple[int, int] = (W, H), levels: tuple[float, float] = (0.0, 255.0),
                dots: int = 0) -> np.ndarray:
    """Rectified image of the scene from camera pose ``T_sc`` (camera -> scene).

    ``levels`` maps paper black/white (0/255) to these grey levels (low ambient light = low tag
    contrast); ``dots`` adds that many projector dots (2-3 px FWHM, 100-150 DN) over the field.
    """
    Kss = K.copy()
    Kss[:2] *= SS
    Kss[0, 2] = K[0, 2] * SS + (SS - 1) / 2  # full-res pixel u -> supersampled SS*u + (SS-1)/2
    Kss[1, 2] = K[1, 2] * SS + (SS - 1) / 2
    w, h = size
    img = np.full((h * SS, w * SS), 60.0, np.float32)
    T_cs = G.invert_se3(T_sc)
    _warp_layer(img, background_texture(1), 400.0, T_cs, Kss)
    ramp = G.make_se3(G.so3_exp(np.array([np.radians(35.0), 0, 0])), [0, 60, 0]) @ _plane_pose((0, 30, 0))
    _warp_layer(img, background_texture(2, 256), 160.0, T_cs @ ramp, Kss)
    for i, T_st in tags.items():
        if i in hide:
            continue
        tex, size = tag_texture(i)
        _warp_layer(img, tex, size, T_cs @ T_st, Kss)
    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    img = levels[0] + img * ((levels[1] - levels[0]) / 255.0)
    if dots:
        add_dots(img, rng, dots, fwhm=(2.0 * w / W, 3.0 * w / W))  # dots shrink with the image
    img += rng.normal(scale=2.0, size=img.shape).astype(np.float32)
    return np.clip(img, 0, 255).astype(np.uint8)


def render_pair(T_sc, tags, rng, hide=(), hide_right=False, K: np.ndarray = K, size: tuple[int, int] = (W, H),
                levels: tuple[float, float] = (0.0, 255.0), dots: int = 0):
    T_s_right = T_sc @ G.make_se3(t=[BASELINE, 0.0, 0.0])  # right camera centre at +B along x_left
    left = render_view(T_sc, tags, rng, hide, K, size, levels, dots)
    right = render_view(T_s_right, {} if hide_right else tags, rng, hide, K, size, levels, dots)
    return left, right


def look_at(eye, target):
    z = np.asarray(target, float) - eye
    z /= np.linalg.norm(z)
    x = np.cross(z, [0, 0, 1.0])
    x /= np.linalg.norm(x)
    return G.make_se3(np.stack([x, np.cross(z, x), z], axis=1), eye)


def trajectory(n: int = 10) -> list[np.ndarray]:
    poses = []
    for k in range(n):
        s = k / (n - 1)
        az, el, dist = np.radians(-20 + 40 * s), np.radians(60 - 10 * s), 290 - 40 * s
        target = np.array([-30 + 20 * s, 10.0, 5.0])  # aim left of the cluster (stereo overlap)
        eye = target + dist * np.array([np.cos(el) * np.sin(az), -np.cos(el) * np.cos(az), np.sin(el)])
        poses.append(look_at(eye, target))
    return poses


def true_corners_w(tags, T_sc0) -> dict[int, np.ndarray]:
    T_ws = G.invert_se3(T_sc0)
    return {i: G.transform_points(T_ws @ T, tag_corners_local(TAG_MM)) for i, T in tags.items()}


@pytest.fixture(scope="module")
def run():
    rng = np.random.default_rng(0)
    tags, poses = scene_tags(), trajectory(10)
    moved = dict(tags)
    tracker = MarkerTracker(K, BASELINE, MarkerParams())
    results = []
    for k, T in enumerate(poses):
        scene = tags
        if k >= 6:  # tag 4 got knocked 8 mm sideways: RANSAC must reject it
            scene = dict(moved)
            scene[4] = G.make_se3(t=[8.0, 0, 0]) @ tags[4]
        hide = (11, 12) if k < 3 else ()  # two ramp tags only appear later: the map has to grow
        left, right = render_pair(T, scene, rng, hide)
        results.append(tracker.track(left, right, k / 30.0))
    return tags, poses, tracker, results


def test_detection_and_triangulation_single_pair():
    rng = np.random.default_rng(5)
    tags = scene_tags()
    T = trajectory(10)[4]
    left, right = render_pair(T, tags, rng)
    tr = MarkerTracker(K, BASELINE)
    dl, dr = tr.detect(left), tr.detect(right)
    assert len(dl) >= 7 and len(dr) >= 7
    obs = tr.triangulate(dl, dr)
    truth = {i: G.transform_points(G.invert_se3(T) @ tags[i], tag_corners_local(TAG_MM)) for i in obs}
    err = np.concatenate([np.linalg.norm(obs[i] - truth[i], axis=1) for i in obs])
    assert len(obs) >= 7 and np.max(err) < 0.6, np.max(err)


def test_triangulation_at_live_resolution():
    """640x400 (fx = 360): tags are only ~20-30 px wide; edge fitting keeps corners sub-mm.

    (Measured on this scene: "edges" median 0.11 mm / p95 0.46 mm; OpenCV's "subpix" 0.60 / 3.2 mm.
    One pixel of disparity is ~3 mm of depth here, so a few corners near 1 mm are expected.)
    """
    rng = np.random.default_rng(6)
    K_half = np.array([[360.0, 0, 319.5], [0, 360.0, 199.5], [0, 0, 1.0]])
    tags = scene_tags()
    errs = []
    tr = MarkerTracker(K_half, BASELINE)
    for T in trajectory(10)[::3]:
        left, right = render_pair(T, tags, rng, K=K_half, size=(640, 400))
        obs = tr.triangulate(tr.detect(left), tr.detect(right))
        assert len(obs) >= 7
        truth = {i: G.transform_points(G.invert_se3(T) @ tags[i], tag_corners_local(TAG_MM)) for i in obs}
        errs.append(np.concatenate([np.linalg.norm(obs[i] - truth[i], axis=1) for i in obs]))
    err = np.concatenate(errs)
    assert np.median(err) < 0.25 and np.percentile(err, 95) < 0.8, (np.median(err), np.percentile(err, 95))


def test_poses_and_ransac(run):
    tags, poses, tracker, results = run
    assert results[0].ok and results[0].info["method"] == "init" and np.allclose(results[0].T_wc, np.eye(4))
    T0_inv = G.invert_se3(poses[0])
    for k, (T, r) in enumerate(zip(poses, results)):
        assert r.ok, (k, r.info)
        rot, trans = G.pose_error(T0_inv @ T, r.T_wc)
        assert rot < 0.3 and trans < 1.0, (k, rot, trans)
        if k >= 6 and 4 in r.info["ids"]:
            assert 4 not in r.info["inliers"]  # the displaced tag is an outlier
    assert any(11 in r.info.get("new_tags", []) for r in results[3:])


def test_map_accuracy_and_refinement(run):
    tags, poses, tracker, _ = run
    truth = true_corners_w(tags, poses[0])
    m = tracker.map
    assert set(m) == set(tags)
    err = {i: np.linalg.norm(m[i] - truth[i], axis=1).max() for i in m}
    assert max(err.values()) < 0.5, err
    assert tracker.n_observations[4] <= 7  # observations of the displaced tag were not merged
    rep = tracker.refine_map()
    assert rep["n_tags"] == len(tags) and rep["rms_mm"] < 0.3
    m2 = tracker.map
    err2 = {i: np.linalg.norm(m2[i] - truth[i], axis=1).max() for i in m2}
    assert max(err2.values()) < 0.5, err2


def test_tracking_with_dot_projector_on():
    """Dot projector on: 10k Gaussian dots (2-3 px FWHM, 100-150 DN) over 34 DN tag contrast.

    The dots break plain cv2.aruco detection; the default prefilter (grey-scale opening), the
    dot mask for the edge refinement, the stereo reprojection refinement and the consensus map
    keep pose and map within the clean-scene thresholds at full resolution.
    """
    rng = np.random.default_rng(11)
    tags, poses = scene_tags(), trajectory(10)
    pairs = [render_pair(T, tags, rng, levels=(18.0, 52.0), dots=10000) for T in poses]
    tracker = MarkerTracker(K, BASELINE)  # defaults: prefilter="open", corner_refine="edges"
    results = [tracker.track(l, r, k / 30.0) for k, (l, r) in enumerate(pairs)]
    assert results[0].ok and results[0].info["method"] == "init"
    T0_inv = G.invert_se3(poses[0])
    for k, (T, r) in enumerate(zip(poses, results)):
        assert r.ok, (k, r.info)
        rot, trans = G.pose_error(T0_inv @ T, r.T_wc)
        assert rot < 0.3 and trans < 1.0, (k, rot, trans)
    truth = true_corners_w(tags, poses[0])
    err = {i: np.linalg.norm(m - truth[i], axis=1).max() for i, m in tracker.map.items()}
    assert len(err) >= 7 and max(err.values()) < 0.5, err
    # without dot suppression far fewer tags are found
    n_default = sum(r.info["n_left"] + r.info["n_right"] for r in results)
    plain = MarkerTracker(K, BASELINE, MarkerParams(prefilter="none", dot_min_dn=0))
    n_plain = sum(len(plain.detect(l)) + len(plain.detect(r)) for l, r in pairs)
    assert n_default >= 1.5 * n_plain, (n_default, n_plain)


def test_pnp_fallback_and_failures():
    rng = np.random.default_rng(3)
    tags, poses = scene_tags(), trajectory(10)
    tracker = MarkerTracker(K, BASELINE)
    blank = np.full((H, W), 128, np.uint8)
    r = tracker.track(blank, blank, 0.0)
    assert not r.ok and r.info["reason"] == "not_initialized"
    for k in (0, 2):
        assert tracker.track(*render_pair(poses[k], tags, rng), k / 30.0).ok
    # right image without tags: single-image PnP on the left image
    left, right = render_pair(poses[4], tags, rng, hide_right=True)
    r = tracker.track(left, right, 4 / 30.0)
    assert r.ok and r.info["method"] == "pnp"
    rot, trans = G.pose_error(G.invert_se3(poses[0]) @ poses[4], r.T_wc)
    assert rot < 0.5 and trans < 3.0, (rot, trans)
    # nothing visible: failure keeps the last good pose
    r2 = tracker.track(blank, blank, 5 / 30.0)
    assert not r2.ok and r2.info["reason"] == "no_tags" and np.allclose(r2.T_wc, r.T_wc)
    no_pnp = MarkerTracker(K, BASELINE, MarkerParams(pnp_fallback=False))
    no_pnp.track(*render_pair(poses[0], tags, rng), 0.0)
    r3 = no_pnp.track(left, right, 4 / 30.0)
    assert not r3.ok and r3.info["reason"] == "few_tags"


def _consensus_reference(obs, thr):
    """The original ``MarkerTracker._consensus`` (one n x n x 4 x 3 tensor), kept as the guard."""
    A = np.stack(obs)
    D = np.linalg.norm(A[:, None] - A[None, :], axis=3).max(axis=2)
    close = D < thr
    best = int(np.argmax(close.sum(axis=1)))
    members = close[best]
    return A[members].mean(axis=0), members


@pytest.mark.parametrize("n", [1, 2, 5, 64, 65, 200, 333])
def test_consensus_blocks_equal_the_single_tensor_version(n):
    rng = np.random.default_rng(n)
    base = tag_corners_local(20.0)
    # a few clusters of observations (with noise) plus exact duplicates, so ties occur
    centres = rng.normal(0.0, 50.0, (3, 1, 3))
    obs = [base + centres[rng.integers(3)] + rng.normal(0.0, 0.4, (4, 3)) for _ in range(n)]
    for k in range(0, n - 1, 3):
        obs[k + 1] = obs[k].copy()  # exact ties
    if n > 6:
        obs[n // 2:] = obs[: n - n // 2]  # whole repeated halves: equal counts for several members
    for thr in (0.5, 2.0, 1e9):
        want_c, want_m = _consensus_reference(obs, thr)
        got_c, got_m = MarkerTracker._consensus(obs, thr)
        assert got_c.dtype == want_c.dtype and got_m.dtype == want_m.dtype
        assert got_c.shape == want_c.shape and got_m.shape == want_m.shape
        assert np.array_equal(got_c, want_c) and np.array_equal(got_m, want_m)
