/**
 * A second pass that joins the first (FEATURE_PLAN N2, APP_API.md "A second pass"), the pure part: what
 * "Scan these areas" keeps until Start, what Start sends, the steps after Stop in words, and the before and
 * after.
 *
 * Imports nothing at run time but `./check.ts` and `./checkWhy.ts` (both pure), so `npm test` checks it with
 * plain node.
 */
import type {
  CheckArea,
  CheckPlanPose,
  CheckResult,
  CheckWhy,
  PassAreaState,
  PassChain,
  PassChainState,
  PassCompare,
  PassLink,
  PassStart,
  PassStep,
  PresetValues,
  Scan,
} from "../api/types";
import { fmtAreaSize, fmtShare, fmtTolerance } from "./check.ts";
import { planWords, poseShort } from "./checkWhy.ts";

/** Said wherever a pass is set up or its result shown (the engine's sentence, as a fallback). */
export const MERGE_NOTE =
  "A merge adds coverage: it fills in surface the first scan did not measure well enough. It does not yet make the surface the first scan already saw well more accurate: where both scans saw a place, the two are averaged.";

/** Why the reference picture is not lined up with the live model, in one sentence. */
export const REFERENCE_NOTE =
  "A picture of the first scan to go by, with the areas to scan lit. The live preview is not lined up with it while you scan: each scan starts its own tracking, and the two are aligned after Stop.";

/** The places to scan from a pass lists: the plan's, then a few areas' own. */
export const MAX_PLACES = 6;

/** One area asked for: its place in the Check answer and its faces on the first mesh (to light them). */
export type PendingArea = { index: number; kind: CheckArea["kind"]; area_mm2: number; center: [number, number, number]; faces: string };
/** A place to scan from, with its sentence. */
export type PendingPose = NonNullable<PassStart["poses"]>[number];

/** What "Scan these areas" keeps until Start (the Scan page shows it, Start sends it). */
export type PendingPass = {
  projectId: string;
  firstId: string;
  firstName: string;
  meshUrl: string;
  tolerance: number;
  version: number;
  areas: PendingArea[];
  poses: PendingPose[];
  /** The first scan's preset, so the pass takes the same one unless the user picks another. */
  preset: { id: string; hand: PresetValues } | null;
  /** The reasons (and so the places) were not there yet when the pass was set up: they are being fetched. */
  placesPending?: boolean;
  /** Fetching them failed. */
  placesFailed?: boolean;
};

/** What to say about the places to scan from when none is listed; null when some are. */
export function placesWords(p: Pick<PendingPass, "poses" | "placesPending" | "placesFailed">): string | null {
  if (p.poses.length) return null;
  if (p.placesPending) return "The places to scan from are still being worked out…";
  if (p.placesFailed) return "The places to scan from could not be worked out this time: scan the lit areas from where they can be seen squarely.";
  return "No place to scan from was found for these areas: scan the lit areas from where they can be seen squarely.";
}

/** The areas and places "Scan these areas" takes: every listed area, and the plan's places. */
export function pendingFromCheck(args: {
  projectId: string;
  scan: Pick<Scan, "id" | "name" | "preset" | "result">;
  result: CheckResult;
  why: CheckWhy | null;
  /** Only these areas (places in the answer); default every listed one. */
  only?: number[];
  /** The places to scan from; default the plan's. */
  plan?: CheckPlanPose[];
}): PendingPass {
  const { projectId, scan, result, why } = args;
  const idx = args.only ?? result.areas.map((_, i) => i);
  const plan = args.plan ?? why?.plan ?? [];
  const poses: PendingPose[] = plan.map((p) => ({ area: p.area, kind: p.kind, text: planWords(p), pose: p.pose }));
  // an area the plan's places do not see, with a place of its own: that one too (the largest first, a few in all)
  const seen = new Set<number>();
  for (const p of plan) {
    seen.add(p.area);
    for (const s of p.also_sees) if (s.share >= 0.5) seen.add(s.area);
  }
  if (why && !args.plan)
    for (const i of idx) {
      const w = why.areas[i];
      if (poses.length >= MAX_PLACES) break;
      // U23: the same sentence form as the plan's places ("Area 2: about 250 mm away, straight on. It sees 95 % of it.")
      if (w?.view.ok && w.view.pose && !seen.has(i))
        poses.push({ area: i, kind: "area", text: `Area ${i + 1}: ${poseShort(w.view.pose)}.${typeof w.view.share_seen === "number" ? ` It sees ${fmtShare(w.view.share_seen)} of it.` : ""}`, pose: w.view.pose });
    }
  return {
    projectId,
    firstId: scan.id,
    firstName: scan.name,
    meshUrl: scan.result?.mesh_url ?? "",
    tolerance: result.tolerance_mm,
    version: result.version,
    areas: idx
      .filter((i) => i >= 0 && i < result.areas.length)
      .map((i) => ({ index: i, kind: result.areas[i].kind, area_mm2: result.areas[i].area_mm2, center: result.areas[i].center, faces: result.areas[i].faces })),
    poses: poses.slice(0, MAX_PLACES),
    preset: scan.preset ? { id: scan.preset.id, hand: { ...(scan.preset.hand ?? {}) } } : null,
    placesPending: !why && !args.plan,
  };
}

