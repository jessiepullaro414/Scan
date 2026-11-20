"""Sections over the API (FEATURE_PLAN.md I1; APP_API.md "Sections"): the scan's current mesh cut by a
plane named one of four ways (a kept plane moved along its normal, a plane of the part's frame, a
point and a normal, three points), answered as ``ohs.mesh.section``'s cut; the DXF and SVG of the
same cut as downloads; sections kept with the scan under ``sections`` in ``features.json``, listed,
renamed, deleted, and cut again on the current mesh when opened.

Every cut must equal the engine's ``section`` on the very arrays the server cuts
(``ProcessManager.mesh_arrays``), with the plane built by the engine's own plane functions; the files
must equal ``dxf_text`` / ``svg_text`` of that cut. Uses the 10-frame sphere scan of
``test_server_process_studio.py`` (its ``env`` fixture), tracked once at preview quality; the kept
fits and the part's frame are written straight into ``features.json`` (as ``test_server_frame.py``
does).
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

import ohs.measure.features as kf  # noqa: E402
from ohs.cad.dxf import dxf_text  # noqa: E402
from ohs.cad.svg import svg_text  # noqa: E402
from ohs.measure.frame import PartFrame, part_frame  # noqa: E402
from ohs.mesh import section as sec  # noqa: E402
from ohs.mesh.confidence import SIGMA_NONE_MM  # noqa: E402
from test_server_process_studio import T, base_url, env, err, faces_b64, get, post, run_job  # noqa: E402,F401

ENGINE_LINE_KEYS = ("closed", "length_mm", "area_mm2", "bbox", "points", "points3d", "sigma", "filled", "faces")


# --------------------------------------------------------------------------- helpers
def scan_dir(e: dict):
    return e["root"] / "projects" / e["pid"] / "scans" / e["sid"]


def patch(url: str, body: dict | None):
    return requests.patch(url, json=body, timeout=T)


def delete(url: str):
    return requests.delete(url, timeout=T)


def arrays(e: dict):
    """The arrays the server cuts: (version, V, F, sigma, filled)."""
    return e["studio"].processing.mesh_arrays(e["pid"], e["sid"])


def plain(x):
    """A value as it comes back through JSON."""
    return json.loads(json.dumps(x))


def engine_cut(e: dict, plane) -> tuple[int, sec.Section]:
    v, V, F, S, Fl = arrays(e)
    return v, sec.section(V, F, plane, sigma=S, filled=Fl)


def runs(filled) -> list[list]:
    """A line's stretches as the API gives them: [first segment, end (exclusive), filled]."""
    f = [bool(x) for x in filled]
    out, start = [], 0
    for i in range(1, len(f) + 1):
        if i == len(f) or f[i] != f[start]:
            out.append([start, i, f[start]])
            start = i
    return out


def same_cut(answer: dict, s: sec.Section) -> None:
    """The answer (full detail) is the engine's section, number for number, with each line's stretches."""
    want = plain(s.to_dict())
    for key in ("format_version", "units", "plane", "length_mm", "n_points", "bbox"):
        assert answer[key] == want[key], key
    assert len(answer["lines"]) == len(want["lines"])
    for got, ln, wl in zip(answer["lines"], s.lines, want["lines"]):
        assert {k: got[k] for k in ENGINE_LINE_KEYS} == wl
        assert got["stretches"] == runs(ln.filled)
    assert answer["n_lines"] == len(s.lines) and answer["n_closed"] == sum(ln.closed for ln in s.lines)


def on_disk(e: dict) -> dict:
    d = scan_dir(e)
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


def plane_shape(point, normal) -> dict:
    n = np.asarray(normal, dtype=np.float64)
    n = n / np.linalg.norm(n)
    return {"kind": "plane", "point": [float(x) for x in point], "normal": [float(x) for x in n], "rms_mm": 0.02,
            "flatness_mm": 0.1, "n": 900, "normal_sigma_deg": 0.03, "offset_sigma_mm": 0.004}


