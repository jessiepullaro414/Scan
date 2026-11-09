/**
 * The capture screen's side panel: views against the target, coverage per camera, pose
 * diversity, board distance and tilt, the laser lines, and the controls (auto-capture,
 * Capture, Finish & calibrate, Undo, Cancel). Each block selects only what it shows.
 */
import { Aperture, ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Camera, Check, Ruler, Sparkles, Undo2, Volume2, VolumeX, X, Zap } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import type { CalibKind } from "../../../api/types";
import { Button, IconButton } from "../../../components/ui/Button";
import { Switch } from "../../../components/ui/Form";
import { Tooltip } from "../../../components/ui/Popover";
import { asPercent, gridPercent, KIND_META } from "../../../lib/setup";
import { unlockAudio } from "../../../lib/sound";
import { cn, clamp } from "../../../lib/util";
import { useCalib } from "../../../store/calib";
import { useUi } from "../../../store/ui";

function Block({ title, children, aside, icon: Icon }: { title: string; children: ReactNode; aside?: ReactNode; icon?: typeof Camera }) {
  return (
    <section className="border-b border-line px-4 py-3" aria-label={title}>
      <div className="mb-1.5 flex items-center gap-2">
        {Icon ? <Icon size={13} className="text-muted" aria-hidden /> : null}
        <h3 className="flex-1 text-[11px] font-semibold uppercase tracking-wider text-subtle">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function ViewsCounter({ kind }: { kind: CalibKind }) {
  const n = useCalib((s) => s.session?.views.length ?? 0);
  const lo = useCalib((s) => s.session?.target_views[0] ?? 0);
  const hi = useCalib((s) => s.session?.target_views[1] ?? 0);
  const min = useCalib((s) => s.session?.min_views ?? 0);
  const max = Math.max(hi * 1.15, n, 1);
  const unit = KIND_META[kind].minLabel;
  const state = n >= hi && hi > 0 ? "Plenty" : n >= lo && lo > 0 ? "Enough" : n >= min && min > 0 ? "Usable" : "";
  return (
    <Block title={kind === "lasers" ? "Poses" : "Views"} icon={Camera} aside={state ? <span className={cn("text-[11px] font-medium", n >= lo ? "text-ok" : "text-warn")}>{state}</span> : null}>
      <div className="flex items-baseline gap-2">
        <span className="font-display text-3xl font-semibold leading-none text-fg tnum" aria-live="polite">
          {n}
        </span>
        <span className="text-sm text-muted">
          of {lo}–{hi} {unit}
        </span>
      </div>
      <div className="relative mt-2 h-2 rounded-full bg-surface-3" role="meter" aria-label={`${n} ${unit}, aim for ${lo} to ${hi}`} aria-valuemin={0} aria-valuemax={hi} aria-valuenow={n}>
        <div className="absolute inset-y-0 rounded-full bg-ok/25" style={{ left: `${(lo / max) * 100}%`, width: `${((hi - lo) / max) * 100}%` }} />
        <div className={cn("absolute inset-y-0 left-0 rounded-full transition-[width] duration-300", n >= lo ? "bg-ok" : n >= min ? "bg-accent" : "bg-warn")} style={{ width: `${clamp(n / max, 0, 1) * 100}%` }} />
        {min > 0 ? <div className="absolute -bottom-1 -top-1 w-px bg-fg/50" style={{ left: `${(min / max) * 100}%` }} title={`Finish unlocks at ${min}`} /> : null}
      </div>
      <div className="mt-1.5 text-[11px] text-subtle">{n < min ? `Finish unlocks at ${min} ${unit}` : n < lo ? `${lo - n} more for a good result` : "You can finish any time"}</div>
    </Block>
  );
}

function MiniGrid({ cells, cols }: { cells: number[]; cols: number }) {
  return (
    <div className="grid gap-px overflow-hidden rounded-[4px] bg-line" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }} aria-hidden>
      {cells.map((c, i) => (
        <span key={i} className="aspect-[2] bg-surface-3" style={c > 0 ? { background: `rgb(70 211 105 / ${Math.min(0.85, 0.3 + 0.16 * c)})` } : undefined} />
      ))}
    </div>
  );
}

