"""Aligning two scans of one part to each other (research plan 4.7.1 and 4.7.2).

Each scan is a mesh in its own world (the camera frame of its first tracked frame). The answer is
the rigid motion ``T`` that takes points of the *source* scan into the *target* scan's world. We
cannot know what the parts will be: the pose is found from whatever angle the scans were taken
when enough of the part is shared, and when the evidence is not there the answer says "not sure",
why, and which poses it had in mind, and is not accepted (MAINTENANCE_PLAN F11).

1. **Clouds.** The mesh's vertices and normals are thinned to one point per ``voxel_mm`` (fine)
   and per ``coarse_voxel_mm`` (coarse). If one plane holds at least ``support_min_share`` of the
   surface and passes the three tests of a table (:func:`_not_a_table`, as
   :mod:`ohs.mesh.support` judges it: something stands on it; no more of the scan lies well
   behind it than in front; the part does not go on behind it beside what stands on it), it is
   what the part rests on: it is set aside together with everything behind it (the fringe a mesh
   grows at a table's rim), and what is in front is the *part*. A plane that fails them is one of
   the part's own faces and stays (``support_note`` says why); the next largest plane is tried
   once. On a table scan that plane is nine tenths of the mesh and has no features; left in, it
   decides the answer, and every in-plane position and turn fits it equally well (measured: every
   guess came out about 100 degrees wrong on the simulated bracket). Then **stray bits**: the
   part's points joined over gaps of ``piece_link_voxels`` voxels form pieces, and pieces under
   ``piece_min_share`` of the largest (bits left on the table, ``stray``) are left out.
2. **First guesses**, when no ``init`` is given: FPFH features of the coarse part clouds, matched
   by Fast Global Registration and by RANSAC with ``ransac_seeds`` seeds
   (:func:`ohs.track.posegraph.global_match`). Any of them may be wrong, and several can be wrong
   together.
3. **Refinement.** From every guess, point-to-plane ICP of the part clouds, coarse to fine, free
   in all six directions (:func:`ohs.track.posegraph._icp` with a tiny ``weak_rel``: Straighten
   keeps a tracked guess along weak directions; here the guess is not to be trusted anywhere).
   Points picked by hand are a rough start and treated the same way. A start from tags
   (``init_from="tags"``, a few tenths of a millimetre off) is different: it is kept along the
   directions the surfaces pin weakly (``start_weak_rel``), so a sphere or a cylinder stays turned as
   the tags say instead of spinning to wherever two partial views overlap most. (From picked
   points such a part still turns freely: tags are the way to align it.) The ICP's Tukey weights
   (scale ``robust_min_mm``) keep it a robust fit, not a least-squares one; for two scans of one
   part that is the better choice (measured, F11).
4. **Turned round.** The best pose is refined again from itself turned half round about each
   principal axis of the source's part and of the target's part, and a quarter round both ways
   about the source's thinnest direction (:func:`_turned_starts`, ``turn_starts``). A wrong pose
   that scores well is most often the right one turned round, whatever the part (its axes are
   where its shape balances), and the first guesses can all fall into it together: on the
   simulated bracket against its true shape 8 of 9 guesses agreed on a pose turned 180°, which
   scored worse than the true pose that none of them reached.
5. **Sure or not sure.** Every refined pose is scored by the share of part points that find the
   other scan within ``match_mm`` with an agreeing normal (the smaller of the two directions),
   and poses within ``agree_mm`` of each other are one pose. From the shape (no ``init``), the
   best pose is accepted only when

   * enough is shared: ``overlap`` of at least ``min_overlap`` and a tight fit (``fit_mm`` at
     most ``max_fit_mm``);
   * it beats every other pose by a clear margin: the next scores less than ``rival_share``
     (0.9) of it. Measured: on every pair that was found the next pose scored 0.24-0.79 of the
     right one; on a ball, a sphere and a plain plate 0.93-1.0;
   * starts that differ reach it: at least two (``reached``: the first guesses that ended there;
     turned starts count only when they found it from elsewhere, not when they came back to the
     pose they were turned from).

   Otherwise it is "not sure" (``doubt``: ``rival``, ``turned``, ``one_guess``), says why in
   words and what settles it (picked points, or choosing one of its poses), and carries the poses
   in ``poses`` (the best first, then the others that score best, at most :data:`MAX_POSES`).
   A start that was given is checked too: picked points decide between poses that fit alike, but
   when another pose scores better than the points' by ``points_rival_share`` (their pose less
   than 0.8 of it), or the refinement pulled the scan more than ``rival_mm`` from where the points
   put it, the answer is "not sure" (``doubt="points"``). A start from the tags is not overruled
   by a better-scoring pose (a sphere turned round always overlaps more of itself somewhere).
   ``spread_mm`` says how far the starts near the chosen pose ended from it.
6. **Same setup.** If the part was not moved on its support between the two scans
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
how many directions the overlap pins down (``dof``: 6 when all); whether it is sure, and a
sentence for the user. It is a pair's alignment; several scans at once (a pose graph over all
pairs, 4.7.3) build on it.

Known limits: the feature guesses of Open3D's RANSAC are not the same on every run (its threads
share one random generator), so a pair near the limits can be sure on one run and not sure on the
next; a thin plate (1 mm) on its table lies within the table's band, so it goes with the table and
the answer is "too little of the part" (picked points do not help: tags on the part, or a scan of
it standing up, do).
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ohs.geometry import fit_plane_ransac, invert_se3, kabsch, so3_exp
from ohs.track import posegraph as pg

__all__ = ["AlignParams", "ScanCloud", "PairAlignment", "scan_cloud", "scan_cloud_from_mesh", "align_pair",
           "pose_from_point_pairs"]

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
    # another pose with this share of the best score makes the pair "not sure" (module docstring, 5.;
    # measured: the next pose scored 0.24-0.79 of the right one on every pair that was found, 0.93-1.0
    # on a ball, a sphere and a plain plate)
    rival_share: float = 0.9
    # picked points are contradicted when their pose scores less than this share of another pose's
    points_rival_share: float = 0.8
    min_part_points: int = 300
    # a plane is the table only when something stands on it, little of the scan lies well behind it,
    # and the part does not go on behind it beside what stands on it (module docstring, 1.)
    support_stand_points: int = 30  # coarse points in front of it, at least
    support_behind_share: float = 0.1  # of those: points well behind it, beside them, at most
    # stray bits left on the table: the part's points joined over gaps of this many voxels form a piece,
    # and pieces under this share of the largest are left out
    piece_link_voxels: float = 3.0
    piece_min_share: float = 0.1
    turn_starts: bool = True  # the chosen pose is tried again turned round (module docstring, 4.)
    weak_rel: float = 3e-3  # a direction with less than this share of the strongest constraint is not pinned
    # a start from tags is kept along directions with less than this share
    # (a sphere's turn about the vertical changes how much of two partial views overlap: at 3e-3 the
    # fit drifted 13 mm from a 0.3 mm start on the simulated sphere, at 0.03 it stayed and fitted better)
    start_weak_rel: float = 0.03

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
    stray: int = 0  # part points left out as stray bits (away from the part's main pieces)
    support_note: str | None = None  # why a large plane was not taken for the table

    @property
    def n_part(self) -> int:
        return int(self.part.n) if self.part is not None else 0


@dataclass
class PairAlignment:
    """What :func:`align_pair` found (module docstring)."""

    T: np.ndarray  # source scan's world into the target scan's world
    accepted: bool = False
    reason: str | None = None  # why not, in plain words
    start: str = "features"  # or "given" (picked points), "tags"
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
    sure: bool = False  # = accepted: the evidence is there (module docstring, 5.)
    doubt: str | None = None  # why not, in one word: part, guess, overlap, fit, rival, turned, one_guess, points
    poses: list[dict] = field(default_factory=list)  # the poses it had in mind, the chosen one first (module docstring, 5.)
    turned: int = 0  # starts tried turned round
    reached: int = 0  # starts that ended at the chosen pose independently of it (module docstring, 5.)

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in ("accepted", "reason", "start", "overlap", "rmse_mm", "fit_mm", "n_matched", "candidates",
                                           "agree", "spread_mm", "dof", "same_setup", "used_support", "ms", "verdict", "sure",
                                           "doubt", "turned", "reached")}
        d["T"] = np.asarray(self.T).tolist()
        d["poses"] = [dict(p) for p in self.poses]
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
    support, share, part_mask, note = None, 0.0, np.ones(len(V), bool), None
    C = full.coarse.pts  # evenly spread, so a share of points is a share of the surface
    left = np.ones(len(C), bool)
    for _ in range(2):  # the largest plane, and when that is a face of the part, the next one
        if left.sum() < 50:
            break
        try:
            n, d, _, _ = fit_plane_ransac(C[left], thresh=float(p.support_dist_mm), iters=800, seed=seed, min_inliers=10)
        except ValueError:
            break
        mask = np.abs(C @ n + d) <= p.support_dist_mm
        if mask.mean() < p.support_min_share:
            break
        # the plane's normal towards the side it was seen from: the part rests on that side, and what
        # lies behind the support (the fringe a mesh grows at a table's rim and underside) is not the part
        if float(np.sum(full.coarse.nrm[mask] @ n)) < 0:
            n, d = -n, -d
        why = _not_a_table(C, n, float(d), p)
        if why is None:
            support, share = (n, float(d)), float(mask.mean())
            part_mask = V @ n + d > p.support_dist_mm
            break
        note = f"a plane holding {100 * mask.mean():.0f} % of the scan was kept as part of the part: it {why}"
        left &= ~mask
    part = _cloud(V[part_mask], N[part_mask], p.voxel_mm, p.coarse_voxel_mm) if part_mask.any() else None
    part, stray = _main_pieces(part, p)
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
    return ScanCloud(part, full, both, support, share, stray=stray, support_note=note)


def _not_a_table(C: np.ndarray, n: np.ndarray, d: float, p: AlignParams) -> str | None:
    """Why the plane ``n . x + d = 0`` (its normal towards where it was seen from) is not what the
    part stands on, or None when it can be. The three guards of :mod:`ohs.mesh.support`, on the
    same coarse cloud:

    * something stands on it: ``support_stand_points`` points or more in front of it (or nothing
      lies behind it either: a bare table, or one with only a thin part on it, is still a table);
    * no more of the scan lies well behind it than in front (a part's own face has the part
      behind it). Only what lies more than twice the band behind counts: a noisy table (Open3D's
      mesh of a short scan, 0.4-0.8 mm scatter) has as many points just outside its band behind
      it as a small part has in front;
    * the part does not go on behind it beside what stands on it (a face of the part with more of
      the part beyond its edge): points well behind it, seen along its normal, within the reach of
      what stands in front, fewer than ``support_behind_share`` of the front.

    Without these, a block scanned all round lost itself: its top face (31 % of the surface) was
    taken for the table and everything behind it, the whole block, set aside."""
    from scipy.spatial import cKDTree  # noqa: PLC0415

    band = float(p.support_dist_mm)
    h = C @ n + d
    front = h > band
    deep = h < -2.0 * band
    nf = int(front.sum())
    if nf < int(p.support_stand_points):
        if int(deep.sum()) < int(p.support_stand_points):
            return None  # nothing in front and nothing behind: a bare table (or one with only a thin part on it)
        return "has nothing standing on it"
    if int(deep.sum()) > nf:
        return "has more of the scan behind it than in front, as a face of the part has the part behind it"
    if deep.any():
        X = C - h[:, None] * n  # seen along the normal
        Xf = X[front]
        reach = max(10.0, float(np.percentile(np.linalg.norm(Xf - Xf.mean(axis=0), axis=1), 95)))
        dist, _ = cKDTree(Xf).query(X[deep], k=1, distance_upper_bound=reach)
        near = int(np.isfinite(dist).sum())
        if near >= max(10, float(p.support_behind_share) * nf):
            return "has the part going on behind it beside what stands on it, as a part's own face has"
    return None


def _main_pieces(c: Any, p: AlignParams) -> tuple[Any, int]:
    """The part's cloud with stray bits left out: its fine points joined over gaps of
    ``piece_link_voxels`` voxels form pieces, and pieces under ``piece_min_share`` of the largest
    go. A table scan leaves such bits on the table when the plane is set aside (33 points, 1.4 %
    of the simulated bracket's part, up to 290 mm away); few as they are, with them 8 of 9 first
    guesses of that scan against the bracket's true shape agreed on a pose turned 180°.
    ``(cloud, points left out)``."""
    from scipy.sparse import coo_matrix  # noqa: PLC0415
    from scipy.sparse.csgraph import connected_components  # noqa: PLC0415
    from scipy.spatial import cKDTree  # noqa: PLC0415

    if c is None or c.n < 2:
        return c, 0
    P, N = c.fine.pts, c.fine.nrm
    pairs = cKDTree(P).query_pairs(float(p.piece_link_voxels) * float(p.voxel_mm), output_type="ndarray")
    A = coo_matrix((np.ones(len(pairs), dtype=np.int8), (pairs[:, 0], pairs[:, 1])), shape=(len(P), len(P)))
    _, lab = connected_components(A, directed=False)
    size = np.bincount(lab)
    keep = size[lab] >= float(p.piece_min_share) * size.max()
    if keep.all():
        return c, 0
    return _cloud(P[keep], N[keep], p.voxel_mm, p.coarse_voxel_mm), int((~keep).sum())


def scan_cloud_from_mesh(path: str | Path, params: AlignParams | None = None, seed: int = 0) -> ScanCloud:
    """:func:`scan_cloud` of a mesh file (a processed scan's ``mesh.ply``)."""
    from ohs.mesh.topology import vertex_normals  # noqa: PLC0415
    from ohs.track.export import load_mesh  # noqa: PLC0415

    V, F, N = load_mesh(path)
    if N is None or len(N) != len(V):
        N = vertex_normals(V, F)
    return scan_cloud(V, N, params, seed)


# --------------------------------------------------------------------------- a rough pose from picked points
def pose_from_point_pairs(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, float]:
    """The rigid motion that takes points picked on the source scan onto the matching points
    picked on the target scan (three pairs or more, not in one line), and how well they then
    agree: ``(T, RMS distance of the pairs in mm)``. A start for :func:`align_pair` (``init``),
    good to how well the points were picked; a large RMS means two pairs do not match.
    ``ValueError`` for fewer than three pairs or pairs that lie in one line."""
    A = np.asarray(src, dtype=np.float64).reshape(-1, 3)
    B = np.asarray(dst, dtype=np.float64).reshape(-1, 3)
    if len(A) != len(B) or len(A) < 3:
        raise ValueError(f"three or more pairs of points are needed ({len(A)} and {len(B)} given)")
    if not (np.all(np.isfinite(A)) and np.all(np.isfinite(B))):
        raise ValueError("a picked point is not a number")
    for P in (A, B):  # points in one line leave the turn about that line open
        s = np.linalg.svd(P - P.mean(axis=0), compute_uv=False)
        if s[1] < 0.05 * max(s[0], 1e-9) or s[0] < 1.0:
            raise ValueError("the picked points lie in one line or on one spot: pick points that span a triangle")
    R, t = kabsch(A, B)
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, t
    rms = float(np.sqrt(np.mean(np.sum((A @ R.T + t - B) ** 2, axis=1))))
    return T, rms


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


def _in_plane_icp(src: Any, dst: Any, T: np.ndarray, normal_src: np.ndarray, levels: tuple, robust_min_mm: float,
                  weak_rel: float | None = None) -> np.ndarray:
    """Point-to-plane ICP of ``src`` onto ``dst`` from ``T`` that only slides on the support plane
    and turns about its normal (``normal_src``: the plane's normal in the source's world). With
    ``weak_rel`` it moves only along the directions the surfaces pin (at least that share of the
    strongest, the turn scaled to mm at the part's size), as :func:`ohs.track.posegraph._icp`
    does: a start that was given is kept along the others (a sphere's turn about the normal)."""
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
    size = float(np.median(np.linalg.norm(P - c, axis=1))) or 1.0
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
            if weak_rel is None:
                try:
                    a = -np.linalg.solve(A + 1e-9 * np.trace(A) * np.eye(3), Bc.T @ g)
                except np.linalg.LinAlgError:
                    return T
            else:
                S = np.array([1.0 / size, 1.0, 1.0])  # the turn in mm at the part's size
                lam, U = np.linalg.eigh(A * S[:, None] * S[None, :])
                keep = lam > max(float(weak_rel) * float(lam[-1]), 1e-30)
                U, lam = U[:, keep], lam[keep]
                a = -S * (U @ ((U.T @ (S * (Bc.T @ g))) / lam))
                if not np.all(np.isfinite(a)):
                    return T
            xi = Bc @ a
            T = T @ pg._exp6(xi)
            if np.linalg.norm(xi[:3]) < 1e-6 and np.linalg.norm(xi[3:]) < 1e-4:
                break
    return T


# --------------------------------------------------------------------------- the pair
def align_pair(src: ScanCloud, dst: ScanCloud, params: AlignParams | None = None, *, init: np.ndarray | None = None,
               same_setup: bool = False, init_from: str = "given") -> PairAlignment:
    """Align the scan ``src`` to the scan ``dst`` (module docstring). ``init``: a known rough pose
    of the source in the target's world (from tags or picked points); without it the first guesses
    come from the shape. ``same_setup``: the part was not moved on its support between the scans.
    ``init_from``: where ``init`` came from, for the result's ``start`` and its sentence
    (``"given"``: points picked by the user; ``"tags"``: :func:`ohs.align.tags.pose_from_tags`)."""
    p = params if params is not None else AlignParams()
    t0 = time.perf_counter()
    if init_from not in ("given", "tags"):
        raise ValueError(f"init_from must be 'given' or 'tags', got {init_from!r}")
    out = PairAlignment(T=np.eye(4) if init is None else np.asarray(init, dtype=np.float64).reshape(4, 4).copy(),
                        start="features" if init is None else init_from, same_setup=bool(same_setup))

    given = {"given": "your points", "tags": "the tags"}.get(out.start, "")

    def done(reason: str | None = None, doubt: str | None = None) -> PairAlignment:
        out.reason = reason
        out.accepted = out.sure = reason is None
        out.doubt = doubt
        out.ms = round(1e3 * (time.perf_counter() - t0), 1)
        if reason is None:
            sure = (f"{out.agree} of {out.candidates} starting guesses agree" if out.start == "features"
                    else "from the tags both scans saw" if out.start == "tags" else "from the start you gave")
            loose = "" if out.dof >= 6 else f" The overlap leaves {6 - out.dof} direction(s) of motion loose."
            out.verdict = (f"Aligned: {100 * out.overlap:.0f} % of the smaller scan's part lies within {p.match_mm:g} mm "
                           f"of the other scan, typically {out.rmse_mm:.2f} mm; {sure}.{loose}")
            if out.start != "features" and len(out.poses) > 1 and out.poses[1]["pick"] >= p.rival_share * out.poses[0]["pick"]:
                out.verdict += f" The shapes alone fit about as well another way: {given} decided."
        else:
            out.verdict = f"Not aligned: {reason}."
        return out

    a, b = src.part, dst.part
    if a is None or b is None or min(a.n, b.n) < p.min_part_points:
        return done("one of the scans shows too little of the part besides its support "
                    f"({0 if a is None else a.n} and {0 if b is None else b.n} points; at least {p.min_part_points} each)",
                    "part")
    sp = pg.StraightenParams(fpfh_radius_mm=float(p.fpfh_radius_mm), fgr_max_corr_mm=float(p.feature_dist_mm))
    for c in (a, b):  # the features are kept on the cloud: made again when the radius is another
        if getattr(c, "fpfh_radius_mm", None) != float(p.fpfh_radius_mm):
            c.feat, c.fpfh_radius_mm = None, float(p.fpfh_radius_mm)
    starts = [out.T.copy()] if init is not None else _first_guesses(a, b, sp, p)
    if not starts:
        return done("no starting guess could be made from the shapes", "guess")
    # from the shape's guesses ICP is free in every direction: nothing of a feature guess is worth
    # keeping, nor of points picked by hand (a few millimetres and degrees off); a start from the
    # tags is better than the shape along the directions the surfaces pin weakly, and kept there
    keep = init is not None and init_from == "tags"
    rel = float(p.start_weak_rel) if keep else 1e-9
    kind = "guess" if init is None else out.start
    cands: list[tuple[np.ndarray, tuple, str]] = []
    for T0 in starts:
        T = pg._icp(a, b, T0, p.icp_part, rel, p.robust_min_mm)
        if np.all(np.isfinite(T)):
            cands.append((T, _score(a, b, T, p.match_mm), kind))
    if not cands:
        return done("the refinement failed from every starting guess", "guess")
    out.candidates = len(cands)
    Pa = a.fine.pts[:: max(1, len(a.fine.pts) // 3000)]  # enough to tell poses apart
    if init is None:
        chosen = max(cands, key=lambda c: _rank(c[1]))
    else:
        chosen = cands[0]
        if chosen[1][1] < p.min_overlap:  # the start fits badly: what do the shapes alone say?
            for T0 in _first_guesses(a, b, sp, p):
                T = pg._icp(a, b, T0, p.icp_part, 1e-9, p.robust_min_mm)
                if np.all(np.isfinite(T)):
                    cands.append((T, _score(a, b, T, p.match_mm), "guess"))
    # the chosen pose tried again turned round: a wrong pose that scores well is most often the
    # right one turned half round (or a quarter, about the thinnest direction), and the first
    # guesses can all fall into it together
    T_before_turns = chosen[0]
    if p.turn_starts and (chosen[1][0] > 0 or init is not None):
        for T0 in _turned_starts(a, b, chosen[0]):
            T = pg._icp(a, b, T0, p.icp_part, 1e-9, p.robust_min_mm)
            if np.all(np.isfinite(T)):
                cands.append((T, _score(a, b, T, p.match_mm), "turned"))
                out.turned += 1
        if init is None:
            chosen = max(cands, key=lambda c: _rank(c[1]))
    groups = _group(cands, Pa, p.agree_mm, chosen[0])
    T, best = chosen[0], chosen[1]
    first = groups[0]  # the chosen pose's group
    out.agree = sum(1 for k in first["members"] if cands[k][2] != "turned")
    # starts that reached the chosen pose independently: a turned start that came back to the pose it
    # was turned from says nothing new; turned starts that found a better pose elsewhere do
    found_by_turning = _apart_mm(Pa, T_before_turns, chosen[0]) > p.agree_mm
    out.reached = len(first["members"]) if found_by_turning else out.agree
    apart = [_apart_mm(Pa, c[0], T) for c in cands]
    out.spread_mm = round(max((d for d in apart if d <= p.rival_mm), default=0.0), 3)
    out.poses = [_pose_row(g, cands, Pa, T, k == 0, out.start) for k, g in enumerate(groups[:MAX_POSES])]
    out.T = T
    _, out.overlap, out.rmse_mm, out.fit_mm, out.n_matched = best
    other = groups[1] if len(groups) > 1 else None
    if out.start == "given" and other is not None and other["score"][0] * p.points_rival_share > best[0]:
        # points picked by hand are checked against the shapes: they decide between poses that fit
        # alike and are not overruled, but a pose that fits clearly better says the points are off.
        # (Not the tags: measured, they hold a sphere where it is, and a sphere turned round always
        # overlaps more of itself somewhere.)
        return done(f"not sure: {given} put the scan where the shapes fit "
                    + (f"{100 * best[0] / other['score'][0]:.0f} % as well as at another pose"
                       if best[0] > 0 else "nowhere, but they fit at another pose")
                    + f" {other['apart']:.0f} mm away: check {given} (two may be swapped), or choose that pose", "points")
    if best[0] <= 0:
        out.poses = []
        return done("the two scans do not overlap at any of the starting guesses", "overlap")
    if same_setup and src.support is not None and dst.support is not None:
        pivot = a.fine.pts.mean(axis=0) @ T[:3, :3].T + T[:3, 3]
        T2, turn, shift = _onto_support(T, src.support, dst.support, pivot)
        if turn <= p.support_max_deg and abs(shift) <= p.support_max_mm:
            T2 = _in_plane_icp(a, b, T2, src.support[0], p.in_plane, p.robust_min_mm,
                               weak_rel=float(p.start_weak_rel) if keep else None)
            s2 = _score(a, b, T2, p.match_mm)
            if np.all(np.isfinite(T2)) and s2[0] >= 0.9 * best[0]:  # the part still fits: keep what the support added
                out.T, out.used_support = T2, True
                _, out.overlap, out.rmse_mm, out.fit_mm, out.n_matched = s2
                out.poses[0]["T"] = np.asarray(T2).tolist()
        else:
            log.info("same setup asked for, but the two supports are %.1f deg and %.1f mm apart after the part's fit: "
                     "left out", turn, shift)
    use_all = out.used_support
    out.dof = _dof(src.both if use_all else a, dst.both if use_all else b, out.T, p.match_mm, p.weak_rel)
    if out.overlap < p.min_overlap:
        return done(f"too little of the two scans overlaps ({100 * out.overlap:.0f} % of the smaller scan's part within "
                    f"{p.match_mm:g} mm; at least {100 * p.min_overlap:.0f} %)", "overlap")
    if not out.fit_mm <= p.max_fit_mm:
        return done(f"the overlapping surfaces do not match closely (the residuals spread over {out.fit_mm:.2f} mm; "
                    f"at most {p.max_fit_mm:g} mm)", "fit")
    if out.start != "features":
        moved = _apart_mm(Pa, starts[0], chosen[0])  # a refinement that pulled the scan far from the start
        if moved > p.rival_mm:
            return done(f"not sure: the shapes pulled the scan {moved:.0f} mm from where {given} put it: check {given}",
                        "points")
    else:
        if other is not None and other["score"][0] >= p.rival_share * best[0]:
            turned = all(cands[k][2] == "turned" for k in other["members"])
            what = ("it fits about as well turned round" if turned else
                    "the part fits in more than one position about equally well")
            return done(f"not sure which way it goes: {what} ({100 * other['score'][0] / best[0]:.0f} % as well, "
                        f"{other['apart']:.0f} mm away; a symmetric shape, a repeated feature, or a surface that can slide "
                        "on itself): pick three or more matching points on both scans, or choose one of the poses",
                        "turned" if turned else "rival")
        if out.reached < 2:
            return done(f"not sure: only one of the {out.candidates + out.turned} starts led to this pose, which is too "
                        "little to trust it: pick three or more matching points on both scans, or scan with more overlap",
                        "one_guess")
    log.info("aligned a pair: overlap %.0f %%, %.2f mm RMS, %d of %d guesses agree (%d starts in all), %d directions "
             "pinned (%s)", 100 * out.overlap, out.rmse_mm, out.agree, out.candidates, out.reached, out.dof,
             "with the support" if out.used_support else "part only")
    return done()


MAX_POSES = 4  # poses kept in the result for the person to look at


def _first_guesses(a: Any, b: Any, sp: Any, p: AlignParams) -> list[np.ndarray]:
    """First guesses from the shape: FPFH features matched by FGR and by RANSAC with ``ransac_seeds`` seeds."""
    o3d = pg._open3d()
    out = []
    for method, seed in [("fgr", 0)] + [("ransac", k) for k in range(int(p.ransac_seeds))]:
        if hasattr(o3d.utility, "random"):
            o3d.utility.random.seed(int(seed))  # the same guesses on every run, as far as Open3D allows
        Tg = pg.global_match(a, b, sp, method, seed)
        if Tg is not None:
            out.append(Tg)
    return out


def _rank(s: tuple) -> tuple:
    """How a refined pose is ranked: by the share of matching surface, then by a tight fit."""
    return s[0], -s[3] if np.isfinite(s[3]) else -np.inf


def _turned_starts(a: Any, b: Any, T: np.ndarray) -> list[np.ndarray]:
    """``T`` turned half round about each principal axis of the source's part (through its centre,
    in its own world) and of the target's part (in the target's world), and a quarter round both
    ways about the source's thinnest direction. These are the poses a part that looks alike from
    two sides fits in, whatever the part: its axes are where its shape is balanced."""
    from ohs.geometry import make_se3, so3_exp  # noqa: PLC0415

    def axes(P):
        m = P.mean(axis=0)
        _, U = np.linalg.eigh((P - m).T @ (P - m))  # columns: smallest spread first
        return m, U

    out = []
    m, U = axes(a.fine.pts)
    for k in range(3):
        R = so3_exp(math.pi * U[:, k])
        out.append(T @ make_se3(R, m - R @ m))
    for ang in (0.5 * math.pi, -0.5 * math.pi):
        R = so3_exp(ang * U[:, 0])
        out.append(T @ make_se3(R, m - R @ m))
    m2, U2 = axes(b.fine.pts)
    c = T[:3, :3] @ m + T[:3, 3]  # the source's centre where T puts it
    for k in range(3):
        R = so3_exp(math.pi * U2[:, k])
        out.append(make_se3(R, c - R @ c) @ T)
    del m2
    return out


def _group(cands: list, P: np.ndarray, agree_mm: float, T_first: np.ndarray) -> list[dict]:
    """The refined poses gathered into distinct poses (within ``agree_mm`` RMS over ``P`` of each
    other's best), the one at ``T_first`` first, the others by their score."""
    order = sorted(range(len(cands)), key=lambda k: _rank(cands[k][1]), reverse=True)
    groups: list[dict] = [{"T": T_first, "score": None, "members": [], "apart": 0.0}]
    for k in order:
        T, s, _ = cands[k]
        for g in groups:
            if _apart_mm(P, T, g["T"]) <= agree_mm:
                g["members"].append(k)
                if g["score"] is None:
                    g["score"] = s
                break
        else:
            groups.append({"T": T, "score": s, "members": [k], "apart": _apart_mm(P, T, T_first)})
    first, rest = groups[0], sorted(groups[1:], key=lambda g: _rank(g["score"]), reverse=True)
    return [first] + rest


def _pose_row(g: dict, cands: list, P: np.ndarray, T_first: np.ndarray, chosen: bool, start: str) -> dict:
    """One pose for the result (module docstring, 5.)."""
    s = g["score"]
    kinds = {cands[k][2] for k in g["members"]}
    how = {"guess": "the first guesses", "turned": "turned round", "given": "your points", "tags": "the tags"}
    return {"T": np.asarray(g["T"]).tolist(), "chosen": bool(chosen), "pick": round(float(s[0]), 4),
            "overlap": round(float(s[1]), 4), "rmse_mm": None if not np.isfinite(s[2]) else round(float(s[2]), 3),
            "fit_mm": None if not np.isfinite(s[3]) else round(float(s[3]), 3), "reached": len(g["members"]),
            "from": [how[k] for k in ("given", "tags", "guess", "turned") if k in kinds],
            "apart_mm": round(float(g["apart"]), 2)}
