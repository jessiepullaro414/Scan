/**
 * Sections as the viewer uses them (FEATURE_PLAN.md I1; APP_API.md "Sections"; the engine is ohs/mesh/section.py):
 * the cutting plane by the engine's own rule (so the view can show the plane while the cut is on its way, and the
 * demo engine cuts with the same axes), reading an answer into flat point lists, and the profile panel's geometry:
 * fitting the profile into the panel, zoom and pan, snapping a click to the profile's points, the distance between
 * two of them, the scale bar, the axes' names, and the numbers of the profile. Pure functions (section.test.ts).
 *
 * The drawing's coordinates are mm in the plane's own axes: x along `u` (right), y along `v` (up), seen from the side
 * the normal points to. A screen point has y down, so the panel turns y over, never mirrors x.
 */
import type { FitShape, PartFrameView, SectionAnswer, SectionLineCompact, SectionLineFull, SectionPlane, SectionPlaneDef, SectionStretch } from "../api/types.ts";

export type Vec3 = [number, number, number];

const UP_LIMIT = Math.SQRT1_2; // |n . Z| above this (the plane within 45° of horizontal): "up" is Y, not Z

const dot = (a: ArrayLike<number>, b: ArrayLike<number>) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: ArrayLike<number>, b: ArrayLike<number>): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a: ArrayLike<number>) => Math.hypot(a[0], a[1], a[2]);

/** Python's `{x:g}` for the numbers a description holds. */
const g = (x: number) => String(Number(x.toPrecision(6)));
const fmt = (p: ArrayLike<number>, digits = 3) => `(${[p[0], p[1], p[2]].map((x) => x.toFixed(digits)).join(", ")})`;

function vec(x: unknown, what: string): Vec3 {
  if (!Array.isArray(x) || x.length !== 3 || !x.every((c) => typeof c === "number" && Number.isFinite(c))) throw new Error(`the ${what} must be three finite numbers`);
  return [x[0], x[1], x[2]];
}

/** The part's frame as the engine uses it here: its rotation's rows (the part's X, Y, Z) and its origin, in the scan's frame. */
function frameAxes(frame: PartFrameView | null): { R: number[][]; O: Vec3 } {
  if (!frame) return { R: [[1, 0, 0], [0, 1, 0], [0, 0, 1]], O: [0, 0, 0] };
  const M = frame.matrix;
  const R = M.slice(0, 3).map((row) => row.slice(0, 3));
  const O = [0, 1, 2].map((j) => -(M[0][j] * M[0][3] + M[1][j] * M[1][3] + M[2][j] * M[2][3])) as Vec3;
  return { R, O };
}

/** The plane through `point` with `normal`, with its drawing axes by the engine's rule: "up" is Z turned into the plane
 * (Y when the plane lies within 45° of horizontal), u = v x n, and the 2D origin where the frame's origin falls on it;
 * the part's axes and origin with a frame, else the scan's. */
export function makePlane(point: ArrayLike<number>, normal: ArrayLike<number>, frame: PartFrameView | null, description: string): SectionPlane {
  const p = vec(Array.from(point), "point");
  const n0 = vec(Array.from(normal), "normal");
  const ln = norm(n0);
  if (ln === 0) throw new Error("the normal must not be zero");
  const n = n0.map((x) => x / ln) as Vec3;
  const { R, O } = frameAxes(frame);
  const up = Math.abs(dot(n, R[2])) <= UP_LIMIT ? R[2] : R[1];
  const k = dot(up, n);
  let v = [up[0] - k * n[0], up[1] - k * n[1], up[2] - k * n[2]];
  const lv = norm(v);
  v = v.map((x) => x / lv);
  let u: number[] = cross(v, n);
  const lu = norm(u);
  u = u.map((x) => x / lu);
  const d = dot([O[0] - p[0], O[1] - p[1], O[2] - p[2]], n);
  return { point: [O[0] - d * n[0], O[1] - d * n[1], O[2] - d * n[2]], normal: n, u, v, description, in_part_frame: !!frame };
}

