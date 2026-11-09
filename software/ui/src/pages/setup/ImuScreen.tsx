/**
 * The IMU step: one big instruction card that walks through it. Armed: put the board in view
 * and press Start. Then "Hold still" counts down 2 s, and "Rotate slowly about all three axes"
 * runs a 20–60 s timer ring with a live gyro-rate gauge and how often the board was seen. It
 * stops by itself at the maximum; Stop is enabled from the minimum. Then it calibrates.
 *
 * CalibLive.imu (APP_API.md, "API deviations (M2)"): `elapsed_s`, `still_s` (2), `min_s` (22)
 * and `max_s` (62) are all seconds of frame time since the first recorded frame, so the hold-still
 * time is included. The screen shows rotation time (elapsed − still) against `min_s − still` to
 * `max_s − still`, i.e. 20–60 s.
 */
import { CircleCheck, Hand, Orbit, Play, RotateCcw, Square, TriangleExclamationPoint } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/Dialog";
import { ProgressBar, Spinner } from "../../components/ui/Feedback";
import { Callout } from "../../components/ui/Layout";
import { sentence } from "../../api/http";
import { asPercent, KIND_META } from "../../lib/setup";
import { unlockAudio } from "../../lib/sound";
import { cn, clamp, useHotkeys } from "../../lib/util";
import { useCalib } from "../../store/calib";
import { useJobs } from "../../store/jobs";
import { SoundToggle } from "./capture/CapturePanel";
import { CalibImage } from "./capture/LiveView";
import { SimChip } from "./common";
import { AdviceList } from "./ResultView";

