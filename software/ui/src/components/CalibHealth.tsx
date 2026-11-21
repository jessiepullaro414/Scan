/**
 * The calibration's status (FEATURE_PLAN H4) on the pages: its icon, the button to the one wizard step to redo, the
 * setup overview's strip and the Scan page's line. The words come from `lib/calibHealth.ts`. It warns; nothing here
 * blocks a scan.
 */
import { OctagonExclamationPoint, ShieldCheck, TriangleExclamationPoint, type LucideIcon } from "lucide-react";
import type { CalibHealth, HealthState } from "../api/types";
import { calibrationSays, deviceHealth, healthView, type HealthTone, type HealthView } from "../lib/calibHealth";
import { navigate, paths } from "../lib/router";
import { cn } from "../lib/util";
import { useDevice } from "../store/device";
import { useSetup } from "../store/setup";
import { LaunchCheckCallout } from "./LaunchCheckCallout";
import { Button } from "./ui/Button";

export const HEALTH_ICON: Record<HealthState, LucideIcon> = { valid: ShieldCheck, check: TriangleExclamationPoint, recalibrate: OctagonExclamationPoint };
export const HEALTH_TEXT: Record<HealthTone, string> = { neutral: "text-ok", warn: "text-warn", danger: "text-danger" };

/** The status of the calibration the connected device scans with (`lib/calibHealth.deviceHealth`). */
export function useDeviceHealth(): CalibHealth | null {
  const device = useDevice((s) => s.device);
  const setup = useSetup((s) => s.setup);
  return deviceHealth(device, setup);
}

/** The button to the one wizard step the status names. */
export function HealthActionButton({ view, size = "sm" }: { view: HealthView; size?: "xs" | "sm" }) {
  if (!view.action) return null;
  const a = view.action;
  return (
    <Button size={size} variant="primary" icon={HEALTH_ICON[view.state]} title={a.hint} onClick={() => navigate(paths.setup(a.page))}>
      {a.label}
    </Button>
  );
}

/** The setup overview's strip: the state, the part, the reason, the button, then the quieter lines. */
export function HealthStrip({ health }: { health: CalibHealth }) {
  const v = healthView(health);
  if (!v) return null;
  const Icon = HEALTH_ICON[v.state];
  const more = health.reasons.slice(1);
  return (
    <div
      className={cn(
        "flex flex-wrap items-start gap-2.5 border-t px-6 py-3 text-sm",
        v.tone === "danger" ? "border-danger/30 bg-danger-soft" : v.tone === "warn" ? "border-warn/30 bg-warn-soft" : "border-line bg-surface-2/50",
      )}
      role={v.state === "valid" ? "status" : "alert"}
      aria-label={calibrationSays(v.title)}
    >
      <Icon size={15} className={cn("mt-0.5 shrink-0", HEALTH_TEXT[v.tone])} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className={v.state === "valid" ? "text-muted" : "text-fg"}>
          <b className={cn("font-semibold", v.state === "valid" && "font-medium text-fg")}>{v.title}.</b> <span className="text-muted">{v.reason}</span>
        </div>
        {v.then ? <div className="mt-0.5 text-xs text-muted">{v.then}</div> : null}
        {v.judged ? <div className="mt-0.5 text-xs text-subtle">{v.judged}</div> : null}
        {more.length ? (
          <ul className="mt-1 space-y-0.5 text-xs text-muted">
            {more.map((r, i) => (
              <li key={i}>
                Also: {r.reason}
              </li>
            ))}
          </ul>
        ) : null}
        {v.provisional ? <div className="mt-0.5 text-xs text-subtle">{v.provisional}</div> : null}
      </div>
      <HealthActionButton view={v} />
    </div>
  );
}

/**
 * The Scan page's (and the Device page's) line when the status is not Valid, with the button; it replaces the launch
 * check's drift callout,
 * which the status already includes. Without a status (an older engine, a replay) the drift callout shows as before.
 */
export function CalibHealthCallout({ glass, className }: { glass?: boolean; className?: string }) {
  const health = useDeviceHealth();
  const v = healthView(health);
  if (!v) return <LaunchCheckCallout glass={glass} className={className} />;
  if (v.state === "valid") return null;
  const Icon = HEALTH_ICON[v.state];
  return (
    <div
      role="status"
      title={[v.reason, v.then].filter(Boolean).join(" ")}
      className={cn(
        "flex animate-pop-in items-center gap-2.5 rounded-xl border px-3 py-2",
        v.tone === "danger" ? "border-danger/40" : "border-warn/40",
        glass ? "glass shadow-lg" : v.tone === "danger" ? "bg-danger-soft" : "bg-warn-soft",
        className,
      )}
    >
      <Icon size={16} className={cn("shrink-0", HEALTH_TEXT[v.tone])} aria-hidden />
      <div className="min-w-0 flex-1 truncate text-xs">
        <b className="font-semibold text-fg">{v.title}.</b> <span className="text-muted">{v.reason}</span>
      </div>
      <HealthActionButton view={v} size="xs" />
    </div>
  );
}