def record(fid: str, name: str, shape: dict, version: int, kind: str = "plane") -> dict:
    point = shape.get("point", shape.get("center"))
    return {"id": fid, "name": name, "kind": kind, "shape": shape,
            "seed": {"kind": kind, "face": 0, "point": point, "tolerance_mm": None}, "n_faces": 100,
            "tolerance_mm": None, "mean_sigma_mm": None, "correlated_points": None, "warnings": [],
            "mesh_version": version, "extent": None, "created": "2026-11-17T12:00:00"}


def centre(e: dict) -> np.ndarray:
    _, V, F, _, _ = arrays(e)
    return np.asarray(V, dtype=np.float64)[np.unique(F)].mean(axis=0)


def shapes(e: dict) -> dict:
    """Kept planes through the middle of the mesh: an upright one (it cuts the ball and the table) and two
    for the part's frame, and a cylinder (not a plane)."""
    c = centre(e)
    return {"f-cut": ("Upright", plane_shape(c, [1.0, 0.15, 0.1]), "plane"),
            "f-top": ("Top", plane_shape(c, [0.0, 0.0, 1.0]), "plane"),
            "f-side": ("Side", plane_shape(c + [5.0, 0, 0], [1.0, 0.0, 0.0]), "plane"),
            "f-pin": ("Pin", {"kind": "cylinder", "point": [float(x) for x in c], "axis": [0.0, 0.0, 1.0],
                              "radius_mm": 2.0, "diameter_mm": 4.0, "rms_mm": 0.01, "n": 300}, "cylinder")}


def kept_fit(e: dict, fid: str) -> dict:
    return next(f for f in kf.load(scan_dir(e))["features"] if f["id"] == fid)


def the_frame(e: dict) -> PartFrame:
    return PartFrame.from_dict(kf.load(scan_dir(e))["frame"])


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def tracked(env):  # noqa: F811 (the fixture imported above)
    """The sphere scan tracked at preview quality; the routes answer before there is a mesh first."""
    b = base_url(env)
    body = {"kind": "point_normal", "point": [0, 0, 250], "normal": [0, 0, 1]}
    err(post(b + "/section", body), 409, "mesh")
    err(post(b + "/sections", body), 409, "mesh")
    err(requests.get(b + "/section/section.dxf", params={"kind": "point_normal", "point": "0,0,250", "normal": "0,0,1"},
                     timeout=T), 409, "mesh")
    s = get(b + "/sections")
    assert s.status_code == 200, s.text
    assert s.json() == {"mesh_version": None, "sections": []}
    err(get(base_url(env, sid="scan-missing") + "/sections"), 404, "scan")
    run_job(env, post(b + "/process/track", {"quality": "preview"}), "process_track")
    return env


@pytest.fixture()
def kept(tracked):
    """The kept planes on the current mesh, no frame, no sections; cleared afterwards."""
    sd = scan_dir(tracked)
    doc = kf.empty()
    v = arrays(tracked)[0]
    doc["features"] = [record(fid, name, shape, v, kind) for fid, (name, shape, kind) in shapes(tracked).items()]
    kf.save(sd, doc)
    yield tracked
    kf.save(sd, kf.empty())


@pytest.fixture()
def framed(kept):
    """As ``kept``, with the part's frame built from Top, Side and a point in the middle."""
    sd = scan_dir(kept)
    doc = kf.load(sd)
    by = {f["id"]: f for f in doc["features"]}
    frame = part_frame({"id": "f-top", "name": "Top", "fit": by["f-top"]["shape"]},
                       {"id": "f-side", "name": "Side", "fit": by["f-side"]["shape"]},
                       {"kind": "point", "point": [float(x) for x in centre(kept)]})
    doc["frame"] = frame.to_dict()
    kf.save(sd, doc)
    return kept


