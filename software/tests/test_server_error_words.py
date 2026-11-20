"""What the server says when something goes wrong, in plain words (UX_PLAN.md U9, U10, U13).

The person who reads these texts scans car parts; they do not read Python, call API routes or run
command lines. So no text the server shows (an error's ``detail`` and ``hint``, a job's ``message``
and ``error``, a stage's ``summary``, a notice) may carry:

* the words of a caught exception (``str(e)``, ``{e}``, ``{e.args}``...), unless the exception is
  an :class:`~ohs.server.protocol.ApiError` (its words are the app's own) or they go through
  :func:`~ohs.server.protocol.plain_reason`, which lets a plain sentence through and sends the rest
  to the log; nor an exception's class name (``type(e).__name__``);
* Python's ``repr`` quoting (``{x!r}``);
* a command line (``ohs ...``, ``pip ...``, ``npm ...``), an API route (``GET ``, ``POST ``, ``/api/``),
  ``APP_API``, or an em dash.

:func:`problems` walks a module's syntax tree and checks the arguments of the calls that make such
texts (:data:`TEXT_CALLS`) and the keywords that hold them (:data:`TEXT_KEYWORDS`), and the module's
constants named like hints and words. What it cannot see: a text built in a variable first and
passed on later (``msg = f"... {e}"`` then ``notify(msg)``: only the call's own expression is
looked at), an exception passed to a helper as a parameter, texts in returned tuples (the Free
space refusals), and the words of the engine's own exceptions (``ohs.mesh`` and the like), which
reach the user only through ``plain_reason``. Dict literals are looked at by their keys (``{"detail":
..., "hint": ...}``, ``{"reason": ..., "text": ...}``).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1] / "ohs" / "server"

#: calls whose arguments are words for the user
TEXT_CALLS = {"ApiError", "_bad", "_err", "notify", "_notify", "progress", "prog", "_set_chain", "_stage_end",
              "_report_lost", "failure_notice", "failed_result", "_idle_tracking", "none", "metric", "advice",
              "_check", "_stop_scan_locked"}
#: keywords of any call that hold words for the user
TEXT_KEYWORDS = {"message", "summary", "reason", "verdict", "detail", "hint", "text", "error", "note"}
#: module constants that hold words for the user
#: (``_NO_UI`` is not one: the page a developer's checkout shows before the UI is built, with its npm commands)
TEXT_CONSTANT = re.compile(r"(HINT|WORDS|NOTE|TEXT|GONE|RELOAD|RETRY|^_NO_(?!UI$))")
#: dicts that are records kept for a bug report, not read on screen (the calibration's report.json download keeps
#: the engine's own words of a failed solve)
RECORDS = {"last_report"}
#: what is never in a text for the user
FORBIDDEN = [(re.compile(r"\bohs\s"), "a command line (ohs ...)"), (re.compile(r"\bpip\s"), "a command line (pip ...)"),
             (re.compile(r"\bnpm\s"), "a command line (npm ...)"),
             (re.compile(r"\b(GET|POST|PUT|PATCH|DELETE) "), "an API route"), (re.compile(r"/api/"), "an API route"),
             (re.compile(r"APP_API"), "APP_API.md"), (re.compile("\u2014"), "an em dash")]


def _caught(tree: ast.AST) -> dict[ast.AST, set[str]]:
    """For each except handler's body nodes: the names bound to a caught exception that is not an ApiError."""
    out: dict[ast.AST, set[str]] = {}
    for h in ast.walk(tree):
        if isinstance(h, ast.ExceptHandler) and h.name:
            types = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
            api_only = all(isinstance(t, ast.Name) and t.id == "ApiError" for t in types if t is not None) and h.type
            if api_only:
                continue
            for node in h.body:
                for sub in ast.walk(node):
                    out.setdefault(sub, set()).add(h.name)
    return out


