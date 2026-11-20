/**
 * Checks for the words of "what a scan keeps" and of freeing its space (FEATURE_PLAN R2; `npm test`). Plain
 * TypeScript that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { ProjectKeepPlan, ScanDiskUse, ScanKeepPlan } from "../api/types.ts";
import {
  KEEP_CHOICE,
  KEEP_CHOICES,
  canRemake,
  confirmStandard,
  doneLine,
  keepLabel,
  keepTitle,
  keptLine,
  lossLines,
  planLine,
  projectDoneLines,
  projectPlanLines,
  removeLines,
  writeLines,
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
check(KEEP_CHOICES.join(",") === "engineering,standard", "two lighter choices, in order");
check(KEEP_CHOICE.engineering.title === "Keep the recording and the result", "engineering title");
check(KEEP_CHOICE.standard.title === "Keep the result only", "standard title");
check(KEEP_CHOICE.standard.text.endsWith("This cannot be undone."), "standard says it cannot be undone");
for (const c of KEEP_CHOICES) check(!/retention|mode/i.test(KEEP_CHOICE[c].title + KEEP_CHOICE[c].text), `no jargon: ${c}`);

// the row's label and its hover text
check(keepLabel(undefined) === "" && keepLabel("full") === "", "a scan that keeps everything says nothing");
check(keepLabel("engineering") === "Keeps recording and result" && keepLabel("standard") === "Keeps result only", "labels");
const freed = { keep: "engineering" as const, freed: [{ when: "2026-11-19T10:00:00", keep: "engineering" as const, bytes: 196 * MB }] };
const t = keepTitle(freed, null, () => "19 Nov");
check(t.startsWith("To free space (19 Nov), its processing stages and undo history were removed: 196.0 MB freed."), `title: ${t}`);
check(t.includes("Make mesh rebuilds the stages"), "engineering: how to get them back");
const again = keepTitle(freed, disk({ stages_bytes: 40 * MB, undo: 3 * MB }));
check(again.includes("The mesh was made again since: stage files take 40.0 MB again."), `made again since: ${again}`);
const std = keepTitle({ keep: "standard", freed: [{ when: "x", keep: "standard", bytes: 4 * GB }] });
check(std.includes("its recording, processing stages and undo history were removed: 4.00 GB freed") && std.includes("cannot be made again"), `standard title: ${std}`);
check(keepTitle({ keep: "full" }) === "", "full: no title");

// whether the mesh can be made again
check(canRemake({}) && canRemake({ has_recording: true, keep: "engineering" }), "a scan with its recording can be remade");
check(!canRemake({ has_recording: false }) && !canRemake({ keep: "standard" }) && !canRemake({ merged_from: [{ id: "a", name: "A" }] }), "no recording: no remake");

// the dialog's lines
check(removeLines(plan()).join("|") === "The undo history: 76.0 MB|The processing stages: 120.0 MB", `remove lines: ${removeLines(plan()).join("|")}`);
check(
  removeLines(plan({ remove: [{ part: "recording", bytes: 2 * GB, files: 900 }] }))[0] === "The recording (camera frames, motion sensor, events): 2.00 GB",
  "recording line",
);
const losses = lossLines(["undo", "stages", "remake", "merge", "refine", "frames"]);
check(losses.length === 6 && losses.every((l) => /^[A-Z].*\.$/.test(l)), "one sentence per loss");
check(losses[0].startsWith("Undo the edits made so far") && losses[3] === "Merge this scan with others. Aligning still works.", "loss words");
check(keptLine(["recording", "result"]) === "It keeps the recording and the mesh with its confidence.", `kept two: ${keptLine(["recording", "result"])}`);
check(keptLine(["result", "laser_points", "features"]) === "It keeps the mesh with its confidence, the laser points and the kept features.", "kept three");
check(keptLine([]) === "", "nothing kept: no line");
check(planLine(plan()) === "Frees 196.0 MB.", `plan line: ${planLine(plan())}`);
// what the move writes: one plain line each, and the parts less the writes are the "Frees" number
const p0 = plan();
check(p0.remove.reduce((s, x) => s + x.bytes, 0) - p0.writes.reduce((s, w) => s + w.bytes, 0) === p0.frees_bytes, "the lines add up");
check(writeLines(p0).join("|") === "The processing record is written again without the history: 2 KB smaller", `write line: ${writeLines(p0).join("|")}`);
check(writeLines(plan({ writes: [{ what: "state", bytes: 300 }] }))[0] === "The processing record is written again: 300 B larger", "a larger record");
check(writeLines(plan({ writes: [], kept_step_bytes: Math.round(18.3 * MB) }))[0] === "The current mesh stays as the starting point for new edits: 18.3 MB (not freed)", "the kept step");
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
check(pl.text === "Frees 296.0 MB on 2 scans.", `project line: ${pl.text}`);
check(pl.skipped.length === 1 && pl.skipped[0] === "Busy one: this scan is busy: it is being processed.", `skipped: ${pl.skipped.join("|")}`);
check(projectPlanLines({ ...pp, n_scans: 0, frees_bytes: 0 }).text.startsWith("Nothing to free"), "nothing in the project");
check(projectPlanLines({ ...pp, n_scans: 1 }).text.endsWith("on 1 scan."), "one scan, singular");

// the second question (Standard) and the toasts
const c = confirmStandard({ name: "Bracket top" }, 4 * GB);
check(c.title === "Delete the recording for good?" && c.button === "Delete the recording", "second question title and button");
check(c.text.startsWith("The recording of “Bracket top” will be deleted from disk (4.00 GB freed in all). This cannot be undone"), `second question: ${c.text}`);
check(confirmStandard({ scans: 3 }, GB).text.startsWith("The recording of 3 scans"), "the project's second question");
check(doneLine(196 * MB, "engineering", "Bracket top") === "Freed 196.0 MB. “Bracket top” now keeps its recording and its result.", "done, engineering");
check(doneLine(GB, "standard", "B") === "Freed 1.00 GB. “B” now keeps its result only.", "done, standard");
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
check(pd.text === "Freed 296.0 MB on 2 scans.", `project done: ${pd.text}`);
check(pd.failed.length === 1 && pd.failed[0].startsWith("D: this scan is busy"), "only real refusals are listed");

console.log(`keepMode.test: ${checks} checks passed`);
