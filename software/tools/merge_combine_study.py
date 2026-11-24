"""A merge that weighs each scan by its own sigma, and a pose from tags where little is shared (ENGINE_PLAN E-D3
item 4: the first tests of C1 and C3 in the design note ``D:\\OpenHandScan-work\\scratch\\ed3\\merge.md``).

Nothing here fuses, tracks or renders: it reads the saved single-scan meshes and the app's merged meshes of the
two merge studies (``tools/merge_accuracy_study.py``, X7: pairs P1-P5; ``tools/merge_overlap_sweep.py``'s merges
M1-M7), on the processor.

    python tools/merge_combine_study.py combine OUT --x7 DIR --mergewarn DIR --recs DIR [--cases P1:a ...]
    python tools/merge_combine_study.py tags OUT --x7 DIR --mergewarn DIR --recs DIR [--pairs P3 P5 M1 M3]
    python tools/merge_combine_study.py table OUT

(run from ``software/``; ``--x7`` / ``--mergewarn``: the studies' folders holding ``work``; ``--recs``: the
scorecard cache's ``recs``.)

**combine (C1).** Each scan's mesh, its sigma and the app's merged mesh, all with the Default recipe. Each single
is placed by the part alone, by itself (as a user sees it). The two singles together, B at the merge's own pose,
are placed jointly (as a merged scan is). On the true surface the two scans saw (``seen_points``: every third
frame's rays at the true poses; per scan, so that each scan's own surface ``S_i`` is known), each mesh is read
along the true normal: the nearest vertex within :data:`RADIUS` whose normal agrees (:data:`MIN_DOT`; the two faces
of a thin wall stay apart) gives a signed error and its sigma; none: no surface there. Where both singles have
surface the two readings are combined (:func:`combine`) by a rule: ``equal`` (each scan alike: the emulation of
today's fusion), ``ivw`` (1/sigma^2) and ``soft`` (the better view takes most of the weight where the two sigmas
differ clearly: :data:`SOFT_RATIO`) and ``capped`` (1/sigma^2, no view more than :data:`CAP_RATIO` times another). The app's merge itself is the row ``merge``. Judged by two rules:

* **E-D4** (the restated rule, decided 2026-11-23): (1) on each scan's own seen surface the merge is no worse than
  that scan (share within 0.5 mm, median distance, share more than 0.3 mm; absent counts as :data:`CAP` mm);
  (2) no thin wall reads thicker (by the two faces' errors along their normals: :func:`wall_errors`); (3) it holds
  more of the seen surface than the larger scan. Part (4) (a pair that shares under 0.70 is aligned from tags) is
  the ``tags`` command's.
* **X7's rule** (the old one): "beats the better single scan" (median and share more than 0.3 mm over what the
  mesh holds, against the better scan over what it holds; no worse beyond the floors, one better beyond them)
  and "reaches the union" (the share within 0.5 mm of the pooled seen surface at least the union's, the union
  being either single within 0.5 mm: an oracle, reported as a ceiling).

The floors (:data:`FLOOR_MED`, :data:`FLOOR_SHARE`) are X7's: a determinism floor, not a spread across seeds.

**The merged sigma.** For a combination, :func:`combine` gives two: the mixture's
``sqrt(a sA^2 + (1-a) sB^2 + a (1-a) (eA - eB)^2)`` (``a``: A's share of the weight; no credit for two scans
being independent, plus their disagreement, which needs no truth), and the naive inverse-variance
``1 / sqrt(1/sA^2 + 1/sB^2)``. Each is checked as a scan's is: the share of what it holds with the true error
within twice its sigma.

**tags (C3).** Tags on the table round the part (:func:`table_tags`), seen by a frame when in view, 150-450 mm
away and looked at from within 70 degrees of their normal (the part's shadow is not modelled). Each sighting's
corners are the true corners in the true camera plus noise along the camera's x / y / z
(:data:`TAG_NOISE_MM`, measured on single frames for a 25 mm tag: ``scratch\\x8\\NOTES.md``, seed 3), fitted to
the printed square, and put into the scan's world by that frame's *tracked* pose, as :func:`ohs.align.tags.tag_map`
does; a tag's corners are the median of its sightings. With ``correlated`` the noise is held for
:data:`CORRELATED_RUN` frames at a time (frames 0.1 s apart see the same dots). The pose comes from
:func:`ohs.align.tags.pose_from_tags`; then the app's refinement (``align_pair(init=..., init_from="tags")``) is run
from it; the merge at each pose is stood in for by the ``equal`` and ``ivw`` combinations, judged as above.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

SOFTWARE = Path(__file__).resolve().parents[1]
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))

RADIUS = 1.5  # mm: how far from a true surface point a mesh's vertex may be read
MIN_DOT = 0.5  # a vertex counts only when its normal agrees with the true one this well
GOOD = 0.5  # mm: "covered"
OVER = 0.3  # mm: the share more than this far off
CAP = 5.0  # mm: the distance an absent surface counts as
FLOOR_MED, FLOOR_SHARE = 0.003, 0.005  # X7's floors (a determinism floor, not a spread across seeds)
SOFT_RATIO = 2.0  # soft winner: at this sigma ratio the better view has 0.5 + 0.45 tanh(1) = 84 % of the weight
CAP_RATIO = 4.0  # capped: no view counts more than this many times the other (an outside review, 2026-11-23)
RULES = ("equal", "ivw", "soft", "capped")
THIN_MM = 3.0  # walls of this true thickness or less are the thin ones (E-D4 part 2; the kill rule)
WALL_ALARM = 0.05  # mm: thicker than today's merge by this much is an alarm
WALL_MIN_N = 40  # samples needed on each face for a wall reading
TAG_MM = 25.0
TAG_NOISE_MM = (0.20, 0.15, 0.36)  # a single frame's corner error, RMS along the camera's x / y / z
CORRELATED_RUN = 10
#: the app's pose variant per pair, and which pairs the pose ruined (fixed by the merge-warning study's table)
APP_VARIANT = {"P1": "a", "P2": "a", "P3": "a", "P4": "p", "P5": "p"}
RUINED = ("P3", "P5", "M1", "M3")
#: the merge-warning study's merges (tools/merge_overlap_sweep.py's rows): scan A, scan B
MW_PAIRS = {
    "M1": ("bracket240-s0@0", "bracket240-s1@20"), "M2": ("bracket240-s2@140", "bracket240-s3@0"),
    "M3": ("sheet-s0@30", "sheet-s1@40"), "M4": ("sheet-s0@110", "sheet-s2@0"),
    "M5": ("holes-low-s1@10", "holes-low-s2@20"), "M6": ("sheet-s0@30", "sheet-s1@140"),
    "M7": ("sheet-s1@40", "sheet-s2@0"),
}


# =========================================================================== the combining rule
def weights(sA: np.ndarray, sB: np.ndarray, rule: str) -> tuple[np.ndarray, np.ndarray]:
    """A's and B's weights (summing to 1) where both scans have surface. ``equal``: alike; ``ivw``: 1/sigma^2;
    ``soft``: the lower sigma takes ``0.5 + 0.45 tanh(ln(ratio) / ln(SOFT_RATIO))``; ``capped``: 1/sigma^2 with
    neither weight more than :data:`CAP_RATIO` times the other."""
    sA = np.maximum(np.asarray(sA, np.float64), 1e-6)
    sB = np.maximum(np.asarray(sB, np.float64), 1e-6)
    if rule == "equal":
        a = np.full(sA.shape, 0.5)
    elif rule == "ivw":
        a = sB ** 2 / (sA ** 2 + sB ** 2)
    elif rule == "capped":
        lo = 1.0 / (1.0 + CAP_RATIO)
        a = np.clip(sB ** 2 / (sA ** 2 + sB ** 2), lo, 1.0 - lo)
    elif rule == "soft":
        a = 0.5 + 0.45 * np.tanh(np.log(sB / sA) / np.log(SOFT_RATIO))
    else:
        raise ValueError(f"unknown rule {rule!r}")
    return a, 1.0 - a


def combine(eA, sA, eB, sB, rule: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Two scans' readings of one place (signed error along the normal and sigma; NaN: no surface there) combined
    by ``rule``: ``(error, mixture sigma, naive inverse-variance sigma)``. Where one scan alone has surface it is
    taken as it is (nothing seen once is lost); where neither has, NaN."""
    eA, sA, eB, sB = (np.asarray(x, np.float64) for x in (eA, sA, eB, sB))
    hA, hB = np.isfinite(eA), np.isfinite(eB)
    both = hA & hB
    e = np.where(hA, eA, eB)
    s = np.where(hA, sA, np.where(hB, sB, np.nan))
    s_ivw = s.copy()
    if both.any():
        a, b = weights(sA[both], sB[both], rule)
        x, y = eA[both], eB[both]
        e[both] = a * x + b * y
        s[both] = np.sqrt(a * sA[both] ** 2 + b * sB[both] ** 2 + a * b * (x - y) ** 2)
        s_ivw[both] = 1.0 / np.sqrt(1.0 / np.maximum(sA[both], 1e-6) ** 2 + 1.0 / np.maximum(sB[both], 1e-6) ** 2)
    return e, s, s_ivw


# =========================================================================== reading a mesh, scoring
def read_surface(P, N, S, X, Xn, radius: float = RADIUS, min_dot: float = MIN_DOT) -> tuple[np.ndarray, np.ndarray]:
    """At each true point ``X`` (normal ``Xn``): the signed distance along ``Xn`` of the nearest of the mesh's
    vertices ``P`` (normals ``N``, sigma ``S``) within ``radius`` whose normal agrees, and its sigma; NaN where none."""
    from scipy.spatial import cKDTree

    X = np.asarray(X, np.float64)
    e = np.full(len(X), np.nan)
    s = np.full(len(X), np.nan)
    if not len(P) or not len(X):
        return e, s
    d, i = cKDTree(P).query(X, k=min(8, len(P)), distance_upper_bound=radius)
    d, i = d.reshape(len(X), -1), i.reshape(len(X), -1)
    ii = np.minimum(i, len(P) - 1)
    ok = np.isfinite(d) & (np.einsum("ikj,ij->ik", np.asarray(N)[ii], Xn) > min_dot)
    first = np.argmax(ok, axis=1)
    rows = np.arange(len(X))
    have = ok[rows, first]
    j = ii[rows, first][have]
    e[have] = np.einsum("ij,ij->i", np.asarray(P)[j] - X[have], Xn[have])
    s[have] = np.asarray(S)[j]
    return e, s


def region_stats(e: np.ndarray) -> dict:
    """Over a region's samples (absent: :data:`CAP`): share within :data:`GOOD`, median distance, share over
    :data:`OVER`."""
    d = np.where(np.isfinite(e), np.minimum(np.abs(e), CAP), CAP)
    if not len(d):
        return {"cover05": None, "med": None, "over03": None, "n": 0}
    return {"cover05": float(np.mean(d < GOOD)), "med": float(np.median(d)), "over03": float(np.mean(d > OVER)),
            "n": int(len(d))}


def held_stats(e: np.ndarray) -> dict:
    """Over what a mesh holds (X7's "median and share over the mesh's own surface", by samples)."""
    x = np.abs(e[np.isfinite(e)])
    return {"med": float(np.median(x)) if len(x) else None, "over03": float(np.mean(x > OVER)) if len(x) else None,
            "n": int(len(x))}


def no_worse(m: dict, s: dict, keys=("cover05", "med", "over03")) -> bool:
    """``m`` no worse than ``s`` beyond the floors (higher cover05 is better, lower median and share)."""
    ok = True
    if "cover05" in keys:
        ok &= m["cover05"] >= s["cover05"] - FLOOR_SHARE
    if "med" in keys:
        ok &= m["med"] <= s["med"] + FLOOR_MED
    if "over03" in keys:
        ok &= m["over03"] <= s["over03"] + FLOOR_SHARE
    return bool(ok)


def beats(m: dict, s: dict) -> bool:
    """X7's "beats": median and share both no worse beyond the floors, one better beyond them."""
    if m["med"] is None or s["med"] is None:
        return False
    return (no_worse(m, s, ("med", "over03"))
            and (m["med"] < s["med"] - FLOOR_MED or m["over03"] < s["over03"] - FLOOR_SHARE))


def two_sigma_cover(e: np.ndarray, s: np.ndarray) -> float | None:
    """The share of what is held whose true error is within twice its sigma."""
    h = np.isfinite(e) & np.isfinite(s)
    return float(np.mean(np.abs(e[h]) <= 2.0 * s[h])) if h.any() else None


def wall_errors(prim_idx: np.ndarray, Xn: np.ndarray, e: np.ndarray, walls: list[dict]) -> dict:
    """Thickness error of each wall (a box: ``{"name", "axis" (unit, world), "true_mm"}``): the median error on
    its face along ``+axis`` plus that on its face along ``-axis`` (each signed outwards, so + is thicker),
    where both faces hold :data:`WALL_MIN_N` read samples; else None."""
    out = {}
    for k, w in enumerate(walls):
        m = (prim_idx == k) & np.isfinite(e)
        c = Xn @ np.asarray(w["axis"], np.float64)
        up, dn = m & (c > 0.9), m & (c < -0.9)
        out[w["name"]] = (float(np.median(e[up]) + np.median(e[dn]))
                          if up.sum() >= WALL_MIN_N and dn.sum() >= WALL_MIN_N else None)
    return out


