/**
 * Checks for the Align page's helpers (`npm test`): which scans can be aligned or merged, the picked
 * points and their pairs, the pose as the 3D view takes it, and an alignment's figures. Plain TypeScript
 * that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import { Vector3 } from "three";
import type { AlignResult } from "../api/types.ts";
import { matrixFromRowMajor } from "../three/coords.ts";
import {
  addPick,
  alignFigures,
  applyPose,
  chooseScans,
  completePairs,
  defaultMergeName,
  flattenPose,
  hasMesh,
  hasRecording,
  isAlignResult,
  isMergeResult,
  mergedFromText,
  MIN_PAIRS,
  pairRows,
  pairsDisagree,
  pickHint,
  removePair,
  type Picks,
  type Vec3,
} from "./align.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`align.test: ${what}`);
}
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const near = (a: number, b: number, tol = 1e-9) => Math.abs(a - b) <= tol;

// ------------------------------------------------------------------ scans
const result = { mesh_url: "m", stl_url: "s", laser_points_url: null, report: {} };
check(hasMesh({ status: "processed", result }), "a processed scan with a result has a mesh");
check(!hasMesh({ status: "processed", result: null }) && !hasMesh({ status: "recorded", result: null }) && !hasMesh({ status: "processing", result }), "no mesh otherwise");
check(hasRecording({}) && hasRecording({ merged_from: [] }), "an ordinary scan has its recording");
check(!hasRecording({ merged_from: [{ id: "a", name: "A" }] }), "a merged scan has none");
check(mergedFromText({}) === null, "nothing to say for an ordinary scan");
check(
  mergedFromText({ merged_from: [{ id: "a", name: "Bracket top" }, { id: "b", name: "Bracket underside" }] }) === "Merged from Bracket top + Bracket underside",
  "what a merged scan was made from, by name",
);
check(defaultMergeName("Bracket top", "Bracket underside") === "Bracket top + Bracket underside", "the default name of a merged scan");

const ids = ["a", "b", "c"];
check(same(chooseScans(ids), { target: "a", source: "b" }), "nothing asked: the first two");
check(same(chooseScans(ids, "c", "a"), { target: "c", source: "a" }), "the asked scans");
check(same(chooseScans(ids, "c"), { target: "c", source: "a" }), "only the target asked: the first other scan is moved");
check(same(chooseScans(ids, null, "a"), { target: "b", source: "a" }), "only the source asked: the first other scan stays");
check(same(chooseScans(ids, "b", "b"), { target: "b", source: "a" }), "the same scan twice: the two differ");
check(same(chooseScans(ids, "x", "y"), { target: "a", source: "b" }), "scans the project does not have");
check(same(chooseScans(["a"]), { target: "a", source: null }), "one scan with a mesh: nothing to move");
check(same(chooseScans([]), { target: null, source: null }), "no scan with a mesh");
const twice = chooseScans(ids, "c", "b");
check(same(chooseScans(ids, twice.target, twice.source), twice), "a choice that holds is kept");

// ------------------------------------------------------------------ picked points
const p = (n: number): Vec3 => [n, n + 0.5, -n];
let picks: Picks = { target: [], source: [] };
check(pairRows(picks).length === 0 && completePairs(picks).length === 0, "nothing picked");
picks = addPick(picks, "target", p(1));
check(same(pairRows(picks), [{ n: 1, target: p(1), source: null }]), "a point on one scan is half a pair");
check(completePairs(picks).length === 0, "half a pair is not sent");
picks = addPick(picks, "source", p(10));
picks = addPick(picks, "source", p(20));
picks = addPick(picks, "target", p(2));
picks = addPick(picks, "target", p(3));
check(picks.target.length === 3 && picks.source.length === 2, "the picks of each scan, in order");
check(
  same(completePairs(picks), [
    { source: p(10), target: p(1) },
    { source: p(20), target: p(2) },
  ]),
  "pairs are formed in order: point 1 with point 1, point 2 with point 2",
);
check(same(pairRows(picks)[2], { n: 3, target: p(3), source: null }), "the third point waits for its other half");
check(completePairs(picks).length < MIN_PAIRS, "two pairs are not enough");
picks = addPick(picks, "source", p(30));
check(completePairs(picks).length === MIN_PAIRS, "three pairs are enough");

const without = removePair(picks, 1);
check(same(without.target, [p(1), p(3)]) && same(without.source, [p(10), p(30)]), "removing a pair takes its point from both scans, the later ones move up");
check(picks.target.length === 3, "the picks given are not changed");
const uneven = removePair({ target: [p(1), p(2)], source: [p(10)] }, 1);
check(same(uneven, { target: [p(1)], source: [p(10)] }), "removing half a pair takes the one point there is");
check(same(removePair(picks, 7), picks) && same(removePair(picks, -1), picks), "a row that is not there removes nothing");

const names = { target: "Top", source: "Underside" };
check(/first on one, then on the other/.test(pickHint({ target: [], source: [] }, names)), "the first hint says what to click");
check(pickHint({ target: [p(1)], source: [] }, names) === "Point 1: now click the same spot on “Underside”", "after a click on the first scan, the second is asked");
check(pickHint({ target: [p(1)], source: [p(1), p(2)] }, names) === "Point 2: now click the same spot on “Top”", "and the other way round");
check(/^2 of 3 points/.test(pickHint({ target: [p(1), p(2)], source: [p(1), p(2)] }, names)), "how many are still needed");
check(/enough to align/.test(pickHint({ target: [p(1), p(2), p(3)], source: [p(1), p(2), p(3)] }, names)), "three are enough");

// ------------------------------------------------------------------ the pose
const T = [
  [0, -1, 0, 10],
  [1, 0, 0, 20],
  [0, 0, 1, 30],
  [0, 0, 0, 1],
]; // a quarter turn about z, then a move
const flat = flattenPose(T);
check(same(flat, [0, -1, 0, 10, 1, 0, 0, 20, 0, 0, 1, 30, 0, 0, 0, 1]), "the rows one after the other");
check(flattenPose(null) === null && flattenPose([]) === null && flattenPose(T.slice(0, 3)) === null, "not four rows");
check(flattenPose([[1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, 0]]) === null, "not four columns");
check(flattenPose([T[0], T[1], [0, 0, NaN, 0], T[3]]) === null && flattenPose([T[0], T[1], [0, 0, "1", 0], T[3]]) === null, "not numbers");
check(same(applyPose(flat!, [1, 2, 3]), [8, 21, 33]), "a point of the source in the target's frame");
// the 3D view's matrix does the same to a point
const m = matrixFromRowMajor(flat!);
const v = new Vector3(1, 2, 3).applyMatrix4(m);
check(near(v.x, 8) && near(v.y, 21) && near(v.z, 33), "the three.js matrix made from the rows moves a point the same way");
check(near(m.elements[12], 10) && near(m.elements[13], 20) && near(m.elements[14], 30), "the move ends up in the matrix's translation");

// ------------------------------------------------------------------ results
const accepted: AlignResult = {
  project_id: "p",
  target: "a",
  source: "b",
  accepted: true,
  verdict: "Aligned: 76 % of the smaller scan's part lies within 1 mm of the other scan, 0.25 mm RMS; 8 of 9 starting guesses agree.",
  reason: null,
  T,
  start: "features",
  overlap: 0.764,
  rmse_mm: 0.249,
  fit_mm: 0.2,
  candidates: 9,
  agree: 8,
  spread_mm: 0.1,
  dof: 6,
  same_setup: true,
  used_support: true,
  support: { target: true, source: true },
  pairs_rms_mm: null,
  n_matched: 1000,
  ms: 2100,
  message: "",
};
check(isAlignResult(accepted), "an alignment's result");
check(!isAlignResult(null) && !isAlignResult({}) && !isAlignResult({ ...accepted, T: [[1]] }) && !isAlignResult({ ...accepted, verdict: undefined }), "something else is not one");
check(isMergeResult({ project_id: "p", scan_id: "s" }) && !isMergeResult({ project_id: "p" }) && !isMergeResult(null), "a merge's result names the new scan");

let f = alignFigures(accepted);
check(f[0].label === "Overlap" && f[0].value === "76 %", "the share that overlaps, as a percentage");
check(f[1].value === "0.25 mm RMS", "the RMS in mm");
check(f[2].value === "8 of 9 agree", "how many starting guesses agree");
check(f.length === 4 && f[3].label === "Shared table" && /^Used/.test(f[3].value), "same setup: the table was used");
check(!f.some((x) => x.warn), "nothing to warn about");

f = alignFigures({ ...accepted, start: "given", candidates: 1, agree: 1, pairs_rms_mm: 1.84, same_setup: false, used_support: false });
check(f[2].value === "From your points", "from picked points: no starting guesses to count");
check(f[3].label === "Your points" && f[3].value === "agree to 1.8 mm" && !f[3].warn, "how well the picked points agree");
check(f.length === 4, "the part was moved: no table row");

f = alignFigures({ ...accepted, start: "given", pairs_rms_mm: 7.2, rmse_mm: null, used_support: false });
check(f[1].value === "–", "no RMS: a dash");
check(f[3].warn === true, "points that disagree by more than 5 mm are marked");
check(/^Not used/.test(f[4].value), "same setup, but the tables did not line up");
check(!pairsDisagree({ pairs_rms_mm: null }) && !pairsDisagree({ pairs_rms_mm: 5 }) && pairsDisagree({ pairs_rms_mm: 5.1 }), "the warning starts above 5 mm");

console.log(`align.test: ${checks} checks passed`);
