"""The report of a scan over the API (FEATURE_PLAN.md I3, the app part; APP_API.md "The report"): the scan's data
collected on the server, each part from its own manager, into the plain structure of ``ohs.report.data``, and
answered as the self-contained HTML page (``build_html``), the kept features as CSV (``features_csv``, written with
a byte-order mark) and as JSON (``features_json``). The request is a POST with a JSON body: the tolerance for the
Check answer, numbers in the part's frame, the kept seam to show, and pictures of the model (PNG bytes in base64).

Uses the 10-frame ball-on-a-table scan of ``test_server_process_studio.py`` (its ``env`` fixture), tracked once at
preview quality. The kept fits, the approval, the relation, the section, the seam and the part's frame are written
straight into ``features.json`` (as ``test_server_section.py`` and ``test_server_nominal.py`` do); the comparison is
the reference test's (the scan's own ball as the reference, aligned from picked points); the ball has no seam, so
the flush engine is replaced for the seam by a stand-in that measures ``test_inspect_gapflush.py``'s two plates
(with a stretch of the second plate missing, so some stations are not trusted). The page is read back with the
engine tests' parser (``test_report_html.Page``) and checked by their test that nothing in it reaches for the
network, with the same hostile names.
"""
from __future__ import annotations

import base64
import csv
import datetime as dt
import hashlib
import io
import json

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

import ohs.measure.features as kf  # noqa: E402
from ohs.inspect.gapflush import measure_gap_flush  # noqa: E402
from ohs.measure.frame import part_frame  # noqa: E402
from ohs.report.tables import CSV_COLUMNS  # noqa: E402
from test_inspect_gapflush import _clicks, _plates  # noqa: E402
from test_report_html import FIT_HOSTILE, PICTURE_HOSTILE, SECTION_HOSTILE, Page, _png  # noqa: E402
from test_report_html import test_nothing_reaches_for_the_network_and_names_stay_text as network_check  # noqa: E402
from test_server_nominal import hole_shape  # noqa: E402
from test_server_process_studio import T, base_url, env, err, get, post, run_job  # noqa: E402,F401
from test_server_reference import align_points, refs_url, start, the_ball, upload  # noqa: E402
from test_server_section import centre, plane_shape, record  # noqa: E402

PARTS_ALWAYS = {"head", "warnings", "meaning", "calibration"}
OPTIONAL = {"check", "frame", "pictures", "features", "relations", "sections", "comparison", "flush"}


# --------------------------------------------------------------------------- helpers
def scan_dir(e: dict):
    return e["root"] / "projects" / e["pid"] / "scans" / e["sid"]


def project_dir(e: dict):
    return e["root"] / "projects" / e["pid"]


def on_disk(d) -> dict:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


def report(e: dict, name: str = "report.html", **body):
    return requests.post(base_url(e) + f"/report/{name}", json=body, timeout=60)


def page(e: dict, **body) -> tuple[str, Page]:
    r = report(e, **body)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html")
    text = r.content.decode("utf-8")
    p = Page(text)
    assert not p.errors, p.errors
    return text, p


def arrays(e: dict):
    return e["studio"].processing.mesh_arrays(e["pid"], e["sid"])


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def seam_plates():
    """Two plates (gap 4 mm, flush 1 mm) with 10 mm of the second plate's top missing: some stations have no
    second surface and their flush is not trusted. Returns (V, F, click a, click b)."""
    V, F, plate = _plates(length=120.0)
    C = V[F].mean(axis=1)
    keep = ~((plate == 1) & (np.abs(C[:, 0]) < 5.0))
    a, b = _clicks(4.0, 1.0, x=30.0)
    return V, F[keep], a, b


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def tracked(env):  # noqa: F811 (the fixture imported above)
    """The ball scan tracked at preview quality. Before there is a mesh the report is made of what exists."""
    text, p = page(env, tolerance_mm=0.5)
    assert set(p.order) <= PARTS_ALWAYS and "check" not in p.order and "made" not in p.order
    err(report(env | {"sid": "scan-missing"}), 404, "scan")
    run_job(env, post(base_url(env) + "/process/track", {"quality": "preview"}), "process_track")
    return env


@pytest.fixture()
def clean(tracked):
    kf.save(scan_dir(tracked), kf.empty())
    yield tracked
    kf.save(scan_dir(tracked), kf.empty())


