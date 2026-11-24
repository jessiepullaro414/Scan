"""tools/scorecard.py: every run carries its identity, and a cached result is used again only when it matches.

The identity: the engine's code (all of ``ohs``, the server's code included), the scorecard's own job code, the
recording and its calibration, the settings, the chain, the pose source, the libraries and the compute device.
A cached result made by other code or inputs, or with no identity at all (everything cached before 2026-11-24),
is run again, or, only with ``history``, used as it is and marked as an old result. Baselines keep each run's
identity, and a compare names both sides' identities. Temporary folders, made-up results, a fake job and a tiny
fake source tree only: nothing is rendered or processed (numpy only)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

SOFTWARE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE))

from tools import scorecard as sc  # noqa: E402

TINY = sc.SceneSpec("tiny-s0", "tiny", "sphere", 0, 4, {"maker": "scan", "scene": "sphere_on_plane", "fps": 30.0})
PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"


def _recording(cache: Path, spec=TINY) -> Path:
    """A complete recording as the renderer leaves it (frames.csv, whole PNGs, calibration, meta, the marker)."""
    path = sc.rec_path(cache, spec)
    (path / "frames").mkdir(parents=True, exist_ok=True)
    rows = ["index,t_host,file"]
    for i in range(spec.frames):
        (path / "frames" / f"{i:06d}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([i]) * 64 + PNG_END)
        rows.append(f"{i},{100 + i / 30:.6f},{i:06d}.png")
    (path / "frames.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (path / "calibration.json").write_text('{"baseline": 60.0}', encoding="utf-8")
    (path / "meta.json").write_text('{"scene": "sphere_on_plane"}', encoding="utf-8")
    (path / "done").write_text('{"finished": "2026-11-24T10:00"}', encoding="utf-8")
    return path


class FakeJob:
    """A job's process: writes a result (without an identity, as a job of any age would) and counts the starts."""

    started: list = []

    def __init__(self, cmd, **kw):
        out = Path(cmd[cmd.index("--out") + 1])
        FakeJob.started.append((cmd[cmd.index("--rec") + 1], cmd[cmd.index("--poses") + 1]))
        sc.write_json(out / "result.json", {"metrics": {"part_med": 0.1 * len(FakeJob.started)}, "info": {}})
        self.returncode, self.pid = 0, 0

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def fake_job(monkeypatch):
    FakeJob.started = []
    monkeypatch.setattr(sc.subprocess, "Popen", FakeJob)
    return FakeJob


def _run(cache: Path, **kw) -> dict:
    return sc.run_job(cache, TINY, "tracked", "cpu", {}, sc.run_dir(cache, "cpu", {}, TINY, "tracked"), **kw)


# ----------------------------------------------------------------------------------- the three claims, as guards
def test_a_result_made_by_other_code_is_not_returned_as_current(tmp_path):
    """The review's probe: a result marked as made by other code, in a folder whose recording is not even there.
    It was returned as current; now it is not (the job would run again: here it cannot, the recording is missing),
    and with ``history`` it comes back marked as an old result."""
    out = tmp_path / "out"
    out.mkdir()
    stale = {"metrics": {"part_med": 0.123}, "info": {"engine_code": "old-code"}, "error": None}
    (out / "result.json").write_text(json.dumps(stale), encoding="utf-8")
    try:
        got = sc.run_job(tmp_path / "no-cache", TINY, "tracked", "cpu", {}, out)
    except RuntimeError as e:
        got = None
        assert "not complete" in str(e)
    assert got is None, f"an old result was returned as current: {got}"
    old = sc.run_job(tmp_path / "no-cache", TINY, "tracked", "cpu", {}, out, history=True)
    assert old["metrics"]["part_med"] == 0.123 and old["history"]["why"]
    assert json.loads((out / "result.json").read_text(encoding="utf-8")) == stale  # never rewritten


