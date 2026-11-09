/** Small shared pieces of the Process page: panel sections, stage badges, number fields. */
import type { LucideIcon } from "lucide-react";
import { useEffect, useId, useState, type ReactNode } from "react";
import type { StageState } from "../../api/types";
import { Badge, type BadgeTone } from "../../components/ui/Badge";
import { cn } from "../../lib/util";

/** A titled section of a side panel (the result viewer's look). */
export function Panel({
  title,
  icon: Icon,
  children,
  aside,
  className,
  id,
}: {
  title: string;
  icon: LucideIcon;
  children: ReactNode;
  aside?: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section className={cn("border-b border-line px-4 py-4", className)} aria-label={title} id={id}>
      <div className="mb-3 flex min-h-6 items-center gap-2">
        <Icon size={14} className="text-muted" aria-hidden />
        <h2 className="flex-1 text-xs font-semibold uppercase tracking-wider text-muted">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

const STAGE_BADGE: Record<StageState, { label: string; tone: BadgeTone }> = {
  none: { label: "Not run", tone: "neutral" },
  running: { label: "Running", tone: "accent" },
  done: { label: "Done", tone: "ok" },
  failed: { label: "Failed", tone: "danger" },
  skipped: { label: "Skipped", tone: "neutral" },
};

export function StageBadge({ state }: { state: StageState }) {
  const b = STAGE_BADGE[state] ?? STAGE_BADGE.none;
  return (
    <Badge tone={b.tone} dot={state !== "running"} spinner={state === "running"}>
      {b.label}
    </Badge>
  );
}

/**
 * A number field that lets you type freely and commits a clamped value on blur / Enter
 * (a half-typed "1" never becomes the value while you type "12").
 */
export function NumberField({
  value,
  onChange,
  min,
  max,
  step = 1,
  unit,
  label,
  disabled,
  className,
  id,
}: {
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  label: string;
  disabled?: boolean;
  className?: string;
  id?: string;
}) {
  const auto = useId();
  const fid = id ?? auto;
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const v = Number(text.replace(",", "."));
    if (!Number.isFinite(v)) {
      setText(String(value));
      return;
    }
    const c = Math.min(max, Math.max(min, step >= 1 ? Math.round(v) : v));
    setText(String(c));
    if (c !== value) onChange(c);
  };
  return (
    <div className={cn("relative", className)}>
      <input
        id={fid}
        type="text"
        inputMode="decimal"
        aria-label={label}
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
            e.preventDefault();
            const v = Math.min(max, Math.max(min, value + (e.key === "ArrowUp" ? step : -step)));
            onChange(Number(v.toFixed(3)));
          }
        }}
        className={cn(
          "h-8 w-full rounded-md border border-line bg-surface-2 px-2.5 font-mono text-[12.5px] text-fg tnum transition-[border-color,box-shadow] duration-150",
          "hover:border-line-strong focus:border-accent focus:shadow-[0_0_0_3px_var(--c-accent-soft)] focus:outline-none disabled:opacity-50",
          unit && "pr-10",
        )}
      />
      {unit ? <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-xs text-subtle">{unit}</span> : null}
    </div>
  );
}

/** A one-line explanation under a control. */
export function Help({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn("text-[11.5px] leading-[1.45] text-subtle", className)}>{children}</p>;
}