export function CoverageStat({ kind }: { kind: CalibKind }) {
  const left = useCalib((s) => (s.live?.kind === kind ? (s.live.coverage?.left.join(",") ?? null) : null));
  const right = useCalib((s) => (s.live?.kind === kind ? (s.live.coverage?.right?.join(",") ?? null) : null));
  const cols = useCalib((s) => s.live?.coverage?.cols ?? 8);
  const lp = useCalib((s) => (s.live?.kind === kind ? (s.live.coverage?.left_pct ?? null) : null));
  const rp = useCalib((s) => (s.live?.kind === kind ? (s.live.coverage?.right_pct ?? null) : null));
  if (left === null) return null;
  const lc = left ? left.split(",").map(Number) : [];
  const rc = right ? right.split(",").map(Number) : null;
  const L = asPercent(lp, gridPercent(lc)) ?? gridPercent(lc);
  const R = rc ? (asPercent(rp, gridPercent(rc)) ?? gridPercent(rc)) : null;
  const goal = 60;
  const one = (label: string, pct: number, cells: number[]) => (
    <div className="min-w-0 flex-1">
      <div className="mb-1 flex items-baseline justify-between gap-1">
        <span className="text-xs text-muted">{label}</span>
        <span className={cn("font-mono text-sm font-semibold tnum", pct >= goal ? "text-ok" : "text-fg")}>{Math.round(pct)} %</span>
      </div>
      <MiniGrid cells={cells} cols={cols} />
    </div>
  );
  return (
    <Block
      title="Coverage"
      icon={Aperture}
      aside={
        <Tooltip content="The share of each image that had board corners in at least one view. The lens edges distort the most, so they need views too.">
          <span tabIndex={0} className="cursor-help text-[11px] text-subtle underline decoration-dotted underline-offset-2">
            want &gt; {goal} %
          </span>
        </Tooltip>
      }
    >
      <div className="flex gap-3">
        {one(rc ? "Left" : "Image", L, lc)}
        {rc && R !== null ? one("Right", R, rc) : null}
      </div>
    </Block>
  );
}

const TILTS = [
  { key: "flat", label: "Flat", icon: null },
  { key: "tilt_left", label: "Left", icon: ArrowLeft },
  { key: "tilt_right", label: "Right", icon: ArrowRight },
  { key: "tilt_up", label: "Up", icon: ArrowUp },
  { key: "tilt_down", label: "Down", icon: ArrowDown },
] as const;
const DISTS = [
  { key: "near", label: "Near" },
  { key: "mid", label: "Mid" },
  { key: "far", label: "Far" },
] as const;

function Chip({ n, label, icon: Icon, need }: { n: number; label: string; icon?: typeof ArrowLeft | null; need: boolean }) {
  const missing = need && n === 0;
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center gap-1 rounded-full border px-2 text-[11px] font-medium",
        n > 0 ? "border-ok/35 bg-ok-soft text-ok" : missing ? "border-dashed border-warn/50 text-warn" : "border-line text-subtle",
      )}
      title={missing ? `No ${label.toLowerCase()} view yet` : `${n} view${n === 1 ? "" : "s"}`}
    >
      {Icon ? <Icon size={11} aria-hidden /> : null}
      {label}
      <span className="font-mono tnum">{n}</span>
    </span>
  );
}

