/**
 * Why an area of the Check answer is weak and where to hold the scanner to scan it again (FEATURE_PLAN N1,
 * APP_API.md "POST .../check/why"), the pure part: the reason in plain words, what could not be told, the
 * pose in words and as a small side view, the plan's lines, the scanner's outline in the 3D view and the
 * camera move that shows it beside its area.
 *
 * Imports nothing at run time but `./check.ts` (itself pure), so `npm test` checks it with plain node.
 */
import type { CheckPlanPose, CheckPose, CheckReason, CheckResult, CheckWhy, CheckWhyArea } from "../api/types";
import { fmtShare } from "./check.ts";

type Vec3 = [number, number, number];

/** A reason's name, for the list. */
export const REASON_TITLE: Record<CheckReason, string> = {
  not_seen: "Not seen",
  steep: "Seen only at a steep angle",
  far: "Seen from too far",
  few_looks: "Seen too few times",
  edge: "On an edge or a tight curve",
  unknown: "No reason found",
};

/** A reason in a few words, for "also": "seen too few times". */
const REASON_SHORT: Record<CheckReason, string> = {
  not_seen: "next to surface that was not seen",
  steep: "seen only at a steep angle",
  far: "seen from too far",
  few_looks: "seen too few times",
  edge: "on an edge or a tight curve",
  unknown: "no reason found",
};

const deg = (x: number | null) => `${Math.round(x ?? 0)}°`;
const mm = (x: number | null) => `${Math.round(x ?? 0)} mm`;
const count = (x: number | null) => String(Math.round(x ?? 0));

/** Why the area is weak, in plain words, with what to do about it. `workingMm`: the scanner's working distance. */
export function reasonWords(a: Pick<CheckWhyArea, "reason" | "kind" | "evidence">, workingMm: number | null): string {
  const e = a.evidence;
  switch (a.reason) {
    case "not_seen":
      return a.kind === "not_measured"
        ? "Nothing measured this surface: there was a hole or a shadow here, and hole filling made it up. Scan it from where it can be seen."
        : "It lies next to surface the scan never saw (a shadow, or where the scan ended). Come round to where the surface goes on.";
    case "steep":
      return `Every look at it was at a steep angle: even the squarest was ${deg(e.view_angle_deg)} off square on. Hold the scanner more square on to it.`;
    case "far":
      return `It was seen only from too far: the nearest look was ${mm(e.distance_mm)} away. Hold the scanner about ${mm(workingMm ?? 250)} from it.`;
    case "few_looks":
      return `It was seen too few times: ${count(e.looks)} separate looks, where the surface that passes had ${count(e.looks_passing)}. Go over it again, more slowly.`;
    case "edge":
      return "It is on an edge, a tight curve or an uneven surface. The mesh's own uncertainty there is above the tolerance, and another pass alone does not lower it.";
    default:
      return "The scan's data do not tell why this area is weak.";
  }
}

/** The other reasons that hold too, in one sentence ("Also seen too few times and on an edge."); null when none. */
export function alsoWords(a: Pick<CheckWhyArea, "reason" | "reasons">): string | null {
  const rest = a.reasons.filter((r) => r !== a.reason).map((r) => REASON_SHORT[r]);
  if (!rest.length) return null;
  const list = rest.length === 1 ? rest[0] : `${rest.slice(0, -1).join(", ")} and ${rest[rest.length - 1]}`;
  return `Also ${list}.`;
}

/** What the engine's words for a missing piece of data mean for the user. */
function plainMissing(rest: string): string {
  if (rest.includes("no count of looks")) return "this mesh has no count of looks (it was made without a graphics card, or by an earlier version of the app)";
  if (rest.includes("voxel size")) return "the scan's report does not say how fine its mesh was made";
  if (rest.includes("hole filling made it")) return "hole filling made all of it, so nothing looked at it";
  if (rest.includes("no surface passes")) return "no surface meets the tolerance to compare with";
  return rest;
}

