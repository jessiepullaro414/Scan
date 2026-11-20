/**
 * The Check panel (APP_API.md "M5 third slice: areas that need another pass"): the user types the
 * tolerance the part needs; the panel says in one sentence how much of the surface meets it and lists
 * the areas that don't, largest first. A click on an area lights it on the mesh and flies the camera
 * to it (useCheck.ts). It says what the answer rests on, and why there is none when the mesh carries
 * no confidence.
 *
 * FEATURE_PLAN N1: once the areas are shown, each gets why it is weak (in plain words, lib/checkWhy.ts) and
 * a small side view of where to hold the scanner, or why there is no such place; "Show where to hold the
 * scanner" draws the scanner there in the 3D view (CheckMark.tsx). Under the list, a short plan: a few
 * places that between them see the largest areas.
 */
import { CircleDashed, CircleExclamationPoint, RefreshCw, ScanLine, SearchCheck, Target, TriangleExclamationPoint } from "lucide-react";
import { useEffect, useId, useState, type ReactNode } from "react";
import { describeError } from "../../api/http";
import type { CheckArea, CheckPlanPose, CheckResult, CheckView, CheckWhy, CheckWhyArea } from "../../api/types";
import { alsoWords, planTitle, planWords, REASON_TITLE, reasonWords, sideSketch, unknownWords } from "../../lib/checkWhy";
import { Button } from "../../components/ui/Button";
import { Spinner } from "../../components/ui/Feedback";
import { Callout } from "../../components/ui/Layout";
import {
  checkNotes,
  checkSentence,
  fmtAreaSize,
  fmtExtent,
  fmtShare,
  fmtTolerance,
  hasAnswer,
  kindMeaning,
  kindName,
  TOLERANCE_MAX_MM,
  TOLERANCE_MIN_MM,
  validTolerance,
} from "../../lib/check";
import { cn } from "../../lib/util";
import { NOT_MEASURED } from "../../three/confidence";
import { CHECK_COLOR, type CheckState } from "./useCheck";
import { fmtSigma, useTheme } from "./ViewerCanvas";

export const CHECK_ICON = SearchCheck;

/** Areas shown before "Show all". */
const FIRST = 6;
const STEP_MM = 0.05;

const RESTS_ON =
  "This rests on the scan's own sigma: how much its measurements of each point scatter, and how close the point is to an edge, a gap or the border of the scan. " +
  "It knows nothing of a calibration or scale error, or of a tracking error that shifts a whole area, so the real error can be larger. " +
  "It has been checked on simulated scans only, not on real parts. There, over 99 % of what it passed was good, and much of what it listed was fine too.";

/** The tolerance field: type freely; Enter, the arrow keys or leaving the field set it. */
function ToleranceField({ id, value, onChange, disabled }: { id: string; value: number; onChange: (mm: number) => void; disabled?: boolean }) {
  const [text, setText] = useState(fmtTolerance(value));
  useEffect(() => setText(fmtTolerance(value)), [value]);
  const commit = () => {
    const typed = Number(text.trim().replace(",", "."));
    const v = text.trim() && Number.isFinite(typed) && typed > 0 ? validTolerance(typed) : value;
    setText(fmtTolerance(v));
    if (v !== value) onChange(v);
  };
  return (
    <div className="relative">
      <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-sm text-subtle" aria-hidden>
        ±
      </span>
      <input
        id={id}
        type="text"
        inputMode="decimal"
        autoComplete="off"
        spellCheck={false}
        value={text}
        disabled={disabled}
        aria-describedby={`${id}-help`}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit(); // and the form asks
          else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
            e.preventDefault();
            onChange(validTolerance(value + (e.key === "ArrowUp" ? STEP_MM : -STEP_MM)));
          }
        }}
        className={cn(
          "h-8 w-full rounded-md border border-line bg-surface-2 pl-7 pr-10 font-mono text-[12.5px] text-fg tnum transition-[border-color,box-shadow] duration-150",
          "hover:border-line-strong focus:border-accent focus:shadow-[0_0_0_3px_var(--c-accent-soft)] focus:outline-none disabled:opacity-50",
        )}
      />
      <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-xs text-subtle" aria-hidden>
        mm
      </span>
    </div>
  );
}

