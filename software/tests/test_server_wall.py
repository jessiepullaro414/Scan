"""The viewer's wall-thickness reading through the server (ENGINE_PLAN X16; APP_API.md "Wall thickness"):
``GET .../wall`` says whether it is offered for the scan and why not, ``POST .../wall`` reads it at a click or refuses
in words. Offered only with ``[process] wall_reading`` on, never for a scan tracked by its markers.

Uses the 10-frame sphere scan of ``test_server_process_studio.py`` (its ``env`` fixture), tracked once at preview
quality: it has no thin wall, so a click is refused in words; the reading itself is tested on a synthetic wall in
``test_measure_wall.py``."""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from ohs.measure.wall import MARKERS_WORDS  # noqa: E402
from test_server_process_studio import T, base_url, env, err, get, post, run_job  # noqa: E402,F401


@pytest.fixture(scope="module")
def tracked(env):  # noqa: F811 (the fixture imported above)
    run_job(env, post(base_url(env) + "/process/track", {"quality": "preview"}), "process_track")
    return env


@pytest.fixture()
def wall_on(tracked, monkeypatch):
    """The setting on, and the server told it has a graphics card (on the processor path too: the route's checks and
    words are what these tests are about; the reading itself is tested in test_measure_wall.py)."""
    import ohs.server.processing as SP

    monkeypatch.setattr(SP, "images_on_gpu", lambda: True)
    cfg = tracked["studio"].cfg
    old = cfg.process.wall_reading
    cfg.process.wall_reading = True
    yield tracked
    cfg.process.wall_reading = old


def test_it_is_on_by_default():
    from ohs.app.config import Config

    assert Config().process.wall_reading is True


def a_click(e: dict) -> dict:
    v, V, F, _, _ = e["studio"].processing.mesh_arrays(e["pid"], e["sid"])
    C = V[F].mean(axis=1)
    face = int(np.argmax(C[:, 2]))  # the top of the sphere
    return {"face": face, "point": [float(x) for x in C[face]], "version": v}


def test_off_in_the_settings_it_is_not_offered_and_says_so(tracked):
    tracked["studio"].cfg.process.wall_reading = False
    r = get(base_url(tracked) + "/wall")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is False and "settings" in body["why"]
    err(post(base_url(tracked) + "/wall", a_click(tracked)), 422, "not offered")


def test_a_click_without_a_thin_wall_is_refused_in_words(wall_on):
    body = get(base_url(wall_on) + "/wall").json()
    assert body["available"] is True and body["why"] is None
    e = err(post(base_url(wall_on) + "/wall", a_click(wall_on)), 422, "no wall seen from both sides")
    assert "thin wall" in e["hint"].lower()


def test_without_a_graphics_card_it_is_not_offered(wall_on, monkeypatch):
    import ohs.server.processing as SP

    monkeypatch.setattr(SP, "images_on_gpu", lambda: False)
    body = get(base_url(wall_on) + "/wall").json()
    assert body["available"] is False and "graphics card" in body["why"]
    err(post(base_url(wall_on) + "/wall", a_click(wall_on)), 422, "graphics card")


def test_a_scan_tracked_by_its_markers_is_not_offered(wall_on):
    st = wall_on["studio"]
    st.projects.update_scan(wall_on["pid"], wall_on["sid"], mode="markers")
    try:
        body = get(base_url(wall_on) + "/wall").json()
        assert body["available"] is False and body["why"] == MARKERS_WORDS
        err(post(base_url(wall_on) + "/wall", a_click(wall_on)), 422, "markers")
    finally:
        st.projects.update_scan(wall_on["pid"], wall_on["sid"], mode="markerless")


def test_a_click_on_an_older_mesh_is_refused(wall_on):
    c = a_click(wall_on)
    c["version"] = int(c["version"]) + 7
    err(post(base_url(wall_on) + "/wall", c), 409, "changed")


def test_the_frame_depth_provider_reads_a_recorded_frame(tracked):
    """``ohs.measure.frame_depth.FrameDepth``, the matcher's depth of a recorded frame on demand (the wall reading's
    provider, for any reading at a click): the rectified left camera's size and intrinsics, depth on a good share of
    the sphere scan's pixels, points facing the camera."""
    from ohs.io.recording import RecordingReader
    from ohs.measure.frame_depth import FrameDepth

    rec = tracked["rec"]
    fd = FrameDepth(rec, RecordingReader(rec).calibration(), (150.0, 600.0))
    i = int(RecordingReader(rec).indices[3])
    D = fd.depth(i)
    assert D.shape == (fd.image_size[1], fd.image_size[0]) and fd.K.shape == (3, 3)
    assert (D > 0).mean() > 0.2 and np.nanmax(D) <= 600.0
    P, N = fd.points(i, np.eye(4))
    assert len(P) > 1000 and np.median(np.einsum("ij,ij->i", N, P)) < 0  # normals face the camera at the origin
