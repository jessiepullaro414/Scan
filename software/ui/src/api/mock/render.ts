/**
 * Mock camera previews, ray-cast from the same scene the mock meshes come from: the
 * rectified left image (headlight shading, the dot pattern, both laser stripes bending over
 * the parts) and a turbo-colourised depth map. Both are encoded as JPEG like the engine's.
 */
import { castRay, K_FULL, LASERS, rotate, dot, type Pose, type V3 } from "./geom";

const GW = 160;
const GH = 100;
const S = GW / K_FULL.w; // grid scale vs 1280x800
const FX = K_FULL.fx * S;
const FY = K_FULL.fy * S;
const CX = K_FULL.cx * S;
const CY = K_FULL.cy * S;

export const LEFT_W = 640;
export const LEFT_H = 400;
export const DEPTH_W = 320;
export const DEPTH_H = 200;

function turbo(x: number): [number, number, number] {
  x = Math.min(1, Math.max(0, x));
  const r = 0.13572138 + x * (4.6153926 + x * (-42.66032258 + x * (132.13108234 + x * (-152.94239396 + x * 59.28637943))));
  const g = 0.09140261 + x * (2.19418839 + x * (4.84296658 + x * (-14.18503333 + x * (4.27729857 + x * 2.82956604))));
  const b = 0.1066733 + x * (12.64194608 + x * (-60.58204836 + x * (110.36276771 + x * (-89.90310912 + x * 27.34824973))));
  return [Math.min(255, Math.max(0, r * 255)), Math.min(255, Math.max(0, g * 255)), Math.min(255, Math.max(0, b * 255))];
}

function canvas(w: number, h: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d")!;
  return [c, ctx];
}

