/** Checks for the camera path's warning (ENGINE_PLAN X0a, APP_API.md "The camera path's warning"; `npm test`). */
import { notesChip, notesTitle, pathWarningChip, scanWarnings, splitWarnings } from "./pathWarning.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`check failed: ${what}`);
}

const ENGINE =
  "The camera path jumps by 37 mm near frame 4, more than a hand moves in 0.8 s: tracking may have slid there. This scan was processed without a GPU, where tracking holds flat, plain scenes less well.";

// the scan's card (the app's words already) and report.json (the engine's): the same sentences, "graphics card" only
const said = scanWarnings({ warnings: [ENGINE] });
check(said.length === 1, "one sentence");
check(said[0].includes("without a graphics card") && !said[0].includes("GPU"), said[0]);
check(scanWarnings({ warnings: ["the path jumps", "", 3, null] })[0] === "The path jumps.", "a sentence, empties left out");

// an older scan (no fields), a path that held, nothing at all
check(scanWarnings({ frames: { processed: 10 } }).length === 0, "an older scan shows nothing");
check(scanWarnings({ warnings: [], path_check: { state: "ok" } }).length === 0, "a path that held says nothing");
check(scanWarnings(null).length === 0 && scanWarnings("x").length === 0 && scanWarnings({ warnings: "x" }).length === 0, "not a report");

// the project page's chip: short words, the sentences on hover (as the calibration status note)
const chip = pathWarningChip({ warnings: [ENGINE] });
check(chip !== null && chip.text === "Tracking may have slid", JSON.stringify(chip));
check(!!chip && chip.title.includes("near frame 4") && chip.title.includes("graphics card"), "the sentence on hover");
check(pathWarningChip({ frames: {} }) === null, "nothing for an older scan");
check(pathWarningChip({ warnings: [] }) === null, "nothing when the path held");

// UX_PLAN R1: only the camera path's own sentence is "Tracking may have slid"; a merge's notes have their own title
const MERGE = "70 raised areas up to 0.59 mm high were left out where the two scans' tables met.";
const both = splitWarnings({ warnings: [MERGE, ENGINE], path_check: { state: "warn", jump_mm: 37, frame: 4 } });
check(both.path.length === 1 && both.path[0].startsWith("The camera path jumps"), `the path's sentence: ${both.path.join("|")}`);
check(both.other.length === 1 && both.other[0] === MERGE, `the merge's note stays, apart: ${both.other.join("|")}`);
check(pathWarningChip({ warnings: [MERGE] }) === null, "a merge's notes alone: no “Tracking may have slid” chip");
const onlyMerge = splitWarnings({ warnings: [MERGE] });
check(onlyMerge.path.length === 0 && onlyMerge.other.length === 1, "a merge's notes are not lost");
const mixed = pathWarningChip({ warnings: [MERGE, ENGINE] });
check(!!mixed && !mixed.title.includes("raised areas"), `the path chip's hover holds the path's sentence only: ${mixed?.title}`);
const notes = notesChip({ warnings: [MERGE, ENGINE] }, true);
// the merge's notes also say what it kept ("1 raised area that may be a thin part is kept too"), and the scan's
// summary has them as one list without the server's grouping: a title that fits both (UX_PLAN, noticed with R1)
check(!!notes && notes.text === "Notes from the merge" && notes.title === MERGE, `the merge's chip: ${JSON.stringify(notes)}`);
check(notesTitle(true) === "Notes from the merge" && notesTitle(false) === "Notes on making the mesh", "the titles");
check(!/left out/.test(notesTitle(true)), "not “What the merge left out” over a note that says something was kept");
check(notesChip({ warnings: [ENGINE] }, true) === null && notesChip({ frames: {} }, false) === null, "no notes: no chip");
// an older engine's sentence (with "GPU") is still the path's
check(splitWarnings({ warnings: ["The camera path jumps by 9 mm near frame 2: tracking may have slid there. This scan was processed without a GPU."] }).path.length === 1, "older words");

console.log(`pathWarning.test: ${checks} checks passed`);
