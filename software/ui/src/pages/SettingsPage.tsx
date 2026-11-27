import {
  Activity,
  Camera,
  Cpu,
  FileCog,
  FolderOpen,
  Grid3x3,
  Info,
  Layers,
  Moon,
  Palette,
  RefreshCw,
  ScanLine,
  Settings as SettingsIcon,
  Sun,
  Tag,
  Wand,
  Zap,
  CircleExclamationPoint,
  ChevronDown,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { MOCK } from "../api/backend";
import { ApiError, api } from "../api/http";
import type { LaserSettingsBody, Settings, ThinWalls } from "../api/types";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { EmptyState, Skeleton } from "../components/ui/Feedback";
import { Segmented, Slider, Switch } from "../components/ui/Form";
import { Code, KeyValue, Page, PageHeader, Section } from "../components/ui/Layout";
import { ADVANCED_TITLE, advancedSections, settingLabel } from "../lib/settingsWords";
import { brightnessRows, laserSettingsOf } from "../lib/laserBrightness";
import { FOUR_LASERS, TWO_LASERS, laserChoice, laserColor, laserLabel, type LaserChoice } from "../lib/laserSet";
import { useConnection } from "../store/connection";
import { configPathOf } from "../store/setup";
import { useUi } from "../store/ui";
import { cn } from "../lib/util";
import { OpenFolderButton, SaveLogButton } from "../components/FolderButtons";
import { ComputerLine } from "../components/ComputerLine";

const SECTION_ICON: Record<string, LucideIcon> = {
  general: FolderOpen,
  paths: FolderOpen,
  camera: Camera,
  pico: Cpu,
  emitters: Zap,
  live: Activity,
  process: Wand,
  board: Grid3x3,
  markers: Tag,
  lasers: Zap,
  server: SettingsIcon,
  projects: FolderOpen,
};

function unitFor(key: string): string {
  const k = key.toLowerCase();
  if (k.endsWith("_mm")) return "\u202fmm";
  if (k.endsWith("_s") || k.endsWith("_seconds")) return "\u202fs";
  if (k.endsWith("_hz")) return "\u202fHz";
  if (k.endsWith("_px")) return "\u202fpx";
  if (k.endsWith("_mb")) return "\u202fMB";
  if (k.endsWith("_deg")) return "°";
  if (k === "fps") return "\u202ffps";
  if (k.endsWith("_level")) return "\u202f%";
  return "";
}

const PATHY = /[\\/]|^~/;

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

/**
 * U12: what the settings file says, read only, under a closed "Advanced" (lib/settingsWords.ts has the words): the
 * settings changed on this page are saved there for you; any other is changed in the file.
 */
function AdvancedSettings({ settings, loading, onReload }: { settings: Settings; loading: boolean; onReload: () => void }) {
  const [open, setOpen] = useState(false);
  const file = configPathOf(settings) ?? (typeof settings.config_file === "string" ? settings.config_file : null) ?? "~/.ohs/config.toml";
  const bodyId = "settings-advanced";
  return (
    <section className="mt-5 rounded-xl border border-line bg-surface shadow-sm" aria-label={ADVANCED_TITLE}>
      <h2>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center gap-2 rounded-xl px-4 py-3 text-left hover:bg-surface-2/50"
        >
          <FileCog size={15} className="shrink-0 text-muted" aria-hidden />
          <span className="flex-1 text-sm font-semibold text-fg">{ADVANCED_TITLE}</span>
          <ChevronDown size={15} className={cn("shrink-0 text-subtle transition-transform", open ? "rotate-180" : "")} aria-hidden />
        </button>
      </h2>
      <div id={bodyId} hidden={!open} className="border-t border-line p-4">
        <p className="max-w-3xl text-sm text-muted">
          Everything below comes from the settings file, <Code>{file}</Code>. The settings at the top of this page are saved there for you. To change
          any other, edit the file and start the app again.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <OpenFolderButton what="settings" label="Open the settings folder" />
          <Button size="sm" variant="ghost" icon={RefreshCw} loading={loading} onClick={onReload}>
            Read the file again
          </Button>
        </div>
        <div className="mt-4 grid gap-5 lg:grid-cols-2">
          {advancedSections(settings as Record<string, unknown>).map((sec) => (
            <Section key={sec.key} title={sec.title} icon={SECTION_ICON[sec.key] ?? FileCog} description={sec.help}>
              <KeyValue
                rows={sec.rows.map((r) => [
                  r.help ? (
                    <span className="inline-flex max-w-[12rem] flex-col">
                      <span>{r.label}</span>
                      <span className="text-[11px] leading-4 text-subtle">{r.help}</span>
                    </span>
                  ) : (
                    r.label
                  ),
                  <Value k={r.key} v={r.value} />,
                ])}
              />
            </Section>
          ))}
        </div>
      </div>
    </section>
  );
}

type ComputeInfo = {
  backend?: string;
  status?: string;
  device?: { name?: string; kind?: string; memory_mb?: number } | null;
};

/** Where the heavy processing runs (health.engine.compute, M3): the graphics card's name, or none (U5: the words). */
function ComputeLabel({ c }: { c: unknown }) {
  const v = c && typeof c === "object" ? (c as ComputeInfo) : null;
  if (!v) return <span className="text-subtle">–</span>;
  if (v.backend === "gpu" && v.device?.name) {
    const gb = v.device.memory_mb ? `, ${(v.device.memory_mb / 1024).toFixed(1)}\u202fGB` : "";
    return (
      <Badge tone="ok" dot>
        {`${v.device.name} (${v.device.kind === "discrete" ? "separate card" : v.device.kind === "integrated" ? "built in" : "graphics card"}${gb})`}
      </Badge>
    );
  }
  return <span className="text-xs text-fg">None in use</span>;
}

const THIN_WALLS: ThinWalls[] = ["auto", "on", "off"];

/** `process.thin_walls` of the engine's settings; `null` when this engine has no such setting. */
function thinWallsOf(settings: Settings | null): ThinWalls | null {
  const p = settings?.process;
  const v = p && typeof p === "object" ? (p as Record<string, unknown>).thin_walls : undefined;
  return THIN_WALLS.includes(v as ThinWalls) ? (v as ThinWalls) : null;
}

/** The one meshing setting that can be changed here: saved to the config file, used by the next mesh. */
function MeshingSection({ settings, onSaved }: { settings: Settings; onSaved: (s: Settings) => void }) {
  const value = thinWallsOf(settings);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  if (!value) return null;
  const save = async (v: ThinWalls) => {
    if (v === value || saving) return;
    setSaving(true);
    try {
      onSaved(await api.setProcess({ thin_walls: v }));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, String(e), null, "/api/settings/process"));
    } finally {
      setSaving(false);
    }
  };
  return (
    <Section title="Making the mesh" icon={Layers}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0 flex-1 basis-64">
          <div className="text-sm text-fg">Thin walls and edges</div>
          <div className="text-xs text-subtle">
            Keeps sheet metal, ribs and edges from coming out too thick. Automatic uses it for scans without markers. If a
            mesh shows dents or many more pinholes, set it to Off and make the mesh again. Needs a graphics card.
          </div>
        </div>
        <Segmented
          label="Thin walls and edges"
          value={value}
          disabled={saving}
          onChange={(v) => void save(v)}
          options={[
            { value: "auto", label: "Automatic" },
            { value: "on", label: "On" },
            { value: "off", label: "Off" },
          ]}
        />
      </div>
      {error ? (
        <div className="mt-2 text-xs text-danger" role="alert">
          Couldn't save the setting: {error.detail}
          {error.hint ? <span className="text-subtle"> {error.hint}</span> : null}
        </div>
      ) : null}
    </Section>
  );
}

