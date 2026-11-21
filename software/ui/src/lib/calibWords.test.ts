/**
 * Checks for the calibration result's words (UX_PLAN U10, U41, U48; `npm test`): no accuracy is
 * promised from a grade, the badge does not repeat the headline, "Nothing to do" is said once.
 */
import { CALIB_VERDICT, CHECK_HEADLINE_FAILED, CHECK_STATE, emptyAdviceText, resultBadge } from "./calibWords.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`check failed: ${what}`);
}

// U10: the exact new sentences (the server is changed to the same ones)
check(CALIB_VERDICT.stereoExcellent === "This calibration is as good as the board allows.", "stereo, excellent");
check(CALIB_VERDICT.stereoGood === "Good: no step needs redoing.", "stereo, good");
check(CALIB_VERDICT.lasersGood === "Both laser lines are calibrated well.", "lasers, two lines");
check(CALIB_VERDICT.lasersGoodAny(4) === "All 4 laser lines are calibrated well.", "lasers, more lines");
check(CALIB_VERDICT.imuGood === "The motion sensor keeps tracking steady on fast sweeps and plain surfaces.", "IMU (U40)");
check(CALIB_VERDICT.laserStage === "The lines are measured on their own, so they correct the surface.", "the Process page's laser stage");
for (const s of [CALIB_VERDICT.stereoExcellent, CALIB_VERDICT.stereoGood, CALIB_VERDICT.lasersGood, CALIB_VERDICT.imuGood, CALIB_VERDICT.laserStage])
  check(!/accura/i.test(s), `no accuracy promised: ${s}`);

// U41: the badge is not said twice
check(resultBadge("excellent", "stereo", true, "Excellent stereo calibration") === null, "headline starts with the grade: no badge");
check(resultBadge("good", "lasers", true, "Lasers calibrated") === "Good", "otherwise the grade's badge");
// U48: the check's state is "Calibration valid"
check(resultBadge("excellent", "check", true, "Your calibration has been checked") === "Calibration valid", "check, ok: Calibration valid");
check(resultBadge("excellent", "check", true, "Calibration valid") === null, "check whose headline says it already: no badge");
check(resultBadge("poor", "check", false, "Your calibration has drifted") === "Drifted" || resultBadge("poor", "check", false, "Your calibration has drifted") === null, "check, drifted");
check(resultBadge("poor", "check", false, "Something moved") === "Drifted", "check, drifted: its badge");

// U41: "Nothing to do: scan away." once
check(emptyAdviceText("check", false, "Left and right rows match. Nothing to do: scan away.") === null, "the verdict says it: no second time");
check(emptyAdviceText("check", false, "Left and right rows match.") === "Nothing to do: scan away.", "said once when the verdict does not");
check(emptyAdviceText("stereo", false, "x") === "Nothing to improve. Save it.", "a result to save");
check(emptyAdviceText("stereo", true, "x") === "Nothing to improve.", "a saved result");

// U31: the demo says what the server says. The server's verdicts (ohs/server/calib_results.py, read as text, not
// run) hold the same sentences for the cameras and the lasers.
{
  const fs = (await import(["node", "fs"].join(":"))) as { readFileSync: (u: URL, enc: "utf8") => string };
  const py = fs.readFileSync(new URL("../../../ohs/server/calib_results.py", import.meta.url), "utf8");
  check(py.includes(CALIB_VERDICT.stereoExcellent), `the server says it too: ${CALIB_VERDICT.stereoExcellent}`);
  check(py.includes(CALIB_VERDICT.stereoGood), `the server says it too: ${CALIB_VERDICT.stereoGood}`);
  check(py.includes("laser lines are calibrated well."), "the server says it too: ... laser lines are calibrated well.");
  // U31's remainder: the check's two headlines and the motion sensor's sentence (the demo's checkResult and imuResult
  // use these constants)
  check(py.includes(`headline = "${CHECK_STATE.ok}"`), `the server's check headline: ${CHECK_STATE.ok}`);
  check(py.includes(`headline = "${CHECK_HEADLINE_FAILED}"`), `the server's failed check headline: ${CHECK_HEADLINE_FAILED}`);
  check(py.includes(`"${CALIB_VERDICT.imuGood}"`), `the server says it too: ${CALIB_VERDICT.imuGood}`);
  // and the demo uses them, not words of its own
  const demo = fs.readFileSync(new URL("../api/mock/calib.ts", import.meta.url), "utf8");
  check(demo.includes("headline: ok ? CHECK_STATE.ok : CHECK_HEADLINE_FAILED"), "the demo's check headline is the server's");
  check(demo.includes("${CALIB_VERDICT.imuGood}"), "the demo's motion sensor sentence is the server's");
}

console.log(`calibWords.test: ${checks} checks passed`);
