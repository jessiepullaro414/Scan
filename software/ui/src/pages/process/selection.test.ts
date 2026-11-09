/**
 * Checks for "visible only" selection (`npm run test`). Plain TypeScript that Node runs as it is
 * (type stripping), no test framework: a failed check throws.
 *
 * A synthetic scene stands in for the GPU. The camera sits at the origin looking down -z, and
 * quads are ray-cast into a depth image exactly as `DepthProbe` writes one: the view depth in
 * 24 bits of RGB between near and far, alpha 0 for background, rows bottom-up as `readPixels`
 * returns them. The vertices are projected by `buildScreenMap`, as on the page.
 */
import { buildScreenMap, MeshSelection, vertexVisibility, type DepthImage } from "./selection.ts";

type Vec3 = [number, number, number];
/** A rectangle: centre, unit axes, half sizes (mm). */
type Quad = { p0: Vec3; eu: Vec3; ev: Vec3; hu: number; hv: number };

const W = 320;
const H = 240;
const FOV = 35;
const NEAR = 1;
const FAR = 10000;
const F = 1 / Math.tan((FOV * Math.PI) / 360);
const ASPECT = W / H;
/** Projection × view (the view is the identity), column-major like three.js' Matrix4.elements. */
const MVP = [F / ASPECT, 0, 0, 0, 0, F, 0, 0, 0, 0, (FAR + NEAR) / (NEAR - FAR), -1, 0, 0, (2 * FAR * NEAR) / (NEAR - FAR), 0];

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`selection.test: ${what}`);
}

/** A 300 mm square 600 mm away, tilted back by `deg` about x: its top runs away from the camera. */
function tilted(deg: number): Quad {
  const t = (deg * Math.PI) / 180;
  return { p0: [0, 0, -600], eu: [1, 0, 0], ev: [0, Math.cos(t), -Math.sin(t)], hu: 150, hv: 150 };
}

/** n×n grid vertices and two triangles per cell over each quad. */
function meshOf(quads: Quad[], n: number) {
  const pos: number[] = [];
  const idx: number[] = [];
  const quadOf: number[] = [];
  /** Per vertex: v along the quad (-1 bottom … 1 top). */
  const vOf: number[] = [];
  quads.forEach((q, qi) => {
    const base = pos.length / 3;
    for (let j = 0; j < n; j++)
      for (let i = 0; i < n; i++) {
        const u = ((i / (n - 1)) * 2 - 1) * q.hu;
        const v = ((j / (n - 1)) * 2 - 1) * q.hv;
        for (let k = 0; k < 3; k++) pos.push(q.p0[k] + u * q.eu[k] + v * q.ev[k]);
        quadOf.push(qi);
        vOf.push(v / q.hv);
      }
    for (let j = 0; j + 1 < n; j++)
      for (let i = 0; i + 1 < n; i++) {
        const a = base + j * n + i;
        idx.push(a, a + 1, a + n + 1, a, a + n + 1, a + n);
      }
  });
  const sel = new MeshSelection(1, new Float32Array(pos), new Uint32Array(idx), null);
  return { sel, quadOf, vOf, map: buildScreenMap(sel, MVP, W, H, null) };
}

/** The depth image DepthProbe would read back, and the nearest quad under each pixel (y down; -1 for none). */
function render(quads: Quad[], rowsBottomUp = true): { img: DepthImage; nearest: Int8Array } {
  // near and far as DepthProbe sets them: around the scene's bounding sphere
  const corners: Vec3[] = [];
  for (const q of quads)
    for (const [a, b] of [[-1, -1], [1, -1], [-1, 1], [1, 1]])
      corners.push(q.p0.map((p, k) => p + a * q.hu * q.eu[k] + b * q.hv * q.ev[k]) as Vec3);
  const c = [0, 1, 2].map((k) => corners.reduce((s, p) => s + p[k], 0) / corners.length) as Vec3;
  const r = Math.max(...corners.map((p) => Math.hypot(p[0] - c[0], p[1] - c[1], p[2] - c[2])));
  const near = Math.max(0.01, -c[2] - r - 2);
  const far = Math.max(near + 1, -c[2] + r + 2);

  const rgba = new Uint8Array(W * H * 4);
  const nearest = new Int8Array(W * H).fill(-1);
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++) {
      // the ray through the pixel's centre, scaled to view depth 1
      const ray: Vec3 = [((((x + 0.5) / W) * 2 - 1) * ASPECT) / F, (1 - ((y + 0.5) / H) * 2) / F, -1];
      let best = Infinity;
      quads.forEach((q, qi) => {
        const n = cross(q.eu, q.ev);
        const den = dot(n, ray);
        if (Math.abs(den) < 1e-12) return;
        const D = dot(n, q.p0) / den;
        if (!(D > 0) || D >= best) return;
        const rel: Vec3 = [ray[0] * D - q.p0[0], ray[1] * D - q.p0[1], ray[2] * D - q.p0[2]];
        if (Math.abs(dot(rel, q.eu)) > q.hu || Math.abs(dot(rel, q.ev)) > q.hv) return;
        best = D;
        nearest[y * W + x] = qi;
      });
      if (best === Infinity) continue;
      const code = Math.floor(Math.min(1, Math.max(0, (best - near) / (far - near))) * 16777215);
      const o = ((rowsBottomUp ? H - 1 - y : y) * W + x) * 4;
      rgba[o] = code >> 16;
      rgba[o + 1] = (code >> 8) & 255;
      rgba[o + 2] = code & 255;
      rgba[o + 3] = 255;
    }
  return { img: { rgba, W, H, near, far, pixelAngle: (2 * Math.tan((FOV * Math.PI) / 360)) / H }, nearest };
}

