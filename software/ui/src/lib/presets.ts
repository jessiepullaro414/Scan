/**
 * Presets by object type (FEATURE_PLAN P2/P3; APP_API.md "Presets"), the pure part: the values a preset
 * names in words, where each value of a scan comes from (the preset, General, or changed by hand), and what
 * the Scan page sends with Start.
 *
 * Imports nothing at run time, so `npm test` checks it with plain node.
 */
import type { Preset, PresetKey, PresetValues, ScanPreset } from "../api/types";

export const GENERAL_ID = "general";
export const THIN_ID = "thin-sheet-small-parts";
/** The order the app shows the values in. */
export const PRESET_KEYS: PresetKey[] = ["mode", "quality", "voxel_mm", "thin_walls", "recipe", "check_tolerance_mm"];
/** The values that act at Make mesh (the mode is chosen before scanning, the tolerance in the Check panel). */
export const MESH_KEYS: PresetKey[] = ["quality", "voxel_mm", "thin_walls", "recipe"];
export const PRESET_LABELS: Record<PresetKey, string> = {
  mode: "Tracking mode",
  quality: "Quality",
  voxel_mm: "Mesh detail",
  thin_walls: "Thin walls and edges",
  recipe: "Recipe after meshing",
  check_tolerance_mm: "Check tolerance",
};
/** The mesh detail the app takes, mm (as the Fuse stage does). */
export const VOXEL_MIN_MM = 0.25;
export const VOXEL_MAX_MM = 10;

const WORDS: Record<string, string> = {
  markerless: "Markerless",
  markers: "Markers",
  standard: "Standard",
  best: "Best",
  preview: "Preview",
  auto: "Auto",
  on: "On",
  off: "Off",
};

/** "0.5" for 0.5, "1" for 1, "0.75" for 0.75. */
export function mm(v: number): string {
  return String(Math.round(v * 1000) / 1000);
}

/** A value in the app's words: "0.5 mm", "Standard", "Auto"; a recipe by its name when `recipeName` knows it. */
export function valueText(key: PresetKey, value: unknown, recipeName?: (id: string) => string | undefined): string {
  if (value === null || value === undefined) return "–";
  if ((key === "voxel_mm" || key === "check_tolerance_mm") && typeof value === "number") return `${key === "check_tolerance_mm" ? "± " : ""}${mm(value)} mm`;
  if (key === "recipe" && typeof value === "string") return recipeName?.(value) ?? value;
  return WORDS[String(value)] ?? String(value);
}

/** What a preset names, in one line: "Mesh detail 0.5 mm", or "Every setting as it is" for General. */
export function presetSummary(p: Pick<Preset, "values">, recipeName?: (id: string) => string | undefined): string {
  const named = PRESET_KEYS.filter((k) => p.values[k] !== undefined && p.values[k] !== null);
  if (!named.length) return "Every setting as it is";
  return named.map((k) => `${PRESET_LABELS[k]} ${valueText(k, p.values[k], recipeName)}`).join(", ");
}

/** The hand changes that differ from what the preset names (a value equal to the preset's is no change). */
export function cleanHand(named: PresetValues, hand: PresetValues): PresetValues {
  const out: Record<string, unknown> = {};
  for (const k of PRESET_KEYS) {
    const v = hand[k];
    if (v !== undefined && v !== null && v !== named[k]) out[k] = v;
  }
  return out as PresetValues;
}

/** What Start sends (APP_API.md `POST /api/scan/start`): the preset and the values changed by hand. */
export function startFields(presetId: string, named: PresetValues, hand: PresetValues): { preset: string; preset_values?: PresetValues } {
  const h = cleanHand(named, hand);
  delete h.mode; // the mode is the start's own `mode`
  return Object.keys(h).length ? { preset: presetId, preset_values: h } : { preset: presetId };
}

/** Where a scan's value comes from: "changed" by hand (from the preset's or General's), "preset", or "general". */
export type ValueOrigin = "changed" | "preset" | "general";
export function valueOrigin(v: Pick<ScanPreset, "changed" | "preset_values">, key: PresetKey): ValueOrigin {
  if (v.changed.includes(key)) return "changed";
  if (v.preset_values[key] !== undefined && v.preset_values[key] !== null) return "preset";
  return "general";
}

/** The value a scan uses for `key`: its own (preset or hand), else General's. */
export function effectiveValue<K extends PresetKey>(v: Pick<ScanPreset, "values" | "general">, key: K): PresetValues[K] {
  const own = v.values[key];
  return (own === null || own === undefined ? v.general[key] : own) as PresetValues[K];
}

/** "Changed by hand from 0.5 mm" / "Changed by hand (General: 1 mm)" / "From the preset" / "General". */
export function originText(v: Pick<ScanPreset, "changed" | "preset_values" | "general">, key: PresetKey, recipeName?: (id: string) => string | undefined): string {
  const o = valueOrigin(v, key);
  if (o === "changed") {
    const named = v.preset_values[key];
    return named !== undefined && named !== null
      ? `Changed by hand from ${valueText(key, named, recipeName)}`
      : `Changed by hand (General: ${valueText(key, v.general[key], recipeName)})`;
  }
  return o === "preset" ? "From the preset" : "As in General";
}
