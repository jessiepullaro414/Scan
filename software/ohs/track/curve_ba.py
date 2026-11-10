"""Laser-curve bundle adjustment: the laser lines' crossings line up the camera poses (research plan
7.3, a first version: poses only, no self-calibration yet).

Every frame gives two accurate 3D curves, one per laser line, triangulated on the calibrated planes
independently of the dense stereo. As the scanner moves, curves from different frames cross on the
surface, and at every crossing the two curves must meet. :func:`laser_curve_adjustment` solves the
poses so that they do:

1. **Curves** (:func:`frame_curves`): the lines of up to ``max_frames`` tracked frames, spread over
   the scan, extracted at full resolution, triangulated, checked against the right image, with
   unit tangents from their neighbours along each line.
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
4. **Never worse:** the solve is first fitted on half the crossings. Only when the other half then
   meet clearly better (their median miss at most ``holdout_max_ratio`` of before) are the poses
   changed. Otherwise the crossings only show the lasers' own scatter, and fitting them would bend
   good poses: on simulated scans without drift the fit moved the poses by 0.4 mm, and the held-out
   half met worse.
5. **Corrections** go to every tracked frame by interpolation between the frames used
   (:func:`ohs.track.posegraph.interpolate_poses`).

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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from ohs.geometry import invert_se3, so3_exp, so3_log

__all__ = ["CurveParams", "Crossings", "frame_curves", "find_crossings", "crossing_residuals", "adjust_poses",
           "laser_curve_adjustment"]

log = logging.getLogger(__name__)

Progress = Callable[[float, str], None]
Curve = tuple[str, np.ndarray, np.ndarray]  # (line name, points (n,3) frame C, unit tangents (n,3) frame C)


@dataclass(frozen=True)
class CurveParams:
    max_frames: int = 200  # frames used, spread over the scan (the rest follow by interpolation)
    radius_mm: float = 3.0  # crossing candidates: point pairs this close
    local_mm: float = 1.5  # the crossing must lie this close to both samples
    min_angle_deg: float = 30.0  # the two curves' tangents differ by at least this
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


# --------------------------------------------------------------------------- 1. curves
def frame_curves(reader: Any, calib: Any, frames: Sequence[int], params: CurveParams | None = None,
                 cancelled: Callable[[], bool] | None = None,
                 progress: Progress | None = None) -> dict[int, list[Curve]]:
    """``{frame index: [(line, points, tangents)]}``: each frame's laser curves at full resolution,
    triangulated on the calibrated planes and checked against the right image (frame C, mm)."""
    from ohs.app.pipeline import default_laser_params, effective_scale  # noqa: PLC0415
    from ohs.vision.laser import LINE_NAMES, extract_laser_lines, stereo_check, triangulate_plane  # noqa: PLC0415
    from ohs.vision.rectify import Rectifier  # noqa: PLC0415

    rect = Rectifier(calib.stereo, 1.0)
    K, B = rect.K, rect.baseline
    lp = default_laser_params(effective_scale(rect.image_size[0]))
    planes = {n: pl for n, pl in (calib.lasers or {}).items() if n in LINE_NAMES}
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
        for name, plane in planes.items():
            uv, seg = ll.get(name), ll.segments(name)
            if len(uv) < 5:
                continue
            P = triangulate_plane(uv, K, plane)
            ok = np.isfinite(P[:, 2]) & (P[:, 2] >= lp.min_depth_mm) & (P[:, 2] <= lp.max_depth_mm)
            if lr is not None and ok.any():
                ok &= stereo_check(P, lr.get(name), K, B, lp.stereo_tol_px)
            n = len(P)
            inner = np.zeros(n, dtype=bool)  # both neighbours on the same polyline and valid
            if len(seg) == n:
                inner[1:-1] = (seg[2:] == seg[1:-1]) & (seg[:-2] == seg[1:-1])
            inner[1:-1] &= ok[2:] & ok[:-2]
            tan = np.zeros((n, 3))
            tan[1:-1] = P[2:] - P[:-2]
            tl = np.linalg.norm(tan, axis=1)
            good = ok & inner & (tl > 1e-6) & (tl < 3.0)
            tan[good] /= tl[good, None]
            if good.sum() >= 10:
                curves.append((name, P[good], tan[good]))
        out[int(f)] = curves
    return out


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


def find_crossings(curves: dict[int, list[Curve]], T: dict[int, np.ndarray],
                   params: CurveParams | None = None) -> Crossings:
    """The crossings of curves from different frames at poses ``T`` (module docstring, step 2)."""
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
    i, j = pairs[:, 0], pairs[:, 1]
    keep = Fr_[i] != Fr_[j]
    i, j = i[keep], j[keep]
    keep = np.linalg.norm(np.cross(Tw_[i], Tw_[j]), axis=1) > math.sin(math.radians(p.min_angle_deg))
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
    local = (np.abs(sa) < p.local_mm) & (np.abs(sb) < p.local_mm)
    i, j, sa, sb, dist = i[local], j[local], sa[local], sb[local], dist[local]
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
                 gaps: dict[int, float] | None = None) -> tuple[dict[int, np.ndarray], list[dict]]:
    """Gauss-Newton on the poses ``T`` of ``frames`` (module docstring, step 3); ``frames[0]``
    stays fixed. ``dense``: ``(points per frame (frame C), k-d tree of the reference vertices,
    vertices, normals)``. ``gaps``: processed frames from each frame to the next (default 1), for
    the random-walk prior. Returns the new poses and one line of numbers per iteration."""
    import scipy.sparse as sp  # noqa: PLC0415
    import scipy.sparse.linalg as spla  # noqa: PLC0415

    p = params or CurveParams()
    frames = [int(f) for f in frames]
    T = {f: np.asarray(T[f], dtype=np.float64).copy() for f in frames}
    if len(frames) < 2:
        return T, []
    col = {f: k - 1 for k, f in enumerate(frames)}
    n = 6 * (len(frames) - 1)
    o = np.mean([T[f][:3, 3] for f in frames], axis=0)
    L = float(p.lever_mm)
    D = np.array([L] * 3 + [1.0] * 3)
    T0 = {f: T[f].copy() for f in frames}
    wo = np.array([1.0 / (L * math.radians(p.odo_deg)) ** 2] * 3 + [1.0 / p.odo_mm ** 2] * 3)
    info: list[dict] = []

    def block(X: np.ndarray, nrm: np.ndarray, f_arr: np.ndarray, sgn: float, row0: int, rows: list, cols: list,
              vals: list) -> None:
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
            for f in frames[1:]:
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
        for k in range(len(frames) - 1):
            fa_, fb_ = frames[k], frames[k + 1]
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
        for f in frames[1:]:
            xi = x[6 * col[f]:6 * col[f] + 6] / D
            E = np.eye(4)
            E[:3, :3] = so3_exp(xi[:3])
            E[:3, 3] = xi[3:] + o - E[:3, :3] @ o  # the rotation is about the scan's centre
            T[f] = E @ T[f]
        info.append({"crossings": int(len(cr)), "used": int((w > 0).sum()),
                     "miss_before_mm": round(float(np.median(np.abs(r))), 4) if len(r) else None,
                     "step_mm": round(float(np.abs(x[3::6]).max()), 4) if n else 0.0})
    return T, info


# --------------------------------------------------------------------------- the whole adjustment
def laser_curve_adjustment(rec_path: str | Path, calib: Any, trajectory: Sequence[Sequence[Any]], *,
                           reference: tuple[np.ndarray, np.ndarray] | None = None,
                           params: CurveParams | None = None, progress: Progress | None = None,
                           cancelled: Callable[[], bool] | None = None) -> tuple[dict[int, np.ndarray], dict]:
    """Adjust the tracked poses so the laser curves meet at their crossings (module docstring).

    ``trajectory``: ``[(frame index, t_host, ok, T_wc)]`` (``trajectory.csv``); ``reference``: the
    surface ``(V, F)`` these poses fused (the base mesh), for the dense term. Returns ``(poses,
    report)``: ``T_wc`` of every tracked frame (the input when nothing changes) and ``frames``,
    ``crossings``, ``miss_mm`` (median before / after), ``holdout`` (median miss of the held-out
    half before / after, and their ratio), ``moved_mm`` / ``moved_deg`` (largest change of a
    camera), ``changed``, ``reason`` (``adjusted``, ``no_lasers``, ``few_crossings``,
    ``not_better``), ``verdict``, ``timings_s`` and, with true poses in the recording,
    ``ground_truth`` (before / after)."""
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
                              "moved_deg": 0.0, "changed": False, "reason": "", "verdict": "", "timings_s": timings,
                              "params": asdict(p)}

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

    if not calib.lasers or len(tracked) < 3:
        return finish(dict(poses_in), "no_lasers", "No laser lines to line the scan up with, so the poses are unchanged.")
    sel = [r for r in tracked]
    if len(sel) > p.max_frames:
        sel = [sel[int(round(x))] for x in np.linspace(0, len(sel) - 1, p.max_frames)]
    frames = [r[0] for r in sel]
    order = [r[0] for r in tracked]
    rank = {f: k for k, f in enumerate(order)}
    gaps = {frames[k]: float(rank[frames[k + 1]] - rank[frames[k]]) for k in range(len(frames) - 1)}
    report["frames"] = len(frames)
    t0 = time.perf_counter()
    curves = frame_curves(reader, calib, frames, p, cancelled=cancelled, progress=lambda f, m: say(0.6 * f, m))
    timings["curves"] = round(time.perf_counter() - t0, 2)
    T_sel = {f: poses_in[f] for f in frames}
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
        for f in frames:
            if cancelled is not None and cancelled():
                raise InterruptedError("cancelled")
            c = kc.get(pos_of[f]) if f in pos_of else None
            if c is not None:
                P = c.fine.pts
                pts[f] = P[:: max(1, len(P) // max(int(p.dense_points), 1))]
        dense = (pts, cKDTree(Vr), Vr, Nr)
        del kc
        timings["dense"] = round(time.perf_counter() - t0, 2)
    t0 = time.perf_counter()
    say(0.8, f"lining up {len(cr):,} laser crossings")
    miss0 = float(np.median(np.abs(crossing_residuals(T_sel, cr)[0])))
    # never worse: fitted on half the crossings, the other half must meet clearly better
    half = np.random.default_rng(0).random(len(cr)) < 0.5
    T_half, _ = adjust_poses(T_sel, cr.subset(half), frames, p, dense, gaps)
    hb = float(np.median(np.abs(crossing_residuals(T_sel, cr.subset(~half))[0])))
    ha = float(np.median(np.abs(crossing_residuals(T_half, cr.subset(~half))[0])))
    ratio = ha / max(hb, 1e-9)
    report["holdout"] = {"before_mm": round(hb, 4), "after_mm": round(ha, 4), "ratio": round(ratio, 3)}
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
        T_new, inf = adjust_poses(T_new, cr, frames, p, dense, gaps)
        info += inf
    miss1 = float(np.median(np.abs(crossing_residuals(T_new, cr)[0])))
    report["miss_mm"] = {"before": round(miss0, 4), "after": round(miss1, 4)}
    report["iterations"] = info
    timings["solve"] = round(time.perf_counter() - t0, 2)
    # every tracked frame, by interpolation between the frames used
    times = np.array([t for _, t, _, _ in tracked])
    T_all = np.stack([T for _, _, _, T in tracked])
    kf_times = np.array([r[1] for r in sel])
    T_out = interpolate_poses(times, T_all, kf_times, np.stack([poses_in[f] for f in frames]),
                              np.stack([T_new[f] for f in frames]))
    poses = {i: T_out[k] for k, (i, _, _, _) in enumerate(tracked)}
    moved = [float(np.linalg.norm(poses[i][:3, 3] - poses_in[i][:3, 3])) for i in poses]
    turned = [float(np.degrees(np.linalg.norm(so3_log(poses[i][:3, :3] @ poses_in[i][:3, :3].T)))) for i in poses]
    report["moved_mm"] = round(max(moved), 3)
    report["moved_deg"] = round(max(turned), 3)
    return finish(poses, "adjusted",
                  f"Lined the scan up on {len(cr):,} laser crossings: their median miss fell from {miss0:.2f} to "
                  f"{miss1:.2f} mm, and the camera moved by up to {max(moved):.2f} mm.")
