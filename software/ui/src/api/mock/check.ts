/**
 * The demo engine's Check (APP_API.md "M5 third slice: areas that need another pass"): a stand-in for
 * `ohs.mesh.weak_areas` that follows its rule, on the mock's meshes.
 *
 * - A face fails when it is filled or one of its vertices has no usable sigma (NaN, zero or less, 50 mm
 *   and above): "not measured"; or when `k` times the mean sigma of its three vertices is above the
 *   tolerance: "uncertain".
 * - Failing faces that share an edge form an area (two patches touching at one vertex stay two). An area
 *   is "not measured" when most of its surface is.
 * - Areas under the minimum size (4 mm², and never fewer than 10 median faces) are left out of the list
 *   but count in the shares, which are by surface area.
 *
 * The demo meshes carry no measured sigma, so {@link madeUpSigma} makes one up from where each vertex
 * lies. Imports nothing at run time, so `npm test` checks it with plain node.
 */
import type { CheckKind } from "../types";

/** A vertex sigma of 50 mm (and above) means "nothing measured it" (the engine's `SIGMA_NONE_MM`). */
export const SIGMA_NONE_MM = 50;
/** The smallest area worth listing, whatever the mesh (mm²): a 2 x 2 mm spot. */
export const MIN_AREA_FLOOR_MM2 = 4;
/** ... and never fewer than this many median faces. */
export const MIN_AREA_FACES = 10;
export const MAX_AREAS = 50;

export type WeakArea = {
  kind: CheckKind;
  area_mm2: number;
  share: number;
  center: [number, number, number];
  extent_mm: number;
  radius_mm: number;
  n_faces: number;
  sigma_median_mm: number | null;
  sigma_worst_mm: number | null;
  /** Sorted face indices. */
  faces: Uint32Array;
};
export type WeakAreas = {
  has_sigma: boolean;
  tolerance_mm: number;
  k: number;
  min_area_mm2: number;
  area_mm2: number;
  share_ok: number | null;
  share_uncertain: number | null;
  share_not_measured: number | null;
  n_areas: number;
  areas: WeakArea[];
};
export type WeakOptions = { k?: number; minAreaMm2?: number | null; maxAreas?: number };

const measured = (s: number) => Number.isFinite(s) && s > 0 && s < SIGMA_NONE_MM;

/**
 * The areas of the mesh that miss `toleranceMm`. `sigma`: one value per vertex (mm), or null for a mesh
 * without confidence (then only the `filled` faces fail and `has_sigma` is false). `filled`: one flag
 * per face, or null. Throws on a bad tolerance or arrays of the wrong length, as the engine does.
 */
