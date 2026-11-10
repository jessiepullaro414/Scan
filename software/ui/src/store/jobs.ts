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
  process: (pid: string, sid: string, quality: ProcessQuality, name?: string) => Promise<Job | null>;
  cancel: (jid: string) => Promise<void>;
};

const LINKS_KEY = "ohs.jobLinks";

export const isActive = (j: Job | undefined | null) => !!j && (j.state === "queued" || j.state === "running");

/**
 * A processing-studio job (M3): a stage (`process_track` / `_straighten` / `_fuse`), a mesh
 * edit (`mesh_edit`) or a recipe. The contract leaves the recipe job's kind open; anything
 * naming a recipe counts. These are followed by the process store, not as "Make mesh".
 */
export const isStudioJob = (kind: string): boolean => /^process_(track|straighten|fuse|refine)|mesh_edit|recipe|^edit/i.test(kind);

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
  const r = job.result;
  if (r && typeof r.project_id === "string" && typeof r.scan_id === "string") {
    return { pid: r.project_id, sid: r.scan_id, quality: (r.quality as ProcessQuality) ?? "full" };
  }
  return null;
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
    if (!finished || !link) return;
    void useProjects.getState().fetchProject(link.pid);
    const label = link.name ? `"${link.name}"` : "the scan";
    if (job.state === "done") {
      toast.success(`Mesh ready for ${label}`, link.quality === "preview" ? "Preview quality" : "Full quality", {
        label: "Open result",
        run: () => navigate(paths.viewer(link.pid, link.sid)),
      });
    } else if (job.state === "failed") {
      toast.error(`Meshing failed for ${label}`, { detail: job.error || job.message || "The job failed.", hint: null });
    } else if (job.state === "cancelled") {
      toast.info("Meshing cancelled", "The raw recording is kept: you can make the mesh again any time.");
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

  process: async (pid, sid, quality, name) => {
    try {
      const job = await api.processScan(pid, sid, quality);
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

export const jobKindLabel = (kind: string): string => {
  const k = kind.toLowerCase();
  if (isStudioJob(k)) {
    if (k.includes("track")) return "Track";
    if (k.includes("straighten")) return "Straighten";
    if (k.includes("fuse")) return "Fuse";
    if (k.includes("refine")) return "Refine with lasers";
    if (k.includes("recipe")) return "Apply recipe";
    return "Mesh edit";
  }
  if (k.includes("process") || k.includes("mesh")) return "Make mesh";
  if (k.includes("sim") || k.includes("demo")) return "Simulator demo";
  if (k.includes("selftest") || k.includes("self_test")) return "Self-test";
  if (k.includes("printable")) return "Print targets";
  if (k.includes("calib")) return "Calibration";
  return kind.replace(/[_-]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());
};

/** A finished simulator-demo job exists (the demo recording is built). */
export const isDemoJob = (j: Job) => /demo/i.test(j.kind) || /^sim/i.test(j.kind);
