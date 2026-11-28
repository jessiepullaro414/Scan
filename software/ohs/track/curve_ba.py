"""Laser-curve bundle adjustment: the laser lines' crossings line up the camera poses (research plan
7.3, a first version: poses only, no self-calibration yet).

Every frame gives two accurate 3D curves, one per laser line, triangulated on the calibrated planes
independently of the dense stereo (four with four lasers: the points of a family go to the plane
their stereo check picks, :func:`~ohs.vision.laser.assign_planes`; there is no dense depth here,
so a point that passes on both planes with 3D points more than ``family_agree_mm`` apart, as
often on a horizontal line, is dropped). As the scanner moves, curves from different
frames cross on the surface, and at every crossing the two curves must meet. :func:`laser_curve_adjustment` solves the
poses so that they do:

1. **Curves** (:func:`frame_curves`): the lines of up to ``max_frames`` tracked frames, spread over
   the scan, extracted at full resolution, triangulated, checked against the right image, with
   unit tangents from their neighbours along each line (:func:`curve_tangents`; a line fitted over
   a short stretch is a setting, ``tangent_fit_mm``, off by default).
2. **Crossings** (:func:`find_crossings`): point pairs from curves of different frames within
   ``radius_mm`` whose tangents differ by at least ``min_angle_deg``; the closest points of the two
   local lines must lie within ``local_mm`` of the samples. One crossing per pair of curves. Its
   residual is how far the curves miss each other along the normal of the two tangents.
3. **The solve** (:func:`adjust_poses`): Gauss-Newton on a small motion of every frame (about the
   scan's centre, rotations scaled to mm at ``lever_mm``; the first frame fixed), with three
   kinds of terms:

   - the crossings (``sigma_mm``, Tukey weights);
   - the tracked motion between consecutive frames as a random-walk prior (``odo_mm`` /
     ``odo_deg`` per frame);
   - a weak point-to-plane term of each frame's own depth against a reference surface
     (``dense_sigma_mm``; the base mesh). Without it, frames that see few crossings (a table far
     from the part) turn freely, and the surfaces they see bend.

   It runs ``rounds`` times, finding the crossings again at the new poses.
4. **Never worse:** the solve is first fitted on part of the crossings, and the poses are changed
   only when the crossings it did not see then meet clearly better (their median miss at most
   ``holdout_max_ratio`` of before). Otherwise the crossings only show the lasers' own scatter, and
   fitting them would bend good poses: on simulated scans without drift the fit moved the poses by
   0.4 mm, and the held-out crossings met worse.

   **The default since 2026-11-21 (the owner's decision, ENGINE_PLAN E-D2): whole frames held out**
   (``holdout="frames"``, :func:`holdout_splits`): every ``holdout_every``-th frame of those used is
   held out, each split in turn; the fit sees only the crossings between two fitted frames, the test
   is the crossings between two held-out frames, and the worse split must pass. Measured on the
   scorecard's app chain and the engine tests (``VALIDATION.md``, "Straighten's laser crossings on
   what Make mesh runs"): it refuses every standing-walls recording, tracked (0.96 to 1.08 against
   the limit of 0.85) and at the true poses (0.89 to 0.93), and still mends the tests' real drift
   (0.57 / 0.61). What it costs against the old test: one walls recording stays as thick as the
   frame pass reads it (its 2 mm wall +0.54 mm instead of -0.05, the 0.5 mm wall's face 0.45
   covered instead of 0.65), and one of the two speckled brackets keeps a camera path 0.3 mm worse
   (median 1.02 instead of 0.73 mm), since the frames test refuses it (1.05) where the old one
   acted. It still acts on the other speckled bracket (0.76). What it does not catch: a walls
   recording with drift added passes (0.61), and acting then makes the path worse. Where it acts,
   the scan's report says so (``warning``, :func:`acted_warning`).

   **The old test stays a setting:** ``holdout="crossings"`` (the default until 2026-11-21) fits half
   the crossings at random and tests the other half. A frame's crossings share that frame's errors,
   so a random half is not an independent check: on thin standing walls it passed even at the true
   poses and then moved them by millimetres. ``enabled=False`` switches the adjustment off.
5. **Corrections** go to every tracked frame by interpolation between the frames used
   (:func:`ohs.track.posegraph.interpolate_poses`).

**Frames in turn** (dot frames and laser frames, :mod:`ohs.app.process`): the trajectory holds the dot frames, which
have no laser line; the curves come from the laser frames, each placed between the given dot frames beside it by the
frame pass's rule and tied to them in the solve (:func:`laser_curve_adjustment`, "Frames in turn"). A recording
without kinds is adjusted exactly as before (``tests/test_track_curve_ba_in_turn.py``).

On the simulator (``VALIDATION.md``, "Laser-curve adjustment"), a full-resolution 400 deg orbit
around the sphere with random-walk drift, after Straighten: the sphere's mesh error 0.17 -> 0.12 mm
and the table's 0.12 -> 0.11 mm (true poses: 0.09). Short scans have few crossings: a frame's
horizontal line meets another frame's vertical line near where the two lines cross in the image,
where the extraction drops points.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from ohs.geometry import invert_se3, so3_exp, so3_log

__all__ = ["CurveParams", "Crossings", "frame_curves", "curve_tangents", "find_crossings", "crossing_residuals", "adjust_poses",
           "holdout_splits", "acted_warning", "laser_curve_adjustment"]

log = logging.getLogger(__name__)

Progress = Callable[[float, str], None]
Curve = tuple[str, np.ndarray, np.ndarray]  # (line name, points (n,3) frame C, unit tangents (n,3) frame C)


@dataclass(frozen=True)
class CurveParams:
    max_frames: int = 200  # frames used, spread over the scan (the rest follow by interpolation)
    radius_mm: float = 3.0  # crossing candidates: point pairs this close
    local_mm: float = 1.5  # the crossing must lie this close to both samples
    min_angle_deg: float = 30.0  # the two curves' tangents differ by at least this
    # a curve's direction (:func:`curve_tangents`). 0, the default: from each sample's two neighbours. More than 0
    # (a setting, measured with 1.5): the line fitted to the samples within ``tangent_fit_mm``, used only where the
    # unbroken curve reaches ``tangent_side_mm`` on both sides and lies within ``tangent_tol_mm`` of that line. It
    # removes the false crossings at bends and stays off: the held-out test is then fooled more easily on thin
    # standing walls (the docstring of :func:`curve_tangents`)
    tangent_fit_mm: float = 0.0
    tangent_side_mm: float = 0.75
    tangent_tol_mm: float = 0.1
    min_crossings: int = 150
    sigma_mm: float = 0.25  # a crossing's miss (the lasers' own scatter, with margin)
    odo_mm: float = 0.2  # tracked motion between consecutive frames: random walk per frame
    odo_deg: float = 0.05
    dense_sigma_mm: float = 1.0  # each frame's depth against the reference surface (weak)
    dense_points: int = 400  # depth points per frame
    lever_mm: float = 250.0
    iterations: int = 3
    rounds: int = 2
    holdout_max_ratio: float = 0.85
    # the held-out test (module docstring, step 4): "frames" (the default since 2026-11-21, ENGINE_PLAN E-D2), whole
    # frames held out (every ``holdout_every``-th frame of those used: the fit sees none of their crossings, the test
    # is the crossings between two of them, which only the motion prior and the depth term move); "crossings" (the
    # default until 2026-11-21, a setting), half the crossings at random
    holdout: str = "frames"
    holdout_every: int = 2
    holdout_min_test: int = 20  # "frames": fewer test crossings than this and the poses are unchanged
    enabled: bool = True  # False: the adjustment is switched off (the poses unchanged, reason "off")


# --------------------------------------------------------------------------- 1. curves
def frame_curves(reader: Any, calib: Any, frames: Sequence[int], params: CurveParams | None = None,
                 cancelled: Callable[[], bool] | None = None,
                 progress: Progress | None = None) -> dict[int, list[Curve]]:
    """``{frame index: [(line, points, tangents)]}``: each frame's laser curves at full resolution,
    triangulated on the calibrated planes and checked against the right image (frame C, mm)."""
    from ohs.app.pipeline import default_laser_params, effective_scale  # noqa: PLC0415
    from ohs.vision.laser import (  # noqa: PLC0415
        assign_planes, extract_laser_lines, is_line_name, line_family, lines_per_family, measuring_camera, stereo_check,
        triangulate_plane, triangulate_right)
    from ohs.vision.rectify import Rectifier  # noqa: PLC0415

    rect = Rectifier(calib.stereo, 1.0)
    K, B = rect.K, rect.baseline
    lp = default_laser_params(effective_scale(rect.image_size[0]))
    planes = {n: pl for n, pl in (calib.lasers or {}).items() if is_line_name(n)}
    if lines_per_family(planes) != lp.lines_per_family:  # four lasers: two lines of each family
        lp = replace(lp, lines_per_family=lines_per_family(planes))
    family = {n: [m for m in planes if line_family(m) == line_family(n)] for n in planes}
    pos_of = {int(i): k for k, i in enumerate(reader.indices)}
    out: dict[int, list[Curve]] = {}
    for done, f in enumerate(frames):
        if cancelled is not None and cancelled():
            raise InterruptedError("cancelled")
        if progress is not None:
            progress(done / max(len(frames), 1), f"reading the laser lines of frame {done + 1} of {len(frames)}")
        if int(f) not in pos_of or not planes:
            continue
        fr = reader.frame(pos_of[int(f)])
        L, R = rect.rectify(fr.left, fr.right)
        ll = extract_laser_lines(L, lp)
        lr = extract_laser_lines(R, lp) if lp.stereo_check else None
        curves: list[Curve] = []
        labels: dict[str, np.ndarray] = {}  # several planes of a family: which plane each point is on
        for name, plane in planes.items():
            uv, seg = ll.get(name), ll.segments(name)
            if len(uv) < 5:
                continue
            fam = family[name]
            if len(fam) > 1:
                key = line_family(name)
                if key not in labels:
                    labels[key] = assign_planes(uv, [planes[m] for m in fam], K, B, lp,
                                                lr.get(name) if lr is not None else None)[1]
                P = triangulate_plane(uv, K, plane)
                ok = labels[key] == fam.index(name)
            else:
                P = triangulate_plane(uv, K, plane)
                ok = np.isfinite(P[:, 2]) & (P[:, 2] >= lp.min_depth_mm) & (P[:, 2] <= lp.max_depth_mm)
                if lr is not None and ok.any():
                    ok &= stereo_check(P, lr.get(name), K, B, lp.stereo_tol_px)
            if lr is not None and lp.triangulate_from == "farther" and measuring_camera(plane, B) == "right" and ok.any():
                # measured from the right camera, the farther one (ohs.vision.laser.measuring_camera)
                i = np.flatnonzero(ok)
                Q, good_r = triangulate_right(uv[i], plane, K, B, lr.get(name), lr.segments(name), lp.stereo_tol_px)
                good_r &= (Q[:, 2] >= lp.min_depth_mm) & (Q[:, 2] <= lp.max_depth_mm)
                P = P.copy()
                P[i] = Q
                ok[i] = good_r
            tan, good = curve_tangents(P, ok, seg, params)
            if good.sum() >= 10:
                curves.append((name, P[good], tan[good]))
        out[int(f)] = curves
    return out


#: the distance between a laser line's samples that ``tangent_fit_mm`` and its two companions are meant for (one
#: pixel at 250 mm in a 1280 px image is 0.35 mm)
_FULL_STEP_MM = 0.4


def curve_tangents(P: np.ndarray, ok: np.ndarray, seg: np.ndarray, params: CurveParams | None = None,
                   ) -> tuple[np.ndarray, np.ndarray]:
    """``(tangents, good)`` of one laser line's samples ``P`` (n, 3; in order along the line; ``ok`` the valid ones,
    ``seg`` the polyline each belongs to): the unit tangent of every sample and which samples a crossing may use.

    With ``tangent_fit_mm`` = 0 (the default): from the two neighbours, every sample that has both.

    With ``tangent_fit_mm`` > 0 (a setting): the direction of the straight line fitted to the sample's unbroken
    stretch of the curve within ``tangent_fit_mm`` of it (valid samples of one polyline, none more than 1 mm from the
    next). A sample is used only where that stretch reaches ``tangent_side_mm`` on both sides and all of it lies
    within ``tangent_tol_mm`` of the line.

    Why the setting exists (ENGINE_PLAN X8, ``VALIDATION.md`` "Why the laser crossings miss at the true poses"):
    where a laser line bends over an edge its samples cut the corner, and one sample off the surface tilts its
    neighbours; a direction from two neighbours a third of a millimetre apart is then 30 degrees and more off, two
    nearly parallel lines of neighbouring frames pass for a crossing, and their "miss" is the distance between the
    lines, 1 to 3 mm at the true poses. The fitted line removes those (45 of 402 crossings on a walls recording, 67 of
    280 on a speckled bracket; then 0 and 1).

    Why it is off: the crossings that remain show the lasers' second error more clearly, the projector's dots pulling
    a line's centre, the same way in neighbouring frames (correlation 0.8 one frame apart). The held-out test takes
    that for drift: with the fitted line a walls recording at the TRUE poses passes it (640 px: ratio 0.57, the
    poses moved by 2.9 mm; with the two neighbours 0.89, refused). Until the test can tell the two apart, the
    cleaner crossings would move thin walls."""
    p = params or CurveParams()
    P = np.asarray(P, dtype=np.float64)
    ok = np.asarray(ok, dtype=bool)
    n = len(P)
    tan = np.zeros((n, 3))
    if n < 3:
        return tan, np.zeros(n, dtype=bool)
    inner = np.zeros(n, dtype=bool)  # both neighbours on the same polyline and valid
    if len(seg) == n:
        inner[1:-1] = (seg[2:] == seg[1:-1]) & (seg[:-2] == seg[1:-1])
    inner[1:-1] &= ok[2:] & ok[:-2]
    tan[1:-1] = P[2:] - P[:-2]
    tl = np.linalg.norm(tan, axis=1)
    good = ok & inner & (tl > 1e-6) & (tl < 3.0)
    tan[good] /= tl[good, None]
    w = float(p.tangent_fit_mm)
    if w <= 0 or not good.any():
        return tan, good
    step = np.linalg.norm(P[1:] - P[:-1], axis=1)
    link = ok[1:] & ok[:-1] & np.isfinite(step) & (step < 1.0)  # sample k and k + 1 on one unbroken stretch
    if len(seg) == n:
        link &= seg[1:] == seg[:-1]
    typical = float(np.median(step[link])) if link.any() else 0.0
    if typical <= 0:
        return tan, np.zeros(n, dtype=bool)
    # samples farther apart than at full resolution and 250 mm (a smaller image, a far or slanted surface) are also
    # less exact, in proportion: the three lengths grow with them, so that the fit keeps its five to ten samples
    grow = max(1.0, typical / _FULL_STEP_MM)
    w, side, tol = w * grow, float(p.tangent_side_mm) * grow, float(p.tangent_tol_mm) * grow
    K = int(min(48, math.ceil(w / typical) + 1))
    idx = np.arange(n)
    # the window of each sample: itself and up to K samples on either side, as far as the stretch is unbroken and
    # within w of the sample
    member = np.zeros((n, 2 * K + 1), dtype=bool)
    member[:, K] = ok
    reach = np.zeros((n, 2))
    for side, sgn in ((0, -1), (1, 1)):
        alive = ok.copy()
        for d in range(1, K + 1):
            j = idx + sgn * d
            inside = (j >= 0) & (j < n)
            jj = np.clip(j, 0, n - 1)
            k = np.clip(np.minimum(jj, jj - sgn), 0, n - 2)  # the link between this sample and the one before it
            dist = np.linalg.norm(P[jj] - P, axis=1)
            alive = alive & inside & link[k] & (dist <= w)
            member[:, K + sgn * d] = alive
            reach[alive, side] = np.maximum(reach[alive, side], dist[alive])
    cols = np.clip(idx[:, None] + np.arange(-K, K + 1)[None, :], 0, n - 1)
    Q = np.where(member[:, :, None], P[cols], 0.0)
    cnt = member.sum(axis=1)
    mean = Q.sum(axis=1) / np.maximum(cnt, 1)[:, None]
    Qc = np.where(member[:, :, None], Q - mean[:, None, :], 0.0)
    cov = np.einsum("nki,nkj->nij", Qc, Qc)
    cov[~np.all(np.isfinite(cov), axis=(1, 2))] = 0.0
    t = np.linalg.eigh(cov)[1][:, :, 2]  # the direction of the largest spread
    along = np.einsum("nki,ni->nk", Qc, t)
    off = np.linalg.norm(Qc - along[:, :, None] * t[:, None, :], axis=2).max(axis=1)
    fit = good & (cnt >= 5) & (reach.min(axis=1) >= side) & (off <= tol)
    flip = np.einsum("ni,ni->n", t, tan) < 0
    t[flip] *= -1.0
    tan[fit] = t[fit]
    return tan, fit


# --------------------------------------------------------------------------- 2. crossings
@dataclass
class Crossings:
    """Crossings of two curves: frames ``fa`` / ``fb``, the crossing point on each curve and its
    tangent, in the frame's camera frame (mm)."""

    fa: np.ndarray
    xa: np.ndarray
    ta: np.ndarray
    fb: np.ndarray
    xb: np.ndarray
    tb: np.ndarray

    def __len__(self) -> int:
        return len(self.fa)

    def subset(self, mask: np.ndarray) -> "Crossings":
        return Crossings(*(getattr(self, k)[mask] for k in ("fa", "xa", "ta", "fb", "xb", "tb")))


