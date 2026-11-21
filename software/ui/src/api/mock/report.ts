/**
 * The demo engine's report (APP_API.md "The report"), answering the engine's three routes
 * (`POST .../report/report.html`, `features.csv`, `features.json`) with the same limits on the pictures.
 *
 * **The HTML is a small builder of the demo's own**, not the engine's (`ohs/report/html.py`): one page with the
 * project's and the scan's names and the date, the line on what a ± is, the warnings it knows of (a fit made on an
 * earlier mesh), a table of the kept fits (name, kind, the fit's main number), the kept sections by name and plane, the
 * first kept seam's mean flush (the gap not offered), and the pictures sent, embedded as `data:` images. Every text is
 * escaped; nothing is fetched and there is no script, as in the engine's page; it says that it comes from the demo. The
 * CSV has the engine's first columns (record, id, name, kind, frame, the diameter and its ±, the position, stale,
 * warnings), one row per kept fit, with a byte-order mark; the JSON is the kept fits, measurements and relations as the
 * demo keeps them, with the names and the date.
 */
import type { FeatureSet, KeptFeature } from "../types";
import { fitLabel } from "../../lib/fits.ts";
import { HttpError } from "./host.ts";

export const MAX_PICTURES = 6;
export const MAX_PICTURE_BYTES = 3 * 2 ** 20;
export const MAX_TOTAL_BYTES = 12 * 2 ** 20;
const FILES: Record<string, [string, string]> = {
  "report.html": ["-report.html", "text/html; charset=utf-8"],
  "features.csv": ["-features.csv", "text/csv; charset=utf-8"],
  "features.json": ["-features.json", "application/json"],
};

/** What the report reads of the demo engine. */
export type ReportSource = {
  names: () => { part: string; scan: string };
  hasMesh: () => boolean;
  features: () => FeatureSet | null;
  sections: () => { name: string; description: string }[];
  /** The seam shown (the one asked for, else the first kept), measured; null without one. */
  seam: (id?: string | null) => { name: string; mean: number | null; n_trusted: number; n_stations: number } | null;
};

const esc = (s: unknown) =>
  String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
const stem = (s: string) => s.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^[._-]+|[._-]+$/g, "").slice(0, 80) || "scan";
const bytesOf = (b64: string) => Math.floor((b64.replace(/[^A-Za-z0-9+/=]/g, "").length * 3) / 4) - (b64.endsWith("==") ? 2 : b64.endsWith("=") ? 1 : 0);
const isPng = (b64: string) => b64.startsWith("iVBORw0KGgo"); // the PNG signature in base64

type Picture = { title: string; caption: string | null; data: string };

function pictures(x: unknown): Picture[] {
  const items = Array.isArray(x) ? x : [];
  if (items.length > MAX_PICTURES) throw new HttpError(422, `${items.length} pictures: a report takes at most ${MAX_PICTURES}`, "Remove some of the views added to the report.");
  let total = 0;
  return items.map((p: Record<string, unknown>, i) => {
    const title = typeof p?.title === "string" ? p.title.trim() : "";
    if (!title) throw new HttpError(422, `picture ${i + 1} has no title`, "Give each picture a title.");
    const data = typeof p.data === "string" ? p.data : "";
    if (!/^[A-Za-z0-9+/]*={0,2}$/.test(data)) throw new HttpError(422, `the picture “${title}” is not base64 text`, "Send each picture's bytes in base64.");
    const n = bytesOf(data);
    if (n > MAX_PICTURE_BYTES) throw new HttpError(413, `the picture “${title}” is ${(n / 2 ** 20).toFixed(1)} MB, more than the 3.0 MB a picture may be`, "Add a smaller view.");
    total += n;
    if (total > MAX_TOTAL_BYTES) throw new HttpError(413, "the pictures come to more than the 12.0 MB a report takes in all", "Add fewer views.");
    return { title, caption: typeof p.caption === "string" && p.caption.trim() ? p.caption.trim() : null, data };
  });
}

function featureRows(f: FeatureSet | null): KeptFeature[] {
  return f?.features ?? [];
}

