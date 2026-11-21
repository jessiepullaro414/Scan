/**
 * Checks for an open calibration session against scanning (UX_PLAN U1, `npm test`): the Scan
 * page's sentence and button, the sidebar's label, and when leaving closes a result.
 */
import type { CalibSession } from "../api/types.ts";
import { calibOpen, closesOnLeave, scanReason, setupLabel } from "./calibOpen.ts";
import type { Route } from "./router.tsx";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`check failed: ${what}`);
}

const session = (state: CalibSession["state"], kind: CalibSession["kind"], canSave: boolean | null): CalibSession =>
  ({ state, kind, result: canSave === null ? null : { can_save: canSave } }) as unknown as CalibSession;

check(calibOpen(null) === null, "no session: nothing open");
check(calibOpen(session("idle", null, null)) === null, "idle: nothing open");
check(calibOpen(session("saved", "stereo", true)) === null, "saved: nothing open");

const check2 = calibOpen(session("result", "check", false));
check(check2?.reason === "A calibration result is still open in Setup.", "the Scan page's sentence for an open result");
check(check2?.label === "Close it" && check2.action === "close", "a check result is closed with Close it");
check(check2?.sidebar === "Setup: a calibration result is still open", "the sidebar says the same");
const stereo = calibOpen(session("result", "stereo", true));
check(stereo?.label === "Review it" && stereo.action === "review" && stereo.step === "stereo", "a result that can be saved: Review it");
const capturing = calibOpen(session("capturing", "lasers", null));
check(capturing?.reason === "A calibration is in progress in Setup." && capturing.label === "Review it", "capturing: in progress, Review it");
check(capturing?.sidebar === "Setup: a calibration is in progress", "capturing: the sidebar's label");

const onCheck: Route = { name: "setup", step: "check" };
const scan: Route = { name: "scan", project: null, first: false, continues: null, choose: false };
const home: Route = { name: "home" };
check(closesOnLeave(session("result", "check", false), onCheck, scan), "leaving a check result by the sidebar closes it");
check(closesOnLeave(session("result", "check", false), onCheck, { name: "setup", step: "done" }), "leaving by the step rail closes it");
check(!closesOnLeave(session("result", "check", false), onCheck, onCheck), "staying on it does not");
check(!closesOnLeave(session("result", "stereo", true), { name: "setup", step: "stereo" }, home), "a result that can be saved stays");
check(!closesOnLeave(session("result", "check", false), home, scan), "not opened from its step: left alone");
check(!closesOnLeave(session("capturing", "check", null), onCheck, scan), "views being captured are not closed");
check(closesOnLeave(session("result", "stereo", false), { name: "setup", step: "stereo" }, home), "a failed solve is closed too");

// UX_PLAN R9: the Scan page tests an open session before "no calibration". With the simulator an open session makes
// `calibration.loaded` false; U1's sentence and its button must still show
const ready = { deviceKnown: true, connected: true, calibrated: true, open: null, projectChosen: true, passReason: null };
const simOpen = scanReason({ ...ready, calibrated: false, open: stereo });
check(simOpen.reason === "A calibration result is still open in Setup." && simOpen.openAction, `the simulator's case: ${JSON.stringify(simOpen)}`);
check(scanReason({ ...ready, calibrated: false, open: capturing }).reason === "A calibration is in progress in Setup.", "views being taken, no calibration loaded");
check(scanReason({ ...ready, calibrated: false }).reason === "No calibration yet: calibrate the scanner in Setup", "nothing open, no calibration");
check(scanReason({ ...ready, connected: false, open: stereo }).reason === "Connect a device to start", "not connected comes first");
check(scanReason(ready).reason === null && !scanReason(ready).openAction, "ready");
check(scanReason({ ...ready, projectChosen: false }).reason === "Choose a project to start", "no project");
check(scanReason({ ...ready, passReason: "Answer the question" }).reason === "Answer the question", "a pass's reason last");
// the sidebar says the same
check(setupLabel({ calibrating: stereo!.sidebar, stale: null, drift: false, calibMissing: true }) === "Setup: a calibration result is still open", "the sidebar: the open session first");
check(setupLabel({ calibrating: null, stale: null, drift: false, calibMissing: true }) === "Setup: no calibration yet", "the sidebar: no calibration");
check(setupLabel({ calibrating: null, stale: "Check recommended", drift: false, calibMissing: false }) === "Setup: check recommended", "the sidebar: the status");
check(setupLabel({ calibrating: null, stale: null, drift: false, calibMissing: false }) === "Setup", "the sidebar: nothing to say");

console.log(`calibOpen.test: ${checks} checks passed`);
