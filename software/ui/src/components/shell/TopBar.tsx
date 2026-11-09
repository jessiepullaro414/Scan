import { ChevronDown, FolderOpen, FolderPlus, LayoutGrid, Moon, Sun } from "lucide-react";
import { useMemo, useState } from "react";
import { navigate, paths, type Route } from "../../lib/router";
import { useProjects } from "../../store/projects";
import { useUi } from "../../store/ui";
import { IconButton } from "../ui/Button";
import { PromptDialog } from "../ui/Dialog";
import { Menu, type MenuItem } from "../ui/Popover";
import { DeviceChip } from "./DeviceChip";
import { JobsMenu } from "./JobsMenu";

const PAGE_TITLE: Partial<Record<Route["name"], string>> = {
  home: "Projects",
  scan: "Live scan",
  device: "Device",
  setup: "Setup",
  calibrate: "Setup",
  settings: "Settings",
  viewer: "Result viewer",
  project: "Project",
};

/** The project in context: the one on screen, else the scan target ("current project"). */
function useContextProjectId(route: Route): string | null {
  const current = useUi((s) => s.currentProjectId);
  if (route.name === "project" || route.name === "viewer") return route.pid;
  if (route.name === "scan" && route.project) return route.project;
  return current;
}

export function TopBar({ route }: { route: Route }) {
  const pid = useContextProjectId(route);
  const list = useProjects((s) => s.list);
  const detail = useProjects((s) => (pid ? s.details[pid] : undefined));
  const create = useProjects((s) => s.create);
  const setCurrent = useUi((s) => s.setCurrentProject);
  const theme = useUi((s) => s.theme);
  const setTheme = useUi((s) => s.setTheme);
  const [creating, setCreating] = useState(false);

  const name = detail?.name ?? list?.find((p) => p.id === pid)?.name ?? null;

  const items = useMemo<MenuItem[]>(() => {
    const recent = (list ?? []).slice(0, 7).map<MenuItem>((p) => ({
      label: p.name,
      icon: FolderOpen,
      hint: p.id === pid ? "current" : undefined,
      onSelect: () => {
        setCurrent(p.id);
        navigate(paths.project(p.id));
      },
    }));
    return [
      ...recent,
      ...(recent.length ? (["separator"] as MenuItem[]) : []),
      { label: "New project…", icon: FolderPlus, onSelect: () => setCreating(true) },
      { label: "All projects", icon: LayoutGrid, onSelect: () => navigate(paths.home()) },
    ];
  }, [list, pid, setCurrent]);

  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-panel px-4">
      <div className="flex min-w-0 items-center gap-2">
        <span className="hidden text-sm font-semibold text-fg sm:inline">{PAGE_TITLE[route.name] ?? "OpenHandScan"}</span>
        <span className="hidden text-subtle sm:inline" aria-hidden>
          /
        </span>
        <Menu
          label="Projects"
          align="start"
          width={260}
          items={items}
          renderTrigger={(p) => (
            <button
              ref={p.ref}
              type="button"
              onClick={p.onClick}
              aria-haspopup={p["aria-haspopup"]}
              aria-expanded={p["aria-expanded"]}
              aria-label={name ? `Project: ${name}. Switch project` : "Choose a project"}
              className="flex h-8 min-w-0 max-w-[360px] items-center gap-2 rounded-lg px-2 text-sm text-fg transition-colors hover:bg-surface-2"
            >
              <FolderOpen size={15} className="shrink-0 text-muted" aria-hidden />
              <span className={name ? "truncate font-medium" : "truncate text-subtle"}>{name ?? "No project selected"}</span>
              <ChevronDown size={14} className="shrink-0 text-subtle" aria-hidden />
            </button>
          )}
        />
      </div>
      <div className="flex-1" />
      <DeviceChip />
      <JobsMenu />
      <IconButton
        icon={theme === "dark" ? Sun : Moon}
        label={theme === "dark" ? "Switch to the light theme" : "Switch to the dark theme"}
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
      />
      <PromptDialog
        open={creating}
        onClose={() => setCreating(false)}
        title="New project"
        description="A project is a folder that keeps its scans, results and measurements together."
        label="Project name"
        placeholder="e.g. Intake bracket"
        confirmLabel="Create project"
        onSubmit={async (v) => {
          const p = await create(v);
          if (!p) return false;
          setCurrent(p.id);
          navigate(paths.project(p.id));
          return true;
        }}
      />
    </header>
  );
}
