/**
 * Checks for a second pass (`npm test`, FEATURE_PLAN N2): what "Scan these areas" keeps and Start sends, the
 * preset the pass takes, the steps after Stop in words, and the before and after. Plain TypeScript that Node
 * runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { CheckPlanPose, CheckPose, CheckResult, CheckWhy, PassCompare } from "../api/types";
import { areaAfterWords, areaLines, missingSentence, passOfText, placesWords, areasWords, chainActive, chainCanContinue, chainRows, compareRows, compareSentence, passPresetFields, passStartBody, passWaitingNote, pendingFromCheck } from "./pass.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`pass.test: ${what}`);
}

const pose = (d = 254, tilt = 0): CheckPose => ({
  T_wc: [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
  position_mm: [0, 0, d],
  camera_position_mm: [-30, 0, d],
  look_dir: [0, 0, -1],
  up: [0, 1, 0],
  target_mm: [0, 0, 0],
  distance_mm: d,
  tilt_deg: tilt,
  azimuth_deg: 0,
});
const area = (mm2: number) => ({ kind: "uncertain" as const, area_mm2: mm2, share: 0.1, center: [1, 2, 3] as [number, number, number], extent_mm: 5, radius_mm: 3, n_faces: 10, sigma_median_mm: 0.2, sigma_worst_mm: 0.4, faces: "AAAA" });
const result = { version: 7, tolerance_mm: 0.3, k: 2, has_sigma: true, area_mm2: 1000, min_area_mm2: 4, share_ok: 0.7, share_uncertain: 0.3, share_not_measured: 0, n_areas: 3, areas: [area(140), area(60), area(20)] } as CheckResult;
const plan: CheckPlanPose[] = [{ area: 0, kind: "area", pose: pose(), share_seen: 0.9, border_seen: null, also_sees: [{ area: 1, share: 0.8 }] }];
const why = {
  version: 7,
  tolerance_mm: 0.3,
  n_areas: 3,
  plan,
  areas: [0, 1, 2].map((i) => ({ index: i, view: i === 2 ? { ok: true, pose: pose(254, 20), text: "Hold it 20° off square." } : { ok: false, pose: null, text: "No pose." } })),
} as unknown as CheckWhy;
const scan = { id: "s1", name: "Top", preset: { id: "thin-sheet-small-parts", name: "Thin sheet", hand: { quality: "best" as const } }, result: { mesh_url: "/m.ply", stl_url: "", laser_points_url: null, report: {} } };

// ---------------------------------------------------------------- Scan these areas, and Start
const p = pendingFromCheck({ projectId: "p1", scan, result, why });
check(p.areas.length === 3 && p.areas[0].index === 0 && p.areas[0].faces === "AAAA", "every listed area, with its faces to light");
check(p.poses.length === 2 && p.poses[0].text!.startsWith("Area 1: about") && p.poses[1].area === 2 && p.poses[1].text === "Area 3: about 250\u202fmm away, 20° from straight on.", "the plan's places, then an area's own place the plan leaves out");
// the plan's place for area 1 also sees most of area 2: area 2 needs no place of its own (it has none here anyway)
const many = pendingFromCheck({ projectId: "p1", scan, result: { ...result, areas: Array.from({ length: 12 }, () => area(10)) } as CheckResult, why: { ...why, areas: Array.from({ length: 12 }, (_, i) => ({ index: i, view: { ok: true, pose: pose(), text: "Here." } })) } as unknown as CheckWhy });
check(many.poses.length === 6 && !many.poses.some((q) => q.area === 1), "a few places in all, none for an area the plan sees");
check(p.version === 7 && p.tolerance === 0.3 && p.meshUrl === "/m.ply" && p.firstName === "Top", "the answer it was made from");
const fromPlan = pendingFromCheck({ projectId: "p1", scan, result, why, only: [0, 1], plan });
check(fromPlan.areas.map((a) => a.index).join() === "0,1" && fromPlan.poses.length === 1, "from the plan: its areas and its places only");
// found in review: pressed before the reasons arrived, the places are "still being worked out", not "none"
const early = pendingFromCheck({ projectId: "p1", scan, result, why: null });
check(early.poses.length === 0 && early.placesPending === true, "no reasons yet: the places are pending");
check(p.placesPending === false && fromPlan.placesPending === false, "with the reasons (or the plan's places): not pending");
check(placesWords(early) === "The places to scan from are still being worked out…", placesWords(early) ?? "null");
check(placesWords({ ...early, placesPending: false, placesFailed: true })!.startsWith("The places to scan from could not be worked out"), "failed: said so");
check(placesWords({ ...early, placesPending: false }) === "No place to scan from was found for these areas: scan the lit areas from where they can be seen squarely.", "asked, and none");
check(placesWords(p) === null, "places listed: nothing to add");
const body = passStartBody(p, false);
check(body.scan_id === "s1" && body.areas.join() === "0,1,2" && body.version === 7 && body.same_setup === true && body.poses!.length === 2, "Start's continues: the part was not moved, so the setup is the same");
check(passStartBody(p, true).same_setup === false, "the part was moved: not the same setup");
// the first scan's preset: nothing is sent, so the engine takes it and says so; another one is sent
check(Object.keys(passPresetFields({ preset: "thin-sheet-small-parts", preset_values: { quality: "best" } }, p.preset)).length === 0, "the first scan's preset: not sent");
check(passPresetFields({ preset: "general" }, p.preset).preset === "general", "another preset: sent");
check(Object.keys(passPresetFields({ preset: "general" }, null)).length === 0, "a first scan made before presets: General, not sent");
check(areasWords(p.areas, 0.3) === "3 areas, 220\u202fmm² in all, at ± 0.3\u202fmm.", areasWords(p.areas, 0.3));

// ---------------------------------------------------------------- the steps after Stop
const rows = (state: string, step: string | null) => chainRows({ state: state as never, step: step as never }).map((r) => r.state).join(" ");
check(rows("waiting", null) === "waiting waiting waiting", "before Stop");
check(rows("processing", "process") === "running waiting waiting", "making the mesh");
check(rows("aligning", "align") === "done running waiting", "aligning");
check(rows("merged", "merge") === "done done done", "merged");
check(rows("not_aligned", "align") === "done stopped skipped", "not accepted: nothing merged");
check(rows("cancelled", "process") === "stopped waiting waiting", "cancelled while making the mesh");
check(chainActive("merging") && !chainActive("merged") && chainCanContinue("not_aligned") && chainCanContinue("cancelled") && !chainCanContinue("merged"), "what can be done");

// ---------------------------------------------------------------- before and after
const cmp = {
  tolerance_mm: 1,
  note: "",
  rule: { match_mm: 2.5, match_angle_deg: 60, passes_share: 0.9 },
  first: { id: "s1", name: "Top", share_ok: 0.72, share_uncertain: 0.28, share_not_measured: 0, n_areas: 12, area_mm2: 1, has_sigma: true },
  merged: { id: "m", name: "Top + Pass", share_ok: 0.81, share_uncertain: 0.19, share_not_measured: 0, n_areas: 1, area_mm2: 1, has_sigma: true },
  areas: [
    { index: 1, kind: "uncertain", area_mm2: 140, center: [0, 0, 0], n_faces: 3, share_ok: 0.96, found_share: 1, state: "passes" },
    { index: 4, kind: "uncertain", area_mm2: 30, center: [0, 0, 0], n_faces: 3, share_ok: 0, found_share: 0, state: "not_found" },
  ],
  areas_note: null,
} as PassCompare;
check(compareSentence(cmp) === "The share of the surface that meets ± 1\u202fmm rose by 9 points: 72\u202f% before, 81\u202f% after.", compareSentence(cmp));
const r = compareRows(cmp);
check(r[0].share === "72\u202f%" && r[0].areas === "12 areas" && r[1].areas === "1 area" && r[1].name === "Top + Pass", "the two rows");
check(areaAfterWords(cmp.areas[0]) === "Area 2 (140\u202fmm²): passes now (96\u202f% of it).", areaAfterWords(cmp.areas[0]));
check(areaAfterWords(cmp.areas[1]) === "Area 5 (30\u202fmm²): not in the merged scan.", areaAfterWords(cmp.areas[1]));
check(compareSentence({ ...cmp, merged: { ...cmp.merged, share_ok: null } }).includes("can't be told"), "no confidence");

// found in review: the merge left the tables out (the part was moved), so the whole merged mesh holds far less surface
// than the first. Its whole-mesh share fell by 35 points while nothing got worse: never "fell by" for missing surface.
const moved = {
  ...cmp,
  tolerance_mm: 0.3,
  first: { ...cmp.first, name: "Scan 2", share_ok: 0.65, n_areas: 667 },
  merged: { ...cmp.merged, name: "Scan 2 + Scan 3", share_ok: 0.3, n_areas: 16 },
  common: { found_share: 0.12, before_ok: 0.41, after_ok: 0.52 },
  missing: { share: 0.88, area_mm2: 52000, why: "the part was moved, so each scan's table was left out of the merge" },
  whole_note: "x",
  areas: Array.from({ length: 47 }, (_, i) => ({ index: i, kind: "uncertain" as const, area_mm2: 10, center: [0, 0, 0] as [number, number, number], n_faces: 1, share_ok: 0, found_share: 0, state: "not_found" as const })),
} as PassCompare;
const ms = compareSentence(moved);
check(!ms.includes("fell") && ms.includes("surface both scans hold") && ms.includes("rose by 11 points") && ms.includes("12\u202f%"), ms);
check(missingSentence(moved) === "88\u202f% of the first scan's surface (52,000\u202fmm²) is not in the merged scan: the part was moved, so each scan's table was left out of the merge.", missingSentence(moved) ?? "null");
check(missingSentence({ ...moved, missing: { share: 0.001, area_mm2: 3, why: null } }) === null, "a sliver missing is not worth a line");
check(missingSentence({ ...moved, missing: { share: 0.2, area_mm2: 900, why: null } })!.endsWith("is not in the merged scan."), "no reason known: none made up");
const lines = areaLines(moved.areas);
check(lines.length === 5 && lines[4] === "And 43 more: 43 not in the merged scan.", lines.join(" | "));
// several areas counted together read as several ("46 partly pass", seen in review as "46 partly passes"); one stays one
const partly = areaLines(moved.areas.map((a, i) => ({ ...a, state: i === 6 ? ("passes" as const) : ("partly" as const) })));
check(partly[4] === "And 43 more: 42 partly pass, 1 passes now." || partly[4] === "And 43 more: 1 passes now, 42 partly pass.", partly[4]);
check(compareRows(moved)[0].label === "Before, whole mesh" && compareRows(moved)[1].label === "After, whole mesh", "the whole-mesh rows say what they are");
// on the same surface a real drop is said as one
check(compareSentence({ ...moved, common: { found_share: 0.99, before_ok: 0.8, after_ok: 0.7 } }).includes("fell by 10 points"), "a real drop on the same surface");

check(passOfText({}) === null, "an ordinary scan");
check(passOfText({ continues: { scan_name: "Top", chain: { state: "merged" } } as never }) === "Second pass of “Top”, merged", "a merged pass");

// a scan that starts while the page has a pass set up, but is not that pass (the scanner's trigger starts an
// ordinary scan, never a pass): said in one plain sentence, while it records
{
  const waiting = passWaitingNote(p, true, null);
  check(waiting === "An ordinary scan was started, not the second pass of “Top”: the pass is still waiting, and Start on this page begins it once this scan is stopped.", `ordinary scan while a pass waits: ${waiting}`);
  check(passWaitingNote(p, true, "s9") === waiting, "a pass of another scan is not this pass either");
  check(passWaitingNote(p, true, "s1") === null, "the pass itself started: nothing to say");
  check(passWaitingNote(p, false, null) === null, "nothing records: the pass panel says it");
  check(passWaitingNote(null, true, null) === null, "no pass set up: an ordinary scan as asked");
  check(!/[—–]/.test(waiting!) && (waiting!.match(/\. /g) ?? []).length === 0, "one sentence, no dashes");
}

console.log(`pass.test: ${checks} checks passed`);
