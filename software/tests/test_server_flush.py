"""The flush along a seam over the API (FEATURE_PLAN.md I4, the app part; APP_API.md "Flush along a seam"):
the scan's current mesh measured from two clicks, one on each panel near the seam
(``ohs.inspect.gapflush.measure_gap_flush``), answered as the engine's record (the stations with their flush,
± and what each saw, lowest / mean / highest, and the gap marked as not offered with the engine's reason);
the stations as a CSV download; seams kept with the scan under ``seams`` in ``features.json`` (the two clicks
as numbers, not the stations), listed, renamed, deleted, and measured again on the current mesh when opened.

Every answer must equal the engine's ``measure_gap_flush(...).to_dict()`` on the very arrays the server
measures. The test scan (the 10-frame ball on a table of ``test_server_process_studio.py``, tracked once at
preview quality) has no seam, so a synthetic two-plate mesh (``test_inspect_gapflush.py``'s plates: a gap of
4 mm, flush 1 mm) is put in place of the scan's mesh: ``ProcessManager.mesh_arrays``, the one way every mesh
route reads the current mesh, is replaced for the test by one that answers the plates with the real mesh's
version (so an edit of the real mesh still makes a new version). On the ball scan itself a click off the
surface is refused in the engine's words.
"""
from __future__ import annotations

import csv
import io
import json

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

import ohs.measure.features as kf  # noqa: E402
from ohs.inspect.gapflush import GAP_NOTE, measure_gap_flush  # noqa: E402
from ohs.server.protocol import sentence  # noqa: E402
from test_inspect_gapflush import _clicks, _plates  # noqa: E402
from test_server_process_studio import T, base_url, env, err, faces_b64, get, post, run_job  # noqa: E402,F401

SIGMA_MM = 0.05  # each plate vertex's sigma
CSV_HEAD = ["position_mm", "x_mm", "y_mm", "z_mm", "flush_mm", "flush_sigma_mm", "flush_trusted", "side_a", "side_b",
            "bridged", "notes"]


# --------------------------------------------------------------------------- helpers
def scan_dir(e: dict):
    return e["root"] / "projects" / e["pid"] / "scans" / e["sid"]


def patch(url: str, body: dict | None):
    return requests.patch(url, json=body, timeout=T)


def delete(url: str):
    return requests.delete(url, timeout=T)


def plain(x):
    return json.loads(json.dumps(x))


def arrays(e: dict):
    return e["studio"].processing.mesh_arrays(e["pid"], e["sid"])


def engine(e: dict, a, b) -> dict:
    """The engine's record on the arrays the server measures."""
    _, V, F, S, Fl = arrays(e)
    return plain(measure_gap_flush(V, F, a, b, sigma=S, filled=Fl).to_dict())


def same(answer: dict, want: dict) -> None:
    """The answer holds the engine's record, key for key."""
    for k, v in want.items():
        assert answer[k] == v, k


def clicks() -> tuple[list[float], list[float]]:
    a, b = _clicks(4.0, 1.0)
    return [float(x) for x in a], [float(x) for x in b]


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def tracked(env):  # noqa: F811 (the fixture imported above)
    """The ball scan tracked at preview quality; the routes answer before there is a mesh first."""
    b = base_url(env)
    a_, b_ = clicks()
    err(post(b + "/flush", {"a": a_, "b": b_}), 409, "mesh")
    err(post(b + "/seams", {"a": a_, "b": b_}), 409, "mesh")
    err(requests.get(b + "/flush/stations.csv", params={"a": ",".join(map(str, a_)), "b": ",".join(map(str, b_))},
                     timeout=T), 409, "mesh")
    s = get(b + "/seams")
    assert s.status_code == 200, s.text
    assert s.json() == {"mesh_version": None, "seams": []}
    err(get(base_url(env, sid="scan-missing") + "/seams"), 404, "scan")
    run_job(env, post(b + "/process/track", {"quality": "preview"}), "process_track")
    return env


