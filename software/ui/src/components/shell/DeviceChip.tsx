import { Camera, Check, Film, FlaskConical, TriangleExclamationPoint, Unplug } from "lucide-react";
import { navigate, paths } from "../../lib/router";
import { cn } from "../../lib/util";
import { useConnection } from "../../store/connection";
import { SOURCE_LABEL, useDevice } from "../../store/device";
import { useJobs } from "../../store/jobs";
import { Spinner } from "../ui/Feedback";
import { Tooltip } from "../ui/Popover";

const SOURCE_ICON = { none: Unplug, camera: Camera, replay: Film, sim: FlaskConical } as const;

/** Always-visible device status: source, connection, calibration. Click → Device page. */
export function DeviceChip() {
  const device = useDevice((s) => s.device);
  const pending = useDevice((s) => s.pending);
  const simJob = useJobs((s) => {
    const id = useDevice.getState().simJobId;
    return id ? s.jobs[id] : undefined;
  });
  const simJobId = useDevice((s) => s.simJobId);
  const offline = useConnection((s) => !!s.healthError?.unreachable && (!s.health || s.link !== "open"));

  const source = device?.source ?? "none";
  const connected = !!device?.connected;
  const busy = device?.busy ?? (simJobId ? "Generating simulator demo…" : null);
  const calibrated = !!device?.calibration?.loaded;
  const Icon = SOURCE_ICON[source] ?? Unplug;

  const statusText = offline
    ? "Engine offline"
    : !device
    ? "Checking…"
    : busy
      ? "Busy"
      : pending === "connect"
        ? "Connecting…"
        : connected
          ? "Connected"
          : "Not connected";

  const tooltip = (
    <div className="space-y-1.5">
      <div className="font-semibold">{connected ? `${SOURCE_LABEL[source]} connected` : "No device connected"}</div>
      {busy ? (
        <div className="text-muted">
          {busy}
          {simJob ? ` ${Math.round(simJob.progress * 100)} %` : ""}
        </div>
      ) : null}
      {connected ? (
        <>
          {device?.camera ? <div className="text-muted">Camera: {device.camera.connected ? "connected" : "not found"}</div> : null}
          {device?.pico ? (
            <div className="text-muted">
              Pico: {device.pico.connected ? `connected${device.pico.firmware ? ` (${device.pico.firmware})` : ""}` : "not found"}
            </div>
          ) : null}
          <div className={calibrated ? "text-ok" : "text-warn"}>
            {calibrated ? "Calibration loaded" : "No calibration: scanning needs one"}
          </div>
        </>
      ) : (
        <div className="text-muted">Choose the camera, a recording or the simulator demo on the Device page.</div>
      )}
    </div>
  );

  return (
    <Tooltip content={tooltip}>
      <button
        type="button"
        onClick={() => navigate(paths.device())}
        aria-label={`Device: ${connected ? SOURCE_LABEL[source] : "none"}, ${statusText}${connected ? (calibrated ? ", calibrated" : ", no calibration") : ""}. Open the Device page.`}
        className="flex h-8 items-center gap-2 rounded-lg border border-line bg-surface-2 pl-2.5 pr-2 text-xs transition-colors hover:border-line-strong hover:bg-surface-3"
      >
        <span className="relative">
          <Icon size={15} className={connected ? "text-fg" : "text-subtle"} aria-hidden />
          <span
            className={cn(
              "absolute -bottom-0.5 -right-1 h-2 w-2 rounded-full border border-panel",
              busy || pending ? "bg-accent animate-pulse" : connected ? "bg-ok" : "bg-subtle",
            )}
            aria-hidden
          />
        </span>
        <span className="hidden font-medium text-fg md:inline">{connected ? SOURCE_LABEL[source] : "No device"}</span>
        <span className="hidden text-subtle lg:inline">{busy || pending ? <Spinner size={11} /> : statusText}</span>
        {connected ? (
          <>
            <span className="h-3.5 w-px bg-line-strong" aria-hidden />
            <span className={cn("inline-flex items-center gap-1 font-medium", calibrated ? "text-ok" : "text-warn")}>
              {calibrated ? <Check size={13} strokeWidth={2.5} aria-hidden /> : <TriangleExclamationPoint size={13} aria-hidden />}
              <span className="hidden xl:inline">{calibrated ? "Calibrated" : "No calibration"}</span>
            </span>
          </>
        ) : null}
      </button>
    </Tooltip>
  );
}
