"""Kept features: the fits, measurements and relations a user keeps with a scan.

A fit made in the viewer is gone when the page closes unless it is written down. This module is
that record: ``scans/<sid>/features.json``, read, checked and written here, with the region of
each fit beside it in ``scans/<sid>/features/<id>.npy``. It knows nothing of the server or the
mesh; :mod:`ohs.server.features` fits and refits through the fit route and keeps the result here.

**The file** (``format_version`` 1)::

    {"format_version": 1,
     "frame": null,                  # the part's coordinate system: PartFrame.to_dict() (ohs.measure.frame), or null
     "features": [{"id": "f-…", "name": "Fit 1", "kind": "plane" | "cylinder" | "hole",
                   "shape": {...},   # exactly as the fit route returns it, with its ± values
                   "seed": {"kind", "face", "point", "tolerance_mm"},   # the click, to fit it again
                   "n_faces", "tolerance_mm", "mean_sigma_mm", "correlated_points", "warnings",
                   "mesh_version",   # the mesh version it was fitted on
                   "extent": {...} | null,   # where its region lies: the small part (below)
                   "form": "boss" | "bore" | null,   # a cylinder's (below); null for a plane or a hole
                   "created"}],
     "measurements": [{"id": "m-…", "name": "Distance 1", "a": [x, y, z], "b": [x, y, z],
                       "mesh_version", "created"}],
     "relations": [{"id": "r-…", "name": "Relation 1", "a": "<feature id>", "b": "<feature id>",
                    "created"}]}

A relation keeps only which two features the user asked to relate: its numbers are worked out
when it is shown, so they always match the features. Ids are short and safe as file names; they
are unique across the three lists.

**The regions** are large (up to a million face indices), so they stay out of the JSON, one
``.npy`` of sorted ``uint32`` face indices per feature, as the processing studio keeps its
selections (``process/selections/<id>.npy``). A region is only valid on the mesh version its
feature was fitted on.

**The extent** (FEATURE_PLAN K2) says where a fit's region lies on its shape:
:attr:`ohs.measure.region.RegionFit.extent`, split by :func:`split_extent`. ``features.json`` is read
and sent whole on every ``GET .../features``, and a scan's plane outline can be large (a scanned
table: about 60,000 points in 722 loops), so the file keeps only the small part:

* plane: ``{"kind": "plane", "origin", "u", "v", "rectangle": {"center", "axes", "half_sizes_mm"},
  "area_mm2", "n_outer", "n_holes", "n_points"}``: the plane's 2D basis, the smallest rectangle round
  the outline, its area, how many outer loops and holes it has and how many points they hold;
* cylinder: as the engine gives it, ``{"kind": "cylinder", "along_mm", "length_mm", "zero",
  "start_deg", "arc_deg"}``;
* hole: ``{"kind": "hole", "n_edge", "edge"}``: the edge's points when there are at most
  :data:`EDGE_MAX_POINTS`, else ``edge`` null.

The rest, a plane's outline loops (and a long hole edge), goes to ``features/<id>.outline.npz``
beside the region file (:func:`save_outline`), valid on the same mesh version as the region. The
loops stay apart: their points one after the other (``outer_points``, ``holes_points``, 2D in the
plane's basis) and each loop's length (``outer_sizes``, ``holes_sizes``). ``extent`` null is a fit
kept before extents existed (or whose extent could not be kept): it loads and shows as before.
:func:`validate` checks the small part's shape; keys it does not know are kept.

**The form** says whether a kept cylinder is a boss (its surface faces away from the axis: a
shaft, a pin) or a bore (a hole's wall): :attr:`ohs.measure.region.RegionFit.form`, set when the
fit is kept and when it is fitted again. The suggested nominal sizes use it
(:func:`ohs.cad.nominal.suggest_fit`): the two are biased differently. Null when the fit could not
tell, and for a plane or a hole; a feature kept before forms existed has no ``form`` key and
loads as it is. :func:`validate` refuses any other value, and a form on a plane or a hole.

**The part's frame** (``frame``) is written by :mod:`ohs.server.features` from
:func:`ohs.measure.frame.part_frame`. :func:`validate` passes it through as it is (an object or
null), so a frame this version cannot read (a later ``format_version``, a damaged matrix) never
stops the features from loading, and is not stripped when the file is written again;
:func:`frame_state` says whether it can be read and whether it is still current.

**Writing** is atomic: a temporary file next to the real one, then a rename, so a crash or a full
disk leaves the old file whole. A document that does not pass :func:`validate` is never written.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import math
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np

__all__ = ["FORMAT_VERSION", "FILE_NAME", "FACES_DIR", "KINDS", "SHAPE_OF", "FORMS", "NAME_MAX", "ID_RE", "empty",
           "validate", "load", "save", "faces_path", "save_faces", "load_faces", "remove_faces", "new_id",
           "next_name", "now", "frame_state", "EDGE_MAX_POINTS", "split_extent", "outline_path", "save_outline",
           "load_outline", "remove_outline"]

FORMAT_VERSION = 1
FILE_NAME = "features.json"
FACES_DIR = "features"
OUTLINE_SUFFIX = ".outline.npz"
#: a hole's edge with at most this many points is kept in features.json (a few kB), a longer one in its own file
EDGE_MAX_POINTS = 128
#: the kinds of fit (as the fit route names them) and the shape each one gives
KINDS = ("plane", "cylinder", "hole")
SHAPE_OF = {"plane": "plane", "cylinder": "cylinder", "hole": "circle"}
#: what a kept cylinder is (``form``; null: not known, and always for a plane or a hole)
FORMS = ("boss", "bore")
NAME_MAX = 80
#: an id: letters, digits, "-" and "_" (it names a file)
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def empty() -> dict:
    """The set of a scan that has none kept yet."""
    return {"format_version": FORMAT_VERSION, "frame": None, "features": [], "measurements": [], "relations": []}


def now() -> str:
    """The time a record is made, as the app writes times (local, to the second)."""
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    """A fresh id: ``f-`` for a feature, ``m-`` for a measurement, ``r-`` for a relation."""
    return f"{prefix}-{secrets.token_hex(5)}"


def next_name(items: Iterable[dict], stem: str) -> str:
    """The default name of the next item: "<stem> N", N one more than the highest such name and
    than the number of items (so a deleted "Fit 2" does not come back next to a "Fit 3")."""
    items = list(items)
    pat = re.compile(rf"{re.escape(stem)} (\d+)")
    top = len(items)
    for it in items:
        m = pat.fullmatch(str(it.get("name", "")).strip())
        if m:
            top = max(top, int(m.group(1)))
    return f"{stem} {top + 1}"


# --------------------------------------------------------------------------- checks
def _fail(where: str, what: str) -> None:
    raise ValueError(f"{where}: {what}")


def _check_id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        _fail(where, f"id must be 1 to 64 letters, digits, '-' or '_', got {value!r}")
    return value


def clean_name(value: Any, where: str = "name") -> str:
    """A name as kept: trimmed, not empty, at most :data:`NAME_MAX` characters."""
    if not isinstance(value, str):
        _fail(where, "name must be text")
    name = value.strip()
    if not name:
        _fail(where, "name must not be empty")
    if len(name) > NAME_MAX:
        _fail(where, f"name must be at most {NAME_MAX} characters, got {len(name)}")
    return name


def _point(value: Any, where: str, what: str) -> list[float]:
    ok = isinstance(value, (list, tuple)) and len(value) == 3 and all(
        isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in value)
    if not ok:
        _fail(where, f"{what} must be three finite numbers, got {value!r}")
    return [float(x) for x in value]


def _whole(value: Any, where: str, what: str, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _fail(where, f"{what} must be a whole number of at least 0, got {value!r}")
    return value


def _number(value: Any, where: str, what: str, positive: bool = False) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or (
            positive and value <= 0):
        _fail(where, f"{what} must be {'a positive' if positive else 'a finite'} number or null, got {value!r}")
    return value


def _text(value: Any, where: str, what: str) -> str:
    if not isinstance(value, str):
        _fail(where, f"{what} must be text, got {value!r}")
    return value


def _extent(e: Any, where: str) -> None:
    """Check a stored extent (the small part, module docstring "The extent"); null is allowed."""
    if e is None:
        return
    if not isinstance(e, dict):
        _fail(where, "extent must be an object or null")

    def need(ok: bool, what: str) -> None:
        if not ok:
            _fail(where, f"extent: {what}")

    def finite(x: Any, low: float | None = None) -> bool:
        return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and (low is None or x >= low)

    def vec(x: Any, n: int = 3) -> bool:
        return isinstance(x, (list, tuple)) and len(x) == n and all(finite(c) for c in x)

    def whole(x: Any) -> bool:
        return isinstance(x, int) and not isinstance(x, bool) and x >= 0

    kind = e.get("kind")
    if kind == "plane":
        for key in ("origin", "u", "v"):
            need(vec(e.get(key)), f"a plane's {key} must be three finite numbers")
        rect = e.get("rectangle")
        need(isinstance(rect, dict), "a plane's rectangle must be an object")
        need(vec(rect.get("center")), "the rectangle's center must be three finite numbers")
        axes = rect.get("axes")
        need(isinstance(axes, list) and len(axes) == 2 and all(vec(a) for a in axes),
             "the rectangle's axes must be two directions of three numbers")
        half = rect.get("half_sizes_mm")
        need(isinstance(half, list) and len(half) == 2 and all(finite(h, 0.0) for h in half),
             "the rectangle's half_sizes_mm must be two numbers of at least 0")
        need(finite(e.get("area_mm2"), 0.0), "a plane's area_mm2 must be a number of at least 0")
        for key in ("n_outer", "n_holes", "n_points"):
            need(whole(e.get(key, 0)), f"a plane's {key} must be a whole number of at least 0")
    elif kind == "cylinder":
        along = e.get("along_mm")
        need(isinstance(along, list) and len(along) == 2 and all(finite(a) for a in along),
             "a cylinder's along_mm must be two finite numbers")
        need(finite(e.get("length_mm"), 0.0), "a cylinder's length_mm must be a number of at least 0")
        need(vec(e.get("zero")), "a cylinder's zero must be three finite numbers")
        for key in ("start_deg", "arc_deg"):
            need(finite(e.get(key)), f"a cylinder's {key} must be a finite number")
    elif kind == "hole":
        need(whole(e.get("n_edge", 0)), "a hole's n_edge must be a whole number of at least 0")
        edge = e.get("edge")
        need(edge is None or (isinstance(edge, list) and all(vec(p) for p in edge)),
             "a hole's edge must be a list of points of three finite numbers, or null")
    else:
        _fail(where, f"extent: kind must be one of {', '.join(KINDS)}, got {kind!r}")


def split_extent(extent: dict | None) -> tuple[dict | None, dict | None]:
    """An extent as :func:`ohs.measure.region.region_extent` gives it, split into ``(the small part
    kept in features.json, the large part for the side file or None)``; module docstring, "The
    extent". ``(None, None)`` for None. Raises ValueError for a kind it does not know."""
    if extent is None:
        return None, None
    kind = extent.get("kind")
    if kind == "plane":
        outer, holes = list(extent.get("outer", [])), list(extent.get("holes", []))
        basis = {"origin": extent["origin"], "u": extent["u"], "v": extent["v"]}
        small = {"kind": "plane", **basis, "rectangle": extent["rectangle"], "area_mm2": extent["area_mm2"],
                 "n_outer": len(outer), "n_holes": len(holes), "n_points": sum(len(x) for x in outer + holes)}
        return small, {"kind": "plane", **basis, "outer": outer, "holes": holes}
    if kind == "cylinder":
        return dict(extent), None
    if kind == "hole":
        edge = list(extent.get("edge") or [])
        if len(edge) <= EDGE_MAX_POINTS:
            return {"kind": "hole", "n_edge": len(edge), "edge": edge}, None
        return {"kind": "hole", "n_edge": len(edge), "edge": None}, {"kind": "hole", "edge": edge}
    raise ValueError(f"extent: kind must be one of {', '.join(KINDS)}, got {kind!r}")


