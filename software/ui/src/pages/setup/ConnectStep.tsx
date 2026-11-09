/** Step 1, Connect: the device status, the cameras found, the Pico and its firmware, or the simulator. */
import {
  ArrowLeftRight,
  Camera,
  Check,
  Cpu,
  FlaskConical,
  Microchip,
  Orbit,
  Plug,
  RefreshCw,
  TriangleExclamationPoint,
  Unplug,
  Usb,
  Webcam,
  Wrench,
  X,
} from "lucide-react";
import { useEffect, type ReactNode } from "react";
import type { CameraProbe, DeviceState } from "../../api/types";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ProgressBar, Skeleton, Spinner } from "../../components/ui/Feedback";
import { Switch } from "../../components/ui/Form";
import { Callout, Code, Section } from "../../components/ui/Layout";
import { sentence } from "../../api/http";
import { cn } from "../../lib/util";
import { SOURCE_LABEL, useDevice, useSimDemoReady } from "../../store/device";
import { useJobs } from "../../store/jobs";
import { isLive, useSession } from "../../store/session";
import { useSetup } from "../../store/setup";
import { CopyCommand, Disclosure, ErrorCallout, StepFooter, StepHeader, StepPage } from "./common";

function Row({ ok, icon: Icon, label, detail, action }: { ok: boolean | null; icon: typeof Camera; label: ReactNode; detail?: ReactNode; action?: ReactNode }) {
  return (
    <li className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
      <span
        className={cn(
          "grid h-9 w-9 shrink-0 place-items-center rounded-lg border",
          ok === null ? "border-line bg-surface-2 text-subtle" : ok ? "border-ok/30 bg-ok-soft text-ok" : "border-warn/30 bg-warn-soft text-warn",
        )}
      >
        <Icon size={17} aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-sm font-medium text-fg">
          {label}
          {ok === true ? <Check size={14} className="text-ok" strokeWidth={2.6} aria-label="OK" /> : ok === false ? <X size={14} className="text-warn" strokeWidth={2.6} aria-label="Problem" /> : null}
        </div>
        {detail ? <div className="mt-0.5 text-xs text-muted">{detail}</div> : null}
      </div>
      {action}
    </li>
  );
}

const firmwareOk = (fw: string | null | undefined) => !!fw && /ohs-fw/i.test(fw);

function picoStatus(d: DeviceState | null): { ok: boolean | null; label: string; detail: string } {
  if (!d?.connected || d.source !== "camera") return { ok: null, label: "Pico", detail: "Connect the scanner to check it." };
  const p = d.pico;
  if (!p?.connected) return { ok: false, label: "Pico not answering", detail: p?.error ? sentence(p.error) : "No reply on any serial port: flash the firmware, or try a data USB cable." };
  if (!firmwareOk(p.firmware)) return { ok: false, label: "Unexpected firmware", detail: `It answers as “${p.firmware ?? "unknown"}”: flash the OpenHandScan firmware below.` };
  return { ok: true, label: "Pico firmware", detail: `${p.firmware}${p.port ? ` on ${p.port}` : ""}` };
}

function imuStatus(d: DeviceState | null): { ok: boolean | null; detail: string } {
  if (!d?.connected) return { ok: null, detail: "Connect the scanner to check it." };
  if (d.source === "sim") return { ok: true, detail: "Synthesized by the simulator" };
  if (d.source !== "camera") return { ok: null, detail: "Recordings carry their own IMU data." };
  if (!d.pico?.connected) return { ok: false, detail: "Needs the Pico." };
  const imu = d.pico.imu;
  if (imu && /none/i.test(imu)) return { ok: false, detail: "The firmware finds no IMU: check the wiring (SDA GP4, SCL GP5, 3V3, GND)." };
  if (d.imu_stream === false) return { ok: false, detail: "The IMU didn't start streaming: tracking runs without the gyro." };
  return { ok: true, detail: imu ? `${imu}, streaming` : "Streaming" };
}

