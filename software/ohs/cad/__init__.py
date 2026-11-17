"""Scan to CAD: the fitted shapes of a scan written for CAD programs (docs/FEATURE_PLAN.md, C).

Fitted shapes, not "any mesh becomes CAD": the kept fits of a scan (planes, cylinders, holes),
in the part's own coordinate system when it has one, as a STEP file and a FreeCAD macro, with
suggested nominal sizes for an engineer to approve.

* :mod:`ohs.cad.shapes`: the kept fits as shapes to export;
* :mod:`ohs.cad.step`: a small STEP writer for those shapes;
* :mod:`ohs.cad.nominal`: suggested nominal sizes.
"""
