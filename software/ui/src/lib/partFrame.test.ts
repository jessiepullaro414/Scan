/**
 * Checks for the part's frame as the viewer uses it (`partFrame.ts`, `npm test`): reading the record, turning
 * positions and directions into it (sizes and lengths unchanged), the axes to draw, and "Build again"'s body.
 * Plain TypeScript that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { CircleShape, CylinderShape, PartFrameView, PlaneShape } from "../api/types.ts";
import { axesInScan, deltaOf, dirToPart, fmtVec, frameProblem, originInScan, placeOf, pointToPart, readFrame, rebuildBody } from "./partFrame.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`partFrame.test: ${what}`);
}
const near = (a: ArrayLike<number>, b: ArrayLike<number>, tol = 1e-12) => a.length === b.length && Array.from(a).every((x, i) => Math.abs(x - b[i]) <= tol);

// the part's X is the scan's y, its Y the scan's -x, its Z the scan's z; the part's origin is at (2, -1, 5) in the scan
const R = [
  [0, 1, 0],
  [-1, 0, 0],
  [0, 0, 1],
];
const O = [2, -1, 5];
const t = R.map((row) => -(row[0] * O[0] + row[1] * O[1] + row[2] * O[2]));
const frame: PartFrameView = {
  format_version: 1,
  units: "mm",
  matrix: [[...R[0], t[0]], [...R[1], t[1]], [...R[2], t[2]], [0, 0, 0, 1]],
  axes: { primary: "Z", secondary: "X", third: "Y" },
  flip_primary: false,
  flip_secondary: true,
  secondary_zero: false,
  built_from: [
    { role: "primary", id: "f-top", name: "Top", kind: "plane", sets: "Z", fit: { kind: "plane" } },
    { role: "secondary", id: "f-side", name: "Side", kind: "plane", sets: "X", fit: { kind: "plane" } },
    { role: "origin", id: "f-hole", name: "Hole", kind: "circle", sets: "X = 0 and Y = 0", fit: { kind: "circle" } },
  ],
  sigma: { angle_deg: 0.02, tilt_deg: 0.01, turn_deg: 0.015, origin_mm: 0.004, origin_axes_mm: [0.003, 0.002, 0.001] },
  warnings: [],
  readable: true,
  out_of_date: false,
  out_of_date_reasons: [],
};

// ------------------------------------------------------------------ reading the record
check(readFrame(frame) === frame, "a readable frame is read as it is");
check(readFrame(null) === null && readFrame({}) === null && readFrame([1]) === null, "none, empty, not an object");
check(readFrame({ ...frame, format_version: 2 }) === null, "a later format");
check(readFrame({ ...frame, readable: false }) === null, "the engine says it can't be read");
check(readFrame({ ...frame, matrix: [[2, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]] }) === null, "a matrix that scales");
check(readFrame({ ...frame, axes: { primary: "Z", secondary: "Z", third: "Y" } }) === null, "axes named twice");
check(frameProblem(frame) === null && frameProblem(null) === null, "no problem with a good one, or none");
check(frameProblem({ format_version: 9, readable: false, out_of_date: true, out_of_date_reasons: ["format 9"] }) === "format 9", "the engine's reason");
check(typeof frameProblem({ odd: true }) === "string", "something unreadable says so");

// ------------------------------------------------------------------ points and directions
check(near(pointToPart(frame, O), [0, 0, 0]), "the origin is (0, 0, 0)");
check(near(originInScan(frame), O), "and back in the scan's frame");
check(near(pointToPart(frame, [2, 0, 5]), [1, 0, 0]), "one along the scan's y is one along the part's X");
check(near(pointToPart(frame, [1, -1, 7]), [0, 1, 2]), "the part's Y is the scan's -x");
check(near(dirToPart(frame, [0, 1, 0]), [1, 0, 0]) && near(dirToPart(frame, [0, 0, -1]), [0, 0, -1]), "directions only turn");
const ax = axesInScan(frame);
check(ax.axes.map((a) => a.name).join() === "X,Y,Z" && near(ax.axes[0].dir, [0, 1, 0]) && near(ax.axes[1].dir, [-1, 0, 0]) && near(ax.origin, O), "the axes to draw");

// ------------------------------------------------------------------ the panels' numbers
const plane: PlaneShape = { kind: "plane", point: [2, -1, 5], normal: [0, 0, 1], rms_mm: 0.01, flatness_mm: 0.05, n: 10, normal_sigma_deg: 0.1, offset_sigma_mm: 0.01 };
const hole: CircleShape = { kind: "circle", center: [5, 0, 5], normal: [0, 0, 1], radius_mm: 3, diameter_mm: 6, rms_mm: 0.01, n: 60, radius_sigma_mm: 0.002, diameter_sigma_mm: 0.004, center_sigma_mm: 0.003 };
const bore: CylinderShape = { kind: "cylinder", point: [2, 3, 0], axis: [0, 1, 0], radius_mm: 2, diameter_mm: 4, rms_mm: 0.01, n: 200, radius_sigma_mm: 0.002, diameter_sigma_mm: 0.004, axis_sigma_deg: 0.05, point_sigma_mm: 0.004 };
{
  const s = placeOf(plane, null);
  check(near(s.position, [2, -1, 5]) && near(s.direction, [0, 0, 1]) && s.positionLabel === "Point" && s.directionLabel === "Normal", "a plane in the scan's frame");
  const p = placeOf(plane, frame);
  check(near(p.position, [0, 0, 0]) && near(p.direction, [0, 0, 1]), "the plane through the origin, along Z");
  const h = placeOf(hole, frame);
  check(h.positionLabel === "Centre" && near(h.position, [1, -3, 0]), "a hole's centre in the part's frame");
  const c = placeOf(bore, frame);
  check(c.positionLabel === "Axis point" && c.directionLabel === "Axis" && near(c.direction, [1, 0, 0]) && near(c.position, [4, 0, -5]), "a cylinder's axis");
  check(hole.diameter_mm === 6 && hole.center[0] === 5, "the shape itself is left alone");
}
{
  const a = [1, 2, 3],
    b = [4, 6, 3];
  const d0 = deltaOf(a, b, null),
    d1 = deltaOf(a, b, frame);
  check(near(d0, [3, 4, 0]) && near(d1, [4, -3, 0]), "Δx / Δy / Δz in each frame");
  check(Math.abs(Math.hypot(...d0) - Math.hypot(...d1)) < 1e-12, "the distance doesn't change");
}
check(fmtVec([1.234, -0.001, 12]) === "1.23, 0.00, 12.00" && fmtVec([0.5, -0.25, 1], 3) === "0.500, −0.250, 1.000", "numbers as the panels show them, with a true minus (U32)");

// ------------------------------------------------------------------ build again
{
  const b = rebuildBody(frame);
  check(!!b && b.primary === "f-top" && b.secondary === "f-side" && b.origin === "f-hole", "the same three fits");
  check(!!b && b.axes === "ZX" && b.flip_secondary === true && b.flip_primary === false && b.secondary_zero === false, "and the same options");
  const withPoint: PartFrameView = {
    ...frame,
    built_from: [frame.built_from[0], frame.built_from[1], { role: "origin", id: null, name: null, kind: "point", sets: "", fit: { kind: "point", point: [1, 2, 3], sigma_mm: 0.01 } }],
  };
  const p = rebuildBody(withPoint);
  check(!!p && typeof p.origin === "object" && near(p.origin.point, [1, 2, 3]) && p.origin.sigma_mm === 0.01, "a point origin comes back as the point");
  const broken: PartFrameView = { ...frame, built_from: [frame.built_from[0], { ...frame.built_from[1], id: null }, frame.built_from[2]] };
  check(rebuildBody(broken) === null, "a slot without an id can't be built again");
}

console.log(`partFrame.test: ${checks} checks passed`);
