/**
 * The live scan screen: a big 3D view of the growing model with the scanner at its current
 * pose, the tracking badge, distance gauge and recording chip on top, camera previews and
 * stats on the right, and the scan controls at the bottom (keyboard: Space, R, Esc, F, ?).
 */
import { useEffect, useRef, useState } from "react";
import { ErrorBoundary } from "../components/shell/ErrorBoundary";
import { useHotkeys } from "../lib/util";
import { useDevice } from "../store/device";
import { useProjects } from "../store/projects";
import { isLive, useSession } from "../store/session";
import { useUi } from "../store/ui";
import { viewBus } from "../three/viewBus";
import { ControlBar } from "./scan/Controls";
import { LiveCanvas } from "./scan/LiveCanvas";
import {
  DistanceGauge,
  LostBanner,
  NoDeviceOverlay,
  RecChip,
  ShortcutHints,
  TrackingBadge,
  ViewToolbar,
} from "./scan/Overlays";
import { CameraPreviews, EmittersPanel, LiveStats, ScanSetup } from "./scan/SidePanel";

export default function ScanPage({ projectParam }: { projectParam: string | null }) {
  const list = useProjects((s) => s.list);
  const fetchList = useProjects((s) => s.fetchList);
  const fetchProject = useProjects((s) => s.fetchProject);
  const current = useUi((s) => s.currentProjectId);
  const setCurrent = useUi((s) => s.setCurrentProject);
  const mode = useUi((s) => s.scanMode);
  const setMode = useUi((s) => s.setScanMode);
  const hintsOpen = useUi((s) => s.hintsOpen);
  const setHintsOpen = useUi((s) => s.setHintsOpen);
  const session = useSession((s) => s.session);
  const lastStopped = useSession((s) => s.lastStopped);
  const device = useDevice((s) => s.device);

  const [projectId, setProjectId] = useState<string | null>(projectParam ?? current);
  const [name, setName] = useState("");
  const [showGrid, setShowGrid] = useState(true);
  const [showTrail, setShowTrail] = useState(true);
  const viewport = useRef<HTMLDivElement>(null);
  const [narrow, setNarrow] = useState(false);

  useEffect(() => {
    if (!list) void fetchList();
  }, [list, fetchList]);

  useEffect(() => {
    if (projectParam) setProjectId(projectParam);
  }, [projectParam]);

  // Keep the selection valid once the project list is known.
  useEffect(() => {
    if (!list) return;
    if (projectId && list.some((p) => p.id === projectId)) return;
    setProjectId(current && list.some((p) => p.id === current) ? current : (list[0]?.id ?? null));
  }, [list]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!projectId) return;
    setCurrent(projectId);
    void fetchProject(projectId);
  }, [projectId, setCurrent, fetchProject]);

  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setNarrow(e.contentRect.width < 1040));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const live = isLive(session);
  const phase = session?.state ?? "idle";
  const connected = !!device?.connected;
  const calibrated = !!device?.calibration?.loaded;
  const reason = !device
    ? "Checking the device…"
    : !connected
      ? "Connect a device to start"
      : !calibrated
        ? "No calibration: run ohs calibrate, then reconnect"
        : !projectId
          ? "Choose a project to start"
          : null;
  const ready = !reason && phase === "idle";

  const start = async () => {
    if (!ready || !projectId) return;
    const ok = await useSession.getState().start({ projectId, mode, name });
    if (ok) setName("");
  };

  useHotkeys({
    space: () => {
      const st = useSession.getState();
      const ph = st.session?.state ?? "idle";
      if (ph === "scanning") void st.pause();
      else if (ph === "paused") void st.resume();
      else if (ph === "idle" && !st.lastStopped) void start();
    },
    r: () => {
      if (isLive(useSession.getState().session)) void useSession.getState().rewind(2);
    },
    escape: () => {
      if (isLive(useSession.getState().session)) void useSession.getState().stop();
    },
    f: () => viewBus.emit({ type: "frame", animate: true }),
    "?": () => setHintsOpen(!hintsOpen),
  });

  return (
    <div className="flex h-full">
      <div ref={viewport} className="relative min-w-0 flex-1 overflow-hidden viewport-bg">
        <ErrorBoundary title="The 3D view couldn't start">
          <LiveCanvas showGrid={showGrid} showTrail={showTrail} />
        </ErrorBoundary>

        <div className="pointer-events-none absolute inset-0">
          <div className="absolute left-4 top-4">
            <TrackingBadge />
          </div>
          <div className="absolute left-1/2 top-4 -translate-x-1/2">
            <RecChip />
          </div>
          <div className="pointer-events-auto absolute right-4 top-4">
            <ViewToolbar showGrid={showGrid} setShowGrid={setShowGrid} showTrail={showTrail} setShowTrail={setShowTrail} />
          </div>
          <div className="absolute right-4 top-1/2 -translate-y-1/2">
            <DistanceGauge />
          </div>
          <div className="absolute inset-0 grid place-items-center">
            <NoDeviceOverlay />
          </div>
          <div className="pointer-events-auto absolute bottom-4 left-4">
            <ShortcutHints compact={narrow && (!!lastStopped || live)} />
          </div>
          <div className="absolute bottom-5 left-1/2 flex -translate-x-1/2 flex-col items-center gap-3">
            <LostBanner />
            <div className="pointer-events-auto">
              <ControlBar ready={ready} reason={reason} onStart={() => void start()} />
            </div>
          </div>
        </div>
      </div>

      <aside
        className="flex w-[320px] shrink-0 flex-col overflow-y-auto border-l border-line bg-panel 2xl:w-[360px] min-[1900px]:w-[420px]"
        aria-label="Scan panel"
      >
        <CameraPreviews />
        {live ? (
          <>
            <LiveStats />
            <EmittersPanel />
          </>
        ) : (
          <>
            <ScanSetup projectId={projectId} setProjectId={setProjectId} name={name} setName={setName} mode={mode} setMode={setMode} />
            {connected ? <EmittersPanel /> : null}
          </>
        )}
      </aside>
    </div>
  );
}
