"""Tags as anchors in Straighten (ENGINE_PLAN X8 step 2).

On a plain part (a long body panel) frame-to-model tracking slides along the part where the surface does not hold
it, and the passes of a scan then sit shifted along it; loops measure only the distance to the surface and its tilt,
so they cannot mend that (``VALIDATION.md``, "The overall size of a large part"). AprilTags in view (on the table
beside the part, or on the part) can: each one is a fixed point that many frames see.

**Detection** (:func:`detect_tag_views`). Straighten's keyframes are looked at, rectified at full resolution, and the
tags found in both images (:meth:`ohs.track.markers.MarkerTracker.detect`, tag36h11, the dots filtered out) are
triangulated in that frame alone: four corners in the camera's frame. The printed size is not needed: the stereo
pair measures it; a detection is kept when its four edges agree with each other and its diagonals with them (within
``tag_shape_tol``), not with a size typed in. A quick look first (the left images of every ``tag_look_every``-th
keyframe): a scan where it finds no tag is left exactly as it is, and costs only that look.

**The fit** (:func:`anchor_keyframes`). Unknowns: every keyframe's pose but the first (it defines the world) and
every tag's pose (its shape is a square of the side the stereo pair measured, the mean over its views). Terms:

* each detection, corner by corner, in the frame that saw it: the tag's corners put into that camera by the two
  poses, against the triangulated ones, weighted by the stereo's own scatter for a corner at that depth
  (``tag_sigma_px`` of the corner's image position times ``Z / f`` across the image, ``tag_sigma_view_px`` times
  ``sqrt(2) Z^2 / (f B)`` along the view: both measured, ``VALIDATION.md`` X8 step 2);
* the tracked motion between consecutive keyframes, with Straighten's drift model in translation and
  ``tag_drift_deg_per_frame`` in the turn (the gyro holds the turn far better than the loops' model says; looser, a frame
  that sees one tag swings about it);
* the loops Straighten verified (their point-to-plane information: they hold the passes' distance along the
  surface's normal, which tags on a table, seen with the stereo's depth scatter, hold worst; without them the passes
  came apart by 0.2-0.4 mm along the normal).

Solved by Gauss-Newton on all of them together (a sparse system of 6 unknowns a keyframe and a tag; 0.1 s for 140
keyframes and 20 tags), each detection's weight cut down beyond ``3 sigma`` (Huber), so no single view pulls hard.
Never a tag placed by averaging its views in the drifted world: the poses and the tags move together.

**Tags that do not fit** are left out, and the report says which and why: a tag seen in fewer than ``tag_min_views``
keyframes (it cannot be checked); a view farther than ``tag_pre_gate_mm`` from where the tracked poses put the tag's
other views (a misread id); after the fit, a tag whose views disagree (more than ``tag_max_bad_share`` of them
farther than ``tag_gate_mm``: moved during the scan by more than the gate, or misread more than once), and a single
view that does not fit (``tag_gate_mm``). Then a tag moved by less than the gate, or one that alone joins two stretches
of the scan: of the tags seen in two or more separate stretches (a tag seen in one stretch agrees with itself and holds
that stretch), the one whose leaving makes the other tags fit best is the suspect; it is left out when, the scan held by
the others, it changes the scan's shape by more than ``tag_influence_mm`` and its own views disagree by more than twice
the tags' typical scatter. Each is decided on a fit without it, one at a time. Measured on synthetic views with the
measured scatter: a tag moved 2-5 mm is left out (one 2 mm move of 6 was not; the scan's shape then 1.1 mm off); a tag
moved 1 mm, about 2.5 times a view's scatter, is not recognised and may bend the scan's shape by up to 2 mm (0.075 % in
length), still less than the slide it mends (4 mm, 0.17 %).

**Never worse, on views held out** (ENGINE_PLAN E-D2, as the laser crossings): the keyframes that see a tag are
split, every ``tag_holdout_every``-th held out, each split in turn. The fit sees none of the held-out keyframes'
detections; their poses follow only the tracked motion. The test: the held-out detections against the fitted
tags, at the fitted poses (after) and with the poses as tracked and the tags placed by the fitted keyframes' views
(before), the median over detections of their corners' RMS distance. A slide between passes puts the same tag in two
places, which no "before" can hide. The poses change only when, in every split, after is at most
``tag_holdout_max_ratio`` of before, and before is above ``tag_min_residual_mm`` (else the scan already agrees with
the tags). Then the fit on all the views is used, and no tracked step between two keyframes may change by more than
Straighten's drift model allows (``max_odometry_chi2``).
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import numpy as np

from ohs.geometry import invert_se3, kabsch, make_se3, so3_exp, so3_log

__all__ = ["TagView", "TagFit", "detect_tag_views", "anchor_keyframes", "tag_square"]

log = logging.getLogger(__name__)


@dataclass
class TagView:
    """One keyframe's detection of one tag: ``k`` the keyframe (its position among the keyframes), ``tag`` the id,
    ``X`` the four triangulated corners (4,3) in that camera's frame, mm (OpenCV corner order)."""

    k: int
    tag: int
    X: np.ndarray


