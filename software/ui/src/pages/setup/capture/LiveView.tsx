/**
 * The capture screen's live view: the calibration preview(s) with the canvas overlay, the hint
 * label at the target region, and the status bar that says what to do now.
 *
 * Performance: the JPEG `<img>` and the overlay canvas are updated outside React (a store
 * subscription sets `img.src`; a rAF loop draws the latest CalibLive). React re-renders here only
 * when a primitive the markup shows changes (the hint text, a count, the layout size).
 */
import { CircleCheck, Hand, ImageOff, Maximize2, Minimize2, Move, Rotate3d, ScanEye, Timer, TriangleExclamationPoint } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type RefObject } from "react";
import type { CalibKind } from "../../../api/types";
import { Spinner } from "../../../components/ui/Feedback";
import { KIND_META } from "../../../lib/setup";
import { cn, useTicker } from "../../../lib/util";
import { useCalib, type CalibCamera } from "../../../store/calib";
import { drawOverlay, type OverlayAnim } from "./overlay";

/** The latest CalibLive, unless it is stale or for another kind. */
function liveFor(kind: CalibKind) {
  const st = useCalib.getState();
  const l = st.live;
  if (!l || l.kind !== kind || performance.now() - st.liveAt > 2000) return null;
  return l;
}

function StaleBadge({ camera }: { camera: CalibCamera }) {
  useTicker(1000);
  const at = useCalib((s) => s.previews[camera]?.at ?? 0);
  if (!at || performance.now() - at < 2500) return null;
  return <span className="absolute right-2 top-2 rounded bg-warn/90 px-1.5 py-0.5 text-[10.5px] font-semibold text-black">No new frames</span>;
}

function CornerCount({ camera, kind }: { camera: CalibCamera; kind: CalibKind }) {
  const n = useCalib((s) => {
    const l = s.live;
    if (!l || l.kind !== kind) return -1;
    const c = camera === "calib_left" ? l.left?.corners : l.right?.corners;
    return c ? Math.floor(c.length / 2) : 0;
  });
  if (n < 0) return null;
  return (
    <span className="absolute bottom-2 left-2 rounded-md bg-black/60 px-1.5 py-0.5 font-mono text-[11px] text-white/90 tnum backdrop-blur-sm">
      {n} corner{n === 1 ? "" : "s"}
    </span>
  );
}

/** Board distance and tilt on the image, where the eye is (amber outside the step's range). */
function PosePill({ kind }: { kind: CalibKind }) {
  const d = useCalib((s) => (s.live?.kind === kind && s.live.distance_mm !== null ? Math.round(s.live.distance_mm) : null));
  const tilt = useCalib((s) => (s.live?.kind === kind && s.live.tilt_deg !== null ? Math.round(s.live.tilt_deg) : null));
  if (d === null) return null;
  const [lo, hi] = KIND_META[kind].distance;
  const ok = d >= lo && d <= hi;
  return (
    <span
      className={cn(
        "absolute bottom-2 right-2 rounded-md px-1.5 py-0.5 font-mono text-[11px] tnum backdrop-blur-sm",
        ok ? "bg-black/60 text-white/90" : "bg-warn/90 font-semibold text-black",
      )}
      title={ok ? "Board distance and tilt" : `Aim for ${lo}–${hi} mm`}
    >
      {d} mm{tilt !== null ? ` · ${tilt}°` : ""}
    </span>
  );
}

function HintLabel({ kind }: { kind: CalibKind }) {
  const text = useCalib((s) => (s.live?.kind === kind ? (s.live.hint?.text ?? null) : null));
  const region = useCalib((s) => (s.live?.kind === kind ? (s.live.hint?.region?.join(",") ?? null) : null));
  const tilt = useCalib((s) => (s.live?.kind === kind ? (s.live.hint?.tilt ?? null) : null));
  const distance = useCalib((s) => (s.live?.kind === kind ? (s.live.hint?.distance ?? null) : null));
  if (!text) return null;
  const r = region ? region.split(",").map(Number) : null;
  const Icon = distance === "closer" ? Maximize2 : distance === "farther" ? Minimize2 : tilt ? Rotate3d : r ? Move : ScanEye;
  // Keep the label inside the image: hug the region's outer edge on the sides, centre it in the middle.
  let style: CSSProperties;
  if (r && r.length === 4) {
    const cx = (r[0] + r[2]) / 2;
    const above = r[1] > 0.2;
    const y: CSSProperties = above ? { top: `${r[1] * 100}%` } : { top: `${r[3] * 100}%` };
    const ty = above ? "calc(-100% - 6px)" : "6px";
    if (cx < 0.36) style = { ...y, left: `${r[0] * 100 + 1.5}%`, transform: `translateY(${ty})` };
    else if (cx > 0.64) style = { ...y, right: `${(1 - r[2]) * 100 + 1.5}%`, transform: `translateY(${ty})` };
    else style = { ...y, left: `${cx * 100}%`, transform: `translate(-50%, ${ty})` };
  } else style = { left: "50%", bottom: "12%", transform: "translateX(-50%)" };
  return (
    <div className="pointer-events-none absolute z-10 w-max max-w-[72%]" style={style}>
      <div key={text} className="flex animate-pop-in items-center gap-2 rounded-2xl border border-white/20 bg-[rgb(20_40_80/0.82)] px-3 py-1.5 text-[13px] font-medium leading-snug text-white shadow-lg backdrop-blur">
        <Icon size={15} className={cn("shrink-0", tilt && "calib-wobble")} aria-hidden />
        {/* wrap to a second line rather than cut the advice off */}
        <span className="line-clamp-2">{text}</span>
      </div>
    </div>
  );
}

