"""The processing studio (APP_API.md "M3 phase 2"): stages, mesh edits with undo / redo, recipes,
inspection overlays.

**Model.** A scan's processing is five stages, each a job that ends in a new *base mesh*:

* ``track`` (``process_track``): :func:`ohs.app.process.process_recording` at a quality
  (``standard``, the default: 30 processed frames per second of scanning; ``best``: every frame;
  ``preview``). The old name ``full`` is accepted and read as ``standard``, in requests, stored
  state and saved recipes. The later stages fuse at the track's quality and given poses, so they
  fuse exactly the frames the track processed;
* ``straighten`` (``process_straighten``): loop closure
  (:func:`ohs.track.posegraph.straighten_trajectory`, imported lazily: while it is missing or when
  it fails, the stage is ``skipped`` with a plain summary and the base stays), then a fuse at the
  corrected poses; then the laser crossings (:func:`ohs.track.curve_ba.laser_curve_adjustment`,
  against that mesh, or the track's without loops), and when they change the poses, a second fuse.
  When neither changes anything (no loop, or loops showing the passes already line up, and
  crossings that already meet), it is ``skipped``, with the engine's verdict as the summary (e.g.
  "No loops found: ..."), and the edits stay;
* ``fuse`` (``process_fuse``): ``process_recording(poses=...)`` at the current poses (the
  straightened ones once straighten is done) and a chosen voxel size;
* ``spacetime`` (``process_spacetime``, "Refine from the images", a trial build): the base's stage
  mesh measured against up to 120 frames of the recording (:func:`ohs.track.spacetime.refine_from_images`:
  small patches searched along their normals for where the two images of every frame agree).
  ``skipped`` (base and edits unchanged) when too few patches hold dots in enough frames;
* ``refine`` (``process_refine``, "Refine with lasers"): the base's stage mesh pulled onto that
  stage's laser cloud (:func:`ohs.mesh.anchor_to_points`, a smooth correction along the normals
  where the lasers disagree with the dense surface by more than their own error). ``skipped``
  (base and edits unchanged) without laser points, or when the mesh already agrees with them.

An ordered list of *edits* (``mesh_edit`` jobs: :mod:`ohs.mesh` operations) applies to the base;
``cursor`` edits are active, and undo / redo move it. A new base clears the edits (face indices
no longer match), a new track also resets straighten, fuse and the refinements (they build on its
trajectory), a new straighten or fuse resets the refinements, and a new spacetime resets refine.
The *current mesh* (base + active edits) is always written to ``result/mesh.ply`` (vertex
normals), ``mesh.stl`` and, when ``[process] export`` asks for it, ``mesh.obj``, each replaced
atomically, and ``version`` goes up by one on every change. "Make mesh" (``process_scan``, M1)
and ``process_recipe`` jobs run the stages and ops of a :class:`Recipe`.

**Confidence.** When the stage's mesh carries each vertex's sigma (the PLY ``quality`` of the
confidence reconstruction), every step keeps it (:func:`ohs.mesh.carry_sigma`: filled holes
are "not measured", 50 mm), the stats get ``sigma_mm`` and ``result/mesh.ply`` its ``quality``.

**Files** (``scans/<sid>/process/``)::

    state.json             stages, base, edits, cursor, version, the stats of every cached step
    track/                 process_recording output: mesh.ply (not cleaned), trajectory.csv,
                           report.json, laser_points.ply, marker_map.json (marker mode)
    straighten/            the fuse at the corrected poses (same files) + straighten.json
    straighten.json        the loop-closure report of the last straighten (also when skipped)
    fuse/                  the fuse at the current poses and the chosen voxel size
    spacetime/             the refined mesh, the source stage's other files, report.json + "spacetime"
    refine/                the refined mesh, the source stage's other files, report.json + "refine"
    cache/base.npz         the base mesh (V float32, F int32, vertex normals float16, sigmas float32)
    cache/<edit id>.npz    the mesh after that edit, for the steps within 10 of the cursor
    selections/<id>.npy    the face indices of a delete / keep edit (to replay it)

A new base also copies the stage's ``report.json``, ``trajectory.csv``, ``laser_points.ply`` and
``marker_map.json`` into ``result/`` (the M1 files), so the viewer and downloads stay complete.
A scan processed before M3 (``result/mesh.ply`` but no ``state.json``) is taken over on first
use: its result becomes the ``track`` stage and the base.

**Undo / redo** load the step's cached mesh (``cache/``), so the last 10 steps are instant (the
time to write the result files). A step outside the cache is rebuilt from the nearest cached
one by replaying the edits (selections from ``selections/``), in the request.

**Locking.** ``Studio._lock`` is held while a request checks the 409 rules and submits its job
(the same lock as ``start_scan``), so two jobs never run on one scan. Each :class:`ScanProcess`
has its own lock around its state; a job does the heavy work without it and takes it to commit.
Undo / redo check the rules under ``Studio._lock``, take the scan lock before letting it go and
write the result files under the scan lock only (a project delete waits for them).
The lock order is ``Studio._lock`` -> scan lock -> store / hub locks; jobs never take
``Studio._lock``.

**Messages.** ``{"type": "process", ...ProcessState}`` on every state change and with every
update of a processing job (latest-only per scan: hub key ``process:<pid>/<sid>``).
"""
from __future__ import annotations

import base64
import binascii
import copy
import csv
import dataclasses
import json
import logging
import math
import os
import re
import secrets
import shutil
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ohs.server.jobs import JobCancelled
from ohs.server.projects import report_summary
from ohs.server.protocol import QUALITY_NAMES, ApiError, jsonable, normalize_quality, now_iso, quality_or_none
from ohs.server.util import safe_call

__all__ = ["ProcessManager", "RecipeStore", "ScanProcess", "BUILTIN_RECIPES", "PROCESS_KINDS", "MESH_OPS",
           "RECIPE_OPS", "normalize_op", "read_trajectory"]

log = logging.getLogger(__name__)

STAGES = ("track", "straighten", "fuse", "spacetime", "refine")
MESH_OPS = ("remove_floaters", "fill_holes", "smooth", "decimate", "delete_faces", "keep_faces")
RECIPE_OPS = MESH_OPS[:4]
PROCESS_KINDS = ("process_scan", "process_track", "process_straighten", "process_fuse", "process_spacetime",
                 "process_refine", "mesh_edit", "process_recipe")
MIN_LASER_POINTS = 200  # fewer laser points than this can't anchor anything
#: refine changes the mesh only when it would move at least this share of the vertices; below, the
#: corrections are the lasers' own scatter (0.4-0.7 % on unbiased simulated scans, 3-14 % with a bias)
REFINE_MIN_SHARE = 0.01
SPACETIME_MIN_PATCHES = 50  # fewer kept patches than this: "Refine from the images" changes nothing
CACHE_STEPS = 10
MAX_TRAJ_POINTS = 2000
MAX_HOLE_OUTLINES = 500
VOXEL_RANGE = (0.25, 10.0)
FORMAT = 1
DEFAULT_FLOATERS = {"min_size_mm": 5.0}

#: what the busy line says while a job of each kind runs
_BUSY = {"process_scan": "Making the mesh…", "process_track": "Tracking the scan…",
         "process_straighten": "Straightening…", "process_fuse": "Fusing…",
         "process_spacetime": "Refining from the images…", "process_refine": "Refining with the laser lines…",
         "process_recipe": "Applying the recipe…"}
_BUSY_OPS = {"remove_floaters": "Removing floaters…", "fill_holes": "Filling holes…", "smooth": "Smoothing…",
             "decimate": "Simplifying…", "delete_faces": "Deleting the selection…",
             "keep_faces": "Keeping the selection…"}

# --------------------------------------------------------------------------- recipes
BUILTIN_RECIPES: list[dict] = [
    {"id": "default", "name": "Default", "builtin": True,
     "description": "What Make mesh does: refines the surface with the laser lines, removes loose bits and closes "
                    "pin holes up to 5 mm. No smoothing and no simplification, so measurements stay true to the scan.",
     "settings": {"quality": "standard", "straighten": "auto", "refine": "auto", "voxel_mm": None},
     "ops": [{"op": "remove_floaters", "params": {"min_size_mm": 5.0, "min_fraction": 0.01}},
             {"op": "fill_holes", "params": {"max_hole_mm": 5.0}}]},
    {"id": "body-panel", "name": "Body panel", "builtin": True,
     "description": "For smooth sheet metal: closes holes up to 15 mm and smooths the surface while keeping "
                    "creases and edges.",
     "settings": {"quality": "standard", "straighten": "auto", "refine": "auto", "voxel_mm": None},
     "ops": [{"op": "remove_floaters", "params": {"min_size_mm": 5.0, "min_fraction": 0.01}},
             {"op": "fill_holes", "params": {"max_hole_mm": 15.0}},
             {"op": "smooth", "params": {"method": "bilateral", "strength": 0.5, "keep_edges": True,
                                         "within_sigma": True}}]},
    {"id": "bracket", "name": "Bracket / mechanical", "builtin": True,
     "description": "For brackets and machined parts: keeps sharp edges, light smoothing, closes pin holes up to "
                    "5 mm.",
     "settings": {"quality": "standard", "straighten": "auto", "refine": "auto", "voxel_mm": None},
     "ops": [{"op": "remove_floaters", "params": {"min_size_mm": 5.0, "min_fraction": 0.01}},
             {"op": "fill_holes", "params": {"max_hole_mm": 5.0}},
             {"op": "smooth", "params": {"method": "bilateral", "strength": 0.25, "keep_edges": True,
                                         "within_sigma": True}}]},
    {"id": "quick-preview", "name": "Quick preview", "builtin": True,
     "description": "Fast look at a scan: preview quality, no straightening, simplified to about 200,000 "
                    "triangles. Not for measuring.",
     "settings": {"quality": "preview", "straighten": "off", "refine": "off", "voxel_mm": None},
     "ops": [{"op": "remove_floaters", "params": {"min_size_mm": 5.0, "min_fraction": 0.01}},
             {"op": "decimate", "params": {"target_triangles": 200_000}}]},
]


def _fmt(x: float) -> str:
    return f"{x:g}" if abs(x) >= 1 or x == 0 else f"{x:.2g}"


