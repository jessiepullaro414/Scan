/**
 * The demo engine's references and comparison (APP_API.md "References and the comparison with one"), like the
 * engine's `ohs/server/reference.py`, with the same routes and answers, kept in memory:
 *
 * - **References** are uploaded as the raw body with the name in the query: the name's last part only, the extension
 *   says the format (STL binary or ASCII, OBJ, PLY; anything else refused in the engine's words), an empty body 422,
 *   above `maxBytes` 413. The corners of an STL are welded. The display copy is the mesh as read (the demo does not
 *   decimate). A project with a processed scan and no reference is given one the first time its list is read: "Demo
 *   part (from the scan).stl", the first such scan's own surface with one end left out and a round patch moved 0.6 mm
 *   out, so the panel can be tried without a file at hand.
 * - **Units:** the engine's rule (`check_units`) on simpler sizes: each mesh's size is the diagonal of its axis-aligned
 *   box, and the scan's part is the whole scan (the demo does not set a table aside).
 * - **Aligning:** from the shape (a job of a few seconds) the demo moves the scan's box centre onto the reference's,
 *   without turning it (the demo part needs no move on the scan it was made from); from points, the centres of the
 *   picked points are brought together, without turning (refined: as a job); on the datums, the kept part frame's
 *   matrix, refused without one (422) or with one built from a fit that is gone (409).
 * - **The deviation:** the reference's surface is sampled (about 200,000 points at most, each with its face's normal) and
 *   each scan vertex measured to the nearest sample along that normal; it is compared when that sample is within 3 mm
 *   and faces within 70° of the vertex's normal, else it is not on the reference; a vertex without a usable sigma is not
 *   measured. **The answer for a tolerance** is the engine's rule: shares by area of the compared faces (each the mean
 *   of its corners), the areas outside joined over shared corners, each with its size, mean, worst and whether it
 *   exceeds k times the scan's sigma.
 */
import type {
  Comparison,
  ComparisonCoverage,
  Job,
  PartFrameRecord,
  ReferenceAlignment,
  ReferenceModel,
  RefDeviationInfo,
  ToleranceAnswer,
  ToleranceRegion,
  UnitsFinding,
} from "../types";
import { encodeFaces } from "../../lib/fits.ts";
import { parsePly } from "../../three/plyParse.ts";
import { plyMesh } from "./geom.ts";
import { HttpError, json, type WorkJobSpec, type WorkStep } from "./host.ts";

type Mesh = { positions: Float32Array; indices: Uint32Array };
type Vec3 = [number, number, number];
type M4 = number[][];

/** What the comparison needs of the demo engine, for one scan. */
export type CompareEngine = {
  /** The current mesh's version, null before there is a mesh. */
  version: () => number | null;
  mesh: () => { positions: ArrayLike<number>; indices: ArrayLike<number>; sigma?: ArrayLike<number> | null } | null;
  /** The scan's kept part frame (as features.json keeps it), or null. */
  frame: () => PartFrameRecord | null;
  /** The ids of the scan's kept fits (a frame built from one that is gone is out of date). */
  featureIds: () => string[];
  startWorkJob: (kind: string, spec: WorkJobSpec) => Job;
};

const NEAR_MM = 3;
const NORMAL_COS = Math.cos((70 * Math.PI) / 180);
const SIGMA_NONE_MM = 50;
const SAMPLES_MAX = 200_000;
const NAME_MAX = 120;
const UNITS: [string, string, number][] = [
  ["inch", "inches", 25.4],
  ["cm", "centimetres", 10],
  ["m", "metres", 1000],
  ["µm", "micrometres", 0.001],
];
const nowIso = () => new Date().toISOString().slice(0, 19);
const newId = () => `ref-${Math.random().toString(16).slice(2, 12).padEnd(10, "0")}`;
const bad = (detail: string, hint: string): never => {
  throw new HttpError(422, detail, hint);
};
const g3 = (x: number) => Number(x.toPrecision(3));

// ---------------------------------------------------------------- reading a file
/** The file's name as the engine keeps it: its last part only (never a path). */
export function cleanName(name: unknown): string {
  if (typeof name !== "string" || !name.trim()) bad("the file's name is missing", "send it as ?name=part.stl: its extension says the format");
  const base = (name as string).split(/[\\/:]/).pop()!.replace(/\s+/g, " ").trim();
  if (!base.replace(/\./g, "")) bad(`${JSON.stringify(name)} is not a file name`, "send the file's own name, e.g. ?name=part.stl");
  if (base.length > NAME_MAX) bad(`the file's name is too long (${base.length} characters)`, `use at most ${NAME_MAX} characters`);
  return base;
}

function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i).toLowerCase() : "";
}

/** A mesh file's triangles: binary or ASCII STL (its corners welded), OBJ (polygons as fans), PLY. Throws an Error in words. */
export function readMeshFile(buf: ArrayBuffer, name: string): Mesh {
  const ext = extOf(name);
  if (ext === ".ply") {
    const p = parsePly(buf);
    if (!p || !p.indices.length) throw new Error("it has no triangles (a point cloud, or a PLY the demo cannot read)");
    return { positions: p.positions, indices: p.indices };
  }
  const corners: number[] = [];
  const faces: number[] = [];
  if (ext === ".stl") {
    const dv = new DataView(buf);
    const n = buf.byteLength >= 84 ? dv.getUint32(80, true) : -1;
    if (n >= 0 && buf.byteLength === 84 + 50 * n) {
      for (let t = 0; t < n; t++) for (let k = 0; k < 3; k++) for (let c = 0; c < 3; c++) corners.push(dv.getFloat32(84 + 50 * t + 12 + 12 * k + 4 * c, true));
    } else {
      const text = new TextDecoder().decode(buf);
      for (const m of text.matchAll(/vertex\s+(\S+)\s+(\S+)\s+(\S+)/g)) corners.push(Number(m[1]), Number(m[2]), Number(m[3]));
      if (corners.length % 9) throw new Error("its triangles are not whole");
    }
    // weld: one vertex per position
    const index = new Map<string, number>();
    const pos: number[] = [];
    for (let i = 0; i < corners.length; i += 3) {
      const key = `${corners[i]},${corners[i + 1]},${corners[i + 2]}`;
      let v = index.get(key);
      if (v === undefined) {
        v = pos.length / 3;
        index.set(key, v);
        pos.push(corners[i], corners[i + 1], corners[i + 2]);
      }
      faces.push(v);
    }
    if (!faces.length) throw new Error("it holds no triangles");
    return { positions: new Float32Array(pos), indices: new Uint32Array(faces) };
  }
  // OBJ
  const pos: number[] = [];
  for (const line of new TextDecoder().decode(buf).split(/\r?\n/)) {
    const w = line.trim().split(/\s+/);
    if (w[0] === "v") pos.push(Number(w[1]), Number(w[2]), Number(w[3]));
    else if (w[0] === "f") {
      const ids = w.slice(1).map((x) => {
        const i = parseInt(x.split("/")[0], 10);
        return i < 0 ? pos.length / 3 + i : i - 1;
      });
      for (let k = 1; k + 1 < ids.length; k++) faces.push(ids[0], ids[k], ids[k + 1]);
    }
  }
  if (!faces.length) throw new Error("it holds no faces");
  return { positions: new Float32Array(pos), indices: new Uint32Array(faces) };
}

