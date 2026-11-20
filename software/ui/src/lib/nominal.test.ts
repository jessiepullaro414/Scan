/**
 * Checks for the viewer's nominal values (`npm test`; nominal.ts): the chip's and the approval's words, the hover
 * text of an offer made only because of a known bias, a value typed by hand checked as the engine checks it, and the
 * ignored offers that come back when the value is measured again. Plain TypeScript that Node runs as it is (type
 * stripping), no test framework.
 */
import type { ApprovedNominal, KeptRelation, NominalOffer, NominalSuggestion } from "../api/types";
import {
  BIAS_SWITCH_HELP,
  MOVE_HELP,
  approvedText,
  approvedTip,
  biasOnlyHint,
  chipText,
  fmtNominal,
  ignoreKey,
  isIgnored,
  measuredText,
  memoryIgnoreStore,
  moveChoice,
  moveOptions,
  moveText,
  nominalText,
  parseNominal,
  setIgnored,
  sizeApproval,
  suggestionTip,
  valueApproval,
} from "./nominal.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`nominal.test: ${what}`);
}

// the words
check(fmtNominal(14) === "14" && fmtNominal(14.5) === "14.5" && fmtNominal(10.25) === "10.25" && fmtNominal(100) === "100" && fmtNominal(0) === "0", "numbers as written");
check(nominalText("diameter", 14) === "Ø 14" && nominalText("radius", 7) === "R 7", "sizes");
check(nominalText("angle", 90) === "90°" && nominalText("angle", 0, "parallel") === "parallel" && nominalText("distance", 10) === "10\u202fmm", "relation values");
check(chipText("diameter", { nominal: 14, word: null }) === "Ø 14?", "the chip");
check(chipText("angle", { nominal: 0, word: "parallel" }) === "parallel?", "a word's chip");
check(measuredText(14.0213, 0.0312, "mm") === "14.02 ± 0.03\u202fmm" && measuredText(0.3, null, "deg") === "0.30°", "measured");
check(measuredText(null, null, "mm") === "not measured now", "no measurement now");
check(measuredText(24.9763, 0.0012, "mm") === "24.976 ± 0.001\u202fmm", `a small ±: ${measuredText(24.9763, 0.0012, "mm")}`);

// an offer's hover text: the engine's sentence; a bias offer says so and that it is a simulator number
const two: NominalSuggestion = { nominal: 14, difference: -0.02, step: 1, allowed_by: "2 sigma", bias: null, word: null, directions: null, sentence: "Ø 14.02 ± 0.03\u202fmm: Ø 14\u202fmm? It is 0.02\u202fmm away." };
const bias: NominalSuggestion = { ...two, nominal: 14.5, allowed_by: "bias", sentence: "Ø 14.30 ± 0.03\u202fmm: Ø 14.5\u202fmm? … offered only because of a known bias …" };
const tip2 = suggestionTip(two, { poses: "tracked", poses_reason: "the processing report says the poses were tracked" });
check(tip2[0] === two.sentence && tip2.some((l) => l.includes("with tracking")) && tip2.some((l) => l.includes("simulator")), `2σ tip: ${tip2.join(" | ")}`);
check(!tip2.some((l) => l.startsWith("Offered only because")), "a 2σ offer does not claim a bias");
const tipB = suggestionTip(bias, null);
check(tipB.some((l) => l.startsWith("Offered only because of a known bias") && l.includes("simulator")), `bias tip: ${tipB.join(" | ")}`);

// a value typed by hand: a number that makes sense for that value
const p = parseNominal(" Ø 14,5 ", "diameter");
check(p.ok && p.nominal === 14.5, "a typed diameter");
check(parseNominal("90°", "angle").ok, "a typed angle");
for (const [text, value] of [["0", "diameter"], ["-1", "diameter"], ["1e9", "diameter"], ["120", "angle"], ["abc", "distance"], ["", "radius"]] as const) {
  const r = parseNominal(text, value);
  check(!r.ok && r.error.length > 0, `refused: ${text} for ${value}`);
}
check(parseNominal("0", "distance").ok && parseNominal("0", "angle").ok, "0 is a distance and an angle");

