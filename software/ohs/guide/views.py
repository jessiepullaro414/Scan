"""Where to hold the scanner to measure a weak area again (FEATURE_PLAN N1).

:func:`suggest_view` takes one area of :func:`ohs.mesh.check.weak_areas` (its faces, centre and
normal) and the scan's :class:`~ohs.mesh.check.ScanViews` (the calibration's working distance and
depth range, the cameras' field of view and baseline), and tries :data:`TILTS_DEG` x
:data:`AZIMUTHS` directions around the area's normal: for each, the scanner stands on that line at
the working distance, its cameras' centre line through the area's centre (the line lasers cross
there), and the area's vertices (at most :data:`VIEW_SAMPLES`) are tested from both cameras:

* **outside**: not in the left camera's image, or not within the depth range;
* **facing away**: the vertex's normal points away from either camera: the camera would look into
  the part from behind (a thin plate's other face, the inside of a wall);
* **steep**: the left camera sees it more than :data:`~ohs.mesh.check.STEEP_DEG` off its normal
  (it would be "steep" again);
* **blocked**: the shadow ray from the vertex to either camera hits the mesh on its way: the part
  itself is in the way. Rays start :data:`~ohs.mesh.check.RAY_OFFSET_MM` off the surface, so a
  sheet of any thickness in the way blocks; something closer than that to the vertex does not
  (``tests/test_guide_views.py`` measures it on a 0.5 mm plate).

What is none of these is **seen**. Where the area lies next to the mesh's open border (surface the
scan did not see: a shadow, or where it ended), the border's vertices near it are tested too, as
**looked past**: in view, the camera on the side the mesh does not go on to (the border's way out
of the mesh, :func:`ohs.mesh.check._open_border`), and nothing in the way. A direction's score is
the share of the area seen (weighted by surface) plus the share of that border looked past: a pose
that measures the area again *and* looks at what lies beyond it. The best score wins, the smaller
tilt on a tie (:data:`TIE_SHARE`). Under :data:`VIEW_MIN_SHARE` of the area seen no pose is
returned: the answer says "cannot be seen from outside the part" and what stopped the directions
tried. An area whose reason is ``edge`` gets no pose either (another pass does not lower that term).

:func:`plan_views` turns the poses of several areas into a short list: an area already seen for the
most part (:data:`PLAN_SEEN_SHARE`) by a pose chosen for a larger one gets none of its own, and when
that leaves fewer poses than asked for, the largest areas' next-best directions are added, each at
least :data:`PLAN_APART_DEG` from those chosen.

The pose is camera C's (the rectified left camera, the frame the scan's trajectory uses) in the
scan's frame, plus the numbers a page can draw: the scanner's centre, the direction it looks, the
distance and the share of the area it would see. Nothing here moves a scan's result.

Speed: the shadow rays of all the directions tried for an area are cast at once, and so are those of
all areas a plan's pose is tested on; ``context`` (:class:`ohs.mesh.check.CheckMesh`) shares the
normals, the ray-casting scene and the open border with the check that found the areas.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

from ohs.mesh.check import (STEEP_COS, CheckMesh, ScanViews, _border_mm, _open_border, _ray_scene, _unblocked,
                            even_sample, pinhole_mm)
from ohs.mesh.topology import as_mesh, face_areas, rows, sorted_unique, vertex_normals

__all__ = ["suggest_view", "suggest_views", "plan_views", "TILTS_DEG", "AZIMUTHS", "VIEW_SAMPLES", "VIEW_MIN_SHARE",
           "TIE_SHARE", "PLAN_SEEN_SHARE", "PLAN_APART_DEG", "scanner_pose"]

#: tilts of the candidate directions from the area's normal (degrees): square on, and rings up to
#: 55, which keeps the area's centre squarer than the steep angle (59.6)
TILTS_DEG = (0.0, 20.0, 40.0, 55.0)
#: directions per ring (every 45 degrees round the normal)
AZIMUTHS = 8
#: an area's vertices tested from each candidate (evenly by index; a share to about 5 %)
VIEW_SAMPLES = 300
#: a pose that would see less than this share of the area is no pose for it
VIEW_MIN_SHARE = 0.1
#: a larger tilt wins only if it scores this much more (else the squarer one)
TIE_SHARE = 0.02
#: a plan gives an area no pose of its own when the poses chosen already see this share of it
PLAN_SEEN_SHARE = 0.5
#: a plan's extra directions keep at least this angle (degrees) from every direction chosen
PLAN_APART_DEG = 60.0


def _frame(d: np.ndarray, up: np.ndarray) -> np.ndarray:
    """Camera rotation (columns x, y, z = OpenCV right, down, forward) looking along ``-d`` with
    image-up as close to ``up`` as the view allows."""
    z = -d / np.linalg.norm(d)
    for u in (np.asarray(up, dtype=np.float64), np.array([0.0, 0.0, 1.0]), np.array([0.0, 1.0, 0.0])):
        y = -(u - (u @ z) * z)
        if np.linalg.norm(y) > 1e-6:
            break
    y /= np.linalg.norm(y)
    x = np.cross(y, z)
    return np.stack([x, y, z], axis=1)


def scanner_pose(target: np.ndarray, d: np.ndarray, views: ScanViews) -> np.ndarray:
    """``T_wc`` of camera C for the scanner on the line ``target + s d`` at the working distance,
    looking back at ``target`` with its cameras' centre line (``x = baseline / 2``) through it."""
    R = _frame(np.asarray(d, dtype=np.float64), views.up)
    centre = np.asarray(target, dtype=np.float64) + float(views.working_mm) * (np.asarray(d) / np.linalg.norm(d))
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = centre - 0.5 * float(views.baseline_mm) * R[:, 0]
    return T