// ---------------------------------------------------------------- small geometry
function box(P: ArrayLike<number>, used?: ArrayLike<number>): { lo: Vec3; hi: Vec3 } {
  const lo: Vec3 = [Infinity, Infinity, Infinity];
  const hi: Vec3 = [-Infinity, -Infinity, -Infinity];
  const visit = (i: number) => {
    for (let k = 0; k < 3; k++) {
      lo[k] = Math.min(lo[k], P[3 * i + k]);
      hi[k] = Math.max(hi[k], P[3 * i + k]);
    }
  };
  if (used) for (let t = 0; t < used.length; t++) visit(used[t]);
  else for (let i = 0; i < P.length / 3; i++) visit(i);
  return { lo, hi };
}
const diag = (b: { lo: Vec3; hi: Vec3 }) => Math.hypot(b.hi[0] - b.lo[0], b.hi[1] - b.lo[1], b.hi[2] - b.lo[2]);

function faceNormals(P: ArrayLike<number>, I: ArrayLike<number>): { n: Float32Array; area: Float64Array } {
  const nF = I.length / 3;
  const n = new Float32Array(nF * 3);
  const area = new Float64Array(nF);
  for (let f = 0; f < nF; f++) {
    const a = I[3 * f] * 3,
      b = I[3 * f + 1] * 3,
      c = I[3 * f + 2] * 3;
    const ux = P[b] - P[a],
      uy = P[b + 1] - P[a + 1],
      uz = P[b + 2] - P[a + 2];
    const vx = P[c] - P[a],
      vy = P[c + 1] - P[a + 1],
      vz = P[c + 2] - P[a + 2];
    const x = uy * vz - uz * vy,
      y = uz * vx - ux * vz,
      z = ux * vy - uy * vx;
    const l = Math.hypot(x, y, z);
    area[f] = l / 2;
    if (l > 0) {
      n[3 * f] = x / l;
      n[3 * f + 1] = y / l;
      n[3 * f + 2] = z / l;
    }
  }
  return { n, area };
}

function vertexNormals(P: ArrayLike<number>, I: ArrayLike<number>): Float32Array {
  const { n, area } = faceNormals(P, I);
  const out = new Float32Array(P.length);
  for (let f = 0; f < I.length / 3; f++)
    for (let k = 0; k < 3; k++) for (let c = 0; c < 3; c++) out[I[3 * f + k] * 3 + c] += n[3 * f + c] * area[f];
  for (let i = 0; i < out.length; i += 3) {
    const l = Math.hypot(out[i], out[i + 1], out[i + 2]) || 1;
    out[i] /= l;
    out[i + 1] /= l;
    out[i + 2] /= l;
  }
  return out;
}

const apply = (M: M4, p: ArrayLike<number>, o = 0): Vec3 => [0, 1, 2].map((r) => M[r][0] * p[o] + M[r][1] * p[o + 1] + M[r][2] * p[o + 2] + M[r][3]) as Vec3;
const turn = (M: M4, p: ArrayLike<number>, o = 0): Vec3 => [0, 1, 2].map((r) => M[r][0] * p[o] + M[r][1] * p[o + 1] + M[r][2] * p[o + 2]) as Vec3;
const eye = (): M4 => [0, 1, 2, 3].map((r) => [0, 1, 2, 3].map((c) => (r === c ? 1 : 0)));
/** The inverse of a rigid motion. */
function invRigid(T: M4): M4 {
  const R = [0, 1, 2].map((r) => [0, 1, 2].map((c) => T[c][r]));
  const t = [0, 1, 2].map((r) => -(R[r][0] * T[0][3] + R[r][1] * T[1][3] + R[r][2] * T[2][3]));
  return [...R.map((row, r) => [...row, t[r]]), [0, 0, 0, 1]];
}
const scaled = (M: M4, s: number): M4 => M.map((row, r) => (r < 3 ? [row[0] * s, row[1] * s, row[2] * s, row[3]] : [...row]));

// ---------------------------------------------------------------- units (the engine's check_units)
export function checkUnits(ref: number, scan: number): UnitsFinding {
  const r = scan / ref;
  const fits = (f: number) => r / f >= 0.3 && r / f <= 1.5;
  const base = { ratio: r, reference_size_mm: ref, scan_size_mm: scan };
  if (fits(1)) {
    let message = `The reference and the scan's part are of a size (${g3(ref)} and ${g3(scan)} mm across): both taken as mm.`;
    if (r > 1 / 0.85) message += ` The scan is ${r.toPrecision(2)} times the reference's size: is there more than the part in it (a clamp, a second part)?`;
    return { status: "same", factor: null, unit: null, candidates: [], message, ...base };
  }
  const cands = UNITS.filter(([, , f]) => fits(f)).map(([unit, , factor]) => ({ unit, factor }));
  if (cands.length) {
    const best = cands.reduce((a, b) => (Math.abs(Math.log(r / b.factor / 0.85)) < Math.abs(Math.log(r / a.factor / 0.85)) ? b : a));
    const words = Object.fromEntries(UNITS.map(([u, w]) => [u, w]));
    let message =
      r >= 1
        ? `The scan's part is ${g3(r)} times the size of the reference: the reference looks to be in ${words[best.unit]}`
        : `The reference is ${g3(1 / r)} times the size of the scan's part: the reference looks to be in ${words[best.unit]}`;
    const others = cands.filter((c) => c !== best).map((c) => words[c.unit]);
    if (others.length) message += ` (or ${others.join(", ")}, if the scan covers less of the part)`;
    message += ` (1 ${best.unit} = ${best.factor} mm). Confirm to scale it by ${best.factor}, or keep it as it is${r < 1 ? " if the scan covers only a small part of it" : ""}.`;
    return { status: "pending", factor: best.factor, unit: best.unit, candidates: cands, message, ...base };
  }
  if (r > 4 || r < 0.25) {
    const [big, small] = r > 1 ? ["scan's part", "reference"] : ["reference", "scan's part"];
    const message = `The ${big} is ${g3(Math.max(r, 1 / r))} times the size of the ${small}, and no unit explains it: check that this is the right reference (or that the scan covers only a small part of it), then confirm to compare it as it is.`;
    return { status: "pending", factor: null, unit: null, candidates: [], message, ...base };
  }
  return { status: "same", factor: null, unit: null, candidates: [], message: `The reference and the scan's part are ${g3(ref)} and ${g3(scan)} mm across: taken as mm (the scan covers part of the reference, or holds more than the part).`, ...base };
}

