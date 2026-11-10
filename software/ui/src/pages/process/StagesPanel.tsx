/**
 * The four stages of a scan's processing: Track (quality), Straighten (loop closure), Fuse
 * (voxel size) and Refine with lasers. Each shows its state, the engine's summary, its job's
 * progress with Cancel, and Run / Re-run. Re-running asks first when there are edits: a new base
 * mesh starts them over.
 */
import { Crosshair, Layers, Play, RotateCcw, Spline, Waypoints, type LucideIcon } from "lucide-react";
import { useState, type ReactNode } from "react";
import type { Job, ProcessQuality, ProcessState, StageId, StageInfo } from "../../api/types";
import { JobProgress } from "../../components/ScanBits";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/Dialog";
import { Segmented } from "../../components/ui/Form";
import { fmtDateTime, fmtFixed, fmtInt, fmtRelative } from "../../lib/format";
import { cn } from "../../lib/util";
import { isActive, useJobs } from "../../store/jobs";
import { useProcess } from "../../store/process";
import { Help, Panel, StageBadge } from "./bits";

export const VOXELS = [0.5, 1, 2] as const;

const STAGES: Record<StageId, { label: string; icon: LucideIcon; what: string }> = {
  track: { label: "Track", icon: Waypoints, what: "Follows the scanner through the recording and fuses the depth into a mesh." },
  straighten: { label: "Straighten", icon: Spline, what: "Finds where the scan came back to a place it had seen, and closes those loops so long scans line up." },
  fuse: { label: "Fuse", icon: Layers, what: "Builds the mesh again from the camera poses, at the detail you choose." },
  refine: {
    label: "Refine with lasers",
    icon: Crosshair,
    what: "Pulls the surface onto the laser lines where they disagree, with a smooth correction. The lines are measured on their own and are more accurate.",
  },
};

/** The live job of a stage: its `job_id`, from the jobs store (or the running job the state reports). */
function useStageJob(stage: StageInfo, state: ProcessState): Job | null {
  return useJobs((s) => {
    const id = stage.job_id ?? (stage.state === "running" ? state.job?.id : null);
    if (!id) return null;
    const j = s.jobs[id] ?? (state.job?.id === id ? state.job : null);
    return j && isActive(j) ? j : null;
  });
}

function StageCard({
  id,
  stage,
  state,
  children,
  details,
  runLabel,
  blocked,
  busy,
  onRun,
}: {
  id: StageId;
  stage: StageInfo;
  state: ProcessState;
  children?: ReactNode;
  details?: ReactNode;
  runLabel: string;
  /** Why it can't run now (shown under the button), or null. */
  blocked: string | null;
  busy: boolean;
  onRun: () => void;
}) {
  const meta = STAGES[id];
  const Icon = meta.icon;
  const job = useStageJob(stage, state);
  const running = stage.state === "running" || !!job;
  return (
    <li className={cn("rounded-xl border bg-surface-2/50 p-3", running ? "border-accent-line" : "border-line")}>
      <div className="flex items-start gap-2.5">
        <div className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-line bg-surface text-muted">
          <Icon size={14} aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="flex-1 text-sm font-semibold text-fg">{meta.label}</h3>
            <StageBadge state={running ? "running" : stage.state} />
          </div>
          <Help className="mt-0.5">{meta.what}</Help>
        </div>
      </div>
      {stage.summary || details ? (
        <div className="mt-2.5 space-y-1 pl-[38px]">
          {details}
          {stage.summary ? (
            <p className={cn("text-xs leading-5", stage.state === "failed" ? "text-danger" : "text-muted")}>{stage.summary}</p>
          ) : null}
          {stage.when && !running ? (
            <p className="text-[11px] text-subtle" title={fmtDateTime(stage.when)}>
              {fmtRelative(stage.when)}
            </p>
          ) : null}
        </div>
      ) : null}
      {running && job ? (
        <JobProgress job={job} className="mt-3 pl-[38px]" label={`${meta.label} progress`} cancelTitle="Stop this stage. The mesh you have stays as it is." icon={meta.icon} />
      ) : (
        <div className="mt-3 space-y-2 pl-[38px]">
          {children}
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant={stage.state === "none" ? "primary" : "secondary"}
              icon={stage.state === "none" ? Play : RotateCcw}
              disabled={busy || !!blocked}
              onClick={onRun}
            >
              {runLabel}
            </Button>
            {blocked ? <span className="text-[11px] text-subtle">{blocked}</span> : null}
          </div>
        </div>
      )}
    </li>
  );
}

