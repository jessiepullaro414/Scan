"""The report of a scan over the API (FEATURE_PLAN I3; APP_API.md "The report"): the scan's data collected from
the managers that keep it, into the plain structure :mod:`ohs.report.data` documents, and answered as the
self-contained HTML page (:func:`ohs.report.build_html`), the kept features as CSV
(:func:`ohs.report.tables.features_csv`) and as JSON (:func:`ohs.report.tables.features_json`).

**Collected** (:meth:`ReportManager.collect`), each part from its own manager, and only what exists: a part the
scan does not have is left out, never shown empty.

* the project's and the scan's names (:mod:`ohs.server.projects`), the date (read once here, on each request,
  and passed as data), the app's version;
* how the scan was made: the scan's processing report (``result/report.json``, the stage the current mesh was
  made by), its known keys only;
* the calibration the scan was made with (UX_PLAN R6, :meth:`ReportManager._calibration`): its recording's own copy,
  and which one that was (the simulator's, a replay's, the scanner's); Setup's words for its steps and Setup's last
  calibration check only while it is Setup's scanner calibration still; a merged scan says it has none of its own
  (R8, with "Merged from" and the merge's notes in ``merge``); its health is the scan's own calibration status (``result/report.json``'s ``health_status``, FEATURE_PLAN H3/H4:
  what this scan saw, not today's setup state); a scan processed before H3 has none and the report says "not
  assessed"; beside it the camera path's check (``result/report.json``'s ``path_check`` and ``warnings``,
  ENGINE_PLAN X0a: did tracking slide?), left out for a scan processed before the check;
* the Check answer at the tolerance the request gives (:meth:`ProcessManager.check
  <ohs.server.processing.ProcessManager.check>`); left out when the request gives none, and before there is a mesh;
* the kept features **as the API shows them** (:meth:`FeatureManager.get <ohs.server.features.FeatureManager.get>`:
  each fit's ``stale``, each relation's engine record, each approval's "check again" and its reason, the frame's
  state), with the moves of the CAD export (:mod:`ohs.server.cad`'s summary) when a relation has one; the request
  chooses the part's frame for the fits' positions and directions;
* the kept sections, each cut again on the current mesh (as ``GET .../sections/{id}`` does), with the warning when
  the mesh has changed since it was kept (:func:`ohs.report.data.section_entry`);
* the kept comparison with a reference (:mod:`ohs.server.reference`): the reference, its units, the alignment, and
  once it is aligned the deviation of the current mesh and the answer at the comparison's kept tolerance and ``k``
  (the engine's ``tolerance_summary``, worked out here so nothing is written); when the mesh has changed since the
  alignment, that note goes with the alignment's warnings;
* one kept seam's flush (:mod:`ohs.server.flush`), measured on the current mesh: the one the request names, else
  the first kept seam the engine can measure (the report's input holds one seam);
* the pictures the request carries (:meth:`ReportManager.pictures`).

**Warnings.** Every warning a part carries goes into its part, and :func:`ohs.report.data.prepare` collects them in
the report's own list near the top.

**Pictures** come from the viewer: PNG bytes (JPEG, GIF and WebP are taken too), base64 in the JSON body, each with
a title and an optional caption. At most :data:`MAX_PICTURES` pictures, each at most :data:`MAX_PICTURE_BYTES` once
decoded and :data:`MAX_TOTAL_BYTES` in all: 6 views of the model of about 1600 pixels across fit easily (such a PNG
is 0.5 to 2 MB). More pictures, or text that is not base64, is 422; a picture over the size is 413, with the limit
in words. A picture that is not an image is left out by the report, with a note naming it.

**Nothing on disk changes:** the report is built on request, in memory, and written nowhere. The reading is the
managers' reading, which writes nothing (the comparison's answer for its tolerance is worked out here rather than
through ``POST .../comparison/tolerance``, which keeps the tolerance it is given).
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
import math
from typing import Any, Mapping

from ohs import __version__
from ohs.server.projects import path_check_short, user_warnings
from ohs.server.protocol import ApiError, quality_or_none

__all__ = ["ReportManager", "MAX_PICTURES", "MAX_PICTURE_BYTES", "MAX_TOTAL_BYTES", "PROCESS_KEYS", "FILES"]

MAX_PICTURES = 6
MAX_PICTURE_BYTES = 3 * 2**20  # one picture, decoded
MAX_TOTAL_BYTES = 12 * 2**20  # all of them
#: the processing report's keys the report shows (ohs.report.data, "process"); the rest (per-frame rows) stay out
PROCESS_KEYS = ("mode", "quality", "source", "scene", "frames", "failures", "imu", "markers", "poses", "mesh", "lasers",
                "wall_time_s", "preset")  # "preset": FEATURE_PLAN P2, the scan's preset and the values it resolved to
#: the scan's own calibration status (``report.json``'s ``health_status``, H3): its states and the keys the report shows
HEALTH_STATES = ("valid", "check", "recalibrate")
HEALTH_KEYS = ("state", "label", "part", "part_label", "reason", "not_judged", "note", "provisional", "source")
#: the files, by their name in the route: (the download name's ending, the media type)
FILES = {"report.html": ("-report.html", "text/html; charset=utf-8"),
         "features.csv": ("-features.csv", "text/csv; charset=utf-8"),
         "features.json": ("-features.json", "application/json")}


def _size(n: int) -> str:
    return f"{n / 2**20:.1f} MB" if n >= 2**20 // 10 else f"{n:,} bytes"


class ReportManager:
    """The report routes of every scan, reading through the other managers: the kept features ``feats``
    (:class:`~ohs.server.features.FeatureManager`), the CAD export ``cad``, the sections ``secs``, the references
    ``refs`` and the seams ``flush``."""

    def __init__(self, feats: Any, cad: Any, secs: Any, refs: Any, flush: Any) -> None:
        self.feats = feats
        self.st = feats.st
        self.cad = cad
        self.secs = secs
        self.refs = refs
        self.flush = flush
        self.max_pictures = MAX_PICTURES
        self.max_picture_bytes = MAX_PICTURE_BYTES
        self.max_total_bytes = MAX_TOTAL_BYTES

    # ------------------------------------------------------------------ the request's pictures
    def pictures(self, items: Any) -> list[dict]:
        """The pictures of a request as the report takes them (``{"title", "caption", "data": bytes}``), checked
        (module docstring, "Pictures")."""
        items = list(items or [])
        if len(items) > self.max_pictures:
            raise ApiError(422, f"{len(items)} pictures are too many: a report takes at most {self.max_pictures}.",
                           "Remove some of the views added to the report.")
        out, total = [], 0
        for i, p in enumerate(items, 1):
            title = str(p.get("title") or "").strip()
            if not title:
                raise ApiError(422, f"Picture {i} has no title.", "Give each picture a title.")
            raw = p.get("data")
            try:
                data = base64.b64decode(raw if isinstance(raw, str) else "", validate=True)
            except (binascii.Error, ValueError):
                raise ApiError(422, f"The picture \"{title}\" cannot be read.",
                               "Remove it and add the view to the report again.") from None
            if len(data) > self.max_picture_bytes:
                raise ApiError(413, f"The picture \"{title}\" is {_size(len(data))}, more than the "
                                    f"{_size(self.max_picture_bytes)} a picture may be.",
                               "Add a smaller view (the viewer makes them at most 1600 pixels across).")
            total += len(data)
            if total > self.max_total_bytes:
                raise ApiError(413, f"The pictures come to more than the {_size(self.max_total_bytes)} a report takes "
                                    "in all.", "Add fewer views.")
            caption = p.get("caption")
            out.append({"title": title, "caption": str(caption).strip() if caption else None, "data": data})
        return out

    # ------------------------------------------------------------------ the parts
    def _result_report(self, pid: str, sid: str) -> dict | None:
        """The scan's ``result/report.json``; None without one."""
        path = self.st.projects.scan_dir(pid, sid) / "result" / "report.json"
        try:
            rep = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return rep if isinstance(rep, dict) else None

    def _process(self, rep: dict | None) -> dict | None:
        """The scan's processing report, its known keys; None without one."""
        if rep is None:
            return None
        out = {k: rep[k] for k in PROCESS_KEYS if k in rep}
        if out.get("quality"):  # stored with the old name "full": reads as "standard"
            out["quality"] = quality_or_none(out["quality"]) or out["quality"]
        return out or None

    def _calibration(self, pid: str, sid: str, entry: dict) -> dict | None:
        """The calibration the scan was made with (UX_PLAN R6), not Setup's current one: the copy in its recording
        (``recording/calibration.json``, the file the scan was recorded and processed with), and which one that was
        (``made_with``: ``"simulator"``, the simulator's own demo calibration; ``"replay"``, the replayed recording's
        own; ``"scanner"``, the scanner's file as it was then; from the recording's ``meta.json``). Only when it is
        Setup's scanner calibration still (the same calibration, :func:`ohs.calib.health.calibration_key`) do Setup's
        words for its steps, its last calibration check and the laser lines this build has but the file lacks come
        beside it. A merged scan has no recording of its own: ``made_with: "merge"`` and the scans it was made from.
        None when the scan has no calibration to show."""
        from ohs.calib import health
        from ohs.types import ScannerCalibration

        if entry.get("merged_from"):
            return {"made_with": "merge", "merged_from": [str(m.get("name") or m.get("id")) for m in entry["merged_from"]
                                                          if isinstance(m, dict)]}
        rec = self.st.projects.recording_dir(pid, sid)
        meta: dict = {}
        try:
            meta = json.loads((rec / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        meta = meta if isinstance(meta, dict) else {}
        device, source = meta.get("device"), meta.get("source")
        kind = ("simulator" if device == "sim" or (device is None and source == "sim") else
                "replay" if device == "replay" else "scanner")
        try:
            calib = ScannerCalibration.load(rec / "calibration.json") if (rec / "calibration.json").is_file() else None
        except (OSError, ValueError, KeyError, TypeError):
            calib = None
        if calib is None:
            return {"made_with": kind, "missing": True}
        steps = calib.steps if isinstance(calib.steps, dict) else {}

        def part(key: str, have: bool, summary: str) -> dict | None:
            if not have:
                return None
            when = (steps.get(key) or {}).get("created") if isinstance(steps.get(key), dict) else None
            return {"when": when or calib.created, "summary": summary}

        lasers = sorted(calib.lasers or {})
        cal: dict[str, Any] = {
            "made_with": kind, "modified": calib.created, "baseline_mm": round(float(calib.stereo.baseline), 3),
            "lasers": lasers,
            "parts": {"stereo": part("stereo", True, "Calibrated"),
                      "lasers": part("lasers", bool(lasers), "Calibrated: the " + ", ".join(lasers) + " laser line"
                                     + ("s" if len(lasers) != 1 else "")),
                      "imu": part("imu", calib.imu is not None, "Calibrated")}}
        if kind == "scanner":
            try:
                same = health.calibration_key(calib) == health.calibration_key(self.st.setup.load_calibration("device"))
                s = self.st.setup.state("device") if same else None
            except (OSError, ValueError, KeyError, TypeError, ApiError):
                s = None
            if s is not None:  # still Setup's scanner calibration: its words for the steps, its check
                sc = s.get("calibration") or {}
                for k, info in (sc.get("parts") or {}).items():
                    if isinstance(info, dict) and cal["parts"].get(k) is not None:
                        cal["parts"][k] = {"when": info.get("when") or cal["parts"][k]["when"],
                                           "summary": info.get("summary") or cal["parts"][k]["summary"]}
                cal["lasers_missing"] = list(sc.get("lasers_missing") or [])
                cal["last_check"] = s.get("last_check")
        return cal

    @staticmethod
    def _merge(entry: dict, rep: dict | None) -> dict | None:
        """A merged scan (UX_PLAN R8): the scans it was merged from, the merge's notes (what it left out of each
        scan's table, what its cleaning took) and its warnings, from its ``report.json``; None for any other scan."""
        if not entry.get("merged_from"):
            return None
        names = [str(m.get("name") or m.get("id")) for m in entry["merged_from"] if isinstance(m, dict)]
        rep = rep if isinstance(rep, dict) else {}
        notes = []
        for s in rep.get("scans") or []:
            t = s.get("table") if isinstance(s, dict) else None
            if isinstance(t, dict) and t.get("summary"):
                text = str(t["summary"])
                notes.append(f"{s.get('name') or 'A scan'}: {text[:1].lower() + text[1:]}")
        cleaning = ((rep.get("mesh") or {}).get("cleaning") or {}).get("summary")
        if cleaning:
            notes.append(str(cleaning))
        return {"from": names, "notes": notes, "warnings": [str(w) for w in rep.get("warnings") or [] if w]}

    def _check(self, pid: str, sid: str, tolerance_mm: float) -> dict:
        r = self.st.processing.check(pid, sid, float(tolerance_mm))
        r["areas"] = [{k: v for k, v in a.items() if k != "faces"} for a in r.get("areas") or []]
        return r

    def _cad_moves(self, pid: str, sid: str, doc: dict) -> list[dict]:
        """The CAD export's moves (``Move.to_dict()``), when a kept relation has one; else none."""
        if not any(r.get("move") for r in doc.get("relations") or []):
            return []
        try:
            return list(self.cad.summary(pid, sid)["nominal"]["moves"])
        except ApiError:
            return []

    def _sections(self, pid: str, sid: str, mesh) -> list[dict]:
        """Each kept section cut again on the current mesh, as the report shows it."""
        import numpy as np

        from ohs.mesh.section import SectionPlane, section
        from ohs.report.data import section_entry

        version, V, F, S, Fl = mesh
        out = []
        for rec in self.secs.list(pid, sid)["sections"]:
            p = rec["plane"]
            plane = SectionPlane(point=np.asarray(p["point"], dtype=np.float64), normal=np.asarray(p["normal"], dtype=np.float64),
                                 u=np.asarray(p["u"], dtype=np.float64), v=np.asarray(p["v"], dtype=np.float64),
                                 description=str(p.get("description", "")), in_part_frame=bool(p.get("in_part_frame", False)))
            warnings = []
            if rec["mesh_version"] != version:
                warnings.append("the mesh has changed since this section was kept: this is the cut of the current mesh")
            cut = section(V, F, plane, sigma=S, filled=Fl)
            out.append(section_entry(cut, name=rec["name"], warnings=warnings))
        return out

    def _comparison(self, pid: str, sid: str, version: int | None) -> dict | None:
        """The kept comparison: the reference, the alignment and, once aligned, the deviation of the current mesh
        and the answer at the kept tolerance."""
        from ohs.inspect.reference import tolerance_summary

        view = self.refs.get(pid, sid)["comparison"]
        if view is None:
            return None
        scale = float(view.get("scale") or 1.0)
        rec = view.get("reference") or {}
        ref = {"name": rec.get("name") or "the CAD model (its file is no longer in the project)",
               "units": view.get("units"), "warnings": list(rec.get("warnings") or [])}
        if isinstance(rec.get("size_mm"), (int, float)):
            ref["size_mm"] = float(rec["size_mm"]) * scale
        if isinstance(rec.get("triangles"), int):
            ref["n_faces"] = rec["triangles"]
        out: dict = {"reference": ref}
        al = view.get("alignment")
        if not isinstance(al, dict):
            return out
        al = {**al, "warnings": list(al.get("warnings") or [])}
        out["alignment"] = al
        if not view.get("ready") or version is None:
            return out
        try:
            info, dev = self.refs.deviation(pid, sid)
        except ApiError as e:
            al["warnings"].append(f"The deviation could not be worked out. {e.detail}")
            return out
        out["deviation"] = dev.to_dict()
        out["tolerance"] = tolerance_summary(dev, float(view.get("tolerance_mm") or 0.3), k=float(view.get("k") or 2.0)).to_dict()
        if info.get("mesh_changed") and info.get("note"):
            al["warnings"].append(str(info["note"]))
        return out

    def _seam(self, pid: str, sid: str, doc: dict, wanted: str | None) -> dict | None:
        """One kept seam's flush on the current mesh (the one asked for, else the first that can be measured)."""
        seams = doc.get("seams") or []
        if wanted is not None:
            if not any(s["id"] == wanted for s in seams):
                raise ApiError(404, "This seam is no longer kept with the scan.", "Reload the page to see the kept seams.")
            seams = [s for s in seams if s["id"] == wanted]
        for s in seams:
            try:
                record, _, view, _ = self.flush.measure_kept(pid, sid, s["id"])
            except ApiError as e:
                if e.status == 422:  # the engine cannot measure it on this mesh: the next one
                    continue
                raise
            return {**record, "name": view["name"]}
        return None

    # ------------------------------------------------------------------ collecting
    def collect(self, pid: str, sid: str, *, tolerance_mm: float | None = None, numbers_in_part_frame: bool = False,
                seam: str | None = None, pictures: Any = (), parts: str = "all") -> dict:
        """The report's input for a scan (module docstring). ``parts``: ``"all"`` for the page, ``"features"`` for
        the CSV (the kept features only), ``"tables"`` for the JSON (the features and the sections)."""
        if tolerance_mm is not None and not (isinstance(tolerance_mm, (int, float)) and math.isfinite(tolerance_mm)
                                             and tolerance_mm > 0):
            raise ApiError(422, "The tolerance must be a number of mm above 0.", "Give the tolerance in mm, for example 0.3.")
        project = self.st.projects.get(pid)  # 404 for an unknown project
        scan = self.st.projects.scan_entry(pid, sid)  # ... or scan
        pics = self.pictures(pictures) if parts == "all" else []
        doc, _, version = self.feats.snapshot(pid, sid)
        data: dict = {"project": {"name": project.get("name") or pid}, "scan": {"name": scan.get("name") or sid},
                      "date": dt.date.today().isoformat(), "app_version": __version__,
                      "numbers_in_part_frame": bool(numbers_in_part_frame)}
        data["features"] = self.feats.get(pid, sid)
        moves = self._cad_moves(pid, sid, doc)
        if moves:
            data["cad_moves"] = moves
        if parts == "features":
            return data
        mesh = self.st.processing.mesh_arrays(pid, sid) if version is not None else None
        if mesh is not None:
            v, V, F, S, Fl = mesh
            mesh = (int(v), V, F, S if S is not None and len(S) == len(V) else None,
                    Fl if Fl is not None and len(Fl) == len(F) else None)
            sections = self._sections(pid, sid, mesh)
            if sections:
                data["sections"] = sections
        if parts == "tables":
            return data
        rep = self._result_report(pid, sid) if version is not None else None
        process = self._process(rep)
        sp = self.st.processing.peek(pid, sid) if process else None
        made = (sp.state.get("made") or {}) if sp is not None else {}
        if process and isinstance(made.get("seconds"), (int, float)):  # the whole Make mesh (UX_PLAN R17)
            process["make_mesh_s"] = float(made["seconds"])
        if process:
            data["process"] = process
        cal = self._calibration(pid, sid, scan)
        merge = self._merge(scan, rep)
        if merge:
            data["merge"] = merge
        status = rep.get("health_status") if rep is not None else None
        if isinstance(status, dict) and status.get("state") in HEALTH_STATES:
            # the scan's own status (H3/H4): what the scan saw of its calibration, not today's setup state
            cal = {**(cal or {}), "health": {k: status.get(k) for k in HEALTH_KEYS}}
        path = path_check_short(rep)
        if path is not None:  # the camera path's check (ENGINE_PLAN X0a), beside the calibration's status
            cal = {**(cal or {}), "path": {**path, "warnings": user_warnings(rep)}}
        if cal:
            data["calibration"] = cal
        if tolerance_mm is not None and version is not None:
            data["check"] = self._check(pid, sid, tolerance_mm)
        comparison = self._comparison(pid, sid, version)
        if comparison:
            data["comparison"] = comparison
        if version is not None:
            g = self._seam(pid, sid, doc, seam)
            if g is not None:
                data["gap_flush"] = g
        elif seam is not None and not any(s["id"] == seam for s in doc.get("seams") or []):
            raise ApiError(404, "This seam is no longer kept with the scan.", "Reload the page to see the kept seams.")
        if pics:
            data["pictures"] = pics
        return data

    # ------------------------------------------------------------------ the files
    def file(self, pid: str, sid: str, name: str, options: Mapping[str, Any]) -> tuple[bytes, str, str]:
        """``POST .../report/{name}`` (``report.html``, ``features.csv`` or ``features.json``) with the request's
        options: the file's bytes, its download name and its media type."""
        from ohs.report import build_html
        from ohs.report.tables import features_csv, features_json
        from ohs.server.cad import _file_stem

        if name not in FILES:
            raise ApiError(404, "The report has no file of this name.",
                           "Download the report page, or its table of fitted shapes (CSV or JSON).")
        opts = {k: options.get(k) for k in ("tolerance_mm", "seam")}
        parts = {"report.html": "all", "features.csv": "features", "features.json": "tables"}[name]
        data = self.collect(pid, sid, tolerance_mm=opts["tolerance_mm"],
                            numbers_in_part_frame=bool(options.get("numbers_in_part_frame")), seam=opts["seam"],
                            pictures=options.get("pictures") or (), parts=parts)
        if name == "report.html":
            body = build_html(data).encode("utf-8")
        elif name == "features.csv":
            body = features_csv(data).encode("utf-8-sig")  # a byte-order mark: spreadsheets read ± and Ø
        else:
            body = features_json(data).encode("utf-8")
        end, media = FILES[name]
        return body, _file_stem(data["scan"]["name"]) + end, media