const zeros = (a: Uint8Array) => a.reduce((s, x) => s + (x ? 0 : 1), 0);

// 1. Orientation: every vertex of a fully visible plane passes, at any tilt; read the other way
//    round, the same image fails much of it (so this check would catch a flipped lookup).
for (const deg of [0, 30, 60, 75, 85]) {
  const scene = [tilted(deg)];
  const { sel, map } = meshOf(scene, 81);
  const { img } = render(scene);
  const vis = vertexVisibility(map.sx, map.sy, map.depth, img);
  check(zeros(vis) === 0, `plane tilted ${deg}°: ${zeros(vis)} of ${sel.vertices} vertices hidden, all are visible`);
  const ok = buildScreenMap(sel, MVP, W, H, img).ok;
  check(zeros(ok) === 0, `plane tilted ${deg}°: ${zeros(ok)} of ${sel.faces} faces not selectable`);
  if (deg >= 30) {
    const flipped = vertexVisibility(map.sx, map.sy, map.depth, render(scene, false).img);
    check(zeros(flipped) > sel.vertices * 0.25, `plane tilted ${deg}°: an upside-down depth image hid only ${zeros(flipped)} vertices`);
  }
}

// 2. Occlusion: a card 150 mm in front of the middle of a 60° plane hides what is behind it.
{
  const card: Quad = { p0: [10, 5, -450], eu: [1, 0, 0], ev: [0, 1, 0], hu: 40, hv: 30 };
  const scene = [tilted(60), card];
  const { sel, map, quadOf } = meshOf(scene, 81);
  const { img, nearest } = render(scene);
  const vis = vertexVisibility(map.sx, map.sy, map.depth, img);
  // Plane vertices: "hidden" when the card covers every pixel within 2 of theirs, "clear" when none.
  const kind = new Int8Array(sel.vertices); // 1 hidden, 2 clear, 0 near the card's outline or the card itself
  for (let v = 0; v < sel.vertices; v++) {
    if (quadOf[v] !== 0) continue;
    const px = map.sx[v] | 0;
    const py = map.sy[v] | 0;
    let card = 0;
    let other = 0;
    for (let dy = -2; dy <= 2; dy++)
      for (let dx = -2; dx <= 2; dx++) {
        const x = px + dx;
        const y = py + dy;
        if (x < 0 || y < 0 || x >= W || y >= H || nearest[y * W + x] !== 1) other++;
        else card++;
      }
    kind[v] = other === 0 ? 1 : card === 0 ? 2 : 0;
  }
  let hidden = 0;
  let hiddenPassed = 0;
  let clear = 0;
  let clearFailed = 0;
  let cardFailed = 0;
  for (let v = 0; v < sel.vertices; v++) {
    if (quadOf[v] === 1 && !vis[v]) cardFailed++;
    if (kind[v] === 1) {
      hidden++;
      if (vis[v]) hiddenPassed++;
    } else if (kind[v] === 2) {
      clear++;
      if (!vis[v]) clearFailed++;
    }
  }
  check(hidden > 200 && hiddenPassed === 0, `card: ${hiddenPassed} of ${hidden} plane vertices behind it count as visible`);
  check(clear > 2000 && clearFailed === 0, `card: ${clearFailed} of ${clear} plane vertices clear of it count as hidden`);
  check(cardFailed === 0, `card: ${cardFailed} of its own vertices count as hidden`);

  const visible = buildScreenMap(sel, MVP, W, H, img);
  const through = buildScreenMap(sel, MVP, W, H, null);
  let hiddenFaces = 0;
  let hiddenOk = 0;
  let clearFaces = 0;
  let clearNotOk = 0;
  for (let f = 0; f < sel.faces; f++) {
    const k = [0, 1, 2].map((i) => kind[sel.index[f * 3 + i]]);
    if (k.every((x) => x === 1)) {
      hiddenFaces++;
      if (visible.ok[f]) hiddenOk++;
    } else if (k.every((x) => x === 2)) {
      clearFaces++;
      if (!visible.ok[f]) clearNotOk++;
    }
  }
  check(hiddenFaces > 200 && hiddenOk === 0, `card: ${hiddenOk} of ${hiddenFaces} faces behind it are selectable when visible only`);
  check(clearFaces > 2000 && clearNotOk === 0, `card: ${clearNotOk} of ${clearFaces} faces clear of it are not selectable`);
  check(zeros(through.ok) === 0, `card: selecting through, ${zeros(through.ok)} faces are not selectable`);
}

// 3. The box over the far part of an oblique plane selects it, visible only as through.
{
  const scene = [tilted(70)];
  const { sel, map, vOf } = meshOf(scene, 121);
  const { img } = render(scene);
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (let v = 0; v < sel.vertices; v++) {
    if (vOf[v] < 0.6) continue; // the top fifth, the farthest away
    x0 = Math.min(x0, map.sx[v]);
    x1 = Math.max(x1, map.sx[v]);
    y0 = Math.min(y0, map.sy[v]);
    y1 = Math.max(y1, map.sy[v]);
  }
  const box = (depth: DepthImage | null) => {
    sel.applyBox(buildScreenMap(sel, MVP, W, H, depth), x0, y0, x1, y1, "replace");
    return sel.count;
  };
  const through = box(null);
  const visible = box(img);
  check(through > 1000 && visible === through, `far part of a 70° plane: ${visible} faces visible only, ${through} through`);
}

console.log(`selection.test: ${checks} checks passed`);
