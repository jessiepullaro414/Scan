/**
 * Checks for the fitted shapes' pure part (`npm test`): the face list codec, the region's triangles,
 * the region summary, the labels and the relation geometry with its ± rules. Plain TypeScript that
 * Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { CircleShape, CylinderShape, PlaneShape } from "../api/types";
import {
  decodeFaces,
  encodeFaces,
  fitLabel,
  fmtSmall,
  lineAngleDeg,
  lineLineDistance,
  parallelLineDistance,
  planePlaneDistance,
  pointPlaneDistance,
  regionTriangles,
  relate,
  rss,
  summarizeRegion,
} from "./fits.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`fits.test: ${what}`);
}
function throws(f: () => unknown): boolean {
  try {
    f();
  } catch {
    return true;
  }
  return false;
}
const near = (a: number, b: number, tol = 1e-9) => Math.abs(a - b) <= tol;
const same = (a: ArrayLike<number>, b: number[]) => a.length === b.length && b.every((v, i) => a[i] === v);

// ------------------------------------------------------------------ face list codec
{
  // [1, 256] as little-endian uint32: 01 00 00 00 00 01 00 00
  check(encodeFaces([1, 256]) === "AQAAAAABAAA=", "encode: little-endian bytes");
  check(same(decodeFaces("AQAAAAABAAA="), [1, 256]), "decode: little-endian bytes");
  check(decodeFaces("").length === 0, "decode: empty list");
  const big = new Uint32Array(100_003);
  for (let i = 0; i < big.length; i++) big[i] = (i * 2654435761) >>> 0;
  big[0] = 0;
  big[1] = 0xffffffff;
  const back = decodeFaces(encodeFaces(big));
  check(back.length === big.length && back.every((v, i) => v === big[i]), "round trip of 100,003 faces, 0 and 2^32 - 1 included");
  check(throws(() => decodeFaces("AQID")), "decode: 3 bytes is not a whole uint32");
  check(throws(() => decodeFaces("not base64!")), "decode: bad base64 throws");
}

// ------------------------------------------------------------------ the region's triangles
{
  const index = [0, 1, 2, 2, 1, 3, 3, 1, 4];
  check(same(regionTriangles(Uint32Array.from([0, 2]), index, null), [0, 1, 2, 3, 1, 4]), "1:1 faces: the corners of faces 0 and 2");
  check(throws(() => regionTriangles(Uint32Array.from([3]), index, null)), "1:1 faces: face 3 of 3 throws");
  // a quad (face 0 = triangles 0, 1), a triangle (face 1), a degenerate face 2 the reader skipped, a triangle (face 3)
  const idx2 = [0, 1, 2, 0, 2, 3, 4, 5, 6, 7, 8, 9];
  const faceOfTri = Uint32Array.from([0, 0, 1, 3]);
  check(same(regionTriangles(Uint32Array.from([0]), idx2, faceOfTri, 4), [0, 1, 2, 0, 2, 3]), "a quad: both its triangles");
  check(same(regionTriangles(Uint32Array.from([1, 3]), idx2, faceOfTri, 4), [4, 5, 6, 7, 8, 9]), "faces after a quad and a skipped face");
  check(regionTriangles(Uint32Array.from([2]), idx2, faceOfTri, 4).length === 0, "a skipped (degenerate) face has no triangles");
  check(throws(() => regionTriangles(Uint32Array.from([4]), idx2, faceOfTri, 4)), "face 4 of 4 throws");
}

// ------------------------------------------------------------------ region summary
{
  // a cylinder of radius 7 along z from z = 2 to z = 12, axis point at the origin; vertex 0 not in the region
  const pos: number[] = [99, 99, 99];
  const tris: number[] = [];
  for (let k = 0; k < 36; k++) {
    const a = (k / 36) * 2 * Math.PI;
    pos.push(7 * Math.cos(a), 7 * Math.sin(a), 2, 7 * Math.cos(a), 7 * Math.sin(a), 12);
  }
  for (let k = 0; k < 36; k++) {
    const a = 1 + 2 * k,
      b = 1 + 2 * ((k + 1) % 36);
    tris.push(a, a + 1, b, b, a + 1, b + 1);
  }
  const cyl: CylinderShape = {
    kind: "cylinder",
    point: [0, 0, 0],
    axis: [0, 0, 1],
    radius_mm: 7,
    diameter_mm: 14,
    rms_mm: 0,
    n: 72,
    radius_sigma_mm: 0.01,
    diameter_sigma_mm: 0.02,
    axis_sigma_deg: 0.1,
    point_sigma_mm: 0.01,
  };
  const s = summarizeRegion(tris, pos, cyl, [7, 0, 5]);
  check(s.vertices === 72, "summary: 72 distinct vertices");
  check(s.along !== null && near(s.along[0], 2, 1e-6) && near(s.along[1], 12, 1e-6), "summary: extent along the axis 2..12");
  check(near(s.anchor[2], 7, 1e-6) && near(s.anchor[0], 0, 1e-9), "summary: the label at the middle of the axis");
  check(near(s.spread, 7, 1e-6), "summary: spread across the axis is the radius");
  check(summarizeRegion(tris, pos, cyl).anchor[2] === s.anchor[2], "summary: a cylinder's label ignores the click");
  // a plane z = 1 through (0, 0, 1): its label goes to the click, put onto the plane
  const pl: PlaneShape = { kind: "plane", point: [0, 0, 1], normal: [0, 0, 1], rms_mm: 0, flatness_mm: 0, n: 3, normal_sigma_deg: 0, offset_sigma_mm: 0 };
  const sp = summarizeRegion([1, 2, 3], [0, 0, 0, 40, 0, 1, 40, 10, 1, 50, 0, 1], pl, [42, 3, 1.2]);
  check(sp.along === null && near(sp.anchor[0], 42) && near(sp.anchor[1], 3) && near(sp.anchor[2], 1, 1e-12), "summary: a plane's label at the click, on the plane");
}

// ------------------------------------------------------------------ labels
const plane = (point: number[], normal: number[], offset = 0.01, flat = 0.04): PlaneShape => ({
  kind: "plane",
  point,
  normal,
  rms_mm: 0.01,
  flatness_mm: flat,
  n: 500,
  normal_sigma_deg: 0.05,
  offset_sigma_mm: offset,
});
const cylinder = (point: number[], axis: number[], d = 14.02, sd = 0.03): CylinderShape => ({
  kind: "cylinder",
  point,
  axis,
  radius_mm: d / 2,
  diameter_mm: d,
  rms_mm: 0.02,
  n: 800,
  radius_sigma_mm: sd / 2,
  diameter_sigma_mm: sd,
  axis_sigma_deg: 0.2,
  point_sigma_mm: 0.02,
});
const circle = (center: number[], normal: number[], d = 25.01, sd = 0.05): CircleShape => ({
  kind: "circle",
  center,
  normal,
  radius_mm: d / 2,
  diameter_mm: d,
  rms_mm: 0.03,
  n: 120,
  radius_sigma_mm: sd / 2,
  diameter_sigma_mm: sd,
  center_sigma_mm: 0.04,
});
{
  check(fitLabel("plane", plane([0, 0, 0], [0, 0, 1])) === "Plane · flat within 0.04 mm", "label: plane");
  check(fitLabel("cylinder", cylinder([0, 0, 0], [0, 0, 1])) === "Cylinder Ø 14.02 ± 0.03 mm", "label: cylinder");
  check(fitLabel("hole", circle([0, 0, 0], [0, 0, 1])) === "Hole Ø 25.01 ± 0.05 mm", "label: hole");
  check(fitLabel("hole", circle([0, 0, 0], [0, 0, 1], 8.0213, 0.0042)) === "Hole Ø 8.021 ± 0.004 mm", "label: a small ± gets three decimals");
  check(fitLabel("plane", plane([0, 0, 0], [0, 0, 1], 0.01, 0.0042)) === "Plane · flat within 0.004 mm", "label: a very flat plane");
  check(fitLabel("cylinder", cylinder([0, 0, 0], [0, 0, 1], 20.00012, 0.0003)) === "Cylinder Ø 20.0001 ± 0.0003 mm", "label: a tiny ± is never 0.000");
  check(fmtSmall(0.035) === "0.035" && fmtSmall(0.0042) === "0.0042" && fmtSmall(0.00013) === "0.00013" && fmtSmall(0.5) === "0.50", "small 1σ: two significant digits");
}

// ------------------------------------------------------------------ geometry
{
  check(lineAngleDeg([0, 0, 1], [0, 0, 1]) === 0, "angle: parallel 0°");
  check(lineAngleDeg([0, 0, 1], [0, 0, -1]) === 0, "angle: opposite normals are parallel");
  check(near(lineAngleDeg([1, 0, 0], [0, 3, 0]), 90), "angle: square 90°");
  check(near(lineAngleDeg([1, 0, 0], [Math.cos(Math.PI / 6), Math.sin(Math.PI / 6), 0]), 30, 1e-9), "angle: 30°");
  check(near(lineAngleDeg([1, 0, 0], [-Math.cos(Math.PI / 6), Math.sin(Math.PI / 6), 0]), 30, 1e-9), "angle: 150° between lines is 30°");
  const tiny = 1e-7; // rad: acos would lose it
  check(near(lineAngleDeg([1, 0, 0], [Math.cos(tiny), Math.sin(tiny), 0]), (tiny * 180) / Math.PI, 1e-12), "angle: exact near 0");

  check(near(planePlaneDistance([0, 0, 0], [0, 0, 1], [30, -12, 5], [0, 0, 1]), 5), "planes: 5 mm apart, points offset sideways");
  check(near(planePlaneDistance([0, 0, 0], [0, 0, 1], [30, -12, 5], [0, 0, -1]), 5), "planes: opposite normals");
  check(near(planePlaneDistance([1, 2, 3], [0, 2, 0], [1, -4, 3], [0, -1, 0]), 6), "planes: normals not unit");

  check(near(parallelLineDistance([0, 0, 0], [0, 0, 1], [3, 4, 50], [0, 0, -1]), 5), "axes: parallel, 5 mm apart");
  check(near(lineLineDistance([0, 0, 0], [1, 0, 0], [5, 9, 7], [0, 1, 0]), 7), "axes: skew, 7 mm apart");
  check(near(lineLineDistance([0, 0, 0], [1, 0, 0], [5, 0, 0], [0, 1, 1]), 0), "axes: crossing, 0 mm");
  check(near(lineLineDistance([0, 0, 0], [0, 0, 1], [3, 4, 0], [0, 0, 1]), 5), "axes: parallel through the skew formula");
  check(near(pointPlaneDistance([2, 3, -8], [0, 0, 1], [0, 0, 2]), 9), "point to plane");
  check(near(rss(0.03, 0.04), 0.05), "root-sum-square");
}

// ------------------------------------------------------------------ relations and their ±
{
  const a = { kind: "plane" as const, shape: plane([0, 0, 0], [0, 0, 1], 0.03) };
  const b = { kind: "plane" as const, shape: plane([40, 10, 12.5], [0, 0, -1], 0.04) };
  const r = relate(a, b);
  const dist = r.rows.find((x) => x.label === "Distance");
  check(!!dist && near(dist.x, 12.5) && dist.sigma !== null && near(dist.sigma, 0.05), "parallel planes: distance 12.5 with ± = RSS of the offsets");
  check(!!dist && dist.value === "12.50 ± 0.05 mm" && /ignores their tilt/.test(dist.tip), "parallel planes: '12.50 ± 0.05 mm', the tip says it ignores tilt");
  check(r.rows[0].sigma === null && near(r.rows[0].x, 0), "parallel planes: the angle has no ±");

  const t = 1.5 * (Math.PI / 180);
  const r2 = relate(a, { kind: "plane", shape: plane([0, 0, 3], [Math.sin(t), 0, Math.cos(t)]) });
  check(r2.rows.some((x) => x.label === "Distance"), "planes 1.5° apart count as parallel");
  const t3 = 3 * (Math.PI / 180);
  const r3 = relate(a, { kind: "plane", shape: plane([0, 0, 3], [Math.sin(t3), 0, Math.cos(t3)]) });
  check(!r3.rows.some((x) => x.label === "Distance") && r3.notes.length === 1 && /Not parallel/.test(r3.notes[0]), "planes 3° apart: no distance, a note says why");
  check(near(r3.rows[0].x, 3, 1e-9), "planes 3° apart: the angle");

  const hole = { kind: "hole" as const, shape: circle([5, 5, 7], [0, 0, 1]) };
  const r4 = relate(a, hole);
  const c2p = r4.rows.find((x) => x.label === "Hole centre to plane");
  check(!!c2p && near(c2p.x, 7) && c2p.sigma === null, "hole centre to plane: 7 mm, no ±");
  check(near(r4.rows[0].x, 90) && /hole's axis/.test(r4.rows[0].label), "a hole square to the plane: 90°");
  check(near(relate(hole, a).rows[1].x, 7), "hole and plane in either order");

  const c1 = { kind: "cylinder" as const, shape: cylinder([0, 0, 0], [0, 0, 1]) };
  const c2 = { kind: "cylinder" as const, shape: cylinder([30, 40, 9], [0, 0, -1]) };
  const r5 = relate(c1, c2);
  const ax = r5.rows.find((x) => x.label === "Distance between the axes");
  check(!!ax && near(ax.x, 50) && ax.sigma === null, "parallel axes: 50 mm, no ±");
  const r6 = relate(c1, { kind: "cylinder", shape: cylinder([0, 0, 0], [1, 0, 0]) });
  check(near(r6.rows[0].x, 90) && r6.rows.some((x) => x.label === "Closest distance of the axes" && near(x.x, 0)), "crossing axes: 90°, closest distance 0");

  const h2 = { kind: "hole" as const, shape: circle([35, 5, 7], [0, 0, -1]) };
  const r7 = relate(hole, h2);
  check(r7.rows.some((x) => x.label === "Centre to centre" && near(x.x, 30)), "two holes: centre to centre 30 mm");

  // a cylinder lying along a plane: the axis to plane distance
  const r8 = relate(a, { kind: "cylinder", shape: cylinder([0, 0, 8], [1, 0, 0]) });
  check(r8.rows.some((x) => x.label === "Axis to plane" && near(x.x, 8)) && near(r8.rows[0].x, 0), "an axis along a plane: 0°, 8 mm above it");
  const r9 = relate(a, c1);
  check(near(r9.rows[0].x, 90) && r9.notes.length === 1 && !r9.rows.some((x) => x.label === "Axis to plane"), "an axis square to a plane: no distance");
  check([r4, r5, r6, r7, r8, r9].every((x) => x.rows.every((row) => row.sigma === null && /No ±/.test(row.tip))), "no ± where it can't be derived, and the tip says so");
}

console.log(`fits.test: ${checks} checks passed`);