/** `lasers.lines` of the engine's settings; `null` when this engine has no such setting (an older engine). */
function laserLinesOf(settings: Settings | null): string[] | null {
  const l = settings?.lasers;
  const v = l && typeof l === "object" ? (l as Record<string, unknown>).lines : undefined;
  return Array.isArray(v) && v.every((x) => typeof x === "string") ? (v as string[]) : null;
}

/** The build's line lasers: two (standard) or four. Saved to the config file; another list stays as it is unless a choice is picked. */
function LasersSection({ settings, onSaved }: { settings: Settings; onSaved: (s: Settings) => void }) {
  const lines = laserLinesOf(settings);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  if (!lines) return null;
  const choice = laserChoice(lines);
  const save = async (v: LaserChoice) => {
    if (v === choice || v === "custom" || saving) return;
    setSaving(true);
    try {
      onSaved(await api.setLasers({ lines: [...(v === "four" ? FOUR_LASERS : TWO_LASERS)] }));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, String(e), null, "/api/settings/lasers"));
    } finally {
      setSaving(false);
    }
  };
  const options: { value: LaserChoice; label: string; disabled?: boolean; title?: string }[] = [
    { value: "two", label: "Two (standard)" },
    { value: "four", label: "Four" },
  ];
  if (choice === "custom") options.push({ value: "custom", label: "Custom", disabled: true, title: "Set in the settings file" });
  return (
    <Section title="Line lasers" icon={Zap}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0 flex-1 basis-64">
          <div className="text-sm text-fg">Line lasers</div>
          <div className="text-xs text-subtle">
            How many line lasers your scanner has. The standard build has two; the 4-laser build adds a second vertical laser
            on the left and a second horizontal laser low on the right. After a change, calibrate the lasers again (setup step 5).
            The lasers you calibrated before keep their calibration.
          </div>
        </div>
        <Segmented label="Line lasers" value={choice} disabled={saving} onChange={(v) => void save(v)} options={options} />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-1.5" aria-label="Configured lasers">
        {lines.map((n) => (
          <span key={n} className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-2 px-2 py-0.5 text-xs text-fg">
            <span className="h-2 w-2 rounded-full" style={{ background: laserColor(n, lines) }} aria-hidden />
            {laserLabel(n)}
            {choice === "custom" ? <span className="font-mono text-[10.5px] text-subtle">{n}</span> : null}
          </span>
        ))}
      </div>
      {choice === "custom" ? (
        <div className="mt-2 text-xs text-subtle">
          Your config file lists its own lasers. Pick Two or Four to replace that list; otherwise it stays as it is.
        </div>
      ) : null}
      {error ? (
        <div className="mt-2 text-xs text-danger" role="alert">
          Couldn't save the setting: {error.detail}
          {error.hint ? <span className="text-subtle"> {error.hint}</span> : null}
        </div>
      ) : null}
      <LaserBrightness settings={settings} onSaved={onSaved} />
    </Section>
  );
}

