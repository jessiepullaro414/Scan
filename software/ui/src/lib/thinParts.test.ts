/** Checks for the thin-parts hint (ENGINE_PLAN X5 step 1, APP_API.md "The thin-parts hint"; `npm test`). */
import type { MadeInfo } from "../api/types";
import { THIN_PRESET_ID, demoThinPartsHint, thinPartsToShow } from "./thinParts.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`check failed: ${what}`);
}

const made = (thin_parts: MadeInfo["thin_parts"]): MadeInfo => ({ job_id: "j", kind: "process_scan", when: "", seconds: 190, stages: {}, thin_parts });
const hint = demoThinPartsHint(190, 1_234_567, 1, false);

check(thinPartsToShow(made(hint)) === hint, "a hint to suggest is shown");
check(thinPartsToShow(made({ ...hint, dismissed: true })) === null, "put away: not shown");
check(thinPartsToShow(made({ ...hint, suggest: false, why_not: "made_fine", words: null })) === null, "made fine already: nothing");
check(thinPartsToShow(made({ ...hint, suggest: false, why_not: "nothing_found", words: null })) === null, "nothing found: nothing");
check(thinPartsToShow(made({ ...hint, suggest: false, why_not: "no_measured_gain", found: ["plates", "holes"], words: null })) === null, "a plate or holes alone: no hint");
check(hint.found.includes("walls") && hint.words!.title === "Thin walls found", "the hint is about the walls");
check(thinPartsToShow(made(undefined)) === null && thinPartsToShow(null) === null, "an older engine or no Make mesh: nothing");
check(hint.preset_id === THIN_PRESET_ID && hint.words!.preset_id === THIN_PRESET_ID, "the action names the thin preset");

// the words: plain, with units, thousands separated, no program words
const text = [hint.words!.intro, ...hint.words!.found, ...hint.words!.gain, ...hint.words!.cost, hint.words!.measured].join(" ");
check(/\b1,794 mm²/.test(text), "thousands separated, with the unit");
check(text.includes("3.2 min") && text.includes("3.8 min to 7.6 min"), "this mesh's time and the preset's");
check(text.includes("4.9 million instead of 1.23 million"), "the triangles");
for (const w of [/\bGPU\b/, /\bCPU\b/, /\bvoxels?\b/i, /\bsigma\b/i, /\bremake\b/i]) check(!w.test(text), `no ${w}`);
check(hint.words!.action === "Make the mesh again with this preset", "one action, in words");

console.log(`thinParts.test: ${checks} checks passed`);
