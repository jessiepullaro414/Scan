/**
 * Face selection on big meshes (about a million triangles) with plain typed arrays, no BVH.
 *
 * - Per camera pose, every vertex is projected to the screen once (`buildScreenMap`). Faces
 *   are tested by their screen centroid; a face behind the camera is never selected.
 * - "Visible only" uses one depth render of the mesh, read back from the GPU: a vertex counts
 *   as visible when it is not behind the drawn surface (a small tolerance in pixels), and a
 *   face when two of its three corners are.
 * - Box and lasso run once, when the drag ends. The brush bins the faces into a screen grid
 *   when the stroke starts, so each pointer move tests only the faces under it.
 * - "Connected" picks the nearest face under the cursor (2D point-in-triangle on the
 *   projected corners), then walks the faces that share vertices with it.
 *
 * Face indices are those of the mesh as loaded, which keeps the file's order: what
 * `delete_faces` / `keep_faces` expect ("face indices of the CURRENT mesh").
 */

export type SelectMode = "replace" | "add" | "subtract";

/** A brush's screen grid: the selectable faces, bucketed by their centroid. */
export type FaceGrid = { cell: number; cols: number; rows: number; start: Int32Array; items: Int32Array };

/** The mesh on screen for one camera pose. */
export type ScreenMap = {
  W: number;
  H: number;
  /** Per vertex: screen position (CSS px, y down) and view depth (mm; <= 0 means behind the camera). */
  sx: Float32Array;
  sy: Float32Array;
  depth: Float32Array;
  /** Per face: 1 when it can be selected (in front, and visible unless selecting through). */
  ok: Uint8Array;
  /** Per face: screen centroid. */
  cx: Float32Array;
  cy: Float32Array;
  through: boolean;
  grid: FaceGrid | null;
};

/** A depth image of the mesh: RGB holds the view depth in 24 bits between `near` and `far`; alpha 0 is background. */
export type DepthImage = { rgba: Uint8Array; W: number; H: number; near: number; far: number; pixelAngle: number };

const LITTLE_ENDIAN = new Uint8Array(new Uint32Array([1]).buffer)[0] === 1;

export class MeshSelection {
  readonly version: number;
  readonly faces: number;
  readonly vertices: number;
  readonly positions: Float32Array;
  readonly index: Uint32Array;
  /** The file's face of each triangle when polygons were split, else null. */
  readonly faceOfTri: Uint32Array | null;
  readonly selected: Uint8Array;
  count = 0;
  private listeners = new Set<() => void>();
  private vf: { start: Uint32Array; list: Uint32Array } | null = null;

  constructor(version: number, positions: Float32Array, index: Uint32Array, faceOfTri: Uint32Array | null) {
    this.version = version;
    this.positions = positions;
    this.index = index;
    this.faceOfTri = faceOfTri;
    this.vertices = positions.length / 3;
    this.faces = index.length / 3;
    this.selected = new Uint8Array(this.faces);
  }

