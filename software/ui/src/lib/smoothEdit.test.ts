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
check(e.label === "Smooth 50\u202f% (evenly, sharp edges kept)" && e.invalid === null, `the default label: ${e.label}`);
check(same(smoothEdit(SMOOTH_DEFAULTS, false).body, e.body), "the same without sigmas");

// turned on by the person: sent only when the mesh has sigmas
e = smoothEdit({ ...SMOOTH_DEFAULTS, withinSigma: true }, true);
check(e.body.params.within_sigma === true && e.label === "Smooth 50\u202f% (evenly, sharp edges kept, within the scatter)", `turned on: ${e.label}`);
e = smoothEdit({ ...SMOOTH_DEFAULTS, withinSigma: true }, false);
check(!("within_sigma" in e.body.params) && e.label === "Smooth 50\u202f% (evenly, sharp edges kept)", "turned on, but the mesh has no sigmas: not sent");

// where to smooth, the method, and a strength of 0
e = smoothEdit({ ...SMOOTH_DEFAULTS, where: "edges" }, true);
check(e.body.params.away_from_edges === true && !("flat_only" in e.body.params) && e.label === "Smooth 50\u202f% (evenly, sharp edges kept, off edges)", `off edges: ${e.label}`);
e = smoothEdit({ ...SMOOTH_DEFAULTS, where: "flat", withinSigma: true }, true);
check(e.body.params.flat_only === true && !("away_from_edges" in e.body.params) && e.label === "Smooth 50\u202f% (evenly, sharp edges kept, flat areas only, within the scatter)", `flat only: ${e.label}`);
e = smoothEdit({ ...SMOOTH_DEFAULTS, method: "bilateral", strength: 0.3, keepEdges: false }, true);
check(e.body.params.keep_edges === true && e.label === "Smooth 30\u202f% (keeping the shape, sharp edges kept)", `bilateral always keeps edges: ${e.label}`);
e = smoothEdit({ ...SMOOTH_DEFAULTS, keepEdges: false }, true);
check(e.body.params.keep_edges === false && e.label === "Smooth 50 % (evenly)", `Taubin with the edge switch off: ${e.label}`);
// UX_PLAN R12: the server's label in words (APP_API.md "Edits"), no method names
for (const c of [SMOOTH_DEFAULTS, { ...SMOOTH_DEFAULTS, method: "bilateral" as const }]) check(!/Taubin|bilateral/i.test(smoothEdit(c, true).label), "no method names");
check(smoothEdit({ ...SMOOTH_DEFAULTS, strength: 0 }, true).invalid === "Choose a strength above 0", "a strength of 0 is refused");

console.log(`smoothEdit.test: ${checks} checks passed`);
