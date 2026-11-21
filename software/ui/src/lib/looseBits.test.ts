/** Checks for what "Remove loose bits" took, said in the history (UX_PLAN U14; `npm test`). */
import { canShowRemoved, removedLine } from "./looseBits.ts";
import type { EditEntry, ProcessState } from "../api/types";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`check failed: ${what}`);
}

const N = " ";
check(removedLine({ pieces: 3, triangles: 120, largest_mm: 4.2 }) === `3 loose bits removed, the largest 4.2${N}mm across`, String(removedLine({ pieces: 3, triangles: 120, largest_mm: 4.2 })));
check(removedLine({ pieces: 1, triangles: 12, largest_mm: 0.5 }) === `1 loose bit removed, 0.5${N}mm across`, "one bit");
check(removedLine(undefined) === null && removedLine(null) === null, "an edit from before, or another edit");

// "Show what was removed": the edit is the current step and the mesh shown before it is the step before it
const edit = (id: string, removed?: EditEntry["removed"]): EditEntry =>
  ({ id, op: removed ? "remove_floaters" : "smooth", params: {}, label: id, created: "", triangles_after: 1, ms: 1, ...(removed ? { removed } : {}) }) as EditEntry;
const st = (cursor: number, edits: EditEntry[]) => ({ cursor, edits }) as unknown as ProcessState;
const e1 = edit("e1"),
  e2 = edit("e2", { pieces: 2, triangles: 24, largest_mm: 4.2 });
check(canShowRemoved(st(1, [e1, e2]), st(2, [e1, e2])) === "e2", "right after the edit");
check(canShowRemoved(st(0, [e1, e2]), st(2, [e1, e2])) === null, "a jump of two steps: the ghost is not the step before");
check(canShowRemoved(st(1, [e1, e2]), st(1, [e1, e2])) === null, "the current step is not a removal");
check(canShowRemoved(st(1, [edit("x"), e2]), st(2, [e1, e2])) === null, "another history before");
check(canShowRemoved(undefined, st(2, [e1, e2])) === null, "no mesh before");
check(canShowRemoved(st(0, [edit("r", { pieces: 1, triangles: 3, largest_mm: 1 })]), st(1, [edit("r", { pieces: 1, triangles: 3, largest_mm: 1 })])) === "r", "the first edit");

console.log(`looseBits.test: ${checks} checks passed`);
