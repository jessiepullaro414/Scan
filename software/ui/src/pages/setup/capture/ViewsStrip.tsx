/** The captured views: thumbnails with their numbers, delete on hover, and errors after a solve. */
import { Hand, ImageOff, Sparkles, TriangleExclamationPoint, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { CalibKind, CalibView } from "../../../api/types";
import { cn } from "../../../lib/util";
import { useCalib } from "../../../store/calib";

function errTone(v: CalibView) {
  if (v.error_px === null) return null;
  if (v.bad) return "bad";
  return v.error_px < 0.5 ? "ok" : v.error_px < 1 ? "warn" : "bad";
}

function Thumb({ v, kind, editable, fresh }: { v: CalibView; kind: CalibKind; editable: boolean; fresh: boolean }) {
  const del = useCalib((s) => s.deleteView);
  const busy = useCalib((s) => s.action === "delete");
  const [failed, setFailed] = useState(false);
  const tone = errTone(v);
  const label = `View ${v.index + 1}${v.auto ? ", auto" : ", by hand"}${v.distance_mm !== null ? `, ${Math.round(v.distance_mm)} mm` : ""}${
    v.error_px !== null ? `, error ${v.error_px.toFixed(2)} px${v.bad ? ", flagged bad" : ""}` : ""
  }`;
  const lines = v.lines;
  return (
    <li
      data-view={v.index}
      className={cn(
        "group relative w-[132px] shrink-0 overflow-hidden rounded-lg border bg-black shadow-sm transition-[border-color,box-shadow]",
        tone === "bad" ? "border-danger ring-2 ring-danger/60" : tone === "warn" ? "border-warn/70" : "border-line-strong",
        fresh && "animate-pop-in",
      )}
      aria-label={label}
      title={v.warnings.length ? v.warnings.join("\n") : undefined}
    >
      <div className="relative aspect-[1.6] w-full">
        {v.thumb_url && !failed ? (
          <img src={v.thumb_url} alt="" className="absolute inset-0 h-full w-full object-cover" onError={() => setFailed(true)} draggable={false} loading="lazy" />
        ) : (
          <div className="absolute inset-0 grid place-items-center text-white/40">
            <ImageOff size={16} aria-hidden />
          </div>
        )}
        {tone === "bad" ? <div className="absolute inset-0 bg-danger/25" aria-hidden /> : null}
        <span className="absolute left-1 top-1 rounded bg-black/65 px-1 font-mono text-[10.5px] font-semibold text-white tnum">#{v.index + 1}</span>
        <span className="absolute right-1 top-1 grid h-4 w-4 place-items-center rounded bg-black/65 text-white/85" title={v.auto ? "Auto-captured" : "Captured by hand"}>
          {v.auto ? <Sparkles size={10} aria-hidden /> : <Hand size={10} aria-hidden />}
        </span>
        {v.warnings.length && tone !== "bad" ? (
          <span className="absolute bottom-1 right-1 grid h-4 w-4 place-items-center rounded bg-warn text-black" aria-hidden>
            <TriangleExclamationPoint size={10} />
          </span>
        ) : null}
        {v.error_px !== null ? (
          <span
            className={cn(
              "absolute bottom-1 left-1 rounded px-1 font-mono text-[10px] font-semibold tnum",
              tone === "bad" ? "bg-danger text-white" : tone === "warn" ? "bg-warn text-black" : "bg-ok text-white",
            )}
          >
            {v.error_px.toFixed(2)} px{v.bad ? " · bad" : ""}
          </span>
        ) : null}
        {editable ? (
          <button
            type="button"
            onClick={() => void del(v.index)}
            disabled={busy}
            aria-label={`Delete view ${v.index + 1}`}
            className="absolute right-1 top-1 grid h-5 w-5 place-items-center rounded bg-danger text-white opacity-0 shadow transition-opacity focus-visible:opacity-100 group-hover:opacity-100 disabled:opacity-40"
          >
            <X size={12} strokeWidth={2.6} aria-hidden />
          </button>
        ) : null}
      </div>
      <div className="flex items-center gap-1 bg-surface-2 px-1.5 py-1 font-mono text-[10px] text-muted tnum">
        {kind === "stereo" ? (
          <span title="Corners left / right">
            {v.corners_left}/{v.corners_right ?? "–"}
          </span>
        ) : lines ? (
          <span title="Laser points on the margin: vertical / horizontal" className={cn(Math.min(lines.vertical, lines.horizontal) < 15 && "text-warn")}>
            V{lines.vertical} H{lines.horizontal}
          </span>
        ) : (
          <span>{v.corners_left} pts</span>
        )}
        <span className="flex-1" />
        <span>{v.distance_mm !== null ? `${Math.round(v.distance_mm)} mm` : "–"}</span>
      </div>
    </li>
  );
}

const NO_VIEWS: CalibView[] = [];

export function ViewsStrip({ kind, editable, className }: { kind: CalibKind; editable: boolean; className?: string }) {
  const views = useCalib((s) => s.session?.views ?? NO_VIEWS);
  const min = useCalib((s) => s.session?.min_views ?? 0);
  const lo = useCalib((s) => s.session?.target_views[0] ?? 0);
  const listRef = useRef<HTMLUListElement>(null);
  const prevCount = useRef(views.length);
  const freshIndex = useRef<number | null>(null);
  if (views.length > prevCount.current) freshIndex.current = views[views.length - 1]?.index ?? null;
  useEffect(() => {
    if (views.length > prevCount.current) {
      // Bring the new view into sight (not the empty slots after it).
      const items = listRef.current?.querySelectorAll<HTMLElement>("li[data-view]");
      items?.[items.length - 1]?.scrollIntoView({ behavior: "smooth", inline: "nearest", block: "nearest" });
    }
    prevCount.current = views.length;
  }, [views.length]);
  const placeholders = Math.max(0, Math.min(lo, 12) - views.length);
  const bad = views.filter((v) => v.bad).length;
  return (
    <div className={cn("border-t border-line bg-panel", className)}>
      <div className="flex items-center gap-2 px-4 pt-2.5 text-[11px] text-subtle">
        <span className="font-semibold uppercase tracking-wider">Captured {kind === "lasers" ? "poses" : "views"}</span>
        <span className="tnum">{views.length}</span>
        {bad ? <span className="font-medium text-danger">· {bad} flagged bad</span> : null}
        <span className="flex-1" />
        {editable && views.length ? <span>Hover a view to delete it</span> : null}
      </div>
      <ul ref={listRef} className="flex gap-2 overflow-x-auto px-4 pb-3 pt-2" aria-label="Captured views">
        {views.map((v) => (
          <Thumb key={v.index} v={v} kind={kind} editable={editable} fresh={v.index === freshIndex.current} />
        ))}
        {Array.from({ length: placeholders }, (_, i) => (
          <li
            key={`p${i}`}
            className="grid h-[104px] w-[132px] shrink-0 place-items-center rounded-lg border border-dashed border-line-strong text-[11px] text-subtle"
            aria-hidden
          >
            {views.length + i + 1 <= min ? `#${views.length + i + 1}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}