def _directions(normal: np.ndarray) -> list[tuple[float, float, np.ndarray]]:
    n = np.asarray(normal, dtype=np.float64)
    n = n / np.linalg.norm(n)
    a = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(n, a)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(n, e1)
    out = []
    for tilt in TILTS_DEG:
        t = math.radians(tilt)
        for k in range(1 if tilt == 0 else AZIMUTHS):
            az = 2.0 * math.pi * k / AZIMUTHS
            out.append((tilt, math.degrees(az), math.cos(t) * n + math.sin(t) * (math.cos(az) * e1 + math.sin(az) * e2)))
    return out


def _cast(scene: Any, rays: list[tuple[np.ndarray, np.ndarray, np.ndarray]]) -> list[np.ndarray]:
    """:func:`~ohs.mesh.check._unblocked` of several ``(P, normals, camera)`` at once (one cast of all
    their rays: the same answer per ray as one cast each)."""
    sizes = [len(p) for p, _, _ in rays]
    if not sum(sizes):
        return [np.zeros(0, dtype=bool) for _ in rays]
    P = np.concatenate([p for p, _, _ in rays])
    N = np.concatenate([n for _, n, _ in rays])
    C = np.concatenate([np.broadcast_to(np.asarray(c, dtype=np.float64), (len(p), 3)) for p, _, c in rays])
    ok = _unblocked(scene, P, N, C)
    cut = np.cumsum([0] + sizes)
    return [ok[cut[i]:cut[i + 1]] for i in range(len(rays))]


def _judge(T: np.ndarray, P: np.ndarray, Nrm: np.ndarray, views: ScanViews, scene: Any) -> dict[str, np.ndarray]:
    """Per vertex from the pose ``T`` (camera C): ``outside``, ``away``, ``steep``, ``blocked``, ``seen``."""
    return _judge_many([T], P, Nrm, views, scene)[0]


