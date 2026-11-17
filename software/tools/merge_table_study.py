"""What a merge of several scans leaves out when the part was moved (FEATURE_PLAN X1, "the merge"):
each scan's table and nothing else, measured with the part's true shape, before and after the
table map (``ohs.mesh.support``, module docstring, 4.).

    python tools/merge_table_study.py bracket <project folder> <work folder>
    python tools/merge_table_study.py plate <work folder> [--thickness 1.0 0.5] [--frames 60]

**bracket**: the front, side and back scans of a project of the simulated bracket (their recordings,
meshes and camera paths are copied into the work folder; the poses between them are read from the
project's merged scan of all three). Front + side and front + side + back are merged three ways:
``same`` (the part not moved: the tables stay, a plain fusion), ``before`` (the part moved, the
old way: everything within 2 mm of each scan's support plane left out of every frame) and ``after``
(each scan's table map). Each merged mesh is compared with the bracket's true shape
(``tests/test_inspect_reference._bracket_reference``) in the simulator's world, placed there through
the front scan's first camera and an ICP of the front scan's own part onto the true shape:

* *table left*: merged surface that is not on the true shape within 3 mm of the table (all of it,
  and within 20 mm of the bracket's base, seen from above);
* *foot covered*: of the base's four walls, the share of the true surface at 0-1, 1-2, 2-4 and
  4-8 mm above the table that has a compared vertex of the merge within 1 mm;
* *within 0.3 mm*: of the compared surface, the share (by area) whose three corners lie within
  0.3 mm of the true shape; and the figures ``VALIDATION.md`` gives for merges (part vertices,
  median error along the normal, share more than 0.3 mm off, vertices within 0.5 mm).

**plate**: a thin plate (``ohs.sim.scene.thin_plate``, 60 x 40 mm with a notch) lying flat, scanned
three times: as it lies (A), turned over and moved (B), slid and turned on the table (C). Each scan
is rendered at 1280 x 800 and fused at its true poses with the Standard quality; A + B and A + C are
merged at the true motion of the plate, ``before`` and ``after``, and also all frames fused with the
tables in (``fused``) and then each scan's table taken out of the merged mesh by the map's rule
(``fused-then-rule``) or by the single-scan edit (``fused-then-edit``). It reports how much of the
plate's top (and, turned over, its bottom) the merge holds, what is left of the tables, and what
the plate's thickness reads (turned over: its top less its bottom; slid: its top above scan A's
table plane).

Each step's time is printed. Everything is written as ``results_*.json`` in the work folder.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

SOFTWARE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE))
sys.path.insert(0, str(SOFTWARE / "tests"))

from ohs import geometry as G  # noqa: E402

BRACKET = {"front": "scan-20261115-202720", "side": "scan-20261115-202720-2", "back": "scan-20261115-211837"}
BRACKET_ALL = "scan-20261115-211953"  # the project's merge of all three: its report holds the poses


def log(msg: str) -> None:
    print(msg, flush=True)


def merge_params(width: int):
    """The app's merge settings: Standard quality, no Poisson (ohs/server/aligning.py)."""
    from ohs.app.config import Config
    from ohs.server.studio import process_params

    return dataclasses.replace(process_params(Config(), "markerless", "standard", width), poisson=False)


def kabsch(P: np.ndarray, Q: np.ndarray) -> np.ndarray:
    cp, cq = P.mean(axis=0), Q.mean(axis=0)
    U, _, Wt = np.linalg.svd((P - cp).T @ (Q - cq))
    D = np.diag([1.0, 1.0, np.sign(np.linalg.det(Wt.T @ U.T))])
    R = Wt.T @ D @ U.T
    return G.make_se3(R, cq - R @ cp)


def run_merge(scans, out: Path, params) -> tuple[dict, float]:
    from ohs.align.merge import merge_scans

    shutil.rmtree(out, ignore_errors=True)
    t0 = time.perf_counter()
    rep = merge_scans(scans, out, params)
    return rep, time.perf_counter() - t0


# --------------------------------------------------------------------------- the bracket
def bracket_reference():
    from ohs.inspect.reference import reference_from_arrays
    from test_inspect_reference import _bracket_reference

    V, F = _bracket_reference()
    return reference_from_arrays(V, F, name="bracket")


