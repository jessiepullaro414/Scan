/**
 * The processing studio (APP_API.md "M3 phase 2"): one `ProcessState` per scan, kept fresh by
 * the engine's `process` messages and re-read after every reconnect, plus what this window
 * started: the job of a stage, an edit or a recipe (until the state reports it), an undo /
 * redo in flight, and the plain reason of the last job that failed.
 *
 * Job progress comes from the M1 `job` messages (the jobs store); a `process` message only
 * says which job runs. When a job ends, the state is read again: the engine sends a job's
 * final message after its side effects.
 */
import { create } from "zustand";
import { ApiError, api } from "../api/http";
import type { EditBody, Job, ProcessQuality, ProcessState, Recipe, RecipeScope, StageId } from "../api/types";
import { navigate, paths } from "../lib/router";
import { isActive, isStudioJob, jobKindLabel, useJobs } from "./jobs";
import { toast } from "./toasts";

export type ProcessAction = StageId | "edit" | "recipe" | "undo" | "redo" | "jump";

/** The last job of a scan that failed or was cancelled, in words. */
export type ProcessFailure = { jobId: string; label: string; cancelled: boolean; detail: string };

export type ProcessEntry = {
  pid: string;
  sid: string;
  state: ProcessState | null;
  loading: boolean;
  /** The last read failed (the state above may be stale). */
  error: ApiError | null;
  /** A request in flight from this window. */
  action: ProcessAction | null;
  /** The job this window just started, until it ends. */
  pending: Job | null;
  failure: ProcessFailure | null;
};

type JobMeta = { key: string; pid: string; sid: string; label: string; handled: boolean };

type ProcessStore = {
  entries: Record<string, ProcessEntry | undefined>;
  /** Jobs of the studio this window knows about: which scan, and what they do in words. */
  jobMeta: Record<string, JobMeta>;
  fetch: (pid: string, sid: string) => Promise<ProcessState | null>;
  apply: (state: ProcessState) => void;
  /** Re-read every scan this window has looked at (after a reconnect). */
  refetchAll: () => void;
  runStage: (pid: string, sid: string, stage: StageId, opts?: { quality?: ProcessQuality; voxel_mm?: number }) => Promise<boolean>;
  edit: (pid: string, sid: string, body: EditBody, label: string) => Promise<boolean>;
  applyRecipe: (pid: string, sid: string, recipe: Recipe, scope: RecipeScope) => Promise<boolean>;
  undo: (pid: string, sid: string) => Promise<boolean>;
  redo: (pid: string, sid: string) => Promise<boolean>;
  /** Undo or redo until `cursor` edits are applied (a click on the history). */
  jumpTo: (pid: string, sid: string, cursor: number) => Promise<void>;
  dismissFailure: (pid: string, sid: string) => void;
};

export const processKey = (pid: string, sid: string) => `${pid}/${sid}`;

const asApiError = (e: unknown, path: string) =>
  e instanceof ApiError ? e : new ApiError(0, e instanceof Error ? e.message : String(e), null, path);

const blank = (pid: string, sid: string): ProcessEntry => ({
  pid,
  sid,
  state: null,
  loading: false,
  error: null,
  action: null,
  pending: null,
  failure: null,
});

const STAGE_LABEL: Record<StageId, string> = { track: "Track", straighten: "Straighten", fuse: "Fuse", spacetime: "Refine from the images", refine: "Refine with lasers" };

/** "Filling holes…" → "Filling holes". */
const fromBusy = (busy: string | null | undefined) => (busy ? busy.replace(/[.…\s]+$/, "") : null);

/** Is the process page of this scan on screen? (Its own panels show what happened.) */
function onPage(pid: string, sid: string): boolean {
  return location.hash.replace(/\?.*$/, "") === "#" + paths.process(pid, sid);
}

/** Put a job into the jobs store unless it knows a newer copy (a late `process` message is older). */
function adoptJob(j: Job) {
  const known = useJobs.getState().jobs[j.id];
  if (!known || (isActive(known) && (!isActive(j) || j.progress >= known.progress))) useJobs.getState().upsert(j);
}

