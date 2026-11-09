import {
  Aperture,
  ArrowRight,
  Check,
  ClipboardCheck,
  Copy,
  Crosshair,
  Gauge,
  Grid3x3,
  Orbit,
  Printer,
  RotateCcw,
  ScanEye,
  Sparkles,
  TriangleExclamationPoint,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Callout, Code, Page, PageHeader, Section } from "../components/ui/Layout";
import { navigate, paths } from "../lib/router";
import { cn, copyText } from "../lib/util";
import { useDevice } from "../store/device";
import { toast } from "../store/toasts";

type Step = {
  key: string;
  title: string;
  icon: LucideIcon;
  what: string;
  how: string;
  time: string;
  emitters: string;
  cli: string;
};

const STEPS: Step[] = [
  {
    key: "stereo",
    title: "Stereo",
    icon: Aperture,
    what: "Lens intrinsics and the exact camera baseline: the base of every millimetre the scanner measures.",
    how: "15–25 views of the ChArUco board at 200–500 mm, tilted 20–45°, covering the corners and edges.",
    time: "≈ 5 min",
    emitters: "Emitters off",
    cli: "ohs calibrate stereo",
  },
  {
    key: "lasers",
    title: "Lasers",
    icon: Zap,
    what: "The two laser planes, for the 0.07 mm reference points on dark and glossy paint.",
    how: "6–10 board poses at 150–450 mm, ±30° tilt, both lines crossing the white page margin.",
    time: "≈ 3 min",
    emitters: "Lines on",
    cli: "ohs calibrate lasers",
  },
  {
    key: "imu",
    title: "IMU",
    icon: Orbit,
    what: "Gyro bias, camera–IMU rotation and clock offset: steadier tracking on fast or featureless sweeps.",
    how: "Hold still for 2 s, then rotate about all axes for 20–60 s while looking at the board.",
    time: "≈ 1 min",
    emitters: "Emitters off",
    cli: "ohs calibrate imu",
  },
  {
    key: "check",
    title: "Check",
    icon: ClipboardCheck,
    what: "A quick verdict on whether the saved calibration still holds (knocks, temperature).",
    how: "3–5 views of the board with the lines on. Pass / fail with the reason.",
    time: "≈ 1 min",
    emitters: "Lines on",
    cli: "ohs calibrate check",
  },
];

const WIZARD_FEATURES: { icon: LucideIcon; title: string; text: string }[] = [
  { icon: Grid3x3, title: "Live coverage heatmap", text: "See which parts of the image still need board views." },
  { icon: ScanEye, title: "Auto-capture", text: "Views are taken when the board is steady and well placed." },
  { icon: Sparkles, title: "Plain-language verdicts", text: "Pass / fail with the reason and what to do next." },
  { icon: RotateCcw, title: "Retry a step", text: "Redo one step without starting over." },
  { icon: Gauge, title: "Launch-time check", text: "Silent unless the calibration has drifted." },
];

