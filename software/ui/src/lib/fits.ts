/**
 * Fitted shapes (APP_API.md "M5 first slice: fitted shapes"), the pure part: the region's face list
 * (base64 of little-endian uint32), the region's triangles in the mesh on screen, a summary of the
 * region, the labels, and the relations between two fits (angles and distances).
 *
 * The ± values of a fit are 1σ, statistical only. A relation gets a ± only where the parts' ±
 * values give one soundly: the distance between two parallel planes (the root-sum-square of their
 * offset ±, which are along the normal; it ignores their tilt). The ± of a direction or of a
 * position across one is the RMS of a two-component error (the engine's `fit.py`), so its share
 * along any one angle or distance is not known: those relations have no ±.
 *
 * Imports nothing at run time, so `npm test` checks it with plain node.
 */
import type { CircleShape, CylinderShape, FitKind, FitShape, PlaneShape } from "../api/types";

type V = ArrayLike<number>;

const DEG = 180 / Math.PI;
const dot = (a: V, b: V) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const sub = (a: V, b: V): number[] => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const cross = (a: V, b: V): number[] => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = (a: V) => Math.hypot(a[0], a[1], a[2]);
function unit(a: V): number[] {
  const l = len(a) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
}

/** Directions closer than this count as parallel (planes, axes, an axis and a plane). */
export const PARALLEL_DEG = 2;

// ------------------------------------------------------------------ the region's faces
/** The API's face list (base64 of little-endian uint32) as numbers. Throws on bad input. */
export function decodeFaces(b64: string): Uint32Array {
  let bin: string;
  try {
    bin = atob(b64);
  } catch {
    throw new Error("the region's face list isn't valid base64");
  }
  if (bin.length % 4) throw new Error("the region's face list isn't a whole number of 32-bit integers");
  const out = new Uint32Array(bin.length / 4);
  for (let i = 0, o = 0; i < out.length; i++, o += 4)
    out[i] = (bin.charCodeAt(o) | (bin.charCodeAt(o + 1) << 8) | (bin.charCodeAt(o + 2) << 16) | (bin.charCodeAt(o + 3) << 24)) >>> 0;
  return out;
}

/** Face indices as the API sends them (the mock engine and the tests). */
export function encodeFaces(ids: ArrayLike<number>): string {
  const bytes = new Uint8Array(ids.length * 4);
  const dv = new DataView(bytes.buffer);
  for (let i = 0; i < ids.length; i++) dv.setUint32(i * 4, ids[i], true);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

/**
 * The corners of the region's triangles (an index buffer for the highlight). `index` is the mesh's
 * triangle index in the file's face order; `faceOfTri` maps each triangle to its face in the file
 * (null when every face is one triangle; ascending, as the PLY reader makes it). Throws when a face
 * is not in the mesh: the region then belongs to another version of it.
 */
export function regionTriangles(faces: ArrayLike<number>, index: ArrayLike<number>, faceOfTri: ArrayLike<number> | null, faceCount?: number): Uint32Array {
  const T = Math.floor(index.length / 3);
  const F = faceOfTri ? (faceCount ?? (T ? faceOfTri[T - 1] + 1 : 0)) : T;
  for (let i = 0; i < faces.length; i++)
    if (!(faces[i] < F)) throw new Error(`face ${faces[i]} is not in this mesh (it has ${F} faces)`);
  if (!faceOfTri) {
    const out = new Uint32Array(faces.length * 3);
    for (let i = 0, o = 0; i < faces.length; i++, o += 3) {
      const t = faces[i] * 3;
      out[o] = index[t];
      out[o + 1] = index[t + 1];
      out[o + 2] = index[t + 2];
    }
    return out;
  }
  // a face's triangles are one run of faceOfTri: find its start by bisection
  const first = new Int32Array(faces.length);
  let n = 0;
  for (let i = 0; i < faces.length; i++) {
    const f = faces[i];
    let lo = 0,
      hi = T;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (faceOfTri[mid] < f) lo = mid + 1;
      else hi = mid;
    }
    first[i] = lo;
    for (let t = lo; t < T && faceOfTri[t] === f; t++) n++;
  }
  const out = new Uint32Array(n * 3);
  let o = 0;
  for (let i = 0; i < faces.length; i++)
    for (let t = first[i]; t < T && faceOfTri[t] === faces[i]; t++) {
      out[o++] = index[t * 3];
      out[o++] = index[t * 3 + 1];
      out[o++] = index[t * 3 + 2];
    }
  return out;
}