def _feature(f: Any, i: int) -> dict:
    where = f"features[{i}]"
    if not isinstance(f, dict):
        _fail(where, "must be an object")
    out = dict(f)
    out["id"] = _check_id(f.get("id"), where)
    out["name"] = clean_name(f.get("name"), where)
    kind = f.get("kind")
    if kind not in KINDS:
        _fail(where, f"kind must be one of {', '.join(KINDS)}, got {kind!r}")
    shape = f.get("shape")
    if not isinstance(shape, dict) or shape.get("kind") != SHAPE_OF[kind]:
        _fail(where, f"shape must be the {SHAPE_OF[kind]} the fit returned")
    seed = f.get("seed")
    if not isinstance(seed, dict):
        _fail(where, "seed must be an object (the click it was grown from)")
    if seed.get("kind", kind) != kind:
        _fail(where, f"seed kind {seed.get('kind')!r} is not the feature's kind {kind!r}")
    out["seed"] = {**seed, "kind": kind, "face": _whole(seed.get("face"), where, "seed face"),
                   "point": _point(seed.get("point"), where, "seed point"),
                   "tolerance_mm": _number(seed.get("tolerance_mm"), where, "seed tolerance_mm", positive=True)}
    out["mesh_version"] = _whole(f.get("mesh_version"), where, "mesh_version")
    out["n_faces"] = _whole(f.get("n_faces", 0), where, "n_faces")
    for key in ("tolerance_mm", "mean_sigma_mm", "correlated_points"):
        out[key] = _number(f.get(key), where, key)
    warnings = f.get("warnings", [])
    if not isinstance(warnings, list) or not all(isinstance(w, str) for w in warnings):
        _fail(where, "warnings must be a list of text")
    _extent(f.get("extent"), where)
    if "form" in f:  # a file written before forms existed has none: it loads as it is
        form = f["form"]
        if form is not None and (not isinstance(form, str) or form not in FORMS):
            _fail(where, f"form must be 'boss', 'bore' or null, got {form!r}")
        if form is not None and kind != "cylinder":
            _fail(where, f"only a cylinder is a boss or a bore, and this feature is a {kind}")
    out["created"] = _text(f.get("created", ""), where, "created")
    return out


