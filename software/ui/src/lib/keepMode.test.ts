/**
 * Checks for the words of "what a scan keeps" and of freeing its space (FEATURE_PLAN R2; `npm test`). Plain
 * TypeScript that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { ProjectKeepPlan, ScanDiskUse, ScanKeepPlan } from "../api/types.ts";
import {
  KEEP_CHOICE,
  KEEP_CHOICES,
  canRemake,
  choiceArgs,
  confirmStandard,
  confirmThin,
  fpsText,
  thinnedLine,
  doneLine,
  keepLabel,
  keepTitle,
  keptLine,
  lossLines,
  planLine,
  projectDoneLines,
  projectPlanLines,
  removeLines,
  remakeBlock,
  stageBlock,
  writeLines,
  BLOCK,
  type StageAction,
  type StagesLite,
} from "./keepMode.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`keepMode.test: ${what}`);
}

const MB = 1024 * 1024;
const GB = 1024 * MB;

const plan = (over: Partial<ScanKeepPlan> = {}): ScanKeepPlan => ({
  scan_id: "scan-1",
  name: "Bracket top",
  keep: "full",
  to: "engineering",
  ok: true,
  refusal: null,
  bytes: 5 * GB,
  frees_bytes: 120 * MB + 76 * MB + 2048,
  remove: [
    { part: "undo", bytes: 76 * MB, files: 11 },
    { part: "stages", bytes: 120 * MB, files: 12 },
  ],
  writes: [{ what: "state", bytes: -2048 }],
  kept_step_bytes: 0,
  loses: ["undo", "stages"],
  keeps: ["recording", "result", "laser_points", "trajectory", "report", "calibration", "features"],
  confirm: false,
  ...over,
});

const disk = (over: Partial<ScanDiskUse> = {}): ScanDiskUse => ({
  id: "scan-1",
  name: "Bracket top",
  n_frames: 10,
  duration_s: 1,
  bytes: 0,
  recording: 0,
  frames: 0,
  n_frame_files: 0,
  stages: {},
  stages_bytes: 0,
  undo: 0,
  result: 0,
  other: 0,
  ...over,
});

// the choices, in order, in plain words (no jargon such as "retention mode")
check(KEEP_CHOICES.join(",") === "engineering,thinned,standard", "three lighter choices, in order (R3: the frames Standard uses between)");
check(KEEP_CHOICE.engineering.title === "Keep the recording and the result", "engineering title");
check(KEEP_CHOICE.standard.title === "Keep the result only", "standard title");
check(KEEP_CHOICE.standard.text.endsWith("This cannot be undone."), "standard says it cannot be undone");
for (const c of KEEP_CHOICES) check(!/retention|mode/i.test(KEEP_CHOICE[c].title + KEEP_CHOICE[c].text), `no jargon: ${c}`);

// the row's label and its hover text
check(keepLabel(undefined) === "" && keepLabel("full") === "", "a scan that keeps everything says nothing");
check(keepLabel("engineering") === "Keeps the recording and the result" && keepLabel("standard") === "Keeps the result only", "labels");
const freed = { keep: "engineering" as const, freed: [{ when: "2026-11-19T10:00:00", keep: "engineering" as const, bytes: 196 * MB }] };
const t = keepTitle(freed, null, () => "19 Nov");
check(t.startsWith("To free space (19 Nov), its processing stages and undo history were removed: 196.0\u202fMB freed."), `title: ${t}`);
check(t.includes("Make mesh rebuilds the stages"), "engineering: how to get them back");
const again = keepTitle(freed, disk({ stages_bytes: 40 * MB, undo: 3 * MB }));
check(again.includes("The mesh was made again since: stage files take 40.0\u202fMB again."), `made again since: ${again}`);
const std = keepTitle({ keep: "standard", freed: [{ when: "x", keep: "standard", bytes: 4 * GB }] });
check(std.includes("its recording, processing stages and undo history were removed: 4.00\u202fGB freed") && std.includes("cannot be made again"), `standard title: ${std}`);
check(keepTitle({ keep: "full" }) === "", "full: no title");

// whether the mesh can be made again
check(canRemake({}) && canRemake({ has_recording: true, keep: "engineering" }), "a scan with its recording can be remade");
check(!canRemake({ has_recording: false }) && !canRemake({ keep: "standard" }) && !canRemake({ merged_from: [{ id: "a", name: "A" }] }), "no recording: no remake");

// the dialog's lines
check(removeLines(plan()).join("|") === "The undo history: 76.0\u202fMB|The processing stages: 120.0\u202fMB", `remove lines: ${removeLines(plan()).join("|")}`);
check(
  removeLines(plan({ remove: [{ part: "recording", bytes: 2 * GB, files: 900 }] }))[0] === "The recording (camera frames and motion sensor): 2.00\u202fGB",
  "recording line",
);
const losses = lossLines(["undo", "stages", "remake", "merge", "refine", "frames"]);
check(losses.length === 6 && losses.every((l) => /^[A-Z].*\.$/.test(l)), "one sentence per loss");
check(losses[0].startsWith("Undo the edits made so far") && losses[3] === "Merge this scan with others. Aligning still works.", "loss words");
check(keptLine(["recording", "result"]) === "It keeps the recording and the mesh with its confidence.", `kept two: ${keptLine(["recording", "result"])}`);
check(keptLine(["result", "laser_points", "features"]) === "It keeps the mesh with its confidence, the laser points and the saved fitted shapes.", "kept three");
check(keptLine([]) === "", "nothing kept: no line");
check(planLine(plan()) === "Frees 196.0\u202fMB.", `plan line: ${planLine(plan())}`);
// what the move writes: one plain line each, and the parts less the writes are the "Frees" number
const p0 = plan();
check(p0.remove.reduce((s, x) => s + x.bytes, 0) - p0.writes.reduce((s, w) => s + w.bytes, 0) === p0.frees_bytes, "the lines add up");
check(writeLines(p0).join("|") === "The processing record is written again without the history: 2\u202fKB smaller", `write line: ${writeLines(p0).join("|")}`);
check(writeLines(plan({ writes: [{ what: "state", bytes: 300 }] }))[0] === "The processing record is written again: 300\u202fB larger", "a larger record");
check(writeLines(plan({ writes: [], kept_step_bytes: Math.round(18.3 * MB) }))[0] === "The current mesh stays as the starting point for new edits: 18.3\u202fMB (not freed)", "the kept step");
check(writeLines(plan({ writes: [], kept_step_bytes: 0 })).length === 0, "nothing written: no line");
check(planLine(plan({ frees_bytes: 0, remove: [] })) === "Frees nothing (nothing of this is on disk).", "frees nothing");
const refused = plan({ ok: false, frees_bytes: 0, refusal: { detail: "this scan is busy: it is being processed", hint: "wait" } });
check(planLine(refused) === "This scan is busy: it is being processed.", `refusal line: ${planLine(refused)}`);

// the whole project
const pp: ProjectKeepPlan = {
  project_id: "p",
  to: "engineering",
  scans: [plan(), plan({ scan_id: "scan-2", name: "Side", frees_bytes: 100 * MB }), { ...refused, scan_id: "scan-3", name: "Busy one" }],
  frees_bytes: 296 * MB,
  n_scans: 2,
  confirm: false,
};
const pl = projectPlanLines(pp);
check(pl.text === "Frees 296.0\u202fMB on 2 scans.", `project line: ${pl.text}`);
check(pl.skipped.length === 1 && pl.skipped[0] === "Busy one: this scan is busy: it is being processed.", `skipped: ${pl.skipped.join("|")}`);
check(projectPlanLines({ ...pp, n_scans: 0, frees_bytes: 0 }).text.startsWith("Nothing to free"), "nothing in the project");
check(projectPlanLines({ ...pp, n_scans: 1 }).text.endsWith("on 1 scan."), "one scan, singular");

// the second question (Standard) and the toasts
const c = confirmStandard({ name: "Bracket top" }, 4 * GB);
check(c.title === "Delete the recording for good?" && c.button === "Delete the recording", "second question title and button");
check(c.text.startsWith("The recording of “Bracket top” will be deleted from disk (4.00\u202fGB freed in all). This cannot be undone"), `second question: ${c.text}`);
check(confirmStandard({ scans: 3 }, GB).text.startsWith("The recording of 3 scans"), "the project's second question");
check(doneLine(196 * MB, "engineering", "Bracket top") === "Freed 196.0\u202fMB. “Bracket top” now keeps the recording and the result.", "done, engineering");
check(doneLine(GB, "standard", "B") === "Freed 1.00\u202fGB. “B” now keeps the result only.", "done, standard");
const pd = projectDoneLines({
  project_id: "p",
  to: "engineering",
  freed_bytes: 296 * MB,
  results: [
    { scan_id: "a", name: "A", ok: true, freed_bytes: 196 * MB },
    { scan_id: "b", name: "B", ok: true, freed_bytes: 100 * MB },
    { scan_id: "c", name: "C", ok: false, freed_bytes: 0, error: { detail: "nothing to free", hint: "nothing to do" } },
    { scan_id: "d", name: "D", ok: false, freed_bytes: 0, error: { detail: "this scan is busy: it is being processed", hint: "wait" } },
  ],
});
check(pd.text === "Freed 296.0\u202fMB on 2 scans.", `project done: ${pd.text}`);
check(pd.failed.length === 1 && pd.failed[0].startsWith("D: this scan is busy"), "only real refusals are listed");
// found in review: the engine's sentences now start with a capital and end with a full stop
const pd2 = projectDoneLines({
  project_id: "p",
  to: "engineering",
  freed_bytes: 0,
  results: [
    { scan_id: "c", name: "C", ok: false, freed_bytes: 0, error: { detail: "There is nothing to free in this scan.", hint: null } },
    { scan_id: "e", name: "E", ok: false, freed_bytes: 0, error: { detail: "There is nothing to free: this scan would not take less space afterwards.", hint: null } },
    { scan_id: "d", name: "D", ok: false, freed_bytes: 0, error: { detail: "This scan is busy: it is being processed.", hint: "Wait." } },
  ],
});
check(pd2.failed.length === 1 && pd2.failed[0] === "D: This scan is busy: it is being processed.", `the engine's new words: ${pd2.failed.join(" | ")}`);

// ---------------------------------------------------------------- what a freed scan can still run (Process page, viewer)
// The engine's rules (ohs/server/processing.py): Make mesh, Track and a recipe on "everything" read the frames;
// Straighten, Fuse and Refine from the images read the frames and the Track stage's files; Refine with lasers reads
// only the base stage's mesh. Freeing removes the stage files but leaves the stages "done" in the record, so a stage's
// files are on disk again only when it finished after the last freeing.
const ALL: StageAction[] = ["make_mesh", "track", "straighten", "fuse", "spacetime", "refine", "recipe_all"];
const FREED_AT = "2026-11-19T10:00:00+00:00";
const st = (when: Partial<Record<"track" | "straighten" | "fuse" | "spacetime" | "refine", string | null>>, base: "track" | "straighten" | "fuse" | "spacetime" | "refine" = "straighten", refineSource: "track" | "straighten" | "fuse" | "spacetime" | null = null): StagesLite => {
  const s = (id: keyof typeof when) => ({ state: when[id] ? ("done" as const) : ("none" as const), when: when[id] ?? null });
  return {
    stages: { track: s("track"), straighten: s("straighten"), fuse: s("fuse"), spacetime: s("spacetime"), refine: { ...s("refine"), source: refineSource } },
    base: { stage: base },
  };
};
const before = "2026-11-18T09:00:00+00:00";
const after = "2026-11-19T10:05:00+00:00";
const madeBefore = st({ track: before, straighten: before });
const full = { keep: "full" as const, has_recording: true };
for (const a of ALL) check(stageBlock(full, madeBefore, a) === null, `a scan that keeps everything can run ${a}`);
check(stageBlock({}, madeBefore, "fuse") === null, "an older engine (no keep fields): nothing is blocked");

// recording and result: the stages are gone, the recording stays
const eng = { keep: "engineering" as const, has_recording: true, freed: [{ when: FREED_AT, keep: "engineering" as const, bytes: 1 }] };
for (const a of ["make_mesh", "track", "recipe_all"] as const) check(stageBlock(eng, madeBefore, a) === null, `recording and result: ${a} still runs`);
for (const a of ["straighten", "fuse", "spacetime", "refine"] as const)
  check(stageBlock(eng, madeBefore, a) === BLOCK.stagesGone, `recording and result: ${a} needs the stage files: ${stageBlock(eng, madeBefore, a)}`);
check(BLOCK.stagesGone === "The stage files this needs were removed to free space. Make mesh, or Follow the camera, builds them again from the recording.", `stage files words: ${BLOCK.stagesGone}`);
// Track (or Make mesh) after the freeing rebuilds the stage files: the other stages run again
const retracked = st({ track: after, straighten: before }, "track");
for (const a of ALL) check(stageBlock(eng, retracked, a) === null, `tracked again since: ${a} runs: ${stageBlock(eng, retracked, a)}`);
// a refinement of a stage made again since is fine; of one from before the freeing it is not
check(stageBlock(eng, st({ track: after, straighten: after }, "straighten"), "refine") === null, "refine: its source made again");
check(stageBlock(eng, st({ track: after, refine: before, straighten: before }, "refine", "straighten"), "refine") === BLOCK.stagesGone, "refine: a refined base whose source is from before");
check(stageBlock(eng, st({ track: after, refine: after }, "refine", "track"), "refine") === null, "refine: a refined base of a new track");
// the latest freeing counts, and a freeing with no date blocks nothing (the engine then says it)
const twice = { ...eng, freed: [{ when: "2026-11-01T10:00:00+00:00", keep: "engineering" as const, bytes: 1 }, { when: FREED_AT, keep: "engineering" as const, bytes: 1 }] };
check(stageBlock(twice, st({ track: "2026-11-05T10:00:00+00:00" }, "track"), "fuse") === BLOCK.stagesGone, "tracked between two freeings: gone again");
check(stageBlock({ ...eng, freed: [] }, madeBefore, "fuse") === null, "no date of the freeing: not blocked");
check(stageBlock(eng, null, "fuse") === null && stageBlock(eng, null, "make_mesh") === null, "no process state yet: only the recording decides");
// not tracked at all: the page's own "Run Track first" says it, not this
check(stageBlock(eng, st({}), "straighten") === null, "never tracked: not this rule's to say");

// result only: nothing that reads the frames, and the stage files never come back
const resOnly = { keep: "standard" as const, has_recording: false, freed: [{ when: FREED_AT, keep: "standard" as const, bytes: 1 }] };
for (const a of ["make_mesh", "track", "straighten", "fuse", "spacetime", "recipe_all"] as const)
  check(stageBlock(resOnly, madeBefore, a) === BLOCK.resultOnly, `result only: ${a} needs the recording: ${stageBlock(resOnly, madeBefore, a)}`);
check(stageBlock(resOnly, madeBefore, "refine") === BLOCK.resultOnlyStages, `result only: refine needs stage files: ${stageBlock(resOnly, madeBefore, "refine")}`);
check(BLOCK.resultOnly === "This scan's recording was removed to free space (it keeps its result only). Only a new scan of the part can make the mesh again.", `result only words: ${BLOCK.resultOnly}`);
check(BLOCK.resultOnlyStages === "The stage files this needs were removed to free space (this scan keeps its result only). Only a new scan of the part can bring them back.", `result only stage words: ${BLOCK.resultOnlyStages}`);
check(stageBlock({ keep: "standard", freed: resOnly.freed }, madeBefore, "track") === BLOCK.resultOnly, "result only, has_recording not sent: still no recording");

// a merged scan has no recording of its own (Refine with lasers needs none)
const merged = { merged_from: [{ id: "a", name: "A" }], keep: "full" as const, has_recording: false };
for (const a of ["make_mesh", "track", "straighten", "fuse", "spacetime", "recipe_all"] as const) check(stageBlock(merged, madeBefore, a) === BLOCK.merged, `merged: ${a}`);
check(stageBlock(merged, madeBefore, "refine") === null, "merged: refine with lasers still runs");
check(BLOCK.merged === "This scan was merged from other scans and has no recording of its own. Merge those scans again to make it again.", `merged words: ${BLOCK.merged}`);
// the engine's order: "keeps its result only" first
check(stageBlock({ ...merged, keep: "standard", freed: resOnly.freed }, madeBefore, "track") === BLOCK.resultOnly, "a merged scan freed to its result only");
// a recording missing for another reason
check(stageBlock({ keep: "full", has_recording: false }, madeBefore, "make_mesh") === BLOCK.noRecording, "no recording on disk");
check(BLOCK.noRecording === "This scan has no recording. Record the part again to make a new mesh.", `no recording words: ${BLOCK.noRecording}`);
// every sentence is plain: one or two short sentences, says why and what brings it back
for (const [k, s] of Object.entries(BLOCK)) check(/^[A-Z].*\.$/.test(s) && s.split(". ").length <= 2 && !/retention|mode|409/i.test(s), `plain words: ${k}`);

// the Make mesh / Remake buttons (project page, viewer, Process page): the same rule as make_mesh
check(remakeBlock(full) === null && remakeBlock(eng) === null, "remake: a scan with its recording");
check(remakeBlock(resOnly) === BLOCK.resultOnly && remakeBlock(merged) === BLOCK.merged, "remake: no recording");
for (const s of [full, eng, resOnly, merged, {}]) check((remakeBlock(s) === null) === canRemake(s), "remakeBlock agrees with canRemake");

// ---------------------------------------------------------------- R3: only the frames Standard quality uses
// the choice is the engine's "recording and result" with thin: true, so every rule above for "engineering" holds
check(JSON.stringify(choiceArgs("thinned")) === '{"mode":"engineering","thin":true}', "thinned: engineering with thin");
check(JSON.stringify(choiceArgs("engineering")) === '{"mode":"engineering","thin":false}' && choiceArgs("standard").thin === false, "the others: no thinning");
check(KEEP_CHOICE.thinned.title === "Keep half the frames and the result", `thinned title: ${KEEP_CHOICE.thinned.title}`);
check(KEEP_CHOICE.thinned.text.endsWith("This cannot be undone.") && KEEP_CHOICE.thinned.text.includes("gives the same mesh"), "thinned: same mesh, cannot be undone");
check(!/—/.test(Object.values(KEEP_CHOICE).map((x) => x.title + x.text).join(" ")), "no em dashes");
const thinned = { when: "2026-11-19T22:00:00", fps: 29.98, fps_before: 59.96, step: 2, frames: 1800, frames_before: 3600 };
check(fpsText(29.98) === "30 frames a second" && fpsText(7.5) === "7.5 frames a second" && fpsText(1) === "1 frame a second", "frame rates in words");
check(keepLabel("engineering", thinned) === "Keeps half the frames and the result", `thinned label: ${keepLabel("engineering", thinned)}`);
check(keepLabel("engineering", null) === "Keeps the recording and the result" && keepLabel("standard", thinned) === "Keeps the result only", "labels without, or past, thinning");
check(
  thinnedLine(thinned) === "Its recording holds half the frames: 30 frames a second (1,800 of 3,600 frames, recorded at 60 frames a second).",
  `thinned line: ${thinnedLine(thinned)}`,
);
check(thinnedLine(thinned, true).startsWith("Its recording will hold half the frames") && thinnedLine(null) === "", "the plan's line, and none");
const tt = keepTitle({ keep: "engineering", thinned, freed: [{ when: "x", keep: "engineering", bytes: 3 * GB, thin: true }] }, null, () => "19 Nov");
check(tt.startsWith("To free space (19 Nov), half of the camera frames, its processing stages and undo history were removed: 3.00\u202fGB freed."), `thinned title: ${tt}`);
check(tt.includes("1,800 of 3,600 frames") && tt.includes("at Standard quality it gives the same mesh as before"), `thinned hover: ${tt}`);
check(removeLines(plan({ remove: [{ part: "frames", bytes: 2 * GB, files: 1800 }] }))[0] === "Half of the camera frames: 2.00\u202fGB", "frames line");
const pt = plan({ writes: [{ what: "state", bytes: -2048 }, { what: "frames_list", bytes: -46800 }, { what: "meta", bytes: 160 }] });
check(
  writeLines(pt).join("|") ===
    "The processing record is written again without the history: 2\u202fKB smaller|The list of frames is written again with the frames that stay: 46\u202fKB smaller|The recording's notes say what it holds now: 160\u202fB larger",
  `thinning writes: ${writeLines(pt).join("|")}`,
);
// Best quality's loss is said beforehand, in numbers (measured, VALIDATION.md)
const best = lossLines(["best"])[0];
check(/^Best quality /.test(best) && /\d/.test(best) && best.endsWith(".") && !/TODO|—/.test(best), `best loss in numbers: ${best}`);
check(best.includes("same mesh as Standard"), `best: what it gives then: ${best}`);
const ct = confirmThin({ name: "Bracket top" }, 3 * GB);
check(ct.title === "Delete half the frames?" && ct.button === "Delete half the frames", "thin: second question");
check(ct.text.startsWith("Half of the camera frames of “Bracket top” (the ones Standard quality skips) will be deleted from disk (3.00\u202fGB freed in all). This cannot be undone."), `thin second question: ${ct.text}`);
check(confirmThin({ scans: 2 }, GB).text.includes("of 2 scans"), "the project's second question");
check(doneLine(3 * GB, "engineering", "B", true) === "Freed 3.00\u202fGB. “B” now keeps half the frames and the result.", "done, thinned");
// a thinned scan is a scan that keeps its recording: Make mesh and the stages as for "recording and result"
const engThin = { ...eng, thinned };
check(canRemake(engThin) && remakeBlock(engThin) === null, "thinned: Make mesh runs");
for (const a of ["straighten", "fuse"] as const) check(stageBlock(engThin, madeBefore, a) === BLOCK.stagesGone, `thinned: ${a} as engineering`);

console.log(`keepMode.test: ${checks} checks passed`);
