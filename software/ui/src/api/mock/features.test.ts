/**
 * Checks for the demo engine's kept features (`npm test`): keep, reload, rename and delete fits,
 * measurements and relations; a fit kept on one mesh version is marked after the mesh changes and
 * comes back after "fit again"; a fit that can't be found again stays marked; bad bodies answer 422.
 * Plain TypeScript that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { FeatureSet, FitResult, KeptFit, KeptMeasurement, KeptRelation, RefitResult } from "../types";
import { MockFeatures, type FeatureEngine } from "./features.ts";
import { HttpError } from "./host.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`features.test: ${what}`);
}

/** A made-up engine: a plane wherever it's clicked, none on faces from 900 up (where the point at x = 9 lands after
 * an edit); the mesh version can change. */
let version: number | null = null;
let refuseAll = false;
const engine: FeatureEngine = {
  version: () => version,
  nearestFace: (p) => (p[0] === 9 ? 909 : Math.round(Math.abs(p[0]))),
  fit: (body): FitResult => {
    if (version === null) throw new HttpError(409, "This scan has no mesh yet", "Make the mesh first.");
    if (Number(body.face) >= 900 || refuseAll) throw new HttpError(422, "No flat area here", "Click in the middle of a flat area.");
    if (body.kind !== "plane" && body.kind !== "cylinder") throw new HttpError(422, `Unknown kind “${String(body.kind)}”`, "Use plane, cylinder or hole.");
    if (typeof body.version === "number" && body.version !== version) throw new HttpError(409, "The mesh changed since this click", "Reload the mesh and click again.");
    return {
      kind: "plane",
      shape: { kind: "plane", point: [0, 0, 0], normal: [0, 0, 1], rms_mm: 0.01, flatness_mm: 0.05, n: 100 + Number(body.face), normal_sigma_deg: 0.01, offset_sigma_mm: 0.002 },
      faces: "AQAAAAIAAAA=",
      n_faces: 2,
      tolerance_mm: 0.2,
      mean_sigma_mm: null,
      correlated_points: null,
      warnings: [],
      version,
    };
  },
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

// ------------------------------------------------------------------ before there is a mesh
{
  const r = await call<FeatureSet>("GET", []);
  check(r.status === 200 && r.data.features.length === 0 && r.data.measurements.length === 0 && r.data.relations.length === 0, "no mesh: an empty set");
  check(r.data.mesh_version === null && r.data.frame === null && r.data.format_version === 1, "no mesh: version null, no frame");
  check((await call("POST", [], { kind: "plane", face: 3, point: [3, 0, 0] })).status === 409, "no mesh: keeping a fit is refused");
}

// ------------------------------------------------------------------ keep, reload, rename, delete
version = 3;
const f1 = await call<KeptFit>("POST", [], { kind: "plane", face: 3, point: [3, 0, 0], version: 3 });
check(f1.status === 200 && f1.data.id.startsWith("f-") && f1.data.name === "Fit 1" && f1.data.stale === false, "a fit kept, named Fit 1");
check(f1.data.mesh_version === 3 && f1.data.faces === "AQAAAAIAAAA=" && f1.data.seed.face === 3 && f1.data.seed.tolerance_mm === null, "its version, region and seed");
const f2 = await call<KeptFit>("POST", [], { kind: "plane", face: 5, point: [5, 0, 0], name: "  Top  " });
check(f2.data.name === "Top", "a given name, trimmed");
const m1 = await call<KeptMeasurement>("POST", ["measurements"], { a: [0, 0, 0], b: [3, 4, 0] });
check(m1.status === 200 && m1.data.name === "Distance 1" && m1.data.mesh_version === 3 && !m1.data.stale, "a measurement kept");
const r1 = await call<KeptRelation>("POST", ["relations"], { a: f1.data.id, b: f2.data.id });
check(r1.status === 200 && r1.data.name === "Relation 1" && r1.data.a === f1.data.id, "a relation kept");
{
  const s = (await call<FeatureSet>("GET", [])).data;
  check(s.features.map((f) => f.name).join() === "Fit 1,Top" && s.measurements.length === 1 && s.relations.length === 1, "reload: the same set");
  check(!("faces" in s.features[0]), "the list leaves the regions out");
  const faces = (await call<{ faces: string; mesh_version: number }>("GET", [f1.data.id, "faces"])).data;
  check(faces.faces === "AQAAAAIAAAA=" && faces.mesh_version === 3, "a region on request");
}
{
  const r = await call<{ name: string }>("PATCH", [f1.data.id], { name: "Table" });
  check(r.status === 200 && r.data.name === "Table", "rename a fit");
  check((await call<{ name: string }>("PATCH", [m1.data.id], { name: "Width" })).data.name === "Width", "rename a measurement");
  check((await call("PATCH", [f1.data.id], { name: "   " })).status === 422, "an empty name: 422");
  check((await call("PATCH", [f1.data.id], { name: "x".repeat(81) })).status === 422, "a long name: 422");
  check((await call("PATCH", [f1.data.id], {})).status === 422, "no name: 422");
  check((await call("PATCH", ["f-nope"], { name: "x" })).status === 404, "rename an unknown id: 404");
  const s = (await call<FeatureSet>("GET", [])).data;
  check(s.features[0].name === "Table" && s.measurements[0].name === "Width", "renames are kept");
}
{
  check((await call("POST", [], { kind: "cone", face: 1, point: [1, 0, 0] })).status === 422, "unknown kind: 422");
  check((await call("POST", [], { kind: "plane", face: -1, point: [1, 0, 0] })).status === 422, "a negative face: 422");
  check((await call("POST", [], { kind: "plane", face: 1, point: [1, 0] })).status === 422, "a point of two numbers: 422");
  check((await call("POST", ["measurements"], { a: [0, 0, 0] })).status === 422, "a measurement without b: 422");
  check((await call("POST", ["relations"], { a: f1.data.id, b: f1.data.id })).status === 422, "a fit related to itself: 422");
  check((await call("POST", ["relations"], { a: f1.data.id, b: "f-nope" })).status === 422, "a relation to an unknown fit: 422");
  check((await call("POST", ["refit"], { ids: "x" })).status === 422, "refit ids that aren't a list: 422");
  const f3 = await call<KeptFit>("POST", [], { kind: "plane", face: 7, point: [7, 0, 0] });
  check(f3.data.name === "Fit 3", "the next default name counts on");
  check((await call("DELETE", [f2.data.id])).status === 204, "delete a fit");
  const s = (await call<FeatureSet>("GET", [])).data;
  check(s.features.length === 2 && s.relations.length === 0, "its relation goes with it");
  check((await call("GET", [f2.data.id, "faces"])).status === 404, "and its region");
  check((await call("DELETE", [f2.data.id])).status === 404, "delete it again: 404");
  check((await call("DELETE", [f3.data.id])).status === 204, "delete another");
}

// ------------------------------------------------------------------ the mesh changes: marked, then fitted again
{
  const lost = await call<KeptFit>("POST", [], { kind: "plane", face: 9, point: [9, 0, 0] });
  version = 4;
  for (let i = 0; i < 2; i++) {
    const s = (await call<FeatureSet>("GET", [])).data;
    check(s.mesh_version === 4 && s.features.every((f) => f.stale && f.mesh_version === 3), "an edit marks every fit; reading refits nothing");
    check(s.measurements.every((m) => m.stale), "and the measurements");
  }
  const r = await call<RefitResult>("POST", ["refit"], {});
  check(r.status === 200 && r.data.refitted.length === 1 && r.data.refitted[0] === f1.data.id, "fit again: the plane comes back");
  check(r.data.failed.length === 1 && r.data.failed[0].id === lost.data.id && !!r.data.failed[0].detail, "the other can't and says why");
  const [g, h] = r.data.features.features;
  check(g.stale === false && g.mesh_version === 4 && g.name === "Table" && g.seed.point[0] === 3, "refitted on the new mesh, its name and click kept");
  check(h.stale === true && h.mesh_version === 3, "the other stays marked");
  const again = await call<RefitResult>("POST", ["refit"], { ids: [f1.data.id] });
  check(again.data.refitted.length === 0 && again.data.failed.length === 0, "nothing stale: nothing to do");
  check((await call("POST", ["refit"], { ids: ["f-nope"] })).status === 404, "refit an unknown id: 404");
  refuseAll = true;
  version = 5;
  const none = await call<RefitResult>("POST", ["refit"], {});
  check(none.data.refitted.length === 0 && none.data.failed.length === 2, "when nothing can be found again, all stay marked");
}

console.log(`features.test: ${checks} checks passed`);
