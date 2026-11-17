/**
 * Checks for the demo engine's nominal values (`npm test`; nominal.ts, the routes in features.ts, the summary in
 * cad.ts): offers by plain ±2σ rounding (no bias table), approving the value offered or one typed, refusing what makes
 * no sense, removing; an approval kept through "fit again" and marked "check again" when the new measurement does not
 * allow it; deleting a fit takes its approvals; the CAD summary lists the approvals. Plain TypeScript that Node runs as
 * it is (type stripping), no test framework.
 */
import type { CadSummary, FeatureSet, FitResult, FitShape, NominalAnswer } from "../types";
import { MockFeatures, type FeatureEngine } from "./features.ts";
import { HttpError } from "./host.ts";
import { mockOffer } from "./nominal.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`nominal.test (mock): ${what}`);
}

const circle = (d: number, sd: number): FitShape => ({ kind: "circle", center: [3, -2, 10], normal: [0, 0, 1], radius_mm: d / 2, diameter_mm: d, rms_mm: 0.01, n: 80, radius_sigma_mm: sd / 2, diameter_sigma_mm: sd, center_sigma_mm: 0.004 });
const SHAPES: Record<number, FitShape> = {
  1: { kind: "plane", point: [0, 0, 10], normal: [0, 0, 1], rms_mm: 0.01, flatness_mm: 0.05, n: 900, normal_sigma_deg: 0.2, offset_sigma_mm: 0.02 },
  2: { kind: "plane", point: [0, 0, -0.02], normal: [0, 0, 1], rms_mm: 0.01, flatness_mm: 0.05, n: 900, normal_sigma_deg: 0.2, offset_sigma_mm: 0.02 },
  3: circle(10.04, 0.03),
};
let version = 3;
const engine: FeatureEngine = {
  version: () => version,
  nearestFace: () => 3,
  fit: (body): FitResult => {
    const shape = SHAPES[Number(body.face)];
    return { kind: shape.kind === "circle" ? "hole" : "plane", shape, faces: "AQAAAA==", n_faces: 1, tolerance_mm: null, mean_sigma_mm: null, correlated_points: null, warnings: [], version };
  },
};
const store = new MockFeatures();
const KEY = "p/s";

function call(method: string, rest: string[], body: Record<string, unknown> = {}): Response {
  try {
    const r = store.route(method, KEY, rest, body, engine);
    if (!r) throw new Error(`no route for ${method} ${rest.join("/")}`);
    return r;
  } catch (e) {
    if (e instanceof HttpError) return new Response(JSON.stringify({ detail: e.detail, hint: e.hint }), { status: e.status });
    throw e;
  }
}
const ans = async (r: Response) => (await r.json()) as NominalAnswer;

// the search: plain ±2σ rounding, coarsest step first
const o = mockOffer({ value: "diameter", label: "Diameter", unit: "mm", measured: 14.02, sigma: 0.03 });
check(o.suggestions.length === 1 && o.suggestions[0].nominal === 14 && o.suggestions[0].allowed_by === "2 sigma", `14.02 ± 0.03: ${JSON.stringify(o)}`);
check(o.suggestions[0].sentence.includes("no known bias"), "the demo says it uses no bias");
check(mockOffer({ value: "diameter", label: "Diameter", unit: "mm", measured: 14.3, sigma: 0.03 }).reason_code === "already_round", "14.3 is already round (a tenth)");
check(mockOffer({ value: "diameter", label: "Diameter", unit: "mm", measured: 14.25, sigma: 0.02 }).reason_code === "no_candidate", "nothing within 2σ");
check(mockOffer({ value: "angle", label: "Angle", unit: "deg", measured: 89.7, sigma: 0.2 }).suggestions[0]?.word === "perpendicular", "90° is perpendicular");
check(mockOffer({ value: "distance", label: "Distance", unit: "mm", measured: 10.2, sigma: 2 }).reason_code === "too_many", "too large a ±");
check(mockOffer({ value: "angle", label: "Angle", unit: "deg", measured: 3, sigma: null }).reason_code === "no_sigma", "no ±");

