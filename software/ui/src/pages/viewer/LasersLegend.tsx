/**
 * The Lasers shading's legend: the diverging colour scale (blue: the lasers see the surface further in
 * than the mesh, grey: they agree, red: further out) with the limit it saturates at (the user's choice),
 * the grey of the surface no laser line crossed, the numbers in a few sentences (lib/lasers.ts) and what
 * the comparison is good for. While the engine compares, when the scan has no laser points, or when the
 * read failed, it says so, with what to do next.
 */
import { RefreshCw } from "lucide-react";
import { Button } from "../../components/ui/Button";
import { Spinner } from "../../components/ui/Feedback";
import { Segmented } from "../../components/ui/Form";
import { deviationCss, fmtLimit } from "../../lib/deviation";
import { hasLaserValues, LASER_LIMITS, LASER_MEANING, LASER_NO_VALUE, LASER_NONE, LASER_NONE_HINT, laserSentences } from "../../lib/lasers";
import { href, paths } from "../../lib/router";
import { cn } from "../../lib/util";
import { useUi } from "../../store/ui";
import type { LaserState } from "./useLasers";
import { useTheme } from "./ViewerCanvas";

const MINUS = "−";

export function LasersLegend({
  state,
  onRetry,
  onReload,
  note,
  className,
}: {
  state: LaserState;
  /** Ask the engine again (after a failed read). */
  onRetry: () => void;
  /** Read the mesh again (when the one on screen is not the engine's any more). */
  onReload: () => void;
  note?: string;
  className?: string;
}) {
  const theme = useTheme();
  const limit = useUi((s) => s.laserLimit);
  const setLimit = useUi((s) => s.setLaserLimit);
  const info = state.phase === "ready" ? state.data.info : null;
  const lim = fmtLimit(limit);

  return (
    <div className={cn("w-[300px] text-[11px] leading-4 text-muted", className)} role="group" aria-label="Lasers shading legend">
      <div className="mb-1.5 flex items-baseline justify-between gap-2">
        <span className="font-semibold text-fg">Lasers</span>
        <span className="text-subtle">the laser lines against the mesh, mm</span>
      </div>
      {note ? (
        <div className="text-fg">{note}</div>
      ) : state.phase === "loading" ? (
        <div className="flex items-center gap-2 text-fg" role="status">
          <Spinner size={13} />
          Comparing the mesh with the lasers…
        </div>
      ) : state.phase === "none" ? (
        <div role="status">
          <div className="text-fg">{LASER_NONE}</div>
          <div className="mt-1">
            {LASER_NONE_HINT}{" "}
            <a href={href(paths.setup("lasers"))} className="font-medium text-accent hover:underline">
              Calibrate the lasers
            </a>
          </div>
          <div className="mt-1 text-subtle">Showing the plain surface.</div>
        </div>
      ) : state.phase === "failed" ? (
        <div role="status">
          <div className="text-fg">{state.message}</div>
          <div className="mt-1">{state.hint}</div>
          <div className="mt-2 flex items-center justify-between gap-2">
            <span className="text-subtle">Showing the plain surface.</span>
            <Button size="xs" icon={RefreshCw} onClick={state.stale ? onReload : onRetry}>
              {state.stale ? "Reload the mesh" : "Try again"}
            </Button>
          </div>
        </div>
      ) : info && !hasLaserValues(info) ? (
        <div role="status">
          {laserSentences(info).map((s, i) => (
            <div key={s} className={cn("text-fg", i > 0 && "mt-1")}>
              {s}
            </div>
          ))}
          <div className="mt-1 text-subtle">Showing the plain surface.</div>
        </div>
      ) : info ? (
        <>
          <div className="h-2.5 rounded-full" style={{ background: deviationCss(theme) }} aria-hidden />
          <div className="relative mt-1 h-4 text-subtle" aria-hidden>
            <span className="absolute left-0">
              <span className="font-mono text-[10.5px] tnum">
                {MINUS}
                {lim}
              </span>{" "}
              lasers further in
            </span>
            <span className="absolute left-1/2 -translate-x-1/2 font-mono text-[10.5px] tnum">0</span>
            <span className="absolute right-0">
              lasers further out <span className="font-mono text-[10.5px] tnum">+{lim}</span>
            </span>
          </div>
          <div className="mt-1.5 flex items-center justify-between gap-2">
            <span>Scale limit (mm)</span>
            <Segmented<string>
              label="Scale limit in mm"
              size="sm"
              value={fmtLimit(limit)}
              onChange={(v) => setLimit(Number(v))}
              options={LASER_LIMITS.map((l) => ({ value: fmtLimit(l), label: fmtLimit(l), title: `Strongest colour at ${MINUS}${fmtLimit(l)} and +${fmtLimit(l)} mm` }))}
            />
          </div>
          <div className="mt-1.5 flex items-center gap-1.5">
            <span className="h-2.5 w-5 shrink-0 rounded-sm ring-1 ring-line" style={{ background: LASER_NO_VALUE[theme] }} aria-hidden />
            No laser line here: nothing to compare
          </div>
          {laserSentences(info).map((s) => (
            <div key={s} className="mt-1.5 text-fg">
              {s}
            </div>
          ))}
          <div className="mt-1.5 text-subtle">{LASER_MEANING}</div>
          <span className="sr-only">
            Blue: the lasers see the surface further in than the mesh, by up to {lim} mm. Light grey: they agree. Red: the lasers see it further out, by up to {lim} mm. Darker grey: no laser
            line crossed there.
          </span>
        </>
      ) : null}
    </div>
  );
}
