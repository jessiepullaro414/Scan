/** Checks for the thin-parts hint (ENGINE_PLAN X5 step 1, APP_API.md "The thin-parts hint"; `npm test`). */
import type { MadeInfo } from "../api/types";
import { THIN_PRESET_ID, demoThinPartsHint, fineWallsNote, hintChoices, thinPartsToShow } from "./thinParts.ts";

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

// ENGINE_PLAN X5 step 2: "Make only the thin walls finer", first, where the engine offers it
check(hintChoices(hint).map((c) => c.kind).join() === "preset" && hintChoices(hint)[0].primary, "without the offer: the preset alone, as before");
const offered = demoThinPartsHint(45, 900_000, 1, false, 117);
const choices = hintChoices(offered);
check(choices.map((c) => c.kind).join() === "fine,preset", "with it: two choices, the finer walls first");
check(choices[0].primary && !choices[1].primary, "the finer walls are the primary choice");
check(choices[0].label === "Make only the thin walls finer" && choices[1].label === "Make the mesh again with this preset", "their words");
const fineText = [offered.words!.fine!.intro, ...offered.words!.fine!.gain, ...offered.words!.fine!.cost].join(" ");
check(fineText.includes("0.04 to 0.52 mm nearer") && fineText.includes("about 1.2 to 1.3 times") && fineText.includes("117 frames"), "its measured gain and cost");
for (const w of [/\bGPU\b/, /\bCPU\b/, /\bvoxels?\b/i, /\bsigma\b/i]) check(!w.test(fineText), `no ${w} in the finer walls' words`);
check(hintChoices(demoThinPartsHint(45, 900_000, 2, false, 117)).length === 1, "at another mesh detail the demo offers the preset alone");
check(thinPartsToShow(made({ ...offered, suggest: false, why_not: "walls_made_finer", words: null })) === null, "walls made finer: no hint");

// after a Make mesh with the choice on: the note in the hint's place
const withFine = (fine_walls: MadeInfo["fine_walls"]): MadeInfo => ({ ...made(null), fine_walls });
check(fineWallsNote(made(hint)) === null && fineWallsNote(null) === null, "not chosen (or an older engine): no note");
const did = fineWallsNote(
  withFine({ chosen: true, acted: true, why: null, share: 0.028, regions: 3, seconds: 6.1, kept_mb: 914, line: "The thin walls were made finer: 2.8 % of the surface on 3 walls.", action: "Make the mesh again without this" }),
);
check(!!did && did.acted && did.line.startsWith("The thin walls were made finer") && did.action === "Make the mesh again without this", "it acted: what it did, one action");
const not = fineWallsNote(withFine({ chosen: true, acted: false, why: "no_thin_walls", share: null, regions: null, seconds: 1.2, kept_mb: 914, line: "No thin walls were found." }));
check(!!not && !not.acted && not.action === null && not.line === "No thin walls were found.", "it did not act: why, in one line, no action");
check(fineWallsNote(withFine({ chosen: true, acted: false, why: "disk", share: null, regions: null, seconds: null, kept_mb: null, line: "" })) === null, "no line: nothing to show");
// ENGINE_PLAN R2-5: the finer model ran out of room: said under the line; none (or an older server): nothing more
check(did!.warnings.length === 0 && not!.warnings.length === 0, "no warnings from an older server: none shown");
const room = "The finer model for the thin walls ran out of room after frame 7: finer surface seen after that was not added";
const full = fineWallsNote(
  withFine({ chosen: true, acted: true, why: null, share: 0.028, regions: 3, seconds: 6.1, kept_mb: 914, line: "The thin walls were made finer.", action: "Make the mesh again without this", warnings: [room, "", "a GPU fell back"] }),
);
check(!!full && full.warnings.length === 2 && full.warnings[0] === `${room}.` && full.warnings[1] === "A graphics card fell back.", "its warnings, as sentences in the app's words");

// a time range whose ends read the same says one number, as the engine (thin_parts._minutes_range)
const short = demoThinPartsHint(8.8, 250_000, 1, false, 117).words!.fine!.cost[0];
check(short.endsWith("This mesh took 9 s; with it about 11 s.") && !/11 s to 11 s/.test(short), short);
check(demoThinPartsHint(45, 900_000, 1, false, 117).words!.fine!.cost[0].includes("with it about 54 s to 58 s"), "a real range keeps both ends");
check(demoThinPartsHint(700, 900_000, 2, false).words!.cost[0].includes("This mesh took 12 min; with the preset about 14 min to 28 min"), "ten minutes and more: whole minutes, as the engine");

console.log(`thinParts.test: ${checks} checks passed`);
