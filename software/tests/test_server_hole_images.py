"""The Hole tool's second answer, the hole read from the scan's camera images (ENGINE_PLAN X15 step 2; APP_API.md
"Hole from the camera images"): ``GET .../hole-images`` says whether it is offered for the scan and why not,
``POST .../hole-images`` reads the hole's entrance rim at a click (``ohs.measure.rim.read_hole``) or refuses in
words. Offered only with ``[process] hole_from_images`` on; never for a merged scan, a scan tracked by its markers, a
scan made at Preview quality, a recording smaller than the reading was measured on, or with the recording, its
calibration or the camera path gone.

Uses the 10-frame sphere scan of ``test_server_process_studio.py`` (its ``env`` fixture), tracked once at Standard
quality: it has no hole, so a click is refused in words; the reading itself is tested on synthetic images in
``test_measure_rim.py``, and an answered reading's shape here with the engine's reading stood in for. Also the frame
provider's additions (``FrameDepth.images``, ``FrameDepth.depth_and_sigma``), with the wall reading's depth
unchanged."""
from __future__ import annotations

import json

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from test_server_process_studio import T, base_url, env, err, get, post, run_job  # noqa: E402,F401


@pytest.fixture(scope="module")
def tracked(env):  # noqa: F811 (the fixture imported above)
    run_job(env, post(base_url(env) + "/process/track", {"quality": "standard"}), "process_track")
    return env


@pytest.fixture()
def hole_on(tracked, monkeypatch):
    """The setting on, the server told it has a graphics card and that this 640 px recording is large enough (the
    route's checks and words are what these tests are about; the reading itself is tested in test_measure_rim.py)."""
    import ohs.server.processing as SP

    monkeypatch.setattr(SP, "images_on_gpu", lambda: True)
    monkeypatch.setattr(SP, "HOLE_IMAGES_MIN_WIDTH", 640)
    cfg = tracked["studio"].cfg
    old = cfg.process.hole_from_images
    cfg.process.hole_from_images = True
    yield tracked
    cfg.process.hole_from_images = old


def a_click(e: dict) -> dict:
    v, V, F, _, _ = e["studio"].processing.mesh_arrays(e["pid"], e["sid"])
    C = V[F].mean(axis=1)
    face = int(np.argmax(C[:, 2]))  # the top of the sphere
    return {"face": face, "point": [float(x) for x in C[face]], "version": v}


URL = "/hole-images"


# --------------------------------------------------------------------------- the provider
def test_the_provider_gives_the_raw_images_as_recorded(tracked):
    from ohs.io.recording import RecordingReader
    from ohs.measure.frame_depth import FrameDepth

    rec = tracked["rec"]
    rd = RecordingReader(rec)
    fd = FrameDepth(rec, rd.calibration(), (150.0, 600.0))
    i = int(rd.indices[3])
    L, R = fd.images(i)
    fr = rd.frame(3)
    assert L.ndim == 2 and R.ndim == 2 and L.shape == (fd.image_size[1], fd.image_size[0])
    assert np.array_equal(L, fr.left) and np.array_equal(R, fr.right)  # grey, unrectified, as recorded
    assert fd.images(i)[0] is L  # kept for the next click


def test_the_provider_gives_the_depth_with_its_sigma_and_leaves_the_walls_depth_as_it_was(tracked):
    from ohs.io.recording import RecordingReader
    from ohs.measure.frame_depth import FrameDepth

    rec = tracked["rec"]
    rd = RecordingReader(rec)
    i = int(rd.indices[4])
    before = FrameDepth(rec, rd.calibration(), (150.0, 600.0)).depth(i)  # the wall reading's depth, alone
    fd = FrameDepth(rec, rd.calibration(), (150.0, 600.0))
    D, S = fd.depth_and_sigma(i)
    assert D.shape == S.shape == before.shape and D.dtype == np.float32
    assert np.array_equal(D, before)  # the same depth as the wall reading's
    ok = D > 0
    assert ok.mean() > 0.2 and np.all(S[ok] > 0) and np.all(S[~ok] == 0)
    assert fd.depth_and_sigma(i)[0] is D  # kept for the next click
    assert np.array_equal(fd.depth(i), before)  # the wall's depth unchanged after it


# --------------------------------------------------------------------------- the route
def test_it_is_on_by_default():
    from ohs.app.config import Config

    assert Config().process.hole_from_images is True