export function DiversityChips({ kind }: { kind: CalibKind }) {
  const div = useCalib((s) => {
    const d = s.live?.kind === kind ? s.live.diversity : null;
    return d ? [d.flat, d.tilt_left, d.tilt_right, d.tilt_up, d.tilt_down, d.near, d.mid, d.far].join(",") : null;
  });
  if (!div) return null;
  const v = div.split(",").map(Number);
  const needTilt = kind !== "check";
  return (
    <Block title="Variety" icon={Sparkles}>
      <div className="mb-1 text-[11px] text-subtle">Tilt</div>
      <div className="mb-2.5 flex flex-wrap gap-1.5">
        {TILTS.map((t, i) => (
          <Chip key={t.key} n={v[i]} label={t.label} icon={t.icon} need={needTilt && i > 0} />
        ))}
      </div>
      <div className="mb-1 text-[11px] text-subtle">Distance</div>
      <div className="flex flex-wrap gap-1.5">
        {DISTS.map((d, i) => (
          <Chip key={d.key} n={v[5 + i]} label={d.label} need />
        ))}
      </div>
    </Block>
  );
}

export function BoardStat({ kind }: { kind: CalibKind }) {
  const d = useCalib((s) => (s.live?.kind === kind && s.live.distance_mm !== null ? Math.round(s.live.distance_mm) : null));
  const tilt = useCalib((s) => (s.live?.kind === kind && s.live.tilt_deg !== null ? Math.round(s.live.tilt_deg) : null));
  const common = useCalib((s) => (s.live?.kind === kind ? s.live.common : null));
  const [lo, hi] = KIND_META[kind].distance;
  const span = hi - lo;
  const pad = span * 0.18;
  const f = (x: number) => clamp((x - (lo - pad)) / (span + 2 * pad), 0, 1) * 100;
  const inRange = d !== null && d >= lo && d <= hi;
  return (
    <Block title="Board" icon={Ruler}>
      <div className="flex items-baseline gap-4">
        <div>
          <span className={cn("font-mono text-lg font-semibold tnum", d === null ? "text-subtle" : inRange ? "text-fg" : "text-warn")}>{d ?? "–"}</span>
          <span className="ml-1 text-xs text-subtle">mm</span>
        </div>
        <div>
          <span className="font-mono text-lg font-semibold text-fg tnum">{tilt ?? "–"}</span>
          <span className="ml-0.5 text-xs text-subtle">° tilt</span>
        </div>
        {common !== null ? (
          <div className="ml-auto text-right">
            <span className={cn("font-mono text-lg font-semibold tnum", common >= 8 ? "text-fg" : "text-warn")}>{common}</span>
            <span className="ml-1 text-xs text-subtle">common</span>
          </div>
        ) : null}
      </div>
      <div className="relative mt-2 h-1.5 rounded-full bg-surface-3" aria-hidden>
        <div className="absolute inset-y-0 rounded-full bg-ok/35" style={{ left: `${f(lo)}%`, width: `${f(hi) - f(lo)}%` }} />
        {d !== null ? (
          <div
            className={cn("absolute -top-1 h-3.5 w-1.5 -translate-x-1/2 rounded-full ring-2 ring-panel transition-[left] duration-150", inRange ? "bg-ok" : "bg-warn")}
            style={{ left: `${f(d)}%` }}
          />
        ) : null}
      </div>
      <div className="mt-1 flex justify-between font-mono text-[10px] text-subtle tnum">
        <span>{lo}</span>
        <span>aim {lo}–{hi} mm</span>
        <span>{hi}</span>
      </div>
    </Block>
  );
}

export function LaserStat() {
  const v = useCalib((s) => s.live?.lines?.vertical ?? null);
  const h = useCalib((s) => s.live?.lines?.horizontal ?? null);
  if (v === null || h === null) return null;
  const row = (label: string, n: number, color: string) => {
    const ok = n >= 15;
    return (
      <li className="flex items-center gap-2.5">
        <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: color }} aria-hidden />
        <span className="flex-1 text-sm text-fg">{label}</span>
        <span className="font-mono text-xs text-muted tnum">{n} pts</span>
        <span
          className={cn("inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10.5px] font-semibold", ok ? "bg-ok-soft text-ok" : "bg-warn-soft text-warn")}
          aria-label={ok ? `${label}: on the white margin` : `${label}: misses the white margin`}
        >
          {ok ? <Check size={11} strokeWidth={3} aria-hidden /> : <X size={11} strokeWidth={3} aria-hidden />}
          {ok ? "on margin" : "missing"}
        </span>
      </li>
    );
  };
  return (
    <Block title="Laser lines" icon={Zap}>
      <ul className="space-y-2">
        {row("Vertical line", v, "#ff5a4f")}
        {row("Horizontal line", h, "#3ad3ff")}
      </ul>
      <p className="mt-2 text-[11px] text-subtle">Each line needs 15+ points on the white paper around the squares.</p>
    </Block>
  );
}

