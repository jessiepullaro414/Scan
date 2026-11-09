/**
 * A small hash router: `#/path?query`. Routes are matched by patterns with `:params`.
 */
import { useEffect, useSyncExternalStore, type AnchorHTMLAttributes, type MouseEvent } from "react";

/** The setup wizard's pages after the overview (`#/setup`): the seven steps, then "done". */
export const SETUP_PAGES = ["connect", "selftest", "print", "stereo", "lasers", "imu", "check", "done"] as const;
export type SetupPage = (typeof SETUP_PAGES)[number];
const isSetupPage = (s: string | undefined): s is SetupPage => !!s && (SETUP_PAGES as readonly string[]).includes(s);

export type Route =
  | { name: "home" }
  | { name: "project"; pid: string }
  | { name: "viewer"; pid: string; sid: string }
  | { name: "scan"; project: string | null; first: boolean }
  | { name: "device" }
  | { name: "setup"; step: SetupPage | null }
  /** The M1 address of the calibration page: redirects to the setup overview. */
  | { name: "calibrate" }
  | { name: "settings" }
  | { name: "notfound"; path: string };

export type RouteName = Route["name"];

function splitHash(hash: string): { path: string; query: URLSearchParams } {
  let h = hash.startsWith("#") ? hash.slice(1) : hash;
  if (!h.startsWith("/")) h = "/" + h;
  const q = h.indexOf("?");
  const path = q >= 0 ? h.slice(0, q) : h;
  const query = new URLSearchParams(q >= 0 ? h.slice(q + 1) : "");
  return { path: path.replace(/\/+$/, "") || "/", query };
}

const dec = (s: string) => {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
};

export function parseRoute(hash: string): Route {
  const { path, query } = splitHash(hash);
  const parts = path.split("/").filter(Boolean).map(dec);
  if (parts.length === 0) return { name: "home" };
  const [a, b, c, d] = parts;
  if (a === "projects" && parts.length === 1) return { name: "home" };
  if (a === "projects" && b && parts.length === 2) return { name: "project", pid: b };
  if (a === "projects" && b && c === "scans" && d && parts.length === 4) return { name: "viewer", pid: b, sid: d };
  if (a === "scan" && parts.length === 1) {
    const first = query.get("first");
    return { name: "scan", project: query.get("project"), first: first === "1" || first === "true" };
  }
  if (a === "device" && parts.length === 1) return { name: "device" };
  if (a === "setup" && parts.length === 1) return { name: "setup", step: null };
  if (a === "setup" && parts.length === 2 && isSetupPage(b)) return { name: "setup", step: b };
  if (a === "calibrate" && parts.length === 1) return { name: "calibrate" };
  if (a === "settings" && parts.length === 1) return { name: "settings" };
  return { name: "notfound", path };
}

const enc = encodeURIComponent;

export const paths = {
  home: () => "/",
  project: (pid: string) => `/projects/${enc(pid)}`,
  viewer: (pid: string, sid: string) => `/projects/${enc(pid)}/scans/${enc(sid)}`,
  scan: (project?: string | null, opts: { first?: boolean } = {}) => {
    const q = new URLSearchParams();
    if (project) q.set("project", project);
    if (opts.first) q.set("first", "1");
    const s = q.toString();
    return s ? `/scan?${s}` : "/scan";
  },
  device: () => "/device",
  setup: (step?: SetupPage | null) => (step ? `/setup/${step}` : "/setup"),
  settings: () => "/settings",
};

/** Replace the current address (no history entry), e.g. an old route that moved. */
export function Redirect({ to }: { to: string }) {
  useEffect(() => {
    navigate(to, { replace: true });
  }, [to]);
  return null;
}

function subscribe(cb: () => void): () => void {
  window.addEventListener("hashchange", cb);
  return () => window.removeEventListener("hashchange", cb);
}

const getHash = () => location.hash;

/** The current route; re-renders on navigation. */
export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, getHash, getHash);
  return parseRoute(hash);
}

export function navigate(to: string, opts: { replace?: boolean } = {}): void {
  const target = "#" + to;
  if (opts.replace) {
    const url = new URL(location.href);
    url.hash = target;
    history.replaceState(null, "", url);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  } else if (location.hash !== target) {
    location.hash = target;
  }
}

export function href(to: string): string {
  return "#" + to;
}

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & { to: string };

/** An anchor for in-app navigation (keeps middle-click / open-in-new-tab working). */
export function Link({ to, onClick, ...rest }: LinkProps) {
  return (
    <a
      href={href(to)}
      onClick={(e: MouseEvent<HTMLAnchorElement>) => {
        onClick?.(e);
      }}
      {...rest}
    />
  );
}
