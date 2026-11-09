import { CircleCheck, CircleX, Layers, Ban } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import type { Job } from "../../api/types";
import { fmtRelative } from "../../lib/format";
import { navigate, paths } from "../../lib/router";
import { cn } from "../../lib/util";
import { isActive, jobKindLabel, useJobs } from "../../store/jobs";
import { Button } from "../ui/Button";
import { EmptyState, ProgressBar, Spinner } from "../ui/Feedback";
import { Popover } from "../ui/Popover";

function JobRow({ job }: { job: Job }) {
  const link = useJobs((s) => s.links[job.id]);
  const cancel = useJobs((s) => s.cancel);
  const [cancelling, setCancelling] = useState(false);
  const active = isActive(job);
  return (
    <li className="px-3 py-2.5">
      <div className="flex items-center gap-2">
        {active ? (
          <Spinner size={14} className="text-accent" />
        ) : job.state === "done" ? (
          <CircleCheck size={15} className="text-ok" aria-hidden />
        ) : job.state === "cancelled" ? (
          <Ban size={15} className="text-subtle" aria-hidden />
        ) : (
          <CircleX size={15} className="text-danger" aria-hidden />
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-fg">
            {jobKindLabel(job.kind)}
            {link?.name ? <span className="font-normal text-muted"> · {link.name}</span> : null}
            {link ? <span className="font-normal text-subtle"> ({link.quality})</span> : null}
          </div>
        </div>
        <span className="shrink-0 text-[11px] text-subtle">{fmtRelative(job.created)}</span>
      </div>
      <div className="mt-1 truncate pl-[23px] text-xs text-muted" title={job.error || job.message}>
        {job.state === "failed" ? job.error || job.message || "Failed" : job.message || job.state}
      </div>
      {active ? (
        <div className="mt-2 flex items-center gap-2 pl-[23px]">
          <ProgressBar value={job.state === "queued" ? null : job.progress} size="sm" label={`${jobKindLabel(job.kind)} progress`} />
          <span className="w-9 shrink-0 text-right font-mono text-[11px] text-muted tnum">
            {job.state === "queued" ? "–" : `${Math.round(job.progress * 100)}%`}
          </span>
          <Button
            size="xs"
            variant="ghost"
            loading={cancelling}
            onClick={async () => {
              setCancelling(true);
              await cancel(job.id);
              setCancelling(false);
            }}
          >
            Cancel
          </Button>
        </div>
      ) : job.state === "done" && link ? (
        <div className="mt-1.5 pl-[23px]">
          <Button size="xs" variant="secondary" onClick={() => navigate(paths.viewer(link.pid, link.sid))}>
            Open result
          </Button>
        </div>
      ) : null}
    </li>
  );
}

/** Top-bar jobs indicator: count of running jobs and a list with progress. */
export function JobsMenu() {
  const jobs = useJobs((s) => s.jobs);
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const list = useMemo(() => Object.values(jobs).sort((a, b) => (a.created < b.created ? 1 : -1)), [jobs]);
  const running = list.filter(isActive);
  const top = running[0];

  return (
    <>
      <button
        ref={anchor}
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={running.length ? `${running.length} job${running.length > 1 ? "s" : ""} running` : "Jobs"}
        className={cn(
          "relative flex h-8 items-center gap-2 rounded-lg border px-2.5 text-xs transition-colors",
          running.length
            ? "border-accent-line bg-accent-soft text-accent hover:brightness-110"
            : "border-line bg-surface-2 text-muted hover:border-line-strong hover:bg-surface-3 hover:text-fg",
        )}
      >
        {running.length ? <Spinner size={14} /> : <Layers size={15} aria-hidden />}
        <span className="hidden font-medium md:inline">{running.length ? `${running.length} running` : "Jobs"}</span>
        {top ? <span className="hidden font-mono tnum lg:inline">{Math.round(top.progress * 100)}%</span> : null}
      </button>
      <Popover open={open} onClose={() => setOpen(false)} anchor={anchor} width={360} label="Jobs">
        <div className="flex items-center justify-between border-b border-line px-3 py-2.5">
          <div className="text-sm font-semibold text-fg">Jobs</div>
          <div className="text-xs text-subtle">{running.length ? `${running.length} running` : "Nothing running"}</div>
        </div>
        {list.length ? (
          <ul className="divide-y divide-line">
            {list.slice(0, 8).map((j) => (
              <JobRow key={j.id} job={j} />
            ))}
          </ul>
        ) : (
          <EmptyState compact icon={Layers} title="No jobs yet">
            Meshing and other long tasks show their progress here. They keep running if you switch pages.
          </EmptyState>
        )}
      </Popover>
    </>
  );
}
