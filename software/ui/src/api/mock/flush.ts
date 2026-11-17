/**
 * The demo engine's flush along a seam (APP_API.md "Flush along a seam"), like the engine's `ohs/server/flush.py`: the
 * step between two panels from two clicks, its stations as CSV, and the seams kept per scan (in memory here), listed,
 * renamed, deleted and measured again when opened.
 *
 * **The reading is a simple one** (`mockFlush`), not the engine's (`ohs/inspect/gapflush.py`, which follows a curved
 * seam, fits a line to each panel's surface off its rounded edge and weighs each point by its sigma): the seam is taken
 * as straight, across the line between the two clicks and square to the first panel's normal there (the mean of the
 * faces' normals within 5 mm of the first click). The mesh's vertices are sorted once into slabs 3 mm wide along the
 * seam; in each slab the vertices within 6 mm (across the seam) of each click's line are that panel's points, and the
 * flush is the mean height of the second panel's points above the first's, along the first panel's normal, with the
 * standard error of both means as its ±. A slab with fewer than six points on a side marks that side "missing" and
 * its flush not trusted; the stations run out each way after three empty slabs. The gap is not read (null), and the
 * answer says it is not offered, with the engine's reason, as the engine does.
 *
 * The answers have the engine's shape; the CSV has the engine's columns and a byte-order mark.
 */
import type { FlushRecord, FlushStation, KeptSeam, SeamStats } from "../types";
import { HttpError, json } from "./host.ts";

type Vec3 = [number, number, number];
export type FlushMesh = { positions: ArrayLike<number>; indices: ArrayLike<number>; sigma?: ArrayLike<number> | null };

/** What the seams need of the demo engine. */
export type FlushEngine = {
  /** The current mesh's version, null before there is a mesh. */
  version: () => number | null;
  mesh: () => FlushMesh | null;
  /** The part's (project's) and the scan's names, for the download names. */
  names?: () => { part: string; scan: string };
};

/** The engine's words (ohs.inspect.gapflush GAP_NOTE). */
export const GAP_NOTE =
  "Gap is not shown: the scan closes a narrow gap over a dark background (the stereo depth runs on across it), so the panels' edges are not measured. On simulated seams of 2 to 6 mm its reading was 1 to 2 mm off, and differently for each gap size. Flush is read from the two surfaces and is shown.";
const STEP_MM = 3;
const WINDOW_MM = 6; // across the seam, round each click's line
const MIN_POINTS = 6;
const CLICK_REACH_MM = 3;
const MAX_MISSES = 3;
const NAME_MAX = 80;
const CSV_COLUMNS = ["position_mm", "x_mm", "y_mm", "z_mm", "flush_mm", "flush_sigma_mm", "flush_trusted", "side_a", "side_b", "bridged", "notes"];

const sub = (a: ArrayLike<number>, b: ArrayLike<number>): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a: ArrayLike<number>, b: ArrayLike<number>) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: ArrayLike<number>, b: ArrayLike<number>): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (a: Vec3): Vec3 => {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
};
const bad = (detail: string, hint = "Click once on each panel, a few millimetres from the seam."): never => {
  throw new HttpError(422, detail, hint);
};
const nowIso = () => new Date().toISOString().slice(0, 19);
const newId = () => `sm-${Math.random().toString(16).slice(2, 12).padEnd(10, "0")}`;
const stem = (s: string) => s.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^[._-]+|[._-]+$/g, "").slice(0, 80) || "scan";

/** A click from a body (three numbers) or a query ("x,y,z"). */
function clickOf(x: unknown, what: string): Vec3 {
  const a = typeof x === "string" ? x.split(",").filter((s) => s.trim()) : x;
  if (!Array.isArray(a) || a.length !== 3) return bad(`${what} must be three numbers (x, y, z in mm), got ${JSON.stringify(x)}`);
  const v = a.map((t) => (typeof t === "string" && t.trim() !== "" ? Number(t) : t));
  if (!v.every((t) => typeof t === "number" && Number.isFinite(t))) return bad(`${what} must be three finite numbers, got ${JSON.stringify(x)}`);
  return v as Vec3;
}

/** The first panel's normal: the area-weighted mean of the faces' normals within 5 mm of the click. */
function normalAt(m: FlushMesh, a: Vec3): Vec3 | null {
  const P = m.positions,
    I = m.indices;
  const n: Vec3 = [0, 0, 0];
  for (let t = 0; t + 2 < I.length; t += 3) {
    const i = 3 * I[t],
      j = 3 * I[t + 1],
      k = 3 * I[t + 2];
    const c = [(P[i] + P[j] + P[k]) / 3, (P[i + 1] + P[j + 1] + P[k + 1]) / 3, (P[i + 2] + P[j + 2] + P[k + 2]) / 3];
    if (Math.hypot(c[0] - a[0], c[1] - a[1], c[2] - a[2]) > 5) continue;
    const f = cross([P[j] - P[i], P[j + 1] - P[i + 1], P[j + 2] - P[i + 2]], [P[k] - P[i], P[k + 1] - P[i + 1], P[k + 2] - P[i + 2]]);
    n[0] += f[0];
    n[1] += f[1];
    n[2] += f[2];
  }
  return Math.hypot(...n) > 0 ? unit(n) : null;
}

