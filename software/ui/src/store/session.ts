/** The scan session: state machine, the 10 Hz status, and the scan controls. */
import { create } from "zustand";
import { api } from "../api/http";
import type { Scan, ScanMode, SessionState, StatusMsg } from "../api/types";
import { useLive } from "./live";
import { useProjects } from "./projects";
import { sameSession } from "./sameSession";
import { toast } from "./toasts";

export type SessionAction = "start" | "pause" | "resume" | "stop" | "rewind";

type SessionStore = {
  session: SessionState | null;
  status: StatusMsg | null;
  /** performance.now() of the last status message. */
  statusAt: number;
  action: SessionAction | null;
  /** The scan saved by the last stop (drives the "Make mesh" call to action). */
  lastStopped: { scan: Scan; projectId: string } | null;
  apply: (s: SessionState) => void;
  applyStatus: (m: StatusMsg) => void;
  fetch: () => Promise<void>;
  start: (args: { projectId: string; mode: ScanMode; name?: string }) => Promise<boolean>;
  pause: () => Promise<void>;
  resume: () => Promise<void>;
  rewind: (seconds?: number) => Promise<void>;
  stop: () => Promise<void>;
  clearLastStopped: () => void;
};

const IDLE: SessionState = { state: "idle", project_id: null, scan_id: null, mode: "markerless", elapsed_s: 0, frames: 0 };

export const useSession = create<SessionStore>((set, get) => {
  const run = async <T>(action: SessionAction, fn: () => Promise<T>, failTitle: string): Promise<T | null> => {
    if (get().action) return null;
    set({ action });
    try {
      const r = await fn();
      set({ action: null });
      return r;
    } catch (e) {
      set({ action: null });
      toast.error(failTitle, e);
      return null;
    }
  };

  return {
    session: null,
    status: null,
    statusAt: 0,
    action: null,
    lastStopped: null,

    apply: (s) => {
      const prev = get().session;
      // A new scan started (here or from the scanner's trigger): clear the old live model.
      if (s.scan_id && s.scan_id !== prev?.scan_id && s.state === "scanning") {
        useLive.getState().resetScan();
        set({ lastStopped: null });
      }
      // Every status message brings the session again: keep the old object when nothing changed.
      if (!sameSession(prev, s)) set({ session: s });
    },

    applyStatus: (m) => {
      get().apply(m.session);
      set({ status: m, statusAt: performance.now() });
    },

    fetch: async () => {
      try {
        get().apply(await api.session());
      } catch {
        /* reported by the connection banner */
      }
    },

    start: async ({ projectId, mode, name }) => {
      const s = await run(
        "start",
        () => api.startScan({ project_id: projectId, mode, ...(name?.trim() ? { name: name.trim() } : {}) }),
        "Couldn't start the scan",
      );
      if (!s) return false;
      useLive.getState().resetScan();
      set({ lastStopped: null });
      get().apply(s);
      return true;
    },

    pause: async () => {
      const s = await run("pause", api.pauseScan, "Couldn't pause");
      if (s) get().apply(s);
    },

    resume: async () => {
      const s = await run("resume", api.resumeScan, "Couldn't resume");
      if (s) get().apply(s);
    },

    rewind: async (seconds = 2) => {
      const s = await run("rewind", () => api.rewindScan(seconds), "Couldn't rewind");
      if (s) {
        get().apply(s);
        toast.info(`Rewound ${seconds} s`, "The last stretch is cut from the model. The raw recording keeps it, marked as cut.");
      }
    },

    stop: async () => {
      const projectId = get().session?.project_id ?? null;
      const r = await run("stop", api.stopScan, "Couldn't stop the scan");
      if (!r) return;
      const pid = projectId ?? get().session?.project_id ?? null;
      set({
        lastStopped: pid ? { scan: r.scan, projectId: pid } : null,
        session: { ...(get().session ?? IDLE), state: "idle" },
      });
      useLive.getState().setGhost(null);
      if (pid) void useProjects.getState().fetchProject(pid);
      void get().fetch();
    },

    clearLastStopped: () => set({ lastStopped: null }),
  };
});

export const isLive = (s: SessionState | null | undefined) => s?.state === "scanning" || s?.state === "paused";
