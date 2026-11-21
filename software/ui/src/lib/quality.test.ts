/**
 * Checks for the qualities and the Edit mesh page's stage list (UX_PLAN U49, `npm test`): the qualities in the
 * order Preview, Standard, Best (Standard stays the default action); a trial stage says "Trial" in its title.
 * Plain TypeScript run by Node, no framework.
 */
import { DEFAULT_QUALITY, QUALITIES, normalizeQuality } from "./quality.ts";
import { STAGE_TITLES, stageTitle } from "./stages.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`quality.test: ${what}`);
}

check(QUALITIES.join(",") === "preview,standard,best", `Preview, Standard, Best (${QUALITIES.join(",")})`);
check(DEFAULT_QUALITY === "standard", "Standard is still the default");
check(normalizeQuality("full") === "standard", "the old name still reads");

check(stageTitle("track") === "Follow the camera" && stageTitle("straighten") === "Straighten" && stageTitle("fuse") === "Build the surface", "the three stages' names (U17)");
check(stageTitle("spacetime") === "Refine from the images (trial)", `a trial stage says so in its title: ${stageTitle("spacetime")}`);
check(STAGE_TITLES.spacetime.trial && !STAGE_TITLES.track.trial && !STAGE_TITLES.fuse.trial && !STAGE_TITLES.refine.trial, "only the trial build is marked");
check(stageTitle("refine") === "Refine with lasers", "Refine with lasers is not a trial");
check(stageTitle("spacetime", { short: true }) === "Refine from the images", "the short name, where the badge says Trial beside it");

console.log(`quality.test: ${checks} checks passed`);
