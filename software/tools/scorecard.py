"""The scorecard: one command, one table, the same scenes and the same scoring every time (ENGINE_PLAN X0).

    python tools/scorecard.py run [--set quick|core|full] [--scenes G ...] [--seeds N ...] [--compute gpu|cpu]
                                  [--set-param SECTION.FIELD=VALUE ...] [--params FILE.json] [--quiet-pc]
    python tools/scorecard.py baseline --save NAME [same filters]      # run, then keep the result as NAME
    python tools/scorecard.py compare NAME [same filters] [--set-param ...]   # run, then the difference table
    python tools/scorecard.py replay --set-param fusion.X=V [--base-param ...] [--scenes ...]  # paired, fusing only
    python tools/scorecard.py render [same filters]     # only render the recordings (once; never again)
    python tools/scorecard.py table [same filters]      # the table of what is in the cache, nothing run
    python tools/scorecard.py selfcheck [same filters]  # the part-alone placement on the true surface: must be 0
    python tools/scorecard.py holes [--seeds ...]       # the hole's reading, by level, clicks and scan length
    python tools/scorecard.py repeat [same filters]     # each recording processed and scored twice: determinism
    python tools/scorecard.py rescore [same filters]    # score finished jobs again from their kept meshes

Every command but replay, holes and selfcheck also takes ``--chain frame|app`` (below, "Chains").

Restartable: after a kill, run the same command again; it carries on where it stopped. E.g.
    python tools/scorecard.py baseline --set core --save today-gpu --render-lanes 3 --cache DIR

(run from ``software/``; the cache folder is ``--cache``, by default ``%OHS_SCORECARD%`` or a
folder in the temp directory.) Every number is judged against the simulator's truth.

**Scenes** (:data:`SCENES`): each a named recording, rendered once into ``<cache>/recs/<name>``
by the builder that made it for ``VALIDATION.md`` (its docstring there says which), and never
rendered again. ``--set quick`` (a few minutes) is the end-to-end sphere and the 0.5 mm plate;
``core`` (what an experiment needs by default) adds the end-to-end car panel, the 200-frame car
panels, the 240-frame brackets, the sheet part, the standing walls, the plates with holes, the six
dark and speckled recordings and the three panel seams; ``full`` adds three stretches each of the
door and the carburettor (their models and recordings in ``~/.ohs``; skipped with a line if absent).

**Each recording** is processed as the app's Make mesh (``ohs.server.studio.process_params`` with
the default ``Config()``, Standard quality, markerless) twice: tracked, and fused at the true
poses. Each mesh then gets the built-in Default recipe (remove floaters, fill holes to 5 mm), on
the mesh renumbered by position (``ohs.mesh.order``), and is scored:

* the camera path against the truth (median, max and last frame; position and rotation);
* **the part alone** (:func:`place_part`): the scan is put into the simulator's world at its first
  tracked frame, then moved by a rigid best fit of the part's own points onto the part's own
  surface. A point belongs to the part when the nearest true surface is the part's (not the table
  or floor); its target is the nearest point of the part (the table is never a target). The
  sample is taken by position, never by vertex number. Then for the part's vertices: median and
  95 % of the distance to the part, the share more than 0.3 mm off, the signed mean (+ outside;
  within 3 mm), and the coverage (the share of the part's surface that the processed frames saw
  from their true poses, within 1 mm and 0.5 mm of the mesh);
* the fits the scene has (:func:`scene_fits`): hole diameter and centre, boss and shaft radius,
  the upright face's offset, wall and plate thickness, gap and flush along a seam;
* the laser lines against the mesh per line (the report's health block), the mesh's open border
  loops, frames tracked and lost, frame time, wall time and the peak memory of the job's process
  (``tools/memory_peaks.py``): rough, unless ``--quiet-pc``.

A number that could not be measured is ``None`` ("n/m" in a table), never 0. The range of a number
over a scene's seeds is that scene's yardstick ("spread").

**Chains** (``--chain``; ENGINE_PLAN X0a). ``frame`` (the default, and every baseline saved before
2026-11-21) is the frame pass only: ``process_recording`` with the parameters above, then the recipe.
``app`` (:func:`app_chain`) runs what the app's Make mesh runs before its recipe, calling the same
engine functions with the server's own parameters and decisions (``ohs.server.processing``:
``stage_params``, ``straighten_looped``, ``refine_changes``): Follow the camera (every piece kept,
as the server's stage), Straighten (``ohs.track.posegraph.straighten_trajectory``, a fuse at the
loops' poses when they change them, ``ohs.track.curve_ba.laser_curve_adjustment`` against that
mesh, a fuse at its poses when it changes them), Refine with lasers (``ohs.mesh.anchor_to_points``)
on the stage's mesh; then the same recipe and scoring. At true poses there is no tracked path to
straighten: the fuse at the true poses and Refine with lasers. A result also says what each stage
did (``info["chain"]``; the table's "What the app chain's stages did"), and so does an app
baseline (``stages``). Its runs have their own folders (``app-default``, ``app-p-<hash>``);
``curve.<CurveParams field>=V`` tries a setting of the laser crossings (app chain only; a
true-pose run is the same with it and keeps one folder). ``process.refine_from_images=true`` (the app's
``[process] refine_from_images``, ENGINE_PLAN X6, off by default) runs "Refine from the images" between Straighten and
Refine with lasers (``ohs.server.processing.spacetime_refine``, as Make mesh runs it), and ``spacetime.<SpacetimeParams
field>=V`` tries one of its parameters (app chain only).

**Compare** pairs each recording with itself in the baseline: the change, the scene's spread in
the baseline, and a verdict per row: "better" or "worse" when the change of the scene's mean is
larger than the spread (and every seed moved the same way), "worse" also when one recording alone
got worse by more than the whole spread, else "same" ("identical" when nothing moved at all); a
scene with one recording has "no spread to judge by" (it neither passes nor fails). Thin parts
(the standing walls, the 0.5 mm plate, the sheet's flange and ribs) and small features (the holes,
the bosses, the shaft, the sphere's radius) are printed first in their own block, whatever their
verdict, and are judged on each recording alone: 0.05 mm thicker or further off, a face lost or
a click no longer answered on any one recording is an alarm. Then one verdict per scene (worse /
no spread to judge by / better / same / identical), and the last line: "better or the same on
every scene", or the scenes that are worse and why.

**Surviving a kill**: a recording is used only when complete (:func:`recording_complete`: its
marker, written last, every planned frame there and whole); one cut off is rendered again. A
result is written whole and last (:func:`write_json`), so one that is there is a finished run's.
Running the same command again after a kill carries on where it stopped; ``--render-lanes N``
renders what is missing in N background processes while the rest is processed. A baseline is saved
only when every job of it finished.

**Replay** (a change to fusing alone): each recording's matcher depth is cached once (a run at the
true poses with the depth estimator wrapped, ``<cache>/depth/<name>``), then fused again with and
without the setting at the same poses (true, and today's tracked trajectory), the depth fed from
the cache (:class:`_CachedDepth`). Nothing in the engine is changed: the tool wraps
``ohs.app.pipeline.DepthEstimator`` and ``ohs.app.process.FramePipeline`` in the job's process.
The yardstick is each recording's own difference, with today's range across seeds beside it.

Jobs run one at a time, each in its own process (``_job``), with a deadline. Results are JSON under
``<cache>/runs/<compute>/<settings>/<recording>/<poses>/``; a result that is there is used again.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

SOFTWARE = Path(__file__).resolve().parents[1]
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))

from ohs import geometry as G  # noqa: E402

__all__ = ["SCENES", "SceneSpec", "scenes_for", "PartTruth", "SdfTruth", "MeshTruth", "truth_for", "place_part",
           "part_scores", "verdict", "compare_rows", "fmt", "NM", "parse_params", "apply_params", "main"]

MODELS = Path.home() / ".ohs"
DEFAULT_CACHE = Path(os.environ.get("OHS_SCORECARD", "") or Path(os.environ.get("TEMP", "/tmp")) / "ohs-scorecard")
NM = "n/m"  # what a table prints for a number that could not be measured
NO_SPREAD = "no spread to judge by"  # a scene with one recording: a change there neither passes nor fails
IMAGE_SIZE = (1280, 800)


# =========================================================================== the scenes
@dataclass(frozen=True)
class SceneSpec:
    """One recording of the fixed set: ``name`` (its folder), ``group`` (the scene whose seeds make a
    spread), ``kind`` (how it is judged), ``seed``, ``frames``, the builder's arguments ``build``,
    the sets it is in and whether it is a thin part (printed first in a comparison)."""

    name: str
    group: str
    kind: str
    seed: int
    frames: int
    build: dict = field(default_factory=dict)
    sets: tuple[str, ...] = ("core", "full")
    thin: bool = False
    note: str = ""


def _specs() -> dict[str, SceneSpec]:
    out: list[SceneSpec] = []
    # the end-to-end tests (tests/test_e2e_scan.py: 36 frames at 30 fps, the scene's own hand path)
    out.append(SceneSpec("e2e-sphere", "e2e-sphere", "sphere", 0, 36, {"maker": "scan", "scene": "sphere_on_plane", "fps": 30.0},
                         sets=("quick", "core", "full"), note="test_full_markerless_sphere"))
    out.append(SceneSpec("e2e-panel", "e2e-panel", "car_panel", 0, 36, {"maker": "scan", "scene": "car_panel", "fps": 30.0},
                         note="test_full_markerless_car_panel"))
    # the 60-frame car panels of "Presets" (tools/preset_study.py panel-s<k>): the scene's own pan, not round the hole
    for s in range(3):
        out.append(SceneSpec(f"panel60-s{s}", "panel60", "car_panel", s, 60, {"maker": "preset", "scene": "car_panel", "fps": 30.0},
                             sets=("full",), note="'Presets' (tools/preset_study.py panel-s<k>)"))
    # the Ø 25 hole: 200 frames at 10 fps, a 400° orbit centred on the hole (m5v / holes make_rec.py)
    for s in range(5):
        out.append(SceneSpec(f"panel200-s{s}", "panel200", "car_panel", s, 200, {"maker": "orbit", "scene": "car_panel"},
                             note="'Fitted shapes against the truth', 'The fits' ± on today's engine'"))
    for s in range(5):
        out.append(SceneSpec(f"bracket240-s{s}", "bracket240", "bracket", s, 240, {"maker": "orbit", "scene": "bracket"},
                             note="'Fitted shapes', 'Thin walls seen from opposite sides', 'The gap guard per voxel'"))
    # thin parts
    for s in range(3):
        out.append(SceneSpec(f"sheet-s{s}", "sheet", "sheet", s, 240, {"maker": "sheet"}, thin=True,
                             note="'Thin walls seen from opposite sides' (scratch shape/sheet.py)"))
        out.append(SceneSpec(f"fins-s{s}", "fins", "fins", s, 120, {"maker": "preset", "scene": "fins", "fps": 30.0}, thin=True,
                             note="'Presets' (tools/preset_study.py fins-s<k>)"))
        out.append(SceneSpec(f"plate05-s{s}", "plate05", "thin_plate", s, 60,
                             {"maker": "preset", "scene": "thin_plate", "fps": 30.0, "thickness": 0.5}, thin=True,
                             sets=("quick", "core", "full") if s == 0 else ("core", "full"),
                             note="'Presets' (tools/preset_study.py plate-s<k>)"))
    # the plates with holes Ø 25, 10, 6 and a blind bore, 10 mm ("low") and 120 mm ("high") above the floor
    for gap in ("low", "high"):
        for s in range(3):
            out.append(SceneSpec(f"holes-{gap}-s{s}", f"holes-{gap}", "holes", s, 200, {"maker": "holes", "gap": gap},
                                 note="'Holes filled in', 'The Hole tool on holes with a wall' (scratch holes/plate_scene.py)"))
    # the six dark and speckled recordings of MAINTENANCE_PLAN F14 (tools/calib_health_study.py's specs)
    for name, base, seed in (("dark", "bracket-dark", 2), ("dark2", "bracket-dark", 12), ("speck", "bracket-speckle", 3),
                             ("speck2", "bracket-speckle", 13), ("plate", "plate-0.5mm", 4), ("plate2", "plate-0.5mm", 14)):
        kind = "thin_plate" if base.startswith("plate") else "bracket"
        out.append(SceneSpec(f"hard-{name}", f"hard-{base}", kind, seed, 90, {"maker": "health", "spec": base},
                             note="MAINTENANCE_PLAN F14 (calib_health_study.RECORDINGS)"))
    # the panel seams of 'Gap and flush' (tools/gapflush_study.py CASES, 60 frames): three different
    # seams, so each is its own scene with one recording (no spread: their range would mix shapes)
    for case, seed in (("taper", 0), ("taper_flush2", 1), ("flush_ramp", 2)):
        out.append(SceneSpec(f"seam-{case}", f"seam-{case}", "seam", seed, 60, {"maker": "seam", "case": case},
                             note="'Gap and flush between two panels'"))
    # stretches of the door's and the carburettor's long recordings (tools/preset_study.py door-w<k>, carb-w<k>)
    for k in range(3):
        out.append(SceneSpec(f"door-w{k}", "door", "door", k, 141, {"maker": "stretch", "rec": f"door-w{k}"}, sets=("full",),
                             note="'Presets' (door-w<k>), 'Large and detailed parts'"))
        out.append(SceneSpec(f"carb-w{k}", "carb", "carb", k, 135, {"maker": "stretch", "rec": f"carb-w{k}"}, sets=("full",),
                             note="'Presets' (carb-w<k>), 'Large and detailed parts'"))
    return {s.name: s for s in out}


SCENES: dict[str, SceneSpec] = _specs()
SETS = ("quick", "core", "full")


def scenes_for(set_name: str = "core", groups: list[str] | None = None, seeds: list[int] | None = None,
               names: list[str] | None = None) -> list[SceneSpec]:
    """The recordings of a set, optionally only some scene groups (or names, or name prefixes) and seeds."""
    out = []
    for s in SCENES.values():
        if set_name not in s.sets:
            continue
        if groups and not any(s.group == g or s.name == g or s.name.startswith(g + "-") or s.group.startswith(g)
                              for g in groups):
            continue
        if seeds is not None and s.seed not in seeds:
            continue
        if names and s.name not in names:
            continue
        out.append(s)
    return out


# --------------------------------------------------------------------------- the builders
# The plates with holes (VALIDATION.md "Holes filled in", built in the scratch folder holes/plate_scene.py
# on 2026-11-12 and never put into the repository: reproduced here as it was written).
HP_GAPS = {"low": 10.0, "high": 120.0}
HP_T = 3.0  # mm, the plate's thickness
HP_PX, HP_PY = 80.0, 45.0  # its half extents
HP_HOLES = {"h25": (-45.0, 0.0, 12.5), "h10": (0.0, 0.0, 5.0), "h6": (30.0, 0.0, 3.0)}
HP_BLOCK = (64.0, 0.0, 14.0, 12.0)  # centre x, y, half width, height above the plate's top
HP_BORE = (64.0, 0.0, 5.0, 8.0)  # centre x, y, radius, depth
HP_YAW = math.radians(15.0)


def holes_plate_T(gap: str) -> np.ndarray:
    return G.make_se3(G.so3_exp(np.array([0.0, 0.0, HP_YAW])), [0.0, 0.0, HP_GAPS[gap]])


def holes_plate_scene(gap: str, seed: int = 0):
    """A flat 3 mm plate with through holes Ø 25, 10 and 6 mm and a block with a blind bore (Ø 10, 8
    deep), floating 10 mm ("low") or 120 mm ("high") above a dark floor."""
    from ohs.sim.scene import Cylinder, HeightField, NoiseAlbedo, Rect, Scene, _table

    T = holes_plate_T(gap)
    alb = NoiseAlbedo(mean=0.6, contrast=0.03, scale_mm=2.0, seed=seed + 3)

    def flat(h):
        def f(x, y):
            return np.full_like(x, h), np.zeros_like(x), np.zeros_like(x)
        return f

    def rect_x(x, y0, y1, z0, z1, name):
        R = np.column_stack([[0, 1.0, 0], [0, 0, 1.0], [1.0, 0, 0]])
        return Rect(T @ G.make_se3(R, [x, 0, 0]), (y0, y1, z0, z1), alb, name=name)

    def rect_y(y, x0, x1, z0, z1, name):
        R = np.column_stack([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])
        return Rect(T @ G.make_se3(R, [0, y, 0]), (x0, x1, z0, z1), alb, name=name)

    holes = list(HP_HOLES.values())
    prims = [_table(0.0, 800.0, NoiseAlbedo(mean=0.3, contrast=0.08, scale_mm=5.0, seed=seed + 1), name="floor")]
    b = (-HP_PX, HP_PX, -HP_PY, HP_PY)
    prims.append(HeightField(T, flat(HP_T), b, (HP_T, HP_T), holes, alb, name="plate_top"))
    prims.append(HeightField(T, flat(0.0), b, (0.0, 0.0), holes, alb, name="plate_bottom"))
    for k, (hx, hy, r) in HP_HOLES.items():
        prims.append(Cylinder(T @ G.make_se3(np.eye(3), [hx, hy, HP_T / 2]), r, HP_T / 2, alb, name=f"wall_{k}", caps=False))
    prims += [rect_x(HP_PX, -HP_PY, HP_PY, 0, HP_T, "side_px"), rect_x(-HP_PX, -HP_PY, HP_PY, 0, HP_T, "side_mx"),
              rect_y(HP_PY, -HP_PX, HP_PX, 0, HP_T, "side_py"), rect_y(-HP_PY, -HP_PX, HP_PX, 0, HP_T, "side_my")]
    bx, by, bh, bz = HP_BLOCK
    zt = HP_T + bz
    cx, cy, cr, cd = HP_BORE
    prims.append(HeightField(T, flat(zt), (bx - bh, bx + bh, by - bh, by + bh), (zt, zt), [(cx, cy, cr)], alb, name="block_top"))
    prims += [rect_x(bx + bh, by - bh, by + bh, HP_T, zt, "block_px"), rect_x(bx - bh, by - bh, by + bh, HP_T, zt, "block_mx"),
              rect_y(by + bh, bx - bh, bx + bh, HP_T, zt, "block_py"), rect_y(by - bh, bx - bh, bx + bh, HP_T, zt, "block_my")]
    prims.append(Cylinder(T @ G.make_se3(np.eye(3), [cx, cy, zt - cd / 2]), cr, cd / 2, alb, name="bore_wall", caps=False))
    prims.append(Rect(T @ G.make_se3(np.eye(3), [cx, cy, zt - cd]), (-cr - 0.5, cr + 0.5, -cr - 0.5, cr + 0.5), alb,
                      name="bore_bottom"))
    target = (T @ np.array([-10.0, 0.0, HP_T, 1.0]))[:3]
    return Scene(prims, name=f"plate_{gap}", target=target, radius=100.0, meta={"plate_T": T.tolist(), "gap": gap})


def _box_sd(L: np.ndarray, c, h) -> np.ndarray:
    q = np.abs(L - np.asarray(c, dtype=np.float64)) - np.asarray(h, dtype=np.float64)
    return np.linalg.norm(np.maximum(q, 0.0), axis=1) + np.minimum(q.max(axis=1), 0.0)


def holes_plate_sdf(gap: str, X: np.ndarray) -> np.ndarray:
    """(N, 3) signed distances (mm, + outside) of the plate, the block (with its bore) and the floor
    at world points (exact on the faces; near the holes' rims outside, an upper bound)."""
    T = holes_plate_T(gap)
    X = np.asarray(X, dtype=np.float64).reshape(-1, 3)
    L = (X - T[:3, 3]) @ T[:3, :3]
    sd_p = _box_sd(L, (0, 0, HP_T / 2), (HP_PX, HP_PY, HP_T / 2))
    for hx, hy, r in HP_HOLES.values():
        sd_p = np.maximum(sd_p, -(np.hypot(L[:, 0] - hx, L[:, 1] - hy) - r))
    bx, by, bh, bz = HP_BLOCK
    sd_b = _box_sd(L, (bx, by, HP_T + bz / 2), (bh, bh, bz / 2))
    cx, cy, cr, cd = HP_BORE
    bore = np.maximum(np.hypot(L[:, 0] - cx, L[:, 1] - cy) - cr, (HP_T + bz - cd) - L[:, 2])
    sd_b = np.maximum(sd_b, -bore)
    return np.column_stack([sd_p, sd_b, X[:, 2]])


# The sheet part (VALIDATION.md "Thin walls seen from opposite sides", scratch shape/sheet.py of 2026-11-14:
# reproduced as written): a 2 mm foot on the table, a 2 mm flange and two ribs (1 mm and 3 mm)
SHEET_WALLS = {"flange_2mm": ((0.0, 0.0, 27.0), (40.0, 1.0, 25.0), 1),
               "rib_1mm": ((25.0, 18.0, 14.0), (0.5, 14.0, 12.0), 0),
               "rib_3mm": ((-25.0, 18.0, 14.0), (1.5, 14.0, 12.0), 0)}
SHEET_FOOT = ((0.0, 15.0, 1.0), (40.0, 20.0, 1.0))
SHEET_TARGET = (0.0, 8.0, 20.0)


def sheet_scene(seed: int = 0):
    from ohs.sim.scene import Box, NoiseAlbedo, Scene, _table

    metal = NoiseAlbedo(mean=0.6, contrast=0.03, scale_mm=2.0, seed=seed + 3)
    prims = [_table(0.0, 600.0, NoiseAlbedo(mean=0.45, contrast=0.06, scale_mm=4.0, seed=seed), name="table"),
             Box(G.make_se3(np.eye(3), SHEET_FOOT[0]), SHEET_FOOT[1], metal, name="foot")]
    for name, (c, h, _) in SHEET_WALLS.items():
        prims.append(Box(G.make_se3(np.eye(3), c), h, metal, name=name))
    return Scene(prims, name="sheet", target=SHEET_TARGET, radius=60.0,
                 meta={"trajectory": {"distance": (200.0, 280.0), "elevation_deg": (35.0, 65.0), "sweep_deg_s": 22.0}})


def panel_hole_world(sc) -> dict:
    """The car panel's hole: the nominal centre, and the circle an exact fit gives on the true edge
    in the plane of the true ring round it (the Hole tool's model; the edge is an ellipse there,
    the panel being tilted about 10° to the cut): Ø 25.20 for the Ø 25 cut."""
    from ohs.measure.fit import fit_circle, fit_plane

    panel = sc.primitives[sc.object_id("panel")]
    hx0, hy0, r0 = panel.holes[0]

    def world(x, y):
        z = panel.height(np.asarray(x, float), np.asarray(y, float))[0]
        return G.transform_points(panel.T_wo, np.column_stack([np.ravel(x), np.ravel(y), np.ravel(z)]))

    th = np.linspace(0, 2 * math.pi, 1440, endpoint=False)
    E = world(hx0 + r0 * np.cos(th), hy0 + r0 * np.sin(th))
    rr, tt = np.meshgrid(np.linspace(r0, r0 + 3.0, 13), th[::4])
    Rg = world(hx0 + rr.ravel() * np.cos(tt.ravel()), hy0 + rr.ravel() * np.sin(tt.ravel()))
    pl = fit_plane(Rg)
    n = pl.normal if pl.normal @ panel.R[:, 2] >= 0 else -pl.normal
    c = fit_circle(E, normal=n)
    return {"centre_nominal": world([hx0], [hy0])[0], "centre": np.asarray(c.center), "normal": np.asarray(n),
            "diameter": 2.0 * float(c.radius_mm), "radius_nominal": float(r0), "local": (hx0, hy0, r0)}


def scene_of(spec: SceneSpec, models: Path | None = None):
    """The :class:`~ohs.sim.scene.Scene` of a recording (the same object rendered and judged)."""
    from ohs.sim.scene import make_scene

    b = spec.build
    m = b["maker"]
    if m in ("scan", "orbit"):
        return make_scene(b["scene"], spec.seed)
    if m == "preset":
        from tools import preset_study as ps

        return ps.scene_of({"scene": b["scene"], "seed": spec.seed, "thickness": b.get("thickness", 1.0)})
    if m == "sheet":
        return sheet_scene(spec.seed)
    if m == "holes":
        return holes_plate_scene(b["gap"], spec.seed)
    if m == "health":
        from tools import calib_health_study as ch

        return ch.scene_of(dict(ch.RECORDINGS[b["spec"]], seed=spec.seed))
    if m == "seam":
        from tools import gapflush_study as gf

        return gf._scene(b["case"])
    if m == "stretch":
        from tools import preset_study as ps

        return ps.scene_of(ps.RECORDINGS[b["rec"]], models or MODELS)
    raise ValueError(f"no builder {m!r}")


def stretch(spec: SceneSpec) -> tuple[int, int | None]:
    """The frames of a recording that are processed: ``(start, stop)``."""
    if spec.build.get("maker") == "stretch":
        from tools import preset_study as ps

        r = ps.RECORDINGS[spec.build["rec"]]
        return int(r["start"]), int(r["stop"])
    return 0, None


def rec_path(cache: Path, spec: SceneSpec, models: Path | None = None) -> Path:
    if spec.build.get("maker") == "stretch":
        from tools import preset_study as ps

        return (models or MODELS) / ps.RECORDINGS[spec.build["rec"]]["source"]
    return cache / "recs" / spec.name


def available(cache: Path, spec: SceneSpec, models: Path | None = None) -> str | None:
    """None when the recording can be had; else why not (a line for the table)."""
    if spec.build.get("maker") == "stretch":
        from tools import preset_study as ps

        r = ps.RECORDINGS[spec.build["rec"]]
        base = models or MODELS
        missing = [str(p) for p in (base / r["source"] / "frames.csv", base / r["model"]) if not p.is_file()]
        return f"skipped: {', '.join(missing)} not found" if missing else None
    return None


def _orbit(scene_name: str, seed: int, n: int):
    """The 400° hand-held orbit of the M5 validation (scratch m5v/make_rec.py = holes/make_rec.py):
    10 fps, the bracket at 200-280 mm and 35-65°, the car panel round its hole at 230-300 mm and 55-75°."""
    from ohs.sim.nominal import NOMINAL_TIME_OFFSET, nominal_calibration
    from ohs.sim.scene import make_scene
    from ohs.sim.trajectory import HandheldTrajectory

    calib = nominal_calibration(IMAGE_SIZE, realistic=True)
    sc = make_scene(scene_name, seed)
    dur = n / 10.0
    t0 = 100.0 + NOMINAL_TIME_OFFSET
    if scene_name == "bracket":
        return HandheldTrajectory(target=sc.target, t0=t0, duration=dur, seed=seed, baseline=calib.stereo.baseline,
                                  distance=(200.0, 280.0), elevation_deg=(35.0, 65.0), sweep_deg_s=400.0 / dur)
    hole = panel_hole_world(sc)["centre_nominal"]
    return HandheldTrajectory(target=hole, t0=t0, duration=dur, seed=seed, baseline=calib.stereo.baseline,
                              distance=(230.0, 300.0), elevation_deg=(55.0, 75.0), sweep_deg_s=400.0 / dur)


def _alive(pid: str) -> bool:
    try:
        from tools.memory_peaks import process_memory

        return int(pid) > 0 and process_memory(int(pid)) is not None
    except (ValueError, OSError):
        return False


# --------------------------------------------------------------------------- surviving a kill
def write_json(path: Path, obj: Any) -> None:
    """Write JSON whole into a temporary file beside ``path``, then move it into place: a kill leaves
    either the old file or the new one, never half of one."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(_plain(obj), indent=1), encoding="utf-8")
    os.replace(tmp, path)


def read_result(path: Path) -> dict | None:
    """A job's ``result.json`` when it is a finished run's (whole JSON with its ``metrics``), else None."""
    try:
        r = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return r if isinstance(r, dict) and isinstance(r.get("metrics"), dict) else None


def _png_whole(f: Path) -> bool:
    try:
        with open(f, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() < 20:
                return False
            fh.seek(-12, os.SEEK_END)
            return fh.read(12)[4:8] == b"IEND"
    except OSError:
        return False


def recording_complete(path: Path, spec: SceneSpec, marker: bool = True) -> bool:
    """Whether the recording at ``path`` was rendered to its end: the ``done`` marker (written last;
    not asked for with ``marker=False``, the renderer's own check before it writes it), ``frames.csv``
    with the scene's planned number of frames, every frame's image there and whole (a PNG ends with
    its IEND chunk). A recording killed part way fails at least one of these. A stretch of a long
    recording (the door, the carburettor) is not rendered here: it is complete when its
    ``frames.csv`` is there."""
    path = Path(path)
    if spec.build.get("maker") == "stretch":
        return (path / "frames.csv").is_file()
    if (marker and not (path / "done").is_file()) or not (path / "frames.csv").is_file():
        return False
    try:
        rows = [r for r in (path / "frames.csv").read_text(encoding="utf-8").splitlines()[1:] if r.strip()]
    except OSError:
        return False
    if len(rows) != spec.frames:
        return False
    for r in rows:
        name = r.rsplit(",", 1)[-1].strip()
        f = path / "frames" / name
        if not f.is_file() or (name.lower().endswith(".png") and not _png_whole(f)):
            return False
    return True


def _take_lock(lock: Path) -> bool:
    """Make ``lock`` holding this process's id, atomically; False when a live process holds it (a
    lock left by a dead process is taken over)."""
    for _ in range(2):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                holder = lock.read_text(encoding="utf-8").strip()
            except OSError:
                holder = ""
            if holder and _alive(holder) and int(holder) != os.getpid():
                return False
            lock.unlink(missing_ok=True)  # its process is gone (killed): the lock is stale
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        return True
    return False


def render(cache: Path, spec: SceneSpec, say=print) -> Path:
    """Render one recording into ``<cache>/recs/<name>`` (once: a complete one is kept; one cut off by
    a kill, :func:`recording_complete`, is deleted and rendered again). Another process rendering the
    same recording is waited for, never doubled."""
    from ohs.sim.datasets import make_scan_recording
    from ohs.sim.nominal import NOMINAL_TIME_OFFSET, nominal_calibration
    from ohs.sim.trajectory import HandheldTrajectory

    path = rec_path(cache, spec)
    b = spec.build
    if b["maker"] == "stretch" or recording_complete(path, spec):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.parent / f"{spec.name}.rendering"
    end = time.monotonic() + 4 * 3600.0
    while not _take_lock(lock):
        if recording_complete(path, spec):
            return path
        if time.monotonic() > end:  # another process renders it: wait for it, never render it twice
            raise TimeoutError(f"{spec.name} is being rendered by another process (no end within 4 h)")
        time.sleep(15.0)
    try:
        if recording_complete(path, spec):  # finished by another process while this one waited
            return path
        if path.exists():
            say(f"{spec.name}: a recording cut off part way is there: rendered again")
        return _render_body(path, spec, say, make_scan_recording, NOMINAL_TIME_OFFSET, nominal_calibration, HandheldTrajectory)
    finally:
        lock.unlink(missing_ok=True)


def _render_body(path, spec, say, make_scan_recording, NOMINAL_TIME_OFFSET, nominal_calibration, HandheldTrajectory) -> Path:
    b = spec.build
    shutil.rmtree(path, ignore_errors=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    t = time.perf_counter()
    common = {"image_size": IMAGE_SIZE, "realistic": True, "depth_every": 0, "seed": spec.seed}
    m = b["maker"]
    if m == "scan":  # tests/test_e2e_scan.py's scan_recording
        make_scan_recording(path, scene=b["scene"], n_frames=spec.frames, **common)
    elif m == "orbit":
        make_scan_recording(path, scene=b["scene"], n_frames=spec.frames, fps=10.0, trajectory=_orbit(b["scene"], spec.seed, spec.frames),
                            overwrite=True, **common)
    elif m == "preset":  # tools/preset_study.py make_recording
        make_scan_recording(path, scene=scene_of(spec), n_frames=spec.frames, fps=float(b["fps"]), **common)
    elif m == "sheet":  # scratch shape/sheet.py render
        calib = nominal_calibration(IMAGE_SIZE, realistic=True)
        dur = spec.frames / 10.0
        traj = HandheldTrajectory(target=np.array(SHEET_TARGET), t0=100.0 + NOMINAL_TIME_OFFSET, duration=dur, seed=spec.seed,
                                  baseline=calib.stereo.baseline, distance=(200.0, 280.0), elevation_deg=(35.0, 65.0),
                                  sweep_deg_s=400.0 / dur)
        make_scan_recording(path, scene=scene_of(spec), n_frames=spec.frames, fps=10.0, trajectory=traj, overwrite=True, **common)
    elif m == "holes":  # scratch holes/make_plate_rec.py
        calib = nominal_calibration(IMAGE_SIZE, realistic=True)
        sc = scene_of(spec)
        dur = spec.frames / 10.0
        traj = HandheldTrajectory(target=sc.target, t0=100.0 + NOMINAL_TIME_OFFSET, duration=dur, seed=spec.seed,
                                  baseline=calib.stereo.baseline, distance=(230.0, 300.0), elevation_deg=(55.0, 75.0),
                                  sweep_deg_s=400.0 / dur)
        make_scan_recording(path, scene=sc, n_frames=spec.frames, fps=10.0, trajectory=traj, overwrite=True, **common)
    elif m == "health":  # tools/calib_health_study.py make_recording, the F14 seeds (scratch f14/make_seeds.py)
        from tools import calib_health_study as ch

        ch.make_recording(path, dict(ch.RECORDINGS[b["spec"]], seed=spec.seed), image_size=IMAGE_SIZE)
    elif m == "seam":  # tools/gapflush_study.py _record
        make_scan_recording(path, scene=scene_of(spec), n_frames=spec.frames, overwrite=True, **common)
    else:
        raise ValueError(f"no builder {m!r}")
    secs = time.perf_counter() - t
    write_json(path / "scorecard_spec.json", dataclasses.asdict(spec))
    if not recording_complete(path, spec, marker=False):
        raise RuntimeError(f"{spec.name}: the renderer ended without all {spec.frames} frames")
    write_json(path / "done", {"frames": spec.frames, "render_s": round(secs, 1),
                               "finished": _dt.datetime.now().isoformat(timespec="seconds")})  # last: the recording is complete
    say(f"{spec.name}: rendered {spec.frames} frames in {secs:.0f} s")
    return path


def render_seconds(cache: Path, spec: SceneSpec) -> float | None:
    """How long the recording took to render (from its marker; None for one rendered before the
    marker held it)."""
    try:
        return float(json.loads((rec_path(cache, spec) / "done").read_text(encoding="utf-8"))["render_s"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


# =========================================================================== the truth
GROUND = ("table", "floor")


class PartTruth:
    """The true surfaces of a scene, split into the part and the ground (table or floor):
    :meth:`nearest` (distance to the nearest surface, whether that is the part's, and the signed
    distance to the part, + outside) and :meth:`closest_part` (the nearest point of the part alone)."""

    signed_ok = True  # False where the part's inside is not known (a model of unknown winding)

    def nearest(self, P: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        raise NotImplementedError

    def closest_part(self, P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """The nearest point of the part to each point, and the part's unit normal there."""
        raise NotImplementedError

    def beyond_edge(self, P: np.ndarray) -> np.ndarray:
        """Points that lie past a free edge of an open surface (a sheet given as triangles): their
        nearest point of the part is on the edge, not under them. None for solids."""
        return np.zeros(len(np.asarray(P).reshape(-1, 3)), bool)

    def for_placement(self, P: np.ndarray) -> np.ndarray:
        """The points that may take part in placing the part (all, for solids)."""
        return np.ones(len(np.asarray(P).reshape(-1, 3)), bool)


class SdfTruth(PartTruth):
    """A scene of solids given as signed distances: ``sdf(P) -> (N, k)``, ``ground`` the columns that
    are table or floor. Closest points by one gradient step of the part's signed distance (exact on
    faces)."""

    def __init__(self, sdf, ground_cols: list[int], h: float = 0.02) -> None:
        self.sdf = sdf
        self.ground_cols = list(ground_cols)
        self.h = float(h)

    def _part(self, P: np.ndarray) -> np.ndarray:
        D = self.sdf(P)
        keep = [k for k in range(D.shape[1]) if k not in self.ground_cols]
        return D[:, keep].min(axis=1)

    def nearest(self, P):
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
        D = self.sdf(P)
        A = np.abs(D)
        own = np.argmin(A, axis=1)
        keep = [k for k in range(D.shape[1]) if k not in self.ground_cols]
        sp = D[:, keep].min(axis=1)
        return A[np.arange(len(P)), own], ~np.isin(own, self.ground_cols), sp

    def closest_part(self, P):
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
        d = self._part(P)
        g = np.zeros_like(P)
        for a in range(3):
            e = np.zeros(3)
            e[a] = self.h
            g[:, a] = (self._part(P + e) - self._part(P - e)) / (2 * self.h)
        g /= np.maximum(np.linalg.norm(g, axis=1, keepdims=True), 1e-12)
        return P - d[:, None] * g, g


class MeshTruth(PartTruth):
    """A part given as triangles (world, mm) over the floor plane z = 0 (or no ground)."""

    def __init__(self, V: np.ndarray, F: np.ndarray, floor_z: float | None = 0.0, signed_ok: bool = True) -> None:
        import open3d as o3d

        self.V, self.F = np.asarray(V, dtype=np.float64), np.asarray(F, dtype=np.int64)
        self.floor_z = floor_z
        self.signed_ok = bool(signed_ok)
        self.rs = o3d.t.geometry.RaycastingScene()
        self.rs.add_triangles(o3d.core.Tensor(self.V.astype(np.float32)), o3d.core.Tensor(self.F.astype(np.uint32)))
        a, b, c = self.V[self.F[:, 0]], self.V[self.F[:, 1]], self.V[self.F[:, 2]]
        n = np.cross(b - a, c - a)
        self.Fn = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)

    def _query(self, P):
        import open3d as o3d

        ans = self.rs.compute_closest_points(o3d.core.Tensor(np.ascontiguousarray(P, dtype=np.float32)))
        Q = ans["points"].numpy().astype(np.float64)
        f = ans["primitive_ids"].numpy().astype(np.int64)
        return Q, f

    def nearest(self, P):
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
        Q, f = self._query(P)
        dp = np.linalg.norm(P - Q, axis=1)
        if self.signed_ok:
            sp = np.where(np.einsum("ij,ij->i", P - Q, self.Fn[f]) < 0, -dp, dp)
        else:
            sp = np.full(len(P), np.nan)
        if self.floor_z is None:
            return dp, np.ones(len(P), bool), sp
        dg = np.abs(P[:, 2] - self.floor_z)
        return np.minimum(dp, dg), dp <= dg, sp

    def closest_part(self, P):
        Q, f = self._query(np.asarray(P, dtype=np.float64).reshape(-1, 3))
        return Q, self.Fn[f]

    def beyond_edge(self, P, min_mm: float = 0.05):
        """Past a free edge: the offset to the nearest triangle point is more than about 25° off
        that triangle's normal (the nearest point was clamped to the triangle's border), and its
        part along the surface is more than ``min_mm``. Over an inner edge where the surface bends
        by less than that (the car panel's 14° crease, a finely meshed curve) a point is never taken
        for one: its offset is within the bend's angle of a normal."""
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
        Q, f = self._query(P)
        v = P - Q
        d = np.linalg.norm(v, axis=1)
        along_n = np.abs(np.einsum("ij,ij->i", v, self.Fn[f]))
        tangential = np.sqrt(np.maximum(d ** 2 - along_n ** 2, 0.0))
        return (tangential > min_mm) & (along_n < 0.9 * d)

    BORDER_MM = 3.0  # points whose nearest true point is this close to a free edge do not place the part

    def _border_tree(self):
        if getattr(self, "_btree", None) is None:
            from scipy.spatial import cKDTree

            E = np.sort(np.concatenate([self.F[:, [0, 1]], self.F[:, [1, 2]], self.F[:, [2, 0]]]), axis=1)
            u, cnt = np.unique(E, axis=0, return_counts=True)
            b = u[cnt == 1]  # edges of one triangle only: the free edges (the outline, the holes)
            if len(b) > 0.2 * len(u):  # not a connected sheet (triangles that share no vertices): no outline to tell
                b = b[:0]
            pts = [np.zeros((0, 3))]
            for a, c in b:
                pa, pc = self.V[a], self.V[c]
                n = max(1, int(np.ceil(np.linalg.norm(pc - pa) / 0.25)))
                pts.append(pa + (pc - pa) * (np.arange(n + 1)[:, None] / n))
            B = np.concatenate(pts)
            self._btree = cKDTree(B) if len(B) else None
        return self._btree

    def for_placement(self, P):
        """Not past a free edge (:meth:`beyond_edge`), and nearest to a point of the part more than
        ``BORDER_MM`` from any free edge: the fill the matcher leaves round a sheet's outline and in
        its holes is biased, and on a gently curved sheet even a little bias slides the placement
        along it (the car panel slid 8 to 13 mm at true poses). Only the sheet's own face places it."""
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
        keep = ~self.beyond_edge(P)
        tree = self._border_tree()
        if tree is not None and keep.any():
            Q, _ = self._query(P[keep])
            keep[keep] = tree.query(Q, distance_upper_bound=self.BORDER_MM)[0] > self.BORDER_MM
        return keep


