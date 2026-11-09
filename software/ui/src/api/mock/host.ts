/**
 * What the mock engine (server.ts) offers its M2 parts (setup.ts, calib.ts): broadcasting,
 * the device, timed jobs and the settings. Errors thrown as `HttpError` become the API's
 * `{detail, hint}` replies.
 */
import type { DeviceState, Job } from "../types";

export class HttpError extends Error {
  status: number;
  detail: string;
  hint: string | null;
  constructor(status: number, detail: string, hint: string | null = null) {
    super(detail);
    this.status = status;
    this.detail = detail;
    this.hint = hint;
  }
}

export type TimedJobSpec = {
  /** Duration at normal speed (shorter with ?fast=1). */
  seconds: number;
  /** The job message for progress p (0..1). */
  message: (p: number) => string;
  /** Called when the progress reaches 1; returns the job's result. Throw an Error to fail the job. */
  done: () => Record<string, unknown> | null;
};

/** A step of real work: progress 0..1 and what it's doing. */
export type WorkStep = { p: number; message: string };

/**
 * A job that does real work (M3: meshes) in small steps across the mock's ticks. Progress is the
 * lesser of the work done and the time since the start over `minSeconds` (so short work still
 * shows like the engine's). The generator returns a `commit` that applies the side effects when
 * the job ends; it runs before the final `job` message, like the engine. A thrown Error fails
 * the job with its message (the plain reason).
 */
export type WorkJobSpec = {
  minSeconds: number;
  work: Generator<WorkStep, () => Record<string, unknown> | null, void>;
  onFail?: (error: string) => void;
  onCancel?: () => void;
  /** Extra top-level fields of the Job (e.g. project_id, scan_id). */
  extra?: Record<string, unknown>;
};

export type MockSettings = {
  camera: { index: number; swap_lr: boolean } & Record<string, unknown>;
  board: { paper: string } & Record<string, unknown>;
  [k: string]: unknown;
};

export interface MockHost {
  readonly fast: boolean;
  /** `?drift=1`: the launch check after a connect finds drift. */
  readonly drift: boolean;
  send(msg: Record<string, unknown>): void;
  /** A binary preview frame, to the clients that subscribed to previews. */
  sendPreview(camera: string, jpeg: Uint8Array, w: number, h: number): void;
  /** Does any client want previews (`subscribe.preview`)? */
  wantsPreviews(): boolean;
  device(): DeviceState;
  /** Merge into the device state and broadcast it. */
  patchDevice(p: Partial<DeviceState>): void;
  scanning(): boolean;
  startJob(kind: string, spec: TimedJobSpec): Job;
  /** M3: a job doing real work in steps (see WorkJobSpec). */
  startWorkJob(kind: string, spec: WorkJobSpec): Job;
  job(jid: string): Job | undefined;
  log(level: "info" | "warn" | "error", message: string): void;
  settings: MockSettings;
}

export function json(status: number, body: unknown): Response {
  if (status === 204) return new Response(null, { status });
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

export const nowIso = (offsetS = 0) => new Date(Date.now() + offsetS * 1000).toISOString();
export const uid = (p: string) => `${p}${Date.now().toString(36).slice(-4)}${Math.random().toString(36).slice(2, 7)}`;
