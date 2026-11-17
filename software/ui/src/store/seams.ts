/**
 * The seams kept with each scan (APP_API.md "Flush along a seam"): read when the viewer opens a scan and again when
 * its mesh changes (to know which were measured on an earlier mesh), written on keep, rename, "mark as current" and
 * delete. The flush itself is not kept here: the Flush tool asks for it as it needs it (pages/viewer/useFlushTool.ts).
 * Errors become a toast with the engine's detail and hint.
 */
import { create } from "zustand";
import { ApiError, api } from "../api/http";
import type { KeptSeam, KeptSeams, NewSeam } from "../api/types";
import { toast } from "./toasts";

export type SeamsEntry = { set: KeptSeams | null; loading: boolean; error: ApiError | null };

type SeamsStore = {
  entries: Record<string, SeamsEntry | undefined>;
  load: (pid: string, sid: string) => Promise<KeptSeams | null>;
  /** Keep a seam; the kept record, or null after a toast. */
  keep: (pid: string, sid: string, body: NewSeam) => Promise<KeptSeam | null>;
  rename: (pid: string, sid: string, id: string, name: string) => Promise<boolean>;
  /** Mark a kept seam as measured on the current mesh (its clicks stay as they are). */
  markCurrent: (pid: string, sid: string, id: string) => Promise<boolean>;
  remove: (pid: string, sid: string, id: string) => Promise<boolean>;
};

export const seamsKey = (pid: string, sid: string) => `${pid}/${sid}`;

export const useSeams = create<SeamsStore>((set, get) => {
  const patch = (pid: string, sid: string, p: Partial<SeamsEntry>) =>
    set((s) => {
      const key = seamsKey(pid, sid);
      return { entries: { ...s.entries, [key]: { ...(s.entries[key] ?? { set: null, loading: false, error: null }), ...p } } };
    });
  /** Put a record in the list held here (replacing the one with its id), or read the list when none is held. */
  const put = (pid: string, sid: string, rec: KeptSeam | null, drop?: string) => {
    const cur = get().entries[seamsKey(pid, sid)]?.set;
    if (!cur) {
      void get().load(pid, sid);
      return;
    }
    let seams = cur.seams.filter((x) => x.id !== drop);
    if (rec) seams = seams.some((x) => x.id === rec.id) ? seams.map((x) => (x.id === rec.id ? rec : x)) : [...seams, rec];
    patch(pid, sid, { set: { ...cur, seams } });
  };

  return {
    entries: {},

    load: async (pid, sid) => {
      patch(pid, sid, { loading: true });
      try {
        const s = await api.seams(pid, sid);
        patch(pid, sid, { set: s, loading: false, error: null });
        return s;
      } catch (e) {
        patch(pid, sid, { loading: false, error: e instanceof ApiError ? e : new ApiError(0, String(e), null, "seams") });
        return null;
      }
    },

    keep: async (pid, sid, body) => {
      try {
        const rec = await api.keepSeam(pid, sid, body);
        put(pid, sid, rec);
        return rec;
      } catch (e) {
        toast.error("Couldn't keep the seam", e);
        return null;
      }
    },

    rename: async (pid, sid, id, name) => {
      try {
        put(pid, sid, await api.updateSeam(pid, sid, id, { name }));
        return true;
      } catch (e) {
        toast.error("Couldn't rename the seam", e);
        return false;
      }
    },

    markCurrent: async (pid, sid, id) => {
      try {
        put(pid, sid, await api.updateSeam(pid, sid, id, { mark_current: true }));
        return true;
      } catch (e) {
        toast.error("Couldn't mark the seam as current", e);
        return false;
      }
    },

    remove: async (pid, sid, id) => {
      try {
        await api.deleteSeam(pid, sid, id);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 404)) {
          toast.error("Couldn't remove the seam", e);
          return false;
        }
      }
      put(pid, sid, null, id);
      return true;
    },
  };
});
