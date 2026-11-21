/**
 * The reference models of each project and the comparison of each scan with one (FEATURE_PLAN I2; APP_API.md
 * "References and the comparison with one"): the list read when the viewer opens a scan, a file added (its refusal
 * kept to show in the panel, in the engine's words), renamed and removed; the scan's comparison read, started with a
 * reference, its units settled, aligned (a best fit is a job: its progress comes from the jobs store, and the
 * comparison is read again when it ends) and removed. The deviation and the answer for a tolerance are not kept here:
 * the viewer asks for them as it needs them (pages/viewer/useComparison.ts). Other errors become a toast with the
 * engine's detail and hint.
 */
import { create } from "zustand";
import { ApiError, api } from "../api/http";
import type { Comparison, ComparisonAlignRequest, Job, ReferenceModel } from "../api/types";
import { useJobs } from "./jobs";
import { toast } from "./toasts";

export type RefListEntry = { refs: ReferenceModel[] | null; loading: boolean; error: ApiError | null };
export type ComparisonEntry = { comparison: Comparison | null; loaded: boolean; busy: boolean; error: ApiError | null };

type ReferencesStore = {
  lists: Record<string, RefListEntry | undefined>;
  comparisons: Record<string, ComparisonEntry | undefined>;
  loadRefs: (pid: string) => Promise<void>;
  /** Add a file to the project: the new reference, or the engine's refusal (not toasted: the panel shows it). */
  upload: (pid: string, file: Blob, name: string) => Promise<ReferenceModel | ApiError>;
  rename: (pid: string, rid: string, name: string) => Promise<boolean>;
  /** Remove a reference (and, on the engine, every comparison that uses it). */
  removeRef: (pid: string, rid: string) => Promise<boolean>;
  loadComparison: (pid: string, sid: string) => Promise<Comparison | null>;
  /** Compare the scan with a reference (replaces its comparison). */
  start: (pid: string, sid: string, rid: string, tolerance?: number) => Promise<Comparison | null>;
  units: (pid: string, sid: string, action: "apply" | "keep") => Promise<Comparison | null>;
  /** Align: the job of a best fit (followed through the jobs store), or null when it was answered at once (or failed). */
  align: (pid: string, sid: string, body: ComparisonAlignRequest) => Promise<Job | null>;
  remove: (pid: string, sid: string) => Promise<boolean>;
  /** Put a comparison read elsewhere (a finished alignment job's) into the store. */
  put: (pid: string, sid: string, c: Comparison | null) => void;
};

export const comparisonKey = (pid: string, sid: string) => `${pid}/${sid}`;
const asError = (e: unknown, path: string) => (e instanceof ApiError ? e : new ApiError(0, String(e), null, path));

export const useReferences = create<ReferencesStore>((set, get) => {
  const patchList = (pid: string, p: Partial<RefListEntry>) =>
    set((s) => ({ lists: { ...s.lists, [pid]: { ...(s.lists[pid] ?? { refs: null, loading: false, error: null }), ...p } } }));
  const patchCmp = (pid: string, sid: string, p: Partial<ComparisonEntry>) =>
    set((s) => {
      const key = comparisonKey(pid, sid);
      return { comparisons: { ...s.comparisons, [key]: { ...(s.comparisons[key] ?? { comparison: null, loaded: false, busy: false, error: null }), ...p } } };
    });
  /** Run a change of the comparison: the answer kept, an error toasted. */
  const change = async (pid: string, sid: string, what: string, fn: () => Promise<Comparison>): Promise<Comparison | null> => {
    patchCmp(pid, sid, { busy: true });
    try {
      const c = await fn();
      patchCmp(pid, sid, { comparison: c, loaded: true, busy: false, error: null });
      return c;
    } catch (e) {
      patchCmp(pid, sid, { busy: false });
      toast.error(what, e);
      return null;
    }
  };

  return {
    lists: {},
    comparisons: {},

    loadRefs: async (pid) => {
      patchList(pid, { loading: true });
      try {
        const r = await api.references(pid);
        patchList(pid, { refs: r.references, loading: false, error: null });
      } catch (e) {
        patchList(pid, { loading: false, error: asError(e, "references") });
      }
    },

    upload: async (pid, file, name) => {
      try {
        const rec = await api.uploadReference(pid, file, name);
        const cur = get().lists[pid]?.refs ?? [];
        patchList(pid, { refs: [...cur.filter((r) => r.id !== rec.id), rec] });
        return rec;
      } catch (e) {
        return asError(e, "references");
      }
    },

    rename: async (pid, rid, name) => {
      try {
        const rec = await api.renameReference(pid, rid, name);
        patchList(pid, { refs: (get().lists[pid]?.refs ?? []).map((r) => (r.id === rid ? rec : r)) });
        return true;
      } catch (e) {
        toast.error("Couldn't rename the CAD model", e);
        return false;
      }
    },

    removeRef: async (pid, rid) => {
      try {
        await api.deleteReference(pid, rid);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 404)) {
          toast.error("Couldn't remove the CAD model", e);
          return false;
        }
      }
      patchList(pid, { refs: (get().lists[pid]?.refs ?? []).filter((r) => r.id !== rid) });
      // the engine removed the comparisons that used it
      for (const [key, c] of Object.entries(get().comparisons))
        if (key.startsWith(`${pid}/`) && c?.comparison?.reference_id === rid) patchCmp(pid, key.slice(pid.length + 1), { comparison: null });
      return true;
    },

    loadComparison: async (pid, sid) => {
      try {
        const r = await api.comparison(pid, sid);
        patchCmp(pid, sid, { comparison: r.comparison, loaded: true, error: null });
        return r.comparison;
      } catch (e) {
        patchCmp(pid, sid, { loaded: true, error: asError(e, "comparison") });
        return null;
      }
    },

    start: (pid, sid, rid, tolerance) => change(pid, sid, "Couldn't compare with this CAD model", () => api.startComparison(pid, sid, rid, tolerance)),

    units: (pid, sid, action) => change(pid, sid, "Couldn't settle the units", () => api.comparisonUnits(pid, sid, action)),

    align: async (pid, sid, body) => {
      patchCmp(pid, sid, { busy: true });
      try {
        const r = await api.alignComparison(pid, sid, body);
        if (r.job) {
          useJobs.getState().upsert(r.job);
          const cur = get().comparisons[comparisonKey(pid, sid)]?.comparison ?? null;
          patchCmp(pid, sid, { busy: false, comparison: cur ? { ...cur, aligning: r.job.id } : cur });
          return r.job;
        }
        patchCmp(pid, sid, { busy: false, comparison: r.comparison, loaded: true });
        return null;
      } catch (e) {
        patchCmp(pid, sid, { busy: false });
        toast.error("Couldn't align the scan with the CAD model", e);
        return null;
      }
    },

    remove: async (pid, sid) => {
      try {
        await api.removeComparison(pid, sid);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 404)) {
          toast.error("Couldn't remove the comparison", e);
          return false;
        }
      }
      patchCmp(pid, sid, { comparison: null, loaded: true });
      return true;
    },

    put: (pid, sid, c) => patchCmp(pid, sid, { comparison: c, loaded: true }),
  };
});
