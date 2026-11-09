/** Right-hand panel of the Scan page: camera previews, setup before a scan, live stats during one. */
import {
  Camera,
  Check,
  ChevronDown,
  Crosshair,
  Cpu,
  FlaskConical,
  FolderOpen,
  FolderPlus,
  ImageOff,
  Lightbulb,
  ScanLine,
  SlidersHorizontal,
  Tag,
  Waypoints,
  TriangleExclamationPoint,
} from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import type { ScanMode } from "../../api/types";
import { EmitterSliders } from "../../components/EmitterSliders";
import { Button } from "../../components/ui/Button";
import { PromptDialog } from "../../components/ui/Dialog";
import { Field, Segmented, Select, TextInput } from "../../components/ui/Form";
import { fmtClock, fmtCompact, fmtFixed, fmtInt } from "../../lib/format";
import { describeTracking } from "../../lib/tracking";
import { navigate, paths } from "../../lib/router";
import { cn, useTicker } from "../../lib/util";
import { SOURCE_LABEL, useDevice } from "../../store/device";
import { useLive } from "../../store/live";
import { useProjects } from "../../store/projects";
import { isLive, useSession } from "../../store/session";
import { useUi } from "../../store/ui";

function PanelSection({
  title,
  icon: Icon,
  children,
  aside,
  collapsible,
  open = true,
  onToggle,
}: {
  title: string;
  icon: typeof Camera;
  children: ReactNode;
  aside?: ReactNode;
  collapsible?: boolean;
  open?: boolean;
  onToggle?: () => void;
}) {
  const id = useId();
  return (
    <section className="border-b border-line" aria-labelledby={id}>
      <div className="flex h-11 items-center gap-2 px-4">
        <Icon size={14} className="text-muted" aria-hidden />
        {collapsible ? (
          <button
            type="button"
            id={id}
            onClick={onToggle}
            aria-expanded={open}
            className="flex flex-1 items-center gap-1.5 text-left text-xs font-semibold uppercase tracking-wider text-muted hover:text-fg"
          >
            {title}
            <ChevronDown size={13} className={cn("transition-transform", !open && "-rotate-90")} aria-hidden />
          </button>
        ) : (
          <h2 id={id} className="flex-1 text-xs font-semibold uppercase tracking-wider text-muted">
            {title}
          </h2>
        )}
        {aside}
      </div>
      {open ? <div className="px-4 pb-4">{children}</div> : null}
    </section>
  );
}

// ------------------------------------------------------------------ camera previews
export function CameraPreviews() {
  const open = useUi((s) => s.previewsOpen);
  const setOpen = useUi((s) => s.setPreviewsOpen);
  const left = useLive((s) => s.previews.left);
  const depth = useLive((s) => s.previews.depth);
  const connected = useDevice((s) => !!s.device?.connected);
  const [swap, setSwap] = useState(false);
  useTicker(1000);
  const main = swap ? depth : left;
  const inset = swap ? left : depth;
  const stale = !!main && performance.now() - main.at > 2500;
  const mainLabel = swap ? "Depth" : "Left · lasers";
  const insetLabel = swap ? "left camera" : "depth";

  return (
    <PanelSection
      title="Cameras"
      icon={Camera}
      collapsible
      open={open}
      onToggle={() => setOpen(!open)}
      aside={main ? <span className="font-mono text-[10.5px] text-subtle tnum">{`${main.w}×${main.h}`}</span> : null}
    >
      <div className="relative aspect-[16/10] w-full overflow-hidden rounded-lg border border-line bg-black">
        {main ? (
          <img src={main.url} alt={swap ? "Colourised depth map" : "Rectified left camera with the laser overlay"} className="absolute inset-0 h-full w-full object-contain" draggable={false} />
        ) : (
          <div className="absolute inset-0 grid place-items-center text-center">
            <div className="flex flex-col items-center gap-1.5 px-6 text-xs text-white/50">
              <ImageOff size={20} aria-hidden />
              {connected ? "Waiting for camera frames…" : "No camera: connect a device"}
            </div>
          </div>
        )}
        {main ? (
          <span className="absolute left-2 top-2 rounded bg-black/55 px-1.5 py-0.5 text-[10.5px] font-medium text-white/85">{mainLabel}</span>
        ) : null}
        {stale ? (
          <span className="absolute right-2 top-2 rounded bg-warn/85 px-1.5 py-0.5 text-[10.5px] font-semibold text-black">No new frames</span>
        ) : null}
        {inset ? (
          <button
            type="button"
            onClick={() => setSwap((s) => !s)}
            className="absolute bottom-2 right-2 aspect-[16/10] w-[36%] overflow-hidden rounded-md border border-white/25 bg-black shadow-lg transition-transform hover:scale-[1.03]"
            aria-label={`Swap: show the ${insetLabel} large`}
            title={`Show the ${insetLabel} large`}
          >
            <img src={inset.url} alt="" className="h-full w-full object-contain" draggable={false} />
          </button>
        ) : null}
      </div>
    </PanelSection>
  );
}