def truth_for(spec: SceneSpec, sc) -> PartTruth:
    """The :class:`PartTruth` of a recording's scene."""
    from tools.calib_health_study import Truth
    from tools.preset_study import _panel_mesh

    if spec.kind == "holes":
        gap = spec.build["gap"]
        return SdfTruth(lambda P: holes_plate_sdf(gap, P), [2])
    if spec.kind in ("car_panel",):
        V, F = _panel_mesh(sc, step=0.4)
        return MeshTruth(V, F, 0.0)
    if spec.kind == "seam":
        V, F = sc.meta["mesh"]
        return MeshTruth(V, F, 0.0)
    if spec.kind in ("door", "carb"):
        V, F = sc.meta["mesh"]
        return MeshTruth(V, F, 0.0, signed_ok=False)  # the models' winding is not known: no signed mean
    t = Truth(sc)
    return SdfTruth(t.prim_sdf, [k for k, n in enumerate(t.names) if n in GROUND])


# =========================================================================== the part alone, placed
def position_order(V: np.ndarray) -> np.ndarray:
    """The vertices' indices sorted by position (x, then y, then z): the same for the same points
    whatever order a mesh lists them in."""
    V = np.asarray(V, dtype=np.float64)
    return np.lexsort((V[:, 2], V[:, 1], V[:, 0])) if len(V) else np.zeros(0, np.int64)


