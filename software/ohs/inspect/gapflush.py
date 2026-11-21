"""Gap and flush along a seam between two panels (FEATURE_PLAN I4).

Between two body panels a car person asks for two numbers along the seam: the **gap**, edge to
edge in the panels' surface, and the **flush**, the step between the two surfaces.
:func:`measure_gap_flush` takes the scan's mesh and two clicks, one on each panel near the seam,
follows the seam, cuts a section across it every :data:`STEP_MM` (:mod:`ohs.mesh.section`) and
reads both in each section. The first click's panel is **panel A**, the second's **panel B**.

**Following the seam.** The first section is square to the seam halfway between the clicks: the
seam's direction is panel A's normal (its faces around the click) crossed with the direction from
click A to click B. Each section gives the seam's middle there (halfway between the two edges)
and panel A's normal; the next section is :data:`STEP_MM` further on, its direction turned half
way towards the line through the last two middles. It goes both ways from the clicks until
:data:`MAX_MISSES` sections in a row find no surface (the panels or the scan end) or
``max_length_mm``.

**In each section** the cut is taken in the station's own axes: ``s`` across the seam from A
towards B, ``h`` along A's normal (towards the side the scan was made from). Only points on faces
that face that side (within 60°) and are measured (not filled in) count for a surface.

* **A panel's surface line** is a straight line fitted to the section's points over
  :data:`FIT_MM` that end :data:`MARGIN_MM` before its edge, away from the rounded edge itself:
  weighted by ``1 / sigma^2``, points further than three times the robust scatter (or three
  sigma) left out and the line fitted again. The first section starts from the clicks (each
  window ends at its click), every section is read twice: the second time with the windows set
  from the edges the first found.
* **The profile is walked** from the window's last point towards the other panel along its
  section line. The walk stops where the line ends (the scan's edge), where the next stretch is
  filled in (not measured), where it has gone :data:`DEEP_MM` below the surface line (down the
  edge's wall), where it turns back by :data:`TURN_BACK_MM` (round under the hem), or where it
  reaches the other panel's window: then the scan runs on across the gap, the section is
  **bridged** (stereo matching fills a narrow gap over a dark background, ``VALIDATION.md``,
  "Holes filled in").
* **The edge point**, by one of :data:`EDGE_RULES` (every station records all of them, for
  comparison; ``rule`` picks the one the answer uses, :data:`DEFAULT_RULE`):

  - ``"drop_<d>"``: where the profile has gone ``d`` mm below its surface line (0.25, 0.5 and
    1 mm), interpolated between the two points either side. On a round edge of radius ``R`` this
    is ``R - sqrt(2 R d - d^2)`` inside its outermost point (0.13 mm for ``R`` = 1 mm and ``d`` =
    0.5 mm): a bias that depends on the edge's shape, not on the scan. The same 0.5 mm the Hole
    tool uses for a hole's rim (``ohs.measure.region``, ``RIM_DROP_MM``).
  - ``"end"``: the point of the walk furthest towards the other panel (the outermost point of
    the edge, or where the scan stops). On a true surface it is the gap a feeler gauge measures;
    on a scan it is wherever the scan's surface ends.

* **Flush** is the distance from A's surface line to B's, square to A's line, at the seam's
  middle (halfway between the two edges): **positive when B stands above A** (out towards the
  side the scan was made from), negative when it is below. Clicking the other panel first
  changes its sign.
* **Gap** is the distance between the two edge points along A's surface line.

**The ± values** (1σ). A line's position at the seam's middle has the variance of its fit
(weighted least squares, scaled by the reduced chi-square when the points scatter more than their
sigma, as :mod:`ohs.measure.fit` does) times ``N_c``: neighbouring points of a scan err together,
so the fit counts as ``n / N_c`` independent points. ``N_c`` is measured once per seam from the
lines' own residuals (their correlation by neighbour, pooled over all sections, summed while it
stays above zero), as :mod:`ohs.measure.region` does for a region. **Flush ±**: both lines' at the
middle. **An edge's ±**: the height error there (the point's own sigma and the line's) divided by
how steeply the profile falls there (over the chord from half to one and a half times the drop;
at least :data:`SLOPE_FLOOR`), so a gently falling edge reads less precisely; for ``"end"`` the
point's own sigma. **Gap ±**: both edges'. Without a
sigma per vertex the points' scatter about their line stands in for it. These are statistical
errors only: a bias shared along the seam (the edge rule's own, the scan's rounding of an edge)
is not in them (``VALIDATION.md``, "Gap and flush").

**What is not trusted, and why** (every station is kept, with ``notes`` in words): the gap where
either side is not measured up to its edge (``side_a`` / ``side_b`` is ``"missing"``: no surface
line; ``"filled"``: the walk ran into filled faces; ``"bridged"``), where the rule's edge was not
found on a side, or where the reading differs from the median of the stations within
:data:`NEIGHBOUR_REACH_MM` by more than :data:`NEIGHBOUR_GAP_MM` (a ragged or missing edge, a
hole near it); the flush where a surface line is missing or the reading differs from its
neighbours by more than :data:`NEIGHBOUR_FLUSH_MM`. Lowest, mean and highest along the seam are
over the trusted stations.

**Is the gap offered?** :data:`GAP_OFFERED` and :data:`GAP_NOTE` hold the answer of the measuring
task (FEATURE_PLAN I4's stop rule: gap only if its bias is the same within 0.2 mm across gaps of
2 to 6 mm); :class:`GapFlush` carries them, so the app shows flush alone, and says why, when the
gap is not offered. The gap is read in either case, for the record.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ohs.mesh.section import plane_from_point_normal, section
from ohs.mesh.topology import as_mesh

__all__ = ["GapFlush", "GapFlushStation", "SeamStats", "measure_gap_flush", "EDGE_RULES", "DEFAULT_RULE", "DROPS_MM",
           "GAP_OFFERED", "GAP_NOTE", "STEP_MM", "FORMAT_VERSION"]

FORMAT_VERSION = 1  # of GapFlush.to_dict()
DROPS_MM = (0.25, 0.5, 1.0)  # the "drop" edge rules: this far below the surface line
EDGE_RULES = ("end",) + tuple(f"drop_{d:g}" for d in DROPS_MM)
DEFAULT_RULE = "drop_0.5"
STEP_MM = 3.0  # a section every this many mm along the seam
FIT_MM = 12.0  # a surface line is fitted over this much of its panel ...
MARGIN_MM = 2.0  # ... ending this far inside the panel's edge (off the rounded edge)
REACH_MM = 10.0  # section points further than this from the station's height (along the normal) are not a panel
DEEP_MM = 3.0  # the walk to the edge stops this far below the surface line
TURN_BACK_MM = 1.0  # ... or when the profile turns back this far (round under a hem)
MIN_POINTS = 6  # a surface line needs at least this many points
FACING_COS = 0.5  # a surface point's face must face within 60° of panel A's normal
SLOPE_FLOOR = 0.2  # an edge's ± divides its height error by the profile's slope there, at least this
CLICK_REACH_MM = 3.0  # a click must lie this close to the mesh
CLICK_PATCH_MM = 5.0  # the faces this close to click A give its normal
SECTION_RADIUS_MM = 45.0  # each section cuts the faces this close to its station (enough for both windows)
MAX_MISSES = 3  # following stops after this many sections in a row without a surface
MAX_LENGTH_MM = 3000.0  # ... or this far from the clicks
NEIGHBOUR_REACH_MM = 25.0  # a reading is held against the median of the stations this close ...
NEIGHBOUR_GAP_MM = 0.5  # ... and not trusted when the gap differs from it by more than this ...
NEIGHBOUR_FLUSH_MM = 0.3  # ... or the flush by more than this
CORR_MAX_LAG = 8  # N_c sums the residuals' correlation over at most this many neighbours

# The answer of the measuring task (FEATURE_PLAN I4, VALIDATION.md "Gap and flush")
# (2026-11-17, simulated hemmed panels over a dark floor, GPU path, Standard quality): the scan closes
# every gap of 2 to 6 mm, tracked and at the true poses, with a shallow valley about 1 mm deep; no
# edge is measured, and the readings of the valley move by 0.8 to 1.3 mm against the truth across
# the gap sizes, against a rule of 0.2 mm. Flush reads within 0.04 mm on average.
GAP_OFFERED = False
GAP_NOTE = ("Gap is not shown: the scan closes a narrow gap over a dark background (the cameras' depth runs on "
            "across it), so the panels' edges are not measured. On simulated seams of 2 to 6 mm its reading was "
            "1 to 2 mm off, and differently for each gap size. Flush is read from the two surfaces and is shown.")


# --------------------------------------------------------------------------- records
def _f(x) -> float | None:
    return None if x is None or not math.isfinite(float(x)) else float(x)


def _v(x) -> list[float] | None:
    return None if x is None else [float(t) for t in x]


@dataclass
class SeamStats:
    """Lowest, mean and highest of a reading over the trusted stations (mm), with where along the
    seam the lowest and highest are; None when no station is trusted."""

    lowest: float | None
    lowest_at_mm: float | None
    mean: float | None
    highest: float | None
    highest_at_mm: float | None
    n_trusted: int
    n_stations: int

    def to_dict(self) -> dict:
        return {"lowest": _f(self.lowest), "lowest_at_mm": _f(self.lowest_at_mm), "mean": _f(self.mean),
                "highest": _f(self.highest), "highest_at_mm": _f(self.highest_at_mm), "n_trusted": int(self.n_trusted),
                "n_stations": int(self.n_stations)}


@dataclass
class GapFlushStation:
    """One section across the seam (see the module docstring). Lengths in mm, points in the
    mesh's frame; None where a value could not be read."""

    position_mm: float  # along the seam from the clicks, + in ``direction``
    point: np.ndarray  # (3,) the seam's middle on panel A's surface line
    direction: np.ndarray  # (3,) the seam's direction here (the section's normal)
    normal: np.ndarray  # (3,) panel A's normal here
    gap_mm: float | None  # by the answer's rule
    gap_sigma_mm: float | None
    flush_mm: float | None  # + : panel B above panel A
    flush_sigma_mm: float | None
    edge_a: np.ndarray | None  # (3,) panel A's edge point by the answer's rule
    edge_b: np.ndarray | None
    side_a: str  # "measured", "filled", "bridged" or "missing"
    side_b: str
    bridged: bool  # the scan runs on across the gap here
    gap_trusted: bool
    flush_trusted: bool
    notes: list[str] = field(default_factory=list)  # why a reading is not trusted, in words
    gap_by_rule: dict = field(default_factory=dict)  # every edge rule's gap (mm or None)
    n_points_a: int = 0  # points on each surface line
    n_points_b: int = 0
    rms_a_mm: float | None = None  # their scatter about it
    rms_b_mm: float | None = None

    def to_dict(self) -> dict:
        return {"position_mm": float(self.position_mm), "point": _v(self.point), "direction": _v(self.direction),
                "normal": _v(self.normal), "gap_mm": _f(self.gap_mm), "gap_sigma_mm": _f(self.gap_sigma_mm),
                "flush_mm": _f(self.flush_mm), "flush_sigma_mm": _f(self.flush_sigma_mm), "edge_a": _v(self.edge_a),
                "edge_b": _v(self.edge_b), "side_a": self.side_a, "side_b": self.side_b, "bridged": bool(self.bridged),
                "gap_trusted": bool(self.gap_trusted), "flush_trusted": bool(self.flush_trusted),
                "notes": list(self.notes), "gap_by_rule": {k: _f(v) for k, v in self.gap_by_rule.items()},
                "n_points_a": int(self.n_points_a), "n_points_b": int(self.n_points_b), "rms_a_mm": _f(self.rms_a_mm),
                "rms_b_mm": _f(self.rms_b_mm)}


