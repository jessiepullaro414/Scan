/**
 * Mesh operations of the demo engine's processing studio, on typed arrays: statistics and hole
 * outlines (MeshStats / HoleOutline as in APP_API.md "M3 phase 2"), and the edits: remove
 * floaters, fill holes, smooth, simplify, delete / keep faces. They are plausible stand-ins for
 * the engine's `ohs.mesh` (vertex clustering instead of quadric decimation, a domed fan instead
 * of a fair fill), fast enough for a million triangles.
 *
 * Long operations are generators that yield their progress (0..1) between steps, so the mock's
 * job runner can spread them over several ticks and keep the page responsive.
 */
import type { HoleOutline, MeshStats } from "../types";
import { vertexNormals } from "../../three/normals";

export type TriMesh = { positions: Float32Array; normals: Float32Array; indices: Uint32Array };
export type Work<T> = Generator<number, T, void>;

/** Run a generator to its end (for replays and seeds). */
export function runSync<T>(g: Generator<unknown, T, void>): T {
  for (;;) {
    const r = g.next();
    if (r.done) return r.value;
  }
}

export function withNormals(positions: Float32Array, indices: Uint32Array): TriMesh {
  return { positions, indices, normals: vertexNormals(positions, indices) };
}

export function meshBytes(m: TriMesh): number {
  return m.positions.byteLength + m.normals.byteLength + m.indices.byteLength;
}

/** Keep the faces where `keep` is 1 (all when null) and drop the vertices no face uses. */
export function compact(m: TriMesh, keep: Uint8Array | null): TriMesh {
  const I = m.indices;
  const F = I.length / 3;
  const V = m.positions.length / 3;
  let nf = 0;
  for (let f = 0; f < F; f++) if (!keep || keep[f]) nf++;
  const remap = new Int32Array(V).fill(-1);
  const out = new Uint32Array(nf * 3);
  let nv = 0,
    o = 0;
  for (let f = 0; f < F; f++) {
    if (keep && !keep[f]) continue;
    for (let k = 0; k < 3; k++) {
      const v = I[f * 3 + k];
      if (remap[v] < 0) remap[v] = nv++;
      out[o++] = remap[v];
    }
  }
  const P = new Float32Array(nv * 3);
  const N = new Float32Array(nv * 3);
  for (let v = 0; v < V; v++) {
    const r = remap[v];
    if (r < 0) continue;
    P[r * 3] = m.positions[v * 3];
    P[r * 3 + 1] = m.positions[v * 3 + 1];
    P[r * 3 + 2] = m.positions[v * 3 + 2];
    N[r * 3] = m.normals[v * 3];
    N[r * 3 + 1] = m.normals[v * 3 + 1];
    N[r * 3 + 2] = m.normals[v * 3 + 2];
  }
  return { positions: P, normals: N, indices: out };
}

// ------------------------------------------------------------------ connectivity
/** Connected pieces (faces sharing vertices): the piece of each face, and how many there are. */
export function components(m: TriMesh): { faceComp: Int32Array; count: number } {
  const I = m.indices;
  const V = m.positions.length / 3;
  const parent = new Int32Array(V);
  for (let v = 0; v < V; v++) parent[v] = v;
  const find = (x: number) => {
    while (parent[x] !== x) {
      parent[x] = parent[parent[x]];
      x = parent[x];
    }
    return x;
  };
  const unite = (a: number, b: number) => {
    const ra = find(a),
      rb = find(b);
    if (ra !== rb) parent[ra < rb ? rb : ra] = ra < rb ? ra : rb;
  };
  for (let i = 0; i < I.length; i += 3) {
    unite(I[i], I[i + 1]);
    unite(I[i], I[i + 2]);
  }
  const label = new Int32Array(V).fill(-1);
  let count = 0;
  const F = I.length / 3;
  const faceComp = new Int32Array(F);
  for (let f = 0; f < F; f++) {
    const r = find(I[f * 3]);
    if (label[r] < 0) label[r] = count++;
    faceComp[f] = label[r];
  }
  return { faceComp, count };
}

/** Neighbouring vertices (CSR over the faces' edges; an edge shared by two faces counts twice). */
function vertexNeighbours(m: TriMesh): { start: Uint32Array; list: Uint32Array } {
  const I = m.indices;
  const V = m.positions.length / 3;
  const start = new Uint32Array(V + 1);
  for (let i = 0; i < I.length; i++) start[I[i] + 1] += 2;
  for (let v = 0; v < V; v++) start[v + 1] += start[v];
  const fill = start.slice(0, V);
  const list = new Uint32Array(start[V]);
  for (let i = 0; i < I.length; i += 3) {
    const a = I[i],
      b = I[i + 1],
      c = I[i + 2];
    list[fill[a]++] = b;
    list[fill[a]++] = c;
    list[fill[b]++] = a;
    list[fill[b]++] = c;
    list[fill[c]++] = a;
    list[fill[c]++] = b;
  }
  return { start, list };
}

