/**
 * The setup wizard (APP_API.md "M2"): the SetupState of the current target, the self-test and
 * printables jobs, the devices probe with the camera settings, and the print scale check.
 *
 * The engine pushes a `setup` message whenever the state changes (self-test, print check,
 * save, launch check, connect); after a reconnect `fetch()` re-reads it.
 */
import { create } from "zustand";
import { ApiError, api } from "../api/http";
import type {
  CameraSettingsBody,
  DevicesProbe,
  Job,
  OpenFolderReply,
  Paper,
  Printables,
  PrintCheck,
  SelfTestResult,
  Settings,
  SetupState,
  SetupStep,
  StepId,
} from "../api/types";
import { isActive, useJobs } from "./jobs";
import { toast } from "./toasts";

export type CameraConfig = { index: number | null; swap_lr: boolean };

type SetupStore = {
  setup: SetupState | null;
  loading: boolean;
  error: ApiError | null;
  fetch: () => Promise<void>;
  apply: (s: SetupState) => void;
  dismiss: (dismissed: boolean) => Promise<boolean>;

  selftestJobId: string | null;
  /** The self-test that just finished here (SetupState.selftest catches up with the `setup` message). */
  selftestResult: SelfTestResult | null;
  selftestStarting: boolean;
  runSelftest: () => Promise<boolean>;

  devices: DevicesProbe | null;
  devicesLoading: boolean;
  devicesError: ApiError | null;
  probeDevices: () => Promise<void>;
  camera: CameraConfig | null;
  /** The config file Studio writes (Settings.effective.config_path), once known. */
  configPath: string | null;
  cameraPending: "index" | "swap" | null;
  /** The camera settings changed while a camera was connected: reconnect to apply them. */
  cameraChanged: boolean;
  fetchCameraConfig: () => Promise<void>;
  setCamera: (body: CameraSettingsBody) => Promise<boolean>;
  clearCameraChanged: () => void;

  printables: Printables | null;
  printablesError: ApiError | null;
  printJobId: string | null;
  printStarting: boolean;
  fetchPrintables: () => Promise<void>;
  generatePrintables: (paper: Paper) => Promise<boolean>;
  openFolder: () => Promise<OpenFolderReply | null>;

  /** The latest print-check reply (SetupState.print_check is null for the simulator). */
  printCheck: PrintCheck | null;
  printCheckPending: "check" | "apply" | null;
  checkPrint: (measured_mm: number, apply?: boolean) => Promise<PrintCheck | null>;
};

const asApiError = (e: unknown, path: string) =>
  e instanceof ApiError ? e : new ApiError(0, e instanceof Error ? e.message : String(e), null, path);

function cameraConfigOf(settings: Settings): CameraConfig | null {
  const cam = (settings as { camera?: { index?: unknown; swap_lr?: unknown } }).camera;
  if (!cam || typeof cam !== "object") return null;
  return { index: typeof cam.index === "number" ? cam.index : null, swap_lr: cam.swap_lr === true };
}

/** The config file the engine reads and writes (`Settings.effective.config_path`), if it says. */
export function configPathOf(settings: Settings): string | null {
  const p = (settings as { effective?: { config_path?: unknown } }).effective?.config_path;
  return typeof p === "string" && p ? p : null;
}

function isSelfTestResult(r: unknown): r is SelfTestResult {
  return !!r && typeof r === "object" && Array.isArray((r as { checks?: unknown }).checks);
}

function isPrintables(r: unknown): r is Printables {
  return !!r && typeof r === "object" && Array.isArray((r as { files?: unknown }).files);
}