def _stack(T: dict[int, np.ndarray], frames: np.ndarray) -> np.ndarray:
    keys = sorted(T)
    pos = {f: k for k, f in enumerate(keys)}
    S = np.stack([T[f] for f in keys])
    return S[[pos[int(f)] for f in frames]]


#: candidate pairs filtered at a time by :func:`find_crossings` (about 70 MB of temporaries)
_CANDIDATE_PIECE = 1 << 19


def _local_crossings(pairs: np.ndarray, Xw_: np.ndarray, Tw_: np.ndarray, Fr_: np.ndarray, sin_min: float,
                     local_mm: float) -> tuple[np.ndarray, ...]:
    """``(i, j, sa, sb, dist)`` of the candidate ``pairs`` (rows ``i, j``) from different frames whose
    tangents differ enough and whose local lines' closest points lie within ``local_mm`` of both samples."""
    i, j = pairs[:, 0], pairs[:, 1]
    keep = Fr_[i] != Fr_[j]
    i, j = i[keep], j[keep]
    keep = np.linalg.norm(np.cross(Tw_[i], Tw_[j]), axis=1) > sin_min
    i, j = i[keep], j[keep]
    a, b, ta, tb = Xw_[i], Xw_[j], Tw_[i], Tw_[j]
    w0 = a - b
    A_ = np.einsum("ij,ij->i", ta, ta)
    B_ = np.einsum("ij,ij->i", ta, tb)
    C_ = np.einsum("ij,ij->i", tb, tb)
    D_ = np.einsum("ij,ij->i", ta, w0)
    E_ = np.einsum("ij,ij->i", tb, w0)
    den = np.maximum(A_ * C_ - B_ * B_, 1e-12)
    sa = (B_ * E_ - C_ * D_) / den
    sb = (A_ * E_ - B_ * D_) / den
    dist = np.linalg.norm((a + sa[:, None] * ta) - (b + sb[:, None] * tb), axis=1)
    local = (np.abs(sa) < local_mm) & (np.abs(sb) < local_mm)
    return i[local], j[local], sa[local], sb[local], dist[local]