def position_sample(V: np.ndarray, n: int, seed: int = 0) -> np.ndarray:
    """At most ``n`` of the points, picked by position (:func:`position_order`), never by vertex number."""
    V = np.asarray(V, dtype=np.float64)
    o = position_order(V)
    if len(o) > n:
        o = o[np.sort(np.random.default_rng(seed).choice(len(o), n, replace=False))]
    return V[o]


def _kabsch(P: np.ndarray, Q: np.ndarray) -> np.ndarray:
    cp, cq = P.mean(axis=0), Q.mean(axis=0)
    U, _, Wt = np.linalg.svd((P - cp).T @ (Q - cq))
    D = np.diag([1.0, 1.0, np.sign(np.linalg.det(Wt.T @ U.T))])
    R = Wt.T @ D @ U.T
    return G.make_se3(R, cq - R @ cp)


#: a direction of the rigid move whose constraint is below this share of the strongest one is left
#: where it is (a gently curved sheet hardly fixes its slide along itself: any bias moves it there)
WEAK = 0.05


def _point_to_plane(P: np.ndarray, Q: np.ndarray, N: np.ndarray, weak: float | None = None) -> tuple[np.ndarray, int]:
    """The small rigid move that brings points ``P`` onto the planes ``(Q, N)`` in least squares
    (about their centroid; rotations scaled by the points' radius so that the six directions are
    comparable), solved only in the directions the planes fix: those whose constraint is below
    ``weak`` times the strongest are left out (no move there). Returns ``(move, directions left out)``."""
    c = P.mean(axis=0)
    p = P - c
    rc = max(float(np.sqrt(np.mean(np.sum(p ** 2, axis=1)))), 1e-6)
    A = np.hstack([np.cross(p, N) / rc, N])
    b = -np.einsum("ij,ij->i", P - Q, N)
    H = A.T @ A
    w, U = np.linalg.eigh(H)
    keep = w > max((WEAK if weak is None else weak) * float(w.max()), 1e-12)
    g = U.T @ (A.T @ b)
    x = U[:, keep] @ (g[keep] / w[keep])
    R = G.so3_exp(x[:3] / rc)
    return G.make_se3(R, c + x[3:] - R @ c), int((~keep).sum())


def place_part(V: np.ndarray, T0: np.ndarray, truth: PartTruth, *, n_sample: int = 30000, iters: int = 60,
               gate: float = 1.5, wide: float = 3.0) -> tuple[np.ndarray, dict]:
    """``T0`` (the scan's world into the simulator's) refined by a rigid best fit of the part alone:
    of a sample of the scan's points taken by position, those whose nearest true surface is the
    part's and within ``wide`` mm (the first 5 rounds) or ``gate`` mm are moved onto the part's
    surface (point to plane, at the part's nearest point and its normal there). The table and floor
    are never a target, and their points never take part; nor do points past a free edge of an open
    surface (:meth:`PartTruth.beyond_edge`: fill hanging off a sheet's border, which would drag the
    sheet along itself; on a closed model it also leaves out points just outside a sharp convex
    corner, which only thins the sample). Returns ``(T, info)``."""
    T = np.asarray(T0, dtype=np.float64).copy()
    V = np.asarray(V, dtype=np.float64)
    d0, part0, _ = truth.nearest(G.transform_points(T, V))
    cand = V[part0 & (d0 < 2.0 * wide)]  # the part's points first, so a small part on a large table gets its share
    S = position_sample(cand if len(cand) >= 300 else V, n_sample)
    info: dict[str, Any] = {"sample": int(len(S)), "candidates": int(len(cand)), "rounds": 0}
    for k in range(iters):
        P = G.transform_points(T, S)
        d, part, _ = truth.nearest(P)
        sel = part & (d < (wide if k < 5 else gate))
        sel[sel] &= truth.for_placement(P[sel])  # fill past a sheet's free edge would drag it along itself
        if sel.sum() < 100:
            info["stopped"] = f"only {int(sel.sum())} part points within the gate"
            break
        Q, N = truth.closest_part(P[sel])
        dT, info["weak_directions"] = _point_to_plane(P[sel], Q, N)
        T = dT @ T
        info["rounds"] = k + 1
        info["used"] = int(sel.sum())
        if np.linalg.norm(dT[:3, 3]) < 1e-6 and G.rotation_angle_deg(dT[:3, :3]) < 1e-7:
            break
    # then point to point, which also pulls along the directions a plane leaves free; not where the
    # part's face leaves a direction free (a sheet): there only the biased fill at its border would pull
    for k in range(iters if not info.get("weak_directions") else 0):
        P = G.transform_points(T, S)
        d, part, _ = truth.nearest(P)
        sel = part & (d < gate)
        sel[sel] &= truth.for_placement(P[sel])
        if sel.sum() < 100:
            break
        Q, _ = truth.closest_part(P[sel])
        dT = _kabsch(P[sel], Q)
        T = dT @ T
        info["rounds_p2p"] = k + 1
        if np.linalg.norm(dT[:3, 3]) < 1e-6 and G.rotation_angle_deg(dT[:3, :3]) < 1e-7:
            break
    info["moved_mm"] = float(np.linalg.norm(T[:3, 3] - np.asarray(T0)[:3, 3]))
    info["turned_deg"] = float(G.rotation_angle_deg(T[:3, :3] @ np.asarray(T0)[:3, :3].T))
    return T, info


def _q(x: np.ndarray, p: float) -> float | None:
    return float(np.percentile(x, p)) if len(x) else None


def part_scores(Vs: np.ndarray, truth: PartTruth) -> dict:
    """The placed mesh's vertices whose nearest true surface is the part's: median and 95 % of the
    distance, the share more than 0.3 mm off, the share more than 3 mm off, the signed mean (+ outside,
    of those within 3 mm) and their count; the ground's median for reference."""
    d, part, sp = truth.nearest(Vs)
    dp = d[part]
    near = part & (d < 3.0)
    out = {"part_n": int(part.sum()), "part_med": _q(dp, 50), "part_p95": _q(dp, 95),
           "part_over03": float(np.mean(dp > 0.3)) if len(dp) else None,
           "part_far3": float(np.mean(dp > 3.0)) if len(dp) else None,
           "part_signed": float(np.nanmean(sp[near])) if near.any() and truth.signed_ok else None,
           "ground_med": _q(d[~part], 50)}
    if isinstance(truth, MeshTruth):  # an open surface: the share of the part's points that is fill past its free edge
        be = truth.beyond_edge(Vs[part]) & (dp > 0.3)
        out["part_beyond_edge"] = float(np.mean(be)) if len(dp) else None
    return out


def mesh_distance(Vs: np.ndarray, F: np.ndarray, P: np.ndarray) -> np.ndarray:
    import open3d as o3d

    if not len(F) or not len(P):
        return np.full(len(P), np.inf)
    rs = o3d.t.geometry.RaycastingScene()
    rs.add_triangles(o3d.core.Tensor(np.asarray(Vs, np.float32)), o3d.core.Tensor(np.asarray(F).astype(np.uint32)))
    return rs.compute_distance(o3d.core.Tensor(np.asarray(P, np.float32))).numpy().astype(np.float64)


# =========================================================================== the fits
#: a click whose true point is farther than this from the mesh lands on a face the scan did not
#: hold (a short scan of the bracket): its fit would measure some other surface, so it is not counted
CLICK_MM = 1.0


class Clicker:
    """Clicks on a placed mesh as a user would: the nearest point of the mesh to a true surface point,
    then ``ohs.measure.fit_at`` there."""

    def __init__(self, Vs: np.ndarray, F: np.ndarray, S, filled) -> None:
        import open3d as o3d

        self.V, self.F, self.S, self.Fl = Vs, F, S, filled
        self.rs = o3d.t.geometry.RaycastingScene()
        self.rs.add_triangles(o3d.core.Tensor(np.asarray(Vs, np.float32)), o3d.core.Tensor(np.asarray(F).astype(np.uint32)))

    def fit(self, p: np.ndarray, kind: str):
        """``(RegionFit or None, error text or None, distance of the click from the mesh)``."""
        import open3d as o3d

        from ohs.measure import fit_at

        ans = self.rs.compute_closest_points(o3d.core.Tensor(np.asarray(p, np.float32).reshape(1, 3)))
        face = int(ans["primitive_ids"].numpy()[0])
        q = ans["points"].numpy()[0].astype(np.float64)
        dist = float(np.linalg.norm(q - np.asarray(p, float)))
        try:
            return fit_at(self.V, self.F, face, q, kind, sigma=self.S, filled=self.Fl), None, dist
        except ValueError as e:
            return None, str(e)[:90], dist


def _med(x: list) -> float | None:
    x = [v for v in x if v is not None and np.isfinite(v)]
    return float(np.median(x)) if x else None


def hole_reading(ck: Clicker, clicks: list[np.ndarray], true_d: float, true_c: np.ndarray) -> dict:
    """The Hole tool at each click: the median diameter and its error, the median centre error, how
    many answered, and how (an open edge, or the level below a face with a wall)."""
    diam, cen, how, refused = [], [], [], []
    for p in clicks:
        r, err, _ = ck.fit(p, "hole")
        if r is None:
            refused.append(err)
            continue
        diam.append(2.0 * float(r.fit.radius_mm))
        cen.append(float(np.linalg.norm(np.asarray(r.fit.center) - true_c)))
        how.append("level" if any(w.startswith("this hole has a wall") for w in r.warnings) else "edge")
    d = _med(diam)
    return {"d": d, "d_err": None if d is None else d - true_d, "centre_err": _med(cen), "answered": len(diam),
            "how": "/".join(sorted(set(how))) or None, "refused": refused[:3]}


def _panel_fits(spec, sc, ck, dr: float = 1.5) -> dict:
    h = panel_hole_world(sc)
    panel = sc.primitives[sc.object_id("panel")]
    hx, hy, r = h["local"]
    clicks = []
    for deg in (0.0, 120.0, 240.0):
        t = math.radians(deg)
        x, y = hx + (r + dr) * math.cos(t), hy + (r + dr) * math.sin(t)
        z = float(panel.height(np.array([x]), np.array([y]))[0][0])
        clicks.append(G.transform_points(panel.T_wo, np.array([[x, y, z]]))[0])
    hr = hole_reading(ck, clicks, h["diameter"], h["centre"])
    return {"hole25_d_err": hr["d_err"], "hole25_centre_err": hr["centre_err"], "hole25_answered": hr["answered"],
            "_hole25": hr}


def _sphere_fits(sc, Vs) -> dict:
    from tools.preset_study import _sphere_fit

    c0 = np.asarray(sc.meta["sphere_center"], dtype=np.float64)
    r0 = float(sc.meta["sphere_radius"])
    d = np.abs(np.linalg.norm(Vs - c0, axis=1) - r0)
    sel = (d < 1.5) & (Vs[:, 2] > 8.0)
    if sel.sum() <= 200:
        return {"sphere_r_err": None, "sphere_c_err": None}
    c, r = _sphere_fit(Vs[sel])
    return {"sphere_r_err": r - r0, "sphere_c_err": float(np.linalg.norm(c - c0))}


def _bracket_fits(sc, Vs, ck) -> dict:
    from tools.calib_health_study import BRACKET_TRUE, Truth, measures

    m = measures(Vs, Truth(sc))
    out: dict[str, Any] = {f"br_{k}_err": (None if m.get(k) is None else float(m[k] - BRACKET_TRUE[k]))
                           for k in ("shaft_d", "base_height", "upright_thickness", "upright_height")}
    prim = {p.name: p for p in sc.primitives}
    Rz = prim["base"].R
    for name, angles in (("boss_left", (180.0, 90.0, 270.0)), ("boss_right", (0.0, 90.0, 270.0))):
        p = prim[name]
        rad = []
        for a in angles:
            t = math.radians(a)
            q = p.t + p.radius * (math.cos(t) * p.R[:, 0] + math.sin(t) * p.R[:, 1])
            r, _, dist = ck.fit(q, "cylinder")
            rad.append(None if r is None or r.kind != "cylinder" or dist > CLICK_MM else float(r.fit.radius_mm) - p.radius)
        out[f"br_{name}_r_err"] = _med(rad)
        out[f"br_{name}_answered"] = sum(v is not None for v in rad)
    sh = prim["shaft"]
    loc = [[0.0, -6.0, 42.0 + 8.0], [8.0 * math.cos(math.radians(45)), 0.0, 42.0 + 8.0 * math.sin(math.radians(45))],
           [-8.0 * math.cos(math.radians(30)), -14.0, 42.0 + 8.0 * math.sin(math.radians(30))]]
    rad = []
    for q in loc:
        r, _, dist = ck.fit(Rz @ np.asarray(q), "cylinder")
        rad.append(None if r is None or r.kind != "cylinder" or dist > CLICK_MM else float(r.fit.radius_mm) - sh.radius)
    out["br_shaft_r_err"] = _med(rad)
    up = prim["upright"]
    n_true = up.R[:, 1]
    p_true = up.t + up.half[1] * n_true  # the upright's front face (towards the bosses)
    offs = []
    for q in ([-25.0, -22.0, 28.0], [25.0, -22.0, 22.0], [-30.0, -22.0, 50.0]):
        r, _, dist = ck.fit(Rz @ np.asarray(q), "plane")
        if r is None or r.kind != "plane" or dist > CLICK_MM:
            offs.append(None)
            continue
        offs.append(float((np.asarray(r.fit.point) - p_true) @ n_true))
    out["br_upright_offset"] = _med(offs)
    return out


