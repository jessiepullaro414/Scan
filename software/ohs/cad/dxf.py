"""A section written as a DXF drawing at 1:1 in mm (FEATURE_PLAN I1): our own small writer.

It writes a :class:`~ohs.mesh.section.Section` (:mod:`ohs.mesh.section`): each line of the cut in
the plane's own 2D coordinates (``u`` to the right, ``v`` up, mm), nothing else.

**The version: DXF R12, ASCII** (``$ACADVER AC1009``). It is the version every CAD and CAM
program reads, old and new, because it needs no object handles, classes or dictionaries: a header,
the line types, the layers and the entities are the whole file. Newer versions (R2000 and later)
can only be read reliably when that whole object structure is right as well, which a writer of a
few lines cannot promise and the tests here could not show. What R12 lacks is a units variable:
the header still declares millimetres, as ``$INSUNITS 4`` (millimetres, what R2000 and later and
FreeCAD, LibreCAD and others read) and ``$MEASUREMENT 1`` (metric); an R12-only reader skips both
and takes the numbers as they are, which are millimetres at 1:1.

**Entities.** Each stretch of a line (:meth:`~ohs.mesh.section.SectionLine.stretches`) is one
``POLYLINE`` (with its ``VERTEX`` entities and a ``SEQEND``), on layer :data:`LAYER_MEASURED`
(continuous) or :data:`LAYER_NOT_MEASURED` (dashed, red): where the cut runs over faces that hole
filling made up. A closed line over one kind of face is one closed polyline (flag 70 = 1); a closed
line with both kinds is several open polylines that meet end to end. R12's ``POLYLINE`` rather
than ``LWPOLYLINE``, which only came with R14. The vertices' z is 0: the drawing is the plane.

**Numbers** are written with 9 decimals (a nanometre), trailing zeros dropped; the same section
gives a byte-identical file (no time stamp, no handles). The file is plain ASCII with ``\\n`` line
ends.
"""
from __future__ import annotations

from pathlib import Path

__all__ = ["dxf_text", "write_dxf", "LAYER_MEASURED", "LAYER_NOT_MEASURED", "DXF_VERSION"]

DXF_VERSION = "AC1009"  # R12
LAYER_MEASURED = "MEASURED"
LAYER_NOT_MEASURED = "NOT_MEASURED"
_DASH_MM = (2.0, -1.0)  # the dashed line type: 2 mm dash, 1 mm gap
_LAYERS = (("0", 7, "CONTINUOUS"), (LAYER_MEASURED, 7, "CONTINUOUS"), (LAYER_NOT_MEASURED, 1, "DASHED"))


def _num(x) -> str:
    """A number with up to 9 decimals, trailing zeros dropped (``12.5``, ``3.0``, never ``-0.0``)."""
    s = f"{float(x):.9f}".rstrip("0")
    s = s + "0" if s.endswith(".") else s
    return "0.0" if s == "-0.0" else s


class _Out:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, code: int, value) -> None:
        self.lines.append(f"{code:>3}")
        self.lines.append(value if isinstance(value, str) else _num(value) if isinstance(value, float) else str(value))

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def _header(w: _Out, bbox) -> None:
    w(0, "SECTION")
    w(2, "HEADER")
    w(9, "$ACADVER")
    w(1, DXF_VERSION)
    w(9, "$INSUNITS")
    w(70, 4)  # millimetres
    w(9, "$MEASUREMENT")
    w(70, 1)  # metric
    lo = (0.0, 0.0) if bbox is None else (float(bbox[0]), float(bbox[1]))
    hi = (0.0, 0.0) if bbox is None else (float(bbox[2]), float(bbox[3]))
    for name, (x, y) in (("$EXTMIN", lo), ("$EXTMAX", hi)):
        w(9, name)
        w(10, x)
        w(20, y)
        w(30, 0.0)
    w(0, "ENDSEC")


def _tables(w: _Out) -> None:
    w(0, "SECTION")
    w(2, "TABLES")
    w(0, "TABLE")
    w(2, "LTYPE")
    w(70, 2)
    for name, text, pattern in (("CONTINUOUS", "Solid line", ()), ("DASHED", "Dashed __ __ __", _DASH_MM)):
        w(0, "LTYPE")
        w(2, name)
        w(70, 0)
        w(3, text)
        w(72, 65)
        w(73, len(pattern))
        w(40, float(sum(abs(p) for p in pattern)))
        for p in pattern:
            w(49, float(p))
    w(0, "ENDTAB")
    w(0, "TABLE")
    w(2, "LAYER")
    w(70, len(_LAYERS))
    for name, color, ltype in _LAYERS:
        w(0, "LAYER")
        w(2, name)
        w(70, 0)
        w(62, color)
        w(6, ltype)
    w(0, "ENDTAB")
    w(0, "ENDSEC")


def _polyline(w: _Out, layer: str, P, closed: bool) -> None:
    w(0, "POLYLINE")
    w(8, layer)
    w(66, 1)  # vertices follow
    w(10, 0.0)
    w(20, 0.0)
    w(30, 0.0)
    w(70, 1 if closed else 0)
    for x, y in P:
        w(0, "VERTEX")
        w(8, layer)
        w(10, float(x))
        w(20, float(y))
        w(30, 0.0)
    w(0, "SEQEND")
    w(8, layer)


def dxf_text(section) -> str:
    """The section as DXF R12 text (module docstring)."""
    w = _Out()
    _header(w, section.bbox)
    _tables(w)
    w(0, "SECTION")
    w(2, "BLOCKS")
    w(0, "ENDSEC")
    w(0, "SECTION")
    w(2, "ENTITIES")
    for ln in section.lines:
        for P, filled, closed in ln.stretches():
            _polyline(w, LAYER_NOT_MEASURED if filled else LAYER_MEASURED, P, closed)
    w(0, "ENDSEC")
    w(0, "EOF")
    return w.text()


def write_dxf(path, section) -> Path:
    """Write :func:`dxf_text` to ``path`` (ASCII); returns the path."""
    path = Path(path)
    path.write_bytes(dxf_text(section).encode("ascii"))
    return path
