/** Shared pieces of the setup wizard: the step frame and footer, chips, error callouts, disclosures. */
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  Copy,
  FlaskConical,
  Lightbulb,
  Minus,
  TriangleExclamationPoint,
  type LucideIcon,
} from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { describeError } from "../../api/http";
import type { StepState } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { Callout } from "../../components/ui/Layout";
import { Tooltip } from "../../components/ui/Popover";
import { RichText } from "../../components/ui/RichText";
import { navigate, paths, type SetupPage } from "../../lib/router";
import { nextPage, prevPage, STEP_META, STEP_ORDER } from "../../lib/setup";
import { cn, copyText } from "../../lib/util";
import { stepOf, useSetup } from "../../store/setup";
import { toast } from "../../store/toasts";

/** "Simulator sandbox": shown whenever the wizard calibrates the simulator. */
export function SimChip({ className }: { className?: string }) {
  const sim = useSetup((s) => s.setup?.target === "sim");
  const path = useSetup((s) => s.setup?.calibration.path);
  if (!sim) return null;
  return (
    <Tooltip
      side="bottom"
      content={
        <div className="space-y-1">
          <div className="font-semibold">Simulator sandbox</div>
          <div className="text-muted">
            While the simulator is connected, the wizard calibrates the virtual scanner into its own file
            {path ? (
              <>
                {" "}
                (<span className="font-mono">{path}</span>)
              </>
            ) : null}
            . Your real scanner's calibration is never touched, and the simulator's scan demo keeps its true calibration.
          </div>
        </div>
      }
    >
      <span
        tabIndex={0}
        className={cn(
          "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full border border-accent-line/70 bg-accent-soft px-2.5 text-xs font-medium text-accent",
          className,
        )}
        aria-label="Simulator sandbox: your real calibration is untouched"
      >
        <FlaskConical size={13} aria-hidden />
        <span>
          Simulator sandbox<span className="hidden xl:inline">: your real calibration is untouched</span>
        </span>
      </span>
    </Tooltip>
  );
}

