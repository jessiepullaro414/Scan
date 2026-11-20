/**
 * Checks for the calibration's status in words (FEATURE_PLAN H4; `npm test`). Plain TypeScript that Node runs as it is
 * (type stripping), no test framework: a failed check throws.
 */
import type { CalibHealth, DeviceState, SetupState } from "../api/types.ts";
import {
  deviceHealth,
  healthView,
  PROVISIONAL,
  scanHealthDetail,
  scanHealthLine,
  scanHealthOf,
} from "./calibHealth.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`calibHealth.test: ${what}`);
}

/** The setup state's status as the real engine gives it today (simulator, 2026-11-19). */
const VALID: CalibHealth = {
  version: 1,
  state: "valid",
  label: "Valid",
  part: null,
  part_label: null,
  reason: "Nothing looks off for the cameras: the launch check (today) and the 2-minute check (on 2026-11-09) found nothing wrong.",
  action: null,
  then: null,
  reasons: [],
  parts: { cameras: "valid", lasers: null, imu: null },
  not_judged: ["lasers", "imu"],
  note: null,
  provisional: true,
  source: "setup",
  scans: { used: 0, window: 3, agree: 2 },
};

const CHECK_LASERS: CalibHealth = {
  ...VALID,
  state: "check",
  label: "Check recommended",
  part: "lasers",
  part_label: "Lasers",
  reason: "In 2 of the last 3 scans the vertical laser line sat 0.07 mm off the mesh (want under 0.05 mm).",
  action: { step: "lasers", text: "Redo the laser calibration" },
  then: { step: "stereo", text: "If that does not mend it, redo the camera calibration" },
  reasons: [],
  parts: { cameras: "valid", lasers: "check", imu: "valid" },
  not_judged: [],
};

const RECAL_CAMERAS: CalibHealth = {
  ...VALID,
  state: "recalibrate",
  label: "Recalibration required",
  part: "cameras",
  part_label: "Cameras",
  reason: "When the scanner connected, its left and right images disagreed by 1.38 px in height (want under 0.6 px): the cameras may have moved.",
  action: { step: "check", text: "Run the 2-minute check to confirm" },
  then: { step: "stereo", text: "If the check does not pass, redo the camera calibration" },
  parts: { cameras: "recalibrate", lasers: null, imu: null },
};

const AGE: CalibHealth = {
  ...VALID,
  state: "check",
  label: "Check recommended",
  part: null,
  reason: "The last 2-minute check was 20 days ago; a check every 14 days or so catches a slow drift early.",
  action: { step: "check", text: "Run the 2-minute check" },
  parts: { cameras: "valid", lasers: null, imu: null },
};

// ---------------------------------------------------------------- the setup state's status
const v = healthView(VALID)!;
check(v.state === "valid" && v.tone === "neutral", "valid is nothing loud");
check(v.title === "Calibration valid", `valid title: ${v.title}`);
check(v.short === "Calibrated", `valid chip word: ${v.short}`);
check(v.reason === VALID.reason, "the engine's own sentence");
check(v.action === null && v.then === null, "valid has no button");
// never says a part is fine when it was not judged
check(v.judged === "Not judged yet: the lasers and the IMU. A scan made with this calibration judges them.", `not judged: ${v.judged}`);
check(!/lasers[^.]*(fine|valid|good)/i.test(`${v.title} ${v.reason} ${v.judged}`), "the lasers are never called fine");
check(v.provisional === PROVISIONAL && PROVISIONAL === "The limits are provisional until the real scanner has been measured.", "provisional line");

const c = healthView(CHECK_LASERS)!;
check(c.tone === "warn" && c.title === "Check recommended for the lasers", `check title: ${c.title}`);
check(c.short === "Check recommended", `check chip word: ${c.short}`);
check(c.action?.page === "lasers" && c.action.label === "Redo the lasers" && c.action.hint === "Redo the laser calibration", "the lasers' button");
check(c.then === "If that does not mend it, redo the camera calibration.", `then line: ${c.then}`);
check(c.judged === null, "everything judged: no line");

const r = healthView(RECAL_CAMERAS)!;
check(r.tone === "danger" && r.title === "Recalibration required for the cameras", `recalibrate title: ${r.title}`);
check(r.short === "Recalibrate", `recalibrate chip word: ${r.short}`);
check(r.action?.page === "check" && r.action.label === "Run the 2-minute check" && r.action.hint === "Run the 2-minute check to confirm", "confirm with the check first");
check(r.then === "If the check does not pass, redo the camera calibration.", "then for the cameras");

const a = healthView(AGE)!;
check(a.title === "Check recommended", `the calibration as a whole: ${a.title}`);

check(healthView({ ...RECAL_CAMERAS, action: { step: "stereo", text: "Redo the camera calibration, then the lasers and the IMU (a new camera calibration needs them again)" } })!.action!.label === "Redo the cameras", "stereo button");
check(healthView({ ...CHECK_LASERS, part: "imu", action: { step: "imu", text: "Redo the IMU calibration" } })!.title === "Check recommended for the IMU", "the IMU in capitals");
check(healthView({ ...CHECK_LASERS, part: "imu", action: { step: "imu", text: "Redo the IMU calibration" } })!.action!.label === "Redo the IMU", "IMU button");

