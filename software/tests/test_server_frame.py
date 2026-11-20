"""The part's coordinate system over the API (APP_API.md "Kept features", "The part's frame";
FEATURE_PLAN.md K3): built from three kept fits with ``ohs.measure.frame.part_frame``, kept under
``frame`` in ``features.json``, read back with whether it is still current, removed, and the mesh
downloaded in it.

Uses the 10-frame sphere scan of ``test_server_process_studio.py`` (its ``env`` fixture), tracked
once at preview quality. A ball on a table cannot give a frame from real fits, so the kept fits are
written straight into ``features.json``: a box with a hole, at a pose that lines up with nothing,
fitted on the current mesh version.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

import ohs.measure.features as kf  # noqa: E402
from ohs.measure.frame import PartFrame, part_frame  # noqa: E402
from ohs.track.export import load_ply  # noqa: E402
from test_server_process_studio import T, base_url, env, err, get, post, run_job  # noqa: E402,F401

# the box's place in the scan's frame: a rotation and a translation that line up with nothing
_Q, _ = np.linalg.qr(np.random.default_rng(7).normal(size=(3, 3)))
Q = _Q if np.linalg.det(_Q) > 0 else -_Q
OFFSET = np.array([12.0, -30.0, 260.0])


def P(x) -> list[float]:
    return [float(v) for v in np.asarray(x, dtype=np.float64) @ Q.T + OFFSET]


def D(d) -> list[float]:
    return [float(v) for v in np.asarray(d, dtype=np.float64) @ Q.T]


def plane(point, normal) -> dict:
    return {"kind": "plane", "point": P(point), "normal": D(normal), "rms_mm": 0.02, "flatness_mm": 0.1, "n": 900,
            "normal_sigma_deg": 0.03, "offset_sigma_mm": 0.004}


def circle(center, normal, r=3.0) -> dict:
    return {"kind": "circle", "center": P(center), "normal": D(normal), "radius_mm": r, "diameter_mm": 2 * r,
            "rms_mm": 0.01, "n": 80, "radius_sigma_mm": 0.003, "diameter_sigma_mm": 0.006, "center_sigma_mm": 0.004}


def cylinder(point, axis, r=2.0) -> dict:
    return {"kind": "cylinder", "point": P(point), "axis": D(axis), "radius_mm": r, "diameter_mm": 2 * r,
            "rms_mm": 0.01, "n": 300, "radius_sigma_mm": 0.003, "diameter_sigma_mm": 0.006, "axis_sigma_deg": 0.05,
            "point_sigma_mm": 0.005}


# a 20 mm box centred at its own origin, a hole in its top face
SHAPES = {
    "f-top": ("Top", "plane", plane([0, 0, 10], [0, 0, 1])),
    "f-side": ("Side", "plane", plane([10, 0, 0], [1, 0, 0])),
    "f-bottom": ("Bottom", "plane", plane([0, 0, -10], [0, 0, 1])),
    "f-hole": ("Hole", "hole", circle([3, -2, 10], [0, 0, 1])),
    "f-hole2": ("Dowel", "hole", circle([-5, 4, 10], [0, 0, 1], 2.0)),
    "f-lying": ("Lying pin", "cylinder", cylinder([0, 4, 10], [1, 0, 0])),
}


def record(fid: str, version: int) -> dict:
    name, kind, shape = SHAPES[fid]
    return {"id": fid, "name": name, "kind": kind, "shape": shape,
            "seed": {"kind": kind, "face": 0, "point": shape.get("point", shape.get("center")), "tolerance_mm": None},
            "n_faces": 100, "tolerance_mm": None, "mean_sigma_mm": None, "correlated_points": None, "warnings": [],
            "mesh_version": version, "extent": None, "created": "2026-11-17T12:00:00"}


def kept(fid: str) -> dict:
    """A slot's input as the server hands it to part_frame: the kept fit's id, name and shape."""
    name, _, shape = SHAPES[fid]
    return {"id": fid, "name": name, "fit": shape}


def scan_dir(e: dict):
    return e["root"] / "projects" / e["pid"] / "scans" / e["sid"]


def delete(url: str):
    return requests.delete(url, timeout=T)


def version(e: dict) -> int:
    return int(get(base_url(e) + "/process").json()["current"]["version"])


@pytest.fixture(scope="module")
def tracked(env):  # noqa: F811 (the fixture imported above)
    """The sphere scan tracked at preview quality; the frame routes answer before there is a mesh first."""
    b = base_url(env)
    err(post(b + "/features/frame", {"primary": "f-top", "secondary": "f-side", "origin": "f-hole"}), 409, "mesh")
    err(get(b + "/features/frame/mesh.ply"), 404, "frame")
    run_job(env, post(b + "/process/track", {"quality": "preview"}), "process_track")
    return env


@pytest.fixture()
def box(tracked):
    """The box's fits kept on the current mesh, no frame; everything else in features.json cleared."""
    sd = scan_dir(tracked)
    doc = kf.empty()
    v = version(tracked)
    doc["features"] = [record(fid, v) for fid in SHAPES]
    kf.save(sd, doc)
    yield tracked
    kf.save(sd, kf.empty())


