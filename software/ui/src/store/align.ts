/**
 * Align two scans and merge them (APP_API.md "M4 first slice"): what the Align page of one project has
 * chosen (the scan that stays, the scan that is moved, whether the part stayed where it was), the points
 * picked on the two meshes, the alignment job and its result, and the merge job and the scan it made.
 *
 * Job progress comes from the `job` messages (the jobs store); when one of the two jobs ends, its result
 * or its plain reason is picked up here. A merge that runs when the page is opened (after a reload) is
 * followed again. Errors keep the API's own words: `detail`, then `hint`.
 */
import { create } from "zustand";
import { api, describeError } from "../api/http";
import type { AlignRequest, AlignResult, Job, MergeResult } from "../api/types";
import { addPick, completePairs, isAlignResult, isMergeResult, MIN_PAIRS, removePair, type Picks, type Side, type Vec3 } from "../lib/align";
import { isActive, useJobs } from "./jobs";
import { useProjects } from "./projects";

/** Something that went wrong, in the API's words: what happened, then what to do. */
export type Problem = { detail: string; hint: string | null };

type AlignState = {
  pid: string | null;
  /** The scan that stays in place, and the scan that is moved onto it. */
  target: string | null;
  source: string | null;
  /** The part was not moved on its table between the two scans. */
  sameSetup: boolean;
  picks: Picks;

  alignStarting: boolean;
  alignJobId: string | null;
  result: AlignResult | null;
  alignProblem: Problem | null;

  mergeStarting: boolean;
  mergeJobId: string | null;
  merged: MergeResult | null;
  mergeProblem: Problem | null;
  mergeCancelled: boolean;
};

type AlignStore = AlignState & {
  /** The page of project `pid` is shown, maybe with the two scans given in the address. */
  open: (pid: string, target?: string | null, source?: string | null) => void;
  /** The page is left: what a finished merge said is not kept for the next visit. */
  leave: () => void;
  /** Other scans: the picked points and the result belonged to the old ones. */
  setScans: (target: string | null, source: string | null) => void;
  /** The two scans change roles; each keeps its picked points. */
  swap: () => void;
  setSameSetup: (same: boolean) => void;
  pick: (side: Side, p: Vec3) => void;
  removePair: (index: number) => void;
  clearPicks: () => void;
  /** Start the alignment: from the shape alone, or (`fromPoints`) from the picked pairs. */
  align: (fromPoints: boolean) => Promise<boolean>;
  /** Merge at the result's pose into a new scan called `name` (empty: the server's default). */
  merge: (name: string) => Promise<boolean>;
};

const noPicks = (): Picks => ({ target: [], source: [] });

const fresh = (): AlignState => ({
  pid: null,
  target: null,
  source: null,
  sameSetup: true,
  picks: noPicks(),
  alignStarting: false,
  alignJobId: null,
  result: null,
  alignProblem: null,
  mergeStarting: false,
  mergeJobId: null,
  merged: null,
  mergeProblem: null,
  mergeCancelled: false,
});

/** What belongs to one pair of scans and one alignment of them. */
const noResult = { alignJobId: null, result: null, alignProblem: null, merged: null, mergeProblem: null, mergeCancelled: false } as const;

/** Put a job into the jobs store unless it knows a newer copy (the reply to a POST can be older than a `job` message). */
function adoptJob(j: Job): void {
  const known = useJobs.getState().jobs[j.id];
  if (!known || (isActive(known) && (!isActive(j) || j.progress >= known.progress))) useJobs.getState().upsert(j);
}

const jobOf = (id: string | null): Job | undefined => (id ? useJobs.getState().jobs[id] : undefined);
const running = (s: AlignState): boolean => s.alignStarting || s.mergeStarting || isActive(jobOf(s.alignJobId)) || isActive(jobOf(s.mergeJobId));

