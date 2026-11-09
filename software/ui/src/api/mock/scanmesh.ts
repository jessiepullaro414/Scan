/**
 * The demo engine's processed scan: the demo scene (a sphere and a gauge block on a table, see
 * geom.ts) fused into a believable scan mesh at a chosen voxel size, like the engine's TSDF:
 *
 * - surface nets over the scene's signed distance, so the mesh is welded and the parts flow
 *   into the table with small fillets (one main piece);
 * - only what a hand-held scan sees: a ragged patch of the table, the parts' tops and sides;
 * - holes where the scanner saw nothing (a shiny top, a shadow, an oil stain), loose floaters
 *   from reflections, and a little scan noise (more before straightening);
 * - about 60k triangles at 2 mm, 250k at 1 mm and 1M at 0.5 mm.
 *
 * It also makes the camera trajectory, with drift, its straightened twin and the loop closures.
 * Everything is in the scan's world frame (the first camera frame), like the engine's files.
 */
import type { TriMesh, Work } from "./meshops";
import { compact, withNormals } from "./meshops";
import { apply, invert, rng, SCENE, trajectory, type Pose, type V3 } from "./geom";

/** World (first camera frame) from the scene frame, as the live scan and the M1 files use. */
export const T_WS: Pose = invert(trajectory(0));

const smin = (a: number, b: number, k: number) => {
  const h = Math.max(k - Math.abs(a - b), 0) / k;
  return Math.min(a, b) - h * h * k * 0.25;
};

const PARTS = { x0: -78, x1: 92, y0: -80, z0: -40, z1: 40 }; // the sphere and the block, scene frame (y down)

/** Signed distance of the scene (negative inside): table (y ≥ 0), sphere and block, filleted together. */
function sceneSdf(x: number, y: number, z: number): number {
  const table = -y;
  // far from the parts the table alone decides
  const dx0 = Math.max(PARTS.x0 - x, 0, x - PARTS.x1),
    dy0 = Math.max(PARTS.y0 - y, 0),
    dz0 = Math.max(PARTS.z0 - z, 0, z - PARTS.z1);
  if (Math.hypot(dx0, dy0, dz0) > Math.abs(table) + 6) return table;
  const { c, r } = SCENE.sphere;
  const sx = x - c[0],
    sy = y - c[1],
    sz = z - c[2];
  const sphere = Math.sqrt(sx * sx + sy * sy + sz * sz) - r;
  const { min, max } = SCENE.box;
  const rr = 1.2; // rounded edges, as a real gauge block's
  const qx = Math.abs(x - (min[0] + max[0]) / 2) - ((max[0] - min[0]) / 2 - rr);
  const qy = Math.abs(y - (min[1] + max[1]) / 2) - ((max[1] - min[1]) / 2 - rr);
  const qz = Math.abs(z - (min[2] + max[2]) / 2) - ((max[2] - min[2]) / 2 - rr);
  const box = Math.hypot(Math.max(qx, 0), Math.max(qy, 0), Math.max(qz, 0)) + Math.min(Math.max(qx, qy, qz), 0) - rr;
  return smin(smin(table, sphere, 5), box, 1.6);
}

/** The table patch the scan covers: ragged, about 360 × 270 mm. */
function inPatch(x: number, z: number): boolean {
  const u = (x - 2) / 178;
  const v = z / 134;
  const wobble = 0.05 * Math.sin(x * 0.041 + 0.7) + 0.045 * Math.sin(z * 0.057 - 1.1) + 0.03 * Math.sin((x - z) * 0.093) + 0.015 * Math.sin(x * 0.31 + z * 0.27);
  return u * u * u * u + v * v * v * v < 0.86 + wobble;
}

/** Places the scanner never saw: centre, radii (ellipsoid), in the scene frame. */
const HOLES: { c: V3; r: V3 }[] = [
  { c: [-38, -80, 0], r: [4.2, 3, 4.2] }, // the sphere's shiny top
  { c: [-38 + 28, -40 - 12, 26], r: [2.2, 2.2, 2.2] }, // a glint on the sphere's flank
  { c: [-110, 0, 60], r: [1.7, 1.5, 1.7] }, // table: small dropouts
  { c: [120, 0, -70], r: [2.6, 1.5, 2.6] },
  { c: [60, 0, 92], r: [1.2, 1.5, 1.2] },
  { c: [-150, 0, -40], r: [1.0, 1.5, 1.4] },
  { c: [-20, 0, -96], r: [6.5, 1.5, 5] }, // an oil stain that didn't scan
  { c: [112, 0, 32], r: [13, 1.5, 4.5] }, // the block's shadow
  { c: [92, -12, 5], r: [1.5, 2.6, 2.6] }, // the block's right side
  { c: [70, -25, -6], r: [6, 1.5, 2.4] }, // the block's polished top
];