// keep two planes and a hole, and relate the planes
for (const face of [1, 2, 3]) check(call("POST", [], { kind: face === 3 ? "hole" : "plane", face, point: [0, 0, 0] }).status === 200, `keep fit ${face}`);
let set = (await call("GET", []).json()) as FeatureSet;
const [p1, p2, hole] = set.features;
const rel = (await call("POST", ["relations"], { a: p1.id, b: p2.id }).json()) as { id: string };
check(set.features.every((f) => Array.isArray(f.nominals) && f.nominals.length === 0), "no approvals at first");

// offers
let a = await ans(call("GET", [hole.id, "nominal"]));
check(a.kind === "fit" && a.poses === "tracked" && a.offers[0].suggestions[0]?.nominal === 10, `the hole's offer: ${JSON.stringify(a.offers)}`);
check((await ans(call("GET", [p1.id, "nominal"]))).offers.length === 0, "a plane: no offers");
const ra = await ans(call("GET", [rel.id, "nominal"]));
check(ra.kind === "relation" && ra.offers.some((x) => x.value === "distance" && x.suggestions[0]?.nominal === 10), `the relation's: ${JSON.stringify(ra.offers)}`);
check(call("GET", ["f-nope", "nominal"]).status === 404, "404 for an unknown id");

// approve, refuse, remove
a = await ans(call("POST", [hole.id, "nominal"], { value: "diameter", nominal: 10, source: "suggested" }));
check(a.nominals.length === 1 && a.nominals[0].nominal === 10 && a.nominals[0].source === "suggested" && !a.nominals[0].check_again, "approved as offered");
check(call("POST", [hole.id, "nominal"], { value: "diameter", nominal: 10.5, source: "suggested" }).status === 422, "a suggested value must be offered");
for (const bad of [0, -2, 1e9]) check(call("POST", [hole.id, "nominal"], { value: "diameter", nominal: bad, source: "entered" }).status === 422, `refused ${bad}`);
check(call("POST", [hole.id, "nominal"], { value: "angle", nominal: 1, source: "entered" }).status === 422, "a fit has no angle");
check(call("POST", [p1.id, "nominal"], { value: "diameter", nominal: 1, source: "entered" }).status === 422, "a plane has no size");
check(call("POST", [rel.id, "nominal"], { value: "distance", nominal: 10, source: "suggested" }).status === 200, "a relation's distance approved");
check(call("POST", [rel.id, "nominal"], { value: "angle", nominal: 120, source: "entered" }).status === 422, "an angle past 90°");
set = (await call("GET", []).json()) as FeatureSet;
check(set.relations[0].nominals?.[0]?.nominal === 10 && set.relations[0].nominals[0].unit === "mm", "the relation shows its approval");
check(set.features[2].nominals?.[0]?.nominal === 10, "the fit shows its approval");
check(call("DELETE", [rel.id, "nominal", "distance"]).status === 204 && call("DELETE", [rel.id, "nominal", "distance"]).status === 404, "removed, once");

// fit again with a new measurement: kept, and marked when it no longer fits
SHAPES[3] = circle(10.4, 0.03);
version = 4;
call("POST", ["refit"], {});
set = (await call("GET", []).json()) as FeatureSet;
const n = set.features[2].nominals![0];
check(n.nominal === 10 && n.check_again && n.measured === 10.04 && n.measured_now === 10.4 && !!n.check_reason?.includes("10.40"), `check again: ${JSON.stringify(n)}`);
check(call("POST", [hole.id, "nominal"], { value: "diameter", nominal: 10.4, source: "entered" }).status === 200, "approved again");
set = (await call("GET", []).json()) as FeatureSet;
check(set.features[2].nominals![0].check_again === false && set.features[2].nominals![0].source === "entered", "the mark is gone");

// the CAD summary lists them; measured only lists none
let s = (await call("GET", ["cad"]).json()) as CadSummary;
check(s.nominal?.sizes_used === 1 && s.notes.some((x) => x.includes("nominal diameter 10.4 mm")), `CAD: ${JSON.stringify(s.nominal)}`);
s = (await call("GET", ["cad"], { measured_only: "true" }).json()) as CadSummary;
check(s.nominal?.measured_only === true && s.nominal.sizes_used === 0 && !s.notes.some((x) => x.includes("nominal")), "measured only");

// deleting the fit takes its approvals
check(call("DELETE", [hole.id]).status === 204, "fit deleted");
check(call("GET", [hole.id, "nominal"]).status === 404, "its approvals went with it");

console.log(`nominal.test (mock): ${checks} checks passed`);
