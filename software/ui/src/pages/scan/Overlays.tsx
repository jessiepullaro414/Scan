/** HTML overlays on the live 3D view. */
import {
  ArrowDown,
  ArrowUp,
  Cpu,
  FlaskConical,
  Focus,
  Grid3x3,
  Keyboard,
  Pause,
  RotateCcw,
  Spline,
  TriangleExclamationPoint,
  CircleCheck,
  CircleOff,
  X,
} from "lucide-react";
import { useState } from "react";
import { IconButton, Button } from "../../components/ui/Button";
import { Kbd } from "../../components/ui/Feedback";
import { fmtClock, fmtInt } from "../../lib/format";
import { navigate, paths } from "../../lib/router";
import {
  describeTracking,
  DIST_MAX,
  DIST_MIN,
  distanceZone,
  SWEET_MAX,
  SWEET_MIN,
  ZONE_TEXT,
  type DistanceZone,
} from "../../lib/tracking";
import { cn, clamp } from "../../lib/util";
import { useDevice } from "../../store/device";
import { useSession } from "../../store/session";
import { useUi } from "../../store/ui";
import { viewBus } from "../../three/viewBus";

// ------------------------------------------------------------------ tracking badge
export function TrackingBadge() {
  const tracking = useSession((s) => s.status?.tracking ?? null);
  const phase = useSession((s) => s.session?.state ?? "idle");
  const live = phase === "scanning" || phase === "paused";
  if (!live) return null;
  const state = tracking?.state ?? "idle";
  const info = describeTracking(state, tracking?.reason);
  const tone =
    state === "ok"
      ? { ring: "border-ok/35", icon: <CircleCheck size={16} className="text-ok" aria-hidden />, text: "text-ok" }
      : state === "weak"
        ? { ring: "border-warn/45", icon: <TriangleExclamationPoint size={16} className="text-warn" aria-hidden />, text: "text-warn" }
        : state === "lost"
          ? { ring: "border-danger/60 animate-lost", icon: <CircleOff size={16} className="text-danger" aria-hidden />, text: "text-danger" }
          : { ring: "border-line-strong", icon: <CircleOff size={16} className="text-subtle" aria-hidden />, text: "text-muted" };
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn("glass pointer-events-none w-[300px] rounded-xl border px-3.5 py-2.5 shadow-md", tone.ring)}
    >
      <div className="flex items-center gap-2">
        {tone.icon}
        <span className={cn("text-sm font-semibold", tone.text)}>{phase === "paused" && state === "idle" ? "Paused" : info.label}</span>
        <span className="flex-1" />
        {tracking && (tracking.fitness !== null || tracking.rmse_mm !== null) ? (
          <span className="font-mono text-[11px] text-muted tnum" title="ICP fitness · inlier RMSE">
            {tracking.fitness !== null ? tracking.fitness.toFixed(2) : "–"}
            {" · "}
            {tracking.rmse_mm !== null ? `${tracking.rmse_mm.toFixed(2)} mm` : "–"}
          </span>
        ) : null}
      </div>
      {state === "weak" || state === "lost" ? (
        <div className="mt-1.5 pl-6">
          {info.text ? <div className="text-sm text-fg">{info.text}</div> : null}
          {info.hint ? <div className="text-xs text-muted">{info.hint}</div> : null}
        </div>
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------------ recording chip
export function RecChip() {
  const phase = useSession((s) => s.session?.state ?? "idle");
  const elapsed = useSession((s) => s.session?.elapsed_s ?? 0);
  const frames = useSession((s) => s.session?.frames ?? 0);
  if (phase !== "scanning" && phase !== "paused" && phase !== "stopping") return null;
  const paused = phase === "paused";
  return (
    <div
      className={cn(
        "glass pointer-events-none flex h-9 items-center gap-2.5 rounded-full border px-3.5 shadow-md",
        paused ? "border-warn/40" : "border-rec/40",
      )}
      role="status"
      aria-label={`${paused ? "Paused" : "Recording"}, ${fmtClock(elapsed)}`}
    >
      {paused ? (
        <Pause size={13} className="text-warn" fill="currentColor" aria-hidden />
      ) : (
        <span className="h-2.5 w-2.5 animate-rec rounded-full bg-rec" aria-hidden />
      )}
      <span className={cn("text-xs font-bold tracking-wider", paused ? "text-warn" : "text-rec")}>
        {phase === "stopping" ? "SAVING" : paused ? "PAUSED" : "REC"}
      </span>
      <span className="font-mono text-sm font-medium text-fg tnum">{fmtClock(elapsed)}</span>
      <span className="h-3.5 w-px bg-line-strong" aria-hidden />
      <span className="font-mono text-xs text-muted tnum">{fmtInt(frames)} frames</span>
    </div>
  );
}

export function LostBanner() {
  const lost = useSession((s) => s.status?.tracking.state === "lost");
  const live = useSession((s) => s.session?.state === "scanning");
  if (!lost || !live) return null;
  return (
    <div
      role="alert"
      className="pointer-events-none flex max-w-[min(520px,calc(100vw-2rem))] animate-pop-in items-center gap-2 rounded-full border border-danger/50 bg-[rgb(120_24_20/0.78)] px-4 py-1.5 text-sm text-white shadow-lg backdrop-blur"
    >
      <RotateCcw size={15} className="shrink-0" aria-hidden />
      <span className="truncate">
        <b>Return to the dashed scanner</b>: tracking resumes on its own.
      </span>
    </div>
  );
}

// ------------------------------------------------------------------ distance gauge
const ZONE_STYLE: Record<DistanceZone, { text: string; bg: string }> = {
  none: { text: "text-subtle", bg: "bg-subtle" },
  "too-close": { text: "text-danger", bg: "bg-danger" },
  close: { text: "text-warn", bg: "bg-warn" },
  good: { text: "text-ok", bg: "bg-ok" },
  far: { text: "text-warn", bg: "bg-warn" },
  "too-far": { text: "text-danger", bg: "bg-danger" },
};

const span = DIST_MAX - DIST_MIN;
const frac = (d: number) => (clamp(d, DIST_MIN, DIST_MAX) - DIST_MIN) / span;

/** Vertical 150–450 mm bar, far at the top; the sweet spot 200–350 mm is highlighted. */
export function DistanceGauge() {
  const d = useSession((s) => s.status?.distance_mm ?? null);
  const connected = useDevice((s) => !!s.device?.connected);
  if (!connected) return null;
  const zone = distanceZone(d);
  const st = ZONE_STYLE[zone];
  const has = zone !== "none" && d !== null;
  const f = has ? frac(d) : 0.5;
  const H = 220;
  return (
    <div
      className="glass pointer-events-none flex w-[76px] flex-col items-center rounded-2xl border border-line-strong px-2 pb-2.5 pt-2.5 shadow-md"
      role="meter"
      aria-label="Distance to the surface"
      aria-valuemin={DIST_MIN}
      aria-valuemax={DIST_MAX}
      aria-valuenow={has ? Math.round(d) : undefined}
      aria-valuetext={has ? `${Math.round(d)} mm, ${ZONE_TEXT[zone]}` : ZONE_TEXT.none}
    >
      <div className="text-[10px] font-semibold uppercase tracking-wider text-subtle">Distance</div>
      <div className={cn("mt-0.5 font-mono text-lg font-semibold leading-6 tnum", st.text)}>{has ? Math.round(d) : "–"}</div>
      <div className="-mt-0.5 mb-2 text-[10px] text-subtle">mm</div>
      <div className="relative w-[60px]" style={{ height: H }}>
        {/* scale labels */}
        {[450, 350, 200, 150].map((v) => (
          <span
            key={v}
            className="absolute left-0 w-[26px] -translate-y-1/2 text-right font-mono text-[9.5px] leading-none text-subtle tnum"
            style={{ top: (1 - frac(v)) * H }}
          >
            {v}
          </span>
        ))}
        {/* track: amber margins, green sweet spot */}
        <div className="absolute left-[32px] top-0 h-full w-3 overflow-hidden rounded-full bg-surface-3">
          <div className="absolute inset-x-0 bg-warn/25" style={{ top: 0, height: (1 - frac(SWEET_MAX)) * H }} />
          <div
            className="absolute inset-x-0 bg-ok/45"
            style={{ top: (1 - frac(SWEET_MAX)) * H, height: (frac(SWEET_MAX) - frac(SWEET_MIN)) * H }}
          />
          <div className="absolute inset-x-0 bg-warn/25" style={{ top: (1 - frac(SWEET_MIN)) * H, bottom: 0 }} />
        </div>
        {/* live marker */}
        <div
          className="absolute left-[28px] w-[32px] transition-[top,opacity] duration-150 ease-linear"
          style={{ top: (1 - f) * H, opacity: has ? 1 : 0 }}
          aria-hidden
        >
          <div className="relative -translate-y-1/2">
            <div className={cn("h-[4px] w-[20px] rounded-full ring-2 ring-black/30", st.bg)} />
            <div
              className={cn(
                "absolute left-[22px] top-1/2 h-0 w-0 -translate-y-1/2 border-y-[6px] border-r-[8px] border-y-transparent",
                zone === "good" ? "border-r-ok" : zone === "close" || zone === "far" ? "border-r-warn" : "border-r-danger",
              )}
            />
          </div>
        </div>
      </div>
      <div className={cn("mt-2 flex h-8 items-center justify-center gap-1 text-center text-[10.5px] font-medium leading-tight", st.text)}>
        {zone === "too-far" || zone === "far" ? <ArrowDown size={11} className="shrink-0" aria-hidden /> : null}
        {zone === "too-close" || zone === "close" ? <ArrowUp size={11} className="shrink-0" aria-hidden /> : null}
        <span>{ZONE_TEXT[zone]}</span>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ view toolbar & hints
export function ViewToolbar({
  showGrid,
  setShowGrid,
  showTrail,
  setShowTrail,
}: {
  showGrid: boolean;
  setShowGrid: (v: boolean) => void;
  showTrail: boolean;
  setShowTrail: (v: boolean) => void;
}) {
  return (
    <div className="glass flex items-center gap-0.5 rounded-xl border border-line-strong p-1 shadow-md" role="toolbar" aria-label="View">
      <IconButton icon={Focus} label="Frame the model (F)" size="sm" onClick={() => viewBus.emit({ type: "frame", animate: true })} />
      <IconButton icon={Grid3x3} label="Grid" size="sm" active={showGrid} onClick={() => setShowGrid(!showGrid)} />
      <IconButton icon={Spline} label="Scanner path" size="sm" active={showTrail} onClick={() => setShowTrail(!showTrail)} />
    </div>
  );
}

const SHORTCUTS: [string, string][] = [
  ["Space", "Start · pause · resume"],
  ["R", "Rewind 2 s"],
  ["Esc", "Stop and save"],
  ["F", "Frame the model"],
  ["?", "Show / hide this help"],
];

/** Keyboard help. `compact` (narrow view, controls busy) shows just the button unless asked. */
export function ShortcutHints({ compact = false }: { compact?: boolean }) {
  const pref = useUi((s) => s.hintsOpen);
  const setPref = useUi((s) => s.setHintsOpen);
  const [peek, setPeek] = useState(false);
  const open = compact ? peek : pref;
  const setOpen = (v: boolean) => (compact ? setPeek(v) : setPref(v));
  if (!open)
    return (
      <IconButton
        icon={Keyboard}
        label="Keyboard shortcuts (?)"
        variant="secondary"
        size="md"
        className="glass !border-line-strong shadow-md"
        onClick={() => setOpen(true)}
      />
    );
  return (
    <div className="glass w-[220px] animate-pop-in rounded-xl border border-line-strong px-3 pb-2.5 pt-2 shadow-md" aria-label="Keyboard shortcuts">
      <div className="mb-1.5 flex items-center gap-1.5">
        <Keyboard size={13} className="text-muted" aria-hidden />
        <span className="flex-1 text-[11px] font-semibold uppercase tracking-wider text-subtle">Shortcuts</span>
        <button
          type="button"
          aria-label="Hide shortcuts"
          onClick={() => setOpen(false)}
          className="grid h-5 w-5 place-items-center rounded text-subtle hover:bg-surface-3 hover:text-fg"
        >
          <X size={12} aria-hidden />
        </button>
      </div>
      <ul className="space-y-1">
        {SHORTCUTS.map(([k, v]) => (
          <li key={k} className="flex items-center gap-2 text-xs text-muted">
            <Kbd className="min-w-[38px]">{k}</Kbd>
            <span>{v}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The coach mark after the setup wizard's "Scan your first object" (`#/scan?…&first=1`). */
export function FirstScanCoach({ onDismiss }: { onDismiss: () => void }) {
  const autoMesh = useUi((s) => s.autoMesh);
  const steps = [
    <>
      Point the scanner at a <b className="font-medium text-fg">mug</b>, about <b className="font-medium text-fg">25 cm</b> away.
    </>,
    <>
      Press the <b className="font-medium text-fg">trigger</b>, or <b className="font-medium text-fg">Start scan</b> below.
    </>,
    <>
      Move <b className="font-medium text-fg">slowly</b> all the way around it, keeping the distance gauge green.
    </>,
  ];
  return (
    <section className="glass coach-pulse w-[min(540px,calc(100vw-2rem))] animate-pop-in rounded-2xl border border-accent-line p-4 shadow-lg" aria-label="Your first scan">
      <div className="flex gap-4">
        <svg viewBox="0 0 96 96" className="h-20 w-20 shrink-0" aria-hidden>
          <ellipse cx="48" cy="82" rx="38" ry="8" fill="var(--c-surface-3)" />
          <path d="M14 58 A36 16 0 0 0 82 58" stroke="var(--c-accent)" strokeWidth="2.5" strokeDasharray="5 4" fill="none" />
          <path d="M80 52 L86 60 L76 62 Z" fill="var(--c-accent)" />
          <rect x="30" y="30" width="34" height="46" rx="5" fill="#e9e4da" />
          <ellipse cx="47" cy="30" rx="17" ry="4.5" fill="#d8d1c4" />
          <ellipse cx="47" cy="30" rx="13" ry="3" fill="#6b4a2e" />
          <path d="M64 40 C76 40 76 60 64 60" stroke="#e9e4da" strokeWidth="5.5" fill="none" strokeLinecap="round" />
        </svg>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-fg">Your first scan</div>
          <ol className="mt-1.5 space-y-1">
            {steps.map((s, i) => (
              <li key={i} className="flex gap-2 text-sm text-muted">
                <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-accent-soft text-[11px] font-semibold text-accent">{i + 1}</span>
                <span>{s}</span>
              </li>
            ))}
          </ol>
        </div>
        <IconButton icon={X} label="Dismiss the first-scan guide" size="sm" onClick={onDismiss} className="-mr-1 -mt-1" />
      </div>
      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="text-xs text-subtle">{autoMesh ? "Stop when it looks complete: the mesh is made right away." : "Stop when it looks complete, then press Make mesh."}</span>
        <Button size="sm" variant="primary" onClick={onDismiss}>
          Got it
        </Button>
      </div>
    </section>
  );
}

/** Centered guidance when nothing is connected: no dead end. */
export function NoDeviceOverlay() {
  const device = useDevice((s) => s.device);
  const connect = useDevice((s) => s.connect);
  const pending = useDevice((s) => s.pending);
  const busy = useDevice((s) => s.device?.busy ?? (s.simJobId ? "Generating simulator demo…" : null));
  if (!device || device.connected) return null;
  return (
    <div className="pointer-events-auto glass w-[400px] animate-pop-in rounded-2xl border border-line-strong p-6 text-center shadow-lg">
      <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-xl border border-line bg-surface-2 text-muted">
        <Cpu size={22} aria-hidden />
      </div>
      <div className="text-base font-semibold text-fg">{busy ? busy : "Connect a device to scan"}</div>
      <p className="mt-1 text-sm text-muted">
        {busy
          ? "The simulator connects itself when the demo recording is ready."
          : "Plug in the scanner, replay a recording, or try the simulator demo: no hardware needed."}
      </p>
      <div className="mt-4 flex justify-center gap-2">
        <Button icon={Cpu} onClick={() => navigate(paths.device())}>
          Device page
        </Button>
        <Button
          variant="primary"
          icon={FlaskConical}
          loading={pending === "connect" || !!busy}
          onClick={() => void connect({ source: "sim" })}
        >
          Start the simulator
        </Button>
      </div>
    </div>
  );
}