def base_wall_samples(step: float = 0.25) -> tuple[np.ndarray, np.ndarray]:
    """Points on the four walls of the bracket's base (simulator's world) and their heights."""
    from ohs.sim.scene import make_scene

    base = next(p for p in make_scene("bracket", 0).primitives if p.name == "base")
    hx, hy, hz = base.half
    out = []
    for ax, other, h_other in ((0, 1, hy), (1, 0, hx)):
        s = np.arange(-h_other, h_other + 1e-9, step)
        z = np.arange(-hz, hz + 1e-9, step)
        S, Z = np.meshgrid(s, z)
        for sign in (-1.0, 1.0):
            L = np.zeros((S.size, 3))
            L[:, ax] = sign * base.half[ax]
            L[:, other] = S.ravel()
            L[:, 2] = Z.ravel()
            out.append(L @ base.R.T + base.t)
    P = np.vstack(out)
    return P, P[:, 2]


def base_footprint_distance(XY: np.ndarray) -> np.ndarray:
    """Distance (mm) of points, seen from above, from the bracket's base (0 inside)."""
    from ohs.sim.scene import make_scene

    base = next(p for p in make_scene("bracket", 0).primitives if p.name == "base")
    L = (np.column_stack([XY, np.zeros(len(XY))]) - base.t) @ base.R
    q = np.abs(L[:, :2]) - base.half[:2]
    return np.linalg.norm(np.maximum(q, 0.0), axis=1)


def judge_bracket(mesh: Path, T_sim: np.ndarray, ref, walls) -> dict:
    """The merged mesh against the bracket's true shape (module docstring)."""
    from scipy.spatial import cKDTree

    from ohs.inspect.reference import ScanSurface, deviation_from_reference
    from ohs.mesh.topology import face_areas

    scan = ScanSurface.from_file(mesh)
    dev = deviation_from_reference(scan, ref, T_sim)
    V, F = dev.V, dev.F
    ok = np.isfinite(dev.offset)
    off = np.abs(dev.offset[ok]).astype(np.float64)
    A = face_areas(V, F)
    C = V[F].mean(axis=1)
    near_table = np.abs(C[:, 2]) <= 3.0
    tl = dev.face_off_reference & near_table
    near_part = base_footprint_distance(C[:, :2]) <= 20.0
    cf = dev.face_compared
    o3 = np.abs(np.nan_to_num(dev.offset.astype(np.float64), nan=9.0))[F].max(axis=1)
    within = float(A[cf & (o3 <= 0.3)].sum() / max(A[cf].sum(), 1e-9))
    P, z = walls
    d, _ = cKDTree(V[ok]).query(P, k=1, distance_upper_bound=1.0)
    cov = {f"{a:g}-{b:g}": float(np.isfinite(d[(z >= a) & (z < b)]).mean()) for a, b in ((0, 1), (1, 2), (2, 4), (4, 8.01))}
    return {"n_vertices": int(len(V)), "n_faces": int(len(F)), "part_vertices": int(ok.sum()),
            "median_mm": float(np.median(off)) if len(off) else None, "over_0_3": float((off > 0.3).mean()) if len(off) else None,
            "good_0_5": int((off <= 0.5).sum()), "within_0_3_area": within,
            "table_left_mm2": float(A[tl].sum()), "table_left_near_mm2": float(A[tl & near_part].sum()),
            "off_reference_mm2": float(A[dev.face_off_reference].sum()), "foot_covered": cov}


