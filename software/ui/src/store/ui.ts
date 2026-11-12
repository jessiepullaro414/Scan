/** UI preferences, persisted in localStorage. */
import { create } from "zustand";
import type { ScanMode } from "../api/types";
import { storage } from "../lib/util";

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
  setTheme: (t: Theme) => void;
  setCurrentProject: (pid: string | null) => void;
  setScanMode: (m: ScanMode) => void;
  setPreviewsOpen: (open: boolean) => void;
  setHintsOpen: (open: boolean) => void;
  setCaptureSound: (on: boolean) => void;
  setAutoMesh: (on: boolean) => void;
};

const K = {
  theme: "ohs.theme",
  project: "ohs.currentProject",
  mode: "ohs.scanMode",
  previews: "ohs.previewsOpen",
  hints: "ohs.hintsOpen",
  sound: "ohs.captureSound",
  autoMesh: "ohs.autoMesh",
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
}

export const useUi = create<UiState>((set) => ({
  theme: readTheme(),
  currentProjectId: storage.get<string | null>(K.project, null),
  scanMode: storage.get<ScanMode>(K.mode, "markerless"),
  previewsOpen: storage.get<boolean>(K.previews, true),
  hintsOpen: storage.get<boolean>(K.hints, true),
  captureSound: storage.get<boolean>(K.sound, true),
  autoMesh: storage.get<boolean>(K.autoMesh, true),
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
}));

applyTheme(useUi.getState().theme);
