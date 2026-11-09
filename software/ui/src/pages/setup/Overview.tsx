/** The setup overview (`#/setup`): progress, "Continue setup", every step, the calibration file, the CLI. */
import {
  ArrowRight,
  ClipboardCheck,
  FileCog,
  Gauge,
  RotateCcw,
  ScanLine,
  Terminal,
  TriangleExclamationPoint,
} from "lucide-react";
import type { SetupStep } from "../../api/types";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Spinner } from "../../components/ui/Feedback";
import { Switch } from "../../components/ui/Form";
import { Callout, KeyValue, Section } from "../../components/ui/Layout";
import { fmtDateTime, fmtFixed, fmtRelative } from "../../lib/format";
import { navigate, paths } from "../../lib/router";
import { firstUnfinished, minutesLeft, setupProgress, STATE_LABEL, STEP_META, STEP_ORDER } from "../../lib/setup";
import { cn, useTicker } from "../../lib/util";
import { isSessionActive, useCalib } from "../../store/calib";
import { useDevice } from "../../store/device";
import { useSetup } from "../../store/setup";
import { CopyCommand, Disclosure, SimChip, STATE_TONE, StepDot, StepPage } from "./common";

function BigRing({ done, total }: { done: number; total: number }) {
  const size = 92;
  const r = size / 2 - 6;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative grid shrink-0 place-items-center" style={{ width: size, height: size }} role="img" aria-label={`${done} of ${total} steps done`}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--c-surface-3)" strokeWidth="8" />
        {done > 0 ? (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke="var(--c-ok)"
            strokeWidth="8"
            strokeLinecap="round"
            strokeDasharray={`${(c * done) / Math.max(1, total)} ${c}`}
            className="transition-[stroke-dasharray] duration-700"
          />
        ) : null}
      </svg>
      <div className="absolute text-center">
        <div className="font-display text-2xl font-semibold leading-none text-fg tnum">{done}</div>
        <div className="mt-0.5 text-[10.5px] text-subtle">of {total}</div>
      </div>
    </div>
  );
}

function StepCard({ step, index }: { step: SetupStep; index: number }) {
  const meta = STEP_META[step.id];
  const live = useCalib((s) => isSessionActive(s.session) && s.session?.kind === step.id);
  const verb = live ? "Continue" : step.state === "done" ? "Redo" : step.state === "warn" ? "Fix" : step.state === "optional" ? "Open" : "Start";
  return (
    <li>
      <a
        href={`#${paths.setup(step.id)}`}
        className={cn(
          "group flex h-full items-start gap-3.5 rounded-xl border bg-surface p-4 shadow-sm transition-[border-color,box-shadow,transform] duration-150",
          "hover:-translate-y-px hover:border-line-strong hover:shadow-md",
          step.state === "warn" ? "border-warn/35" : "border-line",
        )}
        aria-label={`${meta.title}: ${STATE_LABEL[step.state]}. ${step.summary}`}
      >
        <StepDot state={step.state} n={index + 1} size={30} busy={live} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-fg">{meta.title}</span>
            <Badge tone={live ? "accent" : STATE_TONE[step.state]} dot={!live}>
              {live ? "In progress" : STATE_LABEL[step.state]}
            </Badge>
            <span className="text-[11px] text-subtle">{meta.time}</span>
          </div>
          <p className="mt-1 text-sm text-muted">{step.summary || meta.why}</p>
          {step.when ? <p className="mt-0.5 text-xs text-subtle" title={fmtDateTime(step.when)}>{fmtRelative(step.when)}</p> : null}
        </div>
        <span className="mt-0.5 inline-flex shrink-0 items-center gap-1 text-xs font-medium text-accent opacity-80 transition-opacity group-hover:opacity-100">
          {verb} <ArrowRight size={13} aria-hidden />
        </span>
      </a>
    </li>
  );
}