def _measurement(m: Any, i: int) -> dict:
    where = f"measurements[{i}]"
    if not isinstance(m, dict):
        _fail(where, "must be an object")
    out = dict(m)
    out["id"] = _check_id(m.get("id"), where)
    out["name"] = clean_name(m.get("name"), where)
    out["a"] = _point(m.get("a"), where, "a")
    out["b"] = _point(m.get("b"), where, "b")
    out["mesh_version"] = _whole(m.get("mesh_version"), where, "mesh_version", allow_none=True)
    out["created"] = _text(m.get("created", ""), where, "created")
    return out


def _relation(r: Any, i: int, feature_ids: set[str]) -> dict:
    where = f"relations[{i}]"
    if not isinstance(r, dict):
        _fail(where, "must be an object")
    out = dict(r)
    out["id"] = _check_id(r.get("id"), where)
    out["name"] = clean_name(r.get("name"), where)
    for key in ("a", "b"):
        ref = r.get(key)
        if ref not in feature_ids:
            _fail(where, f"{key} names no kept feature: {ref!r}")
    if r["a"] == r["b"]:
        _fail(where, "a feature cannot be related to itself")
    out["created"] = _text(r.get("created", ""), where, "created")
    return out


def validate(doc: Any) -> dict:
    """Check a features document and return a clean copy (names trimmed, numbers as floats where
    they are points). Raises ValueError with a plain message saying where and what.

    ``frame`` is null or an object and is passed through as it is; fields this version does not
    know are kept, so a file written by a later version is not stripped by an earlier one."""
    if not isinstance(doc, dict):
        raise ValueError("the features document must be an object")
    fv = doc.get("format_version")
    if not isinstance(fv, int) or isinstance(fv, bool) or fv < 1:
        raise ValueError(f"format_version must be {FORMAT_VERSION}, got {fv!r}")
    if fv > FORMAT_VERSION:
        raise ValueError(f"format_version {fv} was written by a newer version of OpenHandScan (this one reads "
                         f"{FORMAT_VERSION})")
    frame = doc.get("frame")
    if frame is not None and not isinstance(frame, dict):
        raise ValueError(f"frame must be an object or null, got {type(frame).__name__}")
    for key in ("features", "measurements", "relations"):
        if not isinstance(doc.get(key), list):
            raise ValueError(f"{key} must be a list")
    out = copy.deepcopy(doc)
    out["features"] = [_feature(f, i) for i, f in enumerate(doc["features"])]
    out["measurements"] = [_measurement(m, i) for i, m in enumerate(doc["measurements"])]
    fids = {f["id"] for f in out["features"]}
    out["relations"] = [_relation(r, i, fids) for i, r in enumerate(doc["relations"])]
    seen: set[str] = set()
    for item in out["features"] + out["measurements"] + out["relations"]:
        if item["id"] in seen:
            raise ValueError(f"the id {item['id']!r} is used twice")
        seen.add(item["id"])
    return out


