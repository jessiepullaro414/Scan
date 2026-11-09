/**
 * Canvas overlays on the calibration previews, drawn in a requestAnimationFrame loop from the
 * latest CalibLive (no React render per message): the coverage heatmap, the board and page
 * outlines, the detected corners, the laser line points, the hint's target region with an
 * arrow, and the steady ring. CalibLive coordinates are full-resolution pixels of the image the
 * preview shows; they are scaled by `cssWidth / image_size[0]` (the preview box has the same
 * aspect ratio as the image).
 *
 * The colours are fixed and bright: they sit on a camera image, whatever the app theme.
 */
import type { CalibLive } from "../../../api/types";

export const OVERLAY = {
  ok: "#46d369",
  warn: "#ffb84d",
  accent: "#6aa6ff",
  white: "#ffffff",
  vertical: "#ff5a4f",
  horizontal: "#3ad3ff",
  shadow: "rgba(0,0,0,0.55)",
};

export type OverlayAnim = {
  /** The ring's displayed fill, eased towards `steady`. */
  steady: number;
  /** Seconds since the last capture (for the ring's burst). */
  sinceCapture: number;
};

type Pt = [number, number];

function centroid(xy: number[]): Pt | null {
  const n = Math.floor(xy.length / 2);
  if (!n) return null;
  let x = 0,
    y = 0,
    k = 0;
  for (let i = 0; i < n; i++) {
    const a = xy[i * 2],
      b = xy[i * 2 + 1];
    if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
    x += a;
    y += b;
    k++;
  }
  return k ? [x / k, y / k] : null;
}

/** Convex hull (monotone chain) of flattened points: the right camera's board outline. */
function hull(xy: number[]): number[] {
  const pts: Pt[] = [];
  for (let i = 0; i + 1 < xy.length; i += 2) pts.push([xy[i], xy[i + 1]]);
  if (pts.length < 3) return [];
  pts.sort((a, b) => (a[0] === b[0] ? a[1] - b[1] : a[0] - b[0]));
  const cross = (o: Pt, a: Pt, b: Pt) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: Pt[] = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper: Pt[] = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  return [...lower.slice(0, -1), ...upper.slice(0, -1)].flat();
}

function polygon(ctx: CanvasRenderingContext2D, xy: number[], s: number): boolean {
  let started = false;
  for (let i = 0; i + 1 < xy.length; i += 2) {
    const x = xy[i] * s,
      y = xy[i + 1] * s;
    if (!Number.isFinite(x) || !Number.isFinite(y)) return false;
    if (!started) {
      ctx.moveTo(x, y);
      started = true;
    } else ctx.lineTo(x, y);
  }
  if (started) ctx.closePath();
  return started;
}

function dots(ctx: CanvasRenderingContext2D, xy: number[], s: number, r: number, fill: string) {
  if (!xy.length) return;
  ctx.beginPath();
  for (let i = 0; i + 1 < xy.length; i += 2) {
    const x = xy[i] * s,
      y = xy[i + 1] * s;
    ctx.moveTo(x + r, y);
    ctx.arc(x, y, r, 0, Math.PI * 2);
  }
  // Outline first, then the fill on top: along a dense laser line the dots overlap, and outlines
  // drawn last would cover the neighbours' fill and turn the whole line black.
  ctx.strokeStyle = "rgba(0,0,0,0.6)";
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.fillStyle = fill;
  ctx.fill();
}

