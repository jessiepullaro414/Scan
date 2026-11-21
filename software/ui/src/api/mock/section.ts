/**
 * The demo engine's sections (APP_API.md "Sections"), like the engine's `ohs/server/sections.py`: the current mesh cut
 * by a plane named one of four ways, the cut's DXF and SVG, and the sections kept per scan (in memory here), listed,
 * renamed, deleted and cut again when opened.
 *
 * **The cut is a simple one** (`cutMesh`), not the engine's: each vertex is on the + side of the plane when its signed
 * distance is 0 or more, so every triangle with corners on both sides gives one segment, from the point where its
 * "+ to −" edge crosses the plane to where its "− to +" edge does (the engine's direction rule, so an outline runs
 * counter-clockwise seen from the normal's side), the crossing on an edge computed from that edge's two ends. The
 * segments are joined where they share an edge (by the edge's two vertex numbers), so a mesh whose vertices are welded
 * gives whole lines, closed where they come back round; lines are listed longest first. It does not handle the
 * engine's special cases (a plane through vertices or along edges, unwelded meshes, more than two segments at a point)
 * and the demo has no filled faces, so every stretch is measured. The plane and its drawing axes are the engine's rule
 * (lib/section.ts), so the drawing reads the same way as with the engine.
 *
 * The answers have the engine's shape (compact or full, `max_points`, `truncated`, the totals of the whole cut); the
 * files are a real DXF R12 (one POLYLINE per line) and SVG at 1:1, each saying it comes from the demo.
 */
import type { KeptSection, PartFrameRecord, SectionAnswer, SectionDefinition, SectionLineCompact, SectionLineFull, SectionPlane, SectionPlaneDef } from "../types";
import { readFrame } from "../../lib/partFrame.ts";
import { planeFromDef, type PlaneFit } from "../../lib/section.ts";
import { HttpError, json } from "./host.ts";

type Vec3 = [number, number, number];
export type SectionMesh = { positions: ArrayLike<number>; indices: ArrayLike<number>; sigma?: ArrayLike<number> | null };
/** One line of the demo's cut: 2D points in the plane's axes, the same in 3D, closed or open, each point's sigma. */
export type MockLine = { points: [number, number][]; points3d: Vec3[]; closed: boolean; sigma: number[] | null; faces: number[] };

/** What the sections need of the demo engine. */
export type SectionEngine = {
  /** The current mesh's version, null before there is a mesh. */
  version: () => number | null;
  mesh: () => SectionMesh | null;
  /** The part's (project's) and the scan's names, for the download names. */
  names?: () => { part: string; scan: string };
  /** The scan's kept fits (with the mesh version each was fitted on) and its part's frame. */
  kept: () => { features: (PlaneFit & { mesh_version: number })[]; frame: PartFrameRecord | null };
};

const MAX_POINTS = 20_000;
const KINDS = ["fit", "frame", "point_normal", "points"];
const NAME_MAX = 80;
const dot = (a: ArrayLike<number>, b: ArrayLike<number>) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const bad = (detail: string, hint = "Name the plane as APP_API.md “Sections” says."): never => {
  throw new HttpError(422, detail, hint);
};
const nowIso = () => new Date().toISOString().slice(0, 19);
const newId = () => `s-${Math.random().toString(16).slice(2, 12).padEnd(10, "0")}`;
const stem = (s: string) => s.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^[._-]+|[._-]+$/g, "").slice(0, 80) || "scan";
const r3 = (x: number) => Math.round(x * 1000) / 1000;

