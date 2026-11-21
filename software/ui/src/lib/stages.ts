/**
 * The names of the processing stages on the Edit mesh page (UX_PLAN U17, U49): "Follow the camera / Straighten /
 * Build the surface", and the two refinements; a trial build says so in its title. Pure; `quality.test.ts` checks it.
 */
import type { StageId, StageState } from "../api/types.ts";
import { fmtInt } from "./format.ts";

/**
 * Whether a stage card of the scan's own mesh says "Running" (UX_PLAN R22): from its own state only (a stage run
 * alone), never from the job's kind or message. During "Make mesh again" the scan's stages stay as they are until the
 * new mesh takes their place (APP_API.md, R2); the run being built is shown beside them from `ProcessState.making`
 * (lib/making.ts `makingRows`), one stage running at a time.
 */
export function stageRunning(state: StageState): boolean {
  return state === "running";
}

/**
 * Straighten's line under a "Skipped" badge (R22): "Closed 48 loops" belongs to a straightened scan. Skipped with loops
 * found means they showed the passes already lined up (APP_API: `changed: false`); with the engine's own sentence
 * (`summary`) that one says why, so nothing more. Null where the card shows its usual lines.
 */
export function straightenSkippedLine(s: { state: StageState; loops: number | null; summary: string | null }): string | null {
  if (s.state !== "skipped" || !s.loops || s.loops <= 0 || s.summary) return null;
  return `Found ${fmtInt(s.loops)} loop${s.loops === 1 ? "" : "s"}, and they show the passes already line up: the scan was left as it is.`;
}

export const STAGE_TITLES: Record<StageId, { label: string; trial: boolean }> = {
  track: { label: "Follow the camera", trial: false },
  straighten: { label: "Straighten", trial: false },
  fuse: { label: "Build the surface", trial: false },
  spacetime: { label: "Refine from the images", trial: true },
  refine: { label: "Refine with lasers", trial: false },
};

/** "Refine from the images (trial)"; `short`: the name alone (where a "Trial" badge stands beside it). */
export function stageTitle(id: StageId, o: { short?: boolean } = {}): string {
  const t = STAGE_TITLES[id];
  if (!t) return String(id);
  return t.trial && !o.short ? `${t.label} (trial)` : t.label;
}
