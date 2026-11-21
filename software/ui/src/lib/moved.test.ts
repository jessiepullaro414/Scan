/**
 * Checks for the moved-part question (UX_PLAN U15, `npm test`): no default. "Did you move or turn the part
 * between the two scans? Yes / No" must be answered before aligning, and before a second pass starts; the answer
 * becomes the engine's `same_setup`. Plain TypeScript run by Node, no framework.
 */
import { MOVED_ANSWERS, movedNeeded, movedQuestion, sameSetupOf } from "./moved.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`moved.test: ${what}`);
}

check(movedQuestion(false) === "Did you move or turn the part between the two scans?", "two scans: the plan's words");
check(movedQuestion(true) === "Did you move or turn the part between the scans?", "several scans");
check(MOVED_ANSWERS.map((a) => a.label).join(" / ") === "Yes / No", "the two answers, Yes first");
check(MOVED_ANSWERS[0].moved === true && MOVED_ANSWERS[1].moved === false, "Yes: moved; No: not moved");

check(sameSetupOf(null) === null, "not answered: nothing to send");
check(sameSetupOf(true) === false, "moved: not the same setup (the table is left out)");
check(sameSetupOf(false) === true, "not moved: the same setup (the table helps)");

check(movedNeeded(null, "align") === "Answer the question above first: did you move or turn the part?", `aligning waits for the answer (${movedNeeded(null, "align")})`);
check(movedNeeded(null, "pass") === "Answer first: did you move or turn the part since the first scan?", `a pass waits for the answer (${movedNeeded(null, "pass")})`);
check(movedNeeded(true, "align") === null && movedNeeded(false, "pass") === null, "answered either way: nothing waits");

console.log(`moved.test: ${checks} checks passed`);
