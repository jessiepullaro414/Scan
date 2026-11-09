/** Done: a summary of every part, "Scan your first object", and the 2-minute check. */
import { ArrowRight, ClipboardCheck, PartyPopper, ScanLine, TriangleExclamationPoint } from "lucide-react";
import { useState } from "react";
import { Button } from "../../components/ui/Button";
import { Callout } from "../../components/ui/Layout";
import { fmtDateTime, fmtRelative } from "../../lib/format";
import { href, navigate, paths } from "../../lib/router";
import { firstUnfinished, STATE_LABEL, STEP_META, STEP_ORDER } from "../../lib/setup";
import { cn, useTicker } from "../../lib/util";
import { useDevice } from "../../store/device";
import { useSetup } from "../../store/setup";
import { FIRST_PROJECT, startFirstScan } from "./actions";
import { StepDot, StepFooter, StepHeader, StepPage } from "./common";

/** A mug on the bench with the scanner's path around it. */
function MugArt() {
  return (
    <svg viewBox="0 0 260 170" className="h-auto w-full max-w-[300px]" aria-hidden>
      <ellipse cx="130" cy="140" rx="96" ry="18" fill="var(--c-surface-3)" />
      <path d="M44 96 A90 40 0 0 0 216 96" stroke="var(--c-accent)" strokeWidth="2.5" strokeDasharray="7 6" fill="none" />
      <path d="M212 86 L222 98 L206 102 Z" fill="var(--c-accent)" />
      <g>
        <rect x="98" y="62" width="64" height="74" rx="8" fill="#e9e4da" />
        <ellipse cx="130" cy="62" rx="32" ry="8" fill="#d8d1c4" />
        <ellipse cx="130" cy="62" rx="26" ry="5.5" fill="#6b4a2e" />
        <path d="M162 78 C184 78 184 112 162 112" stroke="#e9e4da" strokeWidth="9" fill="none" strokeLinecap="round" />
        <path d="M104 86 L104 128" stroke="rgb(0 0 0 / 0.08)" strokeWidth="8" strokeLinecap="round" />
      </g>
      <g transform="translate(40 34) rotate(18)">
        <rect x="-22" y="-10" width="44" height="20" rx="6" fill="var(--c-surface-2)" stroke="var(--c-accent)" strokeWidth="2" />
        <circle cx="-10" cy="0" r="4" fill="var(--c-bg)" stroke="var(--c-accent)" strokeWidth="1.5" />
        <circle cx="10" cy="0" r="4" fill="var(--c-bg)" stroke="var(--c-accent)" strokeWidth="1.5" />
      </g>
      <path d="M60 50 L100 82" stroke="#ff5a4f" strokeWidth="1.5" opacity="0.8" />
      <text x="70" y="160" fontSize="10" fill="var(--c-fg-subtle)">about 25 cm</text>
    </svg>
  );
}

