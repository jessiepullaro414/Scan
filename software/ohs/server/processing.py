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

**Make mesh again** (UX_PLAN R2): Make mesh and a recipe on everything build in ``scans/<sid>/.remake-<job id>/``
(its own ``process/`` and ``result/``) and take the old ones' place only when they have finished
(:meth:`ProcessManager._run_staged`, :func:`swap_in`); a cancelled or failed run leaves the scan as it was, and an
app that stopped on the way is put right at the next start (:meth:`ProcessManager.recover_remakes`).
A scan processed before M3 (``result/mesh.ply`` but no ``state.json``) is taken over on first
use: its result becomes the ``track`` stage and the base.

**Undo / redo** load the step's cached mesh (``cache/``), so the last 10 steps are instant (the
time to write the result files). A step outside the cache is rebuilt from the nearest cached
one by replaying the edits (selections from ``selections/``), in the request.

**Freed space** (FEATURE_PLAN R2, :mod:`ohs.server.keep`). When the user frees a scan's space, the
current step becomes the base (:meth:`ProcessManager.keep_current_only`: ``base.in_result``, it is read
from ``result/mesh.ply``, which holds it bit for bit, and gets its cache file again only when a new
edit is about to change the result; ``base.key`` names the step's cache file instead when the result
differs from it; ``base.applied`` lists the edits that made it) and the cache files, the selections and
the stage folders go. Edits, fits and checks work as before; undo stops at that mesh; a step that needs
a removed stage folder answers 409 in plain words (:meth:`ProcessManager._removed`), and without a
recording so does everything that reads the frames. "Refine with lasers" needs no recording, only
the base stage's mesh and laser points (a merged scan can be refined).

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

from ohs.server.jobs import JobCancelled, failure_notice, failure_text
from ohs.server.projects import report_summary, user_warnings
from ohs.server.protocol import (QUALITY_NAMES, ApiError, jsonable, normalize_quality, now_iso, plain_reason,
                                 quality_or_none, sentence)
from ohs.server.util import safe_call
from ohs.track.posegraph import read_trajectory  # the one reader of trajectory.csv (also in __all__ here)

__all__ = ["ProcessManager", "RecipeStore", "ScanProcess", "BUILTIN_RECIPES", "PROCESS_KINDS", "MESH_OPS",
           "RECIPE_OPS", "normalize_op", "read_trajectory"]

log = logging.getLogger(__name__)

STAGES = ("track", "straighten", "fuse", "spacetime", "refine")
#: Make mesh's "Make only the thin walls finer" (ENGINE_PLAN X5 step 2, a choice per scan): a stage of its own, kept in
#: ``state.json`` like the others but in the API's ``stages`` (and ``making.stages``) only once it ran, so that a scan
#: nobody chose it for reads exactly as before
FINE_WALLS = "fine_walls"
ALL_STAGES = STAGES + (FINE_WALLS,)
MESH_OPS = ("remove_floaters", "fill_holes", "smooth", "decimate", "delete_faces", "keep_faces", "remove_table")
#: edits a recipe may hold (selections belong to one scan); "Remove the table" may be in a user's
#: recipe, never in a built-in one
RECIPE_OPS = MESH_OPS[:4] + ("remove_table",)
_SELECTION_OPS = ("delete_faces", "keep_faces", "remove_table")  # edits replayed from their saved faces
#: edits that break ties by position when ``[process] repeatable_edits`` is on (MAINTENANCE_PLAN F15): such
#: an edit records ``by_position: true`` in its params, and a replay (undo / redo, a step rebuilt from the
#: cache) goes by what the edit recorded, not by today's setting. A selection is its faces' numbers.
_BY_POSITION_OPS = ("remove_floaters", "fill_holes", "smooth", "decimate", "remove_table")
TABLE_HINT = "If the part does stand on a table in this scan, select the table with the selection tools and delete it."
#: an edit's and a parameter's names in words (what the user reads in a refusal; UX_PLAN U9)
_OP_WORDS = {"remove_floaters": "Remove loose bits", "fill_holes": "Fill holes", "smooth": "Smooth",
             "decimate": "Simplify", "delete_faces": "Delete the selection", "keep_faces": "Keep the selection",
             "remove_table": "Remove the table", "settings": "The recipe's settings", "fuse": "Build the surface"}
_PARAM_WORDS = {"min_triangles": "the smallest piece (in triangles)", "min_size_mm": "the size of a loose bit",
                "min_fraction": "the share of the mesh", "max_hole_mm": "the hole size",
                "strength": "the strength", "target_triangles": "the number of triangles",
                "ratio": "the share of the triangles", "max_error_mm": "the largest change",
                "margin_mm": "the margin", "voxel_mm": "the mesh detail"}
#: a stage's work in words, for "... failed"
_STAGE_WHAT = {"track": "Following the camera", "straighten": "Straightening", "fuse": "Building the surface",
               "refine": "Refining with the laser lines", "spacetime": "Refining from the images",
               FINE_WALLS: "Making the thin walls finer"}
#: a request the page should never send (an old page, or a bug)
_RELOAD = "Reload the page and try again."
PROCESS_KINDS = ("process_scan", "process_track", "process_straighten", "process_fuse", "process_spacetime",
                 "process_refine", "mesh_edit", "process_recipe")
MIN_LASER_POINTS = 200  # fewer laser points than this can't anchor anything
#: refine changes the mesh only when it would move at least this share of the vertices; below, the
#: corrections are the lasers' own scatter (0.4-0.7 % on unbiased simulated scans, 3-14 % with a bias)
REFINE_MIN_SHARE = 0.01
SPACETIME_MIN_PATCHES = 50  # fewer kept patches than this: "Refine from the images" changes nothing
CACHE_STEPS = 10
#: the reasons route's short plan: this many poses at most (FEATURE_PLAN N1, "3 places to scan from")
PLAN_POSES = 3
MAX_TRAJ_POINTS = 2000
MAX_HOLE_OUTLINES = 500
VOXEL_RANGE = (0.25, 10.0)
FORMAT = 1
THIN_PARTS_HINT = "thin_parts"  # the scan entry's hints_dismissed holds it once the user put the hint away (X5)
DEFAULT_FLOATERS = {"min_size_mm": 5.0}
#: UX_PLAN R18: the share of a Make mesh's time each of its parts takes, so that its percentage moves with the time.
#: Measured 2026-11-21 on simulated scans of 240 frames at Standard quality, the Default recipe, on this PC's graphics
#: card (Radeon 680M, shared with other work): the sphere 257 s (Follow the camera 42 %, Straighten 38 %, Refine with
#: lasers 4 %, the edits 15 %), the bracket 120 s (57 %, 29 %, 2 %, 10 %), the car panel (180 frames) 123 s (46 %, 42 %,
#: 2 %, 9 %); in all three Straighten found no loop and its time was the laser crossings' (about 85 %) and the loop
#: search's. A hand scan that closes loops fuses once more in Straighten (about as long again as the fuse in Follow the
#: camera), so Straighten keeps the larger shares. Before, Follow the camera had 72 % and Straighten 12 %, and the percentage stood at 80 to 83 % while
#: Straighten ran for minutes. The parts that do not run are left out and the rest scaled (:func:`make_mesh_plan`).
MAKE_MESH_SHARES = {"track": 0.47, "straighten": 0.37, "refine": 0.03, "edits": 0.13}
#: "Refine from the images" inside Make mesh (``[process] refine_from_images``, ENGINE_PLAN X6, off by default): its
#: share against the parts above (they keep theirs and are scaled with it)
SPACETIME_SHARE = 0.15
#: "Make only the thin walls finer" inside Make mesh (ENGINE_PLAN X5 step 2, a choice per scan): its share against the
#: parts above, only when it runs. Measured 2026-11-22 (VALIDATION.md, "Fine mesh detail on thin walls only"), what Make
#: mesh runs on this PC's graphics card: the step 5.8 to 6.8 s of 47.9 to 49.7 s on the standing walls and 10.0 to
#: 10.9 s of 89.7 to 91.7 s on the sheet, tracked: 12 to 14 % of the whole, so 0.15 against the others' 1.0
FINE_WALLS_SHARE = 0.15
#: the step runs at the mesh detail it was measured at only (1 mm; any other: skipped, with the reason)
FINE_WALLS_VOXEL_MM = 1.0
#: the kept depth is written only when the scan's drive has room for it and this much more: the new mesh's own files
#: (a run beside the result holds two results while it runs) and room for the system; else the step is skipped
#: before Follow the camera, with the space it needs (the app warns, it does not block)
FINE_WALLS_DISK_MARGIN_MB = 2048.0
#: Straighten's own parts, of its share: the loop search, the fuse at the loops' poses (only with loops), the laser
#: crossings, and the fuse at the crossings' poses (only when they changed the poses); a part that does not run is
#: passed over (the percentage jumps ahead, never back: Straighten saves its mesh at 0.96; before, the first fuse
#: started below the loop search's end)
STRAIGHTEN_SPANS = {"loops": (0.0, 0.1), "fuse": (0.1, 0.45), "crossings": (0.45, 0.75), "fuse2": (0.75, 0.95)}

#: what the busy line says while a job of each kind runs
_BUSY = {"process_scan": "Making the mesh…", "process_track": "Following the camera…",
         "process_straighten": "Straightening…", "process_fuse": "Building the surface…",
         "process_spacetime": "Refining from the images…", "process_refine": "Refining with the laser lines…",
         "process_recipe": "Applying the recipe…"}
_BUSY_OPS = {"remove_floaters": "Removing loose bits…", "fill_holes": "Filling holes…", "smooth": "Smoothing…",
             "decimate": "Simplifying…", "delete_faces": "Deleting the selection…",
             "keep_faces": "Keeping the selection…", "remove_table": "Removing the table…"}

# --------------------------------------------------------------------------- recipes
BUILTIN_RECIPES: list[dict] = [
    {"id": "default", "name": "Default", "builtin": True,
     "description": "What Make mesh does: refines the surface with the laser lines, removes loose bits and closes "
                    "pin holes up to 5 mm around (about Ø 1.6 mm). No smoothing and no simplification, so "
                    "measurements stay true to the scan.",
     "settings": {"quality": "standard", "straighten": "auto", "refine": "auto", "voxel_mm": None},
     "ops": [{"op": "remove_floaters", "params": {"min_size_mm": 5.0, "min_fraction": 0.01}},
             {"op": "fill_holes", "params": {"max_hole_mm": 5.0}}]},
    {"id": "body-panel", "name": "Body panel", "builtin": True,
     "description": "For smooth sheet metal: closes holes up to 15 mm around (about Ø 5 mm) and smooths the "
                    "surface while keeping creases and edges.",
     "settings": {"quality": "standard", "straighten": "auto", "refine": "auto", "voxel_mm": None},
     "ops": [{"op": "remove_floaters", "params": {"min_size_mm": 5.0, "min_fraction": 0.01}},
             {"op": "fill_holes", "params": {"max_hole_mm": 15.0}},
             # away from edges ("Off edges"), the owner's decision of 2026-11-17: it protects edges, where
             # "within 2σ", used here until then, protected none (VALIDATION.md, "Smoothing's "within 2σ" limit")
             {"op": "smooth", "params": {"method": "bilateral", "strength": 0.5, "keep_edges": True,
                                         "away_from_edges": True}}]},
    {"id": "bracket", "name": "Bracket / mechanical", "builtin": True,
     "description": "For brackets and machined parts: keeps sharp edges, light smoothing, closes pin holes up to "
                    "5 mm around (about Ø 1.6 mm).",
     "settings": {"quality": "standard", "straighten": "auto", "refine": "auto", "voxel_mm": None},
     "ops": [{"op": "remove_floaters", "params": {"min_size_mm": 5.0, "min_fraction": 0.01}},
             {"op": "fill_holes", "params": {"max_hole_mm": 5.0}},
             {"op": "smooth", "params": {"method": "bilateral", "strength": 0.25, "keep_edges": True,
                                         "away_from_edges": True}}]},
    {"id": "quick-preview", "name": "Quick preview", "builtin": True,
     "description": "Fast look at a scan: preview quality, no straightening, simplified to about 200,000 "
                    "triangles. Not for measuring.",
     "settings": {"quality": "preview", "straighten": "off", "refine": "off", "voxel_mm": None},
     "ops": [{"op": "remove_floaters", "params": {"min_size_mm": 5.0, "min_fraction": 0.01}},
             {"op": "decimate", "params": {"target_triangles": 200_000}}]},
]


def _fmt(x: float) -> str:
    return f"{x:g}" if abs(x) >= 1 or x == 0 else f"{x:.2g}"


def holes_mm(around_mm: float) -> str:
    """A hole size as the edit means it, the length round the hole, with the diameter of a round hole of that
    length (UX_PLAN U13): "15 mm around (about Ø 5 mm)", "5 mm around (about Ø 1.6 mm)"."""
    d = float(around_mm) / math.pi
    dia = f"{d:.0f}" if d >= 3 else f"{d:.1f}" if d >= 0.1 else f"{d:.2g}"
    return f"{_fmt(float(around_mm))} mm around (about Ø {dia} mm)"


def _stage_failed(stage: str, e: BaseException) -> str:
    """A stage's summary when it failed: the refusal's own words (an ApiError), else the fixed words of a
    program error of that stage (:data:`ohs.server.jobs.FAILURE_TEXT`); the exception is in the log."""
    if isinstance(e, ApiError):
        return f"{_STAGE_WHAT.get(stage, stage.capitalize())} failed. {e}"
    log.warning("stage %s failed: %s: %s", stage, type(e).__name__, e)
    return failure_text(f"process_{stage}")


def _num(params: dict, key: str, op: str, lo: float, hi: float, *, integer: bool = False,
         lo_open: bool = False) -> float | int | None:
    if key not in params or params[key] is None:
        return None
    v = params[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise ApiError(422, f"{_OP_WORDS.get(op, op)}: {_PARAM_WORDS.get(key, key)} must be a number.",
                       "Type a number.")
    if integer:
        if float(v) != int(v):
            raise ApiError(422, f"{_OP_WORDS.get(op, op)}: {_PARAM_WORDS.get(key, key)} must be a whole number.",
                           "Type a whole number.")
        v = int(v)
    else:
        v = float(v)
    if (v <= lo if lo_open else v < lo) or v > hi:
        rng = f"{'above' if lo_open else 'at least'} {_fmt(lo)} and at most {_fmt(hi)}"
        raise ApiError(422, f"{_OP_WORDS.get(op, op)}: {_PARAM_WORDS.get(key, key)} must be {rng} (not {v:g}).",
                       f"Choose {_PARAM_WORDS.get(key, key)} {rng}.")
    return v


def normalize_op(op: str, params: dict | None) -> tuple[dict, str]:
    """Validate an edit's parameters; returns ``(normalised params, label)`` or raises 422.

    ``delete_faces`` / ``keep_faces`` are validated by the caller (the faces are decoded there)."""
    params = dict(params or {})
    allowed = {"remove_floaters": {"min_triangles", "min_size_mm", "keep_largest_only", "min_fraction"},
               "fill_holes": {"max_hole_mm", "watertight"},
               "smooth": {"method", "strength", "keep_edges", "within_sigma", "flat_only", "away_from_edges"},
               "decimate": {"target_triangles", "ratio", "max_error_mm"}, "delete_faces": {"faces"},
               "keep_faces": {"faces"}, "remove_table": {"margin_mm", "keep"}}
    if op not in allowed:
        log.warning("unknown edit %r", op)
        raise ApiError(422, "The app does not know this mesh edit.", _RELOAD)
    extra = sorted(set(params) - allowed[op])
    if extra:
        log.warning("%s: unknown parameter %r", op, extra[0])
        raise ApiError(422, f"{_OP_WORDS[op]}: the page sent a setting this edit does not have.", _RELOAD)
    if op == "remove_floaters":
        out: dict[str, Any] = {}
        n = _num(params, "min_triangles", op, 1, 1e9, integer=True)
        s = _num(params, "min_size_mm", op, 0, 1e5, lo_open=True)
        fr = _num(params, "min_fraction", op, 0, 1, lo_open=True)
        keep = params.get("keep_largest_only")
        if keep is not None and not isinstance(keep, bool):
            raise ApiError(422, "Remove loose bits: keeping the largest piece only is either on or off.", _RELOAD)
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
        return out, "Remove loose bits " + " or ".join(parts)
    if op == "fill_holes":
        m = _num(params, "max_hole_mm", op, 0, 1e5, lo_open=True)
        if m is None:
            raise ApiError(422, "Fill holes: the hole size is missing.",
                           "Give the largest hole to fill, as the length round it in mm.")
        wt = params.get("watertight", False)
        if not isinstance(wt, bool):
            raise ApiError(422, "Fill holes: watertight is either on or off.", _RELOAD)
        if wt:
            return {"max_hole_mm": m, "watertight": True}, f"Make watertight: close every opening up to {holes_mm(m)}"
        return {"max_hole_mm": m}, f"Fill holes up to {holes_mm(m)}"
    if op == "smooth":
        method = params.get("method")
        if method not in ("taubin", "bilateral"):
            raise ApiError(422, "Smooth: the app does not know this way of smoothing.", _RELOAD)
        s = _num(params, "strength", op, 0, 1)
        if s is None:
            raise ApiError(422, "Smooth: the strength is missing.", "Choose a strength from 0 to 100 %.")
        keep = params.get("keep_edges", True)
        if not isinstance(keep, bool):
            raise ApiError(422, "Smooth: keeping the edges is either on or off.", _RELOAD)
        within = params.get("within_sigma", False)
        if not isinstance(within, bool):
            raise ApiError(422, "Smooth: the limit by the scan's scatter is either on or off.", _RELOAD)
        flat = params.get("flat_only", False)
        if not isinstance(flat, bool):
            raise ApiError(422, "Smooth: flat areas only is either on or off.", _RELOAD)
        away = params.get("away_from_edges", False)
        if not isinstance(away, bool):
            raise ApiError(422, "Smooth: away from edges is either on or off.", _RELOAD)
        if flat and away:
            raise ApiError(422, "Smooth: flat areas only, or away from edges, not both.", "Choose one of the two.")
        # in words, not the methods' names (UX_PLAN R12): "Smooth 50 % (keeping the shape, sharp edges kept, off edges)"
        where = ", flat areas only" if flat else ", off edges" if away else ""
        how = "keeping the shape" if method == "bilateral" else "evenly"
        label = (f"Smooth {round(100 * s)} % ({how}{', sharp edges kept' if keep else ''}{where}"
                 f"{', within the scatter' if within else ''})")
        out_s: dict[str, Any] = {"method": method, "strength": s, "keep_edges": keep}
        if flat:
            out_s["flat_only"] = True
        if away:
            out_s["away_from_edges"] = True
        if within:
            out_s["within_sigma"] = True
        return out_s, label
    if op == "decimate":
        t = _num(params, "target_triangles", op, 4, 1e9, integer=True)
        r = _num(params, "ratio", op, 0, 1, lo_open=True)
        e = _num(params, "max_error_mm", op, 0.001, 10)
        if t is not None and r is not None or (t is None and r is None and e is None):
            raise ApiError(422, "Simplify: give a number of triangles or a share of them (not both), or the "
                                "largest change.", "For example 200,000 triangles, or at most 0.02 mm of change.")
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
    if op == "remove_table":
        # the margin is always written down, so that a recipe runs the same when the default moves
        from ohs.mesh.support import MARGIN_MM

        m = _num(params, "margin_mm", op, 0, 20)
        out_t: dict[str, Any] = {"margin_mm": MARGIN_MM if m is None else m}
        # "Keep this": points (mm) on raised areas the user keeps; they travel with the edit
        keep = params.get("keep")
        if keep is not None:
            ok = isinstance(keep, list) and len(keep) <= 50 and all(
                isinstance(q, list) and len(q) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                          and math.isfinite(float(x)) for x in q) for q in keep)
            if not ok:
                raise ApiError(422, "Remove the table: the areas to keep cannot be read (at most 50).",
                               "Click Keep this on each raised area to keep again.")
            if keep:
                out_t["keep"] = [[round(float(x), 2) for x in q] for q in keep]
        # the label gets the count of faces when the edit has run ("Remove the table (12,345 faces)")
        return out_t, "Remove the table"
    return params, op


def _carry_op(op: str) -> str:
    """The edit the sigmas and filled flags follow ``op`` as: "Remove the table" deletes the faces
    it found, as a selection is deleted."""
    return "delete_faces" if op == "remove_table" else op


def _decode_faces(text: Any) -> np.ndarray:
    if not isinstance(text, str) or not text:
        raise ApiError(422, "The selection is missing.", "Select some triangles first.")
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        log.warning("a selection that is not base64")
        raise ApiError(422, "The selection the page sent cannot be read (it is not in the form the app reads).", _RELOAD) from None
    if len(raw) == 0:
        raise ApiError(422, "The selection is empty.", "Select some triangles first.")
    if len(raw) % 4:
        log.warning("a selection of %d bytes", len(raw))
        raise ApiError(422, "The selection the page sent cannot be read.", _RELOAD)
    return np.frombuffer(raw, dtype="<u4").astype(np.int64)


def _slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40].strip("-")
    return s or "recipe"


