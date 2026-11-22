/**
 * Checks for the words around "Remove the table" (`npm test`). Plain TypeScript that Node runs as
 * it is (type stripping), no test framework: a failed check throws.
 */
import type { TablePreview, TableRaised } from "../api/types";
import { encodeFaces } from "./fits.ts";
import { areaText, drawnAreas, fmtArea, TABLE_HELP, TABLE_MARGIN_MM, tableEditLabel, tableFacts, toggleKeep, validMargin } from "./table.ts";

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

// areas in mm², the one area unit beside the server's texts
check(fmtArea(240430) === "240,430\u202fmm²", fmtArea(240430));
check(fmtArea(1234) === "1,234\u202fmm²", fmtArea(1234));
check(fmtArea(3.2) === "3.2\u202fmm²", fmtArea(3.2));

// what the panel says
{
  const f = tableFacts(preview());
  check(f.head === "699,380 faces (240,430\u202fmm², 98\u202f% of the mesh) are the table.", f.head);
  check(f.detail === "The table holds 97\u202f% of the scan and is flat to 0.09\u202fmm. The part is kept right down to the table.", f.detail);
  const up = tableFacts(preview({ lowest_mm: 2.04 }));
  check(up.detail.endsWith("The part is kept from 2.0\u202fmm above the table."), up.detail);
  const none = tableFacts(preview({ lowest_mm: null }));
  check(none.detail.endsWith("Nothing of the scan would be left."), none.detail);
  // a thin part kept is said; one the rule is unsure of is in the engine's warnings, not here
  const raised = (kept: boolean): TableRaised => ({ centre: [0, 0, 0], size_mm: 30, area_mm2: 700, height_mm: 0.5, scatter_mm: 0.08, verdict: kept ? "kept" : "warned", kept, n_faces: 1400 });
  const thin = tableFacts(preview({ raised: [raised(true), raised(false)] }));
  check(thin.detail.endsWith("The part is kept right down to the table. A thin part lying flat stays."), thin.detail);
  const two = tableFacts(preview({ raised: [raised(true), raised(true)] }));
  check(two.detail.endsWith("2 thin parts lying flat stay."), two.detail);
}

// the margin the engine takes
check(TABLE_MARGIN_MM === 1, "the default margin is the engine's 1\u202fmm");
check(validMargin(0) === 0 && validMargin(1.5) === 1.5 && validMargin(20) === 20, "0 to 20\u202fmm are margins");
check(validMargin(-0.1) === null && validMargin(20.5) === null && validMargin(Number.NaN) === null, "the rest are not");

// the hover card says what it does, keeps and costs, and when it refuses
{
  const labels = TABLE_HELP.lines.map((l) => l.label);
  check(labels.join("|") === "What it does|What it keeps|What it costs|Its limit|When it says no", labels.join("|"));
  const limit = TABLE_HELP.lines.find((l) => l.label === "Its limit")!.text;
  check(/the preview says this scan's own limit/i.test(limit) && !/0\.\d mm/.test(limit), limit); // no fixed promise
  check(TABLE_HELP.lines.every((l) => l.text.endsWith(".")), "each line is a sentence");
}

// the raised areas the preview draws: kept and warned ones with their faces; the table's own unevenness is not drawn
{
  const r = (verdict: TableRaised["verdict"], faces: number[] | null, by_user = false): TableRaised => ({
    centre: [12.345, -6.789, 300.111], size_mm: 30.4, area_mm2: 700, height_mm: 0.214, scatter_mm: 0.06, verdict, kept: verdict === "kept",
    by_user, n_faces: faces?.length ?? 0, faces: faces ? encodeFaces(faces) : null,
  });
  const p = preview({ raised: [r("warned", [3, 1, 2]), r("kept", [7, 9]), r("uneven", null), r("kept", [5], true)] });
  const d = drawnAreas(p);
  check(d.length === 3 && d.map((x) => x.verdict).join() === "warned,kept,kept" && d[0].index === 0 && d[2].index === 3, JSON.stringify(d.map((x) => [x.verdict, x.index])));
  check(Array.from(d[0].faces).join() === "3,1,2" && d[2].byUser && !d[1].byUser, "faces and who kept it");
  check(d[0].radius > 15 && d[0].radius < 20, `a view round it (${d[0].radius})`);
  check(areaText(p.raised[0]) === "30\u202fmm across, 0.21\u202fmm high, at (12, \u22127, 300)\u202fmm", areaText(p.raised[0]));
  // "Keep this" adds a point on the area (its centre, to 0.01 mm); again takes it back
  const k1 = toggleKeep([], p.raised[0]);
  check(JSON.stringify(k1) === "[[12.35,-6.79,300.11]]", JSON.stringify(k1));
  check(toggleKeep(k1, p.raised[0]).length === 0, "Keep this twice: not kept");
}

console.log(`table.test: ${checks} checks passed`);
