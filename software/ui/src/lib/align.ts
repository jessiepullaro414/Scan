/**
 * Aligning two scans (APP_API.md "M4 first slice") or several (the second slice): the parts of the
 * Align page that need no screen. Which scans can be aligned or merged, the list of scans, the points
 * picked on two meshes at a time and the pairs they form, the pose as the 3D view takes it, and an
 * alignment's figures in words.
 *
 * Pure (no React, no store, no three.js), so `npm test` runs it with Node.
 */
import type { AlignAllPair, AlignAllResult, AlignResult, MergeResult, PairUse, PickedPoints, PointPair, Scan, TagStart } from "../api/types";

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

type RecordingFields = Pick<Scan, "merged_from" | "keep" | "has_recording">;

/** Why the scan has no frames to merge, in the engine's order (`ohs/server/aligning.py`), or null when it has them. */
function noRecordingKind(s: RecordingFields): "result_only" | "merged" | "missing" | null {
  if (s.keep === "standard") return "result_only"; // R2: removed to free space
  if (s.merged_from?.length) return "merged";
  if (s.has_recording === false) return "missing";
  return null;
}

const MARK = { result_only: " (result only)", merged: " (merged)", missing: " (no recording)" } as const;

/** The mark after a scan's name in the Align page's lists: "" for a scan that can be merged. */
export function recordingMark(s: RecordingFields): string {
  const k = noRecordingKind(s);
  return k ? MARK[k] : "";
}

/**
 * Why the scan cannot be merged (it can still be aligned), said before a merge is started, or null. `several`: the
 * page with three or more scans, where the scan can be taken off the list.
 */
export function cannotMerge(s: RecordingFields & { name?: string }, several: boolean): { title: string; text: string } | null {
  const k = noRecordingKind(s);
  if (!k) return null;
  const name = s.name ?? "This scan";
  const tail = several
    ? "You can align to it and compare the scans here; to merge, remove it from the list or choose scans that"
    : "You can align to it and compare the two here; to merge, choose two scans that";
  if (k === "merged")
    return { title: `“${name}” cannot be merged again`, text: `It is a merged scan and has no recording of its own. ${tail} were recorded.` };
  const why = k === "result_only" ? "Its recording was removed to free space (the scan keeps its result only)." : "Its recording is not on disk.";
  return { title: `“${name}” cannot be merged`, text: `${why} ${tail} still have their recordings.` };
}

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
export const defaultMergeName = (targetName: string, sourceName: string): string => mergeNameOf([targetName, sourceName]);

/** The name a merge of several scans gets when none is typed: their names joined by " + " (the server's default). */
export const mergeNameOf = (names: readonly string[]): string => names.join(" + ");

// ------------------------------------------------------------------ several scans (M4 second slice)
/** At most this many scans are aligned at once. */
export const MAX_SCANS = 8;

/**
 * The scans to start with: the two asked as `target` and `source` (as `chooseScans` does), then the
 * further asked ones (`more`) that have a mesh, each once, at most `MAX_SCANS` in all.
 */
export function chooseList(ids: readonly string[], target?: string | null, source?: string | null, more: readonly string[] = []): string[] {
  const c = chooseScans(ids, target, source);
  const list = [c.target, c.source].filter((x): x is string => !!x);
  for (const id of more) if (list.length < MAX_SCANS && ids.includes(id) && !list.includes(id)) list.push(id);
  return list;
}

/** The first scan with a mesh that is not in the list yet, or null when none is left (or the list is full). */
export function nextFreeScan(ids: readonly string[], list: readonly string[]): string | null {
  if (list.length >= MAX_SCANS) return null;
  return ids.find((id) => !list.includes(id)) ?? null;
}

/** The list with scan `id` at place `index`; a scan already in the list changes places with the one there. */
export function chooseAt(list: readonly string[], index: number, id: string): string[] {
  const next = [...list];
  if (index < 0 || index >= next.length) return next;
  const at = next.indexOf(id);
  if (at >= 0) next[at] = next[index];
  next[index] = id;
  return next;
}

/** The list without place `index`; two scans always stay. */
export const removeAt = (list: readonly string[], index: number): string[] => (list.length > 2 && index >= 0 && index < list.length ? list.filter((_, i) => i !== index) : [...list]);

/** Two lists hold the same scans in the same order. */
export const sameList = (a: readonly string[], b: readonly string[]): boolean => a.length === b.length && a.every((x, i) => x === b[i]);