export function planeFromPointNormal(point: ArrayLike<number>, normal: ArrayLike<number>, frame: PartFrameView | null): SectionPlane {
  const p = vec(Array.from(point), "point"),
    n = vec(Array.from(normal), "normal");
  return makePlane(p, n, frame, `Section through ${fmt(p)}\u202fmm, normal ${fmt(n, 4)}`);
}

/** A kept plane moved `offset` mm along its normal (the normal keeps the fit's sign). */
export function planeFromFit(shape: { point: ArrayLike<number>; normal: ArrayLike<number> }, offset: number, name: string | null, frame: PartFrameView | null): SectionPlane {
  if (!Number.isFinite(offset)) throw new Error("the offset must be a finite number of mm");
  const n0 = vec(Array.from(shape.normal), "plane's normal");
  const ln = norm(n0);
  if (!ln) throw new Error("the plane's normal must not be zero");
  const n = n0.map((x) => x / ln);
  const p0 = vec(Array.from(shape.point), "plane's point");
  const p = p0.map((x, i) => x + offset * n[i]);
  return makePlane(p, n, frame, `Section on ${name || "the fitted plane"}${offset ? `, moved ${offset > 0 ? "+" : ""}${g(offset)}\u202fmm along its normal` : ""}`);
}

/** The plane `axis = value` of the part's frame, seen from the axis's + side ("-Y": from the other side). */
export function planeFromFrame(frame: PartFrameView, axis: string, value: number): SectionPlane {
  let a = String(axis).trim().toUpperCase();
  const sign = a.startsWith("-") ? -1 : 1;
  a = a.replace(/^[+-]+/, "");
  const i = ["X", "Y", "Z"].indexOf(a);
  if (i < 0 || !a) throw new Error(`axis must be X, Y or Z (with an optional sign), got '${axis}'`);
  if (!Number.isFinite(value)) throw new Error("the value must be a finite number of mm");
  const { R, O } = frameAxes(frame);
  const e = R[i];
  const p = [O[0] + value * e[0], O[1] + value * e[1], O[2] + value * e[2]];
  return makePlane(p, e.map((x) => sign * x), frame, `Section at ${a} = ${g(value)}\u202fmm in the part's axes${sign < 0 ? `, seen from -${a}` : ""}`);
}

/** The plane through three points; its normal by the right-hand rule, (p2 − p1) x (p3 − p1). */
export function planeFromPoints(p1: ArrayLike<number>, p2: ArrayLike<number>, p3: ArrayLike<number>, frame: PartFrameView | null): SectionPlane {
  const a = vec(Array.from(p1), "first point"),
    b = vec(Array.from(p2), "second point"),
    c = vec(Array.from(p3), "third point");
  const ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]],
    ac = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
  const n = cross(ab, ac);
  const size = norm(ab) * norm(ac);
  if (size === 0 || norm(n) <= 1e-9 * size) throw new Error("the three points lie on one line (or two are the same), so they do not make a plane");
  const centre = [0, 1, 2].map((k) => (a[k] + b[k] + c[k]) / 3);
  return makePlane(centre, n, frame, `Section through three points ${fmt(a)}, ${fmt(b)}, ${fmt(c)}\u202fmm`);
}

/** A kept fit as the plane functions need it. */
export type PlaneFit = { id: string; name: string; kind: string; shape: FitShape | { kind: string; point?: number[]; normal?: number[] } };

/** The plane a definition names (as the engine builds it); throws an Error with the engine's words. */
export function planeFromDef(def: SectionPlaneDef, fits: PlaneFit[], frame: PartFrameView | null): SectionPlane {
  if (def.kind === "fit") {
    const f = fits.find((x) => x.id === def.fit);
    if (!f) throw new Error(`No fitted shape '${def.fit}'`);
    const s = f.shape as { kind: string; point?: number[]; normal?: number[] };
    if (s.kind !== "plane" || !s.point || !s.normal) throw new Error(`a section is cut by a plane, and "${f.name}" is a ${f.kind}`);
    return planeFromFit({ point: s.point, normal: s.normal }, def.offset_mm ?? 0, f.name, frame);
  }
  if (def.kind === "frame") {
    if (!frame) throw new Error("this scan has no part's axes to cut along");
    return planeFromFrame(frame, def.axis, def.value_mm);
  }
  if (def.kind === "point_normal") return planeFromPointNormal(def.point, def.normal, frame);
  return planeFromPoints(def.points[0], def.points[1], def.points[2], frame);
}

