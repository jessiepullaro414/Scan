/**
 * The words of the Download panel's "For CAD" group (FEATURE_PLAN.md C2; APP_API.md "For CAD"): why it is
 * disabled, the line that says what will be exported and in which frame, what the "real outline" switch costs, and
 * the download names. Pure functions on the server's `CadSummary` (cadExport.test.ts).
 */
import type { CadFile, CadSummary } from "../../api/types";
import { fmtBytes, fmtInt } from "../../lib/format.ts";

/** What the outline switch does, for its hover card. */
export const CAD_OUTLINE_HELP =
  "Off: each plane is its measured rectangle, four clean edges. On: each plane follows its real outline, its largest outer loop and the real holes in it, simplified to about 0.5\u202fmm. Small holes are left out as pits of the scan, and a plane whose outline can't make a valid face stays a rectangle (the notes say so).";

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
  return `${plural(s.n_fits, "fitted shape")} (${cadCounts(s.counts)}), along ${s.frame_label}.`;
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
  "Off: the approved nominal sizes (a cylinder's or a hole's diameter) are written in place of the measured ones, and the notes list each one with its measured value. An approved relation is listed in the notes, or, where you chose it in the relation's card, one of its two shapes is turned and slid to suit it. On: the measured values only, as if nothing were approved: no shape moves.";

/** "2 approved sizes are used in place of the measured ones; 1 approved relation is listed in the notes, not applied.",
 * or null when nothing is approved (or the server says nothing of it). */
export function cadNominalLine(n: CadSummary["nominal"]): string | null {
  if (!n) return null;
  if (n.measured_only) return "Measured values only: the approved nominal values are left out.";
  const applied = n.relations_applied ?? 0;
  if (!n.sizes_used && !n.relations_listed && !applied) return null;
  const parts: string[] = [];
  if (n.sizes_used) parts.push(`${plural(n.sizes_used, "approved size")} ${n.sizes_used === 1 ? "is" : "are"} used in place of the measured ${n.sizes_used === 1 ? "one" : "ones"}`);
  if (applied) parts.push(`${plural(applied, "approved relation value")} ${applied === 1 ? "is" : "are"} applied by moving a shape`);
  if (n.relations_listed) parts.push(`${plural(n.relations_listed, "approved relation")} ${n.relations_listed === 1 ? "is" : "are"} listed in the notes, not applied to the shapes`);
  const s = parts.join("; ");
  return `${s[0].toUpperCase()}${s.slice(1)}.`;
}

/** One line per relation with a move chosen: how far its shape was turned and slid, or why it was not moved. */
export function cadMoveLines(n: CadSummary["nominal"]): string[] {
  return (n?.moves ?? []).map((m) =>
    m.applied
      ? `${m.name}: ${m.moved} turned ${m.turn_deg.toFixed(2)}° about its own point and slid ${m.slide_mm.toFixed(3)}\u202fmm, to suit ${m.to_suit}.`
      : `${m.name}: not applied (${m.reason ?? "no reason given"}), listed only.`,
  );
}

/** The name a CAD file is saved under: after the scan, so the STEP file and the macro of two scans stay apart. */
export function cadDownloadName(stem: string, file: CadFile): string {
  return `${stem || "scan"}-${file}`;
}
