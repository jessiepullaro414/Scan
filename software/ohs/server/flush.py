"""The flush along a seam over the API (FEATURE_PLAN I4; APP_API.md "Flush along a seam"): the step between two
panels read on the scan's current mesh from two clicks, one on each panel near the seam, its stations as a CSV
download, and the seams a user keeps with the scan.

The measurement is :func:`ohs.inspect.gapflush.measure_gap_flush` on the arrays every mesh route shares
(:meth:`ProcessManager.mesh_arrays <ohs.server.processing.ProcessManager.mesh_arrays>`, with each vertex's sigma
and each face's filled flag). **The answer** is the engine's record, :meth:`GapFlush.to_dict
<ohs.inspect.gapflush.GapFlush.to_dict>`, as it is: the stations along the seam in order, each with its flush, its
± (1σ), what each side saw (``side_a`` / ``side_b``: measured, filled, bridged or missing), whether its flush is
trusted and why not in words (``notes``); lowest, mean and highest over the trusted stations with where they are
along the seam; and the gap. **The gap is worked out and not offered** (``gap_offered`` false): the scan closes a
narrow gap over a dark background, so the panels' edges are not measured (``VALIDATION.md``, "Gap and flush
between two panels"); ``gap_note`` says so in the engine's words, and the viewer and the CSV show the flush alone.
Beside the record: the mesh version measured, ``warnings`` (the mesh has changed since a kept seam was kept, and
how many stations are not trusted), the download name and ``kept`` (a kept seam's record, else null).

The first click is panel A and the second panel B: the flush is + where B stands above A, square to A (the
engine's sign). A click the engine refuses (off the mesh, the two clicks too close together, one above the other,
no seam between them) is 422 in the engine's own words.

**The stations as CSV** (:func:`stations_csv`): one row per station in order along the seam, with the columns of
:data:`CSV_COLUMNS` (the place along the seam, the seam's middle there, the flush and its ±, whether it is trusted,
what each side saw, the notes); numbers in full, ``yes`` / ``no``, text that a spreadsheet could take for a formula
made safe; written with a byte-order mark so that a spreadsheet reads the text right. No gap column: the gap is not
offered.

**Kept seams** go into ``features.json`` (``seams``, :mod:`ohs.measure.features`, "Kept seams") through
:meth:`FeatureManager.change_seams <ohs.server.features.FeatureManager.change_seams>`: a name, the two clicks as
numbers, the mesh version it was measured on, and when. The stations are not kept: opening a kept seam measures
the current mesh again from its clicks, and says so when the mesh has changed since (``kept.mesh_changed``, a
warning). Reading writes nothing; ``PATCH`` with ``mark_current`` marks it as measured on the current mesh.

**Nothing else on disk changes:** a measurement and the CSV are made in memory on each request; only keeping,
renaming, marking and deleting a seam write ``features.json``.
"""
from __future__ import annotations

import csv
import io
import math
from typing import Any, Mapping

import ohs.measure.features as kf
from ohs.inspect.gapflush import measure_gap_flush
from ohs.server.protocol import ApiError, plain_reason

__all__ = ["FlushManager", "CSV_COLUMNS", "stations_csv", "clicks_from_query"]

#: the stations CSV's columns, in order
CSV_COLUMNS = ("position_mm", "x_mm", "y_mm", "z_mm", "flush_mm", "flush_sigma_mm", "flush_trusted", "side_a", "side_b",
               "bridged", "notes")
_HINT_CLICKS = "Click once on each panel, a few millimetres from the seam: the first click's panel is panel A."


def _bad(detail: str, hint: str = _HINT_CLICKS) -> ApiError:
    return ApiError(422, detail, hint)


def _click(x: Any, what: str) -> list[float]:
    """Three finite numbers: a list (a body) or "x,y,z" (a query)."""
    if isinstance(x, str):
        x = [p for p in x.replace(";", ",").split(",") if p.strip()]
    if not isinstance(x, (list, tuple)) or len(x) != 3:
        raise _bad(f"The {what} the page sent is not a point on the mesh.")
    out = []
    for v in x:
        try:
            f = float(v) if not isinstance(v, bool) else math.nan
        except (TypeError, ValueError):
            f = math.nan
        if not math.isfinite(f):
            raise _bad(f"The {what} the page sent is not a point on the mesh.")
        out.append(f)
    return out


def clicks_from_query(q: Mapping[str, str]) -> tuple[list[float], list[float]]:
    """The two clicks of a download's query: ``a`` and ``b`` as "x,y,z"."""
    return _click(q.get("a"), "click on the first panel"), _click(q.get("b"), "click on the second panel")


