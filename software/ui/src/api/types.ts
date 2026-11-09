/**
 * Types of the OpenHandScan Studio app API (software/docs/APP_API.md, v1 / milestone 1).
 * JSON is snake_case; lengths are mm, times are seconds, timestamps are ISO 8601.
 */

export type Emitters = { vertical: number; horizontal: number; dots: number }; // percent 0..100
export type EmitterName = keyof Emitters;

export type Source = "none" | "camera" | "replay" | "sim";
export type ConnectSource = Exclude<Source, "none">;

export type CalibrationInfo = {
  loaded: boolean;
  path: string | null;
  image_size: [number, number] | null;
  baseline_mm: number | null;
  lasers: string[];
  imu: boolean;
};

export type DeviceState = {
  source: Source;
  connected: boolean;
  busy: string | null; // e.g. "Generating simulator demo…"
  camera: { connected: boolean; info: Record<string, unknown> } | null;
  pico: { connected: boolean; firmware: string | null; emitters: Emitters } | null;
  calibration: CalibrationInfo;
  replay: { path: string; loop: boolean } | null;
};

export type ScanMode = "markerless" | "markers";
export type ScanStatus = "recording" | "recorded" | "processing" | "processed" | "failed";

export type ScanResult = {
  mesh_url: string;
  stl_url: string;
  laser_points_url: string | null;
  report: Record<string, unknown>;
};

export type Scan = {
  id: string;
  name: string;
  created: string;
  mode: ScanMode;
  n_frames: number;
  duration_s: number;
  status: ScanStatus;
  result: ScanResult | null;
};

export type ProjectSummary = {
  id: string;
  name: string;
  created: string;
  modified: string;
  n_scans: number;
  thumbnail_url: string | null;
};

export type Project = ProjectSummary & { scans: Scan[] };

export type SessionPhase = "idle" | "scanning" | "paused" | "stopping";
export type SessionState = {
  state: SessionPhase;
  project_id: string | null;
  scan_id: string | null;
  mode: ScanMode;
  elapsed_s: number;
  frames: number;
};

export type JobState = "queued" | "running" | "done" | "failed" | "cancelled";
export type Job = {
  id: string;
  kind: string;
  state: JobState;
  progress: number; // 0..1
  message: string;
  result: Record<string, unknown> | null;
  error: string | null;
  created: string;
};

export type Health = { app: string; version: string; engine: { opencv?: string; open3d?: string } & Record<string, unknown> };

export type Settings = Record<string, unknown>;

// ------------------------------------------------------------------ request bodies
export type ConnectBody = { source: ConnectSource; recording?: string; loop?: boolean };
export type StartBody = { project_id: string; mode?: ScanMode; name?: string };
export type ProcessQuality = "preview" | "full";

// ------------------------------------------------------------------ WebSocket /api/ws/live
export type SubscribeMsg = { type: "subscribe"; preview?: boolean; mesh?: boolean; max_preview_hz?: number };

export type TrackingState = "idle" | "ok" | "weak" | "lost";
export type Tracking = {
  state: TrackingState;
  reason: string | null;
  fitness: number | null;
  rmse_mm: number | null;
};

export type StatusMsg = {
  type: "status";
  t: number;
  session: SessionState;
  tracking: Tracking;
  distance_mm: number | null;
  fps: { camera: number; pipeline: number };
  laser_points: number;
  model: { vertices: number; triangles: number };
  emitters: Emitters | null;
};

export type HelloMsg = { type: "hello"; version: string };
export type PoseMsg = { type: "pose"; T_wc: number[]; ok: boolean }; // 16 numbers, row-major
export type GhostMsg = { type: "ghost"; T_wc: number[] };
export type DeviceMsg = { type: "device" } & DeviceState;
export type SessionMsg = { type: "session" } & SessionState;
export type JobMsg = { type: "job" } & Job;
export type LogLevel = "info" | "warn" | "error";
export type LogMsg = { type: "log"; level: LogLevel; message: string };

export type ServerTextMsg = HelloMsg | StatusMsg | PoseMsg | GhostMsg | DeviceMsg | SessionMsg | JobMsg | LogMsg;

export type PreviewCamera = "left" | "depth";
export type PreviewHeader = { type: "preview"; camera: PreviewCamera; format: "jpeg"; w: number; h: number; seq: number };
export type MeshHeader = {
  type: "mesh";
  seq: number;
  vertices: number;
  triangles: number;
  units: "mm";
  frame: "world";
};
