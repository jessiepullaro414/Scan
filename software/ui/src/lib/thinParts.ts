/**
 * The thin-parts hint (ENGINE_PLAN X5 step 1, APP_API.md "The thin-parts hint"): after Make mesh the engine looks for
 * thin walls, a thin plate lying flat and small holes, and suggests the preset “Thin sheet metal and small parts” only
 * when it finds thin walls (the one case where a gain was measured; a plate or holes alone: `why_not`
 * "no_measured_gain", no hint). A suggestion only: the page shows it with one action (set that preset, then Make mesh again, the
 * way the Preset box does it) and a way to put it away. Nothing happens by itself.
 *
 * ENGINE_PLAN X5 step 2: where the engine can run it (`words.fine`) the hint's first choice is "Make only the thin walls
 * finer" (Make mesh again with the scan's choice on); after such a mesh, `fineWallsNote` is the line in the hint's place.
 */
import type { FineWallsOffer, MadeInfo, ThinPartsHint } from "../api/types";

export const THIN_PRESET_ID = "thin-sheet-small-parts";
export const FINE_ACTION = "Make only the thin walls finer";

/** One choice of the hint: "Make only the thin walls finer" (`fine`, ENGINE_PLAN X5 step 2: Make mesh again with the
 * scan's choice on) or the preset (`preset`, as before). The first is the primary button. */
export type HintChoice = { kind: "fine" | "preset"; label: string; primary: boolean };

/** The hint's choices in order: the finer walls first where the engine offers them (`words.fine`: the graphics card's
 * path, mesh detail 1 mm), then the preset; without the offer the preset alone, as before. */
export function hintChoices(h: ThinPartsHint): HintChoice[] {
  const w = h.words;
  if (!w) return [];
  const preset: HintChoice = { kind: "preset", label: w.action, primary: !w.fine };
  return w.fine ? [{ kind: "fine", label: w.fine.action, primary: true }, preset] : [preset];
}

/** After a Make mesh with "Make only the thin walls finer" on (`made.fine_walls`): the note the page shows in the
 * hint's place, the engine's line and, only when it acted, its one action ("Make the mesh again without this":
 * `POST .../process {fine_walls: false}`). Null when the scan did not choose it, or there is no line. */
export function fineWallsNote(made: MadeInfo | null | undefined): { acted: boolean; line: string; action: string | null } | null {
  const f = made?.fine_walls;
  if (!f || !f.line) return null;
  return { acted: !!f.acted, line: f.line, action: f.acted ? (f.action ?? "Make the mesh again without this") : null };
}

/** The hint to show, or null: nothing found, made fine already, put away, or an engine without it. */
export function thinPartsToShow(made: MadeInfo | null | undefined): ThinPartsHint | null {
  const h = made?.thin_parts;
  if (!h || !h.suggest || h.dismissed || !h.words) return null;
  return h;
}

/** The demo's "Make only the thin walls finer", in the engine's words (`ohs/mesh/thin_parts.py fine_words`). */
function demoFine(seconds: number, frames: number): FineWallsOffer {
  const t = (s: number) => (s < 90 ? `${s.toFixed(0)} s` : `${(s / 60).toFixed(1)} min`);
  const mb = (frames * 8_192_000) / 2 ** 20;
  return {
    action: FINE_ACTION,
    intro: "Makes the mesh again at mesh detail 1 mm, then makes only the thin walls finer, at 0.5 mm, from the same frames. Nothing else changes.",
    gain: [
      "Walls of 0.5 to 2 mm read 0.04 to 0.52 mm nearer their true thickness than in this mesh, about half to four fifths of what the whole scan at mesh detail 0.5 mm gains; the rest of the mesh stays as it is.",
      "Each wall's less-seen side keeps its surface as it is, so the walls still read thicker than with the preset.",
    ],
    cost: [
      `Time: about 1.2 to 1.3 times as long. This mesh took ${t(seconds)}; with it about ${t(1.2 * seconds)} to ${t(1.3 * seconds)}.`,
      "Memory: no more memory than this mesh took. Triangles: 7 to 11 % more, on the walls only.",
      `Disk: about ${mb >= 100 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(0)} MB`} of free space while the mesh is made (7.8 MB for each of the ${frames.toLocaleString("en-US")} frames), removed when it is done.`,
    ],
    disk_mb: Math.round(mb * 10) / 10,
  };
}

/** The demo's hint, in the engine's words (`ohs/mesh/thin_parts.py hint_words`), for a scan of thin walls. `frames`:
 * the frames this mesh processed; with it, at mesh detail 1 mm, "Make only the thin walls finer" is offered first. */
export function demoThinPartsHint(seconds: number, triangles: number, voxel: number, dismissed: boolean, frames?: number): ThinPartsHint {
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
      fine: frames && voxel === 1 ? demoFine(seconds, frames) : null,
    },
  };
}
