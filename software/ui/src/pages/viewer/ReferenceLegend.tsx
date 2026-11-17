/**
 * The "Against the reference" shading's legend: the deviation map's colour scale (blue inside the reference, grey on
 * it, orange-red outside), saturating at plus and minus the tolerance; the grey of surface that is not on the
 * reference and the stripes of surface that was not measured; and the answer's sentence. While the engine works the
 * comparison out, or when there is none to show, it says so.
 */
import { Spinner } from "../../components/ui/Feedback";
import { fmtTolerance } from "../../lib/check";
import { deviationCss } from "../../lib/deviation";
import { OFF_REFERENCE, toleranceSentence } from "../../lib/reference";
import { cn } from "../../lib/util";
import { NOT_MEASURED } from "../../three/confidence";
import type { ToleranceAnswer } from "../../api/types";
import type { RefMapState } from "./useComparison";
import { useTheme } from "./ViewerCanvas";

const MINUS = "−";

export function ReferenceLegend({ state, tolerance, answer, note, className }: { state: RefMapState; tolerance: number; answer: ToleranceAnswer | null; note?: string; className?: string }) {
  const theme = useTheme();
  const grey = NOT_MEASURED[theme];
  const lim = fmtTolerance(tolerance);
  return (
    <div className={cn("w-[260px] text-[11px] leading-4 text-muted", className)} role="group" aria-label="Comparison with the reference: legend">
      <div className="mb-1.5 flex items-baseline justify-between gap-2">
        <span className="font-semibold text-fg">Against the reference</span>
        <span className="text-subtle">deviation, mm</span>
      </div>
      {note ? (
        <div className="text-fg">{note}</div>
      ) : state.phase === "loading" ? (
        <div className="flex items-center gap-2 text-fg" role="status">
          <Spinner size={13} />
          Comparing the scan with the reference…
        </div>
      ) : state.phase === "unavailable" ? (
        <div className="text-fg" role="status">
          {state.message.replace(/[.\s]+$/, "")}. Showing the plain surface.
        </div>
      ) : state.phase === "ready" ? (
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
            <span>inside it</span>
            <span>outside it</span>
          </div>
          <div className="mt-1.5 flex items-center gap-1.5">
            <span className="h-2.5 w-5 shrink-0 rounded-sm ring-1 ring-line" style={{ background: OFF_REFERENCE[theme] }} aria-hidden />
            Not on the reference (a table, a clamp): not compared
          </div>
          <div className="mt-1 flex items-center gap-1.5">
            <span
              className="h-2.5 w-5 shrink-0 rounded-sm ring-1 ring-line"
              style={{ background: `repeating-linear-gradient(135deg, ${grey} 0 3px, color-mix(in srgb, ${grey} 80%, black) 3px 6px)` }}
              aria-hidden
            />
            Not measured: not compared
          </div>
          {answer ? <div className="mt-1.5 font-medium text-fg">{toleranceSentence(answer)}</div> : null}
          <div className="mt-1 text-subtle">The strongest colours are at the tolerance (± {lim} mm): set it in the Compare panel.</div>
          <span className="sr-only">
            Blue: inside the reference by up to {lim} mm. Light grey: on it. Red: outside it by up to {lim} mm. Dark grey: not on the reference. Striped: not measured.
          </span>
        </>
      ) : (
        <div className="text-fg">Align the scan with a reference in the Compare panel first.</div>
      )}
    </div>
  );
}