@dataclass
class TagFit:
    """What :func:`anchor_keyframes` found."""

    T: np.ndarray | None  # the keyframes' new poses (K,4,4), None when unchanged
    reason: str  # "anchored", "no_tags", "too_few", "agree", "not_better", "bends_too_much"
    used: list[int] = field(default_factory=list)
    left_out: dict[int, str] = field(default_factory=dict)  # tag id -> why (plain words)
    dropped_views: int = 0  # single views that did not fit, left out
    holdout: dict | None = None
    residual_mm: dict | None = None
    side_mm: dict[int, float] = field(default_factory=dict)  # each tag's side as the stereo pair measured it
    influence_mm: dict[int, float] = field(default_factory=dict)  # how far the scan moves without each tag (last check)


# --------------------------------------------------------------------------- detection
def tag_square(side_mm: float) -> np.ndarray:
    """A tag's corners (4,3) in its own frame for a measured side (OpenCV order, as ``tag_corners_local``)."""
    s = 0.5 * float(side_mm)
    return np.array([[-s, s, 0.0], [s, s, 0.0], [s, -s, 0.0], [-s, -s, 0.0]])


def triangulate_size_free(K: np.ndarray, baseline: float, left: dict[int, np.ndarray], right: dict[int, np.ndarray],
                          shape_tol: float, max_dv_px: float = 2.0, depth_mm: tuple[float, float] = (50.0, 1500.0)
                          ) -> dict[int, np.ndarray]:
    """Camera-frame corners (4,3) of the tags found in both rectified images: rows agree within ``max_dv_px``, the
    disparity is positive, the depth plausible, and the four edges and the two diagonals (over sqrt 2) agree with
    their own mean within ``shape_tol`` (a square of any size: the printed size is not needed)."""
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    out: dict[int, np.ndarray] = {}
    for i in sorted(set(left) & set(right)):
        cl, cr = np.asarray(left[i], float), np.asarray(right[i], float)
        if np.any(np.abs(cl[:, 1] - cr[:, 1]) > max_dv_px):
            continue
        disp = cl[:, 0] - cr[:, 0]
        if np.any(disp <= 0):
            continue
        Z = fx * baseline / disp
        if np.any(Z < depth_mm[0]) or np.any(Z > depth_mm[1]):
            continue
        v = 0.5 * (cl[:, 1] + cr[:, 1])
        X = np.stack([(cl[:, 0] - cx) / fx * Z, (v - cy) / fy * Z, Z], axis=1)
        sides = np.concatenate([np.linalg.norm(X - np.roll(X, -1, axis=0), axis=1),
                                np.linalg.norm(X[:2] - X[2:], axis=1) / math.sqrt(2.0)])
        if np.any(np.abs(sides / sides.mean() - 1.0) > shape_tol):
            continue
        out[int(i)] = X
    return out


