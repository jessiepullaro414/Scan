"""Accumulation of laser-line points in the world frame.

:class:`LaserCloud` collects the per-frame laser points (camera frame ``C``, mm, as produced by
``ohs.vision.laser.laser_points``) together with the frame pose ``T_wc`` and stores them in the
world frame, grouped by laser name (``"vertical"`` / ``"horizontal"`` or any other label).

:meth:`LaserCloud.points` merges everything with voxel-average downsampling: points falling into
the same ``voxel_mm`` cube are replaced by their mean, which both thins the cloud and averages
the sub-pixel noise of repeated passes. ``min_count`` can additionally drop voxels seen fewer
than N times (isolated outliers).
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

import numpy as np

from ohs.geometry import transform_points

__all__ = ["LaserCloud", "voxel_average"]

log = logging.getLogger(__name__)

_KEY_BITS = 21  # per axis: +-2^20 voxels (+-210 m at 0.2 mm)
_KEY_OFF = 1 << (_KEY_BITS - 1)


def voxel_average(P: np.ndarray, voxel_mm: float, min_count: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """Mean point of every occupied ``voxel_mm`` cube; returns ``(points (M,3), counts (M,))``."""
    P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
    if len(P) == 0:
        return P.copy(), np.zeros(0, dtype=np.int64)
    k = np.floor(P / float(voxel_mm)).astype(np.int64) + _KEY_OFF
    if k.min() < 0 or k.max() >= (1 << _KEY_BITS):
        raise ValueError("points too far from the origin for this voxel size")
    key = (k[:, 0] << (2 * _KEY_BITS)) | (k[:, 1] << _KEY_BITS) | k[:, 2]
    _, inv, counts = np.unique(key, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    out = np.stack([np.bincount(inv, weights=P[:, a], minlength=len(counts)) for a in range(3)], axis=1)
    out /= counts[:, None]
    keep = counts >= int(min_count)
    return out[keep], counts[keep]


class LaserCloud:
    """World-frame laser point accumulator (mm)."""

    def __init__(self) -> None:
        self._chunks: dict[str, list[np.ndarray]] = {}
        self._n_frames = 0

    def __len__(self) -> int:
        """Number of raw (not downsampled) points."""
        return int(sum(c.shape[0] for chunks in self._chunks.values() for c in chunks))

    @property
    def names(self) -> list[str]:
        return list(self._chunks)

    @property
    def n_frames(self) -> int:
        """Number of :meth:`add` calls that contributed points."""
        return self._n_frames

    def clear(self) -> None:
        self._chunks.clear()
        self._n_frames = 0

    def add(self, pts_c: np.ndarray, T_wc: np.ndarray, name: str = "laser") -> int:
        """Add camera-frame points ``pts_c`` (N,3) seen at pose ``T_wc``; NaN rows are skipped.

        Returns the number of points added.
        """
        P = np.asarray(pts_c, dtype=np.float64).reshape(-1, 3)
        P = P[np.all(np.isfinite(P), axis=1)]
        if len(P) == 0:
            return 0
        T = np.asarray(T_wc, dtype=np.float64).reshape(4, 4)
        self._chunks.setdefault(name, []).append(transform_points(T, P))
        self._n_frames += 1
        return int(len(P))

    def raw_points(self, names: Iterable[str] | None = None) -> np.ndarray:
        """All accumulated world points (N,3), optionally only for the given laser names."""
        sel = self.names if names is None else [n for n in names if n in self._chunks]
        chunks = [c for n in sel for c in self._chunks[n]]
        return np.concatenate(chunks) if chunks else np.zeros((0, 3))

    def points(self, voxel_mm: float | None = 0.2, names: Iterable[str] | None = None,
               min_count: int = 1) -> np.ndarray:
        """Merged world points (M,3), voxel-averaged with ``voxel_mm`` (``None``/0 = raw)."""
        P = self.raw_points(names)
        if not voxel_mm or voxel_mm <= 0 or len(P) == 0:
            return P
        out, _ = voxel_average(P, voxel_mm, min_count)
        return out

    def save_ply(self, path: str | Path, voxel_mm: float | None = 0.2, names: Iterable[str] | None = None,
                 min_count: int = 1) -> Path:
        """Write :meth:`points` as a binary PLY (no normals)."""
        from ohs.track.export import save_points_ply  # noqa: PLC0415 (keeps import graph flat)

        P = self.points(voxel_mm, names, min_count)
        log.info("LaserCloud: %d raw points -> %d after %s mm voxel averaging", len(self), len(P), voxel_mm)
        return save_points_ply(path, P)
