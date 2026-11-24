"""Aligning several scans of one part at once (research plan 4.7.3).

Every pair of scans is aligned (:func:`ohs.align.pairwise.align_pair`). The accepted pairs are
links between scans; the first scan is the reference and stays where it is. Each other scan is
placed through the chain of best links that reaches it (a maximum spanning tree by overlap), so
two scans that share too little to be aligned to each other (opposite sides of a part) can both
be placed through a third that overlaps each. Where the accepted links form a loop, all of them
are solved together in a pose graph (Open3D's global optimisation, the machinery Straighten
uses), and a link that does not fit the others is left out.

The result gives each scan's pose in the reference scan's world (``None`` for a scan that no
accepted link reaches), every pair's outcome, and a sentence for the user. Only pairs the engine is
sure of are links (MAINTENANCE_PLAN F11): a pair it is not sure of carries its doubt and the poses
it had in mind, and where that leaves a scan unplaced the sentence says so.

Points picked on two scans (``picked``) give that pair a rough start, as for one pair; so do the
tags two scans both saw (``tags``, :mod:`ohs.align.tags`), where no points were picked.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import numpy as np

from ohs.align.pairwise import (AlignParams, ScanCloud, _apart_mm, align_pair, little_shared_merge_warning,
                                pose_from_point_pairs, shared_overlap, what_helps)
from ohs.align.tags import TagMap, align_with_tags
from ohs.geometry import invert_se3

__all__ = ["ManyAlignment", "align_many", "shared_at_poses"]

log = logging.getLogger(__name__)

LINK_CONFLICT_MM = 2.0  # a loop's links that disagree by more than this (RMS over the part) are a conflict
UNSURE = ("rival", "turned", "one_guess", "points")  # a pair's doubts where the engine had poses in mind


@dataclass
class ManyAlignment:
    """What :func:`align_many` found (module docstring). ``T[k]`` takes points of scan ``k``'s mesh
    into the reference scan's world (``None``: not placed)."""

    T: list[np.ndarray | None]
    pairs: list[dict] = field(default_factory=list)  # per pair: i, j, accepted, verdict, overlap, ..., used ("chain" / "loop" / "left out" / "")
    placed: list[int] = field(default_factory=list)
    unplaced: list[int] = field(default_factory=list)
    loops: int = 0  # accepted links beyond the chain
    graph: bool = False  # the loops were solved in a pose graph
    worst_link_mm: float = 0.0  # after solving, how far the worst kept link disagrees with the poses (RMS over its part)
    ms: float = 0.0
    verdict: str = ""
    # links that place scans although the two share little (pairwise module docstring, 7.): (i, j) of the pairs
    little_shared: list[tuple[int, int]] = field(default_factory=list)
    caution: str | None = None  # what that means and what helps, in words

    @property
    def accepted(self) -> bool:
        return not self.unplaced

    def as_dict(self) -> dict:
        return {"accepted": self.accepted, "T": [None if T is None else np.asarray(T).tolist() for T in self.T],
                "pairs": self.pairs, "placed": self.placed, "unplaced": self.unplaced, "loops": self.loops,
                "graph": self.graph, "worst_link_mm": round(float(self.worst_link_mm), 3), "ms": self.ms,
                "verdict": self.verdict, "little_shared": [list(k) for k in self.little_shared], "caution": self.caution}


def _spanning_tree(n: int, edges: dict[tuple[int, int], float]) -> list[tuple[int, int]]:
    """Edges (placed, new) of a maximum spanning tree grown from node 0 (Prim), by weight."""
    placed = {0}
    out: list[tuple[int, int]] = []
    while True:
        best = None
        for (i, j), w in edges.items():
            for a, b in ((i, j), (j, i)):
                if a in placed and b not in placed and (best is None or w > best[0]):
                    best = (w, a, b)
        if best is None:
            return out
        placed.add(best[2])
        out.append((best[1], best[2]))


