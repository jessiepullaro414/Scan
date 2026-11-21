/**
 * Stopping a scan with Esc (UX_PLAN U45): Esc also means "dismiss", so one press only asks, and a second press
 * within a second stops and saves. The Stop button stops at once. Pure: the Scan page keeps `armedAt` and shows
 * the words while it is set; `escStop.test.ts` checks it.
 */

export const ESC_WINDOW_MS = 1000;
export const ESC_AGAIN_TEXT = "Press Esc again to stop";

/** One press of Esc at `now` (ms), with the time of the press before it that asked (null: none). */
export function escPress(armedAt: number | null, now: number): { stop: boolean; armedAt: number | null } {
  if (armedAt !== null && now >= armedAt && now - armedAt <= ESC_WINDOW_MS) return { stop: true, armedAt: null };
  return { stop: false, armedAt: now };
}