def _num(params: dict, key: str, op: str, lo: float, hi: float, *, integer: bool = False,
         lo_open: bool = False) -> float | int | None:
    if key not in params or params[key] is None:
        return None
    v = params[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise ApiError(422, f"{op}: {key} must be a number", f"send a number for {key}")
    if integer:
        if float(v) != int(v):
            raise ApiError(422, f"{op}: {key} must be a whole number", f"send a whole number for {key}")
        v = int(v)
    else:
        v = float(v)
    if (v <= lo if lo_open else v < lo) or v > hi:
        rng = f"{'above' if lo_open else 'at least'} {_fmt(lo)} and at most {_fmt(hi)}"
        raise ApiError(422, f"{op}: {key} must be {rng} (got {v})", f"choose a {key} {rng}")
    return v


def normalize_op(op: str, params: dict | None) -> tuple[dict, str]:
    """Validate an edit's parameters; returns ``(normalised params, label)`` or raises 422.

    ``delete_faces`` / ``keep_faces`` are validated by the caller (the faces are decoded there)."""
    params = dict(params or {})
    allowed = {"remove_floaters": {"min_triangles", "min_size_mm", "keep_largest_only", "min_fraction"},
               "fill_holes": {"max_hole_mm", "watertight"}, "smooth": {"method", "strength", "keep_edges", "within_sigma"},
               "decimate": {"target_triangles", "ratio", "max_error_mm"}, "delete_faces": {"faces"},
               "keep_faces": {"faces"}}
    if op not in allowed:
        raise ApiError(422, f"unknown edit {op!r}", f"use one of {', '.join(MESH_OPS)}")
    extra = sorted(set(params) - allowed[op])
    if extra:
        raise ApiError(422, f"{op}: unknown parameter {extra[0]!r}", f"{op} takes {', '.join(sorted(allowed[op]))}")
    if op == "remove_floaters":
        out: dict[str, Any] = {}
        n = _num(params, "min_triangles", op, 1, 1e9, integer=True)
        s = _num(params, "min_size_mm", op, 0, 1e5, lo_open=True)
        fr = _num(params, "min_fraction", op, 0, 1, lo_open=True)
        keep = params.get("keep_largest_only")
        if keep is not None and not isinstance(keep, bool):
            raise ApiError(422, f"{op}: keep_largest_only must be true or false", "send true or false")
        if keep:
            return {"keep_largest_only": True}, "Keep the largest piece only"
        if n is not None:
            out["min_triangles"] = n
        if s is not None:
            out["min_size_mm"] = s
        if fr is not None:
            out["min_fraction"] = fr
        if not out:
            out = dict(DEFAULT_FLOATERS)
        parts = []
        if "min_size_mm" in out:
            parts.append(f"smaller than {_fmt(out['min_size_mm'])} mm")
        if "min_triangles" in out:
            parts.append(f"under {out['min_triangles']:,} triangles")
        if "min_fraction" in out:
            parts.append(f"under {_fmt(100 * out['min_fraction'])} % of the mesh")
        return out, "Remove loose pieces " + " or ".join(parts)
    if op == "fill_holes":
        m = _num(params, "max_hole_mm", op, 0, 1e5, lo_open=True)
        if m is None:
            raise ApiError(422, "fill_holes: max_hole_mm is missing", "send the largest hole perimeter to fill, in mm")
        wt = params.get("watertight", False)
        if not isinstance(wt, bool):
            raise ApiError(422, "fill_holes: watertight must be true or false", "send true or false")
        if wt:
            return {"max_hole_mm": m, "watertight": True}, f"Make watertight: close every opening up to {_fmt(m)} mm"
        return {"max_hole_mm": m}, f"Fill holes up to {_fmt(m)} mm"
    if op == "smooth":
        method = params.get("method")
        if method not in ("taubin", "bilateral"):
            raise ApiError(422, "smooth: method must be taubin or bilateral", "send method: \"taubin\" or \"bilateral\"")
        s = _num(params, "strength", op, 0, 1)
        if s is None:
            raise ApiError(422, "smooth: strength is missing", "send a strength from 0 to 1")
        keep = params.get("keep_edges", True)
        if not isinstance(keep, bool):
            raise ApiError(422, "smooth: keep_edges must be true or false", "send true or false")
        within = params.get("within_sigma", False)
        if not isinstance(within, bool):
            raise ApiError(422, "smooth: within_sigma must be true or false", "send true or false")
        label = f"Smooth ({method}, {round(100 * s)} %{', edges kept' if keep else ''}{', within 2σ' if within else ''})"
        out_s: dict[str, Any] = {"method": method, "strength": s, "keep_edges": keep}
        if within:
            out_s["within_sigma"] = True
        return out_s, label
    if op == "decimate":
        t = _num(params, "target_triangles", op, 4, 1e9, integer=True)
        r = _num(params, "ratio", op, 0, 1, lo_open=True)
        e = _num(params, "max_error_mm", op, 0.001, 10)
        if t is not None and r is not None or (t is None and r is None and e is None):
            raise ApiError(422, "decimate: send target_triangles or ratio, and / or max_error_mm",
                           "for example {\"target_triangles\": 200000} or {\"max_error_mm\": 0.02}")
        out_d: dict[str, Any] = {}
        if t is not None:
            out_d["target_triangles"] = t
            label = f"Simplify to {t:,} triangles"
        elif r is not None:
            out_d["ratio"] = r
            label = f"Simplify to {_fmt(100 * r)} % of the triangles"
        else:
            label = ""
        if e is not None:
            out_d["max_error_mm"] = e
            label = f"{label} (within {_fmt(e)} mm)" if label else f"Simplify to within {_fmt(e)} mm"
        return out_d, label
    return params, op


def _decode_faces(text: Any) -> np.ndarray:
    if not isinstance(text, str) or not text:
        raise ApiError(422, "the selection is missing", "send faces: base64 of little-endian uint32 face indices")
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(422, "the selection is not valid base64", "send faces as base64 of little-endian uint32 indices") from None
    if len(raw) == 0:
        raise ApiError(422, "the selection is empty", "select some triangles first")
    if len(raw) % 4:
        raise ApiError(422, f"the selection has {len(raw)} bytes, not a whole number of uint32 indices",
                       "send 4 bytes (little-endian uint32) per face index")
    return np.frombuffer(raw, dtype="<u4").astype(np.int64)


def _slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40].strip("-")
    return s or "recipe"


class RecipeStore:
    """The built-in recipes plus user recipes as JSON files in ``<data_dir>/recipes/``. Thread-safe."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()

    def _builtin(self, rid: str) -> dict | None:
        for r in BUILTIN_RECIPES:
            if r["id"] == rid:
                return copy.deepcopy(r)
        return None

    def _path(self, rid: str) -> Path:
        if not isinstance(rid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", rid):
            raise ApiError(404, f"no recipe {rid!r}", "pick a recipe from GET /api/recipes")
        return self.root / f"{rid}.json"

    def list(self) -> list[dict]:
        out = [copy.deepcopy(r) for r in BUILTIN_RECIPES]
        user: list[dict] = []
        if self.root.is_dir():
            for f in sorted(self.root.glob("*.json")):
                try:
                    r = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(r, dict) and r.get("id") == f.stem:
                        user.append(self._view(r))
                except (OSError, ValueError) as e:
                    log.warning("skipping recipe file %s: %s", f, e)
        user.sort(key=lambda r: (str(r.get("name", "")).lower(), r["id"]))
        return out + user

    @staticmethod
    def _view(r: dict) -> dict:
        settings = dict(r.get("settings") or {})
        if "quality" in settings:  # a recipe saved with the old name "full" reads as "standard"
            settings["quality"] = quality_or_none(settings["quality"]) or settings["quality"]
        return {"id": r["id"], "name": r.get("name", r["id"]), "description": r.get("description", ""),
                "builtin": False, "settings": settings, "ops": r.get("ops", [])}

    def get(self, rid: str) -> dict:
        b = self._builtin(rid)
        if b is not None:
            return b
        p = self._path(rid)
        try:
            return self._view(json.loads(p.read_text(encoding="utf-8")))
        except FileNotFoundError:
            raise ApiError(404, f"no recipe {rid!r}", "pick a recipe from GET /api/recipes") from None
        except (OSError, ValueError) as e:
            raise ApiError(500, f"recipe {rid} cannot be read: {e}", f"fix or delete {p}") from None

    @staticmethod
    def validate(settings: dict, ops: list) -> tuple[dict, list[dict]]:
        s = dict(settings or {})
        try:  # "full" is the old name of "standard"
            quality = normalize_quality(s.get("quality", "standard"))
        except ValueError:
            raise ApiError(422, "settings.quality must be standard, best or preview",
                           "use \"standard\", \"best\" or \"preview\"") from None
        straighten = s.get("straighten", "auto")
        if straighten not in ("auto", "off"):
            raise ApiError(422, "settings.straighten must be auto or off", "use \"auto\" or \"off\"")
        refine = s.get("refine", "auto")
        if refine not in ("auto", "off"):
            raise ApiError(422, "settings.refine must be auto or off", "use \"auto\" or \"off\"")
        voxel = _num(s, "voxel_mm", "settings", *VOXEL_RANGE)
        if not isinstance(ops, list) or len(ops) > 30:
            raise ApiError(422, "ops must be a list of at most 30 steps", "send ops: [{op, params}, ...]")
        out_ops = []
        for o in ops:
            if not isinstance(o, dict) or o.get("op") not in RECIPE_OPS:
                raise ApiError(422, f"a recipe step must be one of {', '.join(RECIPE_OPS)}",
                               "selections (delete / keep faces) belong to one scan and cannot be part of a recipe")
            p, _ = normalize_op(o["op"], o.get("params") or {})
            out_ops.append({"op": o["op"], "params": p})
        return {"quality": quality, "straighten": straighten, "refine": refine, "voxel_mm": voxel}, out_ops

    def create(self, name: str, description: str | None, settings: dict, ops: list) -> dict:
        n = " ".join(str(name or "").split())
        if not n:
            raise ApiError(400, "the recipe name is empty", "type a name for the recipe")
        if len(n) > 80:
            raise ApiError(400, f"the recipe name is too long ({len(n)} characters)", "use at most 80 characters")
        s, o = self.validate(settings, ops)
        builtin_ids = {r["id"] for r in BUILTIN_RECIPES}
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            for _ in range(100):
                rid = f"{_slug(n)}-{secrets.token_hex(2)}"
                if rid not in builtin_ids and not (self.root / f"{rid}.json").exists():
                    break
            else:  # pragma: no cover
                raise ApiError(500, "could not find a free recipe id", "try another name")
            rec = {"id": rid, "name": n, "description": " ".join(str(description or "").split())[:500],
                   "builtin": False, "settings": s, "ops": o, "created": now_iso()}
            _write_json(self.root / f"{rid}.json", rec)
        log.info("saved recipe %s (%s)", rid, n)
        return self._view(rec)

    def delete(self, rid: str) -> None:
        if self._builtin(rid) is not None:
            raise ApiError(409, f"{rid!r} is a built-in recipe and cannot be deleted",
                           "only recipes you saved can be deleted")
        p = self._path(rid)
        with self._lock:
            try:
                p.unlink()
            except FileNotFoundError:
                raise ApiError(404, f"no recipe {rid!r}", "pick a recipe from GET /api/recipes") from None


# --------------------------------------------------------------------------- files
def _retry(fn: Callable[[], Any], what: str) -> Any:
    for attempt in range(50):  # Windows: a reader (a download, the viewer) may hold the file
        try:
            return fn()
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.1)
    raise RuntimeError(what)  # pragma: no cover


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex[:8]}")
    tmp.write_text(json.dumps(jsonable(data), indent=1), encoding="utf-8")
    _retry(lambda: os.replace(tmp, path), f"replacing {path}")


def _save_npz(path: Path, V: np.ndarray, F: np.ndarray, N: np.ndarray | None = None,
              S: np.ndarray | None = None, filled: np.ndarray | None = None) -> None:
    """A cached step: V float32, F int32 and, when given, the vertex normals (float16), sigmas
    (float32, mm) and the faces' filled flags (bool)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.stem}.tmp-{uuid.uuid4().hex[:8]}.npz")
    arrays = {"V": np.asarray(V, dtype=np.float32), "F": np.asarray(F, dtype=np.int32)}
    if N is not None:
        arrays["N"] = np.asarray(N, dtype=np.float16)
    if S is not None:
        arrays["S"] = np.asarray(S, dtype=np.float32)
    if filled is not None:
        arrays["filled"] = np.asarray(filled, dtype=bool)
    with open(tmp, "wb") as fh:
        np.savez(fh, **arrays)
    _retry(lambda: os.replace(tmp, path), f"replacing {path}")


def _load_npz(path: Path, normals: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None,
                                                          np.ndarray | None]:
    """``(V, F, N, S, filled)`` of a cached step; ``N`` only when asked for; None when not stored."""
    with np.load(path) as z:
        N = z["N"].astype(np.float32) if normals and "N" in z.files else None
        S = z["S"].astype(np.float32) if "S" in z.files else None
        Fl = z["filled"].astype(bool) if "filled" in z.files else None
        return z["V"].astype(np.float64), z["F"].astype(np.int64), N, S, Fl


_STL = np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("attr", "<u2")])


