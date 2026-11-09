import { Cpu, Crosshair, House, ScanLine, Settings, type LucideIcon } from "lucide-react";
import { MOCK } from "../../api/backend";
import { href, paths, type Route } from "../../lib/router";
import { cn } from "../../lib/util";
import { isSessionActive, useCalib } from "../../store/calib";
import { useConnection } from "../../store/connection";
import { useDevice } from "../../store/device";
import { isLive, useSession } from "../../store/session";
import { LogoMark } from "./Logo";

type Item = { key: string; label: string; icon: LucideIcon; to: string; match: Route["name"][] };

const ITEMS: Item[] = [
  { key: "home", label: "Home", icon: House, to: paths.home(), match: ["home", "project", "viewer"] },
  { key: "scan", label: "Scan", icon: ScanLine, to: paths.scan(), match: ["scan"] },
  { key: "setup", label: "Setup", icon: Crosshair, to: paths.setup(), match: ["setup", "calibrate"] },
  { key: "device", label: "Device", icon: Cpu, to: paths.device(), match: ["device"] },
  { key: "settings", label: "Settings", icon: Settings, to: paths.settings(), match: ["settings"] },
];

export function Sidebar({ route }: { route: Route }) {
  const scanning = useSession((s) => isLive(s.session));
  const paused = useSession((s) => s.session?.state === "paused");
  const calibMissing = useDevice((s) => !!s.device?.connected && !s.device.calibration?.loaded);
  const drift = useDevice((s) => s.device?.launch_check?.state === "drift");
  const calibrating = useCalib((s) => isSessionActive(s.session));
  const deviceBusy = useDevice((s) => !!s.device?.busy || !!s.simJobId);
  const version = useConnection((s) => s.health?.version ?? s.serverVersion);
  const setupBadge = calibMissing || drift ? "warn" : calibrating ? "accent" : null;
  const setupLabel = drift
    ? "Setup: the launch check found calibration drift"
    : calibMissing
      ? "Setup: no calibration yet"
      : calibrating
        ? "Setup: a calibration is in progress"
        : "Setup";

  return (
    <nav
      aria-label="Main"
      className="flex w-[76px] shrink-0 flex-col items-center border-r border-line bg-panel pb-3 pt-3"
    >
      <a href={href(paths.home())} className="mb-4 rounded-xl" aria-label="OpenHandScan Studio home" title="OpenHandScan Studio">
        <LogoMark size={34} />
      </a>
      <ul className="flex w-full flex-1 flex-col items-center gap-1 px-2">
        {ITEMS.map((it) => {
          const active = it.match.includes(route.name);
          const Icon = it.icon;
          const badge =
            it.key === "scan" && scanning ? (paused ? "warn" : "rec") : it.key === "setup" ? setupBadge : it.key === "device" && deviceBusy ? "accent" : null;
          return (
            <li key={it.key} className="w-full">
              <a
                href={href(it.to)}
                aria-label={it.key === "setup" ? setupLabel : it.label}
                title={it.key === "setup" && setupLabel !== "Setup" ? setupLabel : undefined}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "group relative flex w-full flex-col items-center gap-1 rounded-lg px-1 py-2 text-[11px] font-medium transition-colors",
                  active ? "bg-accent-soft text-accent" : "text-muted hover:bg-surface-2 hover:text-fg",
                )}
              >
                {active ? <span className="absolute -left-2 top-2 bottom-2 w-[3px] rounded-r-full bg-accent" aria-hidden /> : null}
                <span className="relative">
                  <Icon size={20} strokeWidth={active ? 2.1 : 1.8} aria-hidden />
                  {badge ? (
                    <span
                      className={cn(
                        "absolute -right-1.5 -top-1 h-2.5 w-2.5 rounded-full border-2 border-panel",
                        badge === "rec" ? "animate-rec bg-rec" : badge === "warn" ? "bg-warn" : "bg-accent",
                      )}
                      aria-hidden
                    />
                  ) : null}
                </span>
                <span className="leading-none">{it.label}</span>
              </a>
            </li>
          );
        })}
      </ul>
      <div className="flex flex-col items-center gap-1.5 text-center">
        {MOCK ? (
          <span
            className="rounded-full border border-warn/40 bg-warn-soft px-1.5 py-0.5 text-[9.5px] font-semibold uppercase tracking-wide text-warn"
            title="Demo mode: an in-browser simulated backend (no engine, no hardware)"
          >
            Demo
          </span>
        ) : null}
        <span className="font-mono text-[10px] text-subtle" title="Engine version">
          {version ? `v${version}` : " "}
        </span>
      </div>
    </nav>
  );
}