type Edges = { from: Uint32Array; to: Uint32Array; boundary: number; nonManifold: number; boundaryVertex: Uint8Array };

/** Boundary half-edges (as the face runs them) and the count of edges used by 3+ faces. */
export function edgeInfo(m: TriMesh): Edges {
  const I = m.indices;
  const V = m.positions.length / 3;
  const start = new Uint32Array(V + 1);
  for (let i = 0; i < I.length; i += 3)
    for (let k = 0; k < 3; k++) {
      const a = I[i + k],
        b = I[i + ((k + 1) % 3)];
      start[(a < b ? a : b) + 1]++;
    }
  for (let v = 0; v < V; v++) start[v + 1] += start[v];
  const fill = start.slice(0, V);
  const other = new Uint32Array(I.length);
  const fwd = new Uint8Array(I.length); // 1: the face runs lo → hi
  for (let i = 0; i < I.length; i += 3)
    for (let k = 0; k < 3; k++) {
      const a = I[i + k],
        b = I[i + ((k + 1) % 3)];
      const lo = a < b ? a : b;
      const at = fill[lo]++;
      other[at] = a < b ? b : a;
      fwd[at] = a < b ? 1 : 0;
    }
  const from: number[] = [];
  const to: number[] = [];
  let nonManifold = 0;
  const boundaryVertex = new Uint8Array(V);
  for (let lo = 0; lo < V; lo++) {
    const s = start[lo],
      e = start[lo + 1];
    // insertion sort of the (short) bucket by the other vertex
    for (let i = s + 1; i < e; i++) {
      const ov = other[i],
        fv = fwd[i];
      let j = i - 1;
      while (j >= s && other[j] > ov) {
        other[j + 1] = other[j];
        fwd[j + 1] = fwd[j];
        j--;
      }
      other[j + 1] = ov;
      fwd[j + 1] = fv;
    }
    for (let i = s; i < e; ) {
      let j = i + 1;
      while (j < e && other[j] === other[i]) j++;
      const n = j - i;
      if (n === 1) {
        const hi = other[i];
        if (fwd[i]) {
          from.push(lo);
          to.push(hi);
        } else {
          from.push(hi);
          to.push(lo);
        }
        boundaryVertex[lo] = 1;
        boundaryVertex[hi] = 1;
      } else if (n > 2) nonManifold++;
      i = j;
    }
  }
  return { from: Uint32Array.from(from), to: Uint32Array.from(to), boundary: from.length, nonManifold, boundaryVertex };
}

/** A boundary loop; `closed` is false for a best-effort group of edges around an orientation seam. */
type Loop = { verts: Uint32Array; perimeter: number; closed: boolean };

/** Faces around each vertex (CSR). */
function vertexFaces(m: TriMesh): { start: Uint32Array; list: Uint32Array } {
  const I = m.indices;
  const V = m.positions.length / 3;
  const start = new Uint32Array(V + 1);
  for (let i = 0; i < I.length; i++) start[I[i] + 1]++;
  for (let v = 0; v < V; v++) start[v + 1] += start[v];
  const fill = start.slice(0, V);
  const list = new Uint32Array(I.length);
  for (let i = 0; i < I.length; i++) list[fill[I[i]]++] = (i / 3) | 0;
  return { start, list };
}

/**
 * Closed boundary loops, walked along the faces' orientation. At each vertex the walk turns
 * around the fan of faces it came in through, so loops that touch at one vertex (bow ties)
 * stay apart.
 */
