/**
 * The demo engine's laser comparison (APP_API.md "M5 fourth slice: where the lasers and the mesh
 * disagree"), made up: the demo engine measures nothing, but the Lasers shading needs something to show.
 * Two sets of laser lines cross the mesh, 12 and 20 mm apart (one set for each line laser); a vertex on
 * a line gets a small signed distance (a slow wave of up to a tenth of a mm plus a little scatter), the
 * given patches read further out or in (areas to scan again), and every other vertex has no value
 * (NaN). The numbers (`laserStats`) are counted from the array the way the engine counts them.
 *
 * Imports nothing at run time, so `npm test` checks it with plain node.
 */
import type { LaserInfo } from "../types";

/** Laser points farther than this from the surface count as stray (mm). */
export const MOCK_GATE_MM = 3;
/** The laser points of a demo scan (the demo report's `n_points_exported`). */
export const MOCK_LASER_POINTS = 41250;
/** Demo laser points that are far off the surface (glints): left out, and said so. */
export const MOCK_STRAY_POINTS = 230;

/** The spacing of the two sets of lines (mm), and the direction each set is counted along. */
const SETS: { every: number; n: [number, number, number] }[] = [
  { every: 12, n: [1, 0, 0] },
  { every: 20, n: [0, 0.6, 0.8] },
];

/** An area where the lasers and the mesh differ: centre, radius and the difference at its centre (mm). */
export type LaserPatch = { c: [number, number, number]; r: number; mm: number };

/** A number in 0..1 from an index, the same every time. */
function scatter(i: number): number {
  const x = Math.sin(i * 12.9898 + 4.1414) * 43758.5453;
  return x - Math.floor(x);
}

/** The mean edge length of (a sample of) the triangles, mm; 1 for an empty mesh. */
function meanEdge(P: ArrayLike<number>, I: ArrayLike<number>): number {
  const F = Math.floor(I.length / 3);
  if (!F) return 1;
  const step = Math.max(1, Math.floor(F / 3000));
  let sum = 0;
  let k = 0;
  for (let f = 0; f < F; f += step) {
    const a = I[f * 3] * 3,
      b = I[f * 3 + 1] * 3;
    sum += Math.hypot(P[b] - P[a], P[b + 1] - P[a + 1], P[b + 2] - P[a + 2]);
    k++;
  }
  return k ? sum / k : 1;
}

/**
 * One value per vertex (mm; NaN = no laser line near). The lines are a little wider than the mesh's
 * edges are long, so every line catches vertices whatever the mesh's resolution.
 */
export function mockLaserValues(positions: ArrayLike<number>, indices: ArrayLike<number>, patches: LaserPatch[] = []): Float32Array {
  const P = positions;
  const V = Math.floor(P.length / 3);
  const half = Math.max(0.5, 0.6 * meanEdge(P, indices));
  const out = new Float32Array(V).fill(NaN);
  for (let i = 0; i < V; i++) {
    const x = P[i * 3],
      y = P[i * 3 + 1],
      z = P[i * 3 + 2];
    let on = false;
    for (const s of SETS) {
      const u = (x * s.n[0] + y * s.n[1] + z * s.n[2]) / s.every;
      if (Math.abs(u - Math.round(u)) * s.every <= half) on = true;
    }
    if (!on) continue;
    let v = 0.11 * Math.sin(x * 0.045 + 0.8) * Math.cos(y * 0.06 + z * 0.02) + 0.04 * Math.sin(z * 0.09 + x * 0.03) + 0.08 * (scatter(i) - 0.5);
    for (const patch of patches) {
      const d = Math.hypot(x - patch.c[0], y - patch.c[1], z - patch.c[2]) / patch.r;
      if (d < 1) v += patch.mm * (1 - d * d) * (1 - d * d);
    }
    out[i] = v;
  }
  return out;
}

/**
 * The numbers of a value array, as the engine counts them: the vertices with a value, the share of the
 * surface they stand for (each vertex stands for a third of its triangles' area), the signed mean, and
 * rms, median, 95th percentile and largest of the distance without its sign; the shares within 0.1 and
 * 0.3 mm are of the vertices with a value. All null but `n` when no vertex has one.
 */
export function laserStats(values: Float32Array, positions: ArrayLike<number>, indices: ArrayLike<number>): LaserInfo["stats"] {
  const P = positions;
  const I = indices;
  const abs: number[] = [];
  let sum = 0,
    sq = 0,
    w1 = 0,
    w3 = 0;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (Number.isNaN(v)) continue;
    const a = Math.abs(v);
    abs.push(a);
    sum += v;
    sq += v * v;
    if (a <= 0.1) w1++;
    if (a <= 0.3) w3++;
  }
  const n = abs.length;
  if (!n) return { n: 0, covered: null, mean_mm: null, rms_mm: null, p50_mm: null, p95_mm: null, max_mm: null, within_0_1: null, within_0_3: null };
  let total = 0,
    covered = 0;
  for (let t = 0; t + 2 < I.length; t += 3) {
    const a = I[t] * 3,
      b = I[t + 1] * 3,
      c = I[t + 2] * 3;
    const ux = P[b] - P[a],
      uy = P[b + 1] - P[a + 1],
      uz = P[b + 2] - P[a + 2];
    const vx = P[c] - P[a],
      vy = P[c + 1] - P[a + 1],
      vz = P[c + 2] - P[a + 2];
    const third = Math.hypot(uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx) / 6;
    total += 3 * third;
    for (let k = 0; k < 3; k++) if (!Number.isNaN(values[I[t + k]])) covered += third;
  }
  abs.sort((x, y) => x - y);
  const q = (f: number) => abs[Math.min(n - 1, Math.floor(f * n))];
  return {
    n,
    covered: total > 0 ? covered / total : 0,
    mean_mm: sum / n,
    rms_mm: Math.sqrt(sq / n),
    p50_mm: q(0.5),
    p95_mm: q(0.95),
    max_mm: abs[n - 1],
    within_0_1: w1 / n,
    within_0_3: w3 / n,
  };
}

/**
 * `lasers.json` and the array of `lasers.bin` for a demo mesh. `available` false: a scan made without
 * laser points (no value anywhere, no points, no numbers).
 */
export function mockLasers(
  version: number,
  positions: ArrayLike<number>,
  indices: ArrayLike<number>,
  available: boolean,
  patches: LaserPatch[] = [],
): { info: LaserInfo; values: Float32Array } {
  const V = Math.floor(positions.length / 3);
  const values = available ? mockLaserValues(positions, indices, patches) : new Float32Array(V).fill(NaN);
  const stats = laserStats(values, positions, indices);
  // the points: most fall on the mesh (the rest past an edge or over a hole), a few of those are stray
  const total = available ? MOCK_LASER_POINTS : 0;
  const onSurface = stats.n ? Math.round(total * 0.967) : 0;
  const stray = stats.n ? MOCK_STRAY_POINTS : 0;
  return {
    info: { version, vertices: V, available, gate_mm: MOCK_GATE_MM, points: { total, on_surface: onSurface, used: onSurface - stray, stray }, stats },
    values,
  };
}
