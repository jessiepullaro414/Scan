/**
 * The calibration's status in words (FEATURE_PLAN H4): Valid, Check recommended or Recalibration required, as the
 * engine judges it (H3, `ohs/calib/health.py`): the setup state's `calibration_health` for the device chip, the setup
 * overview and the Scan page, and each scan's own `health_status` for its card and its report. The app warns with it;
 * it never blocks a scan. Plain functions, tested on their own (`calibHealth.test.ts`).
 *
 * Rules kept here: a part that was not judged is never called fine (it is named as not judged); the limits are
 * provisional until the real scanner is measured (H5), said once where the detail is shown; colour is never the only
 * signal (each tone comes with its words, and the pages add an icon).
 */
import type { CalibHealth, DeviceState, HealthAction, HealthPart, HealthState, ScanHealthSummary, SetupState, SetupStep } from "../api/types";
import type { RouteName, SetupPage } from "./router";

export const STATES: HealthState[] = ["valid", "check", "recalibrate"];
export const STATE_WORDS: Record<HealthState, string> = { valid: "Calibration valid", check: "Check recommended", recalibrate: "Recalibration required" };
/** Amber for a check, red for a recalibration, nothing loud for valid. */
export type HealthTone = "neutral" | "warn" | "danger";
export const STATE_TONE: Record<HealthState, HealthTone> = { valid: "neutral", check: "warn", recalibrate: "danger" };
/** The device chip's word. */
const SHORT: Record<HealthState, string> = { valid: "Calibration valid", check: "Check recommended", recalibrate: "Recalibrate" };
/** The button for each wizard step an action names (the action's own words go in its hover text). */
export const STEP_BUTTON: Record<HealthAction["step"], string> = {
  check: "Check the calibration",
  stereo: "Redo the cameras",
  lasers: "Redo the lasers",
  imu: "Redo the motion sensor",
};
export const PROVISIONAL = "The limits are provisional until the real scanner has been measured.";

/** A headline with what it is about in front, unless it names the calibration itself ("Calibration valid"). */
export function calibrationSays(headline: string): string {
  const h = headline.trim();
  if (!h) return "Calibration";
  return /^calibration\b/i.test(h) ? h : `Calibration: ${h}`;
}

const PART_WORDS: Record<HealthPart, string> = { cameras: "the cameras", lasers: "the lasers", imu: "the motion sensor" };
const PART_LABEL: Record<HealthPart, string> = { cameras: "Cameras", lasers: "Lasers", imu: "Motion sensor" };

const isState = (s: unknown): s is HealthState => typeof s === "string" && (STATES as string[]).includes(s);
const isPart = (p: unknown): p is HealthPart => p === "cameras" || p === "lasers" || p === "imu";
const asSentence = (s: string) => {
  const t = s.trim();
  return t && !/[.!?]$/.test(t) ? `${t}.` : t;
};

/** "the lasers", "the lasers and the motion sensor", "the cameras, the lasers and the motion sensor". */
export function partsWords(parts: HealthPart[]): string {
  const w = parts.filter(isPart).map((p) => PART_WORDS[p]);
  return w.length <= 1 ? (w[0] ?? "") : `${w.slice(0, -1).join(", ")} and ${w[w.length - 1]}`;
}

export type HealthView = {
  state: HealthState;
  tone: HealthTone;
  /** "Calibration valid", "Check recommended for the lasers", "Recalibration required" (the calibration as a whole). */
  title: string;
  /** The device chip's word: "Calibration valid", "Check recommended", "Recalibrate" (U48: one name for the state). */
  short: string;
  /** The engine's one sentence. */
  reason: string;
  /** The button: the wizard page it opens, its label and the action's own words (hover). */
  action: { page: SetupPage; label: string; hint: string } | null;
  /** What to try if that does not mend it: the quieter second line. */
  then: string | null;
  /** Why something could not be judged (the engine's note), or which parts were not judged; null when all were. */
  judged: string | null;
  /** The quiet "provisional" line, or null. */
  provisional: string | null;
};

function actionOf(a: HealthAction | null | undefined): HealthView["action"] {
  if (!a || !(a.step in STEP_BUTTON)) return null;
  return { page: a.step, label: STEP_BUTTON[a.step], hint: asSentence(a.text || STEP_BUTTON[a.step]).replace(/\.$/, "") };
}

function judgedLine(h: CalibHealth): string | null {
  if (h.note) return asSentence(h.note);
  const parts = (h.not_judged ?? []).filter(isPart);
  if (!parts.length) return null;
  if (h.source === "scan") return `Not judged in this scan: ${partsWords(parts)}.`;
  return `Not judged yet: ${partsWords(parts)}. A scan made with this calibration judges ${parts.length === 1 ? "it" : "them"}.`;
}

/**
 * "Calibration loaded": a calibration file is in use (U48: never the state's name). Beside a status that is not valid
 * it says which, and is shown in amber (U46): "Calibration loaded (recalibration required for the cameras)".
 */
export function loadedLine(hv: Pick<HealthView, "state" | "title"> | null): { text: string; warn: boolean } {
  if (!hv || hv.state === "valid") return { text: "Calibration loaded", warn: false };
  return { text: `Calibration loaded (${hv.title.charAt(0).toLowerCase()}${hv.title.slice(1)})`, warn: true };
}

