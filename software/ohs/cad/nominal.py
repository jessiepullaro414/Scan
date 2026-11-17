"""Suggested nominal sizes, for an engineer to approve (``docs/FEATURE_PLAN.md``, C3).

Beside a measured value the app offers the round value the part was probably drawn with:
Ø 14.02 ± 0.03 mm → "14?", 89.7° → "90?", two planes 0.3° apart → "parallel?". **A suggestion is
only an offer.** Nothing here changes a fit or rounds a number silently: :func:`suggest_fit` and
:func:`suggest_relation` say what could be accepted and why, the engineer accepts, changes or
ignores it, and :func:`apply_nominal` writes accepted values into a *copy* of a fit's shape for the
CAD export, leaving the measured shape as it was.

**When a round value is offered.** Where it lies inside the measurement's ±2σ; and, only when asked
for (``with_bias=True``), also where it lies inside the ±2σ widened by the known bias of that kind of
feature and size (:data:`BIASES`). The ± of a fit or a relation is the spread from scan to scan, not
the accuracy (``ohs.measure.region``, "The ± values"; ``ohs.measure.relate``): a bias that is the same
on every scan is not in it. So a boss that reads 0.3 mm small in diameter, with a ± of 0.01 mm, can be
offered its round size only because the table says such bosses read that much small; that offer
says so and names the section of ``VALIDATION.md`` the number comes from. A bias only widens the
window on its own side: a value inside ±2σ alone is always allowed.

**A bias alone makes no offer unless asked** (the owner, after C3: "a Ø 14.3 boss may well be a
Ø 14.3 boss; do not assume"). With ``with_bias`` False, the default, the search runs as if no bias
were known: a round value only a bias would allow is not a candidate, so it neither counts towards
"too many" nor stops the search at its step, and a finer step can then offer a value the ± alone
allows (a Ø 14.45 ± 0.05 boss: 14.5, where the bias would have offered 15). An offer that would
have had both kinds keeps the ± ones. What the bias would add is not hidden: :attr:`Offer.bias_only`
lists those values, and the reason of an offer without suggestions names them. The notes on the
bias (which rows exist for the case) are the same either way.

**The bias table is made of simulator numbers.** Every row is a number ``VALIDATION.md`` states for
simulated scans (GPU path, scans without markers, Standard quality, an orbit), at the true poses or
with tracking. The real scanner's biases are still to be measured (the reference parts of
``ROADMAP.md``, section 3); until then these rows say what the simulator shows, and the CPU path or
marker mode, whose meshes differ (thicker walls, fatter features), may read otherwise. Where the
document has no number for a case (a bore, a boss of another size, a hole read at another level,
any angle, any distance but a thin wall's thickness), no bias is used and the offer says so.

**Which value.** A cylinder or a hole: its diameter (or its radius when asked, ``value="radius"``,
as for a fillet). A plane alone has no size and gets none. A relation: each of its values that has
a ± (the angle; the distance; two holes' centre distance).

**What counts as round** (tried in this order; the first step at which anything fits is offered,
finer steps only when nothing coarser fits):

* lengths, diameters and radii (mm): whole millimetres, then halves, then tenths. Drill and
  fastener clearance sizes (6.6, 9, 11 for M6, M8, M10 ...) get no preference of their own: they are
  whole, half or tenth millimetres already and are found in that order. Preferring one series
  over a nearer step is a choice of design intent that the engineer makes;
* angles (degrees, 0-90 as ``relate`` gives them): 0° and 90°, then 30°, 45° and 60°, then whole
  degrees. 0° is offered as "parallel", 90° as "perpendicular";
* a distance of 0 between two parallel planes is "in one plane", between two parallel axes
  "coaxial", when their angle also allows parallel.

When two candidates of one step fit, both are given, nearest first, rather than one chosen; when
more than :data:`MAX_CANDIDATES` fit, the ± is too large to tell that step's values apart and
nothing is offered (finer steps would be worse).

**When nothing is offered**, the answer says why (:attr:`Offer.reason_code`):

* ``"already_round"``: the measured value is already at the step, as the app shows it (to
  :data:`AT_STEP` of it: the app shows two decimals);
* ``"no_candidate"``: no round value fits, with or without the bias;
* ``"too_many"``: more than :data:`MAX_CANDIDATES` fit;
* ``"no_sigma"``: the value has no ± (an angle with a hole's direction: a circle keeps no ± for its
  normal; a fit stored without one);
* ``"warning"``: the fit carries a warning that makes its size unreliable
  (:data:`UNRELIABLE_WARNINGS`, from what ``ohs.measure.region`` writes).

**Is an approved value still allowed?** :func:`check_fit_nominal` and :func:`check_relation_nominal`
put one given nominal value against a measurement by the same rule: inside ±2σ, inside the window
a known bias widens (only with ``with_bias``), or what the value already reads as. The app keeps an
approved value with the scan, and with it the rule it was approved under; when the fit is measured
again it asks this by that rule, and marks the approval "check again" when the new measurement no
longer allows it. Nothing is changed here either.
"""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field, replace

import numpy as np

__all__ = ["AT_STEP", "MAX_CANDIDATES", "LENGTH_STEPS", "ANGLE_STEPS", "UNRELIABLE_WARNINGS", "Source", "Bias",
           "BIASES", "Suggestion", "Offer", "suggest_fit", "suggest_relation", "apply_nominal", "NominalCheck",
           "check_fit_nominal", "check_relation_nominal"]

AT_STEP = 0.005  # mm or degrees: a value this close to a step is shown as it (the app shows two decimals)
MAX_CANDIDATES = 2  # more round values than this fitting at one step: the ± cannot tell them apart
EPS = 1e-9  # numbers on the edge of a window count as inside it
LENGTH_STEPS = (1.0, 0.5, 0.1)  # mm: whole, half, tenth millimetres
#: (the step's name, as a number, and its values): 0/90, then 30/45/60 (named 15), then whole degrees
ANGLE_STEPS = ((90.0, (0.0, 90.0)), (15.0, (30.0, 45.0, 60.0)), (1.0, tuple(float(d) for d in range(91))))
LEVEL_MATCH_MM = 0.005  # a hole's reading level matches a row's to this (region.py prints it to 0.01 mm)

