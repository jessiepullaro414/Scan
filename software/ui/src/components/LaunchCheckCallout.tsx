/** Shown when the launch check after a connect says the calibration may have drifted. */
import { ClipboardCheck, TriangleExclamationPoint } from "lucide-react";
import { sentence } from "../api/http";
import { fmtFixed } from "../lib/format";
import { navigate, paths } from "../lib/router";
import { cn } from "../lib/util";
import { useDevice } from "../store/device";
import { Button } from "./ui/Button";

export function LaunchCheckCallout({ glass, className }: { glass?: boolean; className?: string }) {
  const lc = useDevice((s) => (s.device?.connected ? (s.device.launch_check ?? null) : null));
  if (!lc || lc.state !== "drift") return null;
  return (
    <div
      role="alert"
      className={cn(
        "flex animate-pop-in items-start gap-3 rounded-xl border border-warn/40 px-4 py-3",
        glass ? "glass shadow-lg" : "bg-warn-soft",
        className,
      )}
    >
      <TriangleExclamationPoint size={18} className="mt-0.5 shrink-0 text-warn" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold text-fg">The calibration may have drifted</div>
        <div className="mt-0.5 text-xs text-muted">
          {lc.message ? sentence(lc.message) : `Left and right image rows differ${lc.dv_px !== null ? ` by ${fmtFixed(lc.dv_px, 1)} px` : ""}.`} A 2-minute check tells
          you whether to recalibrate.
        </div>
      </div>
      <Button size="sm" variant="primary" icon={ClipboardCheck} onClick={() => navigate(paths.setup("check"))}>
        Run the check
      </Button>
    </div>
  );
}
