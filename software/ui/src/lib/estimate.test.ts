/**
 * Checks for the time-left estimate of a job's frame pass (`npm test`). Plain TypeScript that
 * Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { Job, JobFrames } from "../api/types.ts";
import { fmtLeft, jobEstimate } from "./estimate.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`estimate.test: ${what}`);
}

const job = (frames: JobFrames | null | undefined, state: Job["state"] = "running"): Pick<Job, "state" | "frames"> => ({ state, frames });
const fr = (done: number, total: number, fps: number | null, elapsed_s: number, stage = "track"): JobFrames => ({ stage, done, total, fps, elapsed_s });

// no frame pass, or not running
check(jobEstimate(null).kind === "none", "no job");
check(jobEstimate(job(undefined)).kind === "none", "a job without frames (an old engine, or between passes)");
check(jobEstimate(job(null)).kind === "none", "frames: null");
check(jobEstimate(job(fr(10, 100, 5, 2), "queued")).kind === "none", "queued");
check(jobEstimate(job(fr(10, 100, 5, 2), "done")).kind === "none", "finished");
check(jobEstimate(job(fr(10, 100, 5, 2), "failed")).kind === "none", "failed");
check(jobEstimate(job(fr(10, 100, 5, 2), "cancelled")).kind === "none", "cancelled");
check(jobEstimate(job(fr(0, 0, null, 0))).kind === "none", "an empty pass");

// no rate yet, or not steady
check(jobEstimate(job(fr(1, 900, null, 0))).kind === "measuring", "the first frame: no rate");
check(jobEstimate(job(fr(4, 900, 6, 0.5))).kind === "measuring", "too few frames");
check(jobEstimate(job(fr(12, 900, 6, 1.8))).kind === "measuring", "too short and too small a share");
check(jobEstimate(job(fr(20, 900, 0, 3))).kind === "measuring", "a zero rate");
check(jobEstimate(job(fr(20, 900, Number.POSITIVE_INFINITY, 3))).kind === "measuring", "an infinite rate");
const m = jobEstimate(job(fr(2, 900, 6, 0.2)));
check(m.kind === "measuring" && m.text === "Working out the time left…", "the measuring words");

// steady: by time, or by share (a short scan)
const a = jobEstimate(job(fr(20, 920, 6, 3.2)));
check(a.kind === "left" && Math.abs(a.seconds - 150) < 1e-9 && a.text === "About 3\u202fmin left for Follow the camera", `900 frames at 6\u202ffps: ${JSON.stringify(a)}`);
const b = jobEstimate(job(fr(8, 40, 10, 0.7)));
check(b.kind === "left" && Math.abs(b.seconds - 3.2) < 1e-9 && b.text === "A few seconds left for Follow the camera", `a short pass, steady by share: ${JSON.stringify(b)}`);

// the rate changes: the estimate follows it
const slow = jobEstimate(job(fr(100, 1000, 5, 20)));
const fast = jobEstimate(job(fr(100, 1000, 15, 20)));
check(slow.kind === "left" && fast.kind === "left" && Math.abs(slow.seconds - 180) < 1e-9 && Math.abs(fast.seconds - 60) < 1e-9, "the rate changes");
check(slow.kind === "left" && slow.text === "About 3\u202fmin left for Follow the camera" && fast.kind === "left" && fast.text === "About 1\u202fmin left for Follow the camera", "the words follow");

// the other passes
const s = jobEstimate(job(fr(300, 900, 10, 30, "straighten")));
// UX_PLAN R18: the estimate names its stage by the table's name (it is for that stage alone)
check(s.kind === "left" && s.text === "About 1\u202fmin left for Straighten", "a straighten pass: named by its stage");
const fz = jobEstimate(job(fr(300, 900, 10, 30, "fuse")));
check(fz.kind === "left" && fz.text === "About 1\u202fmin left for Build the surface", "a fuse pass");
const u = jobEstimate(job(fr(300, 900, 10, 30, "something")));
check(u.kind === "left" && u.text === "About 1\u202fmin left", "an unknown stage: no suffix");

// the tail: all frames done, no "0 s left"
check(jobEstimate(job(fr(900, 900, 6, 150))).kind === "tail", "the last frame is done");
check(jobEstimate(job(fr(901, 900, 6, 150))).kind === "tail", "done beyond total");

// the words
const words: [number, string][] = [
  [0, "A few seconds left"],
  [14.9, "A few seconds left"],
  [15, "Less than a minute left"],
  [49, "Less than a minute left"],
  [50, "About 1\u202fmin left"],
  [89, "About 1\u202fmin left"],
  [90, "About 2\u202fmin left"],
  [149, "About 2\u202fmin left"],
  [151, "About 3\u202fmin left"],
  [59 * 60, "About 59\u202fmin left"],
  [60 * 60, "About 1\u202fh left"],
  [83 * 60, "About 1\u202fh 25\u202fmin left"],
  [119 * 60, "About 2\u202fh left"],
];
for (const [sec, text] of words) check(fmtLeft(sec) === text, `${sec}\u202fs -> "${fmtLeft(sec)}", expected "${text}"`);

console.log(`estimate.test: ${checks} checks passed`);
