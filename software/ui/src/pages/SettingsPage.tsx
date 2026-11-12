import {
  Activity,
  Camera,
  Cpu,
  FileCog,
  FolderOpen,
  Grid3x3,
  Info,
  Moon,
  Palette,
  RefreshCw,
  Settings as SettingsIcon,
  Sun,
  Tag,
  Wand,
  Zap,
  CircleExclamationPoint,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { MOCK } from "../api/backend";
import { ApiError, api } from "../api/http";
import type { Settings } from "../api/types";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { EmptyState, Skeleton } from "../components/ui/Feedback";
import { Segmented, Switch } from "../components/ui/Form";
import { Code, KeyValue, Page, PageHeader, Section } from "../components/ui/Layout";
import { humanize } from "../lib/format";
import { useConnection } from "../store/connection";
import { configPathOf } from "../store/setup";
import { useUi } from "../store/ui";

const SECTION_ICON: Record<string, LucideIcon> = {
  paths: FolderOpen,
  camera: Camera,
  pico: Cpu,
  emitters: Zap,
  live: Activity,
  process: Wand,
  board: Grid3x3,
  markers: Tag,
  server: SettingsIcon,
  projects: FolderOpen,
};

const SECTION_HELP: Record<string, string> = {
  paths: "Where calibration and scans are stored",
  camera: "Stereo camera mode and exposure",
  pico: "Emitter / IMU controller on USB",
  emitters: "Emitter levels per profile: vertical, horizontal, dots (%)",
  live: "Preview-quality processing while scanning",
  process: "Meshing defaults (Standard and Best quality)",
  board: "ChArUco calibration board",
  markers: "AprilTag stickers",
};

function unitFor(key: string): string {
  const k = key.toLowerCase();
  if (k.endsWith("_mm")) return " mm";
  if (k.endsWith("_s") || k.endsWith("_seconds")) return " s";
  if (k.endsWith("_hz")) return " Hz";
  if (k.endsWith("_px")) return " px";
  if (k.endsWith("_deg")) return "°";
  if (k === "fps") return " fps";
  if (k.endsWith("_level")) return " %";
  return "";
}

const PATHY = /[\\/]|^~/;

const ACRONYMS = new Set(["imu", "fps", "id", "dpi", "tsdf", "ply", "stl", "obj", "ui", "api", "url", "lr", "gpu", "cpu"]);
const SPECIAL: Record<string, string> = {
  fourcc: "FourCC",
  swap_lr: "Swap left / right",
  config_file: "Config file",
  projects_dir: "Projects folder",
  scans_dir: "Scans folder",
  square_mm: "Square size",
  marker_mm: "Marker size",
  tag_mm: "Tag size",
  voxel_mm: "Voxel size",
  markers_dot_level: "Dots in markers mode",
  squares_x: "Squares across",
  squares_y: "Squares down",
};

/** "imu_rate_hz" → "IMU rate": the unit is shown with the value instead. */
function settingLabel(key: string): string {
  if (SPECIAL[key]) return SPECIAL[key];
  const base = key.replace(/_(mm|s|seconds|hz|px|deg|level)$/i, "");
  const words = base.split(/[_-]+/).filter(Boolean);
  return words
    .map((w, i) => (ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(" ");
}

function Value({ k, v }: { k: string; v: unknown }): ReactNode {
  if (v === null || v === undefined) return <span className="text-subtle italic">auto</span>;
  if (typeof v === "boolean")
    return (
      <Badge tone={v ? "ok" : "neutral"} dot>
        {v ? "On" : "Off"}
      </Badge>
    );
  if (typeof v === "number") return <span className="font-mono text-[12.5px] tnum">{`${Number.isInteger(v) ? v : v.toFixed(4).replace(/0+$/, "").replace(/\.$/, "")}${unitFor(k)}`}</span>;
  if (typeof v === "string")
    return PATHY.test(v) ? <span className="break-all font-mono text-xs">{v}</span> : <span>{v === "" ? <span className="text-subtle italic">empty</span> : v}</span>;
  if (Array.isArray(v)) {
    if (v.every((x) => typeof x === "number"))
      return <span className="font-mono text-[12.5px] tnum">{v.join(" / ")}</span>;
    if (v.every((x) => typeof x === "string"))
      return (
        <span className="flex flex-wrap gap-1">
          {v.map((x) => (
            <Badge key={String(x)}>{String(x)}</Badge>
          ))}
        </span>
      );
    return <span className="break-all font-mono text-xs">{JSON.stringify(v)}</span>;
  }
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>);
    return (
      <div className="rounded-lg border border-line bg-surface-2/60 px-3 py-2">
        <KeyValue dense rows={entries.map(([ck, cv]) => [settingLabel(ck), <Value k={ck} v={cv} />])} />
      </div>
    );
  }
  return String(v);
}

function SettingsSections({ settings }: { settings: Settings }) {
  const entries = Object.entries(settings);
  const scalars = entries.filter(([, v]) => v === null || typeof v !== "object" || Array.isArray(v));
  const groups = entries.filter(([, v]) => v !== null && typeof v === "object" && !Array.isArray(v));
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      {scalars.length ? (
        <Section title="General" icon={SettingsIcon}>
          <KeyValue rows={scalars.map(([k, v]) => [settingLabel(k), <Value k={k} v={v} />])} />
        </Section>
      ) : null}
      {groups.map(([k, v]) => (
        <Section key={k} title={humanize(k)} icon={SECTION_ICON[k] ?? FileCog} description={SECTION_HELP[k]}>
          <KeyValue rows={Object.entries(v as Record<string, unknown>).map(([ck, cv]) => [settingLabel(ck), <Value k={ck} v={cv} />])} />
        </Section>
      ))}
    </div>
  );
}