// ---------------------------------------------------------------- the deviation
type MockDeviation = { version: number; key: string; values: Float32Array; states: Uint8Array; info: RefDeviationInfo; sigma: ArrayLike<number> | null; P: ArrayLike<number>; I: ArrayLike<number>; coverage: ComparisonCoverage };

function cellKey(x: number, y: number, z: number): number {
  return ((x * 73856093) ^ (y * 19349663) ^ (z * 83492791)) >>> 0;
}

/** Points spread over a mesh (the faces' corners too), each with its face's normal, about `spacing` apart. */
function samples(P: ArrayLike<number>, I: ArrayLike<number>): { S: Float32Array; N: Float32Array } {
  const { n, area } = faceNormals(P, I);
  const total = area.reduce((a, b) => a + b, 0);
  const h = Math.max(0.5, Math.sqrt(total / SAMPLES_MAX));
  const S: number[] = [];
  const N: number[] = [];
  let seed = 7;
  const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let f = 0; f < I.length / 3; f++) {
    const k = Math.round(area[f] / (h * h));
    const a = I[3 * f] * 3,
      b = I[3 * f + 1] * 3,
      c = I[3 * f + 2] * 3;
    const put = (u: number, v: number) => {
      for (let j = 0; j < 3; j++) S.push(P[a + j] + u * (P[b + j] - P[a + j]) + v * (P[c + j] - P[a + j]));
      N.push(n[3 * f], n[3 * f + 1], n[3 * f + 2]);
    };
    put(0, 0);
    put(1, 0);
    put(0, 1);
    for (let i = 0; i < k; i++) {
      let u = rand(),
        v = rand();
      if (u + v > 1) [u, v] = [1 - u, 1 - v];
      put(u, v);
    }
  }
  return { S: new Float32Array(S), N: new Float32Array(N) };
}

function grid(P: ArrayLike<number>, cell: number): Map<number, number[]> {
  const g = new Map<number, number[]>();
  for (let i = 0; i < P.length / 3; i++) {
    const k = cellKey(Math.floor(P[3 * i] / cell), Math.floor(P[3 * i + 1] / cell), Math.floor(P[3 * i + 2] / cell));
    const l = g.get(k);
    if (l) l.push(i);
    else g.set(k, [i]);
  }
  return g;
}

function nearest(g: Map<number, number[]>, S: ArrayLike<number>, cell: number, p: ArrayLike<number>, o: number, maxD: number): number {
  const cx = Math.floor(p[o] / cell),
    cy = Math.floor(p[o + 1] / cell),
    cz = Math.floor(p[o + 2] / cell);
  let best = -1,
    bd = maxD * maxD;
  for (let dx = -1; dx <= 1; dx++)
    for (let dy = -1; dy <= 1; dy++)
      for (let dz = -1; dz <= 1; dz++) {
        const l = g.get(cellKey(cx + dx, cy + dy, cz + dz));
        if (!l) continue;
        for (const j of l) {
          const d = (S[3 * j] - p[o]) ** 2 + (S[3 * j + 1] - p[o + 1]) ** 2 + (S[3 * j + 2] - p[o + 2]) ** 2;
          if (d <= bd) {
            bd = d;
            best = j;
          }
        }
      }
  return best;
}

// ---------------------------------------------------------------- the store
type StoredRef = { rec: ReferenceModel & { file?: never }; mesh: Mesh; fromScan: string | null };
type StoredCmp = {
  reference_id: string;
  units: UnitsFinding;
  scale: number;
  alignment: ReferenceAlignment | null;
  aligned_mesh_version: number | null;
  tolerance_mm: number;
  k: number;
  created: string;
  modified: string;
  aligning: string | null;
};

export class MockReferences {
  /** The largest body taken (as the engine's). */
  maxBytes = 100 * 2 ** 20;
  private refs = new Map<string, StoredRef[]>();
  private seeded = new Set<string>();
  private cmps = new Map<string, StoredCmp>();
  private devs = new Map<string, MockDeviation>();

  private list(pid: string): StoredRef[] {
    let l = this.refs.get(pid);
    if (!l) this.refs.set(pid, (l = []));
    return l;
  }

  private find(pid: string, rid: string): StoredRef {
    const r = this.list(pid).find((x) => x.rec.id === rid);
    if (!r) throw new HttpError(404, `no reference ${JSON.stringify(rid)} in this project`, "read the list again (GET .../references)");
    return r;
  }

  private add(pid: string, name: string, bytes: number, mesh: Mesh, fromScan: string | null = null): ReferenceModel {
    const b = box(mesh.positions, mesh.indices);
    const sides = [0, 1, 2].map((k) => b.hi[k] - b.lo[k]).sort((x, y) => y - x) as Vec3;
    const { area } = faceNormals(mesh.positions, mesh.indices);
    const edges = new Map<number, number>();
    const nV = mesh.positions.length / 3;
    for (let t = 0; t < mesh.indices.length; t += 3)
      for (let k = 0; k < 3; k++) {
        const a = mesh.indices[t + k],
          c = mesh.indices[t + ((k + 1) % 3)];
        const key = Math.min(a, c) * nV + Math.max(a, c);
        edges.set(key, (edges.get(key) ?? 0) + 1);
      }
    const closed = [...edges.values()].every((x) => x === 2);
    const rec: ReferenceModel = {
      id: newId(),
      name,
      format: extOf(name).slice(1) as ReferenceModel["format"],
      bytes,
      triangles: mesh.indices.length / 3,
      vertices: nV,
      extent_mm: sides,
      size_mm: Math.hypot(...sides),
      area_mm2: area.reduce((x, y) => x + y, 0),
      bounds: [b.lo, b.hi],
      closed,
      orientation: "wound as it came (the demo engine does not check the winding)",
      dropped_faces: 0,
      warnings: [],
      display: { triangles: mesh.indices.length / 3, decimated: false },
      added: nowIso(),
    };
    this.list(pid).push({ rec: rec as StoredRef["rec"], mesh, fromScan });
    return rec;
  }

