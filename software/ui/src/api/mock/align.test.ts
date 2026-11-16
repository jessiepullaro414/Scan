/**
 * Checks for the demo engine's align and merge (`npm test`): the server's rules and error replies
 * (APP_API.md "M4 first slice", `ohs/server/aligning.py`), the made-up answers, and the merge job.
 * Plain TypeScript that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { AlignResult, Job, MergeResult, PointPair, Project, Scan } from "../types.ts";
import { alignedVerdict, AMBIGUOUS_REASON, checkPairs, isPose, isRigid, MockAlign, type AlignHost } from "./align.ts";
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
    "Not aligned: the part fits in more than one position about equally well (a symmetric shape, a repeated feature, or a surface that can slide on itself): give a rough start by picking matching points, or scan with more of the part's distinct features in both.",
  "with the engine's sentence",
);
check(a.reason === AMBIGUOUS_REASON && a.message === a.verdict, "the reason, and the message is the verdict");
check(a.start === "features" && a.pairs_rms_mm === null && a.same_setup === false && a.used_support === false, "from the shape alone, the tables not used");
check(JSON.stringify(a.T) === JSON.stringify(WRONG) && isRigid(a.T), "a pose is still returned, for showing");
check(a.project_id === "p1" && a.target === "top" && a.source === "under", "the result names the project and the two scans");
check(!("scan_id" in a), "an align result names no scan of its own (no “mesh ready” for it)");
check(JSON.stringify(p) === before, "align writes nothing");

mock.align(p, { target: "top", source: "under" });
a = run(last()).result as AlignResult;
check(a.accepted && a.same_setup === true && a.used_support === true, "same setup (the default): accepted, the table used");
check(a.verdict === "Aligned: 76 % of the smaller scan's part lies within 1 mm of the other scan, 0.25 mm RMS; 8 of 9 starting guesses agree.", "the engine's sentence");
check(a.reason === null && a.start === "features" && a.agree === 8 && a.candidates === 9 && a.dof === 6, "its figures");
check(a.overlap === 0.76 && a.rmse_mm === 0.25, "the sentence and the figures agree");
check(JSON.stringify(a.T) === JSON.stringify(IDENTITY), "the demo's scans share a frame: the second stays where it is");

mock.align(p, { target: "top", source: "under", same_setup: false, pairs: [] });
check((run(last()).result as AlignResult).accepted === false, "an empty list of pairs is no pairs");

mock.align(p, { target: "top", source: "under", same_setup: false, pairs: TRIANGLE });
a = run(last()).result as AlignResult;
check(a.accepted && a.start === "given", "with three pairs: accepted, from the given points");
check(/^Aligned: \d+ % of the smaller scan's part lies within 1 mm of the other scan, \d\.\d\d mm RMS; from the start you gave\.$/.test(a.verdict), "the engine's sentence for a given start");
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
const frames = done.steps.map((s) => /^Merging: frame (\d+) of (\d+)$/.exec(s.message)).filter((m): m is RegExpExecArray => !!m);
check(frames.length >= 20, "the job reports “Merging: frame k of n”");
const total = Number(frames[0][2]);
check(total === 864 + 612, "n is the frames of both recordings");
check(frames.every((m) => Number(m[2]) === total), "n stays the same");
check(frames.every((m, i) => i === 0 || Number(m[1]) > Number(frames[i - 1][1])), "k counts up");
check(Number(frames[0][1]) >= 1 && Number(frames[frames.length - 1][1]) === total, "from the first frame to the last");
check(done.steps.every((s, i) => s.p >= 0 && s.p <= 1 && (i === 0 || s.p >= done.steps[i - 1].p)), "the progress only grows");
check(done.steps[done.steps.length - 1].message === "Adding the merged scan to the project", "the last step adds the scan");
check(!done.steps.some((s) => s.message === "Finding what the part lay on"), "same setup: the tables stay in");

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

// a failed merge tells in the log that nothing changed
logs.length = 0;
mock.merge(p, { target: "top", source: "under", T: IDENTITY });
last().spec.onFail?.("the two scans were recorded with different calibrations");
check(logs.length === 1 && logs[0].level === "error" && /^Merging failed: .*The two scans are unchanged\.$/.test(logs[0].message), "a failed merge is reported");

console.log(`mock align.test: ${checks} checks passed`);
