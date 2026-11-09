/**
 * Mock calibration geometry and pictures (no three.js): the ChArUco board on its page, the
 * virtual stereo camera (raw images with a little barrel distortion, rectified ones without),
 * board poses, the laser lines on the page, and canvas renderings: the live calibration
 * previews, view thumbnails, and the printables (previews and a real PDF at 100 % scale).
 *
 * Frames: the board frame has x right, y down, z away from the camera, in mm, origin at the
 * board's top-left corner. Poses map board to camera: X_c = R X_b + t (R row-major).
 */
import { LASERS, normalize, rng, type V3 } from "./geom";

export const IMG_W = 1280;
export const IMG_H = 800;
export const PREV_W = 640;
export const PREV_H = 400;
const PS = PREV_W / IMG_W; // preview pixels per full-resolution pixel

export type Lens = { fx: number; fy: number; cx: number; cy: number; k1: number; k2: number };
/** The virtual scanner's true raw lenses: stereo views are raw, so they show the distortion. */
export const LENS_RAW_L: Lens = { fx: 722.4, fy: 721.9, cx: 641.2, cy: 398.6, k1: -0.061, k2: 0.017 };
export const LENS_RAW_R: Lens = { fx: 721.6, fy: 721.2, cx: 638.4, cy: 401.1, k1: -0.058, k2: 0.016 };
/** The rectified left camera (lasers, IMU and check views). */
export const LENS_RECT: Lens = { fx: 720, fy: 720, cx: 639.5, cy: 399.5, k1: 0, k2: 0 };
export const BASELINE_MM = 60.04;

export const BOARD = { sx: 11, sy: 8, sq: 20, marker: 15, w: 220, h: 160 };
/** White paper around the printed board that counts as "on the page" (as in `ohs calibrate`). */
export const PAGE_MARGIN_MM = 12;
export const PAPER_MM: Record<"letter" | "a4", [number, number]> = { letter: [279.4, 215.9], a4: [297, 210] };

export type BoardPose = { R: number[]; t: V3 };

// ------------------------------------------------------------------ small linear algebra
const d2r = Math.PI / 180;
export const rotX = (deg: number): number[] => {
  const c = Math.cos(deg * d2r),
    s = Math.sin(deg * d2r);
  return [1, 0, 0, 0, c, -s, 0, s, c];
};
export const rotY = (deg: number): number[] => {
  const c = Math.cos(deg * d2r),
    s = Math.sin(deg * d2r);
  return [c, 0, s, 0, 1, 0, -s, 0, c];
};
export const rotZ = (deg: number): number[] => {
  const c = Math.cos(deg * d2r),
    s = Math.sin(deg * d2r);
  return [c, -s, 0, s, c, 0, 0, 0, 1];
};
export function mul3(A: number[], B: number[]): number[] {
  const C = new Array<number>(9);
  for (let i = 0; i < 3; i++)
    for (let j = 0; j < 3; j++) C[i * 3 + j] = A[i * 3] * B[j] + A[i * 3 + 1] * B[3 + j] + A[i * 3 + 2] * B[6 + j];
  return C;
}
export const transpose3 = (R: number[]) => [R[0], R[3], R[6], R[1], R[4], R[7], R[2], R[5], R[8]];
const rot = (R: number[], v: V3): V3 => [
  R[0] * v[0] + R[1] * v[1] + R[2] * v[2],
  R[3] * v[0] + R[4] * v[1] + R[5] * v[2],
  R[6] * v[0] + R[7] * v[1] + R[8] * v[2],
];
export const applyPose = (p: BoardPose, x: V3): V3 => {
  const r = rot(p.R, x);
  return [r[0] + p.t[0], r[1] + p.t[1], r[2] + p.t[2]];
};

