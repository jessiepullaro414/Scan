/**
 * Where the keyboard focus goes when the pressed button goes away (leftovers of UX_PLAN R3): a button that is
 * disabled while its request runs, or replaced by another, drops the focus to the page, and a keyboard user loses the
 * place. Pure; `focusAfter.test.ts` checks it.
 */

/**
 * After "Delete view N": the view whose delete button takes the focus, by its place in the list (views may keep their
 * numbers or be numbered again): the one now at the deleted one's place, else the one before it; null when none is
 * left (then the Capture button).
 */
export function focusAfterDelete(before: readonly number[], deleted: number, after: readonly number[]): number | null {
  if (!after.length) return null;
  const at = before.indexOf(deleted);
  const pos = at < 0 ? after.length - 1 : Math.min(at, after.length - 1);
  return after[pos];
}

/**
 * After Pause or Resume (the button is disabled while the request runs, then another takes its place): give the focus
 * back to the button now there when the pressed one had it and the focus fell to the page ("body"), not when the
 * person has moved on ("other").
 */
export function shouldRefocus(hadFocus: boolean, now: "body" | "other"): boolean {
  return hadFocus && now === "body";
}
