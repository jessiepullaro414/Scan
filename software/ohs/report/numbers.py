"""Numbers as the report writes them: millimetres and degrees, with the digits their ± allows.

The rule is the viewer's (``ui/src/lib/fits.ts``, ``decimals`` and ``fmtPm``), so a value reads the
same in the report as on the fit's card:

* a value with a ± shows as many decimals as the ±: 1 for a ± of 1 or more, 2 down to 0.01, 3
  down to 0.001, else 4. "14.02 ± 0.03 mm", "14.023 ± 0.004 mm", "123.5 ± 1.2 mm";
* a length without a ± shows 2 decimals, 3 below 0.1 mm ("0.046 mm"); an angle 2 ("89.70°");
* "-0.00" is written "0.00": a sign that the digits cannot show is not shown.

Everything here returns plain text; the HTML writer escapes it like any other text.
"""
from __future__ import annotations

import math

__all__ = ["decimals", "fixed", "pm", "length", "angle", "small", "vector", "nominal", "percent", "area", "whole",
           "is_number"]


def is_number(x) -> bool:
    """A finite int or float (not a bool)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def decimals(sigma: float) -> int:
    """How many decimals a value with this ± shows (module docstring)."""
    a = abs(float(sigma))
    return 1 if a >= 1 else 2 if a >= 0.01 else 3 if a >= 0.001 else 4


def fixed(x: float, d: int) -> str:
    """``x`` to ``d`` decimals, never "-0.00"."""
    s = f"{float(x):.{d}f}"
    return s[1:] if s.startswith("-") and float(s) == 0.0 else s


def _unit(unit: str) -> str:
    return "°" if unit == "deg" else f" {unit}" if unit else ""


def _plain(x: float, unit: str) -> int:
    if unit == "deg":
        return 2
    return 3 if abs(float(x)) < 0.1 and float(x) != 0.0 else 2


def pm(x: float, sigma: float | None, unit: str = "mm") -> str:
    """A value and its ±, both to the ±'s decimals: "14.02 ± 0.03 mm", "89.70 ± 0.05°". Without a
    ± (None, or not a finite number) the value alone, as :func:`length` or :func:`angle`."""
    if sigma is None or not is_number(sigma):
        return fixed(x, _plain(x, unit)) + _unit(unit)
    d = decimals(sigma)
    return f"{fixed(x, d)} ± {fixed(sigma, d)}{_unit(unit)}"


def length(x: float) -> str:
    """A length without a ±: "40.00 mm", "0.046 mm"."""
    return pm(x, None, "mm")


def angle(x: float) -> str:
    """An angle without a ±: "89.70°"."""
    return pm(x, None, "deg")


def small(x: float) -> str:
    """A small ± on its own, with two significant digits and never "0.000": "0.035", "0.0042",
    "0.50" (the viewer's ``fmtSmall``)."""
    a = abs(float(x))
    if a >= 0.1 or a == 0.0:
        return fixed(x, 2)
    return fixed(x, min(8, max(3, math.ceil(-math.log10(a)) + 1)))


def vector(v, d: int) -> str:
    """A point or a direction: "(12.30, -4.00, 7.25)"."""
    return "(" + ", ".join(fixed(c, d) for c in v) + ")"


def nominal(x: float) -> str:
    """An approved round value as the engineer gave it: 14 for 14.0, 14.5, 0.25."""
    s = f"{float(x):.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def percent(share: float) -> str:
    """A share of 0 to 1 as a percentage with one decimal: "93.4 %"; "less than 0.1 %" and "more
    than 99.9 %" where one decimal would round a little to nothing or all of it."""
    p = 100.0 * float(share)
    if 0.0 < p < 0.05:
        return "less than 0.1 %"
    if 99.95 <= p < 100.0:
        return "more than 99.9 %"
    return f"{fixed(p, 1)} %"


def area(x: float) -> str:
    """An area, thousands separated (UX_PLAN R19): "1,121 mm²", "278,857 mm²", "4.2 mm²"."""
    return f"{float(x):,.0f} mm²" if abs(x) >= 100 else f"{fixed(x, 1)} mm²"


def whole(n) -> str:
    """A count with thousands separated: "804,372"."""
    return f"{int(n):,}"