/** How the surface splits: meets the tolerance, uncertain, not measured. The sentence above says it in words. */
function ShareBar({ result }: { result: CheckResult }) {
  const grey = NOT_MEASURED[useTheme()];
  const ok = result.share_ok ?? 0;
  const unc = result.share_uncertain ?? 0;
  const nm = result.share_not_measured ?? 0;
  return (
    <div className="mt-2 flex h-2 overflow-hidden rounded-full bg-surface-3" aria-hidden>
      <span className="bg-ok" style={{ width: `${ok * 100}%` }} />
      <span style={{ width: `${unc * 100}%`, background: CHECK_COLOR }} />
      <span style={{ width: `${nm * 100}%`, background: `repeating-linear-gradient(135deg, ${grey} 0 3px, color-mix(in srgb, ${grey} 80%, black) 3px 6px)` }} />
    </div>
  );
}

/** Where to hold the scanner, from the side: the surface, its normal, the scanner leaning off it, the line of sight. */
function SideView({ view }: { view: CheckView }) {
  const pose = view.pose;
  if (!pose) return null;
  const s = sideSketch(pose.tilt_deg);
  const tip = `Seen from the side: the scanner ${Math.round(pose.distance_mm)} mm from the area, ${pose.tilt_deg < 0.5 ? "square on to it" : `${Math.round(pose.tilt_deg)}° off square`}. The 3D view shows it in place.`;
  return (
    <svg viewBox="0 0 120 76" width={120} height={76} role="img" aria-label={tip} className="shrink-0 rounded-md border border-line bg-surface-3/40">
      <title>{tip}</title>
      <line x1={s.surface[0]} y1={s.surface[1]} x2={s.surface[2]} y2={s.surface[3]} stroke="currentColor" strokeOpacity={0.45} strokeWidth={2} />
      <line x1={s.patch[0]} y1={s.patch[1]} x2={s.patch[2]} y2={s.patch[3]} stroke={CHECK_COLOR} strokeWidth={3} strokeLinecap="round" />
      <line x1={s.normal[0]} y1={s.normal[1]} x2={s.normal[2]} y2={s.normal[3]} stroke="currentColor" strokeOpacity={0.35} strokeDasharray="2 2" />
      <line x1={s.sight[0]} y1={s.sight[1]} x2={s.sight[2]} y2={s.sight[3]} stroke={CHECK_COLOR} strokeOpacity={0.8} strokeDasharray="3 2" />
      <g transform={`translate(${s.scanner.x} ${s.scanner.y}) rotate(${s.scanner.angleDeg})`}>
        <rect x={-13} y={-6} width={26} height={9} rx={2} fill="var(--c-surface-2, #1b1f26)" stroke={CHECK_COLOR} strokeWidth={1.5} />
        <circle cx={-7} cy={3} r={1.6} fill={CHECK_COLOR} />
        <circle cx={7} cy={3} r={1.6} fill={CHECK_COLOR} />
      </g>
      <text x={s.label.x} y={s.label.y} fontSize={9} fill="currentColor" fillOpacity={0.7}>
        {pose.tilt_deg < 0.5 ? "square" : `${Math.round(pose.tilt_deg)}°`}
      </text>
    </svg>
  );
}

