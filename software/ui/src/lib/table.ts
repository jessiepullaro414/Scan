/**
 * "Remove the table" on the Process page (FEATURE_PLAN X1): the words the page shows around the
 * engine's answer. The engine finds the surface the part stands on and the faces that are it
 * (ohs/mesh/support.py); the page first shows them highlighted with their count and area, then
 * removes them as an edit (undo brings them back).
 */
import type { TablePreview, TableRaised } from "../api/types";
import { decodeFaces } from "./fits.ts";
import { fmtInt } from "./format.ts";

/** The engine's default margin, mm (ohs.mesh.support.MARGIN_MM). */
export const TABLE_MARGIN_MM = 1;
export const TABLE_MARGIN_MAX_MM = 20;

/** The hover card: what it does, what it keeps, what it costs at the part's foot (measured on the simulated bracket). */
export const TABLE_HELP: { title: string; lines: { label: string; text: string }[] } = {
  title: "Remove the table",
  lines: [
    {
      label: "What it does",
      text: "Finds the flat surface the part stands on and removes it, with anything behind it. You see what goes before it goes, and Ctrl+Z brings it back.",
    },
    {
      label: "What it keeps",
      text: "Every face turned more than 45° from the table, at any height: the part's walls stay right down to the table. A thin part lying flat (a shim, a gasket, a sheet) stays when it stands at a steady height clearly above the table round it, flat and parallel to it, 20 mm across or more. Loose bits that are not the table stay for Remove floaters.",
    },
    {
      label: "What it costs",
      text: "Where the part meets the table the scan's surface is rounded: its lower half goes with the table (on the simulated bracket about 0.1 mm² per mm of the part's edge, all within 1 mm of the table). A thin part kept loses at most about 0.3 mm of its rim. A surface that meets the table at less than 45° (a low dome, a chamfer) loses its bottom margin.",
    },
    {
      label: "Its limit",
      text: "How thin a part it can tell from the table depends on how flat the table is: the preview says this scan's own limit. A raised area it is not sure of goes with the table, but is drawn in amber on the model and listed, with Keep this. Where the table itself rises in many places (often near the scan's edge), such areas are not listed one by one: there the preview says how thin a part cannot be told.",
    },
    {
      label: "When it says no",
      text: "With no table in the scan, or when the largest flat surface is the part's own face, it removes nothing and says why. Select the table by hand then.",
    },
  ],
};

export const TABLE_MARGIN_HELP =
  "How far above the table a face that faces the table's way still counts as the table. 1 mm keeps the most of the part on the simulated scans; more takes more of a sloped part's foot.";

/** The edit's label as the engine writes it in the history. */
export function tableEditLabel(faces: number): string {
  return `Remove the table (${fmtInt(faces)} faces)`;
}

/** 2404.3 cm² → "2,404 cm²", 3.2 → "3.2 cm²". */
export function fmtArea(mm2: number): string {
  const cm2 = mm2 / 100;
  return `${cm2 >= 100 ? fmtInt(Math.round(cm2)) : cm2 >= 10 ? cm2.toFixed(0) : cm2.toFixed(1)} cm²`;
}

/** What the preview found, in one or two short sentences for the panel. */
export function tableFacts(p: TablePreview): { head: string; detail: string } {
  const share = Math.round(p.share * 100);
  const kept =
    p.lowest_mm === null
      ? "Nothing of the scan would be left."
      : p.lowest_mm <= p.band_mm
        ? "The part is kept right down to the table."
        : `The part is kept from ${p.lowest_mm.toFixed(1)} mm above the table.`;
  const pct = p.triangles > 0 ? Math.round((p.n_faces / p.triangles) * 100) : 0;
  const thin = (p.raised ?? []).filter((r) => r.kept).length;
  const thinText = thin ? ` ${thin === 1 ? "A thin part lying flat stays" : `${thin} thin parts lying flat stay`}.` : "";
  return {
    head: `${fmtInt(p.n_faces)} faces (${fmtArea(p.area_mm2)}, ${pct} % of the mesh) are the table.`,
    detail: `The table holds ${share} % of the scan and is flat to ${p.flatness_mm.toFixed(2)} mm. ${kept}${thinText}`,
  };
}

/** A margin the engine takes (0 to 20 mm), or null. */
export function validMargin(v: number): number | null {
  return Number.isFinite(v) && v >= 0 && v <= TABLE_MARGIN_MAX_MM ? v : null;
}
/** A raised area in words: "30 mm across, 0.21 mm high, at (12, -7, 300) mm". */
export function areaText(r: TableRaised): string {
  return `${Math.round(r.size_mm)} mm across, ${r.height_mm.toFixed(2)} mm high, at (${r.centre.map((x) => Math.round(x)).join(", ")}) mm`;
}

/** A raised area the preview draws on the model: kept (a thin part, or kept by the user) or warned (goes unless kept). */
export type DrawnArea = { index: number; verdict: "kept" | "warned"; byUser: boolean; faces: Uint32Array; centre: [number, number, number]; radius: number };

/** The kept and warned areas with their faces (the table's own unevenness is said, not drawn). */
export function drawnAreas(p: TablePreview): DrawnArea[] {
  const out: DrawnArea[] = [];
  (p.raised ?? []).forEach((r, index) => {
    if (r.verdict === "uneven" || !r.faces) return;
    out.push({ index, verdict: r.verdict, byUser: !!r.by_user, faces: decodeFaces(r.faces), centre: r.centre, radius: Math.max(10, r.size_mm * 0.6) });
  });
  return out;
}

/** "Keep this": a point on the area (its centre, to 0.01 mm) added to the list; given again, taken out. */
export function toggleKeep(keep: number[][], r: TableRaised): number[][] {
  const q = r.centre.map((x) => Math.round(x * 100) / 100);
  const same = (a: number[]) => a.length === 3 && a.every((x, i) => Math.abs(x - q[i]) < 0.005);
  return keep.some(same) ? keep.filter((a) => !same(a)) : [...keep, q];
}
