/**
 * The comparison with a reference model (FEATURE_PLAN I2; APP_API.md "References and the comparison with one"), the
 * pure part: the colours of the "Against the reference" shading, the value under the cursor, and the panel's words:
 * the units question with its two sizes, the three ways to align and what each one means, the alignment's result, the
 * shares that were not compared and why, and each area outside the tolerance.
 *
 * The array holds one signed deviation in mm per vertex (+ = the scan lies outside the reference: material where there
 * should be none; - = inside: material missing), NaN where not compared, and a state per vertex: 0 compared, 1 not
 * measured (a filled hole, no usable sigma), 2 not on the reference (a table, a clamp, surface far off). The colours are
 * the deviation map's (lib/deviation.ts), saturating at plus and minus the tolerance; surface that is not on the
 * reference is a plain grey, darker than the scale's middle (as the Lasers shading's "no value"); not measured is the
 * confidence map's striped grey (alpha 0, drawn striped by the surface material).
 *
 * Imports nothing at run time but the other pure modules, so `npm test` checks it with plain node.
 */
import type { ComparisonCoverage, ReferenceAlignment, ToleranceRegion, UnitsFinding } from "../api/types";
import { fmtShare, fmtTolerance } from "./check.ts";
import { deviationColors, type DeviationTheme } from "./deviation.ts";

export const STATE_COMPARED = 0;
export const STATE_NOT_MEASURED = 1;
export const STATE_OFF_REFERENCE = 2;

/** Surface that is not on the reference (sRGB): a plain grey, clearly darker than the scale's middle grey. */
export const OFF_REFERENCE: Record<DeviationTheme, string> = { light: "#8b8882", dark: "#6a6966" };

const MINUS = "−";
const rgb = (h: string): [number, number, number] => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];

/**
 * RGBA vertex colours of the comparison at `limit` mm (the strongest colour at plus and minus it), written into `out`.
 * `toLinear` converts an sRGB byte to the byte the geometry stores; `notMeasured` is the sRGB colour of a vertex that was
 * not measured (alpha 0: drawn striped), a vertex not on the reference gets {@link OFF_REFERENCE} at full alpha.
 */
export function referenceColors(
  values: Float32Array,
  states: Uint8Array,
  limit: number,
  theme: DeviationTheme,
  toLinear: ArrayLike<number>,
  notMeasured: string,
  out: Uint8Array = new Uint8Array(values.length * 4),
): Uint8Array {
  deviationColors(values, limit, theme, toLinear, notMeasured, out, 0);
  const off = rgb(OFF_REFERENCE[theme]).map((v) => toLinear[v]);
  for (let i = 0, o = 0; i < values.length; i++, o += 4) {
    if (states[i] !== STATE_OFF_REFERENCE) continue;
    out[o] = off[0];
    out[o + 1] = off[1];
    out[o + 2] = off[2];
    out[o + 3] = 255;
  }
  return out;
}

/** A deviation without its sign's words: "0.034 mm", "0.12 mm", "1.5 mm". */
export function fmtDev(mm: number | null | undefined, signed = true): string {
  if (mm === null || mm === undefined || !Number.isFinite(mm)) return "–";
  const a = Math.abs(mm);
  const text = a < 0.1 ? a.toFixed(3) : a < 10 ? a.toFixed(2) : a.toFixed(1);
  if (!signed || Number(text) === 0) return `${text}\u202fmm`;
  return `${mm > 0 ? "+" : MINUS}${text}\u202fmm`;
}

/** What the cursor is over: "+0.12 mm (outside)", "−0.30 mm (inside)", "not on the reference", "not measured". */
export function referenceReadout(v: number | null, state: number): string {
  if (state === STATE_OFF_REFERENCE) return "not on the reference";
  if (state === STATE_NOT_MEASURED || v === null || Number.isNaN(v)) return "not measured";
  const text = fmtDev(v);
  if (text.startsWith("+")) return `${text} (outside)`;
  if (text.startsWith(MINUS)) return `${text} (inside)`;
  return `${text} (on it)`;
}

/** The state under a point of a triangle: its nearest corner's (the barycentric weights `w`). */
export function stateAt(states: ArrayLike<number>, a: number, b: number, c: number, w: ArrayLike<number>): number {
  const near = w[0] >= w[1] && w[0] >= w[2] ? a : w[1] >= w[2] ? b : c;
  return states[near];
}

