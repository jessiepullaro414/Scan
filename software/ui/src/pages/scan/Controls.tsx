/** The scan control bar (Start / Pause / Resume / Stop / Rewind) and the post-scan call to action. */
import { CircleCheck, CircleExclamationPoint, Eye, FolderOpen, Pause, Play, Plus, Square, Undo2 } from "lucide-react";
import { JobProgress, MakeMeshButton, ProcessButton } from "../../components/ScanBits";
import { Button } from "../../components/ui/Button";
import { Spinner } from "../../components/ui/Feedback";
import { fmtDuration, fmtInt } from "../../lib/format";
import { navigate, paths } from "../../lib/router";
import { isActive, useScanJob } from "../../store/jobs";
import { useLive } from "../../store/live";
import { useProjects } from "../../store/projects";
import { useSession } from "../../store/session";

export function PostScanCard() {
  const last = useSession((s) => s.lastStopped);
  const clear = useSession((s) => s.clearLastStopped);
  const job = useScanJob(last?.scan.id);
  const fresh = useProjects((s) => (last ? s.details[last.projectId]?.scans.find((x) => x.id === last.scan.id) : undefined));
  if (!last) return null;
  const scan = fresh ?? last.scan;
  const active = isActive(job);
  const done = !active && (scan.status === "processed" || job?.state === "done");
  const failed = !active && !done && (job?.state === "failed" || scan.status === "failed");
  const newScan = () => {
    clear();
    useLive.getState().resetScan();
  };

  return (
    <div className="glass w-[560px] max-w-[calc(100%-2rem)] animate-pop-in rounded-2xl border border-line-strong p-4 shadow-lg">
      <div className="flex items-start gap-3">
        <div
          className={`grid h-9 w-9 shrink-0 place-items-center rounded-full ${failed ? "bg-danger-soft text-danger" : "bg-ok-soft text-ok"}`}
        >
          {failed ? <CircleExclamationPoint size={18} aria-hidden /> : <CircleCheck size={18} aria-hidden />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-fg">
            {done ? "Mesh ready" : failed ? "Meshing failed" : active ? "Making the mesh…" : "Scan saved"}
            <span className="font-normal text-muted"> · {scan.name}</span>
          </div>
          <div className="mt-0.5 text-xs text-muted">
            {failed
              ? job?.error || "The raw recording is kept: try again, or with preview quality."
              : `${fmtInt(scan.n_frames)} frames · ${fmtDuration(scan.duration_s)} · raw recording saved. The live model is a preview; the mesh is rebuilt from the recording at full resolution.`}
          </div>
        </div>
      </div>
      {active && job ? <JobProgress job={job} className="mt-3.5" /> : null}
      <div className="mt-3.5 flex flex-wrap items-center gap-2">
        {done ? (
          <>
            <Button variant="primary" icon={Eye} onClick={() => navigate(paths.viewer(last.projectId, scan.id))}>
              Open result
            </Button>
            <ProcessButton projectId={last.projectId} scan={scan} size="md" label="Fine-tune in Process" />
          </>
        ) : !active ? (
          <MakeMeshButton projectId={last.projectId} scan={scan} variant="primary" size="md" label={failed ? "Try again" : "Make mesh"} />
        ) : null}
        <Button icon={FolderOpen} variant="ghost" onClick={() => navigate(paths.project(last.projectId))}>
          Open project
        </Button>
        <div className="flex-1" />
        <Button icon={Plus} onClick={newScan}>
          New scan
        </Button>
      </div>
    </div>
  );
}

export function ControlBar({ ready, reason, onStart }: { ready: boolean; reason: string | null; onStart: () => void }) {
  const phase = useSession((s) => s.session?.state ?? "idle");
  const action = useSession((s) => s.action);
  const last = useSession((s) => s.lastStopped);
  const pause = useSession((s) => s.pause);
  const resume = useSession((s) => s.resume);
  const stop = useSession((s) => s.stop);
  const rewind = useSession((s) => s.rewind);

  if (phase === "idle" && last) return <PostScanCard />;

  if (phase === "idle") {
    return (
      <div className="flex flex-col items-center gap-2">
        {!ready && reason ? (
          <div className="glass rounded-full border border-line-strong px-3 py-1 text-xs text-muted shadow-md">{reason}</div>
        ) : null}
        <Button
          size="xl"
          variant="primary"
          icon={Play}
          kbd="Space"
          disabled={!ready}
          loading={action === "start"}
          onClick={onStart}
          className="min-w-[200px] shadow-lg"
        >
          Start scan
        </Button>
      </div>
    );
  }

  if (phase === "stopping" || action === "stop") {
    return (
      <div className="glass flex h-14 items-center gap-3 rounded-2xl border border-line-strong px-5 text-sm text-fg shadow-lg">
        <Spinner size={16} className="text-accent" /> Saving the recording…
      </div>
    );
  }

  const paused = phase === "paused";
  return (
    <div className="glass flex items-center gap-2 rounded-2xl border border-line-strong p-2 shadow-lg" role="toolbar" aria-label="Scan controls">
      <Button size="lg" icon={Undo2} kbd="R" loading={action === "rewind"} onClick={() => void rewind(2)} title="Cut the last 2 seconds from the model">
        Rewind 2 s
      </Button>
      {paused ? (
        <Button size="lg" variant="primary" icon={Play} kbd="Space" loading={action === "resume"} onClick={() => void resume()} className="min-w-[140px]">
          Resume
        </Button>
      ) : (
        <Button size="lg" icon={Pause} kbd="Space" loading={action === "pause"} onClick={() => void pause()} className="min-w-[140px]">
          Pause
        </Button>
      )}
      <Button size="lg" variant="danger" icon={Square} kbd="Esc" onClick={() => void stop()} title="Stop and save the recording">
        Stop
      </Button>
    </div>
  );
}