export function CalibImage({
  camera,
  kind,
  label,
  primary,
  size,
  heatmap,
}: {
  camera: CalibCamera;
  kind: CalibKind;
  label: string;
  primary: boolean;
  size: { w: number; h: number };
  heatmap: boolean;
}) {
  const imgRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const flashRef = useRef<HTMLDivElement>(null);
  const has = useCalib((s) => !!s.previews[camera]);
  const active = useCalib((s) => s.session?.state === "capturing" || s.session?.state === "armed" || s.session?.state === "recording");

  // The JPEG: set straight on the element, ~10 times a second.
  useEffect(() => {
    const show = (url: string | undefined) => {
      const img = imgRef.current;
      if (img && url && img.src !== url) img.src = url;
    };
    show(useCalib.getState().previews[camera]?.url);
    return useCalib.subscribe((s, p) => {
      if (s.previews[camera] !== p.previews[camera]) show(s.previews[camera]?.url);
    });
  }, [camera]);

  // The capture flash.
  useEffect(
    () =>
      useCalib.subscribe((s, p) => {
        if (s.captureSeq === p.captureSeq) return;
        const el = flashRef.current;
        if (!el) return;
        el.classList.remove("calib-flash");
        void el.offsetWidth; // restart the animation
        el.classList.add("calib-flash");
      }),
    [],
  );

  // The overlay: one rAF loop per view, reading the store directly.
  useEffect(() => {
    const c = canvasRef.current;
    if (!c || !size.w) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    c.width = Math.round(size.w * dpr);
    c.height = Math.round(size.h * dpr);
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const anim: OverlayAnim = { steady: 0, sinceCapture: 99 };
    let seq = useCalib.getState().captureSeq;
    let capAt = -1e9;
    let last = performance.now();
    const t0 = last;
    let raf = 0;
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const st = useCalib.getState();
      if (st.captureSeq !== seq) {
        seq = st.captureSeq;
        capAt = now;
        anim.steady = 1;
      }
      anim.sinceCapture = (now - capAt) / 1000;
      const live = liveFor(kind);
      const target = live?.ready ? live.steady : 0;
      const k = target < anim.steady ? 1 - Math.exp(-dt * 30) : 1 - Math.exp(-dt * 14);
      anim.steady += (target - anim.steady) * k;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawOverlay(ctx, size.w, size.h, live, anim, { camera: camera === "calib_left" ? "left" : "right", primary, heatmap, t: (now - t0) / 1000 });
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [size.w, size.h, camera, kind, primary, heatmap]);

  return (
    <figure
      className="relative shrink-0 overflow-hidden rounded-xl border border-line-strong bg-black shadow-md"
      style={{ width: size.w, height: size.h }}
      aria-label={`${label}: live view with the detected corners${primary ? ", the coverage map and where to put the board next" : ""}`}
    >
      <img ref={imgRef} alt="" draggable={false} className={cn("absolute inset-0 h-full w-full object-fill transition-opacity duration-200", has ? "opacity-100" : "opacity-0")} />
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" aria-hidden />
      <div ref={flashRef} className="pointer-events-none absolute inset-0 bg-white opacity-0" aria-hidden />
      {!has ? (
        <div className="absolute inset-0 grid place-items-center">
          <div className="flex flex-col items-center gap-2 px-6 text-center text-xs text-white/55">
            {active ? <Spinner size={18} /> : <ImageOff size={20} aria-hidden />}
            {active ? "Waiting for the camera…" : "The camera view appears when the step starts"}
          </div>
        </div>
      ) : null}
      <figcaption className="absolute left-2 top-2 rounded-md bg-black/60 px-1.5 py-0.5 text-[11px] font-medium text-white/90 backdrop-blur-sm">{label}</figcaption>
      {has ? <CornerCount camera={camera} kind={kind} /> : null}
      {has && primary ? <PosePill kind={kind} /> : null}
      <StaleBadge camera={camera} />
      {primary ? <HintLabel kind={kind} /> : null}
    </figure>
  );
}

/** Fits one or two image boxes of the image's aspect ratio into the available space, minus `reserve` px of height. */
function useFit(ref: RefObject<HTMLDivElement | null>, ar: number, n: number, gap: number, reserve: number) {
  const [box, setBox] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const fit = (W: number, H: number) => {
      const w = Math.max(0, Math.floor(Math.min((W - gap * (n - 1)) / n, (H - reserve) * ar)));
      setBox((b) => (b.w === w ? b : { w, h: Math.floor(w / ar) }));
    };
    const r = el.getBoundingClientRect();
    fit(r.width, r.height);
    const ro = new ResizeObserver(([e]) => fit(e.contentRect.width, e.contentRect.height));
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, ar, n, gap, reserve]);
  return box;
}

