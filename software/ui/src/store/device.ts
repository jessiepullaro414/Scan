/** The scanner device: source, connection, calibration and emitters. */
import { create } from "zustand";
import { ApiError, api } from "../api/http";
import type { ConnectBody, ConnectSource, DeviceState, Emitters } from "../api/types";
import { isActive, useJobs } from "./jobs";
import { toast } from "./toasts";

type DeviceStore = {
  device: DeviceState | null;
  loading: boolean;
  error: ApiError | null;
  pending: "connect" | "disconnect" | null;
  pendingSource: ConnectSource | null;
  /** Job building the simulator demo after a 202 from connect. */
  simJobId: string | null;
  fetch: () => Promise<void>;
  apply: (d: DeviceState) => void;
  connect: (body: ConnectBody) => Promise<boolean>;
  disconnect: () => Promise<boolean>;
  setEmitters: (e: Partial<Emitters>) => Promise<void>;
};

export const SOURCE_LABEL: Record<DeviceState["source"], string> = {
  none: "No device",
  camera: "Camera",
  replay: "Replay",
  sim: "Simulator",
};

export const useDevice = create<DeviceStore>((set, get) => ({
  device: null,
  loading: false,
  error: null,
  pending: null,
  pendingSource: null,
  simJobId: null,

  fetch: async () => {
    set({ loading: true });
    try {
      const device = await api.device();
      set({ device, loading: false, error: null });
    } catch (e) {
      set({ loading: false, error: e instanceof ApiError ? e : null });
    }
  },

  apply: (device) => set({ device }),

  connect: async (body) => {
    set({ pending: "connect", pendingSource: body.source });
    try {
      const cur = get().device;
      // Switching sources: release the current one first (emitters off, threads stopped).
      if (cur?.connected && cur.source !== body.source) {
        set({ device: await api.disconnect() });
      }
      const r = await api.connect(body);
      if (r.kind === "job") {
        useJobs.getState().upsert(r.job);
        set({ simJobId: r.job.id, pending: null, pendingSource: null });
        void get().fetch();
        return true;
      }
      set({ device: r.device, pending: null, pendingSource: null });
      return true;
    } catch (e) {
      set({ pending: null, pendingSource: null });
      toast.error(body.source === "sim" ? "Couldn't start the simulator" : body.source === "replay" ? "Couldn't start the replay" : "Couldn't connect the camera", e);
      void get().fetch();
      return false;
    }
  },

  disconnect: async () => {
    set({ pending: "disconnect" });
    try {
      const device = await api.disconnect();
      set({ device, pending: null });
      return true;
    } catch (e) {
      set({ pending: null });
      toast.error("Couldn't disconnect", e);
      return false;
    }
  },

  setEmitters: async (e) => {
    const cur = get().device;
    if (cur?.pico) {
      // Optimistic: sliders stay where the user put them.
      set({ device: { ...cur, pico: { ...cur.pico, emitters: { ...cur.pico.emitters, ...e } } } });
    }
    try {
      const device = await api.setEmitters(e);
      set({ device });
    } catch (err) {
      toast.error("Couldn't set the emitters", err);
      void get().fetch();
    }
  },
}));

// When the simulator demo job finishes, the device connects itself: refresh and report.
useJobs.subscribe((s, prev) => {
  const jid = useDevice.getState().simJobId;
  if (!jid) return;
  const job = s.jobs[jid];
  const before = prev.jobs[jid];
  if (!job || job === before || isActive(job)) return;
  useDevice.setState({ simJobId: null });
  void useDevice.getState().fetch();
  if (job.state === "done") toast.success("Simulator demo ready", "The simulated scanner is connected. Start a scan!");
  else if (job.state === "failed")
    toast.error("Couldn't build the simulator demo", { detail: job.error || job.message || "The job failed.", hint: "Check the engine log, then try again." });
});
