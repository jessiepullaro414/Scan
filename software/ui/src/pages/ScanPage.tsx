/**
 * The live scan screen: a big 3D view of the growing model with the scanner at its current
 * pose, the tracking badge, distance gauge and recording chip on top, camera previews and
 * stats on the right, and the scan controls at the bottom (keyboard: Space, R, Esc twice, F, ?; the sheet in
 * lib/shortcuts.ts). Esc stops only when pressed twice within a second (UX_PLAN U45): it means "dismiss" too.
 *
 * A second pass (FEATURE_PLAN N2, `continues` in the address, set by the Check panel's "Scan these areas"):
 * the side panel says which scan the pass belongs to and shows that scan for reference with the areas lit
 * (pages/scan/PassPanel.tsx); Start sends the link; after Stop the engine's steps take the "Make mesh" card's place.
 */
import { useEffect, useRef, useState } from "react";
import { focusMovedQuestion } from "../components/MovedQuestion";
import { ShortcutSheet } from "../components/ShortcutSheet";
import { Kbd } from "../components/ui/Feedback";
import { ESC_AGAIN_TEXT, ESC_WINDOW_MS, escPress } from "../lib/escStop";
import { movedNeeded } from "../lib/moved";
import { projectForScan } from "../lib/scanProject";
import { SHEETS } from "../lib/shortcuts";
import { CalibHealthCallout } from "../components/CalibHealth";
import { PresetPicker } from "../components/PresetPicker";
import { Callout } from "../components/ui/Layout";
import { calibOpen, scanReason } from "../lib/calibOpen";
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
import { PASS_MOVED_ID, PassSetup } from "./scan/PassPanel";
import { CameraPreviews, EmittersPanel, LiveStats, ScanSetup } from "./scan/SidePanel";

export default function ScanPage({
  projectParam,
  first = false,
  continues = null,
  choose = false,
}: {
  projectParam: string | null;
  first?: boolean;
  continues?: string | null;
  /** U44: Home's "New scan": the project picker focused, "Choose the project for this scan". */
  choose?: boolean;
}) {
  const list = useProjects((s) => s.list);
  const fetchList = useProjects((s) => s.fetchList);
  const fetchProject = useProjects((s) => s.fetchProject);
  const current = useUi((s) => s.currentProjectId);
  const setCurrent = useUi((s) => s.setCurrentProject);
  const mode = useUi((s) => s.scanMode);
  const setMode = useUi((s) => s.setScanMode);
  // Only the phase: the whole session changes ten times a second while scanning.
  const phase = useSession((s) => s.session?.state ?? "idle");
  const lastStopped = useSession((s) => s.lastStopped);
  const device = useDevice((s) => s.device);
  // N2: the pass "Scan these areas" set up, while it is for this project; while scanning, the session says which
  const pending = usePass((s) => s.pending);
  const sessionLink = useSession((s) => s.session?.continues?.scan_id ?? null);
  const calibBusy = useCalib((s) => s.action === "cancel");
  const presetName = usePresets((s) => s.list?.find((p) => p.id === s.chosen)?.name ?? "General");

  const [projectId, setProjectId] = useState<string | null>(() => projectForScan({ list: useProjects.getState().list, current, param: projectParam, choose }));
  /** U25: the shortcut sheet ("?"). */
  const [sheetOpen, setSheetOpen] = useState(false);
  /** U45: the time of an Esc that asked "Press Esc again to stop" (null: none). */
  const [escArmed, setEscArmed] = useState<number | null>(null);
  const escRef = useRef<number | null>(null);
  const passMoved = usePass((s) => s.moved);
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
    setProjectId(projectForScan({ list, current, param: null, choose }));
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
  // R9: an open session before "no calibration" (with the simulator an open session unloads its calibration)
  const { reason, openAction } = scanReason({
    deviceKnown: !!device,
    connected,
    calibrated,
    open,
    projectChosen: !!projectId,
    passReason: passPending ? movedNeeded(passMoved, "pass") : null, // U15: a pass waits for the moved-part answer
  });
  const reasonAction =
    openAction && open
      ? {
          label: open.label,
          busy: calibBusy,
          onClick: () => {
            if (open.action === "close") void useCalib.getState().cancel();
            else navigate(paths.setup(open.step));
          },
        }
      : connected && calibrated && projectId && passPending && passMoved === null
        ? { label: "Answer it", onClick: () => focusMovedQuestion(PASS_MOVED_ID) }
        : null;
  const ready = !reason && phase === "idle";

  const start = async () => {
    if (!ready || !projectId) return;
    const moved = usePass.getState().moved;
    if (passPending && moved === null) return; // U15: no default; the reason says so, with "Answer it"
    const link = passPending
      ? { continues: passStartBody(passPending, moved === true), presetFields: passPresetFields(usePresets.getState().startFields(), passPending.preset) }
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
    // U45: Esc twice within a second stops and saves; the first press only asks
    escape: () => {
      if (!isLive(useSession.getState().session)) return;
      const r = escPress(escRef.current, performance.now());
      escRef.current = r.armedAt;
      setEscArmed(r.armedAt);
      if (r.stop) void useSession.getState().stop();
    },
    f: () => viewBus.emit({ type: "frame", animate: true }),
    "?": () => setSheetOpen(true),
  });
  useEffect(() => {
    if (escArmed === null) return;
    const t = setTimeout(() => {
      escRef.current = null;
      setEscArmed(null);
    }, ESC_WINDOW_MS);
    return () => clearTimeout(t);
  }, [escArmed]);
  useEffect(() => {
    if (!live) {
      escRef.current = null;
      setEscArmed(null);
    }
    // U44: a scan started (Space, the trigger) settles the choice: the address no longer asks
    else if (choose) navigate(paths.scan(projectId), { replace: true });
  }, [live]); // eslint-disable-line react-hooks/exhaustive-deps

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
            <ShortcutHints compact={narrow && (!!lastStopped || live)} onSheet={() => setSheetOpen(true)} />
          </div>
          <div className="absolute bottom-5 left-1/2 flex -translate-x-1/2 flex-col items-center gap-3">
            <LostBanner />
            {escArmed !== null && live ? (
              <div role="status" className="glass flex animate-pop-in items-center gap-2 rounded-full border border-warn/50 px-3.5 py-1.5 text-sm text-fg shadow-md">
                <Kbd>Esc</Kbd> {ESC_AGAIN_TEXT}
              </div>
            ) : null}
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
              A second pass starts from the first scan's Scan check: open the scan, press Check the scan, then Scan these areas again. Start now makes an ordinary scan.
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
            <ScanSetup projectId={projectId} setProjectId={setProjectId} name={name} setName={setName} mode={mode} setMode={setMode} choose={choose && !live} onChosen={(id) => navigate(paths.scan(id), { replace: true })} />
            <PresetPicker mode={mode} setMode={setMode} />
            {connected ? <EmittersPanel /> : null}
          </>
        )}
      </aside>
      <ShortcutSheet open={sheetOpen} onClose={() => setSheetOpen(false)} sheet={SHEETS.scan} />
    </div>
  );
}