// the engine's note says why something could not be judged: it replaces the generic line
const cpu = healthView({ ...VALID, note: "These scans were processed without a GPU, so only the cameras could be judged: the lasers and the IMU cannot be judged on this computer." })!;
check(cpu.judged!.startsWith("These scans were processed without a GPU"), `note: ${cpu.judged}`);
check(healthView({ ...VALID, not_judged: ["imu"] })!.judged === "Not judged yet: the IMU. A scan made with this calibration judges it.", "one part, singular");
check(healthView({ ...VALID, not_judged: ["cameras", "lasers", "imu"] })!.judged!.includes("the cameras, the lasers and the IMU"), "three parts");
check(healthView({ ...VALID, provisional: false })!.provisional === null, "not provisional: no line");
check(healthView(null) === null && healthView(undefined) === null, "no calibration: nothing");
check(healthView({ ...VALID, state: "nonsense" as never }) === null, "an unknown state shows nothing");
check(healthView({ ...CHECK_LASERS, action: { step: "selftest" as never, text: "?" } })!.action === null, "an unknown step: no button");

// ---------------------------------------------------------------- which device it speaks for
const dev = (over: Partial<DeviceState>): DeviceState => ({
  source: "camera",
  connected: true,
  busy: null,
  camera: null,
  pico: null,
  calibration: { loaded: true, path: null, image_size: null, baseline_mm: null, lasers: [], imu: false },
  replay: null,
  ...over,
});
const setup = (target: "device" | "sim", h: CalibHealth | null | undefined) => ({ target, calibration_health: h }) as unknown as SetupState;
check(deviceHealth(dev({}), setup("device", CHECK_LASERS)) === CHECK_LASERS, "the camera: the device target's status");
check(deviceHealth(dev({ source: "sim" }), setup("sim", CHECK_LASERS)) === CHECK_LASERS, "the simulator: the sim target's");
check(deviceHealth(dev({ source: "sim" }), setup("device", CHECK_LASERS)) === null, "a stale target: nothing until the setup state follows");
check(deviceHealth(dev({ source: "replay" }), setup("device", CHECK_LASERS)) === null, "a replay carries its own calibration: nothing");
check(deviceHealth(dev({ connected: false }), setup("device", CHECK_LASERS)) === null, "not connected: nothing");
check(deviceHealth(dev({ calibration: { loaded: false, path: null, image_size: null, baseline_mm: null, lasers: [], imu: false } }), setup("device", CHECK_LASERS)) === null, "no calibration loaded: nothing");
check(deviceHealth(dev({}), setup("device", undefined)) === null, "an older engine: nothing");
check(deviceHealth(null, setup("device", VALID)) === null && deviceHealth(dev({}), null) === null, "nothing known yet");

// ---------------------------------------------------------------- a scan's own status
check(scanHealthOf({ health_status: { state: "check", part: "lasers", reason: "x" } })?.state === "check", "the short form");
check(scanHealthOf({ health_status: RECAL_CAMERAS })?.part === "cameras", "the full form (report.json)");
check(scanHealthOf({ mode: "markerless" }) === null, "a scan from before: nothing");
check(scanHealthOf({ health: { version: 1, row_error: {} } }) === null, "the signs alone are not a status");
check(scanHealthOf({ health_status: { state: "fine" } }) === null && scanHealthOf(null) === null && scanHealthOf("x") === null, "broken: nothing");

check(scanHealthLine({ state: "valid", part: null, reason: "This scan shows nothing off." }) === null, "valid: nothing on the card");
check(scanHealthLine(null) === null, "no status: nothing on the card");
const lc = scanHealthLine({ state: "check", part: "lasers", reason: "The vertical laser line sat 0.07 mm off the mesh." })!;
check(lc.text === "Scanned with a calibration that needed a check" && lc.tone === "warn", `check card: ${lc.text}`);
check(lc.title === "Lasers: The vertical laser line sat 0.07 mm off the mesh.", `card hover: ${lc.title}`);
const lr = scanHealthLine({ state: "recalibrate", part: "cameras", reason: "Its rows disagree by 0.41 px." })!;
check(lr.text === "Scanned with a calibration that needed recalibrating" && lr.tone === "danger", `recalibrate card: ${lr.text}`);
check(lr.title === "Cameras: Its rows disagree by 0.41 px.", "card hover with the part");
check(scanHealthLine({ state: "check", part: null, reason: null })!.title === "Check recommended", "no reason: the state's words");

// the scan's report (the viewer's processing report panel)
check(scanHealthDetail({ mode: "markerless" }) === null, "no status in the report: nothing");
const dv = scanHealthDetail({ health_status: { ...VALID, source: "scan", reason: "This scan shows nothing off for the cameras.", note: "This scan is short (40 frames), so only the cameras could be judged." } })!;
check(dv.tone === "neutral" && dv.headline === "Valid" && dv.reason === "This scan shows nothing off for the cameras.", `valid detail: ${dv.headline}`);
check(dv.lines.length === 2 && dv.lines[0].startsWith("This scan is short") && dv.lines[1] === PROVISIONAL, `valid detail lines: ${dv.lines.join(" | ")}`);
const dn = scanHealthDetail({ health_status: { ...VALID, source: "scan", reason: "This scan shows nothing off for the cameras." } })!;
check(dn.lines[0] === "Not judged in this scan: the lasers and the IMU.", `scan not judged: ${dn.lines[0]}`);
const dc = scanHealthDetail({ health_status: { state: "recalibrate", part: "cameras", reason: "Its rows disagree by 0.41 px." } })!;
check(dc.tone === "danger" && dc.headline === "Scanned with a calibration that needed recalibrating (cameras)", `recalibrate detail: ${dc.headline}`);
check(dc.lines.length === 1 && dc.lines[0] === PROVISIONAL, "the short form: the provisional line only");

console.log(`calibHealth.test: ${checks} checks passed`);
