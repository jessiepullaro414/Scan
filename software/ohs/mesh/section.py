"""A section: the mesh cut by a plane, as the 2D profile an engineer measures and traces (FEATURE_PLAN I1).

:func:`section` cuts a mesh ``(V, F)`` with a :class:`SectionPlane` and returns the cut as ordered
lines (:class:`SectionLine`), each in the plane's own 2D coordinates (mm) and in 3D, closed where
the surface goes all round and open where it has a border or a hole. :mod:`ohs.cad.dxf` and
:mod:`ohs.cad.svg` write it at 1:1.

**The plane** (:class:`SectionPlane`) is made from a point and a normal
(:func:`plane_from_point_normal`), a fitted plane moved along its normal
(:func:`plane_from_fit`), a plane of the part's frame, such as Z = 12 (:func:`plane_from_frame`),
or three points (:func:`plane_from_points`). Points and normals are always given in the scan's
frame. The drawing is the cut **as seen from the side the normal points to**, and its two axes
``u`` (right) and ``v`` (up) are chosen by one fixed rule, so the same plane always gives the same
drawing:

* "up" is the Z axis turned into the plane, unless the plane lies within 45° of horizontal
  (``|n . Z| > cos 45°``); then it is the Y axis turned into the plane. ``v`` is that direction,
  ``u = v x n``, so ``(u, v, n)`` is right-handed and the drawing is never mirrored;
* the 2D coordinates are measured from the point where the frame's origin falls on the plane.

Without a part frame, X, Y, Z and the origin are the scan's; with a :class:`~ohs.measure.frame.PartFrame`
they are the part's. So the plane Z = c of the part is drawn in the part's (X, Y), X = c in
(Y, Z), and Y = c seen from -Y (the front view) in (X, Z); a horizontal cut of the scan reads in
the scan's (x, y).

**The cut.** Each vertex's signed distance to the plane is taken once; a vertex closer than
:data:`SNAP` of the mesh's size (a few nanometres on a part) counts as lying in the plane. Then:

* a triangle with corners on both sides gives one segment, between the points where two of its
  edges cross the plane, or from a corner lying in the plane to the opposite edge. The point on
  an edge is computed once per edge, from the edge's own two ends, so the two triangles of an edge
  share it exactly and the segments join on integer keys, never on distances;
* an edge lying in the plane is part of the cut once (not once per face), unless both of its
  faces lie in the plane (it is then inside a flat area, like the diagonal of a box's face);
* a triangle lying in the plane adds nothing itself: the outline of the flat area it belongs to
  is the cut (by the rule above), so a box cut exactly at its top face gives the top's outline;
* a corner touching the plane with the rest of the surface on one side gives nothing (a point is
  not a line);
* the vertices of the cut faces that sit at the very same position are taken as one (an unwelded
  mesh, as STL files are, is cut as if it were welded), points that fall together are merged, and
  a segment given twice (a face given twice) is kept once. No segment has zero length.

**Joining.** The segments are joined at their shared points. A point with two segments continues
the line. Where more meet (a fin standing on the surface, a non-manifold spot, a saddle exactly in
the plane), the segments are paired around the point, an arriving one with the next leaving one
counter-clockwise, as their faces' orientation says; what cannot be paired ends there. A line that
comes back to a point it passed is split there into a closed loop and the rest, so no line holds a
point twice. A line whose ends meet is closed (its first point is not repeated at the end).

**Direction and sign.** Each segment's direction comes from its face's orientation (right-hand
rule): on a mesh with outward normals, an outline runs counter-clockwise in the drawing and has a
positive area, a hole inside it runs clockwise and has a negative area, whichever way the normal
points. A line takes the direction most of its segments agree on. Lines are listed longest
first.

**Sigma and filled faces.** With a per-vertex ``sigma`` (mm), each point carries it: at a crossing
it is interpolated along the edge like the point itself. With a per-face ``filled`` flag (faces
made by hole filling, not measured), each segment says whether it runs over a filled face (an
edge in the plane: when all its faces are filled), so a drawing can show the stretch that was not
measured differently (:meth:`SectionLine.stretches`).

**Speed.** Only the faces with corners on both sides (or in the plane) are looked at after one
pass over the vertices and one over the faces; on a mesh of a million triangles the cut takes
tens of milliseconds (``tests/test_mesh_section.py``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ohs.mesh.topology import as_mesh, rows

__all__ = ["SectionPlane", "SectionLine", "Section", "section", "plane_from_point_normal", "plane_from_fit",
           "plane_from_frame", "plane_from_points", "SNAP", "FORMAT_VERSION"]

FORMAT_VERSION = 1  # of Section.to_dict()
SNAP = 1e-9  # a vertex this close to the plane, relative to the mesh's size, lies in it
_UP_LIMIT = math.sqrt(0.5)  # |n . Z| above this (the plane within 45° of horizontal): "up" is Y, not Z


# --------------------------------------------------------------------------- the plane
@dataclass
class SectionPlane:
    """A cutting plane and its drawing axes (see the module docstring).

    ``point`` is the 2D origin (where the frame's origin falls on the plane), ``normal`` the side
    the drawing is seen from, ``u`` and ``v`` the drawing's right and up, all in the scan's frame.
    """

    point: np.ndarray  # (3,) on the plane: (0, 0) in the drawing
    normal: np.ndarray  # (3,) unit
    u: np.ndarray  # (3,) unit, the drawing's x
    v: np.ndarray  # (3,) unit, the drawing's y; u x v = normal
    description: str
    in_part_frame: bool = False  # the axes and the 2D origin are the part's

    def distance(self, X) -> np.ndarray:
        """Signed distance (mm) of points (k,3) to the plane, + on the normal's side."""
        return (np.asarray(X, dtype=np.float64).reshape(-1, 3) - self.point) @ self.normal

    def to_2d(self, X) -> np.ndarray:
        """Points (k,3) in the scan's frame -> the drawing's (k,2) (projected onto the plane)."""
        D = np.asarray(X, dtype=np.float64).reshape(-1, 3) - self.point
        return np.stack([D @ self.u, D @ self.v], axis=1)

    def to_3d(self, P) -> np.ndarray:
        """Drawing points (k,2) -> the scan's frame (k,3), on the plane."""
        P = np.asarray(P, dtype=np.float64).reshape(-1, 2)
        return self.point + P[:, :1] * self.u + P[:, 1:] * self.v

    def to_dict(self) -> dict:
        return {"point": [float(x) for x in self.point], "normal": [float(x) for x in self.normal],
                "u": [float(x) for x in self.u], "v": [float(x) for x in self.v], "description": self.description,
                "in_part_frame": bool(self.in_part_frame)}


def _vec(x, what: str) -> np.ndarray:
    try:
        a = np.asarray(x, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError):
        a = np.zeros(0)
    if a.shape != (3,) or not np.all(np.isfinite(a)):
        raise ValueError(f"the {what} must be three finite numbers")
    return a


def _make_plane(point, normal, frame, description: str) -> SectionPlane:
    """The plane through ``point`` with ``normal``, with its drawing axes by the module's rule."""
    p = _vec(point, "point")
    n = _vec(normal, "normal")
    ln = float(np.linalg.norm(n))
    if ln == 0.0:
        raise ValueError("the normal must not be zero")
    n = n / ln
    if frame is None:
        R, O = np.eye(3), np.zeros(3)
    else:
        R, O = np.asarray(frame.rotation, dtype=np.float64), np.asarray(frame.origin, dtype=np.float64)
    up = R[2] if abs(float(n @ R[2])) <= _UP_LIMIT else R[1]
    v = up - float(up @ n) * n
    v /= np.linalg.norm(v)
    u = np.cross(v, n)
    u /= np.linalg.norm(u)
    origin = O - float((O - p) @ n) * n
    return SectionPlane(point=origin, normal=n, u=u, v=v, description=str(description),
                        in_part_frame=frame is not None)


def _fmt(p, digits: int = 3) -> str:
    return "(" + ", ".join(f"{float(x):.{digits}f}" for x in p) + ")"


def plane_from_point_normal(point, normal, *, frame=None, description: str | None = None) -> SectionPlane:
    """The plane through ``point`` (mm) with ``normal`` (any length), both in the scan's frame.
    With ``frame`` (a :class:`~ohs.measure.frame.PartFrame`) the drawing's axes are the part's."""
    if description is None:
        description = f"Section through {_fmt(_vec(point, 'point'))} mm, normal {_fmt(_vec(normal, 'normal'), 4)}"
    return _make_plane(point, normal, frame, description)


def plane_from_fit(fit, offset_mm: float = 0.0, *, frame=None, description: str | None = None) -> SectionPlane:
    """A fitted plane moved ``offset_mm`` along its normal: a :class:`~ohs.measure.fit.PlaneFit`,
    its ``to_dict()``, or a kept feature or region holding one under ``fit``. The normal keeps the
    fit's sign; ValueError for anything that is not a plane."""
    name = None
    if not isinstance(fit, dict) and hasattr(fit, "fit") and not hasattr(fit, "normal"):
        fit = fit.fit  # a RegionFit
    if isinstance(fit, dict) and isinstance(fit.get("fit"), dict):
        name = fit.get("name")
        fit = fit["fit"]
    plain = fit if isinstance(fit, dict) else fit.to_dict() if hasattr(fit, "to_dict") else {}
    if plain.get("kind") != "plane":
        raise ValueError(f"a section needs a plane, got {plain.get('kind')!r}")
    off = float(offset_mm)
    if not math.isfinite(off):
        raise ValueError("the offset must be a finite number of mm")
    n = _vec(plain.get("normal"), "plane's normal")
    if not np.linalg.norm(n):
        raise ValueError("the plane's normal must not be zero")
    n = n / np.linalg.norm(n)
    p = _vec(plain.get("point"), "plane's point") + off * n
    if description is None:
        description = f"Section on {name or 'the fitted plane'}"
        description += f", moved {off:+g} mm along its normal" if off else ""
    return _make_plane(p, n, frame, description)


def plane_from_frame(frame, axis: str, value_mm: float, *, description: str | None = None) -> SectionPlane:
    """The plane ``axis = value_mm`` of the part's frame, seen from the axis's + side; a leading
    ``-`` (``"-Y"``) sees it from the other side (Y = c seen from -Y is the front view, drawn in
    the part's X and Z)."""
    a = str(axis).strip().upper()
    sign = -1.0 if a.startswith("-") else 1.0
    a = a.lstrip("+-")
    if a not in ("X", "Y", "Z"):
        raise ValueError(f"axis must be X, Y or Z (with an optional sign), got {axis!r}")
    value = float(value_mm)
    if not math.isfinite(value):
        raise ValueError("the value must be a finite number of mm")
    e = np.asarray(frame.rotation, dtype=np.float64)[["X", "Y", "Z"].index(a)]
    p = np.asarray(frame.origin, dtype=np.float64) + value * e
    if description is None:
        description = f"Section at {a} = {value:g} mm in the part's frame" + (f", seen from -{a}" if sign < 0 else "")
    return _make_plane(p, sign * e, frame, description)


def plane_from_points(p1, p2, p3, *, frame=None, description: str | None = None) -> SectionPlane:
    """The plane through three points; its normal by the right-hand rule, ``(p2 - p1) x (p3 - p1)``.
    ValueError when they lie on one line."""
    a, b, c = _vec(p1, "first point"), _vec(p2, "second point"), _vec(p3, "third point")
    n = np.cross(b - a, c - a)
    size = float(np.linalg.norm(b - a) * np.linalg.norm(c - a))
    if size == 0.0 or float(np.linalg.norm(n)) <= 1e-9 * size:
        raise ValueError("the three points lie on one line (or two are the same), so they do not make a plane")
    if description is None:
        description = f"Section through three points {_fmt(a)}, {_fmt(b)}, {_fmt(c)} mm"
    return _make_plane((a + b + c) / 3.0, n, frame, description)


# --------------------------------------------------------------------------- the result
@dataclass
class SectionLine:
    """One line of a section: points in order, closed or open (see the module docstring).

    A closed line does not repeat its first point; segment ``i`` runs from point ``i`` to point
    ``i + 1`` (and the last of a closed line back to point 0), so there are as many segments as
    points on a closed line and one fewer on an open one.
    """

    points: np.ndarray  # (k,2) mm, the drawing's coordinates
    points3d: np.ndarray  # (k,3) mm, the scan's frame
    closed: bool
    sigma: np.ndarray | None  # (k,) mm, or None when the mesh has none
    filled: np.ndarray  # (segments,) bool: the segment runs over filled (not measured) faces
    faces: np.ndarray  # (segments,) the face each segment lies on

    @property
    def length(self) -> float:
        """Length in mm."""
        P = np.vstack([self.points, self.points[:1]]) if self.closed else self.points
        return float(np.linalg.norm(np.diff(P, axis=0), axis=1).sum())

    @property
    def area(self) -> float | None:
        """Signed enclosed area (mm^2) of a closed line, + counter-clockwise (an outline), - clockwise
        (a hole); None for an open line."""
        if not self.closed:
            return None
        P = self.points - self.points.mean(axis=0)
        x, y = P[:, 0], P[:, 1]
        return float(0.5 * (x @ np.roll(y, -1) - y @ np.roll(x, -1)))

    @property
    def bbox(self) -> np.ndarray:
        """``[umin, vmin, umax, vmax]`` in the drawing (mm)."""
        return np.concatenate([self.points.min(axis=0), self.points.max(axis=0)])

    def stretches(self) -> list[tuple[np.ndarray, bool, bool]]:
        """The line cut where it passes from measured to filled faces and back: ``[(points (j,2),
        filled, closed)]`` in order, each stretch beginning at the point where the one before ends.
        A closed line on one kind of face is one closed stretch; a closed line with both kinds
        starts at a change, and its last stretch ends at its first point."""
        n = len(self.filled)
        if n == 0:
            return []
        f = np.asarray(self.filled, dtype=bool)
        if f.all() or not f.any():
            return [(self.points.copy(), bool(f[0]), bool(self.closed))]
        if self.closed:
            change = np.flatnonzero(f != np.roll(f, 1))
            order = (int(change[0]) + np.arange(n)) % n
            P = self.points[np.append(order, order[0])]
            f = f[order]
        else:
            P = self.points
        cuts = np.concatenate([[0], np.flatnonzero(f[1:] != f[:-1]) + 1, [n]])
        return [(P[a : b + 1].copy(), bool(f[a]), False) for a, b in zip(cuts[:-1], cuts[1:])]

    def to_dict(self) -> dict:
        return {"closed": bool(self.closed), "length_mm": self.length, "area_mm2": self.area,
                "bbox": [float(x) for x in self.bbox], "points": self.points.tolist(),
                "points3d": self.points3d.tolist(), "sigma": None if self.sigma is None else self.sigma.tolist(),
                "filled": [bool(x) for x in self.filled], "faces": [int(x) for x in self.faces]}


@dataclass
class Section:
    """The cut of a mesh by a plane: its lines, longest first (see the module docstring)."""

    plane: SectionPlane
    lines: list[SectionLine]

    @property
    def length(self) -> float:
        """All lines' length together (mm)."""
        return float(sum(ln.length for ln in self.lines))

    @property
    def n_points(self) -> int:
        return int(sum(len(ln.points) for ln in self.lines))

    @property
    def bbox(self) -> np.ndarray | None:
        """``[umin, vmin, umax, vmax]`` of all lines in the drawing (mm); None when nothing was cut."""
        if not self.lines:
            return None
        B = np.array([ln.bbox for ln in self.lines])
        return np.concatenate([B[:, :2].min(axis=0), B[:, 2:].max(axis=0)])

    def to_dict(self) -> dict:
        """A plain JSON object: the plane, the lines with their numbers and points, the totals."""
        bb = self.bbox
        return {"format_version": FORMAT_VERSION, "units": "mm", "plane": self.plane.to_dict(),
                "lines": [ln.to_dict() for ln in self.lines], "length_mm": self.length, "n_points": self.n_points,
                "bbox": None if bb is None else [float(x) for x in bb]}


# --------------------------------------------------------------------------- the cut
def _segments(V, F, s, filled, normal):
    """The segments of the cut: ``(A, B, pref, face, filled)``. ``A`` and ``B`` are node keys: a
    vertex lying in the plane is its index ``v``, the crossing on edge ``(lo, hi)`` is
    ``-1 - (lo * N + hi)``. ``pref`` is +1 when ``A -> B`` is the direction the face's orientation
    gives, -1 the other way, 0 when that is unknown."""
    N = len(V)
    sF = s[F]
    cand = np.flatnonzero(~((sF > 0).all(axis=1) | (sF < 0).all(axis=1)))
    Fc = F[cand]
    # vertices at the very same place are one (an unwelded mesh is cut as if welded)
    used = np.unique(Fc)
    if len(used) > 1:
        P = V[used]
        order = np.lexsort(P.T[::-1])
        Ps = P[order]
        new = np.empty(len(Ps), dtype=bool)
        new[0] = True
        new[1:] = np.any(Ps[1:] != Ps[:-1], axis=1)
        rep_sorted = used[order][np.flatnonzero(new)[np.cumsum(new) - 1]]
        rep = np.empty(len(used), dtype=np.int64)
        rep[order] = rep_sorted
        if np.any(rep != used):
            Fc = rep[np.searchsorted(used, Fc)]
    sc = s[Fc]
    zeros = (sc == 0).sum(axis=1)
    has_pos, has_neg = (sc > 0).any(axis=1), (sc < 0).any(axis=1)
    j1 = [1, 2, 0]

    # faces with corners on both sides: one segment, from the "+ to -" edge to the "- to +" edge
    cr = np.flatnonzero(has_pos & has_neg)
    Fx, sx = Fc[cr], sc[cr]
    plus = sx >= 0  # a corner in the plane counts as + here (it then is the crossing point itself)
    pm = plus & ~plus[:, j1]
    mp = ~plus & plus[:, j1]
    r = np.arange(len(cr))
    jpm, jmp = pm.argmax(axis=1), mp.argmax(axis=1)
    a_pm, b_pm = Fx[r, jpm], Fx[r, (jpm + 1) % 3]
    a_mp, b_mp = Fx[r, jmp], Fx[r, (jmp + 1) % 3]
    # the corner in the plane, if any, is the point; else the edge's crossing
    keyA = np.where(s[a_pm] == 0, a_pm, -1 - (np.minimum(a_pm, b_pm) * N + np.maximum(a_pm, b_pm)))
    keyB = np.where(s[b_mp] == 0, b_mp, -1 - (np.minimum(a_mp, b_mp) * N + np.maximum(a_mp, b_mp)))
    face_x = cand[cr]

    # edges lying in the plane
    on = np.flatnonzero(zeros >= 2)
    Fo, so = Fc[on], sc[on]
    ek, ef, ev = [], [], []
    if len(on):
        cop = (so == 0).all(axis=1)
        side = np.zeros(len(on))
        if cop.any():  # a face lying in the plane: its own normal's side
            Pa, Pb, Pc = (V[Fo[cop, k]] for k in range(3))
            side[cop] = np.sign(np.cross(Pb - Pa, Pc - Pa) @ normal)
        for j in range(3):
            a, b, c = Fo[:, j], Fo[:, j1[j]], Fo[:, (j + 2) % 3]
            m = (s[a] == 0) & (s[b] == 0) & (a != b)
            if not m.any():
                continue
            a, b, c = a[m], b[m], c[m]
            third = s[c].astype(np.float64)
            third[cop[m]] = side[m][cop[m]]
            ek.append(np.minimum(a, b) * N + np.maximum(a, b))
            ef.append(np.flatnonzero(m))
            ev.append(np.where(a < b, 1.0, -1.0) * third)  # + : the edge's way (low to high vertex)
    if ek:
        ek, ef, ev = np.concatenate(ek), np.concatenate(ef), np.concatenate(ev)
        gface = cand[on][ef]
        gfill = filled[gface] if filled is not None else np.zeros(len(gface), dtype=bool)
        gcop = (so[ef] == 0).all(axis=1)
        o = np.lexsort((gface, gfill, ek))  # by edge; a measured face first, then the lowest
        ek, gface, gfill, gcop, ev = ek[o], gface[o], gfill[o], gcop[o], ev[o]
        first = np.concatenate([[True], ek[1:] != ek[:-1]])
        start = np.flatnonzero(first)
        count = np.diff(np.append(start, len(ek)))
        n_cop = np.add.reduceat(gcop.astype(np.int64), start)
        vote = np.add.reduceat(ev, start)
        all_filled = np.logical_and.reduceat(gfill, start)
        keep = ~((count == 2) & (n_cop == 2))  # inside a flat area lying in the plane
        k = ek[start][keep]
        on_A, on_B = k // N, k % N
        on_pref = np.sign(vote[keep]).astype(np.int64)
        on_face = gface[start][keep]
        on_fill = all_filled[keep]
    else:
        on_A = on_B = on_pref = on_face = np.zeros(0, dtype=np.int64)
        on_fill = np.zeros(0, dtype=bool)

    A = np.concatenate([keyA, on_A])
    B = np.concatenate([keyB, on_B])
    pref = np.concatenate([np.ones(len(cr), dtype=np.int64), on_pref])
    face = np.concatenate([face_x, on_face])
    fill = np.concatenate([filled[face_x] if filled is not None else np.zeros(len(cr), dtype=bool), on_fill])
    return A, B, pref, face, fill


def _node_points(keys, V, d, sigma, N):
    """3D point and sigma of each node key (a vertex, or ``-1 - edge key`` for an edge's crossing)."""
    P = np.empty((len(keys), 3))
    S = np.empty(len(keys)) if sigma is not None else None
    vert = keys >= 0
    P[vert] = rows(V, keys[vert])
    if S is not None:
        S[vert] = sigma[keys[vert]]
    ek = -1 - keys[~vert]
    lo, hi = ek // N, ek % N
    t = d[lo] / (d[lo] - d[hi])
    Vlo = rows(V, lo)
    P[~vert] = Vlo + t[:, None] * (rows(V, hi) - Vlo)
    if S is not None:
        S[~vert] = sigma[lo] + t * (sigma[hi] - sigma[lo])
    return P, S


def _pair_junction(ends: list[int], node_of_end, pref, P2, x: int, link: np.ndarray) -> None:
    """Pair the segment ends meeting at node ``x`` (more than two): around the point by angle, an
    arriving segment with the next leaving one counter-clockwise; ends left over stay ends."""
    items = []
    for e in ends:
        seg, side = divmod(e, 2)
        other = node_of_end[e ^ 1]
        ang = math.atan2(P2[other, 1] - P2[x, 1], P2[other, 0] - P2[x, 0])
        p = int(pref[seg])
        kind = 0 if p == 0 else (1 if (side == 1) == (p > 0) else -1)  # 1 arriving, -1 leaving
        items.append((ang, e, kind))
    items.sort()
    L = len(items)
    used = [False] * L
    stack: list[int] = []
    on_stack = [False] * L
    for k in list(range(L)) * 2:
        if used[k]:
            continue
        if items[k][2] == 1 and not on_stack[k]:
            stack.append(k)
            on_stack[k] = True
        elif items[k][2] == -1 and stack:
            j = stack.pop()
            on_stack[j] = False
            used[j] = used[k] = True
            link[items[j][1]] = items[k][1]
            link[items[k][1]] = items[j][1]


def _simple(nodes: list[int], steps: list[tuple[int, bool]], closed: bool) -> list[tuple[list, list, bool]]:
    """Split a walk at the points it passes twice: ``[(nodes, steps, closed)]``. A closed walk's
    ``nodes`` ends with its first node; the closed pieces returned do not repeat it."""
    out = []
    st_nodes = [nodes[0]]
    st_steps: list[tuple[int, bool]] = []
    where = {nodes[0]: 0}
    for i, stp in enumerate(steps):
        x = nodes[i + 1]
        j = where.get(x)
        if j is None:
            where[x] = len(st_nodes)
            st_nodes.append(x)
            st_steps.append(stp)
            continue
        loop_nodes = st_nodes[j:]
        loop_steps = st_steps[j:] + [stp]
        for y in loop_nodes[1:]:
            del where[y]
        del st_nodes[j + 1 :]
        del st_steps[j:]
        if len(loop_nodes) >= 3:
            out.append((loop_nodes, loop_steps, True))
    if st_steps:
        out.append((st_nodes, st_steps, False))
    return out


def section(V, F, plane: SectionPlane, *, sigma=None, filled=None) -> Section:
    """Cut the mesh ``(V, F)`` with ``plane``; see the module docstring.

    ``sigma``: per-vertex 1σ (mm), carried to every point. ``filled``: per-face flag of faces that
    were filled in (not measured), carried to every segment. Returns a :class:`Section`; ValueError
    for malformed input.
    """
    V, F = as_mesh(V, F)
    N = len(V)
    if sigma is not None:
        sigma = np.asarray(sigma, dtype=np.float64).reshape(-1)
        if len(sigma) != N:
            raise ValueError(f"{len(sigma)} sigma values for {N} vertices")
    if filled is not None:
        filled = np.asarray(filled, dtype=bool).reshape(-1)
        if len(filled) != len(F):
            raise ValueError(f"{len(filled)} filled flags for {len(F)} faces")
    if len(F) == 0 or N == 0:
        return Section(plane, [])
    n = plane.normal
    c = float(plane.point @ n)
    d = V @ n - c
    eps = SNAP * max(float(np.abs(V).max()), abs(c), 1.0)
    s = (d > eps).astype(np.int8) - (d < -eps).astype(np.int8)
    d = np.where(s == 0, 0.0, d)
    A, B, pref, face, fill = _segments(V, F, s, filled, n)
    if len(A) == 0:
        return Section(plane, [])
    # the nodes, merged where they fall on the very same point
    keys, inv = np.unique(np.concatenate([A, B]), return_inverse=True)
    P3, S = _node_points(keys, V, d, sigma, N)
    _, first, inv2 = np.unique(P3, axis=0, return_index=True, return_inverse=True)
    inv2 = inv2.reshape(-1)
    node = first[inv2][inv]  # each end's node, the first of its group
    nA, nB = node[: len(A)], node[len(A) :]
    ok = nA != nB
    lo, hi = np.minimum(nA, nB), np.maximum(nA, nB)
    pk = lo * len(keys) + hi
    _, uniq = np.unique(np.where(ok, pk, -1), return_index=True)
    keep = np.sort(uniq[ok[uniq]])  # one segment per pair of points, the first given
    nA, nB, pref, face, fill = nA[keep], nB[keep], pref[keep], face[keep], fill[keep]
    P2 = plane.to_2d(P3)
    return Section(plane, _join(nA, nB, pref, face, fill, P2, P3, S))


def _join(nA, nB, pref, face, fill, P2, P3, S) -> list[SectionLine]:
    """Join the segments into lines (module docstring, "Joining" and "Direction and sign")."""
    nseg = len(nA)
    node_of_end = np.empty(2 * nseg, dtype=np.int64)
    node_of_end[0::2], node_of_end[1::2] = nA, nB
    K = len(P2)
    order = np.argsort(node_of_end, kind="stable")
    deg = np.bincount(node_of_end, minlength=K)
    ptr = np.concatenate([[0], np.cumsum(deg)])
    link = np.full(2 * nseg, -1, dtype=np.int64)
    two = np.flatnonzero(deg == 2)
    e1, e2 = order[ptr[two]], order[ptr[two] + 1]
    link[e1], link[e2] = e2, e1
    noe = node_of_end.tolist()
    for x in np.flatnonzero(deg > 2).tolist():
        _pair_junction(order[ptr[x] : ptr[x + 1]].tolist(), noe, pref, P2, x, link)
    link_l = link.tolist()
    seen = bytearray(nseg)
    pieces = []

    def walk(e0: int) -> None:
        seg, side = divmod(e0, 2)
        nodes, steps = [noe[e0]], []
        nxt = -1
        while True:
            seen[seg] = 1
            steps.append((seg, side == 0))
            out = 2 * seg + 1 - side
            nodes.append(noe[out])
            nxt = link_l[out]
            if nxt < 0 or seen[nxt >> 1]:
                break
            seg, side = divmod(nxt, 2)
        pieces.extend(_simple(nodes, steps, nxt == e0 and nodes[-1] == nodes[0]))

    for e in range(2 * nseg):
        if link_l[e] < 0 and not seen[e >> 1]:
            walk(e)
    for sgi in range(nseg):
        if not seen[sgi]:
            walk(2 * sgi)

    lines = []
    for nodes, steps, closed in pieces:
        seg = np.fromiter((x for x, _ in steps), dtype=np.int64, count=len(steps))
        fwd = np.fromiter((f for _, f in steps), dtype=bool, count=len(steps))
        nd = np.asarray(nodes + [nodes[0]] if closed else nodes, dtype=np.int64)
        if int((pref[seg] * np.where(fwd, 1, -1)).sum()) < 0:  # most segments run the other way
            nd, seg = nd[::-1], seg[::-1]
        if closed:
            nd = nd[:-1]
        lines.append(SectionLine(points=P2[nd], points3d=P3[nd], closed=bool(closed),
                                 sigma=None if S is None else S[nd], filled=fill[seg].copy(), faces=face[seg].copy()))
    lines.sort(key=lambda ln: -ln.length)
    return lines
