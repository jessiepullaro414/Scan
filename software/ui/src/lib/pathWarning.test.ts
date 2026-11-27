/** Checks for the camera path's warning (ENGINE_PLAN X0a, APP_API.md "The camera path's warning"; `npm test`). */
import { INCOMPLETE_TITLE, incompleteChip, notesChip, notesTitle, pathWarningChip, scanWarnings, splitWarnings } from "./pathWarning.ts";

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

// ENGINE_PLAN R2-5: the mesh model that ran out of room left surface out of the mesh: a warning with its own title, on
// the row, in the viewer and under its stage; not a quiet note. The engine's words, as `pool_full_warning` writes them.
const FULL =
  "The mesh model ran out of room after frame 6: surface seen after that was not added (40 of 1,040 blocks of surface left out; the model holds 1,000 blocks, 12 MB). The mesh is incomplete there. Scan a smaller area at a time, or make the mesh at a coarser mesh detail.";
const FULL_MERGE = FULL.replace("after frame 6", 'after frame 6 of "Bracket from the side"');
const FINE =
  "The finer model for the thin walls ran out of room after frame 7: finer surface seen after that was not added (1,200 of 6,200 blocks of surface left out; the model holds 5,000 blocks, 60 MB). The thin walls it did not reach keep the surface Make mesh made, at the mesh's own detail; nothing was removed. Scan a smaller area at a time to make them all finer.";
const three = splitWarnings({ warnings: [ENGINE, FULL, MERGE, FINE] });
check(three.path.length === 1 && three.incomplete.length === 1 && three.incomplete[0] === FULL, `the mesh model's sentence, apart: ${three.incomplete.join("|")}`);
check(three.other.length === 2 && three.other[0] === MERGE && three.other[1] === FINE, `the thin walls' sentence stays a quiet note: ${three.other.join("|")}`);
check(three.path.length + three.incomplete.length + three.other.length === 4, "no sentence is lost or said twice");
check(splitWarnings({ warnings: [FULL_MERGE] }).incomplete.length === 1, "a merge's, with the scan's name");
const full = incompleteChip({ warnings: [MERGE, FULL] });
check(!!full && full.text === "The mesh is incomplete" && full.title === FULL, `the row's warning: ${JSON.stringify(full)}`);
check(incompleteChip({ warnings: [MERGE, FINE, ENGINE] }) === null && incompleteChip({ frames: {} }) === null, "nothing when the model had room");
const rest = notesChip({ warnings: [FULL, MERGE] }, true);
check(!!rest && rest.title === MERGE, `the notes chip holds the other notes only: ${rest?.title}`);
check(notesChip({ warnings: [FULL] }, false) === null, "the mesh model's sentence alone: no notes chip beside its warning");
check(INCOMPLETE_TITLE === "The mesh is incomplete", "the title");

console.log(`pathWarning.test: ${checks} checks passed`);
