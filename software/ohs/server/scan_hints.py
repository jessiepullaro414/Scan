"""Hints while scanning (FEATURE_PLAN N3, an experiment): light one weak patch of the live model and
say how to move to see it better.

**Status: not adopted.** Measured 2026-11-20 (VALIDATION.md, "Hints while scanning"): a simulated
operator who follows these hints did not end with a clearly higher Check share than one who moved
at random as often (bracket and car panel, three seeds, GPU path; bracket, two seeds, CPU path).
It stays as an option of :class:`ohs.server.session.ScanSession` (``guide=True``) that nothing
turns on; off, it costs one ``is None`` test per frame.

**What a cell knows.** The live model's coverage is counted per ``cell_mm`` (8 mm) cube of the world
(:class:`ohs.server.meshing.CoverageGrid`); the session keeps, for every fused frame, its pose and the
cells it saw (the rewind buffer). :class:`ViewMoments` turns those into, per cell: the number of
frames that saw it, the sum of the unit directions from the cell's centre to the camera and the sum
of their outer products (so the mean square cosine to any normal comes out without keeping the
looks), and the nearest camera distance. It is built on the mesh thread (``ohs-scan-mesh``) from the
frames added since its last update, at the rate the live model is meshed (every 1.5 s or slower),
never per frame; a rewind rebuilds it from the frames that are kept.

**Which cells are weak.** A cell the live mesh passes through is weak when it was seen by fewer than
:data:`FEW_FRAMES` frames (the yellow of the live colours: under half a second at the live rate),
when its looks were steep (the root mean square cosine between the looks and the mesh's normal in
the cell is under :data:`ohs.mesh.check.STEEP_COS`, 59.6 degrees: the angle at which the matcher's
sigma doubles), or when even its nearest look was farther than :data:`FAR_MM`. The steep test is
skipped where the mesh's normals in the cell disagree (an edge: their mean is shorter than
:data:`NORMAL_AGREE`), so a corner is not called steep for having two sides.

**One target.** Weak cells that touch (26 neighbours) form patches of at least :data:`MIN_CELLS`;
the target is the largest one near where the scanner points (weight ``exp(-(d / NEAR_MM)^2)`` on the
distance from the point the optical axis meets at the working distance), and it is kept while it
stays weak and scores at least half of the best (hysteresis: it does not jump between patches).

**One hint.** :meth:`ScanGuide.hint` (per frame, a few vector operations) turns where the target is
relative to the camera into one plain hint: turn to it when it is off the middle of the view; move
closer when it is far; come round left / right, or move higher / lower, when it is seen at a slant
from here; hold the scanner on it otherwise. :class:`GuideHintFilter` shows a hint once 4 of the
last 6 frames agree on it (the rule of the slide and turn hints, :class:`ohs.server.session.WeakHintFilter`)
and never while the tracking has something to say: that hint comes first, one hint at a time.

**Limits.** Everything is at the cell size: a patch is at least :data:`MIN_CELLS` cells (two 8 mm
cubes), and a part thinner than a cell shares its cells with what it lies on, so it is lit with
them. A hint never removes, hides or changes anything: the scan's result is the same with it or
without it.
"""
from __future__ import annotations

import collections
import math
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from ohs.server.meshing import _MASK, _OFF, cell_keys

__all__ = ["ViewMoments", "ScanGuide", "GuideHintFilter", "Target", "key_centres", "FEW_FRAMES", "FAR_MM",
           "MIN_CELLS", "NEAR_MM", "WORKING_MM", "LIT", "HINTS"]

#: a cell seen by fewer frames than this is "seen too little" (the yellow end of the live colours)
FEW_FRAMES = 4
#: the nearest look farther than this (mm) is "too far": sqrt(2) x 250 mm, ``ohs.mesh.check.FAR_RATIO``
FAR_MM = 354.0
#: the working distance the poses aim at (mm): where the lasers cross
WORKING_MM = 250.0
#: the mean of the mesh's unit normals in a cell must be at least this long for the steep test
NORMAL_AGREE = 0.7
#: a patch has at least this many cells
MIN_CELLS = 2
#: "near where the scanner points" (mm)
NEAR_MM = 120.0
#: the target is kept while it scores at least this share of the best patch
KEEP_SHARE = 0.5
#: off the middle of the view by more than this (degrees): "turn to it"
AIM_DEG = 20.0
#: seen from more than this off its normal (degrees): "come round"
ROUND_DEG = 30.0
#: the colour of the lit patch (RGBA): a clear blue, apart from the live model's yellow and green
LIT = np.array([40, 120, 255, 255], np.uint8)

