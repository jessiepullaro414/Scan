/**
 * Checks for where the keyboard focus goes when the pressed button goes away (leftovers of UX_PLAN R3, `npm test`):
 * after "Delete view N" the delete button of the view now in its place (else the one before it, else the Capture
 * button); after Pause or Resume the button that took its place. Plain TypeScript run by Node.
 */
import { focusAfterDelete, shouldRefocus } from "./focusAfter.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`focusAfter.test: ${what}`);
}

// views keep their numbers (index) or are numbered again: the position in the list is what counts
check(focusAfterDelete([0, 1, 2, 3, 4], 2, [0, 1, 3, 4]) === 3, "the next view, numbers kept");
check(focusAfterDelete([0, 1, 2, 3, 4], 2, [0, 1, 2, 3]) === 2, "the next view, numbered again");
check(focusAfterDelete([0, 1, 2], 2, [0, 1]) === 1, "the last one deleted: the one before it");
check(focusAfterDelete([5], 5, []) === null, "none left: the Capture button");
check(focusAfterDelete([0, 1, 2], 1, [0, 1, 2]) === 1, "the delete was refused: the same view's button");
check(focusAfterDelete([0, 1, 2], 9, [0, 1, 2]) === 2, "a view the list did not have: the last one");

// Pause / Resume: take the focus back only when it was on the pressed button and nothing else took it since
check(shouldRefocus(true, "body"), "the focus fell to the page: back on the button");
check(!shouldRefocus(true, "other"), "the person moved on: leave it");
check(!shouldRefocus(false, "body"), "the button did not have the focus: leave it");

console.log(`focusAfter.test: ${checks} checks passed`);
