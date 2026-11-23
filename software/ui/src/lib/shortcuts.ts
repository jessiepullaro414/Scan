/**
 * The keyboard shortcut sheets (UX_PLAN U25): one for each page with keys, opened with "?" (and a button on the
 * page), closed with Esc. Each line names its keys as they are pressed ("Ctrl+Z", "Esc Esc", "1–7") and, in
 * `binds`, the key names the page's `useHotkeys` map listens to (`lib/util.ts` keyName), so a test can hold the
 * sheet and the page together (`shortcuts.test.ts`). Keys held with Ctrl are the page's own listener: no binds.
 *
 * On the Edit mesh page O shows the hole outlines: H, P and C are the viewer's keys for fitting a hole, a plane
 * and a cylinder, and P and C there pick the brush and the connected piece.
 */
import { keyRanges } from "./format.ts";

export type SheetItem = { keys: string; text: string; binds: string[] };
export type SheetGroup = { title: string; items: SheetItem[] };
export type Sheet = { title: string; groups: SheetGroup[]; note?: string };
export type SheetId = "scan" | "viewer" | "process" | "capture" | "imu";

const range = (a: number, b: number) => Array.from({ length: b - a + 1 }, (_, i) => String(a + i));
const SHEET_KEY: SheetItem = { keys: "?", text: "Show this sheet", binds: ["?"] };

export const SHEETS: Record<SheetId, Sheet> = {
  scan: {
    title: "Keyboard shortcuts: scanning",
    groups: [
      {
        title: "Scan",
        items: [
          { keys: "Space", text: "Start the scan, pause, resume", binds: ["space"] },
          { keys: "R", text: "Rewind 2 s: cut the last two seconds from the live preview", binds: ["r"] },
          { keys: "Esc Esc", text: "Stop and save: press Esc twice within a second", binds: ["escape"] },
        ],
      },
      { title: "View", items: [{ keys: "F", text: "Frame the model", binds: ["f"] }, SHEET_KEY] },
    ],
    note: "The scanner's trigger: press to start, pause or resume; hold it for 1 s to stop.",
  },
  viewer: {
    title: "Keyboard shortcuts: the viewer",
    groups: [
      {
        title: "Tools",
        items: [
          { keys: "M", text: "Measure a distance between two points", binds: ["m"] },
          { keys: "G", text: "Fit a shape where you click", binds: ["g"] },
          { keys: "P", text: "Fit a plane", binds: ["p"] },
          { keys: "C", text: "Fit a cylinder", binds: ["c"] },
          { keys: "H", text: "Fit a hole", binds: ["h"] },
          { keys: "S", text: "Section: cut the scan with a plane", binds: ["s"] },
          { keys: "U", text: "Flush along a seam", binds: ["u"] },
          { keys: "Esc", text: "Drop a half-made measurement, then turn the tool off", binds: ["escape"] },
        ],
      },
      {
        title: "View",
        items: [
          { keys: "F", text: "Frame the model", binds: ["f"] },
          { keys: "W", text: "Wireframe", binds: ["w"] },
          { keys: "L", text: "Laser points", binds: ["l"] },
          { keys: "1–7", text: "Shading: grey, studio, direction, confidence, deviation, lasers, CAD model", binds: range(1, 7) },
          SHEET_KEY,
        ],
      },
    ],
  },
  process: {
    title: "Keyboard shortcuts: Edit mesh",
    groups: [
      {
        title: "Select",
        items: [
          { keys: "V", text: "Orbit the view (no selecting)", binds: ["v"] },
          { keys: "B", text: "Box select", binds: ["b"] },
          { keys: "L", text: "Lasso select", binds: ["l"] },
          { keys: "P", text: "Brush select", binds: ["p"] },
          { keys: "C", text: "Select a connected piece", binds: ["c"] },
          { keys: "[ ]", text: "Brush size", binds: ["[", "]"] },
          { keys: "Esc", text: "Back to orbiting", binds: ["escape"] },
          { keys: "Ctrl+A", text: "Select everything", binds: [] },
          { keys: "Ctrl+Shift+A", text: "Clear the selection", binds: [] },
          { keys: "Ctrl+I", text: "Invert the selection", binds: [] },
        ],
      },
      {
        title: "Edit",
        items: [
          { keys: "Delete", text: "Delete the selected faces (Backspace too)", binds: ["delete", "backspace"] },
          { keys: "Ctrl+Z", text: "Undo", binds: [] },
          { keys: "Ctrl+Y", text: "Redo (Ctrl+Shift+Z too)", binds: [] },
        ],
      },
      {
        title: "View",
        items: [
          { keys: "F", text: "Frame the model", binds: ["f"] },
          { keys: "W", text: "Wireframe", binds: ["w"] },
          { keys: "O", text: "Hole outlines", binds: ["o"] },
          { keys: "T", text: "Camera path", binds: ["t"] },
          { keys: "K", text: "Before and after the last change", binds: ["k"] },
          { keys: "1–6", text: "Shading: grey, studio, direction, confidence, deviation, lasers", binds: range(1, 6) },
          SHEET_KEY,
        ],
      },
    ],
  },
  capture: {
    title: "Keyboard shortcuts: calibration",
    groups: [
      {
        title: "While taking views",
        items: [
          { keys: "Space", text: "Take a view now", binds: ["space"] },
          { keys: "Backspace", text: "Delete the last view", binds: ["backspace"] },
          { keys: "Enter", text: "Finish and work out the calibration", binds: ["enter"] },
          { keys: "Esc", text: "Cancel the calibration (asks first)", binds: ["escape"] },
          SHEET_KEY,
        ],
      },
    ],
  },
  imu: {
    title: "Keyboard shortcuts: the motion sensor",
    groups: [
      {
        title: "While recording",
        items: [
          { keys: "Space", text: "Start recording, then stop", binds: ["space"] },
          { keys: "Enter", text: "Work out the calibration", binds: ["enter"] },
          { keys: "Esc", text: "Cancel the calibration (asks first)", binds: ["escape"] },
          SHEET_KEY,
        ],
      },
    ],
  },
};