_NO_RECIPE = "Reload the page to see the recipes the app has."


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
            raise ApiError(404, "This recipe is no longer in the app.", _NO_RECIPE)
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
            raise ApiError(404, "This recipe is no longer in the app.", _NO_RECIPE) from None
        except (OSError, ValueError) as e:
            log.warning("recipe %s cannot be read: %s", p, e)
            raise ApiError(500, "This recipe's file cannot be read.",
                           "Delete the recipe, or restore its file from a backup.") from None

    @staticmethod
    def validate(settings: dict, ops: list) -> tuple[dict, list[dict]]:
        s = dict(settings or {})
        try:  # "full" is the old name of "standard"
            quality = normalize_quality(s.get("quality", "standard"))
        except ValueError:
            raise ApiError(422, "The recipe's quality must be Preview, Standard or Best.", _RELOAD) from None
        straighten = s.get("straighten", "auto")
        if straighten not in ("auto", "off"):
            raise ApiError(422, "The recipe's Straighten is either automatic or off.", _RELOAD)
        refine = s.get("refine", "auto")
        if refine not in ("auto", "off"):
            raise ApiError(422, "The recipe's Refine with the laser lines is either automatic or off.", _RELOAD)
        voxel = _num(s, "voxel_mm", "settings", *VOXEL_RANGE)
        if not isinstance(ops, list) or len(ops) > 30:
            raise ApiError(422, "A recipe has at most 30 steps.", "Remove some steps, then save it again.")
        out_ops = []
        for o in ops:
            if not isinstance(o, dict) or o.get("op") not in RECIPE_OPS:
                raise ApiError(422, "A recipe can hold Remove loose bits, Fill holes, Smooth, Simplify and Remove "
                                    "the table only.", "Selections belong to one scan and cannot be part of a recipe.")
            p, _ = normalize_op(o["op"], o.get("params") or {})
            out_ops.append({"op": o["op"], "params": p})
        return {"quality": quality, "straighten": straighten, "refine": refine, "voxel_mm": voxel}, out_ops

    def create(self, name: str, description: str | None, settings: dict, ops: list) -> dict:
        n = " ".join(str(name or "").split())
        if not n:
            raise ApiError(400, "The recipe name is empty.", "Type a name for the recipe.")
        if len(n) > 80:
            raise ApiError(400, f"The recipe name is too long ({len(n)} characters).", "Use at most 80 characters.")
        s, o = self.validate(settings, ops)
        builtin_ids = {r["id"] for r in BUILTIN_RECIPES}
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            for _ in range(100):
                rid = f"{_slug(n)}-{secrets.token_hex(2)}"
                if rid not in builtin_ids and not (self.root / f"{rid}.json").exists():
                    break
            else:  # pragma: no cover
                raise ApiError(500, "The recipe could not be saved under this name.", "Try another name.")
            rec = {"id": rid, "name": n, "description": " ".join(str(description or "").split())[:500],
                   "builtin": False, "settings": s, "ops": o, "created": now_iso()}
            _write_json(self.root / f"{rid}.json", rec)
        log.info("saved recipe %s (%s)", rid, n)
        return self._view(rec)

    def delete(self, rid: str) -> None:
        if self._builtin(rid) is not None:
            raise ApiError(409, f"\u201c{self._builtin(rid)['name']}\u201d is a built-in recipe and cannot be deleted.",
                           "Only recipes you saved can be deleted.")
        p = self._path(rid)
        with self._lock:
            try:
                p.unlink()
            except FileNotFoundError:
                raise ApiError(404, "This recipe is no longer in the app.", _NO_RECIPE) from None


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


def json_file_size(data: Any) -> int:
    """The bytes :func:`_write_json` writes for ``data`` (a text file: each line end is ``os.linesep``)."""
    text = json.dumps(jsonable(data), indent=1)
    return len(text.encode("utf-8")) + text.count("\n") * (len(os.linesep) - 1)


def _set_straighten(report: dict, rep: dict | None) -> bool:
    """Write what Straighten corrected (``rep``: its ``straighten.json``) into a stage report's
    calibration health block (FEATURE_PLAN H1). False when the report has no block to write into."""
    from ohs.calib.scan_health import straighten_summary

    h = report.get("health")
    if not isinstance(h, dict) or "error" in h:
        return False
    h["straighten"] = straighten_summary(rep)
    return True


def removed_pieces(V: np.ndarray, F: np.ndarray, kept: np.ndarray | None, params: dict | None = None) -> dict | None:
    """What "Remove loose bits" took (UX_PLAN U14): ``{pieces, triangles, largest_mm}`` of the pieces of ``(V, F)``
    (faces joined through shared edges, as the engine counts them) that have no face among ``kept``; ``largest_mm``
    is the largest bounding-box diagonal, the size the engine judges a piece by. None when nothing was removed.
    With the edit's ``params`` (UX_PLAN R23) also ``largest_rule``, the rule that took that largest piece:
    ``"size"`` (smaller than ``min_size_mm``), ``"share"`` (under ``min_fraction`` of the triangles, however large),
    ``"triangles"`` (under ``min_triangles``) or ``"largest_only"`` (Keep the largest piece only); and
    ``largest_triangles``, its count of triangles."""
    from ohs.mesh.clean import piece_sizes

    if kept is None or len(kept) >= len(F):
        return None
    flab, tri, diag = piece_sizes(V, F)
    gone = np.ones(len(tri), dtype=bool)
    gone[np.unique(flab[np.asarray(kept, dtype=np.int64)])] = False
    if not gone.any():
        return None
    out = {"pieces": int(gone.sum()), "triangles": int(tri[gone].sum()), "largest_mm": round(float(diag[gone].max()), 1)}
    if params is not None:
        i = int(np.flatnonzero(gone)[np.argmax(diag[gone])])
        out["largest_rule"] = _floater_rule(params, float(diag[i]), int(tri[i]), len(F))
        out["largest_triangles"] = int(tri[i])
    return out


def _floater_rule(params: dict, diag_mm: float, triangles: int, n_faces: int) -> str | None:
    """Which limit of a "Remove loose bits" edit took a piece of ``diag_mm`` across and ``triangles`` triangles of a
    mesh of ``n_faces`` (the limits combine with "or": the first that holds, in the label's order)."""
    if params.get("keep_largest_only"):
        return "largest_only"
    p = params if any(k in params for k in ("min_size_mm", "min_fraction", "min_triangles")) else DEFAULT_FLOATERS
    if p.get("min_size_mm") is not None and diag_mm < float(p["min_size_mm"]):
        return "size"
    if p.get("min_triangles") is not None and triangles < int(p["min_triangles"]):
        return "triangles"
    if p.get("min_fraction") is not None and triangles < float(p["min_fraction"]) * n_faces:
        return "share"
    return None


def _rule_words(rule: str | None, params: dict) -> str:
    """The rule that took the largest removed piece, as the summary says it after its size (R23)."""
    p = params if any(k in params for k in ("min_size_mm", "min_fraction", "min_triangles")) else DEFAULT_FLOATERS
    if rule == "size":
        return f", smaller than {_fmt(float(p['min_size_mm']))} mm"
    if rule == "triangles":
        return f" but under {int(p['min_triangles']):,} triangles"
    if rule == "share":
        return f" but under {_fmt(100 * float(p['min_fraction']))} % of the mesh"
    return ""


def _carry_path_check(report: dict, track_rep: dict | None) -> None:
    """A stage fused at given poses (straighten, fuse) has no camera path of its own to check: its report takes the
    track stage's ``path_check`` and ``warnings`` (ENGINE_PLAN X0a), since its poses come from that path. Nothing
    when the track's report has no check (processed before it)."""
    if not isinstance(track_rep, dict) or not isinstance(track_rep.get("path_check"), dict):
        return
    if report.get("path_check") is None:
        report["path_check"] = track_rep["path_check"]
    own = [w for w in report.get("warnings") or [] if isinstance(w, str)]
    carried = [w for w in track_rep.get("warnings") or [] if isinstance(w, str) and w not in own]
    report["warnings"] = carried + own


def _carry_crossings_warning(report: dict, crep: dict | None) -> bool:
    """Straighten's laser crossings changed the poses: their sentence for the user (``crep["warning"]``,
    :func:`ohs.track.curve_ba.acted_warning`; ENGINE_PLAN X0a) goes into the stage's fused report's ``warnings``, so
    the base's report (``result/report.json``) and the scan's summary carry it. Returns whether it was added."""
    w = (crep or {}).get("warning")
    if not isinstance(w, str) or not w.strip():
        return False
    ws = [x for x in report.get("warnings") or [] if isinstance(x, str)]
    if w in ws:
        return False
    report["warnings"] = ws + [w]
    return True


def _carry_health_path(report: dict, track_rep: dict | None) -> None:
    """A stage fused at given poses (straighten, fuse) tracks nothing, so its report alone cannot say whether the scan
    was made on the graphics card's path (UX_PLAN R5: its status said "processed without a graphics card" on a scan
    made with one): it takes the track stage's path as ``health_path`` (:func:`ohs.calib.health.path_of`), and its
    ``health_status`` is read again on that path. The refinements copy the report they refine, and so the path."""
    from ohs.calib import health

    if not isinstance(track_rep, dict) or not isinstance(report.get("health"), dict):
        return
    report["health_path"] = {**health.path_of(track_rep), "from": "track"}
    try:
        st = health.report_status(report)
    except Exception:  # a status must not cost the stage
        log.exception("the calibration status of a stage failed")
        return
    if st is not None:
        report["health_status"] = st


def preset_meshing(p: Any, width: int, voxel_mm: float | None, thin_walls: str | None) -> Any:
    """``ProcessParams`` ``p`` with a preset's meshing values (FEATURE_PLAN P2): the voxel size and the thin
    walls setting; ``None`` leaves a value as ``p`` has it (General: ``p`` itself). A processing stage and a merge
    (``ohs.server.aligning``) take them this one way."""
    from ohs.app.pipeline import default_fusion_params, effective_scale

    if voxel_mm is not None:
        fusion = p.pipeline.fusion or default_fusion_params(effective_scale(int(round(width * p.pipeline.scale))))
        p = dataclasses.replace(p, pipeline=dataclasses.replace(p.pipeline, fusion=dataclasses.replace(
            fusion, voxel_mm=float(voxel_mm))))
    if thin_walls is not None:  # the scan's preset names it; else [process] thin_walls
        p = dataclasses.replace(p, pipeline=dataclasses.replace(p.pipeline, thin_walls=str(thin_walls)))
    return p


def stage_params(cfg: Any, mode: str, quality: str, width: int, voxel_mm: float | None = None,
                 thin_walls: str | None = None) -> Any:
    """The ``ProcessParams`` every processing stage fuses with (Follow the camera, and the fuses of Straighten and
    Build the surface): :func:`ohs.server.studio.process_params` at ``quality`` with a preset's meshing values
    (:func:`preset_meshing`), every piece kept (removing floaters is an edit you can see and undo) and PLY only.
    ``tools/scorecard.py --chain app`` calls it too, so that it runs Make mesh with the server's own values."""
    from ohs.server.studio import process_params

    p = preset_meshing(process_params(cfg, mode, quality, width), width, voxel_mm, thin_walls)
    return dataclasses.replace(p, clean_min_component=0.0, export=("ply",))


def straighten_looped(rep: dict | None, poses: dict | None) -> tuple[bool, int]:
    """``(looped, loops)`` of a :func:`ohs.track.posegraph.straighten_trajectory` report: whether its loops or the tags
    in view (ENGINE_PLAN X8) changed the poses (then Straighten fuses at them, and that mesh is the laser crossings'
    reference), and how many loops it closed."""
    rep = rep or {}
    loops = rep.get("loops_accepted")
    if not isinstance(loops, int):
        loops = len(rep.get("loops") or [])
    tags = bool((rep.get("tags") or {}).get("acted"))
    return bool((loops or tags) and poses and rep.get("changed") is not False), loops


def straighten_tags_used(rep: dict | None) -> int | None:
    """How many tags held the scan in a Straighten report (ENGINE_PLAN X8 step 2), for the stage's ``tags``: None
    when the tags did not change the poses (or the report has no tags' step). With it the stage card can tell a move
    by the tags from one by the loops: ``loops`` counts the loops closed, ``correction_mm`` is both together."""
    tg = (rep or {}).get("tags")
    if not isinstance(tg, dict) or not tg.get("acted"):
        return None
    return len(tg.get("used") or [])


def refine_changes(rep: dict) -> bool:
    """Whether "Refine with lasers" changes the mesh, from :func:`ohs.mesh.anchor_to_points`' report: enough laser
    points on the mesh (:data:`MIN_LASER_POINTS`) and at least :data:`REFINE_MIN_SHARE` of the surface moved."""
    return not (rep["used"] < MIN_LASER_POINTS or rep["moved_share"] < REFINE_MIN_SHARE)


def save_refined_mesh(path: Path, V: np.ndarray, F: np.ndarray, S: np.ndarray | None, L: np.ndarray | None) -> None:
    """A refinement's mesh (``mesh.ply``): the vertices moved, each keeping its sigma and its count of looks."""
    from ohs.mesh import vertex_normals
    from ohs.track.export import save_mesh

    save_mesh(path, V, F, vertex_normals(V, F), quality=S, looks=L if L is not None and len(L) == len(V) else None)


def spacetime_changes(rep: dict) -> bool:
    """Whether "Refine from the images" changes the mesh, from :func:`ohs.track.spacetime.refine_from_images`' report:
    at least :data:`SPACETIME_MIN_PATCHES` patches kept (with the guard, the patches it used) and
    :data:`REFINE_MIN_SHARE` of the surface covered."""
    used = (rep.get("guard") or {}).get("used", rep["kept"])
    return not (used < SPACETIME_MIN_PATCHES or rep["covered_share"] < REFINE_MIN_SHARE)


def images_on_gpu() -> bool:
    """Whether "Refine from the images" runs on a graphics card here (:mod:`ohs.compute`). Make mesh runs it only then:
    on the CPU its search takes about 20 minutes for a short scan (VALIDATION.md, "Refine from the images")."""
    from ohs.compute import get_device

    return get_device() is not None


#: The hole reading from the camera images (ENGINE_PLAN X15 step 2) is offered on recordings at least this wide: it
#: was checked only on 1280 x 800 recordings.
HOLE_IMAGES_MIN_WIDTH = 1280
#: ... and without a graphics card only when this is True. Measured 2026-11-26 (VALIDATION.md, "Holes from the camera
#: images in the app"): with the matcher on the processor the stop rule is met on scans tracked by the graphics card,
#: but a scan tracked and meshed on the processor was not run, so it stays off.
HOLE_IMAGES_ON_CPU = False


def face_normal_at(V: np.ndarray, F: np.ndarray, face: int, point: np.ndarray, radius_mm: float = 2.0) -> np.ndarray:
    """The mesh's unit normal at a click on ``face``: the area-weighted mean normal of the faces whose centres lie
    within ``radius_mm`` of ``point`` and that face the clicked face's way within about 37 degrees (a rim's or a bore's
    faces nearby are left out); the clicked face's own normal when nothing else is near."""
    tri = np.asarray(V, dtype=np.float64)[np.asarray(F)[int(face)]]
    n0 = np.cross(tri[1] - tri[0], tri[2] - tri[0])
    if not np.linalg.norm(n0) > 0:
        return np.array([0.0, 0.0, 1.0])
    n0 = n0 / np.linalg.norm(n0)
    Vd = np.asarray(V, dtype=np.float64)
    Fi = np.asarray(F)
    near = np.flatnonzero(np.linalg.norm(Vd[Fi].mean(axis=1) - np.asarray(point, dtype=np.float64), axis=1) <= radius_mm)
    if len(near):
        T = Vd[Fi[near]]
        N = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])  # area-weighted (twice the area)
        a = np.linalg.norm(N, axis=1)
        keep = a > 0
        cos = (N[keep] @ n0) / a[keep]
        m = N[keep][cos > 0.8].sum(axis=0)
        if np.linalg.norm(m) > 0:
            return m / np.linalg.norm(m)
    return n0


#: Make mesh's guard on "Refine from the images" (ENGINE_PLAN X6; ``SpacetimeParams.guard_*``): a patch is used only
#: where the mesh within its fit turns by at most 30 degrees, no free edge lies within 3 mm and no other side of a wall
#: within 2 mm behind it; a vertex that would move more than 0.3 mm stays as it is. Unguarded, the bracket's edges read
#: 0.15 -> 0.24 mm (median) and the hole plate's 10 mm hole 0.2 mm larger; the stage "Refine from the images" itself
#: runs unguarded, as before.
MAKE_MESH_SPACETIME_GUARD = {"guard_smooth_deg": 30.0, "guard_edge_mm": 3.0, "guard_thin_mm": 2.0, "guard_move_mm": 0.3}


def make_mesh_spacetime_params() -> Any:
    """The ``SpacetimeParams`` Make mesh runs "Refine from the images" with (``[process] refine_from_images``,
    ENGINE_PLAN X6): the stage's own, with :data:`MAKE_MESH_SPACETIME_GUARD`."""
    from dataclasses import replace

    from ohs.track.spacetime import SpacetimeParams

    return replace(SpacetimeParams(), **MAKE_MESH_SPACETIME_GUARD)


def spacetime_refine(recording: Path, calib: Any, src_dir: Path, poses: dict[int, np.ndarray], params: Any = None, *,
                     cancelled: Callable[[], bool] | None = None,
                     progress: Callable[[float, str], None] | None = None) -> tuple:
    """"Refine from the images" on a stage's mesh (``src_dir/mesh.ply``): up to ``params.max_frames`` frames of
    ``recording`` with a pose in ``poses`` (:func:`ohs.track.spacetime.frames_from_recording`), then
    :func:`ohs.track.spacetime.refine_from_images`. The one computation behind the stage ("Refine from the images")
    and Make mesh's ``[process] refine_from_images`` (ENGINE_PLAN X6). Returns ``(V2, F, S, L, report)``: the moved
    vertices, the faces, each vertex's sigma and count of looks (kept as they were), and the engine's report (with its
    ``patch_results``: drop them before saving)."""
    from ohs.track.spacetime import SpacetimeParams, frames_from_recording, refine_from_images

    say = progress or (lambda f, m: None)
    params = params or SpacetimeParams()
    left, right, T, K, B = frames_from_recording(str(recording), calib, poses, params.max_frames, cancelled=cancelled,
                                                 progress=lambda f, m: say(0.12 * f, f"Reading the frames: {m}"))
    V, F, S, L = _read_stage_mesh(Path(src_dir) / "mesh.ply", with_looks=True)
    V2, _, rep = refine_from_images(V, F, left, right, T, K, B, params, cancelled=cancelled,
                                    progress=lambda f, m: say(0.12 + 0.8 * f, f"Refining: {m}"))
    return V2, F, S, L, rep


def write_spacetime_stage(out: Path, src_dir: Path, src: str, V2: np.ndarray, F: np.ndarray, S: np.ndarray | None,
                          L: np.ndarray | None, rep: dict) -> dict:
    """A "Refine from the images" stage folder ``out`` (which must exist): the refined mesh, the source stage's other
    files, and its ``report.json`` with ``"spacetime"`` (``rep`` without ``patch_results``, and ``source``). Returns
    the report."""
    save_refined_mesh(out / "mesh.ply", V2, F, S, L)  # each vertex keeps its sigma and its count of looks
    for name in ("trajectory.csv", "laser_points.ply", "marker_map.json"):
        if (src_dir / name).is_file():
            shutil.copy2(src_dir / name, out / name)
    report: dict = {}
    if (src_dir / "report.json").is_file():
        report = json.loads((src_dir / "report.json").read_text(encoding="utf-8"))
    report["spacetime"] = {**{k: v for k, v in rep.items() if k != "patch_results"}, "source": src}
    (out / "report.json").write_text(json.dumps(jsonable(report), indent=1), encoding="utf-8")
    return report


def remove_kept_depth(sp: Any) -> list[Path]:
    """Remove the depth Follow the camera kept for "Make only the thin walls finer" (``track/fused_depth/``, and in a
    pass's folder ``track.tmp-*/``) from the run's process folder and, for a run beside the result, from the scan's
    own: a run stopped on the way (a power cut) leaves it, and it is never used again. Returns what was removed."""
    from ohs.app.fine_walls import SPOOL_DIR

    gone: list[Path] = []
    for d in [sp.dir] + ([sp.live.dir] if getattr(sp, "live", None) is not None else []):
        for f in [d / "track" / SPOOL_DIR, *(d.glob(f"track.tmp-*/{SPOOL_DIR}") if d.is_dir() else [])]:
            if f.exists():
                _rmtree(f)
                gone.append(f)
    if gone:
        log.info("removed kept depth no longer used: %s", ", ".join(str(g) for g in gone))
    return gone


def free_disk_mb(path: Path) -> float:
    """The free space (MB of 2^20 bytes) on the drive of ``path`` (its nearest folder that exists)."""
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free / 2 ** 20


def _mb_words(mb: float) -> str:
    return f"{mb / 1024:.1f} GB" if mb >= 100 else f"{mb:.0f} MB"


#: "Make only the thin walls finer" did not act on this mesh: why, in a line for the page and the stage
_FINE_WHY = {
    "preview": "It does not run at Preview quality, so the mesh was made without it.",
    "no_graphics_card": "It needs a graphics card, so the mesh was made without it.",
    "no_kept_depth": "The frames it needs were not kept, so the mesh was left as it is.",
    "no_thin_walls": "No thin walls were found in the mesh, so it was left as it is.",
    "no_finer_surface": "The finer mesh detail held no surface near the thin walls, so the mesh was left as it is.",
    "failed": "It could not finish, so the mesh was left as it was made. Your recording is safe.",
}
FINE_WALLS_NOTE = "Make only the thin walls finer was on for this scan."


def fine_walls_line(info: dict) -> str:
    """The one line the page shows after a Make mesh with "Make only the thin walls finer" on (``made.fine_walls``):
    what it did, or why it did not."""
    if info.get("acted"):
        regions = info.get("regions")
        on = f" on {regions:,} wall{'s' if regions != 1 else ''}" if isinstance(regions, int) and regions > 0 else ""
        secs = info.get("seconds")
        took = f", in {float(secs):.0f} s" if isinstance(secs, (int, float)) else ""
        return (f"The thin walls were made finer: {100.0 * float(info.get('share') or 0.0):.1f} % of the surface{on}, at "
                f"mesh detail 0.5 mm{took}. The rest of the mesh is as Make mesh made it.")
    why = info.get("why")
    if why == "mesh_detail":
        v = info.get("voxel_mm")
        made = f" and this mesh is made at {_fmt(float(v))} mm" if isinstance(v, (int, float)) else ""
        return (f"{FINE_WALLS_NOTE} It was measured at mesh detail {_fmt(FINE_WALLS_VOXEL_MM)} mm only{made}, so the mesh "
                f"was made without it.")
    if why == "disk":
        return (f"{FINE_WALLS_NOTE} It needs about {_mb_words(float(info.get('need_mb') or 0.0))} of free disk space "
                f"while the mesh is made, and {_mb_words(FINE_WALLS_DISK_MARGIN_MB)} more to spare; the drive has "
                f"{_mb_words(float(info.get('free_mb') or 0.0))} free, so the mesh was made without it. Free some space, "
                f"then make the mesh again.")
    return f"{FINE_WALLS_NOTE} {_FINE_WHY.get(str(why), 'It did not change the mesh.')}"


