/**
 * Checks for the Scan page's warning chips (`npm test`): the show / hide limits of the recording
 * backlog with their hysteresis, and the wording of the GPU fallback. Plain TypeScript that Node
 * runs as it is (type stripping), no test framework: a failed check throws.
 */
import { backlogVisible, describeGpuFallback } from "./warnings.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`check failed: ${what}`);
}

check(!backlogVisible(0, false) && !backlogVisible(60, false), "hidden up to 60 frames");
check(backlogVisible(61, false), "shown above 60");
check(backlogVisible(40, true) && backlogVisible(20, true), "once shown, it stays between 20 and 60");
check(!backlogVisible(40, false), "not shown between 20 and 60 if it was hidden");
check(!backlogVisible(19, true) && !backlogVisible(0, true), "hidden again below 20");
let shown = false;
const seen: boolean[] = [];
for (const n of [10, 70, 55, 62, 30, 25, 15, 40]) seen.push((shown = backlogVisible(n, shown)));
check(seen.join() === "false,true,true,true,true,true,false,false", "no flicker around the limits");

check(describeGpuFallback({ depth: "out of memory" }) === "the depth step (out of memory)", "depth stage in plain words");
check(
  describeGpuFallback({ depth: "a", track_filter: "b" }) === "the depth step (a); the tracking filter (b)",
  "two stages",
);
check(describeGpuFallback({ other: "" }) === "other", "an unknown stage keeps its name, no empty reason");

console.log(`warnings.test: ${checks} checks passed`);