function boundaryLoops(m: TriMesh, edges: Edges): Loop[] {
  const P = m.positions;
  const I = m.indices;
  const V = P.length / 3;
  const n = edges.boundary;
  const { from, to } = edges;
  const bStart = new Uint32Array(V + 1);
  for (let i = 0; i < n; i++) bStart[from[i] + 1]++;
  for (let v = 0; v < V; v++) bStart[v + 1] += bStart[v];
  const fill = bStart.slice(0, V);
  const out = new Uint32Array(n);
  for (let i = 0; i < n; i++) out[fill[from[i]]++] = i;
  const vf = vertexFaces(m);
  const used = new Uint8Array(n);
  const dist = (a: number, b: number) => Math.hypot(P[a * 3] - P[b * 3], P[a * 3 + 1] - P[b * 3 + 1], P[a * 3 + 2] - P[b * 3 + 2]);
  /** An unused boundary half-edge a → b, or -1. */
  const boundary = (a: number, b: number) => {
    for (let k = bStart[a]; k < bStart[a + 1]; k++) if (!used[out[k]] && to[out[k]] === b) return out[k];
    return -1;
  };
  /** The face that runs a → b, or -1. */
  const faceWith = (a: number, b: number) => {
    for (let k = vf.start[b]; k < vf.start[b + 1]; k++) {
      const f = vf.list[k] * 3;
      const x = I[f],
        y = I[f + 1],
        z = I[f + 2];
      if ((x === a && y === b) || (y === a && z === b) || (z === a && x === b)) return f / 3;
    }
    return -1;
  };
  /** Where the boundary goes on from v, having come from prev. */
  const next = (prev: number, v: number) => {
    let f = faceWith(prev, v);
    for (let guard = 0; f >= 0 && guard < 64; guard++) {
      const i = f * 3;
      const k = I[i] === v ? 0 : I[i + 1] === v ? 1 : 2;
      const w = I[i + ((k + 1) % 3)];
      const e = boundary(v, w);
      if (e >= 0) return e;
      f = faceWith(w, v); // the neighbour across v-w
    }
    for (let k = bStart[v]; k < bStart[v + 1]; k++) if (!used[out[k]]) return out[k];
    return -1;
  };
  const loops: Loop[] = [];
  const stray: number[] = []; // edges of walks that couldn't close (orientation seams)
  for (let e0 = 0; e0 < n; e0++) {
    if (used[e0]) continue;
    used[e0] = 1;
    const first = from[e0];
    const verts = [first];
    const walked = [e0];
    let per = dist(first, to[e0]);
    let prev = first;
    let v = to[e0];
    let closed = false;
    for (let guard = 0; guard <= n; guard++) {
      if (v === first) {
        closed = true;
        break;
      }
      verts.push(v);
      const e = next(prev, v);
      if (e < 0) break;
      used[e] = 1;
      walked.push(e);
      per += dist(v, to[e]);
      prev = v;
      v = to[e];
    }
    if (closed && verts.length >= 3) loops.push({ verts: Uint32Array.from(verts), perimeter: per, closed: true });
    else for (const e of walked) stray.push(e);
  }
  if (stray.length) loops.push(...strayLoops(stray, from, to, dist));
  return loops;
}

/**
 * Boundary edges no directed walk could close, grouped by shared vertices: each group counts as
 * one loop (its length is the sum of its edges), its points in a best-effort walk order.
 */
function strayLoops(edges: number[], from: Uint32Array, to: Uint32Array, dist: (a: number, b: number) => number): Loop[] {
  const parent = new Map<number, number>();
  const find = (x: number): number => {
    let r = x;
    while (parent.get(r) !== undefined && parent.get(r) !== r) r = parent.get(r)!;
    let c = x;
    while (c !== r) {
      const p = parent.get(c)!;
      parent.set(c, r);
      c = p;
    }
    return r;
  };
  for (const e of edges) {
    const a = from[e],
      b = to[e];
    if (!parent.has(a)) parent.set(a, a);
    if (!parent.has(b)) parent.set(b, b);
    const ra = find(a),
      rb = find(b);
    if (ra !== rb) parent.set(ra, rb);
  }
  const groups = new Map<number, number[]>();
  for (const e of edges) {
    const r = find(from[e]);
    const g = groups.get(r);
    if (g) g.push(e);
    else groups.set(r, [e]);
  }
  const out: Loop[] = [];
  for (const g of groups.values()) {
    if (g.length < 3) continue;
    const adj = new Map<number, number[]>();
    let per = 0;
    for (const e of g) {
      per += dist(from[e], to[e]);
      for (const [a, b] of [
        [from[e], to[e]],
        [to[e], from[e]],
      ]) {
        const l = adj.get(a);
        if (l) l.push(b);
        else adj.set(a, [b]);
      }
    }
    // walk from a chain end if there is one, taking unvisited neighbours
    let startV = from[g[0]];
    for (const [v, l] of adj) if (l.length % 2) {
      startV = v;
      break;
    }
    const seen = new Set<number>([startV]);
    const verts = [startV];
    let v = startV;
    for (;;) {
      const nb = adj.get(v)?.find((w) => !seen.has(w));
      if (nb === undefined) break;
      seen.add(nb);
      verts.push(nb);
      v = nb;
    }
    out.push({ verts: Uint32Array.from(verts), perimeter: per, closed: false });
  }
  return out;
}

