"""An approved relation moves one of its two shapes in the CAD export, when the user chooses so.

The owner's decision after ``docs/FEATURE_PLAN.md`` C3: an approved relation ("parallel", an angle,
a distance, "coaxial", "in one plane") is by default only listed in the export's notes; the user
may instead choose that its first shape (``"a"``) or its second (``"b"``) moves to suit the approved
values. :func:`apply_moves` makes those moves on *copies* of the kept fits, for the STEP file and the
macro. The measured fits, and everything given in, stay as they are; the part's frame, built from
the measured fits, does not change either.

**What "move to suit" means: the smallest change.** The shape that moves (M) is turned about its
own point (a plane's point, a cylinder's point on its axis, a hole's centre) and slid; the other
(F) stays where it is. The values are ``ohs.measure.relate``'s, so each rule follows what a value
means there for that pair of kinds:

* **an angle** (or its word: "parallel" is 0°, "perpendicular" 90°): M's direction (a plane's or a
  hole's normal, a cylinder's axis) is turned about M's point, in the plane of the two directions,
  by the difference between the angle measured and the one approved: the smallest turn that gives
  it. For two planes or two axes the angle is between their directions; for a plane and an axis it
  is the axis's angle to the plane (90° minus the angle between the normal and the axis), so
  "perpendicular" turns the normal along the axis and "parallel" turns it square to it. When the two
  directions are exactly parallel and another angle is approved there is no one nearest way to
  turn: refused;
* **a distance**, after the turn (when the angle is approved too), along the direction the
  distance is measured in, by the difference: two parallel planes along their mean normal; a plane
  and an axis (a hole's centre, or a cylinder along the plane) along the plane's normal; two
  parallel axes across their mean direction, towards or away from each other; two skew axes along
  the line square to both. The side M is on stays: a distance approved where the two are at no
  distance at all is refused (which side is not known), and so is one the pair has no longer after
  the turn (two planes turned to 20° apart have no one distance);
* **a centre distance** (two holes): alone, M slides along the line of the two centres; with the
  distance across the axes approved too, that is set first and M then slides along the axes (their
  mean direction, or M's own axis when they are skew), which leaves it as it is. A centre distance
  shorter than the distance across the axes cannot be reached that way: refused;
* **"coaxial" and "in one plane"** are an angle of 0 and a distance of 0 approved together: the turn
  and then the slide. With only the distance approved, M is slid but not turned (the angle stays as
  measured).

After the move the relation is worked out again with ``relate`` on the moved copies; every approved
value must read its nominal to :data:`MOVE_TOL` (degrees or mm), else nothing is moved and the
relation stays listed, with the reason.

**Several moves: the order and the conflicts.** One move can spoil another (A moved to suit B,
then B moved to suit C would undo the first). The rule, in the order the moves are given (the app
gives them in the order they were chosen):

1. a shape is moved by one relation only: a later relation that would move it again is refused;
2. a shape an applied relation was measured against (its F) stays where it is: a later relation
   that would move it is refused, as moving it would undo the earlier one;
3. a shape moved earlier may be the F of a later relation: the later one suits it as exported.

So every applied relation still holds when all are done, which is checked again at the end
(:attr:`Move.holds`). What cannot be applied is not applied at all, nothing half-moved: it stays
listed, and :attr:`Move.reason` says why in plain words.

**Check again.** An approval marked "check again" (the measurement changed and no longer allows it)
is applied as approved, with a warning, as an approved size is.

**What goes with a moved shape.** Its extent (a plane's basis and rectangle, a cylinder's zero
direction, a hole's edge points) and a plane's outline (its basis; the loops are 2D in it) are moved
with it, so the face is written where the shape now is. Its sizes and ± values do not change.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

import numpy as np

from ohs.measure.relate import _across_parallel, _line_angle_deg, _mean_direction, relate

__all__ = ["MOVE_TOL", "MOVE_SHAPES", "MOVE_VALUES", "Move", "Moves", "apply_moves"]

MOVE_TOL = 1e-9  # degrees or mm: a moved pair must read its approved values this closely
MOVE_SHAPES = ("a", "b")  # which of a relation's two fits moves
MOVE_VALUES = ("angle", "distance", "centre_distance")  # the relation values a move follows
_TINY = 1e-12  # a length or a sine this small is nothing

_POS = {"plane": "point", "cylinder": "point", "circle": "center"}
_DIR = {"plane": "normal", "cylinder": "axis", "circle": "normal"}


@dataclass
class Move:
    """What became of one relation's move (:func:`apply_moves`)."""

    id: str
    name: str
    shape: str  # "a" or "b": which of the two was to move
    moved_id: str | None = None
    moved_name: str | None = None
    fixed_id: str | None = None
    fixed_name: str | None = None
    applied: bool = False
    turn_deg: float = 0.0  # how far the moved shape was turned about its own point
    slide_mm: float = 0.0  # how far it was slid
    slide: list = field(default_factory=lambda: [0.0, 0.0, 0.0])  # the slide as a vector, in the fits' frame
    values: list = field(default_factory=list)  # each approved value: {"value", "nominal", "word", "exported"}
    holds: bool = False  # every approved value reads its nominal on the exported shapes, after all moves
    check_again: bool = False  # one of its approvals is marked "check again"
    reason: str | None = None  # why it was not applied, in plain words
    warnings: list = field(default_factory=list)

    @property
    def sentence(self) -> str:
        """What the move did, for the notes ("Bottom turned 0.3000 deg about its own point and slid
        0.0000 mm, to suit Top"); empty when it was not applied."""
        if not self.applied:
            return ""
        return (f"{self.moved_name} turned {self.turn_deg:.4f} deg about its own point and slid {self.slide_mm:.4f} mm, "
                f"to suit {self.fixed_name}")

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "shape": self.shape, "moved_id": self.moved_id,
                "moved": self.moved_name, "fixed_id": self.fixed_id, "to_suit": self.fixed_name,
                "applied": bool(self.applied), "turn_deg": float(self.turn_deg), "slide_mm": float(self.slide_mm),
                "slide": [float(x) for x in self.slide], "values": [dict(v) for v in self.values],
                "holds": bool(self.holds), "check_again": bool(self.check_again), "reason": self.reason,
                "warnings": list(self.warnings), "sentence": self.sentence}