def _cell(x: Any) -> str:
    from ohs.report.tables import FORMULA_STARTS

    if x is None:
        return ""
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, (int, float)):
        return repr(float(x)) if isinstance(x, float) else repr(int(x))
    s = str(x)
    return "'" + s if s.startswith(FORMULA_STARTS) else s


def stations_csv(record: Mapping[str, Any]) -> bytes:
    """The stations of a flush record (``GapFlush.to_dict()``) as CSV (module docstring): UTF-8 with a byte-order
    mark, lines ending in CRLF."""
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for s in record.get("stations") or []:
        p = s.get("point") or [None, None, None]
        w.writerow([_cell(v) for v in (s.get("position_mm"), p[0], p[1], p[2], s.get("flush_mm"), s.get("flush_sigma_mm"),
                                         bool(s.get("flush_trusted")), s.get("side_a"), s.get("side_b"),
                                         bool(s.get("bridged")), " | ".join(s.get("notes") or []))])
    return buf.getvalue().encode("utf-8-sig")


def untrusted_warning(record: Mapping[str, Any]) -> str | None:
    """How many stations' flush is not trusted, in words (None when all are)."""
    fl = record.get("flush") or {}
    n, k = fl.get("n_stations"), fl.get("n_trusted")
    if isinstance(n, int) and isinstance(k, int) and n > 0 and k < n:
        return (f"the flush is not trusted at {n - k} of {n} stations along the seam (a surface missing, or a reading "
                "far from its neighbours): they are left out of the lowest, mean and highest")
    return None


