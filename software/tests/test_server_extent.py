"""Where a kept fit's region lies, and the relations between kept fits, from the engine
(FEATURE_PLAN.md K2, the server part; APP_API.md "Kept features").

* A kept fit carries its extent (``ohs.measure.region_extent``): in ``features.json`` only the
  small part (a plane's rectangle, area and loop counts; a cylinder's span and arc; a hole's edge
  when it is short), a plane's outline loops in their own file beside the region file, served by
  ``GET .../features/{id}/outline`` for the mesh version they belong to.
* ``POST .../fit`` answers exactly as before (no extent).
* A kept relation carries ``ohs.measure.relate``'s record for the two kept shapes, and says when
  one of them was fitted on an earlier mesh; ``GET .../features/relate?a=&b=`` answers the same
  for two kept fits that are not kept as a relation.

Uses the 10-frame sphere scan of ``test_server_process_studio.py`` (its ``env`` fixture), tracked
once at preview quality, and its table for a real plane region. Shapes that a ball on a table
cannot give are written straight into ``features.json``, as ``test_server_frame.py`` does.
"""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

import ohs.measure.features as kf  # noqa: E402
from ohs.measure import region_extent, relate  # noqa: E402
from test_server_process_studio import T, base_url, env, err, faces_b64, get, post, run_job  # noqa: E402,F401

FEATURE_JSON_MAX = 20_000  # bytes of one kept plane in features.json: the outline must not be in it
FIT_KEYS = {"kind", "shape", "faces", "n_faces", "tolerance_mm", "mean_sigma_mm", "correlated_points", "warnings",
            "version"}


def delete(url: str):
    return requests.delete(url, timeout=T)


def scan_dir(e: dict):
    return e["root"] / "projects" / e["pid"] / "scans" / e["sid"]


def arrays(e: dict):
    """The current mesh as the fit route sees it: ``(version, V, F)``."""
    v, V, F, _, _ = e["studio"].processing.mesh_arrays(e["pid"], e["sid"])
    return v, V, F


def table_click(e: dict) -> dict:
    """A click in the middle of the table behind the sphere (as test_server_features.py)."""
    v, V, F = arrays(e)
    C = V[F].mean(axis=1)
    far = np.flatnonzero(C[:, 2] >= np.percentile(C[:, 2], 90))
    face = int(far[np.argmin(np.linalg.norm(C[far] - C[far].mean(axis=0), axis=1))])
    return {"face": face, "point": [float(x) for x in C[face]], "version": v}


def decoded(b64: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(b64), dtype="<u4")


def engine_extent(e: dict, f: dict, faces: np.ndarray) -> dict:
    """``region_extent`` on the current mesh for a kept plane's shape and region."""
    _, V, F = arrays(e)
    s = f["shape"]
    fit = SimpleNamespace(point=np.asarray(s["point"], dtype=np.float64), normal=np.asarray(s["normal"], dtype=np.float64))
    return region_extent(V, F, "plane", fit, faces)


def outline_path(e: dict, fid: str):
    return kf.outline_path(scan_dir(e), fid)


@pytest.fixture(scope="module")
def tracked(env):  # noqa: F811 (the fixture imported above)
    run_job(env, post(base_url(env) + "/process/track", {"quality": "preview"}), "process_track")
    return env


@pytest.fixture()
def clean(tracked):
    """No kept features before and after the test."""
    kf.save(scan_dir(tracked), kf.empty())
    yield tracked
    for f in kf.load(scan_dir(tracked))["features"]:
        kf.remove_faces(scan_dir(tracked), f["id"])
        kf.remove_outline(scan_dir(tracked), f["id"])
    kf.save(scan_dir(tracked), kf.empty())


# --------------------------------------------------------------------------- the fit route
def test_the_fit_route_answers_as_before(clean):
    r = post(base_url(clean) + "/fit", {"kind": "plane", **table_click(clean)})
    assert r.status_code == 200, r.text
    assert set(r.json()) == FIT_KEYS