function ScannerStatus() {
  const device = useDevice((s) => s.device);
  const pending = useDevice((s) => s.pending);
  const connect = useDevice((s) => s.connect);
  const disconnect = useDevice((s) => s.disconnect);
  const scanning = useSession((s) => isLive(s.session));
  const changed = useSetup((s) => s.cameraChanged);
  const clearChanged = useSetup((s) => s.clearCameraChanged);
  const connected = !!device?.connected;
  const camOn = connected && device?.source === "camera";
  const info = (device?.camera?.info ?? {}) as Record<string, unknown>;
  const camText = camOn
    ? [info.name, info.width && info.height ? `${String(info.width)}×${String(info.height)}` : null, info.fps ? `${String(info.fps)} fps` : null].filter(Boolean).join(" · ")
    : connected
      ? `${SOURCE_LABEL[device!.source]} connected`
      : "Not connected";
  const pico = picoStatus(device);
  const imu = imuStatus(device);
  const camWarn = (device?.warnings ?? []).find((w) => /camera|stereo|side-by-side/i.test(w));
  const reconnect = async () => {
    if (await connect({ source: "camera" })) clearChanged();
  };

  return (
    <Section
      title="Your scanner"
      icon={Plug}
      actions={
        connected ? (
          <Button size="sm" variant="ghost" icon={Unplug} loading={pending === "disconnect"} disabled={scanning} onClick={() => void disconnect()}>
            Disconnect
          </Button>
        ) : null
      }
    >
      {!device ? (
        <div className="space-y-3">
          <Skeleton className="h-9 w-2/3" />
          <Skeleton className="h-9 w-1/2" />
        </div>
      ) : (
        <>
          <ul className="divide-y divide-line">
            <Row
              ok={camOn ? !camWarn : connected && device.source === "sim" ? true : null}
              icon={device.source === "sim" ? FlaskConical : Camera}
              label={device.source === "sim" && connected ? "Simulated stereo camera" : "Stereo camera"}
              detail={camWarn ? sentence(camWarn) : camText}
            />
            <Row ok={device.source === "sim" && connected ? null : pico.ok} icon={Microchip} label={pico.label} detail={device.source === "sim" && connected ? "The simulator needs no Pico." : pico.detail} />
            <Row ok={imu.ok} icon={Orbit} label="IMU" detail={imu.detail} />
          </ul>
          {changed && camOn ? (
            <Callout
              tone="accent"
              icon={RefreshCw}
              title="Reconnect to use the new camera settings"
              className="mt-4"
              actions={
                <Button size="sm" variant="primary" icon={RefreshCw} loading={pending === "connect"} disabled={scanning} onClick={() => void reconnect()}>
                  Reconnect
                </Button>
              }
            >
              They're saved to the config file and take effect on the next connect.
            </Callout>
          ) : null}
          <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-4">
            {!camOn ? (
              <Button variant="primary" size="lg" icon={Usb} loading={pending === "connect" && useDevice.getState().pendingSource === "camera"} disabled={scanning || pending !== null || !!device.busy} onClick={() => void reconnect()}>
                {connected ? "Switch to the scanner" : "Connect the scanner"}
              </Button>
            ) : (
              <Button icon={RefreshCw} loading={pending === "connect"} disabled={scanning || pending !== null} onClick={() => void reconnect()}>
                Reconnect
              </Button>
            )}
            <span className="text-xs text-subtle">
              {scanning
                ? "Stop the scan first to change the device."
                : camOn
                  ? "Connected. If something above isn't right, fix it and reconnect."
                  : "Plug the stereo camera into a USB 3 port and the Pico into any port."}
            </span>
          </div>
        </>
      )}
    </Section>
  );
}

