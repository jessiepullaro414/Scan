/**
 * Typed REST client for the ohs backend (software/docs/APP_API.md).
 *
 * All requests go through one swappable `fetch`: the real one, or the in-browser mock
 * backend (`?mock=1`), which answers with real `Response` objects, so every code path is
 * the same in both modes. Errors become `ApiError` carrying the API's `detail` and `hint`.
 */
import type {
  ConnectBody,
  DeviceState,
  Emitters,
  Health,
  Job,
  ProcessQuality,
  Project,
  ProjectSummary,
  Scan,
  SessionState,
  Settings,
  StartBody,
} from "./types";

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

let fetchImpl: FetchLike = (input, init) => fetch(input, init);

/** Route every API request through `f` (used by the mock backend). */
export function setFetch(f: FetchLike): void {
  fetchImpl = f;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly hint: string | null;
  readonly path: string;

  constructor(status: number, detail: string, hint: string | null, path: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.hint = hint;
    this.path = path;
  }

  /** The engine could not be reached at all (not running, or the dev proxy has no target). */
  get unreachable(): boolean {
    return this.status === 0 || this.status === 502 || this.status === 503 || this.status === 504;
  }
}

const UNREACHABLE_HINT = "Start the engine with `ohs app` (it listens on 127.0.0.1:8765), or open the demo with ?mock=1.";

function detailText(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  // FastAPI validation errors: [{loc: [...], msg: "...", type: "..."}]
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        if (d && typeof d === "object" && "msg" in d) {
          const loc = Array.isArray((d as { loc?: unknown }).loc)
            ? ((d as { loc: unknown[] }).loc.filter((x) => x !== "body").join(".") as string)
            : "";
          return loc ? `${loc}: ${String((d as { msg: unknown }).msg)}` : String((d as { msg: unknown }).msg);
        }
        return null;
      })
      .filter(Boolean);
    return parts.length ? parts.join("; ") : null;
  }
  return null;
}

async function toApiError(res: Response, path: string): Promise<ApiError> {
  let detail: string | null = null;
  let hint: string | null = null;
  const text = await res.text().catch(() => "");
  if (text) {
    try {
      const body = JSON.parse(text) as { detail?: unknown; hint?: unknown };
      detail = detailText(body.detail);
      hint = typeof body.hint === "string" ? body.hint : null;
    } catch {
      /* not JSON: a proxy or server error page */
    }
  }
  if (!detail) {
    if (res.status >= 500 && !text) {
      // The Vite dev proxy answers 5xx with an empty body when the engine is not running.
      return new ApiError(502, "Can't reach the OpenHandScan engine", UNREACHABLE_HINT, path);
    }
    detail = `${res.status} ${res.statusText || "error"} (${path})`;
  }
  return new ApiError(res.status, detail, hint, path);
}

export type RawResponse<T> = { status: number; data: T };

