/**
 * The calibration session (APP_API.md "M2"): the CalibSession, the latest 10 Hz CalibLive, the
 * calibration previews (`calib_left` / `calib_right`), and the session actions.
 *
 * High-rate data (`live`, `previews`) is read with selectors on primitives, or with
 * `useCalib.subscribe` + refs in the overlays, so a message never re-renders the whole page.
 * `captureSeq` counts the views captured in this page's life: the capture flash and click
 * follow it, whether the view came from Space, the trigger or auto-capture.
 */
import { create } from "zustand";
import { ApiError, api, sentence } from "../api/http";
import type { CalibKind, CalibLive, CalibSession, CalibView, PreviewHeader, SaveReply } from "../api/types";
import { isActive, useJobs } from "./jobs";
import type { PreviewFrame } from "./live";
import { useSetup } from "./setup";
import { toast } from "./toasts";

export type CalibCamera = "calib_left" | "calib_right";
export type CalibAction =
  | "start"
  | "capture"
  | "delete"
  | "auto"
  | "imu_start"
  | "imu_stop"
  | "solve"
  | "continue"
  | "save"
  | "cancel"
  | "remove_bad";

/** A session that is under way (views, a recording or an unsaved result). */
export const isSessionActive = (s: CalibSession | null | undefined) =>
  !!s && s.state !== "idle" && s.state !== "saved";

type CalibStore = {
  session: CalibSession | null;
  loaded: boolean;
  fetchError: ApiError | null;
  live: CalibLive | null;
  /** performance.now() of the last `calib` message. */
  liveAt: number;
  previews: Record<CalibCamera, PreviewFrame | null>;
  action: CalibAction | null;
  /** Why the last start failed (shown on the step, with its hint and a way out). */
  startError: { kind: CalibKind; err: ApiError } | null;
  /** Why the last manual capture was refused (409), shown briefly next to the button. */
  refusal: { text: string; hint: string | null; at: number } | null;
  captureSeq: number;
  lastCapture: { view: CalibView; at: number } | null;
  fetch: () => Promise<void>;
  applySession: (s: CalibSession) => void;
  applyLive: (m: CalibLive) => void;
  pushPreview: (h: PreviewHeader, jpeg: Uint8Array) => void;
  clearPreviews: () => void;
  clearStartError: () => void;
  start: (kind: CalibKind) => Promise<boolean>;
  capture: () => Promise<void>;
  deleteView: (index: number) => Promise<void>;
  undoLast: () => Promise<void>;
  setAuto: (enabled: boolean) => Promise<void>;
  imuStart: () => Promise<void>;
  imuStop: () => Promise<void>;
  solve: () => Promise<boolean>;
  continueCapture: () => Promise<boolean>;
  save: (force?: boolean) => Promise<SaveReply | null>;
  cancel: () => Promise<boolean>;
  /** Advice `remove_bad_views`: drop the flagged views, then solve again. */
  removeBadViewsAndSolve: () => Promise<void>;
  /** Advice `start_over`: cancel (the views stay on disk), then start the same kind. */
  startOver: () => Promise<void>;
};

const asApiError = (e: unknown, path: string) =>
  e instanceof ApiError ? e : new ApiError(0, e instanceof Error ? e.message : String(e), null, path);

// Object URLs per camera, newest last; the newest few stay alive while an <img> may show them.
const urlHistory: Record<CalibCamera, string[]> = { calib_left: [], calib_right: [] };

function retire(camera: CalibCamera, url: string) {
  const h = urlHistory[camera];
  h.push(url);
  while (h.length > 3) {
    const old = h.shift();
    if (old) URL.revokeObjectURL(old);
  }
}

/** Set while this window cancels, so the "session ended" notice is only for outside causes. */
let cancelling = false;