// ------------------------------------------------------------------ statistics
export type Analysis = { stats: MeshStats; holes: HoleOutline[]; loops: Loop[]; border: number };

const analyses = new WeakMap<TriMesh, Analysis>();

/** MeshStats and the hole outlines (every boundary loop but the longest, the outer border). Cached per mesh. */
export function analyse(m: TriMesh): Analysis {
  const hit = analyses.get(m);
  if (hit) return hit;
  const P = m.positions;
  const I = m.indices;
  const F = I.length / 3;
  const V = P.length / 3;
  const edges = edgeInfo(m);
  const loops = boundaryLoops(m, edges);
  let border = -1;
  for (let i = 0; i < loops.length; i++) if (border < 0 || loops[i].perimeter > loops[border].perimeter) border = i;
  const holes: HoleOutline[] = [];
  let largest: number | null = null;
  loops.forEach((l, i) => {
    if (i === border) return;
    const pts: number[] = new Array(l.verts.length * 3);
    for (let k = 0; k < l.verts.length; k++) {
      const v = l.verts[k];
      pts[k * 3] = Math.round(P[v * 3] * 1000) / 1000;
      pts[k * 3 + 1] = Math.round(P[v * 3 + 1] * 1000) / 1000;
      pts[k * 3 + 2] = Math.round(P[v * 3 + 2] * 1000) / 1000;
    }
    holes.push({ perimeter_mm: Math.round(l.perimeter * 100) / 100, n_edges: l.verts.length, points: pts });
    largest = Math.max(largest ?? 0, l.perimeter);
  });
  holes.sort((a, b) => b.perimeter_mm - a.perimeter_mm);

  let minX = Infinity,
    minY = Infinity,
    minZ = Infinity,
    maxX = -Infinity,
    maxY = -Infinity,
    maxZ = -Infinity;
  for (let i = 0; i < P.length; i += 3) {
    const x = P[i],
      y = P[i + 1],
      z = P[i + 2];
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
    if (z < minZ) minZ = z;
    if (z > maxZ) maxZ = z;
  }
  let area = 0,
    vol = 0;
  for (let i = 0; i < I.length; i += 3) {
    const a = I[i] * 3,
      b = I[i + 1] * 3,
      c = I[i + 2] * 3;
    const ux = P[b] - P[a],
      uy = P[b + 1] - P[a + 1],
      uz = P[b + 2] - P[a + 2];
    const vx = P[c] - P[a],
      vy = P[c + 1] - P[a + 1],
      vz = P[c + 2] - P[a + 2];
    const cx = uy * vz - uz * vy,
      cy = uz * vx - ux * vz,
      cz = ux * vy - uy * vx;
    area += Math.hypot(cx, cy, cz) / 2;
    vol += (P[a] * (P[b + 1] * P[c + 2] - P[b + 2] * P[c + 1]) - P[a + 1] * (P[b] * P[c + 2] - P[b + 2] * P[c]) + P[a + 2] * (P[b] * P[c + 1] - P[b + 1] * P[c])) / 6;
  }
  const watertight = edges.boundary === 0 && edges.nonManifold === 0 && F > 0;
  const r2 = (x: number) => Math.round(x * 100) / 100;
  const stats: MeshStats = {
    triangles: F,
    vertices: V,
    components: components(m).count,
    holes: holes.length,
    largest_hole_mm: largest === null ? null : r2(largest),
    border_mm: border >= 0 ? r2(loops[border].perimeter) : null,
    boundary_edges: edges.boundary,
    non_manifold_edges: edges.nonManifold,
    watertight,
    bbox_mm: F ? [r2(maxX - minX), r2(maxY - minY), r2(maxZ - minZ)] : [0, 0, 0],
    area_cm2: r2(area / 100),
    volume_cm3: watertight ? r2(Math.abs(vol) / 1000) : null,
  };
  const out = { stats, holes, loops, border };
  analyses.set(m, out);
  return out;
}

// ------------------------------------------------------------------ edits
export type FloaterParams = { min_triangles?: number; min_size_mm?: number; keep_largest_only?: boolean };

