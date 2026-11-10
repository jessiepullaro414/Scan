/**
 * The levels of the final mesh (APP_API.md): Standard (the default), Best, Preview.
 * Standard processes 30 frames per second of scanning (every 2nd frame of the 60 fps camera), Best every frame.
 * Older engines and stored links say "full", the old name of Standard.
 */
import type { ProcessQuality } from "../api/types";

/** In the order the UI offers them: Standard is the default action, then Best, then Preview. */
export const QUALITIES: ProcessQuality[] = ["standard", "best", "preview"];

export const QUALITY_TEXT: Record<ProcessQuality, { label: string; short: string; help: string }> = {
  standard: { label: "Standard quality", short: "Standard", help: "Full resolution, 30 frames a second" },
  best: { label: "Best quality", short: "Best", help: "Every frame: less noise on smooth surfaces, about twice the wait" },
  preview: { label: "Preview quality", short: "Preview", help: "Fast check, coarser mesh" },
};

/** A quality from the server, a stored link or a recipe: "full" (the old name) is Standard; unknown values are null. */
export function normalizeQuality(q: unknown): ProcessQuality | null {
  if (q === "full") return "standard";
  return q === "standard" || q === "best" || q === "preview" ? q : null;
}

/** "standard quality", "best quality", "preview quality" (lower case, for running text). */
export function qualityName(q: unknown): string | null {
  const n = normalizeQuality(q);
  return n ? QUALITY_TEXT[n].label.toLowerCase() : null;
}
