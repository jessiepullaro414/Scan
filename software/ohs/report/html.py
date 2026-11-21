"""The report of one scan as one self-contained HTML page that prints cleanly (FEATURE_PLAN I3, D1).

:func:`build_html` turns the plain data of :mod:`ohs.report.data` into one HTML file with
everything inside it. What it holds, in order, each part only when its data is there: the head
(project, scan, date, the app's version, one line on what this is), the warnings that were not
resolved, what the numbers mean, how the scan was made, the calibration, the Check answer, the
part's coordinate system, views of the model, the measured features, the relations, the sections,
the comparison with a reference model and the flush along a seam.

**Self-contained and safe.** The page makes no request of any kind: the styles are in the page, no
font, stylesheet, script or image is fetched, and there is no script at all (nothing in the report
needs one). A Content-Security-Policy in the page says so to the browser as well
(``default-src 'none'``; images only from ``data:``; the page's own style). Every name and text
that comes from the data is escaped for HTML, so a fit named ``<script>...`` is text. Pictures and
the sections' drawings are embedded as ``data:`` images in ``<img>``: a browser runs no script and
fetches nothing inside an image, so even an SVG that was handed in carrying one could not run it.
Only PNG, JPEG, GIF and WebP pictures are taken (told by their first bytes, not by what they are
called).

**Prints cleanly.** A print style for A4 and Letter alike: the page size follows the paper the
browser prints on (portrait), with margins of 16-18 mm that leave about 180 mm across on both; black
text on white; table heads repeated on each page, no table row, drawing or warning split across
two pages, headings kept with what follows; drawings scaled to the page's width. Page numbers
("Page 2 of 5") where the browser draws CSS page-margin boxes (Chrome and Edge do). On screen the
page has a light look and follows a dark system setting; either reads well.

**Deterministic.** The same data gives the same bytes: the date and the version are data, nothing
is read from the clock, and nothing depends on the order of a set.
"""
from __future__ import annotations

import base64
import html as _html

from ohs.report import numbers as N
from ohs.report.data import HEADING, HEALTH_WORDS, KIND_WORDS, ROLE_WORDS, SHAPE_WORDS, Prepared, name_of, prepare

__all__ = ["build_html", "MEANING", "LEAD", "PICTURE_TYPES"]

LEAD = ("What was scanned and how, what was measured on it with its ± values, and every warning that was not "
        "resolved. Made by OpenHandScan from the scan's own records.")

#: "What the numbers mean": the same short statement in every report (roadmap, "On accuracy claims")
MEANING = (
    "Every ± in this report is one standard deviation (1σ) of the spread from scan to scan, worked out from the "
    "scan's own points. It is not the accuracy: errors of the calibration and of the scale, and errors that a whole "
    "region shares, come on top.",
    "No accuracy figure is claimed. OpenHandScan states one only after the scanner has been measured against "
    "independently measured reference parts, for that setup and those conditions, and that has not been done yet.",
    "Where a suggested nominal value rests on a known bias (a boss that reads small, a hole read at a level), that "
    "bias was measured on simulated scans, not on the real scanner, and the report says so where it was used.",
    "Lengths are in millimetres, angles in degrees. A value shows as many decimals as its ± allows: "
    "14.02 ± 0.03 mm.",
)

#: picture types taken, by their first bytes
PICTURE_TYPES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"),
                 (b"GIF89a", "image/gif"))

BEST_FIT_NOTE = ("A best fit spreads an overall error of the part (too long, bent or shifted) over the whole part, "
                 "where it hides; an alignment on the part's datums shows where it is.")
DATUM_NOTE = "No best fit was made: an overall error of the part shows where it is."
DRAWING_NOTE = ("Each drawing is scaled to fit the page, so it is not 1:1 here: measure with its scale bar. The "
                "section's SVG and DXF downloads are at 1:1.")

CSS = """
:root{--ink:#1b1f24;--muted:#57606a;--line:#d0d7de;--paper:#ffffff;--band:#f4f6f8;--accent:#0b5cad;
--warn:#8a4b00;--warn-bg:#fff5e0;--warn-line:#e7b45c;color-scheme:light dark}
@media (prefers-color-scheme: dark){:root{--ink:#e6edf3;--muted:#9da7b3;--line:#3a434d;--paper:#14181d;
--band:#1d232a;--accent:#79b8ff;--warn:#f5c26b;--warn-bg:#2a2112;--warn-line:#8a6a2c}}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0 auto;max-width:200mm;padding:24px 16px 48px;color:var(--ink);background:var(--paper);
font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
header{border-bottom:2px solid var(--ink);padding-bottom:12px;margin-bottom:8px}
.kicker{margin:0;color:var(--muted);font-size:12px;letter-spacing:.06em;text-transform:uppercase}
h1{margin:4px 0 2px;font-size:26px;line-height:1.2}
.project{margin:0;font-size:16px}
.lead{margin:10px 0 0;color:var(--muted)}
h2{margin:28px 0 8px;font-size:19px;border-bottom:1px solid var(--line);padding-bottom:4px}
h3{margin:18px 0 6px;font-size:15px}
p{margin:6px 0}
.muted,.note{color:var(--muted)}
.note{font-size:12.5px}
dl.facts{display:grid;grid-template-columns:max-content 1fr;gap:3px 16px;margin:8px 0}
dl.facts dt{color:var(--muted)}
dl.facts dd{margin:0}
nav ol{margin:8px 0 0;padding-left:20px;columns:2;column-gap:24px;font-size:13px}
a{color:var(--accent)}
table{width:100%;border-collapse:collapse;margin:8px 0;font-size:13px}
thead{display:table-header-group}
th,td{text-align:left;vertical-align:top;padding:4px 6px;border-bottom:1px solid var(--line)}
th{background:var(--band);font-weight:600}
td.num,.num{font-variant-numeric:tabular-nums}
td ul{margin:0;padding-left:16px}
.badge{display:inline-block;padding:0 6px;border:1px solid var(--warn-line);border-radius:3px;color:var(--warn);
background:var(--warn-bg);font-size:11.5px;font-weight:600}
.nominal{margin-top:2px}
.warn{color:var(--warn)}
ul.warnings{list-style:none;padding:0;margin:8px 0}
ul.warnings li{border-left:3px solid var(--warn-line);background:var(--warn-bg);padding:5px 8px;margin:4px 0}
ul.warnings .where{font-size:12px;color:var(--muted)}
.meaning p{margin:4px 0}
figure{margin:12px 0;text-align:center}
figure img{max-width:100%;height:auto;border:1px solid var(--line);background:#ffffff}
figcaption{margin-top:4px;font-size:12.5px;color:var(--muted);text-align:left}
footer{margin-top:32px;border-top:1px solid var(--line);padding-top:6px;color:var(--muted);font-size:12px}
@media print{
@page{size:portrait;margin:16mm 15mm 18mm 15mm;
@bottom-right{content:"Page " counter(page) " of " counter(pages);font-size:8pt;color:#000}}
:root{--ink:#000;--muted:#333;--line:#999;--paper:#fff;--band:#eee;--accent:#000;--warn:#000;--warn-bg:#fff;
--warn-line:#000;color-scheme:light}
html,body{color:#000;background:#fff}
body{max-width:none;padding:0;font-size:9.5pt}
h1{font-size:18pt}
h2{font-size:13pt;margin-top:18px}
nav{display:none}
a{color:#000;text-decoration:none}
table{font-size:8.5pt}
thead{display:table-header-group}
tr,figure,img,ul.warnings li,dl.facts{break-inside:avoid;page-break-inside:avoid}
h2,h3{break-after:avoid;page-break-after:avoid}
figure img{max-height:200mm;border-color:#999}
.badge{border-color:#000;color:#000;background:#fff}
}
"""


