"""A second pass's chain between two of its steps (``ohs.server.passes``, FEATURE_PLAN N2).

When one step's job has ended and the next step's job is not submitted yet, the chain still names
the step that ended. A request that looks at the chain in that moment (the page polls it) must not
take it for a chain the app was closed on. Found 2026-11-20 in a full test run on a busy PC: the
pass's page would have read "Stopped while making the pass's mesh: the app was closed before it
ended" for a chain that went on a moment later.
"""
from __future__ import annotations

from types import SimpleNamespace

from ohs.server.passes import PassManager
from ohs.server.protocol import ApiError


class _Projects:
    def __init__(self) -> None:
        self.entry = {"id": "pass", "name": "Pass", "status": "processed", "n_frames": 60,
                      "continues": {"scan_id": "first", "scan_name": "First", "same_setup": True,
                                    "chain": {"state": "processing", "step": "process", "jobs": {"process": "j1"}}}}

    def scan_entry(self, pid: str, sid: str) -> dict:
        return self.entry

    def update_scan(self, pid: str, sid: str, **fields) -> dict:
        self.entry = {**self.entry, **fields}
        return self.entry


class _Jobs:
    """The process job has ended: it is known and no longer active."""

    def get(self, jid: str):
        if jid != "j1":
            raise ApiError(404, "no such job", "")
        return SimpleNamespace(active=False)


def _manager() -> tuple[PassManager, _Projects]:
    projects = _Projects()
    st = SimpleNamespace(projects=projects, jobs=_Jobs(), notify=lambda *a, **k: None)
    m = PassManager(st)
    m._watch["j1"] = ("p", "pass", "process")
    return m, projects


def test_a_look_at_the_chain_between_two_steps_does_not_call_it_stopped(monkeypatch):
    m, projects = _manager()
    seen: list[str] = []

    def finished(job, pid, sid, step):  # the moment after the job ended, before the next step is submitted
        _, c = m._link_of(pid, sid)
        seen.append(m._settle_stale(pid, sid, c)["chain"]["state"])  # what GET .../pass would answer now

    monkeypatch.setattr(m, "_finished", finished)
    m.on_job({"id": "j1", "state": "done", "result": {}})
    assert seen == ["processing"], seen
    assert projects.entry["continues"]["chain"]["state"] == "processing"
    assert "app was closed" not in str(projects.entry["continues"]["chain"].get("message"))
    assert not m._starting and not m._watch  # nothing is left held once the step has been handed on


def test_a_chain_nobody_runs_is_still_called_stopped():
    """The rule itself stays: a chain that says a step runs, with no job and nobody moving it on."""
    m, projects = _manager()
    m._watch.clear()
    _, c = m._link_of("p", "pass")
    out = m._settle_stale("p", "pass", c)
    assert out["chain"]["state"] == "cancelled" and "app was closed" in out["chain"]["message"]