// ------------------------------------------------------------------ the region
export type RegionSummary = {
  /** Distinct vertices of the region's triangles. */
  vertices: number;
  /** Axis shapes: the region's extent along the axis from the shape's point (mm); null for the others. */
  along: [number, number] | null;
  /** RMS distance of the vertices from the shape's point or centre, across its direction (mm). */
  spread: number;
  /** Where the label goes: the click on a plane (onto it), the middle of a cylinder's axis, a circle's centre. */
  anchor: [number, number, number];
};

/** A shape's direction (plane normal, cylinder axis, a hole's axis = its circle's normal) and its position. */
export function directionOf(s: FitShape): number[] {
  return s.kind === "cylinder" ? s.axis : s.normal;
}
export function positionOf(s: FitShape): number[] {
  return s.kind === "circle" ? s.center : s.point;
}

/**
 * Summary of a region (once per fit): its vertices, the extent along an axis and where its label goes
 * (`click`: the clicked point; a plane's label goes there, a large plane's centre may lie anywhere).
 */
export function summarizeRegion(tris: ArrayLike<number>, positions: ArrayLike<number>, shape: FitShape, click?: ArrayLike<number>): RegionSummary {
  const V = Math.floor(positions.length / 3);
  const seen = new Uint8Array(V);
  const p = positionOf(shape);
  const d = unit(directionOf(shape));
  let n = 0,
    sum2 = 0,
    lo = Infinity,
    hi = -Infinity;
  for (let i = 0; i < tris.length; i++) {
    const v = tris[i];
    if (v >= V || seen[v]) continue;
    seen[v] = 1;
    n++;
    const x = positions[v * 3] - p[0],
      y = positions[v * 3 + 1] - p[1],
      z = positions[v * 3 + 2] - p[2];
    const t = x * d[0] + y * d[1] + z * d[2];
    if (t < lo) lo = t;
    if (t > hi) hi = t;
    sum2 += x * x + y * y + z * z - t * t;
  }
  const along: [number, number] | null = shape.kind === "cylinder" && n ? [lo, hi] : null;
  const mid = along ? (along[0] + along[1]) / 2 : 0;
  let anchor: [number, number, number] = [p[0] + d[0] * mid, p[1] + d[1] * mid, p[2] + d[2] * mid];
  if (shape.kind === "plane" && click) {
    const off = dot(sub(click, p), d);
    anchor = [click[0] - d[0] * off, click[1] - d[1] * off, click[2] - d[2] * off];
  }
  return {
    vertices: n,
    along,
    spread: n ? Math.sqrt(Math.max(0, sum2) / n) : 0,
    anchor,
  };
}

// ------------------------------------------------------------------ words
/** Decimals for a length with this uncertainty (or this size): 1.2, 0.04, 0.004, 0.0004. */
function decimals(x: number): number {
  const a = Math.abs(x);
  return a >= 1 ? 1 : a >= 0.01 ? 2 : a >= 0.001 ? 3 : 4;
}
/** A small 1σ with two significant digits, never "0.000": "0.035", "0.0042", "0.00013". */
export function fmtSmall(x: number): string {
  const a = Math.abs(x);
  if (a >= 0.1 || a === 0 || !Number.isFinite(a)) return x.toFixed(2);
  return x.toFixed(Math.min(8, Math.max(3, Math.ceil(-Math.log10(a)) + 1)));
}
/** A length in mm: "0.040 mm", "14.02 mm" (three decimals below 0.1 mm). */
export function fmtLength(x: number): string {
  return `${x.toFixed(Math.abs(x) < 0.1 ? 3 : 2)} mm`;
}
/** A length and its 1σ, both to the σ's decimals: "14.02 ± 0.03 mm". */
export function fmtPm(x: number, sigma: number): string {
  const d = decimals(sigma);
  return `${x.toFixed(d)} ± ${sigma.toFixed(d)} mm`;
}
export function fmtAngle(deg: number): string {
  return `${deg.toFixed(2)}°`;
}

