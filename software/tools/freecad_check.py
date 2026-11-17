"""What FreeCAD reads from a STEP file, as JSON (FEATURE_PLAN C1, D3). Runs inside FreeCAD's
command-line program, not in our Python:

    set OHS_STEP_IN=<file.step>
    set OHS_CHECK_OUT=<report.json>      (optional: else the JSON is printed between two marker lines)
    FreeCADCmd.exe tools/freecad_check.py

(the file may also be given after ``--pass``: ``FreeCADCmd.exe tools/freecad_check.py --pass
<file.step> [<report.json>]``). FreeCADCmd runs the script as a module, not as ``__main__``, and
answers 0 even when the script fails, so the report is the result: ``{"file", "freecad", "occ",
"error", "shapes": [...]}``, ``error`` null when it read the file. Each shape is one object of the
document FreeCAD's STEP import makes (``Import.insert``, as File > Import does), with:

* ``name`` (its label), ``type`` (its FreeCAD type), ``valid`` (``Shape.isValid()``),
  ``bound_box`` (``[[xmin, ymin, zmin], [xmax, ymax, zmax]]``);
* ``faces``: each face's ``surface`` type, with a plane's ``position`` and ``normal``, a
  cylinder's ``center``, ``axis`` and ``radius``, and its parameter range ``bounds``
  (``[umin, umax, vmin, vmax]``) and ``area``;
* ``edges``: each edge's ``curve`` type, its ``start`` and ``end`` points (at its first and last
  parameter: a circle read from a curve set has no vertices), ``length`` and ``n_vertices``, with a
  circle's ``center``, ``normal`` and ``radius``, a line's ``direction``;
* ``vertices``: the points of a shape that is only points.

Containers (an assembly's part) and objects with no shape are listed with ``faces`` and ``edges``
empty; the origin FreeCAD adds to every part (its axes and planes) is left out. Also ``whole``: the file read as one shape (``Part.read``), valid or not, its counts.
"""
import json
import os
import sys
import traceback

# the origin FreeCAD gives every App::Part (its axes and planes): not from the file, left out of the report
_ORIGIN_TYPES = ("App::Origin", "App::Line", "App::Plane", "App::Point")


def _v(p):
    return [float(p.x), float(p.y), float(p.z)]


def _face(f):
    s = f.Surface
    out = {"surface": type(s).__name__, "bounds": [float(x) for x in f.ParameterRange], "area": float(f.Area)}
    if out["surface"] == "Plane":
        out["position"], out["normal"] = _v(s.Position), _v(s.Axis)
    elif out["surface"] == "Cylinder":
        out["center"], out["axis"], out["radius"] = _v(s.Center), _v(s.Axis), float(s.Radius)
    return out


def _edge(e):
    c = e.Curve
    out = {"curve": type(c).__name__, "start": _v(e.valueAt(e.FirstParameter)), "end": _v(e.valueAt(e.LastParameter)),
           "length": float(e.Length), "n_vertices": len(e.Vertexes)}
    if out["curve"] == "Circle":
        out["center"], out["normal"], out["radius"] = _v(c.Center), _v(c.Axis), float(c.Radius)
    elif out["curve"] == "Line":
        out["direction"] = _v(c.Direction)
    return out


def _shape(obj):
    out = {"name": obj.Label, "type": obj.TypeId, "valid": None, "bound_box": None, "faces": [], "edges": [],
           "vertices": []}
    shape = getattr(obj, "Shape", None)
    if obj.TypeId in ("App::Part", "App::LinkGroup") or shape is None or shape.isNull():
        return out
    bb = shape.BoundBox
    out["valid"] = bool(shape.isValid())
    out["bound_box"] = [[bb.XMin, bb.YMin, bb.ZMin], [bb.XMax, bb.YMax, bb.ZMax]]
    out["faces"] = [_face(f) for f in shape.Faces]
    out["edges"] = [_edge(e) for e in shape.Edges]
    if not shape.Edges:
        out["vertices"] = [_v(v.Point) for v in shape.Vertexes]
    return out


def check(path):
    import FreeCAD
    import Import
    import Part

    report = {"file": path, "freecad": ".".join(FreeCAD.Version()[:3]), "occ": Part.OCC_VERSION, "error": None,
              "shapes": [], "whole": None}
    try:
        whole = Part.read(path)
        if not whole.isNull():  # counts only: each object's validity is checked below (a check costs seconds
            # on a face of thousands of edges)
            report["whole"] = {"faces": len(whole.Faces), "edges": len(whole.Edges), "vertices": len(whole.Vertexes)}
        doc = FreeCAD.newDocument("check")
        Import.insert(path, doc.Name)
        report["shapes"] = [_shape(o) for o in doc.Objects if o.TypeId not in _ORIGIN_TYPES]
        FreeCAD.closeDocument(doc.Name)
    except Exception:  # the report says what went wrong
        report["error"] = traceback.format_exc()
    return report


def main():
    args = sys.argv[sys.argv.index("--pass") + 1:] if "--pass" in sys.argv else []
    path = os.environ.get("OHS_STEP_IN") or (args[0] if args else None)
    out = os.environ.get("OHS_CHECK_OUT") or (args[1] if len(args) > 1 else None)
    if not path:
        print("freecad_check.py: give the STEP file in OHS_STEP_IN or after --pass")
        return
    report = check(path)
    text = json.dumps(report, indent=1, ensure_ascii=False)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print("----- freecad_check -----")
        print(text)
        print("----- end -----")


main()
