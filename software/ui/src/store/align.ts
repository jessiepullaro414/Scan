/**
 * Align scans and merge them (APP_API.md "M4 first slice", two scans, and "M4 second slice", up to
 * eight): what the Align page of one project has chosen (the list of scans, the first of which stays in
 * place; whether the part stayed where it was), the points picked between two scans at a time, the
 * alignment job and its result, and the merge job and the scan it made.
 *
 * With two scans the page calls `align` and the merge's first form (target, source, T), exactly as the
 * first slice did; with three or more it calls `align_all` and the merge's `scans` form. When the engine
 * was not sure of two scans, the pose the person chose (`chosenPose`) is the one merged at, and without a
 * choice (or their own picked points) nothing is merged (lib/alignChoice.ts).
 *
 * Job progress comes from the `job` messages (the jobs store); when one of the two jobs ends, its result
 * or its plain reason is picked up here. A merge that runs when the page is opened (after a reload) is
 * followed again. Errors keep the API's own words: `detail`, then `hint`.
 */
import { create } from "zustand";
import { api, describeError } from "../api/http";
import type { AlignAllRequest, AlignAllResult, AlignRequest, AlignResult, Job, MergeResult } from "../api/types";
import {
  addPick,
  completePairs,
  isAlignAllResult,
  isAlignResult,
  isMergeResult,
  keepPicksOf,
  MAX_SCANS,
  mergeScansOf,
  MIN_PAIRS,
  pickedPoints,
  pickPairOf,
  picksFor,
  removePair,
  sameList,
  suggestPickPair,
  withPicks,
  type PickBook,
  type Side,
  type Vec3,
} from "../lib/align";
import { mergeOffer, poseInUse } from "../lib/alignChoice";
import { isActive, useJobs } from "./jobs";
import { useProjects } from "./projects";

/** Something that went wrong, in the API's words: what happened, then what to do. */
export type Problem = { detail: string; hint: string | null };

type AlignState = {
  pid: string | null;
  /** The scans, in order: the first stays in place, the others are moved onto it. */
  scans: string[];
  /** The part was not moved on its table between the scans. */
  sameSetup: boolean;
  /** The points picked so far, per pair of scans. */
  picked: PickBook;
  /** The two scans shown side by side to pick on (null: the first two of the list). */
  pickPair: [string, string] | null;

  alignStarting: boolean;
  alignJobId: string | null;
  /** The answer for two scans (`align`), or for several (`align_all`): at most one of the two is set. */
  result: AlignResult | null;
  multi: AlignAllResult | null;
  alignProblem: Problem | null;
  /** Two scans, the engine not sure: the pose the person looked at and chose (an index into `result.poses`). */
  chosenPose: number | null;

  mergeStarting: boolean;
  mergeJobId: string | null;
  merged: MergeResult | null;
  mergeProblem: Problem | null;
  mergeCancelled: boolean;
};

type AlignStore = AlignState & {
  /** The page of project `pid` is shown, maybe with the scans given in the address. */
  open: (pid: string, target?: string | null, source?: string | null, more?: readonly string[]) => void;
  /** The page is left: what a finished merge said is not kept for the next visit. */
  leave: () => void;
  /** Another list of scans: the result belonged to the old one, and so do the points of scans that left it. */
  setScans: (scans: readonly string[]) => void;
  /** Two scans change roles; each keeps its picked points. */
  swap: () => void;
  setSameSetup: (same: boolean) => void;
  /** Pick on these two scans (shown in the order of the list). */
  setPickPair: (a: string, b: string) => void;
  /** A point on the pair's scan shown on the left (`target`) or on the right (`source`). */
  pick: (side: Side, p: Vec3) => void;
  removePair: (index: number) => void;
  /** The points of the pair being picked on. */
  clearPicks: () => void;
  /** Start the alignment: from the shape alone, or (`fromPoints`) from the picked pairs. */
  align: (fromPoints: boolean) => Promise<boolean>;
  /** The person chooses one of the poses the engine was not sure between (null: none). */
  choosePose: (index: number | null) => void;
  /** Merge at the result's poses (two scans: at the chosen pose, if one was chosen) into a new scan called `name` (empty: the server's default). */
  merge: (name: string) => Promise<boolean>;
};

