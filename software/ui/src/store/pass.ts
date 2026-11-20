/**
 * A second pass (FEATURE_PLAN N2): what "Scan these areas" in the Check panel hands to the Scan page, kept
 * until Start (in this tab's session storage too, so a reload of the Scan page keeps it), and whether the part
 * was moved since the first scan.
 */
import { create } from "zustand";
import { api } from "../api/http";
import type { CheckPlanPose, CheckResult, CheckWhy, Scan } from "../api/types";
import { GENERAL_ID } from "../lib/presets";
import { pendingFromCheck, type PendingPass } from "../lib/pass";
import { navigate, paths } from "../lib/router";
import { usePresets } from "./presets";

const KEY = "ohs.pendingPass";

function load(): PendingPass | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as PendingPass) : null;
  } catch {
    return null;
  }
}

function save(p: PendingPass | null): void {
  try {
    if (p) sessionStorage.setItem(KEY, JSON.stringify(p));
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage blocked or full: the pass is kept while the page is open */
  }
}

type PassStore = {
  pending: PendingPass | null;
  /** The part was moved since the first scan (the alignment then does not use the table). */
  moved: boolean;
  setMoved: (v: boolean) => void;
  /** Keep what the Check panel picked and open the Scan page for the pass. */
  scanAreas: (args: { projectId: string; scan: Scan; result: CheckResult; why: CheckWhy | null; only?: number[]; plan?: CheckPlanPose[] }) => void;
  clear: () => void;
};

export const usePass = create<PassStore>((set, get) => ({
  pending: load(),
  moved: false,
  setMoved: (v) => set({ moved: v }),
  scanAreas: (args) => {
    const p = pendingFromCheck(args);
    save(p);
    set({ pending: p, moved: false });
    // the pass takes the first scan's preset unless the user picks another one (the picker shows it)
    const ps = usePresets.getState();
    ps.choose(p.preset?.id ?? GENERAL_ID);
    if (p.preset?.hand) ps.setHand(p.preset.hand);
    navigate(paths.scan(p.projectId, { continues: p.firstId }));
    // pressed before the reasons arrived: ask for them (the engine keeps them per mesh version and tolerance) and
    // fill the places in; until then the Scan page says they are still being worked out
    if (p.placesPending) {
      const same = (q: PendingPass | null) => !!q && q.firstId === p.firstId && q.version === p.version && q.tolerance === p.tolerance;
      api.checkWhy(p.projectId, p.firstId, p.tolerance, p.version).then(
        (why) => {
          const cur = get().pending;
          if (!same(cur)) return;
          const next = { ...cur!, poses: pendingFromCheck({ ...args, why }).poses, placesPending: false, placesFailed: false };
          save(next);
          set({ pending: next });
        },
        () => {
          const cur = get().pending;
          if (!same(cur)) return;
          const next = { ...cur!, placesPending: false, placesFailed: true };
          save(next);
          set({ pending: next });
        },
      );
    }
  },
  clear: () => {
    save(null);
    set({ pending: null, moved: false });
  },
}));
