/**
 * The camera path's warning (ENGINE_PLAN X0a; APP_API.md "The camera path's warning"): the engine checks a tracked
 * scan's camera path for a jump no hand makes, and when it finds one says that tracking may have slid there (a thin
 * part can then come out thinner or partly missing). The app warns, it does not block. Read from a processing report:
 * the scan's card (`Scan.result.report`, already in the app's words) or `report.json` (the engine's words). A scan
 * processed before the check has no `warnings` and nothing is shown.
 */

/**
 * The app says "graphics card" (UX_PLAN U5). The engine says so too since 2026-11-21 (UX_PLAN U51), but the report of a
 * scan processed before then keeps its "GPU": this turns those into the app's words.
 */
const GPU_WORDS: [RegExp, string][] = [
  [/\bwithout a GPU\b/g, "without a graphics card"],
  [/\ba GPU\b/g, "a graphics card"],
  [/\bthe GPU\b/g, "the graphics card"],
  [/\bGPU\b/g, "graphics card"],
];

function asSentence(s: string): string {
  let t = s.replace(/\s+/g, " ").trim();
  if (!t) return "";
  if (/^[a-z]/.test(t)) t = t[0].toUpperCase() + t.slice(1);
  return /[.!?:…]["'”’)]*$/.test(t) ? t : `${t}.`;
}

/** The report's warnings as sentences in the app's words; [] for an older scan, a path that held, or no report. */
export function scanWarnings(report: unknown): string[] {
  if (!report || typeof report !== "object") return [];
  const ws = (report as { warnings?: unknown }).warnings;
  if (!Array.isArray(ws)) return [];
  return ws
    .filter((w): w is string => typeof w === "string" && w.trim() !== "")
    .map((w) => asSentence(GPU_WORDS.reduce((t, [rx, word]) => t.replace(rx, word), w)));
}

/**
 * The camera path's own sentence (`ohs/track/path_check.py`: "The camera path jumps by … tracking may have slid
 * there."). Every other sentence of a report's `warnings` (a merge's notes about its table, for one) is not about the
 * path (UX_PLAN R1).
 */
const PATH_SENTENCE = /tracking may have slid|^the camera path jumps/i;

/** The report's warnings in two: the camera path's own (`path`), and the rest (`other`, for a merge its notes). */
export function splitWarnings(report: unknown): { path: string[]; other: string[] } {
  const said = scanWarnings(report);
  return { path: said.filter((w) => PATH_SENTENCE.test(w)), other: said.filter((w) => !PATH_SENTENCE.test(w)) };
}

/** The camera path's warning title, on the row's chip, in the viewer and on Edit mesh. */
export const PATH_WARNING_TITLE = "Tracking may have slid";

/** The scan row's short note (the sentences on hover, as the calibration status note); null when there is none. */
export function pathWarningChip(report: unknown): { text: string; title: string } | null {
  const said = splitWarnings(report).path;
  return said.length ? { text: PATH_WARNING_TITLE, title: said.join(" ") } : null;
}

/**
 * The title over the report's other notes. A merge's notes say what it left out and what it kept (a raised area that
 * may be a thin part); the scan's summary has them as one list, without the server's grouping, so the title fits both.
 */
export function notesTitle(merged: boolean): string {
  return merged ? "Notes from the merge" : "Notes on making the mesh";
}

/** The scan row's chip for the report's other notes (a merged scan's: what the merge left out); null when none. */
export function notesChip(report: unknown, merged: boolean): { text: string; title: string } | null {
  const said = splitWarnings(report).other;
  return said.length ? { text: notesTitle(merged), title: said.join(" ") } : null;
}
