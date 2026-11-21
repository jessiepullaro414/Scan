/**
 * Checks for the project a scan goes into (UX_PLAN U44, `npm test`): "New scan" on Home no longer picks the first
 * project silently; it opens the Scan page with the project picker focused, "Choose the project for this scan".
 * Plain TypeScript run by Node, no framework.
 */
import { CHOOSE_PROJECT_TEXT, projectForScan } from "./scanProject.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`scanProject.test: ${what}`);
}

const list = [{ id: "a" }, { id: "b" }, { id: "c" }];
check(CHOOSE_PROJECT_TEXT === "Choose the project for this scan", "the picker's words");

// an ordinary visit (a project's own Scan button, the sidebar): as before
check(projectForScan({ list, current: "b", param: null, choose: false }) === "b", "the current project");
check(projectForScan({ list, current: "gone", param: null, choose: false }) === "a", "no valid current project: the first, as before (the sidebar's Scan)");
check(projectForScan({ list, current: "b", param: "c", choose: false }) === "c", "a project in the address wins");

// "New scan" on Home: the person chooses
check(projectForScan({ list, current: "gone", param: null, choose: true }) === null, "choose: no silent first project");
check(projectForScan({ list, current: null, param: null, choose: true }) === null, "choose, no current project: nothing picked");
check(projectForScan({ list, current: "b", param: null, choose: true }) === "b", "choose: the current project (named in the top bar) is offered, the picker focused");
check(projectForScan({ list: [{ id: "only" }], current: null, param: null, choose: true }) === "only", "choose, one project only: that one");
check(projectForScan({ list: [], current: null, param: null, choose: true }) === null, "no project yet: none");
check(projectForScan({ list: null, current: "b", param: null, choose: true }) === "b", "the list not loaded yet: the current one for now");

console.log(`scanProject.test: ${checks} checks passed`);
