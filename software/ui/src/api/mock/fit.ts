/**
 * The demo engine's fitted shapes (APP_API.md "M5 first slice"): a stand-in for `ohs/measure` that
 * answers like it, same shapes and same errors, on the mock's meshes.
 *
 * - Plane: grow a region from the clicked face over faces whose normal agrees and whose centre stays
 *   within the tolerance of the plane, refitting it as the region grows (total least squares);
 * - Cylinder: an axis from the spread of the normals around the click, a circle across it, then the
 *   same growing; flat and doubly curved surfaces (the demo's ball) are refused with a hint;
 * - Hole: the boundary loop nearest the click, a circle (Gauss-Newton) in the plane of the faces
 *   around it. A hole is always a circle, as the engine's (its tolerance is null).
 *
 * The ± values are 1σ from the residuals (the demo meshes have no per-vertex sigma): exact for the
 * plane and the circle, a simple estimate for the cylinder; no mean sigma. Imports nothing at run time, so
 * `npm test` checks it with plain node.
 */
import type { CircleShape, CylinderShape, FitKind, FitShape, PlaneShape } from "../types";

export type FitMesh = { positions: Float32Array; normals: Float32Array; indices: Uint32Array };
export type FitAnswer =
  | { ok: true; kind: FitKind; shape: FitShape; faces: Uint32Array; tolerance: number | null; warnings: string[] }
  | { ok: false; detail: string; hint: string };

type V3 = [number, number, number];

const KINDS: FitKind[] = ["plane", "cylinder", "hole"];
const DEG = 180 / Math.PI;
const SMALL_REGION = 200; // points: fewer gets a warning
const NEAR_HOLE_MM = 10; // a click this close to a hole's edge picks it (as the engine)

const fail = (detail: string, hint: string): FitAnswer => ({ ok: false, detail, hint });
const clamp = (x: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, x));
const r6 = (x: number) => Math.round(x * 1e6) / 1e6;
const v6 = (v: ArrayLike<number>) => [r6(v[0]), r6(v[1]), r6(v[2])];

