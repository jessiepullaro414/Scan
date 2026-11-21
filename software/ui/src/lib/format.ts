/**
 * Formatting helpers (numbers use tabular figures in the UI; see `.tnum`).
 *
 * UX_PLAN U32, one style for every number a user reads: a narrow no-break space (U+202F)
 * between a number and its unit ("29.7 fps", "453 mm", "12 %"), thousands grouped the same way
 * everywhere ("1,568"), sizes as "1280 × 800", and a true minus sign (−) for negative values.
 */

/** The narrow no-break space between a number and its unit. */
export const NNBSP = " ";
/** The true minus sign (U+2212). */
export const MINUS = "−";

const intFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const compactFmt = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

const finite = (n: number | null | undefined): n is number => n !== null && n !== undefined && Number.isFinite(n);
/** A hyphen-minus in front of a number becomes a true minus. */
const minus = (s: string) => s.replace(/^-/, MINUS);

export function fmtInt(n: number | null | undefined): string {
  if (!finite(n)) return "–";
  return minus(intFmt.format(n));
}

export function fmtCompact(n: number | null | undefined): string {
  if (!finite(n)) return "–";
  return minus(Math.abs(n) < 10000 ? intFmt.format(n) : compactFmt.format(n));
}

export function fmtFixed(n: number | null | undefined, digits = 1): string {
  if (!finite(n)) return "–";
  return minus(n.toFixed(digits));
}

/** A number with `digits` decimals, thousands grouped ("1,289.5"), a true minus; "–" when there is none. */
export function fmtNum(n: number | null | undefined, digits = 0): string {
  if (!finite(n)) return "–";
  const s = new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
  return minus(s === "-0" || /^-0\.0*$/.test(s) ? s.slice(1) : s);
}

/** A value and its unit with the narrow no-break space: "29.7 fps", "453 mm". */
export function withUnit(value: string, unit: string): string {
  return unit ? `${value}${NNBSP}${unit}` : value;
}

/** A number with its unit: `fmtUnit(29.66, "fps", 1)` → "29.7 fps"; "–" alone when there is no number. */
export function fmtUnit(n: number | null | undefined, unit: string, digits = 0): string {
  return finite(n) ? withUnit(fmtNum(n, digits), unit) : "–";
}

/** Millimetres: `fmtMm(453)` → "453 mm", `fmtMm(0.25, 2)` → "0.25 mm". */
export function fmtMm(n: number | null | undefined, digits = 0): string {
  return fmtUnit(n, "mm", digits);
}

/**
 * A mesh detail, one way everywhere (UX_PLAN R19: "0.50 mm" stood beside "0.5 mm"): one decimal, two only when the
 * value needs them: "1.0 mm", "0.5 mm", "0.25 mm".
 */
export function fmtDetailMm(n: number | null | undefined): string {
  if (!finite(n)) return "–";
  return fmtMm(n, Math.abs(Math.round(n * 10) - n * 10) > 1e-6 ? 2 : 1);
}

/** An image or sheet size: "1280 × 800" (with the unit when given: "210 × 297 mm"); sizes are not grouped. */
export function fmtSize(w: number, h: number, unit = ""): string {
  const side = (n: number) => (Number.isFinite(n) ? String(Math.round(n)) : "–");
  return withUnit(`${side(w)}${NNBSP}×${NNBSP}${side(h)}`, unit);
}

/** The distance the user reads in a sentence: rounded to 10 mm ("about 250 mm"); the engine's numbers stay as they are. */
export function fmtAboutMm(n: number | null | undefined): string {
  return finite(n) ? fmtMm(Math.round(n / 10) * 10) : "–";
}

export function fmtPct(x: number | null | undefined, digits = 0): string {
  if (!finite(x)) return "–";
  return withUnit(fmtFixed(x * 100, digits), "%");
}

/** Clock style: 0:07, 12:34, 1:02:03. */
export function fmtClock(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "0:00";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return `${h ? `${h}:` : ""}${mm}:${String(r).padStart(2, "0")}`;
}

/** Words: 27 s, 3 min 12 s, 1 h 4 min. */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "–";
  if (seconds < 60) return withUnit(seconds < 10 ? seconds.toFixed(1) : String(Math.round(seconds)), "s");
  const m = Math.floor(seconds / 60);
  if (m < 60) {
    const s = Math.round(seconds % 60);
    return s ? `${withUnit(String(m), "min")} ${withUnit(String(s), "s")}` : withUnit(String(m), "min");
  }
  const h = Math.floor(m / 60);
  return `${withUnit(String(h), "h")} ${withUnit(String(m % 60), "min")}`;
}

export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "–";
  if (n < 1024) return withUnit(String(n), "B");
  if (n < 1024 * 1024) return withUnit((n / 1024).toFixed(0), "KB");
  if (n < 1024 * 1024 * 1024) return withUnit((n / 1024 / 1024).toFixed(1), "MB");
  return withUnit((n / 1024 / 1024 / 1024).toFixed(2), "GB");
}

const dateFmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
const dateTimeFmt = new Intl.DateTimeFormat(undefined, {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });

function toDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function fmtDateTime(iso: string | null | undefined): string {
  const d = toDate(iso);
  return d ? dateTimeFmt.format(d) : "–";
}

/** "just now", "5 min ago", "3 h ago", "yesterday 14:02", "4 days ago", or a date. */
export function fmtRelative(iso: string | null | undefined, now = Date.now()): string {
  const d = toDate(iso);
  if (!d) return "–";
  const s = (now - d.getTime()) / 1000;
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}\u202fmin ago`;
  if (s < 6 * 3600) return `${Math.round(s / 3600)}\u202fh ago`;
  const today = new Date(now);
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  if (d.getTime() >= startOfToday) return `today ${timeFmt.format(d)}`;
  if (d.getTime() >= startOfToday - 86400_000) return `yesterday ${timeFmt.format(d)}`;
  const days = Math.floor((startOfToday - d.getTime()) / 86400_000) + 1;
  if (days < 7) return `${days} days ago`;
  return dateFmt.format(d);
}

/**
 * Keyboard keys as short ranges: [1..7] → ["1–7"], [1, 2, 3, 5, 6, 7] → ["1–3", "5–7"]; runs of three
 * or more become a range, shorter ones are listed (U25: the viewer said "1–6 shading" for 7 modes).
 */
export function keyRanges(keys: readonly number[]): string[] {
  const k = [...new Set(keys)].sort((a, b) => a - b);
  const out: string[] = [];
  for (let i = 0; i < k.length; ) {
    let j = i;
    while (j + 1 < k.length && k[j + 1] === k[j] + 1) j++;
    if (j - i >= 2) out.push(`${k[i]}–${k[j]}`);
    else for (let m = i; m <= j; m++) out.push(String(k[m]));
    i = j + 1;
  }
  return out;
}

/** Human labels for API identifiers: "image_size" → "Image size". */
export function humanize(key: string): string {
  const s = key.replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** A safe file-name stem. */
export function fileStem(...parts: string[]): string {
  return (
    parts
      .filter(Boolean)
      .join("-")
      .normalize("NFKD")
      .replace(/[^\w.-]+/g, "_")
      .replace(/_+/g, "_")
      .replace(/^[_.-]+|[_.-]+$/g, "")
      .slice(0, 80) || "scan"
  );
}
