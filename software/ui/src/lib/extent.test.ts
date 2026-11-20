/**
 * Checks for a kept fit's extent as the viewer shows it (`npm test`): the card's lines (a plane's size and area, a
 * cylinder's length and arc, nothing for a hole or without an extent), a plane's rectangle and a cylinder's axis
 * and arc in 3D. Plain TypeScript that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { CylinderExtent, CylinderShape, PlaneExtent } from "../api/types";
import { arcText, cylinderMarks, extentRows, fmtArea, planeSize, rectangleOutline } from "./extent.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`extent.test: ${what}`);
}
const near = (a: number, b: number, tol = 1e-9) => Math.abs(a - b) <= tol;
const nearV = (a: ArrayLike<number>, b: ArrayLike<number>, tol = 1e-9) => near(a[0], b[0], tol) && near(a[1], b[1], tol) && near(a[2], b[2], tol);

const plane: PlaneExtent = {
  kind: "plane",
  origin: [0, 0, 5],
  u: [1, 0, 0],
  v: [0, 1, 0],
  rectangle: { center: [10, 20, 5], axes: [[0, 1, 0], [-1, 0, 0]], half_sizes_mm: [60, 40] },
  area_mm2: 9123.4,
  n_outer: 1,
  n_holes: 3,
  n_points: 412,
};

// ------------------------------------------------------------------ the card
{
  check(planeSize(plane) === "120.0 × 80.0\u202fmm", `plane size ${planeSize(plane)}`);
  check(planeSize({ ...plane, rectangle: { ...plane.rectangle, half_sizes_mm: [4.2, 0.35] } }) === "8.40 × 0.70\u202fmm", "small plane: the longer side's decimals");
  check(fmtArea(9123.4) === "9,123\u202fmm²" && fmtArea(56.24) === "56.2\u202fmm²" && fmtArea(0.844) === "0.84\u202fmm²", "areas");
  const rows = extentRows(plane);
  check(rows.map((r) => r[0]).join() === "Size,Area", "a plane: size and area");
  check(rows[0][1] === "120.0 × 80.0\u202fmm" && rows[1][1] === "9,123\u202fmm²", "a plane's values");
  check(rows[1][2].includes("3 holes") && rows.every((r) => r[2].length > 20), "hover texts say what it is");
  const cyl: CylinderExtent = { kind: "cylinder", along_mm: [-2, 10.5], length_mm: 12.5, zero: [1, 0, 0], start_deg: 30, arc_deg: 182.4 };
  const cr = extentRows(cyl);
  check(cr.map((r) => r[0]).join() === "Length,Arc" && cr[0][1] === "12.50\u202fmm" && cr[1][1] === "182° of the round", `a cylinder: ${cr.map((r) => r[1])}`);
  check(arcText({ ...cyl, start_deg: 0, arc_deg: 360 }) === "all round", "a whole round");
  check(extentRows({ kind: "hole", n_edge: 40, edge: null }).length === 0, "a hole: nothing new");
  check(extentRows(null).length === 0 && extentRows(undefined).length === 0, "no extent: nothing");
}

// ------------------------------------------------------------------ a plane's rectangle
{
  const r = rectangleOutline(plane);
  check(r.length === 5 && nearV(r[0], r[4]), "closed: five corners");
  // the first axis is +y (60 mm half), the second -x (40 mm half)
  check(nearV(r[0], [10 + 40, 20 - 60, 5]) && nearV(r[2], [10 - 40, 20 + 60, 5]), `corners ${JSON.stringify(r)}`);
  const sides = [0, 1, 2, 3].map((k) => Math.hypot(r[k + 1][0] - r[k][0], r[k + 1][1] - r[k][1], r[k + 1][2] - r[k][2]));
  check(near(sides[0], 120) && near(sides[1], 80) && near(sides[2], 120) && near(sides[3], 80), `sides ${sides}`);
}

// ------------------------------------------------------------------ a cylinder's axis and arc
{
  const s: CylinderShape = { kind: "cylinder", point: [1, 2, 3], axis: [0, 0, 2], radius_mm: 5, diameter_mm: 10, rms_mm: 0.01, n: 100, radius_sigma_mm: 0.01, diameter_sigma_mm: 0.02, axis_sigma_deg: 0.1, point_sigma_mm: 0.01 };
  const e: CylinderExtent = { kind: "cylinder", along_mm: [-4, 6], length_mm: 10, zero: [1, 0, 0], start_deg: 0, arc_deg: 90 };
  const m = cylinderMarks(s, e, 1);
  check(nearV(m.axis[0], [1, 2, 3 - 5]) && nearV(m.axis[1], [1, 2, 3 + 7]), `axis over the span and the margin: ${JSON.stringify(m.axis)}`);
  // half way along (z = 3 + 1), from zero (+x) turning right-handed about +z, through 90°: ends at +y
  check(nearV(m.arc[0], [6, 2, 4]) && nearV(m.arc[m.arc.length - 1], [1, 7, 4]), `arc ends ${JSON.stringify([m.arc[0], m.arc[m.arc.length - 1]])}`);
  check(m.arc.every((p) => near(Math.hypot(p[0] - 1, p[1] - 2), 5) && near(p[2], 4)), "the arc lies on the cylinder, half way along");
  const whole = cylinderMarks(s, { ...e, arc_deg: 360 }, 1);
  check(nearV(whole.arc[0], whole.arc[whole.arc.length - 1]) && whole.arc.length > 60, "all round: a closed circle");
}

console.log(`extent.test: ${checks} checks passed`);