def detect_tag_views(reader: Any, calib: Any, frames: Sequence[int], params: Any,
                     cancelled: Callable[[], bool] | None = None) -> tuple[dict[int, dict[int, np.ndarray]], dict]:
    """The tags each of ``frames`` (frame indices) sees: ``({frame: {tag: corners (4,3)}}, info)``. A quick look at
    the left images of every ``params.tag_look_every``-th frame first; without a tag there, nothing more is read."""
    from ohs.track.markers import MarkerParams, MarkerTracker  # noqa: PLC0415
    from ohs.vision.rectify import Rectifier  # noqa: PLC0415

    t0 = time.perf_counter()
    rect = Rectifier(calib.stereo, 1.0)
    mt = MarkerTracker(rect.K, rect.baseline, MarkerParams())
    pos_of = {int(i): k for k, i in enumerate(reader.indices)}
    frames = [int(f) for f in frames if int(f) in pos_of]
    info: dict[str, Any] = {"looked_at": 0, "read": 0}
    look = frames[::max(1, int(params.tag_look_every))]
    found = False
    it = reader.iter_ahead([pos_of[f] for f in look], 4)
    try:
        for fr in it:
            if cancelled is not None and cancelled():
                raise InterruptedError("straightening cancelled")
            info["looked_at"] += 1
            if mt.detect(rect.rectify_left(fr.left)):
                found = True
                break
    finally:
        it.close()
    info["look_s"] = round(time.perf_counter() - t0, 2)
    if not found:
        return {}, info
    out: dict[int, dict[int, np.ndarray]] = {}
    it = reader.iter_ahead([pos_of[f] for f in frames], 4)
    try:
        for fr in it:
            if cancelled is not None and cancelled():
                raise InterruptedError("straightening cancelled")
            info["read"] += 1
            left, right = rect.rectify(fr.left, fr.right)
            views = triangulate_size_free(rect.K, rect.baseline, mt.detect(left), mt.detect(right),
                                          float(params.tag_shape_tol))
            if views:
                out[int(fr.index)] = views
    finally:
        it.close()
    info["detect_s"] = round(time.perf_counter() - t0, 2)
    info["K"] = [float(rect.K[0, 0]), float(rect.baseline)]
    return out, info


# --------------------------------------------------------------------------- the fit
def _skew(v: np.ndarray) -> np.ndarray:
    return np.array([[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]])


def _exp(xi: np.ndarray) -> np.ndarray:
    return make_se3(so3_exp(xi[:3]), xi[3:])


def _vec6(E: np.ndarray) -> np.ndarray:
    return np.concatenate([so3_log(E[:3, :3]), E[:3, 3]])


def _adjoint(T: np.ndarray) -> np.ndarray:
    R, t = T[:3, :3], T[:3, 3]
    A = np.zeros((6, 6))
    A[:3, :3] = R
    A[3:, 3:] = R
    A[3:, :3] = _skew(t) @ R
    return A


def _corner_weights(X: np.ndarray, f: float, B: float, sigma_px: float, sigma_view_px: float | None = None) -> np.ndarray:
    """(4,3) inverse variances of triangulated corners ``X`` (camera frame): across the image ``(Z / f) sigma``,
    along the view ``sqrt(2) Z^2 / (f B) sigma``."""
    Z = np.maximum(X[:, 2], 1.0)
    s_xy = Z / f * sigma_px
    s_z = math.sqrt(2.0) * Z * Z / (f * B) * (sigma_px if sigma_view_px is None else sigma_view_px)
    return np.stack([1.0 / s_xy ** 2, 1.0 / s_xy ** 2, 1.0 / s_z ** 2], axis=1)


@dataclass
class _Problem:
    odometry: list  # (k, X (4,4), info (6,6)) between keyframes k and k + 1
    f: float
    B: float
    sigma_px: float
    sigma_view_px: float | None = None
    loops: list = field(default_factory=list)  # (a, b, X = inv(T[b]) @ T[a] as verified, info (6,6)): Straighten's loops


def _edges(prob: _Problem) -> list:
    """The pose-to-pose terms: the tracked motion between consecutive keyframes, and the loops Straighten verified
    (they hold the passes' distance along the surface's normal, which a tag on the table, seen with the stereo's depth
    scatter, holds worst; they say nothing along the surface)."""
    return [(k, k + 1, X, info) for k, X, info in prob.odometry] + list(prob.loops)


