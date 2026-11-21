/**
 * What this computer can do (UX_PLAN U5): said before something is missing, in Settings (About) and on the Home setup
 * card, from the engine's `health.engine.compute` (`ohs.compute.describe()`). "Graphics card" only, never the engine's
 * "GPU", "OpenCL" or "CPU".
 *
 * Without a graphics card (checked against the engine since D7, 2026-11-20): the mesh is made by the model without
 * confidence (so no confidence map, and the Scan check cannot test it), "Thin walls and edges" does nothing, tracking
 * holds flat, plain parts less well (the camera path's warning says when it slid), and making the mesh is slower. The
 * calibration's status judges the lasers and the motion sensor on that path too (D7), so that is not listed.
 */

export type ComputeInfo = { backend?: string; status?: string; device?: { name?: string; kind?: string; memory_mb?: number } | null };

/** Why there is no graphics card in use, in plain words, from the engine's status. */
function why(status: string): string {
  // R20: the engine says the same whether the settings or the environment turned it off, so the line names neither
  if (/turned off/i.test(status)) return "the graphics card is turned off for this app (in its settings, or for this run)";
  if (/PyOpenCL is not installed/i.test(status)) return "the app's support for graphics cards is not installed";
  if (/no OpenCL driver/i.test(status)) return "no driver for a graphics card was found";
  if (/no GPU matches/i.test(status)) return "no graphics card found: none matches the one named in the settings";
  if (/passed the OpenCL check/i.test(status)) return "the graphics card found did not pass the app's check";
  return "no graphics card found";
}

export const WITHOUT_GRAPHICS_CARD = [
  "The mesh has no confidence map, so the Scan check cannot test it against a tolerance.",
  "“Thin walls and edges” has no effect, so thin walls can come out thicker.",
  "Tracking holds flat, plain parts less well (the app warns when it may have slid).",
  "Making the mesh takes longer.",
];

/**
 * One line about this computer, and what is not available without a graphics card; null when nothing is known.
 * `name`: say the graphics card's own name (R12: the engine's name for it can be a code name such as "gfx1035", so it
 * is said in Settings' About only).
 */
export function computerLine(c: unknown, opts: { name?: boolean } = {}): { tone: "ok" | "warn" | "neutral"; text: string; missing: string[] } | null {
  if (!c || typeof c !== "object") return null;
  const v = c as ComputeInfo;
  if (v.backend === "gpu") {
    const name = opts.name ? v.device?.name?.trim() : "";
    return { tone: "ok", text: `This computer: graphics card found${name ? ` (${name})` : ""}. Every feature works.`, missing: [] };
  }
  const status = typeof v.status === "string" ? v.status : "";
  if (/not checked yet/i.test(status)) return { tone: "neutral", text: "This computer: looking for a graphics card…", missing: [] };
  return { tone: "warn", text: `This computer: ${why(status)}. Scans still work, with these limits:`, missing: WITHOUT_GRAPHICS_CARD };
}

/**
 * Why a mesh has no confidence data, and what gets it: "make it again" only where that would help (U5). `hasCard`:
 * this computer has a graphics card in use (null: not known). `where`: the viewer, or Edit mesh (its own words).
 */
export function noConfidenceText(hasCard: boolean | null, where: "viewer" | "process"): string {
  const head = "This mesh has no confidence data: it was made without a graphics card or by an earlier version.";
  if (hasCard === false) return `${head} This computer has none, so making it again here does not add it.`;
  if (hasCard === null) return `${head} Make the mesh again on a computer with a graphics card to get it.`;
  return `${head} ${where === "process" ? "Run “Follow the camera” again to get it." : "Make the mesh again to get it."}`;
}

/**
 * The Scan check panel on a mesh without confidence (UX_PLAN R16): the name from the table ("Scan check"), the
 * Confidence shading's reason said on the page (not only on its hover), and "make it again" only where that helps:
 * `offerRemake` is false on a computer without a graphics card.
 */
export function noCheckCallout(hasCard: boolean | null): { title: string; text: string; offerRemake: boolean } {
  return {
    title: "Scan check can't be used on this mesh",
    text:
      "The Scan check and the Confidence shading need to know how sure the scan is of each point (its confidence). " +
      noConfidenceText(hasCard, "viewer"),
    offerRemake: hasCard !== false,
  };
}

/** The line in the Edit mesh preset box when "Thin walls and edges" can't work here (R16); null when it can. */
export function thinWallsNote(hasCard: boolean | null): string | null {
  return hasCard === false ? "“Thin walls and edges” has no effect on this computer: it needs a graphics card." : null;
}

/** Whether this computer has a graphics card in use, from `health.engine.compute`; null when not known yet. */
export function hasGraphicsCard(c: unknown): boolean | null {
  if (!c || typeof c !== "object") return null;
  const v = c as ComputeInfo;
  if (v.backend === "gpu") return true;
  if (typeof v.status === "string" && /not checked yet/i.test(v.status)) return null;
  return v.backend === "cpu" ? false : null;
}
