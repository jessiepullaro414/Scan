/**
 * The names of the processing stages on the Edit mesh page (UX_PLAN U17, U49): "Follow the camera / Straighten /
 * Build the surface", and the two refinements; a trial build says so in its title. Pure; `quality.test.ts` checks it.
 */
import type { StageId } from "../api/types.ts";

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
