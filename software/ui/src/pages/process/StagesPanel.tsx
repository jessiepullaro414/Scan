/**
 * The stages of a scan's processing: Follow the camera (quality), Straighten (loop closure), Build the surface (at the
 * Preset box's mesh detail, UX_PLAN U49), Refine from the images (a trial build, marked "Trial") and Refine with lasers. Each shows its state, the engine's summary, its job's
 * progress with Cancel, and Run / Re-run. Re-running asks first when there are edits: a new base
 * mesh starts them over.
 */
import { Crosshair, Images, Layers, Play, RotateCcw, Spline, TriangleExclamationPoint, Waypoints, type LucideIcon } from "lucide-react";
import { useState, type ReactNode } from "react";
import type { Job, MakingState, ProcessQuality, ProcessState, Scan, StageId, StageInfo } from "../../api/types";
import { JobProgress } from "../../components/ScanBits";
import { Badge, type BadgeTone } from "../../components/ui/Badge";
import { makingRows, type MakingRowState } from "../../lib/making";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/Dialog";
import { Segmented } from "../../components/ui/Form";
import { fmtDateTime, fmtDetailMm, fmtFixed, fmtInt, fmtRelative } from "../../lib/format";
import { stageBlock, type StageAction } from "../../lib/keepMode";
import { effectiveValue } from "../../lib/presets";
import { DEFAULT_QUALITY, QUALITIES, QUALITY_TEXT, normalizeQuality, qualityName } from "../../lib/quality";
import { STAGE_TITLES, stageRunning, straightenDoneParts, straightenSkippedLine } from "../../lib/stages";
import { INCOMPLETE_TITLE, PATH_WARNING_TITLE, splitWarnings } from "../../lib/pathWarning";
import { cn } from "../../lib/util";
import { isActive, useJobs } from "../../store/jobs";
import { presetKey, usePresets } from "../../store/presets";
import { useProcess } from "../../store/process";
import { Help, Panel, StageBadge } from "./bits";
import { PresetPanel } from "./PresetPanel";

/** What the stages read of the scan: what it keeps on disk (R2) and whether it was merged. */
type KeepFields = Pick<Scan, "keep" | "freed" | "has_recording" | "merged_from">;

