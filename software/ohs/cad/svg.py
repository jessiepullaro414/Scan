"""A section drawn as an SVG at 1:1 (FEATURE_PLAN I1): our own small writer.

It draws a :class:`~ohs.mesh.section.Section` (:mod:`ohs.mesh.section`) in the plane's own 2D
coordinates, so that a millimetre is a millimetre when printed at 100 %:

* the ``width`` and ``height`` are given in mm and the ``viewBox`` has the same numbers, so one
  user unit is one millimetre;
* SVG's y axis points down, the drawing's ``v`` up: each point ``(u, v)`` is drawn at
  ``(u - u0, v0 - v)``, a shift and a turn of the y axis, never a mirror (the drawing reads as the
  cut seen from the side the plane's normal points to, as in :mod:`ohs.mesh.section`);
* each stretch of a line (:meth:`~ohs.mesh.section.SectionLine.stretches`) is one ``path``:
  ``class="measured"`` in solid black, ``class="not-measured"`` (over faces that hole filling made
  up) in dashed red; a closed line over one kind of face is one closed path (``Z``);
* below the drawing, the plane's description and a scale bar (a round length, 1, 2 or 5 times a
  power of ten, about a quarter of the drawing, with its length written beside it in mm);
* the description is also the file's ``title``.

Text is escaped for XML (``&``, ``<``, ``>``, both quotes; control characters become spaces). The
file is UTF-8. Numbers have up to 4 decimals (0.1 µm), trailing zeros dropped; the same section
gives a byte-identical file.
"""
from __future__ import annotations

import math
from pathlib import Path

__all__ = ["svg_text", "write_svg", "MARGIN_MM"]

MARGIN_MM = 10.0  # white space round the drawing
FOOT_MM = 14.0  # below it: the title line and the scale bar
MIN_WIDTH_MM = 80.0  # room for the title and the scale bar on a small section
LINE_MM = 0.25  # line width
FONT_MM = 3.0
COLORS = {"measured": "#000000", "not-measured": "#d62728"}
DASH = "1.2 0.8"


def _num(x) -> str:
    s = f"{float(x):.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _esc(text: str) -> str:
    out = []
    for ch in str(text):
        o = ord(ch)
        if o < 0x20 and ch not in "\t\n\r" or 0xD800 <= o <= 0xDFFF or o in (0xFFFE, 0xFFFF):
            out.append(" ")  # not allowed in XML 1.0
        else:
            out.append({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}.get(ch, ch))
    return "".join(out)


def _scale_length(size: float) -> float:
    """A round length (1, 2 or 5 times a power of ten) of at most a quarter of ``size`` mm."""
    target = max(size, 1.0) / 4.0
    p = 10.0 ** math.floor(math.log10(target))
    return max(m * p for m in (1.0, 2.0, 5.0) if m * p <= target * (1 + 1e-12))


def svg_text(section, *, title: str | None = None) -> str:
    """The section as SVG text (module docstring). ``title``: else the plane's description."""
    title = section.plane.description if title is None else str(title)
    bb = section.bbox
    if bb is None:
        umin = vmin = umax = vmax = 0.0
    else:
        umin, vmin, umax, vmax = (float(x) for x in bb)
    du, dv = umax - umin, vmax - vmin
    W = float(_num(max(du + 2 * MARGIN_MM, MIN_WIDTH_MM)))
    H = float(_num(dv + 2 * MARGIN_MM + FOOT_MM))
    u0 = umin - (W - du) / 2.0
    v0 = vmax + MARGIN_MM

    def xy(p) -> str:
        return f"{_num(p[0] - u0)} {_num(v0 - p[1])}"

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="{_num(W)}mm" height="{_num(H)}mm" '
           f'viewBox="0 0 {_num(W)} {_num(H)}">',
           f"<title>{_esc(title)}</title>",
           "<desc>A section at 1:1, in millimetres. Solid black: measured. Dashed red: not measured (the "
           "surface there was filled in).</desc>",
           f'<rect x="0" y="0" width="{_num(W)}" height="{_num(H)}" fill="#ffffff"/>',
           f'<g id="section" fill="none" stroke-width="{_num(LINE_MM)}" stroke-linejoin="round" '
           'stroke-linecap="round">']
    for ln in section.lines:
        for P, filled, closed in ln.stretches():
            kind = "not-measured" if filled else "measured"
            d = "M " + " L ".join(xy(p) for p in P) + (" Z" if closed else "")
            dash = f' stroke-dasharray="{DASH}"' if filled else ""
            out.append(f'<path class="{kind}" stroke="{COLORS[kind]}"{dash} d="{d}"/>')
    out.append("</g>")
    font = f'font-family="sans-serif" font-size="{_num(FONT_MM)}" fill="#000000"'
    y_title = H - FOOT_MM + 4.0
    out.append(f'<text x="{_num(MARGIN_MM)}" y="{_num(y_title)}" {font}>{_esc(title)}</text>')
    L = _scale_length(max(du, dv))
    y_bar = H - 4.0
    x1, x2 = MARGIN_MM, MARGIN_MM + L
    out.append('<g id="scale-bar" stroke="#000000" stroke-width="0.35">')
    out.append(f'<line x1="{_num(x1)}" y1="{_num(y_bar)}" x2="{_num(x2)}" y2="{_num(y_bar)}"/>')
    for x in (x1, x2):
        out.append(f'<line x1="{_num(x)}" y1="{_num(y_bar - 1.0)}" x2="{_num(x)}" y2="{_num(y_bar + 1.0)}"/>')
    out.append(f'<text x="{_num(x2 + 2.0)}" y="{_num(y_bar + 1.0)}" stroke="none" {font}>{L:g} mm</text>')
    out.append("</g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def write_svg(path, section, **kw) -> Path:
    """Write :func:`svg_text` to ``path`` (UTF-8); returns the path."""
    path = Path(path)
    path.write_bytes(svg_text(section, **kw).encode("utf-8"))
    return path