def body(primary="f-top", secondary="f-side", origin="f-hole", **opts) -> dict:
    return {"primary": primary, "secondary": secondary, "origin": origin, **opts}


def frame_of(e: dict) -> dict | None:
    s = get(base_url(e) + "/features")
    assert s.status_code == 200, s.text
    return s.json()["frame"]


def test_build_read_back_and_remove(box):
    b = base_url(box)
    assert frame_of(box) is None
    r = post(b + "/features/frame", body())
    assert r.status_code == 200, r.text
    built = r.json()
    # kept exactly as the engine's record for the same fits
    want = part_frame(kept("f-top"), kept("f-side"), kept("f-hole")).to_dict()
    stored = kf.load(scan_dir(box))["frame"]
    assert stored == want
    assert built == {**want, "readable": True, "out_of_date": False, "out_of_date_reasons": []}
    # read back: the same, current
    assert frame_of(box) == built
    pf = PartFrame.from_dict(stored)
    assert np.allclose(pf.points_to_part(SHAPES["f-hole"][2]["center"]), 0.0, atol=1e-9)  # the hole is the origin
    assert [x["id"] for x in stored["built_from"]] == ["f-top", "f-side", "f-hole"]
    assert [x["name"] for x in stored["built_from"]] == ["Top", "Side", "Hole"]
    # the features are untouched
    assert [f["id"] for f in get(b + "/features").json()["features"]] == list(SHAPES)
    # remove: back to null, twice is fine
    assert delete(b + "/features/frame").status_code == 204
    assert frame_of(box) is None and kf.load(scan_dir(box))["frame"] is None
    assert delete(b + "/features/frame").status_code == 204
    assert [f["id"] for f in get(b + "/features").json()["features"]] == list(SHAPES)


def test_the_options_and_a_point_as_the_origin(box):
    b = base_url(box)
    opts = {"axes": "XY", "flip_primary": True, "flip_secondary": False, "secondary_zero": True}
    r = post(b + "/features/frame", body(**opts))
    assert r.status_code == 200, r.text
    want = part_frame(kept("f-top"), kept("f-side"), kept("f-hole"), axes="XY", flip_primary=True,
                      secondary_zero=True).to_dict()
    assert kf.load(scan_dir(box))["frame"] == want
    assert r.json()["axes"] == {"primary": "X", "secondary": "Y", "third": "Z"} and r.json()["secondary_zero"] is True
    # a point as the origin (with and without its ±)
    pt = P([-8, -8, 10])
    for origin, extra in (({"point": pt, "sigma_mm": 0.01}, {"sigma_mm": 0.01}), ({"point": pt}, {})):
        r = post(b + "/features/frame", body(origin=origin))
        assert r.status_code == 200, r.text
        want = part_frame(kept("f-top"), kept("f-side"), {"kind": "point", "point": pt, **extra}).to_dict()
        assert kf.load(scan_dir(box))["frame"] == want
        assert r.json()["built_from"][2]["id"] is None and r.json()["out_of_date"] is False
    assert any("±" in w for w in r.json()["warnings"])  # a point without its ±: said so
    err(post(b + "/features/frame", body(origin={"point": [1, 2]})), 422)
    err(post(b + "/features/frame", body(origin={"point": pt, "sigma_mm": -1})), 422)
    err(post(b + "/features/frame", {"primary": "f-top", "secondary": "f-side"}), 422)
    err(post(b + "/features/frame", body(flip_primary="sideways")), 422)


