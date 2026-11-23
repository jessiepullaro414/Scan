/**
 * "Make mesh" built beside the scan's mesh (UX_PLAN R2, R15, R17, R18, R22; APP_API.md "API deviations (M3)", Make
 * mesh). Since the engine builds a new mesh in a folder of its own and puts it in place only once every stage and edit
 * has finished, the scan keeps its mesh, its stages and its edits while the run goes on, and after a cancel or a
 * failure (`Job.mesh_kept`). The run's own stages are in `ProcessState.making`, with the time left for the whole job
 * (`left_s`) and the span of the progress each part takes (`plan`). `ProcessState.made` says how long the Make mesh
 * that made the current mesh took. Pure; `making.test.ts` checks it.
 */
import type { Job, MadeInfo, MakingState, StageId, StageState } from "../api/types";
import { fmtLeft, jobEstimate } from "./estimate.ts";
import { fmtDuration } from "./format.ts";
import { STAGE_TITLES } from "./stages.ts";

type JobLike = Pick<Job, "kind" | "state"> & { scope?: string | null; mesh_kept?: boolean; error?: string | null };

/** A job that makes the whole mesh again: Make mesh (`process_scan`), or a recipe on everything. */
export function isWholeRun(job: (Pick<Job, "kind"> & { scope?: string | null }) | null | undefined): boolean {
  if (!job) return false;
  return job.kind === "process_scan" || (job.kind === "process_recipe" && job.scope === "all");
}

/** Whether the scan's earlier mesh is still its mesh: the engine's `mesh_kept`, else what the page knew. */
export function meshKept(job: { mesh_kept?: boolean } | null | undefined, hadMesh = false): boolean {
  return typeof job?.mesh_kept === "boolean" ? job.mesh_kept : hadMesh;
}

const KEPT = "The mesh stays as it was, and so does the recording.";

/**
 * What a whole run that ended cancelled or failed says, everywhere alike (the toasts, the project row, the Scan page,
 * the Jobs menu, Edit mesh): whether the earlier mesh stays. Null for any other job, or one that has not ended so.
 * `hadMesh`: the scan had a mesh before (for an engine without `mesh_kept`).
 */
export function makeMeshEnded(job: JobLike | null | undefined, hadMesh = false): { title: string; detail: string } | null {
  if (!job || !isWholeRun(job) || (job.state !== "cancelled" && job.state !== "failed")) return null;
  const kept = meshKept(job, hadMesh);
  if (job.state === "cancelled")
    return { title: "Making the mesh was cancelled", detail: kept ? KEPT : "The recording is kept: you can make the mesh any time." };
  // the engine's fixed words start with what failed: the title says it already
  const why = (job.error ?? "").replace(/^Making the mesh failed\.\s*/, "").trim();
  const said = /kept as it was|stays as it was/i.test(why);
  const safe = /recording is (safe|kept)/i.test(why);
  const tail = kept ? (said ? "" : "The mesh stays as it was.") : safe ? "" : "The recording is kept: try again, or at Preview quality.";
  return { title: "Making the mesh failed", detail: [why, tail].filter(Boolean).join(" ") || KEPT };
}

/** The words of the parts of a Make mesh: the stages by the table's names, and the edits. */
const PART_LABEL: Record<string, string> = {
  track: STAGE_TITLES.track.label,
  straighten: STAGE_TITLES.straighten.label,
  fuse: STAGE_TITLES.fuse.label,
  spacetime: STAGE_TITLES.spacetime.label,
  refine: STAGE_TITLES.refine.label,
  fine_walls: "Make only the thin walls finer", // ENGINE_PLAN X5 step 2, when the scan chose it
  edits: "The edits",
};

export type MakingRowState = "done" | "running" | "waiting" | "skipped" | "failed";
export type MakingRow = { id: string; label: string; state: MakingRowState; summary: string | null };

const rowState = (s: StageState | undefined): MakingRowState =>
  s === "running" ? "running" : s === "done" ? "done" : s === "skipped" ? "skipped" : s === "failed" ? "failed" : "waiting";

