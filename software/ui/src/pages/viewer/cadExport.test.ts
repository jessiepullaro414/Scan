/**
 * Checks for the Download panel's "For CAD" group (`npm test`; cadExport.ts): when it is disabled and why, the line
 * that says what will be exported and in which frame, what the outline switch costs, and the download names. Plain
 * TypeScript that Node runs as it is (type stripping), no test framework.
 */
import type { CadSummary } from "../../api/types";
import { cadCounts, cadDownloadName, cadLine, cadUnavailable, outlineCost } from "./cadExport.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`cadExport.test: ${what}`);
}

const base: CadSummary = {
  boundary: "rectangle",
  n_fits: 4,
  counts: { plane: 2, cylinder: 1, hole: 1, point: 0 },
  frame: "part",
  frame_label: "the part's axes",
  notes: [],
  warnings: [],
  stale: [],
  outline: null,
  step_bytes: 12_400,
  macro_bytes: 6_100,
  filenames: { step: "Top-fits.step", macro: "Top-fits.FCMacro" },
  mesh_version: 3,
  reason: null,
};

// disabled, with the reason
check(cadUnavailable(0, true)?.includes("Keep a fit") === true, "no kept fits: keep one first");
check(cadUnavailable(0, false)?.includes("mesh") === true, "no mesh: make it first");
check(cadUnavailable(3, true) === null, "kept fits: available");

// what will be exported
check(cadCounts(base.counts) === "2 planes, 1 cylinder and 1 hole", `counts: ${cadCounts(base.counts)}`);
check(cadCounts({ plane: 1, cylinder: 0, hole: 0, point: 0 }) === "1 plane", "one kind");
check(cadCounts({ plane: 0, cylinder: 0, hole: 3, point: 1 }) === "3 holes and 1 point", "two kinds");
check(cadLine(base) === "4 fitted shapes (2 planes, 1 cylinder and 1 hole), along the part's axes.", `line: ${cadLine(base)}`);
check(cadLine({ ...base, n_fits: 1, counts: { plane: 1, cylinder: 0, hole: 0, point: 0 }, frame: "scan", frame_label: "the scan's own axes" }) === "1 fitted shape (1 plane), along the scan's own axes.", "one fit, the scan's frame");
check(cadLine({ ...base, n_fits: 0, counts: { plane: 0, cylinder: 0, hole: 0, point: 0 }, reason: "this scan has no kept fits to export: fit" }) === "This scan has no kept fits to export: fit.", "the reason when nothing is exported");

// the outline's cost: the sizes of both, the points, the holes kept and left out
const outline: CadSummary = { ...base, boundary: "outline", step_bytes: 2_100_000, macro_bytes: 1_400_000, outline: { planes: 1, points: 2039, holes_kept: 48, holes_left_out: 636 } };
const cost = outlineCost(base, outline);
check(cost.some((l) => l.includes("12\u202fKB") && l.includes("2.0\u202fMB")), `sizes: ${cost.join(" | ")}`);
check(cost.some((l) => l.includes("2,039") || l.includes("2039")), "points");
check(cost.some((l) => l.includes("48 holes kept") && l.includes("636 left out")), "holes");
check(outlineCost(base, null).length === 0, "nothing known yet: nothing said");
const none = outlineCost(base, { ...outline, outline: { planes: 0, points: 0, holes_kept: 0, holes_left_out: 0 } });
check(none.some((l) => l.includes("No plane has an outline")), `no outline kept: ${none.join(" | ")}`);
check(outlineCost(base, { ...outline, outline: { planes: 2, points: 300, holes_kept: 1, holes_left_out: 0 } }).some((l) => l.includes("2 planes")), "two planes");

// the download names: after the scan, the two kinds apart
check(cadDownloadName("Bracket-Top", "fits.step") === "Bracket-Top-fits.step", "STEP name");
check(cadDownloadName("Bracket-Top", "fits.FCMacro") === "Bracket-Top-fits.FCMacro", "macro name");
check(cadDownloadName("", "fits.step") === "scan-fits.step", "no stem");

console.log(`cadExport.test: ${checks} checks passed`);
