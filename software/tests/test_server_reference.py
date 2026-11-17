"""A scan compared with a reference model over the API (FEATURE_PLAN.md I2; APP_API.md "References and the
comparison with one"): reference files added to a project (uploaded as the raw request body), listed, renamed,
deleted and served for display; a scan's comparison with one kept in ``comparison.json`` (the units decision,
the alignment and its pose, the tolerance), aligned from the shapes (a job), from picked point pairs or on the
scan's datums, the deviation per vertex of the current mesh and the answer for a tolerance.

Every number must be the engine's (``ohs.inspect.reference``) on the very arrays the server uses
(``ProcessManager.mesh_arrays``). Uses the 10-frame ball-on-a-table scan of ``test_server_process_studio.py``
(its ``env`` fixture), tracked once at preview quality; the reference is the scan's own ball cut out of its mesh,
with one patch moved out by 1 mm (a known difference). A ball has no orientation, so the pose used for the
numbers comes from picked points (the identity: the reference is cut from the scan itself).
"""
from __future__ import annotations

import base64
import hashlib
import json

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

import ohs.measure.features as kf  # noqa: E402
from ohs.align.pairwise import scan_cloud  # noqa: E402
from ohs.inspect.reference import (ReferenceAlignment, ScanSurface, check_units, deviation_from_reference,  # noqa: E402
                                   read_reference, tolerance_summary)
from ohs.measure.frame import PartFrame  # noqa: E402
from ohs.mesh.topology import vertex_normals  # noqa: E402
from ohs.track.export import load_ply, save_mesh  # noqa: E402
from test_server_frame import SHAPES as FRAME_SHAPES, record as frame_record  # noqa: E402
from test_server_process_studio import T, base_url, env, err, faces_b64, get, post, run_job, wait_job  # noqa: E402,F401

MOVED_MM = 1.0  # the patch of the reference moved outward


# --------------------------------------------------------------------------- helpers
def refs_url(e: dict) -> str:
    return f"{e['url']}/api/projects/{e['pid']}/references"


def upload(e: dict, data: bytes, name: str | None, **kw):
    q = {} if name is None else {"name": name}
    return requests.post(refs_url(e), params=q, data=data, headers={"Content-Type": "application/octet-stream"},
                         timeout=T, **kw)


def patch(url: str, body: dict | None):
    return requests.patch(url, json=body, timeout=T)


def delete(url: str):
    return requests.delete(url, timeout=T)


def plain(x):
    return json.loads(json.dumps(x))


def scan_dir(e: dict):
    return e["root"] / "projects" / e["pid"] / "scans" / e["sid"]


def project_dir(e: dict):
    return e["root"] / "projects" / e["pid"]


def manager(e: dict):
    return e["srv"].app.state.references


def arrays(e: dict):
    return e["studio"].processing.mesh_arrays(e["pid"], e["sid"])


def on_disk(d) -> dict:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


def the_ball(e: dict) -> tuple[np.ndarray, np.ndarray]:
    """The scan's own ball, cut out of its current mesh (the faces in front of the table's plane), with the
    patch round its top moved outward by MOVED_MM: the reference."""
    _, V, F, _, _ = arrays(e)
    V = np.asarray(V, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64)
    N = vertex_normals(V, F)
    c = scan_cloud(V, N)
    assert c.support is not None, "the test scan has a table"
    n, d = c.support
    h = V @ n + d
    keep = (h[F] > 2.0).all(axis=1)
    # only the ball: the largest piece of faces joined at their corners (the table keeps a few stray bits)
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    Fk = F[keep]
    A = coo_matrix((np.ones(2 * len(Fk)), (np.r_[Fk[:, 0], Fk[:, 1]], np.r_[Fk[:, 1], Fk[:, 2]])), shape=(len(V), len(V)))
    lab = connected_components(A, directed=False)[1]
    biggest = np.bincount(lab[Fk[:, 0]]).argmax()
    keep[keep] = lab[Fk[:, 0]] == biggest
    used = np.unique(F[keep])
    remap = np.full(len(V), -1)
    remap[used] = np.arange(len(used))
    Vr, Fr, Nr, hr = V[used].copy(), remap[F[keep]], N[used], h[used]
    top = Vr[np.argmax(hr)]
    patch_ = np.linalg.norm(Vr - top, axis=1) < 5.0
    Vr[patch_] += MOVED_MM * Nr[patch_]
    return Vr, Fr


