/**
 * Checks for the demo engine's Check (`npm test`): the rule of `ohs.mesh.weak_areas` on plates with a
 * known sigma pattern (the cases of the engine's own `tests/test_mesh_check.py`), and the made-up sigma
 * of the demo meshes. Plain TypeScript that Node runs as it is (type stripping), no test framework: a
 * failed check throws.
 */
import { madeUpSigma, MAX_AREAS, SIGMA_NONE_MM, weakAreas, type SigmaScene } from "./check.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`check.test: ${what}`);
}
const near = (a: number | null, b: number, tol = 1e-9) => a !== null && Math.abs(a - b) <= tol;
function throws(fn: () => unknown): boolean {
  try {
    fn();
  } catch {
    return true;
  }
  return false;
}

/** A flat plate of nx x ny vertices, `step` mm apart, two triangles per cell (z = 0). */
function grid(nx: number, ny: number, step: number): { P: Float32Array; I: Uint32Array } {
  const P = new Float32Array(nx * ny * 3);
  for (let j = 0; j < ny; j++)
    for (let i = 0; i < nx; i++) {
      P[(j * nx + i) * 3] = i * step;
      P[(j * nx + i) * 3 + 1] = j * step;
    }
  const I = new Uint32Array((nx - 1) * (ny - 1) * 6);
  let o = 0;
  for (let j = 0; j < ny - 1; j++)
    for (let i = 0; i < nx - 1; i++) {
      const a = j * nx + i,
        b = a + 1,
        c = a + nx,
        d = c + 1;
      I.set([a, b, d, a, d, c], o);
      o += 6;
    }
  return { P, I };
}

const NX = 101,
  NY = 61; // 100 x 60 mm, 12000 faces of 0.5 mm²
const FACE = 0.5;
function plate() {
  const { P, I } = grid(NX, NY, 1);
  return { P, I, S: new Float32Array(NX * NY).fill(0.02), F: I.length / 3 };
}
/** Set sigma on a rectangle of the plate's vertices (inclusive). */
function patch(S: Float32Array, x0: number, x1: number, y0: number, y1: number, value: number): void {
  for (let j = y0; j <= y1; j++) for (let i = x0; i <= x1; i++) S[j * NX + i] = value;
}
/** How many corners of each face have a sigma for which `test` holds. */
function facesWhere(I: Uint32Array, S: Float32Array, test: (s: number) => boolean, atLeast: number): number[] {
  const out: number[] = [];
  for (let f = 0; f < I.length / 3; f++) {
    let n = 0;
    for (let e = 0; e < 3; e++) if (test(S[I[f * 3 + e]])) n++;
    if (n >= atLeast) out.push(f);
  }
  return out;
}
const same = (a: ArrayLike<number>, b: ArrayLike<number>) => a.length === b.length && Array.prototype.every.call(a, (x: number, i: number) => x === b[i]);

