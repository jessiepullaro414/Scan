/**
 * The demo's presets (APP_API.md "Presets"; FEATURE_PLAN P2/P3): the two built-ins as the engine has them,
 * the user's own in memory, each scan's preset (`GET` / `PUT .../preset`) and what Start records. The values
 * are checked as the engine checks them, in the same words.
 */
import type { Preset, PresetKey, PresetValues, Scan, ScanMode, ScanPreset, ScanPresetKept } from "../types";
import { GENERAL_ID, PRESET_KEYS, PRESET_LABELS, THIN_ID } from "../../lib/presets";
import { HttpError, json } from "./host";

const BUILTINS: Preset[] = [
  {
    id: GENERAL_ID,
    name: "General",
    builtin: true,
    description: "What the app does for any part: 1 mm mesh detail, the Default recipe, every setting as it is.",
    card: {
      changes: "Nothing. Every setting stays as it is in the app.",
      why: "Measured on seven simulated parts, the settings as they are give the best result on panels, brackets, castings and large parts. No other setting beat the scan-to-scan spread.",
      costs: null,
    },
    values: {},
  },
  {
    id: THIN_ID,
    name: "Thin sheet metal and small parts",
    builtin: true,
    description: "A finer mesh (0.5 mm instead of 1 mm), so thin walls read closer to their true thickness.",
    card: {
      changes: "A finer mesh: 0.5 mm voxels instead of 1 mm. Nothing else.",
      why: "Thin standing walls read closer to their true thickness. On all three test scans, walls 0.5, 1 and 2 mm thick read 1.03 to 1.08, 1.32 to 1.45 and 2.18 to 2.43 mm, instead of 1.70, 1.71 to 1.90 and 2.53 to 2.66 mm. A detailed casting gains a little too.",
      costs: "Making the mesh takes about 1.2 to 2.4 times as long and needs more graphics memory. It gains nothing on a large panel or a door.",
    },
    values: { voxel_mm: 0.5 },
  },
];

const RANGES: Partial<Record<PresetKey, [number, number, boolean]>> = { voxel_mm: [0.25, 10, false], check_tolerance_mm: [0, 100, true] };
const CHOICES: Partial<Record<PresetKey, string[]>> = {
  mode: ["markerless", "markers"],
  quality: ["standard", "best", "preview"],
  thin_walls: ["auto", "on", "off"],
};
const GENERAL: Required<PresetValues> = { mode: "markerless", quality: "standard", voxel_mm: 1, thin_walls: "auto", recipe: "default", check_tolerance_mm: 0.3 };

function bad(detail: string, hint: string): never {
  throw new HttpError(422, detail, hint);
}

/** The values, checked as the engine does (`ohs.app.presets.validate_values`); null / undefined dropped. */
export function checkValues(values: unknown, recipeExists?: (id: string) => boolean): PresetValues {
  if (values === undefined || values === null) return {};
  if (typeof values !== "object" || Array.isArray(values)) bad("The page asked for something the app does not know.", "Reload the page and try again.");
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(values as Record<string, unknown>)) {
    if (k === "depth_range" || k === "min_depth_mm" || k === "max_depth_mm") bad(`A preset cannot set ${k}: the depth range is not a setting of the app`, `A preset can set ${PRESET_KEYS.join(", ")}.`);
    if (!PRESET_KEYS.includes(k as PresetKey)) bad(`A preset has no value called “${k}”`, `A preset can set ${PRESET_KEYS.join(", ")}.`);
    if (v === null || v === undefined) continue;
    const key = k as PresetKey;
    const label = PRESET_LABELS[key].toLowerCase();
    const rng = RANGES[key];
    const choices = CHOICES[key];
    if (rng) {
      if (typeof v !== "number" || !Number.isFinite(v)) bad(`The ${label} must be a number of millimetres`, `Send a number for ${key}.`);
      const [lo, hi, open] = rng;
      if ((open ? v <= lo : v < lo) || v > hi) bad(`The ${label} must be ${open ? "above" : "at least"} ${lo} mm and at most ${hi} mm (got ${v} mm)`, `Choose a ${label} in that range.`);
      out[key] = v;
    } else if (choices) {
      const s = key === "quality" && v === "full" ? "standard" : v;
      if (typeof s !== "string" || !choices.includes(s)) bad(`The ${label} must be ${choices.slice(0, -1).join(", ")} or ${choices[choices.length - 1]}`, `Use ${choices.join(", ")}.`);
      out[key] = s;
    } else if (key === "recipe") {
      if (typeof v !== "string" || (recipeExists && !recipeExists(v))) bad(`There is no recipe “${String(v)}”`, "Pick a recipe from the Recipes panel.");
      out[key] = v;
    }
  }
  return out as PresetValues;
}

const changedKeys = (named: PresetValues, hand: PresetValues): PresetKey[] => PRESET_KEYS.filter((k) => hand[k] !== undefined && hand[k] !== named[k]);

export class MockPresets {
  private user: Preset[] = [];
  private recipeExists: (id: string) => boolean;
  constructor(recipeExists: (id: string) => boolean = () => true) {
    this.recipeExists = recipeExists;
  }

  list(): Preset[] {
    return [...BUILTINS.map((p) => ({ ...p, values: { ...p.values } })), ...[...this.user].sort((a, b) => a.name.localeCompare(b.name))];
  }