# --------------------------------------------------------------------------- extent
def check_plane_extent(ext: dict, want: dict) -> None:
    """The small part kept in features.json against the engine's extent on the same region."""
    assert ext["kind"] == "plane"
    for key in ("origin", "u", "v"):
        assert np.allclose(ext[key], want[key], atol=1e-9)
    rect, rw = ext["rectangle"], want["rectangle"]
    assert np.allclose(rect["center"], rw["center"], atol=1e-6)
    assert np.allclose(rect["axes"], rw["axes"], atol=1e-9)
    assert np.allclose(rect["half_sizes_mm"], rw["half_sizes_mm"], atol=1e-6)
    assert ext["area_mm2"] == pytest.approx(want["area_mm2"], rel=1e-9)
    assert ext["n_outer"] == len(want["outer"]) and ext["n_holes"] == len(want["holes"])
    assert ext["n_points"] == sum(len(x) for x in want["outer"] + want["holes"])
    assert "outer" not in ext and "holes" not in ext


def check_outline(o: dict, want: dict) -> None:
    assert o["kind"] == "plane"
    for key in ("origin", "u", "v"):
        assert np.allclose(o[key], want[key], atol=1e-9)
    for key in ("outer", "holes"):
        assert len(o[key]) == len(want[key])
        for got, w in zip(o[key], want[key]):
            assert np.allclose(np.asarray(got), np.asarray(w), atol=1e-9)


def test_a_kept_plane_has_its_extent_and_its_outline_beside_it(clean, capsys):
    b = base_url(clean)
    click = table_click(clean)
    r = post(b + "/features", {"kind": "plane", **click})
    assert r.status_code == 200, r.text
    f = r.json()
    faces = decoded(f["faces"])
    want = engine_extent(clean, f, faces)
    check_plane_extent(f["extent"], want)
    # the same when read again
    s = get(b + "/features").json()
    assert s["features"][0]["extent"] == f["extent"]
    # features.json stays small: the outline is in its own file
    rec = kf.load(scan_dir(clean))["features"][0]
    size = len(json.dumps(rec, indent=1))
    assert size < FEATURE_JSON_MAX, f"the kept plane takes {size} bytes in features.json"
    p = outline_path(clean, f["id"])
    assert p.is_file() and p.parent == kf.faces_path(scan_dir(clean), f["id"]).parent
    full = len(json.dumps({"outer": want["outer"], "holes": want["holes"]}))
    with capsys.disabled():
        print(f"\n  kept plane on the test scan: {f['n_faces']} faces, {f['extent']['n_points']} outline points in "
              f"{f['extent']['n_outer']} outer loops and {f['extent']['n_holes']} holes; features.json record "
              f"{size} bytes, the outline as JSON {full} bytes, its file {p.stat().st_size} bytes")
    # the outline route: the loops, for the mesh version they belong to
    o = get(f"{b}/features/{f['id']}/outline")
    assert o.status_code == 200, o.text
    o = o.json()
    assert o["id"] == f["id"] and o["mesh_version"] == click["version"]
    check_outline(o, want)
    # deleted with the feature
    assert delete(f"{b}/features/{f['id']}").status_code == 204
    assert not p.exists()
    err(get(f"{b}/features/{f['id']}/outline"), 404, "no longer in the scan")
    err(get(f"{b}/features/f-nope/outline"), 404, "no longer in the scan")


def test_fit_again_refreshes_the_extent_and_the_outline(clean):
    b = base_url(clean)
    click = table_click(clean)
    f = post(b + "/features", {"kind": "plane", **click}).json()
    v0 = click["version"]
    # an edit that leaves the fit's region alone (as test_server_features.py)
    _, V, F = arrays(clean)
    C = V[F].mean(axis=1)
    rest = np.setdiff1d(np.arange(len(F)), decoded(f["faces"]))
    cut = rest[C[rest, 2] <= np.percentile(C[rest, 2], 5)]
    run_job(clean, post(b + "/process/edits", {"op": "delete_faces", "params": {"faces": faces_b64(cut)},
                                               "version": v0}), "mesh_edit")
    v1 = arrays(clean)[0]
    assert v1 > v0
    # stale: the outline still answers, for the version it was fitted on
    assert get(f"{b}/features/{f['id']}/outline").json()["mesh_version"] == v0
    out = post(b + "/features/refit", {}).json()
    assert out["refitted"] == [f["id"]]
    g = out["features"]["features"][0]
    assert g["mesh_version"] == v1 and g["stale"] is False
    faces = decoded(get(f"{b}/features/{f['id']}/faces").json()["faces"])
    want = engine_extent(clean, g, faces)
    check_plane_extent(g["extent"], want)
    o = get(f"{b}/features/{f['id']}/outline").json()
    assert o["mesh_version"] == v1
    check_outline(o, want)