@dataclass
class GapFlush:
    """Gap and flush along a seam: the stations in order along it and the readings over them."""

    stations: list[GapFlushStation]
    rule: str
    step_mm: float
    gap: SeamStats  # by ``rule``, over the stations whose gap is trusted
    flush: SeamStats
    gap_offered: bool  # GAP_OFFERED: whether the app shows the gap (module docstring)
    gap_note: str
    correlated_points: float  # N_c: how many neighbouring points err together (the ± are widened by its root)
    clicks: tuple[np.ndarray, np.ndarray]

    @property
    def gap_reading(self) -> SeamStats:
        """The gap over the trusted stations, whether or not it is offered."""
        return self.gap

    def to_dict(self) -> dict:
        """Plain JSON: the readings, the stations, and whether the gap is offered and why."""
        return {"format_version": FORMAT_VERSION, "units": "mm", "rule": self.rule, "step_mm": float(self.step_mm),
                "flush_sign": "+ : the second clicked panel stands above the first, square to the first",
                "gap": self.gap.to_dict(), "flush": self.flush.to_dict(), "gap_offered": bool(self.gap_offered),
                "gap_note": self.gap_note, "correlated_points": float(self.correlated_points),
                "clicks": [_v(c) for c in self.clicks], "stations": [s.to_dict() for s in self.stations]}


