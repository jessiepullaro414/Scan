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
              "_check", "_stop_scan_locked", "busy"}  # busy: the calibration's refusal (UX_PLAN R9)
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


# --------------------------------------------------------------------------- one name for each thing (UX_PLAN)
#: names the glossary ("One name for each thing") retired, with the name that took their place. Checked against
#: every text of the server and of the report (``ohs/report``) that a person reads.
RETIRED = [
    (r"\b[Ss]tereo\b", "the cameras (the Cameras step, the camera calibration)"),
    (r"(?i)still holds|2-minute check|launch check", "Calibration check / Calibration valid / start-up check"),
    (r"(?i)\bsigma\b", "scatter (the number) or confidence (the map)"),
    (r"(?i)loose pieces|floaters?\b", "loose bits"),
    (r"(?i)\b(part's|scan's|scan's own|reference's) frame\b|\bcoordinate system\b|\b(first|raw) camera frame",
     "the part's axes / the scan's axes"),
    (r"(?i)\breference\b(?! parts)", "the CAD model"),
    (r"(?<!on a )\bPico\b(?! or ESP32)", "the control board (Pico or ESP32)"),
    (r"(?i)\bemitters?\b|\bdot projector\b", "the lasers and dots"),
    (r"(?i)\blaser planes?\b", "laser lines"),
    (r"(?i)\bvoxels?\b", "mesh detail"),
    (r"(?i)\bremake\b|\brebuild the mesh\b|\bre-run\b|\bstep by step\b|\bfine-tune in process\b|\bprocess the scan\b"
     r"|\brun track\b", "Make mesh / Make mesh again / Edit mesh"),
    (r"(?i)\bbase mesh\b|\btracked mesh\b", "the starting mesh"),
    (r"(?i)\bgyro\b|\bIMU\b(?!\))", "the motion sensor (IMU)"),
    (r"\b(RMS|rms)\b", "typical / typically"),
    (r"(?i)\balign two scans\b|\bfused again\b", "Align and merge"),
    (r"(?i)\bsandbox\b|\bsimulator demo\b", "the simulator"),
]
#: texts with an old word that a person does not read: names the program matches (the engine's self-test
#: check names and its echo lines) and values of the API that the pages compare against (the state names of a
#: comparison with the CAD model)
RETIRED_EXEMPT = {"imu rate", "imu values", "dot projector", "Connecting to the Pico", "Switching each emitter on",
                  "not on the reference"}
#: in the report, "1σ" says once what every ± means (kept, UX_PLAN "What is good"); on the server's texts no σ at all
RETIRED_SERVER_ONLY = [("σ", "scatter (the number) or confidence (the map)")]


def _read_texts(source: str, joined: bool = False) -> list[tuple[int, str]]:
    """Every string a person may read in a module: its string constants, without docstrings, log calls and
    bare identifiers (a word without a space and not capitalised, like a key or a step id). ``joined``: also each
    f-string whole, its values as ``{}`` (so a word next to a value, " DN, " after ``{mean}``, is seen in its text)."""
    tree = ast.parse(source)
    skip: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            b = n.body[0]
            if isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant):
                skip.add(id(b.value))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) \
                and n.func.value.id in ("log", "logger", "logging", "_log"):
            skip |= {id(s) for s in ast.walk(n)}
        if isinstance(n, ast.Call) and _call_name(n) == "safe_call" and n.args:  # its label goes to the log only
            skip |= {id(s) for s in ast.walk(n.args[0])}
        if isinstance(n, ast.Dict):  # a key is a name the program uses, its value is the text
            skip |= {id(k) for k in n.keys if k is not None}
        if isinstance(n, ast.Compare):  # name == "imu rate", "no imu" in detail: matched, not shown
            skip |= {id(s) for s in ast.walk(n)}
        if isinstance(n, ast.Call) and _call_name(n) in ("startswith", "endswith", "replace"):
            skip |= {id(s) for a in n.args[:1] for s in ast.walk(a)}  # the engine's words being matched
    out = [(n.lineno, n.value) for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip
           and (" " in n.value.strip() or n.value[:1].isupper()) and n.value not in RETIRED_EXEMPT]
    if joined:
        for n in ast.walk(tree):
            if isinstance(n, ast.JoinedStr) and id(n) not in skip:
                text = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in n.values)
                if " " in text.strip() and text not in RETIRED_EXEMPT:
                    out.append((n.lineno, text))
    return out


