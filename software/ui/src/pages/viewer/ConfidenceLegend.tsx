/**
 * The confidence map's legend: the colour scale (each point's 1-sigma uncertainty, log scale),
 * the "not measured" stripes, and how much of the shown surface is within 0.1 / 0.3 mm.
 */
import { useMemo } from "react";
import type { BufferGeometry } from "three";
import { fmtPct } from "../../lib/format";
import { cn } from "../../lib/util";
import { NOT_MEASURED, rampCss, SIGMA_TICKS, sigmaPos, sigmaShares } from "../../three/confidence";
import { useTheme } from "./ViewerCanvas";

export function ConfidenceLegend({ geometry, className }: { geometry: BufferGeometry | null; className?: string }) {
  const theme = useTheme();
  const grey = NOT_MEASURED[theme];
  const stats = useMemo(() => sigmaShares(geometry), [geometry]);
  return (
    <div className={cn("w-[250px] text-[11px] leading-4 text-muted", className)} role="group" aria-label="Confidence map legend">
      <div className="mb-1.5 flex items-baseline justify-between gap-2">
        <span className="font-semibold text-fg">Uncertainty</span>
        <span className="text-subtle">1σ of each point, mm</span>
      </div>
      <div className="h-2.5 rounded-full" style={{ background: rampCss(theme) }} aria-hidden />
      <div className="relative mt-1 h-3.5 font-mono text-[10.5px] text-subtle tnum" aria-hidden>
        {SIGMA_TICKS.map((t, i) => (
          <span
            key={t}
            className={cn("absolute", i === 0 ? "" : i === SIGMA_TICKS.length - 1 ? "-translate-x-full" : "-translate-x-1/2")}
            style={{ left: `${sigmaPos(t) * 100}%` }}
          >
            {i === SIGMA_TICKS.length - 1 ? `≥${t}` : t}
          </span>
        ))}
      </div>
      <div className="mt-1.5 flex items-center gap-1.5">
        <span
          className="h-2.5 w-5 shrink-0 rounded-sm ring-1 ring-line"
          style={{ background: `repeating-linear-gradient(135deg, ${grey} 0 3px, color-mix(in srgb, ${grey} 80%, black) 3px 6px)` }}
          aria-hidden
        />
        Not measured (filled holes)
      </div>
      {stats ? (
        <div className="mt-1.5 text-fg">
          <span className="tnum">{fmtPct(stats.within_0_1)}</span> of the surface within 0.1 mm, <span className="tnum">{fmtPct(stats.within_0_3)}</span> within 0.3 mm
          {stats.not_measured >= 0.0005 ? (
            <>
              , <span className="tnum">{fmtPct(stats.not_measured, stats.not_measured < 0.01 ? 1 : 0)}</span> not measured
            </>
          ) : null}
        </div>
      ) : null}
      <span className="sr-only">Light blue: under 0.02 mm. Dark blue: 1 mm or more.</span>
    </div>
  );
}
