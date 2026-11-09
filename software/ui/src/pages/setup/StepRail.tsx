/** The wizard's left rail: every step with its state and summary; compact (dots only) on the capture screens. */
import { Compass, Flag, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import type { CalibKind, SetupStep, StepState } from "../../api/types";
import { Tooltip } from "../../components/ui/Popover";
import { fmtRelative } from "../../lib/format";
import { href, paths, type SetupPage } from "../../lib/router";
import { firstUnfinished, setupProgress, STATE_LABEL, STEP_META, STEP_ORDER } from "../../lib/setup";
import { cn, useTicker } from "../../lib/util";
import { isSessionActive, useCalib } from "../../store/calib";
import { useSetup } from "../../store/setup";
import { StepDot } from "./common";

function ProgressRing({ value, size = 36 }: { value: number; size?: number }) {
  const r = size / 2 - 3;
  const c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden className="shrink-0 -rotate-90">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--c-surface-3)" strokeWidth="4" />
      {value > 0 ? (
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--c-ok)"
          strokeWidth="4"
          strokeLinecap="round"
          strokeDasharray={`${c * value} ${c}`}
          className="transition-[stroke-dasharray] duration-500"
        />
      ) : null}
    </svg>
  );
}

type RailItem = { page: SetupPage | null; title: string; state: StepState | "neutral"; summary: string | null; when: string | null; n?: number };

