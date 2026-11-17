"""Scan to CAD: the fitted shapes of a scan written for CAD programs (docs/FEATURE_PLAN.md, C).

Fitted shapes, not "any mesh becomes CAD": the kept fits of a scan (planes, cylinders, holes),
in the part's own coordinate system when it has one, as a STEP file and a FreeCAD macro, with
suggested nominal sizes for an engineer to approve.

* :mod:`ohs.cad.shapes`: the kept fits as shapes to export;
* :mod:`ohs.cad.step`: a small STEP writer for those shapes;
* :mod:`ohs.cad.fcmacro`: the same shapes as a FreeCAD macro that builds them as named objects;
* :mod:`ohs.cad.nominal`: suggested nominal sizes;
* :mod:`ohs.cad.moves`: an approved relation moving one of its two shapes in the export, when chosen.
"""