function arrow(ctx: CanvasRenderingContext2D, a: Pt, b: Pt, color: string, alpha: number) {
  const dx = b[0] - a[0],
    dy = b[1] - a[1];
  const L = Math.hypot(dx, dy);
  if (L < 40) return;
  const ux = dx / L,
    uy = dy / L;
  const s0: Pt = [a[0] + ux * 34, a[1] + uy * 34];
  const s1: Pt = [b[0] - ux * 14, b[1] - uy * 14];
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 3.5;
  ctx.lineCap = "round";
  ctx.shadowColor = OVERLAY.shadow;
  ctx.shadowBlur = 6;
  ctx.setLineDash([10, 8]);
  ctx.beginPath();
  ctx.moveTo(s0[0], s0[1]);
  ctx.lineTo(s1[0] - ux * 10, s1[1] - uy * 10);
  ctx.stroke();
  ctx.setLineDash([]);
  const h = 14;
  ctx.beginPath();
  ctx.moveTo(s1[0], s1[1]);
  ctx.lineTo(s1[0] - ux * h - uy * h * 0.62, s1[1] - uy * h + ux * h * 0.62);
  ctx.lineTo(s1[0] - ux * h + uy * h * 0.62, s1[1] - uy * h - ux * h * 0.62);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

export type DrawOptions = {
  camera: "left" | "right";
  /** The primary view gets the hint, the arrow and the steady ring. */
  primary: boolean;
  heatmap: boolean;
  /** Seconds, for the animations. */
  t: number;
};

export function drawOverlay(ctx: CanvasRenderingContext2D, W: number, H: number, live: CalibLive | null, anim: OverlayAnim, o: DrawOptions): void {
  ctx.clearRect(0, 0, W, H);
  if (!live || !live.image_size?.[0]) return;
  const s = W / live.image_size[0];
  const left = o.camera === "left";

  // 1. coverage heatmap: views per cell
  const cov = live.coverage;
  const cells = cov ? (left ? cov.left : cov.right) : null;
  if (o.heatmap && cov && cells && cov.cols > 0 && cov.rows > 0) {
    const cw = W / cov.cols,
      ch = H / cov.rows;
    for (let j = 0; j < cov.rows; j++)
      for (let i = 0; i < cov.cols; i++) {
        const n = cells[j * cov.cols + i] ?? 0;
        if (n <= 0) continue;
        ctx.fillStyle = `rgba(70,211,105,${Math.min(0.42, 0.14 + 0.08 * n).toFixed(3)})`;
        ctx.fillRect(i * cw, j * ch, cw, ch);
      }
    ctx.beginPath();
    for (let i = 1; i < cov.cols; i++) {
      const x = Math.round(i * cw) + 0.5;
      ctx.moveTo(x, 0);
      ctx.lineTo(x, H);
    }
    for (let j = 1; j < cov.rows; j++) {
      const y = Math.round(j * ch) + 0.5;
      ctx.moveTo(0, y);
      ctx.lineTo(W, y);
    }
    ctx.strokeStyle = "rgba(255,255,255,0.09)";
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  const corners = (left ? live.left?.corners : live.right?.corners) ?? [];
  const tone = live.ready ? OVERLAY.ok : OVERLAY.warn;

  // 2. the page outline and laser points (lasers / check, rectified left image)
  if (left && live.page && live.page.length >= 8) {
    ctx.save();
    ctx.beginPath();
    if (polygon(ctx, live.page, s)) {
      ctx.setLineDash([6, 5]);
      ctx.strokeStyle = "rgba(255,255,255,0.75)";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
    ctx.restore();
  }

  // 3. the board outline
  const outline = left ? live.outline : corners.length >= 6 ? hull(corners) : null;
  if (outline && outline.length >= 6) {
    ctx.save();
    ctx.beginPath();
    if (polygon(ctx, outline, s)) {
      ctx.fillStyle = live.ready ? "rgba(70,211,105,0.08)" : "rgba(255,184,77,0.06)";
      ctx.fill();
      ctx.shadowColor = OVERLAY.shadow;
      ctx.shadowBlur = 4;
      ctx.strokeStyle = tone;
      ctx.lineWidth = 2;
      ctx.lineJoin = "round";
      ctx.stroke();
    }
    ctx.restore();
  }

  // 4. the detected corners
  dots(ctx, corners, s, Math.max(1.6, Math.min(2.6, W / 300)), tone);

  // 5. laser line points, one colour per line
  if (left && live.lines) {
    dots(ctx, live.lines.vertical_uv ?? [], s, 2, OVERLAY.vertical);
    dots(ctx, live.lines.horizontal_uv ?? [], s, 2, OVERLAY.horizontal);
  }

  if (!o.primary) return;
  const c = (live.outline && live.outline.length >= 8 ? centroid(live.outline) : null) ?? centroid(corners);
  const cPx: Pt | null = c ? [c[0] * s, c[1] * s] : null;

  // 6. the hint's target region, with an arrow from the board
  const reg = live.hint?.region;
  if (reg && reg.length === 4) {
    const x0 = reg[0] * W,
      y0 = reg[1] * H,
      x1 = reg[2] * W,
      y1 = reg[3] * H;
    const inset = 6;
    const pulse = 0.65 + 0.35 * Math.sin(o.t * 4);
    ctx.save();
    ctx.fillStyle = `rgba(106,166,255,${(0.07 + 0.05 * pulse).toFixed(3)})`;
    ctx.fillRect(x0 + inset, y0 + inset, x1 - x0 - inset * 2, y1 - y0 - inset * 2);
    ctx.setLineDash([9, 7]);
    ctx.lineDashOffset = -o.t * 26;
    ctx.strokeStyle = OVERLAY.accent;
    ctx.globalAlpha = 0.6 + 0.4 * pulse;
    ctx.lineWidth = 2.5;
    ctx.shadowColor = OVERLAY.shadow;
    ctx.shadowBlur = 5;
    ctx.strokeRect(x0 + inset, y0 + inset, x1 - x0 - inset * 2, y1 - y0 - inset * 2);
    ctx.restore();
    const target: Pt = [(x0 + x1) / 2, (y0 + y1) / 2];
    const inside = cPx && cPx[0] > x0 && cPx[0] < x1 && cPx[1] > y0 && cPx[1] < y1;
    if (cPx && !inside) arrow(ctx, cPx, target, OVERLAY.accent, 0.75 + 0.25 * pulse);
  }

  // 7. the steady ring: fills while the board is held still
  if (cPx && live.board_found && (live.ready || anim.sinceCapture < 0.6)) {
    const R = 22;
    const v = Math.max(0, Math.min(1, anim.steady));
    const burst = anim.sinceCapture < 0.6 ? anim.sinceCapture / 0.6 : 1;
    ctx.save();
    ctx.shadowColor = OVERLAY.shadow;
    ctx.shadowBlur = 6;
    ctx.beginPath();
    ctx.arc(cPx[0], cPx[1], R, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(0,0,0,0.35)";
    ctx.fill();
    ctx.lineWidth = 5;
    ctx.strokeStyle = "rgba(255,255,255,0.25)";
    ctx.stroke();
    ctx.shadowBlur = 0;
    if (burst < 1) {
      // just captured: a green ring that expands and fades
      ctx.beginPath();
      ctx.arc(cPx[0], cPx[1], R + burst * 26, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(70,211,105,${(1 - burst).toFixed(3)})`;
      ctx.lineWidth = 5 * (1 - burst) + 1;
      ctx.stroke();
    }
    const full = v >= 0.999 || burst < 1;
    ctx.beginPath();
    ctx.arc(cPx[0], cPx[1], R, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * (full ? 1 : v));
    ctx.strokeStyle = full ? OVERLAY.ok : OVERLAY.white;
    ctx.lineWidth = 5;
    ctx.lineCap = "round";
    ctx.stroke();
    if (full) {
      ctx.beginPath();
      ctx.moveTo(cPx[0] - 8, cPx[1] + 0.5);
      ctx.lineTo(cPx[0] - 2.5, cPx[1] + 6);
      ctx.lineTo(cPx[0] + 8.5, cPx[1] - 6);
      ctx.strokeStyle = OVERLAY.ok;
      ctx.lineWidth = 3.5;
      ctx.lineJoin = "round";
      ctx.stroke();
    } else {
      ctx.fillStyle = "#fff";
      ctx.font = "600 11px ui-monospace, Consolas, monospace";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(`${Math.round(v * 100)}`, cPx[0], cPx[1] + 0.5);
    }
    ctx.restore();
  }
}