def test_a_change_in_the_servers_processing_changes_the_fingerprint(tmp_path, monkeypatch):
    fake = tmp_path / "fake"
    (fake / "ohs" / "server" / "static").mkdir(parents=True)
    (fake / "ohs" / "app").mkdir()
    (fake / "ohs" / "app" / "pipeline.py").write_text("x = 1\n", encoding="utf-8")
    sf = fake / "ohs" / "server" / "processing.py"
    sf.write_text("default_recipe = 1\n", encoding="utf-8")
    (fake / "ohs" / "server" / "static" / "index.js").write_text("a\n", encoding="utf-8")
    monkeypatch.setattr(sc, "SOFTWARE", fake)
    h1 = sc.engine_fingerprint()
    sf.write_text("default_recipe = 2\n", encoding="utf-8")
    h2 = sc.engine_fingerprint()
    assert h1 != h2, "a change in server/processing.py left the engine's fingerprint as it was"
    (fake / "ohs" / "server" / "static" / "index.js").write_text("b\n", encoding="utf-8")  # the screens' built files
    sf.write_bytes(b"default_recipe = 2\r\n")  # the same text with Windows line ends
    assert sc.engine_fingerprint() == h2
    (fake / "ohs" / "app" / "__pycache__").mkdir()
    (fake / "ohs" / "app" / "__pycache__" / "stray.py").write_text("y = 1\n", encoding="utf-8")
    assert sc.engine_fingerprint() == h2


def test_a_saved_baseline_keeps_each_runs_identity(tmp_path):
    ident = {"schema": 1, "engine_code": "abc123", "tool_code": "def456", "device": "cpu"}
    res = {"tiny-s0": {"tracked": {"metrics": {"part_med": 0.1}, "info": {}, "identity": ident}}}
    p = sc.save_baseline(tmp_path, "b", "cpu", {}, res, [TINY])
    body = json.loads(p.read_text(encoding="utf-8"))
    assert body["results"]["tiny-s0"]["tracked"].get("identity") == ident, "the baseline dropped the run's identity"
    assert body["identity_schema"] == sc.IDENTITY_SCHEMA


# ----------------------------------------------------------------------------------- the new behaviour
def test_an_unchanged_tree_reuses_its_cache(tmp_path, fake_job):
    _recording(tmp_path)
    a = _run(tmp_path)
    b = _run(tmp_path)
    assert fake_job.started == [("tiny-s0", "tracked")]  # run once, then used again
    assert a == b and a["identity"] == sc.run_identity(tmp_path, TINY, "tracked", "cpu", {})
    for k in ("engine_code", "tool_code", "recording", "calibration", "params", "chain", "poses", "libraries", "device",
              "python", "compute"):
        assert k in a["identity"], k
    assert a["identity"]["device"] == "cpu" and a["identity"]["chain"] == "frame"
    assert a["identity"]["engine_code"] == sc.engine_fingerprint() and a["identity"]["tool_code"] == sc.tool_fingerprint()
    # the job itself got the identity too (a result an orphaned job finishes carries it)
    assert json.loads((sc.run_dir(tmp_path, "cpu", {}, TINY, "tracked") / "identity.json").read_text(encoding="utf-8")) \
        == a["identity"]


def test_force_runs_again(tmp_path, fake_job):
    _recording(tmp_path)
    _run(tmp_path)
    _run(tmp_path, force=True)
    assert fake_job.started == [("tiny-s0", "tracked")] * 2


def test_other_engine_code_runs_again(tmp_path, fake_job, monkeypatch):
    _recording(tmp_path)
    _run(tmp_path)
    monkeypatch.setattr(sc, "engine_fingerprint", lambda: "another-engine")
    r = _run(tmp_path)
    assert len(fake_job.started) == 2 and r["identity"]["engine_code"] == "another-engine"
    _run(tmp_path)
    assert len(fake_job.started) == 2  # and that one is used again


def test_other_scoring_code_runs_again(tmp_path, fake_job, monkeypatch):
    _recording(tmp_path)
    _run(tmp_path)
    monkeypatch.setattr(sc, "tool_fingerprint", lambda: "another-judge")
    _run(tmp_path)
    assert len(fake_job.started) == 2


