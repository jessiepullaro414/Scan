/**
 * Checks for the demo engine's align and merge (`npm test`): the server's rules and error replies
 * (APP_API.md "M4 first slice", `ohs/server/aligning.py`), the made-up answers, and the merge job.
 * Plain TypeScript that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { AlignAllResult, AlignResult, Job, MergeResult, PointPair, Project, Scan } from "../types.ts";
import {
  alignAllVerdict,
  alignedVerdict,
  AMBIGUOUS_REASON,
  checkPairs,
  fitsFromShape,
  isPose,
  isRigid,
  MockAlign,
  quotedNames,
  turnedOver,
  type AlignHost,
} from "./align.ts";
import type { WorkJobSpec, WorkStep } from "./host.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`mock align.test: ${what}`);
}

// ------------------------------------------------------------------ a stand-in for the mock engine
type Reply = { status: number; detail: string; hint: string };
type Started = { job: Job; kind: string; spec: WorkJobSpec };

const started: Started[] = [];
const logs: { level: string; message: string }[] = [];
let n = 0;
const host: AlignHost = {
  startWorkJob(kind, spec) {
    const job = { id: `j-${++n}`, kind, state: "running", progress: 0, message: "Starting…", result: null, error: null, created: "now", ...(spec.extra ?? {}) } as Job;
    started.push({ job, kind, spec });
    return job;
  },
  log: (level, message) => void logs.push({ level, message }),
  fail(status, detail, hint) {
    throw { status, detail, hint } satisfies Reply;
  },
  uid: (prefix) => `${prefix}new${++n}`,
  now: () => "2026-11-15T12:00:00.000Z",
};

/** The error reply of a request, or null when it was accepted. */
function refused(fn: () => unknown): Reply | null {
  try {
    fn();
  } catch (e) {
    if (e && typeof e === "object" && "status" in e) return e as Reply;
    throw e;
  }
  return null;
}

/** Run a job's work to its end: the steps it reported, and what its commit returned. */
function run(s: Started): { steps: WorkStep[]; result: Record<string, unknown> | null } {
  const steps: WorkStep[] = [];
  for (;;) {
    const r = s.spec.work.next();
    if (r.done) return { steps, result: r.value() };
    steps.push(r.value);
  }
}
const last = () => started[started.length - 1];

const WRONG = [
  [-1, 0, 0, -76],
  [0, 1, 0, 0],
  [0, 0, -1, 0],
  [0, 0, 0, 1],
];
const IDENTITY = [
  [1, 0, 0, 0],
  [0, 1, 0, 0],
  [0, 0, 1, 0],
  [0, 0, 0, 1],
];

function scan(id: string, name: string, status: Scan["status"], n_frames: number, duration_s: number): Scan {
  const base = `/api/projects/p1/scans/${id}/files`;
  return {
    id,
    name,
    created: "2026-11-15T10:00:00.000Z",
    mode: "markerless",
    n_frames,
    duration_s,
    status,
    result: status === "processed" ? { mesh_url: `${base}/mesh.ply`, stl_url: `${base}/mesh.stl`, laser_points_url: null, report: {} } : null,
  };
}
/** The demo's seeded project: two processed scans of the bracket, and scans that cannot be aligned yet. */
function project(): Project {
  const scans = [
    scan("top", "Bracket top", "processed", 864, 28.8),
    scan("under", "Bracket underside", "processed", 612, 20.4),
    scan("side", "Bracket side", "recorded", 450, 15),
    scan("busy", "Being meshed", "processing", 300, 10),
  ];
  return { id: "p1", name: "Intake manifold bracket", created: "c", modified: "m", n_scans: scans.length, thumbnail_url: null, scans };
}

const mock = new MockAlign(host, WRONG);
const pairs = (list: [number[], number[]][]): PointPair[] => list.map(([source, target]) => ({ source, target }) as PointPair);
const TRIANGLE = pairs([
  [[0, 0, 0], [0, 0, 0]],
  [[40, 0, 0], [40, 0.4, 0]],
  [[0, 30, 5], [0.3, 30, 5]],
]);