def bracket(project: Path, work: Path) -> None:
    from ohs.align.merge import ScanInput, scan_support_from_mesh
    from ohs.align.pairwise import scan_cloud_from_mesh
    from ohs.io.recording import RecordingReader
    from ohs.track.export import load_mesh

    work = work / "bracket"
    work.mkdir(parents=True, exist_ok=True)
    scans = {}
    t0 = time.perf_counter()
    for name, sid in BRACKET.items():
        src, dst = project / "scans" / sid, work / name
        if not (dst / "done").is_file():
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src / "recording", dst / "recording")
            (dst / "result").mkdir(parents=True)
            for f in ("mesh.ply", "trajectory.csv", "report.json"):
                shutil.copy2(src / "result" / f, dst / "result" / f)
            (dst / "done").write_text("", encoding="utf-8")
        scans[name] = {"dir": dst, "mesh": dst / "result" / "mesh.ply", "traj": dst / "result" / "trajectory.csv",
                       "rec": dst / "recording"}
    log(f"copied the three scans in {time.perf_counter() - t0:.1f} s")
    rep_all = json.loads((project / "scans" / BRACKET_ALL / "result" / "report.json").read_text(encoding="utf-8"))
    for name, s in zip(BRACKET, rep_all["scans"]):
        scans[name]["T"] = np.asarray(s["T"], dtype=np.float64)
    ref = bracket_reference()
    walls = base_wall_samples()
    # the front scan's world in the simulator's: its first camera, then an ICP of its part onto the true shape
    gt = RecordingReader(scans["front"]["rec"]).ground_truth_poses()
    T = gt[min(gt)]
    Vf, _, _ = load_mesh(scans["front"]["mesh"])
    closest = ref.closest()
    for _ in range(20):
        P = Vf @ T[:3, :3].T + T[:3, 3]
        Q, _, _ = closest.query(P)
        sel = (np.linalg.norm(P - Q, axis=1) < 1.5) & (P[:, 2] > 3.0)
        T = kabsch(P[sel], Q[sel]) @ T
    T_sim = T
    log(f"front scan placed in the simulator's world: ICP moved it {np.linalg.norm(T[:3, 3] - gt[min(gt)][:3, 3]):.2f} mm from its first camera's pose")
    # supports
    sup_old, sup_new, timing = {}, {}, {}
    for name, s in scans.items():
        t0 = time.perf_counter()
        sup_old[name] = scan_cloud_from_mesh(s["mesh"]).support
        t1 = time.perf_counter()
        sup_new[name] = scan_support_from_mesh(s["mesh"], name=name)
        t2 = time.perf_counter()
        timing[name] = {"support_plane_s": t1 - t0, "table_map_s": t2 - t1}
        m = sup_new[name][0]
        info = m.to_dict() if hasattr(m, "to_dict") else {"rule": "slab or none", "warnings": sup_new[name][1]}
        log(f"{name}: the alignment's plane in {t1 - t0:.1f} s, the table map in {t2 - t1:.1f} s; "
            f"thin parts {[(round(x['size_mm']), round(x['height_mm'], 2), x['verdict']) for x in info.get('thin_parts', [])]}, "
            f"uneven {info.get('uneven')}, warnings {info.get('warnings')}")
    width = int(RecordingReader(scans["front"]["rec"]).meta.get("image_size", [1280])[0])
    params = merge_params(width)
    results = {"timing": timing, "singles": {}, "merges": {}}
    for name, s in scans.items():
        results["singles"][name] = judge_bracket(s["mesh"], T_sim @ s["T"], ref, walls)
        log(f"single {name}: {results['singles'][name]}")
    for combo in (("front", "side"), ("front", "side", "back")):
        for mode in ("same", "before", "after"):
            inputs = []
            for name in combo:
                s = scans[name]
                if mode == "same":
                    sup, warn = None, []
                elif mode == "before":
                    sup, warn = sup_old[name], []
                else:
                    sup, warn = sup_new[name]
                inputs.append(ScanInput(s["rec"], s["traj"], s["T"], name, support=sup, warnings=warn))
            out = work / f"merge_{'_'.join(combo)}_{mode}"
            rep, secs = run_merge(inputs, out, params)
            row = judge_bracket(out / "mesh.ply", T_sim, ref, walls)
            row.update(seconds=secs, report_vertices=rep["mesh"]["n_vertices"], warnings=rep.get("warnings", []),
                       tables=[sc.get("table") for sc in rep["scans"]], lasers=rep["lasers"],
                       faces_table_removed=rep["mesh"].get("n_faces_table_removed"))
            key = f"{'+'.join(combo)} {mode}"
            results["merges"][key] = row
            log(f"{key}: {secs:.1f} s; " + json.dumps({k: v for k, v in row.items() if k not in ("tables",)}))
            (work.parent / "results_bracket.json").write_text(json.dumps(results, indent=1, default=float), encoding="utf-8")
    # the same-setup merges against the project's own (made by the app on 2026-11-16)
    for combo, sid in ((("front", "side"), "scan-20261115-203047"), (("front", "side", "back"), BRACKET_ALL)):
        mine = (work / f"merge_{'_'.join(combo)}_same" / "mesh.ply").read_bytes()
        theirs = (project / "scans" / sid / "result" / "mesh.ply").read_bytes()
        results.setdefault("same_as_project", {})["+".join(combo)] = mine == theirs
        log(f"{'+'.join(combo)} same setup: mesh.ply {'identical to' if mine == theirs else 'differs from'} the project's merged scan")
    (work.parent / "results_bracket.json").write_text(json.dumps(results, indent=1, default=float), encoding="utf-8")