export function SoundToggle() {
  const on = useUi((s) => s.captureSound);
  const set = useUi((s) => s.setCaptureSound);
  return (
    <IconButton
      icon={on ? Volume2 : VolumeX}
      label={on ? "Mute the capture click" : "Play a click on each capture"}
      size="sm"
      active={on}
      onClick={() => {
        unlockAudio();
        set(!on);
      }}
    />
  );
}

function Refusal() {
  const refusal = useCalib((s) => s.refusal);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!refusal) return;
    setVisible(true);
    const t = setTimeout(() => setVisible(false), 3200);
    return () => clearTimeout(t);
  }, [refusal]);
  if (!refusal || !visible) return null;
  return (
    <div role="alert" className="mt-2 animate-pop-in rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-xs">
      <div className="font-medium text-fg">Not captured: {refusal.text}</div>
      {refusal.hint ? <div className="mt-0.5 text-muted">{refusal.hint}</div> : null}
    </div>
  );
}

export function CaptureControls({ kind, onFinish, onCancel }: { kind: CalibKind; onFinish: () => void; onCancel: () => void }) {
  const auto = useCalib((s) => !!s.session?.auto);
  const setAuto = useCalib((s) => s.setAuto);
  const capture = useCalib((s) => s.capture);
  const undo = useCalib((s) => s.undoLast);
  const action = useCalib((s) => s.action);
  const n = useCalib((s) => s.session?.views.length ?? 0);
  const min = useCalib((s) => s.session?.min_views ?? 1);
  const ready = useCalib((s) => (s.live?.kind === kind ? s.live.ready : false));
  const canFinish = n >= min;
  return (
    <div className="px-4 py-4">
      <div className="mb-3 rounded-lg border border-line bg-surface-2/60 px-3 py-2">
        <Switch
          checked={auto}
          onChange={(v) => void setAuto(v)}
          label="Auto-capture"
          description={auto ? "When the board is steady at a new spot." : "Press Space or the trigger per view."}
        />
      </div>
      <div className="space-y-2">
        <Button
          variant={auto ? "secondary" : "primary"}
          size="lg"
          className="w-full"
          icon={Camera}
          kbd="Space"
          loading={action === "capture"}
          onClick={() => {
            unlockAudio();
            void capture();
          }}
          title={ready ? "Take this view now" : "Takes the view if it can be used"}
        >
          Capture
        </Button>
        <Refusal />
        <Tooltip content={canFinish ? "Solve the calibration from the captured views." : `Needs at least ${min} ${KIND_META[kind].minLabel}.`}>
          <span className="block w-full">
            <Button variant={auto || canFinish ? "primary" : "secondary"} size="lg" className="w-full" kbd="Enter" disabled={!canFinish} loading={action === "solve"} onClick={onFinish}>
              {kind === "check" ? "Finish & check" : "Finish & calibrate"}
            </Button>
          </span>
        </Tooltip>
        <div className="flex gap-2 pt-1">
          <Button variant="ghost" size="sm" icon={Undo2} kbd="⌫" className="flex-1" disabled={!n} loading={action === "delete"} onClick={() => void undo()}>
            Undo last
          </Button>
          <Button variant="danger-ghost" size="sm" kbd="Esc" className="flex-1" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </div>
    </div>
  );
}