// two patches, found separately, largest first
{
  const { P, I, S, F } = plate();
  patch(S, 10, 29, 10, 29, 0.2); // big: fails with one corner in it (2 x (0.2 + 0.02 + 0.02) / 3 = 0.16)
  patch(S, 60, 64, 20, 24, 0.08); // small: needs two corners (2 x (0.08 + 0.08 + 0.02) / 3 = 0.12)
  const r = weakAreas(P, I, S, null, 0.1);
  check(r.has_sigma && r.n_areas === 2 && r.areas.length === 2, "two areas");
  const wantBig = facesWhere(I, S, (s) => s > 0.19, 1);
  const wantSmall = facesWhere(I, S, (s) => s > 0.07 && s < 0.09, 2);
  const [a, b] = r.areas;
  check(a.kind === "uncertain" && b.kind === "uncertain", "both uncertain");
  check(a.n_faces === wantBig.length && b.n_faces === wantSmall.length, "the faces counted by hand");
  check(same(a.faces, wantBig) && same(b.faces, wantSmall), "the same faces, sorted");
  check(near(a.area_mm2, wantBig.length * FACE, 1e-6) && near(b.area_mm2, wantSmall.length * FACE, 1e-6), "areas");
  check(a.area_mm2 > b.area_mm2, "largest first");
  check(Math.abs(a.center[0] - 19.5) < 0.5 && Math.abs(a.center[1] - 19.5) < 0.5 && a.center[2] === 0, "the big one's centre");
  check(Math.abs(b.center[0] - 62) < 0.5 && Math.abs(b.center[1] - 22) < 0.5, "the small one's centre");
  check(Math.abs(a.extent_mm - 21) <= 1 && Math.abs(b.extent_mm - 6) <= 1, "extents");
  check(a.extent_mm / 2 <= a.radius_mm && a.radius_mm <= a.extent_mm, "the radius fits the extent");
  check(near(a.sigma_worst_mm, 0.2, 1e-6) && near(a.sigma_median_mm, 0.2, 1e-6), "the big one's sigma");
  check(near(b.sigma_worst_mm, 0.08, 1e-6) && near(b.sigma_median_mm, 0.08, 1e-6), "the small one's sigma");
  const total = F * FACE;
  const bad = (wantBig.length + wantSmall.length) * FACE;
  check(near(r.area_mm2, total, 1e-6) && near(a.share, a.area_mm2 / total), "the whole surface and an area's share");
  check(near(r.share_ok, 1 - bad / total, 1e-9) && near(r.share_uncertain, bad / total, 1e-9) && r.share_not_measured === 0, "shares by area");
  check(r.tolerance_mm === 0.1 && r.k === 2, "the tolerance and k as used");

  // a looser tolerance: nothing fails (2 x 0.2 = 0.4 < 0.5)
  const loose = weakAreas(P, I, S, null, 0.5);
  check(loose.n_areas === 0 && loose.areas.length === 0 && near(loose.share_ok, 1) && loose.share_uncertain === 0, "nothing fails at 0.5 mm");
  // exactly at the tolerance passes: the rule is "above"
  const flat = new Float32Array(NX * NY).fill(0.05);
  check(weakAreas(P, I, flat, null, 2 * Math.fround(0.05)).share_ok === 1, "2 sigma equal to the tolerance passes");
  check(weakAreas(P, I, flat, null, 0.0999).share_ok === 0, "2 sigma just above it fails");
  // k scales the test: with k = 1 only the 0.2 patch is above 0.1
  const k1 = weakAreas(P, I, S, null, 0.1, { k: 1 });
  check(k1.n_areas === 1 && near(k1.areas[0].sigma_worst_mm, 0.2, 1e-6), "k = 1");
  // an area under the minimum is not listed but counts
  const cut = weakAreas(P, I, S, null, 0.1, { minAreaMm2: (a.area_mm2 + b.area_mm2) / 2 });
  check(cut.n_areas === 1 && cut.areas.length === 1 && near(cut.areas[0].area_mm2, a.area_mm2), "the small one is left out");
  check(near(cut.share_ok, r.share_ok as number) && near(cut.min_area_mm2, (a.area_mm2 + b.area_mm2) / 2), "and still counts in the share");
  // the inputs are left alone
  const S0 = S.slice();
  weakAreas(P, I, S, null, 0.1);
  check(same(S, S0), "sigma untouched");
}

// the default minimum: a speck is left out, and it follows the face size
{
  const { P, I, S, F } = plate();
  S[30 * NX + 50] = 0.5; // one noisy vertex: its 6 faces are 3 mm²
  const r = weakAreas(P, I, S, null, 0.1);
  check(r.areas.length === 0 && r.n_areas === 0, "a speck is not listed");
  check(near(r.share_ok, 1 - (6 * FACE) / (F * FACE)), "but counts in the share");
  check(weakAreas(P, I, S, null, 0.1, { minAreaMm2: 0 }).n_areas === 1, "listed with no minimum");
  check(near(r.min_area_mm2, 5, 1e-6), "10 median faces of 0.5 mm²");
  const coarse = grid(21, 21, 4);
  check(near(weakAreas(coarse.P, coarse.I, new Float32Array(441).fill(0.02), null, 0.1).min_area_mm2, 80, 1e-4), "coarse: 10 faces of 8 mm²");
  const fine = grid(21, 21, 0.1);
  check(near(weakAreas(fine.P, fine.I, new Float32Array(441).fill(0.02), null, 0.1).min_area_mm2, 4), "fine: the floor of 4 mm²");
}

