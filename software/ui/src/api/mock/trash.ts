/**
 * The demo's trash (UX_PLAN U7; APP_API.md "Rename a scan, the trash"): deleted projects and scans kept in memory,
 * restored where they were (a taken name gets " (restored)"), deleted for good only when the page names them with
 * `confirm`. The refusals use the engine's words (`ohs/server/trash.py`, `ohs/server/studio.py`).
 */
import type { Project, Scan, TrashItem, TrashList } from "../types";
import { HttpError } from "./host";

type Kept = { item: TrashItem; project?: Project; scan?: Scan; pid?: string };

let n = 0;
const itemId = (base: string) => `${base}-trash-${(++n).toString(36)}`;

export function restoredName(name: string, taken: Set<string>): string {
  if (!taken.has(name)) return name;
  for (let k = 1; ; k++) {
    const c = `${name} (restored${k === 1 ? "" : ` ${k}`})`;
    if (!taken.has(c)) return c;
  }
}

/** "Scan <n>": the count plus one, or the next number no scan of the project has (U50). */
export function nextScanName(scans: readonly Scan[]): string {
  const used = new Set(scans.map((s) => /^Scan (\d+)$/.exec(s.name)?.[1]).filter(Boolean).map(Number));
  let k = scans.length + 1;
  while (used.has(k)) k++;
  return `Scan ${k}`;
}

/** The demo's guess at a scan's folder size. */
const scanBytes = (s: Scan) => Math.round(s.n_frames * 1.9 * 1024 * 1024);

export class MockTrash {
  private kept: Kept[] = [];
  private projects: Map<string, Project>;

  constructor(projects: Map<string, Project>) {
    this.projects = projects;
  }

  addProject(p: Project, ageS = 0): TrashItem {
    const item: TrashItem = {
      id: itemId(p.id), kind: "project", name: p.name, deleted: new Date(Date.now() - ageS * 1000).toISOString(),
      bytes: p.scans.reduce((a, s) => a + scanBytes(s), 4096), n_scans: p.scans.length, project_id: null, project_name: null, project_exists: null,
    };
    this.kept.unshift({ item, project: p });
    return item;
  }

  addScan(p: Project, s: Scan, ageS = 0): TrashItem {
    const item: TrashItem = {
      id: itemId(s.id), kind: "scan", name: s.name, deleted: new Date(Date.now() - ageS * 1000).toISOString(),
      bytes: scanBytes(s), n_scans: null, project_id: p.id, project_name: p.name, project_exists: true,
    };
    this.kept.unshift({ item, scan: s, pid: p.id });
    return item;
  }

  list(): TrashList {
    const items = this.kept
      .map((k) => ({ ...k.item, ...(k.item.kind === "scan" ? { project_exists: this.projects.has(k.pid ?? "") } : {}) }))
      .sort((x, y) => ((x.deleted ?? "") < (y.deleted ?? "") ? 1 : -1)); // the newest first, as the engine
    return { items, bytes: items.reduce((a, i) => a + i.bytes, 0) };
  }

  private find(id: string): Kept {
    const k = this.kept.find((x) => x.item.id === id);
    if (!k) throw new HttpError(404, "There is no such item in the trash: it may have been restored or deleted.", "Open the trash again to see what it holds.");
    return k;
  }

  restore(id: string): { kind: "project" | "scan"; project_id: string; scan_id: string | null; name: string; renamed: boolean } {
    const k = this.find(id);
    if (k.project) {
      const p = k.project;
      const name = restoredName(p.name, new Set([...this.projects.values()].map((x) => x.name)));
      const renamed = name !== p.name;
      p.name = name;
      this.projects.set(p.id, p);
      this.kept = this.kept.filter((x) => x !== k);
      return { kind: "project", project_id: p.id, scan_id: null, name, renamed };
    }
    const p = this.projects.get(k.pid ?? "");
    if (!p) throw new HttpError(409, "The project this scan was in is not there: it is in the trash too, or it was deleted.", "Restore the project first, then the scan.");
    const s = k.scan!;
    const name = restoredName(s.name, new Set(p.scans.map((x) => x.name)));
    const renamed = name !== s.name;
    s.name = name;
    p.scans.push(s);
    p.n_scans = p.scans.length;
    this.kept = this.kept.filter((x) => x !== k);
    return { kind: "scan", project_id: p.id, scan_id: s.id, name, renamed };
  }

  empty(confirm: unknown, items: unknown): { removed: string[]; left: string[]; bytes: number } {
    if (confirm !== true) throw new HttpError(400, "Emptying the trash deletes its items for good, so it needs your confirmation.", "Press Empty the trash again and confirm it.");
    if (!Array.isArray(items) || !items.length) throw new HttpError(400, "The page did not say which items to delete for good.", "Open the trash again and press Empty the trash.");
    const ids = new Set(items.filter((i): i is string => typeof i === "string"));
    const gone = this.kept.filter((k) => ids.has(k.item.id));
    this.kept = this.kept.filter((k) => !ids.has(k.item.id));
    return { removed: gone.map((k) => k.item.id), left: [], bytes: gone.reduce((a, k) => a + k.item.bytes, 0) };
  }

  counts(): { bytes: number; projects: number; scans: number } {
    const l = this.list();
    return { bytes: l.bytes, projects: l.items.filter((i) => i.kind === "project").length, scans: l.items.filter((i) => i.kind === "scan").length };
  }
}