// approvals
const appr: ApprovedNominal = {
  value: "diameter",
  nominal: 10,
  unit: "mm",
  word: null,
  source: "suggested",
  measured: 10.04,
  sigma: 0.03,
  approved: "2026-11-17T12:00:00",
  measured_now: 10.4,
  sigma_now: 0.03,
  check_again: true,
  check_reason: "check again: it was approved against 10.04 ± 0.03\u202fmm, and it now measures 10.40 ± 0.03\u202fmm.",
};
check(approvedText(appr) === "nominal Ø 10", "the approval's words");
check(approvedText({ value: "angle", nominal: 0, word: "parallel" }) === "nominal parallel", "a word approved");
const at = approvedTip(appr);
check(at.includes("Accepted as offered") && at.includes("10.40 ± 0.03\u202fmm") && at.includes("Check again"), `the approval's tip: ${at}`);
check(sizeApproval([appr]) === appr && sizeApproval([{ ...appr, value: "radius" }])?.value === "radius" && sizeApproval([]) === null, "a fit's size");
check(valueApproval([{ ...appr, value: "angle" }], "angle")?.value === "angle" && valueApproval(undefined, "angle") === null, "a relation's value");

// ignored offers: until the value is measured again
const store = memoryIgnoreStore();
const key = ignoreKey("p/s", "f-boss", "diameter");
check(!isIgnored(store, key, 14.02), "not ignored at first");
setIgnored(store, key, 14.02);
check(isIgnored(store, key, 14.02), "ignored for this measurement");
check(!isIgnored(store, key, 14.03), "shown again when measured again");
check(!isIgnored(store, ignoreKey("p/s", "f-other", "diameter"), 14.02), "only for that fit");
setIgnored(store, key, 14.02, false);
check(!isIgnored(store, key, 14.02), "shown again on request");

// offers a known bias alone allows: off unless asked; the switch's hover card says what a bias is and what it does
check(BIAS_SWITCH_HELP.includes("simulator") && BIAS_SWITCH_HELP.includes("not yet measured on the real scanner"), `bias help: ${BIAS_SWITCH_HELP}`);
check(BIAS_SWITCH_HELP.includes("consistently") && BIAS_SWITCH_HELP.toLowerCase().includes("turned on"), "what a bias is, and what turning it on does");
const offerOff: NominalOffer = { value: "diameter", label: "Diameter", unit: "mm", measured: 14.3, sigma: 0.03, suggestions: [], reason_code: "already_round", reason: "…", bias_only: [14.5] };
check(biasOnlyHint(offerOff, false) === "A known bias alone would allow Ø 14.5: turn on “Offers a known bias allows” to see it.", `hint: ${biasOnlyHint(offerOff, false)}`);
check(biasOnlyHint(offerOff, true) === null && biasOnlyHint({ ...offerOff, bias_only: [] }, false) === null, "no hint when on, or with nothing held back");
check(biasOnlyHint({ ...offerOff, bias_only: undefined }, false) === null && biasOnlyHint(null, false) === null, "an older server: no hint");
check(biasOnlyHint({ ...offerOff, value: "angle", unit: "deg", bias_only: [0, 1] }, false)?.includes("0° or 1°") === true, "two values");
// an approval made with the bias allowed says that its check counts the bias
const withBias = approvedTip({ ...appr, check_again: false, with_bias: true });
check(withBias.includes("known bias"), `approved with the bias: ${withBias}`);
check(!approvedTip({ ...appr, check_again: false }).includes("known bias"), "an approval by the ± alone says nothing of a bias");

// a relation's move in the CAD file: listed only, or one of its two fits moves
const relListed = { a: "f-top", b: "f-bottom" } as Pick<KeptRelation, "a" | "b" | "move">;
check(moveChoice(relListed) === "listed" && moveChoice({ ...relListed, move: { shape: "b", order: 1, chosen: "2026-11-17T12:00:00" } }) === "b", "the choice");
const opts = moveOptions("Top", "Bottom");
check(opts.map((o) => o.value).join() === "listed,a,b" && opts.map((o) => o.label).join() === "Listed only,Move Top,Move Bottom", `options: ${JSON.stringify(opts)}`);
check(opts.every((o) => !!o.title), "each option says what it does");
check(moveOptions("A very long name for a plane", "B")[1].label === "Move A very lo…", `a long name: ${moveOptions("A very long name for a plane", "B")[1].label}`);
check(moveText("listed", "Top", "Bottom") === "Listed in the CAD file's notes only; no shape moves.", moveText("listed", "Top", "Bottom"));
check(moveText("b", "Top", "Bottom") === "In the CAD file, a copy of Bottom is turned and slid to suit Top; the measured fits stay as they are.", moveText("b", "Top", "Bottom"));
check(moveText("a", "Top", "Bottom").includes("a copy of Top") && moveText("a", "Top", "Bottom").includes("to suit Bottom"), "the first moves");
check(MOVE_HELP.includes("smallest") && MOVE_HELP.includes("order"), `move help: ${MOVE_HELP}`);

console.log(`nominal.test: ${checks} checks passed`);