# --------------------------------------------------------------------------- the cut
def test_each_way_of_naming_the_plane_gives_the_engines_cut(kept):
    b = base_url(kept)
    c = centre(kept)
    fit = kept_fit(kept, "f-cut")
    p1, p2, p3 = (c + [0.0, 0.0, 0.0]).tolist(), (c + [0.0, 10.0, 1.0]).tolist(), (c + [0.5, 0.0, 12.0]).tolist()
    cases = [
        ({"kind": "fit", "fit": "f-cut"}, sec.plane_from_fit({"name": "Upright", "fit": fit["shape"]}, 0.0)),
        ({"kind": "fit", "fit": "f-cut", "offset_mm": -3.5}, sec.plane_from_fit({"name": "Upright", "fit": fit["shape"]}, -3.5)),
        ({"kind": "point_normal", "point": c.tolist(), "normal": [0.3, 1.0, 0.2]},
         sec.plane_from_point_normal(c.tolist(), [0.3, 1.0, 0.2])),
        ({"kind": "points", "points": [p1, p2, p3]}, sec.plane_from_points(p1, p2, p3)),
    ]
    for body, plane in cases:
        r = post(b + "/section", body)
        assert r.status_code == 200, r.text
        a = r.json()
        v, s = engine_cut(kept, plane)
        assert s.lines, body  # the plane crosses the scan: the test means something
        same_cut(a, s)
        assert a["mesh_version"] == v and a["stale_fit"] is False and a["warnings"] == [] and a["kept"] is None
        assert a["compact"] is False and a["truncated"] is None
        assert a["definition"]["kind"] == body["kind"]
    # the definition as the server understood it
    a = post(b + "/section", {"kind": "fit", "fit": "f-cut", "offset_mm": 2}).json()
    assert a["definition"] == {"kind": "fit", "fit": "f-cut", "fit_name": "Upright", "offset_mm": 2.0,
                               "fit_mesh_version": a["mesh_version"]}
    assert "Upright" in a["plane"]["description"]


def test_with_the_parts_frame(framed):
    b = base_url(framed)
    pf = the_frame(framed)
    for axis, value in (("Z", 0.0), ("X", -4.0), ("-Y", 2.5), ("y", 1.0)):
        r = post(b + "/section", {"kind": "frame", "axis": axis, "value_mm": value})
        assert r.status_code == 200, r.text
        _, s = engine_cut(framed, sec.plane_from_frame(pf, axis, value))
        same_cut(r.json(), s)
        assert r.json()["plane"]["in_part_frame"] is True
        assert r.json()["definition"] == {"kind": "frame", "axis": axis.strip().upper(), "value_mm": value}
    # the other ways are drawn in the part's axes too
    c = centre(framed)
    r = post(b + "/section", {"kind": "point_normal", "point": c.tolist(), "normal": [1, 0.2, 0]})
    _, s = engine_cut(framed, sec.plane_from_point_normal(c.tolist(), [1, 0.2, 0], frame=pf))
    same_cut(r.json(), s)
    fit = kept_fit(framed, "f-cut")
    r = post(b + "/section", {"kind": "fit", "fit": "f-cut", "offset_mm": 1})
    _, s = engine_cut(framed, sec.plane_from_fit({"name": "Upright", "fit": fit["shape"]}, 1.0, frame=pf))
    same_cut(r.json(), s)


def test_the_compact_answer_and_its_budget(kept):
    b = base_url(kept)
    body = {"kind": "fit", "fit": "f-cut"}
    full = post(b + "/section", body).json()
    r = post(b + "/section", {**body, "compact": True})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["compact"] is True and a["truncated"] is None
    for key in ("plane", "length_mm", "n_points", "bbox", "n_lines", "n_closed", "mesh_version", "definition"):
        assert a[key] == full[key], key
    for got, want in zip(a["lines"], full["lines"]):
        assert set(got) == {"closed", "length_mm", "area_mm2", "bbox", "n_points", "points", "sigma_mm", "stretches"}
        assert got["closed"] == want["closed"] and got["stretches"] == want["stretches"]
        assert got["length_mm"] == pytest.approx(want["length_mm"], abs=1e-4)  # each line's numbers rounded too
        assert (got["area_mm2"] is None) == (want["area_mm2"] is None)
        if want["area_mm2"] is not None:
            assert got["area_mm2"] == pytest.approx(want["area_mm2"], abs=1e-4)
        assert np.abs(np.asarray(got["bbox"]) - want["bbox"]).max() <= 0.0005 + 1e-12
        P = np.asarray(want["points"])
        assert got["n_points"] == len(P) and len(got["points"]) == 2 * len(P)
        assert np.abs(np.asarray(got["points"]).reshape(-1, 2) - P).max() <= 0.0005 + 1e-12  # to the micrometre
        if want["sigma"] is None:
            assert got["sigma_mm"] is None
        else:  # over the measured points (a filled vertex's sigma is the "not measured" mark)
            S = np.asarray(want["sigma"])
            S = S[S < SIGMA_NONE_MM]
            assert got["sigma_mm"] == ({"mean": pytest.approx(S.mean(), abs=1e-4), "max": pytest.approx(S.max(), abs=1e-4)}
                                       if len(S) else {"mean": None, "max": None})
    # a budget: the longest lines whole, the totals still of the whole cut
    n0 = len(full["lines"][0]["points"])
    r = post(b + "/section", {**body, "compact": True, "max_points": n0})
    a = r.json()
    assert r.status_code == 200 and len(a["lines"]) >= 1 and sum(ln["n_points"] for ln in a["lines"]) <= max(n0, 1)
    assert a["n_points"] == full["n_points"] and a["n_lines"] == full["n_lines"]
    if full["n_lines"] > len(a["lines"]):
        assert a["truncated"] == {"lines_shown": len(a["lines"]), "points_shown": sum(ln["n_points"] for ln in a["lines"]),
                                  "lines": full["n_lines"], "points": full["n_points"]}
    err(post(b + "/section", {**body, "compact": True, "max_points": 0}), 422)


