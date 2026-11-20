/**
 * The live scan screen: a big 3D view of the growing model with the scanner at its current
 * pose, the tracking badge, distance gauge and recording chip on top, camera previews and
 * stats on the right, and the scan controls at the bottom (keyboard: Space, R, Esc, F, ?).
 *
 * A second pass (FEATURE_PLAN N2, `continues` in the address, set by the Check panel's "Scan these areas"):
 * the side panel says which scan the pass belongs to and shows that scan for reference with the areas lit
 * (pages/scan/PassPanel.tsx); Start sends the link; after Stop the engine's steps take the "Make mesh" card's place.
 */
import { useEffect, useRef, useState } from "react";
import { CalibHealthCallout } from "../components/CalibHealth";
import { PresetPicker } from "../components/PresetPicker";
import { Callout } from "../components/ui/Layout";
import { calibOpen } from "../lib/calibOpen";
import { passPresetFields, passStartBody } from "../lib/pass";
import { useCalib } from "../store/calib";
import { ErrorBoundary } from "../components/shell/ErrorBoundary";
import { navigate, paths } from "../lib/router";
import { useHotkeys } from "../lib/util";
import { useDevice } from "../store/device";
import { usePass } from "../store/pass";
import { usePresets } from "../store/presets";
import { useProjects } from "../store/projects";
import { isLive, useSession } from "../store/session";
import { useUi } from "../store/ui";
import { viewBus } from "../three/viewBus";
import { ControlBar } from "./scan/Controls";
import { LiveCanvas } from "./scan/LiveCanvas";
import {
  DistanceGauge,
  FirstScanCoach,
  LostBanner,
  NoDeviceOverlay,
  RecChip,
  ShortcutHints,
  TrackingBadge,
  ViewToolbar,
  WarnChips,
} from "./scan/Overlays";
import { PassSetup } from "./scan/PassPanel";
import { CameraPreviews, EmittersPanel, LiveStats, ScanSetup } from "./scan/SidePanel";

export default function ScanPage({ projectParam, first = false, continues = null }: { projectParam: string | null; first?: boolean; continues?: string | null }) {
  const list = useProjects((s) => s.list);
  const fetchList = useProjects((s) => s.fetchList);
  const fetchProject = useProjects((s) => s.fetchProject);
  const current = useUi((s) => s.currentProjectId);
  const setCurrent = useUi((s) => s.setCurrentProject);
  const mode = useUi((s) => s.scanMode);
  const setMode = useUi((s) => s.setScanMode);
  const hintsOpen = useUi((s) => s.hintsOpen);
  const setHintsOpen = useUi((s) => s.setHintsOpen);
  // Only the phase: the whole session changes ten times a second while scanning.
  const phase = useSession((s) => s.session?.state ?? "idle");
  const lastStopped = useSession((s) => s.lastStopped);
  const device = useDevice((s) => s.device);
  // N2: the pass "Scan these areas" set up, while it is for this project; while scanning, the session says which
  const pending = usePass((s) => s.pending);
  const sessionLink = useSession((s) => s.session?.continues?.scan_id ?? null);
  const calibBusy = useCalib((s) => s.action === "cancel");
  const presetName = usePresets((s) => s.list?.find((p) => p.id === s.chosen)?.name ?? "General");

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

  const live = phase === "scanning" || phase === "paused";
  const passPending = pending && continues && pending.firstId === continues && pending.projectId === projectId ? pending : null;
  const pass = live ? (pending && sessionLink === pending.firstId ? pending : null) : passPending;
  const firstScan = useProjects((s) => (pass ? s.details[pass.projectId]?.scans.find((x) => x.id === pass.firstId) : undefined));
  // the pass scans as the first scan was scanned, unless the user changes it; the card of an earlier scan makes way
  useEffect(() => {
    if (passPending && firstScan && firstScan.mode !== useUi.getState().scanMode) setMode(firstScan.mode);
    const last = useSession.getState().lastStopped;
    if (passPending && last && !last.scan.continues) useSession.getState().clearLastStopped();
  }, [passPending?.firstId, firstScan?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const connected = !!device?.connected;
  const calibrated = !!device?.calibration?.loaded;
  // U1: an open calibration session blocks a scan (the engine refuses it): say so, with a way out
  const calibSession = useCalib((s) => s.session);
  const open = calibOpen(calibSession);
  const reason = !device
    ? "Checking the device…"
    : !connected
      ? "Connect a device to start"
      : !calibrated
        ? "No calibration yet: calibrate the scanner in Setup"
        : open
          ? open.reason
          : !projectId
            ? "Choose a project to start"
            : null;
  const reasonAction =
    connected && calibrated && open
      ? {
          label: open.label,
          busy: calibBusy,
          onClick: () => {
            if (open.action === "close") void useCalib.getState().cancel();
            else navigate(paths.setup(open.step));
          },
        }
      : null;
  const ready = !reason && phase === "idle";

  const start = async () => {
    if (!ready || !projectId) return;
    const link = passPending
      ? { continues: passStartBody(passPending, usePass.getState().moved), presetFields: passPresetFields(usePresets.getState().startFields(), passPending.preset) }
      : {};
    const ok = await useSession.getState().start({ projectId, mode, name, ...link });
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
          <div className="absolute left-1/2 top-4 flex w-[min(560px,calc(100%-2rem))] -translate-x-1/2 flex-col items-center gap-3">
            <div className="flex flex-wrap items-center justify-center gap-2">
              <RecChip />
              <WarnChips />
            </div>
            {!live ? (
              <div className="pointer-events-auto w-full">
                <CalibHealthCallout glass />
              </div>
            ) : null}
            {first && !live && connected ? (
              <div className="pointer-events-auto">
                <FirstScanCoach onDismiss={() => navigate(paths.scan(projectId), { replace: true })} />
              </div>
            ) : null}
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
              <ControlBar ready={ready} reason={reason} reasonAction={reasonAction} onStart={() => void start()} />
            </div>
          </div>
        </div>
      </div>

      <aside
        className="flex w-[320px] shrink-0 flex-col overflow-y-auto border-l border-line bg-panel 2xl:w-[360px] min-[1900px]:w-[420px]"
        aria-label="Scan panel"
      >
        <CameraPreviews />
        {pass ? <PassSetup pending={pass} live={live} firstScan={firstScan} presetName={presetName} /> : null}
        {!pass && continues && !live && !lastStopped ? (
          <div className="border-b border-line p-3">
            <Callout tone="neutral" title="Nothing to scan again yet">
              A second pass starts from the first scan's Check panel: open the scan, press Check, then Scan these areas again. Start now makes an ordinary scan.
            </Callout>
          </div>
        ) : null}
        {live ? (
          <>
            <LiveStats />
            <EmittersPanel />
          </>
        ) : (
          <>
            <ScanSetup projectId={projectId} setProjectId={setProjectId} name={name} setName={setName} mode={mode} setMode={setMode} />
            <PresetPicker mode={mode} setMode={setMode} />
            {connected ? <EmittersPanel /> : null}
          </>
        )}
      </aside>
    </div>
  );
}
