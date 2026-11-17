"""Kept features over the API (APP_API.md "Kept features"): fits, measurements and relations saved
with the scan in ``features.json``, renamed and deleted; marked when the mesh changes, and fitted
again from their saved clicks only when asked.

Uses the 10-frame sphere scan of ``test_server_process_studio.py`` (its ``env`` fixture, so the
recording comes from the test cache), tracked once at preview quality.
"""
from __future__ import annotations

import base64

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

import ohs.measure.features as kf  # noqa: E402
from ohs.track.export import load_ply  # noqa: E402
from test_server_process_studio import T, base_url, env, err, faces_b64, get, post, run_job  # noqa: E402,F401


def patch(url: str, body: dict | None):
    return requests.patch(url, json=body, timeout=T)


def delete(url: str):
    return requests.delete(url, timeout=T)


def scan_dir(e: dict):
    return e["root"] / "projects" / e["pid"] / "scans" / e["sid"]


def current_mesh(e: dict, tmp_path) -> tuple[dict, np.ndarray, np.ndarray]:
    cur = get(base_url(e) + "/process").json()["current"]
    ply = tmp_path / f"mesh-{cur['version']}.ply"
    ply.write_bytes(get(e["url"] + cur["mesh_url"]).content)
    m = load_ply(ply)
    return cur, m["vertices"], m["faces"]


def table_click(e: dict, tmp_path) -> dict:
    """A click in the middle of the table behind the sphere (as test_fit_a_plane_on_the_table)."""
    cur, V, F = current_mesh(e, tmp_path)
    C = V[F].mean(axis=1)
    far = np.flatnonzero(C[:, 2] >= np.percentile(C[:, 2], 90))
    face = int(far[np.argmin(np.linalg.norm(C[far] - C[far].mean(axis=0), axis=1))])
    return {"face": face, "point": [float(x) for x in C[face]], "version": cur["version"]}


def decoded(b64: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(b64), dtype="<u4")


@pytest.fixture(scope="module")
def tracked(env):  # noqa: F811 (the fixture imported above)
    """The sphere scan, tracked at preview quality; checks the answers before there is a mesh first."""
    b = base_url(env)
    s = get(b + "/features")
    assert s.status_code == 200, s.text
    assert s.json() == {"format_version": 1, "frame": None, "mesh_version": None, "features": [],
                        "measurements": [], "relations": []}
    err(post(b + "/features", {"kind": "plane", "face": 0, "point": [0, 0, 250]}), 409, "no mesh")
    err(get(base_url(env, sid="scan-missing") + "/features"), 404, "no scan")
    run_job(env, post(b + "/process/track", {"quality": "preview"}), "process_track")
    return env


def test_a_scan_without_features_answers_an_empty_list(tracked):
    s = get(base_url(tracked) + "/features").json()
    v = get(base_url(tracked) + "/process").json()["current"]["version"]
    assert s["features"] == [] and s["measurements"] == [] and s["relations"] == []
    assert s["mesh_version"] == v and s["frame"] is None and s["format_version"] == 1
    assert not (scan_dir(tracked) / kf.FILE_NAME).exists()  # nothing written by reading