/** What could not be told about the area, each in a sentence ("The angle and distance it was seen from can't be told: ..."). */
export function unknownWords(a: Pick<CheckWhyArea, "unknown_because">): string[] {
  const out: string[] = [];
  for (const u of a.unknown_because) {
    const i = u.indexOf(": ");
    const key = i >= 0 ? u.slice(0, i) : "";
    const rest = plainMissing(i >= 0 ? u.slice(i + 2) : u);
    const what =
      key === "steep, far"
        ? "The angle and distance it was seen from can't be told"
        : key === "few_looks"
          ? "How many times it was seen can't be told"
          : key === "edge"
            ? "Whether it is on an edge can't be told"
            : "Something can't be told";
    out.push(`${what}: ${rest}.`);
  }
  return out;
}

/** The pose in a few words: "254 mm away, square on" / "254 mm away, 20° off square". */
export function poseShort(p: Pick<CheckPose, "distance_mm" | "tilt_deg">): string {
  return `${Math.round(p.distance_mm)} mm away, ${p.tilt_deg < 0.5 ? "square on" : `${Math.round(p.tilt_deg)}° off square`}`;
}

/** "3 places to scan from" (the plan's title). */
export function planTitle(n: number): string {
  return n === 1 ? "1 place to scan from" : `${n} places to scan from`;
}

/** The other areas a place of the plan names before it counts the rest ("areas 5, 7, 22, 26 and 6 more"). */
export const PLAN_NAMED = 4;

/** One place of the plan: "For area 2, 254 mm away, square on: it sees 95 % of it, and most of areas 3 and 5." */
export function planWords(p: CheckPlanPose): string {
  const most = p.also_sees.filter((s) => s.share >= 0.5).map((s) => s.area + 1);
  // a pose over a large area sees dozens of small ones: name the first few, count the rest
  const list =
    most.length > PLAN_NAMED + 1
      ? `${most.slice(0, PLAN_NAMED).join(", ")} and ${most.length - PLAN_NAMED} more`
      : most.length > 1
        ? `${most.slice(0, -1).join(", ")} and ${most[most.length - 1]}`
        : `${most[0]}`;
  const also = most.length ? `, and most of area${most.length > 1 ? "s" : ""} ${list}` : "";
  const lead = p.kind === "more" ? `For area ${p.area + 1} again, from another side` : `For area ${p.area + 1}`;
  return `${lead}, ${poseShort(p.pose)}: it sees ${fmtShare(p.share_seen)} of it${also}.`;
}

/** The reasons answer is for this Check answer: the same mesh version, tolerance and areas. */
export function whyFits(why: CheckWhy, result: CheckResult): boolean {
  if (why.version !== result.version || why.tolerance_mm !== result.tolerance_mm || why.areas.length !== result.areas.length) return false;
  return why.areas.every((a, i) => a.n_faces === result.areas[i].n_faces && a.kind === result.areas[i].kind);
}

// ------------------------------------------------------------------ the small side view
/** The side view of a pose (an SVG of `w` x `h`): the surface, its normal, the scanner leaning `tiltDeg` off it, the line of sight. */
export type SideSketch = {
  surface: [number, number, number, number];
  patch: [number, number, number, number];
  normal: [number, number, number, number];
  sight: [number, number, number, number];
  /** The scanner's centre and its turn (degrees, SVG's: clockwise), to draw a box there. */
  scanner: { x: number; y: number; angleDeg: number };
  /** Where the tilt's label goes. */
  label: { x: number; y: number };
};

export function sideSketch(tiltDeg: number, w = 120, h = 76): SideSketch {
  const t = (Math.min(Math.max(tiltDeg, 0), 85) * Math.PI) / 180;
  const cx = w / 2;
  const cy = h - 10;
  const r = h - 24;
  const sx = cx + r * Math.sin(t);
  const sy = cy - r * Math.cos(t);
  return {
    surface: [10, cy, w - 10, cy],
    patch: [cx - 12, cy, cx + 12, cy],
    normal: [cx, cy, cx, cy - r * 0.55],
    sight: [sx, sy, cx, cy],
    scanner: { x: sx, y: sy, angleDeg: (t * 180) / Math.PI },
    label: { x: cx + 0.42 * r * Math.sin(t / 2) + 4, y: cy - 0.42 * r * Math.cos(t / 2) },
  };
}

// ------------------------------------------------------------------ the scanner in the 3D view
const sub = (a: ArrayLike<number>, b: ArrayLike<number>): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a: ArrayLike<number>, b: ArrayLike<number>): Vec3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const scale = (a: ArrayLike<number>, s: number): Vec3 => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a: ArrayLike<number>, b: ArrayLike<number>) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: ArrayLike<number>, b: ArrayLike<number>): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (a: ArrayLike<number>): Vec3 => {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
};

