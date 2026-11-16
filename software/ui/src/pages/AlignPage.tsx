/**
 * Align two scans and merge them (APP_API.md "M4 first slice"). Two processed scans of the same part:
 * one stays in place, the other is moved onto it. The alignment runs from the shape alone or from three
 * or more matching points clicked on the two meshes; its verdict and figures are shown, both meshes can
 * be looked at together at the pose it found, and a merge makes a new scan of the two.
 *
 * The state is in the align store (store/align.ts); the 3D views in align/AlignCanvas.tsx.
 */
import {
  ArrowLeft,
  ArrowUpDown,
  CircleCheck,
  CircleExclamationPoint,
  Columns2,
  Combine,
  Eye,
  FolderOpen,
  Layers,
  Layers2,
  MapPin,
  Move3d,
  RefreshCw,
  Trash,
  TriangleExclamationPoint,
  Wand,
  X,
} from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, sentence } from "../api/http";
import type { AlignResult, Scan } from "../api/types";
import { JobProgress } from "../components/ScanBits";
import { ErrorBoundary } from "../components/shell/ErrorBoundary";
import { Button, IconButton } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/Dialog";
import { EmptyState, ProgressBar, Spinner } from "../components/ui/Feedback";
import { Field, Segmented, Select, TextInput } from "../components/ui/Form";
import { Callout, KeyValue } from "../components/ui/Layout";
import {
  alignFigures,
  chooseScans,
  completePairs,
  defaultMergeName,
  flattenPose,
  hasMesh,
  hasRecording,
  MIN_PAIRS,
  pairRows,
  pairsDisagree,
  pickHint,
  type Picks,
  type Side,
  type Vec3,
} from "../lib/align";
import { fmtBytes } from "../lib/format";
import { href, navigate, paths } from "../lib/router";
import { cn } from "../lib/util";
import { useAlign, type Problem } from "../store/align";
import { isActive, useJobs } from "../store/jobs";
import { useProjects } from "../store/projects";
import { PickView, SCAN_COLOR, TogetherView } from "./align/AlignCanvas";
import { useScanMeshes, type MeshLoad } from "./align/useScanMeshes";

type View = "pick" | "together";

/** What each scan does, in the words of the page. */
const ROLE: Record<Side, string> = { target: "Stays in place", source: "Is moved onto it" };

const SETUPS: { same: boolean; title: string; text: string }[] = [
  {
    same: true,
    title: "The part stayed where it was",
    text: "The table both scans share helps to line them up, and it stays in the merged scan.",
  },
  {
    same: false,
    title: "The part was moved or turned over",
    text: "Only the part itself is matched. Each scan's table is left out of the merged scan, and with it about 2 mm of the part's bottom edge.",
  },
];

const EMPTY_PICKS: Picks = { target: [], source: [] };