def _call_name(node: ast.Call) -> str:
    f = node.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _text_problems(expr: ast.AST, caught: set[str]) -> list[str]:
    out = []
    for sub in ast.walk(expr):
        if isinstance(sub, ast.Call) and _call_name(sub) == "plain_reason":
            continue
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out += [why for rx, why in FORBIDDEN if rx.search(sub.value)]
        if isinstance(sub, ast.FormattedValue):
            if sub.conversion == ord("r"):
                out.append("Python repr quoting ({x!r})")
            names = {n.id for n in ast.walk(sub.value) if isinstance(n, ast.Name)}
            if names & caught and not any(isinstance(c, ast.Call) and _call_name(c) == "plain_reason"
                                          for c in ast.walk(sub.value)):
                out.append(f"the words of a caught exception ({{{', '.join(sorted(names & caught))}}})")
        if isinstance(sub, ast.Call) and _call_name(sub) == "str" and sub.args:
            names = {n.id for n in ast.walk(sub.args[0]) if isinstance(n, ast.Name)}
            if names & caught:
                out.append(f"the words of a caught exception (str({', '.join(sorted(names & caught))}))")
        if isinstance(sub, ast.Attribute) and sub.attr == "__name__":
            out.append("an exception's class name (__name__)")
    return out


def problems(source: str, filename: str = "<source>") -> list[str]:
    """Every text for the user in ``source`` that breaks the rule, as ``file:line: why``."""
    tree = ast.parse(source, filename)
    caught = _caught(tree)
    found: list[str] = []
    records = {n.value for n in ast.walk(tree) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict)
               and any((t.attr if isinstance(t, ast.Attribute) else getattr(t, "id", None)) in RECORDS for t in n.targets)}

    def check(expr: ast.AST, at: ast.AST) -> None:
        names = caught.get(at, set())
        for why in dict.fromkeys(_text_problems(expr, names)):
            found.append(f"{filename}:{getattr(expr, 'lineno', getattr(at, 'lineno', 0))}: {why}")

    # the arguments are skipped inside other text calls' plain_reason (already filtered)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if _call_name(node) in TEXT_CALLS:
                for a in node.args:
                    check(a, node)
            for kw in node.keywords:
                if kw.arg in TEXT_KEYWORDS:
                    check(kw.value, node)
        if isinstance(node, ast.Dict) and node not in records:  # {"detail": ..., "hint": ...}, {"reason": ..., "text": ...}
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value in TEXT_KEYWORDS:
                    check(v, node)
        if isinstance(node, ast.Assign):  # job.error = ..., out["error"] = ... (pairs of a tuple assignment too)
            for t in node.targets:
                pairs = (zip(t.elts, node.value.elts) if isinstance(t, ast.Tuple) and isinstance(node.value, ast.Tuple)
                         and len(t.elts) == len(node.value.elts) else [(t, node.value)])
                for target, value in pairs:
                    key = (target.attr if isinstance(target, ast.Attribute) else
                           target.slice.value if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                           else None)
                    if key in TEXT_KEYWORDS:
                        check(value, node)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and TEXT_CONSTANT.search(t.id) for t in node.targets):
            check(node.value, node)
    return found


# --------------------------------------------------------------------------- the rule on the server's source
def test_every_text_of_the_server_follows_the_rule():
    files = sorted(SERVER.glob("*.py"))
    assert len(files) > 25
    found = [p for f in files for p in problems(f.read_text(encoding="utf-8"), f.name)]
    assert not found, "texts for the user that break UX_PLAN U9:\n" + "\n".join(found)


