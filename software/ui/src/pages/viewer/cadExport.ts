/**
 * The words of the Download panel's "For CAD" group (FEATURE_PLAN.md C2; APP_API.md "For CAD"): why it is
 * disabled, the line that says what will be exported and in which frame, what the "real outline" switch costs, and
 * the download names. Pure functions on the server's `CadSummary` (cadExport.test.ts).
 */
import type { CadFile, CadSummary } from "../../api/types";
import { fmtBytes, fmtInt } from "../../lib/format.ts";

/** What the outline switch does, for its hover card. */
export const CAD_OUTLINE_HELP =
  "Off: each plane is its measured rectangle, four clean edges. On: each plane follows its real outline, its largest outer loop and the real holes in it, simplified to about 0.5 mm. Small holes are left out as pits of the scan, and a plane whose outline can't make a valid face stays a rectangle (the notes say so).";

const plural = (n: number, one: string) => `${fmtInt(n)} ${one}${n === 1 ? "" : "s"}`;

/** Why the CAD files can't be downloaded, or null when they can. */
export function cadUnavailable(nFits: number, hasMesh: boolean): string | null {
  if (!hasMesh) return "Make the mesh first, then fit and keep planes, cylinders or holes.";
  if (nFits === 0) return "Keep a fit first: click a plane, cylinder or hole with the fit tool (G).";
  return null;
}

/** "2 planes, 1 cylinder and 1 hole": the kinds there are, in a sentence. */
export function cadCounts(c: CadSummary["counts"]): string {
  const parts = [
    c.plane ? plural(c.plane, "plane") : "",
    c.cylinder ? plural(c.cylinder, "cylinder") : "",
    c.hole ? plural(c.hole, "hole") : "",
    c.point ? plural(c.point, "point") : "",
  ].filter(Boolean);
  return parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : (parts[0] ?? "nothing");
}

/** "4 fits (2 planes, 1 cylinder and 1 hole), in the part's frame.", or the reason when nothing is exported. */
export function cadLine(s: CadSummary): string {
  if (s.reason) {
    const r = s.reason.trim().replace(/\.$/, "");
    return `${r[0].toUpperCase()}${r.slice(1)}.`;
  }
  return `${plural(s.n_fits, "fit")} (${cadCounts(s.counts)}), in ${s.frame_label}.`;
}

/** What the real outline costs, in a few lines for the switch's hover card: both files' sizes either way, the
 * points the outlines hold, and the holes kept and left out. Empty while the outline's summary is not known. */
export function outlineCost(rect: CadSummary | null, outline: CadSummary | null): string[] {
  if (!outline?.outline) return [];
  const o = outline.outline;
  if (o.planes === 0) return ["No plane has an outline kept: each stays its rectangle. Fit a plane again to measure its outline."];
  const lines: string[] = [];
  if (rect?.step_bytes != null && outline.step_bytes != null)
    lines.push(`STEP ${fmtBytes(rect.step_bytes)} with rectangles, ${fmtBytes(outline.step_bytes)} with the outline; the macro ${fmtBytes(rect.macro_bytes)} and ${fmtBytes(outline.macro_bytes)}.`);
  lines.push(`${plural(o.points, "point")} in the outline${o.planes === 1 ? " of 1 plane" : `s of ${fmtInt(o.planes)} planes`}.`);
  lines.push(`${plural(o.holes_kept, "hole")} kept, ${fmtInt(o.holes_left_out)} left out (pits of the scan, and holes that touch the edge or each other).`);
  return lines;
}

/** What the measured-only switch does, for its hover card. */
export const CAD_MEASURED_HELP =
  "Off: the approved nominal sizes (a cylinder's or a hole's diameter) are written in place of the measured ones, and the notes list each one with its measured value. On: the measured values only, as if nothing were approved. Approved relations (parallel, an angle, a distance) are only listed in the notes either way: no shape is turned to suit another.";

/** "2 approved sizes are used in place of the measured ones; 1 approved relation is listed in the notes, not applied.",
 * or null when nothing is approved (or the server says nothing of it). */
export function cadNominalLine(n: CadSummary["nominal"]): string | null {
  if (!n) return null;
  if (n.measured_only) return "Measured values only: the approved nominal values are left out.";
  if (!n.sizes_used && !n.relations_listed) return null;
  const parts: string[] = [];
  if (n.sizes_used) parts.push(`${plural(n.sizes_used, "approved size")} ${n.sizes_used === 1 ? "is" : "are"} used in place of the measured ${n.sizes_used === 1 ? "one" : "ones"}`);
  if (n.relations_listed) parts.push(`${plural(n.relations_listed, "approved relation")} ${n.relations_listed === 1 ? "is" : "are"} listed in the notes, not applied to the shapes`);
  const s = parts.join("; ");
  return `${s[0].toUpperCase()}${s.slice(1)}.`;
}

/** The name a CAD file is saved under: after the scan, so the STEP file and the macro of two scans stay apart. */
export function cadDownloadName(stem: string, file: CadFile): string {
  return `${stem || "scan"}-${file}`;
}