/** "Plane", "Cylinder", "Hole" (a hole's shape is a circle, or a cylinder for a scanned bore wall). */
export function fitName(kind: FitKind): string {
  return kind === "plane" ? "Plane" : kind === "cylinder" ? "Cylinder" : "Hole";
}

/** The fit's main number: "flat within 0.04 mm", "Ø 14.02 ± 0.03 mm". */
export function fitValue(shape: FitShape): string {
  if (shape.kind === "plane") {
    const f = shape.flatness_mm;
    return `flat within ${f.toFixed(f < 0.01 ? 3 : f < 10 ? 2 : 1)} mm`;
  }
  return `Ø ${fmtPm(shape.diameter_mm, shape.diameter_sigma_mm)}`;
}

/** The label on the mesh: "Plane · flat within 0.04 mm", "Cylinder Ø 14.02 ± 0.03 mm", "Hole Ø 25.01 ± 0.05 mm". */
export function fitLabel(kind: FitKind, shape: FitShape): string {
  return shape.kind === "plane" ? `${fitName(kind)} · ${fitValue(shape)}` : `${fitName(kind)} ${fitValue(shape)}`;
}

// ------------------------------------------------------------------ geometry
/** Angle between two lines or normals (no sign), 0..90°; exact near 0 too. */
export function lineAngleDeg(u: V, v: V): number {
  const a = Math.atan2(len(cross(u, v)), Math.abs(dot(u, v))) * DEG;
  return Math.min(90, Math.max(0, a));
}

/** The mean of two (nearly) parallel directions, the second flipped to agree with the first. */
function meanDirection(u: V, v: V): number[] {
  const a = unit(u);
  const b = unit(v);
  const s = dot(a, b) < 0 ? -1 : 1;
  return unit([a[0] + s * b[0], a[1] + s * b[1], a[2] + s * b[2]]);
}

/** Distance between two (nearly) parallel planes: between their points, along their mean normal. */
export function planePlaneDistance(p1: V, n1: V, p2: V, n2: V): number {
  return Math.abs(dot(meanDirection(n1, n2), sub(p2, p1)));
}

/** Distance between two (nearly) parallel lines: their points' offset across their mean direction. */
export function parallelLineDistance(p1: V, a1: V, p2: V, a2: V): number {
  const a = meanDirection(a1, a2);
  const w = sub(p2, p1);
  const t = dot(w, a);
  return len([w[0] - t * a[0], w[1] - t * a[1], w[2] - t * a[2]]);
}

/** Shortest distance between two lines (along their common perpendicular); parallel lines as above. */
export function lineLineDistance(p1: V, a1: V, p2: V, a2: V): number {
  const c = cross(unit(a1), unit(a2));
  const s = len(c);
  if (s < 1e-9) return parallelLineDistance(p1, a1, p2, a2);
  return Math.abs(dot(sub(p2, p1), c)) / s;
}

/** Distance of a point from a plane. */
export function pointPlaneDistance(q: V, p: V, n: V): number {
  return Math.abs(dot(unit(n), sub(q, p)));
}

/** Root-sum-square of independent 1σ values. */
export function rss(...s: number[]): number {
  return Math.sqrt(s.reduce((t, x) => t + x * x, 0));
}

// ------------------------------------------------------------------ relations
export type RelationRow = {
  label: string;
  value: string;
  /** What it is and why it has (or has no) ±. */
  tip: string;
  /** The number itself (mm or degrees) and its 1σ when there is a sound one. */
  x: number;
  sigma: number | null;
};
export type Relation = { rows: RelationRow[]; notes: string[] };
export type FitPart = { kind: FitKind; shape: FitShape };

const NO_SIGMA =
  "No ±: the fits' ± for a direction or for a position across one is the total over two directions, so its share in this number isn't known.";