  /** The demo part: the scan's own surface, its last 12 % along its longest side left out, a round patch moved 0.6 mm out. */
  private seed(pid: string, from: () => (Mesh & { sid: string }) | null): void {
    if (this.seeded.has(pid) || this.list(pid).length) return;
    const m = from();
    if (!m) return;
    this.seeded.add(pid);
    const b = box(m.positions);
    const axis = [0, 1, 2].reduce((a, k) => (b.hi[k] - b.lo[k] > b.hi[a] - b.lo[a] ? k : a), 0);
    const cut = b.lo[axis] + 0.88 * (b.hi[axis] - b.lo[axis]);
    const N = vertexNormals(m.positions, m.indices);
    const c: Vec3 = [0, 1, 2].map((k) => (b.lo[k] + b.hi[k]) / 2) as Vec3;
    // the patch: round the vertex nearest the box's centre
    let near = 0,
      nd = Infinity;
    for (let i = 0; i < m.positions.length / 3; i++) {
      const d = Math.hypot(m.positions[3 * i] - c[0], m.positions[3 * i + 1] - c[1], m.positions[3 * i + 2] - c[2]);
      if (d < nd) [near, nd] = [i, d];
    }
    const r = 0.06 * diag(b);
    const keep: number[] = [];
    for (let t = 0; t < m.indices.length; t += 3) {
      const mid = (m.positions[m.indices[t] * 3 + axis] + m.positions[m.indices[t + 1] * 3 + axis] + m.positions[m.indices[t + 2] * 3 + axis]) / 3;
      if (mid < cut) keep.push(m.indices[t], m.indices[t + 1], m.indices[t + 2]);
    }
    const remap = new Int32Array(m.positions.length / 3).fill(-1);
    const pos: number[] = [];
    const idx = keep.map((v) => {
      if (remap[v] < 0) {
        remap[v] = pos.length / 3;
        const d = Math.hypot(m.positions[3 * v] - m.positions[3 * near], m.positions[3 * v + 1] - m.positions[3 * near + 1], m.positions[3 * v + 2] - m.positions[3 * near + 2]);
        const lift = d < r ? 0.6 : 0;
        for (let k = 0; k < 3; k++) pos.push(m.positions[3 * v + k] + lift * N[3 * v + k]);
      }
      return remap[v];
    });
    const mesh = { positions: new Float32Array(pos), indices: new Uint32Array(idx) };
    this.add(pid, "Demo part (from the scan).stl", 84 + 50 * (idx.length / 3), mesh, m.sid);
  }

  /** `/api/projects/{pid}/references[/...]`; `rest` starts with "references". `raw`: the body's bytes (an upload). */
  routeReferences(method: string, pid: string, rest: string[], body: Record<string, unknown>, raw: ArrayBuffer | null, seedFrom: () => (Mesh & { sid: string }) | null): Response | undefined {
    const [, rid, file] = rest;
    const is = (m: string, n: number) => method === m && rest.length === n;
    if (is("GET", 1)) {
      this.seed(pid, seedFrom);
      return json(200, { references: this.list(pid).map((r) => r.rec) });
    }
    if (is("POST", 1)) {
      const name = cleanName(body.name);
      const ext = extOf(name);
      if (![".stl", ".obj", ".ply"].includes(ext)) {
        const hint = [".step", ".stp", ".iges", ".igs"].includes(ext) ? " (export a STEP or other CAD file as STL first)" : "";
        bad(`a reference must be an STL, OBJ or PLY mesh; ${name} is ${ext || "without an extension"}${hint}`, "export the part from CAD as STL (binary), OBJ or PLY, and add that file");
      }
      const bytes = raw?.byteLength ?? 0;
      if (bytes > this.maxBytes) throw new HttpError(413, `the file is larger than ${this.maxBytes / 2 ** 20} MB, the most a reference may be`, "export it with coarser triangles, as binary STL rather than ASCII");
      if (!bytes) bad("the file is empty", "send the file's bytes as the request body");
      let mesh: Mesh;
      try {
        mesh = readMeshFile(raw!, name);
      } catch (e) {
        return bad(`${name} could not be read as ${ext.slice(1).toUpperCase()}: ${(e as Error).message}`, "export the part from CAD as STL (binary), OBJ or PLY, and add that file");
      }
      return json(200, this.add(pid, name, bytes, mesh));
    }
    if (rid && is("PATCH", 2)) {
      const r = this.find(pid, rid);
      const n = typeof body.name === "string" ? body.name.replace(/\s+/g, " ").trim() : "";
      if (!n || n.length > NAME_MAX) bad("the name is empty or too long", `type a name of 1 to ${NAME_MAX} characters`);
      r.rec = { ...r.rec, name: n };
      return json(200, r.rec);
    }
    if (rid && is("DELETE", 2)) {
      this.find(pid, rid);
      this.refs.set(
        pid,
        this.list(pid).filter((x) => x.rec.id !== rid),
      );
      for (const [key, c] of this.cmps)
        if (key.startsWith(`${pid}/`) && c.reference_id === rid) {
          this.cmps.delete(key);
          this.devs.delete(key);
        }
      return json(204, null);
    }
    if (rid && file === "mesh.ply" && is("GET", 3)) {
      const r = this.find(pid, rid);
      const body = plyMesh({ positions: r.mesh.positions, normals: vertexNormals(r.mesh.positions, r.mesh.indices), indices: r.mesh.indices });
      return new Response(body, { status: 200, headers: { "content-type": "application/octet-stream" } });
    }
    return undefined;
  }

  // ---------------------------------------------------------------- the comparison
  private view(pid: string, _key: string, c: StoredCmp, version: number | null): Comparison {
    const ref = this.list(pid).find((x) => x.rec.id === c.reference_id)?.rec ?? null;
    const vm = c.alignment ? scaled(invRigid(c.alignment.T), c.scale).map((row, r) => (r < 3 ? row : [0, 0, 0, 1])) : null;
    return {
      format_version: 1,
      reference_id: c.reference_id,
      reference: ref,
      units: c.units,
      scale: c.scale,
      ready: c.units.status !== "pending",
      alignment: c.alignment,
      aligned_mesh_version: c.aligned_mesh_version,
      mesh_version: version,
      mesh_changed: !!c.alignment && version !== null && c.aligned_mesh_version !== version,
      view_matrix: vm,
      tolerance_mm: c.tolerance_mm,
      k: c.k,
      created: c.created,
      modified: c.modified,
      aligning: c.aligning,
    } as Comparison & { key?: string };
  }

