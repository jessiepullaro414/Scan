/**
 * The words for what a scan keeps on disk and for freeing its space (FEATURE_PLAN R2,
 * `GET/POST /api/projects/{pid}/scans/{sid}/keep`, `/api/projects/{pid}/keep`). Pure functions: the
 * project page and its dialog only show what these return.
 */
import type { KeepKept, KeepLoss, KeepMode, KeepPart, ProjectKeepPlan, ProjectKeepResult, Scan, ScanDiskUse, ScanKeepPlan } from "../api/types";
import { fmtBytes, fmtInt } from "./format.ts";

/** The two lighter modes a user can choose, in the order the dialog shows them. */
export const KEEP_CHOICES: Exclude<KeepMode, "full">[] = ["engineering", "standard"];

/** A choice's title and what it means, in plain words. */
export const KEEP_CHOICE: Record<Exclude<KeepMode, "full">, { title: string; text: string }> = {
  engineering: {
    title: "Keep the recording and the result",
    text:
      "The processing stages and the undo history are removed. They can be rebuilt from the recording, so every number " +
      "can still be checked and the mesh made again.",
  },
  standard: {
    title: "Keep the result only",
    text:
      "The recording, the processing stages and the undo history are removed. The mesh with its confidence, the laser " +
      "points, the camera path, the report, the calibration and the kept features stay: the mesh can still be edited, " +
      "measured and exported, but not made again, merged or refined. This cannot be undone.",
  },
};

/** What the scan's row says it keeps ("" for a scan that keeps everything). */
export function keepLabel(mode: KeepMode | undefined): string {
  return mode === "engineering" ? "Keeps recording and result" : mode === "standard" ? "Keeps result only" : "";
}

/** The hover text of that label: what was removed and when, and what is on disk again since (R1's numbers). */
export function keepTitle(scan: Pick<Scan, "keep" | "freed">, disk?: ScanDiskUse | null, when?: (iso: string) => string): string {
  const mode = scan.keep ?? "full";
  if (mode === "full") return "";
  const freed = scan.freed ?? [];
  const total = freed.reduce((a, f) => a + f.bytes, 0);
  const last = freed.length ? freed[freed.length - 1].when : null;
  const what = mode === "standard" ? "its recording, processing stages and undo history were removed" : "its processing stages and undo history were removed";
  const lines = [`To free space${last && when ? ` (${when(last)})` : ""}, ${what}${total ? `: ${fmtBytes(total)} freed` : ""}.`];
  lines.push(
    mode === "standard"
      ? "The mesh can be edited, measured and exported. It cannot be made again, merged or refined."
      : "Make mesh rebuilds the stages from the recording.",
  );
  const back = disk ? disk.stages_bytes + disk.undo : 0;
  if (disk && disk.stages_bytes > 0) lines.push(`The mesh was made again since: stage files take ${fmtBytes(disk.stages_bytes)} again.`);
  else if (disk && back > 0) lines.push(`The undo history takes ${fmtBytes(disk.undo)}.`);
  return lines.join("\n");
}

/** Whether the scan still has the frames its mesh is made from (a merged scan, or one that keeps its result only, has not). */
export function canRemake(scan: Pick<Scan, "has_recording" | "merged_from" | "keep">): boolean {
  return scan.has_recording !== false && scan.keep !== "standard" && !scan.merged_from?.length;
}

const PART_LABEL: Record<KeepPart["part"], string> = {
  recording: "The recording (camera frames, motion sensor, events)",
  stages: "The processing stages",
  undo: "The undo history",
};

/** One line per part that goes: "The undo history: 76.0 MB". */
export function removeLines(plan: Pick<ScanKeepPlan, "remove">): string[] {
  return plan.remove.map((p) => `${PART_LABEL[p.part]}: ${fmtBytes(p.bytes)}`);
}

/**
 * What the move writes or leaves in place, one plain line each, so that the removed parts less these add up
 * to the "Frees" number (the cache file that stays is not counted: it is on disk before and after).
 */
