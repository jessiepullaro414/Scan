"""The kept fits for CAD over the API (FEATURE_PLAN C2; APP_API.md "Kept features", "For CAD").

Three read-only routes under ``/api/projects/{pid}/scans/{sid}/features/cad``:

* ``GET .../features/cad``: what would be exported (:meth:`CadExport.summary`): how many fits of
  each kind, in which frame, the writer's notes and the warnings, the files' sizes and names, so
  the viewer can show it before the download;
* ``GET .../features/cad/fits.step``: the STEP file (:func:`ohs.cad.step.step_text`);
* ``GET .../features/cad/fits.FCMacro``: the FreeCAD macro (:func:`ohs.cad.fcmacro.macro_text`).

All three take ``boundary`` (``rectangle``, the default, or ``outline``: each plane bounded by its
real outline, :func:`ohs.cad.shapes.cad_shapes`). The shapes are made from the scan's kept fits as
``features.json`` holds them (every fit, in its order), each plane's stored outline (only one kept
for the fit's own mesh version) and the part's frame when the scan has one this version can read.
The files are named after the part (the project's name) inside, and after the scan outside (their
download names), so the STEP file and the macro of two scans cannot be mistaken for each other.

**Kept as kept.** The measured values are exported (approved nominal sizes are a later task). A
fit made on an earlier mesh is exported too, it is what the user kept, and the summary says so; an
out-of-date frame is used as it is (as the mesh in the part's frame is), and said so; a frame this
version cannot read is not used, and said so. A scan with no kept fits answers 409 with a clear
message for the files (never an empty file), and a summary with ``reason``.

**Nothing on disk changes:** the files are made in memory on each request from what is read (the
writers give text; no temporary file is needed) and nothing is written.
"""
from __future__ import annotations

import re
from typing import Any

from ohs.server.protocol import ApiError

__all__ = ["CadExport", "CAD_FILES", "BOUNDARIES"]

#: the files, by their name in the route: (their kind, the download name's ending, the media type)
CAD_FILES = {"fits.step": ("step", "-fits.step", "model/step"),
             "fits.FCMacro": ("macro", "-fits.FCMacro", "text/x-python")}
BOUNDARIES = ("rectangle", "outline")
_NO_FITS = "this scan has no kept fits to export"
_NO_FITS_HINT = ("fit planes, cylinders or holes in the viewer first (each fit is kept with the scan), "
                 "then download them for CAD")


