/**
 * Scan result viewer: loads `mesh.ply` from the engine (the fast PLY reader), studio shading,
 * wireframe, normals, the confidence map (each point's sigma, when the mesh has them) and
 * laser-point overlays, two-click distance measurement, fitted shapes (plane, cylinder, hole: one
 * click, the engine fits it, with its ± and the relation of two fits; fits, measurements and relations
 * are kept with the scan, with names, through store/features.ts), the part's coordinate system built from
 * three kept fits (the Coordinate system panel: its axes drawn in the view, the panels' numbers in it on
 * request, the mesh downloaded in it), the Check panel (which areas miss the tolerance the part needs),
 * bounding box in mm, downloads (the kept fits for CAD too: a STEP file and a FreeCAD macro,
 * viewer/CadDownload.tsx), the Section tool (S: the scan cut by a plane, its 2D profile with a distance
 * on it, kept sections, DXF and SVG; viewer/SectionPanel.tsx, SectionProfile.tsx, SectionMarks.tsx,
 * useSectionTool.ts), the comparison with a reference model (the Compare panel: a reference of the project, its
 * units, the alignment, the "Against the reference" shading (7) with a tolerance and the areas outside it, the
 * reference as a ghost, matching points picked side by side; viewer/ComparePanel.tsx, useComparison.ts,
 * PointPairPicker.tsx), the Flush tool (U: two clicks across a seam, the flush along it with a chart, kept seams;
 * viewer/FlushPanel.tsx, FlushChart.tsx, FlushMarks.tsx, useFlushTool.ts), the scan's report (the Download panel's
 * "Report" group: the HTML page with views of the model added from the 3D view, the kept features as CSV and JSON;
 * viewer/ReportGroup.tsx, ViewCapture.tsx) and the processing report.
 */
