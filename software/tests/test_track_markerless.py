"""Tests for ohs.track.markerless (synthetic depth of a sphere resting on a plane).

The scene is rendered by a small numpy ray caster (640x400, fx = 360, like the half-resolution
live stream) with depth-dependent Gaussian noise, random holes and the 150-450 mm validity range
of the stereo matcher. The sphere-on-plane scene is rotationally symmetric about the sphere's
vertical axis, so (as on the real scanner) the gyro prior is what fixes that rotation. The main
run feeds the tracker with ``GyroPredictor`` rotations integrated from synthetic 416 Hz IMU
samples (non-identity ``R_ci``, time offset, residual bias, noise).
"""
import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import geometry as G  # noqa: E402
from ohs.track.imu_prior import GyroPredictor  # noqa: E402
from ohs.track.markerless import FusionParams, MarkerlessTracker, _weak_directions  # noqa: E402
from ohs.types import ImuCalibration, ImuSample  # noqa: E402

W, H, FX = 640, 400, 360.0
K = np.array([[FX, 0, W / 2 - 0.5], [0, FX, H / 2 - 0.5], [0, 0, 1.0]])
CENTER = np.array([0.0, 0.0, 40.0])  # scene frame: plane z = 0 (up = +z), sphere resting on it
RADIUS = 40.0
PLANE_HALF = 220.0
BOX = (np.array([45.0, -60.0, 0.0]), np.array([85.0, -20.0, 30.0]))  # optional box (breaks the symmetry)
FPS = 30.0


def look_at(eye: np.ndarray, target: np.ndarray, roll: float = 0.0) -> np.ndarray:
    """Camera-to-scene pose with OpenCV axes (x right, y down, z forward), scene up = +z."""
    z = target - eye
    z = z / np.linalg.norm(z)
    x = np.cross(z, [0.0, 0.0, 1.0])
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return G.make_se3(np.stack([x, y, z], axis=1) @ G.so3_exp(np.array([0.0, 0.0, roll])), eye)


def render_depth(T_sc: np.ndarray, rng: np.random.Generator | None = None, noise_mm: float = 0.25,
                 box: bool = False) -> np.ndarray:
    """Depth (mm, float32, 0 = invalid) of the sphere-on-plane scene seen from pose ``T_sc``."""
    vs, us = np.mgrid[0:H, 0:W].astype(np.float64)
    rays = np.stack([(us - K[0, 2]) / FX, (vs - K[1, 2]) / FX, np.ones_like(us)], -1).reshape(-1, 3)
    d = rays @ T_sc[:3, :3].T
    o = T_sc[:3, 3]
    with np.errstate(divide="ignore", invalid="ignore"):
        t_plane = -o[2] / d[:, 2]
        hit = o + t_plane[:, None] * d
        t_plane[~(t_plane > 0) | (np.abs(hit[:, 0]) > PLANE_HALF) | (np.abs(hit[:, 1]) > PLANE_HALF)] = np.inf
        oc = o - CENTER
        b = d @ oc
        a = np.einsum("ij,ij->i", d, d)
        disc = b * b - a * (oc @ oc - RADIUS ** 2)
        t_sph = (-b - np.sqrt(np.maximum(disc, 0.0))) / a
        t_sph[(disc < 0) | (t_sph <= 0)] = np.inf
        t_hit = np.minimum(t_plane, t_sph)
        if box:  # slab test against the axis-aligned box
            t1, t2 = (BOX[0] - o) / d, (BOX[1] - o) / d
            t_near = np.nanmax(np.minimum(t1, t2), axis=1)
            t_far = np.nanmin(np.maximum(t1, t2), axis=1)
            t_hit = np.minimum(t_hit, np.where((t_far >= t_near) & (t_near > 0), t_near, np.inf))
    z = t_hit.reshape(H, W)  # rays have z = 1, so t is the depth
    z[~np.isfinite(z)] = 0.0
    if rng is not None:
        z = z + (z > 0) * rng.normal(size=z.shape) * noise_mm * (z / 250.0) ** 2  # stereo: sigma ~ z^2
        for _ in range(40):  # matching holes
            x0, y0 = rng.integers(0, W), rng.integers(0, H)
            sx, sy = rng.integers(3, 25, size=2)
            z[y0:y0 + sy, x0:x0 + sx] = 0.0
        z[rng.random(z.shape) < 0.02] = 0.0
    z[(z < 150.0) | (z > 450.0)] = 0.0
    return z.astype(np.float32)


