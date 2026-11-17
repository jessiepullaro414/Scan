"""Leaving each scan's table out of a merge, and nothing else (FEATURE_PLAN X1, "the merge").

When the part was moved or turned over between scans, each scan's table lies somewhere else
relative to the part and must stay out of the merged model. A slab (everything within 2 mm of the
table's plane) took a thin part lying flat with it, and the bottom 2 mm of every part. Now each
scan's own mesh says, place by place on its table's plane, where the table is bare and where the
part (or a thin part) stands (:class:`ohs.mesh.support.SupportMap`), and the frames are clipped by
that map.

* the clip on synthetic frames (a camera 45Ã‚Â° above a table with a box and a thin disc on it):
  discs of 0.3, 0.8 and 1.5 mm keep their pixels where the map says a part stands, the bare
  table's pixels go, the box's foot keeps its pixels down to the table, laser points alike;
* the map from a finding on a synthetic mesh (a table, a box, a thin part kept, a raised area
  only warned about: kept in a merge);
* what is left of a table in a merged mesh (a ring at the part's foot, crumbs) and the rule that
  takes it out;
* a scan whose table cannot be found: today's slab, said with the scan's name;
* the merge on the cached two-frame recording: the report's fields and warnings, and with the part
  not moved the merged model is bit for bit a plain fusion of the frames.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from ohs import geometry as G
from ohs.mesh.support import SupportMap, find_support, support_map, table_left
from ohs.mesh.topology import compact, face_areas, face_normals
from test_mesh_support import TABLE, PART, box_on_table, raise_disc

CLIP_MM = 2.0  # the margin a single frame's depth gets on bare table (as the slab's)


# --------------------------------------------------------------------------- synthetic frames
K = np.array([[520.0, 0.0, 320.0], [0.0, 520.0, 200.0], [0.0, 0.0, 1.0]])
SIZE = (640, 400)
BOX = ((-50.0, 5.0), (-15.0, 35.0), 20.0)  # x range, y range, height: a thick part
DISC = (35.0, 20.0, 12.0)  # centre x, y and radius of a thin part lying flat
FRAME_NOISE_MM = 0.15  # a single frame's depth scatter


def _camera() -> np.ndarray:
    """T_wc of a camera 45Ã‚Â° above the table, 280 mm from (0, 10, 0), looking at it."""
    target = np.array([0.0, 10.0, 0.0])
    eye = target + np.array([0.0, -200.0, 200.0])
    z = (target - eye) / np.linalg.norm(target - eye)
    x = np.array([1.0, 0.0, 0.0])
    y = np.cross(z, x)
    return G.make_se3(np.column_stack([x, y, z]), eye)


def _frame(t_disc: float, seed: int = 0) -> dict:
    """A depth image of the table with the box and a disc ``t_disc`` thick on it, its true heights
    and what each pixel sees (0 table, 1 box, 2 disc), with the frame's noise along the ray."""
    from ohs.geometry import make_se3
    from ohs.sim.scene import Box, Cylinder, Rect, Scene

    (x0, x1), (y0, y1), H = BOX
    cx, cy, r = DISC
    sc = Scene([Rect(make_se3(np.diag([1.0, -1.0, -1.0]), [0.0, 0.0, 0.0]), (-400.0, 400.0, -400.0, 400.0), 0.5, name="table"),
                Box(make_se3(np.eye(3), [(x0 + x1) / 2, (y0 + y1) / 2, H / 2]), ((x1 - x0) / 2, (y1 - y0) / 2, H / 2), 0.6, name="box"),
                Cylinder(make_se3(np.eye(3), [cx, cy, t_disc / 2]), r, t_disc / 2, 0.6, name="disc")], name="frame")
    T = _camera()
    w, h = SIZE
    v, u = np.mgrid[0:h, 0:w].astype(np.float64)
    ray = np.stack([(u - K[0, 2]) / K[0, 0], (v - K[1, 2]) / K[1, 1], np.ones_like(u)], axis=-1).reshape(-1, 3)
    length = np.linalg.norm(ray, axis=1)
    hit = sc.cast(T[:3, 3], (ray / length[:, None]) @ T[:3, :3].T, 1e-3)
    depth = np.where(np.isfinite(hit.t), hit.t / length, 0.0)
    rng = np.random.default_rng(seed)
    noisy = np.where(depth > 0, depth + rng.normal(0.0, FRAME_NOISE_MM, depth.shape), 0.0)
    P = G.transform_points(T, ray * depth[:, None])  # the true points, in the world
    what = np.where(hit.obj == sc.object_id("box"), 1, np.where(hit.obj == sc.object_id("disc"), 2, 0))
    return {"depth": noisy.reshape(h, w).astype(np.float32), "true": P.reshape(h, w, 3), "what": what.reshape(h, w),
            "seen": (depth > 0).reshape(h, w), "T": T}


