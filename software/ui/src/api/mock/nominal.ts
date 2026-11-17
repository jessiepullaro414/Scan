/**
 * The demo engine's nominal values (APP_API.md "Nominal values"), enough for the viewer's chips to work in demo
 * mode. What it does, and what it leaves out:
 *
 * - **Offers by plain ±2σ rounding.** A fit's diameter (or radius) and each relation value with a ± are offered the
 *   round values of the engine's steps (whole, half, tenth millimetres; 0° and 90°, then 30°, 45° and 60°, then whole
 *   degrees) that lie within twice the ±, coarsest step first, at most two (more: "too_many"), none when the value is
 *   already round (within 0.005) or has no ±. **No bias table**: the demo never offers a value only because of a
 *   known bias, and never refuses for a warning. "parallel" and "perpendicular" are the angles' words; "in one plane"
 *   and "coaxial" are not given. The poses are always "tracked".
 * - **Approvals** are kept per scan in memory beside the measured values, as the engine keeps them; an approval is
 *   marked "check again" when the value measured now differs from the one it was approved against and does not
 *   allow the nominal by the same rule.
 *
 * Plain functions, checked on their own (nominal.test.ts).
 */
import type { ApprovedNominal, FitShape, NominalOffer, NominalValueName, RelationRecord } from "../types";

const AT_STEP = 0.005;
const MAX_CANDIDATES = 2;
const EPS = 1e-9;
const LENGTH_STEPS: [number, number[] | null][] = [
  [1, null],
  [0.5, null],
  [0.1, null],
];
const ANGLE_STEPS: [number, number[] | null][] = [
  [90, [0, 90]],
  [15, [30, 45, 60]],
  [1, Array.from({ length: 91 }, (_, i) => i)],
];
const WORDS: Record<number, string> = { 0: "parallel", 90: "perpendicular" };

/** One measured value: what the search needs. */
type Value = { value: NominalValueName; label: string; unit: "mm" | "deg"; measured: number; sigma: number | null };

const fmt = (x: number) => x.toFixed(6).replace(/\.?0+$/, "");
const shown = (v: Value) => `${v.label} ${v.measured.toFixed(2)}${v.sigma === null ? "" : ` ± ${v.sigma.toFixed(2)}`}${v.unit === "deg" ? "°" : " mm"}`;

/** The round values of one step near the measured value (those of a coarser step left out). */
function candidates(v: Value, step: number, values: number[] | null, coarser: Set<number>): number[] {
  const reach = 2 * (v.sigma ?? 0) + step;
  const all = values ?? Array.from({ length: Math.floor((2 * reach) / step) + 3 }, (_, i) => Math.round((Math.floor((v.measured - reach) / step) + i) * step * 1e6) / 1e6);
  const lowest = v.value === "diameter" || v.value === "radius" ? 1e-6 : 0;
  const highest = v.unit === "deg" ? 90 : Infinity;
  const out = all.filter((x) => x >= lowest - EPS && x <= highest + EPS && !coarser.has(x));
  out.forEach((x) => coarser.add(x));
  return out;
}

/** The demo's offer for one value (module docstring). */
export function mockOffer(v: Value): NominalOffer {
  const offer: NominalOffer = { ...v, suggestions: [], reason_code: null, reason: null };
  const sigma = v.sigma;
  if (sigma === null || !Number.isFinite(sigma)) return { ...offer, sigma: null, reason_code: "no_sigma", reason: `${v.label} has no ±, so nothing is suggested.` };
  const unit = v.unit === "deg" ? "°" : " mm";
  const coarser = new Set<number>();
  for (const [step, values] of v.unit === "deg" ? ANGLE_STEPS : LENGTH_STEPS) {
    const cands = candidates(v, step, values, coarser);
    const at = cands.find((c) => Math.abs(c - v.measured) < AT_STEP);
    if (at !== undefined) return { ...offer, reason_code: "already_round", reason: `${shown(v)} is already a round value (${fmt(at)}${unit}): there is nothing to suggest.` };
    const fits = cands.filter((c) => Math.abs(v.measured - c) <= 2 * sigma + EPS).sort((a, b) => Math.abs(a - v.measured) - Math.abs(b - v.measured));
    if (fits.length > MAX_CANDIDATES) return { ...offer, reason_code: "too_many", reason: `The ± of ${shown(v)} is too large to tell round values apart. Nothing is suggested.` };
    if (fits.length) {
      offer.suggestions = fits.map((c) => {
        const word = v.unit === "deg" ? (WORDS[c] ?? null) : null;
        const named = word ? `${word} (${fmt(c)}°)` : v.value === "diameter" ? `Ø ${fmt(c)} mm` : v.value === "radius" ? `R ${fmt(c)} mm` : `${fmt(c)}${unit}`;
        return {
          nominal: c,
          difference: c - v.measured,
          step,
          allowed_by: "2 sigma" as const,
          bias: null,
          word,
          directions: word,
          sentence: `${shown(v)}: ${named}? It is ${Math.abs(c - v.measured).toFixed(2)}${unit} away, within twice the ± (${(2 * sigma).toFixed(2)}${unit}). The demo engine uses no known bias. The ± is the spread from scan to scan, not the accuracy: accept, change or ignore.`,
        };
      });
      return offer;
    }
  }
  return { ...offer, reason_code: "no_candidate", reason: `No round value lies within twice the ± of ${shown(v)}.` };
}