@pytest.fixture()
def plates(tracked, monkeypatch):
    """Two plates in place of the scan's mesh (module docstring), with the real mesh's version; the kept set
    cleared before and after."""
    proc = tracked["studio"].processing
    real = proc.mesh_arrays
    V, F, _ = _plates(length=120.0)
    S = np.full(len(V), SIGMA_MM)
    Fl = np.zeros(len(F), dtype=bool)

    def fake(pid, sid):
        v = real(pid, sid)[0]
        return v, V, F, S, Fl

    kf.save(scan_dir(tracked), kf.empty())
    monkeypatch.setattr(proc, "mesh_arrays", fake)
    tracked["real_arrays"] = real
    yield tracked
    monkeypatch.undo()
    kf.save(scan_dir(tracked), kf.empty())


# --------------------------------------------------------------------------- the measurement
def test_the_answer_is_the_engines_record(plates):
    b = base_url(plates)
    a_, b_ = clicks()
    r = post(b + "/flush", {"a": a_, "b": b_})
    assert r.status_code == 200, r.text
    got = r.json()
    want = engine(plates, a_, b_)
    same(got, want)
    assert got["mesh_version"] == arrays(plates)[0] and got["kept"] is None and got["warnings"] == []
    assert len(got["stations"]) >= 30 and got["flush"]["mean"] == pytest.approx(1.0, abs=0.02)
    # the gap is worked out for the record and not offered, with the engine's reason
    assert got["gap_offered"] is False and got["gap_note"] == GAP_NOTE
    assert got["filename"].endswith(".csv") and "Sphere" in got["filename"]
    # the clicks the other way round: the sign of the flush turns
    r = post(b + "/flush", {"a": b_, "b": a_})
    assert r.status_code == 200 and r.json()["flush"]["mean"] == pytest.approx(-1.0, abs=0.02)


def test_the_stations_as_csv(plates):
    b = base_url(plates)
    a_, b_ = clicks()
    want = engine(plates, a_, b_)
    q = {"a": ",".join(map(repr, a_)), "b": ",".join(map(repr, b_))}
    r = requests.get(b + "/flush/stations.csv", params=q, timeout=T)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    cd = r.headers["content-disposition"]
    assert "attachment" in cd and cd.rstrip('"').endswith(".csv")
    assert int(r.headers["x-ohs-mesh-version"]) == arrays(plates)[0]
    assert r.content.startswith(b"\xef\xbb\xbf")  # a byte-order mark: spreadsheets read ± and Ø
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0] == CSV_HEAD  # no gap column: the gap is not offered
    assert len(rows) - 1 == len(want["stations"])
    for row, s in zip(rows[1:], want["stations"]):
        cell = dict(zip(CSV_HEAD, row))
        assert float(cell["position_mm"]) == s["position_mm"]
        assert [float(cell[k]) for k in ("x_mm", "y_mm", "z_mm")] == s["point"]
        assert (float(cell["flush_mm"]) if cell["flush_mm"] else None) == s["flush_mm"]
        assert cell["flush_trusted"] == ("yes" if s["flush_trusted"] else "no")
        assert cell["side_a"] == s["side_a"] and cell["side_b"] == s["side_b"]
        assert cell["notes"] == " | ".join(s["notes"])
    err(requests.get(b + "/flush/stations.csv", params={"a": "1,2"}, timeout=T), 422)
    err(requests.get(b + "/flush/stations.pdf", params=q, timeout=T), 404)


