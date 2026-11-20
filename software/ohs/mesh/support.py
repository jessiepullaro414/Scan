"""Finding the surface a part stands on, and removing it ("Remove the table", FEATURE_PLAN X1).

A scan of a part on a table holds the table too, most of its area. Merging scans leaves each
scan's table out (:mod:`ohs.align.merge`); a single scan keeps it, and this takes it away as a
mesh edit: :func:`find_support` says which faces are the table (or refuses, in words) and
:func:`remove_support` removes them.

1. **The plane** is found the alignment's way (:func:`ohs.align.pairwise.scan_cloud`): the
   measured vertices (a vertex with sigma :data:`~ohs.mesh.confidence.SIGMA_NONE_MM`, made up by
   hole filling, is left out) thinned to one point per 1 mm and per 4 mm, and a RANSAC plane
   through the 4 mm points, its points within 1.5 mm; its normal is turned towards the side it was
   seen from, where the part stands. The first plane is the alignment's support plane. It must
   hold ``min_share`` (15 %) of the points (the alignment asks 30 %: a box's top larger than the
   table seen round it leaves the table less). Then the guards of the comparison with a reference
   (:meth:`ohs.inspect.reference.ScanSurface.part_cloud`), without a reference to measure against:

   * something stands on it (at least ``min_part_points`` points in front of it);
   * no more of the scan lies behind it than in front (a part's face has the part behind it),
     counting behind only what lies more than twice the plane's 1.5 mm behind it: the Open3D mesh
     of the 10-frame test scan (CPU path) has more points just outside the band behind its table
     (noise, a slight warp) than the sphere has in front, and was refused before;
   * the part does not go on behind it next to what stands on it: points more than twice the
     plane's 1.5 mm behind it, within the reach of what stands in front (seen along the normal:
     the larger of 10 mm and its own radius, 95th percentile), are fewer than a tenth of the points
     in front. A plate's top with a block on it has the plate's sides there; a table has nothing
     under it (its fringe, where a scan grows one, lies at the scan's border, away from the part).

   A plane that fails is set aside and the next largest is tried (``candidates``, 3): the box's
   top is the largest plane, nothing stands on it, and the table is the next. When none passes,
   :class:`SupportRefused` says why for the largest, in words the user can act on. The plane is
   then fitted again to the mesh: started at the most common height of the faces within its band
   and 30° of its normal, then area-weighted least squares over those within 0.25 mm, four times,
   the band narrowed to three times their robust spread each time. The RMS within it is the table's
   flatness; the *noise band* is four times that (0.3 mm to 1.5 mm).

2. **The faces.** A slab (everything within a height of the plane) would cut the part's walls;
   a mesh edit can tell a wall from the table by which way it faces. A face is the table's when

   * it lies behind the plane: all three corners more than the band behind it ("behind the plane
     everything goes": a table's fringe and underside); or
   * it faces the table's way, its normal within ``facing_deg`` (45°) of the plane's, its centre at
     most ``margin_mm`` above the plane (never less than the band) and it is joined, through faces
     that are the same, to faces of the plane itself (within the band; at least
     ``min_patch_mm2`` of them: a stray noisy face on a wall is not the table).

   Faces turned further from the plane's normal are the part's walls and stay at any height, so
   a wall is kept right down to the table. Where the part meets the table the scan's surface is a
   rounded blend: its lower half (turned less than 45°) goes with the table, its upper half stays,
   so on a vertical wall the part is kept from about 0.3 times the blend's radius up. The cost is on
   a surface that meets the table at a slope gentler than 45° (a low dome, a chamfer): there the
   part's bottom ``margin_mm`` goes.

2b. **Thin parts lying flat** (a shim, a gasket, a sheet, a sticker) face the table's way, lie
   within the margin and are joined to the table through the gentle slope a scan makes of their
   edge: by the rule above they would go. A foot blend rises from the table into a wall; a thin
   part is a plateau. So the faces of the table's candidates that lie more than ``thin_find_k``
   (2.5) times the table's RMS above the plane are joined into raised areas; one of at least
   ``thin_min_mm2`` (25 mm²; the table's own noise makes patches up to 13 mm²) is looked at when it
   does not touch surface rising above the margin (a thick part's foot), and when it is steady: at
   least ``thin_steady`` (40 %) of its area lies at its level (its upper quartile height, within
   twice the RMS or 15 %), not spread up a slope.

   It is judged against **the table round it**, not the scan's sigma: the candidate faces 3 to
   10 mm from it (``ring_mm``, past its smeared rim) give the level it stands on (their median) and
   the scatter it is told from (1.4826 times their median distance from it, at least half the
   table's RMS). Its *rise* above that level must be 2.5 times that scatter to be listed at all.
   It is **kept** from 4 times the scatter (``thin_keep_k``) when its top is parallel to the table
   (within ``thin_tilt_deg``, 0.25°), it is at least ``thin_keep_size_mm`` (20 mm) across and the
   table round it is flat (scatter at most ``flat_table_mm``, 0.1 mm, or 1.5 times the RMS).
   Otherwise it goes with the table and is **warned** about: drawn, listed (where, how large, how
   high) and the user may keep it ("Keep this": ``keep`` points). Two exceptions keep the far edges
   quiet: where the table round it scatters more than that, or where two other raised areas lie
   within ``crowd_mm`` (80 mm), the area is the table's own **unevenness**: it goes, and one warning
   for all of them says how thin a part cannot be told from the table there. A clear plateau (kept)
   stands out even among them. The scan's sigma was tried for this and dropped: on the simulated
   bracket's front view the table is flat to 0.07 mm 75 mm from the part while its sigma there is
   0.15-0.2 mm, and judged by the sigma a 0.3 mm disc 40 mm across went without a word.

   Measured on the four real-size tables (the simulated bracket's front, side and back views and the
   ball scan; discs 10-100 mm across raised by 0.1-0.8 mm with a 1.5 mm smeared edge, near the
   part, 75 mm out and near the scan's border): the bare tables give 0-2 listed areas and 9-71 said
   once as unevenness, none kept. The finding carries the scan's own limit, ``thin_limit_mm`` (2.5
   times the RMS: thinner cannot be told at all) and ``thin_keep_mm`` (4 times). To find the table's
   level under a large thin part (a 100 mm sheet can be a third of the plane's band), the plane is
   refitted from the most common height and its band narrows on a robust spread. When nothing
   stands on the largest plane and nothing lies behind it, it is the table if a thin part (or
   anything above the margin) is found on it, else refused; the refusal names a raised area it
   could not tell.

3. **What is left** is the part. Pieces of it (joined by edges) that lie wholly within the margin
   plus the band above the plane are crumbs of table the rule did not reach (noisy faces at the
   scan's border, folds) and go too, unless they hold a thin part kept; the largest piece always stays. Any other loose piece (a bit
   of the part, something else standing on the table) is left for Remove floaters, and the result
   says how many.

The result (:class:`SupportFinding`) says what was found and done: the plane, its share of the
scan, how much lies in front of and behind it, its flatness, the faces removed and their area,
the lowest height of what is left above the plane, the raised areas (thin parts kept, or said),
and warnings.

4. **For a merge of several scans** of a part that was moved or turned over (:mod:`ohs.align.merge`),
   each scan's table lies somewhere else relative to the part and must stay out of the merged model.
   The merge fuses raw frames, not meshes, so the finding is turned into a map on the table's plane
   (:func:`support_map`, :class:`SupportMap`; cells of ``cell_mm``, 1 mm), place by place:

   * *the part stands here* (``core``): a face that is left (the part, a thin part kept, a loose bit)
     reaches down to within ``reach_mm`` (3 mm) of the plane; and the raised areas that are only
     warned about (they may be thin parts: in a merge they are kept, since a patch of table left is
     better than a part lost). ``stands`` is that grown by ``spread_mm`` (2 mm), for a single frame's
     scatter across the part's edge;
   * *the table was found here* (``table``): a face the finding removes;
   * *a thin part* (``thin``): the raised areas kept or warned about.

   **A frame** (its depth and its laser points, :meth:`SupportMap.in_camera`) keeps a point when it
   lies more than ``clip_mm`` (2 mm, the old slab's margin, for a single frame's noise) above the
   plane, or, where the part stands, anywhere up from ``behind_mm`` (1 mm) behind it. So the table
   goes, a thin part lying flat is kept whatever its thickness, and a part's foot right down to the
   table; what is left of the table is a ring at the part's foot, as wide as ``spread_mm``.

   **The merged mesh** then loses what is left of the table (:func:`table_left`) by the rule of 2.
   and 3. above with this scan's plane, where this scan found its table and not on a thin part: a
   face that faces the table's way (within 45°), lies at most the margin (1 mm) above the plane and
   is joined to faces on the plane itself; and loose pieces wholly within the margin and the band of
   the plane, over this scan's table and with nothing of a thin part, besides the largest piece.
   Laser points go by the same cells (:func:`laser_points_on_table`): within the margin of the plane,
   where the scan found table and the part does not stand.

   Raised areas the single-scan edit only warns about are kept in a merge (said in the map's
   warnings); those where the table itself is uneven go with the table, said once; a part thinner
   than the scan's own limit (``thin_limit_mm``) cannot be told from the table and goes with it.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from ohs.mesh.confidence import SIGMA_NONE_MM
from ohs.mesh.topology import as_mesh, compact, edge_table, face_normals, rows, vertex_normals

__all__ = ["MARGIN_MM", "FACING_DEG", "CLIP_MM", "SupportParams", "SupportFinding", "SupportRefused", "SupportMap",
           "TableLeft", "find_support", "remove_support", "support_map", "table_left", "laser_points_on_table"]

#: how far above the plane a face that faces the table's way still counts as the table (mm). Measured
#: on the simulated bracket seen from three sides (judged by its true shape): at 0.5 mm 180-460 mm² of
#: table was left near the part, at 1 mm 0.5-3.3 mm², and more margin took no more table but more of
#: a sloped part; the part lost 17-19 mm² of its rounded foot, all within 1 mm of the table
MARGIN_MM = 1.0
#: a face turned less than this from the plane's normal faces the table's way
FACING_DEG = 45.0
_REFINE_DEG = 30.0  # faces fitted to the plane: within this of its normal
_REACH_MIN_MM = 10.0
#: a merge (module docstring, 4.): a single frame's depth and laser points on bare table are left out up to
#: this far above the plane (the old slab's margin: a frame scatters several times more than a fused mesh)
CLIP_MM = 2.0
_MAP_CELL_MM = 1.0  # the map's cells on the plane
_MAP_REACH_MM = 3.0  # the part stands where a face that is left reaches this close to the plane
_MAP_SPREAD_MM = 2.0  # ... grown by this, for a frame's scatter across the part's edge
_MAP_BEHIND_MM = 1.0  # where the part stands, a frame keeps what lies up to this far behind the plane
_MAP_MAX_CELLS = 16_000_000


class SupportRefused(ValueError):
    """No surface the part stands on could be told apart: the message says why, in words."""


@dataclass(frozen=True)
class SupportParams:
    """Settings of :func:`find_support` (module docstring)."""

    margin_mm: float = MARGIN_MM
    facing_deg: float = FACING_DEG
    min_share: float = 0.15  # of the scan's 4 mm points on the plane
    dist_mm: float = 1.5  # points this close to the plane are on it (the alignment's support_dist_mm)
    voxel_mm: float = 1.0  # the alignment's clouds
    coarse_voxel_mm: float = 4.0
    candidates: int = 3  # planes tried, largest first
    min_part_points: int = 30  # 4 mm points in front of the plane: something stands on it
    behind_share: float = 0.1  # of the points in front: the part going on behind it next to them
    band_k: float = 4.0  # the noise band: this many times the plane's RMS ...
    band_min_mm: float = 0.3  # ... at least this ...
    min_patch_mm2: float = 10.0  # faces of the plane itself joined to a region of the table
    # thin parts lying flat (module docstring, 2b.)
    thin_find_k: float = 2.5  # a raised area: faces this many times the table's RMS above the plane ...
    thin_min_mm2: float = 25.0  # ... joined over at least this area (the table's own noise: up to 13 mm²)
    thin_steady: float = 0.4  # at least this share of its area at its own level (a plateau, not a slope)
    thin_keep_k: float = 4.0  # kept as a part from this many times the scatter of the table round it; below, warned
    ring_mm: tuple[float, float] = (3.0, 10.0)  # the table round a raised area: this far from it (past its smeared rim)
    flat_table_mm: float = 0.1  # a table round it scattered more than this (or 1.5 x the table's RMS) is uneven there
    thin_tilt_deg: float = 0.25  # kept only with a top this parallel to the table ...
    thin_keep_size_mm: float = 20.0  # ... and at least this large across; smaller or tilted: warned
    crowd_mm: float = 80.0  # with crowd_n other raised areas within this, the table there is uneven
    crowd_n: int = 2
    keep_reach_mm: float = 3.0  # "Keep this": a point this close to a raised area's face keeps that area

    def __post_init__(self) -> None:
        m = float(self.margin_mm)
        if not (math.isfinite(m) and 0.0 <= m <= 20.0):
            raise ValueError(f"margin_mm must be from 0 to 20 mm, got {self.margin_mm!r}")
        for name in ("dist_mm", "voxel_mm", "coarse_voxel_mm", "band_k", "band_min_mm"):
            v = float(getattr(self, name))
            if not (math.isfinite(v) and v > 0):
                raise ValueError(f"{name} must be a positive number, got {getattr(self, name)!r}")
        if not 0.0 < float(self.facing_deg) < 90.0:
            raise ValueError(f"facing_deg must be between 0 and 90, got {self.facing_deg!r}")
        if not 0.0 < float(self.min_share) <= 1.0:
            raise ValueError(f"min_share must be above 0 and at most 1, got {self.min_share!r}")
        if int(self.candidates) < 1:
            raise ValueError("candidates must be at least 1")


@dataclass
class SupportFinding:
    """What :func:`find_support` found (module docstring). ``faces`` / ``kept``: the indices of
    the faces removed / kept, sorted; heights are along ``normal`` from the plane
    ``normal . x + d = 0``, + towards the part."""

    normal: np.ndarray
    d: float
    share: float  # of the scan's 4 mm points on the plane
    front_share: float  # ... in front of it (more than 1.5 mm)
    behind_share: float  # ... behind it
    flatness_mm: float  # RMS of the table's faces about the plane
    band_mm: float
    margin_mm: float
    facing_deg: float
    faces: np.ndarray
    kept: np.ndarray
    area_mm2: float  # removed
    area_left_mm2: float
    lowest_mm: float  # the lowest corner of what is left, above the plane (NaN when nothing is left)
    loose_removed: int  # crumbs of table removed with it
    loose_left: int  # loose pieces left besides the largest (for Remove floaters)
    loose_left_mm2: float
    skipped: list[str] = field(default_factory=list)  # planes tried first and set aside, why
    #: raised areas within the margin (module docstring, 2b.): centre (mm), size_mm (across), area_mm2,
    #: height_mm (median, above the plane), kept (a thin part: stays) or not (goes, with a warning), faces
    raised: list[dict] = field(default_factory=list)
    #: the scan's own limit (module docstring, 2b.): thinner than thin_limit_mm a part lying flat cannot be told
    #: from this table at all; from thin_keep_mm it stays where the table round it is as flat as the table
    thin_limit_mm: float = float("nan")
    thin_keep_mm: float = float("nan")
    warnings: list[str] = field(default_factory=list)
    ms: float = 0.0

    @property
    def n_faces(self) -> int:
        return int(len(self.faces))

    @property
    def rule(self) -> str:
        top = max(self.margin_mm, self.band_mm)
        return (f"The table is the largest flat surface with something standing on it and nothing of the part behind "
                f"it. A face is removed when all its corners lie more than {self.band_mm:.2g} mm behind that plane, or "
                f"when it faces the table's way (within {self.facing_deg:g}° of the plane's normal), lies at most "
                f"{self.margin_mm:g} mm above the plane (the margin{'' if top == self.margin_mm else f'; here the noise band, {top:.2g} mm'}) "
                "and is joined through such faces to the plane's own surface, unless it is a thin part lying flat: a "
                "raised area of steady height at least 4 times the table's scatter above it. Faces turned further away "
                f"(the part's walls) stay at any height. Loose pieces left wholly within {top + self.band_mm:.2g} mm of the plane are "
                "crumbs of table and go too; any other loose piece is left for Remove floaters.")

    @property
    def limit(self) -> str:
        """The scan's own limit for thin parts lying flat, in words."""
        if not math.isfinite(self.thin_limit_mm):
            return ""
        return (f"On this table a part lying flat that is thinner than {self.thin_limit_mm:.2f} mm cannot be told from it. "
                f"From {self.thin_keep_mm:.2f} mm (and 20 mm across) it stays, where the table round it is as flat as here; "
                "between the two it goes with the table, drawn and listed, unless you keep it.")

    @property
    def summary(self) -> str:
        low = ("" if not math.isfinite(self.lowest_mm) else "; the part is kept right down to it" if self.lowest_mm <= self.band_mm
               else f"; the part is kept from {self.lowest_mm:.2f} mm above it")
        return (f"Found the table ({100 * self.share:.0f} % of the scan, flat to {self.flatness_mm:.2f} mm RMS): "
                f"{self.n_faces:,} faces ({self.area_mm2 / 100.0:,.1f} cm²) are the table{low}")

    def to_dict(self) -> dict:
        def f(x: float) -> float | None:
            return float(x) if math.isfinite(float(x)) else None

        return {"normal": [float(x) for x in self.normal], "d": float(self.d), "share": float(self.share),
                "front_share": float(self.front_share), "behind_share": float(self.behind_share),
                "flatness_mm": float(self.flatness_mm), "band_mm": float(self.band_mm), "margin_mm": float(self.margin_mm),
                "facing_deg": float(self.facing_deg), "n_faces": self.n_faces, "area_mm2": float(self.area_mm2),
                "area_left_mm2": float(self.area_left_mm2), "lowest_mm": f(self.lowest_mm),
                "loose_removed": int(self.loose_removed), "loose_left": int(self.loose_left),
                "loose_left_mm2": float(self.loose_left_mm2), "skipped": list(self.skipped),
                "raised": [{k: v for k, v in r.items() if k != "faces"} | {"n_faces": int(len(r["faces"]))}
                           for r in self.raised],
                "thin_limit_mm": f(self.thin_limit_mm), "thin_keep_mm": f(self.thin_keep_mm), "limit": self.limit,
                "warnings": list(self.warnings), "rule": self.rule, "summary": self.summary, "ms": float(self.ms)}