/** Why the area is weak, and where to scan it from (FEATURE_PLAN N1). */
function WhyBlock({ w, workingMm, onShow }: { w: CheckWhyArea; workingMm: number | null; onShow: () => void }) {
  const also = alsoWords(w);
  const unknown = w.reason === "unknown" ? unknownWords(w) : [];
  return (
    <div className="mt-1.5 pl-7 text-[11.5px] leading-4">
      <div className="font-semibold text-fg">{REASON_TITLE[w.reason]}</div>
      <div className="text-muted">{reasonWords(w, workingMm)}</div>
      {also ? <div className="mt-0.5 text-subtle">{also}</div> : null}
      {unknown.map((u) => (
        <div key={u} className="mt-0.5 text-subtle">
          {u}
        </div>
      ))}
      {w.view.ok && w.view.pose ? (
        <div className="mt-1.5 flex items-start gap-2">
          <SideView view={w.view} />
          <div className="min-w-0">
            <div className="text-muted">{w.view.text}</div>
            <Button size="xs" variant="ghost" icon={ScanLine} className="mt-1" onClick={onShow}>
              Show where to hold the scanner
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-1 text-subtle">{w.view.text}</div>
      )}
    </div>
  );
}

/** The short plan: a few places to hold the scanner that between them see the largest areas. */
function PlanBlock({ plan, onPick }: { plan: CheckPlanPose[]; onPick: (p: CheckPlanPose) => void }) {
  if (!plan.length) return null;
  return (
    <div className="mt-3 rounded-lg border border-line bg-surface-2/60 px-3 py-2">
      <div className="text-xs font-semibold text-fg">{planTitle(plan.length)}</div>
      <ol className="mt-1 space-y-1">
        {plan.map((p, i) => (
          <li key={i}>
            <button
              type="button"
              onClick={() => onPick(p)}
              title="Show this place in the 3D view"
              className="flex w-full items-start gap-2 rounded-md px-1 py-0.5 text-left text-[11.5px] leading-4 text-muted transition-colors duration-150 hover:bg-surface-3 hover:text-fg focus-visible:outline-2 focus-visible:outline-accent"
            >
              <span className="grid h-4 min-w-4 place-items-center rounded-full bg-surface-3 px-1 text-[10px] font-bold text-fg">{i + 1}</span>
              <span>{planWords(p)}</span>
            </button>
          </li>
        ))}
      </ol>
      <div className="mt-1 text-[11px] leading-4 text-subtle">A pass from each, at the distance shown, then Check again.</div>
    </div>
  );
}

/** What the reasons rest on, when part of it is missing or was cut down. */
function whyNotes(why: CheckWhy): string[] {
  const out: string[] = [];
  const cam = why.camera_path;
  if (!cam.available && cam.why) out.push(`Whether an area was seen too steeply or from too far can't be told here: ${cam.why}.`);
  else if (cam.frames_used < cam.frames_total)
    out.push(`The reasons look at ${cam.frames_used.toLocaleString()} of the scan's ${cam.frames_total.toLocaleString()} frames, evenly spread.`);
  return out;
}

