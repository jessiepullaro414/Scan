/**
 * Where a kept fit's region lies, as the engine measured it (APP_API.md "Kept features", `extent`;
 * ohs/measure/region.py "Extent"), the pure part: the lines a fit's card shows, and what the 3D view
 * draws from it (a plane's rectangle, a cylinder's span along its axis and the arc it covers).
 *
 * The engine is the source. A fit without an extent (kept before extents existed) is drawn as before,
 * from the viewer's own `summarizeRegion` (lib/fits.ts).
 *
 * Imports nothing at run time, so `npm test` checks it with plain node (extent.test.ts).
 */
import type { CylinderExtent, CylinderShape, FitExtent, PlaneExtent } from "../api/types";

export type Vec3 = [number, number, number];
/** A row of a fit's card: label, value, what it is (the hover text). */
export type ExtentRow = [label: string, value: string, tip: string];

const RAD = Math.PI / 180;

/** Decimals for a size: 120.0, 8.25, 0.412. */
const decimals = (mm: number) => (Math.abs(mm) >= 100 ? 1 : Math.abs(mm) >= 1 ? 2 : 3);
function size(mm: number, like = mm): string {
  return mm.toFixed(decimals(like));
}

/** "1,234 mm²", "56.2 mm²", "0.84 mm²". */
export function fmtArea(mm2: number): string {
  const a = Math.abs(mm2);
  if (a >= 1000) return `${Math.round(mm2).toLocaleString("en-US")} mm²`;
  return `${mm2.toFixed(a >= 10 ? 1 : 2)} mm²`;
}

/** A plane's size as the rectangle's two sides, longer first, both to the longer one's decimals: "120.0 × 80.0 mm". */
export function planeSize(e: PlaneExtent): string {
  const [a, b] = e.rectangle.half_sizes_mm;
  return `${size(2 * a)} × ${size(2 * b, 2 * a)} mm`;
}

/** The arc a cylinder's region covers: "all round", "182° of the round". */
export function arcText(e: CylinderExtent): string {
  return e.arc_deg >= 359.95 ? "all round" : `${e.arc_deg.toFixed(0)}° of the round`;
}

const MESH_NOTE = "Measured on the mesh's own vertices, so good to about a mesh edge.";

/** The card's lines for a fit's extent: a plane's size and area, a cylinder's length and arc; nothing for a hole
 * (its diameter says its size) or without an extent. */
export function extentRows(e: FitExtent | null | undefined): ExtentRow[] {
  if (!e) return [];
  if (e.kind === "plane") {
    const loops = e.n_outer + e.n_holes > 1 ? ` Its outline has ${e.n_outer === 1 ? "one outer loop" : `${e.n_outer} outer loops`}${e.n_holes ? ` and ${e.n_holes === 1 ? "one hole" : `${e.n_holes} holes`}` : ""}.` : "";
    return [
      ["Size", planeSize(e), `The smallest rectangle round the region's outline on the plane, its two sides. ${MESH_NOTE}`],
      ["Area", fmtArea(e.area_mm2), `The area of the region's outline on the plane, less its holes.${loops} ${MESH_NOTE}`],
    ];
  }
  if (e.kind === "cylinder")
    return [
      ["Length", `${size(e.length_mm)} mm`, `How far the region runs along the axis. ${MESH_NOTE}`],
      ["Arc", arcText(e), `How much of the round the region covers: all of it but the widest gap between its points. ${MESH_NOTE}`],
    ];
  return [];
}

const at = (o: ArrayLike<number>, a: ArrayLike<number>, x: number, b: ArrayLike<number>, y: number): Vec3 => [
  o[0] + a[0] * x + b[0] * y,
  o[1] + a[1] * x + b[1] * y,
  o[2] + a[2] * x + b[2] * y,
];

/** A plane's rectangle as a closed line (five corners, the first again at the end). */
export function rectangleOutline(e: PlaneExtent): Vec3[] {
  const { center: c, axes, half_sizes_mm: h } = e.rectangle;
  const [a, b] = axes;
  return [at(c, a, -h[0], b, -h[1]), at(c, a, h[0], b, -h[1]), at(c, a, h[0], b, h[1]), at(c, a, -h[0], b, h[1]), at(c, a, -h[0], b, -h[1])];
}

function unit(a: ArrayLike<number>): number[] {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
}

/**
 * A cylinder's marks from its extent: the axis over the region's span (with `margin` mm beyond each end) and the arc
 * the region covers, drawn on the cylinder's surface half way along. Angles from `zero`, turning right-handed about
 * the axis (as the engine measures them).
 */
export function cylinderMarks(s: CylinderShape, e: CylinderExtent, margin: number, steps = 72): { axis: [Vec3, Vec3]; arc: Vec3[] } {
  const d = unit(s.axis);
  const [lo, hi] = e.along_mm;
  const z = unit(e.zero);
  const side = [d[1] * z[2] - d[2] * z[1], d[2] * z[0] - d[0] * z[2], d[0] * z[1] - d[1] * z[0]];
  const mid = at(s.point, d, (lo + hi) / 2, d, 0);
  const n = Math.max(2, Math.ceil((steps * Math.min(360, Math.max(0, e.arc_deg))) / 360));
  const arc: Vec3[] = [];
  for (let k = 0; k <= n; k++) {
    const t = (e.start_deg + (e.arc_deg * k) / n) * RAD;
    arc.push(at(mid, z, s.radius_mm * Math.cos(t), side, s.radius_mm * Math.sin(t)));
  }
  return { axis: [at(s.point, d, lo - margin, d, 0), at(s.point, d, hi + margin, d, 0)], arc };
}