def _residuals(T: np.ndarray, W: dict[int, np.ndarray], S: dict[int, np.ndarray], views: list[TagView]) -> np.ndarray:
    """Each view's RMS corner distance (mm) between where the poses put the tag and where the frame saw it."""
    out = np.empty(len(views))
    for n, v in enumerate(views):
        P = S[v.tag] @ W[v.tag][:3, :3].T + W[v.tag][:3, 3]
        q = (P - T[v.k][:3, 3]) @ T[v.k][:3, :3]
        out[n] = float(np.sqrt(np.mean(np.sum((q - v.X) ** 2, axis=1))))
    return out


def _place_tags(T: np.ndarray, S: dict[int, np.ndarray], views: list[TagView]) -> dict[int, np.ndarray]:
    """Each tag where the views' frames put it, frames fixed (the start of the fit, and "before" of the test)."""
    acc: dict[int, list[np.ndarray]] = {}
    for v in views:
        acc.setdefault(v.tag, []).append(v.X @ T[v.k][:3, :3].T + T[v.k][:3, 3])
    W = {}
    for m, Xs in acc.items():
        R, t = kabsch(S[m], np.mean(np.stack(Xs), axis=0))
        W[m] = make_se3(R, t)
    return W


def _solve_blocks(Hb: np.ndarray, g: np.ndarray) -> np.ndarray:
    """``H x = g`` for a matrix given as (nb, nb, 6, 6) blocks, most of them zero (keyframes touch their neighbours
    and the tags they saw): a sparse solve."""
    from scipy.sparse import coo_matrix  # noqa: PLC0415
    from scipy.sparse.linalg import spsolve  # noqa: PLC0415

    nb = Hb.shape[0]
    a, b = np.nonzero(np.any(Hb != 0.0, axis=(2, 3)))
    i6 = np.arange(6)
    rows = (6 * a[:, None, None] + i6[None, :, None]) + 0 * i6[None, None, :]
    cols = (6 * b[:, None, None] + i6[None, None, :]) + 0 * i6[None, :, None]
    H = coo_matrix((Hb[a, b].ravel(), (rows.ravel(), cols.ravel())), shape=(6 * nb, 6 * nb)).tocsc()
    H = H + coo_matrix((1e-9 * (1.0 + np.abs(H.diagonal())), (np.arange(6 * nb), np.arange(6 * nb))), shape=H.shape).tocsc()
    return np.asarray(spsolve(H, g), dtype=np.float64)