# --------------------------------------------------------------------------- one section
@dataclass
class _Line:
    """A section line in a station's axes."""

    S: np.ndarray  # (k,) across the seam, A -> B
    H: np.ndarray  # (k,) along A's normal
    P3: np.ndarray  # (k,3)
    sig: np.ndarray | None
    filled: np.ndarray  # (segments,)
    ok: np.ndarray  # (k,) the point is on a measured face that faces the scanned side
    closed: bool


@dataclass
class _Fit:
    a: float  # h at s0
    b: float  # slope dh/ds
    s0: float
    var_a: float  # before N_c
    var_b: float
    rms: float
    n: int
    z: np.ndarray  # the inliers' residuals over their sigma, in order of s (for N_c)

    def at(self, s):
        return self.a + self.b * (np.asarray(s, dtype=np.float64) - self.s0)

    def var_at(self, s, n_c: float = 1.0) -> float:
        return float(n_c * (self.var_a + (float(s) - self.s0) ** 2 * self.var_b))

    def depth(self, s, h):
        """How far points lie below the line (away from the scanned side), square to it."""
        return (self.at(s) - np.asarray(h, dtype=np.float64)) / math.sqrt(1.0 + self.b * self.b)


@dataclass
class _Edge:
    s: float
    h: float
    p3: np.ndarray
    sig: float | None  # the point's own sigma (None: not known)
    slope: float | None  # how steeply the profile falls there (None for "end")