@pytest.fixture(scope="module")
def tracked(env, tmp_path_factory):  # noqa: F811 (the fixture imported above)
    """The ball scan tracked at preview quality, and the reference files: the ball in mm and in inches, a
    STEP file's name, a broken STL. Before there is a mesh the comparison routes answer 409."""
    b = base_url(env)
    tmp = tmp_path_factory.mktemp("refs")
    # a reference can be added before any scan has a mesh; the comparison cannot start
    tri = np.array([[0, 0, 0], [30, 0, 0], [0, 30, 0], [0, 0, 30]], dtype=np.float64)
    save_mesh(tmp / "tetra.stl", tri, np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]))
    r = upload(env, (tmp / "tetra.stl").read_bytes(), "tetra.stl")
    assert r.status_code == 200, r.text
    tetra = r.json()["id"]
    err(post(b + "/comparison", {"reference_id": tetra}), 409, "mesh")
    err(get(b + "/comparison/deviation.json"), 404, "comparison")  # none was started
    assert get(b + "/comparison").json() == {"comparison": None, "mesh_version": None}
    assert delete(f"{refs_url(env)}/{tetra}").status_code == 204
    run_job(env, post(b + "/process/track", {"quality": "preview"}), "process_track")
    Vr, Fr = the_ball(env)
    save_mesh(tmp / "ball.stl", Vr, Fr)
    # in inches as the engine sees the scan's part: its size over the reference's is 25.4. (On the CPU path the
    # preview mesh's noisy table lies more behind its plane than in front, so the engine keeps it as the part's
    # own face and the part reads several times the ball's size: the ball in mm is then asked about too.)
    _, V, F, S, Fl = arrays(env)
    part = ScanSurface(V, F, sigma=S, filled=Fl).part_size_mm()
    ball_mm = read_reference(tmp / "ball.stl").size_mm
    save_mesh(tmp / "ball-inch.stl", Vr * (part / ball_mm / 25.4), Fr)
    env["files"] = {"ball": tmp / "ball.stl", "inch": tmp / "ball-inch.stl"}
    return env


@pytest.fixture()
def ball(tracked):
    """The ball reference uploaded; removed again afterwards (with every comparison that uses it)."""
    r = upload(tracked, tracked["files"]["ball"].read_bytes(), "ball.stl")
    assert r.status_code == 200, r.text
    rec = r.json()
    yield rec
    delete(f"{refs_url(tracked)}/{rec['id']}")
    delete(base_url(tracked) + "/comparison")


def start(e: dict, rid: str, **extra) -> dict:
    """Start a comparison; the units kept as they are when the engine asks about them (the CPU path, above)."""
    r = post(base_url(e) + "/comparison", {"reference_id": rid, **extra})
    assert r.status_code == 200, r.text
    c = r.json()
    if c["units"]["status"] == "pending":
        r = post(base_url(e) + "/comparison/units", {"action": "keep"})
        assert r.status_code == 200, r.text
        c = r.json()
    return c