def judge_case(eA, sA, eB, sB, rows: dict[str, tuple[np.ndarray, np.ndarray]], inA, inB) -> dict:
    """Both rules for every row (``name: (error, sigma)``, read at the same samples), against the singles
    ``eA`` / ``eB`` (each placed by itself), on the regions ``inA`` / ``inB`` (each scan's seen surface)."""
    U = inA | inB
    dA = np.where(np.isfinite(eA), np.abs(eA), np.inf)
    dB = np.where(np.isfinite(eB), np.abs(eB), np.inf)
    union = np.where(np.isfinite(np.minimum(dA, dB)), np.minimum(dA, dB), np.nan)
    sing = {"A": eA, "B": eB}
    reg = {"S_A": inA, "S_B": inB, "S_A or S_B": U}
    out = {"singles": {k: {r: region_stats(v[m]) for r, m in reg.items()} | {"held": held_stats(v[sing_m])}
                       for (k, v), sing_m in zip(sing.items(), (inA, inB))},
           "union oracle": region_stats(union[U]), "rows": {}}
    better = "A" if (out["singles"]["A"]["held"]["over03"] or 1) <= (out["singles"]["B"]["held"]["over03"] or 1) else "B"
    out["better single"] = better
    for name, (e, s) in rows.items():
        r = {k: region_stats(e[m]) for k, m in reg.items()}
        r["held"] = held_stats(e[U])
        r["E-D4 (1) on S_A"] = no_worse(r["S_A"], out["singles"]["A"]["S_A"])
        r["E-D4 (1) on S_B"] = no_worse(r["S_B"], out["singles"]["B"]["S_B"])
        larger = max(out["singles"]["A"]["S_A or S_B"]["cover05"], out["singles"]["B"]["S_A or S_B"]["cover05"])
        r["E-D4 (3) more than the larger scan"] = bool(r["S_A or S_B"]["cover05"] > larger)
        r["X7 beats the better single"] = beats(r["held"], out["singles"][better]["held"])
        r["X7 reaches the union"] = bool(r["S_A or S_B"]["cover05"] >= out["union oracle"]["cover05"] - FLOOR_SHARE)
        r["2 sigma covers"] = two_sigma_cover(e, s)
        out["rows"][name] = r
    for k, v in sing.items():
        out["singles"][k]["2 sigma covers"] = two_sigma_cover(v, sA if k == "A" else sB)
    return out


# =========================================================================== meshes, the truth
def vertex_normals(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    N = np.zeros_like(V)
    for k in range(3):
        np.add.at(N, F[:, k], n)
    return N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)


def recipe_mesh(path: Path):
    """The mesh with the Default recipe as the scorecard applies it: ``(V, F, N, S)``, the normals turned as the
    saved ones."""
    from ohs.mesh.order import canonical
    from ohs.track.export import load_ply
    from scipy.spatial import cKDTree
    from tools import preset_study as ps

    d = load_ply(path)
    c = canonical(np.asarray(d["vertices"], np.float64), np.asarray(d["faces"], np.int64))
    V, F, S, _, _ = ps.apply_ops(c.V, c.F, c.vertex_values(d["quality"]), ps.RECIPES["general"])
    N = vertex_normals(V, F)
    N0 = np.asarray(c.vertex_values(d["normals"]), np.float64)
    j = cKDTree(c.V).query(V)[1]
    if np.median(np.einsum("ij,ij->i", N, N0[j])) < 0:
        N = -N
    return V, F, N, np.asarray(S, np.float64)


def walls_of(sc) -> list[dict]:
    """Each primitive as a wall across its thinnest axis (boxes; others get no axis and no reading)."""
    out = []
    for p in sc.primitives:
        half = getattr(p, "half", None)
        if half is None:
            out.append({"name": p.name, "axis": [0.0, 0.0, 0.0], "true_mm": None})
            continue
        k = int(np.argmin(half))
        out.append({"name": p.name, "axis": np.asarray(p.R)[:, k].tolist(), "true_mm": float(2 * half[k])})
    return out


class Study:
    """Where the saved meshes are (X7's and the merge-warning study's work folders) and the recordings."""

    def __init__(self, x7: Path, mergewarn: Path, recs: Path) -> None:
        from tools import merge_accuracy_study as MS

        self.MS, self.SC = MS, MS.SC
        self.x7, self.mw, self.recs = Path(x7) / "work", Path(mergewarn) / "work", Path(recs)

    def keys(self, pair: str) -> tuple[str, str]:
        return MW_PAIRS[pair] if pair in MW_PAIRS else self.MS.PAIRS[pair][:2]

    def work(self, pair: str) -> Path:
        return self.mw if pair in MW_PAIRS else self.x7

    def merge_dir(self, pair: str, v: str) -> Path:
        return self.work(pair) / "pairs" / pair / f"merge_{v}"

    def app_variant(self, pair: str) -> str:
        return APP_VARIANT.get(pair, "a")

    def setup_keys(self, pair: str, keys: tuple[str, str], poses: str = "tracked") -> dict:
        """:meth:`setup` for a pair named here only (the experiment's spread pairs)."""
        MW_PAIRS.setdefault(pair, tuple(keys))
        return self.setup(pair, poses)

    def setup(self, pair: str, poses: str) -> dict:
        """The scene, its truth, the samples each scan saw, the singles (recipe applied) and their worlds."""
        from tools import preset_study as ps
        from tools.calib_health_study import Truth

        MS, SC = self.MS, self.SC
        ka, kb = self.keys(pair)
        spec, sa, ea = MS.scan_spec(ka)
        _, sb, eb = MS.scan_spec(kb)
        sc = SC.scene_of(spec)
        truth = SC.truth_for(spec, sc)
        ra, rb = self.recs / ka.split("@")[0], self.recs / kb.split("@")[0]
        A, B = MS.scan_dir(self.work(pair), ka, poses), MS.scan_dir(self.work(pair), kb, poses)
        Wa = np.eye(4) if poses == "true" else MS.fitted_world(ra, A / "trajectory.csv")
        Wb = np.eye(4) if poses == "true" else MS.fitted_world(rb, B / "trajectory.csv")
        SA = ps.seen_points(sc, ra, sa, ea, every=3, n_max=60000)
        SB = ps.seen_points(sc, rb, sb, eb, every=3, n_max=60000)
        X0 = np.vstack([SA, SB])
        X, Xn = truth.closest_part(X0)
        from scipy.spatial import cKDTree

        inA = np.r_[np.ones(len(SA), bool), cKDTree(SA).query(SB)[0] < 0.7]
        inB = np.r_[cKDTree(SB).query(SA)[0] < 0.7, np.ones(len(SB), bool)]
        try:
            tr = Truth(sc)
            prim = np.argmin(np.abs(tr.prim_sdf(X)), axis=1)
            names = list(tr.names)
            order = {p.name: k for k, p in enumerate(sc.primitives)}
            prim = np.array([order.get(names[k], -1) for k in prim])
        except Exception:  # noqa: BLE001  (a scene without primitives: no wall readings)
            prim = np.full(len(X), -1)
        mA, mB = recipe_mesh(A / "mesh.ply"), recipe_mesh(B / "mesh.ply")
        return {"pair": pair, "keys": (ka, kb), "spec": spec, "sc": sc, "truth": truth, "recs": (ra, rb),
                "dirs": (A, B), "W": (Wa, Wb), "X": X, "Xn": Xn, "inA": inA, "inB": inB, "prim": prim,
                "walls": walls_of(sc), "mA": mA, "mB": mB}

    def single_readings(self, st: dict) -> tuple:
        from ohs import geometry as G

        SC = self.SC
        out = []
        for (V, F, N, S), W in zip((st["mA"], st["mB"]), st["W"]):
            T, _ = SC.place_part(V, W, st["truth"])
            out.append(read_surface(G.transform_points(T, V), N @ T[:3, :3].T, S, st["X"], st["Xn"]))
        return out[0][0], out[0][1], out[1][0], out[1][1]

    def joint_readings(self, st: dict, T_b: np.ndarray) -> tuple:
        """A in its world and B at the pose ``T_b`` (into A's world), placed together as one merged scan."""
        from ohs import geometry as G

        (Va, _, Na, Sa), (Vb, _, Nb, Sb) = st["mA"], st["mB"]
        Vb2, Nb2 = G.transform_points(T_b, Vb), Nb @ np.asarray(T_b)[:3, :3].T
        T, _ = self.SC.place_part(np.vstack([Va, Vb2]), st["W"][0], st["truth"])
        R = T[:3, :3]
        eA, sA = read_surface(G.transform_points(T, Va), Na @ R.T, Sa, st["X"], st["Xn"])
        eB, sB = read_surface(G.transform_points(T, Vb2), Nb2 @ R.T, Sb, st["X"], st["Xn"])
        return eA, sA, eB, sB

    def merge_reading(self, st: dict, v: str) -> tuple:
        from ohs import geometry as G

        V, F, N, S = recipe_mesh(self.merge_dir(st["pair"], v) / "mesh.ply")
        T, _ = self.SC.place_part(V, st["W"][0], st["truth"])
        return read_surface(G.transform_points(T, V), N @ T[:3, :3].T, S, st["X"], st["Xn"])

    def merge_pose(self, pair: str, v: str) -> np.ndarray:
        return np.asarray(json.loads((self.merge_dir(pair, v) / "done.json").read_text())["T"][1], np.float64)


def walls_table(st: dict, rows: dict) -> dict:
    return {name: wall_errors(st["prim"], st["Xn"], e, st["walls"]) for name, (e, _) in rows.items()}


def thin_walls(st: dict) -> list[str]:
    return [w["name"] for w in st["walls"] if w["true_mm"] is not None and w["true_mm"] <= THIN_MM]


def run_combine(study: Study, out: Path, pair: str, v: str) -> dict:
    t0 = time.perf_counter()
    poses = "true" if v == "c" else "tracked"
    st = study.setup(pair, poses)
    eA1, sA1, eB1, sB1 = study.single_readings(st)
    eA, sA, eB, sB = study.joint_readings(st, study.merge_pose(pair, v))
    eM, sM = study.merge_reading(st, v)
    rows = {"merge (today)": (eM, sM)}
    for rule in RULES:
        e, s_mix, s_ivw = combine(eA, sA, eB, sB, rule)
        rows[rule] = (e, s_mix)
        rows[f"{rule} (naive sigma)"] = (e, s_ivw)
    res = judge_case(eA1, sA1, eB1, sB1, rows, st["inA"], st["inB"])
    res.update({"pair": pair, "variant": v, "ruined_by_pose": pair in RUINED, "keys": st["keys"],
                "walls": walls_table(st, {"A": (eA1, sA1), "B": (eB1, sB1), **rows}), "thin": thin_walls(st),
                "wall_true_mm": {w["name"]: w["true_mm"] for w in st["walls"]},
                "seconds": round(time.perf_counter() - t0, 1)})
    res["kill"] = kill_c1(res)
    _write(out / f"combine_{pair}_{v}.json", res)
    return res


def kill_c1(res: dict) -> dict:
    """C1's kill rule (fixed in the design note): dropped if inverse variance fails E-D4 (1) on a pair the pose
    did not ruin, or reads a thin wall 0.05 mm or more thicker than today's merge."""
    r = res["rows"]["ivw"]
    fails = [] if res["ruined_by_pose"] else [k for k in ("E-D4 (1) on S_A", "E-D4 (1) on S_B") if not r[k]]
    thick = []
    for w in res["thin"]:
        a, b = res["walls"]["ivw"].get(w), res["walls"]["merge (today)"].get(w)
        if a is not None and b is not None and a - b >= WALL_ALARM:
            thick.append(w)
    return {"fails": fails, "thicker walls": thick, "killed": bool(fails or thick)}


# =========================================================================== tags (C3)
def table_tags(n: int = 8, radius_mm: float = 110.0, size_mm: float = TAG_MM) -> dict[int, np.ndarray]:
    """``n`` tags flat on the table (z = 0, facing up) on a ring round the part: id -> corners (4, 3)."""
    out = {}
    h = size_mm / 2.0
    for i in range(n):
        a = 2 * np.pi * (i + 0.5) / n
        c = radius_mm * np.array([np.cos(a), np.sin(a), 0.0])
        u, w = np.array([np.cos(a), np.sin(a), 0.0]), np.array([-np.sin(a), np.cos(a), 0.0])
        out[i] = np.array([c - h * u - h * w, c + h * u - h * w, c + h * u + h * w, c - h * u + h * w])
    return out


def sees(T_wc: np.ndarray, X: np.ndarray, K: np.ndarray, size: tuple[int, int], margin_px: float = 20.0,
         depth: tuple[float, float] = (150.0, 450.0), max_deg: float = 70.0) -> bool:
    """Whether a camera at ``T_wc`` sees the tag ``X`` (corners, world): all corners in the image and range, and
    its face turned to the camera within ``max_deg`` (its normal: up)."""
    Xc = (X - T_wc[:3, 3]) @ T_wc[:3, :3]
    if np.any(Xc[:, 2] < depth[0]) or np.any(Xc[:, 2] > depth[1]):
        return False
    uv = Xc[:, :2] / Xc[:, 2:3] * np.array([K[0, 0], K[1, 1]]) + np.array([K[0, 2], K[1, 2]])
    if np.any(uv < margin_px) or np.any(uv[:, 0] > size[0] - margin_px) or np.any(uv[:, 1] > size[1] - margin_px):
        return False
    to_cam = T_wc[:3, 3] - X.mean(axis=0)
    return bool(to_cam[2] / np.linalg.norm(to_cam) > np.cos(np.radians(max_deg)))


