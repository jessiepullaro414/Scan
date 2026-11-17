"""A small STEP (ISO 10303-21) reader for the tests of ``ohs.cad.step``: enough to check what the
writer wrote, independent of it.

:func:`parse` reads the text into entities (every parameter: strings decoded, references kept as
:class:`Ref`); :func:`missing_references` lists references to entities that are not there;
:func:`read` follows the product structure down to the geometry and gives each product's shapes
back as plain numbers:

* ``plane``: ``point``, ``normal``, ``ref`` and its ``loops`` (each a list of 3D vertex points in
  order, the outer one first), with each loop's kind (``outer`` or ``inner``);
* ``cylinder``: ``point``, ``axis``, ``ref``, ``radius`` and its boundary ``edges`` (each with its
  curve kind, its start and end points and, for a circle, centre and radius);
* ``circle``: ``center``, ``normal``, ``ref``, ``radius`` (and ``start``, ``end``: an edge's vertices);
* ``line``: ``start`` and ``end`` (an edge's vertices, or a trimmed line's ends);
* ``point``: ``point``.

Each shape also has its ``name`` (the item's). It reads only what the writer writes; anything else
raises, so the tests notice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class Ref:
    id: int


@dataclass(frozen=True)
class Enum:
    value: str


@dataclass(frozen=True)
class Typed:
    """A typed parameter such as ``PARAMETER_VALUE(0.)``."""

    name: str
    args: tuple


UNSET = None  # ``$``
DERIVED = "*"


@dataclass
class Entity:
    id: int
    parts: dict  # type name -> its parameters (one for a simple entity, several for a complex one)

    @property
    def type(self) -> str:
        return " ".join(self.parts)

    def args(self, name: str | None = None) -> list:
        if name is None:
            (only,) = self.parts.values()
            return only
        return self.parts[name]


@dataclass
class StepFile:
    header: dict  # FILE_DESCRIPTION, FILE_NAME, FILE_SCHEMA -> their parameters
    entities: dict  # id -> Entity
    by_type: dict = field(default_factory=dict)  # type name -> [Entity]


# --------------------------------------------------------------------------- text
_TOKEN = re.compile(r"""
    (?P<string>'(?:[^']|'')*')
  | (?P<ref>\#\d+)
  | (?P<enum>\.[A-Z_][A-Z0-9_]*\.)
  | (?P<real>[+-]?\d+\.\d*(?:E[+-]?\d+)?)
  | (?P<int>[+-]?\d+)
  | (?P<keyword>!?[A-Z_][A-Z0-9_]*)
  | (?P<sym>[()=,;$*])
  | (?P<space>\s+)
""", re.VERBOSE)


def decode_string(raw: str) -> str:
    """A STEP string literal's text (without its quotes): ``''`` is a quote, ``\\\\`` a backslash,
    ``\\X2\\hhhh…\\X0\\`` UCS-2 and ``\\X4\\hhhhhhhh…\\X0\\`` UCS-4 characters, ``\\X\\hh`` an 8-bit one."""
    s = raw.replace("''", "'")
    out, i = [], 0
    while i < len(s):
        if s.startswith("\\X2\\", i):
            end = s.index("\\X0\\", i)
            hexes = s[i + 4: end]
            assert len(hexes) % 4 == 0, raw
            out.extend(chr(int(hexes[k: k + 4], 16)) for k in range(0, len(hexes), 4))
            i = end + 4
        elif s.startswith("\\X4\\", i):
            end = s.index("\\X0\\", i)
            hexes = s[i + 4: end]
            assert len(hexes) % 8 == 0, raw
            out.extend(chr(int(hexes[k: k + 8], 16)) for k in range(0, len(hexes), 8))
            i = end + 4
        elif s.startswith("\\X\\", i):
            out.append(chr(int(s[i + 3: i + 5], 16)))
            i += 5
        elif s.startswith("\\\\", i):
            out.append("\\")
            i += 2
        elif s[i] == "\\":
            raise ValueError(f"a lone backslash in a STEP string: {raw!r}")
        else:
            assert 0x20 <= ord(s[i]) <= 0x7E, f"a character outside printable ASCII in a STEP string: {raw!r}"
            out.append(s[i])
            i += 1
    return "".join(out)


def _tokens(text: str):
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise ValueError(f"cannot read the STEP text at {text[pos: pos + 40]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "space":
            continue
        yield kind, m.group()


class _Parser:
    def __init__(self, text: str):
        self.toks = list(_tokens(text))
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, value=None):
        tok = self.toks[self.i]
        if value is not None and tok[1] != value:
            raise ValueError(f"expected {value!r}, got {tok[1]!r}")
        self.i += 1
        return tok

    def value(self):
        kind, v = self.take()
        if kind == "string":
            return decode_string(v[1:-1])
        if kind == "ref":
            return Ref(int(v[1:]))
        if kind == "enum":
            return Enum(v[1:-1])
        if kind == "real":
            return float(v)
        if kind == "int":
            return int(v)
        if v == "$":
            return UNSET
        if v == "*":
            return DERIVED
        if v == "(":
            self.i -= 1
            return self.list()
        if kind == "keyword":
            return Typed(v, tuple(self.list()))
        raise ValueError(f"unexpected {v!r}")

    def list(self) -> list:
        self.take("(")
        out = []
        if self.peek()[1] == ")":
            self.take(")")
            return out
        while True:
            out.append(self.value())
            k, v = self.take()
            if v == ")":
                return out
            if v != ",":
                raise ValueError(f"expected ',' or ')', got {v!r}")

    def record(self) -> tuple[str, list]:
        _, name = self.take()
        return name, self.list()


def parse(text: str) -> StepFile:
    """Read a STEP file's text into its header and entities (module docstring)."""
    lines = text.splitlines()
    assert lines[0] == "ISO-10303-21;", lines[0]
    assert lines[-1] == "END-ISO-10303-21;", lines[-1]
    head = text[text.index("HEADER;") + 7: text.index("ENDSEC;")]
    data = text[text.index("DATA;") + 5: text.rindex("ENDSEC;")]
    header = {}
    p = _Parser(head)
    while p.peek()[0] is not None:
        name, args = p.record()
        p.take(";")
        header[name] = args
    entities: dict[int, Entity] = {}
    p = _Parser(data)
    while p.peek()[0] is not None:
        _, ref = p.take()
        eid = int(ref[1:])
        p.take("=")
        parts = {}
        if p.peek()[1] == "(":  # a complex entity: several (type, parameters) one after the other
            p.take("(")
            while p.peek()[1] != ")":
                name, args = p.record()
                parts[name] = args
            p.take(")")
        else:
            name, args = p.record()
            parts[name] = args
        p.take(";")
        assert eid not in entities, f"#{eid} is defined twice"
        entities[eid] = Entity(eid, parts)
    f = StepFile(header=header, entities=entities)
    for e in entities.values():
        for name in e.parts:
            f.by_type.setdefault(name, []).append(e)
    return f


def _refs(x):
    if isinstance(x, Ref):
        yield x.id
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _refs(v)
    elif isinstance(x, Typed):
        yield from _refs(x.args)


def missing_references(f: StepFile) -> list[tuple[int, int]]:
    """``(entity, the id it names)`` for every reference to an entity that is not in the file."""
    out = []
    for e in f.entities.values():
        for args in e.parts.values():
            out.extend((e.id, r) for r in _refs(args) if r not in f.entities)
    return out


# --------------------------------------------------------------------------- geometry
def _get(f: StepFile, r: Ref, *types) -> Entity:
    e = f.entities[r.id]
    if types and not any(t in e.parts for t in types):
        raise ValueError(f"#{r.id} is a {e.type}, expected {' or '.join(types)}")
    return e


def _point(f, r) -> np.ndarray:
    return np.array(_get(f, r, "CARTESIAN_POINT").args()[1], dtype=float)


def _direction(f, r) -> np.ndarray:
    return np.array(_get(f, r, "DIRECTION").args()[1], dtype=float)


def _placement(f, r) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    _, loc, axis, ref = _get(f, r, "AXIS2_PLACEMENT_3D").args()
    return _point(f, loc), _direction(f, axis), _direction(f, ref)


def _vertex(f, r) -> np.ndarray:
    return _point(f, _get(f, r, "VERTEX_POINT").args()[1])


def _curve(f, r) -> dict:
    e = _get(f, r, "LINE", "CIRCLE")
    if "LINE" in e.parts:
        _, pnt, vec = e.args()
        _, d, mag = _get(f, vec, "VECTOR").args()
        return {"kind": "line", "point": _point(f, pnt), "direction": _direction(f, d) * mag}
    _, pl, radius = e.args()
    c, n, x = _placement(f, pl)
    return {"kind": "circle", "center": c, "normal": n, "ref": x, "radius": float(radius)}


def _face(f, e: Entity) -> dict:
    name, bounds, surf, same_sense = e.args("ADVANCED_FACE")
    s = _get(f, surf, "PLANE", "CYLINDRICAL_SURFACE")
    if "PLANE" in s.parts:
        p, n, x = _placement(f, s.args()[1])
        out = {"type": "plane", "name": name, "point": p, "normal": n, "ref": x}
    else:
        _, pl, radius = s.args()
        p, a, x = _placement(f, pl)
        out = {"type": "cylinder", "name": name, "point": p, "axis": a, "ref": x, "radius": float(radius)}
    out["same_sense"] = same_sense.value == "T"
    loops, kinds, edges = [], [], []
    for b in bounds:
        be = _get(f, b, "FACE_OUTER_BOUND", "FACE_BOUND")
        kinds.append("outer" if "FACE_OUTER_BOUND" in be.parts else "inner")
        _, loop, orientation = be.args()
        pts = []
        for oe in _get(f, loop, "EDGE_LOOP").args()[1]:
            _, _, _, ec, forward = _get(f, oe, "ORIENTED_EDGE").args()
            _, v0, v1, geom, sense = _get(f, ec, "EDGE_CURVE").args()
            a, b2 = _vertex(f, v0), _vertex(f, v1)
            if forward.value != "T":
                a, b2 = b2, a
            pts.append(a)
            edges.append({**_curve(f, geom), "start": a, "end": b2, "same_sense": sense.value == "T",
                          "forward": forward.value == "T"})
        loops.append(np.array(pts))
    out["loops"], out["loop_kinds"], out["edges"] = loops, kinds, edges
    return out


def _curve_set_item(f, r) -> dict:
    e = f.entities[r.id]
    if "CARTESIAN_POINT" in e.parts:
        name, xyz = e.args()
        return {"type": "point", "name": name, "point": np.array(xyz, dtype=float)}
    if "CIRCLE" in e.parts:
        c = _curve(f, r)
        return {"type": "circle", "name": e.args()[0], "center": c["center"], "normal": c["normal"], "ref": c["ref"],
                "radius": c["radius"]}
    if "TRIMMED_CURVE" in e.parts:
        name, basis, t1, t2, sense, master = e.args()
        line = _curve(f, basis)
        assert line["kind"] == "line"
        ends = []
        for trim in (t1, t2):
            par = [x for x in trim if isinstance(x, Typed) and x.name == "PARAMETER_VALUE"]
            pts = [x for x in trim if isinstance(x, Ref)]
            at = line["point"] + par[0].args[0] * line["direction"]
            if pts:
                assert np.allclose(_point(f, pts[0]), at, atol=1e-9), "a trim's point and parameter disagree"
            ends.append(at)
        return {"type": "line", "name": name, "start": ends[0], "end": ends[1]}
    raise ValueError(f"#{r.id}: a {e.type} in a curve set")


def _items(f, rep: Entity) -> list[dict]:
    out = []
    for r in rep.args()[1]:
        e = f.entities[r.id]
        if "AXIS2_PLACEMENT_3D" in e.parts:
            continue
        if "SHELL_BASED_SURFACE_MODEL" in e.parts:
            for sh in e.args()[1]:
                for fr in _get(f, sh, "OPEN_SHELL").args()[1]:
                    out.append(_face(f, _get(f, fr, "ADVANCED_FACE")))
        elif "GEOMETRIC_CURVE_SET" in e.parts:
            out.extend(_curve_set_item(f, x) for x in e.args()[1])
        elif "EDGE_BASED_WIREFRAME_MODEL" in e.parts:
            for ces in e.args()[1]:
                for er in _get(f, ces, "CONNECTED_EDGE_SET").args()[1]:
                    name, v0, v1, geom, sense = _get(f, er, "EDGE_CURVE").args()
                    assert sense.value == "T"
                    c = _curve(f, geom)
                    a, b = _vertex(f, v0), _vertex(f, v1)
                    if c["kind"] == "line":
                        # the vertices lie on the line
                        d = c["direction"] / np.linalg.norm(c["direction"])
                        for p in (a, b):
                            q = p - c["point"]
                            assert np.linalg.norm(q - (q @ d) * d) < 1e-9
                        out.append({"type": "line", "name": name, "start": a, "end": b})
                    else:
                        out.append({"type": "circle", "name": name, "center": c["center"], "normal": c["normal"],
                                    "ref": c["ref"], "radius": c["radius"], "start": a, "end": b})
        else:
            raise ValueError(f"#{r.id}: a {e.type} as a representation item")
    return out


def length_unit(f: StepFile, rep: Entity) -> tuple:
    """``(prefix, name)`` of the length unit of a representation's context, e.g. ``("MILLI", "METRE")``."""
    ctx = f.entities[rep.args()[2].id]
    units = ctx.parts["GLOBAL_UNIT_ASSIGNED_CONTEXT"][0]
    for u in units:
        ue = f.entities[u.id]
        if "LENGTH_UNIT" in ue.parts:
            prefix, name = ue.parts["SI_UNIT"]
            return (None if prefix is None else prefix.value, name.value)
    raise ValueError("no length unit")


def read(text: str) -> dict:
    """The file's products and their shapes (module docstring): ``{"schema", "root", "units",
    "products": {name: [shape, ...]}, "description", "file": StepFile}``. Product names must be
    unique."""
    f = parse(text)
    pd_of = {}  # product definition id -> product name
    for e in f.by_type.get("PRODUCT_DEFINITION", []):
        pdf = _get(f, e.args()[2], "PRODUCT_DEFINITION_FORMATION")
        prod = _get(f, pdf.args()[2], "PRODUCT")
        pd_of[e.id] = prod.args()[1]
    rep_of = {}  # product definition id -> its shape representation
    for e in f.by_type.get("SHAPE_DEFINITION_REPRESENTATION", []):
        pds = _get(f, e.args()[0], "PRODUCT_DEFINITION_SHAPE")
        rep_of[pds.args()[2].id] = f.entities[e.args()[1].id]
    children = set()
    for e in f.by_type.get("NEXT_ASSEMBLY_USAGE_OCCURRENCE", []):
        children.add(e.args()[4].id)
    roots = [pd for pd in pd_of if pd not in children]
    assert len(roots) == 1, roots
    linked = {}  # representation id -> the representations it is related to
    for e in f.by_type.get("SHAPE_REPRESENTATION_RELATIONSHIP", []):
        if "REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION" in e.parts:
            continue
        _, _, r1, r2 = e.args("SHAPE_REPRESENTATION_RELATIONSHIP")
        linked.setdefault(r1.id, []).append(f.entities[r2.id])
    products, units = {}, set()
    for pd, name in pd_of.items():
        if pd == roots[0]:
            continue
        assert name not in products, f"two products named {name!r}"
        rep = rep_of[pd]
        shapes = _items(f, rep)
        units.add(length_unit(f, rep))
        for sub in linked.get(rep.id, []):
            shapes.extend(_items(f, sub))
            units.add(length_unit(f, sub))
        products[name] = shapes
    root_rep = rep_of[roots[0]]
    units.add(length_unit(f, root_rep))
    return {"schema": f.header["FILE_SCHEMA"][0], "root": pd_of[roots[0]], "units": units, "products": products,
            "description": f.header["FILE_DESCRIPTION"][0], "file": f}
