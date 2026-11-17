/**
 * Checks for the relation of two fits as the viewer shows it from the engine's record (`npm test`): the values and
 * their ± as the engine gives them, the engine's labels and notes, no ± of the viewer's own where the engine gives
 * none; and the demo backend's record (lib/fits.ts `relationRecord`), which must name its values and say "parallel"
 * as the engine does. Plain TypeScript that Node runs as it is (type stripping), no test framework.
 */
import type { CircleShape, PlaneShape, RelationRecord } from "../api/types";
import { relate, relationRecord } from "./fits.ts";
import { RELATION_NO_SIGMA, RELATION_NO_SIGMA_DEMO, RELATION_SIGMA_NOTE, fmtValue, relationFooter, relationRows } from "./relationView.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`relationView.test: ${what}`);
}

// ------------------------------------------------------------------ the engine's record, shown
{
  const r: RelationRecord = {
    kinds: ["plane", "plane"],
    parallel: true,
    values: [
      { name: "angle", label: "Angle (parallel)", value: 0.0312, sigma: 0.0214, unit: "deg" },
      { name: "distance", label: "Distance", value: 20.00412, sigma: 0.0063, unit: "mm" },
    ],
    notes: [],
  };
  const rows = relationRows(r);
  check(rows.map((x) => x.label).join() === "Angle (parallel),Distance", "the engine's labels, in its order");
  check(rows[0].value === "0.03 ± 0.02°" && rows[1].value === "20.004 ± 0.006 mm", `values ${rows.map((x) => x.value)}`);
  check(rows[0].x === 0.0312 && rows[1].sigma === 0.0063, "the numbers as the engine gave them");
  check(rows.every((x) => x.tip.includes(RELATION_SIGMA_NOTE)), "each ± says what it covers");
  check(relationFooter(r).join() === RELATION_SIGMA_NOTE, "footer: what the ± are");
}
{
  // a hole's axis has no ±: the engine says None, the viewer shows none and says why
  const r: RelationRecord = {
    kinds: ["hole", "plane"],
    parallel: false,
    values: [
      { name: "angle", label: "Angle of the hole's axis to the plane", value: 89.98, sigma: null, unit: "deg" },
      { name: "distance", label: "Hole centre to plane", value: 0.12, sigma: 0.004, unit: "mm" },
    ],
    notes: [],
  };
  const rows = relationRows(r);
  check(rows[0].value === "89.98°" && !rows[0].value.includes("±") && rows[0].sigma === null, `no ± of its own: ${rows[0].value}`);
  check(rows[0].tip.includes(RELATION_NO_SIGMA) && rows[0].tip.startsWith("90° square to the plane"), "the hover says why");
  check(rows[1].value === "0.120 ± 0.004 mm", `a distance with its ±: ${rows[1].value}`);
  check(relationFooter(r).length === 2 && relationFooter(r)[1] === RELATION_NO_SIGMA, "footer: both lines");
}
{
  // the demo backend's angles have no ± and no hole: not the hole's words
  const r: RelationRecord = { kinds: ["plane", "plane"], parallel: false, values: [{ name: "angle", label: "Angle", value: 30, sigma: null, unit: "deg" }], notes: [] };
  check(relationFooter(r).join() === RELATION_NO_SIGMA_DEMO && relationRows(r)[0].tip.endsWith(RELATION_NO_SIGMA_DEMO), "no hole: the demo's words");
}
check(fmtValue({ value: 14.0234, sigma: null, unit: "mm" }) === "14.02 mm" && fmtValue({ value: 45, sigma: 1.4, unit: "deg" }) === "45.00 ± 1.40°", "formats");

// ------------------------------------------------------------------ the demo backend's record
{
  const p1: PlaneShape = { kind: "plane", point: [0, 0, 0], normal: [0, 0, 1], rms_mm: 0.01, flatness_mm: 0.05, n: 100, normal_sigma_deg: 0.02, offset_sigma_mm: 0.003 };
  const p2: PlaneShape = { ...p1, point: [3, 1, 12], normal: [0, 0.01, 1], offset_sigma_mm: 0.004 };
  const p3: PlaneShape = { ...p1, normal: [1, 0, 0] };
  const h: CircleShape = { kind: "circle", center: [5, 5, 0], normal: [0, 0, 1], radius_mm: 3, diameter_mm: 6, rms_mm: 0.01, n: 50, radius_sigma_mm: 0.01, diameter_sigma_mm: 0.02, center_sigma_mm: 0.01 };
  const a = { kind: "plane" as const, shape: p1 },
    b = { kind: "plane" as const, shape: p2 };
  const rec = relationRecord(a, b);
  const old = relate(a, b);
  check(rec.parallel && rec.kinds.join() === "plane,plane", "parallel planes");
  check(rec.values.map((q) => `${q.name}/${q.unit}`).join() === "angle/deg,distance/mm", "named as the engine names them");
  check(rec.values.every((q, i) => q.value === old.rows[i].x && q.sigma === old.rows[i].sigma && q.label === old.rows[i].label), "the same values as relate()");
  const square = relationRecord(a, { kind: "plane", shape: p3 });
  check(!square.parallel && square.notes.length === 1 && square.values.length === 1, "square planes: an angle and a note");
  const hp = relationRecord({ kind: "hole", shape: h }, a);
  check(hp.kinds.join() === "hole,plane" && !hp.parallel && hp.values[1].label === "Hole centre to plane", "a hole and a plane");
  const holes = relationRecord({ kind: "hole", shape: h }, { kind: "hole", shape: { ...h, center: [9, 8, 0] } });
  check(holes.parallel && holes.values.map((q) => q.name).join() === "angle,distance,centre_distance", "two holes: the centre distance named");
}

console.log(`relationView.test: ${checks} checks passed`);
