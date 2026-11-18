/**
 * Checks for smoothEdit.ts (`npm test`): what the Smooth tab sends, and that "within 2σ" is off unless
 * the person turns it on (the owner's decision of 2026-11-17). Plain TypeScript that Node runs as it is,
 * no test framework: a failed check throws.
 */
import { SMOOTH_DEFAULTS, smoothEdit } from "./smoothEdit.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`smoothEdit.test: ${what}`);
}
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

// the defaults: Taubin 50 %, edges protected, everywhere, and NOT within 2σ, sigmas or not
check(SMOOTH_DEFAULTS.withinSigma === false, "within 2σ is off by default");
let e = smoothEdit(SMOOTH_DEFAULTS, true);
check(same(e.body, { op: "smooth", params: { method: "taubin", strength: 0.5, keep_edges: true } }), `the default request has no within_sigma: ${JSON.stringify(e.body)}`);
check(e.label === "Smooth (Taubin, 50 %)" && e.invalid === null, `the default label: ${e.label}`);
check(same(smoothEdit(SMOOTH_DEFAULTS, false).body, e.body), "the same without sigmas");

// turned on by the person: sent only when the mesh has sigmas
e = smoothEdit({ ...SMOOTH_DEFAULTS, withinSigma: true }, true);
check(e.body.params.within_sigma === true && e.label === "Smooth (Taubin, 50 %, within 2σ)", `turned on: ${e.label}`);
e = smoothEdit({ ...SMOOTH_DEFAULTS, withinSigma: true }, false);
check(!("within_sigma" in e.body.params) && e.label === "Smooth (Taubin, 50 %)", "turned on, but the mesh has no sigmas: not sent");

// where to smooth, the method, and a strength of 0
e = smoothEdit({ ...SMOOTH_DEFAULTS, where: "edges" }, true);
check(e.body.params.away_from_edges === true && !("flat_only" in e.body.params) && e.label === "Smooth (Taubin, 50 %, away from edges)", `off edges: ${e.label}`);
e = smoothEdit({ ...SMOOTH_DEFAULTS, where: "flat", withinSigma: true }, true);
check(e.body.params.flat_only === true && !("away_from_edges" in e.body.params) && e.label === "Smooth (Taubin, 50 %, flat areas only, within 2σ)", `flat only: ${e.label}`);
e = smoothEdit({ ...SMOOTH_DEFAULTS, method: "bilateral", strength: 0.3, keepEdges: false }, true);
check(e.body.params.keep_edges === true && e.label === "Smooth, keep edges (30 %)", `bilateral always keeps edges: ${e.label}`);
e = smoothEdit({ ...SMOOTH_DEFAULTS, keepEdges: false }, true);
check(e.body.params.keep_edges === false, "Taubin with the edge switch off");
check(smoothEdit({ ...SMOOTH_DEFAULTS, strength: 0 }, true).invalid === "Choose a strength above 0", "a strength of 0 is refused");

console.log(`smoothEdit.test: ${checks} checks passed`);