/** A build with two lasers in a family: whether each laser has its own wire, and each laser's brightness (saved on release). */
function LaserBrightness({ settings, onSaved }: { settings: Settings; onSaved: (s: Settings) => void }) {
  const lasers = laserSettingsOf(settings);
  const server = lasers?.brightness ?? {};
  const serverKey = JSON.stringify(server);
  const [local, setLocal] = useState<Record<string, number>>(server);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const dragging = useRef(false);
  useEffect(() => {
    if (!dragging.current) setLocal(JSON.parse(serverKey) as Record<string, number>);
  }, [serverKey]);
  const rows = brightnessRows(lasers ? { ...lasers, brightness: local } : null);
  if (!lasers || !rows) return null;
  const own = lasers.own_channels !== false;

  const send = async (body: LaserSettingsBody) => {
    setSaving(true);
    try {
      onSaved(await api.setLasers(body));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, String(e), null, "/api/settings/lasers"));
      setLocal(JSON.parse(serverKey) as Record<string, number>);
    } finally {
      setSaving(false);
    }
  };
  const commit = (name: string, v: number) => {
    dragging.current = false;
    const next = { ...local, [name]: v };
    setLocal(next);
    if ((server[name] ?? 100) !== v) void send({ brightness: next });
  };

  return (
    <div className="mt-4 space-y-4 border-t border-line pt-4">
      <Switch
        label="Each laser has its own wire (firmware 1.2.0 or later)"
        description="Lets each laser have its own brightness, and lets the laser calibration light them one at a time."
        checked={own}
        disabled={saving}
        onChange={(v) => void send({ own_channels: v })}
      />
      <div>
        <div className="text-sm text-fg">Brightness</div>
        <div className="text-xs text-subtle">
          Each laser in percent of its family's level (the lasers and dots sliders on the Device page, shown once the camera and the control board are connected). Turn down a laser whose line looks brighter than the others.
        </div>
      </div>
      <div className="space-y-3">
        {rows.map((r) => (
          <div key={r.name}>
            <div className="mb-1 flex items-center justify-between">
              <label htmlFor={`laser-brightness-${r.name}`} className="flex items-center gap-1.5 text-sm text-fg">
                <span className="h-2 w-2 rounded-full" style={{ background: laserColor(r.name, lasers.lines) }} aria-hidden />
                {r.label}
              </label>
              <span className="w-12 text-right font-mono text-sm text-fg tnum">{Math.round(r.value)}%</span>
            </div>
            <Slider
              id={`laser-brightness-${r.name}`}
              label={`${r.label} brightness`}
              value={r.value}
              onChange={(v) => {
                dragging.current = true;
                setLocal((l) => ({ ...l, [r.name]: v }));
              }}
              onCommit={(v) => commit(r.name, v)}
            />
          </div>
        ))}
      </div>
      {!own ? (
        <div className="text-xs text-subtle">Each pair of lasers shares one wire, so both lasers of a pair get the same brightness.</div>
      ) : null}
      {error ? (
        <div className="text-xs text-danger" role="alert">
          Couldn't save the setting: {error.detail}
          {error.hint ? <span className="text-subtle"> {error.hint}</span> : null}
        </div>
      ) : null}
    </div>
  );
}