def test_a_cut_that_misses_the_scan_is_empty(kept):
    c = centre(kept)
    r = post(base_url(kept) + "/section", {"kind": "point_normal", "point": (c + [0, 0, 5000]).tolist(), "normal": [0, 0, 1]})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["lines"] == [] and a["n_points"] == 0 and a["bbox"] is None and a["length_mm"] == 0.0


def test_a_fit_made_on_an_earlier_mesh_gives_its_plane_and_says_so(kept):
    sd = scan_dir(kept)
    doc = kf.load(sd)
    v = arrays(kept)[0]
    doc["features"][0]["mesh_version"] = v - 1
    kf.save(sd, doc)
    r = post(base_url(kept) + "/section", {"kind": "fit", "fit": "f-cut", "offset_mm": 1.0})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["stale_fit"] is True and a["definition"]["fit_mesh_version"] == v - 1
    assert len(a["warnings"]) == 1 and "Upright" in a["warnings"][0] and "earlier mesh" in a["warnings"][0]
    _, s = engine_cut(kept, sec.plane_from_fit({"name": "Upright", "fit": doc["features"][0]["shape"]}, 1.0))
    same_cut(a, s)


def test_bad_bodies(kept):
    b = base_url(kept)
    c = centre(kept).tolist()
    r = err(post(b + "/section", {"kind": "points", "points": [c, [c[0] + 1, c[1], c[2]], [c[0] + 2, c[1], c[2]]]}), 422)
    assert "line" in r["detail"]
    err(post(b + "/section", {"kind": "point_normal", "point": c, "normal": [0, 0, 0]}), 422, "zero")
    err(post(b + "/section", {"kind": "frame", "axis": "Z", "value_mm": 3}), 422, "part's axes")
    err(post(b + "/section", {"kind": "fit", "fit": "f-pin"}), 422, "plane")
    err(post(b + "/section", {"kind": "fit"}), 422, "needs the plane")
    err(post(b + "/section", {"kind": "point_normal", "point": c}), 422, "direction is missing")
    err(post(b + "/section", {"kind": "points", "points": [c, c]}), 422)
    err(post(b + "/section", {"kind": "points", "points": [c, c, [1, 2]]}), 422)
    err(post(b + "/section", {"kind": "sideways"}), 422, "way of choosing the plane")
    err(post(b + "/section", {"kind": "fit", "fit": "f-cut", "offset_mm": "far"}), 422)
    err(post(b + "/section", {}), 422)
    err(post(b + "/section", {"kind": "fit", "fit": "f-nope"}), 404, "no longer in the scan")
    # the downloads take the same plane as a query
    err(requests.get(b + "/section/section.dxf", params={"kind": "point_normal", "point": "1,2", "normal": "0,0,1"},
                     timeout=T), 422)
    err(requests.get(b + "/section/section.dxf", params={"kind": "frame", "axis": "Z", "value_mm": "1"}, timeout=T), 422, "part's axes")
    err(requests.get(b + "/section/section.pdf", params={"kind": "fit", "fit": "f-cut"}, timeout=T), 404)