function CopyCommand({ cmd }: { cmd: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="flex items-center gap-2 rounded-lg border border-line bg-bg/60 py-1 pl-3 pr-1">
      <span className="select-none font-mono text-xs text-subtle" aria-hidden>
        &gt;
      </span>
      <code className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-fg">{cmd}</code>
      <Button
        size="xs"
        variant="ghost"
        icon={done ? Check : Copy}
        aria-label={`Copy “${cmd}”`}
        onClick={async () => {
          if (await copyText(cmd)) {
            setDone(true);
            setTimeout(() => setDone(false), 1500);
          } else toast.warn("Couldn't copy", "Select the command and copy it by hand.");
        }}
      >
        {done ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}

export function CalibratePage() {
  const device = useDevice((s) => s.device);
  const calib = device?.calibration;
  // Calibration status is only known for a connected device.
  const known = !!device?.connected && !!calib;
  const done: Record<string, boolean | null> = {
    stereo: known ? calib!.loaded : null,
    lasers: known ? calib!.loaded && calib!.lasers.length >= 2 : null,
    imu: known ? calib!.loaded && calib!.imu : null,
    check: null,
  };

  return (
    <Page wide>
      <PageHeader
        title="Calibrate"
        subtitle="Calibration turns pixels into millimetres. Do it once, and again after a knock or a lens change."
        actions={<Badge tone="accent">Guided wizard: coming in Studio M2</Badge>}
      />

      {device?.connected && !calib?.loaded ? (
        <Callout tone="warn" icon={TriangleExclamationPoint} title="This device has no calibration yet" className="mb-5">
          Scanning needs at least the stereo calibration. Run the steps below with the CLI today, then reconnect the device on the{" "}
          <button type="button" className="font-medium text-accent hover:underline" onClick={() => navigate(paths.device())}>
            Device page
          </button>
          .
        </Callout>
      ) : null}

      <Section title="The calibration steps" icon={Crosshair} className="mb-5" description="Preview of the Studio wizard (M2)">
        <ol className="grid gap-3 lg:grid-cols-4">
          {STEPS.map((s, i) => {
            const state = done[s.key];
            return (
              <li key={s.key} className="relative">
                <div
                  className={cn(
                    "flex h-full flex-col rounded-xl border p-4",
                    state ? "border-ok/30 bg-ok-soft/40" : "border-line bg-surface-2/60",
                  )}
                >
                  <div className="mb-3 flex items-center gap-2.5">
                    <span
                      className={cn(
                        "grid h-9 w-9 place-items-center rounded-lg border",
                        state ? "border-ok/30 bg-ok-soft text-ok" : "border-line bg-surface-3 text-accent",
                      )}
                    >
                      <s.icon size={17} aria-hidden />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="text-[11px] font-medium text-subtle">Step {i + 1}</div>
                      <div className="text-sm font-semibold text-fg">{s.title}</div>
                    </div>
                    {state ? (
                      <Badge tone="ok" icon={Check}>
                        Done
                      </Badge>
                    ) : state === false ? (
                      <Badge tone="warn">To do</Badge>
                    ) : null}
                  </div>
                  <p className="text-sm text-fg">{s.what}</p>
                  <p className="mt-2 text-xs leading-5 text-muted">{s.how}</p>
                  <div className="mt-auto flex flex-wrap gap-1.5 pt-3">
                    <Badge>{s.time}</Badge>
                    <Badge>{s.emitters}</Badge>
                  </div>
                </div>
                {i < STEPS.length - 1 ? (
                  <ArrowRight
                    size={16}
                    className="absolute -right-[13px] top-1/2 z-10 hidden -translate-y-1/2 rounded-full bg-surface text-subtle lg:block"
                    aria-hidden
                  />
                ) : null}
              </li>
            );
          })}
        </ol>
      </Section>

      <div className="grid gap-5 lg:grid-cols-[1.25fr_1fr]">
        <Section title="Today: calibrate with the CLI" icon={Printer} description="Run these in a terminal with the scanner plugged in">
          <ol className="space-y-4">
            <li>
              <div className="mb-1.5 text-sm text-fg">
                <span className="mr-2 font-semibold text-accent">1</span>Print the board and the tags at 100 % (no “fit to page”),
                then check the 100 mm scale bar with calipers.
              </div>
              <CopyCommand cmd="ohs printables printables" />
            </li>
            {STEPS.map((s, i) => (
              <li key={s.key}>
                <div className="mb-1.5 text-sm text-fg">
                  <span className="mr-2 font-semibold text-accent">{i + 2}</span>
                  {s.title}: {s.how.charAt(0).toLowerCase() + s.how.slice(1)}
                </div>
                <CopyCommand cmd={s.cli} />
              </li>
            ))}
            <li className="text-sm text-fg">
              <span className="mr-2 font-semibold text-accent">6</span>Reconnect the device in Studio to load the new calibration.
              <div className="mt-1 text-xs text-subtle">
                In the capture window: <Code>SPACE</Code> or the trigger captures a view, <Code>q</Code> finishes and calibrates,{" "}
                <Code>ESC</Code> aborts.
              </div>
            </li>
          </ol>
        </Section>

        <Section title="What the wizard adds" icon={Sparkles} description="Studio M2">
          <ul className="space-y-3.5">
            {WIZARD_FEATURES.map((f) => (
              <li key={f.title} className="flex gap-3">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-line bg-surface-2 text-accent">
                  <f.icon size={15} aria-hidden />
                </span>
                <div>
                  <div className="text-sm font-medium text-fg">{f.title}</div>
                  <div className="text-xs text-muted">{f.text}</div>
                </div>
              </li>
            ))}
          </ul>
          <div className="mt-5 rounded-lg border border-line bg-surface-2/60 p-3 text-xs text-muted">
            The simulator demo ships with its true calibration, so you can try scanning right away:{" "}
            <button type="button" className="font-medium text-accent hover:underline" onClick={() => navigate(paths.device())}>
              open the Device page
            </button>
            .
          </div>
        </Section>
      </div>
    </Page>
  );
}