def _judge_many(Ts: Sequence[np.ndarray], P: np.ndarray, Nrm: np.ndarray, views: ScanViews,
                scene: Any) -> list[dict[str, np.ndarray]]:
    """:func:`_judge` from each pose of ``Ts``, the shadow rays of all of them cast at once."""
    w, h = views.image_size
    K = views.K
    pend = []
    for T in Ts:
        R, t = T[:3, :3], T[:3, 3]
        right = t + float(views.baseline_mm) * R[:, 0]
        Pc = (P - t) @ R
        z = Pc[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = K[0, 0] * Pc[:, 0] / z + K[0, 1] * Pc[:, 1] / z + K[0, 2]
            v = K[1, 1] * Pc[:, 1] / z + K[1, 2]
        outside = ~((z >= views.near_mm) & (z <= views.far_mm) & (u >= -0.5) & (u < w - 0.5) & (v >= -0.5)
                    & (v < h - 0.5))
        cos = []
        for c in (t, right):
            r = c[None] - P
            cos.append(np.einsum("ij,ij->i", Nrm, r) / np.maximum(np.linalg.norm(r, axis=1), 1e-12))
        away = (cos[0] <= 0) | (cos[1] <= 0)
        steep = ~away & (cos[0] < STEEP_COS)
        test = np.flatnonzero(~outside & ~away)
        pend.append((outside, away, steep, test, t, right))
    rays = []
    for _, _, _, test, t, right in pend:
        rays += [(P[test], Nrm[test], t), (P[test], Nrm[test], right)]
    hits = _cast(scene, rays)
    out = []
    for i, (outside, away, steep, test, _, _) in enumerate(pend):
        blocked = np.zeros(len(P), dtype=bool)
        if len(test):
            blocked[test] = ~(hits[2 * i] & hits[2 * i + 1])
        outside = outside & ~away
        seen = ~outside & ~away & ~steep & ~blocked
        out.append({"outside": outside, "away": away, "steep": steep & ~blocked & ~outside, "blocked": blocked,
                    "seen": seen})
    return out


def _missed(j: dict[str, np.ndarray], w: np.ndarray) -> dict[str, float]:
    tot = float(w.sum())
    return {"blocked": float(w[j["blocked"]].sum() / tot), "facing_away": float(w[j["away"]].sum() / tot),
            "steep": float(w[j["steep"]].sum() / tot), "outside": float(w[j["outside"]].sum() / tot)}


_MISS_WORDS = {"blocked": "the part itself is in the way", "facing_away": "it faces into the part",
               "steep": "it is seen only at a steep angle", "outside": "it falls outside the cameras' view"}


class _Mesh:
    """What every area's pose search of one mesh shares: normals, ray-casting scene, open border."""

    def __init__(self, V: np.ndarray, F: np.ndarray, normals: np.ndarray | None, scene: Any, border_mm: float,
                 context: CheckMesh | None = None) -> None:
        self.border_near = 2.0 * float(border_mm)
        # both lengths scale with the voxel size alike (ohs.mesh.check.pinhole_mm, _border_mm)
        min_loop = pinhole_mm(None) * float(border_mm) / _border_mm(None)
        if context is not None:  # shared with the check that found the areas
            if not context.fits(*as_mesh(V, F)):
                raise ValueError("the context is of another mesh")
            self.V, self.F, self.N = context.V, context.F, context.N
            self.scene = context.scene() if scene is None else scene
            self.bv, self.bout, self.btree = context.border(min_loop)
            return
        self.V, self.F = as_mesh(V, F)
        N = vertex_normals(self.V, self.F) if normals is None else np.asarray(normals, dtype=np.float64).reshape(-1, 3)
        ln = np.linalg.norm(N, axis=1, keepdims=True)
        self.N = np.where(ln > 1e-12, N / np.maximum(ln, 1e-12), 0.0)
        self.scene = _ray_scene(self.V, self.F) if scene is None else scene
        self.bv, self.bout = _open_border(self.V, self.F, min_loop)
        self.btree = None
        if len(self.bv):
            from scipy.spatial import cKDTree  # noqa: PLC0415

            self.btree = cKDTree(rows(self.V, self.bv))

    def points(self, area: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """``(P, normals, weights)`` of the area's sampled vertices, and ``(B, outward)`` of the open
        border's vertices next to it (none when it has no border near)."""
        faces = np.asarray(area["faces"], dtype=np.int64)
        Fa = self.F[faces]
        verts = sorted_unique(Fa)
        # each vertex weighs a third of the area's faces at it
        wv = np.bincount(Fa.ravel(), weights=np.repeat(face_areas(self.V, Fa) / 3.0, 3), minlength=len(self.V))[verts]
        # samples evenly in the order of where the vertices lie, not of their numbers (ohs.mesh.check.even_sample)
        pick = even_sample(verts, rows(self.V, verts), VIEW_SAMPLES)
        verts, wv = verts[pick], wv[pick]
        P, Nv = rows(self.V, verts), rows(self.N, verts)
        B, Bo = np.zeros((0, 3)), np.zeros((0, 3))
        if self.btree is not None:
            # the border near the sampled vertices, sampled the same way
            d, j = self.btree.query(P, distance_upper_bound=self.border_near)
            jj = sorted_unique(j[np.isfinite(d)])
            jj = jj[even_sample(jj, rows(self.V, self.bv[jj]), VIEW_SAMPLES)]
            B, Bo = rows(self.V, self.bv[jj]), self.bout[jj]
        return P, Nv, np.maximum(wv, 1e-12), B, Bo


def _looked_past(T: np.ndarray, B: np.ndarray, Bo: np.ndarray, views: ScanViews, scene: Any) -> np.ndarray:
    """Per border vertex: seen from the pose ``T`` from the side the mesh does not go on to (module docstring)."""
    return _looked_past_many([T], B, Bo, views, scene)[0]


def _looked_past_many(Ts: Sequence[np.ndarray], B: np.ndarray, Bo: np.ndarray, views: ScanViews,
                      scene: Any) -> list[np.ndarray]:
    """:func:`_looked_past` from each pose of ``Ts``, the shadow rays of all of them cast at once."""
    if len(B) == 0:
        return [np.zeros(0, dtype=bool) for _ in Ts]
    w, h = views.image_size
    K = views.K
    oks, js, rays = [], [], []
    for T in Ts:
        R, t = T[:3, :3], T[:3, 3]
        Pc = (B - t) @ R
        z = Pc[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = K[0, 0] * Pc[:, 0] / z + K[0, 1] * Pc[:, 1] / z + K[0, 2]
            v = K[1, 1] * Pc[:, 1] / z + K[1, 2]
        ok = (z >= views.near_mm) & (z <= views.far_mm) & (u >= -0.5) & (u < w - 0.5) & (v >= -0.5) & (v < h - 0.5)
        ok &= np.einsum("ij,ij->i", Bo, t[None] - B) > 0
        j = np.flatnonzero(ok)
        oks.append(ok)
        js.append(j)
        rays.append((B[j], Bo[j], t))
    hits = _cast(scene, rays)
    for ok, j, hit in zip(oks, js, hits):
        if len(j):
            ok[j] = hit
    return oks


def suggest_view(V: np.ndarray, F: np.ndarray, area: dict, views: ScanViews, *, normals: np.ndarray | None = None,
                 scene: Any = None, border_mm: float | None = None, _mesh: _Mesh | None = None) -> dict:
    """The pose to scan ``area`` (a dict of :func:`ohs.mesh.check.weak_areas`) from (module docstring).

    Returns ``{"ok": bool, "share_seen", "border_seen"`` (the share of its open border looked past;
    None without one), ``"candidates", "missed": {blocked, facing_away, steep, outside}, "text",
    "alternatives"}`` and, when ``ok``, ``pose``: ``{"T_wc"`` (4x4 list, camera C in the scan's
    frame), ``"position_mm"`` (the scanner's centre), ``"camera_position_mm"`` (camera C),
    ``"look_dir"`` (unit, from the scanner to the area), ``"up"`` (image up), ``"target_mm"``,
    ``"distance_mm"``, ``"tilt_deg"`` (from the area's normal), ``"azimuth_deg"}``; when not,
    ``"reason"`` in words ("cannot be seen from outside the part: ...") and ``pose`` None.
    ``alternatives``: the other directions that see at least :data:`VIEW_MIN_SHARE`, best first,
    each ``{"look_dir", "tilt_deg", "azimuth_deg", "share_seen", "border_seen", "score"}``.
    ``border_mm``: the length over which the model's border term falls off (default: 1 mm voxels'
    1.5 mm; :func:`suggest_views` takes it from the Check answer)."""
    if area.get("reason") == "edge":
        return {"ok": False, "pose": None, "share_seen": None, "border_seen": None, "candidates": 0, "missed": None,
                "alternatives": [],
                "reason": "no place to scan from: on an edge or a tight curve another pass alone does not lower the scatter",
                "text": "No place to scan from: on an edge or a tight curve another pass alone does not lower the scatter."}
    m = _mesh or _Mesh(V, F, normals, scene, _border_mm(None) if border_mm is None else border_mm)
    P, Nv, w, B, Bo = m.points(area)
    normal = np.asarray(area.get("normal") or Nv.sum(axis=0), dtype=np.float64)
    if np.linalg.norm(normal) < 1e-9:
        normal = Nv.sum(axis=0)
    if np.linalg.norm(normal) < 1e-9:
        normal = np.asarray(views.up, dtype=np.float64)
    target = np.asarray(area["center"], dtype=np.float64)
    tried = _directions(normal)
    worst_miss = {"blocked": 0.0, "facing_away": 0.0, "steep": 0.0, "outside": 0.0}
    results = []
    Ts = [scanner_pose(target, d, views) for _, _, d in tried]
    judged = _judge_many(Ts, P, Nv, views, m.scene)  # every direction's rays at once
    pasts = _looked_past_many(Ts, B, Bo, views, m.scene) if len(B) else [None] * len(Ts)
    for (tilt, az, d), T, j, lp in zip(tried, Ts, judged, pasts):
        share = float(w[j["seen"]].sum() / w.sum())
        past = float(lp.mean()) if len(B) else None
        miss = _missed(j, w)
        for kk, vv in miss.items():
            worst_miss[kk] += vv / len(tried)
        score = share + (past or 0.0) if share >= VIEW_MIN_SHARE else share
        results.append((score, tilt, az, d, T, miss, share, past))
    top = max(r[0] for r in results)
    share, tilt, az, d, T, miss = _pick(results, top)
    past = next(r[7] for r in results if r[3] is d)
    alts = [{"look_dir": [float(x) for x in -r[3]], "tilt_deg": float(r[1]), "azimuth_deg": float(r[2]),
             "share_seen": r[6], "border_seen": r[7], "score": r[0]}
            for r in sorted(results, key=lambda r: (-r[0], r[1])) if r[3] is not d and r[6] >= VIEW_MIN_SHARE]
    out: dict[str, Any] = {"share_seen": share, "border_seen": past, "candidates": len(tried), "missed": miss,
                           "alternatives": alts}
    if share < VIEW_MIN_SHARE:
        top_miss = max(worst_miss, key=worst_miss.get)
        why = (f"cannot be seen from outside the part: from {len(tried)} directions within {TILTS_DEG[-1]:.0f}° of its "
               f"normal the best sees {100 * share:.0f} % of it; mostly {_MISS_WORDS[top_miss]} "
               f"(on average {100 * worst_miss[top_miss]:.0f} % of it)")
        return {**out, "ok": False, "pose": None, "reason": why, "text": why[0].upper() + why[1:] + "."}
    pose = _pose_dict(target, d, T, tilt, az, views)
    text = (f"Hold the scanner about {views.working_mm:.0f} mm from the area"
            + (", square on to it" if tilt == 0 else f", {tilt:.0f}° off square") + f": it would see {100 * share:.0f} % of it"
            + ("." if not past else f" and look past {100 * past:.0f} % of the unseen edge beside it."))
    return {**out, "ok": True, "pose": pose, "reason": None, "text": text}


def _pick(results: list, top: float) -> tuple:
    """The squarest direction of those within :data:`TIE_SHARE` of the best score:
    ``(share, tilt, azimuth, d, T, missed)``."""
    r = min((r for r in results if r[0] >= top - TIE_SHARE), key=lambda r: (r[1], -r[0]))
    return r[6], r[1], r[2], r[3], r[4], r[5]


def _pose_dict(target: np.ndarray, d: np.ndarray, T: np.ndarray, tilt: float, az: float, views: ScanViews) -> dict:
    centre = target + float(views.working_mm) * d
    return {"T_wc": T.tolist(), "position_mm": [float(x) for x in centre],
            "camera_position_mm": [float(x) for x in T[:3, 3]], "look_dir": [float(x) for x in -d],
            "up": [float(x) for x in -T[:3, 1]], "target_mm": [float(x) for x in target],
            "distance_mm": float(views.working_mm), "tilt_deg": float(tilt), "azimuth_deg": float(az)}


def _border_of(areas: dict | Sequence[dict]) -> float | None:
    if isinstance(areas, dict):
        return (areas.get("reasons") or {}).get("border_mm")
    return None


def suggest_views(V: np.ndarray, F: np.ndarray, areas: dict | Sequence[dict], views: ScanViews, *,
                  normals: np.ndarray | None = None, context: CheckMesh | None = None) -> list[dict]:
    """:func:`suggest_view` for every area (a :func:`~ohs.mesh.check.weak_areas` result or its
    ``areas`` list), in the same order; the ray-casting scene and the open border are made once (or
    taken from ``context``, the :class:`~ohs.mesh.check.CheckMesh` of this mesh)."""
    lst = areas["areas"] if isinstance(areas, dict) else list(areas)
    if not lst:
        return []
    bm = _border_of(areas)
    m = _Mesh(V, F, normals, None, _border_mm(None) if bm is None else bm, context)
    return [suggest_view(m.V, m.F, a, views, _mesh=m) for a in lst]


def plan_views(V: np.ndarray, F: np.ndarray, areas: dict | Sequence[dict], views: ScanViews, k: int = 3, *,
               normals: np.ndarray | None = None, select: Sequence[int] | None = None,
               suggestions: Sequence[dict] | None = None, context: CheckMesh | None = None) -> list[dict]:
    """Up to ``k`` poses for the areas (a :func:`~ohs.mesh.check.weak_areas` result or its list;
    ``select``: the indices of the areas to plan for, default all), largest area first (module
    docstring). Each: ``{"area"`` (index), ``"pose"``, ``"share_seen"`` (of that area),
    ``"border_seen"``, ``"also_sees"`` ({area index: share} of the other selected areas),
    ``"kind"`` ("area": the area's own pose; "more": a next-best direction of a large area)``}``.
    ``suggestions``: :func:`suggest_views` of the same areas, when already made; ``context``: as for
    :func:`suggest_views`."""
    lst = areas["areas"] if isinstance(areas, dict) else list(areas)
    idx = list(range(len(lst))) if select is None else [int(i) for i in select]
    if not idx or k <= 0:
        return []
    bm = _border_of(areas)
    m = _Mesh(V, F, normals, None, _border_mm(None) if bm is None else bm, context)
    sugg = list(suggestions) if suggestions is not None else [suggest_view(m.V, m.F, a, views, _mesh=m) for a in lst]
    pts = {i: m.points(lst[i]) for i in idx}
    # every selected area's points in one array: a pose is judged on all of them at once
    allP = np.concatenate([pts[i][0] for i in idx])
    allN = np.concatenate([pts[i][1] for i in idx])
    cut = np.cumsum([0] + [len(pts[i][0]) for i in idx])
    chosen: list[dict] = []

    def sees(T: np.ndarray) -> dict[int, float]:
        seen = _judge(T, allP, allN, views, m.scene)["seen"]
        out = {}
        for n, i in enumerate(idx):
            w = pts[i][2]
            out[i] = float(w[seen[cut[n]:cut[n + 1]]].sum() / w.sum())
        return out

    seen_by: list[dict[int, float]] = []
    for i in idx:  # the areas' own poses, largest first, skipping an area the chosen ones already see
        if len(chosen) >= k:
            break
        s = sugg[i]
        if not s["ok"] or any(sb.get(i, 0.0) >= PLAN_SEEN_SHARE for sb in seen_by):
            continue
        T = np.asarray(s["pose"]["T_wc"])
        sb = sees(T)
        seen_by.append(sb)
        chosen.append({"area": i, "pose": s["pose"], "share_seen": s["share_seen"], "border_seen": s["border_seen"],
                       "also_sees": {a: v for a, v in sb.items() if a != i and v > 0}, "kind": "area"})
    dirs = [-np.asarray(c["pose"]["look_dir"]) for c in chosen]
    apart = math.cos(math.radians(PLAN_APART_DEG))
    for i in idx:  # more directions for the largest areas, apart from those chosen
        if len(chosen) >= k:
            break
        for alt in sugg[i].get("alternatives") or []:
            if len(chosen) >= k:
                break
            d = -np.asarray(alt["look_dir"])
            if any(float(d @ e) > apart for e in dirs):
                continue
            target = np.asarray(lst[i]["center"], dtype=np.float64)
            T = scanner_pose(target, d, views)
            sb = sees(T)
            dirs.append(d)
            chosen.append({"area": i, "pose": _pose_dict(target, d, T, alt["tilt_deg"], alt["azimuth_deg"], views),
                           "share_seen": alt["share_seen"], "border_seen": alt["border_seen"],
                           "also_sees": {a: v for a, v in sb.items() if a != i and v > 0}, "kind": "more"})
    while len(chosen) < k and dirs:  # none far enough apart: the one farthest from those chosen
        cands = [(max(float(-np.asarray(alt["look_dir"]) @ e) for e in dirs), i, alt)
                 for i in idx for alt in sugg[i].get("alternatives") or []]
        cands = [c for c in cands if c[0] < 0.999]
        if not cands:
            break
        _, i, alt = min(cands, key=lambda c: c[0])
        d = -np.asarray(alt["look_dir"])
        target = np.asarray(lst[i]["center"], dtype=np.float64)
        T = scanner_pose(target, d, views)
        sb = sees(T)
        dirs.append(d)
        chosen.append({"area": i, "pose": _pose_dict(target, d, T, alt["tilt_deg"], alt["azimuth_deg"], views),
                       "share_seen": alt["share_seen"], "border_seen": alt["border_seen"],
                       "also_sees": {a: v for a, v in sb.items() if a != i and v > 0}, "kind": "more"})
    return chosen
