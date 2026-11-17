/**
 * The relation of two fits as the viewer shows it, from the engine's record (APP_API.md "Kept features":
 * `KeptRelation.relation`, `GET .../features/relate`; ohs/measure/relate.py): each value with its label, its ±
 * where the engine gives one, and a hover text that says what the number is and what its ± covers. Where the
 * engine gives no ± the row says so in the engine's terms; the viewer never makes a ± of its own.
 *
 * Imports nothing at run time but lib/fits.ts's formatting, so `npm test` checks it with plain node
 * (relationView.test.ts).
 */
import type { RelationRecord, RelationValue } from "../api/types";
import { fmtAngle, fmtLength, fmtPm } from "./fits.ts";

export type RelationRow = {
  label: string;
  value: string;
  /** What it is and what its ± covers (or why it has none). */
  tip: string;
  x: number;
  sigma: number | null;
};

/** What a ± from the engine is (ohs/measure/relate.py, "What the ± is"). */
export const RELATION_SIGMA_NOTE =
  "± is 1σ from both fits' own ± (taken as independent): how far the number could move with the scan's scatter. It is not the accuracy: calibration, scale and errors a whole region shares come on top.";
/** Why the engine gives no ± (ohs/measure/relate.py, "What it leaves out"): the only case is an angle with a hole's axis. */
export const RELATION_NO_SIGMA =
  "No ±: a hole's axis direction has no ± (its circle is fitted in a plane taken as given), so an angle with it has none.";
/** No ± without a hole: only the demo backend's relations (lib/fits.ts `relate`) have such values. */
export const RELATION_NO_SIGMA_DEMO = "No ±: the demo engine gives none for this number.";

/** Why a value of this relation has no ±, in the engine's words. */
const noSigma = (r: RelationRecord) => (r.kinds.includes("hole") ? RELATION_NO_SIGMA : RELATION_NO_SIGMA_DEMO);

/** Decimals for a value with this 1σ: 1.2, 0.04, 0.004, 0.0004 (as fmtPm). */
function decimals(sigma: number): number {
  const a = Math.abs(sigma);
  return a >= 1 ? 1 : a >= 0.01 ? 2 : a >= 0.001 ? 3 : 4;
}

/** "90.00 ± 0.04°", "14.02 ± 0.03 mm"; without a 1σ "90.00°", "14.02 mm". */
export function fmtValue(q: Pick<RelationValue, "value" | "sigma" | "unit">): string {
  if (q.unit === "deg") {
    if (q.sigma === null) return fmtAngle(q.value);
    const d = Math.max(2, decimals(q.sigma));
    return `${q.value.toFixed(d)} ± ${q.sigma.toFixed(d)}°`;
  }
  return q.sigma === null ? fmtLength(q.value) : fmtPm(q.value, q.sigma);
}

/** What a value is, from its name and label. */
function what(q: RelationValue): string {
  if (q.name === "angle") return q.label.endsWith("to the plane") ? "90° square to the plane, 0° along it." : "0° parallel, 90° square.";
  if (q.name === "centre_distance") return "Straight from one hole's centre to the other's.";
  if (q.label === "Closest distance of the axes") return "Where the two axes pass closest: along the line square to both (0 where they cross).";
  if (q.label === "Distance between the axes") return "Between the two parallel axes, square to them.";
  if (q.label === "Hole centre to plane") return "From the hole's centre to the plane, square to the plane.";
  if (q.label === "Axis to plane") return "From the axis to the plane it runs along, square to the plane.";
  return "Between the two planes along their mean normal, from one region's centre to the other's.";
}

/** The rows of a relation record, in the engine's order and words. */
export function relationRows(r: RelationRecord): RelationRow[] {
  return r.values.map((q) => ({
    label: q.label,
    value: fmtValue(q),
    tip: `${what(q)} ${q.sigma === null ? noSigma(r) : RELATION_SIGMA_NOTE}`,
    x: q.value,
    sigma: q.sigma,
  }));
}

/** The lines under the rows: what the ± are, and why a value has none when one has none. */
export function relationFooter(r: RelationRecord): string[] {
  const out: string[] = [];
  if (r.values.some((q) => q.sigma !== null)) out.push(RELATION_SIGMA_NOTE);
  if (r.values.some((q) => q.sigma === null)) out.push(noSigma(r));
  return out;
}