function CameraRow({ cam, best }: { cam: CameraProbe; best: boolean }) {
  const setCamera = useSetup((s) => s.setCamera);
  const pending = useSetup((s) => s.cameraPending);
  const Icon = cam.stereo_like ? Camera : Webcam;
  return (
    <li
      className={cn(
        "flex flex-wrap items-center gap-3 rounded-xl border p-3.5 transition-colors",
        cam.configured ? "border-accent-line/70 bg-accent-soft/50" : "border-line bg-surface-2/50",
      )}
    >
      <span className={cn("grid h-10 w-10 shrink-0 place-items-center rounded-lg border", cam.stereo_like ? "border-accent-line bg-accent-soft text-accent" : "border-line bg-surface-3 text-muted")}>
        <Icon size={18} aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-fg">
            Camera {cam.index}
            {cam.name ? <span className="font-normal text-muted"> · {cam.name}</span> : null}
          </span>
          {cam.stereo_like ? <Badge tone="accent">Looks like the stereo camera</Badge> : null}
          {cam.in_use ? (
            <Badge tone="ok" dot>
              In use
            </Badge>
          ) : cam.configured ? (
            <Badge tone="neutral" dot>
              Selected
            </Badge>
          ) : null}
        </div>
        <div className="mt-0.5 font-mono text-xs text-subtle tnum">
          {cam.width} × {cam.height} · {cam.fps} fps · {cam.fourcc}
        </div>
      </div>
      {!cam.configured ? (
        <Button size="sm" variant={best ? "primary" : "secondary"} loading={pending === "index"} onClick={() => void setCamera({ index: cam.index })}>
          Use this camera
        </Button>
      ) : (
        <span className="inline-flex items-center gap-1 text-xs font-medium text-accent">
          <Check size={13} aria-hidden /> Selected
        </span>
      )}
    </li>
  );
}