def test_a_changed_recording_or_calibration_runs_again(tmp_path, fake_job):
    rec = _recording(tmp_path)
    _run(tmp_path)
    (rec / "calibration.json").write_text('{"baseline": 60.5}', encoding="utf-8")
    r = _run(tmp_path)
    assert len(fake_job.started) == 2
    (rec / "frames" / "000002.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"z" * 70 + PNG_END)  # another size
    _run(tmp_path)
    assert len(fake_job.started) == 3
    (rec / "done").write_text('{"finished": "2026-11-25T08:00"}', encoding="utf-8")  # the marker's time: not the content
    assert _run(tmp_path)["identity"]["calibration"] == r["identity"]["calibration"]
    assert len(fake_job.started) == 3


def test_other_libraries_or_device_run_again(tmp_path, fake_job, monkeypatch):
    _recording(tmp_path)
    _run(tmp_path)
    monkeypatch.setattr(sc, "library_versions", lambda: {"numpy": "0.0.1"})
    _run(tmp_path)
    assert len(fake_job.started) == 2
    monkeypatch.setattr(sc, "device_name", lambda compute: "Another GPU")
    _run(tmp_path)
    assert len(fake_job.started) == 3


def test_straightens_settings_still_share_the_true_pose_run(tmp_path, fake_job):
    """The app chain's true-pose run is not straightened: a setting of Straighten alone keeps its result (the folder
    is shared, and so is the identity)."""
    _recording(tmp_path)
    d = sc.run_dir(tmp_path, "cpu", {}, TINY, "true", "app")
    sc.run_job(tmp_path, TINY, "true", "cpu", {}, d, chain="app")
    p = {"curve.enabled": False}
    assert sc.run_dir(tmp_path, "cpu", p, TINY, "true", "app") == d
    sc.run_job(tmp_path, TINY, "true", "cpu", p, d, chain="app")
    assert len(fake_job.started) == 1
    assert sc.run_identity(tmp_path, TINY, "tracked", "cpu", p, "app")["params"] == p


def test_a_result_without_identity_is_history(tmp_path, fake_job):
    """Everything cached before 2026-11-24 has no identity: it is run again, or used as an old result with history."""
    _recording(tmp_path)
    out = sc.run_dir(tmp_path, "cpu", {}, TINY, "tracked")
    out.mkdir(parents=True)
    sc.write_json(out / "result.json", {"metrics": {"part_med": 0.7}, "info": {}})
    before = (out / "result.json").read_bytes()
    old = _run(tmp_path, history=True)
    assert fake_job.started == [] and old["metrics"]["part_med"] == 0.7
    assert old["history"]["why"] == ["no identity (made before 2026-11-24)"]
    assert (out / "result.json").read_bytes() == before  # an old result is never rewritten by reading it
    new = _run(tmp_path)
    assert fake_job.started == [("tiny-s0", "tracked")] and "history" not in new and new["identity"]


def test_a_rerun_keeps_the_old_result_as_history(tmp_path, fake_job):
    """Run again over an old result: the old result.json and its kept mesh folder are moved into ``old/``, never
    deleted; the table of the cache marks an old result as such."""
    _recording(tmp_path)
    out = sc.run_dir(tmp_path, "cpu", {}, TINY, "tracked")
    (out / "proc").mkdir(parents=True)
    (out / "proc" / "mesh.ply").write_bytes(b"ply old")
    sc.write_json(out / "result.json", {"metrics": {"part_med": 0.7}, "info": {}})
    before = (out / "result.json").read_bytes()
    marked = sc.load_results(tmp_path, "cpu", {}, [TINY])[TINY.name]["tracked"]
    assert marked["history"]["why"] == [sc.NO_IDENTITY]
    assert "- HISTORY: 1 result(s) were made by other code or inputs" in sc.history_lines({TINY.name: {"tracked": marked}})[0]
    _run(tmp_path)
    kept = list((out / "old").iterdir())
    assert len(kept) == 1 and (kept[0] / "result.json").read_bytes() == before
    assert (kept[0] / "proc" / "mesh.ply").read_bytes() == b"ply old"
    assert "history" not in sc.load_results(tmp_path, "cpu", {}, [TINY])[TINY.name]["tracked"]


def test_identity_mismatch_names_what_differs():
    a = {"schema": 1, "engine_code": "a", "tool_code": "t", "recording": "r", "device": "cpu"}
    assert sc.identity_mismatch(None, a) == ["no identity (made before 2026-11-24)"]
    assert sc.identity_mismatch(dict(a), a) == []
    b = dict(a, engine_code="b", recording="q")
    assert sc.identity_mismatch(b, a) == ["the engine's code", "the recording"]


def test_the_job_code_imports_only_the_tools_it_hashes():
    """The scorecard's job code (before "running jobs") and the study tools it hashes import no other tool module
    (memory_peaks measures memory only, a rough figure that is never judged)."""
    text = (SOFTWARE / "tools" / "scorecard.py").read_text(encoding="utf-8")
    job = text[:re.search(r"^# =+ running jobs$", text, re.M).start()]
    allowed = {Path(f).stem for f in sc.TOOL_FILES} | {"memory_peaks"}
    for src in [job] + [(SOFTWARE / f).read_text(encoding="utf-8") for f in sc.TOOL_FILES]:
        used = set(re.findall(r"from tools(?:\.(\w+))? import (\w+)", src))
        names = {a or b for a, b in used}
        assert names <= allowed, names - allowed


# ----------------------------------------------------------------------------------- the tables and the compare
def _ident(**kw) -> dict:
    base = {"schema": sc.IDENTITY_SCHEMA, "engine_code": "e1", "tool_code": "t1", "recording": "r1", "calibration": "c1",
            "models": None, "params": {}, "chain": "frame", "poses": "tracked", "pose_input": None, "depth_input": None,
            "libraries": {"numpy": "2.1.3"}, "python": "3.13.1", "device": "cpu", "compute": "cpu"}
    return dict(base, **kw)


BR = [f"bracket240-s{k}" for k in range(3)]


def _results(ident: dict | None, history: bool = False) -> dict:
    out = {}
    for k, n in enumerate(BR):
        r = {"metrics": {"part_med": 0.1 + 0.01 * k}, "info": {}, "error": None}
        if ident is not None:
            r["identity"] = dict(ident, poses="tracked")
        if history:
            r["history"] = {"why": ["the engine's code"]}
        out[n] = {"tracked": r}
    return out


def _compare(tmp_path, monkeypatch, base: dict, new: dict, *extra: str) -> tuple[int, list[str]]:
    p = tmp_path / "baselines" / "b.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"name": "b", "saved": "2026-11-20T00:00:00", "version": "test", "compute": "cpu", "params": {},
                             "recordings": BR, "results": base}), encoding="utf-8")
    monkeypatch.setattr(sc, "run_set", lambda *a, **k: new)
    out = tmp_path / "table.md"
    code = sc.main(["compare", "b", "--scenes", "bracket240", "--seeds", "0", "1", "2", "--compute", "cpu",
                    "--cache", str(tmp_path), "--out", str(out), *extra])
    return code, out.read_text(encoding="utf-8").splitlines()