# --------------------------------------------------------------------------- the plane
def _clouds(V: np.ndarray, N: np.ndarray, ok: np.ndarray, p: SupportParams):
    """The alignment's 4 mm cloud of the measured vertices: ``(points, normals)``
    (:func:`ohs.align.pairwise.scan_cloud`'s first steps, the same order and thinning)."""
    from ohs.align import pairwise as pw  # noqa: PLC0415

    P, Q = V[ok], N[ok]
    order = np.lexsort((P[:, 2], P[:, 1], P[:, 0]))  # the same points in the same order on every run
    c = pw._cloud(P[order], Q[order], float(p.voxel_mm), float(p.coarse_voxel_mm))
    return c.coarse.pts, c.coarse.nrm


def _judge(C: np.ndarray, n: np.ndarray, d: float, p: SupportParams) -> tuple[str | None, dict]:
    """Whether the plane ``(n, d)`` can be the table: ``(None or why not, the numbers)``."""
    from scipy.spatial import cKDTree  # noqa: PLC0415

    h = C @ n + d
    front, behind = h > p.dist_mm, h < -p.dist_mm
    nf, nb = int(front.sum()), int(behind.sum())
    info = {"front": nf / len(C), "behind": nb / len(C)}
    # behind: deeper than twice the plane's own band. A noisy table (Open3D's mesh of a short scan: 0.4 mm
    # scatter, a slight warp) has as many points just outside the band behind it as the part has in front
    deep = h < -2.0 * p.dist_mm
    if nf < int(p.min_part_points):
        # nothing in front and nothing behind: a bare flat surface, or a table with only thin parts on it
        # (within the plane's 1.5 mm); find_support takes it when it keeps a thin part on it
        info["bare"] = int(deep.sum()) < int(p.min_part_points)
        return "has nothing standing on it", info
    if int(deep.sum()) > nf:
        return ("has more of the scan behind it than in front of it, as a face of the part has the part behind it",
                info)
    if deep.any():
        X = C - h[:, None] * n  # seen along the normal
        Xf = X[front]
        reach = max(_REACH_MIN_MM, float(np.percentile(np.linalg.norm(Xf - Xf.mean(axis=0), axis=1), 95)))
        dist, _ = cKDTree(Xf).query(X[deep], k=1, distance_upper_bound=reach)
        near = int(np.isfinite(dist).sum())
        info["behind_near"] = near
        if near >= max(10, float(p.behind_share) * nf):
            return (f"has the part's surface going on behind it next to what stands on it ({near} of the scan's 4 mm "
                    f"points within {reach:.0f} mm), as a part's own face has", info)
    return None, info


