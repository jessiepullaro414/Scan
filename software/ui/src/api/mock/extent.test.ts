/**
 * Checks for the demo engine's extents and relations (`npm test`): `mockExtent` on a small flat grid and a half
 * tube; a kept fit carries its extent and its outline route answers, both go with the fit; "fit again" measures a
 * fit kept without one; a cylinder's form (boss or bore) from its faces, kept with it; kept relations and `features/relate` answer the engine's record (lib/fits.ts's values).
 * Plain TypeScript that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import { relationRecord } from "../../lib/fits.ts";
import type { CylinderShape, FeatureOutline, FeatureSet, FitResult, KeptFit, KeptRelation, PairRelation, PlaneShape, RefitResult } from "../types";
import { mockExtent, mockForm } from "./extent.ts";
import { MockFeatures, type FeatureEngine } from "./features.ts";
import { HttpError } from "./host.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`mock extent.test: ${what}`);
}
const near = (a: number, b: number, tol = 1e-6) => Math.abs(a - b) <= tol;

// ------------------------------------------------------------------ mockExtent
/** A flat grid 30 x 20 mm in z = 2 (two triangles a cell of 1 mm). */
function grid(): { positions: number[]; indices: number[] } {
  const positions: number[] = [],
    indices: number[] = [];
  for (let j = 0; j <= 20; j++) for (let i = 0; i <= 30; i++) positions.push(i, j, 2);
  for (let j = 0; j < 20; j++)
    for (let i = 0; i < 30; i++) {
      const a = j * 31 + i;
      indices.push(a, a + 1, a + 32, a, a + 32, a + 31);
    }
  return { positions, indices };
}
const g = grid();
const mockFeatures = new MockFeatures();
const allFaces = Array.from({ length: g.indices.length / 3 }, (_, k) => k);
const flat: PlaneShape = { kind: "plane", point: [15, 10, 2], normal: [0, 0, 1], rms_mm: 0, flatness_mm: 0, n: 651, normal_sigma_deg: 0.01, offset_sigma_mm: 0.001 };
{
  const { extent, outline } = mockExtent(g.positions, g.indices, allFaces, flat);
  check(extent.kind === "plane", "a plane's extent");
  if (extent.kind === "plane") {
    check(near(extent.rectangle.half_sizes_mm[0], 15) && near(extent.rectangle.half_sizes_mm[1], 10), `half sizes ${extent.rectangle.half_sizes_mm}`);
    check(near(extent.rectangle.center[0], 15) && near(extent.rectangle.center[1], 10) && near(extent.rectangle.center[2], 2), "its centre");
    check(near(extent.area_mm2, 600) && extent.n_outer === 1 && extent.n_holes === 0, `area ${extent.area_mm2}`);
    check(near(Math.abs(extent.rectangle.axes[0][0]), 1), "the longer side first (along x)");
  }
  check(outline !== null && outline.outer.length === 1 && outline.outer[0].length === 4 && outline.holes.length === 0, "its outline: the rectangle");
}
{
  // half a tube of radius 5 round the z axis, 12 mm long: x >= 0
  const positions: number[] = [],
    indices: number[] = [];
  const n = 18;
  for (let k = 0; k <= n; k++) {
    const t = -Math.PI / 2 + (Math.PI * k) / n;
    positions.push(5 * Math.cos(t), 5 * Math.sin(t), -4, 5 * Math.cos(t), 5 * Math.sin(t), 8);
  }
  for (let k = 0; k < n; k++) indices.push(2 * k, 2 * k + 2, 2 * k + 1, 2 * k + 1, 2 * k + 2, 2 * k + 3);
  const shape: CylinderShape = { kind: "cylinder", point: [0, 0, 0], axis: [0, 0, 1], radius_mm: 5, diameter_mm: 10, rms_mm: 0, n: 38, radius_sigma_mm: 0.01, diameter_sigma_mm: 0.02, axis_sigma_deg: 0.1, point_sigma_mm: 0.01 };
  const { extent, outline } = mockExtent(positions, indices, Array.from({ length: 2 * n }, (_, k) => k), shape);
  check(extent.kind === "cylinder" && outline === null, "a cylinder: no outline");
  if (extent.kind === "cylinder") {
    check(near(extent.along_mm[0], -4) && near(extent.along_mm[1], 8) && near(extent.length_mm, 12), `along ${extent.along_mm}`);
    check(near(extent.arc_deg, 180, 1e-6), `half the round: ${extent.arc_deg}`);
  }
  // its faces face away from the axis: a boss; turned over, towards it: a bore; half and half: not known
  const faces = Array.from({ length: 2 * n }, (_, k) => k);
  check(mockForm(positions, indices, faces, shape) === "boss", "the half tube is a boss");
  const turned = indices.map((_, i) => indices[i - (i % 3) + [0, 2, 1][i % 3]]);
  check(mockForm(positions, turned, faces, shape) === "bore", "turned over, a bore");
  const mixed = indices.map((v, i) => (Math.floor(i / 3) % 2 ? turned[i] : v));
  check(mockForm(positions, mixed, faces, shape) === null, "half and half: not known");
  check(mockForm(g.positions, g.indices, allFaces, flat) === null, "a plane has no form");
  // a kept cylinder keeps its form; a plane's is null
  const cylEngine: FeatureEngine = {
    version: () => 1,
    nearestFace: () => 0,
    fit: (): FitResult => ({ kind: "cylinder", shape, faces: "AAAAAAEAAAA=", n_faces: 2, tolerance_mm: 0.1, mean_sigma_mm: null, correlated_points: null, warnings: [], version: 1 }),
    form: (r) => mockForm(positions, indices, faces, r.shape),
  };
  const kept = mockFeatures.route("POST", "p0/s0", [], { kind: "cylinder", face: 0, point: [5, 0, 2] }, cylEngine);
  const k = (await kept!.json()) as KeptFit;
  check(k.form === "boss", `a kept cylinder carries its form: ${k.form}`);
  const set = (await mockFeatures.route("GET", "p0/s0", [], {}, cylEngine)!.json()) as FeatureSet;
  check(set.features[0].form === "boss", "and the set shows it");
}

