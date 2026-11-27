/**
 * Checks for the Align page's helpers (`npm test`): which scans can be aligned or merged, the picked
 * points and their pairs, the pose as the 3D view takes it, and an alignment's figures. Plain TypeScript
 * that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import { Vector3 } from "three";
import type { AlignAllPair, AlignAllResult, AlignHold, AlignResult, MergeResult } from "../api/types.ts";
import { matrixFromRowMajor } from "../three/coords.ts";
import {
  addPick,
  addTurnPick,
  alignAllFigures,
  alignFigures,
  allCautionOf,
  cautionOf,
  applyPose,
  chooseAt,
  chooseList,
  chooseScans,
  completePairs,
  defaultMergeName,
  flattenPose,
  hasMesh,
  hasRecording,
  holdAction,
  holdCaution,
  holdOf,
  holdRequest,
  isAlignAllResult,
  isAlignResult,
  isMergeResult,
  keepPicksOf,
  MAX_SCANS,
  mergedFromText,
  mergeNameOf,
  mergeNeedsConfirm,
  mergeScansOf,
  mergeLeftOut,
  MIN_PAIRS,
  SETUP_TEXT,
  namesText,
  nextFreeScan,
  pairKey,
  pairLine,
  pairRows,
  pairsDisagree,
  pairsOf,
  pairUseText,
  cannotMerge,
  recordingMark,
  pickedPoints,
  pickHint,
  pickPairOf,
  picksFor,
  poseOf,
  removeAt,
  removePair,
  tagsNote,
  sameList,
  SCAN_PALETTE,
  scanColor,
  scanPlacements,
  suggestPickPair,
  unfinishedPairs,
  withPicks,
  type PickBook,
  type Picks,
  type Vec3,
} from "./align.ts";

/** The two-scan page's colours (AlignCanvas.tsx's SCAN_COLOR, which needs three.js to import). */
const SCAN_COLOR_TARGET = "#3f83f0";
const SCAN_COLOR_SOURCE = "#f08c2e";

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
// R2: a scan that keeps its result only can be aligned, not merged; it is marked like a merged scan
check(recordingMark({}) === "" && recordingMark({ keep: "engineering", has_recording: true }) === "", "a scan with its recording: no mark");
check(recordingMark({ merged_from: [{ id: "a", name: "A" }] }) === " (merged)", "a merged scan's mark");
check(recordingMark({ keep: "standard", has_recording: false }) === " (result only)", "a result-only scan's mark");
check(recordingMark({ keep: "full", has_recording: false }) === " (no recording)", "a scan whose recording is missing");
check(cannotMerge({}, false) === null && cannotMerge({ keep: "engineering", has_recording: true }, true) === null, "these can be merged");
const ro = cannotMerge({ name: "B", keep: "standard", has_recording: false }, false)!;
check(ro.title === "“B” cannot be merged", `result only title: ${ro.title}`);
check(
  ro.text === "Its recording was removed to free space (the scan keeps its result only). You can align to it and compare the two here; to merge, choose two scans that still have their recordings.",
  `result only text: ${ro.text}`,
);
check(cannotMerge({ name: "B", keep: "standard" }, true)!.text.endsWith("to merge, remove it from the list or choose scans that still have their recordings."), "result only, several");
const mg = cannotMerge({ name: "A + B", merged_from: [{ id: "a", name: "A" }] }, false)!;
check(mg.title === "“A + B” cannot be merged again" && mg.text.startsWith("It is a merged scan and has no recording of its own."), `merged: ${mg.title}`);
check(cannotMerge({ name: "A + B", merged_from: [{ id: "a", name: "A" }] }, true)!.text.includes("remove it from the list"), "merged, several");
check(cannotMerge({ name: "C", keep: "full", has_recording: false }, false)!.text.startsWith("Its recording is not on disk."), "no recording on disk");
// the engine's order (ohs/server/aligning.py): "keeps its result only" before "merged"
check(cannotMerge({ name: "M", merged_from: [{ id: "a", name: "A" }], keep: "standard", has_recording: false }, false)!.title === "“M” cannot be merged", "merged and freed: result only first");
check(recordingMark({ merged_from: [{ id: "a", name: "A" }], keep: "standard", has_recording: false }) === " (result only)", "merged and freed: its mark");
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