def emulated_tag_map(tags: dict[int, np.ndarray], true_poses: dict[int, np.ndarray], tracked: dict[int, np.ndarray],
                     K: np.ndarray, size: tuple[int, int], noise_mm=TAG_NOISE_MM, rng=None, run: int = 1,
                     min_views: int = 2, outlier_mm: float = 2.0):
    """The tag map a scan would make (module docstring): ``(TagMap, sightings per tag)``."""
    from ohs.align.tags import TagMap
    from ohs.geometry import kabsch
    from ohs.track.markers import tag_corners_local

    rng = rng if rng is not None else np.random.default_rng(0)
    square = tag_corners_local(TAG_MM)
    obs: dict[int, list[np.ndarray]] = {}
    held: dict[int, np.ndarray] = {}
    frames = sorted(set(true_poses) & set(tracked))
    for k, f in enumerate(frames):
        Tt, Tk = true_poses[f], tracked[f]
        for i, X in tags.items():
            if not sees(Tt, X, K, size):
                continue
            if k % max(run, 1) == 0 or i not in held:
                held[i] = rng.normal(0.0, 1.0, (4, 3)) * np.asarray(noise_mm)
            Xc = (X - Tt[:3, 3]) @ Tt[:3, :3] + held[i]
            R, t = kabsch(square, Xc)
            Y = square @ R.T + t
            obs.setdefault(i, []).append(Y @ Tk[:3, :3].T + Tk[:3, 3])
    out = TagMap()
    for i, Xs in sorted(obs.items()):
        A = np.stack(Xs)
        med = np.median(A, axis=0)
        keep = np.linalg.norm(A - med, axis=2).max(axis=1) <= outlier_mm
        if keep.sum() < min_views:
            continue
        out.corners[i] = np.median(A[keep], axis=0)
        out.views[i] = int(keep.sum())
    return out, {i: len(v) for i, v in obs.items()}


def pose_off(T: np.ndarray, T_true: np.ndarray, P: np.ndarray) -> list[float]:
    from ohs import geometry as G

    d = np.linalg.norm(G.transform_points(T, P) - G.transform_points(T_true, P), axis=1)
    return [float(np.median(d)), float(np.percentile(d, 95))]


def run_tags(study: Study, out: Path, pair: str, seeds=(0, 1, 2), correlated=(False, True)) -> dict:
    from ohs.align import scan_cloud_from_mesh
    from ohs.align.pairwise import align_pair
    from ohs.align.tags import pose_from_tags
    from ohs.io.recording import RecordingReader
    from ohs.track.posegraph import read_trajectory

    t0 = time.perf_counter()
    st = study.setup(pair, "tracked")
    eA1, sA1, eB1, sB1 = study.single_readings(st)
    T_true = study.merge_pose(pair, "b2")
    T_app = study.merge_pose(pair, study.app_variant(pair))
    tags = table_tags()
    maps_in = []
    for (key, d), rec in zip(zip(st["keys"], st["dirs"]), st["recs"]):
        _, s, e = study.MS.scan_spec(key)
        reader = RecordingReader(rec)
        gt = {i: T for i, T in reader.ground_truth_poses().items() if s <= i < e}
        tr = {int(r[0]): np.asarray(r[3], np.float64).reshape(4, 4) for r in read_trajectory(d / "trajectory.csv") if r[2]}
        stc = reader.calibration().stereo
        maps_in.append((gt, tr, np.asarray(stc.K_rect, np.float64), tuple(stc.image_size)))
    VB = st["mB"][0]
    clouds = [scan_cloud_from_mesh(d / "mesh.ply") for d in st["dirs"]]
    res = {"pair": pair, "keys": st["keys"], "app pose off": pose_off(T_app, T_true, VB), "runs": [], "poses": {}}

    def judge_at(T_b: np.ndarray) -> dict:
        eA, sA, eB, sB = study.joint_readings(st, T_b)
        rows = {}
        for rule in ("equal", "ivw"):
            e, s, _ = combine(eA, sA, eB, sB, rule)
            rows[rule] = (e, s)
        j = judge_case(eA1, sA1, eB1, sB1, rows, st["inA"], st["inB"])
        j["walls"] = walls_table(st, rows)
        return j

    res["poses"]["true motion"] = judge_at(T_true)
    res["poses"]["app"] = judge_at(T_app)
    for corr in correlated:
        for seed in seeds:
            rng = np.random.default_rng(seed)
            mapsAB, sight = [], []
            for gt, tr, K, size in maps_in:
                m, n = emulated_tag_map(tags, gt, tr, K, size, rng=rng, run=CORRELATED_RUN if corr else 1)
                mapsAB.append(m)
                sight.append(n)
            tp = pose_from_tags(mapsAB[1], mapsAB[0])
            row = {"seed": seed, "correlated": corr, "tags A": {str(k): v for k, v in mapsAB[0].views.items()},
                   "tags B": {str(k): v for k, v in mapsAB[1].views.items()},
                   "shared": sorted(set(mapsAB[0].corners) & set(mapsAB[1].corners))}
            if tp is None:
                row["tag pose"] = None
                res["runs"].append(row)
                continue
            row.update({"tag rms mm": tp.rms_mm, "tags used": tp.ids, "tag pose off": pose_off(tp.T, T_true, VB)})
            al = align_pair(clouds[1], clouds[0], init=tp.T, same_setup=True, init_from="tags")
            row.update({"refined accepted": bool(al.accepted), "refined off": pose_off(al.T, T_true, VB),
                        "refined overlap": float(al.overlap)})
            tag_name, ref_name = f"tags held s{seed}{' corr' if corr else ''}", f"tags refined s{seed}{' corr' if corr else ''}"
            res["poses"][tag_name] = judge_at(tp.T)
            res["poses"][ref_name] = judge_at(np.asarray(al.T))
            row["judged as"] = [tag_name, ref_name]
            res["runs"].append(row)
    res["seconds"] = round(time.perf_counter() - t0, 1)
    res["held passes E-D4 (1)"] = {
        k: {r: bool(p["rows"][r]["E-D4 (1) on S_A"] and p["rows"][r]["E-D4 (1) on S_B"]) for r in p["rows"]}
        for k, p in res["poses"].items()}
    _write(out / f"tags_{pair}.json", res)
    return res


# =========================================================================== per-scan fusion, then combine (C1 reframed)
#: E-D3 item 4, the graphics-card experiment (fixed 2026-11-23 before any run; ``ed3\merge_looks\NOTES.md``)
PS_MIN_OBS = 3.0  # a scan counts at a voxel only when it observed it more than this often (the meshing threshold)
SPREAD_PAIRS = 2  # extra seed pairs per case, besides the case's own
SPREAD_AZ_DEG = 12.0  # a spread pair's stretches look from within this of the case's own sides (centre azimuths)
BASE_CASES = ("P1", "P2", "P3", "P4", "P5", "M1", "M2", "M3", "M4", "M5", "M6", "M7")
SEEDS = {"bracket240": range(5), "panel200": range(5), "sheet": range(3), "holes-low": range(3)}
RSS_LIMIT_GB = 12.0


def keep_apart(tA, nA, sA, tB, nB, sB, gA, gB, min_obs: float = PS_MIN_OBS, keep_faces: bool = True):
    """Two scans' fused volumes combined per voxel, each scan's faces kept apart, each scan weighing the same.

    ``t*``: each scan's own fused value (mm; its own opposite-sides rule already applied inside it), ``n*`` its
    count of observations, ``s*`` its voxel sigma (mm), ``g*`` its value's gradient (..., 3). A scan counts at a
    voxel when ``n > min_obs``. Where both count:

    * their gradients agree (dot >= 0): one surface seen by both: the mean of the two values;
    * they point opposite ways (dot < 0): the two faces of a wall, each seen by one scan: the larger value (free
      space seen by either scan wins), so each face's zero crossing stays where its own scan put it;

    where one counts, its value; where neither, the observation-weighted mean (what one model would hold).
    Returns ``(value, count, sigma, kept_apart)``: ``count`` = nA + nB; ``sigma`` of an averaged voxel
    ``sqrt((sA^2 + sB^2) / 2 + (tA - tB)^2 / 4)`` (the mixture's), of a kept-apart voxel the winner's, of a voxel
    one scan holds its own; ``kept_apart`` marks the voxels the second rule decided. ``keep_faces=False``: the plain
    average wherever both count (the outside review's control: equal say per scan without keeping faces apart)."""
    tA, nA, sA, tB, nB, sB = (np.asarray(x, np.float32) for x in (tA, nA, sA, tB, nB, sB))
    cA, cB = nA > min_obs, nB > min_obs
    both = cA & cB
    opp = both & (np.einsum("...k,...k->...", np.asarray(gA, np.float32), np.asarray(gB, np.float32)) < 0)
    if not keep_faces:
        opp = np.zeros_like(opp)
    avg = both & ~opp
    wn = np.maximum(nA + nB, np.float32(1e-6))
    t = np.where(cA & ~cB, tA, np.where(cB & ~cA, tB, (nA * tA + nB * tB) / wn))
    s = np.where(cA & ~cB, sA, np.where(cB & ~cA, sB, np.sqrt((nA * sA ** 2 + nB * sB ** 2) / wn)))
    t = np.where(avg, 0.5 * (tA + tB), t)
    s = np.where(avg, np.sqrt(0.5 * (sA ** 2 + sB ** 2) + 0.25 * (tA - tB) ** 2), s)
    win_a = tA >= tB
    t = np.where(opp, np.where(win_a, tA, tB), t)
    s = np.where(opp, np.where(win_a, sA, sB), s)
    return t.astype(np.float32), (nA + nB).astype(np.float32), s.astype(np.float32), opp


def brick_gradient(t: np.ndarray) -> np.ndarray:
    """Per voxel the gradient of a value held in bricks (M, 8, 8, 8) indexed [z, y, x], within each brick
    (one-sided at a brick's faces): (M, 8, 8, 8, 3) as (x, y, z)."""
    gz, gy, gx = np.gradient(np.asarray(t, np.float32), axis=(1, 2, 3))
    return np.stack([gx, gy, gz], axis=-1)


def union_bricks(kA: np.ndarray, kB: np.ndarray):
    """The union of two volumes' brick keys and, for each volume, where each union brick is in it (-1: absent)."""
    kA, kB = np.asarray(kA, np.int64).reshape(-1, 3), np.asarray(kB, np.int64).reshape(-1, 3)
    keys = np.unique(np.vstack([kA, kB]), axis=0)

    def code(k):
        return (k[:, 0] + 2 ** 19) * 2 ** 40 + (k[:, 1] + 2 ** 19) * 2 ** 20 + (k[:, 2] + 2 ** 19)  # 20 bits each

    ck = code(keys)  # sorted, as np.unique sorts rows lexicographically and the code keeps that order

    def where(k):
        idx = np.full(len(keys), -1, np.int64)
        if len(k):
            idx[np.searchsorted(ck, code(k))] = np.arange(len(k))
        return idx

    return keys, where(kA), where(kB)


def _watch_rss(limit_gb: float = RSS_LIMIT_GB) -> None:
    """Stop this process (exit 96) when its resident memory passes ``limit_gb``."""
    import os
    import threading

    try:
        import psutil
    except ImportError:  # no watch without psutil
        return
    me = psutil.Process()

    def run() -> None:
        while True:
            if me.memory_info().rss > limit_gb * 2 ** 30:
                print(f"resident memory past {limit_gb} GB: stopped", flush=True)
                os._exit(96)
            time.sleep(1.0)

    threading.Thread(target=run, daemon=True).start()


def fuse_alone(recording: Path, trajectory: Path, T: np.ndarray, params) -> dict:
    """One scan's frames fused alone into a model of their own with the merge's settings (what
    ``ohs.align.merge_scans`` does for one scan: the same pipeline, no clip since the part was not moved), downloaded:
    ``{"keys", "t" (fused value, mm), "n", "s" (voxel sigma), "nlook", "wsum", "voxel_mm", "trunc_mm", "n_integrated",
    "bricks", "model_mb", "s_fuse"}``."""
    from dataclasses import replace

    from ohs.app.pipeline import FramePipeline
    from ohs.app.process import READ_AHEAD_FRAMES
    from ohs.io.recording import RecordingReader
    from ohs.track.posegraph import read_trajectory
    from ohs.track.recon_tsdf import voxel_sigma

    t0 = time.perf_counter()
    p = replace(params, pipeline=replace(params.pipeline, tracking=False, use_imu=False))
    reader = RecordingReader(recording)
    cal = reader.calibration()
    rows = read_trajectory(trajectory)
    poses = {int(r[0]): np.asarray(r[3], np.float64).reshape(4, 4) for r in rows if r[2]}
    poses = {i: Tf for i, Tf in poses.items() if np.all(np.isfinite(Tf))}
    positions = [pos for pos, idx in enumerate(reader.indices) if int(idx) in poses]
    pipe = FramePipeline(cal, p.pipeline)
    try:
        frames = reader.iter_ahead(positions, READ_AHEAD_FRAMES)
        try:
            for frame in frames:
                pipe.process(frame, pose=np.asarray(T) @ poses[int(frame.index)], clip_plane=None)
        finally:
            frames.close()
        model = pipe.release_model()
    finally:
        pipe.close()
    try:
        mb = getattr(model, "memory_mb", None)
        mb = float(mb()) if callable(mb) else (float(mb) if mb is not None else None)
        keys, ch = model.download()
        out = {"keys": keys.astype(np.int64), "t": model.fused_tsdf(ch).astype(np.float32), "n": ch["nobs"].astype(np.float32),
               "s": voxel_sigma(ch["wsum"], ch["m2"], ch["nobs"], ch["nlook"], ch["mxc"], ch["hnear"], ch["snh2"],
                                voxel_mm=model.voxel_mm).astype(np.float32),
               "nlook": ch["nlook"].astype(np.float32), "wsum": ch["wsum"].astype(np.float32),
               "voxel_mm": float(model.voxel_mm), "trunc_mm": float(model.trunc_mm), "n_integrated": int(model.n_integrated),
               "bricks": int(len(keys)), "model_mb": mb}
        del ch
    finally:
        model.close()
    out["s_fuse"] = round(time.perf_counter() - t0, 1)
    return out


