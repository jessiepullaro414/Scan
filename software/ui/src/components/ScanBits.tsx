/** Shared scan pieces: status chip, "Make mesh" button with quality choice, job progress, "Process…". */
import { Box, ChevronDown, Gauge, SlidersHorizontal, Sparkles, Wand, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";
import type { Job, ProcessQuality, Scan } from "../api/types";
import { fmtDuration, fmtFixed, fmtInt, fmtPct } from "../lib/format";
import { isWholeRun, timeLeft } from "../lib/making";
import { QUALITIES, QUALITY_TEXT } from "../lib/quality";
import { navigate, paths } from "../lib/router";
import { cn } from "../lib/util";
import { isActive, jobScanRef, useJobs } from "../store/jobs";
import { processKey, useProcess } from "../store/process";
import { Badge, type BadgeTone } from "./ui/Badge";
import { Button, type ButtonSize, type ButtonVariant } from "./ui/Button";
import { ProgressBar } from "./ui/Feedback";
import { Menu } from "./ui/Popover";

const STATUS: Record<Scan["status"], { label: string; tone: BadgeTone }> = {
  recording: { label: "Recording", tone: "rec" },
  recorded: { label: "Recorded", tone: "neutral" },
  processing: { label: "Making the mesh", tone: "accent" }, // R12: the table's words, not "Meshing"
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

const QUALITY_ICON: Record<ProcessQuality, LucideIcon> = { standard: Wand, best: Sparkles, preview: Gauge };

/** "Make mesh" with a quality menu. Standard quality is the default action, then Best, then Preview. */
export function MakeMeshButton({
  projectId,
  scan,
  size = "sm",
  variant = "secondary",
  label,
  onStarted,
  className,
  blocked,
}: {
  projectId: string;
  scan: Pick<Scan, "id" | "name">;
  size?: ButtonSize;
  variant?: ButtonVariant;
  label?: string;
  onStarted?: (job: Job) => void;
  className?: string;
  /** Why the mesh cannot be made again (R2, `lib/keepMode.remakeBlock`): the button is disabled and says it on hover. */
  blocked?: string | null;
}) {
  const process = useJobs((s) => s.process);
  const [busy, setBusy] = useState(false);
  if (blocked)
    return (
      <Button size={size} variant={variant} icon={Wand} disabled title={blocked} className={className}>
        {label ?? "Make mesh"}
      </Button>
    );
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
        onClick={() => void run("standard")}
        title={`${QUALITY_TEXT.standard.label}: ${QUALITY_TEXT.standard.help}`}
        className="rounded-r-none"
      >
        {label ?? "Make mesh"}
      </Button>
      <Menu
        label="Mesh quality"
        width={300}
        items={QUALITIES.map((q) => ({
          label: QUALITY_TEXT[q].label,
          icon: QUALITY_ICON[q],
          hint: q === "standard" ? "default" : undefined,
          description: QUALITY_TEXT[q].help,
          onSelect: () => void run(q),
        }))}
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

/**
 * Inline progress of a meshing job (or, with `label` / `cancelTitle`, any job) with a cancel button, and the time
 * left (UX_PLAN R18, lib/making.ts): for Make mesh the whole job's, from the engine's `making.left_s` with the part
 * running now; for a stage run alone its frame pass's, named for that stage.
 */
export function JobProgress({
  job,
  compact,
  className,
  label = "Making the mesh",
  cancelTitle,
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
  const whole = isWholeRun(job);
  const ref = whole ? jobScanRef(job) : null;
  // the run being built (ProcessState.making), kept fresh by the engine's `process` messages
  const known = useProcess((s) => (ref ? s.entries[processKey(ref.pid, ref.sid)] !== undefined : true));
  const making = useProcess((s) => (ref ? (s.entries[processKey(ref.pid, ref.sid)]?.state?.making ?? null) : null));
  useEffect(() => {
    if (ref && !known && isActive(job)) void useProcess.getState().fetch(ref.pid, ref.sid);
  }, [ref?.pid, ref?.sid, known]); // eslint-disable-line react-hooks/exhaustive-deps
  const est = timeLeft(job, making);
  const f = job.frames;
  // R2: a Make mesh again leaves the scan's mesh as it is until the new one is ready, and after a cancel
  const stopTitle = cancelTitle ?? (job.mesh_kept ? "Stop making the mesh. The current mesh stays as it is." : "Stop making the mesh. The recording is kept.");
  const estTitle =
    whole && making
      ? `${typeof making.elapsed_s === "number" ? `Running for ${fmtDuration(making.elapsed_s)}. ` : ""}The time left is for every stage and edit still to come, at the pace so far.`
      : f?.fps
        ? `${fmtFixed(f.fps, 1)} frames a second so far (${fmtInt(f.done)} of ${fmtInt(f.total)})`
        : undefined;
  return (
    <div className={cn("min-w-0", className)}>
      <div className="mb-1.5 flex items-center gap-2 text-xs">
        <Icon size={13} className="shrink-0 text-accent" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-muted" title={job.message}>
          {job.state === "queued" ? "Waiting to start…" : job.message || "Working…"}
        </span>
        <span className="shrink-0 font-mono text-fg tnum">{job.state === "queued" ? "" : fmtPct(pct / 100)}</span>
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
            title={stopTitle}
          >
            Cancel
          </Button>
        ) : null}
      </div>
      {est ? (
        <div className="mt-1 text-xs text-subtle" aria-live="polite" title={estTitle}>
          {est.text}
        </div>
      ) : null}
    </div>
  );
}

/** "Process…": the scan's processing studio (stages, edits with undo, selection, recipes). */
export function ProcessButton({
  projectId,
  scan,
  size = "sm",
  variant = "secondary",
  label = "Edit mesh…",
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
      title={recording ? "Stop the scan first" : "Edit the mesh: clean up, fill holes, smooth, simplify, with undo"}
      onClick={() => navigate(paths.process(projectId, scan.id))}
    >
      {label}
    </Button>
  );
}