#: warnings of ``ohs.measure.region`` that make a size unreliable: (a piece of the warning's text, why).
#: Not counted: "small region" / "small hole" (few points widen the ± already), "the region touches
#: filled or not measured faces: they are left out" (the fit uses measured faces only), "this hole
#: has a wall" (its bias, which depends on the level, is in the table).
UNRELIABLE_WARNINGS = (
    ("the points scatter", "the points do not follow the fitted shape (a curved or rough surface?)"),
    ("the region was still growing after", "the region had not settled when the fit was made"),
    ("the hole's edge is incomplete", "part of the hole's edge has no points"),
    ("the hole's edge is not round", "the hole's edge is not round (a slot or a damaged edge?)"),
    ("the hole borders filled or not measured faces",
     "the hole's edge is where the measured surface ends, not where the hole is"),
)
_WALL_WARNING = re.compile(r"this hole has a wall: .*\((\d+(?:\.\d+)?) mm below it\)")


# --------------------------------------------------------------------------- the bias table
@dataclass(frozen=True)
class Source:
    """Where in ``VALIDATION.md`` a bias comes from: a ``##`` heading (as it starts), optionally a
    bold part inside it, and sentences or table rows quoted from it (whitespace aside)."""

    section: str
    quotes: tuple[str, ...]
    part: str | None = None

    def to_dict(self) -> dict:
        return {"section": self.section, "part": self.part, "quotes": list(self.quotes)}


@dataclass(frozen=True)
class Bias:
    """One known bias: ``measured − true`` of a kind of feature, in a range of true sizes.

    ``feature``: "boss" (a convex cylinder: a boss, a shaft, a pin), "hole_edge" (a hole whose
    mesh has an open edge, over a far background), "hole_wall" (a hole read from the flat face
    around it, ``level_mm`` below it), "wall" (a thin wall's thickness: two parallel faces seen
    from opposite sides). ``size_mm``: the true diameter (thickness) the row holds for, both ends
    included: the size measured ± 1 mm (± 0.5 mm for walls), cut where two rows meet; that reach
    is this module's choice, the document measured one size. ``bias_mm``: the lowest and highest
    ``measured − true`` of the diameter (thickness) that the sources state, over seeds and the
    two bosses; radii doubled. ``stated``: what the document says, in its own terms.
    """

    feature: str
    poses: str  # "true" or "tracked"
    size_mm: tuple[float, float]
    bias_mm: tuple[float, float]
    stated: str
    sources: tuple[Source, ...]
    level_mm: float | None = None

    def to_dict(self) -> dict:
        return {"feature": self.feature, "poses": self.poses, "size_mm": list(self.size_mm),
                "bias_mm": list(self.bias_mm), "level_mm": self.level_mm, "stated": self.stated,
                "sources": [s.to_dict() for s in self.sources]}


_FITS_TODAY = "The fits' ± on today's engine"
_THIN_WALLS = "Thin walls seen from opposite sides"
_HOLES_FILLED = "Holes filled in: the cause, and fixes tried"
_PER_VOXEL = "The gap guard per voxel"
_KNOWN = "Known limitations of the pipeline"
_HOLE_WALL = "The Hole tool on holes with a wall"

_BOSS_TRUE = (Source(_FITS_TODAY, ("At true poses the bosses now read 0.23–0.26 mm small in radius, the same on every seed",)),
              Source(_THIN_WALLS, ("| Bracket boss radius / tilt | −0.27 mm / 3.2° | −0.28 mm / 2.9° |",)))
_BOSS_TRACKED = (Source(_FITS_TODAY, ("| Bosses, Ø 14 | radius | −0.15 / −0.18 → −0.26 / −0.23 mm | +0.40 / +0.34 → "
                                      "−0.15 / −0.16 mm |",)),
                 Source(_THIN_WALLS, ("| Bracket mean offset / boss radius | +0.015 / −0.18 mm | +0.008 / −0.19 mm |",)))
_SHAFT = Source(_FITS_TODAY, ("| Shaft, Ø 16 | radius | −0.08 → −0.13 mm | +0.60 → +0.02 mm |",
                              "the shaft at true poses −0.13 mm (93 %)"))
_EDGE_SOURCES = (
    Source(_HOLES_FILLED, ("the Ø 25 hole over a far background can be measured on every one of the 16 runs where it "
                           "could not before, 1.1 to 1.6 mm small at true poses and 0.9 to 3 mm small with tracking",),
           part="The gap guard in the engine"),
    Source(_PER_VOXEL, ("| | true | Hole fit | fails 15 of 15 | 23.49–24.11 |",
                        "| Car panel, Ø 25 hole (as fitted: 25.20) | tracked | Hole fit | fails 13 of 15 | 23.82–24.23 |")),
    Source(_FITS_TODAY, ("its diameter reads 1.1–1.3 mm small with ± 0.22 mm",)),
    Source(_THIN_WALLS, ("| Five car panels: hole diameter / centre / floor | −0.90 / 0.34 / 0.002 mm |",)),
    Source(_KNOWN, ("now fits 1 to 1.6 mm small at true poses and 0.9 to 3 mm small with tracking",)),
)
_WALL_HOLE_HALF = Source(_HOLE_WALL, (
    "about 0.6 mm large in diameter at the true poses (Ø 25: +0.57 to +0.70 mm; Ø 10: +0.28 to +0.60 mm), and −0.3 "
    "to +0.5 mm with tracking",
    "Ø 25.35–25.51 on 8 clicks", "Ø 9.66–10.10"))
_WALL_HOLE_LEVELS = Source(_HOLE_WALL, (
    "The Ø 25 mm hole reads 26.7 mm at 0.15 mm below the face, 26.1 mm at 0.3 mm, 25.6 mm at 0.5 mm, 24.9 mm at 1 mm "
    "and 24.3 mm at 2 mm; the Ø 10 mm hole 11.7, 11.2, 10.6, 9.7 and 9.1 mm",))
_SHEET_TRUE = Source(_THIN_WALLS, ("| Sheet s0 | 2.72 / 2.04 / 3.06 | 2.18 / 1.28 / 3.06 |",
                                   "| Sheet s1 | 2.72 / 1.96 / 3.05 | 2.16 / 1.25 / 3.05 |",
                                   "| Sheet s2 | 2.85 / 1.99 / 3.04 | 2.10 / 1.24 / 3.04 |"))
