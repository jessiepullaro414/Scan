/**
 * The calibration result's words that the UI and the demo share (UX_PLAN U10, U40, U41, U48).
 * A grade says how well the board was measured, never how accurate a scan will be (the roadmap's
 * rule): the verdict sentences below are what the server says too.
 *
 * Plain TypeScript (no React, no icons) so `npm test` can check it.
 */
import type { CalibGrade, CalibKind } from "../api/types";

export const CALIB_VERDICT = {
  /** Stereo, graded excellent. */
  stereoExcellent: "This calibration is as good as the board allows.",
  /** Stereo, graded good. */
  stereoGood: "Good: no step needs redoing.",
  /** Lasers, the two standard lines, graded good or better. */
  lasersGood: "Both laser lines are calibrated well.",
  /** Lasers, any other set. */
  lasersGoodAny: (n: number) => `All ${n} laser lines are calibrated well.`,
  /** IMU, graded usable or better. */
  imuGood: "The motion sensor keeps tracking steady on fast sweeps and plain surfaces.",
  /** The Process page's laser stage. */
  laserStage: "The lines are measured on their own, so they correct the surface.",
};

const GRADE_WORD: Record<CalibGrade, string> = {
  excellent: "Excellent",
  good: "Good",
  usable: "Usable",
  poor: "Poor",
  failed: "Not yet",
};

/** The calibration check's state, as the status says it everywhere else (U48). */
export const CHECK_STATE = { ok: "Calibration valid", drifted: "Drifted" };
/** The headline of a calibration check that did not pass (the server's words, `ohs/server/calib_results.py`). */
export const CHECK_HEADLINE_FAILED = "The calibration needs attention";

/**
 * The badge above a result's headline, or null when the headline already says it (U41:
 * "Excellent" over "Excellent stereo calibration" said it twice).
 */
export function resultBadge(grade: CalibGrade, kind: CalibKind, ok: boolean, headline: string): string | null {
  const word = kind === "check" ? (ok ? CHECK_STATE.ok : CHECK_STATE.drifted) : GRADE_WORD[grade];
  return headline.trim().toLowerCase().includes(word.toLowerCase()) ? null : word;
}

/** What the empty advice list says, or null when the verdict already said it (U41). */
export function emptyAdviceText(kind: CalibKind, saved: boolean, verdict: string): string | null {
  if (kind === "check") return /nothing to do/i.test(verdict) ? null : "Nothing to do: scan away.";
  return saved ? "Nothing to improve." : "Nothing to improve. Save it.";
}