def test_frame_axis_refusals_with_a_frame(framed):
    b = base_url(framed)
    err(post(b + "/section", {"kind": "frame", "axis": "W", "value_mm": 3}), 422, "axis")
    err(post(b + "/section", {"kind": "frame", "axis": "Z"}), 422, "where along the axis")


# --------------------------------------------------------------------------- the files
def test_the_downloads_are_the_writers_text_of_the_same_cut(framed):
    b = base_url(framed)
    before = on_disk(framed)
    pf = the_frame(framed)
    fit = kept_fit(framed, "f-cut")
    c = centre(framed)
    queries = [
        ({"kind": "fit", "fit": "f-cut", "offset_mm": "1.5"}, sec.plane_from_fit({"name": "Upright", "fit": fit["shape"]}, 1.5, frame=pf)),
        ({"kind": "frame", "axis": "-Y", "value_mm": "2"}, sec.plane_from_frame(pf, "-Y", 2.0)),
        ({"kind": "point_normal", "point": ",".join(map(repr, c.tolist())), "normal": "0,1,0"},
         sec.plane_from_point_normal(c.tolist(), [0, 1, 0], frame=pf)),
        ({"kind": "points", "points": ",".join(map(repr, [*c.tolist(), *(c + [0, 1, 0]).tolist(), *(c + [1, 0, 0.5]).tolist()]))},
         sec.plane_from_points(c, c + [0, 1, 0], c + [1, 0, 0.5], frame=pf)),
    ]
    for q, plane in queries:
        v, s = engine_cut(framed, plane)
        dxf = requests.get(b + "/section/section.dxf", params=q, timeout=T)
        assert dxf.status_code == 200, dxf.text
        assert dxf.content.decode("ascii") == dxf_text(s)
        assert int(dxf.headers["x-ohs-mesh-version"]) == v
        cd = dxf.headers["content-disposition"]
        assert "attachment" in cd and "Sphere" in cd and "section" in cd and cd.rstrip('"').endswith(".dxf")
        svg = requests.get(b + "/section/section.svg", params=q, timeout=T)
        assert svg.status_code == 200, svg.text
        assert svg.content.decode("utf-8") == svg_text(s)
        assert svg.headers["content-type"].startswith("image/svg+xml")
        assert svg.headers["content-disposition"].rstrip('"').endswith(".svg")
    # the fit's name and offset name the file; another axis another name
    names = {requests.get(b + "/section/section.dxf", params=q, timeout=T).headers["content-disposition"] for q, _ in queries}
    assert len(names) >= 3
    assert on_disk(framed) == before  # nothing written


