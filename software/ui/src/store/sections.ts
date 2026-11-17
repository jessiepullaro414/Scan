/**
 * The sections kept with each scan (APP_API.md "Sections"): read when the viewer opens a scan and again when its mesh
 * changes (to know which were cut on an earlier mesh), written on keep, rename, "mark as current" and delete. The
 * cuts themselves are not kept here: the Section tool asks for them as it needs them (pages/viewer/useSectionTool.ts).
 * Errors become a toast with the engine's detail and hint.
 */
import { create } from "zustand";
import { ApiError, api } from "../api/http";
import type { KeptSection, KeptSections, NewSection } from "../api/types";
import { toast } from "./toasts";

export type SectionsEntry = { set: KeptSections | null; loading: boolean; error: ApiError | null };

type SectionsStore = {
  entries: Record<string, SectionsEntry | undefined>;
  load: (pid: string, sid: string) => Promise<KeptSections | null>;
  /** Keep a section; the kept record, or null after a toast. */
  keep: (pid: string, sid: string, body: NewSection) => Promise<KeptSection | null>;
  rename: (pid: string, sid: string, id: string, name: string) => Promise<boolean>;
  /** Mark a kept section as cut on the current mesh (its plane stays as it is). */
  markCurrent: (pid: string, sid: string, id: string) => Promise<boolean>;
  remove: (pid: string, sid: string, id: string) => Promise<boolean>;
};

export const sectionsKey = (pid: string, sid: string) => `${pid}/${sid}`;

export const useSections = create<SectionsStore>((set, get) => {
  const patch = (pid: string, sid: string, p: Partial<SectionsEntry>) =>
    set((s) => {
      const key = sectionsKey(pid, sid);
      return { entries: { ...s.entries, [key]: { ...(s.entries[key] ?? { set: null, loading: false, error: null }), ...p } } };
    });
  /** Put a record in the list held here (replacing the one with its id), or read the list when none is held. */
  const put = (pid: string, sid: string, rec: KeptSection | null, drop?: string) => {
    const cur = get().entries[sectionsKey(pid, sid)]?.set;
    if (!cur) {
      void get().load(pid, sid);
      return;
    }
    let sections = cur.sections.filter((x) => x.id !== drop);
    if (rec) sections = sections.some((x) => x.id === rec.id) ? sections.map((x) => (x.id === rec.id ? rec : x)) : [...sections, rec];
    patch(pid, sid, { set: { ...cur, sections } });
  };

  return {
    entries: {},

    load: async (pid, sid) => {
      patch(pid, sid, { loading: true });
      try {
        const s = await api.sections(pid, sid);
        patch(pid, sid, { set: s, loading: false, error: null });
        return s;
      } catch (e) {
        patch(pid, sid, { loading: false, error: e instanceof ApiError ? e : new ApiError(0, String(e), null, "sections") });
        return null;
      }
    },

    keep: async (pid, sid, body) => {
      try {
        const rec = await api.keepSection(pid, sid, body);
        put(pid, sid, rec);
        return rec;
      } catch (e) {
        toast.error("Couldn't keep the section", e);
        return null;
      }
    },

    rename: async (pid, sid, id, name) => {
      try {
        put(pid, sid, await api.updateSection(pid, sid, id, { name }));
        return true;
      } catch (e) {
        toast.error("Couldn't rename the section", e);
        return false;
      }
    },

    markCurrent: async (pid, sid, id) => {
      try {
        put(pid, sid, await api.updateSection(pid, sid, id, { recut: true }));
        return true;
      } catch (e) {
        toast.error("Couldn't mark the section as current", e);
        return false;
      }
    },

    remove: async (pid, sid, id) => {
      try {
        await api.deleteSection(pid, sid, id);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 404)) {
          toast.error("Couldn't remove the section", e);
          return false;
        }
      }
      put(pid, sid, null, id);
      return true;
    },
  };
});