// filled faces and vertices without sigma are "not measured"
{
  const { P, I, S, F } = plate();
  const filled = new Uint8Array(F);
  const cells = facesWhere(I, patchOf(20, 29, 20, 29), (s) => s > 0, 3); // whole cells of a 10 x 10 vertex square
  for (const f of cells) filled[f] = 1;
  const r = weakAreas(P, I, S, filled, 0.1);
  check(r.n_areas === 1 && r.areas[0].kind === "not_measured" && r.areas[0].n_faces === cells.length, "a filled patch is one not measured area");
  check(near(r.share_not_measured, cells.length / F) && r.share_uncertain === 0, "its share");
  check(near(r.areas[0].sigma_worst_mm, 0.02, 1e-6), "its sigma numbers are those of its measured vertices");
  // without any sigma: only the filled faces, and no share that passes
  const none = weakAreas(P, I, null, filled, 0.1);
  check(!none.has_sigma && none.share_ok === null && none.share_uncertain === null, "no sigma: nothing to check against");
  check(none.n_areas === 1 && none.areas[0].kind === "not_measured" && none.areas[0].sigma_median_mm === null, "the filled area is still listed");
  check(near(none.share_not_measured, cells.length / F), "and its share");
  const bare = weakAreas(P, I, null, null, 0.1);
  check(!bare.has_sigma && bare.areas.length === 0 && bare.share_ok === null && bare.share_not_measured === 0, "no sigma, nothing filled");
  // NaN, zero, negative, 50 mm and above: no usable sigma
  for (const bad of [NaN, 0, -0.1, SIGMA_NONE_MM, 80]) {
    const S2 = S.slice();
    patch(S2, 40, 49, 30, 39, bad);
    const q = weakAreas(P, I, S2, null, 0.1);
    const want = facesWhere(I, S2, (s) => !(s > 0 && s < SIGMA_NONE_MM), 1);
    check(q.n_areas === 1 && q.areas[0].kind === "not_measured" && same(q.areas[0].faces, want), `sigma ${bad}: not measured`);
    check(near(q.share_not_measured, want.length / F) && q.share_uncertain === 0, `sigma ${bad}: its share`);
  }
  // nothing measured at all: one area, no sigma numbers
  const all = weakAreas(P, I, new Float32Array(NX * NY).fill(NaN), null, 0.1);
  check(all.share_ok === 0 && all.areas[0].n_faces === F && all.areas[0].sigma_median_mm === null && all.areas[0].sigma_worst_mm === null, "all NaN");

  function patchOf(x0: number, x1: number, y0: number, y1: number): Float32Array {
    const m = new Float32Array(NX * NY);
    patch(m, x0, x1, y0, y1, 1);
    return m;
  }
}

// the kind follows the larger part of an area
{
  const { P, I, S } = plate();
  patch(S, 20, 39, 20, 39, 0.3);
  patch(S, 40, 41, 20, 39, NaN); // a thin strip joined to it
  const r = weakAreas(P, I, S, null, 0.1);
  check(r.n_areas === 1 && r.areas[0].kind === "uncertain", "mostly uncertain");
  patch(S, 20, 39, 20, 39, NaN);
  patch(S, 40, 41, 20, 39, 0.3);
  check(weakAreas(P, I, S, null, 0.1).areas[0].kind === "not_measured", "mostly not measured");
}

// patches touching at one vertex stay two areas; a shared edge joins them
{
  const { P, I } = grid(6, 6, 1);
  const S = new Float32Array(36).fill(0.02);
  const v = (i: number, j: number) => j * 6 + i;
  const faceOf = (verts: number[]) => {
    const want = verts.slice().sort((a, b) => a - b).join();
    for (let f = 0; f < I.length / 3; f++) if (Array.from(I.subarray(f * 3, f * 3 + 3)).sort((a, b) => a - b).join() === want) return f;
    throw new Error(`no face ${want}`);
  };
  const f1 = faceOf([v(0, 0), v(1, 0), v(1, 1)]);
  const f2 = faceOf([v(1, 1), v(2, 1), v(2, 2)]); // shares only the vertex (1, 1) with f1
  const filled = new Uint8Array(I.length / 3);
  filled[f1] = filled[f2] = 1;
  let r = weakAreas(P, I, S, filled, 0.1, { minAreaMm2: 0 });
  check(r.n_areas === 2 && r.areas[0].n_faces === 1 && r.areas[1].n_faces === 1, "a shared vertex does not join");
  check(r.areas[0].faces[0] === Math.min(f1, f2), "equal areas stay in face order");
  const f3 = faceOf([v(0, 0), v(1, 1), v(0, 1)]); // shares an edge with f1
  filled[f3] = 1;
  r = weakAreas(P, I, S, filled, 0.1, { minAreaMm2: 0 });
  check(r.n_areas === 2 && r.areas[0].n_faces === 2 && r.areas[1].n_faces === 1, "a shared edge joins");
  check(same(r.areas[0].faces, [f1, f3].sort((a, b) => a - b)), "the joined faces");
}