def retired_names(source: str, filename: str = "<source>", report: bool = False) -> list[str]:
    rules = RETIRED + ([] if report else RETIRED_SERVER_ONLY)
    return [f"{filename}:{line}: {text[:90]!r}: {why}" for line, text in _read_texts(source)
            for rx, why in rules if re.search(rx, text)]


def test_no_retired_name_in_what_the_user_reads():
    """The glossary's retired names do not come back in the server's or the report's texts."""
    found = [p for f in sorted(SERVER.glob("*.py")) for p in retired_names(f.read_text(encoding="utf-8"), f.name)]
    found += [p for f in sorted((SERVER.parent / "report").glob("*.py"))
              for p in retired_names(f.read_text(encoding="utf-8"), f"report/{f.name}", report=True)]
    assert not found, "retired names (UX_PLAN, One name for each thing):\n" + "\n".join(found)


@pytest.mark.parametrize("src", [
    'metric("Stereo: row error", "x", True, "Matching corners.")', 'headline = "Your calibration still holds"',
    'notify("info", "Removing floaters…")', 'x = {"label": "the part\'s frame"}', 'S = "No Pico (it is unplugged)"',
    'progress(0.4, "Switching the emitters on")', 'x = "1 mm voxels"', 'ApiError(409, "Run Track first.")',
    'x = "Within 2σ of where it was measured"', 'x = "Gyro bias"', 'x = "0.04 mm RMS"', 'x = "Aligned to the reference"',
])
def test_the_retired_names_guard_catches_old_words(src):
    assert retired_names(src)


@pytest.mark.parametrize("src", [
    'x = "the control board (Pico or ESP32)"', 'x = "Motion sensor (IMU)"', 'x = {"stereo": 1}', 'k = "recalibrate_stereo"',
    'log.info("launch check: %s", s)', '"""The 2-minute check, in a docstring."""', 'safe_call("closing the Pico", f)', 'x = "the part\'s axes"',
    'x = "measured against independently measured reference parts"',
])
def test_the_retired_names_guard_lets_new_words_through(src):
    assert not retired_names(src)


# --------------------------------------------------------------------------- the engine's own sentences (UX_PLAN U51)
ENGINE = SERVER.parent
#: the engine's modules whose sentences reach a person through the server, the report or the pages (verdicts,
#: warnings, reasons, notes, labels, help cards; a plain exception's words pass ``plain_reason`` too)
ENGINE_MODULES = [
    "calib/health.py", "calib/drift.py", "calib/stereo.py", "calib/imu.py", "calib/laser_planes.py",
    "app/presets.py", "app/selftest.py", "app/calib_flow.py",
    "mesh/section.py", "mesh/support.py", "mesh/check.py",
    "cad/fcmacro.py", "cad/step.py", "cad/shapes.py", "cad/nominal.py",
    "inspect/reference.py", "inspect/gapflush.py",
    "align/pairwise.py", "align/merge.py",
    "guide/views.py",
    "measure/frame.py", "measure/region.py", "measure/fit.py", "measure/features.py",
    "track/path_check.py",
]
#: in the engine's sentences "1σ" may say what a ± is (as the report does); any other σ is scatter or confidence. And two
#: names the server's list does not hold: one loose piece, and the scan's uncertainty (the table: scatter)
ENGINE_RULES = [(r"(?<!1)σ", "scatter (the number) or confidence (the map)"), (r"(?i)\bloose piece\b", "loose bits"),
                (r"(?i)\buncertain(ty|ties)?\b", "scatter (the number) or confidence (the map)")]
