"""Live mesh snapshots: vertex-clustering decimation and per-vertex quality colours.

**Decimation** (:func:`decimate`): vertex clustering on a cubic grid. Vertices are binned into
cells, each cell becomes one vertex (the mean position, the renormalised mean normal, the maximum
quality value), faces are re-indexed and faces that collapsed (two corners in one cell) or became
duplicates are dropped. The cell starts at ``sqrt(T / max_triangles)`` times the mesh's typical
edge length and grows by 25 % until the mesh fits. Topology is not preserved (thin features can
merge), which is fine for a live preview; the final mesh comes from offline processing.

**Quality** (:class:`CoverageGrid`): Open3D keeps a TSDF weight per voxel, but the live model is
rebuilt from a subset of the frames after a rewind (see :mod:`ohs.server.session`), which would
lower every weight and turn a well-scanned model yellow. So the quality proxy is kept separately:
the number of *tracked frames* that observed each ``cell_mm`` (8 mm) cube of the world. For every
tracked frame, the depth map is sampled every ``stride`` pixels, back-projected to the world with
the frame's pose and the touched cells are counted once. A rewind subtracts the counts of the
frames it drops, so the proxy stays exact. A vertex reads its cell's count, dilated by one ring of
mesh neighbours (a vertex near a cell corner may sit in a cell that few samples hit).

**Colours** (:func:`quality_colors`): RGBA, yellow (255, 200, 0) at ``lo`` observations or fewer,
green (40, 200, 80) at ``hi`` or more, linear in between. At the live ~10 fps, ``lo = 4`` and
``hi = 12`` mean "seen for under half a second" and "seen for more than a second".
"""
from __future__ import annotations

import collections
import logging

import numpy as np

__all__ = ["decimate", "CoverageGrid", "quality_colors", "cell_keys", "dilate_on_mesh", "YELLOW", "GREEN"]

log = logging.getLogger(__name__)

YELLOW = np.array([255, 200, 0, 255], np.float64)
GREEN = np.array([40, 200, 80, 255], np.float64)
_OFF = 1 << 20  # cell index offset: keys are three 21-bit fields
_MASK = (1 << 21) - 1


def cell_keys(P: np.ndarray, cell_mm: float) -> np.ndarray:
    """int64 key of the ``cell_mm`` cube containing each point (N,3) (mm, |coordinate| < 8 km)."""
    q = np.floor(np.asarray(P, dtype=np.float64) / float(cell_mm)).astype(np.int64) + _OFF
    q = np.clip(q, 0, _MASK)
    return (q[:, 0] << 42) | (q[:, 1] << 21) | q[:, 2]


