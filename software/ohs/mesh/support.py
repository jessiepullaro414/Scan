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
   twice the RMS or 15 %), not spread up a slope or a warp. Its height is the median of that level.
   It is told from the table's *scatter*: the table's RMS, or the scan's own sigma there when that
   is larger (at a table scan's far edge, 200-500 mm from the part, sigma is 0.15-0.9 mm and the
   table rises in steady patches 0.25-0.45 mm high). From ``thin_keep_k`` (4) times the scatter it
   is a thin part and stays, its rim down to 2.5 times the RMS. Between 2.5 and 4 times it goes with
   the table and the result says so: where, how large, how high (one warning each, or one for many:
   then it is the table's own unevenness, and the warning says how thin a part cannot be told from
   it). Measured (the scan's noise 0.05-0.1 mm, edges smeared over 1.5 mm, 10-100 mm across, alone
   and beside a box; and raised patches of the simulated ball scan's real table, flat to 0.08 mm):
   kept from 0.5 mm (0.2-0.3 mm on a table flat to 0.05 mm), losing 0-0.2 mm of its rim; 0.2-0.3 mm
   found and warned when 30 mm or more across; 0.1 mm, and 0.2 mm at 10 mm across, cannot be told
   from the table at all. To find the table's level under a large thin part (a 100 mm sheet can be
   a third of the plane's band), the plane is refitted from the most common height and its band
   narrows on a robust spread. When nothing stands on the largest plane and nothing lies behind it,
   it is the table if a thin part (or anything above the margin) is found on it, else refused; the
   refusal names a raised area it could not tell.

3. **What is left** is the part. Pieces of it (joined by edges) that lie wholly within the margin
   plus the band above the plane are crumbs of table the rule did not reach (noisy faces at the
   scan's border, folds) and go too, unless they hold a thin part kept; the largest piece always stays. Any other loose piece (a bit
   of the part, something else standing on the table) is left for Remove floaters, and the result
   says how many.

The result (:class:`SupportFinding`) says what was found and done: the plane, its share of the
scan, how much lies in front of and behind it, its flatness, the faces removed and their area,
the lowest height of what is left above the plane, the raised areas (thin parts kept, or said),
and warnings.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from ohs.mesh.confidence import SIGMA_NONE_MM
from ohs.mesh.topology import as_mesh, compact, edge_table, face_normals, rows, vertex_normals

__all__ = ["MARGIN_MM", "FACING_DEG", "SupportParams", "SupportFinding", "SupportRefused", "find_support",
           "remove_support"]

#: how far above the plane a face that faces the table's way still counts as the table (mm). Measured
#: on the simulated bracket seen from three sides (judged by its true shape): at 0.5 mm 180-460 mm² of
#: table was left near the part, at 1 mm 0.5-3.3 mm², and more margin took no more table but more of
#: a sloped part; the part lost 17-19 mm² of its rounded foot, all within 1 mm of the table
MARGIN_MM = 1.0
#: a face turned less than this from the plane's normal faces the table's way
FACING_DEG = 45.0
_REFINE_DEG = 30.0  # faces fitted to the plane: within this of its normal
_REACH_MIN_MM = 10.0


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
    thin_keep_k: float = 4.0  # kept as a part from this many times the table's RMS; below it, warned

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
            behind: np.ndarray, pairs, n: np.ndarray, rms: float, top: float, band: float, p: SupportParams,
            sigma: np.ndarray | None = None) -> list[dict]:
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
    # surface that rises above the margin (a thick part's walls): a raised area joined to it is that part's foot
    rest = ~cand & ~behind
    ridx, rnc, rlab = _components(rest, pairs)
    rising = np.zeros(len(F), dtype=bool)
    if rnc:
        high = np.full(rnc, -np.inf)
        np.maximum.at(high, rlab, hmax[ridx])
        rising[ridx] = high[rlab] > top + band
    comp = np.full(len(F), -1, dtype=np.int64)
    comp[idx] = lab
    a, b = pairs
    touch = np.zeros(nc, dtype=bool)
    for x, y in ((a, b), (b, a)):
        hit = (comp[x] >= 0) & rising[y]
        touch[comp[x[hit]]] = True
    C = (V[F[:, 0]] + V[F[:, 1]] + V[F[:, 2]]) / 3.0
    out = []
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
        m = float(_wquant(hc[f][core], w[core], 0.5))
        # the scatter it is told from: the table's RMS, or the scan's own sigma there when that is larger
        # (at a table scan's far edge, 200-500 mm from the part, sigma is 0.15-0.9 mm and the table rises
        # in steady patches 0.25-0.45 mm high: on the simulated bracket and ball, 90-110 of them)
        scatter = rms
        if sigma is not None:
            scatter = max(rms, float(np.median(sigma[F[f].reshape(-1)])))
        if m < float(p.thin_find_k) * scatter:
            continue
        P = C[f] - (C[f] @ n)[:, None] * n
        c = (w[:, None] * C[f]).sum(axis=0) / w.sum()
        Pc = P - (c - (c @ n) * n)
        size = 2.0 * float(np.percentile(np.linalg.norm(Pc, axis=1), 98))
        out.append({"centre": [float(x) for x in c], "size_mm": size, "area_mm2": float(area[k]), "height_mm": float(m),
                    "scatter_mm": float(scatter), "kept": bool(m >= float(p.thin_keep_k) * scatter), "faces": f})
    out.sort(key=lambda r: -r["area_mm2"])
    return out