# --------------------------------------------------------------------------- the thin plate
PLACES = {"A": ((0.0, 0.0, 0.0), False, 20.0), "B": ((25.0, -15.0, 40.0), True, 140.0), "C": ((-20.0, 25.0, -55.0), False, 260.0)}


def plate_scan(work: Path, t: float, key: str, frames: int) -> dict:
    """Scan ``key`` of the plate ``t`` mm thick (PLACES): rendered and fused at its true poses."""
    from ohs.app.process import process_recording
    from ohs.io.recording import RecordingReader
    from ohs.sim.datasets import make_scan_recording
    from ohs.sim.nominal import nominal_calibration
    from ohs.sim.scene import thin_plate
    from ohs.sim.trajectory import HandheldTrajectory

    at, turned, az = PLACES[key]
    sc = thin_plate(thickness=t, at=at, turned=turned)
    d = work / f"plate_{t:g}" / key
    rec, out = d / "rec", d / "out"
    times = {}
    if not (rec / "frames.csv").is_file():
        shutil.rmtree(rec, ignore_errors=True)
        cal = nominal_calibration((1280, 800), realistic=True)
        traj = HandheldTrajectory(target=np.asarray(sc.target, float), t0=100.0 + float(cal.imu.time_offset), duration=frames / 30.0,
                                  seed=int(az), baseline=cal.stereo.baseline, azimuth0_deg=az, **dict(sc.meta["trajectory"]))
        t0 = time.perf_counter()
        make_scan_recording(rec, scene=sc, n_frames=frames, image_size=(1280, 800), realistic=True, seed=int(az) % 7,
                            depth_every=0, trajectory=traj)
        times["render_s"] = time.perf_counter() - t0
        log(f"plate {t:g} mm, scan {key}: rendered {frames} frames in {times['render_s']:.0f} s")
    gt = RecordingReader(rec).ground_truth_poses()
    W = gt[min(gt)]
    if not (out / "mesh.ply").is_file():
        t0 = time.perf_counter()
        Wi = G.invert_se3(W)
        process_recording(rec, out, params=merge_params(1280), poses={i: Wi @ T for i, T in gt.items()})
        times["process_s"] = time.perf_counter() - t0
        log(f"plate {t:g} mm, scan {key}: fused at its true poses in {times['process_s']:.0f} s")
    return {"rec": rec, "out": out, "mesh": out / "mesh.ply", "traj": out / "trajectory.csv", "W": W,
            "P": np.asarray(sc.meta["plate_T"], dtype=np.float64), "times": times}


def plate_raster(cell: float = 0.25):
    """The plate's outline in its own frame: a raster, and each cell's distance inside (+) or outside (-) it."""
    from scipy.ndimage import distance_transform_edt

    from ohs.sim.scene import _PLATE_BOXES

    x = np.arange(-40.0, 40.0, cell) + cell / 2
    y = np.arange(-30.0, 30.0, cell) + cell / 2
    X, Y = np.meshgrid(x, y, indexing="ij")
    inside = np.zeros(X.shape, bool)
    for (x0, x1), (y0, y1) in _PLATE_BOXES:
        inside |= (X >= x0) & (X <= x1) & (Y >= y0) & (Y <= y1)
    sd = distance_transform_edt(inside) * cell - distance_transform_edt(~inside) * cell
    return x[0] - cell / 2, y[0] - cell / 2, cell, sd