/** The scanner's axes at a pose (the mesh's frame): right, up, and forward (towards the area). */
export function poseAxes(p: Pick<CheckPose, "look_dir" | "up">): { right: Vec3; up: Vec3; fwd: Vec3 } {
  const fwd = unit(p.look_dir);
  let up = sub(p.up, scale(fwd, dot(p.up, fwd)));
  up = Math.hypot(up[0], up[1], up[2]) > 1e-6 ? unit(up) : unit(cross(fwd, Math.abs(fwd[2]) < 0.9 ? [0, 0, 1] : [0, 1, 0]));
  return { right: unit(cross(fwd, up)), up, fwd }; // camera x = y x z, with y = -up and z = fwd
}

/** Sizes of the drawn scanner (mm): a box about the size of the housing, and a short view cone. */
const BODY: Vec3 = [120, 34, 40]; // across (the two cameras), up, deep
const CONE_MM = 70;
const HALF_W = Math.atan(640 / 720); // the nominal camera's half field of view, across and up
const HALF_H = Math.atan(400 / 720);

/**
 * The scanner at a pose as line segments in the mesh's frame (x y z of both ends, one after the other): its
 * box, a short cone in the direction it looks, and the line of sight from it to the area's centre.
 */
export function scannerLines(p: CheckPose): { body: Float32Array; cone: Float32Array; sight: Float32Array } {
  const { right, up, fwd } = poseAxes(p);
  const c = p.position_mm;
  const corner = (sx: number, sy: number, sz: number) =>
    add(add(add(c, scale(right, (sx * BODY[0]) / 2)), scale(up, (sy * BODY[1]) / 2)), scale(fwd, sz * BODY[2] - BODY[2]));
  const body: number[] = [];
  const seg = (out: number[], a: Vec3, b: Vec3) => out.push(a[0], a[1], a[2], b[0], b[1], b[2]);
  const signs: [number, number][] = [
    [-1, -1],
    [1, -1],
    [1, 1],
    [-1, 1],
  ];
  for (let i = 0; i < 4; i++) {
    const [ax, ay] = signs[i];
    const [bx, by] = signs[(i + 1) % 4];
    seg(body, corner(ax, ay, 0), corner(bx, by, 0));
    seg(body, corner(ax, ay, 1), corner(bx, by, 1));
    seg(body, corner(ax, ay, 0), corner(ax, ay, 1));
  }
  const cone: number[] = [];
  const tip = add(c, scale(fwd, CONE_MM));
  const ends = signs.map(([sx, sy]) => add(add(tip, scale(right, sx * CONE_MM * Math.tan(HALF_W))), scale(up, sy * CONE_MM * Math.tan(HALF_H))));
  for (let i = 0; i < 4; i++) {
    seg(cone, [c[0], c[1], c[2]], ends[i]);
    seg(cone, ends[i], ends[(i + 1) % 4]);
  }
  const sight: number[] = [];
  seg(sight, [c[0], c[1], c[2]], [p.target_mm[0], p.target_mm[1], p.target_mm[2]]);
  return { body: new Float32Array(body), cone: new Float32Array(cone), sight: new Float32Array(sight) };
}

/**
 * Where the camera of the 3D view goes to show the scanner beside its area: a sphere round both, seen from
 * the scanner's side and from the side the area faces (the 3D view's axes: y and z change sign).
 */
export function poseFrame(p: CheckPose, normal: ArrayLike<number> | null, areaRadiusMm: number): { sphere: { center: Vec3; radius: number }; direction: Vec3 } {
  const mid = scale(add(p.position_mm, p.target_mm), 0.5);
  const half = Math.hypot(...sub(p.position_mm, p.target_mm)) / 2;
  const { right } = poseAxes(p);
  const n = normal && Math.hypot(normal[0], normal[1], normal[2]) > 1e-6 ? unit(normal) : scale(unit(p.look_dir), -1);
  const d = unit(add(scale(right, 0.85), scale(n, 0.55)));
  return { sphere: { center: [mid[0], -mid[1], -mid[2]], radius: half + Math.max(areaRadiusMm, 6) + BODY[0] / 2 }, direction: [d[0], -d[1], -d[2]] };
}
