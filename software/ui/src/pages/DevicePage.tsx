import {
  Camera,
  Check,
  CircleCheck,
  Crosshair,
  Film,
  FlaskConical,
  FolderOpen,
  Lightbulb,
  Plug,
  ScanLine,
  SlidersHorizontal,
  TriangleExclamationPoint,
  Unplug,
  Usb,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { ConnectSource } from "../api/types";
import { EmitterSliders } from "../components/EmitterSliders";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { ProgressBar, Skeleton, Spinner } from "../components/ui/Feedback";
import { Field, Switch, TextInput } from "../components/ui/Form";
import { Callout, Code, KeyValue, Page, PageHeader, Section } from "../components/ui/Layout";
import { fmtFixed, humanize } from "../lib/format";
import { navigate, paths } from "../lib/router";
import { cn, storage } from "../lib/util";
import { SOURCE_LABEL, useDevice } from "../store/device";
import { useJobs } from "../store/jobs";
import { isLive, useSession } from "../store/session";

type SourceCard = { id: ConnectSource; title: string; icon: LucideIcon; text: string; needs: string };

const SOURCES: SourceCard[] = [
  {
    id: "camera",
    title: "Camera",
    icon: Camera,
    text: "The handheld scanner: ELP stereo camera with the Pico for lasers, dot projector, trigger and IMU.",
    needs: "Stereo camera + Pico on USB",
  },
  {
    id: "replay",
    title: "Replay a recording",
    icon: Film,
    text: "Play back a recorded scan as if it were live: tune settings or demo without the scanner.",
    needs: "A recording folder",
  },
  {
    id: "sim",
    title: "Simulator demo",
    icon: FlaskConical,
    text: "A simulated scanner over a sphere on a plane, with true calibration. Try the whole app with no hardware.",
    needs: "Nothing",
  },
];

const REPLAY_KEY = "ohs.replayPath";

function StatusLine({ ok, label, detail }: { ok: boolean | null; label: string; detail?: ReactNode }) {
  return (
    <div className="flex items-start gap-2.5">
      <span
        className={cn(
          "mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full",
          ok === null ? "bg-surface-3 text-subtle" : ok ? "bg-ok-soft text-ok" : "bg-warn-soft text-warn",
        )}
      >
        {ok === null ? <span className="h-1.5 w-1.5 rounded-full bg-current" /> : ok ? <Check size={12} strokeWidth={3} /> : <X size={12} strokeWidth={3} />}
      </span>
      <div className="min-w-0">
        <div className="text-sm text-fg">{label}</div>
        {detail ? <div className="text-xs text-subtle">{detail}</div> : null}
      </div>
    </div>
  );
}

export function DevicePage() {
  const device = useDevice((s) => s.device);
  const pending = useDevice((s) => s.pending);
  const pendingSource = useDevice((s) => s.pendingSource);
  const connect = useDevice((s) => s.connect);
  const disconnect = useDevice((s) => s.disconnect);
  const fetchDevice = useDevice((s) => s.fetch);
  const simJobId = useDevice((s) => s.simJobId);
  const simJob = useJobs((s) => (simJobId ? s.jobs[simJobId] : undefined));
  const scanning = useSession((s) => isLive(s.session));

  const [choice, setChoice] = useState<ConnectSource>(() =>
    device && device.source !== "none" ? device.source : "sim",
  );
  const [path, setPath] = useState<string>(() => storage.get(REPLAY_KEY, ""));
  const [loop, setLoop] = useState(true);
  const touched = useRef(false);

  useEffect(() => {
    void fetchDevice();
  }, [fetchDevice]);

  // Follow the connected source until the user picks one.
  useEffect(() => {
    if (!touched.current && device && device.source !== "none") setChoice(device.source);
    if (device?.replay?.path && !path) setPath(device.replay.path);
  }, [device?.source]); // eslint-disable-line react-hooks/exhaustive-deps

  const connected = !!device?.connected;
  const busy = device?.busy ?? (simJobId ? "Generating simulator demo…" : null);
  const isCurrent = connected && device?.source === choice;
  const calib = device?.calibration;

  const doConnect = async () => {
    if (choice === "replay") storage.set(REPLAY_KEY, path.trim());
    await connect(
      choice === "replay" ? { source: "replay", recording: path.trim(), loop } : { source: choice },
    );
  };

  const cameraInfo = useMemo(() => {
    const info = device?.camera?.info ?? {};
    return Object.entries(info).filter(([, v]) => v !== null && v !== undefined && typeof v !== "object");
  }, [device?.camera?.info]);

  const replayInvalid = choice === "replay" && !path.trim();

  return (
    <Page>
      <PageHeader
        title="Device"
        subtitle="Choose where frames come from: the scanner, a recording, or the simulator."
        actions={
          connected ? (
            <>
              <Badge tone="ok" dot>
                {SOURCE_LABEL[device!.source]} connected
              </Badge>
              <Button
                icon={Unplug}
                loading={pending === "disconnect"}
                disabled={scanning}
                title={scanning ? "Stop the scan before disconnecting" : "Disconnect and switch the emitters off"}
                onClick={() => void disconnect()}
              >
                Disconnect
              </Button>
            </>
          ) : device ? (
            <Badge tone="neutral" dot>
              Not connected
            </Badge>
          ) : null
        }
      />

      <Section title="Source" icon={Plug} className="mb-5">
        <div role="radiogroup" aria-label="Device source" className="grid gap-3 md:grid-cols-3">
          {SOURCES.map((s) => {
            const selected = choice === s.id;
            const live = connected && device?.source === s.id;
            return (
              <button
                key={s.id}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => {
                  touched.current = true;
                  setChoice(s.id);
                }}
                className={cn(
                  "relative flex flex-col items-start gap-2 rounded-xl border p-4 text-left transition-[border-color,background-color,box-shadow]",
                  selected
                    ? "border-accent bg-accent-soft shadow-[0_0_0_3px_var(--c-accent-soft)]"
                    : "border-line bg-surface-2 hover:border-line-strong",
                )}
              >
                <div className="flex w-full items-center gap-2.5">
                  <span
                    className={cn(
                      "grid h-9 w-9 place-items-center rounded-lg border",
                      selected ? "border-accent-line bg-accent text-accent-fg" : "border-line bg-surface-3 text-muted",
                    )}
                  >
                    <s.icon size={17} aria-hidden />
                  </span>
                  <span className="flex-1 text-sm font-semibold text-fg">{s.title}</span>
                  {live ? (
                    <Badge tone="ok" dot>
                      Live
                    </Badge>
                  ) : null}
                </div>
                <p className="text-xs leading-5 text-muted">{s.text}</p>
                <p className="mt-auto text-[11px] text-subtle">Needs: {s.needs}</p>
              </button>
            );
          })}
        </div>

        <div className="mt-4 rounded-xl border border-line bg-surface-2/60 p-4">
          {choice === "replay" ? (
            <div className="mb-4 grid gap-4 md:grid-cols-[1fr_auto] md:items-end">
              <Field
                label="Recording folder"
                htmlFor="replay-path"
                help={
                  <>
                    The folder with <Code>meta.json</Code> and <Code>frames.csv</Code>, e.g. one made by <Code>ohs record</Code> or{" "}
                    <Code>ohs sim</Code>.
                  </>
                }
              >
                <TextInput
                  id="replay-path"
                  mono
                  icon={FolderOpen}
                  placeholder="C:\Users\you\OpenHandScan\scans\scan-2026-11-08"
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  invalid={false}
                />
              </Field>
              <div className="pb-6">
                <Switch checked={loop} onChange={setLoop} label="Loop" description="Start over at the end" />
              </div>
            </div>
          ) : null}
          {busy ? (
            <div className="min-h-10 animate-fade-in" role="status">
              <div className="flex items-center gap-3">
                <Spinner size={16} className="text-accent" />
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-fg">{busy}</div>
                  <div className="truncate text-xs text-muted">
                    {simJob?.message || "This happens once. Next time the demo starts right away."}
                  </div>
                </div>
                {simJob ? <span className="font-mono text-sm text-fg tnum">{Math.round(simJob.progress * 100)}%</span> : null}
              </div>
              <ProgressBar className="mt-2.5" value={simJob ? simJob.progress : null} label="Simulator demo progress" />
            </div>
          ) : (
          <div className="flex min-h-10 flex-wrap items-center gap-3">
            {isCurrent ? (
              <>
                <span className="inline-flex items-center gap-2 text-sm text-fg">
                  <CircleCheck size={16} className="text-ok" aria-hidden />
                  {SOURCE_LABEL[choice]} is connected
                  {device?.replay && choice === "replay" ? (
                    <span className="font-mono text-xs text-subtle" title={device.replay.path}>
                      {device.replay.path}
                    </span>
                  ) : null}
                </span>
                <div className="flex-1" />
                <Button variant="primary" icon={ScanLine} onClick={() => navigate(paths.scan())}>
                  Go to scan
                </Button>
              </>
            ) : (
              <>
                <Button
                  variant="primary"
                  size="lg"
                  icon={choice === "camera" ? Usb : choice === "replay" ? Film : FlaskConical}
                  loading={pending === "connect" && pendingSource === choice}
                  disabled={!!busy || replayInvalid || scanning || pending !== null}
                  onClick={() => void doConnect()}
                >
                  {connected ? `Switch to ${SOURCE_LABEL[choice].toLowerCase()}` : choice === "camera" ? "Connect camera" : choice === "replay" ? "Start replay" : "Start simulator"}
                </Button>
                <span className="text-xs text-subtle">
                  {scanning
                    ? "Stop the scan first to change the device."
                    : replayInvalid
                      ? "Enter the recording folder first."
                      : choice === "sim"
                        ? "The first start builds the demo recording; it takes a moment."
                        : choice === "camera"
                          ? "Emitters stay off until a scan starts or you turn them on below."
                          : connected
                            ? "The current device disconnects first."
                            : ""}
                </span>
              </>
            )}
          </div>
          )}
        </div>
      </Section>

      <div className="grid gap-5 lg:grid-cols-2">
        <Section title="Hardware" icon={Usb}>
          {!device ? (
            <div className="space-y-3">
              <Skeleton className="h-5 w-2/3" />
              <Skeleton className="h-5 w-1/2" />
            </div>
          ) : !connected ? (
            <div className="space-y-3">
              <StatusLine ok={null} label="No device connected" detail="Pick a source above and connect." />
              <Callout icon={Lightbulb} title="Scanner not found?">
                Plug the stereo camera into a USB 3 port and the Pico into any port, then connect. <Code>ohs devices</Code> lists
                what the engine can see, and <Code>ohs selftest</Code> checks every part.
              </Callout>
            </div>
          ) : device.source === "camera" ? (
            <div className="space-y-4">
              <StatusLine
                ok={!!device.camera?.connected}
                label={device.camera?.connected ? "Stereo camera connected" : "Stereo camera not found"}
                detail={device.camera?.connected ? null : "Check the USB 3 cable and that no other app uses the camera."}
              />
              {cameraInfo.length ? (
                <KeyValue
                  dense
                  className="pl-7"
                  rows={cameraInfo.map(([k, v]) => [humanize(k), <span className="font-mono text-xs">{String(v)}</span>])}
                />
              ) : null}
              <StatusLine
                ok={!!device.pico?.connected}
                label={device.pico?.connected ? "Pico connected" : "Pico not found"}
                detail={
                  device.pico?.connected
                    ? device.pico.firmware
                      ? `Firmware ${device.pico.firmware}`
                      : "Firmware version unknown"
                    : "Emitters, trigger and IMU are unavailable. Check the cable; flash the firmware (firmware/README.md)."
                }
              />
            </div>
          ) : device.source === "replay" ? (
            <div className="space-y-3">
              <StatusLine ok label="Replaying a recording" detail={device.replay?.loop ? "Loops at the end" : "Plays once"} />
              <KeyValue rows={[["Folder", <span className="break-all font-mono text-xs">{device.replay?.path ?? "–"}</span>]]} />
            </div>
          ) : (
            <div className="space-y-3">
              <StatusLine ok label="Simulated scanner" detail="Rendered stereo frames, laser lines, dots and IMU, with true calibration." />
              <Callout icon={FlaskConical} title="Demo data">
                Scans made with the simulator are real recordings: process, view and measure them like any other.
              </Callout>
            </div>
          )}
        </Section>

        <Section title="Calibration" icon={Crosshair}>
          {!device ? (
            <div className="space-y-3">
              <Skeleton className="h-5 w-1/2" />
              <Skeleton className="h-5 w-3/4" />
            </div>
          ) : calib?.loaded ? (
            <div className="space-y-4">
              <StatusLine ok label="Calibration loaded" detail="Scanning and meshing use this calibration." />
              <KeyValue
                rows={[
                  ["File", <span className="break-all font-mono text-xs" title={calib.path ?? undefined}>{calib.path ?? "from the recording"}</span>],
                  ["Image size", calib.image_size ? `${calib.image_size[0]} × ${calib.image_size[1]} px` : "–"],
                  ["Baseline", calib.baseline_mm !== null ? `${fmtFixed(calib.baseline_mm, 2)} mm` : "–"],
                  [
                    "Laser planes",
                    calib.lasers.length ? (
                      <span className="flex flex-wrap gap-1.5">
                        {calib.lasers.map((l) => (
                          <Badge key={l} tone="accent">
                            {l}
                          </Badge>
                        ))}
                      </span>
                    ) : (
                      <span className="text-warn">None: no laser points</span>
                    ),
                  ],
                  ["IMU", calib.imu ? <span className="text-ok">Calibrated</span> : <span className="text-warn">Not calibrated: no gyro prior</span>],
                ]}
              />
            </div>
          ) : (
            <div className="space-y-3">
              <StatusLine ok={false} label="No calibration" detail={connected ? "Scanning needs one." : "Connect a device to load its calibration."} />
              <Callout
                tone="warn"
                icon={TriangleExclamationPoint}
                title="Run calibration"
                actions={
                  <Button size="sm" icon={Crosshair} onClick={() => navigate(paths.calibrate())}>
                    How to calibrate
                  </Button>
                }
              >
                The guided wizard is coming in Studio M2. For now run <Code>ohs calibrate stereo</Code>, then{" "}
                <Code>lasers</Code> and <Code>imu</Code>, and reconnect.
              </Callout>
            </div>
          )}
        </Section>

        <Section
          title="Emitters"
          icon={SlidersHorizontal}
          className="lg:col-span-2"
          description="Camera only. Replays and the simulator carry their own emitter settings."
          actions={
            device?.source === "camera" && device.pico?.connected ? (
              <span className="inline-flex items-center gap-1.5 text-xs text-warn">
                <Zap size={13} aria-hidden /> Class 3R lasers: never look into them
              </span>
            ) : null
          }
        >
          {device && device.source === "camera" && connected ? (
            device.pico?.connected ? (
              <div className="max-w-2xl">
                <EmitterSliders device={device} />
              </div>
            ) : (
              <Callout tone="warn" icon={TriangleExclamationPoint} title="Pico not found">
                The emitters, the trigger and the IMU need the Pico. Check its USB cable and flash the firmware (see{" "}
                <Code>firmware/README.md</Code>), then reconnect.
              </Callout>
            )
          ) : (
            <div className="text-sm text-muted">
              {connected
                ? `Emitter levels can't be changed for ${device?.source === "sim" ? "the simulator" : "a replay"}.`
                : "Connect the camera to adjust the line lasers and the dot projector."}
            </div>
          )}
        </Section>
      </div>
    </Page>
  );
}
