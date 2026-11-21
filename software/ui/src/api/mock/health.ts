/**
 * The calibration's status in the mock (FEATURE_PLAN H3/H4): the setup state's `calibration_health` and each scan's
 * own `health_status`, in the engine's shape (`ohs/calib/health.py`), so the pages render in `?mock=1` too.
 *
 * The URL picks a status that the demo cannot reach by itself: `health=check` (2 of the last 3 scans found the vertical
 * laser off: Check recommended, lasers), `health=recalibrate` (they found the cameras' rows off: Recalibration
 * required, cameras), `health=cpu` (scans processed without a graphics card by an earlier version, which judges only
 * the cameras there: Calibration valid, with the lasers and the motion sensor not judged and the note why), `health=old`
 * (the last calibration check is 20 days old: Check recommended for the calibration as a whole), `health=failed` (a
 * calibration check that did not pass two days ago, mock/setup.ts: Recalibration required, until a check is run in the
 * demo). Without it the status follows the mock: `drift=1` (the start-up check's drift), a failed calibration check,
 * else Calibration valid for what was looked at.
 *
 * Its sentences are the engine's (UX_PLAN U51): the fixed words are in `HEALTH_WORDS`, and `health.test.ts` finds each
 * of them in `ohs/calib/health.py`.
 */
import type { MockHealth } from "../backend";
import type { CalibHealth, HealthAction, HealthPart, HealthReason, HealthState, LaunchCheck, ScanHealthSummary } from "../types";

export type MockHealthOption = MockHealth;

export const LABEL: Record<HealthState, string> = { valid: "Calibration valid", check: "Check recommended", recalibrate: "Recalibration required" };
export const PART_LABEL: Record<HealthPart, string> = { cameras: "Cameras", lasers: "Lasers", imu: "Motion sensor (IMU)" };
export const STEPS: Record<HealthAction["step"], string> = {
  check: "Run the calibration check",
  stereo: "Redo the camera calibration, then the lasers and the motion sensor (a new camera calibration needs them again)",
  lasers: "Redo the laser calibration",
  imu: "Redo the motion sensor calibration",
};
/** The engine's fixed words that the demo's sentences are made of (each is in `ohs/calib/health.py`, health.test.ts). */
export const HEALTH_WORDS = {
  rowsOff: " the two cameras out of line, enough to put measurements 0.1 mm off: left/right height mismatch ",
  scansFiner: "scans measure this more finely than the calibration check, so their limit is lower).",
  lineOff: " mm off the surface (want within ",
  linesInLine: " mm) while the cameras look in line.",
  lineThen: "If later scans still show it, redo the camera calibration: ",
  vertical: "a small turn of one camera looks just the same",
  launchFound: "When the scanner connected, the start-up check found a left/right height mismatch",
  launchDrift: " (want under 0.6 px: the start-up check looks at three pictures only, so its limit is wider than the calibration check's): the cameras may have moved.",
  launchConfirm: "Run the calibration check to confirm",
  launchThen: "If the check does not pass, redo the camera calibration",
  boardFailed: "The last calibration check did not pass (",
  boardAgain: "Run the calibration check again and follow what it says",
  boardAge: "The last calibration check was ",
  boardAgeTail: " days or so catches a slow drift early.",
  cpuOlder: " processed without a graphics card ",
  cpuOlderHow: "by an earlier version of the software",
  cpuOlderThen: "processing it again judges the lasers and the motion sensor too",
  cpuOnly: ", so only the cameras could be judged: ",
  nothingYet: "Nothing speaks against the calibration yet: no check or scan has looked at it since it was saved.",
  nothingOff: "Nothing looks off: ",
  inOrder: " found the calibration in order.",
  nothingOffFor: "Nothing looks off for ",
  nothingWrong: " found nothing wrong.",
  startUp: "the start-up check (",
  calibCheck: "the calibration check (",
  scanNothingOff: "This scan shows nothing off for ",
} as const;
const W = HEALTH_WORDS;
const act = (step: HealthAction["step"], text?: string): HealthAction => ({ step, text: text ?? STEPS[step] });

type Finding = Omit<HealthReason, "state"> & { state: Exclude<HealthState, "valid"> };

