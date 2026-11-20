"""Sections over the API (FEATURE_PLAN I1; APP_API.md "Sections"): the scan's current mesh cut by a
plane, as the 2D profile an engineer measures and traces, its DXF and SVG at 1:1, and the sections
a user keeps with the scan.

The cut is :func:`ohs.mesh.section.section` on the arrays every mesh route shares
(:meth:`ProcessManager.mesh_arrays <ohs.server.processing.ProcessManager.mesh_arrays>`, with each
vertex's sigma and each face's filled flag), and the files are :func:`ohs.cad.dxf.dxf_text` and
:func:`ohs.cad.svg.svg_text` of that cut. This module adds how the plane is named over the API, the
answer's shape, and the kept sections.

**Naming the plane** (a *definition*, the same in a body and, for the downloads, in a query):

* ``{"kind": "fit", "fit": <kept plane's id>, "offset_mm": 0}``: a kept plane moved along its
  normal (:func:`~ohs.mesh.section.plane_from_fit`, with the fit's name in the description). A fit
  made on an earlier mesh is used too: its plane is a plane. The answer says so (``stale_fit``, a
  warning). 404 for an id that names no kept fit, 422 for a fit that is not a plane;
* ``{"kind": "frame", "axis": "X" | "Y" | "Z" (or "-X" ...), "value_mm": c}``: the plane axis = c
  of the part's frame (:func:`~ohs.mesh.section.plane_from_frame`). 422 when the scan has no frame,
  409 for a frame this version cannot read; an out-of-date frame is used as it is, with a warning;
* ``{"kind": "point_normal", "point": [x, y, z], "normal": [a, b, c]}`` and
  ``{"kind": "points", "points": [p1, p2, p3]}`` (the normal by the right-hand rule), in the scan's
  frame. A zero normal and three points on a line are 422 with the engine's words.

With a part's frame this version can read, every plane is drawn in the part's axes (the engine's
rule, :mod:`ohs.mesh.section`); without one, in the scan's.

**The answer** is the engine's :meth:`Section.to_dict <ohs.mesh.section.Section.to_dict>` (the
plane with its 2D axes and description; the lines in 2D and 3D with each point's sigma, each
segment's filled flag and face, lengths, signed areas, bounding boxes; the totals) with, on each
line, its ``stretches`` (runs of segments over measured or filled faces, ``[first, end, filled]``),
and beside it ``n_lines``, ``n_closed``, the mesh version cut, the definition as understood,
``stale_fit``, ``warnings``, the download names and ``kept`` (a kept section's record, else null).

**Compact** (``compact``, what the viewer asks for, also while an offset is dragged): a real scan's
cut is mostly a few lines of a few hundred points, but a cut along a noisy flat face is thousands of
small loops. On the ball-on-a-table scan (804,372 triangles) a cut upright through the ball has 4
lines of 1,118 points (134 kB in full), but one 0.3 mm off the table's fitted plane has 657 lines of
13,394 points (1.8 MB), one 0.2 mm off 1,356 lines of 32,243 points (4.3 MB), and one exactly in
it 3,218 lines of 211,086 points (27 MB): about 135 bytes a point, too much to send on every move
of a slider. The compact answer leaves out what the drawing does not need: each line's points in 2D
only, as one flat list rounded to :data:`COMPACT_DECIMALS` decimals of a mm (a micrometre, far below
a point's sigma; the 3D points are ``plane.point + x u + y v``), its own numbers rounded alike, no
per-segment faces or filled flags (the stretches say the same), and the sigma as each line's mean
and largest over its measured points: 16 to 26 bytes a point (18 kB and 353 kB for the first two
cuts above). It also carries at most ``max_points`` points (:data:`MAX_POINTS` by default): the lines,
longest first, are sent whole while they fit (the first always), and ``truncated`` says how many
lines and points the cut has and how many were sent (401 kB and 260 kB for the last two); the
totals are always the whole cut's. The downloads always hold the whole cut at full precision.

**Kept sections** go into ``features.json`` (``sections``, :mod:`ohs.measure.features`, "Kept
sections") through :meth:`FeatureManager.change_sections
<ohs.server.features.FeatureManager.change_sections>`: the plane as absolute numbers (with its
drawing axes, so a kept section always draws the same way, whatever happens to the fit or the frame
it was named from), the definition with the fit's name and mesh version, the mesh version it was
cut on, and when. The lines are not kept: opening a kept section cuts the current mesh again with
its plane, and says so when the mesh has changed since (``kept.mesh_changed``, a warning). Reading
writes nothing; ``PATCH`` with ``recut`` marks it as cut on the current mesh.

**Nothing else on disk changes:** a cut and the files are made in memory on each request; only
keeping, renaming, marking and deleting a section write ``features.json``.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

import ohs.measure.features as kf
from ohs.server.protocol import ApiError, plain_reason

__all__ = ["SectionManager", "KINDS", "FILES", "MAX_POINTS", "COMPACT_DECIMALS", "definition", "definition_from_query"]

KINDS = kf.SECTION_KINDS
#: the files, by their name in the route: (the writer, the download name's ending, the media type)
FILES = {"section.dxf": ("dxf", ".dxf", "application/dxf"), "section.svg": ("svg", ".svg", "image/svg+xml")}
COMPACT_DECIMALS = 3  # a compact answer's coordinates: to the micrometre
MAX_POINTS = 20_000  # a compact answer's points unless asked otherwise (about 340 kB)
_AXES = ("X", "Y", "Z")
_HINT_BODY = ("Choose the plane to cut with: a kept plane, an axis of the part, a point and a direction, or three "
              "points.")


#: the body's fields in words
_WORDS = {"offset_mm": "offset", "value_mm": "place along the axis", "point": "point", "normal": "direction",
          "point 1": "first point", "point 2": "second point", "point 3": "third point"}


def _bad(detail: str, hint: str = _HINT_BODY) -> ApiError:
    return ApiError(422, detail, hint)


def _number(x: Any, what: str) -> float:
    """A finite number from a body (a number) or a query (text)."""
    if isinstance(x, bool):
        raise _bad(f"The {_WORDS.get(what, what)} of the section is not a number.")
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise _bad(f"The {_WORDS.get(what, what)} of the section is not a number.") from None
    if not math.isfinite(v):
        raise _bad(f"The {_WORDS.get(what, what)} of the section is not a number.")
    return v


def _vec(x: Any, what: str, n: int = 3) -> list[float]:
    """``n`` finite numbers: a list, or text "x,y,z" (a query)."""
    if isinstance(x, str):
        x = [p for p in x.replace(";", ",").split(",") if p.strip()]
    if not isinstance(x, (list, tuple)) or len(x) != n:
        raise _bad(f"The {_WORDS.get(what, what)} of the section must be {n} numbers.")
    return [_number(v, what) for v in x]


def definition(body: Mapping[str, Any]) -> dict:
    """The plane's definition from a body, checked and in its plain form (module docstring); 422
    with a plain message otherwise. Says nothing yet about whether the fit or the frame exists."""
    kind = body.get("kind")
    if kind not in KINDS:
        raise _bad("The app does not know this way of choosing the plane.")
    if kind == "fit":
        fid = body.get("fit")
        if not isinstance(fid, str) or not fid:
            raise _bad("A section on a kept plane needs the plane.", "Pick a kept plane.")
        off = body.get("offset_mm")
        return {"kind": "fit", "fit": fid, "offset_mm": 0.0 if off is None else _number(off, "offset_mm")}
    if kind == "frame":
        axis = body.get("axis")
        a = axis.strip().upper() if isinstance(axis, str) else ""
        a = a[1:] if a.startswith("+") else a
        if a.lstrip("-") not in _AXES or len(a) > 2:
            raise _bad("The axis must be X, Y or Z (or -X, -Y or -Z to see the plane from the other side).")
        if body.get("value_mm") is None:
            raise _bad("A section along an axis of the part needs where along the axis the plane lies.",
                       "Give it in mm, for example 12 for Z = 12.")
        return {"kind": "frame", "axis": a, "value_mm": _number(body.get("value_mm"), "value_mm")}
    if kind == "point_normal":
        for key in ("point", "normal"):
            if body.get(key) is None:
                raise _bad(f"A section through a point needs a point and a direction; the {_WORDS[key]} is missing.")
        return {"kind": "point_normal", "point": _vec(body["point"], "point"), "normal": _vec(body["normal"], "normal")}
    pts = body.get("points")
    if isinstance(pts, str):  # a query: nine numbers
        flat = _vec(pts, "points", 9)
        pts = [flat[0:3], flat[3:6], flat[6:9]]
    if not isinstance(pts, (list, tuple)) or len(pts) != 3:
        raise _bad("A section through three points needs three points.")
    return {"kind": "points", "points": [_vec(p, f"point {i + 1}") for i, p in enumerate(pts)]}


def definition_from_query(q: Mapping[str, str]) -> dict:
    """:func:`definition` from a download's query: ``kind`` and ``fit``, ``offset_mm``, ``axis``,
    ``value_mm``, ``point`` and ``normal`` ("x,y,z"), ``points`` (nine numbers, comma-separated)."""
    keys = ("kind", "fit", "offset_mm", "axis", "value_mm", "point", "normal", "points")
    return definition({k: q[k] for k in keys if k in q})


def _runs(filled) -> list[list]:
    """A line's stretches as segment runs: ``[[first, end (exclusive), filled], ...]`` in the line's
    own order (:meth:`SectionLine.stretches <ohs.mesh.section.SectionLine.stretches>` says the same
    as points; for a closed line the last segment runs from its last point back to its first)."""
    f = np.asarray(filled, dtype=bool)
    if len(f) == 0:
        return []
    cut = np.flatnonzero(f[1:] != f[:-1]) + 1
    starts = np.concatenate([[0], cut])
    ends = np.concatenate([cut, [len(f)]])
    return [[int(a), int(b), bool(f[a])] for a, b in zip(starts, ends)]


def _compact_line(ln) -> dict:
    from ohs.mesh.confidence import SIGMA_NONE_MM

    sigma = None
    if ln.sigma is not None:
        s = np.asarray(ln.sigma, dtype=np.float64)
        s = s[np.isfinite(s) & (s < SIGMA_NONE_MM)]
        sigma = {"mean": round(float(s.mean()), 4) if len(s) else None, "max": round(float(s.max()), 4) if len(s) else None}
    area = ln.area
    return {"closed": bool(ln.closed), "length_mm": round(ln.length, COMPACT_DECIMALS + 1),
            "area_mm2": None if area is None else round(area, COMPACT_DECIMALS + 1),
            "bbox": np.round(ln.bbox, COMPACT_DECIMALS).tolist(),
            "n_points": int(len(ln.points)), "points": np.round(ln.points, COMPACT_DECIMALS).ravel().tolist(),
            "sigma_mm": sigma, "stretches": _runs(ln.filled)}


def _stored_plane(rec: dict):
    """A kept section's plane, as its numbers say (its drawing axes too)."""
    from ohs.mesh.section import SectionPlane

    p = rec["plane"]
    return SectionPlane(point=np.asarray(p["point"], dtype=np.float64), normal=np.asarray(p["normal"], dtype=np.float64),
                        u=np.asarray(p["u"], dtype=np.float64), v=np.asarray(p["v"], dtype=np.float64),
                        description=str(p.get("description", "")), in_part_frame=bool(p.get("in_part_frame", False)))


