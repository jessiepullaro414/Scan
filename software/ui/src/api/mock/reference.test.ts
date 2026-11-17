/**
 * Checks for the demo engine's references and comparison (`npm test`; `reference.ts`): a reference uploaded as the raw
 * body (the name's last part only, another format refused in the engine's words, an empty or oversized body refused),
 * listed, renamed, served for display and deleted; a comparison started (the units finding for a reference in inches
 * and both ways out of it), aligned from points at once, from the shape as a job, refused on the datums without a
 * frame; the deviation array's length and states, and the answer for a tolerance with its area outside.
 * Plain TypeScript that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { Comparison, ComparisonAlignAnswer, Job, ReferenceList, ReferenceModel, ToleranceAnswer } from "../types";
import { HttpError } from "./host.ts";
import { MockReferences, readMeshFile, type CompareEngine } from "./reference.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`reference.test (mock): ${what}`);
}

/** A 40 x 40 mm plate in z = 0 (1 mm grid, facing +z): the scan. */
function plate(lift?: (x: number, y: number) => number, keep?: (x: number, y: number) => boolean) {
  const n = 41;
  const pos: number[] = [];
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) pos.push(i, j, lift ? lift(i, j) : 0);
  const idx: number[] = [];
  for (let j = 0; j + 1 < n; j++)
    for (let i = 0; i + 1 < n; i++) {
      if (keep && !keep(i + 0.5, j + 0.5)) continue;
      const a = j * n + i;
      idx.push(a, a + 1, a + n + 1, a, a + n + 1, a + n);
    }
  return { positions: new Float32Array(pos), indices: new Uint32Array(idx) };
}

/** A binary STL of a mesh, its numbers times `scale`. */
function stl(m: { positions: Float32Array; indices: Uint32Array }, scale = 1): ArrayBuffer {
  const nT = m.indices.length / 3;
  const buf = new ArrayBuffer(84 + 50 * nT);
  const dv = new DataView(buf);
  dv.setUint32(80, nT, true);
  for (let t = 0; t < nT; t++) {
    const o = 84 + 50 * t;
    for (let k = 0; k < 3; k++) for (let c = 0; c < 3; c++) dv.setFloat32(o + 12 + 12 * k + 4 * c, m.positions[m.indices[3 * t + k] * 3 + c] * scale, true);
  }
  return buf;
}

const bump = (x: number, y: number) => (Math.hypot(x - 15, y - 15) <= 4 ? 0.5 : 0);
const scan = plate();
const reference = plate(bump, (x) => x < 35); // the bump raised 0.5 mm, the strip past x = 35 left out

const jobs = new Map<string, Job>();
let fail: string | null = null;
const engine = (withMesh = true, frame: unknown = null): CompareEngine => ({
  version: () => (withMesh ? 3 : null),
  mesh: () => (withMesh ? { positions: scan.positions, indices: scan.indices, sigma: new Float32Array(scan.positions.length / 3).fill(0.02) } : null),
  frame: () => frame as never,
  featureIds: () => ["f-1", "f-2", "f-3"],
  startWorkJob: (kind, spec) => {
    // run the work to its end at once, as the demo's ticks would
    const job: Job = { id: `j-${jobs.size + 1}`, kind, state: "running", progress: 0, message: "", result: null, error: null, created: "now", ...(spec.extra ?? {}) };
    let step = spec.work.next();
    while (!step.done) step = spec.work.next();
    try {
      job.result = step.value ? step.value() : null;
      job.state = "done";
    } catch (e) {
      fail = (e as Error).message;
      job.state = "failed";
    }
    jobs.set(job.id, job);
    return job;
  },
});

const m = new MockReferences();
const P = "p-1";
async function call(r: Response | undefined): Promise<{ status: number; body: unknown }> {
  if (!r) throw new Error("no route");
  return { status: r.status, body: r.status === 204 ? null : r.headers.get("content-type")?.includes("json") ? await r.json() : await r.arrayBuffer() };
}
async function refs(method: string, rest: string[], body: Record<string, unknown> = {}, raw: ArrayBuffer | null = null) {
  try {
    return await call(m.routeReferences(method, P, rest, body, raw, () => null));
  } catch (e) {
    if (e instanceof HttpError) return { status: e.status, body: { detail: e.detail, hint: e.hint } };
    throw e;
  }
}
async function cmp(method: string, rest: string[], body: Record<string, unknown> = {}, e: CompareEngine = engine()) {
  try {
    return await call(m.routeComparison(method, P, "s-1", rest, body, e));
  } catch (err) {
    if (err instanceof HttpError) return { status: err.status, body: { detail: err.detail, hint: err.hint } };
    throw err;
  }
}
const detail = (r: { body: unknown }) => (r.body as { detail: string }).detail;