def find_crossings(curves: dict[int, list[Curve]], T: dict[int, np.ndarray],
                   params: CurveParams | None = None) -> Crossings:
    """The crossings of curves from different frames at poses ``T`` (module docstring, step 2).

    The candidates (point pairs within ``radius_mm``) are filtered :data:`_CANDIDATE_PIECE` at a time:
    with the scanner held nearly still they reach tens of millions."""
    from scipy.spatial import cKDTree  # noqa: PLC0415

    p = params or CurveParams()
    Xw, Tw, Xc, Tc, Fr, Cid = [], [], [], [], [], []
    cid = 0
    for f, cs in curves.items():
        if f not in T:
            continue
        R, t = T[f][:3, :3], T[f][:3, 3]
        for _, P, tan in cs:
            Xw.append(P @ R.T + t)
            Tw.append(tan @ R.T)
            Xc.append(P)
            Tc.append(tan)
            Fr.append(np.full(len(P), f))
            Cid.append(np.full(len(P), cid))
            cid += 1
    empty = Crossings(np.zeros(0, dtype=np.int64), np.zeros((0, 3)), np.zeros((0, 3)), np.zeros(0, dtype=np.int64),
                      np.zeros((0, 3)), np.zeros((0, 3)))
    if cid < 2:
        return empty
    Xw_, Tw_, Xc_, Tc_ = (np.concatenate(a) for a in (Xw, Tw, Xc, Tc))
    Fr_, Cid_ = np.concatenate(Fr), np.concatenate(Cid)
    pairs = cKDTree(Xw_).query_pairs(float(p.radius_mm), output_type="ndarray")
    if len(pairs) == 0:
        return empty
    # the candidates are filtered a piece at a time, in order (each test is per pair, so the survivors
    # and their order are those of filtering all at once): memory ~16 bytes a candidate (the pairs) plus
    # one piece, not ~140 bytes a candidate (FEATURE_PLAN R5: tens of millions with the scanner held still)
    sin_min = math.sin(math.radians(p.min_angle_deg))
    kept: list[tuple[np.ndarray, ...]] = []
    for s in range(0, len(pairs), _CANDIDATE_PIECE):
        kept.append(_local_crossings(pairs[s:s + _CANDIDATE_PIECE], Xw_, Tw_, Fr_, sin_min, float(p.local_mm)))
    del pairs
    i, j, sa, sb, dist = (np.concatenate([k[n] for k in kept]) for n in range(5))
    del kept
    if len(i) == 0:
        return empty
    lo, hi = np.minimum(Cid_[i], Cid_[j]), np.maximum(Cid_[i], Cid_[j])
    key = lo.astype(np.int64) * (cid + 1) + hi
    order = np.lexsort((dist, key))
    first = np.ones(len(order), dtype=bool)
    first[1:] = key[order][1:] != key[order][:-1]
    sel = order[first]
    i, j, sa, sb = i[sel], j[sel], sa[sel], sb[sel]
    return Crossings(Fr_[i].astype(np.int64), Xc_[i] + sa[:, None] * Tc_[i], Tc_[i], Fr_[j].astype(np.int64),
                     Xc_[j] + sb[:, None] * Tc_[j], Tc_[j])


