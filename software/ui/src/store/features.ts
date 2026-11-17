/**
 * The fits, measurements and relations kept with each scan (APP_API.md "Kept features"): read when
 * the viewer opens a scan and again when its mesh changes (to know which fits were made on an
 * earlier mesh), written on every add, rename and delete, and "fit again" for the stale fits.
 * Nothing is refitted unless the user asks.
 *
 * Errors of a rename, delete or "fit again" become a toast with the engine's detail and hint;
 * keeping a fit throws, so the viewer can tell a changed mesh (409) from a click that fits nothing.
 */
import { create } from "zustand";
import { ApiError, api } from "../api/http";
import type { FeatureSet, KeptFit, KeptMeasurement, KeptRelation, NewFeature, NewMeasurement, RefitResult } from "../api/types";
import { withFeature, withMeasurement, withName, withRelation, without } from "./featureSet";
import { toast } from "./toasts";

export type FeaturesEntry = {
  set: FeatureSet | null;
  loading: boolean;
  /** The last read failed (the set above may be stale). */
  error: ApiError | null;
  /** "Fit again" is running. */
  refitting: boolean;
};

type FeaturesStore = {
  entries: Record<string, FeaturesEntry | undefined>;
  load: (pid: string, sid: string) => Promise<FeatureSet | null>;
  /** Fit where the user clicked and keep it; throws the engine's error (409: the mesh changed). */
  keepFit: (pid: string, sid: string, body: NewFeature) => Promise<KeptFit>;
  keepMeasurement: (pid: string, sid: string, body: NewMeasurement) => Promise<KeptMeasurement | null>;
  keepRelation: (pid: string, sid: string, a: string, b: string) => Promise<KeptRelation | null>;
  rename: (pid: string, sid: string, id: string, name: string) => Promise<boolean>;
  remove: (pid: string, sid: string, id: string) => Promise<boolean>;
  /** Delete several items (a panel's Clear). */
  removeAll: (pid: string, sid: string, ids: string[]) => Promise<void>;
  /** Fit the stale fits again on the current mesh. */
  refit: (pid: string, sid: string) => Promise<RefitResult | null>;
};

export const featuresKey = (pid: string, sid: string) => `${pid}/${sid}`;

const blank = (): FeaturesEntry => ({ set: null, loading: false, error: null, refitting: false });

export const useFeatures = create<FeaturesStore>((set, get) => {
  const patch = (pid: string, sid: string, p: Partial<FeaturesEntry>) =>
    set((s) => {
      const key = featuresKey(pid, sid);
      return { entries: { ...s.entries, [key]: { ...(s.entries[key] ?? blank()), ...p } } };
    });
  /** Apply a change to the set this window holds (none yet: read it). */
  const change = (pid: string, sid: string, fn: (s: FeatureSet) => FeatureSet) => {
    const cur = get().entries[featuresKey(pid, sid)]?.set;
    if (cur) patch(pid, sid, { set: fn(cur) });
    else void get().load(pid, sid);
  };

  return {
    entries: {},

    load: async (pid, sid) => {
      patch(pid, sid, { loading: true });
      try {
        const s = await api.features(pid, sid);
        patch(pid, sid, { set: s, loading: false, error: null });
        return s;
      } catch (e) {
        patch(pid, sid, { loading: false, error: e instanceof ApiError ? e : new ApiError(0, String(e), null, "features") });
        return null;
      }
    },

    keepFit: async (pid, sid, body) => {
      const f = await api.keepFit(pid, sid, body);
      change(pid, sid, (s) => withFeature(s, f));
      return f;
    },

    keepMeasurement: async (pid, sid, body) => {
      try {
        const m = await api.keepMeasurement(pid, sid, body);
        change(pid, sid, (s) => withMeasurement(s, m));
        return m;
      } catch (e) {
        toast.error("Couldn't keep the measurement", e);
        return null;
      }
    },

    keepRelation: async (pid, sid, a, b) => {
      try {
        const r = await api.keepRelation(pid, sid, { a, b });
        change(pid, sid, (s) => withRelation(s, r));
        return r;
      } catch (e) {
        toast.error("Couldn't keep the relation", e);
        return null;
      }
    },

    rename: async (pid, sid, id, name) => {
      try {
        const item = await api.renameFeature(pid, sid, id, name);
        change(pid, sid, (s) => withName(s, id, item.name));
        return true;
      } catch (e) {
        toast.error("Couldn't rename it", e);
        return false;
      }
    },

    remove: async (pid, sid, id) => {
      try {
        await api.deleteFeature(pid, sid, id);
        change(pid, sid, (s) => without(s, id));
        return true;
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          change(pid, sid, (s) => without(s, id)); // already gone
          return true;
        }
        toast.error("Couldn't remove it", e);
        return false;
      }
    },

    removeAll: async (pid, sid, ids) => {
      for (const id of ids) if (!(await get().remove(pid, sid, id))) break;
    },

    refit: async (pid, sid) => {
      const e = get().entries[featuresKey(pid, sid)];
      if (e?.refitting) return null;
      patch(pid, sid, { refitting: true });
      try {
        const r = await api.refitFeatures(pid, sid);
        patch(pid, sid, { set: r.features, refitting: false, error: null });
        const names = (ids: string[]) => ids.map((id) => r.features.features.find((f) => f.id === id)?.name ?? id).join(", ");
        if (r.failed.length)
          toast.warn(
            r.refitted.length ? `Fitted ${r.refitted.length} again; ${r.failed.length} could not be` : "Couldn't fit them again",
            `${names(r.failed.map((x) => x.id))}: ${r.failed[0].detail}`,
            r.failed[0].hint ?? "They stay marked; remove them or click again to fit afresh.",
          );
        else if (r.refitted.length) toast.success(`Fitted ${r.refitted.length === 1 ? "1 shape" : `${r.refitted.length} shapes`} again on the current mesh`);
        return r;
      } catch (err) {
        patch(pid, sid, { refitting: false });
        toast.error("Couldn't fit them again", err);
        return null;
      }
    },
  };
});