/** The status made ready to show; null without one (no calibration, an older engine) or for one it cannot read. */
export function healthView(h: CalibHealth | null | undefined): HealthView | null {
  if (!h || !isState(h.state)) return null;
  const part = isPart(h.part) ? h.part : null;
  const title = h.state === "valid" ? "Calibration valid" : part ? `${STATE_WORDS[h.state]} for ${PART_WORDS[part]}` : STATE_WORDS[h.state];
  return {
    state: h.state,
    tone: STATE_TONE[h.state],
    title,
    short: SHORT[h.state],
    reason: h.reason ? asSentence(h.reason) : "",
    action: h.state === "valid" ? null : actionOf(h.action),
    then: h.state !== "valid" && h.then?.text ? asSentence(h.then.text) : null,
    judged: judgedLine(h),
    provisional: h.provisional ? PROVISIONAL : null,
  };
}

/**
 * The status of the calibration the connected device scans with: the setup state's, when it speaks for that device
 * (the camera: the "device" target, the simulator: "sim"). A replay carries its own calibration, so nothing then.
 */
export function deviceHealth(device: DeviceState | null | undefined, setup: SetupState | null | undefined): CalibHealth | null {
  if (!device?.connected || !device.calibration?.loaded || !setup) return null;
  // U21: the simulator's demo brings its own true calibration; Setup's status is about the simulator's own one
  if (device.calibration.source === "demo") return null;
  const target = device.source === "camera" ? "device" : device.source === "sim" ? "sim" : null;
  if (!target || setup.target !== target) return null;
  return setup.calibration_health ?? null;
}

/**
 * U21: which calibration the connected device scans with, in the chip's word and a line for its tooltip: the
 * simulator's demo uses its own true calibration ("Demo calibration"), which is not the one Setup makes for the
 * simulator; else a file is in use ("Calibration loaded"), or none. Null when nothing is connected.
 */
export function calibInUse(device: DeviceState | null | undefined): { short: string; line: string; demo: boolean } | null {
  if (!device?.connected) return null;
  const c = device.calibration;
  if (!c?.loaded) return { short: "No calibration", line: "No calibration: calibrate in Setup", demo: false };
  if (c.source === "demo")
    return {
      short: "Demo calibration",
      line: "Demo calibration: the simulator's demo scans with its own true calibration. Setup calibrates the simulator separately.",
      demo: true,
    };
  return { short: "Calibration loaded", line: "Calibration loaded", demo: false };
}

// ------------------------------------------------------------------ one button, one fact
/** The pages that show the status's line with its own button (`CalibHealthCallout`): the Scan and Device pages. */
const PAGES_WITH_LINE: RouteName[] = ["scan", "device"];

/**
 * Whether the header shows the status's button beside the device chip. Not on a page that shows the line: the line
 * says why (the engine's sentence) and has the same button, so the header's would only repeat it. The chip keeps its
 * word and icon everywhere.
 */
export function headerHealthButton(route: RouteName): boolean {
  return !PAGES_WITH_LINE.includes(route);
}

/**
 * The setup overview's step card as it is shown beside the status strip: when the strip already says what the
 * 2-minute check found (a failed check) or the launch check's drift, the Check card does not say it again: its summary
 * is left out and the card shows its own description, its state and its date. Everything else is shown as the engine
 * gives it.
 */
export function overviewStep(step: SetupStep, health: CalibHealth | null | undefined): SetupStep {
  if (step.id !== "check" || step.state !== "warn" || !health || health.state === "valid" || !isState(health.state)) return step;
  const said = (health.reasons ?? []).some(
    (r) => r.source === "board_check" || (r.source === "launch_check" && /start-up check|launch check/i.test(step.summary)),
  );
  return said ? { ...step, summary: "" } : step;
}

// ------------------------------------------------------------------ a scan's own status
/** A scan's own status from its report (`health_status`: the short form of the scan list, or report.json's full one). */
export function scanHealthOf(report: unknown): ScanHealthSummary | null {
  if (!report || typeof report !== "object") return null;
  const hs = (report as { health_status?: unknown }).health_status;
  if (!hs || typeof hs !== "object") return null;
  const { state, part, reason } = hs as { state?: unknown; part?: unknown; reason?: unknown };
  if (!isState(state)) return null;
  return { state, part: isPart(part) ? part : null, reason: typeof reason === "string" && reason.trim() ? reason : null };
}

const SCANNED: Record<Exclude<HealthState, "valid">, string> = {
  check: "Scanned with a calibration that needed a check",
  recalibrate: "Scanned with a calibration that needed recalibrating",
};

/** The scan card's line when its own status was not Valid (the reason goes on hover); null otherwise. */
export function scanHealthLine(s: ScanHealthSummary | null | undefined): { text: string; tone: HealthTone; title: string } | null {
  if (!s || !isState(s.state) || s.state === "valid") return null;
  const reason = s.reason ? asSentence(s.reason) : STATE_WORDS[s.state];
  return { text: SCANNED[s.state], tone: STATE_TONE[s.state], title: s.part ? `${PART_LABEL[s.part]}: ${reason}` : reason };
}

/** The scan's status in its report (the viewer's processing report): the headline, the reason and the quiet lines. */
export function scanHealthDetail(report: unknown): { tone: HealthTone; headline: string; reason: string | null; lines: string[] } | null {
  const s = scanHealthOf(report);
  if (!s) return null;
  const full = (report as { health_status: Partial<CalibHealth> }).health_status;
  const headline = s.state === "valid" ? STATE_WORDS.valid : `${SCANNED[s.state]}${s.part ? ` (${PART_WORDS[s.part].replace(/^the /, "")})` : ""}`;
  const lines: string[] = [];
  const judged = Array.isArray(full.not_judged) || full.note ? judgedLine({ ...(full as CalibHealth), source: "scan" }) : null;
  if (judged) lines.push(judged);
  lines.push(PROVISIONAL);
  return { tone: STATE_TONE[s.state], headline, reason: s.reason ? asSentence(s.reason) : null, lines };
}
