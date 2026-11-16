"""Aligning several scans of one part at once (research plan 4.7.3).

Every pair of scans is aligned (:func:`ohs.align.pairwise.align_pair`). The accepted pairs are
links between scans; the first scan is the reference and stays where it is. Each other scan is
placed through the chain of best links that reaches it (a maximum spanning tree by overlap), so
two scans that share too little to be aligned to each other (opposite sides of a part) can both
be placed through a third that overlaps each. Where the accepted links form a loop, all of them
are solved together in a pose graph (Open3D's global optimisation, the machinery Straighten
uses), and a link that does not fit the others is left out.

The result gives each scan's pose in the reference scan's world (``None`` for a scan that no
accepted link reaches), every pair's outcome, and a sentence for the user.

Points picked on two scans (``picked``) give that pair a rough start, as for one pair.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import numpy as np

from ohs.align.pairwise import AlignParams, ScanCloud, _apart_mm, align_pair, pose_from_point_pairs
from ohs.geometry import invert_se3

__all__ = ["ManyAlignment", "align_many"]

log = logging.getLogger(__name__)

LINK_CONFLICT_MM = 2.0  # a loop's links that disagree by more than this (RMS over the part) are a conflict


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

    @property
    def accepted(self) -> bool:
        return not self.unplaced

    def as_dict(self) -> dict:
        return {"accepted": self.accepted, "T": [None if T is None else np.asarray(T).tolist() for T in self.T],
                "pairs": self.pairs, "placed": self.placed, "unplaced": self.unplaced, "loops": self.loops,
                "graph": self.graph, "worst_link_mm": round(float(self.worst_link_mm), 3), "ms": self.ms,
                "verdict": self.verdict}


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


def align_many(clouds: Sequence[ScanCloud], params: AlignParams | None = None, *, same_setup: bool = True,
               picked: dict[tuple[int, int], tuple[Any, Any]] | None = None,
               progress: Callable[[float, str], None] | None = None) -> ManyAlignment:
    """Place all ``clouds`` in the world of ``clouds[0]`` (module docstring). ``picked[(i, j)]``:
    points picked on scan ``j`` and the matching points on scan ``i`` (``(points_j, points_i)``),
    a rough start for that pair. ``ValueError`` for fewer than two scans or picked points that do
    not give a pose."""
    p = params if params is not None else AlignParams()
    t0 = time.perf_counter()
    n = len(clouds)
    if n < 2:
        raise ValueError("at least two scans are needed")
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
    rows: dict[tuple[int, int], dict] = {}
    for k, (i, j) in enumerate(pair_list):
        say(0.85 * k / len(pair_list), f"Aligning scan {j + 1} to scan {i + 1}")
        res = align_pair(clouds[j], clouds[i], p, init=starts.get((i, j)), same_setup=same_setup)
        row = {"i": i, "j": j, **{key: v for key, v in res.as_dict().items() if key != "T"}, "used": ""}
        rows[(i, j)] = row
        if res.accepted:
            links[(i, j)] = res.T
            weight[(i, j)] = float(res.overlap)
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
        info = {key: _information(clouds[key[1]], clouds[key[0]], links[key], 2.0 * p.match_mm) for key in links}
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
        out.verdict = (f"Not all placed: scan{'s' if len(out.unplaced) > 1 else ''} {names} could not be aligned to any "
                       "placed scan. Pick matching points between each of them and a placed scan, or scan with more overlap.")
    else:
        via = "" if out.loops == 0 else (f"; {out.loops} more link{'s' if out.loops > 1 else ''} checked the chain"
                                         + (f", the worst disagrees by {worst:.2f} mm" if worst > 0 else ""))
        out.verdict = f"All {n} scans placed through {len(tree)} link{'s' if len(tree) > 1 else ''}{via}."
        if worst > LINK_CONFLICT_MM:
            out.verdict += " That is a lot: look at the scans together before merging."
    say(1.0, "done")
    return out