export function DoneStep() {
  const setup = useSetup((s) => s.setup)!;
  const device = useDevice((s) => s.device);
  const [busy, setBusy] = useState(false);
  useTicker(60_000);
  const allDone = firstUnfinished(setup) === "done";
  const canScan = !!device?.connected && !!device.calibration?.loaded;
  const parts = setup.calibration.parts;

  return (
    <StepPage wide>
      <StepHeader
        page="done"
        title={allDone ? "Your scanner is ready" : "Almost there"}
        why={allDone ? "Everything the scanner needs is in place." : "What's set up so far, and your first scan."}
      />
      {!allDone ? (
        <Callout
          tone="warn"
          icon={TriangleExclamationPoint}
          title="Some steps are still open"
          className="mb-5"
          actions={
            <Button size="sm" variant="primary" iconRight={ArrowRight} onClick={() => navigate(paths.setup(firstUnfinished(setup)))}>
              Continue with {STEP_META[firstUnfinished(setup)].title}
            </Button>
          }
        >
          You can scan already if the stereo step is done; the others make scans better.
        </Callout>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-[1.25fr_1fr]">
        <section className="overflow-hidden rounded-2xl border border-line bg-surface shadow-sm" aria-label="Scan your first object">
          <div className="flex flex-wrap items-center gap-6 p-6">
            <MugArt />
            <div className="min-w-[240px] flex-1">
              <div className="mb-2 inline-flex items-center gap-1.5 rounded-full border border-accent-line/60 bg-accent-soft px-2.5 py-1 text-xs font-medium text-accent">
                <PartyPopper size={13} aria-hidden /> {allDone ? "All set" : "Ready when you are"}
              </div>
              <h2 className="font-display text-2xl font-semibold text-fg">Scan your first object</h2>
              <p className="mt-1.5 text-sm text-muted">
                A mug or a coffee can is perfect: point the scanner at it from about 25 cm, press the trigger (or Start), and move slowly all the way
                around.
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                <Button
                  variant="primary"
                  size="xl"
                  icon={ScanLine}
                  loading={busy}
                  disabled={!canScan}
                  onClick={async () => {
                    setBusy(true);
                    await startFirstScan();
                    setBusy(false);
                  }}
                >
                  Scan your first object
                </Button>
              </div>
              {!canScan ? (
                <p className="mt-2 text-xs text-warn">
                  {device?.connected ? "Scanning needs the stereo calibration: finish the Stereo step first." : "Connect the scanner (or the simulator) first."}
                </p>
              ) : (
                <p className="mt-2 text-xs text-subtle">Creates a project called “{FIRST_PROJECT}” and opens the Scan page with a short guide.</p>
              )}
            </div>
          </div>
          <a
            href={href(paths.setup("check"))}
            className="flex items-center gap-3 border-t border-line bg-surface-2/50 px-6 py-3.5 text-sm transition-colors hover:bg-surface-2"
          >
            <ClipboardCheck size={17} className="text-accent" aria-hidden />
            <span className="flex-1">
              <b className="font-medium text-fg">Run a 2-minute check any time</b>
              <span className="text-muted"> before an important scan, or after a knock or a hot day in the car.</span>
            </span>
            <ArrowRight size={15} className="text-subtle" aria-hidden />
          </a>
        </section>

        <section className="rounded-2xl border border-line bg-surface p-5 shadow-sm" aria-label="Summary">
          <h2 className="mb-3 text-sm font-semibold text-fg">What's set up</h2>
          <ul className="space-y-3">
            {STEP_ORDER.map((id, i) => {
              const st = setup.steps.find((s) => s.id === id);
              const part = id === "stereo" || id === "lasers" || id === "imu" ? parts[id] : null;
              const when = part?.when ?? st?.when ?? null;
              return (
                <li key={id}>
                  <a href={href(paths.setup(id))} className="group flex items-start gap-3 rounded-lg p-1 -m-1 transition-colors hover:bg-surface-2">
                    <StepDot state={st?.state ?? "todo"} n={i + 1} size={24} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline gap-2">
                        <span className="text-sm font-medium text-fg">{STEP_META[id].title}</span>
                        <span className={cn("text-[11px]", st?.state === "warn" ? "text-warn" : "text-subtle")}>{st ? STATE_LABEL[st.state] : ""}</span>
                        {when ? (
                          <span className="ml-auto shrink-0 text-[11px] text-subtle" title={fmtDateTime(when)}>
                            {fmtRelative(when)}
                          </span>
                        ) : null}
                      </div>
                      <div className="truncate text-xs text-muted">{part?.summary ?? st?.summary ?? ""}</div>
                    </div>
                  </a>
                </li>
              );
            })}
          </ul>
          <p className="mt-4 border-t border-line pt-3 text-xs text-subtle">
            {setup.calibration.exists ? (
              <>
                Saved in <span className="font-mono">{setup.calibration.path}</span>
                {setup.calibration.modified ? `, ${fmtRelative(setup.calibration.modified)}` : ""}.
              </>
            ) : (
              "No calibration file yet."
            )}
          </p>
        </section>
      </div>
      <StepFooter page="done" />
    </StepPage>
  );
}