/** Rotate the whole pose by a camera rotation (the camera turned by Rc: X'_c = Rc^T X_c). */
export function turnCamera(p: BoardPose, Rc: number[]): BoardPose {
  const Rt = transpose3(Rc);
  return { R: mul3(Rt, p.R), t: rot(Rt, p.t) };
}

/** Rotation angle between two rotation matrices, degrees. */
export function angleBetween(A: number[], B: number[]): number {
  const M = mul3(transpose3(A), B);
  const c = Math.min(1, Math.max(-1, (M[0] + M[4] + M[8] - 1) / 2));
  return Math.acos(c) / d2r;
}

// ------------------------------------------------------------------ camera
export function project(l: Lens, X: V3): [number, number] | null {
  if (X[2] < 1) return null;
  const x = X[0] / X[2],
    y = X[1] / X[2];
  const r2 = x * x + y * y;
  const d = 1 + l.k1 * r2 + l.k2 * r2 * r2;
  return [l.fx * x * d + l.cx, l.fy * y * d + l.cy];
}

/** The board centred on image point (u, v) at `dist` mm, tilted by yaw / pitch / roll (degrees). */
export function poseAt(l: Lens, u: number, v: number, dist: number, yaw: number, pitch: number, roll: number): BoardPose {
  const d = normalize([(u - l.cx) / l.fx, (v - l.cy) / l.fy, 1]);
  const c: V3 = [d[0] * dist, d[1] * dist, d[2] * dist];
  const R = mul3(rotY(yaw), mul3(rotX(pitch), rotZ(roll)));
  const half = rot(R, [BOARD.w / 2, BOARD.h / 2, 0]);
  return { R, t: [c[0] - half[0], c[1] - half[1], c[2] - half[2]] };
}

export const rightPose = (p: BoardPose): BoardPose => ({ R: p.R, t: [p.t[0] - BASELINE_MM, p.t[1], p.t[2]] });
export const boardCentre = (p: BoardPose): V3 => applyPose(p, [BOARD.w / 2, BOARD.h / 2, 0]);
export const distanceOf = (p: BoardPose) => Math.hypot(...boardCentre(p));
/** Angle between the board normal and the optical axis, degrees. */
export const tiltOf = (p: BoardPose) => Math.acos(Math.min(1, Math.abs(p.R[8]))) / d2r;

/** The ChArUco corners: the inner corners of the chessboard. */
export const CORNERS: V3[] = (() => {
  const out: V3[] = [];
  for (let j = 1; j < BOARD.sy; j++) for (let i = 1; i < BOARD.sx; i++) out.push([i * BOARD.sq, j * BOARD.sq, 0]);
  return out;
})();

/** The visible corners: flattened full-resolution uv, and a per-corner visibility flag. */
export function visibleCorners(l: Lens, p: BoardPose): { uv: number[]; seen: Uint8Array } {
  const uv: number[] = [];
  const seen = new Uint8Array(CORNERS.length);
  for (let k = 0; k < CORNERS.length; k++) {
    const q = project(l, applyPose(p, CORNERS[k]));
    if (!q || q[0] < 3 || q[1] < 3 || q[0] > IMG_W - 4 || q[1] > IMG_H - 4) continue;
    seen[k] = 1;
    uv.push(round1(q[0]), round1(q[1]));
  }
  return { uv, seen };
}

const round1 = (x: number) => Math.round(x * 10) / 10;

/** The 4 corners of a board-frame rectangle in the image (flattened, may lie outside it). */
export function rectUv(l: Lens, p: BoardPose, x0: number, y0: number, x1: number, y1: number): number[] {
  const out: number[] = [];
  for (const c of [
    [x0, y0],
    [x1, y0],
    [x1, y1],
    [x0, y1],
  ] as const) {
    const q = project(l, applyPose(p, [c[0], c[1], 0]));
    out.push(q ? round1(q[0]) : NaN, q ? round1(q[1]) : NaN);
  }
  return out;
}