// Compare's "From points" takes turns, the scan first (UX: the point picker dropped quick clicks): a click out of
// turn, such as a quick second click on the scan before the page has switched to the CAD model, is not taken, so the
// pairs never get out of step; the state given is not changed
let turns: Picks = { target: [], source: [] };
turns = addTurnPick(turns, "target", p(1));
const early = addTurnPick(turns, "target", p(2));
check(early === turns && turns.target.length === 1, "a second click on the scan before its CAD point: not taken");
check(addTurnPick(turns, "source", p(10)).source.length === 1, "the CAD point is taken");
turns = addTurnPick(addTurnPick(turns, "source", p(10)), "source", p(20));
check(turns.source.length === 1 && turns.target.length === 1, "a second click on the CAD model: not taken");
check(addTurnPick({ target: [], source: [] }, "source", p(1)).source.length === 0, "the CAD model before the scan: not taken");

// the picker's top bar keeps its height on both turns (a prompt that wrapped moved both views ~36 px, and quick
// clicks landed off the model): the prompt is one line, cut with "…" if need be, never wrapped
{
  const fsm = (await import(["node", "fs"].join(":"))) as { readFileSync: (u: URL, enc: "utf8") => string };
  const picker = fsm.readFileSync(new URL("../pages/viewer/PointPairPicker.tsx", import.meta.url), "utf8");
  check(!/flex-wrap/.test(picker), "the picker's bar never wraps");
  check(/className="[^"]*\btruncate\b[^"]*"[^>]*>\{hint\}/.test(picker), "the prompt is one line");
  check(/Align from \{n\} pair\{n === 1 \? "" : "s"\}/.test(picker), "one pair, two pairs");
}

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
  verdict: "Aligned: 76\u202f% of the smaller scan's part lies within 1\u202fmm of the other scan, typically 0.25\u202fmm; 8 of 9 starting guesses agree.",
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
check(f[0].label === "Overlap" && f[0].value === "76\u202f%", "the share that overlaps, as a percentage");
check(f[1].value === "typically 0.25\u202fmm" && f[1].note === "where they overlap", "the RMS in mm, said as typical (U36)");
check(f[2].value === "8 of 9 agree" && f[2].note === "(each starts from a different rough fit)", "how many starting guesses agree, the help apart from it (U36)");
check(f.length === 4 && f[3].label === "Shared table" && /^Used/.test(f[3].value), "same setup: the table was used");
check(!f.some((x) => x.warn), "nothing to warn about");

f = alignFigures({ ...accepted, start: "given", candidates: 1, agree: 1, pairs_rms_mm: 1.84, same_setup: false, used_support: false });
check(f[2].value === "From your points", "from picked points: no starting guesses to count");
check(f[3].label === "Your points" && f[3].value === "agree to 1.8\u202fmm" && !f[3].warn, "how well the picked points agree");
check(f.length === 4, "the part was moved: no table row");

f = alignFigures({ ...accepted, start: "given", pairs_rms_mm: 7.2, rmse_mm: null, used_support: false });
check(f[1].value === "–", "no RMS: a dash");
check(f[3].warn === true, "points that disagree by more than 5\u202fmm are marked");
check(/^Not used/.test(f[4].value), "same setup, but the tables did not line up");
check(!pairsDisagree({ pairs_rms_mm: null }) && !pairsDisagree({ pairs_rms_mm: 5 }) && pairsDisagree({ pairs_rms_mm: 5.1 }), "the warning starts above 5\u202fmm");

const tags = { shared: [2, 3, 4, 5], used: [2, 4, 5], left_out: [3], rms_mm: 0.41, start: "accepted" };
f = alignFigures({ ...accepted, start: "tags", candidates: 1, agree: 1, tags });
check(f[2].label === "Start" && f[2].value === "From the tags", "from the tags: no starting guesses to count");
check(f[2].note === "tags 2, 4 and 5 seen by both scans; tag 3 left out (moved?)", "which tags, and which were left out");
check(tagsNote({ ...tags, used: [5], left_out: [] }) === "tag 5 seen by both scans" && tagsNote(null) === undefined, "one tag; none");

