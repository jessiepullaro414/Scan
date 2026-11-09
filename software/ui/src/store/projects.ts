/** Projects (list and details) with create / rename / delete. */
import { create } from "zustand";
import { ApiError, api } from "../api/http";
import type { Project, ProjectSummary } from "../api/types";
import { toast } from "./toasts";
import { useUi } from "./ui";

type ProjectsStore = {
  list: ProjectSummary[] | null;
  listLoading: boolean;
  listError: ApiError | null;
  details: Record<string, Project | undefined>;
  detailLoading: Record<string, boolean | undefined>;
  detailError: Record<string, ApiError | undefined>;
  fetchList: () => Promise<void>;
  fetchProject: (pid: string) => Promise<Project | null>;
  create: (name: string) => Promise<Project | null>;
  rename: (pid: string, name: string) => Promise<boolean>;
  remove: (pid: string) => Promise<boolean>;
};

const asApiError = (e: unknown, path: string) =>
  e instanceof ApiError ? e : new ApiError(0, e instanceof Error ? e.message : String(e), null, path);

function summarize(p: Project): ProjectSummary {
  return {
    id: p.id,
    name: p.name,
    created: p.created,
    modified: p.modified,
    n_scans: p.n_scans ?? p.scans.length,
    thumbnail_url: p.thumbnail_url ?? null,
  };
}

export const useProjects = create<ProjectsStore>((set, get) => ({
  list: null,
  listLoading: false,
  listError: null,
  details: {},
  detailLoading: {},
  detailError: {},

  fetchList: async () => {
    set({ listLoading: true });
    try {
      const list = await api.projects();
      set({ list, listLoading: false, listError: null });
      // Forget a "current project" that no longer exists.
      const cur = useUi.getState().currentProjectId;
      if (cur && !list.some((p) => p.id === cur)) useUi.getState().setCurrentProject(null);
    } catch (e) {
      set({ listLoading: false, listError: asApiError(e, "/api/projects") });
    }
  },

  fetchProject: async (pid) => {
    set((s) => ({ detailLoading: { ...s.detailLoading, [pid]: true } }));
    try {
      const p = await api.project(pid);
      set((s) => ({
        details: { ...s.details, [pid]: p },
        detailLoading: { ...s.detailLoading, [pid]: false },
        detailError: { ...s.detailError, [pid]: undefined },
        list: s.list ? s.list.map((x) => (x.id === pid ? summarize(p) : x)) : s.list,
      }));
      return p;
    } catch (e) {
      set((s) => ({
        detailLoading: { ...s.detailLoading, [pid]: false },
        detailError: { ...s.detailError, [pid]: asApiError(e, `/api/projects/${pid}`) },
      }));
      return null;
    }
  },

  create: async (name) => {
    try {
      const p = await api.createProject(name);
      set((s) => ({
        details: { ...s.details, [p.id]: p },
        list: [summarize(p), ...(s.list ?? []).filter((x) => x.id !== p.id)],
      }));
      return p;
    } catch (e) {
      toast.error("Couldn't create the project", e);
      return null;
    }
  },

  rename: async (pid, name) => {
    try {
      const p = await api.renameProject(pid, name);
      set((s) => ({
        details: { ...s.details, [pid]: p },
        list: s.list ? s.list.map((x) => (x.id === pid ? summarize(p) : x)) : s.list,
      }));
      return true;
    } catch (e) {
      toast.error("Couldn't rename the project", e);
      return false;
    }
  },

  remove: async (pid) => {
    try {
      await api.deleteProject(pid);
      set((s) => {
        const details = { ...s.details };
        delete details[pid];
        return { details, list: s.list ? s.list.filter((x) => x.id !== pid) : s.list };
      });
      if (useUi.getState().currentProjectId === pid) useUi.getState().setCurrentProject(null);
      void get().fetchList();
      return true;
    } catch (e) {
      toast.error("Couldn't delete the project", e);
      return false;
    }
  },
}));