function inHole(x: number, y: number, z: number): boolean {
  for (const h of HOLES) {
    const dx = (x - h.c[0]) / h.r[0],
      dy = (y - h.c[1]) / h.r[1],
      dz = (z - h.c[2]) / h.r[2];
    if (dx * dx + dy * dy + dz * dz < 1) return true;
  }
  return false;
}

// ------------------------------------------------------------------ surface nets
/** Adds the crossing on one cell edge (cell-local coordinates) to `acc` (x, y, z, count). */
function crossing(acc: Float64Array, va: number, vb: number, xa: number, ya: number, za: number, xb: number, yb: number, zb: number) {
  const t = va / (va - vb);
  acc[0] += xa + t * (xb - xa);
  acc[1] += ya + t * (yb - ya);
  acc[2] += za + t * (zb - za);
  acc[3]++;
}

/**
 * Surface nets of `f` over a grid of step `h`, in blocks of 8³ cells; a block whose centre is
 * farther from the surface than its half diagonal is skipped (f is 1-Lipschitz). Quads whose
 * centre fails `keep` are left out. Yields progress.
 */
function* surfaceNets(
  f: (x: number, y: number, z: number) => number,
  origin: V3,
  size: V3,
  h: number,
  keep: (x: number, y: number, z: number) => boolean,
  skipBlock: (x0: number, z0: number, x1: number, z1: number) => boolean,
): Work<{ positions: Float32Array; indices: Uint32Array }> {
  const B = 8;
  const nx = Math.ceil(size[0] / h) + 1,
    ny = Math.ceil(size[1] / h) + 1,
    nz = Math.ceil(size[2] / h) + 1;
  const nbx = Math.ceil((nx - 1) / B),
    nby = Math.ceil((ny - 1) / B),
    nbz = Math.ceil((nz - 1) / B);
  const blocks: (Int32Array | null)[] = new Array(nbx * nby * nbz).fill(null);
  const vals = new Float32Array((B + 1) ** 3);
  const acc = new Float64Array(4);
  let pos = new Float32Array(1 << 16);
  let np = 0;
  let idx = new Uint32Array(1 << 17);
  let ni = 0;
  const half = (B * h * Math.sqrt(3)) / 2 + 1e-4;
  const [ox, oy, oz] = origin;

  const cell = (i: number, j: number, k: number) => {
    const b = blocks[((k >> 3) * nby + (j >> 3)) * nbx + (i >> 3)];
    return b ? b[(i & 7) + ((j & 7) << 3) + ((k & 7) << 6)] : -1;
  };
  const px = (v: number) => pos[v * 3],
    py = (v: number) => pos[v * 3 + 1],
    pz = (v: number) => pos[v * 3 + 2];
  const quad = (a: number, b: number, c: number, d: number) => {
    if (a < 0 || b < 0 || c < 0 || d < 0) return;
    const cx = (px(a) + px(b) + px(c) + px(d)) / 4,
      cy = (py(a) + py(b) + py(c) + py(d)) / 4,
      cz = (pz(a) + pz(b) + pz(c) + pz(d)) / 4;
    if (!keep(cx, cy, cz)) return;
    if (ni + 6 > idx.length) {
      const g = new Uint32Array(idx.length * 2);
      g.set(idx);
      idx = g;
    }
    // split along the shorter diagonal
    const ac = (px(a) - px(c)) ** 2 + (py(a) - py(c)) ** 2 + (pz(a) - pz(c)) ** 2;
    const bd = (px(b) - px(d)) ** 2 + (py(b) - py(d)) ** 2 + (pz(b) - pz(d)) ** 2;
    if (ac <= bd) {
      idx[ni++] = a;
      idx[ni++] = b;
      idx[ni++] = c;
      idx[ni++] = a;
      idx[ni++] = c;
      idx[ni++] = d;
    } else {
      idx[ni++] = a;
      idx[ni++] = b;
      idx[ni++] = d;
      idx[ni++] = b;
      idx[ni++] = c;
      idx[ni++] = d;
    }
  };

  const total = nbx * nby * nbz;
  let seen = 0;
  for (let bz = 0; bz < nbz; bz++)
    for (let by = 0; by < nby; by++)
      for (let bx = 0; bx < nbx; bx++) {
        if (++seen % 256 === 0) yield seen / total;
        const i0 = bx * B,
          j0 = by * B,
          k0 = bz * B;
        const ci = Math.min(B, nx - 1 - i0),
          cj = Math.min(B, ny - 1 - j0),
          ck = Math.min(B, nz - 1 - k0);
        const x0 = ox + i0 * h,
          z0 = oz + k0 * h;
        if (Math.abs(f(x0 + (ci * h) / 2, oy + (j0 + cj / 2) * h, z0 + (ck * h) / 2)) > half) continue;
        if (skipBlock(x0, z0, x0 + ci * h, z0 + ck * h)) continue;
        const sx = ci + 1,
          sxy = sx * (cj + 1);
        for (let k = 0; k <= ck; k++)
          for (let j = 0; j <= cj; j++)
            for (let i = 0; i <= ci; i++) vals[i + j * sx + k * sxy] = f(ox + (i0 + i) * h, oy + (j0 + j) * h, oz + (k0 + k) * h);
        const vb = new Int32Array(B * B * B).fill(-1);
        blocks[(bz * nby + by) * nbx + bx] = vb;
        for (let k = 0; k < ck; k++)
          for (let j = 0; j < cj; j++)
            for (let i = 0; i < ci; i++) {
              const o = i + j * sx + k * sxy;
              const v0 = vals[o],
                v1 = vals[o + 1],
                v2 = vals[o + sx],
                v3 = vals[o + sx + 1],
                v4 = vals[o + sxy],
                v5 = vals[o + sxy + 1],
                v6 = vals[o + sxy + sx],
                v7 = vals[o + sxy + sx + 1];
              const s0 = v0 < 0,
                s1 = v1 < 0,
                s2 = v2 < 0,
                s3 = v3 < 0,
                s4 = v4 < 0,
                s5 = v5 < 0,
                s6 = v6 < 0,
                s7 = v7 < 0;
              if (s0 === s1 && s0 === s2 && s0 === s3 && s0 === s4 && s0 === s5 && s0 === s6 && s0 === s7) continue;
              // the vertex: mean of the edge crossings (cell-local coordinates)
              acc.fill(0);
              if (s0 !== s1) crossing(acc, v0, v1, 0, 0, 0, 1, 0, 0);
              if (s2 !== s3) crossing(acc, v2, v3, 0, 1, 0, 1, 1, 0);
              if (s4 !== s5) crossing(acc, v4, v5, 0, 0, 1, 1, 0, 1);
              if (s6 !== s7) crossing(acc, v6, v7, 0, 1, 1, 1, 1, 1);
              if (s0 !== s2) crossing(acc, v0, v2, 0, 0, 0, 0, 1, 0);
              if (s1 !== s3) crossing(acc, v1, v3, 1, 0, 0, 1, 1, 0);
              if (s4 !== s6) crossing(acc, v4, v6, 0, 0, 1, 0, 1, 1);
              if (s5 !== s7) crossing(acc, v5, v7, 1, 0, 1, 1, 1, 1);
              if (s0 !== s4) crossing(acc, v0, v4, 0, 0, 0, 0, 0, 1);
              if (s1 !== s5) crossing(acc, v1, v5, 1, 0, 0, 1, 0, 1);
              if (s2 !== s6) crossing(acc, v2, v6, 0, 1, 0, 0, 1, 1);
              if (s3 !== s7) crossing(acc, v3, v7, 1, 1, 0, 1, 1, 1);
              const gi = i0 + i,
                gj = j0 + j,
                gk = k0 + k;
              if (np * 3 + 3 > pos.length) {
                const g = new Float32Array(pos.length * 2);
                g.set(pos);
                pos = g;
              }
              pos[np * 3] = ox + (gi + acc[0] / acc[3]) * h;
              pos[np * 3 + 1] = oy + (gj + acc[1] / acc[3]) * h;
              pos[np * 3 + 2] = oz + (gk + acc[2] / acc[3]) * h;
              vb[i + (j << 3) + (k << 6)] = np++;
              // quads around the three edges leaving this cell's first corner
              if (s0 !== s1 && gj > 0 && gk > 0) {
                const a = cell(gi, gj - 1, gk - 1),
                  b = cell(gi, gj, gk - 1),
                  c = cell(gi, gj, gk),
                  d = cell(gi, gj - 1, gk);
                if (s0) quad(a, b, c, d);
                else quad(d, c, b, a);
              }
              if (s0 !== s2 && gi > 0 && gk > 0) {
                const a = cell(gi - 1, gj, gk - 1),
                  b = cell(gi - 1, gj, gk),
                  c = cell(gi, gj, gk),
                  d = cell(gi, gj, gk - 1);
                if (s0) quad(a, b, c, d);
                else quad(d, c, b, a);
              }
              if (s0 !== s4 && gi > 0 && gj > 0) {
                const a = cell(gi - 1, gj - 1, gk),
                  b = cell(gi, gj - 1, gk),
                  c = cell(gi, gj, gk),
                  d = cell(gi - 1, gj, gk);
                if (s0) quad(a, b, c, d);
                else quad(d, c, b, a);
              }
            }
      }
  return { positions: pos.slice(0, np * 3), indices: idx.slice(0, ni) };
}