const fresh = (): AlignState => ({
  pid: null,
  scans: [],
  sameSetup: true,
  picked: {},
  pickPair: null,
  alignStarting: false,
  alignJobId: null,
  result: null,
  multi: null,
  alignProblem: null,
  chosenPose: null,
  mergeStarting: false,
  mergeJobId: null,
  merged: null,
  mergeProblem: null,
  mergeCancelled: false,
});

/** What belongs to one list of scans and one alignment of them. */
const noResult = { alignJobId: null, result: null, multi: null, alignProblem: null, chosenPose: null, merged: null, mergeProblem: null, mergeCancelled: false } as const;

/** The scans asked for, each once, at most eight. */
function asked(...ids: (string | null | undefined)[]): string[] {
  const out: string[] = [];
  for (const id of ids) if (id && !out.includes(id) && out.length < MAX_SCANS) out.push(id);
  return out;
}

/** Put a job into the jobs store unless it knows a newer copy (the reply to a POST can be older than a `job` message). */
function adoptJob(j: Job): void {
  const known = useJobs.getState().jobs[j.id];
  if (!known || (isActive(known) && (!isActive(j) || j.progress >= known.progress))) useJobs.getState().upsert(j);
}

const jobOf = (id: string | null): Job | undefined => (id ? useJobs.getState().jobs[id] : undefined);
const running = (s: AlignState): boolean => s.alignStarting || s.mergeStarting || isActive(jobOf(s.alignJobId)) || isActive(jobOf(s.mergeJobId));

