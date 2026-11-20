/** Checks for the setup wizard's progress count (UX_PLAN U27, `npm test`): the optional Print step is not counted. */
import type { SetupState } from "../api/types.ts";
import { setupProgress } from "./setup.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`check failed: ${what}`);
}

const ids = ["connect", "selftest", "print", "stereo", "lasers", "imu", "check"] as const;
const setup = (print: "optional" | "done" | "todo", rest: "done" | "todo" = "done") =>
  ({ steps: ids.map((id) => ({ id, state: id === "print" ? print : rest })) }) as unknown as SetupState;

const p = setupProgress(setup("optional"));
check(p.done === 6 && p.total === 6, `everything but the optional print: 6 of 6, not 6 of 7 (${p.done}/${p.total})`);
const q = setupProgress(setup("done"));
check(q.done === 7 && q.total === 7, "the print done too: 7 of 7");
const r = setupProgress(setup("optional", "todo"));
check(r.done === 0 && r.total === 6, "nothing done yet: 0 of 6");

console.log(`setup.test: ${checks} checks passed`);