/**
 * `continues` of `POST /api/scan/start`. `moved`: the Scan page's switch "The part was moved since the first
 * scan"; the engine asks the opposite, `same_setup` (found in review: the switch was sent as it stood, so a
 * part that was not moved was aligned and merged as a moved one, and its table left out of the merge).
 */
export function passStartBody(p: PendingPass, moved: boolean): PassStart {
  return { scan_id: p.firstId, tolerance_mm: p.tolerance, version: p.version, areas: p.areas.map((a) => a.index), poses: p.poses, same_setup: !moved };
}

/**
 * The preset fields Start sends for a pass: none when they are the first scan's (the engine then takes the
 * first scan's preset and says so), else what the picker has.
 */
export function passPresetFields(picked: { preset: string; preset_values?: PresetValues }, first: PendingPass["preset"], generalId = "general"): { preset?: string; preset_values?: PresetValues } {
  const firstId = first?.id ?? generalId;
  const same = picked.preset === firstId && sameValues(picked.preset_values ?? {}, first?.hand ?? {});
  return same ? {} : picked;
}

function sameValues(a: PresetValues, b: PresetValues): boolean {
  const ka = Object.keys(a).filter((k) => k !== "mode" && (a as Record<string, unknown>)[k] !== undefined);
  const kb = Object.keys(b).filter((k) => k !== "mode" && (b as Record<string, unknown>)[k] !== undefined);
  return ka.length === kb.length && ka.every((k) => (a as Record<string, unknown>)[k] === (b as Record<string, unknown>)[k]);
}

/** "2 areas, 1.4 cm² in all, at ± 0.3 mm". */
export function areasWords(areas: readonly Pick<PendingArea, "area_mm2">[], tolerance: number): string {
  const tot = areas.reduce((s, a) => s + a.area_mm2, 0);
  if (!areas.length) return `No area picked: the pass adds whatever it sees (checked at ± ${fmtTolerance(tolerance)}\u202fmm).`;
  return `${areas.length === 1 ? "1 area" : `${areas.length} areas`}, ${fmtAreaSize(tot)} in all, at ± ${fmtTolerance(tolerance)}\u202fmm.`;
}

/** A place to scan from in words: its own sentence, else the distance and angle. */
export function poseWords(p: PendingPose | PassLink["poses"][number]): string {
  if (p.text) return p.text;
  const q = p.pose as { distance_mm?: number; tilt_deg?: number };
  return typeof q.distance_mm === "number" && typeof q.tilt_deg === "number" ? `Hold the scanner ${poseShort({ distance_mm: q.distance_mm, tilt_deg: q.tilt_deg })}.` : "A place the Scan check suggested.";
}

// ---------------------------------------------------------------- the steps after Stop
export const STEP_LABEL: Record<PassStep, string> = {
  process: "Make the pass's mesh",
  align: "Align it to the first scan",
  merge: "Merge the two into a new scan",
};
export type StepRow = { step: PassStep; label: string; state: "done" | "running" | "waiting" | "stopped" | "skipped" };