@dataclass
class _Side:
    fit: _Fit
    status: str  # "measured", "filled", "bridged"
    reason: str  # why the walk stopped
    edges: dict  # rule -> _Edge | None
    walk_s: np.ndarray  # the walked points' s and depth (for a bridged section's middle)
    walk_depth: np.ndarray


def _wls(s, h, w, sigma_given: bool) -> tuple[_Fit, np.ndarray] | None:
    sw = float(w.sum())
    if len(s) < 2 or not sw > 0:
        return None
    s0 = float((w * s).sum() / sw)
    ds = s - s0
    sxx = float((w * ds * ds).sum())
    if not sxx > 1e-12:
        return None
    a = float((w * h).sum() / sw)
    b = float((w * ds * h).sum() / sxx)
    r = h - (a + b * ds)
    dof = max(len(s) - 2, 1)
    chi2 = float((w * r * r).sum()) / dof
    scale = max(chi2, 1.0) if sigma_given else chi2
    rms = math.sqrt(float((w * r * r).sum()) / sw)
    order = np.argsort(s, kind="stable")
    z = (r * np.sqrt(w))[order]
    return _Fit(a, b, s0, scale / sw, scale / sxx, rms, len(s), z), r


def _fit_side(lines: list[_Line], sign: float, q_edge: float, sigma_given: bool):
    """Panel's surface line from the points whose ``q = sign * s`` lies in its window (module
    docstring). Returns ``(fit, line index, point index)`` of its inlier nearest the seam, or None."""
    lo, hi = q_edge - MARGIN_MM - FIT_MM, q_edge - MARGIN_MM
    if not lines:
        return None
    li, pi = [], []
    for k, ln in enumerate(lines):
        q = sign * ln.S
        m = np.flatnonzero(ln.ok & (q >= lo) & (q <= hi) & (np.abs(ln.H) <= REACH_MM))
        li.append(np.full(len(m), k))
        pi.append(m)
    li, pi = np.concatenate(li), np.concatenate(pi)
    if len(pi) < MIN_POINTS:
        return None
    s = np.array([lines[k].S[i] for k, i in zip(li, pi)])
    h = np.array([lines[k].H[i] for k, i in zip(li, pi)])
    if sigma_given:
        sg = np.array([lines[k].sig[i] for k, i in zip(li, pi)])
        sg = np.where(np.isfinite(sg) & (sg > 0), sg, np.nan)
        sg = np.where(np.isfinite(sg), sg, np.nanmax(sg) if np.isfinite(sg).any() else 1.0)
        w = 1.0 / np.maximum(sg, 1e-3) ** 2
    else:
        sg = None
        w = np.ones(len(s))
    keep = np.ones(len(s), dtype=bool)
    out = None
    for _ in range(5):
        if keep.sum() < MIN_POINTS:
            return None
        out = _wls(s[keep], h[keep], w[keep], sigma_given)
        if out is None:
            return None
        fit, _ = out
        r = h - fit.at(s)
        rk = r[keep]
        mad = 1.4826 * float(np.median(np.abs(rk - np.median(rk))))
        thr = max(3.0 * mad, 3.0 * float(np.median(sg[keep])) if sg is not None else 0.0, 0.05)
        new = np.abs(r) <= thr
        if np.array_equal(new, keep):
            break
        keep = new
    if keep.sum() < MIN_POINTS or out is None:
        return None
    fit = out[0]
    counts = np.bincount(li[keep], minlength=len(lines))
    best = int(np.argmax(counts))
    sel = keep & (li == best)
    cand = pi[sel]
    start = int(cand[np.argmax(sign * lines[best].S[cand])])
    return fit, best, start


def _walk(ln: _Line, start: int, sign: float, fit: _Fit, far: float) -> tuple[list[int], str]:
    """Walk along a section line from ``start`` towards the other panel (module docstring)."""
    k = len(ln.S)
    q = sign * ln.S

    def nxt(i, d):
        j = i + d
        if ln.closed:
            return j % k
        return j if 0 <= j < k else None

    cands = [(q[j], d) for d in (1, -1) if (j := nxt(start, d)) is not None]
    if not cands:
        return [start], "end"
    _, d = max(cands)
    path, i, qmax = [start], start, q[start]
    while True:
        j = nxt(i, d)
        if j is None:
            return path, "end"
        if j == start:
            return path, "loop"
        seg = i if d > 0 else j
        if ln.filled[seg % len(ln.filled)]:
            return path, "filled"
        path.append(j)
        if q[j] > far:
            return path, "bridged"
        dep = float(fit.depth(ln.S[j], ln.H[j]))
        if dep > DEEP_MM:
            return path, "deep"
        if q[j] < qmax - TURN_BACK_MM:
            return path, "turned"
        qmax = max(qmax, q[j])
        i = j
        if len(path) > k + 1:
            return path, "loop"


