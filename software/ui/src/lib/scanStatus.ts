/**
 * A scan's status in words on the project page (UX_PLAN U38): why it failed is said on its own
 * line under the row, not hidden in a hover. Plain TypeScript so `npm test` can check it.
 */

/**
 * "Failed: too few frames tracked." The engine's reason carries its own advice, so the row's advice ("Make mesh
 * again, or at Preview quality.") is only for a failure without a reason. A reason that says itself what failed
 * ("Following the camera path failed. …") stands alone, so "failed" is not said twice.
 */
export function failedLine(error: string | null | undefined, remakable: boolean): string {
  const why = (error ?? "").trim().replace(/[.\s]+$/, "");
  if (!why) return remakable ? "Making the mesh failed. Make mesh again, or at Preview quality." : "Making the mesh failed.";
  if (/\bfailed\b/i.test(why)) return `${why}.`;
  // the first word is lowered to follow "Failed:", unless it is an abbreviation ("IMU", "GPU", "STL")
  const keep = /^[A-Z][A-Z0-9]/.test(why);
  return `Failed: ${keep ? why : why.charAt(0).toLowerCase() + why.slice(1)}.`;
}