// ------------------------------------------------------------------ align: what is refused
let p = project();
let r = refused(() => mock.align(p, { target: "top", source: "top" }));
check(r?.status === 409 && r.detail === "a scan cannot be aligned to itself" && r.hint === "pick two different scans", "the same scan twice: 409");
r = refused(() => mock.align(p, { target: "top", source: "nope" }));
check(r?.status === 404 && /no scan 'nope' in project 'p1'/.test(r.detail), "an unknown scan: 404");
r = refused(() => mock.align(p, { target: "top" }));
check(r?.status === 422, "a scan missing from the request: 422");
r = refused(() => mock.align(p, { target: "side", source: "top" }));
check(r?.status === 409 && r.detail === 'the first scan "Bracket side" has no mesh yet' && /make its mesh/.test(r.hint), "a scan without a mesh: 409, and which one");
r = refused(() => mock.align(p, { target: "top", source: "busy" }));
check(r?.status === 409 && r.detail === 'the second scan "Being meshed" is busy', "a scan being processed: 409");

r = refused(() => mock.align(p, { target: "top", source: "under", pairs: TRIANGLE.slice(0, 2) }));
check(r?.status === 422 && r.detail === "2 pair(s) of points are too few" && r.hint === "pick at least three matching points on each scan", "two pairs: 422");
r = refused(() => mock.align(p, { target: "top", source: "under", pairs: TRIANGLE.slice(0, 1) }));
check(r?.status === 422 && r.detail === "1 pair(s) of points are too few", "one pair: 422");
const LINE = pairs([
  [[0, 0, 0], [0, 0, 0]],
  [[10, 0, 0], [10, 0, 0]],
  [[25, 0, 0], [25, 0, 0]],
]);
r = refused(() => mock.align(p, { target: "top", source: "under", pairs: LINE }));
check(r?.status === 422 && /^the picked points do not give a pose: the picked points lie in one line or on one spot/.test(r.detail), "pairs in one line: 422");
check(r?.hint === "pick three or more matching points that span a triangle", "and what to pick instead");
const SPOT = pairs([
  [[5, 5, 5], [0, 0, 0]],
  [[5.1, 5, 5], [40, 0, 0]],
  [[5, 5.1, 5], [0, 30, 0]],
]);
r = refused(() => mock.align(p, { target: "top", source: "under", pairs: SPOT }));
check(r?.status === 422 && /one line or on one spot/.test(r.detail), "the points of one scan on one spot: 422");
r = refused(() => mock.align(p, { target: "top", source: "under", pairs: [...TRIANGLE.slice(0, 2), { source: [0, 30, NaN], target: [0, 30, 5] }] }));
check(r?.status === 422 && /a picked point is not a number/.test(r.detail), "a point that is not a number: 422");
check(started.length === 0, "a refused request starts no job");

// ------------------------------------------------------------------ align: the answers
const before = JSON.stringify(p);
let job = mock.align(p, { target: "top", source: "under", same_setup: false });
check(job.kind === "align" && last().kind === "align", "an align job");
const meta = job as Job & { project_id?: string; target?: string; source?: string };
check(meta.project_id === "p1" && meta.target === "top" && meta.source === "under", "the job says which project and scans");
let done = run(last());
check(done.steps[0].message === 'Reading "Bracket top"' && done.steps[1].message === 'Reading "Bracket underside"', "it reads the two meshes");
check(done.steps.some((s) => s.message === "Aligning the two scans"), "then it aligns them");
let a = done.result as AlignResult;
check(a.accepted === false, "top and underside, the part moved, no points: NOT accepted");
check(
  a.verdict ===
    "Not aligned: not sure which way it goes: it fits about as well turned round (98 % as well, 61 mm away; a symmetric shape, a repeated feature, or a surface that can slide on itself): pick three or more matching points on both scans, or choose one of the poses.",
  "with the engine's sentence",
);
check(a.reason === AMBIGUOUS_REASON && a.message === a.verdict, "the reason, and the message is the verdict");
check(a.start === "features" && a.pairs_rms_mm === null && a.same_setup === false && a.used_support === false, "from the shape alone, the tables not used");
check(JSON.stringify(a.T) === JSON.stringify(WRONG) && isRigid(a.T), "a pose is still returned, for showing");
check(a.sure === false && a.doubt === "turned" && a.poses?.length === 2, "not sure, with the poses it had in mind (F11)");
check(
  !!a.poses && a.poses[0].chosen && !a.poses[1].chosen && JSON.stringify(a.poses[0].T) === JSON.stringify(a.T) && a.poses.every((q) => isRigid(q.T)),
  "the engine's pose first, then the other",
);
check(a.project_id === "p1" && a.target === "top" && a.source === "under", "the result names the project and the two scans");
check(!("scan_id" in a), "an align result names no scan of its own (no “mesh ready” for it)");
check(JSON.stringify(p) === before, "align writes nothing");

