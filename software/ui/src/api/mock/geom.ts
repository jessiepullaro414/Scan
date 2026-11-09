/**
 * Mock backend geometry, in plain arrays (no three.js, so the mock stays small).
 *
 * Scene frame S uses OpenCV-style axes like the engine: y points DOWN. The table is the plane
 * y = 0 and the parts sit on it (negative y). Poses are camera-to-scene: X_s = R X_c + t, with
 * the camera looking along its +z, x right, y down.
 */
export type V3 = [number, number, number];
export type Pose = { R: number[]; t: V3 }; // R row-major 3x3, columns = camera axes in the parent frame

export const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
export const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const scale = (a: V3, s: number): V3 => [a[0] * s, a[1] * s, a[2] * s];
export const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
export const len = (a: V3) => Math.hypot(a[0], a[1], a[2]);
export const normalize = (a: V3): V3 => {
  const l = len(a) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
};

/** OpenCV camera at `eye` looking at `target`; `up` is the scene's up direction. */
export function lookAt(eye: V3, target: V3, up: V3 = [0, -1, 0]): Pose {
  const z = normalize(sub(target, eye));
  let x = cross(z, up);
  if (len(x) < 1e-6) x = [1, 0, 0];
  x = normalize(x);
  const y = cross(z, x);
  return { R: [x[0], y[0], z[0], x[1], y[1], z[1], x[2], y[2], z[2]], t: [...eye] as V3 };
}

export function rotate(R: number[], v: V3): V3 {
  return [R[0] * v[0] + R[1] * v[1] + R[2] * v[2], R[3] * v[0] + R[4] * v[1] + R[5] * v[2], R[6] * v[0] + R[7] * v[1] + R[8] * v[2]];
}

export function rotateT(R: number[], v: V3): V3 {
  return [R[0] * v[0] + R[3] * v[1] + R[6] * v[2], R[1] * v[0] + R[4] * v[1] + R[7] * v[2], R[2] * v[0] + R[5] * v[1] + R[8] * v[2]];
}

export const apply = (p: Pose, x: V3): V3 => add(rotate(p.R, x), p.t);
/** Parent-frame point into the pose's local frame. */
export const applyInv = (p: Pose, x: V3): V3 => rotateT(p.R, sub(x, p.t));

function mul3(A: number[], B: number[]): number[] {
  const C = new Array<number>(9);
  for (let i = 0; i < 3; i++)
    for (let j = 0; j < 3; j++) C[i * 3 + j] = A[i * 3] * B[j] + A[i * 3 + 1] * B[3 + j] + A[i * 3 + 2] * B[6 + j];
  return C;
}

export const compose = (a: Pose, b: Pose): Pose => ({ R: mul3(a.R, b.R), t: apply(a, b.t) });

export function invert(p: Pose): Pose {
  const Rt = [p.R[0], p.R[3], p.R[6], p.R[1], p.R[4], p.R[7], p.R[2], p.R[5], p.R[8]];
  return { R: Rt, t: scale(rotate(Rt, p.t), -1) };
}

/** Rotation about a unit axis (Rodrigues), row-major. */
export function axisAngle(axis: V3, a: number): number[] {
  const [x, y, z] = normalize(axis);
  const c = Math.cos(a),
    s = Math.sin(a),
    C = 1 - c;
  return [c + x * x * C, x * y * C - z * s, x * z * C + y * s, y * x * C + z * s, c + y * y * C, y * z * C - x * s, z * x * C - y * s, z * y * C + x * s, c + z * z * C];
}

export function toRowMajor16(p: Pose): number[] {
  const R = p.R;
  return [R[0], R[1], R[2], p.t[0], R[3], R[4], R[5], p.t[1], R[6], R[7], R[8], p.t[2], 0, 0, 0, 1];
}

/** Small deterministic PRNG. */
export function rng(seed: number) {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13;
    s ^= s >>> 17;
    s ^= s << 5;
    return (s >>> 0) / 4294967296;
  };
}

// ------------------------------------------------------------------ scene: a sphere and a gauge block on a table
export const SCENE = {
  sphere: { c: [-38, -40, 0] as V3, r: 40 },
  box: { min: [32, -25, -22] as V3, max: [92, 0, 22] as V3 }, // 60 x 25 x 44 mm
  table: { x0: -150, x1: 150, z0: -112, z1: 112 },
  target: [2, -20, 0] as V3,
};