export const chainActive = (s: PassChainState | undefined | null): boolean => s === "processing" || s === "aligning" || s === "merging" || s === "waiting";
/** The chain can be run on from where it stopped (POST .../pass/run). */
export const chainCanContinue = (s: PassChainState | undefined | null): boolean => s === "cancelled" || s === "failed" || s === "not_aligned" || s === "not_merged";

const ORDER: PassStep[] = ["process", "align", "merge"];
const RUNNING: Partial<Record<PassChainState, PassStep>> = { processing: "process", aligning: "align", merging: "merge" };

/** Each step's state, for the list. */
export function chainRows(chain: Pick<PassChain, "state" | "step">): StepRow[] {
  const st = chain.state;
  const at = RUNNING[st] ?? chain.step ?? null;
  const k = at ? ORDER.indexOf(at) : -1;
  return ORDER.map((step, i) => {
    let state: StepRow["state"];
    if (st === "merged") state = "done";
    else if (st === "waiting") state = "waiting";
    else if (i < k) state = "done";
    else if (i > k) state = st === "not_aligned" || st === "not_merged" ? "skipped" : "waiting";
    else if (RUNNING[st]) state = "running";
    else if (st === "not_merged") state = "done"; // aligned; the merge could not be made
    else if (st === "not_aligned") state = "stopped"; // the alignment ran and was not accepted
    else state = "stopped";
    return { step, label: STEP_LABEL[step], state };
  });
}

/** The chain's title, one line. */
export function chainTitle(state: PassChainState): string {
  return (
    {
      waiting: "Second pass: waiting for Stop",
      processing: "Second pass: making its mesh…",
      aligning: "Second pass: aligning it to the first scan…",
      merging: "Second pass: merging the two…",
      merged: "Second pass merged",
      not_aligned: "Second pass not merged: the alignment was not accepted",
      not_merged: "Second pass aligned, not merged",
      failed: "Second pass stopped: a step failed",
      cancelled: "Second pass stopped: cancelled",
    } as Record<PassChainState, string>
  )[state];
}

// ---------------------------------------------------------------- before and after
export const AREA_STATE_WORDS: Record<PassAreaState, string> = {
  passes: "passes now",
  partly: "partly passes",
  weak: "still misses the tolerance",
  not_found: "not in the merged scan",
  unknown: "can't be told (no confidence)",
};
/** The same for several areas counted together ("46 partly pass"). */
export const AREA_STATE_WORDS_MANY: Record<PassAreaState, string> = {
  passes: "pass now",
  partly: "partly pass",
  weak: "still miss the tolerance",
  not_found: "not in the merged scan",
  unknown: "can't be told (no confidence)",
};

/** One asked area after the merge: "Area 2 (1.4 cm²): passes now (96 % of it)". */
export function areaAfterWords(a: PassCompare["areas"][number]): string {
  const head = `Area ${a.index + 1} (${fmtAreaSize(a.area_mm2)})`;
  if (a.state === "not_found" || a.state === "unknown" || a.share_ok === null) return `${head}: ${AREA_STATE_WORDS[a.state]}.`;
  return `${head}: ${AREA_STATE_WORDS[a.state]} (${fmtShare(a.share_ok)} of it).`;
}

/** The two rows of the before and after: label, share, areas. */
export function compareRows(c: PassCompare): { label: string; name: string; share: string; areas: string }[] {
  const row = (label: string, s: PassCompare["first"]) => ({
    label,
    name: s.name,
    share: s.share_ok === null ? "–" : fmtShare(s.share_ok),
    areas: s.n_areas === 1 ? "1 area" : `${s.n_areas} areas`,
  });
  // each scan's whole mesh: of different surfaces when the merge holds less or more than the first scan
  return c.common ? [row("Before, whole mesh", c.first), row("After, whole mesh", c.merged)] : [row("Before", c.first), row("After", c.merged)];
}

const change = (a: number, b: number): string => {
  const pts = (b - a) * 100;
  return Math.abs(pts) < 0.5 ? "stayed about the same" : pts > 0 ? `rose by ${Math.round(pts)} points` : `fell by ${Math.round(-pts)} points`;
};

/**
 * The change in one sentence. With the engine's `common` (found in review 2026-11-20): on the surface both scans
 * hold, so surface the merge left out (each scan's table when the part was moved) never reads as "fell by". An
 * older engine's answer has only the whole meshes: then a fall is not called one, since it can come from missing
 * surface.
 */
