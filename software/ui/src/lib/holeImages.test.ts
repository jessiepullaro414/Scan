/**
 * Checks for the Hole tool's second answer (`npm test`, ENGINE_PLAN X15 step 2): the card shows the mesh's answer, then
 * "From the camera images" with the engine's sentences as they come; a refusal shows the engine's words and what to
 * do, never a number. Plain TypeScript that Node runs as it is, no test framework: a failed check throws.
 */
import type { CircleShape, HoleImagesReading } from "../api/types";
import { HOLE_IMAGES_TITLE, HOLE_IMAGES_WAITING, holeImagesAnswer, holeImagesOffered, holeImagesRefused, holeImagesWaiting, meshHoleBlock } from "./holeImages.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`holeImages.test: ${what}`);
}

const r: HoleImagesReading = {
  available: true,
  diameter_mm: 25.0213,
  pm_mm: 0.1108,
  centre: [1, 2, 3],
  normal: [0, 0, 1],
  frames_used: 18,
  frames_tried: 20,
  cover_deg: 301.4,
  warnings: [],
  words: [
    "Ø 25.02 mm ± 0.11 from the camera images (the rim where the face ends, read in 18 frames).",
    "This is the hole's edge in the clicked face, where the face ends: not the bore further down, not the outer edge of a chamfer or countersink, not the smallest gap through the hole.",
  ],
  seconds: 11.24,
  version: 3,
};

const a = holeImagesAnswer(r);
check(a.state === "answer" && a.title === HOLE_IMAGES_TITLE && a.title === "From the camera images", "the block's title");
check(a.state === "answer" && a.headline === "Ø 25.02 mm ± 0.11", `headline: ${a.state === "answer" ? a.headline : ""}`);
check(a.lines.length === 2 && a.lines[0] === r.words[0] && a.lines[1] === r.words[1], "the engine's sentences, as they come");
check(a.meta === "18 of 20 frames · 11.2 s", `meta: ${a.meta}`);

const w = holeImagesWaiting();
check(w.state === "waiting" && w.lines[0] === HOLE_IMAGES_WAITING && HOLE_IMAGES_WAITING === "Reading the rim from the camera frames…", "waiting");

const no = holeImagesRefused("The rim read in the images is 9 mm from the click: it is another hole's.", "Click on the face right beside the hole you mean, within a few millimetres of its edge.", { refused: true, frames_used: 14, frames_tried: 20, seconds: 12.3 });
check(no.state === "refused" && no.lines[0] === "No diameter from the camera images: the rim read in the images is 9 mm from the click: it is another hole's.", `refusal: ${no.lines[0]}`);
check(no.lines[1].startsWith("Click on the face right beside"), "what to do");
check(no.meta === "14 of 20 frames · 12.3 s", `refusal meta: ${no.meta}`);
check(no.lines.every((l) => !/\d+\.\d+ mm ±|Ø/.test(l)), "a refusal shows no diameter");
check(holeImagesRefused("Only 2 frames show the rim (at least 5 needed).", null).meta === null, "no counts: no meta");

const circle: CircleShape = { kind: "circle", center: [0, 0, 0], normal: [0, 0, 1], radius_mm: 11.9, diameter_mm: 23.8, rms_mm: 0.2, n: 400, radius_sigma_mm: 0.02, diameter_sigma_mm: 0.045, center_sigma_mm: 0.03 };
check(meshHoleBlock(circle, null).headline === "Ø 23.80 mm ± 0.04 from the mesh" || meshHoleBlock(circle, null).headline === "Ø 23.80 mm ± 0.05 from the mesh", "the mesh's answer");
const none = meshHoleBlock(null, "No hole could be fitted where you clicked.");
check(none.headline === "The mesh gives no circle here" && none.line === "No hole could be fitted where you clicked.", "the mesh gave none");

check(holeImagesOffered({ available: true, why: null }) && !holeImagesOffered({ available: false, why: "x" }) && !holeImagesOffered(null), "offered");

console.log(`holeImages.test: ${checks} checks passed`);