// at most 50 areas (or as asked), the largest kept, the count of all of them told
{
  const { P, I, S } = plate();
  const sizes = [3, 5, 7, 9];
  [5, 25, 45, 70].forEach((x, n) => patch(S, x, x + sizes[n] - 1, 20, 20 + sizes[n] - 1, 0.3));
  const full = weakAreas(P, I, S, null, 0.1);
  const found = full.areas.map((a) => a.area_mm2);
  check(full.n_areas === 4 && found.length === 4 && found.every((x, i) => i === 0 || found[i - 1] >= x), "four areas, largest first");
  const two = weakAreas(P, I, S, null, 0.1, { maxAreas: 2 });
  check(two.n_areas === 4 && two.areas.length === 2 && two.areas[0].area_mm2 === found[0] && two.areas[1].area_mm2 === found[1], "the two largest of four");
  check(near(two.share_ok, full.share_ok as number), "the share does not depend on the list");
  check(weakAreas(P, I, S, null, 0.1, { maxAreas: 0 }).areas.length === 0, "none listed");
  check(MAX_AREAS === 50, "the API lists at most 50");
}

// an empty mesh, and what the engine refuses
{
  const r = weakAreas(new Float32Array(0), new Uint32Array(0), new Float32Array(0), null, 0.1);
  check(r.areas.length === 0 && r.share_ok === null && r.share_uncertain === null && r.share_not_measured === null && r.area_mm2 === 0, "an empty mesh");
  const { P, I, S, F } = plate();
  check(throws(() => weakAreas(P, I, S.subarray(1), null, 0.1)), "sigma of the wrong length");
  check(throws(() => weakAreas(P, I, S, new Uint8Array(F - 1), 0.1)), "filled flags of the wrong length");
  for (const tol of [0, -1, NaN, Infinity]) check(throws(() => weakAreas(P, I, S, null, tol)), `tolerance ${tol}`);
  check(throws(() => weakAreas(P, I, S, null, 0.1, { k: 0 })), "k = 0");
  check(throws(() => weakAreas(P, I, S, null, 0.1, { minAreaMm2: -1 })), "a negative minimum");
  check(throws(() => weakAreas(P, I, S, null, 0.1, { maxAreas: -1 })), "a negative list length");
}

// the demo's made-up sigma: sure in the open, less sure around a spot, nothing measured inside a hole
{
  const scene: SigmaScene = {
    spots: [
      { c: [30, 0, 30], r: [4, 2, 4], gain: 0.3, none: true },
      { c: [70, 0, 20], r: [8, 3, 5], gain: 0.2 },
    ],
    centre: [50, 30],
    half: [60, 40],
  };
  const { P: flat, I } = grid(NX, NY, 1);
  const P = new Float32Array(flat.length); // the plate laid in x-z, as the demo's table
  for (let i = 0; i < flat.length; i += 3) {
    P[i] = flat[i];
    P[i + 2] = flat[i + 1];
  }
  const S = madeUpSigma(P, scene);
  const at = (x: number, z: number) => S[z * NX + x];
  check(S.length === NX * NY, "one sigma per vertex");
  check(same(S, madeUpSigma(P, scene)), "the same mesh gets the same sigma");
  check(at(30, 30) === SIGMA_NONE_MM && at(31, 31) === SIGMA_NONE_MM, "inside the hole: not measured");
  check(at(34, 30) < SIGMA_NONE_MM && at(34, 30) > 0.2, "at its rim: measured, and unsure");
  check(at(70, 20) > 0.2 && at(70, 20) < 0.4, "in a weak spot: unsure, but measured");
  let lo = Infinity,
    n = 0,
    calm = 0;
  for (let i = 0; i < S.length; i++) {
    if (S[i] >= SIGMA_NONE_MM) continue;
    n++;
    lo = Math.min(lo, S[i]);
    if (S[i] < 0.15) calm++;
  }
  check(lo > 0.015, "never zero or negative");
  check(calm / n > 0.85, "most of the surface is well within 0.3 mm (2 sigma)");
  // through the rule: the default tolerance passes most of it and lists the hole and the weak spot
  const r = weakAreas(P, I, S, null, 0.3);
  check(r.has_sigma && (r.share_ok as number) > 0.85 && (r.share_ok as number) < 1, "0.3 mm: most passes, not all");
  check(r.areas.some((a) => a.kind === "uncertain") && (r.share_not_measured as number) > 0, "an uncertain area, and surface that was not measured");
  const tight = weakAreas(P, I, S, null, 0.05);
  check((tight.share_ok as number) < (r.share_ok as number) && (tight.share_ok as number) < 0.2, "0.05 mm: hardly anything passes");
  const sum = (r.share_ok as number) + (r.share_uncertain as number) + (r.share_not_measured as number);
  check(near(sum, 1, 1e-9), "the three shares add up to 1");
}

console.log(`check.test (mock): ${checks} checks passed`);