class CoverageGrid:
    """Observation counts per world cell (see the module docstring). Not thread-safe."""

    def __init__(self, cell_mm: float = 8.0, stride: int = 4) -> None:
        self.cell_mm = float(cell_mm)
        self.stride = int(stride)
        self._counts: collections.Counter = collections.Counter()
        self._rays: tuple[tuple[int, int], int, np.ndarray] | None = None

    def __len__(self) -> int:
        return len(self._counts)

    def frame_keys(self, depth: np.ndarray, K: np.ndarray, T_wc: np.ndarray) -> np.ndarray:
        """Unique cell keys observed by one depth map (mm, 0 = invalid) at pose ``T_wc``."""
        d = np.asarray(depth)
        s = self.stride
        h, w = d.shape
        key = ((w, h), s)
        if self._rays is None or self._rays[:2] != key:
            vs, us = np.mgrid[s // 2 : h : s, s // 2 : w : s]
            y = (vs - K[1, 2]) / K[1, 1]
            x = (us - K[0, 2] - K[0, 1] * y) / K[0, 0]
            self._rays = ((w, h), s, np.stack([x, y, np.ones_like(x)], axis=-1).reshape(-1, 3))
        rays = self._rays[2]
        z = d[s // 2 :: s, s // 2 :: s].astype(np.float64).reshape(-1)[: len(rays)]
        ok = np.isfinite(z) & (z > 0)
        if not ok.any():
            return np.zeros(0, np.int64)
        Pc = rays[ok] * z[ok, None]
        T = np.asarray(T_wc, dtype=np.float64)
        Pw = Pc @ T[:3, :3].T + T[:3, 3]
        return np.unique(cell_keys(Pw, self.cell_mm))

    def add(self, keys: np.ndarray) -> None:
        if len(keys):
            self._counts.update(keys.tolist())

    def remove(self, keys: np.ndarray) -> None:
        c = self._counts
        for k in keys.tolist():
            v = c.get(k, 0) - 1
            if v > 0:
                c[k] = v
            else:
                c.pop(k, None)

    def clear(self) -> None:
        self._counts.clear()

    def snapshot(self) -> tuple[np.ndarray, np.ndarray]:
        """``(keys sorted, counts)`` arrays, safe to use from another thread."""
        n = len(self._counts)
        if n == 0:
            return np.zeros(0, np.int64), np.zeros(0, np.int32)
        keys = np.fromiter(self._counts.keys(), dtype=np.int64, count=n)
        vals = np.fromiter(self._counts.values(), dtype=np.int32, count=n)
        order = np.argsort(keys, kind="stable")
        return keys[order], vals[order]

    @staticmethod
    def lookup(V: np.ndarray, snapshot: tuple[np.ndarray, np.ndarray], cell_mm: float) -> np.ndarray:
        """Observation count of each vertex's cell (0 for unobserved cells)."""
        keys, vals = snapshot
        out = np.zeros(len(V), np.int32)
        if len(keys) == 0 or len(V) == 0:
            return out
        q = cell_keys(V, cell_mm)
        i = np.clip(np.searchsorted(keys, q), 0, len(keys) - 1)
        hit = keys[i] == q
        out[hit] = vals[i[hit]]
        return out


def dilate_on_mesh(values: np.ndarray, F: np.ndarray) -> np.ndarray:
    """Per-vertex maximum over the vertex and its one-ring (via the faces)."""
    v = np.asarray(values)
    if len(F) == 0:
        return v.copy()
    fmax = v[F].max(axis=1)
    out = v.copy()
    for j in range(3):
        np.maximum.at(out, F[:, j], fmax)
    return out


def quality_colors(counts: np.ndarray, lo: float = 4.0, hi: float = 12.0) -> np.ndarray:
    """RGBA uint8 per vertex: yellow at ``<= lo`` observations, green at ``>= hi``."""
    a = np.clip((np.asarray(counts, dtype=np.float64) - lo) / max(hi - lo, 1e-6), 0.0, 1.0)[:, None]
    return np.rint(YELLOW[None] * (1.0 - a) + GREEN[None] * a).astype(np.uint8)


def _edge_length(V: np.ndarray, F: np.ndarray, sample: int = 20000) -> float:
    if len(F) == 0:
        return 1.0
    idx = np.linspace(0, len(F) - 1, min(len(F), sample)).astype(np.int64)
    e = np.linalg.norm(V[F[idx, 0]] - V[F[idx, 1]], axis=1)
    m = float(np.median(e))
    return m if m > 0 else 1.0


def decimate(V: np.ndarray, F: np.ndarray, N: np.ndarray | None = None, values: np.ndarray | None = None,
             max_triangles: int = 300_000) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None]:
    """Vertex clustering until the mesh has at most ``max_triangles`` (see the module docstring).

    Returns ``(V, F, N, values)``; inputs that fit are returned unchanged.
    """
    V = np.asarray(V, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64).reshape(-1, 3)
    if len(F) <= max_triangles:
        return V, F, N, values
    cell = _edge_length(V, F) * np.sqrt(len(F) / float(max_triangles))
    lo = V.min(axis=0)
    for _ in range(40):
        q = np.floor((V - lo) / cell).astype(np.int64)
        dims = q.max(axis=0) + 1
        lin = (q[:, 0] * dims[1] + q[:, 1]) * dims[2] + q[:, 2]
        uniq, inv = np.unique(lin, return_inverse=True)
        inv = inv.reshape(-1)
        F2 = inv[F]
        keep = (F2[:, 0] != F2[:, 1]) & (F2[:, 1] != F2[:, 2]) & (F2[:, 0] != F2[:, 2])
        F2 = F2[keep]
        if len(F2):
            srt = np.sort(F2, axis=1)
            if len(uniq) < (1 << 21):  # pack the sorted corner triple into one int64 (fast unique)
                packed = (srt[:, 0] << 42) | (srt[:, 1] << 21) | srt[:, 2]
                _, first = np.unique(packed, return_index=True)
            else:
                _, first = np.unique(srt, axis=0, return_index=True)
            F2 = F2[np.sort(first)]
        if len(F2) <= max_triangles:
            break
        cell *= 1.25
    n = len(uniq)
    cnt = np.bincount(inv, minlength=n).astype(np.float64)[:, None]
    V2 = np.stack([np.bincount(inv, weights=V[:, j], minlength=n) for j in range(3)], axis=1) / cnt
    N2 = None
    if N is not None and len(N) == len(V):
        Nn = np.asarray(N, dtype=np.float64)
        N2 = np.stack([np.bincount(inv, weights=Nn[:, j], minlength=n) for j in range(3)], axis=1)
        nrm = np.linalg.norm(N2, axis=1, keepdims=True)
        N2 = np.divide(N2, nrm, out=np.zeros_like(N2), where=nrm > 0)
    vals2 = None
    if values is not None and len(values) == len(V):
        vals2 = np.zeros(n, dtype=np.asarray(values).dtype)
        np.maximum.at(vals2, inv, np.asarray(values))
    # drop vertices no face references any more
    used = np.unique(F2) if len(F2) else np.zeros(0, np.int64)
    remap = np.full(n, -1, np.int64)
    remap[used] = np.arange(len(used))
    F2 = remap[F2] if len(F2) else np.zeros((0, 3), np.int64)
    V2 = V2[used]
    N2 = N2[used] if N2 is not None else None
    vals2 = vals2[used] if vals2 is not None else None
    log.debug("decimate: %d -> %d triangles (cell %.2f mm)", len(F), len(F2), cell)
    return V2, F2, N2, vals2