export function SettingsPage() {
  const theme = useUi((s) => s.theme);
  const setTheme = useUi((s) => s.setTheme);
  const autoMesh = useUi((s) => s.autoMesh);
  const setAutoMesh = useUi((s) => s.setAutoMesh);
  const trackingNumbers = useUi((s) => s.trackingNumbers);
  const setTrackingNumbers = useUi((s) => s.setTrackingNumbers);
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
      {/* U12: the settings a person changes on top; what the file says under a closed "Advanced" at the bottom */}
      <PageHeader title="Settings" subtitle="How the app works on this computer. A change here is saved right away and used from the next scan or mesh on." />

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
            description="At Standard quality, the quality a press on Make mesh uses. Turn it off to choose the quality yourself each time; the job can always be cancelled."
            checked={autoMesh}
            onChange={setAutoMesh}
          />
        </Section>
        <Section title="While scanning" icon={ScanLine}>
          <Switch
            label="Show the tracking numbers"
            description="How well each frame matched the model, and how far apart the surfaces were, beside the tracking status. For finding out why tracking is weak; the words say the same for everyday scans."
            checked={trackingNumbers}
            onChange={setTrackingNumbers}
          />
        </Section>
        {settings ? (
          <>
            <MeshingSection settings={settings} onSaved={setSettings} />
            <LasersSection settings={settings} onSaved={setSettings} />
          </>
        ) : error ? null : (
          [0, 1].map((i) => (
            <div key={i} className="space-y-3 rounded-xl border border-line bg-surface p-4" aria-busy="true">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-2/3" />
            </div>
          ))
        )}
        <Section title="About" icon={Info}>
          {/* U5: what this computer can do, before something is missing */}
          <ComputerLine className="mb-3" />
          <KeyValue
            rows={[
              ["Studio UI", <span className="font-mono text-xs">0.3.0</span>],
              ["Engine", health ? <span className="font-mono text-xs">{`${health.app} ${health.version}`}</span> : serverVersion ?? "–"],
              ["Graphics card", health ? <ComputeLabel c={(health.engine as { compute?: unknown }).compute} /> : "–"],
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
          {/* U34: the app's log, inside the app: its folder, and a copy to attach to a bug report */}
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3">
            <OpenFolderButton what="logs" label="Open the log folder" />
            <SaveLogButton />
          </div>
          <p className="mt-1.5 text-xs text-subtle">If something goes wrong, save the log and attach it to your bug report.</p>
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
      ) : settings ? (
        <AdvancedSettings settings={settings} loading={loading} onReload={() => void load()} />
      ) : null}
    </Page>
  );
}