class FlushManager:
    """The flush routes of every scan, on the kept set of the :class:`~ohs.server.features.FeatureManager`
    ``feats``."""

    def __init__(self, feats: Any) -> None:
        self.feats = feats
        self.st = feats.st

    # ------------------------------------------------------------------ helpers
    def _mesh(self, pid: str, sid: str):
        """The current mesh as ``(version, V, F, sigma, filled)``; 404 for an unknown scan, 409 before there is a
        mesh. A sigma or filled array that does not fit the mesh is left out (the engine would refuse it)."""
        self.st.projects.scan_entry(pid, sid)
        v, V, F, S, Fl = self.st.processing.mesh_arrays(pid, sid)
        S = S if S is not None and len(S) == len(V) else None
        Fl = Fl if Fl is not None and len(Fl) == len(F) else None
        return int(v), V, F, S, Fl

    def measure(self, pid: str, sid: str, a: list[float], b: list[float]) -> tuple[dict, int]:
        """The engine's record for two clicks on the current mesh, and the mesh version measured; 422 in the
        engine's words for clicks it refuses."""
        v, V, F, S, Fl = self._mesh(pid, sid)
        try:
            gf = measure_gap_flush(V, F, a, b, sigma=S, filled=Fl)
        except ValueError as e:
            raise _bad(plain_reason(e, "The gap and flush cannot be measured from these two clicks.")) from None
        return gf.to_dict(), v

    def _filename(self, pid: str, sid: str, name: str | None) -> str:
        from ohs.server.cad import _file_stem

        scan = self.st.projects.scan_entry(pid, sid)
        return _file_stem(scan.get("name") or scan.get("id")) + "-flush" + (f"-{_file_stem(name)}" if name else "") + ".csv"

    @staticmethod
    def _view(rec: dict, version: int | None) -> dict:
        """A kept seam as the API shows it: with ``mesh_changed`` when the current mesh is not the one it was
        measured on."""
        return {**rec, "mesh_changed": version is None or rec["mesh_version"] != version}

    @staticmethod
    def _find(seams: list[dict], xid: str) -> int:
        for i, s in enumerate(seams):
            if s["id"] == xid:
                return i
        raise ApiError(404, "This seam is no longer kept with the scan.", "Reload the page to see the kept seams.")

    def _kept(self, pid: str, sid: str, xid: str) -> dict:
        doc, _, _ = self.feats.snapshot(pid, sid)
        seams = doc.get("seams") or []
        return seams[self._find(seams, xid)]

    @staticmethod
    def _answer(record: dict, version: int, warnings: list[str], filename: str, kept: dict | None = None) -> dict:
        out = dict(record)
        out.update(mesh_version=version, warnings=warnings, filename=filename, kept=kept)
        return out

    def measure_kept(self, pid: str, sid: str, xid: str) -> tuple[dict, int, dict, list[str]]:
        """A kept seam measured again on the current mesh: ``(the engine's record, the mesh version, the kept
        seam as the API shows it, its warnings)``. For the route and for the report (:mod:`ohs.server.report`)."""
        rec = self._kept(pid, sid, xid)
        record, v = self.measure(pid, sid, rec["a"], rec["b"])
        view = self._view(rec, v)
        warnings = []
        if view["mesh_changed"]:
            warnings.append("the mesh has changed since this seam was kept: this is the flush of the current mesh")
        un = untrusted_warning(record)
        if un:
            warnings.append(un)
        return record, v, view, warnings

    # ------------------------------------------------------------------ routes
    def flush(self, pid: str, sid: str, a: Any, b: Any) -> dict:
        """``POST .../flush``: the flush along the seam between the panels of two clicks (module docstring)."""
        a = _click(a, "click on the first panel")
        b = _click(b, "click on the second panel")
        record, v = self.measure(pid, sid, a, b)
        un = untrusted_warning(record)
        return self._answer(record, v, [un] if un else [], self._filename(pid, sid, None))

    def file(self, pid: str, sid: str, name: str, query: Mapping[str, str]) -> tuple[bytes, str, int]:
        """``GET .../flush/stations.csv?a=x,y,z&b=x,y,z``: the stations as CSV, its download name and the mesh
        version measured."""
        self._check_file(name)
        a, b = clicks_from_query(query)
        record, v = self.measure(pid, sid, a, b)
        return stations_csv(record), self._filename(pid, sid, None), v

    @staticmethod
    def _check_file(name: str) -> None:
        if name != "stations.csv":
            raise ApiError(404, "A seam's measurements download as a table (CSV) only.", "Download the table.")

    def list(self, pid: str, sid: str) -> dict:
        """``GET .../seams``: the kept seams, oldest first, each with ``mesh_changed``."""
        doc, _, version = self.feats.snapshot(pid, sid)
        return {"mesh_version": version, "seams": [self._view(s, version) for s in doc.get("seams") or []]}

    def keep(self, pid: str, sid: str, a: Any, b: Any, name: str | None = None) -> dict:
        """``POST .../seams``: keep a seam: its two clicks, a name, the current mesh version. Returns it as
        ``GET .../seams`` shows it. Measures nothing (opening it does)."""
        a = _click(a, "click on the first panel")
        b = _click(b, "click on the second panel")
        try:
            name = None if name is None else kf.clean_name(name)
        except ValueError as e:
            raise _bad(plain_reason(e, "This name cannot be used."),
                       f"Give a name of 1 to {kf.NAME_MAX} characters.") from None
        v = self._mesh(pid, sid)[0]

        def add(seams: list[dict], doc: dict) -> dict:
            rec = {"id": kf.new_id("sm"), "name": name or kf.next_name(seams, "Seam"), "a": a, "b": b,
                   "mesh_version": v, "created": kf.now()}
            seams.append(rec)
            return rec

        return self._view(self.feats.change_seams(pid, sid, add), v)

    def get(self, pid: str, sid: str, xid: str) -> dict:
        """``GET .../seams/{id}``: a kept seam measured again on the current mesh, with ``kept``."""
        record, v, view, warnings = self.measure_kept(pid, sid, xid)
        return self._answer(record, v, warnings, self._filename(pid, sid, view["name"]), view)

    def kept_file(self, pid: str, sid: str, xid: str, name: str) -> tuple[bytes, str, int]:
        """``GET .../seams/{id}/stations.csv``: a kept seam's stations, measured on the current mesh."""
        self._check_file(name)
        record, v, view, _ = self.measure_kept(pid, sid, xid)
        return stations_csv(record), self._filename(pid, sid, view["name"]), v

    def update(self, pid: str, sid: str, xid: str, name: str | None = None, mark_current: bool = False) -> dict:
        """``PATCH .../seams/{id}``: a new name, and/or (``mark_current``) mark it as measured on the current mesh
        (its clicks stay as they are). 422 for neither."""
        if name is None and not mark_current:
            raise _bad("Nothing to change on this seam.", "Give it a new name, or mark it as measured on this mesh.")
        try:
            name = None if name is None else kf.clean_name(name)
        except ValueError as e:
            raise _bad(plain_reason(e, "This name cannot be used."),
                       f"Give a name of 1 to {kf.NAME_MAX} characters.") from None
        v = self._mesh(pid, sid)[0] if mark_current else None

        def change(seams: list[dict], doc: dict) -> dict:
            i = self._find(seams, xid)
            if name is not None:
                seams[i]["name"] = name
            if v is not None:
                seams[i]["mesh_version"] = v
            return seams[i]

        rec = self.feats.change_seams(pid, sid, change)
        return self._view(rec, v if v is not None else self.feats.snapshot(pid, sid)[2])

    def delete(self, pid: str, sid: str, xid: str) -> None:
        """``DELETE .../seams/{id}``: forget a kept seam (404 when there is none)."""

        def drop(seams: list[dict], doc: dict) -> None:
            del seams[self._find(seams, xid)]

        self.feats.change_seams(pid, sid, drop)
