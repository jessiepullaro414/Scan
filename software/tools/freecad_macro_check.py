"""What a FreeCAD macro of fitted shapes builds, as JSON (FEATURE_PLAN C2). Runs inside FreeCAD's
command-line program, not in our Python:

    set OHS_MACRO_IN=<a.FCMacro>[;<b.FCMacro>...]      (several: separated by the path separator)
    set OHS_CHECK_OUT=<a.json>[;<b.json>...]           (one report for each macro)
    FreeCADCmd.exe tools/freecad_macro_check.py

Each macro (``ohs.cad.fcmacro``) is run as FreeCAD runs a macro, in a fresh namespace, and the
document it made is read: ``{"file", "freecad", "error", "document", "shapes": [...]}``,
``error`` null when the macro ran, ``document`` the document's label. Each shape is one object the
macro made (the groups themselves are left out), in the order it was made, in the form
``tools/freecad_check.py`` reports a STEP file's objects (``name``, ``type``, ``valid``,
``bound_box``, ``faces``, ``edges``, ``vertices``), with ``group``: the label of the group it is
in. So ``tests/test_cad_fcmacro.py`` can set the two reports side by side. The document is closed
afterwards and nothing is saved.
"""
import json
import os
import traceback


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


def _shape(obj, group):
    shape = obj.Shape
    bb = shape.BoundBox
    return {"name": obj.Label, "type": obj.TypeId, "group": group, "valid": bool(shape.isValid()),
            "bound_box": [[bb.XMin, bb.YMin, bb.ZMin], [bb.XMax, bb.YMax, bb.ZMax]],
            "faces": [_face(f) for f in shape.Faces], "edges": [_edge(e) for e in shape.Edges],
            "vertices": [] if shape.Edges else [_v(v.Point) for v in shape.Vertexes]}


def check(path):
    import FreeCAD

    report = {"file": path, "freecad": ".".join(FreeCAD.Version()[:3]), "error": None, "document": None, "shapes": []}
    before = set(FreeCAD.listDocuments())
    try:
        with open(path, encoding="utf-8") as fh:
            code = compile(fh.read(), path, "exec")
        exec(code, {"__name__": "__main__", "__file__": path})
        made = [d for name, d in FreeCAD.listDocuments().items() if name not in before]
        if len(made) != 1:
            raise RuntimeError(f"the macro made {len(made)} documents, not one")
        doc = made[0]
        report["document"] = doc.Label
        groups = {}
        for g in doc.Objects:
            if g.TypeId == "App::DocumentObjectGroup":
                for o in g.Group:
                    groups[o.Name] = g.Label
        report["shapes"] = [_shape(o, groups.get(o.Name)) for o in doc.Objects if o.TypeId == "Part::Feature"]
    except Exception:  # the report says what went wrong
        report["error"] = traceback.format_exc()
    finally:
        for name in set(FreeCAD.listDocuments()) - before:
            FreeCAD.closeDocument(name)
    return report


def main():
    macros = [p for p in os.environ.get("OHS_MACRO_IN", "").split(os.pathsep) if p]
    outs = [p for p in os.environ.get("OHS_CHECK_OUT", "").split(os.pathsep) if p]
    if not macros or len(outs) != len(macros):
        print("freecad_macro_check.py: give the macros in OHS_MACRO_IN and as many reports in OHS_CHECK_OUT")
        return
    for path, out in zip(macros, outs):
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(check(path), indent=1, ensure_ascii=False))


main()