type ComputeInfo = {
  backend?: string;
  status?: string;
  device?: { name?: string; kind?: string; memory_mb?: number } | null;
};

/** Where the heavy processing runs (health.engine.compute, M3): the GPU's name, or the CPU and why. */
function ComputeLabel({ c }: { c: unknown }) {
  const v = c && typeof c === "object" ? (c as ComputeInfo) : null;
  if (!v) return <span className="text-subtle">–</span>;
  if (v.backend === "gpu" && v.device?.name) {
    const gb = v.device.memory_mb ? `, ${(v.device.memory_mb / 1024).toFixed(1)} GB` : "";
    return (
      <Badge tone="ok" dot>
        {`GPU: ${v.device.name} (${v.device.kind ?? "gpu"}${gb})`}
      </Badge>
    );
  }
  return (
    <span className="text-xs text-fg" title={v.status}>
      CPU <span className="text-subtle">· {v.status ?? "no GPU in use"}</span>
    </span>
  );
}

export function SettingsPage() {
  const theme = useUi((s) => s.theme);
  const setTheme = useUi((s) => s.setTheme);
  const autoMesh = useUi((s) => s.autoMesh);
  const setAutoMesh = useUi((s) => s.setAutoMesh);
  const health = useConnection((s) => s.health);
  const link = useConnection((s) => s.link);
  const serverVersion = useConnection((s) => s.serverVersion);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      setSettings(await api.settings());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, String(e), null, "/api/settings"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  return (
    <Page wide>
      <PageHeader
        title="Settings"
        subtitle={
          <>
            Edit <Code>{(settings && configPathOf(settings)) ?? "~/.ohs/config.toml"}</Code> (<Code>ohs config --init</Code> writes a
            commented template), then restart the engine. Setup saves the camera choice and the paper size there for you.
          </>
        }
        actions={
          <Button icon={RefreshCw} loading={loading} onClick={() => void load()}>
            Reload
          </Button>
        }
      />

      <div className="mb-5 grid gap-5 lg:grid-cols-2">
        <Section title="Appearance" icon={Palette}>
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-sm text-fg">Theme</div>
              <div className="text-xs text-subtle">Dark is easier on the eyes in a dim garage; light suits a bright shop.</div>
            </div>
            <Segmented
              label="Theme"
              value={theme}
              onChange={setTheme}
              options={[
                { value: "dark", label: "Dark", icon: Moon },
                { value: "light", label: "Light", icon: Sun },
              ]}
            />
          </div>
        </Section>
        <Section title="After a scan" icon={Wand}>
          <Switch
            label="Make the mesh as soon as a scan stops"
            description="Standard quality, as the Make mesh button. Turn it off to choose the quality yourself each time; the job can always be cancelled."
            checked={autoMesh}
            onChange={setAutoMesh}
          />
        </Section>
        <Section title="About" icon={Info}>
          <KeyValue
            rows={[
              ["Studio UI", <span className="font-mono text-xs">0.3.0 (M3)</span>],
              ["Engine", health ? <span className="font-mono text-xs">{`${health.app} ${health.version}`}</span> : serverVersion ?? "–"],
              ["Processing", health ? <ComputeLabel c={(health.engine as { compute?: unknown }).compute} /> : "–"],
              [
                "Engine libraries",
                health ? (
                  <span className="font-mono text-xs">
                    {Object.entries(health.engine ?? {})
                      .filter(([k]) => k !== "compute")
                      .map(([k, v]) => `${k} ${String(v)}`)
                      .join(" · ") || "–"}
                  </span>
                ) : (
                  "–"
                ),
              ],
              [
                "Live link",
                <Badge tone={link === "open" ? "ok" : link === "connecting" ? "accent" : "warn"} dot>
                  {link === "open" ? "Connected" : link === "connecting" ? "Connecting" : "Offline"}
                </Badge>,
              ],
              MOCK ? ["Mode", <Badge tone="warn">Demo (in-browser mock engine)</Badge>] : null,
            ]}
          />
        </Section>
      </div>

      {error && !settings ? (
        <div className="rounded-xl border border-line bg-surface">
          <EmptyState
            tone="danger"
            icon={CircleExclamationPoint}
            title="Couldn't load the settings"
            actions={
              <Button icon={RefreshCw} loading={loading} onClick={() => void load()}>
                Try again
              </Button>
            }
          >
            {error.detail}
            {error.hint ? <div className="mt-1 text-subtle">{error.hint}</div> : null}
          </EmptyState>
        </div>
      ) : !settings ? (
        <div className="grid gap-5 lg:grid-cols-2">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="space-y-3 rounded-xl border border-line bg-surface p-4">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-5/6" />
              <Skeleton className="h-4 w-2/3" />
            </div>
          ))}
        </div>
      ) : (
        <SettingsSections settings={settings} />
      )}
    </Page>
  );
}