_SHEET_TRACKED = Source(_THIN_WALLS, ("| Sheet s0 | 2.86 / 2.41 / 3.54 | 2.61 / 1.97 / 3.53 |",
                                      "| Sheet s1 | 2.72 / 2.49 / 3.19 | 2.26 / 1.66 / 3.17 |",
                                      "| Sheet s2 | 2.98 / 2.56 / 3.71 | 2.60 / 2.20 / 3.71 |",
                                      "Walls are still 0.3–0.6 mm (flange) and 0.7–1.2 mm (1 mm rib) too thick"))

#: The known biases (simulated scans; module docstring). ``bias_mm`` in diameter or thickness.
BIASES: tuple[Bias, ...] = (
    # Ø 14 bosses of the bracket (8 mm stubs): the two studies of today's engine
    Bias("boss", "true", (13.0, 15.0), (-0.56, -0.46),
         "Ø 14 bosses read 0.23–0.28 mm small in radius at true poses", _BOSS_TRUE),
    Bias("boss", "tracked", (13.0, 15.0), (-0.38, -0.30),
         "Ø 14 bosses read 0.15–0.19 mm small in radius with tracking", _BOSS_TRACKED),
    # the Ø 16 shaft of the bracket
    Bias("boss", "true", (15.0, 17.0), (-0.26, -0.26), "a Ø 16 shaft reads 0.13 mm small in radius at true poses",
         (_SHAFT,)),
    Bias("boss", "tracked", (15.0, 17.0), (0.04, 0.04), "a Ø 16 shaft reads 0.02 mm large in radius with tracking",
         (_SHAFT,)),
    # Ø 25 holes with an open edge over a far background (car panel, plate 120 mm above the floor)
    Bias("hole_edge", "true", (24.0, 26.0), (-1.71, -1.0),
         "a Ø 25 hole over a far background reads 1.0–1.7 mm small in diameter at true poses", _EDGE_SOURCES),
    Bias("hole_edge", "tracked", (24.0, 26.0), (-3.0, -0.9),
         "a Ø 25 hole over a far background reads 0.9–3 mm small in diameter with tracking", _EDGE_SOURCES),
    # holes with a wall, read 0.5 mm below the face (RIM_DROP_MM): the plates 10 mm above the floor
    Bias("hole_wall", "true", (24.0, 26.0), (0.57, 0.70),
         "a Ø 25 hole with a wall, read 0.5 mm below the face, reads 0.57–0.70 mm large in diameter at true poses",
         (_WALL_HOLE_HALF,), level_mm=0.5),
    Bias("hole_wall", "true", (9.0, 11.0), (0.28, 0.60),
         "a Ø 10 hole with a wall, read 0.5 mm below the face, reads 0.28–0.60 mm large in diameter at true poses",
         (_WALL_HOLE_HALF,), level_mm=0.5),
    Bias("hole_wall", "tracked", (24.0, 26.0), (-0.3, 0.51),
         "a Ø 25 hole with a wall, read 0.5 mm below the face, reads −0.3 to +0.51 mm off in diameter with tracking",
         (_WALL_HOLE_HALF,), level_mm=0.5),
    Bias("hole_wall", "tracked", (9.0, 11.0), (-0.34, 0.5),
         "a Ø 10 hole with a wall, read 0.5 mm below the face, reads −0.34 to +0.5 mm off in diameter with tracking",
         (_WALL_HOLE_HALF,), level_mm=0.5),
    # the same holes read deeper (true poses only; one reading each)
    Bias("hole_wall", "true", (24.0, 26.0), (-0.1, -0.1),
         "a Ø 25 hole with a wall, read 1 mm below the face, reads 24.9 mm at true poses", (_WALL_HOLE_LEVELS,),
         level_mm=1.0),
    Bias("hole_wall", "true", (9.0, 11.0), (-0.3, -0.3),
         "a Ø 10 hole with a wall, read 1 mm below the face, reads 9.7 mm at true poses", (_WALL_HOLE_LEVELS,),
         level_mm=1.0),
    Bias("hole_wall", "true", (24.0, 26.0), (-0.7, -0.7),
         "a Ø 25 hole with a wall, read 2 mm below the face, reads 24.3 mm at true poses", (_WALL_HOLE_LEVELS,),
         level_mm=2.0),
    Bias("hole_wall", "true", (9.0, 11.0), (-0.9, -0.9),
         "a Ø 10 hole with a wall, read 2 mm below the face, reads 9.1 mm at true poses", (_WALL_HOLE_LEVELS,),
         level_mm=2.0),
    # thin walls of the sheet part, seen from both sides (thickness = distance between two parallel planes)
    Bias("wall", "true", (0.5, 1.5), (0.24, 0.28), "a 1 mm rib reads 1.24–1.28 mm at true poses", (_SHEET_TRUE,)),
    Bias("wall", "true", (1.5, 2.5), (0.10, 0.18), "a 2 mm flange reads 2.10–2.18 mm at true poses", (_SHEET_TRUE,)),
    Bias("wall", "true", (2.5, 3.5), (0.04, 0.06), "a 3 mm rib reads 3.04–3.06 mm at true poses", (_SHEET_TRUE,)),
    Bias("wall", "tracked", (0.5, 1.5), (0.66, 1.20), "a 1 mm rib reads 1.66–2.20 mm with tracking", (_SHEET_TRACKED,)),
    Bias("wall", "tracked", (1.5, 2.5), (0.26, 0.61), "a 2 mm flange reads 2.26–2.61 mm with tracking",
         (_SHEET_TRACKED,)),
    Bias("wall", "tracked", (2.5, 3.5), (0.17, 0.71), "a 3 mm rib reads 3.17–3.71 mm with tracking", (_SHEET_TRACKED,)),
)

_FEATURE_WORDS = {"boss": "a boss", "hole_edge": "a hole with an open edge", "hole_wall": "a hole with a wall",
                  "wall": "a thin wall's thickness"}
_POSES_WORDS = {"true": "at true poses", "tracked": "with tracking"}