def _pose_graph(T: list[np.ndarray | None], links: dict[tuple[int, int], np.ndarray], info: dict[tuple[int, int], np.ndarray],
                tree: set[tuple[int, int]]) -> tuple[list[np.ndarray | None], set[tuple[int, int]]]:
    """Open3D's global optimisation over the placed scans: tree links certain, the others may be
    pruned. Returns the poses and the links that were kept."""
    import open3d as o3d  # noqa: PLC0415

    reg = o3d.pipelines.registration
    ids = [k for k, X in enumerate(T) if X is not None]
    node = {k: m for m, k in enumerate(ids)}
    g = reg.PoseGraph()
    for k in ids:
        g.nodes.append(reg.PoseGraphNode(np.asarray(T[k], dtype=np.float64)))
    order = []
    for (i, j), Tij in links.items():  # Tij: scan j into scan i: an edge from j (source) to i (target)
        if i in node and j in node:
            certain = (i, j) in tree or (j, i) in tree
            g.edges.append(reg.PoseGraphEdge(node[j], node[i], Tij, info[(i, j)], uncertain=not certain))
            order.append((i, j))
    option = reg.GlobalOptimizationOption(max_correspondence_distance=2.0, edge_prune_threshold=0.25,
                                          preference_loop_closure=2.0, reference_node=0)
    with o3d.utility.VerbosityContextManager(o3d.utility.VerbosityLevel.Error):
        reg.global_optimization(g, reg.GlobalOptimizationLevenbergMarquardt(), reg.GlobalOptimizationConvergenceCriteria(), option)
    out: list[np.ndarray | None] = list(T)
    ref = np.asarray(T[ids[0]]) @ invert_se3(np.asarray(g.nodes[0].pose))  # keep the reference exactly where it was
    for m, k in enumerate(ids):
        out[k] = ref @ np.asarray(g.nodes[m].pose, dtype=np.float64)
    out[ids[0]] = np.asarray(T[ids[0]], dtype=np.float64)
    kept = set()
    for e, key in zip(g.edges, order):  # the pruned graph keeps edges in order, with a confidence
        if not e.uncertain or e.confidence >= 0.25:
            kept.add(key)
    return out, kept


def _information(src: ScanCloud, dst: ScanCloud, Tij: np.ndarray, dist: float) -> np.ndarray:
    """Open3D's information matrix of a link (its correspondences), as Straighten builds it."""
    import open3d as o3d  # noqa: PLC0415

    def pc(c: Any) -> Any:
        p = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(c.fine.pts))
        p.normals = o3d.utility.Vector3dVector(c.fine.nrm)
        return p

    reg = o3d.pipelines.registration
    return np.asarray(reg.get_information_matrix_from_point_clouds(pc(dst.part), pc(src.part), dist, invert_se3(Tij)))


def _tag_information(tags_src: TagMap, tags_dst: TagMap, ids: list[int], rms_mm: float, Tij: np.ndarray) -> np.ndarray:
    """A held link's information (ENGINE_PLAN X19): Open3D's information of the used tags' corners, built as
    :func:`_information` builds a shape link's (the target's points against the source's at the inverse of the link,
    so both are in the same frame), each corner weighted by one over the square of the tags' RMS scatter (at least
    0.05 mm), where a surface point counts one."""
    import open3d as o3d  # noqa: PLC0415

    S = np.concatenate([np.asarray(tags_src.corners[k], dtype=np.float64) for k in ids])
    D = np.concatenate([np.asarray(tags_dst.corners[k], dtype=np.float64) for k in ids])
    reg = o3d.pipelines.registration
    pd, ps = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(D)), o3d.geometry.PointCloud(o3d.utility.Vector3dVector(S))
    info = np.asarray(reg.get_information_matrix_from_point_clouds(pd, ps, 5.0 * max(rms_mm, 0.05) + 1.0, invert_se3(Tij)))
    return info / max(rms_mm, 0.05) ** 2


