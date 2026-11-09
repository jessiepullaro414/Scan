/**
 * The setup and calibration wizard (`#/setup`, `#/setup/<step>`): the step rail on the left,
 * the current step on the right. The capture steps (stereo, lasers, IMU, check) get the room:
 * the rail shrinks to its dots there.
 */
import { CircleExclamationPoint, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "../../components/ui/Button";
import { EmptyState, Skeleton } from "../../components/ui/Feedback";
import { Code } from "../../components/ui/Layout";
import type { SetupPage as SetupPageId } from "../../lib/router";
import { isCapturePage } from "../../lib/setup";
import { useCalib } from "../../store/calib";
import { useSetup } from "../../store/setup";
import { CaptureStep } from "./CaptureStep";
import { ConnectStep } from "./ConnectStep";
import { DoneStep } from "./DoneStep";
import { Overview } from "./Overview";
import { PrintStep } from "./PrintStep";
import { SelfTestStep } from "./SelfTestStep";
import { StepRail } from "./StepRail";

function Unavailable() {
  const err = useSetup((s) => s.error);
  const loading = useSetup((s) => s.loading);
  const old = err?.status === 404;
  return (
    <div className="grid h-full place-items-center p-6">
      <div className="max-w-xl rounded-2xl border border-line bg-surface">
        <EmptyState
          tone={old ? "neutral" : "danger"}
          icon={CircleExclamationPoint}
          title={old ? "This engine has no setup wizard yet" : "Couldn't load the setup"}
          actions={
            <Button icon={RefreshCw} loading={loading} onClick={() => void useSetup.getState().fetch()}>
              Try again
            </Button>
          }
        >
          {old ? (
            <>
              The wizard needs OpenHandScan engine 0.2 or newer. Update it, then restart <Code>ohs app</Code>. Meanwhile{" "}
              <Code>ohs calibrate stereo</Code> works from a terminal.
            </>
          ) : (
            <>
              {err?.detail}
              {err?.hint ? <div className="mt-1 text-subtle">{err.hint}</div> : null}
            </>
          )}
        </EmptyState>
      </div>
    </div>
  );
}

function Loading() {
  return (
    <div className="mx-auto w-full max-w-[1040px] space-y-4 px-6 pt-6 xl:px-10" aria-busy="true" aria-label="Loading the setup">
      <Skeleton className="h-8 w-72" />
      <Skeleton className="h-4 w-[28rem]" />
      <Skeleton className="mt-6 h-40 w-full" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}

function StepView({ step }: { step: SetupPageId | null }) {
  switch (step) {
    case null:
      return <Overview />;
    case "connect":
      return <ConnectStep />;
    case "selftest":
      return <SelfTestStep />;
    case "print":
      return <PrintStep />;
    case "stereo":
    case "lasers":
    case "imu":
    case "check":
      return <CaptureStep kind={step} />;
    case "done":
      return <DoneStep />;
    default:
      return <Overview />;
  }
}

export default function SetupPage({ step }: { step: SetupPageId | null }) {
  const setup = useSetup((s) => s.setup);
  const error = useSetup((s) => s.error);
  const capture = isCapturePage(step);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    void useSetup.getState().fetch();
    void useCalib.getState().fetch();
  }, []);

  // Each capture step starts with the rail tucked away; the user can open it.
  useEffect(() => setExpanded(false), [step]);

  return (
    <div className="flex h-full">
      <StepRail current={step} compact={capture && !expanded} canToggle={capture} onToggle={() => setExpanded((e) => !e)} />
      <div className="relative min-w-0 flex-1 overflow-hidden">
        {error && !setup ? <Unavailable /> : !setup ? <Loading /> : <StepView step={step} />}
      </div>
    </div>
  );
}