def crossing_residuals(T: dict[int, np.ndarray], cr: Crossings) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """``(r, m, Xa, Xb)``: each crossing's miss along ``m``, the unit normal of the two world
    tangents (mm), and the two crossing points in the world."""
    if len(cr) == 0:
        z = np.zeros((0, 3))
        return np.zeros(0), z, z, z
    Ta, Tb = _stack(T, cr.fa), _stack(T, cr.fb)
    Xa = np.einsum("nij,nj->ni", Ta[:, :3, :3], cr.xa) + Ta[:, :3, 3]
    Xb = np.einsum("nij,nj->ni", Tb[:, :3, :3], cr.xb) + Tb[:, :3, 3]
    m = np.cross(np.einsum("nij,nj->ni", Ta[:, :3, :3], cr.ta), np.einsum("nij,nj->ni", Tb[:, :3, :3], cr.tb))
    m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)
    return np.einsum("ij,ij->i", m, Xa - Xb), m, Xa, Xb


# --------------------------------------------------------------------------- 3. the solve
def _tukey(r: np.ndarray, floor: float) -> np.ndarray:
    k = 4.685 * max(1.4826 * float(np.median(np.abs(r))) if len(r) else 0.0, floor)
    return np.where(np.abs(r) < k, (1.0 - (r / k) ** 2) ** 2, 0.0)


def adjust_poses(T: dict[int, np.ndarray], cr: Crossings, frames: Sequence[int], params: CurveParams | None = None,
                 dense: tuple[dict[int, np.ndarray], Any, np.ndarray, np.ndarray] | None = None,
                 gaps: dict[int, float] | None = None,
                 tied: dict[int, tuple[Sequence[int], Sequence[float], float]] | None = None,
                 ) -> tuple[dict[int, np.ndarray], list[dict]]:
    """Gauss-Newton on the poses ``T`` of ``frames`` (module docstring, step 3); ``frames[0]``
    stays fixed. ``dense``: ``(points per frame (frame C), k-d tree of the reference vertices,
    vertices, normals)``. ``gaps``: processed frames from each frame to the next (default 1), for
    the random-walk prior. Returns the new poses and one line of numbers per iteration.

    ``tied`` (frames in turn): frames of ``frames`` whose pose is no unknown of its own but placed between others,
    ``{frame: (its neighbours, their times, its time)}`` (a laser frame between the dot frames beside it). Its pose is
    :func:`ohs.track.pose_between.pose_between` of the neighbours' poses (the rotations as turns from the neighbour
    just before it, as :func:`~ohs.track.pose_between.place_laser_frames` places it), its crossings move the
    neighbours with the weights of the same polynomial, and it is placed again after every step. The motion prior,
    ``gaps`` and ``frames[0]`` concern the other frames, in their order. Without ``tied`` the solve is as before."""
    import scipy.sparse as sp  # noqa: PLC0415
    import scipy.sparse.linalg as spla  # noqa: PLC0415

    p = params or CurveParams()
    frames = [int(f) for f in frames]
    T = {f: np.asarray(T[f], dtype=np.float64).copy() for f in frames}
    tied = {int(f): v for f, v in (tied or {}).items()}
    free = [f for f in frames if f not in tied] if tied else frames  # the frames with unknowns of their own
    if len(free) < 2:
        return T, []
    col = {f: k - 1 for k, f in enumerate(free)}
    n = 6 * (len(free) - 1)
    o = np.mean([T[f][:3, 3] for f in free], axis=0)
    L = float(p.lever_mm)
    D = np.array([L] * 3 + [1.0] * 3)
    T0 = {f: T[f].copy() for f in free}
    wo = np.array([1.0 / (L * math.radians(p.odo_deg)) ** 2] * 3 + [1.0 / p.odo_mm ** 2] * 3)
    info: list[dict] = []
    weights: dict[int, list[tuple[int, float]]] = {}
    if tied:
        from ohs.track.pose_between import _lagrange, pose_between  # noqa: PLC0415

        for f, (nb, ts, t) in tied.items():
            # the polynomial's weight of each neighbour at the frame's time: a small move of the neighbours moves the
            # frame by their weighted sum (to first order; the frame is placed again exactly after each step)
            wt = _lagrange(np.asarray(ts, dtype=np.float64), np.eye(len(nb)), float(t))
            weights[f] = [(col.get(int(g), -1), float(wk)) for g, wk in zip(nb, wt)]

        def place() -> None:
            for f, (nb, ts, t) in tied.items():
                T[f] = pose_between(float(t), ts, [T[int(g)] for g in nb], base=len(nb) // 2 - 1)

    def block(X: np.ndarray, nrm: np.ndarray, f_arr: np.ndarray, sgn: float, row0: int, rows: list, cols: list,
              vals: list) -> None:
        if weights:  # some frames are tied: each row goes to the columns of its frame or of its neighbours
            J = sgn * np.concatenate([np.cross(X - o, nrm) / L, nrm], axis=1)
            f_arr = np.asarray(f_arr)
            for f in np.unique(f_arr):
                rr_ = np.flatnonzero(f_arr == f)
                for c_, wk in weights.get(int(f), [(col.get(int(f), -1), 1.0)]):
                    if c_ < 0:
                        continue
                    for q in range(6):
                        rows.append(row0 + rr_)
                        cols.append(np.full(len(rr_), 6 * c_ + q))
                        vals.append(wk * J[rr_, q])
            return
        c = np.array([col.get(int(f), -1) for f in f_arr])
        ok = c >= 0
        J = sgn * np.concatenate([np.cross(X - o, nrm) / L, nrm], axis=1)
        for q in range(6):
            rows.append(row0 + np.flatnonzero(ok))
            cols.append(6 * c[ok] + q)
            vals.append(J[ok, q])

    for it in range(int(p.iterations)):
        r, m, Xa, Xb = crossing_residuals(T, cr)
        w = _tukey(r, p.sigma_mm) / p.sigma_mm ** 2
        rows: list = []
        cols: list = []
        vals: list = []
        block(Xa, m, cr.fa, 1.0, 0, rows, cols, vals)
        block(Xb, m, cr.fb, -1.0, 0, rows, cols, vals)
        res, wts = [r], [w]
        nrow = len(r)
        if dense is not None:
            pts, tree, Vr, Nr = dense
            for f in free[1:]:
                P = pts.get(f)
                if P is None or len(P) == 0:
                    continue
                X = P @ T[f][:3, :3].T + T[f][:3, 3]
                dd, jj = tree.query(X, distance_upper_bound=3.0)
                ok = np.isfinite(dd)
                if ok.sum() < 20:
                    continue
                X, nn = X[ok], Nr[jj[ok]]
                rd = np.einsum("ij,ij->i", nn, X - Vr[jj[ok]])
                block(X, nn, np.full(len(X), f), 1.0, nrow, rows, cols, vals)
                res.append(rd)
                wts.append(_tukey(rd, 0.1) / p.dense_sigma_mm ** 2)
                nrow += len(X)
        J = sp.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(nrow, n))
        rr, ww = np.concatenate(res), np.concatenate(wts)
        H = (J.T @ sp.diags(ww) @ J).tocsr()
        g = J.T @ (ww * rr)
        # the random-walk prior on the change of each consecutive pair's relative motion
        Hd = sp.lil_matrix((n, n))
        po = np.zeros(n)
        for k in range(len(free) - 1):
            fa_, fb_ = free[k], free[k + 1]
            ca, cb = col[fa_], col[fb_]
            gap = max(1.0, float((gaps or {}).get(fa_, 1.0)))
            Ea = T[fa_] @ invert_se3(T0[fa_])
            Eb = T[fb_] @ invert_se3(T0[fb_])
            e = np.concatenate([so3_log(Eb[:3, :3] @ Ea[:3, :3].T) * L, Eb[:3, 3] - Ea[:3, 3]])
            wk = wo / gap
            for q in range(6):
                if cb >= 0:
                    Hd[6 * cb + q, 6 * cb + q] += wk[q]
                    po[6 * cb + q] += wk[q] * e[q]
                if ca >= 0:
                    Hd[6 * ca + q, 6 * ca + q] += wk[q]
                    po[6 * ca + q] -= wk[q] * e[q]
                if ca >= 0 and cb >= 0:
                    Hd[6 * ca + q, 6 * cb + q] -= wk[q]
                    Hd[6 * cb + q, 6 * ca + q] -= wk[q]
        H = (H + Hd.tocsr() + sp.identity(n) * 1e-9).tocsc()
        x = spla.spsolve(H, -(g + po))
        if not np.all(np.isfinite(x)):
            break
        for f in free[1:]:
            xi = x[6 * col[f]:6 * col[f] + 6] / D
            E = np.eye(4)
            E[:3, :3] = so3_exp(xi[:3])
            E[:3, 3] = xi[3:] + o - E[:3, :3] @ o  # the rotation is about the scan's centre
            T[f] = E @ T[f]
        if tied:
            place()
        info.append({"crossings": int(len(cr)), "used": int((w > 0).sum()),
                     "miss_before_mm": round(float(np.median(np.abs(r))), 4) if len(r) else None,
                     "step_mm": round(float(np.abs(x[3::6]).max()), 4) if n else 0.0})
    return T, info