#: texts of those modules that keep an old word, each with why (a regex on the text, and the reason)
ENGINE_EXEMPT = [
    # a check of the program's own input that names its parameter (``sigma``: "3 sigma values for 4 vertices",
    # "sigma must be positive and finite"); a person never causes it (the data the app hands on is checked before)
    (r"^ sigma values for |^sigma must ", "a check of the program's input, named after its parameter"),
    # the self-test's command line: its echo lines (the server turns each into the app's words, ohs/server/selftest.py
    # plain_prompt) and what a flag of the command line skipped
    (r"^Connecting to the Pico\.\.\.$|^Switching each emitter on for $", "an echo line of the command line, reworded by "
                                                                          "the server"),
    (r"^not tested \(--no-(pico|emitters|camera)\)$", "only the command line's flag skips it"),
    # the name of a section of VALIDATION.md, cited as the source of a measured bias (it must match the heading)
    (r"^The gap guard per voxel$", "a heading of VALIDATION.md, cited by name"),
    # the STEP file's own syntax (ISO 10303-21), which CAD programs read
    (r"REPRESENTATION_CONTEXT\(", "STEP syntax, read by CAD programs"),
    # a value the program compares, not a word on a page (the way a nominal fits: "2 sigma" or "bias")
    (r"^2 sigma$", "a kind the program compares"),
    # why a sign of the health block is not used for the calibration status (``ohs.calib.health.DROPPED``): a record
    # of the measurement for the plan, never shown
    (r"^each line against the stereo depth before the drop", "why a sign is dropped from the status: a record for the "
                                                              "plan, not shown"),
]


def engine_retired_names(source: str, filename: str) -> list[str]:
    rules = RETIRED + ENGINE_RULES
    return [f"{filename}:{line}: {text[:90]!r}: {why}" for line, text in _read_texts(source)
            for rx, why in rules if re.search(rx, text) and not any(re.search(e, text) for e, _ in ENGINE_EXEMPT)]


def test_no_retired_name_in_the_engines_sentences():
    """UX_PLAN U51: the glossary's retired names do not come back in the sentences the engine itself makes."""
    found = []
    for rel in ENGINE_MODULES:
        path = ENGINE / rel
        assert path.is_file(), rel
        found += engine_retired_names(path.read_text(encoding="utf-8-sig"), rel)
    assert not found, "retired names in the engine's sentences (UX_PLAN U51):\n" + "\n".join(found)


@pytest.mark.parametrize("src", [  # the engine's sentences before U51, verbatim
    'x = "Run the 2-minute check"',
    'out["message"] = f"Left and right rows agree to {_px(med)} px: the calibration still holds."',
    'PART_LABELS = {"imu": "IMU"}',
    'x = ("the lasers and the IMU are judged only in scans processed at full resolution")',
    'description = f"Section at {a} = {value:g} mm in the part\'s frame"',
    'where = "the part\'s frame" if shapes.in_part_frame else "the scan\'s own frame"',
    'msg = f"The reference is {r:.3g} times the size of the scan\'s part"',
    'x = f"of the other scan, {out.rmse_mm:.2f} mm RMS; {sure}.{loose}"',
    'x = "{_count(n_gone, \'small loose piece\')} ({a} mm²)"',
    'x = f"left ({a:,.0f} mm²): Remove floaters takes it."',
    'x = "the mesh\'s own uncertainty there is above the tolerance"',
    'x = "A finer mesh: 0.5 mm voxels instead of 1 mm. Nothing else."',
    'x = "(the stereo depth runs on across it)"',
    'x = f"the hole\'s edge is not round (RMS {r:.2f} mm): a slot or a damaged edge?"',
    'x = f"HELLO {fw} {ver} on {port}, imu={imu}"',
    'x = "the points scatter 2 times more than their sigma"',
])
def test_the_engine_guard_catches_the_old_sentences(src):
    assert engine_retired_names(src, "old")


@pytest.mark.parametrize("src", [
    'x = "Run the calibration check"', 'x = "Motion sensor (IMU)"', 'x = "the datums\' own ± (1σ): 0.1°"',
    'raise ValueError(f"{len(S)} sigma values for {len(V)} vertices")', 'x = "not tested (--no-pico)"',
    'x = "the part\'s axes set on the CAD model"', 'x = "typically 0.25 mm"',
])
def test_the_engine_guard_lets_new_words_through(src):
    assert not engine_retired_names(src, "new")