@dataclass
class Moves:
    """What :func:`apply_moves` gives: the fits to export (the moved ones copies, the others as
    given), the planes' outlines (moved ones copies), and one :class:`Move` per relation that has a
    move and approved values, in the order given."""

    features: list
    outlines: dict
    moves: list


# --------------------------------------------------------------------------- small helpers
def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64).reshape(3)
    return v / np.linalg.norm(v)


def _vec(v) -> list[float]:
    return [float(x) for x in v]


def _num(x: float) -> str:
    return f"{float(x):.6f}".rstrip("0").rstrip(".")


def _turning(axis: np.ndarray, rad: float) -> np.ndarray:
    """The rotation by ``rad`` about the unit ``axis`` (right-handed)."""
    k = axis
    K = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    return np.eye(3) + math.sin(rad) * K + (1.0 - math.cos(rad)) * (K @ K)


def _kind(f: dict) -> str:
    return f["shape"]["kind"]


def _relate(fa: dict, fb: dict):
    return relate({"kind": fa["kind"], "shape": fa["shape"]}, {"kind": fb["kind"], "shape": fb["shape"]})


def _moved(f: dict, outline: dict | None, R: np.ndarray, pivot: np.ndarray, t: np.ndarray) -> tuple[dict, dict | None]:
    """A copy of the kept fit ``f`` (and of its outline) turned by ``R`` about ``pivot`` and slid by ``t``."""
    def pt(x):
        return _vec(pivot + R @ (np.asarray(x, dtype=np.float64) - pivot) + t)

    def dr(x):
        return _vec(R @ np.asarray(x, dtype=np.float64))

    out = copy.deepcopy(f)
    shape = out["shape"]
    k = shape["kind"]
    shape[_POS[k]] = pt(shape[_POS[k]])
    shape[_DIR[k]] = dr(shape[_DIR[k]])
    ext = out.get("extent")
    if isinstance(ext, dict):
        if ext.get("kind") == "plane":
            for key in ("origin",):
                if ext.get(key) is not None:
                    ext[key] = pt(ext[key])
            for key in ("u", "v"):
                if ext.get(key) is not None:
                    ext[key] = dr(ext[key])
            rect = ext.get("rectangle")
            if isinstance(rect, dict):
                if rect.get("center") is not None:
                    rect["center"] = pt(rect["center"])
                if rect.get("axes"):
                    rect["axes"] = [dr(a) for a in rect["axes"]]
        elif ext.get("kind") == "cylinder" and ext.get("zero") is not None:
            ext["zero"] = dr(ext["zero"])
        elif ext.get("kind") == "hole" and ext.get("edge"):
            ext["edge"] = [pt(p) for p in ext["edge"]]
    new_outline = None
    if isinstance(outline, dict):
        new_outline = copy.deepcopy(outline)
        if new_outline.get("kind") == "plane":
            new_outline["origin"] = pt(new_outline["origin"])
            new_outline["u"], new_outline["v"] = dr(new_outline["u"]), dr(new_outline["v"])
        elif new_outline.get("kind") == "hole" and new_outline.get("edge"):
            new_outline["edge"] = [pt(p) for p in new_outline["edge"]]
    return out, new_outline