def _refit(V: np.ndarray, F: np.ndarray, measured_f: np.ndarray, n: np.ndarray, d: float, p: SupportParams
           ) -> tuple[np.ndarray, float, float]:
    """The plane fitted again to the mesh's faces on it (module docstring, 1.): ``(n, d, RMS)``."""
    fn, fa = face_normals(V, F)
    Cf = (rows(V, F[:, 0]) + rows(V, F[:, 1]) + rows(V, F[:, 2])) / 3.0
    cos_ok = math.cos(math.radians(_REFINE_DEG))
    band = float(p.dist_mm)
    rms = band
    # start at the table's own level: the most common height within the plane's band (a thin part lying
    # flat on it, a fifth or a third of that band's area, would otherwise pull a first fit up to itself)
    sel = measured_f & (np.abs(Cf @ n + d) <= band) & (fn @ n >= cos_ok)
    if sel.sum() >= 3:
        h = Cf[sel] @ n + d
        hist, edges = np.histogram(h, bins=np.arange(-band, band + 1e-9, 0.05), weights=fa[sel])
        hist = np.convolve(hist, [0.25, 0.5, 0.25], mode="same")
        d -= float(0.5 * (edges[np.argmax(hist)] + edges[np.argmax(hist) + 1]))
        band = 0.25  # about 2.5 times a scan's table scatter (0.05-0.1 mm); a part 0.3 mm thick is mostly outside
    for _ in range(4):
        sel = measured_f & (np.abs(Cf @ n + d) <= band) & (fn @ n >= cos_ok)
        w = fa[sel]
        if w.sum() <= 0 or sel.sum() < 3:
            break
        P = Cf[sel]
        c = (w[:, None] * P).sum(axis=0) / w.sum()
        X = P - c
        _, U = np.linalg.eigh((X * w[:, None]).T @ X)
        n2 = U[:, 0] if float(U[:, 0] @ n) >= 0 else -U[:, 0]
        n, d = n2, -float(n2 @ c)
        h = Cf[sel] @ n + d
        # the band narrows on a robust spread (1.4826 x the median distance): a thin part lying flat
        # (a 100 mm sheet 0.8 mm thick is a fifth of the band's faces) does not hold it open
        robust = 1.4826 * float(_wquant(np.abs(h), w, 0.5))
        band = max(3.0 * robust, float(p.band_min_mm) / 2.0)
        within = np.abs(h) <= band
        rms = float(np.sqrt(np.average(h[within] ** 2, weights=w[within]))) if within.any() else robust
    return n, d, rms


