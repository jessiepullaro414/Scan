/**
 * Checks for the "For CAD" group's words about approved nominal values (`npm test`; cadExport.ts `cadNominalLine`):
 * what the line says when sizes are used, when relations are only listed, with the measured values only, and that it
 * says nothing when nothing is approved or an older server says nothing. Plain TypeScript that Node runs as it is
 * (type stripping), no test framework.
 */
import type { CadMove, CadNominal } from "../../api/types";
import { cadMoveLines, cadNominalLine } from "./cadExport.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`cadNominal.test: ${what}`);
}

const none: CadNominal = { measured_only: false, sizes_used: 0, sizes: [], relations_listed: 0, relations: [], relations_note: null };
check(cadNominalLine(undefined) === null, "an older server: nothing said");
check(cadNominalLine(none) === null, "nothing approved: nothing said");
const one = cadNominalLine({ ...none, sizes_used: 1 });
check(one === "1 approved size is used in place of the measured one.", `one size: ${one}`);
const both = cadNominalLine({ ...none, sizes_used: 2, relations_listed: 1 });
check(both === "2 approved sizes are used in place of the measured ones; 1 approved relation is listed in the notes, not applied to the shapes.", `both: ${both}`);
const rel = cadNominalLine({ ...none, relations_listed: 2 });
check(rel === "2 approved relations are listed in the notes, not applied to the shapes.", `relations only: ${rel}`);
check(cadNominalLine({ ...none, measured_only: true })?.startsWith("Measured values only") === true, "measured only");

// relations a move applied
const moved = cadNominalLine({ ...none, relations_applied: 2, relations_listed: 1 });
check(moved === "2 approved relation values are applied by moving a shape; 1 approved relation is listed in the notes, not applied to the shapes.", `moved: ${moved}`);
check(cadNominalLine({ ...none, relations_applied: 1 }) === "1 approved relation value is applied by moving a shape.", "one applied");
// what each move did, and why one was not applied
const mv: CadMove = {
  id: "r-1",
  name: "Top to bottom",
  shape: "b",
  moved_id: "f-b",
  moved: "Bottom",
  fixed_id: "f-a",
  to_suit: "Top",
  applied: true,
  turn_deg: 0.05,
  slide_mm: 0,
  slide: [0, 0, 0],
  values: [],
  holds: true,
  check_again: false,
  reason: null,
  warnings: [],
  sentence: "Bottom turned 0.0500 deg about its own point and slid 0.0000\u202fmm, to suit Top",
};
const lines = cadMoveLines({ ...none, moves: [mv, { ...mv, id: "r-2", name: "Bottom to mid", applied: false, reason: "Bottom is already moved by \"Top to bottom\"" }] });
check(lines.length === 2 && lines[0] === "Top to bottom: Bottom turned 0.05° about its own point and slid 0.000\u202fmm, to suit Top.", `applied: ${lines[0]}`);
check(lines[1] === "Bottom to mid: not applied (Bottom is already moved by \"Top to bottom\"), listed only.", `refused: ${lines[1]}`);
check(cadMoveLines(undefined).length === 0 && cadMoveLines(none).length === 0, "nothing moved, or an older server");

console.log(`cadNominal.test: ${checks} checks passed`);