/** The viewer's seven shadings, keys 1 to 7. */
const VIEWER_SHADINGS = ["grey", "studio", "direction", "confidence", "deviation", "lasers", "CAD model"];

/** Every key name a sheet binds. */
export function sheetKeys(id: SheetId): Set<string> {
  return new Set(SHEETS[id].groups.flatMap((g) => g.items.flatMap((i) => i.binds)));
}

/** The viewer's shading keys that work now, as ranges ("1–7"; "1–3, 6" for a mesh without confidence, edits or a CAD model). */
export function viewerShading(on: { sigma: boolean; deviation: boolean; reference: boolean }): { keys: string; numbers: number[] } {
  const numbers = [1, 2, 3, ...(on.sigma ? [4] : []), ...(on.deviation ? [5] : []), 6, ...(on.reference ? [7] : [])];
  return { keys: keyRanges(numbers).join(", "), numbers };
}

/** A sheet with the viewer's shading line as it is now (the numbers that work). */
export function viewerSheet(on: { sigma: boolean; deviation: boolean; reference: boolean }): Sheet {
  const { keys, numbers } = viewerShading(on);
  const text = `Shading: ${numbers.map((n) => VIEWER_SHADINGS[n - 1]).join(", ")}`;
  return {
    ...SHEETS.viewer,
    groups: SHEETS.viewer.groups.map((g) => ({ ...g, items: g.items.map((i) => (i.keys === "1–7" ? { ...i, keys, text } : i)) })),
    note: keys === "1–7" ? undefined : "The shading keys missing here wait for what they show: confidence data, an edit, a CAD model aligned with the scan.",
  };
}
