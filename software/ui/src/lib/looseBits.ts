/**
 * What "Remove loose bits" took (UX_PLAN U14; APP_API.md, `EditEntry.removed`): the history says how many pieces
 * went and how large the largest was (a thin or small part of the real part can go with the noise), and offers to
 * show them: the mesh before the edit as a ghost over the mesh after it (the Compare control's "Both").
 */
import type { EditEntry, ProcessState } from "../api/types";
import { fmtFixed, fmtInt, withUnit } from "./format.ts";

/** "3 loose bits removed, the largest 4.2 mm across"; null for an edit that removed nothing or says nothing. */
export function removedLine(r: EditEntry["removed"] | null | undefined): string | null {
  if (!r || !(r.pieces > 0)) return null;
  const size = withUnit(fmtFixed(r.largest_mm, 1), "mm");
  return r.pieces === 1 ? `1 loose bit removed, ${size} across` : `${r.pieces} loose bits removed, the largest ${size} across`;
}

/**
 * The history's line under a "Remove loose bits" entry (UX_PLAN R23): the entry's summary, which names the rule that
 * took the largest piece ("…, the largest 40.8 mm across but under 1 % of the mesh …"); the page's own words for an
 * entry without one; null when the edit removed nothing (or is another edit).
 */
export function historyRemovedLine(e: Pick<EditEntry, "removed" | "summary">): string | null {
  if (!e.removed || !(e.removed.pieces > 0)) return null;
  return e.summary?.trim() || removedLine(e.removed);
}

/**
 * The most triangles "Show what was removed" loads on top of the meshes on screen. Edit mesh already holds two
 * versions of the mesh (now and before, for Compare); the ghost and the mesh after the edit come on top, so a pair
 * of large meshes would run a browser short of memory. 6 million triangles is about 300 MB in the graphics card.
 */
export const GHOST_LIMIT_TRIANGLES = 6_000_000;

/**
 * "Show what was removed" for an edit at any time (UX_PLAN R23; APP_API.md `.../edits/{id}/before.ply`): the ghost is
 * the step before it (`step`, 0 = the starting mesh), beside the step after it, which is the mesh on screen when the
 * edit is the last applied one (`afterIsCurrent`). `triangles`: what loading them adds; `tooBig` above the limit.
 * Null for an edit that removed nothing, or one the history does not have.
 */
export function removedGhost(
  state: Pick<ProcessState, "edits" | "cursor" | "base">,
  editId: string,
): { editId: string; step: number; afterIsCurrent: boolean; triangles: number; tooBig: boolean } | null {
  const k = state.edits.findIndex((e) => e.id === editId);
  if (k < 0 || !historyRemovedLine(state.edits[k])) return null;
  const before = k === 0 ? (state.base?.triangles ?? 0) : state.edits[k - 1].triangles_after;
  const afterIsCurrent = k + 1 === state.cursor;
  const triangles = before + (afterIsCurrent ? 0 : state.edits[k].triangles_after);
  return { editId, step: k, afterIsCurrent, triangles, tooBig: triangles > GHOST_LIMIT_TRIANGLES };
}

/** Why the ghost is not shown for meshes this big. */
export function ghostTooBigText(triangles: number): string {
  return `These meshes are too big to show both at once (${fmtInt(triangles)} triangles more): the history keeps what was removed, but this computer would run short of memory.`;
}

/**
 * The edit whose removed bits the ghost would show: the current step removed loose bits and the mesh shown before it
 * is the step right before it (same history). Null otherwise (the ghost would show something else).
 */
export function canShowRemoved(before: ProcessState | undefined, now: ProcessState | undefined): string | null {
  if (!before || !now || now.cursor < 1) return null;
  const e = now.edits[now.cursor - 1];
  if (!e?.removed) return null;
  if (before.cursor !== now.cursor - 1) return null;
  if (now.cursor >= 2 && before.edits[before.cursor - 1]?.id !== now.edits[now.cursor - 2]?.id) return null;
  return e.id;
}
