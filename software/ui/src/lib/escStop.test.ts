/**
 * Checks for stopping a scan with Esc (UX_PLAN U45, `npm test`): Esc is also "dismiss", so one press only asks
 * ("Press Esc again to stop") and a second press within a second stops. Plain TypeScript run by Node, no framework.
 */
import { ESC_AGAIN_TEXT, ESC_WINDOW_MS, escPress } from "./escStop.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`escStop.test: ${what}`);
}

check(ESC_WINDOW_MS === 1000, "within a second");
check(ESC_AGAIN_TEXT === "Press Esc again to stop", "the words on screen");

const a = escPress(null, 5000);
check(!a.stop && a.armedAt === 5000, "the first press does not stop: it asks");
const b = escPress(a.armedAt, 5600);
check(b.stop && b.armedAt === null, "a second press within the second stops, and the ask is gone");
const c = escPress(a.armedAt, 6001);
check(!c.stop && c.armedAt === 6001, "a press after the second asks again");
check(escPress(a.armedAt, 6000).stop, "exactly a second later still counts");
const d = escPress(5000, 4000);
check(!d.stop && d.armedAt === 4000, "a clock that went back asks again");

console.log(`escStop.test: ${checks} checks passed`);