def judge_plate(mesh: Path, W_A: np.ndarray, t: float, turned: bool, plane_A, raster) -> dict:
    """The merged mesh (scan A's world) against the plate as it lay in scan A (module docstring)."""
    from ohs.mesh.topology import face_normals
    from ohs.track.export import load_mesh

    V, F, _ = load_mesh(mesh)
    if not len(F):
        return {"n_faces": 0, "top_covered": 0.0, "bottom_covered": 0.0, "table_left_mm2": 0.0}
    Vs = V @ W_A[:3, :3].T + W_A[:3, 3]
    fn, A = face_normals(Vs, F)
    C = Vs[F].mean(axis=1)
    x0, y0, cell, sd = raster
    i = np.clip(((C[:, 0] - x0) / cell).astype(int), 0, sd.shape[0] - 1)
    j = np.clip(((C[:, 1] - y0) / cell).astype(int), 0, sd.shape[1] - 1)
    inbox = (C[:, 0] > x0) & (C[:, 0] < x0 + cell * sd.shape[0]) & (C[:, 1] > y0) & (C[:, 1] < y0 + cell * sd.shape[1])
    s = np.where(inbox, sd[i, j], -99.0)
    inner = s >= 1.5
    inner_area = float((sd >= 1.5).sum()) * cell * cell
    up, down = fn[:, 2] > 0.7, fn[:, 2] < -0.7
    top = inner & up & (np.abs(C[:, 2] - t) <= 0.5)
    bottom = inner & down & (np.abs(C[:, 2]) <= 0.5)
    out = {"n_faces": int(len(F)), "area_mm2": float(A.sum()),
           "top_covered": float(min(1.0, A[top].sum() / inner_area)),
           "bottom_covered": float(min(1.0, A[bottom].sum() / inner_area)),
           "table_left_mm2": float(A[(s < -3.0) & (np.abs(C[:, 2]) <= 3.0 + t)].sum()),
           "outside_mm2": float(A[s < -3.0].sum())}
    tz = float(np.median(C[inner & up, 2])) if (inner & up).any() else float("nan")
    bz = float(np.median(C[inner & down, 2])) if (inner & down).any() else float("nan")
    out["top_z"], out["bottom_z"] = tz, bz
    if turned:
        out["thickness_reads_mm"] = tz - bz
    elif plane_A is not None and (inner & up).any():
        n, d = plane_A  # scan A's table, in scan A's world: the plate's top above it
        h = V[F[inner & up]].mean(axis=1) @ np.asarray(n) + float(d)
        out["thickness_reads_mm"] = float(np.median(h))
    return out


