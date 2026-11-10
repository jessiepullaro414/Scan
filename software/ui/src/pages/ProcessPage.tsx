/**
 * The processing studio of one scan (`#/projects/:pid/scans/:sid/process`, APP_API.md "M3
 * phase 2"). Left: the stages (Track, Straighten, Fuse), the edits and their history with undo /
 * redo. Middle: the 3D view with selection tools (box, lasso, brush, connected) and overlays
 * (holes, trajectory, before / after). Right: the mesh in numbers, downloads and recipes.
 *
 * The mesh is downloaded again whenever `current.version` changes; the camera stays where it is.
 */
import {
  ArrowLeft,
  Blend,
  Box,
  Brush,
  CircleDashed,
  CircleExclamationPoint,
  Contrast,
  Crop,
  Eye,
  EyeOff,
  Focus,
  Grid2x2,
  Lasso,
  MousePointerClick,
  Move3d,
  Orbit,
  RefreshCw,
  Route,
  Sparkles,
  SquareDashed,
  Target,
  Trash,
  Waypoints,
  X,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Sphere, Vector3 } from "three";
import { ApiError, api } from "../api/http";
import type { HoleOutline, ProcessState, TrajectoryView } from "../api/types";
import { JobProgress, MakeMeshButton, ScanStatusBadge } from "../components/ScanBits";
import { ErrorBoundary } from "../components/shell/ErrorBoundary";
import { Button, IconButton } from "../components/ui/Button";
import { EmptyState, Kbd, ProgressBar, Skeleton, Spinner } from "../components/ui/Feedback";
import { Segmented, Slider } from "../components/ui/Form";
import { Callout } from "../components/ui/Layout";
import { fileStem, fmtBytes, fmtDateTime, fmtDuration, fmtInt } from "../lib/format";
import { href, navigate, paths } from "../lib/router";
import { cn, isModalOpen, isTypingTarget, useHotkeys } from "../lib/util";
import { useConnection } from "../store/connection";
import { isActive, useScanJob } from "../store/jobs";
import { processKey, useProcess, useProcessJob } from "../store/process";
import { useProjects } from "../store/projects";
import { useRecipes } from "../store/recipes";
import { toast } from "../store/toasts";
import { sigmaOf } from "../three/confidence";
import { cvToThree } from "../three/coords";
import { viewBus } from "../three/viewBus";
import { EditsPanel, type EditTab } from "./process/EditsPanel";
import { HistoryPanel } from "./process/HistoryPanel";
import { useVersionedMesh, type LoadedMesh } from "./process/mesh";
import { ProcessCanvas, type CompareMode, type SelectTool } from "./process/ProcessCanvas";
import { RecipesPanel } from "./process/RecipesPanel";
import { StagesPanel } from "./process/StagesPanel";
import { DownloadPanel, StatsPanel } from "./process/StatsPanel";
import { ConfidenceLegend } from "./viewer/ConfidenceLegend";
import type { Shading } from "./viewer/ViewerCanvas";

const TOOLS: { id: SelectTool; label: string; key: string; icon: LucideIcon; hint: string }[] = [
  { id: "navigate", label: "Orbit the view", key: "V", icon: Move3d, hint: "" },
  { id: "box", label: "Box select", key: "B", icon: SquareDashed, hint: "Drag a box" },
  { id: "lasso", label: "Lasso select", key: "L", icon: Lasso, hint: "Draw around the faces" },
  { id: "brush", label: "Brush select", key: "P", icon: Brush, hint: "Paint over the faces" },
  { id: "connected", label: "Select a connected piece", key: "C", icon: MousePointerClick, hint: "Click a piece" },
];