@pytest.mark.parametrize("slots, opts", [
    (("f-hole", "f-side", "f-hole"), {}),  # the primary must be a plane
    (("f-top", "f-bottom", "f-hole"), {}),  # a secondary face parallel to the primary
    (("f-top", "f-hole2", "f-side"), {}),  # a hole across the face needs a point-like origin
    (("f-top", "f-hole2", "f-hole"), {"secondary_zero": True}),  # strict 3-2-1 with a hole across the face
    (("f-top", "f-side", "f-lying"), {}),  # an origin cylinder that does not cross the primary
    (("f-top", "f-side", "f-bottom"), {}),  # an origin face that does not fix the origin
    (("f-top", "f-hole", "f-hole"), {}),  # a secondary hole on the origin
    (("f-top", "f-side", "f-hole"), {"axes": "XX"}),  # axes that are not two different letters
])
def test_each_refusal_answers_422_with_the_engines_words(box, slots, opts):
    with pytest.raises(ValueError) as refused:
        part_frame(*(kept(s) for s in slots), **opts)
    r = err(post(base_url(box) + "/features/frame", body(*slots, **opts)), 422)
    assert r["detail"] == str(refused.value)
    assert kf.load(scan_dir(box))["frame"] is None  # nothing kept


def test_unknown_and_stale_fits_are_refused(box):
    b = base_url(box)
    err(post(b + "/features/frame", body(origin="f-nope")), 422, "f-nope")
    sd = scan_dir(box)
    doc = kf.load(sd)
    doc["features"][3]["mesh_version"] = version(box) - 1  # the hole, fitted on an earlier mesh
    kf.save(sd, doc)
    r = err(post(b + "/features/frame", body()), 409, "Hole")
    assert "again" in r["hint"]
    assert kf.load(sd)["frame"] is None


def test_out_of_date_when_a_fit_changes_or_goes_and_build_again_clears_it(box):
    b = base_url(box)
    sd = scan_dir(box)
    assert post(b + "/features/frame", body()).status_code == 200
    # renaming a fit changes nothing
    assert requests.patch(f"{b}/features/f-hole", json={"name": "Dowel hole"}, timeout=T).status_code == 200
    f = frame_of(box)
    assert f["out_of_date"] is False and f["out_of_date_reasons"] == [] and f["readable"] is True
    # the hole changed (as after "fit again"): out of date, and why; nothing is rebuilt on its own
    doc = kf.load(sd)
    before = doc["frame"]
    hole = next(x for x in doc["features"] if x["id"] == "f-hole")
    hole["shape"] = {**hole["shape"], "center": P([3.2, -2, 10])}
    kf.save(sd, doc)
    for _ in range(2):
        f = frame_of(box)
        assert f["out_of_date"] is True and len(f["out_of_date_reasons"]) == 1
        assert "Dowel hole" in f["out_of_date_reasons"][0] and "origin" in f["out_of_date_reasons"][0]
        assert kf.load(sd)["frame"] == before
    # build again with the same fits and options: current again
    r = post(b + "/features/frame", body())
    assert r.status_code == 200 and r.json()["out_of_date"] is False
    assert kf.load(sd)["frame"] != before
    # a fit it was built from is deleted
    assert delete(f"{b}/features/f-side").status_code == 204
    f = frame_of(box)
    assert f["out_of_date"] is True and any("Side" in x and "deleted" in x for x in f["out_of_date_reasons"])
    assert f["matrix"] == r.json()["matrix"]  # still there, as it was


