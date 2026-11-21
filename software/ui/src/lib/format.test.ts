/**
 * Checks for the one number formatter (UX_PLAN U32, `npm test`): a narrow no-break space before the
 * unit, thousands grouped the same way, a true minus, "1280 × 800", and the key ranges of U25.
 */
import { fmtAboutMm, fmtBytes, fmtDetailMm, fmtDuration, fmtInt, fmtMm, fmtNum, fmtPct, fmtSize, fmtUnit, keyRanges, MINUS, NNBSP } from "./format.ts";

let checks = 0;
function eq(a: unknown, b: unknown, what: string): void {
  checks++;
  if (a !== b) throw new Error(`check failed: ${what}\n  got:  ${JSON.stringify(a)}\n  want: ${JSON.stringify(b)}`);
}
const N = " ";

eq(NNBSP, N, "the narrow no-break space");
eq(fmtUnit(29.66, "fps", 1), `29.7${N}fps`, "fps with its unit (was 29.7fps)");
eq(fmtUnit(7754, "triangles"), `7,754${N}triangles`, "grouped, with the space (was 7,754tris)");
eq(fmtMm(453), `453${N}mm`, "mm (was 453mm)");
eq(fmtMm(0.25, 2), `0.25${N}mm`, "decimals");
// R19: a mesh detail one way everywhere ("0.5 mm", never "0.50 mm" beside it)
eq(fmtDetailMm(0.5), `0.5${N}mm`, "detail 0.5");
eq(fmtDetailMm(1), `1.0${N}mm`, "detail 1");
eq(fmtDetailMm(0.25), `0.25${N}mm`, "detail 0.25 keeps its second decimal");
eq(fmtDetailMm(null), "–", "detail unknown");
eq(fmtNum(1289), "1,289", "the same grouping everywhere (was 1289 beside 1,568)");
eq(fmtInt(1568), "1,568", "fmtInt groups the same way");
eq(fmtMm(-0.12, 2), `${MINUS}0.12${N}mm`, "a true minus");
eq(fmtNum(-0.001, 2), "0.00", "no minus on a rounded zero");
eq(fmtSize(1280, 800), `1280${N}×${N}800`, "one form for sizes (was 1280×800 beside 2560 × 800); sizes are not grouped");
eq(fmtSize(2560, 800), `2560${N}×${N}800`, "the stereo camera's side-by-side image");
eq(fmtSize(210, 297, "mm"), `210${N}×${N}297${N}mm`, "a size with its unit");
eq(fmtPct(0.12), `12${N}%`, "percent");
eq(fmtDuration(27), `27${N}s`, "seconds");
eq(fmtDuration(192), `3${N}min 12${N}s`, "minutes and seconds");
eq(fmtBytes(1.15 * 1024 ** 3), `1.15${N}GB`, "bytes");
eq(fmtAboutMm(254), `250${N}mm`, "a distance in a sentence, rounded to 10 mm (U22)");
eq(fmtUnit(null, "mm"), "–", "no number: a dash");

// U25: the shading keys, as one range when they run on
eq(keyRanges([1, 2, 3, 4, 5, 6, 7]).join(", "), "1–7", "seven modes: 1–7 (was 1–6)");
eq(keyRanges([1, 2, 3, 5, 6, 7]).join(", "), "1–3, 5–7", "a gap");
eq(keyRanges([1, 2, 3, 6]).join(", "), "1–3, 6", "a single key");
eq(keyRanges([1, 2, 6, 7]).join(", "), "1, 2, 6, 7", "two in a row are listed");

console.log(`format.test: ${checks} checks passed`);
