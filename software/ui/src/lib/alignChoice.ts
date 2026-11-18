/**
 * Sure or not sure (MAINTENANCE_PLAN F11; APP_API.md, "Sure or not sure"): what the Align page does with an
 * alignment of two scans the engine was not sure of. The engine accepts a pose only when the evidence is
 * there; otherwise it says why, and hands over the poses it had in mind. The person then looks at each
 * pose laid over the other scan and chooses one, or picks matching points. A merge is offered on a pose
 * the engine was sure of, on one the person chose, or on one from their own points; never on a guess.
 *
 * Pure functions: the page (AlignPage.tsx) and the store (store/align.ts) call them; `alignChoice.test.ts`
 * checks them.
 */
import type { AlignDoubt, AlignPose, AlignResult } from "../api/types.ts";

/** Doubts where the engine had a pose in mind (its `poses`), as against no usable pose at all. */
export const NOT_SURE_DOUBTS: readonly AlignDoubt[] = ["rival", "turned", "one_guess", "points"];

/** Sure (accepted), not sure (poses to look at), or no usable pose. */
export type Standing = "sure" | "not_sure" | "failed";

export function standingOf(r: Pick<AlignResult, "accepted" | "doubt" | "poses">): Standing {
  if (r.accepted) return "sure";
  return r.doubt && NOT_SURE_DOUBTS.includes(r.doubt) && (r.poses?.length ?? 0) > 0 ? "not_sure" : "failed";
}

/** The label above the verdict, as plainly as "Accepted". */
export function standingLabel(r: Pick<AlignResult, "accepted" | "doubt" | "poses">): string {
  const s = standingOf(r);
  return s === "sure" ? "Accepted" : s === "not_sure" ? (r.doubt === "points" ? "Not sure: check your points" : "Not sure") : "Not aligned";
}

/** One pose to look at, in the page's words. */
export type PoseChoice = { index: number; T: number[][]; title: string; detail: string };

/** The poses the engine had in mind: the first is its own best, the others how well they fit against it. */
export function poseChoices(r: Pick<AlignResult, "poses">): PoseChoice[] {
  const poses: AlignPose[] = r.poses ?? [];
  const best = poses[0]?.pick ?? 0;
  return poses.map((p, index) => {
    const share = best > 0 ? Math.round((100 * p.pick) / best) : 0;
    const starts = `${p.reached} start${p.reached === 1 ? "" : "s"} ended here (${p.from.join(", ") || "–"})`;
    return {
      index,
      T: p.T,
      title: index === 0 ? "Pose 1: the engine's best" : `Pose ${index + 1}: ${p.apart_mm.toFixed(0)} mm from pose 1`,
      detail: index === 0 ? starts : `Fits ${share} % as well as pose 1 · ${starts}`,
    };
  });
}

/** The pose the 3D view and the merge use: the one the person chose (or is looking at), else the engine's. */
export function poseInUse(r: Pick<AlignResult, "T" | "poses">, index: number | null): number[][] {
  const p = index === null ? undefined : r.poses?.[index];
  return p ? p.T : r.T;
}

/** Whether a merge is offered for this answer, whether it asks first, and if not offered, why. */
export type MergeOffer = { offered: boolean; confirm: boolean; why: string | null };

export function mergeOffer(r: Pick<AlignResult, "accepted" | "doubt" | "poses" | "start">, chosen: number | null): MergeOffer {
  if (r.accepted) return { offered: true, confirm: false, why: null };
  if (chosen !== null && (r.poses?.length ?? 0) > chosen) return { offered: true, confirm: false, why: null }; // the person looked and chose
  if (r.start === "given")
    // their own points, which the shapes did not confirm: theirs to decide, after a warning
    return { offered: true, confirm: true, why: null };
  return {
    offered: false,
    confirm: false,
    why:
      standingOf(r) === "not_sure"
        ? "The engine is not sure where the second scan goes, so there is nothing to merge at yet. Look at its poses and choose one, or pick matching points and align from them."
        : "The two scans are not aligned, so there is nothing to merge at yet. Pick matching points and align from them.",
  };
}

/** The person's choice, said as theirs (shown with the verdict and before the merge). */
export function choiceText(r: Pick<AlignResult, "poses">, chosen: number | null): string | null {
  const n = r.poses?.length ?? 0;
  if (chosen === null || chosen >= n) return null;
  return `Your choice: pose ${chosen + 1} of ${n}${chosen === 0 ? " (the engine's best)" : ""}. The engine was not sure; you looked and chose it.`;
}