# --------------------------------------------------------------------------- the faces
def _face_pairs(F: np.ndarray, n_vertices: int) -> tuple[np.ndarray, np.ndarray]:
    """Faces that share an edge, as two index arrays (each edge's later faces with its first, as
    :func:`ohs.mesh.topology.edge_components` joins them)."""
    et = edge_table(F, n_vertices)
    a, b = [np.zeros(0, dtype=np.int64)], [np.zeros(0, dtype=np.int64)]
    for k in range(1, int(et.counts.max()) if et.n_edges else 1):
        e = np.flatnonzero(et.counts > k)
        a.append(et.he_order[et.edge_start[e]] // 3)
        b.append(et.he_order[et.edge_start[e] + k] // 3)
    return np.concatenate(a), np.concatenate(b)


def _components(mask: np.ndarray, pairs: tuple[np.ndarray, np.ndarray]) -> tuple[np.ndarray, int, np.ndarray]:
    """The pieces of the faces in ``mask`` joined through shared edges: ``(their indices, count,
    piece of each)``."""
    from scipy.sparse import coo_matrix  # noqa: PLC0415
    from scipy.sparse.csgraph import connected_components  # noqa: PLC0415

    idx = np.flatnonzero(mask)
    if not len(idx):
        return idx, 0, np.zeros(0, dtype=np.int64)
    a, b = pairs
    both = mask[a] & mask[b]
    remap = np.full(len(mask), -1, dtype=np.int64)
    remap[idx] = np.arange(len(idx))
    A = coo_matrix((np.ones(int(both.sum()), dtype=np.int8), (remap[a[both]], remap[b[both]])), shape=(len(idx), len(idx)))
    nc, lab = connected_components(A, directed=False)
    return idx, int(nc), lab.astype(np.int64)


def _wquant(x: np.ndarray, w: np.ndarray, q) -> np.ndarray:
    o = np.argsort(x, kind="stable")
    cw = np.cumsum(w[o])
    return np.interp(np.asarray(q, dtype=np.float64) * cw[-1], cw - 0.5 * w[o], x[o])


def _raised(V: np.ndarray, F: np.ndarray, fa: np.ndarray, hc: np.ndarray, hmax: np.ndarray, cand: np.ndarray,
            behind: np.ndarray, pairs, n: np.ndarray, rms: float, top: float, band: float, p: SupportParams) -> list[dict]:
    """Raised areas among the faces that face the table's way within the margin (module docstring,
    2b.): each with where it is, how large, how high, and whether it is kept as a thin part."""
    lo = max(float(p.thin_find_k) * rms, 0.02)
    idx, nc, lab = _components(cand & (hc > lo), pairs)
    if not nc:
        return []
    area = np.bincount(lab, weights=fa[idx], minlength=nc)
    big = np.flatnonzero(area >= float(p.thin_min_mm2))
    if not len(big):
        return []
    # surface that rises well above the margin (a thick part's walls): a raised area joined to it is that part's
    # foot. Twice the margin, not just past it: a 0.8 mm disc near a scan's edge, where the table itself rises
    # 0.2-0.3 mm, pokes past a 1 mm margin, and was taken for a wall's foot and lost without a word
    rest = ~cand & ~behind
    ridx, rnc, rlab = _components(rest, pairs)
    rising = np.zeros(len(F), dtype=bool)
    if rnc:
        high = np.full(rnc, -np.inf)
        np.maximum.at(high, rlab, hmax[ridx])
        rising[ridx] = high[rlab] > top + max(band, top)
    comp = np.full(len(F), -1, dtype=np.int64)
    comp[idx] = lab
    a, b = pairs
    touch = np.zeros(nc, dtype=bool)
    for x, y in ((a, b), (b, a)):
        hit = (comp[x] >= 0) & rising[y]
        touch[comp[x[hit]]] = True
    C = (V[F[:, 0]] + V[F[:, 1]] + V[F[:, 2]]) / 3.0
    X = C - (C @ n)[:, None] * n  # seen along the normal
    steady = []
    for k in big:
        if touch[k]:
            continue
        f = idx[lab == k]
        w = fa[f]
        # its level: the upper quartile of its heights (the rest is its smeared rim); steady when a good
        # share of it lies at that level, within twice the table's RMS or 15 % of the height
        level = float(_wquant(hc[f], w, 0.75))
        core = np.abs(hc[f] - level) <= max(2.0 * rms, 0.15 * level)
        if w[core].sum() < float(p.thin_steady) * w.sum():
            continue  # a slope or a warp of the table, not a part of steady height
        steady.append((k, f, w, core, float(_wquant(hc[f][core], w[core], 0.5))))
    if not steady:
        return []
    # the table round each area (module docstring, 2b.): the candidate faces 3 to 10 mm from it, not in a
    # raised area. Its median is the level the area stands on, its robust spread the scatter it is told from
    from scipy.spatial import cKDTree  # noqa: PLC0415

    allf = np.concatenate([f for _, f, _, _, _ in steady])
    owner = np.concatenate([np.full(len(f), i) for i, (_, f, _, _, _) in enumerate(steady)])
    inraised = np.zeros(len(F), dtype=bool)
    inraised[idx] = True
    ring_c = np.flatnonzero(cand & ~inraised)
    dist, j = cKDTree(X[allf]).query(X[ring_c], k=1, distance_upper_bound=float(p.ring_mm[1]))
    near = np.isfinite(dist) & (dist >= float(p.ring_mm[0]))
    ring_f, ring_o = ring_c[near], owner[j[near]]
    order = np.argsort(ring_o, kind="stable")
    ring_f, ring_o = ring_f[order], ring_o[order]
    starts = np.searchsorted(ring_o, np.arange(len(steady) + 1))
    out = []
    for i, (k, f, w, core, m) in enumerate(steady):
        rf = ring_f[starts[i]:starts[i + 1]]
        if len(rf) >= 20:
            base = float(np.median(hc[rf]))
            scatter = 1.4826 * float(np.median(np.abs(hc[rf] - base)))
        else:  # no table round it to speak of (it fills the view): the table's own level and spread
            base, scatter = 0.0, rms
        scatter = max(scatter, 0.5 * rms)
        rise = m - base
        if rise < float(p.thin_find_k) * scatter:
            continue  # within the unevenness of the table round it: the table's own
        # its top: how flat, and how parallel to the table (a plane through the faces at its level)
        fc, wc = f[core], w[core]
        cc = (wc[:, None] * C[fc]).sum(axis=0) / wc.sum()
        _, U = np.linalg.eigh(((C[fc] - cc) * wc[:, None]).T @ (C[fc] - cc))
        tilt = float(np.degrees(np.arccos(min(1.0, abs(float(U[:, 0] @ n))))))
        flat = float(np.sqrt(np.average(((C[fc] - cc) @ U[:, 0]) ** 2, weights=wc)))
        c = (w[:, None] * C[f]).sum(axis=0) / w.sum()
        Pc = X[f] - (c - (c @ n) * n)
        size = 2.0 * float(np.percentile(np.linalg.norm(Pc, axis=1), 98))
        uneven = scatter > max(float(p.flat_table_mm), 1.5 * rms)
        keep = (rise >= float(p.thin_keep_k) * scatter and tilt <= float(p.thin_tilt_deg) and size >= float(p.thin_keep_size_mm)
                and not uneven)
        out.append({"centre": [float(x) for x in c], "size_mm": size, "area_mm2": float(area[k]), "height_mm": float(rise),
                    "scatter_mm": float(scatter), "tilt_deg": tilt, "top_mm": flat,
                    "verdict": "kept" if keep else "uneven" if uneven else "warned", "kept": bool(keep), "by_user": False,
                    "faces": f})
    # crowded: where the table rises in steady patches in many places close by (at a table scan's far
    # edge, 230-460 mm from the part on the simulated bracket and ball: 20-37 of them per scan, 6-28 mm
    # across, 0.2-0.6 mm high), one more is the table's own unevenness, not a part: said once, not each
    if len(out) > 1:
        cs = np.array([r["centre"] for r in out])
        cs = cs - (cs @ n)[:, None] * n
        near_n = (cKDTree(cs).query_ball_point(cs, float(p.crowd_mm), return_length=True) - 1).astype(int)
        for r, k in zip(out, near_n):
            r["neighbours"] = int(k)
            if k >= int(p.crowd_n) and r["verdict"] == "warned":  # a clear plateau (kept) stands out even there
                r["verdict"], r["kept"] = "uneven", False
    out.sort(key=lambda r: -r["area_mm2"])
    return out


def find_support(V: np.ndarray, F: np.ndarray, *, normals: np.ndarray | None = None, sigma: np.ndarray | None = None,
                 params: SupportParams | None = None, seed: int = 0, keep=None, by_position: bool = False) -> SupportFinding:
    """The faces of ``(V, F)`` that are the surface the part stands on (module docstring).

    ``normals``: unit vertex normals pointing to where the surface was seen from (default: from
    the faces' winding); ``sigma``: each vertex's sigma in mm, to leave made-up surface (filled
    holes) out of the plane; ``keep``: points (mm) on raised areas the user chose to keep ("Keep this":
    each keeps the raised area with a face within ``keep_reach_mm`` of it). Raises :class:`SupportRefused` with a sentence when no such surface
    can be told apart, ``ValueError`` for bad input. ``by_position``: found on the mesh numbered by
    position (:mod:`ohs.mesh.order`), so the same faces are found whatever order the mesh lists its
    vertices and faces in; the face indices returned are the input's."""
    from ohs.geometry import fit_plane_ransac  # noqa: PLC0415

    p = params if params is not None else SupportParams()
    t0 = time.perf_counter()
    V, F = as_mesh(V, F)
    if len(F) == 0:
        raise ValueError("the mesh has no faces")
    if by_position:
        from ohs.mesh.order import canonical, faces_back  # noqa: PLC0415

        if normals is not None and len(normals) != len(V):
            raise ValueError(f"{len(normals)} normals for {len(V)} vertices")
        if sigma is not None and len(np.asarray(sigma).reshape(-1)) != len(V):
            raise ValueError(f"{len(np.asarray(sigma).reshape(-1))} sigma values for {len(V)} vertices")
        c = canonical(V, F)
        Nc = None if normals is None else np.asarray(normals, dtype=np.float64).reshape(-1, 3)[c.pv]
        Sc = None if sigma is None else np.asarray(sigma, dtype=np.float64).reshape(-1)[c.pv]
        r = find_support(c.V, c.F, normals=Nc, sigma=Sc, params=params, seed=seed, keep=keep)
        r.faces, r.kept = faces_back(c, r.faces), faces_back(c, r.kept)
        for x in r.raised:
            x["faces"] = faces_back(c, x["faces"])
        r.ms = round(1e3 * (time.perf_counter() - t0), 1)
        return r
    N = vertex_normals(V, F) if normals is None else np.asarray(normals, dtype=np.float64).reshape(-1, 3)
    if len(N) != len(V):
        raise ValueError(f"{len(N)} normals for {len(V)} vertices")
    measured = np.ones(len(V), dtype=bool)
    if sigma is not None:
        s = np.asarray(sigma, dtype=np.float64).reshape(-1)
        if len(s) != len(V):
            raise ValueError(f"{len(s)} sigma values for {len(V)} vertices")
        measured = np.isfinite(s) & (s < SIGMA_NONE_MM)
    used = np.zeros(len(V), dtype=bool)
    used[F.reshape(-1)] = True
    ok = used & measured & np.all(np.isfinite(V), axis=1) & np.all(np.isfinite(N), axis=1) & (np.linalg.norm(N, axis=1) > 0.5)
    nothing = "Nothing was removed; if the part does stand on a table here, select the table and delete it."
    if ok.sum() < 50:
        raise SupportRefused(f"No table found: the scan has too little measured surface to look for one. {nothing}")
    C, NC = _clouds(V, N, ok, p)
    if len(C) < 50:
        raise SupportRefused(f"No table found: the scan is too small to look for one. {nothing}")
    # planes, largest first; the first that can be the table is taken
    rest = np.ones(len(C), dtype=bool)
    tried: list[tuple[float, str]] = []
    chosen = None
    bare = None  # the largest plane, when nothing stands on it and nothing lies behind it
    for _ in range(int(p.candidates)):
        idx = np.flatnonzero(rest)
        if len(idx) < 50:
            break
        try:
            n, d, _, _ = fit_plane_ransac(C[idx], thresh=float(p.dist_mm), iters=800, seed=seed, min_inliers=10)
        except ValueError:
            break
        on = np.abs(C @ n + d) < p.dist_mm
        share = float((on & rest).sum()) / len(C)
        if share < p.min_share:
            if not tried:
                raise SupportRefused(f"No table found: the largest flat surface holds {100 * share:.0f} % of the scan, and a "
                                     f"table takes at least {100 * p.min_share:.0f} %. {nothing}")
            break
        if float(np.sum(NC[on] @ n)) < 0:  # towards the side it was seen from: where the part stands
            n, d = -n, -d
        why, info = _judge(C, n, float(d), p)
        if why is None:
            chosen = (n, float(d), share, info)
            break
        if not tried and info.get("bare"):
            bare = (n, float(d), share, info)
        tried.append((share, why))
        rest &= ~on

    def refuse() -> SupportRefused:
        share, why = tried[0]
        more = (f" ({len(tried) - 1} smaller flat surface{'s' if len(tried) > 2 else ''} did not pass either)"
                if len(tried) > 1 else "")
        return SupportRefused(f"No table found: the largest flat surface ({100 * share:.0f} % of the scan) {why}: it looks "
                              f"like a face of the part, not a table{more}. {nothing}")

    if chosen is None and bare is None:
        raise refuse()
    only_thin = chosen is None
    n, d, share, info = chosen if chosen is not None else bare
    # the plane again, on the mesh
    meas_f = measured[F[:, 0]] & measured[F[:, 1]] & measured[F[:, 2]]
    n, d, rms = _refit(V, F, meas_f, n, d, p)
    band = min(max(float(p.band_k) * rms, float(p.band_min_mm)), float(p.dist_mm))
    top = max(float(p.margin_mm), band)
    # the faces (module docstring, 2.)
    fn, fa = face_normals(V, F)
    hv = V @ n + d
    h0, h1, h2 = hv[F[:, 0]], hv[F[:, 1]], hv[F[:, 2]]
    hc = (h0 + h1 + h2) / 3.0
    hmax = np.maximum(np.maximum(h0, h1), h2)
    behind = hmax < -band
    cand = ~behind & (hc <= top) & (fn @ n >= math.cos(math.radians(p.facing_deg)))
    pairs = _face_pairs(F, len(V))
    table = np.zeros(len(F), dtype=bool)
    ci, nc, lab = _components(cand, pairs)
    if nc:
        seed_area = np.bincount(lab, weights=np.where(np.abs(hc[ci]) <= band, fa[ci], 0.0), minlength=nc)
        table[ci] = seed_area[lab] >= float(p.min_patch_mm2)
    # thin parts lying flat (module docstring, 2b.): kept, or said
    raised = [r for r in _raised(V, F, fa, hc, hmax, cand, behind, pairs, n, rms, top, band, p) if table[r["faces"]].any()]
    # "Keep this": a raised area the user chose to keep (a point on it, mm), whatever the rule said of it
    if keep:
        from scipy.spatial import cKDTree  # noqa: PLC0415

        Cn = (V[F[:, 0]] + V[F[:, 1]] + V[F[:, 2]]) / 3.0
        for q in np.asarray(keep, dtype=np.float64).reshape(-1, 3):
            for r in raised:
                dq, _ = cKDTree(Cn[r["faces"]]).query(q, k=1)
                if dq <= float(p.keep_reach_mm):
                    r.update(kept=True, by_user=True, verdict="kept")
                    break
    thin = np.zeros(len(F), dtype=bool)
    for r in raised:
        if r["kept"]:
            thin[r["faces"]] = True
    if only_thin and not thin.any() and float(fa[~table & ~behind & (hc > top)].sum()) < float(p.thin_min_mm2):
        e = refuse()
        unsure = [r for r in raised if not r["kept"]]
        if unsure:
            r = unsure[0]
            e = SupportRefused(str(e).replace(" Nothing was removed", (
                f" A raised area on it, {r['size_mm']:.0f} mm across and {r['height_mm']:.2f} mm high at "
                "({:.0f}, {:.0f}, {:.0f}) mm, may be a thin part too close to the table's own scatter ({:.2f} mm RMS) to "
                "tell.").format(*r["centre"], rms) + " Nothing was removed", 1))
        raise e
    table &= ~thin
    gone = behind | table
    # crumbs (module docstring, 3.)
    ri, npc, lab = _components(~gone, pairs)
    loose_removed = loose_left = 0
    loose_left_mm2 = 0.0
    if npc:
        area = np.bincount(lab, weights=fa[ri], minlength=npc)
        high = np.full(npc, -np.inf)
        np.maximum.at(high, lab, hmax[ri])
        crumb = high <= top + band
        crumb[int(np.argmax(area))] = False
        has_thin = np.zeros(npc, dtype=bool)
        has_thin[lab[thin[ri]]] = True
        crumb &= ~has_thin  # a thin part kept is not a crumb
        gone[ri[crumb[lab]]] = True
        loose_removed = int(crumb.sum())
        left = ~crumb & ~has_thin  # a thin part is a part, not a loose bit for Remove floaters
        left[int(np.argmax(area))] = False
        loose_left, loose_left_mm2 = int(left.sum()), float(area[left].sum())
    faces = np.flatnonzero(gone)
    kept = np.flatnonzero(~gone)
    lowest = float(hv[np.unique(F[kept])].min()) if len(kept) else float("nan")
    warnings = []
    if only_thin:
        tried = []  # the largest plane is the table after all: only thin parts lie on it
    def at(r: dict) -> str:
        return "at ({:.0f}, {:.0f}, {:.0f}) mm".format(*r["centre"])

    for r in raised:
        if r["kept"]:
            who = "kept, as you chose" if r["by_user"] else "kept"
            warnings.append(f"A thin part lying flat is {who}: {r['size_mm']:.0f} mm across, {r['height_mm']:.2f} mm above the "
                            f"table, {at(r)}. It is a piece of its own now: Remove floaters may take it if it is small.")
    warned = [r for r in raised if r["verdict"] == "warned"]
    for r in warned[:8]:
        warnings.append(f"A raised area {r['size_mm']:.0f} mm across, {r['height_mm']:.2f} mm above the table round it, {at(r)}, "
                        f"goes with the table: it may be a thin part (the table round it scatters {r['scatter_mm']:.2f} mm). "
                        "To keep it, choose Keep this, or select it by hand.")
    if len(warned) > 8:
        warnings.append(f"{len(warned) - 8} more raised areas like these go with the table; each is listed and drawn.")
    uneven = [r for r in raised if r["verdict"] == "uneven"]
    if uneven:
        hi = max(r["height_mm"] for r in uneven)
        warnings.append(f"{len(uneven)} raised area{'s' if len(uneven) != 1 else ''} up to {hi:.2f} mm high go with the table "
                        "where it is uneven itself (it scatters more than "
                        f"{max(float(p.flat_table_mm), 1.5 * rms):.2f} mm there, or rises in many places close by): there a "
                        f"thin part lying flat that is thinner than about {math.ceil(10 * hi) / 10:.1f} mm cannot be told from it.")
    if tried:
        s0, why0 = tried[0]
        warnings.append(f"The largest flat surface ({100 * s0:.0f} % of the scan) was not taken for the table: it {why0}. "
                        f"The table is the next one ({100 * share:.0f} %).")
    if rms > 0.3:
        warnings.append(f"The table is not flat to the scan's level ({rms:.2f} mm RMS about its plane): parts of it further "
                        f"than {top:.2g} mm from the plane may be left.")
    if loose_left:
        warnings.append(f"{loose_left} loose piece{'s' if loose_left != 1 else ''} besides the part {'are' if loose_left != 1 else 'is'} "
                        f"left ({loose_left_mm2:,.0f} mm²): Remove floaters takes {'them' if loose_left != 1 else 'it'}.")
    if not len(kept):
        warnings.append("Nothing would be left: the whole scan lies on the plane.")
    return SupportFinding(normal=np.asarray(n, dtype=np.float64), d=float(d), share=share, front_share=float(info["front"]),
                          behind_share=float(info["behind"]), flatness_mm=rms, band_mm=band, margin_mm=float(p.margin_mm),
                          facing_deg=float(p.facing_deg), faces=faces, kept=kept, area_mm2=float(fa[gone].sum()),
                          area_left_mm2=float(fa[~gone].sum()), lowest_mm=lowest, loose_removed=loose_removed,
                          loose_left=loose_left, loose_left_mm2=loose_left_mm2, skipped=[w for _, w in tried],
                          raised=raised, thin_limit_mm=float(p.thin_find_k) * rms, thin_keep_mm=float(p.thin_keep_k) * rms,
                          warnings=warnings, ms=round(1e3 * (time.perf_counter() - t0), 1))


def remove_support(V: np.ndarray, F: np.ndarray, *, normals: np.ndarray | None = None, sigma: np.ndarray | None = None,
                   params: SupportParams | None = None, seed: int = 0, keep=None,
                   by_position: bool = False) -> tuple[np.ndarray, np.ndarray, SupportFinding]:
    """:func:`find_support`, applied: ``(V2, F2, finding)``, the kept faces in their order and
    their vertices (as :func:`ohs.mesh.keep_faces` with ``finding.kept``)."""
    r = find_support(V, F, normals=normals, sigma=sigma, params=params, seed=seed, keep=keep, by_position=by_position)
    V, F = as_mesh(V, F)
    V2, F2, _ = compact(V, F[r.kept])
    return (V2.copy() if V2 is V else V2), F2, r


# --------------------------------------------------------------------------- a merge of several scans (4.)
def _plane_basis(n: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two unit vectors across the plane's normal ``n`` (always the same pair for the same ``n``)."""
    n = np.asarray(n, dtype=np.float64)
    a = np.array([1.0, 0.0, 0.0]) if abs(float(n[0])) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = a - float(a @ n) * n
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(n, e1)


class _MapInCamera:
    """A :class:`SupportMap` seen from one frame's camera (``T_wc``: the camera in the scan's world): what
    :meth:`ohs.app.pipeline.FramePipeline.process` keeps of the frame's depth and laser points
    (``clip_plane``), point by point in the camera's frame."""

    def __init__(self, m: SupportMap, T_wc: np.ndarray) -> None:
        T = np.asarray(T_wc, dtype=np.float64).reshape(4, 4)
        R, t = T[:3, :3], T[:3, 3]
        # a camera point X lies at R X + t in the scan's world: its height and plane coordinates are linear in X
        self.A = np.stack([R.T @ m.normal, R.T @ m.e1, R.T @ m.e2]).astype(np.float32)
        self.b = np.array([float(m.normal @ t) + m.d, float(m.e1 @ t) - m.origin[0], float(m.e2 @ t) - m.origin[1]],
                          dtype=np.float32)
        self.map = m

    def keep(self, P: np.ndarray) -> np.ndarray:
        """Which of the points ``P`` (..., 3), in the camera's frame, are kept."""
        P = np.asarray(P)
        Q = P.reshape(-1, 3).astype(np.float32, copy=False) @ self.A.T + self.b
        return self.map._keep_huv(Q[:, 0], Q[:, 1], Q[:, 2]).reshape(P.shape[:-1])


@dataclass
class SupportMap:
    """Where a scan's table is, place by place on its plane (module docstring, 4.), in the scan's own
    world. The grids are of ``cell_mm`` cells: cell ``(i, j)`` covers the plane coordinates (along
    ``e1``, ``e2``) from ``origin + cell_mm * (i, j)`` to ``origin + cell_mm * (i + 1, j + 1)``; heights
    are along ``normal`` from the plane ``normal . x + d = 0``, + towards the part. ``core``: the part
    (or a thin part) stands here; ``stands``: ``core`` grown by the frames' scatter; ``table``: the scan's
    table was found here; ``thin``: a thin part lying flat (kept, or warned about) is here. ``info``:
    what the finding said, for the merge's report (:meth:`to_dict`)."""

    normal: np.ndarray
    d: float
    e1: np.ndarray
    e2: np.ndarray
    origin: np.ndarray
    cell_mm: float
    stands: np.ndarray
    core: np.ndarray
    table: np.ndarray
    thin: np.ndarray
    clip_mm: float = CLIP_MM
    behind_mm: float = _MAP_BEHIND_MM
    margin_mm: float = MARGIN_MM
    band_mm: float = 0.3
    facing_deg: float = FACING_DEG
    min_patch_mm2: float = 10.0
    info: dict = field(default_factory=dict)
    #: each of ``info["thin_parts"]``'s own cells (flat indices into the grids), in the same order
    thin_cells: list = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self.normal = np.asarray(self.normal, dtype=np.float64).reshape(3)
        self.e1 = np.asarray(self.e1, dtype=np.float64).reshape(3)
        self.e2 = np.asarray(self.e2, dtype=np.float64).reshape(3)
        self.origin = np.asarray(self.origin, dtype=np.float64).reshape(2)
        self.d, self.cell_mm = float(self.d), float(self.cell_mm)
        for name in ("stands", "core", "table", "thin"):
            setattr(self, name, np.asarray(getattr(self, name), dtype=bool))
        if not (self.stands.shape == self.core.shape == self.table.shape == self.thin.shape and self.stands.ndim == 2):
            raise ValueError("the map's grids must be 2-D and of one shape")

    # ------------------------------------------------------------------ looking things up
    def heights(self, P: np.ndarray) -> np.ndarray:
        """Heights of the points ``P`` (n, 3), in the scan's world, above the plane."""
        return np.asarray(P, dtype=np.float64).reshape(-1, 3) @ self.normal + self.d

    def _cells(self, u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        nu, nv = self.stands.shape
        with np.errstate(invalid="ignore"):
            fi, fj = np.floor(u / self.cell_mm), np.floor(v / self.cell_mm)
            inside = (fi >= 0) & (fi < nu) & (fj >= 0) & (fj < nv)  # NaN is outside
        return fi[inside].astype(np.int64), fj[inside].astype(np.int64), inside

    def _lookup(self, grid: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
        i, j, inside = self._cells(u, v)
        out = np.zeros(len(u), dtype=bool)
        out[inside] = grid[i, j]
        return out

    def at(self, grid: np.ndarray, P: np.ndarray) -> np.ndarray:
        """``grid`` (one of the map's) at the points ``P`` (n, 3), in the scan's world; False outside the map."""
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
        return self._lookup(grid, P @ self.e1 - self.origin[0], P @ self.e2 - self.origin[1])

    def _keep_huv(self, h: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
        stands = self._lookup(self.stands, u, v)
        return h > np.where(stands, np.float32(-self.behind_mm), np.float32(self.clip_mm))

    def keep(self, P: np.ndarray) -> np.ndarray:
        """Which of the points ``P`` (n, 3), in the scan's world, a frame keeps (module docstring, 4.)."""
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
        return self._keep_huv(self.heights(P), P @ self.e1 - self.origin[0], P @ self.e2 - self.origin[1])

    def in_camera(self, T_wc: np.ndarray) -> _MapInCamera:
        """The map as one frame's ``clip_plane`` (:meth:`ohs.app.pipeline.FramePipeline.process`): the frame
        at ``T_wc`` in the scan's world keeps what :meth:`keep` keeps."""
        return _MapInCamera(self, T_wc)

    # ------------------------------------------------------------------ what it says
    @property
    def stands_mm2(self) -> float:
        return float(self.core.sum()) * self.cell_mm ** 2

    def to_dict(self) -> dict:
        """The map in plain numbers and words, for the merge's report (no grids)."""
        out = {"plane": {"normal": [float(x) for x in self.normal], "d": float(self.d)}, "cell_mm": self.cell_mm,
               "clip_mm": float(self.clip_mm), "behind_mm": float(self.behind_mm), "margin_mm": float(self.margin_mm),
               "band_mm": float(self.band_mm), "stands_mm2": self.stands_mm2}
        info = {"share": None, "flatness_mm": None, "table_area_mm2": None, "table_faces": None, "thin_parts": [], "uneven": None,
                "thin_limit_mm": None, "thin_keep_mm": None, "limit": "", "warnings": []}
        info.update(self.info)
        out.update(info)
        out["thin_parts"] = [dict(x) for x in out["thin_parts"]]
        out["warnings"] = list(out["warnings"])
        return out


def _mark(grid: np.ndarray, m: SupportMap, V: np.ndarray, F: np.ndarray) -> None:
    """Set the cells of ``grid`` under the faces ``F`` (their corners, edge middles and centres)."""
    if not len(F):
        return
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    for P in (a, b, c, (a + b) / 2, (b + c) / 2, (c + a) / 2, (a + b + c) / 3):
        i, j, _ = m._cells(P @ m.e1 - m.origin[0], P @ m.e2 - m.origin[1])
        grid[i, j] = True


def support_map(V: np.ndarray, F: np.ndarray, finding: SupportFinding, *, cell_mm: float = _MAP_CELL_MM,
                reach_mm: float = _MAP_REACH_MM, spread_mm: float = _MAP_SPREAD_MM, clip_mm: float = CLIP_MM,
                behind_mm: float = _MAP_BEHIND_MM) -> SupportMap:
    """The map on the table's plane of a scan's mesh ``(V, F)`` from its :func:`find_support` finding
    (module docstring, 4.), for a merge of several scans of a part that was moved."""
    from scipy.ndimage import binary_dilation  # noqa: PLC0415

    V, F = as_mesh(V, F)
    n, d = np.asarray(finding.normal, dtype=np.float64), float(finding.d)
    e1, e2 = _plane_basis(n)
    used = np.zeros(len(V), dtype=bool)
    used[F.reshape(-1)] = True
    used &= np.all(np.isfinite(V), axis=1)
    UV = np.column_stack([V[used] @ e1, V[used] @ e2])
    pad = float(spread_mm) + 2.0 * float(cell_mm)
    lo, hi = UV.min(axis=0) - pad, UV.max(axis=0) + pad
    cell = float(cell_mm)
    while math.ceil((hi[0] - lo[0]) / cell) * math.ceil((hi[1] - lo[1]) / cell) > _MAP_MAX_CELLS:
        cell *= 1.5  # a very large table: coarser cells, the same rule
    shape = (int(math.ceil((hi[0] - lo[0]) / cell)), int(math.ceil((hi[1] - lo[1]) / cell)))
    p = SupportParams()
    m = SupportMap(normal=n, d=d, e1=e1, e2=e2, origin=lo, cell_mm=cell, stands=np.zeros(shape, bool),
                   core=np.zeros(shape, bool), table=np.zeros(shape, bool), thin=np.zeros(shape, bool),
                   clip_mm=float(clip_mm), behind_mm=float(behind_mm), margin_mm=float(finding.margin_mm),
                   band_mm=float(finding.band_mm), facing_deg=float(finding.facing_deg), min_patch_mm2=float(p.min_patch_mm2))
    hv = V @ n + d
    low = np.minimum(np.minimum(hv[F[:, 0]], hv[F[:, 1]]), hv[F[:, 2]])
    kept = np.asarray(finding.kept, dtype=np.int64)
    _mark(m.table, m, V, F[np.asarray(finding.faces, dtype=np.int64)])
    _mark(m.core, m, V, F[kept[low[kept] <= float(reach_mm)]])
    warned = [r for r in finding.raised if r["verdict"] == "warned"]
    thin_raised = [r for r in finding.raised if r["kept"] or r["verdict"] == "warned"]  # a thin part, or what may be one
    if thin_raised:
        # each whole: a raised area is the faces above 2.5 times the table's RMS, and the scan's noise dips a thin
        # part's top below that in spots (a 0.3 mm disc on a table of 0.1 mm: 6 % of its top); and a cell more of
        # its smeared rim. Each area's own cells are kept too, so that a merge can say whether it is in the result
        from scipy.ndimage import binary_fill_holes  # noqa: PLC0415

        for r in thin_raised:
            g = np.zeros(shape, dtype=bool)
            _mark(g, m, V, F[np.asarray(r["faces"], dtype=np.int64)])
            g = binary_fill_holes(g)
            m.thin_cells.append(np.flatnonzero(g))
            m.thin |= g
        m.thin = binary_dilation(m.thin, structure=np.ones((3, 3), dtype=bool))
    m.core |= m.thin
    k = int(math.ceil(float(spread_mm) / cell))
    yy, xx = np.mgrid[-k:k + 1, -k:k + 1]
    m.stands = binary_dilation(m.core, structure=(xx * xx + yy * yy) * cell * cell <= float(spread_mm) ** 2 + 1e-9) if k else m.core.copy()
    # what it says (the merge's report)
    def at(r: dict) -> str:
        return "at ({:.0f}, {:.0f}, {:.0f}) mm".format(*r["centre"])

    # what a merge means to do with each (a merge says afterwards what of each is in its result: ohs.align.merge)
    thin_parts = [{"centre": [float(x) for x in r["centre"]], "size_mm": float(r["size_mm"]), "area_mm2": float(r["area_mm2"]),
                   "height_mm": float(r["height_mm"]), "verdict": "kept" if r["kept"] else "warned", "in_merge": "kept",
                   "where": at(r)} for r in thin_raised]
    uneven = [r for r in finding.raised if r["verdict"] == "uneven"]
    warnings = []
    for x in [x for x in thin_parts if x["verdict"] == "warned"][:8]:
        x["warning"] = (f"A raised area {x['size_mm']:.0f} mm across, {x['height_mm']:.2f} mm above the table round it, "
                        f"{x['where']}, may be a thin part: it is kept in the merged scan (if it is the table's own, a patch "
                        "of table that size is left there).")
        warnings.append(x["warning"])
    if len(warned) > 8:
        more = len(warned) - 8
        warnings.append(f"{more} more raised area{'s' if more != 1 else ''} like these {'are' if more != 1 else 'is'} kept in "
                        "the merged scan.")
    if uneven:
        hi_mm = max(r["height_mm"] for r in uneven)
        warnings.append(f"{len(uneven)} raised area{'s' if len(uneven) != 1 else ''} up to {hi_mm:.2f} mm high, where the table is "
                        "uneven itself (near the scan's border), go with the table: there a part lying flat that is thinner "
                        f"than about {math.ceil(10 * hi_mm) / 10:.1f} mm cannot be told from it.")
    if finding.flatness_mm > 0.3:
        warnings.append(f"The table is not flat to the scan's level ({finding.flatness_mm:.2f} mm RMS about its plane): parts of "
                        f"it further than {max(finding.margin_mm, finding.band_mm):.2g} mm from the plane may be left in the merged scan.")
    limit = ""
    if math.isfinite(finding.thin_limit_mm):
        limit = (f"On this table a part lying flat that is thinner than {finding.thin_limit_mm:.2f} mm, or smaller than about "
                 f"{2.0 * math.sqrt(float(p.thin_min_mm2) / math.pi):.0f} mm across, cannot be told from it and is left out "
                 f"with it. Thicker, it is kept.")
    m.info = {"share": float(finding.share), "flatness_mm": float(finding.flatness_mm), "table_area_mm2": float(finding.area_mm2),
              "table_faces": int(finding.n_faces), "thin_parts": thin_parts,
              "uneven": {"count": len(uneven), "highest_mm": float(max(r["height_mm"] for r in uneven))} if uneven else None,
              "thin_limit_mm": float(finding.thin_limit_mm) if math.isfinite(finding.thin_limit_mm) else None,
              "thin_keep_mm": float(finding.thin_keep_mm) if math.isfinite(finding.thin_keep_mm) else None,
              "limit": limit, "warnings": warnings}
    return m


@dataclass
class TableLeft:
    """What :func:`table_left` found of a scan's table in a merged mesh: ``faces`` (indices, sorted)."""

    faces: np.ndarray
    area_mm2: float
    pieces: int  # loose pieces among them (crumbs)
    ms: float = 0.0

    @property
    def n_faces(self) -> int:
        return int(len(self.faces))

    def to_dict(self) -> dict:
        return {"faces": self.n_faces, "area_mm2": float(self.area_mm2), "pieces": int(self.pieces), "ms": float(self.ms)}


def table_left(V: np.ndarray, F: np.ndarray, m: SupportMap, *, T: np.ndarray | None = None) -> TableLeft:
    """The faces of a merged mesh ``(V, F)`` that are what is left of the table of the scan ``m`` maps
    (module docstring, 4.). ``T``: that scan's world in the mesh's (default: the same)."""
    t0 = time.perf_counter()
    V, F = as_mesh(V, F)
    if T is not None:
        Ti = np.linalg.inv(np.asarray(T, dtype=np.float64).reshape(4, 4))
        V = V @ Ti[:3, :3].T + Ti[:3, 3]
    if not len(F):
        return TableLeft(np.zeros(0, dtype=np.int64), 0.0, 0)
    n = m.normal
    fn, fa = face_normals(V, F)
    hv = V @ n + m.d
    h0, h1, h2 = hv[F[:, 0]], hv[F[:, 1]], hv[F[:, 2]]
    hc = (h0 + h1 + h2) / 3.0
    hmax = np.maximum(np.maximum(h0, h1), h2)
    hmin = np.minimum(np.minimum(h0, h1), h2)
    C = (V[F[:, 0]] + V[F[:, 1]] + V[F[:, 2]]) / 3.0
    tab, thin = m.at(m.table, C), m.at(m.thin, C)
    band = float(m.band_mm)
    top = max(float(m.margin_mm), band)
    # faces of the table (2. above): facing its way, within the margin, where this scan found table, joined to the plane
    with np.errstate(invalid="ignore"):
        cand = (hc >= -band) & (hc <= top) & (fn @ n >= math.cos(math.radians(m.facing_deg))) & tab & ~thin
    pairs = _face_pairs(F, len(V))
    gone = np.zeros(len(F), dtype=bool)
    ci, nc, lab = _components(cand, pairs)
    if nc:
        seed = np.bincount(lab, weights=np.where(np.abs(hc[ci]) <= band, fa[ci], 0.0), minlength=nc)
        gone[ci] = seed[lab] >= float(m.min_patch_mm2)
    # crumbs (3. above): loose pieces wholly within the margin and the band, mostly over this scan's table
    pieces = 0
    ri, npc, lab = _components(~gone, pairs)
    if npc > 1:
        area = np.bincount(lab, weights=fa[ri], minlength=npc)
        high = np.full(npc, -np.inf)
        np.maximum.at(high, lab, hmax[ri])
        low = np.full(npc, np.inf)
        np.minimum.at(low, lab, hmin[ri])
        over = np.bincount(lab, weights=fa[ri] * tab[ri], minlength=npc)
        has_thin = np.bincount(lab, weights=thin[ri].astype(np.float64), minlength=npc) > 0
        crumb = (high <= top + band) & (low >= -(top + band)) & (over >= 0.5 * area) & ~has_thin
        crumb[int(np.argmax(area))] = False
        gone[ri[crumb[lab]]] = True
        pieces = int(crumb.sum())
    faces = np.flatnonzero(gone)
    return TableLeft(faces, float(fa[gone].sum()), pieces, round(1e3 * (time.perf_counter() - t0), 1))


def laser_points_on_table(P: np.ndarray, m: SupportMap, *, T: np.ndarray | None = None) -> np.ndarray:
    """Which laser points ``P`` (n, 3) of a merged cloud are on the table of the scan ``m`` maps (module
    docstring, 4.): at most the margin above its plane (or behind it), where that scan found its table and
    the part does not stand. ``T``: that scan's world in the cloud's (default: the same)."""
    P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
    if T is not None:
        Ti = np.linalg.inv(np.asarray(T, dtype=np.float64).reshape(4, 4))
        P = P @ Ti[:3, :3].T + Ti[:3, 3]
    if not len(P):
        return np.zeros(0, dtype=bool)
    top = max(float(m.margin_mm), float(m.band_mm))
    with np.errstate(invalid="ignore"):
        low = m.heights(P) <= top
    return low & m.at(m.table, P) & ~m.at(m.core, P)