# --------------------------------------------------------------------------- the held-out test
def holdout_splits(cr: Crossings, frames: Sequence[int], params: CurveParams | None = None) -> list[tuple[np.ndarray, np.ndarray]]:
    """``[(fit, test)]``: the crossings the held-out test fits on and the ones it checks (module docstring, step 4).

    ``holdout="crossings"`` (a setting; the default until 2026-11-21): one split, half the crossings at random and
    the other half. A crossing shares its two frames' curve errors with the other crossings of those frames, so the
    two halves are not independent: fitting one half also fits the frames' own errors, and the other half meets
    better even at the true poses (standing walls, ``VALIDATION.md``, ENGINE_PLAN X0a).
    ``"frames"`` (the default since 2026-11-21, ENGINE_PLAN E-D2): ``holdout_every`` splits; in the ``j``-th, every ``holdout_every``-th frame of ``frames`` from the
    ``j``-th on is held out. The fit takes the crossings between two fitted frames, the test the crossings between two
    held-out frames, whose poses only the motion prior and the depth term move. A drift moves held-out frames with
    their fitted neighbours, so their crossings meet better; a frame's own errors are not in the fit, so fitting
    them helps no held-out frame. Every frame is held out once, and the test must pass in each split."""
    p = params or CurveParams()
    if p.holdout == "crossings":
        half = np.random.default_rng(0).random(len(cr)) < 0.5
        return [(half, ~half)]
    k = max(2, int(p.holdout_every))
    pos = {int(f): n for n, f in enumerate(frames)}
    ra = np.array([pos.get(int(f), 0) % k for f in cr.fa], dtype=np.int64)
    rb = np.array([pos.get(int(f), 0) % k for f in cr.fb], dtype=np.int64)
    return [((ra != j) & (rb != j), (ra == j) & (rb == j)) for j in range(k)]


def acted_warning(moved_mm: float) -> str:
    """The sentence for the user when the adjustment changed the poses (``report["warning"]``; ENGINE_PLAN X0a).

    No held-out test yet tells thin walls from real drift. With the old test (``holdout="crossings"``) it passed on
    thin standing walls even at the true poses, and where it acted the walls read 0.3 to 0.5 mm thicker on one
    simulated scan and up to 0.6 mm thinner on another (a 2 mm wall read 1.95 mm). The default test (whole frames
    held out, since 2026-11-21) refuses those walls, but a walls recording with drift added still passes it and
    acting then makes the camera path worse, by an amount not measured on the walls' thickness: so the sentence
    names no bound. It acts on other scans too (one of the two speckled brackets). Until a test tells those cases
    apart, the scan says that it acted."""
    return (f"Straighten moved the camera path by up to {moved_mm:.1f} mm to make the laser lines meet where they "
            "cross. On thin walls seen from both sides this can make a wall read thinner or thicker than it is, so "
            "check the thickness of thin walls another way before you rely on it.")


# --------------------------------------------------------------------------- frames in turn
@dataclass
class _InTurn:
    """The frames of the adjustment on a recording whose frames come in turn (:func:`laser_curve_adjustment`, "Frames
    in turn")."""

    curve: list  # [(index, t, True, T_wc)] the frames whose laser curves are read, in time order: placed laser frames, both frames
    solve: list  # [(index, t, True, T_wc)] the solve's frames in time order: those and the dot frames beside them
    laser: set  # the indices of the laser frames among them
    gaps: dict  # the solve's dot frame -> processed dot frames to the next one of the solve
    tied: dict  # the laser frames used -> (the dot frames they are placed between, their times, its time)
    lasers_in: list  # [(index, t)] the laser frames placed (for placing them again)
    dots: list  # [(index, t, ok, T_wc)] the dot frames they are placed between (strictly increasing times)
    breaks: set  # positions in ``dots`` with a cut before them
    max_gap_s: float
    part: dict  # report["frames_in_turn"]


