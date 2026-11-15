"""Aligning two scans of one part to each other (research plan 4.7.1 and 4.7.2).

Each scan is a mesh in its own world (the camera frame of its first tracked frame). The answer is
the rigid motion ``T`` that takes points of the *source* scan into the *target* scan's world.

1. **Clouds.** The mesh's vertices and normals are thinned to one point per ``voxel_mm`` (fine)
   and per ``coarse_voxel_mm`` (coarse). If one plane holds at least ``support_min_share`` of the
   surface, it is what the part rests on (a table): it is set aside together with everything
   behind it (the fringe a mesh grows at a table's rim), and what is in front is the *part*.
   On a table scan that plane is nine tenths of the mesh and has no features; left in, it decides
   the answer, and every in-plane position and turn fits it equally well (measured: every guess
   came out about 100 degrees wrong on the simulated bracket).
2. **First guesses**, when no ``init`` is given: FPFH features of the coarse part clouds, matched
   by Fast Global Registration and by RANSAC with ``ransac_seeds`` seeds
   (:func:`ohs.track.posegraph.global_match`). Any of them may be wrong.
3. **Refinement.** From every guess, point-to-plane ICP of the part clouds, coarse to fine, free
   in all six directions (:func:`ohs.track.posegraph._icp` with a tiny ``weak_rel``: Straighten
   keeps a tracked guess along weak directions; here the guess is not to be trusted anywhere).
4. **The pick.** A refined guess is scored by the share of part points that find the other scan
   within ``match_mm`` with an agreeing normal (the smaller of the two directions). The best one
   wins. It is accepted only if at least one other guess ended at the same pose (``agree``,
   within ``agree_mm``): a pose that only one guess leads to is not trusted. A guess that ended
   somewhere else and scores nearly as well (``rival_share``) means the part fits in more than
   one position (a symmetric shape, a row of equal holes, a surface that can slide on itself),
   and the pair is not accepted either: the user then gives a rough pose. ``spread_mm`` says how
   far the guesses near the chosen pose ended from it.
5. **Same setup.** If the part was not moved on its support between the two scans
   (``same_setup=True``), the support plane is the same surface in both: the pose is turned and
   lifted, about the part's centre, until the two planes coincide (height and tilt, which a large
   plane pins much better than a small part can), and the part is fitted once more along the
   three motions that are left (sliding on the plane and turning about its normal). When the
   planes are further apart than ``support_max_deg`` / ``support_max_mm`` after the part's fit,
   they are not the same surface and the step is left out. If the part was turned over or moved,
   the support is a different surface in each scan and must stay out (``False``).

The result says how much of the smaller scan's part overlaps (``overlap``) and how well: the RMS
along the normals of what matches within ``match_mm`` (``rmse_mm``), and a robust spread of the
residuals of everything within three times that (``fit_mm``), which is what a poor fit shows in;
how many directions the overlap pins down (``dof``: 6 when all); and a sentence for the user. It
is a pair's alignment; several scans at once (a pose graph over all pairs, 4.7.3) build on it.

Known limits: a part's own flat face that is 30 % or more of a scan made without a table is taken
for the support and set aside (``support_min_share`` above 1 turns the step off); the feature
guesses of Open3D's RANSAC are not the same on every run (its threads share one random
generator), so a pair near the limits can be accepted on one run and refused on the next.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ohs.geometry import fit_plane_ransac, invert_se3, so3_exp
from ohs.track import posegraph as pg

__all__ = ["AlignParams", "ScanCloud", "PairAlignment", "scan_cloud", "scan_cloud_from_mesh", "align_pair"]

log = logging.getLogger(__name__)

Level = tuple[bool, float, int]  # (coarse cloud?, correspondence distance mm, iterations)


@dataclass(frozen=True)
class AlignParams:
    """Settings of :func:`align_pair` (mm; module docstring)."""

    voxel_mm: float = 1.0
    coarse_voxel_mm: float = 4.0
    # the support plane
    support_min_share: float = 0.3  # of the coarse points: below this no plane is set aside
    support_dist_mm: float = 1.5  # points this close to the plane belong to it
    # first guesses
    fpfh_radius_mm: float = 20.0
    feature_dist_mm: float = 6.0
    ransac_seeds: int = 8
    # refinement
    icp_part: tuple[Level, ...] = ((True, 15.0, 40), (False, 8.0, 30), (False, 4.0, 30), (False, 2.0, 30))
    robust_min_mm: float = 0.5
    # same setup: the two support planes may be this far apart after the part's fit and still be one surface
    support_max_deg: float = 5.0
    support_max_mm: float = 5.0
    in_plane: tuple[tuple[float, int], ...] = ((4.0, 15), (2.0, 20))  # (correspondence distance mm, iterations)
    # the pick and the verdict
    match_mm: float = 1.0  # a point has a counterpart within this, with an agreeing normal
    min_overlap: float = 0.15
    max_fit_mm: float = 0.7  # the robust spread of the residuals within 3 match_mm
    agree_mm: float = 2.0  # two refined guesses are the same answer within this RMS point distance
    rival_mm: float = 10.0  # a refined guess farther than this from the chosen pose is another answer
    rival_share: float = 0.9  # another answer with this share of the best score makes the pair ambiguous
    min_part_points: int = 300
    weak_rel: float = 3e-3  # a direction with less than this share of the strongest constraint is not pinned

    def __post_init__(self) -> None:
        for name in ("voxel_mm", "coarse_voxel_mm", "support_dist_mm", "match_mm", "agree_mm", "max_fit_mm"):
            if not float(getattr(self, name)) > 0:
                raise ValueError(f"{name} must be positive")
        if int(self.ransac_seeds) < 0:
            raise ValueError("ransac_seeds must be >= 0")
        if not float(self.support_min_share) > 0:
            raise ValueError("support_min_share must be positive (above 1: no support plane is set aside)")


@dataclass
class ScanCloud:
    """One scan's surface as point clouds, in the scan's world (:func:`scan_cloud`)."""

    part: Any  # posegraph._Cloud without the support plane
    full: Any  # posegraph._Cloud of everything
    both: Any  # the part and as many points of the support as the part has: neither outvotes the other
    support: tuple[np.ndarray, float] | None  # the plane n . x + d = 0 that was set aside
    support_share: float  # the share of the surface on it (0 when none was set aside)

    @property
    def n_part(self) -> int:
        return int(self.part.n) if self.part is not None else 0


@dataclass
class PairAlignment:
    """What :func:`align_pair` found (module docstring)."""

    T: np.ndarray  # source scan's world into the target scan's world
    accepted: bool = False
    reason: str | None = None  # why not, in plain words
    start: str = "features"  # or "given"
    overlap: float = 0.0  # share of the smaller scan's part points with a counterpart within match_mm
    rmse_mm: float = float("nan")  # of the matches within match_mm, along the normals
    fit_mm: float = float("nan")  # robust spread (1.4826 median) of the residuals of the matches within 3 match_mm
    n_matched: int = 0
    candidates: int = 0  # refined guesses
    agree: int = 0  # of them, at the chosen pose
    spread_mm: float = 0.0  # how far the guesses near the chosen pose ended from it (RMS point distance, the largest)
    dof: int = 0  # directions the overlap pins down (6: all)
    same_setup: bool = False
    used_support: bool = False  # the last step ran on all points
    ms: float = 0.0
    verdict: str = ""

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in ("accepted", "reason", "start", "overlap", "rmse_mm", "fit_mm", "n_matched", "candidates",
                                           "agree", "spread_mm", "dof", "same_setup", "used_support", "ms", "verdict")}
        d["T"] = np.asarray(self.T).tolist()
        return d


# --------------------------------------------------------------------------- clouds
def _cloud(P: np.ndarray, N: np.ndarray, voxel: float, coarse: float) -> Any:
    """A posegraph cloud (fine and coarse level with search trees) of points with normals."""
    from scipy.spatial import cKDTree  # noqa: PLC0415

    o3d = pg._open3d()
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.asarray(P, dtype=np.float64)))
    pc.normals = o3d.utility.Vector3dVector(np.asarray(N, dtype=np.float64))
    fine = pc.voxel_down_sample(float(voxel))  # averages points and normals per voxel
    fine.normalize_normals()
    co = fine.voxel_down_sample(float(coarse))
    co.normalize_normals()
    levels = []
    for pcd in (fine, co):
        pts = np.asarray(pcd.points, dtype=np.float64).copy()
        nrm = np.asarray(pcd.normals, dtype=np.float64).copy()
        levels.append(pg._Level(pts, nrm, cKDTree(pts, balanced_tree=False, compact_nodes=False) if len(pts) else None))
    return pg._Cloud(levels[0], levels[1], None, len(levels[0].pts))


def scan_cloud(V: np.ndarray, N: np.ndarray, params: AlignParams | None = None, seed: int = 0) -> ScanCloud:
    """The clouds of a scan from its mesh vertices ``V`` (n,3) and unit normals ``N`` (n,3)."""
    p = params if params is not None else AlignParams()
    V = np.asarray(V, dtype=np.float64).reshape(-1, 3)
    N = np.asarray(N, dtype=np.float64).reshape(-1, 3)
    ok = np.all(np.isfinite(V), axis=1) & np.all(np.isfinite(N), axis=1) & (np.linalg.norm(N, axis=1) > 0.5)
    V, N = V[ok], N[ok]
    if len(V) == 0:
        raise ValueError("the scan's mesh has no vertices with normals")
    order = np.lexsort((V[:, 2], V[:, 1], V[:, 0]))  # a mesh comes with its vertices in a different order every run
    V, N = V[order], N[order]
    full = _cloud(V, N, p.voxel_mm, p.coarse_voxel_mm)
    support, share, part_mask = None, 0.0, np.ones(len(V), bool)
    C = full.coarse.pts  # evenly spread, so a share of points is a share of the surface
    if len(C) >= 50:
        try:
            n, d, mask, _ = fit_plane_ransac(C, thresh=float(p.support_dist_mm), iters=800, seed=seed, min_inliers=10)
        except ValueError:
            mask = np.zeros(len(C), bool)
        if mask.mean() >= p.support_min_share:
            # the plane's normal towards the side it was seen from: the part rests on that side, and what
            # lies behind the support (the fringe a mesh grows at a table's rim and underside) is not the part
            if float(np.sum(full.coarse.nrm[mask] @ n)) < 0:
                n, d = -n, -d
            support, share = (n, float(d)), float(mask.mean())
            part_mask = V @ n + d > p.support_dist_mm
    part = _cloud(V[part_mask], N[part_mask], p.voxel_mm, p.coarse_voxel_mm) if part_mask.any() else None
    both = full
    if support is not None and part is not None and part.n:
        # the support near the part, thinned to the part's number of points
        S, NS = full.fine.pts, full.fine.nrm
        on = np.abs(S @ support[0] + support[1]) <= p.support_dist_mm
        reach = 2.0 * float(np.percentile(np.linalg.norm(part.fine.pts - part.fine.pts.mean(axis=0), axis=1), 95))
        near = on & (np.linalg.norm(S - part.fine.pts.mean(axis=0), axis=1) <= reach)
        idx = np.flatnonzero(near if near.sum() >= 50 else on)
        if len(idx) > part.n:
            idx = idx[np.random.default_rng(seed).choice(len(idx), part.n, replace=False)]
        both = _cloud(np.vstack([part.fine.pts, S[idx]]), np.vstack([part.fine.nrm, NS[idx]]), p.voxel_mm, p.coarse_voxel_mm)
    return ScanCloud(part, full, both, support, share)


def scan_cloud_from_mesh(path: str | Path, params: AlignParams | None = None, seed: int = 0) -> ScanCloud:
    """:func:`scan_cloud` of a mesh file (a processed scan's ``mesh.ply``)."""
    from ohs.mesh.topology import vertex_normals  # noqa: PLC0415
    from ohs.track.export import load_mesh  # noqa: PLC0415

    V, F, N = load_mesh(path)
    if N is None or len(N) != len(V):
        N = vertex_normals(V, F)
    return scan_cloud(V, N, params, seed)


# --------------------------------------------------------------------------- scoring
def _score(src: Any, dst: Any, T: np.ndarray, tol: float) -> tuple[float, float, float, float, int]:
    """``(pick, overlap, rmse, fit, matches)`` of two clouds at ``T``. A fine point matches when it
    has a counterpart within ``tol`` with an agreeing normal. ``pick``: the share of matching
    points, the smaller of the two directions (what the guesses are ranked by); ``overlap``: the
    share for the cloud with fewer points; ``rmse``: of the matches, along the normals; ``fit``:
    1.4826 times the median absolute residual of the matches within ``3 tol``, which a wrong pose
    cannot keep small."""
    nan = float("nan")
    if src.n == 0 or dst.n == 0 or not np.all(np.isfinite(T)):
        return 0.0, 0.0, nan, nan, 0
    i, j = pg._match(src.fine.pts, src.fine.nrm, dst.fine, T, tol)
    i2, _ = pg._match(dst.fine.pts, dst.fine.nrm, src.fine, invert_se3(T), tol)
    if len(i) == 0:
        return 0.0, 0.0, nan, nan, 0
    Q = src.fine.pts @ T[:3, :3].T + T[:3, 3]
    r = np.einsum("ij,ij->i", Q[i] - dst.fine.pts[j], dst.fine.nrm[j])
    iw, jw = pg._match(src.fine.pts, src.fine.nrm, dst.fine, T, 3.0 * tol)
    rw = np.einsum("ij,ij->i", Q[iw] - dst.fine.pts[jw], dst.fine.nrm[jw])
    share_src, share_dst = len(i) / src.n, len(i2) / dst.n
    return (min(share_src, share_dst), share_src if src.n <= dst.n else share_dst, float(np.sqrt(np.mean(r * r))),
            1.4826 * float(np.median(np.abs(rw))), int(len(i)))


def _apart_mm(P: np.ndarray, Ta: np.ndarray, Tb: np.ndarray) -> float:
    """RMS distance between the points ``P`` moved by ``Ta`` and by ``Tb``."""
    D = P @ (Ta[:3, :3] - Tb[:3, :3]).T + (Ta[:3, 3] - Tb[:3, 3])
    return float(np.sqrt(np.mean(np.sum(D * D, axis=1))))


def _dof(src: Any, dst: Any, T: np.ndarray, tol: float, weak_rel: float) -> int:
    """How many of the six directions the matched surfaces pin down at ``T``."""
    i, j = pg._match(src.fine.pts, src.fine.nrm, dst.fine, T, tol)
    if len(i) < 30:
        return 0
    P = src.fine.pts[i] - src.fine.pts[i].mean(axis=0)  # about the overlap's centre: turns and shifts apart
    Ns = dst.fine.nrm[j] @ T[:3, :3]
    J = np.concatenate([np.cross(P, Ns), Ns], axis=1)
    L = float(np.median(np.linalg.norm(P, axis=1))) or 1.0  # the overlap's own size scales a turn to mm
    _, lam, _ = pg._scaled_subspace(J.T @ J / len(i), L, weak_rel)
    return int(len(lam))


# --------------------------------------------------------------------------- same setup
def _onto_support(T: np.ndarray, src_plane: tuple[np.ndarray, float], dst_plane: tuple[np.ndarray, float],
                  pivot: np.ndarray) -> tuple[np.ndarray, float, float]:
    """``T`` turned about ``pivot`` (a point in the target's world) and shifted along the target
    plane's normal so that the source's support plane, moved by it, is the target's:
    ``(T, the turn in degrees, the shift in mm)``."""
    ns, ds = np.asarray(src_plane[0], dtype=np.float64), float(src_plane[1])
    nd, dd = np.asarray(dst_plane[0], dtype=np.float64), float(dst_plane[1])
    n1 = T[:3, :3] @ ns
    d1 = ds - float(n1 @ T[:3, 3])
    if float(n1 @ nd) < 0:  # a plane's normal has no sign
        n1, d1 = -n1, -d1
    axis = np.cross(n1, nd)
    sn = float(np.linalg.norm(axis))
    angle = float(np.arctan2(sn, float(n1 @ nd)))
    Rc = so3_exp(axis / sn * angle) if sn > 1e-12 else np.eye(3)
    d2 = d1 + float((n1 - nd) @ pivot)  # the plane's offset after the turn about the pivot
    shift = d2 - dd
    C = np.eye(4)
    C[:3, :3] = Rc
    C[:3, 3] = pivot - Rc @ pivot + shift * nd
    return C @ T, float(np.degrees(angle)), float(shift)


def _in_plane_icp(src: Any, dst: Any, T: np.ndarray, normal_src: np.ndarray, levels: tuple, robust_min_mm: float) -> np.ndarray:
    """Point-to-plane ICP of ``src`` onto ``dst`` from ``T`` that only slides on the support plane
    and turns about its normal (``normal_src``: the plane's normal in the source's world)."""
    n = np.asarray(normal_src, dtype=np.float64)
    n = n / np.linalg.norm(n)
    u = np.cross(n, [1.0, 0.0, 0.0] if abs(n[0]) < 0.9 else [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    B = np.zeros((6, 3))  # a motion of the source frame: rotation first, then translation
    B[:3, 0], B[3:, 1], B[3:, 2] = n, u, v
    step = max(1, -(-len(src.fine.pts) // pg._ICP_POINTS))
    P, N = src.fine.pts[::step], src.fine.nrm[::step]
    c = P.mean(axis=0)
    T = T.copy()
    for dist, n_it in levels:
        for _ in range(int(n_it)):
            sysm = pg._normal_system(P, N, dst.fine, T, float(dist), robust_min_mm)
            if sysm is None:
                return T
            H, g, _, _ = sysm
            # turn about the normal through the part's centre, so that a turn is not also a slide
            Bc = B.copy()
            Bc[3:, 0] = -np.cross(n, c)
            A = Bc.T @ H @ Bc
            try:
                a = -np.linalg.solve(A + 1e-9 * np.trace(A) * np.eye(3), Bc.T @ g)
            except np.linalg.LinAlgError:
                return T
            xi = Bc @ a
            T = T @ pg._exp6(xi)
            if np.linalg.norm(xi[:3]) < 1e-6 and np.linalg.norm(xi[3:]) < 1e-4:
                break
    return T


# --------------------------------------------------------------------------- the pair
def align_pair(src: ScanCloud, dst: ScanCloud, params: AlignParams | None = None, *, init: np.ndarray | None = None,
               same_setup: bool = False) -> PairAlignment:
    """Align the scan ``src`` to the scan ``dst`` (module docstring). ``init``: a known rough pose
    of the source in the target's world (from tags or picked points); without it the first guesses
    come from the shape. ``same_setup``: the part was not moved on its support between the scans."""
    p = params if params is not None else AlignParams()
    t0 = time.perf_counter()
    out = PairAlignment(T=np.eye(4) if init is None else np.asarray(init, dtype=np.float64).reshape(4, 4).copy(),
                        start="features" if init is None else "given", same_setup=bool(same_setup))

    def done(reason: str | None = None) -> PairAlignment:
        out.reason = reason
        out.accepted = reason is None
        out.ms = round(1e3 * (time.perf_counter() - t0), 1)
        if reason is None:
            sure = (f"{out.agree} of {out.candidates} starting guesses agree" if out.start == "features"
                    else "from the given starting pose")
            loose = "" if out.dof >= 6 else f" The overlap leaves {6 - out.dof} direction(s) of motion loose."
            out.verdict = (f"Aligned: {100 * out.overlap:.0f} % of the smaller scan's part lies within {p.match_mm:g} mm "
                           f"of the other scan, {out.rmse_mm:.2f} mm RMS; {sure}.{loose}")
        else:
            out.verdict = f"Not aligned: {reason}."
        return out

    a, b = src.part, dst.part
    if a is None or b is None or min(a.n, b.n) < p.min_part_points:
        return done("one of the scans shows too little of the part besides its support "
                    f"({0 if a is None else a.n} and {0 if b is None else b.n} points; at least {p.min_part_points} each)")
    sp = pg.StraightenParams(fpfh_radius_mm=float(p.fpfh_radius_mm), fgr_max_corr_mm=float(p.feature_dist_mm))
    for c in (a, b):  # the features are kept on the cloud: made again when the radius is another
        if getattr(c, "fpfh_radius_mm", None) != float(p.fpfh_radius_mm):
            c.feat, c.fpfh_radius_mm = None, float(p.fpfh_radius_mm)
    starts: list[np.ndarray] = []
    if init is not None:
        starts.append(out.T.copy())
    else:
        o3d = pg._open3d()
        for method, seed in [("fgr", 0)] + [("ransac", k) for k in range(int(p.ransac_seeds))]:
            if hasattr(o3d.utility, "random"):
                o3d.utility.random.seed(int(seed))  # the same guesses on every run, as far as Open3D allows
            Tg = pg.global_match(a, b, sp, method, seed)
            if Tg is not None:
                starts.append(Tg)
    if not starts:
        return done("no starting guess could be made from the shapes")
    free = 1e-9  # ICP free in every direction: nothing of a feature guess is worth keeping
    refined = []
    for T0 in starts:
        T = pg._icp(a, b, T0, p.icp_part, free, p.robust_min_mm)
        if np.all(np.isfinite(T)):
            refined.append((T, _score(a, b, T, p.match_mm)))
    if not refined:
        return done("the refinement failed from every starting guess")
    out.candidates = len(refined)
    T, best = max(refined, key=lambda c: (c[1][0], -c[1][3] if np.isfinite(c[1][3]) else -np.inf))
    apart = [_apart_mm(a.fine.pts, c[0], T) for c in refined]
    out.agree = sum(d <= p.agree_mm for d in apart)
    out.spread_mm = round(max((d for d in apart if d <= p.rival_mm), default=0.0), 3)
    rival = max((c[1][0] for c, d in zip(refined, apart) if d > p.agree_mm), default=0.0)
    out.T = T
    _, out.overlap, out.rmse_mm, out.fit_mm, out.n_matched = best
    if best[0] <= 0:
        return done("the two scans do not overlap at any of the starting guesses")
    if same_setup and src.support is not None and dst.support is not None:
        pivot = a.fine.pts.mean(axis=0) @ T[:3, :3].T + T[:3, 3]
        T2, turn, shift = _onto_support(T, src.support, dst.support, pivot)
        if turn <= p.support_max_deg and abs(shift) <= p.support_max_mm:
            T2 = _in_plane_icp(a, b, T2, src.support[0], p.in_plane, p.robust_min_mm)
            s2 = _score(a, b, T2, p.match_mm)
            if np.all(np.isfinite(T2)) and s2[0] >= 0.9 * best[0]:  # the part still fits: keep what the support added
                out.T, out.used_support = T2, True
                _, out.overlap, out.rmse_mm, out.fit_mm, out.n_matched = s2
        else:
            log.info("same setup asked for, but the two supports are %.1f deg and %.1f mm apart after the part's fit: "
                     "left out", turn, shift)
    use_all = out.used_support
    out.dof = _dof(src.both if use_all else a, dst.both if use_all else b, out.T, p.match_mm, p.weak_rel)
    if out.overlap < p.min_overlap:
        return done(f"too little of the two scans overlaps ({100 * out.overlap:.0f} % of the smaller scan's part within "
                    f"{p.match_mm:g} mm; at least {100 * p.min_overlap:.0f} %)")
    if not out.fit_mm <= p.max_fit_mm:
        return done(f"the overlapping surfaces do not match closely (the residuals spread over {out.fit_mm:.2f} mm; "
                    f"at most {p.max_fit_mm:g} mm)")
    if out.start == "features" and rival >= p.rival_share * best[0]:
        return done("the part fits in more than one position about equally well (a symmetric shape, a repeated "
                    "feature, or a surface that can slide on itself): give a rough starting pose, or scan with more of "
                    "the part's distinct features in both")
    if out.start == "features" and out.agree < 2:
        return done("only one of the starting guesses led to this pose, which is too little to trust it: "
                    "give a rough starting pose, or scan with more overlap")
    log.info("aligned a pair: overlap %.0f %%, %.2f mm RMS, %d of %d guesses agree, %d directions pinned (%s)",
             100 * out.overlap, out.rmse_mm, out.agree, out.candidates, out.dof,
             "with the support" if out.used_support else "part only")
    return done()