def find_support(V: np.ndarray, F: np.ndarray, *, normals: np.ndarray | None = None, sigma: np.ndarray | None = None,
                 params: SupportParams | None = None, seed: int = 0) -> SupportFinding:
    """The faces of ``(V, F)`` that are the surface the part stands on (module docstring).

    ``normals``: unit vertex normals pointing to where the surface was seen from (default: from
    the faces' winding); ``sigma``: each vertex's sigma in mm, to leave made-up surface (filled
    holes) out of the plane. Raises :class:`SupportRefused` with a sentence when no such surface
    can be told apart, ``ValueError`` for bad input."""
    from ohs.geometry import fit_plane_ransac  # noqa: PLC0415

    p = params if params is not None else SupportParams()
    t0 = time.perf_counter()
    V, F = as_mesh(V, F)
    if len(F) == 0:
        raise ValueError("the mesh has no faces")
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
    svals = None
    if sigma is not None:
        svals = np.where(measured, np.asarray(sigma, dtype=np.float64).reshape(-1), SIGMA_NONE_MM)
    raised = [r for r in _raised(V, F, fa, hc, hmax, cand, behind, pairs, n, rms, top, band, p, svals)
              if table[r["faces"]].any()]
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
            warnings.append(f"A thin part lying flat is kept: {r['size_mm']:.0f} mm across, {r['height_mm']:.2f} mm above the "
                            f"table, {at(r)}. It is a piece of its own now: Remove floaters may take it if it is small.")
    unsure = [r for r in raised if not r["kept"]]
    if 0 < len(unsure) <= 3:
        for r in unsure:
            how = (f"lower the margin below {r['height_mm']:.1f} mm, or select it by hand" if r["height_mm"] > band
                   else "select it by hand")
            warnings.append(f"A raised area {r['size_mm']:.0f} mm across, {r['height_mm']:.2f} mm above the table, {at(r)}, goes "
                            f"with the table: it may be a thin part, too close to the table's own scatter "
                            f"({r['scatter_mm']:.2f} mm) to tell. To keep it, {how}.")
    elif unsure:  # many: the table's own unevenness, said once (on the simulated ball's table, 27 of them)
        hi = max(r["height_mm"] for r in unsure)
        big = ", ".join(f"{r['size_mm']:.0f} mm across {at(r)}" for r in unsure[:3])
        warnings.append(f"{len(unsure)} raised areas up to {hi:.2f} mm above the table go with it (the largest: {big}): the "
                        f"table is that uneven, so a thin part lying flat that is thinner than about "
                        f"{math.ceil(10 * hi) / 10:.1f} mm cannot be "
                        "told from it. If there is one, it may be among them: select it by hand to keep it.")
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
                          raised=raised, warnings=warnings, ms=round(1e3 * (time.perf_counter() - t0), 1))


def remove_support(V: np.ndarray, F: np.ndarray, *, normals: np.ndarray | None = None, sigma: np.ndarray | None = None,
                   params: SupportParams | None = None, seed: int = 0) -> tuple[np.ndarray, np.ndarray, SupportFinding]:
    """:func:`find_support`, applied: ``(V2, F2, finding)``, the kept faces in their order and
    their vertices (as :func:`ohs.mesh.keep_faces` with ``finding.kept``)."""
    r = find_support(V, F, normals=normals, sigma=sigma, params=params, seed=seed)
    V, F = as_mesh(V, F)
    V2, F2, _ = compact(V, F[r.kept])
    return (V2.copy() if V2 is V else V2), F2, r