# --------------------------------------------------------------------------- the answer
@dataclass
class Suggestion:
    """One round value offered for a measured value (an offer, never applied here)."""

    nominal: float  # mm or degrees
    difference: float  # nominal − measured
    step: float  # the step it is round to: 1, 0.5, 0.1 mm; 90 (0°/90°), 15 (30°/45°/60°) or 1 degree
    allowed_by: str  # "2 sigma": inside ±2σ alone; "bias": only with the known bias in ``bias``
    bias: dict | None  # the :class:`Bias` row that allows it (``to_dict``), or None
    word: str | None  # "parallel", "perpendicular", "coaxial", "in one plane", or None
    directions: str | None  # for an angle of 0° or 90°: whether the two *directions* (normals, axes) are
    #                         "parallel" or "perpendicular" (a plane and an axis: the reverse of the word)
    sentence: str  # what the app shows

    def to_dict(self) -> dict:
        return {"nominal": float(self.nominal), "difference": float(self.difference), "step": float(self.step),
                "allowed_by": self.allowed_by, "bias": self.bias, "word": self.word, "directions": self.directions,
                "sentence": self.sentence}


@dataclass
class Offer:
    """What is offered for one measured value: suggestions, or none and why."""

    value: str  # "diameter", "radius", "angle", "distance", "centre_distance"
    label: str  # the value's name as the app shows it
    unit: str  # "mm" or "deg"
    measured: float
    sigma: float | None  # 1σ, the spread from scan to scan; None where not known
    suggestions: list[Suggestion] = field(default_factory=list)
    reason_code: str | None = None  # when there are none: "already_round", "no_candidate", "too_many", "no_sigma", "warning"
    reason: str | None = None  # the same in words
    #: the round values a known bias alone would allow and that are not offered because that was not
    #: asked for (``with_bias`` False; module docstring), nearest first; empty otherwise
    bias_only: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"value": self.value, "label": self.label, "unit": self.unit, "measured": float(self.measured),
                "sigma": None if self.sigma is None else float(self.sigma),
                "suggestions": [s.to_dict() for s in self.suggestions], "reason_code": self.reason_code,
                "reason": self.reason, "bias_only": [float(x) for x in self.bias_only]}


# --------------------------------------------------------------------------- reading the inputs
def _get(x, name: str):
    return x.get(name) if isinstance(x, dict) else getattr(x, name, None)


def _unwrap(x) -> tuple[str | None, object, list[str]]:
    """``(kind, shape, warnings)`` of a kept feature, a RegionFit or its dict, a fit or its dict."""
    if isinstance(x, dict) and isinstance(x.get("shape"), dict):
        return x.get("kind"), x["shape"], list(x.get("warnings") or [])
    if isinstance(x, dict) and isinstance(x.get("fit"), dict):
        return x.get("kind"), x["fit"], list(x.get("warnings") or [])
    if not isinstance(x, dict) and hasattr(x, "fit") and hasattr(x, "faces"):
        return x.kind, x.fit, list(x.warnings or [])
    return None, x, []


def _shape_kind(shape) -> str | None:
    if isinstance(shape, dict):
        return shape.get("kind")
    return {"PlaneFit": "plane", "CylinderFit": "cylinder", "CircleFit": "circle"}.get(type(shape).__name__)


def _unreliable(warnings) -> str | None:
    """The first warning that makes a size unreliable, as ``"<the warning> (<why it counts>)"``."""
    for w in warnings:
        for marker, why in UNRELIABLE_WARNINGS:
            if marker in w:
                return f"“{w}”: {why}"
    return None


def _wall_level(warnings) -> float | None | bool:
    """The level a hole with a wall was read at (mm below the face); False for a hole without one,
    None when the warning gives no level."""
    for w in warnings:
        if w.startswith("this hole has a wall"):
            m = _WALL_WARNING.search(w)
            return float(m.group(1)) if m else None
    return False


# --------------------------------------------------------------------------- the search
def _fmt(x: float) -> str:
    """A round value as written: 14, 14.5, 14.3, 0.25."""
    return f"{x:.6f}".rstrip("0").rstrip(".") if abs(x - round(x)) > 1e-9 else str(int(round(x)))


def _shown(value: str, label: str, x: float, s: float | None, unit: str) -> str:
    pm = "" if s is None else f" ± {s:.2f}"
    if unit == "deg":
        return f"{label} {x:.2f}{pm}°"
    if value == "diameter":
        return f"Ø {x:.2f}{pm} mm"
    if value == "radius":
        return f"R {x:.2f}{pm} mm"
    return f"{label} {x:.2f}{pm} mm"


def _rows(feature: str | None, poses: str, level: float | None) -> list[Bias]:
    if feature is None:
        return []
    rows = [b for b in BIASES if b.feature == feature and b.poses == poses]
    if feature == "hole_wall":
        rows = [b for b in rows if level is not None and abs(b.level_mm - level) <= LEVEL_MATCH_MM]
    return rows


def _no_bias_note(feature: str | None, why_none: str | None, rows: list[Bias], size: float | None, poses: str,
                  level: float | None) -> str:
    """Why no bias widens the window for a value of true size ``size`` (a sentence)."""
    if why_none:
        return f"No known bias is used: {why_none}."
    what = _FEATURE_WORDS.get(feature, "this value")
    if feature == "hole_wall" and not rows:
        at = "an unknown level" if level is None else f"{level:.2f} mm below the face"
        levels = sorted({b.level_mm for b in BIASES if b.feature == "hole_wall" and b.poses == poses})
        return (f"No known bias: VALIDATION.md has numbers for {what} {_POSES_WORDS[poses]} read at "
                f"{', '.join(_fmt(x) for x in levels)} mm below the face only, and this one was read at {at}.")
    if not rows:
        return f"No known bias: VALIDATION.md has no number for {what} {_POSES_WORDS[poses]}."
    spans = ", ".join(f"{_fmt(b.size_mm[0])}–{_fmt(b.size_mm[1])}" for b in sorted(set(rows), key=lambda b: b.size_mm))
    at = "" if size is None else f" ({_fmt(size)} mm is outside them)"
    return f"No known bias at this size: VALIDATION.md's numbers for {what} cover {spans} mm only{at}."


@dataclass
class _Case:
    """One measured value and what is known about it, for the search."""

    value: str
    label: str
    unit: str
    measured: float
    sigma: float
    scale: float = 1.0  # the value is this times the table's quantity (a radius: 0.5 of a diameter)
    feature: str | None = None  # the table's feature, or None (no bias)
    why_no_feature: str | None = None  # why there is none, when there is none
    poses: str = "tracked"
    level: float | None = None
    words: dict = field(default_factory=dict)  # nominal -> (word, directions)
    lowest: float = 0.0  # candidates must be at least this (a diameter more than 0)
    highest: float = math.inf
    with_bias: bool = False  # whether a known bias alone may allow a value (module docstring)


