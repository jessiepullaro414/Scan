/**
 * What this computer can do (UX_PLAN U5), said before something is missing: with a graphics card, its name and
 * "Every feature works"; without one, why, and what is not available (lib/computer.ts). Read from the engine's health.
 */
import { Cpu, MonitorCheck } from "lucide-react";
import { computerLine } from "../lib/computer";
import { cn } from "../lib/util";
import { useConnection } from "../store/connection";

export function ComputerLine({ className, compact = false }: { className?: string; compact?: boolean }) {
  const health = useConnection((s) => s.health);
  const line = computerLine((health?.engine as { compute?: unknown } | undefined)?.compute);
  if (!line) return null;
  const Icon = line.tone === "ok" ? MonitorCheck : Cpu;
  return (
    <div className={cn("flex gap-2 text-xs", className)} role="note">
      <Icon size={14} className={cn("mt-px shrink-0", line.tone === "ok" ? "text-ok" : line.tone === "warn" ? "text-warn" : "text-subtle")} aria-hidden />
      <div className="min-w-0">
        <span className="text-fg">{line.text}</span>
        {line.missing.length ? (
          compact ? (
            <span className="text-muted"> {line.missing.map((m) => m.replace(/\.$/, "").replace(/^[A-Z](?=[a-z])/, (c) => c.toLowerCase())).join("; ")}.</span>
          ) : (
            <ul className="mt-1 list-disc space-y-0.5 pl-4 text-muted">
              {line.missing.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          )
        ) : null}
      </div>
    </div>
  );
}