def _edges(ln: _Line, path: list[int], reason: str, sign: float, fit: _Fit) -> dict:
    """Each rule's edge point on a walked path (module docstring)."""
    idx = np.asarray(path)
    S, H = ln.S[idx], ln.H[idx]
    q = sign * S
    dep = fit.depth(S, H)
    sig = ln.sig[idx] if ln.sig is not None else None
    out: dict = {}
    if reason in ("bridged", "filled"):
        out["end"] = None
    else:
        ok = dep <= DEEP_MM
        m = int(np.flatnonzero(ok)[np.argmax(q[ok])]) if ok.any() else 0
        out["end"] = _Edge(float(S[m]), float(H[m]), ln.P3[idx[m]].copy(), None if sig is None else float(sig[m]), None)
    def crossing(level: float, first: int = 0) -> tuple[int, float] | None:
        """The first segment (from ``first`` on) where the walk goes down through ``level``."""
        for m in range(first, len(idx) - 1):
            if dep[m] < level <= dep[m + 1]:
                return m, float((level - dep[m]) / (dep[m + 1] - dep[m]))
        return None

    def q_at(c: tuple[int, float]) -> float:
        m, t = c
        return float(q[m] + t * (q[m + 1] - q[m]))

    for d in DROPS_MM:
        e = None
        c = crossing(d)
        if c is not None:
            m, t = c
            p3 = ln.P3[idx[m]] + t * (ln.P3[idx[m + 1]] - ln.P3[idx[m]])
            sg = None if sig is None else float(sig[m] + t * (sig[m + 1] - sig[m]))
            # how steeply the profile falls there: over the chord from half to one and a half times
            # the drop (one segment's slope would mostly measure the noise), else that segment's
            lo, hi = crossing(0.5 * d), crossing(1.5 * d, m)
            if lo is not None and hi is not None and q_at(hi) > q_at(lo):
                slope = float(d / (q_at(hi) - q_at(lo)))
            else:
                slope = float((dep[m + 1] - dep[m]) / max(abs(q[m + 1] - q[m]), 1e-9))
            e = _Edge(float(S[m] + t * (S[m + 1] - S[m])), float(H[m] + t * (H[m + 1] - H[m])), p3, sg, slope)
        out[f"drop_{d:g}"] = e
    return out


def _side(lines, sign, q_edge, far, sigma_given) -> _Side | None:
    got = _fit_side(lines, sign, q_edge, sigma_given)
    if got is None:
        return None
    fit, li, start = got
    ln = lines[li]
    path, reason = _walk(ln, start, sign, fit, far)
    status = "bridged" if reason == "bridged" else "filled" if reason == "filled" else "measured"
    idx = np.asarray(path)
    return _Side(fit, status, reason, _edges(ln, path, reason, sign, fit), ln.S[idx], fit.depth(ln.S[idx], ln.H[idx]))


def _edge_q(side: _Side | None, sign: float, rule: str) -> float | None:
    if side is None or side.status != "measured":
        return None  # a bridged or filled side's "edge" is not where its window should end: keep it where it was
    e = side.edges.get(rule) or side.edges.get("end")
    return None if e is None else sign * e.s


def _read_section(lines, qa, qb, rule, sigma_given):
    """Both sides of one section, read twice (module docstring). ``qa`` / ``qb``: where each
    side's edge is expected, in its own ``q`` (A: ``s``; B: ``-s``)."""
    A = B = None
    for _ in range(2):
        A = _side(lines, 1.0, qa, -(qb - MARGIN_MM), sigma_given)
        B = _side(lines, -1.0, qb, -(qa - MARGIN_MM), sigma_given)
        na, nb = _edge_q(A, 1.0, rule), _edge_q(B, -1.0, rule)
        qa = na if na is not None else qa
        qb = nb if nb is not None else qb
    return A, B, qa, qb


# --------------------------------------------------------------------------- the seam
def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _face_normals(V, F) -> np.ndarray:
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    return np.divide(n, ln, out=np.zeros_like(n), where=ln > 0)


@dataclass
class _Raw:
    """A station before the seam's N_c is known."""

    position: float
    point: np.ndarray
    t: np.ndarray
    n: np.ndarray
    A: _Side | None
    B: _Side | None
    s_c: float | None
    flush: float | None
    gaps: dict
    bridged: bool
    d: np.ndarray
    p: np.ndarray
    cos_a: float = 1.0


