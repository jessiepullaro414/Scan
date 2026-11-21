/**
 * Checks for the reasons and poses of the Check panel (`npm test`, FEATURE_PLAN N1): the words, the plan's
 * lines, the side view, the scanner's outline at its pose and the camera move that shows it. Plain
 * TypeScript that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { CheckPlanPose, CheckPose, CheckResult, CheckWhy, CheckWhyArea } from "../api/types";
import { alsoWords, placeLetter, planTitle, planWords, poseAxes, poseFrame, poseShort, REASON_TITLE, reasonWords, scannerLines, sideSketch, unknownWords, viewWords, whyFits } from "./checkWhy.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`checkWhy.test: ${what}`);
}
const near = (a: ArrayLike<number>, b: ArrayLike<number>, tol = 1e-6) => a.length === b.length && Array.from(a).every((x, i) => Math.abs(x - b[i]) <= tol);

const ev = (o: Partial<CheckWhyArea["evidence"]> = {}): CheckWhyArea["evidence"] => ({
  looks: null,
  looks_passing: null,
  view_angle_deg: null,
  distance_mm: null,
  in_view_share: null,
  edge_share: null,
  border_share: 0,
  outward: null,
  ...o,
});

// ---------------------------------------------------------------- the words
const steep = reasonWords({ reason: "steep", kind: "uncertain", evidence: ev({ view_angle_deg: 68.4 }) }, 254);
check(steep === "Seen only at a slant (the best look was 68° from straight on). Hold the scanner straighter to it.", `steep: the angle and what to do (U22): ${steep}`);
const far = reasonWords({ reason: "far", kind: "uncertain", evidence: ev({ distance_mm: 402.3 }) }, 254);
check(far.includes("about 400\u202fmm away") && far.includes("about 250\u202fmm from it"), `far: the distance and the working distance, rounded to 10 mm (U22): ${far}`);
const few = reasonWords({ reason: "few_looks", kind: "uncertain", evidence: ev({ looks: 4, looks_passing: 12 }) }, 254);
check(few.includes("4 separate looks") && few.includes("had 12"), "few looks: the counts");
const edge = reasonWords({ reason: "edge", kind: "uncertain", evidence: ev() }, 254);
check(edge.includes("another pass alone does not lower it") && edge.includes("own scatter"), "edge: worded honestly");
check(reasonWords({ reason: "not_seen", kind: "not_measured", evidence: ev() }, null).includes("hole filling"), "not seen: a filled hole");
check(reasonWords({ reason: "not_seen", kind: "uncertain", evidence: ev() }, null).includes("never saw"), "not seen: beside unseen surface");
check(reasonWords({ reason: "unknown", kind: "uncertain", evidence: ev() }, null).includes("do not tell"), "unknown");
for (const w of [steep, far, few, edge]) check(!w.includes("—") && !w.includes("–"), "no dashes in the words");

check(alsoWords({ reason: "steep", reasons: ["steep"] }) === null, "also: nothing more");
check(alsoWords({ reason: "steep", reasons: ["steep", "few_looks", "edge"] }) === "Also seen too few times and on an edge or a tight curve.", "also: two more");

const unk = unknownWords({
  unknown_because: [
    "steep, far: the scanner's calibration is not here (it goes with the recording, which was removed)",
    "few_looks: the mesh has no count of looks (made by Open3D's model, or an older file)",
    "edge: the voxel size the mesh was made at is not known",
  ],
});
check(unk.length === 3 && unk[0].startsWith("The angle and distance it was seen from can't be told: the scanner's calibration"), "unknown: the camera path");
check(unk[1].includes("graphics card") && !unk[1].includes("Open3D"), "unknown: no looks, in plain words");
check(unk[2].includes("how fine") && !unk[2].includes("voxel"), "unknown: no voxel size, in plain words");

// ---------------------------------------------------------------- the pose and the plan
const pose: CheckPose = {
  T_wc: [
    [1, 0, 0, -30],
    [0, -1, 0, 0],
    [0, 0, -1, 254],
    [0, 0, 0, 1],
  ],
  position_mm: [0, 0, 254],
  camera_position_mm: [-30, 0, 254],
  look_dir: [0, 0, -1],
  up: [0, 1, 0],
  target_mm: [0, 0, 0],
  distance_mm: 254,
  tilt_deg: 0,
  azimuth_deg: 0,
};
check(poseShort(pose) === "about 250\u202fmm away, straight on" && poseShort({ distance_mm: 254, tilt_deg: 19.6 }) === "about 250\u202fmm away, 20° from straight on", `pose in words (U22): ${poseShort(pose)}`);
const plan: CheckPlanPose = { area: 1, kind: "area", pose, share_seen: 0.95, border_seen: null, also_sees: [{ area: 2, share: 0.7 }, { area: 4, share: 0.2 }, { area: 5, share: 0.9 }] };
check(planWords(plan) === "Area 2: about 250\u202fmm away, straight on. It sees 95\u202f% of it, and most of areas 3 and 6.", `plan, one sentence form (U23): ${planWords(plan)}`);
check(viewWords(pose, 0.95) === "About 250\u202fmm away, straight on. It sees 95\u202f% of it.", `an area's own place, the same form (U23): ${viewWords(pose, 0.95)}`);
check(viewWords(pose, null) === "About 250\u202fmm away, straight on.", "an area's own place, share unknown");
check(placeLetter(0) === "A" && placeLetter(2) === "C" && placeLetter(5) === "F", "places lettered A, B, C (U23)");
check(REASON_TITLE.steep === "Seen only at a slant", "the reason's title (U22)");
check(planWords({ ...plan, kind: "more", also_sees: [] }).startsWith("Area 2 again, from another side: about"), `plan: another side: ${planWords({ ...plan, kind: "more", also_sees: [] })}`);
// many other areas: the first four by name, the rest counted (seen in review: one place listed 29 areas)
const many = { ...plan, also_sees: [4, 6, 21, 25, 29, 30, 31, 41, 42, 49].map((area) => ({ area, share: 0.8 })) };
check(planWords(many).endsWith("and most of areas 5, 7, 22, 26 and 6 more."), `plan, many areas: ${planWords(many)}`);
const five = { ...plan, also_sees: [4, 6, 21, 25, 29].map((area) => ({ area, share: 0.8 })) };
check(planWords(five).endsWith("and most of areas 5, 7, 22, 26 and 30."), `plan, five areas: ${planWords(five)}`);
check(planTitle(3) === "3 places to scan from" && planTitle(1) === "1 place to scan from", "plan title");

// the scanner's axes: camera x = right, y = down (= -up), z = forward, as T_wc's columns
const ax = poseAxes(pose);
check(near(ax.fwd, [0, 0, -1]) && near(ax.up, [0, 1, 0]) && near(ax.right, [1, 0, 0]), "axes of a square-on pose");
check(near(ax.right, [pose.T_wc[0][0], pose.T_wc[1][0], pose.T_wc[2][0]]), "right is camera x");
const lines = scannerLines(pose);
check(lines.body.length === 12 * 6 && lines.cone.length === 8 * 6 && lines.sight.length === 6, "box, cone, sight line");
check(near(lines.sight, [0, 0, 254, 0, 0, 0]), "the sight line runs from the scanner to the area");
let zMin = Infinity,
  zMax = -Infinity;
for (let i = 2; i < lines.body.length; i += 3) {
  zMin = Math.min(zMin, lines.body[i]);
  zMax = Math.max(zMax, lines.body[i]);
}
check(Math.abs(zMax - 294) < 1e-3 && Math.abs(zMin - 254) < 1e-3, "the box lies behind the scanner's centre, away from the area");
const fr = poseFrame(pose, [0, 0, 1], 10);
check(near(fr.sphere.center, [0, -0, -127]) && fr.sphere.radius > 127, "the view frames scanner and area (view axes)");
check(Math.abs(Math.hypot(...fr.direction) - 1) < 1e-9 && fr.direction[0] > 0.5, "seen from the scanner's side");

// ---------------------------------------------------------------- the side view
const sq = sideSketch(0);
check(Math.abs(sq.scanner.x - 60) < 1e-9 && sq.scanner.y < sq.surface[1], "square on: right above the area");
const tilted = sideSketch(40);
check(tilted.scanner.x > 60 && tilted.scanner.angleDeg === 40 && tilted.sight[2] === 60, "tilted: leaning off the normal");

// ---------------------------------------------------------------- which answer it fits
const result = { version: 3, tolerance_mm: 0.1, areas: [{ n_faces: 10, kind: "uncertain" }] } as unknown as CheckResult;
const why = { version: 3, tolerance_mm: 0.1, areas: [{ n_faces: 10, kind: "uncertain" }] } as unknown as CheckWhy;
check(whyFits(why, result), "fits the same answer");
check(!whyFits({ ...why, version: 4 }, result) && !whyFits({ ...why, tolerance_mm: 0.3 }, result), "not another version or tolerance");

console.log(`checkWhy.test: ${checks} checks passed`);
