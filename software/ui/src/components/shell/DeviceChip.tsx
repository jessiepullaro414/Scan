import { Camera, Check, Film, FlaskConical, TriangleExclamationPoint, Unplug } from "lucide-react";
import { calibInUse, headerHealthButton, healthView } from "../../lib/calibHealth";
import { navigate, paths, useRoute } from "../../lib/router";
import { HEALTH_ICON, HEALTH_TEXT, useDeviceHealth } from "../CalibHealth";
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
  const busy = device?.busy ?? (simJobId ? "Preparing the simulator…" : null);
  const calibrated = !!device?.calibration?.loaded;
  const Icon = SOURCE_ICON[source] ?? Unplug;
  const hv = healthView(useDeviceHealth());
  const inUse = calibInUse(device);
  const showButton = headerHealthButton(useRoute().name);

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
          {simJob ? ` ${Math.round(simJob.progress * 100)}\u202f%` : ""}
        </div>
      ) : null}
      {connected ? (
        <>
          {device?.camera ? <div className="text-muted">Camera: {device.camera.connected ? "connected" : "not found"}</div> : null}
          {device?.pico ? (
            <div className="text-muted">
              Control board: {device.pico.connected ? `connected${device.pico.firmware ? ` (${device.pico.firmware})` : ""}` : "not found"}
            </div>
          ) : null}
          {hv ? (
            // H4: the calibration's status (it includes the launch check, so the drift line is folded into it)
            <div className="space-y-0.5">
              <div className={cn("font-medium", hv.state === "valid" ? "text-ok" : HEALTH_TEXT[hv.tone])}>{hv.title}</div>
              <div className="text-muted">{hv.reason}</div>
              {hv.then ? <div className="text-muted">{hv.then}</div> : null}
              {hv.judged ? <div className="text-subtle">{hv.judged}</div> : null}
            </div>
          ) : (
            <>
              <div className={calibrated ? "text-ok" : "text-warn"}>{inUse?.line ?? (calibrated ? "Calibration loaded" : "No calibration: calibrate in Setup")}</div>
              {device?.launch_check?.state === "drift" ? (
                <div className="text-warn">The calibration may have drifted: run the calibration check in Setup</div>
              ) : null}
            </>
          )}
        </>
      ) : (
        <div className="text-muted">Choose the camera, a recording or the simulator on the Device page.</div>
      )}
    </div>
  );

  const warned = connected && hv && hv.state !== "valid" ? hv : null;
  const HealthIcon = warned ? HEALTH_ICON[warned.state] : null;
  // U48: the state's one name ("Calibration valid") when the engine judged it; "Calibration loaded" only says a file is in use
  // U21: the simulator demo's own calibration is "Demo calibration" (Setup's status does not speak for it)
  const calibWords = warned ? warned.short : hv && calibrated ? hv.short : (inUse?.short ?? (calibrated ? "Calibration loaded" : "No calibration"));

  return (
    <span className="inline-flex items-center gap-1.5">
    <Tooltip content={tooltip}>
      <button
        type="button"
        onClick={() => navigate(paths.device())}
        aria-label={`Device: ${connected ? SOURCE_LABEL[source] : "none"}, ${statusText}${connected ? (warned ? `, calibration: ${warned.title}` : calibrated ? `, ${calibWords.toLowerCase()}` : ", no calibration") : ""}. Open the Device page.`}
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
            <span className={cn("inline-flex items-center gap-1 font-medium", warned ? HEALTH_TEXT[warned.tone] : calibrated ? "text-ok" : "text-warn")}>
              {HealthIcon ? (
                <HealthIcon size={13} aria-hidden />
              ) : calibrated ? (
                <Check size={13} strokeWidth={2.5} aria-hidden />
              ) : (
                <TriangleExclamationPoint size={13} aria-hidden />
              )}
              <span className="hidden xl:inline">{calibWords}</span>
            </span>
          </>
        ) : null}
      </button>
    </Tooltip>
      {warned?.action && showButton ? (
        // the one wizard step the status names (the chip itself opens the Device page); not on the Scan and Device
        // pages, whose own line says why and has the same button
        <button
          type="button"
          onClick={() => navigate(paths.setup(warned.action!.page))}
          title={`${warned.title}. ${warned.action.hint}.`}
          aria-label={`${warned.title}. ${warned.action.label}.`}
          className={cn(
            "flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-medium transition-colors",
            warned.tone === "danger" ? "border-danger/40 bg-danger-soft text-danger hover:border-danger/60" : "border-warn/40 bg-warn-soft text-warn hover:border-warn/60",
          )}
        >
          {HealthIcon ? <HealthIcon size={13} aria-hidden /> : null}
          <span className="hidden lg:inline">{warned.action.label}</span>
        </button>
      ) : null}
    </span>
  );
}