/** Every pair of the list, the earlier scan first (it is shown on the left, its points are the pair's `target`). */
export function pairsOf(list: readonly string[]): [string, string][] {
  const out: [string, string][] = [];
  for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) out.push([list[i], list[j]]);
  return out;
}

/** Names in a sentence: “A”, “A” and “B”, “A”, “B” and “C”. */
export function namesText(names: readonly string[]): string {
  const q = names.map((n) => `“${n}”`);
  return q.length <= 1 ? (q[0] ?? "") : `${q.slice(0, -1).join(", ")} and ${q[q.length - 1]}`;
}

/**
 * A fixed palette of clearly different colours, one per place in the list: the first two are the
 * two-scan page's (the one that stays, the one that is moved).
 */
export const SCAN_PALETTE = ["#3f83f0", "#f08c2e", "#2fae66", "#a35ee8", "#e0475b", "#19b4c8", "#d4b21c", "#e35fb7"] as const;
export const scanColor = (index: number): string => SCAN_PALETTE[((index % SCAN_PALETTE.length) + SCAN_PALETTE.length) % SCAN_PALETTE.length];

// ------------------------------------------------------------------ picked points of several pairs
/** The points picked between two scans: `picks.target` on scan `a`, `picks.source` on scan `b`. */
export type PairPicks = { a: string; b: string; picks: Picks };
/** All picked points of the page, by pair of scans (`pairKey`). */
export type PickBook = Record<string, PairPicks>;

/** The same key for (x, y) and (y, x). */
export const pairKey = (x: string, y: string): string => (x < y ? `${x}\n${y}` : `${y}\n${x}`);

const flip = (p: Picks): Picks => ({ target: p.source, source: p.target });
/** No points yet (one shared value: the views it is handed to need not draw again). Never changed: the helpers make new lists. */
const NO_PICKS: Picks = Object.freeze({ target: Object.freeze([]) as unknown as Vec3[], source: Object.freeze([]) as unknown as Vec3[] });

/** The points of the pair with `a` as the target side and `b` as the source side, however they were stored. */
export function picksFor(book: PickBook, a: string, b: string): Picks {
  const e = book[pairKey(a, b)];
  if (!e) return NO_PICKS;
  return e.a === a ? e.picks : flip(e.picks);
}

/** The book with the pair's points set (`picks.target` on `a`); an empty pair is dropped. */
export function withPicks(book: PickBook, a: string, b: string, picks: Picks): PickBook {
  const next = { ...book };
  const k = pairKey(a, b);
  if (!picks.target.length && !picks.source.length) delete next[k];
  else next[k] = { a, b, picks };
  return next;
}

/** Only the pairs of scans that are still in the list. */
export function keepPicksOf(book: PickBook, list: readonly string[]): PickBook {
  const next: PickBook = {};
  for (const [k, e] of Object.entries(book)) if (list.includes(e.a) && list.includes(e.b)) next[k] = e;
  return next;
}

/** The picked points to send (`AlignAllRequest.picked`): each pair of the list with enough complete pairs. */
export function pickedPoints(book: PickBook, list: readonly string[]): PickedPoints[] {
  const out: PickedPoints[] = [];
  for (const [a, b] of pairsOf(list)) {
    const pairs = completePairs(picksFor(book, a, b));
    if (pairs.length >= MIN_PAIRS) out.push({ a, b, pairs });
  }
  return out;
}

/** Pairs of the list with some points, but too few complete pairs to be sent. */
export function unfinishedPairs(book: PickBook, list: readonly string[]): [string, string][] {
  return pairsOf(list).filter(([a, b]) => {
    const p = picksFor(book, a, b);
    return (p.target.length || p.source.length) && completePairs(p).length < MIN_PAIRS;
  });
}

/** The pair to pick on: the asked one when both are in the list (earlier scan first), else the first two. */
export function pickPairOf(list: readonly string[], asked: readonly [string, string] | null): [string, string] | null {
  if (asked) {
    const i = list.indexOf(asked[0]),
      j = list.indexOf(asked[1]);
    if (i >= 0 && j >= 0 && i !== j) return i < j ? [asked[0], asked[1]] : [asked[1], asked[0]];
  }
  return list.length >= 2 ? [list[0], list[1]] : null;
}

