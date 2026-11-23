/**
 * Checks for the Edit mesh stage cards' states (UX_PLAN R22, `npm test`): one stage "Running" at a time, and no
 * "Closed 48 loops" under "Skipped". Plain TypeScript run by Node, no framework.
 */
import { STAGE_TITLES, jobKindLabel, stageRunning, straightenSkippedLine } from "./stages.ts";

const fs = (await import(["node", "fs"].join(":"))) as { readFileSync: (u: URL, enc: "utf8") => string };

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`stages.test: ${what}`);
}

// a stage card of the scan's own mesh says "Running" from its own state only (a stage run alone); during "Make mesh
// again" the scan's stages stay as they are (done) and the run being built is shown beside them, from `making`
// (lib/making.ts `makingRows`), so a finished stage is never "Running" because the run is at it (UX_PLAN R15, R22)
check(!stageRunning("done"), "a finished stage of the current mesh: not running, whatever the run does");
check(stageRunning("running"), "a stage run on its own: running");
check(!stageRunning("none") && !stageRunning("skipped") && !stageRunning("failed"), "the other states");

// Straighten skipped with loops: not "Closed 48 loops"
check(straightenSkippedLine({ state: "skipped", loops: 48, summary: null }) === "Found 48 loops, and they show the passes already line up: the scan was left as it is.", "skipped with loops, said");
check(straightenSkippedLine({ state: "skipped", loops: 1200, summary: null })!.startsWith("Found 1,200 loops"), "thousands grouped");
check(straightenSkippedLine({ state: "skipped", loops: 48, summary: "Straightening could not finish, so the scan was left as it is." }) === null, "the engine's own sentence says why");
check(straightenSkippedLine({ state: "done", loops: 48, summary: null }) === null, "done: the card's own line");

// the page uses them: a stage's job alone does not make it "Running", and "Closed … loops" only for a done stage
const panel = fs.readFileSync(new URL("../pages/process/StagesPanel.tsx", import.meta.url), "utf8");
check(/stageRunning\(stage\.state\)/.test(panel) && !/const running = stage\.state === "running" \|\| !!job/.test(panel), "StagesPanel reads stageRunning on the card's own state");
check(/makingRows\(/.test(panel) && /The new mesh being made/.test(panel), "StagesPanel shows the run being built beside the stages");
check(/straightenSkippedLine\(/.test(panel), "StagesPanel says a skipped Straighten's loops in its own words");

// the Jobs panel names a stage's job as its card does: "Build the surface", never the old "Fuse"
for (const id of ["track", "straighten", "fuse", "spacetime", "refine"] as const)
  check(jobKindLabel(`process_${id}`) === STAGE_TITLES[id].label, `process_${id}: ${jobKindLabel(`process_${id}`)}`);
check(jobKindLabel("process_scan") === "Make mesh" && jobKindLabel("mesh_edit") === "Mesh edit", "a whole run and an edit");

// the Jobs panel: the time left wraps, and a row's button that opens a page closes the panel
const jobsMenu = fs.readFileSync(new URL("../components/shell/JobsMenu.tsx", import.meta.url), "utf8");
check(!/est\?\.kind === "left" \? <div className="[^"]*truncate/.test(jobsMenu), "the time left is never cut off");
check(/onDone=\{\(\) => setOpen\(false\)\}/.test(jobsMenu), "the panel closes when a row's button opens a page");

console.log(`stages.test: ${checks} checks passed`);