export const useAlign = create<AlignStore>((set, get) => ({
  ...fresh(),

  open: (pid, target, source) => {
    const s = get();
    if (source && source === target) source = null; // the two must differ
    if (s.pid !== pid) {
      set({ ...fresh(), pid, target: target || null, source: source || null });
      follow();
      return;
    }
    const t = target || s.target;
    const src = source || s.source;
    if (t !== src && (t !== s.target || src !== s.source)) get().setScans(t, src);
  },

  leave: () => {
    if (!isActive(jobOf(get().mergeJobId))) set({ merged: null, mergeProblem: null, mergeCancelled: false });
  },

  setScans: (target, source) => {
    const s = get();
    if ((target === s.target && source === s.source) || running(s)) return;
    if (target === s.source && source === s.target) return get().swap();
    set({ ...noResult, target, source, picks: noPicks() });
  },

  swap: () => {
    const s = get();
    if (running(s)) return;
    set({ ...noResult, target: s.source, source: s.target, picks: { target: s.picks.source, source: s.picks.target } });
  },

  setSameSetup: (sameSetup) => {
    const s = get();
    if (sameSetup === s.sameSetup || running(s)) return;
    set({ ...noResult, sameSetup }); // the result was worked out for the other case
  },

  pick: (side, p) => set((s) => ({ picks: addPick(s.picks, side, p) })),
  removePair: (index) => set((s) => ({ picks: removePair(s.picks, index) })),
  clearPicks: () => set({ picks: noPicks() }),

  align: async (fromPoints) => {
    const s = get();
    const { pid, target, source } = s;
    if (!pid || !target || !source || running(s)) return false;
    const body: AlignRequest = { target, source, same_setup: s.sameSetup };
    if (fromPoints) {
      body.pairs = completePairs(s.picks);
      if (body.pairs.length < MIN_PAIRS) return false;
    }
    set({ alignStarting: true, alignProblem: null });
    try {
      const job = await api.alignScans(pid, body);
      adoptJob(job);
      // the old result goes only now: a request that is refused (points in one line…) leaves it on screen
      set({ alignStarting: false, alignJobId: job.id, result: null, merged: null, mergeProblem: null, mergeCancelled: false });
      follow();
      return true;
    } catch (e) {
      set({ alignStarting: false, alignProblem: describeError(e) });
      return false;
    }
  },

  merge: async (name) => {
    const s = get();
    const { pid, target, source, result } = s;
    if (!pid || !target || !source || !result || running(s)) return false;
    if (result.target !== target || result.source !== source) return false;
    set({ mergeStarting: true, mergeProblem: null, mergeCancelled: false, merged: null });
    try {
      const title = name.trim();
      const job = await api.mergeScans(pid, { target, source, T: result.T, same_setup: result.same_setup, ...(title ? { name: title } : {}) });
      adoptJob(job);
      set({ mergeStarting: false, mergeJobId: job.id });
      follow();
      return true;
    } catch (e) {
      set({ mergeStarting: false, mergeProblem: describeError(e) });
      return false;
    }
  },
}));

/** Pick up the two jobs when they end, and a merge of this project that runs without this page knowing. */
function follow(): void {
  const s = useAlign.getState();
  const jobs = useJobs.getState().jobs;

  const al = s.alignJobId ? jobs[s.alignJobId] : undefined;
  if (al && !isActive(al)) {
    if (al.state === "done" && isAlignResult(al.result)) useAlign.setState({ alignJobId: null, result: al.result, alignProblem: null });
    else if (al.state === "done")
      useAlign.setState({ alignJobId: null, alignProblem: { detail: "The alignment finished, but its answer couldn't be read", hint: "Align again. If it happens again, check the engine log." } });
    else if (al.state === "failed")
      useAlign.setState({
        alignJobId: null,
        alignProblem: describeError({ detail: al.error || al.message || "The alignment failed", hint: "Check that both scans open in the viewer, then align again." }),
      });
    else useAlign.setState({ alignJobId: null }); // cancelled: nothing was written, nothing to say
  }

  const mg = s.mergeJobId ? jobs[s.mergeJobId] : undefined;
  if (mg && !isActive(mg)) {
    if (mg.state === "done" && isMergeResult(mg.result)) {
      useAlign.setState({ mergeJobId: null, merged: mg.result, mergeProblem: null });
      if (s.pid) void useProjects.getState().fetchProject(s.pid);
    } else if (mg.state === "cancelled") useAlign.setState({ mergeJobId: null, mergeCancelled: true });
    else
      useAlign.setState({
        mergeJobId: null,
        mergeProblem: describeError({
          detail: (mg.state === "failed" ? mg.error || mg.message : null) || "The merge failed",
          hint: "The two scans are unchanged. Fix what the message says, then merge again.",
        }),
      });
  }

  // A merge of this project already running (the page was reloaded, or it was started in another window).
  if (s.pid && !s.mergeJobId && !s.mergeStarting) {
    const found = Object.values(jobs).find((j) => /^merge$/i.test(j.kind) && isActive(j) && (j as Job & { project_id?: unknown }).project_id === s.pid);
    if (found) {
      const meta = found as Job & { target?: unknown; source?: unknown };
      const same = meta.target === s.target && meta.source === s.source;
      useAlign.setState({
        mergeJobId: found.id,
        merged: null,
        mergeProblem: null,
        mergeCancelled: false,
        ...(same || typeof meta.target !== "string" || typeof meta.source !== "string"
          ? {}
          : { target: meta.target, source: meta.source, picks: noPicks(), result: null, alignJobId: null, alignProblem: null }),
      });
    }
  }
}

useJobs.subscribe(follow);