_WHY = {"few": "seen too little", "steep": "seen only at a slant", "far": "seen only from far"}
#: code -> what to do (the text is "Lit area <why>: <what to do>")
HINTS = {
    "turn_left": "turn the scanner to the left",
    "turn_right": "turn the scanner to the right",
    "turn_up": "tilt the scanner up",
    "turn_down": "tilt the scanner down",
    "closer": "move closer",
    "round_left": "come round to the left",
    "round_right": "come round to the right",
    "higher": "move higher and tilt down",
    "lower": "move lower and tilt up",
    "hold": "hold the scanner on it",
}


def key_centres(keys: np.ndarray, cell_mm: float) -> np.ndarray:
    """The centres (N,3, mm) of the cells with keys ``keys`` (:func:`ohs.server.meshing.cell_keys`)."""
    k = np.asarray(keys, dtype=np.int64)
    q = np.stack([(k >> 42) & _MASK, (k >> 21) & _MASK, k & _MASK], axis=1) - _OFF
    return (q.astype(np.float64) + 0.5) * float(cell_mm)


def _ijk(keys: np.ndarray) -> np.ndarray:
    k = np.asarray(keys, dtype=np.int64)
    return np.stack([(k >> 42) & _MASK, (k >> 21) & _MASK, k & _MASK], axis=1)


class ViewMoments:
    """Per cell: frames, sum of view directions, sum of their outer products, nearest distance."""

    def __init__(self, cell_mm: float = 8.0) -> None:
        self.cell_mm = float(cell_mm)
        self.clear()

    def clear(self) -> None:
        self.keys = np.zeros(0, np.int64)
        self.n = np.zeros(0, np.float64)
        self.s1 = np.zeros((0, 3), np.float64)
        self.s2 = np.zeros((0, 6), np.float64)  # xx yy zz xy xz yz
        self.dmin = np.zeros(0, np.float64)

    def __len__(self) -> int:
        return len(self.keys)

    def add_frames(self, frames: Sequence[tuple[np.ndarray, np.ndarray]]) -> None:
        """Add frames given as ``(T_wc, cell keys)``."""
        ks, cs = [], []
        for T, keys in frames:
            if len(keys):
                ks.append(np.asarray(keys, np.int64))
                cs.append(np.broadcast_to(np.asarray(T, np.float64)[:3, 3], (len(keys), 3)))
        if not ks:
            return
        K = np.concatenate(ks)
        C = np.concatenate(cs)
        v = C - key_centres(K, self.cell_mm)
        d = np.linalg.norm(v, axis=1)
        v = v / np.maximum(d, 1e-9)[:, None]
        o = np.stack([v[:, 0] ** 2, v[:, 1] ** 2, v[:, 2] ** 2, v[:, 0] * v[:, 1], v[:, 0] * v[:, 2],
                      v[:, 1] * v[:, 2]], axis=1)
        allk, inv = np.unique(np.concatenate([self.keys, K]), return_inverse=True)
        m = len(allk)
        n = np.zeros(m)
        s1 = np.zeros((m, 3))
        s2 = np.zeros((m, 6))
        dmin = np.full(m, np.inf)
        old, new = inv[: len(self.keys)], inv[len(self.keys):]
        n[old], s1[old], s2[old], dmin[old] = self.n, self.s1, self.s2, self.dmin
        n += np.bincount(new, minlength=m)
        for j in range(3):
            s1[:, j] += np.bincount(new, weights=v[:, j], minlength=m)
        for j in range(6):
            s2[:, j] += np.bincount(new, weights=o[:, j], minlength=m)
        np.minimum.at(dmin, new, d)
        self.keys, self.n, self.s1, self.s2, self.dmin = allk, n, s1, s2, dmin

    def rms_cos(self, idx: np.ndarray, normals: np.ndarray) -> np.ndarray:
        """Root mean square cosine between the looks of cells ``idx`` and ``normals`` (M,3, unit)."""
        s, N = self.s2[idx], normals
        q = (s[:, 0] * N[:, 0] ** 2 + s[:, 1] * N[:, 1] ** 2 + s[:, 2] * N[:, 2] ** 2
             + 2 * (s[:, 3] * N[:, 0] * N[:, 1] + s[:, 4] * N[:, 0] * N[:, 2] + s[:, 5] * N[:, 1] * N[:, 2]))
        return np.sqrt(np.clip(q / np.maximum(self.n[idx], 1.0), 0.0, 1.0))


