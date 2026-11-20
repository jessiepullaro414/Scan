/**
 * Checks for the recipe chips and the hole sizes (UX_PLAN U8, U13, U32; `npm test`): a hole's size
 * is the length around it, with its diameter worked out from that length, never hard-coded.
 */
import type { Recipe } from "../api/types.ts";
import { historyLabel, holeSize, opChip, settingsChips } from "./recipes.ts";

let checks = 0;
function eq(a: unknown, b: unknown, what: string): void {
  checks++;
  if (a !== b) throw new Error(`check failed: ${what}\n  got:  ${JSON.stringify(a)}\n  want: ${JSON.stringify(b)}`);
}
const N = " ";

eq(holeSize(15), `15${N}mm around (Ø${N}5)`, "15 mm around is about 4.8 mm across: Ø 5");
eq(holeSize(5), `5${N}mm around (Ø${N}1.6)`, "5 mm around: Ø 1.6");
eq(holeSize(40), `40${N}mm around (Ø${N}13)`, "worked out from the value: 40 mm around, Ø 13");
eq(opChip({ op: "fill_holes", params: { max_hole_mm: 15 } }), `Holes up to 15${N}mm around (Ø${N}5)`, "the chip (U13)");
eq(opChip({ op: "fill_holes", params: {} }), "Fill holes", "no size");
eq(opChip({ op: "remove_floaters", params: { min_size_mm: 5 } }), `Loose bits under 5${N}mm`, "loose bits (U8)");
eq(opChip({ op: "remove_floaters", params: { keep_largest_only: true } }), "Largest piece only", "largest only");
eq(opChip({ op: "smooth", params: { method: "bilateral", strength: 0.5 } }), `Smooth, keep edges 50${N}%`, "smooth");
eq(opChip({ op: "decimate", params: { target_triangles: 200000 } }), "Simplify to 200,000 triangles", "simplify");

const r = (voxel: number | null) => ({ settings: { quality: "standard", straighten: "auto", refine: "auto", voxel_mm: voxel } }) as unknown as Recipe;
eq(settingsChips(r(1)).join(" | "), `Standard quality | Straighten | Laser refine | Mesh detail 1${N}mm`, "mesh detail, not voxels (U8)");
eq(settingsChips(r(0.5))[3], `Mesh detail 0.5${N}mm`, "half a millimetre");
eq(settingsChips(r(null))[3], "Mesh detail auto", "auto");

// the history says the same as the chip (the engine sends "Fill holes up to 15 mm")
eq(historyLabel("Fill holes up to 15 mm"), `Fill holes up to 15${N}mm around (Ø${N}5)`, "history, fill holes");
eq(historyLabel("Make watertight: close every opening up to 10 mm"), `Make watertight: close every opening up to 10${N}mm around (Ø${N}3)`, "history, watertight");
eq(historyLabel("Remove floaters smaller than 5 mm"), `Remove loose bits smaller than 5${N}mm`, "history, loose bits");
eq(historyLabel("Smooth (Taubin, 50 %)"), "Smooth (Taubin, 50 %)", "other labels as they are");

console.log(`recipes.test: ${checks} checks passed`);