function AreaRow({
  area,
  n,
  on,
  result,
  onSelect,
  why,
  whyLoading,
  workingMm,
  onShowPose,
}: {
  area: CheckArea;
  n: number;
  on: boolean;
  result: CheckResult;
  onSelect: () => void;
  why: CheckWhyArea | null;
  whyLoading: boolean;
  workingMm: number | null;
  onShowPose: () => void;
}) {
  const Icon = area.kind === "not_measured" ? CircleDashed : Target;
  const meaning = kindMeaning(area.kind, result.tolerance_mm, result.k);
  const rows: [string, string, string][] = [["Share of the surface", fmtShare(area.share), "How much of the whole surface this area is."]];
  if (area.sigma_median_mm !== null)
    rows.push(["Median sigma", fmtSigma(area.sigma_median_mm), "Half of the measured points of this area are surer than this (1σ, from the scan itself)."]);
  if (area.sigma_worst_mm !== null) rows.push(["Worst sigma", fmtSigma(area.sigma_worst_mm), "The least sure measured point of this area (1σ)."]);
  if (area.sigma_median_mm === null && area.sigma_worst_mm === null) rows.push(["Sigma", "none", "No point of this area was measured."]);
  return (
    <li>
      <button
        type="button"
        aria-pressed={on}
        title={on ? "Lit on the mesh: click to turn it off" : "Show it on the mesh"}
        onClick={onSelect}
        className={cn(
          "block w-full rounded-lg border bg-surface-2/60 px-3 py-2 text-left transition-colors duration-150 hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-accent",
          !on && "border-line",
        )}
        style={on ? { borderColor: CHECK_COLOR } : undefined}
      >
        <span className="flex items-start gap-2">
          <span
            className={cn("grid h-5 min-w-5 place-items-center rounded-full px-1 text-[10.5px] font-bold", !on && "bg-surface-3 text-muted")}
            style={on ? { background: CHECK_COLOR, color: "#0b0d11" } : undefined}
          >
            {n}
          </span>
          <span className="flex min-w-0 flex-1 flex-wrap items-baseline justify-between gap-x-2">
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-fg">
              <Icon size={13} className="shrink-0 text-muted" aria-hidden />
              {kindName(area.kind)}
            </span>
            <span className="font-mono text-[12.5px] font-semibold text-fg tnum">
              {fmtAreaSize(area.area_mm2)}
              <span className="font-normal text-muted"> · {fmtExtent(area.extent_mm)} across</span>
            </span>
          </span>
        </span>
        <span className="mt-1 block pl-7 text-[11.5px] leading-4 text-muted">{meaning}</span>
        <span className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 pl-7 text-[11.5px]">
          {rows.map(([k, v, tip]) => (
            <span key={k} className="contents" title={tip}>
              <span className="text-subtle">{k}</span>
              <span className="text-right font-mono text-fg tnum">{v}</span>
            </span>
          ))}
        </span>
      </button>
      {why ? (
        <WhyBlock w={why} workingMm={workingMm} onShow={onShowPose} />
      ) : whyLoading ? (
        <div className="mt-1 pl-7 text-[11.5px] leading-4 text-subtle">Finding out why, and where to scan it from…</div>
      ) : null}
    </li>
  );
}

