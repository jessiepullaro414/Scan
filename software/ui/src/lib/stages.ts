/**
 * The names of the processing stages on the Edit mesh page (UX_PLAN U17, U49): "Follow the camera / Straighten /
 * Build the surface", and the two refinements; a trial build says so in its title. Pure; `quality.test.ts` checks it.
 */
import type { StageId, StageState } from "../api/types.ts";
import { fmtInt } from "./format.ts";

/**
 * Whether a stage card says "Running" (UX_PLAN R22): its own state says so, or the run "Make mesh again" is building
 * says so for it (`ProcessState.making`). A finished stage whose `job_id` is the whole run's job is not running: that
 * made "Follow the camera" (done) and "Straighten" both "Running" at once.
 */
export function stageRunning(state: StageState, making?: { state: StageState } | null): boolean {
  return state === "running" || making?.state === "running";
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
