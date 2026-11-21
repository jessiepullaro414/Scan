"""Renaming a scan, moving it to the trash, the trash itself, restoring and emptying it (UX_PLAN U7 with U50;
APP_API.md "Rename a scan, the trash").

The data safety rules: a scan goes to ``<projects>/.trash`` as a project does (never deleted there); a busy scan
(recording, being processed, aligned or merged) is refused in a plain sentence; a scan that other scans were merged
from or continue says so first and goes only when the page says it may; restore puts it back where it was, and a
clash of name or id is renamed, never overwritten; nothing is deleted for good without ``confirm`` and the list of
the items the page showed. Ids only: a path from the page is never used.
"""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402

T = 30


@pytest.fixture()
def env(tmp_path):
    cfg = Config()
    cfg.paths.calibration = str(tmp_path / "no-calibration.json")
    app = create_app(cfg, projects_dir=tmp_path / "projects", demo_dir=tmp_path / "demo", static_dir=tmp_path / "static",
                     data_dir=tmp_path / "data", config_path=tmp_path / "config.toml")
    with ServerThread(app) as srv:
        yield {"url": srv.url, "root": tmp_path / "projects", "studio": app.state.studio}


def _project(env, name="Door bracket") -> str:
    return requests.post(env["url"] + "/api/projects", json={"name": name}, timeout=T).json()["id"]


def _scan(env, pid, name=None, **fields) -> str:
    st = env["studio"]
    scan, rec = st.projects.add_scan(pid, name, "markerless")
    rec.mkdir(parents=True)
    (rec / "frames.csv").write_text("index,t\n0,0.0\n", encoding="utf-8")
    st.projects.update_scan(pid, scan["id"], status="recorded", n_frames=1, duration_s=0.1, **fields)
    return scan["id"]


def _scans(env, pid) -> list[dict]:
    return requests.get(f"{env['url']}/api/projects/{pid}", timeout=T).json()["scans"]


def _plain(r, status: int) -> dict:
    assert r.status_code == status, r.text
    body = r.json()
    for k in ("detail", "hint"):
        assert body.get(k) and body[k][0].isupper() and body[k].rstrip("\"”").endswith((".", "?", "!")), body
        assert "scan-" not in body[k] and "/api" not in body[k] and ".trash" not in body[k], body
    return body


# --------------------------------------------------------------------------- rename (U7, U50)
def test_rename_a_scan(env):
    pid = _project(env)
    sid = _scan(env, pid)
    assert _scans(env, pid)[0]["name"] == "Scan 1"
    r = requests.patch(f"{env['url']}/api/projects/{pid}/scans/{sid}", json={"name": "  Top,  front side "}, timeout=T)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Top, front side" and r.json()["id"] == sid
    assert _scans(env, pid)[0]["name"] == "Top, front side"
    _plain(requests.patch(f"{env['url']}/api/projects/{pid}/scans/{sid}", json={"name": "  "}, timeout=T), 400)
    _plain(requests.patch(f"{env['url']}/api/projects/{pid}/scans/scan-nope", json={"name": "x"}, timeout=T), 404)


def test_a_rename_to_a_name_another_scan_has_is_refused_with_a_suggestion(env):
    """UX_PLAN R21: two scans of one project never share a name; the refusal offers the next free one."""
    pid = _project(env)
    a, b = _scan(env, pid, "Bracket"), _scan(env, pid, "Bracket (2)")
    c = _scan(env, pid, "Other")
    body = _plain(requests.patch(f"{env['url']}/api/projects/{pid}/scans/{c}", json={"name": " bracket "}, timeout=T), 409)
    assert body["detail"] == "Another scan of this project is already called “bracket”."
    assert body["suggestion"] == "bracket (3)" and "“bracket (3)”" in body["hint"]
    assert [s["name"] for s in _scans(env, pid)] == ["Bracket", "Bracket (2)", "Other"]  # nothing changed
    r = requests.patch(f"{env['url']}/api/projects/{pid}/scans/{a}", json={"name": "Bracket"}, timeout=T)
    assert r.status_code == 200  # its own name
    assert b


def test_a_merges_default_name_takes_the_next_free_one():
    from ohs.server.projects import free_name

    assert free_name("Top + Side", ["Top", "Side"]) == "Top + Side"
    assert free_name("Top + Side", ["Top + Side", "top + side (2)"]) == "Top + Side (3)"


def test_a_new_scan_takes_the_next_free_number(env):
    """U50: after "Scan 2" went to the trash, the next scan is not a second "Scan 3"."""
    pid = _project(env)
    _scan(env, pid)
    b = _scan(env, pid)
    _scan(env, pid)
    assert [s["name"] for s in _scans(env, pid)] == ["Scan 1", "Scan 2", "Scan 3"]
    assert requests.delete(f"{env['url']}/api/projects/{pid}/scans/{b}", timeout=T).status_code == 200
    _scan(env, pid)
    assert sorted(s["name"] for s in _scans(env, pid)) == ["Scan 1", "Scan 3", "Scan 4"]


