/**
 * What "Remove loose bits" took (UX_PLAN U14; APP_API.md, `EditEntry.removed`): the history says how many pieces
 * went and how large the largest was (a thin or small part of the real part can go with the noise), and offers to
 * show them: the mesh before the edit as a ghost over the mesh after it (the Compare control's "Both").
 */
import type { EditEntry, ProcessState } from "../api/types";
import { fmtFixed, withUnit } from "./format.ts";

/** "3 loose bits removed, the largest 4.2 mm across"; null for an edit that removed nothing or says nothing. */
export function removedLine(r: EditEntry["removed"] | null | undefined): string | null {
  if (!r || !(r.pieces > 0)) return null;
  const size = withUnit(fmtFixed(r.largest_mm, 1), "mm");
  return r.pieces === 1 ? `1 loose bit removed, ${size} across` : `${r.pieces} loose bits removed, the largest ${size} across`;
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
