/**
 * Checks for the demo engine's second pass (`npm test`, FEATURE_PLAN N2): the refusals at Start in the
 * engine's words, the steps after Stop one after the other, a cancelled step, an alignment that is not
 * accepted (nothing merged), Continue, and the before and after. Plain TypeScript that Node runs as it is
 * (type stripping), no test framework: a failed check throws.
 */
import type { CheckResult, Job, Project, Scan } from "../types.ts";
import { HttpError } from "./host.ts";
import { MockPasses, type PassHost } from "./pass.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`mock pass.test: ${what}`);
}
function refused(fn: () => unknown): HttpError | null {
  try {
    fn();
  } catch (e) {
    if (e instanceof HttpError) return e;
    throw e;
  }
  return null;
}

const area = (mm2: number) => ({ kind: "uncertain" as const, area_mm2: mm2, share: mm2 / 1000, center: [0, 0, 0] as [number, number, number], extent_mm: 5, radius_mm: 3, n_faces: 4, sigma_median_mm: 0.4, sigma_worst_mm: 0.9, faces: "AQAAAA==" });
const answer: CheckResult = { version: 3, tolerance_mm: 0.3, k: 2, has_sigma: true, area_mm2: 1000, min_area_mm2: 4, share_ok: 0.7, share_uncertain: 0.3, share_not_measured: 0, n_areas: 2, areas: [area(120), area(40)] };
const scan = (id: string, name: string, extra: Partial<Scan> = {}): Scan => ({
  id,
  name,
  created: "now",
  mode: "markerless",
  n_frames: 60,
  duration_s: 2,
  status: "processed",
  result: { mesh_url: `/m/${id}.ply`, stl_url: "", laser_points_url: null, report: {} },
  has_recording: true,
  ...extra,
});
const first = scan("s1", "Top");
const project: Project = { id: "p1", name: "Bracket", created: "now", modified: "now", n_scans: 1, thumbnail_url: null, scans: [first] };

const jobs = new Map<string, Job>();
let n = 0;
let accept = true;
const made = (kind: string, result: Record<string, unknown> | null = null): Job => {
  const j: Job = { id: `j-${++n}`, kind, state: "running", progress: 0, message: "", result, error: null, created: "now" };
  jobs.set(j.id, j);
  return j;
};
const logs: string[] = [];
const host: PassHost = {
  makeMesh: () => made("process_scan"),
  align: (_p, body) => made("align", { accepted: accept, verdict: accept ? "Aligned: 80 % within 1 mm." : "Not aligned: not sure.", T: [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], target: body.target, source: body.source }),
  merge: (p, body) => {
    const m = scan("m1", String(body.name), { merged_from: [{ id: "s1", name: "Top" }] });
    return made("merge", { scan_id: m.id, scan: m, _add: () => p.scans.push(m) });
  },
  check: () => answer,
  job: (jid) => jobs.get(jid),
  log: (_l, m) => void logs.push(m),
};
const passes = new MockPasses(host);
const finish = (jid: string | undefined, state: Job["state"] = "done") => {
  const j = jid ? jobs.get(jid) : undefined;
  check(!!j, `a job to finish (${jid})`);
  j!.state = state;
  const add = (j!.result as { _add?: () => void } | null)?._add;
  if (state === "done" && add) add();
  passes.tick([project]);
};

// ---------------------------------------------------------------- Start: the refusals, then the link
const err = (body: unknown) => refused(() => passes.link(project, body));
check(err({ scan_id: "nope", tolerance_mm: 0.3 })?.status === 404, "an unknown scan");
check(err({ scan_id: "s1", tolerance_mm: 0 })?.detail.includes("tolerance") === true, "a tolerance of 0");
check(err({ scan_id: "s1", tolerance_mm: 0.3, areas: [5] })?.detail.includes("not in the Check answer") === true, "an area not in the answer");
check(err({ scan_id: "s1", tolerance_mm: 0.3, areas: [0, 0] })?.detail.includes("twice") === true, "an area twice");
check(err({ scan_id: "s1", tolerance_mm: 0.3, version: 9, areas: [0] })?.status === 409, "another mesh version");
project.scans.push(scan("s0", "No mesh", { result: null, status: "recorded" }));
check(err({ scan_id: "s0", tolerance_mm: 0.3 })?.detail.includes("has no mesh yet") === true, "a scan without a mesh");
project.scans.pop();
const { link, faces } = passes.link(project, { scan_id: "s1", tolerance_mm: 0.3, version: 3, areas: [0, 1] });
check(link.scan_name === "Top" && link.areas.length === 2 && link.same_setup && link.merge_problem === null && faces.length === 2, "the link");
check(link.chain.state === "waiting", "waiting for Stop");

// ---------------------------------------------------------------- after Stop: the steps, a cancel, Continue
const pass = scan("s2", "Pass", { continues: link, status: "recorded", result: null });
project.scans.push(pass);
passes.started(pass, faces);
passes.afterStop(project, pass);
check(link.chain.state === "processing" && !!link.chain.jobs.process, "Stop: the pass's mesh first");
finish(link.chain.jobs.process, "cancelled");
check(link.chain.state === "cancelled" && link.chain.message.includes("kept as its own scan"), "a cancelled step keeps the pass");
check(refused(() => passes.compare(project, pass, {}))?.status === 409, "nothing to compare before the merge");
passes.run(project, pass);
check(link.chain.state === "processing", "Continue: the mesh again");
check(refused(() => passes.run(project, pass))?.detail.includes("running already") === true, "not twice at once");
pass.result = { mesh_url: "/m/s2.ply", stl_url: "", laser_points_url: null, report: {} };
accept = false;
finish(link.chain.jobs.process);
check(link.chain.state === "aligning", "then the alignment");
finish(link.chain.jobs.align);
check(link.chain.state === "not_aligned" && link.chain.message.includes("Nothing was merged") && link.chain.message.includes("Align page"), "not accepted: nothing merged, and where to go");
check(project.scans.length === 2, "no merged scan");
accept = true;
const v = passes.run(project, pass);
check(v.chain.state === "aligning" && !v.chain.jobs.merge, "Continue: align again (the mesh is there)");
finish(link.chain.jobs.align);
check(link.chain.state === "merging", "accepted: merge");
finish(link.chain.jobs.merge);
check(link.chain.state === "merged" && link.chain.merged_scan_id === "m1" && project.scans.length === 3, "merged into a new scan");
check(refused(() => passes.run(project, pass))?.detail.includes("merged already") === true, "nothing left to run");

// ---------------------------------------------------------------- before and after
const c = passes.compare(project, pass, {});
check(c.first.name === "Top" && c.merged.name === "Top + Pass" && (c.merged.share_ok ?? 0) > (c.first.share_ok ?? 0), "the merged share is higher");
check(c.areas.length === 2 && c.areas.some((a) => a.state === "passes") && c.note.includes("does not yet"), "the asked areas, and what a merge does not do");
check(c.common!.after_ok! > c.common!.before_ok! && c.missing!.why === null && link.setup_words!.startsWith("The part was not moved"), "on the surface both hold; the setup in words");
const view = passes.view(project, pass);
check(view.first.name === "Top" && view.areas_faces?.length === 2 && view.steps.length === 3, "the view");
check(refused(() => passes.view(project, first))?.detail.includes("is not a second pass") === true, "an ordinary scan has no pass");

console.log(`mock pass.test: ${checks} checks passed`);
