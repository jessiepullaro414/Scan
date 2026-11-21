"""A small STEP writer for fitted shapes (FEATURE_PLAN C1): ISO 10303-21 text, in mm.

It writes what :func:`ohs.cad.shapes.cad_shapes` gives (flat faces, cylindrical faces, holes'
circles, points and the part's axes) and nothing else: no solids, no free-form surfaces. The
owner chose our own writer over OpenCascade (FEATURE_PLAN D2): these shapes are simple, the format
is text, and OpenCascade would add hundreds of MB to the exe.

**The schema: AP214** (``AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }``). It is the schema CAD
programs have read longest and most alike, and the one OpenCascade (FreeCAD) writes by default,
with this very schema line; it has all this file needs: faces bounded by edges on planes and
cylinders, wireframe edges and points, names, and an assembly of named parts. AP242 holds the same
entities but adds what is not used here (tessellation, PMI), and older readers are less sure to
take it; AP203 is about solids first.

**The structure** follows what OpenCascade writes, which CAD programs open as a matter of course:

* one root product, the part (``title``), whose representation holds only its origin placement;
* under it, one product per thing, named after the feature, joined by a
  ``NEXT_ASSEMBLY_USAGE_OCCURRENCE`` of the same name and placed where it is (an identity
  transformation): a CAD program shows each as a named part in its tree. A cylinder's or a hole's
  axis is a product of its own, "<name> axis", as are the part's "X axis", "Y axis", "Z axis"
  and "Part origin". Each product name is used once: a name already taken (two features of the
  same name, one named like the part's title, or "X axis" when the part's axes are written, which
  keep theirs) gets " (2)", " (3)", ... after it, the first that is free, compared without regard
  to case;
* each product's ``SHAPE_REPRESENTATION`` holds its origin and is related to what it is made of:
  a face is an ``ADVANCED_FACE`` on a ``PLANE`` or a ``CYLINDRICAL_SURFACE`` (in an
  ``OPEN_SHELL`` of a ``SHELL_BASED_SURFACE_MODEL``, in a
  ``MANIFOLD_SURFACE_SHAPE_REPRESENTATION``); a circle or a line is one ``EDGE_CURVE`` on a
  ``CIRCLE`` or a ``LINE`` between vertices (in a ``CONNECTED_EDGE_SET`` of an
  ``EDGE_BASED_WIREFRAME_MODEL``, in an ``EDGE_BASED_WIREFRAME_SHAPE_REPRESENTATION``); a point
  is a ``CARTESIAN_POINT`` in a ``GEOMETRIC_CURVE_SET`` (in a
  ``GEOMETRICALLY_BOUNDED_WIREFRAME_SHAPE_REPRESENTATION``);
* the face, edge or point carries the name too.

**What FreeCAD shows** (1.1.4, OpenCascade 7.8.1, File > Import with its default settings;
``tools/freecad_check.py``): each product as one object labelled with the product's name (non-ASCII
included; the item names are not used; without a product name it falls back to the occurrence's
name, then its id), the face products as faces and the edge products as edges, all valid. That is
why each product holds one thing: a product whose wireframe is two edge sets (a circle and its
axis) is left out, and one set of two edges that do not touch is shown but not valid. It also
leaves out products of geometric curves and points (``GEOMETRIC_CURVE_SET``: FreeCAD's own export
of a circle comes back without it too). So
a point is not shown in FreeCAD (the part's origin is where its three axes meet); FreeCAD's
"read as one shape" (``Part.read``) has it, and other programs read such points.

**Faces.** A plane's boundary is straight edges (``LINE``) between its points, the outer loop
counter-clockwise seen from the normal's side and the holes clockwise (``FACE_OUTER_BOUND``,
``FACE_BOUND``). A cylinder covering part of its round is bounded by two arcs of ``CIRCLE`` and two
lines along the axis. A full round has one loop with a seam: the bottom circle, the seam line up,
the top circle backwards, the seam line down (the seam edge used twice, once each way), as
OpenCascade writes a whole cylinder. The surface's placement is the fit's own point and direction,
so the numbers read back are the feature's.

**Numbers** are written as Python's shortest repr of each float (it reads back as the same
float), in STEP's form (``1.5E-07``, ``3.``). The same shapes give byte-identical files: nothing
depends on the time or on dict order, and the header's time stamp is empty unless one is given.

**Text** (names, notes) is escaped as ISO 10303-21 requires: ``'`` doubled, ``\\`` doubled, and
every character outside printable ASCII written as ``\\X2\\hhhh\\X0\\`` (``\\X4\\`` above
U+FFFF), so the file is plain ASCII. The notes of :func:`~ohs.cad.shapes.cad_shapes` go into the
header's ``FILE_DESCRIPTION``, and a shape's own note into its product's description.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from ohs.cad.shapes import CadShapes, CylinderFace, HoleCircle, PlaneFace, PointMark, _square_to

__all__ = ["SCHEMA", "step_text", "write_step", "step_string", "step_real"]

SCHEMA = "AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }"
UNCERTAINTY_MM = 1e-7  # the file's length uncertainty, as OpenCascade writes it
SYSTEM = "OpenHandScan"


# --------------------------------------------------------------------------- encoding
def step_real(x) -> str:
    """A float in STEP's form: the shortest text that reads back as the same float, with a
    decimal point and an upper-case exponent (``1.5E-07``, ``3.``, ``-0.25``)."""
    x = float(x)
    if not math.isfinite(x):
        raise ValueError(f"a STEP number must be finite, got {x!r}")
    if x == 0.0:
        return "0."
    s = repr(x)
    mant, _, exp = s.partition("e")
    if "." not in mant:
        mant += "."
    elif mant.endswith(".0"):
        mant = mant[:-1]
    return mant + ("E" + exp if exp else "")


def step_string(text: str) -> str:
    """Text as a STEP string literal, quotes included (module docstring, "Text")."""
    out = []
    wide: list[str] = []

    def flush():
        if wide:
            if any(len(h) == 8 for h in wide):
                out.append("\\X4\\" + "".join(h.rjust(8, "0") for h in wide) + "\\X0\\")
            else:
                out.append("\\X2\\" + "".join(wide) + "\\X0\\")
            wide.clear()

    for ch in str(text):
        o = ord(ch)
        if 0x20 <= o <= 0x7E:
            flush()
            out.append("''" if ch == "'" else "\\\\" if ch == "\\" else ch)
        else:
            h = f"{o:04X}" if o <= 0xFFFF else f"{o:08X}"
            if wide and (len(h) == 8) != (len(wide[0]) == 8):  # one run per width
                flush()
            wide.append(h)
    flush()
    return "'" + "".join(out) + "'"


# --------------------------------------------------------------------------- the writer
class _Data:
    """The DATA section, one entity per line, numbered in the order they are made."""

    def __init__(self):
        self.lines: list[str] = []

    def add(self, text: str) -> int:
        self.lines.append(text)
        return len(self.lines)

    # ----- geometry
    def point(self, p, name: str = "") -> int:
        return self.add(f"CARTESIAN_POINT({step_string(name)},({','.join(step_real(v) for v in p)}))")

    def direction(self, d) -> int:
        return self.add(f"DIRECTION('',({','.join(step_real(v) for v in d)}))")

    def placement(self, p, z, x) -> int:
        return self.add(f"AXIS2_PLACEMENT_3D('',#{self.point(p)},#{self.direction(z)},#{self.direction(x)})")

    def line(self, p, d) -> int:
        """A ``LINE`` through ``p`` along the unit ``d``: its parameter is mm from ``p``."""
        vec = self.add(f"VECTOR('',#{self.direction(d)},1.)")
        return self.add(f"LINE('',#{self.point(p)},#{vec})")

    def circle(self, c, n, x, r, name: str = "") -> int:
        return self.add(f"CIRCLE({step_string(name)},#{self.placement(c, n, x)},{step_real(r)})")

    # ----- topology
    def vertex(self, p) -> int:
        return self.add(f"VERTEX_POINT('',#{self.point(p)})")

    def edge(self, v0: int, v1: int, curve: int, name: str = "") -> int:
        return self.add(f"EDGE_CURVE({step_string(name)},#{v0},#{v1},#{curve},.T.)")

    def line_edge(self, a, b, name: str) -> int:
        """A straight edge from ``a`` to ``b`` (a wireframe line: an axis)."""
        a, b = np.asarray(a, float), np.asarray(b, float)
        d = (b - a) / np.linalg.norm(b - a)
        return self.edge(self.vertex(a), self.vertex(b), self.line(a, d), name)

    def circle_edge(self, c, n, x, r, name: str) -> int:
        """A whole circle as one edge, starting and ending at the one vertex ``c + r x``."""
        v = self.vertex(np.asarray(c, float) + r * np.asarray(x, float))
        return self.edge(v, v, self.circle(c, n, x, r), name)

    def loop(self, edges) -> int:
        """An ``EDGE_LOOP`` of ``(edge, forward)``."""
        oes = [self.add(f"ORIENTED_EDGE('',*,*,#{e},{'.T.' if fwd else '.F.'})") for e, fwd in edges]
        return self.add(f"EDGE_LOOP('',({','.join(f'#{i}' for i in oes)}))")

    def polygon(self, P) -> int:
        """A closed loop of straight edges through the points ``P`` (N,3), in order."""
        P = np.asarray(P, float)
        vs = [self.vertex(p) for p in P]
        edges = []
        for i in range(len(P)):
            j = (i + 1) % len(P)
            d = P[j] - P[i]
            edges.append((self.edge(vs[i], vs[j], self.line(P[i], d / np.linalg.norm(d))), True))
        return self.loop(edges)

    def text(self) -> str:
        return "".join(f"#{i}={t};\n" for i, t in enumerate(self.lines, start=1))


class _Names:
    """Product names, each used once: a name already taken (by an earlier feature, the part's
    title, or with a frame the part's axes "X axis", "Y axis", "Z axis" and "Part origin", which
    keep theirs) gets " (2)", " (3)", ... after it, the first that is free. Compared without regard
    to case, as a user reads them."""

    def __init__(self, reserved):
        self.used = {str(n).casefold() for n in reserved}

    def __call__(self, name: str) -> str:
        out, k = name, 1
        while out.casefold() in self.used:
            k += 1
            out = f"{name} ({k})"
        self.used.add(out.casefold())
        return out


def _plane_face(w: _Data, s: PlaneFace, name: str) -> int:
    bounds = [w.add(f"FACE_OUTER_BOUND('',#{w.polygon(s.outer)},.T.)")]
    bounds += [w.add(f"FACE_BOUND('',#{w.polygon(h)},.T.)") for h in s.holes]
    surface = w.add(f"PLANE('',#{w.placement(s.point, s.normal, s.ref)})")
    return w.add(f"ADVANCED_FACE({step_string(name)},({','.join(f'#{b}' for b in bounds)}),#{surface},.T.)")


def _cylinder_face(w: _Data, s: CylinderFace, name: str) -> int:
    a, x = s.axis, s.zero
    y = np.cross(a, x)
    lo, hi = s.along
    c0, c1 = s.point + lo * a, s.point + hi * a
    t0 = math.radians(s.start_deg)
    t1 = t0 + math.radians(s.arc_deg)

    def at(c, t):
        return c + s.radius * (math.cos(t) * x + math.sin(t) * y)

    surface = w.add(f"CYLINDRICAL_SURFACE('',#{w.placement(s.point, a, x)},{step_real(s.radius)})")
    bottom, top = w.circle(c0, a, x, s.radius), w.circle(c1, a, x, s.radius)
    if s.full:
        vb, vt = w.vertex(at(c0, t0)), w.vertex(at(c1, t0))
        eb, et = w.edge(vb, vb, bottom), w.edge(vt, vt, top)
        seam = w.edge(vb, vt, w.line(at(c0, t0), a))
        loop = w.loop([(eb, True), (seam, True), (et, False), (seam, False)])
    else:
        vb0, vb1 = w.vertex(at(c0, t0)), w.vertex(at(c0, t1))
        vt0, vt1 = w.vertex(at(c1, t0)), w.vertex(at(c1, t1))
        eb, et = w.edge(vb0, vb1, bottom), w.edge(vt0, vt1, top)
        side1 = w.edge(vb1, vt1, w.line(at(c0, t1), a))
        side0 = w.edge(vb0, vt0, w.line(at(c0, t0), a))
        loop = w.loop([(eb, True), (side1, True), (et, False), (side0, False)])
    bound = w.add(f"FACE_OUTER_BOUND('',#{loop},.T.)")
    return w.add(f"ADVANCED_FACE({step_string(name)},(#{bound}),#{surface},.T.)")


class _Assembly:
    """The products: a root (the part) and one product per shape under it (module docstring)."""

    def __init__(self, w: _Data, title: str):
        self.w = w
        app = w.add("APPLICATION_CONTEXT('core data for automotive mechanical design processes')")
        w.add(f"APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,#{app})")
        self.product_context = w.add(f"PRODUCT_CONTEXT('',#{app},'mechanical')")
        self.definition_context = w.add(f"PRODUCT_DEFINITION_CONTEXT('part definition',#{app},'design')")
        mm = w.add("(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.))")
        rad = w.add("(NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.))")
        sr = w.add("(NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT())")
        unc = w.add(f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE({step_real(UNCERTAINTY_MM)}),#{mm},"
                    "'distance_accuracy_value','confusion accuracy')")
        self.context = w.add(f"(GEOMETRIC_REPRESENTATION_CONTEXT(3) GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{unc})) "
                             f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{mm},#{rad},#{sr})) "
                             "REPRESENTATION_CONTEXT('Context #1','3D Context with UNIT and UNCERTAINTY'))")
        self.root, self.root_rep, self.root_origin = self._product(title, "")
        self.n = 0

    def _origin(self) -> int:
        return self.w.placement((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))

    def _product(self, name: str, description: str) -> tuple[int, int, int]:
        w = self.w
        prod = w.add(f"PRODUCT({step_string(name)},{step_string(name)},{step_string(description)},"
                     f"(#{self.product_context}))")
        w.add(f"PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,(#{prod}))")
        formation = w.add(f"PRODUCT_DEFINITION_FORMATION('','',#{prod})")
        pd = w.add(f"PRODUCT_DEFINITION('design','',#{formation},#{self.definition_context})")
        pds = w.add(f"PRODUCT_DEFINITION_SHAPE('','',#{pd})")
        origin = self._origin()
        rep = w.add(f"SHAPE_REPRESENTATION({step_string(name)},(#{origin}),#{self.context})")
        w.add(f"SHAPE_DEFINITION_REPRESENTATION(#{pds},#{rep})")
        return pd, rep, origin

    def part(self, name: str, note: str | None, face: int | None = None, edge: int | None = None,
             points=()) -> None:
        """A product under the root holding one ``face`` (an ADVANCED_FACE id), one ``edge`` (an
        EDGE_CURVE id) or ``points`` (CARTESIAN_POINT ids): one thing per product, which FreeCAD
        shows as one named object (module docstring, "What FreeCAD shows")."""
        w = self.w
        s = step_string(name)
        pd, rep, origin = self._product(name, note or "")
        if face is not None:
            shell = w.add(f"OPEN_SHELL({s},(#{face}))")
            model = w.add(f"SHELL_BASED_SURFACE_MODEL({s},(#{shell}))")
            sub = w.add(f"MANIFOLD_SURFACE_SHAPE_REPRESENTATION({s},(#{self._origin()},#{model}),#{self.context})")
            w.add(f"SHAPE_REPRESENTATION_RELATIONSHIP('','',#{rep},#{sub})")
        if edge is not None:
            chain = w.add(f"CONNECTED_EDGE_SET({s},(#{edge}))")
            model = w.add(f"EDGE_BASED_WIREFRAME_MODEL({s},(#{chain}))")
            sub = w.add(f"EDGE_BASED_WIREFRAME_SHAPE_REPRESENTATION({s},(#{self._origin()},#{model}),#{self.context})")
            w.add(f"SHAPE_REPRESENTATION_RELATIONSHIP('','',#{rep},#{sub})")
        if points:
            cset = w.add(f"GEOMETRIC_CURVE_SET({s},({','.join(f'#{p}' for p in points)}))")
            sub = w.add(f"GEOMETRICALLY_BOUNDED_WIREFRAME_SHAPE_REPRESENTATION({s},(#{self._origin()},#{cset}),"
                        f"#{self.context})")
            w.add(f"SHAPE_REPRESENTATION_RELATIONSHIP('','',#{rep},#{sub})")
        self.n += 1
        nauo = w.add(f"NEXT_ASSEMBLY_USAGE_OCCURRENCE('NAUO{self.n}',{step_string(name)},'',#{self.root},#{pd},$)")
        place = w.add(f"PRODUCT_DEFINITION_SHAPE('Placement','Placement of an item',#{nauo})")
        move = w.add(f"ITEM_DEFINED_TRANSFORMATION('','',#{self.root_origin},#{origin})")
        rel = w.add(f"(REPRESENTATION_RELATIONSHIP('','',#{rep},#{self.root_rep}) "
                    f"REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#{move}) SHAPE_REPRESENTATION_RELATIONSHIP())")
        w.add(f"CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#{rel},#{place})")


def step_text(shapes: CadShapes, *, title: str = "Fitted shapes", timestamp: str = "", author: str = "",
              organization: str = "") -> str:
    """The STEP file of ``shapes`` (from :func:`ohs.cad.shapes.cad_shapes`) as text (module
    docstring). ``title`` names the part (the root product and the file); ``timestamp`` goes into
    the header as given (empty by default, so the same shapes give the same bytes)."""
    w = _Data()
    asm = _Assembly(w, title)
    unique = _Names([title] + ([*shapes.axes.names, shapes.axes.origin_name] if shapes.axes is not None else []))
    for s in shapes.shapes:
        own = unique(s.name)
        if isinstance(s, PlaneFace):
            asm.part(own, s.note, face=_plane_face(w, s, own))
        elif isinstance(s, CylinderFace):
            asm.part(own, s.note, face=_cylinder_face(w, s, own))
            name = unique(f"{own} axis")
            asm.part(name, None, edge=w.line_edge(s.point + s.along[0] * s.axis, s.point + s.along[1] * s.axis, name))
        elif isinstance(s, HoleCircle):
            asm.part(own, s.note, edge=w.circle_edge(s.center, s.normal, _square_to(s.normal), s.radius, own))
            name = unique(f"{own} axis")
            asm.part(name, None, edge=w.line_edge(s.center - s.axis_half_mm * s.normal,
                                                  s.center + s.axis_half_mm * s.normal, name))
        elif isinstance(s, PointMark):
            asm.part(own, s.note, points=[w.point(s.point, own)])
        else:
            raise ValueError(f"cannot write a {type(s).__name__} as STEP")
    if shapes.axes is not None:
        ax = shapes.axes
        for name, d in zip(ax.names, ax.axes):
            asm.part(name, None, edge=w.line_edge(ax.origin, ax.origin + ax.length_mm * d, name))
        asm.part(ax.origin_name, None, points=[w.point(ax.origin, ax.origin_name)])
    where = "the part's axes" if shapes.in_part_frame else "the scan's own axes"
    description = [f"{SYSTEM} fitted shapes, mm, in {where}"] + list(shapes.notes)
    header = (
        "ISO-10303-21;\n"
        "HEADER;\n"
        f"FILE_DESCRIPTION(({','.join(step_string(d) for d in description)}),'2;1');\n"
        f"FILE_NAME({step_string(title)},{step_string(timestamp)},({step_string(author)}),"
        f"({step_string(organization)}),{step_string(SYSTEM)},{step_string(SYSTEM)},'');\n"
        f"FILE_SCHEMA(({step_string(SCHEMA)}));\n"
        "ENDSEC;\n"
    )
    return header + "DATA;\n" + w.text() + "ENDSEC;\nEND-ISO-10303-21;\n"


def write_step(path, shapes: CadShapes, **kw) -> Path:
    """Write :func:`step_text` to ``path`` (ASCII, ``\\n`` line ends); returns the path."""
    path = Path(path)
    path.write_bytes(step_text(shapes, **kw).encode("ascii"))
    return path
