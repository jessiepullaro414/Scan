/**
 * Scan result viewer: loads `mesh.ply` from the engine (the fast PLY reader), studio shading,
 * wireframe, normals, the confidence map (each point's sigma, when the mesh has them) and
 * laser-point overlays, two-click distance measurement, fitted shapes (plane, cylinder, hole: one
 * click, the engine fits it, with its ± and the relation of two fits; fits, measurements and relations
 * are kept with the scan, with names, through store/features.ts), the part's coordinate system built from
 * three kept fits (the Coordinate system panel: its axes drawn in the view, the panels' numbers in it on
 * request, the mesh downloaded in it), the Check panel (which areas miss the tolerance the part needs),
 * bounding box in mm, downloads (the kept fits for CAD too: a STEP file and a FreeCAD macro,
 * viewer/CadDownload.tsx) and the processing report.
 */
import {
  ArrowLeft,
  Box,
  CircleExclamationPoint,
  Download,
  Eye,
  FileText,
  Focus,
  Grid2x2,
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
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { BufferGeometry } from "three";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { ApiError, api } from "../api/http";
import type { FitKind, KeptFeature, KeptFit, Project, Scan } from "../api/types";
import { JobProgress, MakeMeshButton, ProcessButton, ScanStatusBadge } from "../components/ScanBits";
import { ErrorBoundary } from "../components/shell/ErrorBoundary";
import { Button, IconButton } from "../components/ui/Button";
import { EmptyState, Kbd, ProgressBar, Skeleton, Spinner } from "../components/ui/Feedback";
import { Segmented } from "../components/ui/Form";
import { KeyValue } from "../components/ui/Layout";
import { downloadFile } from "../lib/download";
import { decodeFaces, fitLabel, fitName, regionTriangles, summarizeRegion, type RegionSummary } from "../lib/fits";
import { fileStem, fmtBytes, fmtCompact, fmtDateTime, fmtDuration, fmtFixed, fmtInt, fmtPct } from "../lib/format";
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
import { CHECK_ICON, CheckPanel } from "./viewer/CheckPanel";
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
import { fmtSigma, ViewerCanvas, type Measurement, type Shading, type Vec3 } from "./viewer/ViewerCanvas";

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

function ReportSummary({ report }: { report: Rec }) {
  const frames = obj(report.frames);
  const mesh = obj(report.mesh);
  const lasers = obj(report.lasers);
  const gt = obj(report.ground_truth);
  const failures = obj(report.failures);
  const gtTrans = obj(gt?.trans_mm);
  const gtRot = obj(gt?.rot_deg);
  const sigma = obj(mesh?.sigma_mm);
  const failureRows = failures ? Object.entries(failures).filter(([, v]) => num(v)) : [];
  return (
    <div className="space-y-4">
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
          mesh && typeof mesh.method === "string" ? ["Meshing", `${mesh.method.toUpperCase()}${num(mesh.voxel_mm) !== null ? ` · ${fmtFixed(num(mesh.voxel_mm), 2)} mm voxels` : ""}`] : null,
          sigma && num(sigma.median) !== null
            ? [
                "Uncertainty",
                <span className="tnum" title="Each point's 1σ, as fused (before any edits)">
                  median {fmtSigma(num(sigma.median)!)}
                  {num(sigma.within_0_1) !== null ? <span className="text-muted"> · {fmtPct(num(sigma.within_0_1))} within 0.1 mm</span> : null}
                </span>,
              ]
            : null,
          lasers && num(lasers.n_points_exported) !== null ? ["Laser points", <span className="tnum">{fmtInt(num(lasers.n_points_exported))}</span>] : null,
          num(report.wall_time_s) !== null ? ["Processing time", fmtDuration(num(report.wall_time_s))] : null,
          typeof report.source === "string" ? ["Source", report.source === "sim" ? "Simulator" : report.source] : null,
        ]}
      />
      {gt && num(gt.n) ? (
        <div className="rounded-lg border border-ok/25 bg-ok-soft/50 px-3 py-2.5">
          <div className="mb-1.5 text-xs font-semibold text-ok">Accuracy vs ground truth (simulator)</div>
          <KeyValue
            dense
            rows={[
              num(gt.rms_trans_mm) !== null ? ["Position RMS", <span className="font-mono text-xs tnum">{fmtFixed(num(gt.rms_trans_mm), 2)} mm</span>] : null,
              gtTrans && num(gtTrans.max) !== null ? ["Position max", <span className="font-mono text-xs tnum">{fmtFixed(num(gtTrans.max), 2)} mm</span>] : null,
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
function Panel({ title, icon: Icon, children, aside }: { title: string; icon: typeof Box; children: ReactNode; aside?: ReactNode }) {
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
  const hasResult = !!scan && scan.status === "processed" && !!scan.result;

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

  // Measure and Fit are one tool at a time.
  const pickTool = (t: "measure" | "fit" | null) => {
    setMeasure(t === "measure");
    setFitOn(t === "fit");
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
    w: () => setWireframe((v) => !v),
    l: () => urls?.laser && setShowLaser((v) => !v),
    "1": () => setShading("clay"),
    "2": () => setShading("studio"),
    "3": () => setShading("normals"),
    "4": () => hasSigma && setShading("confidence"),
    "5": () => devEnabled && setShading("deviation"),
    "6": () => setShading("lasers"),
    escape: () => {
      if (pendingRef.current) setPendingPoint(null);
      else if (measure) setMeasure(false);
      else if (fitOn) setFitOn(false);
    },
  });

  const geometry = load.phase === "ready" ? load.geometry : null;
  // the confidence map needs each point's sigma (meshes made with the confidence reconstruction)
  const hasSigma = !!sigmaOf(geometry);
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
  // open holes: the count in the mesh's numbers, which this page reads with the mesh version
  const openHoles = useProcess((s) => s.entries[processKey(pid, sid)]?.state?.current?.stats.holes ?? null);
  const shown: Shading =
    shading === "deviation"
      ? devOn && dev.phase === "ready"
        ? "deviation"
        : "clay"
      : shading === "lasers"
        ? lasShown
          ? "lasers"
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
  const deltaNames = shownFrame ? ["ΔX", "ΔY", "ΔZ"] : ["Δx", "Δy", "Δz"];
  const measureRows = useMemo(
    () =>
      keptMeasurements.map((m, i) => {
        const [dx, dy, dz] = deltaOf(m.a, m.b, shownFrame);
        const where = shownFrame ? `in the part's frame (${axisOrder(shownFrame).join(" ")})` : "in the scan's frame";
        const at = (p: Vec3) => fmtVec(shownFrame ? pointToPart(shownFrame, p) : p);
        const tip = `From (${at(m.a)}) to (${at(m.b)}) mm, ${where}.`;
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
        <div className="text-base font-semibold text-fg">{active ? "Making the mesh…" : scan.status === "failed" ? "Meshing failed" : "This scan has no mesh yet"}</div>
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
              <MakeMeshButton projectId={pid} scan={scan} variant="primary" size="md" />
              {scan.status !== "recording" ? <ProcessButton projectId={pid} scan={scan} variant="ghost" size="md" label="Step by step…" /> : null}
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
            <MakeMeshButton projectId={pid} scan={scan} label="Rebuild the mesh" />
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
            />
          </ErrorBoundary>
        ) : (
          <div className="absolute inset-0 grid place-items-center p-6">{stage}</div>
        )}

        {/* breadcrumb */}
        <div className="pointer-events-none absolute left-4 top-4 flex flex-col gap-2">
          <div className="glass pointer-events-auto flex h-9 items-center gap-1.5 rounded-xl border border-line-strong pl-1.5 pr-3 text-sm shadow-md">
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
            <div className="glass pointer-events-auto flex flex-wrap items-center gap-1 rounded-xl border border-line-strong p-1 shadow-md" role="toolbar" aria-label="View options">
              <Segmented<Shading>
                label="Shading"
                size="sm"
                value={devOn ? "deviation" : shading === "lasers" ? "lasers" : shown}
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
                      : "This mesh has no confidence data: it was made without a GPU or by an earlier version. Make it again to get it.",
                  },
                  {
                    value: "deviation",
                    label: "Deviation",
                    icon: Diff,
                    disabled: !devEnabled,
                    title: devEnabled ? "How far the edits moved the surface from the mesh as measured (5)" : "No edits yet: the mesh is as measured",
                  },
                  { value: "lasers", label: "Lasers", icon: ScanLine, title: "Where the laser lines and the mesh agree, and where they differ (6)" },
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
                Process…
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
        </div>

        <Panel title="Size" icon={Box}>
          {size ? (
            <>
              <div className="grid grid-cols-3 gap-2">
                {(["x", "y", "z"] as const).map((k) => (
                  <div key={k} className="rounded-lg border border-line bg-surface-2/60 px-2.5 py-2">
                    <div className="text-[10.5px] font-semibold uppercase text-subtle">{k}</div>
                    <div className="font-mono text-sm font-semibold text-fg tnum">{fmtFixed(size[k], 1)}</div>
                    <div className="text-[10px] text-subtle">mm</div>
                  </div>
                ))}
              </div>
              <KeyValue
                dense
                className="mt-3"
                rows={[
                  ["Diagonal", <span className="font-mono text-xs tnum">{fmtFixed(Math.hypot(size.x, size.y, size.z), 1)} mm</span>],
                  ["Triangles", <span className="font-mono text-xs tnum">{fmtInt(tris)}</span>],
                  ["Vertices", <span className="font-mono text-xs tnum">{fmtInt(verts)}</span>],
                  load.phase === "ready" ? ["PLY size", <span className="font-mono text-xs tnum">{fmtBytes(load.bytes)}</span>] : null,
                ]}
              />
              <div className="mt-2 text-[11px] text-subtle">Bounding box along the scan's axes (the first camera frame).</div>
            </>
          ) : hasResult && load.phase !== "error" ? (
            <div className="grid grid-cols-3 gap-2" aria-busy="true">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-[62px]" />
              ))}
            </div>
          ) : (
            <div className="text-sm text-muted">Shown once the mesh is made.</div>
          )}
        </Panel>

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
                    <span className="flex-1 text-right font-mono text-[15px] font-semibold text-fg tnum">{d.toFixed(2)} mm</span>
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
                title="Remove all fits"
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

        <Panel title="Coordinate system" icon={FRAME_ICON}>
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

        {hasResult ? (
          <Panel title="Check" icon={CHECK_ICON}>
            <CheckPanel
              check={check}
              ready={!!geometry}
              hasSigma={hasSigma}
              remake={project && scan ? <MakeMeshButton projectId={project.id} scan={scan} label="Make the mesh again" /> : undefined}
              holes={
                openHoles
                  ? {
                      count: openHoles,
                      action: (
                        <Button size="xs" icon={SlidersHorizontal} onClick={() => navigate(paths.process(pid, sid))}>
                          See them in Process
                        </Button>
                      ),
                    }
                  : null
              }
            />
          </Panel>
        ) : null}

        {hasResult && project && scan ? (
          <Panel title="Fine-tune" icon={SlidersHorizontal}>
            <p className="text-sm text-muted">Remove floating bits, fill holes, smooth or simplify, cut away what you don't need. Every step can be undone.</p>
            <ProcessButton projectId={project.id} scan={scan} variant="primary" className="mt-3" label="Fine-tune in Process" />
          </Panel>
        ) : null}

        <Panel title="Download" icon={Download}>
          {hasResult && urls ? (
            <div className="grid grid-cols-2 gap-2">
              <Button size="sm" icon={Download} onClick={() => void downloadFile(urls.stl, `${stem}.stl`)}>
                STL
              </Button>
              <Button size="sm" icon={Download} onClick={() => void downloadFile(urls.mesh, `${stem}.ply`)}>
                PLY
              </Button>
              {urls.laser ? (
                <Button size="sm" icon={Download} onClick={() => void downloadFile(urls.laser!, `${stem}-laser.ply`)}>
                  Laser points
                </Button>
              ) : null}
              <Button size="sm" icon={FileText} onClick={() => void downloadFile(urls.report, `${stem}-report.json`)}>
                Report
              </Button>
              {partFrame ? (
                <>
                  <Button size="sm" className="col-span-2" icon={Download} onClick={() => void downloadFile(api.frameMeshUrl(pid, sid, "mesh.stl"), `${stem}-part-frame.stl`)} title="The mesh in the part's coordinate system">
                    STL in the part's frame
                  </Button>
                  <Button size="sm" className="col-span-2" icon={Download} onClick={() => void downloadFile(api.frameMeshUrl(pid, sid, "mesh.ply"), `${stem}-part-frame.ply`)} title="The mesh in the part's coordinate system">
                    PLY in the part's frame
                  </Button>
                </>
              ) : null}
            </div>
          ) : (
            <div className="text-sm text-muted">Make the mesh first.</div>
          )}
          {hasResult && urls ? <CadDownload pid={pid} sid={sid} stem={stem} kept={kept} hasMesh={hasResult} /> : null}
          {hasResult && project && scan ? (
            <div className="mt-3 flex items-center justify-between gap-2">
              <span className="text-xs text-subtle">
                Units: mm. STL for CAD and slicers.
                {partFrame ? ` STL and PLY are in the scan's frame unless "in the part's frame".${partFrame.out_of_date ? " The coordinate system is out of date." : ""}` : ""}
              </span>
              <MakeMeshButton projectId={project.id} scan={scan} label="Remake" variant="ghost" size="xs" />
            </div>
          ) : null}
        </Panel>

        <Panel title="Report" icon={FileText}>
          {report ? (
            <ReportSummary report={report} />
          ) : hasResult ? (
            <div className="text-sm text-muted">No processing report for this result.</div>
          ) : (
            <div className="text-sm text-muted">Shown after meshing.</div>
          )}
        </Panel>

        <div className={cn("space-y-1.5 px-4 py-3 text-[11px] leading-5 text-subtle")}>
          <div>
            <Eye size={12} className="mr-1 inline" aria-hidden />
            Left drag orbits, right drag pans, the wheel zooms towards the cursor.
          </div>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span>
              <Kbd>F</Kbd> frame
            </span>
            <span>
              <Kbd>M</Kbd> measure
            </span>
            <span>
              <Kbd>G</Kbd> fit (<Kbd>P</Kbd> <Kbd>C</Kbd> <Kbd>H</Kbd>)
            </span>
            <span>
              <Kbd>W</Kbd> wireframe
            </span>
            <span>
              <Kbd>L</Kbd> laser points
            </span>
            <span>
              {devEnabled && hasSigma ? (
                <>
                  <Kbd>1</Kbd>–<Kbd>6</Kbd>
                </>
              ) : devEnabled ? (
                <>
                  <Kbd>1</Kbd>–<Kbd>3</Kbd>, <Kbd>5</Kbd>, <Kbd>6</Kbd>
                </>
              ) : (
                <>
                  <Kbd>1</Kbd>–<Kbd>{hasSigma ? 4 : 3}</Kbd>, <Kbd>6</Kbd>
                </>
              )}{" "}
              shading
            </span>
          </div>
          {tris > 0 ? <div className="sr-only">{fmtCompact(tris)} triangles</div> : null}
        </div>
      </aside>
    </div>
  );
}