def _file_stem(name: str) -> str:
    """A scan's name as the start of a download name: letters, digits, ``.``, ``-`` and ``_``."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name)).strip("._-")
    return stem[:80] or "scan"


class CadExport:
    """The CAD routes of every scan, on the kept features of the
    :class:`~ohs.server.features.FeatureManager` ``feats`` (read with its
    :meth:`~ohs.server.features.FeatureManager.snapshot`)."""

    def __init__(self, feats: Any) -> None:
        self.feats = feats
        self.st = feats.st

    def _read(self, pid: str, sid: str, boundary: str) -> dict:
        """The scan's kept fits as shapes to export, with what the summary says of them."""
        import ohs.measure.features as kf
        from ohs.cad.shapes import cad_shapes

        if boundary not in BOUNDARIES:
            raise ApiError(422, f"boundary must be 'rectangle' or 'outline', got {boundary!r}",
                           "leave it out for each plane's rectangle, or give 'outline' for its real outline")
        project = self.st.projects.get(pid)  # 404 for an unknown project
        scan = self.st.projects.scan_entry(pid, sid)  # ... or scan
        doc, outlines, version = self.feats.snapshot(pid, sid, plane_outlines=boundary == "outline")
        features = doc["features"]
        warnings = []
        stale = [f for f in features if version is None or f["mesh_version"] != version]
        for f in stale:
            warnings.append(f"\"{f['name']}\" was fitted on an earlier mesh (version {f['mesh_version']}"
                            + (f", the mesh is now version {version}" if version is not None else "")
                            + "): exported as it was kept; fit it again for this mesh's numbers")
        frame = None
        state = kf.frame_state(doc["frame"], features)
        if state is not None and not state["readable"]:
            warnings.append(f"{state['out_of_date_reasons'][0]}: the shapes are in the scan's own frame")
        elif state is not None:
            frame = doc["frame"]
            if state["out_of_date"]:
                warnings.append("the part's frame is out of date (" + "; ".join(state["out_of_date_reasons"])
                                + "): used as it is; build it again for the fits as they are now")
        shapes = None
        if features:
            try:
                shapes = cad_shapes(features, outlines=outlines, frame=frame, boundary=boundary)
            except ValueError as e:
                raise ApiError(409, f"the kept fits cannot be exported: {e}",
                               "fit that fit again, or delete it, then download again") from None
        return {"project": project, "scan": scan, "features": features, "outlines": outlines, "shapes": shapes,
                "boundary": boundary, "version": version, "stale": [f["id"] for f in stale], "warnings": warnings,
                "frame": frame}

    @staticmethod
    def _texts(r: dict) -> tuple[str, str]:
        from ohs.cad.fcmacro import macro_text
        from ohs.cad.step import step_text

        title = r["project"]["name"]
        return step_text(r["shapes"], title=title), macro_text(r["shapes"], title=title)

    @staticmethod
    def _filenames(scan: dict) -> dict:
        stem = _file_stem(scan.get("name") or scan.get("id"))
        return {kind: stem + end for kind, end, _ in CAD_FILES.values()}

    # ------------------------------------------------------------------ routes
    def summary(self, pid: str, sid: str, boundary: str = "rectangle") -> dict:
        """``GET .../features/cad``: what the two files would hold.

        ``{"boundary", "n_fits", "counts": {"plane", "cylinder", "hole", "point"}, "frame": "part" |
        "scan", "frame_label", "notes" (the writer's), "warnings" (fits made on an earlier mesh, a
        frame out of date or not readable), "stale" (those fits' ids), "outline" (with
        ``boundary=outline``: how many planes, points and holes the faces hold and how many of the
        outlines' holes were left out; else null), "step_bytes", "macro_bytes", "filenames":
        {"step", "macro"}, "mesh_version", "reason"}``: ``reason`` says why nothing can be exported
        (no kept fits; then the counts are 0 and the sizes null), else null."""
        from ohs.cad.shapes import CylinderFace, HoleCircle, PlaneFace, PointMark

        r = self._read(pid, sid, boundary)
        shapes = r["shapes"]
        counts = {"plane": 0, "cylinder": 0, "hole": 0, "point": 0}
        out = {"boundary": boundary, "n_fits": len(r["features"]), "counts": counts,
               "frame": "part" if r["frame"] is not None else "scan",
               "frame_label": "the part's frame" if r["frame"] is not None else "the scan's own frame",
               "notes": [], "warnings": r["warnings"], "stale": r["stale"], "outline": None, "step_bytes": None,
               "macro_bytes": None, "filenames": self._filenames(r["scan"]), "mesh_version": r["version"],
               "reason": None}
        if shapes is None:
            out["reason"] = f"{_NO_FITS}: {_NO_FITS_HINT}"
            return out
        kind = {PlaneFace: "plane", CylinderFace: "cylinder", HoleCircle: "hole", PointMark: "point"}
        for s in shapes.shapes:
            counts[kind[type(s)]] += 1
        out["notes"] = list(shapes.notes)
        if boundary == "outline":
            planes = [s for s in shapes.shapes if isinstance(s, PlaneFace) and s.id in r["outlines"]]
            kept = sum(len(s.holes) for s in planes)
            out["outline"] = {"planes": len(planes),
                              "points": sum(len(s.outer) + sum(len(h) for h in s.holes) for s in planes),
                              "holes_kept": kept,
                              "holes_left_out": sum(len(r["outlines"][s.id]["holes"]) for s in planes) - kept}
        step, macro = self._texts(r)
        out["step_bytes"], out["macro_bytes"] = len(step), len(macro)
        return out

    def file(self, pid: str, sid: str, name: str, boundary: str = "rectangle") -> tuple[bytes, str, str, int | None]:
        """``GET .../features/cad/{name}`` (``fits.step`` or ``fits.FCMacro``): the file's bytes, its
        download name, its media type and the mesh version read with the kept fits. 404 for another
        name, 409 when the scan has no kept fits (or one that cannot be exported)."""
        if name not in CAD_FILES:
            raise ApiError(404, f"no CAD file {name!r}", "available: " + ", ".join(CAD_FILES))
        r = self._read(pid, sid, boundary)
        if r["shapes"] is None:
            raise ApiError(409, _NO_FITS, _NO_FITS_HINT)
        kind, _, media = CAD_FILES[name]
        step, macro = self._texts(r)
        text = step if kind == "step" else macro
        return text.encode("ascii"), self._filenames(r["scan"])[kind], media, r["version"]