def _allowed(case: _Case, nominal: float, rows: list[Bias], with_bias: bool | None = None
             ) -> tuple[str, Bias | None] | None:
    """How ``nominal`` fits ``case``: ("2 sigma", None), ("bias", row), or None. A bias counts only
    with ``with_bias`` (the case's own choice when not given)."""
    d = case.measured - nominal
    two = 2.0 * case.sigma + EPS
    if abs(d) <= two:
        return "2 sigma", None
    if not (case.with_bias if with_bias is None else with_bias):
        return None
    size = nominal / case.scale
    for b in rows:
        if b.size_mm[0] - EPS <= size <= b.size_mm[1] + EPS:
            lo, hi = min(b.bias_mm[0], 0.0) * case.scale, max(b.bias_mm[1], 0.0) * case.scale
            if lo - two <= d <= hi + two:
                return "bias", b
    return None


def _candidates(case: _Case, step: float, values: tuple[float, ...] | None, coarser: set[float]) -> list[float]:
    """The round values of one step near the measured value, leaving out those of a coarser step
    (``coarser``, which this adds them to)."""
    if values is None:
        reach = 2.0 * case.sigma + max(abs(x) for b in BIASES for x in b.bias_mm) * case.scale + step
        k0, k1 = math.floor((case.measured - reach) / step), math.ceil((case.measured + reach) / step)
        values = tuple(round(k * step, 6) for k in range(k0, k1 + 1))
    out = []
    for v in values:
        v = float(v)
        if v < case.lowest - EPS or v > case.highest + EPS or v in coarser:
            continue
        out.append(v)
    coarser.update(out)
    return out


def _offered(case: _Case, nominal: float, word: str | None) -> str:
    """A round value as an offer names it: "Ø 14 mm", "parallel (0°)", "10 mm"."""
    unit = "°" if case.unit == "deg" else " mm"
    if word and case.value == "angle":
        return f"{word} ({_fmt(nominal)}°)"
    if word:
        return f"{word} ({_fmt(nominal)} mm)"
    if case.value == "diameter":
        return f"Ø {_fmt(nominal)} mm"
    if case.value == "radius":
        return f"R {_fmt(nominal)} mm"
    return f"{_fmt(nominal)}{unit}"


def _sentence(case: _Case, nominal: float, how: str, row: Bias | None, note: str, word: str | None) -> str:
    shown = _shown(case.value, case.label, case.measured, case.sigma, case.unit)
    unit = "°" if case.unit == "deg" else " mm"
    offered = _offered(case, nominal, word)
    gap = abs(nominal - case.measured)
    two = 2.0 * case.sigma
    if how == "2 sigma":
        why = f"It is {gap:.2f}{unit} away, within twice the ± ({two:.2f}{unit})."
        if note:
            why += " " + note
    else:
        src = row.sources[0]
        where = src.section + (f", “{src.part}”" if src.part else "")
        why = (f"It is {gap:.2f}{unit} away, more than twice the ± ({two:.2f}{unit}), and is offered only because of "
               f"a known bias: {row.stated} on simulated scans (VALIDATION.md, “{where}”). The real scanner's "
               "bias is still to be measured.")
    return (f"{shown}: {offered}? {why} The ± is the spread from scan to scan, not the accuracy: accept, change "
            "or ignore.")


def _offer(case: _Case) -> Offer:
    """Search the steps of ``case`` (module docstring) and say what is offered, or why nothing is.
    Without ``with_bias``, also what a known bias alone would add (:attr:`Offer.bias_only`, and a
    sentence in the reason when nothing is offered)."""
    offer = _search(case)
    rows = _rows(case.feature, case.poses, case.level)
    if case.with_bias or not rows:
        return offer
    on = _search(replace(case, with_bias=True))
    offer.bias_only = [s.nominal for s in on.suggestions if s.allowed_by == "bias"]
    if offer.bias_only and not offer.suggestions:
        named = " or ".join(_offered(case, x, case.words.get(x, (None, None))[0]) for x in offer.bias_only)
        stated = "; ".join(dict.fromkeys(s.bias["stated"] for s in on.suggestions if s.bias))
        offer.reason = (f"{offer.reason} Only with a known bias ({stated}, on simulated scans) would {named} be "
                        "offered: an offer that only a bias allows is made only when asked for.")
    return offer


def _search(case: _Case) -> Offer:
    """The search itself (:func:`_offer`)."""
    offer = Offer(case.value, case.label, case.unit, case.measured, case.sigma)
    rows = _rows(case.feature, case.poses, case.level)
    steps = ANGLE_STEPS if case.unit == "deg" else tuple((s, None) for s in LENGTH_STEPS)
    coarser: set[float] = set()
    shown = _shown(case.value, case.label, case.measured, case.sigma, case.unit)
    unit = "°" if case.unit == "deg" else " mm"
    for step, values in steps:
        cands = _candidates(case, step, values, coarser)
        at = [c for c in cands if abs(c - case.measured) < AT_STEP]
        if at:
            offer.reason_code = "already_round"
            word = case.words.get(at[0], (None, None))[0]
            offer.reason = (f"{shown} is already a round value ({word or _fmt(at[0]) + unit}): there is nothing "
                            "to suggest.")
            return offer
        fits = []
        for c in cands:
            how = _allowed(case, c, rows)
            if how is not None:
                fits.append((abs(c - case.measured), c, how))
        if len(fits) > MAX_CANDIDATES:
            offer.reason_code = "too_many"
            offer.reason = (f"The ± of {shown} is too large to tell round values apart: "
                            f"{', '.join(_fmt(c) + unit for _, c, _ in sorted(fits))} all fit. Nothing is "
                            "suggested.")
            return offer
        if fits:
            for _, c, (how, row) in sorted(fits):
                word, directions = case.words.get(c, (None, None))
                note = "" if how == "bias" else _no_bias_note(case.feature, case.why_no_feature, rows,
                                                               c / case.scale, case.poses, case.level)
                if how == "2 sigma" and rows and any(b.size_mm[0] - EPS <= c / case.scale <= b.size_mm[1] + EPS
                                                     for b in rows):
                    note = ""  # a bias is known here, and the value needs none
                offer.suggestions.append(Suggestion(
                    nominal=c, difference=c - case.measured, step=step, allowed_by=how,
                    bias=None if row is None else row.to_dict(), word=word, directions=directions,
                    sentence=_sentence(case, c, how, row, note, word)))
            return offer
    offer.reason_code = "no_candidate"
    size = case.measured / case.scale
    near = [b for b in rows if b.size_mm[0] - EPS <= size <= b.size_mm[1] + EPS]
    if near and case.with_bias:
        note = f"The known bias was allowed for ({'; '.join(b.stated for b in near)})."
    elif near:
        note = (f"No known bias was counted ({'; '.join(b.stated for b in near)}): an offer that only a bias "
                "allows is made only when asked for.")
    else:
        note = _no_bias_note(case.feature, case.why_no_feature, rows, size if rows else None, case.poses, case.level)
    offer.reason = f"No round value lies within twice the ± of {shown} ({2 * case.sigma:.2f}{unit}). {note}"
    return offer