def fine_walls_offer(report: dict, track_report: dict | None = None) -> dict | None:
    """Whether the thin-parts hint may offer "Make only the thin walls finer" for a mesh made with ``report`` (its
    ``report.json``): made with the mesh model on the graphics card (the depth is kept only there), at the mesh detail
    the step was measured at (:data:`FINE_WALLS_VOXEL_MM`), not at Preview quality. Then ``{frames, bytes_per_frame}``
    for the disk space it needs (the frames Follow the camera processed, from ``track_report`` when given); else
    None, and the hint offers the preset alone, as before."""
    from ohs.app.fine_walls import KEPT_BYTES_PER_PIXEL

    mesh = report.get("mesh") or {}
    if "on the GPU" not in str(mesh.get("reconstruction") or ""):
        return None
    v = mesh.get("voxel_mm")
    if not isinstance(v, (int, float)) or abs(float(v) - FINE_WALLS_VOXEL_MM) > 1e-6:
        return None
    if quality_or_none(report.get("quality")) == "preview":
        return None
    frames = ((track_report or {}).get("frames") or {}).get("processed") or (report.get("frames") or {}).get("processed")
    size = report.get("image_size")
    if not isinstance(frames, int) or frames <= 0 or not isinstance(size, list) or len(size) != 2:
        return None
    return {"frames": frames, "bytes_per_frame": KEPT_BYTES_PER_PIXEL * int(size[0]) * int(size[1])}


