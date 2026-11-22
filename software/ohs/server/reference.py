"""References and the comparison with one over the API (FEATURE_PLAN I2; APP_API.md "References and the
comparison with one"): the part as it should be (an STL, OBJ or PLY) added to a project, and a scan of the part
as it is compared with it: "87 % within ± 0.3 mm", and where the rest is.

The engine is :mod:`ohs.inspect.reference` (reading and checking a reference, the units finding, the three
alignments, the deviation per vertex and the answer for a tolerance); the scan is the current mesh every mesh
route shares (:meth:`ProcessManager.mesh_arrays <ohs.server.processing.ProcessManager.mesh_arrays>`, with each
vertex's sigma and each face's filled flag). This module adds where things are kept, the routes' shapes, the
alignment as a job, and the caches.

**References of a project** live in ``<project>/references/``: each file as it came (``<id>.stl``, ``.obj`` or
``.ply``), a display copy (``<id>.view.ply``: its triangles, decimated to :data:`DISPLAY_MAX_TRIANGLES` when it
has more, as the binary PLY the viewer reads for scan meshes) and ``index.json`` (``{"format_version",
"references": [...]}``: id, name, format, size in bytes, triangles, vertices, extent, area, the engine's notes,
when it was added). Every scan of the project can use them.

**Upload without multipart:** FastAPI's multipart parser needs a package that is not installed, so a file is
sent as the raw request body (``POST .../references?name=<the file's name>``). The name is only a label and the
extension says the format: only its last part is kept (``../../x.stl`` and ``C:\\x.stl`` are both ``x.stl``),
and the file is stored under the id. A format other than STL, OBJ or PLY is refused in the engine's words (a
STEP file: export it as STL first), before anything is written. **The limit** is :data:`MAX_BYTES` (100 MB): a
binary STL of about two million triangles, several times a fine CAD export of a hand-sized part and three times
the largest scan the app makes. The whole body is held in memory once and then read and checked by the engine
(its vertices welded, its winding made consistent), which takes some seconds per million triangles and a few
hundred bytes per triangle of memory. A larger body is refused with 413 and not kept (it is read to its end
and dropped, so the client is not cut off mid-send).

**The comparison of a scan** lives in ``scans/<sid>/comparison.json``: which reference, the units decision (the
engine's finding and the scale applied), how it was aligned (the engine's :class:`ReferenceAlignment` record,
with the pose ``T`` that takes the scan's points into the reference's frame) and on which mesh version, the
tolerance and ``k``. Not the arrays: the deviation is worked out on request and kept in memory per mesh version
and pose. A scan has at most one comparison; starting one with another reference replaces it.

* **Units.** Starting a comparison reads the reference against the scan's part
  (:func:`~ohs.inspect.reference.check_units`). A ``"pending"`` finding is answered as it is, and the alignment,
  the deviation and the tolerance answer are refused (409) until ``POST .../comparison/units`` applies the
  factor or keeps the units. Changing the units drops the alignment (it was made at the other size).
* **Aligning** (``POST .../comparison/align``): from the shapes (:func:`~ohs.inspect.reference.align_by_shape`,
  seconds: a job of kind ``reference_align`` with progress and cancel), from three or more picked point pairs
  (:func:`~ohs.inspect.reference.align_by_points`: refined on the shapes as a job, or used as they are, answered
  at once) or on the scan's datums (:func:`~ohs.inspect.reference.align_by_datums` with the part's frame of
  ``features.json``: refused, 422, without a frame, and 409 when it is out of date or cannot be read). A best fit
  made while the scan has a current frame also says how far it lies from the datum pose (``vs_datums``). An
  alignment the engine does not accept is answered but not kept.
* **The deviation** (``GET .../comparison/deviation.json`` and ``.bin``): the engine's
  :func:`~ohs.inspect.reference.deviation_from_reference` on the current mesh at the kept pose. The binary is one
  little-endian float32 per vertex of ``mesh.ply`` (mm, + outside the reference; NaN where not compared), then one
  byte per vertex: 0 compared, 1 not measured (a filled face, no usable sigma), 2 not on the reference (a table, a
  clamp, surface far off). Kept per mesh version and pose.
* **The answer for a tolerance** (``POST .../comparison/tolerance``): the engine's
  :func:`~ohs.inspect.reference.tolerance_summary` on that deviation (fast: the deviation is kept), each region
  with its centre in the scan's frame and its faces (base64 ``uint32``, as the Check areas) for the viewer. The
  tolerance asked is kept.
* **When the mesh changes** the scan's frame of reference is the same, so the kept pose stays valid: the
  deviation is worked out again on the current mesh and the answer says so (``mesh_changed``, ``note``).
* Removing the comparison removes ``comparison.json``; removing a reference removes its files and the
  comparisons of the project's scans that use it. Nothing else on disk changes.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

import numpy as np

import ohs.measure.features as kf
from ohs.server.protocol import ApiError, jsonable, plain_reason, sentence

__all__ = ["ReferenceManager", "MAX_BYTES", "DISPLAY_MAX_TRIANGLES", "STATES", "clean_file_name"]

log = logging.getLogger(__name__)

FORMAT_VERSION = 1  # of index.json and comparison.json
MAX_BYTES = 100 * 2**20  # module docstring, "The limit"
DISPLAY_MAX_TRIANGLES = 250_000  # the display copy: as many triangles as a smooth ghost over a large scan allows
NAME_MAX = 120
FOLDER = "references"
INDEX = "index.json"
#: the hint when that list cannot be read: where the file is, in words (its path goes to the log)
_INDEX_RESTORE = ("Restore the list's file (index.json in the references folder of the project's folder) from a backup; "
                  "the scans are untouched.")
COMPARISON = "comparison.json"
DEFAULT_TOLERANCE_MM = 0.3
DEFAULT_K = 2.0
JOB_KIND = "reference_align"
#: the state byte of each vertex in deviation.bin, by its value
STATES = ["compared", "not measured", "not on the reference"]
_HINT_FORMAT = "Export the part from CAD as STL (binary), OBJ or PLY, and add that file."
_HINT_FILE = ("The scan's mesh and recording are untouched. Restore the file comparison.json in the scan's folder "
              "from a backup, or delete it to compare again.")
_GONE = "Reload the page to see the project's CAD models."


def _bad(detail: str, hint: str) -> ApiError:
    return ApiError(422, detail, hint)


def clean_file_name(name: Any) -> str:
    """A reference's name from the file name the client sent: its last part only (a name is never used as a path:
    ``../../x.stl``, ``C:\\x.stl`` and ``/x.stl`` all give ``x.stl``), spaces collapsed, no control characters.
    422 for an empty name or one that is only dots."""
    if not isinstance(name, str) or not name.strip():
        raise _bad("The file has no name, so its format is not known.", _HINT_FORMAT)
    base = re.split(r"[\\/:]", name)[-1]
    base = " ".join("".join(ch for ch in base if ch.isprintable()).split())
    if not base.strip("."):
        raise _bad("This is not a file name.", "Pick the file itself, for example part.stl.")
    if len(base) > NAME_MAX:
        raise _bad(f"The file's name is too long ({len(base)} characters).",
                   f"Rename it to at most {NAME_MAX} characters, then add it again.")
    return base


def _write_json(path: Path, data: Any) -> None:
    """Written through a temporary file, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    tmp.write_text(json.dumps(jsonable(data), indent=2), encoding="utf-8")
    for attempt in range(50):  # Windows: another reader may hold the file for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.02)


