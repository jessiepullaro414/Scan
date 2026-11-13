/**
 * Checks for the Lasers shading's pure part (`npm test`): the limits, the numbers in sentences, the
 * shares and distances as text, the readout and the words of a failed read. Plain TypeScript that Node
 * runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { LaserInfo } from "../api/types";
import {
  DEFAULT_LASER_LIMIT,
  fmtLaserMm,
  fmtLaserShare,
  hasLaserValues,
  LASER_LIMITS,
  LASER_MEANING,
  LASER_NO_VALUE,
  LASER_NONE,
  LASER_NONE_HINT,
  laserFailure,
  laserReadout,
  laserSentences,
  validLaserLimit,
} from "./lasers.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`lasers.test: ${what}`);
}

/** A comparison with laser lines over 12 % of the surface, as the engine would send it. */
function info(over: { available?: boolean; gate_mm?: number; points?: Partial<LaserInfo["points"]>; stats?: Partial<LaserInfo["stats"]> } = {}): LaserInfo {
  return {
    version: 3,
    vertices: 1000,
    available: over.available ?? true,
    gate_mm: over.gate_mm ?? 3,
    points: { total: 41250, on_surface: 39870, used: 39870, stray: 0, ...over.points },
    stats: { n: 120, covered: 0.12, mean_mm: 0.004, rms_mm: 0.09, p50_mm: 0.04, p95_mm: 0.28, max_mm: 0.46, within_0_1: 0.81, within_0_3: 0.97, ...over.stats },
  };
}
const NO_STATS = { n: 0, covered: null, mean_mm: null, rms_mm: null, p50_mm: null, p95_mm: null, max_mm: null, within_0_1: null, within_0_3: null };

// the limits
check((LASER_LIMITS as readonly number[]).includes(DEFAULT_LASER_LIMIT), "the default limit is one of the choices");
check(validLaserLimit(0.5) === 0.5 && validLaserLimit(0.05) === DEFAULT_LASER_LIMIT && validLaserLimit("x") === DEFAULT_LASER_LIMIT && validLaserLimit(null) === DEFAULT_LASER_LIMIT, "validLaserLimit");
check([...LASER_LIMITS].every((l, i, a) => i === 0 || l > a[i - 1]), "the limits rise");

// the surface without a value: a grey, darker than the scale's middle grey of its theme
{
  const rgb = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  for (const [theme, middle] of [
    ["light", "#c4c3c0"],
    ["dark", "#d4d5d7"],
  ] as const) {
    const [r, g, b] = rgb(LASER_NO_VALUE[theme]);
    check(Math.max(r, g, b) - Math.min(r, g, b) < 16, `${theme}: the no-value colour is a grey`);
    check(rgb(middle)[1] - g >= 48, `${theme}: clearly darker than the colour of a line that agrees`);
  }
}

// shares and distances as text
check(fmtLaserShare(0.12) === "12 %" && fmtLaserShare(0.81) === "81 %" && fmtLaserShare(0.97) === "97 %", "whole per cent in the middle");
check(fmtLaserShare(0.004) === "0.4 %" && fmtLaserShare(0.05) === "5 %" && fmtLaserShare(0.996) === "99.6 %", "a decimal at the ends");
check(fmtLaserShare(0.0004) === "under 0.1 %" && fmtLaserShare(0.9996) === "over 99.9 %", "never 0 % or 100 % for a share that isn't");
check(fmtLaserShare(0) === "0 %" && fmtLaserShare(1) === "100 %" && fmtLaserShare(null) === "–" && fmtLaserShare(NaN) === "–", "the ends and no value");
check(fmtLaserMm(0.04) === "0.04 mm" && fmtLaserMm(0.1) === "0.1 mm" && fmtLaserMm(0.256) === "0.26 mm" && fmtLaserMm(3) === "3 mm", "two decimals at most, no trailing zeros");
check(fmtLaserMm(0.004) === "0.004 mm" && fmtLaserMm(0.0001) === "under 0.001 mm" && fmtLaserMm(0) === "0 mm", "small distances keep a digit");
check(fmtLaserMm(-0.08) === "0.08 mm" && fmtLaserMm(12.34) === "12.3 mm" && fmtLaserMm(null) === "–", "no sign, one decimal from 10 mm, no value");