/** Drop the loose pieces: smaller than a size (bounding-box diagonal), with few triangles, or all but the largest. */
export function* removeFloaters(m: TriMesh, p: FloaterParams): Work<TriMesh> {
  const { faceComp, count } = components(m);
  yield 0.4;
  const tris = new Int32Array(count);
  const box = new Float32Array(count * 6);
  for (let c = 0; c < count; c++) box.set([Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity], c * 6);
  const P = m.positions,
    I = m.indices;
  for (let f = 0; f < faceComp.length; f++) {
    const c = faceComp[f];
    tris[c]++;
    for (let k = 0; k < 3; k++) {
      const v = I[f * 3 + k] * 3;
      for (let d = 0; d < 3; d++) {
        const x = P[v + d];
        if (x < box[c * 6 + d]) box[c * 6 + d] = x;
        if (x > box[c * 6 + 3 + d]) box[c * 6 + 3 + d] = x;
      }
    }
  }
  yield 0.7;
  let largest = 0;
  for (let c = 1; c < count; c++) if (tris[c] > tris[largest]) largest = c;
  const drop = new Uint8Array(count);
  for (let c = 0; c < count; c++) {
    if (c === largest) continue;
    const diag = Math.hypot(box[c * 6 + 3] - box[c * 6], box[c * 6 + 4] - box[c * 6 + 1], box[c * 6 + 5] - box[c * 6 + 2]);
    if (p.keep_largest_only) drop[c] = 1;
    if (p.min_triangles !== undefined && tris[c] < p.min_triangles) drop[c] = 1;
    if (p.min_size_mm !== undefined && diag < p.min_size_mm) drop[c] = 1;
  }
  const keep = new Uint8Array(faceComp.length);
  for (let f = 0; f < keep.length; f++) keep[f] = drop[faceComp[f]] ? 0 : 1;
  return compact(m, keep);
}

/**
 * Close every hole up to `maxMm` around (the outer border never) with a domed patch: a ring
 * and a centre raised along the hole's normal as far as the surrounding surface curves.
 */
export function* fillHoles(m: TriMesh, maxMm: number): Work<TriMesh> {
  const a = analyse(m);
  const todo = a.loops.filter((l, i) => i !== a.border && l.closed && l.perimeter <= maxMm);
  if (!todo.length) return m;
  const P = m.positions,
    N = m.normals;
  const pos: number[] = Array.from(P);
  const tris: number[] = [];
  let vi = P.length / 3;
  todo.forEach((l) => {
    const vs = l.verts;
    const k = vs.length;
    let cx = 0,
      cy = 0,
      cz = 0,
      nx = 0,
      ny = 0,
      nz = 0;
    for (let i = 0; i < k; i++) {
      const v = vs[i] * 3;
      cx += P[v];
      cy += P[v + 1];
      cz += P[v + 2];
      nx += N[v];
      ny += N[v + 1];
      nz += N[v + 2];
    }
    cx /= k;
    cy /= k;
    cz /= k;
    const nl = Math.hypot(nx, ny, nz) || 1;
    nx /= nl;
    ny /= nl;
    nz /= nl;
    // How far the surrounding tangent planes put the centre: the dome's height.
    let bulge = 0,
      wsum = 0;
    for (let i = 0; i < k; i++) {
      const v = vs[i] * 3;
      const d = N[v] * nx + N[v + 1] * ny + N[v + 2] * nz;
      if (d < 0.3) continue;
      bulge += ((P[v] - cx) * N[v] + (P[v + 1] - cy) * N[v + 1] + (P[v + 2] - cz) * N[v + 2]) / d;
      wsum++;
    }
    bulge = wsum ? (bulge / wsum) * 0.5 : 0;
    const centre = vi++;
    pos.push(cx + nx * bulge, cy + ny * bulge, cz + nz * bulge);
    if (k < 6) {
      for (let i = 0; i < k; i++) tris.push(vs[(i + 1) % k], vs[i], centre);
    } else {
      const ring = vi;
      for (let i = 0; i < k; i++) {
        const v = vs[i] * 3;
        const h = bulge * 0.75;
        pos.push((P[v] + cx) / 2 + nx * h, (P[v + 1] + cy) / 2 + ny * h, (P[v + 2] + cz) / 2 + nz * h);
      }
      vi += k;
      for (let i = 0; i < k; i++) {
        const j = (i + 1) % k;
        tris.push(vs[j], vs[i], ring + i);
        tris.push(vs[j], ring + i, ring + j);
        tris.push(ring + j, ring + i, centre);
      }
    }
  });
  yield 0.8;
  const I = new Uint32Array(m.indices.length + tris.length);
  I.set(m.indices);
  I.set(tris, m.indices.length);
  const positions = Float32Array.from(pos);
  return withNormals(positions, I);
}

/**
 * Taubin (λ|μ, keeps the volume) or an edge-keeping, normal-weighted ("bilateral") smoothing.
 * The border never moves; with `keepEdges`, neither do vertices on sharp creases.
 */
