/**
 * Renaming a scan, moving it to the trash, and the trash (UX_PLAN U7 with U50; APP_API.md "Rename a scan, the
 * trash"). The words the project page and the trash dialog say; the server checks the same things and refuses in a
 * plain sentence.
 */
import type { Scan, TrashItem } from "../api/types";
import { fmtBytes } from "./format.ts";

export type Dependent = { id: string; name: string; how: "merged" | "continues" };

/** The scans that depend on scan `sid`: a second pass of it, or merged from it. */
export function scanDependents(scans: readonly Scan[], sid: string): Dependent[] {
  const out: Dependent[] = [];
  for (const s of scans) {
    if (s.id === sid) continue;
    if (s.continues && (s.continues as { scan_id?: string }).scan_id === sid) out.push({ id: s.id, name: s.name, how: "continues" });
    else if (s.merged_from?.some((m) => m.id === sid)) out.push({ id: s.id, name: s.name, how: "merged" });
  }
  return out;
}

function and(names: string[]): string {
  return names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** What depends on the scan, in one sentence; null when nothing does. */
export function dependentsSentence(deps: readonly Dependent[]): string | null {
  const passes = deps.filter((d) => d.how === "continues").map((d) => `“${d.name}”`);
  const merged = deps.filter((d) => d.how === "merged").map((d) => `“${d.name}”`);
  if (!passes.length && !merged.length) return null;
  const p = passes.length ? `${and(passes)} ${passes.length === 1 ? "is a second pass" : "are second passes"} of this scan` : null;
  const m = merged.length ? `${and(merged)} ${merged.length === 1 ? "was" : "were"} merged from ${p ? "it" : "this scan"}` : null;
  return `${[p, m].filter(Boolean).join(", and ")}.`;
}

/** Why the scan cannot go to the trash now (the server refuses it too); null when it can. */
export function scanBusyReason(scan: Pick<Scan, "status">, jobRunning: boolean): string | null {
  if (scan.status === "recording") return "This scan is recording. Stop it first.";
  if (jobRunning || scan.status === "processing") return "This scan is busy: its mesh is being made, or it is being aligned or merged. Wait for it to finish, or cancel it.";
  return null;
}

/** A trash row's second line: what it is, where it was, its size. */
export function trashItemLine(it: TrashItem): string {
  const what =
    it.kind === "project"
      ? `Project, ${it.n_scans ?? 0} scan${it.n_scans === 1 ? "" : "s"}`
      : `Scan from ${it.project_name ? `“${it.project_name}”` : "a project"}${it.project_exists === false ? " (the project is in the trash or gone)" : ""}`;
  return `${what} · ${fmtBytes(it.bytes)}`;
}

function count(items: readonly TrashItem[]): string {
  const p = items.filter((i) => i.kind === "project").length;
  const s = items.length - p;
  const parts = [p ? `${p} project${p === 1 ? "" : "s"}` : null, s ? `${s} scan${s === 1 ? "" : "s"}` : null].filter(Boolean);
  return parts.join(" and ");
}

/** The trash in one line. */
export function trashSummary(items: readonly TrashItem[]): string {
  if (!items.length) return "The trash is empty.";
  const n = items.length;
  return `${count(items)} take${n === 1 ? "s" : ""} ${fmtBytes(items.reduce((a, i) => a + i.bytes, 0))}.`;
}

/** The second question before deleting for good. */
export function emptyTrashQuestion(items: readonly TrashItem[]): { title: string; body: string } {
  return {
    title: `Delete ${count(items)} for good?`,
    body: `This frees ${fmtBytes(items.reduce((a, i) => a + i.bytes, 0))}. It cannot be undone: the recordings, meshes and measurements in them are gone for good.`,
  };
}
