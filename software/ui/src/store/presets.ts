/**
 * Presets (FEATURE_PLAN P2/P3): the list, the choice on the Scan page ("What are you scanning?", with the
 * values changed by hand there, sent with Start) and each scan's preset for the Process page.
 *
 * The choice is kept while the app is open (not stored): the next scan starts with the preset picked last.
 */
import { create } from "zustand";
import { api } from "../api/http";
import type { Preset, PresetValues, ScanPreset, ScanPresetBody } from "../api/types";
import { GENERAL_ID, startFields } from "../lib/presets";
import { toast } from "./toasts";
import { useProjects } from "./projects";

type PresetStore = {
  list: Preset[] | null;
  fetchList: () => Promise<void>;
  /** The preset picked on the Scan page, and the values changed by hand there. */
  chosen: string;
  hand: PresetValues;
  choose: (id: string) => void;
  setHand: (values: PresetValues) => void;
  /** What Start sends with the scan. */
  startFields: () => { preset: string; preset_values?: PresetValues };
  /** Each scan's preset, by `pid/sid`. */
  scans: Record<string, ScanPreset>;
  fetchScan: (pid: string, sid: string) => Promise<ScanPreset | null>;
  setScan: (pid: string, sid: string, body: ScanPresetBody) => Promise<ScanPreset | null>;
};

export const presetKey = (pid: string, sid: string) => `${pid}/${sid}`;

export const usePresets = create<PresetStore>((set, get) => ({
  list: null,
  fetchList: async () => {
    try {
      set({ list: await api.presets() });
    } catch {
      /* an engine without presets: the picker stays hidden */
      set({ list: [] });
    }
  },

  chosen: GENERAL_ID,
  hand: {},
  choose: (id) => set({ chosen: id, hand: {} }),
  setHand: (values) => set({ hand: values }),
  startFields: () => {
    const { list, chosen, hand } = get();
    const p = list?.find((x) => x.id === chosen);
    if (!p) return { preset: GENERAL_ID };
    return startFields(p.id, p.values, hand);
  },

  scans: {},
  fetchScan: async (pid, sid) => {
    try {
      const v = await api.scanPreset(pid, sid);
      set((s) => ({ scans: { ...s.scans, [presetKey(pid, sid)]: v } }));
      return v;
    } catch {
      return null; // an engine without presets: the panel stays hidden
    }
  },
  setScan: async (pid, sid, body) => {
    try {
      const v = await api.setScanPreset(pid, sid, body);
      set((s) => ({ scans: { ...s.scans, [presetKey(pid, sid)]: v } }));
      void useProjects.getState().fetchProject(pid);
      return v;
    } catch (e) {
      toast.error("Couldn't change the preset", e);
      return null;
    }
  },
}));