// ------------------------------------------------------------------ the tag pose held, offered per alignment (ENGINE_PLAN X19)
{
  const offerWords =
    "Both scans saw tags 0, 1, 2 and 3. Where two scans share little, these tags can place the scan more exactly than the shapes, as long as no tag moved against the part between the two scans (tags on the part, or tags on the table and the part not touched).";
  const links = [{ a: "a", b: "b", tags: [0, 1, 2, 3], shape_moves_mm: null }];
  const offered: AlignHold = { state: "offered", asked: null, words: offerWords, caution: null, links };
  const fromTags: AlignResult = { ...accepted, start: "tags", overlap: 0.46, little_shared: true, caution: "Little is shared: …", tags: { ...tags, used: [0, 1, 2, 3], left_out: [] } };
  check(holdOf({ ...fromTags, hold: offered }) === offered, "the block as the server wrote it");
  check(holdOf(fromTags) === null && holdOf({ ...fromTags, hold: null }) === null && holdOf(null) === null, "no block (an older server, or nothing to say): none");
  check(holdOf({ hold: { ...offered, state: "maybe" as never } }) === null && holdOf({ hold: { ...offered, words: "" } }) === null, "a block that cannot be read: none");
  const a = holdAction(offered);
  check(a !== null && a.label === "Hold the tags' position" && a.holdTags === true && !a.quiet, "offered: a button that holds the tags for this alignment");
  check(holdCaution(offered) === null, "an offer has no caution");
  check(alignFigures({ ...fromTags, hold: offered })[2].value === "From the tags", "offered: the start is still from the tags");

  const caution =
    'The shapes, starting at the tags, would move "Back" 1.4 mm from where the tags put it. A small disagreement is normal where two scans share little, but it is also what a part nudged against tags on the table looks like. If the tags are on the table and the part may have moved, let the shapes decide.';
  const held: AlignHold = { state: "held", asked: true, words: 'The tags hold "Back" on "Top", as asked for this alignment.', caution, links: [{ ...links[0], shape_moves_mm: 1.4 }] };
  const h = holdAction(held);
  check(h !== null && h.label === "Let the shapes decide" && h.holdTags === false && h.quiet, "held: a quiet button that lets the shapes decide");
  check(holdCaution(held) === caution && holdCaution({ ...held, caution: null }) === null, "held: the caution when the server wrote one");
  const fh = alignFigures({ ...fromTags, held_by_tags: true, hold: held });
  check(fh[2].label === "Start" && fh[2].value === "The tags hold the position" && fh[2].note === "tags 0, 1, 2 and 3 seen by both scans", `held: the start figure says so: ${fh[2].value}`);

  const notHeld: AlignHold = {
    state: "not_held",
    asked: true,
    words: "Holding the tags' position was asked for, but it was not applied: only 2 tags were seen by both scans, too few to hold the position.",
    caution: null,
    links,
  };
  check(holdAction(notHeld) === null && holdCaution(notHeld) === null && holdAction(null) === null, "not held: the words, nothing to press");
  check(alignFigures({ ...fromTags, hold: notHeld })[2].value === "From the tags", "not held: from the tags");
  // the table took no part in a held position: the row does not say the tables did not line up
  const tableRow = (r: Parameters<typeof alignFigures>[0]) => alignFigures(r).find((x) => x.label === "Shared table")?.value;
  check(tableRow({ ...fromTags, same_setup: true, used_support: false, hold: held }) === "Not used: the tags decide the position", "held: the table was not used because the tags decide");
  check(tableRow({ ...fromTags, same_setup: true, used_support: false, hold: notHeld }) === "Not used: the two scans' tables did not line up", "not held: the table row as before");

  // the choice is per alignment: only a button of the block sends hold_tags, and never with picked points
  check(same(holdRequest(undefined, false), {}) && same(holdRequest(true, false), { hold_tags: true }) && same(holdRequest(false, false), { hold_tags: false }), "a plain Align sends nothing; the buttons send their choice");
  check(same(holdRequest(true, true), {}), "with picked points the start is not from tags: nothing sent");

  // several scans: one block for the whole result
  const multiHeld = { hold: { ...held, words: 'The tags hold "Back" on "Top" and "Side" on "Back", as asked for this alignment.' } };
  check(holdOf(multiHeld)?.state === "held" && holdAction(holdOf(multiHeld))?.holdTags === false, "several scans: the same block and button");
}

