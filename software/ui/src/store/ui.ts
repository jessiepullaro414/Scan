/** UI preferences, persisted in localStorage. */
import { create } from "zustand";
import type { ScanMode } from "../api/types";
import { validLimit } from "../lib/deviation";
import { validLaserLimit } from "../lib/lasers";
import { storage } from "../lib/util";
import { syncWindowTheme } from "../lib/windowChrome";

export type Theme = "dark" | "light";

type UiState = {
  theme: Theme;
  /** The project scans go into (and the one named in the top bar). */
  currentProjectId: string | null;
  scanMode: ScanMode;
  previewsOpen: boolean;
  hintsOpen: boolean;
  /** The calibration capture click (WebAudio). */
  captureSound: boolean;
  /** Start "Make mesh" (standard quality) as soon as a scan is stopped. */
  autoMesh: boolean;
  /** The deviation map's scale: the move (mm) that shows as the strongest colour. */
  deviationLimit: number;
  /** The Lasers shading's scale: the distance between the lasers and the mesh (mm) that shows as the strongest colour. */
  laserLimit: number;
  /** Nominal offers: also show the values a known bias alone allows (off: only where the ± alone allows one). */
  nominalWithBias: boolean;
  /** U8: the tracking's match and distance numbers on the Scan page (off: the words only). */
  trackingNumbers: boolean;
  setTrackingNumbers: (on: boolean) => void;
  setTheme: (t: Theme) => void;
  setCurrentProject: (pid: string | null) => void;
  setScanMode: (m: ScanMode) => void;
  setPreviewsOpen: (open: boolean) => void;
  setHintsOpen: (open: boolean) => void;
  setCaptureSound: (on: boolean) => void;
  setAutoMesh: (on: boolean) => void;
  setDeviationLimit: (mm: number) => void;
  setLaserLimit: (mm: number) => void;
  setNominalWithBias: (on: boolean) => void;
};

const K = {
  theme: "ohs.theme",
  project: "ohs.currentProject",
  mode: "ohs.scanMode",
  previews: "ohs.previewsOpen",
  hints: "ohs.hintsOpen",
  sound: "ohs.captureSound",
  autoMesh: "ohs.autoMesh",
  deviationLimit: "ohs.deviationLimit",
  laserLimit: "ohs.laserLimit",
  nominalWithBias: "ohs.nominalWithBias",
  trackingNumbers: "ohs.trackingNumbers",
};

function readTheme(): Theme {
  try {
    const t = localStorage.getItem(K.theme);
    return t === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

function applyTheme(t: Theme) {
  document.documentElement.setAttribute("data-theme", t);
  syncWindowTheme(t); // the desktop window's title bar
}

export const useUi = create<UiState>((set) => ({
  theme: readTheme(),
  currentProjectId: storage.get<string | null>(K.project, null),
  scanMode: storage.get<ScanMode>(K.mode, "markerless"),
  previewsOpen: storage.get<boolean>(K.previews, true),
  hintsOpen: storage.get<boolean>(K.hints, true),
  captureSound: storage.get<boolean>(K.sound, true),
  autoMesh: storage.get<boolean>(K.autoMesh, true),
  deviationLimit: validLimit(storage.get<unknown>(K.deviationLimit, null)),
  laserLimit: validLaserLimit(storage.get<unknown>(K.laserLimit, null)),
  nominalWithBias: storage.get<unknown>(K.nominalWithBias, false) === true,
  trackingNumbers: storage.get<unknown>(K.trackingNumbers, false) === true,
  setTrackingNumbers: (on) => {
    storage.set(K.trackingNumbers, on);
    set({ trackingNumbers: on });
  },
  setTheme: (theme) => {
    try {
      localStorage.setItem(K.theme, theme);
    } catch {
      /* ignore */
    }
    applyTheme(theme);
    set({ theme });
  },
  setCurrentProject: (pid) => {
    storage.set(K.project, pid);
    set({ currentProjectId: pid });
  },
  setScanMode: (scanMode) => {
    storage.set(K.mode, scanMode);
    set({ scanMode });
  },
  setPreviewsOpen: (previewsOpen) => {
    storage.set(K.previews, previewsOpen);
    set({ previewsOpen });
  },
  setHintsOpen: (hintsOpen) => {
    storage.set(K.hints, hintsOpen);
    set({ hintsOpen });
  },
  setCaptureSound: (captureSound) => {
    storage.set(K.sound, captureSound);
    set({ captureSound });
  },
  setAutoMesh: (autoMesh) => {
    storage.set(K.autoMesh, autoMesh);
    set({ autoMesh });
  },
  setDeviationLimit: (mm) => {
    const deviationLimit = validLimit(mm);
    storage.set(K.deviationLimit, deviationLimit);
    set({ deviationLimit });
  },
  setLaserLimit: (mm) => {
    const laserLimit = validLaserLimit(mm);
    storage.set(K.laserLimit, laserLimit);
    set({ laserLimit });
  },
  setNominalWithBias: (on) => {
    storage.set(K.nominalWithBias, on);
    set({ nominalWithBias: on });
  },
}));

applyTheme(useUi.getState().theme);
