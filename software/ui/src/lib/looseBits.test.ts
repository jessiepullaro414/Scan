/** Checks for what "Remove loose bits" took, said in the history (UX_PLAN U14; `npm test`). */
import { GHOST_LIMIT_TRIANGLES, canShowRemoved, ghostTooBigText, historyRemovedLine, removedGhost, removedLine } from "./looseBits.ts";
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

// UX_PLAN R23: the history's line is the entry's summary, which names the rule that took the largest piece
const share = edit("s", { pieces: 297, triangles: 12345, largest_mm: 40.8, largest_rule: "share", largest_triangles: 900 });
share.summary = "Removed 297 loose bits, the largest 40.8 mm across but under 1 % of the mesh (12,345 triangles)";
check(historyRemovedLine(share) === share.summary, `the summary: ${historyRemovedLine(share)}`);
check(historyRemovedLine(e2) === `2 loose bits removed, the largest 4.2${N}mm across`, "an entry without a summary: the page's own words");
check(historyRemovedLine(e1) === null, "not a removal");
check(historyRemovedLine({ ...share, summary: "No loose bits matched: nothing was removed", removed: undefined }) === null, "nothing removed: nothing to show");

// "Show what was removed" at any later time: the ghost is the step before the edit (before.ply), beside the step
// after it (after.ply), or the current mesh when that edit is the last applied one
const base = { triangles: 1000, vertices: 600, stage: "track" as const };
const hist = (cursor: number) => ({ ...st(cursor, [edit("a", { pieces: 3, triangles: 30, largest_mm: 2 }), edit("b"), { ...edit("c", { pieces: 1, triangles: 5, largest_mm: 1 }), triangles_after: 900 }]), base }) as unknown as ProcessState;
const ga = removedGhost(hist(3), "a");
check(!!ga && ga.step === 0 && !ga.afterIsCurrent && ga.triangles === 1000 + 1, `the first edit, two steps back: ${JSON.stringify(ga)}`);
const gc = removedGhost(hist(3), "c");
check(!!gc && gc.step === 2 && gc.afterIsCurrent && gc.triangles === 1, `the last applied edit: the current mesh beside it: ${JSON.stringify(gc)}`);
const undone = removedGhost(hist(1), "c");
check(!!undone && !undone.afterIsCurrent && undone.step === 2, "an undone edit after the cursor: both from the engine");
check(removedGhost(hist(3), "b") === null && removedGhost(hist(3), "x") === null, "not a removal, or not in the history");
check(!ga!.tooBig && removedGhost({ ...hist(3), base: { ...base, triangles: GHOST_LIMIT_TRIANGLES } } as ProcessState, "a")!.tooBig, "too big to show both");
check(ghostTooBigText(5_000_000) === "These meshes are too big to show both at once (5,000,000 triangles more): the history keeps what was removed, but this computer would run short of memory.", ghostTooBigText(5_000_000));

// while "Show what was removed" is on the view shows two meshes and cannot select: the selection tools are
// unavailable (none looks active, no "Drag a box" pill, no selection bar) and the canvas only navigates
{
  const fsm = (await import(["node", "fs"].join(":"))) as { readFileSync: (u: URL, enc: "utf8") => string };
  const page = fsm.readFileSync(new URL("../pages/ProcessPage.tsx", import.meta.url), "utf8");
  check(/const canSelect = !!mesh && !comparing && !removedView\.view;/.test(page), "no selecting while the removed view shows");
  check(/const showSelBar = canSelect && /.test(page), "the selection bar and its pill follow");
  check(/active=\{canSelect && tool === t\.id\}/.test(page) && /tool=\{canSelect \? tool : "navigate"\}/.test(page), "no tool looks active, the canvas navigates");
}

console.log(`looseBits.test: ${checks} checks passed`);
