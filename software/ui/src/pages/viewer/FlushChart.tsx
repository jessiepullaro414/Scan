/**
 * The small chart of the flush along a seam (FEATURE_PLAN.md I4): across, the place along the seam (mm); up, the flush
 * (mm, + where panel B stands above panel A). Each station's reading is a dot with its ± band (1σ, the spread from
 * scan to scan), the mean a dashed line; a station whose flush is not trusted is a hollow grey dot (at the chart's
 * edge when its reading lies outside), with its reason on hover. The scaling is lib/flush.ts's `flushChart`.
 */
import { useMemo, useState } from "react";
import type { FlushRecord } from "../../api/types";
import { flushChart, flushColor, flushDecimals, typicalSigma } from "../../lib/flush";

const BOX = { width: 296, height: 132, left: 40, right: 8, top: 8, bottom: 20 };

export function FlushChart({ record }: { record: FlushRecord }) {
  const c = useMemo(() => flushChart(record, BOX), [record]);
  const d = flushDecimals(typicalSigma(record));
  const [hover, setHover] = useState<number | null>(null);
  const lo = record.flush.lowest ?? 0,
    hi = record.flush.highest ?? 0;
  const h = hover !== null ? record.stations[hover] : null;
  // the ticks' decimals follow their step: 0.05 -> 2, 0.1 or 0.2 -> 1, 1 -> 0
  const step = c.yTicks.length > 1 ? c.yTicks[1] - c.yTicks[0] : 1;
  const td = Math.min(3, Math.max(0, Math.ceil(-Math.log10(step) - 1e-9)));
  const fmt = (v: number) => (Math.abs(v) < 1e-12 ? "0" : v.toFixed(td));
  const rgb = (x: [number, number, number]) => `rgb(${x.map((t) => Math.round(255 * t)).join(" ")})`;
  return (
    <div className="relative">
      <svg width={BOX.width} height={BOX.height} viewBox={`0 0 ${BOX.width} ${BOX.height}`} className="block max-w-full text-subtle" role="img" aria-label="The flush along the seam">
        {/* the frame and the ticks */}
        {c.yTicks.map((t) => (
          <g key={`y${t}`}>
            <line x1={BOX.left} x2={BOX.width - BOX.right} y1={c.y(t)} y2={c.y(t)} stroke="currentColor" strokeOpacity={0.18} />
            <text x={BOX.left - 4} y={c.y(t)} textAnchor="end" dominantBaseline="middle" fontSize={9.5} fill="currentColor" className="tnum">
              {fmt(t)}
            </text>
          </g>
        ))}
        {c.xTicks.map((t) => (
          <text key={`x${t}`} x={c.x(t)} y={BOX.height - 6} textAnchor="middle" fontSize={9.5} fill="currentColor" className="tnum">
            {Math.round(t)}
          </text>
        ))}
        {c.zero !== null ? <line x1={BOX.left} x2={BOX.width - BOX.right} y1={c.zero} y2={c.zero} stroke="currentColor" strokeOpacity={0.45} /> : null}
        {/* the ± bands, then the mean, then the readings */}
        {c.points.map((p) =>
          p.trusted && p.lo !== null && p.hi !== null ? <line key={`b${p.i}`} x1={p.x} x2={p.x} y1={p.hi} y2={p.lo} stroke="var(--color-accent, #4c8dff)" strokeOpacity={0.35} strokeWidth={3} strokeLinecap="round" /> : null,
        )}
        {c.mean !== null ? <line x1={BOX.left} x2={BOX.width - BOX.right} y1={c.mean} y2={c.mean} stroke="var(--color-accent, #4c8dff)" strokeWidth={1.2} strokeDasharray="4 3" /> : null}
        {c.points.map((p) =>
          p.y === null ? null : p.trusted ? (
            <circle key={`p${p.i}`} cx={p.x} cy={p.y} r={hover === p.i ? 3.6 : 2.5} fill={rgb(flushColor(record.stations[p.i].flush_mm, true, lo, hi))} stroke="rgb(17 20 25 / 0.55)" strokeWidth={0.6} />
          ) : (
            <circle key={`p${p.i}`} cx={p.x} cy={p.y} r={hover === p.i ? 3.6 : 2.8} fill="none" stroke="#8c8c8c" strokeWidth={1.3} strokeDasharray={p.clipped ? "1.5 1.2" : undefined} />
          ),
        )}
        {/* hover targets, one column per station */}
        {c.points.map((p, k) => {
          const prev = k ? c.points[k - 1].x : BOX.left,
            next = k + 1 < c.points.length ? c.points[k + 1].x : BOX.width - BOX.right;
          return <rect key={`h${p.i}`} x={(prev + p.x) / 2} width={Math.max(1, (next - prev) / 2)} y={BOX.top} height={BOX.height - BOX.top - BOX.bottom} fill="transparent" onMouseEnter={() => setHover(p.i)} onMouseLeave={() => setHover(null)} />;
        })}
        <text x={4} y={BOX.top + 2} fontSize={9} fill="currentColor" dominantBaseline="hanging">
          mm
        </text>
      </svg>
      <div className="mt-0.5 flex justify-between text-[10.5px] text-subtle">
        <span>along the seam (mm)</span>
        <span className="flex items-center gap-2">
          <span className="flex items-center gap-1">
            <span className="inline-block h-0 w-3 border-t border-dashed border-accent" aria-hidden /> mean
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full border border-[#8c8c8c]" aria-hidden /> not trusted
          </span>
        </span>
      </div>
      {h ? (
        <div className="pointer-events-none absolute left-10 top-1 max-w-[250px] rounded-md border border-line bg-panel/95 px-2 py-1 text-[11px] leading-4 text-fg shadow-md">
          <div className="font-mono tnum">
            {Math.round(h.position_mm)} mm:{" "}
            {h.flush_mm === null ? "no reading" : `${h.flush_mm > 0 ? "+" : h.flush_mm < 0 ? "−" : ""}${Math.abs(h.flush_mm).toFixed(d)}${h.flush_sigma_mm !== null ? ` ± ${h.flush_sigma_mm.toFixed(d)}` : ""} mm`}
          </div>
          {!h.flush_trusted ? <div className="text-warn">Not trusted{h.notes.length ? `: ${h.notes.join(" ")}` : ""}</div> : null}
        </div>
      ) : null}
    </div>
  );
}
