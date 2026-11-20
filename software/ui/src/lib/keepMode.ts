/**
 * The words for what a scan keeps on disk and for freeing its space (FEATURE_PLAN R2,
 * `GET/POST /api/projects/{pid}/scans/{sid}/keep`, `/api/projects/{pid}/keep`). Pure functions: the
 * project page and its dialog only show what these return.
 */
import type {
  KeepKept,
  KeepLoss,
  KeepMode,
  KeepPart,
  ProjectKeepPlan,
  ProjectKeepResult,
  Scan,
  ScanDiskUse,
  ScanKeepPlan,
  StageId,
  StageState,
} from "../api/types";
import { fmtBytes, fmtInt } from "./format.ts";

/**
 * What the dialog offers, lightest last: the two lighter modes, and between them (R3) "recording and result" with the
 * camera frames Standard quality skips removed too. That one is the engine's "engineering" with `thin: true`, so the
 * scan still keeps its recording and everything that needs only the frames Standard uses works as before.
 */
export type KeepChoice = "engineering" | "thinned" | "standard";
export const KEEP_CHOICES: KeepChoice[] = ["engineering", "thinned", "standard"];

/** The route's arguments for a choice. */
export function choiceArgs(c: KeepChoice): { mode: Exclude<KeepMode, "full">; thin: boolean } {
  return c === "thinned" ? { mode: "engineering", thin: true } : { mode: c, thin: false };
}

/** A choice's title and what it means, in plain words. */
export const KEEP_CHOICE: Record<KeepChoice, { title: string; text: string }> = {
  engineering: {
    title: "Keep the recording and the result",
    text:
      "The processing stages and the undo history are removed. They can be rebuilt from the recording, so every number " +
      "can still be checked and the mesh made again.",
  },
  thinned: {
    title: "Keep the frames Standard quality uses, and the result",
    text:
      "As above, and the camera frames Standard quality skips are removed too (every 2nd frame of a recording made at 60 " +
      "frames a second). Make mesh at Standard quality gives the same mesh as before, and the motion sensor, the events " +
      "and the calibration stay. Best quality then works from the frames that stay. This cannot be undone.",
  },
  standard: {
    title: "Keep the result only",
    text:
      "The recording, the processing stages and the undo history are removed. The mesh with its confidence, the laser " +
      "points, the camera path, the report, the calibration and the kept features stay: the mesh can still be edited, " +
      "measured and exported, but not made again, merged or refined. This cannot be undone.",
  },
};

/** "30 frames a second" (whole numbers; one decimal below 10). */
export function fpsText(fps: number): string {
  const v = fps >= 10 ? Math.round(fps) : Math.round(fps * 10) / 10;
  return `${v} frame${v === 1 ? "" : "s"} a second`;
}

/** What the scan's row says it keeps ("" for a scan that keeps everything). */
export function keepLabel(mode: KeepMode | undefined, thinned?: Scan["thinned"] | null): string {
  if (mode === "engineering" && thinned) return `Keeps recording (${fpsText(thinned.fps)}) and result`;
  return mode === "engineering" ? "Keeps recording and result" : mode === "standard" ? "Keeps result only" : "";
}

/** R3: what a thinned recording holds (or, with `future`, would hold), one line ("" when it is not thinned). */
export function thinnedLine(thinned: Scan["thinned"] | ScanKeepPlan["thinned"] | null | undefined, future = false): string {
  if (!thinned) return "";
  return (
    `Its recording ${future ? "will hold" : "holds"} the frames Standard quality uses: ${fpsText(thinned.fps)} ` +
    `(${fmtInt(thinned.frames)} of ${fmtInt(thinned.frames_before)} frames, recorded at ${fpsText(thinned.fps_before)}).`
  );
}