# --------------------------------------------------------------------------- kept sections
def test_keep_list_rename_and_delete(kept):
    b = base_url(kept)
    sd = scan_dir(kept)
    fit = kept_fit(kept, "f-cut")
    features_before = get(b + "/features").json()
    r = post(b + "/sections", {"kind": "fit", "fit": "f-cut", "offset_mm": 2.0, "name": "  Across the ball "})
    assert r.status_code == 200, r.text
    s = r.json()
    v = arrays(kept)[0]
    plane = sec.plane_from_fit({"name": "Upright", "fit": fit["shape"]}, 2.0)
    assert s["id"].startswith("s-") and s["name"] == "Across the ball"
    assert s["plane"] == plain(plane.to_dict())
    assert s["definition"] == {"kind": "fit", "fit": "f-cut", "fit_name": "Upright", "offset_mm": 2.0, "fit_mesh_version": v}
    assert s["mesh_version"] == v and s["mesh_changed"] is False and s["created"]
    stored = kf.load(sd)["sections"]
    assert [x["id"] for x in stored] == [s["id"]] and "mesh_changed" not in stored[0]
    assert {k: v2 for k, v2 in s.items() if k != "mesh_changed"} == stored[0]
    # a default name, and every way kept
    c = centre(kept).tolist()
    s2 = post(b + "/sections", {"kind": "point_normal", "point": c, "normal": [0, 1, 0]}).json()
    assert s2["name"] == "Section 2" and s2["definition"] == {"kind": "point_normal", "point": c, "normal": [0.0, 1.0, 0.0]}
    listed = get(b + "/sections").json()
    assert listed["mesh_version"] == v and [x["id"] for x in listed["sections"]] == [s["id"], s2["id"]]
    assert all(x["mesh_changed"] is False for x in listed["sections"])
    # GET .../features answers as before (no sections in it)
    assert get(b + "/features").json() == features_before
    # opened: cut again with the stored plane
    r = get(f"{b}/sections/{s['id']}")
    assert r.status_code == 200, r.text
    a = r.json()
    _, cut = engine_cut(kept, plane)
    same_cut(a, cut)
    assert a["kept"] == {**s, "mesh_changed": False} and a["warnings"] == [] and a["definition"] == s["definition"]
    a = requests.get(f"{b}/sections/{s['id']}", params={"compact": "true"}, timeout=T).json()
    assert a["compact"] is True and a["n_points"] == cut.n_points
    # its files
    dxf = get(f"{b}/sections/{s['id']}/section.dxf")
    assert dxf.status_code == 200 and dxf.content.decode("ascii") == dxf_text(cut)
    assert "Across_the_ball" in dxf.headers["content-disposition"]
    svg = get(f"{b}/sections/{s['id']}/section.svg")
    assert svg.content.decode("utf-8") == svg_text(cut)
    err(get(f"{b}/sections/{s['id']}/section.png"), 404)
    # rename
    r = patch(f"{b}/sections/{s['id']}", {"name": "Ball, upright"})
    assert r.status_code == 200 and r.json()["name"] == "Ball, upright"
    assert kf.load(sd)["sections"][0]["name"] == "Ball, upright"
    err(patch(f"{b}/sections/{s['id']}", {"name": "   "}), 422)
    err(patch(f"{b}/sections/{s['id']}", {}), 422)
    err(patch(f"{b}/sections/s-nope", {"name": "x"}), 404)
    # deleting the fit it was made from leaves the section (its plane is kept as numbers)
    assert delete(f"{b}/features/f-cut").status_code == 204
    assert get(f"{b}/sections/{s['id']}").json()["plane"] == s["plane"]
    # delete
    assert delete(f"{b}/sections/{s['id']}").status_code == 204
    assert [x["id"] for x in get(b + "/sections").json()["sections"]] == [s2["id"]]
    err(delete(f"{b}/sections/{s['id']}"), 404)
    err(get(f"{b}/sections/{s['id']}"), 404)
    err(get(f"{b}/sections/{s['id']}/section.dxf"), 404)
    assert delete(f"{b}/sections/{s2['id']}").status_code == 204
    assert "sections" not in json.loads((sd / kf.FILE_NAME).read_text(encoding="utf-8"))  # as before any was kept
    # bad bodies keep nothing
    err(post(b + "/sections", {"kind": "frame", "axis": "Z", "value_mm": 1}), 422, "part's axes")
    err(post(b + "/sections", {"kind": "point_normal", "point": c, "normal": [0, 1, 0], "name": ""}), 422)
    assert get(b + "/sections").json()["sections"] == []


def test_keep_from_the_frame_and_from_three_points(framed):
    b = base_url(framed)
    pf = the_frame(framed)
    s = post(b + "/sections", {"kind": "frame", "axis": "x", "value_mm": 1.5}).json()
    assert s["plane"] == plain(sec.plane_from_frame(pf, "X", 1.5).to_dict())
    assert s["definition"] == {"kind": "frame", "axis": "X", "value_mm": 1.5}
    c = centre(framed)
    pts = [c.tolist(), (c + [0, 2, 0]).tolist(), (c + [3, 0, 1]).tolist()]
    t = post(b + "/sections", {"kind": "points", "points": pts}).json()
    assert t["plane"] == plain(sec.plane_from_points(*pts, frame=pf).to_dict())
    assert t["definition"] == {"kind": "points", "points": pts}
    # removing the frame changes nothing of a kept section: its plane and axes are numbers
    assert delete(b + "/features/frame").status_code == 204
    a = get(f"{b}/sections/{s['id']}").json()
    assert a["plane"] == s["plane"]
    plane = sec.SectionPlane(point=np.asarray(s["plane"]["point"]), normal=np.asarray(s["plane"]["normal"]),
                             u=np.asarray(s["plane"]["u"]), v=np.asarray(s["plane"]["v"]),
                             description=s["plane"]["description"], in_part_frame=True)
    _, cut = engine_cut(framed, plane)
    same_cut(a, cut)