@dataclass
class Target:
    keys: np.ndarray  # sorted cell keys of the patch
    centre: np.ndarray  # mm, world
    normal: np.ndarray  # unit, world (the side the surface faces)
    reason: str  # "few" | "steep" | "far"
    score: float


class ScanGuide:
    """The weak cells, the target patch and its hint (module docstring). :meth:`update` runs on the
    mesh thread, :meth:`hint` on the scan thread; the target is swapped in one assignment."""

    def __init__(self, cell_mm: float = 8.0) -> None:
        from ohs.mesh.check import STEEP_COS

        self.cell_mm = float(cell_mm)
        self.steep_cos = float(STEEP_COS)
        self.moments = ViewMoments(cell_mm)
        self.target: Target | None = None
        self.n_weak = 0

    def reset(self) -> None:
        self.moments.clear()
        self.target = None

    # -------------------------------------------------------------- mesh thread
    def update(self, V: np.ndarray, N: np.ndarray, frames: Sequence[tuple[np.ndarray, np.ndarray]],
               T_now: np.ndarray | None, rebuild: bool = False) -> Target | None:
        """Add ``frames`` (``(T_wc, keys)`` since the last update; all kept frames with ``rebuild``),
        find the weak cells of the mesh ``V``, ``N`` and choose the target near the pose ``T_now``."""
        if rebuild:
            self.moments.clear()
        self.moments.add_frames(frames)
        V = np.asarray(V, np.float64)
        N = np.asarray(N, np.float64)
        mo = self.moments
        if len(V) == 0 or len(mo) == 0:
            self.target, self.n_weak = None, 0
            return None
        vk = cell_keys(V, self.cell_mm)
        ck, inv = np.unique(vk, return_inverse=True)
        cnt = np.bincount(inv).astype(np.float64)
        ns = np.stack([np.bincount(inv, weights=N[:, j], minlength=len(ck)) for j in range(3)], axis=1) / cnt[:, None]
        agree = np.linalg.norm(ns, axis=1)
        nrm = ns / np.maximum(agree, 1e-9)[:, None]
        i = np.clip(np.searchsorted(mo.keys, ck), 0, len(mo.keys) - 1)
        seen = mo.keys[i] == ck
        frames_n = np.where(seen, mo.n[i], 0.0)
        few = frames_n < FEW_FRAMES
        rc = np.ones(len(ck))
        rc[seen] = mo.rms_cos(i[seen], nrm[seen])
        steep = seen & (agree >= NORMAL_AGREE) & (rc < self.steep_cos) & ~few
        far = seen & (np.where(seen, mo.dmin[i], 0.0) > FAR_MM) & ~few & ~steep
        weak = few | steep | far
        self.n_weak = int(weak.sum())
        if not weak.any():
            self.target = None
            return None
        why = np.where(few, 0, np.where(steep, 1, 2))[weak]
        wk, wn = ck[weak], nrm[weak]
        labels, n_lab = _label(wk)
        look = None
        if T_now is not None:
            T = np.asarray(T_now, np.float64)
            look = T[:3, 3] + WORKING_MM * T[:3, 2]
        cen = key_centres(wk, self.cell_mm)
        best, best_score = None, 0.0
        cands = []
        for lab in range(n_lab):
            m = labels == lab
            if m.sum() < MIN_CELLS:
                continue
            c = cen[m].mean(axis=0)
            w = 1.0 if look is None else math.exp(-(float(np.linalg.norm(c - look)) / NEAR_MM) ** 2)
            score = float(m.sum()) * w
            cands.append((m, c, score))
            if score > best_score:
                best, best_score = len(cands) - 1, score
        if best is None:
            self.target = None
            return None
        pick = best
        old = self.target
        if old is not None:  # hysteresis: keep the patch that holds most of the old target's cells
            share = [np.isin(wk[m], old.keys).sum() for m, _, _ in cands]
            j = int(np.argmax(share))
            if share[j] > 0 and cands[j][2] >= KEEP_SHARE * best_score:
                pick = j
        m, c, score = cands[pick]
        nn = wn[m].sum(axis=0)
        nn = nn / max(float(np.linalg.norm(nn)), 1e-9)
        r = np.bincount(why[m], minlength=3)
        reason = ("few", "steep", "far")[int(np.argmax(r))]
        tgt = Target(np.sort(wk[m]), c, nn, reason, score)
        self.target = tgt
        return tgt

    def lit(self, V: np.ndarray) -> np.ndarray:
        """Which vertices lie in the target patch's cells."""
        t = self.target
        if t is None or len(V) == 0:
            return np.zeros(len(V), bool)
        q = cell_keys(V, self.cell_mm)
        i = np.clip(np.searchsorted(t.keys, q), 0, len(t.keys) - 1)
        return t.keys[i] == q

    # -------------------------------------------------------------- scan thread
    def hint(self, T_wc: np.ndarray) -> dict | None:
        """``{"code", "reason", "text"}`` for the camera at ``T_wc``, or None without a target."""
        t = self.target
        if t is None:
            return None
        return hint_for(t, np.asarray(T_wc, np.float64))


