/**
 * The demo engine's extent of a fit (APP_API.md "Kept features", `extent`), a simple stand-in for
 * `ohs.measure.region_extent`, so that the viewer's cards and outlines work with `?mock=1`:
 *
 * - plane: the region's vertices on the plane, in a basis `origin`, `u`, `v`; the rectangle round them along `u` and
 *   `v` (not the smallest one, as the engine's); the area of the region's triangles; the outline is that rectangle
 *   (one loop, no holes);
 * - cylinder: the region's vertices along the axis from the shape's point, and the arc they cover (all of the round
 *   but the widest gap between their angles; a gap under 30° counts as all round);
 * - hole: its circle as 72 points.
 *
 * And a cylinder's form (`mockForm`): a boss or a bore, read from its region's faces as the engine reads it.
 *
 * Imports nothing at run time, so `npm test` checks it with plain node (extent.test.ts).
 */
import type { CylinderForm, FeatureOutline, FitExtent, FitShape } from "../types";

type V = number[];
const RAD = Math.PI / 180;
const ROUND_GAP_DEG = 30; // a widest gap under this counts as all round

function unit(a: V): V {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
}
const cross = (a: V, b: V): V => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a: V, b: V) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
/** Two unit vectors square to `d` and to each other, (u, v, d) right-handed. */
function across(d: V): [V, V] {
  const n = unit(d);
  const u = unit(cross(n, Math.abs(n[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0]));
  return [u, cross(n, u)];
}

/** The outline route's body for a kept fit (without its id and mesh version). */
export type MockOutline = { kind: "plane"; origin: V; u: V; v: V; outer: number[][][]; holes: number[][][] } | null;

/** The extent of a fit's region (`faces`: triangle ids of `indices`) and its outline (`null` for a cylinder or hole). */
export function mockExtent(positions: ArrayLike<number>, indices: ArrayLike<number>, faces: ArrayLike<number>, shape: FitShape): { extent: FitExtent; outline: MockOutline } {
  const seen = new Set<number>();
  for (let i = 0; i < faces.length; i++) for (let k = 0; k < 3; k++) seen.add(indices[faces[i] * 3 + k]);
  const P = (v: number): V => [positions[v * 3], positions[v * 3 + 1], positions[v * 3 + 2]];
  if (shape.kind === "plane") {
    const o = shape.point;
    const [u, v] = across(shape.normal);
    let x0 = Infinity,
      x1 = -Infinity,
      y0 = Infinity,
      y1 = -Infinity;
    for (const i of seen) {
      const q = P(i);
      const w = [q[0] - o[0], q[1] - o[1], q[2] - o[2]];
      const x = dot(w, u),
        y = dot(w, v);
      x0 = Math.min(x0, x);
      x1 = Math.max(x1, x);
      y0 = Math.min(y0, y);
      y1 = Math.max(y1, y);
    }
    if (!seen.size) x0 = x1 = y0 = y1 = 0;
    let area = 0;
    for (let i = 0; i < faces.length; i++) {
      const [a, b, c] = [0, 1, 2].map((k) => P(indices[faces[i] * 3 + k]));
      area += 0.5 * Math.hypot(...cross([b[0] - a[0], b[1] - a[1], b[2] - a[2]], [c[0] - a[0], c[1] - a[1], c[2] - a[2]]));
    }
    const long = x1 - x0 >= y1 - y0;
    const [ax, bx] = long ? [u, v] : [v, u];
    const half: [number, number] = long ? [(x1 - x0) / 2, (y1 - y0) / 2] : [(y1 - y0) / 2, (x1 - x0) / 2];
    const cx = (x0 + x1) / 2,
      cy = (y0 + y1) / 2;
    const center = [o[0] + u[0] * cx + v[0] * cy, o[1] + u[1] * cx + v[1] * cy, o[2] + u[2] * cx + v[2] * cy];
    const extent: FitExtent = {
      kind: "plane",
      origin: [...o],
      u,
      v,
      rectangle: { center, axes: [ax, bx], half_sizes_mm: half },
      area_mm2: area,
      n_outer: 1,
      n_holes: 0,
      n_points: 4,
    };
    return { extent, outline: { kind: "plane", origin: [...o], u, v, outer: [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]], holes: [] } };
  }
  if (shape.kind === "cylinder") {
    const d = unit(shape.axis);
    const [zero, side] = across(d);
    let lo = Infinity,
      hi = -Infinity;
    const ang: number[] = [];
    for (const i of seen) {
      const q = P(i);
      const w = [q[0] - shape.point[0], q[1] - shape.point[1], q[2] - shape.point[2]];
      const t = dot(w, d);
      lo = Math.min(lo, t);
      hi = Math.max(hi, t);
      ang.push(((Math.atan2(dot(w, side), dot(w, zero)) / RAD) % 360 + 360) % 360);
    }
    if (!seen.size) lo = hi = 0;
    ang.sort((a, b) => a - b);
    let gap = 360,
      after = 0;
    for (let k = 0; k < ang.length; k++) {
      const g = (k + 1 < ang.length ? ang[k + 1] : ang[0] + 360) - ang[k];
      if (k === 0 || g > gap) {
        gap = g;
        after = k + 1 < ang.length ? ang[k + 1] : ang[0];
      }
    }
    const round = ang.length < 2 || gap < ROUND_GAP_DEG;
    return {
      extent: { kind: "cylinder", along_mm: [lo, hi], length_mm: hi - lo, zero, start_deg: round ? 0 : after, arc_deg: round ? 360 : 360 - gap },
      outline: null,
    };
  }
  const [u, v] = across(shape.normal);
  const edge: number[][] = [];
  for (let k = 0; k < 72; k++) {
    const t = (k / 72) * 2 * Math.PI;
    const c = Math.cos(t) * shape.radius_mm,
      s = Math.sin(t) * shape.radius_mm;
    edge.push([shape.center[0] + u[0] * c + v[0] * s, shape.center[1] + u[1] * c + v[1] * s, shape.center[2] + u[2] * c + v[2] * s]);
  }
  return { extent: { kind: "hole", n_edge: edge.length, edge }, outline: null };
}