def test_a_fit_kept_before_extents_loads_and_gets_one_on_fit_again(clean):
    """A fit kept before this task has ``extent`` null and no outline file: it loads and shows as
    before, and "fit again" gives it its extent (from its own click: it is not stale, so its shape
    stays the same)."""
    b = base_url(clean)
    click = table_click(clean)
    fit = post(b + "/fit", {"kind": "plane", **click}).json()
    sd = scan_dir(clean)
    doc = kf.empty()
    doc["features"] = [{"id": "f-old", "name": "Old table", "kind": "plane", "shape": fit["shape"],
                        "seed": {"kind": "plane", "face": click["face"], "point": click["point"], "tolerance_mm": None},
                        "n_faces": fit["n_faces"], "tolerance_mm": fit["tolerance_mm"],
                        "mean_sigma_mm": fit["mean_sigma_mm"], "correlated_points": fit["correlated_points"],
                        "warnings": fit["warnings"], "mesh_version": fit["version"], "extent": None,
                        "created": "2026-11-17T09:00:00"}]
    kf.save(sd, doc)
    kf.save_faces(sd, "f-old", decoded(fit["faces"]))
    s = get(b + "/features").json()
    old = s["features"][0]
    assert old["extent"] is None and old["stale"] is False and old["shape"] == fit["shape"]
    err(get(f"{b}/features/f-old/outline"), 404, "before outlines were measured")
    out = post(b + "/features/refit", {}).json()
    assert out["refitted"] == ["f-old"] and out["failed"] == []
    g = out["features"]["features"][0]
    assert g["shape"] == fit["shape"] and g["name"] == "Old table" and g["mesh_version"] == fit["version"]
    check_plane_extent(g["extent"], engine_extent(clean, g, decoded(fit["faces"])))
    assert get(f"{b}/features/f-old/outline").status_code == 200
    # once it has its extent there is nothing more to fit again
    again = post(b + "/features/refit", {}).json()
    assert again["refitted"] == [] and again["failed"] == []


# --------------------------------------------------------------------------- relations
_Q, _ = np.linalg.qr(np.random.default_rng(11).normal(size=(3, 3)))
Q = _Q if np.linalg.det(_Q) > 0 else -_Q


def P(x) -> list[float]:
    return [float(v) for v in np.asarray(x, dtype=np.float64) @ Q.T + np.array([5.0, -20.0, 240.0])]


def D(d) -> list[float]:
    return [float(v) for v in np.asarray(d, dtype=np.float64) @ Q.T]


SHAPES = {  # a 20 mm block: two faces, a pin across it, two holes in its top
    "f-top": ("Top", "plane", {"kind": "plane", "point": P([1, 2, 10]), "normal": D([0, 0.001, 1]), "rms_mm": 0.02,
                               "flatness_mm": 0.1, "n": 900, "normal_sigma_deg": 0.03, "offset_sigma_mm": 0.004}),
    "f-bottom": ("Bottom", "plane", {"kind": "plane", "point": P([-3, 1, -10]), "normal": D([0, 0, 1]), "rms_mm": 0.02,
                                     "flatness_mm": 0.1, "n": 900, "normal_sigma_deg": 0.04, "offset_sigma_mm": 0.005}),
    "f-pin": ("Pin", "cylinder", {"kind": "cylinder", "point": P([0, 4, 0]), "axis": D([1, 0, 0]), "radius_mm": 2.0,
                                  "diameter_mm": 4.0, "rms_mm": 0.01, "n": 300, "radius_sigma_mm": 0.003,
                                  "diameter_sigma_mm": 0.006, "axis_sigma_deg": 0.05, "point_sigma_mm": 0.005}),
    "f-hole": ("Hole", "hole", {"kind": "circle", "center": P([3, -2, 10]), "normal": D([0, 0, 1]), "radius_mm": 3.0,
                                "diameter_mm": 6.0, "rms_mm": 0.01, "n": 80, "radius_sigma_mm": 0.003,
                                "diameter_sigma_mm": 0.006, "center_sigma_mm": 0.004}),
    "f-hole2": ("Dowel", "hole", {"kind": "circle", "center": P([-5, 4, 10]), "normal": D([0, 0, 1]), "radius_mm": 2.0,
                                  "diameter_mm": 4.0, "rms_mm": 0.01, "n": 80, "radius_sigma_mm": 0.003,
                                  "diameter_sigma_mm": 0.006, "center_sigma_mm": 0.004}),
}