def _station(V, F, FN, sigma, filled, tree, p, t, n, qa, qb, rule, sigma_given):
    """Cut and read one section at ``p``; returns ``(raw or None, qa, qb)`` with the edges'
    expected places for the next section, relative to this section's seam middle."""
    t = _unit(t)
    n = _unit(n - (n @ t) * t)
    d = np.cross(t, n)
    faces = np.asarray(tree.query_ball_point(p, SECTION_RADIUS_MM), dtype=np.int64)
    if len(faces) == 0:
        return None, qa, qb
    Fs = F[faces]
    used, inv = np.unique(Fs, return_inverse=True)
    sec = section(V[used], inv.reshape(-1, 3), plane_from_point_normal(p, t),
                  sigma=None if sigma is None else sigma[used], filled=None if filled is None else filled[faces])
    lines = []
    for ln in sec.lines:
        D = ln.points3d - p
        gf = faces[ln.faces]
        seg_up = (FN[gf] @ n >= FACING_COS) & ~np.asarray(ln.filled, dtype=bool)
        k = len(ln.points3d)
        ok = np.zeros(k, dtype=bool)
        idx = np.arange(len(seg_up))
        ok[idx[seg_up]] = True
        ok[(idx[seg_up] + 1) % k] = True
        lines.append(_Line(D @ d, D @ n, ln.points3d, ln.sigma, np.asarray(ln.filled, dtype=bool), ok, bool(ln.closed)))
    A, B, qa2, qb2 = _read_section(lines, qa, qb, rule, sigma_given)
    if A is None and B is None:
        return None, qa, qb
    ea, eb = (A.edges.get(rule) if A else None), (B.edges.get(rule) if B else None)
    ga, gb = (A.edges.get("end") if A else None), (B.edges.get("end") if B else None)
    bridged = bool((A is not None and A.status == "bridged") or (B is not None and B.status == "bridged"))
    if ea is not None and eb is not None:
        s_c = 0.5 * (ea.s + eb.s)
    elif ga is not None and gb is not None:
        s_c = 0.5 * (ga.s + gb.s)
    elif bridged and A is not None and B is not None and A.status == "bridged":
        ref = 0.5 * (A.fit.at(A.walk_s) + B.fit.at(A.walk_s))  # the groove's deepest point below both lines
        h_walk = A.fit.at(A.walk_s) - A.walk_depth * math.sqrt(1 + A.fit.b ** 2)
        s_c = float(A.walk_s[int(np.argmax(ref - h_walk))])
    else:
        s_c = 0.5 * (qa2 - qb2) if (A is None) == (B is None) else 0.0
    fit0 = A.fit if A is not None else B.fit
    point = p + s_c * d + float(fit0.at(s_c)) * n
    flush = None
    cos_a = 1.0
    gaps: dict = {}
    if A is not None and B is not None:
        bA = A.fit.b
        cos_a = 1.0 / math.sqrt(1.0 + bA * bA)
        nx, ny = -bA * cos_a, cos_a
        h0 = float(A.fit.at(s_c))
        flush = (float(B.fit.at(s_c)) - h0) / (ny - B.fit.b * nx)
        ux, uy = cos_a, bA * cos_a
        for r in EDGE_RULES:
            e1, e2 = A.edges.get(r), B.edges.get(r)
            gaps[r] = None if e1 is None or e2 is None else (e2.s - e1.s) * ux + (e2.h - e1.h) * uy
    else:
        gaps = {r: None for r in EDGE_RULES}
    nA = _unit(-A.fit.b * d + n) if A is not None else n
    raw = _Raw(0.0, point, t, nA, A, B, s_c, flush, gaps, bridged, d, p, cos_a)
    # the edges' places relative to this middle, for the next section
    return raw, qa2 - s_c, qb2 + s_c


def _pooled_nc(raws: list[_Raw]) -> float:
    """N_c from the surface lines' residuals, pooled over all sections (module docstring)."""
    seqs = [side.fit.z for r in raws for side in (r.A, r.B) if side is not None and len(side.fit.z) > 2]
    if not seqs:
        return 1.0
    den = sum(float(z @ z) for z in seqs)
    if not den > 0:
        return 1.0
    nc = 1.0
    for lag in range(1, CORR_MAX_LAG + 1):
        num = sum(float(z[:-lag] @ z[lag:]) for z in seqs if len(z) > lag)
        rho = num / den
        if rho <= 0:
            break
        nc += 2.0 * rho
    return float(min(max(nc, 1.0), 50.0))


def _edge_sigma(side: _Side, e: _Edge, n_c: float, sigma_given: bool) -> float:
    sp = e.sig if (sigma_given and e.sig is not None) else side.fit.rms
    if e.slope is None:
        return float(sp)
    return math.sqrt(sp * sp + side.fit.var_at(e.s, n_c)) / max(abs(e.slope), SLOPE_FLOOR)