@pytest.fixture()
def full(clean, monkeypatch):
    """A kept plane on top and one at the side, a hole with an approval marked "check again", a relation, a section
    kept on an earlier mesh, a comparison with the ball and a kept seam (measured by the stand-in)."""
    e = clean
    sd = scan_dir(e)
    v = arrays(e)[0]
    c = centre(e)
    hole = hole_shape(10.04, 0.03)
    hole["center"] = [float(x) for x in c + [0.0, 0.0, 5.0]]
    doc = kf.empty()
    doc["features"] = [record("f-top", "Datum top", plane_shape(c, [0.0, 0.0, 1.0]), v),
                       record("f-side", "Datum side", plane_shape(c + [5.0, 0, 0], [1.0, 0.0, 0.0]), v),
                       {**record("f-bore", FIT_HOSTILE, hole, v, kind="hole"),
                        "nominals": [{"value": "diameter", "nominal": 10.5, "unit": "mm", "word": None, "source": "entered",
                                      "measured": 10.5, "sigma": 0.03, "approved": "2026-11-17T12:00:00"}]}]
    doc["relations"] = [{"id": "r-sq", "name": "Squareness A-B", "a": "f-top", "b": "f-side", "created": "2026-11-17T12:00:00"}]
    doc["sections"] = [{"id": "s-1", "name": SECTION_HOSTILE,
                        "plane": {"point": [float(x) for x in c], "normal": [0.0, 1.0, 0.0], "u": [1.0, 0.0, 0.0],
                                  "v": [0.0, 0.0, -1.0], "description": "Section through the ball", "in_part_frame": False},
                        "definition": {"kind": "point_normal", "point": [float(x) for x in c], "normal": [0.0, 1.0, 0.0]},
                        "mesh_version": v - 1, "created": "2026-11-17T12:00:00"}]
    Vp, Fp, a, b = seam_plates()
    doc["seams"] = [{"id": "sm-door", "name": "Door seam", "a": [float(x) for x in a], "b": [float(x) for x in b],
                     "mesh_version": v, "created": "2026-11-17T12:00:00"}]
    kf.save(sd, doc)
    import ohs.server.flush as flush_module

    real = measure_gap_flush
    monkeypatch.setattr(flush_module, "measure_gap_flush", lambda V, F, a_, b_, **kw: real(Vp, Fp, a_, b_))
    # the comparison: the scan's own ball as the reference, aligned from picked points
    from ohs.track.export import save_mesh

    Vr, Fr = the_ball(e)
    save_mesh(e["root"] / "report-ball.stl", Vr, Fr)
    ref = upload(e, (e["root"] / "report-ball.stl").read_bytes(), "ball.stl").json()
    start(e, ref["id"], tolerance_mm=0.4)
    align_points(e)
    e["seam"] = (Vp, Fp, a, b)
    yield e
    requests.delete(f"{refs_url(e)}/{ref['id']}", timeout=T)
    requests.delete(base_url(e) + "/comparison", timeout=T)


# --------------------------------------------------------------------------- the page
def test_a_scan_with_everything_kept(full):
    before = on_disk(project_dir(full))
    png = _png(3, 2)
    text, p = page(full, tolerance_mm=0.5, pictures=[{"title": PICTURE_HOSTILE, "caption": "From the front", "data": b64(png)}])
    assert p.order == ["head", "warnings", "meaning", "made", "calibration", "check", "pictures", "features", "relations",
                       "sections", "comparison", "flush"]
    # each name once, where it belongs
    for part, name in (("features", "Datum top"), ("features", "Datum side"), ("features", FIT_HOSTILE),
                       ("relations", "Squareness A-B"), ("sections", SECTION_HOSTILE), ("flush", "Door seam"),
                       ("comparison", "ball.stl"), ("pictures", PICTURE_HOSTILE)):
        assert p.text(part).count(name) == 1, (part, name)
    scan =full["studio"].projects.scan_entry(full["pid"], full["sid"])
    project = full["studio"].projects.get(full["pid"])
    assert scan["name"] in p.text("head") and project["name"] in p.text("head")
    assert dt.date.today().isoformat() in p.text("head")
    # the approval as the API shows it: marked "check again", with the API's reason among the warnings
    view = get(base_url(full) + "/features").json()
    bore = next(f for f in view["features"] if f["id"] == "f-bore")
    assert bore["nominals"][0]["check_again"] is True
    reason = bore["nominals"][0]["check_reason"]
    assert "check again" in p.text("features") and reason in p.text("warnings")
    # every part's warnings in the list near the top
    w = p.text("warnings")
    assert "the mesh has changed since this section was kept" in w
    want = measure_gap_flush(*full["seam"])
    assert 0 < want.flush.n_trusted < want.flush.n_stations
    assert f"not trusted at {want.flush.n_stations - want.flush.n_trusted} of {want.flush.n_stations} stations" in w
    # the parts' own numbers
    assert "± 0.5 mm" in p.text("check")
    assert "Gap is not shown" in p.text("flush") and "Door seam" in p.text("flush")
    assert "within ± 0.4 mm" in p.text("comparison")  # the comparison's kept tolerance
    srcs = [dict(a)["src"] for t, a in p.tags if t == "img"]
    assert "data:image/png;base64," + b64(png) in srcs
    # nothing reaches for the network; the hostile names are text
    network_check((None, text, p))
    assert on_disk(project_dir(full)) == before  # built on request, written nowhere