/** The hover text of that label: what was removed and when, and what is on disk again since (R1's numbers). */
export function keepTitle(scan: Pick<Scan, "keep" | "freed" | "thinned">, disk?: ScanDiskUse | null, when?: (iso: string) => string): string {
  const mode = scan.keep ?? "full";
  if (mode === "full") return "";
  const freed = scan.freed ?? [];
  const total = freed.reduce((a, f) => a + f.bytes, 0);
  const last = freed.length ? freed[freed.length - 1].when : null;
  const what =
    mode === "standard"
      ? "its recording, processing stages and undo history were removed"
      : scan.thinned
        ? "the camera frames Standard quality skips, its processing stages and undo history were removed"
        : "its processing stages and undo history were removed";
  const lines = [`To free space${last && when ? ` (${when(last)})` : ""}, ${what}${total ? `: ${fmtBytes(total)} freed` : ""}.`];
  if (mode === "engineering" && scan.thinned) lines.push(thinnedLine(scan.thinned));
  lines.push(
    mode === "standard"
      ? "The mesh can be edited, measured and exported. It cannot be made again, merged or refined."
      : scan.thinned
        ? "Make mesh rebuilds the stages from the recording; at Standard quality it gives the same mesh as before."
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

// ---------------------------------------------------------------- what a freed scan can still run
/** The stage actions of the Process page and the viewer's Make mesh / Remake. `recipe_all`: a recipe on "everything". */
export type StageAction = "make_mesh" | "track" | "straighten" | "fuse" | "spacetime" | "refine" | "recipe_all";
type StageLite = { state: StageState; when: string | null };
/** What the rule reads of the process state (`ProcessState` fits it). */
export type StagesLite = {
  stages: Record<StageId, StageLite> & { refine: StageLite & { source: StageId | null } };
  base: { stage: StageId } | null;
};

/** Why an action cannot run, one plain sentence each: why, and what would bring it back. */
export const BLOCK = {
  resultOnly: "This scan's recording was removed to free space (it keeps its result only). Only a new scan of the part can make the mesh again.",
  resultOnlyStages:
    "The stage files this needs were removed to free space (this scan keeps its result only). Only a new scan of the part can bring them back.",
  stagesGone: "The stage files this needs were removed to free space. Make mesh, or Track, builds them again from the recording.",
  merged: "This scan was merged from other scans and has no recording of its own. Merge those scans again to make it again.",
  noRecording: "This scan has no recording. Record the part again to make a new mesh.",
} as const;

/** The actions that read the recording's frames (the engine's `_need_recording`). */
const READS_FRAMES: StageAction[] = ["make_mesh", "track", "straighten", "fuse", "spacetime", "recipe_all"];

/** Why the scan has no frames to read, in the engine's order, or null when it has them. */
function noFrames(scan: Pick<Scan, "keep" | "has_recording" | "merged_from">): string | null {
  if (scan.keep === "standard") return BLOCK.resultOnly;
  if (scan.merged_from?.length) return BLOCK.merged;
  if (scan.has_recording === false) return BLOCK.noRecording;
  return null;
}

/** When the scan's space was last freed (ms), or null (never, or no date). */
function lastFreed(scan: Pick<Scan, "keep" | "freed">): number | null {
  if (scan.keep !== "engineering" && scan.keep !== "standard") return null;
  const t = (scan.freed ?? []).map((f) => Date.parse(f.when)).filter((x) => Number.isFinite(x));
  return t.length ? Math.max(...t) : null;
}

/**
 * Whether a stage that ran is missing its files: freeing removes them but leaves the stage "done" in the record, so a
 * stage's files are on disk only when it finished after the last freeing. A stage that never ran is not this rule's to say.
 */
function filesGone(stage: StageLite | undefined, freedAt: number): boolean {
  if (!stage || stage.state !== "done") return false;
  const t = stage.when ? Date.parse(stage.when) : NaN;
  return !(Number.isFinite(t) && t > freedAt);
}

/**
 * Why `action` cannot run on this scan now (one sentence: why, and what brings it back), or null when it can, as the
 * engine decides it (`ohs/server/processing.py`): what reads the frames needs the recording; Straighten, Fuse and Refine
 * from the images also need the Track stage's files; Refine with lasers needs only the base stage's mesh file. After
 * "recording and result", Make mesh or Track rebuilds the stage files and the other stages run again. Without the
 * process state (`state` null) only the recording decides.
 */
export function stageBlock(
  scan: Pick<Scan, "keep" | "freed" | "has_recording" | "merged_from">,
  state: StagesLite | null,
  action: StageAction,
): string | null {
  if (READS_FRAMES.includes(action)) {
    const why = noFrames(scan);
    if (why) return why;
  }
  if (action === "make_mesh" || action === "track" || action === "recipe_all" || !state) return null;
  const freedAt = lastFreed(scan);
  if (freedAt === null) return null;
  const gone = scan.keep === "standard" ? BLOCK.resultOnlyStages : BLOCK.stagesGone;
  if (action === "refine") {
    const b = state.base?.stage ?? "track";
    const src = b === "refine" ? (state.stages.refine.source ?? "track") : b;
    return filesGone(state.stages[src], freedAt) ? gone : null;
  }
  return filesGone(state.stages.track, freedAt) ? gone : null;
}

/** Why Make mesh (Remake, Rebuild) cannot run on this scan, or null: the same rule as `stageBlock(…, "make_mesh")`. */
export function remakeBlock(scan: Pick<Scan, "keep" | "freed" | "has_recording" | "merged_from">): string | null {
  return stageBlock(scan, null, "make_mesh");
}

const PART_LABEL: Record<KeepPart["part"], string> = {
  recording: "The recording (camera frames, motion sensor, events)",
  frames: "The camera frames Standard quality skips",
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
  const what: Record<ScanKeepPlan["writes"][number]["what"], [string, string]> = {
    state: ["The processing record is written again without the history", "The processing record is written again"],
    frames_list: ["The list of frames is written again with the frames that stay", "The list of frames is written again"],
    meta: ["The recording's notes are written again", "The recording's notes say what it holds now"],
  };
  const out = plan.writes.map((w) => {
    const [smaller, larger] = what[w.what] ?? what.state;
    return w.bytes < 0 ? `${smaller}: ${fmtBytes(-w.bytes)} smaller` : `${larger}: ${fmtBytes(w.bytes)} larger`;
  });
  if (plan.kept_step_bytes > 0) out.push(`The current mesh stays as the starting point for new edits: ${fmtBytes(plan.kept_step_bytes)} (not freed)`);
  return out;
}

/**
 * R3: what Best quality loses once only the frames Standard uses are kept, measured on the simulated scans
 * (VALIDATION.md, "Keeping only the frames Standard uses"). Best then works from the frames that stay and gives the
 * Standard mesh.
 */
export const BEST_LOSS =
  "Best quality from every recorded frame: it then works from the frames that stay and gives the same mesh as Standard. " +
  "On the simulated test scans that meant 3.6 to 4.9\u202f% less surface within 0.5\u202fmm of the true shape and half the laser " +
  "points; the median error was 0.019\u202fmm larger on the car panel and 0.011\u202fmm smaller on the bracket.";

const LOSS: Record<KeepLoss, string> = {
  undo: "Undo the edits made so far. The mesh stays as it is now, and new edits can be undone.",
  stages: "Run Straighten, Fuse or a refinement on its own, until the mesh is made again (Make mesh rebuilds the stages).",
  remake: "Make the mesh again: Make mesh, Track, Straighten and Fuse need the recording.",
  merge: "Merge this scan with others. Aligning still works.",
  refine: "Refine with lasers, or from the images.",
  frames: "Go back to the camera frames to check a number.",
  best: BEST_LOSS,
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

/** R3, the second question for "the frames Standard quality uses": the title and the words. */
export function confirmThin(what: { name?: string; scans?: number }, bytes: number): { title: string; text: string; button: string } {
  const whose = what.name ? `of “${what.name}”` : `of ${what.scans ?? 0} scan${what.scans === 1 ? "" : "s"}`;
  return {
    title: "Delete the frames Standard quality skips?",
    text:
      `The camera frames Standard quality skips ${whose} will be deleted from disk (${fmtBytes(bytes)} freed in all). This ` +
      "cannot be undone. Make mesh at Standard quality still gives the same mesh; Best quality will work from the frames " +
      "that stay, so it gives the Standard mesh too.",
    button: "Delete the skipped frames",
  };
}

/** What the toast says once it is done. */
export function doneLine(freed: number, mode: KeepMode, name?: string, thin = false): string {
  const keeps = mode === "standard" ? "its result only" : thin ? "the frames Standard quality uses and its result" : "its recording and its result";
  return name ? `Freed ${fmtBytes(freed)}. “${name}” now keeps ${keeps}.` : `Freed ${fmtBytes(freed)}.`;
}

/** The project move's toast: what was freed, and the scans left as they were. */
export function projectDoneLines(r: ProjectKeepResult): { text: string; failed: string[] } {
  const moved = r.results.filter((x) => x.ok).length;
  // the engine's words: "There is nothing to free: …" (older engines: "nothing to free…"); not a failure, not listed
  const nothing = (detail: string) => /^(there is )?nothing to free/i.test(detail);
  const stop = (s: string) => (/[.!?…]$/.test(s) ? s : `${s}.`);
  const failed = r.results.filter((x) => !x.ok && x.error && !nothing(x.error.detail)).map((x) => `${x.name}: ${stop(x.error!.detail)}`);
  return { text: `Freed ${fmtBytes(r.freed_bytes)} on ${fmtInt(moved)} scan${moved === 1 ? "" : "s"}.`, failed };
}

function capital(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}