def test_a_kept_section_is_cut_again_after_an_edit_and_says_the_mesh_changed(kept):
    b = base_url(kept)
    fit = kept_fit(kept, "f-cut")
    plane = sec.plane_from_fit({"name": "Upright", "fit": fit["shape"]}, 0.0)
    s = post(b + "/sections", {"kind": "fit", "fit": "f-cut"}).json()
    v0, before = engine_cut(kept, plane)
    assert before.lines
    # cut away the faces next to the plane on one side of the scan's middle
    _, V, F, _, _ = arrays(kept)
    C = np.asarray(V, dtype=np.float64)[F].mean(axis=1)
    near = np.abs(plane.distance(C)) < 4.0
    cutaway = np.flatnonzero(near & (C[:, 1] < np.median(C[near, 1])))
    assert len(cutaway)
    run_job(kept, post(b + "/process/edits", {"op": "delete_faces", "params": {"faces": faces_b64(cutaway)},
                                              "version": v0}), "mesh_edit")
    v1, after = engine_cut(kept, plane)
    assert v1 > v0 and after.n_points != before.n_points
    a = get(f"{b}/sections/{s['id']}").json()
    same_cut(a, after)
    assert a["mesh_version"] == v1 and a["kept"]["mesh_changed"] is True and a["kept"]["mesh_version"] == v0
    assert len(a["warnings"]) == 1 and "changed" in a["warnings"][0]
    assert get(b + "/sections").json()["sections"][0]["mesh_changed"] is True
    # nothing is written by reading; "recut" keeps it as cut on this mesh
    assert kf.load(scan_dir(kept))["sections"][0]["mesh_version"] == v0
    r = patch(f"{b}/sections/{s['id']}", {"recut": True})
    assert r.status_code == 200, r.text
    assert r.json()["mesh_version"] == v1 and r.json()["mesh_changed"] is False and r.json()["plane"] == s["plane"]
    assert get(f"{b}/sections/{s['id']}").json()["warnings"] == []


def test_a_file_without_sections_loads_and_ids_stay_unique(tracked, tmp_path):
    sd = scan_dir(tracked)
    kf.save(sd, kf.empty())
    text = (sd / kf.FILE_NAME).read_text(encoding="utf-8")
    assert "sections" not in text
    assert get(base_url(tracked) + "/sections").json()["sections"] == []
    assert (sd / kf.FILE_NAME).read_text(encoding="utf-8") == text  # nothing written by reading
    # the file's checks
    good = {"id": "s-1", "name": "A", "plane": {"point": [0, 0, 0], "normal": [0, 0, 1], "u": [1, 0, 0], "v": [0, 1, 0],
                                                  "description": "d", "in_part_frame": False},
            "definition": {"kind": "point_normal", "point": [0, 0, 0], "normal": [0, 0, 1]}, "mesh_version": 1,
            "created": "2026-11-17T12:00:00"}
    doc = {**kf.empty(), "sections": [good]}
    assert kf.validate(doc)["sections"] == [good]
    for bad in ({**good, "id": "a b"}, {**good, "name": ""}, {**good, "plane": {**good["plane"], "normal": [0, 0, 0]}},
                {**good, "plane": {**good["plane"], "u": [1, 2]}}, {**good, "definition": {"kind": "cone"}},
                {**good, "mesh_version": -1}, {**good, "plane": "flat"}):
        with pytest.raises(ValueError):
            kf.validate({**kf.empty(), "sections": [bad]})
    with pytest.raises(ValueError):
        kf.validate({**kf.empty(), "sections": {"s-1": good}})
    m = {"id": "s-1", "name": "D", "a": [0, 0, 0], "b": [1, 0, 0], "mesh_version": 1, "created": ""}
    with pytest.raises(ValueError, match="twice"):
        kf.validate({**kf.empty(), "measurements": [m], "sections": [good]})
    kf.save(sd, kf.empty())
