/**
 * The setup wizard's words and small helpers, shared by the wizard, Home and the shell.
 */
import {
  Aperture,
  ClipboardCheck,
  Flag,
  ListChecks,
  Orbit,
  Printer,
  Usb,
  Zap,
  type LucideIcon,
} from "lucide-react";
import type {
  CalibGrade,
  CalibKind,
  CheckStatus,
  SelfTestCheck,
  SetupState,
  StepId,
  StepState,
} from "../api/types";
import type { SetupPage } from "./router";

export const STEP_ORDER: StepId[] = ["connect", "selftest", "print", "stereo", "lasers", "imu", "check"];

export type StepMeta = {
  title: string;
  /** One line: why this step matters. */
  why: string;
  icon: LucideIcon;
  /** Rough time, for the overview. */
  time: string;
  minutes: number;
};

export const STEP_META: Record<SetupPage, StepMeta> = {
  connect: {
    title: "Connect",
    why: "Studio needs to see the stereo camera and the Pico before anything else.",
    icon: Usb,
    time: "1 min",
    minutes: 1,
  },
  selftest: {
    title: "Self-test",
    why: "A one-minute hardware check catches wiring and camera problems before they spoil a calibration.",
    icon: ListChecks,
    time: "1 min",
    minutes: 1,
  },
  print: {
    title: "Print targets",
    why: "Calibration measures against the printed board, so the print must be exactly the right size.",
    icon: Printer,
    time: "5 min",
    minutes: 5,
  },
  stereo: {
    title: "Stereo",
    why: "Lens and baseline calibration: the base of every millimetre the scanner measures.",
    icon: Aperture,
    time: "5 min",
    minutes: 5,
  },
  lasers: {
    title: "Lasers",
    why: "The two laser planes give precise reference points, even on black and glossy paint.",
    icon: Zap,
    time: "3 min",
    minutes: 3,
  },
  imu: {
    title: "IMU",
    why: "The gyro keeps tracking steady on fast sweeps and plain, featureless surfaces.",
    icon: Orbit,
    time: "1 min",
    minutes: 1,
  },
  check: {
    title: "Check",
    why: "A two-minute check that the saved calibration still holds after a knock or a hot day in the car.",
    icon: ClipboardCheck,
    time: "2 min",
    minutes: 2,
  },
  done: {
    title: "Done",
    why: "Everything the scanner needs is in place.",
    icon: Flag,
    time: "",
    minutes: 0,
  },
};

/** "Stereo calibration" → "stereo calibration", but "IMU calibration" stays as it is. */
export const lcFirst = (s: string) => (/^[A-Z][a-z]/.test(s) ? s.charAt(0).toLowerCase() + s.slice(1) : s);

export const CAPTURE_PAGES: SetupPage[] = ["stereo", "lasers", "imu", "check"];
export const isCapturePage = (p: SetupPage | null | undefined): p is CalibKind =>
  !!p && (CAPTURE_PAGES as string[]).includes(p);

/** The wizard pages in order, after the overview. */
export const PAGE_ORDER: SetupPage[] = [...STEP_ORDER, "done"];

export function nextPage(p: SetupPage | null): SetupPage | null {
  if (p === null) return PAGE_ORDER[0];
  const i = PAGE_ORDER.indexOf(p);
  return i >= 0 && i < PAGE_ORDER.length - 1 ? PAGE_ORDER[i + 1] : null;
}

/** `null` is the overview. */
export function prevPage(p: SetupPage | null): SetupPage | null | undefined {
  if (p === null) return undefined;
  const i = PAGE_ORDER.indexOf(p);
  return i > 0 ? PAGE_ORDER[i - 1] : null;
}

export const STATE_LABEL: Record<StepState, string> = {
  done: "Done",
  todo: "To do",
  warn: "Needs attention",
  optional: "Optional",
};

/** The step "Continue setup" opens: the first one to do or to fix (optional steps are skipped). */
export function firstUnfinished(setup: SetupState | null | undefined): SetupPage {
  if (!setup) return "connect";
  for (const id of STEP_ORDER) {
    const st = setup.steps.find((s) => s.id === id);
    if (st && (st.state === "todo" || st.state === "warn")) return id;
  }
  return "done";
}

export function setupProgress(setup: SetupState | null | undefined): { done: number; total: number; required: number } {
  if (!setup) return { done: 0, total: STEP_ORDER.length, required: STEP_ORDER.length };
  const done = setup.steps.filter((s) => s.state === "done").length;
  const required = setup.steps.filter((s) => s.state !== "optional").length;
  return { done, total: setup.steps.length, required };
}

/** Minutes left for the steps still to do, for "about N minutes". */
export function minutesLeft(setup: SetupState | null | undefined): number {
  if (!setup) return STEP_ORDER.reduce((a, id) => a + STEP_META[id].minutes, 0);
  return setup.steps.filter((s) => s.state === "todo" || s.state === "warn").reduce((a, s) => a + STEP_META[s.id].minutes, 0);
}