function toJpeg(c: HTMLCanvasElement, q: number): Promise<Uint8Array> {
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

export class PreviewRenderer {
  private dirs = new Float32Array(GW * GH * 3);
  private depth = new Float32Array(GW * GH);
  private shade = new Float32Array(GW * GH);
  private pts = new Float32Array(GW * GH * 3); // hit points in the camera frame
  private small: [HTMLCanvasElement, CanvasRenderingContext2D];
  private smallImg: ImageData;
  private left: [HTMLCanvasElement, CanvasRenderingContext2D];
  private depthOut: [HTMLCanvasElement, CanvasRenderingContext2D];
  private dots: HTMLCanvasElement;

  constructor() {
    for (let v = 0; v < GH; v++)
      for (let u = 0; u < GW; u++) {
        const x = (u + 0.5 - CX) / FX,
          y = (v + 0.5 - CY) / FY;
        const l = Math.hypot(x, y, 1);
        const k = (v * GW + u) * 3;
        this.dirs[k] = x / l;
        this.dirs[k + 1] = y / l;
        this.dirs[k + 2] = 1 / l;
      }
    this.small = canvas(GW, GH);
    this.smallImg = this.small[1].createImageData(GW, GH);
    this.left = canvas(LEFT_W, LEFT_H);
    this.depthOut = canvas(DEPTH_W, DEPTH_H);
    // The projector's random dots are fixed relative to the scanner, so in the image they stay put.
    const [dc, dctx] = canvas(LEFT_W, LEFT_H);
    let s = 12345;
    const rnd = () => ((s = (s * 1103515245 + 12345) >>> 0) / 4294967296);
    for (let i = 0; i < 5200; i++) {
      const a = 0.25 + rnd() * 0.75;
      dctx.fillStyle = `rgba(255,255,255,${a.toFixed(2)})`;
      const r = 0.6 + rnd() * 0.9;
      dctx.beginPath();
      dctx.arc(rnd() * LEFT_W, rnd() * LEFT_H, r, 0, Math.PI * 2);
      dctx.fill();
    }
    this.dots = dc;
  }

  /** Ray-cast the scene for a camera pose (scene frame); returns the distance at the image centre. */
  render(pose: Pose, opts: { dots: number; lines: number }): number | null {
    const { dirs, depth, shade, pts } = this;
    const o = pose.t;
    for (let i = 0; i < GW * GH; i++) {
      const dc: V3 = [dirs[i * 3], dirs[i * 3 + 1], dirs[i * 3 + 2]];
      const ds = rotate(pose.R, dc);
      const h = castRay(o, ds, 900);
      if (!h.id) {
        depth[i] = 0;
        shade[i] = 0.035;
        continue;
      }
      const z = h.t * dc[2];
      depth[i] = z;
      pts[i * 3] = dc[0] * h.t;
      pts[i * 3 + 1] = dc[1] * h.t;
      pts[i * 3 + 2] = z;
      const lam = Math.max(0, -dot(h.n, ds));
      const px = o[0] + ds[0] * h.t,
        pz = o[2] + ds[2] * h.t;
      const albedo =
        h.id === 1
          ? 0.36 + 0.05 * Math.sin(px * 0.21 + Math.sin(pz * 0.05) * 3) + 0.03 * Math.sin(pz * 0.9)
          : h.id === 2
            ? 0.7
            : 0.84;
      const fall = 1 / (1 + Math.pow(h.t / 520, 2));
      shade[i] = albedo * (0.16 + 0.84 * lam) * (0.55 + 0.75 * fall);
    }

    // left image
    const img = this.smallImg.data;
    for (let i = 0; i < GW * GH; i++) {
      const g = Math.min(255, Math.max(0, shade[i] * 255));
      img[i * 4] = img[i * 4 + 1] = img[i * 4 + 2] = g;
      img[i * 4 + 3] = 255;
    }
    const [sc, sctx] = this.small;
    sctx.putImageData(this.smallImg, 0, 0);
    const [, lctx] = this.left;
    lctx.imageSmoothingEnabled = true;
    lctx.imageSmoothingQuality = "high";
    lctx.globalCompositeOperation = "source-over";
    lctx.globalAlpha = 1;
    lctx.drawImage(sc, 0, 0, LEFT_W, LEFT_H);
    if (opts.dots > 0) {
      lctx.globalCompositeOperation = "lighter";
      lctx.globalAlpha = 0.32 * Math.min(1, opts.dots / 100);
      lctx.drawImage(this.dots, 0, 0);
      lctx.globalAlpha = 1;
      lctx.globalCompositeOperation = "source-over";
    }
    if (opts.lines > 0) {
      this.drawLaser(lctx, "vertical", "#ff4040");
      this.drawLaser(lctx, "horizontal", "#38e0ff");
    }
    // vignette
    const vg = lctx.createRadialGradient(LEFT_W / 2, LEFT_H / 2, LEFT_H * 0.35, LEFT_W / 2, LEFT_H / 2, LEFT_W * 0.62);
    vg.addColorStop(0, "rgba(0,0,0,0)");
    vg.addColorStop(1, "rgba(0,0,0,0.45)");
    lctx.fillStyle = vg;
    lctx.fillRect(0, 0, LEFT_W, LEFT_H);

    // depth map: near = warm, far = cool, invalid = black
    for (let i = 0; i < GW * GH; i++) {
      const z = depth[i];
      if (z < 150 || z > 450) {
        img[i * 4] = img[i * 4 + 1] = img[i * 4 + 2] = 0;
      } else {
        const [r, g, b] = turbo(1 - (z - 150) / 300);
        img[i * 4] = r;
        img[i * 4 + 1] = g;
        img[i * 4 + 2] = b;
      }
      img[i * 4 + 3] = 255;
    }
    sctx.putImageData(this.smallImg, 0, 0);
    const [, dctx] = this.depthOut;
    dctx.imageSmoothingEnabled = true;
    dctx.drawImage(sc, 0, 0, DEPTH_W, DEPTH_H);

    // distance: median depth in the central patch
    const vals: number[] = [];
    for (let v = GH / 2 - 5; v < GH / 2 + 5; v++)
      for (let u = GW / 2 - 6; u < GW / 2 + 6; u++) {
        const z = depth[v * GW + u];
        if (z > 0) vals.push(z);
      }
    if (vals.length < 30) return null;
    vals.sort((a, b) => a - b);
    return vals[vals.length >> 1];
  }

  private drawLaser(ctx: CanvasRenderingContext2D, which: "vertical" | "horizontal", color: string) {
    const L = LASERS[which];
    const { pts, depth } = this;
    const s = (i: number) => L.n[0] * pts[i * 3] + L.n[1] * pts[i * 3 + 1] + L.n[2] * pts[i * 3 + 2] + L.d;
    const segs: Array<Array<[number, number]>> = [];
    let cur: Array<[number, number]> = [];
    const outer = which === "vertical" ? GH : GW;
    const inner = which === "vertical" ? GW : GH;
    for (let a = 0; a < outer; a++) {
      let found: number | null = null;
      for (let b = 0; b < inner - 1; b++) {
        const i0 = which === "vertical" ? a * GW + b : b * GW + a;
        const i1 = which === "vertical" ? a * GW + b + 1 : (b + 1) * GW + a;
        if (!(depth[i0] > 0 && depth[i1] > 0)) continue;
        if (Math.abs(depth[i0] - depth[i1]) > 25) continue; // occlusion edge
        const s0 = s(i0),
          s1 = s(i1);
        if ((s0 <= 0 && s1 > 0) || (s0 > 0 && s1 <= 0)) {
          found = b + s0 / (s0 - s1);
          break;
        }
      }
      if (found === null) {
        if (cur.length > 1) segs.push(cur);
        cur = [];
        continue;
      }
      const sx = LEFT_W / GW,
        sy = LEFT_H / GH;
      cur.push(which === "vertical" ? [(found + 0.5) * sx, (a + 0.5) * sy] : [(a + 0.5) * sx, (found + 0.5) * sy]);
    }
    if (cur.length > 1) segs.push(cur);
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.2;
    ctx.lineJoin = "round";
    ctx.shadowColor = color;
    ctx.shadowBlur = 7;
    for (const seg of segs) {
      ctx.beginPath();
      ctx.moveTo(seg[0][0], seg[0][1]);
      for (let k = 1; k < seg.length; k++) ctx.lineTo(seg[k][0], seg[k][1]);
      ctx.stroke();
    }
    ctx.restore();
  }

  async encode(): Promise<{ left: Uint8Array; depth: Uint8Array }> {
    const [left, depth] = await Promise.all([toJpeg(this.left[0], 0.74), toJpeg(this.depthOut[0], 0.8)]);
    return { left, depth };
  }
}
