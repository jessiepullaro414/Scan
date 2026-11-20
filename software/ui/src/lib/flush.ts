/**
 * The flush along a seam as the viewer shows it (FEATURE_PLAN.md I4; APP_API.md "Flush along a seam"): the small
 * chart's scaling (the reading with its ± band, the mean as a line, the stations that are not trusted marked), the
 * colour of each station on the model, and the numbers in words: the mean first (the number to trust), then the lowest
 * and the highest and where they are, and which panel stands proud. Plain functions of the engine's record, so they
 * are tested on their own (`flush.test.ts`).
 */
import type { FlushRecord, FlushStation } from "../api/types";

/** A mean flush within this of zero reads as "level" (the engine's mean is within 0.04 mm of the truth on simulated seams). */
export const LEVEL_MM = 0.05;
/** The chart's height range is never narrower than this (mm), so a seam that is level does not show its noise as a cliff. */
export const MIN_SPAN_MM = 0.2;

export type ChartBox = { width: number; height: number; left: number; right: number; top: number; bottom: number };
export type ChartPoint = {
  i: number;
  x: number; // px
  y: number | null; // px; null: no reading
  lo: number | null; // the ± band's ends, px (y grows downwards: lo is the lower edge, a larger number)
  hi: number | null;
  trusted: boolean;
  clipped: boolean; // the reading lies outside the chart's range and is drawn at its edge
};
export type FlushChart = {
  xRange: [number, number]; // mm along the seam
  yRange: [number, number]; // mm of flush
  xTicks: number[];
  yTicks: number[];
  x: (mm: number) => number;
  y: (mm: number) => number;
  points: ChartPoint[];
  mean: number | null; // px of the mean line, null without one
  zero: number | null; // px of flush 0, when it is in range
};

/** About `n` round tick values covering [lo, hi] (1, 2 or 5 times a power of ten apart). */
export function niceTicks(lo: number, hi: number, n = 4): number[] {
  if (!(Number.isFinite(lo) && Number.isFinite(hi)) || hi <= lo) return [lo];
  const raw = (hi - lo) / Math.max(1, n);
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 5, 10].map((m) => m * p).find((s) => s >= raw) ?? 10 * p;
  const out: number[] = [];
  for (let t = Math.ceil(lo / step - 1e-9) * step; t <= hi + 1e-9 * step; t += step) out.push(Math.abs(t) < step * 1e-9 ? 0 : t);
  return out;
}

/** The trusted stations' readings with their ± (the ones the chart's range is made for). */
function trustedSpan(st: FlushStation[]): [number, number] | null {
  let lo = Infinity,
    hi = -Infinity;
  for (const s of st) {
    if (!s.flush_trusted || s.flush_mm === null) continue;
    const sg = s.flush_sigma_mm ?? 0;
    lo = Math.min(lo, s.flush_mm - sg);
    hi = Math.max(hi, s.flush_mm + sg);
  }
  return lo <= hi ? [lo, hi] : null;
}

/**
 * The chart of a flush record in a box of `box` pixels: x along the seam (all stations), y the flush. The height range
 * holds every trusted reading with its ± band and the mean, at least MIN_SPAN_MM, with a little room; a reading that is
 * not trusted and lies outside it is drawn at the edge (`clipped`) rather than stretching the chart.
 */
export function flushChart(rec: Pick<FlushRecord, "stations" | "flush">, box: ChartBox): FlushChart {
  const st = rec.stations;
  const xs = st.map((s) => s.position_mm);
  let x0 = xs.length ? Math.min(...xs) : 0,
    x1 = xs.length ? Math.max(...xs) : 1;
  if (x1 - x0 < 1e-9) {
    x0 -= 1;
    x1 += 1;
  }
  let span = trustedSpan(st);
  if (!span) {
    const all = st.filter((s) => s.flush_mm !== null).map((s) => s.flush_mm as number);
    span = all.length ? [Math.min(...all), Math.max(...all)] : [-0.1, 0.1];
  }
  const mean = rec.flush.mean;
  let [y0, y1] = span;
  if (mean !== null) {
    y0 = Math.min(y0, mean);
    y1 = Math.max(y1, mean);
  }
  const pad = 0.08 * Math.max(y1 - y0, 1e-9);
  y0 -= pad;
  y1 += pad;
  if (y1 - y0 < MIN_SPAN_MM) {
    const c = (y0 + y1) / 2;
    y0 = c - MIN_SPAN_MM / 2;
    y1 = c + MIN_SPAN_MM / 2;
  }
  const w = box.width - box.left - box.right,
    h = box.height - box.top - box.bottom;
  const x = (mm: number) => box.left + ((mm - x0) / (x1 - x0)) * w;
  const y = (mm: number) => box.top + (1 - (mm - y0) / (y1 - y0)) * h;
  const clampY = (mm: number) => Math.min(Math.max(mm, y0), y1);
  const points: ChartPoint[] = st.map((s, i) => {
    if (s.flush_mm === null) return { i, x: x(s.position_mm), y: null, lo: null, hi: null, trusted: s.flush_trusted, clipped: false };
    const v = s.flush_mm;
    const sg = s.flush_sigma_mm;
    return {
      i,
      x: x(s.position_mm),
      y: y(clampY(v)),
      lo: sg === null ? null : y(clampY(v - sg)),
      hi: sg === null ? null : y(clampY(v + sg)),
      trusted: s.flush_trusted,
      clipped: v < y0 || v > y1,
    };
  });
  return {
    xRange: [x0, x1],
    yRange: [y0, y1],
    xTicks: niceTicks(x0, x1, 4),
    yTicks: niceTicks(y0, y1, 3),
    x,
    y,
    points,
    mean: mean === null ? null : y(mean),
    zero: y0 <= 0 && 0 <= y1 ? y(0) : null,
  };
}