def _map(stand_disc: bool = True, spread: float = 2.0) -> SupportMap:
    """The map a finding would give for the frame's scene: the table everywhere, the box's walls and
    the disc where the part stands (grown by ``spread``), the disc a thin part."""
    cell = 1.0
    origin = np.array([-150.0, -150.0])
    n = 300
    c = origin[None, None, :] + cell * (np.stack(np.meshgrid(np.arange(n), np.arange(n), indexing="ij"), axis=-1) + 0.5)
    X, Y = c[..., 0], c[..., 1]
    (x0, x1), (y0, y1), _ = BOX
    out = np.maximum(np.maximum(x0 - X, X - x1), np.maximum(y0 - Y, Y - y1))  # > 0 outside the box's footprint
    box = np.abs(out) <= 1.0  # its walls' footprint
    disc = np.hypot(X - DISC[0], Y - DISC[1]) <= DISC[2]
    core = box | (disc if stand_disc else False)
    stands = np.abs(out) <= 1.0 + spread
    if stand_disc:
        stands |= np.hypot(X - DISC[0], Y - DISC[1]) <= DISC[2] + spread
    table = ~((out < 0) | (disc if stand_disc else False))
    return SupportMap(normal=np.array([0.0, 0.0, 1.0]), d=0.0, e1=np.array([1.0, 0.0, 0.0]), e2=np.array([0.0, 1.0, 0.0]),
                      origin=origin, cell_mm=cell, stands=stands, core=core, table=table,
                      thin=disc if stand_disc else np.zeros_like(disc), clip_mm=CLIP_MM)


def _pipe_stub() -> SimpleNamespace:
    return SimpleNamespace(image_size=SIZE, K=K, _clip_rays=None)


def _clip(depth: np.ndarray, clip) -> np.ndarray:
    from ohs.app.pipeline import FramePipeline

    return FramePipeline._clip_depth(_pipe_stub(), depth, clip)


def _slab(T: np.ndarray, margin: float = CLIP_MM):
    """The slab the merge used before: the table's plane (z = 0) in the camera, moved up by the margin."""
    n = np.array([0.0, 0.0, 1.0])
    return T[:3, :3].T @ n, float(n @ T[:3, 3]) - margin


@pytest.mark.parametrize("t", [0.3, 0.8, 1.5])
def test_a_thin_disc_keeps_its_pixels_where_the_map_says_a_part_stands(t):
    f = _frame(t)
    disc = f["what"] == 2
    assert disc.sum() > 1000  # the disc is well in view
    kept = _clip(f["depth"], _map().in_camera(f["T"])) > 0
    assert kept[disc].mean() > 0.999, f"{t} mm: {100 * kept[disc].mean():.1f} % of the disc kept"
    # the slab took all of it
    assert (_clip(f["depth"], _slab(f["T"]))[disc] > 0).mean() < 0.01
    # a map that does not know the disc (thinner than the scan's limit) leaves it out with the table
    assert (_clip(f["depth"], _map(stand_disc=False).in_camera(f["T"]))[disc] > 0).mean() < 0.01


