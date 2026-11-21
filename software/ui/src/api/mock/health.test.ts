/**
 * The demo's calibration status says what the engine says (UX_PLAN U51, U31; `npm test`): every fixed piece of its
 * sentences, its state and part labels and its step words are found in the engine's `ohs/calib/health.py`, read as
 * text (not run). Python's implicit joins of string literals over lines are undone first, so a sentence split over
 * two lines there is found whole.
 */
import { HEALTH_WORDS, LABEL, mockScanStatus, mockSetupHealth, PART_LABEL, SEEDED_SCAN_HEALTH, STEPS } from "./health.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`health.test (mock): ${what}`);
}

const fs = (await import(["node", "fs"].join(":"))) as { readFileSync: (u: URL, enc: "utf8") => string };
const raw = fs.readFileSync(new URL("../../../../ohs/calib/health.py", import.meta.url), "utf8");
// "...a " \n  f"b..." -> "...a b..."
const py = raw.replace(/"\s*\r?\n\s*f?"/g, "");

for (const [k, w] of Object.entries(HEALTH_WORDS)) check(py.includes(w), `the engine says it too (${k}): ${w}`);
for (const w of Object.values(LABEL)) check(py.includes(`"${w}"`), `the engine's state label: ${w}`);
for (const w of Object.values(PART_LABEL)) check(py.includes(`"${w}"`), `the engine's part label: ${w}`);
for (const w of Object.values(STEPS)) check(py.includes(`"${w}"`), `the engine's step words: ${w}`);
// the one name for the cameras' number, and why the scans' limit is lower (U4)
check(py.includes("left/right height mismatch") && !/apart in height|disagreed[^"]*in height/.test(py), "one name for the number (U4)");

// the demo's sentences are made of those pieces, for each status the URL can ask for
const when = new Date(Date.now() - 3_600_000).toISOString();
const sentences: string[] = [];
for (const option of ["check", "recalibrate", "cpu", "old", null] as const) {
  const h = mockSetupHealth({ stereo: true, option: option as never, launch: { state: "drift", dv_px: 1.38, matches: 287, message: null, when }, lastCheck: { when, ok: false, verdict: "Something moved." } });
  check(!!h, `a status for ${option}`);
  sentences.push(h!.reason, h!.label, ...(h!.note ? [h!.note] : []), ...h!.reasons.map((r) => r.reason), ...h!.reasons.flatMap((r) => [r.action?.text ?? "", r.then?.text ?? ""]));
}
const valid = mockSetupHealth({ stereo: true, option: null as never, launch: { state: "ok", dv_px: 0.1, matches: 300, message: null, when }, lastCheck: { when, ok: true, verdict: "ok" } });
sentences.push(valid!.reason);
for (const s of Object.values(SEEDED_SCAN_HEALTH)) sentences.push(s.reason ?? "", mockScanStatus(s).label);
for (const s of sentences.filter(Boolean))
  check(!/2-minute|launch check|still holds|\bIMU\b(?!\))|in height \(|\bValid\b/.test(s), `no old name in the demo's sentence: ${s}`);
check(sentences.some((s) => s.includes(HEALTH_WORDS.scansFiner)), "the scans' lower limit says why (U4)");
check(
  valid!.reason === `${HEALTH_WORDS.nothingOffFor}the cameras: the start-up check (today) and the calibration check (today)${HEALTH_WORDS.nothingWrong}`,
  `the valid sentence, as the engine makes it: ${valid!.reason}`,
);

console.log(`health.test (mock): ${checks} checks passed`);
