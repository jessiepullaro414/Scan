import {
  ArrowLeft,
  Box,
  CircleExclamationPoint,
  Download,
  Eye,
  FolderOpen,
  Pencil,
  RefreshCw,
  ScanLine,
  Tag,
  Trash,
  Waypoints,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/http";
import type { Project, Scan } from "../api/types";
import { JobProgress, MakeMeshButton, ScanStatusBadge } from "../components/ScanBits";
import { Button, IconButton } from "../components/ui/Button";
import { ConfirmDialog, PromptDialog } from "../components/ui/Dialog";
import { EmptyState, Skeleton } from "../components/ui/Feedback";
import { Code, Page, PageHeader } from "../components/ui/Layout";
import { Menu } from "../components/ui/Popover";
import { downloadFile } from "../lib/download";
import { fileStem, fmtDateTime, fmtDuration, fmtInt, fmtRelative } from "../lib/format";
import { href, navigate, paths } from "../lib/router";
import { useTicker } from "../lib/util";
import { useConnection } from "../store/connection";
import { isActive, useScanJob } from "../store/jobs";
import { useProjects } from "../store/projects";
import { useSession } from "../store/session";
import { toast } from "../store/toasts";
import { useUi } from "../store/ui";

function resultUrls(project: Project, scan: Scan) {
  const r = scan.result;
  return {
    mesh: r?.mesh_url || api.fileUrl(project.id, scan.id, "mesh.ply"),
    stl: r?.stl_url || api.fileUrl(project.id, scan.id, "mesh.stl"),
    laser: r?.laser_points_url ?? null,
  };
}

function ScanRow({ project, scan }: { project: Project; scan: Scan }) {
  const job = useScanJob(scan.id);
  const active = isActive(job);
  const processing = active || scan.status === "processing";
  const hasResult = scan.status === "processed" && !!scan.result;
  const urls = resultUrls(project, scan);
  const stem = fileStem(project.name, scan.name);

  return (
    <li className="grid min-h-[64px] grid-cols-[minmax(0,1.5fr)_112px_minmax(0,1fr)_minmax(300px,330px)] items-center gap-4 px-4 py-2.5 transition-colors hover:bg-surface-2/60">
      <div className="flex min-w-0 items-center gap-3">
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
          <div className="truncate text-xs text-subtle" title={fmtDateTime(scan.created)}>
            {fmtRelative(scan.created)}
          </div>
        </div>
      </div>
      <div title={scan.status === "failed" ? (job?.error ?? "Meshing failed. Try again, or try preview quality.") : undefined}>
        <ScanStatusBadge scan={scan} job={job} />
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
        <span className="inline-flex items-center gap-1" title="Tracking mode">
          <Tag size={12} aria-hidden /> {scan.mode === "markers" ? "Markers" : "Markerless"}
        </span>
        <span className="tnum">{fmtInt(scan.n_frames)} frames</span>
        <span className="tnum">{fmtDuration(scan.duration_s)}</span>
      </div>
      <div className="flex min-w-0 items-center justify-end gap-2">
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
                <Menu
                  label="Download"
                  width={230}
                  items={[
                    { label: "Mesh (STL)", icon: Download, hint: "for CAD / slicers", onSelect: () => void downloadFile(urls.stl, `${stem}.stl`) },
                    { label: "Mesh (PLY)", icon: Download, hint: "with normals", onSelect: () => void downloadFile(urls.mesh, `${stem}.ply`) },
                    ...(urls.laser
                      ? [{ label: "Laser points (PLY)", icon: Download, onSelect: () => void downloadFile(urls.laser!, `${stem}-laser.ply`) }]
                      : []),
                  ]}
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
                <MakeMeshButton projectId={project.id} scan={scan} label="Remake" variant="ghost" />
              </>
            ) : (
              <MakeMeshButton projectId={project.id} scan={scan} variant={scan.status === "failed" ? "secondary" : "primary"} />
            )}
          </>
        )}
      </div>
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
  const live = useSession((s) => s.session);
  const link = useConnection((s) => s.link);
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  useTicker(30_000);

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
  const scanningHere = (live?.state === "scanning" || live?.state === "paused") && live.project_id === pid;

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
        <div className="grid grid-cols-[minmax(0,1.5fr)_112px_minmax(0,1fr)_minmax(300px,330px)] gap-4 border-b border-line bg-surface-2/50 px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-subtle">
          <div>Scan</div>
          <div>Status</div>
          <div>Details</div>
          <div className="text-right">Actions</div>
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
              <ScanRow key={s.id} project={project!} scan={s} />
            ))}
          </ul>
        )}
      </section>
      {project ? (
        <p className="mt-4 text-xs text-subtle">
          Raw recordings stay in <Code>scans/&lt;id&gt;/recording</Code>; meshes in <Code>scans/&lt;id&gt;/result</Code> inside
          the project folder.
        </p>
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
          if (await remove(pid)) {
            toast.success(`Moved “${name}” to the trash`, "Its folder is in .trash inside your projects folder.");
            navigate(paths.home());
          }
        }}
      >
        The project folder and all its scans move to the <Code>.trash</Code> folder inside your projects folder. Nothing is
        permanently deleted.
      </ConfirmDialog>
    </Page>
  );
}