export function StagesPanel({ pid, sid, state, busy }: { pid: string; sid: string; state: ProcessState; busy: boolean }) {
  const runStage = useProcess((s) => s.runStage);
  const { track, straighten, fuse, refine } = state.stages;
  const [quality, setQuality] = useState<ProcessQuality>(track.quality ?? "full");
  const [voxel, setVoxel] = useState<number>(fuse.voxel_mm ?? 1);
  const [confirm, setConfirm] = useState<{ stage: StageId; run: () => Promise<unknown> } | null>(null);
  const edits = state.edits.length;
  const tracked = track.state === "done";

  /** Run now, or ask first when edits would be lost. */
  const go = (stage: StageId, run: () => Promise<unknown>) => {
    if (edits > 0) setConfirm({ stage, run });
    else void run();
  };

  const loops = straighten.loops;
  const straightenDetails =
    straighten.state === "done" || straighten.state === "skipped" ? (
      loops && loops > 0 ? (
        <p className="text-xs text-fg">
          Closed <b className="font-semibold tnum">{fmtInt(loops)}</b> loop{loops === 1 ? "" : "s"}
          {straighten.correction_mm !== null ? (
            <>
              {" "}
              · moved the scan by <b className="font-semibold tnum">{fmtFixed(straighten.correction_mm, 1)} mm</b>
              {straighten.correction_deg !== null ? <span className="tnum"> and {fmtFixed(straighten.correction_deg, 2)}°</span> : null}
            </>
          ) : null}
        </p>
      ) : !straighten.summary ? (
        <p className="text-xs text-muted">
          {straighten.state === "skipped"
            ? "Skipped: the poses from Track are used as they are."
            : "No loops found: the scan never came back to a place it had already seen, so there was nothing to correct and the mesh is unchanged."}
        </p>
      ) : null
    ) : null;

  return (
    <Panel title="Stages" icon={Waypoints}>
      <ol className="space-y-2.5" aria-label="Processing stages">
        <StageCard
          id="track"
          stage={track}
          state={state}
          busy={busy}
          blocked={null}
          runLabel={track.state === "none" ? "Run Track" : "Re-run"}
          onRun={() => go("track", () => runStage(pid, sid, "track", { quality }))}
          details={
            track.state === "done" && (track.tracked_pct !== null || track.quality) ? (
              <p className="text-xs text-fg">
                {track.tracked_pct !== null ? (
                  <>
                    <b className="font-semibold tnum">{fmtFixed(track.tracked_pct, 0)} %</b> of the frames tracked
                  </>
                ) : null}
                {track.quality ? <span className="text-muted">{track.tracked_pct !== null ? " · " : ""}{track.quality === "full" ? "full quality" : "preview quality"}</span> : null}
              </p>
            ) : null
          }
        >
          <Segmented<ProcessQuality>
            label="Track quality"
            size="sm"
            value={quality}
            onChange={setQuality}
            disabled={busy}
            options={[
              { value: "preview", label: "Preview", title: "Every other frame at half resolution: quick to check" },
              { value: "full", label: "Full", title: "Every frame at full resolution: best accuracy" },
            ]}
          />
        </StageCard>

        <StageCard
          id="straighten"
          stage={straighten}
          state={state}
          busy={busy}
          blocked={tracked ? null : "Run Track first"}
          runLabel={straighten.state === "none" ? "Straighten" : "Re-run"}
          onRun={() => go("straighten", () => runStage(pid, sid, "straighten"))}
          details={straightenDetails}
        />

        <StageCard
          id="fuse"
          stage={fuse}
          state={state}
          busy={busy}
          blocked={tracked ? null : "Run Track first"}
          runLabel={fuse.state === "none" ? "Fuse" : "Re-run"}
          onRun={() => go("fuse", () => runStage(pid, sid, "fuse", { voxel_mm: voxel }))}
          details={
            fuse.state === "done" && fuse.voxel_mm !== null && !fuse.summary ? (
              <p className="text-xs text-fg">
                Voxel size <b className="font-semibold tnum">{fmtFixed(fuse.voxel_mm, 1)} mm</b>
              </p>
            ) : null
          }
        >
          <Segmented<string>
            label="Voxel size"
            size="sm"
            value={String(voxel)}
            onChange={(v) => setVoxel(Number(v))}
            disabled={busy || !tracked}
            options={VOXELS.map((v) => ({ value: String(v), label: `${v} mm`, title: v === 0.5 ? "Finest detail, about 4× the triangles of 1 mm" : v === 1 ? "The usual detail" : "Coarse and light" }))}
          />
          <Help>Smaller voxels keep finer detail; each halving makes about 4× the triangles.</Help>
        </StageCard>

        <StageCard
          id="refine"
          stage={refine}
          state={state}
          busy={busy}
          blocked={tracked && state.base ? null : "Run Track first"}
          runLabel={refine.state === "none" ? "Refine" : "Re-run"}
          onRun={() => go("refine", () => runStage(pid, sid, "refine"))}
          details={
            (refine.state === "done" || refine.state === "skipped") && refine.outliers ? (
              <p className="text-xs text-muted">
                <b className="font-semibold text-fg tnum">{fmtInt(refine.outliers)}</b> laser point{refine.outliers === 1 ? "" : "s"} lay off the surface
                (glints, reflections) and {refine.outliers === 1 ? "was" : "were"} left out.
              </p>
            ) : null
          }
        />
      </ol>

      {state.base ? (
        <p className="mt-3 text-[11.5px] text-subtle">
          Base mesh: <span className="font-mono text-muted tnum">{fmtInt(state.base.triangles)}</span> triangles, from {STAGES[state.base.stage]?.label ?? state.base.stage}.
          {edits ? ` Edits apply on top of it.` : ""}
        </p>
      ) : null}

      <ConfirmDialog
        open={!!confirm}
        onClose={() => setConfirm(null)}
        title={confirm ? `Re-run ${STAGES[confirm.stage].label}?` : ""}
        confirmLabel={confirm ? `Re-run ${STAGES[confirm.stage].label}` : "Re-run"}
        onConfirm={async () => {
          await confirm?.run();
        }}
      >
        This starts the edits over: {edits === 1 ? "your edit is" : `your ${fmtInt(edits)} edits are`} cleared, because the faces of a new mesh don't
        match them. The raw recording is kept, and you can save the steps as a recipe first.
      </ConfirmDialog>
    </Panel>
  );
}
