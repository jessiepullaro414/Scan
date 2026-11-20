/**
 * The report's request as the viewer builds it (FEATURE_PLAN.md I3; APP_API.md "The report"): the views of the model
 * the user added (kept in the page's memory only, each a PNG made from the 3D view), their limits, and the request's
 * body: the tolerance for the Check answer, numbers in the part's frame, the seam shown, the pictures. The limits are
 * the engine's (`ohs/server/report.py`): a view over them is refused here first, in words. Plain functions, tested on
 * their own (`report.test.ts`).
 */
import type { ReportPicture, ReportRequest } from "../api/types";

/** At most this many views in a report (the engine's MAX_PICTURES). */
export const MAX_VIEWS = 6;
/** Each view's PNG at most this many bytes (the engine's MAX_PICTURE_BYTES: 3 MB). */
export const MAX_VIEW_BYTES = 3 * 2 ** 20;
/** All of them at most this many bytes (the engine's MAX_TOTAL_BYTES: 12 MB). */
export const MAX_TOTAL_BYTES = 12 * 2 ** 20;
/** A view is made at most this many pixels across (its longer side): sharp on an A4 page, about 0.5 to 2 MB. */
export const MAX_SIDE_PX = 1600;

/** A view of the model added to the report. */
export type ReportView = {
  id: string;
  title: string;
  caption: string;
  /** The PNG's bytes in base64 (no "data:" prefix). */
  data: string;
  bytes: number;
  width: number;
  height: number;
};

/** The limits in words, for the panel. */
export const LIMITS_TEXT = `Up to ${MAX_VIEWS} views, each at most ${MAX_VIEW_BYTES / 2 ** 20}\u202fMB (made at most ${MAX_SIDE_PX} pixels across).`;

/** How many bytes a base64 text holds. */
export function base64Bytes(b64: string): number {
  const n = b64.replace(/[^A-Za-z0-9+/=]/g, "").length;
  const pad = b64.endsWith("==") ? 2 : b64.endsWith("=") ? 1 : 0;
  return Math.max(0, Math.floor((n * 3) / 4) - pad);
}

/** A data URL's base64 part ("data:image/png;base64,AAA" -> "AAA"); null for anything else. */
export function dataUrlBase64(url: string): string | null {
  const m = /^data:image\/png;base64,(.*)$/s.exec(url);
  return m ? m[1] : null;
}

/** The size a view is made at: the canvas's own, scaled down so its longer side is at most `max` pixels. */
export function viewSize(width: number, height: number, max = MAX_SIDE_PX): { width: number; height: number } {
  const w = Math.max(1, Math.round(width)),
    h = Math.max(1, Math.round(height));
  const s = Math.min(1, max / Math.max(w, h));
  return { width: Math.max(1, Math.round(w * s)), height: Math.max(1, Math.round(h * s)) };
}

/** Why one more view of `bytes` cannot be added to `views` (null: it can). */
export function cannotAdd(views: ReportView[], bytes: number): string | null {
  if (views.length >= MAX_VIEWS) return `A report takes at most ${MAX_VIEWS} views: remove one first.`;
  if (bytes > MAX_VIEW_BYTES) return `This view is ${(bytes / 2 ** 20).toFixed(1)}\u202fMB, more than the ${MAX_VIEW_BYTES / 2 ** 20}\u202fMB a view may be.`;
  const total = views.reduce((s, v) => s + v.bytes, 0) + bytes;
  if (total > MAX_TOTAL_BYTES) return `The views would come to ${(total / 2 ** 20).toFixed(1)}\u202fMB, more than the ${MAX_TOTAL_BYTES / 2 ** 20}\u202fMB a report takes: remove one first.`;
  return null;
}

/** The next view's default title: "View N", one more than the highest such title and than the number of views. */
export function nextTitle(views: ReportView[]): string {
  let top = views.length;
  for (const v of views) {
    const m = /^View (\d+)$/.exec(v.title.trim());
    if (m) top = Math.max(top, Number(m[1]));
  }
  return `View ${top + 1}`;
}

/** The list with one more view (a new list), or the reason it cannot take it. */
export function addView(views: ReportView[], v: Omit<ReportView, "bytes" | "title"> & { title?: string }): { views: ReportView[]; error: string | null } {
  const bytes = base64Bytes(v.data);
  const why = cannotAdd(views, bytes);
  if (why) return { views, error: why };
  return { views: [...views, { ...v, title: v.title?.trim() || nextTitle(views), bytes }], error: null };
}

export function removeView(views: ReportView[], id: string): ReportView[] {
  return views.filter((v) => v.id !== id);
}

/** What the report is asked to hold beyond the scan's own data. */
export type ReportOptions = {
  /** The Check answer at this tolerance (± mm); null leaves it out. */
  tolerance: number | null;
  /** The fits' positions and directions in the part's frame (only when the scan has a frame). */
  inPartFrame: boolean;
  hasFrame: boolean;
  /** The kept seam shown (null: the engine's choice, the first). */
  seam: string | null;
  views: ReportView[];
};

/** The request's body (APP_API.md "The report"): only what is set, the pictures in order. */
export function reportRequest(o: ReportOptions): ReportRequest {
  const out: ReportRequest = {};
  if (o.tolerance !== null && Number.isFinite(o.tolerance) && o.tolerance > 0) out.tolerance_mm = o.tolerance;
  if (o.inPartFrame && o.hasFrame) out.numbers_in_part_frame = true;
  if (o.seam) out.seam = o.seam;
  if (o.views.length)
    out.pictures = o.views.map((v): ReportPicture => (v.caption.trim() ? { title: v.title, caption: v.caption.trim(), data: v.data } : { title: v.title, data: v.data }));
  return out;
}