export function* smooth(m: TriMesh, method: "taubin" | "bilateral", strength: number, keepEdges: boolean): Work<TriMesh> {
  const V = m.positions.length / 3;
  const { start, list } = vertexNeighbours(m);
  const edges = edgeInfo(m);
  const fixed = new Uint8Array(V);
  for (let v = 0; v < V; v++) if (edges.boundaryVertex[v]) fixed[v] = 1;
  const N0 = m.normals;
  if (keepEdges) {
    // a vertex whose neighbours' normals turn by more than ~35° sits on a crease
    for (let v = 0; v < V; v++) {
      for (let k = start[v]; k < start[v + 1]; k++) {
        const u = list[k];
        if (N0[v * 3] * N0[u * 3] + N0[v * 3 + 1] * N0[u * 3 + 1] + N0[v * 3 + 2] * N0[u * 3 + 2] < 0.82) {
          fixed[v] = 1;
          break;
        }
      }
    }
  }
  yield 0.1;
  let P = Float32Array.from(m.positions);
  let Q = new Float32Array(P.length);
  const bilateral = method === "bilateral";
  const iters = bilateral ? Math.max(1, Math.round(1 + 9 * strength)) : Math.max(1, Math.round(2 + 18 * strength));
  const pass = (factor: number) => {
    for (let v = 0; v < V; v++) {
      const i = v * 3;
      if (fixed[v] || start[v] === start[v + 1]) {
        Q[i] = P[i];
        Q[i + 1] = P[i + 1];
        Q[i + 2] = P[i + 2];
        continue;
      }
      let ax = 0,
        ay = 0,
        az = 0,
        w = 0;
      for (let k = start[v]; k < start[v + 1]; k++) {
        const u = list[k] * 3;
        let wu = 1;
        if (bilateral) {
          const d = 1 - (N0[i] * N0[u] + N0[i + 1] * N0[u + 1] + N0[i + 2] * N0[u + 2]);
          wu = Math.exp(-(d * d) / 0.004);
        }
        ax += P[u] * wu;
        ay += P[u + 1] * wu;
        az += P[u + 2] * wu;
        w += wu;
      }
      if (w <= 1e-9) {
        Q[i] = P[i];
        Q[i + 1] = P[i + 1];
        Q[i + 2] = P[i + 2];
        continue;
      }
      Q[i] = P[i] + factor * (ax / w - P[i]);
      Q[i + 1] = P[i + 1] + factor * (ay / w - P[i + 1]);
      Q[i + 2] = P[i + 2] + factor * (az / w - P[i + 2]);
    }
    const t = P;
    P = Q;
    Q = t;
  };
  for (let it = 0; it < iters; it++) {
    pass(bilateral ? 0.6 : 0.5);
    pass(bilateral ? -0.3 : -0.53);
    yield 0.1 + (0.85 * (it + 1)) / iters;
  }
  return withNormals(P, m.indices);
}

/** Fewer triangles by vertex clustering on a grid sized for the target (a stand-in for quadric decimation). */
export function* decimate(m: TriMesh, target: number): Work<TriMesh> {
  const F = m.indices.length / 3;
  if (target >= F) return m;
  const P = m.positions,
    I = m.indices;
  // mean edge length from a sample
  let sum = 0,
    n = 0;
  const step = Math.max(1, Math.floor(F / 4000));
  for (let f = 0; f < F; f += step) {
    const a = I[f * 3] * 3,
      b = I[f * 3 + 1] * 3;
    sum += Math.hypot(P[a] - P[b], P[a + 1] - P[b + 1], P[a + 2] - P[b + 2]);
    n++;
  }
  let cell = (sum / Math.max(1, n)) * Math.sqrt(F / Math.max(4, target)) * 0.85;
  const locked = edgeInfo(m).boundaryVertex;
  let best: TriMesh | null = null;
  for (let attempt = 0; attempt < 4; attempt++) {
    const out = cluster(m, cell, locked);
    yield 0.2 + attempt * 0.2;
    const got = out.indices.length / 3;
    if (!best || Math.abs(got - target) < Math.abs(best.indices.length / 3 - target)) best = out;
    if (Math.abs(got - target) <= target * 0.08 || got === 0) break;
    cell *= Math.sqrt(got / target);
  }
  return best!;
}