/** The state circle of a step: done ✓, to do (its number), warning !, optional (dashed). */
export function StepDot({
  state,
  n,
  current,
  icon: Icon,
  size = 26,
  busy,
}: {
  state: StepState | "neutral";
  n?: number;
  current?: boolean;
  icon?: LucideIcon;
  size?: number;
  busy?: boolean;
}) {
  const s = size;
  const inner =
    state === "done" ? (
      <Check size={s * 0.52} strokeWidth={3} aria-hidden />
    ) : state === "warn" ? (
      <span className="text-[13px] font-bold leading-none" aria-hidden>
        !
      </span>
    ) : Icon ? (
      <Icon size={s * 0.52} aria-hidden />
    ) : (
      <span className="text-[11px] font-semibold leading-none tnum" aria-hidden>
        {n ?? ""}
      </span>
    );
  return (
    <span
      className={cn(
        "relative grid shrink-0 place-items-center rounded-full border transition-colors",
        state === "done"
          ? "border-ok bg-ok text-white"
          : state === "warn"
            ? "border-warn bg-warn text-black"
            : state === "optional"
              ? "border-dashed border-line-strong bg-surface-2 text-muted"
              : "border-line-strong bg-surface-2 text-muted",
        current && state !== "done" && state !== "warn" && "border-accent text-accent",
        current && "ring-[3px] ring-accent-soft",
      )}
      style={{ width: s, height: s }}
    >
      {inner}
      {busy ? <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 animate-rec rounded-full border-2 border-panel bg-accent" aria-hidden /> : null}
    </span>
  );
}

export const STATE_TONE: Record<StepState, "ok" | "warn" | "neutral" | "accent"> = {
  done: "ok",
  todo: "neutral",
  warn: "warn",
  optional: "neutral",
};

/** Header of a wizard page: "Step 3 of 7", the title, why it matters, and the sandbox chip. */
export function StepHeader({ page, aside, title, why }: { page: SetupPage; aside?: ReactNode; title?: ReactNode; why?: string }) {
  const meta = STEP_META[page];
  const i = (STEP_ORDER as string[]).indexOf(page);
  const Icon = meta.icon;
  return (
    <div className="mb-6 flex flex-wrap items-start gap-4">
      <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-line bg-surface-2 text-accent">
        <Icon size={20} aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-xs font-medium text-subtle">{i >= 0 ? `Step ${i + 1} of ${STEP_ORDER.length}` : "Setup"}</div>
        <h1 className="font-display text-2xl font-semibold tracking-[-0.01em] text-fg">{title ?? meta.title}</h1>
        <p className="mt-0.5 max-w-2xl text-sm text-muted">{why ?? meta.why}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <SimChip />
        {aside}
      </div>
    </div>
  );
}

/** Back / Skip / Next. Next is the primary action once the step is done (or optional). */
export function StepFooter({ page, extra, nextLabel }: { page: SetupPage; extra?: ReactNode; nextLabel?: string }) {
  const step = useSetup((s) => (page === "done" ? null : stepOf(s.setup, page)));
  const next = nextPage(page);
  const prev = prevPage(page);
  const done = !step || step.state === "done" || step.state === "optional";
  return (
    <>
      {/* pushes the footer to the bottom of the pane when the step is short */}
      <div className="min-h-8 flex-1" aria-hidden />
      <div className="sticky bottom-0 z-10 -mx-6 flex flex-wrap items-center gap-2 border-t border-line bg-bg/85 px-6 py-3 backdrop-blur xl:-mx-10 xl:px-10">
        {prev !== undefined ? (
          <Button variant="ghost" icon={ArrowLeft} onClick={() => navigate(paths.setup(prev))}>
            {prev === null ? "Overview" : `Back: ${STEP_META[prev].title}`}
          </Button>
        ) : null}
        <div className="flex-1" />
        {extra}
        {next ? (
          done ? (
            <Button variant="primary" iconRight={ArrowRight} onClick={() => navigate(paths.setup(next))}>
              {nextLabel ?? `Next: ${STEP_META[next].title}`}
            </Button>
          ) : (
            <Button variant="secondary" iconRight={ArrowRight} onClick={() => navigate(paths.setup(next))} title="You can come back to this step any time">
              Skip for now
            </Button>
          )
        ) : null}
      </div>
    </>
  );
}

/** A scrollable wizard page with a comfortable width; a flex column, so the footer can sit at the bottom. */
export function StepPage({ children, wide }: { children: ReactNode; wide?: boolean }) {
  return (
    <div className="h-full scroll-pb-24 overflow-y-auto" id="setup-scroll">
      <div className={cn("mx-auto flex min-h-full w-full flex-col px-6 pt-6 xl:px-10", wide ? "max-w-[1240px]" : "max-w-[1040px]")}>{children}</div>
    </div>
  );
}

/** Links that get you out of an error, guessed from what the engine said. */
function errorLinks(text: string, here?: SetupPage | null): { label: string; to: string }[] {
  const out: { label: string; to: string }[] = [];
  const add = (label: string, to: string, page?: SetupPage | null) => {
    if (page !== undefined && page === here) return;
    if (!out.some((o) => o.to === to)) out.push({ label, to });
  };
  if (/no device|not connected|connect (the|a)|replay|pico|stereo camera|camera marked/i.test(text)) add("Go to Connect", paths.setup("connect"), "connect");
  if (/stereo (step|calibration)|calibrate stereo|stereo first/i.test(text)) add("Go to Stereo", paths.setup("stereo"), "stereo");
  if (/scan is running|stop the scan/i.test(text)) add("Open the Scan page", paths.scan());
  if (/print|scale bar/i.test(text)) add("Go to Print targets", paths.setup("print"), "print");
  return out.slice(0, 2);
}

/** An API error in plain words (what happened, and what to do), with ways out. */
export function ErrorCallout({
  title,
  err,
  here,
  onRetry,
  retryLabel = "Try again",
  className,
  onDismiss,
}: {
  title: string;
  err: unknown;
  here?: SetupPage | null;
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
  onDismiss?: () => void;
}) {
  const { detail, hint } = describeError(err);
  const links = errorLinks(`${detail} ${hint ?? ""}`, here);
  return (
    <Callout
      tone="danger"
      icon={TriangleExclamationPoint}
      title={title}
      className={className}
      actions={
        links.length || onRetry || onDismiss ? (
          <>
            {links.map((l) => (
              <Button key={l.to} size="sm" variant="secondary" onClick={() => navigate(l.to)}>
                {l.label}
              </Button>
            ))}
            {onRetry ? (
              <Button size="sm" variant="secondary" onClick={onRetry}>
                {retryLabel}
              </Button>
            ) : null}
            {onDismiss ? (
              <Button size="sm" variant="ghost" onClick={onDismiss}>
                Dismiss
              </Button>
            ) : null}
          </>
        ) : undefined
      }
    >
      <span className="text-sm text-fg">
        <RichText text={detail} />
      </span>
      {hint ? (
        <span className="mt-1.5 flex gap-1.5 text-xs text-muted">
          <Lightbulb size={13} className="mt-0.5 shrink-0 text-warn" aria-hidden />
          <span>
            <RichText text={hint} />
          </span>
        </span>
      ) : null}
    </Callout>
  );
}

/** A labelled expander (keyboard accessible): "How to fix", "What this means", "Advanced". */
export function Disclosure({
  title,
  icon: Icon,
  children,
  defaultOpen = false,
  className,
  tone = "neutral",
  size = "md",
}: {
  title: ReactNode;
  icon?: LucideIcon;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
  tone?: "neutral" | "accent" | "warn";
  size?: "sm" | "md";
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div className={cn("rounded-lg border", tone === "warn" ? "border-warn/30 bg-warn-soft/40" : tone === "accent" ? "border-accent-line/60 bg-accent-soft/40" : "border-line bg-surface-2/50", className)}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex w-full items-center gap-2 rounded-lg text-left font-medium text-fg transition-colors hover:bg-surface-2",
          size === "sm" ? "px-2.5 py-1.5 text-xs" : "px-3.5 py-2.5 text-sm",
        )}
      >
        {Icon ? <Icon size={size === "sm" ? 13 : 15} className={tone === "warn" ? "text-warn" : tone === "accent" ? "text-accent" : "text-muted"} aria-hidden /> : null}
        <span className="min-w-0 flex-1">{title}</span>
        <ChevronDown size={size === "sm" ? 13 : 15} className={cn("shrink-0 text-subtle transition-transform", open && "rotate-180")} aria-hidden />
      </button>
      {open ? (
        <div id={id} className={cn("animate-fade-in", size === "sm" ? "px-2.5 pb-2.5 text-xs" : "px-3.5 pb-3.5 text-sm")}>
          {children}
        </div>
      ) : null}
    </div>
  );
}

