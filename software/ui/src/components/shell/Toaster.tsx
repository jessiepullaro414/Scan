import { CircleCheck, CircleExclamationPoint, Info, Lightbulb, TriangleExclamationPoint, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cn } from "../../lib/util";
import { useToasts, type Toast } from "../../store/toasts";
import { RichText } from "../ui/RichText";

const ICONS = { error: CircleExclamationPoint, warn: TriangleExclamationPoint, success: CircleCheck, info: Info } as const;
const TONE = { error: "text-danger", warn: "text-warn", success: "text-ok", info: "text-accent" } as const;
const BAR = { error: "bg-danger", warn: "bg-warn", success: "bg-ok", info: "bg-accent" } as const;

function ToastCard({ t }: { t: Toast }) {
  const dismiss = useToasts((s) => s.dismiss);
  const [paused, setPaused] = useState(false);
  const remaining = useRef(t.timeout);
  const started = useRef(performance.now());

  useEffect(() => {
    if (!t.timeout || paused) return;
    started.current = performance.now();
    const id = setTimeout(() => dismiss(t.id), remaining.current);
    return () => {
      clearTimeout(id);
      remaining.current -= performance.now() - started.current;
    };
  }, [paused, t.id, t.timeout, dismiss]);

  const Icon = ICONS[t.kind];
  return (
    <div
      role={t.kind === "error" ? "alert" : "status"}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      className="pointer-events-auto relative w-[380px] max-w-[calc(100vw-2rem)] animate-slide-in overflow-hidden rounded-xl border border-line-strong bg-panel shadow-lg"
    >
      <div className="flex gap-3 px-4 py-3">
        <Icon size={18} className={cn("mt-px shrink-0", TONE[t.kind])} aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-fg">{t.title}</div>
          {t.detail ? (
            <div className="mt-0.5 break-words text-sm text-muted">
              <RichText text={t.detail} />
            </div>
          ) : null}
          {t.hint ? (
            <div className="mt-2 flex gap-1.5 rounded-md bg-surface-2 px-2.5 py-1.5 text-xs text-fg">
              <Lightbulb size={13} className="mt-0.5 shrink-0 text-warn" aria-hidden />
              <span className="break-words">
                <RichText text={t.hint} />
              </span>
            </div>
          ) : null}
          {t.action ? (
            <button
              type="button"
              onClick={() => {
                t.action?.run();
                dismiss(t.id);
              }}
              className="mt-2 text-sm font-medium text-accent hover:underline"
            >
              {t.action.label}
            </button>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => dismiss(t.id)}
          aria-label="Dismiss"
          className="-mr-1 -mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md text-subtle hover:bg-surface-2 hover:text-fg"
        >
          <X size={14} aria-hidden />
        </button>
      </div>
      {t.timeout ? (
        <div className="absolute inset-x-0 bottom-0 h-[2px] bg-transparent">
          <div
            className={cn("h-full origin-left opacity-60", BAR[t.kind])}
            style={{
              animation: `toast-life ${t.timeout}ms linear forwards`,
              animationPlayState: paused ? "paused" : "running",
            }}
          />
        </div>
      ) : null}
    </div>
  );
}

export function Toaster() {
  const toasts = useToasts((s) => s.toasts);
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed bottom-4 right-4 z-[110] flex flex-col items-end gap-2"
    >
      {toasts.map((t) => (
        <ToastCard key={t.id} t={t} />
      ))}
    </div>
  );
}