# --------------------------------------------------------------------------- one relation's move
class _Refused(Exception):
    """A move that cannot be made, with the reason in plain words."""


def _target_lines_deg(kinds: tuple[str, str], angle: float) -> float:
    """The angle between the two directions (as lines) that gives the relation's ``angle``."""
    return 90.0 - angle if (kinds[0] == "plane") != (kinds[1] == "plane") else angle


def _turn(m: dict, f: dict, angle: float, names: tuple[str, str]) -> tuple[np.ndarray, float]:
    """The rotation of M's direction about its own point that gives the approved ``angle``, and how
    far it turns (degrees)."""
    d, g = _unit(m["shape"][_DIR[_kind(m)]]), _unit(f["shape"][_DIR[_kind(f)]])
    target = _target_lines_deg((_kind(m), _kind(f)), angle)
    beta = _line_angle_deg(d, g)
    phi = target - beta
    if abs(phi) <= 0.0:
        return np.eye(3), 0.0
    gp = g if float(d @ g) >= 0.0 else -g
    k = np.cross(gp, d)
    if np.linalg.norm(k) < _TINY:
        raise _Refused(f"the directions of {names[0]} and {names[1]} are exactly parallel: there is no one nearest way "
                       f"to turn {names[0]} to {_num(angle)} deg")
    return _turning(k / np.linalg.norm(k), math.radians(phi)), abs(phi)


def _distance_along(fa: dict, fb: dict, parallel: bool) -> tuple[float, np.ndarray | None]:
    """The signed distance ``d`` of the pair as ``relate`` measures it (its value is ``|d|``) and the
    unit direction ``e`` with ``d = e . (pos_b - pos_a)`` near the pair as it is: moving b by ``s e``
    (or a by ``-s e``) changes ``d`` by ``s``. ``e`` is None for two parallel axes at no distance."""
    ka, kb = _kind(fa), _kind(fb)
    pa, pb = np.asarray(fa["shape"][_POS[ka]], float), np.asarray(fb["shape"][_POS[kb]], float)
    da, db = _unit(fa["shape"][_DIR[ka]]), _unit(fb["shape"][_DIR[kb]])
    w = pb - pa
    if ka == "plane" and kb == "plane":
        e = _mean_direction(da, db)
    elif ka == "plane":
        e = da
    elif kb == "plane":
        e = -db
    elif parallel:
        across = _across_parallel(pa, da, pb, db)
        d = float(np.linalg.norm(across))
        return d, (across / d if d > _TINY else None)
    else:
        c = np.cross(da, db)
        e = c / np.linalg.norm(c)
    return float(e @ w), e