/** After a result with scans not placed: the pair to pick on next, an unplaced scan with the first placed one. */
export function suggestPickPair(r: Pick<AlignAllResult, "placed" | "unplaced">, list: readonly string[]): [string, string] | null {
  const lost = list.find((id) => r.unplaced.includes(id));
  const anchor = list.find((id) => r.placed.includes(id));
  return lost && anchor ? pickPairOf(list, [anchor, lost]) : null;
}

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

/**
 * The two answers to "Was the part moved between the scans?", in the page's words: what the merge then
 * does with each scan's table (APP_API.md, "M4 first slice", the merge).
 */
export const SETUP_TEXT = {
  same: "The table the scans share helps to line them up, and it stays in the merged scan.",
  moved:
    "Only the part itself is matched. Each scan's table is found on its own mesh and left out of the merged scan; the part is kept right down to the table, and thin parts lying flat on it are kept. A part thinner than the scan can tell from its table (about 0.2\u202fmm) goes with the table; the merge says what it left out.",
} as const;

/** What a merge left out of each scan's table, and what to look at: one line per scan ("“Top”: …"), and the warnings. */
export function mergeLeftOut(r: Pick<MergeResult, "scan_id" | "tables" | "warnings" | "cleaning">): { lines: string[]; warnings: string[]; warn: boolean } {
  const lines = (r.tables ?? []).map((t) => `“${t.name}”: ${t.summary}`);
  if (lines.length && r.cleaning) lines.push(`The merged scan: ${r.cleaning}`);
  const warnings = [...(r.warnings ?? [])];
  return { lines, warnings, warn: warnings.length > 0 };
}

/** The picked pairs agree badly: one of them probably does not match. */
export const pairsDisagree = (r: Pick<AlignResult, "pairs_rms_mm">): boolean => typeof r.pairs_rms_mm === "number" && r.pairs_rms_mm > PAIRS_WARN_MM;

export type Figure = { label: string; value: string; note?: string; warn?: boolean };

const mm = (v: number, digits = 2) => `${v.toFixed(digits)} mm`;

/** Which tags a start from tags used, e.g. "tags 2, 4 and 5 seen by both scans; tag 3 left out (moved?)". */
export function tagsNote(t: TagStart | null | undefined): string | undefined {
  if (!t || !t.used.length) return undefined;
  const list = (ids: number[]) => (ids.length === 1 ? `${ids[0]}` : `${ids.slice(0, -1).join(", ")} and ${ids[ids.length - 1]}`);
  const used = `tag${t.used.length > 1 ? "s" : ""} ${list(t.used)} seen by both scans`;
  return t.left_out.length ? `${used}; tag${t.left_out.length > 1 ? "s" : ""} ${list(t.left_out)} left out (moved?)` : used;
}

/** An alignment's figures for the small list under its verdict. */
export function alignFigures(r: AlignResult): Figure[] {
  const out: Figure[] = [
    { label: "Overlap", value: `${Math.round(100 * r.overlap)}\u202f%`, note: "of the smaller scan lies within 1\u202fmm of the other" },
    // U36: "typically 0.25 mm where they overlap"; the help in brackets, apart from the count
    { label: "Distance apart", value: typeof r.rmse_mm === "number" ? `typically ${mm(r.rmse_mm)}` : "–", note: "where they overlap" },
    r.start === "given"
      ? { label: "Start", value: "From your points" }
      : r.start === "tags"
        ? { label: "Start", value: "From the tags", note: tagsNote(r.tags) }
        : { label: "Starting guesses", value: `${r.agree} of ${r.candidates} agree`, note: "(each starts from a different rough fit)" },
  ];
  if (typeof r.pairs_rms_mm === "number") out.push({ label: "Your points", value: `agree to ${mm(r.pairs_rms_mm, 1)}`, warn: pairsDisagree(r) });
  if (r.same_setup) out.push({ label: "Shared table", value: r.used_support ? "Used for height and tilt" : "Not used: the two scans' tables did not line up" });
  return out;
}

// ------------------------------------------------------------------ results of several scans
/** A link (a pair used to place the scans) that disagrees by more than this (mm) asks for a look before merging. */
export const LINK_WARN_MM = 2;

export function isAlignAllResult(r: unknown): r is AlignAllResult {
  const x = r as Partial<AlignAllResult> | null;
  return (
    !!x &&
    typeof x === "object" &&
    typeof x.accepted === "boolean" &&
    typeof x.verdict === "string" &&
    Array.isArray(x.scans) &&
    Array.isArray(x.placed) &&
    Array.isArray(x.unplaced) &&
    Array.isArray(x.pairs) &&
    !!x.T &&
    typeof x.T === "object" &&
    !Array.isArray(x.T)
  );
}