class SectionManager:
    """The section routes of every scan, on the kept set of the
    :class:`~ohs.server.features.FeatureManager` ``feats``."""

    def __init__(self, feats: Any) -> None:
        self.feats = feats
        self.st = feats.st

    # ------------------------------------------------------------------ helpers
    def _mesh(self, pid: str, sid: str):
        """The current mesh as ``(version, V, F, sigma, filled)``; 409 before there is a mesh. A sigma
        or filled array that does not fit the mesh is left out (the engine would refuse it)."""
        v, V, F, S, Fl = self.st.processing.mesh_arrays(pid, sid)
        S = S if S is not None and len(S) == len(V) else None
        Fl = Fl if Fl is not None and len(Fl) == len(F) else None
        return int(v), V, F, S, Fl

    @staticmethod
    def _frame(doc: dict) -> tuple[Any, dict | None]:
        """The part's frame when this version can read it (else None), and its state."""
        from ohs.measure.frame import PartFrame

        state = kf.frame_state(doc.get("frame"), doc["features"])
        if state is None or not state["readable"]:
            return None, state
        return PartFrame.from_dict(doc["frame"]), state

    def _plane(self, d: dict, doc: dict, version: int | None):
        """The engine's plane for a definition: ``(plane, the definition as kept, stale_fit, warnings)``."""
        from ohs.mesh import section as sec

        pf, state = self._frame(doc)
        warnings: list[str] = []
        full = dict(d)
        stale = False
        kind = d["kind"]
        try:
            if kind == "fit":
                f = next((x for x in doc["features"] if x["id"] == d["fit"]), None)
                if f is None:
                    raise ApiError(404, "This fitted shape is no longer in the scan.",
                                   "Reload the page to see the scan's current list.")
                if f["kind"] != "plane":
                    raise _bad(f"A section is cut by a plane, and \"{f['name']}\" is a {f['kind']}.", "Pick a kept plane.")
                plane = sec.plane_from_fit({"name": f["name"], "fit": f["shape"]}, d["offset_mm"], frame=pf)
                full.update(fit_name=f["name"], fit_mesh_version=int(f["mesh_version"]))
                stale = version is not None and int(f["mesh_version"]) != version
                if stale:
                    warnings.append(f"\"{f['name']}\" was fitted on an earlier mesh, before the last edit: its plane is "
                                    "used as it was kept; fit it again for this mesh's plane")
            elif kind == "frame":
                if state is None:
                    raise _bad("The part's axes are not set for this scan, so there is no axis to cut along.",
                               "Set the part's axes first, or choose the plane another way.")
                if pf is None:
                    raise ApiError(409, state["out_of_date_reasons"][0], "Set the part's axes again, or remove them.")
                if state["out_of_date"]:
                    warnings.append("the part's axes are out of date (" + "; ".join(state["out_of_date_reasons"])
                                    + "): used as they are; set them again for the fitted shapes as they are now")
                plane = sec.plane_from_frame(pf, d["axis"], d["value_mm"])
            elif kind == "point_normal":
                plane = sec.plane_from_point_normal(d["point"], d["normal"], frame=pf)
            else:
                plane = sec.plane_from_points(*d["points"], frame=pf)
        except ValueError as e:
            raise _bad(plain_reason(e, "No section can be cut with this plane."), "Choose another plane.") from None
        if state is not None and not state["readable"] and kind != "frame":
            warnings.append(f"{state['out_of_date_reasons'][0]}: the drawing is in the scan's axes")
        return plane, full, stale, warnings

    def _filenames(self, pid: str, sid: str, label: str) -> dict:
        from ohs.server.cad import _file_stem

        scan = self.st.projects.scan_entry(pid, sid)
        stem = _file_stem(scan.get("name") or scan.get("id")) + "-" + _file_stem(label)
        return {kind: stem + end for kind, end, _ in FILES.values()}

    @staticmethod
    def _label(d: dict) -> str:
        """A cut's part of its download name: the fit and its offset, the frame's axis and value."""
        if d["kind"] == "fit":
            off = d["offset_mm"]
            return f"section-{d.get('fit_name') or d['fit']}" + (f"-offset-{'minus' if off < 0 else ''}{abs(off):g}mm" if off else "")
        if d["kind"] == "frame":
            a = d["axis"]
            return f"section-{a.lstrip('-')}{d['value_mm']:g}mm" + ("-from-minus" if a.startswith("-") else "")
        return "section"

    @staticmethod
    def _view(rec: dict, version: int | None) -> dict:
        """A kept section as the API shows it: with ``mesh_changed`` when the current mesh is not the
        one it was cut on."""
        return {**rec, "mesh_changed": version is None or rec["mesh_version"] != version}

    @staticmethod
    def _find(sections: list[dict], xid: str) -> int:
        for i, s in enumerate(sections):
            if s["id"] == xid:
                return i
        raise ApiError(404, "This section is no longer kept with the scan.", "Reload the page to see the kept sections.")

    def _kept(self, pid: str, sid: str, xid: str) -> dict:
        doc, _, _ = self.feats.snapshot(pid, sid)
        sections = doc.get("sections") or []
        return sections[self._find(sections, xid)]

    @staticmethod
    def _answer(s, version: int, d: dict, stale: bool, warnings: list[str], compact: bool, max_points: int | None,
                filenames: dict, kept: dict | None = None) -> dict:
        lines = s.lines
        if compact and max_points is None:
            max_points = MAX_POINTS
        if max_points is not None and max_points < 1:
            raise _bad("The page asked for a section with no points.", "Reload the page and cut it again.")
        shown, total = [], 0
        for ln in lines:
            if max_points is not None and shown and total + len(ln.points) > max_points:
                break
            shown.append(ln)
            total += len(ln.points)
        from ohs.mesh.section import FORMAT_VERSION

        bb = s.bbox
        return {"format_version": FORMAT_VERSION, "units": "mm", "plane": s.plane.to_dict(),
                "lines": [_compact_line(ln) if compact else {**ln.to_dict(), "stretches": _runs(ln.filled)} for ln in shown],
                "length_mm": s.length, "n_points": s.n_points, "bbox": None if bb is None else [float(x) for x in bb],
                "n_lines": len(lines), "n_closed": sum(bool(ln.closed) for ln in lines), "mesh_version": version,
                "definition": d, "stale_fit": bool(stale), "warnings": warnings, "compact": bool(compact),
                "truncated": None if len(shown) == len(lines) else {"lines_shown": len(shown), "points_shown": total,
                                                                    "lines": len(lines), "points": s.n_points},
                "filenames": filenames, "kept": kept}

    def _cut_definition(self, pid: str, sid: str, d: dict):
        """Cut the current mesh with a definition's plane: ``(section, version, definition, stale, warnings)``."""
        from ohs.mesh import section as sec

        doc, _, _ = self.feats.snapshot(pid, sid)  # 404 for an unknown project or scan
        v, V, F, S, Fl = self._mesh(pid, sid)
        plane, full, stale, warnings = self._plane(d, doc, v)
        return sec.section(V, F, plane, sigma=S, filled=Fl), v, full, stale, warnings

    def _cut_kept(self, pid: str, sid: str, xid: str):
        """Cut the current mesh again with a kept section's plane: ``(section, version, view, warnings)``."""
        from ohs.mesh import section as sec

        rec = self._kept(pid, sid, xid)
        v, V, F, S, Fl = self._mesh(pid, sid)
        view = self._view(rec, v)
        warnings = []
        if view["mesh_changed"]:
            warnings.append("the mesh has changed since this section was kept: this is the cut of the current mesh")
        return sec.section(V, F, _stored_plane(rec), sigma=S, filled=Fl), v, view, warnings

    @staticmethod
    def _text(s, name: str) -> bytes:
        from ohs.cad.dxf import dxf_text
        from ohs.cad.svg import svg_text

        if FILES[name][0] == "dxf":
            return dxf_text(s).encode("ascii")
        return svg_text(s).encode("utf-8")

    @staticmethod
    def _check_file(name: str) -> None:
        if name not in FILES:
            raise ApiError(404, "A section has no file of this name.", "Download one of the files the page lists.")

    # ------------------------------------------------------------------ routes
    def cut(self, pid: str, sid: str, body: Mapping[str, Any], compact: bool = False, max_points: int | None = None) -> dict:
        """``POST .../section``: the current mesh cut by the plane the body names (module docstring)."""
        d = definition(body)
        s, v, full, stale, warnings = self._cut_definition(pid, sid, d)
        return self._answer(s, v, full, stale, warnings, compact, max_points, self._filenames(pid, sid, self._label(full)))

    def file(self, pid: str, sid: str, name: str, query: Mapping[str, str]) -> tuple[bytes, str, str, int]:
        """``GET .../section/{name}`` (``section.dxf`` or ``section.svg``) with the plane in the query:
        the file's bytes, its download name, its media type and the mesh version cut."""
        self._check_file(name)
        d = definition_from_query(query)
        s, v, full, _, _ = self._cut_definition(pid, sid, d)
        kind, _, media = FILES[name]
        return self._text(s, name), self._filenames(pid, sid, self._label(full))[kind], media, v

    def list(self, pid: str, sid: str) -> dict:
        """``GET .../sections``: the kept sections, oldest first, each with ``mesh_changed``."""
        doc, _, version = self.feats.snapshot(pid, sid)
        return {"mesh_version": version, "sections": [self._view(s, version) for s in doc.get("sections") or []]}

    def keep(self, pid: str, sid: str, body: Mapping[str, Any], name: str | None = None) -> dict:
        """``POST .../sections``: keep a section: its plane as numbers, how it was named, the current
        mesh version. Returns it as ``GET .../sections`` shows it. Cuts nothing."""
        d = definition(body)
        try:
            name = None if name is None else kf.clean_name(name)
        except ValueError as e:
            raise _bad(plain_reason(e, "This name cannot be used."),
                       f"Give a name of 1 to {kf.NAME_MAX} characters.") from None
        v = self._mesh(pid, sid)[0]

        def add(sections: list[dict], doc: dict) -> dict:
            plane, full, _, _ = self._plane(d, doc, v)
            rec = {"id": kf.new_id("s"), "name": name or kf.next_name(sections, "Section"),
                   "plane": plane.to_dict(), "definition": full, "mesh_version": v, "created": kf.now()}
            sections.append(rec)
            return rec

        return self._view(self.feats.change_sections(pid, sid, add), v)

    def get(self, pid: str, sid: str, xid: str, compact: bool = False, max_points: int | None = None) -> dict:
        """``GET .../sections/{id}``: a kept section cut again on the current mesh, with ``kept``."""
        s, v, view, warnings = self._cut_kept(pid, sid, xid)
        return self._answer(s, v, view["definition"], False, warnings, compact, max_points,
                            self._filenames(pid, sid, f"section-{view['name']}"), view)

    def kept_file(self, pid: str, sid: str, xid: str, name: str) -> tuple[bytes, str, str, int]:
        """``GET .../sections/{id}/{name}``: a kept section's file, cut on the current mesh."""
        self._check_file(name)
        s, v, view, _ = self._cut_kept(pid, sid, xid)
        kind, _, media = FILES[name]
        return self._text(s, name), self._filenames(pid, sid, f"section-{view['name']}")[kind], media, v

    def update(self, pid: str, sid: str, xid: str, name: str | None = None, recut: bool = False) -> dict:
        """``PATCH .../sections/{id}``: a new name, and/or (``recut``) mark it as cut on the current
        mesh (its plane stays as it is). 422 for neither."""
        if name is None and not recut:
            raise _bad("Nothing to change on this section.", "Give it a new name, or cut it again on this mesh.")
        try:
            name = None if name is None else kf.clean_name(name)
        except ValueError as e:
            raise _bad(plain_reason(e, "This name cannot be used."),
                       f"Give a name of 1 to {kf.NAME_MAX} characters.") from None
        v = self._mesh(pid, sid)[0] if recut else None

        def change(sections: list[dict], doc: dict) -> dict:
            i = self._find(sections, xid)
            if name is not None:
                sections[i]["name"] = name
            if v is not None:
                sections[i]["mesh_version"] = v
            return sections[i]

        rec = self.feats.change_sections(pid, sid, change)
        return self._view(rec, v if v is not None else self.feats.snapshot(pid, sid)[2])

    def delete(self, pid: str, sid: str, xid: str) -> None:
        """``DELETE .../sections/{id}``: forget a kept section (404 when there is none)."""

        def drop(sections: list[dict], doc: dict) -> None:
            del sections[self._find(sections, xid)]

        self.feats.change_sections(pid, sid, drop)
