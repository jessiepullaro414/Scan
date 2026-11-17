/**
 * The Section tool's 2D profile (FEATURE_PLAN.md I1), floating over the 3D view: the cut as the engine drew it, in
 * the plane's own axes, seen from the side its normal points to (measured stretches solid, not-measured ones dashed
 * red), with the axes named, the drawing's origin, a scale bar, and the profile's numbers (lines, closed loops, total
 * length, the closed loops' areas). It fits the profile when a cut comes (until the user zooms or pans; "Fit" or a
 * double click fits it again); the wheel zooms about the cursor, a drag pans. Two clicks measure a distance between two
 * of the profile's points (a click snaps to the nearest point within a few pixels), with its dx and dy along the
 * drawing's axes. The geometry is lib/section.ts's; the paths are made once per cut and only their transform changes
 * with zoom and pan.
 */
import { CircleHelp, Maximize2, Minimize2, Ruler, Scan, TriangleExclamationPoint, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { sentence } from "../../api/http";
import type { PartFrameView, SectionAnswer } from "../../api/types";
import { IconButton } from "../../components/ui/Button";
import { Spinner } from "../../components/ui/Feedback";
import { Tooltip } from "../../components/ui/Popover";
import { fmtInt } from "../../lib/format";
import { axisNames, distance2d, fitView, panBy, profileNumbers, profileOf, scaleBar, snapTo, toScreen, zoomAbout, type Snap, type View } from "../../lib/section";
import { cn } from "../../lib/util";

const SIZES = { normal: { w: 440, h: 250 }, large: { w: 760, h: 470 } } as const;
const SNAP_PX = 12;
const NOT_MEASURED = "#e5484d";
const HELP =
  "The cut in the plane's own axes, seen from the side its normal points to, at the engine's numbers. Solid: measured surface; dashed red: surface that hole filling made up. Click two points of the profile to measure between them (a click snaps to the nearest point of the profile). Drag to pan, the wheel zooms, a double click fits the profile again.";

const mm = (x: number, d = 2) => `${(Math.abs(x) < 0.5 * 10 ** -d ? 0 : x).toFixed(d)}`;
const area = (x: number) => `${Math.abs(x) >= 1000 ? fmtInt(Math.round(x)) : x.toFixed(1)} mm²`;

/** The profile's paths in the drawing's own mm (y up): the measured stretches and the not-measured ones. */
function pathsOf(answer: SectionAnswer): { measured: string; filled: string } {
  const meas: string[] = [],
    fill: string[] = [];
  for (const ln of profileOf(answer)) {
    const P = ln.pts;
    for (const [a, b, filled] of ln.stretches) {
      const parts: string[] = [];
      for (let i = a; i <= b; i++) {
        const j = i % ln.n;
        parts.push(`${P[2 * j]} ${P[2 * j + 1]}`);
      }
      const d = `M${parts.join("L")}${ln.closed && a === 0 && b === ln.n && ln.stretches.length === 1 ? "Z" : ""}`;
      (filled ? fill : meas).push(d);
    }
  }
  return { measured: meas.join(""), filled: fill.join("") };
}

export function SectionProfile({ answer, frame, busy, onClose }: { answer: SectionAnswer; frame: PartFrameView | null; busy: boolean; onClose: () => void }) {
  const [size, setSize] = useState<keyof typeof SIZES>("normal");
  const { w, h } = SIZES[size];
  const [view, setView] = useState<View>(() => fitView(answer.bbox, w, h));
  const touched = useRef(false);
  const [marks, setMarks] = useState<Snap[]>([]);
  const [hover, setHover] = useState<Snap | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const lines = useMemo(() => profileOf(answer), [answer]);
  const paths = useMemo(() => pathsOf(answer), [answer]);
  const names = useMemo(() => axisNames(answer.plane, answer.plane.in_part_frame ? frame : null), [answer.plane, frame]);
  const nums = useMemo(() => profileNumbers(answer), [answer]);
  const planeKey = JSON.stringify([answer.plane.point, answer.plane.normal, answer.mesh_version]);

  // a new cut: fitted (unless the user has zoomed or panned); a new plane drops the measurement
  useEffect(() => {
    if (!touched.current) setView(fitView(answer.bbox, w, h));
  }, [answer, w, h]);
  useEffect(() => {
    setMarks([]);
    setHover(null);
  }, [planeKey]);
  const fit = () => {
    touched.current = false;
    setView(fitView(answer.bbox, w, h));
  };

  // the wheel zooms about the cursor (a listener of its own: React's is passive and can't keep the page still)
  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      touched.current = true;
      setView((v) => zoomAbout(v, w, h, e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * 0.0015)));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [w, h]);

  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const local = (e: ReactPointerEvent) => {
    const r = svg.current!.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top] as const;
  };
  const onDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return;
    (e.target as Element).setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, moved: false };
  };
  const onMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (d && e.buttons & 1) {
      const dx = e.clientX - d.x,
        dy = e.clientY - d.y;
      if (!d.moved && Math.hypot(dx, dy) < 4) return;
      d.moved = true;
      d.x = e.clientX;
      d.y = e.clientY;
      touched.current = true;
      setView((v) => panBy(v, dx, dy));
      setHover(null);
      return;
    }
    const [sx, sy] = local(e);
    setHover(snapTo(lines, view, w, h, sx, sy, SNAP_PX));
  };
  const onUp = (e: ReactPointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    drag.current = null;
    if (!d || d.moved || e.button !== 0) return;
    const [sx, sy] = local(e);
    const s = snapTo(lines, view, w, h, sx, sy, SNAP_PX);
    if (!s) return; // not on the profile: nothing to measure from
    setMarks((m) => (m.length >= 2 ? [s] : [...m, s]));
  };

  // the drawing's mm -> the panel's pixels, y turned over (never mirrored)
  const transform = `matrix(${view.scale} 0 0 ${-view.scale} ${w / 2 - view.cx * view.scale} ${h / 2 + view.cy * view.scale})`;
  const bar = scaleBar(view, w);
  const [ox, oy] = toScreen(view, w, h, 0, 0);
  const dist = marks.length === 2 ? distance2d(marks[0], marks[1]) : null;
  const sc = (s: Snap) => toScreen(view, w, h, s.x, s.y);
  const firstSigma = answer.lines[0]?.sigma_mm?.mean ?? null;

  return (
    <div className="absolute bottom-4 right-4 z-10 max-w-[calc(100%-2rem)] animate-pop-in overflow-hidden rounded-xl border border-line-strong bg-panel shadow-lg" style={{ width: w + 2 }} aria-label="Section profile">
      <div className="flex items-center gap-1.5 border-b border-line px-2.5 py-1.5">
        <Ruler size={13} className="shrink-0 text-accent" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-xs font-semibold text-fg" title={answer.plane.description}>
          {answer.kept ? answer.kept.name : "Section"}
          <span className="font-normal text-subtle"> · {answer.plane.description.replace(/^Section /, "")}</span>
        </span>
        {busy ? <Spinner size={13} className="text-accent" /> : null}
        <Tooltip side="top" content={<div className="max-w-[300px] text-muted">{HELP}</div>}>
          <button type="button" className="grid h-5 w-5 shrink-0 place-items-center rounded text-subtle hover:text-fg" aria-label={`About the profile: ${HELP}`}>
            <CircleHelp size={13} aria-hidden />
          </button>
        </Tooltip>
        <IconButton icon={Scan} size="xs" label="Fit the profile (double click)" onClick={fit} />
        <IconButton icon={size === "normal" ? Maximize2 : Minimize2} size="xs" label={size === "normal" ? "Larger" : "Smaller"} onClick={() => setSize((s) => (s === "normal" ? "large" : "normal"))} />
        <IconButton icon={X} size="xs" label="Close the Section tool (Esc)" onClick={onClose} />
      </div>
      <svg
        ref={svg}
        width={w}
        height={h}
        className={cn("block touch-none select-none", hover ? "cursor-crosshair" : "cursor-grab")}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerLeave={() => setHover(null)}
        onDoubleClick={fit}
        role="img"
        aria-label={`The section's profile: ${nums.lines} lines, ${mm(nums.length, 1)} mm long`}
      >
        {/* the drawing's origin (where the frame's origin falls on the plane) */}
        {ox > -20 && ox < w + 20 && oy > -20 && oy < h + 20 ? (
          <g stroke="currentColor" className="text-subtle" strokeWidth={1} opacity={0.5}>
            <line x1={ox - 6} y1={oy} x2={ox + 6} y2={oy} />
            <line x1={ox} y1={oy - 6} x2={ox} y2={oy + 6} />
          </g>
        ) : null}
        <g transform={transform} fill="none" strokeLinejoin="round" strokeLinecap="round">
          <path d={paths.measured} stroke="currentColor" className="text-fg" strokeWidth={1.4} vectorEffect="non-scaling-stroke" />
          {paths.filled ? <path d={paths.filled} stroke={NOT_MEASURED} strokeWidth={1.4} strokeDasharray="5 3" vectorEffect="non-scaling-stroke" /> : null}
        </g>
        {/* the measurement */}
        {marks.length === 2 ? (
          <line x1={sc(marks[0])[0]} y1={sc(marks[0])[1]} x2={sc(marks[1])[0]} y2={sc(marks[1])[1]} stroke="var(--c-accent)" strokeWidth={1.6} />
        ) : null}
        {marks.map((s, i) => (
          <circle key={i} cx={sc(s)[0]} cy={sc(s)[1]} r={4} fill="var(--c-accent)" stroke="white" strokeWidth={1} />
        ))}
        {hover ? <circle cx={sc(hover)[0]} cy={sc(hover)[1]} r={6} fill="none" stroke="var(--c-accent)" strokeWidth={1.4} /> : null}
        {/* the axes' names */}
        <g className="text-muted" fill="currentColor" stroke="currentColor" fontSize={10.5}>
          <line x1={14} y1={h - 14} x2={40} y2={h - 14} strokeWidth={1.2} />
          <path d={`M40 ${h - 17} L46 ${h - 14} L40 ${h - 11} Z`} stroke="none" />
          <text x={49} y={h - 10.5} stroke="none">
            {names.u}
          </text>
          <line x1={14} y1={h - 14} x2={14} y2={h - 40} strokeWidth={1.2} />
          <path d={`M11 ${h - 40} L14 ${h - 46} L17 ${h - 40} Z`} stroke="none" />
          <text x={10} y={h - 50} stroke="none">
            {names.v}
          </text>
        </g>
        {/* the scale bar */}
        <g className="text-muted" stroke="currentColor" fill="currentColor" fontSize={10.5}>
          <line x1={w - 14 - bar.px} y1={h - 14} x2={w - 14} y2={h - 14} strokeWidth={1.4} />
          <line x1={w - 14 - bar.px} y1={h - 18} x2={w - 14 - bar.px} y2={h - 10} strokeWidth={1.2} />
          <line x1={w - 14} y1={h - 18} x2={w - 14} y2={h - 10} strokeWidth={1.2} />
          <text x={w - 14 - bar.px / 2} y={h - 21} textAnchor="middle" stroke="none">
            {bar.label}
          </text>
        </g>
        {!answer.lines.length ? (
          <text x={w / 2} y={h / 2} textAnchor="middle" className="fill-current text-subtle" fontSize={12}>
            The plane misses the scan
          </text>
        ) : null}
      </svg>
      <div className="space-y-1 border-t border-line px-2.5 py-1.5 text-[11.5px] leading-4">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 font-mono text-fg tnum" aria-label="Distance">
          {dist ? (
            <>
              <span className="font-semibold">{mm(dist.d, 3)} mm</span>
              <span className="text-subtle">
                Δ{names.u} {mm(dist.dx, 3)}
              </span>
              <span className="text-subtle">
                Δ{names.v} {mm(dist.dy, 3)}
              </span>
            </>
          ) : (
            <span className="font-sans text-subtle">
              {marks.length === 1 ? "Click a second point of the profile" : "Click two points of the profile to measure between them"}
              {hover ? ` · ${names.u} ${mm(hover.x)}, ${names.v} ${mm(hover.y)}` : ""}
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-subtle tnum">
          <span>
            {fmtInt(nums.lines)} {nums.lines === 1 ? "line" : "lines"}, {fmtInt(nums.closed)} closed
          </span>
          <span>{mm(nums.length, 1)} mm long</span>
          {nums.loops.slice(0, 3).map((l) => (
            <span key={l.line} title={l.kind === "outline" ? "A closed outline (counter-clockwise)" : "A hole inside an outline (clockwise)"}>
              {l.kind === "outline" ? "Outline" : "Hole"} {area(Math.abs(l.area))}
            </span>
          ))}
          {nums.loops.length > 1 ? <span title="The closed loops' areas together: outlines less holes">Net {area(nums.net)}</span> : null}
          {firstSigma !== null ? <span title="Mean 1σ of the longest line's measured points">σ {firstSigma.toFixed(3)} mm</span> : null}
        </div>
        {answer.truncated ? (
          <div className="text-subtle">
            Showing the {fmtInt(answer.truncated.lines_shown)} longest of {fmtInt(answer.truncated.lines)} lines ({fmtInt(answer.truncated.points_shown)} of{" "}
            {fmtInt(answer.truncated.points)} points); the numbers and the downloads hold them all.
          </div>
        ) : null}
        {answer.warnings.map((wt) => (
          <div key={wt} className="flex items-start gap-1.5 text-warn">
            <TriangleExclamationPoint size={12} className="mt-px shrink-0" aria-hidden />
            <span>{sentence(wt)}.</span>
          </div>
        ))}
      </div>
    </div>
  );
}