def plate(work: Path, thicknesses: list[float], frames: int) -> None:
    from ohs.align.merge import ScanInput, scan_support_from_mesh
    from ohs.align.pairwise import scan_cloud_from_mesh
    from ohs.mesh import support as S
    from ohs.track.export import load_ply, save_mesh

    raster = plate_raster()
    results = {}
    params = merge_params(1280)
    for t in thicknesses:
        sc = {k: plate_scan(work, t, k, frames) for k in PLACES}
        sup_old, sup_new, info = {}, {}, {}
        for k, s in sc.items():
            t0 = time.perf_counter()
            sup_old[k] = scan_cloud_from_mesh(s["mesh"]).support
            t1 = time.perf_counter()
            sup_new[k] = scan_support_from_mesh(s["mesh"], name=f"plate {k}")
            t2 = time.perf_counter()
            m = sup_new[k][0]
            info[k] = {"plane_s": t1 - t0, "map_s": t2 - t1, "times": s["times"],
                       "map": m.to_dict() if isinstance(m, S.SupportMap) else None, "warnings": sup_new[k][1],
                       "old_plane": None if sup_old[k] is None else [list(map(float, sup_old[k][0])), float(sup_old[k][1])]}
            log(f"plate {t:g}, scan {k}: map in {t2 - t1:.1f} s; "
                + (f"thin parts {[(round(x['size_mm']), round(x['height_mm'], 2), x['verdict']) for x in info[k]['map']['thin_parts']]}, "
                   f"limit {info[k]['map']['thin_limit_mm']:.2f} mm" if info[k]["map"] else f"refused: {sup_new[k][1]}"))
        plane_A = None
        if isinstance(sup_new["A"][0], S.SupportMap):
            plane_A = (sup_new["A"][0].normal, sup_new["A"][0].d)
        res = {"scans": info, "merges": {}}
        for other, turned in (("B", True), ("C", False)):
            a, b = sc["A"], sc[other]
            T = G.invert_se3(a["W"]) @ a["P"] @ G.invert_se3(b["P"]) @ b["W"]  # scan b's world in scan a's
            for mode in ("before", "after", "fused"):
                sups = {"before": (sup_old["A"], sup_old[other]), "after": (sup_new["A"][0], sup_new[other][0]), "fused": (None, None)}[mode]
                warns = {"after": (sup_new["A"][1], sup_new[other][1])}.get(mode, ([], []))
                inputs = [ScanInput(a["rec"], a["traj"], np.eye(4), "A", support=sups[0], warnings=warns[0]),
                          ScanInput(b["rec"], b["traj"], T, other, support=sups[1], warnings=warns[1])]
                out = work / f"plate_{t:g}" / f"merge_A{other}_{mode}"
                rep, secs = run_merge(inputs, out, params)
                row = judge_plate(out / "mesh.ply", a["W"], t, turned, plane_A, raster)
                row.update(seconds=secs, warnings=rep.get("warnings", []), tables=[x.get("table") for x in rep["scans"]])
                res["merges"][f"A+{other} {mode}"] = row
                log(f"plate {t:g} A+{other} {mode}: {secs:.1f} s; " + json.dumps({k2: v for k2, v in row.items() if k2 != "tables"}, default=float))
                if mode == "fused":  # the tables taken out of the fused mesh afterwards
                    d = load_ply(out / "mesh.ply")
                    V, F = d["vertices"], d["faces"]
                    for how in ("rule", "edit"):
                        t0 = time.perf_counter()
                        if how == "rule":
                            gone = np.zeros(len(F), bool)
                            for m, Tm in ((sup_new["A"][0], np.eye(4)), (sup_new[other][0], T)):
                                if isinstance(m, S.SupportMap):
                                    gone[S.table_left(V, F, m, T=Tm).faces] = True
                            keep = ~gone
                            note = ""
                        else:
                            try:
                                f = S.find_support(V, F, sigma=d["quality"])
                                keep = np.zeros(len(F), bool)
                                keep[f.kept] = True
                                note = f.summary
                            except S.SupportRefused as e:
                                keep, note = np.ones(len(F), bool), str(e)
                        secs2 = time.perf_counter() - t0
                        from ohs.mesh.topology import compact

                        V2, F2, _ = compact(V, F[keep])
                        p2 = out.parent / f"merge_A{other}_fused_then_{how}.ply"
                        save_mesh(p2, V2, F2)
                        row = judge_plate(p2, a["W"], t, turned, plane_A, raster)
                        row.update(seconds=secs2, note=note)
                        res["merges"][f"A+{other} fused-then-{how}"] = row
                        log(f"plate {t:g} A+{other} fused-then-{how}: {secs2:.1f} s; " + json.dumps(row, default=float))
                (work / "results_plate.json").write_text(json.dumps({**results, f"{t:g}": res}, indent=1, default=float), encoding="utf-8")
        results[f"{t:g}"] = res
        (work / "results_plate.json").write_text(json.dumps(results, indent=1, default=float), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="what", required=True)
    b = sub.add_parser("bracket")
    b.add_argument("project", type=Path)
    b.add_argument("work", type=Path)
    p = sub.add_parser("plate")
    p.add_argument("work", type=Path)
    p.add_argument("--thickness", type=float, nargs="+", default=[1.0, 0.5])
    p.add_argument("--frames", type=int, default=60)
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    if a.what == "bracket":
        bracket(a.project, a.work)
    else:
        plate(a.work, a.thickness, a.frames)
    log(f"done in {time.perf_counter() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