def _plate_fits(sc, Vs, seen, cover, prefix="plate") -> dict:
    """A thin plate lying flat: its thickness, as the distance from its top's plane to the table round
    it (a rigid move does not change it), and the share of its seen top the mesh holds within half
    its thickness."""
    from ohs.measure import fit_plane
    from ohs.sim.scene import Box

    t = float(sc.meta["thickness_mm"])
    boxes = [p for p in sc.primitives if isinstance(p, Box)]

    def inside(P, grow):
        m = np.zeros(len(P), bool)
        for b in boxes:
            pl = (P - b.t) @ b.R
            m |= np.all(np.abs(pl[:, :2]) < b.half[:2] + grow, axis=1)
        return m

    top = inside(Vs, -2.0) & (Vs[:, 2] > -1.0) & (Vs[:, 2] < t + 1.0)
    ring = inside(Vs, 10.0) & ~inside(Vs, 3.0) & (np.abs(Vs[:, 2]) < 1.0)
    out: dict[str, Any] = {f"{prefix}_t_err": None}
    if top.sum() > 50 and ring.sum() > 50:
        pl = fit_plane(Vs[top])
        n = pl.normal if pl.normal[2] > 0 else -pl.normal
        out[f"{prefix}_t_err"] = float(-np.median((Vs[ring] - pl.point) @ n)) - t
    st = inside(seen, -1.0) & (seen[:, 2] > t - 0.01)
    out[f"{prefix}_top_kept"] = float(np.mean(cover[st] < t / 2.0)) if st.any() else None
    return out


def _fin_fits(sc, Vs, seen, cover) -> dict:
    from tools.preset_study import _fin_fits as pf

    r = pf(type("J", (), {"sc": sc})(), Vs, seen, cover)
    out = {}
    for t in (0.5, 1.0, 2.0):
        key = f"t{t:g}".replace(".", "_")
        v = r.get(f"{key}_read")
        out[f"wall{t:g}_err"] = None if v is None else float(v - t)
        out[f"wall{t:g}_cover05"] = r.get(f"{key}_cover05")
    return out


def _sheet_fits(Vs, F, filled) -> dict:
    """The sheet part's flange and ribs: a plane on each face (inside its outline by 3 mm, above the
    foot), the thickness between them (scratch shape/sheet.py, on the part placed alone)."""
    keep = np.zeros(len(Vs), bool)
    Fm = F if filled is None else F[~np.asarray(filled, bool)]
    keep[np.unique(Fm)] = True
    V = Vs[keep]
    out: dict[str, Any] = {}
    for name, (c, h, ax) in SHEET_WALLS.items():
        c, h = np.array(c), np.array(h)
        q = V - c
        others = [i for i in range(3) if i != ax]
        inside = np.all(np.abs(q[:, others]) < h[others] - 3.0, axis=1) & (np.abs(q[:, ax]) < h[ax] + 2.5)
        if name != "flange_2mm":
            inside &= q[:, 1] < h[1] - 3.0
        inside &= V[:, 2] > 5.0
        P = V[inside]
        key = f"sheet_{name.split('_')[0]}{name.split('_')[1]}_err"
        out[key] = None
        if len(P) < 400:
            continue
        side = P[:, ax] - np.median(P[:, ax])
        planes = []
        for sgn in (1, -1):
            S = P[sgn * side > 0]
            if len(S) < 200:
                planes.append(None)
                continue
            cen = S.mean(axis=0)
            n = np.linalg.svd(S - cen, full_matrices=False)[2][2]
            n *= np.sign(n[ax]) * sgn
            planes.append((cen, n))
        if planes[0] is not None and planes[1] is not None:
            (c1, n1), (c2, n2) = planes
            nm = n1 - n2
            nm /= np.linalg.norm(nm)
            out[key] = float((c1 - c2) @ nm) - float(2 * h[ax])
    return out


def _holes_fits(spec, ck) -> dict:
    gap = spec.build["gap"]
    T = holes_plate_T(gap)
    out: dict[str, Any] = {}
    items = [(k, HP_HOLES[k], HP_T) for k in HP_HOLES] + [("bore", HP_BORE[:3], HP_T + HP_BLOCK[3])]
    for key, (hx, hy, r), z in items:
        clicks = []
        for deg in (0.0, 120.0, 240.0):
            t = math.radians(deg) + 0.3
            clicks.append(G.transform_points(T, np.array([[hx + (r + 1.5) * math.cos(t), hy + (r + 1.5) * math.sin(t), z]]))[0])
        c = G.transform_points(T, np.array([[hx, hy, z]]))[0]
        hr = hole_reading(ck, clicks, 2.0 * r, c)
        out[f"{key}_d_err"] = hr["d_err"]
        out[f"{key}_centre_err"] = hr["centre_err"]
        out[f"{key}_answered"] = hr["answered"]
        out[f"_{key}"] = hr
    return out


def _seam_fits(spec, sc, Vs, F, S, filled) -> dict:
    from scipy.spatial import cKDTree

    from ohs.inspect.gapflush import DEFAULT_RULE, measure_gap_flush
    from ohs.sim.scene import panel_seam_truth

    tree = cKDTree(Vs)
    clicks = [Vs[tree.query(c)[1]] for c in sc.meta["clicks"]]
    out: dict[str, Any] = {"flush_err": None, "flush_trusted": 0, "gap_err": None, "gap_trusted": 0}
    try:
        res = measure_gap_flush(Vs, F, clicks[0], clicks[1], sigma=S, filled=filled, rule=DEFAULT_RULE)
    except ValueError as e:
        out["_refused"] = str(e)[:90]
        return out
    X = np.array([s.point for s in res.stations]) if res.stations else np.zeros((0, 3))
    if not len(X):
        return out
    tr = panel_seam_truth(sc, X)
    st = [s.to_dict() for s in res.stations]
    fl = [d["flush_mm"] - tr["flush_mm"][k] for k, d in enumerate(st) if d.get("flush_trusted") and tr["on_seam"][k]]
    gp = [d["gap_mm"] - tr["gap_mm"][k] for k, d in enumerate(st) if d.get("gap_mm") is not None and tr["on_seam"][k]]
    out.update(flush_err=float(np.mean(fl)) if fl else None, flush_trusted=len(fl),
               gap_err=float(np.mean(gp)) if gp else None, gap_trusted=sum(bool(d.get("gap_trusted")) for d in st))
    return out


def scene_fits(spec: SceneSpec, sc, Vs, F, S, filled, seen, cover) -> dict:
    """The fits of the scene, on the placed mesh (simulator world)."""
    k = spec.kind
    if k == "sphere":
        return _sphere_fits(sc, Vs)
    if k == "car_panel":
        return _panel_fits(spec, sc, Clicker(Vs, F, S, filled))
    if k == "bracket":
        return _bracket_fits(sc, Vs, Clicker(Vs, F, S, filled))
    if k == "thin_plate":
        return _plate_fits(sc, Vs, seen, cover)
    if k == "fins":
        return _fin_fits(sc, Vs, seen, cover)
    if k == "sheet":
        return _sheet_fits(Vs, F, filled)
    if k == "holes":
        return _holes_fits(spec, Clicker(Vs, F, S, filled))
    if k == "seam":
        return _seam_fits(spec, sc, Vs, F, S, filled)
    return {}


# =========================================================================== the settings to try
SECTIONS = ("process", "pipeline", "fusion", "depth", "laser", "const", "curve", "spacetime")
#: settings that act in Straighten only (``curve.<CurveParams field>``: the laser crossings' adjustment); they need
#: ``--chain app``, and a true-pose run, which is not straightened, ignores them
STRAIGHTEN_ONLY = ("curve.",)
#: settings of the app's Make mesh outside the frame pass (ENGINE_PLAN X6): ``process.refine_from_images=true`` (the
#: app's ``[process] refine_from_images``: "Refine from the images" between Straighten and Refine with lasers) and
#: ``spacetime.<SpacetimeParams field>`` (its parameters, over ``make_mesh_spacetime_params()``); ``--chain app`` only
APP_ONLY = ("process.refine_from_images", "spacetime.")
#: what runs on each recording (module docstring, "Chains")
CHAINS = ("frame", "app")


def parse_params(items: list[str] | None, path: str | Path | None = None) -> dict[str, Any]:
    """``["fusion.voxel_mm=0.5", ...]`` and/or a JSON file ``{"fusion.voxel_mm": 0.5}`` into one dict.
    A value is read as JSON (numbers, true / false / null, lists), else kept as text."""
    out: dict[str, Any] = {}
    if path:
        out.update(json.loads(Path(path).read_text(encoding="utf-8")))
    for it in items or []:
        if "=" not in it:
            raise ValueError(f"--set-param wants SECTION.FIELD=VALUE, got {it!r}")
        k, v = it.split("=", 1)
        try:
            out[k.strip()] = json.loads(v)
        except json.JSONDecodeError:
            out[k.strip()] = v
    for k in out:
        if k.split(".", 1)[0] not in SECTIONS or "." not in k:
            raise ValueError(f"unknown setting {k!r}: write SECTION.FIELD with SECTION one of {', '.join(SECTIONS)}")
    return out


def settings_key(params: dict, chain: str = "frame", poses: str | None = None) -> str:
    """The folder name of a run's settings. ``chain`` "frame" (the frame pass only) keeps the names it always had
    (``default``, ``p-<hash>``), so every saved run is found again; ``app`` (:func:`app_chain`) is ``app-default`` /
    ``app-p-<hash>``. At true poses the app chain does not straighten (:data:`STRAIGHTEN_ONLY`), so Straighten's
    settings are left out of a true-pose run's key: it is the same run with and without them."""
    if chain == "app" and poses == "true":
        params = {k: v for k, v in params.items() if not k.startswith(STRAIGHTEN_ONLY)}
    key = "default" if not params else "p-" + hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:10]
    return key if chain == "frame" else f"{chain}-{key}"


def base_params(spec: SceneSpec):
    """The app's Make mesh (Standard quality, the default config, markerless) for the recording."""
    from ohs.app.config import Config
    from ohs.server.studio import process_params

    start, stop = stretch(spec)
    pp = process_params(Config(), "markerless", "standard", IMAGE_SIZE[0])
    return dataclasses.replace(pp, export=("ply",), start=int(start), stop=stop)


def apply_params(pp, params: dict):
    """``ProcessParams`` ``pp`` with the settings of :func:`parse_params`. ``const.<module>.<NAME>``
    sets a module constant (in this process only: a job's)."""
    import importlib

    from ohs.app.pipeline import default_depth_params, default_fusion_params, default_laser_params, effective_scale

    pipe = dataclasses.replace(pp.pipeline)
    top: dict[str, Any] = {}
    s = effective_scale(IMAGE_SIZE[0])
    for key, v in params.items():
        sec, name = key.split(".", 1)
        if key.startswith(APP_ONLY):  # the app chain's own steps (:func:`_app_spacetime`), not the frame pass
            continue
        if sec == "process":
            top[name] = v
        elif sec == "pipeline":
            setattr(pipe, name, v)
        elif sec == "fusion":
            pipe.fusion = dataclasses.replace(pipe.fusion or default_fusion_params(s), **{name: v})
        elif sec == "depth":
            pipe.depth = dataclasses.replace(pipe.depth or default_depth_params(s), **{name: v})
        elif sec == "laser":
            pipe.laser = dataclasses.replace(pipe.laser or default_laser_params(s), **{name: v})
        elif sec == "const":
            mod, _, const = name.rpartition(".")
            m = importlib.import_module(mod)
            if not hasattr(m, const):
                raise ValueError(f"{mod} has no {const}")
            setattr(m, const, v)
        # "curve": Straighten's laser crossings (:func:`curve_params`), not the frame pass
    return dataclasses.replace(pp, pipeline=pipe, **top)


def curve_params(params: dict):
    """The laser crossings' ``CurveParams`` with the ``curve.<field>`` settings; None without any, so that Straighten
    is called exactly as the server calls it (its own default)."""
    from ohs.track.curve_ba import CurveParams

    kw = {k.split(".", 1)[1]: v for k, v in params.items() if k.startswith("curve.")}
    if not kw:
        return None
    bad = [k for k in kw if k not in {f.name for f in dataclasses.fields(CurveParams)}]
    if bad:
        raise ValueError(f"CurveParams has no {', '.join(bad)}")
    return dataclasses.replace(CurveParams(), **{k: tuple(v) if isinstance(v, list) else v for k, v in kw.items()})


def app_params(spec: SceneSpec, params: dict, rec: Path):
    """What the server's Make mesh fuses with (:func:`ohs.server.processing.stage_params`: Standard quality, the default
    ``Config()``, markerless, the recording's own width, no preset), the recording's stretch, and the settings tried."""
    from ohs.app.config import Config
    from ohs.io.recording import RecordingReader
    from ohs.server.processing import stage_params

    reader = RecordingReader(rec)
    size = reader.meta.get("image_size") or list(reader.frame(0).image_size)
    start, stop = stretch(spec)
    pp = stage_params(Config(), "markerless", "standard", int(size[0]))
    return apply_params(dataclasses.replace(pp, start=int(start), stop=stop), params)


# =========================================================================== the app's Make mesh (--chain app)
def app_chain(rec: Path, proc: Path, spec: SceneSpec, params: dict, poses: dict | None) -> tuple[dict, dict]:
    """Run on ``rec`` what the app's Make mesh runs before its recipe (``ohs.server.processing``: ``_run_recipe`` on
    everything with the Default recipe's settings, ``straighten`` and ``refine`` "auto"), with the server's own
    parameters and decisions: Follow the camera (:func:`app_params`), Straighten (the loops, a fuse at their poses when
    they change them, the laser crossings against that mesh, a fuse at their poses when they change them), Refine with
    lasers on the stage's mesh. Each stage's files go to ``proc/stages/<stage>/`` as the server writes them under
    ``process/``; the last one (the app's base mesh) is copied to ``proc/`` for :func:`judge`, which then applies the
    recipe's edits as for the frame pass.

    With given (true) ``poses`` there is no tracked camera path to straighten: Follow the camera fuses at them and
    Refine with lasers runs. Returns ``(report of the base stage, what each stage did)``."""
    from ohs.app.process import process_recording

    pp = app_params(spec, params, rec)
    st = proc / "stages"
    st.mkdir(parents=True, exist_ok=True)
    info: dict[str, Any] = {"chain": "app", "stage_s": {}}
    t = time.perf_counter()
    process_recording(rec, st / "track", params=pp, poses=poses)
    info["stage_s"]["track"] = round(time.perf_counter() - t, 1)
    base = st / "track"
    if poses is None:
        base = _app_straighten(rec, st, pp, params, info)
    else:
        info["straighten"] = {"ran": False, "why": "true poses: there is no tracked camera path to straighten"}
    if params.get("process.refine_from_images"):  # ENGINE_PLAN X6, off unless asked for
        base = _app_spacetime(rec, base, st, params, info)
    base = _app_refine(base, st, info)
    for name in ("mesh.ply", "report.json", "trajectory.csv", "laser_points.ply"):
        if (base / name).is_file():
            shutil.copy2(base / name, proc / name)
    info["base"] = base.name
    return json.loads((base / "report.json").read_text(encoding="utf-8")), info


def _app_fuse(rec: Path, out: Path, pp, poses: dict) -> None:
    from ohs.app.process import process_recording

    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    process_recording(rec, tmp, params=pp, poses=poses)
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)


def _app_straighten(rec: Path, st: Path, pp, params: dict, info: dict) -> Path:
    """Straighten as ``ProcessManager._do_straighten`` runs it; returns the stage folder of the base mesh after it."""
    from ohs.io.recording import RecordingReader
    from ohs.server import processing as SP
    from ohs.track.curve_ba import laser_curve_adjustment
    from ohs.track.posegraph import read_trajectory, straighten_trajectory

    t = time.perf_counter()
    traj = read_trajectory(st / "track" / "trajectory.csv")
    calib = RecordingReader(rec).calibration()
    s: dict[str, Any] = {"ran": True}
    info["straighten"] = s
    try:
        poses, rep = straighten_trajectory(rec, calib, traj)
    except Exception as e:  # noqa: BLE001 - the server's stage is skipped, the base stays
        s.update(failed=f"{type(e).__name__}: {e}", looped=False, adjusted=False)
        return st / "track"
    rep = dict(rep or {})
    looped, loops = SP.straighten_looped(rep, poses)
    s.update(loops=int(loops or 0), looped=looped, loop_verdict=rep.get("verdict"),
             loop_correction_mm=rep.get("correction_mm"), loops_s=round(time.perf_counter() - t, 1))
    if looped:  # the loops' fuse is also the reference surface for the laser crossings
        t1 = time.perf_counter()
        _app_fuse(rec, st / "straighten", pp, poses)
        s["fuse_loops_s"] = round(time.perf_counter() - t1, 1)
        ref_dir, cur = st / "straighten", dict(poses)
    else:
        ref_dir, cur = st / "track", {i: T for i, _, ok, T in traj if ok}
    t1 = time.perf_counter()
    new_poses = cur
    try:
        Vr, Fr, _ = SP._read_stage_mesh(ref_dir / "mesh.ply")
        rows_cur = [(i, tt, True, cur[i]) for i, tt, ok, _ in traj if ok and i in cur]
        new_poses, crep = laser_curve_adjustment(rec, calib, rows_cur, reference=(Vr, Fr), params=curve_params(params))
    except Exception as e:  # noqa: BLE001 - as the server: "could not finish", the poses stay
        crep = {"changed": False, "reason": "failed", "verdict": f"failed: {type(e).__name__}: {e}"}
    s["crossings_s"] = round(time.perf_counter() - t1, 1)
    rep["laser_curves"] = crep
    adjusted = bool(crep.get("changed"))
    s["adjusted"] = adjusted
    s["crossings"] = {k: crep.get(k) for k in ("reason", "changed", "frames", "crossings", "holdout", "miss_mm", "moved_mm",
                                                "moved_deg", "verdict", "warning", "ground_truth")}
    write_json(st / "straighten.json", _plain(rep))
    if adjusted:
        t1 = time.perf_counter()
        _app_fuse(rec, st / "straighten", pp, new_poses)
        s["fuse_crossings_s"] = round(time.perf_counter() - t1, 1)
    info["stage_s"]["straighten"] = round(time.perf_counter() - t, 1)
    return st / "straighten" if (looped or adjusted) else st / "track"


def spacetime_params(params: dict):
    """Make mesh's ``SpacetimeParams`` (``ohs.server.processing.make_mesh_spacetime_params``) with the
    ``spacetime.<field>`` settings."""
    from ohs.server.processing import make_mesh_spacetime_params
    from ohs.track.spacetime import SpacetimeParams

    p = make_mesh_spacetime_params()
    kw = {k.split(".", 1)[1]: v for k, v in params.items() if k.startswith("spacetime.")}
    bad = [k for k in kw if k not in {f.name for f in dataclasses.fields(SpacetimeParams)}]
    if bad:
        raise ValueError(f"SpacetimeParams has no {', '.join(bad)}")
    return dataclasses.replace(p, **{k: tuple(tuple(x) if isinstance(x, list) else x for x in v) if isinstance(v, list)
                                     else v for k, v in kw.items()})


