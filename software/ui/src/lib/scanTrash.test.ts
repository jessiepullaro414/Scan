/** Checks for renaming a scan, moving it to the trash, and the trash (UX_PLAN U7 with U50; `npm test`). */
import { emptyTrashQuestion, scanBusyReason, scanDependents, dependentsSentence, trashItemLine, trashSummary } from "./scanTrash.ts";
import type { Scan, TrashItem } from "../api/types";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`check failed: ${what}`);
}

const scan = (id: string, name: string, extra: Partial<Scan> = {}): Scan => ({
  id, name, created: "2026-11-20T10:00:00", mode: "markerless", n_frames: 10, duration_s: 1, status: "processed", result: null, ...extra,
});

// what depends on a scan: merged from it, or a second pass of it
const top = scan("a", "Top");
const pass = scan("b", "Top, second pass", { continues: { scan_id: "a" } as Scan["continues"] });
const merged = scan("c", "Top merged", { merged_from: [{ id: "a", name: "Top" }, { id: "x", name: "B" }] });
const other = scan("d", "Side");
const deps = scanDependents([top, pass, merged, other], "a");
check(deps.length === 2 && deps[0].how === "continues" && deps[1].how === "merged", JSON.stringify(deps));
check(scanDependents([top, other], "a").length === 0, "nothing depends");
const said = dependentsSentence(deps);
check(said === "“Top, second pass” is a second pass of this scan, and “Top merged” was merged from it.", String(said));
check(dependentsSentence([{ id: "c", name: "M", how: "merged" }, { id: "e", name: "N", how: "merged" }]) === "“M” and “N” were merged from this scan.", "two merged");
check(dependentsSentence([]) === null, "none");

// a busy scan cannot go (the server says so too)
check(scanBusyReason(scan("a", "x", { status: "recording" }), false) === "This scan is recording. Stop it first.", "recording");
check(scanBusyReason(scan("a", "x", { status: "processing" }), false)!.startsWith("This scan is busy"), "processing");
check(scanBusyReason(top, true)!.startsWith("This scan is busy"), "a job");
check(scanBusyReason(top, false) === null, "free");

// the trash's rows
const item = (over: Partial<TrashItem>): TrashItem => ({
  id: "i", kind: "scan", name: "Top", deleted: null, bytes: 1_572_864, n_scans: null, project_id: "p", project_name: "Bracket", project_exists: true, ...over,
});
check(trashItemLine(item({})) === "Scan from “Bracket” · 1.5 MB", trashItemLine(item({})));
check(trashItemLine(item({ kind: "project", n_scans: 3, project_name: null, project_id: null, project_exists: null })) === "Project, 3 scans · 1.5 MB", "a project");
check(trashItemLine(item({ kind: "project", n_scans: 1, project_name: null, project_id: null, project_exists: null })) === "Project, 1 scan · 1.5 MB", "one scan");
check(trashSummary([]) === "The trash is empty.", "empty");
check(trashSummary([item({}), item({ id: "j", kind: "project", bytes: 524_288 })]) === "1 project and 1 scan take 2.0 MB.", trashSummary([item({}), item({ id: "j", kind: "project", bytes: 524_288 })]));

// the second question: what goes for good, and that it cannot be undone
const q = emptyTrashQuestion([item({}), item({ id: "j" })]);
check(q.title === "Delete 2 scans for good?" && q.body.includes("cannot be undone") && q.body.includes("3.0 MB"), JSON.stringify(q));
check(emptyTrashQuestion([item({ kind: "project" })]).title === "Delete 1 project for good?", "one project");

console.log(`scanTrash.test: ${checks} checks passed`);
