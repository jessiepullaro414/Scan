/**
 * The result of a solve, in plain words: a verdict card per grade, the metric rows with what
 * each means, the advice with buttons, the simulator's comparison with the truth, and Save.
 * After saving: where it went, the notes ("the laser planes were dropped…") and the next step.
 */
import {
  ArrowRight,
  CircleCheck,
  CircleHelp,
  CircleX,
  ClipboardCheck,
  Download,
  FileCheck,
  FlaskConical,
  Lightbulb,
  Plus,
  RotateCcw,
  Save,
  ThumbsUp,
  TriangleExclamationPoint,
  Trophy,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import type { CalibAdvice, CalibAdviceAction, CalibGrade, CalibKind, CalibMetric } from "../../api/types";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/Dialog";
import { Callout, Code, Section } from "../../components/ui/Layout";
import { Tooltip } from "../../components/ui/Popover";
import { sentence } from "../../api/http";
import { downloadFile } from "../../lib/download";
import { navigate, paths } from "../../lib/router";
import { GRADE_LABEL, GRADE_TONE, KIND_META, nextPage, STEP_META } from "../../lib/setup";
import { cn } from "../../lib/util";
import { useCalib } from "../../store/calib";
import { useAdviceAction } from "./actions";
import { OkMark, StepHeader, StepPage } from "./common";
import { ViewsStrip } from "./capture/ViewsStrip";

const GRADE_ICON: Record<CalibGrade, LucideIcon> = {
  excellent: Trophy,
  good: CircleCheck,
  usable: ThumbsUp,
  poor: TriangleExclamationPoint,
  failed: CircleX,
};

const ACTION_LABEL: Record<CalibAdviceAction, string> = {
  add_views: "Add more views",
  remove_bad_views: "Drop and recalibrate",
  start_over: "Start over",
  recalibrate_stereo: "Redo the stereo step",
  reprint: "Go to Print targets",
  check_board: "Check the board",
};

export function AdviceList({ advice, kind, compact }: { advice: CalibAdvice[]; kind: CalibKind; compact?: boolean }) {
  const run = useAdviceAction(kind);
  const busy = useCalib((s) => s.action !== null);
  const state = useCalib((s) => s.session?.state);
  if (!advice.length) return null;
  return (
    <ul className={cn("space-y-2", compact && "space-y-1.5")}>
      {advice.map((a, i) => {
        // "Add more views" is already where we are when capturing
        const action = a.action === "add_views" && state === "capturing" ? null : a.action;
        return (
          <li key={i} className={cn("flex flex-wrap items-start gap-3 rounded-lg border border-line bg-surface-2/60", compact ? "px-3 py-2" : "p-3")}>
            <Lightbulb size={15} className="mt-0.5 shrink-0 text-warn" aria-hidden />
            <p className="min-w-[200px] flex-1 text-sm text-fg">{sentence(a.text)}</p>
            {action ? (
              <Button size="sm" variant={i === 0 ? "primary" : "secondary"} disabled={busy} onClick={() => run(action)}>
                {ACTION_LABEL[action]}
              </Button>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function MetricRow({ m }: { m: CalibMetric }) {
  return (
    <li className="flex items-start gap-3 py-2.5 first:pt-0 last:pb-0">
      <OkMark ok={m.ok} className="mt-0.5" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 text-sm text-fg">
          {m.label}
          {m.help ? (
            <Tooltip content={<div><div className="mb-0.5 font-semibold">What this means</div><div className="text-muted">{m.help}</div></div>}>
              <button type="button" className="grid h-5 w-5 place-items-center rounded text-subtle hover:text-fg" aria-label={`What “${m.label}” means: ${m.help}`}>
                <CircleHelp size={13} aria-hidden />
              </button>
            </Tooltip>
          ) : null}
        </div>
      </div>
      <div className={cn("text-right font-mono text-[13px] tnum", m.ok === false ? "text-warn" : "text-fg")}>{m.value}</div>
    </li>
  );
}

export function ResultView({ kind }: { kind: CalibKind }) {
  const session = useCalib((s) => s.session)!;
  const action = useCalib((s) => s.action);
  const save = useCalib((s) => s.save);
  const continueCapture = useCalib((s) => s.continueCapture);
  const startOver = useCalib((s) => s.startOver);
  const start = useCalib((s) => s.start);
  const cancel = useCalib((s) => s.cancel);
  const [confirm, setConfirm] = useState(false);
  const r = session.result;
  const saved = session.state === "saved" ? session.saved : null;
  const next = nextPage(kind);
  const isCheck = kind === "check";
  const grade: CalibGrade = r?.grade ?? "good";
  const tone = GRADE_TONE[grade];
  const Icon = GRADE_ICON[grade];
  const hasViews = kind !== "imu" && session.views.length > 0;

  const onSave = () => {
    if (!r) return;
    if (!r.ok) setConfirm(true);
    else void save(false);
  };

  return (
    <StepPage wide>
      <StepHeader page={kind} title={KIND_META[kind].title} />

      {r ? (
        <div
          className={cn(
            "mb-5 flex flex-wrap items-start gap-5 rounded-2xl border p-6 shadow-sm",
            tone === "ok" ? "border-ok/35 bg-ok-soft/60" : tone === "warn" ? "border-warn/35 bg-warn-soft/60" : tone === "danger" ? "border-danger/35 bg-danger-soft/60" : "border-line bg-surface",
          )}
          role="status"
        >
          <span
            className={cn(
              "grid h-16 w-16 shrink-0 place-items-center rounded-2xl",
              tone === "ok" ? "bg-ok text-white" : tone === "warn" ? "bg-warn text-black" : "bg-danger text-white",
            )}
          >
            <Icon size={30} aria-hidden />
          </span>
          <div className="min-w-[260px] flex-1">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <Badge tone={tone}>{isCheck ? (r.ok ? "Still holds" : "Drifted") : GRADE_LABEL[grade]}</Badge>
              {saved ? (
                <Badge tone="ok" icon={FileCheck}>
                  Saved
                </Badge>
              ) : null}
            </div>
            <h2 className="font-display text-2xl font-semibold tracking-[-0.01em] text-fg">{sentence(r.headline)}</h2>
            <p className="mt-1 max-w-3xl text-base text-muted">{sentence(r.verdict)}</p>
          </div>
        </div>
      ) : null}

      {saved ? (
        <div className="mb-5 rounded-xl border border-line bg-surface p-5 shadow-sm">
          <div className="flex flex-wrap items-start gap-4">
            <CircleCheck size={22} className="mt-0.5 shrink-0 text-ok" aria-hidden />
            <div className="min-w-[260px] flex-1">
              <div className="text-sm font-semibold text-fg">Saved to <Code>{saved.path}</Code></div>
              <div className="mt-0.5 text-xs text-muted">
                {saved.backup ? (
                  <>
                    The previous version is kept as <Code>{saved.backup}</Code>.
                  </>
                ) : (
                  "This is the first calibration file here."
                )}
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button icon={RotateCcw} onClick={() => void start(kind)} loading={action === "start"}>
                Calibrate again
              </Button>
              {next ? (
                <Button variant="primary" iconRight={ArrowRight} onClick={() => navigate(paths.setup(next))}>
                  Next step: {STEP_META[next].title}
                </Button>
              ) : null}
            </div>
          </div>
          {saved.notes.length ? (
            <ul className="mt-4 space-y-2">
              {saved.notes.map((n, i) => (
                <li key={i}>
                  <Callout
                    tone="warn"
                    icon={TriangleExclamationPoint}
                    title={sentence(n)}
                    actions={
                      /laser/i.test(n) ? (
                        <Button size="sm" onClick={() => navigate(paths.setup("lasers"))}>
                          Go to Lasers
                        </Button>
                      ) : /imu/i.test(n) ? (
                        <Button size="sm" onClick={() => navigate(paths.setup("imu"))}>
                          Go to IMU
                        </Button>
                      ) : undefined
                    }
                  />
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {r ? (
        <div className="grid gap-5 lg:grid-cols-[1.15fr_1fr]">
          <Section title="The numbers" icon={ClipboardCheck} description="Hover or focus the ? for what each one means">
            {r.metrics.length ? (
              <ul className="divide-y divide-line">
                {r.metrics.map((m, i) => (
                  <MetricRow key={`${m.label}-${i}`} m={m} />
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted">No numbers in this result.</p>
            )}
          </Section>
          <div className="space-y-5">
            {r.advice.length ? (
              <Section title={r.ok ? "To make it even better" : "What to do"} icon={Lightbulb}>
                <AdviceList advice={r.advice} kind={kind} />
              </Section>
            ) : (
              <Section title="Advice" icon={Lightbulb}>
                <p className="text-sm text-muted">{isCheck ? "Nothing to do: scan away." : saved ? "Nothing to improve." : "Nothing to improve. Save it."}</p>
              </Section>
            )}
            {r.sim_truth?.length ? (
              <Section title="Against the true calibration" icon={FlaskConical} description="Only the simulator knows the truth">
                <ul className="divide-y divide-line">
                  {r.sim_truth.map((m, i) => (
                    <MetricRow key={`${m.label}-${i}`} m={m} />
                  ))}
                </ul>
              </Section>
            ) : null}
          </div>
        </div>
      ) : (
        <Callout tone="neutral" icon={CircleHelp} title="No result to show">
          The session has no result. Start the step again.
        </Callout>
      )}

      {hasViews ? (
        <div className="mt-5 overflow-hidden rounded-xl border border-line">
          <ViewsStrip kind={kind} editable={false} className="border-t-0" />
        </div>
      ) : null}

      <div className="min-h-6 flex-1" aria-hidden />
      {!saved ? (
        <div className="sticky bottom-0 z-10 -mx-6 flex flex-wrap items-center gap-2 border-t border-line bg-bg/85 px-6 py-3 backdrop-blur xl:-mx-10 xl:px-10">
          {isCheck ? (
            <>
              <Button icon={RotateCcw} disabled={action !== null} onClick={() => void startOver()}>
                Run the check again
              </Button>
              {r ? (
                <Button variant="ghost" icon={Download} onClick={() => void downloadFile(r.report_url, "calibration_check_report.json")}>
                  Download full report
                </Button>
              ) : null}
              <div className="flex-1" />
              <Button
                variant="primary"
                iconRight={ArrowRight}
                loading={action === "cancel"}
                onClick={async () => {
                  if (await cancel()) navigate(paths.setup("done"));
                }}
              >
                Done
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" icon={RotateCcw} disabled={action !== null} onClick={() => void startOver()}>
                Start over
              </Button>
              {r ? (
                <Button variant="ghost" icon={Download} onClick={() => void downloadFile(r.report_url, `calibration_${kind}_report.json`)}>
                  Download full report
                </Button>
              ) : null}
              <div className="flex-1" />
              {kind !== "imu" ? (
                <Button icon={Plus} loading={action === "continue"} disabled={action !== null && action !== "continue"} onClick={() => void continueCapture()}>
                  Add more views
                </Button>
              ) : null}
              {r?.can_save ? (
                <Button variant={r.ok ? "primary" : "secondary"} size="lg" icon={Save} loading={action === "save"} disabled={action !== null && action !== "save"} onClick={onSave}>
                  Save calibration
                </Button>
              ) : null}
            </>
          )}
        </div>
      ) : null}

      <ConfirmDialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Save a calibration below the quality bar?"
        confirmLabel="Save anyway"
        tone="danger"
        onConfirm={() => save(true)}
      >
        It's graded {GRADE_LABEL[grade].toLowerCase()}: scans made with it may be visibly off. The previous file is kept as a backup, and you can redo this
        step any time. Following the advice usually takes a couple of minutes.
      </ConfirmDialog>
      {saved ? <div className="h-10" /> : null}
    </StepPage>
  );
}