// ------------------------------------------------------------------ several scans: the list
check(mergeNameOf(["A", "B", "C"]) === "A + B + C" && defaultMergeName("A", "B") === mergeNameOf(["A", "B"]), "the default name of several: joined by “ + ”");
const ids5 = ["a", "b", "c", "d", "e"];
check(same(chooseList(ids5), ["a", "b"]), "nothing asked: the first two");
check(same(chooseList(ids5, "c", "a"), ["c", "a"]), "two asked: as chooseScans");
check(same(chooseList(ids5, "c", "a", ["e", "x", "a", "e", "b"]), ["c", "a", "e", "b"]), "more asked: each once, only scans with a mesh");
check(same(chooseList(ids5, null, "b", ["d"]), ["a", "b", "d"]), "the first missing: the first free one stays");
const many = Array.from({ length: 12 }, (_, i) => `s${i}`);
check(chooseList(many, "s0", "s1", many.slice(2)).length === MAX_SCANS && MAX_SCANS === 8, "at most eight");
check(nextFreeScan(ids5, ["a", "c"]) === "b" && nextFreeScan(["a", "b"], ["b", "a"]) === null, "the next scan to add, or none left");
check(nextFreeScan(many, many.slice(0, 8)) === null, "a full list takes no more");
check(same(chooseAt(["a", "b", "c"], 2, "d"), ["a", "b", "d"]), "another scan at a place");
check(same(chooseAt(["a", "b", "c"], 0, "c"), ["c", "b", "a"]), "a scan already listed changes places");
check(same(chooseAt(["a", "b"], 1, "a"), ["b", "a"]), "with two: the same as swapping them");
check(same(removeAt(["a", "b", "c"], 0), ["b", "c"]) && same(removeAt(["a", "b"], 0), ["a", "b"]), "removing one; two always stay");
check(sameList(["a", "b"], ["a", "b"]) && !sameList(["a", "b"], ["b", "a"]) && !sameList(["a"], ["a", "b"]), "same list, same order");
check(same(pairsOf(["a", "b", "c"]), [["a", "b"], ["a", "c"], ["b", "c"]]) && pairsOf(many.slice(0, 8)).length === 28, "every pair, the earlier first: 3 for three, 28 for eight");
check(namesText(["A"]) === "“A”" && namesText(["A", "B"]) === "“A” and “B”" && namesText(["A", "B", "C"]) === "“A”, “B” and “C”", "names in a sentence");
check(new Set(SCAN_PALETTE).size === SCAN_PALETTE.length && SCAN_PALETTE.length >= MAX_SCANS, "one colour for each of eight scans, all different");
check(scanColor(0) === SCAN_COLOR_TARGET && scanColor(1) === SCAN_COLOR_SOURCE && scanColor(8) === scanColor(0), "the first two are the two-scan colours");

// ------------------------------------------------------------------ several scans: picked points by pair
let book: PickBook = {};
check(pairKey("a", "b") === pairKey("b", "a") && pairKey("a", "b") !== pairKey("a", "c"), "one key for a pair, either way round");
book = withPicks(book, "a", "b", { target: [p(1), p(2), p(3)], source: [p(10), p(20), p(30)] });
check(same(picksFor(book, "a", "b").target, [p(1), p(2), p(3)]), "the points on a, as stored");
check(same(picksFor(book, "b", "a"), { target: [p(10), p(20), p(30)], source: [p(1), p(2), p(3)] }), "the other way round: the sides change");
check(same(picksFor(book, "a", "c"), { target: [], source: [] }), "a pair without points");
book = withPicks(book, "c", "b", { target: [p(4)], source: [p(40)] });
check(Object.keys(book).length === 2, "points of two pairs");
check(Object.keys(withPicks(book, "b", "c", { target: [], source: [] })).length === 1, "an emptied pair is dropped");
const sent = pickedPoints(book, ["a", "b", "c"]);
check(sent.length === 1 && sent[0].a === "a" && sent[0].b === "b" && sent[0].pairs.length === 3, "only pairs with three complete pairs are sent");
check(same(sent[0].pairs[0], { source: p(10), target: p(1) }), "target points on a, source points on b");
const rev = pickedPoints(book, ["b", "a", "c"]);
check(rev[0].a === "b" && same(rev[0].pairs[0], { source: p(1), target: p(10) }), "in the list's order: the earlier scan is a");
check(same(unfinishedPairs(book, ["a", "b", "c"]), [["b", "c"]]), "a pair with too few points is named");
check(pickedPoints(book, ["a", "c"]).length === 0, "pairs of scans not in the list are not sent");
check(Object.keys(keepPicksOf(book, ["a", "b"])).length === 1 && Object.keys(keepPicksOf(book, ["a", "b", "c"])).length === 2, "a scan leaves the list: its points go");
check(same(pickPairOf(["a", "b", "c"], null), ["a", "b"]), "no pair asked: the first two");
check(same(pickPairOf(["a", "b", "c"], ["c", "a"]), ["a", "c"]), "the asked pair, the earlier scan on the left");
check(same(pickPairOf(["a", "b", "c"], ["a", "x"]), ["a", "b"]) && pickPairOf(["a"], null) === null, "a pair not in the list: the first two");