def _app_spacetime(rec: Path, base: Path, st: Path, params: dict, info: dict) -> Path:
    """"Refine from the images" as Make mesh runs it with ``[process] refine_from_images``
    (``ProcessManager._make_mesh_spacetime``: the GPU only, ``spacetime_refine`` on the base stage's mesh at its poses,
    ``spacetime_changes``, ``write_spacetime_stage``). Returns the stage folder of the base mesh after it. Keeps
    ``stages/spacetime_moves.npz`` (each vertex's move and the patches' results) for a look."""
    from ohs.compute import get_device
    from ohs.io.recording import RecordingReader
    from ohs.server import processing as SP
    from ohs.track.posegraph import read_trajectory

    t = time.perf_counter()
    r: dict[str, Any] = {"source": base.name, "acted": False}
    info["spacetime"] = r
    try:
        if get_device() is None:
            r["why"] = "no graphics card: the mesh is left as it is"
            return base
        poses = {i: T for i, _, ok, T in read_trajectory(base / "trajectory.csv") if ok}
        calib = RecordingReader(rec).calibration()
        V2, F, S, L, rep = SP.spacetime_refine(rec, calib, base, poses, spacetime_params(params))
        pr = rep.pop("patch_results", None) or {}
        V0 = SP._read_stage_mesh(base / "mesh.ply")[0]
        np.savez_compressed(st / "spacetime_moves.npz", move=np.asarray(V2 - V0, np.float32),
                            **{f"patch_{k}": np.asarray(v) for k, v in pr.items() if k != "reason"},
                            patch_reason=np.asarray(pr.get("reason", []), dtype=str))
        r.update({k: rep.get(k) for k in ("patches", "kept", "rejected", "frames", "covered_share", "moved_share",
                                          "moved", "device", "ms", "guard")})
        if not SP.spacetime_changes(rep):
            r["why"] = "too few patches hold dots in enough frames"
            return base
        out = st / "spacetime"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        SP.write_spacetime_stage(out, base, base.name, V2, F, S, L, rep)
        r["acted"] = True
        return out
    except Exception as e:  # noqa: BLE001 - as the server: the stage says it failed, the mesh stays as it was
        r["failed"] = f"{type(e).__name__}: {e}"
        return base
    finally:
        info["stage_s"]["spacetime"] = round(time.perf_counter() - t, 1)


def _app_refine(src: Path, st: Path, info: dict) -> Path:
    """Refine with lasers as ``ProcessManager._do_refine`` runs it on the base stage's mesh; returns the stage folder
    of the base mesh after it."""
    from ohs.mesh import anchor_to_points
    from ohs.server import processing as SP
    from ohs.server.protocol import jsonable
    from ohs.track.export import load_ply

    t = time.perf_counter()
    r: dict[str, Any] = {"source": src.name, "acted": False}
    info["refine"] = r
    try:
        lp = src / "laser_points.ply"
        P = load_ply(lp)["vertices"] if lp.is_file() else np.zeros((0, 3))
        r["points"] = int(len(P))
        if len(P) < SP.MIN_LASER_POINTS:
            r["why"] = "no laser points"
            return src
        V, F, S, L = SP._read_stage_mesh(src / "mesh.ply", with_looks=True)
        V2, _, rep = anchor_to_points(V, F, P)
        r.update(used=rep["used"], outliers=rep["outliers"], moved_pct=round(100.0 * rep["moved_share"], 1),
                 moved_mm_p95=rep["moved"]["p95"], residual_before_mm=rep["residual_before"]["median"],
                 residual_after_mm=rep["residual_after"]["median"])
        if not SP.refine_changes(rep):
            r["why"] = "the mesh already agrees with the laser lines" if rep["used"] >= SP.MIN_LASER_POINTS else "too few points"
            return src
        out = st / "refine"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        SP.save_refined_mesh(out / "mesh.ply", V2, F, S, L)
        for name in ("trajectory.csv", "laser_points.ply", "marker_map.json"):
            if (src / name).is_file():
                shutil.copy2(src / name, out / name)
        report = json.loads((src / "report.json").read_text(encoding="utf-8")) if (src / "report.json").is_file() else {}
        report["refine"] = {**rep, "source": src.name}
        (out / "report.json").write_text(json.dumps(jsonable(report), indent=1), encoding="utf-8")
        r["acted"] = True
        return out
    except Exception as e:  # noqa: BLE001 - as the server: the stage says it failed, the mesh stays as fused
        r["failed"] = f"{type(e).__name__}: {e}"
        return src
    finally:
        info["stage_s"]["refine"] = round(time.perf_counter() - t, 1)


# =========================================================================== one job (its own process)
_HOOK: dict[str, Any] = {"index": None}


def _install_depth_hook(mode: str, folder: Path):
    """``capture``: the matcher's depth (and sigma) of every processed frame saved to ``folder``;
    ``feed``: read from there instead of matching. The engine is not changed: the tool's subclasses
    take the place of ``ohs.app.pipeline.DepthEstimator`` and ``ohs.app.process.FramePipeline`` (in
    the job's process). Returns a function that puts the originals back."""
    import ohs.app.pipeline as PL
    import ohs.app.process as PR

    folder.mkdir(parents=True, exist_ok=True)
    Base = PL.DepthEstimator

    class _CachedDepth(Base):  # type: ignore[misc, valid-type]
        def _get(self, fn, with_sigma: bool):
            f = folder / f"{int(_HOOK['index']):06d}.npz"
            if mode == "feed":
                with np.load(f) as z:
                    depth = z["depth"].copy()
                    sigma = z["sigma"].copy() if "sigma" in z.files else None
                return (depth, sigma) if with_sigma else depth
            out = fn()
            depth, sigma = out if with_sigma else (out, None)
            arrs = {"depth": np.asarray(depth)}
            if sigma is not None:
                arrs["sigma"] = np.asarray(sigma)
            np.savez_compressed(f, **arrs)
            return out

        def compute_with_sigma(self, left, right):
            return self._get(lambda: Base.compute_with_sigma(self, left, right), True)

        def compute(self, left, right):
            return self._get(lambda: Base.compute(self, left, right), False)

    BasePipe = PR.FramePipeline

    class _Pipe(BasePipe):  # type: ignore[misc, valid-type]
        def process(self, frame, pose=None, **kw):
            _HOOK["index"] = int(frame.index)
            return super().process(frame, pose=pose, **kw)

    PL.DepthEstimator = _CachedDepth
    PR.FramePipeline = _Pipe

    def restore() -> None:
        PL.DepthEstimator = Base
        PR.FramePipeline = BasePipe

    return restore


def _traj(path: Path) -> list[tuple[int, np.ndarray]]:
    from ohs.track.posegraph import read_trajectory

    return [(int(i), np.asarray(T, dtype=np.float64)) for i, _, ok, T in read_trajectory(path) if ok]


def judge(spec: SceneSpec, rec: Path, proc: Path, report: dict, models: Path | None = None) -> dict:
    """Score one processed recording (module docstring): ``{"metrics": {...}, "info": {...}}``."""
    from ohs.io.recording import RecordingReader
    from ohs.mesh.order import canonical
    from ohs.mesh.topology import boundary_loops
    from ohs.track.export import load_ply
    from tools import preset_study as ps

    sc = scene_of(spec, models)
    truth = truth_for(spec, sc)
    d = load_ply(proc / "mesh.ply")
    V, F, S = d["vertices"], d["faces"], d["quality"]
    M: dict[str, Any] = {}
    info: dict[str, Any] = {}
    M["loops"] = int(len(boundary_loops(V, F).perimeter)) if F is not None and len(F) else None
    fr = report.get("frames", {})
    M["frames_tracked"] = fr.get("tracked")
    M["frames_lost"] = None if fr.get("processed") is None else int(fr["processed"]) - int(fr.get("tracked") or 0)
    g = report.get("ground_truth") or {}
    given = report.get("poses") == "given"
    for key, path in (("traj_med", ("trans_mm", "median")), ("traj_max", ("trans_mm", "max")),
                      ("rot_med", ("rot_deg", "median")), ("rot_max", ("rot_deg", "max"))):
        v = (g.get(path[0]) or {}).get(path[1])
        M[key] = None if v is None else float(v)
    M["traj_last"] = g.get("final_trans_mm")
    M["rot_last"] = g.get("final_rot_deg")
    info["poses"] = "given" if given else "tracked"
    lm = ((report.get("health") or {}).get("laser_mesh") or {}).get("per_line") or {}
    for name, st in sorted(lm.items()):
        M[f"laser_{name}_med"] = st.get("median_mm")
        M[f"laser_{name}_p95abs"] = st.get("abs_p95_mm")
    tm = (report.get("timings_ms") or {}).get("total") or {}
    M["frame_ms"] = tm.get("median")
    M["wall_s"] = report.get("wall_time_s")
    info["compute"] = (report.get("params") or {}).get("compute")
    prm = report.get("params") or {}
    info["path_settings"] = {"depth.flip_average": (prm.get("depth") or {}).get("flip_average"),
                             "fusion.velocity_max_accel": (prm.get("fusion") or {}).get("velocity_max_accel"),
                             "fusion.raycast_weight_threshold": (prm.get("fusion") or {}).get("raycast_weight_threshold")}
    if F is None or not len(F):
        info["empty"] = True
        return {"metrics": M, "info": info}
    c = canonical(np.asarray(V, np.float64), np.asarray(F, np.int64))
    V, F, S = c.V, c.F, c.vertex_values(S)
    V2, F2, S2, Fl2, _ = ps.apply_ops(V, F, S, ps.RECIPES["general"])
    reader = RecordingReader(rec)
    gt = reader.ground_truth_poses()
    tr = [(i, T) for i, T in _traj(proc / "trajectory.csv") if i in gt]
    if not tr:
        info["placed"] = "nothing tracked"
        return {"metrics": M, "info": info}
    i0, T_est0 = tr[0]
    T0 = gt[i0] @ G.invert_se3(T_est0)
    T, pinfo = place_part(V2, T0, truth)
    info["placement"] = pinfo
    info["T"] = T.tolist()
    Vs = G.transform_points(T, V2)
    M.update(part_scores(Vs, truth))
    start, stop = stretch(spec)
    n_fr = len([i for i in gt if i >= start and (stop is None or i < stop)])
    seen = ps.seen_points(sc, rec, start, stop, every=max(1, int(round(n_fr / 30.0))))  # about 30 frames' rays
    cover = mesh_distance(Vs, F2, seen)
    M["cover1"] = float(np.mean(cover < 1.0)) if len(seen) else None
    M["cover05"] = float(np.mean(cover < 0.5)) if len(seen) else None
    info["seen_n"] = int(len(seen))
    fits = scene_fits(spec, sc, Vs, F2, S2, Fl2, seen, cover)
    for k, v in fits.items():
        (info if k.startswith("_") else M)[k.lstrip("_") if k.startswith("_") else k] = v
    M["faces"] = int(len(F2))
    return {"metrics": M, "info": info}


def job_main(a: argparse.Namespace) -> int:
    """One processing and scoring job, in its own process (called by the parent with ``_job``)."""
    from ohs.app.process import process_recording
    from ohs.io.recording import RecordingReader

    spec = SCENES[a.rec]
    cache = Path(a.cache)
    models = Path(a.models) if a.models else None
    rec = rec_path(cache, spec, models)
    out = Path(a.out)
    params = json.loads(a.params) if a.params else {}
    if a.depth_hook:
        _install_depth_hook(a.depth_hook, Path(a.depth_dir))
    chain = getattr(a, "chain", "frame") or "frame"
    pp = apply_params(base_params(spec), params) if chain == "frame" else None
    poses = None
    start, stop = stretch(spec)
    if a.poses == "true":
        gt = RecordingReader(rec).ground_truth_poses()
        poses = {i: T for i, T in gt.items() if i >= start and (stop is None or i < stop)}
    elif a.poses == "replay-tracked":
        poses = dict(_traj(Path(a.trajectory)))
    proc = out / "proc"
    shutil.rmtree(proc, ignore_errors=True)
    from tools.memory_peaks import Sampler, summarize

    t0 = time.perf_counter()
    # this process's own memory, sampled while it processes (a venv's python.exe is a launcher: its
    # child would not be the process the parent started), with Windows' own peak at each sample
    stages = None
    with Sampler(os.getpid(), None, every=1.0, seconds=24 * 3600.0) as smp:
        if chain == "app":
            rep, stages = app_chain(rec, proc, spec, params, poses)
        else:
            rep = process_recording(rec, proc, params=pp, poses=poses)
    wall = time.perf_counter() - t0
    peaks = summarize(smp.rows).get("", {}) if smp.rows else {}
    t1 = time.perf_counter()
    res = judge(spec, rec, proc, rep, models)
    if stages is not None:
        res["info"]["chain"] = _plain(stages)
    res["metrics"]["peak_ws_gb"] = round(float(peaks["peak_ws"]) / 1e9, 2) if peaks.get("peak_ws") else None
    res["metrics"]["peak_gpu_shared_gb"] = round(float(peaks["gpu_shared"]) / 1e9, 2) if peaks.get("gpu_shared") else None
    res["info"]["judge_s"] = round(time.perf_counter() - t1, 1)
    res["info"]["process_s"] = round(wall, 1)
    res["info"]["version"] = __import__("ohs").__version__
    res["info"]["params"] = params
    res["info"]["started"] = _dt.datetime.now().isoformat(timespec="seconds")
    res["info"]["engine_code"] = engine_fingerprint()
    if a.keep_mesh == 0:
        for f in ("mesh.ply", "laser_points.ply"):
            (proc / f).unlink(missing_ok=True)
        shutil.rmtree(proc / "stages", ignore_errors=True)
    write_json(out / "result.json", res)  # last, and whole: a result is there only when its run finished
    return 0


def engine_fingerprint() -> str:
    """A short hash of the engine's code (``ohs/**/*.py`` without the server's and the UI's folders,
    whose edits are texts and screens): results with different fingerprints came from different
    engines, and a baseline that mixes them says so."""
    h = hashlib.sha1()
    root = SOFTWARE / "ohs"
    for f in sorted(root.rglob("*.py")):
        rel = f.relative_to(root).as_posix()
        if rel.startswith(("server/", "ui/")) or "__pycache__" in rel:
            continue
        h.update(rel.encode())
        h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:12]


KEEP_FROM_RUN = ("peak_ws_gb", "peak_gpu_shared_gb")


def rescore(cache: Path, spec: SceneSpec, compute: str, params: dict, poses: str, models: Path | None = None,
            chain: str = "frame") -> dict | None:
    """Score a finished job again from its kept mesh and report (``proc/``), without processing
    again: for a change to the scoring alone. Keeps what only the run could measure (memory,
    times); returns the new result, or None when the mesh was not kept."""
    out = run_dir(cache, compute, params, spec, poses, chain)
    old = read_result(out / "result.json")
    proc = out / "proc"
    if old is None or not (proc / "mesh.ply").is_file() or not (proc / "report.json").is_file():
        return None
    rep = json.loads((proc / "report.json").read_text(encoding="utf-8"))
    t = time.perf_counter()
    new = judge(spec, rec_path(cache, spec, models), proc, rep, models)
    for k in KEEP_FROM_RUN:
        new["metrics"][k] = old["metrics"].get(k)
    info = dict(old.get("info") or {})
    info.update(new["info"])
    info["judge_s"] = round(time.perf_counter() - t, 1)
    info["rescored"] = _dt.datetime.now().isoformat(timespec="seconds")
    new["info"] = info
    write_json(out / "result.json", new)
    return new


def _plain(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, np.ndarray):
        return _plain(x.tolist())
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        v = float(x)
        return v if math.isfinite(v) else None
    if isinstance(x, np.bool_):
        return bool(x)
    return x


# =========================================================================== running jobs
def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def compute_name(compute: str | None) -> str:
    return "cpu" if (compute or os.environ.get("OHS_COMPUTE", "")).strip().lower() == "cpu" else "gpu"


def run_job(cache: Path, spec: SceneSpec, poses: str, compute: str, params: dict, out: Path, *, models: Path | None = None,
            depth_hook: str | None = None, depth_dir: Path | None = None, trajectory: Path | None = None,
            keep_mesh: bool = True, force: bool = False, deadline_s: float | None = None, chain: str = "frame") -> dict:
    """Run one job in a child process (with a deadline) unless its result is there; returns the result
    (the job measures its own peak memory with ``tools/memory_peaks.py``).

    Survives a kill: a result is used again only when it is a finished run's (:func:`read_result`);
    a job never runs on a recording cut off part way (``RuntimeError``); and a job still running in
    a process a killed parent left behind is waited for (its ``running`` marker), not started twice."""
    res_path = out / "result.json"
    if not force and (r := read_result(res_path)) is not None:
        return r
    rec = rec_path(cache, spec, models)
    if not recording_complete(rec, spec):
        raise RuntimeError(f"{spec.name}: the recording at {rec} is not complete (cut off part way?): render it first")
    out.mkdir(parents=True, exist_ok=True)
    running = out / "running"
    # the app chain straightens and may fuse twice more: three times the frame pass's limit
    limit = deadline_s or (600.0 + spec.frames * (8.0 if compute == "cpu" else 4.0)) * (3.0 if chain == "app" else 1.0)
    end = time.monotonic() + limit
    while running.is_file() and _alive(running.read_text(encoding="utf-8").strip() or "0"):
        if time.monotonic() > end:
            raise TimeoutError(f"{spec.name} {poses}: another process runs this job and did not end in time")
        time.sleep(15.0)
        if not force and (r := read_result(res_path)) is not None:
            return r  # the job a killed run left behind finished
    res_path.unlink(missing_ok=True)
    cmd = [sys.executable, str(Path(__file__).resolve()), "_job", "--cache", str(cache), "--rec", spec.name, "--poses", poses,
           "--out", str(out), "--params", json.dumps(params), "--keep-mesh", "1" if keep_mesh else "0"]
    if chain != "frame":
        cmd += ["--chain", chain]
    if models:
        cmd += ["--models", str(models)]
    if depth_hook:
        cmd += ["--depth-hook", depth_hook, "--depth-dir", str(depth_dir)]
    if trajectory:
        cmd += ["--trajectory", str(trajectory)]
    env = dict(os.environ)
    env.pop("OHS_COMPUTE", None)
    if compute == "cpu":
        env["OHS_COMPUTE"] = "cpu"
    env["PYTHONUNBUFFERED"] = "1"
    logf = out / "job.log"
    t0 = time.perf_counter()
    with open(logf, "w", encoding="utf-8") as fh:
        p = subprocess.Popen(cmd, cwd=str(SOFTWARE), stdout=fh, stderr=subprocess.STDOUT, env=env)
        running.write_text(str(p.pid), encoding="utf-8")
        try:
            p.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            if sys.platform == "win32":  # the venv's python.exe is a launcher: end its child too
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, check=False)
            p.kill()
            p.wait(30)
    running.unlink(missing_ok=True)
    wall = time.perf_counter() - t0
    res = read_result(res_path)
    if p.returncode != 0 or res is None:
        tail = logf.read_text(encoding="utf-8", errors="replace").splitlines()[-6:]
        res = {"error": f"job ended with {p.returncode} after {wall:.0f} s (limit {limit:.0f} s)", "log_tail": tail,
               "metrics": {}, "info": {}}
        res_path.unlink(missing_ok=True)
        write_json(out / "error.json", res)
        return res
    (out / "error.json").unlink(missing_ok=True)
    res["info"]["job_wall_s"] = round(wall, 1)
    write_json(res_path, res)
    return res


