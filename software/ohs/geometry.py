"""Small, dependency-light geometry helpers shared by all modules (numpy only).

Units are millimetres; rotations are 3x3 matrices; poses are 4x4 ``T_wc`` (``p_w = T_wc @ p_c``).
"""
from __future__ import annotations

import numpy as np

__all__ = [
    "make_se3",
    "invert_se3",
    "transform_points",
    "skew",
    "so3_exp",
    "so3_log",
    "rotation_angle_deg",
    "pose_error",
    "integrate_gyro",
    "pixels_to_rays",
    "project_points",
    "ray_plane_intersect",
    "fit_plane",
    "fit_plane_ransac",
    "kabsch",
    "depth_to_points",
]


# --------------------------------------------------------------------------- SE3 / SO3
def make_se3(R: np.ndarray | None = None, t: np.ndarray | None = None) -> np.ndarray:
    T = np.eye(4)
    if R is not None:
        T[:3, :3] = R
    if t is not None:
        T[:3, 3] = np.asarray(t, dtype=np.float64).reshape(3)
    return T


def invert_se3(T: np.ndarray) -> np.ndarray:
    R, t = T[:3, :3], T[:3, 3]
    Ti = np.eye(4)
    Ti[:3, :3] = R.T
    Ti[:3, 3] = -R.T @ t
    return Ti