def _one(rel: dict, fa: dict, fb: dict, outlines: dict, mv: Move) -> tuple[dict, dict | None]:
    """Move M of one relation (module docstring); returns M's moved copy and its outline. Raises
    :class:`_Refused`."""
    approved = {n["value"]: n for n in rel.get("nominals") or [] if n.get("value") in MOVE_VALUES}
    m, f = (fa, fb) if mv.shape == "a" else (fb, fa)
    names = (m["name"], f["name"])
    mk = _kind(m)
    pivot = np.asarray(m["shape"][_POS[mk]], dtype=np.float64)
    R, turn = np.eye(3), 0.0
    if "angle" in approved:
        R, turn = _turn(m, f, float(approved["angle"]["nominal"]), names)
    moved, _ = _moved(m, None, R, pivot, np.zeros(3))
    total = np.zeros(3)
    sign = 1.0 if mv.shape == "b" else -1.0  # moving a by -s e changes the distance as moving b by s e

    def pair():
        return (moved, f) if mv.shape == "a" else (f, moved)

    def slide(v: np.ndarray) -> None:
        nonlocal moved, total
        moved, _ = _moved(moved, None, np.eye(3), pivot, v)
        total = total + v

    if "distance" in approved:
        D = float(approved["distance"]["nominal"])
        r = _relate(*pair())
        if r.get("distance") is None:
            raise _Refused(f"after the turn {names[0]} and {names[1]} have no one distance (they are not parallel), "
                           "so the approved distance cannot be set")
        d, e = _distance_along(*pair(), r.parallel)
        if e is None or (abs(d) <= _TINY and D > 0.0):
            raise _Refused(f"{names[0]} and {names[1]} are at no distance now: which side to slide {names[0]} to is not "
                           "known")
        target = math.copysign(D, d) if abs(d) > _TINY else 0.0
        slide(sign * (target - d) * e)
    if "centre_distance" in approved:
        C = float(approved["centre_distance"]["nominal"])
        a2, b2 = pair()
        ca, cb = np.asarray(a2["shape"]["center"], float), np.asarray(b2["shape"]["center"], float)
        w = cb - ca
        if "distance" in approved:  # along the axes, which keeps the distance across them
            r = _relate(a2, b2)
            u = (_mean_direction(_unit(a2["shape"]["normal"]), _unit(b2["shape"]["normal"])) if r.parallel
                 else _unit(moved["shape"]["normal"]))
            g = float(w @ u)
            disc = g * g - float(w @ w) + C * C
            if disc < -1e-9:
                across = math.sqrt(max(0.0, float(w @ w) - g * g))
                raise _Refused(f"the centre distance {_num(C)} mm cannot be reached by sliding along the axes: they are "
                               f"{across:.4f} mm apart across them")
            root = math.sqrt(max(disc, 0.0))
            t = min((-g + root, -g - root), key=abs)
            slide(sign * t * u)
        else:  # along the line of the two centres
            d = float(np.linalg.norm(w))
            if d <= _TINY:
                raise _Refused(f"the centres of {names[0]} and {names[1]} coincide: which way to slide {names[0]} is not "
                               "known")
            slide(sign * (C - d) * (w / d))
    # the pair as exported must read every approved value
    r = _relate(*pair())
    mv.values = []
    for value, n in approved.items():
        q = r.get(value)
        mv.values.append({"value": value, "nominal": float(n["nominal"]), "word": n.get("word"),
                          "exported": None if q is None else float(q.value)})
        if q is None or abs(q.value - float(n["nominal"])) > MOVE_TOL:
            got = "nothing" if q is None else f"{q.value:.9f}"
            raise _Refused(f"the move does not reach the approved {value.replace('_', ' ')} {_num(n['nominal'])} "
                           f"(it would read {got})")
    mv.turn_deg, mv.slide_mm, mv.slide = float(turn), float(np.linalg.norm(total)), _vec(total)
    return _moved(m, outlines.get(m["id"]), R, pivot, total)