export const useAlign = create<AlignStore>((set, get) => ({
  ...fresh(),

  open: (pid, target, source, more = []) => {
    const s = get();
    const list = asked(target, source, ...more);
    if (s.pid !== pid) {
      set({ ...fresh(), pid, scans: list });
      follow();
      return;
    }
    // the address names at least the two scans; fewer: keep what the page has
    if (list.length >= 2 && !sameList(list, s.scans)) get().setScans(list);
  },

  leave: () => {
    if (!isActive(jobOf(get().mergeJobId))) set({ merged: null, mergeProblem: null, mergeCancelled: false });
  },

  setScans: (scans) => {
    const s = get();
    if (sameList(scans, s.scans) || running(s)) return;
    const list = [...scans];
    set({ ...noResult, scans: list, picked: keepPicksOf(s.picked, list) });
  },

  swap: () => {
    const s = get();
    if (s.scans.length !== 2) return;
    get().setScans([s.scans[1], s.scans[0]]);
  },

  setSameSetup: (sameSetup) => {
    const s = get();
    if (sameSetup === s.sameSetup || running(s)) return;
    set({ ...noResult, sameSetup }); // the result was worked out for the other case
  },

  setPickPair: (a, b) => {
    if (a !== b) set({ pickPair: [a, b] });
  },

  pick: (side, p) =>
    set((s) => {
      const pair = pickPairOf(s.scans, s.pickPair);
      if (!pair) return {};
      return { picked: withPicks(s.picked, pair[0], pair[1], addPick(picksFor(s.picked, pair[0], pair[1]), side, p)) };
    }),
  removePair: (index) =>
    set((s) => {
      const pair = pickPairOf(s.scans, s.pickPair);
      if (!pair) return {};
      return { picked: withPicks(s.picked, pair[0], pair[1], removePair(picksFor(s.picked, pair[0], pair[1]), index)) };
    }),
  clearPicks: () =>
    set((s) => {
      const pair = pickPairOf(s.scans, s.pickPair);
      return pair ? { picked: withPicks(s.picked, pair[0], pair[1], { target: [], source: [] }) } : {};
    }),

  align: async (fromPoints) => {
    const s = get();
    const { pid, scans } = s;
    if (!pid || scans.length < 2 || running(s)) return false;
    let start: () => Promise<Job>;
    if (scans.length === 2) {
      const [target, source] = scans;
      const body: AlignRequest = { target, source, same_setup: s.sameSetup };
      if (fromPoints) {
        body.pairs = completePairs(picksFor(s.picked, target, source));
        if (body.pairs.length < MIN_PAIRS) return false;
      }
      start = () => api.alignScans(pid, body);
    } else {
      const body: AlignAllRequest = { scans: [...scans], same_setup: s.sameSetup };
      if (fromPoints) {
        body.picked = pickedPoints(s.picked, scans);
        if (!body.picked.length) return false;
      }
      start = () => api.alignAllScans(pid, body);
    }
    set({ alignStarting: true, alignProblem: null });
    try {
      const job = await start();
      adoptJob(job);
      // the old result goes only now: a request that is refused (points in one line…) leaves it on screen
      set({ alignStarting: false, alignJobId: job.id, result: null, multi: null, chosenPose: null, merged: null, mergeProblem: null, mergeCancelled: false });
      follow();
      return true;
    } catch (e) {
      set({ alignStarting: false, alignProblem: describeError(e) });
      return false;
    }
  },

  choosePose: (index) => {
    const s = get();
    if (running(s) || !s.result) return;
    set({ chosenPose: index !== null && index >= 0 && index < (s.result.poses?.length ?? 0) ? index : null });
  },

  merge: async (name) => {
    const s = get();
    const { pid, scans, result, multi } = s;
    if (!pid || scans.length < 2 || running(s)) return false;
    const title = name.trim();
    let start: () => Promise<Job>;
    if (scans.length === 2) {
      const [target, source] = scans;
      if (!result || result.target !== target || result.source !== source) return false;
      if (!mergeOffer(result, s.chosenPose).offered) return false; // the engine was not sure, and nothing was chosen
      const T = poseInUse(result, s.chosenPose);
      start = () => api.mergeScans(pid, { target, source, T, same_setup: result.same_setup, ...(title ? { name: title } : {}) });
    } else {
      if (!multi || !sameList(multi.scans, scans)) return false;
      const list = mergeScansOf(multi, scans);
      if (!list) return false; // a scan is not placed: nothing to merge it at
      start = () => api.mergeScans(pid, { scans: list, same_setup: multi.same_setup, ...(title ? { name: title } : {}) });
    }
    set({ mergeStarting: true, mergeProblem: null, mergeCancelled: false, merged: null });
    try {
      const job = await start();
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

const isIdList = (x: unknown): x is string[] => Array.isArray(x) && x.length >= 2 && x.every((v) => typeof v === "string");

/** Pick up the two jobs when they end, and a merge of this project that runs without this page knowing. */
function follow(): void {
  const s = useAlign.getState();
  const jobs = useJobs.getState().jobs;
  const both = s.scans.length > 2 ? "the scans" : "both scans";

  const al = s.alignJobId ? jobs[s.alignJobId] : undefined;
  if (al && !isActive(al)) {
    if (al.state === "done" && isAlignAllResult(al.result)) {
      const multi = al.result;
      // scans not placed: pick on one of them and a placed one next (unless the pair picked on already has one)
      const pair = pickPairOf(s.scans, s.pickPair);
      const keep = pair && pair.some((id) => multi.unplaced.includes(id));
      useAlign.setState({ alignJobId: null, multi, result: null, alignProblem: null, ...(keep ? {} : { pickPair: suggestPickPair(multi, s.scans) ?? s.pickPair }) });
    } else if (al.state === "done" && isAlignResult(al.result)) useAlign.setState({ alignJobId: null, result: al.result, multi: null, alignProblem: null, chosenPose: null });
    else if (al.state === "done")
      useAlign.setState({ alignJobId: null, alignProblem: { detail: "The alignment finished, but its answer couldn't be read", hint: "Align again. If it happens again, check the engine log." } });
    else if (al.state === "failed")
      useAlign.setState({
        alignJobId: null,
        alignProblem: describeError({ detail: al.error || al.message || "The alignment failed", hint: `Check that ${both} open in the viewer, then align again.` }),
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
          hint: `${s.scans.length > 2 ? "The scans are" : "The two scans are"} unchanged. Fix what the message says, then merge again.`,
        }),
      });
  }

  // A merge of this project already running (the page was reloaded, or it was started in another window).
  if (s.pid && !s.mergeJobId && !s.mergeStarting) {
    const found = Object.values(jobs).find((j) => /^merge$/i.test(j.kind) && isActive(j) && (j as Job & { project_id?: unknown }).project_id === s.pid);
    if (found) {
      const meta = found as Job & { target?: unknown; source?: unknown; scans?: unknown };
      const list = isIdList(meta.scans) ? meta.scans : typeof meta.target === "string" && typeof meta.source === "string" ? [meta.target, meta.source] : null;
      const same = !list || sameList(list, s.scans);
      useAlign.setState({
        mergeJobId: found.id,
        merged: null,
        mergeProblem: null,
        mergeCancelled: false,
        ...(same || !list ? {} : { scans: [...list], picked: keepPicksOf(s.picked, list), result: null, multi: null, alignJobId: null, alignProblem: null }),
      });
    }
  }
}

useJobs.subscribe(follow);