  private load(key: string): StoredCmp {
    const c = this.cmps.get(key);
    if (!c) throw new HttpError(404, "this scan has no comparison with a reference", "start one: POST .../comparison with a reference of the project");
    return c;
  }

  private ready(c: StoredCmp): void {
    if (c.units.status === "pending") throw new HttpError(409, `the reference's units are not settled: ${c.units.message}`, "apply the factor or keep the units first (POST .../comparison/units)");
  }

  private alignment(method: ReferenceAlignment["method"], T: M4, verdict: string, extra: Partial<ReferenceAlignment> = {}): ReferenceAlignment {
    const described = { shape: "best fit on the shapes", points: `from ${extra.n_points ?? 0} picked point pairs`, "points+shape": `from ${extra.n_points ?? 0} picked point pairs, refined on the shapes`, datums: "on the part's datums, no best fit", given: "at a given pose" }[method];
    return { format_version: 1, method, described, accepted: true, verdict, T, pair: null, points_rms_mm: null, n_points: 0, frame: null, fit: null, turn_check: null, vs_datums: null, warnings: [], ms: 0, ...extra };
  }

  private vsDatums(T: M4, frame: PartFrameRecord | null, P: ArrayLike<number>): ReferenceAlignment["vs_datums"] {
    if (!frame) return null;
    let s = 0,
      mx = 0,
      n = 0;
    for (let i = 0; i < P.length; i += 3 * 97) {
      const a = apply(T, P, i),
        b = apply(frame.matrix, P, i);
      const d = Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
      s += d * d;
      mx = Math.max(mx, d);
      n++;
    }
    const rms = Math.sqrt(s / Math.max(n, 1));
    const note = `The best fit lies ${rms.toFixed(2)} mm (RMS; at most ${mx.toFixed(2)} mm) from the alignment on the datums. A best fit spreads an overall error (a part too long, bent or shifted) over the whole part; on the datums it shows where it is.`;
    return { rms_mm: rms, max_mm: mx, shift_mm: rms, angle_deg: 0, note };
  }

  private currentFrame(e: CompareEngine): { frame: PartFrameRecord | null; outOfDate: string[] } {
    const frame = e.frame();
    if (!frame) return { frame: null, outOfDate: [] };
    const ids = new Set(e.featureIds());
    const gone = frame.built_from.filter((s) => s.id && !ids.has(s.id)).map((s) => `"${s.name ?? s.id}" it was built from was deleted`);
    return { frame, outOfDate: gone };
  }

  /** `/api/projects/{pid}/scans/{sid}/comparison[/...]`; `rest` starts with "comparison". */
  routeComparison(method: string, pid: string, sid: string, rest: string[], body: Record<string, unknown>, e: CompareEngine): Response | undefined {
    const key = `${pid}/${sid}`;
    const [, a] = rest;
    const is = (m: string, n: number) => method === m && rest.length === n;
    if (is("GET", 1)) {
      const c = this.cmps.get(key);
      return json(200, { comparison: c ? this.view(pid, key, c, e.version()) : null, mesh_version: e.version() });
    }
    if (is("POST", 1)) {
      if (typeof body.reference_id !== "string") bad("invalid request: reference_id: Field required", "check the request body against APP_API.md");
      const tol = body.tolerance_mm;
      if (tol !== undefined && tol !== null && !(typeof tol === "number" && tol > 0 && tol <= 100)) bad("invalid request: tolerance_mm: must be above 0 and at most 100", "check the request body against APP_API.md");
      const ref = this.find(pid, body.reference_id as string);
      const m = e.mesh();
      if (!m) throw new HttpError(409, "this scan has no mesh yet", "run Track (or Make mesh) first");
      const units = checkUnits(ref.rec.size_mm, diag(box(m.positions)));
      const now = nowIso();
      const c: StoredCmp = { reference_id: ref.rec.id, units, scale: 1, alignment: null, aligned_mesh_version: null, tolerance_mm: typeof tol === "number" ? tol : 0.3, k: 2, created: now, modified: now, aligning: null };
      this.cmps.set(key, c);
      this.devs.delete(key);
      return json(200, this.view(pid, key, c, e.version()));
    }
    if (is("DELETE", 1)) {
      this.load(key);
      this.cmps.delete(key);
      this.devs.delete(key);
      return json(204, null);
    }
    if (a === "units" && is("POST", 2)) {
      const c = this.load(key);
      if (body.action === "apply") {
        const f = body.factor ?? c.units.factor;
        if (typeof f !== "number" || !(f > 0)) bad("there is no unit factor to apply: the sizes fit no unit", "give the factor, or keep the units");
        c.units = { ...c.units, status: "applied", factor: f as number, message: `Scaled by ${f}${c.units.unit && c.units.factor === f ? ` (${c.units.unit} to mm)` : ""} as confirmed.` };
        c.scale = f as number;
      } else if (body.action === "keep") {
        c.units = { ...c.units, status: "kept", message: "Kept as it is (in mm), as confirmed." };
        c.scale = 1;
      } else bad(`invalid request: action: Input should be 'apply' or 'keep'`, "check the request body against APP_API.md");
      c.alignment = null;
      c.aligned_mesh_version = null;
      c.modified = nowIso();
      this.devs.delete(key);
      return json(200, this.view(pid, key, c, e.version()));
    }
    if (a === "align" && is("POST", 2)) return json(200, this.align(pid, key, body, e));
    if ((a === "deviation.json" || a === "deviation.bin") && is("GET", 2)) {
      const d = this.deviation(pid, key, e);
      if (a === "deviation.json") return json(200, d.info);
      const buf = new ArrayBuffer(d.values.length * 5);
      new Float32Array(buf, 0, d.values.length).set(d.values);
      new Uint8Array(buf, d.values.length * 4).set(d.states);
      return new Response(buf, { status: 200, headers: { "content-type": "application/octet-stream", "X-OHS-Mesh-Version": String(d.version) } });
    }
    if (a === "tolerance" && is("POST", 2)) {
      const tol = body.tolerance_mm;
      if (typeof tol !== "number" || !(tol > 0) || tol > 100) bad("invalid request: tolerance_mm: must be above 0 and at most 100", "check the request body against APP_API.md");
      const c = this.load(key);
      const d = this.deviation(pid, key, e);
      c.tolerance_mm = tol as number;
      return json(200, this.tolerance(c, d, tol as number));
    }
    return undefined;
  }

