/**
 * Checks for the kept set's changes (`featureSet.ts`, `npm test`). Plain TypeScript that Node runs as
 * it is (type stripping), no test framework: a failed check throws.
 */
import type { FeatureSet, KeptFeature, KeptFit, KeptMeasurement } from "../api/types.ts";
import { staleFits, withFeature, withMeasurement, withName, withRelation, without } from "./featureSet.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`featureSet.test: ${what}`);
}

const empty: FeatureSet = { format_version: 1, frame: { kept: [1, 2] }, mesh_version: 4, features: [], measurements: [], relations: [] };
const fit = (id: string, stale = false): KeptFeature => ({
  id,
  name: `Fit ${id}`,
  kind: "plane",
  shape: { kind: "plane", point: [0, 0, 0], normal: [0, 0, 1], rms_mm: 0.01, flatness_mm: 0.05, n: 10, normal_sigma_deg: 0.1, offset_sigma_mm: 0.01 },
  seed: { kind: "plane", face: 1, point: [0, 0, 0], tolerance_mm: null },
  n_faces: 2,
  tolerance_mm: 0.2,
  mean_sigma_mm: null,
  correlated_points: null,
  warnings: [],
  mesh_version: stale ? 3 : 4,
  extent: null,
  created: "2026-11-17T10:00:00",
  stale,
});
const meas: KeptMeasurement = { id: "m-1", name: "Distance 1", a: [0, 0, 0], b: [1, 0, 0], mesh_version: 4, created: "", stale: false };

const kept: KeptFit = { ...fit("f-1"), faces: "AQAAAA==" };
let s = withFeature(empty, kept);
check(s.features.length === 1 && !("faces" in s.features[0]), "a kept fit joins the set without its region");
check(s.frame === empty.frame && empty.features.length === 0, "the frame is untouched, the old set unchanged");
s = withFeature(s, fit("f-2", true));
s = withFeature(s, { ...fit("f-1"), name: "Again" });
check(s.features.map((f) => f.id).join() === "f-1,f-2" && s.features[0].name === "Again", "the same id again replaces it in place");
s = withMeasurement(s, meas);
s = withRelation(s, { id: "r-1", name: "Relation 1", a: "f-1", b: "f-2", created: "" });
check(s.measurements.length === 1 && s.relations.length === 1, "a measurement and a relation");
const named = withName(s, "m-1", "Width");
check(named.measurements[0].name === "Width" && named.features === s.features && named.relations === s.relations, "rename touches only that list");
check(withName(s, "r-1", "Twins").relations[0].name === "Twins", "rename a relation");
check(staleFits(s) === 1 && staleFits(null) === 0, "one fit made on an earlier mesh");
const gone = without(s, "f-2");
check(gone.features.length === 1 && gone.relations.length === 0, "a fit takes its relations along");
check(without(s, "m-1").measurements.length === 0 && without(s, "m-1").relations.length === 1, "a measurement goes alone");

console.log(`featureSet.test: ${checks} checks passed`);