export function CheckPanel({
  check,
  ready,
  hasSigma,
  remake,
  holes,
}: {
  check: CheckState;
  /** A mesh is on screen. */
  ready: boolean;
  /** That mesh carries each point's sigma (its confidence). */
  hasSigma: boolean;
  /** What makes the mesh again, for a mesh without confidence (a button). */
  remake?: ReactNode;
  /** The mesh's open holes, when known: how many, and a way to see them. */
  holes?: { count: number; action?: ReactNode } | null;
}) {
  const id = useId();
  const [all, setAll] = useState(false);
  const { result, error, loading } = check;
  useEffect(() => setAll(false), [result]);

  if (!ready) return <div className="text-sm text-muted">Available once the mesh is loaded.</div>;

  // nothing to check against: say why, and what gets it
  if (!hasSigma || (result && !hasAnswer(result)))
    return (
      <Callout tone="neutral" icon={CircleExclamationPoint} title="Check can't be used on this mesh" actions={remake}>
        Check compares your tolerance with how sure the scan is of each point (its confidence), and this mesh doesn't carry that: it was made without a graphics card
        (GPU), or by an earlier version of the app. Make the mesh again on a computer with a graphics card, and Check works on it.
      </Callout>
    );

  const shown = result ? (all ? result.areas : result.areas.slice(0, FIRST)) : [];
  const why = check.why.why;
  const notes = result ? checkNotes(result) : [];
  const waitingFor = `± ${fmtTolerance(check.tolerance)} mm`;
  return (
    <>
      <form
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          check.run();
        }}
      >
        <div className="min-w-0 flex-1">
          <label htmlFor={id} className="mb-1.5 block text-xs font-medium text-muted">
            Tolerance your part needs
          </label>
          <ToleranceField id={id} value={check.tolerance} onChange={check.setTolerance} />
        </div>
        <Button type="submit" size="sm" variant={check.on ? "secondary" : "primary"} icon={CHECK_ICON} className="h-8" loading={loading}>
          {check.on ? "Check again" : "Check"}
        </Button>
      </form>
      <div id={`${id}-help`} className="mt-1.5 text-[11.5px] leading-4 text-subtle">
        {check.on
          ? `From ${fmtTolerance(TOLERANCE_MIN_MM)} to ${fmtTolerance(TOLERANCE_MAX_MM)} mm. A new tolerance checks again by itself.`
          : "How far off the part may be, for example 0.3 for a part that has to fit. Check then tells how much of the scan is good enough, and which areas to scan again."}
      </div>

      {error ? (
        error.kind === "changed" ? (
          <Callout
            className="mt-3"
            tone="warn"
            icon={TriangleExclamationPoint}
            title="The mesh changed since it was opened"
            actions={
              <Button size="sm" icon={RefreshCw} onClick={check.reload}>
                Reload the mesh
              </Button>
            }
          >
            The engine checked a newer version than the one on screen, so its areas would not line up. Reload the mesh: it is checked again by itself.
          </Callout>
        ) : (
          <Callout
            className="mt-3"
            tone="danger"
            icon={CircleExclamationPoint}
            title="Couldn't check the surface"
            actions={
              <Button size="sm" icon={RefreshCw} onClick={check.run}>
                Try again
              </Button>
            }
          >
            {describeError(error.error).detail}
            {describeError(error.error).hint ? <span className="block text-subtle">{describeError(error.error).hint}</span> : null}
          </Callout>
        )
      ) : null}

      <div className="mt-3" aria-live="polite" aria-busy={loading}>
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-fg" role="status">
            <Spinner size={14} className="text-accent" />
            Checking the surface at {waitingFor}…
          </div>
        ) : result ? (
          <p className="text-sm font-medium leading-5 text-fg">{checkSentence(result)}</p>
        ) : null}
      </div>

      {result ? (
        <div className={cn("transition-opacity duration-150", loading && "opacity-50")}>
          <ShareBar result={result} />
          {notes.map((n) => (
            <div key={n} className="mt-1.5 text-[11.5px] leading-4 text-muted">
              {n}
            </div>
          ))}
          {result.areas.length ? (
            <>
              <ol className="mt-3 space-y-2" aria-label="Areas that need another pass, largest first">
                {shown.map((a, i) => (
                  <AreaRow
                    key={i}
                    area={a}
                    n={i + 1}
                    on={check.lit?.index === i}
                    result={result}
                    onSelect={() => check.select(i)}
                    why={why?.areas[i] ?? null}
                    whyLoading={check.why.loading}
                    workingMm={why?.given.working_mm ?? null}
                    onShowPose={() => check.select(i, { showPose: true })}
                  />
                ))}
              </ol>
              {result.areas.length > FIRST ? (
                <Button size="xs" variant="ghost" className="mt-2" aria-expanded={all} onClick={() => setAll((v) => !v)}>
                  {all ? "Show the largest only" : `Show all ${result.areas.length} areas`}
                </Button>
              ) : null}
              <div className="mt-2 text-[11.5px] leading-4 text-subtle">Click an area to see it on the mesh.</div>
              {why ? <PlanBlock plan={why.plan} onPick={(p) => check.select(p.area, { pose: p.pose, showPose: true })} /> : null}
              {why
                ? whyNotes(why).map((t) => (
                    <div key={t} className="mt-1.5 text-[11.5px] leading-4 text-subtle">
                      {t}
                    </div>
                  ))
                : null}
              {check.why.failed ? (
                <div className="mt-1.5 text-[11.5px] leading-4 text-subtle">Why each area is weak, and where to scan it from, could not be worked out this time.</div>
              ) : null}
            </>
          ) : null}
          {holes && holes.count > 0 ? (
            <div className="mt-3 rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-[11.5px] leading-4 text-muted">
              <span className="font-semibold text-fg">Not scanned:</span> this mesh also has {holes.count === 1 ? "1 open hole" : `${holes.count} open holes`}, where there is no surface at
              all. {holes.count === 1 ? "It is" : "They are"} not in this list.
              {holes.action ? <div className="mt-1.5">{holes.action}</div> : null}
            </div>
          ) : null}
          <div className="mt-3 text-[11px] leading-4 text-subtle">{RESTS_ON}</div>
        </div>
      ) : null}
    </>
  );
}