// ------------------------------------------------------------------ several scans: results
const I4 = [
  [1, 0, 0, 0],
  [0, 1, 0, 0],
  [0, 0, 1, 0],
  [0, 0, 0, 1],
];
const pr = (a: string, b: string, used: AlignAllPair["used"], extra: Partial<AlignAllPair> = {}): AlignAllPair => ({
  a,
  b,
  used,
  accepted: used !== "",
  verdict: used ? "Aligned: …" : "Not aligned: …",
  overlap: used ? 0.76 : 0.43,
  rmse_mm: 0.25,
  start: "features",
  agree: 8,
  ...extra,
});
const all: AlignAllResult = {
  project_id: "p",
  scans: ["a", "b", "c"],
  accepted: true,
  T: { a: I4, b: T, c: I4 },
  placed: ["a", "b", "c"],
  unplaced: [],
  pairs: [pr("a", "b", "chain"), pr("a", "c", "chain"), pr("b", "c", "loop", { disagree_mm: 0.27 })],
  loops: 1,
  graph: true,
  worst_link_mm: 0.27,
  support: { a: true, b: true, c: false },
  same_setup: true,
  ms: 9000,
  verdict: "All 3 scans placed through 2 links; 1 more link checked the chain, the worst disagrees by 0.27\u202fmm.",
  message: "",
};
check(isAlignAllResult(all) && !isAlignResult(all), "a result of several scans, not of two");
check(!isAlignAllResult(accepted) && !isAlignAllResult(null) && !isAlignAllResult({ ...all, T: [] }), "a result of two is not one");
check(same(poseOf(all, "b"), flat) && poseOf({ ...all, placed: ["a", "c"] }, "b") === null, "a placed scan's pose; none for one not placed");
check(pairUseText("chain") === "in the chain" && pairUseText("loop") === "checks the chain" && pairUseText("left out") === "left out" && pairUseText("") === "not used", "how a pair was used, in words");
check(pairLine(all.pairs[0], "Top") === "with “Top”: accepted, 76\u202f% overlap, in the chain", "a pair in the chain");
check(pairLine(all.pairs[2], "Side") === "with “Side”: accepted, 76\u202f% overlap, checks the chain, disagrees by 0.27\u202fmm", "a pair that checks the chain");
check(pairLine(pr("a", "b", ""), "Top") === "with “Top”: not accepted, 43\u202f% overlap, not used", "a pair not accepted");
const rowsOf = scanPlacements(all, ["a", "b", "c"]);
check(rowsOf.length === 3 && rowsOf[0].pairs.length === 0 && rowsOf[1].pairs.length === 1 && rowsOf[2].pairs.length === 2, "each pair once, under its later scan");
check(rowsOf[2].pairs[0].other === "a" && rowsOf[2].pairs[1].other === "b", "in the list's order");
check(rowsOf.every((r) => r.placed), "all placed");
const lost: AlignAllResult = {
  ...all,
  accepted: false,
  T: { a: I4, b: null, c: I4 },
  placed: ["a", "c"],
  unplaced: ["b"],
  pairs: [pr("a", "b", ""), pr("a", "c", "chain"), pr("b", "c", "")],
  loops: 0,
  graph: false,
  worst_link_mm: 0,
};
check(!scanPlacements(lost, ["a", "b", "c"])[1].placed && scanPlacements(lost, ["a", "b", "c"])[0].placed, "a scan not placed; the first always stays");
check(same(suggestPickPair(lost, ["a", "b", "c"]), ["a", "b"]) && suggestPickPair(all, ["a", "b", "c"]) === null, "next: pick between the scan not placed and a placed one");
check(same(suggestPickPair({ placed: ["c", "a"], unplaced: ["b"] }, ["c", "b", "a"]), ["c", "b"]), "in the list's order");
check(mergeScansOf(lost, ["a", "b", "c"]) === null, "a scan not placed: nothing to merge");
check(same(mergeScansOf(all, ["a", "b", "c"]), [{ id: "a", T: I4 }, { id: "b", T }, { id: "c", T: I4 }]), "the merge's scans form: every scan at its pose, in order");
check(!mergeNeedsConfirm(all) && mergeNeedsConfirm(lost) && mergeNeedsConfirm({ ...all, worst_link_mm: 2.4 }) && !mergeNeedsConfirm({ ...all, worst_link_mm: 2 }), "confirm when not all placed or a pair disagrees by more than 2\u202fmm");
let ff = alignAllFigures(all);
check(ff[0].label === "Placed" && ff[0].value === "3 of 3 scans" && !ff[0].warn, "how many were placed");
check(ff[1].label === "Worst disagreement" && ff[1].value === "0.27\u202fmm" && !ff[1].warn, "the worst disagreement");
check(ff[ff.length - 1].label === "Shared table" && ff[ff.length - 1].value === "Found in 2 of 3 scans", "the shared table");
ff = alignAllFigures({ ...lost, same_setup: false });
check(ff.length === 1 && ff[0].value === "2 of 3 scans" && ff[0].warn === true, "not all placed is marked; no checks, no table");
ff = alignAllFigures({ ...all, worst_link_mm: 3.1, pairs: [...all.pairs, pr("a", "d", "left out", { disagree_mm: 9 })] });
check(ff[1].warn === true && ff.some((f) => f.label === "Left out" && f.value === "1 pair"), "a large disagreement is marked, a pair left out is counted");