// ------------------------------------------------------------------ kept fits, outlines and relations
let version: number | null = 1;
const shapes: Record<number, PlaneShape> = {
  1: flat,
  2: { ...flat, point: [3, 4, 12], normal: [0, 0.001, 1] },
};
const engine: FeatureEngine = {
  version: () => version,
  nearestFace: () => 1,
  fit: (body): FitResult => {
    if (version === null) throw new HttpError(409, "This scan has no mesh yet", "Make the mesh first.");
    return {
      kind: "plane",
      shape: shapes[Number(body.face)] ?? flat,
      faces: "AQAAAAIAAAA=",
      n_faces: 2,
      tolerance_mm: 0.2,
      mean_sigma_mm: null,
      correlated_points: null,
      warnings: [],
      version,
    };
  },
  extent: (r) => mockExtent(g.positions, g.indices, allFaces, r.shape),
};
const mock = new MockFeatures();
const call = async <T>(method: string, rest: string[], body: Record<string, unknown> = {}): Promise<{ status: number; data: T }> => {
  let r: Response | undefined;
  try {
    r = mock.route(method, "p1/s1", rest, body, engine);
  } catch (e) {
    if (e instanceof HttpError) return { status: e.status, data: { detail: e.detail, hint: e.hint } as T };
    throw e;
  }
  if (!r) return { status: 404, data: undefined as T };
  return { status: r.status, data: r.status === 204 ? (undefined as T) : ((await r.json()) as T) };
};

const f1 = (await call<KeptFit>("POST", [], { kind: "plane", face: 1, point: [15, 10, 2] })).data;
check(f1.form === null, "a kept plane's form is null");
const f2 = (await call<KeptFit>("POST", [], { kind: "plane", face: 2, point: [3, 4, 12] })).data;
check(f1.extent?.kind === "plane" && f2.extent?.kind === "plane", "a kept fit carries its extent");
{
  const o = await call<FeatureOutline>("GET", [f1.id, "outline"]);
  check(o.status === 200 && o.data.kind === "plane" && o.data.mesh_version === 1 && o.data.id === f1.id, "its outline route answers");
  check((await call("GET", ["f-nope", "outline"])).status === 404, "no such fit: 404");
}
// relations: the engine's form, with fits.ts's values
const want = relationRecord(f1, f2);
{
  const r = await call<KeptRelation>("POST", ["relations"], { a: f1.id, b: f2.id });
  check(r.status === 200 && JSON.stringify(r.data.relation) === JSON.stringify(want) && r.data.stale === false, "a kept relation carries its record");
  const s = (await call<FeatureSet>("GET", [])).data;
  check(JSON.stringify(s.relations[0].relation) === JSON.stringify(want) && s.relations[0].stale === false, "and so does the set");
  const p = await call<PairRelation>("GET", ["relate"], { a: f1.id, b: f2.id });
  check(p.status === 200 && p.data.a === f1.id && JSON.stringify(p.data.relation) === JSON.stringify(want), "relate: the same record");
  check((await call("GET", ["relate"], { a: f1.id, b: "f-nope" })).status === 404, "relate: an unknown fit 404");
  check((await call("GET", ["relate"], { a: f1.id, b: f1.id })).status === 422, "relate: one fit twice 422");
  check((await call("GET", ["relate"], { a: f1.id })).status === 422, "relate: b missing 422");
  const rn = await call<KeptRelation>("PATCH", [r.data.id], { name: "Thickness" });
  check(rn.data.name === "Thickness" && JSON.stringify(rn.data.relation) === JSON.stringify(want), "a rename answers the relation as shown");
}
// an edit: the relation is stale until its fits are fitted again
version = 2;
{
  const s = (await call<FeatureSet>("GET", [])).data;
  check(s.relations[0].stale === true, "a relation of stale fits is marked");
  const r = (await call<RefitResult>("POST", ["refit"], {})).data;
  check(r.refitted.length === 2 && r.features.relations[0].stale === false, "fit again: current again");
  check((await call<FeatureOutline>("GET", [f1.id, "outline"])).data.mesh_version === 2, "the outline is the new mesh's");
}
// a fit kept without an extent (before extents existed) gets one on "fit again", from its own click
{
  const noExtent: FeatureEngine = { ...engine, extent: undefined };
  const r = mock.route("POST", "p1/s1", [], { kind: "plane", face: 1, point: [15, 10, 2] }, noExtent);
  const old = (await r!.json()) as KeptFit;
  check(old.extent === null && (await call("GET", [old.id, "outline"])).status === 404, "kept without an extent: no outline");
  const out = (await call<RefitResult>("POST", ["refit"], {})).data;
  check(out.refitted.join() === old.id, "fit again measures it (and nothing else)");
  const back = out.features.features.find((f) => f.id === old.id)!;
  check(back.extent?.kind === "plane" && back.seed.face === 1 && back.mesh_version === 2, "from its own click, on its own mesh");
}
// a fit takes its outline with it
check((await call("DELETE", [f1.id])).status === 204 && (await call("GET", [f1.id, "outline"])).status === 404, "deleted with the fit");

console.log(`mock extent.test: ${checks} checks passed`);