def record(fid: str, version: int) -> dict:
    name, kind, shape = SHAPES[fid]
    return {"id": fid, "name": name, "kind": kind, "shape": shape,
            "seed": {"kind": kind, "face": 0, "point": shape.get("point", shape.get("center")), "tolerance_mm": None},
            "n_faces": 100, "tolerance_mm": None, "mean_sigma_mm": None, "correlated_points": None, "warnings": [],
            "mesh_version": version, "extent": None, "created": "2026-11-17T12:00:00"}


def engine(a: str, b: str) -> dict:
    """``relate`` on two kept shapes, as the viewer's ``{kind, shape}``."""
    return relate(*({"kind": SHAPES[x][1], "shape": SHAPES[x][2]} for x in (a, b))).to_dict()


@pytest.fixture()
def block(clean):
    """The block's fits kept on the current mesh; the bottom one on an earlier mesh."""
    v = arrays(clean)[0]
    doc = kf.empty()
    doc["features"] = [record(fid, v) for fid in SHAPES]
    doc["features"][1]["mesh_version"] = v - 1
    kf.save(scan_dir(clean), doc)
    return clean


PAIRS = [("f-top", "f-bottom"), ("f-top", "f-pin"), ("f-pin", "f-hole"), ("f-hole", "f-hole2"), ("f-hole", "f-top")]


def test_kept_relations_carry_the_engines_record(block):
    b = base_url(block)
    made = []
    for a, c in PAIRS:
        r = post(b + "/features/relations", {"a": a, "b": c})
        assert r.status_code == 200, r.text
        rel = r.json()
        assert rel["relation"] == engine(a, c)
        assert rel["stale"] is ("f-bottom" in (a, c))
        made.append(rel)
    s = get(b + "/features").json()
    assert [r["id"] for r in s["relations"]] == [r["id"] for r in made]
    for rel, (a, c) in zip(s["relations"], PAIRS):
        assert rel["a"] == a and rel["b"] == c
        assert rel["relation"] == engine(a, c)
        assert rel["stale"] is ("f-bottom" in (a, c))  # one of its fits was fitted on an earlier mesh
    # the record is the engine's: kinds, parallel, values with their 1σ, notes
    top_bottom = s["relations"][0]["relation"]
    assert top_bottom["kinds"] == ["plane", "plane"] and top_bottom["parallel"] is True
    assert [q["name"] for q in top_bottom["values"]] == ["angle", "distance"]
    assert all(q["sigma"] is not None and q["sigma"] > 0 for q in top_bottom["values"])
    # a rename answers the relation as it is shown
    rn = requests.patch(f"{b}/features/{made[0]['id']}", json={"name": "Thickness"}, timeout=T).json()
    assert rn["name"] == "Thickness" and rn["relation"] == engine("f-top", "f-bottom") and rn["stale"] is True
    # nothing of it is stored: features.json keeps only the two ids
    stored = kf.load(scan_dir(block))["relations"][0]
    assert set(stored) == {"id", "name", "a", "b", "created"}


def test_the_relation_of_two_fits_not_kept_as_one(block):
    b = base_url(block)
    for a, c in PAIRS:
        r = get(f"{b}/features/relate?a={a}&b={c}")
        assert r.status_code == 200, r.text
        out = r.json()
        assert out == {"a": a, "b": c, "stale": "f-bottom" in (a, c), "relation": engine(a, c)}
    # a hole's axis has no ±: the engine says None, and so does the route
    hole_top = get(f"{b}/features/relate?a=f-hole&b=f-top").json()["relation"]
    assert hole_top["values"][0]["name"] == "angle" and hole_top["values"][0]["sigma"] is None
    # nothing was kept by asking
    assert kf.load(scan_dir(block))["relations"] == []
    err(get(f"{b}/features/relate?a=f-top&b=f-nope"), 404, "no longer in the scan")
    err(get(f"{b}/features/relate?a=f-nope&b=f-top"), 404, "no longer in the scan")
    err(get(f"{b}/features/relate?a=f-top&b=f-top"), 422, "itself")
    assert get(f"{b}/features/relate?a=f-top").status_code == 422
    err(get(base_url(block, sid="scan-missing") + "/features/relate?a=f-top&b=f-pin"), 404)