/** Irregular outline of the scanned table patch (a real scan has ragged edges). */
export function inTable(x: number, z: number): boolean {
  const u = (x - 2) / 146;
  const v = z / 106;
  const wobble = 0.06 * Math.sin(x * 0.045 + 1.3) + 0.05 * Math.sin(z * 0.06 - 0.4) + 0.03 * Math.sin((x + z) * 0.11);
  return u * u * u * u + v * v * v * v < 0.82 + wobble;
}

export type Hit = { t: number; n: V3; id: 0 | 1 | 2 | 3 };

/** Ray (scene frame) against the table (1), the sphere (2) and the block (3). */
export function castRay(o: V3, d: V3, maxT = 5000): Hit {
  let best: Hit = { t: maxT, n: [0, 0, 0], id: 0 };
  // table y = 0, seen from above (y < 0 is up)
  if (d[1] > 1e-9 && o[1] < 0) {
    const t = -o[1] / d[1];
    if (t > 0 && t < best.t) {
      const x = o[0] + d[0] * t,
        z = o[2] + d[2] * t;
      if (inTable(x, z)) best = { t, n: [0, -1, 0], id: 1 };
    }
  }
  // sphere
  const { c, r } = SCENE.sphere;
  const oc = sub(o, c);
  const b = dot(oc, d);
  const cc = dot(oc, oc) - r * r;
  const disc = b * b - cc;
  if (disc > 0) {
    const t = -b - Math.sqrt(disc);
    if (t > 0 && t < best.t) {
      const p = add(o, scale(d, t));
      best = { t, n: normalize(sub(p, c)), id: 2 };
    }
  }
  // block (slab method)
  const { min, max } = SCENE.box;
  let t0 = -Infinity,
    t1 = Infinity,
    axis = -1,
    sign = 1;
  for (let k = 0; k < 3; k++) {
    if (Math.abs(d[k]) < 1e-12) {
      if (o[k] < min[k] || o[k] > max[k]) {
        t0 = Infinity;
        break;
      }
      continue;
    }
    let a = (min[k] - o[k]) / d[k],
      bb = (max[k] - o[k]) / d[k];
    let s = -1;
    if (a > bb) {
      [a, bb] = [bb, a];
      s = 1;
    }
    if (a > t0) {
      t0 = a;
      axis = k;
      sign = s;
    }
    if (bb < t1) t1 = bb;
  }
  if (t0 <= t1 && t0 > 0 && t0 < best.t && axis >= 0) {
    const n: V3 = [0, 0, 0];
    n[axis] = sign;
    best = { t: t0, n, id: 3 };
  }
  return best;
}

/** Is the segment from `p` to the camera at `cam` blocked by the sphere or the block? */
export function occluded(p: V3, cam: V3, selfId: number): boolean {
  const v = sub(cam, p);
  const L = len(v);
  const d = scale(v, 1 / L);
  const o = add(p, scale(d, 0.8));
  const h = castRay(o, d, L - 1);
  return h.id !== 0 && !(h.id === selfId && selfId === 1);
}

// ------------------------------------------------------------------ meshes
export type MeshData = {
  positions: Float32Array;
  normals: Float32Array;
  indices: Uint32Array;
  /** Which part each vertex belongs to (1 table, 2 sphere, 3 block). */
  part: Uint8Array;
};

class Builder {
  p: number[] = [];
  n: number[] = [];
  part: number[] = [];
  f: number[] = [];
  v(x: V3, n: V3, part: number): number {
    this.p.push(x[0], x[1], x[2]);
    this.n.push(n[0], n[1], n[2]);
    this.part.push(part);
    return this.part.length - 1;
  }
  tri(a: number, b: number, c: number) {
    this.f.push(a, b, c);
  }
  build(): MeshData {
    return {
      positions: new Float32Array(this.p),
      normals: new Float32Array(this.n),
      indices: new Uint32Array(this.f),
      part: new Uint8Array(this.part),
    };
  }
}