def test_the_seam_shown_is_the_one_asked_for(full):
    sd = scan_dir(full)
    doc = kf.load(sd)
    doc["seams"].append({**doc["seams"][0], "id": "sm-boot", "name": "Boot seam"})
    kf.save(sd, doc)
    _, p = page(full)
    assert "Door seam" in p.text("flush") and "Boot seam" not in p.text("flush")
    _, p = page(full, seam="sm-boot")
    assert "Boot seam" in p.text("flush") and "Door seam" not in p.text("flush")
    err(report(full, seam="sm-nothing"), 404, "seam")


def test_a_scan_with_nothing_kept_has_only_the_parts_that_exist(clean):
    _, p = page(clean)
    assert not set(p.order) & OPTIONAL, p.order
    assert set(p.order) >= PARTS_ALWAYS
    # with a tolerance the Check answer is in
    _, p = page(clean, tolerance_mm=0.3)
    assert "check" in p.order and "± 0.3 mm" in p.text("check")


def test_numbers_in_the_parts_frame_move_the_positions_not_the_sizes(clean):
    e = clean
    sd = scan_dir(e)
    v = arrays(e)[0]
    c = centre(e)
    hole = hole_shape(10.04, 0.03)
    hole["center"] = [float(x) for x in c + [3.0, 2.0, 5.0]]
    doc = kf.empty()
    doc["features"] = [record("f-top", "Top", plane_shape(c, [0.0, 0.0, 1.0]), v),
                       record("f-side", "Side", plane_shape(c + [5.0, 0, 0], [1.0, 0.0, 0.0]), v),
                       record("f-bore", "Bore", hole, v, kind="hole")]
    by = {f["id"]: f for f in doc["features"]}
    doc["frame"] = part_frame({"id": "f-top", "name": "Top", "fit": by["f-top"]["shape"]},
                              {"id": "f-side", "name": "Side", "fit": by["f-side"]["shape"]},
                              {"kind": "point", "point": [float(x) for x in c]}).to_dict()
    kf.save(sd, doc)

    def rows(**body) -> dict:
        r = report(e, "features.csv", **body)
        assert r.status_code == 200, r.text
        got = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
        return {x["name"]: x for x in got}

    scan_rows, part_rows = rows(), rows(numbers_in_part_frame=True)
    s, q = scan_rows["Bore"], part_rows["Bore"]
    assert s["frame"] == "scan" and q["frame"] == "part"
    assert s["diameter_mm"] == q["diameter_mm"] and s["diameter_sigma_mm"] == q["diameter_sigma_mm"]
    P_scan = np.array([float(s[k]) for k in ("x_mm", "y_mm", "z_mm")])
    P_part = np.array([float(q[k]) for k in ("x_mm", "y_mm", "z_mm")])
    assert np.allclose(P_scan, hole["center"]) and not np.allclose(P_scan, P_part)
    assert np.linalg.norm(P_part) == pytest.approx(np.linalg.norm(np.asarray(hole["center"]) - c), abs=1e-6)
    _, p = page(e, numbers_in_part_frame=True)
    assert "in the part's frame" in p.text("features") and "frame" in p.order
    _, p = page(e)
    assert "in the scan's own frame" in p.text("features")


def test_pictures_are_embedded_and_bad_ones_refused_or_left_out(clean):
    e = clean
    png = _png(4, 4)
    text, p = page(e, pictures=[{"title": "Front", "data": b64(png)}, {"title": "Not a picture", "data": b64(b"hello there")}])
    assert "data:image/png;base64," + b64(png) in text
    assert "Not shown" in p.text("pictures") and "Not a picture" in p.text("pictures")
    err(report(e, pictures=[{"title": "Broken", "data": "@@@ not base64 @@@"}]), 422, "cannot be read")
    err(report(e, pictures=[{"title": f"View {i}", "data": b64(png)} for i in range(7)]), 422, "6")
    err(report(e, pictures=[{"title": "", "data": b64(png)}]), 422)
    rep = e["srv"].app.state.report
    limit = rep.max_picture_bytes
    try:
        rep.max_picture_bytes = 20
        r = err(report(e, pictures=[{"title": "Large", "data": b64(png)}]), 413, "Large")
        assert "MB" in r["detail"] or "bytes" in r["detail"]
    finally:
        rep.max_picture_bytes = limit
    err(report(e, tolerance_mm=-1), 422)
    err(report(e, "report.pdf"), 404)