def frame_state(frame: Any, features: list[dict]) -> dict | None:
    """Whether the kept part's frame can be read and is still current, for showing it.

    Returns None without a frame, else ``{"readable", "out_of_date", "out_of_date_reasons"}``.
    The frame is *out of date* when a fit it was built from has been deleted, or its shape is no
    longer the one the frame was built from (fitted again, or changed): the shapes kept in the
    frame's ``built_from`` are compared with the kept fits of the same id. A rename is not a change;
    a point given as the origin has no id and never goes out of date. A frame that cannot be read
    (:meth:`PartFrame.from_dict <ohs.measure.frame.PartFrame.from_dict>` refuses it, e.g. a later
    ``format_version``) is shown as not readable and out of date, with the reason. Nothing is
    rebuilt here."""
    if frame is None:
        return None
    from ohs.measure.frame import PartFrame

    try:
        built_from = PartFrame.from_dict(frame).built_from
        if not isinstance(built_from, list) or not all(isinstance(x, dict) for x in built_from):
            raise ValueError("its built_from must be a list of objects")
    except (ValueError, TypeError, AttributeError, KeyError) as e:
        return {"readable": False, "out_of_date": True,
                "out_of_date_reasons": [f"this version cannot read the part's frame: {e}"]}
    by_id = {f.get("id"): f for f in features}
    reasons = []
    for slot in built_from:
        fid = slot.get("id")
        if fid is None:
            continue
        role = slot.get("role") or "fit"
        f = by_id.get(fid)
        if f is None:
            reasons.append(f"the {role} \"{slot.get('name') or fid}\" was deleted")
        elif f.get("shape") != slot.get("fit"):
            reasons.append(f"the {role} \"{f.get('name') or fid}\" was fitted again or changed since")
    return {"readable": True, "out_of_date": bool(reasons), "out_of_date_reasons": reasons}


