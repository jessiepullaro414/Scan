/** Formatting helpers (numbers use tabular figures in the UI; see `.tnum`). */

const intFmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
const compactFmt = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });

export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "–";
  return intFmt.format(n);
}

export function fmtCompact(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "–";
  return Math.abs(n) < 10000 ? intFmt.format(n) : compactFmt.format(n);
}

export function fmtFixed(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "–";
  return n.toFixed(digits);
}

export function fmtMm(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "–";
  return `${n.toFixed(digits)} mm`;
}

export function fmtPct(x: number | null | undefined, digits = 0): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return "–";
  return `${(x * 100).toFixed(digits)} %`;
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
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`;
  const m = Math.floor(seconds / 60);
  if (m < 60) {
    const s = Math.round(seconds % 60);
    return s ? `${m} min ${s} s` : `${m} min`;
  }
  const h = Math.floor(m / 60);
  return `${h} h ${m % 60} min`;
}

export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "–";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
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

export function fmtDate(iso: string | null | undefined): string {
  const d = toDate(iso);
  return d ? dateFmt.format(d) : "–";
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
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`;
  if (s < 6 * 3600) return `${Math.round(s / 3600)} h ago`;
  const today = new Date(now);
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  if (d.getTime() >= startOfToday) return `today ${timeFmt.format(d)}`;
  if (d.getTime() >= startOfToday - 86400_000) return `yesterday ${timeFmt.format(d)}`;
  const days = Math.floor((startOfToday - d.getTime()) / 86400_000) + 1;
  if (days < 7) return `${days} days ago`;
  return dateFmt.format(d);
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