// ---------------------------------------------------------------- the units question
/** "155 mm", "3,937 mm", "0.42 mm": a size across. */
export function fmtSize(mm: number | null | undefined): string {
  if (mm === null || mm === undefined || !Number.isFinite(mm)) return "–";
  if (mm >= 100) return `${Math.round(mm).toLocaleString("en-US")}\u202fmm`;
  return `${mm >= 10 ? mm.toFixed(1) : mm >= 1 ? mm.toFixed(2) : mm.toPrecision(2)}\u202fmm`;
}

export type UnitsChoice = {
  /** The question in one line. */
  question: string;
  /** Scale it: the size it then has, or null when no unit fits (only "as it is" is offered). */
  scaled: { factor: number; label: string; size: string } | null;
  /** Keep the numbers as mm: the size as read. */
  asIs: { label: string; size: string };
  /** The scan's part, for comparing. */
  scan: string;
};

const UNIT_WORDS: Record<string, string> = { inch: "inches", cm: "centimetres", m: "metres", "µm": "micrometres" };

/** The units question as a choice with the two sizes (null unless the finding waits for an answer). */
export function unitsChoice(u: UnitsFinding): UnitsChoice | null {
  if (u.status !== "pending") return null;
  const ref = u.reference_size_mm ?? null;
  const scaled =
    u.factor !== null && u.unit
      ? { factor: u.factor, label: `It is in ${UNIT_WORDS[u.unit] ?? u.unit}: scale it by ${u.factor}`, size: fmtSize(ref === null ? null : ref * u.factor) }
      : null;
  return {
    question: scaled ? `Is the reference in ${UNIT_WORDS[u.unit!] ?? u.unit}?` : "The reference and the scan are very different in size",
    scaled,
    asIs: { label: "Keep it as it is (mm)", size: fmtSize(ref) },
    scan: fmtSize(u.scan_size_mm),
  };
}

/** The units as settled, in a few words, or null when there was nothing to settle. */
export function unitsNote(u: UnitsFinding, scale: number): string | null {
  if (u.status === "applied") return `Scaled by ${scale}${u.unit ? ` (${UNIT_WORDS[u.unit] ?? u.unit} to mm)` : ""}, as you confirmed.`;
  if (u.status === "kept") return "Taken as mm, as you confirmed.";
  return null;
}

// ---------------------------------------------------------------- how to align
export type AlignMethod = "shape" | "datums" | "points";

export const ALIGN_METHODS: { value: AlignMethod; label: string; help: string }[] = [
  {
    value: "shape",
    label: "From the shape",
    help:
      "A best fit: the scan is moved until its surface matches the reference as closely as it can overall. An overall error of the part (a little too long, bent, " +
      "shifted) is spread over the whole part, so it shows as small deviations everywhere instead of where it is. Takes a few seconds.",
  },
  {
    value: "datums",
    label: "On the part's axes",
    help:
      "No fit at all: the scan's coordinate system (built from three kept fits) is put onto the reference's own axes, so the reference must be modelled in the part's " +
      "axes. An overall error then shows where it is: a part 0.5\u202fmm too long reads 0.5\u202fmm at the far end. Needs a current coordinate system.",
  },
  {
    value: "points",
    label: "From points",
    help:
      "Click three or more matching points on the scan and on the reference, in the same order. For when the shape alone does not find the pose (a symmetric part, " +
      "a scan of only a small part of it). The points are then refined on the shapes (a best fit), unless you turn that off.",
  },
];

/** The alignment's result in words: which way it was aligned, the engine's verdict, its warnings, and for a best fit how far it lies from the datum pose. */
export function alignmentLines(al: ReferenceAlignment): { title: string; verdict: string; notes: string[] } {
  const notes = [...al.warnings];
  if (al.fit?.note) notes.push(al.fit.note.charAt(0).toUpperCase() + al.fit.note.slice(1) + ".");
  if (al.vs_datums) notes.push(al.vs_datums.note);
  const title = al.accepted ? `Aligned ${al.described}` : `Not aligned (${al.described})`;
  return { title, verdict: al.verdict, notes: notes.map((n) => (/[.!?]$/.test(n) ? n : `${n}.`)).map((n) => n.charAt(0).toUpperCase() + n.slice(1)) };
}