/** The stages a part of the plan stands for: Straighten fuses again at its poses, inside its share. */
const PART_STAGES: Record<string, StageId[]> = { track: ["track"], straighten: ["straighten", "fuse"], refine: ["refine"] };

/**
 * The run being built, part by part, for "The new mesh being made" on Edit mesh: the stages from `making.stages`
 * (never from the job's kind or message), the edits from the plan and the job's progress. One part running at most.
 */
export function makingRows(making: MakingState, progress: number): MakingRow[] {
  const st = making.stages ?? {};
  const parts: string[] = making.plan?.length
    ? making.plan.map((p) => p.stage)
    : (["track", "straighten", "fuse", "spacetime", "refine"] as const).filter((id) => id === "track" || id === "straighten" || id === "refine" || (st[id] && st[id]!.state !== "none"));
  const rows: MakingRow[] = parts.map((id) => {
    if (id === "edits") {
      const span = making.plan?.find((p) => p.stage === "edits");
      const stageRuns = Object.values(st).some((s) => s?.state === "running");
      const state: MakingRowState = !span ? "waiting" : progress >= span.to ? "done" : !stageRuns && progress >= span.from ? "running" : "waiting";
      return { id, label: PART_LABEL.edits, state, summary: null };
    }
    const ids = PART_STAGES[id] ?? [id as StageId];
    const states = ids.map((k) => st[k]?.state);
    const own = st[ids[0]];
    const state: MakingRowState = states.includes("running") ? "running" : rowState(own?.state);
    return { id, label: PART_LABEL[id] ?? id, state, summary: own?.summary ?? null };
  });
  let seen = false;
  for (const r of rows) {
    if (r.state !== "running") continue;
    if (seen) r.state = "waiting";
    seen = true;
  }
  return rows;
}

/** The part running now, in words ("Straighten", "the edits"); null when it cannot be told. */
export function makingNow(making: MakingState, progress: number): string | null {
  const run = makingRows(making, progress).find((r) => r.state === "running");
  if (run) return run.id === "edits" ? "the edits" : run.label;
  return null;
}

/**
 * The time left of a running job (UX_PLAN R18). A whole run says the time left for the whole job, from the engine's
 * `making.left_s`, with the part running now; before that is known (an older engine, or the state not read yet) the
 * running stage's own estimate, named, and that other stages follow. A stage run alone: its own estimate, named.
 */
export function timeLeft(job: (JobLike & Pick<Job, "progress" | "frames">) | null | undefined, making: MakingState | null | undefined): { kind: "left" | "measuring"; text: string } | null {
  if (!job || job.state !== "running") return null;
  if (isWholeRun(job) && making) {
    const now = makingNow(making, job.progress);
    const where = now ? ` (now: ${now})` : "";
    const left = making.left_s;
    if (typeof left === "number" && Number.isFinite(left) && left >= 0) return { kind: "left", text: `${fmtLeft(left, " for the whole mesh")}${where}` };
    return { kind: "measuring", text: `Working out the time left…${where}` };
  }
  const e = jobEstimate(job);
  if (e.kind === "left") return { kind: "left", text: isWholeRun(job) ? `${e.text}, then the other stages` : e.text };
  if (e.kind === "measuring") return { kind: "measuring", text: e.text };
  return null;
}

/**
 * The processing time in the viewer's report (UX_PLAN R17): how long the whole Make mesh took (`made.seconds`), else
 * the old number named for what it is (the report's `wall_time_s`: the last stage that made this mesh).
 */
export function processingTime(made: MadeInfo | null | undefined, wallTimeS: number | null | undefined): { label: string; value: string; title?: string } | null {
  if (made && typeof made.seconds === "number" && Number.isFinite(made.seconds))
    return { label: "Making the mesh took", value: fmtDuration(made.seconds), title: "The whole Make mesh: every stage and edit" };
  if (typeof wallTimeS === "number" && Number.isFinite(wallTimeS))
    return { label: "The last stage took", value: fmtDuration(wallTimeS), title: "The last stage that made this mesh; the stages before it and the edits not counted" };
  return null;
}