def _solve(T0: np.ndarray, S: dict[int, np.ndarray], views: list[TagView], prob: _Problem, iterations: int = 12
           ) -> tuple[np.ndarray, dict[int, np.ndarray]]:
    """Gauss-Newton on the keyframes (the first fixed) and the tags (module docstring, "The fit")."""
    K = len(T0)
    T = np.asarray(T0, float).copy()
    tags = sorted({v.tag for v in views})
    W = _place_tags(T, S, views)
    tag_block = {m: (K - 1) + j for j, m in enumerate(tags)}
    nb = (K - 1) + len(tags)  # blocks of 6: keyframes 1..K-1, then the tags
    n = 6 * nb
    vk = np.array([v.k for v in views], dtype=np.int64)
    vt = np.array([tag_block[v.tag] for v in views], dtype=np.int64)
    Xv = np.stack([v.X for v in views]) if views else np.zeros((0, 4, 3))  # (V,4,3)
    Sv = np.stack([S[v.tag] for v in views]) if views else np.zeros((0, 4, 3))
    w = np.stack([_corner_weights(x, prob.f, prob.B, prob.sigma_px, prob.sigma_view_px) for x in Xv]) if views else np.zeros((0, 4, 3))
    for _ in range(iterations):
        Hb = np.zeros((nb, nb, 6, 6))
        gb = np.zeros((nb, 6))
        for a, b, X, info in _edges(prob):  # the tracked motion between keyframes, and the loops' measurements
            A = invert_se3(X) @ invert_se3(T[b])
            e = _vec6(A @ T[a])
            J = _adjoint(A)
            blocks = ([(a - 1, J)] if a >= 1 else []) + ([(b - 1, -J)] if b >= 1 else [])
            for a, Ja in blocks:
                gb[a] += Ja.T @ info @ e
                for b, Jb in blocks:
                    Hb[a, b] += Ja.T @ info @ Jb
        if len(views):  # each detection, corner by corner, in its own camera
            Wt = np.stack([W[v.tag] for v in views])  # (V,4,4)
            Tk = T[vk]
            P = np.einsum("vij,vcj->vci", Wt[:, :3, :3], Sv) + Wt[:, None, :3, 3]  # (V,4,3) world
            Rt = np.transpose(Tk[:, :3, :3], (0, 2, 1))
            q = np.einsum("vij,vcj->vci", Rt, P - Tk[:, None, :3, 3])
            r = q - Xv
            chi = np.sqrt(np.mean(np.sum(r * r * w, axis=2), axis=1))
            hub = np.where(chi <= 3.0, 1.0, 3.0 / np.maximum(chi, 1e-12))  # Huber: no view pulls harder than 3 sigma
            ww = w * hub[:, None, None]  # (V,4,3)
            sk = np.zeros(P.shape + (3,))
            sk[..., 0, 1], sk[..., 0, 2], sk[..., 1, 2] = -P[..., 2], P[..., 1], -P[..., 0]
            sk[..., 1, 0], sk[..., 2, 0], sk[..., 2, 1] = P[..., 2], -P[..., 1], P[..., 0]
            RtS = np.einsum("vij,vcjk->vcik", Rt, sk)  # (V,4,3,3)
            Rt4 = np.broadcast_to(Rt[:, None], RtS.shape)
            Jf = np.concatenate([RtS, -Rt4], axis=3)  # (V,4,3,6) left perturbation of the keyframe
            Jt = np.concatenate([-RtS, Rt4], axis=3)  # left perturbation of the tag
            WJf, WJt = Jf * ww[..., None], Jt * ww[..., None]
            Hff = np.einsum("vcia,vcib->vab", Jf, WJf)
            Htt = np.einsum("vcia,vcib->vab", Jt, WJt)
            Hft = np.einsum("vcia,vcib->vab", Jf, WJt)
            gf = np.einsum("vcia,vci->va", WJf, r)
            gt_ = np.einsum("vcia,vci->va", WJt, r)
            np.add.at(Hb, (vt, vt), Htt)
            np.add.at(gb, vt, gt_)
            f = vk >= 1
            fb = vk[f] - 1
            np.add.at(Hb, (fb, fb), Hff[f])
            np.add.at(Hb, (fb, vt[f]), Hft[f])
            np.add.at(Hb, (vt[f], fb), np.transpose(Hft[f], (0, 2, 1)))
            np.add.at(gb, fb, gf[f])
        dx = -_solve_blocks(Hb, gb.reshape(n))
        for k in range(1, K):
            T[k] = _exp(dx[6 * (k - 1):6 * k]) @ T[k]
        for m in tags:
            b = 6 * tag_block[m]
            W[m] = _exp(dx[b:b + 6]) @ W[m]
        if float(np.max(np.abs(dx))) < 1e-7:
            break
    return T, W


AHEAD_MM = 265.0  # where a keyframe measures the surface: its error there is what the mesh shows


def _ahead(T: np.ndarray) -> np.ndarray:
    return T[:, :3, 3] + AHEAD_MM * T[:, :3, 2]


def _shape_change(A: np.ndarray, B: np.ndarray) -> float:
    """The largest distance between two sets of points after a rigid best fit of one on the other: how much the
    scan's shape differs (a turn or a shift of the whole scan changes no measurement)."""
    R, t = kabsch(A, B)
    return float(np.max(np.linalg.norm(A @ R.T + t - B, axis=1)))


STRETCH_GAP = 10  # keyframes: a tag not seen for longer than this is seen again in another stretch of the scan


def _stretches(views: list[TagView]) -> int:
    """In how many separate stretches of the scan (runs of keyframes, gaps over :data:`STRETCH_GAP`) a tag was seen:
    only a tag seen in two or more can be told to have moved, and only such a tag joins stretches together."""
    ks = sorted({v.k for v in views})
    return 1 + int(sum(1 for a, b in zip(ks, ks[1:]) if b - a > STRETCH_GAP)) if ks else 0


def _odometry_chi2(T: np.ndarray, odometry: list) -> np.ndarray:
    return np.array([float(_vec6(invert_se3(X) @ invert_se3(T[k + 1]) @ T[k]) @ info
                           @ _vec6(invert_se3(X) @ invert_se3(T[k + 1]) @ T[k])) for k, X, info in odometry])