def test_keep_list_rename_and_delete(plates):
    b = base_url(plates)
    sd = scan_dir(plates)
    a_, b_ = clicks()
    features_before = get(b + "/features").json()
    r = post(b + "/seams", {"a": a_, "b": b_, "name": "  Door to wing "})
    assert r.status_code == 200, r.text
    s = r.json()
    v = arrays(plates)[0]
    assert s["id"].startswith("sm-") and s["name"] == "Door to wing"
    assert s["a"] == a_ and s["b"] == b_ and s["mesh_version"] == v and s["mesh_changed"] is False and s["created"]
    stored = kf.load(sd)["seams"]
    assert stored == [{k: x for k, x in s.items() if k != "mesh_changed"}]
    assert set(stored[0]) == {"id", "name", "a", "b", "mesh_version", "created"}  # not the stations
    s2 = post(b + "/seams", {"a": b_, "b": a_}).json()
    assert s2["name"] == "Seam 2"
    listed = get(b + "/seams").json()
    assert listed["mesh_version"] == v and [x["id"] for x in listed["seams"]] == [s["id"], s2["id"]]
    assert get(b + "/features").json() == features_before  # GET .../features answers as before
    # opened: measured again on the current mesh
    r = get(f"{b}/seams/{s['id']}")
    assert r.status_code == 200, r.text
    got = r.json()
    same(got, engine(plates, a_, b_))
    assert got["kept"] == s and got["warnings"] == [] and "Door_to_wing" in got["filename"]
    csv_ = get(f"{b}/seams/{s['id']}/stations.csv")
    assert csv_.status_code == 200 and "Door_to_wing" in csv_.headers["content-disposition"]
    assert len(list(csv.reader(io.StringIO(csv_.content.decode("utf-8-sig"))))) == len(got["stations"]) + 1
    # rename
    r = patch(f"{b}/seams/{s['id']}", {"name": "Bonnet"})
    assert r.status_code == 200 and r.json()["name"] == "Bonnet" and kf.load(sd)["seams"][0]["name"] == "Bonnet"
    err(patch(f"{b}/seams/{s['id']}", {"name": "   "}), 422)
    err(patch(f"{b}/seams/{s['id']}", {}), 422)
    err(patch(f"{b}/seams/sm-nope", {"name": "x"}), 404)
    # delete
    assert delete(f"{b}/seams/{s['id']}").status_code == 204
    assert [x["id"] for x in get(b + "/seams").json()["seams"]] == [s2["id"]]
    err(delete(f"{b}/seams/{s['id']}"), 404)
    err(get(f"{b}/seams/{s['id']}"), 404)
    err(get(f"{b}/seams/{s['id']}/stations.csv"), 404)
    assert delete(f"{b}/seams/{s2['id']}").status_code == 204
    assert "seams" not in json.loads((sd / kf.FILE_NAME).read_text(encoding="utf-8"))  # as before any was kept


def test_measured_again_after_an_edit_and_saying_so(plates):
    b = base_url(plates)
    a_, b_ = clicks()
    s = post(b + "/seams", {"a": a_, "b": b_}).json()
    v0 = s["mesh_version"]
    # an edit of the real mesh: a new version (the plates stay in place of it)
    _, _, Freal, _, _ = plates["real_arrays"](plates["pid"], plates["sid"])
    run_job(plates, post(b + "/process/edits", {"op": "delete_faces", "params": {"faces": faces_b64(np.arange(0, len(Freal), 97))},
                                                "version": v0}), "mesh_edit")
    v1 = arrays(plates)[0]
    assert v1 > v0
    got = get(f"{b}/seams/{s['id']}").json()
    same(got, engine(plates, a_, b_))
    assert got["mesh_version"] == v1 and got["kept"]["mesh_changed"] is True and got["kept"]["mesh_version"] == v0
    assert len(got["warnings"]) == 1 and "changed" in got["warnings"][0]
    assert get(b + "/seams").json()["seams"][0]["mesh_changed"] is True
    assert kf.load(scan_dir(plates))["seams"][0]["mesh_version"] == v0  # reading writes nothing
    r = patch(f"{b}/seams/{s['id']}", {"mark_current": True})
    assert r.status_code == 200, r.text
    assert r.json()["mesh_version"] == v1 and r.json()["mesh_changed"] is False and r.json()["a"] == a_
    assert get(f"{b}/seams/{s['id']}").json()["warnings"] == []