def _write_stl(path: Path, V: np.ndarray, F: np.ndarray) -> None:
    """Binary STL in mm, like :func:`ohs.track.export.save_mesh` but with ``np.take`` gathers
    (about three times faster on 1M triangles, which undo / redo feel)."""
    P = np.take(np.ascontiguousarray(V, dtype=np.float32), np.asarray(F, dtype=np.int64), axis=0)
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    ln = np.sqrt(np.einsum("ij,ij->i", n, n))[:, None]
    tri = np.empty(len(F), dtype=_STL)
    tri["n"] = np.divide(n, ln, out=np.zeros_like(n), where=ln > 0)
    tri["v"] = P
    tri["attr"] = 0
    with open(path, "wb") as fh:
        fh.write(b"OpenHandScan binary STL, units: mm".ljust(80, b" "))
        fh.write(np.uint32(len(F)).tobytes())
        fh.write(tri.tobytes())


def _rmtree(path: Path) -> None:
    for attempt in range(30):
        if not path.exists():
            return
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        time.sleep(0.1)


def _move_dir(src: Path, dst: Path) -> None:
    """Replace ``dst`` by ``src`` (the old one is moved aside first, then removed)."""
    old = None
    if dst.exists():
        old = dst.with_name(f"{dst.name}.old-{uuid.uuid4().hex[:8]}")
        _retry(lambda: dst.rename(old), f"moving {dst} aside")
    _retry(lambda: src.rename(dst), f"renaming {src}")
    if old is not None:
        _rmtree(old)


def read_trajectory(path: str | Path) -> list[tuple[int, float, bool, np.ndarray]]:
    """``[(frame index, t_host, ok, T_wc 4x4)]`` from a ``trajectory.csv`` of process_recording."""
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            T = np.array([float(r[f"T{i}{j}"]) for i in range(4) for j in range(4)], dtype=np.float64).reshape(4, 4)
            out.append((int(r["index"]), float(r["t"]), r["ok"] in ("1", "True", "true"), T))
    return out


def _read_stage_mesh(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """A stage's ``mesh.ply`` as ``(V, F, S)``: degenerate faces and unused vertices dropped; ``S``
    (each vertex's sigma, the PLY ``quality``) is None when the file has none."""
    from ohs.mesh.topology import compact
    from ohs.track.export import load_ply

    d = load_ply(path)
    V = np.asarray(d["vertices"], dtype=np.float64)
    F = np.zeros((0, 3), dtype=np.int64) if d["faces"] is None else np.asarray(d["faces"], dtype=np.int64).reshape(-1, 3)
    F = F[(F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])]
    V2, F2, idx = compact(V, F)
    S = d.get("quality")
    S = np.asarray(S, dtype=np.float32)[idx] if S is not None and len(S) == len(V) else None
    return V2, F2, S


def _stats(V: np.ndarray, F: np.ndarray, S: np.ndarray | None, filled: np.ndarray | None = None) -> dict:
    """``MeshStats`` with the confidence (``sigma_mm``, None without sigmas) and the area made by
    hole filling (``filled_cm2``)."""
    from ohs.mesh import filled_area, mesh_stats, sigma_stats

    return {**mesh_stats(V, F), "sigma_mm": sigma_stats(S, V, F),
            "filled_cm2": round(filled_area(V, F, filled) / 100.0, 2)}


# --------------------------------------------------------------------------- per scan
def _empty_state(pid: str, sid: str) -> dict:
    base = {"state": "none", "summary": None, "when": None, "job_id": None}
    return {"format": FORMAT, "project_id": pid, "scan_id": sid,
            "stages": {"track": {**base, "quality": None, "tracked_pct": None, "voxel_mm": None},
                       "straighten": {**base, "loops": None, "correction_mm": None, "correction_deg": None,
                                      "crossings": None},
                       "fuse": {**base, "voxel_mm": None},
                       "spacetime": {**base, "source": None, "frames": None, "patches": None, "kept": None,
                                     "covered_pct": None, "moved_mm": None, "device": None},
                       "refine": {**base, "source": None, "points": None, "outliers": None, "moved_pct": None,
                                  "moved_mm": None, "residual_before_mm": None, "residual_after_mm": None}},
            "base": None, "edits": [], "cursor": 0, "version": 0}


class ScanProcess:
    """One scan's processing state and files (see the module docstring)."""

    def __init__(self, pid: str, sid: str, scan_dir: Path) -> None:
        self.pid, self.sid = pid, sid
        self.dir = Path(scan_dir) / "process"
        self.result_dir = Path(scan_dir) / "result"
        self.recording_dir = Path(scan_dir) / "recording"
        self.lock = threading.RLock()
        self.state = _empty_state(pid, sid)
        self._holes: tuple[int, list] | None = None
        # (version, V, F, sigma, filled) of the current step, for fits (POST .../fit)
        self._fit_mesh: tuple[int, np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None] | None = None

    # ------------------------------------------------------------------ persistence
    @property
    def state_path(self) -> Path:
        return self.dir / "state.json"

    def load(self) -> bool:
        """Read ``state.json``; False when there is none. A stage left ``running`` (the app
        stopped) becomes ``failed``."""
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False
        except (OSError, ValueError) as e:
            log.warning("process state of %s/%s unreadable (%s); starting over", self.pid, self.sid, e)
            return False
        st = _empty_state(self.pid, self.sid)
        st.update({k: v for k, v in data.items() if k in st})
        for name in STAGES:
            info = {**_empty_state(self.pid, self.sid)["stages"][name], **(data.get("stages", {}).get(name) or {})}
            if info["state"] == "running":
                info.update(state="failed", summary="Interrupted: the app stopped while this ran. Run it again.")
            if "quality" in info:  # state written with the old name "full" reads as "standard"
                info["quality"] = quality_or_none(info["quality"])
            if isinstance(info.get("summary"), str) and info["summary"].endswith(" at full quality"):  # the old name in the text
                info["summary"] = info["summary"][: -len("full quality")] + "standard quality"
            st["stages"][name] = info
        st["cursor"] = max(0, min(int(st.get("cursor", 0)), len(st.get("edits", []))))
        self.state = st
        return True

    def save(self) -> None:
        _write_json(self.state_path, self.state)

    # ------------------------------------------------------------------ steps and cache
    def step_key(self, k: int) -> str:
        return "base" if k == 0 else str(self.state["edits"][k - 1]["id"])

    def cache_path(self, k: int) -> Path:
        return self.dir / "cache" / f"{self.step_key(k)}.npz"

    def selection_path(self, edit_id: str) -> Path:
        return self.dir / "selections" / f"{edit_id}.npy"

    def stats_at(self, k: int) -> dict | None:
        if self.state["base"] is None:
            return None
        return self.state["base"].get("stats") if k == 0 else self.state["edits"][k - 1].get("stats")

    def prune_cache(self) -> None:
        """Keep the base and the steps within :data:`CACHE_STEPS` of the cursor."""
        cur = self.state["cursor"]
        keep = {"base"} | {self.step_key(k) for k in range(max(1, cur - CACHE_STEPS),
                                                           min(len(self.state["edits"]), cur + CACHE_STEPS) + 1)}
        d = self.dir / "cache"
        if d.is_dir():
            for f in d.glob("*.npz"):
                if f.stem not in keep and not f.name.startswith("."):
                    safe_call(f"removing {f}", f.unlink)

    def drop_edits_after(self, k: int) -> None:
        """Forget edits ``k..`` (their cached meshes and selections)."""
        for e in self.state["edits"][k:]:
            for p in (self.dir / "cache" / f"{e['id']}.npz", self.selection_path(e["id"])):
                if p.exists():
                    safe_call(f"removing {p}", p.unlink)
        del self.state["edits"][k:]
        self.state["cursor"] = min(self.state["cursor"], k)

    def holes(self, V_F: Callable[[], tuple[np.ndarray, np.ndarray]]) -> list:
        from ohs.mesh import hole_outlines

        v = int(self.state["version"])
        cached = self._holes
        if cached is not None and cached[0] == v:
            return cached[1]
        V, F = V_F()
        out = hole_outlines(V, F, max_holes=MAX_HOLE_OUTLINES)
        self._holes = (v, out)
        return out


class _Prog:
    """Maps a sub-task's progress into ``[p0, p1]`` of the job (and checks for cancel)."""

    def __init__(self, ctx: Any, p0: float, p1: float) -> None:
        self.ctx, self.p0, self.p1 = ctx, p0, p1

    def __call__(self, fraction: float, message: str | None = None, force: bool = False) -> None:
        self.ctx.check()
        f = min(1.0, max(0.0, float(fraction)))
        self.ctx.progress(self.p0 + (self.p1 - self.p0) * f, message, force=force)

    def sub(self, a: float, b: float) -> "_Prog":
        return _Prog(self.ctx, self.p0 + (self.p1 - self.p0) * a, self.p0 + (self.p1 - self.p0) * b)


def _straighten_progress(prog: _Prog) -> Callable[..., None]:
    """Adapter for ``straighten_trajectory``'s progress callback, whatever its arguments are:
    ``(fraction, message)``, ``(k, n)`` or ``(k, n, message)``."""

    def cb(*args: Any, **kw: Any) -> None:
        msg = kw.get("message")
        frac = None
        nums = [a for a in args if isinstance(a, (int, float)) and not isinstance(a, bool)]
        texts = [a for a in args if isinstance(a, str)]
        if texts:
            msg = texts[0]
        if len(nums) >= 2 and nums[1]:
            frac = float(nums[0]) / float(nums[1])
        elif nums:
            frac = float(nums[0])
        prog(frac if frac is not None else 0.0, f"Straightening: {msg}" if msg else "Straightening the trajectory")

    return cb