export function weakAreas(
  positions: ArrayLike<number>,
  indices: ArrayLike<number>,
  sigma: ArrayLike<number> | null,
  filled: ArrayLike<number | boolean> | null,
  toleranceMm: number,
  options: WeakOptions = {},
): WeakAreas {
  const k = options.k ?? 2;
  const maxAreas = options.maxAreas ?? MAX_AREAS;
  if (!Number.isFinite(toleranceMm) || toleranceMm <= 0) throw new Error(`the tolerance must be above 0 mm, got ${toleranceMm}`);
  if (!Number.isFinite(k) || k <= 0) throw new Error(`k must be above 0, got ${k}`);
  const givenMin = options.minAreaMm2 ?? null;
  if (givenMin !== null && (!Number.isFinite(givenMin) || givenMin < 0)) throw new Error(`the minimum area must be 0 mm^2 or more, got ${givenMin}`);
  if (maxAreas < 0) throw new Error(`max_areas must be 0 or more, got ${maxAreas}`);
  const P = positions;
  const I = indices;
  const V = Math.floor(P.length / 3);
  const F = Math.floor(I.length / 3);
  if (sigma && sigma.length !== V) throw new Error(`${sigma.length} sigma values for ${V} vertices`);
  if (filled && filled.length !== F) throw new Error(`${filled.length} filled flags for ${F} faces`);

  // each face's area, the whole surface, and the minimum size of a listed area
  const area = new Float64Array(F);
  let total = 0;
  for (let f = 0; f < F; f++) {
    const a = I[f * 3] * 3,
      b = I[f * 3 + 1] * 3,
      c = I[f * 3 + 2] * 3;
    const ux = P[b] - P[a],
      uy = P[b + 1] - P[a + 1],
      uz = P[b + 2] - P[a + 2];
    const vx = P[c] - P[a],
      vy = P[c + 1] - P[a + 1],
      vz = P[c + 2] - P[a + 2];
    area[f] = Math.hypot(uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx) / 2;
    total += area[f];
  }
  let minArea = givenMin;
  if (minArea === null) {
    let med = 0;
    if (F) {
      const sorted = area.slice().sort();
      med = F % 2 ? sorted[(F - 1) / 2] : (sorted[F / 2 - 1] + sorted[F / 2]) / 2;
    }
    minArea = Math.max(MIN_AREA_FLOOR_MM2, MIN_AREA_FACES * med);
  }
  const answer = (ok: number | null, unc: number | null, nm: number | null, nAreas: number, areas: WeakArea[]): WeakAreas => ({
    has_sigma: sigma !== null,
    tolerance_mm: toleranceMm,
    k,
    min_area_mm2: minArea as number,
    area_mm2: total,
    share_ok: ok,
    share_uncertain: unc,
    share_not_measured: nm,
    n_areas: nAreas,
    areas,
  });
  if (F === 0 || total <= 0) return answer(null, null, null, 0, []);

  // per face: 0 passes, 1 uncertain, 2 not measured
  const state = new Uint8Array(F);
  let nmArea = 0,
    uncArea = 0,
    failing = 0;
  for (let f = 0; f < F; f++) {
    let st = filled && filled[f] ? 2 : 0;
    if (!st && sigma) {
      const sa = sigma[I[f * 3]],
        sb = sigma[I[f * 3 + 1]],
        sc = sigma[I[f * 3 + 2]];
      if (!(measured(sa) && measured(sb) && measured(sc))) st = 2;
      else if ((k * (sa + sb + sc)) / 3 > toleranceMm) st = 1;
    }
    if (!st) continue;
    state[f] = st;
    failing++;
    if (st === 2) nmArea += area[f];
    else uncArea += area[f];
  }
  const nmShare = nmArea / total;
  const uncShare = sigma ? uncArea / total : null;
  const okShare = sigma ? Math.max(0, 1 - nmShare - (uncShare as number)) : null;
  if (!failing) return answer(okShare, uncShare, nmShare, 0, []);

  // the failing faces, joined over shared edges: every edge goes under its lower vertex, and the faces
  // of two equal edges there are united
  const fidx = new Uint32Array(failing);
  for (let f = 0, n = 0; f < F; f++) if (state[f]) fidx[n++] = f;
  const start = new Uint32Array(V + 1);
  for (let j = 0; j < failing; j++) {
    const o = fidx[j] * 3;
    for (let e = 0; e < 3; e++) start[Math.min(I[o + e], I[o + ((e + 1) % 3)]) + 1]++;
  }
  for (let v = 0; v < V; v++) start[v + 1] += start[v];
  const fill = start.slice(0, V);
  const heHigh = new Uint32Array(failing * 3);
  const heFace = new Uint32Array(failing * 3);
  for (let j = 0; j < failing; j++) {
    const o = fidx[j] * 3;
    for (let e = 0; e < 3; e++) {
      const a = I[o + e],
        b = I[o + ((e + 1) % 3)];
      const at = fill[Math.min(a, b)]++;
      heHigh[at] = Math.max(a, b);
      heFace[at] = j;
    }
  }
  const parent = new Int32Array(failing);
  for (let j = 0; j < failing; j++) parent[j] = j;
  const find = (x: number) => {
    while (parent[x] !== x) {
      parent[x] = parent[parent[x]];
      x = parent[x];
    }
    return x;
  };
  for (let v = 0; v < V; v++)
    for (let i = start[v]; i < start[v + 1]; i++)
      for (let j = i + 1; j < start[v + 1]; j++)
        if (heHigh[i] === heHigh[j]) {
          const ra = find(heFace[i]),
            rb = find(heFace[j]);
          if (ra !== rb) parent[ra < rb ? rb : ra] = ra < rb ? ra : rb;
        }

  // groups, numbered by their first face; their area and how much of it was not measured
  const group = new Int32Array(failing);
  const label = new Int32Array(failing).fill(-1);
  let nGroups = 0;
  for (let j = 0; j < failing; j++) {
    const r = find(j);
    if (label[r] < 0) label[r] = nGroups++;
    group[j] = label[r];
  }
  const gArea = new Float64Array(nGroups);
  const gNm = new Float64Array(nGroups);
  const gFaces = new Uint32Array(nGroups);
  for (let j = 0; j < failing; j++) {
    const g = group[j];
    gArea[g] += area[fidx[j]];
    if (state[fidx[j]] === 2) gNm[g] += area[fidx[j]];
    gFaces[g]++;
  }
  const listed: number[] = [];
  for (let g = 0; g < nGroups; g++) if (gArea[g] >= minArea) listed.push(g);
  listed.sort((a, b) => gArea[b] - gArea[a] || a - b); // largest first, ties in face order
  const nAreas = listed.length;
  listed.length = Math.min(nAreas, maxAreas);

  // the listed areas' faces (ascending, as the failing faces are)
  const place = new Int32Array(nGroups).fill(-1);
  const lists = listed.map((g, i) => {
    place[g] = i;
    return new Uint32Array(gFaces[g]);
  });
  const filledTo = new Uint32Array(listed.length);
  for (let j = 0; j < failing; j++) {
    const i = place[group[j]];
    if (i >= 0) lists[i][filledTo[i]++] = fidx[j];
  }

  const stamp = new Int32Array(V).fill(-1);
  const areas = listed.map((g, i): WeakArea => {
    const faces = lists[i];
    let cx = 0,
      cy = 0,
      cz = 0;
    for (let n = 0; n < faces.length; n++) {
      const f = faces[n];
      const a = I[f * 3] * 3,
        b = I[f * 3 + 1] * 3,
        c = I[f * 3 + 2] * 3;
      const w = area[f] / 3;
      cx += w * (P[a] + P[b] + P[c]);
      cy += w * (P[a + 1] + P[b + 1] + P[c + 1]);
      cz += w * (P[a + 2] + P[b + 2] + P[c + 2]);
    }
    cx /= gArea[g];
    cy /= gArea[g];
    cz /= gArea[g];
    let r2 = 0;
    const lo = [Infinity, Infinity, Infinity];
    const hi = [-Infinity, -Infinity, -Infinity];
    const good: number[] = [];
    for (let n = 0; n < faces.length; n++)
      for (let e = 0; e < 3; e++) {
        const v = I[faces[n] * 3 + e];
        if (stamp[v] === i) continue;
        stamp[v] = i;
        const x = P[v * 3],
          y = P[v * 3 + 1],
          z = P[v * 3 + 2];
        r2 = Math.max(r2, (x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2);
        lo[0] = Math.min(lo[0], x);
        lo[1] = Math.min(lo[1], y);
        lo[2] = Math.min(lo[2], z);
        hi[0] = Math.max(hi[0], x);
        hi[1] = Math.max(hi[1], y);
        hi[2] = Math.max(hi[2], z);
        if (sigma && measured(sigma[v])) good.push(sigma[v]);
      }
    let median: number | null = null;
    let worst: number | null = null;
    if (good.length) {
      good.sort((a, b) => a - b);
      const h = good.length >> 1;
      median = good.length % 2 ? good[h] : (good[h - 1] + good[h]) / 2;
      worst = good[good.length - 1];
    }
    return {
      kind: 2 * gNm[g] > gArea[g] ? "not_measured" : "uncertain",
      area_mm2: gArea[g],
      share: gArea[g] / total,
      center: [cx, cy, cz],
      extent_mm: Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]),
      radius_mm: Math.sqrt(r2),
      n_faces: faces.length,
      sigma_median_mm: median,
      sigma_worst_mm: worst,
      faces,
    };
  });
  return answer(okShare, uncShare, nmShare, nAreas, areas);
}

