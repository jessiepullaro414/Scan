/** Pure logic of the Scan page's two warning chips: when the recording backlog shows, and the GPU fallback wording. */

/** The "saving is behind" chip shows above this many waiting frames and hides again below `BACKLOG_HIDE`. */
export const BACKLOG_SHOW = 60;
export const BACKLOG_HIDE = 20;

/** Whether the chip is shown now, given whether it was shown before (between the two limits it stays as it was). */
export function backlogVisible(backlog: number, wasShown: boolean): boolean {
  if (backlog > BACKLOG_SHOW) return true;
  if (backlog < BACKLOG_HIDE) return false;
  return wasShown;
}

const STAGE_TEXT: Record<string, string> = { depth: "the depth step", track_filter: "the tracking filter" };

/** "the depth step (out of memory); the tracking filter (...)": the stages that fell back, in plain words. */
export function describeGpuFallback(fallback: Record<string, string>): string {
  return Object.entries(fallback)
    .map(([stage, reason]) => `${STAGE_TEXT[stage] ?? stage}${reason ? ` (${reason})` : ""}`)
    .join("; ");
}
