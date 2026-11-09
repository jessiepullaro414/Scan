/**
 * A small hash router: `#/path?query`. Routes are matched by patterns with `:params`.
 */
import { useSyncExternalStore, type AnchorHTMLAttributes, type MouseEvent } from "react";

export type Route =
  | { name: "home" }
  | { name: "project"; pid: string }
  | { name: "viewer"; pid: string; sid: string }
  | { name: "scan"; project: string | null }
  | { name: "device" }
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
  if (a === "scan" && parts.length === 1) return { name: "scan", project: query.get("project") };
  if (a === "device" && parts.length === 1) return { name: "device" };
  if (a === "calibrate" && parts.length === 1) return { name: "calibrate" };
  if (a === "settings" && parts.length === 1) return { name: "settings" };
  return { name: "notfound", path };
}

const enc = encodeURIComponent;

export const paths = {
  home: () => "/",
  project: (pid: string) => `/projects/${enc(pid)}`,
  viewer: (pid: string, sid: string) => `/projects/${enc(pid)}/scans/${enc(sid)}`,
  scan: (project?: string | null) => (project ? `/scan?project=${enc(project)}` : "/scan"),
  device: () => "/device",
  calibrate: () => "/calibrate",
  settings: () => "/settings",
};

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