/** A share of a cylinder's region area this large facing one way decides its form (as the engine's FORM_MAJORITY). */
const FORM_MAJORITY = 0.8;

/**
 * Whether a cylinder fit is a boss or a bore, as `ohs.measure.region.cylinder_form` reads it: each face of the region
 * votes with its area, its normal (from the order of its corners) against the direction from the axis to its centre;
 * away from the axis is a boss, towards it a bore. null when no clear majority, and for a plane or a hole.
 */
export function mockForm(positions: ArrayLike<number>, indices: ArrayLike<number>, faces: ArrayLike<number>, shape: FitShape): CylinderForm | null {
  if (shape.kind !== "cylinder") return null;
  const d = unit(shape.axis);
  let away = 0,
    towards = 0;
  for (let i = 0; i < faces.length; i++) {
    const [a, b, c] = [0, 1, 2].map((k) => {
      const v = indices[faces[i] * 3 + k];
      return [positions[v * 3], positions[v * 3 + 1], positions[v * 3 + 2]];
    });
    const n = cross([b[0] - a[0], b[1] - a[1], b[2] - a[2]], [c[0] - a[0], c[1] - a[1], c[2] - a[2]]);
    const w = [(a[0] + b[0] + c[0]) / 3 - shape.point[0], (a[1] + b[1] + c[1]) / 3 - shape.point[1], (a[2] + b[2] + c[2]) / 3 - shape.point[2]];
    const t = dot(w, d);
    const along = dot(n, [w[0] - t * d[0], w[1] - t * d[1], w[2] - t * d[2]]);
    const area = Math.hypot(n[0], n[1], n[2]);
    if (along > 0) away += area;
    else if (along < 0) towards += area;
  }
  if (!(away + towards > 0)) return null;
  const share = away / (away + towards);
  return share >= FORM_MAJORITY ? "boss" : 1 - share >= FORM_MAJORITY ? "bore" : null;
}

/** The outline route's answer from a kept mock outline. */
export function outlineAnswer(id: string, meshVersion: number, o: NonNullable<MockOutline>): FeatureOutline {
  return { id, mesh_version: meshVersion, ...o };
}