def combine_volumes(A: dict, B: dict, keep_faces: bool = True) -> dict:
    """:func:`keep_apart` over the union of two fused volumes' bricks."""
    keys, ia, ib = union_bricks(A["keys"], B["keys"])
    shape = (len(keys), 8, 8, 8)

    def put(vol, idx, name, fill=0.0):
        a = np.full(shape, np.float32(fill), np.float32)
        m = idx >= 0
        a[m] = vol[name][idx[m]]
        return a

    trunc = np.float32(A["trunc_mm"])
    tA, tB = put(A, ia, "t", trunc), put(B, ib, "t", trunc)
    nA, nB = put(A, ia, "n"), put(B, ib, "n")
    sA, sB = put(A, ia, "s", 50.0), put(B, ib, "s", 50.0)
    t, n, s, opp = keep_apart(tA, nA, sA, tB, nB, sB, brick_gradient(tA), brick_gradient(tB), keep_faces=keep_faces)
    return {"keys": keys, "t": t, "n": n, "s": s, "nlook": put(A, ia, "nlook") + put(B, ib, "nlook"),
            "wsum": put(A, ia, "wsum") + put(B, ib, "wsum"), "voxel_mm": A["voxel_mm"], "trunc_mm": A["trunc_mm"],
            "n_integrated": A["n_integrated"] + B["n_integrated"], "kept_apart_voxels": int(opp.sum()),
            "bricks": int(len(keys))}


def mesh_volume(vol: dict, weight_threshold: float = 3.0):
    """Marching cubes over a combined volume as ``ConfidenceTsdf.extract_mesh`` does: ``(V, F, N, sigma, looks)``."""
    import open3d as o3d
    import open3d.core as o3c

    from ohs.track.recon_tsdf import BRICK, mesh_sigma_terms

    keys = vol["keys"]
    vbg = o3d.t.geometry.VoxelBlockGrid(
        attr_names=("tsdf", "weight", "color"), attr_dtypes=(o3c.float32, o3c.float32, o3c.float32),
        attr_channels=((1), (1), (3)), voxel_size=vol["voxel_mm"], block_resolution=BRICK,
        block_count=int(len(keys) + 64), device=o3c.Device("CPU:0"))
    buf, _ = vbg.hashmap().activate(o3c.Tensor(np.ascontiguousarray(keys, np.int32)))
    idx = buf.cpu().numpy().astype(np.int64)
    vbg.attribute("tsdf").numpy()[idx] = np.clip(vol["t"] / np.float32(vol["trunc_mm"]), -1.0, 1.0)[..., None]
    vbg.attribute("weight").numpy()[idx] = vol["n"][..., None]
    vbg.attribute("color").numpy()[idx] = np.stack([vol["s"], vol["nlook"], vol["wsum"]], axis=-1)
    mesh = vbg.extract_triangle_mesh(weight_threshold=float(min(weight_threshold, max(vol["n_integrated"] - 1, 0))))
    V = mesh.vertex.positions.cpu().numpy().astype(np.float64)
    F = mesh.triangle.indices.cpu().numpy().astype(np.int64)
    N = mesh.vertex.normals.cpu().numpy().astype(np.float64)
    C = mesh.vertex.colors.cpu().numpy()
    sg = (C[:, 0] * np.float32(255.0)).astype(np.float64)
    S = np.sqrt(sg * sg + mesh_sigma_terms(V, F, N, voxel_mm=vol["voxel_mm"])).astype(np.float32)
    return V, F, N, S, (C[:, 1] * np.float32(255.0)).astype(np.float32)


class Experiment:
    """The graphics-card experiment's folder: its plan (pairs, poses, spread pairs) and the jobs' results."""

    def __init__(self, root: Path, recs: Path) -> None:
        from tools import merge_accuracy_study as MS

        self.MS, self.SC = MS, MS.SC
        self.root, self.recs = Path(root), Path(recs)
        self.work = self.root / "work"
        self.plan_path = self.root / "plan.json"
        self.plan = json.loads(self.plan_path.read_text()) if self.plan_path.is_file() else None
        if self.plan:
            for name, c in self.plan["pairs"].items():
                MS.PAIRS[name] = (c["a"], c["b"], c["start"], name)
                MW_PAIRS.setdefault(name, (c["a"], c["b"]))

    def study(self) -> "Study":
        st = Study(self.root, self.root, self.recs)
        st.x7 = st.mw = self.work
        return st

    def app_variant(self, name: str) -> str:
        f = self.work / "pairs" / name / "app_variant.txt"
        return f.read_text().strip() if f.is_file() else self.plan["pairs"][name]["app"]


def centre_azimuth(rec: Path, start: int, sc, gt=None) -> float:
    """The circular mean azimuth (degrees, about the scene's target) of a 90-frame stretch's true cameras."""
    from ohs.io.recording import RecordingReader

    gt = gt if gt is not None else RecordingReader(rec).ground_truth_poses()
    C = np.array([gt[i][:3, 3] for i in sorted(gt) if start <= i < start + 90])
    tgt = np.asarray(sc.target, float)
    a = np.arctan2(C[:, 1] - tgt[1], C[:, 0] - tgt[0])
    return float(np.degrees(np.arctan2(np.sin(a).mean(), np.cos(a).mean())))


def plan_experiment(x7: Path, mergewarn: Path, recs: Path, out: Path) -> dict:
    """Fix the experiment before any run: the 12 cases, their poses, and :data:`SPREAD_PAIRS` more seed pairs per
    case whose stretches look from the case's own two sides (centre azimuths within :data:`SPREAD_AZ_DEG`; starts on
    a 10-frame grid; a seed pair other than the case's own), the closest first. Copies the cases' saved single
    scans and alignments into ``out/work`` (the studies' folders are only read)."""
    import shutil

    from ohs.io.recording import RecordingReader
    from tools import merge_accuracy_study as MS

    SC = MS.SC
    recs = Path(recs)
    work = Path(out) / "work"
    pairs: dict[str, dict] = {}
    gts: dict[str, dict] = {}
    for case in BASE_CASES:
        ka, kb = MW_PAIRS[case] if case in MW_PAIRS else MS.PAIRS[case][:2]
        start = "points" if case == "P3" else "shape"
        pairs[case] = {"a": ka, "b": kb, "start": start, "app": APP_VARIANT.get(case, "a"), "case": case,
                       "spread": False, "ruined_by_pose": case in RUINED}
        scene = ka.split("@")[0].rsplit("-s", 1)[0]
        spec, _, _ = MS.scan_spec(ka)
        sc = SC.scene_of(spec)

        def gt_of(name):
            if name not in gts:
                gts[name] = RecordingReader(recs / name).ground_truth_poses()
            return gts[name]

        want = [centre_azimuth(None, int(k.split("@")[1]), sc, gt_of(k.split("@")[0])) for k in (ka, kb)]
        own = {int(k.split("@")[0].rsplit("-s", 1)[1]) for k in (ka, kb)}
        cand = []
        for i in SEEDS[scene]:
            for j in SEEDS[scene]:
                if i == j or {i, j} == own:
                    continue
                best = []
                for seed, w in ((i, want[0]), (j, want[1])):
                    g = gt_of(f"{scene}-s{seed}")
                    n = max(g) + 1
                    opts = [(abs((centre_azimuth(None, s0, sc, g) - w + 180) % 360 - 180), s0) for s0 in range(0, n - 89, 10)]
                    best.append(min(opts))
                if max(b[0] for b in best) <= SPREAD_AZ_DEG:
                    shared = int(i in own) + int(j in own)  # scans of the case's own seeds: fewer first
                    cand.append((shared, best[0][0] + best[1][0], f"{scene}-s{i}@{best[0][1]}", f"{scene}-s{j}@{best[1][1]}"))
        cand = [c[1:] + (c[0],) for c in sorted(cand)]
        for k, (_, a, b, _) in enumerate(cand[:SPREAD_PAIRS]):
            pairs[f"{case}s{k + 1}"] = {"a": a, "b": b, "start": start, "app": "a", "case": case, "spread": True,
                                        "ruined_by_pose": None}
        print(case, "spread pairs", [(round(c[0], 1), c[1], c[2], f"shares {c[3]} seed(s)") for c in cand[:SPREAD_PAIRS]], flush=True)
    for case in BASE_CASES:  # the cases' own singles and alignments, copied
        src = (Path(mergewarn) if case in MW_PAIRS else Path(x7)) / "work"
        c = pairs[case]
        for key in (c["a"], c["b"]):
            dst = MS.scan_dir(work, key, "tracked")
            if not (dst / "done.json").is_file():
                shutil.copytree(MS.scan_dir(src, key, "tracked"), dst, dirs_exist_ok=True)
        pd = work / "pairs" / case
        pd.mkdir(parents=True, exist_ok=True)
        for f in ("align.json", "align_points.json"):
            if (src / "pairs" / case / f).is_file():
                shutil.copy2(src / "pairs" / case / f, pd / f)
    singles = sorted({k for c in pairs.values() for k in (c["a"], c["b"])
                      if not (MS.scan_dir(work, k, "tracked") / "done.json").is_file()})
    plan = {"fixed": time.strftime("%Y-%m-%d %H:%M"), "pairs": pairs, "new_singles": singles, "poses": ["app", "b2"],
            "min_obs": PS_MIN_OBS, "spread_az_deg": SPREAD_AZ_DEG}
    _write(Path(out) / "plan.json", plan)
    return plan


def run_today(ex: Experiment, name: str) -> None:
    """Today's merge at the app's pose (a spread pair is aligned first: from the shape, or from picked points when
    the shape is not sure, as the app then asks) and at the true motion."""
    MS = ex.MS
    c = ex.plan["pairs"][name]
    pd = ex.work / "pairs" / name
    pd.mkdir(parents=True, exist_ok=True)
    if c["spread"] and not (pd / "app_variant.txt").is_file():
        MS.run_pair(ex.work, ex.recs, name, ["a"])
        if json.loads((pd / "align.json").read_text())["accepted"]:
            (pd / "app_variant.txt").write_text("a")
        else:
            MS.run_pair(ex.work, ex.recs, name, ["p"])
            (pd / "app_variant.txt").write_text("p")
    MS.run_pair(ex.work, ex.recs, name, [ex.app_variant(name), "b2"])


def run_perscan(ex: Experiment, name: str) -> dict:
    """Each scan fused alone, combined (:func:`combine_volumes`), meshed and cleaned as a merge is: ``merge_ps_{v}``
    at the app's pose and the true motion, the poses of today's merges (read from their ``done.json``)."""
    from ohs.mesh.confidence import SIGMA_NONE_MM
    from ohs.track.export import clean_mesh, save_mesh

    MS = ex.MS
    c = ex.plan["pairs"][name]
    params = MS.merge_params()
    pd = ex.work / "pairs" / name
    A, B = MS.scan_dir(ex.work, c["a"], "tracked"), MS.scan_dir(ex.work, c["b"], "tracked")
    ra, rb = ex.recs / c["a"].split("@")[0], ex.recs / c["b"].split("@")[0]
    info = {}
    volA = None
    for v in (ex.app_variant(name), "b2"):
        todo = [(tag, kf) for tag, kf in (("ps", True), ("pb", False)) if not (pd / f"merge_{tag}_{v}" / "done.json").is_file()]
        if not todo:
            continue
        T = np.asarray(json.loads((pd / f"merge_{v}" / "done.json").read_text())["T"][1])
        if volA is None:
            volA = fuse_alone(ra, A / "trajectory.csv", np.eye(4), params)
        volB = fuse_alone(rb, B / "trajectory.csv", T, params)
        for tag, kf in todo:
            t0 = time.perf_counter()
            out = pd / f"merge_{tag}_{v}"
            vol = combine_volumes(volA, volB, keep_faces=kf)
            V, F, N, S, L = mesh_volume(vol)
            if params.clean_min_component > 0 and len(F):
                V, F, idx = clean_mesh(V, F, min_component_fraction=float(params.clean_min_component), return_index=True)
                N, S, L = N[idx], S[idx], L[idx]
            S = np.minimum(S, np.float32(SIGMA_NONE_MM))
            out.mkdir(parents=True, exist_ok=True)
            save_mesh(out / "mesh.ply", V, F, N, quality=S, looks=L)
            row = {"s_combine_mesh": round(time.perf_counter() - t0, 1), "T": [np.eye(4).tolist(), T.tolist()],
                   "vertices": int(len(V)), "keep_faces": kf,
                   "bricks": {"A": volA["bricks"], "B": volB["bricks"], "union": vol["bricks"]},
                   "model_mb": {"A": volA["model_mb"], "B": volB["model_mb"]},
                   "fuse_s": {"A": volA["s_fuse"], "B": volB["s_fuse"]}, "kept_apart_voxels": vol["kept_apart_voxels"]}
            _write(out / "done.json", row)
            info[f"{tag}_{v}"] = row
            del vol
        del volB
    return info


