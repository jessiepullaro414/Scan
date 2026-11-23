/**
 * Checks for "Make mesh" built beside the scan's mesh (UX_PLAN R2, R15, R17, R18, R22; `npm test`): which jobs make
 * the whole mesh, what is said when one ends cancelled or failed (`Job.mesh_kept`), the new run's stages (one running
 * at a time), the time left for the whole job (`making.left_s`), and how long the mesh took (`ProcessState.made`).
 * Plain TypeScript run by Node, no framework.
 */
import type { Job, MakingState } from "../api/types.ts";
import { isWholeRun, makeMeshEnded, makingNow, makingRows, processingTime, timeLeft } from "./making.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`making.test: ${what}`);
}

const N = " ";
const job = (p: Partial<Job> & { scope?: string | null } = {}): Job =>
  ({ id: "j-1", kind: "process_scan", state: "running", progress: 0.5, message: "", result: null, error: null, created: "", ...p }) as Job;

// which jobs make the whole mesh (the real kinds: APP_API.md "Jobs")
check(isWholeRun(job()), "Make mesh: process_scan");
check(isWholeRun(job({ kind: "process_recipe", scope: "all" })), "a recipe on everything");
check(!isWholeRun(job({ kind: "process_recipe", scope: "edits" })), "a recipe on the edits only");
check(!isWholeRun(job({ kind: "process_track" })) && !isWholeRun(job({ kind: "mesh_edit" })) && !isWholeRun(null), "a stage alone, an edit, nothing");

// R2: a cancelled or failed Make mesh says whether the earlier mesh stays (`mesh_kept`), everywhere in the same words
const kept = makeMeshEnded(job({ state: "cancelled", mesh_kept: true }));
check(kept?.title === "Making the mesh was cancelled" && kept.detail === "The mesh stays as it was, and so does the recording.", JSON.stringify(kept));
const fresh = makeMeshEnded(job({ state: "cancelled", mesh_kept: false }));
check(fresh?.detail === "The recording is kept: you can make the mesh any time.", JSON.stringify(fresh));
// an engine without the field: what the page knew (the scan had a mesh before)
check(makeMeshEnded(job({ state: "cancelled" }), true)?.detail === kept!.detail, "no field, the scan had a mesh: kept");
check(makeMeshEnded(job({ state: "cancelled" }), false)?.detail === fresh!.detail, "no field, no mesh before");
check(makeMeshEnded(job({ state: "cancelled", mesh_kept: false }), true)?.detail === fresh!.detail, "the field wins over the guess");
const failedKept = makeMeshEnded(job({ state: "failed", mesh_kept: true, error: "Making the mesh failed. Your recording is safe: try Make mesh again, or at Preview quality." }));
check(failedKept?.title === "Making the mesh failed", `failed: ${failedKept?.title}`);
check(failedKept!.detail === "Your recording is safe: try Make mesh again, or at Preview quality. The mesh stays as it was.", `the title not said twice: ${failedKept!.detail}`);
const swap = makeMeshEnded(job({ state: "failed", mesh_kept: true, error: "The new mesh could not take the place of the earlier one, because a file of this scan is open in another program. The earlier mesh is kept as it was." }));
check(swap!.detail.endsWith("The earlier mesh is kept as it was.") && !swap!.detail.includes("stays as it was"), `the engine said it already: ${swap!.detail}`);
const failedNew = makeMeshEnded(job({ state: "failed", mesh_kept: false, error: "Tracking failed on 83 % of the frames." }));
check(failedNew!.detail === "Tracking failed on 83 % of the frames. The recording is kept: try again, or at Preview quality.", failedNew!.detail);
check(makeMeshEnded(job({ state: "done", mesh_kept: false })) === null && makeMeshEnded(job()) === null, "done or running: nothing to say");
check(makeMeshEnded(job({ kind: "process_track", state: "cancelled" })) === null, "a stage alone: its own words");
check(makeMeshEnded(job({ kind: "process_recipe", scope: "all", state: "cancelled", mesh_kept: true }))!.detail === kept!.detail, "a recipe on everything says it too");

// R15, R22: the new run's stages, from `making` alone; one running at a time; the edits from the plan
const making: MakingState = {
  stages: { track: { state: "done", summary: "Tracked 1,212 of 1,321 frames" }, straighten: { state: "running", summary: null }, fuse: { state: "none", summary: null }, spacetime: { state: "none", summary: null }, refine: { state: "none", summary: null } },
  plan: [
    { stage: "track", from: 0, to: 0.47 },
    { stage: "straighten", from: 0.47, to: 0.84 },
    { stage: "refine", from: 0.84, to: 0.87 },
    { stage: "edits", from: 0.87, to: 1 },
  ],
  elapsed_s: 600,
  left_s: 500,
};
const rows = makingRows(making, 0.55);
check(rows.map((r) => `${r.id}:${r.state}`).join(" ") === "track:done straighten:running refine:waiting edits:waiting", rows.map((r) => `${r.id}:${r.state}`).join(" "));
check(rows.map((r) => r.label).join(", ") === "Follow the camera, Straighten, Refine with lasers, The edits", rows.map((r) => r.label).join(", "));
check(rows.filter((r) => r.state === "running").length === 1, "one running");
check(rows[0].summary === "Tracked 1,212 of 1,321 frames", "a finished stage's summary");
// the edits: no stage runs, the progress is in their span
const late: MakingState = { ...making, stages: { ...making.stages, straighten: { state: "done", summary: null }, refine: { state: "skipped", summary: "Nothing to change" } } };
check(makingRows(late, 0.9).map((r) => r.state).join(" ") === "done done skipped running", makingRows(late, 0.9).map((r) => r.state).join(" "));
// Straighten's inner fuse runs: still the Straighten row, still one running
const fusing: MakingState = { ...making, stages: { ...making.stages, straighten: { state: "done", summary: null }, fuse: { state: "running", summary: null } } };
check(makingRows(fusing, 0.7).filter((r) => r.state === "running").map((r) => r.id).join() === "straighten", "the fuse inside Straighten");
// two stages that say running (never from the engine): only the first is shown running
const two: MakingState = { stages: { track: { state: "running", summary: null }, straighten: { state: "running", summary: null } }, plan: making.plan };
check(makingRows(two, 0.2).filter((r) => r.state === "running").length === 1, "never two running");
// no plan (an older engine): the stages it reports, in order
const noPlan: MakingState = { stages: { track: { state: "done", summary: null }, straighten: { state: "running", summary: null }, fuse: { state: "none", summary: null }, refine: { state: "none", summary: null } } };
check(makingRows(noPlan, 0.5).map((r) => r.id).join(" ") === "track straighten refine", makingRows(noPlan, 0.5).map((r) => r.id).join(" "));