// ------------------------------------------------------------------ what a merge of a moved part left out
{
  const base = { project_id: "p", scan_id: "s", scan: { id: "s", name: "Both" } as never, merged_from: ["a", "b"], message: 'Merged into "Both"' };
  const moved: MergeResult = {
    ...base,
    tables: [
      { id: "a", name: "Top", rule: "map", summary: "Its table is left out (2,404\u202fcm²); the part is kept down to the table.", warnings: [] },
      {
        id: "b",
        name: "Side",
        rule: "slab",
        summary: "Its table is left out as everything within 2\u202fmm of its plane, and with it about 2\u202fmm of the part's bottom edge.",
        warnings: ['No table could be told apart on "Side": the largest flat surface has nothing standing on it.'],
      },
    ],
    warnings: ['No table could be told apart on "Side": the largest flat surface has nothing standing on it.'],
  };
  const lo = mergeLeftOut(moved);
  check(lo.lines.length === 2 && lo.lines[0] === "“Top”: Its table is left out (2,404\u202fcm²); the part is kept down to the table.", `one line per scan: ${lo.lines[0]}`);
  check(lo.lines[1].startsWith("“Side”: ") && lo.lines[1].includes("2\u202fmm"), "the scan left out the old way says so");
  check(same(lo.warnings, moved.warnings) && lo.warn, "the warnings, as the engine gave them, and a reason to look");
  const clean = mergeLeftOut({ ...moved, tables: [moved.tables![0]], warnings: [] });
  check(clean.lines.length === 1 && clean.warnings.length === 0 && !clean.warn, "nothing to warn about");
  const stayed = mergeLeftOut({ ...base, tables: [], warnings: [] });
  check(stayed.lines.length === 0 && stayed.warnings.length === 0 && !stayed.warn, "the part stayed: nothing was left out");
  const cleaned = mergeLeftOut({ ...moved, cleaning: "1 small loose bit (3\u202fmm²) was left out as too small (under 1\u202f% of the triangles each); 1 small bit over a thin part was kept." });
  check(cleaned.lines.length === 3 && cleaned.lines[2] === "The merged scan: 1 small loose bit (3\u202fmm²) was left out as too small (under 1\u202f% of the triangles each); 1 small bit over a thin part was kept.", `the cleaning, said: ${cleaned.lines[2]}`);
  // UX_PLAN R7: the merge always says what its cleaning took, also when the part was not moved (no tables)
  const noTables = mergeLeftOut({ ...base, tables: [], warnings: [], cleaning: "No loose bit was left out as too small (under 1 % of the triangles each)." });
  check(noTables.lines.length === 1 && noTables.lines[0] === "The merged scan: No loose bit was left out as too small (under 1 % of the triangles each).", `the cleaning, said without tables: ${noTables.lines.join("|")}`);
  check(mergeLeftOut({ ...base, tables: [], warnings: [], cleaning: null }).lines.length === 0, "nothing cleaned (cleaning null): nothing to say");
  const older = mergeLeftOut(base);
  check(older.lines.length === 0 && older.warnings.length === 0, "an older server says nothing of it");
  // the choice before merging says plainly what goes and what stays
  const movedText = SETUP_TEXT.moved;
  check(!/2 mm of the part's bottom edge/.test(movedText), "no longer: 2\u202fmm of the part's bottom edge");
  check(/table/.test(movedText) && /thin part/.test(movedText) && /down to the table/.test(movedText), `what goes and what stays: ${movedText}`);
}

// ------------------------------------------------------------------ little shared (after ENGINE_PLAN X7): a caution, not an error
{
  const caution =
    "Little is shared: 46 % of the smaller scan's part lies within 1 mm of the other. The two scans fit where they meet, but away from there the position can be a millimetre or more off, and a merge can then come out worse than either scan (a wall seen from both sides too thick or too thin). Merging is still possible. To make the position sure, scan one more pass that sees part of what each of them saw and align all three, or put tags on the part where both scans see them (a thin part scanned from both faces: one pass with it standing on edge sees both faces).";
  const little: AlignResult = { ...accepted, start: "given", overlap: 0.46, little_shared: true, caution };
  check(cautionOf(little) === caution, "an accepted alignment that shares little: its caution, as the engine wrote it");
  check(cautionOf(accepted) === null && cautionOf({ ...accepted, little_shared: false, caution: null }) === null, "much shared, or an older server: none");
  check(cautionOf({ ...little, accepted: false }) === null, "not accepted: the verdict says why, no caution");
  check(cautionOf({ ...little, caution: "" }) === null, "no words: nothing shown");
  const lf = alignFigures(little);
  check(lf[0].label === "Overlap" && lf[0].value === "46 %" && lf[0].warn === true, "the overlap is marked when the scans share little");
  check(!alignFigures(accepted)[0].warn, "and not otherwise");
  // Merge stays possible: the caution does not ask to confirm a merge of several scans either
  const allLittle: AlignAllResult = {
    ...all,
    pairs: [pr("a", "b", "chain", { overlap: 0.46, little_shared: true, caution }), pr("a", "c", "chain"), pr("b", "c", "loop", { disagree_mm: 0.27 })],
    little_shared: [{ a: "a", b: "b" }],
    caution: "Where two scans share little, …",
    verdict: all.verdict + " Little is shared between “B” and “A” (46 %).",
  };
  check(allCautionOf(allLittle) === "Where two scans share little, …" && allCautionOf(all) === null, "several scans: the caution when a link shares little");
  check(allCautionOf({ ...allLittle, little_shared: [] }) === null, "no link that shares little: none");
  check(!mergeNeedsConfirm(allLittle), "sharing little does not stop or confirm the merge");
  check(pairLine(allLittle.pairs[0], "Top") === "with “Top”: accepted, 46 % overlap, in the chain, shares little", `a link that shares little says so: ${pairLine(allLittle.pairs[0], "Top")}`);
  check(pairLine(pr("a", "b", "", { overlap: 0.3, little_shared: false }), "Top") === "with “Top”: not accepted, 30 % overlap, not used", "a pair not accepted: nothing more");
  // the merge's own warning is one of its warnings: shown with the others
  const merged: MergeResult = {
    project_id: "p",
    scan_id: "m",
    scan: { id: "m", name: "A + B", created: "", mode: "markerless", n_frames: 1, duration_s: 0, status: "processed", result: null },
    merged_from: ["a", "b"],
    message: "",
    tables: [],
    warnings: ['"A" and "B" share little where they were merged: 46 % of the smaller one\'s part lies within 1 mm of the other. …'],
    caution: '"A" and "B" share little where they were merged: 46 % of the smaller one\'s part lies within 1 mm of the other. …',
  };
  const ml = mergeLeftOut(merged);
  check(ml.warn && ml.warnings.length === 1 && ml.warnings[0].startsWith('"A" and "B" share little'), "the merge's caution is among its warnings, to look at");
}

console.log(`align.test: ${checks} checks passed`);