mock.align(p, { target: "top", source: "under" });
a = run(last()).result as AlignResult;
check(a.accepted && a.same_setup === true && a.used_support === true, "same setup (the default): accepted, the table used");
check(a.verdict === "Aligned: 76 % of the smaller scan's part lies within 1 mm of the other scan, typically 0.25 mm; 8 of 9 starting guesses agree.", "the engine's sentence");
check(a.reason === null && a.start === "features" && a.agree === 8 && a.candidates === 9 && a.dof === 6, "its figures");
check(a.overlap === 0.76 && a.rmse_mm === 0.25, "the sentence and the figures agree");
check(JSON.stringify(a.T) === JSON.stringify(IDENTITY), "the demo's scans share a frame: the second stays where it is");

mock.align(p, { target: "top", source: "under", same_setup: false, pairs: [] });
check((run(last()).result as AlignResult).accepted === false, "an empty list of pairs is no pairs");

mock.align(p, { target: "top", source: "under", same_setup: false, pairs: TRIANGLE });
a = run(last()).result as AlignResult;
check(a.accepted && a.start === "given", "with three pairs: accepted, from the given points");
check(/^Aligned: \d+ % of the smaller scan's part lies within 1 mm of the other scan, typically \d\.\d\d mm; from the start you gave\.$/.test(a.verdict), "the engine's sentence for a given start");
check(a.verdict === alignedVerdict(a.overlap, a.rmse_mm!, "given", a.agree, a.candidates), "made from the figures");
check(typeof a.pairs_rms_mm === "number" && a.pairs_rms_mm >= 0 && a.pairs_rms_mm < 1, "well picked pairs agree to a fraction of a mm");
check(a.same_setup === false && a.used_support === false && isRigid(a.T), "the part was moved: no table, a rigid pose");

const OFF = pairs([
  [[0, 0, 0], [0, 0, 0]],
  [[40, 0, 0], [40, 0, 0]],
  [[0, 30, 5], [0, 55, 5]], // this pair does not match
  [[20, 10, 0], [20, 10, 0]],
]);
mock.align(p, { target: "under", source: "top", pairs: OFF });
a = run(last()).result as AlignResult;
check(a.accepted && a.pairs_rms_mm !== null && a.pairs_rms_mm > 5, "a pair that does not match shows as a large pairs_rms_mm");
check(a.target === "under" && a.source === "top", "either scan can be the one that stays");

// ------------------------------------------------------------------ the pure checks
check("rms" in checkPairs(TRIANGLE) && "error" in checkPairs(LINE) && "error" in checkPairs(TRIANGLE.slice(0, 2)), "checkPairs");
const moved = pairs([
  [[0, 0, 0], [100, 50, -20]],
  [[40, 0, 0], [100, 90, -20]],
  [[0, 30, 5], [70, 50, -15]],
]); // the second scan turned a quarter and moved: the same triangle
const c = checkPairs(moved);
check("rms" in c && c.rms < 1e-9, "pairs that a rigid motion explains agree exactly, wherever the scans are");
check(isPose(IDENTITY) && !isPose([[1, 0], [0, 1]]) && !isPose(null) && !isPose([IDENTITY[0], IDENTITY[1], IDENTITY[2], [0, 0, 0, "1"]]), "isPose");
check(isRigid(IDENTITY) && isRigid(WRONG), "rigid motions");
check(!isRigid([[2, 0, 0, 0], IDENTITY[1], IDENTITY[2], IDENTITY[3]]), "a scale is not one");
check(!isRigid([[-1, 0, 0, 0], IDENTITY[1], IDENTITY[2], IDENTITY[3]]), "nor a mirror");
check(!isRigid([IDENTITY[0], IDENTITY[1], IDENTITY[2], [0, 0, 0.1, 1]]) && !isRigid([[1, 0, 0, NaN], IDENTITY[1], IDENTITY[2], IDENTITY[3]]), "nor a bad last row or a NaN");

// ------------------------------------------------------------------ merge: what is refused
r = refused(() => mock.merge(p, { target: "top", source: "under", T: [[1, 0, 0], [0, 1, 0], [0, 0, 1]] }));
check(r?.status === 422 && r.detail === "the pose must be a 4x4 matrix" && r.hint === "send the pose the alignment returned", "a pose that is not 4x4: 422");
r = refused(() => mock.merge(p, { target: "top", source: "under" }));
check(r?.status === 422 && r.detail === "the pose must be a 4x4 matrix", "no pose: 422");
r = refused(() => mock.merge(p, { target: "top", source: "under", T: [[2, 0, 0, 0], IDENTITY[1], IDENTITY[2], IDENTITY[3]] }));
check(r?.status === 422 && r.detail === "the pose is not a rigid motion", "a pose that is not a rigid motion: 422");
r = refused(() => mock.merge(p, { target: "top", source: "top", T: IDENTITY }));
check(r?.status === 409 && r.detail === "a scan cannot be aligned to itself", "the same scan twice: 409");
r = refused(() => mock.merge(p, { target: "top", source: "side", T: IDENTITY }));
check(r?.status === 409 && /"Bracket side" has no mesh yet/.test(r.detail), "a scan without a mesh: 409");

// ------------------------------------------------------------------ merge: cancelled
logs.length = 0;
const count = p.scans.length;
job = mock.merge(p, { target: "top", source: "under", T: IDENTITY });
check(job.kind === "merge" && (job as Job & { target?: string }).target === "top", "a merge job, with the same meta");
const cancelled = last();
for (let i = 0; i < 10; i++) cancelled.spec.work.next();
cancelled.spec.onCancel?.();
check(p.scans.length === count && logs.length === 0, "a cancelled merge adds nothing to the project");

// ------------------------------------------------------------------ merge: done
mock.merge(p, { target: "top", source: "under", T: IDENTITY });
done = run(last());
// UX_PLAN R19: the numbers with thousands grouped ("frame 1,212 of 1,476"); read without the commas
const frames = done.steps.map((s) => /^Merging: frame (\d+) of (\d+)$/.exec(s.message.replace(/(\d),(\d)/g, "$1$2"))).filter((m): m is RegExpExecArray => !!m);
check(frames.length >= 20, "the job reports “Merging: frame k of n”");
check(done.steps.some((s) => / of 1,476$/.test(s.message)), "thousands grouped");
const total = Number(frames[0][2]);
check(total === 864 + 612, "n is the frames of both recordings");
check(frames.every((m) => Number(m[2]) === total), "n stays the same");
check(frames.every((m, i) => i === 0 || Number(m[1]) > Number(frames[i - 1][1])), "k counts up");
check(Number(frames[0][1]) >= 1 && Number(frames[frames.length - 1][1]) === total, "from the first frame to the last");
check(done.steps.every((s, i) => s.p >= 0 && s.p <= 1 && (i === 0 || s.p >= done.steps[i - 1].p)), "the progress only grows");
check(done.steps[done.steps.length - 1].message === "Adding the merged scan to the project", "the last step adds the scan");
check(!done.steps.some((s) => s.message === "Finding what the part lay on"), "same setup: the tables stay in");
check(JSON.stringify((done.result as MergeResult).tables) === "[]" && JSON.stringify((done.result as MergeResult).warnings) === "[]", "same setup: nothing left out, nothing said");

let m = done.result as MergeResult;
check(p.scans.length === count + 1 && p.n_scans === count + 1, "the project has one more scan");
let made = p.scans[p.scans.length - 1];
check(m.project_id === "p1" && m.scan_id === made.id && m.scan.id === made.id, "the result names the new scan");
check(JSON.stringify(m.merged_from) === JSON.stringify(["top", "under"]), "and the two it was made from");
check(made.name === "Bracket top + Bracket underside" && m.message === 'Merged into "Bracket top + Bracket underside"', "the default name");
check(made.status === "processed" && made.mode === "markerless" && made.n_frames === total && made.duration_s === 0, "a processed, markerless scan of the frames fused");
check(!!made.result && made.result.mesh_url.endsWith(`/scans/${made.id}/files/mesh.ply`), "with its own mesh");
check(
  JSON.stringify(made.merged_from) ===
    JSON.stringify([
      { id: "top", name: "Bracket top" },
      { id: "under", name: "Bracket underside" },
    ]),
  "merged_from: the two scans, by id and name",
);
check(logs.length === 1 && logs[0].level === "info" && logs[0].message === "Merged scan ready: Bracket top + Bracket underside", "the notification");
check(p.scans[0].name === "Bracket top" && p.scans[0].merged_from === undefined && p.scans[1].status === "processed", "the two scans are unchanged");

mock.merge(p, { target: "under", source: "top", T: WRONG, same_setup: false, name: "  Whole bracket  " });
done = run(last());
check(done.steps[0].message === "Finding what the part lay on", "the part was moved: each scan's table is found first");
m = done.result as MergeResult;
made = p.scans[p.scans.length - 1];
check(made.name === "Whole bracket" && m.scan.name === "Whole bracket", "a given name, trimmed");
check(JSON.stringify(m.merged_from) === JSON.stringify(["under", "top"]), "target first");
check(
  m.tables?.length === 2 && m.tables[0].id === "under" && m.tables[0].name === "Bracket underside" && m.tables.every((t) => t.rule === "map" && t.summary.length > 20),
  "the part was moved: each scan says what of it was left out",
);
check(Array.isArray(m.warnings) && m.warnings.some((w) => w.includes("Bracket underside") && /thin part/.test(w)), "and a raised area that may be a thin part is said, with the scan's name");

// a merged scan has no recording: it can be aligned to, not merged again
const mergedId = made.id;
r = refused(() => mock.merge(p, { target: mergedId, source: "top", T: IDENTITY }));
check(
  r?.status === 409 && r.detail === 'the first scan "Whole bracket" has no recording (a merged scan, or its recording was removed)' && r.hint === "merge scans that still have their recordings",
  "merging a merged scan again: 409",
);
r = refused(() => mock.merge(p, { target: "top", source: mergedId, T: IDENTITY }));
check(r?.status === 409 && /^the second scan "Whole bracket" has no recording/.test(r.detail), "whichever of the two it is");
check(refused(() => mock.align(p, { target: mergedId, source: "top" })) === null, "another scan can be aligned to a merged scan");
// R2: a scan that keeps its result only can be aligned, not merged (the engine's words, ohs/server/aligning.py)
const topScan = p.scans.find((x) => x.id === "top")!;
topScan.keep = "standard";
topScan.has_recording = false;
r = refused(() => mock.merge(p, { target: "under", source: "top", T: IDENTITY }));
check(
  r?.status === 409 && r.detail === 'the second scan "Bracket top" has no recording: it was removed to free space (the scan keeps its result only)',
  `a result-only scan is not merged: ${r?.detail}`,
);
check(refused(() => mock.align(p, { target: "under", source: "top" })) === null, "a result-only scan can still be aligned");
topScan.keep = "full";
topScan.has_recording = true;

// a failed merge tells in the log that nothing changed
logs.length = 0;
mock.merge(p, { target: "top", source: "under", T: IDENTITY });
last().spec.onFail?.("the two scans were recorded with different calibrations");
check(logs.length === 1 && logs[0].level === "error" && /^Merging failed: .*The two scans are unchanged\.$/.test(logs[0].message), "a failed merge is reported");

// ------------------------------------------------------------------ several scans (M4 second slice)
/** The demo's seeded project now: three processed scans of the bracket, and one only recorded. */
function project3(): Project {
  const scans = [
    scan("top", "Bracket top", "processed", 864, 28.8),
    scan("under", "Bracket underside", "processed", 612, 20.4),
    scan("side", "Bracket side", "processed", 450, 15),
    scan("end", "Bracket end", "recorded", 390, 13),
  ];
  return { id: "p1", name: "Intake manifold bracket", created: "c", modified: "m", n_scans: scans.length, thumbnail_url: null, scans };
}
const THREE = ["top", "under", "side"];
const runAll = (body: Record<string, unknown>) => {
  mock.alignAll(p, body);
  return run(last());
};

p = project3();
const nJobs = started.length;
// what is refused
r = refused(() => mock.alignAll(p, {}));
check(r?.status === 422 && r.detail === "scans: give the ids of 2 to 8 scans", "no list: 422");
r = refused(() => mock.alignAll(p, { scans: ["top"] }));
check(r?.status === 422 && r.detail === "1 scan(s) given: 2 to 8 are aligned at once" && r.hint === "add a second scan", "one scan: 422");
r = refused(() => mock.alignAll(p, { scans: Array.from({ length: 9 }, (_, i) => `s${i}`) }));
check(r?.status === 422 && /^9 scan\(s\) given/.test(r.detail), "nine scans: 422");
r = refused(() => mock.alignAll(p, { scans: ["top", "under", "top"] }));
check(r?.status === 409 && r.detail === 'the scan "Bracket top" is listed twice' && r.hint === "list each scan once", "a scan listed twice: 409");
r = refused(() => mock.alignAll(p, { scans: ["top", "nope"] }));
check(r?.status === 404, "an unknown scan: 404");
r = refused(() => mock.alignAll(p, { scans: ["top", "under", "end"] }));
check(r?.status === 409 && r.detail === 'the scan "Bracket end" has no mesh yet', "a scan without a mesh: 409, by name");
r = refused(() => mock.alignAll(p, { scans: THREE, picked: [{ a: "top", b: "end", pairs: TRIANGLE }] }));
check(r?.status === 422 && r.detail === "picked points name a scan that is not in the list ('end')" && r.hint === "pick points only on the listed scans", "picked points on a scan not in the list: 422");
r = refused(() => mock.alignAll(p, { scans: THREE, picked: [{ a: "top", b: "side", pairs: TRIANGLE.slice(0, 2) }] }));
check(r?.status === 422 && r.detail === '2 pair(s) of points between "Bracket top" and "Bracket side" are too few', "fewer than three picked pairs: 422");
r = refused(() => mock.alignAll(p, { scans: THREE, picked: [{ a: "side", b: "under", pairs: LINE }] }));
check(r?.status === 422 && /^the points picked between "Bracket side" and "Bracket underside" do not give a pose: the picked points lie in one line/.test(r.detail), "picked points in one line: 422");
r = refused(() => mock.alignAll(p, { scans: THREE, picked: [{ a: "top", b: "top", pairs: TRIANGLE }] }));
check(r?.status === 422, "picked points between a scan and itself: 422");
check(started.length === nJobs, "a refused request starts no job");

// the part stayed: all three placed
const before3 = JSON.stringify(p);
job = mock.alignAll(p, { scans: THREE });
check(job.kind === "align_all" && JSON.stringify((job as Job & { scans?: string[] }).scans) === JSON.stringify(THREE) && (job as Job & { project_id?: string }).project_id === "p1", "an align_all job, with the project and the scans");
done = run(last());
check(done.steps.length === 4 && done.steps[0].message === 'Aligning "Bracket top" and "Bracket underside" (pair 1 of 3)', "every pair is aligned, one after the other");
check(done.steps[done.steps.length - 1].message === "Placing the scans", "then the scans are placed");
let all = done.result as AlignAllResult;
check(all.accepted && all.unplaced.length === 0 && JSON.stringify(all.placed) === JSON.stringify(THREE), "all three placed");
check(all.verdict === "All 3 scans placed through 2 links; 1 more link checked the chain, the worst disagrees by 0.27 mm.", "the contract's sentence");
check(all.message === all.verdict && all.loops === 1 && all.graph === true && all.worst_link_mm === 0.27, "loops, graph and the worst link");
check(JSON.stringify(all.pairs.map((x) => [x.a, x.b, x.used])) === JSON.stringify([["top", "under", "chain"], ["top", "side", "chain"], ["under", "side", "loop"]]), "every pair, and how it was used");
check(all.pairs.every((x) => x.accepted && !("T" in x) && typeof x.verdict === "string" && x.start === "features"), "each pair as an alignment of two, without T");
check(all.pairs[2].disagree_mm === 0.27, "the loop says how far it disagrees");
check(THREE.every((id) => JSON.stringify(all.T[id]) === JSON.stringify(IDENTITY)), "the demo's scans share a frame: every pose is the identity");
check(THREE.every((id) => all.support[id] === true) && all.same_setup === true, "a table in each");
check(JSON.stringify(p) === before3, "align_all writes nothing");

// the part was moved: the underside is not placed
all = runAll({ scans: THREE, same_setup: false }).result as AlignAllResult;
check(!all.accepted && JSON.stringify(all.unplaced) === JSON.stringify(["under"]) && JSON.stringify(all.placed) === JSON.stringify(["top", "side"]), "the part moved: “Bracket underside” is not placed");
check(
  all.verdict ===
    'Not all placed: "Bracket underside" could not be aligned to any placed scan. Pick matching points between each of them and a placed scan, or scan with more overlap.',
  "the contract's sentence, by name",
);
check(all.T.under === null && JSON.stringify(all.T.side) === JSON.stringify(IDENTITY), "no pose for it, the others placed");
check(JSON.stringify(all.pairs.map((x) => x.used)) === JSON.stringify(["", "chain", ""]) && !all.pairs[0].accepted, "only top and side are accepted");
check(all.pairs[0].verdict.startsWith("Not aligned: ") && all.loops === 0 && all.graph === false, "a pair not accepted says why");

// picked points between the underside and a placed scan place it
all = runAll({ scans: THREE, same_setup: false, picked: [{ a: "side", b: "under", pairs: TRIANGLE }] }).result as AlignAllResult;
check(all.accepted && all.unplaced.length === 0, "picked points between the underside and the side: all placed");
const us = all.pairs.find((x) => x.a === "under" && x.b === "side")!;
check(us.accepted && us.start === "given" && us.used === "chain" && typeof us.pairs_rms_mm === "number" && us.pairs_rms_mm < 1, "that pair from the given points, in the chain");
check(all.verdict === "All 3 scans placed through 2 links.", "no loop: nothing checked the chain");
all = runAll({ scans: THREE, same_setup: false, picked: [{ a: "under", b: "top", pairs: TRIANGLE }] }).result as AlignAllResult;
check(all.accepted, "either way round, with any placed scan");

// picked points that do not match are left out; ones that disagree by more than 2 mm warn
all = runAll({ scans: THREE, picked: [{ a: "under", b: "side", pairs: OFF }] }).result as AlignAllResult;
const off = all.pairs.find((x) => x.a === "under" && x.b === "side")!;
check(off.used === "left out" && typeof off.disagree_mm === "number" && off.disagree_mm > 5, "picked points that disagree by more than 5 mm: the loop is left out");
check(all.accepted && all.loops === 1 && all.verdict === "All 3 scans placed through 2 links. 1 link did not fit the others and was left out.", "and the verdict says so");
const ROUGH = pairs([
  [[0, 0, 0], [0, 0, 0]],
  [[40, 0, 0], [40, 0, 0]],
  [[0, 30, 5], [0, 34, 5]],
]); // agree to about 2.7 mm
all = runAll({ scans: THREE, picked: [{ a: "under", b: "side", pairs: ROUGH }] }).result as AlignAllResult;
check(all.worst_link_mm > 2 && all.worst_link_mm < 5, "a loop from rough points disagrees by more than 2 mm");
check(/the worst disagrees by \d\.\d\d mm\. That is a lot: look at the scans together before merging\.$/.test(all.verdict), "the warning ends the verdict");

// eight scans: 28 pairs
const p8: Project = { ...project3(), scans: Array.from({ length: 8 }, (_, i) => scan(`s${i}`, `Part ${i + 1}`, "processed", 300, 10)) };
mock.alignAll(p8, { scans: p8.scans.map((s) => s.id) });
const eight = run(last());
check(eight.steps.length === 29 && /\(pair 28 of 28\)$/.test(eight.steps[27].message) && last().spec.minSeconds <= 10, "eight scans: every one of the 28 pairs");
all = eight.result as AlignAllResult;
check(all.accepted && all.pairs.filter((x) => x.used === "chain").length === 7 && all.loops === 21, "seven links place them, the rest check");
check(/^All 8 scans placed through 7 links; 21 more links checked the chain, the worst disagrees by 0\.\d\d mm\.$/.test(all.verdict), "the sentence for eight");

// the pure helpers
check(turnedOver({ name: "Bracket underside" }) && turnedOver({ name: "Bottom" }) && !turnedOver({ name: "Bracket side" }), "the demo's turned-over scans");
check(fitsFromShape({ name: "a" }, { name: "Bottom" }, true) && !fitsFromShape({ name: "a" }, { name: "Bottom" }, false) && fitsFromShape({ name: "a" }, { name: "b" }, false), "the shape alone fits unless turned over, or the table settles it");
check(quotedNames(["A"]) === '"A"' && quotedNames(["A", "B", "C"]) === '"A", "B" and "C"', "names in the engine's sentences");
check(
  alignAllVerdict({ n: 4, chain: 2, loops: 0, leftOut: 0, worst: 0, unplaced: ["Back", "Front"] }).startsWith('Not all placed: "Back" and "Front" could not be aligned to any placed scan.'),
  "two scans not placed",
);
check(alignAllVerdict({ n: 2, chain: 1, loops: 0, leftOut: 0, worst: 0, unplaced: [] }) === "All 2 scans placed through 1 link.", "one link");

// two scans: the first form, now with the scans in the job's meta
p = project3();
job = mock.merge(p, { target: "top", source: "side", T: IDENTITY });
check(JSON.stringify((job as Job & { scans?: string[] }).scans) === JSON.stringify(["top", "side"]) && (job as Job & { target?: string }).target === "top", "a merge of two: target, source and the scans in its meta");
m = run(last()).result as MergeResult;
check(m.scan.name === "Bracket top + Bracket side" && JSON.stringify(m.merged_from) === JSON.stringify(["top", "side"]), "merged as before");

// merging several: what is refused
const at = (ids: string[]) => ids.map((id) => ({ id, T: IDENTITY }));
r = refused(() => mock.merge(p, { target: "top", source: "under", T: IDENTITY, scans: at(THREE) }));
check(r?.status === 422 && r.detail === "give either target, source and T, or scans, not both", "both forms: 422");
r = refused(() => mock.merge(p, { name: "x" }));
check(r?.status === 422 && r.detail === "nothing to merge: give target, source and T, or scans", "neither form: 422");
r = refused(() => mock.merge(p, { scans: at(["top"]) }));
check(r?.status === 422 && /^1 scan\(s\) given/.test(r.detail), "one scan: 422");
r = refused(() => mock.merge(p, { scans: at(["top", "under", "under"]) }));
check(r?.status === 409 && r.detail === 'the scan "Bracket underside" is listed twice', "a scan listed twice: 409");
r = refused(() => mock.merge(p, { scans: [{ id: "top", T: IDENTITY }, { id: "under", T: [[2, 0, 0, 0], IDENTITY[1], IDENTITY[2], IDENTITY[3]] }, { id: "side", T: IDENTITY }] }));
check(r?.status === 422 && r.detail === 'the pose of "Bracket underside" is not a rigid motion', "a pose that is not a rigid motion: 422, by name");
r = refused(() => mock.merge(p, { scans: [{ id: "top", T: IDENTITY }, { id: "under" }, { id: "side", T: IDENTITY }] }));
check(r?.status === 422 && r.detail === 'the pose of "Bracket underside" must be a 4x4 matrix', "a missing pose: 422");
r = refused(() => mock.merge(p, { scans: ["top", "under"] }));
check(r?.status === 422 && r.detail === "scans: give each scan as {id, T}", "scans without poses: 422");
const mergedTwo = p.scans[p.scans.length - 1].id;
r = refused(() => mock.merge(p, { scans: at(["top", "under", mergedTwo]) }));
check(r?.status === 409 && /^the scan "Bracket top \+ Bracket side" has no recording/.test(r.detail), "a merged scan in the list: 409");

// merging several: done
logs.length = 0;
const count3 = p.scans.length;
job = mock.merge(p, { scans: at(THREE) });
const meta3 = job as Job & { scans?: string[]; target?: string; source?: string };
check(job.kind === "merge" && JSON.stringify(meta3.scans) === JSON.stringify(THREE) && meta3.target === undefined && meta3.source === undefined, "a merge job of three: the scans in its meta, no target or source");
done = run(last());
const frames3 = done.steps.map((s) => /^Merging: frame (\d+) of (\d+)$/.exec(s.message.replace(/(\d),(\d)/g, "$1$2"))).filter((x): x is RegExpExecArray => !!x);
check(Number(frames3[0][2]) === 864 + 612 + 450, "n is the frames of all three recordings");
m = done.result as MergeResult;
made = p.scans[p.scans.length - 1];
check(p.scans.length === count3 + 1 && m.scan_id === made.id, "one more scan");
check(made.name === "Bracket top + Bracket underside + Bracket side", "the default name: the names joined by “ + ”");
check(JSON.stringify(m.merged_from) === JSON.stringify(THREE) && JSON.stringify(made.merged_from?.map((x) => x.name)) === JSON.stringify(["Bracket top", "Bracket underside", "Bracket side"]), "merged_from: all of them, in order");
check(logs.length === 1 && logs[0].message === "Merged scan ready: Bracket top + Bracket underside + Bracket side", "the notification");
mock.merge(p, { scans: at(["side", "top", "under"]), same_setup: false, name: " All sides " });
done = run(last());
check(done.steps[0].message === "Finding what the part lay on" && (done.result as MergeResult).scan.name === "All sides", "the part moved, a given name");
check(JSON.stringify((done.result as MergeResult).tables?.map((t) => t.id)) === JSON.stringify(["side", "top", "under"]), "each of the three scans' tables, in order");
logs.length = 0;
mock.merge(p, { scans: at(THREE) });
last().spec.onFail?.("the scans were recorded with different calibrations");
check(logs.length === 1 && /^Merging failed: .*The scans are unchanged\.$/.test(logs[0].message), "a failed merge of several is reported");

console.log(`mock align.test: ${checks} checks passed`);
