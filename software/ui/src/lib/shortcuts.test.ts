/**
 * Checks for the shortcut sheets (UX_PLAN U25, `npm test`): one sheet for each page with keys, opened with "?";
 * every key a page listens to is on its sheet and nothing else is; no key twice on a sheet; on the Edit mesh page
 * O shows the hole outlines (H, P and C are the viewer's fit keys); Esc stops a scan only when pressed twice (U45).
 * The pages' keys are read from their sources (the `useHotkeys` maps). Plain TypeScript run by Node, no framework.
 */
import { SHEETS, sheetKeys, viewerSheet, viewerShading, type SheetId } from "./shortcuts.ts";

const fs = (await import(["node", "fs"].join(":"))) as { readFileSync: (u: URL, enc: "utf8") => string };

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`shortcuts.test: ${what}`);
}

/** The keys of every `useHotkeys({ … })` map in a source (the `keyName` values: "f", "escape", "1", "?", "["…). */
function hotkeysOf(source: string): string[] {
  const out: string[] = [];
  for (const m of source.matchAll(/useHotkeys\(\s*\{([\s\S]*?)\n\s*\}\s*(?:,[^)]*)?\);/g)) {
    for (const k of m[1].matchAll(/^\s{4,6}(?:"([^"]+)"|([a-z0-9]+)):\s*(?:\(|[a-z])/gm)) out.push(k[1] ?? k[2]);
  }
  return out;
}
const src = new URL("../", import.meta.url);
const PAGES: Record<SheetId, string> = {
  scan: "pages/ScanPage.tsx",
  viewer: "pages/ViewerPage.tsx",
  process: "pages/ProcessPage.tsx",
  capture: "pages/setup/CaptureStep.tsx",
  imu: "pages/setup/ImuScreen.tsx",
};
for (const [id, file] of Object.entries(PAGES) as [SheetId, string][]) {
  const page = new Set(hotkeysOf(fs.readFileSync(new URL(file, src), "utf8")));
  const sheet = sheetKeys(id);
  check(page.size > 2, `${file}: its keys are found (${[...page].join(" ")})`);
  for (const k of page) check(sheet.has(k), `${file} listens to “${k}”, which its sheet does not list`);
  for (const k of sheet) check(page.has(k) || k === "ctrl", `the ${id} sheet lists “${k}”, which ${file} does not listen to`);
  check(page.has("?"), `${file}: “?” opens the sheet`);
  const binds = SHEETS[id].groups.flatMap((g) => g.items.flatMap((i) => i.binds));
  check(binds.length === new Set(binds).size, `the ${id} sheet: no key twice (${binds.join(" ")})`);
}

// ---------------------------------------------------------------- the words
const all = Object.values(SHEETS).flatMap((s) => s.groups.flatMap((g) => g.items));
check(all.every((i) => /^[A-Z0-9]/.test(i.text) && !/\s[-–—]\s/.test(i.text)), "each line a capital first, no dash as punctuation");
const process = SHEETS.process.groups.flatMap((g) => g.items);
const o = process.find((i) => i.binds.includes("o"));
check(!!o && /hole outlines/i.test(o.text), "Edit mesh: O shows the hole outlines");
check(!process.some((i) => i.binds.includes("h")), "Edit mesh: H is not a key there any more (it fits a hole in the viewer)");
const esc = SHEETS.scan.groups.flatMap((g) => g.items).find((i) => i.binds.includes("escape"));
check(!!esc && esc.keys === "Esc Esc" && /twice/.test(esc.text), "the Scan sheet: Esc twice stops the scan");
check(SHEETS.viewer.title === "Keyboard shortcuts: the viewer" && SHEETS.process.title === "Keyboard shortcuts: Edit mesh", "the sheets' titles");

// ---------------------------------------------------------------- the viewer's shading keys follow what works now (U25's ranges)
check(viewerShading({ sigma: true, deviation: true, reference: true }).keys === "1–7", "every shading on: 1–7");
check(viewerShading({ sigma: false, deviation: false, reference: false }).keys === "1–3, 6", `only the plain ones: ${viewerShading({ sigma: false, deviation: false, reference: false }).keys}`);
check(viewerShading({ sigma: true, deviation: false, reference: true }).keys === "1–4, 6, 7", `no edits yet: ${viewerShading({ sigma: true, deviation: false, reference: true }).keys}`);

const shadingLine = (on: { sigma: boolean; deviation: boolean; reference: boolean }) =>
  viewerSheet(on).groups.flatMap((g) => g.items).find((i) => i.binds.includes("1"))!;
check(shadingLine({ sigma: true, deviation: true, reference: true }).text === "Shading: grey, studio, direction, confidence, deviation, lasers, CAD model", "all seven named");
const few = shadingLine({ sigma: false, deviation: false, reference: false });
check(few.keys === "1–3, 6" && few.text === "Shading: grey, studio, direction, lasers", `the line names only the shadings its keys reach: ${few.keys} ${few.text}`);

// the trigger (ohs/server/studio.py _handle_trigger): a short press starts, pauses or resumes; a hold of 1 s stops.
// The sheet and the Scan page's tip say the same sentence.
const TRIGGER = "The scanner's trigger: press to start, pause or resume; hold it for 1 s to stop.";
check(SHEETS.scan.note === TRIGGER, `the trigger note: ${SHEETS.scan.note}`);
const side = fs.readFileSync(new URL("../pages/scan/SidePanel.tsx", import.meta.url), "utf8");
check(side.includes("Trigger: press to start, pause or resume; hold it for 1&#8239;s to stop."), "the Scan page's tip says the same");

console.log(`shortcuts.test: ${checks} checks passed`);