// ---------------------------------------------------------------- the cut
export function cutMesh(m: SectionMesh, plane: SectionPlane): MockLine[] {
  const P = m.positions,
    I = m.indices,
    S = m.sigma ?? null;
  const nV = Math.floor(P.length / 3);
  const n = plane.normal,
    c = dot(plane.point, n);
  const d = new Float64Array(nV);
  for (let i = 0; i < nV; i++) d[i] = P[3 * i] * n[0] + P[3 * i + 1] * n[1] + P[3 * i + 2] * n[2] - c;
  const key = (a: number, b: number) => (a < b ? a * nV + b : b * nV + a);
  const starts: number[] = [],
    ends: number[] = [],
    faces: number[] = [];
  for (let t = 0, f = 0; t + 2 < I.length; t += 3, f++) {
    const v = [I[t], I[t + 1], I[t + 2]];
    const s = v.map((x) => d[x] >= 0);
    if (s[0] === s[1] && s[1] === s[2]) continue;
    let st = -1,
      en = -1;
    for (let j = 0; j < 3; j++) {
      const a = v[j],
        b = v[(j + 1) % 3];
      if (s[j] && !s[(j + 1) % 3]) st = key(a, b);
      else if (!s[j] && s[(j + 1) % 3]) en = key(a, b);
    }
    starts.push(st);
    ends.push(en);
    faces.push(f);
  }
  // join: the segment that starts where this one ends
  const byStart = new Map<number, number>();
  starts.forEach((k, i) => byStart.set(k, i));
  const hasPrev = new Set<number>();
  ends.forEach((k) => {
    const nx = byStart.get(k);
    if (nx !== undefined) hasPrev.add(nx);
  });
  const seen = new Uint8Array(starts.length);
  const chains: { keys: number[]; segs: number[]; closed: boolean }[] = [];
  const walk = (i0: number) => {
    const keys = [starts[i0]],
      segs: number[] = [];
    let i = i0;
    let closed = false;
    for (;;) {
      seen[i] = 1;
      segs.push(i);
      const nx = byStart.get(ends[i]);
      if (nx === i0) {
        closed = true;
        break;
      }
      keys.push(ends[i]);
      if (nx === undefined || seen[nx]) break;
      i = nx;
    }
    chains.push({ keys, segs, closed });
  };
  for (let i = 0; i < starts.length; i++) if (!seen[i] && !hasPrev.has(i)) walk(i); // open lines from their first end
  for (let i = 0; i < starts.length; i++) if (!seen[i]) walk(i); // the rest are loops
  const point = (k: number): { p: Vec3; s: number | null } => {
    const lo = Math.floor(k / nV),
      hi = k - lo * nV;
    const t = d[lo] / (d[lo] - d[hi]);
    const p = [0, 1, 2].map((j) => P[3 * lo + j] + t * (P[3 * hi + j] - P[3 * lo + j])) as Vec3;
    return { p, s: S ? S[lo] + t * (S[hi] - S[lo]) : null };
  };
  const lines = chains.map(({ keys, segs, closed }) => {
    const pts = keys.map(point);
    const points3d = pts.map((x) => x.p);
    const points = points3d.map((p) => {
      const q = [p[0] - plane.point[0], p[1] - plane.point[1], p[2] - plane.point[2]];
      return [dot(q, plane.u), dot(q, plane.v)] as [number, number];
    });
    return { points, points3d, closed, sigma: S ? pts.map((x) => x.s as number) : null, faces: segs.map((i) => faces[i]) };
  });
  return lines.sort((a, b) => lengthOf(b) - lengthOf(a));
}

function lengthOf(l: Pick<MockLine, "points" | "closed">): number {
  const P = l.points;
  let s = 0;
  for (let i = 1; i < P.length; i++) s += Math.hypot(P[i][0] - P[i - 1][0], P[i][1] - P[i - 1][1]);
  if (l.closed && P.length > 1) s += Math.hypot(P[0][0] - P[P.length - 1][0], P[0][1] - P[P.length - 1][1]);
  return s;
}

function areaOf(l: MockLine): number | null {
  if (!l.closed) return null;
  const P = l.points;
  const mx = P.reduce((s, p) => s + p[0], 0) / P.length,
    my = P.reduce((s, p) => s + p[1], 0) / P.length;
  let a = 0;
  for (let i = 0; i < P.length; i++) {
    const [x0, y0] = P[i],
      [x1, y1] = P[(i + 1) % P.length];
    a += (x0 - mx) * (y1 - my) - (x1 - mx) * (y0 - my);
  }
  return a / 2;
}

