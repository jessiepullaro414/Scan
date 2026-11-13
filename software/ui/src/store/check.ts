/**
 * The Check panel's tolerance, kept while the app is open (not stored): the next scan opened in the
 * viewer or on the Process page starts with the tolerance typed last.
 */
import { create } from "zustand";
import { DEFAULT_TOLERANCE_MM, validTolerance } from "../lib/check";

type CheckPrefs = {
  /** The tolerance the part needs, ± mm. */
  tolerance: number;
  setTolerance: (mm: number) => void;
};

export const useCheckPrefs = create<CheckPrefs>((set) => ({
  tolerance: DEFAULT_TOLERANCE_MM,
  setTolerance: (mm) => set({ tolerance: validTolerance(mm) }),
}));