def test_off_in_the_settings_it_is_not_offered_and_says_so(hole_on):
    hole_on["studio"].cfg.process.hole_from_images = False
    r = get(base_url(hole_on) + URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is False and "settings" in body["why"]
    err(post(base_url(hole_on) + URL, a_click(hole_on)), 422, "not offered")


def test_offered_on_a_tracked_scan(hole_on):
    body = get(base_url(hole_on) + URL).json()
    assert body == {"available": True, "why": None}


def test_a_click_without_a_hole_is_refused_in_words_and_gives_no_number(hole_on):
    r = post(base_url(hole_on) + URL, a_click(hole_on))
    e = err(r, 422, "frames show the rim")
    assert "beside the hole" in e["hint"]
    assert e["refused"] is True and "diameter_mm" not in e and e["frames_used"] == 0


def test_without_a_graphics_card_it_is_not_offered_until_measured(hole_on, monkeypatch):
    import ohs.server.processing as SP

    monkeypatch.setattr(SP, "images_on_gpu", lambda: False)
    monkeypatch.setattr(SP, "HOLE_IMAGES_ON_CPU", False)
    body = get(base_url(hole_on) + URL).json()
    assert body["available"] is False and "graphics card" in body["why"]
    err(post(base_url(hole_on) + URL, a_click(hole_on)), 422, "graphics card")


def test_a_small_recording_is_not_offered(hole_on, monkeypatch):
    import ohs.server.processing as SP

    monkeypatch.setattr(SP, "HOLE_IMAGES_MIN_WIDTH", 1280)
    body = get(base_url(hole_on) + URL).json()
    assert body["available"] is False and "1280" in body["why"]


def test_a_scan_tracked_by_its_markers_is_not_offered(hole_on):
    st = hole_on["studio"]
    st.projects.update_scan(hole_on["pid"], hole_on["sid"], mode="markers")
    try:
        body = get(base_url(hole_on) + URL).json()
        assert body["available"] is False and "markers" in body["why"]
        err(post(base_url(hole_on) + URL, a_click(hole_on)), 422, "markers")
    finally:
        st.projects.update_scan(hole_on["pid"], hole_on["sid"], mode="markerless")


def test_a_merged_scan_is_not_offered(hole_on):
    st = hole_on["studio"]
    st.projects.update_scan(hole_on["pid"], hole_on["sid"], merged_from=["a", "b"])
    try:
        body = get(base_url(hole_on) + URL).json()
        assert body["available"] is False and "merged" in body["why"]
    finally:
        st.projects.update_scan(hole_on["pid"], hole_on["sid"], merged_from=None)


def test_a_scan_made_at_preview_quality_is_not_offered(hole_on):
    sp = hole_on["studio"].processing.scan(hole_on["pid"], hole_on["sid"])
    track = sp.state["stages"]["track"]
    old = track.get("quality")
    track["quality"] = "preview"
    try:
        body = get(base_url(hole_on) + URL).json()
        assert body["available"] is False and "Preview" in body["why"]
    finally:
        track["quality"] = old


def test_with_the_recording_gone_it_is_not_offered(hole_on):
    rec = hole_on["studio"].processing.scan(hole_on["pid"], hole_on["sid"]).recording_dir
    (rec / "frames").rename(rec / "frames-away")
    try:
        body = get(base_url(hole_on) + URL).json()
        assert body["available"] is False and "recording" in body["why"]
    finally:
        (rec / "frames-away").rename(rec / "frames")


def test_a_click_on_an_older_mesh_is_refused(hole_on):
    c = a_click(hole_on)
    c["version"] = int(c["version"]) + 7
    err(post(base_url(hole_on) + URL, c), 409, "changed")


def test_a_click_off_the_mesh_is_refused(hole_on):
    c = a_click(hole_on)
    c["face"] = 10 ** 8
    err(post(base_url(hole_on) + URL, c), 422, "not on the current mesh")


def _reading(**kw):
    from ohs.measure.rim import RimReading

    base = dict(refused=None, diameter_mm=25.0213, pm_mm=0.1108, centre=np.array([1.0, 2.0, 3.0]),
                normal=np.array([0.0, 0.0, 1.0]), frames_used=18, frames=20, covered_deg=301.4, seconds=11.2)
    base.update(kw)
    return RimReading(**base)


def test_an_answered_reading_says_which_diameter_in_the_engines_words(hole_on, monkeypatch):
    """The engine's reading stood in for (its numbers are test_measure_rim.py's): what the route sends, and what it
    hands the engine (the click, the face's normal from the mesh, the scan's camera path, no path warning here)."""
    import ohs.measure.rim as RIM

    seen = {}

    def fake(click, normal, **kw):
        seen.update(click=np.asarray(click), normal=np.asarray(normal), **kw)
        return _reading()

    monkeypatch.setattr(RIM, "read_hole", fake)
    c = a_click(hole_on)
    r = post(base_url(hole_on) + URL, c)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["available"] is True and b["version"] == c["version"]
    assert b["diameter_mm"] == pytest.approx(25.0213) and b["pm_mm"] == pytest.approx(0.1108)
    assert b["centre"] == [1.0, 2.0, 3.0] and b["normal"] == [0.0, 0.0, 1.0]
    assert b["frames_used"] == 18 and b["frames_tried"] == 20 and b["cover_deg"] == pytest.approx(301.4)
    assert b["warnings"] == [] and b["seconds"] >= 0
    assert b["words"][0] == "Ø 25.02 mm ± 0.11 from the camera images (the rim where the face ends, read in 18 frames)."
    assert any("not the bore" in w for w in b["words"])
    assert not any("position" in w for w in b["words"])
    # what the engine was handed
    assert np.allclose(seen["click"], c["point"]) and abs(np.linalg.norm(seen["normal"]) - 1.0) < 1e-9
    from ohs.server.processing import face_normal_at

    _, V, F, _, _ = hole_on["studio"].processing.mesh_arrays(hole_on["pid"], hole_on["sid"])
    assert np.allclose(seen["normal"], face_normal_at(V, F, c["face"], np.asarray(c["point"])))  # the mesh's, there
    assert seen["path_warns"] is False and len(seen["poses"]) >= 5 and callable(seen["images"])
    assert callable(seen["depth"])


def test_a_path_warning_says_the_position_may_be_off_not_the_diameter(hole_on, monkeypatch):
    import ohs.measure.rim as RIM

    sp = hole_on["studio"].processing.scan(hole_on["pid"], hole_on["sid"])
    rep_path = sp.result_dir / "report.json"
    old = rep_path.read_text(encoding="utf-8")
    rep = json.loads(old)
    rep["path_check"] = {"state": "warn", "jump_mm": 4.0, "frame": 5}
    rep_path.write_text(json.dumps(rep), encoding="utf-8")
    seen = {}

    def fake(click, normal, **kw):
        seen.update(kw)
        return _reading(warnings=["the scan's camera path check warns"])

    monkeypatch.setattr(RIM, "read_hole", fake)
    try:
        b = post(base_url(hole_on) + URL, a_click(hole_on)).json()
    finally:
        rep_path.write_text(old, encoding="utf-8")
    assert seen["path_warns"] is True
    assert any("position may be off" in w and "diameter does not depend" in w for w in b["words"])
    assert b["warnings"] and b["diameter_mm"] == pytest.approx(25.0213)


def test_a_refusal_shows_the_engines_words_and_what_to_do(hole_on, monkeypatch):
    import ohs.measure.rim as RIM

    monkeypatch.setattr(RIM, "read_hole", lambda click, normal, **kw: _reading(
        refused="the rim read in the images is 9 mm from the click: it is another hole's", frames_used=14))
    e = err(post(base_url(hole_on) + URL, a_click(hole_on)), 422, "another hole")
    assert "hole you mean" in e["hint"] and "diameter_mm" not in e and e["frames_used"] == 14


def test_the_normal_at_a_click_beside_a_hole_is_the_faces_not_the_bores():
    """A flat face (z = 0, normal +z) with a bore's wall (normal along x) 1 mm from the click: the face's normal."""
    from ohs.server.processing import face_normal_at

    g = np.arange(-5.0, 5.01, 0.5)
    X, Y = np.meshgrid(g, g)
    V = np.column_stack([X.ravel(), Y.ravel(), np.zeros(X.size)])
    n = len(g)
    F = [[r * n + c, r * n + c + 1, (r + 1) * n + c] for r in range(n - 1) for c in range(n - 1)]
    k = len(V)
    V = np.vstack([V, [[1.0, -3.0, 0.0], [1.0, 3.0, 0.0], [1.0, 0.0, -3.0]]])  # the bore's wall, x = 1
    F = np.asarray(F + [[k, k + 1, k + 2]] * 20)
    nrm = face_normal_at(V, F, 0, np.array([0.0, 0.0, 0.0]))
    assert np.allclose(nrm, [0.0, 0.0, 1.0])
    assert np.allclose(face_normal_at(V, F, len(F) - 1, np.array([5.0, 0.0, -20.0])), [-1.0, 0.0, 0.0])  # alone
