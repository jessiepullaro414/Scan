/**
 * The Lasers shading (APP_API.md "M5 fourth slice: where the lasers and the mesh disagree"), the pure part: the scale's
 * limits, the colour of the surface without a value, and the words: the numbers in a few sentences,
 * what a failed read says, and the value under the cursor.
 *
 * The scanner measures the surface in two independent ways: the stereo depth, which makes the mesh, and
 * the two laser lines, which give a sparse cloud of points. The array holds, per vertex, the signed
 * distance in mm from the mesh to the laser points near it (+ = the lasers see the surface further out
 * than the mesh, - = further in, NaN = no laser point came near: most of the surface, the lasers draw
 * lines). The colours are the deviation map's (lib/deviation.ts: blue, grey, orange-red), on a surface
 * of a darker grey, so that a laser line that agrees with the mesh (the scale's light grey) still shows.
 *
 * Imports nothing at run time, so `npm test` checks it with plain node.
 */
import type { LaserInfo } from "../api/types";

export type LaserTheme = "light" | "dark";

/** What a person can choose as the scale's end (mm). */
export const LASER_LIMITS = [0.1, 0.2, 0.3, 0.5, 1] as const;
/** 0.3 mm: the larger of the two distances the numbers are counted at, so the strongest colour means "more than 0.3 mm apart". */
export const DEFAULT_LASER_LIMIT = 0.3;

/** The limit if it is one of the choices, else the default (a stored value may be anything). */
export function validLaserLimit(x: unknown): number {
  return typeof x === "number" && (LASER_LIMITS as readonly number[]).includes(x) ? x : DEFAULT_LASER_LIMIT;
}

/**
 * The surface where no laser point came near (sRGB): a plain grey, clearly darker than the scale's
 * middle grey (#c4c3c0 on light, #d4d5d7 on dark), which is the colour of a laser line that agrees.
 */
export const LASER_NO_VALUE: Record<LaserTheme, string> = { light: "#8b8882", dark: "#6a6966" };

const MINUS = "−";

/** A count in words: "41,250". */
function fmtCount(n: number): string {
  return Math.round(n).toLocaleString("en-US");
}

/** A share: "12 %", "0.4 %", "99.6 %"; never "100 %" or "0 %" for a share that isn't (as lib/check.ts `fmtShare`). */
export function fmtLaserShare(x: number | null | undefined): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return "–";
  if (x <= 0) return "0 %";
  if (x >= 1) return "100 %";
  if (x < 0.001) return "under 0.1 %";
  if (x > 0.999) return "over 99.9 %";
  return x < 0.1 || x > 0.99 ? `${(x * 100).toFixed(1).replace(/\.0$/, "")} %` : `${Math.round(x * 100)} %`;
}

/** A distance for the sentences, without its sign: "0.04 mm", "0.1 mm", "0.004 mm", "1.5 mm". */
export function fmtLaserMm(mm: number | null | undefined): string {
  if (mm === null || mm === undefined || !Number.isFinite(mm)) return "–";
  const a = Math.abs(mm);
  const two = Number(a.toFixed(a < 10 ? 2 : 1));
  return `${two > 0 || a === 0 ? two : Number(a.toFixed(3)) || "under 0.001"} mm`;
}

/** From this mean difference (mm) on, the lasers sit to one side of the mesh as a whole, and the legend says so. */
export const LASER_OFFSET_MM = 0.05;

/** Whether the map has anything to show: the scan has laser points and some came near the mesh. */
export function hasLaserValues(info: LaserInfo): boolean {
  return info.available && info.stats.n > 0;
}

/**
 * The numbers in sentences, the main one first:
 * "The laser lines cover 12 % of the surface. There the mesh and the lasers agree within 0.1 mm for
 * 81 % and within 0.3 mm for 97 %; typical difference 0.04 mm."
 * Then, when they apply: the lasers sitting to one side of the mesh as a whole, and the stray points
 * left out. For a scan without laser points, or one whose points all miss the mesh, why there is nothing.
 */
