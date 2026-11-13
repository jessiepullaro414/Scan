/**
 * The deviation map's legend: the diverging colour scale (blue inward, grey unchanged, red outward)
 * with the limit it saturates at (the user's choice), the "new surface" stripes, how much of the
 * surface is within the limit, the largest and 95th-percentile move, and what it compares with.
 * While the engine computes the map, or when there is none to show, it says so.
 */
import { useMemo } from "react";
import { Spinner } from "../../components/ui/Feedback";
import { Segmented } from "../../components/ui/Form";
import { DEVIATION_LIMITS, deviationCss, fmtLimit, fmtMove, shareWithin } from "../../lib/deviation";
import { fmtPct } from "../../lib/format";
import { cn } from "../../lib/util";
import { useUi } from "../../store/ui";
import { NOT_MEASURED } from "../../three/confidence";
import type { DeviationState } from "./useDeviation";
import { useTheme } from "./ViewerCanvas";

const MINUS = "−";

export function DeviationLegend({ state, note, className }: { state: DeviationState; note?: string; className?: string }) {
  const theme = useTheme();
  const limit = useUi((s) => s.deviationLimit);
  const setLimit = useUi((s) => s.setDeviationLimit);
  const data = state.phase === "ready" ? state.data : null;
  const within = useMemo(() => (data ? shareWithin(data.values, limit) : null), [data, limit]);
  const grey = NOT_MEASURED[theme];
  const lim = fmtLimit(limit);

  return (
    <div className={cn("w-[250px] text-[11px] leading-4 text-muted", className)} role="group" aria-label="Deviation map legend">
      <div className="mb-1.5 flex items-baseline justify-between gap-2">
        <span className="font-semibold text-fg">Deviation</span>
        <span className="text-subtle">move from the measured mesh, mm</span>
      </div>
      {note ? (
        <div className="text-fg">{note}</div>
      ) : state.phase === "loading" ? (
        <div className="flex items-center gap-2 text-fg" role="status">
          <Spinner size={13} />
          Computing the deviation…
        </div>
      ) : state.phase === "unavailable" ? (
        <div className="text-fg" role="status">
          {state.message.replace(/[.s]+$/, "")}. Showing the plain surface.
        </div>
      ) : data ? (
        <>
          <div className="h-2.5 rounded-full" style={{ background: deviationCss(theme) }} aria-hidden />
          <div className="relative mt-1 h-3.5 font-mono text-[10.5px] text-subtle tnum" aria-hidden>
            <span className="absolute left-0">
              {MINUS}
              {lim}
            </span>
            <span className="absolute left-1/2 -translate-x-1/2">0</span>
            <span className="absolute right-0">+{lim}</span>
          </div>
          <div className="flex justify-between text-subtle" aria-hidden>
            <span>inward</span>
            <span>outward</span>
          </div>
          <div className="mt-1.5 flex items-center justify-between gap-2">
            <span>Scale limit (mm)</span>
            <Segmented<string>
              label="Scale limit in mm"
              size="sm"
              value={fmtLimit(limit)}
              onChange={(v) => setLimit(Number(v))}
              options={DEVIATION_LIMITS.map((l) => ({ value: fmtLimit(l), label: fmtLimit(l), title: `Strongest colour at ${MINUS}${fmtLimit(l)} and +${fmtLimit(l)} mm` }))}
            />
          </div>
          {data.info.stats.new > 0 ? (
            <div className="mt-1.5 flex items-center gap-1.5">
              <span
                className="h-2.5 w-5 shrink-0 rounded-sm ring-1 ring-line"
                style={{ background: `repeating-linear-gradient(135deg, ${grey} 0 3px, color-mix(in srgb, ${grey} 80%, black) 3px 6px)` }}
                aria-hidden
              />
              New surface (filled holes): nothing to compare
            </div>
          ) : null}
          <div className="mt-1.5 text-fg">
            {within === null ? (
              "Nothing to compare yet."
            ) : (
              <>
                <span className="tnum">{fmtPct(within, within > 0.995 && within < 1 ? 1 : 0)}</span> of the surface within ±{lim} mm
              </>
            )}
          </div>
          {data.info.stats.n > 0 ? (
            <div className="text-fg">
              Largest move <span className="tnum">{fmtMove(data.info.stats.max_mm)}</span>, 95 % within <span className="tnum">{fmtMove(data.info.stats.p95_mm)}</span>
            </div>
          ) : null}
          {data.info.stats.new > 0 ? (
            <div className="text-fg">
              <span className="tnum">{fmtNew(data.info.stats.new / (data.info.stats.n + data.info.stats.new))}</span> of the surface is new
            </div>
          ) : null}
          <div className="mt-1.5 text-subtle">Against the mesh as measured, before the edits.</div>
          <span className="sr-only">Blue: moved inward by up to {lim} mm. Grey: unchanged. Red: moved outward by up to {lim} mm. Striped: new surface.</span>
        </>
      ) : null}
    </div>
  );
}

/** A share of new surface: "0.4 %" for the small ones, "<0.1 %" for the tiniest. */
function fmtNew(x: number): string {
  return x < 0.0005 ? "<0.1 %" : fmtPct(x, x < 0.01 ? 1 : 0);
}