function Ring({ value, size, stroke, color, children, marker }: { value: number; size: number; stroke: number; color: string; children?: ReactNode; marker?: number }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = clamp(value, 0, 1);
  return (
    <div className="relative grid shrink-0 place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--c-surface-3)" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${c * v} ${c}`}
          style={{ transition: "stroke-dasharray 120ms linear" }}
        />
        {marker !== undefined ? (
          <line
            x1={size / 2 + (r - stroke) * Math.cos(marker * 2 * Math.PI)}
            y1={size / 2 + (r - stroke) * Math.sin(marker * 2 * Math.PI)}
            x2={size / 2 + (r + stroke) * Math.cos(marker * 2 * Math.PI)}
            y2={size / 2 + (r + stroke) * Math.sin(marker * 2 * Math.PI)}
            stroke="var(--c-fg)"
            strokeWidth="2.5"
            strokeLinecap="round"
          />
        ) : null}
      </svg>
      <div className="absolute inset-0 grid place-items-center text-center">{children}</div>
    </div>
  );
}

/** A half-circle gauge for the gyro rate, deg/s, with the comfortable band. */
function GyroGauge({ dps, phase }: { dps: number; phase: string }) {
  const max = 200;
  const a = (x: number) => Math.PI * (1 - clamp(x, 0, max) / max);
  const W = 220,
    H = 124,
    cx = W / 2,
    cy = 112,
    r = 92;
  const arc = (x0: number, x1: number) => {
    const p0 = [cx + r * Math.cos(a(x0)), cy - r * Math.sin(a(x0))];
    const p1 = [cx + r * Math.cos(a(x1)), cy - r * Math.sin(a(x1))];
    return `M${p0[0]} ${p0[1]} A${r} ${r} 0 0 1 ${p1[0]} ${p1[1]}`;
  };
  const still = phase === "still" || phase === "armed";
  const done = phase === "done";
  const good = !done && (still ? dps < 5 : dps >= 20 && dps <= 150);
  const fast = !still && !done && dps > 150;
  const ang = a(dps);
  const nx = cx + (r - 14) * Math.cos(ang),
    ny = cy - (r - 14) * Math.sin(ang);
  return (
    <div className="text-center" role="meter" aria-label="Gyro rate" aria-valuemin={0} aria-valuemax={max} aria-valuenow={Math.round(dps)} aria-valuetext={`${Math.round(dps)} degrees per second`}>
      <svg viewBox={`0 0 ${W} ${H}`} className="mx-auto h-auto w-full max-w-[240px]" aria-hidden>
        <path d={arc(0, max)} stroke="var(--c-surface-3)" strokeWidth="14" fill="none" strokeLinecap="round" />
        {still ? (
          <path d={arc(0, 5)} stroke="var(--c-ok)" strokeOpacity="0.6" strokeWidth="14" fill="none" />
        ) : (
          <>
            <path d={arc(20, 150)} stroke="var(--c-ok)" strokeOpacity="0.45" strokeWidth="14" fill="none" />
            <path d={arc(150, max)} stroke="var(--c-warn)" strokeOpacity="0.45" strokeWidth="14" fill="none" />
          </>
        )}
        <line x1={cx} y1={cy} x2={nx} y2={ny} stroke="var(--c-fg)" strokeWidth="3.5" strokeLinecap="round" style={{ transition: "all 120ms linear" }} />
        <circle cx={cx} cy={cy} r="7" fill="var(--c-fg)" />
        <text x={cx - r} y={cy + 13} fontSize="10" fill="var(--c-fg-subtle)" textAnchor="middle">
          0
        </text>
        <text x={cx + r} y={cy + 13} fontSize="10" fill="var(--c-fg-subtle)" textAnchor="middle">
          {max}
        </text>
      </svg>
      <div className={cn("-mt-1 font-mono text-xl font-semibold tnum", good ? "text-ok" : fast ? "text-warn" : "text-fg")}>
        {Math.round(dps)} <span className="text-xs font-normal text-subtle">°/s</span>
      </div>
      <div className="text-xs text-muted">
        {done ? "Recording done" : still ? (dps < 5 ? "Still" : "Moving: hold still") : fast ? "A little slower" : dps < 20 ? "Rotate a bit more" : "Good pace"}
      </div>
    </div>
  );
}

const AXES = [
  { key: "roll", label: "Roll", how: "twist it like a key", cls: "imu-roll" },
  { key: "pitch", label: "Pitch", how: "nod it up and down", cls: "imu-pitch" },
  { key: "yaw", label: "Yaw", how: "turn it left and right", cls: "imu-yaw" },
] as const;

/** The three rotation axes, animated; the current one is highlighted while rotating. */
function Axes({ active }: { active: number | null }) {
  return (
    <div className="grid grid-cols-3 gap-3" aria-label="Rotate about all three axes: roll, pitch and yaw">
      {AXES.map((a, i) => (
        <div
          key={a.key}
          className={cn(
            "rounded-xl border p-3 text-center transition-colors",
            active === i ? "border-accent bg-accent-soft" : "border-line bg-surface-2/60",
          )}
        >
          <svg viewBox="0 0 120 70" className="mx-auto h-[58px] w-full" aria-hidden>
            <g className={a.cls} style={{ transformOrigin: "60px 35px", transformBox: "view-box" }}>
              <rect x="28" y="22" width="64" height="26" rx="8" fill="var(--c-surface-3)" stroke="var(--c-accent)" strokeWidth="2" />
              <circle cx="44" cy="35" r="5.5" fill="var(--c-bg)" stroke="var(--c-accent)" strokeWidth="1.5" />
              <circle cx="76" cy="35" r="5.5" fill="var(--c-bg)" stroke="var(--c-accent)" strokeWidth="1.5" />
              <rect x="54" y="48" width="12" height="16" rx="3" fill="var(--c-surface-3)" stroke="var(--c-accent)" strokeWidth="1.5" />
            </g>
          </svg>
          <div className={cn("mt-1 text-sm font-semibold", active === i ? "text-accent" : "text-fg")}>{a.label}</div>
          <div className="text-[11px] text-muted">{a.how}</div>
        </div>
      ))}
    </div>
  );
}

function SmallPreview() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.floor(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return (
    <div ref={ref} className="w-full">
      {w > 40 ? <CalibImage camera="calib_left" kind="imu" label="Left camera" primary={false} size={{ w, h: Math.round(w / 1.6) }} heatmap={false} /> : null}
    </div>
  );
}

export function ImuScreen() {
  const state = useCalib((s) => s.session?.state);
  const guidance = useCalib((s) => s.session?.guidance ?? "");
  const result = useCalib((s) => (s.session?.result?.grade === "failed" ? s.session.result : null));
  const phase = useCalib((s) => (s.live?.kind === "imu" ? (s.live.imu?.phase ?? null) : null));
  const elapsed = useCalib((s) => (s.live?.kind === "imu" ? (s.live.imu?.elapsed_s ?? 0) : 0));
  const stillS = useCalib((s) => (s.live?.kind === "imu" ? (s.live.imu?.still_s ?? 2) : 2));
  const minTotal = useCalib((s) => (s.live?.kind === "imu" ? (s.live.imu?.min_s ?? 22) : 22));
  const maxTotal = useCalib((s) => (s.live?.kind === "imu" ? (s.live.imu?.max_s ?? 62) : 62));
  const gyro = useCalib((s) => (s.live?.kind === "imu" ? (s.live.imu?.gyro_dps ?? 0) : 0));
  const boardRaw = useCalib((s) => (s.live?.kind === "imu" ? (s.live.imu?.board_pct ?? null) : null));
  const action = useCalib((s) => s.action);
  const imuStart = useCalib((s) => s.imuStart);
  const imuStop = useCalib((s) => s.imuStop);
  const solve = useCalib((s) => s.solve);
  const cancel = useCalib((s) => s.cancel);
  const jobId = useCalib((s) => s.session?.job_id ?? null);
  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));
  const [confirm, setConfirm] = useState(false);

  const recording = state === "recording";
  const armed = state === "armed";
  const recorded = state === "capturing";
  const solving = state === "solving";
  const p = recording ? (phase === "rotate" || elapsed >= stillS ? "rotate" : "still") : armed ? "armed" : "done";
  const rot = Math.max(0, elapsed - stillS);
  // rotation-time limits (the engine's min_s / max_s include the hold-still time)
  const minS = Math.max(0, Math.round(minTotal - stillS));
  const maxS = Math.max(1, Math.round(maxTotal - stillS));
  const canStop = recording && p === "rotate" && elapsed >= minTotal;
  const board = asPercent(boardRaw);
  const activeAxis = p === "rotate" ? Math.floor(rot / 4) % 3 : null;

  // Stopped (by hand or by itself): calibrate right away, once. The engine's `calib_session`
  // can arrive while the stop request is still in flight, so wait until no action is pending.
  const prev = useRef(state);
  const wantSolve = useRef(false);
  useEffect(() => {
    if (prev.current === "recording" && state === "capturing") wantSolve.current = true;
    else if (state !== "capturing") wantSolve.current = false;
    prev.current = state;
  }, [state]);
  useEffect(() => {
    if (wantSolve.current && !action && state === "capturing") {
      wantSolve.current = false;
      void solve();
    }
  }, [action, state, solve]);

  useHotkeys(
    {
      space: () => {
        unlockAudio();
        if (armed) void imuStart();
        else if (canStop) void imuStop();
      },
      enter: () => {
        if (recorded) void solve();
      },
      escape: () => setConfirm(true),
    },
    !solving,
  );

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-panel px-4">
        <Orbit size={17} className="shrink-0 text-accent" aria-hidden />
        <h1 className="shrink-0 text-sm font-semibold text-fg">{KIND_META.imu.title}</h1>
        <span className="hidden min-w-0 flex-1 truncate text-xs text-muted md:block">{sentence(guidance)}</span>
        <span className="flex-1 md:hidden" />
        <SimChip />
        <SoundToggle />
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto grid w-full max-w-[1240px] gap-5 p-5 lg:grid-cols-[1fr_320px]">
          <div className="space-y-4">
            {result && armed ? (
              <Callout tone="warn" icon={TriangleExclamationPoint} title="Not yet: here's why">
                <span className="block text-sm text-fg">{sentence(result.verdict)}</span>
                {result.advice.length ? (
                  <span className="mt-2 block">
                    <AdviceList advice={result.advice.filter((a) => a.action !== "start_over")} kind="imu" compact />
                  </span>
                ) : null}
              </Callout>
            ) : null}
            <section
              className={cn(
                "relative overflow-hidden rounded-2xl border p-7 shadow-sm transition-colors",
                p === "still" ? "border-warn/50 bg-warn-soft/50" : p === "rotate" ? "border-accent/50 bg-accent-soft/50" : "border-line bg-surface",
              )}
              aria-live="polite"
            >
              {armed ? (
                <div className="flex flex-wrap items-center gap-7">
                  <span className="grid h-24 w-24 shrink-0 place-items-center rounded-3xl border border-accent-line bg-accent-soft text-accent">
                    <Hand size={42} aria-hidden />
                  </span>
                  <div className="min-w-[240px] flex-1">
                    <div className="text-xs font-semibold uppercase tracking-wider text-subtle">Get ready</div>
                    <h2 className="mt-1 font-display text-3xl font-semibold text-fg">Put the board in view, then press Start</h2>
                    <p className="mt-1.5 text-sm text-muted">
                      Hold the scanner still for 2 seconds after you press Start, then rotate it slowly. The trigger works too.
                    </p>
                  </div>
                  <Button
                    variant="primary"
                    size="xl"
                    icon={Play}
                    kbd="Space"
                    loading={action === "imu_start"}
                    onClick={() => {
                      unlockAudio();
                      void imuStart();
                    }}
                  >
                    Start
                  </Button>
                </div>
              ) : p === "still" ? (
                <div className="flex flex-wrap items-center gap-7">
                  <Ring value={1 - clamp(elapsed / Math.max(0.1, stillS), 0, 1)} size={132} stroke={12} color="var(--c-warn)">
                    <div>
                      <div className="font-display text-5xl font-semibold text-fg tnum">{Math.max(0, Math.ceil(stillS - elapsed))}</div>
                      <div className="text-xs text-muted">seconds</div>
                    </div>
                  </Ring>
                  <div className="min-w-[240px] flex-1">
                    <div className="text-xs font-semibold uppercase tracking-wider text-warn">Recording</div>
                    <h2 className="mt-1 font-display text-4xl font-semibold text-fg">Hold still</h2>
                    <p className="mt-1.5 text-sm text-muted">The gyro measures its bias while the scanner doesn't move.</p>
                  </div>
                </div>
              ) : p === "rotate" ? (
                <div className="flex flex-wrap items-center gap-7">
                  <Ring value={rot / Math.max(1, maxS)} size={150} stroke={12} color={rot >= minS ? "var(--c-ok)" : "var(--c-accent)"} marker={minS / Math.max(1, maxS)}>
                    <div>
                      <div className="font-display text-5xl font-semibold text-fg tnum">{Math.floor(rot)}</div>
                      <div className="text-xs text-muted">of {minS}–{maxS} s</div>
                    </div>
                  </Ring>
                  <div className="min-w-[240px] flex-1">
                    <div className="text-xs font-semibold uppercase tracking-wider text-accent">Recording</div>
                    <h2 className="mt-1 font-display text-3xl font-semibold text-fg">Rotate slowly about all three axes, ±20–40°</h2>
                    <p className="mt-1.5 text-sm text-muted">
                      {rot < minS
                        ? `Keep going: Stop unlocks at ${minS} s. Keep the board in view.`
                        : `Good: stop whenever you like. It stops by itself at ${maxS} s.`}
                    </p>
                    <div className="mt-4 flex flex-wrap items-center gap-3">
                      <Button
                        variant={canStop ? "primary" : "secondary"}
                        size="lg"
                        icon={Square}
                        kbd="Space"
                        disabled={!canStop}
                        loading={action === "imu_stop"}
                        onClick={() => void imuStop()}
                      >
                        {canStop ? "Stop" : `Stop (at ${minS} s)`}
                      </Button>
                      <ProgressBar className="max-w-[240px]" value={rot / Math.max(1, maxS)} tone={rot >= minS ? "ok" : "accent"} label="Rotation time" />
                    </div>
                  </div>
                </div>
              ) : solving ? (
                <div className="flex flex-wrap items-center gap-6" role="status">
                  <Spinner size={42} className="text-accent" />
                  <div className="min-w-[240px] flex-1">
                    <div className="text-xs font-semibold uppercase tracking-wider text-subtle">Recording done</div>
                    <h2 className="mt-1 font-display text-2xl font-semibold text-fg">Calibrating the IMU…</h2>
                    <p className="mt-1 text-sm text-muted">{job?.message || "Fitting the gyro to the camera's motion"}</p>
                    <ProgressBar className="mt-3 max-w-md" value={job && job.state !== "queued" ? job.progress : null} label="Calibration progress" />
                  </div>
                </div>
              ) : (
                <div className="flex flex-wrap items-center gap-6">
                  <CircleCheck size={44} className="text-ok" aria-hidden />
                  <div className="min-w-[240px] flex-1">
                    <div className="text-xs font-semibold uppercase tracking-wider text-subtle">Recording done</div>
                    <h2 className="mt-1 font-display text-2xl font-semibold text-fg">{rot > 0 ? `${Math.round(rot)} s of rotation recorded` : "Recording saved"}</h2>
                    <p className="mt-1 text-sm text-muted">Calibrate it now, or record again.</p>
                  </div>
                  <div className="flex gap-2">
                    <Button icon={RotateCcw} onClick={() => void useCalib.getState().startOver()}>
                      Record again
                    </Button>
                    <Button variant="primary" size="lg" kbd="Enter" loading={action === "solve"} onClick={() => void solve()}>
                      Calibrate
                    </Button>
                  </div>
                </div>
              )}
            </section>
            <Axes active={activeAxis} />
            <div className="flex justify-end">
              <Button variant="danger-ghost" size="sm" kbd="Esc" onClick={() => setConfirm(true)} disabled={solving}>
                Cancel
              </Button>
            </div>
          </div>

          <aside className="space-y-4" aria-label="IMU recording">
            <div className="rounded-xl border border-line bg-panel p-3">
              <SmallPreview />
            </div>
            <div className="rounded-xl border border-line bg-panel p-4">
              <div className="mb-1 flex items-baseline justify-between">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-subtle">Board seen</span>
                <span className={cn("font-mono text-lg font-semibold tnum", board !== null && board < 80 ? "text-warn" : "text-fg")}>
                  {board === null || (!recording && !recorded) ? "–" : `${Math.round(board)} %`}
                </span>
              </div>
              <ProgressBar value={board === null ? 0 : board / 100} tone={board !== null && board < 80 ? "warn" : "ok"} label="Share of frames with the board in view" />
              <p className={cn("mt-1.5 text-xs", board !== null && board < 80 && recording ? "text-warn" : "text-subtle")}>
                {board !== null && board < 80 && recording ? "Keep the board in view: smaller, slower turns." : "Keep it above 80 %: the board's pose is what the gyro is compared with."}
              </p>
            </div>
            <div className="rounded-xl border border-line bg-panel p-4">
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-subtle">Gyro rate</div>
              <GyroGauge dps={gyro} phase={p} />
            </div>
          </aside>
        </div>
      </div>
      <ConfirmDialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Cancel the IMU calibration?"
        confirmLabel="Cancel calibration"
        tone="danger"
        onConfirm={() => cancel()}
      >
        The recording stays on disk, but this session ends. Your saved calibration doesn't change.
      </ConfirmDialog>
    </div>
  );
}