# --------------------------------------------------------------------------- files
def _retry(fn, what: str) -> Any:
    for attempt in range(50):  # Windows: a reader may hold the file for a moment
        try:
            return fn()
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.05)
    raise RuntimeError(what)  # pragma: no cover


def load(scan_dir: str | Path) -> dict:
    """The kept features of a scan (``<scan_dir>/features.json``), checked; an empty set when the
    file is not there. Raises ValueError for a damaged or invalid file (it is left as it is)."""
    path = Path(scan_dir) / FILE_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty()
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise ValueError(f"{FILE_NAME} cannot be read: {e}") from None
    try:
        return validate(doc)
    except ValueError as e:
        raise ValueError(f"{FILE_NAME}: {e}") from None


def save(scan_dir: str | Path, doc: Any) -> dict:
    """Check ``doc`` and write it to ``<scan_dir>/features.json`` atomically (a temporary file,
    then a rename). Returns the checked copy that was written. Nothing is written when the
    check fails, and a failed write leaves the old file whole and no temporary file behind."""
    clean = validate(doc)
    path = Path(scan_dir) / FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{FILE_NAME}.tmp-{secrets.token_hex(4)}")
    try:
        tmp.write_text(json.dumps(clean, indent=1), encoding="utf-8")
        _retry(lambda: os.replace(tmp, path), f"replacing {path}")
    finally:
        if tmp.exists():
            tmp.unlink()
    return clean


def faces_path(scan_dir: str | Path, fid: str) -> Path:
    """Where a feature's region is kept: ``<scan_dir>/features/<id>.npy``."""
    return Path(scan_dir) / FACES_DIR / f"{_check_id(fid, 'faces')}.npy"