/** A drawing point (x, y) in the scan's frame. */
export function to3d(plane: SectionPlane, x: number, y: number): Vec3 {
  const { point: p, u, v } = plane;
  return [p[0] + x * u[0] + y * v[0], p[1] + x * u[1] + y * v[1], p[2] + x * u[2] + y * v[2]];
}

// ---------------------------------------------------------------- reading the answer
/** A line of the profile: its points one after the other ([x0, y0, x1, y1, ...], mm). */
export type ProfileLine = { closed: boolean; pts: Float64Array; n: number; stretches: SectionStretch[]; area: number | null; length: number };

/** The answer's lines as flat point lists (compact or full). */
export function profileOf(a: Pick<SectionAnswer<SectionLineCompact | SectionLineFull>, "lines">): ProfileLine[] {
  return a.lines.map((ln) => {
    const raw = ln.points as number[] | number[][];
    let pts: Float64Array;
    if (raw.length && Array.isArray(raw[0])) {
      const P = raw as number[][];
      pts = new Float64Array(P.length * 2);
      for (let i = 0; i < P.length; i++) {
        pts[2 * i] = P[i][0];
        pts[2 * i + 1] = P[i][1];
      }
    } else pts = Float64Array.from(raw as number[]);
    return { closed: ln.closed, pts, n: pts.length / 2, stretches: ln.stretches, area: ln.area_mm2, length: ln.length_mm };
  });
}

// ---------------------------------------------------------------- the panel's view: fit, zoom, pan
/** Where the panel looks: the drawing point (cx, cy) at the panel's centre, `scale` px per mm. */
export type View = { cx: number; cy: number; scale: number };

const DEFAULT_SCALE = 10; // px per mm when there is nothing to fit

/** The view that shows the whole bounding box ([xmin, ymin, xmax, ymax], mm) in a w x h panel with `margin` px round
 * it, centred: the larger of the two ratios limits; a straight line fits its length; a point or nothing gets 10 px/mm. */
export function fitView(bbox: number[] | null, w: number, h: number, margin = 24): View {
  if (!bbox) return { cx: 0, cy: 0, scale: DEFAULT_SCALE };
  const [x0, y0, x1, y1] = bbox;
  const dx = x1 - x0,
    dy = y1 - y0;
  const aw = Math.max(1, w - 2 * margin),
    ah = Math.max(1, h - 2 * margin);
  const ratios = [dx > 0 ? aw / dx : Infinity, dy > 0 ? ah / dy : Infinity];
  const scale = Math.min(...ratios);
  return { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, scale: Number.isFinite(scale) ? scale : DEFAULT_SCALE };
}

/** A drawing point on the screen (px from the panel's top left; y down). */
export function toScreen(v: View, w: number, h: number, x: number, y: number): [number, number] {
  return [w / 2 + (x - v.cx) * v.scale, h / 2 - (y - v.cy) * v.scale];
}

/** A screen point in the drawing (mm). */
export function toDrawing(v: View, w: number, h: number, sx: number, sy: number): [number, number] {
  return [v.cx + (sx - w / 2) / v.scale, v.cy - (sy - h / 2) / v.scale];
}

/** Zoom by `factor` about the screen point (sx, sy): the drawing point under it stays there. */
export function zoomAbout(v: View, w: number, h: number, sx: number, sy: number, factor: number): View {
  const [x, y] = toDrawing(v, w, h, sx, sy);
  const scale = Math.min(1e6, Math.max(1e-4, v.scale * factor));
  return { scale, cx: x - (sx - w / 2) / scale, cy: y + (sy - h / 2) / scale };
}