// ---------------------------------------------------------------- the answer
/** "87 % within ± 0.3 mm (8.2 % above, 5.2 % below)", from the shares (the engine's sentence says the same). */
export function toleranceSentence(a: { share_within: number | null; share_above: number | null; share_below: number | null; tolerance_mm: number }): string {
  if (a.share_within === null) return "Nothing of the scan could be compared with the reference.";
  const pct = (x: number, d: number) => `${(x * 100).toFixed(d)}\u202f%`;
  let s = `${pct(a.share_within, 0)} within ± ${fmtTolerance(a.tolerance_mm)}\u202fmm`;
  if (a.share_above || a.share_below) s += ` (${pct(a.share_above ?? 0, 1)} above, ${pct(a.share_below ?? 0, 1)} below)`;
  return s;
}

/**
 * What was not compared and why, and how much of the reference the scan did not cover, in sentences:
 * "12 % of the scan is not on the reference (a table, a clamp, or surface more than 3 mm from it)." ...
 */
export function coverageLines(c: ComparisonCoverage, nearMm = 3): string[] {
  const out: string[] = [];
  const off = c.off_reference_share ?? 0;
  const nm = c.not_measured_share ?? 0;
  if (off > 0) out.push(`${fmtShare(off)} of the scan is not on the reference (a table, a clamp, or surface more than ${nearMm}\u202fmm from it): grey, and in no share.`);
  if (nm > 0) out.push(`${fmtShare(nm)} of the scan was not measured (filled holes, or no usable uncertainty): striped, and in no share.`);
  if (c.uncovered_share !== null && c.uncovered_share > 0)
    out.push(`${fmtShare(c.uncovered_share)} of the reference's surface was not scanned: nothing is said about it.`);
  else if (c.uncovered_share === 0) out.push("The scan covers all of the reference.");
  return out;
}

/** An area's title: "Above +0.62 mm" (mean), with its worst. */
export function regionTitle(r: Pick<ToleranceRegion, "side" | "mean_mm" | "worst_mm">): { title: string; worst: string } {
  return { title: `${r.side === "above" ? "Outside" : "Inside"} by ${fmtDev(r.mean_mm, false)} on average`, worst: `worst ${fmtDev(r.worst_mm)}` };
}

/**
 * Whether an area exceeds the scan's own uncertainty, said plainly: a deviation within k sigma of the scan is no
 * evidence of a real difference.
 */
export function regionUncertainty(r: Pick<ToleranceRegion, "exceeds_uncertainty" | "sigma_median_mm">, k = 2): { tone: "ok" | "warn" | "none"; text: string } {
  if (r.exceeds_uncertainty === null || r.sigma_median_mm === null)
    return { tone: "none", text: "The scan carries no uncertainty here, so it cannot tell a real difference from its own error." };
  const band = `${fmtTolerance(k)}σ = ± ${fmtDev(k * r.sigma_median_mm, false)}`;
  return r.exceeds_uncertainty
    ? { tone: "ok", text: `Beyond the scan's own uncertainty (${band}): a real difference.` }
    : { tone: "warn", text: `Within the scan's own uncertainty (${band}): this alone is no evidence of a real difference. Scan it again more closely to tell.` };
}

/** Where the camera goes to look at an area: its centre in the 3D view's axes (y up) and a radius. */
export function regionSphere(r: Pick<ToleranceRegion, "center_scan" | "radius_mm">): { center: [number, number, number]; radius: number } {
  const c = r.center_scan;
  return { center: [c[0], -c[1], -c[2]], radius: Math.max(r.radius_mm, 6) };
}

/** A 4 x 4 matrix (rows) as 16 numbers, row-major; null for anything else. */
export function rowMajor(M: unknown): number[] | null {
  if (!Array.isArray(M) || M.length !== 4) return null;
  const out: number[] = [];
  for (const row of M) {
    if (!Array.isArray(row) || row.length !== 4) return null;
    for (const x of row) {
      if (typeof x !== "number" || !Number.isFinite(x)) return null;
      out.push(x);
    }
  }
  return out;
}