  private align(pid: string, key: string, body: Record<string, unknown>, e: CompareEngine): { job: Job | null; comparison: Comparison | null } {
    const c = this.load(key);
    this.ready(c);
    const method = body.method;
    if (method !== "shape" && method !== "points" && method !== "datums") return bad(`invalid request: method: Input should be 'shape', 'points' or 'datums'`, "check the request body against APP_API.md");
    if (c.aligning) throw new HttpError(409, "this scan is being aligned with the reference already", "wait for it, or cancel it");
    const m = e.mesh();
    const version = e.version();
    if (!m || version === null) throw new HttpError(409, "this scan has no mesh yet", "make its mesh first");
    const ref = this.find(pid, c.reference_id);
    const keep = (al: ReferenceAlignment) => {
      const cur = this.cmps.get(key);
      if (!cur || cur.reference_id !== c.reference_id || cur.scale !== c.scale) return false;
      cur.alignment = al;
      cur.aligned_mesh_version = version;
      cur.modified = nowIso();
      this.devs.delete(key);
      return true;
    };
    const fr = this.currentFrame(e);
    if (method === "datums") {
      if (!fr.frame) bad("this scan has no part's frame (coordinate system) to align on", "build the coordinate system from three kept fits first, or align from the shape");
      if (fr.outOfDate.length) throw new HttpError(409, `the part's frame is out of date (${fr.outOfDate.join("; ")})`, "build the coordinate system again for the fits as they are now");
      keep(this.alignment("datums", fr.frame!.matrix, "Aligned on the datums: the scan's part frame put onto the reference's own axes; no best fit, so an overall error of the part shows where it is.", { frame: fr.frame as unknown as Record<string, unknown> }));
      return { job: null, comparison: this.view(pid, key, this.cmps.get(key)!, version) };
    }
    let T: M4 = eye();
    let extra: Partial<ReferenceAlignment> = {};
    if (method === "points") {
      const pairs = Array.isArray(body.pairs) ? (body.pairs as { scan: Vec3; reference: Vec3 }[]) : [];
      if (pairs.length < 3) bad(`${pairs.length} pair(s) of points are too few`, "pick at least three matching points on the scan and on the reference");
      const mean = (pts: Vec3[]) => [0, 1, 2].map((k) => pts.reduce((s, p) => s + p[k], 0) / pts.length) as Vec3;
      const ms = mean(pairs.map((p) => p.scan)),
        mr = mean(pairs.map((p) => p.reference));
      // a line: the spread across the line through the first and last point
      const span = pairs.map((p) => Math.hypot(p.scan[0] - ms[0], p.scan[1] - ms[1], p.scan[2] - ms[2]));
      const a0 = pairs[0].scan,
        a1 = pairs[pairs.length - 1].scan;
      const dir = [a1[0] - a0[0], a1[1] - a0[1], a1[2] - a0[2]];
      const dl = Math.hypot(...dir) || 1;
      const off = pairs.map((p) => {
        const v = [p.scan[0] - a0[0], p.scan[1] - a0[1], p.scan[2] - a0[2]];
        const t = (v[0] * dir[0] + v[1] * dir[1] + v[2] * dir[2]) / dl;
        return Math.sqrt(Math.max(0, v[0] ** 2 + v[1] ** 2 + v[2] ** 2 - t * t));
      });
      if (Math.max(...off) < 1e-3 * Math.max(...span, 1)) bad("the picked points do not give a pose: they lie in a line", "pick three or more points that span a triangle");
      T = [
        [1, 0, 0, mr[0] - ms[0]],
        [0, 1, 0, mr[1] - ms[1]],
        [0, 0, 1, mr[2] - ms[2]],
        [0, 0, 0, 1],
      ];
      const rms = Math.sqrt(pairs.reduce((s, p) => s + [0, 1, 2].reduce((q, k) => q + (p.scan[k] + T[k][3] - p.reference[k]) ** 2, 0), 0) / pairs.length);
      extra = { n_points: pairs.length, points_rms_mm: rms, warnings: rms > 2 ? [`the picked pairs agree only to ${rms.toFixed(1)} mm RMS: a pair may be wrong or in the wrong order`] : [] };
      if (body.refine === false) {
        keep(this.alignment("points", T, `Aligned from ${pairs.length} picked pairs: they agree to ${rms.toFixed(2)} mm RMS; not refined on the shapes, so the result is as good as the picks.`, extra));
        return { job: null, comparison: this.view(pid, key, this.cmps.get(key)!, version) };
      }
    } else if (ref.fromScan !== key.split("/")[1]) {
      // the demo's best fit: the box centres together
      const bs = box(m.positions),
        br = box(ref.mesh.positions, ref.mesh.indices);
      T = [0, 1, 2].map((k) => [...[0, 1, 2].map((j) => (j === k ? 1 : 0)), ((br.lo[k] + br.hi[k]) / 2) * c.scale - (bs.lo[k] + bs.hi[k]) / 2]).concat([[0, 0, 0, 1]]);
    }
    const self = this;
    const isShape = method === "shape";
    function* work(): Generator<WorkStep, () => Record<string, unknown> | null, void> {
      yield { p: 0.1, message: "Reading the scan" };
      yield { p: 0.2, message: "Reading the reference" };
      for (let i = 0; i < 6; i++) yield { p: 0.3 + 0.1 * i, message: isShape ? "Aligning from the shapes (a best fit, a few seconds)" : "Aligning from the picked points, refined on the shapes" };
      yield { p: 0.95, message: "Keeping the alignment" };
      return () => {
        const verdict = isShape
          ? "Aligned on the shapes (best fit): 98 % of the matched surface lies within 1 mm, 0.08 mm RMS (the demo engine moves the box centres together and does not turn the scan)."
          : `Aligned from ${extra.n_points} picked pairs, refined on the shapes: 98 % of the matched surface lies within 1 mm (the demo engine does not refine).`;
        const al = self.alignment(isShape ? "shape" : "points+shape", T, verdict, { ...extra, vs_datums: fr.frame && !fr.outOfDate.length ? self.vsDatums(T, fr.frame, m!.positions) : null });
        const cur = self.cmps.get(key);
        if (cur) cur.aligning = null;
        const kept = keep(al);
        const now = self.cmps.get(key);
        return { alignment: al, kept, comparison: now ? self.view(pid, key, now, e.version()) : null, mesh_version: version, message: al.verdict };
      };
    }
    const job = e.startWorkJob("reference_align", {
      minSeconds: 2.5,
      work: work(),
      onFail: () => {
        const cur = this.cmps.get(key);
        if (cur) cur.aligning = null;
      },
      onCancel: () => {
        const cur = this.cmps.get(key);
        if (cur) cur.aligning = null;
      },
      extra: { project_id: pid, scan_id: key.split("/")[1], reference_id: c.reference_id, method },
    });
    if (job.state === "running" || job.state === "queued") c.aligning = job.id;
    return { job, comparison: null };
  }