def test_the_bare_table_goes_and_the_box_keeps_its_foot():
    f = _frame(0.8)
    clip = _map().in_camera(f["T"])
    kept = _clip(f["depth"], clip) > 0
    z = f["true"][..., 2]
    table = (f["what"] == 0) & f["seen"]
    # the table round the part goes; next to the part (where the map grows the part's footprint) it is
    # left for the merged mesh's own rule (table_left)
    X, Y = f["true"][..., 0], f["true"][..., 1]
    near = np.zeros_like(table)
    (x0, x1), (y0, y1), _ = BOX
    near |= (X > x0 - 4) & (X < x1 + 4) & (Y > y0 - 4) & (Y < y1 + 4)
    near |= np.hypot(X - DISC[0], Y - DISC[1]) < DISC[2] + 4
    assert kept[table & ~near].mean() < 1e-3
    # the box's walls keep their pixels right down to the table (the slab took their bottom 2 mm)
    wall = (f["what"] == 1) & (z < BOX[2] - 0.5)
    foot = wall & (z < 2.0)
    assert foot.sum() > 100
    assert kept[foot].mean() > 0.999 and kept[wall].mean() > 0.999
    assert (_clip(f["depth"], _slab(f["T"]))[foot] > 0).mean() < 0.01
    # where the part stands, only what lies more than behind_mm (1 mm) behind the plane goes
    y = np.linspace(BOX[1][0] + 5.0, BOX[1][1] - 5.0, 20)
    at_wall = np.column_stack([np.full_like(y, BOX[0][0]), y, np.zeros_like(y)])
    cam = G.transform_points(G.invert_se3(f["T"]), at_wall - [0.0, 0.0, 3.0]).astype(np.float32)
    assert not clip.keep(cam).any()
    cam = G.transform_points(G.invert_se3(f["T"]), at_wall - [0.0, 0.0, 0.5]).astype(np.float32)
    assert clip.keep(cam).all()


def test_laser_points_follow_the_same_rule():
    from ohs.app.pipeline import FramePipeline

    f = _frame(0.5)
    rows = np.zeros(f["depth"].shape, dtype=bool)
    rows[::9] = True  # lines across the image, as a laser draws them
    sel = rows & (f["depth"] > 0)
    w, h = SIZE
    v, u = np.mgrid[0:h, 0:w].astype(np.float64)
    ray = np.stack([(u - K[0, 2]) / K[0, 0], (v - K[1, 2]) / K[1, 1], np.ones_like(u)], axis=-1)
    P = (ray * f["depth"][..., None])[sel]  # camera frame, as the pipeline's laser points
    clip = _map().in_camera(f["T"])
    got = FramePipeline._clip_points(P, clip)
    want = P[clip.keep(P.astype(np.float32))]
    assert np.array_equal(got, want)
    by_depth = _clip(f["depth"], clip)[sel] > 0
    assert len(got) == int(by_depth.sum())  # the same points as the depth keeps
    what = f["what"][sel]
    kept = clip.keep(P.astype(np.float32))
    assert kept[what == 2].all() and kept[what == 1].all()
    # the plane form is today's: what is in front of the plane
    n, d = _slab(f["T"])
    assert np.array_equal(FramePipeline._clip_points(P, (n, d)), P[P @ n + d > 0])


# --------------------------------------------------------------------------- the map from a finding
WARN_AT, KEEP_AT = np.array([-62.0, 0.0, 0.0]), np.array([0.0, 70.0, 0.0])


@pytest.fixture(scope="module")
def scene():
    """A box on a table, a 0.8 mm disc 30 mm across (kept as a thin part) and a 0.3 mm disc 24 mm
    across (only warned about: the single-scan edit removes it unless the user keeps it)."""
    V, F, lab = box_on_table()
    V = raise_disc(V, [0, 0, 1], 0.0, KEEP_AT, 15.0, 0.8)
    V = raise_disc(V, [0, 0, 1], 0.0, WARN_AT, 12.0, 0.3)
    r = find_support(V, F)
    return V, F, lab, r