# --------------------------------------------------------------------------- escaping
def esc(x) -> str:
    """Text from the data, made safe for HTML: control characters become spaces, then ``& < > " '``
    are escaped (for text and for attribute values alike)."""
    s = "".join(" " if (ord(c) < 32 and c not in "\t\n\r") or ord(c) == 127 else c for c in str(x))
    return _html.escape(s, quote=True)


def _dl(rows: list[tuple[str, str]]) -> str:
    """A list of facts: ``(label, value as HTML)``; labels are fixed text."""
    return "<dl class=\"facts\">" + "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in rows) + "</dl>"


def _table(head: list[str], rows: list[list[str]], num: tuple[int, ...] = ()) -> str:
    """A table: fixed head words, cells as HTML; the columns in ``num`` are numbers."""
    out = ["<table><thead><tr>" + "".join(f"<th>{esc(h)}</th>" for h in head) + "</tr></thead><tbody>"]
    for r in rows:
        out.append("<tr>" + "".join(f"<td class=\"num\">{c}</td>" if i in num else f"<td>{c}</td>"
                                    for i, c in enumerate(r)) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _ul(items: list[str]) -> str:
    """A list of texts from the data, escaped."""
    return "<ul>" + "".join(f"<li>{esc(t)}</li>" for t in items) + "</ul>" if items else ""


def _badge(text: str) -> str:
    return f"<span class=\"badge\">{esc(text)}</span>"


def _when(x) -> str:
    """A time as the app keeps it ("2026-11-15T09:12:00"), shown as "2026-11-15 09:12"."""
    s = str(x)
    if len(s) >= 16 and s[10] == "T":
        return s[:10] + " " + s[11:16]
    return s


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _num(x) -> bool:
    return N.is_number(x)


def _vec_ok(v) -> bool:
    return isinstance(v, (list, tuple)) and len(v) == 3 and all(_num(c) for c in v)


def _part(pid: str, body: list[str]) -> str:
    return f"<section id=\"{pid}\"><h2>{esc(HEADING[pid])}</h2>" + "".join(body) + "</section>"


# --------------------------------------------------------------------------- the parts
def _head(data: dict, contents: list[str]) -> str:
    project, scan = name_of(data.get("project")), name_of(data.get("scan"))
    out = ["<header id=\"head\"><p class=\"kicker\">OpenHandScan scan report</p>",
           f"<h1>{esc(scan) if scan else 'Scan report'}</h1>"]
    if project:
        out.append(f"<p class=\"project\">Project: {esc(project)}</p>")
    facts = []
    if data.get("date") not in (None, ""):
        facts.append(("Date", esc(data["date"])))
    if data.get("app_version") not in (None, ""):
        facts.append(("Made with", f"OpenHandScan {esc(data['app_version'])}"))
    if facts:
        out.append(_dl(facts))
    out.append(f"<p class=\"lead\">{esc(LEAD)}</p>")
    out.append("<nav><ol>" + "".join(f"<li><a href=\"#{p}\">{esc(HEADING[p])}</a></li>" for p in contents)
               + "</ol></nav></header>")
    return "".join(out)


def _warnings(prep: Prepared) -> str:
    if not prep.warnings:
        return _part("warnings", ["<p>None of the parts in this report carries a warning.</p>"])
    n = len(prep.warnings)
    body = [f"<p>{n} warning{'s' if n != 1 else ''}, each also shown where it belongs. The scan was not stopped "
            "for any of them: they are for the reader to weigh.</p><ul class=\"warnings\">"]
    for w in prep.warnings:
        item = f"<strong>{esc(w.item)}</strong>: " if w.item else ""
        body.append(f"<li><span class=\"where\"><a href=\"#{w.part}\">{esc(HEADING.get(w.part, w.part))}</a></span>"
                    f"<br>{item}{esc(w.text)}</li>")
    body.append("</ul>")
    return _part("warnings", body)


def _meaning() -> str:
    return _part("meaning", ["<div class=\"meaning\">"] + [f"<p>{esc(p)}</p>" for p in MEANING] + ["</div>"])


_PRESET_WORDS = {"mode": "tracking mode", "quality": "quality", "voxel_mm": "mesh detail", "thin_walls": "thin walls",
                 "recipe": "recipe", "check_tolerance_mm": "check tolerance"}


def _preset_value(k: str, v) -> str:
    if k in ("voxel_mm", "check_tolerance_mm") and _num(v):
        return f"{N.nominal(v)} mm"
    return str(v)


def _preset_text(pr: dict) -> str:
    """"Thin sheet metal and small parts (mesh detail 0.5 mm); changed by hand: mesh detail 0.75 mm"."""
    text = str(pr.get("name") or pr.get("id") or "General")
    named = pr.get("preset_values") if isinstance(pr.get("preset_values"), dict) else {}
    if named:
        text += " (" + ", ".join(f"{_PRESET_WORDS.get(k, k)} {_preset_value(k, v)}" for k, v in named.items()) + ")"
    hand = pr.get("hand") if isinstance(pr.get("hand"), dict) else {}
    changed = [k for k in (pr.get("changed") or []) if k in hand]
    if changed:
        text += "; changed by hand: " + ", ".join(f"{_PRESET_WORDS.get(k, k)} {_preset_value(k, hand[k])}" for k in changed)
    if pr.get("note"):
        text += ". " + str(pr["note"])
    return text


def _made(p: dict, merge: dict | None = None) -> str | None:
    rows: list[tuple[str, str]] = []
    if isinstance(merge, dict):  # a merged scan (UX_PLAN R8): what it was made from, and what the merge left out
        names = [str(n) for n in merge.get("from") or [] if n]
        if names:
            rows.append(("Merged from", esc(", ".join(names) + ": aligned and merged into one mesh; it has no recording "
                                                               "of its own")))
        notes = [str(n) for n in merge.get("notes") or [] if n]
        if notes:
            rows.append(("What the merge left out", "<br>".join(esc(n) for n in notes)))
    if p.get("source") == "sim" or p.get("scene"):
        rows.append(("Made", "In the simulator" + (f" (scene “{esc(p['scene'])}”)" if p.get("scene") else "")))
    mode = p.get("mode")
    if mode:
        rows.append(("Tracking mode", esc({"markerless": "Without markers: the surface's own shape tracks the scanner",
                                           "markers": "With markers"}.get(str(mode), str(mode)))))
    if p.get("quality"):
        rows.append(("Quality", esc(str(p["quality"]).capitalize())))
    if isinstance(p.get("preset"), dict):  # FEATURE_PLAN P2; a scan processed before presets has none
        rows.append(("Preset", esc(_preset_text(p["preset"]))))
    fr = p.get("frames") if isinstance(p.get("frames"), dict) else {}
    if _num(fr.get("processed")):
        text = f"{N.whole(fr['processed'])} frames used"
        if _num(fr.get("in_recording")):
            text += f" of {N.whole(fr['in_recording'])} recorded"
        if _num(fr.get("step")) and fr["step"] > 1:
            text += f" (every {_ordinal(int(fr['step']))} frame)"
        if _num(fr.get("cut")) and fr["cut"]:
            text += f"; {N.whole(fr['cut'])} left out where the recording was cut"
        rows.append(("Frames", esc(text)))
    if p.get("poses") == "given":
        rows.append(("Tracking", "Built from the camera path that Follow the camera found (straightened, when Straighten corrected it)"))
    elif _num(fr.get("tracked")) and _num(fr.get("processed")) and fr["processed"]:
        text = f"{N.whole(fr['tracked'])} of {N.whole(fr['processed'])} frames tracked ({N.percent(fr['tracked'] / fr['processed'])})"
        fails = p.get("failures") if isinstance(p.get("failures"), dict) else {}
        if fails:
            text += "; not tracked: " + ", ".join(f"{N.whole(v)} ({k})" for k, v in fails.items() if _num(v))
        rows.append(("Tracking", esc(text)))
    imu = p.get("imu") if isinstance(p.get("imu"), dict) else {}
    if imu.get("gyro_prior") and _num(imu.get("frames_with_prior")):
        rows.append(("Motion sensor", esc(f"helped the tracking on {N.whole(imu['frames_with_prior'])} frames")))
    mk = p.get("markers") if isinstance(p.get("markers"), dict) else {}
    if _num(mk.get("map_size")):
        rows.append(("Markers", esc(f"{N.whole(mk['map_size'])} in the map")))
    mesh = p.get("mesh") if isinstance(p.get("mesh"), dict) else {}
    if _num(mesh.get("n_faces")):
        text = f"{N.whole(mesh['n_faces'])} triangles"
        if _num(mesh.get("n_vertices")):
            text += f", {N.whole(mesh['n_vertices'])} points"
        bb = mesh.get("bbox_mm")
        if isinstance(bb, (list, tuple)) and len(bb) == 2 and _vec_ok(bb[0]) and _vec_ok(bb[1]):
            text += "; " + " × ".join(N.fixed(b - a, 1) for a, b in zip(bb[0], bb[1])) + " mm across"
        if _num(mesh.get("voxel_mm")):
            text += f"; mesh detail {N.nominal(mesh['voxel_mm'])} mm"
        if mesh.get("method") == "poisson":
            text += " (Poisson surface: no ± per point)"
        rows.append(("Mesh", esc(text)))
    las = p.get("lasers") if isinstance(p.get("lasers"), dict) else {}
    if las.get("planes") or _num(las.get("n_points_exported")):
        parts = []
        if las.get("planes"):
            parts.append("lines " + ", ".join(str(x) for x in las["planes"]))
        if _num(las.get("n_points_exported")):
            parts.append(f"{N.whole(las['n_points_exported'])} laser points kept")
        rows.append(("Lasers", esc("; ".join(parts))))
    def _dur(x: float) -> str:
        s = int(round(x))
        return f"{s // 60} min {s % 60:02d} s" if s >= 60 else f"{s} s"

    # UX_PLAN R17: the whole Make mesh when it is known, else said to be the last stage's time alone
    if _num(p.get("make_mesh_s")):
        rows.append(("Processing time", esc(f"{_dur(p['make_mesh_s'])} for the whole Make mesh, every stage and edit")))
    elif _num(p.get("wall_time_s")):
        rows.append(("Processing time", esc(f"{_dur(p['wall_time_s'])} for the last stage that made this mesh (the "
                                            "stages before it and the edits not counted)")))
    return _part("made", [_dl(rows)]) if rows else None


#: which calibration the scan was made with (ohs.server.report, "made_with"), in words
MADE_WITH = {
    "simulator": "The simulator's own calibration, the one its demo recording was made with.",
    "replay": "The replayed recording's own calibration, the one it was recorded with.",
    "scanner": "The scanner's calibration, as it was when this scan was recorded.",
}


def _merged_calibration(cal: dict) -> str:
    """The calibration part of a merged scan (UX_PLAN R8): it has no recording, so no calibration of its own."""
    names = [str(n) for n in cal.get("merged_from") or [] if n]
    text = ("Each scan it was merged from" + (f" ({', '.join(names)})" if names else "")
            + " was made with its own calibration; its report says how that calibration held.")
    health = ("Not judged for the merged scan itself: a merge has no recording of its own, so there is nothing new "
              "to judge the calibration by. The reports of the scans it was merged from have it.")
    return _part("calibration", [_dl([("Made with", esc(text)), ("Health", esc(health))])])


def _calibration(cal: dict) -> str:
    if cal.get("made_with") == "merge":
        return _merged_calibration(cal)
    rows: list[tuple[str, str]] = []
    if cal.get("made_with") in MADE_WITH:
        rows.append(("Made with", esc(MADE_WITH[cal["made_with"]])))
    if cal.get("missing"):
        rows.append(("Calibration file", _badge("warning") + " " + esc("The recording has no calibration file, so it is "
                                                                       "not known what it was made with.")))
    parts = cal.get("parts") if isinstance(cal.get("parts"), dict) else None
    missing = [str(x) for x in cal.get("lasers_missing") or []]
    if parts is not None:
        for key, label in (("stereo", "Cameras"), ("lasers", "Laser lines"), ("imu", "Motion sensor (IMU)")):
            info = parts.get(key)
            if isinstance(info, dict):
                text = esc(str(info.get("summary") or "Calibrated"))
                text += f" <span class=\"muted\">({esc(_when(info['when']))})</span>" if info.get("when") else ""
            elif key in parts:
                text = "Not calibrated"
            else:
                continue
            if key == "lasers" and missing:  # the build has laser lines the file lacks
                text += "<br>" + _badge("warning") + " " + esc(
                    f"Not calibrated: the {', '.join(missing)} laser line{'s' if len(missing) > 1 else ''} of this scanner.")
            rows.append((label, text))
    facts = []
    if cal.get("modified"):
        facts.append(f"saved {_when(cal['modified'])}")
    if _num(cal.get("baseline_mm")):
        facts.append(f"the cameras {N.length(cal['baseline_mm'])} apart")
    if facts:
        rows.append(("Calibration file", esc("; ".join(facts))))
    last = cal.get("last_check")
    if isinstance(last, dict):
        ok = last.get("ok")
        word = "Passed" if ok else "Not passed" if ok is False else "Done"
        verdict = str(last.get("verdict") or "").strip()
        text = esc(verdict or word)  # the verdict says whether it passed: never "passed: The check passed"
        if last.get("when"):
            text += f" <span class=\"muted\">({esc(_when(last['when']))})</span>"
        rows.append(("Calibration check", (_badge("warning") + " " if ok is False else "") + text))
    health = cal.get("health")
    if isinstance(health, dict):
        state = str(health.get("state") or "")
        label = HEALTH_WORDS.get(state, state or "Not known")
        text = esc(label) + (esc(f" ({health['part']})") if health.get("part") else "")
        if health.get("reason"):
            text += esc(f": {health['reason']}")
        # what could not be judged is said, never called fine (H4)
        names = [{"imu": "the motion sensor"}.get(str(p), f"the {p}") for p in health.get("not_judged") or []]
        if names:
            text += "<br>" + f"<span class=\"muted\">{esc('Not judged: ' + ' and '.join(names) + '.')}</span>"
        if health.get("note"):
            text += "<br>" + f"<span class=\"muted\">{esc(str(health['note']))}</span>"
        if health.get("provisional"):
            src = "what this scan saw of its calibration" if health.get("source") == "scan" else "the calibration's checks"
            text += "<br>" + (f"<span class=\"muted\">{esc(f'Judged from {src}. The limits are provisional until the real scanner has been measured.')}</span>")
        rows.append(("Health", (_badge("warning") + " " if state != "valid" else "") + text))
    else:
        rows.append(("Health", "Not assessed: this scan's processing did not judge its calibration (it was processed "
                               "before the app did that). Processing it again judges it."))
    path = cal.get("path")
    if isinstance(path, dict):  # the camera path's check for a slide (ENGINE_PLAN X0a); it warns only
        said = [str(w) for w in path.get("warnings") or [] if isinstance(w, str) and w.strip()]
        if said:
            rows.append(("Camera path", _badge("warning") + " " + "<br>".join(esc(w) for w in said)))
        elif path.get("state") == "ok":
            rows.append(("Camera path", esc("No jump found: the camera moved as a hand moves, so tracking did not "
                                            "slide anywhere it could tell.")))
        else:
            rows.append(("Camera path", esc("Not judged: the scan is too short to judge whether tracking slid.")))
    return _part("calibration", [_dl(rows)])


def _check(c: dict) -> str | None:
    tol, k = c.get("tolerance_mm"), c.get("k", 2.0)
    if not _num(tol):
        return None
    k = k if _num(k) else 2.0
    t = f"± {N.nominal(tol)} mm"
    body = []
    ok, unc, nm = c.get("share_ok"), c.get("share_uncertain"), c.get("share_not_measured")
    if ok is None and nm is None:
        body.append(f"<p>The mesh is empty: there is nothing to check against {esc(t)}.</p>")
    elif not c.get("has_sigma", True) or ok is None:
        body.append(f"<p>This mesh has no ± per point, so only the filled-in areas can be told: {esc(N.percent(nm))} of "
                    f"the surface is not measured. Whether the rest meets {esc(t)} cannot be said.</p>")
    else:
        body.append(f"<p><strong>{esc(N.percent(ok))} of the surface was measured finely enough for {esc(t)}</strong>: "
                    f"there, the scan's own scatter ({N.nominal(k)} times its ±) is within the tolerance.</p>")
        rows = [("Measured finely enough", esc(N.percent(ok))),
                ("Measured too loosely", esc(f"{N.percent(unc)} (the scan's scatter there is wider than {t})")),
                ("Not measured", esc(f"{N.percent(nm)} (filled in: nothing measured it)"))]
        if _num(c.get("area_mm2")):
            rows.append(("Surface", esc(N.area(c["area_mm2"]))))
        body.append(_dl(rows))
    areas = [a for a in c.get("areas") or [] if isinstance(a, dict)]
    if areas:
        body.append("<h3>The areas that do not meet it, largest first</h3>")
        rows = []
        for i, a in enumerate(areas, 1):
            kind = "Not measured" if a.get("kind") == "not_measured" else "Measured too loosely"
            sig = "—"
            if _num(a.get("sigma_median_mm")):  # the scatter is the same k σ the check uses
                sig = f"{N.small(k * a['sigma_median_mm'])} / {N.small(k * a['sigma_worst_mm'])} mm" if _num(
                    a.get("sigma_worst_mm")) else f"{N.small(k * a['sigma_median_mm'])} mm"
            rows.append([str(i), esc(kind), esc(N.area(a["area_mm2"])) if _num(a.get("area_mm2")) else "—",
                         esc(N.percent(a["share"])) if _num(a.get("share")) else "—",
                         esc(N.vector(a["center"], 1) + " mm") if _vec_ok(a.get("center")) else "—",
                         esc(N.length(a["extent_mm"])) if _num(a.get("extent_mm")) else "—", esc(sig)])
        body.append(_table(["#", "Kind", "Area", "Share", "Centre (scan's axes)", "Across", "Typical / largest scatter"], rows,
                           num=(0, 2, 3, 4, 5, 6)))
        n = c.get("n_areas")
        notes = []
        if isinstance(n, int) and n > len(areas):
            notes.append(f"The largest {len(areas)} of {n} areas are listed.")
        if _num(c.get("min_area_mm2")):
            notes.append(f"Areas under {N.fixed(c['min_area_mm2'], 1)} mm² are not listed; they count in the shares.")
        if notes:
            body.append(f"<p class=\"note\">{esc(' '.join(notes))}</p>")
    return _part("check", body)


def _sigma_text(s, unit: str) -> str:
    return "not known (see the warnings)" if not _num(s) else (N.small(s) + ("°" if unit == "deg" else " mm"))


def _frame(prep: Prepared) -> str | None:
    fr = prep.frame
    if fr is None:
        return None
    body = []
    if not fr.get("readable", True):
        body.append(f"<p>{_badge('cannot be read')} {esc('; '.join(str(x) for x in fr.get('out_of_date_reasons') or []))}</p>")
        return _part("frame", body)
    if fr.get("out_of_date"):
        reasons = "; ".join(str(x) for x in fr.get("out_of_date_reasons") or [])
        body.append(f"<p>{_badge('out of date')} {esc(reasons)}. The numbers below are those of the axes as they were set.</p>")
    rows = []
    for b in fr.get("built_from") or []:
        if isinstance(b, dict):
            rows.append([esc(ROLE_WORDS.get(b.get("role"), str(b.get("role") or ""))),
                         esc(b.get("name") or b.get("id") or "—"),
                         esc(SHAPE_WORDS.get(b.get("kind"), str(b.get("kind") or ""))), esc(b.get("sets") or "")])
    if rows:
        body.append("<h3>Built from</h3>")
        body.append(_table(["Role", "Fitted shape", "Kind", "What it sets"], rows))
    facts = []
    axes = fr.get("axes") if isinstance(fr.get("axes"), dict) else {}
    if axes:
        facts.append(("Axes", esc(f"the primary gives {axes.get('primary')}, the secondary {axes.get('secondary')}; "
                                  f"{axes.get('third')} completes a right-handed set of axes")))
    choices = []
    if fr.get("flip_primary"):
        choices.append(f"{axes.get('primary', 'the first axis')} turned round")
    if fr.get("flip_secondary"):
        choices.append(f"{axes.get('secondary', 'the second axis')} turned round")
    if fr.get("secondary_zero"):
        choices.append("strict 3-2-1")
    if choices:
        facts.append(("Choices", esc(", ".join(choices))))
    sig = fr.get("sigma") if isinstance(fr.get("sigma"), dict) else {}
    if sig:
        facts.append(("± of the turn", esc(f"{_sigma_text(sig.get('angle_deg'), 'deg')} in all (tilt "
                                           f"{_sigma_text(sig.get('tilt_deg'), 'deg')}, turn about the first axis "
                                           f"{_sigma_text(sig.get('turn_deg'), 'deg')})")))
        per = sig.get("origin_axes_mm")
        text = _sigma_text(sig.get("origin_mm"), "mm")
        if isinstance(per, (list, tuple)) and len(per) == 3:
            text += " (" + ", ".join(f"{n} {_sigma_text(s, 'mm')}" for n, s in zip("XYZ", per)) + ")"
        facts.append(("± of the origin", esc(text)))
    pf = prep.part_frame
    if pf is not None:
        facts.append(("Origin, along the scan's axes", esc(N.vector(pf.origin, 3) + " mm")))
        for n, row in zip("XYZ", pf.rotation):
            facts.append((f"{n} axis, along the scan's axes", esc(N.vector(row, 4))))
    body.append(_dl(facts))
    if sig:
        body.append("<p class=\"note\">A turn error moves a point L mm from the origin by about L times the turn (in "
                    "radians).</p>")
    warns = [str(w) for w in fr.get("warnings") or []]
    if warns:
        body.append("<h3>Warnings</h3>" + _ul(warns))
    return _part("frame", body)


def _picture_type(data) -> str | None:
    if not isinstance(data, (bytes, bytearray)):
        return None
    b = bytes(data[:16])
    for magic, mime in PICTURE_TYPES:
        if b.startswith(magic):
            return mime
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return None


def _pictures(pics: list) -> str | None:
    body = []
    left_out = []
    for i, p in enumerate(pics, 1):
        if not isinstance(p, dict):
            continue
        title = str(p.get("title") or f"View {i}")
        mime = _picture_type(p.get("data"))
        if mime is None:
            left_out.append(title)
            continue
        src = f"data:{mime};base64," + base64.b64encode(bytes(p["data"])).decode("ascii")
        cap = f"<strong>{esc(title)}</strong>" + (f": {esc(p['caption'])}" if p.get("caption") else "")
        body.append(f"<figure><img src=\"{src}\" alt=\"{esc(title)}\"><figcaption>{cap}</figcaption></figure>")
    if left_out:
        body.append(f"<p class=\"note\">Not shown (not a PNG, JPEG, GIF or WebP image): {esc(', '.join(left_out))}.</p>")
    return _part("pictures", body) if body else None


def _size_html(v: dict) -> str:
    """A fit's size, and beside it each approved nominal value."""
    kind = v["shape"].get("kind")
    if kind == "plane":
        text = esc(f"flat to {N.small(v['rms_mm'])} mm (typical)") if _num(v.get("rms_mm")) else "—"
    elif _num(v.get("diameter_mm")):
        text = esc("Ø " + N.pm(v["diameter_mm"], v.get("diameter_sigma_mm"), "mm"))
    else:
        text = "—"
    for n in v["nominals"]:
        value = n.get("value")
        if value == "radius" and _num(v.get("radius_mm")):
            sym, meas, sig = "R", v["radius_mm"], v.get("radius_sigma_mm")
        elif _num(v.get("diameter_mm")):
            sym, meas, sig = "Ø", v["diameter_mm"], v.get("diameter_sigma_mm")
        else:
            continue
        measured = N.pm(meas, sig, "")
        line = f"nominal {sym} {N.nominal(n['nominal'])} (measured {measured})" if _num(n.get("nominal")) else ""
        extra = []
        if n.get("source") == "entered":
            extra.append("entered")
        if n.get("with_bias"):
            extra.append("allowed only by a known bias, a simulator number")
        if extra:
            line += f" · {', '.join(extra)}"
        text += f"<div class=\"nominal\">{esc(line)}" + (" " + _badge("check again") if n.get("check_again") else "") \
            + "</div>"
    return text


def _features(prep: Prepared) -> str | None:
    if not prep.features and not prep.measurements:
        return None
    body = []
    if prep.in_part_frame:
        where = "Positions and directions are along the part's axes (see “The part's axes”, below the warnings)."
        if prep.frame and prep.frame.get("out_of_date"):
            where += " Those axes are out of date."
    elif prep.part_frame_unreadable:
        where = "Positions and directions are along the scan's own axes: the part's axes cannot be read."
    else:
        where = "Positions and directions are along the scan's own axes."
    body.append(f"<p class=\"note\">{esc(where)} A plane's position is the centre of its points, with the ± of its "
                "offset along its normal; a cylinder's a point on its axis and a hole's its centre, with the ± across "
                "the axis.</p>")
    if prep.features:
        rows = []
        for v in prep.features:
            kind = KIND_WORDS.get(v["kind"], str(v["kind"]))
            if v["kind"] == "cylinder" and v.get("form") in ("boss", "bore"):
                kind += f", {v['form']}"
            pos = "—"
            if _vec_ok(v.get("position")):
                s = v.get("position_sigma")
                d = N.decimals(s) if _num(s) else 2
                pos = N.vector(v["position"], d) + (f" ± {N.fixed(s, d)}" if _num(s) else "") + " mm"
            dr = "—"
            if _vec_ok(v.get("direction")):
                s = v.get("direction_sigma")
                dr = N.vector(v["direction"], 4) + (f" ± {N.small(s)}°" if _num(s) else " (no ±)")
            ext = "—"
            if v["kind"] == "plane" and _num(v.get("length_mm")) and _num(v.get("width_mm")):
                ext = f"{N.fixed(v['length_mm'], 1)} × {N.fixed(v['width_mm'], 1)} mm"
                if _num(v.get("area_mm2")):
                    ext += f", {N.area(v['area_mm2'])}"
            elif v["kind"] == "cylinder" and _num(v.get("length_mm")):
                ext = f"length {N.fixed(v['length_mm'], 1)} mm" + (f", round {N.fixed(v['arc_deg'], 0)}°"
                                                                    if _num(v.get("arc_deg")) else "")
            where = f"<div>at {esc(pos)}</div><div>{'axis' if v['kind'] == 'cylinder' else 'normal'} {esc(dr)}</div>"
            rows.append([f"<strong>{esc(v['name'])}</strong><div class=\"muted\">{esc(kind)}</div>", _size_html(v), where,
                         esc(ext), _ul(v["notes"]) or "—"])
        body.append(_table(["Name", "Size", "Where", "Extent", "Warnings"], rows, num=(1, 2, 3)))
        body.append("<p class=\"note\">A plane's size is how flat it is: how far its points typically lie from it. Extent: a plane's "
                    "smallest rectangle round its outline and its measured area; a cylinder's length along its axis "
                    "and how much of its round was scanned.</p>")
    if prep.measurements:
        body.append("<h3>Distances between two points</h3>")
        rows = [[f"<strong>{esc(m['name'])}</strong>", esc(N.vector(m["a"], 2) + " mm"), esc(N.vector(m["b"], 2) + " mm"),
                 esc(N.length(m["distance_mm"])), _ul(m["notes"]) or "—"] for m in prep.measurements]
        body.append(_table(["Name", "From", "To", "Distance", "Warnings"], rows, num=(1, 2, 3)))
        body.append("<p class=\"note\">Two clicked points on the mesh: no ± (the clicks are where the user put "
                    "them).</p>")
    return _part("features", body)


def _value_html(q: dict) -> str:
    unit = "deg" if q.get("unit") == "deg" else "mm"
    if not _num(q.get("value")):
        return esc(str(q.get("label") or q.get("name") or ""))
    return esc(f"{q.get('label') or q.get('name')}: {N.pm(q['value'], q.get('sigma'), unit)}")


def _approval_html(n: dict) -> str:
    value = str(n.get("value") or "")
    unit = "°" if n.get("unit") == "deg" else " mm"
    nom = N.nominal(n["nominal"]) + unit if _num(n.get("nominal")) else "—"
    what = {"angle": "angle", "distance": "distance", "centre_distance": "centre to centre"}.get(value, value)
    text = f"{n['word']} ({what} {nom})" if n.get("word") else f"{what} {nom}"
    if n.get("source") == "entered":
        text += " · entered"
    if n.get("with_bias"):
        text += " · allowed only by a known bias, a simulator number"
    return esc(text) + (" " + _badge("check again") if n.get("check_again") else "")


def _relations(prep: Prepared) -> str | None:
    if not prep.relations:
        return None
    rows = []
    for r in prep.relations:
        values = "<br>".join(_value_html(q) for q in r["values"]) or "—"
        if r["notes"]:
            values += f"<div class=\"note\">{esc(' '.join(r['notes']))}</div>"
        approved = "<br>".join(_approval_html(n) for n in r["nominals"]) or "—"
        cad = esc(r["cad_move"])
        if r.get("cad_result"):
            cad += f"<div class=\"note\">{esc(r['cad_result'])}</div>"
        rows.append([f"<strong>{esc(r['name'])}</strong>", esc(f"{r['a_name']} and {r['b_name']}"), values, approved, cad,
                     _ul(r["warnings"]) or "—"])
    body = [_table(["Name", "Between", "Values", "Approved", "In the CAD files", "Warnings"], rows, num=(2,))]
    body.append("<p class=\"note\">Each value's ± comes from both fits' own ±. An approved value is the round value "
                "an engineer accepted; the measured value stays as it is. “Listed only”: the approved value is listed "
                "in the CAD files and no shape is moved to suit it.</p>")
    return _part("relations", body)


def _sections(entries: list) -> str | None:
    body = [f"<p class=\"note\">{esc(DRAWING_NOTE)}</p>"]
    n = 0
    for e in entries:
        if not isinstance(e, dict):
            continue
        n += 1
        name = str(e.get("name") or f"Section {n}")
        body.append(f"<h3>{esc(name)}</h3>")
        if e.get("description"):
            body.append(f"<p>{esc(e['description'])}" + (" (in the part's axes)" if e.get("in_part_frame") else "") + "</p>")
        svg = e.get("svg")
        if isinstance(svg, str) and svg.lstrip().startswith(("<?xml", "<svg")):
            src = "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
            body.append(f"<figure><img src=\"{src}\" alt=\"{esc('Section: ' + name)}\"><figcaption>Solid black: "
                        "measured. Dashed red: not measured (the surface there was filled in). Seen from the side "
                        "the plane's normal points to.</figcaption></figure>")
        rows = []
        if _num(e.get("length_mm")):
            lines = f"{N.whole(e.get('n_lines') or 0)} line{'s' if e.get('n_lines') != 1 else ''}"
            if isinstance(e.get("n_closed"), int):
                lines += f", {e['n_closed']} closed"
            rows.append(("Cut", esc(f"{lines}; {N.length(e['length_mm'])} in all")))
        size = e.get("size_mm")
        if isinstance(size, (list, tuple)) and len(size) == 2 and all(_num(x) for x in size):
            rows.append(("Size of the drawing", esc(f"{N.fixed(size[0], 2)} × {N.fixed(size[1], 2)} mm")))
        if _num(e.get("not_measured_mm")):
            rows.append(("Not measured", esc(N.length(e["not_measured_mm"]) + " of the cut runs over filled faces")))
        if rows:
            body.append(_dl(rows))
        lines = [ln for ln in e.get("lines") or [] if isinstance(ln, dict)]
        if lines:
            shown = lines[:12]
            body.append(_table(["Line", "Closed", "Length", "Enclosed area"], [
                [str(i), "yes" if ln.get("closed") else "no", esc(N.length(ln["length_mm"])) if _num(ln.get("length_mm")) else "—",
                 esc(N.area(ln["area_mm2"])) if _num(ln.get("area_mm2")) else "—"] for i, ln in enumerate(shown, 1)],
                num=(0, 2, 3)))
            if len(lines) > len(shown):
                body.append(f"<p class=\"note\">The {len(shown)} longest of {len(lines)} lines are listed.</p>")
        if e.get("warnings"):
            body.append(_ul([str(w) for w in e["warnings"]]))
    return _part("sections", body) if n else None


def _comparison(c: dict) -> str | None:
    ref = c.get("reference") if isinstance(c.get("reference"), dict) else {}
    al = c.get("alignment") if isinstance(c.get("alignment"), dict) else {}
    dev = c.get("deviation") if isinstance(c.get("deviation"), dict) else {}
    tol = c.get("tolerance") if isinstance(c.get("tolerance"), dict) else {}
    if not (ref or al or dev or tol):
        return None
    body = []
    if tol.get("sentence"):
        body.append(f"<p><strong>{esc(tol['sentence'])}</strong></p>")
    rows = []
    if ref:
        text = str(ref.get("name") or "the CAD model")
        extra = []
        if _num(ref.get("size_mm")):
            extra.append(f"{N.fixed(ref['size_mm'], 1)} mm across")
        if _num(ref.get("n_faces")):
            extra.append(f"{N.whole(ref['n_faces'])} triangles")
        rows.append(("CAD model", esc(text + (f" ({', '.join(extra)})" if extra else ""))))
        units = ref.get("units") if isinstance(ref.get("units"), dict) else None
        if units is not None and units.get("status") != "same" and units.get("message"):
            rows.append(("Its units", esc(units["message"])))
    if al:
        how = str(al.get("described") or al.get("method") or "")
        text = esc(how[:1].upper() + how[1:]) + (f". {esc(al['verdict'])}" if al.get("verdict") else "")
        method = al.get("method")
        if method in ("shape", "points+shape"):
            text += f"<div class=\"note\">{esc(BEST_FIT_NOTE)}</div>"
        elif method == "datums":
            text += f"<div class=\"note\">{esc(DATUM_NOTE)}</div>"
        vs = al.get("vs_datums") if isinstance(al.get("vs_datums"), dict) else None
        if vs and vs.get("note"):
            text += f"<div class=\"note\">{esc(vs['note'])}</div>"
        if al.get("accepted") is False:
            text = _badge("not accepted") + " " + text
        rows.append(("Aligned", text))
    if _num(tol.get("tolerance_mm")):
        t = N.nominal(tol["tolerance_mm"])
        parts = [f"within ± {t} mm: {N.percent(tol['share_within'])}" if _num(tol.get("share_within")) else None,
                 f"above (+, material outside the CAD model): {N.percent(tol['share_above'])}"
                 if _num(tol.get("share_above")) else None,
                 f"below (-, inside it): {N.percent(tol['share_below'])}" if _num(tol.get("share_below")) else None]
        rows.append(("Of the compared surface", esc("; ".join(p for p in parts if p))))
        if _num(tol.get("share_uncertain")):
            k = N.nominal(tol.get("k") or 2)
            rows.append(("Too uncertain to tell", esc(f"{N.percent(tol['share_uncertain'])} of the compared surface, where "
                                                      f"the scan's own scatter ({k} times its ±) is wider than ± {t} mm")))
        worst = []
        if _num(tol.get("worst_above_mm")):
            worst.append(f"{N.fixed(tol['worst_above_mm'], 2)} mm above")
        if _num(tol.get("worst_below_mm")):
            worst.append(f"{N.fixed(tol['worst_below_mm'], 2)} mm below")
        if worst:
            rows.append(("Furthest out", esc(", ".join(worst))))
        st = tol.get("stats") if isinstance(tol.get("stats"), dict) else {}
        if all(_num(st.get(k)) for k in ("median_mm", "robust_sigma_mm", "p05_mm", "p95_mm")):
            rows.append(("Spread", esc(f"median {N.fixed(st['median_mm'], 3)} mm, robust spread "
                                       f"{N.fixed(st['robust_sigma_mm'], 3)} mm, 90 % between "
                                       f"{N.fixed(st['p05_mm'], 3)} and {N.fixed(st['p95_mm'], 3)} mm")))
    gone = []
    if _num(dev.get("off_reference_share")):
        gone.append(f"{N.percent(dev['off_reference_share'])} of the scan is not on the CAD model (a table, clamps, or "
                    f"surface more than {N.nominal(dev.get('near_mm') or 3)} mm off) and was not compared")
    if _num(dev.get("not_measured_share")):
        gone.append(f"{N.percent(dev['not_measured_share'])} of the scan was filled in, not measured, and was not compared")
    if _num(dev.get("uncovered_share")):
        gone.append(f"{N.percent(dev['uncovered_share'])} of the CAD model was not scanned")
    if gone:
        rows.append(("Not compared", "<br>".join(esc(g[:1].upper() + g[1:] + ".") for g in gone)))
    if rows:
        body.append(_dl(rows))
    regions = [r for r in tol.get("regions") or [] if isinstance(r, dict)]
    if regions:
        body.append("<h3>The areas outside the tolerance, largest first</h3>")
        rows = []
        for i, r in enumerate(regions, 1):
            side = "Above (+)" if r.get("side") == "above" else "Below (-)"
            ex = r.get("exceeds_uncertainty")
            beyond = "yes" if ex is True else "no: within the scan's scatter" if ex is False else "not known"
            rows.append([str(i), esc(side), esc(N.area(r["area_mm2"])) if _num(r.get("area_mm2")) else "—",
                         esc(N.percent(r["share"])) if _num(r.get("share")) else "—",
                         esc(N.fixed(r["mean_mm"], 3) + " mm") if _num(r.get("mean_mm")) else "—",
                         esc(N.fixed(r["worst_mm"], 3) + " mm") if _num(r.get("worst_mm")) else "—",
                         esc(N.vector(r["center"], 1) + " mm") if _vec_ok(r.get("center")) else "—", esc(beyond)])
        body.append(_table(["#", "Side", "Area", "Share", "Mean", "Worst", "Centre (CAD model's axes)",
                            "More than the scan's scatter"], rows, num=(0, 2, 3, 4, 5, 6)))
        n = tol.get("n_regions")
        if isinstance(n, int) and n > len(regions):
            body.append(f"<p class=\"note\">The largest {len(regions)} of {n} areas are listed.</p>")
    notes = [str(x) for x in dev.get("notes") or []]
    if notes:
        body.append("<h3>Notes</h3>" + _ul(notes))
    if dev.get("rule"):
        body.append(f"<p class=\"note\">{esc(dev['rule'])}</p>")
    warns = [str(w) for w in (ref.get("warnings") or []) + (al.get("warnings") or [])]
    if warns:
        body.append("<h3>Warnings</h3>" + _ul(warns))
    return _part("comparison", body)


def _stats_rows(label: str, st: dict, d: int, sigma: float | None) -> list[tuple[str, str]]:
    if not _num(st.get("mean")):
        return [(label, "no station along the seam could be trusted")]
    pm = f" (each station ± about {N.fixed(sigma, d)} mm)" if _num(sigma) else ""

    def at(key):
        return f" at {N.fixed(st[key], 0)} mm" if _num(st.get(key)) else ""

    return [(label, esc(f"mean {N.fixed(st['mean'], d)} mm{pm}; lowest {N.fixed(st['lowest'], d)} mm{at('lowest_at_mm')}, "
                        f"highest {N.fixed(st['highest'], d)} mm{at('highest_at_mm')} along the seam"))]


def _flush(g: dict) -> str | None:
    fl = g.get("flush") if isinstance(g.get("flush"), dict) else None
    if fl is None:
        return None
    body = []
    if g.get("name"):
        body.append(f"<h3>{esc(g['name'])}</h3>")
    st = [s for s in g.get("stations") or [] if isinstance(s, dict)]
    sig = sorted(s["flush_sigma_mm"] for s in st if s.get("flush_trusted") and _num(s.get("flush_sigma_mm")))
    med = sig[len(sig) // 2] if sig else None
    d = N.decimals(med) if _num(med) else 2
    rows = _stats_rows("Flush", fl, d, med)
    rows.append(("Sign", "+ : the second clicked panel stands above the first, square to the first"))
    if isinstance(fl.get("n_stations"), int):
        step = f" every {N.nominal(g['step_mm'])} mm" if _num(g.get("step_mm")) else ""
        rows.append(("Stations", esc(f"{fl.get('n_trusted', 0)} of {fl['n_stations']} sections across the seam{step} "
                                     "trusted")))
    gap = g.get("gap") if isinstance(g.get("gap"), dict) else {}
    if g.get("gap_offered"):
        gs = sorted(s["gap_sigma_mm"] for s in st if s.get("gap_trusted") and _num(s.get("gap_sigma_mm")))
        gmed = gs[len(gs) // 2] if gs else None
        rows += _stats_rows("Gap", gap, N.decimals(gmed) if _num(gmed) else 2, gmed)
    else:
        rows.append(("Gap", esc(g.get("gap_note") or "Gap is not shown.")))
    body.append(_dl(rows))
    return _part("flush", body)


# --------------------------------------------------------------------------- the page
def build_html(data: dict) -> str:
    """The report as one self-contained HTML page (module docstring), from the plain data of
    :mod:`ohs.report.data`. The same data gives the same text; ``data`` is not changed."""
    prep = prepare(data)
    parts: list[tuple[str, str]] = [("warnings", _warnings(prep)), ("meaning", _meaning())]

    def add(pid: str, html_text: str | None) -> None:
        if html_text:
            parts.append((pid, html_text))

    if isinstance(data.get("process"), dict) or isinstance(data.get("merge"), dict):
        add("made", _made(data.get("process") if isinstance(data.get("process"), dict) else {}, data.get("merge")))
    if isinstance(data.get("calibration"), dict):
        add("calibration", _calibration(data["calibration"]))
    if isinstance(data.get("check"), dict):
        add("check", _check(data["check"]))
    add("frame", _frame(prep))
    if isinstance(data.get("pictures"), (list, tuple)):
        add("pictures", _pictures(list(data["pictures"])))
    add("features", _features(prep))
    add("relations", _relations(prep))
    if isinstance(data.get("sections"), (list, tuple)):
        add("sections", _sections(list(data["sections"])))
    if isinstance(data.get("comparison"), dict):
        add("comparison", _comparison(data["comparison"]))
    if isinstance(data.get("gap_flush"), dict):
        add("flush", _flush(data["gap_flush"]))

    scan, project = name_of(data.get("scan")), name_of(data.get("project"))
    title = "Scan report" + (f": {scan}" if scan else "") + (f" ({project})" if project else "")
    version = f"OpenHandScan {data['app_version']}" if data.get("app_version") not in (None, "") else "OpenHandScan"
    foot = version + (f", {data['date']}" if data.get("date") not in (None, "") else "")
    out = ["<!DOCTYPE html>", "<html lang=\"en\">", "<head>", "<meta charset=\"utf-8\">",
           "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; "
           "style-src 'unsafe-inline'\">",
           "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
           f"<meta name=\"generator\" content=\"{esc(version)}\">",
           f"<title>{esc(title)}</title>", f"<style>{CSS}</style>", "</head>", "<body>",
           _head(data, [p for p, _ in parts]), "<main>"]
    out += [h for _, h in parts]
    out += ["</main>", f"<footer>Made by {esc(foot)}. Every ± is 1σ, the spread from scan to scan, not the accuracy."
                       "</footer>", "</body>", "</html>", ""]
    return "\n".join(out)
