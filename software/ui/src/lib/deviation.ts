/**
 * The deviation map (APP_API.md "M5 second slice: the deviation map"), the pure part: the colour
 * scale, the colours of a value array at a limit, the share within the limit, and the words for a
 * value. The array holds one signed move in mm per vertex (+ outward, - inward, NaN = new surface
 * with nothing to compare).
 *
 * The scale is diverging: blue = moved inward, grey = unchanged, orange-red = moved outward,
 * saturated at plus and minus the limit. Blue against orange stays apart for red-green colour-blind
 * viewers (no green anywhere); each side also gets lighter towards the grey, so the size of a move
 * reads in lightness as well as in hue. Each theme has its own steps.
 *
 * Imports nothing at run time, so `npm test` checks it with plain node.
 */

export type DeviationTheme = "light" | "dark";

/** What a person can choose as the scale's end (mm). */
export const DEVIATION_LIMITS = [0.05, 0.1, 0.2, 0.5, 1] as const;
/** 0.05 mm: at 0.1 the map hides most of the edge rounding smoothing does (VALIDATION.md, "The deviation map against the truth"). */
export const DEFAULT_DEVIATION_LIMIT = 0.05;

/** Inward end to outward end (sRGB hex): five steps with the neutral grey in the middle. */
export const DEVIATION_RAMPS: Record<DeviationTheme, string[]> = {
  light: ["#1b5cc0", "#6b9be0", "#c4c3c0", "#ee9972", "#c0401a"],
  dark: ["#2f78ee", "#80a9ea", "#d4d5d7", "#f2a07a", "#e2552a"],
};

/** The limit if it is one of the choices, else the default (a stored value may be anything). */
export function validLimit(x: unknown): number {
  return typeof x === "number" && (DEVIATION_LIMITS as readonly number[]).includes(x) ? x : DEFAULT_DEVIATION_LIMIT;
}

const rgb = (h: string): [number, number, number] => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];

/** The scale's colour at `t`: 0 = the inward end (-limit), 0.5 = unchanged, 1 = the outward end (+limit). sRGB bytes. */
export function deviationRgb(theme: DeviationTheme, t: number): [number, number, number] {
  const stops = DEVIATION_RAMPS[theme].map(rgb);
  const x = Math.min(Math.max(Number.isFinite(t) ? t : 0.5, 0), 1) * (stops.length - 1);
  const i = Math.min(Math.floor(x), stops.length - 2);
  const f = x - i;
  return [0, 1, 2].map((c) => Math.round(stops[i][c] + (stops[i + 1][c] - stops[i][c]) * f)) as [number, number, number];
}

/** The same as a hex string. */
export function deviationHex(theme: DeviationTheme, t: number): string {
  return `#${deviationRgb(theme, t)
    .map((v) => v.toString(16).padStart(2, "0"))
    .join("")}`;
}

/** A CSS gradient of the scale (left: inward, right: outward). */
export function deviationCss(theme: DeviationTheme): string {
  return `linear-gradient(90deg, ${DEVIATION_RAMPS[theme].join(", ")})`;
}

/** Steps of the colour table: a value between -limit and +limit is mapped onto this many. */
export const LUT_SIZE = 256;

/** The scale as a table of sRGB bytes (3 per step), the first step at -limit, the last at +limit. */
export function deviationLut(theme: DeviationTheme): Uint8Array {
  const lut = new Uint8Array(LUT_SIZE * 3);
  for (let k = 0; k < LUT_SIZE; k++) lut.set(deviationRgb(theme, k / (LUT_SIZE - 1)), k * 3);
  return lut;
}

/**
 * RGBA vertex colours of `values` at `limit`, written into `out` (4 bytes per vertex; made when not
 * given). `toLinear` converts an sRGB byte to the byte the geometry stores (the renderer reads vertex
 * colours as linear); `none` is the sRGB colour of a vertex with no value, whose alpha is 0 (the
 * surface material draws those striped, like the confidence map's "not measured").
 */