export const outlineUv = (l: Lens, p: BoardPose) => rectUv(l, p, 0, 0, BOARD.w, BOARD.h);
export const pageUv = (l: Lens, p: BoardPose) =>
  rectUv(l, p, -PAGE_MARGIN_MM, -PAGE_MARGIN_MM, BOARD.w + PAGE_MARGIN_MM, BOARD.h + PAGE_MARGIN_MM);

// ------------------------------------------------------------------ laser lines on the page
type Seg = [[number, number], [number, number]];

/** The laser plane (rectified left camera frame) meets the board plane in a x + b y + c = 0 (board mm). */
function lineOnBoard(p: BoardPose, which: "vertical" | "horizontal"): [number, number, number] {
  const L = LASERS[which];
  const n = rot(transpose3(p.R), L.n);
  return [n[0], n[1], L.n[0] * p.t[0] + L.n[1] * p.t[1] + L.n[2] * p.t[2] + L.d];
}

/** The part of the line a x + b y + c = 0 inside a rectangle (Liang–Barsky on a parametrised line). */
function clipLine(a: number, b: number, c: number, x0: number, y0: number, x1: number, y1: number): Seg | null {
  // point on the line and its direction
  const n2 = a * a + b * b;
  if (n2 < 1e-12) return null;
  const px = (-a * c) / n2,
    py = (-b * c) / n2;
  const dx = -b,
    dy = a;
  let t0 = -1e6,
    t1 = 1e6;
  const clip = (p: number, q: number) => {
    if (Math.abs(p) < 1e-12) return q >= 0;
    const r = q / p;
    if (p < 0) {
      if (r > t1) return false;
      if (r > t0) t0 = r;
    } else {
      if (r < t0) return false;
      if (r < t1) t1 = r;
    }
    return true;
  };
  if (!clip(-dx, px - x0) || !clip(dx, x1 - px) || !clip(-dy, py - y0) || !clip(dy, y1 - py)) return null;
  if (t1 <= t0) return null;
  return [
    [px + dx * t0, py + dy * t0],
    [px + dx * t1, py + dy * t1],
  ];
}

export type LineInfo = {
  /** The line across the whole paper (board mm), for drawing. */
  paper: Seg | null;
  /** Detected points on the white margin: count (one per image row or column) and sampled uv. */
  count: number;
  uv: number[];
};

/** Both laser lines for a pose in the rectified left view. */
export function laserLines(p: BoardPose, paper: [number, number]): { vertical: LineInfo; horizontal: LineInfo } {
  const m = PAGE_MARGIN_MM;
  const px0 = -(paper[0] - BOARD.w) / 2,
    py0 = -(paper[1] - BOARD.h) / 2;
  const one = (which: "vertical" | "horizontal"): LineInfo => {
    const [a, b, c] = lineOnBoard(p, which);
    const full = clipLine(a, b, c, px0, py0, BOARD.w - px0, BOARD.h - py0);
    const page = clipLine(a, b, c, -m, -m, BOARD.w + m, BOARD.h + m);
    if (!page) return { paper: full, count: 0, uv: [] };
    const onBoard = clipLine(a, b, c, 0, 0, BOARD.w, BOARD.h);
    // The page segment minus the board segment: up to two pieces of white margin.
    const pieces: Seg[] = [];
    if (!onBoard) pieces.push(page);
    else {
      const [P0, P1] = page;
      const dir = [P1[0] - P0[0], P1[1] - P0[1]];
      const len2 = dir[0] * dir[0] + dir[1] * dir[1] || 1;
      const tOf = (q: [number, number]) => ((q[0] - P0[0]) * dir[0] + (q[1] - P0[1]) * dir[1]) / len2;
      const ta = Math.min(tOf(onBoard[0]), tOf(onBoard[1]));
      const tb = Math.max(tOf(onBoard[0]), tOf(onBoard[1]));
      const at = (t: number): [number, number] => [P0[0] + dir[0] * t, P0[1] + dir[1] * t];
      if (ta > 0.001) pieces.push([P0, at(ta)]);
      if (tb < 0.999) pieces.push([at(tb), P1]);
    }
    const seen = new Set<number>();
    const uv: number[] = [];
    let k = 0;
    for (const [A, B] of pieces) {
      const L = Math.hypot(B[0] - A[0], B[1] - A[1]);
      const n = Math.max(2, Math.ceil(L / 0.25));
      for (let i = 0; i <= n; i++) {
        const s = i / n;
        const q = project(LENS_RECT, applyPose(p, [A[0] + (B[0] - A[0]) * s, A[1] + (B[1] - A[1]) * s, 0]));
        if (!q || q[0] < 0 || q[1] < 0 || q[0] >= IMG_W || q[1] >= IMG_H) continue;
        const key = which === "vertical" ? Math.floor(q[1]) : Math.floor(q[0]);
        if (seen.has(key)) continue;
        seen.add(key);
        if (k++ % 6 === 0) uv.push(round1(q[0]), round1(q[1]));
      }
    }
    return { paper: full, count: seen.size, uv };
  };
  return { vertical: one("vertical"), horizontal: one("horizontal") };
}

