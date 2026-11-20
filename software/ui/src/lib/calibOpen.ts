/**
 * An open calibration session blocks a scan (the engine answers 409: a scan and a calibration
 * never run together). UX_PLAN U1: the Scan page says why Start is not possible and offers a way
 * out, the sidebar says the same, and leaving a result that cannot be saved (a check, or a
 * failed solve) closes it, so it never blocks the first scan unseen.
 *
 * Plain TypeScript (no React, no icons) so `npm test` can check it.
 */
import type { CalibKind, CalibSession } from "../api/types";
import type { Route } from "./router";

export type CalibOpen = {
  /** The sentence the Scan page shows in place of Start's reason. */
  reason: string;
  /** The sidebar's label for Setup (its badge says the same). */
  sidebar: string;
  /** "close": a result that cannot be saved, closed with one press; "review": go to Setup and see it. */
  action: "close" | "review";
  label: string;
  /** The Setup step the session belongs to. */
  step: CalibKind | null;
};

/** What an open calibration session means for scanning, or null when nothing is open. */
export function calibOpen(s: CalibSession | null | undefined): CalibOpen | null {
  if (!s || s.state === "idle" || s.state === "saved") return null;
  const step = s.kind;
  if (s.state === "result") {
    const closeable = !s.result || !s.result.can_save;
    return {
      reason: "A calibration result is still open in Setup.",
      sidebar: "Setup: a calibration result is still open",
      action: closeable ? "close" : "review",
      label: closeable ? "Close it" : "Review it",
      step,
    };
  }
  return {
    reason: "A calibration is in progress in Setup.",
    sidebar: "Setup: a calibration is in progress",
    action: "review",
    label: "Review it",
    step,
  };
}

/**
 * Leaving the page that shows a result that cannot be saved closes it (as the advice buttons do,
 * pages/setup/actions.ts `leaveTo`). A result that can be saved stays, to come back to.
 */
export function closesOnLeave(s: CalibSession | null | undefined, from: Route, to: Route): boolean {
  if (!s || s.state !== "result" || (s.result && s.result.can_save)) return false;
  const onIt = (r: Route) => r.name === "setup" && r.step !== null && r.step === s.kind;
  return onIt(from) && !onIt(to);
}
