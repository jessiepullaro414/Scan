/**
 * The moved-part question (UX_PLAN U15): there is no default. Turning the part over is the usual reason for a
 * second scan, so "Did you move or turn the part between the two scans? Yes / No" is asked on the Align page and
 * for a second pass, and must be answered before aligning and before the pass starts. The answer is the engine's
 * `same_setup` (not moved: the table both scans share helps and stays; moved: each scan's table is left out).
 *
 * Pure: the Align page, the pass panel and their stores use it; `moved.test.ts` checks it.
 */

/** null: not answered yet; true: the part was moved or turned; false: it stayed where it was. */
export type MovedAnswer = boolean | null;

export function movedQuestion(several: boolean): string {
  return several ? "Did you move or turn the part between the scans?" : "Did you move or turn the part between the two scans?";
}

/** The two answers, in the order shown. */
export const MOVED_ANSWERS: readonly { moved: boolean; label: string }[] = [
  { moved: true, label: "Yes" },
  { moved: false, label: "No" },
];

/** The engine's `same_setup` for an answer (null while unanswered). */
export function sameSetupOf(moved: MovedAnswer): boolean | null {
  return moved === null ? null : !moved;
}

/** What waits for the answer, said where the button is; null once answered. */
export function movedNeeded(moved: MovedAnswer, where: "align" | "pass"): string | null {
  if (moved !== null) return null;
  return where === "align" ? "Answer the question above first: did you move or turn the part?" : "Answer first: did you move or turn the part since the first scan?";
}