// ------------------------------------------------------------------ small linear algebra
/** Eigenvalues (ascending) and unit eigenvectors of a symmetric 3x3 matrix (Jacobi). */
export function eigSym3(m: number[][]): { values: number[]; vectors: V3[] } {
  const a = m.map((r) => r.slice());
  const v = [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ];
  for (let sweep = 0; sweep < 60; sweep++) {
    let p = 0,
      q = 1;
    for (const [i, j] of [
      [0, 1],
      [0, 2],
      [1, 2],
    ])
      if (Math.abs(a[i][j]) > Math.abs(a[p][q])) [p, q] = [i, j];
    const scale = Math.abs(a[0][0]) + Math.abs(a[1][1]) + Math.abs(a[2][2]) || 1;
    if (Math.abs(a[p][q]) <= 1e-15 * scale) break;
    const theta = (a[q][q] - a[p][p]) / (2 * a[p][q]);
    const t = Math.sign(theta || 1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
    const c = 1 / Math.sqrt(t * t + 1),
      s = t * c;
    for (let k = 0; k < 3; k++) {
      const akp = a[k][p],
        akq = a[k][q];
      a[k][p] = c * akp - s * akq;
      a[k][q] = s * akp + c * akq;
    }
    for (let k = 0; k < 3; k++) {
      const apk = a[p][k],
        aqk = a[q][k];
      a[p][k] = c * apk - s * aqk;
      a[q][k] = s * apk + c * aqk;
    }
    for (let k = 0; k < 3; k++) {
      const vkp = v[k][p],
        vkq = v[k][q];
      v[k][p] = c * vkp - s * vkq;
      v[k][q] = s * vkp + c * vkq;
    }
  }
  const order = [0, 1, 2].sort((i, j) => a[i][i] - a[j][j]);
  return { values: order.map((i) => a[i][i]), vectors: order.map((i) => [v[0][i], v[1][i], v[2][i]] as V3) };
}

/** Solve a 3x3 system; null when singular. */
function solve3(A: number[][], b: number[]): number[] | null {
  const det = (m: number[][]) =>
    m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
  const d = det(A);
  if (!Number.isFinite(d) || Math.abs(d) < 1e-300) return null;
  return [0, 1, 2].map((k) => det(A.map((row, i) => row.map((x, j) => (j === k ? b[i] : x)))) / d);
}

function inverse3(A: number[][]): number[][] | null {
  const cols = [0, 1, 2].map((k) => solve3(A, [0, 1, 2].map((i) => (i === k ? 1 : 0))));
  if (cols.some((c) => !c)) return null;
  return [0, 1, 2].map((i) => [0, 1, 2].map((j) => cols[j]![i]));
}

/** `v` or `-v`, whichever has its largest component positive (as the engine's `_canonical`). */
function canonical(v: V3): V3 {
  let k = 0;
  for (let i = 1; i < 3; i++) if (Math.abs(v[i]) > Math.abs(v[k])) k = i;
  return v[k] < 0 ? [-v[0], -v[1], -v[2]] : v;
}

/** Two unit vectors across `n`. */
function basis(n: V3): [V3, V3] {
  const e: V3 = Math.abs(n[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
  const u = unit(cross(n, e));
  return [u, cross(n, u)];
}
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a: ArrayLike<number>, b: ArrayLike<number>) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
function unit(a: V3): V3 {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
}

// ------------------------------------------------------------------ the mesh
type Topo = { vfStart: Uint32Array; vfList: Uint32Array };
const topos = new WeakMap<Uint32Array, Topo>();

/** Faces around each vertex (CSR), once per mesh. */
function topo(m: FitMesh): Topo {
  const hit = topos.get(m.indices);
  if (hit) return hit;
  const I = m.indices;
  const V = m.positions.length / 3;
  const vfStart = new Uint32Array(V + 1);
  for (let i = 0; i < I.length; i++) vfStart[I[i] + 1]++;
  for (let v = 0; v < V; v++) vfStart[v + 1] += vfStart[v];
  const fill = vfStart.slice(0, V);
  const vfList = new Uint32Array(I.length);
  for (let i = 0; i < I.length; i++) vfList[fill[I[i]]++] = (i / 3) | 0;
  const t = { vfStart, vfList };
  topos.set(m.indices, t);
  return t;
}

function faceCentre(m: FitMesh, f: number, out: number[]): number[] {
  const P = m.positions,
    I = m.indices;
  const a = I[f * 3] * 3,
    b = I[f * 3 + 1] * 3,
    c = I[f * 3 + 2] * 3;
  out[0] = (P[a] + P[b] + P[c]) / 3;
  out[1] = (P[a + 1] + P[b + 1] + P[c + 1]) / 3;
  out[2] = (P[a + 2] + P[b + 2] + P[c + 2]) / 3;
  return out;
}

/** The face's unit normal (zero for a degenerate face). */
function faceNormal(m: FitMesh, f: number, out: number[]): number[] {
  const P = m.positions,
    I = m.indices;
  const a = I[f * 3] * 3,
    b = I[f * 3 + 1] * 3,
    c = I[f * 3 + 2] * 3;
  const ux = P[b] - P[a],
    uy = P[b + 1] - P[a + 1],
    uz = P[b + 2] - P[a + 2];
  const vx = P[c] - P[a],
    vy = P[c + 1] - P[a + 1],
    vz = P[c + 2] - P[a + 2];
  const x = uy * vz - uz * vy,
    y = uz * vx - ux * vz,
    z = ux * vy - uy * vx;
  const l = Math.hypot(x, y, z) || Infinity;
  out[0] = x / l;
  out[1] = y / l;
  out[2] = z / l;
  return out;
}

/**
 * Grow a region of faces from `seed` over faces sharing a vertex, keeping those `accept` takes;
 * `refit` is called when the region has doubled (from 32 faces), so the test follows the shape, and
 * the faces refused so far are tried again under the new fit.
 */
function grow(m: FitMesh, seed: number, accept: (f: number) => boolean, refit: (faces: Uint32Array, n: number) => void): { faces: Uint32Array; n: number } {
  const F = m.indices.length / 3;
  const { vfStart, vfList } = topo(m);
  const state = new Uint8Array(F); // 1 taken, 2 refused
  let list = new Uint32Array(Math.min(F, 1 << 16));
  let refused: number[] = [];
  let head = 0,
    n = 0,
    next = 32;
  const take = (g: number) => {
    state[g] = 1;
    if (n === list.length) {
      const bigger = new Uint32Array(Math.min(F, list.length * 2));
      bigger.set(list);
      list = bigger;
    }
    list[n++] = g;
  };
  take(seed);
  while (head < n) {
    const f = list[head++];
    for (let k = 0; k < 3; k++) {
      const v = m.indices[f * 3 + k];
      for (let i = vfStart[v]; i < vfStart[v + 1]; i++) {
        const g = vfList[i];
        if (state[g]) continue;
        if (!accept(g)) {
          state[g] = 2;
          refused.push(g);
          continue;
        }
        take(g);
        if (n >= next) {
          next *= 2;
          refit(list, n);
          const again = refused;
          refused = [];
          for (const r of again) {
            if (accept(r)) take(r);
            else refused.push(r);
          }
        }
      }
    }
  }
  return { faces: list.slice(0, n).sort(), n };
}

/** The distinct vertices of some faces. */
function verticesOf(m: FitMesh, faces: ArrayLike<number>, n = faces.length): Uint32Array {
  const V = m.positions.length / 3;
  const seen = new Uint8Array(V);
  const out: number[] = [];
  for (let i = 0; i < n; i++)
    for (let k = 0; k < 3; k++) {
      const v = m.indices[faces[i] * 3 + k];
      if (!seen[v]) {
        seen[v] = 1;
        out.push(v);
      }
    }
  return Uint32Array.from(out);
}

/** Faces whose centre lies within `r` of `p`, grown from `seed` (the patch around a click). */
function patch(m: FitMesh, seed: number, p: ArrayLike<number>, r: number): Uint32Array {
  const c = [0, 0, 0];
  return grow(
    m,
    seed,
    (f) => {
      faceCentre(m, f, c);
      return Math.hypot(c[0] - p[0], c[1] - p[1], c[2] - p[2]) <= r;
    },
    () => {},
  ).faces;
}

// ------------------------------------------------------------------ plane
type PlaneFit = { c: V3; n: V3; lambda: number[] };

/** Total least squares through the vertices: the centroid and the direction of least spread. */
function planeOf(m: FitMesh, verts: ArrayLike<number>): PlaneFit | null {
  const P = m.positions;
  const N = verts.length;
  if (N < 3) return null;
  let cx = 0,
    cy = 0,
    cz = 0;
  for (let i = 0; i < N; i++) {
    const v = verts[i] * 3;
    cx += P[v];
    cy += P[v + 1];
    cz += P[v + 2];
  }
  cx /= N;
  cy /= N;
  cz /= N;
  let xx = 0,
    xy = 0,
    xz = 0,
    yy = 0,
    yz = 0,
    zz = 0;
  for (let i = 0; i < N; i++) {
    const v = verts[i] * 3;
    const x = P[v] - cx,
      y = P[v + 1] - cy,
      z = P[v + 2] - cz;
    xx += x * x;
    xy += x * y;
    xz += x * z;
    yy += y * y;
    yz += y * z;
    zz += z * z;
  }
  const e = eigSym3([
    [xx / N, xy / N, xz / N],
    [xy / N, yy / N, yz / N],
    [xz / N, yz / N, zz / N],
  ]);
  if (!(e.values[1] > 0)) return null; // all on a line
  return { c: [cx, cy, cz], n: canonical(e.vectors[0]), lambda: e.values };
}

/** The local noise around a click: the RMS off the plane of a small patch, as a tolerance. */
function localTolerance(m: FitMesh, seed: number, p: ArrayLike<number>, given: number | null): number {
  if (given !== null) return given;
  const pl = planeOf(m, verticesOf(m, patch(m, seed, p, 2.5)));
  if (!pl) return 0.25;
  return clamp(4 * Math.sqrt(Math.max(0, pl.lambda[0])), 0.05, 1);
}

function fitPlane(m: FitMesh, seed: number, p: ArrayLike<number>, given: number | null): FitAnswer {
  const tol = localTolerance(m, seed, p, given);
  let pl = planeOf(m, verticesOf(m, patch(m, seed, p, 2.5)));
  if (!pl) return fail("There's too little surface here to fit a plane", "Click inside a flat face, away from its edges.");
  const cosMax = Math.cos(25 / DEG);
  const c = [0, 0, 0],
    nf = [0, 0, 0];
  const region = grow(
    m,
    seed,
    (f) => {
      const q = pl!;
      faceNormal(m, f, nf);
      if (Math.abs(dot(nf, q.n)) < cosMax) return false;
      faceCentre(m, f, c);
      return Math.abs((c[0] - q.c[0]) * q.n[0] + (c[1] - q.c[1]) * q.n[1] + (c[2] - q.c[2]) * q.n[2]) <= tol;
    },
    (faces, n) => {
      pl = planeOf(m, verticesOf(m, faces, n)) ?? pl;
    },
  );
  const verts = verticesOf(m, region.faces);
  const fit = planeOf(m, verts);
  if (!fit || verts.length < 6) return fail("No plane found here: the surface is too small or not flat", "Click inside a flat face, or choose Cylinder for a round one.");
  const P = m.positions;
  let s2 = 0,
    lo = Infinity,
    hi = -Infinity;
  for (let i = 0; i < verts.length; i++) {
    const v = verts[i] * 3;
    const d = (P[v] - fit.c[0]) * fit.n[0] + (P[v + 1] - fit.c[1]) * fit.n[1] + (P[v + 2] - fit.c[2]) * fit.n[2];
    s2 += d * d;
    if (d < lo) lo = d;
    if (d > hi) hi = d;
  }
  const N = verts.length;
  const rms = Math.sqrt(s2 / N);
  const s = Math.sqrt(s2 / Math.max(1, N - 3)); // the noise, from the residuals
  const tilt = Math.sqrt(((s * s) / N) * (1 / fit.lambda[1] + 1 / fit.lambda[2])); // RMS angle of the normal, rad
  const shape: PlaneShape = {
    kind: "plane",
    point: v6(fit.c),
    normal: v6(fit.n),
    rms_mm: r6(rms),
    flatness_mm: r6(hi - lo),
    n: N,
    normal_sigma_deg: r6(tilt * DEG),
    offset_sigma_mm: r6(s / Math.sqrt(N)),
  };
  const warnings = N < SMALL_REGION ? [`small region: ${N} points`] : [];
  return { ok: true, kind: "plane", shape, faces: region.faces, tolerance: r6(tol), warnings };
}

// ------------------------------------------------------------------ circle (in a plane) and cylinder
type Circle2 = { x: number; y: number; r: number };

/** Algebraic (Kasa) circle through 2D points: the start for Gauss-Newton. */
function kasa(xs: Float64Array, ys: Float64Array): Circle2 | null {
  const N = xs.length;
  let sx = 0,
    sy = 0,
    sxx = 0,
    syy = 0,
    sxy = 0,
    sz = 0,
    sxz = 0,
    syz = 0;
  for (let i = 0; i < N; i++) {
    const x = xs[i],
      y = ys[i],
      z = x * x + y * y;
    sx += x;
    sy += y;
    sxx += x * x;
    syy += y * y;
    sxy += x * y;
    sz += z;
    sxz += x * z;
    syz += y * z;
  }
  const s = solve3(
    [
      [sxx, sxy, sx],
      [sxy, syy, sy],
      [sx, sy, N],
    ],
    [-sxz, -syz, -sz],
  );
  if (!s) return null;
  const x = -s[0] / 2,
    y = -s[1] / 2;
  const r2 = x * x + y * y - s[2];
  return r2 > 0 ? { x, y, r: Math.sqrt(r2) } : null;
}

/** Geometric circle fit (Gauss-Newton on |p - c| - r) with its 3x3 covariance from the residuals. */
function circleFit(xs: Float64Array, ys: Float64Array): { c: Circle2; rms: number; cov: number[][] } | null {
  let c = kasa(xs, ys);
  if (!c) return null;
  const N = xs.length;
  const J = (cc: Circle2) => {
    const A = [
      [0, 0, 0],
      [0, 0, 0],
      [0, 0, 0],
    ];
    const g = [0, 0, 0];
    let ss = 0;
    for (let i = 0; i < N; i++) {
      const dx = xs[i] - cc.x,
        dy = ys[i] - cc.y;
      const d = Math.hypot(dx, dy) || 1e-12;
      const f = d - cc.r;
      const j = [-dx / d, -dy / d, -1];
      for (let a = 0; a < 3; a++) {
        g[a] += j[a] * f;
        for (let b = 0; b < 3; b++) A[a][b] += j[a] * j[b];
      }
      ss += f * f;
    }
    return { A, g, ss };
  };
  for (let it = 0; it < 20; it++) {
    const { A, g } = J(c);
    const step = solve3(A, [-g[0], -g[1], -g[2]]);
    if (!step) break;
    c = { x: c.x + step[0], y: c.y + step[1], r: c.r + step[2] };
    if (Math.hypot(step[0], step[1], step[2]) < 1e-9 * (1 + c.r)) break;
  }
  const { A, ss } = J(c);
  const inv = inverse3(A);
  if (!inv || !(c.r > 0)) return null;
  const s2 = ss / Math.max(1, N - 3);
  return { c, rms: Math.sqrt(ss / N), cov: inv.map((row) => row.map((x) => x * s2)) };
}

/** Points across a direction: 2D coordinates in a basis (u, v) around `o`. */
function across(m: FitMesh, verts: ArrayLike<number>, o: V3, u: V3, v: V3): { xs: Float64Array; ys: Float64Array } {
  const P = m.positions;
  const xs = new Float64Array(verts.length),
    ys = new Float64Array(verts.length);
  for (let i = 0; i < verts.length; i++) {
    const k = verts[i] * 3;
    const x = P[k] - o[0],
      y = P[k + 1] - o[1],
      z = P[k + 2] - o[2];
    xs[i] = x * u[0] + y * u[1] + z * u[2];
    ys[i] = x * v[0] + y * v[1] + z * v[2];
  }
  return { xs, ys };
}

type Cyl = { axis: V3; q: V3; r: number };

/** Axis from the normals (the direction least spread in them) and a circle across it. */
function cylinderOf(m: FitMesh, verts: ArrayLike<number>): { cyl: Cyl; spread: number[] } | null {
  const Nn = m.normals;
  const P = m.positions;
  const M = [
    [0, 0, 0],
    [0, 0, 0],
    [0, 0, 0],
  ];
  let cx = 0,
    cy = 0,
    cz = 0;
  for (let i = 0; i < verts.length; i++) {
    const k = verts[i] * 3;
    for (let a = 0; a < 3; a++) for (let b = 0; b < 3; b++) M[a][b] += Nn[k + a] * Nn[k + b];
    cx += P[k];
    cy += P[k + 1];
    cz += P[k + 2];
  }
  const N = verts.length || 1;
  const e = eigSym3(M.map((r) => r.map((x) => x / N)));
  const axis = canonical(unit(e.vectors[0]));
  const o: V3 = [cx / N, cy / N, cz / N];
  const [u, v] = basis(axis);
  const { xs, ys } = across(m, verts, o, u, v);
  const c = kasa(xs, ys);
  if (!c) return null;
  const q: V3 = [o[0] + c.x * u[0] + c.y * v[0], o[1] + c.x * u[1] + c.y * v[1], o[2] + c.x * u[2] + c.y * v[2]];
  return { cyl: { axis, q, r: c.r }, spread: e.values };
}

function radialDistance(c: Cyl, p: ArrayLike<number>): { d: number; dir: V3 } {
  const w: V3 = [p[0] - c.q[0], p[1] - c.q[1], p[2] - c.q[2]];
  const t = dot(w, c.axis);
  const x: V3 = [w[0] - t * c.axis[0], w[1] - t * c.axis[1], w[2] - t * c.axis[2]];
  const d = Math.hypot(x[0], x[1], x[2]);
  return { d, dir: d ? [x[0] / d, x[1] / d, x[2] / d] : [0, 0, 0] };
}

const ROUND_HINT = "Click a round wall that runs straight along its length: a shaft, a boss or the wall of a bore.";

function fitCylinder(m: FitMesh, seed: number, p: ArrayLike<number>, given: number | null): FitAnswer {
  // a wider patch until the normals turn (a big round looks flat close up), or it is flat
  let start: ReturnType<typeof cylinderOf> = null;
  for (const radius of [4, 8, 16, 32]) {
    const verts = verticesOf(m, patch(m, seed, p, radius));
    if (verts.length < 20) continue;
    start = cylinderOf(m, verts);
    if (start && start.spread[1] >= 0.006) break;
  }
  // the normals' spread: across the round (λ1) it must be clearly above the noise, along the axis (λ0) small
  if (!start || start.spread[1] < 0.006 || start.cyl.r > 2000)
    return fail("No cylinder here: the surface is flat", `For a flat face choose Plane. ${ROUND_HINT}`);
  if (start.spread[0] > 0.3 * start.spread[1]) return fail("No cylinder here: the surface curves both ways (like a ball)", ROUND_HINT);
  let cyl = start.cyl;
  const tol = given ?? clamp(1.5 * localTolerance(m, seed, p, null), 0.08, 1);
  const cosMax = Math.cos(30 / DEG);
  const c = [0, 0, 0],
    nf = [0, 0, 0];
  const region = grow(
    m,
    seed,
    (f) => {
      faceCentre(m, f, c);
      const { d, dir } = radialDistance(cyl, c);
      if (Math.abs(d - cyl.r) > tol) return false;
      faceNormal(m, f, nf);
      return Math.abs(dot(nf, dir)) >= cosMax;
    },
    (faces, n) => {
      const next = cylinderOf(m, verticesOf(m, faces, n));
      if (next && next.cyl.r < 2000) cyl = next.cyl;
    },
  );
  const all = verticesOf(m, region.faces);
  const fin = cylinderOf(m, all);
  if (!fin || all.length < 12) return fail("No cylinder found here: too little of a round surface", ROUND_HINT);
  cyl = fin.cyl;
  // residuals, the length along the axis and the arc covered
  const P = m.positions;
  let s2 = 0,
    lo = Infinity,
    hi = -Infinity,
    cxs = 0,
    cys = 0,
    czs = 0;
  const [u, v] = basis(cyl.axis);
  const bins = new Uint8Array(36);
  for (let i = 0; i < all.length; i++) {
    const k = all[i] * 3;
    const pt = [P[k], P[k + 1], P[k + 2]];
    const { d, dir } = radialDistance(cyl, pt);
    s2 += (d - cyl.r) ** 2;
    const t = (pt[0] - cyl.q[0]) * cyl.axis[0] + (pt[1] - cyl.q[1]) * cyl.axis[1] + (pt[2] - cyl.q[2]) * cyl.axis[2];
    if (t < lo) lo = t;
    if (t > hi) hi = t;
    cxs += pt[0];
    cys += pt[1];
    czs += pt[2];
    const ang = Math.atan2(dot(dir, v), dot(dir, u));
    bins[Math.min(35, Math.floor(((ang + Math.PI) / (2 * Math.PI)) * 36))] = 1;
  }
  const N = all.length;
  const arc = (bins.reduce((a, b) => a + b, 0) / 36) * 2 * Math.PI;
  const L = Math.max(hi - lo, 1e-3);
  const s = Math.sqrt(s2 / Math.max(1, N - 5));
  const arcFactor = Math.sqrt((2 * Math.PI) / Math.max(arc, 0.3));
  const radiusSigma = (s / Math.sqrt(N)) * arcFactor;
  if (cyl.r < 3 * radiusSigma) return fail("No cylinder found here: too little of its round was scanned for its noise", ROUND_HINT);
  // the axis point nearest the points' centroid
  const g: V3 = [cxs / N, cys / N, czs / N];
  const tg = (g[0] - cyl.q[0]) * cyl.axis[0] + (g[1] - cyl.q[1]) * cyl.axis[1] + (g[2] - cyl.q[2]) * cyl.axis[2];
  const point: V3 = [cyl.q[0] + tg * cyl.axis[0], cyl.q[1] + tg * cyl.axis[1], cyl.q[2] + tg * cyl.axis[2]];
  const shape: CylinderShape = {
    kind: "cylinder",
    point: v6(point),
    axis: v6(cyl.axis),
    radius_mm: r6(cyl.r),
    diameter_mm: r6(2 * cyl.r),
    rms_mm: r6(Math.sqrt(s2 / N)),
    n: N,
    radius_sigma_mm: r6(radiusSigma),
    diameter_sigma_mm: r6(2 * radiusSigma),
    axis_sigma_deg: r6(((Math.SQRT2 * s) / (Math.sqrt(N) * (L / Math.sqrt(12)))) * arcFactor * DEG),
    point_sigma_mm: r6(((Math.SQRT2 * s) / Math.sqrt(N)) * arcFactor),
  };
  const warnings: string[] = [];
  if (N < SMALL_REGION) warnings.push(`small region: ${N} points`);
  if (arc < (2 * Math.PI) / 3) warnings.push(`only ${Math.round(arc * DEG)}° of the round was scanned: the ± values are rough`);
  return { ok: true, kind: "cylinder", shape, faces: region.faces, tolerance: r6(tol), warnings };
}

function fitHole(m: FitMesh, p: ArrayLike<number>, holes: Uint32Array[]): FitAnswer {
  const P = m.positions;
  let best = -1,
    bestD = Infinity;
  holes.forEach((loop, i) => {
    for (let k = 0; k < loop.length; k++) {
      const v = loop[k] * 3;
      const d = Math.hypot(P[v] - p[0], P[v + 1] - p[1], P[v + 2] - p[2]);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    }
  });
  if (best < 0 || bestD > NEAR_HOLE_MM)
    return fail(
      best < 0
        ? "No hole edge on this mesh; to measure a bore from its wall, use Cylinder"
        : `No hole edge within ${NEAR_HOLE_MM} mm of the click (the nearest edge is ${Math.round(bestD)} mm away); to measure a bore from its wall, use Cylinder`,
      "Click near the edge of a hole that was scanned all round.",
    );
  const loop = holes[best];
  if (loop.length < 6) return fail(`This hole is too small to fit: ${loop.length} points on its edge`, "Holes need at least 6 points on their edge; scan it closer.");
  // the faces around the edge: the region, and the plane of the circle
  const { vfStart, vfList } = topo(m);
  const ring = new Set<number>();
  for (let k = 0; k < loop.length; k++) for (let i = vfStart[loop[k]]; i < vfStart[loop[k] + 1]; i++) ring.add(vfList[i]);
  const faces = Uint32Array.from(ring).sort();
  const pl = planeOf(m, verticesOf(m, faces));
  if (!pl) return fail("No hole found here: the surface around the edge is not flat enough", "Choose Cylinder to fit the wall of a bore instead.");
  const [u, v] = basis(pl.n);
  const { xs, ys } = across(m, loop, pl.c, u, v);
  const fit = circleFit(xs, ys);
  if (!fit) return fail("No hole found here: its edge is too straight to fit a circle", "Click next to a round hole, or choose Plane.");
  const centre: V3 = [pl.c[0] + fit.c.x * u[0] + fit.c.y * v[0], pl.c[1] + fit.c.x * u[1] + fit.c.y * v[1], pl.c[2] + fit.c.x * u[2] + fit.c.y * v[2]];
  const rs = Math.sqrt(Math.max(0, fit.cov[2][2]));
  if (fit.c.r < 3 * rs) return fail("No hole found here: its edge does not fix a circle for its noise", "Click next to a round hole.");
  const shape: CircleShape = {
    kind: "circle",
    center: v6(centre),
    normal: v6(pl.n),
    radius_mm: r6(fit.c.r),
    diameter_mm: r6(2 * fit.c.r),
    rms_mm: r6(fit.rms),
    n: loop.length,
    radius_sigma_mm: r6(rs),
    diameter_sigma_mm: r6(2 * rs),
    center_sigma_mm: r6(Math.sqrt(Math.max(0, fit.cov[0][0] + fit.cov[1][1]))),
  };
  const warnings: string[] = [];
  if (loop.length < 24) warnings.push(`small region: ${loop.length} points on the edge`);
  if (fit.rms > 0.08 * fit.c.r) warnings.push(`the edge is not round: it strays ${fit.rms.toFixed(2)} mm from the circle (RMS)`);
  return { ok: true, kind: "hole", shape, faces, tolerance: null, warnings };
}

/**
 * Answer a `FitRequest` on mesh `m`. `holes` gives the mesh's hole edges (closed boundary loops, the
 * outer border left out) as vertex lists, asked only for a hole. Errors are the API's 422s.
 */
export function mockFit(m: FitMesh, body: Record<string, unknown>, holes: () => Uint32Array[]): FitAnswer {
  const kind = body.kind as FitKind;
  if (!KINDS.includes(kind)) return fail(`Unknown kind “${String(body.kind)}”`, "Use plane, cylinder or hole.");
  const F = m.indices.length / 3;
  const face = body.face;
  if (typeof face !== "number" || !Number.isInteger(face) || face < 0 || face >= F)
    return fail(`Face ${String(face)} is not in the mesh (it has ${F} faces)`, "The mesh may have changed: open it again and click once more.");
  const pt = body.point;
  if (!Array.isArray(pt) || pt.length !== 3 || !pt.every((x) => typeof x === "number" && Number.isFinite(x)))
    return fail("The point must be three numbers (mm)", "Send the clicked point on the face, in the mesh's frame.");
  const t = body.tolerance_mm;
  if (t !== undefined && t !== null && !(typeof t === "number" && t > 0 && t <= 10)) return fail("The tolerance must be above 0 and at most 10 mm", "Leave it out to have it chosen from the local noise.");
  const given = typeof t === "number" ? t : null;
  if (kind === "plane") return fitPlane(m, face, pt as number[], given);
  if (kind === "cylinder") return fitCylinder(m, face, pt as number[], given);
  return fitHole(m, pt as number[], holes());
}
