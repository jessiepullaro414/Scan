/**
 * Checks for the laser-set helpers (`npm test`): labels, colours, the Settings choice and the rows
 * the laser calibration shows per configured line. Plain TypeScript that Node runs as it is (type
 * stripping), no test framework: a failed check throws.
 */
import {
  FOUR_LASERS,
  TWO_LASERS,
  laserChoice,
  laserColor,
  laserFamilies,
  laserFamily,
  laserLabel,
  laserRows,
  laserShort,
  laserWords,
  viewLaserSummary,
} from "./laserSet.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`laserSet.test: ${what}`);
}

// names
check(laserFamily("vertical_2") === "vertical" && laserFamily("horizontal") === "horizontal", "families");
check(laserFamily("diagonal") === null && laserFamily("verticalx") === null, "not a laser name");
check(laserLabel("vertical") === "Vertical" && laserLabel("horizontal_2") === "Horizontal 2", "labels");
check(laserLabel("vertical_left") === "Vertical left" && laserLabel("odd") === "odd", "other labels");
check(laserWords("vertical_2") === "second vertical" && laserWords("horizontal") === "horizontal", "words");
check(laserShort("vertical") === "V" && laserShort("horizontal_2") === "H2", "short tags");
const fam = laserFamilies(FOUR_LASERS);
check(fam.vertical.join() === "vertical,vertical_2" && fam.horizontal.join() === "horizontal,horizontal_2", "grouped by family");

// colours: the first pair as before, the second pair distinct, unknown names still coloured
check(laserColor("vertical") === "#ff5a4f" && laserColor("horizontal") === "#3ad3ff", "the standard colours");
const four = FOUR_LASERS.map((n) => laserColor(n));
check(new Set(four).size === 4, "four distinct colours");
const custom = ["vertical", "vertical_a", "vertical_b", "horizontal"];
const cc = custom.map((n) => laserColor(n, custom));
check(new Set(cc).size === 4 && cc.every((c) => /^#[0-9a-f]{6}$/.test(c)), "custom names get their own colours");

// the Settings choice
check(laserChoice(["vertical", "horizontal"]) === "two" && laserChoice(null) === "two", "two");
check(laserChoice([...FOUR_LASERS]) === "four", "four");
check(laserChoice(["horizontal", "vertical"]) === "custom" && laserChoice(["vertical", "horizontal", "vertical_2"]) === "custom", "custom");

// live rows: an older server (no per-laser points) shows the two lines as before
let rows = laserRows({ vertical: 40, horizontal: 3, vertical_uv: [], horizontal_uv: [] }, TWO_LASERS);
check(rows.length === 2 && rows[0].label === "Vertical line" && rows[0].points === 40 && rows[0].ok, "old server: vertical");
check(rows[1].label === "Horizontal line" && !rows[1].ok && rows[1].color === "#3ad3ff", "old server: horizontal");
// four lasers, named
rows = laserRows(
  { vertical: 80, horizontal: 70, vertical_uv: [], horizontal_uv: [], points: { vertical: 40, horizontal: 60, vertical_2: 40, horizontal_2: 2 } },
  FOUR_LASERS,
);
check(rows.map((r) => r.label).join("|") === "Vertical line|Vertical 2 line|Horizontal line|Horizontal 2 line", "named rows, by family");
check(rows[3].points === 2 && !rows[3].ok && rows[3].color === laserColor("horizontal_2"), "the low line misses");
// four lasers, not named yet: the lines found per family
rows = laserRows(
  {
    vertical: 70,
    horizontal: 40,
    vertical_uv: [],
    horizontal_uv: [],
    points: { vertical: null, horizontal: null, vertical_2: null, horizontal_2: null },
    found: { vertical: [40, 30], horizontal: [40] },
  },
  FOUR_LASERS,
);
check(rows.length === 4 && rows[0].label === "Vertical line 1 of 2" && rows[1].points === 30 && rows[1].ok, "unnamed vertical rows");
check(rows[3].label === "Horizontal line 2 of 2" && rows[3].points === 0 && !rows[3].ok, "the second horizontal line is not found");
check(rows.every((r) => r.unnamed), "unnamed rows say so");

// a captured view in the strip
let v = viewLaserSummary({ lines: { vertical: 40, horizontal: 33 } }, TWO_LASERS);
check(v !== null && v.text === "V40 H33" && v.ok, "two lasers: the old text");
v = viewLaserSummary({ lines: { vertical: 80, horizontal: 70 }, line_points: { vertical: 40, horizontal: 60, vertical_2: 40, horizontal_2: 2 } }, FOUR_LASERS);
check(v !== null && v.text === "V40 V2 40 H60 H2 2" && !v.ok, `four named: ${v?.text}`);
v = viewLaserSummary(
  { lines: { vertical: 70, horizontal: 60 }, line_points: { vertical: null, horizontal: null, vertical_2: null, horizontal_2: null }, lines_found: { vertical: [40, 30], horizontal: [40, 20] } },
  FOUR_LASERS,
);
check(v !== null && v.text === "V 40/30 H 40/20" && v.ok, `four unnamed: ${v?.text}`);
check(viewLaserSummary({ lines: null }, TWO_LASERS) === null, "stereo views have no lines");

console.log(`laserSet.test: ${checks} checks passed`);