  find(id: string): Preset | null {
    return this.list().find((p) => p.id === id) ?? null;
  }

  /** `/api/presets[/{id}]`. */
  route(method: string, seg: string[], body: Record<string, unknown>): Response | undefined {
    const [a, b] = seg;
    if (a !== "presets") return undefined;
    if (method === "GET" && seg.length === 1) return json(200, this.list());
    if (method === "POST" && seg.length === 1) {
      const name = String(body.name ?? "").trim().replace(/\s+/g, " ");
      if (!name) throw new HttpError(400, "The preset's name is empty", "Type a name for the preset.");
      if (BUILTINS.some((p) => p.name.toLowerCase() === name.toLowerCase())) throw new HttpError(409, `“${name}” is the name of a built-in preset`, "Choose another name.");
      const values = checkValues(body.values, this.recipeExists);
      const id = `${name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "preset"}-${Math.random().toString(16).slice(2, 6)}`;
      const p: Preset = { id, name, builtin: false, description: String(body.description ?? ""), card: null, values, created: new Date().toISOString() };
      this.user.push(p);
      return json(200, p);
    }
    if (method === "DELETE" && seg.length === 2) {
      const p = this.find(b);
      if (!p) throw new HttpError(404, `There is no preset “${b}”`, "Read the list again.");
      if (p.builtin) throw new HttpError(409, `“${p.name}” is a built-in preset and cannot be deleted`, "Only presets you saved can be deleted.");
      this.user = this.user.filter((x) => x.id !== b);
      return json(204, null);
    }
    return undefined;
  }

  /** What Start records on the new scan (404 for an unknown preset, 422 for a value the app does not take). */
  forStart(body: Record<string, unknown>, mode: ScanMode): ScanPresetKept {
    const id = typeof body.preset === "string" && body.preset ? body.preset : GENERAL_ID;
    const p = this.find(id);
    if (!p) throw new HttpError(404, `There is no preset “${id}”`, "Pick a preset from the list.");
    const hand = checkValues(body.preset_values, this.recipeExists);
    delete hand.mode;
    if (p.values.mode && p.values.mode !== mode) hand.mode = mode;
    for (const k of PRESET_KEYS) if (hand[k] === p.values[k]) delete hand[k];
    return { id: p.id, name: p.name, hand };
  }

  /** The values that act on the scan's processing (the mode is the scan's own). */
  acting(s: Scan): PresetValues {
    const kept = s.preset;
    const p = kept ? this.find(kept.id) : null;
    const out = { ...(p?.values ?? {}), ...(kept?.hand ?? {}) };
    delete out.mode;
    return out;
  }

  view(s: Scan): ScanPreset {
    const kept = s.preset;
    const p = kept ? this.find(kept.id) : this.find(GENERAL_ID);
    const named = p?.values ?? {};
    const hand = kept?.hand ?? {};
    const act = { ...named, ...hand } as PresetValues;
    const values = Object.fromEntries(PRESET_KEYS.map((k) => [k, act[k] ?? null])) as ScanPreset["values"];
    if (!act.mode) values.mode = s.mode;
    const missing = !!kept && !p;
    return {
      id: kept?.id ?? GENERAL_ID,
      name: kept?.name ?? "General",
      preset_values: { ...named },
      hand: { ...hand },
      changed: changedKeys(named, hand),
      values,
      note: !kept
        ? "This scan was made before presets, so it is made as General."
        : missing
          ? "This scan's preset is no longer in the app, so the scan is made as General."
          : null,
      before_presets: !kept,
      missing,
      general: { ...GENERAL, mode: s.mode },
      labels: { ...PRESET_LABELS },
      keys: [...PRESET_KEYS],
    };
  }

  /** `GET` / `PUT .../preset`. */
  routeScan(method: string, s: Scan, body: Record<string, unknown>): Response | undefined {
    if (method === "GET") return json(200, this.view(s));
    if (method !== "PUT") return undefined;
    if (s.status === "recording" || s.status === "processing") throw new HttpError(409, "This scan is busy", "Wait until it finishes.");
    const values = (body.values ?? null) as Record<string, unknown> | null;
    if (values && "mode" in values) bad("The tracking mode cannot change after scanning", "Start a new scan with the other tracking mode.");
    const checked = checkValues(values ?? {}, this.recipeExists);
    let kept: ScanPresetKept = s.preset ? { ...s.preset, hand: { ...s.preset.hand } } : { id: GENERAL_ID, name: "General", hand: {} };
    if (typeof body.preset_id === "string" && body.preset_id !== kept.id) {
      const p = this.find(body.preset_id);
      if (!p) throw new HttpError(404, `There is no preset “${body.preset_id}”`, "Pick a preset from the list.");
      kept = { id: p.id, name: p.name, hand: {} };
    }
    const named = this.find(kept.id)?.values ?? {};
    const hand: Record<string, unknown> = { ...kept.hand };
    for (const k of Object.keys(values ?? {}) as PresetKey[]) {
      if (checked[k] !== undefined && checked[k] !== named[k]) hand[k] = checked[k];
      else delete hand[k];
    }
    s.preset = { ...kept, hand: hand as PresetValues };
    return json(200, this.view(s));
  }
}
