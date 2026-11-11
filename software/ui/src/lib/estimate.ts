/**
 * The time left of a job's frame pass (plan F4), from the rate the engine measured on this job
 * (`Job.frames`: frames done of total, frames per second since the first frame).
 *
 * - No estimate until the rate is steady: at least `MIN_FRAMES` frames measured, and either
 *   `MIN_SECONDS` of them or `MIN_SHARE` of the pass done.
 * - After the last frame (the mesh is made from them, then cleaned and saved) there is no frame
 *   counter: "tail", and the job's message says what is happening.
 * - The estimate covers this pass only: a straighten pass after the tracking has its own count.
 *
 * Pure (no React, no store), so `npm test` runs it with Node.
 */
import type { Job } from "../api/types";

export const MIN_FRAMES = 5;
export const MIN_SECONDS = 3;
export const MIN_SHARE = 0.15;

export type Estimate =
  /** Nothing to estimate: no frame pass is running (queued, finished, or a stage without frames). */
  | { kind: "none" }
  /** Frames are being processed, the rate isn't steady yet. */
  | { kind: "measuring"; text: string }
  /** About `seconds` left in this pass. */
  | { kind: "left"; seconds: number; text: string }
  /** All frames done: the mesh is being made from them. */
  | { kind: "tail" };

/** What the pass is doing, for the words: "tracking", "fusing", or nothing for a stage it doesn't know. */
function doing(stage: string): string {
  if (stage === "track") return " for tracking";
  if (stage === "straighten" || stage === "fuse") return " for fusing";
  return "";
}

/** Seconds left in words, rounded so the text doesn't flicker: "About 4 min left". */
export function fmtLeft(seconds: number, suffix = ""): string {
  if (seconds < 15) return `A few seconds left${suffix}`;
  if (seconds < 50) return `Less than a minute left${suffix}`;
  if (seconds < 90) return `About 1 min left${suffix}`;
  const min = Math.round(seconds / 60);
  if (min < 60) return `About ${min} min left${suffix}`;
  const five = Math.round(min / 5) * 5;
  const h = Math.floor(five / 60);
  const m = five % 60;
  return m ? `About ${h} h ${m} min left${suffix}` : `About ${h} h left${suffix}`;
}

export function jobEstimate(job: Pick<Job, "state" | "frames"> | null | undefined): Estimate {
  if (!job || job.state !== "running") return { kind: "none" };
  const f = job.frames;
  if (!f || !(f.total > 0)) return { kind: "none" };
  if (f.done >= f.total) return { kind: "tail" };
  const measured = f.done - 1; // the rate is counted from the first frame on
  const steady =
    f.fps !== null && f.fps > 0 && Number.isFinite(f.fps) && measured >= MIN_FRAMES && (f.elapsed_s >= MIN_SECONDS || f.done >= MIN_SHARE * f.total);
  if (!steady) return { kind: "measuring", text: "Working out the time left…" };
  const seconds = (f.total - f.done) / f.fps!;
  return { kind: "left", seconds, text: fmtLeft(seconds, doing(f.stage)) };
}
