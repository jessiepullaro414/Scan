import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { initBackend } from "./api/backend";
import { startLive } from "./api/live";
import "./store/ui"; // applies the saved theme
import "./index.css";

const root = createRoot(document.getElementById("root")!);

void initBackend().then(async () => {
  startLive();
  if (import.meta.env.DEV) {
    // Dev-only handle for debugging and smoke tests: window.__ohs.device.getState() etc.
    const [{ useDevice }, { useJobs }, { useSession }, { useLive }, { useProjects }, { useToasts }, { useSetup }, { useCalib }] = await Promise.all([
      import("./store/device"),
      import("./store/jobs"),
      import("./store/session"),
      import("./store/live"),
      import("./store/projects"),
      import("./store/toasts"),
      import("./store/setup"),
      import("./store/calib"),
    ]);
    (window as unknown as { __ohs: unknown }).__ohs = {
      device: useDevice,
      jobs: useJobs,
      session: useSession,
      live: useLive,
      projects: useProjects,
      toasts: useToasts,
      setup: useSetup,
      calib: useCalib,
    };
  }
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