function joined(
  findings: Finding[],
  parts: CalibHealth["parts"],
  valid: string,
  note: string | null,
  extra: Partial<CalibHealth>,
): CalibHealth {
  const top = findings[0] ?? null;
  for (const f of findings) if (f.part && (parts[f.part] === null || parts[f.part] === "valid")) parts[f.part] = f.state;
  const state: HealthState = top?.state ?? "valid";
  return {
    version: 1,
    state,
    label: LABEL[state],
    part: top?.part ?? null,
    part_label: top?.part ? PART_LABEL[top.part] : null,
    reason: top?.reason ?? valid,
    action: top?.action ?? null,
    then: top?.then ?? null,
    reasons: findings,
    parts,
    not_judged: (Object.keys(parts) as HealthPart[]).filter((p) => parts[p] === null),
    note,
    provisional: true,
    source: "setup",
    ...extra,
  };
}

const ago = (iso: string) => {
  const days = Math.floor((Date.now() - Date.parse(iso)) / 86_400_000);
  return days <= 0 ? "today" : days === 1 ? "yesterday" : days < 7 ? `${days} days ago` : `on ${iso.slice(0, 10)}`;
};

/** The setup state's status for a target with a stereo calibration (null without one). */
export function mockSetupHealth(opts: {
  stereo: boolean;
  option: MockHealthOption;
  launch: LaunchCheck | null;
  lastCheck: { when: string; ok: boolean; verdict: string } | null;
}): CalibHealth | null {
  if (!opts.stereo) return null;
  const findings: Finding[] = [];
  const parts: CalibHealth["parts"] = { cameras: null, lasers: null, imu: null };
  const scans = { used: 0, window: 3, agree: 2 };
  let note: string | null = null;
  const lc = opts.lastCheck;
  if (lc && !lc.ok)
    findings.push({ state: "recalibrate", source: "board_check", part: null, kind: "board_check_failed", value: null, then: null,
      reason: `${W.boardFailed}${ago(lc.when)}): ${lc.verdict.replace(/\.$/, "")}.`,
      action: act("check", W.boardAgain) });
  if (opts.option === "recalibrate") {
    scans.used = 3;
    findings.push({ state: "recalibrate", source: "scans", part: "cameras", kind: "rows", value: 0.34,
      reason: `2 of the last 3 scans show${W.rowsOff}0.34 px (want under 0.15 px: ${W.scansFiner}`,
      action: act("stereo"), then: null });
  }
  if (opts.option === "check") {
    scans.used = 3;
    parts.cameras = "valid";
    parts.imu = "valid";
    findings.push({ state: "check", source: "scans", part: "lasers", kind: "line", value: 0.07,
      reason: `2 of the last 3 scans show the vertical laser line 0.07${W.lineOff}0.05${W.linesInLine}`,
      action: act("lasers"), then: act("stereo", `${W.lineThen}${W.vertical}`) });
  }
  if (opts.launch?.state === "drift")
    findings.push({ state: "recalibrate", source: "launch_check", part: "cameras", kind: "launch_drift", value: opts.launch.dv_px,
      reason: `${W.launchFound} of ${(opts.launch.dv_px ?? 0).toFixed(2)} px${W.launchDrift}`,
      action: act("check", W.launchConfirm), then: act("stereo", W.launchThen) });
  if (opts.option === "old")
    findings.push({ state: "check", source: "board_check_age", part: null, kind: "age", value: 20,
      reason: `${W.boardAge}20 days ago; a check every 14${W.boardAgeTail}`, action: act("check"), then: null });
  if (opts.option === "cpu") {
    scans.used = 3;
    note = `These scans were${W.cpuOlder}${W.cpuOlderHow}${W.cpuOnly}${W.cpuOlderThen}.`;
  }
  const rank = { recalibrate: 0, check: 1 } as const;
  const partRank = (p: HealthPart | null) => (p === "cameras" ? 0 : p === "lasers" ? 1 : p === "imu" ? 2 : 3);
  findings.sort((a, b) => rank[a.state] - rank[b.state] || partRank(a.part) - partRank(b.part));
  const evidence: string[] = [];
  if (opts.launch?.state === "ok") evidence.push(`${W.startUp}${ago(opts.launch.when)})`);
  if (scans.used) evidence.push(`the last ${scans.used} scans`);
  if (lc?.ok) evidence.push(`${W.calibCheck}${ago(lc.when)})`);
  if (evidence.length && parts.cameras === null) parts.cameras = "valid";
  const list = evidence.length > 1 ? `${evidence.slice(0, -1).join(", ")} and ${evidence[evidence.length - 1]}` : evidence[0];
  const judged = (Object.keys(parts) as HealthPart[]).filter((p) => parts[p] !== null || findings.some((f) => f.part === p));
  const valid = !evidence.length
    ? W.nothingYet
    : judged.length === 3
      ? `${W.nothingOff}${list}${W.inOrder}`
      : `${W.nothingOffFor}the ${judged.map((p) => (p === "imu" ? "motion sensor" : p)).join(" and the ")}: ${list}${W.nothingWrong}`;
  return joined(findings, parts, valid, note, { scans });
}

