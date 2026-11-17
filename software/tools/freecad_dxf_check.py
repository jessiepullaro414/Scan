"""What FreeCAD reads from a DXF file, as JSON (FEATURE_PLAN I1). Runs inside FreeCAD's
command-line program, not in our Python:

    set OHS_DXF_IN=<file.dxf>
    set OHS_CHECK_OUT=<report.json>      (optional: else the JSON is printed between two marker lines)
    FreeCADCmd.exe tools/freecad_dxf_check.py

(the file may also be given after ``--pass``: ``FreeCADCmd.exe tools/freecad_dxf_check.py --pass
<file.dxf> [<report.json>]``). It uses FreeCAD's built-in DXF importer (``Import.readDXF``, the
one File > Import uses unless the legacy Python importer is switched on), with the user's own
import settings, unchanged. FreeCADCmd answers 0 even when the script fails, so the report is the
result: ``{"file", "freecad", "error", "stats", "objects": [...], "edges": [...]}``, ``error`` null
when it read the file.

* ``stats``: what the importer says about the file (its DXF version, the units it found and the
  scale it used, the entities it made);
* ``objects``: each shape it made, with its ``name``, ``layer`` (the layer group holding it),
  ``valid``, ``closed`` (one wire, closed) and the number of ``edges``;
* ``edges``: every edge of those shapes with its ``layer``, ``curve`` type, ``start`` and ``end``.
"""
import json
import os
import sys
import traceback


def _v(p):
    return [float(p.x), float(p.y), float(p.z)]


def check(path):
    import FreeCAD
    import Import

    report = {"file": path, "freecad": ".".join(FreeCAD.Version()[:3]), "error": None, "stats": None,
              "objects": [], "edges": []}
    try:
        doc = FreeCAD.newDocument("check")
        FreeCAD.setActiveDocument(doc.Name)
        stats = Import.readDXF(path)
        report["stats"] = json.loads(json.dumps(stats, default=str))
        doc.recompute()
        layer_of = {}
        for o in doc.Objects:
            if o.Name != "LayerContainer" and hasattr(o, "Group") and o.TypeId == "App::FeaturePython":
                for child in o.Group:
                    layer_of[child.Name] = o.Label
        for o in doc.Objects:
            shape = getattr(o, "Shape", None)
            if o.TypeId != "Part::Feature" or shape is None or shape.isNull():
                continue
            layer = layer_of.get(o.Name)
            wires = shape.Wires
            report["objects"].append({"name": o.Label, "layer": layer, "valid": bool(shape.isValid()),
                                      "closed": len(wires) == 1 and bool(wires[0].isClosed()),
                                      "edges": len(shape.Edges)})
            for e in shape.Edges:
                report["edges"].append({"layer": layer, "curve": type(e.Curve).__name__,
                                        "start": _v(e.valueAt(e.FirstParameter)), "end": _v(e.valueAt(e.LastParameter))})
        FreeCAD.closeDocument(doc.Name)
    except Exception:  # the report says what went wrong
        report["error"] = traceback.format_exc()
    return report


def main():
    args = sys.argv[sys.argv.index("--pass") + 1:] if "--pass" in sys.argv else []
    path = os.environ.get("OHS_DXF_IN") or (args[0] if args else None)
    out = os.environ.get("OHS_CHECK_OUT") or (args[1] if len(args) > 1 else None)
    if not path:
        print("freecad_dxf_check.py: give the DXF file in OHS_DXF_IN or after --pass")
        return
    text = json.dumps(check(path), indent=1, ensure_ascii=False)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print("----- freecad_dxf_check -----")
        print(text)
        print("----- end -----")


main()
