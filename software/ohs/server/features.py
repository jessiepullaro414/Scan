"""Kept features over the API (APP_API.md "Kept features"): fits, two-point measurements and
relations saved with a scan, renamed and deleted, and fitted again when the mesh has changed.

The records and the file are :mod:`ohs.measure.features`; the fits are the fit route's
(:meth:`ProcessManager.fit <ohs.server.processing.ProcessManager.fit>`), so a kept fit has exactly
the numbers a click in the viewer gives. This module adds:

* **keeping a fit:** ``POST .../features`` fits like ``POST .../fit`` and keeps the answer, the
  click it was grown from (its ``seed``) and the mesh version, in one step; the region goes to its
  own file;
* **staleness:** a feature fitted on another mesh version than the current one is *stale*
  ("fitted on an earlier mesh"). Reading marks it; nothing is refitted on its own;
* **fit again:** ``POST .../features/refit`` refits stale features on the current mesh from their
  seeds. Face numbers change with an edit, so the seed's face is found again as the measured face
  nearest the saved point; when that face is not under the point any more (the region was cut
  away), the feature is not fitted on some other surface. A feature that cannot be fitted again
  stays as it was, still marked, and the answer says why;
* **the extent** (FEATURE_PLAN K2): a fit is kept and fitted again with where its region lies on
  its shape (the fit route's ``extent``, asked for with ``with_extent``). ``features.json`` keeps
  its small part, the outline goes to its own file (:func:`ohs.measure.features.split_extent`),
  served by ``GET .../features/{id}/outline``. A fit kept before extents existed has none; "fit
  again" gives it one (from its own click, on its own mesh, when it is not stale);
* **a cylinder's form:** a fit is kept and fitted again with whether a cylinder is a boss or a bore
  (the fit route's ``form``, asked for with ``with_form``; null for a plane or a hole, and when the
  fit cannot tell). A fit kept before forms existed has no ``form`` and is not fitted again for it;
  its next "fit again" sets it;
* **relations from the engine:** every relation shown (a kept one in ``GET .../features``, any two
  kept fits in ``GET .../features/relate``) carries :func:`ohs.measure.relate.relate` on the two
  kept shapes, its values with their 1σ, and ``stale`` when one of the fits was made on an earlier
  mesh. Nothing of it is stored: it is worked out when shown;
* **the part's frame** (FEATURE_PLAN K3): ``POST .../features/frame`` builds the part's coordinate
  system from three kept fits (:func:`ohs.measure.frame.part_frame`) and keeps it under ``frame``;
  a refusal of the engine is answered 422 with its own words, a fit made on an earlier mesh 409.
  Reading shows whether the frame is still current (:func:`ohs.measure.features.frame_state`);
  nothing is rebuilt on its own. ``GET .../features/frame/mesh.ply`` (or ``.stl``) is the scan's
  current result mesh turned into the frame, made on request: the scan's own files stay as they are.

**Locking.** One lock per scan around reading, changing and writing ``features.json``. Fits run
outside it (they can take a second); a refit is only kept when its feature has not been changed
or deleted meanwhile.
"""
from __future__ import annotations

import base64
import tempfile
import threading
from pathlib import Path
from typing import Any

import numpy as np

import ohs.measure.features as kf
from ohs.server.protocol import ApiError

__all__ = ["FeatureManager"]

GONE_MIN_MM = 2.0  # fit again: the surface under a saved click is gone when the nearest face is farther than this
GONE_EDGES = 1.5  # ... and than this many of that face's longest edge
_HINT_FILE ="check or restore the file (the scan's mesh and recording are untouched), or delete it to start again"


