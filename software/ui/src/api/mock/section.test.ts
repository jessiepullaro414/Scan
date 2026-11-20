/**
 * Checks for the demo engine's sections (`npm test`; `section.ts`): the simple triangle-plane cut on a known shape
 * (a 10 mm cube cut level and upright: a closed square of the true size and area, counter-clockwise seen from the
 * normal's side; a plane that misses it: nothing), and the routes as the engine answers them: a cut named each way,
 * the compact answer, keep, list, rename, delete, a kept section cut again with "the mesh has changed", the refusals.
 * Plain TypeScript that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { KeptSection, KeptSections, SectionAnswer, SectionLineCompact } from "../types";
import { planeFromPointNormal } from "../../lib/section.ts";
import { HttpError } from "./host.ts";
import { cutMesh, MockSections, type SectionEngine } from "./section.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`section.test (mock): ${what}`);
}
const near = (a: ArrayLike<number>, b: ArrayLike<number>, tol = 1e-9) => a.length === b.length && Array.from(a).every((x, i) => Math.abs(x - b[i]) <= tol);

/** A 10 mm cube from (0, 0, 0) to (10, 10, 10), its triangles facing out. */
function cube() {
  const positions = new Float32Array([0, 0, 0, 10, 0, 0, 10, 10, 0, 0, 10, 0, 0, 0, 10, 10, 0, 10, 10, 10, 10, 0, 10, 10]);
  // prettier-ignore
  const indices = new Uint32Array([
    0, 2, 1, 0, 3, 2, // bottom (z = 0, facing -z)
    4, 5, 6, 4, 6, 7, // top
    0, 1, 5, 0, 5, 4, // y = 0
    1, 2, 6, 1, 6, 5, // x = 10
    2, 3, 7, 2, 7, 6, // y = 10
    3, 0, 4, 3, 4, 7, // x = 0
  ]);
  return { positions, indices, sigma: new Float32Array(8).fill(0.05) };
}

// ---------------------------------------------------------------- the cut on a known shape
{
  const m = cube();
  const level = planeFromPointNormal([3, 3, 5], [0, 0, 1], null);
  const lines = cutMesh(m, level);
  check(lines.length === 1 && lines[0].closed, "a level cut through the cube: one closed line");
  const L = lines[0];
  let len = 0,
    area = 0;
  for (let i = 0; i < L.points.length; i++) {
    const [x0, y0] = L.points[i],
      [x1, y1] = L.points[(i + 1) % L.points.length];
    len += Math.hypot(x1 - x0, y1 - y0);
    area += 0.5 * (x0 * y1 - x1 * y0);
  }
  check(Math.abs(len - 40) < 1e-6, "its length is the square's: 40\u202fmm");
  check(Math.abs(area - 100) < 1e-6, "its area 100\u202fmm², positive: counter-clockwise seen from the normal's side");
  const xs = L.points.map((p) => p[0]),
    ys = L.points.map((p) => p[1]);
  check(near([Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)], [0, 0, 10, 10], 1e-6), "the square's place in the drawing (x, y)");
  check(L.points3d.every((p) => Math.abs(p[2] - 5) < 1e-6), "the 3D points lie on the plane");
  check(!!L.sigma && L.sigma.every((s) => Math.abs(s - 0.05) < 1e-7), "each point carries the sigma");
  // seen from below: the same square, still counter-clockwise (the drawing is never mirrored)
  const below = cutMesh(m, planeFromPointNormal([3, 3, 5], [0, 0, -1], null))[0];
  let a2 = 0;
  for (let i = 0; i < below.points.length; i++) {
    const [x0, y0] = below.points[i],
      [x1, y1] = below.points[(i + 1) % below.points.length];
    a2 += 0.5 * (x0 * y1 - x1 * y0);
  }
  check(Math.abs(a2 - 100) < 1e-6, "seen from the other side: still +100\u202fmm²");
  // upright, tilted: a closed loop whose length is the cube's outline in that plane
  const up = cutMesh(m, planeFromPointNormal([5, 5, 5], [1, 0, 0], null));
  check(up.length === 1 && up[0].closed && Math.abs(up[0].points.length - 8) <= 4, "an upright cut: one closed loop");
  check(cutMesh(m, planeFromPointNormal([5, 5, 50], [0, 0, 1], null)).length === 0, "a plane that misses the cube: nothing");
}

// ---------------------------------------------------------------- the routes
let version: number | null = null;
const kept = {
  features: [
    {
      id: "f-top",
      name: "Top",
      kind: "plane" as const,
      shape: { kind: "plane" as const, point: [0, 0, 10], normal: [0, 0, 1], rms_mm: 0.01, flatness_mm: 0.02, n: 100, normal_sigma_deg: 0.1, offset_sigma_mm: 0.01 },
      mesh_version: 1,
    },
    { id: "f-pin", name: "Pin", kind: "cylinder" as const, shape: { kind: "cylinder" as const, point: [5, 5, 0], axis: [0, 0, 1], radius_mm: 1, diameter_mm: 2, rms_mm: 0.01, n: 10 }, mesh_version: 1 },
  ],
  frame: null as null | Record<string, unknown>,
};
const engine: SectionEngine = {
  version: () => version,
  mesh: () => (version === null ? null : cube()),
  names: () => ({ part: "Bracket", scan: "Scan 1" }),
  kept: () => kept as unknown as ReturnType<SectionEngine["kept"]>,
};
const sections = new MockSections();
async function call<T>(method: string, word: "section" | "sections", rest: string[], body: Record<string, unknown> = {}): Promise<{ status: number; data: T }> {
  try {
    const r = sections.route(method, "p/s", word, rest, body, engine);
    if (!r) return { status: 404, data: null as T };
    return { status: r.status, data: r.status === 204 ? (null as T) : r.headers.get("content-type")?.includes("json") ? ((await r.json()) as T) : ((await r.text()) as T) };
  } catch (e) {
    if (e instanceof HttpError) return { status: e.status, data: { detail: e.detail } as T };
    throw e;
  }
}