def test_the_map_from_a_finding(scene):
    V, F, lab, r = scene
    verdicts = {}
    for x in r.raised:
        c = np.asarray(x["centre"])
        verdicts["keep" if np.linalg.norm(c[:2] - KEEP_AT[:2]) < 5 else "warn" if np.linalg.norm(c[:2] - WARN_AT[:2]) < 5 else "other"] = x["verdict"]
    assert verdicts.get("keep") == "kept" and verdicts.get("warn") == "warned", r.raised
    m = support_map(V, F, r)
    assert abs(float(m.normal @ [0, 0, 1])) > 0.999 and abs(m.d) < 0.05
    P = np.array([
        [0.0, 70.0, 0.8],    # 0 the kept disc's top
        [-62.0, 0.0, 0.3],   # 1 the warned disc's top: kept in a merge
        [60.0, -60.0, 0.5],  # 2 bare table (noise): out
        [60.0, -60.0, 2.5],  # 3 above the clip margin: in
        [40.3, 0.0, 0.3],    # 4 the box's foot: in
        [40.3, 0.0, -0.5],   # 5 just under the plane at the foot: in (within behind_mm)
        [0.0, 0.0, -3.0],    # 6 well behind the plane under the box: out
        [46.0, 0.0, 0.5],    # 7 the table 6 mm from the box: out
    ])
    assert m.keep(P).tolist() == [True, True, False, True, True, True, False, False]
    assert m.at(m.thin, P[:2]).all() and not m.at(m.thin, P[2:]).any()
    assert m.at(m.table, P[[2, 7]]).all() and not m.at(m.table, P[[0]]).any()
    # in a camera, the same answer
    T = _camera()
    Pc = G.transform_points(G.invert_se3(T), P).astype(np.float32)
    assert m.in_camera(T).keep(Pc).tolist() == m.keep(P).tolist()
    # what it says: the plane, the table's area, the thin parts and what happens to each
    d = m.to_dict()
    assert d["plane"]["normal"] == pytest.approx(list(m.normal)) and d["table_area_mm2"] == pytest.approx(r.area_mm2)
    thin = {round(x["height_mm"], 1): x for x in d["thin_parts"]}
    assert thin[0.8]["verdict"] == "kept" and thin[0.8]["in_merge"] == "kept"
    assert thin[0.3]["verdict"] == "warned" and thin[0.3]["in_merge"] == "kept"
    assert d["thin_limit_mm"] == pytest.approx(r.thin_limit_mm) and d["limit"]
    warned = [w for w in d["warnings"] if "kept in the merged scan" in w]
    assert len(warned) == 1 and "may be a thin part" in warned[0], d["warnings"]
    assert d["clip_mm"] == CLIP_MM and d["stands_mm2"] > 0


def test_uneven_areas_go_and_are_said_once():
    """Where the table rises in many places (a scan's border) the raised areas are the table's own:
    they go with it in a merge, as in the single-scan edit, and one warning says so."""
    from test_mesh_support import _noise, _wide_scene

    V, F, lab = _wide_scene()
    rng = np.random.default_rng(32)
    on_table = np.abs(V[:, 2]) < 1.0
    V[:, 2] += on_table * np.clip((V[:, 0] - 50.0) / 10.0, 0.0, 1.0) * _noise(V, 0.15, 33)
    for b in np.column_stack([rng.uniform(60.0, 140.0, 12), rng.uniform(-60.0, 60.0, 12)]):
        V[:, 2] += on_table * rng.uniform(0.3, 0.45) * np.exp(-np.sum((V[:, :2] - b) ** 2, axis=1) / (2 * rng.uniform(3.0, 6.0) ** 2))
    r = find_support(V, F, sigma=np.full(len(V), 0.6))
    uneven = [x for x in r.raised if x["verdict"] == "uneven"]
    assert uneven
    m = support_map(V, F, r)
    d = m.to_dict()
    assert d["uneven"]["count"] == len(uneven) and sum("uneven" in w for w in d["warnings"]) == 1
    C = np.array([x["centre"] for x in uneven])
    assert not m.at(m.thin, C).any()