def arc_trajectory(n: int = 36) -> list[np.ndarray]:
    """60 deg arc around the sphere, descending, closing in and with some roll (T_sc per frame)."""
    poses = []
    for k in range(n):
        s = k / (n - 1)
        az, el = np.radians(-30 + 60 * s), np.radians(55 - 15 * s)
        dist = 265.0 - 35.0 * s
        target = np.array([12 * np.sin(2 * np.pi * s), -10 + 20 * s, 30.0])
        eye = target + dist * np.array([np.cos(el) * np.sin(az), -np.cos(el) * np.cos(az), np.sin(el)])
        poses.append(look_at(eye, target, roll=np.radians(8 * np.sin(np.pi * s))))
    return poses


def gyro_prior(poses, k: int, rng: np.random.Generator, sigma_deg: float = 0.05) -> np.ndarray:
    """Relative rotation previous -> current camera with gyro-like noise (v_prev = R @ v_cur)."""
    R = poses[k - 1][:3, :3].T @ poses[k][:3, :3]
    return R @ G.so3_exp(rng.normal(scale=np.radians(sigma_deg), size=3))


def imu_predictor(poses, rng: np.random.Generator, rate: float = 416.0) -> GyroPredictor:
    """GyroPredictor fed with synthetic IMU samples of the (slerp-interpolated) trajectory.

    The IMU is rotated (``R_ci``), its clock is offset and its true bias differs from the
    calibrated one by 0.003 rad/s; samples carry 0.003 rad/s white noise.
    """
    R_ci = G.so3_exp(np.array([0.4, -1.1, 0.7]))
    calib = ImuCalibration(R_ci=R_ci, time_offset=0.0123, gyro_bias=np.array([0.01, -0.02, 0.015]))
    true_bias = calib.gyro_bias + np.array([0.003, -0.002, 0.002])
    w_frames = [G.so3_log(poses[k][:3, :3].T @ poses[k + 1][:3, :3]) * FPS for k in range(len(poses) - 1)]
    t_cam = np.arange(-0.05, (len(poses) - 1) / FPS + 0.05, 1.0 / rate)
    idx = np.clip(np.floor(t_cam * FPS).astype(int), 0, len(w_frames) - 1)
    w_c = np.array(w_frames)[idx]
    w_c[(t_cam < 0) | (t_cam > (len(poses) - 1) / FPS)] = 0.0
    w_i = w_c @ R_ci + true_bias + rng.normal(scale=0.003, size=w_c.shape)  # R_ci^T w_c + bias
    gp = GyroPredictor(calib)
    gp.add(ImuSample(float(t - calib.time_offset), float(t), w, np.zeros(3)) for t, w in zip(t_cam, w_i))
    return gp


def radial_errors(V_w: np.ndarray, T_s_w: np.ndarray) -> np.ndarray:
    """|r - R| of mesh vertices near the sphere (excluding the contact zone with the plane)."""
    Vs = G.transform_points(T_s_w, V_w)
    r = np.linalg.norm(Vs - CENTER, axis=1)
    near = (np.abs(r - RADIUS) < 5.0) & (Vs[:, 2] > 10.0)
    return np.abs(r[near] - RADIUS)


@pytest.fixture(scope="module")
def tracked_run():
    rng = np.random.default_rng(0)
    poses = arc_trajectory(36)
    gp = imu_predictor(poses, rng)
    tracker = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, device="auto"))
    results = []
    for k, T in enumerate(poses):
        t_prev, t = (k - 1) / FPS, k / FPS
        R_prior = gp.relative_rotation(t_prev, t) if k and gp.covers(t_prev, t) else None
        assert k == 0 or R_prior is not None
        results.append(tracker.track(render_depth(T, rng), None, t, R_prior))
    return poses, tracker, results


def test_first_frame_defines_world(tracked_run):
    _, tracker, results = tracked_run
    assert results[0].ok and results[0].info.get("first_frame")
    assert np.allclose(results[0].T_wc, np.eye(4))
    assert len(tracker.trajectory) == 36 and tracker.n_integrated == 36