function bboxOf(P: [number, number][]): number[] {
  const xs = P.map((p) => p[0]),
    ys = P.map((p) => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

/** The answer as the engine gives it (compact or full; the lines longest first, at most `maxPoints` when asked). */
export function sectionAnswer(
  lines: MockLine[],
  plane: SectionPlane,
  o: { version: number; definition: SectionDefinition; stale: boolean; warnings: string[]; compact: boolean; maxPoints: number | null; filenames: { dxf: string; svg: string }; kept: KeptSection | null },
): SectionAnswer<SectionLineCompact | SectionLineFull> {
  const max = o.maxPoints ?? (o.compact ? MAX_POINTS : null);
  const shown: MockLine[] = [];
  let total = 0;
  for (const l of lines) {
    if (max !== null && shown.length && total + l.points.length > max) break;
    shown.push(l);
    total += l.points.length;
  }
  const nPoints = lines.reduce((s, l) => s + l.points.length, 0);
  const all = lines.flatMap((l) => l.points);
  const out = shown.map((l) => {
    const nseg = l.closed ? l.points.length : l.points.length - 1;
    const base = { closed: l.closed, length_mm: lengthOf(l), area_mm2: areaOf(l), bbox: bboxOf(l.points), stretches: [[0, nseg, false]] as [number, number, boolean][] };
    if (!o.compact)
      return { ...base, points: l.points, points3d: l.points3d, sigma: l.sigma, filled: new Array<boolean>(nseg).fill(false), faces: l.faces.slice(0, nseg) } as SectionLineFull;
    const sg = l.sigma;
    return {
      ...base,
      n_points: l.points.length,
      points: l.points.flatMap((p) => [r3(p[0]), r3(p[1])]),
      sigma_mm: sg ? { mean: sg.reduce((s, x) => s + x, 0) / sg.length, max: Math.max(...sg) } : null,
    } as SectionLineCompact;
  });
  return {
    format_version: 1,
    units: "mm",
    plane,
    lines: out,
    length_mm: lines.reduce((s, l) => s + lengthOf(l), 0),
    n_points: nPoints,
    bbox: all.length ? bboxOf(all) : null,
    n_lines: lines.length,
    n_closed: lines.filter((l) => l.closed).length,
    mesh_version: o.version,
    definition: o.definition,
    stale_fit: o.stale,
    warnings: o.warnings,
    compact: o.compact,
    truncated: shown.length === lines.length ? null : { lines_shown: shown.length, points_shown: total, lines: lines.length, points: nPoints },
    filenames: o.filenames,
    kept: o.kept,
  };
}

// ---------------------------------------------------------------- the files (from the demo, at 1:1 in mm)
function dxfText(lines: MockLine[]): string {
  const out = ["999", "OpenHandScan demo engine: a section at 1:1 in mm", "0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009", "9", "$INSUNITS", "70", "4", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"];
  for (const l of lines) {
    out.push("0", "POLYLINE", "8", "MEASURED", "66", "1", "70", l.closed ? "1" : "0");
    for (const [x, y] of l.points) out.push("0", "VERTEX", "8", "MEASURED", "10", x.toFixed(6), "20", y.toFixed(6), "30", "0.0");
    out.push("0", "SEQEND");
  }
  out.push("0", "ENDSEC", "0", "EOF");
  return out.join("\n") + "\n";
}

function svgText(lines: MockLine[], title: string): string {
  const all = lines.flatMap((l) => l.points);
  const [x0, y0, x1, y1] = all.length ? bboxOf(all) : [0, 0, 0, 0];
  const M = 10;
  const W = Math.max(x1 - x0 + 2 * M, 80),
    H = y1 - y0 + 2 * M;
  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const paths = lines.map((l) => `<path d="M ${l.points.map(([x, y]) => `${(x - x0 + M).toFixed(4)} ${(y1 + M - y).toFixed(4)}`).join(" L ")}${l.closed ? " Z" : ""}" stroke="#000000" stroke-width="0.25" fill="none"/>`);
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    `<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="${W.toFixed(4)}mm" height="${H.toFixed(4)}mm" viewBox="0 0 ${W.toFixed(4)} ${H.toFixed(4)}">`,
    `<title>${esc(title)}</title>`,
    "<desc>From the OpenHandScan demo engine: a section at 1:1, in millimetres.</desc>",
    ...paths,
    "</svg>",
  ].join("\n");
}

// ---------------------------------------------------------------- the definition, as the engine checks it
function num(x: unknown, what: string): number {
  const v = typeof x === "string" && x.trim() !== "" ? Number(x) : x;
  if (typeof v !== "number" || !Number.isFinite(v)) return bad(`${what} must be a finite number, got ${String(x)}`);
  return v;
}
function vec(x: unknown, what: string, n = 3): number[] {
  const a = typeof x === "string" ? x.split(",").filter((s) => s.trim()) : x;
  if (!Array.isArray(a) || a.length !== n) return bad(`${what} must be ${n} numbers, got ${JSON.stringify(x)}`);
  return a.map((v) => num(v, what));
}

/** The plane's definition from a body or a query (strings), checked as the engine checks it. */
export function definitionOf(b: Record<string, unknown>): SectionPlaneDef {
  const kind = b.kind;
  if (typeof kind !== "string" || !KINDS.includes(kind)) return bad(`kind must be one of ${KINDS.join(", ")}, got ${String(kind)}`);
  if (kind === "fit") {
    if (typeof b.fit !== "string" || !b.fit) return bad("A section on a saved plane needs that fitted shape.");
    return { kind, fit: b.fit, offset_mm: b.offset_mm === undefined || b.offset_mm === null ? 0 : num(b.offset_mm, "offset_mm") };
  }
  if (kind === "frame") {
    const a = typeof b.axis === "string" ? b.axis.trim().toUpperCase().replace(/^\+/, "") : "";
    if (!/^-?[XYZ]$/.test(a)) return bad(`axis must be X, Y or Z (with an optional '-'), got ${String(b.axis)}`);
    if (b.value_mm === undefined || b.value_mm === null) return bad("A section along the part's axes needs its value in mm.");
    return { kind, axis: a, value_mm: num(b.value_mm, "value_mm") };
  }
  if (kind === "point_normal") {
    if (b.point === undefined || b.normal === undefined) return bad("a section through a point needs a point and a normal");
    return { kind, point: vec(b.point, "point"), normal: vec(b.normal, "normal") };
  }
  let pts = b.points;
  if (typeof pts === "string") {
    const f = vec(pts, "points", 9);
    pts = [f.slice(0, 3), f.slice(3, 6), f.slice(6, 9)];
  }
  if (!Array.isArray(pts) || pts.length !== 3) return bad("a section through three points needs three points of three numbers");
  return { kind: "points", points: pts.map((p, i) => vec(p, `point ${i + 1}`)) };
}

type KeptRecord = Omit<KeptSection, "mesh_changed">;

export class MockSections {
  private sets = new Map<string, KeptRecord[]>();

  private list(key: string): KeptRecord[] {
    let s = this.sets.get(key);
    if (!s) this.sets.set(key, (s = []));
    return s;
  }

  /** A scan's kept sections (for the report, report.ts). */
  kept(key: string): KeptRecord[] {
    return this.list(key);
  }

  private mesh(engine: SectionEngine): { version: number; mesh: SectionMesh } {
    const version = engine.version();
    const mesh = engine.mesh();
    if (version === null || !mesh) throw new HttpError(409, "This scan has no mesh yet", "Make the mesh first.");
    return { version, mesh };
  }

  /** The plane a definition names, with the definition as kept, whether its fit is stale, and the warnings. */
  private plane(def: SectionPlaneDef, engine: SectionEngine, version: number) {
    const { features, frame } = engine.kept();
    const pf = frame ? readFrame(frame) : null;
    const full: SectionDefinition = { ...def };
    const warnings: string[] = [];
    let stale = false;
    if (def.kind === "fit") {
      const f = features.find((x) => x.id === def.fit);
      if (!f) throw new HttpError(404, `No fitted shape “${def.fit}”`, "Read the list again.");
      full.fit_name = f.name;
      full.fit_mesh_version = f.mesh_version;
      stale = f.mesh_version !== version;
      if (stale) warnings.push(`"${f.name}" was fitted on an earlier mesh (version ${f.mesh_version}, the mesh is now version ${version}): its plane is used as it was kept; fit it again for this mesh's plane`);
    }
    try {
      return { plane: planeFromDef(def, features, pf), full, stale, warnings };
    } catch (e) {
      return bad((e as Error).message, "Name another plane.");
    }
  }

  private filenames(engine: SectionEngine, label: string) {
    const base = `${stem(engine.names?.().scan ?? "scan")}-${stem(label)}`;
    return { dxf: `${base}.dxf`, svg: `${base}.svg` };
  }

  private label(d: SectionDefinition): string {
    if (d.kind === "fit") return `section-${d.fit_name ?? d.fit}${d.offset_mm ? `-offset-${d.offset_mm < 0 ? "minus" : ""}${Math.abs(d.offset_mm)}mm` : ""}`;
    if (d.kind === "frame") return `section-${d.axis.replace("-", "")}${d.value_mm}mm${d.axis.startsWith("-") ? "-from-minus" : ""}`;
    return "section";
  }

  private view(r: KeptRecord, version: number | null): KeptSection {
    return { ...r, mesh_changed: version === null || r.mesh_version !== version };
  }

  private find(key: string, id: string): number {
    const i = this.list(key).findIndex((x) => x.id === id);
    if (i < 0) throw new HttpError(404, `No kept section “${id}”`, "Read the list again.");
    return i;
  }

  private file(name: string, lines: MockLine[], title: string, filenames: { dxf: string; svg: string }, version: number): Response {
    if (name !== "section.dxf" && name !== "section.svg") throw new HttpError(404, `No section file “${name}”`, "Available: section.dxf, section.svg.");
    const dxf = name === "section.dxf";
    return new Response(dxf ? dxfText(lines) : svgText(lines, title), {
      status: 200,
      headers: { "content-type": dxf ? "application/dxf" : "image/svg+xml", "content-disposition": `attachment; filename="${dxf ? filenames.dxf : filenames.svg}"`, "X-OHS-Mesh-Version": String(version) },
    });
  }

  /** `/api/projects/{pid}/scans/{sid}/section[/...]` and `.../sections[/...]`; `rest` is what follows the word. */
  route(method: string, key: string, word: "section" | "sections", rest: string[], body: Record<string, unknown>, engine: SectionEngine): Response | undefined {
    const is = (m: string, n: number) => method === m && rest.length === n;
    const compact = body.compact === true || body.compact === "true";
    const maxPoints = body.max_points === undefined || body.max_points === null ? null : num(body.max_points, "max_points");
    if (maxPoints !== null && maxPoints < 1) bad("max_points must be at least 1");
    if (word === "section") {
      if (is("POST", 0) || is("GET", 1)) {
        const def = definitionOf(body);
        const { version, mesh } = this.mesh(engine);
        const p = this.plane(def, engine, version);
        const lines = cutMesh(mesh, p.plane);
        const filenames = this.filenames(engine, this.label(p.full));
        if (method === "GET") return this.file(rest[0], lines, p.plane.description, filenames, version);
        return json(200, sectionAnswer(lines, p.plane, { version, definition: p.full, stale: p.stale, warnings: p.warnings, compact, maxPoints, filenames, kept: null }));
      }
      return undefined;
    }
    const list = this.list(key);
    if (is("GET", 0)) {
      const v = engine.version();
      return json(200, { mesh_version: v, sections: list.map((r) => this.view(r, v)) });
    }
    if (is("POST", 0)) {
      const def = definitionOf(body);
      let name: string | null = null;
      if (body.name !== undefined && body.name !== null) {
        name = typeof body.name === "string" ? body.name.trim() : "";
        if (!name || name.length > NAME_MAX) bad(`name must be 1 to ${NAME_MAX} characters`, `Give a name of 1 to ${NAME_MAX} characters.`);
      }
      const { version } = this.mesh(engine);
      const p = this.plane(def, engine, version);
      let top = list.length;
      for (const r of list) {
        const m = /^Section (\d+)$/.exec(r.name);
        if (m) top = Math.max(top, Number(m[1]));
      }
      const rec: KeptRecord = { id: newId(), name: name ?? `Section ${top + 1}`, plane: p.plane, definition: p.full, mesh_version: version, created: nowIso() };
      list.push(rec);
      return json(200, this.view(rec, version));
    }
    const id = rest[0];
    if (id && (is("GET", 1) || is("GET", 2))) {
      const rec = list[this.find(key, id)];
      const { version, mesh } = this.mesh(engine);
      const lines = cutMesh(mesh, rec.plane);
      const view = this.view(rec, version);
      const filenames = this.filenames(engine, `section-${rec.name}`);
      if (rest.length === 2) return this.file(rest[1], lines, rec.plane.description, filenames, version);
      const warnings = view.mesh_changed ? [`the mesh has changed since this section was kept (it was cut on version ${rec.mesh_version}, the mesh is now version ${version}): this is the cut of the current mesh`] : [];
      return json(200, sectionAnswer(lines, rec.plane, { version, definition: rec.definition, stale: false, warnings, compact, maxPoints, filenames, kept: view }));
    }
    if (id && is("PATCH", 1)) {
      const i = this.find(key, id);
      if (body.name === undefined && !body.recut) bad("give a new name, or recut: true");
      if (body.name !== undefined) {
        const name = typeof body.name === "string" ? body.name.trim() : "";
        if (!name || name.length > NAME_MAX) bad(`name must be 1 to ${NAME_MAX} characters`, `Give a name of 1 to ${NAME_MAX} characters.`);
        list[i] = { ...list[i], name };
      }
      if (body.recut) list[i] = { ...list[i], mesh_version: this.mesh(engine).version };
      return json(200, this.view(list[i], engine.version()));
    }
    if (id && is("DELETE", 1)) {
      list.splice(this.find(key, id), 1);
      return json(204, null);
    }
    return undefined;
  }
}