/** Where a laser line crosses the image at a given board distance (fronto-parallel), full-res px. */
export function laserCross(dist: number): [number, number] {
  return [83455 / dist + 396.4, 611.8 - 53786 / dist];
}

// ------------------------------------------------------------------ marker patterns
function markerBits(): Uint8Array[] {
  const r = rng(4242);
  const out: Uint8Array[] = [];
  for (let k = 0; k < 60; k++) {
    const b = new Uint8Array(25);
    let ones = 0;
    for (let i = 0; i < 25; i++) {
      b[i] = r() < 0.5 ? 1 : 0;
      ones += b[i];
    }
    if (ones < 8) for (let i = 0; i < 25; i += 3) b[i] = 1;
    out.push(b);
  }
  return out;
}
const MARKERS = markerBits();

/**
 * Board-frame polygons of the printed pattern: the black chessboard squares and marker
 * squares, and the white bits inside the markers. Drawn with one fill per colour.
 */
type Quad = [number, number, number, number]; // x0, y0, x1, y1
const PATTERN: { black: Quad[]; white: Quad[] } = (() => {
  const black: Quad[] = [];
  const white: Quad[] = [];
  const { sx, sy, sq, marker } = BOARD;
  const cell = marker / 7;
  let m = 0;
  for (let j = 0; j < sy; j++)
    for (let i = 0; i < sx; i++) {
      const x = i * sq,
        y = j * sq;
      if ((i + j) % 2 === 0) {
        black.push([x, y, x + sq, y + sq]);
        continue;
      }
      const o = (sq - marker) / 2;
      black.push([x + o, y + o, x + o + marker, y + o + marker]);
      const bits = MARKERS[m++ % MARKERS.length];
      for (let bj = 0; bj < 5; bj++)
        for (let bi = 0; bi < 5; bi++) {
          if (!bits[bj * 5 + bi]) continue;
          const cx = x + o + (bi + 1) * cell,
            cy = y + o + (bj + 1) * cell;
          white.push([cx, cy, cx + cell, cy + cell]);
        }
    }
  return { black, white };
})();

// ------------------------------------------------------------------ canvases
function makeCanvas(w: number, h: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  return [c, c.getContext("2d")!];
}

export function toJpeg(c: HTMLCanvasElement, q = 0.78): Promise<Uint8Array> {
  return new Promise((resolve, reject) =>
    c.toBlob(
      (b) => {
        if (!b) return reject(new Error("JPEG encoding failed"));
        b.arrayBuffer().then((a) => resolve(new Uint8Array(a)), reject);
      },
      "image/jpeg",
      q,
    ),
  );
}