# --------------------------------------------------------------------------- what is left in a merged mesh
def test_what_is_left_of_a_table_in_a_merged_mesh_goes(scene):
    """The merged mesh of a moved part holds, of this scan's table, a ring at the part's foot (the
    frames keep the table where the part's footprint is grown) and crumbs. They go; the box, its
    walls right down to the table, and both thin parts stay; a loose bit of the part above the
    table stays. The rule works in the merged world (the scan moved by T)."""
    V, F, lab, r = scene
    m = support_map(V, F, r)
    C = V[F].mean(axis=1)
    (hx, hy) = (40.0, 25.0)
    out = np.maximum(np.abs(C[:, 0]) - hx, np.abs(C[:, 1]) - hy)
    ring = (lab == TABLE) & (out < 2.5)
    near_disc = (np.linalg.norm(C[:, :2] - KEEP_AT[:2], axis=1) < 17.0) | (np.linalg.norm(C[:, :2] - WARN_AT[:2], axis=1) < 14.0)
    keep = (lab != TABLE) | near_disc
    crumb = (lab == TABLE) & (np.linalg.norm(C[:, :2] - [80.0, -80.0], axis=1) < 1.5)  # a speck the frames left
    sel = keep | ring | crumb
    Vm, Fm, _ = compact(V, F[sel])
    lab_m, ring_m, crumb_m, disc_m = lab[sel], ring[sel], crumb[sel], near_disc[sel]
    # a loose bit of the part, 20 mm above the table
    Vs, Fs, _ = box_on_table(hx=3.0, hy=3.0, H=3.0, rf=0.5, half=6.0, seed=12)
    Vs, Fs, _ = compact(Vs, Fs[Vs[Fs].mean(axis=1)[:, 2] > 1.0])
    nV = len(Vm)
    Vm = np.vstack([Vm, Vs + [0.0, -80.0, 20.0]])
    Fm = np.vstack([Fm, Fs + nV])
    bit = np.r_[np.zeros(len(lab_m), bool), np.ones(len(Fs), bool)]
    lab_m = np.r_[lab_m, np.full(len(Fs), PART)]
    ring_m, crumb_m, disc_m = (np.r_[a, np.zeros(len(Fs), bool)] for a in (ring_m, crumb_m, disc_m))
    T = G.make_se3(G.so3_exp(np.array([0.4, -1.1, 2.0])), [120.0, -40.0, 300.0])  # the merged world
    left = table_left(G.transform_points(T, Vm), Fm, m, T=T)
    gone = np.zeros(len(Fm), bool)
    gone[left.faces] = True
    A = face_areas(Vm, Fm)
    assert A[gone & ring_m].sum() >= 0.95 * A[ring_m].sum()
    assert gone[crumb_m].all() and left.pieces >= 1
    part = (lab_m == PART) & ~bit
    fn = face_normals(Vm, Fm)[0]
    steep = fn[:, 2] < math.cos(math.radians(45.0))
    assert not (gone & part & steep).any()  # the box's walls stay at any height
    # both thin parts stay, the warned one too: their tops whole, their smeared rims as the single-scan edit
    # keeps them (it loses at most 1 mm of a thin part's rim, test_mesh_support.RIM_MM)
    Cm = Vm[Fm].mean(axis=1)
    tops = (np.linalg.norm(Cm[:, :2] - KEEP_AT[:2], axis=1) < 13.0) | (np.linalg.norm(Cm[:, :2] - WARN_AT[:2], axis=1) < 10.0)
    assert not (gone & tops & ~bit).any()
    plates = (np.linalg.norm(Cm[:, :2] - KEEP_AT[:2], axis=1) < 15.0) | (np.linalg.norm(Cm[:, :2] - WARN_AT[:2], axis=1) < 12.0)
    assert A[gone & plates & ~bit].sum() <= 1.0 * math.pi * (30.0 + 24.0), A[gone & plates & ~bit].sum()
    assert not gone[bit].any()
    assert left.n_faces == int(gone.sum()) and left.area_mm2 == pytest.approx(float(A[gone].sum()))
    # the same mesh in the scan's own world gives the same faces
    assert np.array_equal(table_left(Vm, Fm, m).faces, left.faces)