# --------------------------------------------------------------------------- to the trash and back
def test_move_a_scan_to_the_trash_and_restore_it(env):
    pid = _project(env)
    sid = _scan(env, pid, "Underside")
    folder = env["root"] / pid / "scans" / sid
    (folder / "recording" / "data.bin").write_bytes(b"x" * 1000)
    r = requests.delete(f"{env['url']}/api/projects/{pid}/scans/{sid}", timeout=T)
    assert r.status_code == 200, r.text
    item = r.json()["item"]
    assert item["kind"] == "scan" and item["name"] == "Underside" and item["project_id"] == pid
    assert not folder.exists() and _scans(env, pid) == []
    moved = env["root"] / ".trash" / item["id"]
    assert (moved / "recording" / "data.bin").read_bytes() == b"x" * 1000  # never deleted: moved

    trash = requests.get(env["url"] + "/api/trash", timeout=T).json()
    assert [i["id"] for i in trash["items"]] == [item["id"]]
    assert trash["items"][0]["project_name"] == "Door bracket" and trash["items"][0]["bytes"] >= 1000
    disk = requests.get(f"{env['url']}/api/projects/{pid}/disk", timeout=T).json()
    assert disk["trash"]["scans"] == 1 and disk["trash"]["projects"] == 0

    r = requests.post(f"{env['url']}/api/trash/{item['id']}/restore", timeout=T)
    assert r.status_code == 200, r.text
    back = r.json()
    assert back == {**back, "kind": "scan", "project_id": pid, "scan_id": sid, "name": "Underside", "renamed": False}
    assert (folder / "recording" / "data.bin").is_file()
    assert not (folder / "trashed-scan.json").exists()
    assert [s["id"] for s in _scans(env, pid)] == [sid]
    assert requests.get(env["url"] + "/api/trash", timeout=T).json()["items"] == []


def test_restore_with_a_name_clash_renames_and_never_overwrites(env):
    pid = _project(env)
    sid = _scan(env, pid, "Top")
    item = requests.delete(f"{env['url']}/api/projects/{pid}/scans/{sid}", timeout=T).json()["item"]
    sid2 = _scan(env, pid, "Top")  # a new scan of the same name meanwhile
    # and a folder with the old id (made outside the app): it stays as it is
    clash = env["root"] / pid / "scans" / sid
    clash.mkdir(parents=True, exist_ok=True)  # (the new scan may have taken the same id in the same second)
    (clash / "keep.txt").write_text("mine", encoding="utf-8")
    back = requests.post(f"{env['url']}/api/trash/{item['id']}/restore", timeout=T).json()
    assert back["renamed"] is True and back["name"] == "Top (restored)" and back["scan_id"] != sid
    assert (clash / "keep.txt").read_text(encoding="utf-8") == "mine"
    names = sorted(s["name"] for s in _scans(env, pid))
    assert names == ["Top", "Top (restored)"] and sid2 in {s["id"] for s in _scans(env, pid)}


def test_a_scan_whose_project_is_gone_is_not_restored(env):
    pid = _project(env)
    sid = _scan(env, pid)
    item = requests.delete(f"{env['url']}/api/projects/{pid}/scans/{sid}", timeout=T).json()["item"]
    assert requests.delete(f"{env['url']}/api/projects/{pid}", timeout=T).status_code == 204
    trash = requests.get(env["url"] + "/api/trash", timeout=T).json()["items"]
    assert {i["kind"] for i in trash} == {"scan", "project"}
    scan_item = next(i for i in trash if i["kind"] == "scan")
    assert scan_item["project_exists"] is False
    body = _plain(requests.post(f"{env['url']}/api/trash/{item['id']}/restore", timeout=T), 409)
    assert "project" in body["detail"].lower()
    # restore the project first, then the scan
    proj = next(i for i in trash if i["kind"] == "project")
    back = requests.post(f"{env['url']}/api/trash/{proj['id']}/restore", timeout=T).json()
    assert back["kind"] == "project" and back["project_id"] == pid and back["renamed"] is False
    assert requests.post(f"{env['url']}/api/trash/{item['id']}/restore", timeout=T).status_code == 200
    assert len(_scans(env, pid)) == 1


def test_restore_a_project_whose_id_was_taken(env):
    pid = _project(env, "Fender")
    proj = requests.delete(f"{env['url']}/api/projects/{pid}", timeout=T)
    assert proj.status_code == 204
    (env["root"] / pid).mkdir()  # a folder of that name came back meanwhile
    (env["root"] / pid / "x.txt").write_text("mine", encoding="utf-8")
    _project(env, "Fender")
    item = requests.get(env["url"] + "/api/trash", timeout=T).json()["items"][0]
    back = requests.post(f"{env['url']}/api/trash/{item['id']}/restore", timeout=T).json()
    assert back["project_id"] != pid and back["renamed"] is True and back["name"] == "Fender (restored)"
    assert (env["root"] / pid / "x.txt").read_text(encoding="utf-8") == "mine"
    got = requests.get(f"{env['url']}/api/projects/{back['project_id']}", timeout=T).json()
    assert got["id"] == back["project_id"] and got["name"] == "Fender (restored)"