def _refused(value: str, label: str, unit: str, measured: float, sigma: float | None, code: str, reason: str) -> Offer:
    return Offer(value, label, unit, measured, sigma, [], code, reason)


def _check_poses(poses: str) -> None:
    if poses not in _POSES_WORDS:
        raise ValueError(f"poses must be 'tracked' or 'true', got {poses!r}")


# --------------------------------------------------------------------------- fits
def _biased(cases, with_bias: bool):
    """Set the bias choice on the cases of a search (refusals and None pass as they are)."""
    for c in cases:
        if isinstance(c, _Case):
            c.with_bias = bool(with_bias)
    return cases


def suggest_fit(feature, *, form: str | None = None, poses: str = "tracked", value: str | None = None,
                with_bias: bool = False) -> list[Offer]:
    """Suggested nominal sizes for one fit: a list of :class:`Offer` (empty for a plane).

    ``feature``: a kept feature (``{"kind", "shape", "warnings", ...}`` as ``features.json`` holds
    it), a :class:`~ohs.measure.region.RegionFit` or its dict, or a bare fit or its ``to_dict()``
    (then there are no warnings). ``form``, for a cylinder: "boss" (convex: a boss, a shaft, a pin)
    or "bore"; None to take the feature's own ``form`` (a kept cylinder's, or a RegionFit's,
    ``ohs.measure.region.cylinder_form``). When neither says, no bias is used. A form given here
    wins over the feature's. A
    hole's form comes from its warnings (with a wall, and the level it was read at, or not); a
    ``form`` given for a hole is an error. ``poses``: "tracked" (a scan as a user makes it) or
    "true" (a simulated scan fused at its true poses), which picks the table's rows. ``value``:
    "diameter" (the default) or "radius". ``with_bias``: also offer the round values that only a
    known bias allows (module docstring, "A bias alone makes no offer unless asked"); off by default.
    """
    (c,) = _biased([_fit_case(feature, form=form, poses=poses, value=value)], with_bias)
    if c is None:
        return []
    return [c if isinstance(c, Offer) else _offer(c)]


def _fit_case(feature, *, form: str | None, poses: str, value: str | None) -> _Case | Offer | None:
    """The measured value of a fit as the search sees it (:func:`suggest_fit`): a :class:`_Case`, a
    refusal (an :class:`Offer` with its reason) when nothing can be offered for it, or None for a
    plane. Raises ValueError as :func:`suggest_fit` does."""
    _check_poses(poses)
    kind, shape, warnings = _unwrap(feature)
    shape_kind = _shape_kind(shape)
    if shape_kind not in ("plane", "cylinder", "circle"):
        raise ValueError(f"not a fitted shape (a plane, a cylinder or a hole): {shape_kind!r}")
    if shape_kind == "plane":
        return None
    value = value or "diameter"
    if value not in ("diameter", "radius"):
        raise ValueError(f"value must be 'diameter' or 'radius', got {value!r}")
    radius = float(_get(shape, "radius_mm"))
    rs = _get(shape, "radius_sigma_mm")
    if rs is None and _get(shape, "diameter_sigma_mm") is not None:
        rs = float(_get(shape, "diameter_sigma_mm")) / 2.0
    scale = 1.0 if value == "diameter" else 0.5
    measured = 2.0 * radius * scale
    sigma = None if rs is None else 2.0 * float(rs) * scale
    label = "Diameter" if value == "diameter" else "Radius"
    if sigma is None or not math.isfinite(sigma) or sigma < 0:
        return _refused(value, label, "mm", measured, None, "no_sigma",
                        f"This fit has no ± for its {value}, so no size is suggested: a round value is only offered "
                        "where the spread of the measurement is known.")
    bad = _unreliable(warnings)
    if bad:
        return _refused(value, label, "mm", measured, sigma, "warning",
                        f"Not suggested: the fit warns {bad}. Its size is not reliable enough to name a nominal one.")
    case = _Case(value, label, "mm", measured, sigma, scale=scale, poses=poses, lowest=1e-6)
    if shape_kind == "cylinder" or kind == "cylinder":
        if form is None:
            form = _get(feature, "form")  # a kept cylinder (or a RegionFit) says what it is
        if form == "boss":
            case.feature = "boss"
        elif form == "bore":
            case.why_no_feature = "VALIDATION.md has no number for a bore"
        elif form is None:
            case.why_no_feature = "the fit does not say whether this cylinder is a boss or a bore"
        else:
            raise ValueError(f"form must be 'boss', 'bore' or None for a cylinder, got {form!r}")
    else:
        if form is not None:
            raise ValueError("a hole's form comes from its warnings: give no form")
        level = _wall_level(warnings)
        if level is False:
            case.feature = "hole_edge"
        else:
            case.feature, case.level = "hole_wall", level
    return case


# --------------------------------------------------------------------------- relations
def suggest_relation(relation, *, form: str | None = None, poses: str = "tracked", warnings=(),
                     with_bias: bool = False) -> list[Offer]:
    """Suggested nominal values for a relation between two fits: one :class:`Offer` per value.

    ``relation``: a :class:`~ohs.measure.relate.Relation` or its ``to_dict()``. ``form``: "wall"
    when two parallel planes are the two faces of one wall (seen from opposite sides), so that its
    thickness's bias applies; None otherwise. ``poses`` and ``with_bias`` as for
    :func:`suggest_fit`. ``warnings``: the two fits' warnings (a relation keeps none of its own); one
    that counts stops every value.

    Words: an angle of 0° is "parallel", of 90° "perpendicular" (for a plane and an axis: the axis
    along the plane, or square to it); a distance of 0 between two parallel planes is "in one
    plane", between two parallel axes "coaxial", when the angle allows parallel as well.
    """
    cases = _biased(_relation_cases(relation, form=form, poses=poses, warnings=warnings), with_bias)
    return [c if isinstance(c, Offer) else _offer(c) for c in cases]