/** Pan: the drawing follows the mouse by (dx, dy) px. */
export function panBy(v: View, dx: number, dy: number): View {
  return { scale: v.scale, cx: v.cx - dx / v.scale, cy: v.cy + dy / v.scale };
}

// ---------------------------------------------------------------- measuring on the profile
export type Snap = { x: number; y: number; line: number; index: number; distPx: number };

/** The profile's point nearest the screen point (sx, sy), when it is within `radius` px; else null. */
export function snapTo(lines: ProfileLine[], v: View, w: number, h: number, sx: number, sy: number, radius: number): Snap | null {
  const [x, y] = toDrawing(v, w, h, sx, sy);
  const r = radius / v.scale;
  let best: Snap | null = null;
  let bd = r * r;
  for (let li = 0; li < lines.length; li++) {
    const P = lines[li].pts;
    for (let i = 0; i < P.length; i += 2) {
      const ddx = P[i] - x,
        ddy = P[i + 1] - y;
      const d = ddx * ddx + ddy * ddy;
      if (d <= bd) {
        bd = d;
        best = { x: P[i], y: P[i + 1], line: li, index: i / 2, distPx: Math.sqrt(d) * v.scale };
      }
    }
  }
  return best;
}

/** The distance between two drawing points, with its two parts along the drawing's axes (b − a). */
export function distance2d(a: { x: number; y: number }, b: { x: number; y: number }): { d: number; dx: number; dy: number } {
  const dx = b.x - a.x,
    dy = b.y - a.y;
  return { d: Math.hypot(dx, dy), dx, dy };
}

/** The scale bar: a round length (1, 2 or 5 times a power of ten) of at most a quarter of the panel's width. */
export function scaleBar(v: View, widthPx: number): { mm: number; px: number; label: string } {
  const target = widthPx / v.scale / 4;
  const p = 10 ** Math.floor(Math.log10(target));
  let mm = p;
  for (const m of [1, 2, 5]) if (m * p <= target * (1 + 1e-12)) mm = Number((m * p).toPrecision(12));
  return { mm, px: mm * v.scale, label: `${g(mm)}\u202fmm` };
}

/** The names of the drawing's axes: the frame's axis a direction lies along ("X", or "−X" the other way; the part's in
 * capitals, the scan's in small letters), else "u" and "v". */
export function axisNames(plane: Pick<SectionPlane, "u" | "v">, frame: PartFrameView | null): { u: string; v: string } {
  const { R } = frameAxes(frame);
  const names = frame ? ["X", "Y", "Z"] : ["x", "y", "z"];
  const name = (d: ArrayLike<number>, fallback: string) => {
    for (let k = 0; k < 3; k++) {
      const c = dot(d, R[k]);
      if (Math.abs(c) > 0.9999) return `${c < 0 ? "−" : ""}${names[k]}`;
    }
    return fallback;
  };
  return { u: name(plane.u, "u"), v: name(plane.v, "v") };
}

/** The numbers of a profile: lines, closed loops, the total length (all the cut's), and the closed loops' areas
 * (largest first; + an outline, − a hole) and their sum, of the lines in the answer. */
export function profileNumbers(a: Pick<SectionAnswer<SectionLineCompact | SectionLineFull>, "lines" | "n_lines" | "n_closed" | "length_mm">): {
  lines: number;
  closed: number;
  length: number;
  loops: { area: number; kind: "outline" | "hole"; line: number }[];
  net: number;
} {
  const loops = a.lines
    .map((ln, line) => ({ area: ln.area_mm2, line }))
    .filter((x): x is { area: number; line: number } => x.area !== null)
    .map((x) => ({ ...x, kind: (x.area >= 0 ? "outline" : "hole") as "outline" | "hole" }))
    .sort((p, q) => Math.abs(q.area) - Math.abs(p.area));
  return { lines: a.n_lines, closed: a.n_closed, length: a.length_mm, loops, net: loops.reduce((s, x) => s + x.area, 0) };
}
