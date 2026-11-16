/**
 * Aligning two scans (APP_API.md "M4 first slice"): the parts of the Align page that need no screen.
 * Which scans can be aligned or merged, the points picked on the two meshes and the pairs they form,
 * the pose as the 3D view takes it, and an alignment's figures in words.
 *
 * Pure (no React, no store, no three.js), so `npm test` runs it with Node.
 */
import type { AlignResult, MergeResult, PointPair, Scan } from "../api/types";

export type Vec3 = [number, number, number];

/** The points picked so far on each mesh, in the order they were clicked (each in its scan's own frame, mm). */
export type Picks = { target: Vec3[]; source: Vec3[] };
export type Side = keyof Picks;

/** An alignment from picked points needs this many complete pairs. */
export const MIN_PAIRS = 3;
/** Picked pairs that agree worse than this (mm) probably hold a pair that does not match. */
export const PAIRS_WARN_MM = 5;

// ------------------------------------------------------------------ scans
/** The scan has a mesh: it can be aligned. */
export const hasMesh = (s: Pick<Scan, "status" | "result">): boolean => s.status === "processed" && !!s.result;

/** A merged scan has no recording of its own: no new mesh from the frames, and no merging again. */
export const hasRecording = (s: Pick<Scan, "merged_from">): boolean => !s.merged_from?.length;

/** "Merged from Bracket top + Bracket underside", or null for an ordinary scan. */
export function mergedFromText(s: Pick<Scan, "merged_from">): string | null {
  const from = s.merged_from;
  return from?.length ? `Merged from ${from.map((x) => x.name).join(" + ")}` : null;
}

/**
 * The two scans to start with: the asked ones where they have a mesh and differ, else the first that
 * are free (`ids`: the scans with a mesh, in the project's order). Null where the project has none left.
 */
export function chooseScans(ids: readonly string[], target?: string | null, source?: string | null): { target: string | null; source: string | null } {
  let t = target && ids.includes(target) ? target : null;
  let s = source && source !== t && ids.includes(source) ? source : null;
  if (!t) t = ids.find((id) => id !== s) ?? null;
  if (!s) s = ids.find((id) => id !== t) ?? null;
  return { target: t, source: s };
}

/** The name a merged scan gets when none is typed (the server's default). */
export const defaultMergeName = (targetName: string, sourceName: string): string => `${targetName} + ${sourceName}`;

// ------------------------------------------------------------------ picked points
export type PairRow = { n: number; target: Vec3 | null; source: Vec3 | null };

/** The picks as numbered rows: point 1 on one mesh goes with point 1 on the other, and so on. */
export function pairRows(picks: Picks): PairRow[] {
  const n = Math.max(picks.target.length, picks.source.length);
  const rows: PairRow[] = [];
  for (let i = 0; i < n; i++) rows.push({ n: i + 1, target: picks.target[i] ?? null, source: picks.source[i] ?? null });
  return rows;
}

/** The complete pairs (a point on both meshes), in order: what the API takes. */
export function completePairs(picks: Picks): PointPair[] {
  const n = Math.min(picks.target.length, picks.source.length);
  const pairs: PointPair[] = [];
  for (let i = 0; i < n; i++) pairs.push({ source: picks.source[i], target: picks.target[i] });
  return pairs;
}

export const addPick = (picks: Picks, side: Side, p: Vec3): Picks => ({ ...picks, [side]: [...picks[side], p] });

/** Without row `index` (0-based): its point on each mesh that has one. The later points move up a number. */
export function removePair(picks: Picks, index: number): Picks {
  const drop = (list: Vec3[]) => (index >= 0 && index < list.length ? [...list.slice(0, index), ...list.slice(index + 1)] : list);
  return { target: drop(picks.target), source: drop(picks.source) };
}

/** What to click next, in words. `names`: the two scans' names. */
export function pickHint(picks: Picks, names: { target: string; source: string }): string {
  const t = picks.target.length,
    s = picks.source.length;
  if (t !== s) {
    const behind = t < s ? names.target : names.source;
    return `Point ${Math.min(t, s) + 1}: now click the same spot on “${behind}”`;
  }
  if (t === 0) return "Click a spot you can find on both scans (a corner, a hole, an edge), first on one, then on the other";
  if (t < MIN_PAIRS) return `${t} of ${MIN_PAIRS} points: click another spot on both scans, away from the others`;
  return `${t} points: enough to align. More points, spread over the part, make a better start`;
}

// ------------------------------------------------------------------ the pose
/**
 * `T` (4x4, row-major rows) as 16 numbers, row-major, for `matrixFromRowMajor` (three/coords.ts); null
 * when it is not a 4x4 matrix of finite numbers.
 */
export function flattenPose(T: unknown): number[] | null {
  if (!Array.isArray(T) || T.length !== 4) return null;
  const out: number[] = [];
  for (const row of T) {
    if (!Array.isArray(row) || row.length !== 4) return null;
    for (const x of row) {
      if (typeof x !== "number" || !Number.isFinite(x)) return null;
      out.push(x);
    }
  }
  return out;
}

/** A point of the source's mesh in the target's frame: `T` (16 numbers, row-major) applied to `p`. */
export function applyPose(T: ArrayLike<number>, p: Vec3): Vec3 {
  return [
    T[0] * p[0] + T[1] * p[1] + T[2] * p[2] + T[3],
    T[4] * p[0] + T[5] * p[1] + T[6] * p[2] + T[7],
    T[8] * p[0] + T[9] * p[1] + T[10] * p[2] + T[11],
  ];
}

// ------------------------------------------------------------------ results
export function isAlignResult(r: unknown): r is AlignResult {
  const x = r as Partial<AlignResult> | null;
  return !!x && typeof x === "object" && typeof x.accepted === "boolean" && typeof x.verdict === "string" && flattenPose(x.T) !== null;
}

export function isMergeResult(r: unknown): r is MergeResult {
  const x = r as Partial<MergeResult> | null;
  return !!x && typeof x === "object" && typeof x.project_id === "string" && typeof x.scan_id === "string";
}

/** The picked pairs agree badly: one of them probably does not match. */
export const pairsDisagree = (r: Pick<AlignResult, "pairs_rms_mm">): boolean => typeof r.pairs_rms_mm === "number" && r.pairs_rms_mm > PAIRS_WARN_MM;

export type Figure = { label: string; value: string; note?: string; warn?: boolean };

const mm = (v: number, digits = 2) => `${v.toFixed(digits)} mm`;

/** An alignment's figures for the small list under its verdict. */
export function alignFigures(r: AlignResult): Figure[] {
  const out: Figure[] = [
    { label: "Overlap", value: `${Math.round(100 * r.overlap)} %`, note: "of the smaller scan lies within 1 mm of the other" },
    { label: "Distance apart", value: typeof r.rmse_mm === "number" ? `${mm(r.rmse_mm)} RMS` : "–", note: "where the two overlap" },
    r.start === "given"
      ? { label: "Start", value: "From your points" }
      : { label: "Starting guesses", value: `${r.agree} of ${r.candidates} agree`, note: "each one starts from a different rough fit" },
  ];
  if (typeof r.pairs_rms_mm === "number") out.push({ label: "Your points", value: `agree to ${mm(r.pairs_rms_mm, 1)}`, warn: pairsDisagree(r) });
  if (r.same_setup) out.push({ label: "Shared table", value: r.used_support ? "Used for height and tilt" : "Not used: the two scans' tables did not line up" });
  return out;
}
