/**
 * The words of a recipe's steps (Process page, Recipes panel) and the edit history.
 *
 * UX_PLAN U13: a hole's size is the length around it (that is what `max_hole_mm` means): 15 mm
 * around is a round hole about 4.8 mm across, so the chip says "Holes up to 15 mm around (Ø 5)",
 * the diameter worked out from the value. U8: "Loose bits" (not floaters), "Mesh detail" (not voxels).
 */
import type { Recipe, RecipeOp } from "../api/types";
import { fmtInt, fmtNum, NNBSP, withUnit } from "./format.ts";
import { QUALITY_TEXT, normalizeQuality } from "./quality.ts";

const n = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** A length in mm with up to one decimal ("5", "0.5", "15"). */
const mmText = (v: number) => fmtNum(v, Number.isInteger(v) ? 0 : v < 10 ? 1 : 0);

/** The diameter of a round hole whose edge is `perimeter` long, as the user reads it: one decimal under 3 mm. */
function diameterText(perimeter: number): string {
  const d = perimeter / Math.PI;
  return d < 3 ? fmtNum(Math.round(d * 10) / 10, 1) : fmtNum(Math.round(d));
}

/** "15 mm around (Ø 5)": a hole size from the length around it. */
export function holeSize(perimeterMm: number): string {
  return `${withUnit(mmText(perimeterMm), "mm")} around (Ø${NNBSP}${diameterText(perimeterMm)})`;
}

/** One short chip per recipe step: "Loose bits under 5 mm", "Holes up to 15 mm around (Ø 5)", "Smooth 50 %". */
export function opChip(o: RecipeOp): string {
  const p = o.params ?? {};
  switch (o.op) {
    case "remove_floaters":
      if (p.keep_largest_only) return "Largest piece only";
      if (n(p.min_size_mm) !== null) return `Loose bits under ${withUnit(mmText(n(p.min_size_mm)!), "mm")}`;
      if (n(p.min_triangles) !== null) return `Loose bits under ${fmtInt(n(p.min_triangles))} triangles`;
      return "Remove loose bits";
    case "fill_holes":
      return n(p.max_hole_mm) !== null ? `Holes up to ${holeSize(n(p.max_hole_mm)!)}` : "Fill holes";
    case "smooth":
      return `${p.method === "bilateral" ? "Smooth, keep edges" : "Smooth"}${n(p.strength) !== null ? ` ${withUnit(String(Math.round(n(p.strength)! * 100)), "%")}` : ""}`;
    case "decimate":
      if (n(p.ratio) !== null) return `Simplify ${withUnit(String(Math.round(n(p.ratio)! * 100)), "%")}`;
      if (n(p.target_triangles) !== null) return `Simplify to ${fmtInt(n(p.target_triangles))} triangles`;
      return "Simplify";
    case "remove_table":
      return n(p.margin_mm) !== null ? `No table (${withUnit(mmText(n(p.margin_mm)!), "mm")} margin)` : "No table";
    default:
      return String(o.op);
  }
}

/** The chips of a recipe's stage settings. */
export function settingsChips(r: Pick<Recipe, "settings">): string[] {
  const s = r.settings;
  return [
    QUALITY_TEXT[normalizeQuality(s.quality) ?? "standard"].label,
    s.straighten === "off" ? "No straighten" : "Straighten",
    s.refine === "off" ? "No laser refine" : "Laser refine",
    s.voxel_mm !== null ? `Mesh detail ${withUnit(mmText(s.voxel_mm), "mm")}` : "Mesh detail auto",
  ];
}

/**
 * An edit's label in the history, from the engine's words: a hole size gets "around (Ø …)" as on
 * the chips, "floaters" become loose bits. Anything else stays as the engine wrote it.
 */
export function historyLabel(label: string): string {
  const hole = /^(Fill holes up to|Make watertight: close every opening up to) ([\d.]+) ?mm$/.exec(label.trim());
  if (hole) return `${hole[1]} ${holeSize(Number(hole[2]))}`;
  const bits = /^Remove floaters (smaller than|under) ([\d.]+) ?mm$/.exec(label.trim());
  if (bits) return `Remove loose bits ${bits[1]} ${withUnit(bits[2], "mm")}`;
  return label.replace(/\bfloaters\b/g, "loose bits").replace(/\bFloaters\b/g, "Loose bits");
}