/** A CLI command with a copy button. */
export function CopyCommand({ cmd, className }: { cmd: string; className?: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className={cn("flex items-center gap-2 rounded-lg border border-line bg-bg/60 py-1 pl-3 pr-1", className)}>
      <span className="select-none font-mono text-xs text-subtle" aria-hidden>
        &gt;
      </span>
      <code className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-fg" title={cmd}>
        {cmd}
      </code>
      <Button
        size="xs"
        variant="ghost"
        icon={done ? Check : Copy}
        aria-label={`Copy “${cmd}”`}
        onClick={async () => {
          if (await copyText(cmd)) {
            setDone(true);
            setTimeout(() => setDone(false), 1500);
          } else toast.warn("Couldn't copy", "Select the command and copy it by hand.");
        }}
      >
        {done ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}

/** A marker for a metric or a check: ✓ ok, ! not ok, – no verdict. */
export function OkMark({ ok, className }: { ok: boolean | null; className?: string }) {
  return (
    <span
      className={cn(
        "grid h-5 w-5 shrink-0 place-items-center rounded-full",
        ok === null ? "bg-surface-3 text-subtle" : ok ? "bg-ok-soft text-ok" : "bg-warn-soft text-warn",
        className,
      )}
      aria-label={ok === null ? "No verdict" : ok ? "Good" : "Needs attention"}
      role="img"
    >
      {ok === null ? <Minus size={11} strokeWidth={3} aria-hidden /> : ok ? <Check size={11} strokeWidth={3} aria-hidden /> : <span className="text-[11px] font-bold leading-none" aria-hidden>!</span>}
    </span>
  );
}
