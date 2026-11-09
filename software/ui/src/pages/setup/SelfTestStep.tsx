/** Step 2, Self-test: one job checks the camera, the Pico and IMU, the emitters and the trigger. */
import {
  CircleCheck,
  CircleX,
  Hand,
  Info,
  ListChecks,
  Play,
  RotateCcw,
  SkipForward,
  TriangleExclamationPoint,
  Wrench,
  Zap,
} from "lucide-react";
import type { CheckStatus, Job, SelfTestCheck, SelfTestResult } from "../../api/types";
import { Badge, type BadgeTone } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ProgressBar } from "../../components/ui/Feedback";
import { Callout } from "../../components/ui/Layout";
import { RichText } from "../../components/ui/RichText";
import { sentence } from "../../api/http";
import { fmtDateTime, fmtRelative } from "../../lib/format";
import { navigate, paths } from "../../lib/router";
import { checkLabel, groupChecks, isInstruction, STATUS_RANK } from "../../lib/setup";
import { cn, useTicker } from "../../lib/util";
import { useDevice } from "../../store/device";
import { isActive, useJobs } from "../../store/jobs";
import { isLive, useSession } from "../../store/session";
import { useSetup } from "../../store/setup";
import { Disclosure, StepFooter, StepHeader, StepPage } from "./common";

const STATUS_STYLE: Record<CheckStatus, { tone: BadgeTone; icon: typeof CircleCheck; label: string }> = {
  PASS: { tone: "ok", icon: CircleCheck, label: "Pass" },
  WARN: { tone: "warn", icon: TriangleExclamationPoint, label: "Warning" },
  FAIL: { tone: "danger", icon: CircleX, label: "Fail" },
  SKIP: { tone: "neutral", icon: SkipForward, label: "Skipped" },
  INFO: { tone: "accent", icon: Info, label: "Info" },
};

export function StatusChip({ status }: { status: CheckStatus }) {
  const s = STATUS_STYLE[status] ?? STATUS_STYLE.INFO;
  return (
    <Badge tone={s.tone} icon={s.icon} className="min-w-[64px] justify-center">
      {status}
    </Badge>
  );
}

function CheckRow({ c }: { c: SelfTestCheck }) {
  return (
    <li className="py-3 first:pt-0 last:pb-0">
      <div className="flex items-start gap-3">
        <StatusChip status={c.status} />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-fg">{checkLabel(c.name)}</div>
          <div className="mt-0.5 text-xs text-muted">
            <RichText text={sentence(c.detail)} />
          </div>
          {c.fix ? (
            <Disclosure title="How to fix" icon={Wrench} size="sm" className="mt-2" tone={c.status === "FAIL" || c.status === "WARN" ? "warn" : "neutral"} defaultOpen={c.status === "FAIL"}>
              <RichText text={sentence(c.fix)} />
            </Disclosure>
          ) : null}
        </div>
      </div>
    </li>
  );
}