/** A station's colour on the model (r, g, b in 0..1): its flush across [lo, hi] from blue through white to orange;
 * a station whose flush is not trusted (or has none) is grey. */
export function flushColor(v: number | null, trusted: boolean, lo: number, hi: number): [number, number, number] {
  if (v === null || !trusted) return [0.55, 0.55, 0.55];
  const t = hi > lo ? Math.min(1, Math.max(0, (v - lo) / (hi - lo))) : 0.5;
  const blue: [number, number, number] = [0.2, 0.45, 0.95],
    mid: [number, number, number] = [0.95, 0.95, 0.95],
    orange: [number, number, number] = [0.98, 0.5, 0.12];
  const [a, b, u] = t < 0.5 ? [blue, mid, t * 2] : [mid, orange, (t - 0.5) * 2];
  return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u];
}

/** The median ± of the trusted stations (each station's 1σ), or null. */
export function typicalSigma(rec: Pick<FlushRecord, "stations">): number | null {
  const s = rec.stations.filter((x) => x.flush_trusted && x.flush_sigma_mm !== null).map((x) => x.flush_sigma_mm as number);
  if (!s.length) return null;
  s.sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

/** Decimals for a flush with this ± (the app's rule: as many as the ±; two without one). */
export function flushDecimals(sigma: number | null): number {
  if (sigma === null || !Number.isFinite(sigma)) return 2;
  const a = Math.abs(sigma);
  return a >= 1 ? 1 : a >= 0.01 ? 2 : a >= 0.001 ? 3 : 4;
}

const signed = (x: number, d: number) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(d)}`;

export type FlushWords = {
  /** The mean flush, signed, to the ±'s decimals ("+0.84"), or null when no station is trusted. */
  mean: string | null;
  /** Each station's typical ± ("0.03"), or null. */
  sigma: string | null;
  lowest: string | null;
  lowestAt: string | null; // "at −12 mm along the seam"
  highest: string | null;
  highestAt: string | null;
  /** Which panel stands proud, in words. */
  proud: string | null;
  /** "38 of 41 stations trusted". */
  trusted: string;
};

/** The flush in words (module docstring). Panel A is the first click's, panel B the second's; + : B stands above A. */
export function flushWords(rec: Pick<FlushRecord, "stations" | "flush">): FlushWords {
  const f = rec.flush;
  const sg = typicalSigma(rec);
  const d = flushDecimals(sg);
  const at = (mm: number | null) => (mm === null ? null : `at ${signed(Math.round(mm), 0) || "0"}\u202fmm along the seam`);
  let proud: string | null = null;
  if (f.mean !== null) {
    const m = Math.abs(f.mean).toFixed(d);
    if (Math.abs(f.mean) < LEVEL_MM) proud = `The two panels are level: on average within ${LEVEL_MM}\u202fmm of each other.`;
    else if (f.mean > 0) proud = `Panel B (your second click) stands proud of panel A by ${m}\u202fmm on average.`;
    else proud = `Panel A (your first click) stands proud of panel B by ${m}\u202fmm on average.`;
  }
  return {
    mean: f.mean === null ? null : signed(f.mean, d),
    sigma: sg === null ? null : sg.toFixed(d),
    lowest: f.lowest === null ? null : signed(f.lowest, d),
    lowestAt: at(f.lowest_at_mm),
    highest: f.highest === null ? null : signed(f.highest, d),
    highestAt: at(f.highest_at_mm),
    proud,
    trusted: `${f.n_trusted} of ${f.n_stations} station${f.n_stations === 1 ? "" : "s"} trusted`,
  };
}