def run_dir(cache: Path, compute: str, params: dict, spec: SceneSpec, poses: str, chain: str = "frame") -> Path:
    return cache / "runs" / compute / settings_key(params, chain, poses) / spec.name / poses


def start_render_lanes(cache: Path, specs: list[SceneSpec], lanes: int, models: Path | None = None) -> list:
    """Render the recordings that are not complete in ``lanes`` background processes (``render``,
    round robin; logs in ``<cache>/logs``), so that processing can start on those that are."""
    todo = [s for s in specs if not available(cache, s, models) and s.build.get("maker") != "stretch"
            and not recording_complete(rec_path(cache, s), s)]
    procs = []
    if lanes <= 0 or not todo:
        return procs
    (cache / "logs").mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for k in range(min(lanes, len(todo))):
        names = [s.name for s in todo[k::lanes]]
        cmd = [sys.executable, str(Path(__file__).resolve()), "render", "--set", "full", "--cache", str(cache), "--scenes", *names]
        fh = open(cache / "logs" / f"render-lane{k}-{stamp}.log", "w", encoding="utf-8")  # noqa: SIM115
        procs.append(subprocess.Popen(cmd, cwd=str(SOFTWARE), stdout=fh, stderr=subprocess.STDOUT,
                                      env=dict(os.environ, PYTHONUNBUFFERED="1")))
        log(f"render lane {k}: {' '.join(names)}")
    return procs


def run_set(cache: Path, specs: list[SceneSpec], compute: str, params: dict, *, models: Path | None = None,
            poses: tuple[str, ...] = ("tracked", "true"), force: bool = False, render_lanes: int = 0,
            chain: str = "frame") -> dict[str, dict[str, dict]]:
    """Render (once) and run every recording at both pose sources; ``{rec: {poses: result}}``.

    Restartable: run it again after a kill and it carries on where it stopped (complete recordings
    and finished results are used again, a recording or job cut off part way is done again). With
    ``render_lanes`` the recordings still to render are rendered in that many background processes
    while the complete ones are processed, each as soon as it is complete."""
    key = settings_key(params, chain)
    for ps_ in poses:
        sdir = cache / "runs" / compute / settings_key(params, chain, ps_)
        sdir.mkdir(parents=True, exist_ok=True)
        write_json(sdir / "settings.json", {k: v for k, v in params.items()  # what the folder's key was made of
                                            if chain == "frame" or ps_ != "true" or not k.startswith(STRAIGHTEN_ONLY)})
    out: dict[str, dict[str, dict]] = {}
    lanes = start_render_lanes(cache, specs, render_lanes, models)
    pending = list(specs)
    end = time.monotonic() + 12 * 3600.0
    while pending:
        ready = [s for s in pending if available(cache, s, models) or recording_complete(rec_path(cache, s, models), s)]
        if not ready:
            if any(p.poll() is None for p in lanes) and time.monotonic() < end:
                time.sleep(20.0)  # the lanes are still rendering
                continue
            ready = pending[:1]  # no lane renders it (any more): render it here
        spec = ready[0]
        pending.remove(spec)
        why = available(cache, spec, models)
        if why:
            log(f"{spec.name}: {why}")
            out[spec.name] = {"skipped": why}
            continue
        try:
            render(cache, spec, say=log)
        except (RuntimeError, TimeoutError, OSError, ValueError) as e:
            log(f"{spec.name}: render FAILED {type(e).__name__}: {e}")
            out[spec.name] = {p_: {"error": f"render failed: {e}", "metrics": {}, "info": {}} for p_ in poses}
            continue
        for ps_ in poses:
            t = time.perf_counter()
            r = run_job(cache, spec, ps_, compute, params, run_dir(cache, compute, params, spec, ps_, chain), models=models,
                        force=force, chain=chain)
            out.setdefault(spec.name, {})[ps_] = r
            m = r.get("metrics", {})
            did = f"; {stages_text((r.get('info') or {}).get('chain'))}" if chain == "app" else ""
            log(f"{spec.name} {ps_} ({compute}, {key}): {r.get('error') or ''} part {fmt(m.get('part_med'))}/"
                f"{fmt(m.get('part_p95'))} mm, over 0.3 {fmt(m.get('part_over03'))}, cover {fmt(m.get('cover1'))}, "
                f"path max {fmt(m.get('traj_max'))} mm ({time.perf_counter() - t:.0f} s){did}")
    return out


# =========================================================================== the metrics and the tables
#: how a metric is judged: "min" (smaller is better), "max", "zero" (nearer 0 is better), "info" (no
#: verdict; timings and memory unless --quiet-pc). Matched by name, first match wins.
GOALS: tuple[tuple[str, str], ...] = (
    ("frame_ms", "time"), ("wall_s", "time"), ("peak_", "time"), ("faces", "info"), ("part_n", "info"),
    ("ground_med", "info"), ("frames_tracked", "max"), ("frames_lost", "min"), ("cover", "max"), ("_answered", "max"),
    ("_top_kept", "max"), ("_trusted", "max"), ("part_signed", "zero"), ("_err", "zero"), ("_offset", "zero"),
    ("laser_", "zero"), ("loops", "min"), ("", "min"))
#: the order of the rows in a table: common metrics first, then the scene's fits
ORDER = ("traj_med", "traj_max", "traj_last", "rot_med", "rot_max", "rot_last", "part_med", "part_p95", "part_over03",
         "part_signed", "part_far3", "cover1", "cover05", "frames_tracked", "frames_lost", "loops")
TIMING = ("frame_ms", "wall_s", "peak_ws_gb", "peak_gpu_shared_gb")
THIN_KEYS = ("wall0.5_err", "wall1_err", "wall2_err", "wall0.5_cover05", "wall1_cover05", "wall2_cover05",
             "sheet_flange2mm_err", "sheet_rib1mm_err", "sheet_rib3mm_err", "plate_t_err", "plate_top_kept")


def goal(metric: str, quiet_pc: bool = False) -> str:
    if metric.startswith("laser_") and metric.endswith("p95abs"):
        return "min"
    for pre, g in GOALS:
        if pre in metric:
            return ("min" if quiet_pc else "info") if g == "time" else g
    return "min"


def fmt(x: Any, nd: int | None = None) -> str:
    """A number for a table: ``n/m`` when it was not measured (never 0)."""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return NM
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        if nd is None:
            a = abs(x)
            nd = 4 if a < 0.1 else 3 if a < 10 else 1
        return f"{x:.{nd}f}"
    return str(x)


def metric_rows(metrics: list[str]) -> list[str]:
    head = [m for m in ORDER if m in metrics]
    rest = sorted(m for m in metrics if m not in ORDER and m not in TIMING)
    return head + rest + [m for m in TIMING if m in metrics]


def spread(vals: list[Any]) -> float | None:
    """The range of the measured values (max - min), None with fewer than two."""
    v = [float(x) for x in vals if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))]
    return (max(v) - min(v)) if len(v) >= 2 else None


def verdict(base: list[Any], new: list[Any], rng: float | None, g: str) -> tuple[str, float | None]:
    """The verdict of a metric over a scene's recordings (paired: ``base[i]`` and ``new[i]`` are the
    same recording): ``("better" | "worse" | "same" | "identical" | "n/m" | "info" | "better?" |
    "worse?", the mean improvement)``. "better" / "worse": the mean improvement is larger than the
    scene's spread ``rng`` and every recording moved that way; "?" marks a change where there is
    no spread to judge it by (one recording)."""
    pairs = [(float(b), float(n)) for b, n in zip(base, new) if isinstance(b, (int, float)) and isinstance(n, (int, float))
             and not isinstance(b, bool) and math.isfinite(float(b)) and math.isfinite(float(n))]
    if not pairs:
        return NM, None
    if all(b == n for b, n in pairs):
        return "identical", 0.0
    if g == "min":
        imp = [b - n for b, n in pairs]
    elif g == "max":
        imp = [n - b for b, n in pairs]
    elif g == "zero":
        imp = [abs(b) - abs(n) for b, n in pairs]
    else:
        return "info", float(np.mean([n - b for b, n in pairs]))
    m = float(np.mean(imp))
    if rng is None:
        return NO_SPREAD, m
    same_way = all(x > 0 for x in imp) if m > 0 else all(x < 0 for x in imp)
    if any(x < -rng for x in imp):  # one recording alone worse by more than the scene's whole spread
        return "worse", m
    if abs(m) > rng and same_way:
        return ("better" if m > 0 else "worse"), m
    return "same", m


def improvements(base: list[Any], new: list[Any], g: str) -> list[float | None]:
    """Each recording's own improvement (+ better) under goal ``g``; None where either is unmeasured."""
    out: list[float | None] = []
    for b, n in zip(base, new):
        if not _num(b) or not _num(n) or g not in ("min", "max", "zero"):
            out.append(None)
            continue
        b, n = float(b), float(n)
        out.append(b - n if g == "min" else n - b if g == "max" else abs(b) - abs(n))
    return out


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


#: small features (ENGINE_PLAN: mounting holes, bosses, the shaft, the sphere): each recording is
#: judged on its own, like the thin parts, so that one seed going wrong is not averaged away
SMALL_PREFIXES = ("hole25_", "h25_", "h10_", "h6_", "bore_", "br_boss_", "br_shaft_", "sphere_r_")
#: a thin part's or small feature's change on one recording that raises its alarm (mm): the stop
#: rules' 0.05 mm ("no wall gets more than 0.05 mm thicker")
ALARM_MM = 0.05
#: the alarms that make a scene worse (THINNER and LESS THIN alone are not: the reading is getting nearer the truth)
WORSE_FLAGS = ("THICKER", "LOST", "FACE LOST?", "FURTHER", "FEWER ANSWERS")


def is_small(metric: str) -> bool:
    return metric.startswith(SMALL_PREFIXES) and (metric.endswith("_err") or metric.endswith("_answered"))


def thin_flag(metric: str, base: list[Any], new: list[Any], names: list[str] | None = None) -> str:
    """A thin part's or small feature's own alarm, per recording (never averaged): a wall or plate
    more than 0.05 mm thicker and further from the truth ("THICKER"; "LESS THIN", no alarm, when a
    part read too thin moves towards its true thickness), or further from the truth on the thin side ("FURTHER"); a
    face's coverage down by more than 0.2 ("FACE LOST?"); a hole, boss or shaft reading more than
    0.05 mm further off ("FURTHER") or answering fewer clicks ("FEWER ANSWERS"); a number that was
    measured and is no longer ("LOST"). With ``names``, each flag says on which recordings."""
    flags: dict[str, list[str]] = {}
    names = names or [""] * len(base)

    def add(f, who):
        flags.setdefault(f, []).append(who)

    small = is_small(metric)
    for b, n, who in zip(base, new, names):
        if not _num(b) or not _num(n):
            if b is not None and n is None:
                add("LOST", who)
            continue
        b, n = float(b), float(n)
        if metric.endswith("_answered"):
            if n < b:
                add("FEWER ANSWERS", who)
        elif metric.endswith("_err") and small:
            if abs(n) - abs(b) > ALARM_MM:
                add("FURTHER", who)
        elif metric.endswith("_err"):
            if n - b > ALARM_MM:  # thicker: an alarm only when that is further from the truth
                add("THICKER" if abs(n) - abs(b) > ALARM_MM else "LESS THIN", who)
            elif b - n > ALARM_MM:
                add("FURTHER" if abs(n) - abs(b) > ALARM_MM else "THINNER", who)
        if ("cover05" in metric or "top_kept" in metric) and b - n > 0.2:
            add("FACE LOST?", who)
    if not any(names):
        return " ".join(sorted(flags))
    return " ".join(f"{f} ({', '.join(w)})" for f, w in sorted(flags.items()))


def stages_text(ch: dict | None) -> str:
    """What the app chain's stages did on one run, in a line: loops, the laser crossings' held-out ratio and
    whether they acted, Refine with lasers."""
    if not isinstance(ch, dict):
        return NM
    s, r = ch.get("straighten") or {}, ch.get("refine") or {}
    if not s.get("ran"):
        st = "Straighten not run"
    elif s.get("failed"):
        st = f"Straighten failed ({s['failed']})"
    else:
        c = s.get("crossings") or {}
        ratio = (c.get("holdout") or {}).get("ratio")
        st = (f"loops {s.get('loops', 0)}{' (moved)' if s.get('looped') else ''}; crossings {c.get('crossings')} "
              f"ratio {fmt(ratio, 3)} {'ACTED' if s.get('adjusted') else 'no'} ({c.get('reason')}"
              + (f", moved {fmt(c.get('moved_mm'), 2)} mm" if s.get("adjusted") else "") + ")")
    rf = ("failed" if r.get("failed") else f"ACTED {fmt(r.get('moved_pct'), 1)} %" if r.get("acted")
          else f"no ({fmt(r.get('moved_pct'), 1)} %)" if r.get("moved_pct") is not None else f"no ({r.get('why')})")
    sp = ch.get("spacetime")  # only with process.refine_from_images (ENGINE_PLAN X6)
    sx = "" if not isinstance(sp, dict) else (
        f"; images failed ({sp['failed']})" if sp.get("failed") else
        f"; images ACTED {sp.get('kept')} of {sp.get('patches')} patches, covered {fmt(sp.get('covered_share'), 3)}"
        if sp.get("acted") else f"; images no ({sp.get('why')})")
    return f"{st}{sx}; Refine {rf}; base {ch.get('base')}"


def stages_table(results: dict[str, dict[str, dict]], specs: list[SceneSpec]) -> list[str]:
    """One row per app-chain run: what Straighten and Refine with lasers did (module docstring, "Chains")."""
    lines = ["## What the app chain's stages did", "",
             "| recording | poses | loops (moved the poses?) | crossings | held-out ratio | crossings acted | moved mm | "
             "Refine acted (moved %) | base mesh |", "|---|---|---|---|---|---|---|---|---|"]
    for spec in specs:
        for ps_ in ("tracked", "true"):
            ch = ((results.get(spec.name, {}).get(ps_) or {}).get("info") or {}).get("chain")
            if not isinstance(ch, dict):
                continue
            s, r = ch.get("straighten") or {}, ch.get("refine") or {}
            c = s.get("crossings") or {}
            if s.get("ran"):
                loops = f"{s.get('loops', 0)} ({'yes' if s.get('looped') else 'no'})"
                cells = [loops, fmt(c.get("crossings")), fmt((c.get("holdout") or {}).get("ratio"), 3),
                         ("yes" if s.get("adjusted") else "no") + f" ({c.get('reason')})",
                         fmt(c.get("moved_mm"), 2) if s.get("adjusted") else "-"]
            else:
                cells = ["not run (true poses)", "-", "-", "-", "-"]
            rf = "failed" if r.get("failed") else ("yes" if r.get("acted") else "no") + f" ({fmt(r.get('moved_pct'), 1)})"
            lines.append(f"| {spec.name} | {ps_} | " + " | ".join(cells) + f" | {rf} | {ch.get('base')} |")
    return lines + [""]


def load_results(cache: Path, compute: str, params: dict, specs: list[SceneSpec],
                 chain: str = "frame") -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for spec in specs:
        for ps_ in ("tracked", "true"):
            r = read_result(run_dir(cache, compute, params, spec, ps_, chain) / "result.json")
            if r is not None:
                out.setdefault(spec.name, {})[ps_] = r
    return out


CHAIN_WORDS = {"frame": "the frame pass only (Follow the camera), then the Default recipe's edits",
               "app": "what the app's Make mesh runs: Follow the camera, Straighten (loops and laser crossings, each when "
                      "its own test allows; not at true poses), Refine with lasers, then the Default recipe's edits"}


def header(cache: Path, compute: str, params: dict, results: dict, quiet_pc: bool, title: str,
           chain: str = "frame") -> list[str]:
    """What was run: the facts the tool can know without git."""
    import ohs

    devs, path_set = set(), set()
    for rr in results.values():
        for r in rr.values():
            c = (r.get("info") or {}).get("compute") if isinstance(r, dict) else None
            if c:
                devs.add(f"depth {c.get('depth')} on {c.get('depth_device')}, tracking model {c.get('tracking_model')}")
            p = (r.get("info") or {}).get("path_settings") if isinstance(r, dict) else None
            if p:
                path_set.add(json.dumps(p, sort_keys=True))
    try:
        from tools.snapshot import device_description

        dev = device_description() if compute == "gpu" else "cpu (OHS_COMPUTE=cpu)"
    except Exception:  # noqa: BLE001
        dev = NM
    return [f"# {title}", "",
            f"- engine: ohs {ohs.__version__} ({SOFTWARE / 'ohs'}); compute path: {compute}; device: {dev}",
            f"- as the runs report it: {'; '.join(sorted(devs)) or NM}",
            f"- the path's resolved defaults, as the runs' reports give them: {' | '.join(sorted(path_set)) or NM}"
            + (" (the CPU path's defaults of 2026-11-20, decision D7: depth fix, velocity guard, ray-cast threshold 8 at "
               "full resolution)" if compute == "cpu" else ""),
            f"- date: {_dt.datetime.now().isoformat(timespec='minutes')}; cache: {cache}",
            f"- settings that differ from the defaults: {json.dumps(params, sort_keys=True) if params else 'none'}",
            f"- chain: {chain}, {CHAIN_WORDS[chain]}",
            "- scoring: the part alone, placed by its own best fit (not the table); the Default recipe on the mesh "
            "renumbered by position; coverage within 1 / 0.5 mm of the part's seen surface",
            f"- timings and memory: {'measured on a quiet PC (--quiet-pc)' if quiet_pc else 'ROUGH (other work on the PC); not judged'}",
            f"- '{NM}': not measured (never 0); spread = range over a scene's seeds", ""]


