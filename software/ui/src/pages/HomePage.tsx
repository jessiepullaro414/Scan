import {
  ArrowRight,
  Check,
  Cpu,
  Crosshair,
  Ellipsis,
  FlaskConical,
  FolderPlus,
  Pencil,
  Plus,
  Ruler,
  ScanLine,
  Search,
  Trash,
  Box,
  RefreshCw,
  CircleExclamationPoint,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/http";
import type { ProjectSummary } from "../api/types";
import { ProjectThumb } from "../components/ProjectThumb";
import { Button, IconButton } from "../components/ui/Button";
import { ConfirmDialog, PromptDialog } from "../components/ui/Dialog";
import { EmptyState, Skeleton } from "../components/ui/Feedback";
import { TextInput } from "../components/ui/Form";
import { Code, Page, PageHeader } from "../components/ui/Layout";
import { Menu } from "../components/ui/Popover";
import { fmtDateTime, fmtRelative } from "../lib/format";
import { href, navigate, paths } from "../lib/router";
import { firstUnfinished, minutesLeft, setupProgress, STATE_LABEL, STEP_META, STEP_ORDER } from "../lib/setup";
import { cn, useTicker } from "../lib/util";
import { useDevice } from "../store/device";
import { useProjects } from "../store/projects";
import { useSetup } from "../store/setup";
import { toast } from "../store/toasts";
import { useUi } from "../store/ui";

/** First run: the setup wizard's card, with its progress and the simulator as another way in. */
function SetupCard() {
  const setup = useSetup((s) => s.setup);
  const dismiss = useSetup((s) => s.dismiss);
  const connect = useDevice((s) => s.connect);
  const simConnected = useDevice((s) => !!s.device?.connected && s.device.source === "sim");
  if (!setup?.first_run) return null;
  const prog = setupProgress(setup);
  const next = firstUnfinished(setup);
  const mins = minutesLeft(setup);
  const sim = setup.target === "sim";
  return (
    <section className="mb-6 animate-fade-in overflow-hidden rounded-2xl border border-accent-line/60 bg-surface shadow-sm" aria-label="Set up your scanner">
      <div className="flex flex-wrap items-center gap-6 p-6">
        <span className="grid h-14 w-14 shrink-0 place-items-center rounded-2xl border border-accent-line bg-accent-soft text-accent">
          <Crosshair size={26} aria-hidden />
        </span>
        <div className="min-w-[260px] flex-1">
          <div className="text-xs font-medium text-accent">{sim ? "Simulator sandbox" : "First run"}</div>
          <h2 className="font-display text-xl font-semibold text-fg">Set up your scanner</h2>
          <p className="mt-0.5 text-sm text-muted">
            {prog.done === 0
              ? `Connect it, test it, print the board and calibrate: about ${mins} minutes, once. Every step says why it matters.`
              : `${prog.done} of ${prog.total} steps done. Next: ${STEP_META[next].title}${mins ? `, about ${mins} minutes left` : ""}.`}
          </p>
          <ol className="mt-3 flex flex-wrap gap-1.5" aria-label="Setup steps">
            {STEP_ORDER.map((id) => {
              const st = setup.steps.find((s) => s.id === id);
              const state = st?.state ?? "todo";
              return (
                <li key={id}>
                  <a
                    href={href(paths.setup(id))}
                    title={`${STEP_META[id].title}: ${STATE_LABEL[state]}${st?.summary ? `. ${st.summary}` : ""}`}
                    className={cn(
                      "inline-flex h-6 items-center gap-1 rounded-full border px-2 text-[11px] font-medium transition-colors hover:border-line-strong",
                      state === "done" ? "border-ok/30 bg-ok-soft text-ok" : state === "warn" ? "border-warn/30 bg-warn-soft text-warn" : "border-line text-muted",
                    )}
                  >
                    {state === "done" ? <Check size={11} strokeWidth={3} aria-hidden /> : null}
                    {STEP_META[id].title}
                  </a>
                </li>
              );
            })}
          </ol>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" size="lg" iconRight={ArrowRight} onClick={() => navigate(paths.setup(prog.done === 0 ? null : next))}>
            {prog.done === 0 ? "Start setup" : "Continue setup"}
          </Button>
          {!simConnected ? (
            <Button
              size="lg"
              icon={FlaskConical}
              onClick={async () => {
                navigate(paths.device());
                await connect({ source: "sim" });
              }}
            >
              Try the simulator
            </Button>
          ) : null}
          <Button variant="ghost" onClick={() => void dismiss(true)} title="You can turn the card back on in Setup">
            Not now
          </Button>
        </div>
      </div>
    </section>
  );
}

/** The engine's projects folder (settings paths.projects_dir), with the default until it loads. */
function useProjectsDir(): string {
  const [dir, setDir] = useState("~/OpenHandScan/projects");
  useEffect(() => {
    let alive = true;
    api
      .settings()
      .then((s) => {
        const p = (s as { paths?: { projects_dir?: unknown } }).paths?.projects_dir;
        if (alive && typeof p === "string" && p) setDir(p);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);
  return dir;
}

function ProjectCard({
  p,
  onRename,
  onDelete,
}: {
  p: ProjectSummary;
  onRename: () => void;
  onDelete: () => void;
}) {
  const setCurrent = useUi((s) => s.setCurrentProject);
  const current = useUi((s) => s.currentProjectId === p.id);
  return (
    <article
      className={cn(
        "group relative overflow-hidden rounded-xl border bg-surface shadow-sm transition-[border-color,box-shadow,transform] duration-150",
        "hover:-translate-y-px hover:border-line-strong hover:shadow-md focus-within:border-line-strong",
        current ? "border-accent-line/70" : "border-line",
      )}
    >
      <a href={href(paths.project(p.id))} className="block rounded-xl focus-visible:outline-offset-[-2px]" aria-label={`Open project ${p.name}`}>
        <ProjectThumb id={p.id} name={p.name} url={p.thumbnail_url} scans={p.n_scans} className="aspect-[16/10] w-full" />
        <div className="px-3.5 pb-3 pt-2.5">
          <div className="flex items-center gap-2">
            <h3 className="min-w-0 flex-1 truncate text-sm font-semibold text-fg">{p.name}</h3>
            {current ? <span className="text-[10.5px] font-medium text-accent">Current</span> : null}
          </div>
          <div className="mt-0.5 flex items-center gap-1.5 text-xs text-subtle">
            <span>
              {p.n_scans} scan{p.n_scans === 1 ? "" : "s"}
            </span>
            <span aria-hidden>·</span>
            <span title={`Modified ${fmtDateTime(p.modified)}`}>edited {fmtRelative(p.modified)}</span>
          </div>
        </div>
      </a>
      <div className="absolute right-2 top-2 flex gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        <Button
          size="sm"
          variant="secondary"
          icon={ScanLine}
          className="glass !border-white/10"
          onClick={() => {
            setCurrent(p.id);
            navigate(paths.scan(p.id));
          }}
        >
          Scan
        </Button>
        <Menu
          label={`Actions for ${p.name}`}
          items={[
            { label: "Open", icon: ArrowRight, onSelect: () => navigate(paths.project(p.id)) },
            {
              label: "New scan here",
              icon: ScanLine,
              onSelect: () => {
                setCurrent(p.id);
                navigate(paths.scan(p.id));
              },
            },
            { label: "Rename…", icon: Pencil, onSelect: onRename },
            "separator",
            { label: "Move to trash…", icon: Trash, danger: true, onSelect: onDelete },
          ]}
          renderTrigger={(t) => (
            <IconButton
              ref={t.ref}
              onClick={t.onClick}
              aria-haspopup={t["aria-haspopup"]}
              aria-expanded={t["aria-expanded"]}
              icon={Ellipsis}
              label={`More actions for ${p.name}`}
              variant="secondary"
              size="sm"
              className="glass !border-white/10"
            />
          )}
        />
      </div>
    </article>
  );
}

function GetStarted({ onCreate }: { onCreate: () => void }) {
  const connect = useDevice((s) => s.connect);
  const steps = [
    {
      icon: Cpu,
      title: "Connect the scanner",
      text: "Plug in the stereo camera and the Pico, or pick the simulator demo: no hardware needed.",
    },
    {
      icon: ScanLine,
      title: "Scan the part",
      text: "Sweep slowly 200–350 mm from the surface. The model grows live; the raw stream is always recorded.",
    },
    {
      icon: Ruler,
      title: "Make the mesh and measure",
      text: "One click builds the mesh at full resolution. Measure in mm, then export STL or PLY for CAD.",
    },
  ];
  return (
    <div className="animate-fade-in overflow-hidden rounded-2xl border border-line bg-surface shadow-sm">
      <div className="grid gap-0 lg:grid-cols-[1.1fr_1fr]">
        <div className="p-8">
          <div className="mb-2 inline-flex items-center gap-1.5 rounded-full border border-accent-line/60 bg-accent-soft px-2.5 py-1 text-xs font-medium text-accent">
            <FolderPlus size={13} aria-hidden /> No projects yet
          </div>
          <h2 className="font-display text-2xl font-semibold tracking-[-0.01em] text-fg">From part to mesh in three steps</h2>
          <p className="mt-2 max-w-lg text-sm text-muted">
            A project keeps the scans of one job together: an intake, a fender, a bracket. Everything stays on this computer, in
            plain files you can open without Studio.
          </p>
          <ol className="mt-6 space-y-4">
            {steps.map((s, i) => (
              <li key={s.title} className="flex gap-3.5">
                <div className="relative grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-line bg-surface-2 text-accent">
                  <s.icon size={17} aria-hidden />
                  <span className="absolute -right-1.5 -top-1.5 grid h-4 w-4 place-items-center rounded-full bg-accent text-[9.5px] font-bold text-accent-fg">
                    {i + 1}
                  </span>
                </div>
                <div>
                  <div className="text-sm font-semibold text-fg">{s.title}</div>
                  <div className="text-sm text-muted">{s.text}</div>
                </div>
              </li>
            ))}
          </ol>
          <div className="mt-7 flex flex-wrap gap-2">
            <Button variant="primary" size="lg" icon={Plus} onClick={onCreate}>
              Create your first project
            </Button>
            <Button
              variant="secondary"
              size="lg"
              icon={FlaskConical}
              onClick={async () => {
                navigate(paths.device());
                await connect({ source: "sim" });
              }}
            >
              Try the simulator demo
            </Button>
          </div>
        </div>
        <div className="relative hidden min-h-[320px] border-l border-line viewport-bg lg:block" aria-hidden>
          <HeroArt />
        </div>
      </div>
    </div>
  );
}

/** A static illustration of the live scan view (scanner frustum over a part). */
function HeroArt() {
  return (
    <svg viewBox="0 0 400 320" className="absolute inset-0 h-full w-full" preserveAspectRatio="xMidYMid meet">
      <defs>
        <linearGradient id="hero-sweep" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--c-accent)" stopOpacity="0.32" />
          <stop offset="1" stopColor="var(--c-accent)" stopOpacity="0" />
        </linearGradient>
        <radialGradient id="hero-ball" cx="0.38" cy="0.32" r="0.8">
          <stop offset="0" stopColor="#7fd38c" />
          <stop offset="0.55" stopColor="#3f9a52" />
          <stop offset="1" stopColor="#1f4d2a" />
        </radialGradient>
      </defs>
      <g stroke="var(--c-line-strong)" strokeWidth="1" opacity="0.7">
        {Array.from({ length: 11 }, (_, i) => (
          <line key={`h${i}`} x1={40} y1={210 + i * 9} x2={360} y2={210 + i * 9} opacity={1 - i * 0.07} />
        ))}
        {Array.from({ length: 13 }, (_, i) => (
          <line key={`v${i}`} x1={80 + i * 20} y1={210} x2={20 + i * 30} y2={300} opacity={0.6} />
        ))}
      </g>
      <path d="M95 236 L305 236 L335 286 L65 286 Z" fill="#d9b44a" opacity="0.22" />
      <path d="M120 240 L280 240 L300 276 L100 276 Z" fill="#59b86a" opacity="0.42" />
      <ellipse cx="200" cy="246" rx="58" ry="12" fill="#000" opacity="0.25" />
      <circle cx="200" cy="200" r="48" fill="url(#hero-ball)" />
      <path d="M140 92 L200 70 L262 92 L200 200 Z" fill="url(#hero-sweep)" />
      <g stroke="var(--c-accent)" strokeWidth="1.6" fill="none" strokeLinejoin="round">
        <path d="M170 60 L230 60 L238 76 L162 76 Z" fill="var(--c-surface-2)" />
        <path d="M200 76 L140 120 M200 76 L262 120 M140 120 L262 120" strokeOpacity="0.85" />
      </g>
      <path d="M200 156 Q214 200 200 246" stroke="#ff4d4d" strokeWidth="2" fill="none" opacity="0.9" />
      <path d="M152 204 Q200 214 248 204" stroke="#4dd8ff" strokeWidth="2" fill="none" opacity="0.9" />
      <g transform="translate(332 104)">
        <rect x="-8" y="-60" width="16" height="120" rx="8" fill="var(--c-surface-3)" stroke="var(--c-line-strong)" />
        <rect x="-8" y="-30" width="16" height="50" rx="4" fill="var(--c-ok)" opacity="0.35" />
        <path d="M-14 -6 L-8 0 L-14 6 Z" fill="var(--c-ok)" />
      </g>
    </svg>
  );
}

export function HomePage() {
  const list = useProjects((s) => s.list);
  const loading = useProjects((s) => s.listLoading);
  const error = useProjects((s) => s.listError);
  const fetchList = useProjects((s) => s.fetchList);
  const create = useProjects((s) => s.create);
  const rename = useProjects((s) => s.rename);
  const remove = useProjects((s) => s.remove);
  const setCurrent = useUi((s) => s.setCurrentProject);
  const current = useUi((s) => s.currentProjectId);
  const projectsDir = useProjectsDir();
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState<ProjectSummary | null>(null);
  const [deleting, setDeleting] = useState<ProjectSummary | null>(null);
  const [query, setQuery] = useState("");
  useTicker(30_000); // keep "edited 5 min ago" fresh

  useEffect(() => {
    void fetchList();
  }, [fetchList]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (list ?? []).filter((p) => !q || p.name.toLowerCase().includes(q));
  }, [list, query]);

  const startScan = () => {
    const target = current && list?.some((p) => p.id === current) ? current : list?.[0]?.id ?? null;
    if (target) setCurrent(target);
    navigate(paths.scan(target));
  };

  const empty = list !== null && list.length === 0;

  return (
    <Page wide>
      <PageHeader
        title="Projects"
        subtitle="Each project is a folder on this computer with its scans, results and measurements."
        actions={
          <>
            {list && list.length > 4 ? (
              <TextInput
                icon={Search}
                placeholder="Search projects"
                aria-label="Search projects"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                className="w-56"
              />
            ) : null}
            <Button icon={FolderPlus} onClick={() => setCreating(true)}>
              New project
            </Button>
            <Button variant="primary" icon={ScanLine} onClick={startScan}>
              New scan
            </Button>
          </>
        }
      />

      <SetupCard />

      {error && !list ? (
        <div className="rounded-xl border border-line bg-surface">
          <EmptyState
            tone="danger"
            icon={CircleExclamationPoint}
            title="Couldn't load the projects"
            actions={
              <Button icon={RefreshCw} loading={loading} onClick={() => void fetchList()}>
                Try again
              </Button>
            }
          >
            {error.detail}
            {error.hint ? <div className="mt-1 text-subtle">{error.hint}</div> : null}
          </EmptyState>
        </div>
      ) : list === null ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-4" aria-busy="true" aria-label="Loading projects">
          {Array.from({ length: 8 }, (_, i) => (
            <div key={i} className="overflow-hidden rounded-xl border border-line bg-surface">
              <Skeleton className="aspect-[16/10] w-full !rounded-none" />
              <div className="space-y-2 px-3.5 pb-3.5 pt-3">
                <Skeleton className="h-4 w-2/3" />
                <Skeleton className="h-3 w-1/3" />
              </div>
            </div>
          ))}
        </div>
      ) : empty ? (
        <GetStarted onCreate={() => setCreating(true)} />
      ) : (
        <>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-4">
            {shown.map((p) => (
              <ProjectCard key={p.id} p={p} onRename={() => setRenaming(p)} onDelete={() => setDeleting(p)} />
            ))}
            {!query ? (
              <button
                type="button"
                onClick={() => setCreating(true)}
                className="group grid min-h-[200px] place-items-center rounded-xl border border-dashed border-line-strong text-muted transition-colors hover:border-accent-line hover:bg-accent-soft hover:text-accent"
              >
                <span className="flex flex-col items-center gap-2 text-sm font-medium">
                  <span className="grid h-10 w-10 place-items-center rounded-full border border-current/30">
                    <Plus size={18} aria-hidden />
                  </span>
                  New project
                </span>
              </button>
            ) : null}
          </div>
          {query && shown.length === 0 ? (
            <EmptyState icon={Search} title={`No project matches “${query}”`} compact>
              Check the spelling, or clear the search.
            </EmptyState>
          ) : null}
          <p className="mt-8 flex items-center gap-2 text-xs text-subtle">
            <Box size={13} aria-hidden /> Projects live in <Code>{projectsDir}</Code>. Deleted projects go to its{" "}
            <Code>.trash</Code> folder and can be restored from there.
          </p>
        </>
      )}

      <PromptDialog
        open={creating}
        onClose={() => setCreating(false)}
        title="New project"
        description="A project is a folder that keeps its scans, results and measurements together."
        label="Project name"
        placeholder="e.g. Intake bracket"
        confirmLabel="Create project"
        onSubmit={async (name) => {
          const p = await create(name);
          if (!p) return false;
          setCurrent(p.id);
          toast.success(`Created “${p.name}”`, "Scans you start now go into this project.", {
            label: "Start a scan",
            run: () => navigate(paths.scan(p.id)),
          });
          return true;
        }}
      />
      <PromptDialog
        open={!!renaming}
        onClose={() => setRenaming(null)}
        title="Rename project"
        label="Project name"
        initial={renaming?.name ?? ""}
        confirmLabel="Rename"
        onSubmit={async (name) => (renaming ? rename(renaming.id, name) : false)}
      />
      <ConfirmDialog
        open={!!deleting}
        onClose={() => setDeleting(null)}
        title={`Move “${deleting?.name ?? ""}” to the trash?`}
        confirmLabel="Move to trash"
        tone="danger"
        onConfirm={async () => {
          if (!deleting) return;
          const name = deleting.name;
          if (await remove(deleting.id)) toast.success(`Moved “${name}” to the trash`, "Its folder is in .trash inside your projects folder.");
        }}
      >
        {deleting && deleting.n_scans > 0
          ? `The project folder and its ${deleting.n_scans} scan${deleting.n_scans === 1 ? "" : "s"} move to the `
          : "The project folder moves to the "}
        <Code>.trash</Code> folder inside your projects folder. Nothing is permanently deleted: you can restore it from there.
      </ConfirmDialog>
    </Page>
  );
}
