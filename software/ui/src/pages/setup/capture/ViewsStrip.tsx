/** The captured views: thumbnails with their numbers, a visible × to delete each (U20), and errors after a solve. */
import { Hand, ImageOff, Sparkles, TriangleExclamationPoint, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { CalibKind, CalibView } from "../../../api/types";
import { focusAfterDelete } from "../../../lib/focusAfter";
import { TWO_LASERS, viewLaserSummary } from "../../../lib/laserSet";
import { cn } from "../../../lib/util";
import { useCalib } from "../../../store/calib";

function errTone(v: CalibView) {
  if (v.error_px === null) return null;
  if (v.bad) return "bad";
  return v.error_px < 0.5 ? "ok" : v.error_px < 1 ? "warn" : "bad";
}

function Thumb({
  v,
  kind,
  editable,
  fresh,
  names,
  onDelete,
}: {
  v: CalibView;
  kind: CalibKind;
  editable: boolean;
  fresh: boolean;
  names: readonly string[];
  onDelete: (index: number, hadFocus: boolean) => void;
}) {
  const busy = useCalib((s) => s.action === "delete");
  const [failed, setFailed] = useState(false);
  const tone = errTone(v);
  const label = `View ${v.index + 1}${v.auto ? ", auto" : ", by hand"}${v.distance_mm !== null ? `, ${Math.round(v.distance_mm)}\u202fmm` : ""}${
    v.error_px !== null ? `, error ${v.error_px.toFixed(2)}\u202fpx${v.bad ? ", flagged bad" : ""}` : ""
  }`;
  const laser = viewLaserSummary(v, names);
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
        <span className={cn("absolute top-1 grid h-4 w-4 place-items-center rounded bg-black/65 text-white/85", editable ? "left-9" : "right-1")} title={v.auto ? "Auto-captured" : "Captured by hand"}>
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
            {v.error_px.toFixed(2)}&#8239;px{v.bad ? " · bad" : ""}
          </span>
        ) : null}
        {editable ? (
          <button
            type="button"
            onClick={(e) => onDelete(v.index, document.activeElement === e.currentTarget)}
            disabled={busy}
            data-delete-view={v.index}
            aria-label={`Delete view ${v.index + 1}`}
            title={`Delete view ${v.index + 1}`}
            className="absolute right-1 top-1 grid h-5 w-5 place-items-center rounded bg-black/70 text-white shadow ring-1 ring-white/30 transition-colors hover:bg-danger focus-visible:bg-danger disabled:opacity-40"
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
        ) : laser ? (
          <span title={laser.title} className={cn("min-w-0 truncate", !laser.ok && "text-warn")}>
            {laser.text}
          </span>
        ) : (
          <span>{v.corners_left} pts</span>
        )}
        <span className="flex-1" />
        <span>{v.distance_mm !== null ? `${Math.round(v.distance_mm)}\u202fmm` : "–"}</span>
      </div>
    </li>
  );
}

const NO_VIEWS: CalibView[] = [];

export function ViewsStrip({ kind, editable, className }: { kind: CalibKind; editable: boolean; className?: string }) {
  const views = useCalib((s) => s.session?.views ?? NO_VIEWS);
  const names = useCalib((s) => s.session?.lasers ?? TWO_LASERS);
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
  const del = useCalib((s) => s.deleteView);
  /** "Delete view N" keeps the keyboard's place (the leftover of UX_PLAN R3): the button is disabled while the
   * request runs and then gone, so the focus goes to the delete button of the view now in its place, else the one
   * before it, else the Capture button (lib/focusAfter.ts). */
  const onDelete = async (index: number, hadFocus: boolean) => {
    const before = views.map((v) => v.index);
    await del(index);
    if (!hadFocus) return;
    requestAnimationFrame(() => {
      const now = (useCalib.getState().session?.views ?? []).map((v) => v.index);
      const at = focusAfterDelete(before, index, now);
      const target =
        at === null
          ? document.querySelector<HTMLElement>("[data-calib-capture]")
          : listRef.current?.querySelector<HTMLElement>(`[data-delete-view="${at}"]`);
      target?.focus();
    });
  };
  return (
    <div className={cn("border-t border-line bg-panel", className)}>
      <div className="flex items-center gap-2 px-4 pt-2.5 text-[11px] text-subtle">
        <span className="font-semibold uppercase tracking-wider">Captured views</span>
        <span className="tnum">{views.length}</span>
        {bad ? <span className="font-medium text-danger">· {bad} flagged bad</span> : null}
        <span className="flex-1" />
        {editable && views.length ? <span>Press × on a view to delete it</span> : null}
      </div>
      <ul ref={listRef} className="flex gap-2 overflow-x-auto px-4 pb-3 pt-2" aria-label="Captured views">
        {views.map((v) => (
          <Thumb key={v.index} v={v} kind={kind} editable={editable} fresh={v.index === freshIndex.current} names={names} onDelete={(i, f) => void onDelete(i, f)} />
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
