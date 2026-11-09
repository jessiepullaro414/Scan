/**
 * Chooses the backend: the real ohs engine (same origin, `/api`), or the in-browser mock
 * (`?mock=1` in the URL or `VITE_MOCK=1` at build/dev time). The mock module is loaded
 * lazily, so it costs nothing when the real engine is used.
 */
import { setFetch } from "./http";
import { setSocketFactory } from "./socket";

function queryFlag(name: string): string | null {
  const search = new URLSearchParams(location.search);
  if (search.has(name)) return search.get(name);
  const q = location.hash.indexOf("?");
  if (q >= 0) {
    const hp = new URLSearchParams(location.hash.slice(q + 1));
    if (hp.has(name)) return hp.get(name);
  }
  return null;
}

function detectMock(): boolean {
  const flag = queryFlag("mock");
  if (flag !== null) return flag !== "0" && flag !== "false";
  return import.meta.env.VITE_MOCK === "1" || import.meta.env.VITE_MOCK === "true";
}

export const MOCK = detectMock();

export type MockOptions = { empty: boolean; fast: boolean };

let ready: Promise<void> | null = null;

/** Install the mock transport when in mock mode. Call once before any API use. */
export function initBackend(): Promise<void> {
  if (ready) return ready;
  ready = (async () => {
    if (!MOCK) return;
    const { createMockBackend } = await import("./mock/server");
    const backend = createMockBackend({ empty: queryFlag("empty") === "1", fast: queryFlag("fast") === "1" });
    setFetch(backend.fetch);
    setSocketFactory(backend.socket);
  })();
  return ready;
}

/** URL that opens the same page in demo (mock) mode. */
export function mockUrl(): string {
  const u = new URL(location.href);
  u.searchParams.set("mock", "1");
  return u.toString();
}

/** URL of the same page with the mock switched off. */
export function realUrl(): string {
  const u = new URL(location.href);
  u.searchParams.delete("mock");
  return u.toString();
}
