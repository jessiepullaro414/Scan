/**
 * Checks for the Check panel's pure part (`npm test`): the tolerance, the answer in words, the sizes
 * and shares as text, and the direction to look at an area from. Plain TypeScript that Node runs as
 * it is (type stripping), no test framework: a failed check throws.
 */
import type { CheckArea, CheckResult } from "../api/types";
import {
  checkNotes,
  checkSentence,
  DEFAULT_TOLERANCE_MM,
  fmtAreaSize,
  fmtExtent,
  fmtShare,
  fmtTolerance,
  hasAnswer,
  kindMeaning,
  kindName,
  meanNormal,
  TOLERANCE_MAX_MM,
  TOLERANCE_MIN_MM,
  unlistedShare,
  validTolerance,
  viewDirection,
} from "./check.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`check.test: ${what}`);
}
const eq = (got: unknown, want: unknown, what: string) => check(got === want, `${what}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);

const area = (share: number, kind: CheckArea["kind"] = "uncertain"): CheckArea => ({
  kind,
  area_mm2: share * 10000,
  share,
  center: [0, 0, 0],
  extent_mm: 10,
  radius_mm: 6,
  n_faces: 100,
  sigma_median_mm: 0.2,
  sigma_worst_mm: 0.4,
  faces: "",
});
const result = (over: Partial<CheckResult>): CheckResult => ({
  version: 3,
  tolerance_mm: 0.1,
  k: 2,
  has_sigma: true,
  area_mm2: 10000,
  min_area_mm2: 5,
  share_ok: 0.92,
  share_uncertain: 0.08,
  share_not_measured: 0,
  n_areas: 3,
  areas: [area(0.05), area(0.02), area(0.01)],
  ...over,
});

// the tolerance
eq(DEFAULT_TOLERANCE_MM, 0.3, "the default");
eq(validTolerance(0.1), 0.1, "a tolerance in range is kept");
eq(validTolerance(0.12345), 0.123, "to a thousandth");
eq(validTolerance(0.001), TOLERANCE_MIN_MM, "too small: the smallest");
eq(validTolerance(500), TOLERANCE_MAX_MM, "too large: the largest");
for (const bad of [0, -1, NaN, Infinity, "0.2", null, undefined]) eq(validTolerance(bad), DEFAULT_TOLERANCE_MM, `${String(bad)}: the default`);
eq(fmtTolerance(0.3), "0.3", "0.3");
eq(fmtTolerance(0.25), "0.25", "0.25");
eq(fmtTolerance(1), "1", "1");
eq(fmtTolerance(0.1 + 0.2), "0.3", "no float dust");

// shares
eq(fmtShare(0.92), "92 %", "a plain share");
eq(fmtShare(0.996), "99.6 %", "nearly all keeps a decimal");
eq(fmtShare(0.9996), "over 99.9 %", "not quite all is never 100 %");
eq(fmtShare(1), "100 %", "all");
eq(fmtShare(0.004), "0.4 %", "a small share keeps a decimal");
eq(fmtShare(0.02), "2 %", "but no empty one");
eq(fmtShare(0.0002), "under 0.1 %", "a tiny share is never 0 %");
eq(fmtShare(0), "0 %", "none");
eq(fmtShare(null), "–", "no value");

// sizes
eq(fmtAreaSize(3.24), "3.2 mm²", "a few mm²");
eq(fmtAreaSize(42.4), "42 mm²", "tens of mm²");
eq(fmtAreaSize(140), "1.4 cm²", "cm² from 100 mm²");
eq(fmtAreaSize(12500), "125 cm²", "many cm²");
eq(fmtExtent(4.52), "4.5 mm", "a short reach");
eq(fmtExtent(23.4), "23 mm", "a long reach");

// the kinds
eq(kindName("uncertain"), "Uncertain", "uncertain");
eq(kindName("not_measured"), "Not measured", "not measured");
check(kindMeaning("uncertain", 0.1).includes("2σ") && kindMeaning("uncertain", 0.1).includes("± 0.1 mm"), "uncertain: 2σ against the tolerance");
check(kindMeaning("not_measured", 0.1).includes("Hole filling"), "not measured: hole filling");
for (const text of [kindMeaning("uncertain", 0.1), kindMeaning("not_measured", 0.1)]) check(!text.includes("—"), "no em dash");

// the answer in one sentence
eq(checkSentence(result({})), "92 % of the surface meets ± 0.1 mm; 3 areas need another pass.", "the plan's example");
eq(checkSentence(result({ n_areas: 1, areas: [area(0.08)] })), "92 % of the surface meets ± 0.1 mm; 1 area needs another pass.", "one area");
eq(
  checkSentence(result({ tolerance_mm: 0.3, share_ok: 1, share_uncertain: 0, n_areas: 0, areas: [] })),
  "All of the surface meets ± 0.3 mm; no area needs another pass.",
  "all of it",
);
eq(
  checkSentence(result({ tolerance_mm: 0.3, share_ok: 0.998, share_uncertain: 0.002, n_areas: 0, areas: [] })),
  "99.8 % of the surface meets ± 0.3 mm; no area is large enough to list.",
  "only specks",
);
eq(
  checkSentence(result({ tolerance_mm: 0.05, share_ok: 0, share_uncertain: 1, n_areas: 1, areas: [area(1)] })),
  "None of the surface meets ± 0.05 mm; 1 area needs another pass.",
  "none of it",
);
eq(checkSentence(result({ n_areas: 120 })).includes("120 areas need"), true, "the count is of all areas, not of the list");

// what is not in the list
check(Math.abs((unlistedShare(result({})) as number) - 0) < 1e-12, "everything listed: nothing left");
check(Math.abs((unlistedShare(result({ share_ok: 0.9, share_uncertain: 0.1 })) as number) - 0.02) < 1e-9, "the rest is 2 %");
eq(unlistedShare(result({ has_sigma: false, share_ok: null, share_uncertain: null })), null, "no answer, no rest");
eq(unlistedShare(result({ share_ok: 0.9200000000001 })), 0, "rounding dust is nothing");
eq(checkNotes(result({})).length, 0, "nothing to add when all is listed and measured");
{
  const notes = checkNotes(result({ share_ok: 0.9, share_uncertain: 0.1 }));
  eq(notes.length, 1, "one note");
  eq(notes[0], "2 % of the surface also misses it, in spots under 5.0 mm²: too small to list, but counted.", "the specks");
  const tiny = checkNotes(result({ share_ok: 0.9196, share_uncertain: 0.0804 }));
  eq(tiny[0], "Under 0.1 % of the surface also misses it, in spots under 5.0 mm²: too small to list, but counted.", "a tiny rest reads as a sentence");
}
{
  const notes = checkNotes(result({ share_ok: 0.5, share_uncertain: 0.5, n_areas: 80 }));
  check(notes.includes("The 3 largest of 80 areas are listed."), "the list is cut");
  check(notes.some((n) => n.startsWith("42 % of the surface also misses it, in the areas not listed and in spots under")), "the rest, cut list");
}
{
  const notes = checkNotes(result({ share_ok: 0.9, share_uncertain: 0.07, share_not_measured: 0.03, areas: [area(0.07), area(0.03, "not_measured")], n_areas: 2 }));
  eq(notes[0], "Of the rest, 7 % of the surface is uncertain and 3 % was not measured (filled holes).", "the split");
  const only = checkNotes(result({ share_ok: 0.97, share_uncertain: 0, share_not_measured: 0.03, areas: [area(0.03, "not_measured")], n_areas: 1 }));
  eq(only[0], "All of the rest was not measured (filled holes).", "only filled holes");
}
for (const r of [result({}), result({ share_ok: 0.5, share_uncertain: 0.4, share_not_measured: 0.1, n_areas: 80 })])
  for (const text of [checkSentence(r), ...checkNotes(r)]) check(!text.includes("—"), "no em dash in the answer");

// is there an answer at all
check(hasAnswer(result({})), "a mesh with sigmas has an answer");
check(!hasAnswer(result({ has_sigma: false, share_ok: null, share_uncertain: null })), "no sigmas: none");
check(!hasAnswer(result({ share_ok: 0, share_uncertain: 0, share_not_measured: 1 })), "nothing measured: none");
check(!hasAnswer(result({ share_ok: null, share_uncertain: null, share_not_measured: null })), "an empty mesh: none");

// the direction to look from
{
  // a square in the plane z = 0, wound to face +z
  const P = [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0];
  const tris = [0, 1, 2, 0, 2, 3];
  const up = meanNormal(tris, P, null);
  check(!!up && Math.abs(up[2] - 1) < 1e-9 && Math.abs(up[0]) < 1e-9, "from the winding: +z");
  // the file's normals win over the winding
  const N = [0, 0, -1, 0, 0, -1, 0, 0, -1, 0, 0, -1];
  const down = meanNormal(tris, P, N);
  check(!!down && Math.abs(down[2] + 1) < 1e-9, "from the normals: -z");
  // a large triangle outweighs a small one
  const P2 = [0, 0, 0, 10, 0, 0, 0, 10, 0, 0, 0, 0, 0, 0.1, 0, 0, 0, 0.1];
  const mostly = meanNormal([0, 1, 2, 3, 4, 5], P2, null);
  check(!!mostly && mostly[2] > 0.99, "weighted by area");
  // two faces back to back face every way: no direction
  eq(meanNormal([0, 1, 2, 0, 2, 1], P, null), null, "back to back: none");
  eq(meanNormal([], P, null), null, "no triangles: none");
  eq(meanNormal([0, 0, 0], P, null), null, "no area: none");
}

// where the camera goes: the normal in the view's axes (y and z change sign), never on the pole
{
  const side = viewDirection([1, 0, 0]);
  check(side[0] === 1 && Math.abs(side[1]) === 0 && Math.abs(side[2]) === 0, "a side face: straight at it");
  const front = viewDirection([0, 0, -2]);
  check(Math.abs(front[2] - 1) < 1e-12 && Math.abs(front[1]) < 1e-12, "towards the first camera: +z of the view, unit length");
  const top = viewDirection([0, -1, 0]); // up in the scan's frame (y down)
  check(top[1] > 0.9 && top[1] < 0.96, "a face that looks up: from above, leaned off the pole");
  check(top[2] > 0 && top[0] > 0 && Math.abs(Math.hypot(...top) - 1) < 1e-12, "leaned to the front, unit length");
  const under = viewDirection([0, 1, 0]);
  check(under[1] < -0.9 && under[1] > -0.96, "a face that looks down: from below, leaned too");
  const slope = viewDirection([0, -0.8, -0.6]);
  check(Math.abs(slope[1] - 0.8) < 1e-12 && Math.abs(slope[2] - 0.6) < 1e-12, "a slope is left as it is");
  // an open scan made from above (the mesh faces up: -y in its frame)
  const up = [0, -1, 0];
  const same = viewDirection([0, -0.8, -0.6], up);
  check(Math.abs(same[1] - 0.8) < 1e-12 && Math.abs(same[2] - 0.6) < 1e-12, "an area that faces the scan's side: along its normal");
  const foot = viewDirection([0, 0.7, -0.7], up); // the foot of a ball on the table: it faces down and sideways
  check(Math.abs(foot[1] - 0.25) < 1e-9 && foot[2] > 0.9 && Math.abs(Math.hypot(...foot) - 1) < 1e-9, "an area that faces away: from the scan's side, at a flat angle");
  check(foot[1] * -0.7 + foot[2] * 0.7 > 0, "and still onto its front");
  const wall = viewDirection([1, 0, 0], up);
  check(Math.abs(wall[1] - 0.25) < 1e-9 && wall[0] > 0.9, "a wall square to the scan's side is looked at from a little above");
  const away = viewDirection([0, 1, 0], up);
  check(away[1] > 0.9, "an area that faces straight away: from the scan's side");
  const round = viewDirection([0, 0.7, -0.7], null);
  check(round[1] < -0.69, "scanned all round: along the normal, wherever it points");
}

console.log(`check.test: ${checks} checks passed`);