def align_many(clouds: Sequence[ScanCloud], params: AlignParams | None = None, *, same_setup: bool = True,
               picked: dict[tuple[int, int], tuple[Any, Any]] | None = None,
               tags: Sequence[TagMap | None] | None = None,
               progress: Callable[[float, str], None] | None = None) -> ManyAlignment:
    """Place all ``clouds`` in the world of ``clouds[0]`` (module docstring). ``picked[(i, j)]``:
    points picked on scan ``j`` and the matching points on scan ``i`` (``(points_j, points_i)``),
    a rough start for that pair. ``tags[k]``: the tags scan ``k`` saw (:func:`ohs.align.tags.tag_map`;
    ``None``: none known): a pair without picked points that shares tags starts from them, and
    if that start does not lead to an accepted alignment, from the shape's guesses as without
    tags (the pair's ``tags`` entry says what happened). ``ValueError`` for fewer than two scans,
    picked points that do not give a pose, or a ``tags`` list of another length."""
    p = params if params is not None else AlignParams()
    t0 = time.perf_counter()
    n = len(clouds)
    if n < 2:
        raise ValueError("at least two scans are needed")
    if tags is not None and len(tags) != n:
        raise ValueError(f"tags: {len(tags)} maps for {n} scans")
    say = progress or (lambda f, m: None)
    starts: dict[tuple[int, int], np.ndarray] = {}
    for (i, j), (pj, pi) in (picked or {}).items():
        a, b = (i, j) if i < j else (j, i)
        Tij, _ = pose_from_point_pairs(pj, pi)  # scan j into scan i
        starts[(a, b)] = Tij if (a, b) == (i, j) else invert_se3(Tij)
    # 1. every pair
    pair_list = [(i, j) for i in range(n) for j in range(i + 1, n)]
    links: dict[tuple[int, int], np.ndarray] = {}
    weight: dict[tuple[int, int], float] = {}
    held: dict[tuple[int, int], tuple] = {}  # links the tags hold: (tags of j, tags of i, the tags used, their RMS)
    rows: dict[tuple[int, int], dict] = {}
    for k, (i, j) in enumerate(pair_list):
        say(0.85 * k / len(pair_list), f"Aligning scan {j + 1} to scan {i + 1}")
        tag_row = None
        if (i, j) not in starts and tags is not None:
            res, tag_row = align_with_tags(clouds[j], clouds[i], tags[j], tags[i], p, same_setup=same_setup)
        else:
            res = align_pair(clouds[j], clouds[i], p, init=starts.get((i, j)), same_setup=same_setup)
        row = {"i": i, "j": j, **{key: v for key, v in res.as_dict().items() if key != "T"}, "used": ""}
        if tag_row is not None:
            row["tags"] = tag_row
        rows[(i, j)] = row
        if p.hold_tags and res.tag_hold is not None:  # the tag pose held (pairwise module docstring, 8.; ENGINE_PLAN X19)
            row["link_weight"] = "tags" if res.tag_hold["held_by_tags"] else "overlap"
        if res.accepted:
            links[(i, j)] = res.T
            weight[(i, j)] = float(res.overlap)
            if p.hold_tags and res.tag_hold is not None and res.tag_hold["held_by_tags"]:
                # a held link places its scan by the tags: first in the chain (above any overlap), and in a loop its
                # information is the tags' corners at their own scatter (below), not the little the shapes share
                weight[(i, j)] = 1.0 + 1.0 / (1.0 + float(res.tag_hold["tag_pose_sigma_mm"] or 0.0))
                held[(i, j)] = (tags[j], tags[i], list(res.tag_hold["tags_used"]), float(res.tag_hold["tag_rms_mm"] or 0.0))
    # 2. the chain of best links from the reference
    tree = _spanning_tree(n, weight)
    T: list[np.ndarray | None] = [None] * n
    T[0] = np.eye(4)
    for a, b in tree:  # a placed, b new
        if (a, b) in links:  # scan b into scan a
            T[b] = T[a] @ links[(a, b)]
        else:
            T[b] = T[a] @ invert_se3(links[(b, a)])
        rows[(min(a, b), max(a, b))]["used"] = "chain"
    tree_set = {(min(a, b), max(a, b)) for a, b in tree}
    out = ManyAlignment(T=T)
    out.placed = [k for k in range(n) if T[k] is not None]
    out.unplaced = [k for k in range(n) if T[k] is None]
    extra = [key for key in links if key not in tree_set and T[key[0]] is not None and T[key[1]] is not None]
    out.loops = len(extra)
    # 3. loops: solve all links together, leave out what does not fit
    if extra:
        say(0.9, "Fitting all links together")
        info = {key: (_tag_information(*held[key], links[key]) if key in held else
                      _information(clouds[key[1]], clouds[key[0]], links[key], 2.0 * p.match_mm)) for key in links}
        try:
            T2, kept = _pose_graph(T, links, info, tree_set)
            if all(X is None or np.all(np.isfinite(X)) for X in T2):
                out.T, out.graph = T2, True
                for key in extra:
                    rows[key]["used"] = "loop" if key in kept else "left out"
        except Exception as e:  # pragma: no cover - Open3D refuses a degenerate graph
            log.warning("the pose graph over the scans failed (%s): keeping the chain", e)
    for key in extra if not out.graph else ():
        rows[key]["used"] = "loop"
    worst = 0.0
    for key in links:
        if rows[key]["used"] in ("chain", "loop") and out.T[key[0]] is not None and out.T[key[1]] is not None:
            i, j = key  # the link says T_j = T_i @ links: how far apart do the two put scan j's part?
            d = _apart_mm(clouds[j].part.fine.pts, out.T[i] @ links[key], out.T[j])
            rows[key]["disagree_mm"] = round(d, 3)
            worst = max(worst, d)
    out.worst_link_mm = worst
    out.pairs = [rows[key] for key in pair_list]
    out.ms = round(1e3 * (time.perf_counter() - t0), 1)
    if out.unplaced:
        names = ", ".join(str(k + 1) for k in out.unplaced)
        # where the engine had a pose but was not sure of it, say so: picking points (or choosing one
        # of its poses) settles it, where more scanning would not
        unsure = [f"scan {r['j'] + 1} with scan {r['i'] + 1}" for r in rows.values()
                  if not r["accepted"] and r.get("poses") and (r["i"] in out.unplaced or r["j"] in out.unplaced)
                  and r.get("doubt") in UNSURE]
        out.verdict = (f"Not all placed: scan{'s' if len(out.unplaced) > 1 else ''} {names} could not be aligned to any "
                       "placed scan" + (f" (not sure of {', '.join(unsure)}: the shapes fit more than one way)" if unsure else "")
                       + ". Pick matching points between each of them and a placed scan, or scan with more overlap.")
    else:
        via = "" if out.loops == 0 else (f"; {out.loops} more link{'s' if out.loops > 1 else ''} checked the chain"
                                         + (f", the worst disagrees by {worst:.2f} mm" if worst > 0 else ""))
        out.verdict = f"All {n} scans placed through {len(tree)} link{'s' if len(tree) > 1 else ''}{via}."
        if worst > LINK_CONFLICT_MM:
            out.verdict += " That is a lot: look at the scans together before merging."
    # links that place a scan although the two share little: named in the verdict, the caution says what it means
    out.little_shared = [key for key in pair_list if rows[key]["used"] in ("chain", "loop") and rows[key].get("little_shared")]
    if out.little_shared:
        out.verdict += (" Little is shared between " + ", and between ".join(
            f"scan {j + 1} and scan {i + 1} ({100 * rows[(i, j)]['overlap']:.0f} %)" for i, j in out.little_shared) + ".")
        out.caution = ("Where two scans share little, they fit where they meet, but away from there the position can be a "
                       "millimetre or more off, and a merge can then come out worse than either scan (a wall seen from both "
                       "sides too thick or too thin). Merging is still possible. To make the positions sure, "
                       f"{what_helps('align them all again')}.")
    say(1.0, "done")
    return out


