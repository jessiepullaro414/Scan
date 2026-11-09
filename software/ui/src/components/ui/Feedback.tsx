import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn, clamp } from "../../lib/util";

export function Spinner({ size = 16, className }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className={cn("shrink-0 animate-spin", className)}
      role="presentation"
      aria-hidden
    >
      <circle cx="12" cy="12" r="9.5" fill="none" stroke="currentColor" strokeOpacity="0.22" strokeWidth="3" />
      <path d="M21.5 12a9.5 9.5 0 0 0-9.5-9.5" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

type Tone = "accent" | "ok" | "warn" | "danger" | "neutral";

const BAR_TONE: Record<Tone, string> = {
  accent: "bg-accent",
  ok: "bg-ok",
  warn: "bg-warn",
  danger: "bg-danger",
  neutral: "bg-muted",
};

/** A progress bar. `value` is 0..1; `null` shows an indeterminate sweep. */
export function ProgressBar({
  value,
  tone = "accent",
  className,
  label,
  size = "md",
}: {
  value: number | null;
  tone?: Tone;
  className?: string;
  label?: string;
  size?: "sm" | "md";
}) {
  const pct = value === null ? null : clamp(value, 0, 1) * 100;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct === null ? undefined : Math.round(pct)}
      className={cn("relative w-full overflow-hidden rounded-full bg-surface-3", size === "sm" ? "h-1" : "h-1.5", className)}
    >
      {pct === null ? (
        <div className={cn("absolute inset-y-0 w-1/3 rounded-full animate-indeterminate", BAR_TONE[tone])} />
      ) : (
        <div
          className={cn("h-full rounded-full transition-[width] duration-300 ease-out", BAR_TONE[tone])}
          style={{ width: `${pct}%` }}
        />
      )}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton", className)} aria-hidden />;
}

export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        "inline-flex h-5 min-w-5 items-center justify-center rounded border border-line-strong bg-surface-2 px-1.5",
        "font-mono text-[10.5px] font-medium leading-none text-muted shadow-[0_1px_0_var(--c-line-strong)]",
        className,
      )}
    >
      {children}
    </kbd>
  );
}

/** Empty / error / first-run states: an icon, a message, what to do, and actions. */
export function EmptyState({
  icon: Icon,
  title,
  children,
  actions,
  tone = "neutral",
  className,
  compact,
}: {
  icon: LucideIcon;
  title: string;
  children?: ReactNode;
  actions?: ReactNode;
  tone?: "neutral" | "danger" | "accent";
  className?: string;
  compact?: boolean;
}) {
  return (
    <div className={cn("flex flex-col items-center text-center", compact ? "gap-2 px-4 py-6" : "gap-3 px-6 py-12", className)}>
      <div
        className={cn(
          "grid place-items-center rounded-2xl border",
          compact ? "h-10 w-10" : "h-14 w-14",
          tone === "danger"
            ? "border-danger/30 bg-danger-soft text-danger"
            : tone === "accent"
              ? "border-accent-line bg-accent-soft text-accent"
              : "border-line bg-surface-2 text-muted",
        )}
      >
        <Icon size={compact ? 18 : 24} strokeWidth={1.7} aria-hidden />
      </div>
      <div className={cn("font-semibold text-fg", compact ? "text-sm" : "text-lg")}>{title}</div>
      {children ? <div className={cn("max-w-md text-muted", compact ? "text-xs" : "text-sm")}>{children}</div> : null}
      {actions ? <div className="mt-2 flex flex-wrap items-center justify-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** A coloured status dot, optionally pulsing. */
export function Dot({ tone, pulse, className }: { tone: Tone | "rec"; pulse?: boolean; className?: string }) {
  const color =
    tone === "rec"
      ? "bg-rec"
      : tone === "ok"
        ? "bg-ok"
        : tone === "warn"
          ? "bg-warn"
          : tone === "danger"
            ? "bg-danger"
            : tone === "accent"
              ? "bg-accent"
              : "bg-subtle";
  return (
    <span
      aria-hidden
      className={cn("inline-block h-2 w-2 shrink-0 rounded-full", color, pulse && "animate-rec", className)}
    />
  );
}
