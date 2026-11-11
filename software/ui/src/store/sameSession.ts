/**
 * Whether two session states say the same thing. The engine sends the session with every 10 Hz
 * status message; the store keeps the old object when nothing changed, so that components
 * reading it are not rendered again for nothing.
 */
import type { SessionState } from "../api/types.ts";

export function sameSession(a: SessionState | null | undefined, b: SessionState | null | undefined): boolean {
  if (a === b) return true;
  if (!a || !b) return !a && !b;
  const ka = Object.keys(a) as (keyof SessionState)[];
  if (ka.length !== Object.keys(b).length) return false;
  return ka.every((k) => Object.is(a[k], b[k]));
}