def _in_turn_frames(reader: Any, kinds: np.ndarray, rows: list, tracked: list, max_frames: int,
                    max_gap_s: float | None) -> _InTurn:
    """Which frames the adjustment reads and solves on an in-turn recording (:func:`laser_curve_adjustment`, "Frames in
    turn"). ``rows``: the trajectory passed in (all its rows), ``tracked``: its tracked rows."""
    from collections import Counter  # noqa: PLC0415

    from ohs.app.in_turn import DOT_KINDS, GAP_FACTOR  # noqa: PLC0415
    from ohs.app.process import cut_intervals  # noqa: PLC0415
    from ohs.track.pose_between import LEFT_OUT_REASONS, place_laser_frames  # noqa: PLC0415

    index, times = reader.indices, reader.times()
    kind_of = {int(i): str(kinds[k]) for k, i in enumerate(index)}
    given = {r[0] for r in rows}
    dots, last = [], -math.inf
    for r in sorted((r for r in rows if kind_of.get(r[0], "both") in DOT_KINDS), key=lambda r: r[1]):
        if r[1] > last:  # strictly increasing times (two dot frames at the same time: the first), as the frame pass
            dots.append((r[0], r[1], bool(r[2] and np.all(np.isfinite(r[3]))), r[3]))  # tracked as ``tracked`` says
            last = r[1]
    dts = [float(r[1]) for r in dots]
    step = np.diff(dts)
    dt = float(np.median(step)) if len(step) else 0.0
    if max_gap_s is None:
        max_gap_s = GAP_FACTOR * dt if dt > 0 else math.inf
    cuts = cut_intervals(reader)
    breaks = {k for k in range(1, len(dots)) if any(a <= dts[k] and b >= dts[k - 1] for a, b in cuts)}
    # the laser frames of the recording within the dot frames' time span (and one usual interval beyond its ends, where
    # the frame pass also counts a laser frame left out "at the ends")
    lo, hi = (dts[0] - dt, dts[-1] + dt) if dts else (math.inf, -math.inf)
    near = [(int(i), float(times[k])) for k, i in enumerate(index)
            if kind_of[int(i)] == "lasers" and int(i) not in given and lo <= float(times[k]) <= hi]
    outside = sum(1 for k, i in enumerate(index) if kind_of[int(i)] == "lasers" and int(i) not in given) - len(near)
    inside_cut = [c for c in near if any(a <= c[1] <= b for a, b in cuts)]
    cand = [c for c in near if not any(a <= c[1] <= b for a, b in cuts)]
    placed = place_laser_frames(cand, dots, breaks=breaks, max_gap_s=max_gap_s)
    left = Counter(pl.reason for pl in placed if pl.T_wc is None)
    left["cut"] += len(inside_cut)
    ok = [pl for pl in placed if pl.T_wc is not None]
    both = [r for r in tracked if kind_of.get(r[0], "both") in ("both", "lasers")]
    curve = sorted([(pl.index, pl.t, True, pl.T_wc) for pl in ok] + both, key=lambda r: r[1])
    if len(curve) > max_frames:  # spread over the scan, as without kinds
        curve = [curve[int(round(x))] for x in np.linspace(0, len(curve) - 1, max_frames)]
    used = {r[0] for r in curve}
    placed_of = {pl.index: pl for pl in ok}
    laser = {f for f in used if f in placed_of}
    # the solve's unknowns are dot frames: those each laser frame used is placed between, and the scan's first
    # tracked frame, which stays where it is; a laser frame's pose follows theirs (adjust_poses, ``tied``)
    beside: set[int] = {tracked[0][0]}
    for f in laser:
        beside.update(placed_of[f].neighbours)
    tracked_of = {r[0]: r for r in tracked}
    solve = sorted([r for r in curve] + [tracked_of[f] for f in beside if f in tracked_of and f not in used],
                   key=lambda r: r[1])
    # the motion prior between the solve's consecutive dot frames: processed frames between them, as without kinds
    rank = {r[0]: k for k, r in enumerate(tracked)}
    free = [r[0] for r in solve if r[0] not in laser]
    gaps = {free[k]: float(rank[free[k + 1]] - rank[free[k]]) for k in range(len(free) - 1)}
    time_of = {r[0]: float(r[1]) for r in tracked}
    tied = {f: (placed_of[f].neighbours, tuple(time_of[g] for g in placed_of[f].neighbours), float(placed_of[f].t))
            for f in laser}
    methods = Counter(placed_of[f].method for f in laser)
    part = {
        "dot_frames": {"given": len(rows), "tracked": len(tracked), "in_solve": len(solve) - len(laser),
                       "interval_s": round(dt, 6)},
        "both_frames": sum(1 for r in curve if r[0] not in laser),
        "laser_frames": {"candidates": len(near), "placed": len(ok), "used": len(laser),
                         "not_selected": len(ok) - len(laser),
                         "left_out": {r: int(left[r]) for r in LEFT_OUT_REASONS if left[r]},
                         "outside_the_trajectory": int(outside)},
        "left_out_kinds": {kd: int(np.count_nonzero(kinds == kd)) for kd in ("mixed", "dark") if np.any(kinds == kd)},
        "placing": {"rule": "the frame pass's (ohs.track.pose_between), between the dot frames given",
                    "cubic": int(methods["cubic"]), "straight": int(methods["straight"]),
                    "max_gap_s": float(max_gap_s)},
    }
    return _InTurn(curve, solve, laser, gaps, tied, [(pl.index, pl.t) for pl in ok], dots, breaks, float(max_gap_s),
                   part)


def _laser_poses_after(turn: _InTurn, poses: dict[int, np.ndarray], adjusted: dict[int, np.ndarray],
                       placed_in: dict[int, np.ndarray], gt: dict[int, np.ndarray] | None) -> dict:
    """``report["frames_in_turn"]["laser_poses"]`` after the poses changed: the laser frames used placed again between
    the new dot poses, against the poses the solve gave them; with true poses, all three against the truth."""
    from ohs.app.in_turn import LaserFrames  # noqa: PLC0415
    from ohs.geometry import pose_error  # noqa: PLC0415
    from ohs.track.pose_between import place_laser_frames  # noqa: PLC0415

    dots = [(i, t, ok, poses.get(i, T)) for i, t, ok, T in turn.dots]
    again = {pl.index: pl.T_wc for pl in place_laser_frames(turn.lasers_in, dots, breaks=turn.breaks,
                                                            max_gap_s=turn.max_gap_s) if pl.T_wc is not None}
    used = sorted(f for f in turn.laser if f in again and f in adjusted)
    err = [pose_error(adjusted[f], again[f]) for f in used]
    out: dict[str, Any] = {
        "afterwards": "placed again between the new dot poses (as the frame pass places them)",
        "solve_vs_placed_again": {
            "n": len(err),
            "mm_median": round(float(np.median([e[1] for e in err])), 4) if err else None,
            "mm_max": round(float(max(e[1] for e in err)), 4) if err else None,
            "deg_median": round(float(np.median([e[0] for e in err])), 5) if err else None,
            "deg_max": round(float(max(e[0] for e in err)), 5) if err else None}}
    if gt:
        def truth(traj: list, P: dict[int, np.ndarray]) -> dict:
            g = LaserFrames._truth(gt, traj, [(f, 0.0, True, P[f]) for f in used if f in P])
            return {k: ({s: v for s, v in g[k].items() if s in ("median", "p90", "max")} if isinstance(g[k], dict)
                        else g[k]) for k in ("n", "trans_mm", "rot_deg") if k in g}
        out["ground_truth"] = {"before": truth(turn.dots, placed_in), "placed_again": truth(dots, again),
                               "solve": truth(dots, adjusted)}
    return out