# --------------------------------------------------------------------------- program words (UX_PLAN R12, R14, R19, R20)
#: words of the program that reached the screens on the real engine (2026-11-21), with the words that took their place.
#: Checked against every text of the server and the report, and the engine sentences they pass on (progress lines of
#: Refine with lasers and Refine from the images, the self-test's details, the merge's and the table's notes).
PROGRAM_WORDS = [
    (r"\b(GPU|CPU)\b", "the graphics card / without a graphics card (the device's name: Settings' About only)"),
    (r"(?i)\btrajector(y|ies)\b", "the camera path"),
    (r"(?i)\bcorrection field\b", "where the surface must move"),
    (r"\bDN\b", "brightness, of 255"),
    (r"\|accel\||m/s\^2", "gravity reads … m/s²"),
    (r"\b\d{3,}x\d{3,}\b", "1,280 × 800 (a size in words, thousands separated)"),
    (r"(?i)last board check|\bboard check\b", "Calibration check"),
    (r"(?i)\bbaseline\b", "the distance between the cameras"),
    (r"(?i)given camera path", "the camera path that Follow the camera found"),
    (r"cm²", "mm² (one unit for areas)"),
    (r"(?i)reprojection|principal point", "how well the corners fit / image centre"),
    (r"Scanner problem|Engine error", "no title: the sentence says what happened"),
]
PROGRAM_WORD_MODULES = ["app/selftest.py", "mesh/anchor.py", "align/merge.py", "mesh/support.py", "track/spacetime.py"]


def program_words(source: str, filename: str = "<source>") -> list[str]:
    """The texts of ``source`` with a program word (a regular expression's own text, with its word boundaries, is not
    one)."""
    return [f"{filename}:{line}: {text[:90]!r}: {why}" for line, text in _read_texts(source, joined=True)
            for rx, why in PROGRAM_WORDS if "\\b" not in text and re.search(rx, text)]


def test_no_program_word_in_what_the_user_reads():
    found = [p for f in sorted(SERVER.glob("*.py")) for p in program_words(f.read_text(encoding="utf-8"), f.name)]
    found += [p for f in sorted((SERVER.parent / "report").glob("*.py"))
              for p in program_words(f.read_text(encoding="utf-8"), f"report/{f.name}")]
    found += [p for rel in PROGRAM_WORD_MODULES for p in program_words((ENGINE / rel).read_text(encoding="utf-8-sig"), rel)]
    assert not found, "program words (UX_PLAN R12, R19, R20):\n" + "\n".join(found)


def test_counts_of_frames_are_written_with_thousands_separated():
    """"frame 23 of 1321" (R19): the processing's frame counts take the separator."""
    src = (SERVER / "processing.py").read_text(encoding="utf-8")
    assert "frame {k + 1:,} of {n:,}" in src and "all {n:,} frames done" in src
    assert not re.search(r"frame \{k \+ 1\} of \{n\}|all \{n\} frames", src)


@pytest.mark.parametrize("src", [  # the texts the walk on the real engine found, verbatim or as the code made them
    'where = f" on the GPU ({dev.name})"', 'x = " on the CPU"', 'prog(0, "Straightening the trajectory")',
    'say(0.2, "fitting the correction field")', 'x = f"mean {mean:.0f} DN, {sat:.1%} saturated"',
    'detail = f"|accel| {g:.2f} m/s^2"', 'x = f"2560x800 (2 x {w}x{h}) from {what}"',
    'rows.append(("Last board check", text))', 'facts.append(f"baseline {b} mm")',
    'x = "Built from a given camera path (straightened, or rebuilt from the tracked path)"',
    'x = f"Its table is left out ({a:,.1f} cm² of its mesh"', 'metric("Reprojection error", v, ok, why)',
    'metric("Principal point vs truth", v, ok, why)', 'notify("error", f"Scanner problem: {m}")',
])
def test_the_program_words_guard_catches_the_old_texts(src):
    assert program_words(src)


@pytest.mark.parametrize("src", [
    'x = "Following the camera: frame 1,212 of 1,321"', 'x = "the graphics card"', 'x = "Calibration check"',
    'x = "the cameras 60.00 mm apart"', 'x = r"\\bthe GPU\\b"', 'x = "12,000 mm² of its mesh"',
])
def test_the_program_words_guard_lets_plain_texts_through(src):
    assert not program_words(src)
