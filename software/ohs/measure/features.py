"""Kept features: the fits, measurements and relations a user keeps with a scan.

A fit made in the viewer is gone when the page closes unless it is written down. This module is
that record: ``scans/<sid>/features.json``, read, checked and written here, with the region of
each fit beside it in ``scans/<sid>/features/<id>.npy``. It knows nothing of the server or the
mesh; :mod:`ohs.server.features` fits and refits through the fit route and keeps the result here.

**The file** (``format_version`` 1)::

    {"format_version": 1,
     "frame": null,                  # the part's coordinate system (a later task): kept as given
     "features": [{"id": "f-…", "name": "Fit 1", "kind": "plane" | "cylinder" | "hole",
                   "shape": {...},   # exactly as the fit route returns it, with its ± values
                   "seed": {"kind", "face", "point", "tolerance_mm"},   # the click, to fit it again
                   "n_faces", "tolerance_mm", "mean_sigma_mm", "correlated_points", "warnings",
                   "mesh_version",   # the mesh version it was fitted on
                   "extent": null,   # its outline or span (a later task fills it)
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

__all__ = ["FORMAT_VERSION", "FILE_NAME", "FACES_DIR", "KINDS", "SHAPE_OF", "NAME_MAX", "ID_RE", "empty",
           "validate", "load", "save", "faces_path", "save_faces", "load_faces", "remove_faces", "new_id",
           "next_name", "now"]

FORMAT_VERSION = 1
FILE_NAME = "features.json"
FACES_DIR = "features"
#: the kinds of fit (as the fit route names them) and the shape each one gives
KINDS = ("plane", "cylinder", "hole")
SHAPE_OF = {"plane": "plane", "cylinder": "cylinder", "hole": "circle"}
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
    extent = f.get("extent")
    if extent is not None and not isinstance(extent, dict):
        _fail(where, "extent must be an object or null")
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