// U49: the names from lib/stages.ts; a trial build carries a "Trial" badge in its title
const STAGES: Record<StageId, { label: string; icon: LucideIcon; what: string; trial: boolean }> = {
  track: {
    label: STAGE_TITLES.track.label,
    trial: false,
    icon: Waypoints,
    what: "Follows the scanner through the recording and builds a first mesh from the depth, at the preset's mesh detail.",
  },
  straighten: {
    label: STAGE_TITLES.straighten.label,
    trial: false,
    icon: Spline,
    what: "Finds where the scan came back to a place it had seen and closes those loops, then lines the scan up where the laser lines of different frames cross.",
  },
  fuse: {
    label: STAGE_TITLES.fuse.label,
    trial: false,
    icon: Layers,
    what: "Builds the mesh again from the camera path: after Straighten, or to change the mesh detail. “Follow the camera” already made a first mesh, so this one is optional.",
  },
  spacetime: {
    label: STAGE_TITLES.spacetime.label,
    trial: STAGE_TITLES.spacetime.trial,
    icon: Images,
    what: "Measures the surface again against every frame: each frame lights it with a different arrangement of dots, so the small errors of single frames don't add up. A trial build: it needs a graphics card to be quick.",
  },
  refine: {
    label: STAGE_TITLES.refine.label,
    trial: false,
    icon: Crosshair,
    what: "Pulls the surface onto the laser lines where they disagree, with a smooth correction. The lines are measured on their own, so they correct the surface.",
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
  optional = false,
}: {
  id: StageId;
  stage: StageInfo;
  state: ProcessState;
  children?: ReactNode;
  details?: ReactNode;
  runLabel: string;
  /** Not run, and not needed: "Optional" instead of "Not run" (Build the surface after "Follow the camera"). */
  optional?: boolean;
  /** Why it can't run now (shown under the button), or null. */
  blocked: string | null;
  busy: boolean;
  onRun: () => void;
}) {
  const meta = STAGES[id];
  const Icon = meta.icon;
  // R22: "Running" only from the stage's own state (a stage run alone); during Make mesh again the scan's stages stay
  // as they are and the run is shown above them (MakingPanel), from `making`
  const running = stageRunning(stage.state);
  const stageJob = useStageJob(stage, state);
  const job = running ? (stageJob ?? (state.job && isActive(state.job) ? state.job : null)) : null;
  return (
    <li className={cn("rounded-xl border bg-surface-2/50 p-3", running ? "border-accent-line" : "border-line")}>
      <div className="flex items-start gap-2.5">
        <div className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-line bg-surface text-muted">
          <Icon size={14} aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="flex min-w-0 flex-1 flex-wrap items-center gap-x-1.5 text-sm font-semibold text-fg">
              {meta.label}
              {meta.trial ? (
                <Badge tone="accent" title="A trial build: try it, and compare with the mesh before it">
                  Trial
                </Badge>
              ) : null}
            </h3>
            {optional && stage.state === "none" && !running ? (
              <Badge tone="neutral" dot>
                Optional
              </Badge>
            ) : (
              <StageBadge state={running ? "running" : stage.state} />
            )}
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

/**
 * A stage's warnings (`stage.warnings`, through `splitWarnings`): the camera path's in the warning style with its title
 * (ENGINE_PLAN X0a: the path jumped where no hand moves), the mesh model's that ran out of room in the same style
 * with its own title (ENGINE_PLAN R2-5: surface is missing from that stage's mesh), every other note quietly, without
 * a title (UX_PLAN R1). Each stage sends its own only: the path's comes with "Follow the camera", not again under the
 * stages built on that path. It warns only.
 */
function StageNotes({ said }: { said: { path: string[]; incomplete: string[]; other: string[] } }) {
  const warned = (title: string, w: string) => (
    <p key={w} className="flex gap-1.5 rounded-md border border-warn/30 bg-warn-soft px-2 py-1.5 text-xs leading-5 text-fg" role="note">
      <TriangleExclamationPoint size={13} className="mt-[3px] shrink-0 text-warn" aria-hidden />
      <span>
        <b className="font-semibold">{title}.</b> {w}
      </span>
    </p>
  );
  return (
    <>
      {said.path.map((w) => warned(PATH_WARNING_TITLE, w))}
      {said.incomplete.map((w) => warned(INCOMPLETE_TITLE, w))}
      {said.other.map((w) => (
        <p key={w} className="text-xs leading-5 text-muted" role="note">
          {w}
        </p>
      ))}
    </>
  );
}

const ROW_BADGE: Record<MakingRowState, { label: string; tone: BadgeTone }> = {
  done: { label: "Done", tone: "ok" },
  running: { label: "Running", tone: "accent" },
  waiting: { label: "To come", tone: "neutral" },
  skipped: { label: "Skipped", tone: "neutral" },
  failed: { label: "Failed", tone: "danger" },
};

/**
 * UX_PLAN R15, R22: "Make mesh again" (or a recipe on everything) builds the new mesh beside this one (APP_API.md, R2):
 * its stages, from `ProcessState.making`, one running at a time, with the job's progress, the time left for the whole
 * job (R18) and Cancel. The stage cards below stay those of the mesh the scan has now.
 */
function MakingPanel({ making, job }: { making: MakingState; job: Job | null }) {
  const progress = job?.progress ?? 0;
  const rows = makingRows(making, progress);
  return (
    <section className="mb-3 rounded-xl border border-accent-line bg-accent-soft/40 p-3" aria-label="The new mesh being made">
      <h3 className="text-sm font-semibold text-fg">The new mesh being made</h3>
      <Help className="mt-0.5">It is built beside the mesh you have. The stages below stay as they are until it is ready, and stay so if you cancel it.</Help>
      <ol className="mt-2.5 space-y-1.5" aria-label="Stages of the new mesh">
        {rows.map((r) => (
          <li key={r.id} className="flex items-start gap-2 text-xs" aria-current={r.state === "running" ? "step" : undefined}>
            <span className="min-w-0 flex-1">
              <span className={cn("block font-medium", r.state === "waiting" ? "text-muted" : "text-fg")}>{r.label}</span>
              {r.summary && r.state !== "running" ? <span className="block leading-5 text-subtle">{r.summary}</span> : null}
            </span>
            <Badge tone={ROW_BADGE[r.state].tone} dot={r.state !== "running"} spinner={r.state === "running"}>
              {ROW_BADGE[r.state].label}
            </Badge>
          </li>
        ))}
      </ol>
      {job && isActive(job) ? <JobProgress job={job} className="mt-3" label="Making the new mesh" /> : null}
    </section>
  );
}

export function StagesPanel({ pid, sid, state, busy, scan }: { pid: string; sid: string; state: ProcessState; busy: boolean; scan?: KeepFields }) {
  const runStage = useProcess((s) => s.runStage);
  // the job building the new mesh, live from the jobs store (its progress comes in `job` messages)
  const runJob = useJobs((s) => (state.making && state.job ? (s.jobs[state.job.id] ?? state.job) : null));
  // R2: what a freed (or merged) scan can no longer run, in the same words as the engine's refusals (lib/keepMode.ts)
  const gone = (a: StageAction) => (scan ? stageBlock(scan, state, a) : null);
  const { track, straighten, fuse, spacetime, refine } = state.stages;
  const [quality, setQuality] = useState<ProcessQuality>(normalizeQuality(track.quality) ?? DEFAULT_QUALITY);
  // U49: one "Mesh detail" control, the Preset box's; "Build the surface" uses it (before the preset is read: the
  // detail of the last build, else of the track, else 1 mm as before)
  const preset = usePresets((s) => s.scans[presetKey(pid, sid)]);
  const presetVoxel = preset ? effectiveValue(preset, "voxel_mm") : null;
  const voxel = typeof presetVoxel === "number" && presetVoxel > 0 ? presetVoxel : (fuse.voxel_mm ?? track.voxel_mm ?? 1);
  const [confirm, setConfirm] = useState<{ stage: StageId; run: () => Promise<unknown> } | null>(null);
  const edits = state.edits.length;
  const tracked = track.state === "done";
  const trackWarnings = splitWarnings(track); // ENGINE_PLAN X0a: the camera path's warning (none for an older scan); R1
  // ENGINE_PLAN R2-5: what Straighten's and Build the surface's own fuse warned (none from an older server)
  const notesOf = (stage: StageInfo) => {
    const said = splitWarnings(stage);
    return said.path.length || said.incomplete.length || said.other.length ? <StageNotes said={said} /> : null;
  };
  const straightenNotes = notesOf(straighten);
  const fuseNotes = notesOf(fuse);

  /** Run now, or ask first when edits would be lost. */
  const go = (stage: StageId, run: () => Promise<unknown>) => {
    if (edits > 0) setConfirm({ stage, run });
    else void run();
  };

  const skippedLine = straightenSkippedLine(straighten); // R22: not "Closed 48 loops" under "Skipped"
  // the loops closed and, when AprilTags held the scan (X8 step 2), the tags: the move is theirs together
  const doneParts = straightenDoneParts(straighten);
  const straightenDetails =
    skippedLine ? (
      <p className="text-xs text-muted">{skippedLine}</p>
    ) : straighten.state === "done" || straighten.state === "skipped" ? (
      doneParts ? (
        <p className="text-xs text-fg tnum">
          {doneParts.map((p, i) => (p.b ? <b key={i} className="font-semibold">{p.t}</b> : <span key={i}>{p.t}</span>))}
        </p>
      ) : !straighten.summary ? (
        <p className="text-xs text-muted">
          {straighten.state === "skipped"
            ? "Skipped: the camera path from “Follow the camera” is used as it is."
            : "No loops found: the scan never came back to a place it had already seen, so there was nothing to correct and the mesh is unchanged."}
        </p>
      ) : null
    ) : null;

  return (
    <Panel title="Stages" icon={Waypoints}>
      {state.making ? <MakingPanel making={state.making} job={runJob} /> : null}
      <PresetPanel pid={pid} sid={sid} busy={busy} />
      <ol className="space-y-2.5" aria-label="Processing stages">
        <StageCard
          id="track"
          stage={track}
          state={state}
          busy={busy}
          blocked={gone("track")}
          runLabel={track.state === "none" ? "Run" : "Run again"}
          onRun={() => go("track", () => runStage(pid, sid, "track", { quality }))}
          details={
            track.state === "done" && (track.tracked_pct !== null || track.quality || trackWarnings.path.length || trackWarnings.incomplete.length || trackWarnings.other.length) ? (
              <>
                {track.tracked_pct !== null || track.quality ? (
                  <p className="text-xs text-fg">
                    {track.tracked_pct !== null ? (
                      <>
                        <b className="font-semibold tnum">{fmtFixed(track.tracked_pct, 0)}&#8239;%</b> of the frames tracked
                      </>
                    ) : null}
                    {qualityName(track.quality) ? <span className="text-muted">{track.tracked_pct !== null ? " · " : ""}{qualityName(track.quality)}</span> : null}
                  </p>
                ) : null}
                <StageNotes said={trackWarnings} />
              </>
            ) : null
          }
        >
          <Segmented<ProcessQuality>
            label="Quality"
            size="sm"
            value={quality}
            onChange={setQuality}
            disabled={busy || !!gone("track")}
            options={QUALITIES.map((q) => ({ value: q, label: QUALITY_TEXT[q].short, title: `${QUALITY_TEXT[q].label}: ${QUALITY_TEXT[q].help}` }))}
          />
        </StageCard>

        <StageCard
          id="straighten"
          stage={straighten}
          state={state}
          busy={busy}
          blocked={gone("straighten") ?? (tracked ? null : "Run “Follow the camera” first")}
          runLabel={straighten.state === "none" ? "Straighten" : "Run again"}
          onRun={() => go("straighten", () => runStage(pid, sid, "straighten"))}
          details={
            straightenDetails || straightenNotes ? (
              <>
                {straightenDetails}
                {straightenNotes}
              </>
            ) : null
          }
        />

        <StageCard
          id="fuse"
          stage={fuse}
          state={state}
          busy={busy}
          blocked={gone("fuse") ?? (tracked ? null : "Run “Follow the camera” first")}
          runLabel={fuse.state === "none" ? "Build" : "Run again"}
          optional={tracked}
          onRun={() => go("fuse", () => runStage(pid, sid, "fuse", { voxel_mm: voxel }))}
          details={
            (fuse.state === "done" && fuse.voxel_mm !== null && !fuse.summary) || fuseNotes ? (
              <>
                {fuse.state === "done" && fuse.voxel_mm !== null && !fuse.summary ? (
                  <p className="text-xs text-fg">
                    Built at mesh detail <b className="font-semibold tnum">{fmtDetailMm(fuse.voxel_mm)}</b>
                  </p>
                ) : null}
                {fuseNotes}
              </>
            ) : null
          }
        >
          <Help>
            At the mesh detail in the Preset box above: <b className="font-semibold text-fg tnum">{fmtDetailMm(voxel)}</b>. Finer mesh detail keeps
            smaller features; each halving makes about 4× the triangles.
          </Help>
        </StageCard>

        {spacetime ? (
          <StageCard
            id="spacetime"
            stage={spacetime}
            state={state}
            busy={busy}
            blocked={gone("spacetime") ?? (tracked && state.base ? null : "Run “Follow the camera” first")}
            runLabel={spacetime.state === "none" ? "Refine" : "Run again"}
            onRun={() => go("spacetime", () => runStage(pid, sid, "spacetime"))}
          >
            <Help>The rest keeps the fused surface. Run it before Refine with lasers, which starts over after it.</Help>
          </StageCard>
        ) : null}

        <StageCard
          id="refine"
          stage={refine}
          state={state}
          busy={busy}
          blocked={gone("refine") ?? (tracked && state.base ? null : "Run “Follow the camera” first")}
          runLabel={refine.state === "none" ? "Refine" : "Run again"}
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
          Starting mesh: <span className="font-mono text-muted tnum">{fmtInt(state.base.triangles)}</span> triangles, from {state.base.stage === "fine_walls" ? "Make only the thin walls finer" : (STAGES[state.base.stage]?.label ?? state.base.stage)}.
          {edits ? ` Edits apply on top of it.` : ""}
        </p>
      ) : null}

      <ConfirmDialog
        open={!!confirm}
        onClose={() => setConfirm(null)}
        title={confirm ? `Run “${STAGES[confirm.stage].label}” again?` : ""}
        confirmLabel={confirm ? "Run it again" : "Run again"}
        onConfirm={() => confirm?.run()} // U6: a run that could not start keeps the dialog open
      >
        This starts the edits over: {edits === 1 ? "your edit is" : `your ${fmtInt(edits)} edits are`} cleared, because the faces of a new mesh don't
        match them. The raw recording is kept, and you can save the steps as a recipe first.
      </ConfirmDialog>
    </Panel>
  );
}
