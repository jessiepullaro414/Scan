/**
 * Checks for the demo engine's kept fits for CAD (`npm test`; `features.ts` routes, `cad.ts`): the summary's counts,
 * frame, warnings and names, the reason with no kept fits, the files (small fixed texts) with their names, 409
 * without kept fits, 404 for another file and 422 for another boundary. Plain TypeScript that Node runs as it is
 * (type stripping), no test framework.
 */
import type { CadSummary, FitResult, FitShape } from "../types";
import { cadFileStem } from "./cad.ts";
import { MockFeatures, type FeatureEngine } from "./features.ts";
import { HttpError } from "./host.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`cad.test: ${what}`);
}

const SHAPES: Record<number, FitShape> = {
  1: { kind: "plane", point: [0, 0, 10], normal: [0, 0, 1], rms_mm: 0.01, flatness_mm: 0.05, n: 900, normal_sigma_deg: 0.03, offset_sigma_mm: 0.004 },
  2: { kind: "plane", point: [10, 0, 0], normal: [1, 0, 0], rms_mm: 0.01, flatness_mm: 0.05, n: 900, normal_sigma_deg: 0.03, offset_sigma_mm: 0.004 },
  3: { kind: "circle", center: [3, -2, 10], normal: [0, 0, 1], radius_mm: 3, diameter_mm: 6, rms_mm: 0.01, n: 80, radius_sigma_mm: 0.003, diameter_sigma_mm: 0.006, center_sigma_mm: 0.004 },
};
let version: number | null = 3;
const engine: FeatureEngine = {
  version: () => version,
  nearestFace: () => 1,
  names: () => ({ part: "Bracket", scan: "Top / left: 2" }),
  fit: (body): FitResult => {
    const shape = SHAPES[Number(body.face)];
    return { kind: shape.kind === "circle" ? "hole" : (shape.kind as "plane" | "cylinder"), shape, faces: "AQAAAA==", n_faces: 1, tolerance_mm: null, mean_sigma_mm: null, correlated_points: null, warnings: [], version: version! };
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
const summary = async (boundary?: string) => (await call("GET", ["cad"], boundary ? { boundary } : {}).json()) as CadSummary;

// no kept fits: a reason, and no file
let s = await summary();
check(s.n_fits === 0 && !!s.reason && s.reason.includes("no fitted shapes"), "the reason without kept fits");
check(s.step_bytes === null && s.notes.length === 0, "no sizes, no notes");
for (const name of ["fits.step", "fits.FCMacro"]) {
  const r = call("GET", ["cad", name]);
  check(r.status === 409 && ((await r.json()) as { detail: string }).detail.includes("no fitted shapes"), `409 for ${name}`);
}

// three kept fits, no frame: counts, the scan's frame, names after the scan
for (const face of [1, 2, 3]) check(call("POST", [], { kind: face === 3 ? "hole" : "plane", face, point: [0, 0, 0] }).status === 200, `keep fit ${face}`);
s = await summary();
check(s.n_fits === 3 && s.counts.plane === 2 && s.counts.hole === 1 && s.counts.cylinder === 0, "the counts");
check(s.frame === "scan" && s.frame_label === "the scan's own axes", "the scan's frame");
check(s.notes.some((n) => n.includes("no part frame")) && s.notes.some((n) => n.includes("demo")), "the notes say so");
check(s.warnings.length === 0 && s.reason === null && s.boundary === "rectangle", "nothing to warn about");
check(s.filenames.step === "Top_left_2-fits.step" && s.filenames.macro === "Top_left_2-fits.FCMacro", `names: ${JSON.stringify(s.filenames)}`);
check(cadFileStem("  ") === "scan" && cadFileStem("Ø14 boss") === "14_boss", "the stem as the engine makes it");

// the files: small fixed texts, named, the sizes as the summary says
const step = call("GET", ["cad", "fits.step"]);
const stepText = await step.text();
check(step.status === 200 && stepText.startsWith("ISO-10303-21;") && stepText.length === s.step_bytes, "the STEP text");
check(step.headers.get("content-disposition")!.includes("Top_left_2-fits.step"), "the STEP name");
const macro = call("GET", ["cad", "fits.FCMacro"]);
const macroText = await macro.text();
check(macro.status === 200 && macroText.includes("import FreeCAD") && macroText.length === s.macro_bytes, "the macro text");
check(macro.headers.get("content-disposition")!.includes(".FCMacro"), "the macro name");
check(call("GET", ["cad", "fits.iges"]).status === 404, "404 for another file");
check(call("GET", ["cad"], { boundary: "lasso" }).status === 422, "422 for another boundary");
check((await summary("outline")).boundary === "outline" && (await summary("outline")).outline?.planes === 0, "outline: no outlines kept here");

// a fit made on an earlier mesh is exported, and said so
version = 4;
s = await summary();
check(s.n_fits === 3 && s.stale.length === 3 && s.warnings.length === 3 && s.warnings[0].includes("earlier mesh"), "stale fits are warned about");

console.log(`cad.test: ${checks} checks passed`);