def run_judge_exp(ex: Experiment, name: str) -> dict:
    """Both rules on today's merge and the per-scan one, at the app's pose and the true motion."""
    st = ex.study()
    c = ex.plan["pairs"][name]
    res = {}
    stt = st.setup_keys(name, (c["a"], c["b"]))
    eA1, sA1, eB1, sB1 = st.single_readings(stt)
    for v in (ex.app_variant(name), "b2"):
        eA, sA, eB, sB = st.joint_readings(stt, st.merge_pose(name, v))
        rows = {"today": st.merge_reading(stt, v), "per-scan": st.merge_reading(stt, f"ps_{v}")}
        if (st.merge_dir(name, f"pb_{v}") / "mesh.ply").is_file():
            rows["per-scan plain average"] = st.merge_reading(stt, f"pb_{v}")
        e, s, _ = combine(eA, sA, eB, sB, "equal")
        rows["surface emulation (equal)"] = (e, s)
        j = judge_case(eA1, sA1, eB1, sB1, rows, stt["inA"], stt["inB"])
        j["walls"] = walls_table(stt, {"A": (eA1, sA1), "B": (eB1, sB1), **rows})
        j["thin"] = [w["name"] for w in stt["walls"] if w["true_mm"] is not None and w["true_mm"] <= 2.0]
        al = ex.work / "pairs" / name / ("align_points.json" if v == "p" else "align.json")
        j["overlap"] = json.loads(al.read_text())["result"]["overlap"] if v in ("a", "p") and al.is_file() else None
        res[v] = j
    _write(ex.work / "pairs" / name / "judge_exp.json", res)
    return res


def exp_verdict(ex: Experiment) -> dict:
    """The spread and the kill rule (fixed before any run).

    * The spread of a metric in a case (and pose): the range over the case's pairs of a single scan's own number on
      its own surface, scan A's and scan B's taken apart, the larger of the two, at least the floors. E-D4 (1) holds when on S_A and on S_B
      the merge is no worse than that scan by more than that spread.
    * A pair is sound when the pose did not ruin it: the X7 / merge-warning label for a case's own pair; for a spread
      pair, its alignment's overlap at least the caution's 0.70.
    * Dropped if, at either pose: the per-scan merge fails E-D4 (1) on a sound pair where today's passes; or reads
      a wall of 2 mm or less 0.05 mm or more thicker than today's; or holds less of the seen surface within 0.5 mm
      than today's by more than the range of today's own over the case's pairs (at least the floor)."""
    P = ex.plan["pairs"]
    J = {n: json.loads((ex.work / "pairs" / n / "judge_exp.json").read_text()) for n in P
         if (ex.work / "pairs" / n / "judge_exp.json").is_file()}
    out: dict = {"cases": {}, "kill": []}
    for case in BASE_CASES:
        names = [n for n in P if P[n]["case"] == case and n in J]
        for pose in ("app", "b2"):
            def vj(n):
                return J[n][ex.app_variant(n)] if pose == "app" else J[n]["b2"]

            sp: dict = {}
            for m, fl in (("cover05", FLOOR_SHARE), ("med", FLOOR_MED), ("over03", FLOOR_SHARE)):
                rng = [np.ptp([vj(n)["singles"][k][f"S_{k}"][m] for n in names]) for k in ("A", "B")]
                sp[m] = max(max(rng), fl) if len(names) > 1 else fl
            tc = [vj(n)["rows"]["today"]["S_A or S_B"]["cover05"] for n in names]
            sp["today cover05"] = max(max(tc) - min(tc), FLOOR_SHARE) if len(names) > 1 else FLOOR_SHARE
            sp["pairs"] = len(names)
            rows = []
            for n in names:
                j = vj(n)
                sound = (not P[n]["ruined_by_pose"]) if not P[n]["spread"] else (j.get("overlap") or 1.0) >= 0.70
                r: dict = {"pair": n, "sound": bool(sound), "overlap": j.get("overlap")}
                for meth in [m for m in ("today", "per-scan", "per-scan plain average", "surface emulation (equal)") if m in j["rows"]]:
                    ok = True
                    for k in ("A", "B"):
                        m, s = j["rows"][meth][f"S_{k}"], j["singles"][k][f"S_{k}"]
                        ok &= (m["cover05"] >= s["cover05"] - sp["cover05"] and m["med"] <= s["med"] + sp["med"]
                               and m["over03"] <= s["over03"] + sp["over03"])
                    r[f"{meth} E-D4 (1)"] = bool(ok)
                    r[f"{meth} E-D4 (3)"] = j["rows"][meth]["E-D4 (3) more than the larger scan"]
                    r[f"{meth} X7"] = [j["rows"][meth]["X7 beats the better single"], j["rows"][meth]["X7 reaches the union"]]
                    r[f"{meth} all"] = j["rows"][meth]["S_A or S_B"]
                    r[f"{meth} 2s"] = j["rows"][meth]["2 sigma covers"]
                r["singles 2s"] = [j["singles"]["A"]["2 sigma covers"], j["singles"]["B"]["2 sigma covers"]]
                r["walls"] = {w: [j["walls"]["today"].get(w), j["walls"]["per-scan"].get(w)] for w in j["thin"]}
                r["walls all"] = j["walls"]
                why = []
                if sound and r["today E-D4 (1)"] and not r["per-scan E-D4 (1)"]:
                    why.append("fails E-D4 (1) where today passes")
                for w, (a, b) in r["walls"].items():
                    if a is not None and b is not None and b - a >= WALL_ALARM:
                        why.append(f"{w} {b - a:+.2f} mm thicker")
                d = r["per-scan all"]["cover05"] - r["today all"]["cover05"]
                if d < -sp["today cover05"]:
                    why.append(f"coverage {100 * d:+.1f} points")
                r["kill"] = why
                if why:
                    out["kill"].append({"pair": n, "pose": pose, "why": why})
                rows.append(r)
            out["cases"][f"{case} {pose}"] = {"spread": sp, "pairs": rows}
    out["killed"] = bool(out["kill"])
    _write(ex.root / "verdict.json", out)
    return out


# =========================================================================== today's merge at a held tag pose (X19's test)
TAG_RUNS = tuple((seed, corr) for corr in (False, True) for seed in (0, 1, 2))  # the C3 first test's order


def tag_run_name(seed: int, corr: bool) -> str:
    return f"tag{seed}{'c' if corr else ''}"


def emulate_tag_poses(study: "Study", pair: str, keys: tuple[str, str], runs=TAG_RUNS) -> list[dict]:
    """The C3 emulation's tag poses of B in A's world (:func:`run_tags`' maps, recomputed exactly: same tags, same
    seeds, same order), each with its corner RMS and its distance from the true motion (merge b2's pose)."""
    from ohs.align.tags import pose_from_tags
    from ohs.io.recording import RecordingReader
    from ohs.track.posegraph import read_trajectory
    from ohs.track.export import load_ply

    MS = study.MS
    tags = table_tags()
    maps_in = []
    for key in keys:
        _, s, e = MS.scan_spec(key)
        rec = study.recs / key.split("@")[0]
        d = MS.scan_dir(study.work(pair), key, "tracked")
        reader = RecordingReader(rec)
        gt = {i: T for i, T in reader.ground_truth_poses().items() if s <= i < e}
        tr = {int(r[0]): np.asarray(r[3], np.float64).reshape(4, 4) for r in read_trajectory(d / "trajectory.csv") if r[2]}
        stc = reader.calibration().stereo
        maps_in.append((gt, tr, np.asarray(stc.K_rect, np.float64), tuple(stc.image_size)))
    VB = load_ply(MS.scan_dir(study.work(pair), keys[1], "tracked") / "mesh.ply")["vertices"].astype(np.float64)[::7]
    T_true = study.merge_pose(pair, "b2")
    out = []
    for seed, corr in runs:
        rng = np.random.default_rng(seed)
        maps = [emulated_tag_map(tags, gt, tr, K, size, rng=rng, run=CORRELATED_RUN if corr else 1)[0]
                for gt, tr, K, size in maps_in]
        tp = pose_from_tags(maps[1], maps[0])
        out.append({"run": tag_run_name(seed, corr), "seed": seed, "correlated": corr,
                    "T": None if tp is None else tp.T, "rms_mm": None if tp is None else tp.rms_mm,
                    "off_true": None if tp is None else pose_off(tp.T, T_true, VB)})
    return out


def run_tag_merges(ex: "Experiment", name: str, runs=TAG_RUNS) -> list[dict]:
    """Today's merge (``merge_scans``, the server's settings) with B at each emulated tag pose, held:
    ``merge_tag{seed}[c]``."""
    from ohs.align import ScanInput, merge_scans

    MS = ex.MS
    c = ex.plan["pairs"][name]
    st = ex.study()
    pd = ex.work / "pairs" / name
    A, B = MS.scan_dir(ex.work, c["a"], "tracked"), MS.scan_dir(ex.work, c["b"], "tracked")
    ra, rb = ex.recs / c["a"].split("@")[0], ex.recs / c["b"].split("@")[0]
    params = MS.merge_params()
    rows = []
    for tp in emulate_tag_poses(st, name, (c["a"], c["b"]), runs):
        mdir = pd / f"merge_{tp['run']}"
        if tp["T"] is None or (mdir / "done.json").is_file():
            rows.append({k: v for k, v in tp.items() if k != "T"})
            continue
        t0 = time.perf_counter()
        r = merge_scans([ScanInput(ra, A / "trajectory.csv", np.eye(4), "A"), ScanInput(rb, B / "trajectory.csv", tp["T"], "B")],
                        mdir, params)
        _write(mdir / "done.json", {"s": round(time.perf_counter() - t0, 1), "T": [np.eye(4).tolist(), tp["T"].tolist()],
                                    "vertices": r["mesh"]["n_vertices"], "tag rms mm": tp["rms_mm"], "off_true": tp["off_true"],
                                    "seed": tp["seed"], "correlated": tp["correlated"]})
        rows.append({k: v for k, v in tp.items() if k != "T"})
    return rows


def judge_tag_merges(ex: "Experiment", name: str) -> dict:
    """E-D4 for today's merge at each held tag pose, beside today's at the app's pose and at the true motion, with the
    case's spread from gpu1's verdict (the singles' own ranges)."""
    st = ex.study()
    c = ex.plan["pairs"][name]
    pd = ex.work / "pairs" / name
    verdict = json.loads((ex.root / "verdict.json").read_text())
    sp = verdict["cases"][f"{c['case']} b2"]["spread"]
    stt = st.setup_keys(name, (c["a"], c["b"]))
    eA1, sA1, eB1, sB1 = st.single_readings(stt)
    rows = {"app": st.merge_reading(stt, ex.app_variant(name)), "true motion": st.merge_reading(stt, "b2")}
    info = {}
    for d in sorted(pd.glob("merge_tag*")):
        if (d / "mesh.ply").is_file():
            run = d.name[len("merge_"):]
            rows[run] = st.merge_reading(stt, run)
            info[run] = json.loads((d / "done.json").read_text())
    j = judge_case(eA1, sA1, eB1, sB1, rows, stt["inA"], stt["inB"])
    walls = walls_table(stt, rows)
    thin = [w["name"] for w in stt["walls"] if w["true_mm"] is not None and w["true_mm"] <= 2.0]
    out = {"pair": name, "case": c["case"], "spread": sp, "rows": {}}
    for k, r in j["rows"].items():
        ok = True
        for s in ("A", "B"):
            m, g = r[f"S_{s}"], j["singles"][s][f"S_{s}"]
            ok &= (m["cover05"] >= g["cover05"] - sp["cover05"] and m["med"] <= g["med"] + sp["med"]
                   and m["over03"] <= g["over03"] + sp["over03"])
        out["rows"][k] = {"E-D4 (1)": bool(ok), "E-D4 (3)": r["E-D4 (3) more than the larger scan"],
                          "X7": [r["X7 beats the better single"], r["X7 reaches the union"]],
                          "all": r["S_A or S_B"], "S_A": r["S_A"], "S_B": r["S_B"], "2 sigma covers": r["2 sigma covers"],
                          "walls": {w: walls[k].get(w) for w in thin},
                          "off_true": info.get(k, {}).get("off_true"), "tag rms mm": info.get(k, {}).get("tag rms mm")}
    _write(pd / "judge_tags.json", out)
    return out


# =========================================================================== X19's stop-rule test: tags rendered into both scans
#: the tags on the table round the part (fixed before any run, ed3\gpu1\NOTES.md): 8 tag36h11 tags of 25 mm with a
#: 6 mm white margin, on a ring of 110 mm round the scene's target, in the table's own frame (its y the other way
#: round, as tools/large_part_study.py and tools/wall_thickness_study.py put them)
X19_TAG_MM = 25.0
X19_RING_MM = 110.0
X19_TAGS = 8
#: pair: (scan A, scan B); the recordings: "<scene>t-s<seed>" (tags), "<scene>tm-s<seed>" (tag 2 moved 2 mm along x)
X19_PAIRS = {
    "T3": ("bracket240t-s4@0", "bracket240t-s1@20"),   # as X7's P3: the bracket's opposite sides
    "T1": ("bracket240t-s0@0", "bracket240t-s1@20"),   # as M1
    "T3b": ("bracket240t-s0@0", "bracket240t-s3@10"),  # as P3s2
    "TS": ("sheett-s0@30", "sheett-s1@40"),            # as M3 (the sheet: reported, not gated)
    "TM": ("bracket240t-s4@0", "bracket240tm-s1@20"),  # T3 with tag 2 moved 2 mm between the scans
    "TH": ("bracket240t-s0@0", "bracket240t-s4@0"),    # one side twice: shares 0.70 or more (bit for bit)
}
X19_GATED = ("T3", "T1", "T3b")


def x19_rec_name(key: str) -> str:
    return key.split("@")[0]


def x19_register(SC, names) -> None:
    """The tagged recordings as scorecard scenes (their geometry is the untagged scene's: the judge's truth)."""
    import dataclasses as dc

    for name in names:
        base = name.replace("bracket240tm", "bracket240").replace("bracket240t", "bracket240").replace("sheett", "sheet")
        if name not in SC.SCENES:
            SC.SCENES[name] = dc.replace(SC.SCENES[base], name=name)


