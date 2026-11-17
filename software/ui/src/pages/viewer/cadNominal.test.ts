/**
 * Checks for the "For CAD" group's words about approved nominal values (`npm test`; cadExport.ts `cadNominalLine`):
 * what the line says when sizes are used, when relations are only listed, with the measured values only, and that it
 * says nothing when nothing is approved or an older server says nothing. Plain TypeScript that Node runs as it is
 * (type stripping), no test framework.
 */
import type { CadNominal } from "../../api/types";
import { cadNominalLine } from "./cadExport.ts";

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

console.log(`cadNominal.test: ${checks} checks passed`);
