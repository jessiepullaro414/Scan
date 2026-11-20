"""Which settings a preset could hold matter for which object (FEATURE_PLAN P1).

    python tools/preset_study.py quick <work folder>      # the small default run (two recordings)
    python tools/preset_study.py render <work folder> [--rec NAME ...]
    python tools/preset_study.py spread <work folder> [--rec NAME ...]   # today's settings on every recording
    python tools/preset_study.py sweep <work folder> [--rec NAME ...] [--setting NAME ...] [--value V ...]
    python tools/preset_study.py recipes <work folder> [--rec NAME ...] [--recipe NAME ...]
    python tools/preset_study.py table <work folder>       # the Markdown tables, from the cache
    python tools/preset_study.py all <work folder>         # render, spread, sweep (seed 0) and recipes

A preset ("What are you scanning?") would set a handful of values. This study varies each of them
alone around today's defaults ("General": ``ohs.server.studio.process_params`` with the default
``Config()``, then the built-in "Default" recipe) and measures what changes:

* the settings of :data:`SETTINGS`: the voxel size of the mesh, the quality (Preview / Standard /
  Best), thin walls (auto / on / off), the gap guard (px at 1280 px width) and the matcher's depth
  range; each is a whole ``process_recording`` run, the same as Make mesh without Straighten and
  Refine (they change none of these short scans);
* the edits after meshing (:data:`RECIPES`: fill size, smoothing strength and where), applied to
  the mesh of the default run, as the app's recipes apply them.

Every run is judged against the simulator's true shape (:class:`Judge`): the mesh's error on the
object, the share of the object's seen surface that the mesh reaches (coverage), the fits the
validation uses for that scene (the bracket's measures of ``tools/calib_health_study.py``, the car
panel's Ø 25 hole with the Hole tool, the sphere's radius, the thin plate's and the thin walls'
thickness) and the time. The yardstick is the spread from scan to scan: the same scene and settings
on three recordings (other seeds: other noise and another hand path; for the door and the
carburetor, other stretches of the one long recording). A setting is worth a preset only where it
moves a result by more than that spread (the table marks those with ``*``).

Scenes (:data:`RECORDINGS`): the sphere, the car panel and the bracket as in the validation, a
0.5 mm plate lying on the table (``ohs.sim.scene.thin_plate``), three thin walls standing on the
table (0.5, 1 and 2 mm, :func:`fins_scene`, seen from both sides), and three stretches each of the
door trim panel and the carburetor of VALIDATION.md "Large and detailed parts" (their recordings
and models in ``~/.ohs``; ``--models`` moves them). The rendered recordings are 60 fps, so that
Standard (30 frames per second of scanning) and Best (every frame) differ, as on the scanner; the
door's and the carburetor's are 30 fps, where the two are the same.

Everything goes into the work folder: ``recs/<name>`` (the recordings, rendered once),
``runs/<rec>/<setting>=<value>.json`` (one run; a run that is there is never computed again),
``out/<rec>`` (the default run's output, kept for the recipes), ``recipes/<rec>/<recipe>.json`` and
``tables.md``. Timings are wall-clock seconds of ``process_recording``; compare them only as
ratios to the default run on the same recording, measured next to it.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

SOFTWARE = Path(__file__).resolve().parents[1]
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))

from ohs import geometry as G  # noqa: E402

__all__ = ["RECORDINGS", "SETTINGS", "DEFAULTS", "RECIPES", "fins_scene", "scene_of", "Judge", "process_params_for",
           "apply_ops", "judge_mesh", "spread", "marks", "tables"]

MODELS = Path.home() / ".ohs"  # the door's and the carburetor's recordings and models (--models)
WAIT_S = 0.0  # --wait: seconds to wait for a recording another process renders (0: render it here)

# --------------------------------------------------------------------------- the scenes
FIN_THICKNESS = (0.5, 1.0, 2.0)  # mm, the three thin walls of fins_scene
FIN_HALF = (20.0, 12.5)  # half length (along x) and half height of a wall, mm
FIN_X = (-50.0, 0.0, 50.0)  # their centres along x
FIN_BASE = 8.0  # mm, the base plate they stand on


def fins_scene(seed: int = 0, thickness: tuple[float, ...] = FIN_THICKNESS):
    """Thin walls standing in a row along x on an 8 mm base plate (160 x 50 mm) with a block beside
    it, their faces to +y and -y: sheet metal and shims on edge, 40 mm long and 25 mm tall, like the
    flanges of a stamped bracket. (Walls standing on the bare table, even with blocks beside them,
    did not hold the tracker's translation over the first frames: 11-19 mm.) The scan sweeps from
    one side over the walls' ends to the other (about +x, +-60 deg), so both faces of every wall
    are seen."""
    from ohs.sim.scene import Box, NoiseAlbedo, Scene, thin_plate

    table = thin_plate(seed=seed).primitives[0]  # the plate scene's table
    metal = NoiseAlbedo(mean=0.62, contrast=0.03, scale_mm=2.0, seed=seed + 3)
    prims = [table, Box(G.make_se3(np.eye(3), [0.0, 0.0, FIN_BASE / 2]), (80.0, 25.0, FIN_BASE / 2), metal, name="base"),
             Box(G.make_se3(np.eye(3), [0.0, 40.0, 10.0]), (20.0, 12.0, 10.0), metal, name="block")]
    for k, (x, t) in enumerate(zip(FIN_X, thickness)):
        prims.append(Box(G.make_se3(np.eye(3), [x, 0.0, FIN_BASE + FIN_HALF[1]]), (FIN_HALF[0], t / 2.0, FIN_HALF[1]),
                         metal, name=f"wall_{k}"))
    return Scene(prims, name="fins", target=(10.0, 0.0, 10.0), radius=90.0,
                 meta={"thickness_mm": list(thickness),
                       "trajectory": {"distance": (210.0, 270.0), "elevation_deg": (35.0, 60.0), "sweep_deg_s": 30.0,
                                      "azimuth0_deg": 0.0}})


#: the recordings: rendered ones (``scene``, ``seed``, ``frames`` at ``fps``; 30 fps as the validation's
#: scans, so Standard processes every frame) and stretches of the door's and the carburetor's long
#: recordings (``source`` under --models, frames ``start``..``stop``). ``bracket-60fps`` is the
#: bracket's seed-0 motion at the scanner's 60 fps, where Standard takes every 2nd frame and Best all.
RECORDINGS: dict[str, dict] = {}
for _k in range(3):
    RECORDINGS[f"sphere-s{_k}"] = {"scene": "sphere_on_plane", "seed": _k, "frames": 36, "fps": 30.0}
    RECORDINGS[f"panel-s{_k}"] = {"scene": "car_panel", "seed": _k, "frames": 60, "fps": 30.0}
    RECORDINGS[f"bracket-s{_k}"] = {"scene": "bracket", "seed": _k, "frames": 90, "fps": 30.0}
    RECORDINGS[f"plate-s{_k}"] = {"scene": "thin_plate", "seed": _k, "frames": 60, "fps": 30.0, "thickness": 0.5}
    RECORDINGS[f"fins-s{_k}"] = {"scene": "fins", "seed": _k, "frames": 120, "fps": 30.0}
    # half of one of the door's five passes (282 frames each), a quarter of the carburetor's orbit
    RECORDINGS[f"door-w{_k}"] = {"scene": "door", "source": "simscans/rec-door", "model": "models/car-door/car door.OBJ",
                                 "orient": "flat", "start": 282 * _k, "stop": 282 * _k + 141, "fps": 30.0}
    RECORDINGS[f"carb-w{_k}"] = {"scene": "carb", "source": "simscans/rec-carb",
                                 "model": "models/carburetor/0_karburator-500k.obj", "orient": "z-up",
                                 "start": 270 * _k, "stop": 270 * _k + 135, "fps": 30.0}
RECORDINGS["bracket-60fps"] = {"scene": "bracket", "seed": 0, "frames": 180, "fps": 60.0}
SCENES = ("sphere", "panel", "bracket", "plate", "fins", "door", "carb")


def scene_kind(rec: str) -> str:
    return rec.split("-")[0]


def in_spread(rec: str) -> bool:
    """The recordings that make a scene's spread: its seeds or stretches (not ``bracket-60fps``)."""
    return rec.rsplit("-", 1)[-1][:1] in ("s", "w") and rec.rsplit("-", 1)[-1][1:].isdigit()


def scene_of(spec: dict, models: Path | None = None):
    """The :class:`~ohs.sim.scene.Scene` of a recording spec (the same object rendered and judged)."""
    from ohs.sim.scene import make_scene, mesh_on_floor, thin_plate

    if spec["scene"] == "thin_plate":
        return thin_plate(seed=int(spec["seed"]), thickness=float(spec.get("thickness", 1.0)))
    if spec["scene"] == "fins":
        return fins_scene(int(spec["seed"]))
    if "model" in spec:
        return mesh_on_floor(str((models or MODELS) / spec["model"]), orient=spec["orient"], lift_mm=0.0, name=spec["scene"])
    return make_scene(spec["scene"], int(spec["seed"]))


def rec_path(work: Path, name: str, models: Path | None = None) -> Path:
    spec = RECORDINGS[name]
    return (models or MODELS) / spec["source"] if "source" in spec else work / "recs" / name


def make_recording(work: Path, name: str, image_size: tuple[int, int] = (1280, 800)) -> Path:
    """Render one recording of :data:`RECORDINGS` (once: an existing one is kept)."""
    from ohs.sim.datasets import make_scan_recording

    spec = RECORDINGS[name]
    path = rec_path(work, name)
    if "source" in spec or ((path / "frames.csv").is_file() and (path / "done").is_file()):
        return path
    if WAIT_S > 0:  # another process renders it: wait for its "done", never render it here too
        end = time.monotonic() + WAIT_S
        while not (path / "done").is_file():
            if time.monotonic() > end:
                raise TimeoutError(f"{name} was not rendered within {WAIT_S:.0f} s")
            time.sleep(20.0)
        return path
    shutil.rmtree(path, ignore_errors=True)
    make_scan_recording(path, scene=scene_of(spec), n_frames=int(spec["frames"]), fps=float(spec["fps"]),
                        image_size=image_size, seed=int(spec["seed"]), realistic=True, depth_every=0)
    (path / "done").write_text("", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- the settings
#: what a preset could set, each with its values; ``DEFAULTS`` is today's ("General")
SETTINGS: dict[str, tuple] = {
    "voxel": (0.5, 0.75, 1.0, 1.5, 2.0),  # mm, the mesh's voxel (Fuse); Standard's default 1 mm
    "quality": ("preview", "standard", "best"),
    "thin_walls": ("auto", "on", "off"),
    "gap_guard": (0.0, 4.0, 8.0, 12.0),  # px at 1280 px width; 0 = off
    # mm, the matcher's (DepthParams min / max). 150-600 is the widest the GPU matcher takes on the Radeon 680M:
    # 100-600 needs 368 disparities and the GPU allows 256 work-items per group, so it runs on the CPU
    "depth_range": ("200-350", "150-450", "150-600"),
}
DEFAULTS: dict[str, Any] = {"voxel": 1.0, "quality": "standard", "thin_walls": "auto", "gap_guard": 8.0,
                            "depth_range": "150-450"}


def _value(setting: str, text: str) -> Any:
    if setting in ("voxel", "gap_guard"):
        return float(text)
    return str(text)


def process_params_for(setting: str, value: Any, image_width: int = 1280, start: int = 0, stop: int | None = None):
    """``ProcessParams`` of the app's Make mesh (``process_params`` with the default config,
    markerless) with one setting changed; ``setting`` ``"default"`` changes nothing."""
    from ohs.app.config import Config
    from ohs.app.pipeline import default_depth_params, default_fusion_params, effective_scale
    from ohs.server.studio import process_params

    quality = str(value) if setting == "quality" else "standard"
    pp = process_params(Config(), "markerless", quality, image_width)
    pipe = dataclasses.replace(pp.pipeline)
    if setting == "voxel":
        pipe.fusion = dataclasses.replace(default_fusion_params(effective_scale(image_width)), voxel_mm=float(value))
    elif setting == "thin_walls":
        pipe.thin_walls = str(value)
    elif setting == "gap_guard":
        pipe.gap_guard_px = float(value)
    elif setting == "depth_range":
        lo, hi = (float(x) for x in str(value).split("-"))
        pipe.depth = dataclasses.replace(default_depth_params(pipe.scale), min_depth_mm=lo, max_depth_mm=hi)
    elif setting not in ("default", "repeat", "quality"):
        raise ValueError(f"unknown setting {setting!r}; settings: {', '.join(SETTINGS)}")
    return dataclasses.replace(pp, pipeline=pipe, export=("ply",), health=False, start=int(start), stop=stop)


# --------------------------------------------------------------------------- the recipes
FLOATERS = {"op": "remove_floaters", "params": {"min_size_mm": 5.0, "min_fraction": 0.01}}


def _fill(mm: float) -> list[dict]:
    return [{"op": "fill_holes", "params": {"max_hole_mm": float(mm)}}] if mm > 0 else []


def _smooth(method: str, strength: float, where: str) -> dict:
    return {"op": "smooth", "params": {"method": method, "strength": strength, "keep_edges": True,
                                       "flat_only": where == "flat", "away_from_edges": where == "off_edges"}}


#: the edits after meshing, each a list of ops as a recipe holds them. "general" is the built-in
#: "Default" recipe (what Make mesh does); "body-panel" and "bracket" are the two other built-ins.
#: Fill sizes are a hole's perimeter in mm (``ohs.mesh.fill_holes``): the car panel's Ø 25 mm hole
#: is about 75 mm round.
RECIPES: dict[str, list[dict]] = {
    "general": [FLOATERS, *_fill(5)],
    "fill-0": [FLOATERS],
    "fill-15": [FLOATERS, *_fill(15)],
    "fill-30": [FLOATERS, *_fill(30)],
    "fill-80": [FLOATERS, *_fill(80)],
    "bilateral-25-off-edges": [FLOATERS, *_fill(5), _smooth("bilateral", 0.25, "off_edges")],
    "bilateral-50-off-edges": [FLOATERS, *_fill(5), _smooth("bilateral", 0.5, "off_edges")],
    "bilateral-50-everywhere": [FLOATERS, *_fill(5), _smooth("bilateral", 0.5, "everywhere")],
    "taubin-50-everywhere": [FLOATERS, *_fill(5), _smooth("taubin", 0.5, "everywhere")],
    "taubin-50-flat-only": [FLOATERS, *_fill(5), _smooth("taubin", 0.5, "flat")],
    "body-panel": [FLOATERS, *_fill(15), _smooth("bilateral", 0.5, "off_edges")],
    "bracket": [FLOATERS, *_fill(5), _smooth("bilateral", 0.25, "off_edges")],
}


def apply_ops(V: np.ndarray, F: np.ndarray, S: np.ndarray | None, ops: list[dict]):
    """Run a recipe's ops as the app does (``ProcessManager._apply``): ``(V, F, S, filled, n_after_floaters)``;
    ``S`` and the filled flags follow the mesh (``ohs.mesh.carry_sigma`` / ``carry_filled``)."""
    from ohs import mesh as M

    Fl = None
    n_common = len(V)
    for o in ops:
        op, p = o["op"], o["params"]
        by = None
        if op == "remove_floaters":
            V2, F2, by = M.remove_floaters(V, F, **p, return_kept=True)
            n_common = len(V2)
        elif op == "fill_holes":
            V2, F2 = M.fill_holes(V, F, p["max_hole_mm"])
        elif op == "smooth":
            V2, F2 = M.smooth(V, F, p["method"], p["strength"], p.get("keep_edges", True),
                              flat_only=bool(p.get("flat_only")), away_from_edges=bool(p.get("away_from_edges")))
        else:
            raise ValueError(f"op {op!r} is not in this study")
        S = M.carry_sigma(op, V, F, S, V2, by)
        Fl = M.carry_filled(op, V, F, Fl, V2, F2, by)
        V, F = V2, F2
    return V, F, S, Fl, n_common


# --------------------------------------------------------------------------- the truth
class Judge:
    """The true scene of a recording: distances, the nearest true point (for placing a scan),
    which points are on the object (not the table or floor) and which are near a true edge, and the
    object's seen surface (rays cast from the true camera poses of the processed frames)."""

    def __init__(self, spec: dict, models: Path | None = None) -> None:
        self.spec = spec
        self.sc = scene_of(spec, models)
        self.kind = spec["scene"]
        self.sdf = None
        self.rs = None
        try:
            from tools.calib_health_study import Truth

            self.sdf = Truth(self.sc)
            self.ground = [k for k, p in enumerate(self.sdf.prims) if p.name in ("table", "floor")]
        except ValueError:  # a height field or a mesh: as triangles
            self._mesh_truth()

    # a true triangle mesh (the car panel's height field, or the door's / carburetor's model) and the floor
    def _mesh_truth(self) -> None:
        import open3d as o3d

        if "mesh" in self.sc.meta:
            V, F = self.sc.meta["mesh"]
        else:
            V, F = _panel_mesh(self.sc)
        half = 3000.0
        Vf = np.array([[-half, -half, 0], [half, -half, 0], [half, half, 0], [-half, half, 0]], dtype=np.float64)
        Ff = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64) + len(V)
        self.n_obj = len(F)
        self.Vt, self.Ft = np.asarray(V, dtype=np.float64), np.asarray(F, dtype=np.int64)
        rs = o3d.t.geometry.RaycastingScene()
        rs.add_triangles(o3d.core.Tensor(np.vstack([V, Vf]).astype(np.float32)),
                         o3d.core.Tensor(np.vstack([F, Ff]).astype(np.uint32)))
        self.rs = rs

    def _mesh_query(self, P: np.ndarray):
        import open3d as o3d

        ans = self.rs.compute_closest_points(o3d.core.Tensor(np.ascontiguousarray(P, dtype=np.float32)))
        Q = ans["points"].numpy().astype(np.float64)
        return Q, ans["primitive_ids"].numpy().astype(np.int64) < self.n_obj

    def closest(self, P: np.ndarray) -> np.ndarray:
        if self.sdf is not None:
            return self.sdf.closest(P)
        return self._mesh_query(P)[0]

    def dist(self, P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Unsigned distance to the nearest true surface, and whether that surface is the object's."""
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
        if self.sdf is not None:
            D = self.sdf.prim_sdf(P)
            A = np.abs(D)
            own = np.argmin(A, axis=1)
            return A[np.arange(len(P)), own], ~np.isin(own, self.ground)
        Q, obj = self._mesh_query(P)
        return np.linalg.norm(P - Q, axis=1), obj

    def edges(self, P: np.ndarray, near: float = 1.5) -> np.ndarray | None:
        """Points within ``near`` mm of a true edge (a solid's own edge, or where two solids or a solid
        and the table meet); the car panel's crease, border and hole rim. None for the mesh scenes."""
        from ohs.sim.scene import Box, Cylinder

        P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
        if self.sdf is not None:
            A = np.abs(self.sdf.prim_sdf(P))
            order = np.argsort(A, axis=1)
            own = order[:, 0]
            out = A[np.arange(len(P)), order[:, 1]] < near if A.shape[1] > 1 else np.zeros(len(P), bool)
            for k, p in enumerate(self.sdf.prims):
                sel = own == k
                if not sel.any():
                    continue
                pl = (P[sel] - p.t) @ p.R
                if isinstance(p, Box):
                    out[sel] |= (np.abs(pl) > p.half - near).sum(axis=1) >= 2
                elif isinstance(p, Cylinder):
                    out[sel] |= (np.abs(pl[:, 2]) > p.half_height - near) & (np.hypot(pl[:, 0], pl[:, 1]) > p.radius - near)
            return out
        if self.kind != "car_panel":
            return None
        panel = self.sc.primitives[self.sc.object_id("panel")]
        pl = (P - panel.t) @ panel.R
        x0, x1, y0, y1 = panel.bounds
        e = (np.abs(pl[:, 1] - 30.0) < 3.0 + near) | (pl[:, 0] < x0 + 2 * near) | (pl[:, 0] > x1 - 2 * near) \
            | (pl[:, 1] < y0 + 2 * near) | (pl[:, 1] > y1 - 2 * near)
        for hx, hy, hr in panel.holes:
            e |= np.abs(np.hypot(pl[:, 0] - hx, pl[:, 1] - hy) - hr) < 2 * near
        return e

    def seen(self, rec: Path, start: int = 0, stop: int | None = None, every: int = 12, stride: int = 8,
             depth_range: tuple[float, float] = (150.0, 450.0), n_max: int = 200_000) -> np.ndarray:
        """Points of the object's true surface that the processed frames saw (the left camera's rays
        from the true poses, every ``every``-th frame, every ``stride``-th pixel, within the depth range)."""
        from ohs.io.recording import RecordingReader

        reader = RecordingReader(rec)
        st = reader.calibration().stereo
        K = np.asarray(st.K_rect, dtype=np.float64)
        w, h = st.image_size
        gt = reader.ground_truth_poses()
        idx = [i for i in sorted(gt) if i >= start and (stop is None or i < stop)][::every]
        vs, us = np.mgrid[stride // 2:h:stride, stride // 2:w:stride]
        d = np.stack([(us.ravel() - K[0, 2]) / K[0, 0], (vs.ravel() - K[1, 2]) / K[1, 1], np.ones(us.size)], axis=1)
        d /= np.linalg.norm(d, axis=1, keepdims=True)
        out = []
        ground = {k for k, p in enumerate(self.sc.primitives) if p.name in ("table", "floor")}
        for i in idx:
            T = gt[i]
            dw = d @ T[:3, :3].T
            hit = self.sc.cast(T[:3, 3], dw, surface=False)
            z = hit.t * d[:, 2]
            ok = np.isfinite(hit.t) & (z >= depth_range[0]) & (z <= depth_range[1]) & ~np.isin(hit.obj, list(ground))
            out.append(T[:3, 3] + hit.t[ok, None] * dw[ok])
        P = np.concatenate(out) if out else np.zeros((0, 3))
        if len(P) > n_max:
            P = P[np.random.default_rng(0).choice(len(P), n_max, replace=False)]
        return P


def _panel_mesh(sc, step: float = 0.4) -> tuple[np.ndarray, np.ndarray]:
    """The car panel's true surface as triangles (world, mm), its hole left out."""
    panel = sc.primitives[sc.object_id("panel")]
    x0, x1, y0, y1 = panel.bounds
    xs = np.arange(x0, x1 + 1e-9, step)
    ys = np.arange(y0, y1 + 1e-9, step)
    X, Y = np.meshgrid(xs, ys)
    Z = panel.height(X.ravel(), Y.ravel())[0]
    V = G.transform_points(panel.T_wo, np.stack([X.ravel(), Y.ravel(), Z], axis=1))
    nx = len(xs)
    i, j = np.meshgrid(np.arange(len(ys) - 1), np.arange(nx - 1), indexing="ij")
    a = (i * nx + j).ravel()
    F = np.concatenate([np.stack([a, a + 1, a + nx + 1], 1), np.stack([a, a + nx + 1, a + nx], 1)])
    c = np.stack([X.ravel(), Y.ravel()], 1)[F].mean(axis=1)
    keep = np.ones(len(F), bool)
    for hx, hy, hr in panel.holes:
        keep &= np.hypot(c[:, 0] - hx, c[:, 1] - hy) > hr
    return V, F[keep]


# --------------------------------------------------------------------------- judging one mesh
def _stats(x: np.ndarray) -> dict:
    if not len(x):
        return {"n": 0}
    return {"n": int(len(x)), "median": float(np.median(x)), "p95": float(np.percentile(x, 95))}


def _mesh_distance(Vs: np.ndarray, F: np.ndarray, P: np.ndarray) -> np.ndarray:
    import open3d as o3d

    if not len(F) or not len(P):
        return np.full(len(P), np.inf)
    rs = o3d.t.geometry.RaycastingScene()
    rs.add_triangles(o3d.core.Tensor(Vs.astype(np.float32)), o3d.core.Tensor(np.asarray(F).astype(np.uint32)))
    return rs.compute_distance(o3d.core.Tensor(P.astype(np.float32))).numpy().astype(np.float64)


def _sphere_fit(P: np.ndarray) -> tuple[np.ndarray, float]:
    A = np.c_[2.0 * P, np.ones(len(P))]
    sol, *_ = np.linalg.lstsq(A, (P * P).sum(axis=1), rcond=None)
    c = sol[:3]
    return c, float(math.sqrt(max(sol[3] + c @ c, 0.0)))


def _plane_gap(Pa: np.ndarray, Pb: np.ndarray, min_points: int = 50) -> float | None:
    from ohs.measure import fit_plane

    if len(Pa) < min_points or len(Pb) < min_points:
        return None
    a, b = fit_plane(Pa), fit_plane(Pb)
    return abs(float(a.normal @ (b.point - a.point)))


def scene_fits(judge: Judge, Vs: np.ndarray, F: np.ndarray, S: np.ndarray | None, filled: np.ndarray | None,
               seen: np.ndarray, cover: np.ndarray) -> dict:
    """The fits the validation uses for the scene, on the placed mesh (simulator world)."""
    kind = judge.kind
    out: dict[str, Any] = {}
    if kind == "sphere_on_plane":
        c0 = np.asarray(judge.sc.meta["sphere_center"], dtype=np.float64)
        r0 = float(judge.sc.meta["sphere_radius"])
        d = np.abs(np.linalg.norm(Vs - c0, axis=1) - r0)
        sel = (d < 1.5) & (Vs[:, 2] > 8.0)  # the cap away from where it meets the table
        if sel.sum() > 200:
            c, r = _sphere_fit(Vs[sel])
            out.update(radius_err=r - r0, centre_err=float(np.linalg.norm(c - c0)))
    elif kind == "bracket":
        from tools.calib_health_study import BRACKET_TRUE, measures

        m = measures(Vs, judge.sdf)
        errs = {k: (None if m.get(k) is None else float(m[k] - BRACKET_TRUE[k])) for k in BRACKET_TRUE}
        out.update({f"{k}_err": v for k, v in errs.items()})
        vals = [abs(v) for v in errs.values() if v is not None]
        out["fit_max_abs"] = max(vals) if vals else None
        out["fits_answered"] = len(vals)
    elif kind == "car_panel":
        out.update(_hole_fits(judge, Vs, F, S, filled))
    elif kind == "thin_plate":
        out.update(_plate_fits(judge, Vs, seen, cover))
    elif kind == "fins":
        out.update(_fin_fits(judge, Vs, seen, cover))
    return out


def _hole_fits(judge: Judge, Vs, F, S, filled, out_mm: float = 3.0) -> dict:
    """The car panel's Ø 25 hole with the Hole tool, three clicks 3 mm outside the true rim."""
    import open3d as o3d

    from ohs.measure import fit_at

    panel = judge.sc.primitives[judge.sc.object_id("panel")]
    hx, hy, hr = panel.holes[0]
    c_true = G.transform_points(panel.T_wo, np.array([[hx, hy, float(panel.height(np.array([hx]), np.array([hy]))[0][0])]]))[0]
    rs = o3d.t.geometry.RaycastingScene()
    rs.add_triangles(o3d.core.Tensor(Vs.astype(np.float32)), o3d.core.Tensor(np.asarray(F).astype(np.uint32)))
    diam, centre, refused = [], [], []
    for ang in (0.0, 120.0, 240.0):
        a = math.radians(ang)
        x, y = hx + (hr + out_mm) * math.cos(a), hy + (hr + out_mm) * math.sin(a)
        p = G.transform_points(panel.T_wo, np.array([[x, y, float(panel.height(np.array([x]), np.array([y]))[0][0])]]))
        ans = rs.compute_closest_points(o3d.core.Tensor(p.astype(np.float32)))
        face = int(ans["primitive_ids"].numpy()[0])
        q = ans["points"].numpy()[0].astype(np.float64)
        try:
            r = fit_at(Vs, F, face, q, "hole", sigma=S, filled=filled)
            diam.append(float(r.fit.diameter_mm))
            centre.append(float(np.linalg.norm(np.asarray(r.fit.center) - c_true)))
        except ValueError as e:
            refused.append(str(e)[:80])
    return {"hole_d": float(np.median(diam)) if diam else None, "hole_d_err": float(np.median(diam)) - 2 * hr if diam else None,
            "hole_centre_err": float(np.median(centre)) if centre else None, "hole_answered": len(diam),
            "hole_refused": refused}


def _plate_fits(judge: Judge, Vs, seen, cover) -> dict:
    """The 0.5 mm plate: its thickness as the mesh reads it (its top above the table round it) and
    the share of its seen top that the mesh holds within half its thickness."""
    from ohs.sim.scene import Box

    t = float(judge.sc.meta["thickness_mm"])
    boxes = [p for p in judge.sc.primitives if isinstance(p, Box)]

    def inside(P, grow):
        m = np.zeros(len(P), bool)
        for b in boxes:
            pl = (P - b.t) @ b.R
            m |= np.all(np.abs(pl[:, :2]) < b.half[:2] + grow, axis=1)
        return m

    top = inside(Vs, -2.0) & (Vs[:, 2] > -1.0) & (Vs[:, 2] < t + 1.0)
    ring = inside(Vs, 10.0) & ~inside(Vs, 3.0) & (np.abs(Vs[:, 2]) < 1.0)
    out: dict[str, Any] = {"thickness_read": None}
    if top.sum() > 50 and ring.sum() > 50:
        out["thickness_read"] = float(np.median(Vs[top, 2]) - np.median(Vs[ring, 2]))
    st = inside(seen, -1.0) & (seen[:, 2] > t - 0.01)
    out["top_kept"] = float(np.mean(cover[st] < t / 2.0)) if st.any() else None
    return out


def _fin_fits(judge: Judge, Vs, seen, cover) -> dict:
    """Each thin wall: its thickness from its two faces (a plane each, away from its ends, foot and
    top) and the share of its seen faces within 0.5 mm of the mesh."""
    from ohs.sim.scene import Box

    out: dict[str, Any] = {}
    walls = [p for p in judge.sc.primitives if isinstance(p, Box) and p.name.startswith("wall")]
    for p in walls:
        t = 2.0 * float(p.half[1])
        key = f"t{t:g}".replace(".", "_")
        pl = (Vs - p.t) @ p.R
        core = (np.abs(pl[:, 0]) < p.half[0] - 4.0) & (np.abs(pl[:, 2]) < p.half[2] - 3.5) & (np.abs(pl[:, 1]) < t / 2 + 2.0)
        out[f"{key}_read"] = _plane_gap(Vs[core & (pl[:, 1] > 0)], Vs[core & (pl[:, 1] <= 0)])
        ps = (seen - p.t) @ p.R
        own = (np.abs(ps[:, 0]) < p.half[0] - 1.0) & (np.abs(ps[:, 2]) < p.half[2] - 1.0) & (np.abs(np.abs(ps[:, 1]) - t / 2) < 0.05)
        out[f"{key}_cover05"] = float(np.mean(cover[own] < 0.5)) if own.any() else None
    return out


def judge_mesh(judge: Judge, Vs: np.ndarray, F: np.ndarray, S: np.ndarray | None, filled: np.ndarray | None,
               seen: np.ndarray) -> dict:
    """Error, coverage and fits of a placed mesh (simulator world)."""
    d, obj = judge.dist(Vs)
    cover = _mesh_distance(Vs, F, seen)
    res = {"faces": int(len(F)), "object": _stats(d[obj]), "floor": _stats(d[~obj]),
           "far_share": float(np.mean(d[obj] > 3.0)) if obj.any() else None,
           "cover1": float(np.mean(cover < 1.0)) if len(seen) else None,
           "cover05": float(np.mean(cover < 0.5)) if len(seen) else None, "seen_n": int(len(seen))}
    res["fits"] = scene_fits(judge, Vs, F, S, filled, seen, cover)
    return res


# --------------------------------------------------------------------------- one run
_JUDGES: dict[str, Judge] = {}
_SEEN: dict[tuple, np.ndarray] = {}


def _judge(rec: str) -> Judge:
    key = rec if not rec.startswith(("door", "carb")) else scene_kind(rec)
    spec = RECORDINGS[rec]
    if key not in _JUDGES or _JUDGES[key].spec.get("seed") != spec.get("seed"):
        _JUDGES[key] = Judge(spec)
    return _JUDGES[key]


def _seen(judge: Judge, rec: str, path: Path, depth_range: tuple[float, float]) -> np.ndarray:
    spec = RECORDINGS[rec]
    key = (rec, depth_range)
    if key not in _SEEN:
        _SEEN[key] = judge.seen(path, int(spec.get("start", 0)), spec.get("stop"), depth_range=depth_range,
                                every=6 if "source" in spec else 12)
    return _SEEN[key]


def _placement(path: Path, out: Path, judge: Judge, V: np.ndarray) -> np.ndarray:
    """The scan's world in the simulator's: the first tracked frame's true pose, refined by ICP of
    the scan onto the true surface (``calib_health_study.place``), so a rigid misplacement is not shape."""
    from ohs.io.recording import RecordingReader
    from ohs.track.posegraph import read_trajectory
    from tools.calib_health_study import place

    gt = RecordingReader(path).ground_truth_poses()
    i0, T_est0 = next((i, T) for i, _, ok, T in read_trajectory(out / "trajectory.csv") if ok and i in gt)
    return place(V, gt[i0] @ G.invert_se3(T_est0), judge)


def _value_key(setting: str, value: Any) -> str:
    return f"{setting}={value:g}" if isinstance(value, float) else f"{setting}={value}"


def run_case(work: Path, rec: str, setting: str, value: Any, *, keep: bool = False) -> dict:
    """Process recording ``rec`` with one setting changed and judge it after the General recipe; cached."""
    from ohs.app.process import process_recording
    from ohs.track.export import load_ply

    cache = work / "runs" / rec / f"{_value_key(setting, value)}.json"
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    spec = RECORDINGS[rec]
    path = rec_path(work, rec)
    params = process_params_for(setting, value, 1280, int(spec.get("start", 0)), spec.get("stop"))
    out = work / "out" / rec if keep else work / "tmp" / f"{rec}-{_value_key(setting, value)}"
    shutil.rmtree(out, ignore_errors=True)
    row: dict[str, Any] = {"rec": rec, "scene": scene_kind(rec), "setting": setting, "value": value,
                           "compute": os.environ.get("OHS_COMPUTE", "") or "auto", "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    t0 = time.perf_counter()
    try:
        rep = process_recording(path, out, params=params)
    except Exception as e:  # noqa: BLE001 - a setting the engine refuses (memory, a bad range) is a result
        row.update(error=f"{type(e).__name__}: {e}"[:300], process_s=round(time.perf_counter() - t0, 1))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(row, indent=1), encoding="utf-8")
        shutil.rmtree(out, ignore_errors=True)
        return row
    row["process_s"] = round(time.perf_counter() - t0, 1)
    t1 = time.perf_counter()
    judge = _judge(rec)
    d = load_ply(out / "mesh.ply")
    V, F, S = d["vertices"], d["faces"], d["quality"]
    T = _placement(path, out, judge, V)
    V2, F2, S2, Fl, _ = apply_ops(V, F, S, RECIPES["general"])
    rng = params.pipeline.depth
    depth_range = (150.0, 450.0) if rng is None else (float(rng.min_depth_mm), float(rng.max_depth_mm))
    seen = _seen(judge, rec, path, (150.0, 450.0))
    row["judge"] = judge_mesh(judge, G.transform_points(T, V2), F2, S2, Fl, seen)
    row["placement"] = T.tolist()
    gt = rep.get("ground_truth") or {}
    row["frames"] = {"processed": rep["frames"]["processed"], "tracked": rep["frames"]["tracked"]}
    row["traj"] = {"median_mm": (gt.get("trans_mm") or {}).get("median"), "max_mm": (gt.get("trans_mm") or {}).get("max"),
                   "max_deg": (gt.get("rot_deg") or {}).get("max")}
    row["detail_mode"] = (rep.get("tracking") or {}).get("detail_mode", rep.get("detail_mode"))
    row["device"] = (rep.get("params") or {}).get("compute")
    row["depth_range"] = list(depth_range)
    row["judge_s"] = round(time.perf_counter() - t1, 1)
    if not keep:
        shutil.rmtree(out, ignore_errors=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(row, indent=1, default=float), encoding="utf-8")
    return row


def run_recipe(work: Path, rec: str, name: str) -> dict:
    """One recipe on the default run's mesh of ``rec``, judged; cached. Also compares each vertex
    with the General recipe's (smoothing moves vertices; the floaters' removal keeps their order)."""
    from ohs.track.export import load_ply

    cache = work / "recipes" / rec / f"{name}.json"
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    base = run_case(work, rec, "default", "-", keep=True)
    out = work / "out" / rec
    if "error" in base or not (out / "mesh.ply").is_file():
        return {"rec": rec, "recipe": name, "error": base.get("error", "no default mesh")}
    judge = _judge(rec)
    d = load_ply(out / "mesh.ply")
    T = np.asarray(base["placement"], dtype=np.float64)
    seen = _seen(judge, rec, rec_path(work, rec), (150.0, 450.0))
    t0 = time.perf_counter()
    V, F, S, Fl, n = apply_ops(d["vertices"], d["faces"], d["quality"], RECIPES[name])
    secs = time.perf_counter() - t0
    Vs = G.transform_points(T, V)
    row = {"rec": rec, "scene": scene_kind(rec), "recipe": name, "edit_s": round(secs, 1),
           "judge": judge_mesh(judge, Vs, F, S, Fl, seen)}
    if name != "general":
        Vg, _, _, _, ng = apply_ops(d["vertices"], d["faces"], d["quality"], RECIPES["general"])
        m = min(n, ng)
        dg, obj = judge.dist(G.transform_points(T, Vg[:m]))
        dn, _ = judge.dist(Vs[:m])
        ch = dn - dg
        e = judge.edges(G.transform_points(T, Vg[:m]))
        row["vs_general"] = {"worse_share": float(np.mean(ch[obj] > 0.05)), "better_share": float(np.mean(ch[obj] < -0.05)),
                             "edge_worse_share": None if e is None or not (e & obj).any() else float(np.mean(ch[e & obj] > 0.05)),
                             "edge_mean_before": None if e is None or not (e & obj).any() else float(np.mean(dg[e & obj])),
                             "edge_mean_after": None if e is None or not (e & obj).any() else float(np.mean(dn[e & obj])),
                             "flat_median_before": float(np.median(dg[obj & ~e])) if e is not None and (obj & ~e).any() else None,
                             "flat_median_after": float(np.median(dn[obj & ~e])) if e is not None and (obj & ~e).any() else None}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(row, indent=1, default=float), encoding="utf-8")
    return row


# --------------------------------------------------------------------------- the tables
#: the numbers shown per scene: (label, path into a run's row, digits)
COMMON = (("err med", ("judge", "object", "median"), 3), ("err p95", ("judge", "object", "p95"), 3),
          ("cover 1mm", ("judge", "cover1"), 4), ("traj max", ("traj", "max_mm"), 2))
FITS: dict[str, tuple] = {
    "sphere": (("radius err", ("judge", "fits", "radius_err"), 3),),
    "bracket": (("shaft Ø err", ("judge", "fits", "shaft_d_err"), 3), ("upright t err", ("judge", "fits", "upright_thickness_err"), 3),
                ("base h err", ("judge", "fits", "base_height_err"), 3), ("fit max", ("judge", "fits", "fit_max_abs"), 3)),
    "panel": (("hole Ø err", ("judge", "fits", "hole_d_err"), 2), ("holes answered", ("judge", "fits", "hole_answered"), 0)),
    "plate": (("thickness read", ("judge", "fits", "thickness_read"), 3), ("top kept", ("judge", "fits", "top_kept"), 3)),
    "fins": (("0.5 wall", ("judge", "fits", "t0_5_read"), 2), ("1 wall", ("judge", "fits", "t1_read"), 2),
             ("2 wall", ("judge", "fits", "t2_read"), 2), ("0.5 cover", ("judge", "fits", "t0_5_cover05"), 3)),
    "door": (), "carb": (),
}


def _get(d: Any, path: tuple) -> Any:
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def columns(scene: str) -> tuple:
    return COMMON + FITS.get(scene, ())


def _load(work: Path) -> dict[str, dict[str, dict]]:
    runs: dict[str, dict[str, dict]] = {}
    for p in sorted((work / "runs").glob("*/*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        runs.setdefault(r["rec"], {})[p.stem] = r
    return runs


def spread(runs: dict, scene: str) -> dict[str, dict]:
    """Each shown number with today's settings over the scene's recordings: values, range (max - min)."""
    recs = [r for r in RECORDINGS if scene_kind(r) == scene and in_spread(r) and "default=-" in runs.get(r, {})]
    out = {}
    for label, path, _ in columns(scene):
        vals = [_get(runs[r]["default=-"], path) for r in recs]
        v = [x for x in vals if isinstance(x, (int, float))]
        out[label] = {"values": dict(zip(recs, vals)), "range": (max(v) - min(v)) if len(v) >= 2 else None}
    return out


def marks(value: Any, base: Any, rng: float | None) -> str:
    """``*`` when ``value`` differs from ``base`` (the default on the same recording) by more than the
    scan-to-scan range; ``=`` when it is the same number; else nothing."""
    if not isinstance(value, (int, float)) or not isinstance(base, (int, float)):
        return "" if value == base else "*"
    if value == base:
        return "="
    if rng is not None and abs(value - base) > rng:
        return "*"
    return ""


def _f(x: Any, nd: int) -> str:
    if x is None:
        return "–"
    if isinstance(x, (int, float)):
        return f"{x:.{nd}f}"
    return str(x)


def tables(work: Path) -> str:
    runs = _load(work)
    lines = ["# Preset study (FEATURE_PLAN P1)", "", "`*`: moved by more than the spread from scan to scan; `=`: the same number.", ""]
    for scene in SCENES:
        recs = [r for r in RECORDINGS if scene_kind(r) == scene and r in runs]
        if not recs:
            continue
        cols = columns(scene)
        sp = spread(runs, scene)
        lines += [f"## {scene}", "", "### Spread from scan to scan (today's settings)", "",
                  "| Recording | " + " | ".join(c[0] for c in cols) + " | s |", "|---|" + "---|" * (len(cols) + 1)]
        for r in recs:
            b = runs[r].get("default=-")
            if b:
                lines.append(f"| {r} | " + " | ".join(_f(_get(b, c[1]), c[2]) for c in cols) + f" | {b.get('process_s')} |")
        lines.append("| range | " + " | ".join(_f(sp[c[0]]["range"], c[2]) for c in cols) + " | |")
        lines += ["", "### One setting at a time", "",
                  "| Recording | Setting | " + " | ".join(c[0] for c in cols) + " | time ratio |",
                  "|---|---|" + "---|" * (len(cols) + 1)]
        for r in recs:
            b = runs[r].get("default=-")
            if not b:
                continue
            for key in sorted(k for k in runs[r] if k != "default=-"):
                x = runs[r][key]
                if "error" in x:
                    lines.append(f"| {r} | {key} | " + x["error"][:60] + " |" + " |" * len(cols))
                    continue
                cells = []
                for label, path, nd in cols:
                    v = _get(x, path)
                    cells.append(_f(v, nd) + marks(v, _get(b, path), sp[label]["range"]))
                ratio = (x.get("process_s") or 0) / b["process_s"] if b.get("process_s") else None
                lines.append(f"| {r} | {key} | " + " | ".join(cells) + f" | {_f(ratio, 2)} |")
        rr = sorted((work / "recipes").glob(f"{scene}-*/*.json"))
        if rr:
            lines += ["", "### Edits after meshing (on the default run's mesh)", "",
                      "| Recording | Recipe | " + " | ".join(c[0] for c in cols[:3] + cols[4:])
                      + " | worse > 0.05 | better | edges worse | flat median after (before) | edit s |",
                      "|---|---|" + "---|" * (len(cols) + 4)]
            byrec: dict[str, dict] = {}
            for p in rr:
                x = json.loads(p.read_text(encoding="utf-8"))
                byrec.setdefault(x["rec"], {})[x["recipe"]] = x
            for r, xs in sorted(byrec.items()):
                g = xs.get("general")
                for name in RECIPES:
                    x = xs.get(name)
                    if x is None or "error" in x:
                        continue
                    cells = []
                    for label, path, nd in cols[:3] + cols[4:]:
                        v = _get(x, path)
                        cells.append(_f(v, nd) + ("" if g is None or name == "general" else marks(v, _get(g, path), sp[label]["range"])))
                    vg = x.get("vs_general") or {}
                    flat = f"{_f(vg.get('flat_median_after'), 4)} ({_f(vg.get('flat_median_before'), 4)})" if vg else ""
                    lines.append(f"| {r} | {name} | " + " | ".join(cells) + f" | {_f(vg.get('worse_share'), 3)} | "
                                 f"{_f(vg.get('better_share'), 3)} | {_f(vg.get('edge_worse_share'), 3)} | {flat} | {x.get('edit_s')} |")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- the command line
def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _brief(r: dict) -> str:
    if "error" in r:
        return f"ERROR {r['error']}"
    j = r.get("judge", {})
    return (f"{r.get('process_s')} s, err {_f(_get(j, ('object', 'median')), 3)}/{_f(_get(j, ('object', 'p95')), 3)}, "
            f"cover {_f(j.get('cover1'), 4)}, traj {_f(_get(r, ('traj', 'max_mm')), 2)}, fits "
            f"{json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in (j.get('fits') or {}).items() if k != 'hole_refused'})}")


def _values(setting: str, given: list[str] | None, rec: str) -> list[Any]:
    vals = [_value(setting, v) for v in given] if given else [v for v in SETTINGS[setting] if v != DEFAULTS[setting]]
    if setting == "thin_walls" and not given:
        vals = ["off"]  # "on" is "auto" in a markerless scan (PipelineParams.thin_walls): checked once on demand
    if setting == "quality" and float(RECORDINGS[rec].get("fps", 30.0)) < 45.0:
        vals = [v for v in vals if v != "best"]  # 30 fps: Best processes what Standard does
    return vals


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=("quick", "render", "spread", "sweep", "recipes", "repeat", "table", "all"))
    ap.add_argument("work", type=Path)
    ap.add_argument("--rec", nargs="+", default=None, help="recordings (default: see the module docstring)")
    ap.add_argument("--setting", nargs="+", default=None, choices=tuple(SETTINGS), help="settings of the sweep (default: all)")
    ap.add_argument("--value", nargs="+", default=None, help="only these values of the one --setting")
    ap.add_argument("--recipe", nargs="+", default=None, choices=tuple(RECIPES), help="recipes (default: all)")
    ap.add_argument("--models", type=Path, default=None, help=f"the folder of the door's and carburetor's files (default {MODELS})")
    ap.add_argument("--wait", type=float, default=0.0,
                    help="wait up to this many seconds for a recording that another process renders, instead of rendering it")
    a = ap.parse_args(argv)
    globals()["WAIT_S"] = float(a.wait)
    if a.models is not None:
        globals()["MODELS"] = a.models
    work = a.work
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    if a.what == "quick":
        recs = a.rec or ["plate-s0", "bracket-s0"]
        for r in recs:
            make_recording(work, r)
            log(f"{r} default: {_brief(run_case(work, r, 'default', '-', keep=True))}")
            log(f"{r} voxel 2: {_brief(run_case(work, r, 'voxel', 2.0))}")
            log(f"{r} body-panel recipe: {json.dumps(run_recipe(work, r, 'body-panel').get('vs_general'))}")
    seed0 = [r for r in RECORDINGS if r.endswith(("-s0", "-w0")) or not in_spread(r)]
    if a.what in ("render", "all"):
        for r in a.rec or list(RECORDINGS):
            t = time.perf_counter()
            make_recording(work, r)
            log(f"{r}: ready ({time.perf_counter() - t:.0f} s)")
    if a.what in ("spread", "all"):
        for r in a.rec or list(RECORDINGS):
            make_recording(work, r)
            log(f"{r} default: {_brief(run_case(work, r, 'default', '-', keep=True))}")
    if a.what == "repeat":  # the same run again: is the GPU path deterministic?
        for r in a.rec or ["bracket-s0"]:
            x, y = run_case(work, r, "default", "-", keep=True), run_case(work, r, "repeat", "-")
            same = json.dumps(x.get("judge"), sort_keys=True) == json.dumps(y.get("judge"), sort_keys=True)
            log(f"{r} repeat: {'identical' if same else 'DIFFERENT'}; {_brief(y)}")
    if a.what in ("sweep", "all"):
        for r in a.rec or seed0:
            make_recording(work, r)
            run_case(work, r, "default", "-", keep=True)
            for s in a.setting or (["quality"] if not in_spread(r) else list(SETTINGS)):
                for v in _values(s, a.value, r):
                    log(f"{r} {s}={v}: {_brief(run_case(work, r, s, v))}")
    if a.what in ("recipes", "all"):
        for r in a.rec or list(RECORDINGS):
            for name in a.recipe or list(RECIPES):
                x = run_recipe(work, r, name)
                log(f"{r} recipe {name}: {_brief(x) if 'judge' in x else x.get('error')}; {json.dumps(x.get('vs_general'))}")
    text = tables(work)
    (work / "tables.md").write_text(text, encoding="utf-8")
    if a.what == "table":
        print(text)
    log(f"done in {time.perf_counter() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
