/**
 * Checks for alignChoice.ts (`npm test`): sure or not sure, the poses to look at, the pose in use, and
 * when a merge is offered. Plain TypeScript that Node runs as it is, no test framework: a failed check throws.
 */
import type { AlignPose, AlignResult } from "../api/types.ts";
import { choiceText, mergeOffer, poseChoices, poseInUse, standingLabel, standingOf } from "./alignChoice.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`alignChoice.test: ${what}`);
}

const I = [
  [1, 0, 0, 0],
  [0, 1, 0, 0],
  [0, 0, 1, 0],
  [0, 0, 0, 1],
];
const HALF = [
  [-1, 0, 0, 10],
  [0, -1, 0, 0],
  [0, 0, 1, 0],
  [0, 0, 0, 1],
];
const pose = (T: number[][], pick: number, apart: number, chosen: boolean, from: string[], reached = 3): AlignPose => ({
  T,
  chosen,
  pick,
  overlap: pick,
  rmse_mm: 0.3,
  fit_mm: 0.25,
  reached,
  from,
  apart_mm: apart,
});
type R = Pick<AlignResult, "accepted" | "doubt" | "poses" | "start" | "T">;
const sure: R = { accepted: true, doubt: null, poses: [pose(I, 0.8, 0, true, ["the first guesses"]), pose(HALF, 0.4, 60, false, ["turned round"], 1)], start: "features", T: I };
const turned: R = { accepted: false, doubt: "turned", poses: [pose(HALF, 0.71, 0, true, ["the first guesses"], 8), pose(I, 0.7, 61, false, ["turned round"], 2)], start: "features", T: HALF };
const none: R = { accepted: false, doubt: "part", poses: [], start: "features", T: I };
const old: R = { accepted: false, doubt: undefined, poses: undefined, start: "features", T: I }; // an older server
const points: R = { accepted: false, doubt: "points", poses: [pose(HALF, 0.2, 0, true, ["your points"], 1), pose(I, 0.7, 80, false, ["the first guesses"], 6)], start: "given", T: HALF };

// ------------------------------------------------------------------ sure or not
check(standingOf(sure) === "sure" && standingLabel(sure) === "Accepted", "accepted is sure");
check(standingOf(turned) === "not_sure" && standingLabel(turned) === "Not sure", "a pose turned round fits as well: not sure");
check(standingOf(points) === "not_sure" && standingLabel(points) === "Not sure: check your points", "the points and the shapes disagree");
check(standingOf(none) === "failed" && standingLabel(none) === "Not aligned", "no usable pose");
check(standingOf(old) === "failed", "an older server's refusal: no poses to offer");

// ------------------------------------------------------------------ the poses
const c = poseChoices(turned);
check(c.length === 2 && c[0].title === "Pose 1: the engine's best" && c[1].title === "Pose 2: 61 mm from pose 1", "the poses, the engine's first");
check(c[1].detail === "Fits 99 % as well as pose 1 · 2 starts ended here (turned round)", "how well the other fits, and how it was found");
check(c[0].detail === "8 starts ended here (the first guesses)", "the first: how often it was found");
check(poseChoices(old).length === 0, "none from an older server");
check(poseInUse(turned, null) === HALF && poseInUse(turned, 1) === I && poseInUse(turned, 5) === HALF, "the pose in use: the chosen one, else the engine's");

// ------------------------------------------------------------------ the merge
check(mergeOffer(sure, null).offered && !mergeOffer(sure, null).confirm, "sure: merged at once");
const o = mergeOffer(turned, null);
check(!o.offered && !!o.why && o.why.includes("not sure") && o.why.includes("choose one"), "not sure and nothing chosen: no merge, and why");
check(mergeOffer(turned, 1).offered && !mergeOffer(turned, 1).confirm, "a pose the person chose: merged at it");
check(!mergeOffer(none, null).offered && mergeOffer(none, null).why!.includes("Pick matching points"), "not aligned: pick points");
check(mergeOffer(points, null).offered && mergeOffer(points, null).confirm, "their own points the shapes disagree with: theirs to decide, after a warning");
check(choiceText(turned, 1) === "Your choice: pose 2 of 2. The engine was not sure; you looked and chose it.", "the choice said as theirs");
check(choiceText(turned, 0)!.includes("(the engine's best)") && choiceText(turned, null) === null && choiceText(none, 0) === null, "only a real choice");

console.log(`alignChoice.test: ${checks} checks passed`);
