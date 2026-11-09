/** Shared scan pieces: status chip, "Make mesh" button with quality choice, job progress, "Process…". */
import { Box, ChevronDown, Gauge, SlidersHorizontal, Sparkles, Wand, type LucideIcon } from "lucide-react";
import { useState } from "react";
import type { Job, ProcessQuality, Scan } from "../api/types";
import { navigate, paths } from "../lib/router";
import { cn } from "../lib/util";
import { isActive, useJobs } from "../store/jobs";
import { Badge, type BadgeTone } from "./ui/Badge";
import { Button, type ButtonSize, type ButtonVariant } from "./ui/Button";
import { ProgressBar } from "./ui/Feedback";
import { Menu } from "./ui/Popover";

const STATUS: Record<Scan["status"], { label: string; tone: BadgeTone }> = {
  recording: { label: "Recording", tone: "rec" },
  recorded: { label: "Recorded", tone: "neutral" },
  processing: { label: "Meshing", tone: "accent" },
  processed: { label: "Mesh ready", tone: "ok" },
  failed: { label: "Failed", tone: "danger" },
};

export function ScanStatusBadge({ scan, job }: { scan: Scan; job?: Job }) {
  const active = isActive(job);
  const status = active ? "processing" : scan.status;
  const s = STATUS[status] ?? STATUS.recorded;
  return (
    <Badge tone={s.tone} dot={!active} pulse={status === "recording"} spinner={status === "processing"}>
      {s.label}
    </Badge>
  );
}

export const QUALITY_TEXT: Record<ProcessQuality, { label: string; help: string }> = {
  preview: { label: "Preview quality", help: "Fast check, coarser mesh" },
  full: { label: "Full quality", help: "Full resolution, best accuracy" },
};

/** "Make mesh" with a quality menu. Full quality is the default action. */
export function MakeMeshButton({
  projectId,
  scan,
  size = "sm",
  variant = "secondary",
  label,
  onStarted,
  className,
}: {
  projectId: string;
  scan: Pick<Scan, "id" | "name">;
  size?: ButtonSize;
  variant?: ButtonVariant;
  label?: string;
  onStarted?: (job: Job) => void;
  className?: string;
}) {
  const process = useJobs((s) => s.process);
  const [busy, setBusy] = useState(false);
  const run = async (q: ProcessQuality) => {
    setBusy(true);
    const job = await process(projectId, scan.id, q, scan.name);
    setBusy(false);
    if (job) onStarted?.(job);
  };
  return (
    <div className={cn("inline-flex", className)}>
      <Button
        size={size}
        variant={variant}
        icon={Wand}
        loading={busy}
        onClick={() => void run("full")}
        className="rounded-r-none"
      >
        {label ?? "Make mesh"}
      </Button>
      <Menu
        label="Mesh quality"
        width={250}
        items={[
          { label: "Full quality", icon: Sparkles, hint: "best", onSelect: () => void run("full") },
          { label: "Preview quality", icon: Gauge, hint: "fast", onSelect: () => void run("preview") },
        ]}
        renderTrigger={(t) => (
          <Button
            ref={t.ref}
            onClick={t.onClick}
            aria-haspopup={t["aria-haspopup"]}
            aria-expanded={t["aria-expanded"]}
            aria-label="Choose the mesh quality"
            size={size}
            variant={variant}
            disabled={busy}
            className={cn("rounded-l-none border-l px-1.5", variant === "primary" ? "border-l-white/20" : "border-l-line-strong")}
          >
            <ChevronDown size={14} aria-hidden />
          </Button>
        )}
      />
    </div>
  );
}

/** Inline progress of a meshing job (or, with `label` / `cancelTitle`, any job) with a cancel button. */
export function JobProgress({
  job,
  compact,
  className,
  label = "Meshing progress",
  cancelTitle = "Stop meshing. The raw recording is kept.",
  icon: Icon = Box,
}: {
  job: Job;
  compact?: boolean;
  className?: string;
  label?: string;
  cancelTitle?: string;
  icon?: LucideIcon;
}) {
  const cancel = useJobs((s) => s.cancel);
  const [cancelling, setCancelling] = useState(false);
  const pct = Math.round(job.progress * 100);
  return (
    <div className={cn("min-w-0", className)}>
      <div className="mb-1.5 flex items-center gap-2 text-xs">
        <Icon size={13} className="shrink-0 text-accent" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-muted" title={job.message}>
          {job.state === "queued" ? "Waiting to start…" : job.message || "Working…"}
        </span>
        <span className="shrink-0 font-mono text-fg tnum">{job.state === "queued" ? "" : `${pct}%`}</span>
      </div>
      <div className="flex items-center gap-2">
        <ProgressBar value={job.state === "queued" ? null : job.progress} label={label} />
        {!compact ? (
          <Button
            size="xs"
            variant="ghost"
            loading={cancelling}
            onClick={async () => {
              setCancelling(true);
              await cancel(job.id);
              setCancelling(false);
            }}
            title={cancelTitle}
          >
            Cancel
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/** "Process…": the scan's processing studio (stages, edits with undo, selection, recipes). */
export function ProcessButton({
  projectId,
  scan,
  size = "sm",
  variant = "secondary",
  label = "Process…",
  className,
}: {
  projectId: string;
  scan: Pick<Scan, "id" | "status">;
  size?: ButtonSize;
  variant?: ButtonVariant;
  label?: string;
  className?: string;
}) {
  const recording = scan.status === "recording";
  return (
    <Button
      size={size}
      variant={variant}
      icon={SlidersHorizontal}
      disabled={recording}
      className={className}
      title={recording ? "Stop the scan first" : "Step-by-step processing: clean up, fill holes, smooth, simplify, with undo"}
      onClick={() => navigate(paths.process(projectId, scan.id))}
    >
      {label}
    </Button>
  );
}