/** The live copy of a job (the jobs store), else the one given. */
export function liveJob(j: Job | null | undefined): Job | null {
  if (!j) return null;
  return useJobs.getState().jobs[j.id] ?? j;
}

export const useProcess = create<ProcessStore>((set, get) => {
  const patch = (pid: string, sid: string, p: Partial<ProcessEntry>) =>
    set((s) => {
      const key = processKey(pid, sid);
      return { entries: { ...s.entries, [key]: { ...(s.entries[key] ?? blank(pid, sid)), ...p } } };
    });

  const remember = (pid: string, sid: string, job: Job, label: string) =>
    set((s) => {
      const ids = Object.keys(s.jobMeta);
      const jobMeta = { ...s.jobMeta, [job.id]: { key: processKey(pid, sid), pid, sid, label, handled: false } };
      if (ids.length > 60) for (const id of ids.slice(0, ids.length - 60)) delete jobMeta[id];
      return { jobMeta };
    });

  /** Start a job (stage, edit, recipe); errors become a toast with the engine's detail and hint. */
  const startJob = async (
    pid: string,
    sid: string,
    action: ProcessAction,
    label: string,
    errorTitle: string,
    call: () => Promise<Job>,
  ): Promise<boolean> => {
    const e = get().entries[processKey(pid, sid)];
    if (e?.action) return false;
    patch(pid, sid, { action, failure: null });
    try {
      const job = await call();
      remember(pid, sid, job, label);
      useJobs.getState().upsert(job);
      patch(pid, sid, { action: null, pending: isActive(job) ? job : null });
      // Show the busy state at once, even if the live socket is down.
      void get().fetch(pid, sid);
      return true;
    } catch (err) {
      patch(pid, sid, { action: null });
      toast.error(errorTitle, err);
      return false;
    }
  };

  return {
    entries: {},
    jobMeta: {},

    fetch: async (pid, sid) => {
      patch(pid, sid, { loading: true });
      try {
        const state = await api.processState(pid, sid);
        get().apply(state);
        return state;
      } catch (e) {
        patch(pid, sid, { loading: false, error: asApiError(e, `/api/projects/${pid}/scans/${sid}/process`) });
        return null;
      }
    },

    apply: (state) => {
      const { project_id: pid, scan_id: sid } = state;
      if (!pid || !sid) return;
      if (state.job) {
        adoptJob(state.job);
        if (!get().jobMeta[state.job.id]) {
          const label = fromBusy(state.busy) ?? jobKindLabel(state.job.kind);
          remember(pid, sid, state.job, label);
        }
      }
      const prev = get().entries[processKey(pid, sid)];
      let pending = prev?.pending ?? null;
      if (pending && !isActive(liveJob(pending))) pending = null;
      patch(pid, sid, { state, loading: false, error: null, pending });
    },

    refetchAll: () => {
      for (const e of Object.values(get().entries)) if (e && (e.state || e.error)) void get().fetch(e.pid, e.sid);
    },

    runStage: (pid, sid, stage, opts = {}) => {
      const label = STAGE_LABEL[stage];
      const call =
        stage === "track"
          ? () => api.processTrack(pid, sid, opts.quality ?? "full")
          : stage === "straighten"
            ? () => api.processStraighten(pid, sid)
            : stage === "refine"
              ? () => api.processRefine(pid, sid)
              : stage === "spacetime"
                ? () => api.processSpacetime(pid, sid)
                : () => api.processFuse(pid, sid, opts.voxel_mm ?? 1);
      return startJob(pid, sid, stage, label, `Couldn't start ${label}`, call);
    },

    edit: (pid, sid, body, label) => startJob(pid, sid, "edit", label, `Couldn't start “${label}”`, () => api.processEdit(pid, sid, body)),

    applyRecipe: (pid, sid, recipe, scope) =>
      startJob(pid, sid, "recipe", `Recipe “${recipe.name}”`, `Couldn't apply “${recipe.name}”`, () =>
        api.processRecipe(pid, sid, recipe.id, scope),
      ),

    undo: async (pid, sid) => {
      if (get().entries[processKey(pid, sid)]?.action) return false;
      patch(pid, sid, { action: "undo" });
      try {
        get().apply(await api.processUndo(pid, sid));
        return true;
      } catch (e) {
        toast.error("Couldn't undo", e);
        return false;
      } finally {
        patch(pid, sid, { action: null });
      }
    },

    redo: async (pid, sid) => {
      if (get().entries[processKey(pid, sid)]?.action) return false;
      patch(pid, sid, { action: "redo" });
      try {
        get().apply(await api.processRedo(pid, sid));
        return true;
      } catch (e) {
        toast.error("Couldn't redo", e);
        return false;
      } finally {
        patch(pid, sid, { action: null });
      }
    },

    jumpTo: async (pid, sid, cursor) => {
      const e = get().entries[processKey(pid, sid)];
      if (!e?.state || e.action || e.state.cursor === cursor) return;
      patch(pid, sid, { action: "jump" });
      try {
        let st: ProcessState = e.state;
        for (let guard = 0; st.cursor !== cursor && guard < 500; guard++) {
          const back = st.cursor > cursor;
          if (back ? !st.can_undo : !st.can_redo) break;
          st = back ? await api.processUndo(pid, sid) : await api.processRedo(pid, sid);
          get().apply(st);
        }
      } catch (err) {
        toast.error("Couldn't go to that step", err);
      } finally {
        patch(pid, sid, { action: null });
      }
    },

    dismissFailure: (pid, sid) => patch(pid, sid, { failure: null }),
  };
});