function Cameras() {
  const devices = useSetup((s) => s.devices);
  const loading = useSetup((s) => s.devicesLoading);
  const error = useSetup((s) => s.devicesError);
  const probe = useSetup((s) => s.probeDevices);
  const camera = useSetup((s) => s.camera);
  const setCamera = useSetup((s) => s.setCamera);
  const pending = useSetup((s) => s.cameraPending);
  const configuredStereo = !!devices?.cameras.find((c) => c.configured)?.stereo_like;

  return (
    <Section
      title="Cameras found"
      icon={Camera}
      description="The stereo camera shows one wide side-by-side image (2560 × 800)"
      actions={
        <Button size="sm" variant="ghost" icon={RefreshCw} loading={loading} onClick={() => void probe()}>
          Refresh
        </Button>
      }
    >
      {error && !devices ? (
        <ErrorCallout title="Couldn't list the cameras" err={error} here="connect" onRetry={() => void probe()} />
      ) : !devices ? (
        <div className="space-y-2.5" aria-busy="true">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : devices.cameras.length === 0 ? (
        <Callout tone="warn" icon={TriangleExclamationPoint} title="No camera found">
          Plug the stereo camera into a USB 3 port (blue inside), close other apps that use a camera (Zoom, Teams, the Camera app), then Refresh.
        </Callout>
      ) : (
        <>
          <ul className="space-y-2.5">
            {devices.cameras.map((c) => (
              <CameraRow key={c.index} cam={c} best={c.stereo_like && !configuredStereo} />
            ))}
          </ul>
          {devices.note ? <p className="mt-3 text-xs text-muted">{sentence(devices.note)}</p> : null}
        </>
      )}
      <div className="mt-4 border-t border-line pt-4">
        <Switch
          checked={!!camera?.swap_lr}
          disabled={pending === "swap" || !camera}
          onChange={(v) => void setCamera({ swap_lr: v })}
          label="Swap left and right"
          description="Turn on if the stereo calibration says the cameras look swapped (a baseline with the wrong sign). It takes effect on the next connect."
        />
      </div>
    </Section>
  );
}

function FlashSteps() {
  return (
    <ol className="space-y-4">
      <li>
        <div className="mb-1 font-medium text-fg">
          <span className="mr-2 text-accent">1</span>Flash MicroPython (once)
        </div>
        <ul className="ml-5 list-disc space-y-1 text-muted">
          <li>
            Download the MicroPython <Code>.uf2</Code> for your board from micropython.org: <Code>RPI_PICO</Code> for a Pico, <Code>RPI_PICO2</Code> for a
            Pico 2 (the <Code>_W</Code> builds work too).
          </li>
          <li>
            Hold the <b className="text-fg">BOOTSEL</b> button and plug the Pico into USB. A drive called <Code>RPI-RP2</Code> (Pico) or <Code>RP2350</Code>{" "}
            (Pico 2) appears.
          </li>
          <li>Copy the .uf2 file onto that drive. The Pico reboots into MicroPython and shows up as a serial port (COM3, COM5… on Windows).</li>
        </ul>
      </li>
      <li>
        <div className="mb-1 font-medium text-fg">
          <span className="mr-2 text-accent">2</span>Copy the firmware files
        </div>
        <p className="mb-2 text-muted">
          With <Code>mpremote</Code> (<Code>pip install mpremote</Code>), from the repository folder:
        </p>
        <div className="space-y-1.5">
          <CopyCommand cmd="mpremote connect auto cp firmware/lsm6dsox.py :lsm6dsox.py" />
          <CopyCommand cmd="mpremote connect auto cp firmware/main.py :main.py" />
          <CopyCommand cmd="mpremote connect auto reset" />
        </div>
        <p className="mt-2 text-muted">
          Several boards attached? Use <Code>connect COM5</Code> instead of <Code>auto</Code>. Prefer a window? In Thonny pick the interpreter
          “MicroPython (Raspberry Pi Pico)”, press Stop, then save <Code>main.py</Code> and <Code>lsm6dsox.py</Code> to the Pico under the same names,
          and unplug and replug it.
        </p>
      </li>
      <li>
        <div className="mb-1 font-medium text-fg">
          <span className="mr-2 text-accent">3</span>Close the other programs, then connect
        </div>
        <p className="text-muted">
          Only one program can hold the Pico's port: close Thonny and mpremote, then press Refresh above and Connect the scanner. The firmware answers
          as <Code>ohs-fw</Code>.
        </p>
      </li>
      <li className="rounded-lg border border-line bg-surface-2/60 p-3 text-xs text-muted">
        <b className="text-fg">Still no answer?</b> The firmware isn't running: <Code>mpremote connect COM5 repl</Code>, then Ctrl-D shows it start and
        any error. <b className="text-fg">imu=NONE</b> means the IMU isn't found: check SDA on GP4 and SCL on GP5. The full guide is in{" "}
        <Code>firmware/README.md</Code>.
      </li>
    </ol>
  );
}

function PicoSection() {
  const devices = useSetup((s) => s.devices);
  const device = useDevice((s) => s.device);
  const camOn = !!device?.connected && device.source === "camera";
  const simOn = !!device?.connected && device.source === "sim";
  const st = picoStatus(device);
  // With the simulator there is no Pico to flash: keep the firmware steps folded away.
  const problem = !simOn && (camOn ? st.ok === false : !!devices && !devices.pico_port);
  return (
    <Section title="Pico" icon={Cpu} description="Drives the lasers and the dot projector, reads the trigger, streams the IMU">
      <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <div>
          <span className="text-xs text-subtle">Serial port</span>
          <div className="font-mono text-fg">{devices ? (devices.pico_port ?? <span className="font-sans text-warn">No Pico found</span>) : "…"}</div>
        </div>
        <div>
          <span className="text-xs text-subtle">Firmware</span>
          <div className={cn("text-fg", camOn && !st.ok && "text-warn")}>{camOn ? (device?.pico?.firmware ?? "No answer") : "Connect to read it"}</div>
        </div>
      </div>
      <Disclosure
        title={problem ? "Flash the firmware" : "Flash or update the firmware"}
        icon={Wrench}
        tone={problem ? "warn" : "neutral"}
        defaultOpen={problem}
        key={problem ? "open" : "closed"}
      >
        <FlashSteps />
      </Disclosure>
    </Section>
  );
}

function SimulatorCard() {
  const device = useDevice((s) => s.device);
  const pending = useDevice((s) => s.pending);
  const pendingSource = useDevice((s) => s.pendingSource);
  const connect = useDevice((s) => s.connect);
  const simJobId = useDevice((s) => s.simJobId);
  const simJob = useJobs((s) => (simJobId ? s.jobs[simJobId] : undefined));
  const ready = useSimDemoReady();
  const scanning = useSession((s) => isLive(s.session));
  const busy = device?.busy ?? (simJobId ? "Generating simulator demo…" : null);
  const simOn = !!device?.connected && device.source === "sim";
  return (
    <div className="overflow-hidden rounded-xl border border-dashed border-line-strong bg-surface-2/40 p-5">
      <div className="flex flex-wrap items-center gap-4">
        <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-accent-line bg-accent-soft text-accent">
          <FlaskConical size={20} aria-hidden />
        </span>
        <div className="min-w-[240px] flex-1">
          <div className="text-sm font-semibold text-fg">No hardware yet? Try the simulator.</div>
          <p className="mt-0.5 text-sm text-muted">
            A virtual scanner with a slightly distorted lens runs the whole wizard. It calibrates into a sandbox file, so your real calibration is
            never touched.
          </p>
        </div>
        {simOn ? (
          <Badge tone="ok" dot>
            Simulator connected
          </Badge>
        ) : !busy ? (
          <Button
            variant="secondary"
            size="lg"
            icon={FlaskConical}
            loading={pending === "connect" && pendingSource === "sim"}
            disabled={scanning || pending !== null}
            onClick={() => void connect({ source: "sim" })}
          >
            Use the simulator
          </Button>
        ) : null}
      </div>
      {busy ? (
        <div className="mt-4 animate-fade-in" role="status">
          <div className="mb-1.5 flex items-center gap-2 text-sm">
            <Spinner size={14} className="text-accent" />
            <span className="font-medium text-fg">{busy}</span>
            <span className="min-w-0 flex-1 truncate text-xs text-muted">{simJob?.message || "This happens once; next time the simulator starts right away."}</span>
            {simJob ? <span className="font-mono text-sm text-fg tnum">{Math.round(simJob.progress * 100)}%</span> : null}
          </div>
          <ProgressBar value={simJob ? simJob.progress : null} label="Simulator demo progress" />
        </div>
      ) : !simOn && !ready ? (
        <p className="mt-3 text-xs text-subtle">The first start builds the simulator's demo recording: about a minute, once.</p>
      ) : null}
    </div>
  );
}

export function ConnectStep() {
  const link = useDevice((s) => `${s.device?.source ?? "none"}:${s.device?.connected ? 1 : 0}`);
  const configPath = useSetup((s) => s.configPath);
  useEffect(() => {
    void useSetup.getState().fetchCameraConfig();
    void useDevice.getState().fetch();
  }, []);
  // The probe marks the camera in use: read it again whenever the connection changes.
  useEffect(() => {
    void useSetup.getState().probeDevices();
  }, [link]);
  return (
    <StepPage>
      <StepHeader page="connect" />
      <div className="space-y-5">
        <ScannerStatus />
        <div className="grid gap-5 2xl:grid-cols-2">
          <Cameras />
          <PicoSection />
        </div>
        <SimulatorCard />
        <p className="flex items-center gap-2 text-xs text-subtle">
          <ArrowLeftRight size={13} aria-hidden /> Studio saves the camera you pick and the swap setting to{" "}
          <Code>{configPath ?? "~/.ohs/config.toml"}</Code>.
        </p>
      </div>
      <StepFooter page="connect" />
    </StepPage>
  );
}