  private deviation(pid: string, key: string, e: CompareEngine): MockDeviation {
    const c = this.load(key);
    this.ready(c);
    if (!c.alignment) throw new HttpError(409, "the scan is not aligned with the reference yet", "align it first: from the shape, on the datums or from picked points");
    const m = e.mesh();
    const version = e.version();
    if (!m || version === null) throw new HttpError(409, "this scan has no mesh yet", "make its mesh first");
    const dk = `${version}/${c.reference_id}/${c.scale}/${c.alignment.T.flat().join(",")}`;
    const kept = this.devs.get(key);
    if (kept && kept.key === dk) return kept;
    const ref = this.find(pid, c.reference_id);
    // the reference in the scan's frame
    const M = scaled(invRigid(c.alignment.T), c.scale).map((row, r) => (r < 3 ? row : [0, 0, 0, 1]));
    const R = ref.mesh.positions;
    const Rs = new Float32Array(R.length);
    for (let i = 0; i < R.length; i += 3) Rs.set(apply(M, R, i), i);
    const { S, N: SN0 } = samples(Rs, ref.mesh.indices);
    const SN = new Float32Array(SN0.length);
    for (let i = 0; i < SN0.length; i += 3) {
      const t = turn(M, SN0, i);
      const l = Math.hypot(...t) || 1;
      SN.set([t[0] / l, t[1] / l, t[2] / l], i);
    }
    const g = grid(S, NEAR_MM);
    const P = m.positions,
      I = m.indices;
    const nV = P.length / 3;
    const VN = vertexNormals(P, I);
    const sigma = m.sigma ?? null;
    const values = new Float32Array(nV).fill(NaN);
    const states = new Uint8Array(nV);
    for (let i = 0; i < nV; i++) {
      if (sigma && !(sigma[i] > 0 && sigma[i] < SIGMA_NONE_MM)) {
        states[i] = 1;
        continue;
      }
      const j = nearest(g, S, NEAR_MM, P, 3 * i, NEAR_MM + 1);
      if (j < 0) {
        states[i] = 2;
        continue;
      }
      const d = (P[3 * i] - S[3 * j]) * SN[3 * j] + (P[3 * i + 1] - S[3 * j + 1]) * SN[3 * j + 1] + (P[3 * i + 2] - S[3 * j + 2]) * SN[3 * j + 2];
      const facing = VN[3 * i] * SN[3 * j] + VN[3 * i + 1] * SN[3 * j + 1] + VN[3 * i + 2] * SN[3 * j + 2];
      if (Math.abs(d) > NEAR_MM || (facing < NORMAL_COS && Math.abs(d) > 0.3)) {
        states[i] = 2;
        continue;
      }
      values[i] = d;
    }
    // areas, and how much of the reference the compared surface covers
    const { area } = faceNormals(P, I);
    let scanA = 0,
      cmpA = 0,
      offA = 0,
      nmA = 0;
    for (let f = 0; f < I.length / 3; f++) {
      const s = [states[I[3 * f]], states[I[3 * f + 1]], states[I[3 * f + 2]]];
      scanA += area[f];
      if (s.some((x) => x === 1)) nmA += area[f];
      else if (s.every((x) => x === 0)) cmpA += area[f];
      else offA += area[f];
    }
    const compared: number[] = [];
    for (let i = 0; i < nV; i++) if (states[i] === 0) compared.push(P[3 * i], P[3 * i + 1], P[3 * i + 2]);
    const gc = grid(compared, 2);
    let covered = 0;
    for (let j = 0; j < S.length; j += 3) if (nearest(gc, compared, 2, S, j, 2) >= 0) covered++;
    const uncovered = compared.length ? 1 - covered / (S.length / 3) : 1;
    const refArea = ref.rec.area_mm2 * c.scale * c.scale;
    const n = (x: number) => (scanA > 0 ? x / scanA : 0);
    const count = (s: number) => states.reduce((k, x) => k + (x === s ? 1 : 0), 0);
    const changed = c.aligned_mesh_version !== version;
    const info: RefDeviationInfo = {
      version,
      vertices: nV,
      reference_id: c.reference_id,
      scale: c.scale,
      aligned_mesh_version: c.aligned_mesh_version,
      mesh_changed: changed,
      note: changed
        ? `The mesh has changed since the scan was aligned (version ${c.aligned_mesh_version}, now ${version}): the pose is kept, since the scan's frame is the same, and the deviation is worked out again on the current mesh.`
        : null,
      layout: "float32 per vertex (mm, NaN where not compared), then one state byte per vertex",
      states: ["compared", "not measured", "not on the reference"],
      method: c.alignment.method,
      n_vertices: nV,
      n_compared: count(0),
      n_not_measured: count(1),
      n_off_reference: count(2),
      n_along_normal: 0,
      scan_area_mm2: scanA,
      compared_area_mm2: cmpA,
      off_reference_area_mm2: offA,
      off_reference_share: n(offA),
      not_measured_area_mm2: nmA,
      not_measured_share: n(nmA),
      reference_area_mm2: refArea,
      uncovered_share: uncovered,
      uncovered_area_mm2: uncovered * refArea,
      near_mm: NEAR_MM,
      snap_mm: 0.3,
      normal_deg: 70,
      cover_mm: 2,
      has_sigma: !!sigma,
      rule: `A measured scan vertex is compared when the reference's surface lies within ${NEAR_MM} mm of it, facing within 70° of the way it faces (the demo engine measures to the nearest of points spread over the reference's surface); any other measured vertex is not on the reference. A face is compared when its three vertices are.`,
      notes: [],
    };
    const coverage: ComparisonCoverage = {
      scan_area_mm2: scanA,
      compared_area_mm2: cmpA,
      off_reference_share: n(offA),
      not_measured_share: n(nmA),
      uncovered_share: uncovered,
      uncovered_area_mm2: uncovered * refArea,
      reference_area_mm2: refArea,
    };
    const d: MockDeviation = { version, key: dk, values, states, info, sigma, P, I, coverage };
    this.devs.set(key, d);
    return d;
  }

