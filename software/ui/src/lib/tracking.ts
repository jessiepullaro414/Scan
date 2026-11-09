/**
 * Plain-language tracking messages. The engine reports machine reasons (see
 * ohs/track/markerless.py and markers.py); free-text reasons are shown as they are,
 * split at the first ":" into what happened and what to do.
 */
import type { TrackingState } from "../api/types";

type Reason = { text: string; hint: string };

const REASONS: Record<string, Reason> = {
  // markerless (ICP + TSDF)
  no_depth: { text: "No depth on the surface", hint: "Hold the scanner 200–350 mm from the part." },
  bad_depth: { text: "Unreliable depth", hint: "Avoid glare and turn the dot projector up." },
  model_not_visible: { text: "The model is out of view", hint: "Point back at an area you already scanned." },
  diverged: { text: "Tracking jumped", hint: "Move slower and keep overlap with the scanned area." },
  few_inliers: { text: "Too few features", hint: "Add tags or move closer." },
  low_fitness: { text: "Little overlap with the model", hint: "Slow down and keep scanned area in view." },
  high_rmse: { text: "Surface doesn't match the model", hint: "Hold steady; check the part hasn't moved." },
  jump: { text: "Moved too fast", hint: "Slow down and return to the last good spot." },
  // markers (AprilTags)
  not_initialized: { text: "Waiting for tags", hint: "Show at least 2 AprilTags to start the map." },
  no_tags: { text: "No tags in view", hint: "Point at an area with AprilTag stickers." },
  few_tags: { text: "Too few tags", hint: "Keep 2 or more tags in view, or add stickers." },
  high_residual: { text: "Tags don't agree", hint: "A tag may have moved; hold steady." },
  high_reprojection: { text: "Tag positions are inconsistent", hint: "Move closer or reduce motion blur." },
};

export type TrackingInfo = { label: string; text: string | null; hint: string | null };

const LABELS: Record<TrackingState, string> = {
  idle: "Not tracking",
  ok: "Tracking OK",
  weak: "Tracking weak",
  lost: "Tracking lost",
};

export function describeReason(reason: string | null | undefined): Reason | null {
  if (!reason) return null;
  const known = REASONS[reason.trim().toLowerCase()];
  if (known) return known;
  const i = reason.indexOf(":");
  const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
  if (i > 0) return { text: cap(reason.slice(0, i).trim()), hint: cap(reason.slice(i + 1).trim()) };
  return { text: cap(reason.replace(/_/g, " ")), hint: "" };
}

export function describeTracking(state: TrackingState, reason: string | null | undefined): TrackingInfo {
  const r = state === "ok" || state === "idle" ? null : describeReason(reason);
  return {
    label: LABELS[state] ?? "Tracking",
    text: r?.text ?? (state === "lost" ? "Tracking lost" : null),
    hint: r?.hint || (state === "lost" ? "Return to the ghost scanner to recover." : null),
  };
}

// ------------------------------------------------------------------ distance zones
export const DIST_MIN = 150;
export const DIST_MAX = 450;
export const SWEET_MIN = 200;
export const SWEET_MAX = 350;

export type DistanceZone = "none" | "too-close" | "close" | "good" | "far" | "too-far";

export function distanceZone(d: number | null | undefined): DistanceZone {
  if (d === null || d === undefined || !Number.isFinite(d) || d <= 0) return "none";
  if (d < DIST_MIN) return "too-close";
  if (d < SWEET_MIN) return "close";
  if (d <= SWEET_MAX) return "good";
  if (d <= DIST_MAX) return "far";
  return "too-far";
}

export const ZONE_TEXT: Record<DistanceZone, string> = {
  none: "No surface",
  "too-close": "Too close",
  close: "Move back a little",
  good: "Good distance",
  far: "Move closer",
  "too-far": "Too far",
};
