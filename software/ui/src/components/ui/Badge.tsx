import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/util";
import { Spinner } from "./Feedback";

export type BadgeTone = "neutral" | "accent" | "ok" | "warn" | "danger" | "rec";

const TONES: Record<BadgeTone, string> = {
  neutral: "bg-surface-3 text-muted border-line",
  accent: "bg-accent-soft text-accent border-accent-line/60",
  ok: "bg-ok-soft text-ok border-ok/25",
  warn: "bg-warn-soft text-warn border-warn/25",
  danger: "bg-danger-soft text-danger border-danger/25",
  rec: "bg-danger-soft text-rec border-rec/30",
};

const DOTS: Record<BadgeTone, string> = {
  neutral: "bg-subtle",
  accent: "bg-accent",
  ok: "bg-ok",
  warn: "bg-warn",
  danger: "bg-danger",
  rec: "bg-rec",
};

/** A small status chip. */
export function Badge({
  tone = "neutral",
  icon: Icon,
  dot,
  pulse,
  spinner,
  children,
  className,
  title,
}: {
  tone?: BadgeTone;
  icon?: LucideIcon;
  dot?: boolean;
  pulse?: boolean;
  spinner?: boolean;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex h-5 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-2 text-[11px] font-medium leading-none",
        TONES[tone],
        className,
      )}
    >
      {spinner ? (
        <Spinner size={10} />
      ) : Icon ? (
        <Icon size={11} strokeWidth={2.2} aria-hidden />
      ) : dot ? (
        <span className={cn("h-1.5 w-1.5 rounded-full", DOTS[tone], pulse && "animate-rec")} aria-hidden />
      ) : null}
      {children}
    </span>
  );
}