function stats(st: FlushStation[]): SeamStats {
  const t = st.filter((s) => s.flush_trusted && s.flush_mm !== null);
  if (!t.length) return { lowest: null, lowest_at_mm: null, mean: null, highest: null, highest_at_mm: null, n_trusted: 0, n_stations: st.length };
  let lo = t[0],
    hi = t[0],
    sum = 0;
  for (const s of t) {
    if (s.flush_mm! < lo.flush_mm!) lo = s;
    if (s.flush_mm! > hi.flush_mm!) hi = s;
    sum += s.flush_mm!;
  }
  return { lowest: lo.flush_mm, lowest_at_mm: lo.position_mm, mean: sum / t.length, highest: hi.flush_mm, highest_at_mm: hi.position_mm, n_trusted: t.length, n_stations: st.length };
}

/** The demo's flush record for two clicks (module docstring). Throws HttpError 422 for clicks it cannot use. */
export function mockFlush(m: FlushMesh, a: Vec3, b: Vec3): FlushRecord {
  const P = m.positions,
    S = m.sigma ?? null;
  const nV = Math.floor(P.length / 3);
  for (const [c, name] of [
    [a, "first"],
    [b, "second"],
  ] as const) {
    let best = Infinity;
    for (let i = 0; i < nV; i++) best = Math.min(best, Math.hypot(P[3 * i] - c[0], P[3 * i + 1] - c[1], P[3 * i + 2] - c[2]));
    if (!(best <= CLICK_REACH_MM)) bad(`the ${name} click is ${best.toFixed(1)} mm from the mesh: click on the panel's surface`);
  }
  if (Math.hypot(...sub(b, a)) < 2) bad("the two clicks are too close together: click one on each panel, either side of the seam");
  const n = normalAt(m, a);
  if (!n) return bad("no faces around the first click");
  const ab = sub(b, a);
  const acrossRaw: Vec3 = [ab[0] - dot(ab, n) * n[0], ab[1] - dot(ab, n) * n[1], ab[2] - dot(ab, n) * n[2]];
  if (Math.hypot(...acrossRaw) < 1) bad("the clicks lie one above the other: click one on each panel, either side of the seam");
  const d = unit(acrossRaw);
  const t = unit(cross(n, d));
  const p0: Vec3 = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2];
  const sa = dot(sub(a, p0), d),
    sb = dot(sub(b, p0), d);
  // each vertex once: its slab along the seam, which panel's window it is in, its height
  type Acc = { n: number; h: number; hh: number; s2: number };
  const slabs = new Map<number, { A: Acc; B: Acc }>();
  for (let i = 0; i < nV; i++) {
    const q = sub([P[3 * i], P[3 * i + 1], P[3 * i + 2]], p0);
    const across = dot(q, d);
    const side = Math.abs(across - sa) <= WINDOW_MM ? "A" : Math.abs(across - sb) <= WINDOW_MM ? "B" : null;
    if (!side) continue;
    const h = dot(q, n);
    if (Math.abs(h - (side === "A" ? dot(sub(a, p0), n) : dot(sub(b, p0), n))) > 10) continue; // far off the panel's height
    const k = Math.round(dot(q, t) / STEP_MM);
    let e = slabs.get(k);
    if (!e) slabs.set(k, (e = { A: { n: 0, h: 0, hh: 0, s2: 0 }, B: { n: 0, h: 0, hh: 0, s2: 0 } }));
    const acc = e[side];
    acc.n++;
    acc.h += h;
    acc.hh += h * h;
    const s = S ? Number(S[i]) : 0;
    acc.s2 += Number.isFinite(s) && s < 1 ? s * s : 0;
  }
  const stations: FlushStation[] = [];
  const station = (k: number): FlushStation | null => {
    const e = slabs.get(k);
    if (!e || (e.A.n < MIN_POINTS && e.B.n < MIN_POINTS)) return null;
    const mean = (x: Acc) => x.h / x.n;
    const se = (x: Acc) => Math.sqrt(Math.max(0, x.hh / x.n - mean(x) ** 2) / x.n + x.s2 / x.n / x.n);
    const okA = e.A.n >= MIN_POINTS,
      okB = e.B.n >= MIN_POINTS;
    const flush = okA && okB ? mean(e.B) - mean(e.A) : null;
    const sigma = okA && okB ? Math.max(1e-4, Math.hypot(se(e.A), se(e.B))) : null;
    const hA = okA ? mean(e.A) : mean(e.B);
    const mid = (sa + sb) / 2;
    const pos = k * STEP_MM;
    const point: number[] = [0, 1, 2].map((j) => p0[j] + pos * t[j] + mid * d[j] + hA * n[j]);
    const notes: string[] = [];
    if (!okA) notes.push("The first panel has no surface to fit here.");
    if (!okB) notes.push("The second panel has no surface to fit here.");
    return {
      position_mm: pos,
      point,
      direction: [...t],
      normal: [...n],
      gap_mm: null,
      gap_sigma_mm: null,
      flush_mm: flush,
      flush_sigma_mm: sigma,
      edge_a: null,
      edge_b: null,
      side_a: okA ? "measured" : "missing",
      side_b: okB ? "measured" : "missing",
      bridged: false,
      gap_trusted: false,
      flush_trusted: flush !== null,
      notes,
      gap_by_rule: {},
      n_points_a: e.A.n,
      n_points_b: e.B.n,
      rms_a_mm: null,
      rms_b_mm: null,
    };
  };
  const first = station(0);
  if (!first || first.flush_mm === null) bad("no seam found between the clicks: each panel needs some measured surface between its click and the seam");
  stations.push(first!);
  for (const dir of [1, -1]) {
    let misses = 0;
    for (let k = dir; Math.abs(k) < 1000 && misses < MAX_MISSES; k += dir) {
      const s = station(k);
      if (!s) {
        misses++;
        continue;
      }
      misses = 0;
      stations.push(s);
    }
  }
  stations.sort((x, y) => x.position_mm - y.position_mm);
  const flush = stats(stations);
  return {
    format_version: 1,
    units: "mm",
    rule: "demo",
    step_mm: STEP_MM,
    flush_sign: "+ : the second clicked panel stands above the first, square to the first",
    gap: { lowest: null, lowest_at_mm: null, mean: null, highest: null, highest_at_mm: null, n_trusted: 0, n_stations: stations.length },
    flush,
    gap_offered: false,
    gap_note: GAP_NOTE,
    correlated_points: 1,
    clicks: [[...a], [...b]],
    stations,
  };
}