def test_bad_bodies_and_unknown_ids(plates):
    b = base_url(plates)
    a_, b_ = clicks()
    err(post(b + "/flush", {"a": a_}), 422)
    err(post(b + "/flush", {"a": a_, "b": [1.0, 2.0]}), 422)
    err(post(b + "/flush", {"a": a_, "b": ["x", 0, 0]}), 422)
    err(post(b + "/flush", {}), 422)
    # clicks the engine refuses: its own words
    with pytest.raises(ValueError) as refused:
        engine(plates, a_, a_)
    r = err(post(b + "/flush", {"a": a_, "b": a_}), 422)
    assert r["detail"] == sentence(str(refused.value))
    err(post(b + "/flush", {"a": [a_[0], a_[1], a_[2] + 40.0], "b": b_}), 422, "mesh")
    err(post(b + "/seams", {"a": a_, "b": b_, "name": ""}), 422)
    err(post(b + "/seams", {"a": a_}), 422)
    assert get(b + "/seams").json()["seams"] == []  # bad bodies keep nothing
    err(get(f"{b}/seams/sm-nothing"), 404, "seam")
    err(delete(f"{b}/seams/sm-nothing"), 404)
    err(post(base_url(plates, sid="scan-missing") + "/flush", {"a": a_, "b": b_}), 404)


def test_on_the_ball_scan_a_click_off_the_surface_is_refused_in_the_engines_words(tracked):
    """No stand-in here: the real mesh of the ball on its table."""
    b = base_url(tracked)
    _, V, F, _, _ = arrays(tracked)
    top = np.asarray(V, dtype=np.float64)[np.unique(F)].max(axis=0)
    a_ = [float(x) for x in top + 500.0]
    b_ = [float(x) for x in top + 520.0]
    r = err(post(b + "/flush", {"a": a_, "b": b_}), 422, "first click")
    assert "mm from the mesh" in r["detail"]


def test_a_file_without_seams_loads_and_ids_stay_unique(tracked):
    sd = scan_dir(tracked)
    kf.save(sd, kf.empty())
    text = (sd / kf.FILE_NAME).read_text(encoding="utf-8")
    assert "seams" not in text
    assert get(base_url(tracked) + "/seams").json()["seams"] == []
    assert (sd / kf.FILE_NAME).read_text(encoding="utf-8") == text  # nothing written by reading
    good = {"id": "sm-1", "name": "A", "a": [0, 0, 0], "b": [10, 0, 0], "mesh_version": 1, "created": "2026-11-17T12:00:00"}
    assert kf.validate({**kf.empty(), "seams": [good]})["seams"] == [{**good, "a": [0.0, 0.0, 0.0], "b": [10.0, 0.0, 0.0]}]
    for bad in ({**good, "id": "a b"}, {**good, "name": ""}, {**good, "a": [0, 0]}, {**good, "b": [0, 0, float("nan")]},
                {**good, "mesh_version": -1}, "seam"):
        with pytest.raises(ValueError):
            kf.validate({**kf.empty(), "seams": [bad]})
    with pytest.raises(ValueError):
        kf.validate({**kf.empty(), "seams": {"sm-1": good}})
    m = {"id": "sm-1", "name": "D", "a": [0, 0, 0], "b": [1, 0, 0], "mesh_version": 1, "created": ""}
    with pytest.raises(ValueError, match="twice"):
        kf.validate({**kf.empty(), "measurements": [m], "seams": [good]})
    # a section and a seam with the same id
    sec = {"id": "sm-1", "name": "S", "plane": {"point": [0, 0, 0], "normal": [0, 0, 1], "u": [1, 0, 0], "v": [0, 1, 0],
                                                 "description": "d", "in_part_frame": False},
           "definition": {"kind": "point_normal", "point": [0, 0, 0], "normal": [0, 0, 1]}, "mesh_version": 1, "created": ""}
    with pytest.raises(ValueError, match="twice"):
        kf.validate({**kf.empty(), "sections": [sec], "seams": [good]})
    kf.save(sd, kf.empty())
