"""A small DXF (ASCII) reader for the tests of ``ohs.cad.dxf``: enough to check what the writer
wrote, independent of it.

:func:`read` gives back ``{"header": {name: value}, "layers": {name: {"color", "linetype"}},
"linetypes": [names], "polylines": [{"layer", "closed", "points" (k,2)}]}``. It checks the file's
frame as it goes (group codes and values in pairs, sections opened and closed, ``EOF`` last, every
``POLYLINE`` ended by a ``SEQEND``) and raises on anything else in the entities, so the tests
notice.
"""
from __future__ import annotations

import numpy as np


def pairs(text: str) -> list[tuple[int, str]]:
    """The file as ``(group code, value)`` pairs; the code is an integer, the value stripped."""
    lines = text.splitlines()
    if len(lines) % 2:
        raise ValueError("a DXF file has an even number of lines (a code, then its value)")
    return [(int(lines[i].strip()), lines[i + 1].strip()) for i in range(0, len(lines), 2)]


def sections(text: str) -> dict[str, list[tuple[int, str]]]:
    """Each section's pairs, by name; checks the frame (``SECTION`` / ``ENDSEC`` / ``EOF``)."""
    p = pairs(text)
    if p[-1] != (0, "EOF"):
        raise ValueError("the file does not end with EOF")
    out: dict[str, list] = {}
    i = 0
    while p[i] != (0, "EOF"):
        if p[i] != (0, "SECTION") or p[i + 1][0] != 2:
            raise ValueError(f"expected a SECTION at pair {i}, got {p[i]}")
        name = p[i + 1][1]
        j = p.index((0, "ENDSEC"), i)
        out[name] = p[i + 2 : j]
        i = j + 1
    return out


def _records(ps: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """Split pairs into records, each starting at a code 0."""
    out: list[list] = []
    for c, v in ps:
        if c == 0:
            out.append([(c, v)])
        elif out:
            out[-1].append((c, v))
        else:
            raise ValueError("pairs before the first record")
    return out


def _get(rec, code, default=None):
    for c, v in rec:
        if c == code:
            return v
    return default


def read(text: str) -> dict:
    sec = sections(text)
    header: dict[str, str] = {}
    hp = sec.get("HEADER", [])
    for i, (c, v) in enumerate(hp):
        if c == 9:
            header[v] = hp[i + 1][1]
    layers: dict[str, dict] = {}
    linetypes: list[str] = []
    for rec in _records(sec.get("TABLES", [])):
        if rec[0][1] == "LAYER":
            layers[_get(rec, 2)] = {"color": int(_get(rec, 62)), "linetype": _get(rec, 6)}
        elif rec[0][1] == "LTYPE":
            linetypes.append(_get(rec, 2))
    polylines: list[dict] = []
    current = None
    for rec in _records(sec.get("ENTITIES", [])):
        kind = rec[0][1]
        if kind == "POLYLINE":
            if current is not None:
                raise ValueError("a POLYLINE inside a POLYLINE")
            if _get(rec, 66) != "1":
                raise ValueError("a POLYLINE without its vertices-follow flag")
            current = {"layer": _get(rec, 8), "closed": bool(int(_get(rec, 70, "0")) & 1), "points": []}
        elif kind == "VERTEX":
            if current is None or _get(rec, 8) != current["layer"]:
                raise ValueError("a VERTEX outside its POLYLINE, or on another layer")
            current["points"].append((float(_get(rec, 10)), float(_get(rec, 20)), float(_get(rec, 30, "0"))))
        elif kind == "SEQEND":
            if current is None:
                raise ValueError("a SEQEND without a POLYLINE")
            current["points"] = np.asarray(current["points"], float).reshape(-1, 3)
            if np.any(current["points"][:, 2] != 0.0):
                raise ValueError("a vertex off the drawing's plane")
            current["points"] = current["points"][:, :2]
            polylines.append(current)
            current = None
        else:
            raise ValueError(f"an entity this reader does not know: {kind}")
    if current is not None:
        raise ValueError("a POLYLINE without its SEQEND")
    return {"header": header, "layers": layers, "linetypes": linetypes, "polylines": polylines}