function Results({ r }: { r: SelfTestResult }) {
  useTicker(30_000);
  const counts = { PASS: 0, WARN: 0, FAIL: 0, SKIP: 0, INFO: 0 } as Record<CheckStatus, number>;
  for (const c of r.checks) counts[c.status] = (counts[c.status] ?? 0) + 1;
  const groups = groupChecks(r.checks).map((g) => ({ ...g, checks: [...g.checks].sort((a, b) => STATUS_RANK[a.status] - STATUS_RANK[b.status]) }));
  const overallTone = r.overall === "PASS" ? "ok" : r.overall === "WARN" ? "warn" : "danger";
  return (
    <section aria-label="Self-test results" className="overflow-hidden rounded-xl border border-line bg-surface shadow-sm">
      <header className={cn("flex flex-wrap items-center gap-3 border-b px-5 py-4", overallTone === "ok" ? "border-ok/25 bg-ok-soft/50" : overallTone === "warn" ? "border-warn/25 bg-warn-soft/50" : "border-danger/25 bg-danger-soft/50")}>
        {r.overall === "PASS" ? (
          <CircleCheck size={26} className="text-ok" aria-hidden />
        ) : r.overall === "WARN" ? (
          <TriangleExclamationPoint size={26} className="text-warn" aria-hidden />
        ) : (
          <CircleX size={26} className="text-danger" aria-hidden />
        )}
        <div className="min-w-0 flex-1">
          <div className="text-base font-semibold text-fg">
            {r.overall === "PASS" ? "Everything works" : r.overall === "WARN" ? "Works, with warnings" : "Something needs fixing"}
          </div>
          <div className="text-xs text-muted" title={fmtDateTime(r.when)}>
            {r.source === "sim" ? "Simulator" : r.source === "replay" ? "Replay" : "Scanner"} · {fmtRelative(r.when)}
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5" aria-label="Summary">
          {(["PASS", "WARN", "FAIL", "SKIP"] as const).map((k) =>
            counts[k] ? (
              <Badge key={k} tone={STATUS_STYLE[k].tone}>
                {counts[k]} {k === "PASS" ? "passed" : k === "WARN" ? (counts[k] > 1 ? "warnings" : "warning") : k === "FAIL" ? "failed" : "skipped"}
              </Badge>
            ) : null,
          )}
        </div>
      </header>
      <div className="grid gap-x-8 gap-y-5 p-5 lg:grid-cols-2">
        {groups.map((g) => (
          <div key={g.key}>
            <h3 className="mb-2.5 text-xs font-semibold uppercase tracking-wider text-subtle">{g.title}</h3>
            <ul className="divide-y divide-line">
              {g.checks.map((c, i) => (
                <CheckRow key={`${c.name}-${i}`} c={c} />
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

function Running({ job }: { job: Job }) {
  const msg = job.message || "Starting…";
  const instruction = isInstruction(msg);
  const trigger = /trigger/i.test(msg);
  const laser = /laser|projector|emitter/i.test(msg);
  return (
    <div className="space-y-4" role="status" aria-live="assertive">
      <div
        className={cn(
          "relative overflow-hidden rounded-2xl border p-6 transition-colors",
          instruction ? "border-accent bg-accent-soft shadow-[0_0_0_4px_var(--c-accent-soft)]" : "border-line bg-surface",
        )}
      >
        {instruction ? <span className="pointer-events-none absolute inset-0 animate-pulse rounded-2xl bg-accent/5" aria-hidden /> : null}
        <div className="relative flex items-center gap-5">
          <span
            className={cn(
              "grid h-16 w-16 shrink-0 place-items-center rounded-2xl border",
              instruction ? "border-accent bg-accent text-accent-fg" : "border-line bg-surface-2 text-accent",
              instruction && trigger && "animate-bounce",
            )}
          >
            {trigger ? <Hand size={28} aria-hidden /> : laser ? <Zap size={28} aria-hidden /> : <ListChecks size={28} aria-hidden />}
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-xs font-semibold uppercase tracking-wider text-subtle">{instruction ? "Your turn" : "Testing"}</div>
            <div className={cn("mt-1 font-display font-semibold text-fg", instruction ? "text-2xl" : "text-xl")}>{msg}</div>
          </div>
          <span className="font-mono text-lg text-fg tnum">{Math.round(job.progress * 100)}%</span>
        </div>
        <ProgressBar className="relative mt-5" value={job.state === "queued" ? null : job.progress} label="Self-test progress" />
      </div>
      {laser ? (
        <Callout tone="warn" icon={Zap} title="Class 3R lasers">
          Point the scanner at a wall 20–40 cm away and never look into the beams.
        </Callout>
      ) : null}
    </div>
  );
}

export function SelfTestStep() {
  const device = useDevice((s) => s.device);
  const jobId = useSetup((s) => s.selftestJobId);
  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));
  const starting = useSetup((s) => s.selftestStarting);
  const run = useSetup((s) => s.runSelftest);
  const last = useSetup((s) => s.selftestResult ?? s.setup?.selftest ?? null);
  const scanning = useSession((s) => isLive(s.session));
  const cancel = useJobs((s) => s.cancel);
  const connected = !!device?.connected;
  const running = isActive(job);
  const replay = device?.source === "replay";
  const sim = connected && device?.source === "sim";

  return (
    <StepPage>
      <StepHeader page="selftest" />
      {!connected ? (
        <Callout
          tone="warn"
          icon={TriangleExclamationPoint}
          title="Connect the scanner first"
          className="mb-5"
          actions={
            <Button size="sm" variant="primary" onClick={() => navigate(paths.setup("connect"))}>
              Go to Connect
            </Button>
          }
        >
          The self-test talks to the camera and the Pico, or to the simulator.
        </Callout>
      ) : replay ? (
        <Callout tone="warn" icon={TriangleExclamationPoint} title="A replay is connected" className="mb-5" actions={<Button size="sm" onClick={() => navigate(paths.setup("connect"))}>Go to Connect</Button>}>
          The self-test needs the scanner or the simulator.
        </Callout>
      ) : null}

      {running && job ? (
        <div className="mb-5">
          <Running job={job} />
          <div className="mt-3 flex justify-end">
            <Button size="sm" variant="ghost" onClick={() => void cancel(job.id)}>
              Cancel the self-test
            </Button>
          </div>
        </div>
      ) : (
        <div className="mb-5 flex flex-wrap items-center gap-4 rounded-xl border border-line bg-surface p-5 shadow-sm">
          <div className="min-w-[260px] flex-1">
            <div className="text-sm font-semibold text-fg">
              {last ? "Run it again after any fix" : sim ? "A few seconds on the simulator" : "About a minute, with two prompts"}
            </div>
            <p className="mt-0.5 text-sm text-muted">
              {sim
                ? "It checks the simulated camera stream and the IMU. The Pico, the emitters and the trigger are skipped: the simulator has none."
                : "Point the scanner at a wall 20–40 cm away. Each laser and the dot projector turns on for a second (watch the wall), then it asks you to press the trigger."}
            </p>
          </div>
          <Button
            variant="primary"
            size="lg"
            icon={last ? RotateCcw : Play}
            loading={starting}
            disabled={!connected || replay || scanning}
            onClick={() => void run()}
            title={scanning ? "Stop the scan first" : undefined}
          >
            {last ? "Run the self-test again" : "Run self-test"}
          </Button>
        </div>
      )}

      {last && !running ? <Results r={last} /> : null}
      <StepFooter page="selftest" />
    </StepPage>
  );
}