export function StepRail({
  current,
  compact,
  onToggle,
  canToggle,
}: {
  current: SetupPage | null;
  compact: boolean;
  onToggle?: () => void;
  canToggle?: boolean;
}) {
  const setup = useSetup((s) => s.setup);
  const sessionKind = useCalib((s) => (isSessionActive(s.session) ? (s.session?.kind ?? null) : null));
  useTicker(60_000);
  const prog = setupProgress(setup);
  const allDone = !!setup && firstUnfinished(setup) === "done";
  const byId = new Map<string, SetupStep>((setup?.steps ?? []).map((s) => [s.id, s]));
  const items: RailItem[] = [
    { page: null, title: "Overview", state: "neutral", summary: setup ? `${prog.done} of ${prog.total} steps done` : null, when: null },
    ...STEP_ORDER.map((id, i): RailItem => {
      const st = byId.get(id);
      return { page: id, title: STEP_META[id].title, state: st?.state ?? "todo", summary: st?.summary ?? null, when: st?.when ?? null, n: i + 1 };
    }),
    { page: "done", title: "Done", state: allDone ? "done" : "neutral", summary: allDone ? "Ready to scan" : "Scan your first object", when: null },
  ];

  return (
    <nav
      aria-label="Setup steps"
      className={cn(
        "flex shrink-0 flex-col overflow-y-auto overflow-x-hidden border-r border-line bg-panel transition-[width] duration-200",
        compact ? "w-[60px]" : "w-[272px]",
      )}
    >
      <div className={cn("flex items-center gap-3 border-b border-line", compact ? "h-14 justify-center px-2" : "h-[68px] px-4")}>
        {compact ? (
          <Tooltip side="right" content={`${prog.done} of ${prog.total} steps done`}>
            <span tabIndex={0} className="relative grid place-items-center rounded-full" aria-label={`${prog.done} of ${prog.total} steps done`}>
              <ProgressRing value={prog.total ? prog.done / prog.total : 0} size={34} />
              <span className="absolute font-mono text-[10px] font-semibold text-fg tnum">{prog.done}</span>
            </span>
          </Tooltip>
        ) : (
          <>
            <span className="relative grid place-items-center">
              <ProgressRing value={prog.total ? prog.done / prog.total : 0} size={40} />
              <span className="absolute font-mono text-[11px] font-semibold text-fg tnum">
                {prog.done}/{prog.total}
              </span>
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold text-fg">Scanner setup</div>
              <div className="truncate text-xs text-subtle">
                {!setup ? "Loading…" : allDone ? "All set: ready to scan" : `${prog.total - prog.done} step${prog.total - prog.done === 1 ? "" : "s"} to go`}
              </div>
            </div>
          </>
        )}
      </div>

      <ol className={cn("relative flex-1", compact ? "px-2 py-3" : "px-3 py-3")}>
        {items.map((it, i) => {
          const active = it.page === current;
          const busy = !!sessionKind && it.page === (sessionKind as CalibKind);
          const last = i === items.length - 1;
          const label = `${it.title}${it.state !== "neutral" ? `: ${STATE_LABEL[it.state]}` : ""}${it.summary ? `. ${it.summary}` : ""}${busy ? ". In progress" : ""}`;
          const dot = (
            <StepDot
              state={it.state}
              n={it.n}
              current={active}
              icon={it.page === null ? Compass : it.page === "done" ? Flag : undefined}
              size={compact ? 28 : 26}
              busy={busy}
            />
          );
          return (
            <li key={it.page ?? "overview"} className="relative">
              {!last ? (
                <span
                  className={cn(
                    "absolute w-px",
                    compact ? "left-1/2 top-[38px] h-[calc(100%-30px)] -translate-x-1/2" : "left-[25px] top-[36px] h-[calc(100%-26px)]",
                    it.state === "done" ? "bg-ok/50" : "bg-line-strong",
                  )}
                  aria-hidden
                />
              ) : null}
              {compact ? (
                <Tooltip
                  side="right"
                  content={
                    <div className="space-y-0.5">
                      <div className="font-semibold">{it.title}</div>
                      {it.state !== "neutral" ? <div className="text-muted">{STATE_LABEL[it.state]}</div> : null}
                      {it.summary ? <div className="text-muted">{it.summary}</div> : null}
                      {busy ? <div className="text-accent">In progress</div> : null}
                    </div>
                  }
                >
                  <a
                    href={href(paths.setup(it.page))}
                    aria-label={label}
                    aria-current={active ? "step" : undefined}
                    className={cn("my-1 grid h-10 w-11 place-items-center rounded-lg transition-colors", active ? "bg-accent-soft" : "hover:bg-surface-2")}
                  >
                    {dot}
                  </a>
                </Tooltip>
              ) : (
                <a
                  href={href(paths.setup(it.page))}
                  aria-label={label}
                  aria-current={active ? "step" : undefined}
                  className={cn(
                    "group relative mb-0.5 flex gap-3 rounded-lg px-2.5 py-2 transition-colors",
                    active ? "bg-accent-soft" : "hover:bg-surface-2",
                  )}
                >
                  {dot}
                  <span className="min-w-0 flex-1 pt-0.5">
                    <span className="flex items-center gap-2">
                      <span className={cn("truncate text-sm font-medium", active ? "text-accent" : "text-fg")}>{it.title}</span>
                      {busy ? (
                        <span className="rounded-full bg-accent px-1.5 py-px text-[9.5px] font-semibold uppercase tracking-wide text-accent-fg">Live</span>
                      ) : it.state === "optional" ? (
                        <span className="text-[10.5px] font-medium text-subtle">Optional</span>
                      ) : it.state === "warn" ? (
                        <span className="text-[10.5px] font-medium text-warn">Check</span>
                      ) : null}
                    </span>
                    {it.summary ? <span className="mt-0.5 line-clamp-2 block text-xs leading-[1.35] text-subtle">{it.summary}</span> : null}
                    {it.when && it.state === "done" ? <span className="block text-[10.5px] text-subtle/80">{fmtRelative(it.when)}</span> : null}
                  </span>
                </a>
              )}
            </li>
          );
        })}
      </ol>
      {canToggle && onToggle ? (
        <div className={cn("border-t border-line p-2", compact ? "flex justify-center" : "")}>
          <button
            type="button"
            onClick={onToggle}
            className="flex h-8 w-full items-center justify-center gap-2 rounded-md text-xs text-muted transition-colors hover:bg-surface-2 hover:text-fg"
            aria-label={compact ? "Show the step names" : "Hide the step names"}
            title={compact ? "Show the step names" : "Hide the step names"}
          >
            {compact ? <PanelLeftOpen size={15} aria-hidden /> : <PanelLeftClose size={15} aria-hidden />}
            {!compact ? "Hide step names" : null}
          </button>
        </div>
      ) : null}
    </nav>
  );
}