def _relation_cases(relation, *, form: str | None, poses: str, warnings) -> list[_Case | Offer]:
    """Each value of a relation as the search sees it (:func:`suggest_relation`): a :class:`_Case`,
    or a refusal (an :class:`Offer` with its reason). Raises ValueError as :func:`suggest_relation`."""
    _check_poses(poses)
    rel = relation.to_dict() if hasattr(relation, "to_dict") else relation
    kinds = tuple(rel.get("kinds") or ())
    planes = sum(k == "plane" for k in kinds)
    if form not in (None, "wall"):
        raise ValueError(f"form must be 'wall' or None, got {form!r}")
    if form == "wall" and planes != 2:
        raise ValueError("form 'wall' is for the distance between two planes")
    bad = _unreliable(warnings)
    values = list(rel.get("values") or [])
    offers: list[_Case | Offer] = []
    angle_parallel = False
    for q in values:
        if q.get("name") == "angle" and q.get("sigma") is not None:
            v, s = float(q["value"]), float(q["sigma"])
            angle_parallel = v < AT_STEP or v <= 2.0 * s + EPS
    for q in values:
        name, label, unit = q.get("name"), q.get("label") or q.get("name"), q.get("unit")
        v, s = float(q["value"]), q.get("sigma")
        if s is None:
            why = ("an angle with a hole's direction has none: a hole keeps a ± for its centre only"
                   if name == "angle" and "hole" in kinds else "the relation gives none for it")
            offers.append(_refused(name, label, unit, v, None, "no_sigma",
                                   f"{label} has no ± ({why}), so nothing is suggested."))
            continue
        s = float(s)
        if bad:
            offers.append(_refused(name, label, unit, v, s, "warning",
                                   f"Not suggested: a fit of this relation warns {bad}."))
            continue
        case = _Case(name, label, unit, v, s, poses=poses)
        if unit == "deg":
            case.highest = 90.0
            square = "parallel" if planes == 1 else "perpendicular"
            along = "perpendicular" if planes == 1 else "parallel"
            case.words = {0.0: ("parallel", along), 90.0: ("perpendicular", square)}
            case.why_no_feature = "VALIDATION.md has no bias for angles"
        else:
            if name == "distance" and rel.get("parallel") and angle_parallel:
                if planes == 2:
                    case.words = {0.0: ("in one plane", None)}
                elif planes == 0:
                    case.words = {0.0: ("coaxial", None)}
            if form == "wall" and name == "distance":
                case.feature = "wall"
                case.lowest = 1e-6
            else:
                case.why_no_feature = ("VALIDATION.md has no bias for this distance"
                                       if planes != 2 or name != "distance" else
                                       "VALIDATION.md has a bias for a thin wall's thickness only (form 'wall')")
        offers.append(case)
    return offers


# --------------------------------------------------------------------------- is an approved value still allowed?
@dataclass
class NominalCheck:
    """Whether a nominal value (one the engineer approved, or is about to) is allowed for a
    measurement by the rule the offers follow (module docstring, "When a round value is offered")."""

    value: str  # "diameter", "radius", "angle", "distance", "centre_distance"
    unit: str  # "mm" or "deg"
    nominal: float
    measured: float | None  # None: the measurement has no such value (a relation's distance gone)
    sigma: float | None
    allowed: bool
    allowed_by: str | None  # "at step" (the value reads as it), "2 sigma", "bias"; None when not allowed
    word: str | None  # "parallel", "perpendicular", "in one plane", "coaxial" for that value, else None
    reason: str  # the same in words

    def to_dict(self) -> dict:
        return {"value": self.value, "unit": self.unit, "nominal": float(self.nominal),
                "measured": None if self.measured is None else float(self.measured),
                "sigma": None if self.sigma is None else float(self.sigma), "allowed": bool(self.allowed),
                "allowed_by": self.allowed_by, "word": self.word, "reason": self.reason}


def _word_of(case: _Case, nominal: float) -> str | None:
    return next((w for x, (w, _) in case.words.items() if abs(x - nominal) <= EPS), None)


def _check(c: _Case | Offer, nominal: float) -> NominalCheck:
    """``nominal`` against one measured value (a case of the search, or a refusal)."""
    nominal = float(nominal)
    if isinstance(c, Offer):  # nothing could be offered for this value: nothing is allowed for it either
        offered = _offered(_Case(c.value, c.label, c.unit, c.measured, 0.0), nominal, None)
        return NominalCheck(c.value, c.unit, nominal, c.measured, c.sigma, False, None, None,
                            f"{offered} cannot be checked against the measurement: {c.reason}")
    word = _word_of(case=c, nominal=nominal)
    shown = _shown(c.value, c.label, c.measured, c.sigma, c.unit)
    offered = _offered(c, nominal, word)
    unit = "°" if c.unit == "deg" else " mm"
    out = NominalCheck(c.value, c.unit, nominal, c.measured, c.sigma, False, None, word, "")
    if not (math.isfinite(nominal) and c.lowest - EPS <= nominal <= c.highest + EPS):
        out.reason = f"{offered} is not a value {c.label.lower()} can have."
        return out
    gap = abs(nominal - c.measured)
    two = 2.0 * c.sigma
    if gap < AT_STEP:
        out.allowed, out.allowed_by = True, "at step"
        out.reason = f"{shown} reads {offered} as shown."
        return out
    rows = _rows(c.feature, c.poses, c.level)
    how = _allowed(c, nominal, rows)
    if how is None:
        size = nominal / c.scale
        near = [b for b in rows if b.size_mm[0] - EPS <= size <= b.size_mm[1] + EPS]
        biased = None if c.with_bias else _allowed(c, nominal, rows, with_bias=True)
        if biased is not None:  # a bias would allow it, and the rule asked for does not count one
            out.reason = (f"{offered} is {gap:.2f}{unit} from {shown}: more than twice the ± ({two:.2f}{unit}); "
                          f"only a known bias would allow it ({biased[1].stated} on simulated scans), and a "
                          "known bias is not counted for this value.")
            return out
        beyond = f", and beyond the known bias ({'; '.join(b.stated for b in near)})" if near else ""
        out.reason = (f"{offered} is {gap:.2f}{unit} from {shown}: more than twice the ± ({two:.2f}{unit}){beyond}. "
                      "The measurement does not support it.")
        return out
    out.allowed, out.allowed_by = True, how[0]
    if how[0] == "2 sigma":
        out.reason = f"{offered} is {gap:.2f}{unit} from {shown}, within twice the ± ({two:.2f}{unit})."
    else:
        row = how[1]
        out.reason = (f"{offered} is {gap:.2f}{unit} from {shown}, more than twice the ± ({two:.2f}{unit}), and is "
                      f"allowed only by a known bias: {row.stated} on simulated scans (VALIDATION.md, "
                      f"“{row.sources[0].section}”).")
    return out


