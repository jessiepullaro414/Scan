"""tools/scorecard.py: a comparison against a baseline on incomplete evidence is never a pass.

A failed run, a run that is not there, a pose source that is not there, or a reading the baseline had
and the new run lost: the comparison says it is incomplete, names what is missing, never prints
"Better or the same on every scene.", and the compare command ends with exit status 1. Made-up
results only: nothing is rendered or processed (numpy only)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SOFTWARE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE))

from tools import scorecard as sc  # noqa: E402

SUCCESS = "**Better or the same on every scene.**"
BR = [f"bracket240-s{k}" for k in range(3)]
HL = [f"holes-low-s{k}" for k in range(3)]
POSES = ("tracked", "true")


def _metrics(k: int) -> dict:
    return {"traj_med": 0.5 + 0.1 * k, "part_med": 0.10 + 0.01 * k, "cover1": 0.90 + 0.01 * k,
            "br_upright_thickness_err": 0.05 + 0.02 * k, "br_boss_left_r_err": -0.15 + 0.02 * k,
            "br_boss_left_answered": 3, "faces": 100000 + k, "frame_ms": 300.0 + k, "_stray": {"a": 1}}


def _base() -> dict:
    return {n: {p: {"metrics": _metrics(k), "error": None} for p in POSES} for k, n in enumerate(BR)}


def _copy(d: dict) -> dict:
    return json.loads(json.dumps(d))


def _specs(names=BR):
    return [sc.SCENES[n] for n in names]


def _all_text(rows) -> str:
    return "\n".join(sc.compare_table(rows))


def _assert_not_a_pass(rows) -> str:
    """The success sentence appears nowhere: not in the last line, not in the summary, not in the table."""
    last = sc.verdict_line(rows)
    assert last != SUCCESS, last
    assert "Better or the same" not in last, last
    assert "Better or the same" not in sc.summary_line(rows), sc.summary_line(rows)
    text = _all_text(rows)
    assert "Better or the same" not in text
    assert text.rstrip().splitlines()[-1] == last
    return last


def test_all_failed_runs_are_not_a_pass():
    new = {n: {p: {"error": "device failed", "metrics": {}} for p in POSES} for n in BR}
    rows = sc.compare_rows(_base(), new, _specs())
    last = _assert_not_a_pass(rows)
    assert last.startswith("**Not judged: the comparison is incomplete.**")
    assert "Failed" in last and "bracket240-s0 tracked (device failed)" in last and "bracket240-s2 true" in last
    ck = rows.check
    assert ck["complete"] is False
    assert sorted((f["rec"], f["poses"]) for f in ck["failed"]) == sorted((n, p) for n in BR for p in POSES)
    assert all(f["error"] == "device failed" for f in ck["failed"])
    assert ck["missing"] == [] and ck["lost"] == []


def test_an_empty_candidate_is_not_a_pass():
    rows = sc.compare_rows(_base(), {}, _specs())
    last = _assert_not_a_pass(rows)
    assert last.startswith("**Not judged: the comparison is incomplete.**")
    assert "Not run: " in last and all(f"{n} {p}" in last for n in BR for p in POSES)
    ck = rows.check
    assert ck["complete"] is False and ck["compared"] == 0
    assert sorted((m["rec"], m["poses"]) for m in ck["missing"]) == sorted((n, p) for n in BR for p in POSES)


def test_one_of_three_recordings_is_not_a_pass():
    base = _base()
    rows = sc.compare_rows(base, {BR[0]: _copy(base[BR[0]])}, _specs())
    last = _assert_not_a_pass(rows)
    assert "Not run: " in last and "bracket240-s1 tracked" in last and "bracket240-s2 true" in last
    assert "bracket240-s0" not in last.split("Not run: ")[1]
    ck = rows.check
    assert ck["complete"] is False and ck["compared"] == 2
    assert sorted((m["rec"], m["poses"]) for m in ck["missing"]) == sorted((n, p) for n in BR[1:] for p in POSES)


def test_a_missing_pose_source_is_not_a_pass():
    base = _base()
    new = {n: {"tracked": _copy(base[n]["tracked"])} for n in BR}  # the true-pose runs are not there
    rows = sc.compare_rows(base, new, _specs())
    last = _assert_not_a_pass(rows)
    assert all(f"{n} true" in last for n in BR) and "bracket240-s0 tracked" not in last
    assert rows.check["complete"] is False


def test_an_empty_metric_dictionary_is_not_a_pass():
    """A run with no error that measured nothing: every reading of the baseline is lost."""
    base = _base()
    new = _copy(base)
    new[BR[1]]["tracked"] = {"metrics": {}, "error": None}
    rows = sc.compare_rows(base, new, _specs())
    last = _assert_not_a_pass(rows)
    assert "Not measured any more: " in last and "bracket240-s1 tracked (" in last and "part_med" in last
    lost = rows.check["lost"]
    assert [(x["rec"], x["poses"]) for x in lost] == [("bracket240-s1", "tracked")]
    assert "part_med" in lost[0]["metrics"] and "faces" not in lost[0]["metrics"]  # not judged: not mandatory
    assert "frame_ms" not in lost[0]["metrics"] and "_stray" not in lost[0]["metrics"]


def test_a_lost_wall_thickness_is_not_a_pass():
    """A run that otherwise succeeded no longer measures the bracket's upright thickness (not a watched metric:
    today's verdict took the two other recordings alone and called it identical)."""
    base = _base()
    new = _copy(base)
    new[BR[1]]["tracked"]["metrics"]["br_upright_thickness_err"] = None
    rows = sc.compare_rows(base, new, _specs())
    last = _assert_not_a_pass(rows)
    assert "Not measured any more: bracket240-s1 tracked (br_upright_thickness_err)" in last
    assert rows.check["lost"] == [{"rec": "bracket240-s1", "poses": "tracked", "metrics": ["br_upright_thickness_err"]}]
    row = next(r for r in rows if r["metric"] == "br_upright_thickness_err" and r["poses"] == "tracked")
    assert row["new"][1] is None and sc.fmt(row["new"][1]) == sc.NM  # never 0


