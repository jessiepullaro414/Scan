/**
 * Checks for Home's two first-run cards (UX_PLAN U26, `npm test`): while it is the first run, the setup card is the
 * one with buttons; the lower card's step 1 reads "Set up the scanner (about N minutes, once)" and it has no buttons
 * of its own. After the first run, step 1 names the calibration too. Plain TypeScript run by Node, no framework.
 */
import { getStarted } from "./getStarted.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`getStarted.test: ${what}`);
}

const first = getStarted({ firstRun: true, minutes: 16 });
check(first.steps[0].title === "Set up the scanner (about 16 minutes, once)", `first run, step 1: ${first.steps[0].title}`);
check(/control board \(Pico or ESP32\)/.test(first.steps[0].text) && /calibrat/.test(first.steps[0].text), "step 1 names the control board and the calibration");
check(!first.buttons, "first run: the lower card has no buttons (the setup card above has them)");
check(first.steps.length === 3 && first.steps[1].title === "Scan the part" && first.steps[2].title === "Make the mesh and measure", "the other two steps as they were");

const one = getStarted({ firstRun: true, minutes: 1 });
check(one.steps[0].title === "Set up the scanner (about 1 minute, once)", `one minute: ${one.steps[0].title}`);
const none = getStarted({ firstRun: true, minutes: 0 });
check(none.steps[0].title === "Set up the scanner (once)", `nothing left to time: ${none.steps[0].title}`);

const later = getStarted({ firstRun: false, minutes: 0 });
check(later.buttons, "after the first run: the card's own buttons (Create your first project, Try the simulator)");
check(later.steps[0].title === "Connect and calibrate the scanner", `after the first run, step 1: ${later.steps[0].title}`);
check(/Try the simulator|the simulator/.test(later.steps[0].text) && !/demo|sandbox/i.test(later.steps[0].text), "the simulator by its one name");

const all = [first, later].flatMap((g) => g.steps.flatMap((s) => [s.title, s.text]));
check(all.every((t) => !/\s[-–—]\s/.test(t)), "no dash as punctuation");

console.log(`getStarted.test: ${checks} checks passed`);