def table(results: dict[str, dict[str, dict]], specs: list[SceneSpec]) -> list[str]:
    """One block per scene: a row per metric, a column per recording, then the range over the seeds."""
    lines: list[str] = []
    groups: dict[str, list[SceneSpec]] = {}
    for s in specs:
        groups.setdefault(s.group, []).append(s)
    for gname, ss in groups.items():
        for ps_ in ("tracked", "true"):
            cols = [s for s in ss if ps_ in results.get(s.name, {})]
            if not cols:
                skipped = [s.name for s in ss if "skipped" in results.get(s.name, {})]
                if skipped and ps_ == "tracked":
                    lines += [f"## {gname}: {results[skipped[0]]['skipped']}", ""]
                continue
            errs = [f"{s.name}: {results[s.name][ps_]['error']}" for s in cols if results[s.name][ps_].get("error")]
            ms = sorted({k for s in cols for k in results[s.name][ps_].get("metrics", {})})
            thin = " (THIN PART)" if any(s.thin for s in ss) else ""
            lines += [f"## {gname}, {ps_} poses{thin}", ""]
            lines += [f"- ERROR {e}" for e in errs]
            lines.append("| metric | " + " | ".join(s.name for s in cols) + " | spread |")
            lines.append("|---|" + "---|" * (len(cols) + 1))
            for m in metric_rows(ms):
                vals = [results[s.name][ps_].get("metrics", {}).get(m) for s in cols]
                if ps_ == "true" and m.startswith(("traj_", "rot_")):
                    continue  # the poses were given
                lab = m + (" (rough)" if m in TIMING else "")
                lines.append(f"| {lab} | " + " | ".join(fmt(v) for v in vals) + f" | {fmt(spread(vals))} |")
            lines.append("")
    return lines


def compare_rows(base: dict[str, dict[str, dict]], new: dict[str, dict[str, dict]], specs: list[SceneSpec],
                 quiet_pc: bool = False) -> list[dict]:
    """Every (scene, poses, metric): the baseline's and the new mean, the change, the scene's spread in
    the baseline, the verdict and the thin-part flag."""
    rows = []
    groups: dict[str, list[SceneSpec]] = {}
    for s in specs:
        groups.setdefault(s.group, []).append(s)
    for gname, ss in groups.items():
        for ps_ in ("tracked", "true"):
            names = [s.name for s in ss if ps_ in base.get(s.name, {}) and ps_ in new.get(s.name, {})]
            if not names:
                continue
            ms = sorted({k for n in names for k in list(base[n][ps_].get("metrics", {})) + list(new[n][ps_].get("metrics", {}))})
            for m in ms:
                if ps_ == "true" and m.startswith(("traj_", "rot_")):
                    continue
                b = [base[n][ps_].get("metrics", {}).get(m) for n in names]
                v = [new[n][ps_].get("metrics", {}).get(m) for n in names]
                g = goal(m, quiet_pc)
                rng = spread(b)
                ver, imp = verdict(b, v, rng, g)
                num_b = [x for x in b if isinstance(x, (int, float)) and not isinstance(x, bool)]
                num_v = [x for x in v if isinstance(x, (int, float)) and not isinstance(x, bool)]
                watched = m in THIN_KEYS or is_small(m)
                per = improvements(b, v, g)
                worst = None
                if rng is not None and any(x is not None and x < -rng for x in per):
                    worst = names[int(np.argmin([x if x is not None else math.inf for x in per]))]
                rows.append({"scene": gname, "poses": ps_, "metric": m, "recs": names, "base": b, "new": v,
                             "base_mean": float(np.mean(num_b)) if num_b else None,
                             "new_mean": float(np.mean(num_v)) if num_v else None,
                             "change": (float(np.mean(num_v)) - float(np.mean(num_b))) if num_b and num_v else None,
                             "spread": rng, "verdict": ver, "improvement": imp, "goal": g, "worst_rec": worst,
                             "thin": m in THIN_KEYS, "small": is_small(m),
                             "flag": thin_flag(m, b, v) if watched else "",
                             "flag_recs": thin_flag(m, b, v, names) if watched else ""})
    return rows


def _alarm(r: dict) -> bool:
    return bool(r["flag"]) and any(f in r["flag"] for f in WORSE_FLAGS)


def scene_verdicts(rows: list[dict]) -> list[dict]:
    """One verdict per scene and pose source: "worse" (a metric worse beyond the scene's spread, on
    average or on one recording alone, or a thin part's or small feature's alarm on any recording),
    else "no spread to judge by" (a scene with one recording where something changed: it neither
    passes nor fails), else "better" (something better beyond the spread, nothing worse), "same"
    (changes within the spread) or "identical". ``why`` names the metrics (and recordings) that
    decided it."""
    out = []
    keys: list[tuple[str, str]] = []
    for r in rows:
        if (r["scene"], r["poses"]) not in keys:
            keys.append((r["scene"], r["poses"]))
    for scene, poses in keys:
        rs = [r for r in rows if r["scene"] == scene and r["poses"] == poses]
        recs = max((r["recs"] for r in rs), key=len)
        judged = [r for r in rs if r["goal"] != "info"]
        worse = []
        for r in judged:
            if _alarm(r):
                worse.append(f"{r['metric']} {r['flag_recs']}")
            elif r["verdict"] == "worse":
                worse.append(r["metric"] + (f" on {r['worst_rec']}" if r.get("worst_rec") else ""))
        nospread = [r["metric"] for r in judged if r["verdict"] == NO_SPREAD]
        better = [r["metric"] for r in judged if r["verdict"] == "better"]
        if worse:
            v, why = "worse", worse
        elif nospread and len(recs) == 1:
            v, why = NO_SPREAD, [f"changed: {', '.join(nospread)}"]
        elif better:
            v, why = "better", better
        elif all(r["verdict"] in ("identical", NM) for r in judged):
            v, why = "identical", []
        else:
            v, why = "same", [f"no spread for: {', '.join(nospread)}"] if nospread else []
        out.append({"scene": scene, "poses": poses, "recs": recs, "verdict": v, "why": "; ".join(why)})
    return out


def verdict_line(rows: list[dict]) -> str:
    """The one last line: better or the same on every scene, or the scenes that are worse (and why)."""
    sv = scene_verdicts(rows)
    worse = [s for s in sv if s["verdict"] == "worse"]
    if worse:
        return "**Worse on: " + ", ".join(f"{s['scene']} ({s['poses']}: {s['why']})" for s in worse) + ".**"
    unjudged = [f"{s['scene']} ({s['poses']})" for s in sv if s["verdict"] == NO_SPREAD]
    if unjudged:
        return f"**Better or the same on every scene that has a spread** ({NO_SPREAD}: {', '.join(unjudged)})."
    return "**Better or the same on every scene.**"


def compare_table(rows: list[dict]) -> list[str]:
    def score(r):
        imp = r["improvement"] or 0.0
        sp = r["spread"] or 0.0
        return abs(imp) / sp if sp > 0 else abs(imp) * 1e6

    def line(r):
        flag = r.get("flag_recs") or r["flag"]
        worst = f" (on {r['worst_rec']})" if r.get("worst_rec") and r["verdict"] == "worse" else ""
        return (f"| {r['scene']} | {r['poses']} | {r['metric']} | {fmt(r['base_mean'])} | {fmt(r['new_mean'])} | "
                f"{fmt(r['change'])} | {fmt(r['spread'])} | {r['verdict']}{worst}{' ' + flag if flag else ''} | "
                + " ".join(fmt(n) if not isinstance(n, (int, float)) or not isinstance(b, (int, float)) else fmt(n - b)
                           for b, n in zip(r["base"], r["new"])) + " |")

    head = ["| scene | poses | metric | baseline | now | change | spread | verdict | change per recording |",
            "|---|---|---|---|---|---|---|---|---|"]

    def watched(r):
        return r["thin"] or r.get("small")

    thin = [r for r in rows if watched(r)]
    worse = sorted([r for r in rows if not watched(r) and r["verdict"] == "worse"], key=score, reverse=True)
    better = sorted([r for r in rows if not watched(r) and r["verdict"] == "better"], key=score, reverse=True)
    nospread = [r for r in rows if not watched(r) and r["verdict"] == NO_SPREAD and r["goal"] != "info"]
    rest = [r for r in rows if not watched(r) and r not in worse and r not in better and r not in nospread]
    thin_bad = [r for r in thin if _alarm(r) or r["verdict"] == "worse"]
    out = [f"## THIN PARTS AND SMALL FEATURES (always shown, each recording judged on its own)"
           f"{': ' + str(len(thin_bad)) + ' ALARM(S)' if thin_bad else ''}", ""] + head
    out += [line(r) for r in thin] + [""]
    out += ["## Worse (worst first)", ""] + head + [line(r) for r in worse] + [""]
    out += ["## Better (best first)", ""] + head + [line(r) for r in better] + [""]
    out += ["## Changed where the scene has one recording (" + NO_SPREAD + "; neither passes nor fails)", ""] + head
    out += [line(r) for r in nospread] + [""]
    counts: dict[str, int] = {}
    for r in rest:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    out += ["## The rest", "", ", ".join(f"{v}: {n}" for v, n in sorted(counts.items())) or "none", ""]
    out += head + [line(r) for r in rest] + [""]
    out += ["## Per scene", "", "| scene | poses | recordings | verdict | why |", "|---|---|---|---|---|"]
    out += [f"| {s['scene']} | {s['poses']} | {len(s['recs'])} | {s['verdict']} | {s['why']} |" for s in scene_verdicts(rows)]
    out += ["", "## Summary", "", summary_line(rows), "", verdict_line(rows)]
    return out


def summary_line(rows: list[dict]) -> str:
    """The owner's rule (ENGINE_PLAN, Decisions): better, or the same within its own spread, on every
    scene, and nothing thin worse. Says that plainly, or lists the scenes that are worse (and those
    that cannot be judged). (The table's very last line is :func:`verdict_line`.)"""
    worse_scenes = sorted({f"{r['scene']} ({r['poses']})" for r in rows if r["verdict"] == "worse"})
    thin_scenes = sorted({f"{r['scene']} ({r['poses']}: {r['metric']} {r['flag'] or 'worse'})" for r in rows
                          if r["thin"] and (_alarm(r) or r["verdict"] == "worse")})
    small_scenes = sorted({f"{r['scene']} ({r['poses']}: {r['metric']} {r['flag'] or 'worse'})" for r in rows
                           if r.get("small") and not r["thin"] and (_alarm(r) or r["verdict"] == "worse")})
    unjudged = sorted({r["scene"] for r in rows if r["verdict"] == NO_SPREAD and r["goal"] != "info"})
    parts = []
    if not worse_scenes and not thin_scenes and not small_scenes:
        parts.append("**Better or the same on every scene" + (" that has a spread" if unjudged else "") + ".**")
    else:
        if worse_scenes:
            parts.append("**Worse on: " + ", ".join(worse_scenes) + ".**")
        if thin_scenes:
            parts.append("**Thin parts worse: " + "; ".join(thin_scenes) + ".**")
        if small_scenes:
            parts.append("**Small features worse: " + "; ".join(small_scenes) + ".**")
    if unjudged:
        parts.append(f"Changed with {NO_SPREAD} (not counted as passing): " + ", ".join(unjudged) + ".")
    return " ".join(parts)


# =========================================================================== baselines
def save_baseline(cache: Path, name: str, compute: str, params: dict, results: dict, specs: list[SceneSpec],
                  chain: str = "frame") -> Path:
    """A baseline file. The frame pass's has the form it always had; the app chain's also says ``chain`` and, for each
    run, ``stages`` (what Straighten and Refine with lasers did)."""
    import ohs

    p = cache / "baselines" / f"{name}.json"
    if p.exists():
        raise SystemExit(f"a baseline {name!r} is there already ({p}): saved baselines are never overwritten")
    p.parent.mkdir(parents=True, exist_ok=True)

    def one(v: dict) -> dict:
        d = {"metrics": v.get("metrics", {}), "error": v.get("error")}
        if chain != "frame":
            d["stages"] = (v.get("info") or {}).get("chain")
        return d

    body = {"name": name, "saved": _dt.datetime.now().isoformat(timespec="seconds"), "version": ohs.__version__,
            "compute": compute, "params": params, "recordings": [s.name for s in specs],
            "results": {r: {k: one(v) for k, v in rr.items() if k != "skipped"} for r, rr in results.items()}}
    if chain != "frame":
        body["chain"] = chain
    write_json(p, body)
    return p


def load_baseline(cache: Path, name: str) -> dict:
    p = Path(name) if Path(name).suffix == ".json" else cache / "baselines" / f"{name}.json"
    if not p.is_file():
        raise SystemExit(f"no baseline {name!r} ({p}); save one with: scorecard.py baseline --save {name}")
    return json.loads(p.read_text(encoding="utf-8"))


# =========================================================================== the paired replay
def replay(cache: Path, specs: list[SceneSpec], compute: str, base: dict, change: dict, *, models: Path | None = None,
           force: bool = False) -> dict:
    """Paired replay of a change to fusing alone (module docstring): ``{rec: {poses: {"A": result, "B": result}}}``,
    and ``{"_check": {rec: bool}}``, whether feeding the cached depth gives the capture run again."""
    both = dict(base, **change)
    out: dict[str, Any] = {"_check": {}}
    for spec in specs:
        why = available(cache, spec, models)
        if why:
            log(f"{spec.name}: {why}")
            continue
        render(cache, spec, say=log)
        tr = run_job(cache, spec, "tracked", compute, base, run_dir(cache, compute, base, spec, "tracked"), models=models)
        traj = run_dir(cache, compute, base, spec, "tracked") / "proc" / "trajectory.csv"
        if tr.get("error") or not traj.is_file():
            log(f"{spec.name}: no tracked trajectory ({tr.get('error')})")
            continue
        ddir = cache / "depth" / compute / spec.name
        cap = run_job(cache, spec, "true", compute, base, cache / "replay" / compute / settings_key(base) / spec.name / "capture",
                      models=models, depth_hook="capture", depth_dir=ddir, keep_mesh=False)
        if cap.get("error"):
            log(f"{spec.name}: the capture run failed ({cap['error']})")
            continue
        (ddir / "done").write_text("", encoding="utf-8")
        for label, prm in (("A", base), ("B", both)):
            for ps_, how in (("true", "true"), ("tracked", "replay-tracked")):
                d = cache / "replay" / compute / settings_key(prm) / spec.name / ps_
                r = run_job(cache, spec, how, compute, prm, d, models=models, depth_hook="feed", depth_dir=ddir,
                            trajectory=traj if ps_ == "tracked" else None, keep_mesh=False, force=force)
                out.setdefault(spec.name, {}).setdefault(ps_, {})[label] = r
        a_true = out[spec.name]["true"]["A"].get("metrics", {})
        same = all(a_true.get(k) == v for k, v in cap.get("metrics", {}).items() if k not in TIMING and not k.startswith("peak"))
        out["_check"][spec.name] = same
        log(f"{spec.name}: replayed; the cached depth gives the capture run again: {'yes' if same else 'NO'}")
    return out


def replay_table(res: dict, specs: list[SceneSpec], quiet_pc: bool = False) -> list[str]:
    """Per scene, poses and metric: each recording's own difference B - A, a verdict by their signs
    ("better on all n", "worse on all n", "mixed", "identical") and today's range across seeds (A)."""
    lines = ["| scene | poses | metric | A (mean) | B - A per recording | verdict | today's spread (A) |",
             "|---|---|---|---|---|---|---|"]
    groups: dict[str, list[SceneSpec]] = {}
    for s in specs:
        if s.name in res:
            groups.setdefault(s.group, []).append(s)
    rows = []
    for gname, ss in groups.items():
        for ps_ in ("true", "tracked"):
            recs = [s.name for s in ss if ps_ in res[s.name]]
            ms = sorted({k for n in recs for k in res[n][ps_]["A"].get("metrics", {})})
            for m in metric_rows(ms):
                if m.startswith(("traj_", "rot_")) or m in ("frames_tracked", "frames_lost"):
                    continue
                a = [res[n][ps_]["A"].get("metrics", {}).get(m) for n in recs]
                b = [res[n][ps_]["B"].get("metrics", {}).get(m) for n in recs]
                g = goal(m, quiet_pc)
                diffs = [None if not isinstance(x, (int, float)) or not isinstance(y, (int, float)) else y - x for x, y in zip(a, b)]
                ok = [d for d in diffs if d is not None]
                if not ok:
                    ver = NM
                elif all(d == 0 for d in ok):
                    ver = "identical"
                elif g in ("info",):
                    ver = "info"
                else:
                    imp = []
                    for x, y in zip(a, b):
                        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                            imp.append(x - y if g == "min" else y - x if g == "max" else abs(x) - abs(y))
                    nb, nw = sum(i > 0 for i in imp), sum(i < 0 for i in imp)
                    n0 = len(imp) - nb - nw
                    ver = (f"better on all {nb}" if nb == len(imp) else f"worse on all {nw}" if nw == len(imp)
                           else f"better on {nb}, unchanged on {n0}" if nw == 0
                           else f"worse on {nw}, unchanged on {n0}" if nb == 0
                           else f"mixed ({nb} better, {nw} worse{f', {n0} unchanged' if n0 else ''})")
                num = [x for x in a if isinstance(x, (int, float))]
                rows.append((gname, ps_, m, float(np.mean(num)) if num else None, diffs, ver, spread(a)))
    for gname, ps_, m, am, diffs, ver, sp in rows:
        lines.append(f"| {gname} | {ps_} | {m} | {fmt(am)} | {' '.join(fmt(d) for d in diffs)} | {ver} | {fmt(sp)} |")
    return lines


