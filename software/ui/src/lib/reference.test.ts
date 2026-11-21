/**
 * Checks for the comparison with a reference's pure part (`npm test`): the colours of the shading by state, the
 * readout, the units question with its two sizes, the alignment's and the areas' words, the coverage sentences.
 * Plain TypeScript that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { ComparisonCoverage, ReferenceAlignment, UnitsFinding } from "../api/types";
import { deviationColors } from "./deviation.ts";
import {
  ALIGN_METHODS,
  alignmentLines,
  coverageLines,
  fmtDev,
  fmtSize,
  OFF_REFERENCE,
  referenceColors,
  referenceReadout,
  regionSphere,
  regionTitle,
  regionUncertainty,
  rowMajor,
  stateAt,
  toleranceSentence,
  unitsChoice,
  unitsNote,
} from "./reference.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`reference.test: ${what}`);
}

const identity = Array.from({ length: 256 }, (_, i) => i);
const hexRgb = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));

// colours: compared vertices as the deviation map at the limit; not on the reference plain grey; not measured striped
{
  const values = new Float32Array([0.3, -0.3, NaN, NaN, 0]);
  const states = new Uint8Array([0, 0, 2, 1, 0]);
  const got = referenceColors(values, states, 0.3, "light", identity, "#d9d4cb");
  const dev = deviationColors(values, 0.3, "light", identity, "#d9d4cb");
  for (const i of [0, 1, 4]) check([0, 1, 2, 3].every((k) => got[i * 4 + k] === dev[i * 4 + k]), `compared vertex ${i} as the deviation map`);
  check(got[11] === 255 && [0, 1, 2].every((k) => got[8 + k] === hexRgb(OFF_REFERENCE.light)[k]), "not on the reference: plain grey, opaque");
  check(got[15] === 0 && [0, 1, 2].every((k) => got[12 + k] === hexRgb("#d9d4cb")[k]), "not measured: alpha 0, the striped grey");
  const out = new Uint8Array(20);
  check(referenceColors(values, states, 0.3, "dark", identity, "#8a8781", out) === out, "written into the given array");
}

// the readout and the state under a point
check(referenceReadout(0.123, 0) === "+0.12\u202fmm (outside)", "outside");
check(referenceReadout(-0.0345, 0) === "−0.035\u202fmm (inside)", "inside, with a true minus");
check(referenceReadout(0.0001, 0) === "0.000\u202fmm (on it)", "on it");
check(referenceReadout(NaN, 2) === "not on the CAD model" && referenceReadout(NaN, 1) === "not measured", "the two kinds of not compared");
check(stateAt([0, 2, 1], 0, 1, 2, [0.1, 0.7, 0.2]) === 2, "the nearest corner's state");
check(fmtDev(-1.234) === "−1.23\u202fmm" && fmtDev(0.5, false) === "0.50\u202fmm" && fmtDev(null) === "–", "deviations");

// the units question: the two sizes, or only "as it is" when no unit fits
{
  const u: UnitsFinding = {
    status: "pending",
    ratio: 25.2,
    factor: 25.4,
    unit: "inch",
    candidates: [{ unit: "inch", factor: 25.4 }],
    reference_size_mm: 6.1,
    scan_size_mm: 153.7,
    message: "…",
  };
  const c = unitsChoice(u)!;
  check(c.question === "Is the CAD model in inches?", c.question);
  check(c.scaled!.factor === 25.4 && c.scaled!.size === "155\u202fmm" && c.asIs.size === "6.10\u202fmm" && c.scan === "154\u202fmm", JSON.stringify(c));
  const far = unitsChoice({ ...u, factor: null, unit: null, candidates: [] })!;
  check(far.scaled === null && /very different/.test(far.question), "no unit fits: keep it or pick another reference");
  check(unitsChoice({ ...u, status: "same" }) === null, "nothing to ask");
  check(unitsNote({ ...u, status: "applied" }, 25.4) === "Scaled by 25.4 (inches to mm), as you confirmed.", "applied");
  check(unitsNote({ ...u, status: "kept" }, 1) === "Taken as mm, as you confirmed." && unitsNote({ ...u, status: "same" }, 1) === null, "kept, same");
  check(fmtSize(3937.2) === "3,937\u202fmm" && fmtSize(0.42) === "0.42\u202fmm" && fmtSize(12.34) === "12.3\u202fmm", "sizes");
}

// the three ways to align, each with its card
check(ALIGN_METHODS.map((m) => m.value).join() === "shape,datums,points", "three ways, a best fit first");
check(/spread/.test(ALIGN_METHODS[0].help) && /where it is/.test(ALIGN_METHODS[1].help) && /symmetric/.test(ALIGN_METHODS[2].help), "what each means");

// the alignment's result: which way, the verdict, the warnings, the distance from the datum pose
{
  const al = {
    method: "shape",
    described: "best fit on the shapes",
    accepted: true,
    verdict: "Aligned on the shapes (best fit): 97\u202f% ...",
    warnings: ["the scan's largest plane was kept"],
    fit: { robust_mm: 3, moved_mm: 0.02, taken: true, note: "made a least-squares fit: moved 0.020\u202fmm" },
    vs_datums: { rms_mm: 0.3, max_mm: 0.5, shift_mm: 0.2, angle_deg: 0.1, note: "The best fit lies 0.30\u202fmm (RMS) from the alignment on the datums." },
  } as unknown as ReferenceAlignment;
  const l = alignmentLines(al);
  check(l.title === "Aligned best fit on the shapes" && l.verdict.startsWith("Aligned on the shapes"), l.title);
  check(l.notes.length === 3 && l.notes[0] === "The scan's largest plane was kept." && /datums/.test(l.notes[2]), JSON.stringify(l.notes));
  check(alignmentLines({ ...al, accepted: false }).title.startsWith("Not aligned"), "refused");
}

// the answer and the coverage
check(toleranceSentence({ share_within: 0.866, share_above: 0.082, share_below: 0.052, tolerance_mm: 0.3 }) === "87\u202f% within ± 0.3\u202fmm (8.2\u202f% above, 5.2\u202f% below)", "the sentence");
check(toleranceSentence({ share_within: 1, share_above: 0, share_below: 0, tolerance_mm: 0.5 }) === "100\u202f% within ± 0.5\u202fmm", "all within");
check(/Nothing/.test(toleranceSentence({ share_within: null, share_above: null, share_below: null, tolerance_mm: 0.3 })), "nothing compared");
{
  const c: ComparisonCoverage = {
    scan_area_mm2: 1000,
    compared_area_mm2: 400,
    off_reference_share: 0.55,
    not_measured_share: 0.02,
    uncovered_share: 0.74,
    uncovered_area_mm2: 7400,
    reference_area_mm2: 10000,
  };
  const lines = coverageLines(c);
  check(lines.length === 3 && /55\u202f% of the scan is not on the CAD model/.test(lines[0]) && /2\u202f% of the scan was not measured/.test(lines[1]), lines.join(" | "));
  check(/74\u202f% of the CAD model's surface was not scanned/.test(lines[2]), lines[2]);
  check(coverageLines({ ...c, off_reference_share: 0, not_measured_share: 0, uncovered_share: 0 }).join() === "The scan covers all of the CAD model.", "all covered");
}

// an area: its words, and the scan's own uncertainty said plainly
{
  const r = { side: "below" as const, mean_mm: -0.84, worst_mm: -1.02, exceeds_uncertainty: true, sigma_median_mm: 0.05, center_scan: [1, 2, 3] as [number, number, number], radius_mm: 2 };
  check(regionTitle(r).title === "Inside by 0.84\u202fmm on average" && regionTitle(r).worst === "worst −1.02\u202fmm", JSON.stringify(regionTitle(r)));
  check(regionTitle({ ...r, side: "above", mean_mm: 0.4 }).title === "Outside by 0.40\u202fmm on average", "above: outside");
  const u = regionUncertainty(r);
  check(u.tone === "ok" && /real difference/.test(u.text) && /scatter here \(0\.10\u202fmm\)/.test(u.text), u.text);
  const w = regionUncertainty({ ...r, exceeds_uncertainty: false });
  check(w.tone === "warn" && /no evidence of a real difference/.test(w.text), w.text);
  check(regionUncertainty({ ...r, exceeds_uncertainty: null, sigma_median_mm: null }).tone === "none", "no sigma");
  const s = regionSphere(r);
  check(s.center.join() === "1,-2,-3" && s.radius === 6, "the view's axes, at least 6\u202fmm");
}

check(rowMajor([[1, 0, 0, 2], [0, 1, 0, 3], [0, 0, 1, 4], [0, 0, 0, 1]])!.join() === "1,0,0,2,0,1,0,3,0,0,1,4,0,0,0,1", "row-major");
check(rowMajor([[1, 2]]) === null && rowMajor(null) === null, "not a 4 x 4");

console.log(`reference.test: ${checks} checks passed`);
