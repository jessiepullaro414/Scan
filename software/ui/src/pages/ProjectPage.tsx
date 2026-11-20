import {
  Archive,
  ArrowLeft,
  Box,
  CircleExclamationPoint,
  Combine,
  Download,
  Ellipsis,
  Eye,
  FolderOpen,
  HardDrive,
  Pencil,
  RefreshCw,
  ScanLine,
  SlidersHorizontal,
  Tag,
  Trash,
  Wand,
  Waypoints,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/http";
import type { Project, ProjectDiskUse, Scan, ScanDiskUse } from "../api/types";
import { JobProgress, MakeMeshButton, ProcessButton, ScanStatusBadge } from "../components/ScanBits";
import { Button, IconButton } from "../components/ui/Button";
import { ConfirmDialog, PromptDialog } from "../components/ui/Dialog";
import { EmptyState, Skeleton } from "../components/ui/Feedback";
import { Code, Page, PageHeader } from "../components/ui/Layout";
import { Menu } from "../components/ui/Popover";
import { HEALTH_ICON, HEALTH_TEXT } from "../components/CalibHealth";
import { hasMesh, hasRecording, mergedFromText } from "../lib/align";
import { passOfText } from "../lib/pass";
import { scanHealthLine, scanHealthOf } from "../lib/calibHealth";
import { projectDiskText, scanDiskTitle, trashText } from "../lib/diskUse";
import { canRemake, keepLabel, keepTitle } from "../lib/keepMode";
import { FreeSpaceDialog } from "../components/FreeSpaceDialog";
import { failedLine } from "../lib/scanStatus";
import { meshDownloadItems, meshUrls } from "../components/MeshDownloads";
import { fileStem, fmtBytes, fmtDateTime, fmtDuration, fmtInt, fmtRelative } from "../lib/format";
import { href, navigate, paths } from "../lib/router";
import { cn, useTicker } from "../lib/util";
import { useConnection } from "../store/connection";
import { isActive, useJobs, useScanJob } from "../store/jobs";
import { useProjects } from "../store/projects";
import { isLive, useSession } from "../store/session";
import { toast } from "../store/toasts";
import { useUi } from "../store/ui";

/** The scan table's columns (the header and every row): scan, status, details, actions (index.css `.scan-row`, U16). */
const ROW_GRID = "scan-row";



/**
 * R1: what the project takes on disk. Asked for after the page has painted (the engine walks the
 * folder), again when a scan's status changes; a failure shows nothing (it informs only).
 */
function useDiskUse(pid: string, project: Project | undefined): ProjectDiskUse | null {
  const [disk, setDisk] = useState<ProjectDiskUse | null>(null);
  const key = project ? project.scans.map((s) => `${s.id}:${s.status}:${s.keep ?? ""}:${s.freed?.length ?? 0}`).join(",") || "-" : "";
  useEffect(() => {
    if (!key) return;
    let live = true;
    api.projectDisk(pid).then(
      (d) => live && setDisk(d),
      () => live && setDisk(null),
    );
    return () => {
      live = false;
    };
  }, [pid, key]);
  return disk && disk.project_id === pid ? disk : null;
}

/** U16: under about 1100 px the row's secondary actions are in this menu (index.css `.sr-more`). */
function MoreMenu({ name, items }: { name: string; items: Parameters<typeof Menu>[0]["items"] }) {
  return (
    <span className="sr-more">
      <Menu
        label={`More for ${name}`}
        width={260}
        items={items}
        renderTrigger={(t) => (
          <Button ref={t.ref} onClick={t.onClick} aria-haspopup={t["aria-haspopup"]} aria-expanded={t["aria-expanded"]} size="sm" icon={Ellipsis} aria-label={`More for ${name}`}>
            More
          </Button>
        )}
      />
    </span>
  );
}

function ScanRow({ project, scan, disk, onFreeSpace }: { project: Project; scan: Scan; disk?: ScanDiskUse; onFreeSpace: (scan: Scan) => void }) {
  const job = useScanJob(scan.id);
  const active = isActive(job);
  const processing = active || scan.status === "processing";
  const hasResult = scan.status === "processed" && !!scan.result;
  const urls = meshUrls(project.id, scan.id, scan.result);
  const stem = fileStem(project.name, scan.name);
  const downloads = meshDownloadItems(urls, stem); // U42: the one list, OBJ included
  const makeMesh = useJobs((s) => s.process);
  // A merged scan (M4) has no recording of its own: nothing that makes its mesh from the frames again.
  const recorded = hasRecording(scan);
  // R2: a scan that keeps its result only has no frames to make its mesh from again
  const remakable = canRemake(scan);
  const kept = keepLabel(scan.keep, scan.thinned); // R3: and at what rate its recording is kept
  const mergedFrom = mergedFromText(scan) ?? passOfText(scan); // N2: or the scan a second pass belongs to
  const own = hasResult ? scanHealthOf(scan.result?.report) : null;
  const health = scanHealthLine(own);
  const HealthIcon = health && own ? HEALTH_ICON[own.state] : null;

  return (
    <li className={cn(ROW_GRID, "min-h-[64px] items-center px-4 py-2.5 transition-colors hover:bg-surface-2/60")}>
      <div className="sr-name flex min-w-0 items-center gap-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-line bg-surface-2 text-muted">
          {hasResult ? <Box size={16} aria-hidden /> : <Waypoints size={16} aria-hidden />}
        </div>
        <div className="min-w-0">
          {hasResult ? (
            <a href={href(paths.viewer(project.id, scan.id))} className="block truncate text-sm font-medium text-fg hover:text-accent">
              {scan.name}
            </a>
          ) : (
            <div className="truncate text-sm font-medium text-fg">{scan.name}</div>
          )}
          <div className="truncate text-xs text-subtle" title={mergedFrom ? `${fmtDateTime(scan.created)} · ${mergedFrom}` : fmtDateTime(scan.created)}>
            {fmtRelative(scan.created)}
            {mergedFrom ? ` · ${mergedFrom}` : null}
          </div>
        </div>
      </div>
      <div className="sr-status">
        <ScanStatusBadge scan={scan} job={job} />
      </div>
      <div className="sr-details flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
        <span className="inline-flex items-center gap-1" title="Tracking mode">
          <Tag size={12} aria-hidden /> {scan.mode === "markers" ? "Markers" : "Markerless"}
        </span>
        <span className="tnum">{fmtInt(scan.n_frames)} frames</span>
        {recorded ? (
          <span className="tnum">{fmtDuration(scan.duration_s)}</span>
        ) : (
          <span className="inline-flex items-center gap-1" title="Made by merging two scans: it has no recording of its own">
            <Combine size={12} aria-hidden /> Merged
          </span>
        )}
        {health && HealthIcon ? (
          // H4: this scan's own calibration status, when it was not Valid (the reason on hover)
          <span className={cn("inline-flex cursor-help items-center gap-1", HEALTH_TEXT[health.tone])} title={health.title}>
            <HealthIcon size={12} aria-hidden /> {health.text}
          </span>
        ) : null}
        {kept ? (
          // R2: what the user chose to keep (the words say it; the hover says what was removed, and what is back since)
          <span className="inline-flex cursor-help items-center gap-1" title={keepTitle(scan, disk, fmtDateTime)}>
            <Archive size={12} aria-hidden /> {kept}
          </span>
        ) : null}
        {disk && disk.bytes > 0 ? (
          // U39: the size and the way to free space, visible (the parts on hover)
          <button
            type="button"
            className="inline-flex items-center gap-1 rounded tnum text-accent underline-offset-2 hover:underline disabled:text-muted disabled:no-underline"
            title={scanDiskTitle(disk)}
            onClick={() => onFreeSpace(scan)}
            disabled={processing || scan.status === "recording"}
          >
            <HardDrive size={12} aria-hidden /> {fmtBytes(disk.bytes)} · Free space…
          </button>
        ) : null}
      </div>
      <div className="sr-actions flex min-w-0 items-center justify-end gap-2">
        {processing && job ? (
          <JobProgress job={job} className="w-full max-w-[300px]" />
        ) : processing ? (
          <span className="text-xs text-muted">Meshing on the engine…</span>
        ) : scan.status === "recording" ? (
          <Button size="sm" variant="secondary" icon={ScanLine} onClick={() => navigate(paths.scan(project.id))}>
            Go to live scan
          </Button>
        ) : (
          <>
            {hasResult ? (
              <>
                <Button size="sm" variant="primary" icon={Eye} onClick={() => navigate(paths.viewer(project.id, scan.id))}>
                  Open
                </Button>
                <span className="sr-secondary">
                <ProcessButton projectId={project.id} scan={scan} label="Process" />
                <Menu
                  label="Download"
                  width={260}
                  items={downloads}
                  renderTrigger={(t) => (
                    <Button
                      ref={t.ref}
                      onClick={t.onClick}
                      aria-haspopup={t["aria-haspopup"]}
                      aria-expanded={t["aria-expanded"]}
                      size="sm"
                      icon={Download}
                    >
                      Download
                    </Button>
                  )}
                />
                {remakable ? <MakeMeshButton projectId={project.id} scan={scan} label="Remake" variant="ghost" /> : null}
                </span>
                <MoreMenu
                  name={scan.name}
                  items={[
                    { label: "Process", icon: SlidersHorizontal, onSelect: () => navigate(paths.process(project.id, scan.id)) },
                    ...downloads,
                    ...(remakable ? [{ label: "Remake", icon: Wand, hint: "standard quality", onSelect: () => void makeMesh(project.id, scan.id, "standard", scan.name) }] : []),
                  ]}
                />
              </>
            ) : !recorded ? (
              <ProcessButton projectId={project.id} scan={scan} label="Process" />
            ) : (
              <>
                <MakeMeshButton projectId={project.id} scan={scan} variant={scan.status === "failed" ? "secondary" : "primary"} />
                <span className="sr-secondary">
                  <ProcessButton projectId={project.id} scan={scan} label="Process" variant="ghost" />
                </span>
                <MoreMenu name={scan.name} items={[{ label: "Process", icon: SlidersHorizontal, onSelect: () => navigate(paths.process(project.id, scan.id)) }]} />
              </>
            )}
          </>
        )}
      </div>
      {scan.status === "failed" && !processing ? (
        <div className="sr-fail flex items-start gap-1.5 pl-12 text-xs text-danger" role="note">
          <CircleExclamationPoint size={13} className="mt-px shrink-0" aria-hidden />
          <span>{failedLine(job?.error, remakable && recorded)}</span>
        </div>
      ) : null}
    </li>
  );
}

export function ProjectPage({ pid }: { pid: string }) {
  const project = useProjects((s) => s.details[pid]);
  const loading = useProjects((s) => !!s.detailLoading[pid]);
  const error = useProjects((s) => s.detailError[pid]);
  const summary = useProjects((s) => s.list?.find((p) => p.id === pid));
  const fetchProject = useProjects((s) => s.fetchProject);
  const rename = useProjects((s) => s.rename);
  const remove = useProjects((s) => s.remove);
  const setCurrent = useUi((s) => s.setCurrentProject);
  const scanningHere = useSession((s) => isLive(s.session) && s.session?.project_id === pid);
  const link = useConnection((s) => s.link);
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  /** R2: the scan whose space is being freed, or null for the whole project; undefined while closed. */
  const [freeing, setFreeing] = useState<Scan | null | undefined>(undefined);
  useTicker(30_000);
  const disk = useDiskUse(pid, project);
  const trash = disk ? trashText(disk) : null;

  useEffect(() => {
    void fetchProject(pid);
  }, [pid, fetchProject]);

  // Keep the list fresh while the engine works on it and live updates are unavailable.
  const busy = !!project?.scans.some((s) => s.status === "processing" || s.status === "recording");
  useEffect(() => {
    if (!busy) return;
    const id = setInterval(() => void fetchProject(pid), link === "open" ? 5000 : 2000);
    return () => clearInterval(id);
  }, [busy, link, pid, fetchProject]);

  const name = project?.name ?? summary?.name ?? "";
  const scans = project ? [...project.scans].sort((a, b) => (a.created < b.created ? 1 : -1)) : null;
  /** Two scans with a mesh can be aligned to each other and merged (M4). */
  const canAlign = !!project && project.scans.filter(hasMesh).length >= 2;

  if (error && !project) {
    const notFound = error.status === 404;
    return (
      <Page>
        <div className="mt-10 rounded-xl border border-line bg-surface">
          <EmptyState
            tone={notFound ? "neutral" : "danger"}
            icon={notFound ? FolderOpen : CircleExclamationPoint}
            title={notFound ? "This project doesn't exist any more" : "Couldn't load the project"}
            actions={
              <>
                <Button icon={ArrowLeft} onClick={() => navigate(paths.home())}>
                  All projects
                </Button>
                {!notFound ? (
                  <Button variant="primary" icon={RefreshCw} loading={loading} onClick={() => void fetchProject(pid)}>
                    Try again
                  </Button>
                ) : null}
              </>
            }
          >
            {notFound ? "It may have been moved to the trash, or renamed outside Studio." : error.detail}
            {!notFound && error.hint ? <div className="mt-1 text-subtle">{error.hint}</div> : null}
          </EmptyState>
        </div>
      </Page>
    );
  }

  return (
    <Page wide>
      <a href={href(paths.home())} className="mb-3 inline-flex items-center gap-1.5 text-xs font-medium text-muted hover:text-fg">
        <ArrowLeft size={13} aria-hidden /> All projects
      </a>
      <PageHeader
        title={name || <Skeleton className="h-7 w-64" />}
        subtitle={
          project ? (
            <span className="flex flex-wrap items-center gap-x-2">
              <span>
                {project.scans.length} scan{project.scans.length === 1 ? "" : "s"}
              </span>
              <span aria-hidden>·</span>
              <span title={fmtDateTime(project.modified)}>edited {fmtRelative(project.modified)}</span>
              <span aria-hidden>·</span>
              <span>created {fmtDateTime(project.created)}</span>
            </span>
          ) : (
            <Skeleton className="mt-1 h-4 w-72" />
          )
        }
        actions={
          <>
            <IconButton icon={Pencil} label="Rename project" variant="secondary" onClick={() => setRenaming(true)} disabled={!project} />
            <IconButton icon={Trash} label="Move project to trash" variant="secondary" onClick={() => setDeleting(true)} disabled={!project} />
            {canAlign ? (
              <Button icon={Combine} onClick={() => navigate(paths.align(pid))} title="Line up two to eight scans of the same part and merge them into one">
                Align and merge scans
              </Button>
            ) : null}
            <Button
              variant="primary"
              icon={ScanLine}
              onClick={() => {
                setCurrent(pid);
                navigate(paths.scan(pid));
              }}
            >
              {scanningHere ? "Back to live scan" : "New scan"}
            </Button>
          </>
        }
      />

      <section aria-label="Scans" className="overflow-hidden rounded-xl border border-line bg-surface shadow-sm">
        <div className={cn(ROW_GRID, "scan-head border-b border-line bg-surface-2/50 px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-subtle")}>
          <div className="sr-name">Scan</div>
          <div className="sr-status">Status</div>
          <div className="sr-details">Details</div>
          <div className="sr-actions text-right">Actions</div>
        </div>
        {scans === null ? (
          <ul aria-busy="true">
            {Array.from({ length: 3 }, (_, i) => (
              <li key={i} className="flex items-center gap-3 px-4 py-3.5">
                <Skeleton className="h-9 w-9" />
                <div className="flex-1 space-y-1.5">
                  <Skeleton className="h-4 w-48" />
                  <Skeleton className="h-3 w-24" />
                </div>
                <Skeleton className="h-7 w-28" />
              </li>
            ))}
          </ul>
        ) : scans.length === 0 ? (
          <EmptyState
            icon={ScanLine}
            title="No scans in this project yet"
            actions={
              <Button
                variant="primary"
                icon={ScanLine}
                onClick={() => {
                  setCurrent(pid);
                  navigate(paths.scan(pid));
                }}
              >
                Start a scan
              </Button>
            }
          >
            Connect the scanner (or the simulator demo), then start a scan. Every scan records the raw camera stream, so you
            can rebuild its mesh later with better settings.
          </EmptyState>
        ) : (
          <ul className="divide-y divide-line">
            {scans.map((s) => (
              <ScanRow key={s.id} project={project!} scan={s} disk={disk?.scans.find((d) => d.id === s.id)} onFreeSpace={setFreeing} />
            ))}
          </ul>
        )}
      </section>
      {project && disk ? (
        <p className="mt-4 flex items-start gap-1.5 text-xs text-muted">
          <HardDrive size={13} className="mt-px shrink-0" aria-hidden />
          <span>
            <span className="tnum">This project takes {projectDiskText(disk)}.</span>
            {trash ? <span className="tnum"> {trash}</span> : null} Point at a scan's size for what it holds.
          </span>
          {project.scans.length ? (
            <Button size="xs" variant="ghost" icon={HardDrive} className="-mt-0.5 ml-1" onClick={() => setFreeing(null)}>
              Free space…
            </Button>
          ) : null}
        </p>
      ) : null}
      {project && freeing !== undefined ? (
        <FreeSpaceDialog open onClose={() => setFreeing(undefined)} project={project} scan={freeing} onDone={() => void fetchProject(pid)} />
      ) : null}
      {project ? (
        // U39: no folder paths for the user to decode; the folder itself opens from a button once the engine offers it
        <p className="mt-2 text-xs text-subtle">Every scan keeps its recording, so its mesh can be made again with other settings.</p>
      ) : null}

      <PromptDialog
        open={renaming}
        onClose={() => setRenaming(false)}
        title="Rename project"
        label="Project name"
        initial={name}
        confirmLabel="Rename"
        onSubmit={(v) => rename(pid, v)}
      />
      <ConfirmDialog
        open={deleting}
        onClose={() => setDeleting(false)}
        title={`Move “${name}” to the trash?`}
        confirmLabel="Move to trash"
        tone="danger"
        onConfirm={async () => {
          if (!(await remove(pid))) return false; // U6: a failed move keeps the dialog open
          toast.success(`Moved “${name}” to the trash`, "Its folder is in .trash inside your projects folder.");
          navigate(paths.home());
        }}
      >
        The project folder and all its scans move to the <Code>.trash</Code> folder inside your projects folder. Nothing is
        permanently deleted.
      </ConfirmDialog>
    </Page>
  );
}