# --------------------------------------------------------------------------- a cylinder's form
def test_a_kept_cylinder_keeps_its_form_and_a_file_without_one_loads(clean):
    """A kept cylinder says whether it is a boss or a bore (``form``, from the fit). Written into
    ``features.json`` with "boss", "bore", null or no ``form`` at all (kept before it existed), the
    file loads and the features answer as kept; another value, or a form on a plane, is refused."""
    v = arrays(clean)[0]
    doc = kf.empty()
    old = record("f-pin", v)  # no "form" key: kept before forms existed
    doc["features"] = [old, {**record("f-pin", v), "id": "f-boss", "form": "boss"},
                       {**record("f-pin", v), "id": "f-bore", "form": "bore"},
                       {**record("f-pin", v), "id": "f-unknown", "form": None},
                       {**record("f-top", v), "form": None}]
    kf.save(scan_dir(clean), doc)
    s = get(base_url(clean) + "/features")
    assert s.status_code == 200, s.text
    got = {f["id"]: f for f in s.json()["features"]}
    assert "form" not in got["f-pin"]
    assert got["f-boss"]["form"] == "boss" and got["f-bore"]["form"] == "bore" and got["f-unknown"]["form"] is None
    assert got["f-top"]["form"] is None
    for bad, kind, what in (("shaft", "f-pin", "form"), (1, "f-pin", "form"), ("boss", "f-top", "cylinder")):
        d = kf.empty()
        d["features"] = [{**record(kind, v), "form": bad}]
        with pytest.raises(ValueError, match=what):
            kf.validate(d)


def test_the_form_is_asked_for_when_a_fit_is_kept_and_fitted_again(clean, monkeypatch):
    """``POST .../fit`` answers today's keys; the kept features ask the fit for its form
    (``with_form``) when a fit is kept and when it is fitted again, and keep it (a plane's is null).
    A ball on a table has no cylinder to click, so a cylinder's form is checked through the record
    the kept features make of a fit's answer."""
    from ohs.server.features import FeatureManager

    b = base_url(clean)
    click = table_click(clean)
    r = post(b + "/fit", {"kind": "plane", **click})
    assert r.status_code == 200 and set(r.json()) == FIT_KEYS
    pm = clean["studio"].processing
    asked = []
    real = pm.fit

    def fit(*args, **kwargs):
        asked.append(kwargs.get("with_form", False))
        return real(*args, **kwargs)

    monkeypatch.setattr(pm, "fit", fit)
    f = post(b + "/features", {"kind": "plane", **click})
    assert f.status_code == 200, f.text
    assert f.json()["form"] is None and kf.load(scan_dir(clean))["features"][0]["form"] is None
    doc = kf.load(scan_dir(clean))
    doc["features"][0]["extent"] = None  # "fit again" measures it from its own click
    del doc["features"][0]["form"]
    kf.save(scan_dir(clean), doc)
    out = post(b + "/features/refit", {}).json()
    assert out["refitted"] == [f.json()["id"]]
    assert asked == [True, True]
    assert "form" in kf.load(scan_dir(clean))["features"][0]
    # a cylinder's answer: its form goes into the record
    answer = {"kind": "cylinder", "shape": SHAPES["f-pin"][2], "faces": "", "n_faces": 3, "tolerance_mm": 0.1,
              "mean_sigma_mm": None, "correlated_points": 2.0, "warnings": [], "version": 1, "extent": None}
    seed = {"kind": "cylinder", "face": 0, "point": [0.0, 0.0, 0.0], "tolerance_mm": None}
    for form in ("boss", "bore", None):
        rec, _ = FeatureManager._record({**answer, "form": form}, seed, "f-c", "Pin", "2026-11-17T12:00:00")
        assert rec["form"] == form
        assert kf.validate({**kf.empty(), "features": [rec]})["features"][0]["form"] == form
