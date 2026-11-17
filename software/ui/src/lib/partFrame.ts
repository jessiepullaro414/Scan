/**
 * The part's coordinate system as the viewer uses it (APP_API.md "The part's frame"; the engine is
 * ohs/measure/frame.py): reading the kept record, turning the numbers the panels show into it, the
 * axes to draw, and the body that builds it again. Pure functions (partFrame.test.ts checks them).
 *
 * The frame's matrix takes a point of the scan's frame to the part's: `x_part = R x_scan + t`; R's
 * rows are the part's X, Y and Z axes in the scan's frame. Positions (a plane's point, a hole's
 * centre, an axis point, a measurement's ends) move with R and t; directions (normals, axes, a
 * distance's Δx / Δy / Δz) only turn with R. Sizes, distances, angles and ± values are the same in
 * every frame, so nothing here touches them. The 3D view stays in the scan's frame.
 */
import type { AxisName, FitShape, FrameRole, NewFrame, PartFrameView } from "../api/types.ts";

export type Vec3 = [number, number, number];

const isNum = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const AXES: AxisName[] = ["X", "Y", "Z"];
export const ROLES: FrameRole[] = ["primary", "secondary", "origin"];

/**
 * The kept frame when this version can read it (a `PartFrameView`: format 1, a 4x4 rotation and translation,
 * the axes named X, Y and Z once each), else null: none kept, or one this version cannot read (`frameProblem`
 * says why).
 */
export function readFrame(raw: unknown): PartFrameView | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const f = raw as Record<string, unknown>;
  if (f.readable === false || f.format_version !== 1) return null;
  const M = f.matrix;
  if (!Array.isArray(M) || M.length !== 4 || !M.every((row) => Array.isArray(row) && row.length === 4 && row.every(isNum))) return null;
  const R = (M as number[][]).slice(0, 3).map((row) => row.slice(0, 3));
  for (let i = 0; i < 3; i++)
    for (let j = 0; j < 3; j++) {
      const d = R[i][0] * R[j][0] + R[i][1] * R[j][1] + R[i][2] * R[j][2];
      if (Math.abs(d - (i === j ? 1 : 0)) > 1e-6) return null;
    }
  const a = f.axes as Record<string, unknown> | undefined;
  if (!a || typeof a !== "object") return null;
  const names = [a.primary, a.secondary, a.third];
  if ([...names].sort().join() !== "X,Y,Z") return null;
  if (!Array.isArray(f.built_from) || !f.sigma || typeof f.sigma !== "object") return null;
  return raw as PartFrameView;
}

/** Why a kept frame can't be used (null when it can, or when there is none). */
export function frameProblem(raw: unknown): string | null {
  if (!raw || typeof raw !== "object") return null;
  if (readFrame(raw)) return null;
  const reasons = (raw as { out_of_date_reasons?: unknown }).out_of_date_reasons;
  return Array.isArray(reasons) && typeof reasons[0] === "string" ? reasons[0] : "this version can't read the part's frame";
}

export function rotationOf(f: PartFrameView): number[][] {
  return f.matrix.slice(0, 3).map((row) => row.slice(0, 3));
}

/** A point of the scan's frame in the part's. */
export function pointToPart(f: PartFrameView, p: ArrayLike<number>): Vec3 {
  const M = f.matrix;
  return [0, 1, 2].map((i) => M[i][0] * p[0] + M[i][1] * p[1] + M[i][2] * p[2] + M[i][3]) as Vec3;
}

/** A direction (a normal, an axis, a difference of two points) of the scan's frame in the part's: turned only. */
export function dirToPart(f: PartFrameView, d: ArrayLike<number>): Vec3 {
  const M = f.matrix;
  return [0, 1, 2].map((i) => M[i][0] * d[0] + M[i][1] * d[1] + M[i][2] * d[2]) as Vec3;
}

/** The part's origin in the scan's frame: `-R^T t`. */
export function originInScan(f: PartFrameView): Vec3 {
  const M = f.matrix;
  return [0, 1, 2].map((j) => -(M[0][j] * M[0][3] + M[1][j] * M[1][3] + M[2][j] * M[2][3])) as Vec3;
}

/** The part's axes as the 3D view draws them (in the scan's frame): the origin, and X, Y, Z as unit directions. */
export function axesInScan(f: PartFrameView): { origin: Vec3; axes: { name: AxisName; dir: Vec3 }[] } {
  return { origin: originInScan(f), axes: AXES.map((name, i) => ({ name, dir: [f.matrix[i][0], f.matrix[i][1], f.matrix[i][2]] as Vec3 })) };
}

/** The frame's axis names in slot order: "Z, X, Y" for the default. */
export function axisOrder(f: PartFrameView): [AxisName, AxisName, AxisName] {
  return [f.axes.primary, f.axes.secondary, f.axes.third];
}

/** Where a fit is and which way it points, as the panels show them: in the part's frame when `frame` is given. */
export function placeOf(shape: FitShape, frame: PartFrameView | null): { position: Vec3; direction: Vec3; positionLabel: string; directionLabel: string } {
  const p = (shape.kind === "circle" ? shape.center : shape.point) as number[];
  const d = (shape.kind === "cylinder" ? shape.axis : shape.normal) as number[];
  return {
    position: frame ? pointToPart(frame, p) : [p[0], p[1], p[2]],
    direction: frame ? dirToPart(frame, d) : [d[0], d[1], d[2]],
    positionLabel: shape.kind === "circle" ? "Centre" : shape.kind === "cylinder" ? "Axis point" : "Point",
    directionLabel: shape.kind === "cylinder" ? "Axis" : "Normal",
  };
}

/** A distance's Δx / Δy / Δz (b − a): along the part's axes when `frame` is given. Its length is the same. */
export function deltaOf(a: ArrayLike<number>, b: ArrayLike<number>, frame: PartFrameView | null): Vec3 {
  const d: Vec3 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  return frame ? dirToPart(frame, d) : d;
}

/** "12.30, -4.10, 0.00" (no "-0.00"). */
export function fmtVec(v: ArrayLike<number>, digits = 2): string {
  return [v[0], v[1], v[2]].map((x) => (Math.abs(x) < 0.5 * 10 ** -digits ? 0 : x).toFixed(digits)).join(", ");
}

/** The body that builds this frame again: the same fits (a point origin as it was) and the same options. Null
 * when a slot has neither an id nor a point (it can't be built again from the record). */
export function rebuildBody(f: PartFrameView): NewFrame | null {
  const slot = (role: FrameRole) => f.built_from.find((s) => s.role === role);
  const ids: (string | NewFrame["origin"] | null)[] = ROLES.map((role) => {
    const s = slot(role);
    if (!s) return null;
    if (s.id) return s.id;
    const p = s.fit.point;
    if (role === "origin" && Array.isArray(p) && p.length === 3 && p.every(isNum)) {
      const sigma = s.fit.sigma_mm;
      return { point: [p[0], p[1], p[2]], ...(isNum(sigma) ? { sigma_mm: sigma } : {}) };
    }
    return null;
  });
  if (ids.some((x) => x === null) || typeof ids[0] !== "string" || typeof ids[1] !== "string") return null;
  return {
    primary: ids[0],
    secondary: ids[1],
    origin: ids[2] as NewFrame["origin"],
    axes: `${f.axes.primary}${f.axes.secondary}`,
    flip_primary: f.flip_primary,
    flip_secondary: f.flip_secondary,
    secondary_zero: f.secondary_zero,
  };
}