def shared_at_poses(clouds: Sequence[ScanCloud], T: Sequence[np.ndarray], params: AlignParams | None = None,
                    names: Sequence[str] | None = None) -> dict:
    """How much scans share at the poses ``T`` (each scan's mesh into one world), as a merge is given them: every
    pair's ``overlap`` (:func:`ohs.align.pairwise.shared_overlap`), the chain of best-shared pairs that holds all
    the scans together (a maximum spanning tree by overlap, as :func:`align_many` places scans), and the pairs of
    that chain that share less than ``caution_overlap``, with a warning in words for each (``names``: the scans'
    names; "scan 1", ... without). Two scans that share little can each be well placed through a third, so only
    the chain counts. ``{"pairs": [{i, j, overlap}], "chain": [[i, j]], "little_shared": [{i, j, overlap}],
    "limit", "warnings"}``."""
    p = params if params is not None else AlignParams()
    n = len(clouds)
    if len(T) != n:
        raise ValueError(f"{len(T)} poses for {n} scans")
    names = list(names) if names is not None else [f"scan {k + 1}" for k in range(n)]
    over: dict[tuple[int, int], float] = {}
    for i in range(n):
        for j in range(i + 1, n):
            Tij = invert_se3(np.asarray(T[i], dtype=np.float64)) @ np.asarray(T[j], dtype=np.float64)  # scan j into scan i
            over[(i, j)] = shared_overlap(clouds[j], clouds[i], Tij, p)
    chain = sorted((min(a, b), max(a, b)) for a, b in _spanning_tree(n, over))
    weak = [key for key in chain if over[key] < p.caution_overlap]
    then = "merge all three" if n == 2 else "merge them all again"
    return {"pairs": [{"i": i, "j": j, "overlap": round(v, 4)} for (i, j), v in over.items()],
            "chain": [list(k) for k in chain],
            "little_shared": [{"i": i, "j": j, "overlap": round(over[(i, j)], 4)} for i, j in weak],
            "limit": float(p.caution_overlap),
            "warnings": [little_shared_merge_warning(names[i], names[j], over[(i, j)], p.match_mm, then) for i, j in weak]}
