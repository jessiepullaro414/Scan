"""The overall size of a large part: a 1.3 m plain panel scanned in passes (ENGINE_PLAN X8, steps 1 and 2).

Step 2 (tags as anchors in Straighten, ``ohs.track.tag_anchors``): the same panel and sweep with AprilTags on the table
(:data:`TAG_LAYOUTS`, ``render --tags tags``), each recording processed twice tracked, Straighten's tags off and on
(``process REC tracked --anchors off|on``: folders ``tracked-anchors-off`` / ``-on``), and at true poses twice (``true``,
and ``--world-yaw 20``: the same true poses in a world turned and moved a fraction of a voxel, which shows the edge
reading's own precision). ``measure REC`` (seed 3 only) measures the two weights the tags' fit needs. Judged by the
sliced edges (:func:`end_edge_slice`) against the same seed's true-pose reading, the camera path's end to end and the
passes' slide (:func:`verdict2`, the owner's stop rule).

    python tools/large_part_study.py budget WORK [--frames 20]        # render 20 frames of seed 0, time them
    python tools/large_part_study.py render WORK SEED [--frames N]    # one recording -> WORK/recs/s<SEED>[-n<N>]
    python tools/large_part_study.py process WORK REC {tracked,true}  # what Make mesh runs -> WORK/runs/<REC>/<poses>
    python tools/large_part_study.py judge WORK REC                   # the numbers -> WORK/runs/<REC>/judge.json
    python tools/large_part_study.py table WORK                       # the table of what is judged

(run from ``software/``). A measurement, not an engine change: nothing in the engine or its defaults is touched; the
process step wraps two engine classes in its own process only to count what they do (below). Every step is one
process; render and process are heavy (the graphics card, every processor core): run them one at a time
(``VALIDATION.md``, X8). The order per seed: ``render``, ``process tracked``, ``process true``, ``judge``; ``table``.

**The scene** (:func:`panel_scene`, built from the simulator's existing pieces): a plain painted panel, one surface
(``HeightField`` "panel"), 1300 x 400 mm, gently doubly curved and convex up (radii 6 m along, 2 m across), lying on a
2 x 2 m dark table on its four corners (1 mm above it); two Ø10 holes on the centre line 30 mm from each end, 14 mm
above the table; paint and table as the car panel's. Nothing along its length to hold on to but its edges.

**The scan** (:func:`sweep`): ``SweepTrajectory`` as the door's in ``VALIDATION.md`` "Large and detailed parts"
(distance 240-290 mm, elevation 60-75°, the same hand noise), here 3 passes along the panel, out, back, out, the aim
travelling 1200 mm, the passes 130 mm apart, 56.5 s at about 100 mm/s, rendered as the scorecard renders (1280 x
800, realistic) at :data:`FPS`.

**process**: ``tools/scorecard.py``'s ``app_chain`` (Follow the camera, Straighten, Refine with lasers, the server's
own parameters, Standard quality, 1 mm mesh detail), tracked, or fused at the true poses (the ceiling, and each
method's own bias). Counted on the way (the job's process only): every move of the graphics card's rolling 768 mm
tracking cube (``GpuTsdfVolume.follow``), the mesh model's brick pool (``ConfidenceTsdf._slots_for``), the warnings
logged, the memory (a run past :data:`MAX_WS_GB` is stopped).

**judge** (the module's metrics; each mesh after the Default recipe, as the scorecard): (1) end to end by the Hole
tool, clicked as the scorecard clicks the car panel's hole, each end's clicks placed by that end alone; (2) end to
end from the mesh's two short edges (their middles, read against a local fit of the panel); (3) end to end from the
camera path: placed on the true path by the frames at one end (in position; the turn is the whole path's), how far
the frames at the other end put the panel;
(4) the bow, from the mesh placed by its two ends (along the normal) and from its long edges (sideways), and from the
path; (5) the slide between passes at revisit points found from the truth, before and after Straighten; the path's
error, the mesh against the true panel, time, memory, the app's warnings and Straighten's report, the cube.

**Fails** (fixed before any result, ``VALIDATION.md`` X8): over :data:`LIMIT_PCT` % end to end on any seed, by the Hole
tool where it answers or by the edges against the same seed's true-pose reading.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

SOFTWARE = Path(__file__).resolve().parents[1]
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))

from ohs import geometry as G  # noqa: E402
from tools import scorecard as SC  # noqa: E402

# --------------------------------------------------------------------------- the set-up (fixed beforehand)
HALF_L, HALF_W = 650.0, 200.0  # the panel: 1300 x 400 mm
RX, RY = 6000.0, 2000.0  # its radii along and across (convex up)
CORNER_Z = 1.0  # its corners rest this far above the table
H0 = CORNER_Z + HALF_L ** 2 / (2 * RX) + HALF_W ** 2 / (2 * RY)  # its top above the table
HOLE_X, HOLE_R = 620.0, 5.0  # Ø10 holes on the centre line, 30 mm from each end
TABLE_HALF = 1000.0
PASSES, TRAVEL, SPACING = 3, 1200.0, 130.0
AIM_Z = 30.0
DURATION = 56.5  # s: one pass 18.85 s, about 100 mm/s at its middle
FPS = 10.0
SEEDS = (0, 1, 2)
LIMIT_PCT = 0.05
MAX_WS_GB = 15.0  # a process past this is stopped (this PC has 19.8 GB)
END_X = 450.0  # a frame or vertex "at an end": |x| beyond this; "in the middle": |x| below MID_X
MID_X = 150.0
CLICK_DR = 1.5  # mm outside the hole's rim, as the scorecard clicks the car panel's hole
AHEAD_MM = 265.0


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def write_json(path: Path, obj: Any) -> None:
    SC.write_json(path, SC._plain(obj))


# --------------------------------------------------------------------------- scene and scan
def height(x, y):
    """The panel's top in its own frame (z up from H0) and its slopes."""
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    return -x * x / (2 * RX) - y * y / (2 * RY), -x / RX, -y / RY


#: step 2's tags on the table (fixed before rendering, NOTES.md): 25 mm tag36h11 with a 6 mm white margin; ids in
#: the order listed. "tags": both long sides, 300 mm apart, 30 mm off the panel's edge, and five across each short end
#: (a pass sees the side tags only on its own side, the middle pass none: the ends are what every pass sees at its
#: turns, and what joins the passes); "oneside": the near side only, and the ends; "spaced600": the sides 600 mm
#: apart, and the ends; "panel": on the panel itself, along its centre line and its two long edges, 300 mm apart.
TAG_MM = 25.0
_SIDES = [(x, y) for y in (-230.0, 230.0) for x in (-600.0, -300.0, 0.0, 300.0, 600.0)]
_ENDS = [(x, y) for x in (-700.0, 700.0) for y in (-120.0, -60.0, 0.0, 60.0, 120.0)]
TAG_LAYOUTS: dict[str, list[tuple[float, float]]] = {
    "tags": _SIDES + _ENDS,
    "oneside": [q for q in _SIDES if q[1] < 0] + _ENDS,
    "spaced600": [q for q in _SIDES if q[0] in (-600.0, 0.0, 600.0)] + _ENDS,
    "panel": [(x, y) for y in (-150.0, 0.0, 150.0) for x in (-450.0, -150.0, 150.0, 450.0)],
    # step 3 (NOTES.md, fixed before rendering): the near long side only, 150 mm apart (two or three in view where that
    # side is in view, so frames see tags spaced along the length), and the same ends as "tags"
    "row150": [(x, -230.0) for x in np.arange(-600.0, 600.1, 150.0).tolist()] + _ENDS,
}


def _tag_decals(layout: str, flip_y: bool) -> list:
    from ohs.sim.scene import Decal, apriltag_texture

    out = []
    for m, (x, y) in enumerate(TAG_LAYOUTS[layout]):
        # the table's own frame has y the other way round (it is seen from its local -z side)
        cy = y if flip_y else -y
        out.append(Decal(apriltag_texture(m, TAG_MM), 10, (x, cy), angle=0.3 * m, margin_mm=TAG_MM / 4, tag_id=m, flip_y=flip_y))
    return out


def panel_scene(seed: int = 0, tags: str | None = None):
    """The scene (module docstring); ``tags``: a layout of :data:`TAG_LAYOUTS` (step 2), on the table or the panel."""
    from ohs.sim.scene import DecalAlbedo, HeightField, NoiseAlbedo, Scene, _table, marker_corners_world

    T = G.make_se3(np.eye(3), [0.0, 0.0, H0])
    zmin = float(height(HALF_L, HALF_W)[0])
    paint = NoiseAlbedo(mean=0.72, contrast=0.015, scale_mm=4.0, seed=seed)
    floor = NoiseAlbedo(mean=0.3, contrast=0.08, scale_mm=5.0, seed=seed + 1)
    on_panel = tags == "panel"
    decals = _tag_decals(tags, on_panel) if tags else []
    panel = HeightField(T, height, (-HALF_L, HALF_L, -HALF_W, HALF_W), (zmin, 0.0), [(-HOLE_X, 0.0, HOLE_R), (HOLE_X, 0.0, HOLE_R)],
                        DecalAlbedo(paint, decals) if on_panel else paint, name="panel")
    table = _table(0.0, TABLE_HALF, DecalAlbedo(floor, decals) if decals and not on_panel else floor, name="table")
    markers = {dc.tag_id: marker_corners_world(panel if on_panel else table, dc) for dc in decals}
    return Scene([table, panel], name="panel1300", target=(0.0, 0.0, AIM_Z), radius=700.0, markers=markers,
                 meta={"panel_T_wo": T.tolist(), "tags": tags})


def scene_for(rec: Path):
    """The scene a recording was rendered from (its seed, and its tags: ``tags.json``, step 2)."""
    m = json.loads((rec / "meta.json").read_text(encoding="utf-8"))
    tj = rec / "tags.json"
    tags = json.loads(tj.read_text(encoding="utf-8")).get("layout") if tj.is_file() else None
    return panel_scene(int(m["seed"]), tags)


def sweep(seed: int):
    from ohs.sim.nominal import NOMINAL_TIME_OFFSET, nominal_calibration
    from ohs.sim.trajectory import SweepTrajectory

    calib = nominal_calibration(SC.IMAGE_SIZE, realistic=True)
    return SweepTrajectory(target=np.array([0.0, 0.0, AIM_Z]), t0=100.0 + NOMINAL_TIME_OFFSET, duration=DURATION, passes=PASSES,
                           length_mm=TRAVEL, spacing_mm=SPACING, heading_deg=0.0, distance=(240.0, 290.0), elevation_deg=(60.0, 75.0),
                           roll_deg=8.0, aim_jitter_mm=4.0, tremor_mm=0.08, tremor_deg=0.03, seed=seed, baseline=calib.stereo.baseline)


