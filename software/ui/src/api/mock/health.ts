/**
 * The calibration's status in the mock (FEATURE_PLAN H3/H4): the setup state's `calibration_health` and each scan's
 * own `health_status`, in the engine's shape (`ohs/calib/health.py`), so the pages render in `?mock=1` too.
 *
 * The URL picks a status that the demo cannot reach by itself: `health=check` (2 of the last 3 scans found the vertical
 * laser off: Check recommended, lasers), `health=recalibrate` (they found the cameras' rows off: Recalibration
 * required, cameras), `health=cpu` (scans processed without a GPU: Valid, with the lasers and the IMU not judged and
 * the note why), `health=old` (the last 2-minute check is 20 days old: Check recommended for the calibration as a
 * whole). Without it the status follows the mock: `drift=1` (the launch check's drift), a failed 2-minute check, else
 * Valid for what was looked at.
 */
import type { MockHealth } from "../backend";
import type { CalibHealth, HealthAction, HealthPart, HealthReason, HealthState, LaunchCheck, ScanHealthSummary } from "../types";

export type MockHealthOption = MockHealth;

const LABEL: Record<HealthState, string> = { valid: "Valid", check: "Check recommended", recalibrate: "Recalibration required" };
const PART_LABEL: Record<HealthPart, string> = { cameras: "Cameras", lasers: "Lasers", imu: "IMU" };
const STEPS: Record<HealthAction["step"], string> = {
  check: "Run the 2-minute check",
  stereo: "Redo the camera calibration, then the lasers and the IMU (a new camera calibration needs them again)",
  lasers: "Redo the laser calibration",
  imu: "Redo the IMU calibration",
};
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
      reason: `The last 2-minute check did not pass (${ago(lc.when)}): ${lc.verdict.replace(/\.$/, "")}.`,
      action: act("check", "Run the 2-minute check again and follow what it says") });
  if (opts.option === "recalibrate") {
    scans.used = 3;
    findings.push({ state: "recalibrate", source: "scans", part: "cameras", kind: "rows", value: 0.34,
      reason: "In 2 of the last 3 scans the left and right images disagreed by 0.34 px in height (want under 0.15 px): the cameras have moved.",
      action: act("stereo"), then: null });
  }
  if (opts.option === "check") {
    scans.used = 3;
    parts.cameras = "valid";
    parts.imu = "valid";
    findings.push({ state: "check", source: "scans", part: "lasers", kind: "laser_line", value: 0.07,
      reason: "In 2 of the last 3 scans the vertical laser line sat 0.07 mm off the mesh (want under 0.05 mm): the vertical laser may have moved.",
      action: act("lasers"), then: act("stereo", "If that does not mend it, redo the camera calibration (a camera that turned about its axis looks the same)") });
  }
  if (opts.launch?.state === "drift")
    findings.push({ state: "recalibrate", source: "launch_check", part: "cameras", kind: "launch_drift", value: opts.launch.dv_px,
      reason: `When the scanner connected, its left and right images disagreed by ${(opts.launch.dv_px ?? 0).toFixed(2)} px in height (want under 0.6 px): the cameras may have moved.`,
      action: act("check", "Run the 2-minute check to confirm"), then: act("stereo", "If the check does not pass, redo the camera calibration") });
  if (opts.option === "old")
    findings.push({ state: "check", source: "board_check_age", part: null, kind: "age", value: 20,
      reason: "The last 2-minute check was 20 days ago; a check every 14 days or so catches a slow drift early.", action: act("check"), then: null });
  if (opts.option === "cpu") {
    scans.used = 3;
    note = "These scans were processed without a GPU, so only the cameras could be judged: the lasers and the IMU cannot be judged on this computer.";
  }
  const rank = { recalibrate: 0, check: 1 } as const;
  const partRank = (p: HealthPart | null) => (p === "cameras" ? 0 : p === "lasers" ? 1 : p === "imu" ? 2 : 3);
  findings.sort((a, b) => rank[a.state] - rank[b.state] || partRank(a.part) - partRank(b.part));
  const evidence: string[] = [];
  if (opts.launch?.state === "ok") evidence.push(`the launch check (${ago(opts.launch.when)})`);
  if (scans.used) evidence.push(`the last ${scans.used} scans`);
  if (lc?.ok) evidence.push(`the 2-minute check (${ago(lc.when)})`);
  if (evidence.length && parts.cameras === null) parts.cameras = "valid";
  const list = evidence.length > 1 ? `${evidence.slice(0, -1).join(", ")} and ${evidence[evidence.length - 1]}` : evidence[0];
  const judged = (Object.keys(parts) as HealthPart[]).filter((p) => parts[p] !== null || findings.some((f) => f.part === p));
  const valid = !evidence.length
    ? "Nothing speaks against the calibration yet: no check or scan has looked at it since it was saved."
    : judged.length === 3
      ? `Nothing looks off: ${list} found the calibration in order.`
      : `Nothing looks off for the ${judged.map((p) => (p === "imu" ? "IMU" : p)).join(" and the ")}: ${list} found nothing wrong.`;
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
  "Bracket top": { state: "valid", part: null, reason: "This scan shows nothing off for the cameras, the lasers and the IMU." },
  "Bracket side": {
    state: "check",
    part: "lasers",
    reason: "The vertical laser line sat 0.07 mm off the mesh (want under 0.05 mm): the vertical laser may have moved.",
  },
  "Wheel arch": {
    state: "recalibrate",
    part: "cameras",
    reason: "Its left and right images disagreed by 0.34 px in height (want under 0.15 px): the cameras have moved.",
  },
};

/** A newly processed scan in the mock: nothing off. */
export const MOCK_SCAN_VALID: ScanHealthSummary = SEEDED_SCAN_HEALTH["Bracket top"];