// ------------------------------------------------------------------ calibration kinds
export type KindMeta = {
  title: string;
  /** What to do, in a sentence (the engine's `guidance` is shown as well). */
  how: string;
  needs: string;
  minLabel: string;
  /** Board distance range, mm. */
  distance: [number, number];
};

export const KIND_META: Record<CalibKind, KindMeta> = {
  stereo: {
    title: "Stereo calibration",
    how: "Hold the glued board in front of the scanner and move it around: every corner and edge of the image, tilted left, right, up and down, near and far.",
    needs: "The printed board, glued flat to glass. The emitters stay off.",
    minLabel: "views",
    distance: [200, 500],
  },
  lasers: {
    title: "Laser calibration",
    how: "Hold the board so both laser lines cross its white margin. Change the distance and the tilt between views.",
    needs: "The board and the stereo calibration. The line lasers switch on.",
    minLabel: "poses",
    distance: [150, 450],
  },
  imu: {
    title: "IMU calibration",
    how: "Put the board where the camera sees it. Hold the scanner still for 2 s, then rotate it slowly about all three axes.",
    needs: "The board in view and the stereo calibration.",
    minLabel: "recording",
    distance: [150, 600],
  },
  check: {
    title: "Calibration check",
    how: "Show the board with both laser lines on its margin, at 3 to 5 different distances. Nothing is changed: it only reports.",
    needs: "The board and a saved calibration. The line lasers switch on.",
    minLabel: "views",
    distance: [150, 450],
  },
};

export const GRADE_LABEL: Record<CalibGrade, string> = {
  excellent: "Excellent",
  good: "Good",
  usable: "Usable",
  poor: "Poor",
  failed: "Not yet",
};

export const GRADE_TONE: Record<CalibGrade, "ok" | "accent" | "warn" | "danger"> = {
  excellent: "ok",
  good: "ok",
  usable: "warn",
  poor: "danger",
  failed: "danger",
};

/**
 * A percentage that the engine may send as 0..100 (as `_pct` suggests) or as a 0..1 fraction.
 * `ref` is an independent estimate in percent (e.g. computed from the coverage grid): a value
 * of at most 1 while `ref` is clearly above 1 is read as a fraction.
 */
export function asPercent(v: number | null | undefined, ref?: number): number | null {
  if (v === null || v === undefined || !Number.isFinite(v)) return null;
  if (v > 1) return v;
  if (ref !== undefined) return ref > 1.5 ? v * 100 : v;
  return v * 100;
}

/** Coverage in percent from the grid itself: the share of cells with at least one view. */
export function gridPercent(cells: number[] | null | undefined): number {
  if (!cells || !cells.length) return 0;
  let n = 0;
  for (const c of cells) if (c > 0) n++;
  return (100 * n) / cells.length;
}

/**
 * Is a self-test job message an instruction for the user ("Press and release the trigger
 * now") rather than progress ("Measuring the frame rate…")? Instructions are shown big.
 */
export function isInstruction(message: string | null | undefined): boolean {
  if (!message) return false;
  const m = message.trim();
  return (
    /^(press|release|hold|keep|point|aim|cover|uncover|move|wave|rotate|tilt|look|place|put|show|turn|switch|tap|plug|unplug|watch)\b/i.test(m) ||
    /\bnow\b[.!…]*$/i.test(m) ||
    /\b(press|release) the trigger\b/i.test(m)
  );
}

export type CheckGroup = { key: string; title: string; checks: SelfTestCheck[] };

const GROUPS: { key: string; title: string; re: RegExp }[] = [
  { key: "camera", title: "Camera", re: /camera|image|fps|frame/i },
  { key: "pico", title: "Pico and IMU", re: /pico|firmware|imu|gyro|accel|clock|sync/i },
  { key: "emitters", title: "Lasers and projector", re: /laser|projector|emitter|dot/i },
  { key: "trigger", title: "Trigger", re: /trigger|button/i },
];

/** Self-test checks grouped by part, in a fixed order. */
export function groupChecks(checks: SelfTestCheck[]): CheckGroup[] {
  const out: CheckGroup[] = GROUPS.map((g) => ({ key: g.key, title: g.title, checks: [] }));
  const other: CheckGroup = { key: "other", title: "Other", checks: [] };
  for (const c of checks) {
    const i = GROUPS.findIndex((g) => g.re.test(c.name));
    (i >= 0 ? out[i] : other).checks.push(c);
  }
  return [...out, other].filter((g) => g.checks.length);
}

export const STATUS_RANK: Record<CheckStatus, number> = { FAIL: 0, WARN: 1, INFO: 2, SKIP: 3, PASS: 4 };

/** "camera fps" → "Camera fps", "imu values" → "IMU values". */
export function checkLabel(name: string): string {
  const s = name.replace(/[_-]+/g, " ").trim();
  const words = s.split(/\s+/).map((w) => (/^(imu|fps|led|usb|id)$/i.test(w) ? w.toUpperCase() : w));
  const t = words.join(" ");
  return t.charAt(0).toUpperCase() + t.slice(1);
}
