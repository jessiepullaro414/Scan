/**
 * Checks for the Edit mesh stage cards' states (UX_PLAN R22, `npm test`): one stage "Running" at a time, and no
 * "Closed 48 loops" under "Skipped". Plain TypeScript run by Node, no framework.
 */
import { stageRunning, straightenSkippedLine } from "./stages.ts";

const fs = (await import(["node", "fs"].join(":"))) as { readFileSync: (u: URL, enc: "utf8") => string };

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`stages.test: ${what}`);
}

// a run of Make mesh again: the current mesh's stages are done; the run being built says which one runs
check(!stageRunning("done", { state: "done" }), "Follow the camera, done in the run too: not running");
check(stageRunning("done", { state: "running" }), "Straighten, done on the current mesh, running in the run being built");
check(!stageRunning("done", null) && !stageRunning("done"), "a finished stage of the current mesh, no run: not running");
check(stageRunning("running", null), "a stage run on its own: running");
check(!stageRunning("none", { state: "none" }), "a stage the run has not reached: not running");
const run = { track: "done", straighten: "running", fuse: "none", refine: "none" } as const;
check(Object.values(run).filter((m) => stageRunning("done", { state: m })).length === 1, "one stage running at a time");

// Straighten skipped with loops: not "Closed 48 loops"
check(straightenSkippedLine({ state: "skipped", loops: 48, summary: null }) === "Found 48 loops, and they show the passes already line up: the scan was left as it is.", "skipped with loops, said");
check(straightenSkippedLine({ state: "skipped", loops: 1200, summary: null })!.startsWith("Found 1,200 loops"), "thousands grouped");
check(straightenSkippedLine({ state: "skipped", loops: 48, summary: "Straightening could not finish, so the scan was left as it is." }) === null, "the engine's own sentence says why");
check(straightenSkippedLine({ state: "done", loops: 48, summary: null }) === null, "done: the card's own line");

// the page uses them: a stage's job alone does not make it "Running", and "Closed … loops" only for a done stage
const panel = fs.readFileSync(new URL("../pages/process/StagesPanel.tsx", import.meta.url), "utf8");
check(/stageRunning\(/.test(panel) && !/const running = stage\.state === "running" \|\| !!job/.test(panel), "StagesPanel reads stageRunning");
check(/straightenSkippedLine\(/.test(panel), "StagesPanel says a skipped Straighten's loops in its own words");

console.log(`stages.test: ${checks} checks passed`);
