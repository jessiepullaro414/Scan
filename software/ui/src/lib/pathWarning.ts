/**
 * The camera path's warning (ENGINE_PLAN X0a; APP_API.md "The camera path's warning"): the engine checks a tracked
 * scan's camera path for a jump no hand makes, and when it finds one says that tracking may have slid there (a thin
 * part can then come out thinner or partly missing). The app warns, it does not block. Read from a processing report:
 * the scan's card (`Scan.result.report`, already in the app's words) or `report.json` (the engine's words). A scan
 * processed before the check has no `warnings` and nothing is shown.
 */

/** The engine says "GPU"; the app says "graphics card" (UX_PLAN U5). */
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

/** The scan row's short note (the sentences on hover, as the calibration status note); null when there is none. */
export function pathWarningChip(report: unknown): { text: string; title: string } | null {
  const said = scanWarnings(report);
  return said.length ? { text: "Tracking may have slid", title: said.join(" ") } : null;
}