// ------------------------------------------------------------------ noise
function hash3(x: number, y: number, z: number, seed: number): number {
  let h = (x * 374761393 + y * 668265263 + z * 2147483647 + seed * 144665) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

/** Smooth value noise, about -1..1, features `scale` mm across. */
function valueNoise(x: number, y: number, z: number, scale: number, seed: number): number {
  x /= scale;
  y /= scale;
  z /= scale;
  const xi = Math.floor(x),
    yi = Math.floor(y),
    zi = Math.floor(z);
  const fx = x - xi,
    fy = y - yi,
    fz = z - zi;
  const u = fx * fx * (3 - 2 * fx),
    v = fy * fy * (3 - 2 * fy),
    w = fz * fz * (3 - 2 * fz);
  let s = 0;
  for (let c = 0; c < 8; c++) {
    const dx = c & 1,
      dy = (c >> 1) & 1,
      dz = (c >> 2) & 1;
    const wt = (dx ? u : 1 - u) * (dy ? v : 1 - v) * (dz ? w : 1 - w);
    s += wt * hash3(xi + dx, yi + dy, zi + dz, seed);
  }
  return s * 2 - 1;
}

// ------------------------------------------------------------------ floaters
/** A closed, welded blob (UV ellipsoid) for the loose bits a scan picks up. */
function blob(out: { p: number[]; f: number[] }, c: V3, r: V3, seg: number) {
  const rings = Math.max(3, seg >> 1);
  const base = out.p.length / 3;
  out.p.push(c[0], c[1] - r[1], c[2]); // top (y down)
  for (let j = 1; j < rings; j++) {
    const th = (Math.PI * j) / rings;
    for (let i = 0; i < seg; i++) {
      const ph = (2 * Math.PI * i) / seg;
      out.p.push(c[0] + r[0] * Math.sin(th) * Math.cos(ph), c[1] - r[1] * Math.cos(th), c[2] + r[2] * Math.sin(th) * Math.sin(ph));
    }
  }
  out.p.push(c[0], c[1] + r[1], c[2]);
  const bottom = base + 1 + (rings - 1) * seg;
  const at = (j: number, i: number) => base + 1 + (j - 1) * seg + (i % seg);
  for (let i = 0; i < seg; i++) out.f.push(base, at(1, i + 1), at(1, i));
  for (let j = 1; j < rings - 1; j++)
    for (let i = 0; i < seg; i++) {
      out.f.push(at(j, i), at(j, i + 1), at(j + 1, i + 1));
      out.f.push(at(j, i), at(j + 1, i + 1), at(j + 1, i));
    }
  for (let i = 0; i < seg; i++) out.f.push(bottom, at(rings - 1, i), at(rings - 1, i + 1));
}

const FLOATERS: { c: V3; r: V3 }[] = [
  { c: [-70, -55, 45], r: [1, 1, 1] },
  { c: [-10, -70, -40], r: [1.6, 1.6, 1.6] },
  { c: [40, -45, 50], r: [2.2, 2.2, 2.2] },
  { c: [120, -30, -30], r: [0.8, 0.8, 0.8] },
  { c: [10, -35, 65], r: [1.3, 1.3, 1.3] },
  { c: [85, -60, 20], r: [2.6, 2.6, 2.6] },
  { c: [-120, -12, -60], r: [3, 3, 3] },
  { c: [0, -60, 20], r: [7, 1.2, 4] }, // reflection ghosts: flat smears in the air
  { c: [105, -8, 62], r: [9, 0.8, 5] },
];

// ------------------------------------------------------------------ the scan
export type ScanMeshOptions = {
  voxel: number;
  /** Surface noise amplitude, mm (before straightening it is larger). */
  noise: number;
  seed: number;
};

const meshCache = new Map<string, TriMesh>();

/** The fused scan mesh for a voxel size (world frame). Cached: the same settings give the same mesh. */
export function* scanMesh(o: ScanMeshOptions): Work<TriMesh> {
  const key = `${o.voxel}|${o.noise}|${o.seed}`;
  const hit = meshCache.get(key);
  if (hit) {
    meshCache.delete(key);
    meshCache.set(key, hit);
    return hit;
  }
  const h = o.voxel;
  // Grid planes never fall exactly on the table (y = 0): no zero samples.
  const origin: V3 = [-178.013, -83.27, -136.021];
  const size: V3 = [360, 86, 272];
  const nets = yield* scaled(
    surfaceNets(
      sceneSdf,
      origin,
      size,
      h,
      (x, y, z) => inPatch(x, z) && !inHole(x, y, z),
      (x0, z0, x1, z1) => !(inPatch(x0, z0) || inPatch(x1, z0) || inPatch(x0, z1) || inPatch(x1, z1) || inPatch((x0 + x1) / 2, (z0 + z1) / 2)),
    ),
    0,
    0.8,
  );
  // drop the vertices of quads that were left out
  const base = compact({ positions: nets.positions, indices: nets.indices, normals: new Float32Array(nets.positions.length) }, null);
  yield 0.84;

  // onto the surface (one Newton step), then scan noise along the normal
  const P = base.positions;
  const rand = rng(o.seed * 7919 + 13);
  const eps = h * 0.05;
  for (let i = 0; i < P.length; i += 3) {
    if (i % 60000 === 0) yield 0.84 + (0.08 * i) / P.length;
    let x = P[i],
      y = P[i + 1],
      z = P[i + 2];
    const d = sceneSdf(x, y, z);
    let gx = (sceneSdf(x + eps, y, z) - sceneSdf(x - eps, y, z)) / (2 * eps);
    let gy = (sceneSdf(x, y + eps, z) - sceneSdf(x, y - eps, z)) / (2 * eps);
    let gz = (sceneSdf(x, y, z + eps) - sceneSdf(x, y, z - eps)) / (2 * eps);
    const gl = Math.hypot(gx, gy, gz) || 1;
    gx /= gl;
    gy /= gl;
    gz /= gl;
    if (Math.abs(d) > 1e-4) {
      x -= d * gx;
      y -= d * gy;
      z -= d * gz;
    }
    const n = o.noise * (0.75 * valueNoise(x, y, z, 3.2, o.seed) + 0.35 * valueNoise(x, y, z, 1.1, o.seed + 1)) + (rand() - 0.5) * o.noise * 0.5;
    P[i] = x + gx * n;
    P[i + 1] = y + gy * n;
    P[i + 2] = z + gz * n;
  }
  yield 0.92;

  // loose bits from reflections
  const extra = { p: [] as number[], f: [] as number[] };
  for (const fl of FLOATERS) {
    const r = Math.max(fl.r[0], fl.r[1], fl.r[2]);
    blob(extra, fl.c, fl.r, Math.min(48, Math.max(8, Math.round((2 * Math.PI * r) / h))));
  }
  const V0 = P.length / 3;
  const positions = new Float32Array(P.length + extra.p.length);
  positions.set(P);
  positions.set(extra.p, P.length);
  const indices = new Uint32Array(base.indices.length + extra.f.length);
  indices.set(base.indices);
  for (let i = 0; i < extra.f.length; i++) indices[base.indices.length + i] = extra.f[i] + V0;

  // into the world frame (the first camera frame)
  for (let i = 0; i < positions.length; i += 3) {
    const w = apply(T_WS, [positions[i], positions[i + 1], positions[i + 2]]);
    positions[i] = w[0];
    positions[i + 1] = w[1];
    positions[i + 2] = w[2];
  }
  yield 0.97;
  const mesh = withNormals(positions, indices);
  meshCache.set(key, mesh);
  while (meshCache.size > 3) meshCache.delete(meshCache.keys().next().value as string);
  return mesh;
}

/** Re-map a work generator's progress into [a, b]. */
export function* scaled<T>(g: Work<T>, a: number, b: number): Work<T> {
  for (;;) {
    const r = g.next();
    if (r.done) return r.value;
    yield a + (b - a) * r.value;
  }
}

// ------------------------------------------------------------------ trajectory
export type MockTrajectory = {
  raw: number[][];
  straightened: number[][];
  /** Index pairs into `raw` where the scan came back to a place it had seen. */
  loops: [number, number][];
  /** How far straightening moved the scan (the end), mm and degrees. */
  correction_mm: number;
  correction_deg: number;
};

const r1 = (x: number) => Math.round(x * 10) / 10;

/**
 * Camera positions along the demo sweep for a scan of `duration` s, as tracked (with a slow
 * drift that grows to ~2 mm over 30 s) and straightened; loops are revisits more than 6 s apart.
 */
export function mockTrajectory(duration: number, seed: number): MockTrajectory {
  const n = Math.max(12, Math.min(320, Math.round(duration * 7)));
  const rand = rng(seed * 31 + 5);
  const drift: V3 = [1.3 + rand() * 0.4, -0.8 - rand() * 0.3, 1.0 + rand() * 0.3];
  const T = Math.max(1, duration);
  const truth: V3[] = [];
  const raw: number[][] = [];
  const straight: number[][] = [];
  const dirs: V3[] = [];
  for (let i = 0; i < n; i++) {
    const t = (duration * i) / Math.max(1, n - 1);
    const pose = trajectory(t);
    const w = apply(T_WS, pose.t);
    truth.push(w);
    // drift grows with time (and a little faster towards the end)
    const k = (t / 28.8) * (0.4 + 0.6 * (t / T));
    const wob = 0.25 * Math.sin(t * 0.9) * (t / T);
    raw.push([r1(w[0] + drift[0] * k + wob), r1(w[1] + drift[1] * k), r1(w[2] + drift[2] * k - wob)]);
    straight.push([r1(w[0] + (rand() - 0.5) * 0.15), r1(w[1] + (rand() - 0.5) * 0.15), r1(w[2] + (rand() - 0.5) * 0.15)]);
    dirs.push([pose.R[2], pose.R[5], pose.R[8]]);
  }
  // revisits: close in space, far apart in time, looking the same way
  const cands: { i: number; j: number; d: number }[] = [];
  for (let i = 0; i < n; i++)
    for (let j = i + 1; j < n; j++) {
      const dt = (duration * (j - i)) / Math.max(1, n - 1);
      if (dt < 6) continue;
      const a = truth[i],
        b = truth[j];
      const d = Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
      const facing = dirs[i][0] * dirs[j][0] + dirs[i][1] * dirs[j][1] + dirs[i][2] * dirs[j][2];
      if (d < 130 && facing > 0.95) cands.push({ i, j, d }); // the views overlap: a revisit ICP can confirm
    }
  cands.sort((x, y) => x.d - y.d);
  const loops: [number, number][] = [];
  const gap = Math.max(3, Math.round(n / 10));
  for (const c of cands) {
    if (loops.length >= 4) break;
    if (loops.some(([a, b]) => Math.abs(a - c.i) < gap || Math.abs(b - c.j) < gap)) continue;
    loops.push([c.i, c.j]);
  }
  const end = raw[n - 1],
    tEnd = truth[n - 1];
  return {
    raw,
    straightened: straight,
    loops,
    correction_mm: Math.round(Math.hypot(end[0] - tEnd[0], end[1] - tEnd[1], end[2] - tEnd[2]) * 100) / 100,
    correction_deg: Math.round(0.012 * T * 100) / 100,
  };
}