def _shape_of(rel: dict) -> str | None:
    """The shape a relation moves ("a", "b"), or None (listed only). ValueError for anything else."""
    move = rel.get("move")
    if isinstance(move, dict):
        move = move.get("shape")
    if move in (None, "listed"):
        return None
    if move not in MOVE_SHAPES:
        raise ValueError(f"a relation's move is 'a', 'b' or none (listed only), got {move!r}")
    return move


# --------------------------------------------------------------------------- all of them
def apply_moves(features, relations, outlines=None) -> Moves:
    """Move shapes to suit the approved relations whose move is chosen (module docstring).

    ``features``: the kept fits as ``features.json`` holds them (``id``, ``name``, ``kind``,
    ``shape``, ``extent``), with approved sizes already in their shapes when wanted.
    ``relations``: kept relations (``id``, ``name``, ``a``, ``b``) with ``move`` ("a" or "b", or
    ``{"shape": "a" | "b", ...}`` as kept; none or "listed": listed only) and ``nominals`` (each
    ``{"value", "nominal", "word"?, "check_again"?}``), **in the order the moves are applied**.
    ``outlines``: a plane's id -> its outline, as :func:`ohs.cad.shapes.cad_shapes` takes them.
    Nothing given in is changed. ValueError for a move other than "a", "b" or listed."""
    outlines = dict(outlines or {})
    current = {f["id"]: f for f in features}
    moved_by: dict[str, str] = {}
    held_by: dict[str, str] = {}
    moves: list[Move] = []
    for rel in relations:
        shape = _shape_of(rel)
        approved = [n for n in rel.get("nominals") or [] if n.get("value") in MOVE_VALUES]
        if shape is None or not approved:
            continue
        mv = Move(id=rel["id"], name=rel["name"], shape=shape)
        mv.check_again = any(n.get("check_again") for n in approved)
        moves.append(mv)
        fa, fb = current.get(rel["a"]), current.get(rel["b"])
        if fa is None or fb is None:
            mv.reason = "one of its fits is not kept any more"
            continue
        m, f = (fa, fb) if shape == "a" else (fb, fa)
        mv.moved_id, mv.moved_name, mv.fixed_id, mv.fixed_name = m["id"], m["name"], f["id"], f["name"]
        if m["id"] in moved_by:
            mv.reason = f"{m['name']} is already moved by \"{moved_by[m['id']]}\": a shape is moved by one relation only"
            continue
        if m["id"] in held_by:
            mv.reason = (f"\"{held_by[m['id']]}\" was applied with {m['name']} where it is: moving {m['name']} now "
                         "would undo it")
            continue
        try:
            new, new_outline = _one(rel, fa, fb, outlines, mv)
        except _Refused as e:
            mv.reason = str(e)
            continue
        mv.applied = True
        current[m["id"]] = new
        if new_outline is not None:
            outlines[m["id"]] = new_outline
        moved_by[m["id"]] = rel["name"]
        held_by.setdefault(f["id"], rel["name"])
        for n in approved:
            if n.get("check_again"):
                what = n.get("word") or f"{n['value'].replace('_', ' ')} {_num(n['nominal'])}"
                mv.warnings.append(f"\"{rel['name']}\": its approved {what} is marked check again: {m['name']} is moved "
                                   "to suit it as approved; approve it again or remove it")
    # every applied relation must still hold on the shapes as exported (the rule above sees to it)
    for mv in moves:
        if not mv.applied:
            continue
        rel = next(r for r in relations if r["id"] == mv.id)
        r = _relate(current[rel["a"]], current[rel["b"]])
        mv.holds = all(r.get(v["value"]) is not None and abs(r.get(v["value"]).value - v["nominal"]) <= MOVE_TOL
                       for v in mv.values)
        if not mv.holds:
            mv.warnings.append(f"\"{mv.name}\" no longer reads its approved values on the exported shapes")
    return Moves(features=[current[f["id"]] for f in features], outlines=outlines, moves=moves)