class FeatureManager:
    """The kept features of every scan of the :class:`~ohs.server.studio.Studio` ``st``."""

    def __init__(self, st: Any) -> None:
        self.st = st
        self._locks: dict[tuple[str, str], threading.Lock] = {}
        self._guard = threading.Lock()

    # ------------------------------------------------------------------ helpers
    def _lock(self, pid: str, sid: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault((pid, sid), threading.Lock())

    def _dir(self, pid: str, sid: str):
        self.st.projects.scan_entry(pid, sid)  # 404 for an unknown project or scan
        return self.st.projects.scan_dir(pid, sid)

    def _load(self, pid: str, sid: str) -> dict:
        d = self._dir(pid, sid)
        try:
            return kf.load(d)
        except ValueError as e:
            raise ApiError(500, f"the kept features of scan {sid} cannot be read: {e}", _HINT_FILE) from None

    def _save(self, pid: str, sid: str, doc: dict) -> dict:
        return kf.save(self.st.projects.scan_dir(pid, sid), doc)

    def _version(self, pid: str, sid: str) -> int | None:
        """The current mesh's version, None while the scan has no mesh."""
        sp = self.st.processing.scan(pid, sid)
        with sp.lock:
            return None if sp.state["base"] is None else int(sp.state["version"])

    @staticmethod
    def _view(item: dict, version: int | None) -> dict:
        """A feature or measurement as the API shows it: with ``stale`` when it was made on another mesh."""
        return {**item, "stale": version is None or item.get("mesh_version") != version}

    @staticmethod
    def _frame_view(frame: dict | None, features: list[dict]) -> dict | None:
        """The kept frame as the API shows it: with ``readable``, ``out_of_date`` and why
        (:func:`ohs.measure.features.frame_state`); None without one."""
        state = kf.frame_state(frame, features)
        return None if state is None else {**frame, **state}

    @staticmethod
    def _relation_view(rel: dict, features: list[dict], version: int | None) -> dict:
        """A kept relation as the API shows it: with ``relation``, the engine's record for its two
        fits (:meth:`Relation.to_dict <ohs.measure.relate.Relation.to_dict>`), and ``stale`` when
        one of them was fitted on another mesh version than the current one. A pair the engine
        cannot relate (a damaged shape) has ``relation`` null and ``error`` with its words."""
        from ohs.measure.relate import relate

        by_id = {f["id"]: f for f in features}
        a, b = by_id.get(rel["a"]), by_id.get(rel["b"])
        out = {**rel, "stale": any(f is None or version is None or f["mesh_version"] != version for f in (a, b))}
        try:
            out["relation"] = relate({"kind": a["kind"], "shape": a["shape"]}, {"kind": b["kind"], "shape": b["shape"]}).to_dict()
        except (ValueError, TypeError, KeyError) as e:
            out["relation"], out["error"] = None, f"these two fits cannot be related: {e}"
        return out

    def _set_view(self, doc: dict, version: int | None) -> dict:
        return {"format_version": doc["format_version"], "frame": self._frame_view(doc["frame"], doc["features"]),
                "mesh_version": version,
                "features": [self._view(f, version) for f in doc["features"]],
                "measurements": [self._view(m, version) for m in doc["measurements"]],
                "relations": [self._relation_view(r, doc["features"], version) for r in doc["relations"]]}

    @staticmethod
    def _name(name: str | None, default: str) -> str:
        if name is None:
            return default
        try:
            return kf.clean_name(name)
        except ValueError as e:
            raise ApiError(422, str(e), f"give a name of 1 to {kf.NAME_MAX} characters") from None

    @staticmethod
    def _find(doc: dict, item_id: str) -> tuple[str, int]:
        for key in ("features", "measurements", "relations"):
            for i, it in enumerate(doc[key]):
                if it["id"] == item_id:
                    return key, i
        raise ApiError(404, f"no kept feature, measurement or relation {item_id!r}",
                       "read the list again (GET .../features)")

    @staticmethod
    def _record(fit: dict, seed: dict, fid: str, name: str, created: str) -> tuple[dict, dict | None]:
        """A feature record from the fit route's answer (asked ``with_extent`` and ``with_form``), and
        the large part of its extent for the side file (or None). The faces are left out: they go to
        their own file."""
        extent, side = kf.split_extent(fit.get("extent"))
        rec = {"id": fid, "name": name, "kind": fit["kind"], "shape": fit["shape"], "seed": seed,
               "n_faces": int(fit["n_faces"]), "tolerance_mm": fit["tolerance_mm"],
               "mean_sigma_mm": fit["mean_sigma_mm"], "correlated_points": fit["correlated_points"],
               "warnings": list(fit["warnings"]), "mesh_version": int(fit["version"]), "extent": extent,
               "form": fit.get("form"), "created": created}
        return rec, side

    @staticmethod
    def _keep_files(d, fid: str, fit: dict, side: dict | None) -> None:
        """Write a fit's region and outline files (an outline kept before and no longer needed goes)."""
        kf.save_faces(d, fid, np.frombuffer(base64.b64decode(fit["faces"]), dtype="<u4"))
        if side is None:
            kf.remove_outline(d, fid)
        else:
            kf.save_outline(d, fid, side, int(fit["version"]))

    # ------------------------------------------------------------------ routes
    def get(self, pid: str, sid: str) -> dict:
        """``GET .../features``: the scan's kept set, each feature and measurement marked ``stale``
        when it was made on another mesh version than the current one."""
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
        return self._set_view(doc, self._version(pid, sid))

    def add_fit(self, pid: str, sid: str, kind: str, face: int, point: Any, tolerance_mm: float | None,
                version: int | None, name: str | None) -> dict:
        """``POST .../features``: fit as ``POST .../fit`` does and keep it. Returns the feature with
        its region (``faces``, base64 as in the fit's answer)."""
        self._dir(pid, sid)
        name = None if name is None else self._name(name, "")
        fit = self.st.processing.fit(pid, sid, kind, face, point, tolerance_mm, version, with_extent=True,
                                     with_form=True)
        seed = {"kind": kind, "face": int(face), "point": [float(x) for x in point],
                "tolerance_mm": None if tolerance_mm is None else float(tolerance_mm)}
        d = self.st.projects.scan_dir(pid, sid)
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            fid = kf.new_id("f")
            rec, side = self._record(fit, seed, fid, name or kf.next_name(doc["features"], "Fit"), kf.now())
            doc["features"].append(rec)
            try:
                self._keep_files(d, fid, fit, side)
                self._save(pid, sid, doc)
            except BaseException:
                kf.remove_faces(d, fid)
                kf.remove_outline(d, fid)
                raise
        return {**self._view(rec, int(fit["version"])), "faces": fit["faces"]}

    def add_measurement(self, pid: str, sid: str, a: Any, b: Any, version: int | None, name: str | None) -> dict:
        """``POST .../features/measurements``: keep a two-point distance. ``version``: the mesh it
        was measured on (the current one when not given)."""
        self._dir(pid, sid)
        current = self._version(pid, sid)
        mv = current if version is None else int(version)
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            rec = {"id": kf.new_id("m"), "name": self._name(name, kf.next_name(doc["measurements"], "Distance")),
                   "a": [float(x) for x in a], "b": [float(x) for x in b], "mesh_version": mv, "created": kf.now()}
            doc["measurements"].append(rec)
            self._save(pid, sid, doc)
        return self._view(rec, current)

    def add_relation(self, pid: str, sid: str, a: str, b: str, name: str | None) -> dict:
        """``POST .../features/relations``: keep that two features are to be related. Returns it as
        ``GET`` shows it (with the engine's record)."""
        self._dir(pid, sid)
        version = self._version(pid, sid)
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            ids = {f["id"] for f in doc["features"]}
            for ref in (a, b):
                if ref not in ids:
                    raise ApiError(422, f"no kept fit {ref!r} to relate", "keep both fits first, then relate them")
            if a == b:
                raise ApiError(422, "a fit cannot be related to itself", "pick two different fits")
            rec = {"id": kf.new_id("r"), "name": self._name(name, kf.next_name(doc["relations"], "Relation")),
                   "a": a, "b": b, "created": kf.now()}
            doc["relations"].append(rec)
            self._save(pid, sid, doc)
        return self._relation_view(rec, doc["features"], version)

    def relate(self, pid: str, sid: str, a: str, b: str) -> dict:
        """``GET .../features/relate?a=&b=``: how two kept fits relate, as a kept relation shows it,
        without keeping it: ``{"a", "b", "stale", "relation"}``. 404 for an id that names no kept
        fit, 422 for one fit twice."""
        self._dir(pid, sid)
        version = self._version(pid, sid)
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
        ids = {f["id"] for f in doc["features"]}
        for ref in (a, b):
            if ref not in ids:
                raise ApiError(404, f"no kept fit {ref!r}", "read the list again (GET .../features)")
        if a == b:
            raise ApiError(422, "a fit cannot be related to itself", "pick two different fits")
        out = self._relation_view({"a": a, "b": b}, doc["features"], version)
        return {"a": a, "b": b, "stale": out["stale"], "relation": out["relation"],
                **({"error": out["error"]} if "error" in out else {})}

    def rename(self, pid: str, sid: str, item_id: str, name: str) -> dict:
        """``PATCH .../features/{id}``: a new name for a feature, measurement or relation."""
        new = self._name(name, "")
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            key, i = self._find(doc, item_id)
            doc[key][i] = {**doc[key][i], "name": new}
            saved = self._save(pid, sid, doc)
            item = saved[key][i]
        version = self._version(pid, sid)
        return self._relation_view(item, saved["features"], version) if key == "relations" else self._view(item, version)

    def delete(self, pid: str, sid: str, item_id: str) -> None:
        """``DELETE .../features/{id}``: forget an item; a feature takes its relations, region and outline with it."""
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            key, i = self._find(doc, item_id)
            del doc[key][i]
            if key == "features":
                doc["relations"] = [r for r in doc["relations"] if item_id not in (r["a"], r["b"])]
            self._save(pid, sid, doc)
            if key == "features":
                kf.remove_faces(self.st.projects.scan_dir(pid, sid), item_id)
                kf.remove_outline(self.st.projects.scan_dir(pid, sid), item_id)

    def faces(self, pid: str, sid: str, fid: str) -> dict:
        """``GET .../features/{id}/faces``: a feature's region, for the mesh version it was fitted on."""
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            feat = next((f for f in doc["features"] if f["id"] == fid), None)
            faces = None if feat is None else kf.load_faces(self.st.projects.scan_dir(pid, sid), fid)
        if feat is None or faces is None:
            raise ApiError(404, f"no kept region for {fid!r}", "read the list again (GET .../features)")
        return {"id": fid, "mesh_version": int(feat["mesh_version"]), "n_faces": int(len(faces)),
                "faces": base64.b64encode(faces.astype("<u4").tobytes()).decode("ascii")}

    def outline(self, pid: str, sid: str, fid: str) -> dict:
        """``GET .../features/{id}/outline``: a feature's whole outline, for the mesh version it was
        fitted on: a plane's loops (2D in its basis ``origin``, ``u``, ``v``, as ``region_extent``
        gives them) or a hole's edge (3D). 404 for a cylinder (it has no outline; its span and arc are
        in ``extent``), and for a fit kept before extents existed (fit it again)."""
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            feat = next((f for f in doc["features"] if f["id"] == fid), None)
            out = None if feat is None else kf.load_outline(self.st.projects.scan_dir(pid, sid), fid)
        if out is not None and out["mesh_version"] != feat["mesh_version"]:
            out = None  # left over from another fit of it (a write that did not finish): not this fit's
        if feat is None:
            raise ApiError(404, f"no kept fit {fid!r}", "read the list again (GET .../features)")
        ext = feat.get("extent")
        if out is None and ext is not None and ext.get("kind") == "hole" and ext.get("edge") is not None:
            out = {"kind": "hole", "edge": ext["edge"]}  # a short edge is kept in features.json itself
        if out is None:
            why = ("a cylinder has no outline: its span and arc are in its extent" if ext is not None
                   else "it was kept before outlines were measured")
            raise ApiError(404, f"no outline kept for {fid!r}: {why}",
                           "fit it again (POST .../features/refit)" if ext is None else "read its extent instead")
        out.pop("mesh_version", None)
        return {"id": fid, "mesh_version": int(feat["mesh_version"]), **out}

    def refit(self, pid: str, sid: str, ids: list[str] | None = None) -> dict:
        """``POST .../features/refit``: fit the stale features (all, or those of ``ids``) again on the
        current mesh, from their seeds, and those kept before extents existed (``extent`` null): a
        current one of those from its own click, so its shape stays the same and it gains its
        extent. Returns the set as ``GET`` does, which ones were refitted and which could not be
        (with the reason); those stay as they were, still marked."""
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
        if ids is not None:
            known = {f["id"] for f in doc["features"]}
            for i in ids:
                if i not in known:
                    raise ApiError(404, f"no kept fit {i!r}", "read the list again (GET .../features)")
        version = self._version(pid, sid)
        if version is None:
            raise ApiError(409, "this scan has no mesh yet", "run Track (or Make mesh) first")
        todo = [f for f in doc["features"] if (f["mesh_version"] != version or f.get("extent") is None)
                and (ids is None or f["id"] in ids)]
        done: dict[str, tuple[dict, dict]] = {}
        failed: list[dict] = []
        if todo:
            V, F, Fl, v = self._mesh(pid, sid)
            C = V[F].mean(axis=1)
            measured = np.flatnonzero(~Fl) if Fl is not None and len(Fl) == len(F) else None
            for f in todo:
                seed = f["seed"]
                p = np.asarray(seed["point"], dtype=np.float64)
                if f["mesh_version"] == v and 0 <= int(seed["face"]) < len(F):
                    face = int(seed["face"])  # not stale (only its extent is missing): its own click
                elif measured is not None and len(measured):
                    face = int(measured[np.argmin(np.einsum("ij,ij->i", C[measured] - p, C[measured] - p))])
                elif len(C):
                    face = int(np.argmin(np.einsum("ij,ij->i", C - p, C - p)))
                else:
                    face = None
                gone = self._gone(V, F, C, face, p)
                if gone is not None:
                    failed.append({"id": f["id"], "detail": gone,
                                   "hint": "click the feature again on this mesh, or delete the kept fit"})
                    continue
                try:
                    fit = self.st.processing.fit(pid, sid, f["kind"], face, seed["point"], seed["tolerance_mm"], v,
                                                 with_extent=True, with_form=True)
                except ApiError as e:
                    failed.append({"id": f["id"], "detail": e.detail, "hint": e.hint})
                    continue
                done[f["id"]] = (f, {**fit, "face": face})
        refitted: list[str] = []
        d = self.st.projects.scan_dir(pid, sid)
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            for i, cur in enumerate(doc["features"]):
                hit = done.get(cur["id"])
                if hit is None:
                    continue
                before, fit = hit
                if cur != before:  # changed meanwhile (renamed, refitted): kept as it is now
                    continue
                seed = {**cur["seed"], "face": fit["face"]}
                rec, side = self._record(fit, seed, cur["id"], cur["name"], cur["created"])
                doc["features"][i] = {**cur, **rec}
                self._keep_files(d, cur["id"], fit, side)
                refitted.append(cur["id"])
            if refitted:
                self._save(pid, sid, doc)
        return {"features": self._set_view(doc, self._version(pid, sid)), "refitted": refitted, "failed": failed}

    # ------------------------------------------------------------------ the part's frame (K3)
    def build_frame(self, pid: str, sid: str, primary: str, secondary: str, origin: Any, axes: str = "ZX",
                    flip_primary: bool = False, flip_secondary: bool = False, secondary_zero: bool = False) -> dict:
        """``POST .../features/frame``: build the part's frame from three kept fits and keep it.

        ``primary`` and ``secondary`` are kept fit ids; ``origin`` is one too, or a point
        ``{"point": [x, y, z], "sigma_mm": s | None}`` in the scan's frame. Each fit goes to
        :func:`~ohs.measure.frame.part_frame` as its id, name and shape. Replaces a frame kept
        before. Returns the frame as ``GET`` shows it. 422 for an unknown fit or a refusal of the
        engine (its message as the detail), 409 before there is a mesh and for a fit made on an
        earlier mesh (its numbers are not this mesh's)."""
        from ohs.measure.frame import part_frame

        self._dir(pid, sid)
        version = self._version(pid, sid)
        if version is None:
            raise ApiError(409, "this scan has no mesh yet", "run Track (or Make mesh), fit the features, then build the frame")
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            by_id = {f["id"]: f for f in doc["features"]}
            slots = []
            for role, ref in (("primary", primary), ("secondary", secondary), ("origin", origin)):
                if isinstance(ref, dict):  # a point as the origin
                    point = {"kind": "point", "point": [float(x) for x in ref["point"]]}
                    if ref.get("sigma_mm") is not None:
                        point["sigma_mm"] = float(ref["sigma_mm"])
                    slots.append(point)
                    continue
                f = by_id.get(ref)
                if f is None:
                    raise ApiError(422, f"no kept fit {ref!r} for the {role}",
                                   "pick one of the kept fits (GET .../features), or keep the fit first")
                if f["mesh_version"] != version:
                    raise ApiError(409, f"the {role} \"{f['name']}\" was fitted on an earlier mesh (version "
                                        f"{f['mesh_version']}, the mesh is now version {version})",
                                   "fit it again first (POST .../features/refit), then build the frame")
                slots.append({"id": f["id"], "name": f["name"], "fit": f["shape"]})
            try:
                frame = part_frame(*slots, axes=axes, flip_primary=flip_primary, flip_secondary=flip_secondary,
                                   secondary_zero=secondary_zero)
            except ValueError as e:
                raise ApiError(422, str(e), "pick other fits for the slots, or change the options") from None
            doc["frame"] = frame.to_dict()
            saved = self._save(pid, sid, doc)
        return self._frame_view(saved["frame"], saved["features"])

    def remove_frame(self, pid: str, sid: str) -> None:
        """``DELETE .../features/frame``: forget the part's frame (nothing happens without one)."""
        with self._lock(pid, sid):
            doc = self._load(pid, sid)
            if doc["frame"] is not None:
                doc["frame"] = None
                self._save(pid, sid, doc)

    def frame_mesh(self, pid: str, sid: str, name: str) -> tuple[bytes, str, int | None]:
        """``GET .../features/frame/mesh.ply`` (or ``mesh.stl``): the scan's current result mesh
        (``result/mesh.ply``) with its vertices and normals turned into the part's frame
        (:meth:`PartFrame.mesh_to_part <ohs.measure.frame.PartFrame.mesh_to_part>`); the PLY keeps
        each vertex's sigma and each face's filled flag. Made on request in a temporary folder: the
        scan's own files are not touched. Returns the file's bytes, its download name and the mesh
        version read before it (the file is that version or a newer one). 404 without a frame or a
        mesh, 409 for a frame this version cannot read. An out-of-date frame is used as it is (the
        viewer says so)."""
        from ohs.measure.frame import PartFrame
        from ohs.track.export import load_ply, save_mesh, write_stl

        if name not in ("mesh.ply", "mesh.stl"):
            raise ApiError(404, f"no file {name!r} in the part's frame", "available: mesh.ply, mesh.stl")
        with self._lock(pid, sid):
            frame = self._load(pid, sid)["frame"]
        if frame is None:
            raise ApiError(404, "this scan has no part's frame", "build one first (POST .../features/frame)")
        try:
            pf = PartFrame.from_dict(frame)
        except (ValueError, TypeError, AttributeError, KeyError) as e:
            raise ApiError(409, f"this version cannot read the part's frame: {e}",
                           "build the frame again, or remove it") from None
        version = self._version(pid, sid)
        path, _ = self.st.projects.file_path(pid, sid, "mesh.ply")  # 404 before there is a mesh
        m = load_ply(path)
        V, N = pf.mesh_to_part(m["vertices"], m["normals"])
        F = m["faces"] if m["faces"] is not None else np.zeros((0, 3), dtype=np.int64)
        with tempfile.TemporaryDirectory(prefix="ohs-frame-") as tmp:
            out = Path(tmp) / name
            if name == "mesh.stl":
                write_stl(out, V, F)
            else:
                save_mesh(out, V, F, N, quality=m["quality"], filled=m["filled"])
            data = out.read_bytes()
        return data, f"mesh-part-frame.{name.rsplit('.', 1)[1]}", version

    @staticmethod
    def _gone(V: np.ndarray, F: np.ndarray, C: np.ndarray, face: int | None, p: np.ndarray) -> str | None:
        """Why a saved click cannot be fitted again, when the surface under it is gone: the nearest
        measured face is farther from the clicked point than :data:`GONE_MIN_MM` and than
        :data:`GONE_EDGES` of its own longest edge (on a coarse mesh the face centres lie far
        apart). None when the surface is still there. Without this a fit whose region was cut away
        would be fitted again on whatever surface is nearest, under the same name."""
        if face is None:
            return "the mesh has no measured surface to fit on"
        dist = float(np.linalg.norm(C[face] - p))
        tri = V[F[face]].astype(np.float64)
        edge = float(np.linalg.norm(tri - tri[[1, 2, 0]], axis=1).max())
        if dist <= max(GONE_MIN_MM, GONE_EDGES * edge):
            return None
        return (f"the surface under this fit is gone: the nearest measured surface is {dist:.1f} mm from where "
                "it was clicked")

    def _mesh(self, pid: str, sid: str) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, int]:
        """The current mesh's vertices, faces, filled flags and version: the arrays the fit route
        keeps per mesh version (:meth:`ProcessManager.mesh_arrays
        <ohs.server.processing.ProcessManager.mesh_arrays>`, loaded once, shared with it)."""
        v, V, F, _, Fl = self.st.processing.mesh_arrays(pid, sid)
        return V, F, Fl, v
