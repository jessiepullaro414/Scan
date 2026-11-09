import { lazy, Suspense, useEffect } from "react";
import { ConnectionBanner, EngineUnreachable } from "./components/shell/ConnectionBanner";
import { ErrorBoundary } from "./components/shell/ErrorBoundary";
import { Sidebar } from "./components/shell/Sidebar";
import { Toaster } from "./components/shell/Toaster";
import { TopBar } from "./components/shell/TopBar";
import { Spinner } from "./components/ui/Feedback";
import { reconnectLive } from "./api/live";
import { paths, Redirect, useRoute, type Route } from "./lib/router";
import { DevicePage } from "./pages/DevicePage";
import { HomePage } from "./pages/HomePage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { ProjectPage } from "./pages/ProjectPage";
import { SettingsPage } from "./pages/SettingsPage";
import { useCalib } from "./store/calib";
import { useConnection } from "./store/connection";
import { useDevice } from "./store/device";
import { isActive, useJobs } from "./store/jobs";
import { useProjects } from "./store/projects";
import { useSession } from "./store/session";
import { useSetup } from "./store/setup";

// The 3D pages carry three.js; they load on first use. So does the setup wizard.
const ScanPage = lazy(() => import("./pages/ScanPage"));
const ViewerPage = lazy(() => import("./pages/ViewerPage"));
const ProcessPage = lazy(() => import("./pages/ProcessPage"));
const SetupPage = lazy(() => import("./pages/setup/SetupPage"));

function LoadingPage() {
  return (
    <div className="grid h-full place-items-center">
      <div className="flex items-center gap-2.5 text-sm text-muted">
        <Spinner size={16} /> Loading…
      </div>
    </div>
  );
}

function Loading3D() {
  return (
    <div className="grid h-full place-items-center viewport-bg">
      <div className="flex items-center gap-2.5 text-sm text-muted">
        <Spinner size={16} /> Loading the 3D view…
      </div>
    </div>
  );
}

function PageFor({ route }: { route: Route }) {
  switch (route.name) {
    case "home":
      return <HomePage />;
    case "project":
      return <ProjectPage pid={route.pid} />;
    case "viewer":
      return (
        <Suspense fallback={<Loading3D />}>
          <ViewerPage pid={route.pid} sid={route.sid} />
        </Suspense>
      );
    case "process":
      return (
        <Suspense fallback={<Loading3D />}>
          <ProcessPage key={`${route.pid}/${route.sid}`} pid={route.pid} sid={route.sid} />
        </Suspense>
      );
    case "scan":
      return (
        <Suspense fallback={<Loading3D />}>
          <ScanPage projectParam={route.project} first={route.first} />
        </Suspense>
      );
    case "device":
      return <DevicePage />;
    case "setup":
      return (
        <Suspense fallback={<LoadingPage />}>
          <SetupPage step={route.step} />
        </Suspense>
      );
    case "calibrate":
      return <Redirect to={paths.setup()} />;
    case "settings":
      return <SettingsPage />;
    default:
      return <NotFoundPage path={route.name === "notfound" ? route.path : ""} />;
  }
}

function loadAll() {
  void useDevice.getState().fetch();
  void useSession.getState().fetch();
  void useProjects.getState().fetchList();
  void useJobs.getState().fetchAll();
  void useSetup.getState().fetch();
  void useCalib.getState().fetch();
}

export function App() {
  const route = useRoute();
  const health = useConnection((s) => s.health);
  const healthError = useConnection((s) => s.healthError);
  const checking = useConnection((s) => s.checking);
  const everOpen = useConnection((s) => s.everOpen);
  const link = useConnection((s) => s.link);

  // Boot: health first, then everything else; keep retrying while the engine is down.
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const attempt = async () => {
      const ok = await useConnection.getState().checkHealth();
      if (stop) return;
      if (ok) loadAll();
      else timer = setTimeout(attempt, 3000);
    };
    void attempt();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  // Without the live socket, poll running jobs so progress never freezes.
  useEffect(() => {
    if (link === "open") return;
    const id = setInterval(() => {
      const jobs = Object.values(useJobs.getState().jobs);
      if (jobs.some(isActive) || useDevice.getState().simJobId) void useJobs.getState().fetchAll();
    }, 1500);
    return () => clearInterval(id);
  }, [link]);

  const unreachable = !health && !everOpen && !!healthError?.unreachable;
  // A crash on one wizard step clears when you go to another step.
  const routeKey = route.name + ("pid" in route ? route.pid : "") + ("sid" in route ? `/${route.sid}` : "") + (route.name === "setup" ? (route.step ?? "") : "");

  return (
    <div className="flex h-full bg-bg text-fg">
      <Sidebar route={route} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar route={route} />
        <ConnectionBanner />
        <main className="relative min-h-0 flex-1" id="main">
          {unreachable ? (
            <EngineUnreachable
              checking={checking}
              onRetry={async () => {
                reconnectLive();
                if (await useConnection.getState().checkHealth()) loadAll();
              }}
            />
          ) : (
            <ErrorBoundary resetKey={routeKey}>
              <PageFor route={route} />
            </ErrorBoundary>
          )}
        </main>
      </div>
      <Toaster />
    </div>
  );
}
