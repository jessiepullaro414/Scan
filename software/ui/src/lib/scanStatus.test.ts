/** Checks for why a scan failed, said on the project page (UX_PLAN U38, `npm test`). */
import { failedLine } from "./scanStatus.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`check failed: ${what}`);
}

// the engine's reason carries its own advice: the row's advice is only for a failure without a reason (said once)
check(failedLine("Too few frames tracked.", true) === "Failed: too few frames tracked.", failedLine("Too few frames tracked.", true));
const fixed = "Following the camera path failed. Your recording is safe: try Follow the camera again, or Make mesh at Preview quality.";
check(failedLine(fixed, true) === fixed, `a reason that says what failed stands alone, "failed" said once: ${failedLine(fixed, true)}`);
check(failedLine("The recording has no frames. Record the scan again.", true) === "Failed: the recording has no frames. Record the scan again.", "a refusal's reason and hint, no advice added");
check(failedLine("too few frames tracked", false) === "Failed: too few frames tracked.", "no recording to make it from: no advice");
// found in review: a reason that starts with an abbreviation keeps its capitals ("iMU" was shown)
check(failedLine("IMU data is missing.", false) === "Failed: IMU data is missing.", failedLine("IMU data is missing.", false));
check(failedLine("A frame could not be read", false) === "Failed: a frame could not be read.", "a one-letter first word is lowered");
// UX_PLAN R12: "Making the mesh", not "Meshing" (expectations changed with the words)
check(failedLine(null, true) === "Making the mesh failed. Make mesh again, or at Preview quality.", "no reason given");
check(failedLine("  ", false) === "Making the mesh failed.", "an empty reason");

console.log(`scanStatus.test: ${checks} checks passed`);