def test_an_old_baseline_is_named_without_identity(tmp_path, monkeypatch):
    code, lines = _compare(tmp_path, monkeypatch, _results(None), _results(_ident()))
    assert code == 0
    assert "- baseline identity: without identity (saved before 2026-11-24)" in lines
    assert any(x.startswith("- this run's identity: engine code e1, scorecard code t1, chain frame, device cpu, numpy 2.1.3")
               for x in lines), lines
    assert lines[-1] == "**Better or the same on every scene.**"


def test_the_compare_names_both_identities_and_the_facts(tmp_path, monkeypatch):
    base = _results(_ident())
    new = _results(_ident(engine_code="e2", device="Another GPU", libraries={"numpy": "2.2.0"}))
    new[BR[1]]["tracked"]["identity"]["recording"] = "r2"
    _, lines = _compare(tmp_path, monkeypatch, base, new)
    text = "\n".join(lines)
    assert "- baseline identity: engine code e1, scorecard code t1, chain frame, device cpu, numpy 2.1.3" in text
    assert "- the engine's code differs from the baseline's: e1 there, e2 here." in text
    assert "- NOTE: the compute device differs from the baseline's: cpu there, Another GPU here." in text
    assert "- NOTE: the libraries differ from the baseline's: numpy 2.1.3 there, numpy 2.2.0 here." in text
    assert "- NOTE: the recording differs from the baseline's on 1 recording(s): bracket240-s1 tracked." in text
    _, lines = _compare(tmp_path, monkeypatch, base, _results(_ident()))
    assert "- NOTE: the same engine code and the same settings as the baseline." in lines


def test_a_compare_with_old_results_is_not_judged(tmp_path, monkeypatch):
    code, lines = _compare(tmp_path, monkeypatch, _results(_ident()), _results(None, history=True), "--history")
    assert code == 1
    assert lines[-1].startswith("**Not judged: the comparison is incomplete.**")
    assert "Old results (made by other code or inputs, used with --history): bracket240-s0 tracked" in lines[-1]
    assert any(x.startswith("- HISTORY: 3 result(s) were made by other code or inputs") for x in lines), lines


def test_a_baseline_is_not_saved_from_old_results(tmp_path, monkeypatch):
    res = {n: {p: {"metrics": {"part_med": 0.1}, "info": {}, "history": {"why": ["the engine's code"]}}
               for p in ("tracked", "true")} for n in BR}
    monkeypatch.setattr(sc, "run_set", lambda *a, **k: res)
    out = tmp_path / "t.md"
    sc.main(["baseline", "--save", "x", "--scenes", "bracket240", "--seeds", "0", "1", "2", "--compute", "cpu",
             "--cache", str(tmp_path), "--out", str(out), "--history"])
    assert not (tmp_path / "baselines" / "x.json").exists()
    assert "baseline NOT saved: 6 result(s) are old" in out.read_text(encoding="utf-8")
