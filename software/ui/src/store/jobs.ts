/**
 * Background jobs (meshing, simulator demo generation…).
 *
 * The API's Job has no scan reference, so the UI remembers which scan each processing job
 * belongs to (persisted, so a reload mid-job still shows its progress). When a job carries
 * `result.project_id` / `result.scan_id`, those are used as well.
 */
import { create } from "zustand";
import { api } from "../api/http";
import type { Job, ProcessQuality } from "../api/types";
import { QUALITY_TEXT, normalizeQuality } from "../lib/quality";
import { isWholeRun, makeMeshEnded } from "../lib/making";
import { isStudioJob } from "../lib/stages";
import { useConnection } from "./connection";
import { paths, navigate } from "../lib/router";
import { storage } from "../lib/util";
import { useProjects } from "./projects";
import { toast } from "./toasts";

export type ScanJobLink = { pid: string; sid: string; quality: ProcessQuality; name?: string };

type JobsStore = {
  jobs: Record<string, Job>;
  links: Record<string, ScanJobLink>;
  loaded: boolean;
  upsert: (job: Job) => void;
  fetchAll: () => Promise<void>;
  /** `fineWalls`: the scan's choice of "Make only the thin walls finer" (ENGINE_PLAN X5 step 2); undefined keeps it. */
  process: (pid: string, sid: string, quality: ProcessQuality, name?: string, fineWalls?: boolean) => Promise<Job | null>;
  cancel: (jid: string) => Promise<void>;
};

const LINKS_KEY = "ohs.jobLinks";

export const isActive = (j: Job | undefined | null) => !!j && (j.state === "queued" || j.state === "running");

/**
 * A processing-studio job (M3): a stage (`process_track` / `_straighten` / `_fuse`), a mesh
 * edit (`mesh_edit`) or a recipe. The contract leaves the recipe job's kind open; anything
 * naming a recipe counts. These are followed by the process store, not as "Make mesh".
 */
export { isStudioJob };

/**
 * Aligning two scans or several, or merging them into a new one (M4, kinds `align`, `align_all` and
 * `merge`). None is a "Make mesh": an align job writes nothing (and says nothing when it ends: the Align
 * page shows it), and a merge job's result names the new scan, which gets its own words below.
 */
export const isAlignJob = (kind: string): boolean => /^(align|align_all|merge)$/i.test(kind);

/** The scan a job works on, when the engine says (top-level extras, else the result). */
export function jobScanRef(job: Job): { pid: string; sid: string } | null {
  const top = job as Job & { project_id?: unknown; scan_id?: unknown };
  if (typeof top.project_id === "string" && typeof top.scan_id === "string") return { pid: top.project_id, sid: top.scan_id };
  const r = job.result;
  if (r && typeof r.project_id === "string" && typeof r.scan_id === "string") return { pid: r.project_id, sid: r.scan_id };
  return null;
}

function linkFromResult(job: Job): ScanJobLink | null {
  if (isStudioJob(job.kind)) return null; // not a "Make mesh": no "Mesh ready" toast after every edit
  if (isAlignJob(job.kind)) return null; // nor these: a merge is told in its own words (`mergeFinished`)
  const r = job.result;
  if (r && typeof r.project_id === "string" && typeof r.scan_id === "string") {
    const scan = r.scan as { quality?: unknown } | null | undefined; // "Make mesh" has the quality in its scan
    return { pid: r.project_id, sid: r.scan_id, quality: normalizeQuality(r.quality ?? scan?.quality) ?? "standard" };
  }
  return null;
}

/** A merge job ended: the project has a new scan (or not), said in the merge's own words. */
function mergeFinished(job: Job): void {
  const top = job as Job & { project_id?: unknown };
  const r = job.result;
  const pid = typeof r?.project_id === "string" ? r.project_id : typeof top.project_id === "string" ? top.project_id : null;
  if (pid) void useProjects.getState().fetchProject(pid);
  // On the project's Align page nothing more is said: the page shows it.
  if (pid && location.hash.replace(/\?.*$/, "") === "#" + paths.align(pid)) return;
  if (job.state === "done" && pid && typeof r?.scan_id === "string") {
    const sid = r.scan_id;
    const name = (r.scan as { name?: unknown } | null | undefined)?.name;
    toast.success("Merged scan ready", typeof name === "string" ? name : undefined, { label: "Open the merged scan", run: () => navigate(paths.viewer(pid, sid)) });
  } else if (job.state === "cancelled") {
    const scans = (job as Job & { scans?: unknown }).scans;
    const several = Array.isArray(scans) && scans.length > 2;
    toast.info("Merging cancelled", `Nothing was added to the project. ${several ? "The scans are" : "The two scans are"} unchanged.`);
  }
  // a failed merge: the engine reports it itself (an error in its log, shown as a toast), and the Align page shows it
}