// ------------------------------------------------------------------ setup (before a scan)
function Check1({ ok, warn, children, action }: { ok: boolean; warn?: boolean; children: ReactNode; action?: ReactNode }) {
  return (
    <li className="flex items-start gap-2.5">
      <span
        className={cn(
          "mt-0.5 grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full",
          ok ? "bg-ok-soft text-ok" : warn ? "bg-warn-soft text-warn" : "bg-surface-3 text-subtle",
        )}
      >
        {ok ? <Check size={11} strokeWidth={3} aria-hidden /> : <TriangleExclamationPoint size={10} strokeWidth={2.6} aria-hidden />}
      </span>
      <div className="min-w-0 flex-1 text-sm text-fg">
        {children}
        {action ? <div className="mt-1.5">{action}</div> : null}
      </div>
    </li>
  );
}

export function ScanSetup({
  projectId,
  setProjectId,
  name,
  setName,
  mode,
  setMode,
}: {
  projectId: string | null;
  setProjectId: (id: string | null) => void;
  name: string;
  setName: (v: string) => void;
  mode: ScanMode;
  setMode: (m: ScanMode) => void;
}) {
  const device = useDevice((s) => s.device);
  const connect = useDevice((s) => s.connect);
  const pending = useDevice((s) => s.pending);
  const busy = useDevice((s) => s.device?.busy ?? (s.simJobId ? "Generating simulator demo…" : null));
  const list = useProjects((s) => s.list);
  const create = useProjects((s) => s.create);
  const project = useProjects((s) => (projectId ? s.details[projectId] : undefined));
  const [creating, setCreating] = useState(false);
  const connected = !!device?.connected;
  const calibrated = !!device?.calibration?.loaded;
  const nameId = useId();
  const projId = useId();

  return (
    <PanelSection title="Scan setup" icon={SlidersHorizontal}>
      <ul className="mb-4 space-y-2.5 rounded-lg border border-line bg-surface-2/60 p-3" aria-label="Ready to scan">
        <Check1
          ok={connected}
          warn={!connected}
          action={
            !connected ? (
              <div className="flex flex-wrap gap-1.5">
                <Button size="xs" variant="primary" icon={FlaskConical} loading={pending === "connect" || !!busy} onClick={() => void connect({ source: "sim" })}>
                  {busy ? "Preparing demo…" : "Use the simulator"}
                </Button>
                <Button size="xs" icon={Cpu} onClick={() => navigate(paths.device())}>
                  Device page
                </Button>
              </div>
            ) : null
          }
        >
          {connected ? (
            <>
              {SOURCE_LABEL[device!.source]} connected
            </>
          ) : (
            "No device connected"
          )}
        </Check1>
        <Check1
          ok={connected && calibrated}
          warn={connected && !calibrated}
          action={
            connected && !calibrated ? (
              <Button size="xs" variant="primary" icon={Crosshair} onClick={() => navigate(paths.setup("stereo"))}>
                Calibrate in Setup
              </Button>
            ) : null
          }
        >
          {!connected ? (
            <span className="text-muted">Calibration</span>
          ) : calibrated ? (
            "Calibration loaded"
          ) : (
            <>
              No calibration yet
              <div className="text-xs text-muted">The setup wizard calibrates the scanner in about 10 minutes.</div>
            </>
          )}
        </Check1>
        <Check1 ok={!!projectId} warn={!projectId}>
          {projectId ? (project?.name ?? list?.find((p) => p.id === projectId)?.name ?? "Project selected") : "Choose a project below"}
        </Check1>
      </ul>

      <div className="space-y-4">
        <Field
          label="Project"
          htmlFor={projId}
          aside={
            <button type="button" className="inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline" onClick={() => setCreating(true)}>
              <FolderPlus size={12} aria-hidden /> New
            </button>
          }
        >
          {list && list.length ? (
            <Select id={projId} value={projectId ?? ""} onChange={(e) => setProjectId(e.target.value || null)}>
              {!projectId ? <option value="">Choose a project…</option> : null}
              {list.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
          ) : (
            <Button className="w-full" icon={FolderPlus} onClick={() => setCreating(true)}>
              Create a project
            </Button>
          )}
        </Field>
        <Field
          label="Tracking mode"
          help={
            mode === "markerless"
              ? "Tracks the shape itself. Best for parts with curves and edges."
              : "AprilTag stickers keep big flat panels drift-free. Stick 2+ tags in view."
          }
        >
          <Segmented<ScanMode>
            label="Tracking mode"
            value={mode}
            onChange={setMode}
            className="w-full"
            options={[
              { value: "markerless", label: "Markerless", icon: Waypoints },
              { value: "markers", label: "Markers", icon: Tag },
            ]}
          />
        </Field>
        <Field label="Scan name" htmlFor={nameId} help="Optional. Shown in the project's scan list.">
          <TextInput
            id={nameId}
            value={name}
            maxLength={80}
            placeholder={`Scan ${(project?.scans.length ?? 0) + 1}`}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <div className="flex gap-2 rounded-lg border border-line bg-surface-2/60 px-3 py-2.5 text-xs text-muted">
          <Lightbulb size={14} className="mt-px shrink-0 text-warn" aria-hidden />
          <span>
            Hold the scanner <b className="text-fg">200–350 mm</b> from the surface and sweep slowly, overlapping what you already
            scanned. The trigger button starts and pauses too.
          </span>
        </div>
      </div>

      <PromptDialog
        open={creating}
        onClose={() => setCreating(false)}
        title="New project"
        description="Scans you start go into this project."
        label="Project name"
        placeholder="e.g. Intake bracket"
        confirmLabel="Create project"
        onSubmit={async (v) => {
          const p = await create(v);
          if (!p) return false;
          setProjectId(p.id);
          return true;
        }}
      />
    </PanelSection>
  );
}

// ------------------------------------------------------------------ live stats
function Stat({ label, value, unit, tone }: { label: string; value: string; unit?: string; tone?: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface-2/60 px-3 py-2">
      <div className="text-[10.5px] font-medium uppercase tracking-wider text-subtle">{label}</div>
      <div className={cn("mt-0.5 truncate font-mono text-[15px] font-semibold text-fg tnum", tone)}>
        {value}
        {unit ? <span className="ml-1 text-[11px] font-normal text-subtle">{unit}</span> : null}
      </div>
    </div>
  );
}

export function LiveStats() {
  const status = useSession((s) => s.status);
  const session = useSession((s) => s.session);
  const project = useProjects((s) => (session?.project_id ? (s.details[session.project_id] ?? s.list?.find((p) => p.id === session.project_id)) : undefined));
  const device = useDevice((s) => s.device);
  const live = isLive(session);
  const tracking = status?.tracking;
  const info = tracking ? describeTracking(tracking.state, tracking.reason) : null;
  const fpsLow = !!status && status.fps.pipeline > 0 && status.fps.pipeline < 8;

  return (
    <PanelSection title={live ? "Live" : "Status"} icon={ScanLine}>
      {live ? (
        <div className="mb-3 flex items-center gap-2 text-xs text-muted">
          <FolderOpen size={13} aria-hidden />
          <span className="truncate text-fg">{project?.name ?? "Project"}</span>
          <span aria-hidden>·</span>
          <span>{session?.mode === "markers" ? "Markers" : "Markerless"}</span>
        </div>
      ) : null}
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Elapsed" value={fmtClock(session?.elapsed_s ?? 0)} />
        <Stat label="Frames" value={fmtInt(session?.frames ?? 0)} />
        <Stat label="Camera" value={status ? fmtFixed(status.fps.camera, 1) : "–"} unit="fps" />
        <Stat label="Pipeline" value={status ? fmtFixed(status.fps.pipeline, 1) : "–"} unit="fps" tone={fpsLow ? "text-warn" : undefined} />
        <Stat label="Laser points" value={status ? fmtCompact(status.laser_points) : "–"} />
        <Stat label="Model" value={status ? fmtCompact(status.model.triangles) : "–"} unit="tris" />
      </div>
      {live && info ? (
        <div className="mt-3 rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-subtle">Tracking</span>
            <span
              className={cn(
                "font-medium",
                tracking?.state === "ok" ? "text-ok" : tracking?.state === "weak" ? "text-warn" : tracking?.state === "lost" ? "text-danger" : "text-muted",
              )}
            >
              {info.label}
            </span>
          </div>
          <div className="mt-1 flex items-center justify-between text-muted">
            <span>Fitness / RMSE</span>
            <span className="font-mono tnum">
              {tracking?.fitness !== null && tracking?.fitness !== undefined ? tracking.fitness.toFixed(2) : "–"} /{" "}
              {tracking?.rmse_mm !== null && tracking?.rmse_mm !== undefined ? `${tracking.rmse_mm.toFixed(2)} mm` : "–"}
            </span>
          </div>
        </div>
      ) : null}
      {status?.emitters && device?.source === "camera" ? (
        <div className="mt-3 flex items-center justify-between rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-xs text-muted">
          <span>Emitters V / H / dots</span>
          <span className="font-mono text-fg tnum">
            {Math.round(status.emitters.vertical)} / {Math.round(status.emitters.horizontal)} / {Math.round(status.emitters.dots)} %
          </span>
        </div>
      ) : null}
      {fpsLow && live ? (
        <div className="mt-3 flex gap-2 text-xs text-warn">
          <TriangleExclamationPoint size={13} className="mt-px shrink-0" aria-hidden />
          The live pipeline is slow on this computer. Move slower; the recording still keeps every frame.
        </div>
      ) : null}
    </PanelSection>
  );
}

export function EmittersPanel() {
  const device = useDevice((s) => s.device);
  const [open, setOpen] = useState(false);
  if (!device || device.source !== "camera" || !device.pico?.connected) return null;
  return (
    <PanelSection title="Emitters" icon={SlidersHorizontal} collapsible open={open} onToggle={() => setOpen(!open)}>
      <EmitterSliders device={device} compact />
    </PanelSection>
  );
}