/** A fit's diameter (or radius) as the search sees it; null for a plane. */
export function fitValue(shape: FitShape, value: "diameter" | "radius" = "diameter"): Value | null {
  if (shape.kind === "plane") return null;
  const k = value === "diameter" ? 1 : 0.5;
  return { value, label: value === "diameter" ? "Diameter" : "Radius", unit: "mm", measured: shape.diameter_mm * k, sigma: Number.isFinite(shape.diameter_sigma_mm) ? shape.diameter_sigma_mm * k : null };
}

/** A relation's values as the search sees them. */
export function relationValues(r: RelationRecord): Value[] {
  return r.values.map((q) => ({ value: q.name, label: q.label, unit: q.unit, measured: q.value, sigma: q.sigma }));
}

/** Whether `nominal` is allowed for a value measured now (within twice the ±, or what it reads as). */
export function mockAllowed(v: Value | null, nominal: number): boolean {
  if (!v || v.sigma === null) return false;
  const d = Math.abs(v.measured - nominal);
  return d < AT_STEP || d <= 2 * v.sigma + EPS;
}

/** What an approval is kept as (the engine's record). */
export type StoredNominal = Omit<ApprovedNominal, "measured_now" | "sigma_now" | "check_again" | "check_reason">;

/** An approval as the engine shows it: with the value now and "check again" (measured again, no longer allowed). */
export function nominalView(n: StoredNominal, now: Value | null): ApprovedNominal {
  const changed = !now || Math.abs(now.measured - n.measured) > 1e-9 || (now.sigma ?? -1) !== (n.sigma ?? -1);
  const again = changed && !mockAllowed(now, n.nominal);
  const u = n.unit === "deg" ? "°" : " mm";
  const s = (x: number | null, sg: number | null) => (x === null ? "nothing" : `${x.toFixed(2)}${sg === null ? "" : ` ± ${sg.toFixed(2)}`}${u}`);
  return {
    ...n,
    measured_now: now?.measured ?? null,
    sigma_now: now?.sigma ?? null,
    check_again: again,
    check_reason: again ? `check again: it was approved against ${s(n.measured, n.sigma)}, and it now measures ${s(now?.measured ?? null, now?.sigma ?? null)}. ${fmt(n.nominal)}${u} is more than twice the ± away.` : null,
  };
}

/** The limits of a typed value (as the engine's). */
const LIMITS: Record<NominalValueName, [number, number, boolean]> = {
  diameter: [0, 10_000, true],
  radius: [0, 5_000, true],
  angle: [0, 90, false],
  distance: [0, 10_000, false],
  centre_distance: [0, 10_000, false],
};

/** Why a nominal value makes no sense for `value`, or null when it does. */
export function badNominal(value: NominalValueName, x: unknown): string | null {
  const [lo, hi, open] = LIMITS[value];
  if (typeof x !== "number" || !Number.isFinite(x) || x > hi || x < lo || (open && x <= lo))
    return `a nominal ${value.replace("_", " ")} must be a number ${open ? "more than" : "at least"} ${lo} and at most ${hi} ${value === "angle" ? "deg" : "mm"}, got ${String(x)}`;
  return null;
}