# --------------------------------------------------------------------------- the whole adjustment
def laser_curve_adjustment(rec_path: str | Path, calib: Any, trajectory: Sequence[Sequence[Any]], *,
                           reference: tuple[np.ndarray, np.ndarray] | None = None,
                           params: CurveParams | None = None, progress: Progress | None = None,
                           cancelled: Callable[[], bool] | None = None,
                           laser_max_gap_s: float | None = None) -> tuple[dict[int, np.ndarray], dict]:
    """Adjust the tracked poses so the laser curves meet at their crossings (module docstring).

    ``trajectory``: ``[(frame index, t_host, ok, T_wc)]`` (``trajectory.csv``); ``reference``: the
    surface ``(V, F)`` these poses fused (the base mesh), for the dense term. Returns ``(poses,
    report)``: ``T_wc`` of every tracked frame (the input when nothing changes) and ``frames``,
    ``crossings``, ``miss_mm`` (median before / after), ``holdout`` (median miss of the held-out
    crossings before / after, and their ratio; with whole frames held out, the default, also ``by``,
    ``every``, ``fit``, ``test`` and each split's ``ratios``, the worst of which is ``ratio``), ``moved_mm`` / ``moved_deg`` (largest change of a
    camera), ``changed``, ``reason`` (``adjusted``, ``no_lasers``, ``few_crossings``,
    ``not_better``, ``off``), ``verdict``, ``warning`` (None, or when the poses changed a sentence for
    the user: :func:`acted_warning`), ``timings_s`` and, with true poses in the recording,
    ``ground_truth`` (before / after).

    **Frames in turn** (a recording with kinds of frame other than ``both``, as :mod:`ohs.app.process` decides): the
    trajectory holds the dot frames, which have no laser line, so none is looked for in them. The curves come from the
    recording's ``lasers`` frames (and its ``both`` frames, at their own poses), each placed between the given dot
    frames beside it in time by the frame pass's rule (:func:`ohs.track.pose_between.place_laser_frames`; left out at
    the scan's ends, beside an untracked frame, across a cut or a gap longer than ``laser_max_gap_s``, by default
    ``GAP_FACTOR`` (2.5) times the median interval of the trajectory's dot frames). ``max_frames`` counts these frames.
    The solve's unknowns are the dot frames the laser frames used lie between (and the first tracked frame, fixed): a
    laser frame has no pose of its own, it stays placed between them by the same rule (:func:`adjust_poses`,
    ``tied``), so its crossings move the dot frames beside it; the dot frames carry the depth term (with
    ``reference``; a laser frame has no depth) and the motion prior, as without kinds. The held-out test holds out
    every ``holdout_every``-th laser frame. Every tracked dot frame gets its pose from the solve, or by interpolation
    in time between the solve's frames as without kinds. ``report["frames_in_turn"]`` says what was used: ``dot_frames``,
    ``both_frames``, ``laser_frames`` (``candidates``, ``placed``, ``used``, ``not_selected`` (placed, beyond
    ``max_frames``), ``left_out`` by reason, ``outside_the_trajectory``, and ``with_curves``), ``left_out_kinds``,
    ``placing``, and when the poses changed ``laser_poses``: the laser frames' poses are to be placed again between
    the new dot poses (as :func:`ohs.app.process.process_recording` does with given poses), with how far that is from
    the poses the solve gave them and, with true poses, all of them against the truth."""
    from scipy.spatial import cKDTree  # noqa: PLC0415

    from ohs.app.process import trajectory_errors  # noqa: PLC0415
    from ohs.io.recording import RecordingReader  # noqa: PLC0415
    from ohs.mesh.topology import vertex_normals  # noqa: PLC0415
    from ohs.track.posegraph import KeyframeClouds, StraightenParams, interpolate_poses  # noqa: PLC0415

    p = params or CurveParams()
    say = progress or (lambda f, m: None)
    wall = time.perf_counter()
    reader = RecordingReader(rec_path)
    if calib is None:
        calib = reader.calibration()
    rows = sorted(((int(i), float(t), bool(ok), np.asarray(T, dtype=np.float64)) for i, t, ok, T in trajectory),
                  key=lambda r: r[0])
    tracked = [r for r in rows if r[2] and np.all(np.isfinite(r[3]))]
    poses_in = {i: T.copy() for i, _, _, T in tracked}
    timings: dict[str, float] = {}
    report: dict[str, Any] = {"frames": 0, "crossings": 0, "miss_mm": None, "holdout": None, "moved_mm": 0.0,
                              "moved_deg": 0.0, "changed": False, "reason": "", "verdict": "", "warning": None,
                              "timings_s": timings, "params": asdict(p)}

    def finish(poses: dict[int, np.ndarray], reason: str, verdict: str) -> tuple[dict[int, np.ndarray], dict]:
        report["reason"], report["verdict"] = reason, verdict
        report["changed"] = any(not np.array_equal(poses[i], poses_in[i]) for i in poses_in)
        gt = reader.ground_truth_poses()
        if gt:
            def summ(P: dict[int, np.ndarray]) -> dict:
                e = trajectory_errors(gt, [(i, T, True) for i, T in P.items()])
                return {k: v for k, v in e.items() if k != "per_frame"}
            report["ground_truth"] = {"before": summ(poses_in), "after": summ(poses)}
        timings["total"] = round(time.perf_counter() - wall, 2)
        log.info("laser-curve adjustment %s: %s", rec_path, verdict)
        return poses, report

    if not p.enabled:
        return finish(dict(poses_in), "off", "Lining the scan up on the laser crossings is switched off, so the poses "
                                             "are unchanged.")
    if p.holdout not in ("crossings", "frames"):
        raise ValueError(f"CurveParams.holdout must be 'crossings' or 'frames', not {p.holdout!r}")
    if not calib.lasers or len(tracked) < 3:
        return finish(dict(poses_in), "no_lasers", "No laser lines to line the scan up with, so the poses are unchanged.")
    kinds = reader.kinds()
    turn: _InTurn | None = None
    if reader.has_kinds and bool(np.any(kinds != "both")):
        # frames in turn (docstring): the laser frames placed between the given dot frames
        turn = _in_turn_frames(reader, kinds, rows, tracked, int(p.max_frames), laser_max_gap_s)
        report["frames_in_turn"] = turn.part
        if not turn.curve:
            return finish(dict(poses_in), "no_lasers",
                          "No laser frame of this recording could be placed between its dot frames, so the poses are "
                          "unchanged.")
        sel, solve = turn.curve, turn.solve
        frames = [r[0] for r in sel]
        solve_frames = [r[0] for r in solve]
        start = {r[0]: r[3] for r in solve}
        gaps, tied = turn.gaps, turn.tied
    else:
        sel = [r for r in tracked]
        if len(sel) > p.max_frames:
            sel = [sel[int(round(x))] for x in np.linspace(0, len(sel) - 1, p.max_frames)]
        frames = [r[0] for r in sel]
        order = [r[0] for r in tracked]
        rank = {f: k for k, f in enumerate(order)}
        gaps = {frames[k]: float(rank[frames[k + 1]] - rank[frames[k]]) for k in range(len(frames) - 1)}
        solve, solve_frames, start, tied = sel, frames, poses_in, None
    report["frames"] = len(frames)
    t0 = time.perf_counter()
    curves = frame_curves(reader, calib, frames, p, cancelled=cancelled, progress=lambda f, m: say(0.6 * f, m))
    timings["curves"] = round(time.perf_counter() - t0, 2)
    if turn is not None:
        turn.part["laser_frames"]["with_curves"] = sum(1 for f in turn.laser if curves.get(f))
    T_sel = {f: start[f] for f in solve_frames}
    cr = find_crossings(curves, T_sel, p)
    report["crossings"] = len(cr)
    if len(cr) < p.min_crossings:
        return finish(dict(poses_in), "few_crossings",
                      f"The laser lines cross too rarely in this scan ({len(cr)} crossings) to line it up, so the "
                      "poses are unchanged. Sweeping in two directions makes more crossings.")
    dense = None
    if reference is not None and p.dense_sigma_mm > 0:
        t0 = time.perf_counter()
        say(0.6, "reading each frame's depth")
        Vr = np.asarray(reference[0], dtype=np.float64)
        Fr = np.asarray(reference[1], dtype=np.int64)
        Nr = vertex_normals(Vr, Fr)
        kc = KeyframeClouds(reader, calib, StraightenParams())
        pos_of = {int(i): k for k, i in enumerate(reader.indices)}
        pts: dict[int, np.ndarray] = {}
        for f in solve_frames:
            if cancelled is not None and cancelled():
                raise InterruptedError("cancelled")
            if turn is not None and f in turn.laser:
                continue  # a laser frame has no depth of its own
            P = kc.fine_points(pos_of[f]) if f in pos_of else None  # the points only: no trees, nothing kept
            if P is not None:
                pts[f] = P[:: max(1, len(P) // max(int(p.dense_points), 1))]
        dense = (pts, cKDTree(Vr), Vr, Nr)
        del kc
        timings["dense"] = round(time.perf_counter() - t0, 2)
    t0 = time.perf_counter()
    say(0.8, f"lining up {len(cr):,} laser crossings")
    miss0 = float(np.median(np.abs(crossing_residuals(T_sel, cr)[0])))
    # never worse: fitted on part of the crossings, the held-out ones must meet clearly better (in every split); with
    # frames in turn the frames held out are laser frames (and both frames): the frames whose curves were read
    splits = holdout_splits(cr, frames, p)
    n_test = min(int(t.sum()) for _, t in splits)
    if p.holdout == "frames" and n_test < p.holdout_min_test:
        timings["solve"] = round(time.perf_counter() - t0, 2)
        report["holdout"] = {"by": "frames", "every": int(p.holdout_every), "test": n_test}
        report["miss_mm"] = {"before": round(miss0, 4), "after": round(miss0, 4)}
        return finish(dict(poses_in), "few_crossings",
                      f"Too few laser crossings join the frames held out for the check ({n_test}) to line the scan up "
                      "safely, so the poses are unchanged.")
    tests = []
    for fit, test in splits:
        T_half, _ = adjust_poses(T_sel, cr.subset(fit), solve_frames, p, dense, gaps, tied)
        hb = float(np.median(np.abs(crossing_residuals(T_sel, cr.subset(test))[0])))
        ha = float(np.median(np.abs(crossing_residuals(T_half, cr.subset(test))[0])))
        tests.append((ha / max(hb, 1e-9), hb, ha, int(fit.sum()), int(test.sum())))
    ratio, hb, ha, n_fit, n_test = max(tests)  # the split that meets least better
    report["holdout"] = {"before_mm": round(hb, 4), "after_mm": round(ha, 4), "ratio": round(ratio, 3)}
    if p.holdout == "frames":
        report["holdout"].update(by="frames", every=int(p.holdout_every), fit=n_fit, test=n_test,
                                 ratios=[round(r[0], 3) for r in tests])
    if ratio > p.holdout_max_ratio:
        timings["solve"] = round(time.perf_counter() - t0, 2)
        report["miss_mm"] = {"before": round(miss0, 4), "after": round(miss0, 4)}
        return finish(dict(poses_in), "not_better",
                      f"The {len(cr):,} laser crossings already meet within the lasers' own scatter "
                      f"({miss0:.2f} mm), so the poses are unchanged.")
    T_new, info = T_sel, []
    for rnd in range(max(1, int(p.rounds))):
        if rnd:
            cr = find_crossings(curves, T_new, p)
        T_new, inf = adjust_poses(T_new, cr, solve_frames, p, dense, gaps, tied)
        info += inf
    miss1 = float(np.median(np.abs(crossing_residuals(T_new, cr)[0])))
    report["miss_mm"] = {"before": round(miss0, 4), "after": round(miss1, 4)}
    report["iterations"] = info
    timings["solve"] = round(time.perf_counter() - t0, 2)
    # every tracked frame, by interpolation between the frames of the solve
    times = np.array([t for _, t, _, _ in tracked])
    T_all = np.stack([T for _, _, _, T in tracked])
    kf_times = np.array([r[1] for r in solve])
    T_out = interpolate_poses(times, T_all, kf_times, np.stack([start[f] for f in solve_frames]),
                              np.stack([T_new[f] for f in solve_frames]))
    poses = {i: T_out[k] for k, (i, _, _, _) in enumerate(tracked)}
    moved = [float(np.linalg.norm(poses[i][:3, 3] - poses_in[i][:3, 3])) for i in poses]
    turned = [float(np.degrees(np.linalg.norm(so3_log(poses[i][:3, :3] @ poses_in[i][:3, :3].T)))) for i in poses]
    report["moved_mm"] = round(max(moved), 3)
    report["moved_deg"] = round(max(turned), 3)
    report["warning"] = acted_warning(max(moved))
    if turn is not None:
        turn.part["laser_poses"] = _laser_poses_after(turn, poses, {f: T_new[f] for f in turn.laser},
                                                      {f: start[f] for f in turn.laser}, reader.ground_truth_poses())
    return finish(poses, "adjusted",
                  f"Lined the scan up on {len(cr):,} laser crossings: their median miss fell from {miss0:.2f} to "
                  f"{miss1:.2f} mm, and the camera moved by up to {max(moved):.2f} mm.")