def x19_scene(name: str, moved: dict | None = None):
    """The scene of a tagged recording: the scorecard's scene of that seed with :data:`X19_TAGS` tags on its table
    (``moved``: tag id -> (dx, dy) mm on the table)."""
    from ohs.sim.scene import Decal, DecalAlbedo, Scene, apriltag_texture, marker_corners_world
    from tools import merge_accuracy_study as MS

    SC = MS.SC
    x19_register(SC, [name])
    sc0 = SC.scene_of(SC.SCENES[name])
    table = next(p for p in sc0.primitives if p.name == "table")
    tgt = np.asarray(sc0.target, np.float64)
    decals = []
    for m in range(X19_TAGS):
        a = 2 * np.pi * (m + 0.5) / X19_TAGS
        x, y = tgt[0] + X19_RING_MM * np.cos(a), tgt[1] + X19_RING_MM * np.sin(a)
        dx, dy = (moved or {}).get(m, (0.0, 0.0))
        decals.append(Decal(apriltag_texture(m, X19_TAG_MM), 10, (x + dx, -(y + dy)), angle=0.3 * m, margin_mm=X19_TAG_MM / 4,
                            tag_id=m))
    table.albedo = DecalAlbedo(table.albedo, decals)
    markers = {dc.tag_id: marker_corners_world(table, dc) for dc in decals}
    return Scene(list(sc0.primitives), name=sc0.name + "_tags", target=sc0.target, radius=sc0.radius, markers=markers,
                 meta=dict(sc0.meta, tags={"mm": X19_TAG_MM, "ring_mm": X19_RING_MM, "moved": moved or {}}))


def x19_render(root: Path, key: str) -> Path:
    """Render a tagged recording, frames 0 to the stretch's end, on the scorecard's own orbit of that seed."""
    import shutil

    from ohs.sim.datasets import make_scan_recording
    from ohs.sim.nominal import NOMINAL_TIME_OFFSET, nominal_calibration
    from ohs.sim.trajectory import HandheldTrajectory
    from tools import merge_accuracy_study as MS

    SC = MS.SC
    name = x19_rec_name(key)
    path = Path(root) / "recs" / name
    x19_register(SC, [name])
    _, s, e = MS.scan_spec(key)
    if (path / "done").is_file():
        d = json.loads((path / "done").read_text())
        if int(d["frames"]) >= e:
            return path
    shutil.rmtree(path, ignore_errors=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    moved = {2: (2.0, 0.0)} if "bracket240tm" in name else None
    sc = x19_scene(name, moved)
    spec = SC.SCENES[name]
    common = {"image_size": SC.IMAGE_SIZE, "realistic": True, "depth_every": 0, "seed": spec.seed}
    if spec.build["maker"] == "orbit":
        traj = SC._orbit(spec.build["scene"], spec.seed, spec.frames)
    else:  # the sheet, as the scorecard renders it
        calib = nominal_calibration(SC.IMAGE_SIZE, realistic=True)
        dur = spec.frames / 10.0
        traj = HandheldTrajectory(target=np.array(SC.SHEET_TARGET), t0=100.0 + NOMINAL_TIME_OFFSET, duration=dur, seed=spec.seed,
                                  baseline=calib.stereo.baseline, distance=(200.0, 280.0), elevation_deg=(35.0, 65.0),
                                  sweep_deg_s=400.0 / dur)
    t0 = time.perf_counter()
    make_scan_recording(path, scene=sc, n_frames=e, fps=10.0, trajectory=traj, overwrite=True, **common)
    _write(path / "tags.json", {"tag_mm": X19_TAG_MM, "moved": moved or {},
                                "corners": {str(i): np.asarray(X).tolist() for i, X in sc.markers.items()}})
    _write(path / "done", {"frames": e, "render_s": round(time.perf_counter() - t0, 1)})
    return path


def x19_tag_map(rec: Path, scan_dir: Path, tag_mm: float | None = None):
    """The tags a scan saw, as the server finds them (``tag_map`` at the scan's poses, 120 frames at most), cached."""
    from ohs.align.tags import TagMap, tag_map
    from ohs.track.markers import MarkerParams

    f = scan_dir / "tag_map.json"
    if f.is_file():
        d = json.loads(f.read_text())
        return TagMap({int(k): np.asarray(v["corners"]) for k, v in d["tags"].items()},
                      {int(k): int(v["views"]) for k, v in d["tags"].items()},
                      {int(k): float(v["spread_mm"]) for k, v in d["tags"].items()})
    if tag_mm is None:  # the printed size the recording's tags have (its tags.json), as a user types it in [markers]
        tj = Path(rec) / "tags.json"
        tag_mm = float(json.loads(tj.read_text())["tag_mm"]) if tj.is_file() else X19_TAG_MM
    m = tag_map(rec, scan_dir / "trajectory.csv", params=MarkerParams(tag_mm=float(tag_mm)), max_frames=120)
    f.write_text(json.dumps(m.as_dict()), encoding="utf-8")
    return m


def x19_pair(root: Path, recs: Path, name: str) -> dict:
    """Align the pair as the app does from the tags (``align_with_tags``, same setup), the hold off and on; merge at both
    poses and at the true motion (``merge_scans``, the server's settings)."""
    from dataclasses import replace

    from ohs.align import AlignParams, ScanInput, merge_scans, scan_cloud_from_mesh
    from ohs.align.tags import align_with_tags
    from tools import merge_accuracy_study as MS

    ka, kb = X19_PAIRS[name]
    MS.PAIRS[name] = (ka, kb, "tags", name)
    work = Path(root) / "work"
    pd = work / "pairs" / name
    pd.mkdir(parents=True, exist_ok=True)
    A, B = MS.scan_dir(work, ka, "tracked"), MS.scan_dir(work, kb, "tracked")
    ra, rb = Path(recs) / x19_rec_name(ka), Path(recs) / x19_rec_name(kb)
    ta, tb = x19_tag_map(ra, A), x19_tag_map(rb, B)
    dst, src = scan_cloud_from_mesh(A / "mesh.ply"), scan_cloud_from_mesh(B / "mesh.ply")
    out = {"pair": name, "keys": [ka, kb], "tags A": {str(k): v for k, v in ta.views.items()},
           "tags B": {str(k): v for k, v in tb.views.items()}}
    params = MS.merge_params()
    poses = {}
    for tag, p in (("off", AlignParams()), ("on", replace(AlignParams(), hold_tags=True))):
        res, row = align_with_tags(src, dst, tb, ta, p, same_setup=True)
        d = res.as_dict()
        out[tag] = {"result": d, "tags": row}
        poses[tag] = np.asarray(res.T)
    W = MS.fitted_world
    poses["b2"] = G_invert(W(ra, A / "trajectory.csv")) @ W(rb, B / "trajectory.csv")
    for tag, T in poses.items():
        mdir = pd / f"merge_{tag}"
        if (mdir / "done.json").is_file():
            continue
        t0 = time.perf_counter()
        r = merge_scans([ScanInput(ra, A / "trajectory.csv", np.eye(4), "A"), ScanInput(rb, B / "trajectory.csv", T, "B")], mdir, params)
        _write(mdir / "done.json", {"s": round(time.perf_counter() - t0, 1), "T": [np.eye(4).tolist(), np.asarray(T).tolist()],
                                    "vertices": r["mesh"]["n_vertices"]})
    _write(pd / "align.json", out)
    return out


def G_invert(T: np.ndarray) -> np.ndarray:
    from ohs import geometry as G

    return G.invert_se3(np.asarray(T, np.float64))


def x19_judge(root: Path, recs: Path, gpu1: Path, name: str) -> dict:
    """E-D4 for the merges at the hold-off pose, the hold-on pose and the true motion, with gpu1's spread for the case it
    stands for (the bracket's opposite sides: P3; the sheet: M3)."""
    from tools import merge_accuracy_study as MS

    ka, kb = X19_PAIRS[name]
    x19_register(MS.SC, [x19_rec_name(ka), x19_rec_name(kb)])
    st = Study(Path(root), Path(root), Path(recs))
    st.x7 = st.mw = Path(root) / "work"
    MW_PAIRS[name] = (ka, kb)
    case = "M3" if name == "TS" else "P3"
    sp = json.loads((Path(gpu1) / "verdict.json").read_text())["cases"][f"{case} b2"]["spread"]
    stt = st.setup(name, "tracked")
    eA1, sA1, eB1, sB1 = st.single_readings(stt)
    rows = {k: st.merge_reading(stt, k) for k in ("off", "on", "b2")}
    j = judge_case(eA1, sA1, eB1, sB1, rows, stt["inA"], stt["inB"])
    walls = walls_table(stt, rows)
    thin = [w["name"] for w in stt["walls"] if w["true_mm"] is not None and w["true_mm"] <= 2.0]
    al = json.loads((Path(root) / "work" / "pairs" / name / "align.json").read_text())
    from ohs.track.export import load_ply

    VB = load_ply(MS.scan_dir(st.work(name), kb, "tracked") / "mesh.ply")["vertices"].astype(np.float64)[::7]
    T_true = st.merge_pose(name, "b2")
    out = {"pair": name, "case": case, "spread": sp, "rows": {}, "align": al,
           "singles": {k: {r: j["singles"][k][r] for r in ("S_A", "S_B", "S_A or S_B")} for k in ("A", "B")},
           "singles walls": {k: {w: walls_table(stt, {k: e})[k].get(w) for w in thin}
                             for k, e in (("A", (eA1, sA1)), ("B", (eB1, sB1)))},
           "union oracle": j["union oracle"]}
    for k, r in j["rows"].items():
        ok = True
        for s in ("A", "B"):
            m, g = r[f"S_{s}"], j["singles"][s][f"S_{s}"]
            ok &= (m["cover05"] >= g["cover05"] - sp["cover05"] and m["med"] <= g["med"] + sp["med"]
                   and m["over03"] <= g["over03"] + sp["over03"])
        out["rows"][k] = {"E-D4 (1)": bool(ok), "E-D4 (3)": r["E-D4 (3) more than the larger scan"],
                          "X7": [r["X7 beats the better single"], r["X7 reaches the union"]], "all": r["S_A or S_B"],
                          "S_A": r["S_A"], "S_B": r["S_B"], "2 sigma covers": r["2 sigma covers"],
                          "walls": {w: walls[k].get(w) for w in thin},
                          "off_true": pose_off(st.merge_pose(name, k), T_true, VB) if k != "b2" else [0.0, 0.0]}
    _write(Path(root) / "work" / "pairs" / name / "judge.json", out)
    return out


# =========================================================================== X19: the bracket turned over, tags on the part
#: (accepted by the coordinator 2026-11-24, ed3\gpu1\NOTES.md) scan A: the bracket as it stands, scan B: upside down, propped
#: level on a block (named "floor": ground, neither part nor judge target); tags of 20 mm (2.5 mm white margin) stuck on
#: the upright's two large faces, three on one and two on the other (the face both scans of an opposite-sides pair see is
#: the same physical face once the part is turned over); B's table-tag variant keeps X19_TAGS on the table.
X19T_TAG_MM = 20.0
#: in the upright's local x' (along it) and y' (up it, from its middle): the shaft passes through the upright at x' 0,
#: y' +8 (radius 8), so no tag there. First go (2026-11-24, x19run\old1): three tags on the outer face ("+", local +z',
#: away from the base), two on the inner one; the inner face was never faced by a stretch's frames and the decals painted
#: through to the other face (mirrored copies beside tag 2). Second go: the five places of 20 mm tags the outer face
#: holds, two rows (not one line), each painted on its own face only (:class:`_FaceDecals`). 25 mm tags (3.1 mm margin)
#: fit only two on that face (the face is 90 x 52 mm and the shaft's stub sits in the middle of the upper half).
X19T_ROWS = {"+": [(-30.0, 13.0), (30.0, 13.0), (-30.0, -13.0), (0.0, -13.0), (30.0, -13.0)], "-": []}
#: the prop of the turned part (scan B): a block 20 x 3 mm under the base's free edge (its local y +27 .. +30, x -10 ..
#: +10, clear of the tab), from the table to the base. The first go put it under the edge the upright stands on, inside
#: the upright, its face flush with the outer face: it fought that face in the renders.
X19T_PROP = {"y": 28.5, "half_x": 10.0, "half_y": 1.5}


class _FaceDecals:
    """Decals on a box's local XY plane painted on one face only (``DecalAlbedo`` paints the same x, y on both faces):
    ``faces[k]`` +1 paints decal k where local z > 0, -1 where z < 0."""

    def __init__(self, base, decals, faces):
        from ohs.sim.scene import DecalAlbedo

        self.plus = DecalAlbedo(base, [d for d, f in zip(decals, faces) if f > 0])
        self.minus = DecalAlbedo(base, [d for d, f in zip(decals, faces) if f < 0])
        self.decals = list(decals)

    def __call__(self, p, n, footprint):
        a = self.plus(p, n, footprint)
        m = p[:, 2] < 0
        if np.any(m):
            a[m] = self.minus(p[m], n[m], footprint[m])
        return a
#: recordings: "bracket240tp-s<seed>" upright, tags on the part; "bracket240tpt-s<seed>" turned, tags on the part;
#: "bracket240ttt-s<seed>" turned, tags on the table ("bracket240t-s<seed>": upright, tags on the table, as before)
X19T_PAIRS = {
    "R1": ("bracket240tp-s4@0", "bracket240tpt-s1@20"),   # A: the turned-over test, three seed pairs (as T3, T1, T3b)
    "R2": ("bracket240tp-s0@0", "bracket240tpt-s1@20"),
    "R3": ("bracket240tp-s0@0", "bracket240tpt-s3@10"),
    "Q1": ("bracket240t-s4@0", "bracket240ttt-s1@20"),    # B: the same pairs, the tags left on the table
    "Q2": ("bracket240t-s0@0", "bracket240ttt-s1@20"),
    "Q3": ("bracket240t-s0@0", "bracket240ttt-s3@10"),
}


def x19t_pairs(root: Path) -> dict:
    """The pairs: :data:`X19T_PAIRS`, with a scan B replaced where ``<root>/x19t_pairs.json`` says (an unsound turned scan
    replaced by another seed, at most twice: the coordinator's rule)."""
    f = Path(root) / "x19t_pairs.json"
    out = dict(X19T_PAIRS)
    if f.is_file():
        out.update({k: tuple(v) for k, v in json.loads(f.read_text()).items()})
    return out


def x19t_kind(name: str) -> tuple[bool, str]:
    """(turned over?, where the tags are: "part" | "table") of a recording name."""
    stem = name.split("-s")[0]
    return stem in ("bracket240tpt", "bracket240ttt"), ("part" if stem in ("bracket240tp", "bracket240tpt") else "table")


def x19t_register(SC, names) -> None:
    import dataclasses as dc

    for name in names:
        seed = name.split("-s")[1].split("@")[0]
        if name not in SC.SCENES:
            SC.SCENES[name] = dc.replace(SC.SCENES[f"bracket240-s{seed}"], name=name)


def x19t_motion(sc0) -> np.ndarray:
    """The part's motion from standing to upside down (world, 4x4): half a turn about the base's long axis through the
    base's centre, then lifted so that the lowest point of the part touches the table."""
    from ohs import geometry as G
    from ohs.sim.scene import Box, Cylinder

    base = next(p for p in sc0.primitives if p.name == "base")
    u = base.R[:, 0]
    R = G.so3_exp(np.pi * u / np.linalg.norm(u))
    M = np.eye(4)
    M[:3, :3] = R
    M[:3, 3] = base.t - R @ base.t
    pts = []
    for p in sc0.primitives:
        if p.name == "table":
            continue
        if isinstance(p, Box):
            c = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]) * p.half
        elif isinstance(p, Cylinder):
            a = np.linspace(0, 2 * np.pi, 48, endpoint=False)
            ring = np.c_[p.radius * np.cos(a), p.radius * np.sin(a)]
            c = np.vstack([np.c_[ring, np.full(len(a), z)] for z in (-p.half_height, p.half_height)])
        else:
            continue
        pts.append((c @ p.R.T + p.t) @ M[:3, :3].T + M[:3, 3])
    M[2, 3] -= float(np.min(np.vstack(pts)[:, 2]))
    return M