export function deviationColors(
  values: Float32Array,
  limit: number,
  theme: DeviationTheme,
  toLinear: ArrayLike<number>,
  none: string,
  out: Uint8Array = new Uint8Array(values.length * 4),
): Uint8Array {
  const lut = deviationLut(theme);
  const lin = new Uint8Array(lut.length);
  for (let i = 0; i < lut.length; i++) lin[i] = toLinear[lut[i]];
  const nc = rgb(none).map((v) => toLinear[v]);
  const scale = (LUT_SIZE - 1) / (2 * limit);
  for (let i = 0, o = 0; i < values.length; i++, o += 4) {
    const v = values[i];
    if (Number.isNaN(v)) {
      out[o] = nc[0];
      out[o + 1] = nc[1];
      out[o + 2] = nc[2];
      out[o + 3] = 0;
      continue;
    }
    const k = Math.min(LUT_SIZE - 1, Math.max(0, Math.round((v + limit) * scale))) * 3;
    out[o] = lin[k];
    out[o + 1] = lin[k + 1];
    out[o + 2] = lin[k + 2];
    out[o + 3] = 255;
  }
  return out;
}

/** The share of the compared vertices (not NaN) whose move is within plus or minus `limit`, or null when none was compared. */
export function shareWithin(values: Float32Array, limit: number): number | null {
  const lim = Math.fround(limit); // the values are float32: a move of exactly the limit counts as within
  let n = 0;
  let within = 0;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (Number.isNaN(v)) continue;
    n++;
    if (Math.abs(v) <= lim) within++;
  }
  return n ? within / n : null;
}

/** The share of all vertices that is new surface (NaN), 0..1. */
export function newShare(values: Float32Array): number {
  let k = 0;
  for (let i = 0; i < values.length; i++) if (Number.isNaN(values[i])) k++;
  return values.length ? k / values.length : 0;
}

/** A length in mm for the legend: "0.05", "0.1", "1". */
export function fmtLimit(mm: number): string {
  return String(Number(mm.toFixed(3)));
}

/** A move for the legend's numbers: "0.034 mm" (more digits for the small ones). */
export function fmtMove(mm: number | null | undefined): string {
  if (mm === null || mm === undefined || !Number.isFinite(mm)) return "–";
  const a = Math.abs(mm);
  return `${a < 0.1 ? a.toFixed(3) : a < 10 ? a.toFixed(2) : a.toFixed(1)} mm`;
}

const MINUS = "−";

/**
 * What the cursor is over, in words: "+0.034 mm (outward)", "-0.12 mm (inward)" (with a true minus
 * sign), "0.000 mm (unchanged)" (under half a unit of the last digit) or "new surface" (null or NaN:
 * nothing to compare). Three decimals under 0.1 mm, two under 10 mm.
 */
export function deviationReadout(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "new surface";
  const a = Math.abs(v);
  const digits = a < 0.1 ? 3 : a < 10 ? 2 : 1;
  const text = a.toFixed(digits);
  if (Number(text) === 0) return `${(0).toFixed(digits)} mm (unchanged)`;
  return v > 0 ? `+${text} mm (outward)` : `${MINUS}${text} mm (inward)`;
}

/**
 * The value under a point of a triangle: the corners' values weighted by the barycentric weights
 * `w` (negative ones count as 0), the corners without a value left out. null when the corner nearest
 * to the point has none ("new surface").
 */
export function interpolateMove(values: ArrayLike<number>, a: number, b: number, c: number, w: ArrayLike<number>): number | null {
  const v = [values[a], values[b], values[c]];
  const near = w[0] >= w[1] && w[0] >= w[2] ? 0 : w[1] >= w[2] ? 1 : 2;
  if (Number.isNaN(v[near])) return null;
  let sum = 0;
  let ws = 0;
  for (let k = 0; k < 3; k++) {
    if (Number.isNaN(v[k])) continue;
    const wk = Math.max(w[k], 0);
    sum += wk * v[k];
    ws += wk;
  }
  return ws > 0 ? sum / ws : v[near];
}
