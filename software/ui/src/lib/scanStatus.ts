/**
 * A scan's status in words on the project page (UX_PLAN U38): why it failed is said on its own
 * line under the row, not hidden in a hover. Plain TypeScript so `npm test` can check it.
 */

/** "Failed: too few frames tracked. Make mesh again, or at Preview quality." */
export function failedLine(error: string | null | undefined, remakable: boolean): string {
  const why = (error ?? "").trim().replace(/[.\s]+$/, "");
  // the first word is lowered to follow "Failed:", unless it is an abbreviation ("IMU", "GPU", "STL")
  const keep = /^[A-Z][A-Z0-9]/.test(why);
  const head = why ? `Failed: ${keep ? why : why.charAt(0).toLowerCase() + why.slice(1)}.` : "Making the mesh failed.";
  return remakable ? `${head} Make mesh again, or at Preview quality.` : head;
}