export const useSetup = create<SetupStore>((set, get) => ({
  setup: null,
  loading: false,
  error: null,

  fetch: async () => {
    set({ loading: true });
    try {
      const setup = await api.setup();
      get().apply(setup);
      set({ loading: false, error: null });
    } catch (e) {
      set({ loading: false, error: asApiError(e, "/api/setup") });
    }
  },

  apply: (setup) => {
    const prev = get().setup;
    // A new target (the simulator connected or left): forget what belongs to the old one.
    if (prev && prev.target !== setup.target) set({ selftestResult: null, printCheck: null });
    set({ setup, error: null, printCheck: setup.print_check ?? (prev?.target === setup.target ? get().printCheck : null) });
  },

  dismiss: async (dismissed) => {
    try {
      get().apply(await api.dismissSetup(dismissed));
      return true;
    } catch (e) {
      toast.error(dismissed ? "Couldn't hide the setup card" : "Couldn't show the setup card", e);
      return false;
    }
  },

  // ---------------------------------------------------------------- self-test
  selftestJobId: null,
  selftestResult: null,
  selftestStarting: false,
  runSelftest: async () => {
    if (get().selftestStarting) return false;
    set({ selftestStarting: true });
    try {
      const job = await api.runSelftest();
      useJobs.getState().upsert(job);
      set({ selftestJobId: job.id, selftestStarting: false, selftestResult: null });
      return true;
    } catch (e) {
      set({ selftestStarting: false });
      toast.error("Couldn't start the self-test", e);
      return false;
    }
  },

  // ---------------------------------------------------------------- devices and camera settings
  devices: null,
  devicesLoading: false,
  devicesError: null,
  probeDevices: async () => {
    if (get().devicesLoading) return;
    set({ devicesLoading: true });
    try {
      const devices = await api.devices();
      set({ devices, devicesLoading: false, devicesError: null });
    } catch (e) {
      set({ devicesLoading: false, devicesError: asApiError(e, "/api/setup/devices") });
    }
  },
  camera: null,
  configPath: null,
  cameraPending: null,
  cameraChanged: false,
  fetchCameraConfig: async () => {
    try {
      const settings = await api.settings();
      set({ camera: cameraConfigOf(settings), configPath: configPathOf(settings) ?? get().configPath });
    } catch {
      /* the camera list still works; the switch shows the default */
    }
  },
  setCamera: async (body) => {
    set({ cameraPending: body.index !== undefined ? "index" : "swap" });
    try {
      const settings = await api.setCamera(body);
      const camera = cameraConfigOf(settings) ?? {
        index: body.index ?? get().camera?.index ?? null,
        swap_lr: body.swap_lr ?? get().camera?.swap_lr ?? false,
      };
      set({ camera, configPath: configPathOf(settings) ?? get().configPath, cameraPending: null, cameraChanged: true });
      // The probe marks the configured camera: refresh it.
      const d = get().devices;
      if (d && body.index !== undefined) {
        set({
          devices: {
            ...d,
            configured_index: body.index,
            cameras: d.cameras.map((c) => ({ ...c, configured: c.index === body.index })),
          },
        });
      }
      return true;
    } catch (e) {
      set({ cameraPending: null });
      toast.error(body.index !== undefined ? "Couldn't choose that camera" : "Couldn't change swap left/right", e);
      return false;
    }
  },
  clearCameraChanged: () => set({ cameraChanged: false }),

  // ---------------------------------------------------------------- printables
  printables: null,
  printablesError: null,
  printJobId: null,
  printStarting: false,
  fetchPrintables: async () => {
    try {
      set({ printables: await api.printables(), printablesError: null });
    } catch (e) {
      set({ printablesError: asApiError(e, "/api/setup/printables") });
    }
  },
  generatePrintables: async (paper) => {
    if (get().printStarting) return false;
    set({ printStarting: true });
    try {
      const job = await api.generatePrintables(paper);
      useJobs.getState().upsert(job);
      set({ printJobId: job.id, printStarting: false });
      return true;
    } catch (e) {
      set({ printStarting: false });
      toast.error("Couldn't make the printables", e);
      return false;
    }
  },
  openFolder: async () => {
    try {
      return await api.openPrintables();
    } catch (e) {
      toast.error("Couldn't open the folder", e);
      return null;
    }
  },

  // ---------------------------------------------------------------- print check
  printCheck: null,
  printCheckPending: null,
  checkPrint: async (measured, apply) => {
    set({ printCheckPending: apply ? "apply" : "check" });
    try {
      const r = await api.printCheck(measured, apply);
      set({ printCheck: r, printCheckPending: null });
      if (apply) toast.success("Using your measurement", "The board is scaled for calibration only. Your printer settings stay as they are.");
      return r;
    } catch (e) {
      set({ printCheckPending: null });
      toast.error(apply ? "Couldn't use the measurement" : "Couldn't check the print", e);
      return null;
    }
  },
}));

/** A step of the current SetupState. */
export function stepOf(setup: SetupState | null | undefined, id: StepId): SetupStep | null {
  return setup?.steps.find((s) => s.id === id) ?? null;
}

// Follow the self-test and printables jobs: adopt one started elsewhere, and pick up results.
useJobs.subscribe((s, prev) => {
  const st = useSetup.getState();
  const adopt = (kindRe: RegExp, current: string | null): Job | undefined =>
    current ? undefined : Object.values(s.jobs).find((j) => kindRe.test(j.kind) && isActive(j));
  const selfAdopt = adopt(/self.?test/i, st.selftestJobId);
  if (selfAdopt) useSetup.setState({ selftestJobId: selfAdopt.id, selftestResult: null });
  const printAdopt = adopt(/printable/i, st.printJobId);
  if (printAdopt) useSetup.setState({ printJobId: printAdopt.id });

  const finished = (jid: string | null): Job | null => {
    if (!jid) return null;
    const job = s.jobs[jid];
    const before = prev.jobs[jid];
    if (!job || job === before || isActive(job)) return null;
    return job;
  };

  const self = finished(useSetup.getState().selftestJobId);
  if (self) {
    useSetup.setState({ selftestJobId: null, selftestResult: self.state === "done" && isSelfTestResult(self.result) ? self.result : null });
    void useSetup.getState().fetch();
    if (self.state === "failed")
      toast.error("The self-test couldn't finish", { detail: self.error || self.message || "The job failed.", hint: "Check the connection, then run it again." });
  }

  const print = finished(useSetup.getState().printJobId);
  if (print) {
    useSetup.setState({ printJobId: null });
    if (print.state === "done" && isPrintables(print.result)) useSetup.setState({ printables: print.result, printablesError: null });
    else void useSetup.getState().fetchPrintables();
    void useSetup.getState().fetch();
    if (print.state === "failed")
      toast.error("Couldn't make the printables", { detail: print.error || print.message || "The job failed.", hint: "Check the engine log, then try again." });
  }
});
