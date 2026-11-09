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
  log(level: "info" | "warn" | "error", message: string): void;
  settings: MockSettings;
}

export function json(status: number, body: unknown): Response {
  if (status === 204) return new Response(null, { status });
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

export const nowIso = (offsetS = 0) => new Date(Date.now() + offsetS * 1000).toISOString();
export const uid = (p: string) => `${p}${Date.now().toString(36).slice(-4)}${Math.random().toString(36).slice(2, 7)}`;