// ---------------------------------------------------------------- reading files
{
  const read = readMeshFile(stl(reference), "x.stl");
  check(read.indices.length / 3 === reference.indices.length / 3, "a binary STL: every triangle");
  check(read.positions.length / 3 < (reference.indices.length / 3) * 3, "its corners welded");
  const ascii = new TextEncoder().encode("solid t\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid t\n");
  check(readMeshFile(ascii.buffer, "t.stl").indices.length === 3, "an ASCII STL");
  const obj = new TextEncoder().encode("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1/1 2/2 3/3 4/4\n");
  check(readMeshFile(obj.buffer, "q.obj").indices.length === 6, "an OBJ quad: two triangles");
}

// ---------------------------------------------------------------- references of a project
{
  const up = await refs("POST", ["references"], { name: "../../bracket.stl" }, stl(reference));
  check(up.status === 200, `upload: ${JSON.stringify(up.body)}`);
  const rec = up.body as ReferenceModel;
  check(rec.name === "bracket.stl" && rec.format === "stl" && rec.triangles === reference.indices.length / 3, "the name's last part, the triangles");
  check(rec.display.triangles === rec.triangles && !rec.display.decimated, "the display copy");
  const step = await refs("POST", ["references"], { name: "part.step" }, new ArrayBuffer(10));
  check(step.status === 422 && /export a STEP or other CAD file as STL first/.test(detail(step)), "a STEP file: the engine's words");
  check((await refs("POST", ["references"], { name: "x.stl" }, new ArrayBuffer(0))).status === 422, "an empty body");
  check((await refs("POST", ["references"], {}, stl(reference))).status === 422, "no name");
  m.maxBytes = 100;
  check((await refs("POST", ["references"], { name: "big.stl" }, stl(reference))).status === 413, "too large");
  m.maxBytes = 100 * 2 ** 20;
  const list = (await refs("GET", ["references"])).body as ReferenceList;
  check(list.references.length === 1 && list.references[0].id === rec.id, "listed");
  const ren = await refs("PATCH", ["references", rec.id], { name: "  Bracket as drawn " });
  check(ren.status === 200 && (ren.body as ReferenceModel).name === "Bracket as drawn", "renamed");
  const mesh = await refs("GET", ["references", rec.id, "mesh.ply"]);
  check(mesh.status === 200 && new TextDecoder().decode(new Uint8Array(mesh.body as ArrayBuffer, 0, 3)) === "ply", "served as PLY");
  check((await refs("GET", ["references", "ref-nope", "mesh.ply"])).status === 404, "unknown id");

  // ---------------------------------------------------------------- the comparison
  check((await cmp("POST", ["comparison"], { reference_id: rec.id }, engine(false))).status === 409, "before there is a mesh: 409");
  check((await cmp("POST", ["comparison"], { reference_id: "ref-nope" })).status === 404, "an unknown reference");
  check(((await cmp("GET", ["comparison"])).body as { comparison: unknown }).comparison === null, "none yet");
  let c = (await cmp("POST", ["comparison"], { reference_id: rec.id, tolerance_mm: 0.2 })).body as Comparison;
  check(c.units.status === "same" && c.alignment === null && c.tolerance_mm === 0.2, `units same: ${c.units.message}`);
  check((await cmp("GET", ["comparison", "deviation.json"])).status === 409, "not aligned yet");
  check((await cmp("POST", ["comparison", "align"], { method: "datums" })).status === 422, "datums without a frame");
  check((await cmp("POST", ["comparison", "align"], { method: "points", pairs: [{ scan: [0, 0, 0], reference: [0, 0, 0] }] })).status === 422, "too few pairs");
  const pairs = [
    [0, 0, 0],
    [40, 0, 0],
    [0, 40, 0],
  ].map((p) => ({ scan: p, reference: p }));
  const a = (await cmp("POST", ["comparison", "align"], { method: "points", pairs, refine: false })).body as ComparisonAlignAnswer;
  check(a.job === null && a.comparison!.alignment!.method === "points" && a.comparison!.view_matrix !== null, "points, as they are: at once");
  // the deviation: one value and one state per vertex; the bump 0.5 mm inside, the strip past the reference not on it
  const info = (await cmp("GET", ["comparison", "deviation.json"])).body as { vertices: number; version: number; states: string[] };
  const bin = (await cmp("GET", ["comparison", "deviation.bin"])).body as ArrayBuffer;
  const n = scan.positions.length / 3;
  check(info.vertices === n && bin.byteLength === 5 * n && info.states.length === 3, "float32 then a state byte per vertex");
  const values = new Float32Array(bin, 0, n);
  const states = new Uint8Array(bin, 4 * n, n);
  const at = (x: number, y: number) => y * 41 + x;
  check(states[at(15, 15)] === 0 && Math.abs(values[at(15, 15)] + 0.5) < 1e-4, `the bump reads -0.5: ${values[at(15, 15)]}`);
  check(states[at(5, 30)] === 0 && Math.abs(values[at(5, 30)]) < 1e-4, "the rest reads 0");
  check(states[at(40, 20)] === 2 && Number.isNaN(values[at(40, 20)]), "past the reference: not on it");
  const t = (await cmp("POST", ["comparison", "tolerance"], { tolerance_mm: 0.2 })).body as ToleranceAnswer;
  check(t.share_above === 0 && (t.share_below ?? 0) > 0.02 && /within ± 0.2 mm/.test(t.sentence), t.sentence);
  check(t.regions.length === 1 && t.regions[0].side === "below" && Math.abs(t.regions[0].worst_mm + 0.5) < 1e-4, "one area inside");
  check(Math.abs(t.regions[0].center_scan[0] - 15) < 1 && typeof t.regions[0].faces === "string", "its place in the scan's frame, its faces");
  check((t.coverage.off_reference_share ?? 0) > 0 && t.version === 3, "the coverage");
  check((await cmp("POST", ["comparison", "tolerance"], { tolerance_mm: 0 })).status === 422, "a bad tolerance");
  // from the shape: a job
  const s = (await cmp("POST", ["comparison", "align"], { method: "shape" })).body as ComparisonAlignAnswer;
  check(s.job !== null && s.job.kind === "reference_align" && s.comparison === null && fail === null, `a job: ${fail}`);
  const done = jobs.get(s.job!.id)!;
  check(done.state === "done" && (done.result as { kept: boolean }).kept === true, "kept when accepted");
  c = ((await cmp("GET", ["comparison"])).body as { comparison: Comparison }).comparison;
  check(c.alignment!.method === "shape" && c.tolerance_mm === 0.2, "kept with the scan");

  // a reference in inches: asked, then applied or kept
  const inch = (await refs("POST", ["references"], { name: "plate-inch.stl" }, stl(reference, 1 / 25.4))).body as ReferenceModel;
  c = (await cmp("POST", ["comparison"], { reference_id: inch.id })).body as Comparison;
  check(c.units.status === "pending" && c.units.unit === "inch" && c.units.factor === 25.4 && !c.ready, `asked: ${c.units.message}`);
  check((await cmp("POST", ["comparison", "align"], { method: "shape" })).status === 409, "nothing until it is settled");
  c = (await cmp("POST", ["comparison", "units"], { action: "apply" })).body as Comparison;
  check(c.units.status === "applied" && c.scale === 25.4 && c.ready, "applied");
  c = (await cmp("POST", ["comparison", "units"], { action: "keep" })).body as Comparison;
  check(c.units.status === "kept" && c.scale === 1, "kept");
  check((await cmp("POST", ["comparison", "units"], { action: "guess" })).status === 422, "a bad action");

  // deleting the reference takes its comparison with it
  check((await refs("DELETE", ["references", inch.id])).status === 204, "deleted");
  check(((await cmp("GET", ["comparison"])).body as { comparison: unknown }).comparison === null, "its comparison is gone");
  check((await cmp("DELETE", ["comparison"])).status === 404, "nothing to remove");
}

console.log(`reference.test (mock): ${checks} checks passed`);