// the numbers in sentences
{
  const s = laserSentences(info());
  check(s.length === 1, "no stray points, no offset: one sentence block");
  check(
    s[0] === "The laser lines cover 12 % of the surface. There the mesh and the lasers agree within 0.1 mm for 81 % and within 0.3 mm for 97 %; typical difference 0.04 mm.",
    `the main sentence: ${s[0]}`,
  );
}
{
  const s = laserSentences(info({ points: { stray: 230, used: 39640 } }));
  check(s.length === 2 && s[1] === "230 laser points were more than 3 mm off and are left out.", `stray points: ${s[1]}`);
  check(laserSentences(info({ points: { stray: 1 } }))[1] === "1 laser point was more than 3 mm off and is left out.", "one stray point");
  check(laserSentences(info({ points: { stray: 1200 }, gate_mm: 2.5 }))[1] === "1,200 laser points were more than 2.5 mm off and are left out.", "the gate and the count as they are");
}
{
  check(laserSentences(info({ stats: { within_0_1: 1, within_0_3: 1 } }))[0].includes("agree within 0.1 mm everywhere; typical difference 0.04 mm."), "all within 0.1 mm");
  check(laserSentences(info({ stats: { within_0_3: 1 } }))[0].includes("within 0.1 mm for 81 % and within 0.3 mm everywhere;"), "all within 0.3 mm");
  check(laserSentences(info({ stats: { within_0_1: 0.9996, within_0_3: 0.99999 } }))[0].includes("for over 99.9 % and within 0.3 mm for over 99.9 %"), "nearly all is not all");
}
{
  const out = laserSentences(info({ stats: { mean_mm: 0.08 } }));
  check(out.length === 2 && out[1] === "On average the lasers see the surface 0.08 mm further out than the mesh.", `an offset outward: ${out[1]}`);
  check(laserSentences(info({ stats: { mean_mm: -0.12 } }))[1] === "On average the lasers see the surface 0.12 mm further in than the mesh.", "an offset inward");
  check(laserSentences(info({ stats: { mean_mm: 0.03 } })).length === 1, "a small mean is not mentioned");
  const both = laserSentences(info({ stats: { mean_mm: 0.08 }, points: { stray: 5 } }));
  check(both.length === 3 && /On average/.test(both[1]) && /left out/.test(both[2]), "the offset, then the stray points");
}
// laser points, but none near the mesh
{
  const none = info({ stats: NO_STATS, points: { on_surface: 0, used: 0 } });
  check(!hasLaserValues(none) && hasLaserValues(info()), "hasLaserValues");
  check(laserSentences(none)[0] === "None of this scan's 41,250 laser points lies close enough to this mesh to compare.", "none near the mesh");
  const stray = laserSentences(info({ stats: NO_STATS, points: { on_surface: 300, used: 0, stray: 300 } }));
  check(stray.length === 2 && /300 laser points were more than 3 mm off/.test(stray[1]), "none used, all stray: both are said");
  check(/gave no points/.test(laserSentences(info({ stats: NO_STATS, points: { total: 0, on_surface: 0, used: 0 } }))[0]), "no points at all");
}
// a scan without laser points
{
  const off = info({ available: false, stats: NO_STATS, points: { total: 0, on_surface: 0, used: 0 } });
  check(!hasLaserValues(off), "not available: nothing to show");
  check(laserSentences(off).length === 1 && laserSentences(off)[0] === LASER_NONE, "not available: why");
  check(/lasers were off/.test(LASER_NONE) && /calibrat/.test(LASER_NONE) && /scan the part again with the lasers on/.test(LASER_NONE_HINT), "why, and what to do");
}
// no em dash in what the legend says
for (const text of [LASER_NONE, LASER_NONE_HINT, LASER_MEANING, ...laserSentences(info({ stats: { mean_mm: 0.08 }, points: { stray: 5 } }))]) check(!text.includes("—"), `no em dash: ${text.slice(0, 30)}`);
check(/Refine with lasers/.test(LASER_MEANING) && /scan that area again/.test(LASER_MEANING), "what to do where they differ");

// the readout
check(laserReadout(0.034) === "+0.034 mm (lasers further out)", "further out");
check(laserReadout(-0.12) === "−0.12 mm (lasers further in)", "further in, with a true minus");
check(laserReadout(0) === "0.000 mm (they agree)" && laserReadout(-0.00001) === "0.000 mm (they agree)", "zero and a hair, with no sign");
check(laserReadout(NaN) === null && laserReadout(null) === null && laserReadout(undefined) === null, "no laser line there: nothing to show");

// a failed read
{
  const old = laserFailure(404, "Not Found", null);
  check(/can't compare the mesh with the lasers yet/.test(old.message) && /Update OpenHandScan/.test(old.hint), "a bare 404: the engine is older than the app");
  check(laserFailure(404, null, null).message === old.message && laserFailure(404, "404 Not Found (/api/x)", null).message === old.message, "a 404 without words of its own, too");
  const gone = laserFailure(404, "Scan not found", "It may have been deleted");
  check(gone.message === "Couldn't compare the mesh with the lasers: Scan not found." && gone.hint === "It may have been deleted.", "a 404 with its own words keeps them");
  const busy = laserFailure(409, "This scan has no mesh yet", "Make the mesh first.");
  check(busy.message === "This scan has no mesh yet." && busy.hint === "Make the mesh first.", "a 409 in the engine's words");
  check(laserFailure(409, null, null).hint === "Try again in a moment.", "a 409 without words");
  const down = laserFailure(0, "Can't reach the OpenHandScan engine", "Start the engine.");
  check(down.message === "Couldn't compare the mesh with the lasers: Can't reach the OpenHandScan engine." && down.hint === "Start the engine.", "unreachable");
  const odd = laserFailure(500, null, null);
  check(odd.message === "Couldn't compare the mesh with the lasers." && /Try again/.test(odd.hint), "no words at all: still what happened and what to do");
}

console.log(`lasers.test: ${checks} checks passed`);