def test_tracking_accuracy_along_arc(tracked_run):
    poses, _, results = tracked_run
    T0_inv = G.invert_se3(poses[0])
    errs = [G.pose_error(T0_inv @ T, r.T_wc) for T, r in zip(poses, results)]
    assert all(r.ok for r in results), [r.info.get("reason") for r in results if not r.ok]
    rot, trans = errs[-1]
    assert rot < 1.5 and trans < 3.0, (rot, trans)
    assert max(e[1] for e in errs) < 3.0
    fit = [r.info["fitness"] for r in results[1:]]
    assert min(fit) > 0.5


def test_fused_mesh_and_points(tracked_run):
    poses, tracker, _ = tracked_run
    V, F, N = tracker.extract_mesh()
    assert len(F) > 10000 and F.max() < len(V)
    err = radial_errors(V, poses[0])
    assert len(err) > 1000 and np.median(err) < 0.5, np.median(err)
    # faces are counter-clockwise from outside: face normals agree with the outward vertex normals
    fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    assert np.median(np.einsum("ij,ij->i", fn, N[F].mean(axis=1))) > 0
    Vs = G.transform_points(poses[0], V)
    plane = (np.abs(Vs[:, 2]) < 3.0) & (np.linalg.norm(Vs[:, :2] - CENTER[:2], axis=1) > 60.0)
    assert np.median(np.abs(Vs[plane, 2])) < 0.5
    P, Np = tracker.extract_points()
    assert len(P) > 1000 and np.median(radial_errors(P, poses[0])) < 0.5
    up = G.transform_points(poses[0], P)[:, 2] < 3.0
    n_s = Np @ poses[0][:3, :3].T
    assert np.median(n_s[up & (np.linalg.norm(G.transform_points(poses[0], P)[:, :2], axis=1) > 60), 2]) > 0.95


def test_garbage_depth_fails_and_relocalises():
    rng = np.random.default_rng(1)
    poses = arc_trajectory(36)
    T0_inv = G.invert_se3(poses[0])
    tracker = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, device="auto"))
    assert not tracker.track(np.zeros((H, W), np.float32), None, 0.0).ok  # nothing to start from
    for k in range(12):
        res = tracker.track(render_depth(poses[k], rng), None, k / 30.0, gyro_prior(poses, k, rng) if k else None)
        assert res.ok
    n_int, T_good = tracker.n_integrated, res.T_wc.copy()
    for k in (12, 13):  # garbage: random depth in the valid range, then an empty frame
        garbage = rng.uniform(150, 450, size=(H, W)).astype(np.float32) if k == 12 else np.zeros((H, W), np.float32)
        bad = tracker.track(garbage, None, k / 30.0, gyro_prior(poses, k, rng))
        assert not bad.ok and bad.info["reason"] in ("bad_depth", "low_fitness", "few_inliers", "high_rmse", "no_depth")
        assert np.allclose(bad.T_wc, T_good) and tracker.n_integrated == n_int and tracker.is_lost
    assert bad.info["reason"] == "no_depth"
    # garbage that still contains smooth patches (noise smoothed over 9 px) must fail the ICP gates
    blobs = rng.uniform(150, 450, size=(H // 9 + 1, W // 9 + 1)).astype(np.float32)
    patchy = np.kron(blobs, np.ones((9, 9), np.float32))[:H, :W]
    bad = tracker.track(patchy, None, 13.5 / 30.0, np.eye(3))
    assert not bad.ok and bad.info["reason"] in ("low_fitness", "few_inliers", "high_rmse", "jump"), bad.info["reason"]
    assert tracker.n_integrated == n_int
    recovered = None
    for k in range(14, 20):
        res = tracker.track(render_depth(poses[k], rng), None, k / 30.0, gyro_prior(poses, k, rng))
        if res.ok and recovered is None:
            recovered = k
        if recovered is not None:
            assert res.ok
            rot, trans = G.pose_error(T0_inv @ poses[k], res.T_wc)
            assert rot < 1.5 and trans < 3.0, (k, rot, trans)
    assert recovered is not None and recovered <= 15, recovered
    assert [ok for _, _, ok in tracker.trajectory].count(False) == 4 + (recovered - 14)


def test_tracks_without_gyro_when_geometry_constrains_the_pose():
    """No R_prior (no IMU): constant-velocity prediction + ICP on a scene with a box next to the sphere."""
    rng = np.random.default_rng(4)
    poses = arc_trajectory(24)
    tracker = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, device="auto"))
    T0_inv = G.invert_se3(poses[0])
    for k, T in enumerate(poses):
        res = tracker.track(render_depth(T, rng, box=True), None, k / FPS)
        assert res.ok and not res.info.get("gyro_prior", False), (k, res.info.get("reason"))
    rot, trans = G.pose_error(T0_inv @ poses[-1], res.T_wc)
    assert rot < 1.5 and trans < 3.0, (rot, trans)


