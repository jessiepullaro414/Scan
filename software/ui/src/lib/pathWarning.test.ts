/** Checks for the camera path's warning (ENGINE_PLAN X0a, APP_API.md "The camera path's warning"; `npm test`). */
import { pathWarningChip, scanWarnings } from "./pathWarning.ts";

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

console.log(`pathWarning.test: ${checks} checks passed`);