  /** Listen to every change (the highlight redraws; it throttles itself). */
  onChange(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  changed(): void {
    for (const l of this.listeners) l();
  }

  clear(): void {
    if (!this.count) return;
    this.selected.fill(0);
    this.count = 0;
    this.changed();
  }

  all(): void {
    this.selected.fill(1);
    this.count = this.faces;
    this.changed();
  }

  invert(): void {
    const s = this.selected;
    for (let f = 0; f < s.length; f++) s[f] ^= 1;
    this.count = this.faces - this.count;
    this.changed();
  }

  // ---------------------------------------------------------------- regions
  /** Faces whose centroid is inside the screen rectangle. */
  applyBox(map: ScreenMap, x0: number, y0: number, x1: number, y1: number, mode: SelectMode): void {
    const ax = Math.min(x0, x1),
      bx = Math.max(x0, x1),
      ay = Math.min(y0, y1),
      by = Math.max(y0, y1);
    if (mode === "replace") this.resetQuiet();
    const { ok, cx, cy } = map;
    const s = this.selected;
    const set = mode !== "subtract" ? 1 : 0;
    let delta = 0;
    for (let f = 0; f < this.faces; f++) {
      if (!ok[f]) continue;
      const x = cx[f],
        y = cy[f];
      if (x < ax || x > bx || y < ay || y > by) continue;
      if (s[f] !== set) {
        s[f] = set;
        delta++;
      }
    }
    this.count += set ? delta : -delta;
    this.changed();
  }

  /** Faces whose centroid falls on a set pixel of `mask` (W×H, row-major, y down). */
  applyMask(map: ScreenMap, mask: Uint8Array, mode: SelectMode): void {
    if (mode === "replace") this.resetQuiet();
    const { ok, cx, cy, W, H } = map;
    const s = this.selected;
    const set = mode !== "subtract" ? 1 : 0;
    let delta = 0;
    for (let f = 0; f < this.faces; f++) {
      if (!ok[f]) continue;
      const x = cx[f] | 0,
        y = cy[f] | 0;
      if (x < 0 || y < 0 || x >= W || y >= H || !mask[y * W + x]) continue;
      if (s[f] !== set) {
        s[f] = set;
        delta++;
      }
    }
    this.count += set ? delta : -delta;
    this.changed();
  }

  /** The brush swept from a to b with radius r (px). Returns how many faces changed. */
  brushSegment(map: ScreenMap, ax: number, ay: number, bx: number, by: number, r: number, subtract: boolean): number {
    const g = map.grid;
    if (!g) return 0;
    const { cx, cy } = map;
    const s = this.selected;
    const set = subtract ? 0 : 1;
    const c0 = Math.max(0, Math.floor((Math.min(ax, bx) - r) / g.cell));
    const c1 = Math.min(g.cols - 1, Math.floor((Math.max(ax, bx) + r) / g.cell));
    const r0 = Math.max(0, Math.floor((Math.min(ay, by) - r) / g.cell));
    const r1 = Math.min(g.rows - 1, Math.floor((Math.max(ay, by) + r) / g.cell));
    const dx = bx - ax,
      dy = by - ay;
    const len2 = dx * dx + dy * dy;
    const r2 = r * r;
    let delta = 0;
    for (let row = r0; row <= r1; row++) {
      for (let col = c0; col <= c1; col++) {
        const cell = row * g.cols + col;
        const end: number = g.start[cell + 1];
        for (let k: number = g.start[cell]; k < end; k++) {
          const f: number = g.items[k];
          if (s[f] === set) continue;
          // distance from the centroid to the segment
          let px = cx[f] - ax,
            py = cy[f] - ay;
          if (len2 > 0) {
            const t = Math.max(0, Math.min(1, (px * dx + py * dy) / len2));
            px -= t * dx;
            py -= t * dy;
          }
          if (px * px + py * py > r2) continue;
          s[f] = set;
          delta++;
        }
      }
    }
    if (delta) {
      this.count += set ? delta : -delta;
      this.changed();
    }
    return delta;
  }

  /** The nearest face under a screen point, or -1. */
  pick(map: ScreenMap, x: number, y: number): number {
    const { sx, sy, depth } = map;
    const I = this.index;
    let best = -1;
    let bestZ = Infinity;
    for (let f = 0, i = 0; f < this.faces; f++, i += 3) {
      const a = I[i],
        b = I[i + 1],
        c = I[i + 2];
      const ax = sx[a],
        bx = sx[b],
        cx = sx[c];
      if ((x < ax && x < bx && x < cx) || (x > ax && x > bx && x > cx)) continue;
      const ay = sy[a],
        by = sy[b],
        cy = sy[c];
      if ((y < ay && y < by && y < cy) || (y > ay && y > by && y > cy)) continue;
      const za = depth[a],
        zb = depth[b],
        zc = depth[c];
      if (za <= 0 || zb <= 0 || zc <= 0) continue;
      const d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
      if (d === 0) continue;
      const l1 = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / d;
      const l2 = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / d;
      const l3 = 1 - l1 - l2;
      if (l1 < -1e-6 || l2 < -1e-6 || l3 < -1e-6) continue;
      const z = l1 * za + l2 * zb + l3 * zc;
      if (z < bestZ) {
        bestZ = z;
        best = f;
      }
    }
    return best;
  }

  /** The piece (faces sharing vertices, transitively) that `seed` belongs to. Returns its size. */
  selectConnected(seed: number, mode: SelectMode): number {
    if (seed < 0 || seed >= this.faces) return 0;
    const { start, list } = this.adjacency();
    const I = this.index;
    const seen = new Uint8Array(this.faces);
    const queue = new Uint32Array(this.faces);
    let head = 0,
      tail = 0;
    queue[tail++] = seed;
    seen[seed] = 1;
    while (head < tail) {
      const f = queue[head++];
      for (let k = 0; k < 3; k++) {
        const v = I[f * 3 + k];
        for (let j = start[v], e = start[v + 1]; j < e; j++) {
          const g = list[j];
          if (!seen[g]) {
            seen[g] = 1;
            queue[tail++] = g;
          }
        }
      }
    }
    if (mode === "replace") this.resetQuiet();
    const s = this.selected;
    const set = mode !== "subtract" ? 1 : 0;
    let delta = 0;
    for (let k = 0; k < tail; k++) {
      const f = queue[k];
      if (s[f] !== set) {
        s[f] = set;
        delta++;
      }
    }
    this.count += set ? delta : -delta;
    this.changed();
    return tail;
  }

  // ---------------------------------------------------------------- output
  /** Writes the selected triangles' corners into `out`; returns how many indices it wrote. */
  fillHighlight(out: Uint32Array): number {
    const s = this.selected;
    const I = this.index;
    let n = 0;
    for (let f = 0; f < s.length; f++) {
      if (!s[f]) continue;
      const i = f * 3;
      out[n] = I[i];
      out[n + 1] = I[i + 1];
      out[n + 2] = I[i + 2];
      n += 3;
    }
    return n;
  }

  /** The selection for `delete_faces` / `keep_faces`: base64 of little-endian uint32 face indices, ascending. */
  encode(): { faces: string; count: number } {
    const s = this.selected;
    const ids = new Uint32Array(this.count);
    let n = 0;
    if (!this.faceOfTri) {
      for (let f = 0; f < s.length && n < ids.length; f++) if (s[f]) ids[n++] = f;
    } else {
      // Triangles of one polygon are consecutive: send each polygon once.
      let last = -1;
      for (let f = 0; f < s.length; f++) {
        if (!s[f]) continue;
        const o = this.faceOfTri[f];
        if (o !== last) ids[n++] = o;
        last = o;
      }
    }
    const used = ids.subarray(0, n);
    let bytes: Uint8Array;
    if (LITTLE_ENDIAN) bytes = new Uint8Array(used.buffer, used.byteOffset, n * 4);
    else {
      bytes = new Uint8Array(n * 4);
      const dv = new DataView(bytes.buffer);
      for (let i = 0; i < n; i++) dv.setUint32(i * 4, used[i], true);
    }
    return { faces: base64(bytes), count: n };
  }

  private resetQuiet(): void {
    if (this.count) this.selected.fill(0);
    this.count = 0;
  }

  /** Faces around each vertex (CSR), built on first use. */
  private adjacency(): { start: Uint32Array; list: Uint32Array } {
    if (this.vf) return this.vf;
    const I = this.index;
    const V = this.vertices;
    const start = new Uint32Array(V + 1);
    for (let i = 0; i < I.length; i++) start[I[i] + 1]++;
    for (let v = 0; v < V; v++) start[v + 1] += start[v];
    const fill = start.slice(0, V);
    const list = new Uint32Array(I.length);
    for (let i = 0; i < I.length; i++) list[fill[I[i]]++] = (i / 3) | 0;
    this.vf = { start, list };
    return this.vf;
  }
}

// ------------------------------------------------------------------ projection
/**
 * Project the mesh for a camera: `mvp` is projection × view × model (column-major, as in
 * three.js' Matrix4.elements); W×H is the canvas in CSS px. With a depth image, faces hidden
 * behind the surface are left out.
 */
export function buildScreenMap(sel: MeshSelection, mvp: ArrayLike<number>, W: number, H: number, depthImage: DepthImage | null): ScreenMap {
  const V = sel.vertices;
  const P = sel.positions;
  const m = mvp;
  const sx = new Float32Array(V);
  const sy = new Float32Array(V);
  const depth = new Float32Array(V);
  const hw = W / 2,
    hh = H / 2;
  for (let v = 0, i = 0; v < V; v++, i += 3) {
    const x = P[i],
      y = P[i + 1],
      z = P[i + 2];
    const w = m[3] * x + m[7] * y + m[11] * z + m[15];
    if (w <= 1e-6) {
      depth[v] = -1;
      continue;
    }
    sx[v] = ((m[0] * x + m[4] * y + m[8] * z + m[12]) / w + 1) * hw;
    sy[v] = (1 - (m[1] * x + m[5] * y + m[9] * z + m[13]) / w) * hh;
    depth[v] = w; // perspective: clip w is the distance along the view axis
  }

  const vis = depthImage ? vertexVisibility(sx, sy, depth, depthImage) : null;

  const F = sel.faces;
  const I = sel.index;
  const ok = new Uint8Array(F);
  const cx = new Float32Array(F);
  const cy = new Float32Array(F);
  for (let f = 0, i = 0; f < F; f++, i += 3) {
    const a = I[i],
      b = I[i + 1],
      c = I[i + 2];
    if (depth[a] <= 0 || depth[b] <= 0 || depth[c] <= 0) continue;
    if (vis && vis[a] + vis[b] + vis[c] < 2) continue;
    ok[f] = 1;
    cx[f] = (sx[a] + sx[b] + sx[c]) / 3;
    cy[f] = (sy[a] + sy[b] + sy[c]) / 3;
  }
  return { W, H, sx, sy, depth, ok, cx, cy, through: !depthImage, grid: null };
}

/**
 * Per vertex: 1 unless the drawn surface is in front of it. A vertex passes when, at its pixel
 * or one of the four next to it, the depth image shows background or a surface no nearer than
 * the vertex minus a tolerance of a few pixels' footprint (so steep surfaces still pass).
 *
 * `sx`/`sy` are y-down screen pixels; `img.rgba` is the depth render as `readPixels` returns it,
 * rows bottom-up (row 0 is the bottom of the view). `selection.test.ts` checks this orientation.
 */
export function vertexVisibility(sx: Float32Array, sy: Float32Array, depth: Float32Array, img: DepthImage): Uint8Array {
  const { rgba, W, H, near, far, pixelAngle } = img;
  const V = depth.length;
  const out = new Uint8Array(V);
  const scale = (far - near) / 16777215;
  const OFF = [0, 0, 1, 0, -1, 0, 0, 1, 0, -1];
  for (let v = 0; v < V; v++) {
    const d = depth[v];
    if (d <= 0) continue;
    const px = sx[v] | 0,
      py = sy[v] | 0;
    if (px < 0 || py < 0 || px >= W || py >= H) {
      out[v] = 1; // off screen: never inside a region anyway
      continue;
    }
    const tol = 2.5 * d * pixelAngle + 0.05;
    let pass = 0;
    for (let k = 0; k < OFF.length && !pass; k += 2) {
      const x = px + OFF[k],
        y = py + OFF[k + 1];
      if (x < 0 || y < 0 || x >= W || y >= H) continue;
      const o = ((H - 1 - y) * W + x) * 4; // the image's rows run bottom-up
      if (rgba[o + 3] === 0) {
        pass = 1; // background: nothing in front
        break;
      }
      const surface = near + (rgba[o] * 65536 + rgba[o + 1] * 256 + rgba[o + 2]) * scale;
      if (d <= surface + tol) pass = 1;
    }
    out[v] = pass;
  }
  return out;
}

/** Bucket the selectable faces by their centroid (for the brush). */
export function buildGrid(map: ScreenMap, cell = 24): FaceGrid {
  const cols = Math.max(1, Math.ceil(map.W / cell));
  const rows = Math.max(1, Math.ceil(map.H / cell));
  const start = new Int32Array(cols * rows + 1);
  const { ok, cx, cy, W, H } = map;
  const F = ok.length;
  const cellOf = new Int32Array(F).fill(-1);
  for (let f = 0; f < F; f++) {
    if (!ok[f]) continue;
    const x = cx[f],
      y = cy[f];
    if (x < 0 || y < 0 || x >= W || y >= H) continue;
    const c = ((y / cell) | 0) * cols + ((x / cell) | 0);
    cellOf[f] = c;
    start[c + 1]++;
  }
  for (let c = 0; c < cols * rows; c++) start[c + 1] += start[c];
  const fill = start.slice(0, cols * rows);
  const items = new Int32Array(start[cols * rows]);
  for (let f = 0; f < F; f++) {
    const c = cellOf[f];
    if (c >= 0) items[fill[c]++] = f;
  }
  return { cell, cols, rows, start, items };
}

/** Rasterise a closed polygon (flattened x, y in px) into a W×H mask (even-odd rule). */
export function polygonMask(pts: ArrayLike<number>, W: number, H: number): Uint8Array {
  const mask = new Uint8Array(W * H);
  const n = pts.length / 2;
  if (n < 3) return mask;
  let minY = Infinity,
    maxY = -Infinity;
  for (let i = 0; i < n; i++) {
    minY = Math.min(minY, pts[i * 2 + 1]);
    maxY = Math.max(maxY, pts[i * 2 + 1]);
  }
  const y0 = Math.max(0, Math.floor(minY));
  const y1 = Math.min(H - 1, Math.ceil(maxY));
  const xs: number[] = [];
  for (let y = y0; y <= y1; y++) {
    const yc = y + 0.5;
    xs.length = 0;
    for (let i = 0, j = n - 1; i < n; j = i++) {
      const yi = pts[i * 2 + 1],
        yj = pts[j * 2 + 1];
      if (yi > yc !== yj > yc) {
        const xi = pts[i * 2],
          xj = pts[j * 2];
        xs.push(xi + ((yc - yi) / (yj - yi)) * (xj - xi));
      }
    }
    xs.sort((a, b) => a - b);
    const row = y * W;
    for (let k = 0; k + 1 < xs.length; k += 2) {
      const a = Math.max(0, Math.ceil(xs[k] - 0.5));
      const b = Math.min(W - 1, Math.floor(xs[k + 1] - 0.5));
      if (b >= a) mask.fill(1, row + a, row + b + 1);
    }
  }
  return mask;
}

const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const B64_CODES = Uint8Array.from(B64, (c) => c.charCodeAt(0));

/** Standard base64 with padding, fast for megabytes. */
export function base64(bytes: Uint8Array): string {
  const n = bytes.length;
  const out = new Uint8Array(Math.ceil(n / 3) * 4);
  let o = 0,
    i = 0;
  for (; i + 2 < n; i += 3) {
    const v = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
    out[o++] = B64_CODES[(v >> 18) & 63];
    out[o++] = B64_CODES[(v >> 12) & 63];
    out[o++] = B64_CODES[(v >> 6) & 63];
    out[o++] = B64_CODES[v & 63];
  }
  if (i < n) {
    const v = (bytes[i] << 16) | (i + 1 < n ? bytes[i + 1] << 8 : 0);
    out[o++] = B64_CODES[(v >> 18) & 63];
    out[o++] = B64_CODES[(v >> 12) & 63];
    out[o++] = i + 1 < n ? B64_CODES[(v >> 6) & 63] : 61; // "="
    out[o++] = 61;
  }
  return new TextDecoder().decode(out);
}
