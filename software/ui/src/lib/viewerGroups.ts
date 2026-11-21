/**
 * The viewer's side panel (UX_PLAN U11): four groups, each folding, the first open. (1) "Is it good?": the Scan
 * check and the processing report with the calibration status; (2) "Measure": measurements, fitted shapes, the
 * part's axes (offered only once a plane is saved, as "Set the part's axes…"), sections and flush, each with what
 * was saved inside it; (3) "Compare with CAD"; (4) "Export": the downloads, For CAD, the report, Edit mesh and
 * Make mesh again. The scan's size stays above the groups, always in view.
 *
 * Pure: the page (ViewerPage.tsx) renders these; `viewerGroups.test.ts` checks them.
 */

export type ViewerGroupId = "good" | "measure" | "cad" | "export";

export type ViewerGroup = { id: ViewerGroupId; title: string; sections: string[] };

/** The groups in the order of the panel, with the sections each holds (their titles on the page). */
export const VIEWER_GROUPS: readonly ViewerGroup[] = [
  { id: "good", title: "Is it good?", sections: ["Scan check", "Processing report"] },
  { id: "measure", title: "Measure", sections: ["Measurements", "Fitted shapes", "The part's axes", "Sections", "Flush along a seam"] },
  { id: "cad", title: "Compare with CAD", sections: ["Compare with the CAD model"] },
  { id: "export", title: "Export", sections: ["Download", "For CAD", "Report", "Edit mesh", "Make mesh again"] },
];

export type GroupsOpen = Record<ViewerGroupId, boolean>;

/** When the viewer opens: the first group open, the others folded. */
export function initialOpen(): GroupsOpen {
  return { good: true, measure: false, cad: false, export: false };
}

/** What can be turned on from the toolbar or a key: the four tools, the CAD model's shading, picking matching points. */
export type ViewerTool = "measure" | "fit" | "section" | "flush" | "reference" | "pairs";

/** The group whose panel belongs to a tool (opened when the tool is turned on, so its panel is in view). */
export function groupOfTool(t: ViewerTool): ViewerGroupId {
  return t === "reference" || t === "pairs" ? "cad" : "measure";
}

/** The groups open with the tool's own group opened too (the same object when it already was). */
export function openFor(open: GroupsOpen, t: ViewerTool): GroupsOpen {
  const g = groupOfTool(t);
  return open[g] ? open : { ...open, [g]: true };
}

/**
 * How the part's axes show in Measure: not at all before a plane is saved (the primary needs one), then as the
 * button "Set the part's axes…", and as their panel once asked for, or when the scan has axes already.
 */
export function axesEntry(a: { planes: number; hasFrame: boolean; asked: boolean }): "hidden" | "offer" | "panel" {
  if (a.hasFrame) return "panel";
  if (a.planes < 1) return "hidden";
  return a.asked ? "panel" : "offer";
}