def anchor_keyframes(T_kf: np.ndarray, views: list[TagView], odometry: list, params: Any, f: float, B: float,
                     loops: list | None = None) -> TagFit:
    """Hold keyframes ``T_kf`` (K,4,4) to the tags they saw (module docstring). ``odometry``: ``(k, X, info)`` per
    pair of consecutive keyframes, ``X = inv(T[k+1]) @ T[k]`` as tracked; ``loops``: ``(a, b, X, info)``, Straighten's
    verified loops between keyframes ``a`` and ``b`` (``X = inv(T[b]) @ T[a]`` as the clouds matched)."""
    p = params
    T_kf = np.asarray(T_kf, float)
    prob = _Problem(list(odometry), float(f), float(B), float(p.tag_sigma_px), float(p.tag_sigma_view_px),
                    loops=list(loops or []))
    fit = TagFit(None, "no_tags")
    if not views:
        return fit
    # each tag's side, as the stereo pair measured it (the mean over its views): no printed size needed
    sides: dict[int, list[float]] = {}
    for v in views:
        sides.setdefault(v.tag, []).append(float(np.mean(np.linalg.norm(v.X - np.roll(v.X, -1, axis=0), axis=1))))
    fit.side_mm = {m: float(np.mean(s)) for m, s in sides.items()}
    S = {m: tag_square(s) for m, s in fit.side_mm.items()}
    keep = list(views)

    def seen_in(vs: list[TagView]) -> dict[int, int]:
        c: dict[int, set[int]] = {}
        for v in vs:
            c.setdefault(v.tag, set()).add(v.k)
        return {m: len(ks) for m, ks in c.items()}

    def too_few(vs: list[TagView]) -> list[TagView]:
        for m, nk in seen_in(vs).items():
            if nk < int(p.tag_min_views) and m not in fit.left_out:
                fit.left_out[m] = f"seen in only {nk} of the frames looked at"
        return [v for v in vs if v.tag not in fit.left_out]

    keep = too_few(keep)
    # a misread id: a view far from where the tracked poses put the tag's other views
    if keep:
        world = {}
        for v in keep:
            world.setdefault(v.tag, []).append(v.X @ T_kf[v.k][:3, :3].T + T_kf[v.k][:3, 3])
        med = {m: np.median(np.stack(x), axis=0) for m, x in world.items()}
        far = [float(np.max(np.linalg.norm(v.X @ T_kf[v.k][:3, :3].T + T_kf[v.k][:3, 3] - med[v.tag], axis=1)))
               > float(p.tag_pre_gate_mm) for v in keep]
        fit.dropped_views += int(sum(far))
        keep = too_few([v for v, bad in zip(keep, far) if not bad])
    # disagreeing tags and views, each decided on a fit without it
    for _ in range(8):
        if len(seen_in(keep)) < 2:
            break
        T, W = _solve(T_kf, S, keep, prob)
        res = _residuals(T, W, S, keep)
        bad = res > float(p.tag_gate_mm)
        if not bad.any():
            break
        mine = {m: [i for i, v in enumerate(keep) if v.tag == m] for m in seen_in(keep)}
        bad_tags = [m for m, ii in mine.items() if float(np.mean(bad[ii])) > float(p.tag_max_bad_share) and int(bad[ii].sum()) >= 2]
        worst = max(bad_tags, key=lambda m: float(np.median(res[mine[m]]))) if bad_tags else None
        if worst is not None:
            fit.left_out[worst] = "it disagreed with the other tags (moved during the scan, or misread)"
            keep = [v for v in keep if v.tag != worst]
        else:  # single views that do not fit: the worst one at a time is decided on a fit without it
            i = int(np.argmax(res))
            fit.dropped_views += 1
            keep = keep[:i] + keep[i + 1:]
        keep = too_few(keep)
    # a tag moved by less than the gate, or one that alone joins two stretches of the scan: each tag is decided on a
    # fit without it. A candidate changes the scan's shape by more than ``tag_influence_mm`` and, the scan held by the
    # others, its own views disagree with each other by more than twice the tags' typical scatter (a tag that only one
    # stretch of the scan sees agrees with itself, and is kept: it holds that stretch). Of the candidates, the one
    # whose leaving makes the other tags fit best is left out, then all is decided again without it.
    for _ in range(len(seen_in(keep))):
        tags_now = sorted(seen_in(keep))
        if len(tags_now) < 3:
            break
        T_a, W_a = _solve(T_kf, S, keep, prob)
        A = _ahead(T_a)
        res_a = _residuals(T_a, W_a, S, keep)
        typical = float(np.median([np.median(res_a[[i for i, v in enumerate(keep) if v.tag == m]]) for m in tags_now]))
        best_m, best_gain, candidate = None, 0.0, False
        for m in tags_now:
            rest = [v for v in keep if v.tag != m]
            mine = [v for v in keep if v.tag == m]
            if len(seen_in(rest)) < 2 or _stretches(mine) < 2:  # seen in one stretch of the scan: it holds that stretch
                continue
            T_r, W_r = _solve(T_kf, S, rest, prob)
            d = _shape_change(_ahead(T_r), A)
            fit.influence_mm[m] = round(d, 3)
            rest_i = [i for i, v in enumerate(keep) if v.tag != m]
            gain = float(np.median(res_a[rest_i])) - float(np.median(_residuals(T_r, W_r, S, rest)))  # the others' fit
            if gain > best_gain:
                own = float(np.median(_residuals(T_r, _place_tags(T_r, S, mine), S, mine)))
                best_m, best_gain = m, gain
                candidate = d > float(p.tag_influence_mm) and own > 2.0 * typical
        # the tag whose leaving helps the others most is the suspect; it is left out only when it also moves the scan
        # and disagrees with itself (else a tag that only looks bad because the suspect is still in would go instead)
        worst = best_m if candidate else None
        if worst is None:
            break
        fit.left_out[worst] = (f"no other tag confirms where it is (without it the scan's shape would change by up to "
                               f"{fit.influence_mm[worst]:.1f} mm): it may have moved during the scan")
        keep = too_few([v for v in keep if v.tag != worst])
    fit.used = sorted(seen_in(keep))
    if len(fit.used) < 2:
        fit.reason = "too_few"
        return fit
    # never worse, on whole keyframes held out
    ks = sorted({v.k for v in keep})
    every = max(2, int(p.tag_holdout_every))
    splits = []
    for j in range(every):
        held = set(ks[j::every])
        fit_v = [v for v in keep if v.k not in held]
        test_v = [v for v in keep if v.k in held and v.tag in {u.tag for u in fit_v}]
        if len(test_v) < int(p.tag_holdout_min_test) or len({u.tag for u in fit_v}) < 2:
            splits.append({"test": len(test_v), "before_mm": None, "after_mm": None, "ratio": None})
            continue
        before = float(np.median(_residuals(T_kf, _place_tags(T_kf, S, fit_v), S, test_v)))
        Tj, Wj = _solve(T_kf, S, fit_v, prob)
        after = float(np.median(_residuals(Tj, Wj, S, test_v)))
        splits.append({"test": len(test_v), "before_mm": round(before, 4), "after_mm": round(after, 4),
                       "ratio": round(after / max(before, 1e-12), 4)})
    fit.holdout = {"every": every, "splits": splits}
    if any(s["ratio"] is None for s in splits):
        fit.reason = "too_few"
        return fit
    if max(s["before_mm"] for s in splits) < float(p.tag_min_residual_mm):
        fit.reason = "agree"
        return fit
    if any(s["ratio"] > float(p.tag_holdout_max_ratio) for s in splits):
        fit.reason = "not_better"
        return fit
    T, W = _solve(T_kf, S, keep, prob)
    before = _residuals(T_kf, _place_tags(T_kf, S, keep), S, keep)
    after = _residuals(T, W, S, keep)
    chi2 = _odometry_chi2(T, odometry) if odometry else np.zeros(0)
    fit.residual_mm = {"before_median": float(np.median(before)), "after_median": float(np.median(after)),
                       "after_max": float(after.max()), "views": len(keep),
                       "odometry_chi2_max": float(chi2.max()) if len(chi2) else 0.0}
    if len(chi2) and chi2.max() > float(p.max_odometry_chi2):
        fit.reason = "bends_too_much"
        return fit
    fit.T = T
    fit.reason = "anchored"
    return fit