# --------------------------------------------------------------------------- refusals
def test_a_busy_scan_is_refused(env):
    st = env["studio"]
    pid = _project(env)
    sid = _scan(env, pid)
    gate = threading.Event()
    job = st.jobs.submit("process_track", lambda ctx: gate.wait(20) and None, meta={"project_id": pid, "scan_id": sid})
    try:
        body = _plain(requests.delete(f"{env['url']}/api/projects/{pid}/scans/{sid}", timeout=T), 409)
        assert "busy" in body["detail"].lower()
    finally:
        gate.set()
    deadline = time.monotonic() + 20
    while job.active and time.monotonic() < deadline:
        time.sleep(0.05)
    for meta in ({"project_id": pid, "scans": ["a", sid]}, {"project_id": pid, "target": sid, "source": "b"}):
        gate2 = threading.Event()
        j = st.jobs.submit("merge", lambda ctx, g=gate2: g.wait(20) and None, meta=meta)
        try:
            _plain(requests.delete(f"{env['url']}/api/projects/{pid}/scans/{sid}", timeout=T), 409)
        finally:
            gate2.set()
        deadline = time.monotonic() + 20
        while j.active and time.monotonic() < deadline:
            time.sleep(0.05)
    st.session = SimpleNamespace(active=True, project_id=pid, scan_id=sid, meta={})
    try:
        body = _plain(requests.delete(f"{env['url']}/api/projects/{pid}/scans/{sid}", timeout=T), 409)
        assert "recording" in body["detail"].lower()
    finally:
        st.session = None
    assert (env["root"] / pid / "scans" / sid).is_dir()  # nothing moved


def test_a_scan_others_depend_on_says_so_first(env):
    pid = _project(env)
    first = _scan(env, pid, "Top")
    _scan(env, pid, "Top, second pass", continues={"scan_id": first})
    _scan(env, pid, "Top merged", merged_from=[{"id": first, "name": "Top"}, {"id": "other", "name": "B"}])
    body = _plain(requests.delete(f"{env['url']}/api/projects/{pid}/scans/{first}", timeout=T), 409)
    assert "“Top, second pass”" in body["detail"] and "“Top merged”" in body["detail"]
    assert (env["root"] / pid / "scans" / first).is_dir()
    r = requests.delete(f"{env['url']}/api/projects/{pid}/scans/{first}", params={"dependents_ok": "true"}, timeout=T)
    assert r.status_code == 200, r.text
    assert len(_scans(env, pid)) == 2  # the others stay


def test_ids_only_never_a_path(env):
    pid = _project(env)
    for bad in ("..", "..%2F..%2Fx", "a..b", "%2E%2E", ".trash", "C:%5CWindows"):
        r = requests.post(f"{env['url']}/api/trash/{bad}/restore", timeout=T)
        assert r.status_code in (404, 405), (bad, r.status_code)
        r = requests.delete(f"{env['url']}/api/projects/{pid}/scans/{bad}", timeout=T)
        assert r.status_code in (404, 405), (bad, r.status_code)
    r = requests.post(env["url"] + "/api/trash/empty", json={"confirm": True, "items": ["..", "../" + pid, pid]}, timeout=T)
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == [] and (env["root"] / pid / "project.json").is_file()


# --------------------------------------------------------------------------- empty the trash
def test_empty_the_trash_needs_confirm_and_the_listed_items(env):
    pid = _project(env)
    a, b = _scan(env, pid, "A"), _scan(env, pid, "B")
    ia = requests.delete(f"{env['url']}/api/projects/{pid}/scans/{a}", timeout=T).json()["item"]
    _plain(requests.post(env["url"] + "/api/trash/empty", json={"items": [ia["id"]]}, timeout=T), 400)
    _plain(requests.post(env["url"] + "/api/trash/empty", json={"confirm": True}, timeout=T), 400)
    ib = requests.delete(f"{env['url']}/api/projects/{pid}/scans/{b}", timeout=T).json()["item"]  # after the list
    r = requests.post(env["url"] + "/api/trash/empty", json={"confirm": True, "items": [ia["id"]]}, timeout=T)
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == [ia["id"]] and r.json()["bytes"] >= 0
    left = requests.get(env["url"] + "/api/trash", timeout=T).json()["items"]
    assert [i["id"] for i in left] == [ib["id"]]  # moved after the page's list: kept
    assert not (env["root"] / ".trash" / ia["id"]).exists()


def test_an_unknown_folder_in_the_trash_is_left_alone(env):
    (env["root"] / ".trash" / "my-notes").mkdir(parents=True)
    (env["root"] / ".trash" / "my-notes" / "n.txt").write_text("x", encoding="utf-8")
    assert requests.get(env["url"] + "/api/trash", timeout=T).json()["items"] == []
    r = requests.post(env["url"] + "/api/trash/empty", json={"confirm": True, "items": ["my-notes"]}, timeout=T)
    assert r.json()["removed"] == [] and (env["root"] / ".trash" / "my-notes" / "n.txt").is_file()
    assert json.loads(json.dumps(r.json()))  # plain JSON