# --------------------------------------------------------------------------- a scan without a table found
def test_a_scan_whose_table_is_refused_keeps_todays_slab_and_says_so():
    from ohs.align.merge import scan_support
    from ohs.align.pairwise import scan_cloud
    from ohs.mesh.topology import vertex_normals
    from test_mesh_support import ball_on_table

    # a plate's top with a block on it: find_support refuses it (a part's own face, F11)
    Vp, Fp, lp = box_on_table(hx=45.0, hy=30.0, H=8.0, rf=0.5, half=60.0, seed=6)
    Vp, Fp, _ = compact(Vp, Fp[lp == PART])
    Vb, Fb, lb = box_on_table(hx=20.0, hy=15.0, H=25.0, rf=0.5, half=60.0, seed=7)
    Vb, Fb, _ = compact(Vb, Fb[lb == PART])
    V, F = np.vstack([Vp, Vb + [15.0, 0.0, 8.0]]), np.vstack([Fp, Fb + len(Vp)])
    sup, warnings = scan_support(V, F, name="Bracket upside down")
    plane = scan_cloud(V, vertex_normals(V, F)).support
    assert (sup is None) == (plane is None)
    assert len(warnings) == 1 and "Bracket upside down" in warnings[0] and "No table" in warnings[0]
    if sup is not None:
        assert not isinstance(sup, SupportMap) and "2 mm" in warnings[0]
    else:
        assert "nothing" in warnings[0].lower()
    # nothing flat at all: no plane either, nothing is left out, said
    V, F, lab = ball_on_table(seed=5)
    V, F, _ = compact(V, F[lab == PART])
    sup, warnings = scan_support(V, F, name="Dome")
    assert sup is None and len(warnings) == 1 and "Dome" in warnings[0] and "nothing" in warnings[0].lower()
    # a table found: a map, and no warning beyond what the map says
    V, F, lab = box_on_table()
    sup, warnings = scan_support(V, F, name="Box")
    assert isinstance(sup, SupportMap) and warnings == []


# --------------------------------------------------------------------------- the merge, on a cached recording
@pytest.fixture(scope="module")
def two_frames(tmp_path_factory):
    """The two-frame recording of ``test_align.test_merge_takes_scans_of_different_calibrations``
    (cached), its frames at their true poses, and its mesh fused at them."""
    from ohs.app.pipeline import PipelineParams
    from ohs.app.process import ProcessParams, process_recording
    from ohs.io.recording import RecordingReader
    from recordings import scan_recording

    tmp = tmp_path_factory.mktemp("merge-table")
    rec = scan_recording(tmp / "r0", n_frames=2, image_size=(640, 400), realistic=False, seed=0, depth_every=0)
    gt = RecordingReader(rec).ground_truth_poses()
    first = min(gt)
    T0 = G.invert_se3(gt[first])
    rows = [(int(i), 0.0, True, T0 @ T) for i, T in sorted(gt.items())]
    out = tmp / "scan"
    process_recording(rec, out, params=ProcessParams(pipeline=PipelineParams(), export=("ply",)), poses={i: T0 @ T for i, T in gt.items()})
    return {"rec": rec, "rows": rows, "mesh": out / "mesh.ply", "tmp": tmp}


def _params():
    from ohs.app.pipeline import PipelineParams
    from ohs.app.process import ProcessParams

    return ProcessParams(pipeline=PipelineParams(), export=("ply",))


def test_a_merge_with_the_part_moved_says_what_it_left_out(two_frames, tmp_path):
    from ohs.align.merge import ScanInput, merge_scans, scan_support_from_mesh

    sup, warn = scan_support_from_mesh(two_frames["mesh"], name="Front")
    assert isinstance(sup, SupportMap), warn
    refused_plane = (np.array([0.0, 0.0, 1.0]), 10_000.0)  # a scan whose table was refused: today's slab (here far away)
    note = "No table could be told apart on \"Other\": (made up for the test)."
    rep = merge_scans([ScanInput(two_frames["rec"], two_frames["rows"], name="Front", support=sup, warnings=warn),
                       ScanInput(two_frames["rec"], two_frames["rows"], name="Other", support=refused_plane, warnings=[note])],
                      tmp_path / "m", _params())
    a, b = rep["scans"]
    assert a["support_left_out"] and b["support_left_out"]
    ta, tb = a["table"], b["table"]
    assert ta["rule"] == "map" and tb["rule"] == "slab" and tb["margin_mm"] == CLIP_MM
    for key in ("plane", "flatness_mm", "table_area_mm2", "stands_mm2", "thin_parts", "uneven", "thin_limit_mm", "limit",
                "left_in_mesh", "laser_points_removed", "warnings", "summary"):
        assert key in ta, key
    assert set(ta["left_in_mesh"]) >= {"faces", "area_mm2", "pieces"}
    assert isinstance(ta["summary"], str) and ta["summary"]
    assert any("Other" in w and "No table" in w for w in rep["warnings"])
    assert all("\"Front\"" in w or "\"Other\"" in w for w in rep["warnings"])  # each says which scan
    assert len(rep["warnings"]) == len(ta["warnings"]) + len(tb["warnings"])
    import json

    on_disk = json.loads((tmp_path / "m" / "report.json").read_text(encoding="utf-8"))
    assert on_disk["warnings"] == rep["warnings"] and on_disk["scans"][0]["table"]["rule"] == "map"