/** The camera view(s), as large as the space allows, with the status line right under them. */
export function Stage({ kind, heatmap }: { kind: CalibKind; heatmap: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const stereo = kind === "stereo";
  const size = useCalib((s) => (s.live?.image_size ? `${s.live.image_size[0]}x${s.live.image_size[1]}` : "1280x800"));
  const [iw, ih] = size.split("x").map(Number);
  const ar = iw > 0 && ih > 0 ? iw / ih : 1.6;
  const box = useFit(ref, ar, stereo ? 2 : 1, 12, 56);
  return (
    <div ref={ref} className="relative min-h-0 min-w-0 flex-1">
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        {box.w > 40 ? (
          <div className="flex gap-3">
            <CalibImage camera="calib_left" kind={kind} label={stereo ? "Left camera" : "Left camera, rectified"} primary size={box} heatmap={heatmap} />
            {stereo ? <CalibImage camera="calib_right" kind={kind} label="Right camera" primary={false} size={box} heatmap={heatmap} /> : null}
          </div>
        ) : null}
        <StatusBar kind={kind} />
      </div>
    </div>
  );
}

/** One line under the images: what to do right now. */
export function StatusBar({ kind }: { kind: CalibKind }) {
  const state = useCalib((s) => s.session?.state);
  const ready = useCalib((s) => (s.live?.kind === kind ? s.live.ready : false));
  const reason = useCalib((s) => (s.live?.kind === kind ? s.live.reason : null));
  const found = useCalib((s) => (s.live?.kind === kind ? s.live.board_found : false));
  const steady = useCalib((s) => (s.live?.kind === kind ? Math.round((s.live.steady ?? 0) * 10) / 10 : 0));
  const auto = useCalib((s) => !!s.session?.auto);
  const has = useCalib((s) => !!s.live && s.live.kind === kind);
  const enough = useCalib((s) => (s.session ? s.session.views.length >= s.session.target_views[1] && s.session.target_views[1] > 0 : false));
  if (state !== "capturing") return <div className="h-11" />;
  let tone: "ok" | "warn" | "neutral" = "neutral";
  let icon = <Spinner size={15} />;
  let text = "Waiting for the camera…";
  if (has) {
    if (ready) {
      tone = "ok";
      icon = steady >= 1 ? <CircleCheck size={16} aria-hidden /> : <Timer size={16} aria-hidden />;
      text = enough
        ? "Plenty of views: press Enter to calibrate, or keep adding"
        : auto
          ? steady > 0.05
            ? "Hold still… the view is taken when the ring is full"
            : "Good spot: hold still to capture"
          : "Good spot: press Space to capture";
    } else {
      tone = found ? "warn" : "neutral";
      icon = found ? <TriangleExclamationPoint size={16} aria-hidden /> : <Hand size={16} aria-hidden />;
      text = reason ?? (found ? "Move the board to a new spot" : "Show the board to the camera");
    }
  }
  return (
    <div className="flex h-11 items-center justify-center" role="status" aria-live="polite">
      <div
        className={cn(
          "flex max-w-full items-center gap-2 rounded-full border px-4 py-1.5 text-sm font-medium shadow-sm transition-colors",
          tone === "ok" ? "border-ok/40 bg-ok-soft text-ok" : tone === "warn" ? "border-warn/40 bg-warn-soft text-warn" : "border-line-strong bg-surface-2 text-muted",
        )}
      >
        {icon}
        <span className="truncate text-fg">{text}</span>
      </div>
    </div>
  );
}