export function compareSentence(c: PassCompare): string {
  const tol = `± ${fmtTolerance(c.tolerance_mm)}\u202fmm`;
  if (c.common) {
    const { before_ok: a, after_ok: b, found_share: f } = c.common;
    if (a === null || b === null) return `The scans carry no confidence, so the share that meets ${tol} can't be told.`;
    return `On the surface both scans hold (${fmtShare(f)} of the first scan's), the share that meets ${tol} ${change(a, b)}: ${fmtShare(a)} before, ${fmtShare(b)} after.`;
  }
  const a = c.first.share_ok;
  const b = c.merged.share_ok;
  if (a === null || b === null) return `The scans carry no confidence, so the share that meets ${tol} can't be told.`;
  if (b < a - 0.005)
    return `On the whole meshes the share that meets ${tol} is ${fmtShare(a)} before and ${fmtShare(b)} after; they need not hold the same surface, so this is not a loss.`;
  return `The share of the surface that meets ${tol} ${change(a, b)}: ${fmtShare(a)} before, ${fmtShare(b)} after.`;
}

/** What of the first scan's surface is not in the merged scan, and why when known; null for a sliver (under 0.5 %). */
export function missingSentence(c: PassCompare): string | null {
  const m = c.missing;
  if (!m || m.share < 0.005) return null;
  const head = `${fmtShare(m.share)} of the first scan's surface (${fmtAreaSize(m.area_mm2)}) is not in the merged scan`;
  return m.why ? `${head}: ${m.why}.` : `${head}.`;
}

/** The areas asked for after the merge: the first few named, the rest counted by what became of them. */
export const AREAS_NAMED = 4;
export function areaLines(areas: PassCompare["areas"], named = AREAS_NAMED): string[] {
  const out = areas.slice(0, named).map(areaAfterWords);
  const rest = areas.slice(named);
  if (rest.length) {
    const by = new Map<PassAreaState, number>();
    for (const a of rest) by.set(a.state, (by.get(a.state) ?? 0) + 1);
    // several areas: "46 partly pass", not "46 partly passes" (seen in review)
    out.push(`And ${rest.length} more: ${[...by].map(([s, n]) => `${n} ${n === 1 ? AREA_STATE_WORDS[s] : AREA_STATE_WORDS_MANY[s]}`).join(", ")}.`);
  }
  return out;
}

/** On the project page: "Second pass of “Top”, merged" (null for a scan that continues nothing). */
export function passOfText(s: Pick<Scan, "continues">): string | null {
  const c = s.continues;
  if (!c) return null;
  const tail: Partial<Record<PassChainState, string>> = {
    merged: "merged",
    not_aligned: "not merged (the alignment was not accepted)",
    not_merged: "aligned, not merged",
    failed: "stopped (a step failed)",
    cancelled: "stopped (cancelled)",
    processing: "its steps are running",
    aligning: "its steps are running",
    merging: "its steps are running",
  };
  const t = tail[c.chain?.state];
  return `Second pass of “${c.scan_name}”${t ? `, ${t}` : ""}`;
}

/**
 * While a scan records that is not the pass the Scan page has set up (the scanner's trigger always starts an
 * ordinary scan, never a pass), one plain sentence that says so and that the pass still waits; null otherwise.
 * `sessionLink`: the scan the recording session continues (`session.continues.scan_id`), null for an ordinary scan.
 */
export function passWaitingNote(pending: Pick<PendingPass, "firstId" | "firstName"> | null, live: boolean, sessionLink: string | null): string | null {
  if (!pending || !live || sessionLink === pending.firstId) return null;
  return `An ordinary scan was started, not the second pass of “${pending.firstName}”: the pass is still waiting, and Start on this page begins it once this scan is stopped.`;
}

/** The setup the pass is sent with, in words (as the engine says it back in `PassLink.setup_words`). */
export function setupWords(sameSetup: boolean): string {
  return sameSetup
    ? "The part was not moved since the first scan: the table helps to align the two, and the merge keeps it."
    : "The part was moved since the first scan: the alignment uses the part alone, and each scan's table is left out of the merge.";
}