def _constraints(P: np.ndarray, N: np.ndarray) -> np.ndarray:
    """Point-to-plane ICP rows [p x n, n]."""
    return np.concatenate([np.cross(P, N), N], axis=1)


def _grid(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    A, B = np.meshgrid(a, b)
    return A.ravel(), B.ravel()


def test_weak_directions_say_what_the_surface_cannot_pin_down():
    ratio = FusionParams().degenerate_eig_ratio
    # a flat panel facing the camera: it can slide in its plane and turn about its normal
    x, y = _grid(np.arange(-60.0, 61.0, 5.0), np.arange(-40.0, 41.0, 5.0))
    P = np.stack([x, y, np.full_like(x, 300.0)], axis=1)
    N = np.tile([0.0, 0.0, -1.0], (len(P), 1))
    r, weak = _weak_directions(_constraints(P, N), P, ratio)
    kinds = {w["kind"]: w["axis"] for w in weak}
    assert r < 1e-9 and set(kinds) == {"slide", "turn"}
    assert abs(kinds["turn"][2]) > 0.99 and abs(kinds["slide"][2]) < 0.01  # turns about z, slides within xy
    # a sphere on a table, seen from the side (camera y points down): it turns about its vertical axis only
    c, R = np.array([0.0, 0.0, 300.0]), 40.0
    th, ph = _grid(np.radians(np.arange(95.0, 266.0, 5.0)), np.radians(np.arange(-80.0, 81.0, 5.0)))
    d = np.stack([np.cos(ph) * np.sin(th), np.sin(ph), np.cos(ph) * np.cos(th)], axis=1)  # the camera-facing half
    xt, zt = _grid(np.arange(-150.0, 151.0, 10.0), np.arange(200.0, 451.0, 10.0))
    Pt = np.stack([xt, np.full_like(xt, R), zt], axis=1)
    P = np.concatenate([c + R * d, Pt])
    N = np.concatenate([d, np.tile([0.0, -1.0, 0.0], (len(Pt), 1))])
    r, weak = _weak_directions(_constraints(P, N), P, ratio)
    assert r < 1e-9 and [w["kind"] for w in weak] == ["turn"] and abs(weak[0]["axis"][1]) > 0.99
    # a box corner (three faces): nothing is weak
    u, v = _grid(np.arange(0.0, 41.0, 4.0), np.arange(0.0, 41.0, 4.0))
    faces = [(np.stack([np.full_like(u, 30.0), u - 20, v + 280], axis=1), [-1.0, 0.0, 0.0]),
             (np.stack([u - 20, np.full_like(u, 30.0), v + 280], axis=1), [0.0, -1.0, 0.0]),
             (np.stack([u - 20, v - 20, np.full_like(u, 320.0)], axis=1), [0.0, 0.0, -1.0])]
    P = np.concatenate([f for f, _ in faces])
    N = np.concatenate([np.tile(n, (len(f), 1)) for f, n in faces])
    r, weak = _weak_directions(_constraints(P, N), P, ratio)
    assert r > ratio and weak == []


def test_integrate_external_poses_and_reset():
    rng = np.random.default_rng(2)
    poses = arc_trajectory(8)
    tracker = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=1.0, device="auto"))
    T0_inv = G.invert_se3(poses[0])
    for k, T in enumerate(poses):
        tracker.integrate(render_depth(T, rng), None, T0_inv @ T, t=k / 30.0)
    assert tracker.n_integrated == 8 and len(tracker.trajectory) == 8
    V, F, N = tracker.extract_mesh()
    err = radial_errors(V, poses[0])
    assert np.median(err) < 0.3, np.median(err)
    # tracking continues from the externally supplied pose
    res = tracker.track(render_depth(poses[-1], rng), None, 8 / 30.0, np.eye(3))
    assert res.ok and G.pose_error(T0_inv @ poses[-1], res.T_wc)[1] < 1.0
    tracker.reset()
    assert tracker.n_integrated == 0 and tracker.trajectory == [] and len(tracker.extract_mesh()[0]) == 0