def test_a_frame_this_version_cannot_read_still_loads_the_features(box):
    sd = scan_dir(box)
    doc = kf.load(sd)
    doc["frame"] = {"format_version": 99, "matrix": "from the future"}
    kf.save(sd, doc)
    s = get(base_url(box) + "/features")
    assert s.status_code == 200, s.text
    s = s.json()
    assert [f["id"] for f in s["features"]] == list(SHAPES)
    assert s["frame"]["format_version"] == 99 and s["frame"]["readable"] is False and s["frame"]["out_of_date"] is True
    assert "99" in s["frame"]["out_of_date_reasons"][0]
    err(get(base_url(box) + "/features/frame/mesh.ply"), 409, "read")
    assert kf.load(sd)["frame"] == {"format_version": 99, "matrix": "from the future"}  # kept as it was
    # building a new one replaces it
    assert post(base_url(box) + "/features/frame", body()).status_code == 200
    assert kf.load(sd)["frame"]["format_version"] == 1


def _stl_triangles(raw: bytes) -> np.ndarray:
    n = int(np.frombuffer(raw, dtype="<u4", count=1, offset=80)[0])
    assert len(raw) == 84 + 50 * n
    return np.frombuffer(raw, dtype=np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")]), count=n,
                         offset=84)


def _result_files(e: dict) -> dict:
    d = scan_dir(e) / "result"
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir()) if p.is_file()}


def test_the_mesh_in_the_parts_frame(box, tmp_path):
    b = base_url(box)
    err(get(b + "/features/frame/mesh.ply"), 404, "frame")
    names = ("mesh.ply", "mesh.stl")
    before = {n: get(f"{b}/files/{n}").content for n in names}
    on_disk = _result_files(box)
    r = post(b + "/features/frame", body())
    assert r.status_code == 200, r.text
    pf = PartFrame.from_dict(r.json())
    R, t = pf.rotation, pf.matrix[:3, 3]

    ply = get(b + "/features/frame/mesh.ply")
    assert ply.status_code == 200 and ply.content.startswith(b"ply")
    assert "part-frame" in ply.headers["content-disposition"] and "mesh.ply" not in ply.headers["content-disposition"]
    assert int(ply.headers["x-ohs-mesh-version"]) == version(box)
    (tmp_path / "scan.ply").write_bytes(before["mesh.ply"])
    (tmp_path / "part.ply").write_bytes(ply.content)
    scan, part = load_ply(tmp_path / "scan.ply"), load_ply(tmp_path / "part.ply")
    assert np.array_equal(part["faces"], scan["faces"])
    assert np.allclose(part["vertices"], scan["vertices"] @ R.T + t, rtol=0, atol=1e-4)
    assert np.allclose(part["normals"], scan["normals"] @ R.T, rtol=0, atol=1e-6)
    for key in ("quality", "filled", "looks"):  # each vertex's sigma and looks and each face's filled flag go along
        assert (part[key] is None) == (scan[key] is None)
        if scan[key] is not None:
            assert np.array_equal(part[key], scan[key])

    stl = get(b + "/features/frame/mesh.stl")
    assert stl.status_code == 200 and "part-frame" in stl.headers["content-disposition"]
    s0, s1 = _stl_triangles(before["mesh.stl"]), _stl_triangles(stl.content)
    assert len(s0) == len(s1)
    assert np.allclose(s1["v"], s0["v"].astype(np.float64) @ R.T + t, rtol=0, atol=1e-4)
    assert np.allclose(s1["n"], s0["n"].astype(np.float64) @ R.T, rtol=0, atol=1e-5)
    err(get(b + "/features/frame/mesh.obj"), 404)

    # the ordinary downloads and the files on disk are as they were
    assert {n: get(f"{b}/files/{n}").content for n in names} == before
    assert _result_files(box) == on_disk
    assert delete(b + "/features/frame").status_code == 204
    err(get(b + "/features/frame/mesh.stl"), 404, "frame")