function html(src: ReportSource, opts: Record<string, unknown>, pics: Picture[]): string {
  const { part, scan } = src.names();
  const date = new Date().toISOString().slice(0, 10);
  const feats = featureRows(src.features());
  const warnings = feats.filter((f) => f.stale).map((f) => `${f.name}: fitted on an earlier mesh: fit it again to measure the current mesh.`);
  const out: string[] = [
    "<!DOCTYPE html>",
    '<html lang="en"><head><meta charset="utf-8">',
    `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">`,
    `<title>Scan report: ${esc(scan)} (${esc(part)})</title>`,
    "<style>body{font:14px/1.45 system-ui,sans-serif;max-width:190mm;margin:0 auto;padding:24px 16px;color:#1b1f24;background:#fff}h2{border-bottom:1px solid #ccc}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:4px 6px;text-align:left}img{max-width:100%;border:1px solid #ccc}.note{color:#555;font-size:12.5px}</style>",
    "</head><body>",
    `<header id="head"><p class="note">OpenHandScan scan report, made by the demo engine (not the engine's report)</p><h1>${esc(scan)}</h1><p>Project: ${esc(part)}</p><p>Date: ${date}</p></header>`,
    `<section id="warnings"><h2>Warnings that were not resolved</h2>${warnings.length ? `<ul>${warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : "<p>None.</p>"}</section>`,
    '<section id="meaning"><h2>What the numbers mean</h2><p>Every ± is one standard deviation of the spread from scan to scan, not the accuracy. No accuracy figure is claimed.</p></section>',
  ];
  if (typeof opts.tolerance_mm === "number")
    out.push(`<section id="check"><h2>Scan check: measured finely enough for the tolerance?</h2><p class="note">The demo does not show the Scan check at ± ${esc(opts.tolerance_mm)} mm in its report.</p></section>`);
  if (pics.length) {
    const shown = pics.filter((p) => isPng(p.data));
    const left = pics.filter((p) => !isPng(p.data));
    out.push('<section id="pictures"><h2>Views of the mesh</h2>');
    for (const p of shown) out.push(`<figure><img src="data:image/png;base64,${p.data}" alt="${esc(p.title)}"><figcaption><strong>${esc(p.title)}</strong>${p.caption ? `: ${esc(p.caption)}` : ""}</figcaption></figure>`);
    if (left.length) out.push(`<p class="note">Not shown (not a PNG image): ${esc(left.map((p) => p.title).join(", "))}.</p>`);
    out.push("</section>");
  }
  if (feats.length) {
    out.push(
      `<section id="features"><h2>Fitted shapes and measurements</h2><p class="note">${opts.numbers_in_part_frame ? "Asked for along the part's axes; the demo shows the scan's own axes." : "Positions along the scan's own axes."}</p><table><thead><tr><th>Name</th><th>Kind</th><th>Size</th></tr></thead><tbody>`,
    );
    for (const f of feats) out.push(`<tr><td>${esc(f.name)}</td><td>${esc(f.kind)}</td><td>${esc(fitLabel(f.kind, f.shape))}</td></tr>`);
    out.push("</tbody></table></section>");
  }
  const secs = src.sections();
  if (secs.length) out.push(`<section id="sections"><h2>Sections</h2><ul>${secs.map((s) => `<li><strong>${esc(s.name)}</strong>: ${esc(s.description)}</li>`).join("")}</ul></section>`);
  const seam = src.seam(typeof opts.seam === "string" ? opts.seam : null);
  if (seam)
    out.push(
      `<section id="flush"><h2>Flush along a seam</h2><h3>${esc(seam.name)}</h3><p>Flush: mean ${seam.mean === null ? "not read" : `${seam.mean.toFixed(2)} mm`} (${seam.n_trusted} of ${seam.n_stations} stations trusted).</p><p>Gap: not offered.</p></section>`,
    );
  out.push(`<footer class="note">Made by the OpenHandScan demo engine, ${date}. Every ± is 1σ, the spread from scan to scan, not the accuracy.</footer></body></html>`, "");
  return out.join("\n");
}

function csv(src: ReportSource): string {
  const cols = ["record", "id", "name", "kind", "frame", "diameter_mm", "diameter_sigma_mm", "x_mm", "y_mm", "z_mm", "stale", "warnings"];
  const cell = (x: unknown) => {
    if (x === null || x === undefined || x === false) return "";
    if (x === true) return "yes";
    if (typeof x === "number") return String(x);
    const s = String(x);
    const safe = /^[=+\-@\t\r]/.test(s) ? `'${s}` : s;
    return /[",\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
  };
  const rows = [cols.join(",")];
  for (const f of featureRows(src.features())) {
    const s = f.shape as { point?: number[]; center?: number[]; diameter_mm?: number; diameter_sigma_mm?: number };
    const p = s.center ?? s.point ?? [];
    rows.push(["feature", f.id, f.name, f.kind, "scan", s.diameter_mm ?? null, s.diameter_sigma_mm ?? null, p[0], p[1], p[2], f.stale, f.warnings.join(" | ")].map(cell).join(","));
  }
  return "﻿" + rows.join("\r\n") + "\r\n";
}

export class MockReport {
  /** `POST /api/projects/{pid}/scans/{sid}/report/{name}`; `rest` is what follows "report". */
  async route(method: string, rest: string[], body: Record<string, unknown>, src: ReportSource): Promise<Response | undefined> {
    if (method !== "POST" || rest.length !== 1) return undefined;
    const name = rest[0];
    const file = FILES[name];
    if (!file) throw new HttpError(404, `no report file “${name}”`, "Available: report.html, features.csv, features.json.");
    const t = body.tolerance_mm;
    if (t !== undefined && t !== null && !(typeof t === "number" && Number.isFinite(t) && t > 0 && t <= 100))
      throw new HttpError(422, `the tolerance must be above 0 mm, got ${JSON.stringify(t)}`, "Give the tolerance in mm, e.g. 0.3.");
    const { scan, part } = src.names();
    let text: string;
    if (name === "report.html") text = html(src, src.hasMesh() ? body : { ...body, tolerance_mm: undefined }, pictures(body.pictures));
    else if (name === "features.csv") text = csv(src);
    else {
      const f = src.features();
      text =
        JSON.stringify(
          {
            format: "OpenHandScan kept features",
            format_version: 1,
            project: part,
            scan,
            date: new Date().toISOString().slice(0, 10),
            app_version: "demo",
            units: { length: "mm", angle: "deg" },
            plus_minus: "1σ: the spread from scan to scan, not the accuracy",
            mesh_version: f?.mesh_version ?? null,
            frame: f?.frame ?? null,
            features: f?.features ?? [],
            measurements: f?.measurements ?? [],
            relations: f?.relations ?? [],
            sections: src.sections(),
          },
          null,
          1,
        ) + "\n";
    }
    return new Response(text, { status: 200, headers: { "content-type": file[1], "content-disposition": `attachment; filename="${stem(scan)}${file[0]}"` } });
  }
}