async function send<T>(method: string, path: string, body?: unknown): Promise<RawResponse<T>> {
  let res: Response;
  try {
    res = await fetchImpl(path, {
      method,
      headers: body === undefined ? { Accept: "application/json" } : { "Content-Type": "application/json", Accept: "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Can't reach the OpenHandScan engine", UNREACHABLE_HINT, path);
  }
  if (!res.ok) throw await toApiError(res, path);
  if (res.status === 204) return { status: 204, data: undefined as T };
  const text = await res.text();
  if (!text) return { status: res.status, data: undefined as T };
  try {
    return { status: res.status, data: JSON.parse(text) as T };
  } catch {
    throw new ApiError(res.status, `The engine sent an unreadable reply to ${path}`, "Check the engine log.", path);
  }
}

const get = async <T>(path: string) => (await send<T>("GET", path)).data;
const post = async <T>(path: string, body?: unknown) => (await send<T>("POST", path, body ?? {})).data;
const patch = async <T>(path: string, body: unknown) => (await send<T>("PATCH", path, body)).data;
const del = async (path: string) => {
  await send<void>("DELETE", path);
};

const enc = encodeURIComponent;

/** Result of `POST /api/device/connect`: 200 with the device, or 202 with the job building the demo. */
export type ConnectResult = { kind: "connected"; device: DeviceState } | { kind: "job"; job: Job };

export type ProgressFn = (loaded: number, total: number | null) => void;

export const api = {
  health: () => get<Health>("/api/health"),

  device: () => get<DeviceState>("/api/device"),
  connect: async (body: ConnectBody): Promise<ConnectResult> => {
    const r = await send<DeviceState | { job: Job }>("POST", "/api/device/connect", body);
    if (r.status === 202 || (r.data && typeof r.data === "object" && "job" in r.data)) {
      return { kind: "job", job: (r.data as { job: Job }).job };
    }
    return { kind: "connected", device: r.data as DeviceState };
  },
  disconnect: () => post<DeviceState>("/api/device/disconnect"),
  setEmitters: (e: Partial<Emitters>) => post<DeviceState>("/api/device/emitters", e),

  projects: () => get<ProjectSummary[]>("/api/projects"),
  createProject: (name: string) => post<Project>("/api/projects", { name }),
  project: (pid: string) => get<Project>(`/api/projects/${enc(pid)}`),
  renameProject: (pid: string, name: string) => patch<Project>(`/api/projects/${enc(pid)}`, { name }),
  deleteProject: (pid: string) => del(`/api/projects/${enc(pid)}`),

  session: () => get<SessionState>("/api/scan"),
  startScan: (body: StartBody) => post<SessionState>("/api/scan/start", body),
  pauseScan: () => post<SessionState>("/api/scan/pause"),
  resumeScan: () => post<SessionState>("/api/scan/resume"),
  rewindScan: (seconds: number) => post<SessionState>("/api/scan/rewind", { seconds }),
  stopScan: () => post<{ scan: Scan }>("/api/scan/stop"),

  processScan: (pid: string, sid: string, quality: ProcessQuality) =>
    post<Job>(`/api/projects/${enc(pid)}/scans/${enc(sid)}/process`, { quality }),

  jobs: () => get<Job[]>("/api/jobs"),
  job: (jid: string) => get<Job>(`/api/jobs/${enc(jid)}`),
  cancelJob: (jid: string) => post<Job>(`/api/jobs/${enc(jid)}/cancel`),

  settings: () => get<Settings>("/api/settings"),

  /** URL of a result file (`mesh.ply`, `mesh.stl`, `laser_points.ply`, `report.json`). */
  fileUrl: (pid: string, sid: string, name: string) => `/api/projects/${enc(pid)}/scans/${enc(sid)}/files/${enc(name)}`,

  /** Download a file into memory, reporting progress when the size is known. */
  async fetchBinary(url: string, onProgress?: ProgressFn, signal?: AbortSignal): Promise<ArrayBuffer> {
    let res: Response;
    try {
      res = await fetchImpl(url, { signal });
    } catch (e) {
      if ((e as { name?: string }).name === "AbortError") throw e;
      throw new ApiError(0, "Can't reach the OpenHandScan engine", UNREACHABLE_HINT, url);
    }
    if (!res.ok) throw await toApiError(res, url);
    const total = Number(res.headers.get("content-length")) || null;
    if (!res.body || !onProgress) {
      const buf = await res.arrayBuffer();
      onProgress?.(buf.byteLength, buf.byteLength);
      return buf;
    }
    const reader = res.body.getReader();
    const chunks: Uint8Array[] = [];
    let loaded = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      loaded += value.byteLength;
      onProgress(loaded, total);
    }
    const out = new Uint8Array(loaded);
    let off = 0;
    for (const c of chunks) {
      out.set(c, off);
      off += c.byteLength;
    }
    return out.buffer;
  },

  async fetchJson<T = unknown>(url: string): Promise<T> {
    const r = await send<T>("GET", url);
    return r.data;
  },

  /** Raw access for downloads (mock mode builds a blob from it). */
  rawFetch: (url: string) => fetchImpl(url),
};

/** Normalise anything thrown into an ApiError-like shape for toasts. */
export function describeError(err: unknown): { detail: string; hint: string | null } {
  if (err instanceof ApiError) return { detail: err.detail, hint: err.hint };
  if (err instanceof Error) return { detail: err.message, hint: null };
  if (err && typeof err === "object" && typeof (err as { detail?: unknown }).detail === "string") {
    const h = (err as { hint?: unknown }).hint;
    return { detail: (err as { detail: string }).detail, hint: typeof h === "string" ? h : null };
  }
  return { detail: String(err), hint: null };
}