// ------------------------------------------------------------------ a made-up sigma for the demo meshes
/** A place the scan is less sure of: an ellipsoid (centre, radii, mm). `none`: nothing was measured inside it. */
export type SigmaSpot = { c: [number, number, number]; r: [number, number, number]; gain: number; none?: boolean };
/** Where the made-up sigma comes from: the spots, and the middle and half size (x, z) of what the scan covers. */
export type SigmaScene = { spots: SigmaSpot[]; centre: [number, number]; half: [number, number] };

/** A vertex inside this share of a `none` spot's radii has no sigma: hole filling made it. */
const NONE_INSIDE = 0.85;

/**
 * A believable sigma (mm) for each vertex, from its position alone, so it needs no bookkeeping through
 * the mock's edits: a few hundredths of a mm over most of the surface, more towards the edge of what
 * the scan covers and around each spot, a noisy vertex now and then (specks), and 50 mm ("not
 * measured") inside the `none` spots: the mock's scan has no surface there, so a vertex there was made
 * by hole filling.
 */
export function madeUpSigma(positions: ArrayLike<number>, scene: SigmaScene): Float32Array {
  const V = Math.floor(positions.length / 3);
  const out = new Float32Array(V);
  for (let i = 0; i < V; i++) {
    const x = positions[i * 3],
      y = positions[i * 3 + 1],
      z = positions[i * 3 + 2];
    let s = 0.04 + 0.012 * Math.sin(x * 0.045 + 0.6) * Math.cos(z * 0.038 - 0.4) + 0.006 * Math.sin((x + z) * 0.11);
    // towards the edge of the scanned patch the views are few and flat
    const u = (x - scene.centre[0]) / scene.half[0],
      w = (z - scene.centre[1]) / scene.half[1];
    const t = Math.min(1, Math.max(0, (u * u * u * u + w * w * w * w - 0.45) / 0.45));
    s += 0.07 * t * t * (3 - 2 * t);
    let none = false;
    for (const spot of scene.spots) {
      const dx = (x - spot.c[0]) / spot.r[0],
        dy = (y - spot.c[1]) / spot.r[1],
        dz = (z - spot.c[2]) / spot.r[2];
      const d2 = dx * dx + dy * dy + dz * dz;
      if (d2 > 16) continue;
      const d = Math.sqrt(d2);
      if (spot.none && d < NONE_INSIDE) {
        none = true;
        break;
      }
      s += spot.gain * (d <= 1 ? 1 : Math.exp(-(((d - 1) / 0.9) ** 2)));
    }
    if (none) {
      out[i] = SIGMA_NONE_MM;
      continue;
    }
    // about one vertex in 2500 is noisy: a speck too small to list
    const h = Math.sin(x * 12.9898 + y * 78.233 + z * 37.719) * 43758.5453;
    if (h - Math.floor(h) < 0.0004) s += 0.45;
    out[i] = s;
  }
  return out;
}