function Panel({ title, icon: Icon, children, aside }: { title: string; icon: typeof Layers; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="border-b border-line px-4 py-4" aria-label={title}>
      <div className="mb-3 flex items-center gap-2">
        <Icon size={14} className="text-muted" aria-hidden />
        <h2 className="flex-1 text-xs font-semibold uppercase tracking-wider text-muted">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

/** An error as the API gave it: what happened, then what to do. */
function ProblemCallout({ problem, className }: { problem: Problem; className?: string }) {
  return (
    <Callout tone="danger" icon={CircleExclamationPoint} title={problem.detail} className={className}>
      {problem.hint}
    </Callout>
  );
}

const ColorDot = ({ side, hollow }: { side: Side; hollow?: boolean }) => (
  <span
    aria-hidden
    className="inline-block h-2.5 w-2.5 shrink-0 rounded-full border"
    style={{ borderColor: SCAN_COLOR[side], background: hollow ? "transparent" : SCAN_COLOR[side] }}
  />
);

/** One scan's mesh in its own view (or how far its download is), with its role and name. */
const MeshPane = memo(function MeshPane({
  side,
  scan,
  load,
  picks,
  enabled,
  onPick,
  onRetry,
  className,
}: {
  side: Side;
  scan: Pick<Scan, "id" | "name"> | null;
  load: MeshLoad | undefined;
  picks: Vec3[];
  enabled: boolean;
  onPick: (side: Side, p: Vec3) => void;
  onRetry: (id: string) => void;
  className?: string;
}) {
  const pick = useCallback((p: Vec3) => onPick(side, p), [onPick, side]);
  return (
    <div className={cn("relative min-w-0 overflow-hidden", className)}>
      {scan && load?.phase === "ready" ? (
        <ErrorBoundary title="The 3D view couldn't start">
          <PickView
            geometry={load.geometry}
            picks={picks}
            color={SCAN_COLOR[side]}
            label={`3D view of “${scan.name}” (${ROLE[side].toLowerCase()}): click the part to pick a point`}
            enabled={enabled}
            onPick={pick}
          />
        </ErrorBoundary>
      ) : (
        <div className="absolute inset-0 grid place-items-center p-6">
          {!scan ? (
            <div className="text-sm text-muted">Choose a scan on the right.</div>
          ) : load?.phase === "error" ? (
            <EmptyState
              compact
              tone="danger"
              icon={CircleExclamationPoint}
              title="Couldn't open the mesh"
              actions={
                <Button size="sm" variant="primary" icon={RefreshCw} onClick={() => onRetry(scan.id)}>
                  Try again
                </Button>
              }
            >
              {load.detail}
              {load.hint ? <div className="mt-1 text-subtle">{load.hint}</div> : null}
            </EmptyState>
          ) : (
            <div className="w-[240px] text-center">
              <div className="mb-3 flex items-center justify-center gap-2 text-sm text-fg">
                <Spinner size={16} className="text-accent" />
                Loading the mesh…
              </div>
              <ProgressBar value={load?.phase === "loading" && load.total ? load.loaded / load.total : null} label={`Loading the mesh of ${scan.name}`} />
              <div className="mt-2 font-mono text-xs text-subtle tnum">
                {load?.phase === "loading" && load.loaded ? `${fmtBytes(load.loaded)}${load.total ? ` of ${fmtBytes(load.total)}` : ""}` : " "}
              </div>
            </div>
          )}
        </div>
      )}
      {scan ? (
        <div className="pointer-events-none absolute bottom-4 left-4 right-28">
          <div className="glass inline-flex max-w-full items-center gap-2 rounded-xl border border-line-strong px-3 py-1.5 text-xs shadow-md">
            <ColorDot side={side} />
            <span className="shrink-0 text-muted">{ROLE[side]}</span>
            <span className="min-w-0 truncate font-medium text-fg">{scan.name}</span>
            {picks.length ? (
              <span className="shrink-0 text-subtle tnum">
                · {picks.length} point{picks.length === 1 ? "" : "s"}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
});

const Together = memo(TogetherView);

/** The verdict sentence, accepted or not, and the small list of figures. */
function Verdict({ result }: { result: AlignResult }) {
  const ok = result.accepted;
  return (
    <div className="mt-3">
      <div className={cn("flex items-start gap-2.5 rounded-lg border px-3.5 py-3", ok ? "border-ok/30 bg-ok-soft/60" : "border-warn/30 bg-warn-soft/60")} role="status">
        {ok ? <CircleCheck size={19} className="mt-0.5 shrink-0 text-ok" aria-hidden /> : <TriangleExclamationPoint size={19} className="mt-0.5 shrink-0 text-warn" aria-hidden />}
        <div className="min-w-0">
          <div className={cn("text-[11px] font-semibold uppercase tracking-wider", ok ? "text-ok" : "text-warn")}>{ok ? "Accepted" : "Not accepted"}</div>
          <p className="mt-0.5 text-sm font-medium leading-5 text-fg">{sentence(result.verdict)}</p>
        </div>
      </div>
      <KeyValue
        dense
        className="mt-3"
        rows={alignFigures(result).map((f) => [
          f.label,
          <span className="tnum">
            <span className={cn(f.warn && "font-medium text-warn")}>{f.value}</span>
            {f.note ? <span className="text-xs text-subtle"> {f.note}</span> : null}
          </span>,
        ])}
      />
    </div>
  );
}

export default function AlignPage({ pid, target: wantTarget, source: wantSource }: { pid: string; target: string | null; source: string | null }) {
  const project = useProjects((s) => s.details[pid]);
  const projectError = useProjects((s) => s.detailError[pid]);
  const projectLoading = useProjects((s) => !!s.detailLoading[pid]);
  const fetchProject = useProjects((s) => s.fetchProject);
  const st = useAlign();
  /** The store holds this project's choices (after `open`; before, another project's or none). */
  const mine = st.pid === pid;

  const [view, setView] = useState<View>("pick");
  /** The typed name of the merged scan; null: the default, which follows the two scans. */
  const [name, setName] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    void fetchProject(pid);
  }, [pid, fetchProject]);

  useEffect(() => {
    useAlign.getState().open(pid, wantTarget, wantSource);
  }, [pid, wantTarget, wantSource]);

  useEffect(() => () => useAlign.getState().leave(), []);

  // ---------------------------------------------------------------- the two scans
  const scans = useMemo(() => (project ? project.scans.filter(hasMesh) : []), [project]);
  const targetId = mine ? st.target : null;
  const sourceId = mine ? st.source : null;
  const targetScan = scans.find((s) => s.id === targetId) ?? null;
  const sourceScan = (sourceId !== targetId ? scans.find((s) => s.id === sourceId) : null) ?? null;

  // Scans the project does not have (an old address), or none chosen yet: the first two with a mesh.
  useEffect(() => {
    if (!project || !mine) return;
    const c = chooseScans(
      scans.map((s) => s.id),
      targetId,
      sourceId,
    );
    if (c.target !== targetId || c.source !== sourceId) useAlign.getState().setScans(c.target, c.source);
  }, [project, mine, scans, targetId, sourceId]);

  // The address names the two scans, so a reload comes back to them.
  useEffect(() => {
    if (!mine || !targetScan || !sourceScan) return;
    if (targetScan.id !== wantTarget || sourceScan.id !== wantSource) navigate(paths.align(pid, targetScan.id, sourceScan.id), { replace: true });
  }, [mine, pid, targetScan, sourceScan, wantTarget, wantSource]);

  useEffect(() => setName(null), [targetId, sourceId]);

  const wanted = useMemo(
    () => [targetScan, sourceScan].filter((s): s is Scan => !!s).map((s) => ({ id: s.id, url: s.result?.mesh_url || api.fileUrl(pid, s.id, "mesh.ply") })),
    [pid, targetScan, sourceScan],
  );
  const { loads, retry } = useScanMeshes(wanted);
  const targetLoad = targetScan ? loads[targetScan.id] : undefined;
  const sourceLoad = sourceScan ? loads[sourceScan.id] : undefined;

  // ---------------------------------------------------------------- jobs and the result
  const alignJob = useJobs((s) => (mine && st.alignJobId ? s.jobs[st.alignJobId] : undefined));
  const mergeJob = useJobs((s) => (mine && st.mergeJobId ? s.jobs[st.mergeJobId] : undefined));
  const aligning = mine && (st.alignStarting || isActive(alignJob));
  const merging = mine && (st.mergeStarting || isActive(mergeJob));
  const busy = aligning || merging;
  const result = mine ? st.result : null;
  const merged = mine ? st.merged : null;
  const pose = useMemo(() => (result ? flattenPose(result.T) : null), [result]);

  // A new result: look at both scans together. No result (other scans, another setting): back to picking.
  useEffect(() => setView(result ? "together" : "pick"), [result]);

  const picks = mine ? st.picks : EMPTY_PICKS;
  const rows = pairRows(picks);
  const nPairs = completePairs(picks).length;
  const ready = !!targetScan && !!sourceScan;
  const defaultName = ready ? defaultMergeName(targetScan.name, sourceScan.name) : "";
  const mergeName = name ?? defaultName;
  /** A merged scan has no recording: it can be aligned to, not merged again. */
  const noRecording = [targetScan, sourceScan].find((s) => s && !hasRecording(s)) ?? null;

  const onPick = useCallback((side: Side, p: Vec3) => useAlign.getState().pick(side, p), []);
  const choose = (side: Side, id: string) => {
    const other = side === "target" ? sourceId : targetId;
    if (id === other) st.swap();
    else st.setScans(side === "target" ? id : targetId, side === "source" ? id : sourceId);
  };

  // ---------------------------------------------------------------- nothing to align
  if (projectError && !project) {
    const notFound = projectError.status === 404;
    return (
      <div className="grid h-full place-items-center p-6">
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
                <Button variant="primary" icon={RefreshCw} loading={projectLoading} onClick={() => void fetchProject(pid)}>
                  Try again
                </Button>
              ) : null}
            </>
          }
        >
          {notFound ? "It may have been moved to the trash, or renamed outside Studio." : projectError.detail}
          {!notFound && projectError.hint ? <div className="mt-1 text-subtle">{projectError.hint}</div> : null}
        </EmptyState>
      </div>
    );
  }
  if (!project || !mine) {
    return (
      <div className="grid h-full place-items-center viewport-bg">
        <div className="flex items-center gap-2.5 text-sm text-muted">
          <Spinner size={16} /> Loading the project…
        </div>
      </div>
    );
  }
  if (scans.length < 2) {
    return (
      <div className="grid h-full place-items-center p-6">
        <EmptyState
          icon={Combine}
          title="Two scans with a mesh are needed"
          actions={
            <Button variant="primary" icon={ArrowLeft} onClick={() => navigate(paths.project(pid))}>
              Back to the project
            </Button>
          }
        >
          {scans.length === 1 ? `Only “${scans[0].name}” has a mesh so far.` : "No scan of this project has a mesh yet."} Scan the part a second time (or make the
          mesh of another scan) on the project page, then come back.
        </EmptyState>
      </div>
    );
  }

  const targetGeometry = targetLoad?.phase === "ready" ? targetLoad.geometry : null;
  const sourceGeometry = sourceLoad?.phase === "ready" ? sourceLoad.geometry : null;
  const names = { target: targetScan?.name ?? "the first scan", source: sourceScan?.name ?? "the second scan" };
  /** Both scans in one view: needs a result's pose and both meshes (until then each scan's own view shows how far it is). */
  const together = view === "together" && !!pose && !!targetGeometry && !!sourceGeometry;

  return (
    <div className="flex h-full">
      <div className="relative min-w-0 flex-1 overflow-hidden viewport-bg">
        {/* The two views stay mounted (hidden) while both scans are shown together: coming back to pick costs nothing.
            Keyed by scan: swapping the two moves each view to the other side with its camera and its mesh. */}
        <div className={cn("absolute inset-0 grid grid-cols-2", together && "pointer-events-none invisible")} aria-hidden={together || undefined}>
          {(["target", "source"] as const).map((side, i) => {
            const scan = side === "target" ? targetScan : sourceScan;
            return (
              <MeshPane
                key={scan?.id ?? side}
                side={side}
                scan={scan}
                load={side === "target" ? targetLoad : sourceLoad}
                picks={picks[side]}
                enabled={!busy && !together}
                onPick={onPick}
                onRetry={retry}
                className={i ? "border-l border-line" : undefined}
              />
            );
          })}
        </div>
        {together ? (
          <ErrorBoundary title="The 3D view couldn't start">
            <Together
              target={targetGeometry}
              source={sourceGeometry}
              pose={pose}
              label={`3D view of both scans together: “${names.source}” moved onto “${names.target}”`}
              frameKey={`${targetGeometry.uuid}/${sourceGeometry.uuid}/${pose.join(",")}`}
            />
          </ErrorBoundary>
        ) : null}

        {/* breadcrumb and the choice of view */}
        <div className="pointer-events-none absolute left-4 top-4 flex flex-col items-start gap-2">
          <div className="glass pointer-events-auto flex h-9 items-center gap-1.5 rounded-xl border border-line-strong pl-1.5 pr-3 text-sm shadow-md">
            <IconButton icon={ArrowLeft} size="sm" label="Back to the project" onClick={() => navigate(paths.project(pid))} />
            <a href={href(paths.project(pid))} className="max-w-[220px] truncate text-muted hover:text-fg">
              {project.name}
            </a>
            <span className="text-subtle" aria-hidden>
              /
            </span>
            <span className="font-medium text-fg">Align two scans</span>
          </div>
          <div className="glass pointer-events-auto rounded-xl border border-line-strong p-1 shadow-md" role="toolbar" aria-label="View">
            <Segmented<View>
              label="View"
              size="sm"
              value={view}
              onChange={setView}
              options={[
                { value: "pick", label: "Side by side", icon: Columns2, title: "Each scan in its own view: click the part to pick matching points" },
                {
                  value: "together",
                  label: "Both together",
                  icon: Layers2,
                  disabled: !pose,
                  title: pose ? "Both scans in one view, the second moved onto the first, to judge the fit by eye" : "Align the two scans first",
                },
              ]}
            />
          </div>
        </div>

        {together ? (
          <div className="pointer-events-none absolute bottom-4 left-4 right-28">
            <div className="glass inline-block max-w-full rounded-xl border border-line-strong px-3 py-2 text-xs shadow-md">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <span className="inline-flex min-w-0 items-center gap-2">
                  <ColorDot side="target" />
                  <span className="truncate font-medium text-fg">{names.target}</span>
                  <span className="shrink-0 text-muted">stays in place</span>
                </span>
                <span className="inline-flex min-w-0 items-center gap-2">
                  <ColorDot side="source" />
                  <span className="truncate font-medium text-fg">{names.source}</span>
                  <span className="shrink-0 text-muted">moved onto it</span>
                </span>
              </div>
              <div className="mt-1 text-subtle">A good fit shows the two colours finely mixed where the scans overlap. Doubled edges or a gap mean it is off.</div>
            </div>
          </div>
        ) : null}
      </div>

      <aside className="relative flex w-[360px] shrink-0 flex-col overflow-y-auto border-l border-line bg-panel 2xl:w-[400px]" aria-label="Align and merge">
        <div className="border-b border-line px-4 py-4">
          <h1 className="text-base font-semibold text-fg">Align two scans</h1>
          <p className="mt-1 text-xs leading-5 text-subtle">Line up two scans of the same part, check the fit, then merge them into one new scan.</p>
        </div>

        <Panel title="The two scans" icon={Layers}>
          <Field label={ROLE.target} htmlFor="align-target" aside={<ColorDot side="target" />}>
            <Select id="align-target" value={targetId ?? ""} disabled={busy} onChange={(e) => choose("target", e.target.value)}>
              {scans.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                  {hasRecording(s) ? "" : " (merged)"}
                </option>
              ))}
            </Select>
          </Field>
          <div className="my-1.5 flex justify-center">
            <Button size="xs" variant="ghost" icon={ArrowUpDown} disabled={busy || !ready} onClick={() => st.swap()} title="The other scan stays in place; the picked points are kept">
              Swap the two
            </Button>
          </div>
          <Field label={ROLE.source} htmlFor="align-source" aside={<ColorDot side="source" />}>
            <Select id="align-source" value={sourceId ?? ""} disabled={busy} onChange={(e) => choose("source", e.target.value)}>
              {scans.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                  {hasRecording(s) ? "" : " (merged)"}
                </option>
              ))}
            </Select>
          </Field>
        </Panel>

        <Panel title="Between the two scans" icon={Move3d}>
          <div role="radiogroup" aria-label="Was the part moved between the two scans?" className="space-y-2">
            {SETUPS.map((o) => {
              const selected = st.sameSetup === o.same;
              return (
                <button
                  key={String(o.same)}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  disabled={busy}
                  onClick={() => st.setSameSetup(o.same)}
                  className={cn(
                    "flex w-full flex-col items-start gap-0.5 rounded-lg border px-3 py-2.5 text-left transition-[border-color,background-color,box-shadow] disabled:opacity-60",
                    selected ? "border-accent bg-accent-soft shadow-[0_0_0_3px_var(--c-accent-soft)]" : "border-line bg-surface-2 hover:border-line-strong",
                  )}
                >
                  <span className="text-sm font-medium text-fg">{o.title}</span>
                  <span className="text-xs leading-5 text-muted">{o.text}</span>
                </button>
              );
            })}
          </div>
        </Panel>

        <Panel title="Align" icon={Wand}>
          {aligning ? (
            <div role="status" aria-live="polite">
              <div className="mb-1.5 flex items-center gap-2 text-xs">
                <Spinner size={13} className="shrink-0 text-accent" />
                <span className="min-w-0 flex-1 truncate text-muted" title={alignJob?.message}>
                  {!alignJob || alignJob.state === "queued" ? "Starting…" : alignJob.message || "Working…"}
                </span>
                <span className="shrink-0 font-mono text-fg tnum">{alignJob && alignJob.state !== "queued" ? `${Math.round(alignJob.progress * 100)}%` : ""}</span>
              </div>
              <ProgressBar value={alignJob && alignJob.state !== "queued" ? alignJob.progress : null} label="Alignment progress" />
            </div>
          ) : (
            <>
              <Button variant={result ? "secondary" : "primary"} icon={Wand} className="w-full" disabled={!ready || merging} onClick={() => void st.align(false)}>
                {result ? "Align again from the shape alone" : "Align the two scans"}
              </Button>
              {!result ? <p className="mt-2 text-xs leading-5 text-subtle">Works from the shape of the two scans and takes a few seconds. Nothing is changed yet.</p> : null}
            </>
          )}
          {st.alignProblem ? <ProblemCallout problem={st.alignProblem} className="mt-3" /> : null}
          {result && !aligning ? (
            <>
              <Verdict result={result} />
              {pairsDisagree(result) ? (
                <Callout tone="warn" icon={TriangleExclamationPoint} title="Two of your points probably do not match" className="mt-3">
                  The picked points disagree by {result.pairs_rms_mm?.toFixed(1)} mm. Compare the numbers on the two scans, remove the point that is off, and align from the
                  points again.
                </Callout>
              ) : null}
              {!result.accepted ? (
                <Callout
                  tone="accent"
                  icon={MapPin}
                  title={result.start === "given" ? "Check your points, or add more" : "Next: pick matching points"}
                  className="mt-3"
                  actions={
                    view !== "pick" ? (
                      <Button size="sm" variant="primary" icon={Columns2} onClick={() => setView("pick")}>
                        Pick points
                      </Button>
                    ) : undefined
                  }
                >
                  {result.start === "given"
                    ? "Each number must sit on the same spot of the part in both scans. Remove a point that is off, add points further apart, then align from the points again."
                    : "The shape alone did not settle it. Click three or more matching spots on both scans, then choose “Align from these points”."}
                </Callout>
              ) : view !== "together" && pose ? (
                <div className="mt-3">
                  <Button size="sm" icon={Layers2} onClick={() => setView("together")}>
                    Show both together
                  </Button>
                </div>
              ) : null}
            </>
          ) : null}
        </Panel>

        <Panel
          title="Matching points"
          icon={MapPin}
          aside={
            rows.length ? (
              <Button size="xs" variant="ghost" icon={Trash} disabled={busy} onClick={() => st.clearPicks()}>
                Clear all
              </Button>
            ) : null
          }
        >
          <p className="text-xs leading-5 text-muted">
            A rough start for the alignment: click the same spot of the part on both scans, three times or more. A millimetre or two off is fine.
          </p>
          {view === "pick" ? (
            <p className="mt-2 text-xs font-medium leading-5 text-fg" role="status">
              {pickHint(picks, names)}
            </p>
          ) : (
            <div className="mt-2">
              <Button size="sm" icon={Columns2} onClick={() => setView("pick")}>
                Show the scans side by side to pick
              </Button>
            </div>
          )}
          {rows.length ? (
            <ul className="mt-3 divide-y divide-line rounded-lg border border-line bg-surface-2/50" aria-label="Picked points">
              {rows.map((r) => (
                <li key={r.n} className="flex items-center gap-2.5 py-1 pl-2.5 pr-1 text-xs">
                  <span className="grid h-5 min-w-5 place-items-center rounded-full bg-surface-3 px-1 font-mono font-semibold text-fg tnum">{r.n}</span>
                  <ColorDot side="target" hollow={!r.target} />
                  <ColorDot side="source" hollow={!r.source} />
                  <span className={cn("min-w-0 flex-1 truncate", r.target && r.source ? "text-muted" : "text-warn")}>
                    {r.target && r.source ? "On both scans" : `Not yet on “${r.target ? names.source : names.target}”`}
                  </span>
                  <IconButton icon={X} size="xs" label={`Remove point ${r.n}`} disabled={busy} onClick={() => st.removePair(r.n - 1)} />
                </li>
              ))}
            </ul>
          ) : null}
          <Button
            className="mt-3 w-full"
            variant={result && !result.accepted && nPairs >= MIN_PAIRS ? "primary" : "secondary"}
            icon={MapPin}
            disabled={!ready || busy || nPairs < MIN_PAIRS}
            title={nPairs < MIN_PAIRS ? `Pick at least ${MIN_PAIRS} points on both scans first` : undefined}
            onClick={() => void st.align(true)}
          >
            Align from these points
          </Button>
        </Panel>

        <Panel title="Merge" icon={Combine}>
          {merged ? (
            <Callout
              tone="ok"
              icon={CircleCheck}
              title={`Merged into “${merged.scan?.name ?? mergeName}”`}
              actions={
                <>
                  <Button size="sm" variant="primary" icon={Eye} onClick={() => navigate(paths.viewer(pid, merged.scan_id))}>
                    Open the merged scan
                  </Button>
                  <Button size="sm" icon={ArrowLeft} onClick={() => navigate(paths.project(pid))}>
                    Back to the project
                  </Button>
                </>
              }
            >
              The new scan is in the project. The two scans it was made from are unchanged.
            </Callout>
          ) : merging ? (
            mergeJob ? (
              <JobProgress job={mergeJob} label="Merging progress" cancelTitle="Stop merging. Nothing is added to the project." icon={Combine} />
            ) : (
              <ProgressBar value={null} label="Merging progress" />
            )
          ) : !result ? (
            <p className="text-sm text-muted">Align the two scans first. The merge puts the second scan where the alignment found it.</p>
          ) : noRecording ? (
            <Callout tone="neutral" icon={Combine} title={`“${noRecording.name}” cannot be merged again`}>
              It is a merged scan and has no recording of its own. You can align to it and compare the two here; to merge, choose two scans that were recorded.
            </Callout>
          ) : (
            <>
              <Field label="Name of the new scan" htmlFor="merge-name">
                <TextInput id="merge-name" value={mergeName} maxLength={120} placeholder={defaultName} onChange={(e) => setName(e.target.value)} />
              </Field>
              <p className="mt-2 text-xs leading-5 text-subtle">
                Both recordings are fused again into one new scan, which takes about as long as making both meshes. The two scans stay as they are.
              </p>
              {!result.accepted ? (
                <p className="mt-2 flex items-start gap-1.5 text-xs leading-5 text-warn">
                  <TriangleExclamationPoint size={13} className="mt-1 shrink-0" aria-hidden />
                  The alignment was not accepted, so the second scan may be in the wrong place. Merging asks you to confirm.
                </p>
              ) : null}
              <Button
                className="mt-3 w-full"
                variant="primary"
                icon={Combine}
                loading={st.mergeStarting}
                disabled={aligning}
                onClick={() => (result.accepted ? void st.merge(mergeName) : setConfirming(true))}
              >
                Merge into a new scan
              </Button>
            </>
          )}
          {st.mergeCancelled && !merging && !merged ? (
            <Callout tone="neutral" title="Merging was cancelled" className="mt-3">
              Nothing was added to the project, and the two scans are unchanged. Merge again when you are ready.
            </Callout>
          ) : null}
          {st.mergeProblem && !merging ? <ProblemCallout problem={st.mergeProblem} className="mt-3" /> : null}
        </Panel>
      </aside>

      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        title="Merge although the alignment was not accepted?"
        confirmLabel="Merge anyway"
        tone="danger"
        onConfirm={() => st.merge(mergeName)}
      >
        The second scan may be in the wrong place. The merged scan would then show the part twice, or smeared. Look at both scans together first: if they do not sit on
        each other, pick matching points and align again instead.
      </ConfirmDialog>
    </div>
  );
}