function untrusted(rec: FlushRecord): string | null {
  const f = rec.flush;
  return f.n_stations > 0 && f.n_trusted < f.n_stations
    ? `the flush is not trusted at ${f.n_stations - f.n_trusted} of ${f.n_stations} stations along the seam (a surface missing, or a reading far from its neighbours): they are left out of the lowest, mean and highest`
    : null;
}

/** The stations as CSV (the engine's columns), with a byte-order mark. */
export function stationsCsv(rec: FlushRecord): string {
  const cell = (x: unknown) => {
    if (x === null || x === undefined) return "";
    if (typeof x === "boolean") return x ? "yes" : "no";
    if (typeof x === "number") return String(x);
    const s = String(x);
    const safe = /^[=+\-@\t\r]/.test(s) ? `'${s}` : s;
    return /[",\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
  };
  const rows = [CSV_COLUMNS.join(",")];
  for (const s of rec.stations)
    rows.push([s.position_mm, s.point[0], s.point[1], s.point[2], s.flush_mm, s.flush_sigma_mm, s.flush_trusted, s.side_a, s.side_b, s.bridged, s.notes.join(" | ")].map(cell).join(","));
  return "﻿" + rows.join("\r\n") + "\r\n";
}

type KeptRecord = Omit<KeptSeam, "mesh_changed">;

export class MockFlush {
  private sets = new Map<string, KeptRecord[]>();

  private list(key: string): KeptRecord[] {
    let s = this.sets.get(key);
    if (!s) this.sets.set(key, (s = []));
    return s;
  }

  /** A scan's kept seams (for the report, report.ts). */
  kept(key: string): KeptRecord[] {
    return this.list(key);
  }

  private mesh(engine: FlushEngine): { version: number; mesh: FlushMesh } {
    const version = engine.version();
    const mesh = engine.mesh();
    if (version === null || !mesh) throw new HttpError(409, "This scan has no mesh yet", "Make the mesh first.");
    return { version, mesh };
  }

  private filename(engine: FlushEngine, name: string | null): string {
    return `${stem(engine.names?.().scan ?? "scan")}-flush${name ? `-${stem(name)}` : ""}.csv`;
  }

  private view(r: KeptRecord, version: number | null): KeptSeam {
    return { ...r, mesh_changed: version === null || r.mesh_version !== version };
  }

  private find(key: string, id: string): number {
    const i = this.list(key).findIndex((x) => x.id === id);
    if (i < 0) throw new HttpError(404, `No kept seam “${id}”`, "Read the list again.");
    return i;
  }

  private nameOf(x: unknown): string {
    const n = typeof x === "string" ? x.trim() : "";
    if (!n || n.length > NAME_MAX) bad(`name must be 1 to ${NAME_MAX} characters`, `Give a name of 1 to ${NAME_MAX} characters.`);
    return n;
  }

  /** A kept seam measured on the current mesh, with its warnings (for the route and the report). */
  measureKept(key: string, id: string, engine: FlushEngine): { record: FlushRecord; version: number; view: KeptSeam; warnings: string[] } {
    const rec = this.list(key)[this.find(key, id)];
    const { version, mesh } = this.mesh(engine);
    const record = mockFlush(mesh, rec.a, rec.b);
    const view = this.view(rec, version);
    const warnings: string[] = [];
    if (view.mesh_changed)
      warnings.push(`the mesh has changed since this seam was kept (it was measured on version ${rec.mesh_version}, the mesh is now version ${version}): this is the flush of the current mesh`);
    const u = untrusted(record);
    if (u) warnings.push(u);
    return { record, version, view, warnings };
  }

  private csv(text: string, filename: string, version: number): Response {
    return new Response(text, { status: 200, headers: { "content-type": "text/csv; charset=utf-8", "content-disposition": `attachment; filename="${filename}"`, "X-OHS-Mesh-Version": String(version) } });
  }

  /** `/api/projects/{pid}/scans/{sid}/flush[/...]` and `.../seams[/...]`; `rest` is what follows the word. */
  route(method: string, key: string, word: "flush" | "seams", rest: string[], body: Record<string, unknown>, engine: FlushEngine): Response | undefined {
    const is = (m: string, n: number) => method === m && rest.length === n;
    if (word === "flush") {
      if (is("POST", 0) || is("GET", 1)) {
        if (method === "GET" && rest[0] !== "stations.csv") throw new HttpError(404, `No flush file “${rest[0]}”`, "Available: stations.csv.");
        const a = clickOf(body.a, "a (the click on the first panel)"),
          b = clickOf(body.b, "b (the click on the second panel)");
        const { version, mesh } = this.mesh(engine);
        const record = mockFlush(mesh, a, b);
        if (method === "GET") return this.csv(stationsCsv(record), this.filename(engine, null), version);
        const u = untrusted(record);
        return json(200, { ...record, mesh_version: version, warnings: u ? [u] : [], filename: this.filename(engine, null), kept: null });
      }
      return undefined;
    }
    const list = this.list(key);
    if (is("GET", 0)) {
      const v = engine.version();
      return json(200, { mesh_version: v, seams: list.map((r) => this.view(r, v)) });
    }
    if (is("POST", 0)) {
      const a = clickOf(body.a, "a (the click on the first panel)"),
        b = clickOf(body.b, "b (the click on the second panel)");
      const name = body.name === undefined || body.name === null ? null : this.nameOf(body.name);
      const { version } = this.mesh(engine);
      let top = list.length;
      for (const r of list) {
        const m = /^Seam (\d+)$/.exec(r.name);
        if (m) top = Math.max(top, Number(m[1]));
      }
      const rec: KeptRecord = { id: newId(), name: name ?? `Seam ${top + 1}`, a, b, mesh_version: version, created: nowIso() };
      list.push(rec);
      return json(200, this.view(rec, version));
    }
    const id = rest[0];
    if (id && (is("GET", 1) || is("GET", 2))) {
      if (rest.length === 2 && rest[1] !== "stations.csv") throw new HttpError(404, `No flush file “${rest[1]}”`, "Available: stations.csv.");
      const { record, version, view, warnings } = this.measureKept(key, id, engine);
      const filename = this.filename(engine, view.name);
      if (rest.length === 2) return this.csv(stationsCsv(record), filename, version);
      return json(200, { ...record, mesh_version: version, warnings, filename, kept: view });
    }
    if (id && is("PATCH", 1)) {
      const i = this.find(key, id);
      if (body.name === undefined && !body.mark_current) bad("give a new name, or mark_current: true", "Send {“name”: …} or {“mark_current”: true}.");
      if (body.name !== undefined) list[i] = { ...list[i], name: this.nameOf(body.name) };
      if (body.mark_current) list[i] = { ...list[i], mesh_version: this.mesh(engine).version };
      return json(200, this.view(list[i], engine.version()));
    }
    if (id && is("DELETE", 1)) {
      list.splice(this.find(key, id), 1);
      return json(204, null);
    }
    return undefined;
  }
}