/**
 * The scene as a mesh (the visible surfaces only, like a real scan): a ragged table patch
 * with holes under the parts, a sphere without its bottom cap, and the block without its base.
 * `res` scales the resolution; `noiseMm` adds a little surface noise along the normals.
 */
export function sceneMesh(res: number, noiseMm = 0, seed = 1): MeshData {
  const B = new Builder();
  const rand = rng(seed);
  const jitter = (x: V3, n: V3): V3 => (noiseMm ? add(x, scale(n, (rand() - 0.5) * 2 * noiseMm)) : x);
  const { table, sphere, box } = SCENE;

  // table grid; triangles are flipped so they face up (-y) from the cameras' side
  const nx = Math.round(80 * res),
    nz = Math.round(60 * res);
  const idx = new Int32Array((nx + 1) * (nz + 1)).fill(-1);
  for (let j = 0; j <= nz; j++) {
    for (let i = 0; i <= nx; i++) {
      const x = table.x0 + ((table.x1 - table.x0) * i) / nx;
      const z = table.z0 + ((table.z1 - table.z0) * j) / nz;
      if (!inTable(x, z)) continue;
      const inBox = x > box.min[0] - 0.5 && x < box.max[0] + 0.5 && z > box.min[2] - 0.5 && z < box.max[2] + 0.5;
      const dS = Math.hypot(x - sphere.c[0], z - sphere.c[2]);
      if (inBox || dS < 9) continue;
      idx[j * (nx + 1) + i] = B.v(jitter([x, 0, z], [0, -1, 0]), [0, -1, 0], 1);
    }
  }
  for (let j = 0; j < nz; j++) {
    for (let i = 0; i < nx; i++) {
      const a = idx[j * (nx + 1) + i],
        b = idx[j * (nx + 1) + i + 1],
        c = idx[(j + 1) * (nx + 1) + i],
        d = idx[(j + 1) * (nx + 1) + i + 1];
      if (a < 0 || b < 0 || c < 0 || d < 0) continue;
      // counter-clockwise seen from above (-y): front faces look at the cameras
      B.tri(a, b, c);
      B.tri(b, d, c);
    }
  }

  // sphere: latitude from the top down to just above the contact with the table
  const nLon = Math.round(64 * res),
    nLat = Math.round(40 * res);
  const capStop = Math.acos(-0.93); // stop before the bottom cap (hidden by the table)
  const rows: number[][] = [];
  for (let j = 0; j <= nLat; j++) {
    const th = (capStop * j) / nLat; // 0 = top (-y)
    const row: number[] = [];
    for (let i = 0; i <= nLon; i++) {
      const ph = (2 * Math.PI * i) / nLon;
      const n: V3 = [Math.sin(th) * Math.cos(ph), -Math.cos(th), Math.sin(th) * Math.sin(ph)];
      row.push(B.v(jitter(add(sphere.c, scale(n, sphere.r)), n), n, 2));
    }
    rows.push(row);
  }
  for (let j = 0; j < nLat; j++) {
    for (let i = 0; i < nLon; i++) {
      const a = rows[j][i],
        b = rows[j][i + 1],
        c = rows[j + 1][i],
        d = rows[j + 1][i + 1];
      // outward-facing winding
      B.tri(a, c, b);
      B.tri(b, c, d);
    }
  }

  // block: top and four sides
  const step = 3 / res;
  const face = (o: V3, u: V3, v: V3, nu: number, nv: number, n: V3) => {
    const ids: number[][] = [];
    for (let j = 0; j <= nv; j++) {
      const row: number[] = [];
      for (let i = 0; i <= nu; i++) row.push(B.v(jitter(add(o, add(scale(u, i / nu), scale(v, j / nv))), n), n, 3));
      ids.push(row);
    }
    for (let j = 0; j < nv; j++)
      for (let i = 0; i < nu; i++) {
        const a = ids[j][i],
          b = ids[j][i + 1],
          c = ids[j + 1][i],
          d = ids[j + 1][i + 1];
        // orient each triangle along the face normal
        const pa: V3 = [B.p[a * 3], B.p[a * 3 + 1], B.p[a * 3 + 2]];
        const pb: V3 = [B.p[b * 3], B.p[b * 3 + 1], B.p[b * 3 + 2]];
        const pc: V3 = [B.p[c * 3], B.p[c * 3 + 1], B.p[c * 3 + 2]];
        if (dot(cross(sub(pb, pa), sub(pc, pa)), n) > 0) {
          B.tri(a, b, c);
          B.tri(b, d, c);
        } else {
          B.tri(a, c, b);
          B.tri(b, c, d);
        }
      }
  };
  const [x0, y0, z0] = box.min;
  const [x1, y1, z1] = box.max;
  const W = x1 - x0,
    H = y1 - y0,
    D = z1 - z0;
  const nW = Math.max(2, Math.round(W / step)),
    nH = Math.max(2, Math.round(H / step)),
    nD = Math.max(2, Math.round(D / step));
  face([x0, y0, z0], [W, 0, 0], [0, 0, D], nW, nD, [0, -1, 0]); // top (y0 is the top: y down)
  face([x0, y0, z0], [W, 0, 0], [0, H, 0], nW, nH, [0, 0, -1]); // front (z0)
  face([x0, y0, z1], [W, 0, 0], [0, H, 0], nW, nH, [0, 0, 1]); // back (z1)
  face([x0, y0, z0], [0, 0, D], [0, H, 0], nD, nH, [-1, 0, 0]); // left (x0)
  face([x1, y0, z0], [0, 0, D], [0, H, 0], nD, nH, [1, 0, 0]); // right (x1)

  return B.build();
}

