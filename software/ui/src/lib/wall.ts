/**
 * The wall-thickness reading's display (ENGINE_PLAN X16): a thin wall's thickness read from the scan's camera frames
 * at a click. One-sided by design: the engine says "at most X mm" and why the wall may be up to about 1 mm thinner
 * (scans place a thin wall's two sides slightly too far apart). The card shows the engine's sentences as they come,
 * never a "t ± s". Pure functions, tested by `npm test` (wall.test.ts).
 */
import type { WallOffer, WallReading } from "../api/types";

export type WallCard = {
  /** "At most 1.23 mm". */
  headline: string;
  /** The engine's sentences, in its order. */
  lines: string[];
  /** "17 frames · 4.2 s". */
  meta: string;
};

/** What the card under the view shows for a reading. */
export function wallCard(r: WallReading): WallCard {
  const n = (r.frames?.[0] ?? 0) + (r.frames?.[1] ?? 0);
  return {
    headline: `At most ${r.at_most_mm.toFixed(2)} mm`,
    lines: [...r.words],
    meta: `${n} frames · ${r.seconds.toFixed(1)} s`,
  };
}

/** The shape picker's title for the "Wall" choice: what it does, or why the scan does not offer it. */
export function wallOfferTitle(o: WallOffer | null): string {
  if (o && !o.available) return o.why ?? "Not offered for this scan.";
  return "A thin wall's thickness, read from the camera frames (T)";
}

/** The hint under the view while the Wall choice is on. */
export const WALL_HINT = "Click one face of a thin wall whose both sides were scanned";