async function routes(): Promise<void> {
  const body = { kind: "fit", fit: "f-top", offset_mm: -5, compact: true };
  check((await call("POST", "section", [], body)).status === 409, "409 before there is a mesh");
  version = 1;
  const r = await call<SectionAnswer>("POST", "section", [], body);
  check(r.status === 200 && r.data.lines.length === 1, "a kept plane moved 5\u202fmm down cuts the cube");
  const a = r.data;
  const ln = a.lines[0] as SectionLineCompact;
  check(a.compact && ln.points.length === 2 * ln.n_points && ln.closed && Math.abs(a.length_mm - 40) < 1e-6, "the compact answer: flat 2D points, the length");
  check(Math.abs((ln.area_mm2 ?? 0) - 100) < 1e-3 && a.n_closed === 1 && a.n_lines === 1, "the area and the counts");
  check(a.definition.kind === "fit" && a.definition.fit_name === "Top" && a.plane.description === "Section on Top, moved -5\u202fmm along its normal", "the definition and the engine's description");
  check(ln.stretches.length === 1 && ln.stretches[0][2] === false, "all measured (the demo has no filled faces)");
  check(a.filenames.dxf.endsWith(".dxf") && a.filenames.dxf.includes("Scan_1"), "the download names from the scan");
  check(!a.stale_fit && a.warnings.length === 0 && a.kept === null, "nothing to warn about");
  const pts = await call<SectionAnswer>("POST", "section", [], { kind: "points", points: [[0, 0, 5], [1, 0, 5], [0, 1, 5]] });
  check(pts.status === 200 && Math.abs(pts.data.length_mm - 40) < 1e-6, "three points");
  check((await call("POST", "section", [], { kind: "points", points: [[0, 0, 5], [1, 0, 5], [2, 0, 5]] })).status === 422, "three points on a line: 422");
  check((await call("POST", "section", [], { kind: "point_normal", point: [0, 0, 5], normal: [0, 0, 0] })).status === 422, "a zero normal: 422");
  check((await call("POST", "section", [], { kind: "frame", axis: "Z", value_mm: 1 })).status === 422, "no frame: 422");
  check((await call("POST", "section", [], { kind: "fit", fit: "f-pin" })).status === 422, "a cylinder is no plane: 422");
  check((await call("POST", "section", [], { kind: "fit", fit: "f-nope" })).status === 404, "an unknown fit: 404");
  // a fit made on an earlier mesh: its plane is used, and said so
  version = 2;
  const stale = await call<SectionAnswer>("POST", "section", [], body);
  check(stale.status === 200 && stale.data.stale_fit && stale.data.warnings.length === 1, "a stale fit's plane is used and flagged");
  // the files (a query's strings, as the mock server hands them over)
  const dxf = await call<string>("GET", "section", ["section.dxf"], { kind: "point_normal", point: "0,0,5", normal: "0,0,1" });
  check(dxf.status === 200 && dxf.data.includes("demo"), "a DXF from the demo");

  // kept sections
  const k = await call<KeptSection>("POST", "sections", [], { kind: "fit", fit: "f-top", offset_mm: -5, name: "Middle" });
  check(k.status === 200 && k.data.id.startsWith("s-") && k.data.name === "Middle" && k.data.mesh_version === 2 && !k.data.mesh_changed, "kept");
  const k2 = await call<KeptSection>("POST", "sections", [], { kind: "point_normal", point: [5, 5, 5], normal: [1, 0, 0] });
  check(k2.data.name === "Section 2", "a default name");
  const list = await call<KeptSections>("GET", "sections", []);
  check(list.data.sections.length === 2 && list.data.mesh_version === 2, "listed");
  const open = await call<SectionAnswer>("GET", "sections", [k.data.id], { compact: "true" });
  check(open.status === 200 && open.data.kept?.id === k.data.id && Math.abs(open.data.length_mm - 40) < 1e-6 && open.data.warnings.length === 0, "opened: cut again");
  version = 3;
  const changed = await call<SectionAnswer>("GET", "sections", [k.data.id]);
  check(changed.data.kept?.mesh_changed === true && changed.data.warnings.length === 1, "after an edit: the mesh has changed, said so");
  const marked = await call<KeptSection>("PATCH", "sections", [k.data.id], { recut: true });
  check(marked.data.mesh_version === 3 && !marked.data.mesh_changed, "marked as cut on the current mesh");
  const renamed = await call<KeptSection>("PATCH", "sections", [k.data.id], { name: "Waist" });
  check(renamed.data.name === "Waist", "renamed");
  check((await call("PATCH", "sections", [k.data.id], { name: " " })).status === 422, "an empty name: 422");
  check((await call("DELETE", "sections", [k.data.id])).status === 204, "deleted");
  check((await call("DELETE", "sections", [k.data.id])).status === 404, "twice: 404");
  check((await call("GET", "sections", ["s-nope"])).status === 404, "an unknown section: 404");
  check((await call<KeptSections>("GET", "sections", [])).data.sections.length === 1, "one left");
}

await routes();
console.log(`section.test (mock): ${checks} checks passed`);