export function writeLines(plan: Pick<ScanKeepPlan, "writes" | "kept_step_bytes">): string[] {
  const out = plan.writes.map((w) =>
    w.bytes < 0
      ? `The processing record is written again without the history: ${fmtBytes(-w.bytes)} smaller`
      : `The processing record is written again: ${fmtBytes(w.bytes)} larger`,
  );
  if (plan.kept_step_bytes > 0) out.push(`The current mesh stays as the starting point for new edits: ${fmtBytes(plan.kept_step_bytes)} (not freed)`);
  return out;
}

const LOSS: Record<KeepLoss, string> = {
  undo: "Undo the edits made so far. The mesh stays as it is now, and new edits can be undone.",
  stages: "Run Straighten, Fuse or a refinement on its own, until the mesh is made again (Make mesh rebuilds the stages).",
  remake: "Make the mesh again: Make mesh, Track, Straighten and Fuse need the recording.",
  merge: "Merge this scan with others. Aligning still works.",
  refine: "Refine with lasers, or from the images.",
  frames: "Go back to the camera frames to check a number.",
};

/** What can no longer be done afterwards, one sentence each. */
export function lossLines(loses: KeepLoss[]): string[] {
  return loses.map((l) => LOSS[l]);
}

const KEPT: Record<KeepKept, string> = {
  recording: "the recording",
  result: "the mesh with its confidence",
  laser_points: "the laser points",
  trajectory: "the camera path",
  report: "the processing report",
  calibration: "the calibration",
  features: "the kept features",
};

/** "It keeps the recording, the mesh with its confidence and the laser points." */
export function keptLine(keeps: KeepKept[]): string {
  const names = keeps.map((k) => KEPT[k]);
  if (!names.length) return "";
  const list = names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return `It keeps ${list}.`;
}

/** The plan in one line: how much it frees, or why it cannot. */
export function planLine(plan: ScanKeepPlan): string {
  if (!plan.ok) return plan.refusal ? `${capital(plan.refusal.detail)}.` : "This scan cannot be moved.";
  return plan.frees_bytes > 0 ? `Frees ${fmtBytes(plan.frees_bytes)}.` : "Frees nothing (nothing of this is on disk).";
}

/** The project plan in one line, and one line per scan that would be left as it is (with the reason). */
export function projectPlanLines(plan: ProjectKeepPlan): { text: string; skipped: string[] } {
  const n = plan.n_scans;
  const text =
    n > 0 ? `Frees ${fmtBytes(plan.frees_bytes)} on ${n} scan${n === 1 ? "" : "s"}.` : "Nothing to free: no scan of this project has any of this on disk.";
  const skipped = plan.scans.filter((s) => !s.ok && s.refusal).map((s) => `${s.name}: ${s.refusal!.detail}.`);
  return { text, skipped };
}

/** The second question (Standard only): the title and the words. */
export function confirmStandard(what: { name?: string; scans?: number }, bytes: number): { title: string; text: string; button: string } {
  const whose = what.name ? `of “${what.name}”` : `of ${what.scans ?? 0} scan${what.scans === 1 ? "" : "s"}`;
  return {
    title: "Delete the recording for good?",
    text:
      `The recording ${whose} will be deleted from disk (${fmtBytes(bytes)} freed in all). This cannot be undone: ` +
      "the mesh can never be made again from it, merged or refined. The mesh, its measurements and the kept features stay.",
    button: "Delete the recording",
  };
}

/** What the toast says once it is done. */
export function doneLine(freed: number, mode: KeepMode, name?: string): string {
  const keeps = mode === "standard" ? "its result only" : "its recording and its result";
  return name ? `Freed ${fmtBytes(freed)}. “${name}” now keeps ${keeps}.` : `Freed ${fmtBytes(freed)}.`;
}

/** The project move's toast: what was freed, and the scans left as they were. */
export function projectDoneLines(r: ProjectKeepResult): { text: string; failed: string[] } {
  const moved = r.results.filter((x) => x.ok).length;
  const failed = r.results.filter((x) => !x.ok && x.error && !x.error.detail.startsWith("nothing to free")).map((x) => `${x.name}: ${x.error!.detail}.`);
  return { text: `Freed ${fmtBytes(r.freed_bytes)} on ${fmtInt(moved)} scan${moved === 1 ? "" : "s"}.`, failed };
}

function capital(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}