def _neighbour_flags(pos: np.ndarray, val: list, limit: float) -> list[float | None]:
    """How far each reading lies from the median of the readings within NEIGHBOUR_REACH_MM (None:
    no reading, or too few neighbours); the caller flags those beyond ``limit``."""
    out: list[float | None] = []
    have = np.array([v is not None for v in val])
    x = np.array([v if v is not None else np.nan for v in val], dtype=np.float64)
    for k in range(len(val)):
        if not have[k]:
            out.append(None)
            continue
        m = have & (np.abs(pos - pos[k]) <= NEIGHBOUR_REACH_MM)
        m[k] = False
        if m.sum() < 2:
            out.append(None)
            continue
        out.append(float(x[k] - np.median(x[m])))
    return out


def _stats(pos, val, trusted) -> SeamStats:
    m = [k for k in range(len(val)) if trusted[k] and val[k] is not None]
    if not m:
        return SeamStats(None, None, None, None, None, 0, len(val))
    x = np.array([val[k] for k in m])
    lo, hi = int(np.argmin(x)), int(np.argmax(x))
    return SeamStats(float(x[lo]), float(pos[m[lo]]), float(x.mean()), float(x[hi]), float(pos[m[hi]]), len(m), len(val))


_SIDE_WORDS = {"missing": "has no surface to fit here", "filled": "runs into filled-in (not measured) surface before its edge",
               "bridged": "runs on across the gap: the scan has closed it here"}


def measure_gap_flush(V, F, click_a, click_b, *, sigma=None, filled=None, step_mm: float = STEP_MM,
                      rule: str = DEFAULT_RULE, max_length_mm: float = MAX_LENGTH_MM) -> GapFlush:
    """Gap and flush along the seam between the panel of ``click_a`` (panel A) and that of
    ``click_b`` (panel B): see the module docstring.

    ``V``, ``F``: the mesh (mm); ``sigma``: per-vertex 1σ (mm, the PLY's quality); ``filled``:
    per-face flag of filled-in faces. The clicks are points on the mesh (within
    :data:`CLICK_REACH_MM`), one on each panel near the seam. ValueError for a click off the
    mesh, clicks that are too close together, an unknown ``rule``, or when no surface is found
    between the clicks."""
    from scipy.spatial import cKDTree

    if rule not in EDGE_RULES:
        raise ValueError(f"unknown edge rule {rule!r}; the rules are {', '.join(EDGE_RULES)}")
    V, F = as_mesh(V, F)
    if len(F) == 0:
        raise ValueError("the mesh has no faces")
    if sigma is not None:
        sigma = np.asarray(sigma, dtype=np.float64).reshape(-1)
        if len(sigma) != len(V):
            raise ValueError(f"{len(sigma)} sigma values for {len(V)} vertices")
    if filled is not None:
        filled = np.asarray(filled, dtype=bool).reshape(-1)
        if len(filled) != len(F):
            raise ValueError(f"{len(filled)} filled flags for {len(F)} faces")
    step = float(step_mm)
    if not (math.isfinite(step) and step > 0):
        raise ValueError("the step along the seam must be a positive number of mm")
    a = np.asarray(click_a, dtype=np.float64).reshape(3)
    b = np.asarray(click_b, dtype=np.float64).reshape(3)
    vtree = cKDTree(V)
    for c, name in ((a, "first"), (b, "second")):
        dist, _ = vtree.query(c)
        if not dist <= CLICK_REACH_MM:
            raise ValueError(f"the {name} click is {dist:.1f} mm from the mesh: click on the panel's surface")
    if np.linalg.norm(b - a) < 2.0:
        raise ValueError("the two clicks are too close together: click one on each panel, either side of the seam")
    FN = _face_normals(V, F)
    C = V[F].mean(axis=1)
    tree = cKDTree(C)
    near = np.asarray(tree.query_ball_point(a, CLICK_PATCH_MM), dtype=np.int64)
    if len(near) == 0:
        raise ValueError("no faces around the first click")
    areas = np.linalg.norm(np.cross(V[F[near, 1]] - V[F[near, 0]], V[F[near, 2]] - V[F[near, 0]]), axis=1)
    n0 = _unit((FN[near] * areas[:, None]).sum(axis=0))
    across = (b - a) - float((b - a) @ n0) * n0
    if np.linalg.norm(across) < 1.0:
        raise ValueError("the clicks lie one above the other: click one on each panel, either side of the seam")
    d0 = _unit(across)
    t0 = _unit(np.cross(n0, d0))
    p0 = 0.5 * (a + b)
    sigma_given = sigma is not None
    qa0 = float((a - p0) @ d0) + MARGIN_MM  # each window ends at its click
    qb0 = -float((b - p0) @ d0) + MARGIN_MM

    raw0, qa_rel, qb_rel = _station(V, F, FN, sigma, filled, tree, p0, t0, n0, qa0, qb0, rule, sigma_given)
    if raw0 is None or raw0.A is None or raw0.B is None:
        raise ValueError("no seam found between the clicks: each panel needs some measured surface between its click "
                         "and the seam")
    raws = [raw0]
    for sgn in (1.0, -1.0):  # each section is one step further along the seam than the one before
        qa, qb = qa_rel, qb_rel
        pos = 0.0
        misses = 0
        t, n = raw0.t, raw0.n
        centre_prev = raw0.point
        p = raw0.point + sgn * step * t
        while abs(pos) + step <= max_length_mm:
            pos += sgn * step
            raw, qa_n, qb_n = _station(V, F, FN, sigma, filled, tree, p, t, n, qa, qb, rule, sigma_given)
            if raw is None:
                misses += 1
                if misses >= MAX_MISSES:
                    break
                p = p + sgn * step * t
                continue
            misses = 0
            raw.position = pos
            raws.append(raw)
            qa, qb = qa_n, qb_n
            n = raw.n
            moved = raw.point - centre_prev
            moved = moved - float(moved @ n) * n
            if np.linalg.norm(moved) > 0.5 * step:
                t = _unit(raw.t + sgn * _unit(moved))
            t = _unit(t - float(t @ n) * n)
            centre_prev = raw.point
            p = raw.point + sgn * step * t
    raws.sort(key=lambda r: r.position)
    return _finish(raws, rule, step, sigma_given, (a, b))