#: texts of the server as they were before UX_PLAN U9 (verbatim), each inside the except clause it was in
OLD = [
    ('except Exception as exc:\n    _err(500, f"internal error: {type(exc).__name__}: {exc}",\n'
     '         "this is a bug: see the log (ohs -v app) and report it; your recordings are safe")', 3),
    ('except Exception as e:\n    text = str(e) or type(e).__name__\n    job.state, job.message = "failed", "Failed"\n'
     '    job.error = f"{text} (try again; if it keeps failing, run `ohs -v app` and look at the log)"', 1),
    ('except (ValueError, TypeError, KeyError) as e:\n'
     '    out["relation"], out["error"] = None, f"these two fits cannot be related: {e}"', 1),
    ('except Exception as e:\n    raise ApiError(503, f"the camera could not be opened: {e}",\n'
     '                   "check the USB cable, close other programs that use the camera, then run `ohs devices` "\n'
     '                   "and set camera.index in the settings")', 2),
    ('except Exception as err:\n    raise ApiError(500, f"the live processing could not start: {err}",\n'
     '                   "check the calibration (ohs calibrate ...) and that Open3D is installed (pip install -e .[fusion])")',
     3),
    ('except Exception as e:\n    _stage_end(sp, "fuse", state="failed", summary=f"Fusing failed: {e}")', 1),
    ('raise ApiError(404, f"no job {job_id!r}", "list the jobs with GET /api/jobs")', 2),
    ('raise ApiError(404, f"no recipe {rid!r}", "pick a recipe from GET /api/recipes")', 2),
    ('except ValueError as e:\n    raise ApiError(422, str(e), "give the tolerance in mm, for example 0.1")', 1),
    ('raise ApiError(404, f"unknown API route /api/{rest}", "see software/docs/APP_API.md for the routes")', 2),
    ('except Exception as e:\n    self._notify("warn", f"the emitters could not be switched on ({e}): check the Pico\'s USB cable")',
     1),
    ('except Exception as e:\n    set_chain(pid, sid, state="failed", message=f"The pass could not be processed: {e}.")', 1),
    ('X_HINT = "check the request body against APP_API.md \u2014 or GET /api/docs"', 3),
    ('except Exception as e:\n    crep = {"ok": False, "verdict": "failed", "summary": f"The check failed: {e}"}', 1),
    ('except Exception as e:\n    res = {"state": "fail", "detail": str(e)}', 1),
]
#: texts that follow the rule (and look a little like those that do not)
FINE = [
    'except ApiError as e:\n    notify("warn", str(e))',
    'except ApiError as e:\n    set_chain(pid, sid, message=f"Could not start it. {e} The pass is kept.")',
    'except ValueError as e:\n    raise ApiError(422, plain_reason(e, "This name cannot be used."), "Give a name.")',
    'except OSError as e:\n    log.warning("cannot write %s: %s", path, e)\n    raise ApiError(500, "It could not be saved.", "Check the disk.")',
    'raise ApiError(409, f"The scan \\"{name}\\" is busy.", "Wait until it has finished (see Jobs).")',
    'notify("info", "Pipes, ohms and posts are words: Get it, post it.")',
    'except ValueError as e:\n    self.last_report = {"kind": kind, "error": str(e)}',
]


def _in_try(src: str) -> str:
    return "try:\n    pass\n" + src if src.startswith("except") else src


@pytest.mark.parametrize("src, n", OLD)
def test_the_guard_catches_the_old_texts(src, n):
    """Seen failing: each of these is one of the server's texts before U9, and the guard finds it."""
    found = problems(_in_try(src))
    assert len(found) >= n, found


@pytest.mark.parametrize("src", FINE)
def test_the_guard_lets_plain_texts_through(src):
    assert problems(_in_try(src)) == []