/** A dim workshop behind the board: rendered once. */
function workshopBackground(seed: number): HTMLCanvasElement {
  const [c, ctx] = makeCanvas(PREV_W, PREV_H);
  const g = ctx.createLinearGradient(0, 0, 0, PREV_H);
  g.addColorStop(0, "#34373b");
  g.addColorStop(0.55, "#222427");
  g.addColorStop(1, "#151618");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, PREV_W, PREV_H);
  const r = rng(seed);
  // shelves and a pegboard, softly out of focus
  ctx.filter = "blur(3px)";
  ctx.fillStyle = "rgba(255,255,255,0.05)";
  ctx.fillRect(0, 88, PREV_W, 7);
  ctx.fillRect(0, 214, PREV_W, 6);
  for (let i = 0; i < 9; i++) {
    ctx.fillStyle = `rgba(${150 + r() * 90},${120 + r() * 80},${80 + r() * 60},${0.07 + r() * 0.07})`;
    ctx.fillRect(r() * PREV_W, 40 + r() * 40, 18 + r() * 40, 40 + r() * 8);
  }
  const lamp = ctx.createRadialGradient(PREV_W * 0.78, 30, 4, PREV_W * 0.78, 30, 180);
  lamp.addColorStop(0, "rgba(255,240,215,0.30)");
  lamp.addColorStop(1, "rgba(255,240,215,0)");
  ctx.fillStyle = lamp;
  ctx.fillRect(0, 0, PREV_W, PREV_H);
  ctx.filter = "none";
  // sensor noise
  const img = ctx.getImageData(0, 0, PREV_W, PREV_H);
  for (let i = 0; i < img.data.length; i += 4) {
    const n = (r() - 0.5) * 9;
    img.data[i] += n;
    img.data[i + 1] += n;
    img.data[i + 2] += n;
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

export type DrawOpts = { lens: Lens; pose: BoardPose; paper: [number, number]; lasers: boolean; light?: number };

/** Draws one calibration camera view (640 x 400) of the board on its page. */
export class CalibRenderer {
  readonly canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private bg: HTMLCanvasElement;

  constructor(seed = 3) {
    [this.canvas, this.ctx] = makeCanvas(PREV_W, PREV_H);
    this.bg = workshopBackground(seed);
  }

  draw(o: DrawOpts): HTMLCanvasElement {
    const { ctx } = this;
    const { lens, pose } = o;
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    ctx.drawImage(this.bg, 0, 0);
    const P = (x: number, y: number): [number, number] | null => {
      const q = project(lens, applyPose(pose, [x, y, 0]));
      return q ? [q[0] * PS, q[1] * PS] : null;
    };
    const quadPath = (q: Quad) => {
      const a = P(q[0], q[1]),
        b = P(q[2], q[1]),
        c = P(q[2], q[3]),
        d = P(q[0], q[3]);
      if (!a || !b || !c || !d) return;
      ctx.moveTo(a[0], a[1]);
      ctx.lineTo(b[0], b[1]);
      ctx.lineTo(c[0], c[1]);
      ctx.lineTo(d[0], d[1]);
      ctx.closePath();
    };
    const px0 = -(o.paper[0] - BOARD.w) / 2,
      py0 = -(o.paper[1] - BOARD.h) / 2,
      px1 = BOARD.w - px0,
      py1 = BOARD.h - py0;
    // The paper, with subdivided edges so the lens distortion bends them.
    const paperPath = () => {
      ctx.beginPath();
      const N = 10;
      const edge = (x0: number, y0: number, x1: number, y1: number, first: boolean) => {
        for (let i = first ? 0 : 1; i <= N; i++) {
          const q = P(x0 + ((x1 - x0) * i) / N, y0 + ((y1 - y0) * i) / N);
          if (!q) continue;
          if (first && i === 0) ctx.moveTo(q[0], q[1]);
          else ctx.lineTo(q[0], q[1]);
        }
      };
      edge(px0, py0, px1, py0, true);
      edge(px1, py0, px1, py1, false);
      edge(px1, py1, px0, py1, false);
      edge(px0, py1, px0, py0, false);
      ctx.closePath();
    };
    // Lit by the lamp: brighter when the page faces it.
    const nz = Math.abs(pose.R[8]);
    const lit = Math.min(1, (o.light ?? 1) * (0.74 + 0.26 * nz + 0.08 * pose.R[5]));
    const paperRgb = [Math.round(236 * lit), Math.round(235 * lit), Math.round(229 * lit)];
    // soft shadow under the page (the hand-held glass)
    ctx.save();
    ctx.shadowColor = "rgba(0,0,0,0.55)";
    ctx.shadowBlur = 14;
    ctx.shadowOffsetY = 6;
    paperPath();
    ctx.fillStyle = `rgb(${paperRgb[0]},${paperRgb[1]},${paperRgb[2]})`;
    ctx.fill();
    ctx.restore();

    ctx.save();
    paperPath();
    ctx.clip();
    ctx.beginPath();
    for (const q of PATTERN.black) quadPath(q);
    ctx.fillStyle = "#17181a";
    ctx.fill();
    ctx.beginPath();
    for (const q of PATTERN.white) quadPath(q);
    ctx.fillStyle = `rgb(${paperRgb[0]},${paperRgb[1]},${paperRgb[2]})`;
    ctx.fill();
    // shading across the page
    const a = P(px0, py0),
      b = P(px1, py1);
    if (a && b) {
      const g = ctx.createLinearGradient(a[0], a[1], b[0], b[1]);
      g.addColorStop(0, "rgba(255,255,255,0.06)");
      g.addColorStop(1, "rgba(0,0,0,0.16)");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, PREV_W, PREV_H);
    }
    if (o.lasers) {
      const lines = laserLines(pose, o.paper);
      for (const L of [lines.vertical, lines.horizontal]) {
        if (!L.paper) continue;
        const [A, B] = L.paper;
        ctx.beginPath();
        const N = 24;
        for (let i = 0; i <= N; i++) {
          const q = P(A[0] + ((B[0] - A[0]) * i) / N, A[1] + ((B[1] - A[1]) * i) / N);
          if (!q) continue;
          if (i === 0) ctx.moveTo(q[0], q[1]);
          else ctx.lineTo(q[0], q[1]);
        }
        ctx.lineJoin = "round";
        ctx.strokeStyle = "rgba(255,40,24,0.28)";
        ctx.lineWidth = 6;
        ctx.stroke();
        ctx.strokeStyle = "#ff3322";
        ctx.lineWidth = 1.7;
        ctx.stroke();
        ctx.strokeStyle = "rgba(255,226,214,0.85)";
        ctx.lineWidth = 0.6;
        ctx.stroke();
      }
    }
    ctx.restore();

    // vignette
    const vg = ctx.createRadialGradient(PREV_W / 2, PREV_H / 2, PREV_H * 0.38, PREV_W / 2, PREV_H / 2, PREV_W * 0.66);
    vg.addColorStop(0, "rgba(0,0,0,0)");
    vg.addColorStop(1, "rgba(0,0,0,0.42)");
    ctx.fillStyle = vg;
    ctx.fillRect(0, 0, PREV_W, PREV_H);
    return this.canvas;
  }

  /** A small JPEG of the current picture, for the views strip. */
  thumbnail(): string {
    const [c, ctx] = makeCanvas(192, 120);
    ctx.drawImage(this.canvas, 0, 0, 192, 120);
    return c.toDataURL("image/jpeg", 0.74);
  }
}

