/**
 * Checks for the wall-thickness reading's display (`npm test`, ENGINE_PLAN X16): the card shows the engine's
 * one-sided sentences as they come, an "at most" headline and never a "±"; a refusal shows the engine's words.
 * Plain TypeScript that Node runs as it is, no test framework: a failed check throws.
 */
import type { WallReading } from "../api/types";
import { wallCard, wallOfferTitle } from "./wall.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`wall.test: ${what}`);
}

const r: WallReading = {
  available: true,
  at_most_mm: 1.23,
  reading_mm: 1.16,
  pm_mm: 0.03,
  mesh_mm: 1.87,
  frames: [8, 9],
  points: [2400, 3100],
  tilt_deg: 0.6,
  seconds: 4.2,
  words: [
    "At most 1.23 mm thick here (read from 17 camera frames: 8 on one side, 9 on the other).",
    "The scan cannot rule out that the wall is up to about 1 mm thinner: scans place a thin wall's two sides slightly too far apart (on simulated walls by 0.3 to 1.0 mm).",
    "For the real thickness, lay the part flat and scan it from above, or measure it with calipers.",
    "The mesh reads 1.87 mm here; the mesh holds thin walls thicker than the frames do.",
  ],
  timings_s: {},
  version: 3,
};

const c = wallCard(r);
check(c.headline === "At most 1.23 mm", `headline: ${c.headline}`);
check(c.lines.length === 4 && c.lines[0] === r.words[0] && c.lines[1].includes("up to about 1 mm thinner"), "the engine's sentences, as they come");
check(!c.headline.includes("±") && c.lines.every((l) => !l.includes("±")), "never a ±");
check(c.meta === "17 frames · 4.2 s", `meta: ${c.meta}`);

check(wallOfferTitle({ available: true, why: null }) === "A thin wall's thickness, read from the camera frames (T)", "offered");
check(wallOfferTitle({ available: false, why: "Not offered for a scan tracked by its markers." }) === "Not offered for a scan tracked by its markers.", "not offered: the reason");

console.log(`wall.test: ${checks} checks passed`);