/** Fetch something while `key` is set; results are kept per key (the last few). */
function useKeyedData<T>(key: string | null, load: () => Promise<T>) {
  const cache = useRef(new Map<string, T>());
  const loadRef = useRef(load);
  loadRef.current = load;
  const [, bump] = useState(0);
  const [pending, setPending] = useState<{ key: string; error: unknown } | null>(null);
  useEffect(() => {
    if (!key || cache.current.has(key)) return;
    let cancelled = false;
    setPending({ key, error: null });
    loadRef.current().then(
      (d) => {
        if (cancelled) return;
        cache.current.set(key, d);
        if (cache.current.size > 4) cache.current.delete(cache.current.keys().next().value as string);
        setPending(null);
        bump((n) => n + 1);
      },
      (error: unknown) => {
        if (!cancelled) setPending({ key, error });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [key]);
  const data = key ? (cache.current.get(key) ?? null) : null;
  const err = pending && pending.key === key ? pending.error : null;
  return { data, loading: !!key && !data && !err, error: err };
}

/** What changed between two versions, in words, for the before / after toggle. */
function describeChange(a: ProcessState | undefined, b: ProcessState | undefined): string {
  if (!a || !b) return "the previous version";
  const baseChanged = a.base?.stage !== b.base?.stage || a.base?.triangles !== b.base?.triangles;
  if (baseChanged && b.cursor === 0)
    return `before ${b.base ? { track: "Track", straighten: "Straighten", fuse: "Fuse", refine: "Refine with lasers" }[b.base.stage] : "the stage"} ran again`;
  if (b.cursor === a.cursor + 1 && b.edits[b.cursor - 1] && (a.edits[a.cursor]?.id !== b.edits[b.cursor - 1].id || a.edits.length < b.edits.length))
    return `before “${b.edits[b.cursor - 1].label}”`;
  if (b.cursor < a.cursor) return "before the undo";
  if (b.cursor > a.cursor) return "before the redo";
  return "the previous version";
}

/** Wide enough for a third column (Tailwind's xl, 1280 px)? */
function useWide(): boolean {
  const query = "(min-width: 1280px)";
  const [wide, setWide] = useState(() => matchMedia(query).matches);
  useEffect(() => {
    const m = matchMedia(query);
    const on = () => setWide(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return wide;
}

function Overlay({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("glass pointer-events-auto rounded-xl border border-line-strong shadow-md", className)}>{children}</div>;
}

export default function ProcessPage({ pid, sid }: { pid: string; sid: string }) {
  const project = useProjects((s) => s.details[pid]);
  const projectError = useProjects((s) => s.detailError[pid]);
  const fetchProject = useProjects((s) => s.fetchProject);
  const entry = useProcess((s) => s.entries[processKey(pid, sid)]);
  const state = entry?.state ?? null;
  const job = useProcessJob(pid, sid);
  const makeJob = useScanJob(sid);
  const link = useConnection((s) => s.link);
  const wide = useWide();
  const scan = project?.scans.find((s) => s.id === sid);
  const stem = project && scan ? fileStem(project.name, scan.name) : "scan";

  useEffect(() => {
    void fetchProject(pid);
    void useProcess.getState().fetch(pid, sid);
    if (!useRecipes.getState().list) void useRecipes.getState().fetch();
  }, [pid, sid, fetchProject]);

  // The engine pushes `process` messages; without the live socket, poll while something runs.
  const running = !!job || !!state?.busy || isActive(makeJob);
  useEffect(() => {
    if (link === "open" || !running) return;
    const id = setInterval(() => void useProcess.getState().fetch(pid, sid), 2000);
    return () => clearInterval(id);
  }, [link, running, pid, sid]);

  // ---------------------------------------------------------------- the mesh
  const version = state?.current?.version ?? null;
  const loaded = useVersionedMesh(state?.current?.mesh_url ?? null, version);
  const mesh = state?.current ? loaded.current : null;
  const previous = mesh ? loaded.previous : null;
  const versionStates = useRef(new Map<number, ProcessState>());
  useEffect(() => {
    if (state?.current) versionStates.current.set(state.current.version, state);
  }, [state]);
  const changeLabel = useMemo(
    () => (mesh && previous ? describeChange(versionStates.current.get(previous.version), versionStates.current.get(mesh.version)) : null),
    [mesh, previous],
  );

  // ---------------------------------------------------------------- view state
  const [shading, setShading] = useState<Shading>("clay");
  const [wireframe, setWireframe] = useState(false);
  const [showHoles, setShowHoles] = useState(false);
  const [showTraj, setShowTraj] = useState(false);
  const [compare, setCompare] = useState<CompareMode>("now");
  const [tool, setTool] = useState<SelectTool>("navigate");
  const [through, setThrough] = useState(false);
  const [brush, setBrush] = useState(28);
  const [count, setCount] = useState(0);
  const [tab, setTab] = useState<EditTab>("floaters");
  const [fillMm, setFillMm] = useState(10);
  const overlayRef = useRef<SVGSVGElement>(null);
  const holeLabelRef = useRef<HTMLDivElement>(null);
  const liveCountRef = useRef<HTMLSpanElement>(null);

  const busy = !!job || !!state?.busy || !!entry?.action || isActive(makeJob);
  const comparing = compare === "before" && !!previous;
  const canSelect = !!mesh && !comparing;
  // the confidence map needs each point's sigma (meshes made with the confidence reconstruction)
  const hasSigma = !!sigmaOf(mesh?.geometry);
  const shownShading: Shading = shading === "confidence" && !hasSigma ? "clay" : shading;

  // A new version (an edit, undo or redo) brings a new, empty selection. Say so when that drops
  // faces the user had selected, unless Delete or Keep used them.
  const lastMesh = useRef<LoadedMesh | null>(null);
  const usedSelection = useRef<number | null>(null); // the version whose selection Delete / Keep sent
  useEffect(() => {
    const old = lastMesh.current;
    lastMesh.current = mesh;
    if (mesh && old && old.selection.count > 0 && usedSelection.current !== old.version)
      toast.info("Selection cleared", "The mesh changed, so select again.");
    setCount(mesh?.selection.count ?? 0);
  }, [mesh]);
  const showSelBar = !!mesh && !comparing && (tool !== "navigate" || count > 0);
  useEffect(() => {
    if (liveCountRef.current) liveCountRef.current.textContent = fmtInt(count);
  }, [count, showSelBar]);
  useEffect(() => {
    if (!previous && compare !== "now") setCompare("now");
  }, [previous, compare]);

  const onLive = useCallback((n: number) => {
    if (liveCountRef.current) liveCountRef.current.textContent = fmtInt(n);
  }, []);
  const onCommit = useCallback((n: number) => {
    setCount(n);
    if (liveCountRef.current) liveCountRef.current.textContent = fmtInt(n);
  }, []);

  // ---------------------------------------------------------------- overlays' data
  const holesWanted = (showHoles || tab === "holes") && version !== null && !!mesh;
  const holes = useKeyedData<HoleOutline[]>(holesWanted ? `holes:${version}` : null, () => api.processHoles(pid, sid));
  const st = state?.stages;
  const trajKey = showTraj && st && st.track.state === "done" ? `traj:${st.track.when}|${st.straighten.state}|${st.straighten.when}` : null;
  const traj = useKeyedData<TrajectoryView>(trajKey, () => api.processTrajectory(pid, sid));
  useEffect(() => {
    if (holes.error) {
      toast.error("Couldn't load the holes", holes.error);
      setShowHoles(false);
    }
  }, [holes.error]);
  useEffect(() => {
    if (traj.error) {
      toast.error("Couldn't load the trajectory", traj.error);
      setShowTraj(false);
    }
  }, [traj.error]);

  // Showing the trajectory frames the scanner's path with the mesh, once.
  const framedTraj = useRef<string | null>(null);
  useEffect(() => {
    const t = traj.data;
    if (!t || !mesh || !trajKey || framedTraj.current === trajKey) return;
    framedTraj.current = trajKey;
    const pts = t.raw.filter((p) => p.length >= 3);
    if (!pts.length) return;
    const s = mesh.geometry.boundingSphere!;
    const sphere = new Sphere(cvToThree(s.center), s.radius);
    for (const p of pts) sphere.expandByPoint(cvToThree(new Vector3(p[0], p[1], p[2])));
    viewBus.emit({ type: "frame", animate: true, sphere: { center: [sphere.center.x, sphere.center.y, sphere.center.z], radius: sphere.radius } });
  }, [traj.data, mesh, trajKey]);

  // ---------------------------------------------------------------- selection actions
  const selectionOp = async (op: "delete_faces" | "keep_faces") => {
    if (!mesh || !state?.current || busy) return;
    const sel = mesh.selection;
    if (mesh.version !== state.current.version) {
      toast.warn("The mesh changed", "The selection was made on an older version. Select again.");
      sel.clear();
      setCount(0);
      return;
    }
    if (!sel.count) return;
    if (op === "delete_faces" && sel.count >= sel.faces) {
      toast.warn("That's the whole mesh", "Select less, or use Keep to crop to the selection.");
      return;
    }
    if (op === "keep_faces" && sel.count >= sel.faces) {
      toast.info("Nothing to crop", "Every face is selected.");
      return;
    }
    const { faces, count: n } = sel.encode();
    const label = op === "delete_faces" ? `Delete ${fmtInt(n)} faces` : `Keep ${fmtInt(n)} faces`;
    usedSelection.current = mesh.version;
    if (!(await useProcess.getState().edit(pid, sid, { op, params: { faces } }, label))) usedSelection.current = null;
  };
  const selectAll = () => {
    if (!mesh || !canSelect) return;
    mesh.selection.all();
    onCommit(mesh.selection.count);
  };
  const invert = () => {
    if (!mesh || !canSelect) return;
    mesh.selection.invert();
    onCommit(mesh.selection.count);
  };
  const clearSel = () => {
    if (!mesh) return;
    mesh.selection.clear();
    onCommit(0);
  };

  // ---------------------------------------------------------------- keyboard
  const pickTool = (t: SelectTool) => {
    if (t !== "navigate" && !canSelect) return;
    setTool(t);
  };
  useHotkeys({
    f: () => viewBus.emit({ type: "frame", animate: true }),
    w: () => setWireframe((v) => !v),
    h: () => setShowHoles((v) => !v),
    t: () => setShowTraj((v) => !v),
    k: () => previous && setCompare((c) => (c === "now" ? "before" : c === "before" ? "overlay" : "now")),
    "1": () => setShading("clay"),
    "2": () => setShading("studio"),
    "3": () => setShading("normals"),
    "4": () => hasSigma && setShading("confidence"),
    v: () => pickTool("navigate"),
    b: () => pickTool("box"),
    l: () => pickTool("lasso"),
    p: () => pickTool("brush"),
    c: () => pickTool("connected"),
    "[": () => setBrush((r) => Math.max(4, Math.round(r / 1.25))),
    "]": () => setBrush((r) => Math.min(160, Math.round(r * 1.25))),
    escape: () => setTool("navigate"),
    delete: () => void selectionOp("delete_faces"),
    backspace: () => void selectionOp("delete_faces"),
  });
  const keys = useRef({ undo: () => {}, redo: () => {}, all: () => {}, invert: () => {}, clear: () => {} });
  keys.current = {
    undo: () => !busy && state?.can_undo && void useProcess.getState().undo(pid, sid),
    redo: () => !busy && state?.can_redo && void useProcess.getState().redo(pid, sid),
    all: selectAll,
    invert,
    clear: clearSel,
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.defaultPrevented) return;
      if (isTypingTarget(e.target) || isModalOpen()) return;
      const k = e.key.toLowerCase();
      if (k === "z" && !e.shiftKey) keys.current.undo();
      else if ((k === "z" && e.shiftKey) || k === "y") keys.current.redo();
      else if (k === "a" && e.shiftKey) keys.current.clear();
      else if (k === "a") keys.current.all();
      else if (k === "i") keys.current.invert();
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ---------------------------------------------------------------- render: the middle
  const stats = state?.current?.stats ?? null;
  const load = loaded.load;
  let center: ReactNode = null;
  if (projectError && !project) {
    center = (
      <EmptyState
        tone={projectError.status === 404 ? "neutral" : "danger"}
        icon={CircleExclamationPoint}
        title={projectError.status === 404 ? "This project doesn't exist any more" : "Couldn't load the scan"}
        actions={<Button onClick={() => navigate(paths.home())}>All projects</Button>}
      >
        {projectError.detail}
        {projectError.hint ? <div className="mt-1 text-subtle">{projectError.hint}</div> : null}
      </EmptyState>
    );
  } else if (project && !scan) {
    center = (
      <EmptyState icon={Box} title="This scan isn't in the project" actions={<Button onClick={() => navigate(paths.project(pid))}>Back to the project</Button>}>
        It may have been removed outside Studio.
      </EmptyState>
    );
  } else if (!state && entry?.error) {
    const e = entry.error;
    const old = e.status === 404 || e.status === 405;
    center = (
      <EmptyState
        tone="danger"
        icon={CircleExclamationPoint}
        title={old ? "This engine has no processing studio yet" : "Couldn't load the processing state"}
        actions={
          <>
            <Button variant="primary" icon={RefreshCw} loading={entry.loading} onClick={() => void useProcess.getState().fetch(pid, sid)}>
              Try again
            </Button>
            <Button onClick={() => navigate(paths.viewer(pid, sid))}>Open the viewer</Button>
          </>
        }
      >
        {old ? "The Process page needs OpenHandScan engine 0.3 or newer. “Make mesh” and the viewer still work." : e.detail}
        {!old && e.hint ? <div className="mt-1 text-subtle">{e.hint}</div> : null}
      </EmptyState>
    );
  } else if (!state || !project) {
    center = (
      <div className="flex items-center gap-2 text-sm text-muted">
        <Spinner size={16} /> Loading the scan…
      </div>
    );
  } else if (!state.current) {
    const t = state.stages.track;
    const working = !!job || t.state === "running" || isActive(makeJob);
    const activeJob = job ?? (isActive(makeJob) ? makeJob! : null);
    center = (
      <div className="glass w-[460px] max-w-full rounded-2xl border border-line-strong p-6 shadow-lg">
        <div className="mb-3 grid h-11 w-11 place-items-center rounded-xl border border-line bg-surface-2 text-accent">
          {working ? <Spinner size={20} /> : <Box size={20} aria-hidden />}
        </div>
        <div className="text-base font-semibold text-fg">{working ? "Building the mesh…" : t.state === "failed" ? "Tracking failed" : "No mesh yet"}</div>
        <p className="mt-1 text-sm text-muted">
          {working
            ? "The engine is working through the raw recording. The mesh opens here when it's ready."
            : t.state === "failed"
              ? (t.summary ?? "Try again, or try preview quality. The raw recording is kept.")
              : scan?.status === "recording"
                ? "This scan is still recording. Stop it first."
                : "Make the mesh in one click (the Default recipe), or go step by step: run Track on the left, then edit."}
        </p>
        <div className="mt-4">
          {activeJob ? (
            <JobProgress job={activeJob} label="Processing progress" cancelTitle="Stop. The raw recording is kept." />
          ) : working ? (
            <ProgressBar value={null} label="Processing progress" />
          ) : scan && scan.status !== "recording" ? (
            <MakeMeshButton projectId={pid} scan={scan} variant="primary" size="md" />
          ) : null}
        </div>
      </div>
    );
  } else if (!mesh && load.phase === "error") {
    const err = load.error;
    center = (
      <EmptyState
        tone="danger"
        icon={CircleExclamationPoint}
        title="Couldn't open the mesh"
        actions={
          <Button variant="primary" icon={RefreshCw} onClick={loaded.retry}>
            Try again
          </Button>
        }
      >
        {err instanceof ApiError ? err.detail : `The file couldn't be read: ${err.message}`}
        {err instanceof ApiError && err.hint ? <div className="mt-1 text-subtle">{err.hint}</div> : null}
      </EmptyState>
    );
  } else if (!mesh) {
    const pct = load.phase === "download" && load.total ? load.loaded / load.total : null;
    center = (
      <div className="w-[300px] text-center">
        <div className="mb-3 flex items-center justify-center gap-2 text-sm text-fg">
          <Spinner size={16} className="text-accent" />
          {load.phase === "parse" ? "Preparing the mesh…" : "Loading the mesh…"}
        </div>
        <ProgressBar value={load.phase === "parse" ? null : pct} label="Loading the mesh" />
        <div className="mt-2 font-mono text-xs text-subtle tnum">
          {load.phase === "download" ? `${fmtBytes(load.loaded)}${load.total ? ` of ${fmtBytes(load.total)}` : ""}` : " "}
        </div>
      </div>
    );
  }

  const failure = entry?.failure ?? null;
  const reloading = !!mesh && (load.phase === "download" || load.phase === "parse");
  const toolInfo = TOOLS.find((t) => t.id === tool)!;

  // The mesh's numbers, downloads and recipes: a right column on wide windows, under the history on narrower ones.
  const details = (
    <>
      <StatsPanel stats={stats} loading={(!state && !entry?.error && !projectError) || (!!state?.current && !stats)} />
      <DownloadPanel pid={pid} sid={sid} stem={stem} ready={!!state?.current} />
      {state ? <RecipesPanel pid={pid} sid={sid} state={state} busy={busy} /> : null}
      <div className="space-y-1.5 px-4 py-3 text-[11px] leading-5 text-subtle">
        <div>
          <Waypoints size={12} className="mr-1 inline" aria-hidden />
          Orbit: left drag (right drag while selecting). Pan: right drag (middle while selecting). Zoom: the wheel.
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span>
            <Kbd>B</Kbd> <Kbd>L</Kbd> <Kbd>P</Kbd> <Kbd>C</Kbd> select
          </span>
          <span>
            <Kbd>V</Kbd>/<Kbd>Esc</Kbd> orbit
          </span>
          <span>
            <Kbd>Del</Kbd> delete
          </span>
          <span>
            <Kbd>Ctrl</Kbd>+<Kbd>Z</Kbd> undo
          </span>
          <span>
            <Kbd>Ctrl</Kbd>+<Kbd>Y</Kbd> redo
          </span>
          <span>
            <Kbd>H</Kbd> holes
          </span>
          <span>
            <Kbd>T</Kbd> trajectory
          </span>
          <span>
            <Kbd>K</Kbd> before/after
          </span>
          <span>
            <Kbd>F</Kbd> frame
          </span>
        </div>
      </div>
    </>
  );

  return (
    <div className="flex h-full">
      {/* ------------------------------------------------------------ left: stages, edits, history */}
      <aside className="flex w-[320px] shrink-0 flex-col overflow-y-auto border-r border-line bg-panel 2xl:w-[360px]" aria-label="Processing steps">
        <div className="border-b border-line px-4 py-4">
          <a href={href(paths.project(pid))} className="mb-2 inline-flex items-center gap-1.5 text-xs font-medium text-muted hover:text-fg">
            <ArrowLeft size={13} aria-hidden /> {project?.name ?? "Project"}
          </a>
          {scan ? (
            <>
              <div className="flex items-start gap-2">
                <h1 className="min-w-0 flex-1 truncate text-base font-semibold text-fg" title={scan.name}>
                  {scan.name}
                </h1>
                <ScanStatusBadge scan={scan} job={makeJob} />
              </div>
              <div className="mt-1 text-xs text-subtle">
                {fmtDateTime(scan.created)} · {fmtInt(scan.n_frames)} frames · {fmtDuration(scan.duration_s)}
              </div>
            </>
          ) : (
            <div className="space-y-2">
              <Skeleton className="h-5 w-40" />
              <Skeleton className="h-3 w-56" />
            </div>
          )}
        </div>

        {failure ? (
          <div className="border-b border-line px-4 py-3">
            <Callout
              tone={failure.cancelled ? "neutral" : "danger"}
              icon={failure.cancelled ? X : CircleExclamationPoint}
              title={failure.cancelled ? `${failure.label}: cancelled` : `${failure.label} failed`}
              actions={
                <Button size="xs" variant="ghost" onClick={() => useProcess.getState().dismissFailure(pid, sid)}>
                  Dismiss
                </Button>
              }
            >
              {failure.detail} {failure.cancelled ? "" : state?.current ? "The mesh is as it was." : "The raw recording is kept."}
            </Callout>
          </div>
        ) : null}

        {state ? (
          <>
            <StagesPanel pid={pid} sid={sid} state={state} busy={busy} />
            <EditsPanel
              pid={pid}
              sid={sid}
              stats={stats}
              busy={busy}
              hasMesh={!!state.current}
              tab={tab}
              setTab={setTab}
              fillMm={fillMm}
              setFillMm={setFillMm}
              holes={holes.data}
            />
            <HistoryPanel pid={pid} sid={sid} state={state} busy={busy} />
            {!wide ? details : null}
          </>
        ) : entry?.error || (projectError && !project) || (project && !scan) ? null : (
          <div className="space-y-3 px-4 py-4" aria-busy="true">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-[104px]" />
            ))}
          </div>
        )}
      </aside>

      {/* ------------------------------------------------------------ middle: the 3D view */}
      <div className="relative min-w-0 flex-1 overflow-hidden viewport-bg">
        {mesh ? (
          <ErrorBoundary title="The 3D view couldn't start">
            <ProcessCanvas
              mesh={mesh}
              previous={previous}
              compare={previous ? compare : "now"}
              shading={shownShading}
              wireframe={wireframe}
              holes={holesWanted ? holes.data : null}
              fillUpTo={tab === "holes" ? fillMm : null}
              trajectory={showTraj ? traj.data : null}
              tool={tool}
              through={through}
              brushRadius={brush}
              selectEnabled={canSelect}
              overlay={overlayRef}
              holeLabel={holeLabelRef}
              onLive={onLive}
              onCommit={onCommit}
              frameKey={1}
            />
          </ErrorBoundary>
        ) : (
          <div className="absolute inset-0 grid place-items-center p-6">{center}</div>
        )}

        {/* gesture drawing (box, lasso, brush), updated without React */}
        <svg ref={overlayRef} className="pointer-events-none absolute inset-0 h-full w-full overflow-visible" aria-hidden>
          <rect data-box visibility="hidden" className="fill-[var(--c-accent-soft)] stroke-[var(--c-accent)] [stroke-dasharray:5_4] [stroke-width:1.5] data-[mode=subtract]:fill-[var(--c-danger-soft)] data-[mode=subtract]:stroke-[var(--c-danger)]" />
          <path data-lasso visibility="hidden" className="fill-[var(--c-accent-soft)] stroke-[var(--c-accent)] [stroke-dasharray:5_4] [stroke-width:1.5] [stroke-linejoin:round] data-[mode=subtract]:fill-[var(--c-danger-soft)] data-[mode=subtract]:stroke-[var(--c-danger)]" />
          <circle data-brush r={brush} visibility="hidden" className="fill-[var(--c-accent-soft)] stroke-[var(--c-accent)] [stroke-width:1.5] data-[mode=subtract]:fill-[var(--c-danger-soft)] data-[mode=subtract]:stroke-[var(--c-danger)]" />
        </svg>
        <div
          ref={holeLabelRef}
          className="pointer-events-none absolute left-0 top-0 z-10 hidden rounded-md border border-white/15 bg-[rgb(17_20_25/0.9)] px-2 py-1 font-mono text-[11.5px] text-white shadow-lg tnum"
          role="status"
        />

        {/* top left: where we are, and the view options */}
        <div className="pointer-events-none absolute left-4 top-4 flex flex-col items-start gap-2">
          <Overlay className="flex h-9 items-center gap-1.5 pl-1.5 pr-3 text-sm">
            <IconButton icon={ArrowLeft} size="sm" label="Back to the project" onClick={() => navigate(paths.project(pid))} />
            <a href={href(paths.project(pid))} className="max-w-[180px] truncate text-muted hover:text-fg">
              {project?.name ?? "Project"}
            </a>
            <span className="text-subtle" aria-hidden>
              /
            </span>
            {scan?.status === "processed" ? (
              <a href={href(paths.viewer(pid, sid))} className="max-w-[200px] truncate text-muted hover:text-fg" title="Open in the viewer">
                {scan.name}
              </a>
            ) : (
              <span className="max-w-[200px] truncate text-muted">{scan?.name ?? "Scan"}</span>
            )}
            <span className="text-subtle" aria-hidden>
              /
            </span>
            <span className="font-medium text-fg">Process</span>
          </Overlay>
          {mesh ? (
            <Overlay className="flex flex-wrap items-center gap-1 p-1">
              <div role="toolbar" aria-label="View options" className="flex flex-wrap items-center gap-1">
                <Segmented<Shading>
                  label="Shading"
                  size="sm"
                  value={shownShading}
                  onChange={setShading}
                  options={[
                    { value: "clay", label: "Clay", icon: Brush, title: "Matcap clay (1)" },
                    { value: "studio", label: "Studio", icon: Sparkles, title: "Studio lighting (2)" },
                    { value: "normals", label: "Normals", icon: Orbit, title: "Normal shading (3)" },
                    {
                      value: "confidence",
                      label: "Confidence",
                      icon: Target,
                      disabled: !hasSigma,
                      title: hasSigma
                        ? "How well each point was measured (4)"
                        : "This mesh has no confidence data: it was made without a GPU or by an earlier version. Run Track again to get it.",
                    },
                  ]}
                />
                <span className="mx-1 h-5 w-px bg-line-strong" aria-hidden />
                <IconButton icon={Grid2x2} size="sm" label="Wireframe (W)" active={wireframe} onClick={() => setWireframe((v) => !v)} />
                <IconButton
                  icon={CircleDashed}
                  size="sm"
                  label={stats?.holes ? `Hole outlines: ${stats.holes} (H)` : "Hole outlines (H)"}
                  active={showHoles || tab === "holes"}
                  loading={holes.loading}
                  onClick={() => setShowHoles((v) => !v)}
                />
                <IconButton
                  icon={Route}
                  size="sm"
                  label={st?.track.state === "done" ? "Scanner trajectory (T)" : "The trajectory needs a finished Track"}
                  active={showTraj}
                  loading={traj.loading}
                  disabled={st?.track.state !== "done"}
                  onClick={() => setShowTraj((v) => !v)}
                />
                <IconButton icon={Focus} size="sm" label="Frame the model (F)" onClick={() => viewBus.emit({ type: "frame", animate: true })} />
              </div>
              {previous ? (
                <>
                  <span className="mx-1 h-5 w-px bg-line-strong" aria-hidden />
                  <Segmented<CompareMode>
                    label={`Compare with ${changeLabel ?? "the previous version"}`}
                    size="sm"
                    value={compare}
                    onChange={setCompare}
                    options={[
                      { value: "now", label: "Now", icon: Eye, title: "The current mesh (K cycles)" },
                      { value: "before", label: "Before", icon: Contrast, title: `The mesh ${changeLabel ?? "before the last change"}` },
                      { value: "overlay", label: "Both", icon: Blend, title: "The previous mesh as an orange ghost: removed parts show" },
                    ]}
                  />
                </>
              ) : null}
            </Overlay>
          ) : null}
          {/* the trajectory's legend, under the toggle (the bottom belongs to the selection bar) */}
          {showTraj && traj.data && mesh ? (
            <Overlay className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5 text-[11px] text-muted">
              <span className="flex items-center gap-1.5">
                <span className="h-0.5 w-5 rounded bg-[#e5a83a]" aria-hidden /> As tracked{traj.data.straightened ? " (dashed)" : ""}
              </span>
              {traj.data.straightened ? (
                <span className="flex items-center gap-1.5">
                  <span className="h-0.5 w-5 rounded bg-accent" aria-hidden /> Straightened
                </span>
              ) : null}
              {traj.data.loops.length ? (
                <span className="flex items-center gap-1.5">
                  <span className="h-0.5 w-5 rounded border-t border-dashed border-[#b77cff]" aria-hidden />
                  {`${traj.data.loops.length} loop closure${traj.data.loops.length === 1 ? "" : "s"}`}
                </span>
              ) : (
                <span>No loop closures</span>
              )}
              <span className="flex items-center gap-1">
                <span className="h-2 w-2 rounded-full bg-[#3fb950]" aria-hidden /> start
              </span>
              <span className="flex items-center gap-1">
                <span className="h-2 w-2 rounded-full bg-[#f2564b]" aria-hidden /> end
              </span>
            </Overlay>
          ) : null}
        </div>

        {/* left edge: selection tools */}
        {mesh ? (
          <div className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2">
            <Overlay className="flex flex-col items-center gap-1 p-1">
              <div role="toolbar" aria-label="Selection tools" aria-orientation="vertical" className="flex flex-col gap-1">
                {TOOLS.map((t) => (
                  <IconButton
                    key={t.id}
                    icon={t.icon}
                    size="md"
                    label={`${t.label} (${t.key})`}
                    active={tool === t.id}
                    disabled={t.id !== "navigate" && !canSelect}
                    onClick={() => pickTool(t.id)}
                  />
                ))}
              </div>
              <span className="my-0.5 h-px w-6 bg-line-strong" aria-hidden />
              <IconButton
                icon={through ? EyeOff : Eye}
                size="md"
                label={through ? "Selecting through the model, hidden faces too: click for visible faces only" : "Selecting visible faces only: click to select through the model"}
                active={through}
                onClick={() => setThrough((v) => !v)}
              />
            </Overlay>
          </div>
        ) : null}

        {/* top right: the running job, a new version loading, or what "Before" shows */}
        <div className="pointer-events-none absolute right-4 top-4 flex w-[min(380px,calc(100%-2rem))] flex-col items-end gap-2">
          {mesh && job ? (
            <Overlay className="w-full px-3.5 py-2.5">
              <div className="mb-1 truncate text-xs font-medium text-fg">{state?.busy ?? "Working…"}</div>
              <JobProgress job={job} label="Job progress" cancelTitle="Stop. The mesh stays as it was." />
            </Overlay>
          ) : mesh && state?.busy ? (
            <Overlay className="flex items-center gap-2 px-3 py-2 text-xs text-fg">
              <Spinner size={13} className="text-accent" /> {state.busy}
            </Overlay>
          ) : null}
          {reloading ? (
            <Overlay className="flex items-center gap-2 px-3 py-1.5 text-xs text-muted">
              <Spinner size={12} className="text-accent" />
              {load.phase === "parse" ? "Preparing the new mesh…" : `Loading the new mesh${load.phase === "download" && load.total ? ` · ${Math.round((load.loaded / load.total) * 100)}%` : "…"}`}
            </Overlay>
          ) : mesh && load.phase === "error" ? (
            <Overlay className="flex items-center gap-2 px-3 py-1.5 text-xs">
              <CircleExclamationPoint size={13} className="text-danger" aria-hidden />
              <span className="text-fg">Couldn't load the new version: {load.error instanceof ApiError ? load.error.detail : load.error.message}</span>
              <Button size="xs" variant="ghost" icon={RefreshCw} onClick={loaded.retry}>
                Retry
              </Button>
            </Overlay>
          ) : comparing ? (
            <Overlay className="px-3 py-1.5 text-xs text-fg">
              Showing the mesh <b className="font-semibold">{changeLabel ?? "before the last change"}</b>. Press <Kbd>K</Kbd> or choose “Now” to go back.
            </Overlay>
          ) : null}
        </div>

        {/* bottom left: the confidence map's legend, above the selection bar when that shows */}
        {shownShading === "confidence" && mesh ? (
          <div className={cn("pointer-events-none absolute left-4 transition-[bottom] duration-200", showSelBar ? "bottom-28" : "bottom-4")}>
            <Overlay className="px-3 py-2.5">
              <ConfidenceLegend geometry={(comparing ? previous : mesh)?.geometry ?? null} />
            </Overlay>
          </div>
        ) : null}

        {/* bottom centre: the selection */}
        {showSelBar ? (
          <div className="pointer-events-none absolute bottom-5 left-1/2 flex -translate-x-1/2 flex-col items-center gap-2">
            {tool !== "navigate" ? (
              <div className="glass pointer-events-auto flex items-center gap-2 rounded-full border border-accent-line px-3.5 py-1.5 text-xs text-fg shadow-md">
                <toolInfo.icon size={13} className="text-accent" aria-hidden />
                <span>{toolInfo.hint}</span>
                <span className="text-subtle">·</span>
                <span className="text-muted">
                  <Kbd>Shift</Kbd> adds, <Kbd>Ctrl</Kbd> {tool === "brush" ? "erases" : "removes"}
                </span>
                <span className="text-subtle">·</span>
                <span className="text-muted">right-drag orbits</span>
                {tool === "brush" ? (
                  <>
                    <span className="text-subtle">·</span>
                    <span className="w-24">
                      <Slider label="Brush size" value={brush} onChange={setBrush} min={4} max={160} step={1} />
                    </span>
                    <span className="text-muted">
                      <Kbd>[</Kbd> <Kbd>]</Kbd>
                    </span>
                  </>
                ) : null}
              </div>
            ) : null}
            <div className="glass pointer-events-auto flex items-center gap-1.5 rounded-2xl border border-line-strong p-1.5 pl-3.5 shadow-lg" role="region" aria-label="Selection">
              <span className="mr-1 whitespace-nowrap text-sm text-fg" aria-live="polite">
                {/* written directly (also while a brush paints), never by React */}
                <span ref={liveCountRef} className="font-mono font-semibold tnum" />{" "}
                <span className="text-muted">face{count === 1 ? "" : "s"} selected</span>
              </span>
              <Button size="sm" variant="ghost" onClick={invert} title="Invert the selection (Ctrl+I)" disabled={!canSelect}>
                Invert
              </Button>
              <Button size="sm" variant="ghost" onClick={selectAll} title="Select everything (Ctrl+A)" disabled={!canSelect}>
                All
              </Button>
              <Button size="sm" variant="ghost" onClick={clearSel} title="Clear the selection (Ctrl+Shift+A)" disabled={!count}>
                Clear
              </Button>
              <span className="mx-0.5 h-5 w-px bg-line-strong" aria-hidden />
              <Button size="sm" variant="danger" icon={Trash} disabled={!count || busy} onClick={() => void selectionOp("delete_faces")} title="Delete the selected faces (Delete)">
                Delete
              </Button>
              <Button size="sm" variant="primary" icon={Crop} disabled={!count || busy} onClick={() => void selectionOp("keep_faces")} title="Keep only the selected faces: crop to them">
                Keep only these
              </Button>
            </div>
          </div>
        ) : null}
      </div>

      {/* ------------------------------------------------------------ right: numbers, downloads, recipes */}
      {wide ? (
        <aside className="flex w-[300px] shrink-0 flex-col overflow-y-auto border-l border-line bg-panel 2xl:w-[340px]" aria-label="Mesh details">
          {details}
        </aside>
      ) : null}
    </div>
  );
}