  private tolerance(c: StoredCmp, d: MockDeviation, tol: number): ToleranceAnswer {
    const { P, I, values, states, sigma } = d;
    const k = c.k;
    const fidx: number[] = [];
    for (let f = 0; f < I.length / 3; f++) if (states[I[3 * f]] === 0 && states[I[3 * f + 1]] === 0 && states[I[3 * f + 2]] === 0) fidx.push(f);
    const { area } = faceNormals(P, I);
    const a = fidx.map((f) => area[f]);
    const fd = fidx.map((f) => (values[I[3 * f]] + values[I[3 * f + 1]] + values[I[3 * f + 2]]) / 3);
    const total = a.reduce((x, y) => x + y, 0);
    const base = { format_version: 1, tolerance_mm: tol, k, version: d.version, mesh_changed: d.info.mesh_changed, aligned_mesh_version: c.aligned_mesh_version, method: c.alignment!.method, coverage: d.coverage };
    if (!(total > 0))
      return { ...base, compared_area_mm2: 0, share_within: null, share_above: null, share_below: null, share_uncertain: null, worst_above_mm: null, worst_below_mm: null, stats: {}, min_area_mm2: 4, n_regions: 0, regions: [], sentence: "nothing of the scan could be compared with the reference" };
    let above = 0,
      below = 0,
      unc = 0,
      mean = 0,
      sq = 0;
    const fs = fidx.map((f) => (sigma ? (sigma[I[3 * f]] + sigma[I[3 * f + 1]] + sigma[I[3 * f + 2]]) / 3 : NaN));
    fd.forEach((v, i) => {
      if (v > tol) above += a[i];
      else if (v < -tol) below += a[i];
      if (k * fs[i] > tol) unc += a[i];
      mean += v * a[i];
      sq += v * v * a[i];
    });
    const order = fd.map((_, i) => i).sort((x, y) => fd[x] - fd[y]);
    const quant = (q: number) => {
      let s = 0;
      for (const i of order) {
        s += a[i];
        if (s >= q * total) return fd[i];
      }
      return fd[order[order.length - 1]];
    };
    let worstA = -Infinity,
      worstB = Infinity;
    for (const f of fidx)
      for (let j = 0; j < 3; j++) {
        worstA = Math.max(worstA, values[I[3 * f + j]]);
        worstB = Math.min(worstB, values[I[3 * f + j]]);
      }
    const minArea = Math.max(4, 10 * [...a].sort((x, y) => x - y)[Math.floor(a.length / 2)]);
    const T = c.alignment!.T;
    const regions: ToleranceRegion[] = [];
    let nRegions = 0;
    for (const side of ["above", "below"] as const) {
      const sel = fd.map((_, i) => i).filter((i) => (side === "above" ? fd[i] > tol : fd[i] < -tol));
      if (!sel.length) continue;
      // faces outside joined over their shared corners
      const parent = new Map<number, number>();
      const root = (x: number): number => {
        let r = x;
        while (parent.has(r) && parent.get(r) !== r) r = parent.get(r)!;
        parent.set(x, r);
        return r;
      };
      for (const i of sel) {
        const f = fidx[i];
        const [p0, p1, p2] = [I[3 * f], I[3 * f + 1], I[3 * f + 2]].map(root);
        parent.set(p1, p0);
        parent.set(p2, p0);
      }
      const groups = new Map<number, number[]>();
      for (const i of sel) {
        const r = root(I[3 * fidx[i]]);
        const l = groups.get(r);
        if (l) l.push(i);
        else groups.set(r, [i]);
      }
      for (const g of groups.values()) {
        const ga = g.reduce((s, i) => s + a[i], 0);
        if (ga < minArea) continue;
        nRegions++;
        const cen: Vec3 = [0, 0, 0];
        const verts = new Set<number>();
        let gm = 0,
          beyond = 0;
        for (const i of g) {
          const f = fidx[i];
          for (let j = 0; j < 3; j++) {
            verts.add(I[3 * f + j]);
            for (let q = 0; q < 3; q++) cen[q] += (P[I[3 * f + j] * 3 + q] / 3) * a[i];
          }
          gm += fd[i] * a[i];
          if (Math.abs(fd[i]) > k * fs[i]) beyond += a[i];
        }
        for (let q = 0; q < 3; q++) cen[q] /= ga;
        const vl = [...verts];
        const b = box(P, vl);
        const radius = Math.max(...vl.map((v) => Math.hypot(P[3 * v] - cen[0], P[3 * v + 1] - cen[1], P[3 * v + 2] - cen[2])));
        const worst = side === "above" ? Math.max(...vl.map((v) => values[v])) : Math.min(...vl.map((v) => values[v]));
        const sv = sigma ? vl.map((v) => sigma[v]).sort((x, y) => x - y) : [];
        const smed = sv.length ? sv[Math.floor(sv.length / 2)] : null;
        regions.push({
          side,
          area_mm2: ga,
          share: ga / total,
          center: apply(T, cen),
          center_scan: cen,
          extent_mm: Math.max(b.hi[0] - b.lo[0], b.hi[1] - b.lo[1], b.hi[2] - b.lo[2]),
          radius_mm: radius,
          n_faces: g.length,
          mean_mm: gm / ga,
          worst_mm: worst,
          sigma_median_mm: smed,
          exceeds_uncertainty: smed === null ? null : Math.abs(gm / ga) > k * smed,
          share_beyond_uncertainty: sigma ? beyond / ga : null,
          faces: encodeFaces(g.map((i) => fidx[i]).sort((x, y) => x - y)),
        });
      }
    }
    regions.sort((x, y) => y.area_mm2 - x.area_mm2);
    const within = Math.max(0, 1 - above / total - below / total);
    let sentence = `${(100 * within).toFixed(0)} % within ± ${tol} mm`;
    if (above || below) sentence += ` (${((100 * above) / total).toFixed(1)} % above, ${((100 * below) / total).toFixed(1)} % below)`;
    const med = quant(0.5);
    return {
      ...base,
      compared_area_mm2: total,
      share_within: within,
      share_above: above / total,
      share_below: below / total,
      share_uncertain: sigma ? unc / total : null,
      worst_above_mm: worstA,
      worst_below_mm: worstB,
      stats: { mean_mm: mean / total, rms_mm: Math.sqrt(sq / total), median_mm: med, p05_mm: quant(0.05), p95_mm: quant(0.95) },
      min_area_mm2: minArea,
      n_regions: nRegions,
      regions: regions.slice(0, 50),
      sentence,
    };
  }
}