def transform_points(T: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Apply a 4x4 transform to (N,3) points."""
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
    return pts @ T[:3, :3].T + T[:3, 3]


def skew(v: np.ndarray) -> np.ndarray:
    x, y, z = np.asarray(v, dtype=np.float64).reshape(3)
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])


def so3_exp(w: np.ndarray) -> np.ndarray:
    """Rotation matrix from a rotation vector (Rodrigues), stable for small angles."""
    w = np.asarray(w, dtype=np.float64).reshape(3)
    th = float(np.linalg.norm(w))
    W = skew(w)
    if th < 1e-8:
        return np.eye(3) + W + 0.5 * W @ W
    return np.eye(3) + np.sin(th) / th * W + (1.0 - np.cos(th)) / th**2 * W @ W


def so3_log(R: np.ndarray) -> np.ndarray:
    """Rotation vector of a rotation matrix (inverse of :func:`so3_exp`)."""
    R = np.asarray(R, dtype=np.float64)
    cos_th = np.clip((np.trace(R) - 1.0) / 2.0, -1.0, 1.0)
    th = float(np.arccos(cos_th))
    if th < 1e-8:
        return 0.5 * np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    if np.pi - th < 1e-4:
        # near 180 deg: axis from the symmetric part
        B = (R + np.eye(3)) / 2.0
        axis = np.sqrt(np.clip(np.diag(B), 0.0, None))
        k = int(np.argmax(axis))
        axis[k] = np.sqrt(B[k, k])
        for j in range(3):
            if j != k:
                axis[j] = B[k, j] / axis[k]
        axis /= np.linalg.norm(axis)
        return axis * th
    return th / (2.0 * np.sin(th)) * np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])


def rotation_angle_deg(R: np.ndarray) -> float:
    return float(np.degrees(np.linalg.norm(so3_log(R))))


def pose_error(T_a: np.ndarray, T_b: np.ndarray) -> tuple[float, float]:
    """(rotation error in degrees, translation error in mm) between two poses."""
    D = invert_se3(T_a) @ T_b
    return rotation_angle_deg(D[:3, :3]), float(np.linalg.norm(D[:3, 3]))


def integrate_gyro(
    t: np.ndarray, gyro: np.ndarray, t0: float, t1: float, bias: np.ndarray | None = None
) -> np.ndarray:
    """Integrate body angular velocity samples over ``[t0, t1]``.

    ``t`` (N,) sorted sample times, ``gyro`` (N,3) rad/s in the body frame. The angular velocity is
    linearly interpolated between samples (and held constant outside the sample range; callers
    should check coverage). Returns ``R_01`` such that ``v_0 = R_01 @ v_1`` for a vector expressed
    in the body frame at ``t1`` (i.e. the orientation of body@t1 relative to body@t0).
    """
    t = np.asarray(t, dtype=np.float64).reshape(-1)
    g = np.asarray(gyro, dtype=np.float64).reshape(-1, 3)
    if bias is not None:
        g = g - np.asarray(bias, dtype=np.float64).reshape(1, 3)
    if t1 == t0 or len(t) == 0:
        return np.eye(3)
    sign = 1.0
    if t1 < t0:
        t0, t1, sign = t1, t0, -1.0
    inner = t[(t > t0) & (t < t1)]
    grid = np.concatenate([[t0], inner, [t1]])
    w = np.stack([np.interp(grid, t, g[:, k]) for k in range(3)], axis=1)
    R = np.eye(3)
    for i in range(len(grid) - 1):
        dt = grid[i + 1] - grid[i]
        R = R @ so3_exp(0.5 * (w[i] + w[i + 1]) * dt)
    return R if sign > 0 else R.T


# --------------------------------------------------------------------------- rays, planes
def pixels_to_rays(uv: np.ndarray, K: np.ndarray) -> np.ndarray:
    """Rays (N,3) with z = 1 through pixel coordinates ``uv`` (N,2) of an ideal pinhole ``K``."""
    uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
    fx, fy, cx, cy, s = K[0, 0], K[1, 1], K[0, 2], K[1, 2], K[0, 1]
    y = (uv[:, 1] - cy) / fy
    x = (uv[:, 0] - cx - s * y) / fx
    return np.stack([x, y, np.ones_like(x)], axis=1)


def project_points(P: np.ndarray, K: np.ndarray) -> np.ndarray:
    """Project (N,3) camera-frame points with pinhole ``K``. Points with z <= 0 give NaN."""
    P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
    z = P[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        x = np.where(z > 0, P[:, 0] / z, np.nan)
        y = np.where(z > 0, P[:, 1] / z, np.nan)
    u = K[0, 0] * x + K[0, 1] * y + K[0, 2]
    v = K[1, 1] * y + K[1, 2]
    return np.stack([u, v], axis=1)


def ray_plane_intersect(rays: np.ndarray, normal: np.ndarray, d: float, origin: np.ndarray | None = None) -> np.ndarray:
    """Intersect rays ``origin + s * ray`` (s > 0) with plane ``n . X + d = 0``.

    Returns (N,3) points; rows are NaN where the ray is parallel to the plane or the hit is behind.
    """
    rays = np.asarray(rays, dtype=np.float64).reshape(-1, 3)
    n = np.asarray(normal, dtype=np.float64).reshape(3)
    o = np.zeros(3) if origin is None else np.asarray(origin, dtype=np.float64).reshape(3)
    denom = rays @ n
    with np.errstate(divide="ignore", invalid="ignore"):
        s = -(o @ n + d) / denom
    bad = (np.abs(denom) < 1e-12) | ~(s > 0)
    out = o + s[:, None] * rays
    out[bad] = np.nan
    return out


def fit_plane(points: np.ndarray, weights: np.ndarray | None = None) -> tuple[np.ndarray, float, float]:
    """Least-squares plane through (N>=3,3) points. Returns ``(n, d, rms)`` with ``d <= 0``."""
    P = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if len(P) < 3:
        raise ValueError("need at least 3 points to fit a plane")
    w = np.ones(len(P)) if weights is None else np.asarray(weights, dtype=np.float64).reshape(-1)
    c = (P * w[:, None]).sum(0) / w.sum()
    Q = (P - c) * np.sqrt(w)[:, None]
    _, _, Vt = np.linalg.svd(Q, full_matrices=False)
    n = Vt[-1]
    d = -float(n @ c)
    if d > 0:
        n, d = -n, -d
    rms = float(np.sqrt(np.mean((P @ n + d) ** 2)))
    return n, d, rms


def fit_plane_ransac(
    points: np.ndarray, thresh: float, iters: int = 500, seed: int = 0, min_inliers: int = 3
) -> tuple[np.ndarray, float, np.ndarray, float]:
    """RANSAC plane fit followed by a least-squares refit on the inliers.

    Returns ``(n, d, inlier_mask, rms_of_inliers)``.
    """
    P = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if len(P) < 3:
        raise ValueError("need at least 3 points to fit a plane")
    rng = np.random.default_rng(seed)
    best_mask, best_count = None, -1
    for _ in range(iters):
        idx = rng.choice(len(P), 3, replace=False)
        a, b, c = P[idx]
        n = np.cross(b - a, c - a)
        norm = np.linalg.norm(n)
        if norm < 1e-9:
            continue
        n /= norm
        d = -n @ a
        mask = np.abs(P @ n + d) < thresh
        cnt = int(mask.sum())
        if cnt > best_count:
            best_mask, best_count = mask, cnt
    if best_mask is None or best_count < min_inliers:
        raise ValueError("RANSAC failed to find a plane")
    n, d, _ = fit_plane(P[best_mask])
    mask = np.abs(P @ n + d) < thresh
    n, d, rms = fit_plane(P[mask])
    return n, d, mask, rms


def kabsch(A: np.ndarray, B: np.ndarray, weights: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Rigid transform minimising ``sum w ||R a + t - b||^2`` for paired (N,3) points.

    Returns ``(R, t)`` so that ``B ~= A @ R.T + t``.
    """
    A = np.asarray(A, dtype=np.float64).reshape(-1, 3)
    B = np.asarray(B, dtype=np.float64).reshape(-1, 3)
    w = np.ones(len(A)) if weights is None else np.asarray(weights, dtype=np.float64).reshape(-1)
    w = w / w.sum()
    ca, cb = (A * w[:, None]).sum(0), (B * w[:, None]).sum(0)
    H = ((A - ca) * w[:, None]).T @ (B - cb)
    U, _, Vt = np.linalg.svd(H)
    D = np.diag([1.0, 1.0, np.sign(np.linalg.det(Vt.T @ U.T))])
    R = Vt.T @ D @ U.T
    return R, cb - R @ ca


def depth_to_points(
    depth: np.ndarray, K: np.ndarray, stride: int = 1, mask: np.ndarray | None = None
) -> np.ndarray:
    """Back-project a depth map (mm, 0/NaN = invalid) to (N,3) points in the camera frame."""
    D = np.asarray(depth, dtype=np.float64)[::stride, ::stride]
    h, w = D.shape
    vs, us = np.mgrid[0:h, 0:w]
    us, vs = us * stride, vs * stride
    valid = np.isfinite(D) & (D > 0)
    if mask is not None:
        valid &= np.asarray(mask, dtype=bool)[::stride, ::stride]
    z = D[valid]
    uv = np.stack([us[valid], vs[valid]], axis=1)
    return pixels_to_rays(uv, K) * z[:, None]