/** Vertex clustering on a grid; border vertices stay put (so the outline and the holes keep their shape). */
function cluster(m: TriMesh, cell: number, locked: Uint8Array): TriMesh {
  const P = m.positions,
    I = m.indices;
  const V = P.length / 3;
  const ids = new Map<number, number>();
  const cid = new Int32Array(V);
  const acc: number[] = [];
  for (let v = 0; v < V; v++) {
    const ix = Math.floor(P[v * 3] / cell) + 32768,
      iy = Math.floor(P[v * 3 + 1] / cell) + 32768,
      iz = Math.floor(P[v * 3 + 2] / cell) + 32768;
    const key = locked[v] ? -1 - v : (ix * 65536 + iy) * 65536 + iz;
    let c = ids.get(key);
    if (c === undefined) {
      c = ids.size;
      ids.set(key, c);
      acc.push(0, 0, 0, 0);
    }
    cid[v] = c;
    acc[c * 4] += P[v * 3];
    acc[c * 4 + 1] += P[v * 3 + 1];
    acc[c * 4 + 2] += P[v * 3 + 2];
    acc[c * 4 + 3]++;
  }
  const C = ids.size;
  const positions = new Float32Array(C * 3);
  for (let c = 0; c < C; c++) {
    const w = acc[c * 4 + 3];
    positions[c * 3] = acc[c * 4] / w;
    positions[c * 3 + 1] = acc[c * 4 + 1] / w;
    positions[c * 3 + 2] = acc[c * 4 + 2] / w;
  }
  const out: number[] = [];
  // Two faces on the same three clusters: the same winding is a duplicate (keep one); opposite
  // windings are a fold (a "pillow"): drop both, so the faces around them stay consistently oriented.
  const numeric = C * C * C < 2 ** 52;
  const seen = new Map<number | string, number>(); // key → face index in `out` (-1 once dropped)
  const even: boolean[] = [];
  for (let i = 0; i < I.length; i += 3) {
    const a = cid[I[i]],
      b = cid[I[i + 1]],
      c = cid[I[i + 2]];
    if (a === b || b === c || a === c) continue;
    const lo = Math.min(a, b, c),
      hi = Math.max(a, b, c),
      mid = a + b + c - lo - hi;
    const k = numeric ? (lo * C + mid) * C + hi : `${lo},${mid},${hi}`;
    const ev = (a === lo && b === mid) || (b === lo && c === mid) || (c === lo && a === mid);
    const prev = seen.get(k);
    if (prev !== undefined) {
      if (prev >= 0 && even[prev] !== ev) {
        out[prev * 3] = out[prev * 3 + 1] = out[prev * 3 + 2] = -1;
        seen.set(k, -1);
      }
      continue;
    }
    seen.set(k, out.length / 3);
    even.push(ev);
    out.push(a, b, c);
  }
  const kept = out.filter((x) => x >= 0);
  // Clustering leaves pinholes a cell or two across: close them, as a decimator wouldn't open them.
  return runSync(fillHoles(dropNonManifold(compact(withNormals(positions, Uint32Array.from(kept)), null)), cell * 4));
}

/** Leaves at most two faces on every edge (clustering can fold three or more onto one). */
function dropNonManifold(m: TriMesh): TriMesh {
  const I = m.indices;
  const V = m.positions.length / 3;
  const F = I.length / 3;
  const start = new Uint32Array(V + 1);
  for (let i = 0; i < I.length; i++) {
    const a = I[i],
      b = I[i - (i % 3) + ((i % 3) + 1) % 3];
    start[(a < b ? a : b) + 1]++;
  }
  for (let v = 0; v < V; v++) start[v + 1] += start[v];
  const fill = start.slice(0, V);
  const other = new Uint32Array(I.length);
  const face = new Uint32Array(I.length);
  for (let i = 0; i < I.length; i++) {
    const a = I[i],
      b = I[i - (i % 3) + ((i % 3) + 1) % 3];
    const at = fill[a < b ? a : b]++;
    other[at] = a < b ? b : a;
    face[at] = (i / 3) | 0;
  }
  const drop = new Uint8Array(F);
  let dropped = 0;
  for (let lo = 0; lo < V; lo++) {
    const s = start[lo],
      e = start[lo + 1];
    if (e - s < 3) continue;
    for (let i = s; i < e; i++) {
      let seen = 0;
      for (let j = s; j < i; j++) if (other[j] === other[i] && !drop[face[j]]) seen++;
      if (seen >= 2 && !drop[face[i]]) {
        drop[face[i]] = 1;
        dropped++;
      }
    }
  }
  if (!dropped) return m;
  for (let f = 0; f < F; f++) drop[f] ^= 1;
  return compact(m, drop);
}