const CLI: { text: string; cmd: string }[] = [
  { text: "Find the camera and the Pico", cmd: "ohs devices" },
  { text: "Check the hardware", cmd: "ohs selftest" },
  { text: "Make the board, the tags and the projector mask (print at 100 %)", cmd: "ohs printables print/" },
  { text: "Stereo: 15–25 views, 200–500 mm, tilted 20–45°", cmd: "ohs calibrate stereo" },
  { text: "Lasers: 6–10 poses, both lines on the white margin", cmd: "ohs calibrate lasers" },
  { text: "IMU: hold still 2 s, then rotate for 20–60 s", cmd: "ohs calibrate imu" },
  { text: "Check: 3–5 views, reports and never writes", cmd: "ohs calibrate check" },
];

export function Overview() {
  const setup = useSetup((s) => s.setup)!;
  const dismiss = useSetup((s) => s.dismiss);
  const launch = useDevice((s) => s.device?.launch_check ?? null) ?? setup.launch_check;
  useTicker(60_000);
  const prog = setupProgress(setup);
  const next = firstUnfinished(setup);
  const allDone = next === "done";
  const mins = minutesLeft(setup);
  const cal = setup.calibration;

  return (
    <StepPage wide>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="mb-1.5 text-xs font-medium text-subtle">Setup</div>
          <h1 className="font-display text-2xl font-semibold tracking-[-0.01em] text-fg">Set up your scanner</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted">
            Seven short steps turn the parts on your bench into a measuring tool. Every step says why it matters, and any one of them can be redone on
            its own.
          </p>
        </div>
        <SimChip />
      </div>

      <div className="mb-6 overflow-hidden rounded-2xl border border-line bg-surface shadow-sm">
        <div className="flex flex-wrap items-center gap-6 p-6">
          <BigRing done={prog.done} total={prog.total} />
          <div className="min-w-[260px] flex-1">
            <div className="font-display text-xl font-semibold text-fg">
              {allDone ? "Everything is set up" : `${prog.total - prog.done} step${prog.total - prog.done === 1 ? "" : "s"} to go`}
              {!allDone && mins ? <span className="font-normal text-muted"> · about {mins} min</span> : null}
            </div>
            <p className="mt-1 text-sm text-muted">
              {allDone ? (
                "The scanner is calibrated. Run the 2-minute check before an important scan, or after a knock or a hot day in the car."
              ) : (
                <>
                  Next: <b className="font-medium text-fg">{STEP_META[next].title}</b>. {STEP_META[next].why}
                </>
              )}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {allDone ? (
              <>
                <Button variant="primary" size="lg" icon={ScanLine} onClick={() => navigate(paths.setup("done"))}>
                  Scan your first object
                </Button>
                <Button size="lg" icon={ClipboardCheck} onClick={() => navigate(paths.setup("check"))}>
                  Run the check
                </Button>
              </>
            ) : (
              <Button variant="primary" size="xl" iconRight={ArrowRight} onClick={() => navigate(paths.setup(next))}>
                {prog.done === 0 ? "Start setup" : "Continue setup"}
              </Button>
            )}
          </div>
        </div>
        {launch && launch.state !== "unknown" ? (
          <div
            className={cn(
              "flex flex-wrap items-center gap-2.5 border-t px-6 py-3 text-sm",
              launch.state === "drift" ? "border-warn/30 bg-warn-soft" : "border-line bg-surface-2/50",
            )}
            role={launch.state === "drift" ? "alert" : "status"}
          >
            {launch.state === "running" ? (
              <>
                <Spinner size={14} className="text-accent" />
                <span className="text-muted">Launch check: comparing the left and right images…</span>
              </>
            ) : launch.state === "ok" ? (
              <>
                <Gauge size={15} className="text-ok" aria-hidden />
                <span className="text-muted">
                  Launch check: the calibration holds
                  {launch.dv_px !== null ? ` (rows match to ${fmtFixed(launch.dv_px, 2)} px)` : ""}, {fmtRelative(launch.when)}.
                </span>
              </>
            ) : (
              <>
                <TriangleExclamationPoint size={15} className="text-warn" aria-hidden />
                <span className="flex-1 text-fg">
                  <b className="font-semibold">The calibration may have drifted.</b>{" "}
                  <span className="text-muted">{launch.message ?? "Left and right image rows disagree."}</span>
                </span>
                <Button size="sm" variant="primary" icon={ClipboardCheck} onClick={() => navigate(paths.setup("check"))}>
                  Run the 2-minute check
                </Button>
              </>
            )}
          </div>
        ) : null}
      </div>

      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-subtle">The steps</h2>
      <ol className="mb-8 grid gap-3 lg:grid-cols-2">
        {STEP_ORDER.map((id, i) => {
          const st = setup.steps.find((s) => s.id === id) ?? { id, state: "todo" as const, summary: "", when: null };
          return <StepCard key={id} step={st} index={i} />;
        })}
      </ol>

      <div className="grid gap-5 pb-12 lg:grid-cols-[1.2fr_1fr]">
        <Section title="Calibration file" icon={FileCog} description={setup.target === "sim" ? "The simulator's sandbox" : "Used by the scanner"}>
          <KeyValue
            rows={[
              [
                "File",
                <span className="break-all font-mono text-xs" title={cal.path}>
                  {cal.path}
                  {!cal.exists ? <span className="ml-1.5 font-sans text-subtle">(not written yet)</span> : null}
                </span>,
              ],
              cal.modified ? ["Saved", <span title={fmtDateTime(cal.modified)}>{fmtRelative(cal.modified)}</span>] : null,
              cal.backup ? ["Previous version", <span className="break-all font-mono text-xs">{cal.backup}</span>] : null,
              [
                "Parts",
                <span className="flex flex-wrap gap-1.5">
                  {(["stereo", "lasers", "imu"] as const).map((k) => {
                    const p = cal.parts[k];
                    return (
                      <Badge key={k} tone={p ? "ok" : "neutral"} dot title={p ? `${p.summary}, ${fmtDateTime(p.when)}` : "Not calibrated"}>
                        {k === "imu" ? "IMU" : k.charAt(0).toUpperCase() + k.slice(1)}
                      </Badge>
                    );
                  })}
                </span>,
              ],
              cal.image_size ? ["Image size", `${cal.image_size[0]} × ${cal.image_size[1]} px`] : null,
              cal.baseline_mm !== null ? ["Baseline", `${fmtFixed(cal.baseline_mm, 2)} mm`] : null,
              ["Board", <span className="text-xs">{setup.board.describe}{setup.board.scale !== 1 ? `, measured scale ${fmtFixed(setup.board.scale * 100, 2)} %` : ""}</span>],
            ]}
          />
        </Section>

        <div className="space-y-4">
          <Section title="Reminder" icon={RotateCcw}>
            <Switch
              checked={!setup.dismissed}
              onChange={(v) => void dismiss(!v)}
              label="Show the setup card on Home"
              description="Until the stereo calibration is done, Home shows a card that brings you back here."
            />
          </Section>
          <Disclosure title="Advanced: the same steps from the command line" icon={Terminal}>
            <p className="mb-3 text-xs text-muted">
              Every step also runs in a terminal with the scanner plugged in. In the capture window, Space or the trigger captures a view, q finishes
              and calibrates, Esc aborts. Reconnect the device in Studio afterwards to load the new calibration.
            </p>
            <ol className="space-y-2.5">
              {CLI.map((c, i) => (
                <li key={c.cmd}>
                  <div className="mb-1 text-xs text-fg">
                    <span className="mr-1.5 font-semibold text-accent">{i + 1}</span>
                    {c.text}
                  </div>
                  <CopyCommand cmd={c.cmd} />
                </li>
              ))}
            </ol>
          </Disclosure>
          {setup.target === "sim" ? (
            <Callout tone="accent" icon={ScanLine} title="Calibrating the simulator">
              Practise the whole wizard here: the virtual scanner has a slightly distorted lens, and every result is compared with its true
              calibration. Connect the camera to calibrate the real scanner.
            </Callout>
          ) : null}
        </div>
      </div>
    </StepPage>
  );
}