export const useCalib = create<CalibStore>((set, get) => {
  const run = async <T>(action: CalibAction, fn: () => Promise<T>, failTitle: string | null): Promise<T | null> => {
    if (get().action) return null;
    set({ action });
    try {
      const r = await fn();
      set({ action: null });
      return r;
    } catch (e) {
      set({ action: null });
      if (failTitle) toast.error(failTitle, e);
      return null;
    }
  };

  return {
    session: null,
    loaded: false,
    fetchError: null,
    live: null,
    liveAt: 0,
    previews: { calib_left: null, calib_right: null },
    action: null,
    startError: null,
    refusal: null,
    captureSeq: 0,
    lastCapture: null,

    fetch: async () => {
      try {
        get().applySession(await api.calibration());
        set({ fetchError: null });
      } catch (e) {
        set({ fetchError: asApiError(e, "/api/calibration"), loaded: true });
      }
    },

    applySession: (s) => {
      const prev = get().session;
      const same = !!prev && !!prev.id && prev.id === s.id;
      if (same && prev && s.views.length > prev.views.length) {
        const newest = s.views.reduce((a, b) => (b.index > a.index ? b : a), s.views[0]);
        set({ captureSeq: get().captureSeq + 1, lastCapture: { view: newest, at: performance.now() }, refusal: null });
      }
      if (!same || s.state === "idle") {
        // Another session (or none): the old live data and previews don't apply any more.
        if (prev && (prev.id !== s.id || s.state === "idle")) {
          get().clearPreviews();
          set({ live: null, refusal: null });
        }
      }
      if (prev && isSessionActive(prev) && s.state === "idle" && !cancelling) {
        toast.info(
          "The calibration session was closed",
          "The device disconnected, or the session was cancelled from another window. The captured views are kept on disk.",
        );
      }
      set({ session: s, loaded: true });
    },

    applyLive: (m) => set({ live: m, liveAt: performance.now() }),

    pushPreview: (h, jpeg) => {
      const camera: CalibCamera = h.camera === "calib_right" ? "calib_right" : "calib_left";
      const url = URL.createObjectURL(new Blob([jpeg as BlobPart], { type: "image/jpeg" }));
      retire(camera, url);
      set((s) => ({ previews: { ...s.previews, [camera]: { url, w: h.w, h: h.h, seq: h.seq, at: performance.now() } } }));
    },

    clearPreviews: () => {
      for (const cam of ["calib_left", "calib_right"] as const) {
        for (const u of urlHistory[cam]) URL.revokeObjectURL(u);
        urlHistory[cam] = [];
      }
      set({ previews: { calib_left: null, calib_right: null } });
    },

    clearStartError: () => set({ startError: null }),

    start: async (kind) => {
      if (get().action) return false;
      set({ action: "start", startError: null });
      try {
        const s = await api.calibStart(kind);
        set({ action: null });
        get().applySession(s);
        return true;
      } catch (e) {
        set({ action: null, startError: { kind, err: asApiError(e, "/api/calibration/start") } });
        return false;
      }
    },

    capture: async () => {
      if (get().action) return;
      set({ action: "capture" });
      try {
        const r = await api.calibCapture();
        set({ action: null });
        get().applySession(r.session);
      } catch (e) {
        set({ action: null });
        if (e instanceof ApiError && e.status === 409) {
          set({ refusal: { text: sentence(e.detail), hint: e.hint ? sentence(e.hint) : null, at: performance.now() } });
        } else toast.error("Couldn't capture the view", e);
      }
    },

    deleteView: async (index) => {
      const s = await run("delete", () => api.calibDeleteView(index), "Couldn't delete the view");
      if (s) get().applySession(s);
    },

    undoLast: async () => {
      const views = get().session?.views ?? [];
      if (!views.length) return;
      const last = views.reduce((a, b) => (b.index > a.index ? b : a), views[0]);
      await get().deleteView(last.index);
    },

    setAuto: async (enabled) => {
      const cur = get().session;
      if (cur) set({ session: { ...cur, auto: enabled } }); // optimistic: the switch moves at once
      const s = await run("auto", () => api.calibAuto(enabled), enabled ? "Couldn't turn auto-capture on" : "Couldn't turn auto-capture off");
      if (s) get().applySession(s);
      else void get().fetch();
    },

    imuStart: async () => {
      const s = await run("imu_start", api.calibImuStart, "Couldn't start the IMU recording");
      if (s) get().applySession(s);
    },

    imuStop: async () => {
      const s = await run("imu_stop", api.calibImuStop, "Couldn't stop the IMU recording");
      if (s) get().applySession(s);
    },

    solve: async () => {
      const job = await run("solve", api.calibSolve, "Couldn't start the calibration");
      if (!job) return false;
      useJobs.getState().upsert(job);
      const cur = get().session;
      // The engine says so too (`calib_session`); this keeps the screen from flickering meanwhile.
      if (cur && cur.state !== "result") set({ session: { ...cur, state: "solving", job_id: job.id } });
      return true;
    },

    continueCapture: async () => {
      const s = await run("continue", api.calibContinue, "Couldn't go back to capturing");
      if (s) get().applySession(s);
      return !!s;
    },

    save: async (force) => {
      const r = await run("save", () => api.calibSave(force), "Couldn't save the calibration");
      if (!r) return null;
      get().applySession(r.session);
      useSetup.getState().apply(r.setup);
      return r;
    },

    cancel: async () => {
      cancelling = true;
      try {
        const s = await run("cancel", api.calibCancel, "Couldn't cancel the session");
        if (s) get().applySession(s);
        return !!s;
      } finally {
        cancelling = false;
      }
    },

    removeBadViewsAndSolve: async () => {
      if (get().action) return;
      set({ action: "remove_bad" });
      try {
        let s = get().session;
        if (!s) return;
        const bad = s.views.filter((v) => v.bad).map((v) => v.index);
        const fromResult = s.result?.per_view.filter((p) => p.bad).map((p) => p.index) ?? [];
        const drop = [...new Set([...bad, ...fromResult])].sort((a, b) => b - a);
        if (s.state === "result") {
          s = await api.calibContinue();
          get().applySession(s);
        }
        for (const i of drop) {
          if (!s.views.some((v) => v.index === i)) continue;
          s = await api.calibDeleteView(i);
          get().applySession(s);
        }
        set({ action: null });
        await get().solve();
      } catch (e) {
        toast.error("Couldn't drop the bad views", e);
        void get().fetch();
      } finally {
        if (get().action === "remove_bad") set({ action: null });
      }
    },

    startOver: async () => {
      const kind = get().session?.kind;
      if (!kind) return;
      if (await get().cancel()) await get().start(kind);
    },
  };
});

// The solve job finished: make sure the result shows even without the live socket.
useJobs.subscribe((s, prev) => {
  const jid = useCalib.getState().session?.job_id;
  if (!jid) return;
  const job = s.jobs[jid];
  if (!job || job === prev.jobs[jid] || isActive(job)) return;
  void useCalib.getState().fetch();
  if (job.state === "failed" && !useCalib.getState().session?.result)
    toast.error("The calibration didn't finish", { detail: job.error || job.message || "The job failed.", hint: "Your views are kept: try again." });
});
