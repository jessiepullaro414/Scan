/**
 * Checks for an open calibration session against scanning (UX_PLAN U1, `npm test`): the Scan
 * page's sentence and button, the sidebar's label, and when leaving closes a result.
 */
import type { CalibSession } from "../api/types.ts";
import { calibOpen, closesOnLeave } from "./calibOpen.ts";
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
const scan: Route = { name: "scan", project: null, first: false, continues: null };
const home: Route = { name: "home" };
check(closesOnLeave(session("result", "check", false), onCheck, scan), "leaving a check result by the sidebar closes it");
check(closesOnLeave(session("result", "check", false), onCheck, { name: "setup", step: "done" }), "leaving by the step rail closes it");
check(!closesOnLeave(session("result", "check", false), onCheck, onCheck), "staying on it does not");
check(!closesOnLeave(session("result", "stereo", true), { name: "setup", step: "stereo" }, home), "a result that can be saved stays");
check(!closesOnLeave(session("result", "check", false), home, scan), "not opened from its step: left alone");
check(!closesOnLeave(session("capturing", "check", null), onCheck, scan), "views being captured are not closed");
check(closesOnLeave(session("result", "stereo", false), { name: "setup", step: "stereo" }, home), "a failed solve is closed too");

console.log(`calibOpen.test: ${checks} checks passed`);
