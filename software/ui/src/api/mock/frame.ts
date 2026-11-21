/**
 * The demo engine's part's frame (APP_API.md "The part's frame"), a simplified `ohs/measure/frame.py`:
 * enough for the viewer's Coordinate system panel to work in demo mode. The rule:
 *
 * - the primary is a plane: its normal is the first axis (Z by default), the plane Z = 0;
 * - a plane secondary's normal, or a cylinder's axis or a hole's direction lying along the primary face
 *   (more than 45° from Z), turned into the primary plane, is the second axis (X). A face within 20° of the
 *   primary is refused. A cylinder or hole across the face sets X from the origin to it (the origin must then be
 *   a hole, a cylinder or a point, at least 1 mm away);
 * - the origin: a hole's centre, a point or a cylinder's point moved along Z onto the primary plane; a plane
 *   origin, or the strict 3-2-1 switch, makes it where three planes meet (as the engine does);
 * - signs as the engine's: Z points from the primary towards the secondary feature (else the origin), X from a
 *   plane secondary towards the primary's point, an axis's X from the origin towards it;
 * - the flips turn an axis round (and the third with it).
 *
 * Left out: the ± (the frame's sigma is all null, with a warning saying so) and the cylinder crossing checks.
 * Refusals are 422 with a message in the engine's style.
 */
import type { AxisName, FrameRole, FrameSlot, KeptFeature, PartFrameRecord } from "../types";
import { HttpError } from "./host.ts";

type V = [number, number, number];
const dot = (a: number[], b: number[]) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const sub = (a: number[], b: number[]): V => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const scale = (a: number[], s: number): V => [a[0] * s, a[1] * s, a[2] * s];
const cross = (a: number[], b: number[]): V => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (a: number[]): V => scale(a, 1 / (Math.hypot(a[0], a[1], a[2]) || 1));
const lineAngle = (a: number[], b: number[]) => (Math.atan2(Math.hypot(...cross(a, b)), Math.abs(dot(a, b))) * 180) / Math.PI;
const canonical = (v: number[]) => {
  let k = 0;
  for (let i = 1; i < 3; i++) if (Math.abs(v[i]) > Math.abs(v[k])) k = i;
  return v[k] < 0 ? -1 : 1;
};
const refuse = (detail: string): never => {
  throw new HttpError(422, detail, "Pick other fits for the slots, or change the options.");
};

/** A slot's input: a kept fit, or a point (the origin only). */
export type FrameInput = { feature: Omit<KeptFeature, "stale"> } | { point: V; sigma_mm: number | null };

type Feat = { role: FrameRole; kind: "plane" | "cylinder" | "circle" | "point"; p: V; d: V | null; slot: Omit<FrameSlot, "sets"> };
const NAMES = { plane: "plane", cylinder: "cylinder", circle: "hole", point: "point" } as const;

function feat(x: FrameInput, role: FrameRole): Feat {
  if ("point" in x) {
    const fit: Record<string, unknown> = { kind: "point", point: [...x.point] };
    if (x.sigma_mm !== null) fit.sigma_mm = x.sigma_mm;
    return { role, kind: "point", p: [...x.point] as V, d: null, slot: { role, id: null, name: null, kind: "point", fit } };
  }
  const s = x.feature.shape;
  const p = (s.kind === "circle" ? s.center : s.point) as number[];
  const d = (s.kind === "cylinder" ? s.axis : s.normal) as number[];
  return { role, kind: s.kind, p: [p[0], p[1], p[2]], d: unit(d), slot: { role, id: x.feature.id, name: x.feature.name, kind: s.kind, fit: { ...s } } };
}

/** The planes `n · x = c` that meet at one point. */
function meet(planes: [V, V][]): V {
  const [[a, pa], [b, pb], [c, pc]] = planes;
  const bc = cross(b, c),
    cA = cross(c, a),
    ab = cross(a, b);
  const da = dot(a, pa),
    db = dot(b, pb),
    dc = dot(c, pc),
    det = dot(a, bc);
  return [0, 1, 2].map((i) => (bc[i] * da + cA[i] * db + ab[i] * dc) / det) as V;
}