/** A scan's own full status (report.json's `health_status`) from the short one the scan list keeps. */
export function mockScanStatus(s: ScanHealthSummary): CalibHealth {
  const parts: CalibHealth["parts"] = { cameras: "valid", lasers: "valid", imu: "valid" };
  if (s.part) parts[s.part] = s.state;
  const action = s.state === "valid" ? null : act(s.part === "lasers" ? "lasers" : s.part === "imu" ? "imu" : "stereo");
  return {
    version: 1,
    state: s.state,
    label: LABEL[s.state],
    part: s.part,
    part_label: s.part ? PART_LABEL[s.part] : null,
    reason: s.reason ?? "",
    action,
    then: null,
    reasons: s.state === "valid" ? [] : [{ state: s.state, source: "scan", part: s.part, reason: s.reason ?? "", action, then: null, kind: "mock", value: null }],
    parts,
    not_judged: [],
    note: null,
    provisional: true,
    source: "scan",
  };
}

/** The seeded scans' own status, by name (one from before H3 has none). */
export const SEEDED_SCAN_HEALTH: Record<string, ScanHealthSummary> = {
  "Bracket top": { state: "valid", part: null, reason: `${W.scanNothingOff}the cameras, the lasers and the motion sensor.` },
  "Bracket side": {
    state: "check",
    part: "lasers",
    reason: `This scan shows the vertical laser line 0.07${W.lineOff}0.05${W.linesInLine}`,
  },
  "Wheel arch": {
    state: "recalibrate",
    part: "cameras",
    reason: `This scan shows${W.rowsOff}0.34 px (want under 0.15 px: ${W.scansFiner}`,
  },
};

/**
 * The camera path's check (ENGINE_PLAN X0a) of the seeded scans, by name: one whose tracking slid, in the engine's
 * words (`ohs/track/path_check.py`). Every other scan's path held.
 */
export const SEEDED_PATH_WARNING: Record<string, { path_check: Record<string, unknown>; warnings: string[] }> = {
  "Bracket side": {
    path_check: { version: 1, window_s: 0.8, limit_mm: 4.0, frames: 812, jump_mm: 9.4, frame: 412, state: "warn" },
    warnings: [
      "The camera path jumps by 9 mm near frame 412, more than a hand moves in 0.8 s: tracking may have slid there. A thin part (a sheet, or a plate lying flat) can then come out thinner or partly missing, and sizes across that part of the scan may be off. Scan it again more slowly, with more of the part's edges in view. This scan was processed without a graphics card, where tracking holds flat, plain scenes less well.",
    ],
  },
};

/** A scan's camera path check in the mock: the seeded one, else a path that held. */
export function mockPathCheck(name: string, frames: number): { path_check: Record<string, unknown>; warnings: string[] } {
  return SEEDED_PATH_WARNING[name] ?? { path_check: { version: 1, window_s: 0.8, limit_mm: 4.0, frames, jump_mm: 1.6, frame: 3, state: "ok", warning: null }, warnings: [] };
}

/** A newly processed scan in the mock: nothing off. */
export const MOCK_SCAN_VALID: ScanHealthSummary = SEEDED_SCAN_HEALTH["Bracket top"];