export function deleteFaces(m: TriMesh, faces: Uint32Array, keepThem: boolean): TriMesh {
  const F = m.indices.length / 3;
  const mark = new Uint8Array(F);
  for (let i = 0; i < faces.length; i++) mark[faces[i]] = 1;
  if (!keepThem) for (let f = 0; f < F; f++) mark[f] ^= 1;
  return compact(m, mark);
}

// ------------------------------------------------------------------ files
/**
 * Binary PLY (float x y z nx ny nz; uchar int face lists), its header padded so the vertices start 4-byte
 * aligned. With `sigma` (one value per vertex, mm) each vertex also gets a float `quality`, where the
 * engine writes its sigma.
 */
export function encodePly(m: TriMesh, sigma: Float32Array | null = null): ArrayBuffer {
  const V = m.positions.length / 3;
  const F = m.indices.length / 3;
  const q = sigma && sigma.length === V ? sigma : null;
  const n = q ? 7 : 6; // floats per vertex
  const head = (pad: string) =>
    `ply\nformat binary_little_endian 1.0\ncomment OpenHandScan demo engine (mm)${pad}\n` +
    `element vertex ${V}\nproperty float x\nproperty float y\nproperty float z\nproperty float nx\nproperty float ny\nproperty float nz\n` +
    (q ? "property float quality\n" : "") +
    `element face ${F}\nproperty list uchar int vertex_indices\nend_header\n`;
  const enc = new TextEncoder();
  const hb = enc.encode(head(" ".repeat((4 - (enc.encode(head("")).length % 4)) % 4)));
  const buf = new ArrayBuffer(hb.length + V * n * 4 + F * 13);
  new Uint8Array(buf).set(hb);
  const vf = new Float32Array(buf, hb.length, V * n);
  for (let v = 0; v < V; v++) {
    vf[v * n] = m.positions[v * 3];
    vf[v * n + 1] = m.positions[v * 3 + 1];
    vf[v * n + 2] = m.positions[v * 3 + 2];
    vf[v * n + 3] = m.normals[v * 3];
    vf[v * n + 4] = m.normals[v * 3 + 1];
    vf[v * n + 5] = m.normals[v * 3 + 2];
    if (q) vf[v * n + 6] = q[v];
  }
  const dv = new DataView(buf);
  let o = hb.length + V * n * 4;
  const I = m.indices;
  for (let f = 0; f < F; f++) {
    dv.setUint8(o, 3);
    dv.setInt32(o + 1, I[f * 3], true);
    dv.setInt32(o + 5, I[f * 3 + 1], true);
    dv.setInt32(o + 9, I[f * 3 + 2], true);
    o += 13;
  }
  return buf;
}

export function encodeObj(m: TriMesh): ArrayBuffer {
  const parts: string[] = ["# OpenHandScan demo engine, units mm\n"];
  const P = m.positions,
    N = m.normals,
    I = m.indices;
  const f3 = (x: number) => (Math.round(x * 10000) / 10000).toString();
  let chunk: string[] = [];
  const flush = () => {
    parts.push(chunk.join(""));
    chunk = [];
  };
  for (let v = 0; v < P.length / 3; v++) {
    chunk.push(`v ${f3(P[v * 3])} ${f3(P[v * 3 + 1])} ${f3(P[v * 3 + 2])}\n`);
    if (chunk.length >= 20000) flush();
  }
  for (let v = 0; v < N.length / 3; v++) {
    chunk.push(`vn ${f3(N[v * 3])} ${f3(N[v * 3 + 1])} ${f3(N[v * 3 + 2])}\n`);
    if (chunk.length >= 20000) flush();
  }
  for (let f = 0; f < I.length / 3; f++) {
    const a = I[f * 3] + 1,
      b = I[f * 3 + 1] + 1,
      c = I[f * 3 + 2] + 1;
    chunk.push(`f ${a}//${a} ${b}//${b} ${c}//${c}\n`);
    if (chunk.length >= 20000) flush();
  }
  flush();
  return new TextEncoder().encode(parts.join("")).buffer as ArrayBuffer;
}

/** base64 → little-endian uint32 array; null when it isn't valid. */
export function decodeFaces(b64: string): Uint32Array | null {
  let bin: string;
  try {
    bin = atob(b64);
  } catch {
    return null;
  }
  if (bin.length % 4) return null;
  const out = new Uint32Array(bin.length / 4);
  for (let i = 0; i < out.length; i++) {
    const o = i * 4;
    out[i] = (bin.charCodeAt(o) | (bin.charCodeAt(o + 1) << 8) | (bin.charCodeAt(o + 2) << 16) | (bin.charCodeAt(o + 3) << 24)) >>> 0;
  }
  return out;
}