import {
  ArrowLeft,
  Box,
  ChevronDown,
  CircleExclamationPoint,
  Download,
  Eye,
  FileText,
  Focus,
  Grid2x2,
  Info,
  Layers,
  RefreshCw,
  Ruler,
  Sparkles,
  Trash,
  X,
  Brush,
  Diff,
  Orbit,
  ScanLine,
  Shapes,
  SlidersHorizontal,
  Target,
  TriangleExclamationPoint,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { BufferGeometry } from "three";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { ApiError, api } from "../api/http";
import type { FitKind, KeptFeature, KeptFit, MadeInfo, Project, Scan } from "../api/types";
import { isWholeRun, processingTime } from "../lib/making";
import { HEALTH_ICON, HEALTH_TEXT } from "../components/CalibHealth";
import { JobProgress, MakeMeshButton, ProcessButton, ScanStatusBadge } from "../components/ScanBits";
import { ErrorBoundary } from "../components/shell/ErrorBoundary";
import { Button, IconButton } from "../components/ui/Button";
import { EmptyState, Kbd, ProgressBar, Skeleton, Spinner } from "../components/ui/Feedback";
import { Segmented } from "../components/ui/Form";
import { KeyValue } from "../components/ui/Layout";
import { calibrationSays, scanHealthDetail, scanHealthOf } from "../lib/calibHealth";
import { PATH_WARNING_TITLE, notesTitle, splitWarnings } from "../lib/pathWarning";
import { hasGraphicsCard, noConfidenceText } from "../lib/computer";
import { useConnection } from "../store/connection";
import { downloadFile } from "../lib/download";
import { decodeFaces, fitLabel, fitName, regionTriangles, summarizeRegion, type RegionSummary } from "../lib/fits";
import { fileStem, fmtBytes, fmtCompact, fmtDateTime, fmtDetailMm, fmtDuration, fmtFixed, fmtInt, fmtPct } from "../lib/format";
import { viewerSheet } from "../lib/shortcuts";
import { axesEntry, groupOfTool, initialOpen, openFor, type GroupsOpen, type ViewerGroupId, type ViewerTool } from "../lib/viewerGroups";
import { ShortcutButton, ShortcutSheet } from "../components/ShortcutSheet";
import { remakeBlock } from "../lib/keepMode";
import { fineWallsNote, thinPartsToShow } from "../lib/thinParts";
import { FineWallsNote, ThinPartsHint } from "./viewer/ThinPartsHint";
import { hasLaserValues } from "../lib/lasers";
import { axisOrder, deltaOf, fmtVec, pointToPart, readFrame } from "../lib/partFrame";
import { href, navigate, paths } from "../lib/router";
import { describeReason } from "../lib/tracking";
import { cn, useHotkeys } from "../lib/util";
import { featuresKey, useFeatures } from "../store/features";
import { isActive, useScanJob } from "../store/jobs";
import { processKey, useProcess } from "../store/process";
import { useProjects } from "../store/projects";
import { toast } from "../store/toasts";
import { useUi } from "../store/ui";
import { sigmaOf } from "../three/confidence";
import { disposeMeshGeometry, plyGeometry, readPly } from "../three/ply";
import { viewBus } from "../three/viewBus";
import { CadDownload } from "./viewer/CadDownload";
import { MeshDownloadButtons } from "../components/MeshDownloads";
import { PRINT_CLOSED_LINE, meshAxesLine } from "../lib/downloads";
import { CHECK_ICON, CheckPanel } from "./viewer/CheckPanel";
import { usePass } from "../store/pass";
import { ConfidenceLegend } from "./viewer/ConfidenceLegend";
import { DeviationLegend } from "./viewer/DeviationLegend";
import { LasersLegend } from "./viewer/LasersLegend";
import { useCheck } from "./viewer/useCheck";
import { useDeviation } from "./viewer/useDeviation";
import { useLasers } from "./viewer/useLasers";
import { FIT_ICON, FitList } from "./viewer/FitsPanel";
import type { NominalActions } from "./viewer/NominalSlot";
import { useIgnoredOffers, useNominalOffers } from "./viewer/useNominals";
import { FRAME_ICON, FramePanel } from "./viewer/FramePanel";
import type { FitItem } from "./viewer/FitTool";
import { usePairRelation } from "./viewer/usePairRelation";
import { NameEdit } from "./viewer/NameEdit";
import { SECTION_ICON, SectionPanel } from "./viewer/SectionPanel";
import { SectionProfile } from "./viewer/SectionProfile";
import { useSectionTool, type Bounds } from "./viewer/useSectionTool";
import { fmtSigma, ViewerCanvas, type Measurement, type Shading, type Vec3 } from "./viewer/ViewerCanvas";
import { useSections, sectionsKey } from "../store/sections";
import type { PlaneFit } from "../lib/section";
import { COMPARE_ICON, ComparePanel } from "./viewer/ComparePanel";
import { PointPairPicker } from "./viewer/PointPairPicker";
import { ReferenceLegend } from "./viewer/ReferenceLegend";
import { useComparison } from "./viewer/useComparison";
import { FLUSH_ICON, FlushPanel } from "./viewer/FlushPanel";
import { useFlushTool } from "./viewer/useFlushTool";
import { ReportGroup } from "./viewer/ReportGroup";
import type { CaptureMark } from "./viewer/ViewCapture";
import { seamsKey, useSeams } from "../store/seams";
import type { ReportView } from "../lib/report";

type Load =
  | { phase: "idle" }
  | { phase: "download"; loaded: number; total: number | null }
  | { phase: "parse" }
  | { phase: "ready"; geometry: BufferGeometry; bytes: number }
  | { phase: "error"; error: ApiError | Error };

function urlsFor(project: Project, scan: Scan) {
  const r = scan.result;
  return {
    mesh: r?.mesh_url || api.fileUrl(project.id, scan.id, "mesh.ply"),
    stl: r?.stl_url || api.fileUrl(project.id, scan.id, "mesh.stl"),
    laser: r?.laser_points_url ?? null,
    report: api.fileUrl(project.id, scan.id, "report.json"),
  };
}

/** The laser point cloud (points, maybe coloured). */
function parsePly(buf: ArrayBuffer): BufferGeometry {
  const g = new PLYLoader().parse(buf);
  if (g.index && !g.getAttribute("normal")) g.computeVertexNormals();
  g.computeBoundingBox();
  g.computeBoundingSphere();
  return g;
}

// ------------------------------------------------------------------ report helpers
type Rec = Record<string, unknown>;
const obj = (v: unknown): Rec | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function ReportSummary({ report, merged, made }: { report: Rec; merged: boolean; made?: MadeInfo | null }) {
  // UX_PLAN R17: the time the whole Make mesh took (`ProcessState.made`), else the last stage's, named as such
  const took = processingTime(made, num(report.wall_time_s));
  const frames = obj(report.frames);
  const mesh = obj(report.mesh);
  const lasers = obj(report.lasers);
  const gt = obj(report.ground_truth);
  const failures = obj(report.failures);
  const gtTrans = obj(gt?.trans_mm);
  const gtRot = obj(gt?.rot_deg);
  const sigma = obj(mesh?.sigma_mm);
  const failureRows = failures ? Object.entries(failures).filter(([, v]) => num(v)) : [];
  // H4: what this scan saw of its calibration (its own health_status); nothing for a scan processed before H3
  const health = scanHealthDetail(report);
  const HealthIcon = health ? HEALTH_ICON[scanHealthOf(report)!.state] : null;
  // ENGINE_PLAN X0a: the camera path's warning (nothing for a scan processed before the check, or when it held)
  // UX_PLAN R1: only the path's own sentence goes under that title; the report's other notes (a merge's: what it left
  // out) under their own
  const { path: pathWarnings, other: notes } = splitWarnings(report);
  return (
    <div className="space-y-4">
      {pathWarnings.length ? (
        <div className="flex gap-2.5 rounded-lg border border-warn/30 bg-warn-soft px-3 py-2.5 text-xs" role="note" aria-label="Camera path: tracking may have slid">
          <TriangleExclamationPoint size={14} className="mt-px shrink-0 text-warn" aria-hidden />
          <div className="min-w-0 flex-1 space-y-0.5">
            <div className="font-semibold text-fg">{PATH_WARNING_TITLE}</div>
            {pathWarnings.map((w) => (
              <div key={w} className="text-muted">
                {w}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {notes.length ? (
        <div className="flex gap-2.5 rounded-lg border border-line bg-surface-2/50 px-3 py-2.5 text-xs" role="note" aria-label={notesTitle(merged)}>
          <Info size={14} className="mt-px shrink-0 text-muted" aria-hidden />
          <div className="min-w-0 flex-1 space-y-0.5">
            <div className="font-semibold text-fg">{notesTitle(merged)}</div>
            {notes.map((w) => (
              <div key={w} className="text-muted">
                {w}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {health && HealthIcon ? (
        <div
          className={cn(
            "flex gap-2.5 rounded-lg border px-3 py-2.5 text-xs",
            health.tone === "danger" ? "border-danger/30 bg-danger-soft" : health.tone === "warn" ? "border-warn/30 bg-warn-soft" : "border-line bg-surface-2/50",
          )}
          aria-label={calibrationSays(health.headline)}
        >
          <HealthIcon size={14} className={cn("mt-px shrink-0", HEALTH_TEXT[health.tone])} aria-hidden />
          <div className="min-w-0 flex-1 space-y-0.5">
            <div className="font-semibold text-fg">{health.tone === "neutral" ? calibrationSays(health.headline) : health.headline}</div>
            {health.reason ? <div className="text-muted">{health.reason}</div> : null}
            {health.lines.map((l) => (
              <div key={l} className="text-subtle">
                {l}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      <KeyValue
        dense
        rows={[
          frames
            ? [
                "Frames",
                <span className="tnum">
                  {fmtInt(num(frames.tracked))} of {fmtInt(num(frames.processed))} tracked
                  {num(frames.tracked_fraction) !== null ? <span className="text-muted"> ({fmtPct(num(frames.tracked_fraction))})</span> : null}
                </span>,
              ]
            : null,
          typeof report.mode === "string" ? ["Mode", report.mode === "markers" ? "Markers" : "Markerless"] : null,
          // U8: "Mesh detail 1.0 mm", not "TSDF · 1.00 mm voxels"
          mesh && num(mesh.voxel_mm) !== null ? ["Mesh detail", fmtDetailMm(num(mesh.voxel_mm))] : null,
          sigma && num(sigma.median) !== null
            ? [
                "Confidence",
                <span className="tnum" title="Each point's ±, as fused (before any edits)">
                  median {fmtSigma(num(sigma.median)!)}
                  {num(sigma.within_0_1) !== null ? <span className="text-muted"> · {fmtPct(num(sigma.within_0_1))} within 0.1&#8239;mm</span> : null}
                </span>,
              ]
            : null,
          lasers && num(lasers.n_points_exported) !== null ? ["Laser points", <span className="tnum">{fmtInt(num(lasers.n_points_exported))}</span>] : null,
          took ? [took.label, <span title={took.title}>{took.value}</span>] : null,
          typeof report.source === "string" ? ["Source", report.source === "sim" ? "Simulator" : report.source] : null,
        ]}
      />
      {gt && num(gt.n) ? (
        <div className="rounded-lg border border-ok/25 bg-ok-soft/50 px-3 py-2.5">
          <div className="mb-1.5 text-xs font-semibold text-ok">Accuracy vs ground truth (simulator)</div>
          <KeyValue
            dense
            rows={[
              num(gt.rms_trans_mm) !== null ? ["Position, typical", <span className="font-mono text-xs tnum">{fmtFixed(num(gt.rms_trans_mm), 2)}&#8239;mm</span>] : null,
              gtTrans && num(gtTrans.max) !== null ? ["Position max", <span className="font-mono text-xs tnum">{fmtFixed(num(gtTrans.max), 2)}&#8239;mm</span>] : null,
              gtRot && num(gtRot.max) !== null ? ["Rotation max", <span className="font-mono text-xs tnum">{fmtFixed(num(gtRot.max), 2)}°</span>] : null,
            ]}
          />
        </div>
      ) : null}
      {failureRows.length ? (
        <div>
          <div className="mb-1.5 text-xs font-semibold text-subtle">Frames not tracked</div>
          <ul className="space-y-1">
            {failureRows.map(([k, v]) => (
              <li key={k} className="flex items-center justify-between gap-2 text-xs">
                <span className="text-muted">{describeReason(k)?.text ?? k}</span>
                <span className="font-mono text-fg tnum">{fmtInt(num(v))}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------------ panel bits
/** One of the side panel's four groups (U11): a heading that folds it, the count of what is saved inside when folded. */
function Group({
  id,
  title,
  icon: Icon,
  open,
  onToggle,
  summary,
  children,
}: {
  id: ViewerGroupId;
  title: string;
  icon: typeof Box;
  open: boolean;
  onToggle: (id: ViewerGroupId) => void;
  summary?: string | null;
  children: ReactNode;
}) {
  const bodyId = `viewer-group-${id}`;
  return (
    <section className="border-b border-line" aria-label={title}>
      <h2>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => onToggle(id)}
          className="flex w-full items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-surface-2/50 focus-visible:outline-offset-[-2px]"
        >
          <Icon size={15} className="shrink-0 text-muted" aria-hidden />
          <span className="flex-1 text-sm font-semibold text-fg">{title}</span>
          {!open && summary ? <span className="truncate text-[11px] text-subtle">{summary}</span> : null}
          <ChevronDown size={15} className={cn("shrink-0 text-subtle transition-transform", open ? "rotate-180" : "")} aria-hidden />
        </button>
      </h2>
      <div id={bodyId} hidden={!open}>
        {children}
      </div>
    </section>
  );
}

/** "2 measurements · 1 fitted shape" for a folded Measure group; null when nothing is saved. */
function savedSummary(measurements: number, shapes: number, sections: number, seams: number): string | null {
  const parts = [
    [measurements, "measurement"],
    [shapes, "fitted shape"],
    [sections, "section"],
    [seams, "seam"],
  ]
    .filter(([n]) => (n as number) > 0)
    .map(([n, w]) => `${n} ${w}${n === 1 ? "" : "s"}`);
  return parts.length ? parts.join(" · ") : null;
}

function Panel({ title, icon: Icon, children, aside }: { title: string; icon: typeof Box; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="border-t border-line/70 px-4 py-4" aria-label={title}>
      <div className="mb-3 flex items-center gap-2">
        <Icon size={14} className="text-muted" aria-hidden />
        <h3 className="flex-1 text-xs font-semibold uppercase tracking-wider text-muted">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

/** A kept fit's region on one mesh on screen (its geometry's uuid): the triangles, and where to draw the label. */
type Region = { meshId: string; version: number; tris: Uint32Array; region: RegionSummary };

/** A fit's region (base64 face indices of mesh.ply) on the mesh on screen; throws when it doesn't match that mesh. */
function regionOn(g: BufferGeometry, faces: string, f: Pick<KeptFeature, "shape" | "seed" | "mesh_version">): Region {
  const tris = regionTriangles(decodeFaces(faces), g.index!.array, (g.userData.faceOfTri as Uint32Array | null | undefined) ?? null, g.userData.faceCount as number | undefined);
  return { meshId: g.uuid, version: f.mesh_version, tris, region: summarizeRegion(tris, g.getAttribute("position").array, f.shape, f.seed.point) };
}

const FIT_HINT: Record<FitKind, string> = {
  plane: "Click a flat face to fit a plane",
  cylinder: "Click a round wall (a shaft, a boss or a bore) to fit a cylinder",
  hole: "Click next to a hole's edge to fit its circle",
};

export default function ViewerPage({ pid, sid }: { pid: string; sid: string }) {
  const project = useProjects((s) => s.details[pid]);
  const projectError = useProjects((s) => s.detailError[pid]);
  const fetchProject = useProjects((s) => s.fetchProject);
  const job = useScanJob(sid);
  const scan = project?.scans.find((s) => s.id === sid);
  const urls = project && scan ? urlsFor(project, scan) : null;
  // UX_PLAN R15: while "Make mesh again" runs the scan is "processing" but keeps its current mesh (the run is built
  // apart and takes its place when done): that mesh's downloads, report and tools stay, with the job's progress shown
  // (the engine since R2 may leave the scan's entry, its status too, as it was until the run takes its place: then the
  // scan's own whole-run job says it)
  const wholeRun = !!job && isActive(job) && isWholeRun(job);
  const remaking = !!scan?.result?.mesh_url && (scan.status === "processing" || wholeRun);
  const hasResult = !!scan && !!scan.result && (scan.status === "processed" || remaking);

  const [load, setLoad] = useState<Load>({ phase: "idle" });
  const [reloadKey, setReloadKey] = useState(0);
  const [frameKey, setFrameKey] = useState(0);
  const [laser, setLaser] = useState<BufferGeometry | null>(null);
  const [laserLoading, setLaserLoading] = useState(false);
  const [report, setReport] = useState<Rec | null>(null);
  const [shading, setShading] = useState<Shading>("clay");
  const [wireframe, setWireframe] = useState(false);
  const [showLaser, setShowLaser] = useState(false);
  const [measure, setMeasure] = useState(false);
  const [pending, setPending] = useState<Vec3 | null>(null);
  const [fitOn, setFitOn] = useState(false);
  const [fitKind, setFitKind] = useState<FitKind>("plane");
  /** The Section tool is on (FEATURE_PLAN I1). */
  const [sectionOn, setSectionOn] = useState(false);
  /** Matching points are being picked on the scan and the reference (FEATURE_PLAN I2). */
  const [pickPairs, setPickPairs] = useState(false);
  /** The Flush tool is on (FEATURE_PLAN I4). */
  const [flushOn, setFlushOn] = useState(false);
  /** The views of the model added to the report (this page's memory only; FEATURE_PLAN I3). */
  const [reportViews, setReportViews] = useState<ReportView[]>([]);
  /** The fits, measurements and relations kept with the scan (those made on an earlier mesh stay, marked so). */
  const kept = useFeatures((s) => s.entries[featuresKey(pid, sid)]?.set ?? null);
  const refitting = useFeatures((s) => s.entries[featuresKey(pid, sid)]?.refitting ?? false);
  const framing = useFeatures((s) => s.entries[featuresKey(pid, sid)]?.framing ?? false);
  /** Show the panels' positions and directions in the part's frame (when it has one). */
  const [inPart, setInPart] = useState(false);
  /** The kept fits' regions on the mesh on screen, by feature id (read once per mesh). */
  const [regions, setRegions] = useState<Record<string, Region>>({});
  const [fitBusy, setFitBusy] = useState<{ p: Vec3; kind: FitKind } | null>(null);
  /** Fits picked for a relation (their ids; two at most, the latest kept). */
  const [pickedFits, setPickedFits] = useState<string[]>([]);
  const geomRef = useRef<BufferGeometry | null>(null);
  const fitRunning = useRef(false);

  useEffect(() => {
    void fetchProject(pid);
  }, [pid, fetchProject]);

  // Refresh while the engine is meshing this scan.
  useEffect(() => {
    if (!isActive(job) && scan?.status !== "processing") return;
    const id = setInterval(() => void fetchProject(pid), 2500);
    return () => clearInterval(id);
  }, [job, scan?.status, pid, fetchProject]);

  const meshUrl = hasResult ? urls!.mesh : null;

  // A job that finishes while a mesh is shown (Make mesh shows the tracked mesh before its later
  // stages): the finished mesh replaces it in place, the old one stays until it's read, the camera
  // stays where it is.
  const quiet = useRef(false);
  const wasActive = useRef(isActive(job));
  useEffect(() => {
    const active = isActive(job);
    if (wasActive.current && !active && job?.state === "done" && geomRef.current) {
      quiet.current = true;
      setReloadKey((k) => k + 1);
    }
    wasActive.current = active;
  }, [job]);

  // Download + parse the mesh.
  useEffect(() => {
    if (!meshUrl) return;
    const ctrl = new AbortController();
    let last = 0;
    const inPlace = quiet.current && !!geomRef.current;
    quiet.current = false;
    if (!inPlace) setLoad({ phase: "download", loaded: 0, total: null });
    // The mesh's version first (the scan's process state; null when unknown): the engine writes
    // mesh.ply before it counts a new version, so the file read next is this version or a newer one,
    // and a fit sent with it is refused (409) rather than answered for a mesh that isn't on screen.
    let version: number | null = null;
    useProcess
      .getState()
      .fetch(pid, sid)
      .then((st) => {
        version = st?.current?.version ?? null;
        return api.fetchBinary(
          meshUrl,
          (loaded, total) => {
            const now = performance.now();
            if (!inPlace && (now - last > 80 || loaded === total)) {
              last = now;
              setLoad({ phase: "download", loaded, total });
            }
          },
          ctrl.signal,
        );
      })
      .then(
        (buf) =>
          new Promise<void>((resolve) => {
            if (ctrl.signal.aborted) return resolve();
            if (!inPlace) setLoad({ phase: "parse" });
            // Let the "Preparing" state paint before the synchronous parse.
            setTimeout(() => {
              if (ctrl.signal.aborted) return resolve(); // left the page or changed scan meanwhile
              try {
                const g = plyGeometry(readPly(buf));
                g.userData.meshVersion = version;
                disposeMeshGeometry(geomRef.current);
                geomRef.current = g;
                setLoad({ phase: "ready", geometry: g, bytes: buf.byteLength });
                if (!inPlace) setFrameKey((k) => k + 1);
              } catch (e) {
                if (inPlace) toast.error("Couldn't show the finished mesh", e);
                else setLoad({ phase: "error", error: e instanceof Error ? e : new Error(String(e)) });
              }
              resolve();
            }, 30);
          }),
      )
      .catch((e: unknown) => {
        if ((e as { name?: string }).name === "AbortError") return;
        if (inPlace) toast.error("Couldn't show the finished mesh", e); // the mesh on screen stays
        else setLoad({ phase: "error", error: e instanceof Error ? e : new Error(String(e)) });
      });
    return () => ctrl.abort();
  }, [meshUrl, reloadKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(
    () => () => {
      disposeMeshGeometry(geomRef.current);
    },
    [],
  );

  // Report: report.json when present, else the summary embedded in the scan.
  useEffect(() => {
    if (!hasResult || !urls) {
      setReport(null);
      return;
    }
    let cancelled = false;
    const inline = scan?.result?.report && Object.keys(scan.result.report).length ? scan.result.report : null;
    setReport(inline);
    api
      .fetchJson<Rec>(urls.report)
      .then((r) => {
        if (!cancelled && obj(r)) setReport(r);
      })
      .catch(() => {
        /* optional file */
      });
    return () => {
      cancelled = true;
    };
  }, [hasResult, urls?.report]); // eslint-disable-line react-hooks/exhaustive-deps

  // Laser points: fetched the first time they're shown.
  useEffect(() => {
    if (!showLaser || laser || laserLoading || !urls?.laser) return;
    setLaserLoading(true);
    api
      .fetchBinary(urls.laser)
      .then((buf) => setLaser(parsePly(buf)))
      .catch(() => {
        setShowLaser(false);
      })
      .finally(() => setLaserLoading(false));
  }, [showLaser, laser, laserLoading, urls?.laser]);
  useEffect(() => () => laser?.dispose(), [laser]);

  const pendingRef = useRef<Vec3 | null>(null);
  const setPendingPoint = (p: Vec3 | null) => {
    pendingRef.current = p;
    setPending(p);
  };
  // Leaving measure mode drops a half-made measurement.
  useEffect(() => {
    if (!measure) {
      pendingRef.current = null;
      setPending(null);
    }
  }, [measure]);
  const onPick = useCallback(
    (p: Vec3) => {
      const a = pendingRef.current;
      if (!a) {
        pendingRef.current = p;
        setPending(p);
        return;
      }
      pendingRef.current = null;
      setPending(null);
      const version = geomRef.current?.userData.meshVersion;
      void useFeatures.getState().keepMeasurement(pid, sid, typeof version === "number" ? { a, b: p, version } : { a, b: p });
    },
    [pid, sid],
  );

  /** U11: the side panel's four groups, the first open; a tool turned on opens the group its panel is in. */
  const [groupsOpen, setGroupsOpen] = useState<GroupsOpen>(initialOpen);
  const openGroupFor = (t: ViewerTool) => {
    const g = groupOfTool(t);
    if (groupsOpen[g]) return;
    setGroupsOpen((o) => openFor(o, t));
    // the group just opened is brought into the panel's view (it may be below the fold)
    setTimeout(() => document.getElementById(`viewer-group-${g}`)?.parentElement?.scrollIntoView({ block: "start", behavior: "smooth" }), 50);
  };
  const toggleGroup = (id: ViewerGroupId) => setGroupsOpen((o) => ({ ...o, [id]: !o[id] }));
  /** "Set the part's axes…" was pressed (the panel shows; it shows anyway once the scan has axes). */
  const [axesAsked, setAxesAsked] = useState(false);
  /** U25: the shortcut sheet. */
  const [sheetOpen, setSheetOpen] = useState(false);
  useEffect(() => {
    if (shading === "reference") openGroupFor("reference");
  }, [shading]);
  useEffect(() => {
    if (pickPairs) openGroupFor("pairs");
  }, [pickPairs]);

  // Measure, Fit, Section and Flush are one tool at a time.
  const pickTool = (t: "measure" | "fit" | "section" | "flush" | null) => {
    setMeasure(t === "measure");
    setFitOn(t === "fit");
    setSectionOn(t === "section");
    setFlushOn(t === "flush");
    if (t) openGroupFor(t);
  };
  const fitShape = (k: FitKind) => {
    setFitKind(k);
    pickTool("fit");
  };
  /** Read the mesh again with the camera where it is (as when a job of this scan ends). */
  const reloadInPlace = useCallback(() => {
    quiet.current = true;
    setReloadKey((k) => k + 1);
  }, []);

  /** A fit just kept: its region decoded once and mapped onto the mesh it was asked on. */
  const addFit = useCallback(
    (r: KeptFit, g: BufferGeometry) => {
      // the mesh on screen has a known version: the engine fitted another one only when it changed meanwhile
      const shown = g.userData.meshVersion;
      if (typeof shown === "number" && shown !== r.mesh_version) {
        toast.warn("The mesh changed since the viewer opened it", `The engine fitted version ${r.mesh_version}, not the one shown. The fit is kept.`, "Reloading the mesh.");
        reloadInPlace();
        return;
      }
      try {
        const reg = regionOn(g, r.faces, r);
        setRegions((all) => ({ ...all, [r.id]: reg }));
      } catch (e) {
        toast.error("The fit's region doesn't match the mesh on screen", e, { label: "Reload the mesh", run: reloadInPlace });
      }
    },
    [reloadInPlace],
  );

  /** One click with the Fit tool: ask the engine to fit and keep it (one at a time). */
  const onFitPick = useCallback(
    (p: Vec3, face: number) => {
      const g = geomRef.current;
      if (fitRunning.current || !g) return;
      fitRunning.current = true;
      const kind = fitKind;
      // the version of the mesh on screen, when known: the engine refuses the click (409) if it changed since
      const version = typeof g.userData.meshVersion === "number" ? (g.userData.meshVersion as number) : undefined;
      setFitBusy({ p, kind });
      useFeatures
        .getState()
        .keepFit(pid, sid, version === undefined ? { kind, face, point: p } : { kind, face, point: p, version })
        .then((r) => addFit(r, g))
        .catch((e: unknown) => {
          if (e instanceof ApiError && e.status === 409 && version !== undefined) {
            toast.warn("The mesh changed since it was loaded", "The viewer is reloading it.", "Click again once it shows.");
            if (geomRef.current === g) reloadInPlace();
          } else toast.error(`Couldn't fit a ${fitName(kind).toLowerCase()} here`, e);
        })
        .finally(() => {
          fitRunning.current = false;
          setFitBusy(null);
        });
    },
    [pid, sid, fitKind, addFit, reloadInPlace],
  );
  const removeFit = (id: string) => {
    void useFeatures.getState().remove(pid, sid, id);
    setPickedFits((ids) => ids.filter((x) => x !== id));
  };
  const togglePick = (id: string) => setPickedFits((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id].slice(-2)));
  const renameKept = (id: string, name: string) => useFeatures.getState().rename(pid, sid, id, name);

  useHotkeys({
    f: () => viewBus.emit({ type: "frame", animate: true }),
    m: () => pickTool(measure ? null : "measure"),
    g: () => pickTool(fitOn ? null : "fit"),
    p: () => fitShape("plane"),
    c: () => fitShape("cylinder"),
    h: () => fitShape("hole"),
    s: () => pickTool(sectionOn ? null : "section"),
    u: () => pickTool(flushOn ? null : "flush"),
    w: () => setWireframe((v) => !v),
    l: () => urls?.laser && setShowLaser((v) => !v),
    "1": () => setShading("clay"),
    "2": () => setShading("studio"),
    "3": () => setShading("normals"),
    "4": () => hasSigma && setShading("confidence"),
    "5": () => devEnabled && setShading("deviation"),
    "6": () => setShading("lasers"),
    "7": () => refEnabled && setShading("reference"),
    "?": () => setSheetOpen(true),
    escape: () => {
      if (pendingRef.current) setPendingPoint(null);
      else if (measure) setMeasure(false);
      else if (fitOn) setFitOn(false);
      else if (sectionOn) setSectionOn(false);
      else if (flushOn) setFlushOn(false);
    },
  });

  const geometry = load.phase === "ready" ? load.geometry : null;
  // the confidence map needs each point's sigma (meshes made with the confidence reconstruction)
  const hasSigma = !!sigmaOf(geometry);
  // U5: "make it again" only where it would help (this computer has a graphics card)
  const hasCard = hasGraphicsCard((useConnection((s) => s.health)?.engine as { compute?: unknown } | undefined)?.compute);
  // the deviation map needs edits (the cursor of the scan's process state, which this page reads for the
  // mesh version); when that is not known, try and let the answer tell
  const cursor = useProcess((s) => s.entries[processKey(pid, sid)]?.state?.cursor ?? null);
  const devEnabled = cursor === null || cursor > 0;
  const devOn = shading === "deviation" && devEnabled;
  const meshVersion = typeof geometry?.userData.meshVersion === "number" ? (geometry.userData.meshVersion as number) : null;
  const dev = useDeviation(pid, sid, geometry, meshVersion, devOn);
  // the Lasers shading compares the mesh with the scan's laser points; whether it has any, the answer tells
  const las = useLasers(pid, sid, geometry, meshVersion, shading === "lasers");
  const lasShown = las.state.phase === "ready" && hasLaserValues(las.state.data.info);
  const check = useCheck(pid, sid, geometry, meshVersion, reloadInPlace);
  // the comparison with a reference: its shading once the scan is aligned with one
  const cmp = useComparison(pid, sid, geometry, meshVersion, shading === "reference");
  const refEnabled = !!cmp.comparison?.alignment && cmp.comparison.ready;
  // open holes: the count in the mesh's numbers, which this page reads with the mesh version
  const openHoles = useProcess((s) => s.entries[processKey(pid, sid)]?.state?.current?.stats.holes ?? null);
  // the Export group's line for a 3D print: only when the mesh's numbers say it is not closed
  const watertight = useProcess((s) => s.entries[processKey(pid, sid)]?.state?.current?.stats.watertight ?? null);
  // R17: the Make mesh that made this mesh (its whole time)
  const made = useProcess((s) => s.entries[processKey(pid, sid)]?.state?.made ?? null);
  const thinHint = thinPartsToShow(made); // ENGINE_PLAN X5
  const fineNote = fineWallsNote(made); // X5 step 2: what "Make only the thin walls finer" did, or why not
  const shown: Shading =
    shading === "deviation"
      ? devOn && dev.phase === "ready"
        ? "deviation"
        : "clay"
      : shading === "lasers"
        ? lasShown
          ? "lasers"
          : "clay"
        : shading === "reference"
          ? cmp.map.phase === "ready"
            ? "reference"
            : "clay"
          : shading === "confidence" && !hasSigma
            ? "clay"
            : shading;
  const box = geometry?.boundingBox ?? null;
  const size = box ? { x: box.max.x - box.min.x, y: box.max.y - box.min.y, z: box.max.z - box.min.z } : null;
  const tris = geometry ? (geometry.index ? geometry.index.count / 3 : geometry.getAttribute("position").count / 3) : 0;
  const verts = geometry ? geometry.getAttribute("position").count : 0;
  const stem = project && scan ? fileStem(project.name, scan.name) : "scan";
  const FitKindIcon = FIT_ICON[fitKind];

  // The kept set: read when the scan opens and again whenever another mesh version is shown (which fits are stale).
  useEffect(() => {
    void useFeatures.getState().load(pid, sid);
  }, [pid, sid, meshVersion]);
  /** Fitted on the mesh on screen (so its region can be drawn on it). */
  const onScreen = useCallback((f: KeptFeature) => (meshVersion !== null ? f.mesh_version === meshVersion : !f.stale), [meshVersion]);
  // The regions of the kept fits made on the mesh on screen: read once per fit and mesh.
  const asked = useRef(new Set<string>());
  useEffect(() => {
    const g = geometry;
    if (!g || !kept) return;
    for (const f of kept.features) {
      const have = regions[f.id];
      if (!onScreen(f) || (have && have.meshId === g.uuid && have.version === f.mesh_version)) continue;
      const key = `${g.uuid}/${f.id}/${f.mesh_version}`;
      if (asked.current.has(key)) continue;
      asked.current.add(key);
      api
        .featureFaces(pid, sid, f.id)
        .then((r) => {
          if (geomRef.current !== g || r.mesh_version !== f.mesh_version) return;
          const reg = regionOn(g, r.faces, f);
          setRegions((all) => ({ ...all, [f.id]: reg }));
        })
        .catch(() => {
          /* listed without its region on the mesh */
        });
    }
  }, [geometry, kept, regions, onScreen, pid, sid]);
  const fits = useMemo<FitItem[]>(
    () =>
      (kept?.features ?? []).map((f, i) => {
        const reg = geometry && onScreen(f) ? regions[f.id] : undefined;
        const drawn = reg && reg.meshId === geometry!.uuid && reg.version === f.mesh_version ? reg : null;
        return {
          id: i + 1,
          fid: f.id,
          name: f.name,
          kind: f.kind,
          shape: f.shape,
          nFaces: f.n_faces,
          tolerance: f.tolerance_mm,
          meanSigma: f.mean_sigma_mm,
          correlated: f.correlated_points,
          warnings: f.warnings,
          version: f.mesh_version,
          stale: f.stale,
          tris: drawn?.tris ?? null,
          region: drawn?.region ?? null,
          extent: f.extent ?? null,
          form: f.form ?? null,
          nominals: f.nominals ?? [],
          label: fitLabel(f.kind, f.shape),
        };
      }),
    [kept, regions, geometry, onScreen],
  );
  const meshFits = useMemo(() => fits.filter((f) => f.tris), [fits]);
  /** The relation of the two fits picked in the list, from the engine. */
  const pairRelation = usePairRelation(pid, sid, pickedFits[0], pickedFits[1], kept);
  const pickedNums = useMemo(() => fits.filter((f) => pickedFits.includes(f.fid)).map((f) => f.id), [fits, pickedFits]);
  // Suggested nominal values beside the measured ones (FEATURE_PLAN C3): the engine's offers, the approvals, and the
  // offers ignored in this browser (until their value is measured again). Offers that only a known bias allows are
  // asked for only while the switch under the fitted shapes is on (a preference kept in this browser); an approval is
  // sent with that choice, which the engine keeps as its rule. A kept relation's move in the CAD file is chosen here too.
  const nominalWithBias = useUi((u) => u.nominalWithBias);
  const setNominalWithBias = useUi((u) => u.setNominalWithBias);
  const nominalOffers = useNominalOffers(pid, sid, kept, nominalWithBias);
  const ignoredOffers = useIgnoredOffers(pid, sid);
  const nominalActions = useMemo<NominalActions>(
    () => ({
      approve: async (id, value, nominal, source) =>
        !!(await useFeatures.getState().approveNominal(pid, sid, id, { value, nominal, source, ...(nominalWithBias ? { with_bias: true } : {}) })),
      remove: (id, value) => void useFeatures.getState().removeNominal(pid, sid, id, value),
      ignore: ignoredOffers.ignore,
      ignored: ignoredOffers.ignored,
      setMove: (id, move) => void useFeatures.getState().setRelationMove(pid, sid, id, move),
      withBias: nominalWithBias,
    }),
    [pid, sid, ignoredOffers.ignore, ignoredOffers.ignored, nominalWithBias],
  );
  const biasOffers = useMemo(() => ({ on: nominalWithBias, onChange: setNominalWithBias }), [nominalWithBias, setNominalWithBias]);
  const keptMeasurements = useMemo(() => kept?.measurements ?? [], [kept]);
  const measurements = useMemo<Measurement[]>(() => keptMeasurements.map((m, i) => ({ id: i + 1, a: m.a, b: m.b })), [keptMeasurements]);

  // The part's coordinate system: drawn in the view when this version can read it; the panels' numbers in it on request.
  const partFrame = useMemo(() => readFrame(kept?.frame ?? null), [kept]);
  const shownFrame = inPart ? partFrame : null;

  // The Section tool (FEATURE_PLAN I1): the kept planes to cut along, the mesh's box for the sliders, the kept sections
  // (read again with each mesh version: which were cut on an earlier mesh).
  const planeFits = useMemo<PlaneFit[]>(() => (kept?.features ?? []).filter((f) => f.kind === "plane").map((f) => ({ id: f.id, name: f.name, kind: f.kind, shape: f.shape })), [kept]);
  const meshBounds = useMemo<Bounds | null>(() => (box ? { min: [box.min.x, box.min.y, box.min.z], max: [box.max.x, box.max.y, box.max.z] } : null), [box]);
  const section = useSectionTool({ pid, sid, on: sectionOn && !!geometry, fits: planeFits, frame: partFrame, meshVersion, bounds: meshBounds });
  const keptSections = useSections((s) => s.entries[sectionsKey(pid, sid)]?.set?.sections ?? null);
  useEffect(() => {
    void useSections.getState().load(pid, sid);
  }, [pid, sid, meshVersion]);
  const sectionView = useMemo(
    () =>
      sectionOn
        ? { plane: section.plane, answer: section.answer, hideFront: section.hideFront, picking: section.picking, points: section.points, onPickPoint: section.addPoint }
        : null,
    [sectionOn, section.plane, section.answer, section.hideFront, section.picking, section.points, section.addPoint],
  );
  // The Flush tool (FEATURE_PLAN I4): two clicks, the flush along the seam; the kept seams (read again with each mesh
  // version: which were measured on an earlier mesh).
  const flushTool = useFlushTool({ pid, sid, on: flushOn && !!geometry, meshVersion });
  const keptSeams = useSeams((s) => s.entries[seamsKey(pid, sid)]?.set?.seams ?? null);
  useEffect(() => {
    void useSeams.getState().load(pid, sid);
  }, [pid, sid, meshVersion]);
  const flushView = useMemo(
    () => (flushOn ? { clicks: flushTool.clicks, answer: flushTool.answer, picking: flushTool.picking, onPick: flushTool.addClick } : null),
    [flushOn, flushTool.clicks, flushTool.answer, flushTool.picking, flushTool.addClick],
  );
  // a picture of the view for the report marks the fits drawn on the mesh by their numbers and names
  const captureMarks = useMemo<CaptureMark[]>(() => meshFits.filter((f) => f.region).map((f) => ({ p: f.region!.anchor as [number, number, number], text: `${f.id}. ${f.name}` })), [meshFits]);
  // the Compare panel's ghost, picked area and the shading's limit (the tolerance), for the 3D view
  const referenceView = useMemo(
    () => ({
      ghost: cmp.ghost && cmp.refGeometry && cmp.matrix ? { geometry: cmp.refGeometry, matrix: cmp.matrix } : null,
      lit: cmp.lit,
      limit: cmp.tolerance,
    }),
    [cmp.ghost, cmp.refGeometry, cmp.matrix, cmp.lit, cmp.tolerance],
  );
  const axes = axesEntry({ planes: planeFits.length, hasFrame: !!kept?.frame, asked: axesAsked });
  const frameState: "none" | "out_of_date" | "current" = partFrame ? (partFrame.out_of_date ? "out_of_date" : "current") : kept?.frame ? "out_of_date" : "none";
  const deltaNames = shownFrame ? ["ΔX", "ΔY", "ΔZ"] : ["Δx", "Δy", "Δz"];
  const measureRows = useMemo(
    () =>
      keptMeasurements.map((m, i) => {
        const [dx, dy, dz] = deltaOf(m.a, m.b, shownFrame);
        const where = shownFrame ? `along the part's axes (${axisOrder(shownFrame).join(" ")})` : "along the scan's axes";
        const at = (p: Vec3) => fmtVec(shownFrame ? pointToPart(shownFrame, p) : p);
        const tip = `From (${at(m.a)}) to (${at(m.b)}) mm, ${where}.`;
        return { m, i, d: Math.hypot(dx, dy, dz), dx, dy, dz, tip };
      }),
    [keptMeasurements, shownFrame],
  );

  // ---------------------------------------------------------------- render
  let stage: ReactNode;
  if (projectError && !project) {
    stage = (
      <EmptyState
        tone={projectError.status === 404 ? "neutral" : "danger"}
        icon={CircleExclamationPoint}
        title={projectError.status === 404 ? "This project doesn't exist any more" : "Couldn't load the scan"}
        actions={<Button onClick={() => navigate(paths.home())}>All projects</Button>}
      >
        {projectError.detail}
      </EmptyState>
    );
  } else if (!project) {
    stage = (
      <div className="flex items-center gap-2 text-sm text-muted">
        <Spinner size={16} /> Loading the scan…
      </div>
    );
  } else if (!scan) {
    stage = (
      <EmptyState icon={Box} title="This scan isn't in the project" actions={<Button onClick={() => navigate(paths.project(pid))}>Back to the project</Button>}>
        It may have been removed outside Studio.
      </EmptyState>
    );
  } else if (!hasResult) {
    const active = isActive(job) || scan.status === "processing";
    stage = (
      <div className="glass w-[440px] rounded-2xl border border-line-strong p-6 shadow-lg">
        <div className="mb-3 grid h-11 w-11 place-items-center rounded-xl border border-line bg-surface-2 text-accent">
          {active ? <Spinner size={20} /> : <Box size={20} aria-hidden />}
        </div>
        <div className="text-base font-semibold text-fg">{active ? "Making the mesh…" : scan.status === "failed" ? "Making the mesh failed" : "This scan has no mesh yet"}</div>
        <p className="mt-1 text-sm text-muted">
          {active
            ? "The engine is building the mesh from the raw recording. It opens here when it's done."
            : scan.status === "failed"
              ? (job?.error ?? "Try again, or use preview quality. The raw recording is kept.")
              : "Build it from the raw recording: Standard or Best quality for measuring, preview for a quick look."}
        </p>
        <div className="mt-4">
          {active && job ? (
            <JobProgress job={job} />
          ) : active ? (
            <ProgressBar value={null} />
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <MakeMeshButton projectId={pid} scan={scan} variant="primary" size="md" blocked={remakeBlock(scan)} />
              {scan.status !== "recording" ? <ProcessButton projectId={pid} scan={scan} variant="ghost" size="md" label="Edit mesh…" /> : null}
            </div>
          )}
        </div>
      </div>
    );
  } else if (load.phase === "error") {
    const err = load.error;
    stage = (
      <EmptyState
        tone="danger"
        icon={CircleExclamationPoint}
        title="Couldn't open the mesh"
        actions={
          <>
            <Button variant="primary" icon={RefreshCw} onClick={() => setReloadKey((k) => k + 1)}>
              Try again
            </Button>
            <MakeMeshButton projectId={pid} scan={scan} label="Make mesh again" blocked={remakeBlock(scan)} />
          </>
        }
      >
        {err instanceof ApiError ? err.detail : `The file couldn't be read: ${err.message}`}
        {err instanceof ApiError && err.hint ? <div className="mt-1 text-subtle">{err.hint}</div> : null}
      </EmptyState>
    );
  } else if (load.phase !== "ready") {
    const pct = load.phase === "download" && load.total ? load.loaded / load.total : null;
    stage = (
      <div className="w-[300px] text-center">
        <div className="mb-3 flex items-center justify-center gap-2 text-sm text-fg">
          <Spinner size={16} className="text-accent" />
          {load.phase === "parse" ? "Preparing the mesh…" : "Loading the mesh…"}
        </div>
        <ProgressBar value={load.phase === "parse" ? null : pct} label="Loading the mesh" />
        <div className="mt-2 font-mono text-xs text-subtle tnum">
          {load.phase === "download" ? `${fmtBytes(load.loaded)}${load.total ? ` of ${fmtBytes(load.total)}` : ""}` : " "}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full">
      <div className="relative min-w-0 flex-1 overflow-hidden viewport-bg">
        {geometry ? (
          <ErrorBoundary title="The 3D view couldn't start">
            <ViewerCanvas
              geometry={geometry}
              laser={laser}
              shading={shown}
              wireframe={wireframe}
              showLaser={showLaser}
              measure={measure}
              measurements={measurements}
              pending={pending}
              onPick={onPick}
              fit={fitOn}
              fits={meshFits}
              fitBusy={fitBusy}
              pickedFits={pickedNums}
              onFitPick={onFitPick}
              checkLit={check.lit}
              frameKey={frameKey}
              partFrame={partFrame ? { frame: partFrame, outOfDate: partFrame.out_of_date } : null}
              section={sectionView}
              reference={referenceView}
              flush={flushView}
              captureMarks={captureMarks}
            />
          </ErrorBoundary>
        ) : (
          <div className="absolute inset-0 grid place-items-center p-6">{stage}</div>
        )}
        {geometry && remaking ? (
          // R15: the job's progress and Cancel on the page; the mesh on screen is the current one until the new one is in
          <div className="pointer-events-none absolute inset-x-0 bottom-4 z-10 flex justify-center px-4">
            <div className="glass pointer-events-auto w-full max-w-[440px] rounded-xl border border-line-strong px-4 py-3 shadow-lg" role="status" aria-label="Making the mesh">
              <div className="text-sm font-semibold text-fg">Making the mesh</div>
              <p className="mb-2 mt-0.5 text-xs text-muted">The mesh you see, its downloads and its report stay as they are until the new mesh is ready.</p>
              {job && isActive(job) ? (
                <JobProgress job={job} label="Making the mesh" cancelTitle="Stop making the mesh. The current mesh stays as it is." />
              ) : (
                <ProgressBar value={null} label="Making the mesh" />
              )}
            </div>
          </div>
        ) : null}

        {/* breadcrumb */}
        {/* bounded on the right too, so the toolbar wraps inside the 3D view instead of running under the side panel */}
        <div className="pointer-events-none absolute left-4 right-4 top-4 flex flex-col items-start gap-2">
          <div className="glass pointer-events-auto flex h-9 max-w-full items-center gap-1.5 rounded-xl border border-line-strong pl-1.5 pr-3 text-sm shadow-md">
            <IconButton icon={ArrowLeft} size="sm" label="Back to the project" onClick={() => navigate(paths.project(pid))} />
            <a href={href(paths.project(pid))} className="max-w-[220px] truncate text-muted hover:text-fg">
              {project?.name ?? "Project"}
            </a>
            <span className="text-subtle" aria-hidden>
              /
            </span>
            <span className="max-w-[260px] truncate font-medium text-fg">{scan?.name ?? "Scan"}</span>
          </div>
          {geometry ? (
            <div className="glass pointer-events-auto flex max-w-full flex-wrap items-center gap-1 rounded-xl border border-line-strong p-1 shadow-md" role="toolbar" aria-label="View options">
              <Segmented<Shading>
                label="Shading"
                size="sm"
                wrap
                value={devOn ? "deviation" : shading === "lasers" || shading === "reference" ? shading : shown}
                onChange={setShading}
                options={[
                  { value: "clay", label: "Grey", icon: Brush, title: "Plain grey (1)" },
                  { value: "studio", label: "Studio", icon: Sparkles, title: "Studio lighting (2)" },
                  { value: "normals", label: "Direction", icon: Orbit, title: "Colour by direction (3)" },
                  {
                    value: "confidence",
                    label: "Confidence",
                    icon: Target,
                    disabled: !hasSigma,
                    title: hasSigma
                      ? "How well each point was measured (4)"
                      : noConfidenceText(hasCard, "viewer"),
                  },
                  {
                    value: "deviation",
                    label: "Deviation",
                    icon: Diff,
                    disabled: !devEnabled,
                    title: devEnabled ? "How far the edits moved the surface from the mesh as measured (5)" : "No edits yet: the mesh is as measured",
                  },
                  { value: "lasers", label: "Lasers", icon: ScanLine, title: "Where the laser lines and the mesh agree, and where they differ (6)" },
                  {
                    value: "reference",
                    label: "CAD model",
                    icon: COMPARE_ICON,
                    disabled: !refEnabled,
                    title: refEnabled ? "How far the scan is from the CAD model, against a tolerance (7)" : "Compare the scan with a CAD model first (the Compare panel)",
                  },
                ]}
              />
              <span className="mx-1 h-5 w-px bg-line-strong" aria-hidden />
              <IconButton icon={Grid2x2} size="sm" label="Wireframe (W)" active={wireframe} onClick={() => setWireframe((v) => !v)} />
              <IconButton
                icon={Layers}
                size="sm"
                label={urls?.laser ? "Laser point cloud (L)" : "No laser points in this result"}
                active={showLaser}
                loading={laserLoading}
                disabled={!urls?.laser}
                onClick={() => setShowLaser((v) => !v)}
              />
              <span className="mx-1 h-5 w-px bg-line-strong" aria-hidden />
              <Button
                size="sm"
                variant={measure ? "primary" : "ghost"}
                icon={Ruler}
                onClick={() => pickTool(measure ? null : "measure")}
                aria-pressed={measure}
                title="Measure a distance (M)"
              >
                Measure
              </Button>
              <Button
                size="sm"
                variant={fitOn ? "primary" : "ghost"}
                icon={Shapes}
                onClick={() => pickTool(fitOn ? null : "fit")}
                aria-pressed={fitOn}
                title="Fit a plane, cylinder or hole where you click (G)"
              >
                Fit
              </Button>
              <Button
                size="sm"
                variant={sectionOn ? "primary" : "ghost"}
                icon={SECTION_ICON}
                onClick={() => pickTool(sectionOn ? null : "section")}
                aria-pressed={sectionOn}
                title="Cut the scan with a plane: its 2D profile, a distance on it, DXF and SVG (S)"
              >
                Section
              </Button>
              <Button
                size="sm"
                variant={flushOn ? "primary" : "ghost"}
                icon={FLUSH_ICON}
                onClick={() => pickTool(flushOn ? null : "flush")}
                aria-pressed={flushOn}
                title="The flush between two panels along their seam: click once on each panel (U)"
              >
                Flush
              </Button>
              {fitOn ? (
                <Segmented<FitKind>
                  label="Shape to fit"
                  size="sm"
                  value={fitKind}
                  onChange={setFitKind}
                  options={[
                    { value: "plane", label: "Plane", icon: FIT_ICON.plane, title: "A flat face (P)" },
                    { value: "cylinder", label: "Cylinder", icon: FIT_ICON.cylinder, title: "A shaft, a boss or the wall of a bore (C)" },
                    { value: "hole", label: "Hole", icon: FIT_ICON.hole, title: "A hole's round edge (H)" },
                  ]}
                />
              ) : null}
              <IconButton icon={Focus} size="sm" label="Frame the model (F)" onClick={() => viewBus.emit({ type: "frame", animate: true })} />
              <span className="mx-1 h-5 w-px bg-line-strong" aria-hidden />
              <Button size="sm" variant="ghost" icon={SlidersHorizontal} onClick={() => navigate(paths.process(pid, sid))} title="Clean up, fill holes, smooth or simplify, with undo">
                Edit mesh…
              </Button>
            </div>
          ) : null}
        </div>

        {shown === "confidence" && geometry ? (
          <div className="glass absolute bottom-4 left-4 animate-pop-in rounded-xl border border-line-strong px-3 py-2.5 shadow-md">
            <ConfidenceLegend geometry={geometry} />
          </div>
        ) : null}

        {shading === "deviation" && geometry ? (
          <div className="glass absolute bottom-4 left-4 animate-pop-in rounded-xl border border-line-strong px-3 py-2.5 shadow-md">
            <DeviationLegend state={devEnabled ? dev : { phase: "unavailable", message: "No edits yet: the mesh is as measured" }} />
          </div>
        ) : null}

        {shading === "lasers" && geometry ? (
          <div className="glass absolute bottom-4 left-4 animate-pop-in rounded-xl border border-line-strong px-3 py-2.5 shadow-md">
            <LasersLegend state={las.state} onRetry={las.retry} onReload={reloadInPlace} />
          </div>
        ) : null}

        {shading === "reference" && geometry ? (
          <div className="glass absolute bottom-4 left-4 animate-pop-in rounded-xl border border-line-strong px-3 py-2.5 shadow-md">
            <ReferenceLegend state={refEnabled ? cmp.map : { phase: "idle" }} tolerance={cmp.tolerance} answer={cmp.answer} />
          </div>
        ) : null}

        {pickPairs && geometry && cmp.refGeometry && cmp.comparison ? (
          <PointPairPicker
            scan={geometry}
            reference={cmp.refGeometry}
            scale={cmp.comparison.scale}
            busy={cmp.busy}
            onClose={() => setPickPairs(false)}
            onAlign={(pairs, refine) => {
              cmp.align({ method: "points", pairs, refine });
              setPickPairs(false);
            }}
          />
        ) : null}

        {measure && geometry ? (
          <div className="pointer-events-none absolute bottom-5 left-1/2 -translate-x-1/2">
            <div className="glass flex animate-pop-in items-center gap-2 rounded-full border border-accent-line px-4 py-2 text-sm text-fg shadow-lg">
              <Ruler size={15} className="text-accent" aria-hidden />
              {pending ? "Click the second point" : "Click two points on the surface"}
              <span className="text-subtle">·</span>
              <span className="text-xs text-muted">
                <Kbd>Esc</Kbd> {pending ? "cancel" : "done"}
              </span>
            </div>
          </div>
        ) : null}

        {sectionOn && geometry && section.picking ? (
          <div className="pointer-events-none absolute bottom-5 left-4">
            <div className="glass flex animate-pop-in items-center gap-2 whitespace-nowrap rounded-full border border-accent-line px-4 py-2 text-sm text-fg shadow-lg" role="status">
              <SECTION_ICON size={15} className="text-accent" aria-hidden />
              Click point {section.points.length + 1} of 3 on the surface
              <span className="text-subtle">·</span>
              <span className="text-xs text-muted">
                <Kbd>Esc</Kbd> done
              </span>
            </div>
          </div>
        ) : null}

        {flushOn && geometry && flushTool.picking ? (
          <div className="pointer-events-none absolute bottom-5 left-1/2 -translate-x-1/2">
            <div className="glass flex animate-pop-in items-center gap-2 whitespace-nowrap rounded-full border border-accent-line px-4 py-2 text-sm text-fg shadow-lg" role="status">
              <FLUSH_ICON size={15} className="text-accent" aria-hidden />
              {flushTool.clicks.length === 0 ? "Click panel A, near the seam" : "Now click panel B, across the seam"}
              <span className="text-subtle">·</span>
              <span className="text-xs text-muted">
                <Kbd>Esc</Kbd> done
              </span>
            </div>
          </div>
        ) : null}

        {sectionOn && geometry && section.answer ? (
          <SectionProfile answer={section.answer} frame={partFrame} busy={section.busy} onClose={() => setSectionOn(false)} />
        ) : null}

        {fitOn && geometry ? (
          <div className="pointer-events-none absolute bottom-5 left-1/2 -translate-x-1/2">
            <div className="glass flex animate-pop-in items-center gap-2 whitespace-nowrap rounded-full border border-accent-line px-4 py-2 text-sm text-fg shadow-lg" role="status">
              {fitBusy ? <Spinner size={15} className="text-accent" /> : <FitKindIcon size={15} className="text-accent" aria-hidden />}
              {fitBusy ? `Fitting a ${fitName(fitBusy.kind).toLowerCase()}…` : FIT_HINT[fitKind]}
              <span className="text-subtle">·</span>
              <span className="text-xs text-muted">
                <Kbd>Esc</Kbd> done
              </span>
            </div>
          </div>
        ) : null}
      </div>

      {/* no side panel for a project that is gone: its "Is it good?" and "Export" would have nothing to act on */}
      {projectError && !project ? null : (
      <aside className="relative flex w-[330px] shrink-0 flex-col overflow-y-auto border-l border-line bg-panel 2xl:w-[360px]" aria-label="Result details">
        <div className="border-b border-line px-4 py-4">
          {scan ? (
            <>
              <div className="flex items-start gap-2">
                <h1 className="min-w-0 flex-1 truncate text-base font-semibold text-fg" title={scan.name}>
                  {scan.name}
                </h1>
                <ScanStatusBadge scan={scan} job={job} />
              </div>
              <div className="mt-1 text-xs text-subtle">
                {fmtDateTime(scan.created)} · {scan.mode === "markers" ? "Markers" : "Markerless"} · {fmtInt(scan.n_frames)} frames ·{" "}
                {fmtDuration(scan.duration_s)}
              </div>
            </>
          ) : (
            <div className="space-y-2">
              <Skeleton className="h-5 w-40" />
              <Skeleton className="h-3 w-56" />
            </div>
          )}
          {/* U11: the size stays in view above the groups; its details are in the processing report */}
          {size ? (
            <div className="mt-3" aria-label="Size">
              <div className="grid grid-cols-3 gap-2">
                {(["x", "y", "z"] as const).map((k) => (
                  <div key={k} className="rounded-lg border border-line bg-surface-2/60 px-2.5 py-1.5">
                    <div className="text-[10.5px] font-semibold uppercase text-subtle">{k}</div>
                    <div className="font-mono text-sm font-semibold text-fg tnum">
                      {fmtFixed(size[k], 1)}
                      <span className="text-[10.5px] font-normal text-subtle">&#8239;mm</span>
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-1.5 text-[11px] text-subtle">Size along the scan's own directions.</div>
            </div>
          ) : hasResult && load.phase !== "error" ? (
            <div className="mt-3 grid grid-cols-3 gap-2" aria-busy="true">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-[46px]" />
              ))}
            </div>
          ) : null}
        </div>

        <Group id="good" title="Is it good?" icon={CHECK_ICON} open={groupsOpen.good} onToggle={toggleGroup}>
          {hasResult ? (
            <Panel title="Scan check" icon={CHECK_ICON}>
              <CheckPanel
                check={check}
                ready={!!geometry}
                onScanAreas={
                  project && scan?.result && check.result ? (pick) => usePass.getState().scanAreas({ projectId: project.id, scan, result: check.result!, why: check.why.why, ...pick }) : undefined
                }
                hasSigma={hasSigma}
                hasCard={hasCard}
                remake={
                  project && scan ? (
                    <span className="inline-flex flex-col items-start gap-1">
                      <MakeMeshButton projectId={project.id} scan={scan} label="Make the mesh again" blocked={remakeBlock(scan)} />
                      {remakeBlock(scan) ? <span className="text-[11.5px] leading-4 text-subtle">{remakeBlock(scan)}</span> : null}
                    </span>
                  ) : undefined
                }
                holes={
                  openHoles
                    ? {
                        count: openHoles,
                        action: (
                          <Button size="xs" icon={SlidersHorizontal} onClick={() => navigate(paths.process(pid, sid))}>
                            See them on the Edit mesh page
                          </Button>
                        ),
                      }
                    : null
                }
              />
            </Panel>
          ) : null}
          <Panel title="Processing report" icon={FileText}>
            {/* ENGINE_PLAN X5: thin walls, a thin plate or small holes found; a suggestion with its choices. After a
                mesh made with "Make only the thin walls finer" on: what it did (or why not) in the hint's place */}
            {(thinHint || fineNote) && project && scan ? (
              <div className="mb-4 space-y-2">
                {fineNote ? <FineWallsNote pid={pid} sid={sid} name={scan.name} made={made} quality={report?.quality} blocked={remakeBlock(scan)} /> : null}
                {thinHint ? <ThinPartsHint pid={pid} sid={sid} name={scan.name} hint={thinHint} quality={report?.quality} blocked={remakeBlock(scan)} /> : null}
              </div>
            ) : null}
            {report ? (
              <ReportSummary report={report} merged={!!scan?.merged_from?.length} made={made} />
            ) : hasResult ? (
              <div className="text-sm text-muted">No processing report for this result.</div>
            ) : (
              <div className="text-sm text-muted">Shown after meshing.</div>
            )}
            {size ? (
              <KeyValue
                dense
                className="mt-3"
                rows={[
                  ["Diagonal", <span className="font-mono text-xs tnum">{fmtFixed(Math.hypot(size.x, size.y, size.z), 1)}&#8239;mm</span>],
                  ["Triangles", <span className="font-mono text-xs tnum">{fmtInt(tris)}</span>],
                  ["Vertices", <span className="font-mono text-xs tnum">{fmtInt(verts)}</span>],
                  load.phase === "ready" ? ["PLY size", <span className="font-mono text-xs tnum">{fmtBytes(load.bytes)}</span>] : null,
                ]}
              />
            ) : null}
          </Panel>
        </Group>

        <Group
          id="measure"
          title="Measure"
          icon={Ruler}
          open={groupsOpen.measure}
          onToggle={toggleGroup}
          summary={savedSummary(keptMeasurements.length, fits.length, keptSections?.length ?? 0, keptSeams?.length ?? 0)}
        >
        <Panel
          title="Measurements"
          icon={Ruler}
          aside={
            keptMeasurements.length ? (
              <Button
                size="xs"
                variant="ghost"
                icon={Trash}
                title="Remove all measurements"
                onClick={() => {
                  void useFeatures.getState().removeAll(
                    pid,
                    sid,
                    keptMeasurements.map((m) => m.id),
                  );
                  setPendingPoint(null);
                }}
              >
                Clear
              </Button>
            ) : null
          }
        >
          {measureRows.length ? (
            <ul className="space-y-2">
              {measureRows.map(({ m, i, d, dx, dy, dz, tip }) => (
                <li key={m.id} className="group rounded-lg border border-line bg-surface-2/60 px-3 py-2" title={tip}>
                  <div className="flex items-center gap-2">
                    <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-accent-soft text-[10.5px] font-bold text-accent">{i + 1}</span>
                    <NameEdit value={m.name} what="measurement" onRename={(n) => renameKept(m.id, n)} className="min-w-0 max-w-[45%] text-xs font-semibold text-fg" />
                    <span className="flex-1 text-right font-mono text-[15px] font-semibold text-fg tnum">{d.toFixed(2)}&#8239;mm</span>
                    <IconButton
                      icon={X}
                      size="xs"
                      label={`Remove ${m.name}`}
                      className="opacity-60 group-hover:opacity-100"
                      onClick={() => void useFeatures.getState().remove(pid, sid, m.id)}
                    />
                  </div>
                  <div className="mt-1 flex gap-3 pl-7 font-mono text-[11px] text-subtle tnum">
                    <span>
                      {deltaNames[0]} {dx.toFixed(1)}
                    </span>
                    <span>
                      {deltaNames[1]} {dy.toFixed(1)}
                    </span>
                    <span>
                      {deltaNames[2]} {dz.toFixed(1)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <div className="text-sm text-muted">
              {geometry ? (
                <>
                  Turn on <b className="text-fg">Measure</b> (<Kbd>M</Kbd>) and click two points on the surface to get the distance in mm.
                </>
              ) : (
                "Available once the mesh is loaded."
              )}
            </div>
          )}
          {geometry && !measure ? (
            <Button size="sm" className="mt-3" icon={Ruler} onClick={() => pickTool("measure")}>
              Measure a distance
            </Button>
          ) : null}
        </Panel>

        <Panel
          title="Fitted shapes"
          icon={Shapes}
          aside={
            fits.length ? (
              <Button
                size="xs"
                variant="ghost"
                icon={Trash}
                title="Remove all fitted shapes"
                onClick={() => {
                  void useFeatures.getState().removeAll(
                    pid,
                    sid,
                    fits.map((f) => f.fid),
                  );
                  setPickedFits([]);
                }}
              >
                Clear
              </Button>
            ) : null
          }
        >
          <FitList
            fits={fits}
            relations={kept?.relations ?? []}
            picked={pickedFits}
            pairRelation={pairRelation}
            onTogglePick={togglePick}
            onPickPair={(a, b) => setPickedFits([a, b])}
            onRemove={removeFit}
            onRename={renameKept}
            onKeepRelation={(a, b) => void useFeatures.getState().keepRelation(pid, sid, a, b)}
            onRemoveRelation={(id) => void useFeatures.getState().remove(pid, sid, id)}
            onRefit={() => void useFeatures.getState().refit(pid, sid)}
            refitting={refitting}
            onStart={() => pickTool("fit")}
            fitOn={fitOn}
            ready={!!geometry}
            frame={shownFrame}
            nominals={nominalOffers}
            nominalActions={nominalActions}
            biasOffers={biasOffers}
          />
        </Panel>

        {/* U11: the part's axes only once a plane is saved, first as "Set the part's axes…" */}
        {axes === "offer" ? (
          <div className="border-t border-line/70 px-4 py-3">
            <Button size="sm" icon={FRAME_ICON} onClick={() => setAxesAsked(true)}>
              Set the part's axes…
            </Button>
            <p className="mt-1.5 text-[11.5px] leading-4 text-subtle">Level, turn and zero the scan on the part's own faces and holes, from the saved fitted shapes.</p>
          </div>
        ) : axes === "panel" ? (
          <Panel title="The part's axes" icon={FRAME_ICON}>
            <FramePanel
              raw={kept?.frame ?? null}
              fits={fits}
              selected={pickedFits.length ? pickedFits[pickedFits.length - 1] : null}
              inPart={inPart}
              onInPart={setInPart}
              framing={framing}
              onBuild={(body) => useFeatures.getState().buildFrame(pid, sid, body)}
              onRemove={() => {
                void useFeatures.getState().removeFrame(pid, sid);
                setInPart(false);
              }}
              ready={!!geometry}
            />
          </Panel>
        ) : null}

        <Panel title="Sections" icon={SECTION_ICON}>
          <SectionPanel
            pid={pid}
            sid={sid}
            on={sectionOn && !!geometry}
            onToggle={(on) => pickTool(on ? "section" : null)}
            tool={section}
            fits={fits}
            hasFrame={!!partFrame}
            kept={keptSections ?? []}
            ready={!!geometry}
          />
        </Panel>

        <Panel title="Flush along a seam" icon={FLUSH_ICON}>
          <FlushPanel pid={pid} sid={sid} on={flushOn && !!geometry} onToggle={(on) => pickTool(on ? "flush" : null)} tool={flushTool} kept={keptSeams ?? []} ready={!!geometry} />
        </Panel>
        </Group>

        <Group id="cad" title="Compare with CAD" icon={COMPARE_ICON} open={groupsOpen.cad} onToggle={toggleGroup} summary={refEnabled ? "Aligned with a CAD model" : null}>
          {hasResult ? (
            <Panel title="Compare with the CAD model" icon={COMPARE_ICON}>
              <ComparePanel
                tool={cmp}
                ready={!!geometry}
                frame={frameState}
                shadingOn={shading === "reference"}
                onShading={(on) => setShading(on ? "reference" : "clay")}
                onPickPoints={() => setPickPairs(true)}
                picking={pickPairs}
              />
            </Panel>
          ) : (
            <div className="px-4 pb-4 text-sm text-muted">Make the mesh first.</div>
          )}
        </Group>

        <Group id="export" title="Export" icon={Download} open={groupsOpen.export} onToggle={toggleGroup}>
        <Panel title="Download" icon={Download}>
          {hasResult && urls ? (
            <>
            {/* U42: the one download list (STL, PLY, OBJ, laser points), as on the project and Process pages */}
            <MeshDownloadButtons urls={{ stl: urls.stl, ply: urls.mesh, obj: api.fileUrl(pid, sid, "mesh.obj"), laser: urls.laser }} stem={stem} />
            <div className="mt-2 grid grid-cols-2 gap-2">
              <Button
                size="sm"
                className="col-span-2"
                icon={FileText}
                onClick={() => void downloadFile(urls.report, `${stem}-processing-report.json`)}
                title="How the engine made the mesh (frames, tracking, timings), as JSON: not the scan's report below"
              >
                Processing report (JSON)
              </Button>
              {partFrame ? (
                <>
                  <Button size="sm" className="col-span-2" icon={Download} onClick={() => void downloadFile(api.frameMeshUrl(pid, sid, "mesh.stl"), `${stem}-part-frame.stl`)} title="The mesh along the part's axes">
                    STL along the part's axes
                  </Button>
                  <Button size="sm" className="col-span-2" icon={Download} onClick={() => void downloadFile(api.frameMeshUrl(pid, sid, "mesh.ply"), `${stem}-part-frame.ply`)} title="The mesh along the part's axes">
                    PLY along the part's axes
                  </Button>
                </>
              ) : null}
            </div>
            </>
          ) : (
            <div className="text-sm text-muted">Make the mesh first.</div>
          )}
          {hasResult && project && scan ? (
            <>
              <p className="mt-2 text-xs text-subtle">
                Units: mm. STL for CAD and slicers. {meshAxesLine(partFrame ? "current" : axes, !!partFrame?.out_of_date)}
              </p>
              {watertight === false ? (
                <p className="mt-1 text-xs text-subtle">
                  {PRINT_CLOSED_LINE}{" "}
                  <button type="button" className="text-accent underline-offset-2 hover:underline" onClick={() => navigate(paths.process(pid, sid))}>
                    Open Edit mesh
                  </button>
                </p>
              ) : null}
            </>
          ) : null}
          {hasResult && urls ? <CadDownload pid={pid} sid={sid} stem={stem} kept={kept} hasMesh={hasResult} /> : null}
          {hasResult ? (
            <ReportGroup pid={pid} sid={sid} checkTolerance={check.tolerance} hasFrame={!!partFrame} seams={keptSeams ?? []} views={reportViews} onViews={setReportViews} ready={!!geometry} />
          ) : null}
        </Panel>

        {hasResult && project && scan ? (
          <Panel title="Edit mesh" icon={SlidersHorizontal}>
            <p className="text-sm text-muted">Remove loose bits, fill holes, smooth or simplify, cut away what you don't need. Every step can be undone.</p>
            <ProcessButton projectId={project.id} scan={scan} variant="primary" className="mt-3" label="Edit mesh…" />
          </Panel>
        ) : null}

        {hasResult && project && scan ? (
          <Panel title="Make mesh again" icon={RefreshCw}>
            {/* R2: a scan without its recording cannot be made again: the reason instead of the button (lib/keepMode.ts) */}
            {remakeBlock(scan) ? (
              <p className="text-xs text-subtle">{remakeBlock(scan)}</p>
            ) : (
              <>
                <p className="text-sm text-muted">From the raw recording, at the quality you choose. Saved measurements and fitted shapes stay; they are marked when the new mesh differs.</p>
                <MakeMeshButton projectId={project.id} scan={scan} label="Make mesh again" className="mt-3" />
              </>
            )}
          </Panel>
        ) : null}
        </Group>

        <div className="space-y-1.5 px-4 py-3 text-[11px] leading-5 text-subtle">
          <div>
            <Eye size={12} className="mr-1 inline" aria-hidden />
            Left drag orbits, right drag pans, the wheel zooms towards the cursor.
          </div>
          <ShortcutButton onClick={() => setSheetOpen(true)} className="-ml-2" />
          {tris > 0 ? <div className="sr-only">{fmtCompact(tris)} triangles</div> : null}
        </div>
      </aside>
      )}
      <ShortcutSheet open={sheetOpen} onClose={() => setSheetOpen(false)} sheet={viewerSheet({ sigma: hasSigma, deviation: devEnabled, reference: refEnabled })} />
    </div>
  );
}