def test_the_scan_card_keeps_a_short_calibration_status():
    """FEATURE_PLAN H4: the scan list (``project.json``) keeps the scan's own calibration status in short, for its card:
    the state, the part and the reason; nothing for a scan without one (processed before H3) or with a broken one."""
    from ohs.server.projects import report_summary

    status = {"version": 1, "state": "check", "label": "Check recommended", "part": "lasers", "part_label": "Lasers",
              "reason": "The vertical laser line sits 0.07 mm off the mesh.", "action": {"step": "lasers", "text": "Redo"},
              "then": None, "reasons": [{"state": "check"}], "parts": {"cameras": "valid", "lasers": "check", "imu": None},
              "not_judged": ["imu"], "note": None, "provisional": True, "source": "scan", "signs": {"row_error": {}}}
    out = report_summary({"mode": "markerless", "health": {"version": 1, "row_error": {}}, "health_status": status})
    assert out["health_status"] == {"state": "check", "part": "lasers", "reason": status["reason"]}
    assert "health" not in out  # the signs stay in the report
    assert "health_status" not in report_summary({"mode": "markerless"})
    for bad in (None, "check", {"state": "fine"}, {"part": "lasers"}):
        assert "health_status" not in report_summary({"health_status": bad}), bad


def test_the_reports_health_is_the_scans_own_status(clean):
    """FEATURE_PLAN H4: the report's "Health" is what the scan's own ``health_status`` says (state, part, reason), the
    scan card has its short form, and "Not assessed" stays for a scan without one."""
    e = clean
    path = scan_dir(e) / "result" / "report.json"
    original = path.read_bytes()
    rep = json.loads(original)
    s = rep.get("health_status")
    assert isinstance(s, dict) and s["source"] == "scan", "the tracked scan has its own status (H3)"
    # the scan card: the short form, where the server writes the processed scan's summary
    scan = get(f"{e['url']}/api/projects/{e['pid']}").json()["scans"][0]
    assert scan["result"]["report"]["health_status"] == {"state": s["state"], "part": s["part"], "reason": s["reason"]}
    assert e["studio"].projects.scan_entry(e["pid"], e["sid"])["report"]["health_status"]["state"] == s["state"]
    _, p = page(e)
    cal = p.text("calibration")
    assert "Not assessed" not in cal and s["label"] in cal and s["reason"] in cal
    try:
        given = {**s, "state": "recalibrate", "label": "Recalibration required", "part": "cameras", "part_label": "Cameras",
                 "reason": "Its left and right images disagree by 0.41 px in height."}
        path.write_text(json.dumps({**rep, "health_status": given}), encoding="utf-8")
        _, p = page(e)
        cal = p.text("calibration")
        assert "Recalibration required" in cal and "0.41 px in height" in cal and "provisional" in cal
        assert "0.41 px in height" in p.text("warnings")
        path.write_text(json.dumps({k: v for k, v in rep.items() if k != "health_status"}), encoding="utf-8")
        _, p = page(e)
        assert "Not assessed" in p.text("calibration")
    finally:
        path.write_bytes(original)


def test_the_features_as_csv_and_json(full):
    before = on_disk(project_dir(full))
    r = report(full, "features.csv")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert r.content.startswith(b"\xef\xbb\xbf")  # a byte-order mark: spreadsheets read ± and Ø
    assert "attachment" in r.headers["content-disposition"] and r.headers["content-disposition"].rstrip('"').endswith(".csv")
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0] == list(CSV_COLUMNS)
    body = [dict(zip(CSV_COLUMNS, x)) for x in rows[1:]]
    assert [x["record"] for x in body] == ["feature", "feature", "feature", "relation"]
    bore = next(x for x in body if x["id"] == "f-bore")
    assert bore["check_again"] == "yes" and float(bore["nominal_diameter_mm"]) == 10.5  # the API's view
    j = report(full, "features.json")
    assert j.status_code == 200 and j.headers["content-type"].startswith("application/json")
    doc = json.loads(j.content.decode("utf-8"))
    assert doc["format"] == "OpenHandScan kept features" and len(doc["features"]) == 3 and len(doc["relations"]) == 1
    assert doc["date"] == dt.date.today().isoformat() and doc["scan"] == "Sphere"
    assert [s["name"] for s in doc["sections"]] == [SECTION_HOSTILE]
    assert on_disk(project_dir(full)) == before
