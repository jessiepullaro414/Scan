/**
 * Checks for `sameSession` (`npm test`). Plain TypeScript that Node runs as it is (type
 * stripping), no test framework: a failed check throws.
 */
import type { SessionState } from "../api/types.ts";
import { sameSession } from "./sameSession.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`sameSession.test: ${what}`);
}

const idle: SessionState = { state: "idle", project_id: null, scan_id: null, mode: "markerless", elapsed_s: 0, frames: 0 };
const scan: SessionState = { state: "scanning", project_id: "p1", scan_id: "s1", mode: "markerless", elapsed_s: 1.5, frames: 45 };

check(sameSession(idle, idle), "the same object");
check(sameSession(idle, { ...idle }), "an equal copy (what every idle status message brings)");
check(sameSession(scan, { ...scan }), "an equal copy while scanning");
check(sameSession(null, null) && sameSession(undefined, null), "nothing on both sides");
check(!sameSession(null, idle) && !sameSession(idle, null), "nothing on one side");

// every field counts
check(!sameSession(scan, { ...scan, state: "paused" }), "state");
check(!sameSession(scan, { ...scan, project_id: "p2" }), "project_id");
check(!sameSession(scan, { ...scan, scan_id: "s2" }), "scan_id");
check(!sameSession(scan, { ...scan, mode: "markers" }), "mode");
check(!sameSession(scan, { ...scan, elapsed_s: 1.6 }), "elapsed_s");
check(!sameSession(scan, { ...scan, frames: 46 }), "frames");
check(!sameSession(idle, { ...idle, project_id: "p1" }), "null against a value");

// a field this UI does not know yet (a newer engine) still counts
const extra = { ...idle, note: "a" } as SessionState;
check(!sameSession(idle, extra) && !sameSession(extra, idle), "an extra field on one side");
check(sameSession(extra, { ...extra }), "the same extra field on both sides");
check(!sameSession(extra, { ...idle, note: "b" } as SessionState), "an extra field that differs");
check(!sameSession(extra, { ...idle, other: "a" } as SessionState), "a different extra field of the same count");

console.log(`sameSession.test: ${checks} checks passed`);
