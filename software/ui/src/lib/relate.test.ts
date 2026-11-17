/**
 * The viewer's relations and the engine's are one (`npm test`): the shared fixture
 * `software/tests/data/relations.json` holds shapes and the relation values for them, and
 * `tests/test_measure_relate.py` checks `ohs.measure.relate` against the same file. Here `relate()`
 * must give the same rows (labels, values to 1e-6) and notes, and `summarizeRegion` the same
 * extent along a cylinder's axis as `ohs.measure.region_extent`. Plain TypeScript that Node runs as
 * it is (type stripping), no test framework: a failed check throws.
 */
import type { CylinderShape } from "../api/types";
import { type FitPart, relate, summarizeRegion } from "./fits.ts";

type Row = { label: string; x: number };
type Case = { name: string; a: FitPart; b: FitPart; rows: Row[]; notes: string[] };
type Fixture = {
  relations: Case[];
  along: { positions: number[]; faces: number[][]; region: number[]; shape: CylinderShape; along: [number, number] };
};

// a path in a variable: Node reads the JSON by itself, and the type checker does not look for the file
const path = "../../../tests/data/relations.json";
const fixture = ((await import(path, { with: { type: "json" } })) as { default: Fixture }).default;

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`relate.test: ${what}`);
}
const TOL = 1e-6;

// ------------------------------------------------------------------ relations
check(fixture.relations.length >= 20, "the fixture has its cases");
for (const c of fixture.relations) {
  for (const [a, b] of [
    [c.a, c.b],
    [c.b, c.a],
  ]) {
    const r = relate(a, b);
    const got = r.rows.map((row) => row.label);
    const want = c.rows.map((row) => row.label);
    check(got.join(" | ") === want.join(" | "), `${c.name}: rows ${got.join(", ")} (want ${want.join(", ")})`);
    r.rows.forEach((row, i) => check(Math.abs(row.x - c.rows[i].x) <= TOL, `${c.name}: ${row.label} ${row.x} (want ${c.rows[i].x})`));
    check(r.notes.join(" | ") === c.notes.join(" | "), `${c.name}: notes "${r.notes.join(" | ")}"`);
  }
}

// ------------------------------------------------------------------ a cylinder's extent along its axis
{
  const f = fixture.along;
  const tris = f.region.flatMap((k) => f.faces[k]);
  const s = summarizeRegion(tris, f.positions, f.shape);
  check(s.along !== null, "a cylinder has an extent along its axis");
  check(Math.abs(s.along![0] - f.along[0]) <= TOL && Math.abs(s.along![1] - f.along[1]) <= TOL, `along ${s.along} (want ${f.along})`);
}

console.log(`relate.test: ${checks} checks passed`);
