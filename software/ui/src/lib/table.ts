/**
 * "Remove the table" on the Process page (FEATURE_PLAN X1): the words the page shows around the
 * engine's answer. The engine finds the surface the part stands on and the faces that are it
 * (ohs/mesh/support.py); the page first shows them highlighted with their count and area, then
 * removes them as an edit (undo brings them back).
 */
import type { TablePreview } from "../api/types";
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
      text: "Every face turned more than 45° from the table, at any height: the part's walls stay right down to the table. A thin part lying flat (a shim, a gasket, a sheet) stays when it stands at a steady height clearly above the table's own unevenness: on the simulated scans from 0.5 mm thick. Loose bits that are not the table stay for Remove floaters.",
    },
    {
      label: "What it costs",
      text: "Where the part meets the table the scan's surface is rounded: its lower half goes with the table (on the simulated bracket about 0.1 mm² per mm of the part's edge, all within 1 mm of the table). A thin part kept loses at most about 0.3 mm of its rim (on the real-size test, under 0.1 mm). A surface that meets the table at less than 45° (a low dome, a chamfer) loses its bottom margin.",
    },
    {
      label: "Its limit",
      text: "A part lying flat that is thinner than about 0.5 mm cannot always be told from the table, whose own unevenness rises 0.2-0.3 mm in patches. From about 0.2 mm it is found and goes with the table, and the preview says where it is: select it by hand to keep it. Thinner than about 0.2 mm it cannot be told from the table at all.",
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