def test_keep_reload_rename_and_delete(tracked, tmp_path):
    b = base_url(tracked)
    click = table_click(tracked, tmp_path)
    r = post(b + "/features", {"kind": "plane", **click})
    assert r.status_code == 200, r.text
    f1 = r.json()
    assert f1["id"].startswith("f-") and f1["name"] == "Fit 1" and f1["kind"] == "plane" and f1["stale"] is False
    assert f1["mesh_version"] == click["version"] and f1["created"]
    # where the region lies (K2): a plane's rectangle and area, the outline itself in its own file
    ext = f1["extent"]
    assert ext["kind"] == "plane" and ext["area_mm2"] > 0 and min(ext["rectangle"]["half_sizes_mm"]) > 0
    assert "outer" not in ext and ext["n_outer"] >= 1
    assert f1["seed"] == {"kind": "plane", "face": click["face"], "point": click["point"], "tolerance_mm": None}
    assert f1["shape"]["kind"] == "plane" and f1["shape"]["normal_sigma_deg"] > 0 and f1["shape"]["offset_sigma_mm"] > 0
    faces = decoded(f1["faces"])
    assert len(faces) == f1["n_faces"] > 100 and click["face"] in faces
    # the same numbers as the fit route gives for that click
    fit = post(b + "/fit", {"kind": "plane", **click}).json()
    assert fit["shape"] == f1["shape"] and fit["faces"] == f1["faces"] and fit["warnings"] == f1["warnings"]
    assert fit["tolerance_mm"] == f1["tolerance_mm"] and fit["mean_sigma_mm"] == f1["mean_sigma_mm"]
    # kept in the scan's folder: the record in features.json, the region beside it
    doc = kf.load(scan_dir(tracked))
    assert [f["id"] for f in doc["features"]] == [f1["id"]] and "faces" not in doc["features"][0]
    assert np.array_equal(kf.load_faces(scan_dir(tracked), f1["id"]), faces)
    fr = get(f"{b}/features/{f1['id']}/faces").json()
    assert fr == {"id": f1["id"], "mesh_version": click["version"], "n_faces": len(faces), "faces": f1["faces"]}
    err(get(f"{b}/features/f-nope/faces"), 404, "f-nope")

    f2 = post(b + "/features", {"kind": "plane", **click, "name": "  Table  "}).json()
    assert f2["name"] == "Table" and f2["id"] != f1["id"]
    m1 = post(b + "/features/measurements", {"a": [0, 0, 0], "b": [3, 4, 0], "version": click["version"]})
    assert m1.status_code == 200, m1.text
    m1 = m1.json()
    assert m1["id"].startswith("m-") and m1["name"] == "Distance 1" and m1["a"] == [0, 0, 0] and m1["b"] == [3, 4, 0]
    assert m1["mesh_version"] == click["version"] and m1["stale"] is False
    rel = post(b + "/features/relations", {"a": f1["id"], "b": f2["id"]})
    assert rel.status_code == 200, rel.text
    rel = rel.json()
    assert rel["id"].startswith("r-") and rel["a"] == f1["id"] and rel["b"] == f2["id"] and rel["name"] == "Relation 1"

    # reload: the same set, in order (the viewer opened again)
    s = get(b + "/features").json()
    assert [f["id"] for f in s["features"]] == [f1["id"], f2["id"]]
    assert [f["name"] for f in s["features"]] == ["Fit 1", "Table"]
    assert all("faces" not in f for f in s["features"]) and s["features"][0]["shape"] == f1["shape"]
    assert [m["id"] for m in s["measurements"]] == [m1["id"]] and [x["id"] for x in s["relations"]] == [rel["id"]]

    # rename: a fit, a measurement, a relation
    r = patch(f"{b}/features/{f1['id']}", {"name": "Table top"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Table top" and r.json()["shape"] == f1["shape"] and r.json()["stale"] is False
    assert patch(f"{b}/features/{m1['id']}", {"name": "Width"}).json()["name"] == "Width"
    assert patch(f"{b}/features/{rel['id']}", {"name": "Twins"}).json()["name"] == "Twins"
    s = get(b + "/features").json()
    assert s["features"][0]["name"] == "Table top" and s["measurements"][0]["name"] == "Width"
    assert s["relations"][0]["name"] == "Twins"
    assert kf.load(scan_dir(tracked))["features"][0]["name"] == "Table top"
    err(patch(f"{b}/features/{f1['id']}", {"name": "   "}), 422, "name")
    err(patch(f"{b}/features/f-nope", {"name": "x"}), 404, "f-nope")
    # a relation needs two kept fits
    err(post(b + "/features/relations", {"a": f1["id"], "b": "f-nope"}), 422, "f-nope")
    err(post(b + "/features/relations", {"a": f1["id"], "b": f1["id"]}), 422, "itself")
    # the next default name counts on from the highest
    f3 = post(b + "/features", {"kind": "plane", **click}).json()
    assert f3["name"] == "Fit 3"

    # delete: a fit takes its relations and its region file with it
    assert delete(f"{b}/features/{f2['id']}").status_code == 204
    s = get(b + "/features").json()
    assert [f["id"] for f in s["features"]] == [f1["id"], f3["id"]] and s["relations"] == []
    assert kf.load_faces(scan_dir(tracked), f2["id"]) is None
    assert delete(f"{b}/features/{m1['id']}").status_code == 204
    assert get(b + "/features").json()["measurements"] == []
    err(delete(f"{b}/features/{f2['id']}"), 404, f2["id"])
    for f in (f1, f3):
        assert delete(f"{b}/features/{f['id']}").status_code == 204
    s = get(b + "/features").json()
    assert s["features"] == [] and s["measurements"] == [] and s["relations"] == []


def test_bad_bodies_answer_422(tracked, tmp_path):
    b = base_url(tracked)
    click = table_click(tracked, tmp_path)
    err(post(b + "/features", {"kind": "cone", **click}), 422, "unknown kind")
    err(post(b + "/features", {**click, "kind": "plane", "face": -1}), 422)
    err(post(b + "/features", {**click, "kind": "plane", "point": [0, 0]}), 422)
    err(post(b + "/features", {"kind": "plane"}), 422)
    err(post(b + "/features", {"kind": "plane", **click, "name": "x" * 81}), 422)
    err(post(b + "/features", {"kind": "plane", **click, "name": "  "}), 422, "name")
    err(post(b + "/features", {"kind": "plane", **click, "tolerance_mm": -1}), 422)
    err(post(b + "/features", {**click, "kind": "plane", "version": click["version"] - 1}), 409, "changed")
    err(post(b + "/features/measurements", {"a": [0, 0], "b": [1, 1, 1]}), 422)
    err(post(b + "/features/measurements", {"a": [0, 0, 0]}), 422)
    err(post(b + "/features/relations", {"a": "f-1"}), 422)
    err(patch(f"{b}/features/f-1", {}), 422)
    err(patch(f"{b}/features/f-1", {"name": 5}), 422)
    err(post(b + "/features/refit", {"ids": "f-1"}), 422)
    assert get(b + "/features").json()["features"] == []  # nothing was kept


def test_an_edit_marks_a_fit_and_fit_again_brings_it_back(tracked, tmp_path):
    b = base_url(tracked)
    click = table_click(tracked, tmp_path)
    f = post(b + "/features", {"kind": "plane", **click}).json()
    v0 = click["version"]
    assert get(b + "/features").json()["features"][0]["stale"] is False
    # a fit kept on this mesh that cannot be found again: a hole where the table has none
    sd = scan_dir(tracked)
    doc = kf.load(sd)
    hole = dict(doc["features"][0], id="f-hole", name="Not a hole", kind="hole",
                shape={"kind": "circle", "center": click["point"], "normal": [0.0, 0.0, 1.0], "radius_mm": 5.0,
                       "diameter_mm": 10.0, "rms_mm": 0.1, "n": 40, "radius_sigma_mm": 0.01,
                       "diameter_sigma_mm": 0.02, "center_sigma_mm": 0.01},
                seed={"kind": "hole", "face": click["face"], "point": click["point"], "tolerance_mm": None},
                tolerance_mm=None, correlated_points=None)
    doc["features"].append(hole)
    kf.save(sd, doc)
    # an edit that leaves the fit's region alone: cut away the nearest 5 % of the rest (on a mesh
    # without a GPU the region is most of the tilted table, so "the front of the sphere" would cut it)
    cur, V, F = current_mesh(tracked, tmp_path)
    C = V[F].mean(axis=1)
    rest = np.setdiff1d(np.arange(len(F)), decoded(f["faces"]))
    cut = rest[C[rest, 2] <= np.percentile(C[rest, 2], 5)]
    run_job(tracked, post(b + "/process/edits", {"op": "delete_faces", "params": {"faces": faces_b64(cut)},
                                                 "version": cur["version"]}), "mesh_edit")
    v1 = get(b + "/process").json()["current"]["version"]
    assert v1 > v0
    # marked, not refitted: reading twice changes nothing
    for _ in range(2):
        s = get(b + "/features").json()
        assert s["mesh_version"] == v1
        assert [(x["id"], x["stale"], x["mesh_version"]) for x in s["features"]] == [(f["id"], True, v0),
                                                                                     ("f-hole", True, v0)]
    assert s["features"][0]["shape"] == f["shape"]
    # its region file still answers, for the version it was fitted on
    assert get(f"{b}/features/{f['id']}/faces").json()["mesh_version"] == v0
    # fit again: the plane comes back, the hole cannot and stays marked
    r = post(b + "/features/refit", {})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["refitted"] == [f["id"]]
    assert [x["id"] for x in out["failed"]] == ["f-hole"] and out["failed"][0]["detail"] and out["failed"][0]["hint"]
    s = out["features"]
    assert s == get(b + "/features").json()
    g, h = s["features"]
    assert g["id"] == f["id"] and g["name"] == f["name"] and g["created"] == f["created"]
    assert g["stale"] is False and g["mesh_version"] == v1 and h["stale"] is True and h["mesh_version"] == v0
    assert g["seed"]["point"] == f["seed"]["point"] and g["seed"]["kind"] == "plane"
    # within its ± of before: the normal's direction and the plane's offset
    old, new = f["shape"], g["shape"]
    n0, n1 = np.array(old["normal"]), np.array(new["normal"])
    angle = np.degrees(np.arccos(min(1.0, abs(float(n0 @ n1)))))
    assert angle <= old["normal_sigma_deg"] + 1e-9
    assert abs(float((np.array(new["point"]) - np.array(old["point"])) @ n0)) <= old["offset_sigma_mm"] + 1e-9
    # the region file is now the new mesh's
    fr = get(f"{b}/features/{f['id']}/faces").json()
    assert fr["mesh_version"] == v1 and len(decoded(fr["faces"])) == g["n_faces"]
    _, V1, F1 = current_mesh(tracked, tmp_path)
    assert decoded(fr["faces"]).max() < len(F1)
    # nothing left to fit again: an empty answer
    again = post(b + "/features/refit", {"ids": [f["id"]]}).json()
    assert again["refitted"] == [] and again["failed"] == []
    err(post(b + "/features/refit", {"ids": ["f-nope"]}), 404, "f-nope")


def test_fit_again_refuses_when_the_surface_under_the_click_is_gone(tracked, tmp_path):
    """A fit whose whole region was cut away is not fitted again on whatever surface is nearest."""
    b = base_url(tracked)
    click = table_click(tracked, tmp_path)
    f = post(b + "/features", {"kind": "plane", **click, "name": "Table"}).json()
    run_job(tracked, post(b + "/process/edits", {"op": "delete_faces", "params": {"faces": f["faces"]},
                                                 "version": click["version"]}), "mesh_edit")
    out = post(b + "/features/refit", {"ids": [f["id"]]}).json()
    assert out["refitted"] == []
    assert [x["id"] for x in out["failed"]] == [f["id"]]
    assert "gone" in out["failed"][0]["detail"] and out["failed"][0]["hint"]
    kept = next(x for x in out["features"]["features"] if x["id"] == f["id"])
    assert kept["stale"] is True and kept["shape"] == f["shape"] and kept["mesh_version"] == click["version"]
