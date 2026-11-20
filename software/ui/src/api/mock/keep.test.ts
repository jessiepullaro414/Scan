/**
 * Checks for the demo backend's "what a scan keeps" (R2, keep.ts; `npm test`): the plan frees what the move
 * frees, Standard asks twice and then refuses what needs the recording, a busy or merged scan is refused in
 * the engine's words.
 */
import type { Project, Scan } from "../types.ts";
import { HttpError } from "./host.ts";
import { keepModeArg, keepThinArg, MockKeep } from "./keep.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`mock keep.test: ${what}`);
}
function throws(f: () => unknown, needle: string, what: string): void {
  try {
    f();
  } catch (e) {
    check(e instanceof HttpError && e.status === 409 && e.detail.includes(needle), `${what}: ${(e as Error).message}`);
    return;
  }
  check(false, `${what}: no refusal`);
}

const scan = (id: string, over: Partial<Scan> = {}): Scan => ({
  id,
  name: id.toUpperCase(),
  created: "2026-11-19T10:00:00",
  mode: "markerless",
  n_frames: 300,
  duration_s: 10,
  status: "processed",
  result: { mesh_url: "m", stl_url: "s", laser_points_url: null, report: {} } as Scan["result"],
  ...over,
});
const project: Project = {
  id: "p",
  name: "P",
  created: "x",
  modified: "x",
  n_scans: 4,
  thumbnail_url: null,
  scans: [scan("a"), scan("b"), scan("c", { status: "processing" }), scan("m", { merged_from: [{ id: "a", name: "A" }] })],
};
const k = new MockKeep();
const [a, b, c, m] = project.scans;

// Engineering: the plan's bytes are what the disk loses
const before = k.scanDisk(project, a).bytes;
const pe = k.plan(project, a, "engineering");
check(pe.ok && pe.frees_bytes > 0 && !pe.confirm && pe.loses.includes("stages"), "engineering plan");
check(pe.remove.reduce((s, x) => s + x.bytes, 0) - pe.writes.reduce((s, w) => s + w.bytes, 0) === pe.frees_bytes, "the lines add up");
check(k.scanDisk(project, a).bytes === before, "a plan changes nothing");
const r = k.apply(project, a, "engineering", false);
check(r.freed_bytes === pe.frees_bytes && before - k.scanDisk(project, a).bytes === pe.frees_bytes, "freed what it said");
check(a.keep === "engineering" && a.has_recording === true && a.freed?.length === 1, "recorded on the scan");
check(k.refusal(project, a, "stage")?.detail.includes("removed to free space") === true, "a stage on its own is refused");
check(k.refusal(project, a, "remake") === null, "make mesh still works");
throws(() => k.apply(project, a, "engineering", false), "nothing to free", "nothing left to free");
k.remade(project, a);
check(k.plan(project, a, "engineering").frees_bytes > 0, "made again: there is something to free again");

// Standard: asks twice, then the recording is gone and what needs it says so
const ps = k.plan(project, b, "standard");
check(ps.ok && ps.confirm && ps.loses.includes("remake") && ps.remove.some((x) => x.part === "recording"), "standard plan");
throws(() => k.apply(project, b, "standard", false), "cannot be undone", "the second yes is needed");
k.apply(project, b, "standard", true);
check(b.keep === "standard" && b.has_recording === false, "standard recorded");
check(k.refusal(project, b, "remake")?.detail.includes("recording was removed") === true, "make mesh refused");
throws(() => k.apply(project, b, "engineering", false), "cannot keep its recording again", "no way back");

// busy, merged, and the whole project
check(!k.plan(project, c, "engineering").ok && k.plan(project, c, "engineering").refusal!.detail.includes("busy"), "busy scan refused");
check(k.plan(project, m, "engineering").refusal!.detail.includes("no recording of its own"), "merged: no engineering");
check(k.plan(project, m, "standard").ok, "merged: the result only");
const pp = k.projectPlan(project, "standard");
check(pp.frees_bytes === pp.scans.filter((x) => x.ok).reduce((s, x) => s + x.frees_bytes, 0) && pp.confirm, "project plan adds up");
const done = k.projectApply(project, "standard", true);
check(done.freed_bytes === pp.frees_bytes && done.results.find((x) => x.scan_id === "c")!.ok === false, "project move");
check(k.projectDisk(project).bytes === k.projectDisk(project).scans.reduce((s, x) => s + x.bytes, 0) + k.projectDisk(project).other, "disk adds up");
check(keepModeArg("standard") === "standard", "a mode");
try {
  keepModeArg("sometimes");
  check(false, "an unknown mode is refused");
} catch (e) {
  check(e instanceof HttpError && e.status === 422, "an unknown mode is a 422");
}

// R3: only the frames Standard quality uses (a 60 frames a second scan; "engineering" with thin)
const p3: Project = { ...project, id: "p3", scans: [scan("f", { n_frames: 600, duration_s: 10 }), scan("t", { n_frames: 300, duration_s: 10 })] };
const [f60, t30] = p3.scans;
const b0 = k.scanDisk(p3, f60).bytes;
const pt = k.plan(p3, f60, "engineering", true);
check(pt.ok && pt.thin === true && pt.confirm && pt.loses.includes("best") && pt.keeps.includes("recording"), "thin plan");
check(pt.thinned?.step === 2 && pt.thinned.frames === 300 && pt.thinned.frames_before === 600 && pt.thinned.fps === 30, `what it holds after: ${JSON.stringify(pt.thinned)}`);
const fr = pt.remove.find((x) => x.part === "frames");
check(!!fr && fr.files === 300 && fr.bytes > 0, "the frames it removes");
check(pt.remove.reduce((s, x) => s + x.bytes, 0) - pt.writes.reduce((s, w) => s + w.bytes, 0) === pt.frees_bytes, "thin: the lines add up");
check(pt.frees_bytes > k.plan(p3, f60, "engineering").frees_bytes && !k.plan(p3, f60, "engineering").confirm, "more than without, and only it asks twice");
check(k.scanDisk(p3, f60).bytes === b0, "a thin plan changes nothing");
throws(() => k.apply(p3, f60, "engineering", false, true), "cannot be undone", "thin: the second yes is needed");
const rt = k.apply(p3, f60, "engineering", true, true);
check(rt.freed_bytes === pt.frees_bytes && b0 - k.scanDisk(p3, f60).bytes === pt.frees_bytes, "thin: freed what it said");
check(f60.keep === "engineering" && f60.thinned?.fps === 30 && f60.n_frames === 300 && f60.has_recording === true && f60.freed?.at(-1)?.thin === true, "thinned, recorded");
check(k.refusal(p3, f60, "remake") === null, "thinned: make mesh still works");
const again3 = k.plan(p3, f60, "engineering", true);
check(!again3.remove.some((x) => x.part === "frames") && !again3.confirm, "nothing left to thin");
const p30 = k.plan(p3, t30, "engineering", true);
check(!p30.ok && p30.refusal!.detail.includes("every one of them"), `30 frames a second: ${p30.refusal?.detail}`);
const pp3 = k.projectPlan(p3, "engineering", true);
check(pp3.thin === true && pp3.scans.find((x) => x.scan_id === "t")!.ok === false, "project thin plan");
try {
  keepThinArg("true", "standard");
  check(false, "thin with standard is refused");
} catch (e) {
  check(e instanceof HttpError && e.status === 422 && e.detail.includes("recording and the result"), "thin with another mode: 422");
}
check(keepThinArg("true", "engineering") === true && keepThinArg(undefined, "standard") === false, "thin from a query");

console.log(`mock keep.test: ${checks} checks passed`);