/** Follow the studio jobs: when one ends, record a failure, tell the user, and re-read the state. */
useJobs.subscribe((s) => {
  const { jobMeta } = useProcess.getState();
  for (const [jid, meta] of Object.entries(jobMeta)) {
    const job = s.jobs[jid];
    if (!job || meta.handled || isActive(job)) continue;
    useProcess.setState((st) => ({ jobMeta: { ...st.jobMeta, [jid]: { ...meta, handled: true } } }));
    const entry = useProcess.getState().entries[meta.key];
    const pending = entry?.pending?.id === jid ? null : (entry?.pending ?? null);
    // No toast on the scan's Process page (its panels show it), nor for "Make mesh" (the jobs store tells).
    const quiet = onPage(meta.pid, meta.sid) || !isStudioJob(job.kind);
    let failure = entry?.failure ?? null;
    if (job.state === "failed") {
      const detail = job.error || job.message || "The job failed.";
      failure = { jobId: jid, label: meta.label, cancelled: false, detail };
      if (!quiet)
        toast.error(`${meta.label} failed`, { detail, hint: null }, { label: "Open Process", run: () => navigate(paths.process(meta.pid, meta.sid)) });
    } else if (job.state === "cancelled") {
      failure = { jobId: jid, label: meta.label, cancelled: true, detail: job.message && !/^cancel/i.test(job.message) ? job.message : "Stopped before it finished." };
    } else if (job.state === "done") {
      if (failure?.jobId === jid) failure = null;
      if (!quiet) toast.success(`${meta.label}: done`, undefined, { label: "Open Process", run: () => navigate(paths.process(meta.pid, meta.sid)) });
    }
    useProcess.setState((st) => ({
      entries: { ...st.entries, [meta.key]: { ...(st.entries[meta.key] ?? blank(meta.pid, meta.sid)), pending, failure } },
    }));
    void useProcess.getState().fetch(meta.pid, meta.sid);
  }
});

/** The running job of a scan (this window's, else the one the state reports), live from the jobs store. */
export function useProcessJob(pid: string, sid: string): Job | null {
  const pending = useProcess((s) => s.entries[processKey(pid, sid)]?.pending ?? null);
  const reported = useProcess((s) => s.entries[processKey(pid, sid)]?.state?.job ?? null);
  return useJobs((s) => {
    for (const j of [pending, reported]) {
      if (!j) continue;
      const live = s.jobs[j.id] ?? j;
      if (isActive(live)) return live;
    }
    return null;
  });
}
