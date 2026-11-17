/**
 * Checks for the flush along a seam as the viewer shows it (`flush.ts`, `npm test`): the chart's scaling (every station
 * across the width, the trusted readings and their ± band inside the height with the mean, a minimum height for a level
 * seam, a reading that is not trusted drawn at the edge rather than stretching the chart, round ticks), the stations'
 * colours, and the numbers in words (the mean first, lowest and highest and where, which panel stands proud). Plain
 * TypeScript that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { FlushRecord, FlushStation } from "../api/types.ts";
import { flushChart, flushColor, flushWords, LEVEL_MM, MIN_SPAN_MM, niceTicks, typicalSigma } from "./flush.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`flush.test: ${what}`);
}
const near = (a: number, b: number, tol = 1e-9) => Math.abs(a - b) <= tol;

function station(position: number, flush: number | null, sigma: number | null, trusted = true): FlushStation {
  return {
    position_mm: position,
    point: [position, 0, 0],
    direction: [1, 0, 0],
    normal: [0, 0, 1],
    gap_mm: 4,
    gap_sigma_mm: 0.1,
    flush_mm: flush,
    flush_sigma_mm: sigma,
    edge_a: null,
    edge_b: null,
    side_a: "measured",
    side_b: flush === null ? "missing" : "measured",
    bridged: false,
    gap_trusted: false,
    flush_trusted: trusted && flush !== null,
    notes: trusted ? [] : ["The flush differs from its neighbours' by +0.90 mm."],
    gap_by_rule: {},
    n_points_a: 20,
    n_points_b: 20,
    rms_a_mm: 0.02,
    rms_b_mm: 0.02,
  };
}

function record(stations: FlushStation[]): Pick<FlushRecord, "stations" | "flush"> {
  const t = stations.filter((s) => s.flush_trusted);
  const v = t.map((s) => s.flush_mm as number);
  const lo = v.length ? v.indexOf(Math.min(...v)) : -1,
    hi = v.length ? v.indexOf(Math.max(...v)) : -1;
  return {
    stations,
    flush: {
      lowest: v.length ? v[lo] : null,
      lowest_at_mm: v.length ? t[lo].position_mm : null,
      mean: v.length ? v.reduce((a, b) => a + b, 0) / v.length : null,
      highest: v.length ? v[hi] : null,
      highest_at_mm: v.length ? t[hi].position_mm : null,
      n_trusted: t.length,
      n_stations: stations.length,
    },
  };
}

const box = { width: 300, height: 120, left: 34, right: 8, top: 8, bottom: 18 };

// ---------------------------------------------------------------- round ticks
{
  check(JSON.stringify(niceTicks(0, 1, 4)) === JSON.stringify([0, 0.5, 1]), `0 to 1: ${niceTicks(0, 1, 4)}`);
  const t = niceTicks(-60, 61, 4);
  check(t.length >= 3 && t.every((x) => x >= -60 && x <= 61) && t.includes(0), `ticks over the seam: ${t}`);
  const steps = t.slice(1).map((x, i) => x - t[i]);
  check(steps.every((s) => near(s, steps[0], 1e-9)) && [1, 2, 5].includes(steps[0] / Math.pow(10, Math.floor(Math.log10(steps[0])))), `even round steps: ${steps}`);
  const f = niceTicks(0.87, 1.13, 3);
  check(f.length >= 2 && f.every((x) => x >= 0.87 - 1e-12 && x <= 1.13 + 1e-12), `flush ticks inside the range: ${f}`);
  check(niceTicks(1, 1).length === 1, "an empty range: one tick");
}

// ---------------------------------------------------------------- the chart's scaling
{
  // a seam of 41 stations, flush 1.0 ± 0.03 rising to 1.2, one station far off and not trusted, one with no reading
  const st: FlushStation[] = [];
  for (let i = 0; i <= 40; i++) st.push(station(-60 + 3 * i, 1 + (0.2 * i) / 40, 0.03));
  st[10] = station(st[10].position_mm, 3.5, 0.03, false);
  st[20] = station(st[20].position_mm, null, null, false);
  const rec = record(st);
  const c = flushChart(rec, box);
  check(near(c.x(-60), box.left) && near(c.x(60), box.width - box.right), "the first and the last station span the width");
  check(c.xRange[0] === -60 && c.xRange[1] === 60, "x range: the stations' positions");
  check(c.yRange[0] <= 1 - 0.03 && c.yRange[1] >= 1.2 + 0.03, `the trusted readings and their ± inside: ${c.yRange}`);
  check(c.yRange[1] < 2, `the far reading that is not trusted does not stretch the chart: ${c.yRange}`);
  const far = c.points[10];
  check(far.clipped && !far.trusted && near(far.y!, box.top), "drawn at the top edge, marked");
  check(c.points[20].y === null && !c.points[20].trusted, "a station with no reading has no point");
  check(c.points.filter((p) => p.y !== null).every((p) => p.y! >= box.top - 1e-9 && p.y! <= box.height - box.bottom + 1e-9), "every point inside the box");
  const p0 = c.points[0];
  check(p0.lo! > p0.y! && p0.hi! < p0.y!, "the ± band around the reading (y grows downwards)");
  check(near(p0.lo! - p0.y!, p0.y! - p0.hi!, 1e-9), "the band is symmetric");
  check(c.mean !== null && near(c.mean, c.y(rec.flush.mean!)), "the mean line at the mean");
  check(c.zero === null, "flush 0 is not in range here");
  check(c.y(c.yRange[0]) > c.y(c.yRange[1]), "a higher flush is drawn higher");
}
{
  // a level seam: the range is at least MIN_SPAN_MM round the readings, and 0 is in it
  const st = [0, 3, 6, 9].map((p) => station(p, 0.001 * p, 0.002));
  const c = flushChart(record(st), box);
  check(c.yRange[1] - c.yRange[0] >= MIN_SPAN_MM - 1e-12, `a level seam keeps a minimum height: ${c.yRange}`);
  check(c.zero !== null, "flush 0 is drawn on a level seam");
}
{
  // nothing trusted: the readings there are still scaled, and an empty record does not throw
  const c = flushChart(record([station(0, 0.5, 0.1, false), station(3, 0.7, 0.1, false)]), box);
  check(c.mean === null && c.yRange[0] <= 0.5 && c.yRange[1] >= 0.7, "no trusted station: the readings' range, no mean");
  const e = flushChart(record([]), box);
  check(e.points.length === 0 && e.mean === null, "no stations");
}

// ---------------------------------------------------------------- the colours
{
  const g = flushColor(1, false, 0, 2);
  check(g[0] === g[1] && g[1] === g[2], "not trusted: grey");
  check(flushColor(null, true, 0, 2).every((x) => x === g[0]), "no reading: grey");
  const lo = flushColor(0, true, 0, 2),
    hi = flushColor(2, true, 0, 2),
    mid = flushColor(1, true, 0, 2);
  check(lo[2] > lo[0] && hi[0] > hi[2], "low blue, high orange");
  check(mid.every((x) => x > 0.9), "the middle is light");
  check(flushColor(5, true, 0, 2).join() === hi.join(), "clamped above");
}

// ---------------------------------------------------------------- the words
{
  const st = [station(-30, 0.82, 0.03), station(-27, 0.86, 0.031), station(0, 0.9, 0.029), station(27, 0.78, 0.03)];
  const w = flushWords(record(st));
  check(w.mean === "+0.84", `the mean first, to the ±'s decimals: ${w.mean}`);
  check(w.sigma === "0.03", `the typical ±: ${w.sigma}`);
  check(w.lowest === "+0.78" && w.lowestAt === "at +27 mm along the seam", `the lowest and where: ${w.lowest} ${w.lowestAt}`);
  check(w.highest === "+0.90" && w.highestAt === "at 0 mm along the seam", `the highest and where: ${w.highest} ${w.highestAt}`);
  check(!!w.proud && w.proud.startsWith("Panel B") && w.proud.includes("0.84"), `B stands proud: ${w.proud}`);
  check(w.trusted === "4 of 4 stations trusted", w.trusted);
  check(typicalSigma(record(st)) === 0.03, "the median ±");
  const down = flushWords(record(st.map((s) => ({ ...s, flush_mm: -(s.flush_mm as number) }))));
  check(down.mean === "−0.84" && !!down.proud && down.proud.startsWith("Panel A"), `A stands proud: ${down.proud}`);
  const level = flushWords(record([station(0, 0.01, 0.02), station(3, -0.02, 0.02)]));
  check(!!level.proud && level.proud.includes("level") && level.proud.includes(String(LEVEL_MM)), `level: ${level.proud}`);
  const none = flushWords(record([station(0, null, null, false)]));
  check(none.mean === null && none.proud === null && none.trusted === "0 of 1 station trusted", "nothing trusted: no numbers");
}

console.log(`flush.test: ${checks} checks passed`);