def test_a_lost_hole_diameter_says_incomplete():
    """A hole the baseline measured is no longer measured on one seed (its key gone): the alarm stays (LOST), and the
    comparison also says which reading is missing."""
    base = {n: {"tracked": {"metrics": {"part_med": 0.06 + 0.01 * k, "h10_d_err": -0.30 + 0.1 * k, "h10_answered": 3},
                            "error": None}} for k, n in enumerate(HL)}
    new = _copy(base)
    del new[HL[2]]["tracked"]["metrics"]["h10_d_err"]
    rows = sc.compare_rows(base, new, _specs(HL))
    last = _assert_not_a_pass(rows)
    assert last.startswith("**Not judged: the comparison is incomplete.**")
    assert "holes-low-s2 tracked (h10_d_err)" in last
    assert "LOST" in _all_text(rows)
    assert rows.check["complete"] is False


def test_a_complete_unchanged_set_is_still_a_pass():
    base = _base()
    rows = sc.compare_rows(base, _copy(base), _specs())
    assert rows.check["complete"] is True
    assert rows.check["missing"] == rows.check["failed"] == rows.check["lost"] == []
    assert {s["verdict"] for s in sc.scene_verdicts(rows)} == {"identical"}
    assert sc.verdict_line(rows) == SUCCESS and sc.summary_line(rows) == SUCCESS
    assert _all_text(rows).rstrip().splitlines()[-1] == SUCCESS
    assert "INCOMPLETE" not in _all_text(rows)


def test_a_real_regression_is_still_worse():
    base = _base()
    new = _copy(base)
    for n in BR:
        new[n]["tracked"]["metrics"]["part_med"] += 0.2
    rows = sc.compare_rows(base, new, _specs())
    assert rows.check["complete"] is True
    assert sc.verdict_line(rows).startswith("**Worse on: bracket240 (tracked")


