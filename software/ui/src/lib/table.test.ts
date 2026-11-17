/**
 * Checks for the words around "Remove the table" (`npm test`). Plain TypeScript that Node runs as
 * it is (type stripping), no test framework: a failed check throws.
 */
import type { TablePreview } from "../api/types";
import { fmtArea, TABLE_HELP, TABLE_MARGIN_MM, tableEditLabel, tableFacts, validMargin } from "./table.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`table.test: ${what}`);
}

const preview = (over: Partial<TablePreview> = {}): TablePreview => ({
  version: 7,
  triangles: 715226,
  faces: "",
  n_faces: 699380,
  area_mm2: 240430,
  area_left_mm2: 5300,
  normal: [0, 0, 1],
  d: 0,
  share: 0.973,
  front_share: 0.02,
  behind_share: 0,
  flatness_mm: 0.09,
  band_mm: 0.36,
  margin_mm: 1,
  facing_deg: 45,
  lowest_mm: 0.08,
  loose_removed: 12,
  loose_left: 0,
  loose_left_mm2: 0,
  skipped: [],
  raised: [],
  warnings: [],
  rule: "",
  summary: "",
  ms: 1300,
  ...over,
});

// the label is the engine's
check(tableEditLabel(699380) === "Remove the table (699,380 faces)", tableEditLabel(699380));

// areas in cm²
check(fmtArea(240430) === "2,404 cm²", fmtArea(240430));
check(fmtArea(1234) === "12 cm²", fmtArea(1234));
check(fmtArea(320) === "3.2 cm²", fmtArea(320));

// what the panel says
{
  const f = tableFacts(preview());
  check(f.head === "699,380 faces (2,404 cm², 98 % of the mesh) are the table.", f.head);
  check(f.detail === "The table holds 97 % of the scan and is flat to 0.09 mm. The part is kept right down to the table.", f.detail);
  const up = tableFacts(preview({ lowest_mm: 2.04 }));
  check(up.detail.endsWith("The part is kept from 2.0 mm above the table."), up.detail);
  const none = tableFacts(preview({ lowest_mm: null }));
  check(none.detail.endsWith("Nothing of the scan would be left."), none.detail);
  // a thin part kept is said; one the rule is unsure of is in the engine's warnings, not here
  const raised = (kept: boolean) => ({ centre: [0, 0, 0] as [number, number, number], size_mm: 30, area_mm2: 700, height_mm: 0.5, scatter_mm: 0.08, kept, n_faces: 1400 });
  const thin = tableFacts(preview({ raised: [raised(true), raised(false)] }));
  check(thin.detail.endsWith("The part is kept right down to the table. A thin part lying flat stays."), thin.detail);
  const two = tableFacts(preview({ raised: [raised(true), raised(true)] }));
  check(two.detail.endsWith("2 thin parts lying flat stay."), two.detail);
}

// the margin the engine takes
check(TABLE_MARGIN_MM === 1, "the default margin is the engine's 1 mm");
check(validMargin(0) === 0 && validMargin(1.5) === 1.5 && validMargin(20) === 20, "0 to 20 mm are margins");
check(validMargin(-0.1) === null && validMargin(20.5) === null && validMargin(Number.NaN) === null, "the rest are not");

// the hover card says what it does, keeps and costs, and when it refuses
{
  const labels = TABLE_HELP.lines.map((l) => l.label);
  check(labels.join("|") === "What it does|What it keeps|What it costs|Its limit|When it says no", labels.join("|"));
  const limit = TABLE_HELP.lines.find((l) => l.label === "Its limit")!.text;
  check(/thinner than about 0\.5 mm/.test(limit) && /cannot be told from the table/.test(limit), limit);
  check(TABLE_HELP.lines.every((l) => l.text.endsWith(".")), "each line is a sentence");
}

console.log(`table.test: ${checks} checks passed`);
