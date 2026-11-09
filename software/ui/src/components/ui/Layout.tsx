import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/util";

/** Scrollable page body with a comfortable max width. */
export function Page({ children, className, wide }: { children: ReactNode; className?: string; wide?: boolean }) {
  return (
    <div className="h-full overflow-y-auto">
      <div className={cn("mx-auto w-full px-6 pb-12 pt-6 xl:px-10", wide ? "max-w-[1760px]" : "max-w-[1240px]", className)}>
        {children}
      </div>
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
  eyebrow,
  icon: Icon,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  eyebrow?: ReactNode;
  icon?: LucideIcon;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow ? <div className="mb-1.5 text-xs font-medium text-subtle">{eyebrow}</div> : null}
        <h1 className="flex items-center gap-2.5 font-display text-2xl font-semibold tracking-[-0.01em] text-fg">
          {Icon ? <Icon size={22} className="text-muted" aria-hidden /> : null}
          <span className="truncate">{title}</span>
        </h1>
        {subtitle ? <div className="mt-1 text-sm text-muted">{subtitle}</div> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function Card({ children, className, as: As = "div" }: { children: ReactNode; className?: string; as?: "div" | "section" }) {
  return <As className={cn("rounded-xl border border-line bg-surface shadow-sm", className)}>{children}</As>;
}

/** A titled card section. */
export function Section({
  title,
  icon: Icon,
  actions,
  children,
  className,
  bodyClassName,
  description,
}: {
  title: string;
  icon?: LucideIcon;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  description?: ReactNode;
}) {
  return (
    <section className={cn("rounded-xl border border-line bg-surface shadow-sm", className)} aria-label={title}>
      <header className="flex min-h-11 items-center gap-2 border-b border-line px-4 py-2">
        {Icon ? <Icon size={15} className="text-muted" aria-hidden /> : null}
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-fg">{title}</h2>
          {description ? <div className="text-xs text-subtle">{description}</div> : null}
        </div>
        {actions}
      </header>
      <div className={cn("p-4", bodyClassName)}>{children}</div>
    </section>
  );
}

/** Key / value rows. */
export function KeyValue({
  rows,
  className,
  dense,
}: {
  rows: Array<[ReactNode, ReactNode] | null | false>;
  className?: string;
  dense?: boolean;
}) {
  return (
    <dl className={cn("grid grid-cols-[minmax(110px,auto)_1fr] gap-x-4", dense ? "gap-y-1" : "gap-y-2", className)}>
      {rows.filter(Boolean).map((r, i) => {
        const [k, v] = r as [ReactNode, ReactNode];
        return (
          <div key={i} className="contents">
            <dt className="text-xs leading-5 text-subtle">{k}</dt>
            <dd className="min-w-0 break-words text-sm leading-5 text-fg">{v}</dd>
          </div>
        );
      })}
    </dl>
  );
}

/** Inline callout: what's missing and what to do. */
export function Callout({
  tone = "neutral",
  icon: Icon,
  title,
  children,
  actions,
  className,
}: {
  tone?: "neutral" | "accent" | "warn" | "danger" | "ok";
  icon?: LucideIcon;
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  const tones = {
    neutral: "border-line bg-surface-2 text-muted",
    accent: "border-accent-line/70 bg-accent-soft text-accent",
    warn: "border-warn/30 bg-warn-soft text-warn",
    danger: "border-danger/30 bg-danger-soft text-danger",
    ok: "border-ok/30 bg-ok-soft text-ok",
  } as const;
  return (
    <div className={cn("flex gap-3 rounded-lg border px-3.5 py-3", tones[tone], className)} role={tone === "danger" ? "alert" : undefined}>
      {Icon ? <Icon size={17} className="mt-px shrink-0" aria-hidden /> : null}
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-fg">{title}</div>
        {children ? <div className="mt-0.5 text-xs leading-5 text-muted">{children}</div> : null}
        {actions ? <div className="mt-2.5 flex flex-wrap gap-2">{actions}</div> : null}
      </div>
    </div>
  );
}

/** A small inline code chip for CLI commands. */
export function Code({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <code className={cn("rounded border border-line bg-surface-3 px-1.5 py-px font-mono text-[11.5px] text-fg", className)}>
      {children}
    </code>
  );
}
