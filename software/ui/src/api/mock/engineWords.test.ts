/**
 * The demo's copies of the engine's texts (UX_PLAN R12, R19, R23; APP_API.md, the before/now table of "Found on the
 * real engine: the server's side"), `npm test`: the edit labels, the summary of Remove loose bits with the rule that
 * took its largest piece, and the progress lines, as `ohs/server/processing.py` writes them.
 */
import { FAILURE_TEXT, editLabel, floatersRule, floatersSummary, printCheckVerdict, trackProgress } from "./engineWords.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`engineWords.test: ${what}`);
}

// labels (APP_API.md "Edits", "Labels")
check(editLabel("remove_floaters", { min_size_mm: 5, min_fraction: 0.01 }) === "Remove loose bits smaller than 5 mm or under 1 % of the mesh", editLabel("remove_floaters", { min_size_mm: 5, min_fraction: 0.01 }));
check(editLabel("remove_floaters", { min_triangles: 1200 }) === "Remove loose bits under 1,200 triangles", "by triangles");
check(editLabel("remove_floaters", { keep_largest_only: true }) === "Keep the largest piece only", "the largest only");
check(editLabel("smooth", { method: "bilateral", strength: 0.5, keep_edges: true, away_from_edges: true }) === "Smooth 50 % (keeping the shape, sharp edges kept, off edges)", editLabel("smooth", { method: "bilateral", strength: 0.5, keep_edges: true, away_from_edges: true }));
check(editLabel("smooth", { method: "taubin", strength: 0.25, keep_edges: false }) === "Smooth 25 % (evenly)", "Taubin: evenly");
check(editLabel("smooth", { method: "taubin", strength: 0.5, keep_edges: true, flat_only: true, within_sigma: true }) === "Smooth 50 % (evenly, sharp edges kept, flat areas only, within the scatter)", "flat, within");
check(editLabel("fill_holes", { max_hole_mm: 15 }) === "Fill holes up to 15 mm around (about Ø 5 mm)", editLabel("fill_holes", { max_hole_mm: 15 }));
check(editLabel("fill_holes", { max_hole_mm: 60, watertight: true }) === "Make watertight: close every opening up to 60 mm around (about Ø 19 mm)", editLabel("fill_holes", { max_hole_mm: 60, watertight: true }));
check(editLabel("decimate", { target_triangles: 200000 }) === "Simplify to 200,000 triangles", "simplify");
check(editLabel("decimate", { ratio: 0.5 }) === "Simplify to 50 % of the triangles", "simplify by share");
check(editLabel("decimate", { max_error_mm: 0.02 }) === "Simplify to within 0.02 mm", "simplify within");
check(editLabel("delete_faces", {}, 1234) === "Delete 1,234 triangles" && editLabel("keep_faces", {}, 12) === "Keep 12 triangles", "delete / keep");
for (const l of [editLabel("smooth", { method: "bilateral", strength: 0.5 }), editLabel("smooth", { method: "taubin", strength: 0.5 })]) check(!/Taubin|bilateral|σ/.test(l), `no method names: ${l}`);

// the rule that took the largest removed piece (processing._floater_rule), and the summary that names it
const P = { min_size_mm: 5, min_fraction: 0.01 };
check(floatersRule(P, 4.2, 300, 100_000) === "size", "smaller than 5 mm");
check(floatersRule(P, 40.8, 900, 100_000) === "share", "large, but under 1 % of the mesh");
check(floatersRule(P, 40.8, 5000, 100_000) === null, "large and over 1 %: no rule (never removed)");
check(floatersRule({ min_triangles: 100 }, 40, 50, 10_000) === "triangles", "under 100 triangles");
check(floatersRule({ keep_largest_only: true }, 40, 5000, 10_000) === "largest_only", "the largest only");
check(floatersRule({}, 4, 10, 1000) === "size", "{} means the default limits");
check(floatersSummary({ pieces: 3, triangles: 1234, largest_mm: 4.2, largest_rule: "size" }, P) === "Removed 3 loose bits, the largest 4.2 mm across, smaller than 5 mm (1,234 triangles)", "size");
check(floatersSummary({ pieces: 297, triangles: 12345, largest_mm: 40.8, largest_rule: "share" }, P) === "Removed 297 loose bits, the largest 40.8 mm across but under 1 % of the mesh (12,345 triangles)", "share");
check(floatersSummary({ pieces: 1, triangles: 12, largest_mm: 4.2, largest_rule: "size" }, P) === "Removed 1 loose bit, 4.2 mm across, smaller than 5 mm (12 triangles)", "one bit");
check(floatersSummary({ pieces: 3, triangles: 1234, largest_mm: 40.8, largest_rule: "largest_only" }, { keep_largest_only: true }) === "Kept the largest piece only: removed 3 other pieces, the largest 40.8 mm across (1,234 triangles)", "the largest only");
check(floatersSummary({ pieces: 2, triangles: 80, largest_mm: 9, largest_rule: "triangles" }, { min_triangles: 100 }) === "Removed 2 loose bits, the largest 9.0 mm across but under 100 triangles (80 triangles)", "triangles");

// progress lines: thousands grouped, no device
check(trackProgress(23, 1321) === "Following the camera: frame 23 of 1,321", trackProgress(23, 1321));
check(trackProgress(1321, 1321) === "Following the camera: all 1,321 frames done, making the mesh", trackProgress(1321, 1321));

// the print check's verdicts (ohs/server/setup.py, print_check): the engine's words, its numbers
check(printCheckVerdict(100.1, false, 20) === "Your print is accurate: the bar measures 100.10 mm (+0.10 %). The board is ready for calibration.", printCheckVerdict(100.1, false, 20));
check(printCheckVerdict(99.9, false, 20) === "Your print is accurate: the bar measures 99.90 mm (-0.10 %). The board is ready for calibration.", printCheckVerdict(99.9, false, 20));
check(printCheckVerdict(98.8, false, 20) === "Your printer scaled the page by -1.2 % (it printed at 98.8 %: the 100 mm bar measures 98.80 mm). Reprint it at 100 % / Actual size (turn off “Fit to page”), or use your measurement and the app calibrates with the measured size.", printCheckVerdict(98.8, false, 20));
check(printCheckVerdict(98.8, true, 20) === "Your printer scaled the page by -1.2 % (it printed at 98.8 %: the 100 mm bar measures 98.80 mm). Using your measurement: the app calibrates with squares of 19.76 mm. For the best result, reprint at 100 % / Actual size.", printCheckVerdict(98.8, true, 20));
check(printCheckVerdict(90, false, 20) === "The bar measures 90.00 mm, 10.0 % off: more than a printer scales a page. Measure again between the outer edges of the bar; if it really is that far off, reprint at 100 % / Actual size.", printCheckVerdict(90, false, 20));

// a failed stage says what failed in the engine's fixed words (ohs/server/jobs.py FAILURE_TEXT), never a code word
check(FAILURE_TEXT.process_track === "Following the camera path failed. Your recording is safe: try Follow the camera again, or Make mesh at Preview quality.", FAILURE_TEXT.process_track);
check(FAILURE_TEXT.process_scan === "Making the mesh failed. Your recording is safe: try Make mesh again, or at Preview quality.", FAILURE_TEXT.process_scan);
for (const t of Object.values(FAILURE_TEXT)) check(!/[a-z]+_[a-z]+/.test(t), `no code word: ${t}`);

console.log(`engineWords.test: ${checks} checks passed`);