def check_fit_nominal(feature, nominal: float, *, value: str | None = None, form: str | None = None,
                      poses: str = "tracked", with_bias: bool = False) -> NominalCheck:
    """Whether ``nominal`` (mm) is allowed for a fit's diameter (or radius, ``value="radius"``) by
    the rule :func:`suggest_fit` offers by: inside twice the ±, inside the window a known bias
    widens (only with ``with_bias``: give the rule the value was approved under), or what the value
    already reads as (within :data:`AT_STEP`). It is how the app tells that an approved value no
    longer fits a fit measured again. A value nothing could be offered for (no ±, a warning that
    counts) is not allowed, with that reason. Arguments as for :func:`suggest_fit`; a plane has no
    size (ValueError)."""
    (c,) = _biased([_fit_case(feature, form=form, poses=poses, value=value)], with_bias)
    if c is None:
        raise ValueError("a plane alone has no size to check")
    return _check(c, nominal)


def check_relation_nominal(relation, value: str, nominal: float, *, form: str | None = None, poses: str = "tracked",
                           warnings=(), with_bias: bool = False) -> NominalCheck:
    """Whether ``nominal`` (mm or degrees) is allowed for the relation's ``value`` ("angle",
    "distance", "centre_distance") by the rule :func:`suggest_relation` offers by, as
    :func:`check_fit_nominal`; ``word`` is what the nominal means there ("parallel" for an angle of
    0, ...). A value the relation does not have (any more) is not allowed: ``measured`` is None and
    the reason says so. Arguments as for :func:`suggest_relation`."""
    for c in _biased(_relation_cases(relation, form=form, poses=poses, warnings=warnings), with_bias):
        if c.value == value:
            return _check(c, nominal)
    unit = "deg" if value == "angle" else "mm"
    return NominalCheck(value, unit, float(nominal), None, None, False, None, None,
                        f"the relation has no {value.replace('_', ' ')} now (the two fits are related differently).")


# --------------------------------------------------------------------------- applying accepted values
def apply_nominal(shape, *, diameter_mm: float | None = None, radius_mm: float | None = None,
                  direction: str | None = None, to=None) -> dict:
    """A copy of a fit's shape (a dict as ``to_dict()`` gives it, or the fit) with accepted nominal
    values in place of the measured ones, for the CAD export. The measured shape is not changed.

    * ``diameter_mm`` or ``radius_mm`` (a cylinder or a hole): both keys are set, the one from the
      other; giving both that disagree is an error.
    * ``direction`` ("parallel" or "perpendicular") with ``to`` (a 3-vector, the other fit's
      direction): the shape's direction (a plane's or a hole's normal, a cylinder's axis) is turned
      to be parallel to ``to`` (keeping its own sign) or perpendicular to it (the nearest such
      direction). Both must be given: a direction is never snapped to an assumed one. For a plane
      and an axis, "perpendicular" between them means their *directions* are parallel
      (:attr:`Suggestion.directions` says which).

    What was set is listed under ``"nominal"`` in the copy, so a report can show measured and
    nominal side by side; the ± values stay the measured ones.
    """
    out = copy.deepcopy(shape) if isinstance(shape, dict) else shape.to_dict()
    kind = out.get("kind")
    nominal = dict(out.get("nominal") or {})
    if diameter_mm is not None or radius_mm is not None:
        if kind not in ("cylinder", "circle"):
            raise ValueError(f"a {kind} has no diameter or radius")
        if diameter_mm is not None and radius_mm is not None and abs(float(diameter_mm) - 2.0 * float(radius_mm)) > 1e-9:
            raise ValueError(f"the diameter {diameter_mm} and the radius {radius_mm} disagree")
        d = float(diameter_mm) if diameter_mm is not None else 2.0 * float(radius_mm)
        if not (math.isfinite(d) and d > 0):
            raise ValueError(f"a nominal diameter must be positive, got {d}")
        out["diameter_mm"], out["radius_mm"] = d, d / 2.0
        if diameter_mm is not None:
            nominal["diameter_mm"] = d
        else:
            nominal["radius_mm"] = d / 2.0
    if (direction is None) != (to is None):
        raise ValueError("a direction is snapped only with both 'direction' and 'to' (the other fit's direction)")
    if direction is not None:
        if direction not in ("parallel", "perpendicular"):
            raise ValueError(f"direction must be 'parallel' or 'perpendicular', got {direction!r}")
        key = "axis" if kind == "cylinder" else "normal"
        if key not in out:
            raise ValueError(f"a {kind} has no direction to snap")
        dvec = np.asarray(out[key], dtype=np.float64)
        dvec = dvec / np.linalg.norm(dvec)
        t = np.asarray(to, dtype=np.float64).reshape(-1)
        if t.shape != (3,) or not np.all(np.isfinite(t)) or np.linalg.norm(t) == 0:
            raise ValueError("'to' must be a finite, non-zero 3-vector")
        t = t / np.linalg.norm(t)
        if direction == "parallel":
            new = t if float(dvec @ t) >= 0 else -t
        else:
            p = dvec - float(dvec @ t) * t
            if np.linalg.norm(p) < 1e-9:
                raise ValueError("the direction is along 'to': there is no one perpendicular direction nearest it")
            new = p / np.linalg.norm(p)
        out[key] = [float(x) for x in new]
        nominal["direction"] = {"relation": direction, "to": [float(x) for x in t]}
    out["nominal"] = nominal
    return out