# --------------------------------------------------------------------------- the manager
class ProcessManager:
    """The processing studio of :class:`~ohs.server.studio.Studio` (see the module docstring)."""

    def __init__(self, studio: Any) -> None:
        self.st = studio
        self.recipes = RecipeStore(Path(studio.data_dir) / "recipes")
        self._scans: dict[tuple[str, str], ScanProcess] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ access
    def scan(self, pid: str, sid: str) -> ScanProcess:
        """The :class:`ScanProcess` of a scan (404 if the scan is unknown), loaded on first use."""
        self.st.projects.scan_entry(pid, sid)  # 404
        key = (pid, sid)
        with self._lock:
            sp = self._scans.get(key)
            if sp is None:
                sp = ScanProcess(pid, sid, self.st.projects.scan_dir(pid, sid))
                self._scans[key] = sp
                new = True
            else:
                new = False
        if new:
            with sp.lock:
                if not sp.load():
                    safe_call("taking over the earlier result", lambda: self._adopt_result(sp))
        return sp

    def _adopt_result(self, sp: ScanProcess) -> None:
        """A scan processed before M3 (``result/mesh.ply``, no state): its result becomes the
        track stage and the base."""
        res = sp.result_dir
        if not (res / "mesh.ply").is_file():
            return
        from ohs.mesh import vertex_normals

        track = sp.dir / "track"
        track.mkdir(parents=True, exist_ok=True)
        for name in ("mesh.ply", "trajectory.csv", "report.json", "laser_points.ply", "marker_map.json"):
            if (res / name).is_file():
                shutil.copy2(res / name, track / name)
        V, F, S = _read_stage_mesh(res / "mesh.ply")
        report = {}
        try:
            report = json.loads((res / "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        entry = self.st.projects.scan_entry(sp.pid, sp.sid)
        quality = quality_or_none(entry.get("quality") or report.get("quality"))
        st = sp.state
        info = st["stages"]["track"]
        frac = (report.get("frames") or {}).get("tracked_fraction")
        info.update(state="done", when=entry.get("created") or now_iso(),
                    quality=quality,
                    tracked_pct=round(100 * float(frac), 1) if isinstance(frac, (int, float)) else None,
                    summary=self._track_summary(report, quality))
        stats = _stats(V, F, S)
        st["base"] = {"triangles": int(len(F)), "vertices": int(len(V)), "stage": "track", "stats": stats}
        st["edits"], st["cursor"], st["version"] = [], 0, 1
        _save_npz(sp.cache_path(0), V, F, vertex_normals(V, F), S)
        sp.save()
        log.info("scan %s/%s: the earlier result is now the track stage of the processing studio", sp.pid, sp.sid)

    def forget(self, pid: str) -> None:
        """Drop the cached states of a project's scans (the project was moved to the trash)."""
        with self._lock:
            for key in [k for k in self._scans if k[0] == pid]:
                del self._scans[key]

    def wait_moves(self, pid: str) -> None:
        """Wait for an undo / redo still writing the files of one of the project's scans (it holds
        the scan lock, not ``Studio._lock``). Call under ``Studio._lock``, so no new one starts."""
        with self._lock:
            sps = [sp for key, sp in self._scans.items() if key[0] == pid]
        for sp in sps:
            with sp.lock:
                pass

    def active_job(self, pid: str, sid: str) -> Any:
        for job in self.st.jobs.active():
            if job.kind in PROCESS_KINDS and job.meta.get("project_id") == pid and job.meta.get("scan_id") == sid:
                return job
        return None

    # ------------------------------------------------------------------ the API view
    def view(self, sp: ScanProcess) -> dict:
        job = self.active_job(sp.pid, sp.sid)
        with sp.lock:
            st = sp.state
            stages = {}
            for name in STAGES:
                info = dict(st["stages"][name])
                stages[name] = info
            edits = [{k: v for k, v in e.items() if k != "stats"} for e in st["edits"]]
            base = st["base"]
            current = None
            if base is not None:
                current = {"version": int(st["version"]),
                           "mesh_url": f"/api/projects/{sp.pid}/scans/{sp.sid}/files/mesh.ply",
                           "stats": sp.stats_at(st["cursor"])}
            out = {"project_id": sp.pid, "scan_id": sp.sid, "stages": stages,
                   "base": None if base is None else {"triangles": base["triangles"], "vertices": base["vertices"],
                                                      "stage": base["stage"]},
                   "edits": edits, "cursor": int(st["cursor"]),
                   "can_undo": base is not None and st["cursor"] > 0,
                   "can_redo": base is not None and st["cursor"] < len(st["edits"]),
                   "current": current, "busy": None, "job": None}
        if job is not None:
            out["job"] = job.to_dict()
            op = job.meta.get("op")
            out["busy"] = _BUSY_OPS.get(op) if job.kind == "mesh_edit" else _BUSY.get(job.kind, "Working…")
            if job.kind == "process_recipe" and job.meta.get("recipe_name"):
                out["busy"] = f"Applying the recipe {job.meta['recipe_name']}…"
        return jsonable(out)

    def state(self, pid: str, sid: str) -> dict:
        return self.view(self.scan(pid, sid))

    def publish(self, sp: ScanProcess) -> None:
        self.st.hub.publish_json({"type": "process", **self.view(sp)}, key=f"process:{sp.pid}/{sp.sid}")

    def on_job(self, job: dict) -> None:
        """Every update of a processing job also updates its scan's ``process`` message."""
        if job.get("kind") not in PROCESS_KINDS:
            return
        sp = self._scans.get((job.get("project_id"), job.get("scan_id")))
        if sp is not None:
            safe_call("publishing the process state", lambda: self.publish(sp))

    def seed(self, client: Any) -> None:
        """New WebSocket clients get the process state of every scan with a running job."""
        for job in self.st.jobs.active():
            if job.kind in PROCESS_KINDS:
                sp = self._scans.get((job.meta.get("project_id"), job.meta.get("scan_id")))
                if sp is not None:
                    self.st.hub.offer(client, f"process:{sp.pid}/{sp.sid}", {"type": "process", **self.view(sp)})

    # ------------------------------------------------------------------ rules
    def _check_free(self, pid: str, sid: str, *, make_mesh: bool = False) -> dict:
        """The 409 rules (call under ``Studio._lock``); returns the scan entry."""
        entry = self.st.projects.scan_entry(pid, sid)
        s = self.st.session
        if s is not None and s.active and s.project_id == pid and s.scan_id == sid:
            raise ApiError(409, "this scan is still recording", "stop the scan first")
        job = self.active_job(pid, sid)
        if job is not None:
            if make_mesh and job.kind == "process_scan":
                raise ApiError(409, "this scan is already being processed", f"wait for job {job.id} to finish, or cancel it")
            sp = self._scans.get((pid, sid))
            busy = (self.view(sp)["busy"] if sp is not None else None) or "processing"
            raise ApiError(409, f"this scan is busy ({busy.rstrip('…').lower()})",
                           f"wait for job {job.id} to finish, or cancel it")
        return entry

    def _need_recording(self, pid: str, sid: str) -> Path:
        rec = self.st.projects.recording_dir(pid, sid)
        if not (rec / "frames.csv").is_file():
            raise ApiError(409, "this scan has no recording", "record the scan again")
        return rec

    @staticmethod
    def _need_track(sp: ScanProcess) -> None:
        if sp.state["stages"]["track"]["state"] != "done" or not (sp.dir / "track" / "trajectory.csv").is_file():
            raise ApiError(409, "this needs a finished Track stage", "run Track (or Make mesh) first")

    @staticmethod
    def _need_base(sp: ScanProcess) -> None:
        if sp.state["base"] is None:
            raise ApiError(409, "this scan has no mesh yet", "run Track (or Make mesh) first")

    # ------------------------------------------------------------------ mesh access
    def load_step(self, sp: ScanProcess, k: int) -> tuple[np.ndarray, np.ndarray]:
        """The mesh after ``k`` edits: from the cache, else replayed from the nearest cached step."""
        return self._load_step(sp, k)[:2]

    def _load_step(self, sp: ScanProcess, k: int) -> tuple[np.ndarray, np.ndarray, np.ndarray | None,
                                                            np.ndarray | None]:
        """:meth:`load_step` with each vertex's sigma and each face's filled flag (None without)."""
        from ohs.mesh import carry_filled, carry_sigma

        with sp.lock:
            path = sp.cache_path(k)
            if path.is_file():
                V, F, _, S, Fl = _load_npz(path)
                return V, F, S, Fl
            j = k - 1
            while j > 0 and not sp.cache_path(j).is_file():
                j -= 1
            if not sp.cache_path(j).is_file():
                raise ApiError(500, "the base mesh of this scan is missing from the process folder",
                               "run the last stage again (Track, Straighten or Fuse)")
            V, F, _, S, Fl = _load_npz(sp.cache_path(j))
            edits = sp.state["edits"]
            log.info("scan %s/%s: rebuilding step %d from step %d (%d edits)", sp.pid, sp.sid, k, j, k - j)
            for i in range(j, k):
                e = edits[i]
                faces = np.load(sp.selection_path(str(e["id"]))) if e["op"] in ("delete_faces", "keep_faces") else None
                V2, F2 = self._apply(sp, e["op"], e["params"], V, F, edit_id=e["id"], faces=faces, S=S)
                S = carry_sigma(e["op"], V, F, S, V2, faces)
                Fl = carry_filled(e["op"], V, F, Fl, V2, F2, faces)
                V, F = V2, F2
                if i + 1 >= k - CACHE_STEPS:
                    _save_npz(sp.cache_path(i + 1), V, F, S=S, filled=Fl)
            return V, F, S, Fl

    def current_mesh(self, sp: ScanProcess) -> tuple[np.ndarray, np.ndarray]:
        self._need_base(sp)
        return self.load_step(sp, sp.state["cursor"])

    def _formats(self) -> tuple[str, ...]:
        fmts = ["ply", "stl"]
        if "obj" in [str(e).lower() for e in getattr(self.st.cfg.process, "export", [])]:
            fmts.append("obj")
        return tuple(fmts)

    def _write_result(self, sp: ScanProcess, V: np.ndarray, F: np.ndarray, N: np.ndarray | None = None,
                      S: np.ndarray | None = None, filled: np.ndarray | None = None) -> None:
        """The result files of a step; the PLY carries each vertex's sigma (``quality``) and each
        face's filled flag (``filled``) when known."""
        from ohs.mesh import vertex_normals
        from ohs.track.export import save_mesh

        sp.result_dir.mkdir(parents=True, exist_ok=True)
        if N is None:
            N = vertex_normals(V, F)
        if S is not None and len(S) != len(V):
            S = None
        if filled is not None and len(filled) != len(F):
            filled = None
        for ext in self._formats():
            tmp = sp.result_dir / f".mesh-{uuid.uuid4().hex[:8]}.{ext}"
            if ext == "stl":
                _write_stl(tmp, V, F)
            else:
                save_mesh(tmp, V, F, N, quality=S if ext == "ply" else None, filled=filled if ext == "ply" else None)
            dst = sp.result_dir / f"mesh.{ext}"
            _retry(lambda t=tmp, d=dst: os.replace(t, d), f"replacing {dst}")
        stale = sp.result_dir / "mesh.obj"
        if "obj" not in self._formats() and stale.exists():
            safe_call("removing an old mesh.obj", stale.unlink)

    def _show_step(self, sp: ScanProcess, k: int, V: np.ndarray | None = None, F: np.ndarray | None = None,
                   N: np.ndarray | None = None, S: np.ndarray | None = None, filled: np.ndarray | None = None) -> None:
        """Make step ``k`` the current mesh: result files, version + 1, save (under the scan lock).

        Undo / redo pass no mesh: the cached step (with its normals, sigmas and flags) is loaded."""
        if V is None:
            path = sp.cache_path(k)
            if path.is_file():
                V, F, N, S, filled = _load_npz(path, normals=True)
            else:
                V, F, S, filled = self._load_step(sp, k)
        sp.state["cursor"] = k
        self._write_result(sp, V, F, N, S, filled)
        sp.state["version"] = int(sp.state["version"]) + 1
        sp.prune_cache()
        sp.save()

    # ------------------------------------------------------------------ edits
    def _apply(self, sp: ScanProcess, op: str, params: dict, V: np.ndarray, F: np.ndarray, *,
               edit_id: str | None = None, faces: np.ndarray | None = None, S: np.ndarray | None = None,
               progress: Callable[[float, str], None] | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Run one mesh operation on ``(V, F)`` (``S``: the vertices' sigmas, for ``within_sigma``)."""
        from ohs import mesh as M

        if op in ("delete_faces", "keep_faces"):
            if faces is None:
                faces = np.load(sp.selection_path(str(edit_id)))
            if len(faces) and int(faces.max()) >= len(F):
                raise ApiError(409, "the selection refers to triangles the current mesh does not have",
                               "reload the mesh and select again")
            return (M.delete_faces if op == "delete_faces" else M.keep_faces)(V, F, faces)
        if op == "remove_floaters":
            return M.remove_floaters(V, F, **params)
        if op == "fill_holes":
            return M.fill_holes(V, F, params["max_hole_mm"], include_borders=bool(params.get("watertight")),
                                progress=progress)
        if op == "smooth":
            # within 2 sigma: each vertex moves at most twice its own standard deviation (no sigmas: no limit)
            limit = 2.0 * S if params.get("within_sigma") and S is not None and len(S) == len(V) else None
            return M.smooth(V, F, params["method"], params["strength"], params.get("keep_edges", True), limit=limit,
                            progress=progress)
        if op == "decimate":
            return M.decimate(V, F, params.get("target_triangles"), params.get("ratio"),
                              max_error_mm=params.get("max_error_mm"), progress=progress)
        raise ApiError(422, f"unknown edit {op!r}", f"use one of {', '.join(MESH_OPS)}")

    @staticmethod
    def _summary(op: str, params: dict, V: np.ndarray, F: np.ndarray, V2: np.ndarray, F2: np.ndarray,
                 before: dict | None, after: dict) -> str:
        """What an edit did, in plain words (from the stats before and after)."""
        b = before or {}
        if op == "remove_floaters":
            if len(F2) == len(F):
                return "No loose pieces matched: nothing was removed"
            n = int(b.get("components", 0)) - int(after["components"])
            return f"Removed {n:,} loose piece{'s' if n != 1 else ''} ({len(F) - len(F2):,} triangles)"
        if op == "fill_holes":
            left = int(after["holes"]) + (after.get("border_mm") is not None)
            if params.get("watertight"):
                if len(F2) == len(F):
                    return (f"No opening up to {_fmt(params['max_hole_mm'])} mm: nothing was closed"
                            if left else "Already watertight: nothing to close")
                return (f"Closed every opening ({len(F2) - len(F):,} new triangles): the mesh is watertight"
                        if after.get("watertight") else
                        f"Closed openings with {len(F2) - len(F):,} new triangles; not watertight yet: {left:,} "
                        f"opening{'s are' if left != 1 else ' is'} larger than {_fmt(params['max_hole_mm'])} mm")
            if len(F2) == len(F):
                return f"No holes up to {_fmt(params['max_hole_mm'])} mm: nothing was filled"
            n = int(b.get("holes", 0)) - int(after["holes"])
            return f"Filled {n:,} hole{'s' if n != 1 else ''} ({len(F2) - len(F):,} new triangles)"
        if op == "smooth" and len(V2) == len(V) and len(V):
            d = np.linalg.norm(V2 - V, axis=1)
            within = ", each point within 2σ of where it was measured" if params.get("within_sigma") else ""
            return f"Moved the surface by {float(np.mean(d)):.3f} mm on average and {float(np.max(d)):.2f} mm at most{within}"
        if op == "decimate" and len(F2) < len(F):
            from ohs.mesh import deviation

            dev = deviation(V, V2, F2)
            return (f"{len(F):,} -> {len(F2):,} triangles; the surface moved at most {float(dev.max()):.3f} mm "
                    f"({float(np.percentile(dev, 99.9)):.3f} mm for 99.9 % of it)")
        return f"{len(F):,} -> {len(F2):,} triangles"

    def _commit_edit(self, sp: ScanProcess, op: str, params: dict, label: str, V: np.ndarray, F: np.ndarray,
                     ms: float, summary: str, job_id: str | None, faces: np.ndarray | None = None,
                     stats: dict | None = None, S: np.ndarray | None = None, filled: np.ndarray | None = None) -> dict:
        """Append an edit after the cursor (the redo steps are dropped) and show it."""
        from ohs.mesh import vertex_normals

        if stats is None:
            stats = _stats(V, F, S, filled)
        N = vertex_normals(V, F)
        with sp.lock:
            k = sp.state["cursor"]
            sp.drop_edits_after(k)
            eid = f"e-{uuid.uuid4().hex[:8]}"
            entry = {"id": eid, "op": op, "params": params, "label": label, "created": now_iso(),
                     "triangles_after": int(len(F)), "ms": int(round(ms)), "summary": summary, "job_id": job_id,
                     "stats": stats}
            if faces is not None:
                sp.selection_path(eid).parent.mkdir(parents=True, exist_ok=True)
                np.save(sp.selection_path(eid), faces.astype(np.int64))
            sp.state["edits"].append(entry)
            _save_npz(sp.cache_path(k + 1), V, F, N, S, filled)
            self._show_step(sp, k + 1, V, F, N, S, filled)
            return {key: v for key, v in entry.items() if key != "stats"}

    def _run_edit(self, ctx: Any, sp: ScanProcess, op: str, params: dict, label: str, prog: _Prog,
                  faces: np.ndarray | None = None) -> dict:
        from ohs.mesh import carry_filled, carry_sigma

        with sp.lock:
            k = sp.state["cursor"]
            before = sp.stats_at(k)
        prog(0.02, f"{label}: loading the mesh", force=True)
        V, F, S, Fl = self._load_step(sp, k)
        t0 = time.perf_counter()
        V2, F2 = self._apply(sp, op, params, V, F, faces=faces, S=S,
                             progress=lambda f, m=None: prog(0.05 + 0.8 * f, f"{label}: {m}" if m else label))
        ms = 1e3 * (time.perf_counter() - t0)
        if len(F2) == 0:
            raise ApiError(409, "this would remove the whole mesh", "change the settings or the selection")
        ctx.check()
        prog(0.88, f"{label}: measuring the result", force=True)
        S2 = carry_sigma(op, V, F, S, V2, faces)
        Fl2 = carry_filled(op, V, F, Fl, V2, F2, faces)
        stats = _stats(V2, F2, S2, Fl2)
        summary = self._summary(op, params, V, F, V2, F2, before, stats)
        prog(0.94, f"{label}: saving", force=True)
        return self._commit_edit(sp, op, params, label, V2, F2, ms, summary, ctx.job.id, faces, stats, S2, Fl2)

    def submit_edit(self, pid: str, sid: str, op: str, params: dict | None, version: int | None = None) -> Any:
        if op not in MESH_OPS:
            raise ApiError(422, f"unknown edit {op!r}", f"use one of {', '.join(MESH_OPS)}")
        faces = None
        if op in ("delete_faces", "keep_faces"):
            p = dict(params or {})
            extra = sorted(set(p) - {"faces"})
            if extra:
                raise ApiError(422, f"{op}: unknown parameter {extra[0]!r}", f"{op} takes faces (base64)")
            from ohs.mesh.topology import sorted_unique

            faces = sorted_unique(_decode_faces(p.get("faces")))
            norm = {"count": int(len(faces))}
            label = f"Delete {len(faces):,} triangles" if op == "delete_faces" else f"Keep {len(faces):,} triangles"
        else:
            norm, label = normalize_op(op, params)
        with self.st._lock:
            self._check_free(pid, sid)
            sp = self.scan(pid, sid)
            self._need_base(sp)
            with sp.lock:
                if version is not None and int(version) != int(sp.state["version"]):
                    raise ApiError(409, f"the mesh changed since you selected (version {version}, now "
                                        f"{sp.state['version']})", "select again on the current mesh")
                n_cur = (sp.stats_at(sp.state["cursor"]) or {}).get("triangles")
            if faces is not None and n_cur is not None and len(faces) and int(faces.max()) >= int(n_cur):
                raise ApiError(422, f"the selection has triangle {int(faces.max())} but the mesh has {n_cur}",
                               "reload the mesh and select again")

            def run(ctx: Any) -> dict:
                entry = self._run_edit(ctx, sp, op, norm, label, _Prog(ctx, 0.0, 1.0), faces)
                self.publish(sp)
                return {"project_id": pid, "scan_id": sid, "edit": entry, "message": entry["summary"]}

            job = self.st.jobs.submit("mesh_edit", run, message=f"Queued: {label}",
                                      meta={"project_id": pid, "scan_id": sid, "op": op},
                                      on_done=lambda j: self._edit_done(sp, j, label))
        self.publish(sp)
        return job

    def _edit_done(self, sp: ScanProcess, job: Any, label: str) -> None:
        if job.state == "failed":
            self.st.notify("warn", f"{label} failed: {job.error}. The mesh is unchanged.")

    def undo(self, pid: str, sid: str) -> dict:
        return self._move(pid, sid, -1)

    def redo(self, pid: str, sid: str) -> dict:
        return self._move(pid, sid, +1)

    def _move(self, pid: str, sid: str, step: int) -> dict:
        with self.st._lock:
            self._check_free(pid, sid)
            sp = self.scan(pid, sid)
            self._need_base(sp)
            sp.lock.acquire()  # before Studio._lock is let go: no job on this scan starts in between
        try:  # the files are written under the scan lock only (a scan start or connect never waits)
            k = sp.state["cursor"] + step
            if k < 0:
                raise ApiError(409, "nothing to undo", "the mesh is the stage's result already")
            if k > len(sp.state["edits"]):
                raise ApiError(409, "nothing to redo", "redo only works after an undo")
            self._show_step(sp, k)
        finally:
            sp.lock.release()
        self.publish(sp)
        return self.view(sp)

    # ------------------------------------------------------------------ stages
    def _scan_params(self, sp: ScanProcess) -> tuple[Path, str, int]:
        from ohs.io.recording import RecordingReader

        entry = self.st.projects.scan_entry(sp.pid, sp.sid)
        mode = entry.get("mode") if entry.get("mode") in ("markerless", "markers") else "markerless"
        reader = RecordingReader(sp.recording_dir)
        if len(reader) == 0:
            raise ApiError(409, "the recording has no frames", "record the scan again")
        size = reader.meta.get("image_size") or list(reader.frame(0).image_size)
        return sp.recording_dir, mode, int(size[0])

    def _stage_params(self, mode: str, quality: str, voxel_mm: float | None, width: int) -> Any:
        from ohs.app.pipeline import default_fusion_params, effective_scale
        from ohs.server.studio import process_params

        p = process_params(self.st.cfg, mode, quality, width)
        if voxel_mm is not None:
            fusion = p.pipeline.fusion or default_fusion_params(effective_scale(int(round(width * p.pipeline.scale))))
            p = dataclasses.replace(p, pipeline=dataclasses.replace(p.pipeline, fusion=dataclasses.replace(
                fusion, voxel_mm=float(voxel_mm))))
        # the base keeps every piece: removing floaters is an edit you can see and undo
        return dataclasses.replace(p, clean_min_component=0.0, export=("ply",))

    @staticmethod
    def _track_summary(report: dict, quality: str | None) -> str:
        fr = report.get("frames") or {}
        n, t = fr.get("processed"), fr.get("tracked")
        q = QUALITY_NAMES.get(quality_or_none(quality) or "", "")
        if isinstance(n, int) and isinstance(t, int) and n:
            s = f"Tracked {t:,} of {n:,} frames ({100 * t / n:.0f} %)"
            return s + (f" at {q}" if q else "")
        return "Tracked" + (f" at {q}" if q else "")

    def _stage_start(self, sp: ScanProcess, name: str, job_id: str) -> dict:
        with sp.lock:
            before = dict(sp.state["stages"][name])
            sp.state["stages"][name].update(state="running", job_id=job_id, summary=None)
            sp.save()
        self.publish(sp)
        return before

    def _stage_end(self, sp: ScanProcess, name: str, **fields: Any) -> None:
        with sp.lock:
            sp.state["stages"][name].update(when=now_iso(), **fields)
            sp.save()
        self.publish(sp)

    def _stage_restore(self, sp: ScanProcess, name: str, before: dict) -> None:
        """A cancelled stage goes back to what it was before (its files were not touched)."""
        with sp.lock:
            sp.state["stages"][name] = dict(before)
            sp.save()
        self.publish(sp)

    def _new_base(self, sp: ScanProcess, stage: str, stage_dir: Path, report: dict, quality: str | None) -> None:
        """The stage's mesh becomes the base; the edits are cleared and the result files updated."""
        from ohs.mesh import vertex_normals

        V, F, S = _read_stage_mesh(stage_dir / "mesh.ply")
        stats = _stats(V, F, S)
        N = vertex_normals(V, F)
        with sp.lock:
            sp.drop_edits_after(0)
            for f in (sp.dir / "cache").glob("*.npz") if (sp.dir / "cache").is_dir() else []:
                safe_call(f"removing {f}", f.unlink)
            sp.state["base"] = {"triangles": int(len(F)), "vertices": int(len(V)), "stage": stage, "stats": stats}
            sp.state["edits"], sp.state["cursor"] = [], 0
            _save_npz(sp.cache_path(0), V, F, N, S)
            sp.result_dir.mkdir(parents=True, exist_ok=True)
            for name in ("report.json", "trajectory.csv", "laser_points.ply", "marker_map.json"):
                src = stage_dir / name
                if src.is_file():
                    tmp = sp.result_dir / f".{name}.tmp-{uuid.uuid4().hex[:8]}"
                    shutil.copy2(src, tmp)
                    _retry(lambda t=tmp, d=sp.result_dir / name: os.replace(t, d), f"replacing {name}")
            self._show_step(sp, 0, V, F, N, S)
        summary = report_summary(report)
        if quality:
            summary["quality"] = quality
        fields: dict[str, Any] = {"status": "processed", "report": summary, "error": None}
        if quality:
            fields["quality"] = quality
        safe_call("updating the scan", lambda: self.st.projects.update_scan(sp.pid, sp.sid, **fields))

    def _fuse_into(self, ctx: Any, sp: ScanProcess, name: str, prog: _Prog, *, quality: str, voxel_mm: float | None,
                   poses: dict[int, np.ndarray] | None) -> dict:
        """``process_recording`` into ``process/<name>/`` (tracked, or at ``poses``); returns the report."""
        from ohs import compute
        from ohs.app.process import process_recording

        rec, mode, width = self._scan_params(sp)
        params = self._stage_params(mode, quality, voxel_mm, width)
        tmp = sp.dir / f"{name}.tmp-{ctx.job.id}"
        _rmtree(tmp)
        dev = compute.get_device()
        where = f" on the GPU ({dev.name})" if dev is not None else " on the CPU"
        verb = "Tracking" if poses is None else "Fusing"
        # the job's `frames` field (APP_API.md): this pass's count and its rate since the first frame
        # was done (the loading and the first frame's warm-up are not in the rate)
        t_first: list[float] = []

        def progress(k: int, n: int, res: Any) -> None:
            now = time.monotonic()
            if not t_first:
                t_first.append(now)
            elapsed = now - t_first[0]
            fps = k / elapsed if k > 0 and elapsed > 0 else None
            last = k + 1 >= n
            ctx.set_frames({"stage": name, "done": k + 1, "total": n, "fps": None if fps is None else round(fps, 2),
                            "elapsed_s": round(elapsed, 2)})
            if last:  # the tail: the mesh is made from the frames (no frame counter any more)
                prog(1.0, f"{verb}: all {n} frames done, making the mesh", force=True)
                return
            lost = "" if res.track.ok else " (tracking lost)"
            prog((k + 1) / max(n, 1), f"{verb}: frame {k + 1} of {n}{where}{lost}")

        try:
            report = process_recording(rec, tmp, params=params, progress=progress, poses=poses)
            ctx.check()
            report["quality"] = quality
            (tmp / "report.json").write_text(json.dumps(jsonable(report), indent=1), encoding="utf-8")
            _move_dir(tmp, sp.dir / name)
        finally:
            _rmtree(tmp)
            ctx.set_frames(None)  # the pass is over
        return report

    def _do_track(self, ctx: Any, sp: ScanProcess, prog: _Prog, quality: str, voxel_mm: float | None = None) -> None:
        before = self._stage_start(sp, "track", ctx.job.id)
        try:
            prog(0.0, "Loading the recording", force=True)
            report = self._fuse_into(ctx, sp, "track", prog.sub(0.0, 0.95), quality=quality, voxel_mm=voxel_mm,
                                     poses=None)
            prog(0.96, "Saving the mesh", force=True)
            fr = report.get("frames") or {}
            with sp.lock:  # a new trajectory: straighten, fuse and refine start over
                for name in ("straighten", "fuse", "spacetime", "refine"):
                    sp.state["stages"][name] = _empty_state(sp.pid, sp.sid)["stages"][name]
                    _rmtree(sp.dir / name)
                p = sp.dir / "straighten.json"
                if p.exists():
                    safe_call("removing straighten.json", p.unlink)
            self._new_base(sp, "track", sp.dir / "track", report, quality)
            self._stage_end(sp, "track", state="done", summary=self._track_summary(report, quality), quality=quality,
                            tracked_pct=round(100.0 * float(fr.get("tracked_fraction", 0.0)), 1), voxel_mm=voxel_mm,
                            job_id=ctx.job.id)
        except (JobCancelled, InterruptedError):
            self._stage_restore(sp, "track", before)
            raise
        except Exception as e:
            self._stage_end(sp, "track", state="failed", summary=f"Tracking failed: {e}")
            raise

    def _do_straighten(self, ctx: Any, sp: ScanProcess, prog: _Prog) -> str:
        """Returns the summary. ``skipped`` (base unchanged) when straightening is not available,
        fails, or finds no loop."""
        before = self._stage_start(sp, "straighten", ctx.job.id)
        try:
            with sp.lock:
                track = dict(sp.state["stages"]["track"])
            traj = read_trajectory(sp.dir / "track" / "trajectory.csv")
            prog(0.0, "Looking for loops in the trajectory", force=True)
            try:
                from ohs.track.posegraph import straighten_trajectory
            except ImportError:
                msg = "Straightening is not available in this version of the engine, so the scan was left as it is"
                self._stage_end(sp, "straighten", state="skipped", summary=msg, loops=None, correction_mm=None,
                                correction_deg=None)
                return msg
            from ohs.io.recording import RecordingReader

            calib = RecordingReader(sp.recording_dir).calibration()
            try:
                poses, rep = straighten_trajectory(sp.recording_dir, calib, traj,
                                                   progress=_straighten_progress(prog.sub(0.0, 0.4)),
                                                   cancelled=lambda: ctx.cancelled)
            except (JobCancelled, InterruptedError):
                raise
            except Exception as e:
                if ctx.cancelled:
                    raise JobCancelled() from e
                log.exception("straighten_trajectory failed")
                msg = f"Straightening failed ({e}), so the scan was left as it is"
                self._stage_end(sp, "straighten", state="skipped", summary=msg, loops=None, correction_mm=None,
                                correction_deg=None)
                return msg
            ctx.check()
            rep = dict(rep or {})
            _write_json(sp.dir / "straighten.json", rep)
            loops = rep.get("loops_accepted")
            if not isinstance(loops, int):
                loops = len(rep.get("loops") or [])
            corr_mm, corr_deg = rep.get("correction_mm"), rep.get("correction_deg")
            verdict = rep.get("verdict") if isinstance(rep.get("verdict"), str) and rep.get("verdict") else None
            looped = bool(loops and poses and rep.get("changed") is not False)
            quality, voxel = quality_or_none(track.get("quality")) or "standard", track.get("voxel_mm")
            report: dict = {}
            if looped:  # the loops' fuse is also the reference surface for the laser crossings
                report = self._fuse_into(ctx, sp, "straighten", prog.sub(0.3, 0.6), quality=quality, voxel_mm=voxel,
                                         poses=poses)
                ref_dir, cur = sp.dir / "straighten", dict(poses)
            else:
                ref_dir, cur = sp.dir / "track", {i: T for i, _, ok, T in traj if ok}
            # the laser lines' crossings (research plan 7.3): never worse, see ohs.track.curve_ba
            crep: dict = {}
            new_poses = cur
            try:
                from ohs.track.curve_ba import laser_curve_adjustment

                Vr, Fr, _ = _read_stage_mesh(ref_dir / "mesh.ply")
                rows_cur = [(i, t, True, cur[i]) for i, t, ok, _ in traj if ok and i in cur]
                new_poses, crep = laser_curve_adjustment(
                    sp.recording_dir, calib, rows_cur, reference=(Vr, Fr), cancelled=lambda: ctx.cancelled,
                    progress=lambda f, m: prog(0.6 + 0.15 * f, f"Laser crossings: {m}"))
            except (JobCancelled, InterruptedError):
                raise
            except Exception as e:
                if ctx.cancelled:
                    raise JobCancelled() from e
                log.exception("laser_curve_adjustment failed")
                crep = {"changed": False, "reason": "failed", "verdict": f"Lining up the laser crossings failed ({e})"}
            ctx.check()
            rep["laser_curves"] = crep
            _write_json(sp.dir / "straighten.json", rep)
            adjusted = bool(crep.get("changed"))
            crossings = int(crep["crossings"]) if adjusted else None
            if not looped and not adjusted:
                # no loop (or loops that show the passes already line up) and nothing for the lasers to fix:
                # the base (and its edits) stay
                msg = verdict or "No loops found: nothing to straighten"
                self._stage_end(sp, "straighten", state="skipped", summary=msg, loops=int(loops or 0),
                                correction_mm=0.0, correction_deg=0.0, crossings=None)
                return msg
            if adjusted:
                report = self._fuse_into(ctx, sp, "straighten", prog.sub(0.75, 0.95), quality=quality, voxel_mm=voxel,
                                         poses=new_poses)
                track_poses = {i: T for i, _, ok, T in traj if ok}
                corr_mm = max((float(np.linalg.norm(new_poses[i][:3, 3] - track_poses[i][:3, 3]))
                               for i in new_poses if i in track_poses), default=0.0)
                corr_deg = max(float(corr_deg or 0.0), float(crep.get("moved_deg") or 0.0))
            (sp.dir / "straighten" / "straighten.json").write_text(json.dumps(jsonable(rep), indent=1), encoding="utf-8")
            prog(0.96, "Saving the mesh", force=True)
            with sp.lock:  # fuse and the refinements built on the old poses
                for name in ("fuse", "spacetime", "refine"):
                    sp.state["stages"][name] = _empty_state(sp.pid, sp.sid)["stages"][name]
                    _rmtree(sp.dir / name)
            self._new_base(sp, "straighten", sp.dir / "straighten", report, track.get("quality"))
            if looped and verdict is None:
                moved = f", moved the scan by up to {float(corr_mm):.1f} mm" if isinstance(corr_mm, (int, float)) else ""
                verdict = f"Closed {loops} loop{'s' if loops != 1 else ''}{moved}"
            if adjusted:
                lasers = (f"lined the scan up on {crossings:,} laser crossings (median miss {crep['miss_mm']['before']:.2f} "
                          f"-> {crep['miss_mm']['after']:.2f} mm, the camera moved by up to {crep['moved_mm']:.1f} mm)")
                verdict = (f"{verdict.rstrip('.')}; then {lasers}" if looped else
                           f"No loops found, but the laser lines cross: {lasers}")
            self._stage_end(sp, "straighten", state="done", summary=verdict, loops=int(loops or 0),
                            correction_mm=corr_mm, correction_deg=corr_deg, crossings=crossings, job_id=ctx.job.id)
            return verdict
        except (JobCancelled, InterruptedError):
            self._stage_restore(sp, "straighten", before)
            raise
        except Exception as e:
            self._stage_end(sp, "straighten", state="failed", summary=f"Straightening failed: {e}")
            raise

    def _current_poses(self, sp: ScanProcess) -> dict[int, np.ndarray]:
        with sp.lock:
            straight = sp.state["stages"]["straighten"]["state"] == "done"
        src = sp.dir / ("straighten" if straight else "track") / "trajectory.csv"
        return {i: T for i, _, ok, T in read_trajectory(src) if ok}

    def _do_fuse(self, ctx: Any, sp: ScanProcess, prog: _Prog, voxel_mm: float | None) -> None:
        before = self._stage_start(sp, "fuse", ctx.job.id)
        try:
            with sp.lock:
                track = dict(sp.state["stages"]["track"])
            poses = self._current_poses(sp)
            if not poses:
                raise ApiError(409, "the track stage has no tracked frames to fuse", "run Track again")
            report = self._fuse_into(ctx, sp, "fuse", prog.sub(0.0, 0.95), quality=quality_or_none(track.get("quality")) or "standard",
                                     voxel_mm=voxel_mm if voxel_mm is not None else track.get("voxel_mm"), poses=poses)
            prog(0.96, "Saving the mesh", force=True)
            with sp.lock:  # the refinements built on the previous base
                for name in ("spacetime", "refine"):
                    sp.state["stages"][name] = _empty_state(sp.pid, sp.sid)["stages"][name]
                    _rmtree(sp.dir / name)
            self._new_base(sp, "fuse", sp.dir / "fuse", report, track.get("quality"))
            used = (report.get("mesh") or {}).get("voxel_mm")
            n = (report.get("frames") or {}).get("processed")
            self._stage_end(sp, "fuse", state="done", voxel_mm=used, job_id=ctx.job.id,
                            summary=f"Fused {n:,} frames at {_fmt(float(used))} mm voxels" if used and n else "Fused")
        except (JobCancelled, InterruptedError):
            self._stage_restore(sp, "fuse", before)
            raise
        except Exception as e:
            self._stage_end(sp, "fuse", state="failed", summary=f"Fusing failed: {e}")
            raise

    def _do_refine(self, ctx: Any, sp: ScanProcess, prog: _Prog) -> str:
        """"Refine with lasers": pull the base stage's mesh onto its laser cloud
        (:func:`ohs.mesh.anchor_to_points`). Returns the summary; ``skipped`` (base and edits
        unchanged) when the scan has no laser points or the mesh already agrees with them."""
        from ohs.mesh import anchor_to_points, vertex_normals
        from ohs.track.export import load_ply, save_mesh

        before = self._stage_start(sp, "refine", ctx.job.id)
        try:
            self._need_base(sp)
            with sp.lock:
                src = (sp.state["base"] or {}).get("stage")
                if src == "refine":  # refine again: from the mesh it refined
                    src = before.get("source") or "track"
                quality = sp.state["stages"]["track"].get("quality")
            src_dir = sp.dir / str(src)
            prog(0.0, "Reading the laser points", force=True)
            lp = src_dir / "laser_points.ply"
            P = load_ply(lp)["vertices"] if lp.is_file() else np.zeros((0, 3))
            if len(P) < MIN_LASER_POINTS:
                msg = ("This scan has no laser points (the lasers were off or not calibrated), so the mesh was left "
                       "as it is")
                self._stage_end(sp, "refine", state="skipped", summary=msg, source=src, points=int(len(P)), outliers=None,
                                moved_pct=0.0, moved_mm=None, residual_before_mm=None, residual_after_mm=None)
                return msg
            V, F, S = _read_stage_mesh(src_dir / "mesh.ply")
            V2, _, rep = anchor_to_points(V, F, P, progress=lambda f, m: prog(0.05 + 0.85 * f, f"Refining: {m}"))
            ctx.check()
            r0, r1 = rep["residual_before"]["median"], rep["residual_after"]["median"]
            fields = {"source": src, "points": rep["used"], "outliers": rep["outliers"],
                      "moved_pct": round(100.0 * rep["moved_share"], 1), "moved_mm": rep["moved"]["p95"],
                      "residual_before_mm": r0, "residual_after_mm": r1}
            if rep["used"] < MIN_LASER_POINTS or rep["moved_share"] < REFINE_MIN_SHARE:
                msg = (f"The mesh already agrees with the laser lines ({(r0 or 0.0):.2f} mm median difference over "
                       f"{rep['used']:,} points), so nothing was changed" if rep["used"] >= MIN_LASER_POINTS else
                       "Too few laser points lie on the mesh to refine it, so it was left as it is")
                self._stage_end(sp, "refine", state="skipped", summary=msg, **fields)
                return msg
            prog(0.92, "Saving the mesh", force=True)
            tmp = sp.dir / f"refine.tmp-{ctx.job.id}"
            _rmtree(tmp)
            tmp.mkdir(parents=True)
            try:
                save_mesh(tmp / "mesh.ply", V2, F, vertex_normals(V2, F), quality=S)
                for name in ("trajectory.csv", "laser_points.ply", "marker_map.json"):
                    if (src_dir / name).is_file():
                        shutil.copy2(src_dir / name, tmp / name)
                report: dict = {}
                if (src_dir / "report.json").is_file():
                    report = json.loads((src_dir / "report.json").read_text(encoding="utf-8"))
                report["refine"] = {**rep, "source": src}
                (tmp / "report.json").write_text(json.dumps(jsonable(report), indent=1), encoding="utf-8")
                ctx.check()
                _move_dir(tmp, sp.dir / "refine")
            finally:
                _rmtree(tmp)
            self._new_base(sp, "refine", sp.dir / "refine", report, quality)
            summary = (f"Pulled {fields['moved_pct']:g} % of the surface onto {rep['used']:,} laser points, by up to "
                       f"{rep['moved']['p95']:.2f} mm (95 %); the median difference fell from {r0:.2f} to {r1:.2f} mm")
            self._stage_end(sp, "refine", state="done", summary=summary, job_id=ctx.job.id, **fields)
            return summary
        except (JobCancelled, InterruptedError):
            self._stage_restore(sp, "refine", before)
            raise
        except Exception as e:
            self._stage_end(sp, "refine", state="failed", summary=f"Refining failed: {e}")
            raise

    def _do_spacetime(self, ctx: Any, sp: ScanProcess, prog: _Prog) -> str:
        """"Refine from the images" (a trial build): measure the base stage's mesh against the
        recording's frames (:func:`ohs.track.spacetime.refine_from_images`). Returns the summary;
        ``skipped`` (base and edits unchanged) when too few patches hold dot texture in enough
        frames."""
        from ohs.io.recording import RecordingReader
        from ohs.mesh import vertex_normals
        from ohs.track.export import save_mesh
        from ohs.track.spacetime import SpacetimeParams, frames_from_recording, refine_from_images

        before = self._stage_start(sp, "spacetime", ctx.job.id)
        try:
            self._need_base(sp)
            with sp.lock:
                src = (sp.state["base"] or {}).get("stage")
                if src == "refine":  # the lasers refine last: start from the mesh they refined
                    src = sp.state["stages"]["refine"].get("source") or "track"
                if src == "spacetime":  # again: from the mesh it refined
                    src = before.get("source") or "track"
                quality = sp.state["stages"]["track"].get("quality")
            src_dir = sp.dir / str(src)
            poses = self._current_poses(sp)
            calib = RecordingReader(sp.recording_dir).calibration()
            if calib is None or not poses:
                raise ApiError(409, "this scan has no calibration or no tracked frames", "run Track again")
            params = SpacetimeParams()
            left, right, T, K, B = frames_from_recording(
                str(sp.recording_dir), calib, poses, params.max_frames, cancelled=lambda: ctx.cancelled,
                progress=lambda f, m: prog(0.12 * f, f"Reading the frames: {m}"))
            ctx.check()
            V, F, S = _read_stage_mesh(src_dir / "mesh.ply")
            V2, _, rep = refine_from_images(V, F, left, right, T, K, B, params, cancelled=lambda: ctx.cancelled,
                                            progress=lambda f, m: prog(0.12 + 0.8 * f, f"Refining: {m}"))
            del left, right
            ctx.check()
            rep.pop("patch_results", None)
            covered = round(100.0 * float(rep["covered_share"]), 1)
            fields = {"source": src, "frames": rep["frames"], "patches": rep["patches"], "kept": rep["kept"],
                      "covered_pct": covered, "moved_mm": rep["moved"]["p95"], "device": rep["device"]}
            if rep["kept"] < SPACETIME_MIN_PATCHES or rep["covered_share"] < REFINE_MIN_SHARE:
                msg = (f"Too few parts of the surface hold dots in enough frames ({rep['kept']:,} of "
                       f"{rep['patches']:,} patches), so the mesh was left as it is")
                self._stage_end(sp, "spacetime", state="skipped", summary=msg, **fields)
                return msg
            prog(0.93, "Saving the mesh", force=True)
            tmp = sp.dir / f"spacetime.tmp-{ctx.job.id}"
            _rmtree(tmp)
            tmp.mkdir(parents=True)
            try:
                save_mesh(tmp / "mesh.ply", V2, F, vertex_normals(V2, F), quality=S)  # each vertex keeps its sigma
                for name in ("trajectory.csv", "laser_points.ply", "marker_map.json"):
                    if (src_dir / name).is_file():
                        shutil.copy2(src_dir / name, tmp / name)
                report: dict = {}
                if (src_dir / "report.json").is_file():
                    report = json.loads((src_dir / "report.json").read_text(encoding="utf-8"))
                report["spacetime"] = {**rep, "source": src}
                (tmp / "report.json").write_text(json.dumps(jsonable(report), indent=1), encoding="utf-8")
                ctx.check()
                _move_dir(tmp, sp.dir / "spacetime")
            finally:
                _rmtree(tmp)
            with sp.lock:  # the lasers refine the new surface
                sp.state["stages"]["refine"] = _empty_state(sp.pid, sp.sid)["stages"]["refine"]
                _rmtree(sp.dir / "refine")
            self._new_base(sp, "spacetime", sp.dir / "spacetime", report, quality)
            where = "on the GPU" if rep["device"] == "gpu" else "on the CPU"
            summary = (f"Measured {covered:g} % of the surface against {rep['frames']:,} frames ({rep['kept']:,} patches, "
                       f"{where}); it moved by up to {rep['moved']['p95']:.2f} mm (95 %)")
            self._stage_end(sp, "spacetime", state="done", summary=summary, job_id=ctx.job.id, **fields)
            return summary
        except (JobCancelled, InterruptedError):
            self._stage_restore(sp, "spacetime", before)
            raise
        except Exception as e:
            self._stage_end(sp, "spacetime", state="failed", summary=f"Refining from the images failed: {e}")
            raise

    def _stage_job(self, pid: str, sid: str, kind: str, body: Callable[[Any, ScanProcess, _Prog], str],
                   *, need_track: bool, message: str, meta: dict | None = None) -> Any:
        with self.st._lock:
            self._check_free(pid, sid)
            self._need_recording(pid, sid)
            sp = self.scan(pid, sid)
            if need_track:
                self._need_track(sp)
            had_result = (sp.result_dir / "mesh.ply").is_file()
            self.st.projects.update_scan(pid, sid, status="processing")

            def run(ctx: Any) -> dict:
                msg = body(ctx, sp, _Prog(ctx, 0.0, 1.0))
                return {"project_id": pid, "scan_id": sid, "scan": self.st.projects.get_scan(pid, sid), "message": msg}

            def done(job: Any) -> None:
                self._settle_status(sp, job, had_result)
                if job.state == "failed":
                    self.st.notify("error", f"{message} failed: {job.error}")
                self.publish(sp)

            job = self.st.jobs.submit(kind, run, message=f"Queued: {message}",
                                      meta={"project_id": pid, "scan_id": sid, **(meta or {})}, on_done=done)
        self.publish(sp)
        return job

    def _settle_status(self, sp: ScanProcess, job: Any, had_result: bool) -> None:
        """The scan's status after a stage job: ``processed`` when it has a mesh, else ``failed``
        (with the error) or ``recorded`` (cancelled, or a stage that changed nothing)."""
        has = (sp.result_dir / "mesh.ply").is_file()
        if job.state == "failed" and not has:
            fields: dict[str, Any] = {"status": "failed", "error": job.error}
        else:
            fields = {"status": "processed" if has else "recorded"}
        safe_call("updating the scan", lambda: self.st.projects.update_scan(sp.pid, sp.sid, **fields))

    @staticmethod
    def _quality_arg(quality: Any) -> str:
        """A requested quality, normalised ("full" = "standard"); 400 if unknown."""
        try:
            return normalize_quality(quality)
        except ValueError:
            raise ApiError(400, f"unknown quality {quality!r}", "use standard, best or preview") from None

    def submit_track(self, pid: str, sid: str, quality: str) -> Any:
        quality = self._quality_arg(quality)

        def body(ctx: Any, sp: ScanProcess, prog: _Prog) -> str:
            self._do_track(ctx, sp, prog, quality)
            return sp.state["stages"]["track"]["summary"] or "Tracked"

        return self._stage_job(pid, sid, "process_track", body, need_track=False, message="Tracking",
                               meta={"quality": quality})

    def submit_straighten(self, pid: str, sid: str) -> Any:
        return self._stage_job(pid, sid, "process_straighten", lambda ctx, sp, prog: self._do_straighten(ctx, sp, prog),
                               need_track=True, message="Straightening")

    def submit_refine(self, pid: str, sid: str) -> Any:
        return self._stage_job(pid, sid, "process_refine", lambda ctx, sp, prog: self._do_refine(ctx, sp, prog),
                               need_track=True, message="Refining with the laser lines")

    def submit_spacetime(self, pid: str, sid: str) -> Any:
        return self._stage_job(pid, sid, "process_spacetime", lambda ctx, sp, prog: self._do_spacetime(ctx, sp, prog),
                               need_track=True, message="Refining from the images")

    def submit_fuse(self, pid: str, sid: str, voxel_mm: float | None) -> Any:
        if voxel_mm is not None:
            _num({"voxel_mm": voxel_mm}, "voxel_mm", "fuse", *VOXEL_RANGE)

        def body(ctx: Any, sp: ScanProcess, prog: _Prog) -> str:
            self._do_fuse(ctx, sp, prog, voxel_mm)
            return sp.state["stages"]["fuse"]["summary"] or "Fused"

        return self._stage_job(pid, sid, "process_fuse", body, need_track=True, message="Fusing",
                               meta={"voxel_mm": voxel_mm})

    # ------------------------------------------------------------------ recipes
    def _apply_ops(self, ctx: Any, sp: ScanProcess, ops: list[dict], prog: _Prog) -> list[str]:
        """Replace the edits with ``ops`` (each becomes an edit)."""
        with sp.lock:
            if sp.state["edits"] or sp.state["cursor"]:
                sp.drop_edits_after(0)
                self._show_step(sp, 0)
        self.publish(sp)
        out = []
        for i, o in enumerate(ops):
            params, label = normalize_op(o["op"], o.get("params") or {})
            entry = self._run_edit(ctx, sp, o["op"], params, label, prog.sub(i / max(len(ops), 1), (i + 1) / max(len(ops), 1)))
            out.append(entry["summary"])
            self.publish(sp)
        return out

    def _run_recipe(self, ctx: Any, sp: ScanProcess, recipe: dict, scope: str, prog: _Prog,
                    quality: str | None = None) -> str:
        s = recipe.get("settings") or {}
        ops = recipe.get("ops") or []
        p_ops = 0.0
        if scope == "all":
            q = quality_or_none(quality or s.get("quality")) or "standard"
            straighten = s.get("straighten", "auto") == "auto"
            refine = s.get("refine", "auto") == "auto"
            p_ops = 0.9 if refine else 0.88
            p_track = (0.72 if straighten else 0.82) if refine else (0.75 if straighten else 0.85)
            p_refine = 0.84 if straighten else p_track
            self._do_track(ctx, sp, prog.sub(0.0, p_track), q, s.get("voxel_mm"))
            if straighten:
                self._do_straighten(ctx, sp, prog.sub(p_track, p_refine if refine else 0.88))
            if refine:
                try:
                    self._do_refine(ctx, sp, prog.sub(p_refine, p_ops))
                except (JobCancelled, InterruptedError):
                    raise
                except Exception:  # the stage says it failed; the mesh stays as fused
                    log.exception("refine with lasers failed in a recipe")
        else:
            self._need_base(sp)
        sums = self._apply_ops(ctx, sp, ops, prog.sub(p_ops, 1.0))
        return f"{recipe.get('name', 'Recipe')} applied" + (f": {'; '.join(sums)}" if sums else "")

    def submit_recipe(self, pid: str, sid: str, recipe_id: str, scope: str) -> Any:
        if scope not in ("edits", "all"):
            raise ApiError(422, f"unknown scope {scope!r}", "use edits or all")
        recipe = self.recipes.get(recipe_id)  # 404
        if scope == "edits":
            with self.st._lock:
                self._check_free(pid, sid)
                sp = self.scan(pid, sid)
                self._need_base(sp)

                def run(ctx: Any) -> dict:
                    msg = self._run_recipe(ctx, sp, recipe, "edits", _Prog(ctx, 0.0, 1.0))
                    return {"project_id": pid, "scan_id": sid, "recipe_id": recipe_id, "message": msg}

                def done(job: Any) -> None:
                    if job.state == "failed":
                        self.st.notify("warn", f"The recipe {recipe['name']} stopped: {job.error}")
                    self.publish(sp)

                job = self.st.jobs.submit("process_recipe", run, message=f"Queued: recipe {recipe['name']}",
                                          meta={"project_id": pid, "scan_id": sid, "recipe_id": recipe_id,
                                                "recipe_name": recipe["name"], "scope": scope}, on_done=done)
            self.publish(sp)
            return job
        return self._stage_job(pid, sid, "process_recipe",
                               lambda ctx, sp, prog: self._run_recipe(ctx, sp, recipe, "all", prog),
                               need_track=False, message=f"The recipe {recipe['name']}",
                               meta={"recipe_id": recipe_id, "recipe_name": recipe["name"], "scope": scope})

    def make_mesh(self, pid: str, sid: str, quality: str) -> Any:
        """``POST .../process`` (M1 "Make mesh"): the Default recipe, all stages, at ``quality``."""
        quality = self._quality_arg(quality)
        recipe = self.recipes.get("default")
        with self.st._lock:
            entry = self._check_free(pid, sid, make_mesh=True)
            self._need_recording(pid, sid)
            sp = self.scan(pid, sid)
            had_result = (sp.result_dir / "mesh.ply").is_file()
            self.st.projects.update_scan(pid, sid, status="processing", error=None)

            def run(ctx: Any) -> dict:
                self._run_recipe(ctx, sp, recipe, "all", _Prog(ctx, 0.0, 1.0), quality=quality)
                scan = self.st.projects.get_scan(pid, sid)
                return {"project_id": pid, "scan_id": sid, "scan": scan, "message": "Mesh ready"}

            def done(job: Any) -> None:
                if job.state == "done":
                    self.st.notify("info", f"Processing done: {entry.get('name', sid)}")
                elif job.state == "failed":  # as in M1: failed, with the reason
                    safe_call("updating the scan",
                              lambda: self.st.projects.update_scan(pid, sid, status="failed", error=job.error))
                    self.st.notify("error", f"Processing failed: {job.error}. The raw recording is kept; try again "
                                            "(or with quality 'preview').")
                else:
                    self._settle_status(sp, job, had_result)
                    self.st.notify("info", "Processing cancelled; the raw recording is kept")
                self.publish(sp)

            job = self.st.jobs.submit("process_scan", run, message="Queued for processing",
                                      meta={"project_id": pid, "scan_id": sid, "quality": quality}, on_done=done)
        self.publish(sp)
        return job

    def save_recipe(self, name: str, description: str | None, from_scan: dict | None, settings: dict | None,
                    ops: list | None) -> dict:
        if from_scan is not None:
            sp = self.scan(str(from_scan.get("project_id")), str(from_scan.get("scan_id")))
            with sp.lock:
                st = sp.state
                if st["base"] is None:
                    raise ApiError(409, "this scan has no processing steps to save", "run Track and some edits first")
                track = st["stages"]["track"]
                made_by = st["base"]["stage"]
                for name in ("refine", "spacetime"):  # a refinement: the stage it refined
                    if made_by == name:
                        made_by = st["stages"][name].get("source") or "track"
                voxel = st["stages"]["fuse"].get("voxel_mm") if made_by == "fuse" else track.get("voxel_mm")
                settings = {"quality": quality_or_none(track.get("quality")) or "standard",
                            "straighten": "auto" if st["stages"]["straighten"]["state"] in ("done", "skipped") else "off",
                            "refine": "auto" if st["stages"]["refine"]["state"] in ("done", "skipped") else "off",
                            "voxel_mm": voxel}
                ops = [{"op": e["op"], "params": e["params"]} for e in st["edits"][: st["cursor"]]
                       if e["op"] in RECIPE_OPS]
        elif settings is None or ops is None:
            raise ApiError(422, "send either from_scan or both settings and ops",
                           "for example {name, from_scan: {project_id, scan_id}}")
        return self.recipes.create(name, description, settings, ops)

    # ------------------------------------------------------------------ overlays
    def holes(self, pid: str, sid: str) -> list:
        sp = self.scan(pid, sid)
        self._need_base(sp)
        return sp.holes(lambda: self.current_mesh(sp))

    def fit(self, pid: str, sid: str, kind: str, face: int, point: Any, tolerance_mm: float | None = None,
            version: int | None = None) -> dict:
        """``POST .../fit``: one click on the current mesh grows a plane, cylinder or hole region and
        fits it (:func:`ohs.measure.fit_at`). Read-only; the mesh is kept per version for the next
        click. ``version``: the mesh version the click was made on (409 when it changed)."""
        from ohs.measure import KINDS, fit_at

        if kind not in KINDS:
            raise ApiError(422, f"unknown kind {kind!r}", f"use one of: {', '.join(KINDS)}")
        sp = self.scan(pid, sid)
        self._need_base(sp)
        with sp.lock:  # the step's arrays, loaded once per version
            v = int(sp.state["version"])
            if version is not None and int(version) != v:
                raise ApiError(409, "the mesh changed since this click", "reload the mesh and click again")
            cached = sp._fit_mesh
            if cached is None or cached[0] != v:
                V, F, S, Fl = self._load_step(sp, int(sp.state["cursor"]))
                cached = sp._fit_mesh = (v, V, F, S, Fl)
        _, V, F, S, Fl = cached
        if not 0 <= int(face) < len(F):
            raise ApiError(422, f"face {face} is not on the mesh ({len(F)} faces)", "reload the mesh and click again")
        try:
            r = fit_at(V, F, int(face), point, kind, sigma=S, filled=Fl, tolerance_mm=tolerance_mm)
        except ValueError as e:
            hint = {"plane": "click in the middle of a flat area",
                    "cylinder": "click on the curved wall of a boss, shaft or bore",
                    "hole": "click near the edge of a hole that was scanned all round"}[kind]
            raise ApiError(422, str(e), hint) from None
        faces = np.asarray(r.faces, dtype="<u4")
        return {"kind": kind, "shape": r.fit.to_dict(), "faces": base64.b64encode(faces.tobytes()).decode("ascii"),
                "n_faces": int(len(faces)),
                "tolerance_mm": None if r.tolerance_mm is None else float(r.tolerance_mm),  # None for a hole
                "mean_sigma_mm": None if r.mean_sigma_mm is None else float(r.mean_sigma_mm),
                "warnings": [str(w) for w in r.warnings], "version": v}

    def trajectory(self, pid: str, sid: str) -> dict:
        sp = self.scan(pid, sid)
        path = sp.dir / "track" / "trajectory.csv"
        if not path.is_file():
            raise ApiError(404, "this scan has no trajectory yet", "run Track (or Make mesh) first")
        rows = [(i, T) for i, _, ok, T in read_trajectory(path) if ok]
        step = max(1, math.ceil(len(rows) / MAX_TRAJ_POINTS))
        keep = rows[::step]
        if rows and keep[-1][0] != rows[-1][0]:
            keep.append(rows[-1])
        idx = np.array([i for i, _ in keep], dtype=np.int64)
        raw = [[round(float(v), 2) for v in T[:3, 3]] for _, T in keep]
        straightened = None
        loops: list[list[int]] = []
        with sp.lock:
            done = sp.state["stages"]["straighten"]["state"] == "done"
        sp_path = sp.dir / "straighten" / "trajectory.csv"
        if done and sp_path.is_file():
            corr = {i: T for i, _, ok, T in read_trajectory(sp_path)}
            straightened = [[round(float(v), 2) for v in (corr[i] if i in corr else T)[:3, 3]] for i, T in keep]
        rep_path = sp.dir / "straighten.json"
        if rep_path.is_file() and len(idx):
            try:
                rep = json.loads(rep_path.read_text(encoding="utf-8"))
                for pair in rep.get("loops") or []:
                    a, b = (int(pair[0]), int(pair[1]))
                    ia = int(np.clip(np.searchsorted(idx, a), 0, len(idx) - 1))
                    ib = int(np.clip(np.searchsorted(idx, b), 0, len(idx) - 1))
                    loops.append([ia, ib])
            except (OSError, ValueError, TypeError, IndexError) as e:
                log.warning("straighten.json of %s/%s unreadable: %s", pid, sid, e)
        return {"raw": raw, "straightened": straightened, "loops": loops, "frames": idx.tolist()}
