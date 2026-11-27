/**
 * The Hole tool's card (ENGINE_PLAN X15 step 2): the mesh's own answer, then a second block "From the camera images",
 * the diameter of the hole's entrance rim read from the scan's camera images at the same click. The engine writes the
 * sentences (which diameter it is, when the position may be off); the card shows them as they come. A refusal is shown
 * quietly in that block in the engine's words, with no number: the mesh's answer still stands. Pure functions, tested
 * by `npm test` (holeImages.test.ts).
 */
import type { CircleShape, HoleImagesOffer, HoleImagesReading } from "../api/types";

export const HOLE_IMAGES_TITLE = "From the camera images";
/** While the reading runs (5 to 15 s). */
export const HOLE_IMAGES_WAITING = "Reading the rim from the camera frames…";

/** The card's first block: the mesh's circle, or why the mesh gave none. */
export type MeshHoleBlock = { headline: string; line: string | null };

export function meshHoleBlock(circle: CircleShape | null, failed: string | null): MeshHoleBlock {
  if (circle) return { headline: `Ø ${circle.diameter_mm.toFixed(2)} mm ± ${circle.diameter_sigma_mm.toFixed(2)} from the mesh`, line: null };
  return { headline: "The mesh gives no circle here", line: failed };
}

/** The second block: waiting, the answer, or a refusal (no number). */
export type HoleImagesBlock =
  | { state: "waiting"; title: string; lines: string[]; meta: null }
  | { state: "answer"; title: string; headline: string; lines: string[]; meta: string }
  | { state: "refused"; title: string; lines: string[]; meta: string | null };

export function holeImagesWaiting(): HoleImagesBlock {
  return { state: "waiting", title: HOLE_IMAGES_TITLE, lines: [HOLE_IMAGES_WAITING], meta: null };
}

export function holeImagesAnswer(r: HoleImagesReading): HoleImagesBlock {
  return {
    state: "answer",
    title: HOLE_IMAGES_TITLE,
    headline: `Ø ${r.diameter_mm.toFixed(2)} mm ± ${r.pm_mm.toFixed(2)}`,
    lines: [...r.words],
    meta: `${r.frames_used} of ${r.frames_tried} frames · ${r.seconds.toFixed(1)} s`,
  };
}

/** A refusal (422): the engine's words (`detail`) and what to do (`hint`); the frames counted when the engine says. */
export function holeImagesRefused(detail: string, hint: string | null, extra: Record<string, unknown> = {}): HoleImagesBlock {
  const used = typeof extra.frames_used === "number" ? extra.frames_used : null;
  const tried = typeof extra.frames_tried === "number" ? extra.frames_tried : null;
  const secs = typeof extra.seconds === "number" ? extra.seconds : null;
  const meta = used !== null && tried !== null ? `${used} of ${tried} frames` + (secs !== null ? ` · ${secs.toFixed(1)} s` : "") : null;
  const words = detail.charAt(0).toLowerCase() + detail.slice(1); // the engine's sentence, after a colon
  return { state: "refused", title: HOLE_IMAGES_TITLE, lines: [`No diameter from the camera images: ${words}`, ...(hint ? [hint] : [])], meta };
}

/** Whether the page asks for the image reading after a Hole click. */
export function holeImagesOffered(o: HoleImagesOffer | null): boolean {
  return !!o?.available;
}