def _read_json(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _save_npz(path: Path, V: np.ndarray, F: np.ndarray, N: np.ndarray | None = None,
              S: np.ndarray | None = None, filled: np.ndarray | None = None, L: np.ndarray | None = None) -> None:
    """A cached step: V float32, F int32 and, when given, the vertex normals (float16), sigmas
    (float32, mm), the faces' filled flags (bool) and the vertices' counts of looks (``L``, float32:
    FEATURE_PLAN N1; a file from before has none, and reads as a step without looks)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.stem}.tmp-{uuid.uuid4().hex[:8]}.npz")
    arrays = {"V": np.asarray(V, dtype=np.float32), "F": np.asarray(F, dtype=np.int32)}
    if N is not None:
        arrays["N"] = np.asarray(N, dtype=np.float16)
    if S is not None:
        arrays["S"] = np.asarray(S, dtype=np.float32)
    if filled is not None:
        arrays["filled"] = np.asarray(filled, dtype=bool)
    if L is not None:
        arrays["L"] = np.asarray(L, dtype=np.float32)
    with open(tmp, "wb") as fh:
        np.savez(fh, **arrays)
    _retry(lambda: os.replace(tmp, path), f"replacing {path}")


def _load_npz(path: Path, normals: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None,
                                                          np.ndarray | None, np.ndarray | None]:
    """``(V, F, N, S, filled, L)`` of a cached step; ``N`` only when asked for; None when not stored."""
    with np.load(path) as z:
        N = z["N"].astype(np.float32) if normals and "N" in z.files else None
        S = z["S"].astype(np.float32) if "S" in z.files else None
        Fl = z["filled"].astype(bool) if "filled" in z.files else None
        L = z["L"].astype(np.float32) if "L" in z.files else None
        return z["V"].astype(np.float64), z["F"].astype(np.int64), N, S, Fl, L


def _load_npz_looks(path: Path) -> np.ndarray | None:
    """Only the counts of looks of a cached step (the other arrays of the file are not read)."""
    with np.load(path) as z:
        return z["L"].astype(np.float32) if "L" in z.files else None


def _load_result_mesh(path: Path, normals: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray | None,
                                                                  np.ndarray | None, np.ndarray | None,
                                                                  np.ndarray | None]:
    """``(V, F, N, S, filled, L)`` of ``result/mesh.ply`` as :func:`_load_npz` gives a cached step: the PLY
    stores the vertices, normals, sigmas and looks as float32 and the filled flags as uchar, so a step
    written by :meth:`ProcessManager._write_result` reads back bit for bit (no compaction, no face dropped)."""
    from ohs.track.export import load_ply

    d = load_ply(path)
    V = np.asarray(d["vertices"], dtype=np.float64)
    F = np.zeros((0, 3), dtype=np.int64) if d["faces"] is None else np.asarray(d["faces"], dtype=np.int64).reshape(-1, 3)
    N = np.asarray(d["normals"], dtype=np.float32) if normals and d.get("normals") is not None else None
    S = None if d.get("quality") is None else np.asarray(d["quality"], dtype=np.float32)
    Fl = None if d.get("filled") is None else np.asarray(d["filled"], dtype=bool)
    L = None if d.get("looks") is None else np.asarray(d["looks"], dtype=np.float32)
    return V, F, N, S, Fl, L


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


#: a Make mesh (or a recipe on everything) builds in ``<scan>/.remake-<job id>/`` beside the scan's result
REMAKE_PREFIX = ".remake-"
_SWAP_FILE = "swap.json"  # in the folder of a run whose result is whole: it is about to take the old one's place
_SWAPPED = ("process", "result")
SWAP_PATIENCE_S = 30.0  # a file held open (a download, Windows) is waited for this long before the swap gives up


def _rename(src: Path, dst: Path) -> None:
    """``src.rename(dst)``, retried while Windows says a file inside is held open (up to :data:`SWAP_PATIENCE_S`)."""
    deadline = time.monotonic() + SWAP_PATIENCE_S
    while True:
        try:
            src.rename(dst)
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


def _roll_back_swap(scan_dir: Path, staging: Path) -> None:
    """Undo a swap that did not finish (:func:`swap_in`): each folder of the new run that was moved in goes back into
    ``staging``, and each folder of the old result moved aside (``staging/old/``) comes back. Nothing of the old is
    removed on the way."""
    for name in _SWAPPED:
        here, new, was = scan_dir / name, staging / name, staging / "old" / name
        if not new.exists() and here.exists():  # the new one was moved in: it goes back
            _rename(here, new)
        if was.exists() and not here.exists():  # the old one was moved aside: it comes back
            _rename(was, here)


def swap_in(scan_dir: Path, staging: Path, fields: dict) -> None:
    """Put a finished run (``staging/process``, ``staging/result``) in the place of the scan's ``process/`` and
    ``result/``. Steps, each one a rename, so that at every moment either the old result or the new one is whole on
    disk: ``swap.json`` (with ``fields``, the scan entry's new values) is written; the old folders move to
    ``staging/old/``; the new ones move in. A failure on
    the way puts everything back (:func:`_roll_back_swap`) and raises; an app that stops on the way is put right at
    the next start (:func:`recover_remake`). The caller removes ``staging`` afterwards."""
    for name in _SWAPPED:
        if not (staging / name).is_dir():
            raise RuntimeError(f"the new run has no {name} folder")
    _write_json(staging / _SWAP_FILE, {"fields": fields})
    old = staging / "old"
    old.mkdir(exist_ok=True)
    try:
        for name in _SWAPPED:
            if (scan_dir / name).exists():
                _rename(scan_dir / name, old / name)
        for name in _SWAPPED:
            _rename(staging / name, scan_dir / name)
    except BaseException:
        safe_call("putting the earlier result back", lambda: _roll_back_swap(scan_dir, staging))
        if not _old_waiting(staging):  # all back: the swap is forgotten (else swap.json stays for the next start)
            safe_call("forgetting the swap", lambda: (staging / _SWAP_FILE).unlink(missing_ok=True))
        raise


def _old_waiting(staging: Path) -> bool:
    """True while a folder of the old result is in ``staging/old/`` and the new one has not wholly taken its place:
    the run's folder must then stay (it holds the scan's result) for :func:`recover_remake` to put back."""
    return (any((staging / "old" / name).exists() for name in _SWAPPED)
            and any((staging / name).exists() for name in _SWAPPED))


def recover_remake(scan_dir: Path, staging: Path) -> dict | None:
    """Put right a run's folder left by an app that stopped (call at start, before the scan is used): a run that had
    not finished (no ``swap.json``) is removed, and the scan's result was never touched; a swap that stopped half
    way is undone (the old result comes back whole); a swap whose new folders were all moved in is finished. Returns
    the scan entry's new values when the new result stays (to write into ``project.json``), else None."""
    swap = _read_json(staging / _SWAP_FILE)
    keep_new = swap is not None and not any((staging / name).exists() for name in _SWAPPED)
    if swap is not None and not keep_new:
        _roll_back_swap(scan_dir, staging)
        if any((staging / "old" / name).exists() for name in _SWAPPED):
            # the undo could not put an old folder back (its place is taken): the run's folder holds a part of
            # the scan's earlier result, so it is never removed; it stays for a person to look at
            log.error("scan %s: %s still holds a part of the earlier result and is left as it is", scan_dir, staging.name)
            return None
    _rmtree(staging)
    return dict(swap.get("fields") or {}) if keep_new and swap is not None else None


def _ply_bytes(V: np.ndarray, F: np.ndarray) -> bytes:
    """A binary little-endian PLY of a mesh's vertices (float32) and triangles (int32), nothing else."""
    V = np.ascontiguousarray(V, dtype="<f4").reshape(-1, 3)
    F = np.asarray(F, dtype="<i4").reshape(-1, 3)
    head = "\n".join(["ply", "format binary_little_endian 1.0", f"element vertex {len(V)}", "property float x",
                      "property float y", "property float z", f"element face {len(F)}",
                      "property list uchar int vertex_indices", "end_header", ""])
    faces = np.empty(len(F), dtype=[("n", "u1"), ("v", "<i4", (3,))])
    faces["n"], faces["v"] = 3, F
    return head.encode("ascii") + V.tobytes() + faces.tobytes()


def _read_stage_mesh(path: Path, with_looks: bool = False) -> tuple:
    """A stage's ``mesh.ply`` as ``(V, F, S)``: degenerate faces and unused vertices dropped; ``S``
    (each vertex's sigma, the PLY ``quality``) is None when the file has none. ``with_looks``: also
    ``L``, each vertex's count of looks (the PLY ``looks``; None the same way)."""
    from ohs.mesh.topology import compact
    from ohs.track.export import load_ply

    d = load_ply(path)
    V = np.asarray(d["vertices"], dtype=np.float64)
    F = np.zeros((0, 3), dtype=np.int64) if d["faces"] is None else np.asarray(d["faces"], dtype=np.int64).reshape(-1, 3)
    F = F[(F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])]
    V2, F2, idx = compact(V, F)
    S = d.get("quality")
    S = np.asarray(S, dtype=np.float32)[idx] if S is not None and len(S) == len(V) else None
    if not with_looks:
        return V2, F2, S
    L = d.get("looks")
    L = np.asarray(L, dtype=np.float32)[idx] if L is not None and len(L) == len(V) else None
    return V2, F2, S, L


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
            "stages": {"track": {**base, "quality": None, "tracked_pct": None, "voxel_mm": None, "warnings": []},
                       "straighten": {**base, "loops": None, "correction_mm": None, "correction_deg": None,
                                      "crossings": None, "tags": None},
                       "fuse": {**base, "voxel_mm": None},
                       "spacetime": {**base, "source": None, "frames": None, "patches": None, "kept": None,
                                     "covered_pct": None, "moved_mm": None, "device": None},
                       "refine": {**base, "source": None, "points": None, "outliers": None, "moved_pct": None,
                                  "moved_mm": None, "residual_before_mm": None, "residual_after_mm": None},
                       FINE_WALLS: {**base, "source": None, "why": None, "share_pct": None, "regions": None,
                                    "kept_mb": None}},
            "base": None, "edits": [], "cursor": 0, "version": 0, "made": None}


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
        # (version, offsets, answer) of the deviation map of the current step, and the lock that makes it once
        self._offset: tuple[int, np.ndarray, dict] | None = None
        self._offset_lock = threading.Lock()
        # the same for the laser agreement map (lasers.json / lasers.bin)
        self._lasers: tuple[int, np.ndarray, dict] | None = None
        self._lasers_lock = threading.Lock()
        # (version, margin, faces, finding) of the last "Remove the table" preview: the edit on the
        # same version and margin removes those faces without looking again
        self._table: tuple[int, float, np.ndarray, dict] | None = None
        # FEATURE_PLAN N1: (version, tolerance, answer without the faces' base64) of the last Check, the
        # areas the reasons route explains; (version, CheckMesh, looks) of the current step; (version,
        # tolerance, answer) of the last reasons; the lock that makes them once
        self._check: tuple[int, float, dict] | None = None
        self._why_mesh: tuple[int, Any, np.ndarray | None] | None = None
        self._why: tuple[int, float, dict] | None = None
        self._why_lock = threading.Lock()
        # UX_PLAN R2: a run building beside the result (ProcessManager._run_staged). On the run's own ScanProcess,
        # `live` is the scan it will replace and `deferred` what it will write outside its folder once it takes the
        # old one's place (the scan entry's fields, the calibration health); on the scan, `making` is that run
        self.live: ScanProcess | None = None
        self.making: ScanProcess | None = None
        self.deferred: dict[str, Any] = {"fields": {}, "health": None}
        self._t_stage: dict[str, float] = {}  # when each running stage started (time.monotonic), for its `seconds`
        self.plan: list[dict] | None = None  # a Make mesh's parts and their spans of the percentage (R18)
        # what "Make only the thin walls finer" did in this run (ENGINE_PLAN X5 step 2), for made.fine_walls; None: off
        self.fine_walls: dict | None = None
        self.t0: float | None = None  # when a run beside the result started (time.monotonic)

    def reset_caches(self) -> None:
        """Forget everything kept in memory about the mesh (its files were replaced)."""
        self._holes = self._fit_mesh = self._offset = self._lasers = self._table = None
        self._check = self._why_mesh = self._why = None

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
        for name in ALL_STAGES:
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
        """The cache file name of step ``k``: ``base``, or the edit's id. A base kept when the scan's
        space was freed (FEATURE_PLAN R2, :meth:`ProcessManager.keep_current_only`) keeps its step's file."""
        if k == 0:
            return str((self.state["base"] or {}).get("key") or "base")
        return str(self.state["edits"][k - 1]["id"])

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
        keep = {self.step_key(0)} | {self.step_key(k) for k in range(max(1, cur - CACHE_STEPS),
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


def _span(span: tuple[float, float], f: float) -> float:
    return span[0] + (span[1] - span[0]) * min(1.0, max(0.0, float(f)))


def make_mesh_plan(*, straighten: bool, refine: bool, edits: bool, spacetime: bool = False,
                   fine_walls: bool = False) -> dict[str, tuple[float, float]]:
    """The span of the percentage each part of a Make mesh (or a recipe on everything) takes: ``{part: (from, to)}``
    in order, from :data:`MAKE_MESH_SHARES` for the parts that run (UX_PLAN R18). ``spacetime``: "Refine from the
    images" between Straighten and Refine with lasers (``[process] refine_from_images``, off by default:
    :data:`SPACETIME_SHARE`). ``fine_walls``: "Make only the thin walls finer" right after Straighten (a choice per
    scan, ENGINE_PLAN X5 step 2: :data:`FINE_WALLS_SHARE`), only when it will run."""
    run = {"track": True, "straighten": straighten, FINE_WALLS: fine_walls, "spacetime": spacetime, "refine": refine,
           "edits": edits}
    table = {**MAKE_MESH_SHARES, "spacetime": SPACETIME_SHARE, FINE_WALLS: FINE_WALLS_SHARE}
    shares = {k: table[k] for k in ("track", "straighten", FINE_WALLS, "spacetime", "refine", "edits") if run[k]}
    total = sum(shares.values())
    out, a = {}, 0.0
    for k, v in shares.items():
        b = a + v / total
        out[k] = (a, b)
        a = b
    out[list(out)[-1]] = (out[list(out)[-1]][0], 1.0)
    return out


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
        prog(frac if frac is not None else 0.0, f"Straightening: {msg}" if msg else "Straightening the camera path")

    return cb


# --------------------------------------------------------------------------- the manager
class ProcessManager:
    """The processing studio of :class:`~ohs.server.studio.Studio` (see the module docstring)."""

    def __init__(self, studio: Any) -> None:
        self.st = studio
        self.recipes = RecipeStore(Path(studio.data_dir) / "recipes")
        self._scans: dict[tuple[str, str], ScanProcess] = {}
        self._lock = threading.Lock()
        # readings from the camera frames at a click (ENGINE_PLAN X16, the wall): one at a time, and the last
        # recording's matcher kept (ohs.measure.frame_depth.FrameDepth)
        self._wall_lock = threading.Lock()
        self._frame_depth: tuple[tuple, object] | None = None

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

    def peek(self, pid: str, sid: str) -> ScanProcess | None:
        """The scan's :class:`ScanProcess` when it is loaded; else one read from ``state.json`` and not kept
        (never taken over from an earlier result); None when the scan has no processing state. Writes
        nothing (FEATURE_PLAN R2: a plan changes nothing on disk)."""
        with self._lock:
            sp = self._scans.get((pid, sid))
        if sp is not None:
            return sp
        sd = self.st.projects.scan_dir(pid, sid)
        if not (sd / "process" / "state.json").is_file():
            return None
        sp = ScanProcess(pid, sid, sd)
        return sp if sp.load() else None

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
        V, F, S, L = _read_stage_mesh(res / "mesh.ply", with_looks=True)
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
                    summary=self._track_summary(report, quality), warnings=user_warnings(report))
        stats = _stats(V, F, S)
        st["base"] = {"triangles": int(len(F)), "vertices": int(len(V)), "stage": "track", "stats": stats}
        st["edits"], st["cursor"], st["version"] = [], 0, 1
        _save_npz(sp.cache_path(0), V, F, vertex_normals(V, F), S, L=L)
        sp.save()
        log.info("scan %s/%s: the earlier result is now the track stage of the processing studio", sp.pid, sp.sid)

    def forget(self, pid: str) -> None:
        """Drop the cached states of a project's scans (the project was moved to the trash)."""
        with self._lock:
            for key in [k for k in self._scans if k[0] == pid]:
                del self._scans[key]

    def forget_scan(self, pid: str, sid: str) -> None:
        """Drop the cached state of one scan (it was moved to the trash; a restore reads it again from disk)."""
        with self._lock:
            self._scans.pop((pid, sid), None)

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
            for name in ALL_STAGES:
                info = dict(st["stages"][name])
                if name == FINE_WALLS and info.get("state") == "none":  # only once it ran (a choice per scan)
                    continue
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
                   "current": current, "busy": None, "job": None, "making": None, "made": st.get("made")}
            mk = sp.making
        if mk is not None:  # UX_PLAN R2: the new run's stages; the scan's own stay as they are until it is done
            with mk.lock:
                out["making"] = {"stages": {name: {k: mk.state["stages"][name].get(k)
                                                   for k in ("state", "summary", "started", "seconds")}
                                            for name in ALL_STAGES
                                            if name != FINE_WALLS or mk.state["stages"][name].get("state") != "none"},
                                 "plan": copy.deepcopy(mk.plan)}
            if mk.t0 is not None:  # R18: how long the whole job has run, and what is left at this pace
                el = time.monotonic() - mk.t0
                p = float(job.progress) if job is not None else 0.0
                out["making"]["elapsed_s"] = round(el, 1)
                out["making"]["left_s"] = round(el * (1.0 - p) / p, 0) if p >= 0.03 else None
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
        if sp.live is not None:  # a run building beside the result: the scan's own state goes out, with the run's
            sp = sp.live
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
            raise ApiError(409, "This scan is still recording.", "Stop the scan first.")
        job = self.active_job(pid, sid)
        if job is not None:
            if make_mesh and job.kind == "process_scan":
                raise ApiError(409, "This scan's mesh is already being made.", "Wait for it to finish, or press Cancel.")
            sp = self._scans.get((pid, sid))
            busy = (self.view(sp)["busy"] if sp is not None else None) or "Making the mesh"
            raise ApiError(409, f"This scan is busy: {busy.rstrip('…').lower()}.", "Wait for it to finish, or press Cancel.")
        return entry

    def _need_recording(self, pid: str, sid: str) -> Path:
        rec = self.st.projects.recording_dir(pid, sid)
        if not (rec / "frames.csv").is_file():
            entry = self.st.projects.scan_entry(pid, sid)
            if entry.get("keep") == "standard":  # FEATURE_PLAN R2: removed on purpose, by the user
                raise ApiError(409, "This scan's recording was removed to free space (it keeps its result only), so its "
                                    "mesh cannot be made again from the frames.",
                               "The mesh can still be edited, measured and exported. To make it again, scan the part "
                               "again.")
            if entry.get("merged_from"):
                raise ApiError(409, "This scan has no recording of its own (it was merged from other scans).",
                               "It can be edited, measured and exported. To make it again, merge the scans it was made "
                               "from again.")
            raise ApiError(409, "This scan has no recording.", "Record the scan again.")
        return rec

    def _removed(self, pid: str, sid: str) -> ApiError:
        """The refusal of a step whose stage files were removed to free space (FEATURE_PLAN R2)."""
        entry = self.st.projects.scan_entry(pid, sid)
        rec = self.st.projects.recording_dir(pid, sid)
        if entry.get("keep") == "standard" or not (rec / "frames.csv").is_file():
            return ApiError(409, "The stage files this needs were removed to free space (this scan keeps its result only).",
                            "The mesh can still be edited, measured and exported. The stage files can only be made "
                            "again from a recording, which this scan no longer has.")
        return ApiError(409, "The stage files this needs were removed to free space (this scan keeps its recording and "
                             "its result).", "Press Make mesh again (or run “Follow the camera” first) to make them "
                                             "again from the recording.")

    def _freed(self, sp: ScanProcess) -> bool:
        """True when the scan's space was freed (FEATURE_PLAN R2): stage files may be missing on purpose."""
        return self.st.projects.scan_entry(sp.pid, sp.sid).get("keep") in ("engineering", "standard")

    def _need_track(self, sp: ScanProcess) -> None:
        done = sp.state["stages"]["track"]["state"] == "done"
        if done and not (sp.dir / "track" / "trajectory.csv").is_file() and self._freed(sp):
            raise self._removed(sp.pid, sp.sid)
        if not done or not (sp.dir / "track" / "trajectory.csv").is_file():
            raise ApiError(409, "This step needs “Follow the camera” to have finished.",
                           "Run “Follow the camera” first, or press Make mesh.")

    def _need_refine_source(self, sp: ScanProcess) -> None:
        """"Refine with lasers" reads only the base stage's mesh and laser points (no recording, no
        trajectory), so a merged scan can be refined too."""
        st = sp.state
        if st["stages"]["track"]["state"] != "done" or st["base"] is None:
            raise ApiError(409, "This step needs “Follow the camera” to have finished.",
                           "Run “Follow the camera” first, or press Make mesh.")
        src = st["base"].get("stage")
        if src == "refine":
            src = st["stages"]["refine"].get("source") or "track"
        if not (sp.dir / str(src) / "mesh.ply").is_file():
            if self._freed(sp):
                raise self._removed(sp.pid, sp.sid)
            log.warning("scan %s/%s: the %s stage's mesh is missing", sp.pid, sp.sid, src)
            raise ApiError(409, f"The mesh of the {str(src).capitalize()} stage is missing.",
                           "Run “Follow the camera” again, or press Make mesh.")

    @staticmethod
    def _need_base(sp: ScanProcess) -> None:
        if sp.state["base"] is None:
            raise ApiError(409, "This scan has no mesh yet.", "Press Make mesh first (or run “Follow the camera”).")

    # ------------------------------------------------------------------ mesh access
    def load_step(self, sp: ScanProcess, k: int) -> tuple[np.ndarray, np.ndarray]:
        """The mesh after ``k`` edits: from the cache, else replayed from the nearest cached step."""
        return self._load_step(sp, k)[:2]

    def _load_step(self, sp: ScanProcess, k: int, looks: bool = False) -> tuple:
        """:meth:`load_step` with each vertex's sigma and each face's filled flag (None without):
        ``(V, F, S, filled)``; with ``looks`` also each vertex's count of looks (``L``, None without:
        FEATURE_PLAN N1), which a replayed edit carries as it carries the sigma."""
        from ohs.mesh import carry_filled, carry_looks, carry_sigma

        with sp.lock:
            got = self._cached(sp, k)
            if got is not None:
                V, F, _, S, Fl, L = got
                return (V, F, S, Fl, L) if looks else (V, F, S, Fl)
            j = k - 1
            while j > 0 and not sp.cache_path(j).is_file():
                j -= 1
            got = self._cached(sp, j)
            if got is None:
                log.warning("scan %s/%s: no cached step before %d", sp.pid, sp.sid, k)
                raise ApiError(500, "The starting mesh of this scan is missing.",
                               "Run its last stage again (Follow the camera, Straighten or Build the surface), or "
                               "press Make mesh.")
            V, F, _, S, Fl, L = got
            edits = sp.state["edits"]
            log.info("scan %s/%s: rebuilding step %d from step %d (%d edits)", sp.pid, sp.sid, k, j, k - j)
            for i in range(j, k):
                e = edits[i]
                faces = np.load(sp.selection_path(str(e["id"]))) if e["op"] in _SELECTION_OPS else None
                V2, F2, by = self._apply(sp, e["op"], e["params"], V, F, edit_id=e["id"], faces=faces, S=S)
                bp = {"by_position": True} if e["params"].get("by_position") else {}  # as the edit recorded (F15)
                S = carry_sigma(_carry_op(e["op"]), V, F, S, V2, by, **bp)
                L = carry_looks(_carry_op(e["op"]), V, F, L, V2, by, **bp)
                Fl = carry_filled(_carry_op(e["op"]), V, F, Fl, V2, F2, by, **bp)
                V, F = V2, F2
                if i + 1 >= k - CACHE_STEPS:
                    _save_npz(sp.cache_path(i + 1), V, F, S=S, filled=Fl, L=L)
            return (V, F, S, Fl, L) if looks else (V, F, S, Fl)

    def mesh_before(self, pid: str, sid: str, edit_id: str, after: bool = False) -> tuple[bytes, int, int]:
        """``GET .../process/edits/{edit_id}/before.ply`` (UX_PLAN R23): the mesh just before that edit of the scan's
        history (the step the edit was applied to), for the page to show as a ghost beside the mesh after it: what a
        "Remove loose bits" (or any edit) took stays visible at any later time, for any step of the history, also an
        undone one. A binary PLY of the vertices and faces only. The step comes from the undo cache, or is rebuilt from
        the nearest cached step as an undo does. ``after`` (``.../after.ply``): the mesh that edit made instead, at any
        step of the history too. Returns ``(PLY bytes, the step's number, the current mesh version)``.
        404 for an edit the history no longer has (another Make mesh, a recipe, or Free space removed it)."""
        sp = self.scan(pid, sid)
        self._need_base(sp)
        with sp.lock:
            ids = [str(e["id"]) for e in sp.state["edits"]]
            if edit_id not in ids:
                raise ApiError(404, "This edit is no longer in the scan's history.",
                               "Reload the page to see the history as it is now.")
            k = ids.index(edit_id) + (1 if after else 0)
            V, F = self.load_step(sp, k)
            version = int(sp.state["version"])
        return _ply_bytes(V, F), k, version

    def step_looks(self, sp: ScanProcess, k: int) -> np.ndarray | None:
        """Each vertex's count of looks at step ``k`` (None without): read alone from its cache file when
        there is one, else with the step."""
        with sp.lock:
            p = sp.cache_path(k)
            if p.is_file():
                return _load_npz_looks(p)
            return self._load_step(sp, k, looks=True)[4]

    def current_mesh(self, sp: ScanProcess) -> tuple[np.ndarray, np.ndarray]:
        self._need_base(sp)
        return self.load_step(sp, sp.state["cursor"])

    def mesh_arrays(self, pid: str, sid: str) -> tuple[int, np.ndarray, np.ndarray, np.ndarray | None,
                                                       np.ndarray | None]:
        """The current mesh as ``(version, V, F, sigma, filled)``: the arrays the fit route keeps
        per mesh version (loaded once, shared with the fits and the check; read them, do not change
        them). 409 before there is a mesh."""
        sp = self.scan(pid, sid)
        self._need_base(sp)
        with sp.lock:
            v = int(sp.state["version"])
            cached = sp._fit_mesh
            if cached is None or cached[0] != v:
                V, F, S, Fl = self._load_step(sp, int(sp.state["cursor"]))
                cached = sp._fit_mesh = (v, V, F, S, Fl)
        return cached

    def _cached(self, sp: ScanProcess, k: int, normals: bool = False) -> tuple | None:
        """``(V, F, N, S, filled, L)`` of step ``k`` from its cache file; for a base kept in ``result/``
        (FEATURE_PLAN R2: ``base.in_result``, no cache file) from ``result/mesh.ply``, which holds it bit
        for bit; None when neither is there."""
        p = sp.cache_path(k)
        if p.is_file():
            return _load_npz(p, normals=normals)
        res = sp.result_dir / "mesh.ply"
        if k == 0 and (sp.state["base"] or {}).get("in_result") and res.is_file():
            return _load_result_mesh(res, normals=normals)
        return None

    def kept_step(self, sp: ScanProcess) -> tuple[Path | None, bool] | None:
        """R2, read-only: how the current step stays once the undo history goes. ``(None, True)``:
        ``result/mesh.ply`` holds it (vertices, faces, sigmas and filled flags equal to its cache file's,
        or no cache file: the result is always written from the current step), so no cache file needs
        to stay; ``(cache file, False)``: the result differs (a scan taken over from before the studio
        has a compacted base), so that file stays. None: no mesh, or neither file is there. Writes
        nothing. Call under the scan lock."""
        if sp.state["base"] is None:
            return None
        k = int(sp.state["cursor"])
        res = sp.result_dir / "mesh.ply"
        p = sp.cache_path(k)
        if not p.is_file():
            if k == 0 and (sp.state["base"] or {}).get("in_result") and res.is_file():
                return None, True
            return (None, True) if res.is_file() else None
        if not res.is_file():
            return p, False
        a, b = _load_npz(p), _load_result_mesh(res)
        same = all((x is None and y is None) or (x is not None and y is not None and x.shape == y.shape and
                                                   np.array_equal(x, y)) for i, (x, y) in enumerate(zip(a, b)) if i != 2)
        return (None, True) if same else (p, False)

    def keep_current_only(self, sp: ScanProcess, in_result: bool) -> None:
        """FEATURE_PLAN R2: the current step becomes the base, so that the undo history (the cache files,
        the selections) can be removed and edits still work. The mesh, its version, its sigmas and its
        filled flags do not change (kept features stay current); the edits that made it are kept by
        name in ``base.applied`` (for "save as recipe"); undo stops here. ``in_result``: the base is read
        from ``result/mesh.ply`` (no cache file stays; the first new edit writes the base's cache file
        before the result changes), else from the current step's cache file. Writes ``state.json``
        only, before anything is removed, so the scan is whole at every moment. Call under the scan lock."""
        new = self.collapsed_state(sp, in_result)
        if new is not None:
            sp.state.clear()
            sp.state.update(new)
            sp._offset = None  # the deviation map starts again from this mesh
            sp.save()

    @staticmethod
    def collapsed_state(sp: ScanProcess, in_result: bool) -> dict | None:
        """The ``state.json`` :meth:`keep_current_only` writes (a new dict), or None when it would change
        nothing. Call under the scan lock."""
        st = sp.state
        if st["base"] is None:
            return None
        k = int(st["cursor"])
        base = st["base"]
        if k == 0 and not st["edits"] and (not in_result or base.get("in_result")):
            return None  # the base already is the current step, and stays where it is
        stats = (sp.stats_at(k) or base.get("stats") or {}) if k else base.get("stats") or {}
        applied = list(base.get("applied") or []) + [{"op": e["op"], "params": e["params"], "label": e.get("label")}
                                                     for e in st["edits"][:k]]
        out = copy.deepcopy(st)
        nb = {"triangles": int(stats.get("triangles", base["triangles"] if k == 0 else 0)),
              "vertices": int(stats.get("vertices", base["vertices"] if k == 0 else 0)),
              "stage": base["stage"], "stats": copy.deepcopy(stats), "applied": applied}
        if in_result:
            nb["in_result"] = True
        else:
            nb["key"] = sp.step_key(k)
        out["base"] = nb
        out["edits"], out["cursor"] = [], 0
        return out

    def _formats(self) -> tuple[str, ...]:
        fmts = ["ply", "stl"]
        if "obj" in [str(e).lower() for e in getattr(self.st.cfg.process, "export", [])]:
            fmts.append("obj")
        return tuple(fmts)

    def _write_result(self, sp: ScanProcess, V: np.ndarray, F: np.ndarray, N: np.ndarray | None = None,
                      S: np.ndarray | None = None, filled: np.ndarray | None = None, L: np.ndarray | None = None) -> None:
        """The result files of a step; the PLY carries each vertex's sigma (``quality``), its count of
        looks (``looks``) and each face's filled flag (``filled``) when known."""
        from ohs.mesh import vertex_normals
        from ohs.track.export import save_mesh, write_stl

        sp.result_dir.mkdir(parents=True, exist_ok=True)
        if N is None:
            N = vertex_normals(V, F)
        if S is not None and len(S) != len(V):
            S = None
        if L is not None and len(L) != len(V):
            L = None
        if filled is not None and len(filled) != len(F):
            filled = None
        for ext in self._formats():
            tmp = sp.result_dir / f".mesh-{uuid.uuid4().hex[:8]}.{ext}"
            if ext == "stl":
                write_stl(tmp, V, F)
            else:
                save_mesh(tmp, V, F, N, quality=S if ext == "ply" else None, filled=filled if ext == "ply" else None,
                          looks=L if ext == "ply" else None)
            dst = sp.result_dir / f"mesh.{ext}"
            _retry(lambda t=tmp, d=dst: os.replace(t, d), f"replacing {dst}")
        stale = sp.result_dir / "mesh.obj"
        if "obj" not in self._formats() and stale.exists():
            safe_call("removing an old mesh.obj", stale.unlink)

    def _show_step(self, sp: ScanProcess, k: int, V: np.ndarray | None = None, F: np.ndarray | None = None,
                   N: np.ndarray | None = None, S: np.ndarray | None = None, filled: np.ndarray | None = None,
                   L: np.ndarray | None = None) -> None:
        """Make step ``k`` the current mesh: result files, version + 1, save (under the scan lock).

        Undo / redo pass no mesh: the cached step (with its normals, sigmas, looks and flags) is loaded."""
        base = sp.state["base"] or {}
        if base.get("in_result") and k != 0 and not sp.cache_path(0).is_file() and int(sp.state["cursor"]) == 0:
            # R2: the base lives in result/mesh.ply only; it gets its cache file before the result changes
            got = self._cached(sp, 0, normals=True)
            if got is not None:
                _save_npz(sp.cache_path(0), *got)
        if V is None:
            got = self._cached(sp, k, normals=True)
            if got is not None:
                V, F, N, S, filled, L = got
            else:
                V, F, S, filled, L = self._load_step(sp, k, looks=True)
        sp.state["cursor"] = k
        self._write_result(sp, V, F, N, S, filled, L)
        sp.state["version"] = int(sp.state["version"]) + 1
        sp.prune_cache()
        sp.save()

    # ------------------------------------------------------------------ edits
    def _apply(self, sp: ScanProcess, op: str, params: dict, V: np.ndarray, F: np.ndarray, *,
               edit_id: str | None = None, faces: np.ndarray | None = None, S: np.ndarray | None = None,
               progress: Callable[[float, str], None] | None = None) -> tuple[np.ndarray, np.ndarray,
                                                                                    np.ndarray | None]:
        """Run one mesh operation on ``(V, F)`` (``S``: the vertices' sigmas, for ``within_sigma``).
        Returns ``(V2, F2, faces)``: ``faces`` are the faces the edit went by (the selection of
        ``delete_faces`` / ``keep_faces``, the faces ``remove_floaters`` kept, else None), which is what
        :func:`ohs.mesh.carry_sigma` and :func:`ohs.mesh.carry_filled` follow the mesh by."""
        from ohs import mesh as M

        if op == "remove_table":
            # the faces it removes: from the saved selection when replayed (the same faces, bit for
            # bit, whatever the engine finds today), else found now
            if faces is None and edit_id is not None and sp.selection_path(str(edit_id)).is_file():
                faces = np.load(sp.selection_path(str(edit_id)))
            if faces is None:
                faces = self._find_table(V, F, S, params)[0]
            if len(faces) and int(faces.max()) >= len(F):
                raise ApiError(409, "The table this edit removed is not on the current mesh.",
                               "Undo to the step it was removed from, or remove it again.")
            return (*M.delete_faces(V, F, faces), faces)
        if op in ("delete_faces", "keep_faces"):
            if faces is None:
                faces = np.load(sp.selection_path(str(edit_id)))
            if len(faces) and int(faces.max()) >= len(F):
                raise ApiError(409, "The selection is not on the current mesh.", "Reload the mesh and select again.")
            return (*(M.delete_faces if op == "delete_faces" else M.keep_faces)(V, F, faces), faces)
        # by position only as the edit recorded it (F15); off, the call is exactly the one before F15
        bp = {"by_position": True} if params.get("by_position") else {}
        if op == "remove_floaters":
            return M.remove_floaters(V, F, **{k: v for k, v in params.items() if k != "by_position"}, return_kept=True,
                                     **bp)
        if op == "fill_holes":
            return (*M.fill_holes(V, F, params["max_hole_mm"], include_borders=bool(params.get("watertight")),
                                  progress=progress, **bp), None)
        if op == "smooth":
            # within 2 sigma: each vertex moves at most twice its own standard deviation (no sigmas: no limit)
            limit = 2.0 * S if params.get("within_sigma") and S is not None and len(S) == len(V) else None
            return (*M.smooth(V, F, params["method"], params["strength"], params.get("keep_edges", True), limit=limit,
                              flat_only=bool(params.get("flat_only", False)),
                              away_from_edges=bool(params.get("away_from_edges", False)), progress=progress, **bp), None)
        if op == "decimate":
            return (*M.decimate(V, F, params.get("target_triangles"), params.get("ratio"),
                                max_error_mm=params.get("max_error_mm"), progress=progress, **bp), None)
        log.warning("unknown edit %r", op)
        raise ApiError(422, "The app does not know this mesh edit.", _RELOAD)

    @staticmethod
    def _summary(op: str, params: dict, V: np.ndarray, F: np.ndarray, V2: np.ndarray, F2: np.ndarray,
                 before: dict | None, after: dict, kept: np.ndarray | None = None) -> str:
        """What an edit did, in plain words (from the stats before and after; ``kept``: the faces
        ``remove_floaters`` kept, to say how large the largest removed piece was, U14)."""
        b = before or {}
        if op == "remove_floaters":
            if len(F2) == len(F):
                return "No loose bits matched: nothing was removed"
            gone = removed_pieces(V, F, kept, params)
            if gone is not None:
                n, size = gone["pieces"], f"{gone['largest_mm']:.1f} mm across"
                if gone["largest_rule"] == "largest_only":
                    return (f"Kept the largest piece only: removed {n:,} other piece{'s' if n != 1 else ''}, "
                            f"{'the largest ' if n != 1 else ''}{size} ({len(F) - len(F2):,} triangles)")
                rule = _rule_words(gone["largest_rule"], params)  # R23: what took the largest one
                return (f"Removed {n:,} loose bit{'s' if n != 1 else ''}, {'the largest ' if n != 1 else ''}{size}{rule} "
                        f"({len(F) - len(F2):,} triangles)")
            n = int(b.get("components", 0)) - int(after["components"])
            return f"Removed {n:,} loose bit{'s' if n != 1 else ''} ({len(F) - len(F2):,} triangles)"
        if op == "fill_holes":
            left = int(after["holes"]) + (after.get("border_mm") is not None)
            if params.get("watertight"):
                if len(F2) == len(F):
                    return (f"No opening up to {holes_mm(params['max_hole_mm'])}: nothing was closed"
                            if left else "Already watertight: nothing to close")
                return (f"Closed every opening ({len(F2) - len(F):,} new triangles): the mesh is watertight"
                        if after.get("watertight") else
                        f"Closed openings with {len(F2) - len(F):,} new triangles; not watertight yet: {left:,} "
                        f"opening{'s are' if left != 1 else ' is'} larger than {holes_mm(params['max_hole_mm'])}")
            if len(F2) == len(F):
                return f"No holes up to {holes_mm(params['max_hole_mm'])}: nothing was filled"
            n = int(b.get("holes", 0)) - int(after["holes"])
            return f"Filled {n:,} hole{'s' if n != 1 else ''} ({len(F2) - len(F):,} new triangles)"
        if op == "smooth" and len(V2) == len(V) and len(V):
            d = np.linalg.norm(V2 - V, axis=1)
            within = ", each point within its scatter of where it was measured" if params.get("within_sigma") else ""
            return f"Moved the surface by {float(np.mean(d)):.3f} mm on average and {float(np.max(d)):.2f} mm at most{within}"
        if op == "decimate" and len(F2) < len(F):
            from ohs.mesh import deviation

            dev = deviation(V, V2, F2)
            return (f"{len(F):,} -> {len(F2):,} triangles; the surface moved at most {float(dev.max()):.3f} mm "
                    f"({float(np.percentile(dev, 99.9)):.3f} mm for 99.9 % of it)")
        return f"{len(F):,} -> {len(F2):,} triangles"

    def _commit_edit(self, sp: ScanProcess, op: str, params: dict, label: str, V: np.ndarray, F: np.ndarray,
                     ms: float, summary: str, job_id: str | None, faces: np.ndarray | None = None,
                     stats: dict | None = None, S: np.ndarray | None = None, filled: np.ndarray | None = None,
                     L: np.ndarray | None = None, extra: dict | None = None) -> dict:
        """Append an edit after the cursor (the redo steps are dropped) and show it. ``extra``: more fields of the
        entry (``removed`` of Remove loose bits, U14)."""
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
                     "stats": stats, **(extra or {})}
            if faces is not None:
                sp.selection_path(eid).parent.mkdir(parents=True, exist_ok=True)
                np.save(sp.selection_path(eid), faces.astype(np.int64))
            sp.state["edits"].append(entry)
            _save_npz(sp.cache_path(k + 1), V, F, N, S, filled, L)
            self._show_step(sp, k + 1, V, F, N, S, filled, L)
            return {key: v for key, v in entry.items() if key != "stats"}

    @staticmethod
    def _find_table(V: np.ndarray, F: np.ndarray, S: np.ndarray | None, params: dict) -> tuple[np.ndarray, dict]:
        """:func:`ohs.mesh.support.find_support` on a step's mesh with an edit's ``params`` (margin, the
        areas kept): ``(the faces it removes, what it found)``, the finding with each raised area's faces
        beside it (``_raised_faces``, for the preview); its refusal is a 422 in its own words, and nothing
        changes."""
        from ohs.mesh import support

        bp = {"by_position": True} if params.get("by_position") else {}
        try:
            r = support.find_support(V, F, sigma=S if S is not None and len(S) == len(V) else None,
                                     params=support.SupportParams(margin_mm=float(params.get("margin_mm", 1.0))),
                                     keep=params.get("keep") or None, **bp)
        except support.SupportRefused as e:
            raise ApiError(422, plain_reason(e, "No table was found under the part."), TABLE_HINT) from None
        return r.faces.astype(np.int64), {**r.to_dict(), "_raised_faces": [x["faces"] for x in r.raised]}

    @staticmethod
    def _table_key(params: dict) -> tuple:
        """What a "Remove the table" answer depends on besides the mesh: the margin, the areas kept, and
        whether ties are broken by position (F15)."""
        return (float(params["margin_mm"]), tuple(tuple(q) for q in params.get("keep") or ()),
                bool(params.get("by_position")))

    def _edit_params(self, op: str, params: dict) -> dict:
        """An edit's params as it runs and is recorded: with ``by_position: true`` when ``[process]
        repeatable_edits`` is on and the edit breaks ties (F15), else exactly as given."""
        if op in _BY_POSITION_OPS and bool(getattr(self.st.cfg.process, "repeatable_edits", False)):
            return {**params, "by_position": True}
        return params

    @staticmethod
    def _table_summary(info: dict) -> str:
        """What "Remove the table" did, in plain words (from the engine's finding)."""
        low = info.get("lowest_mm")
        kept = ("" if low is None else "; the part is kept right down to it" if low <= info["band_mm"]
                else f"; the part is kept from {low:.2f} mm above it")
        out = (f"Removed the table: {info['n_faces']:,} triangles ({info['area_mm2']:,.0f} mm², "
               f"{100 * info['share']:.0f} % of the scan, flat to {info['flatness_mm']:.2f} mm){kept}")
        return " ".join([out + "."] + list(info.get("warnings") or []))

    def _run_edit(self, ctx: Any, sp: ScanProcess, op: str, params: dict, label: str, prog: _Prog,
                  faces: np.ndarray | None = None) -> dict:
        from ohs.mesh import carry_filled, carry_looks, carry_sigma

        params = self._edit_params(op, params)  # recorded with the edit: a replay goes by it (F15)
        with sp.lock:
            k = sp.state["cursor"]
            version = int(sp.state["version"])
            before = sp.stats_at(k)
            table = sp._table
        prog(0.02, f"{label}: loading the mesh", force=True)
        V, F, S, Fl, L = self._load_step(sp, k, looks=True)
        t0 = time.perf_counter()
        info = None
        if op == "remove_table":  # the faces of the preview on this very mesh, else found now
            if table is not None and table[0] == version and table[1] == self._table_key(params):
                faces, info = table[2], table[3]
            else:
                prog(0.05, f"{label}: finding the table", force=True)
                faces, info = self._find_table(V, F, S, params)
            n_keep = sum(1 for r in info["raised"] if r.get("by_user"))
            label = f"Remove the table ({len(faces):,} faces" + (f", {n_keep} area{'s' if n_keep != 1 else ''} kept)" if n_keep else ")")
        V2, F2, by = self._apply(sp, op, params, V, F, faces=faces, S=S,
                                 progress=lambda f, m=None: prog(0.05 + 0.8 * f, f"{label}: {m}" if m else label))
        ms = 1e3 * (time.perf_counter() - t0)
        if len(F2) == 0:
            raise ApiError(409, "This would remove the whole mesh, so nothing was changed.",
                           "Change the settings or the selection.")
        ctx.check()
        prog(0.88, f"{label}: measuring the result", force=True)
        bp = {"by_position": True} if params.get("by_position") else {}
        S2 = carry_sigma(_carry_op(op), V, F, S, V2, by, **bp)
        L2 = carry_looks(_carry_op(op), V, F, L, V2, by, **bp)  # each vertex's count of looks, as its sigma
        Fl2 = carry_filled(_carry_op(op), V, F, Fl, V2, F2, by, **bp)
        stats = _stats(V2, F2, S2, Fl2)
        summary = (self._table_summary(info) if info is not None else
                   self._summary(op, params, V, F, V2, F2, before, stats, kept=by if op == "remove_floaters" else None))
        removed = removed_pieces(V, F, by, params) if op == "remove_floaters" else None  # U14: what it took, for the page
        prog(0.94, f"{label}: saving", force=True)
        return self._commit_edit(sp, op, params, label, V2, F2, ms, summary, ctx.job.id, faces, stats, S2, Fl2, L2,
                                 extra={"removed": removed} if removed is not None else None)

    def table_preview(self, pid: str, sid: str, margin_mm: float | None = None, version: int | None = None,
                      keep: list | None = None) -> dict:
        """``POST .../process/remove-table/preview``: the faces "Remove the table" would remove from
        the current mesh, and what the engine found, without changing anything. Read-only; uses the
        arrays the fits keep per mesh version, and keeps the answer for the edit that follows.
        ``version``: the mesh version the page shows (409 when it changed). The engine's refusal
        is a 422 in its own words. ``keep``: points on raised areas the user keeps ("Keep this"), as the
        edit takes them. Each raised area that is drawn (kept, or warned about) comes with its faces."""
        body: dict[str, Any] = {} if margin_mm is None else {"margin_mm": margin_mm}
        if keep is not None:
            body["keep"] = keep
        params, _ = normalize_op("remove_table", body)
        params = self._edit_params("remove_table", params)  # as the edit that follows will run (F15)
        m = self._table_key(params)
        sp = self.scan(pid, sid)
        self._need_base(sp)
        with sp.lock:
            v = int(sp.state["version"])
            if version is not None and int(version) != v:
                raise ApiError(409, "The mesh changed since the page loaded it.", "Reload the mesh and try again.")
            cached = sp._fit_mesh
            if cached is None or cached[0] != v:
                V, F, S, Fl = self._load_step(sp, int(sp.state["cursor"]))
                cached = sp._fit_mesh = (v, V, F, S, Fl)
            table = sp._table
        if table is not None and table[0] == v and table[1] == m:
            faces, info = table[2], table[3]
        else:
            _, V, F, S, _ = cached
            faces, info = self._find_table(V, F, S, params)
            with sp.lock:
                if int(sp.state["version"]) == v:  # not changed meanwhile
                    sp._table = (v, m, faces, info)
        def b64(x: np.ndarray) -> str:
            return base64.b64encode(np.asarray(x, dtype="<u4").tobytes()).decode("ascii")

        raised = [{**r, "faces": b64(f) if r["verdict"] in ("kept", "warned") else None}
                  for r, f in zip(info["raised"], info["_raised_faces"])]
        out = {k: x for k, x in info.items() if k != "_raised_faces"}
        return {**out, "raised": raised, "version": v, "triangles": int(len(cached[2])), "faces": b64(faces)}

    def submit_edit(self, pid: str, sid: str, op: str, params: dict | None, version: int | None = None) -> Any:
        if op not in MESH_OPS:
            log.warning("unknown edit %r", op)
            raise ApiError(422, "The app does not know this mesh edit.", _RELOAD)
        faces = None
        if op in ("delete_faces", "keep_faces"):
            p = dict(params or {})
            extra = sorted(set(p) - {"faces"})
            if extra:
                log.warning("%s: unknown parameter %r", op, extra[0])
                raise ApiError(422, f"{_OP_WORDS[op]}: the page sent a setting this edit does not have.", _RELOAD)
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
                    raise ApiError(409, "The mesh changed since you selected.", "Select again on the current mesh.")
                n_cur = (sp.stats_at(sp.state["cursor"]) or {}).get("triangles")
            if faces is not None and n_cur is not None and len(faces) and int(faces.max()) >= int(n_cur):
                raise ApiError(422, f"The selection has triangle {int(faces.max()):,}, but the mesh has {int(n_cur):,}.",
                               "Reload the mesh and select again.")

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
            self.st.notify("warn", failure_notice(job, f"{label} failed.", "The mesh is unchanged."))

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
                if (sp.state["base"] or {}).get("applied"):  # FEATURE_PLAN R2: the history was removed on purpose
                    raise ApiError(409, "There is nothing to undo: the earlier steps were removed to free space.",
                                   "Undo stops at the mesh as it was when the space was freed; new edits can be undone.")
                raise ApiError(409, "There is nothing to undo.", "The mesh is the stage's result already.")
            if k > len(sp.state["edits"]):
                raise ApiError(409, "There is nothing to redo.", "Redo only works after an undo.")
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
            raise ApiError(409, "The recording has no frames.", "Record the scan again.")
        size = reader.meta.get("image_size") or list(reader.frame(0).image_size)
        return sp.recording_dir, mode, int(size[0])

    def _stage_params(self, mode: str, quality: str, voxel_mm: float | None, width: int,
                      thin_walls: str | None = None) -> Any:
        return stage_params(self.st.cfg, mode, quality, width, voxel_mm, thin_walls)

    # ------------------------------------------------------------------ presets (FEATURE_PLAN P2)
    def _preset_acting(self, sp: ScanProcess) -> dict:
        """The values the scan's preset sets (with the hand changes); {} for General, a scan made before
        presets and one whose preset is gone: then every stage runs as it did before presets."""
        presets = getattr(self.st, "presets", None)
        return {} if presets is None else presets.acting(sp.pid, sp.sid)

    def _preset_report(self, sp: ScanProcess, quality: str, report: dict, params: Any) -> dict | None:
        """The scan's preset for a stage's ``report.json``, with the values this mesh was made with."""
        from ohs.app.presets import GENERAL_TOLERANCE_MM

        presets = getattr(self.st, "presets", None)
        if presets is None:
            return None
        act = presets.acting(sp.pid, sp.sid)
        used = {"mode": params.pipeline.mode, "quality": quality,
                "voxel_mm": (report.get("mesh") or {}).get("voxel_mm"), "thin_walls": params.pipeline.thin_walls,
                "recipe": act.get("recipe", "default"), "check_tolerance_mm": act.get("check_tolerance_mm", GENERAL_TOLERANCE_MM)}
        return presets.report_block(sp.pid, sp.sid, used)

    def _preset_recipe(self, pid: str, sid: str) -> dict:
        """The recipe Make mesh runs: the preset's when it names one (and it still exists), else Default."""
        presets = getattr(self.st, "presets", None)
        rid = (presets.acting(pid, sid) if presets is not None else {}).get("recipe")
        if rid and rid != "default":
            try:
                return self.recipes.get(rid)
            except ApiError:
                self.st.notify("warn", "The preset's recipe is no longer in the app, so Make mesh uses the Default "
                                       "recipe.")
        return self.recipes.get("default")

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
        """The stage runs from now: ``running``, with ``started`` (UX_PLAN R18: the page says how long it has run)."""
        with sp.lock:
            before = dict(sp.state["stages"][name])
            sp.state["stages"][name].update(state="running", job_id=job_id, summary=None, started=now_iso(),
                                            seconds=None)
            sp._t_stage[name] = time.monotonic()
            sp.save()
        self.publish(sp)
        return before

    def _stage_end(self, sp: ScanProcess, name: str, **fields: Any) -> None:
        """The stage ended (done, skipped or failed): ``when``, and ``seconds``, how long it ran (R17, R18)."""
        with sp.lock:
            t0 = sp._t_stage.pop(name, None)
            secs = round(time.monotonic() - t0, 1) if t0 is not None else None
            sp.state["stages"][name].update(when=now_iso(), seconds=secs, **fields)
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

        V, F, S, L = _read_stage_mesh(stage_dir / "mesh.ply", with_looks=True)
        stats = _stats(V, F, S)
        N = vertex_normals(V, F)
        with sp.lock:
            sp.drop_edits_after(0)
            for f in (sp.dir / "cache").glob("*.npz") if (sp.dir / "cache").is_dir() else []:
                safe_call(f"removing {f}", f.unlink)
            sp.state["base"] = {"triangles": int(len(F)), "vertices": int(len(V)), "stage": stage, "stats": stats}
            if sp.live is None:  # a stage on the scan itself: the time of the Make mesh that made the old base is no more
                sp.state["made"] = None
            sp.state["edits"], sp.state["cursor"] = [], 0
            _save_npz(sp.cache_path(0), V, F, N, S, L=L)
            sp.result_dir.mkdir(parents=True, exist_ok=True)
            for name in ("report.json", "trajectory.csv", "laser_points.ply", "marker_map.json"):
                src = stage_dir / name
                if src.is_file():
                    tmp = sp.result_dir / f".{name}.tmp-{uuid.uuid4().hex[:8]}"
                    shutil.copy2(src, tmp)
                    _retry(lambda t=tmp, d=sp.result_dir / name: os.replace(t, d), f"replacing {name}")
            self._show_step(sp, 0, V, F, N, S, L=L)
        summary = report_summary(report)
        if quality:
            summary["quality"] = quality
        fields: dict[str, Any] = {"status": "processed", "report": summary, "error": None}
        if quality:
            fields["quality"] = quality
        if sp.live is not None:  # a run beside the result (R2): the scan entry changes when it takes the old one's place
            sp.deferred["fields"].update(fields)
            return
        safe_call("updating the scan", lambda: self.st.projects.update_scan(sp.pid, sp.sid, **fields))

    def _fuse_into(self, ctx: Any, sp: ScanProcess, name: str, prog: _Prog, *, quality: str, voxel_mm: float | None,
                   poses: dict[int, np.ndarray] | None, keep_depth: bool = False) -> dict:
        """``process_recording`` into ``process/<name>/`` (tracked, or at ``poses``); returns the report. ``keep_depth``:
        the pass keeps each fused frame's depth in ``<name>/fused_depth/`` (``ProcessParams.fine_walls``, for "Make
        only the thin walls finer"; Follow the camera in a Make mesh that runs it, never another fuse)."""
        from ohs.app.process import process_recording

        rec, mode, width = self._scan_params(sp)
        params = self._stage_params(mode, quality, voxel_mm, width,
                                    thin_walls=self._preset_acting(sp).get("thin_walls"))  # P2: None for General
        if keep_depth:
            params = dataclasses.replace(params, fine_walls=True)
        tmp = sp.dir / f"{name}.tmp-{ctx.job.id}"
        _rmtree(tmp)
        # no device name in what the user reads (UX_PLAN R12): Settings' About names the graphics card
        verb = "Following the camera" if poses is None else "Building the surface"
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
                prog(1.0, f"{verb}: all {n:,} frames done, making the mesh", force=True)
                return
            lost = "" if res.track.ok else " (tracking lost)"
            prog((k + 1) / max(n, 1), f"{verb}: frame {k + 1:,} of {n:,}{lost}")

        try:
            report = process_recording(rec, tmp, params=params, progress=progress, poses=poses)
            ctx.check()
            report["quality"] = quality
            preset = self._preset_report(sp, quality, report, params)  # P2: the preset and what it resolved to
            if preset is not None:
                report["preset"] = preset
            if name != "track":  # a later stage: what Straighten corrected so far (calibration health)
                st_rep = _read_json(sp.dir / "straighten.json")
                if st_rep is not None:
                    _set_straighten(report, st_rep)
                track_rep = _read_json(sp.dir / "track" / "report.json")
                _carry_path_check(report, track_rep)
                _carry_health_path(report, track_rep)
            (tmp / "report.json").write_text(json.dumps(jsonable(report), indent=1), encoding="utf-8")
            _move_dir(tmp, sp.dir / name)
        finally:
            _rmtree(tmp)
            ctx.set_frames(None)  # the pass is over
        return report

    def _do_track(self, ctx: Any, sp: ScanProcess, prog: _Prog, quality: str, voxel_mm: float | None = None,
                  keep_depth: bool = False) -> None:
        if voxel_mm is None:  # P2: the scan's preset's voxel when it names one (None for General: as before)
            voxel_mm = self._preset_acting(sp).get("voxel_mm")
        # depth kept by a run that was stopped on the way (a power cut) goes now: it is never used again
        remove_kept_depth(sp)
        before = self._stage_start(sp, "track", ctx.job.id)
        try:
            prog(0.0, "Loading the recording", force=True)
            report = self._fuse_into(ctx, sp, "track", prog.sub(0.0, 0.95), quality=quality, voxel_mm=voxel_mm,
                                     poses=None, keep_depth=keep_depth)
            prog(0.96, "Saving the mesh", force=True)
            fr = report.get("frames") or {}
            with sp.lock:  # a new trajectory: straighten, fuse and refine start over
                for name in ("straighten", "fuse", "spacetime", "refine", FINE_WALLS):
                    sp.state["stages"][name] = _empty_state(sp.pid, sp.sid)["stages"][name]
                    _rmtree(sp.dir / name)
                p = sp.dir / "straighten.json"
                if p.exists():
                    safe_call("removing straighten.json", p.unlink)
            self._new_base(sp, "track", sp.dir / "track", report, quality)
            # the tracked pass is the one with every sign (calibration health, FEATURE_PLAN H3); a run beside the
            # result (R2) records it once it has taken the old one's place
            if sp.live is not None:
                sp.deferred["health"] = report
            else:
                safe_call("recording the calibration health",
                          lambda: self.st.setup.record_scan_health(sp.pid, sp.sid, report, sp.recording_dir))
            self._stage_end(sp, "track", state="done", summary=self._track_summary(report, quality), quality=quality,
                            tracked_pct=round(100.0 * float(fr.get("tracked_fraction", 0.0)), 1), voxel_mm=voxel_mm,
                            job_id=ctx.job.id, warnings=user_warnings(report))
        except (JobCancelled, InterruptedError):
            self._stage_restore(sp, "track", before)
            raise
        except Exception as e:
            self._stage_end(sp, "track", state="failed", summary=_stage_failed("track", e))
            raise

    def _do_straighten(self, ctx: Any, sp: ScanProcess, prog: _Prog) -> str:
        """Returns the summary. ``skipped`` (base unchanged) when straightening is not available,
        fails, or finds no loop."""
        before = self._stage_start(sp, "straighten", ctx.job.id)
        try:
            with sp.lock:
                track = dict(sp.state["stages"]["track"])
            traj = read_trajectory(sp.dir / "track" / "trajectory.csv")
            prog(0.0, "Looking for loops in the camera path", force=True)
            try:
                from ohs.track.posegraph import StraightenParams, straighten_trajectory
            except ImportError:
                msg = "Straightening is not available in this version of the engine, so the scan was left as it is"
                self._stage_end(sp, "straighten", state="skipped", summary=msg, loops=None, correction_mm=None,
                                correction_deg=None, tags=None)
                return msg
            from ohs.io.recording import RecordingReader

            calib = RecordingReader(sp.recording_dir).calibration()
            try:
                sparams = StraightenParams(tags=bool(getattr(self.st.cfg.process, "straighten_tags",
                                                             StraightenParams.tags)))  # ENGINE_PLAN X8 step 2
                poses, rep = straighten_trajectory(sp.recording_dir, calib, traj, params=sparams,
                                                   progress=_straighten_progress(prog.sub(*STRAIGHTEN_SPANS["loops"])),
                                                   cancelled=lambda: ctx.cancelled)
            except (JobCancelled, InterruptedError):
                raise
            except Exception as e:
                if ctx.cancelled:
                    raise JobCancelled() from e
                log.exception("straighten_trajectory failed")
                msg = "Straightening could not finish, so the scan was left as it is"
                self._stage_end(sp, "straighten", state="skipped", summary=msg, loops=None, correction_mm=None,
                                correction_deg=None, tags=None)
                return msg
            ctx.check()
            rep = dict(rep or {})
            _write_json(sp.dir / "straighten.json", rep)
            looped, loops = straighten_looped(rep, poses)
            corr_mm, corr_deg = rep.get("correction_mm"), rep.get("correction_deg")
            verdict = rep.get("verdict") if isinstance(rep.get("verdict"), str) and rep.get("verdict") else None
            quality, voxel = quality_or_none(track.get("quality")) or "standard", track.get("voxel_mm")
            report: dict = {}
            if looped:  # the loops' fuse is also the reference surface for the laser crossings
                report = self._fuse_into(ctx, sp, "straighten", prog.sub(*STRAIGHTEN_SPANS["fuse"]), quality=quality,
                                         voxel_mm=voxel,
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
                    progress=lambda f, m: prog(_span(STRAIGHTEN_SPANS["crossings"], f), f"Laser crossings: {m}"))
            except (JobCancelled, InterruptedError):
                raise
            except Exception as e:
                if ctx.cancelled:
                    raise JobCancelled() from e
                log.exception("laser_curve_adjustment failed")
                crep = {"changed": False, "reason": "failed", "verdict": "Lining up the laser crossings could not finish"}
            ctx.check()
            rep["laser_curves"] = crep
            _write_json(sp.dir / "straighten.json", rep)
            adjusted = bool(crep.get("changed"))
            crossings = int(crep["crossings"]) if adjusted else None
            if not looped and not adjusted:
                # no loop (or loops that show the passes already line up) and nothing for the lasers to fix:
                # the base (and its edits) stay
                msg = verdict or "No loops found: nothing to straighten"
                # loops closed and nothing moved: said as what "skipped" means (UX_PLAN R22)
                msg = msg.replace(": the passes already line up", ", but there was nothing to straighten: the passes "
                                                                  "already line up", 1)
                with sp.lock:  # the base stays; its report says that Straighten found nothing to correct
                    res_rep = _read_json(sp.result_dir / "report.json")
                    if res_rep is not None and _set_straighten(res_rep, rep):
                        _write_json(sp.result_dir / "report.json", res_rep)
                self._stage_end(sp, "straighten", state="skipped", summary=msg, loops=int(loops or 0),
                                correction_mm=0.0, correction_deg=0.0, crossings=None, tags=None)
                return msg
            if adjusted:
                report = self._fuse_into(ctx, sp, "straighten", prog.sub(*STRAIGHTEN_SPANS["fuse2"]), quality=quality,
                                         voxel_mm=voxel,
                                         poses=new_poses)
                track_poses = {i: T for i, _, ok, T in traj if ok}
                corr_mm = max((float(np.linalg.norm(new_poses[i][:3, 3] - track_poses[i][:3, 3]))
                               for i in new_poses if i in track_poses), default=0.0)
                corr_deg = max(float(corr_deg or 0.0), float(crep.get("moved_deg") or 0.0))
            (sp.dir / "straighten" / "straighten.json").write_text(json.dumps(jsonable(rep), indent=1), encoding="utf-8")
            said = _carry_crossings_warning(report, crep) if adjusted else False
            if _set_straighten(report, rep) or said:  # with the laser crossings, which came after the first fuse
                _write_json(sp.dir / "straighten" / "report.json", report)
            prog(0.96, "Saving the mesh", force=True)
            with sp.lock:  # fuse and the refinements built on the old poses
                for name in ("fuse", "spacetime", "refine", FINE_WALLS):
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
                if isinstance(crep.get("warning"), str) and crep["warning"]:  # ENGINE_PLAN X0a: it may be fooled
                    verdict = f"{verdict.rstrip('.')}. {crep['warning']}"
            self._stage_end(sp, "straighten", state="done", summary=verdict, loops=int(loops or 0),
                            correction_mm=corr_mm, correction_deg=corr_deg, crossings=crossings, job_id=ctx.job.id,
                            tags=straighten_tags_used(rep))  # ENGINE_PLAN X8 step 2: how many tags held the scan
            return verdict
        except (JobCancelled, InterruptedError):
            self._stage_restore(sp, "straighten", before)
            raise
        except Exception as e:
            self._stage_end(sp, "straighten", state="failed", summary=_stage_failed("straighten", e))
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
                raise ApiError(409, "“Follow the camera” has no tracked frames to build the surface from.",
                               "Run “Follow the camera” again.")
            report = self._fuse_into(ctx, sp, "fuse", prog.sub(0.0, 0.95), quality=quality_or_none(track.get("quality")) or "standard",
                                     voxel_mm=voxel_mm if voxel_mm is not None else track.get("voxel_mm"), poses=poses)
            prog(0.96, "Saving the mesh", force=True)
            with sp.lock:  # the refinements built on the previous base
                for name in ("spacetime", "refine", FINE_WALLS):
                    sp.state["stages"][name] = _empty_state(sp.pid, sp.sid)["stages"][name]
                    _rmtree(sp.dir / name)
            self._new_base(sp, "fuse", sp.dir / "fuse", report, track.get("quality"))
            used = (report.get("mesh") or {}).get("voxel_mm")
            n = (report.get("frames") or {}).get("processed")
            self._stage_end(sp, "fuse", state="done", voxel_mm=used, job_id=ctx.job.id,
                            summary=f"Built the surface from {n:,} frames at a mesh detail of {_fmt(float(used))} mm"
                            if used and n else "Built the surface")
        except (JobCancelled, InterruptedError):
            self._stage_restore(sp, "fuse", before)
            raise
        except Exception as e:
            self._stage_end(sp, "fuse", state="failed", summary=_stage_failed("fuse", e))
            raise

    def _do_refine(self, ctx: Any, sp: ScanProcess, prog: _Prog) -> str:
        """"Refine with lasers": pull the base stage's mesh onto its laser cloud
        (:func:`ohs.mesh.anchor_to_points`). Returns the summary; ``skipped`` (base and edits
        unchanged) when the scan has no laser points or the mesh already agrees with them."""
        from ohs.mesh import anchor_to_points
        from ohs.track.export import load_ply

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
            V, F, S, L = _read_stage_mesh(src_dir / "mesh.ply", with_looks=True)
            V2, _, rep = anchor_to_points(V, F, P, progress=lambda f, m: prog(0.05 + 0.85 * f, f"Refining: {m}"))
            ctx.check()
            r0, r1 = rep["residual_before"]["median"], rep["residual_after"]["median"]
            fields = {"source": src, "points": rep["used"], "outliers": rep["outliers"],
                      "moved_pct": round(100.0 * rep["moved_share"], 1), "moved_mm": rep["moved"]["p95"],
                      "residual_before_mm": r0, "residual_after_mm": r1}
            if not refine_changes(rep):
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
                save_refined_mesh(tmp / "mesh.ply", V2, F, S, L)  # the vertices move: each keeps its sigma and looks
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
            self._stage_end(sp, "refine", state="failed", summary=_stage_failed("refine", e))
            raise

    def _do_spacetime(self, ctx: Any, sp: ScanProcess, prog: _Prog, params: Any = None) -> str:
        """"Refine from the images" (a trial build): measure the base stage's mesh against the
        recording's frames (:func:`spacetime_refine`; ``params``: its ``SpacetimeParams``, the
        defaults when None). Returns the summary; ``skipped`` (base and edits unchanged) when too few
        patches hold dot texture in enough frames."""
        from ohs.io.recording import RecordingReader
        from ohs.track.spacetime import GPU_RESET_HINT, GPU_RESET_TEXT, GpuResetError, SpacetimeParams

        before = self._stage_start(sp, "spacetime", ctx.job.id)
        try:
            self._need_base(sp)
            with sp.lock:
                sp.state["stages"]["spacetime"].pop("guard", None)  # a guarded run's (Make mesh, ENGINE_PLAN X6) counts
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
                raise ApiError(409, "This scan has no calibration or no tracked frames.", "Run “Follow the camera” again.")
            try:
                V2, F, S, L, rep = spacetime_refine(sp.recording_dir, calib, src_dir, poses, params or SpacetimeParams(),
                                                    cancelled=lambda: ctx.cancelled, progress=prog)
            except GpuResetError as e:  # the search's self-check: nothing is written, the mesh stays as it was
                raise ApiError(503, GPU_RESET_TEXT, GPU_RESET_HINT) from e
            ctx.check()
            rep.pop("patch_results", None)
            covered = round(100.0 * float(rep["covered_share"]), 1)
            fields = {"source": src, "frames": rep["frames"], "patches": rep["patches"], "kept": rep["kept"],
                      "covered_pct": covered, "moved_mm": rep["moved"]["p95"], "device": rep["device"]}
            if rep.get("guard"):  # Make mesh's guard (ENGINE_PLAN X6): what it refined and what it left
                fields["guard"] = rep["guard"]
            if not spacetime_changes(rep):
                msg = (f"Too few parts of the surface hold dots in enough frames ({rep['kept']:,} of "
                       f"{rep['patches']:,} patches), so the mesh was left as it is")
                self._stage_end(sp, "spacetime", state="skipped", summary=msg, **fields)
                return msg
            prog(0.93, "Saving the mesh", force=True)
            tmp = sp.dir / f"spacetime.tmp-{ctx.job.id}"
            _rmtree(tmp)
            tmp.mkdir(parents=True)
            try:
                report = write_spacetime_stage(tmp, src_dir, str(src), V2, F, S, L, rep)
                ctx.check()
                _move_dir(tmp, sp.dir / "spacetime")
            finally:
                _rmtree(tmp)
            with sp.lock:  # the lasers refine the new surface
                sp.state["stages"]["refine"] = _empty_state(sp.pid, sp.sid)["stages"]["refine"]
                _rmtree(sp.dir / "refine")
            self._new_base(sp, "spacetime", sp.dir / "spacetime", report, quality)
            where = "on the graphics card" if rep["device"] == "gpu" else "without a graphics card"
            summary = (f"Measured {covered:g} % of the surface against {rep['frames']:,} frames ({rep['kept']:,} patches, "
                       f"{where}); it moved by up to {rep['moved']['p95']:.2f} mm (95 %)")
            if rep.get("guard"):
                g = rep["guard"]
                left = sum(g["patches_left"].values())
                summary += (f"; {left:,} more patches were left as they were (at edges, rims, thin walls or where the "
                            f"images disagree with the mesh)")
                if g["limits"].get("move_mm") is not None:
                    summary += (f", and {g['vertices_left']:,} points that would have moved more than "
                                f"{g['limits']['move_mm']:g} mm")
            self._stage_end(sp, "spacetime", state="done", summary=summary, job_id=ctx.job.id, **fields)
            return summary
        except (JobCancelled, InterruptedError):
            self._stage_restore(sp, "spacetime", before)
            raise
        except Exception as e:
            self._stage_end(sp, "spacetime", state="failed", summary=_stage_failed("spacetime", e))
            raise

    def _make_mesh_spacetime(self, ctx: Any, sp: ScanProcess, prog: _Prog) -> str:
        """"Refine from the images" as a part of Make mesh (``[process] refine_from_images``, ENGINE_PLAN X6): the
        stage itself (:meth:`_do_spacetime`) with :func:`make_mesh_spacetime_params`, on the graphics card only.
        Without one (its search takes about 20 minutes for a short scan on the CPU) the stage is ``skipped``, the mesh
        stays as it is, and the log says so."""
        if not images_on_gpu():
            msg =("Refine from the images needs a graphics card (without one it takes many minutes), so Make mesh "
                   "left the mesh as it is")
            log.info("make mesh: refine from the images skipped: no graphics card")
            self._stage_start(sp, "spacetime", ctx.job.id)
            self._stage_end(sp, "spacetime", state="skipped", summary=msg, device="cpu")
            return msg
        return self._do_spacetime(ctx, sp, prog, make_mesh_spacetime_params())

    # ------------------------------------------------------------------ "Make only the thin walls finer" (X5 step 2)
    def fine_walls_chosen(self, pid: str, sid: str) -> bool:
        """Whether Make mesh makes this scan's thin walls finer: the scan's own choice (``fine_walls`` in its entry, set
        by ``POST .../process {fine_walls}``), else ``[process] fine_walls`` (off by default)."""
        v = self.st.projects.scan_entry(pid, sid).get("fine_walls")
        return v if isinstance(v, bool) else bool(getattr(self.st.cfg.process, "fine_walls", False))

    def _fine_walls_plan(self, sp: ScanProcess, quality: str, voxel_mm: float | None) -> dict | None:
        """Before Follow the camera: None when the scan did not choose "Make only the thin walls finer" (Make mesh runs
        exactly as before); else ``{"keep": True, frames, need_mb, free_mb}`` (Follow the camera keeps the depth) or
        ``{"keep": False, "why": ..., ...}``: not at Preview quality, only on the graphics card's path, only at the mesh
        detail it was measured at (:data:`FINE_WALLS_VOXEL_MM`), only when the drive has the kept depth's estimate
        (:func:`ohs.app.fine_walls.kept_depth_estimate`) and :data:`FINE_WALLS_DISK_MARGIN_MB` free. A plan that
        fails is logged and Make mesh goes on without the step (``why`` "failed")."""
        if not self.fine_walls_chosen(sp.pid, sp.sid):
            return None
        try:
            return self._fine_walls_plan_on(sp, quality, voxel_mm)
        except (JobCancelled, InterruptedError, ApiError):
            raise
        except Exception:  # it never costs the scan: Make mesh goes on without it
            log.exception("make only the thin walls finer: could not be planned; the mesh is made without it")
            return {"keep": False, "why": "failed"}

    def _fine_walls_plan_on(self, sp: ScanProcess, quality: str, voxel_mm: float | None) -> dict:
        """:meth:`_fine_walls_plan` for a scan that chose it."""
        from ohs.app.fine_walls import kept_depth_estimate
        from ohs.app.pipeline import default_fusion_params, effective_scale

        if quality == "preview":
            return {"keep": False, "why": "preview"}
        if not images_on_gpu():
            return {"keep": False, "why": "no_graphics_card"}
        act = self._preset_acting(sp)
        rec, mode, width = self._scan_params(sp)
        params = self._stage_params(mode, quality, voxel_mm if voxel_mm is not None else act.get("voxel_mm"), width,
                                    thin_walls=act.get("thin_walls"))
        fusion = params.pipeline.fusion or default_fusion_params(effective_scale(int(round(width * params.pipeline.scale))))
        v = float(fusion.voxel_mm)
        if abs(v - FINE_WALLS_VOXEL_MM) > 1e-6:
            return {"keep": False, "why": "mesh_detail", "voxel_mm": v}
        est = kept_depth_estimate(rec, params)
        need = est["bytes"] / 2 ** 20
        free = free_disk_mb(sp.dir)
        out = {"frames": est["frames"], "need_mb": round(need, 1), "free_mb": round(free, 1),
               "margin_mb": FINE_WALLS_DISK_MARGIN_MB}
        if free < need + FINE_WALLS_DISK_MARGIN_MB:
            log.warning("make only the thin walls finer: skipped, %.0f MB free, %.0f MB needed + %.0f MB margin", free,
                        need, FINE_WALLS_DISK_MARGIN_MB)
            return {"keep": False, "why": "disk", **out}
        return {"keep": True, **out}

    def _make_mesh_fine_walls(self, ctx: Any, sp: ScanProcess, prog: _Prog, plan: dict) -> dict:
        """"Make only the thin walls finer" in Make mesh (ENGINE_PLAN X5 step 2), after Straighten: the base stage's mesh
        refined on its thin walls with the depth Follow the camera kept (:func:`ohs.app.fine_walls.make_fine_walls`).
        When it acted its mesh becomes the base (stage ``fine_walls``) and Refine with lasers, the edits, the Check and
        the report work on it; when it did not (or ``plan`` says it could not run) the stage is ``skipped`` with the
        reason. It never costs the scan: a failure is logged, the stage says so, the mesh stays as fused. The kept depth
        is removed in every case. Returns the page's block (``made.fine_walls``)."""
        from ohs.app.fine_walls import SPOOL_DIR, make_fine_walls, spool_size_mb

        info: dict[str, Any] = {"chosen": True, "acted": False, "why": plan.get("why"), "share": None, "regions": None,
                                "seconds": None, "kept_mb": None,
                                **{k: plan[k] for k in ("need_mb", "free_mb", "voxel_mm") if k in plan}}
        spool = sp.dir / "track" / SPOOL_DIR
        tmp = sp.dir / f"{FINE_WALLS}.tmp-{ctx.job.id}"
        try:
            if not plan.get("keep"):
                info["line"] = fine_walls_line(info)
                self._stage_start(sp, FINE_WALLS, ctx.job.id)
                self._stage_end(sp, FINE_WALLS, state="skipped", summary=info["line"], why=info["why"])
                return info
            info["kept_mb"] = round(spool_size_mb(spool), 1)
            before = self._stage_start(sp, FINE_WALLS, ctx.job.id)
            t0 = time.monotonic()
            try:
                with sp.lock:
                    src = str((sp.state["base"] or {}).get("stage") or "track")
                    quality = sp.state["stages"]["track"].get("quality")
                src_dir = sp.dir / src
                prog(0.0, "Making the thin walls finer", force=True)
                _rmtree(tmp)
                rep = make_fine_walls(src_dir, spool, out_dir=tmp, cancelled=lambda: ctx.cancelled,
                                      progress=lambda f, m: prog(0.95 * f, f"Making the thin walls finer: {m}"))
                ctx.check()
                rep["source"] = src
                walls = rep.get("walls") or {}
                info.update(regions=walls.get("n_regions"), share=rep.get("share"), report=rep,
                            seconds=round(time.monotonic() - t0, 1))
                if not rep.get("acted"):
                    info["why"] = rep.get("why") or "no_thin_walls"
                    info["line"] = fine_walls_line(info)
                    self._stage_end(sp, FINE_WALLS, state="skipped", summary=info["line"], source=src, why=info["why"],
                                    kept_mb=info["kept_mb"], regions=info["regions"])
                    return info
                for name in ("trajectory.csv", "laser_points.ply", "marker_map.json"):
                    if (src_dir / name).is_file():
                        shutil.copy2(src_dir / name, tmp / name)
                report = _read_json(src_dir / "report.json") or {}
                report["fine_walls"] = rep
                _write_json(tmp / "report.json", jsonable(report))
                ctx.check()
                _move_dir(tmp, sp.dir / FINE_WALLS)
                with sp.lock:  # the lasers refine the finer surface
                    sp.state["stages"]["refine"] = _empty_state(sp.pid, sp.sid)["stages"]["refine"]
                    _rmtree(sp.dir / "refine")
                prog(0.96, "Saving the mesh", force=True)
                self._new_base(sp, FINE_WALLS, sp.dir / FINE_WALLS, report, quality)
                info.update(acted=True, why=None, seconds=round(time.monotonic() - t0, 1))
                info["line"] = fine_walls_line(info)
                info["action"] = "Make the mesh again without this"
                self._stage_end(sp, FINE_WALLS, state="done", summary=info["line"], job_id=ctx.job.id, source=src,
                                why=None, share_pct=round(100.0 * float(rep.get("share") or 0.0), 2),
                                regions=info["regions"], kept_mb=info["kept_mb"])
                return info
            except (JobCancelled, InterruptedError):
                self._stage_restore(sp, FINE_WALLS, before)
                raise
            except Exception:  # it never costs the scan: the mesh stays as fused
                if ctx.cancelled:
                    raise JobCancelled() from None
                log.exception("make only the thin walls finer failed; the mesh stays as fused")
                info.update(acted=False, why="failed", report=None)
                info["line"] = fine_walls_line(info)
                self._stage_end(sp, FINE_WALLS, state="failed", summary=info["line"], why="failed")
                return info
        finally:
            _rmtree(tmp)
            _rmtree(spool)  # the kept depth goes in every case: done, skipped, failed or cancelled

    @staticmethod
    def _record_fine_walls(sp: ScanProcess, made: dict, block: dict | None) -> None:
        """``result/report.json``'s ``fine_walls``: the engine's block (the walls found, the regions, the frames, the
        surface made finer, the moves, the seam, the times; :func:`ohs.app.fine_walls.make_fine_walls`) with what the
        page reads (``made.fine_walls``), also when it did not act or could not run."""
        path = sp.result_dir / "report.json"
        rep = _read_json(path)
        if rep is None:
            return
        rep["fine_walls"] = {**(block or {}), **{k: v for k, v in made.items() if k not in ("line", "action")}}
        _write_json(path, jsonable(rep))

    # ------------------------------------------------------------------ a run beside the result (UX_PLAN R2)
    def _run_staged(self, ctx: Any, sp: ScanProcess, fn: Callable[[ScanProcess], str]) -> str:
        """Run ``fn`` (every stage from the recording, then the recipe's edits) on a :class:`ScanProcess` of its own
        in ``<scan>/.remake-<job id>/`` (its ``process/`` and ``result/``; the recording is the scan's), and put it in
        the place of the scan's ``process/`` and ``result/`` only once it has finished (:func:`swap_in`). Until then
        the scan's mesh, stages, edits and undo history, report, laser points and scan entry are not touched; a
        cancelled or failed run removes its folder and leaves the scan exactly as it was.

        **Disk space:** while it runs, the scan holds two results (the old one and the new one being built, about the
        size of the old ``process/`` and ``result/`` together); the Free space numbers count the run's folder under the
        scan's ``other`` until it takes the old one's place, when the old one goes. An app that stops on the way is
        put right at the next start (:meth:`recover_remakes`)."""
        scan_dir = sp.dir.parent
        staging = scan_dir / f"{REMAKE_PREFIX}{ctx.job.id}"
        _rmtree(staging)
        staging.mkdir(parents=True)
        new = ScanProcess(sp.pid, sp.sid, staging)
        new.recording_dir = sp.recording_dir
        new.live = sp
        t0 = new.t0 = time.monotonic()
        with sp.lock:
            new.state["version"] = int(sp.state["version"])  # the new mesh's versions follow the old one's
            sp.making = new
        try:
            msg = fn(new)
            # how long the whole run took, every stage and edit (UX_PLAN R17: the report's time is the whole Make mesh)
            new.state["made"] = {"job_id": ctx.job.id, "kind": ctx.job.kind, "when": now_iso(),
                                 "seconds": round(time.monotonic() - t0, 1),
                                 "stages": {n: new.state["stages"][n].get("seconds") for n in ALL_STAGES
                                            if new.state["stages"][n].get("seconds") is not None}}
            if new.fine_walls is not None:  # ENGINE_PLAN X5 step 2: what "Make only the thin walls finer" did
                fw = dict(new.fine_walls)
                block = fw.pop("report", None)
                new.state["made"]["fine_walls"] = fw
                safe_call("recording the thin walls made finer", lambda: self._record_fine_walls(new, fw, block))
            # ENGINE_PLAN X5 step 1: thin walls, a thin plate, small holes? A hint only: it reads the mesh it found
            ctx.progress(1.0, "Looking for thin walls and small holes", force=True)
            hint = safe_call("looking for thin parts", lambda: self._thin_parts(new))
            if hint is not None:
                new.state["made"]["thin_parts"] = hint
            new.save()
            self._take_place(ctx, sp, new)
            return msg
        finally:
            with sp.lock:
                sp.making = None
            safe_call("removing the kept depth", lambda: remove_kept_depth(new))  # never kept past the run
            if _old_waiting(staging):  # a swap that could not be undone: once more now, else at the next start
                safe_call("putting the earlier result back", lambda: recover_remake(scan_dir, staging))
            if _old_waiting(staging):
                log.error("scan %s/%s: the earlier result waits in %s; it is put back at the next start", sp.pid,
                          sp.sid, staging)
            else:
                _rmtree(staging)

    def _take_place(self, ctx: Any, sp: ScanProcess, new: ScanProcess) -> None:
        """The finished run takes the old result's place (the job's point of no return), then the scan entry and the
        calibration health are written as a run on the scan itself writes them."""
        staging = new.dir.parent
        if not (new.result_dir / "mesh.ply").is_file():
            raise ApiError(500, "The new mesh was not made, so the earlier one is kept as it was.",
                           "Press Make mesh again.")
        fields = jsonable(dict(new.deferred["fields"]))
        with sp.lock:
            ctx.commit()  # a cancel from here on is too late: the new result goes in whole
            try:
                swap_in(sp.dir.parent, staging, fields)
            except OSError:
                log.exception("scan %s/%s: the new result could not take the old one's place", sp.pid, sp.sid)
                raise ApiError(409, "The new mesh could not take the place of the earlier one, because a file of this "
                                    "scan is open in another program. The earlier mesh is kept as it was.",
                               "Close the program that has the file open, then press Make mesh again.") from None
            sp.load()
            sp.reset_caches()
        if fields:
            safe_call("updating the scan", lambda: self.st.projects.update_scan(sp.pid, sp.sid, **fields))
        rep = new.deferred.get("health")
        if isinstance(rep, dict):
            safe_call("recording the calibration health",
                      lambda: self.st.setup.record_scan_health(sp.pid, sp.sid, rep, sp.recording_dir))

    def recover_remakes(self) -> int:
        """At start: put right every run's folder (``.remake-*``) an app that stopped left in a scan
        (:func:`recover_remake`); returns how many there were."""
        root = Path(self.st.projects.root)
        n = 0
        if not root.is_dir():
            return 0
        for pdir in root.iterdir():
            if not pdir.is_dir() or pdir.name.startswith(".") or not (pdir / "scans").is_dir():
                continue
            for sdir in (pdir / "scans").iterdir():
                for staging in (sorted(sdir.glob(REMAKE_PREFIX + "*")) if sdir.is_dir() else []):
                    n += 1
                    try:
                        fields = recover_remake(sdir, staging)
                    except OSError:
                        log.exception("scan %s: a run's folder %s could not be put right", sdir, staging.name)
                        continue
                    log.warning("scan %s/%s: a Make mesh stopped by the app was %s", pdir.name, sdir.name,
                                "finished (its new result is whole)" if fields is not None else
                                "undone (the earlier result is as it was)")
                    if fields:
                        safe_call("updating the scan",
                                  lambda: self.st.projects.update_scan(pdir.name, sdir.name, **fields))
        return n

    def _stage_job(self, pid: str, sid: str, kind: str, body: Callable[[Any, ScanProcess, _Prog], str],
                   *, need_track: bool, message: str, meta: dict | None = None, need_recording: bool = True,
                   check: Callable[[ScanProcess], None] | None = None, staged: bool = False) -> Any:
        with self.st._lock:
            self._check_free(pid, sid)
            if need_recording:
                self._need_recording(pid, sid)
            sp = self.scan(pid, sid)
            if need_track:
                self._need_track(sp)
            if check is not None:
                check(sp)
            had_result = (sp.result_dir / "mesh.ply").is_file()
            before = self.st.projects.scan_entry(pid, sid)
            self.st.projects.update_scan(pid, sid, status="processing")

            def run(ctx: Any) -> dict:
                if staged:  # every stage from the recording: built beside the result (R2)
                    msg = self._run_staged(ctx, sp, lambda new: body(ctx, new, _Prog(ctx, 0.0, 1.0)))
                    ctx.job.meta["mesh_kept"] = False  # the new mesh took the earlier one's place
                else:
                    msg = body(ctx, sp, _Prog(ctx, 0.0, 1.0))
                return {"project_id": pid, "scan_id": sid, "scan": self.st.projects.get_scan(pid, sid), "message": msg}

            def done(job: Any) -> None:
                kept = staged and had_result and job.state != "done"
                if kept:  # the scan is as it was, its status too
                    self._restore_status(pid, sid, before)
                else:
                    self._settle_status(sp, job, had_result)
                if job.state == "failed":
                    self.st.notify("error", failure_notice(job, f"{message} failed.",
                                                           "The earlier mesh is kept as it was." if kept else ""))
                self.publish(sp)

            job = self.st.jobs.submit(kind, run, message=f"Queued: {message}",
                                      meta={"project_id": pid, "scan_id": sid, **(meta or {}),
                                            **({"mesh_kept": had_result} if staged else {})}, on_done=done)
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

    def _restore_status(self, pid: str, sid: str, before: dict) -> None:
        """The scan entry's status and error as they were before a run that left the scan as it was (R2)."""
        safe_call("updating the scan", lambda: self.st.projects.update_scan(
            pid, sid, status=before.get("status") or "processed", error=before.get("error")))

    @staticmethod
    def _quality_arg(quality: Any) -> str:
        """A requested quality, normalised ("full" = "standard"); 400 if unknown."""
        try:
            return normalize_quality(quality)
        except ValueError:
            raise ApiError(400, "The app does not know this quality.", "Pick Preview, Standard or Best.") from None

    def submit_track(self, pid: str, sid: str, quality: str | None) -> Any:
        if quality is None:  # P2: the scan's preset's quality, else standard
            presets = getattr(self.st, "presets", None)
            quality = (presets.acting(pid, sid) if presets is not None else {}).get("quality", "standard")
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
        # it reads only the base stage's mesh and laser points: no recording needed (a merged scan too)
        return self._stage_job(pid, sid, "process_refine", lambda ctx, sp, prog: self._do_refine(ctx, sp, prog),
                               need_track=False, need_recording=False, check=self._need_refine_source,
                               message="Refining with the laser lines")

    def submit_spacetime(self, pid: str, sid: str) -> Any:
        return self._stage_job(pid, sid, "process_spacetime", lambda ctx, sp, prog: self._do_spacetime(ctx, sp, prog),
                               need_track=True, message="Refining from the images")

    def submit_fuse(self, pid: str, sid: str, voxel_mm: float | None) -> Any:
        if voxel_mm is not None:
            _num({"voxel_mm": voxel_mm}, "voxel_mm", "fuse", *VOXEL_RANGE)

        def body(ctx: Any, sp: ScanProcess, prog: _Prog) -> str:
            self._do_fuse(ctx, sp, prog, voxel_mm)
            return sp.state["stages"]["fuse"]["summary"] or "Surface built"

        return self._stage_job(pid, sid, "process_fuse", body, need_track=True, message="Building the surface",
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
            try:
                entry = self._run_edit(ctx, sp, o["op"], params, label, prog.sub(i / max(len(ops), 1), (i + 1) / max(len(ops), 1)))
            except ApiError as e:
                if o["op"] != "remove_table" or e.status != 422:
                    raise
                # a scan without a table: the step is skipped, in the engine's words, and the rest runs
                out.append(f"Remove the table: skipped. {e.detail.rstrip('.')}")  # the steps are joined with "; "
                continue
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
            # ENGINE_PLAN X6: "Refine from the images" too, when the app's setting asks for it (off by default)
            spacetime = bool(getattr(self.st.cfg.process, "refine_from_images", False)) and q != "preview"
            # ENGINE_PLAN X5 step 2: "Make only the thin walls finer", when the scan chose it (None: as before)
            fine = self._fine_walls_plan(sp, q, s.get("voxel_mm"))
            keep = bool(fine and fine.get("keep"))
            # each part's share of the percentage (UX_PLAN R18: MAKE_MESH_SHARES), the parts that run only
            spans = make_mesh_plan(straighten=straighten, refine=refine, edits=bool(ops), spacetime=spacetime,
                                   fine_walls=keep)
            sp.plan = [{"stage": k, "from": round(a, 4), "to": round(b, 4)} for k, (a, b) in spans.items()]
            p_ops = spans["edits"][0] if "edits" in spans else 1.0
            self._do_track(ctx, sp, prog.sub(*spans["track"]), q, s.get("voxel_mm"), keep_depth=keep)
            if straighten:
                self._do_straighten(ctx, sp, prog.sub(*spans["straighten"]))
            if fine is not None:
                part = prog.sub(*spans[FINE_WALLS]) if keep else prog.sub(spans["track"][1], spans["track"][1])
                sp.fine_walls = self._make_mesh_fine_walls(ctx, sp, part, fine)
            if spacetime:
                try:
                    self._make_mesh_spacetime(ctx, sp, prog.sub(*spans["spacetime"]))
                except (JobCancelled, InterruptedError):
                    raise
                except Exception:  # the stage says it failed; the mesh stays as fused
                    log.exception("refine from the images failed in a recipe")
            if refine:
                try:
                    self._do_refine(ctx, sp, prog.sub(*spans["refine"]))
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
            raise ApiError(422, "The recipe can run on the edits only, or on everything.", _RELOAD)
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
                        self.st.notify("warn", failure_notice(job, f"The recipe \u201c{recipe['name']}\u201d stopped."))
                    self.publish(sp)

                job = self.st.jobs.submit("process_recipe", run, message=f"Queued: recipe {recipe['name']}",
                                          meta={"project_id": pid, "scan_id": sid, "recipe_id": recipe_id,
                                                "recipe_name": recipe["name"], "scope": scope}, on_done=done)
            self.publish(sp)
            return job
        return self._stage_job(pid, sid, "process_recipe",
                               lambda ctx, sp, prog: self._run_recipe(ctx, sp, recipe, "all", prog),
                               need_track=False, message=f"The recipe {recipe['name']}",
                               meta={"recipe_id": recipe_id, "recipe_name": recipe["name"], "scope": scope}, staged=True)

    # ------------------------------------------------------------------ the thin-parts hint (ENGINE_PLAN X5 step 1)
    def _thin_parts(self, sp: ScanProcess) -> dict | None:
        """At the end of Make mesh (or a recipe on everything): look for thin walls, a thin plate lying flat and small
        holes in the mesh it made (:func:`ohs.mesh.thin_parts.find_thin_parts`, which only reads) and say whether the
        preset "Thin sheet metal and small parts" is worth a try. The whole finding goes into ``result/report.json``
        (``thin_parts``); the hint, in words, is returned for ``ProcessState.made.thin_parts``. Nothing is changed:
        the user decides. None when the mesh's detail is not known."""
        from ohs.mesh.thin_parts import find_thin_parts, hint_words

        rep_path = sp.result_dir / "report.json"
        rep = _read_json(rep_path) or {}
        voxel = (rep.get("mesh") or {}).get("voxel_mm")
        if not isinstance(voxel, (int, float)) or not voxel > 0:
            return None
        with sp.lock:
            V, F, S, _ = self._load_step(sp, int(sp.state["cursor"]))
        prm = rep.get("params") or {}
        made = sp.state.get("made") or {}
        # ENGINE_PLAN X5 step 2: walls made finer in this mesh are not suggested again, nor estimated
        finer = bool((made.get("fine_walls") or {}).get("acted"))
        finding = find_thin_parts(V, F, voxel_mm=float(voxel), sigma=S, preset_id=(rep.get("preset") or {}).get("id"),
                                  opposite_sides=(prm.get("reconstruction") or {}).get("opposite_mm") is not None,
                                  walls_made_finer=finer)
        rep["thin_parts"] = finding
        _write_json(rep_path, rep)
        card = (prm.get("compute") or {}).get("depth") not in (None, "cpu")
        words = hint_words(finding, seconds=made.get("seconds"), triangles=int(len(F)), graphics_card=card,
                           fine=None if finer else fine_walls_offer(rep, _read_json(sp.dir / "track" / "report.json")))
        entry = self.st.projects.scan_entry(sp.pid, sp.sid)
        kinds = [k for k in ("walls", "plates", "holes") if (finding.get(k) or {}).get("found")]
        return {"suggest": bool(finding.get("suggest")), "why_not": finding.get("why_not"), "found": kinds,
                "voxel_mm": float(voxel), "preset_id": finding.get("suggest_preset"),
                "dismissed": THIN_PARTS_HINT in (entry.get("hints_dismissed") or []), "words": words}

    def dismiss_thin_parts(self, pid: str, sid: str) -> dict:
        """``POST .../process/hints/thin-parts/dismiss``: the user put the hint away. It stays away for this scan, also
        after the mesh is made again (the scan's entry keeps ``hints_dismissed``). Changes nothing else."""
        sp = self.scan(pid, sid)
        entry = self.st.projects.scan_entry(pid, sid)
        gone = [h for h in (entry.get("hints_dismissed") or []) if isinstance(h, str)]
        if THIN_PARTS_HINT not in gone:
            self.st.projects.update_scan(pid, sid, hints_dismissed=gone + [THIN_PARTS_HINT])
        with sp.lock:
            made = sp.state.get("made")
            if isinstance(made, dict) and isinstance(made.get("thin_parts"), dict):
                made["thin_parts"]["dismissed"] = True
                sp.save()
        self.publish(sp)
        return self.view(sp)

    def make_mesh(self, pid: str, sid: str, quality: str, fine_walls: bool | None = None) -> Any:
        """``POST .../process`` (M1 "Make mesh"): the Default recipe, all stages, at ``quality``. ``fine_walls``
        (ENGINE_PLAN X5 step 2): when given, the scan's choice of "Make only the thin walls finer", stored in its entry
        for this and every later Make mesh (and recipe on everything); None: the stored choice stays."""
        quality = self._quality_arg(quality)
        recipe = self._preset_recipe(pid, sid)  # P2: the preset's recipe when it names one, else Default
        with self.st._lock:
            entry = self._check_free(pid, sid, make_mesh=True)
            self._need_recording(pid, sid)
            sp = self.scan(pid, sid)
            if fine_walls is not None:  # stored only once the request is taken
                self.st.projects.update_scan(pid, sid, fine_walls=bool(fine_walls))
            had_result = (sp.result_dir / "mesh.ply").is_file()
            self.st.projects.update_scan(pid, sid, status="processing", **({} if had_result else {"error": None}))

            def run(ctx: Any) -> dict:
                # built beside the scan's result, which it replaces only once it has finished (UX_PLAN R2)
                self._run_staged(ctx, sp, lambda new: self._run_recipe(ctx, new, recipe, "all", _Prog(ctx, 0.0, 1.0),
                                                                       quality=quality))
                ctx.job.meta["mesh_kept"] = False  # the new mesh took the earlier one's place
                scan = self.st.projects.get_scan(pid, sid)
                return {"project_id": pid, "scan_id": sid, "scan": scan, "message": "Mesh ready"}

            def done(job: Any) -> None:
                kept = had_result and job.state != "done"
                if kept:
                    self._restore_status(pid, sid, entry)
                if job.state == "done":
                    self.st.notify("info", f"Mesh ready: \u201c{entry.get('name', sid)}\u201d.")
                elif job.state == "failed" and kept:
                    why = "" if job.error == failure_text(job.kind) else job.error or ""
                    self.st.notify("error", " ".join(x for x in (
                        "Making the mesh again failed.", why, "The earlier mesh is kept as it was, with its edits; your "
                        "recording is safe.") if x))
                elif job.state == "failed":  # as in M1: failed, with the reason
                    safe_call("updating the scan",
                              lambda: self.st.projects.update_scan(pid, sid, status="failed", error=job.error))
                    self.st.notify("error", failure_notice(job, "Making the mesh failed.",
                                                           "Your recording is safe: try Make mesh again, or at "
                                                           "Preview quality."))
                elif kept:
                    self.st.notify("info", "Making the mesh again was cancelled. The earlier mesh is kept as it was, "
                                           "with its edits.")
                else:
                    self._settle_status(sp, job, had_result)
                    self.st.notify("info", "Making the mesh was cancelled. Your recording is safe.")
                self.publish(sp)

            job = self.st.jobs.submit("process_scan", run, message="Queued for processing",
                                      meta={"project_id": pid, "scan_id": sid, "quality": quality,
                                            "mesh_kept": had_result}, on_done=done)
        self.publish(sp)
        return job

    def save_recipe(self, name: str, description: str | None, from_scan: dict | None, settings: dict | None,
                    ops: list | None) -> dict:
        if from_scan is not None:
            sp = self.scan(str(from_scan.get("project_id")), str(from_scan.get("scan_id")))
            with sp.lock:
                st = sp.state
                if st["base"] is None:
                    raise ApiError(409, "This scan has no processing steps to save as a recipe.",
                                   "Make its mesh and some edits first.")
                track = st["stages"]["track"]
                made_by = st["base"]["stage"]
                for name in ("refine", "spacetime", FINE_WALLS):  # a refinement: the stage it refined
                    if made_by == name:
                        made_by = st["stages"][name].get("source") or "track"
                voxel = st["stages"]["fuse"].get("voxel_mm") if made_by == "fuse" else track.get("voxel_mm")
                settings = {"quality": quality_or_none(track.get("quality")) or "standard",
                            "straighten": "auto" if st["stages"]["straighten"]["state"] in ("done", "skipped") else "off",
                            "refine": "auto" if st["stages"]["refine"]["state"] in ("done", "skipped") else "off",
                            "voxel_mm": voxel}
                # the edits before the space was freed (FEATURE_PLAN R2: kept by name in the base), then the rest
                # without by_position (F15): how ties are broken is the app's setting when the recipe runs
                ops = [{"op": e["op"], "params": {k: v for k, v in e["params"].items() if k != "by_position"}} for e in
                       list(st["base"].get("applied") or []) + st["edits"][: st["cursor"]] if e["op"] in RECIPE_OPS]
        elif settings is None or ops is None:
            raise ApiError(422, "The page did not say what the recipe holds.", _RELOAD)
        return self.recipes.create(name, description, settings, ops)

    # ------------------------------------------------------------------ overlays
    def holes(self, pid: str, sid: str) -> list:
        sp = self.scan(pid, sid)
        self._need_base(sp)
        return sp.holes(lambda: self.current_mesh(sp))

    def fit(self, pid: str, sid: str, kind: str, face: int, point: Any, tolerance_mm: float | None = None,
            version: int | None = None, with_extent: bool = False, with_form: bool = False) -> dict:
        """``POST .../fit``: one click on the current mesh grows a plane, cylinder or hole region and
        fits it (:func:`ohs.measure.fit_at`). Read-only; the mesh is kept per version for the next
        click. ``version``: the mesh version the click was made on (409 when it changed).
        ``with_extent``: the answer also has ``extent``, where the region lies on its shape
        (:attr:`RegionFit.extent <ohs.measure.region.RegionFit.extent>`, made with every fit anyway);
        the kept features ask for it. ``with_form``: the answer also has ``form``, whether a cylinder
        is a "boss" or a "bore" (:attr:`RegionFit.form <ohs.measure.region.RegionFit.form>`; None
        when not clear, and for a plane or a hole); the kept features ask for it too. Both off by
        default: the route answers as it did before."""
        from ohs.measure import KINDS, fit_at

        if kind not in KINDS:
            raise ApiError(422, "The app does not know this kind of shape.", "Pick a plane, a cylinder or a hole.")
        sp = self.scan(pid, sid)
        self._need_base(sp)
        with sp.lock:  # the step's arrays, loaded once per version
            v = int(sp.state["version"])
            if version is not None and int(version) != v:
                raise ApiError(409, "The mesh changed since this click.", "Reload the mesh and click again.")
            cached = sp._fit_mesh
            if cached is None or cached[0] != v:
                V, F, S, Fl = self._load_step(sp, int(sp.state["cursor"]))
                cached = sp._fit_mesh = (v, V, F, S, Fl)
        _, V, F, S, Fl = cached
        if not 0 <= int(face) < len(F):
            raise ApiError(422, "The click is not on the current mesh.", "Reload the mesh and click again.")
        try:
            r = fit_at(V, F, int(face), point, kind, sigma=S, filled=Fl, tolerance_mm=tolerance_mm)
        except ValueError as e:
            hint = {"plane": "Click in the middle of a flat area.",
                    "cylinder": "Click on the curved wall of a boss, shaft or bore.",
                    "hole": "Click near the edge of a hole that was scanned all round."}[kind]
            raise ApiError(422, plain_reason(e, f"No {kind} could be fitted where you clicked."), hint) from None
        faces = np.asarray(r.faces, dtype="<u4")
        out = {"kind": kind, "shape": r.fit.to_dict(), "faces": base64.b64encode(faces.tobytes()).decode("ascii"),
               "n_faces": int(len(faces)),
               "tolerance_mm": None if r.tolerance_mm is None else float(r.tolerance_mm),  # None for a hole
               "mean_sigma_mm": None if r.mean_sigma_mm is None else float(r.mean_sigma_mm),
               "correlated_points": None if r.correlated_points is None else float(r.correlated_points),  # None for a hole
               "warnings": [str(w) for w in r.warnings], "version": v}
        if with_extent:
            out["extent"] = r.extent
        if with_form:
            out["form"] = r.form
        return out

    # ------------------------------------------------------------------ wall thickness (ENGINE_PLAN X16)
    def _wall_inputs(self, sp: ScanProcess) -> tuple[dict | None, str | None]:
        """What the wall-thickness reading needs for this scan, or why it is not offered: ``(inputs, why)``,
        ``inputs`` = ``{calib, depth_range, trajectory}``; the setting, the scan's tracking mode, a merge, the
        recording's calibration, the depth range in the report and the base stage's camera path."""
        from ohs.measure.wall import MARKERS_WORDS

        if not bool(getattr(self.st.cfg.process, "wall_reading", False)):
            return None, ("The wall-thickness reading is off in the settings ([process] wall_reading): it is a "
                          "trial while scans place thin walls' sides slightly too far apart.")
        entry = self.st.projects.scan_entry(sp.pid, sp.sid)
        if entry.get("mode") == "markers":
            return None, MARKERS_WORDS
        if not images_on_gpu():
            return None, ("Not offered without a graphics card: there the camera frames' depth is coarser, and on "
                          "simulated walls the reading was no nearer the truth than the mesh.")
        rep = _read_json(sp.result_dir / "report.json") or {}
        if entry.get("merged_from") or rep.get("merged"):
            return None, "Not offered for a merged scan: read the wall on one of the scans it was made from."
        return self._frames_inputs(sp, rep)

    def _frames_inputs(self, sp: ScanProcess, rep: dict) -> tuple[dict | None, str | None]:
        """What a reading from the scan's camera frames needs (the wall's, the hole's): ``({calib, depth_range,
        trajectory}, None)``, or ``(None, why)``: the recording's calibration, the depth range in the report and the
        base stage's camera path."""
        from ohs.types import ScannerCalibration

        if rep.get("calibration") not in (None, "recording"):
            return None, "Not offered: the scan was made with another calibration than its recording's."
        cal_path = sp.recording_dir / "calibration.json"
        try:
            cal = ScannerCalibration.load(cal_path) if cal_path.is_file() else None
        except (OSError, ValueError, KeyError, TypeError):
            cal = None
        if cal is None or not (sp.recording_dir / "frames").is_dir():
            return None, "Not offered: the scan's recording or its calibration is no longer here."
        d = (rep.get("params") or {}).get("depth") or {}
        lo, hi = d.get("min_depth_mm"), d.get("max_depth_mm")
        if not (isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and 0 < lo < hi):
            return None, "Not offered: the scan's report does not say the depth range it measured."
        with sp.lock:
            stage = str((sp.state.get("base") or {}).get("stage") or "track")
        traj = next((q for q in (sp.dir / stage / "trajectory.csv", sp.result_dir / "trajectory.csv",
                                 sp.dir / "track" / "trajectory.csv") if q.is_file()), None)
        if traj is None:
            return None, "Not offered: the scan's camera path is not here."
        return {"calib": cal, "depth_range": (float(lo), float(hi)), "trajectory": traj}, None

    def wall_available(self, pid: str, sid: str) -> dict:
        """``GET .../wall``: whether the wall-thickness reading is offered for this scan, and why not."""
        sp = self.scan(pid, sid)
        self._need_base(sp)
        _, why = self._wall_inputs(sp)
        return {"available": why is None, "why": why}

    def wall(self, pid: str, sid: str, face: int, point: Any, version: int | None = None) -> dict:
        """``POST .../wall``: a thin wall's thickness read from the scan's camera frames at a click
        (:func:`ohs.measure.wall.read_wall_thickness`): one-sided ("at most"), with the mesh's own reading beside it.
        Read-only. The matcher runs again on the 6 to 30 frames that see the clicked wall (on the graphics card
        when there is one); one reading at a time."""
        from ohs.measure.wall import WallRefused, read_wall_thickness

        sp = self.scan(pid, sid)
        self._need_base(sp)
        inputs, why = self._wall_inputs(sp)
        if inputs is None:
            raise ApiError(422, f"The wall-thickness reading is not offered for this scan. {why}",
                           "Measure the wall with calipers, or lay the part flat and scan it from above.")
        v, V, F = self._click_mesh(sp, face, version)
        poses = self._frames_poses(inputs)
        with self._wall_lock:  # one reading at a time: the matcher is shared
            fd = self._frames_provider(sp, inputs)
            try:
                r = read_wall_thickness(V, F, point, poses=poses, K=fd.K, image_size=fd.image_size,
                                        depth_range=inputs["depth_range"], depth_of=fd.depth)
            except WallRefused as e:
                raise ApiError(422, plain_reason(e, "No wall thickness could be read where you clicked."),
                               e.hint or "Click on one face of a thin wall whose both sides were scanned.") from None
        out = r.to_dict()
        out.update(version=v, available=True)
        return out

    def _click_mesh(self, sp: ScanProcess, face: int, version: int | None) -> tuple[int, np.ndarray, np.ndarray]:
        """``(version, V, F)`` of the current mesh a click was made on: 409 when it changed since, 422 for a face
        outside it (the fit's arrays, loaded once per version)."""
        with sp.lock:
            v = int(sp.state["version"])
            if version is not None and int(version) != v:
                raise ApiError(409, "The mesh changed since this click.", "Reload the mesh and click again.")
            cached = sp._fit_mesh
            if cached is None or cached[0] != v:
                V, F, S, Fl = self._load_step(sp, int(sp.state["cursor"]))
                cached = sp._fit_mesh = (v, V, F, S, Fl)
        _, V, F, _, _ = cached
        if not 0 <= int(face) < len(F):
            raise ApiError(422, "The click is not on the current mesh.", "Reload the mesh and click again.")
        return v, V, F

    @staticmethod
    def _frames_poses(inputs: dict) -> dict[int, np.ndarray]:
        """The scan's final camera path (frame index -> ``T_wc`` of the rectified left camera), the tracked frames."""
        try:
            rows = read_trajectory(inputs["trajectory"])
        except (OSError, ValueError):
            raise ApiError(422, "The scan's camera path cannot be read.", "Make the mesh again.") from None
        return {int(i): np.asarray(T, dtype=np.float64) for i, _, ok, T in rows
                if ok and np.all(np.isfinite(np.asarray(T, dtype=np.float64)))}

    def _frames_provider(self, sp: ScanProcess, inputs: dict) -> Any:
        """The recording's :class:`~ohs.measure.frame_depth.FrameDepth`, one kept (the last recording read). Call
        with ``_wall_lock`` held: the matcher is shared."""
        from ohs.measure.frame_depth import FrameDepth

        key = (str(sp.recording_dir), inputs["depth_range"])
        if self._frame_depth is None or self._frame_depth[0] != key:
            self._frame_depth = (key, FrameDepth(sp.recording_dir, inputs["calib"], inputs["depth_range"]))
        return self._frame_depth[1]

    # ------------------------------------------------------------------ the hole from the camera images (X15 step 2)
    def _hole_inputs(self, sp: ScanProcess) -> tuple[dict | None, str | None]:
        """What the hole reading from the camera images needs for this scan, or why it is not offered: ``(inputs,
        why)``, ``inputs`` as :meth:`_frames_inputs` gives them plus ``path_warns``; the setting, a merge, the scan's
        tracking mode, a graphics card (:data:`HOLE_IMAGES_ON_CPU`), the quality it was made at, the recording's
        calibration and size (:data:`HOLE_IMAGES_MIN_WIDTH`), the depth range in the report, the camera path."""
        from ohs.server.projects import path_check_short

        if not bool(getattr(self.st.cfg.process, "hole_from_images", False)):
            return None, "The hole reading from the camera images is off in the settings ([process] hole_from_images)."
        entry = self.st.projects.scan_entry(sp.pid, sp.sid)
        rep = _read_json(sp.result_dir / "report.json") or {}
        if entry.get("merged_from") or rep.get("merged"):
            return None, "Not offered for a merged scan: read the hole on one of the scans it was made from."
        if entry.get("mode") == "markers":
            return None, ("Not offered for a scan tracked by its markers: the reading has not been checked on such "
                          "scans yet.")
        if not HOLE_IMAGES_ON_CPU and not images_on_gpu():
            return None, ("Not offered without a graphics card: the reading from the camera images has not been "
                          "checked without one yet.")
        with sp.lock:
            quality = quality_or_none(((sp.state.get("stages") or {}).get("track") or {}).get("quality")
                                      or rep.get("quality"))
        if quality == "preview":
            return None, ("Not offered for a scan made at Preview quality: the reading was checked on scans made at "
                          "Standard quality. Make the mesh at Standard quality to use it.")
        inputs, why = self._frames_inputs(sp, rep)
        if inputs is None:
            return None, why
        w = int(inputs["calib"].stereo.image_size[0])
        if w < HOLE_IMAGES_MIN_WIDTH:
            return None, (f"Not offered for this recording's {w} pixel wide images: the reading was checked only on "
                          f"recordings {HOLE_IMAGES_MIN_WIDTH} pixels wide.")
        inputs["path_warns"] = (path_check_short(rep) or {}).get("state") == "warn"
        return inputs, None

    def hole_images_available(self, pid: str, sid: str) -> dict:
        """``GET .../hole-images``: whether the hole reading from the camera images is offered for this scan, and why
        not."""
        sp = self.scan(pid, sid)
        self._need_base(sp)
        _, why = self._hole_inputs(sp)
        return {"available": why is None, "why": why}

    def hole_images(self, pid: str, sid: str, face: int, point: Any, version: int | None = None) -> dict:
        """``POST .../hole-images``: the diameter of a hole's entrance rim read from the scan's camera images at a
        click on the face beside it (:func:`ohs.measure.rim.read_hole`), beside the mesh's own Hole answer. Read-only;
        5 to 15 s (the matcher runs again on up to 20 frames, kept for the next click); one reading at a time. A
        refusal is a 422 with the engine's words as the detail, what to do as the hint, no diameter."""
        from ohs.measure import rim as RIM

        sp = self.scan(pid, sid)
        self._need_base(sp)
        inputs, why = self._hole_inputs(sp)
        if inputs is None:
            raise ApiError(422, f"The hole reading from the camera images is not offered for this scan. {why}",
                           "The mesh's own answer stands; for the hole's true size use calipers or a pin gauge.")
        v, V, F = self._click_mesh(sp, face, version)
        click = np.asarray(point, dtype=np.float64).reshape(3)
        if not np.all(np.isfinite(click)):
            raise ApiError(422, "The click is not on the current mesh.", "Reload the mesh and click again.")
        normal = face_normal_at(V, F, int(face), click)
        poses = self._frames_poses(inputs)
        t0 = time.perf_counter()
        with self._wall_lock:  # one reading at a time: the matcher is shared with the wall reading
            fd = self._frames_provider(sp, inputs)
            res = RIM.read_hole(click, normal, poses=poses, calib=inputs["calib"],
                                images=lambda i: fd.images(i) if fd.has(i) else None,
                                depth=lambda i: fd.depth_and_sigma(i) if fd.has(i) else None,
                                path_warns=bool(inputs["path_warns"]))
        seconds = round(time.perf_counter() - t0, 1)
        if res.refused or res.diameter_mm is None or res.pm_mm is None or res.centre is None:
            refused = res.refused or "no diameter could be read from the camera images here"
            raise ApiError(422, plain_reason(refused, "No diameter could be read from the camera images here."),
                           RIM.refusal_hint(refused),
                           extra={"refused": True, "frames_used": int(res.frames_used),
                                  "frames_tried": int(res.frames), "seconds": seconds})
        return {"available": True, "diameter_mm": float(res.diameter_mm), "pm_mm": float(res.pm_mm),
                "centre": [float(x) for x in res.centre],
                "normal": [float(x) for x in (res.normal if res.normal is not None else normal)],
                "frames_used": int(res.frames_used), "frames_tried": int(res.frames),
                "cover_deg": None if res.covered_deg is None else float(res.covered_deg),
                "warnings": [sentence(w) for w in res.warnings], "words": RIM.reading_words(res),
                "seconds": seconds, "version": v}

    def deviation(self, pid: str, sid: str) -> tuple[dict, np.ndarray]:
        """The deviation map of the current mesh: how far each vertex lies from the scan's base mesh
        (the last stage's result, before any edit), signed, in mm (:func:`ohs.mesh.surface_offset`;
        NaN for vertices that only filled faces use). Returns the answer of ``deviation.json`` and
        the float32 array of ``deviation.bin`` (one value per vertex of ``result/mesh.ply``, in its
        order). Made once per mesh version."""
        from ohs.mesh import offset_stats, surface_offset

        sp = self.scan(pid, sid)
        self._need_base(sp)
        with sp._offset_lock:  # a second request waits for the first one's result
            with sp.lock:
                v, k = int(sp.state["version"]), int(sp.state["cursor"])
                base = dict(sp.state["base"])
                cached = sp._offset
            if cached is not None and cached[0] == v:
                return cached[2], cached[1]
            V, F, _, Fl = self._load_step(sp, k)
            if k == 0:  # no edits: the mesh is its own reference
                off = np.zeros(len(V), dtype=np.float32)
            else:
                Vr, Fr, _, _ = self._load_step(sp, 0)
                filled = Fl if Fl is not None and len(Fl) == len(F) else np.zeros(len(F), dtype=bool)
                off = surface_offset(V, Vr, Fr, F=F, filled=filled)
            info = {"version": v, "vertices": int(len(V)), "edits": k,
                    "reference": {"stage": base["stage"], "triangles": int(base["triangles"]),
                                  "vertices": int(base["vertices"])},
                    "stats": offset_stats(off)}
            with sp.lock:
                if int(sp.state["version"]) == v:  # not changed meanwhile
                    sp._offset = (v, off, info)
            return info, off

    def lasers(self, pid: str, sid: str) -> tuple[dict, np.ndarray]:
        """The laser agreement map of the current mesh (:func:`ohs.mesh.laser_agreement`): for each
        vertex, how far the laser points near it lie from the mesh, signed, in mm; NaN where no laser
        point came near. The laser points are the base's (``result/laser_points.ply``, in the mesh's
        frame). Returns the answer of ``lasers.json`` and the float32 array of ``lasers.bin`` (one
        value per vertex of ``result/mesh.ply``, in its order). ``available`` is False, and every
        value NaN, when the scan has no laser points. Made once per mesh version."""
        from ohs.mesh import laser_agreement
        from ohs.track.export import load_ply

        sp = self.scan(pid, sid)
        self._need_base(sp)
        with sp._lasers_lock:  # a second request waits for the first one's result
            with sp.lock:
                v, k = int(sp.state["version"]), int(sp.state["cursor"])
                cached = sp._lasers
            if cached is not None and cached[0] == v:
                return cached[2], cached[1]
            V, F, _, _ = self._load_step(sp, k)
            lp = sp.result_dir / "laser_points.ply"
            P = load_ply(lp)["vertices"] if lp.is_file() else np.zeros((0, 3))
            val, rep = laser_agreement(V, F, P)
            info = {"version": v, "vertices": int(len(V)), "available": bool(len(P) >= MIN_LASER_POINTS), **rep}
            with sp.lock:
                if int(sp.state["version"]) == v:  # not changed meanwhile
                    sp._lasers = (v, val, info)
            return info, val

    def check(self, pid: str, sid: str, tolerance_mm: float) -> dict:
        """``POST .../check``: the areas of the current mesh that miss ``tolerance_mm``
        (:func:`ohs.mesh.weak_areas`: 2 sigma above the tolerance, or surface that hole filling made
        up), largest first, with the share of the surface that meets it. Read-only; uses the arrays
        the fits keep per mesh version."""
        from ohs.mesh import weak_areas

        sp = self.scan(pid, sid)
        self._need_base(sp)
        with sp.lock:
            v = int(sp.state["version"])
            cached = sp._fit_mesh
            if cached is None or cached[0] != v:
                V, F, S, Fl = self._load_step(sp, int(sp.state["cursor"]))
                cached = sp._fit_mesh = (v, V, F, S, Fl)
        _, V, F, S, Fl = cached
        try:
            # why=False: the reasons and poses (FEATURE_PLAN N1) reach this route in their own step
            r = weak_areas(V, F, S, Fl if Fl is not None and len(Fl) == len(F) else None, tolerance_mm=float(tolerance_mm),
                           why=False)
            r.pop("reasons", None)
        except ValueError as e:
            raise ApiError(422, plain_reason(e, "The Scan check cannot be worked out at this tolerance."),
                           "Give the tolerance in mm, for example 0.1.") from None
        # kept (the areas with their faces as arrays) for the reasons route, which explains these areas
        sp._check = (v, float(tolerance_mm), {**r, "areas": [dict(a) for a in r["areas"]]})
        for a in r["areas"]:
            faces = np.asarray(a["faces"], dtype="<u4")
            a["faces"] = base64.b64encode(faces.tobytes()).decode("ascii")
            a["center"] = [float(x) for x in a["center"]]
        r["version"] = v
        return r

    def check_why(self, pid: str, sid: str, tolerance_mm: float, version: int | None = None) -> dict:
        """``POST .../check/why`` (FEATURE_PLAN N1): why each area of the Check answer at ``tolerance_mm``
        is weak (:func:`ohs.mesh.check.add_reasons`), the pose to scan it from or why there is none
        (:func:`ohs.guide.views.suggest_views`) and a short plan of up to :data:`PLAN_POSES` poses
        (:func:`ohs.guide.views.plan_views`). The areas are those ``POST .../check`` gives for the same
        tolerance and mesh version, in the same order. Read-only. The camera path is the scan's
        (:meth:`_scan_views`); what is not there makes the reasons that need it "unknown", in words.
        Made once per mesh version and tolerance; the parts every check of a mesh version shares (the
        ray-casting scene, the open border, the curvature term) are kept for the next tolerance.
        ``version``: the mesh version of the answer on screen (409 when it changed)."""
        from ohs.guide.views import plan_views, suggest_views
        from ohs.mesh.check import CheckMesh, add_reasons, weak_areas

        t0 = time.perf_counter()
        tol = float(tolerance_mm)
        if not math.isfinite(tol) or tol <= 0:
            raise ApiError(422, "The tolerance must be a number of mm above 0.", "Give the tolerance in mm, for "
                                                                                 "example 0.1.")
        sp = self.scan(pid, sid)
        self._need_base(sp)
        v, V, F, S, Fl = self.mesh_arrays(pid, sid)
        if version is not None and int(version) != v:
            raise ApiError(409, "The mesh changed since the page checked it.", "Reload the mesh and check again.")
        Fl = Fl if Fl is not None and len(Fl) == len(F) else None
        with sp._why_lock:  # a second request waits for the first one's answer
            got = sp._why
            if got is not None and got[0] == v and got[1] == tol:
                return got[2]
            wm = sp._why_mesh
            if wm is None or wm[0] != v:
                with sp.lock:
                    k = int(sp.state["cursor"])
                    same = int(sp.state["version"]) == v
                L = self.step_looks(sp, k) if same else None
                if L is not None and len(L) != len(V):
                    L = None
                wm = sp._why_mesh = (v, CheckMesh(V, F), L)
            _, ctx, L = wm
            views, cam = self._scan_views(sp)
            voxel = self._voxel_mm(sp)
            try:
                ctx.prepare(voxel, scene=views is not None)  # the shared parts, side by side
                kept = sp._check
                base = (kept[2] if kept is not None and kept[0] == v and kept[1] == tol
                        else weak_areas(V, F, S, Fl, tolerance_mm=tol, why=False))
                r = add_reasons(V, F, S, Fl, base, looks=L, views=views, voxel_mm=voxel, context=ctx)
                sugg = suggest_views(V, F, r, views[0], context=ctx) if views else None
                plan = plan_views(V, F, r, views[0], PLAN_POSES, suggestions=sugg, context=ctx) if views else []
            except ValueError as e:
                raise ApiError(422, plain_reason(e, "The reasons cannot be worked out at this tolerance."),
                               "Give the tolerance in mm, for example 0.1.") from None
            answer = self._why_answer(r, sugg, plan, cam, v, tol)
            answer["ms"] = int(round(1e3 * (time.perf_counter() - t0)))
            answer = jsonable(answer)
            with sp.lock:
                if int(sp.state["version"]) == v:  # not changed meanwhile
                    sp._why = (v, tol, answer)
            return answer

    @staticmethod
    def _why_answer(r: dict, sugg: list[dict] | None, plan: list[dict], cam: dict, v: int, tol: float) -> dict:
        """The reasons route's answer from the engine's (module docstring of :mod:`ohs.mesh.check`)."""
        no_path = "steep, far: the scan's camera path is not given"
        areas = []
        for i, a in enumerate(r["areas"]):
            unknown = [f"steep, far: {cam['why']}" if u == no_path and cam.get("why") else u for u in a["unknown_because"]]
            if sugg is not None:
                s = sugg[i]
                view = {key: s.get(key) for key in ("ok", "pose", "share_seen", "border_seen", "candidates", "missed",
                                                    "reason", "text")}
            else:
                why = cam.get("why") or "the scan's camera path is not here"
                view = {"ok": False, "pose": None, "share_seen": None, "border_seen": None, "candidates": 0,
                        "missed": None, "reason": f"no place to scan from: {why}", "text": f"No place to scan from: {why}."}
            areas.append({"index": i, "kind": a["kind"], "area_mm2": a["area_mm2"], "n_faces": a["n_faces"],
                          "center": [float(x) for x in a["center"]], "normal": a["normal"], "reason": a["reason"],
                          "reasons": a["reasons"], "reason_text": a["reason_text"], "evidence": a["evidence"],
                          "unknown_because": unknown, "view": view})
        given = dict(r.get("reasons") or {})
        cam = {**cam, "frames_total": given.pop("frames_total", 0), "frames_used": given.pop("frames_used", 0),
               "frames_cap": given.pop("frames_cap", None)}
        return {"version": v, "tolerance_mm": tol, "n_areas": int(r["n_areas"]), "areas": areas,
                "plan": [{"area": int(p["area"]), "kind": p["kind"], "pose": p["pose"], "share_seen": p["share_seen"],
                          "border_seen": p["border_seen"],
                          "also_sees": [{"area": int(a), "share": float(s)} for a, s in sorted(p["also_sees"].items())]}
                         for p in plan],
                "given": given, "camera_path": cam}

    def _voxel_mm(self, sp: ScanProcess) -> float | None:
        """The voxel size the current base mesh was made at (its ``report.json``, ``mesh.voxel_mm``)."""
        rep = _read_json(sp.result_dir / "report.json") or {}
        vox = (rep.get("mesh") or {}).get("voxel_mm")
        return float(vox) if isinstance(vox, (int, float)) and vox > 0 else None

    def _scan_views(self, sp: ScanProcess) -> tuple[list | None, dict]:
        """Where the scan's frames looked from (:class:`ohs.mesh.check.ScanViews`, one per scan of a merge)
        and what that rests on: ``(views or None, {"available", "why", "scans": [{name, frames, trajectory}]})``.
        A scan: the trajectory of its base stage (``process/<stage>/trajectory.csv``, else the copy in
        ``result/``, else the track stage's), the calibration in its recording and the depth range in its
        report. A merge: each scan it was made from, by the merged scan's report (its ``T``) and that
        scan's own files; when one of them is missing or has changed since the merge, none is used
        (part of the camera path would make the rest look steep or far)."""
        from ohs.mesh.check import ScanViews
        from ohs.types import ScannerCalibration

        entry = self.st.projects.scan_entry(sp.pid, sp.sid)
        rep = _read_json(sp.result_dir / "report.json") or {}

        def none(why: str) -> tuple[None, dict]:
            return None, {"available": False, "why": why, "scans": []}

        def depth_range(report: dict) -> tuple[float, float] | None:
            d = (report.get("params") or {}).get("depth") or {}
            lo, hi = d.get("min_depth_mm"), d.get("max_depth_mm")
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and 0 < lo < hi:
                return float(lo), float(hi)
            return None

        def calibration(path: Path) -> Any:
            try:
                return ScannerCalibration.load(path) if path.is_file() else None
            except (OSError, ValueError, KeyError, TypeError) as e:
                log.warning("calibration %s unreadable: %s", path, e)
                return None

        def frames(rows: list) -> int:
            return sum(1 for r in rows if r[2] and np.all(np.isfinite(np.asarray(r[3], dtype=np.float64))))

        merged = entry.get("merged_from") or ([] if not rep.get("merged") else None)
        if merged is not None and (merged or rep.get("merged")):
            inputs = rep.get("scans") or []
            if not merged or len(inputs) != len(merged):
                return none("the merged scan's report does not say which scans it was made from")
            views, scans = [], []
            for src, inp in zip(merged, inputs):
                name = str(src.get("name") or inp.get("name") or src.get("id"))
                try:
                    sd = self.st.projects.scan_dir(sp.pid, str(src["id"]))
                except (ApiError, KeyError):
                    sd = None
                if sd is None or not sd.is_dir():
                    return none(f"the scan \"{name}\" it was made from is no longer in the project")
                traj = sd / "result" / "trajectory.csv"
                if not traj.is_file():
                    return none(f"the camera path of \"{name}\", one of the scans it was made from, is not here")
                cal = calibration(sd / "recording" / "calibration.json")
                if cal is None and inp.get("recording"):
                    cal = calibration(Path(str(inp["recording"])) / "calibration.json")
                if cal is None:
                    return none(f"the calibration of \"{name}\" went with its recording")
                rng = depth_range(_read_json(sd / "result" / "report.json") or {})
                if rng is None:
                    return none(f"the report of \"{name}\" does not say the depth range it measured")
                try:
                    rows = read_trajectory(traj)
                except (OSError, ValueError) as e:
                    log.warning("the camera path %s cannot be read: %s", traj, e)
                    return none(f"the camera path of \"{name}\" cannot be read")
                n = frames(rows)
                if isinstance(inp.get("frames"), int) and n != int(inp["frames"]):
                    return none(f"the camera path of \"{name}\" changed since the merge ({n:,} frames now, "
                                f"{int(inp['frames']):,} then)")
                T = np.asarray(inp.get("T", np.eye(4)), dtype=np.float64).reshape(4, 4)
                views.append(ScanViews.from_calibration(rows, cal, rng, T=T))
                scans.append({"name": name, "frames": n, "trajectory": str(traj)})
            return views, {"available": True, "why": None, "scans": scans}
        if rep.get("calibration") not in (None, "recording"):
            return none("the scan was made with another calibration than its recording's")
        cal = calibration(sp.recording_dir / "calibration.json")
        if cal is None:
            return none("the scanner's calibration is not here (it goes with the recording, which was removed)")
        rng = depth_range(rep)
        if rng is None:
            return none("the scan's report does not say the depth range it measured")
        with sp.lock:
            stage = str((sp.state.get("base") or {}).get("stage") or "track")
        traj = next((p for p in (sp.dir / stage / "trajectory.csv", sp.result_dir / "trajectory.csv",
                                 sp.dir / "track" / "trajectory.csv") if p.is_file()), None)
        if traj is None:
            return none("the scan's camera path is not here")
        try:
            rows = read_trajectory(traj)
        except (OSError, ValueError) as e:
            log.warning("the camera path %s cannot be read: %s", traj, e)
            return none("the scan's camera path cannot be read")
        if not frames(rows):
            return none("the scan's camera path has no tracked frame")
        return [ScanViews.from_calibration(rows, cal, rng)], {
            "available": True, "why": None, "scans": [{"name": str(entry.get("name") or sp.sid), "frames": frames(rows),
                                                      "trajectory": str(traj)}]}

    def trajectory(self, pid: str, sid: str) -> dict:
        sp = self.scan(pid, sid)
        path = sp.dir / "track" / "trajectory.csv"
        if not path.is_file():  # the track stage's files were removed to free space (R2): the result's path
            path = sp.result_dir / "trajectory.csv"
        if not path.is_file():
            raise ApiError(404, "This scan has no camera path yet.", "Press Make mesh first (or run “Follow the camera”).")
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
