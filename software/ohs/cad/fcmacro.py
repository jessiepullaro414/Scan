"""The fitted shapes as a FreeCAD macro (FEATURE_PLAN C2): a short Python text for FreeCAD's ``Part``
workbench that builds the same shapes as the STEP file (:mod:`ohs.cad.step`), as named objects.

It takes what :func:`ohs.cad.shapes.cad_shapes` gives, as the STEP writer does, and writes a
``.FCMacro``: run in FreeCAD (Macro > Macros..., or open it and press "Execute macro") it makes a
new document named after the part, holding:

* each plane as a flat face with its boundary (its rectangle or its outline, with the holes kept
  in it), in the group "Planes";
* each cylinder as a cylindrical face over its measured span and arc, in "Cylinders";
* each hole as a circle, in "Holes";
* an axis line for every cylinder and hole, "<name> axis", in "Axes";
* each point as a vertex, in "Points";
* with the part's frame, its "X axis", "Y axis" and "Z axis" lines and its "Part origin", in
  "Part frame" (unlike FreeCAD's STEP import, the macro shows the origin as a point).

Each object is a ``Part::Feature`` labelled with the fit's name. **The names are the STEP file's**
(a name already taken gets " (2)", " (3)", ..., as :class:`ohs.cad.step._Names` gives them), with
one more rule: the groups' labels are taken too, so a fit named "Planes" is "Planes (2)" here (in
FreeCAD two objects cannot share a label).

**The geometry is the STEP file's**, number for number: each surface and circle is FreeCAD's own
(``Part.Plane``, ``Part.Cylinder``, ``Part.Circle``), made at the origin and moved by one matrix to
the fit's point with the same directions as the STEP file's placements (a plane's ``ref``, a
cylinder's ``zero``, a hole's circle starting where the STEP file's does), so FreeCAD reports the
same position, axis, parameter range and edges for both (``tests/test_cad_fcmacro.py`` compares
them to 1e-6 mm). A plane's face is its plane bounded by the outer loop, with the holes cut in it;
a cylinder's is its surface between the two angles and the two heights.

**Text and code.** The macro is plain ASCII, the same shapes give the same bytes, and it reads from
the top: the notes (:attr:`~ohs.cad.shapes.CadShapes.notes`) as comments, a few small functions
that build and group the objects, then one call per object with its numbers. A name is data: every
name (and the part's title) is written as a Python string literal by ``ascii()``, which escapes
quotes, backslashes, line ends and every character outside ASCII, so it reads back as exactly that
text and can never end the literal and run as code. Comments carry text the same way
(:func:`comment_text`): one line of printable ASCII, everything else escaped, so a note cannot run
on into a line of code. Numbers are Python's shortest ``repr`` of each float (it reads back as the
same float).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ohs.cad.shapes import CadShapes, CylinderFace, HoleCircle, PlaneFace, PointMark, _square_to
from ohs.cad.step import SYSTEM, _Names

__all__ = ["macro_text", "write_macro", "comment_text", "GROUPS"]

#: the groups the objects go into, by what they are
GROUPS = {"plane": "Planes", "cylinder": "Cylinders", "hole": "Holes", "axis": "Axes", "point": "Points",
          "frame": "Part frame"}
POINTS_PER_LINE = 4  # a loop's points written this many to a line
_GROUP_CONSTANTS = (("PLANES", "plane"), ("CYLINDERS", "cylinder"), ("HOLES", "hole"), ("AXES", "axis"),
                    ("POINTS", "point"), ("FRAME", "frame"))  # the macro's names for the groups' labels

# The fixed part of the macro: the functions the calls below it use. Plain text, no data in it.
_CODE = '''\
import math

import FreeCAD as App
import Part


def _vec(p):
    return App.Vector(*p)


def _placed(geometry, origin, x, z):
    """A surface or circle made at the origin on the Z axis, moved so its own X runs along x and its Z
    along z, from origin (the placement the STEP file gives it)."""
    y = (z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0])
    geometry.transform(App.Matrix(x[0], y[0], z[0], origin[0], x[1], y[1], z[1], origin[1],
                                  x[2], y[2], z[2], origin[2], 0, 0, 0, 1))
    return geometry


def _loop(points):
    return Part.makePolygon([_vec(p) for p in points] + [_vec(points[0])])


doc = App.newDocument(TITLE)
doc.Label = TITLE
_groups = {}


def _add(group, label, shape):
    """One object labelled ``label`` holding ``shape``, in the group ``group`` (made when first used)."""
    if group not in _groups:
        _groups[group] = doc.addObject("App::DocumentObjectGroup", "Group")
        _groups[group].Label = group
    obj = doc.addObject("Part::Feature", "Shape")
    obj.Label = label
    obj.Shape = shape
    _groups[group].addObject(obj)
    return obj


def plane(label, point, normal, ref, outer, holes=()):
    """A flat face: the plane through point with normal (ref a direction in it), bounded by the loop
    outer (counter-clockwise seen from the normal's side), the loops holes cut out of it."""
    face = Part.Face(_placed(Part.Plane(), point, ref, normal), _loop(outer))
    if holes:
        face.cutHoles([_loop(h) for h in holes])
    return _add(GROUP_PLANES, label, face)


def cylinder(label, point, axis, zero, radius, along, start_deg, arc_deg):
    """A cylindrical face of radius round the axis through point, from along[0] to along[1] mm along
    it, over the arc from start_deg through arc_deg (angles from zero, right-handed about the axis)."""
    surface = _placed(Part.Cylinder(), point, zero, axis)
    surface.Radius = radius
    start = math.radians(start_deg)
    return _add(GROUP_CYLINDERS, label, surface.toShape(start, start + math.radians(arc_deg), along[0], along[1]))


def hole(label, center, normal, start, radius):
    """A hole's circle round center, square to normal, starting at center + radius * start."""
    circle = _placed(Part.Circle(), center, start, normal)
    circle.Radius = radius
    return _add(GROUP_HOLES, label, circle.toShape())


def _line(group, label, start, end):
    return _add(group, label, Part.LineSegment(_vec(start), _vec(end)).toShape())


def axis(label, start, end):
    """A cylinder's or a hole's axis, from start to end."""
    return _line(GROUP_AXES, label, start, end)


def point(label, p):
    return _add(GROUP_POINTS, label, Part.Vertex(_vec(p)))


def part_axis(label, start, end):
    """One of the part's axes, from its origin."""
    return _line(GROUP_FRAME, label, start, end)


def part_origin(label, p):
    return _add(GROUP_FRAME, label, Part.Vertex(_vec(p)))


'''

_END = '''
doc.recompute()
if App.GuiUp:
    import FreeCADGui

    FreeCADGui.SendMsgToActiveView("ViewFit")
'''


def comment_text(text: str) -> str:
    """``text`` as it is written in a comment: one line of printable ASCII. A character outside it
    (a line end, a tab, ``Ø``) is written as its Python escape (``\\n``, ``\\t``, ``\\xd8``), so
    nothing of the text can end the comment."""
    out = []
    for ch in str(text):
        o = ord(ch)
        if 0x20 <= o < 0x7F:
            out.append(ch)
        else:
            out.append(ch.encode("unicode_escape").decode("ascii"))
    return "".join(out)


def _num(x) -> str:
    x = float(x)
    if not np.isfinite(x):
        raise ValueError(f"a macro number must be finite, got {x!r}")
    return repr(x + 0.0)  # -0.0 as 0.0


def _vec(v) -> str:
    return "(" + ", ".join(_num(x) for x in np.asarray(v, dtype=np.float64).reshape(-1)) + ")"


def _points(P, indent: str) -> str:
    """A loop's points as a list, :data:`POINTS_PER_LINE` to a line."""
    rows = [_vec(p) for p in np.asarray(P, dtype=np.float64).reshape(-1, 3)]
    lines = [", ".join(rows[i: i + POINTS_PER_LINE]) for i in range(0, len(rows), POINTS_PER_LINE)]
    return "[\n" + "".join(f"{indent}    {ln},\n" for ln in lines) + f"{indent}]"


def _call(fn: str, label: str, **args: str) -> str:
    """One builder call: the label (a string literal), then each argument by its name."""
    return f"{fn}({ascii(label)}, {', '.join(f'{k}={v}' for k, v in args.items())})\n"


def _plane(s: PlaneFace, label: str) -> str:
    holes = ""
    if s.holes:
        holes = ",\n      holes=[\n" + "".join(f"          {_points(h, '          ')},\n" for h in s.holes) + "      ]"
    return (f"plane({ascii(label)}, point={_vec(s.point)}, normal={_vec(s.normal)}, ref={_vec(s.ref)},\n"
            f"      outer={_points(s.outer, '      ')}{holes})\n")


def macro_text(shapes: CadShapes, *, title: str = "Fitted shapes") -> str:
    """The FreeCAD macro of ``shapes`` (from :func:`ohs.cad.shapes.cad_shapes`) as text (module
    docstring). ``title`` names the part: the document FreeCAD makes is labelled with it."""
    where = "the part's frame" if shapes.in_part_frame else "the scan's own frame"
    head = [
        f"{SYSTEM}: fitted shapes for FreeCAD, in mm, in {where}.",
        f"Part: {title}",
        "Run it in FreeCAD (Macro > Macros..., or open it and press Execute macro): it makes a new",
        "document with one object per shape, labelled with the fit's name, in the groups",
        ", ".join(f'"{g}"' for g in GROUPS.values()) + ": the shapes of the STEP file exported with it.",
        "Every name below is a quoted string: text, never code.",
    ]
    lines = [f"# {comment_text(h)}" for h in head]
    if shapes.notes:
        lines += ["#", "# Notes:"] + [f"# - {comment_text(n)}" for n in shapes.notes]
    out = "\n".join(lines) + "\n\n"
    out += f"TITLE = {ascii(str(title))}\n"
    out += "".join(f"GROUP_{k} = {ascii(GROUPS[g])}\n" for k, g in _GROUP_CONSTANTS)
    out += "\n" + _CODE
    reserved = [title] + list(GROUPS.values())
    if shapes.axes is not None:
        reserved += [*shapes.axes.names, shapes.axes.origin_name]
    unique = _Names(reserved)
    body = []
    for s in shapes.shapes:
        own = unique(s.name)
        if isinstance(s, PlaneFace):
            body.append(_plane(s, own))
        elif isinstance(s, CylinderFace):
            body.append(_call("cylinder", own, point=_vec(s.point), axis=_vec(s.axis), zero=_vec(s.zero),
                              radius=_num(s.radius), along=f"({_num(s.along[0])}, {_num(s.along[1])})",
                              start_deg=_num(s.start_deg), arc_deg=_num(s.arc_deg)))
            body.append(_call("axis", unique(f"{own} axis"), start=_vec(s.point + s.along[0] * s.axis),
                              end=_vec(s.point + s.along[1] * s.axis)))
        elif isinstance(s, HoleCircle):
            body.append(_call("hole", own, center=_vec(s.center), normal=_vec(s.normal),
                              start=_vec(_square_to(s.normal)), radius=_num(s.radius)))
            body.append(_call("axis", unique(f"{own} axis"), start=_vec(s.center - s.axis_half_mm * s.normal),
                              end=_vec(s.center + s.axis_half_mm * s.normal)))
        elif isinstance(s, PointMark):
            body.append(_call("point", own, p=_vec(s.point)))
        else:
            raise ValueError(f"cannot write a {type(s).__name__} as a FreeCAD macro")
    if shapes.axes is not None:
        ax = shapes.axes
        for name, d in zip(ax.names, ax.axes):
            body.append(_call("part_axis", name, start=_vec(ax.origin), end=_vec(ax.origin + ax.length_mm * d)))
        body.append(_call("part_origin", ax.origin_name, p=_vec(ax.origin)))
    out += "# --- the shapes, in mm\n" + "".join(body) + _END
    return out


def write_macro(path, shapes: CadShapes, **kw) -> Path:
    """Write :func:`macro_text` to ``path`` (ASCII, ``\\n`` line ends); returns the path."""
    path = Path(path)
    path.write_bytes(macro_text(shapes, **kw).encode("ascii"))
    return path