# =========================================================================== the placement's own check
def selfcheck(cache: Path, specs: list[SceneSpec], models: Path | None = None, err_mm: float = 1.0, err_deg: float = 0.5,
              table_mm: float = -1.0) -> list[str]:
    """The placement on the truth itself: the true surface as the frames saw it (part and ground),
    moved by a known rigid error, with the ground moved ``table_mm`` more on its own (down: a table
    a tracked scan left lower than the part). Placing the part alone must score 0 and move the part's
    points back onto their place (the ground must not pull it). A part with a symmetry (the sphere
    on its own) can be left turned about it: then the points slide along the surface, the score stays 0."""
    from tools.preset_study import seen_points

    lines = ["| recording | points (part / ground) | left over: shift mm, turn deg | part points moved, RMS mm | "
             "part median / p95 mm | signed |", "|---|---|---|---|---|---|"]
    rng = np.random.default_rng(1)
    for spec in specs:
        if available(cache, spec, models) or not recording_complete(rec_path(cache, spec, models), spec):
            continue
        sc = scene_of(spec, models)
        truth = truth_for(spec, sc)
        start, stop = stretch(spec)
        P = seen_points(sc, rec_path(cache, spec, models), start, stop, every=24, stride=12, ground=())
        P = position_sample(P, 60000)
        _, part, _ = truth.nearest(P)
        ax = rng.normal(size=3)
        ax /= np.linalg.norm(ax)
        T_err = G.make_se3(G.so3_exp(np.radians(err_deg) * ax), err_mm * ax[::-1] / np.linalg.norm(ax))
        Q = P.copy()
        Q[~part] += np.array([0.0, 0.0, table_mm])  # the table on its own, as tracking would leave it
        V = G.transform_points(G.invert_se3(T_err), Q)
        T, _ = place_part(V, np.eye(4), truth)
        D = T @ G.invert_se3(T_err)  # what is left of the error on the part
        Vs = G.transform_points(T, V)
        moved = float(np.sqrt(np.mean(np.sum((Vs[part] - P[part]) ** 2, axis=1))))
        sc_ = part_scores(Vs[part], truth)
        lines.append(f"| {spec.name} | {int(part.sum())} / {int((~part).sum())} | {np.linalg.norm(D[:3, 3]):.5f}, "
                     f"{G.rotation_angle_deg(D[:3, :3]):.5f} | {moved:.5f} | {fmt(sc_['part_med'])} / {fmt(sc_['part_p95'])} | "
                     f"{fmt(sc_['part_signed'])} |")
    return lines


# =========================================================================== the hole's two readings
def hole_study(cache: Path, specs: list[SceneSpec], compute: str, params: dict) -> list[str]:
    """Why the car panel's Ø 25 hole read large in "Presets" and small in "The fits' ± on today's
    engine": on each panel recording's kept mesh (``run`` first), the Hole tool's reading with the
    clicks 1.5 mm (the fits study) and 3 mm (the preset study) outside the rim, against the edge as the
    tool sees it on the truth (25.20) and the nominal cut (25.0); with the part placed alone and with
    the whole scene (the preset study's placement); how the tool read it (an open edge, or the level
    below the face) and that level from 0.15 to 2 mm; and, without the tool, the fused rim's profile:
    the radius at which the mesh is that far below the true panel surface."""
    import ohs.measure.region as RG
    from ohs.io.recording import RecordingReader
    from ohs.mesh.order import canonical
    from ohs.track.export import load_ply
    from tools import preset_study as ps
    from tools.calib_health_study import place

    levels = (0.15, 0.3, 0.5, 1.0, 2.0)
    lines = ["| recording | poses | open edge? | Ø, clicks 1.5 mm out (err vs 25.20 / vs 25.0) | Ø, clicks 3 mm out | "
             "Ø, whole-scene placement, 3 mm | how | Ø by level " + "/".join(f"{x:g}" for x in levels)
             + " mm | rim profile: r where 0.15 / 0.5 / 1 / 2 mm below (true edge r 12.60) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for spec in specs:
        if spec.kind != "car_panel":
            continue
        sc_ = scene_of(spec)
        h = panel_hole_world(sc_)
        panel = sc_.primitives[sc_.object_id("panel")]
        hx, hy, r0 = h["local"]
        for ps_ in ("tracked", "true"):
            d = run_dir(cache, compute, params, spec, ps_)
            if not (d / "proc" / "mesh.ply").is_file() or not (d / "result.json").is_file():
                continue
            res = json.loads((d / "result.json").read_text(encoding="utf-8"))
            m = load_ply(d / "proc" / "mesh.ply")
            c = canonical(m["vertices"], m["faces"])
            V, F, S, Fl, _ = ps.apply_ops(c.V, c.F, c.vertex_values(m["quality"]), ps.RECIPES["general"])
            T = np.asarray(res["info"]["T"])
            gt = RecordingReader(rec_path(cache, spec)).ground_truth_poses()
            tr = [(i, Tt) for i, Tt in _traj(d / "proc" / "trajectory.csv") if i in gt]
            T0 = gt[tr[0][0]] @ G.invert_se3(tr[0][1])
            Tw = place(V, T0, ps.Judge({"scene": "car_panel", "seed": spec.seed}))
            Vs, Vw = G.transform_points(T, V), G.transform_points(Tw, V)

            def clicks(dr):
                out = []
                for deg in (0.0, 120.0, 240.0):
                    t = math.radians(deg)
                    x, y = hx + (r0 + dr) * math.cos(t), hy + (r0 + dr) * math.sin(t)
                    z = float(panel.height(np.array([x]), np.array([y]))[0][0])
                    out.append(G.transform_points(panel.T_wo, np.array([[x, y, z]]))[0])
                return out

            ck, ckw = Clicker(Vs, F, S, Fl), Clicker(Vw, F, S, Fl)
            r15 = hole_reading(ck, clicks(1.5), h["diameter"], h["centre"])
            r3 = hole_reading(ck, clicks(3.0), h["diameter"], h["centre"])
            rw = hole_reading(ckw, clicks(3.0), h["diameter"], h["centre"])
            by_level = []
            old = RG.RIM_DROP_MM
            try:
                for lv in levels:
                    RG.RIM_DROP_MM = lv
                    by_level.append(hole_reading(ck, clicks(1.5), h["diameter"], h["centre"]))
            finally:
                RG.RIM_DROP_MM = old
            # the rim without the tool: in the panel's frame, the mesh's height below the true surface by radius
            L = (Vs - panel.t) @ panel.R
            rr = np.hypot(L[:, 0] - hx, L[:, 1] - hy)
            near = rr < r0 + 8.0
            dz = L[near, 2] - panel.height(L[near, 0], L[near, 1])[0]
            prof = []
            for lv in (0.15, 0.5, 1.0, 2.0):
                bins = np.arange(r0 - 8.0, r0 + 8.0, 0.25)
                med = [np.median(dz[(rr[near] >= b) & (rr[near] < b + 0.25)]) if np.any((rr[near] >= b) & (rr[near] < b + 0.25))
                       else np.nan for b in bins]
                # the outermost radius (coming in from the face) where the median height first drops below -lv
                cross = None
                for b, mz in sorted(zip(bins, med), key=lambda x: -x[0]):
                    if np.isfinite(mz) and mz < -lv:
                        cross = b + 0.125
                        break
                prof.append(cross)
            open_edge = r15["how"] if r15["how"] else NM

            def dd(x):
                return NM if x["d"] is None else f"{x['d']:.2f} ({x['d'] - h['diameter']:+.2f} / {x['d'] - 2 * r0:+.2f})"

            lines.append(f"| {spec.name} | {ps_} | {open_edge} | {dd(r15)} | {dd(r3)} | {dd(rw)} | "
                         f"{r15['how'] or ', '.join(r15['refused'])[:60]} | "
                         + " / ".join(fmt(x["d"], 2) for x in by_level) + " | "
                         + " / ".join(fmt(x, 2) for x in prof) + " |")
    return lines


# =========================================================================== the command line
def _filters(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--set", dest="set_name", choices=SETS, default="core", help="quick, core (default) or full")
    ap.add_argument("--scenes", nargs="+", default=None, help="only these scene groups or recordings (e.g. bracket240 fins)")
    ap.add_argument("--seeds", nargs="+", type=int, default=None, help="only these seeds")
    ap.add_argument("--compute", choices=("gpu", "cpu"), default=None, help="the compute path (default: the GPU when there is one)")
    ap.add_argument("--set-param", dest="set_param", action="append", default=None, metavar="SECTION.FIELD=VALUE",
                    help=f"a setting to try (sections: {', '.join(SECTIONS)}); repeatable")
    ap.add_argument("--params", default=None, help="settings to try, as a JSON file {\"fusion.voxel_mm\": 0.5}")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help=f"the cache folder (default {DEFAULT_CACHE})")
    ap.add_argument("--models", type=Path, default=None, help=f"the door's and carburettor's files (default {MODELS})")
    ap.add_argument("--quiet-pc", action="store_true", help="the PC was quiet: judge timings and memory too")
    ap.add_argument("--force", action="store_true", help="run again even where a result is there")
    ap.add_argument("--out", type=Path, default=None, help="also write the table to this file")
    ap.add_argument("--render-lanes", dest="render_lanes", type=int, default=0,
                    help="render the recordings still to render in this many background processes while the rest is processed")
    ap.add_argument("--chain", choices=CHAINS, default="frame",
                    help="frame (default): the frame pass only; app: what the app's Make mesh runs (Straighten, Refine "
                         "with lasers)")


def _emit(lines: list[str], out: Path | None, cache: Path, name: str) -> None:
    text = "\n".join(lines)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    p = out or (cache / "tables" / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.md")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    print(f"\n(table written to {p})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scorecard", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, hlp in (("run", "process and score, print the table"), ("render", "only render the recordings"),
                      ("table", "the table of what is in the cache"), ("selfcheck", "the placement on the truth itself"),
                      ("repeat", "score each recording a second time and compare (determinism)"),
                      ("rescore", "score the finished jobs again from their kept meshes (a change to the scoring alone)"),
                      ("holes", "the car panel's hole read every way (needs run first)")):
        _filters(sub.add_parser(name, help=hlp))
    b = sub.add_parser("baseline", help="run, then keep the result as a named baseline")
    _filters(b)
    b.add_argument("--save", required=True, metavar="NAME")
    c = sub.add_parser("compare", help="run, then the difference against a baseline")
    _filters(c)
    c.add_argument("name", metavar="NAME")
    r = sub.add_parser("replay", help="paired replay of a change to fusing alone (--set-param is the change)")
    _filters(r)
    r.add_argument("--base-param", dest="base_param", action="append", default=None, metavar="SECTION.FIELD=VALUE",
                   help="a setting both sides get")
    j = sub.add_parser("_job", help=argparse.SUPPRESS)
    j.add_argument("--cache", required=True)
    j.add_argument("--rec", required=True)
    j.add_argument("--poses", required=True, choices=("tracked", "true", "replay-tracked"))
    j.add_argument("--out", required=True)
    j.add_argument("--params", default="{}")
    j.add_argument("--models", default=None)
    j.add_argument("--depth-hook", dest="depth_hook", choices=("capture", "feed"), default=None)
    j.add_argument("--depth-dir", dest="depth_dir", default=None)
    j.add_argument("--trajectory", default=None)
    j.add_argument("--keep-mesh", dest="keep_mesh", type=int, default=1)
    j.add_argument("--chain", choices=CHAINS, default="frame")
    a = ap.parse_args(argv)
    if a.cmd == "_job":
        return job_main(a)
    chain = a.chain
    cache = a.cache
    cache.mkdir(parents=True, exist_ok=True)
    models = a.models
    if a.set_name == "core" and a.scenes and any(s.startswith(("door", "carb", "panel60")) for s in a.scenes):
        a.set_name = "full"
    specs = scenes_for(a.set_name, a.scenes, a.seeds)
    if not specs:
        raise SystemExit("no recording matches the filters")
    compute = compute_name(a.compute)
    params = parse_params(a.set_param, a.params)
    if chain == "frame" and any(k.startswith(STRAIGHTEN_ONLY) for k in params):
        raise SystemExit("curve.* settings act in Straighten, which only the app chain runs: add --chain app")
    if chain == "frame" and any(k.startswith(APP_ONLY) for k in params):
        raise SystemExit("process.refine_from_images and spacetime.* act in the app's Make mesh: add --chain app")
    if chain != "frame" and a.cmd in ("replay", "holes", "selfcheck"):
        raise SystemExit(f"{a.cmd} is for the frame pass only (leave out --chain)")
    t0 = time.perf_counter()
    if a.cmd == "render":
        failed = []
        for s in specs:
            why = available(cache, s, models)
            if why:
                log(f"{s.name}: {why}")
                continue
            try:
                log(f"{s.name}: ready at {render(cache, s, say=log)}")
            except Exception as e:  # noqa: BLE001 (one failed recording must not stop the others)
                failed.append(s.name)
                log(f"{s.name}: FAILED {type(e).__name__}: {e}")
        return 1 if failed else 0
    if a.cmd == "holes":
        _emit(["# The car panel's Ø 25 hole, read every way", ""] + hole_study(cache, specs, compute, params), a.out, cache, "holes")
        return 0
    if a.cmd == "rescore":
        lines = ["# Scored again from the kept meshes", "", "| recording | poses | metrics changed (timings left out) |", "|---|---|---|"]
        for s in specs:
            for ps_ in ("tracked", "true"):
                old = read_result(run_dir(cache, compute, params, s, ps_, chain) / "result.json")
                new = rescore(cache, s, compute, params, ps_, models, chain)
                if new is None:
                    continue
                mo, mn = old["metrics"], new["metrics"]
                ch = [f"{k} {fmt(mo.get(k))} -> {fmt(mn.get(k))}" for k in sorted(set(mo) | set(mn))
                      if k not in TIMING and mo.get(k) != mn.get(k)]
                lines.append(f"| {s.name} | {ps_} | {'; '.join(ch) or 'none'} |")
                log(f"{s.name} {ps_}: rescored, {len(ch)} metrics changed")
        _emit(lines, a.out, cache, "rescore")
        return 0
    if a.cmd == "selfcheck":
        _emit(["# Placing the part alone on the truth itself", ""] + selfcheck(cache, specs, models), a.out, cache, "selfcheck")
        return 0
    if a.cmd == "replay":
        if not params:
            raise SystemExit("replay wants the change to try: --set-param fusion.X=V (or pipeline.gap_guard_px=...)")
        bad = [k for k in params if not k.startswith(("fusion.", "pipeline.", "const."))]
        if bad:
            raise SystemExit(f"replay is for changes to fusing alone: {bad} would change the matcher's depth or the frames")
        base = parse_params(a.base_param)
        res = replay(cache, specs, compute, base, params, models=models, force=a.force)
        lines = header(cache, compute, dict(base, **params), {}, a.quiet_pc, "Paired replay: B = A + the change") + [
            f"- A: {json.dumps(base) if base else 'the defaults'}; B: A + {json.dumps(params)}",
            f"- the cached depth fed back gives the capture run again: "
            f"{', '.join(f'{k} {chr(10003) if v else chr(10007)}' for k, v in res['_check'].items()) or NM}", ""]
        pairs = {k: v for k, v in res.items() if k != "_check"}
        lines += replay_table(pairs, specs, a.quiet_pc)
        lines.append(f"\n{time.perf_counter() - t0:.0f} s")
        # the owner's rule on the same pairs: B against A, with A's range across seeds as the spread
        side = {lab: {n: {p: r[lab] for p, r in v.items()} for n, v in pairs.items()} for lab in ("A", "B")}
        lines += ["", "# The verdict: B against A (A's range across seeds as each scene's spread)", ""]
        lines += compare_table(compare_rows(side["A"], side["B"], specs, a.quiet_pc))
        _emit(lines, a.out, cache, "replay")
        return 0
    if a.cmd == "repeat":
        lines = ["# The same recording scored twice", "", "| recording | poses | differing metrics (timings left out) |", "|---|---|---|"]
        for s in specs:
            if available(cache, s, models):
                continue
            render(cache, s, say=log)
            for ps_ in ("tracked", "true"):
                x = run_job(cache, s, ps_, compute, params, run_dir(cache, compute, params, s, ps_, chain), models=models,
                            chain=chain)
                y = run_job(cache, s, ps_, compute, params, run_dir(cache, compute, params, s, ps_ + "-repeat", chain),
                            models=models, keep_mesh=False, force=True, chain=chain)
                mx, my = x.get("metrics", {}), y.get("metrics", {})
                diff = [k for k in sorted(set(mx) | set(my)) if k not in TIMING and mx.get(k) != my.get(k)]
                text = ", ".join(f"{k}: {mx.get(k)} / {my.get(k)}" for k in diff) or "none: identical"
                lines.append(f"| {s.name} | {ps_} | {text} |")
        _emit(lines, a.out, cache, "repeat")
        return 0
    if a.cmd == "baseline":  # before hours of work: a saved baseline is never overwritten
        bp = cache / "baselines" / f"{a.save}.json"
        if bp.exists():
            raise SystemExit(f"a baseline {a.save!r} is there already ({bp}): choose another name")
    if a.cmd == "table":
        res = load_results(cache, compute, params, specs, chain)
    else:
        res = run_set(cache, specs, compute, params, models=models, force=a.force, render_lanes=a.render_lanes, chain=chain)
    title ={"run": "Scorecard", "table": "Scorecard (from the cache)", "baseline": f"Scorecard, saved as baseline {getattr(a, 'save', '')}",
             "compare": f"Scorecard against baseline {getattr(a, 'name', '')}"}[a.cmd]
    lines = header(cache, compute, params, res, a.quiet_pc, title, chain)
    if a.cmd == "baseline":
        missing = [f"{s.name} {p_}" for s in specs if "skipped" not in res.get(s.name, {})
                   for p_ in ("tracked", "true") if (res.get(s.name, {}).get(p_) or {}).get("error") or p_ not in res.get(s.name, {})]
        if missing:
            lines.append(f"- baseline NOT saved: {len(missing)} job(s) did not finish ({', '.join(missing)}); "
                         "run the same command again to carry on\n")
        else:
            p = save_baseline(cache, a.save, compute, params, res, specs, chain)
            lines.append(f"- baseline saved: {p}\n")
        fps = sorted({(r.get("info") or {}).get("engine_code") or "before fingerprints" for rr in res.values()
                      for k, r in rr.items() if k != "skipped" and isinstance(r, dict) and not r.get("error")})
        if len(fps) > 1:
            lines.append(f"- NOTE: the results came from {len(fps)} versions of the engine's code: {', '.join(fps)}\n")
    if a.cmd == "compare":
        bl = load_baseline(cache, a.name)
        lines.append(f"- baseline: {a.name} (saved {bl.get('saved')}, ohs {bl.get('version')}, {bl.get('compute')}, "
                     f"settings {json.dumps(bl.get('params')) or 'none'})")
        if bl.get("compute") != compute:
            lines.append(f"- NOTE: the baseline ran on the {bl.get('compute')} path, this on the {compute} path")
        if bl.get("chain", "frame") != chain:
            lines.append(f"- NOTE: the baseline ran the {bl.get('chain', 'frame')} chain, this the {chain} chain: the "
                         f"difference is what the chain adds")
        lines.append(f"- {len(specs)} recordings, {time.perf_counter() - t0:.0f} s")
        lines.append("")
        if chain == "app":
            lines += stages_table(res, specs)
        lines += compare_table(compare_rows(bl["results"], res, specs, a.quiet_pc))  # its last line: the verdict
    else:
        if chain == "app":
            lines += stages_table(res, specs)
        lines += table(res, specs)
        lines.append(f"\n{len(specs)} recordings, {time.perf_counter() - t0:.0f} s")
    _emit(lines, a.out, cache, a.cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