def test_an_incomplete_regression_says_both():
    base = _base()
    new = _copy(base)
    for n in BR[:2]:
        new[n]["tracked"]["metrics"]["part_med"] += 0.2
    del new[BR[2]]
    last = _assert_not_a_pass(sc.compare_rows(base, new, _specs()))
    assert last.startswith("**Not judged: the comparison is incomplete.**") and "Worse on: bracket240 (tracked" in last


def test_a_recording_skipped_here_is_not_missing():
    """A recording the tool skips on this PC (its model files are not here) is the tool's own notion: named, not
    counted as missing."""
    base = _base()
    new = _copy(base)
    new[BR[2]] = {"skipped": "skipped: door.stl not found"}
    rows = sc.compare_rows(base, new, _specs())
    ck = rows.check
    assert ck["complete"] is True and ck["missing"] == []
    assert ck["skipped"] == [{"rec": "bracket240-s2", "why": "skipped: door.stl not found"}]
    assert sc.verdict_line(rows) == SUCCESS
    assert "bracket240-s2: skipped: door.stl not found" in _all_text(rows)


def test_everything_skipped_is_not_a_pass():
    new = {n: {"skipped": "skipped: door.stl not found"} for n in BR}
    rows = sc.compare_rows(_base(), new, _specs())
    last = _assert_not_a_pass(rows)
    assert "Nothing was compared." in last and rows.check["complete"] is False


def test_no_rows_at_all_are_not_a_pass():
    assert sc.verdict_line([]) != SUCCESS and "Nothing was compared." in sc.verdict_line([])
    assert "Better or the same" not in sc.summary_line([])
    rows = sc.compare_rows({}, {}, _specs())  # the baseline has none of these recordings
    _assert_not_a_pass(rows)
    assert rows.check["complete"] is False


def test_a_failed_baseline_run_is_not_a_pass():
    base = _base()
    base[BR[0]]["true"] = {"error": "timed out", "metrics": {}}
    rows = sc.compare_rows(base, _copy(base), _specs())
    last = _assert_not_a_pass(rows)
    assert "Failed in the baseline: bracket240-s0 true (timed out)" in last


def test_a_scene_with_nothing_measured_is_not_a_pass():
    """Both sides n/m on every judged reading of a scene: not "identical"."""
    base = {n: {"tracked": {"metrics": {"part_med": None}, "error": None}} for n in BR}
    rows = sc.compare_rows(base, _copy(base), _specs())
    assert {s["verdict"] for s in sc.scene_verdicts(rows)} == {sc.NM}
    _assert_not_a_pass(rows)


def _write_baseline(cache: Path, name: str, results: dict) -> None:
    p = cache / "baselines" / f"{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"name": name, "saved": "2026-11-24T00:00:00", "version": "test", "compute": "cpu",
                             "params": {}, "recordings": BR, "results": results}), encoding="utf-8")


@pytest.mark.parametrize("case", ["complete", "failed", "empty", "one_of_three", "lost"])
def test_the_compare_command_exit_status(tmp_path, monkeypatch, case):
    """The command line: exit status 0 for a complete comparison, 1 when evidence is missing; the table is written
    either way, its last line the verdict."""
    base = _base()
    _write_baseline(tmp_path, "b", base)
    new = {"complete": _copy(base),
           "failed": {n: {p: {"error": "device failed", "metrics": {}, "info": {}} for p in POSES} for n in BR},
           "empty": {},
           "one_of_three": {BR[0]: _copy(base[BR[0]])},
           "lost": _copy(base)}[case]
    if case == "lost":
        new[BR[1]]["tracked"]["metrics"]["br_upright_thickness_err"] = None
    monkeypatch.setattr(sc, "run_set", lambda *a, **k: new)
    out = tmp_path / "table.md"
    code = sc.main(["compare", "b", "--scenes", "bracket240", "--compute", "cpu", "--cache", str(tmp_path),
                    "--out", str(out)])
    last = out.read_text(encoding="utf-8").rstrip().splitlines()[-1]
    if case == "complete":
        assert code == 0 and last == SUCCESS
    else:
        assert code == 1, last
        assert last.startswith("**Not judged: the comparison is incomplete.**") and "Better or the same" not in last