def align_points(e: dict) -> dict:
    """The identity pose from four picked pairs (the same points on the scan and on the reference), not refined:
    answered at once."""
    _, V, F, _, _ = arrays(e)
    P = np.asarray(V, dtype=np.float64)[np.unique(F)]
    lo, hi = P.min(axis=0), P.max(axis=0)
    pts = [[float(x) for x in p] for p in (lo, hi, [lo[0], hi[1], lo[2]], [hi[0], lo[1], hi[2]])]
    r = post(base_url(e) + "/comparison/align", {"method": "points", "refine": False,
                                                 "pairs": [{"scan": p, "reference": p} for p in pts]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["job"] is None
    return out["comparison"]


def engine_deviation(e: dict, path, T: list):
    """The engine's deviation of the current mesh against the reference file at the pose ``T``."""
    _, V, F, S, Fl = arrays(e)
    scan = ScanSurface(V, F, sigma=S, filled=Fl)
    ref = read_reference(path)
    al = ReferenceAlignment(T=np.asarray(T, dtype=np.float64), method="points", accepted=True, verdict="")
    return scan, ref, deviation_from_reference(scan, ref, al)


def read_bin(e: dict) -> tuple[dict, np.ndarray, np.ndarray]:
    b = base_url(e)
    info = get(b + "/comparison/deviation.json")
    assert info.status_code == 200, info.text
    info = info.json()
    r = get(b + "/comparison/deviation.bin")
    assert r.status_code == 200 and r.headers["X-OHS-Mesh-Version"] == str(info["version"])
    n = info["vertices"]
    assert len(r.content) == 5 * n  # float32 values, then one state byte per vertex
    return info, np.frombuffer(r.content[: 4 * n], dtype="<f4"), np.frombuffer(r.content[4 * n:], dtype=np.uint8)


# --------------------------------------------------------------------------- references of a project
def test_upload_list_rename_serve_and_delete(tracked):
    data = tracked["files"]["ball"].read_bytes()
    r = upload(tracked, data, "ball.stl")
    assert r.status_code == 200, r.text
    rec = r.json()
    ref = read_reference(tracked["files"]["ball"])
    assert rec["name"] == "ball.stl" and rec["format"] == "stl" and rec["bytes"] == len(data)
    assert rec["triangles"] == len(ref.F) and rec["vertices"] == len(ref.V)
    assert rec["extent_mm"] == pytest.approx(ref.extent_mm) and rec["size_mm"] == pytest.approx(ref.size_mm)
    assert rec["added"] and rec["id"].startswith("ref-")
    folder = project_dir(tracked) / "references"
    assert (folder / f"{rec['id']}.stl").read_bytes() == data  # the file as it came
    assert {x["id"] for x in get(refs_url(tracked)).json()["references"]} == {rec["id"]}
    # served for display in the viewer's binary form (a PLY of its triangles, not decimated at this size)
    m = get(f"{refs_url(tracked)}/{rec['id']}/mesh.ply")
    assert m.status_code == 200 and m.content.startswith(b"ply")
    p = tracked["root"] / "ref-view.ply"
    p.write_bytes(m.content)
    assert len(load_ply(p)["faces"]) == rec["display"]["triangles"] == len(ref.F) and rec["display"]["decimated"] is False
    # rename: only the name changes
    r = patch(f"{refs_url(tracked)}/{rec['id']}", {"name": "  Ball as drawn  "})
    assert r.status_code == 200 and r.json()["name"] == "Ball as drawn" and r.json()["triangles"] == rec["triangles"]
    assert get(refs_url(tracked)).json()["references"][0]["name"] == "Ball as drawn"
    err(patch(f"{refs_url(tracked)}/{rec['id']}", {"name": "   "}), 422)
    err(patch(f"{refs_url(tracked)}/{rec['id']}", {}), 422)
    # delete: its files go, the list is empty
    assert delete(f"{refs_url(tracked)}/{rec['id']}").status_code == 204
    assert get(refs_url(tracked)).json()["references"] == []
    assert not any(p.name.startswith(rec["id"]) for p in folder.iterdir())
    for call in (lambda u: get(u + "/mesh.ply"), lambda u: patch(u, {"name": "x"}), delete):
        err(call(f"{refs_url(tracked)}/{rec['id']}"), 404, "reference")
    err(get(f"{tracked['url']}/api/projects/no-such-project/references"), 404)


def test_wrong_formats_broken_files_and_oversized_bodies_are_refused(tracked):
    folder = project_dir(tracked) / "references"
    before = sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []
    data = tracked["files"]["ball"].read_bytes()
    # another format: the engine's own words
    with pytest.raises(ValueError) as refused:
        read_reference(tracked["root"] / "part.step")
    r = err(upload(tracked, b"ISO-10303-21;", "part.step"), 422, "STL")
    assert r["detail"] == str(refused.value) and "export" in r["detail"]
    err(upload(tracked, data, "ball"), 422)  # no extension
    err(upload(tracked, data, None), 422, "name")  # no name at all
    err(upload(tracked, b"", "empty.stl"), 422)
    err(upload(tracked, b"solid nothing\nendsolid\n", "broken.stl"), 422)
    # too large: refused before it is read (with a Content-Length, and streamed without one)
    m = manager(tracked)
    limit = m.max_bytes
    try:
        m.max_bytes = 1000
        err(upload(tracked, data, "ball.stl"), 413, "MB")
        err(upload(tracked, (data[i:i + 700] for i in range(0, len(data), 700)), "ball.stl"), 413)
    finally:
        m.max_bytes = limit
    assert (sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []) == before  # nothing left behind
    assert get(refs_url(tracked)).json()["references"] == []


def test_a_name_with_path_parts_is_not_used_as_a_path(tracked):
    data = tracked["files"]["ball"].read_bytes()
    root = tracked["root"]
    before = on_disk(root / "projects")
    for name in ("../../evil.stl", "..\\..\\evil.stl", "C:\\temp\\evil.stl", "/etc/evil.stl"):
        r = upload(tracked, data, name)
        assert r.status_code == 200, r.text
        rec = r.json()
        assert rec["name"] == "evil.stl"
        assert delete(f"{refs_url(tracked)}/{rec['id']}").status_code == 204
    assert not any(p.name == "evil.stl" for p in root.rglob("*"))
    assert on_disk(root / "projects") == before
    err(upload(tracked, data, ".."), 422)


# --------------------------------------------------------------------------- the comparison
def test_the_units_finding_and_both_ways_out_of_it(tracked):
    b = base_url(tracked)
    rec = upload(tracked, tracked["files"]["inch"].read_bytes(), "ball-inch.stl").json()
    try:
        c = post(b + "/comparison", {"reference_id": rec["id"]})
        assert c.status_code == 200, c.text
        c = c.json()
        scan = ScanSurface(*arrays(tracked)[1:3], sigma=arrays(tracked)[3], filled=arrays(tracked)[4])
        want = check_units(read_reference(tracked["files"]["inch"]).size_mm, scan.part_size_mm())
        assert want.status == "pending" and want.unit == "inch"
        assert c["units"] == plain(want.to_dict()) and c["alignment"] is None and c["scale"] == 1.0
        assert json.loads((scan_dir(tracked) / "comparison.json").read_text())["units"]["status"] == "pending"
        # nothing more until it is settled
        for method in ("shape", "datums"):
            err(post(b + "/comparison/align", {"method": method}), 409, "units")
        err(get(b + "/comparison/deviation.json"), 409)
        err(post(b + "/comparison/tolerance", {"tolerance_mm": 0.3}), 409)
        # apply the factor: 25.4
        c = post(b + "/comparison/units", {"action": "apply"})
        assert c.status_code == 200, c.text
        c = c.json()
        assert c["units"]["status"] == "applied" and c["units"]["factor"] == 25.4 and c["scale"] == 25.4
        assert get(b + "/comparison").json()["comparison"]["units"]["status"] == "applied"
        c = align_points(tracked)
        assert c["alignment"]["method"] == "points"
        # or keep it as it is: the alignment made at the other size goes
        c = post(b + "/comparison/units", {"action": "keep"}).json()
        assert c["units"]["status"] == "kept" and c["scale"] == 1.0 and c["alignment"] is None
        err(post(b + "/comparison/units", {"action": "guess"}), 422)
        err(post(b + "/comparison/units", {"action": "apply", "factor": -2}), 422)
        # the ball in mm: the engine's finding for it (nothing to confirm on the default path)
        rec2 = upload(tracked, tracked["files"]["ball"].read_bytes(), "ball.stl").json()
        c = post(b + "/comparison", {"reference_id": rec2["id"]}).json()
        want = check_units(read_reference(tracked["files"]["ball"]).size_mm, scan.part_size_mm())
        assert c["units"] == plain(want.to_dict()) and c["reference"]["id"] == rec2["id"]
        delete(f"{refs_url(tracked)}/{rec2['id']}")
    finally:
        delete(f"{refs_url(tracked)}/{rec['id']}")
        delete(b + "/comparison")


def test_points_deviation_and_tolerance_equal_the_engine(tracked, ball):
    b = base_url(tracked)
    start(tracked, ball["id"], tolerance_mm=0.5)
    c = align_points(tracked)
    al = c["alignment"]
    assert al["method"] == "points" and al["accepted"] and al["n_points"] == 4
    assert np.allclose(al["T"], np.eye(4), atol=1e-9)
    assert c["aligned_mesh_version"] == c["mesh_version"] and c["mesh_changed"] is False
    assert np.allclose(c["view_matrix"], np.linalg.inv(np.asarray(al["T"])), atol=1e-12)
    # the deviation: the engine's, value for value and state for state
    info, values, state = read_bin(tracked)
    scan, ref, dev = engine_deviation(tracked, tracked["files"]["ball"], al["T"])
    assert info["vertices"] == len(dev.V) and info["mesh_changed"] is False
    assert np.array_equal(values, dev.offset, equal_nan=True)
    assert np.array_equal(state, dev.state.astype(np.uint8))
    assert np.isnan(values[state != 0]).all() and np.isfinite(values[state == 0]).all()
    assert (state == 0).sum() > 100 and (state == 2).sum() > 100  # the ball compared, the table not on the reference
    assert info["states"] == ["compared", "not measured", "not on the reference"]
    for k in ("n_compared", "n_off_reference", "n_not_measured", "compared_area_mm2", "uncovered_share"):
        assert info[k] == plain(dev.to_dict())[k], k
    # the answer for a tolerance: the engine's tolerance_summary on the same arrays
    for tol in (0.5, 0.2):
        r = post(b + "/comparison/tolerance", {"tolerance_mm": tol})
        assert r.status_code == 200, r.text
        got = r.json()
        want = plain(tolerance_summary(dev, tol).to_dict())
        assert {k: got[k] for k in want if k != "regions"} == {k: v for k, v in want.items() if k != "regions"}
        assert [{k: x[k] for k in w} for x, w in zip(got["regions"], want["regions"])] == want["regions"]
        assert len(got["regions"]) == len(want["regions"]) and got["version"] == info["version"]
    # the moved patch: a region below the tolerance about 1 mm in, beyond the scan's own uncertainty
    got = post(b + "/comparison/tolerance", {"tolerance_mm": 0.5}).json()
    below = [x for x in got["regions"] if x["side"] == "below"]
    assert below and abs(below[0]["worst_mm"] + MOVED_MM) < 0.35, got["regions"]
    rg = below[0]
    faces = np.frombuffer(base64.b64decode(rg["faces"]), dtype="<u4")
    assert len(faces) == rg["n_faces"]
    assert np.allclose(rg["center_scan"], rg["center"], atol=1e-6)  # the identity pose
    assert got["coverage"]["off_reference_share"] == pytest.approx(dev.off_reference_share)
    # the tolerance is kept
    assert json.loads((scan_dir(tracked) / "comparison.json").read_text())["tolerance_mm"] == 0.5
    err(post(b + "/comparison/tolerance", {"tolerance_mm": 0}), 422)
    err(post(b + "/comparison/tolerance", {"tolerance_mm": "wide"}), 422)


def test_the_scans_other_files_stay_byte_identical(tracked, ball):
    b = base_url(tracked)
    before = on_disk(scan_dir(tracked))
    start(tracked, ball["id"])
    align_points(tracked)
    read_bin(tracked)
    post(b + "/comparison/tolerance", {"tolerance_mm": 0.3})
    after = on_disk(scan_dir(tracked))
    assert set(after) - set(before) == {"comparison.json"}
    assert {k: v for k, v in after.items() if k != "comparison.json"} == before
    assert delete(b + "/comparison").status_code == 204
    assert on_disk(scan_dir(tracked)) == before
    err(delete(b + "/comparison"), 404, "comparison")
    assert get(b + "/comparison").json()["comparison"] is None


def test_shape_alignment_runs_as_a_job_and_can_be_cancelled(tracked, ball):
    b = base_url(tracked)
    start(tracked, ball["id"])
    r = post(b + "/comparison/align", {"method": "shape"})
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    assert job["kind"] == "reference_align" and r.json()["comparison"] is None
    requests.post(f"{tracked['url']}/api/jobs/{job['id']}/cancel", timeout=T)
    done = wait_job(tracked, job["id"])
    assert done["state"] == "cancelled", done
    assert get(b + "/comparison").json()["comparison"]["alignment"] is None  # nothing kept


def test_datums_refused_without_a_frame_then_on_the_frame_and_a_best_fit_beside_it(tracked, ball):
    b = base_url(tracked)
    sd = scan_dir(tracked)
    start(tracked, ball["id"])
    err(post(b + "/comparison/align", {"method": "datums"}), 422, "frame")
    v = int(get(b + "/process").json()["current"]["version"])
    doc = kf.empty()
    doc["features"] = [frame_record(fid, v) for fid in FRAME_SHAPES]
    kf.save(sd, doc)
    try:
        assert post(b + "/features/frame", {"primary": "f-top", "secondary": "f-side", "origin": "f-hole"}).status_code == 200
        frame = kf.load(sd)["frame"]
        r = post(b + "/comparison/align", {"method": "datums"})
        assert r.status_code == 200, r.text
        c = r.json()["comparison"]
        assert c["alignment"]["method"] == "datums" and c["alignment"]["accepted"]
        assert np.allclose(c["alignment"]["T"], PartFrame.from_dict(frame).matrix, atol=1e-12)
        # a best fit from the shape: a job to done, which also says how far it lies from the datum pose
        job = post(b + "/comparison/align", {"method": "shape"}).json()["job"]
        done = wait_job(tracked, job["id"])
        assert done["state"] == "done", done
        res = done["result"]
        assert res["alignment"]["method"] == "shape" and res["alignment"]["vs_datums"]["rms_mm"] is not None
        assert res["kept"] is res["alignment"]["accepted"]
        kept = get(b + "/comparison").json()["comparison"]["alignment"]
        assert kept["method"] == ("shape" if res["kept"] else "datums")
        # a frame that is out of date: refused in words
        doc = kf.load(sd)
        doc["features"] = [f for f in doc["features"] if f["id"] != "f-hole"]
        kf.save(sd, doc)
        err(post(b + "/comparison/align", {"method": "datums"}), 409, "out of date")
    finally:
        kf.save(sd, kf.empty())


def test_bad_bodies_unknown_ids_and_a_busy_or_missing_comparison(tracked, ball):
    b = base_url(tracked)
    err(post(b + "/comparison/align", {"method": "shape"}), 404, "comparison")
    err(post(b + "/comparison/units", {"action": "keep"}), 404, "comparison")
    err(get(b + "/comparison/deviation.json"), 404, "comparison")
    err(post(b + "/comparison", {"reference_id": "r-nothing"}), 404, "reference")
    err(post(b + "/comparison", {}), 422)
    err(post(b + "/comparison", {"reference_id": ball["id"], "tolerance_mm": -1}), 422)
    err(get(f"{tracked['url']}/api/projects/{tracked['pid']}/scans/scan-nope/comparison"), 404)
    start(tracked, ball["id"])
    err(get(b + "/comparison/deviation.json"), 409, "align")  # not aligned yet
    err(post(b + "/comparison/align", {"method": "sideways"}), 422)
    err(post(b + "/comparison/align", {"method": "points", "pairs": [{"scan": [0, 0, 0], "reference": [0, 0, 0]}]}), 422)
    err(post(b + "/comparison/align", {"method": "points", "pairs": [{"scan": [0, 0, i], "reference": [0, 0, i]}
                                                                     for i in range(4)], "refine": False}), 422)
    err(post(b + "/comparison/align", {"method": "points"}), 422)


def test_kept_across_a_reload_and_worked_out_again_after_an_edit(tracked, ball):
    b = base_url(tracked)
    start(tracked, ball["id"], tolerance_mm=0.4)
    c0 = align_points(tracked)
    info0, values0, _ = read_bin(tracked)
    # the app starts again: nothing in memory, the comparison is read back from its file
    manager(tracked).clear_cache()
    c1 = get(b + "/comparison").json()["comparison"]
    assert c1["alignment"] == c0["alignment"] and c1["tolerance_mm"] == 0.4 and c1["reference"]["id"] == ball["id"]
    info1, values1, _ = read_bin(tracked)
    assert info1["version"] == info0["version"] and np.array_equal(values1, values0, equal_nan=True)
    # an edit: the pose stays, the deviation is worked out again on the current mesh, and the answer says so
    _, V, F, _, _ = arrays(tracked)
    cut = np.arange(0, len(F), 97)
    run_job(tracked, post(b + "/process/edits", {"op": "delete_faces", "params": {"faces": faces_b64(cut)},
                                                 "version": info0["version"]}), "mesh_edit")
    info2, values2, state2 = read_bin(tracked)
    assert info2["version"] != info0["version"] and info2["mesh_changed"] is True and info2["note"]
    assert info2["aligned_mesh_version"] == info0["version"]
    c2 = get(b + "/comparison").json()["comparison"]
    assert c2["mesh_changed"] is True and c2["alignment"]["T"] == c0["alignment"]["T"]
    _, _, dev = engine_deviation(tracked, tracked["files"]["ball"], c0["alignment"]["T"])
    assert len(values2) == len(dev.V) and np.array_equal(values2, dev.offset, equal_nan=True)
    assert post(b + "/comparison/tolerance", {"tolerance_mm": 0.4}).json()["mesh_changed"] is True
    # deleting the reference removes the comparisons that use it
    assert delete(f"{refs_url(tracked)}/{ball['id']}").status_code == 204
    assert not (scan_dir(tracked) / "comparison.json").exists()
    assert get(b + "/comparison").json()["comparison"] is None