/** The part's frame from three slots (simplified rule, module docstring). Throws HttpError 422 when it can't be made. */
export function mockPartFrame(inputs: [FrameInput, FrameInput, FrameInput], opts: { axes?: string; flip_primary?: boolean; flip_secondary?: boolean; secondary_zero?: boolean } = {}): PartFrameRecord {
  const axes = String(opts.axes ?? "ZX").toUpperCase();
  if (axes.length !== 2 || axes[0] === axes[1] || ![...axes].every((c) => "XYZ".includes(c)))
    refuse(`The axes must be two different letters of X, Y and Z, the primary's axis first and then the secondary's (for example ZX), not ${opts.axes}`);
  const names = [axes[0], axes[1], "XYZ".split("").find((c) => !axes.includes(c))!] as [AxisName, AxisName, AxisName];
  const [Z, X] = names;
  const [f1, f2, f3] = inputs.map((x, i) => feat(x, (["primary", "secondary", "origin"] as const)[i]));
  if (f1.kind !== "plane") refuse(`the primary must be a plane (the face the part sits on, or its main flat face), got a ${NAMES[f1.kind]}`);
  if (f2.kind === "point") refuse("the secondary must be a plane, a cylinder or a hole (it sets the turn about the first axis), got a point");
  const n1 = f1.d!,
    p1 = f1.p;
  const tilt = lineAngle(f2.d!, n1);
  let mode: "plane" | "along" | "through" = "plane";
  if (f2.kind === "plane") {
    if (tilt < 20)
      refuse(`the secondary face is nearly parallel to the primary (${tilt.toFixed(1)}° between them; it needs 20° or more), so it cannot set the turn about ${Z}: pick a face that stands across the primary, such as a side face, or a hole or a cylinder`);
  } else mode = tilt < 45 ? "through" : "along";
  if (mode === "through" && f3.kind === "plane")
    refuse(`a ${NAMES[f2.kind]} across the primary face sets the turn only with a hole, a cylinder or a point as the origin (the ${X} axis runs from the origin to it): pick one as the origin, or a side face as the secondary`);
  if (mode === "through" && opts.secondary_zero) refuse(`Strict 3-2-1 needs a plane, or a cylinder or a hole lying along the primary face, as the secondary; a ${NAMES[f2.kind]} across the face only sets the turn`);
  const warnings: string[] = ["the demo engine does not work out the frame's ±"];
  // Z: from the primary plane towards the part
  let s1 = 0;
  for (const f of [f2, f3]) {
    const h = dot(n1, sub(f.p, p1));
    if (Math.abs(h) >= 0.5) {
      s1 = h > 0 ? 1 : -1;
      break;
    }
  }
  if (!s1) {
    s1 = canonical(n1);
    warnings.push(`the features all lie in the primary face, so nothing tells on which side of it the part is: the ${Z} axis was set by the scan's own axes; flip it if it points out of the part`);
  }
  let a1 = scale(n1, s1);
  const onPlane = (p: V): V => sub(p, scale(a1, dot(a1, sub(p, p1))));
  let a2: V;
  if (mode === "through") {
    const w = sub(onPlane(f2.p), onPlane(f3.p));
    const base = Math.hypot(...w);
    if (base < 1) refuse(`the secondary ${NAMES[f2.kind]} is too close to the origin (${base.toFixed(2)} mm; it needs 1 mm or more) to set the turn about ${Z}: pick another hole farther away, or a side face`);
    a2 = unit(w);
  } else {
    const w = unit(sub(f2.d!, scale(a1, dot(f2.d!, a1))));
    const tests = mode === "plane" ? [dot(f2.d!, sub(p1, f2.p)), dot(f2.d!, sub(f3.p, f2.p))] : [dot(w, sub(f2.p, f3.p))];
    const h = tests.find((x) => Math.abs(x) >= 0.5);
    if (h === undefined) warnings.push(`nothing tells which way the ${X} axis should run along the secondary ${NAMES[f2.kind]}: it was set by the scan's own axes; flip it if needed`);
    a2 = scale(w, h === undefined ? canonical(w) : h > 0 ? 1 : -1);
  }
  if (opts.flip_primary) a1 = scale(a1, -1);
  if (opts.flip_secondary) a2 = scale(a2, -1);
  const a3 = cross(a1, a2);
  // the origin
  let O: V;
  if (mode !== "through" && (f3.kind === "plane" || opts.secondary_zero)) {
    const second: [V, V] = mode === "plane" ? [f2.d!, f2.p] : [a3, f2.p];
    const third: [V, V] = f3.kind === "plane" ? [f3.d!, f3.p] : [mode === "plane" ? a3 : a2, onPlane(f3.p)];
    if (Math.abs(dot(a1, cross(second[0], third[0]))) < 0.2) refuse("the origin face does not fix the origin: it is nearly parallel to the line where the primary and the secondary meet: pick a face across that line, or a hole");
    O = meet([[a1, p1], second, third]);
  } else O = onPlane(f3.p);
  // rows by axis name, right-handed
  const R: V[] = [
    [0, 0, 0],
    [0, 0, 0],
    [0, 0, 0],
  ];
  const at = (c: string) => "XYZ".indexOf(c);
  R[at(names[0])] = a1;
  R[at(names[1])] = a2;
  R[at(names[2])] = a3;
  if (dot(R[0], cross(R[1], R[2])) < 0) R[at(names[2])] = scale(a3, -1);
  const t = R.map((r) => -dot(r, O));
  const sets = [`the ${Z} axis (its normal) and ${Z} = 0 (the plane)`, `the ${X} axis`, "the origin"];
  return {
    format_version: 1,
    units: "mm",
    matrix: [[...R[0], t[0]], [...R[1], t[1]], [...R[2], t[2]], [0, 0, 0, 1]],
    axes: { primary: names[0], secondary: names[1], third: names[2] },
    flip_primary: !!opts.flip_primary,
    flip_secondary: !!opts.flip_secondary,
    secondary_zero: !!opts.secondary_zero,
    built_from: [f1, f2, f3].map((f, i) => ({ ...f.slot, sets: sets[i] })),
    sigma: { angle_deg: null, tilt_deg: null, turn_deg: null, origin_mm: null, origin_axes_mm: [null, null, null] },
    warnings,
  };
}

/** Whether a kept frame is still current (as `ohs.measure.features.frame_state`): out of date when a fit it was
 * built from was deleted, or its shape is not the one it was built from. */
export function frameState(frame: PartFrameRecord, features: Omit<KeptFeature, "stale">[]): { readable: true; out_of_date: boolean; out_of_date_reasons: string[] } {
  const reasons: string[] = [];
  for (const s of frame.built_from) {
    if (!s.id) continue;
    const f = features.find((x) => x.id === s.id);
    if (!f) reasons.push(`the ${s.role} "${s.name ?? s.id}" was deleted`);
    else if (JSON.stringify(f.shape) !== JSON.stringify(s.fit)) reasons.push(`the ${s.role} "${f.name}" was fitted again or changed since`);
  }
  return { readable: true, out_of_date: reasons.length > 0, out_of_date_reasons: reasons };
}
