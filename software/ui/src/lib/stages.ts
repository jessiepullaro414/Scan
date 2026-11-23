/**
 * The names of the processing stages on the Edit mesh page (UX_PLAN U17, U49): "Follow the camera / Straighten /
 * Build the surface", and the two refinements; a trial build says so in its title. Pure; `quality.test.ts` checks it.
 */
import type { StageId, StageState } from "../api/types.ts";
import { fmtFixed, fmtInt } from "./format.ts";

/**
 * Whether a stage card of the scan's own mesh says "Running" (UX_PLAN R22): from its own state only (a stage run
 * alone), never from the job's kind or message. During "Make mesh again" the scan's stages stay as they are until the
 * new mesh takes their place (APP_API.md, R2); the run being built is shown beside them from `ProcessState.making`
 * (lib/making.ts `makingRows`), one stage running at a time.
 */
export function stageRunning(state: StageState): boolean {
  return state === "running";
}

/**
 * Straighten's line under a "Skipped" badge (R22): "Closed 48 loops" belongs to a straightened scan. Skipped with loops
 * found means they showed the passes already lined up (APP_API: `changed: false`); with the engine's own sentence
 * (`summary`) that one says why, so nothing more. Null where the card shows its usual lines.
 */
export function straightenSkippedLine(s: { state: StageState; loops: number | null; summary: string | null }): string | null {
  if (s.state !== "skipped" || !s.loops || s.loops <= 0 || s.summary) return null;
  return `Found ${fmtInt(s.loops)} loop${s.loops === 1 ? "" : "s"}, and they show the passes already line up: the scan was left as it is.`;
}

export const STAGE_TITLES: Record<StageId, { label: string; trial: boolean }> = {
  track: { label: "Follow the camera", trial: false },
  straighten: { label: "Straighten", trial: false },
  fuse: { label: "Build the surface", trial: false },
  spacetime: { label: "Refine from the images", trial: true },
  refine: { label: "Refine with lasers", trial: false },
};

/** "Refine from the images (trial)"; `short`: the name alone (where a "Trial" badge stands beside it). */
export function stageTitle(id: StageId, o: { short?: boolean } = {}): string {
  const t = STAGE_TITLES[id];
  if (!t) return String(id);
  return t.trial && !o.short ? `${t.label} (trial)` : t.label;
}

/** A piece of a stage card's line: `b`, a number shown in bold. */
export type LinePart = { t: string; b?: boolean };

/**
 * A done Straighten's line: "Closed 48 loops · moved the scan by 2.4 mm". When AprilTags held the scan (`tags`,
 * ENGINE_PLAN X8 step 2; missing from an older engine: as null) the move is the loops' and the tags' together, so the
 * line says so: "Closed 48 loops · 5 tags held the scan · moved the scan by 2.4 mm", or without loops "5 tags held the
 * scan · …". Null where the card shows its other lines (no loops and no tags, or not done).
 */
export function straightenDoneParts(s: { state: StageState; loops: number | null; tags?: number | null; correction_mm: number | null; correction_deg: number | null }): LinePart[] | null {
  const loops = s.loops ?? 0;
  const tags = typeof s.tags === "number" ? s.tags : null;
  if (s.state !== "done" || (loops <= 0 && tags === null)) return null;
  const out: LinePart[] = [];
  if (loops > 0) out.push({ t: "Closed " }, { t: fmtInt(loops), b: true }, { t: ` loop${loops === 1 ? "" : "s"}` });
  if (tags !== null) out.push({ t: out.length ? " · " : "" }, { t: fmtInt(tags), b: true }, { t: ` tag${tags === 1 ? "" : "s"} held the scan` });
  if (s.correction_mm !== null) {
    out.push({ t: " · moved the scan by " }, { t: `${fmtFixed(s.correction_mm, 1)}${String.fromCharCode(0x202f)}mm`, b: true });
    if (s.correction_deg !== null) out.push({ t: ` and ${fmtFixed(s.correction_deg, 2)}°` });
  }
  return out.filter((p) => p.t);
}

/** The same line as text. */
export function straightenDoneLine(s: Parameters<typeof straightenDoneParts>[0]): string | null {
  const p = straightenDoneParts(s);
  return p ? p.map((x) => x.t).join("") : null;
}

/** A job of the Edit mesh page: a stage alone, an edit or a recipe. */
export const isStudioJob = (kind: string): boolean => /^process_(track|straighten|fuse|spacetime|refine)|mesh_edit|recipe|^edit/i.test(kind);

/** A job's name in the Jobs panel: a stage's job by its card's name (`STAGE_TITLES`). */
export const jobKindLabel = (kind: string): string => {
  const k = kind.toLowerCase();
  if (isStudioJob(k)) {
    if (k.includes("track")) return STAGE_TITLES.track.label;
    if (k.includes("straighten")) return STAGE_TITLES.straighten.label;
    if (k.includes("fuse")) return STAGE_TITLES.fuse.label;
    if (k.includes("spacetime")) return STAGE_TITLES.spacetime.label;
    if (k.includes("refine")) return STAGE_TITLES.refine.label;
    if (k.includes("recipe")) return "Apply recipe";
    return "Mesh edit";
  }
  if (k === "align") return "Align and merge scans";
  if (k === "align_all") return "Align and merge scans";
  if (k === "merge") return "Merge scans";
  if (k === "reference_align") return "Align with the CAD model";
  if (k.includes("process") || k.includes("mesh")) return "Make mesh";
  if (k.includes("sim") || k.includes("demo")) return "Simulator";
  if (k.includes("selftest") || k.includes("self_test")) return "Self-test";
  if (k.includes("printable")) return "Print targets";
  if (k.includes("calib")) return "Calibration";
  return kind.replace(/[_-]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());
};