def hint_for(t: Target, T: np.ndarray) -> dict:
    R, c = T[:3, :3], T[:3, 3]
    q = R.T @ (t.centre - c)  # the target in the camera frame (x right, y down, z forward)
    off = math.degrees(math.atan2(math.hypot(q[0], q[1]), q[2]))
    if q[2] <= 0 or off > AIM_DEG:
        if abs(q[0]) >= abs(q[1]):
            code = "turn_left" if q[0] < 0 else "turn_right"
        else:
            code = "turn_up" if q[1] < 0 else "turn_down"
    elif float(np.linalg.norm(t.centre - c)) > FAR_MM:
        code = "closer"
    else:
        u = (c - t.centre) / max(float(np.linalg.norm(c - t.centre)), 1e-9)
        if math.degrees(math.acos(float(np.clip(u @ t.normal, -1.0, 1.0)))) > ROUND_DEG:
            d = R.T @ (t.normal - u)  # where the camera should move, in its own frame
            if abs(d[0]) >= abs(d[1]):
                code = "round_left" if d[0] < 0 else "round_right"
            else:
                code = "higher" if d[1] < 0 else "lower"
        else:
            code = "hold"
    return {"code": code, "reason": t.reason, "text": f"Lit area {_WHY[t.reason]}: {HINTS[code]}"}


def _label(keys: np.ndarray) -> tuple[np.ndarray, int]:
    """Connected components (26 neighbours) of the cells ``keys``: (label per key, count)."""
    from scipy import ndimage

    ijk = _ijk(keys)
    lo = ijk.min(axis=0)
    q = ijk - lo
    dims = q.max(axis=0) + 1
    grid = np.zeros(tuple(int(d) for d in dims), bool)
    grid[q[:, 0], q[:, 1], q[:, 2]] = True
    lab, n = ndimage.label(grid, structure=np.ones((3, 3, 3), bool))
    return lab[q[:, 0], q[:, 1], q[:, 2]] - 1, int(n)


class GuideHintFilter:
    """The rule of :class:`ohs.server.session.WeakHintFilter` for the guide's hint: a hint shows once
    ``need`` of the last ``window`` frames gave it and stays while they do; while the tracking shows
    a hint of its own (any state but ``ok``) the guide says nothing, so there is one hint at a time."""

    def __init__(self, window: int = 6, need: int = 4) -> None:
        self._recent: collections.deque = collections.deque(maxlen=window)
        self._need = need
        self._shown: dict | None = None

    def apply(self, guide: dict | None, tracking: dict) -> dict | None:
        code = guide["code"] if guide else None
        self._recent.append(code)
        votes = collections.Counter(c for c in self._recent if c is not None)
        if self._shown is not None and votes.get(self._shown["code"], 0) >= self._need:
            if code == self._shown["code"]:
                self._shown = guide  # the same hint, its newest words
        else:
            self._shown = guide if code is not None and votes[code] >= self._need else None
        if tracking.get("state") != "ok" or tracking.get("reason"):
            return None
        return self._shown


def lit_colors(colors: np.ndarray, lit: np.ndarray) -> np.ndarray:
    """``colors`` (RGBA uint8) with the lit vertices in :data:`LIT`."""
    out = np.array(colors, copy=True)
    if lit.any():
        out[lit] = LIT
    return out


def frames_of(ring: Sequence[Any]) -> list[tuple[np.ndarray, np.ndarray]]:
    """``(T, keys)`` of the session's kept frames."""
    return [(e.T, e.keys) for e in ring]