// ENGINE_PLAN X5 step 2: "Make only the thin walls finer" as a part of its own, when the scan chose it
const finer: MakingState = {
  stages: { ...making.stages, straighten: { state: "done", summary: null }, fine_walls: { state: "running", summary: null } },
  plan: [
    { stage: "track", from: 0, to: 0.41 },
    { stage: "straighten", from: 0.41, to: 0.73 },
    { stage: "fine_walls", from: 0.73, to: 0.86 },
    { stage: "refine", from: 0.86, to: 0.89 },
    { stage: "edits", from: 0.89, to: 1 },
  ],
};
const fr = makingRows(finer, 0.8);
check(fr.map((r) => `${r.id}:${r.state}`).join(" ") === "track:done straighten:done fine_walls:running refine:waiting edits:waiting", fr.map((r) => `${r.id}:${r.state}`).join(" "));
check(fr[2].label === "Make only the thin walls finer" && makingNow(finer, 0.8) === "Make only the thin walls finer", fr[2].label);

check(makingNow(making, 0.55) === "Straighten", `now: ${makingNow(making, 0.55)}`);
check(makingNow(late, 0.9) === "the edits", `now: ${makingNow(late, 0.9)}`);
check(makingNow({ stages: {} }, 0.5) === null, "nothing known");

// R18: the time left of the whole job, from `left_s`; never one stage's frame counter while others are to come
const frames = { stage: "track", done: 1200, total: 1321, fps: 6, elapsed_s: 200 };
const whole = timeLeft(job({ progress: 0.55, frames }), making);
check(whole?.text === `About 8${N}min left for the whole mesh (now: Straighten)`, `whole: ${whole?.text}`);
check(timeLeft(job({ progress: 0.02 }), { ...making, left_s: null })?.text === "Working out the time left… (now: Straighten)", "under 3 %: measuring");
check(timeLeft(job({ progress: 0.995 }), { ...late, left_s: 4 })?.text === "A few seconds left for the whole mesh (now: the edits)", "at the end, for the whole job");
// a whole run before the state is read (or an older engine): the stage's estimate, named, and what follows
const before = timeLeft(job({ frames: { stage: "track", done: 20, total: 920, fps: 6, elapsed_s: 3.2 } }), null);
check(before?.text === `About 3${N}min left for Follow the camera, then the other stages`, `no making: ${before?.text}`);
check(timeLeft(job({ frames: { stage: "track", done: 920, total: 920, fps: 6, elapsed_s: 150 } }), null) === null, "the tail without making: nothing");
// a stage run alone: today's estimate, for its stage
const alone = timeLeft(job({ kind: "process_track", frames: { stage: "track", done: 20, total: 920, fps: 6, elapsed_s: 3.2 } }), null);
check(alone?.text === `About 3${N}min left for Follow the camera`, `alone: ${alone?.text}`);
const fuseAlone = timeLeft(job({ kind: "process_fuse", frames: { stage: "fuse", done: 300, total: 900, fps: 10, elapsed_s: 30 } }), null);
check(fuseAlone?.text === `About 1${N}min left for Build the surface`, `fuse alone: ${fuseAlone?.text}`);
// a stage alone ignores a `making` it does not belong to, and a finished job says nothing
check(timeLeft(job({ kind: "process_track", frames: { stage: "track", done: 20, total: 920, fps: 6, elapsed_s: 3.2 } }), making)?.text === alone?.text, "a stage alone: not the run's time");
check(timeLeft(job({ state: "done" }), making) === null && timeLeft(job({ state: "queued" }), making) === null, "not running");

// R17: how long making the mesh took (the whole Make mesh), else the last stage's time, named as such
const made = processingTime({ job_id: "j", kind: "process_scan", when: "", seconds: 1563, stages: { track: 700 } }, 447);
check(made?.label === "Making the mesh took" && made.value === `26${N}min 3${N}s`, JSON.stringify(made));
const last = processingTime(null, 447);
check(last?.label === "The last stage took" && last.value === `7${N}min 27${N}s` && /not counted/.test(last.title ?? ""), JSON.stringify(last));
check(processingTime(undefined, null) === null, "nothing known");

console.log(`making.test: ${checks} checks passed`);