// ------------------------------------------------------------------ hand-held trajectory
const P0 = SCENE.target;

/** Camera pose (scene frame) along a smooth sweep around the parts; `tau` in seconds. */
export function trajectory(tau: number): Pose {
  const th = 0.95 * Math.sin(0.105 * tau) + 0.22 * Math.sin(0.41 * tau + 1.3);
  const ph = 0.55 + 0.33 * Math.sin(0.16 * tau - 1.35) + 0.04 * Math.sin(0.93 * tau);
  const d = 285 + 125 * Math.sin(0.21 * tau + 0.25) + 22 * Math.sin(0.67 * tau + 2);
  const eye: V3 = add(P0, [d * Math.sin(th) * Math.cos(ph), -d * Math.sin(ph), -d * Math.cos(th) * Math.cos(ph)]);
  const shake: V3 = [0.9 * Math.sin(7.1 * tau), 0.7 * Math.sin(8.3 * tau + 1), 0.8 * Math.sin(6.7 * tau + 2)];
  const aim: V3 = add(P0, [10 * Math.sin(0.37 * tau), 6 * Math.sin(0.29 * tau + 1), 10 * Math.sin(0.31 * tau + 2)]);
  return lookAt(add(eye, shake), aim);
}

// ------------------------------------------------------------------ nominal optics (ARCHITECTURE.md §7)
export const K_FULL = { fx: 720, fy: 720, cx: 640, cy: 400, w: 1280, h: 800 };
export const LASERS = {
  vertical: { n: [0.9473, 0, 0.3205] as V3, d: -109.8, origin: [116.4, 0, -1.4] as V3, contains: [0, 1, 0] as V3 },
  horizontal: { n: [0, -0.9594, 0.2822] as V3, d: -71.67, origin: [30, -75.1, -1.2] as V3, contains: [1, 0, 0] as V3 },
};

/** Laser points along both stripes for a camera pose (scene frame), with ~0.03 mm noise. */
export function laserPoints(pose: Pose, samples: number, rand: () => number, out: number[]): void {
  for (const L of [LASERS.vertical, LASERS.horizontal]) {
    const fwd = normalize(cross(L.contains, L.n)); // in-plane direction away from the camera
    const f = fwd[2] < 0 ? scale(fwd, -1) : fwd;
    const o = apply(pose, L.origin);
    for (let k = 0; k < samples; k++) {
      const a = ((k / (samples - 1)) * 2 - 1) * (Math.PI / 6);
      const dc = add(scale(f, Math.cos(a)), scale(L.contains, Math.sin(a)));
      const d = normalize(rotate(pose.R, dc));
      const h = castRay(o, d, 700);
      if (!h.id) continue;
      const p = add(o, scale(d, h.t));
      const nz = (rand() - 0.5) * 0.06;
      out.push(p[0] + h.n[0] * nz, p[1] + h.n[1] * nz, p[2] + h.n[2] * nz);
    }
  }
}

