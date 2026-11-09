"""Known shapes for the mesh tests (``ohs.mesh``): icosphere, punched holes, grids, a box, a step."""
from __future__ import annotations

import numpy as np


def icosphere(radius: float = 40.0, level: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """Closed sphere: an icosahedron subdivided ``level`` times (20 * 4^level triangles)."""
    t = (1.0 + 5 ** 0.5) / 2.0
    V = np.array([[-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0], [0, -1, t], [0, 1, t], [0, -1, -t], [0, 1, -t],
                  [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1]], float)
    F = np.array([[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4], [11, 10, 2],
                  [10, 7, 6], [7, 1, 8], [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9], [4, 9, 5],
                  [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1]], np.int64)
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    for _ in range(level):
        a, b, c = F[:, 0], F[:, 1], F[:, 2]
        e = np.sort(np.stack([np.stack([a, b], 1), np.stack([b, c], 1), np.stack([c, a], 1)], 1).reshape(-1, 2), axis=1)
        key = e[:, 0] * len(V) + e[:, 1]
        uk, inv = np.unique(key, return_inverse=True)
        mid = len(V) + inv.reshape(-1, 3)
        M = (V[uk // len(V)] + V[uk % len(V)]) / 2
        V = np.vstack([V, M / np.linalg.norm(M, axis=1, keepdims=True)])
        ab, bc, ca = mid[:, 0], mid[:, 1], mid[:, 2]
        F = np.concatenate([np.stack([a, ab, ca], 1), np.stack([b, bc, ab], 1), np.stack([c, ca, bc], 1),
                            np.stack([ab, bc, ca], 1)])
    return V * radius, F


def punch(V: np.ndarray, F: np.ndarray, centers, radii) -> tuple[np.ndarray, np.ndarray]:
    """Remove the faces whose centroid lies within ``radius`` of each centre (round holes)."""
    c = V[F].mean(axis=1)
    keep = np.ones(len(F), dtype=bool)
    for p, r in zip(centers, radii):
        keep &= np.linalg.norm(c - np.asarray(p, float), axis=1) > r
    return V, F[keep]


def grid(nx: int, ny: int, step: float = 1.0, z=None) -> tuple[np.ndarray, np.ndarray]:
    """An open rectangular sheet of ``(nx-1) * (ny-1) * 2`` triangles (normals +z)."""
    ys, xs = np.mgrid[0:ny, 0:nx]
    V = np.stack([xs.ravel() * step, ys.ravel() * step, np.zeros(nx * ny)], axis=1).astype(float)
    if z is not None:
        V[:, 2] = z(V[:, 0], V[:, 1])
    idx = np.arange(nx * ny).reshape(ny, nx)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel()
    return V, np.concatenate([np.stack([a, b, d], 1), np.stack([a, d, c], 1)])


def box(size: float = 20.0, n: int = 32) -> tuple[np.ndarray, np.ndarray]:
    """A closed cube centred at the origin, ``n x n`` quads per side, outward normals."""
    h = size / 2.0
    s = np.linspace(-h, h, n + 1)
    U, W = np.meshgrid(s, s, indexing="ij")
    Vs, Fs, off = [], [], 0
    for axis in range(3):
        for sign in (-1.0, 1.0):
            P = np.zeros(((n + 1) ** 2, 3))
            o = [k for k in range(3) if k != axis]
            P[:, axis] = sign * h
            P[:, o[0]] = U.ravel()
            P[:, o[1]] = W.ravel()
            idx = np.arange((n + 1) ** 2).reshape(n + 1, n + 1) + off
            a, b, c, d = idx[:-1, :-1].ravel(), idx[1:, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel()
            T = np.concatenate([np.stack([a, b, d], 1), np.stack([a, d, c], 1)])
            nrm = np.cross(P[T[0, 1] - off] - P[T[0, 0] - off], P[T[0, 2] - off] - P[T[0, 0] - off])
            if nrm[axis] * sign < 0:
                T = T[:, [0, 2, 1]]
            Vs.append(P)
            Fs.append(T)
            off += len(P)
    V = np.vstack(Vs)
    F = np.vstack(Fs)
    # merge the duplicated vertices along the cube's edges
    key = np.round(V / (size / n / 4)).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return V[first], inv.reshape(-1)[F]


def box_distance(P: np.ndarray, h: float) -> np.ndarray:
    """Signed distance of points to the cube ``[-h, h]^3``."""
    q = np.abs(P) - h
    return np.linalg.norm(np.maximum(q, 0.0), axis=1) + np.minimum(q.max(axis=1), 0.0)


def step(width: float = 40.0, depth: float = 20.0, height: float = 5.0, h: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """An open sheet with a sharp right-angle step (a bracket's edge): z = 0 for x < width / 2,
    a vertical wall at x = width / 2, z = height beyond. Returns ``(V, F)``."""
    xs = np.arange(0.0, width + 1e-9, h)
    x0 = width / 2.0
    n_wall = int(round(height / h)) - 1
    px = np.concatenate([xs[xs <= x0], np.full(n_wall, x0), xs[xs > x0]])
    pz = np.concatenate([np.zeros((xs <= x0).sum()), np.linspace(h, height - h, n_wall), np.full((xs > x0).sum(), height)])
    ys = np.arange(0.0, depth + 1e-9, h)
    P = len(px)
    V = np.stack([np.tile(px, len(ys)), np.repeat(ys, P), np.tile(pz, len(ys))], axis=1)
    idx = np.arange(len(ys) * P).reshape(len(ys), P)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel()
    return V, np.concatenate([np.stack([a, b, d], 1), np.stack([a, d, c], 1)])


def step_distance(P: np.ndarray, x0: float = 20.0, height: float = 5.0) -> np.ndarray:
    """Distance of points to the true step surface of :func:`step` (ignoring its outer edges)."""
    x, z = P[:, 0], P[:, 2]
    low = np.hypot(np.maximum(x - x0, 0.0), z)
    high = np.hypot(np.maximum(x0 - x, 0.0), z - height)
    wall = np.hypot(x - x0, np.maximum(np.maximum(-z, z - height), 0.0))
    return np.minimum(np.minimum(low, high), wall)