function angleRow(label: string, deg: number, what: string): RelationRow {
  return { label, value: fmtAngle(deg), tip: `${what} ${NO_SIGMA}`, x: deg, sigma: null };
}
function lengthRow(label: string, mm: number, what: string): RelationRow {
  return { label, value: fmtLength(mm), tip: `${what} ${NO_SIGMA}`, x: mm, sigma: null };
}

function planes(a: PlaneShape, b: PlaneShape): Relation {
  const deg = lineAngleDeg(a.normal, b.normal);
  const parallel = deg <= PARALLEL_DEG;
  const rows = [angleRow(parallel ? "Angle (parallel)" : "Angle", deg, "Between the two planes: 0° parallel, 90° square.")];
  const notes: string[] = [];
  if (parallel) {
    const d = planePlaneDistance(a.point, a.normal, b.point, b.normal);
    const s = rss(a.offset_sigma_mm, b.offset_sigma_mm);
    rows.push({
      label: "Distance",
      value: fmtPm(d, s),
      tip:
        "Between the two planes along their mean normal, at the centres of the regions. ± is the root-sum-square of the two planes' offset ± " +
        "(1σ, statistical only); it ignores their tilt.",
      x: d,
      sigma: s,
    });
  } else notes.push(`Not parallel (more than ${PARALLEL_DEG}° apart): there is no one distance between them.`);
  return { rows, notes };
}

function axes(a: FitPart & { shape: CylinderShape | CircleShape }, b: FitPart & { shape: CylinderShape | CircleShape }): Relation {
  const da = directionOf(a.shape),
    db = directionOf(b.shape);
  const pa = positionOf(a.shape),
    pb = positionOf(b.shape);
  const deg = lineAngleDeg(da, db);
  const parallel = deg <= PARALLEL_DEG;
  const rows = [angleRow(parallel ? "Angle of the axes (parallel)" : "Angle of the axes", deg, "Between the two axes: 0° parallel, 90° square.")];
  if (parallel) rows.push(lengthRow("Distance between the axes", parallelLineDistance(pa, da, pb, db), "Between the two parallel axes, square to them."));
  else rows.push(lengthRow("Closest distance of the axes", lineLineDistance(pa, da, pb, db), "Where the two axes pass closest: along the line square to both (0 where they cross)."));
  if (a.shape.kind === "circle" && b.shape.kind === "circle") rows.push(lengthRow("Centre to centre", len(sub(pb, pa)), "Straight from one hole's centre to the other's."));
  return { rows, notes: [] };
}

function planeAxis(plane: PlaneShape, other: FitPart & { shape: CylinderShape | CircleShape }): Relation {
  const dir = directionOf(other.shape);
  const deg = 90 - lineAngleDeg(plane.normal, dir);
  const what = other.kind === "hole" ? "the hole's axis" : "the axis";
  const rows = [angleRow(`Angle of ${what} to the plane`, deg, `Between ${what} and the plane: 90° square to it, 0° along it.`)];
  const notes: string[] = [];
  if (other.shape.kind === "circle") rows.push(lengthRow("Hole centre to plane", pointPlaneDistance(other.shape.center, plane.point, plane.normal), "From the hole's centre to the plane, square to the plane."));
  else if (deg <= PARALLEL_DEG) rows.push(lengthRow("Axis to plane", pointPlaneDistance(other.shape.point, plane.point, plane.normal), "From the axis to the plane it runs along, square to the plane."));
  else notes.push(`The axis is not parallel to the plane (more than ${PARALLEL_DEG}°): there is no one distance between them.`);
  return { rows, notes };
}

/** What applies between two fits: angles, and the distances that have one meaning. */
export function relate(a: FitPart, b: FitPart): Relation {
  if (a.shape.kind === "plane" && b.shape.kind === "plane") return planes(a.shape, b.shape);
  if (a.shape.kind === "plane") return planeAxis(a.shape, b as FitPart & { shape: CylinderShape | CircleShape });
  if (b.shape.kind === "plane") return planeAxis(b.shape, a as FitPart & { shape: CylinderShape | CircleShape });
  return axes(a as FitPart & { shape: CylinderShape | CircleShape }, b as FitPart & { shape: CylinderShape | CircleShape });
}