/** A placed scan's pose (16 numbers, row-major) into the first scan's frame; null when it is not placed. */
export function poseOf(r: Pick<AlignAllResult, "T" | "placed">, id: string): number[] | null {
  return r.placed.includes(id) ? flattenPose(r.T[id]) : null;
}

/** How the placement used a pair, in words. */
export function pairUseText(used: PairUse): string {
  if (used === "chain") return "in the chain";
  if (used === "loop") return "checks the chain";
  if (used === "left out") return "left out";
  return "not used";
}

/** One pair in a short line: “with “Top”: accepted, 76 % overlap, in the chain”. `other`: the other scan's name. */
export function pairLine(p: AlignAllPair, other: string): string {
  const parts = [`with “${other}”: ${p.accepted ? "accepted" : "not accepted"}`, `${Math.round(100 * p.overlap)}\u202f% overlap`, pairUseText(p.used)];
  if (typeof p.disagree_mm === "number" && (p.used === "loop" || p.used === "left out")) parts.push(`disagrees by ${p.disagree_mm.toFixed(2)}\u202fmm`);
  return parts.join(", ");
}

export type ScanPlacement = {
  id: string;
  /** Its place in the list (0: it stays in place). */
  index: number;
  placed: boolean;
  /** The pairs it makes with the scans before it in the list (each pair is shown once, under its later scan). */
  pairs: { pair: AlignAllPair; other: string }[];
};

/** Each scan of the list: placed or not, and its pairs with the scans before it. */
export function scanPlacements(r: Pick<AlignAllResult, "placed" | "pairs">, list: readonly string[]): ScanPlacement[] {
  return list.map((id, index) => {
    const pairs: ScanPlacement["pairs"] = [];
    for (const p of r.pairs) {
      const other = p.a === id ? p.b : p.b === id ? p.a : null;
      if (other === null || other === id) continue;
      const j = list.indexOf(other);
      if (j >= 0 && j < index) pairs.push({ pair: p, other });
    }
    pairs.sort((x, y) => list.indexOf(x.other) - list.indexOf(y.other));
    return { id, index, placed: index === 0 || r.placed.includes(id), pairs };
  });
}

/** The figures under the verdict of several scans. */
export function alignAllFigures(r: AlignAllResult): Figure[] {
  const n = r.scans.length;
  const out: Figure[] = [{ label: "Placed", value: `${r.placed.length} of ${n} scans`, warn: r.unplaced.length > 0 }];
  const checked = r.pairs.filter((p) => typeof p.disagree_mm === "number" && p.used !== "");
  if (r.loops > 0 && checked.length) // a chain alone agrees with itself: nothing to show
    out.push({ label: "Worst disagreement", value: `${r.worst_link_mm.toFixed(2)}\u202fmm`, note: "between a pair and where the scans were placed", warn: needsALook(r) });
  const leftOut = r.pairs.filter((p) => p.used === "left out").length;
  if (leftOut) out.push({ label: "Left out", value: `${leftOut} pair${leftOut === 1 ? "" : "s"}`, note: "that did not fit the others" });
  if (r.same_setup) {
    const found = r.scans.filter((id) => r.support[id]).length;
    out.push({ label: "Shared table", value: `Found in ${found} of ${n} scans` });
  }
  return out;
}

/** A pair disagrees so much with where the scans were placed that they should be looked at before merging. */
export const needsALook = (r: Pick<AlignAllResult, "worst_link_mm">): boolean => typeof r.worst_link_mm === "number" && r.worst_link_mm > LINK_WARN_MM;

/** Merging several scans asks to confirm: not every scan placed, or a pair disagrees by more than 2 mm. */
export const mergeNeedsConfirm = (r: Pick<AlignAllResult, "accepted" | "worst_link_mm">): boolean => !r.accepted || needsALook(r);

/** The merge of several scans, as the API takes it: every scan at its pose, or null when one is not placed. */
export function mergeScansOf(r: Pick<AlignAllResult, "T" | "placed">, list: readonly string[]): { id: string; T: number[][] }[] | null {
  const out: { id: string; T: number[][] }[] = [];
  for (const id of list) {
    const T = r.T[id];
    if (!r.placed.includes(id) || !T || flattenPose(T) === null) return null;
    out.push({ id, T });
  }
  return out;
}