def x19t_scene(name: str):
    """The scene of a recording of the turned-over test (module comment above)."""
    from ohs.sim.scene import Box, Cylinder, Decal, Scene, apriltag_texture
    from tools import merge_accuracy_study as MS

    SC = MS.SC
    x19t_register(SC, [name])
    turned, tags = x19t_kind(name)
    seed = int(name.split("-s")[1].split("@")[0])
    if tags == "table":  # the X19 table tags round the target (as the first rendered test)
        sc0 = x19_scene(f"bracket240t-s{seed}")
    else:
        sc0 = SC.scene_of(SC.SCENES[f"bracket240-s{seed}"])
    prims = list(sc0.primitives)
    markers = dict(sc0.markers or {}) if tags == "table" else {}
    if tags == "part":  # the upright re-framed (local z = its thin axis: its large faces are its local XY plane)
        k = next(i for i, p in enumerate(prims) if p.name == "upright")
        up = prims[k]
        Q = np.eye(4)
        Q[:3, :3] = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])  # columns: x, y' = z, z' = -y
        T_new = up.T_wo @ Q
        half = np.array([up.half[0], up.half[2], up.half[1]])
        decals, faces, m = [], [], 0
        for face, flip in (("+", True), ("-", False)):  # flip_y: reads from local +z (the face at z' = +half)
            for x, y in X19T_ROWS[face]:
                decals.append(Decal(apriltag_texture(m, X19T_TAG_MM), 10, (x, y), angle=0.0, margin_mm=X19T_TAG_MM / 8,
                                    tag_id=m, flip_y=flip))
                faces.append(1 if flip else -1)
                m += 1
        prims[k] = Box(T_new, half, _FaceDecals(up.albedo, decals, faces), name="upright")
        for dc in decals:
            xy = dc.corners_local()
            z = half[2] if dc.flip_y else -half[2]
            markers[dc.tag_id] = np.c_[xy, np.full(4, z)] @ T_new[:3, :3].T + T_new[:3, 3]
    if turned:
        M = x19t_motion(SC.scene_of(SC.SCENES[f"bracket240-s{seed}"]))
        out = []
        for p in prims:
            if p.name == "table":
                out.append(p)
            elif isinstance(p, Cylinder):
                out.append(Cylinder(M @ p.T_wo, p.radius, p.half_height, p.albedo, name=p.name, caps=getattr(p, "caps", True)))
            elif isinstance(p, Box):
                out.append(Box(M @ p.T_wo, p.half, p.albedo, name=p.name))
            else:
                raise ValueError(f"cannot turn {type(p).__name__}")
        # the prop (X19T_PROP): under the base's free edge, clear of the tab and the bosses, up to the base
        base = next(p for p in out if p.name == "base")
        hz = float(base.t[2] - base.half[2]) / 2.0
        c = base.t + base.R @ np.array([0.0, X19T_PROP["y"], 0.0])
        Tb = np.eye(4)
        Tb[:3, :3] = base.R
        Tb[:3, 3] = [c[0], c[1], hz]
        out.append(Box(Tb, (X19T_PROP["half_x"], X19T_PROP["half_y"], hz), 0.55, name="floor"))
        prims = out
        if tags == "part":
            markers = {i: X @ M[:3, :3].T + M[:3, 3] for i, X in markers.items()}
    return Scene(prims, name=sc0.name + ("_turned" if turned else "") + f"_tags_{tags}", target=sc0.target,
                 radius=sc0.radius, markers=markers, meta=dict(sc0.meta, x19t={"turned": turned, "tags": tags}))


def x19t_render(root: Path, key: str) -> Path:
    """Render a recording of the turned-over test, frames 0 to the stretch's end, on the scorecard's orbit of the seed."""
    import shutil

    from ohs.sim.datasets import make_scan_recording
    from tools import merge_accuracy_study as MS

    SC = MS.SC
    name = x19_rec_name(key)
    x19t_register(SC, [name])
    _, s, e = MS.scan_spec(key)
    path = Path(root) / "recs" / name
    if (path / "done").is_file() and int(json.loads((path / "done").read_text())["frames"]) >= e:
        return path
    shutil.rmtree(path, ignore_errors=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    sc = x19t_scene(name)
    spec = SC.SCENES[name]
    t0 = time.perf_counter()
    make_scan_recording(path, scene=sc, n_frames=e, fps=10.0, trajectory=SC._orbit("bracket", spec.seed, spec.frames),
                        overwrite=True, image_size=SC.IMAGE_SIZE, realistic=True, depth_every=0, seed=spec.seed)
    _write(path / "tags.json", {"tag_mm": X19T_TAG_MM if "tp" in name else X19_TAG_MM,
                                "corners": {str(i): np.asarray(X).tolist() for i, X in sc.markers.items()}})
    _write(path / "done", {"frames": e, "render_s": round(time.perf_counter() - t0, 1)})
    return path


def x19t_visible(recs: Path, key: str, min_frames: int = 3) -> dict:
    """Before rendering (processor): which tags a stretch's frames would see (in view, 150-450 mm, face turned to the
    camera within 70 degrees, the part's shadow not counted), from the true poses of the scorecard's own recording of
    that seed."""
    from ohs.io.recording import RecordingReader
    from tools import merge_accuracy_study as MS

    name = x19_rec_name(key)
    x19t_register(MS.SC, [name])
    _, s, e = MS.scan_spec(key)
    seed = name.split("-s")[1]
    reader = RecordingReader(Path(recs) / f"bracket240-s{seed}")
    gt = {i: T for i, T in reader.ground_truth_poses().items() if s <= i < e}
    stc = reader.calibration().stereo
    K, size = np.asarray(stc.K_rect, np.float64), tuple(stc.image_size)
    sc = x19t_scene(name)
    up = next(p for p in sc.primitives if p.name == "upright")
    on_part = x19t_kind(name)[1] == "part"
    seen = {}
    for i, X in sc.markers.items():
        # the side a tag reads from: up for table tags; on the upright, its face's outward normal (tags 0-2: local +z')
        nrm = (up.R[:, 2] * (1.0 if int(i) < len(X19T_ROWS["+"]) else -1.0)) if on_part else np.array([0.0, 0.0, 1.0])
        n = 0
        for T in gt.values():
            Xc = (X - T[:3, 3]) @ T[:3, :3]
            if np.any(Xc[:, 2] < 150) or np.any(Xc[:, 2] > 450):
                continue
            uv = Xc[:, :2] / Xc[:, 2:3] * np.array([K[0, 0], K[1, 1]]) + np.array([K[0, 2], K[1, 2]])
            if np.any(uv < 20) or np.any(uv[:, 0] > size[0] - 20) or np.any(uv[:, 1] > size[1] - 20):
                continue
            v = T[:3, 3] - X.mean(axis=0)
            if float(v @ nrm) / np.linalg.norm(v) > np.cos(np.radians(70)):
                n += 1
        seen[int(i)] = n
    return {"key": key, "frames": len(gt), "seen": seen, "tags_seen": sorted(k for k, n in seen.items() if n >= min_frames)}


def x19t_part_motion(name_b: str) -> np.ndarray:
    """The part's pose in scan B's scene relative to scan A's (identity when B is not turned)."""
    from tools import merge_accuracy_study as MS

    turned, _ = x19t_kind(name_b)
    if not turned:
        return np.eye(4)
    seed = name_b.split("-s")[1].split("@")[0]
    return x19t_motion(MS.SC.scene_of(MS.SC.SCENES[f"bracket240-s{seed}"]))


def x19t_pair(root: Path, recs: Path, name: str, shift_mm: float | None = None) -> dict:
    """As :func:`x19_pair` for a turned-over pair (the part moved: ``same_setup`` False): aligned through
    ``align_with_tags`` with the hold off and on; merged at both poses and at the part's true motion, each scan's table
    left out by its own map as the server's merge of a moved part does. ``shift_mm``: C,
    the tag map found in scan B shifted as a whole along B's x by that much (alignments only, no merge)."""
    from dataclasses import replace

    from ohs.align import AlignParams, ScanInput, merge_scans, scan_cloud_from_mesh
    from ohs.align.tags import TagMap, align_with_tags
    from tools import merge_accuracy_study as MS

    ka, kb = x19t_pairs(root)[name]
    x19t_register(MS.SC, [x19_rec_name(ka), x19_rec_name(kb)])
    MS.PAIRS[name] = (ka, kb, "tags", name)
    work = Path(root) / "work"
    pd = work / "pairs" / (name + ("" if shift_mm is None else f"_shift{shift_mm:g}"))
    pd.mkdir(parents=True, exist_ok=True)
    A, B = MS.scan_dir(work, ka, "tracked"), MS.scan_dir(work, kb, "tracked")
    ra, rb = Path(recs) / x19_rec_name(ka), Path(recs) / x19_rec_name(kb)
    ta, tb = x19_tag_map(ra, A), x19_tag_map(rb, B)
    if shift_mm is not None:
        tb = TagMap({k: np.asarray(X) + np.array([shift_mm, 0.0, 0.0]) for k, X in tb.corners.items()}, dict(tb.views),
                    dict(tb.spread_mm))
    dst, src = scan_cloud_from_mesh(A / "mesh.ply"), scan_cloud_from_mesh(B / "mesh.ply")
    out = {"pair": name, "keys": [ka, kb], "shift_mm": shift_mm, "tags A": {str(k): v for k, v in ta.views.items()},
           "tags B": {str(k): v for k, v in tb.views.items()},
           "tag corners A": {str(k): np.asarray(X).tolist() for k, X in ta.corners.items()}}
    poses = {}
    for tag, p in (("off", AlignParams()), ("on", replace(AlignParams(), hold_tags=True))):
        res, row = align_with_tags(src, dst, tb, ta, p, same_setup=False)
        out[tag] = {"result": res.as_dict(), "tags": row}
        poses[tag] = np.asarray(res.T)
    W = MS.fitted_world
    poses["b2"] = G_invert(W(ra, A / "trajectory.csv")) @ G_invert(x19t_part_motion(x19_rec_name(kb))) @ W(rb, B / "trajectory.csv")
    out["b2_T"] = poses["b2"].tolist()
    if shift_mm is None:
        from ohs.align.merge import scan_support_from_mesh

        params = MS.merge_params()
        # the part was moved: each scan's table found on its own mesh and left out of the merge, as the server does
        (supA, wA), (supB, wB) = scan_support_from_mesh(A / "mesh.ply", name="A"), scan_support_from_mesh(B / "mesh.ply", name="B")
        out["supports"] = {"A": supA is not None, "B": supB is not None, "warnings": list(wA) + list(wB)}
        for tag, T in poses.items():
            mdir = pd / f"merge_{tag}"
            if (mdir / "done.json").is_file():
                continue
            t0 = time.perf_counter()
            r = merge_scans([ScanInput(ra, A / "trajectory.csv", np.eye(4), "A", support=supA, warnings=list(wA)),
                             ScanInput(rb, B / "trajectory.csv", T, "B", support=supB, warnings=list(wB))], mdir, params)
            _write(mdir / "done.json", {"s": round(time.perf_counter() - t0, 1), "T": [np.eye(4).tolist(), np.asarray(T).tolist()],
                                        "vertices": r["mesh"]["n_vertices"]})
    _write(pd / "align.json", out)
    return out


def x19t_judge(root: Path, recs: Path, gpu1: Path, name: str) -> dict:
    """E-D4 for a turned-over pair: everything judged in scan A's frame and truth (the part as it stands); scan B's mesh,
    its seen surface and its world brought there by the inverse of the part's motion; gpu1's P3 spread."""
    from scipy.spatial import cKDTree

    from tools import merge_accuracy_study as MS
    from tools import preset_study as ps
    from tools.calib_health_study import Truth

    SC = MS.SC
    ka, kb = x19t_pairs(root)[name]
    na, nb = x19_rec_name(ka), x19_rec_name(kb)
    x19t_register(SC, [na, nb])
    work = Path(root) / "work"
    st = Study(Path(root), Path(root), Path(recs))
    st.x7 = st.mw = work
    MW_PAIRS[name] = (ka, kb)
    sp = json.loads((Path(gpu1) / "verdict.json").read_text())["cases"]["P3 b2"]["spread"]
    spec, sa, ea = MS.scan_spec(ka)
    _, sb, eb = MS.scan_spec(kb)
    scA, scB = x19t_scene(na), x19t_scene(nb)
    truth = SC.truth_for(SC.SCENES["bracket240-s" + na.split("-s")[1]], SC.scene_of(SC.SCENES["bracket240-s" + na.split("-s")[1]]))
    Mi = G_invert(x19t_part_motion(nb))
    ra, rb = Path(recs) / na, Path(recs) / nb
    A, B = MS.scan_dir(work, ka, "tracked"), MS.scan_dir(work, kb, "tracked")
    Wa = MS.fitted_world(ra, A / "trajectory.csv")
    Wb = Mi @ MS.fitted_world(rb, B / "trajectory.csv")
    SA = ps.seen_points(scA, ra, sa, ea, every=3, n_max=60000)
    SB = ps.seen_points(scB, rb, sb, eb, every=3, n_max=60000) @ Mi[:3, :3].T + Mi[:3, 3]
    X, Xn = truth.closest_part(np.vstack([SA, SB]))
    inA = np.r_[np.ones(len(SA), bool), cKDTree(SA).query(SB)[0] < 0.7]
    inB = np.r_[cKDTree(SB).query(SA)[0] < 0.7, np.ones(len(SB), bool)]
    tr = Truth(scA)
    order = {p.name: k for k, p in enumerate(scA.primitives)}
    prim = np.array([order.get(tr.names[k], -1) for k in np.argmin(np.abs(tr.prim_sdf(X)), axis=1)])
    stt = {"pair": name, "keys": (ka, kb), "spec": spec, "sc": scA, "truth": truth, "recs": (ra, rb), "dirs": (A, B),
           "W": (Wa, Wb), "X": X, "Xn": Xn, "inA": inA, "inB": inB, "prim": prim, "walls": walls_of(scA),
           "mA": recipe_mesh(A / "mesh.ply"), "mB": recipe_mesh(B / "mesh.ply")}
    eA1, sA1, eB1, sB1 = st.single_readings(stt)
    rows = {k: st.merge_reading(stt, k) for k in ("off", "on", "b2")}
    j = judge_case(eA1, sA1, eB1, sB1, rows, inA, inB)
    al = json.loads((work / "pairs" / name / "align.json").read_text())
    from ohs.track.export import load_ply

    VB = load_ply(B / "mesh.ply")["vertices"].astype(np.float64)[::7]
    T_true = np.asarray(al["b2_T"])
    out = {"pair": name, "spread": sp, "rows": {}, "align": al,
           "singles": {k: {r: j["singles"][k][r] for r in ("S_A", "S_B", "S_A or S_B")} for k in ("A", "B")},
           "union oracle": j["union oracle"],
           "single scans' own placement moved (mm)": {"A": None, "B": None}}
    for k, r in j["rows"].items():
        ok = True
        for s in ("A", "B"):
            m, g = r[f"S_{s}"], j["singles"][s][f"S_{s}"]
            ok &= (m["cover05"] >= g["cover05"] - sp["cover05"] and m["med"] <= g["med"] + sp["med"]
                   and m["over03"] <= g["over03"] + sp["over03"])
        out["rows"][k] = {"E-D4 (1)": bool(ok), "E-D4 (3)": r["E-D4 (3) more than the larger scan"],
                          "X7": [r["X7 beats the better single"], r["X7 reaches the union"]], "all": r["S_A or S_B"],
                          "S_A": r["S_A"], "S_B": r["S_B"], "2 sigma covers": r["2 sigma covers"],
                          "off_true": pose_off(st.merge_pose(name, k), T_true, VB) if k != "b2" else [0.0, 0.0]}
        if k != "b2" and al.get("tag corners A"):
            out["rows"][k]["error split"] = x19t_error_split(np.asarray(st.merge_pose(name, k)), T_true, VB,
                                                             al["tag corners A"])
    _write(work / "pairs" / name / "judge.json", out)
    return out


def x19t_error_split(T: np.ndarray, T_true: np.ndarray, V: np.ndarray, corners_a: dict) -> dict:
    """How a pose's error splits: the move of scan B's middle (mm), the turn (degrees) in all and about the tag rows'
    direction (tags 0 -> 1 in scan A's tag map) and about the tags' face normal, and the move at the tags (mm)."""
    from ohs import geometry as G

    E = T @ G_invert(T_true)
    w = np.degrees(G.so3_log(E[:3, :3]))
    C = {int(k): np.asarray(v) for k, v in corners_a.items()}
    P = np.vstack(list(C.values()))
    out = {"move at B's middle mm": float(np.linalg.norm(E[:3, :3] @ V.mean(0) + E[:3, 3] - V.mean(0))),
           "turn deg": float(np.linalg.norm(w)),
           "move at the tags mm (rms)": float(np.sqrt(np.mean(np.sum((P @ E[:3, :3].T + E[:3, 3] - P) ** 2, axis=1))))}
    if 0 in C and 1 in C:
        u = C[1].mean(0) - C[0].mean(0)
        u /= np.linalg.norm(u)
        q = P - P.mean(0)
        nrm = np.linalg.svd(q, full_matrices=False)[2][2]
        out["turn about the rows deg"] = float(w @ u)
        out["turn about the face normal deg"] = float(w @ nrm)
        out["turn about the third axis deg"] = float(w @ np.cross(nrm, u))
    return out


# =========================================================================== output
def _plain(x):
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else round(float(x), 5)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_plain(obj), indent=1), encoding="utf-8")