def test_a_merge_with_the_part_not_moved_is_a_plain_fusion(two_frames, tmp_path, monkeypatch):
    """No supports: no clip, no rule on the mesh, and the merged mesh is byte for byte what fusing the
    frames at their poses gives (the merge before this change did exactly that)."""
    from dataclasses import replace

    import ohs.mesh.support as support_mod
    from ohs.align.merge import ScanInput, merge_scans
    from ohs.app.pipeline import FramePipeline
    from ohs.io.recording import RecordingReader
    from ohs.mesh.confidence import SIGMA_NONE_MM
    from ohs.track.export import clean_mesh, save_mesh

    def never(*a, **k):
        raise AssertionError("the table rule ran on a merge with the part not moved")

    monkeypatch.setattr(support_mod, "table_left", never)
    clips = []
    real = FramePipeline.process

    def spy(self, frame, pose=None, clip_plane=None):
        clips.append(clip_plane)
        return real(self, frame, pose=pose, clip_plane=clip_plane)

    monkeypatch.setattr(FramePipeline, "process", spy)
    T = G.make_se3(G.so3_exp(np.array([0.0, 0.0, 0.3])), [5.0, 0.0, 0.0])
    rep = merge_scans([ScanInput(two_frames["rec"], two_frames["rows"]), ScanInput(two_frames["rec"], two_frames["rows"], T)],
                      tmp_path / "m", _params())
    assert clips and all(c is None for c in clips)
    assert rep["warnings"] == [] and all(s["table"] is None and not s["support_left_out"] for s in rep["scans"])
    monkeypatch.setattr(FramePipeline, "process", real)
    # the same frames fused by hand
    p = _params()
    reader = RecordingReader(two_frames["rec"])
    poses = {i: Tf for i, _, _, Tf in two_frames["rows"]}
    with FramePipeline(reader.calibration(), replace(p.pipeline, tracking=False, use_imu=False)) as pipe:
        for Ts in (np.eye(4), T):
            for pos in range(len(reader)):
                fr = reader.frame(pos)
                pipe.process(fr, pose=Ts @ poses[int(fr.index)])
        V, F, N, S = pipe.mesh_with_sigma()
    if p.clean_min_component > 0 and len(F):
        V, F, idx = clean_mesh(V, F, min_component_fraction=float(p.clean_min_component), return_index=True)
        N = N[idx] if N is not None and len(N) else N
        S = S[idx] if S is not None and len(S) else S
    S = np.minimum(S, np.float32(SIGMA_NONE_MM)).astype(np.float32) if S is not None else None
    save_mesh(tmp_path / "by_hand.ply", V, F, N if N is not None and len(N) == len(V) else None,
              quality=S if S is not None and len(S) == len(V) else None)
    # the same mesh, vertex for vertex and face for face; the GPU model hands its vertices out in an order
    # that changes from run to run, so both are put in one order first
    from ohs.track.export import load_ply

    def canon(path):
        d = load_ply(path)
        V, Fm = d["vertices"], d["faces"]
        o = np.lexsort(V.T[::-1])
        inv = np.empty_like(o)
        inv[o] = np.arange(len(o))
        Fc = np.sort(inv[Fm], axis=1)
        return V[o], Fc[np.lexsort(Fc.T[::-1])], None if d["quality"] is None else d["quality"][o]

    a, b = canon(tmp_path / "by_hand.ply"), canon(tmp_path / "m" / "mesh.ply")
    assert len(a[0]) > 1000 and all(np.array_equal(x, y) for x, y in zip(a, b))
