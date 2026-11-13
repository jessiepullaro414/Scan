/**
 * Checks for the deviation map's pure part (`npm test`): the scale, the colours at a limit, the share
 * within the limit and the readout. Plain TypeScript that Node runs as it is (type stripping), no
 * test framework: a failed check throws.
 */
import {
  DEFAULT_DEVIATION_LIMIT,
  DEVIATION_LIMITS,
  DEVIATION_RAMPS,
  deviationColors,
  deviationHex,
  deviationReadout,
  fmtLimit,
  fmtMove,
  interpolateMove,
  newShare,
  shareWithin,
  validLimit,
} from "./deviation.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`deviation.test: ${what}`);
}

const hexRgb = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const identity = Array.from({ length: 256 }, (_, i) => i);

// the scale: the ends are the ramp's ends, the middle is the neutral grey, no green leads
for (const theme of ["light", "dark"] as const) {
  const ramp = DEVIATION_RAMPS[theme];
  check(deviationHex(theme, 0) === ramp[0], `${theme}: -limit is the first step`);
  check(deviationHex(theme, 1) === ramp[4], `${theme}: +limit is the last step`);
  check(deviationHex(theme, 0.5) === ramp[2], `${theme}: zero is the neutral grey`);
  const [r, g, b] = hexRgb(deviationHex(theme, 0.5));
  check(Math.max(r, g, b) - Math.min(r, g, b) < 16, `${theme}: the middle is grey`);
  const lo = hexRgb(ramp[0]);
  const hi = hexRgb(ramp[4]);
  check(lo[2] > lo[0] && lo[2] > lo[1], `${theme}: the inward end is blue`);
  check(hi[0] > hi[1] && hi[1] > hi[2], `${theme}: the outward end is orange-red`);
  for (const t of [0, 0.1, 0.25, 0.4, 0.6, 0.75, 0.9, 1]) {
    const [rr, gg, bb] = hexRgb(deviationHex(theme, t));
    check(!(gg > rr && gg > bb), `${theme}: no green at t=${t}`);
  }
  check(deviationHex(theme, -3) === ramp[0] && deviationHex(theme, 9) === ramp[4], `${theme}: clamped`);
  check(deviationHex(theme, NaN) === ramp[2], `${theme}: NaN reads as the middle`);
}

// colours at a limit
{
  const values = new Float32Array([0, 0.1, -0.1, 0.5, -0.5, NaN, 0.05]);
  const out = deviationColors(values, 0.1, "light", identity, "#d9d4cb");
  check(out.length === values.length * 4, "four bytes per vertex");
  const at = (i: number) => `#${[0, 1, 2].map((c) => out[i * 4 + c].toString(16).padStart(2, "0")).join("")}`;
  check(Math.abs(hexRgb(at(0))[0] - hexRgb(DEVIATION_RAMPS.light[2])[0]) <= 3, "zero is neutral");
  check(at(1) === DEVIATION_RAMPS.light[4], "+limit is the outward end");
  check(at(2) === DEVIATION_RAMPS.light[0], "-limit is the inward end");
  check(at(3) === at(1) && at(4) === at(2), "beyond the limit saturates");
  check(out[3] === 255 && out[7] === 255, "a value has alpha 255");
  check(out[5 * 4 + 3] === 0, "NaN has alpha 0 (striped)");
  check(at(5) === "#d9d4cb", "NaN gets the no-value colour");
  check(hexRgb(at(6))[0] > hexRgb(at(0))[0] && hexRgb(at(6))[2] < hexRgb(at(0))[2], "half the limit outward is warmer than zero");
  // the same value at a wider limit is closer to neutral
  const wide = deviationColors(new Float32Array([0.1]), 0.5, "light", identity, "#000000");
  const narrow = deviationColors(new Float32Array([0.1]), 0.1, "light", identity, "#000000");
  check(Math.abs(wide[2] - hexRgb(DEVIATION_RAMPS.light[2])[2]) < Math.abs(narrow[2] - hexRgb(DEVIATION_RAMPS.light[2])[2]), "a wider limit moves the colour toward the middle");
  // written in place, the same array returned
  const buf = new Uint8Array(values.length * 4);
  check(deviationColors(values, 0.1, "dark", identity, "#8a8781", buf) === buf, "writes into the given array");
  // the linear table is applied
  const seven = Array.from({ length: 256 }, () => 7);
  check(deviationColors(new Float32Array([0]), 0.1, "dark", seven, "#8a8781")[0] === 7, "toLinear is applied");
}

// the share within the limit, over compared vertices only
{
  const v = new Float32Array([0, 0.05, -0.1, 0.11, -0.2, NaN, NaN]);
  check(Math.abs(shareWithin(v, 0.1)! - 3 / 5) < 1e-9, "3 of 5 compared vertices within 0.1");
  check(Math.abs(shareWithin(v, 0.2)! - 1) < 1e-9, "all within 0.2");
  check(Math.abs(shareWithin(v, 0.05)! - 2 / 5) < 1e-9, "2 of 5 within 0.05");
  check(shareWithin(new Float32Array([NaN, NaN]), 0.1) === null, "nothing compared: null");
  check(shareWithin(new Float32Array(0), 0.1) === null, "empty: null");
  check(Math.abs(newShare(v) - 2 / 7) < 1e-9, "2 of 7 vertices are new surface");
  check(newShare(new Float32Array(0)) === 0, "no vertices, no new surface");
}

// the readout
check(deviationReadout(0.034) === "+0.034 mm (outward)", "outward");
check(deviationReadout(-0.12) === "−0.12 mm (inward)", "inward");
check(deviationReadout(0.25) === "+0.25 mm (outward)", "two digits from 0.1 mm");
check(deviationReadout(0) === "0.000 mm (unchanged)", "zero");
check(deviationReadout(-0.00001) === "0.000 mm (unchanged)", "a hair is unchanged, with no minus sign");
check(deviationReadout(NaN) === "new surface" && deviationReadout(null) === "new surface", "no value");
check(fmtMove(0.0342) === "0.034 mm" && fmtMove(0.5) === "0.50 mm" && fmtMove(null) === "–", "fmtMove");
check(fmtLimit(0.05) === "0.05" && fmtLimit(1) === "1" && fmtLimit(0.1) === "0.1", "fmtLimit");

// interpolation under the cursor
{
  const v = new Float32Array([0.1, 0.3, NaN]);
  check(Math.abs(interpolateMove(v, 0, 1, 2, [0.5, 0.5, 0])! - 0.2) < 1e-6, "midway between two corners");
  check(interpolateMove(v, 0, 1, 2, [0.1, 0.1, 0.8]) === null, "nearest corner has no value: new surface");
  check(Math.abs(interpolateMove(v, 0, 1, 2, [0.6, 0.1, 0.3])! - (0.6 * 0.1 + 0.1 * 0.3) / 0.7) < 1e-6, "corners without a value are left out");
  check(Math.abs(interpolateMove(v, 0, 1, 2, [1.2, -0.1, -0.1])! - 0.1) < 1e-6, "negative weights count as 0");
}

// the limits
check((DEVIATION_LIMITS as readonly number[]).includes(DEFAULT_DEVIATION_LIMIT), "the default is one of the choices");
check(validLimit(0.2) === 0.2 && validLimit(0.3) === DEFAULT_DEVIATION_LIMIT && validLimit("x") === DEFAULT_DEVIATION_LIMIT, "validLimit");

console.log(`deviation.test: ${checks} checks passed`);