export const useJobs = create<JobsStore>((set, get) => ({
  jobs: {},
  links: storage.get<Record<string, ScanJobLink>>(LINKS_KEY, {}),
  loaded: false,

  upsert: (job) => {
    const prev = get().jobs[job.id];
    const link = get().links[job.id] ?? linkFromResult(job);
    set((s) => ({ jobs: { ...s.jobs, [job.id]: job } }));
    const finished = prev && isActive(prev) && !isActive(job);
    if (finished && /^merge$/i.test(job.kind)) return mergeFinished(job);
    if (!finished) return;
    if (!link) {
      // a job this window did not start (another window, the API): nothing is said here, but the project it names is
      // read again, so its scan does not stay "Making the mesh" on screen (found in review on the real engine)
      const pid = (job as Job & { project_id?: unknown }).project_id;
      if (typeof pid === "string") void useProjects.getState().fetchProject(pid);
      return;
    }
    // read before the project is fetched again: whether the scan had a mesh while this one was being made
    const hadMesh = !!useProjects.getState().details[link.pid]?.scans.find((s) => s.id === link.sid)?.result?.mesh_url;
    void useProjects.getState().fetchProject(link.pid);
    const label = link.name ? `"${link.name}"` : "the scan";
    if (job.state === "done") {
      toast.success(`Mesh ready for ${label}`, QUALITY_TEXT[normalizeQuality(link.quality) ?? "standard"].label, {
        label: "Open result",
        run: () => navigate(paths.viewer(link.pid, link.sid)),
      });
    } else if (job.state === "failed") {
      // The engine says a failed Make mesh itself, in a notice that is its own toast (UX_PLAN R14), whether the earlier
      // mesh stays included; a second toast would repeat it. Without the live link, the page says it.
      if (useConnection.getState().link === "open" && isWholeRun(job)) return;
      const ended = makeMeshEnded(job, hadMesh);
      toast.error(`Making the mesh failed for ${label}`, { detail: ended?.detail ?? (job.error || job.message || "The job failed."), hint: null });
    } else if (job.state === "cancelled") {
      // R12: the table's words; R2: the earlier mesh stays as it was (`mesh_kept`)
      const ended = makeMeshEnded(job, hadMesh);
      toast.info(`${ended?.title ?? "Making the mesh was cancelled"} for ${label}`, ended?.detail ?? "The recording is kept: you can make the mesh any time.");
    }
  },

  fetchAll: async () => {
    try {
      const list = await api.jobs();
      for (const j of list) get().upsert(j);
      set({ loaded: true });
    } catch {
      /* the connection banner already reports an unreachable engine */
    }
  },

  process: async (pid, sid, quality, name, fineWalls) => {
    try {
      const job = await api.processScan(pid, sid, quality, fineWalls);
      const links = { ...get().links, [job.id]: { pid, sid, quality, name } };
      // Keep the persisted map small.
      const ids = Object.keys(links);
      if (ids.length > 60) for (const id of ids.slice(0, ids.length - 60)) delete links[id];
      storage.set(LINKS_KEY, links);
      set((s) => ({ links, jobs: { ...s.jobs, [job.id]: job } }));
      void useProjects.getState().fetchProject(pid);
      return job;
    } catch (e) {
      toast.error("Couldn't start meshing", e);
      return null;
    }
  },

  cancel: async (jid) => {
    try {
      const job = await api.cancelJob(jid);
      get().upsert(job);
    } catch (e) {
      toast.error("Couldn't cancel the job", e);
    }
  },
}));

/** Newest job linked to a scan (an active one wins). */
export function selectScanJob(s: { jobs: Record<string, Job>; links: Record<string, ScanJobLink> }, sid: string): Job | undefined {
  let best: Job | undefined;
  for (const [jid, link] of Object.entries(s.links)) {
    if (link.sid !== sid) continue;
    const j = s.jobs[jid];
    if (!j) continue;
    if (!best) best = j;
    else if (isActive(j) && !isActive(best)) best = j;
    else if (isActive(j) === isActive(best) && j.created > best.created) best = j;
  }
  if (!best) {
    for (const j of Object.values(s.jobs)) {
      const l = linkFromResult(j);
      if (l && l.sid === sid && (!best || j.created > best.created)) best = j;
    }
  }
  return best;
}

export function useScanJob(sid: string | null | undefined): Job | undefined {
  return useJobs((s) => (sid ? selectScanJob(s, sid) : undefined));
}

export { jobKindLabel } from "../lib/stages";

/** A finished simulator-demo job exists (the demo recording is built). */
export const isDemoJob = (j: Job) => /demo/i.test(j.kind) || /^sim/i.test(j.kind);