// ------------------------------------------------------------------ printables
export type Sheet = { canvas: HTMLCanvasElement; wMm: number; hMm: number };

function sheet(paper: "letter" | "a4", pxPerMm: number): [HTMLCanvasElement, CanvasRenderingContext2D, number, number] {
  const [w, h] = PAPER_MM[paper];
  const [c, ctx] = makeCanvas(Math.round(w * pxPerMm), Math.round(h * pxPerMm));
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, c.width, c.height);
  ctx.scale(pxPerMm, pxPerMm);
  return [c, ctx, w, h];
}

/** The ChArUco board page (landscape) with its 100 mm scale bar. */
export function boardSheet(paper: "letter" | "a4", pxPerMm: number): Sheet {
  const [c, ctx, w, h] = sheet(paper, pxPerMm);
  const ox = (w - BOARD.w) / 2,
    oy = (h - BOARD.h) / 2 - 4;
  ctx.fillStyle = "#000";
  for (const q of PATTERN.black) ctx.fillRect(ox + q[0], oy + q[1], q[2] - q[0], q[3] - q[1]);
  ctx.fillStyle = "#fff";
  for (const q of PATTERN.white) ctx.fillRect(ox + q[0], oy + q[1], q[2] - q[0], q[3] - q[1]);
  // 100 mm scale bar
  const by = oy + BOARD.h + 14;
  const bx = (w - 100) / 2;
  ctx.fillStyle = "#000";
  ctx.fillRect(bx, by, 100, 1.2);
  ctx.lineWidth = 0.3;
  ctx.strokeStyle = "#000";
  for (let i = 0; i <= 10; i++) {
    ctx.beginPath();
    ctx.moveTo(bx + i * 10, by - (i % 5 === 0 ? 3 : 1.6));
    ctx.lineTo(bx + i * 10, by + 1.2);
    ctx.stroke();
  }
  ctx.font = "2.6px sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("100 mm: measure with calipers. Print at 100 % (actual size), never “fit to page”.", w / 2, by + 5.5);
  ctx.font = "2.2px sans-serif";
  ctx.fillStyle = "#555";
  ctx.fillText("OpenHandScan ChArUco 11 × 8 · 20 mm squares · 15 mm markers · DICT_5X5_100", w / 2, oy - 4);
  return { canvas: c, wMm: w, hMm: h };
}

