/**
 * The thin-parts hint (ENGINE_PLAN X5 step 1, APP_API.md "The thin-parts hint"): after Make mesh the engine looks for
 * thin walls, a thin plate lying flat and small holes, and suggests the preset “Thin sheet metal and small parts” only
 * when it finds thin walls (the one case where a gain was measured; a plate or holes alone: `why_not`
 * "no_measured_gain", no hint). A suggestion only: the page shows it with one action (set that preset, then Make mesh again, the
 * way the Preset box does it) and a way to put it away. Nothing happens by itself.
 */
import type { MadeInfo, ThinPartsHint } from "../api/types";

export const THIN_PRESET_ID = "thin-sheet-small-parts";

/** The hint to show, or null: nothing found, made fine already, put away, or an engine without it. */
export function thinPartsToShow(made: MadeInfo | null | undefined): ThinPartsHint | null {
  const h = made?.thin_parts;
  if (!h || !h.suggest || h.dismissed || !h.words) return null;
  return h;
}

/** The demo's hint, in the engine's words (`ohs/mesh/thin_parts.py hint_words`), for a scan of thin walls. */
export function demoThinPartsHint(seconds: number, triangles: number, voxel: number, dismissed: boolean): ThinPartsHint {
  const t = (s: number) => (s < 90 ? `${s.toFixed(0)} s` : `${(s / 60).toFixed(1)} min`);
  return {
    suggest: true,
    why_not: null,
    found: ["walls"],
    voxel_mm: voxel,
    preset_id: THIN_PRESET_ID,
    dismissed,
    words: {
      title: "Thin walls found",
      intro: `The preset “Thin sheet metal and small parts” makes the mesh at mesh detail 0.5 mm instead of ${voxel} mm, and changes nothing else.`,
      found: [
        "Thin walls: 1,794 mm² of the surface (0.5 %) lies on walls seen from both sides that read 1.84 mm thick (most of it 1.33 to 2.54 mm), probably 0.54 to 1.74 mm in truth.",
      ],
      gain: [
        "Walls seen from both sides read 0.23 to 0.67 mm nearer their true thickness at mesh detail 0.5 mm: simulated walls 0.5 to 2 mm thick read 1 to 2.4 mm instead of 1.7 to 2.7 mm.",
      ],
      cost: [
        `Time: 1.2 to 2.4 times as long. This mesh took ${t(seconds)}; with the preset about ${t(1.2 * seconds)} to ${t(2.4 * seconds)}.`,
        `Triangles: about 4 times as many, about ${((4 * triangles) / 1e6).toFixed(1)} million instead of ${(triangles / 1e6).toFixed(2)} million.`,
        "Memory: scans made at mesh detail 0.5 mm with 3.34 to 6.4 million triangles took 7 to 11.9 GB of memory and 2.2 to 3 GB of the graphics card's shared memory.",
      ],
      measured: "Measured on simulated scans, on a mini PC with built-in graphics and 20 GB of memory.",
      action: "Make the mesh again with this preset",
      preset_id: THIN_PRESET_ID,
      preset_name: "Thin sheet metal and small parts",
    },
  };
}
