/**
 * The calibration steps (stereo, lasers, IMU, check). Without a session for this kind: an intro
 * with what to do and a Start button. While capturing: the live view with its overlays, the side
 * panel and the views strip (keyboard: Space, Enter, Backspace, Esc). After a solve: the result.
 */
import { Aperture, Grid3x3, Play, RotateCcw, TriangleExclamationPoint, X, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import type { CalibKind, CalibSession } from "../../api/types";
import { Badge } from "../../components/ui/Badge";
import { Button, IconButton } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/Dialog";
import { ProgressBar, Skeleton, Spinner } from "../../components/ui/Feedback";
import { Callout } from "../../components/ui/Layout";
import { sentence } from "../../api/http";
import { fmtRelative } from "../../lib/format";
import { navigate, paths } from "../../lib/router";
import { KIND_META, lcFirst, STEP_META } from "../../lib/setup";
import { playCaptureClick, unlockAudio } from "../../lib/sound";
import { useHotkeys } from "../../lib/util";
import { isSessionActive, useCalib } from "../../store/calib";
import { useDevice } from "../../store/device";
import { isActive, useJobs } from "../../store/jobs";
import { isLive, useSession } from "../../store/session";
import { stepOf, useSetup } from "../../store/setup";
import { useUi } from "../../store/ui";
import { BoardStat, CaptureControls, CoverageStat, DiversityChips, LaserStat, SoundToggle, ViewsCounter } from "./capture/CapturePanel";
import { Stage } from "./capture/LiveView";
import { ViewsStrip } from "./capture/ViewsStrip";
import { ErrorCallout, SimChip, StepFooter, StepHeader, StepPage } from "./common";
import { ImuScreen } from "./ImuScreen";
import { AdviceList, ResultView } from "./ResultView";

const HOW: Record<CalibKind, string[]> = {
  stereo: [
    "Hold the glued board 20–50 cm in front of the scanner, facing it.",
    "Follow the blue box: it shows where to put the board next. Tilt it when asked.",
    "Hold still: the ring fills and the view is taken by itself (or press Space).",
    "At 15–25 views, press Enter. You get a grade and plain advice.",
  ],
  lasers: [
    "The two line lasers switch on. Never look into them.",
    "Hold the board so both lines cross the white paper around the squares.",
    "Change the distance (15–45 cm) and the tilt between poses.",
    "At 6–10 poses, press Enter.",
  ],
  imu: [
    "Put the board where the camera sees it: propped on a table is fine.",
    "Press Start, then hold the scanner still for 2 seconds.",
    "Rotate it slowly about all three axes, ±20–40°, for 20–60 seconds, keeping the board in view.",
    "Press Stop, or wait: it stops by itself at 60 s.",
  ],
  check: [
    "The line lasers switch on. Never look into them.",
    "Show the board at 3 to 5 different distances, with both lines on its white margin.",
    "Press Enter: everything is compared with the saved calibration, which never changes.",
  ],
};

/** A small picture of what the step looks like. */
function KindArt({ kind }: { kind: CalibKind }) {
  const board = (x: number, y: number, w: number, h: number, skew = 0) => {
    const cols = 6,
      rows = 4;
    const cells = [];
    for (let j = 0; j < rows; j++)
      for (let i = 0; i < cols; i++)
        if ((i + j) % 2 === 0) cells.push(<rect key={`${i}-${j}`} x={x + (i * w) / cols} y={y + (j * h) / rows} width={w / cols} height={h / rows} fill="#1b1d20" />);
    return (
      <g transform={`skewY(${skew})`}>
        <rect x={x - 8} y={y - 8} width={w + 16} height={h + 16} rx="3" fill="#eceae4" />
        {cells}
      </g>
    );
  };
  return (
    <svg viewBox="0 0 320 180" className="h-auto w-full" aria-hidden>
      <rect width="320" height="180" rx="12" fill="#191c20" />
      {kind === "stereo" ? (
        <>
          <g stroke="rgb(255 255 255 / 0.08)">
            {[1, 2].map((i) => (
              <line key={`v${i}`} x1={(320 * i) / 3} y1="0" x2={(320 * i) / 3} y2="180" />
            ))}
            {[1, 2].map((i) => (
              <line key={`h${i}`} x1="0" y1={(180 * i) / 3} x2="320" y2={(180 * i) / 3} />
            ))}
          </g>
          <rect x="6" y="6" width="94" height="48" fill="rgb(70 211 105 / 0.22)" />
          <rect x="113" y="66" width="94" height="48" fill="rgb(70 211 105 / 0.22)" />
          {board(122, 72, 76, 36)}
          <rect x="218" y="8" width="94" height="44" rx="2" fill="rgb(106 166 255 / 0.12)" stroke="#6aa6ff" strokeWidth="2" strokeDasharray="7 5" />
          <path d="M200 74 L244 48" stroke="#6aa6ff" strokeWidth="3" strokeDasharray="7 5" strokeLinecap="round" />
          <path d="M252 43 L238 45 L246 56 Z" fill="#6aa6ff" />
        </>
      ) : kind === "lasers" || kind === "check" ? (
        <>
          {board(90, 50, 140, 80)}
          <path d="M168 26 L160 154" stroke="#ff3322" strokeWidth="2.5" />
          <path d="M70 96 L250 84" stroke="#ff3322" strokeWidth="2.5" />
          <circle cx="166" cy="38" r="4" fill="#ff5a4f" />
          <circle cx="161" cy="146" r="4" fill="#ff5a4f" />
          <circle cx="84" cy="95" r="4" fill="#3ad3ff" />
          <circle cx="236" cy="85" r="4" fill="#3ad3ff" />
          {kind === "check" ? (
            <g transform="translate(262 18)">
              <circle cx="20" cy="20" r="18" fill="#46d369" />
              <path d="M11 20 L18 27 L30 13" stroke="#fff" strokeWidth="4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
            </g>
          ) : null}
        </>
      ) : (
        <>
          {board(100, 96, 120, 56)}
          <g transform="translate(160 54)">
            <rect x="-34" y="-14" width="68" height="28" rx="8" fill="#2a2f36" stroke="#6aa6ff" strokeWidth="2" />
            <circle cx="-18" cy="0" r="6" fill="#0e1013" stroke="#6aa6ff" strokeWidth="1.5" />
            <circle cx="18" cy="0" r="6" fill="#0e1013" stroke="#6aa6ff" strokeWidth="1.5" />
          </g>
          <path d="M108 40 A56 30 0 0 1 212 40" stroke="#6aa6ff" strokeWidth="2.5" fill="none" strokeDasharray="6 5" />
          <path d="M214 34 L216 48 L204 42 Z" fill="#6aa6ff" />
          <path d="M106 34 L104 48 L116 42 Z" fill="#6aa6ff" />
        </>
      )}
    </svg>
  );
}

function OtherSession({ other, kind }: { other: CalibSession; kind: CalibKind }) {
  const cancel = useCalib((s) => s.cancel);
  const start = useCalib((s) => s.start);
  const action = useCalib((s) => s.action);
  const [confirm, setConfirm] = useState(false);
  const ok = other.kind ?? "stereo";
  const result = other.state === "result";
  // A result that can't be saved (a check) is just closed: nothing is lost.
  const closable = result && !!other.result && !other.result.can_save;
  const name = lcFirst(STEP_META[ok].title);
  const title = closable
    ? `The ${name} result is still open`
    : result
      ? `Your ${name} result isn't saved yet`
      : `The ${name} calibration is in progress`;
  const switchOver = async () => {
    if (await cancel()) await start(kind);
  };
  return (
    <Callout
      tone="accent"
      icon={Aperture}
      title={title}
      className="mb-5"
      actions={
        <>
          <Button size="sm" variant={closable ? "secondary" : "primary"} onClick={() => navigate(paths.setup(ok))}>
            {result ? "Review it" : `Continue ${STEP_META[ok].title}`}
          </Button>
          <Button
            size="sm"
            variant={closable ? "primary" : "ghost"}
            loading={action === "cancel" || action === "start"}
            onClick={() => (closable ? void switchOver() : setConfirm(true))}
          >
            {closable ? `Close it and start ${STEP_META[kind].title}` : `Discard it and start ${STEP_META[kind].title}`}
          </Button>
        </>
      }
    >
      {closable
        ? "One calibration runs at a time. Closing it loses nothing: its verdict is kept in Setup."
        : result
          ? "Save it or discard it first: one calibration runs at a time."
          : `${other.views.length} view${other.views.length === 1 ? "" : "s"} so far. One calibration runs at a time.`}
      <ConfirmDialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title={result ? "Discard the unsaved result?" : `Stop the ${lcFirst(STEP_META[ok].title)} calibration?`}
        confirmLabel={`Discard and start ${STEP_META[kind].title}`}
        tone="danger"
        onConfirm={switchOver}
      >
        The captured views stay on disk, but this session ends{result ? " and the result is not saved" : ""}. Your saved calibration doesn't change.
      </ConfirmDialog>
    </Callout>
  );
}

function Intro({ kind, other }: { kind: CalibKind; other: CalibSession | null }) {
  const device = useDevice((s) => s.device);
  const setup = useSetup((s) => s.setup)!;
  const startError = useCalib((s) => (s.startError?.kind === kind ? s.startError.err : null));
  const clearError = useCalib((s) => s.clearStartError);
  const action = useCalib((s) => s.action);
  const start = useCalib((s) => s.start);
  const scanning = useSession((s) => isLive(s.session));
  const step = stepOf(setup, kind);
  const meta = KIND_META[kind];
  const connected = !!device?.connected;
  const replay = device?.source === "replay";
  const needsStereo = kind !== "stereo" && !setup.calibration.stereo;
  const noPico = device?.source === "camera" && !device.pico?.connected;
  const done = step?.state === "done";
  const dropsOthers = kind === "stereo" && (setup.calibration.lasers.length > 0 || setup.calibration.imu);
  const blocked = !connected || replay || needsStereo || scanning || !!other || (kind === "imu" && noPico);

  useEffect(() => () => clearError(), [clearError]);

  const blockers: { title: string; text: string; to: string; label: string }[] = [];
  if (!connected) blockers.push({ title: "Connect the scanner first", text: "Calibration needs the camera, or the simulator.", to: paths.setup("connect"), label: "Go to Connect" });
  else if (replay) blockers.push({ title: "A replay is connected", text: "Calibration needs the camera or the simulator.", to: paths.setup("connect"), label: "Go to Connect" });
  if (needsStereo)
    blockers.push({ title: "Do the stereo step first", text: `The ${lcFirst(meta.title)} builds on the stereo calibration.`, to: paths.setup("stereo"), label: "Go to Stereo" });
  if (scanning) blockers.push({ title: "A scan is running", text: "A scan and a calibration never run together: stop the scan first.", to: paths.scan(), label: "Open the Scan page" });
  if (kind === "imu" && connected && noPico)
    blockers.push({ title: "The IMU step needs the Pico", text: "Its IMU is on the Pico. Tracking works without it: you can skip this step.", to: paths.setup("connect"), label: "Go to Connect" });

  return (
    <StepPage wide>
      <StepHeader
        page={kind}
        aside={
          done && step?.when ? (
            <Badge tone="ok" dot>
              Calibrated {fmtRelative(step.when)}
            </Badge>
          ) : null
        }
      />
      {other ? <OtherSession other={other} kind={kind} /> : null}
      <div className="grid gap-5 lg:grid-cols-[1fr_1.1fr]">
        <div className="overflow-hidden rounded-xl border border-line bg-surface shadow-sm">
          <KindArt kind={kind} />
        </div>
        <div className="rounded-xl border border-line bg-surface p-5 shadow-sm">
          <h2 className="text-sm font-semibold text-fg">How it works</h2>
          <ol className="mt-3 space-y-2.5">
            {HOW[kind].map((t, i) => (
              <li key={i} className="flex gap-3 text-sm text-fg">
                <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-accent-soft text-xs font-semibold text-accent">{i + 1}</span>
                <span className="pt-0.5">{t}</span>
              </li>
            ))}
          </ol>
          <p className="mt-4 border-t border-line pt-3 text-xs text-muted">
            <b className="font-medium text-fg">You need:</b> {meta.needs} About {STEP_META[kind].time}.
          </p>
        </div>
      </div>

      <div className="mt-5 space-y-3">
        {blockers.map((b) => (
          <Callout
            key={b.title}
            tone="warn"
            icon={TriangleExclamationPoint}
            title={b.title}
            actions={
              <Button size="sm" variant="primary" onClick={() => navigate(b.to)}>
                {b.label}
              </Button>
            }
          >
            {b.text}
          </Callout>
        ))}
        {(kind === "lasers" || kind === "check") && connected && device?.source === "camera" && !noPico ? (
          <Callout tone="warn" icon={Zap} title="The line lasers switch on during this step">
            They're Class 3R: never look into the beams, and keep them pointed at the board.
          </Callout>
        ) : null}
        {dropsOthers ? (
          <Callout tone="neutral" icon={TriangleExclamationPoint} title="Saving a new stereo calibration resets the lasers and the IMU">
            They're expressed in the stereo cameras' frame, so they're dropped when you save a new one: redo those two steps after (about 4 minutes).
          </Callout>
        ) : null}
        {startError ? <ErrorCallout title={`Couldn't start the ${lcFirst(meta.title)}`} err={startError} here={kind} onRetry={() => void start(kind)} onDismiss={clearError} /> : null}
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3 rounded-xl border border-line bg-surface p-5 shadow-sm">
        <Button
          variant="primary"
          size="xl"
          icon={done ? RotateCcw : Play}
          loading={action === "start"}
          disabled={blocked}
          onClick={() => {
            unlockAudio();
            void start(kind);
          }}
        >
          {done ? (kind === "check" ? "Run the check again" : `Calibrate ${lcFirst(STEP_META[kind].title)} again`) : `Start ${lcFirst(meta.title)}`}
        </Button>
        <span className="text-sm text-muted">
          {done
            ? kind === "check"
              ? "Run it before an important scan, or after a knock."
              : "Your saved calibration stays until you save a new one."
            : setup.target === "sim"
              ? "The simulated board moves by itself: watch the guidance at work."
              : "You can stop at any time; captured views are kept."}
        </span>
      </div>
      <StepFooter page={kind} />
    </StepPage>
  );
}

function FailedBanner({ kind }: { kind: CalibKind }) {
  const result = useCalib((s) => (s.session?.state === "capturing" && s.session.result?.grade === "failed" ? s.session.result : null));
  const [hidden, setHidden] = useState<unknown>(null);
  if (!result || hidden === result) return null;
  return (
    <div className="mb-3 animate-pop-in rounded-xl border border-warn/40 bg-warn-soft p-4 shadow-sm" role="alert">
      <div className="flex items-start gap-3">
        <TriangleExclamationPoint size={20} className="mt-0.5 shrink-0 text-warn" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-fg">Not yet: here's why</div>
          <div className="mt-0.5 text-sm text-fg">{sentence(result.headline.replace(/^not yet\s*[:,–-]?\s*/i, ""))}</div>
          <p className="mt-0.5 text-xs text-muted">{sentence(result.verdict)}</p>
          {result.advice.length ? (
            <div className="mt-2.5">
              <AdviceList advice={result.advice} kind={kind} compact />
            </div>
          ) : null}
        </div>
        <IconButton icon={X} label="Hide this" size="sm" onClick={() => setHidden(result)} />
      </div>
    </div>
  );
}

function SolvingOverlay() {
  const jobId = useCalib((s) => s.session?.job_id ?? null);
  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));
  const cancelJob = useJobs((s) => s.cancel);
  return (
    <div className="absolute inset-0 z-20 grid animate-fade-in place-items-center bg-overlay backdrop-blur-[3px]">
      <div className="w-[420px] max-w-[calc(100%-2rem)] rounded-2xl border border-line-strong bg-panel p-6 shadow-lg" role="status" aria-live="polite">
        <div className="flex items-center gap-3">
          <Spinner size={22} className="text-accent" />
          <div className="min-w-0 flex-1">
            <div className="text-base font-semibold text-fg">Calibrating…</div>
            <div className="truncate text-sm text-muted">{job?.message || "Working on the captured views"}</div>
          </div>
          {job ? <span className="font-mono text-sm text-fg tnum">{Math.round(job.progress * 100)}%</span> : null}
        </div>
        <ProgressBar className="mt-4" value={job && job.state !== "queued" ? job.progress : null} label="Calibration progress" />
        {job && isActive(job) ? (
          <div className="mt-4 flex justify-end">
            <Button size="sm" variant="ghost" onClick={() => void cancelJob(job.id)}>
              Cancel
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** The click on each capture (if not muted). */
function useCaptureSound() {
  useEffect(
    () =>
      useCalib.subscribe((s, p) => {
        if (s.captureSeq !== p.captureSeq && useUi.getState().captureSound) playCaptureClick();
      }),
    [],
  );
}

function SafetyNote() {
  const text = useCalib((s) => s.session?.emitters ?? "");
  const kind = useCalib((s) => s.session?.kind);
  if ((kind !== "lasers" && kind !== "check") || !/laser/i.test(text) || /stay off|need/i.test(text)) return null;
  return (
    <div className="flex items-center gap-2 border-b border-warn/30 bg-warn-soft px-4 py-1.5 text-xs text-fg" role="note">
      <Zap size={13} className="shrink-0 text-warn" aria-hidden />
      <span className="font-medium">{sentence(text)}</span>
      <span className="text-muted">Keep them pointed at the board.</span>
    </div>
  );
}

function CaptureScreen({ kind }: { kind: CalibKind }) {
  const state = useCalib((s) => s.session?.state);
  const guidance = useCalib((s) => s.session?.guidance ?? "");
  const n = useCalib((s) => s.session?.views.length ?? 0);
  const min = useCalib((s) => s.session?.min_views ?? 1);
  const capture = useCalib((s) => s.capture);
  const undo = useCalib((s) => s.undoLast);
  const solve = useCalib((s) => s.solve);
  const cancel = useCalib((s) => s.cancel);
  const [confirm, setConfirm] = useState(false);
  const [heatmap, setHeatmap] = useState(true);
  const capturing = state === "capturing";
  const lasers = kind === "lasers" || kind === "check";
  const Icon = STEP_META[kind].icon;
  useCaptureSound();

  const finish = () => {
    if (n >= min) void solve();
  };
  useHotkeys(
    {
      space: () => {
        unlockAudio();
        void capture();
      },
      enter: finish,
      backspace: () => void undo(),
      escape: () => setConfirm(true),
    },
    capturing,
  );

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-panel px-4">
        <Icon size={17} className="shrink-0 text-accent" aria-hidden />
        <h1 className="shrink-0 text-sm font-semibold text-fg">{KIND_META[kind].title}</h1>
        <span className="hidden min-w-0 flex-1 truncate text-xs text-muted md:block" title={guidance}>
          {sentence(guidance)}
        </span>
        <span className="flex-1 md:hidden" />
        <SimChip />
        <IconButton icon={Grid3x3} label={heatmap ? "Hide the coverage map" : "Show the coverage map"} size="sm" active={heatmap} onClick={() => setHeatmap((h) => !h)} />
        <SoundToggle />
      </header>
      <SafetyNote />
      <div className="flex min-h-0 flex-1">
        <div className="relative flex min-w-0 flex-1 flex-col px-4 pt-3">
          <FailedBanner kind={kind} />
          <Stage kind={kind} heatmap={heatmap} />
          {state === "solving" ? <SolvingOverlay /> : null}
        </div>
        <aside className="flex w-[300px] shrink-0 flex-col border-l border-line bg-panel 2xl:w-[340px]" aria-label="Calibration panel">
          <div className="min-h-0 flex-1 overflow-y-auto">
            <ViewsCounter kind={kind} />
            {lasers ? <LaserStat /> : null}
            <CoverageStat kind={kind} />
            <DiversityChips kind={kind} />
            <BoardStat kind={kind} />
          </div>
          <div className="border-t border-line">
            <CaptureControls kind={kind} onFinish={finish} onCancel={() => setConfirm(true)} />
          </div>
        </aside>
      </div>
      <ViewsStrip kind={kind} editable={capturing} />
      <ConfirmDialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title={`Cancel the ${lcFirst(KIND_META[kind].title)}?`}
        confirmLabel="Cancel calibration"
        tone="danger"
        onConfirm={() => cancel()}
      >
        {n ? `The ${n} captured view${n === 1 ? "" : "s"} stay on disk, but this session ends.` : "This session ends."} Your saved calibration doesn't change.
      </ConfirmDialog>
    </div>
  );
}

export function CaptureStep({ kind }: { kind: CalibKind }) {
  const session = useCalib((s) => s.session);
  const loaded = useCalib((s) => s.loaded);
  const fetchError = useCalib((s) => s.fetchError);
  if (!loaded) {
    return (
      <StepPage wide>
        <Skeleton className="h-8 w-72" />
        <Skeleton className="mt-6 h-64 w-full" />
      </StepPage>
    );
  }
  if (fetchError && !session) {
    return (
      <StepPage>
        <StepHeader page={kind} />
        <ErrorCallout title="Couldn't read the calibration session" err={fetchError} here={kind} onRetry={() => void useCalib.getState().fetch()} />
      </StepPage>
    );
  }
  const st = session?.state ?? "idle";
  const mine = session?.kind === kind;
  if (mine && (st === "result" || st === "saved")) return <ResultView kind={kind} />;
  if (mine && kind === "imu" && (st === "armed" || st === "recording" || st === "capturing" || st === "solving")) return <ImuScreen />;
  if (mine && (st === "capturing" || st === "solving")) return <CaptureScreen kind={kind} />;
  return <Intro kind={kind} other={!mine && isSessionActive(session) ? session : null} />;
}