/** 48 AprilTag stickers with dashed cut lines. */
export function tagsSheet(paper: "letter" | "a4", pxPerMm: number): Sheet {
  const [c, ctx, w, h] = sheet(paper, pxPerMm);
  const cols = 8,
    rows = 6,
    cell = 30,
    tag = 20;
  const ox = (w - cols * cell) / 2,
    oy = (h - rows * cell) / 2;
  const r = rng(36);
  ctx.setLineDash([1.2, 1.2]);
  ctx.lineWidth = 0.2;
  ctx.strokeStyle = "#999";
  for (let i = 0; i <= cols; i++) {
    ctx.beginPath();
    ctx.moveTo(ox + i * cell, oy);
    ctx.lineTo(ox + i * cell, oy + rows * cell);
    ctx.stroke();
  }
  for (let j = 0; j <= rows; j++) {
    ctx.beginPath();
    ctx.moveTo(ox, oy + j * cell);
    ctx.lineTo(ox + cols * cell, oy + j * cell);
    ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.font = "2px sans-serif";
  ctx.textAlign = "center";
  for (let j = 0; j < rows; j++)
    for (let i = 0; i < cols; i++) {
      const x = ox + i * cell + (cell - tag) / 2,
        y = oy + j * cell + (cell - tag) / 2 - 1.5;
      ctx.fillStyle = "#000";
      ctx.fillRect(x, y, tag, tag);
      const b = tag / 8;
      ctx.fillStyle = "#fff";
      for (let v = 0; v < 6; v++) for (let u = 0; u < 6; u++) if (r() < 0.5) ctx.fillRect(x + (u + 1) * b, y + (v + 1) * b, b, b);
      ctx.fillStyle = "#666";
      ctx.fillText(`tag36h11 · ${j * cols + i}`, ox + i * cell + cell / 2, y + tag + 3.4);
    }
  return { canvas: c, wMm: w, hMm: h };
}

/** The LED projector mask sheet: 48 copies of the random-dot mask. */
export function maskSheet(paper: "letter" | "a4", pxPerMm: number): Sheet {
  const [c, ctx, w, h] = sheet(paper, pxPerMm);
  const cols = 8,
    rows = 6,
    cell = 26,
    m = 16;
  const ox = (w - cols * cell) / 2,
    oy = (h - rows * cell) / 2;
  const dots: [number, number][] = [];
  const r = rng(777);
  for (let k = 0; k < 900; k++) dots.push([r() * m, r() * m]);
  for (let j = 0; j < rows; j++)
    for (let i = 0; i < cols; i++) {
      const x = ox + i * cell + (cell - m) / 2,
        y = oy + j * cell + (cell - m) / 2;
      ctx.fillStyle = "#000";
      ctx.fillRect(x - 0.6, y - 0.6, m + 1.2, m + 1.2);
      ctx.fillStyle = "#fff";
      const dot = Math.max(0.12, 0.8 / pxPerMm); // at least about a pixel, so previews show the dots
      for (const [dx, dy] of dots) ctx.fillRect(x + dx, y + dy, dot, dot);
    }
  ctx.font = "2.4px sans-serif";
  ctx.fillStyle = "#555";
  ctx.textAlign = "center";
  ctx.fillText("LED projector mask (option B) · print at 100 % on laser transparency film, 1200 dpi · cut out the cleanest copy", w / 2, oy - 5);
  return { canvas: c, wMm: w, hMm: h };
}

export function previewDataUrl(s: Sheet, maxW = 560): string {
  const k = maxW / s.canvas.width;
  const [c, ctx] = makeCanvas(maxW, Math.round(s.canvas.height * k));
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(s.canvas, 0, 0, c.width, c.height);
  return c.toDataURL("image/png");
}

/** A one-page PDF showing the sheet at its true size (so "actual size" prints it at 100 %). */
export async function sheetPdf(s: Sheet): Promise<ArrayBuffer> {
  const jpeg = await toJpeg(s.canvas, 0.9);
  const enc = new TextEncoder();
  const wPt = ((s.wMm / 25.4) * 72).toFixed(2),
    hPt = ((s.hMm / 25.4) * 72).toFixed(2);
  const content = enc.encode(`q ${wPt} 0 0 ${hPt} 0 0 cm /Im0 Do Q\n`);
  const parts: Uint8Array[] = [];
  const offsets: number[] = [];
  let size = 0;
  const push = (b: Uint8Array | string) => {
    const u = typeof b === "string" ? enc.encode(b) : b;
    parts.push(u);
    size += u.length;
  };
  const obj = (n: number, body: string) => {
    offsets[n] = size;
    push(`${n} 0 obj\n${body}\nendobj\n`);
  };
  push("%PDF-1.4\n%âãÏÓ\n");
  obj(1, "<< /Type /Catalog /Pages 2 0 R >>");
  obj(2, "<< /Type /Pages /Kids [3 0 R] /Count 1 >>");
  obj(3, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${wPt} ${hPt}] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>`);
  offsets[4] = size;
  push(
    `4 0 obj\n<< /Type /XObject /Subtype /Image /Width ${s.canvas.width} /Height ${s.canvas.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpeg.length} >>\nstream\n`,
  );
  push(jpeg);
  push("\nendstream\nendobj\n");
  offsets[5] = size;
  push(`5 0 obj\n<< /Length ${content.length} >>\nstream\n`);
  push(content);
  push("endstream\nendobj\n");
  const xref = size;
  let x = "xref\n0 6\n0000000000 65535 f \n";
  for (let n = 1; n <= 5; n++) x += `${String(offsets[n]).padStart(10, "0")} 00000 n \n`;
  push(x);
  push(`trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
  const out = new Uint8Array(size);
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out.buffer;
}
