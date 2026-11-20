/**
 * Checks for the presets' pure part (`npm test`): values in words, where a scan's value comes from, what
 * Start sends. Plain TypeScript that Node runs as it is (type stripping), no test framework.
 */
import type { ScanPreset } from "../api/types";
import { cleanHand, effectiveValue, mm, originText, presetSummary, startFields, THIN_ID, valueOrigin, valueText } from "./presets.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`presets.test: ${what}`);
}
function eq<T>(a: T, b: T, what: string): void {
  check(JSON.stringify(a) === JSON.stringify(b), `${what}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`);
}

eq(mm(0.5), "0.5", "half a mm");
eq(mm(1), "1", "a whole mm");
eq(valueText("voxel_mm", 0.5), "0.5 mm", "the voxel in mm");
eq(valueText("check_tolerance_mm", 0.3), "± 0.3 mm", "the tolerance with ±");
eq(valueText("quality", "best"), "Best", "a quality in words");
eq(valueText("recipe", "bracket", (id) => (id === "bracket" ? "Bracket / mechanical" : undefined)), "Bracket / mechanical", "a recipe by its name");
eq(valueText("voxel_mm", null), "–", "no value");

eq(presetSummary({ values: {} }), "Every setting as it is", "General names nothing");
eq(presetSummary({ values: { voxel_mm: 0.5 } }), "Mesh detail 0.5 mm", "the thin preset");

eq(cleanHand({ voxel_mm: 0.5 }, { voxel_mm: 0.5 }), {}, "the preset's own value is no change");
eq(cleanHand({ voxel_mm: 0.5 }, { voxel_mm: 0.75, thin_walls: "on" }), { voxel_mm: 0.75, thin_walls: "on" }, "changes are kept");
eq(startFields(THIN_ID, { voxel_mm: 0.5 }, {}), { preset: THIN_ID }, "Start with the preset as it is");
eq(startFields(THIN_ID, { voxel_mm: 0.5 }, { voxel_mm: 1, mode: "markers" }), { preset: THIN_ID, preset_values: { voxel_mm: 1 } }, "the mode goes as the start's own");

const view = {
  changed: ["voxel_mm"],
  preset_values: { voxel_mm: 0.5 },
  values: { mode: "markerless", quality: null, voxel_mm: 0.75, thin_walls: null, recipe: null, check_tolerance_mm: null },
  general: { mode: "markerless", quality: "standard", voxel_mm: 1, thin_walls: "auto", recipe: "default", check_tolerance_mm: 0.3 },
} as unknown as ScanPreset;
eq(valueOrigin(view, "voxel_mm"), "changed", "a hand change");
eq(valueOrigin(view, "quality"), "general", "a value the preset does not name");
eq(originText(view, "voxel_mm"), "Changed by hand from 0.5 mm", "changed from the preset's value");
eq(originText(view, "thin_walls"), "As in General", "General's");
eq(effectiveValue(view, "voxel_mm"), 0.75, "the hand value acts");
eq(effectiveValue(view, "quality"), "standard", "General's quality");
const general = { ...view, changed: ["voxel_mm"], preset_values: {} } as unknown as ScanPreset;
eq(originText(general, "voxel_mm"), "Changed by hand (General: 1 mm)", "changed from General's");
const thin = { ...view, changed: [], values: { ...view.values, voxel_mm: 0.5 } } as unknown as ScanPreset;
eq(originText(thin, "voxel_mm"), "From the preset", "the preset's value");

console.log(`presets.test: ${checks} checks passed`);