def n_frames(fps: float = FPS) -> int:
    return int(round(DURATION * fps))


def pass_of(i: int, fps: float) -> int:
    return min(int((i / fps) // (DURATION / PASSES)), PASSES - 1)


def spec_for(rec: Path) -> SC.SceneSpec:
    m = json.loads((rec / "meta.json").read_text(encoding="utf-8"))
    return SC.SceneSpec(rec.name, "panel1300", "large_panel", int(m["seed"]), int(m["n_frames"]), {"maker": "large"})


def render(work: Path, seed: int, frames: int | None = None, fps: float = FPS, tags: str | None = None) -> Path:
    from ohs.sim.datasets import make_scan_recording

    n = frames or n_frames(fps)
    name = f"s{seed}" + (f"-n{frames}" if frames is not None else "") + (f"-{tags}" if tags else "")
    path = work / "recs" / name
    if (path / "done").is_file():
        return path
    shutil.rmtree(path, ignore_errors=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    t = time.perf_counter()
    sc = panel_scene(seed, tags)
    make_scan_recording(path, scene=sc, n_frames=n, fps=fps, trajectory=sweep(seed), overwrite=True,
                        image_size=SC.IMAGE_SIZE, realistic=True, depth_every=0, seed=seed)
    if tags:
        write_json(path / "tags.json", {"layout": tags, "tag_mm": TAG_MM,
                                        "corners": {str(i): np.asarray(X).tolist() for i, X in sc.markers.items()}})
    secs = time.perf_counter() - t
    size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    write_json(path / "done", {"frames": n, "fps": fps, "render_s": round(secs, 1), "bytes": size})
    log(f"{path.name}: {n} frames in {secs:.0f} s ({secs / n:.2f} s a frame), {size / 2 ** 30:.2f} GB")
    return path


def budget(work: Path, frames: int) -> None:
    path = render(work, 0, frames)
    d = json.loads((path / "done").read_text(encoding="utf-8"))
    per, size = d["render_s"] / frames, d["bytes"] / frames
    for fps in (FPS, 8.0, 6.0):
        n = n_frames(fps)
        log(f"at {fps:g} fps: {n} frames, about {per * n / 60:.0f} min and {size * n / 2 ** 30:.1f} GB a seed")


# --------------------------------------------------------------------------- process (what Make mesh runs)
def _instrument() -> dict:
    """Count, in this process only, what the tracking cube and the mesh model do, and the warnings logged."""
    import ohs.track.recon_tsdf as RT
    import ohs.track.volume_gpu as VG

    st: dict[str, Any] = {"cube": {}, "pool": {}, "warnings": {}}
    follow0 = VG.GpuTsdfVolume.follow

    def follow(self, T_wc):
        moved = follow0(self, T_wc)
        c = st["cube"].setdefault(str(id(self)), {"calls": 0, "moves": 0, "edge_mm": float(self.size_mm), "moves_at": []})
        c["calls"] += 1
        if moved:
            c["moves"] += 1
            c["moves_at"].append([c["calls"], [round(float(v), 1) for v in np.asarray(T_wc)[:3, 3]]])
        return moved

    VG.GpuTsdfVolume.follow = follow
    slots0 = RT.ConfidenceTsdf._slots_for

    def slots(self, keys):
        out = slots0(self, keys)
        p = st["pool"].setdefault(str(id(self)), {})
        p.update(n_bricks=int(self.n_bricks), capacity=int(self.capacity), max_bricks=int(self.max_bricks),
                 voxel_mm=float(self.voxel_mm))
        return out

    RT.ConfidenceTsdf._slots_for = slots

    class Grab(logging.Handler):
        def emit(self, r: logging.LogRecord) -> None:
            if r.levelno >= logging.WARNING:
                k = f"{r.name}: {r.getMessage()}"[:240]
                st["warnings"][k] = st["warnings"].get(k, 0) + 1

    logging.getLogger().addHandler(Grab())
    return st


def _watch_memory(stop: threading.Event, out: dict) -> None:
    from tools.memory_peaks import process_memory

    while not stop.wait(1.0):
        m = process_memory(os.getpid()) or {}
        ws = float(m.get("ws") or 0.0) / 1e9
        out["max_ws_gb"] = max(out.get("max_ws_gb", 0.0), ws)
        if ws > MAX_WS_GB:
            print(f"STOPPED: working set {ws:.1f} GB > {MAX_WS_GB} GB", flush=True)
            os._exit(95)


def _straighten_with(anchors: bool | None, overrides: dict) -> None:
    """Straighten in this process with the tags as anchors on or off (step 2; ``None``: the engine's default) and any
    other ``StraightenParams`` field of ``overrides``: the scorecard's ``app_chain`` calls ``straighten_trajectory``
    with its defaults, so the module's function is wrapped here (this process only)."""
    import dataclasses

    import ohs.track.posegraph as PG

    orig = PG.straighten_trajectory
    kw = dict(overrides)
    if anchors is not None:
        kw["tags"] = bool(anchors)

    def wrapped(rec, calib, traj, **k):
        if kw:
            k["params"] = dataclasses.replace(k.get("params") or PG.StraightenParams(), **kw)
        poses, rep = orig(rec, calib, traj, **k)
        if _SAVE.get("path") is not None:  # the poses as Straighten's loops and tags left them (before the crossings)
            rows = sorted((i, t, ok) for i, t, ok, _ in PG._as_rows(traj))
            PG.write_trajectory(_SAVE["path"], [(i, t, ok and i in poses, poses.get(i, np.eye(4))) for i, t, ok in rows])
        return poses, rep

    PG.straighten_trajectory = wrapped


_SAVE: dict[str, Any] = {"path": None}


def world_turned(yaw_deg: float) -> np.ndarray:
    """A world turned by ``yaw_deg`` about z and moved by a fraction of a voxel (step 2's check of the edge reading):
    true poses fused in it give the same surface on another phase of the mesh model's grid."""
    return G.make_se3(G.so3_exp(np.array([0.0, 0.0, math.radians(yaw_deg)])), [0.37, 0.61, 0.23])


def process(work: Path, rec_name: str, poses: str, params: dict | None = None, tag: str = "", anchors: bool | None = None,
            straighten: dict | None = None, world_yaw: float | None = None) -> Path:
    """``params`` (``scorecard.parse_params``: e.g. ``fusion.gpu_volume_voxels=768``) is for a diagnostic run only, in
    its own folder ``<poses>-<tag>``; the study's runs use none. ``anchors``: Straighten's tags on or off (step 2, folder
    ``tracked-anchors-on`` / ``-off`` unless ``tag`` names it); ``straighten``: other ``StraightenParams`` fields."""
    from ohs.io.recording import RecordingReader
    from tools.memory_peaks import Sampler, summarize

    rec = work / "recs" / rec_name
    if anchors is not None and not tag and poses == "tracked":
        tag = "anchors-" + ("on" if anchors else "off")
    out = work / "runs" / rec_name / (poses + (f"-{tag}" if tag else ""))
    if (out / "done.json").is_file():
        return out
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    spec = spec_for(rec)
    if poses == "tracked":
        _straighten_with(anchors, straighten or {})
        _SAVE["path"] = out / "straighten_poses.csv"
    st = _instrument()
    given = dict(RecordingReader(rec).ground_truth_poses()) if poses == "true" else None
    world = world_turned(world_yaw) if (given is not None and world_yaw is not None) else None
    if world is not None:
        given = {i: world @ T for i, T in given.items()}
    mem: dict[str, Any] = {}
    stop = threading.Event()
    th = threading.Thread(target=_watch_memory, args=(stop, mem), daemon=True)
    th.start()
    t0 = time.perf_counter()
    with Sampler(os.getpid(), None, every=1.0, seconds=6 * 3600.0) as smp:
        rep, info = SC.app_chain(rec, out, spec, dict(params or {}), given)
    wall = time.perf_counter() - t0
    stop.set()
    peaks = summarize(smp.rows).get("", {}) if smp.rows else {}
    stg = out / "stages"
    for name in ("trajectory.csv", "report.json"):  # the tracked path before Straighten, and its report
        if (stg / "track" / name).is_file():
            shutil.copy2(stg / "track" / name, out / f"track_{name}")
    if (stg / "straighten.json").is_file():
        shutil.copy2(stg / "straighten.json", out / "straighten.json")
    shutil.rmtree(stg, ignore_errors=True)
    tr = json.loads((out / "track_report.json").read_text(encoding="utf-8")) if (out / "track_report.json").is_file() else {}
    done = {"poses": poses, "world": None if world is None else world.tolist(), "params": params or {}, "anchors": anchors, "straighten_params": straighten or {}, "make_mesh_s": round(wall, 1), "chain": info,
            "peak_ws_gb": round(float(peaks["peak_ws"]) / 1e9, 2) if peaks.get("peak_ws") else None,
            "peak_gpu_shared_gb": round(float(peaks["gpu_shared"]) / 1e9, 2) if peaks.get("gpu_shared") else None,
            "watch_max_ws_gb": round(mem.get("max_ws_gb", 0.0), 2),
            "frames": tr.get("frames") or rep.get("frames"), "ground_truth": tr.get("ground_truth"),
            "warnings": tr.get("warnings"), "path_check": tr.get("path_check"),
            "final_warnings": rep.get("warnings"), "cube": st["cube"], "pool": st["pool"], "logged": st["warnings"]}
    write_json(out / "done.json", done)
    log(f"{rec_name} {poses}: {wall:.0f} s, peak {done['peak_ws_gb']} GB; cube moves "
        f"{[c['moves'] for c in st['cube'].values()]}; straighten {json.dumps(SC._plain(info.get('straighten')))[:300]}")
    return out


# --------------------------------------------------------------------------- the truth
def panel_of(sc):
    return sc.primitives[sc.object_id("panel")]


def to_local(sc, P: np.ndarray) -> np.ndarray:
    p = panel_of(sc)
    return (np.asarray(P, np.float64).reshape(-1, 3) - p.t) @ p.R


def to_world(sc, L: np.ndarray) -> np.ndarray:
    return G.transform_points(panel_of(sc).T_wo, np.asarray(L, np.float64).reshape(-1, 3))


def surface_local(x, y) -> np.ndarray:
    x, y = np.atleast_1d(np.asarray(x, np.float64)), np.atleast_1d(np.asarray(y, np.float64))
    return np.column_stack([x, y, height(x, y)[0]])


def frame_at(sc, P: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """World unit vectors at panel points ``P``: along the length, across, the normal (up)."""
    L = to_local(sc, P)
    _, hx, hy = height(L[:, 0], L[:, 1])
    R = panel_of(sc).R
    tx = np.column_stack([np.ones_like(hx), np.zeros_like(hx), hx])
    tx /= np.linalg.norm(tx, axis=1, keepdims=True)
    n = np.column_stack([-hx, -hy, np.ones_like(hx)])
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    ty = np.cross(n, tx)
    return tx @ R.T, ty @ R.T, n @ R.T


def true_hole(sc, k: int) -> dict:
    """Hole ``k``: the circle an exact fit gives on the true edge in the plane of the true ring round it (as
    ``scorecard.panel_hole_world``)."""
    from ohs.measure.fit import fit_circle, fit_plane

    hx0, hy0, r0 = panel_of(sc).holes[k]
    th = np.linspace(0, 2 * math.pi, 1440, endpoint=False)
    E = to_world(sc, surface_local(hx0 + r0 * np.cos(th), hy0 + r0 * np.sin(th)))
    rr, tt = np.meshgrid(np.linspace(r0, r0 + 3.0, 13), th[::4])
    Rg = to_world(sc, surface_local(hx0 + rr.ravel() * np.cos(tt.ravel()), hy0 + rr.ravel() * np.sin(tt.ravel())))
    pl = fit_plane(Rg)
    n = pl.normal if pl.normal @ panel_of(sc).R[:, 2] >= 0 else -pl.normal
    c = fit_circle(E, normal=n)
    return {"centre": np.asarray(c.center), "diameter": 2.0 * float(c.radius_mm), "local": (hx0, hy0, r0)}


def truth_of(sc):
    from tools.preset_study import _panel_mesh

    V, F = _panel_mesh(sc, step=1.0)
    return SC.MeshTruth(V, F, 0.0)


# --------------------------------------------------------------------------- the path
def path_rows(traj: Path) -> dict[int, np.ndarray]:
    return dict(SC._traj(traj))


def view_points(sc, gt: dict[int, np.ndarray], idx: list[int]) -> tuple[list[int], np.ndarray]:
    """The true panel point under each frame's image centre (frames whose centre ray misses the panel are left out)."""
    O = np.array([gt[i][:3, 3] for i in idx])
    D = np.array([gt[i][:3, 2] for i in idx])
    hit = sc.cast(O, D, surface=False)
    ok = np.isfinite(hit.t) & (hit.obj == sc.object_id("panel"))
    return [i for i, k in zip(idx, ok) if k], O[ok] + hit.t[ok, None] * D[ok]


def fit_world(trk: dict, gt: dict, idx: list[int]) -> np.ndarray:
    """The tracked world in the true one that best fits frames ``idx`` (each camera centre and a point ahead)."""
    P, Q = [], []
    for i in idx:
        for z in (0.0, AHEAD_MM):
            P.append(trk[i][:3, 3] + z * trk[i][:3, 2])
            Q.append(gt[i][:3, 3] + z * gt[i][:3, 2])
    return SC._kabsch(np.asarray(P), np.asarray(Q))


def fit_shift(trk: dict, gt: dict, idx: list[int], R: np.ndarray) -> np.ndarray:
    """As :func:`fit_world` over frames ``idx`` but turned by ``R`` (the whole path's fit) and fitted only in position: a
    patch of frames at one end of the panel fixes the turn badly (a few tenths of a degree read as tens of mm a metre
    away), while the gyro holds the turn along the whole path."""
    P, Q = [], []
    for i in idx:
        for z in (0.0, AHEAD_MM):
            P.append(trk[i][:3, 3] + z * trk[i][:3, 2])
            Q.append(gt[i][:3, 3] + z * gt[i][:3, 2])
    return G.make_se3(R, np.mean(np.asarray(Q) - np.asarray(P) @ R.T, axis=0))


def where_tracked(trk: dict, gt: dict, idx: list[int], P: np.ndarray) -> np.ndarray:
    """Where each frame's tracked pose puts true point ``P[k]`` (the tracked world)."""
    return np.array([G.transform_points(trk[i] @ G.invert_se3(gt[i]), P[k:k + 1])[0] for k, i in enumerate(idx)])


def path_metrics(sc, trk: dict, gt: dict, fps: float) -> dict:
    """End to end, bow and the path's error from the camera path (metrics 3 and 4b)."""
    idx = sorted(i for i in trk if i in gt)
    idx, P = view_points(sc, gt, idx)
    Q = where_tracked(trk, gt, idx, P)
    x = to_local(sc, P)[:, 0]
    tx, ty, n = frame_at(sc, P)
    passes = np.array([pass_of(i, fps) for i in idx])
    A, B, M = x < -END_X, x > END_X, np.abs(x) < MID_X
    Tall = fit_world(trk, gt, idx)
    Rall = Tall[:3, :3]

    def offsets(T: np.ndarray, sel: np.ndarray) -> dict:
        e = G.transform_points(T, Q[sel]) - P[sel]
        return {"n": int(sel.sum()), "along": float(np.median(np.einsum("ij,ij->i", e, tx[sel]))),
                "across": float(np.median(np.einsum("ij,ij->i", e, ty[sel]))),
                "normal": float(np.median(np.einsum("ij,ij->i", e, n[sel])))}

    def anchored(sel_a: np.ndarray, sel_b: np.ndarray) -> dict | None:
        if sel_a.sum() < 3 or sel_b.sum() < 3:
            return None
        ia, ib = [idx[k] for k in np.flatnonzero(sel_a)], [idx[k] for k in np.flatnonzero(sel_b)]
        b_at_a, a_at_b = offsets(fit_shift(trk, gt, ia, Rall), sel_b), offsets(fit_shift(trk, gt, ib, Rall), sel_a)
        err = 0.5 * (b_at_a["along"] - a_at_b["along"])
        span = float(np.median(x[sel_b]) - np.median(x[sel_a]))  # how far apart the two ends' views are
        return {"B_when_A_placed": b_at_a, "A_when_B_placed": a_at_b, "length_err_mm": err, "span_mm": span,
                "length_err_pct": 100.0 * err / span}

    out: dict[str, Any] = {"frames": len(idx), "end_to_end": anchored(A, B)}
    out["per_pass"] = {str(k): anchored(A & (passes == k), B & (passes == k)) for k in range(PASSES)}
    ends = [idx[k] for k in np.flatnonzero(A | B)]
    out["bow"] = offsets(fit_world(trk, gt, ends), M) if M.sum() >= 3 and len(ends) >= 3 else None
    C = np.array([trk[i][:3, 3] for i in idx])
    Ct = np.array([gt[i][:3, 3] for i in idx])
    d_fit = np.linalg.norm(G.transform_points(Tall, C) - Ct, axis=1)
    T_first = gt[idx[0]] @ G.invert_se3(trk[idx[0]])
    d_first = np.linalg.norm(G.transform_points(T_first, C) - Ct, axis=1)
    out["error_fitted"] = {"median": float(np.median(d_fit)), "max": float(d_fit.max())}
    out["error_first_frame"] = {"median": float(np.median(d_first)), "max": float(d_first.max()), "last": float(d_first[-1])}
    # the along-length error of each view point with the whole path fitted, by pass and position: where it grows
    e = G.transform_points(Tall, Q) - P
    al = np.einsum("ij,ij->i", e, tx)
    out["along_by_x"] = {str(k): [[float(lo), float(np.median(al[(passes == k) & (x >= lo) & (x < lo + 200)]))]
                                  for lo in range(-600, 600, 200) if ((passes == k) & (x >= lo) & (x < lo + 200)).sum() >= 3]
                         for k in range(PASSES)}
    return out


def revisits(sc, trk: dict, gt: dict, K: np.ndarray, size: tuple[int, int], fps: float) -> dict:
    """Metric 5: where two passes saw the same true point, how far their tracked poses put it apart."""
    xs, ys = np.arange(-600.0, 600.1, 50.0), np.arange(-150.0, 150.1, 50.0)
    X, Y = np.meshgrid(xs, ys)
    P = to_world(sc, surface_local(X.ravel(), Y.ravel()))
    _, _, n = frame_at(sc, P)
    w, h = size
    idx = sorted(i for i in trk if i in gt)
    best = np.full((len(P), PASSES), -1, np.int64)
    score = np.full((len(P), PASSES), np.inf)
    for i in idx:
        Tc = G.invert_se3(gt[i])
        pc = G.transform_points(Tc, P)
        z = pc[:, 2]
        u = K[0, 0] * pc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]
        v = K[1, 1] * pc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]
        facing = np.einsum("ij,ij->i", gt[i][:3, 3] - P, n) > 0
        ok = (z > 150) & (z < 450) & (u > 20) & (u < w - 20) & (v > 20) & (v < h - 20) & facing
        s = np.hypot(u - K[0, 2], v - K[1, 2])
        k = pass_of(i, fps)
        better = ok & (s < score[:, k])
        score[better, k] = s[better]
        best[better, k] = i
    R = fit_world(trk, gt, idx)[:3, :3]
    tx, ty, _ = frame_at(sc, P)
    xl = to_local(sc, P)[:, 0]
    out: dict[str, Any] = {}
    for a, b in ((0, 1), (1, 2), (0, 2)):
        sel = np.flatnonzero((best[:, a] >= 0) & (best[:, b] >= 0))
        if not len(sel):
            continue
        qa = np.array([G.transform_points(trk[best[k, a]] @ G.invert_se3(gt[best[k, a]]), P[k:k + 1])[0] for k in sel])
        qb = np.array([G.transform_points(trk[best[k, b]] @ G.invert_se3(gt[best[k, b]]), P[k:k + 1])[0] for k in sel])
        d = (qa - qb) @ R.T
        comp = {"along": np.einsum("ij,ij->i", d, tx[sel]), "across": np.einsum("ij,ij->i", d, ty[sel]),
                "gap": np.einsum("ij,ij->i", d, n[sel])}
        r: dict[str, Any] = {"points": int(len(sel))}
        for c, v in comp.items():
            r[f"{c}_rms"] = float(np.sqrt(np.mean(v ** 2)))
            r[f"{c}_max"] = float(np.max(np.abs(v)))
        r["along_by_x"] = [[float(lo), float(np.mean(comp["along"][(xl[sel] >= lo) & (xl[sel] < lo + 400)]))]
                           for lo in (-600, -200, 200) if ((xl[sel] >= lo) & (xl[sel] < lo + 400)).any()]
        out[f"{a + 1}-{b + 1}"] = r
    return out


# --------------------------------------------------------------------------- the mesh
def _quad_fit(L: np.ndarray) -> np.ndarray | None:
    """``z = c0 + c1 x + c2 y + c3 x^2 + c4 y^2 + c5 x y`` fitted robustly to local points ``L``."""
    if len(L) < 30:
        return None
    x, y, z = L[:, 0], L[:, 1], L[:, 2]
    A = np.column_stack([np.ones_like(x), x, y, x * x, y * y, x * y])
    keep = np.ones(len(L), bool)
    c = None
    for _ in range(3):
        c, *_ = np.linalg.lstsq(A[keep], z[keep], rcond=None)
        r = np.abs(A @ c - z)
        mad = float(np.median(r[keep])) + 1e-6
        keep = r < max(4.0 * 1.4826 * mad, 0.15)
    return c


def _quad(c: np.ndarray, x, y):
    return c[0] + c[1] * x + c[2] * y + c[3] * x * x + c[4] * y * y + c[5] * x * y


def end_edge(L: np.ndarray, s: float) -> np.ndarray | None:
    """The middle of the short end edge on side ``s`` (+1 / -1) in local coordinates of a placed mesh (metric 2)."""
    sx = s * L[:, 0]
    near = np.abs(L[:, 2] - height(L[:, 0], L[:, 1])[0]) < 3.0
    reg = near & (sx > HALF_L - 150) & (sx < HALF_L - 20) & (np.abs(L[:, 1]) < 110)
    c = _quad_fit(L[reg])
    if c is None:
        return None
    cand = (sx > HALF_L - 60) & (sx < HALF_L + 15) & (np.abs(L[:, 1]) < 100) & (np.abs(L[:, 2] - _quad(c, L[:, 0], L[:, 1])) < 0.5)
    xe = []
    for y0 in np.arange(-100.0, 100.0, 10.0):
        sel = cand & (L[:, 1] >= y0) & (L[:, 1] < y0 + 10)
        if sel.sum() >= 3:
            xe.append(float(np.max(sx[sel])))
    if len(xe) < 5:
        return None
    x = s * float(np.median(xe))
    return np.array([x, 0.0, float(_quad(c, x, 0.0))])


EDGE_DROP_MM = 0.5  # step 2's reading: where the mesh falls this far below the panel's own surface


def end_edge_slice(L: np.ndarray, F: np.ndarray, s: float, half_y: float = 150.0, step: float = 2.5) -> tuple[np.ndarray | None, dict]:
    """The middle of the short end edge on side ``s`` (step 2's reading, "edges_slice"). The mesh is cut along lines
    across the edge (``y`` = const every ``step`` mm, ``|y| < half_y``, where the edge stands at least 5 mm above the
    table; the width, the spacing and the drop chosen on seed 3, the true poses fused twice agreeing within 0.01 mm): the cut's profile, interpolated along the triangles' edges, is followed outwards from the panel until it
    falls :data:`EDGE_DROP_MM` below a local fit of the panel (20-150 mm in from the end); that crossing, interpolated,
    is where the panel ends on that line. The median over the lines is the edge's middle. The same fill falls off both
    ends, so the reading's own bias is the same at both (measured at true poses). Step 1's reading (:func:`end_edge`,
    the outermost vertex within 0.5 mm) jumps by whole voxels where an end lies along the mesh model's grid."""
    sx = s * L[:, 0]
    near = np.abs(L[:, 2] - height(L[:, 0], L[:, 1])[0]) < 3.0
    c = _quad_fit(L[near & (sx > HALF_L - 150) & (sx < HALF_L - 20) & (np.abs(L[:, 1]) < half_y + 10)])
    if c is None:
        return None, {}
    sel_f = F[np.all(sx[F] > HALF_L - 60, axis=1) & np.all(sx[F] < HALF_L + 30, axis=1) & np.any(np.abs(L[F, 1]) < half_y + step, axis=1)]
    P = L[sel_f]  # (M,3,3)
    xe = []
    for y0 in np.arange(-half_y + step / 2, half_y, step):
        pts = []
        for a, b in ((0, 1), (1, 2), (2, 0)):
            ya, yb = P[:, a, 1] - y0, P[:, b, 1] - y0
            cross = (ya * yb < 0)
            t = ya[cross] / (ya[cross] - yb[cross])
            pts.append(P[cross, a] + t[:, None] * (P[cross, b] - P[cross, a]))
        Q = np.concatenate(pts)
        if len(Q) < 10:
            continue
        x = s * Q[:, 0]
        r = Q[:, 2] - _quad(c, Q[:, 0], np.full(len(Q), y0))
        o = np.argsort(x)
        x, r = x[o], r[o]
        inside = (x < HALF_L - 20) & (r > -EDGE_DROP_MM)
        if not inside.any():
            continue
        k0 = int(np.flatnonzero(inside)[-1])
        below = np.flatnonzero((np.arange(len(x)) > k0) & (r <= -EDGE_DROP_MM))
        # the first point beyond the panel's part that is below the drop; before it the last one above
        above = np.flatnonzero(r > -EDGE_DROP_MM)
        if not len(below):
            continue
        j = int(below[0])
        i = int(above[above < j][-1]) if (above < j).any() else j
        if i == j:
            continue
        xe.append(float(x[i] + (x[j] - x[i]) * (r[i] + EDGE_DROP_MM) / (r[i] - r[j])))
    if len(xe) < 10:
        return None, {"lines": len(xe)}
    x = s * float(np.median(xe))
    return np.array([x, 0.0, float(_quad(c, x, 0.0))]), {"lines": len(xe), "spread_mm": float(np.std(xe))}


def hole_dip(L: np.ndarray, hx: float, hy: float = 0.0, r_in: float = 9.0, ring: tuple[float, float] = (14.0, 40.0)
             ) -> tuple[np.ndarray | None, dict]:
    """Where a Ø10 hole is on the mesh although the scan closed it (step 2's reading, "hole_dips"): the matcher fills a
    small hole over a near floor, and the fill sags below the panel. The panel round it (a ring ``ring`` mm from the
    hole's place) is fitted by a local quadric; the vertices within ``r_in`` of the place that lie below it weigh by how
    far below, and their weighted centre (across and along the panel) is the hole's centre. ``(hx, hy)``: where the
    hole is in the placed mesh's local frame, roughly (a few mm do not matter: the weights decide)."""
    d = np.hypot(L[:, 0] - hx, L[:, 1] - hy)
    near = np.abs(L[:, 2] - height(L[:, 0], L[:, 1])[0]) < 3.0
    c = _quad_fit(L[near & (d > ring[0]) & (d < ring[1])])
    if c is None:
        return None, {}
    for _ in range(2):  # once from the rough place, once from the first centre
        d = np.hypot(L[:, 0] - hx, L[:, 1] - hy)
        sel = d < r_in
        w = np.maximum(0.0, -(L[sel, 2] - _quad(c, L[sel, 0], L[sel, 1])))
        if w.sum() <= 0:
            return None, {"dip_mm": 0.0}
        hx, hy = float(np.sum(w * L[sel, 0]) / w.sum()), float(np.sum(w * L[sel, 1]) / w.sum())
    return np.array([hx, hy, float(_quad(c, hx, hy))]), {"dip_mm": float(w.max()), "n": int(sel.sum())}


def edge_length(sc, T: np.ndarray, V2: np.ndarray, reader) -> dict:
    """The distance between the two short edges' middles (by ``reader``), put back into the mesh's own frame."""
    L = to_local(sc, G.transform_points(T, V2))
    true_len = float(np.linalg.norm(surface_local(HALF_L, 0.0) - surface_local(-HALF_L, 0.0)))
    out: dict[str, Any] = {"true_mm": true_len}
    got = [reader(L, s) for s in (-1.0, 1.0)]
    ends = [g[0] if isinstance(g, tuple) else g for g in got]
    if isinstance(got[0], tuple):
        out["info"] = [g[1] for g in got]
    if ends[0] is None or ends[1] is None:
        return out
    back = G.invert_se3(T)
    pa, pb = (G.transform_points(back, to_world(sc, q))[0] for q in ends)
    ln = float(np.linalg.norm(pa - pb))
    out.update(mm=ln, err_mm=ln - true_len, err_pct=100.0 * (ln - true_len) / true_len, local_A=ends[0].tolist(),
               local_B=ends[1].tolist())
    return out


def dips_length(sc, T: np.ndarray, V2: np.ndarray) -> dict:
    """The distance between the two holes' centres read from the dips (:func:`hole_dip`) in the mesh's own frame,
    against the true distance between the holes."""
    L = to_local(sc, G.transform_points(T, V2))
    true_mm = float(np.linalg.norm(true_hole(sc, 0)["centre"] - true_hole(sc, 1)["centre"]))
    out: dict[str, Any] = {"true_mm": true_mm}
    got = [hole_dip(L, -HOLE_X), hole_dip(L, HOLE_X)]
    out["info"] = [g[1] for g in got]
    if got[0][0] is None or got[1][0] is None:
        return out
    back = G.invert_se3(T)
    pa, pb = (G.transform_points(back, to_world(sc, g[0]))[0] for g in got)
    ln = float(np.linalg.norm(pa - pb))
    out.update(mm=ln, err_mm=ln - true_mm, err_pct=100.0 * (ln - true_mm) / true_mm, local_A=got[0][0].tolist(),
               local_B=got[1][0].tolist())
    return out


def long_edges(L: np.ndarray) -> dict:
    """The long edges' sideways positions per 25 mm station (local y), for the sideways bow."""
    out: dict[str, list] = {}
    near = np.abs(L[:, 2] - height(L[:, 0], L[:, 1])[0]) < 5.0
    for s, name in ((-1.0, "near"), (1.0, "far")):
        sy = s * L[:, 1]
        rows = []
        for x0 in np.arange(-575.0, 575.1, 25.0):
            reg = near & (np.abs(L[:, 0] - x0) < 60) & (sy > HALF_W - 120) & (sy < HALF_W - 20)
            c = _quad_fit(L[reg])
            if c is None:
                continue
            cand = (np.abs(L[:, 0] - x0) < 12.5) & (sy > HALF_W - 20) & (sy < HALF_W + 15) & (np.abs(L[:, 2] - _quad(c, L[:, 0], L[:, 1])) < 0.5)
            if cand.sum() >= 3:
                rows.append([float(x0), s * float(np.max(sy[cand]))])
        out[name] = rows
    return out


def sideways_bow(edges: dict) -> dict | None:
    r = {}
    for name, rows in edges.items():
        a = np.asarray(rows)
        if not len(a):
            return None
        mid, end = a[np.abs(a[:, 0]) < MID_X, 1], a[np.abs(a[:, 0]) > END_X, 1]
        if not len(mid) or not len(end):
            return None
        r[name] = float(np.mean(mid) - np.mean(end))
        r[f"{name}_width_reading"] = float(np.mean(a[:, 1]))
    r["bow"] = 0.5 * (r["near"] + r["far"])
    return r


def hole_tool(sc, truth, V2, F2, S2, Fl2, T: np.ndarray) -> dict:
    """Metric 1: the Hole tool at each end, its clicks placed by that end's region alone; centres in the mesh's frame."""
    out: dict[str, Any] = {}
    cen = []
    for k, name in ((0, "A"), (1, "B")):
        h = true_hole(sc, k)
        hx, hy, r = h["local"]
        L = to_local(sc, G.transform_points(T, V2))
        sel = (np.abs(L[:, 0] - hx) < 150) & (np.abs(L[:, 1]) < 150)
        Te, pinfo = SC.place_part(V2[sel], T, truth)
        ck = SC.Clicker(G.transform_points(Te, V2), F2, S2, Fl2)
        clicks = to_world(sc, surface_local([hx + (r + CLICK_DR) * math.cos(math.radians(a)) for a in (0, 120, 240)],
                                            [hy + (r + CLICK_DR) * math.sin(math.radians(a)) for a in (0, 120, 240)]))
        cs, ds, refused = [], [], []
        for p in clicks:
            fit, err, dist = ck.fit(p, "hole")
            if fit is None:
                refused.append(err)
                continue
            cs.append(G.transform_points(G.invert_se3(Te), np.asarray(fit.fit.center).reshape(1, 3))[0])
            ds.append(2.0 * float(fit.fit.radius_mm))
        c = np.median(np.asarray(cs), axis=0) if cs else None
        cen.append(c)
        out[name] = {"answered": len(cs), "refused": refused, "d": float(np.median(ds)) if ds else None,
                     "d_err": float(np.median(ds)) - h["diameter"] if ds else None, "true_d": h["diameter"],
                     "end_placement_moved_mm": pinfo.get("moved_mm")}
    true_dist = float(np.linalg.norm(true_hole(sc, 0)["centre"] - true_hole(sc, 1)["centre"]))
    out["true_mm"] = true_dist
    if cen[0] is not None and cen[1] is not None:
        d = float(np.linalg.norm(cen[0] - cen[1]))
        out.update(mm=d, err_mm=d - true_dist, err_pct=100.0 * (d - true_dist) / true_dist)
    return out


def load_placed(truth, mesh: Path, T0: np.ndarray):
    """A mesh after the Default recipe (as the scorecard), placed on the truth by the part alone: ``(V2, F2, S2, Fl2, T,
    placement info)``."""
    from ohs.mesh.order import canonical
    from ohs.track.export import load_ply
    from tools import preset_study as ps

    d = load_ply(mesh)
    c = canonical(np.asarray(d["vertices"], np.float64), np.asarray(d["faces"], np.int64))
    V, F, S = c.V, c.F, c.vertex_values(d["quality"])
    V2, F2, S2, Fl2, _ = ps.apply_ops(V, F, S, ps.RECIPES["general"])
    T, pinfo = SC.place_part(V2, T0, truth)
    return V2, F2, S2, Fl2, T, pinfo


def edge_check(work: Path, rec_name: str, folder: str) -> dict:
    """The end-to-end readings of one saved mesh (step 1's edges, the sliced edges, the holes' dips) against the truth."""
    rec = work / "recs" / rec_name
    sc = scene_for(rec)
    truth = truth_of(sc)
    run = work / "runs" / rec_name / folder
    done = json.loads((run / "done.json").read_text(encoding="utf-8"))
    T0 = np.eye(4) if not done.get("world") else G.invert_se3(np.asarray(done["world"]))
    if not folder.startswith("true"):
        from ohs.io.recording import RecordingReader

        gt = RecordingReader(rec).ground_truth_poses()
        trk = path_rows(run / "trajectory.csv")
        T0 = fit_world(trk, gt, sorted(i for i in trk if i in gt))
    V2, F2, _, _, T, _ = load_placed(truth, run / "mesh.ply", T0)
    out = {"edges": edge_length(sc, T, V2, end_edge), "edges_slice": edge_length(sc, T, V2, lambda L_, s: end_edge_slice(L_, F2, s))}
    log(f"  slice: {_f(out['edges_slice'].get('err_mm'))} mm (A {out['edges_slice'].get('local_A')}, "
        f"B {out['edges_slice'].get('local_B')}, {out['edges_slice'].get('info')})")
    out["hole_dips"] = dips_length(sc, T, V2)
    log(f"  hole dips: {_f(out['hole_dips'].get('err_mm'))} mm (A {out['hole_dips'].get('local_A')}, "
        f"B {out['hole_dips'].get('local_B')}, {out['hole_dips'].get('info')})")
    log(f"{rec_name} {folder}: step 1's edges {_f(out['edges'].get('err_mm'))} mm")
    return out


def mesh_metrics(sc, truth, mesh: Path, T0: np.ndarray) -> dict:
    V2, F2, S2, Fl2, T, pinfo = load_placed(truth, mesh, T0)
    out: dict[str, Any] = {"vertices": int(len(V2)), "faces": int(len(F2)), "placement": pinfo}
    out.update(SC.part_scores(G.transform_points(T, V2), truth))
    t = time.perf_counter()
    out["holes"] = hole_tool(sc, truth, V2, F2, S2, Fl2, T)
    out["holes_s"] = round(time.perf_counter() - t, 1)
    # metric 2: the short edges' middles, put back into the mesh's own frame
    L = to_local(sc, G.transform_points(T, V2))
    ea, eb = end_edge(L, -1.0), end_edge(L, 1.0)
    true_len = float(np.linalg.norm(surface_local(HALF_L, 0.0) - surface_local(-HALF_L, 0.0)))
    e: dict[str, Any] = {"true_mm": true_len}
    if ea is not None and eb is not None:
        back = G.invert_se3(T)
        pa, pb = (G.transform_points(back, to_world(sc, q))[0] for q in (ea, eb))
        ln = float(np.linalg.norm(pa - pb))
        e.update(mm=ln, err_mm=ln - true_len, err_pct=100.0 * (ln - true_len) / true_len, local_A=ea.tolist(), local_B=eb.tolist())
    out["edges"] = e
    out["edges_slice"] = edge_length(sc, T, V2, lambda L_, s: end_edge_slice(L_, F2, s))  # step 2's reading
    out["hole_dips"] = dips_length(sc, T, V2)
    # metric 4a: the bow, the mesh placed by its two ends
    ends = (np.abs(L[:, 0]) > END_X) & (np.abs(L[:, 1]) < HALF_W - 10)
    Tn, ninfo = SC.place_part(V2[ends], T, truth)
    Vn = G.transform_points(Tn, V2)
    dist, part, sp = truth.nearest(Vn)
    Ln = to_local(sc, Vn)
    mid = part & (np.abs(Ln[:, 0]) < MID_X) & (np.abs(Ln[:, 1]) < 150) & (dist < 5.0)
    endm = part & (np.abs(Ln[:, 0]) > END_X) & (np.abs(Ln[:, 1]) < 150) & (dist < 5.0)
    edges = long_edges(Ln)
    out["bow"] = {"normal_mid": float(np.median(sp[mid])) if mid.any() else None,
                  "normal_ends": float(np.median(sp[endm])) if endm.any() else None,
                  "sideways": sideways_bow(edges), "placement": ninfo, "long_edges": edges}
    return out


# --------------------------------------------------------------------------- judge and table
def judge(work: Path, rec_name: str) -> dict:
    from ohs.io.recording import RecordingReader

    rec = work / "recs" / rec_name
    meta = json.loads((rec / "meta.json").read_text(encoding="utf-8"))
    seed, fps = int(meta["seed"]), float(meta["fps"])
    sc = scene_for(rec)
    truth = truth_of(sc)
    reader = RecordingReader(rec)
    gt = reader.ground_truth_poses()
    stc = reader.calibration().stereo
    K, size = np.asarray(stc.K_rect, np.float64), tuple(stc.image_size)
    res: dict[str, Any] = {"rec": rec_name, "seed": seed, "fps": fps, "frames": len(gt),
                           "true_hole_mm": float(np.linalg.norm(true_hole(sc, 0)["centre"] - true_hole(sc, 1)["centre"]))}
    runs = sorted(p.name for p in (work / "runs" / rec_name).iterdir() if (p / "done.json").is_file())
    for folder in sorted(runs, key=lambda n: (n not in ("tracked", "true"), n)):
        run = work / "runs" / rec_name / folder
        poses = "true" if folder.startswith("true") else "tracked"
        t = time.perf_counter()
        done = json.loads((run / "done.json").read_text(encoding="utf-8"))
        r: dict[str, Any] = {k: done.get(k) for k in ("params", "make_mesh_s", "peak_ws_gb", "peak_gpu_shared_gb", "watch_max_ws_gb", "frames",
                                                       "ground_truth", "warnings", "path_check", "final_warnings", "logged")}
        r["cube"] = {k: {"calls": c["calls"], "moves": c["moves"], "edge_mm": c["edge_mm"]} for k, c in done.get("cube", {}).items()}
        r["pool"] = done.get("pool")
        ch = done.get("chain") or {}
        r["stage_s"] = ch.get("stage_s")
        r["straighten"] = ch.get("straighten")
        r["refine"] = ch.get("refine")
        T0 = np.eye(4) if not done.get("world") else G.invert_se3(np.asarray(done["world"]))
        if poses == "tracked":
            trk0, trk = path_rows(run / "track_trajectory.csv"), path_rows(run / "trajectory.csv")
            T0 = fit_world(trk, gt, sorted(i for i in trk if i in gt))
            r["path_track"] = path_metrics(sc, trk0, gt, fps)
            r["path_final"] = path_metrics(sc, trk, gt, fps)
            r["revisits_track"] = revisits(sc, trk0, gt, K, size, fps)
            r["revisits_final"] = revisits(sc, trk, gt, K, size, fps)
            if (run / "straighten_poses.csv").is_file():  # after the loops and the tags, before the laser crossings
                trs = path_rows(run / "straighten_poses.csv")
                r["path_straighten"] = path_metrics(sc, trs, gt, fps)
                r["revisits_straighten"] = revisits(sc, trs, gt, K, size, fps)
            sj = run / "straighten.json"
            if sj.is_file():
                s = json.loads(sj.read_text(encoding="utf-8"))
                r["straighten_report"] = {k: s.get(k) for k in ("verdict", "reason", "loops_accepted", "loop_candidates", "keyframes",
                                                                 "correction_mm", "correction_deg", "residual_mm", "ground_truth",
                                                                 "tags", "timings_s")}
                r["straighten_report"]["crossings"] = {k: (s.get("laser_curves") or {}).get(k) for k in ("verdict", "warning", "changed",
                                                                                                      "crossings", "moved_mm")}
        r["mesh"] = mesh_metrics(sc, truth, run / "mesh.ply", T0)
        r["judge_s"] = round(time.perf_counter() - t, 1)
        res[folder] = r
        log(f"{rec_name} {folder}: judged in {r['judge_s']} s")
    tr, tp = res.get("tracked"), res.get("true")
    if tr and tp:
        a, b = tr["mesh"]["edges"].get("mm"), tp["mesh"]["edges"].get("mm")
        if a is not None and b is not None:
            res["edges_vs_true_poses"] = {"err_mm": a - b, "err_pct": 100.0 * (a - b) / tr["mesh"]["edges"]["true_mm"]}
    if tp:  # step 2: the sliced edges of every tracked run against the same seed's true-pose reading
        b = tp["mesh"].get("edges_slice", {}).get("mm")
        res["slice_vs_true_poses"] = {}
        for name, r in res.items():
            if isinstance(r, dict) and "mesh" in r and not name.startswith("true") and b is not None:
                a = r["mesh"].get("edges_slice", {}).get("mm")
                if a is not None:
                    res["slice_vs_true_poses"][name] = {"err_mm": a - b, "err_pct": 100.0 * (a - b) / r["mesh"]["edges_slice"]["true_mm"]}
        yw = [r for name, r in res.items() if name.startswith("true-yaw") and isinstance(r, dict) and "mesh" in r]
        if yw and b is not None and yw[0]["mesh"].get("edges_slice", {}).get("mm") is not None:
            res["slice_true_twice_mm"] = yw[0]["mesh"]["edges_slice"]["mm"] - b  # the reading's own precision
    write_json(work / "runs" / rec_name / "judge.json", res)
    return res


def verdict2(res: dict, run: str = "tracked-anchors-on") -> tuple[str, list[str]]:
    """Step 2's stop rule (the owner's, fixed before any tagged result; part c, the no-tags test, is the test suite's):
    (a) the sliced edges within 0.05 % of the same seed's true-pose reading, (b) the camera path end to end within 0.05 %
    (after Straighten), (d) the passes' slide along the panel under 0.65 mm RMS for every pair of passes."""
    r = res.get(run)
    if not r:
        return "not run", []
    why = []
    e = (res.get("slice_vs_true_poses") or {}).get(run) or {}
    if e.get("err_pct") is None or abs(e["err_pct"]) > LIMIT_PCT:
        why.append(f"(a) edges {_f(e.get('err_pct'))} %")
    p = (r.get("path_final") or {}).get("end_to_end") or {}
    if p.get("length_err_pct") is None or abs(p["length_err_pct"]) > LIMIT_PCT:
        why.append(f"(b) path {_f(p.get('length_err_pct'))} %")
    worst = max((v["along_rms"] for v in (r.get("revisits_final") or {}).values()), default=None)
    if worst is None or worst >= 0.65:
        why.append(f"(d) slide {_f(worst, 2)} mm RMS")
    return ("misses" if why else "meets"), why


def measure_tags(work: Path, rec_name: str, every: int = 2) -> dict:
    """Step 2's two weights, measured (on seed 3 only, never on the judged seeds): (1) a single frame's tag corners
    against the truth, every ``every``-th frame at its true pose: their scatter across the image and along the view, as
    the corner's scatter in the image (``tag_sigma_px``: across, ``err * f / Z``; along, ``err * f * B / (sqrt 2 Z^2)``);
    (2) how the tracked turn wanders (``tag_drift_deg_per_frame``): the tracked path's turn against the true one over
    ``lag`` frames, its mean square growing as ``lag * s^2`` for a random walk of ``s`` a frame."""
    from ohs.io.recording import RecordingReader
    from ohs.track.posegraph import StraightenParams
    from ohs.track.tag_anchors import detect_tag_views

    rec = work / "recs" / rec_name
    reader = RecordingReader(rec)
    gt = reader.ground_truth_poses()
    calib = reader.calibration()
    tj = json.loads((rec / "tags.json").read_text(encoding="utf-8"))
    truth = {int(i): np.asarray(X) for i, X in tj["corners"].items()}
    frames = sorted(gt)[::every]
    p = dataclasses_replace(StraightenParams(), tag_look_every=1)
    det, info = detect_tag_views(reader, calib, frames, p)
    f, B = info["K"]
    ex, ey, ez, Zs, n_ids = [], [], [], [], {}
    for fr, views in det.items():
        Tc = G.invert_se3(gt[fr])
        for m, X in views.items():
            if m not in truth:
                n_ids["wrong"] = n_ids.get("wrong", 0) + 1
                continue
            Xt = G.transform_points(Tc, truth[m])
            e = X - Xt
            ex += list(e[:, 0])
            ey += list(e[:, 1])
            ez += list(e[:, 2])
            Zs += list(Xt[:, 2])
    ex, ey, ez, Zs = map(np.asarray, (ex, ey, ez, Zs))
    out: dict[str, Any] = {"frames_looked": len(frames), "frames_with_tag": len(det), "corners": int(len(ex)),
                           "views_per_frame": float(np.mean([len(v) for v in det.values()])) if det else 0.0,
                           "frames_two": int(sum(len(v) >= 2 for v in det.values())), "unknown_ids": n_ids.get("wrong", 0)}
    if len(ex):
        px_xy = np.sqrt(np.mean(np.concatenate([ex, ey]) ** 2 * np.concatenate([f / Zs, f / Zs]) ** 2))
        px_z = np.sqrt(np.mean((ez * f * B / (math.sqrt(2.0) * Zs ** 2)) ** 2))
        out.update(err_mm={"x_rms": float(np.sqrt(np.mean(ex ** 2))), "y_rms": float(np.sqrt(np.mean(ey ** 2))),
                           "z_rms": float(np.sqrt(np.mean(ez ** 2))), "z_mean": float(np.mean(ez))},
                   sigma_px={"across": float(px_xy), "along_view": float(px_z)})
    run = work / "runs" / rec_name / "tracked-anchors-off"
    trj = run / "track_trajectory.csv"
    if trj.is_file():
        trk = path_rows(trj)
        idx = sorted(i for i in trk if i in gt)
        have = set(idx)
        rows = []
        for lag in (4, 8, 16, 32, 64):  # the tracked turn over lag frames against the true one (the worlds cancel)
            a = [G.rotation_angle_deg((gt[i][:3, :3].T @ gt[i + lag][:3, :3]).T @ (trk[i][:3, :3].T @ trk[i + lag][:3, :3]))
                 for i in idx if i + lag in have]
            rows.append([lag, float(np.mean(np.square(a)))])
        lags = np.array([r[0] for r in rows], float)
        ms = np.array([r[1] for r in rows])
        out["turn"] = {"lag_mean_sq_deg2": rows, "deg_per_frame": float(math.sqrt(max(np.sum(lags * ms) / np.sum(lags * lags), 0.0)))}
    write_json(work / "runs" / rec_name / "measured.json", out)
    log(f"{rec_name}: {json.dumps(out)[:900]}")
    return out


def no_tags_check(work: Path, rec_name: str) -> dict:
    """Straighten with the tags on, on a step 1 recording (no tags), against what step 1 saved with the code from before
    step 2 (``straighten.json``; the poses: ``trajectory.csv``, written after the laser crossings, which changed nothing
    on these recordings). Every field but the new ``tags``, the ``tag_*`` settings and the timings must be the same,
    and the poses to the 6 decimals the file keeps."""
    from ohs.io.recording import RecordingReader
    from ohs.track.posegraph import StraightenParams, straighten_trajectory

    rec = work / "recs" / rec_name
    run = work / "runs" / rec_name / "tracked"
    old = json.loads((run / "straighten.json").read_text(encoding="utf-8"))
    rows = SC._traj(run / "track_trajectory.csv")
    from ohs.track.posegraph import read_trajectory

    traj = read_trajectory(run / "track_trajectory.csv")
    poses, rep = straighten_trajectory(rec, RecordingReader(rec).calibration(), traj, params=StraightenParams(tags=True))
    new = json.loads(json.dumps(SC._plain(rep)))

    def strip(r: dict) -> dict:
        r = {k: v for k, v in r.items() if k not in ("tags", "timings_s", "laser_curves")}
        r["params"] = {k: v for k, v in r["params"].items() if not (k == "tags" or k.startswith("tag_"))}
        return r

    diff = sorted(k for k in set(strip(old)) | set(strip(new)) if strip(old).get(k) != strip(new).get(k))
    saved = dict(SC._traj(run / "trajectory.csv"))
    dmax = max(float(np.max(np.abs(poses[i] - saved[i]))) for i in saved)
    out = {"fields_differing": diff, "pose_max_diff": dmax, "tags": new.get("tags"), "rows": len(rows)}
    write_json(work / "runs" / rec_name / "no_tags_check.json", out)
    log(f"{rec_name}: no-tags check: {out}")
    return out


def spaced_share(det: dict[int, dict[int, Any]], frames: list[int], places: list[tuple[float, float]],
                 min_dx: float = 100.0) -> dict:
    """Of ``frames``, how many saw a tag, two or more tags, and two tags at least ``min_dx`` apart along the panel
    (their layout x): only such a frame measures a length along the part between tags."""
    one = two = spaced = 0
    for fr in frames:
        ids = [m for m in (det.get(fr) or {}) if 0 <= m < len(places)]
        one += bool(ids)
        two += len(ids) >= 2
        xs = [places[m][0] for m in ids]
        spaced += bool(xs) and (max(xs) - min(xs)) >= min_dx
    n = max(len(frames), 1)
    return {"keyframes": len(frames), "one": one, "two": two, "spaced": spaced, "min_dx_mm": min_dx,
            "share_two": two / n, "share_spaced": spaced / n}


def tag_spacing(work: Path, rec_name: str, run: str = "tracked-anchors-on") -> dict:
    """Step 3: the tags each of Straighten's keyframes detects (the keyframes chosen from the run's tracked path, as
    Straighten chooses them), and the share that see two tags spaced along the panel (:func:`spaced_share`). Light: it
    reads the keyframes' images only."""
    from ohs.io.recording import RecordingReader
    from ohs.track.posegraph import StraightenParams, _as_rows, select_keyframes
    from ohs.track.tag_anchors import detect_tag_views

    rec = work / "recs" / rec_name
    reader = RecordingReader(rec)
    layout = json.loads((rec / "tags.json").read_text(encoding="utf-8"))["layout"]
    rows = _as_rows(work / "runs" / rec_name / run / "track_trajectory.csv")  # as Straighten reads it
    p = StraightenParams()
    kf = [int(rows[k][0]) for k in select_keyframes(rows, p.kf_travel_mm, p.kf_rotation_deg, p.kf_max_gap_s, p.max_keyframes)]
    det, _ = detect_tag_views(reader, reader.calibration(), kf, dataclasses_replace(p, tag_look_every=1))
    out = spaced_share(det, kf, TAG_LAYOUTS[layout])
    out["layout"] = layout
    write_json(work / "runs" / rec_name / "spacing.json", out)
    log(f"{rec_name}: {out}")
    return out


# --------------------------------------------------------------------------- the never-worse check's limit (X8 follow-up)
#: the held-out check's limits tried (``StraightenParams.tag_holdout_max_ratio``; the engine's default 0.85), and
#: "bypass": the check not applied (no limit on the ratio, no "already agrees" floor). Study only: no default changes.
LIMITS: tuple[Any, ...] = (0.85, 0.90, 0.95, 1.00, "bypass")
#: the harm cases (NOTES.md, fixed before running them), all emulated on the saved detections (no new render)
HARMS = ("none", "moved5", "noise3", "slip3", "half")


def _below_normal() -> None:
    """This process at below-normal priority (another agent's long study shares the processor)."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)


def limit_prep(work: Path, rec_name: str) -> Path:
    """Straighten's loops on the processor, on the scan's tracked path (``tracked-anchors-off/track_trajectory.csv``,
    the same path both of its tracked runs started from), and the tags each keyframe sees: everything the tags' block
    takes, saved so :func:`limit_sweep` runs only the block. The loops here run on the processor path, not on the
    graphics card as in the judged runs: their result is compared with the run's saved one (``loops_check``)."""
    import pickle

    from ohs.track.posegraph import StraightenParams, _straighten_loops, read_trajectory, select_keyframes
    from ohs.track.tag_anchors import TagView, detect_tag_views

    rec = work / "recs" / rec_name
    run = work / "runs" / rec_name / "tracked-anchors-off"
    out = work / "limit" / f"{rec_name}.pkl"
    if out.is_file():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    p = StraightenParams(tags=True)
    traj = read_trajectory(run / "track_trajectory.csv")
    t0 = time.perf_counter()
    ctx: dict = {}
    poses, rep = _straighten_loops(rec, None, traj, params=p, ctx=ctx)
    rows = ctx["rows"]
    kf_pos = select_keyframes(rows, p.kf_travel_mm, p.kf_rotation_deg, p.kf_max_gap_s, p.max_keyframes)
    kf_frames = [int(rows[k][0]) for k in kf_pos]
    det, info = detect_tag_views(ctx["reader"], ctx["calib"], kf_frames, dataclasses_replace(p, tag_look_every=1))
    views = [TagView(k, m, X) for k, fr in enumerate(kf_frames) for m, X in sorted(det.get(fr, {}).items())]
    saved = path_rows(run / "straighten_poses.csv") if (run / "straighten_poses.csv").is_file() else {}
    gpu = json.loads((run / "straighten.json").read_text(encoding="utf-8")) if (run / "straighten.json").is_file() else {}
    diff = max((float(np.linalg.norm(poses[i][:3, 3] - saved[i][:3, 3])) for i in poses if i in saved), default=None)
    check = {"cpu_reason": rep.get("reason"), "gpu_reason": gpu.get("reason"), "cpu_verdict": rep.get("verdict"),
             "gpu_verdict": gpu.get("verdict"), "max_pose_diff_mm": diff, "seconds": round(time.perf_counter() - t0, 1)}
    blob = {"rows": [(int(r[0]), float(r[1]), bool(r[2]), np.asarray(r[3])) for r in rows], "kf_pos": list(kf_pos),
            "kf_frames": kf_frames, "poses": poses, "loops": ctx.get("loops") or [], "views": views, "K": info["K"],
            "loops_reason": rep.get("reason"), "loops_check": check}
    with out.open("wb") as f:
        pickle.dump(blob, f)
    log(f"{rec_name}: prepared, {len(views)} views in {len(kf_frames)} keyframes; loops {check}")
    return out


def _harm(name: str, views: list, kf_frames: list[int], gt: dict, layout: str, p: Any, f: float, B: float) -> tuple[list, dict]:
    """A harm case applied to the saved detections (an emulation; NOTES.md): ``moved5``: the most-seen tag moved
    5 mm along the panel from the scan's middle keyframe on; ``noise3``: every corner's scatter three times the
    measured one (noise of sqrt(8) times it added); ``slip3``: the tags along the long sides (not the ends) moved
    3 mm along the panel together from the middle keyframe on (a strip that slipped; a strip laid shifted from the
    start changes nothing, the fit never knows where a tag lies); ``half``: half the detections dropped at random."""
    from ohs.track.tag_anchors import TagView, _corner_weights

    rng = np.random.default_rng(12345)
    half_k = len(kf_frames) // 2
    info: dict[str, Any] = {"harm": name}

    def moved(v, d):
        R = gt[kf_frames[v.k]][:3, :3]  # the true camera's turn: a move of the tag in the world, in its frame
        return TagView(v.k, v.tag, v.X + R.T @ np.asarray(d))

    if name == "none":
        return list(views), info
    if name == "moved5":
        counts: dict[int, int] = {}
        for v in views:
            counts[v.tag] = counts.get(v.tag, 0) + 1
        m = max(sorted(counts), key=lambda t: counts[t])
        info["bad_tags"] = [m]
        return [moved(v, [5.0, 0.0, 0.0]) if (v.tag == m and v.k >= half_k) else v for v in views], info
    if name == "noise3":
        out = []
        for v in views:
            w = _corner_weights(v.X, f, B, float(p.tag_sigma_px), float(p.tag_sigma_view_px))
            out.append(TagView(v.k, v.tag, v.X + rng.normal(size=v.X.shape) * math.sqrt(8.0) / np.sqrt(w)))
        return out, info
    if name == "slip3":
        side = [m for m, (x, _) in enumerate(TAG_LAYOUTS[layout]) if abs(x) <= HALF_L]
        info["bad_tags"] = side
        return [moved(v, [3.0, 0.0, 0.0]) if (v.tag in side and v.k >= half_k) else v for v in views], info
    if name == "half":
        keep = rng.random(len(views)) >= 0.5
        return [v for v, k in zip(views, keep) if k], info
    raise ValueError(name)


def limit_sweep(work: Path, rec_name: str, harm: str = "none") -> dict:
    """The tags' block alone (``ohs.track.tag_anchors.anchor_keyframes``, as ``posegraph._anchor_to_tags`` calls it)
    on the prepared scan (:func:`limit_prep`), at each limit of :data:`LIMITS`, with ``harm`` applied; the camera path
    against the truth after it, and without tags (the loops' poses). No graphics card, no mesh."""
    import pickle

    from ohs.io.recording import RecordingReader
    from ohs.track.posegraph import StraightenParams, interpolate_poses, odometry_edges, tag_odometry_params
    from ohs.track.tag_anchors import anchor_keyframes

    rec = work / "recs" / rec_name
    with (work / "limit" / f"{rec_name}.pkl").open("rb") as fh:
        b = pickle.load(fh)
    meta = json.loads((rec / "meta.json").read_text(encoding="utf-8"))
    fps = float(meta["fps"])
    sc = scene_for(rec)
    reader = RecordingReader(rec)
    gt = reader.ground_truth_poses()
    stc = reader.calibration().stereo
    K, size = np.asarray(stc.K_rect, np.float64), tuple(stc.image_size)
    layout = json.loads((rec / "tags.json").read_text(encoding="utf-8"))["layout"]
    rows, kf_pos, kf_frames, poses = b["rows"], b["kf_pos"], b["kf_frames"], b["poses"]
    f, B = b["K"]
    base = StraightenParams(tags=True)
    views, hinfo = _harm(harm, b["views"], kf_frames, gt, layout, base, f, B)
    T_kf = np.stack([poses[fr] for fr in kf_frames])
    nfr = np.diff(np.asarray(kf_pos))
    lost = np.array([sum(1 for r in rows[kf_pos[k]:kf_pos[k + 1]] if not r[2]) for k in range(len(kf_pos) - 1)])
    odometry = [(e.k, e.X, e.info) for e in odometry_edges(T_kf, nfr, lost, tag_odometry_params(base))]
    tracked = [r for r in rows if r[2]]
    idx = [int(r[0]) for r in tracked]
    T_cur = np.stack([poses[i] for i in idx])
    times = np.array([float(r[1]) for r in tracked])
    kf_times = np.array([float(rows[k][1]) for k in kf_pos])

    def score(P: dict) -> dict:
        pm = path_metrics(sc, P, gt, fps)
        rv = revisits(sc, P, gt, K, size, fps)
        e = pm["end_to_end"]
        return {"end_to_end_pct": e.get("length_err_pct"), "end_to_end_mm": e.get("length_err_mm"),
                "per_pass_mm": [(v or {}).get("length_err_mm") for v in pm["per_pass"].values()],
                "slide_rms": {k: v["along_rms"] for k, v in rv.items()},
                "slide_worst_rms": max((v["along_rms"] for v in rv.values()), default=None)}

    res: dict[str, Any] = {"rec": rec_name, "harm": hinfo, "views": len(views), "loops_reason": b["loops_reason"],
                           "loops_check": b["loops_check"], "no_tags": score(poses), "limits": {}}
    for L in LIMITS:
        p = (dataclasses_replace(base, tag_holdout_max_ratio=1e9, tag_min_residual_mm=0.0) if L == "bypass"
             else dataclasses_replace(base, tag_holdout_max_ratio=float(L)))
        t0 = time.perf_counter()
        fit = anchor_keyframes(T_kf, views, odometry, p, f, B, loops=b["loops"])
        r: dict[str, Any] = {"reason": fit.reason, "acted": fit.T is not None, "used": len(fit.used),
                             "left_out": sorted(fit.left_out), "dropped_views": fit.dropped_views,
                             "ratios": [s.get("ratio") for s in (fit.holdout or {}).get("splits", [])],
                             "before_mm": [s.get("before_mm") for s in (fit.holdout or {}).get("splits", [])],
                             "seconds": round(time.perf_counter() - t0, 1)}
        bad = hinfo.get("bad_tags")
        r["bad_left_out"] = bool(bad) and all(m in fit.left_out for m in bad)
        if fit.T is not None:
            T_new = interpolate_poses(times, T_cur, kf_times, T_kf, fit.T)
            r.update(score({i: T_new[m] for m, i in enumerate(idx)}))
        else:
            r.update(res["no_tags"])
        res["limits"][str(L)] = r
    write_json(work / "limit" / f"{rec_name}-{harm}.json", res)
    log(f"{rec_name} {harm}: no tags {res['no_tags']['end_to_end_pct']:.3f} % / {res['no_tags']['slide_worst_rms']:.2f} mm; "
        + "; ".join(f"{L}: {v['reason']} {v['ratios']} -> {_f(v['end_to_end_pct'])} % / {_f(v['slide_worst_rms'], 2)} mm"
                    for L, v in res["limits"].items()))
    return res


def limit_verdicts(work: Path, recs: list[str], tol_pct: float = 0.01, tol_mm: float = 0.1) -> dict:
    """The rule of the follow-up (NOTES.md): a limit is safe to propose when on every harm case and scan the result
    is no worse than without tags (end to end not larger by more than ``tol_pct`` % and the worst slide not larger by
    more than ``tol_mm``), or the tags are refused, or the bad tag is left out; worth proposing when it makes the tags
    act on step 3's seed 2 and that scan then has the path under 0.05 %."""
    out: dict[str, Any] = {}
    for L in LIMITS:
        harms = []
        for rec in recs:
            for h in HARMS[1:]:
                j = work / "limit" / f"{rec}-{h}.json"
                if not j.is_file():
                    harms.append({"rec": rec, "harm": h, "missing": True})
                    continue
                d = json.loads(j.read_text(encoding="utf-8"))
                r, n = d["limits"][str(L)], d["no_tags"]
                worse = (abs(r["end_to_end_pct"]) > abs(n["end_to_end_pct"]) + tol_pct
                         or r["slide_worst_rms"] > n["slide_worst_rms"] + tol_mm)
                ok = (not r["acted"]) or r["bad_left_out"] or not worse
                harms.append({"rec": rec, "harm": h, "ok": ok, "acted": r["acted"], "worse": worse,
                              "e2e": r["end_to_end_pct"], "e2e_no_tags": n["end_to_end_pct"],
                              "slide": r["slide_worst_rms"], "slide_no_tags": n["slide_worst_rms"]})
        s2 = work / "limit" / "s2-row150-none.json"
        worth = None
        if s2.is_file():
            r = json.loads(s2.read_text(encoding="utf-8"))["limits"][str(L)]
            worth = bool(r["acted"] and abs(r["end_to_end_pct"]) < LIMIT_PCT)
        out[str(L)] = {"safe": all(x.get("ok") for x in harms) and not any(x.get("missing") for x in harms),
                       "worth": worth, "unsafe_cases": [x for x in harms if not x.get("ok", False)]}
    write_json(work / "limit" / "verdicts.json", out)
    for L, v in out.items():
        log(f"limit {L}: safe {v['safe']}, worth {v['worth']}; not safe on "
            f"{[(x['rec'], x['harm']) for x in v['unsafe_cases']]}")
    return out


def dataclasses_replace(obj: Any, **kw: Any) -> Any:
    import dataclasses

    return dataclasses.replace(obj, **kw)


def _f(x: Any, nd: int = 3) -> str:
    return "n/m" if x is None else f"{x:.{nd}f}"


def verdict(res: dict) -> tuple[str, list[str]]:
    """Fails (module docstring) when the Hole tool or the edges against the true-pose reading are over the limit."""
    why = []
    h = ((res.get("tracked") or {}).get("mesh") or {}).get("holes") or {}
    if h.get("err_pct") is not None and abs(h["err_pct"]) > LIMIT_PCT:
        why.append(f"Hole tool {h['err_pct']:+.3f} %")
    e = res.get("edges_vs_true_poses") or {}
    if e.get("err_pct") is not None and abs(e["err_pct"]) > LIMIT_PCT:
        why.append(f"edges {e['err_pct']:+.3f} %")
    if h.get("err_pct") is None and e.get("err_pct") is None:
        return "not measured", why
    return ("FAILS" if why else "passes"), why


def table(work: Path) -> None:
    for f in sorted((work / "runs").glob("*/judge.json")):
        j = json.loads(f.read_text(encoding="utf-8"))
        v, why = verdict(j)
        print(f"\n== {j['rec']} (seed {j['seed']}, {j['frames']} frames at {j['fps']:g} fps): {v} {'; '.join(why)}")
        for name, r in j.items():
            if not (isinstance(r, dict) and "mesh" in r):
                continue
            poses = "true" if name.startswith("true") else "tracked"
            print(f"  [{name}] {r.get('params') or ''}")
            m = r["mesh"]
            h, e, b = m["holes"], m["edges"], m["bow"]
            print(f"  {poses:7s} holes: {_f(h.get('err_mm'))} mm ({_f(h.get('err_pct'))} %), answered {h['A']['answered']}/{h['B']['answered']}, "
                  f"d_err {_f(h['A']['d_err'], 2)}/{_f(h['B']['d_err'], 2)}; refused {h['A']['refused'][:1]} {h['B']['refused'][:1]}")
            print(f"          edges: {_f(e.get('err_mm'))} mm ({_f(e.get('err_pct'))} %); bow normal {_f(b['normal_mid'])} mm "
                  f"(ends {_f(b['normal_ends'])}), sideways {_f((b.get('sideways') or {}).get('bow'))} mm")
            print(f"          mesh median {_f(m.get('part_med'))} mm, over 0.3 mm {_f(m.get('part_over03'))}; Make mesh {r['make_mesh_s']} s; "
                  f"peak {r['peak_ws_gb']} GB; cube {r['cube']}")
            if poses == "tracked":
                for k in [x for x in ("path_track", "path_straighten", "path_final") if x in r]:
                    p = r[k]
                    ee = p.get("end_to_end") or {}
                    print(f"          {k}: end to end {_f(ee.get('length_err_mm'))} mm, bow {p.get('bow')}, err fitted {p['error_fitted']}, "
                          f"first {p['error_first_frame']}")
                    print(f"            per pass {[_f((v or {}).get('length_err_mm')) for v in p['per_pass'].values()]}")
                for k in [x for x in ("revisits_track", "revisits_straighten", "revisits_final") if x in r]:
                    print(f"          {k}: " + "; ".join(f"{pp} along {_f(v['along_rms'], 2)}/{_f(v['along_max'], 2)} across "
                                                       f"{_f(v['across_rms'], 2)} gap {_f(v['gap_rms'], 2)}" for pp, v in r[k].items()))
                s = r.get("straighten_report") or {}
                print(f"          straighten: {s.get('verdict')} loops {s.get('loops_accepted')} corr {s.get('correction_mm')}; "
                      f"crossings {s.get('crossings')}")
                print(f"          warnings {r.get('warnings')} path_check {(r.get('path_check') or {}).get('state')}")
        if j.get("edges_vs_true_poses"):
            print(f"  edges tracked against true poses: {j['edges_vs_true_poses']}")
        if j.get("slice_vs_true_poses") is not None:
            print(f"  step 2: sliced edges against true poses {j['slice_vs_true_poses']}; true poses twice "
                  f"{_f(j.get('slice_true_twice_mm'))} mm")
            for run in [n for n in j if n.startswith("tracked")]:
                v2, why2 = verdict2(j, run)
                tg = ((j[run].get("straighten_report") or {}).get("tags") or {})
                print(f"  [{run}] stop rule: {v2} {'; '.join(why2)}; tags {tg.get('reason')} used {len(tg.get('used') or [])} "
                      f"left out {tg.get('left_out')} frames 1/2 tags {tg.get('frames_one')}/{tg.get('frames_two')}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=("budget", "render", "process", "judge", "table", "edgecheck", "measure", "notagcheck",
                                     "spacing", "limitprep", "limit", "limitverdict"))
    ap.add_argument("work", type=Path)
    ap.add_argument("args", nargs="*")
    ap.add_argument("--frames", type=int, default=None)
    ap.add_argument("--fps", type=float, default=FPS)
    ap.add_argument("--set-param", action="append", default=None, help="process, a diagnostic run only: SECTION.FIELD=VALUE")
    ap.add_argument("--tag", default="", help="process with --set-param: the run's folder is <poses>-<tag>")
    ap.add_argument("--tags", default=None, choices=sorted(TAG_LAYOUTS), help="render: the tags' layout (step 2)")
    ap.add_argument("--anchors", default=None, choices=("on", "off"), help="process: Straighten's tags on or off (step 2)")
    ap.add_argument("--straighten-param", action="append", default=None,
                    help="process: another StraightenParams field FIELD=VALUE (tuning, seed 3 only; needs --tag)")
    ap.add_argument("--world-yaw", type=float, default=None,
                    help="process true: fuse the true poses in a world turned by this (deg) and moved a fraction of a voxel")
    a = ap.parse_args(argv)
    if (a.set_param or a.straighten_param) and not a.tag:
        ap.error("--set-param and --straighten-param need --tag (a diagnostic run never takes the study's own folder)")
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if a.what == "budget":
        budget(a.work, a.frames or 20)
    elif a.what == "render":
        render(a.work, int(a.args[0]), a.frames, a.fps, a.tags)
    elif a.what == "process":
        sp = {k: json.loads(v) for k, v in (s.split("=", 1) for s in a.straighten_param or [])}
        process(a.work, a.args[0], a.args[1], SC.parse_params(a.set_param), a.tag,
                None if a.anchors is None else a.anchors == "on", sp, a.world_yaw)
    elif a.what == "edgecheck":
        edge_check(a.work, a.args[0], a.args[1])
    elif a.what == "measure":
        measure_tags(a.work, a.args[0])
    elif a.what == "notagcheck":
        no_tags_check(a.work, a.args[0])
    elif a.what == "spacing":
        tag_spacing(a.work, a.args[0])
    elif a.what == "limitprep":
        _below_normal()
        limit_prep(a.work, a.args[0])
    elif a.what == "limit":
        _below_normal()
        limit_sweep(a.work, a.args[0], a.args[1] if len(a.args) > 1 else "none")
    elif a.what == "limitverdict":
        limit_verdicts(a.work, a.args)
    elif a.what == "judge":
        judge(a.work, a.args[0])
    else:
        table(a.work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