// ------------------------------------------------------------------ file writers
export function plyMesh(m: { positions: Float32Array; normals: Float32Array; indices: Uint32Array }): ArrayBuffer {
  const V = m.positions.length / 3;
  const F = m.indices.length / 3;
  const header =
    "ply\nformat binary_little_endian 1.0\ncomment OpenHandScan mock (mm)\n" +
    `element vertex ${V}\nproperty float x\nproperty float y\nproperty float z\nproperty float nx\nproperty float ny\nproperty float nz\n` +
    `element face ${F}\nproperty list uchar int vertex_indices\nend_header\n`;
  const hb = new TextEncoder().encode(header);
  const buf = new ArrayBuffer(hb.length + V * 24 + F * 13);
  new Uint8Array(buf).set(hb);
  const dv = new DataView(buf);
  let o = hb.length;
  for (let i = 0; i < V; i++) {
    for (let k = 0; k < 3; k++, o += 4) dv.setFloat32(o, m.positions[i * 3 + k], true);
    for (let k = 0; k < 3; k++, o += 4) dv.setFloat32(o, m.normals[i * 3 + k], true);
  }
  for (let f = 0; f < F; f++) {
    dv.setUint8(o, 3);
    o += 1;
    for (let k = 0; k < 3; k++, o += 4) dv.setInt32(o, m.indices[f * 3 + k], true);
  }
  return buf;
}

export function plyPoints(points: Float32Array): ArrayBuffer {
  const V = points.length / 3;
  const header = `ply\nformat binary_little_endian 1.0\ncomment OpenHandScan mock laser points (mm)\nelement vertex ${V}\nproperty float x\nproperty float y\nproperty float z\nend_header\n`;
  const hb = new TextEncoder().encode(header);
  const buf = new ArrayBuffer(hb.length + V * 12);
  new Uint8Array(buf).set(hb);
  const dv = new DataView(buf);
  let o = hb.length;
  for (let i = 0; i < V * 3; i++, o += 4) dv.setFloat32(o, points[i], true);
  return buf;
}

export function stlMesh(m: { positions: Float32Array; indices: Uint32Array }): ArrayBuffer {
  const F = m.indices.length / 3;
  const buf = new ArrayBuffer(84 + F * 50);
  const dv = new DataView(buf);
  const head = new TextEncoder().encode("OpenHandScan mock mesh, units mm");
  new Uint8Array(buf, 0, 80).set(head.slice(0, 80));
  dv.setUint32(80, F, true);
  let o = 84;
  const P = m.positions;
  for (let f = 0; f < F; f++) {
    const a = m.indices[f * 3] * 3,
      b = m.indices[f * 3 + 1] * 3,
      c = m.indices[f * 3 + 2] * 3;
    const n = normalize(cross([P[b] - P[a], P[b + 1] - P[a + 1], P[b + 2] - P[a + 2]], [P[c] - P[a], P[c + 1] - P[a + 1], P[c + 2] - P[a + 2]]));
    for (const v of [n[0], n[1], n[2], P[a], P[a + 1], P[a + 2], P[b], P[b + 1], P[b + 2], P[c], P[c + 1], P[c + 2]]) {
      dv.setFloat32(o, v, true);
      o += 4;
    }
    dv.setUint16(o, 0, true);
    o += 2;
  }
  return buf;
}

/** Transform a mesh's positions and normals by a pose (scene → world frame). */
export function transformMesh<T extends { positions: Float32Array; normals: Float32Array }>(m: T, p: Pose): T {
  const P = new Float32Array(m.positions.length);
  const N = new Float32Array(m.normals.length);
  for (let i = 0; i < P.length; i += 3) {
    const x = apply(p, [m.positions[i], m.positions[i + 1], m.positions[i + 2]]);
    const n = rotate(p.R, [m.normals[i], m.normals[i + 1], m.normals[i + 2]]);
    P[i] = x[0];
    P[i + 1] = x[1];
    P[i + 2] = x[2];
    N[i] = n[0];
    N[i + 1] = n[1];
    N[i + 2] = n[2];
  }
  return { ...m, positions: P, normals: N };
}