def _f(s: dict) -> str:
    return f"{s['cover05']:.3f}/{s['med']:.3f}/{100 * s['over03']:4.1f}"


def print_table(out: Path) -> None:
    for f in sorted(Path(out).glob("combine_*.json")):
        j = json.loads(f.read_text())
        S = j["singles"]
        print(f"== {j['pair']} {j['variant']}{' (pose ruined)' if j['ruined_by_pose'] else ''}  A on S_A {_f(S['A']['S_A'])}"
              f"  B on S_B {_f(S['B']['S_B'])}  union oracle {_f(j['union oracle'])}  better {j['better single']}"
              f"  2s: A {S['A']['2 sigma covers']} B {S['B']['2 sigma covers']}  kill {j['kill']}")
        for name, r in j["rows"].items():
            w = {k: v for k, v in j["walls"][name].items() if k in j["thin"] and v is not None}
            print(f"   {name:22s} S_A {_f(r['S_A'])} S_B {_f(r['S_B'])} all {_f(r['S_A or S_B'])}"
                  f" | E-D4 {'y' if r['E-D4 (1) on S_A'] else 'n'}{'y' if r['E-D4 (1) on S_B'] else 'n'}"
                  f"{'y' if r['E-D4 (3) more than the larger scan'] else 'n'}"
                  f" | X7 {'y' if r['X7 beats the better single'] else 'n'}{'y' if r['X7 reaches the union'] else 'n'}"
                  f" | 2s {r['2 sigma covers']} | walls {w}")
    for f in sorted(Path(out).glob("tags_*.json")):
        j = json.loads(f.read_text())
        print(f"== tags {j['pair']}: app pose off {j['app pose off']}")
        for r in j["runs"]:
            print("   ", {k: r.get(k) for k in ("seed", "correlated", "shared", "tag rms mm", "tag pose off",
                                                 "refined accepted", "refined off", "tags A", "tags B")})
        for name, p in j["poses"].items():
            for rule, r in p["rows"].items():
                print(f"   {name:24s} {rule:6s} S_A {_f(r['S_A'])} S_B {_f(r['S_B'])}"
                      f" E-D4 {'y' if r['E-D4 (1) on S_A'] else 'n'}{'y' if r['E-D4 (1) on S_B'] else 'n'}"
                      f" X7 {'y' if r['X7 beats the better single'] else 'n'}{'y' if r['X7 reaches the union'] else 'n'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("combine", "tags", "table", "plan", "single", "today", "perscan", "judge-exp", "verdict",
                                    "tagmerge", "judge-tags", "x19-render", "x19-single", "x19-pair", "x19-judge",
                                    "x19t-visible", "x19t-render", "x19t-single", "x19t-pair", "x19t-judge"))
    ap.add_argument("out", type=Path)
    ap.add_argument("--x7", type=Path)
    ap.add_argument("--mergewarn", type=Path)
    ap.add_argument("--recs", type=Path)
    ap.add_argument("--cases", nargs="*", help="PAIR:VARIANT (combine)")
    ap.add_argument("--pairs", nargs="*", default=["P3", "P5", "M1", "M3"], help="(tags)")
    ap.add_argument("--name", help="(single: a scan key; today / perscan / judge-exp: a pair of the plan)")
    ap.add_argument("--runs", nargs="*", help="(tagmerge: which tag runs, e.g. tag0; default all six)")
    ap.add_argument("--gpu1", type=Path, help="(x19-judge: the per-scan experiment's folder, for its spreads)")
    ap.add_argument("--shift", type=float, help="(x19t-pair: C, scan B's found tag map shifted as a whole by this, mm)")
    a = ap.parse_args(argv)
    ap_runs = [r for r in TAG_RUNS if a.cmd != "tagmerge" or a.runs is None or tag_run_name(*r) in a.runs]
    if a.cmd.startswith("x19t-"):  # X19, the bracket turned over (OUT: its folder; recordings in OUT/recs)
        _watch_rss()
        from tools import merge_accuracy_study as MS

        recs = Path(a.recs) if a.recs else Path(a.out) / "recs"
        if a.cmd == "x19t-visible":
            print(json.dumps(_plain(x19t_visible(a.recs, a.name))), flush=True)
        elif a.cmd == "x19t-render":
            print(x19t_render(a.out, a.name), flush=True)
        elif a.cmd == "x19t-single":
            x19t_register(MS.SC, [x19_rec_name(a.name)])
            MS.run_single(Path(a.out) / "work", recs, a.name, "tracked")
        elif a.cmd == "x19t-pair":
            r = x19t_pair(a.out, recs, a.name, a.shift)
            print(json.dumps(_plain({k: {"accepted": v["result"]["accepted"], "held": v["result"].get("held_by_tags"),
                                         "verdict": v["result"]["verdict"]} for k, v in r.items() if k in ("off", "on")}))[:3000])
        else:
            x19t_judge(a.out, recs, a.gpu1, a.name)
        return 0
    if a.cmd.startswith("x19-"):  # X19's stop-rule test (OUT: its folder; recordings in OUT/recs)
        _watch_rss()
        from tools import merge_accuracy_study as MS

        recs = Path(a.recs) if a.recs else Path(a.out) / "recs"
        if a.cmd == "x19-render":
            print(x19_render(a.out, a.name), flush=True)
        elif a.cmd == "x19-single":
            x19_register(MS.SC, [x19_rec_name(a.name)])
            MS.run_single(Path(a.out) / "work", recs, a.name, "tracked")
        elif a.cmd == "x19-pair":
            print(json.dumps(_plain({k: v for k, v in x19_pair(a.out, recs, a.name).items() if k in ("off", "on")}))[:3000])
        else:
            x19_judge(a.out, recs, a.gpu1, a.name)
        return 0
    if a.cmd in ("plan", "single", "today", "perscan", "judge-exp", "verdict", "tagmerge", "judge-tags"):  # OUT: its folder
        _watch_rss()
        if a.cmd == "plan":
            plan_experiment(a.x7, a.mergewarn, a.recs, a.out)
            return 0
        ex = Experiment(a.out, a.recs)
        if a.cmd == "single":
            ex.MS.run_single(ex.work, ex.recs, a.name, "tracked")
        elif a.cmd == "today":
            run_today(ex, a.name)
        elif a.cmd == "perscan":
            print(json.dumps(_plain(run_perscan(ex, a.name)))[:2000], flush=True)
        elif a.cmd == "judge-exp":
            run_judge_exp(ex, a.name)
        elif a.cmd == "tagmerge":
            print(json.dumps(_plain(run_tag_merges(ex, a.name, ap_runs)))[:2000], flush=True)
        elif a.cmd == "judge-tags":
            judge_tag_merges(ex, a.name)
        else:
            v = exp_verdict(ex)
            print("killed" if v["killed"] else "alive", json.dumps(_plain(v["kill"]))[:3000])
        return 0
    if a.cmd == "table":
        print_table(a.out)
        return 0
    study = Study(a.x7, a.mergewarn, a.recs)
    if a.cmd == "combine":
        for case in a.cases or []:
            pair, v = case.split(":")
            r = run_combine(study, a.out, pair, v)
            print(pair, v, "kill", r["kill"], f"{r['seconds']} s", flush=True)
    else:
        for pair in a.pairs:
            r = run_tags(study, a.out, pair)
            print(pair, "tags", f"{r['seconds']} s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