def _finish(raws: list[_Raw], rule: str, step: float, sigma_given: bool, clicks) -> GapFlush:
    """The ± values, the trust flags and the readings along the seam."""
    n_c = _pooled_nc(raws)
    pos = np.array([r.position for r in raws])
    stations: list[GapFlushStation] = []
    for r in raws:
        A, B = r.A, r.B
        notes: list[str] = []
        side_a = "missing" if A is None else A.status
        side_b = "missing" if B is None else B.status
        for nm, sd in (("first", side_a), ("second", side_b)):
            if sd != "measured":
                notes.append(f"The {nm} panel {_SIDE_WORDS[sd]}.")
        flush_sigma = None
        if A is not None and B is not None and r.flush is not None:
            flush_sigma = math.sqrt(A.fit.var_at(r.s_c, n_c) + B.fit.var_at(r.s_c, n_c)) * r.cos_a
        ea = A.edges.get(rule) if A is not None else None
        eb = B.edges.get(rule) if B is not None else None
        gap = r.gaps.get(rule)
        gap_sigma = None
        if gap is not None:
            gap_sigma = math.sqrt(_edge_sigma(A, ea, n_c, sigma_given) ** 2 + _edge_sigma(B, eb, n_c, sigma_given) ** 2)
        for nm, sd, e in (("first", A, ea), ("second", B, eb)):
            if sd is not None and sd.status == "measured" and e is None and rule != "end":
                d = rule.split("_", 1)[1]
                notes.append(f"The {nm} panel's profile ends before it drops {d} mm below its surface: its edge is "
                             f"not measured by this rule.")
        stations.append(GapFlushStation(
            position_mm=float(r.position), point=r.point, direction=r.t, normal=r.n, gap_mm=gap, gap_sigma_mm=gap_sigma,
            flush_mm=r.flush, flush_sigma_mm=flush_sigma, edge_a=None if ea is None else ea.p3,
            edge_b=None if eb is None else eb.p3, side_a=side_a, side_b=side_b, bridged=r.bridged,
            gap_trusted=bool(gap is not None and side_a == "measured" and side_b == "measured" and not r.bridged),
            flush_trusted=bool(r.flush is not None), notes=notes, gap_by_rule=dict(r.gaps),
            n_points_a=0 if A is None else A.fit.n, n_points_b=0 if B is None else B.fit.n,
            rms_a_mm=None if A is None else A.fit.rms, rms_b_mm=None if B is None else B.fit.rms))
    gd = _neighbour_flags(pos, [s.gap_mm if s.gap_trusted else None for s in stations], NEIGHBOUR_GAP_MM)
    fd = _neighbour_flags(pos, [s.flush_mm if s.flush_trusted else None for s in stations], NEIGHBOUR_FLUSH_MM)
    for s, g, f in zip(stations, gd, fd):
        if g is not None and abs(g) > NEIGHBOUR_GAP_MM:
            s.gap_trusted = False
            s.notes.append(f"The gap differs from its neighbours' by {g:+.2f} mm: a ragged or missing edge.")
        if f is not None and abs(f) > NEIGHBOUR_FLUSH_MM:
            s.flush_trusted = False
            s.notes.append(f"The flush differs from its neighbours' by {f:+.2f} mm.")
    gap = _stats(pos, [s.gap_mm for s in stations], [s.gap_trusted for s in stations])
    flush = _stats(pos, [s.flush_mm for s in stations], [s.flush_trusted for s in stations])
    return GapFlush(stations, rule, step, gap, flush, GAP_OFFERED, GAP_NOTE, n_c, clicks)