def save_faces(scan_dir: str | Path, fid: str, faces: Any) -> Path:
    """Keep a feature's region: its face indices, sorted, each once, as uint32 (atomic write)."""
    path = faces_path(scan_dir, fid)
    arr = np.unique(np.asarray(faces, dtype=np.int64).reshape(-1))
    if len(arr) and (arr[0] < 0 or arr[-1] > np.iinfo(np.uint32).max):
        raise ValueError("face indices must be at least 0")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.stem}.tmp-{secrets.token_hex(4)}.npy")
    try:
        np.save(tmp, arr.astype(np.uint32))
        _retry(lambda: os.replace(tmp, path), f"replacing {path}")
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def load_faces(scan_dir: str | Path, fid: str) -> np.ndarray | None:
    """A feature's region (uint32 face indices, sorted), or None when it has none kept."""
    try:
        return np.load(faces_path(scan_dir, fid)).astype(np.uint32, copy=False)
    except FileNotFoundError:
        return None


def remove_faces(scan_dir: str | Path, fid: str) -> None:
    """Forget a feature's region (nothing happens when there is none)."""
    try:
        _retry(lambda: faces_path(scan_dir, fid).unlink(), "removing a region")
    except FileNotFoundError:
        pass


def outline_path(scan_dir: str | Path, fid: str) -> Path:
    """Where a feature's outline is kept: ``<scan_dir>/features/<id>.outline.npz``."""
    return Path(scan_dir) / FACES_DIR / f"{_check_id(fid, 'outline')}{OUTLINE_SUFFIX}"


def _loops(loops: list, dim: int) -> tuple[np.ndarray, np.ndarray]:
    """Loops (lists of points) as ``(their points one after the other (n, dim), each loop's length)``."""
    sizes = np.array([len(x) for x in loops], dtype=np.int64)
    pts = np.asarray([p for x in loops for p in x], dtype=np.float64).reshape(-1, dim)
    return pts, sizes


def _unloops(pts: np.ndarray, sizes: np.ndarray) -> list:
    ends = np.cumsum(sizes)
    return [pts[e - n: e].tolist() for e, n in zip(ends.tolist(), sizes.tolist())]


def save_outline(scan_dir: str | Path, fid: str, side: dict, mesh_version: int) -> Path:
    """Keep the large part of a feature's extent (:func:`split_extent`): a plane's outline loops or
    a long hole edge, with the mesh version it is valid on (atomic write; module docstring)."""
    kind = side.get("kind")
    arrays: dict[str, np.ndarray] = {"kind": np.array(kind), "mesh_version": np.array(int(mesh_version), dtype=np.int64)}
    if kind == "plane":
        for key in ("origin", "u", "v"):
            arrays[key] = np.asarray(side[key], dtype=np.float64).reshape(3)
        arrays["outer_points"], arrays["outer_sizes"] = _loops(side["outer"], 2)
        arrays["holes_points"], arrays["holes_sizes"] = _loops(side["holes"], 2)
    elif kind == "hole":
        arrays["edge"] = np.asarray(side["edge"], dtype=np.float64).reshape(-1, 3)
    else:
        raise ValueError(f"outline: kind must be plane or hole, got {kind!r}")
    path = outline_path(scan_dir, fid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{fid}.tmp-{secrets.token_hex(4)}{OUTLINE_SUFFIX}")
    try:
        with open(tmp, "wb") as fh:
            np.savez(fh, **arrays)
        _retry(lambda: os.replace(tmp, path), f"replacing {path}")
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def load_outline(scan_dir: str | Path, fid: str) -> dict | None:
    """A feature's outline as plain JSON, or None when it has none kept: ``{"kind": "plane",
    "origin", "u", "v", "outer", "holes", "mesh_version"}`` (the loops as ``region_extent`` gives
    them) or ``{"kind": "hole", "edge", "mesh_version"}``."""
    try:
        with np.load(outline_path(scan_dir, fid), allow_pickle=False) as z:
            kind = str(z["kind"])
            out: dict[str, Any] = {"kind": kind}
            if kind == "plane":
                for key in ("origin", "u", "v"):
                    out[key] = z[key].tolist()
                out["outer"] = _unloops(z["outer_points"], z["outer_sizes"])
                out["holes"] = _unloops(z["holes_points"], z["holes_sizes"])
            else:
                out["edge"] = z["edge"].tolist()
            out["mesh_version"] = int(z["mesh_version"])
            return out
    except FileNotFoundError:
        return None


def remove_outline(scan_dir: str | Path, fid: str) -> None:
    """Forget a feature's outline (nothing happens when there is none)."""
    try:
        _retry(lambda: outline_path(scan_dir, fid).unlink(), "removing an outline")
    except FileNotFoundError:
        pass