def _units_finding(d: Mapping[str, Any]):
    """A :class:`~ohs.inspect.reference.UnitsFinding` from its ``to_dict()``."""
    from ohs.inspect.reference import UnitsFinding

    nan = float("nan")
    num = (lambda x: nan if x is None else float(x))
    return UnitsFinding(status=str(d["status"]), ratio=num(d.get("ratio")), factor=d.get("factor"), unit=d.get("unit"),
                        candidates=list(d.get("candidates") or []), reference_size_mm=num(d.get("reference_size_mm")),
                        scan_size_mm=num(d.get("scan_size_mm")), message=str(d.get("message") or ""))


def _faces_b64(faces: np.ndarray) -> str:
    return base64.b64encode(np.asarray(faces, dtype="<u4").tobytes()).decode("ascii")


class ReferenceManager:
    """The reference and comparison routes of every project and scan of the :class:`~ohs.server.studio.Studio`
    ``st``; the part's frame is read through the :class:`~ohs.server.features.FeatureManager` ``feats``."""

    def __init__(self, st: Any, feats: Any) -> None:
        self.st = st
        self.feats = feats
        self.max_bytes = MAX_BYTES
        self._guard = threading.Lock()
        self._locks: dict[tuple, threading.Lock] = {}
        self._models: dict[tuple, Any] = {}  # (pid, rid, mtime, scale) -> ReferenceModel
        self._scans: dict[tuple, tuple[int, Any]] = {}  # (pid, sid) -> (mesh version, ScanSurface)
        self._devs: dict[tuple, tuple[tuple, Any, dict]] = {}  # (pid, sid) -> (key, Deviation, info)

    # ------------------------------------------------------------------ helpers
    def _lock(self, *key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def clear_cache(self) -> None:
        """Forget everything held in memory (as when the app starts again); the files stay."""
        with self._guard:
            self._models.clear()
            self._scans.clear()
            self._devs.clear()

    def _folder(self, pid: str) -> Path:
        return self.st.projects.references_dir(pid)  # 404 for an unknown project

    @staticmethod
    def _read_index(folder: Path) -> list[dict]:
        p = folder / INDEX
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as e:
            log.warning("the project's list of references %s cannot be read: %s", p, e)
            raise ApiError(500, "The project's list of CAD models cannot be read.", _INDEX_RESTORE) from None
        refs = data.get("references") if isinstance(data, dict) else None
        if not isinstance(refs, list):
            log.warning("the project's list of references %s holds no list", p)  # the path goes to the log only
            raise ApiError(500, "The project's list of CAD models cannot be read.", _INDEX_RESTORE)
        return [r for r in refs if isinstance(r, dict) and isinstance(r.get("id"), str)]

    @staticmethod
    def _write_index(folder: Path, refs: list[dict]) -> None:
        _write_json(folder / INDEX, {"format_version": FORMAT_VERSION, "references": refs})

    @staticmethod
    def _ref_view(rec: dict) -> dict:
        """A reference as the API shows it (where it is stored is the server's business)."""
        return {k: v for k, v in rec.items() if k != "file"}

    def _record(self, pid: str, rid: str) -> tuple[Path, dict]:
        folder = self._folder(pid)
        for r in self._read_index(folder):
            if r["id"] == rid:
                return folder, r
        raise ApiError(404, "This CAD model is no longer in the project.", _GONE)

    def _base_model(self, pid: str, rid: str, scale: float = 1.0):
        """The reference as the engine reads it (cached per file and scale): its numbers times ``scale``."""
        from ohs.inspect.reference import read_reference

        folder, rec = self._record(pid, rid)
        path = folder / rec["file"]
        try:
            mtime = path.stat().st_mtime_ns
        except FileNotFoundError:
            raise ApiError(404, f"The file of the CAD model \"{rec['name']}\" is missing.",
                           "Remove the CAD model and add the file again.") from None
        key = (pid, rid, mtime, float(scale))
        with self._guard:
            m = self._models.get(key)
        if m is not None:
            return m
        base_key = (pid, rid, mtime, 1.0)
        with self._guard:
            base = self._models.get(base_key)
        if base is None:
            try:
                base = read_reference(path, name=rec["name"])
            except ValueError as e:
                raise ApiError(409, plain_reason(e, "The CAD model's file cannot be read."),
                               "Remove the CAD model and add the file again.") from None
            with self._guard:
                self._models[base_key] = base
        m = base if scale == 1.0 else base.apply_units(scale)
        with self._guard:
            self._models[key] = m
        return m

    def _version(self, pid: str, sid: str) -> int | None:
        sp = self.st.processing.scan(pid, sid)
        with sp.lock:
            return None if sp.state["base"] is None else int(sp.state["version"])

    def _scan(self, pid: str, sid: str) -> tuple[int, Any]:
        """``(mesh version, ScanSurface)`` of the current mesh, made once per version; 409 before there is a mesh."""
        from ohs.inspect.reference import ScanSurface

        v, V, F, S, Fl = self.st.processing.mesh_arrays(pid, sid)
        with self._guard:
            c = self._scans.get((pid, sid))
        if c is not None and c[0] == int(v):
            return c
        S = S if S is not None and len(S) == len(V) else None
        Fl = Fl if Fl is not None and len(Fl) == len(F) else None
        c = (int(v), ScanSurface(V, F, sigma=S, filled=Fl, name=sid))
        with self._guard:
            self._scans[(pid, sid)] = c
        return c

    def _frame(self, pid: str, sid: str) -> tuple[dict | None, dict | None]:
        """The scan's part frame (as ``features.json`` keeps it) and its state (None: no frame)."""
        doc, _, _ = self.feats.snapshot(pid, sid)
        return doc.get("frame"), kf.frame_state(doc.get("frame"), doc["features"])

    def _current_frame(self, pid: str, sid: str) -> dict | None:
        """The part frame when it can be read and is current (for a best fit's distance from it), else None."""
        frame, state = self._frame(pid, sid)
        return frame if state is not None and state["readable"] and not state["out_of_date"] else None

    # ------------------------------------------------------------------ references of a project
    def list(self, pid: str) -> dict:
        """``GET /api/projects/{pid}/references``: oldest first."""
        return {"references": [self._ref_view(r) for r in self._read_index(self._folder(pid))]}

    def check_name(self, name: Any) -> tuple[str, str]:
        """``(the clean name, its extension)``; 422 in the engine's words for a format it does not read."""
        from ohs.inspect.reference import SUFFIXES, read_reference

        clean = clean_file_name(name)
        ext = Path(clean).suffix.lower()
        if ext not in SUFFIXES:
            try:  # the engine says it first, about the extension, before it looks for the file
                read_reference(Path(f"never-read{ext}"), name=clean)
            except ValueError as e:
                raise _bad(plain_reason(e, "This file cannot be read as a CAD model."), _HINT_FORMAT) from None
        return clean, ext

    async def upload(self, pid: str, name: Any, length: str | None, chunks: AsyncIterator[bytes]) -> dict:
        """``POST /api/projects/{pid}/references?name=...``: the request body is the file (module docstring)."""
        import asyncio

        clean, ext = self.check_name(name)
        folder = self._folder(pid)
        limit = int(self.max_bytes)
        try:
            too_big = length is not None and int(length) > limit
        except ValueError:
            too_big = False
        buf = bytearray()
        n = 0
        async for c in chunks:
            n += len(c)
            if too_big or n > limit:  # read to the end and dropped: the client is not cut off mid-send
                too_big = True
                buf = bytearray()
                continue
            buf += c
        if too_big:
            raise ApiError(413, f"The file is larger than {limit / 2**20:g} MB, the most a CAD model may be.",
                           "Export it with coarser triangles (a chord tolerance of 0.01 mm is plenty), as binary STL "
                           "rather than ASCII.")
        if not buf:
            raise _bad("The file is empty.", _HINT_FORMAT)
        return await asyncio.to_thread(self._add, pid, folder, clean, ext, bytes(buf))

    def _add(self, pid: str, folder: Path, clean: str, ext: str, data: bytes) -> dict:
        from ohs.inspect.reference import read_reference

        rid = f"ref-{secrets.token_hex(5)}"
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f".upload-{rid}{ext}"
        final = folder / f"{rid}{ext}"
        try:
            tmp.write_bytes(data)
            try:
                ref = read_reference(tmp, name=clean)
            except ValueError as e:
                raise _bad(plain_reason(e, "This file cannot be read as a CAD model."), _HINT_FORMAT) from None
            os.replace(tmp, final)
            display = self._write_display(folder, rid, ref)
        except BaseException:
            for p in (tmp, final, folder / f"{rid}.view.ply"):
                p.unlink(missing_ok=True)
            raise
        rec = {"id": rid, "name": clean, "format": ext[1:], "file": final.name, "bytes": len(data),
               "triangles": int(len(ref.F)), "vertices": int(len(ref.V)), "extent_mm": [float(e) for e in ref.extent_mm],
               "size_mm": float(ref.size_mm), "area_mm2": float(ref.area_mm2),
               "bounds": [[float(x) for x in ref.bounds[0]], [float(x) for x in ref.bounds[1]]],
               "closed": bool(ref.orientation.get("closed")), "orientation": str(ref.orientation.get("note", "")),
               "dropped_faces": int(ref.dropped_faces), "warnings": list(ref.warnings), "display": display,
               "added": kf.now()}
        with self._lock("index", pid):
            refs = self._read_index(folder)
            refs.append(rec)
            self._write_index(folder, refs)
        with self._guard:
            self._models[(pid, rid, final.stat().st_mtime_ns, 1.0)] = ref
        log.info("reference %s (%s, %d triangles) added to project %s", rid, clean, len(ref.F), pid)
        return self._ref_view(rec)

    @staticmethod
    def _write_display(folder: Path, rid: str, ref: Any) -> dict:
        """The display copy (module docstring): the reference's triangles as they were wound, decimated when there
        are more than :data:`DISPLAY_MAX_TRIANGLES`."""
        from ohs.track.export import save_mesh

        V, F = ref.V, ref.F
        decimated = False
        if len(F) > DISPLAY_MAX_TRIANGLES:
            from ohs.mesh import decimate

            try:
                V, F = decimate(V, F, target_triangles=DISPLAY_MAX_TRIANGLES)
                decimated = True
            except Exception as e:  # noqa: BLE001 (shown in full rather than not at all)
                log.warning("the display copy of reference %s could not be decimated: %s", rid, e)
        used = np.unique(F)
        remap = np.full(len(V), -1, dtype=np.int64)
        remap[used] = np.arange(len(used))
        save_mesh(folder / f"{rid}.view.ply", np.asarray(V)[used], remap[F])
        return {"triangles": int(len(F)), "decimated": decimated}

    def mesh(self, pid: str, rid: str) -> Path:
        """``GET .../references/{rid}/mesh.ply``: the display copy (made again when it is missing)."""
        folder, rec = self._record(pid, rid)
        path = folder / f"{rid}.view.ply"
        if not path.is_file():
            self._write_display(folder, rid, self._base_model(pid, rid))
        return path

    def rename(self, pid: str, rid: str, name: str) -> dict:
        """``PATCH .../references/{rid}``: a new name (its label only; the format stays as it was read)."""
        n = " ".join(str(name or "").split())
        if not n:
            raise _bad("The name is empty.", "Type a name for the CAD model.")
        if len(n) > NAME_MAX:
            raise _bad(f"The name is too long ({len(n)} characters).", f"Use at most {NAME_MAX} characters.")
        folder = self._folder(pid)
        with self._lock("index", pid):
            refs = self._read_index(folder)
            for r in refs:
                if r["id"] == rid:
                    r["name"] = n
                    self._write_index(folder, refs)
                    return self._ref_view(r)
        raise ApiError(404, "This CAD model is no longer in the project.", _GONE)

    def delete(self, pid: str, rid: str) -> None:
        """``DELETE .../references/{rid}``: its files and the comparisons of the project's scans that use it."""
        folder = self._folder(pid)
        with self._lock("index", pid):
            refs = self._read_index(folder)
            rec = next((r for r in refs if r["id"] == rid), None)
            if rec is None:
                raise ApiError(404, "This CAD model is no longer in the project.", _GONE)
            self._write_index(folder, [r for r in refs if r["id"] != rid])
            for p in folder.iterdir():
                if p.name.startswith(rid + "."):
                    p.unlink(missing_ok=True)
        for scan in self.st.projects.get(pid)["scans"]:
            sid = scan["id"]
            with self._lock("scan", pid, sid):
                doc = self._read(pid, sid, missing_ok=True)
                if doc is not None and doc.get("reference_id") == rid:
                    self._path(pid, sid).unlink(missing_ok=True)
                    with self._guard:
                        self._devs.pop((pid, sid), None)
        with self._guard:
            for k in [k for k in self._models if k[0] == pid and k[1] == rid]:
                del self._models[k]
        log.info("reference %s removed from project %s", rid, pid)

    # ------------------------------------------------------------------ the comparison file
    def _path(self, pid: str, sid: str) -> Path:
        return self.st.projects.scan_dir(pid, sid) / COMPARISON

    def _read(self, pid: str, sid: str, missing_ok: bool = False) -> dict | None:
        p = self._path(pid, sid)
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if missing_ok:
                return None
            raise ApiError(404, "This scan is not compared with a CAD model yet.",
                           "Pick one of the project's CAD models to compare it with.") from None
        except (OSError, ValueError) as e:
            log.warning("the comparison of %s/%s cannot be read: %s", pid, sid, e)
            raise ApiError(500, "This scan's comparison with the CAD model cannot be read.", _HINT_FILE) from None
        if not isinstance(doc, dict) or not isinstance(doc.get("reference_id"), str) or not isinstance(doc.get("units"), dict):
            raise ApiError(500, "This scan's comparison with the CAD model is not one this version of the app can "
                                "read.", _HINT_FILE)
        if int(doc.get("format_version", 0)) > FORMAT_VERSION:
            raise ApiError(409, "This scan's comparison with the CAD model was made by a later version of the app.",
                           "Update the app, or remove the comparison.")
        return doc

    def _load(self, pid: str, sid: str) -> dict:
        self.st.projects.scan_entry(pid, sid)  # 404 for an unknown project or scan
        return self._read(pid, sid)  # type: ignore[return-value]

    def _save(self, pid: str, sid: str, doc: dict) -> None:
        doc["modified"] = kf.now()
        _write_json(self._path(pid, sid), doc)

    def _aligning_job(self, pid: str, sid: str) -> Any:
        for job in self.st.jobs.active(JOB_KIND):
            if job.meta.get("project_id") == pid and job.meta.get("scan_id") == sid:
                return job
        return None

    def _view(self, pid: str, sid: str, doc: dict, version: int | None) -> dict:
        """The kept comparison as the API shows it: with the reference's record, the current mesh version,
        ``mesh_changed`` and ``view_matrix`` (the reference's file numbers into the scan's frame, for drawing it)."""
        try:
            ref = self._ref_view(self._record(pid, doc["reference_id"])[1])
        except ApiError:
            ref = None
        al = doc.get("alignment")
        scale = float(doc.get("scale", 1.0))
        vm = None
        if al is not None:
            vm = (np.linalg.inv(np.asarray(al["T"], dtype=np.float64)) @ np.diag([scale, scale, scale, 1.0])).tolist()
        job = self._aligning_job(pid, sid)
        return jsonable({"format_version": FORMAT_VERSION, "reference_id": doc["reference_id"], "reference": ref,
                         "units": doc["units"], "scale": scale, "ready": doc["units"].get("status") != "pending",
                         "alignment": al, "aligned_mesh_version": doc.get("aligned_mesh_version"), "mesh_version": version,
                         "mesh_changed": bool(al is not None and version is not None
                                              and doc.get("aligned_mesh_version") != version),
                         "view_matrix": vm, "tolerance_mm": float(doc.get("tolerance_mm", DEFAULT_TOLERANCE_MM)),
                         "k": float(doc.get("k", DEFAULT_K)), "created": doc.get("created"),
                         "modified": doc.get("modified"), "aligning": None if job is None else job.id})

    @staticmethod
    def _require_ready(doc: dict) -> None:
        if doc["units"].get("status") == "pending":
            raise ApiError(409, f"The CAD model's units are not settled yet. {sentence(doc['units'].get('message'))}",
                           "Apply the factor, or keep the units as they are, first.")

    # ------------------------------------------------------------------ comparison routes
    def get(self, pid: str, sid: str) -> dict:
        """``GET .../comparison``: ``{"comparison": the view or null, "mesh_version"}``."""
        self.st.projects.scan_entry(pid, sid)
        v = self._version(pid, sid)
        doc = self._read(pid, sid, missing_ok=True)
        return {"comparison": None if doc is None else self._view(pid, sid, doc, v), "mesh_version": v}

    def start(self, pid: str, sid: str, rid: str, tolerance_mm: float | None = None) -> dict:
        """``POST .../comparison``: compare the scan with a reference of its project (replaces a kept comparison).
        The answer carries the units finding; nothing more happens while it is ``pending``."""
        from ohs.inspect.reference import check_units

        self.st.projects.scan_entry(pid, sid)
        self._record(pid, rid)  # 404 before the mesh is read
        v, scan = self._scan(pid, sid)  # 409 before there is a mesh
        base = self._base_model(pid, rid)
        try:
            finding = check_units(base.size_mm, scan.part_size_mm())
        except ValueError as e:
            raise _bad(plain_reason(e, "The scan and the CAD model cannot be compared."),
                       "Check that the scan has a mesh and that the CAD model is the right part.") from None
        now = kf.now()
        doc = {"format_version": FORMAT_VERSION, "reference_id": rid, "units": finding.to_dict(), "scale": 1.0,
               "alignment": None, "aligned_mesh_version": None,
               "tolerance_mm": float(tolerance_mm) if tolerance_mm is not None else DEFAULT_TOLERANCE_MM, "k": DEFAULT_K,
               "created": now, "modified": now}
        with self._lock("scan", pid, sid):
            self._save(pid, sid, doc)
            with self._guard:
                self._devs.pop((pid, sid), None)
        return self._view(pid, sid, doc, v)

    def units(self, pid: str, sid: str, action: str, factor: float | None = None) -> dict:
        """``POST .../comparison/units``: ``apply`` the factor (the finding's, or ``factor``) or ``keep`` the units.
        A kept alignment goes (it was made at the other size)."""
        from dataclasses import replace

        with self._lock("scan", pid, sid):
            doc = self._load(pid, sid)
            base = replace(self._base_model(pid, doc["reference_id"]), units=_units_finding(doc["units"]))
            if action == "apply":
                f = factor if factor is not None else doc["units"].get("factor")
                if f is None:
                    raise _bad("There is no unit factor to apply: the sizes fit no unit.",
                               "Type the factor, or keep the units as they are.")
                try:
                    m = base.apply_units(float(f))
                except ValueError as e:
                    raise _bad(plain_reason(e, "This factor cannot be used."), "Give a factor above 0.") from None
                doc["units"], doc["scale"] = m.units.to_dict(), float(f)
            elif action == "keep":
                doc["units"], doc["scale"] = base.keep_units().units.to_dict(), 1.0
            else:
                raise _bad("The page asked for something the units cannot do.",
                           "Apply the factor, or keep the units as they are.")
            doc["alignment"], doc["aligned_mesh_version"] = None, None
            self._save(pid, sid, doc)
            with self._guard:
                self._devs.pop((pid, sid), None)
        return self._view(pid, sid, doc, self._version(pid, sid))

    def _keep(self, pid: str, sid: str, rid: str, scale: float, al: Any, version: int) -> tuple[bool, dict | None]:
        """Keep an accepted alignment, when the comparison is still the one it was made for: ``(kept, view)``."""
        with self._lock("scan", pid, sid):
            doc = self._read(pid, sid, missing_ok=True)
            if doc is None:
                return False, None
            if al.accepted and doc["reference_id"] == rid and float(doc.get("scale", 1.0)) == float(scale):
                doc["alignment"], doc["aligned_mesh_version"] = jsonable(al.to_dict()), int(version)
                self._save(pid, sid, doc)
                with self._guard:
                    self._devs.pop((pid, sid), None)
                return True, self._view(pid, sid, doc, self._version(pid, sid))
            return False, self._view(pid, sid, doc, self._version(pid, sid))

    def align(self, pid: str, sid: str, method: str, pairs: list[dict] | None = None, refine: bool = True) -> dict:
        """``POST .../comparison/align``: ``{"job": ..., "comparison": null}`` for a best fit (``shape``, or
        ``points`` refined), ``{"job": null, "comparison": ...}`` for what is answered at once (``datums``,
        ``points`` not refined). Module docstring, "Aligning"."""
        from ohs.align.pairwise import pose_from_point_pairs
        from ohs.inspect.reference import align_by_datums, align_by_points

        doc = self._load(pid, sid)
        self._require_ready(doc)
        if method not in ("shape", "points", "datums"):
            raise _bad("The app does not know this way of aligning.",
                       "Align from the shape, on the part's axes, or from picked points.")
        rid, scale = doc["reference_id"], float(doc.get("scale", 1.0))
        if self._aligning_job(pid, sid) is not None:
            raise ApiError(409, "This scan is being aligned with the CAD model already.", "Wait for it, or cancel it.")
        sp = rp = None
        if method == "points":
            if not pairs or len(pairs) < 3:
                raise _bad(f"{len(pairs or [])} {'pair' if len(pairs or []) == 1 else 'pairs'} of matching points "
                           f"{'is' if len(pairs or []) == 1 else 'are'} too few.",
                           "Pick at least three matching points on the scan and on the CAD model.")
            sp = [p["scan"] for p in pairs]
            rp = [p["reference"] for p in pairs]
            try:
                pose_from_point_pairs(sp, rp)
            except ValueError as e:
                log.info("picked points give no pose: %s", e)
                raise _bad("The picked points cannot place the scan: they may lie on a line.",
                           "Pick three or more points that span a triangle.") from None
        if method == "datums":
            frame, state = self._frame(pid, sid)
            if state is None:
                raise _bad("The part's axes are not set for this scan, so there is nothing to align on.",
                           "Set the part's axes from three fitted shapes first, or align from the shape.")
            if not state["readable"]:
                raise ApiError(409, state["out_of_date_reasons"][0], "Set the part's axes again, or remove them.")
            if state["out_of_date"]:
                raise ApiError(409, "The part's axes are out of date (" + "; ".join(state["out_of_date_reasons"]) + ").",
                               "Set the part's axes again for the shapes as they are now.")
            v = self._version(pid, sid)
            if v is None:
                raise ApiError(409, "This scan has no mesh yet.", "Press Make mesh first.")
            try:
                al = align_by_datums(frame)
            except ValueError as e:
                raise ApiError(409, plain_reason(e, "The part's axes cannot be used to align."),
                               "Set the part's axes again.") from None
            return {"job": None, "comparison": self._keep(pid, sid, rid, scale, al, v)[1]}
        if method == "points" and not refine:
            v, scan = self._scan(pid, sid)
            try:
                al = align_by_points(scan, self._base_model(pid, rid, scale), sp, rp, refine=False)
            except ValueError as e:
                log.info("picked points give no pose: %s", e)
                raise _bad("The picked points cannot place the scan: they may lie on a line.",
                           "Pick three or more points that span a triangle.") from None
            return {"job": None, "comparison": self._keep(pid, sid, rid, scale, al, v)[1]}
        return {"job": self._submit(pid, sid, rid, scale, method, sp, rp).to_dict(), "comparison": None}

    def _submit(self, pid: str, sid: str, rid: str, scale: float, method: str, sp: Any, rp: Any) -> Any:
        from ohs.inspect.reference import align_by_points, align_by_shape

        def run(ctx: Any) -> dict:
            ctx.progress(0.05, "Reading the scan", force=True)
            v, scan = self._scan(pid, sid)
            ctx.check()
            ctx.progress(0.15, "Reading the CAD model", force=True)
            model = self._base_model(pid, rid, scale)
            frame = self._current_frame(pid, sid)
            ctx.check()
            if method == "shape":
                ctx.progress(0.25, "Aligning from the shapes (a best fit, a few seconds)", force=True)
                al = align_by_shape(scan, model, datums=frame)
            else:
                ctx.progress(0.25, "Aligning from the picked points, refined on the shapes", force=True)
                al = align_by_points(scan, model, sp, rp, refine=True, datums=frame)
            ctx.check()  # cancelled meanwhile: nothing is kept
            ctx.progress(0.95, "Keeping the alignment", force=True)
            kept, view = self._keep(pid, sid, rid, scale, al, v)
            return {"alignment": al.to_dict(), "kept": kept, "comparison": view, "mesh_version": v, "message": al.verdict}

        return self.st.jobs.submit(JOB_KIND, run, message="Queued: align the scan with the CAD model",
                                   meta={"project_id": pid, "scan_id": sid, "reference_id": rid, "method": method})

    def deviation(self, pid: str, sid: str) -> tuple[dict, Any]:
        """The deviation of the current mesh at the kept pose: ``(deviation.json, the engine's Deviation)``, made
        once per mesh version, pose and reference."""
        from ohs.inspect.reference import ReferenceAlignment, deviation_from_reference

        doc = self._load(pid, sid)
        self._require_ready(doc)
        al = doc.get("alignment")
        if al is None:
            raise ApiError(409, "The scan is not aligned with the CAD model yet.",
                           "Align it first: from the shape, on the part's axes, or from picked points.")
        rid, scale = doc["reference_id"], float(doc.get("scale", 1.0))
        with self._lock("dev", pid, sid):  # a second request waits for the first one's result
            v, scan = self._scan(pid, sid)
            model = self._base_model(pid, rid, scale)
            T = np.asarray(al["T"], dtype=np.float64)
            key = (v, rid, id(model), T.tobytes())
            with self._guard:
                cached = self._devs.get((pid, sid))
            if cached is not None and cached[0] == key:
                return cached[2], cached[1]
            ra = ReferenceAlignment(T=T, method=str(al["method"]), accepted=True, verdict=str(al.get("verdict", "")))
            try:
                dev = deviation_from_reference(scan, model, ra)
            except ValueError as e:
                raise ApiError(409, plain_reason(e, "The deviation from the CAD model cannot be worked out."),
                               "Align the scan again.") from None
            changed = doc.get("aligned_mesh_version") != v
            info = jsonable({"version": v, "vertices": int(len(dev.V)), "reference_id": rid, "scale": scale,
                             "aligned_mesh_version": doc.get("aligned_mesh_version"), "mesh_changed": changed,
                             "note": ("The mesh has changed since the scan was aligned with the CAD model. The "
                                      "alignment is kept (the scan's axes are the same) and the deviation is worked "
                                      "out again on the current mesh."
                                      if changed else None),
                             "layout": "float32 per vertex (mm, NaN where not compared), then one state byte per vertex",
                             "states": STATES, **dev.to_dict()})
            with self._guard:
                self._devs[(pid, sid)] = (key, dev, info)
            return info, dev

    def deviation_bin(self, pid: str, sid: str) -> tuple[bytes, int]:
        """``GET .../comparison/deviation.bin``: the values, then the states (module docstring)."""
        info, dev = self.deviation(pid, sid)
        data = dev.offset.astype("<f4", copy=False).tobytes() + dev.state.astype(np.uint8, copy=False).tobytes()
        return data, int(info["version"])

    def tolerance(self, pid: str, sid: str, tolerance_mm: float, k: float | None = None) -> dict:
        """``POST .../comparison/tolerance``: the engine's answer at ``tolerance_mm`` (kept with the comparison)."""
        from ohs.inspect.reference import tolerance_summary

        if not (isinstance(tolerance_mm, (int, float)) and math.isfinite(tolerance_mm) and tolerance_mm > 0):
            raise _bad("The tolerance must be a number of mm above 0.", "Give the tolerance in mm, for example 0.3.")
        info, dev = self.deviation(pid, sid)
        doc = self._load(pid, sid)
        kk = float(k) if k is not None else float(doc.get("k", DEFAULT_K))
        try:
            s = tolerance_summary(dev, float(tolerance_mm), k=kk)
        except ValueError as e:
            raise _bad(plain_reason(e, "The comparison cannot be worked out at this tolerance."),
                       "Give the tolerance in mm, for example 0.3.") from None
        Tinv = np.linalg.inv(np.asarray(doc["alignment"]["T"], dtype=np.float64))
        out = s.to_dict()
        regions = []
        for r, d in zip(s.regions, out["regions"]):
            c = Tinv[:3, :3] @ np.asarray(r.center, dtype=np.float64) + Tinv[:3, 3]
            regions.append({**d, "center_scan": [float(x) for x in c], "faces": _faces_b64(r.faces)})
        out.update(regions=regions, version=info["version"], mesh_changed=info["mesh_changed"],
                   aligned_mesh_version=info["aligned_mesh_version"], method=doc["alignment"]["method"],
                   coverage={key: info[key] for key in ("scan_area_mm2", "compared_area_mm2", "off_reference_share",
                                                         "not_measured_share", "uncovered_share", "uncovered_area_mm2",
                                                         "reference_area_mm2")})
        with self._lock("scan", pid, sid):
            cur = self._read(pid, sid, missing_ok=True)
            if cur is not None and cur.get("tolerance_mm") != float(tolerance_mm) and cur.get("alignment") == doc.get("alignment"):
                cur["tolerance_mm"] = float(tolerance_mm)
                self._save(pid, sid, cur)
        return jsonable(out)

    def remove(self, pid: str, sid: str) -> None:
        """``DELETE .../comparison``: forget it (404 when there is none)."""
        with self._lock("scan", pid, sid):
            self._load(pid, sid)
            self._path(pid, sid).unlink(missing_ok=True)
            with self._guard:
                self._devs.pop((pid, sid), None)