export function laserSentences(info: LaserInfo): string[] {
  if (!info.available) return [LASER_NONE];
  const { stats: s, points: p } = info;
  const out: string[] = [];
  if (s.n <= 0) {
    out.push(
      p.total > 0
        ? `None of this scan's ${fmtCount(p.total)} laser points lies close enough to this mesh to compare.`
        : "This scan's laser lines gave no points, so there is nothing to compare.",
    );
  } else {
    const w1 = s.within_0_1 ?? 0;
    const w3 = s.within_0_3 ?? 0;
    const agree =
      w1 >= 1
        ? "There the mesh and the lasers agree within 0.1 mm everywhere"
        : w3 >= 1
          ? `There the mesh and the lasers agree within 0.1 mm for ${fmtLaserShare(w1)} and within 0.3 mm everywhere`
          : `There the mesh and the lasers agree within 0.1 mm for ${fmtLaserShare(w1)} and within 0.3 mm for ${fmtLaserShare(w3)}`;
    out.push(`The laser lines cover ${fmtLaserShare(s.covered)} of the surface. ${agree}; typical difference ${fmtLaserMm(s.p50_mm)}.`);
    if (s.mean_mm !== null && Math.abs(s.mean_mm) >= LASER_OFFSET_MM)
      out.push(`On average the lasers see the surface ${fmtLaserMm(s.mean_mm)} further ${s.mean_mm > 0 ? "out" : "in"} than the mesh.`);
  }
  if (p.stray > 0) {
    const gate = fmtLaserMm(info.gate_mm);
    out.push(p.stray === 1 ? `1 laser point was more than ${gate} off and is left out.` : `${fmtCount(p.stray)} laser points were more than ${gate} off and are left out.`);
  }
  return out;
}

/** Why a scan has no laser comparison (`available` false). */
export const LASER_NONE = "This scan has no laser points: the lasers were off, or they were not calibrated when it was made.";
/** What to do about it. */
export const LASER_NONE_HINT = "To compare, scan the part again with the lasers on and calibrated.";

/** What the comparison is good for, in the legend. */
export const LASER_MEANING =
  "The lasers and the stereo depth are two independent measurements: where they agree, the surface can be trusted more. " +
  "Where they differ by more than your part's tolerance, scan that area again or use “Refine with lasers” (Process page).";

/**
 * What the cursor is over, in words: "+0.034 mm (lasers further out)", "-0.12 mm (lasers further in)"
 * (with a true minus sign), "0.000 mm (they agree)" (under half a unit of the last digit); null where no
 * laser point came near (nothing is shown there: that is most of the surface).
 */
export function laserReadout(v: number | null | undefined): string | null {
  if (v === null || v === undefined || Number.isNaN(v)) return null;
  const a = Math.abs(v);
  const digits = a < 0.1 ? 3 : a < 10 ? 2 : 1;
  const text = a.toFixed(digits);
  if (Number(text) === 0) return `${(0).toFixed(digits)} mm (they agree)`;
  return v > 0 ? `+${text} mm (lasers further out)` : `${MINUS}${text} mm (lasers further in)`;
}

/**
 * A failed read in two sentences: what happened and what to do. `status`: the HTTP status (0: the
 * engine could not be reached), `detail` and `hint`: the engine's own words, when it gave any.
 * A bare 404 ("Not Found", the engine's answer to a request it does not know) means the engine is older
 * than this app; a 404 with words of its own (the scan is gone) is told in those words.
 */
export function laserFailure(status: number | null, detail: string | null, hint: string | null): { message: string; hint: string } {
  const end = (s: string) => (/[.!?…]$/.test(s.trim()) ? s.trim() : `${s.trim()}.`);
  if (status === 404 && (!detail || /^(404 )?not found\b/i.test(detail.trim())))
    return {
      message: "This version of the engine can't compare the mesh with the lasers yet.",
      hint: "Update OpenHandScan and start it again, then choose Lasers once more.",
    };
  if (status === 409)
    return {
      message: detail ? end(detail) : "The mesh is not ready to compare.",
      hint: hint ? end(hint) : "Try again in a moment.",
    };
  return {
    message: `Couldn't compare the mesh with the lasers${detail ? `: ${end(detail)}` : "."}`,
    hint: hint ? end(hint) : "Try again. If it keeps failing, look at the engine's log.",
  };
}