# --------------------------------------------------------------------------- the helpers behind the rule
def test_sentence_and_plain_reason():
    from ohs.server.protocol import ApiError, plain_reason, sentence

    assert sentence("the scan is busy") == "The scan is busy."
    assert sentence("  Done.  ") == "Done." and sentence("Why?") == "Why?" and sentence("") == ""
    assert sentence('the scan "Door"') == 'The scan "Door".' and sentence("(see Jobs)") == "(see Jobs)."
    e = ApiError(409, "the scan is busy", "wait for it")
    assert (e.detail, e.hint, str(e)) == ("The scan is busy.", "Wait for it.", "The scan is busy. Wait for it.")
    assert ApiError(404, "Gone.").hint is None and str(ApiError(404, "Gone.")) == "Gone."
    # a plain sentence written for the user passes; a program's words do not
    assert plain_reason(ValueError("a name must be 1 to 80 characters"), "x") == "a name must be 1 to 80 characters"
    for raw in ("could not broadcast input array from shape (3,) into shape (4,)", "'NoneType' object has no attribute 'x'",
                "[Errno 13] Permission denied: 'C:\\\\scan\\\\mesh.ply'", "KeyError: 'faces'", "voxel_mm must be > 0",
                "index 12 is out of bounds for axis 0 with size 3 (dtype=int64)"):
        assert plain_reason(ValueError(raw), "fallback") == "fallback", raw


def test_a_failed_job_says_what_failed_in_plain_words():
    """A job's error: an ApiError's detail and hint as two sentences; any other exception the fixed words of
    its kind, never the exception's (they are in the log)."""
    from ohs.server.jobs import FAILURE_TEXT, JobRunner, failure_notice
    from ohs.server.protocol import ApiError

    runner = JobRunner()
    try:
        def boom(ctx):
            raise RuntimeError("index 7 out of range in _fuse_into (secret internals)")

        def refused(ctx):
            raise ApiError(409, "the recording has no frames", "record the scan again")

        j1 = runner.wait(runner.submit("process_fuse", boom).id, 30)
        j2 = runner.wait(runner.submit("process_scan", refused).id, 30)
        j3 = runner.wait(runner.submit("some_new_kind", boom).id, 30)
    finally:
        runner.shutdown()
    assert j1.state == "failed" and j1.error == FAILURE_TEXT["process_fuse"] and "secret" not in j1.error
    assert j1.error == "Building the surface failed. Try again, or Make mesh at Standard quality. Your recording is safe."
    assert j2.state == "failed" and j2.error == "The recording has no frames. Record the scan again."
    assert j3.error.startswith("The task failed.") and "secret" not in j3.error
    assert failure_notice(j1, "Merging failed.") == j1.error  # the fixed words stand alone
    assert failure_notice(j2, "Making the mesh failed.", "Your recording is safe.") == (
        "Making the mesh failed. The recording has no frames. Record the scan again. Your recording is safe.")
    for text in FAILURE_TEXT.values():
        assert text[0].isupper() and text.endswith(".") and not problems(f"notify('error', {text!r})")


def test_hole_sizes_say_what_the_size_means():
    """U13: a hole's size is the length round it; the edit's label says so, with the diameter."""
    from ohs.server.processing import BUILTIN_RECIPES, holes_mm, normalize_op

    assert holes_mm(15) == "15 mm around (about Ø 5 mm)"
    assert holes_mm(5) == "5 mm around (about Ø 1.6 mm)"
    assert holes_mm(10) == "10 mm around (about Ø 3 mm)" and holes_mm(40) == "40 mm around (about Ø 13 mm)"
    assert normalize_op("fill_holes", {"max_hole_mm": 15})[1] == "Fill holes up to 15 mm around (about Ø 5 mm)"
    assert normalize_op("fill_holes", {"max_hole_mm": 5, "watertight": True})[1] == (
        "Make watertight: close every opening up to 5 mm around (about Ø 1.6 mm)")
    for r in BUILTIN_RECIPES:
        for o in r["ops"]:
            if o["op"] == "fill_holes":
                assert holes_mm(o["params"]["max_hole_mm"]) in r["description"], r["name"]


def test_calibration_verdicts_promise_no_accuracy():
    """U10: a calibration's grade is not an accuracy; no verdict promises one (the roadmap's rule)."""
    src = (SERVER / "calib_results.py").read_text(encoding="utf-8")
    for promise in ("will be accurate", "accurate scans", "Laser points will be accurate"):
        assert promise not in src
    for said in ("This calibration is as good as the board allows.", "Good: no step needs redoing.",
                 "laser lines are calibrated well."):
        assert said in src
