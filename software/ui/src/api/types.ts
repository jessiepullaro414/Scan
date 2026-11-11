/**
 * Types of the OpenHandScan Studio app API (software/docs/APP_API.md: milestones 1 and 2, and
 * M3 phase 2, the processing studio). JSON is snake_case; lengths are mm, times are seconds,
 * timestamps are ISO 8601.
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

export type PicoState = {
  connected: boolean;
  firmware: string | null; // e.g. "ohs-fw 1.0.0"
  emitters: Emitters;
  // extras (APP_API.md, "API deviations"): optional
  imu?: string | null; // the IMU the firmware reports, e.g. "LSM6DSOX" or "NONE"
  port?: string | null;
  error?: string | null;
};

export type DeviceState = {
  source: Source;
  connected: boolean;
  busy: string | null; // e.g. "Generating simulator demo…"
  camera: { connected: boolean; info: Record<string, unknown> } | null;
  pico: PicoState | null;
  calibration: CalibrationInfo;
  replay: { path: string; loop: boolean } | null;
  /** M2: the silent drift check run after each connect (absent from an M1 engine). */
  launch_check?: LaunchCheck | null;
  /** M2: what the simulator shows; null unless the source is "sim". */
  sim_scene?: SimScene | null;
  /** M2 extra: the simulator demo recording exists (connecting the simulator is instant). */
  sim_demo_ready?: boolean;
  // extras (APP_API.md, "API deviations"): optional
  error?: string | null;
  warnings?: string[];
  imu_stream?: boolean;
  /** The IMU chip's temperature in °C: live from firmware 1.1+ (about once a second while the IMU
   * streams), or as recorded for a replay. Null when unknown. */
  imu_temp_c?: number | null;
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
  /** Extra (F4): the pass over the recording's frames running now; absent between passes. */
  frames?: JobFrames | null;
};

/** A job's frame pass: which stage, frames done of total, and its rate since the first frame (null until known). */
export type JobFrames = { stage: string; done: number; total: number; fps: number | null; elapsed_s: number };

export type Health = { app: string; version: string; engine: { opencv?: string; open3d?: string } & Record<string, unknown> };

export type Settings = Record<string, unknown>;

// ------------------------------------------------------------------ request bodies
export type ConnectBody = { source: ConnectSource; recording?: string; loop?: boolean };
export type StartBody = { project_id: string; mode?: ScanMode; name?: string };
/** The level of the final mesh: "standard" (default, 30 frames a second of scanning), "best" (every frame), "preview".
 * Older engines send "full", the old name of "standard": read it with `normalizeQuality` (lib/quality.ts). */
export type ProcessQuality = "standard" | "best" | "preview";

// ------------------------------------------------------------------ WebSocket /api/ws/live
export type SubscribeMsg = { type: "subscribe"; preview?: boolean; mesh?: boolean; max_preview_hz?: number };

export type TrackingState = "idle" | "ok" | "weak" | "lost";
export type Tracking = {
  state: TrackingState;
  reason: string | null;
  fitness: number | null;
  rmse_mm: number | null;
  /** M3 extra: what the surface can't pin down in this frame ("slide", "turn"); see `code` "degenerate". */
  weak?: string[];
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

export type CalibLiveMsg = CalibLive; // type: "calib"
export type CalibSessionMsg = { type: "calib_session" } & CalibSession;
export type SetupMsg = { type: "setup" } & SetupState;
/** M3: a scan's processing state changed (keyed by its project_id / scan_id). */
export type ProcessMsg = { type: "process" } & ProcessState;

export type ServerTextMsg =
  | HelloMsg
  | StatusMsg
  | PoseMsg
  | GhostMsg
  | DeviceMsg
  | SessionMsg
  | JobMsg
  | LogMsg
  | CalibLiveMsg
  | CalibSessionMsg
  | SetupMsg
  | ProcessMsg;

/** `calib_left` / `calib_right`: the calibration views (M2), about 640 px wide. */
export type PreviewCamera = "left" | "depth" | "calib_left" | "calib_right";
export type PreviewHeader = { type: "preview"; camera: PreviewCamera; format: "jpeg"; w: number; h: number; seq: number };
export type MeshHeader = {
  type: "mesh";
  seq: number;
  vertices: number;
  triangles: number;
  units: "mm";
  frame: "world";
};

// ================================================================== M2: setup and calibration
// APP_API.md "M2: setup and calibration". Everything here adds to M1; nothing in M1 changes.

export type CalibKind = "stereo" | "lasers" | "imu" | "check";
/** "sim" while the simulator is connected (a sandbox file), "device" otherwise. */
export type CalibTarget = "device" | "sim";
export type SimScene = "demo" | "calib_stereo" | "calib_lasers" | "calib_imu" | "calib_check";

export type LaunchCheckState = "running" | "ok" | "drift" | "unknown";
export type LaunchCheck = {
  state: LaunchCheckState;
  dv_px: number | null; // median rectified row error, full-resolution px
  matches: number;
  message: string | null;
  when: string;
};

// ------------------------------------------------------------------ setup
export type StepId = "connect" | "selftest" | "print" | "stereo" | "lasers" | "imu" | "check";
export type StepState = "done" | "todo" | "warn" | "optional";
export type SetupStep = { id: StepId; state: StepState; summary: string; when: string | null };
/** Recorded by Studio when it saved that part of the calibration. */
export type PartInfo = { when: string; summary: string; session: string | null };
export type Paper = "letter" | "a4";

export type SetupCalibration = {
  path: string;
  exists: boolean;
  modified: string | null;
  image_size: [number, number] | null;
  baseline_mm: number | null;
  stereo: boolean;
  lasers: string[];
  imu: boolean;
  parts: { stereo: PartInfo | null; lasers: PartInfo | null; imu: PartInfo | null };
  backup: string | null; // the previous version (calibration.json.bak), if any
};

export type BoardInfo = {
  squares_x: number;
  squares_y: number;
  square_mm: number;
  marker_mm: number; // as printed (config [board])
  dictionary: string;
  paper: Paper;
  scale: number; // measured print scale used for calibration (1 = exact)
  describe: string;
};

export type SetupState = {
  target: CalibTarget;
  first_run: boolean; // the target has no stereo calibration and the wizard was not dismissed
  dismissed: boolean;
  steps: SetupStep[]; // always all seven, in StepId order
  calibration: SetupCalibration;
  selftest: SelfTestResult | null; // the last self-test for this target
  print_check: PrintCheck | null; // null for the sim target (its board is virtual and exact)
  board: BoardInfo;
  launch_check: LaunchCheck | null;
  last_check: { when: string; ok: boolean; verdict: string } | null;
};

export type CheckStatus = "PASS" | "WARN" | "FAIL" | "SKIP" | "INFO";
export type SelfTestCheck = { name: string; status: CheckStatus; detail: string; fix: string | null }; // fix: plain words
export type SelfTestResult = {
  overall: "PASS" | "WARN" | "FAIL";
  when: string;
  source: "camera" | "replay" | "sim";
  checks: SelfTestCheck[];
};

export type CameraProbe = {
  index: number;
  width: number;
  height: number;
  fps: number;
  fourcc: string;
  name: string | null;
  stereo_like: boolean;
  configured: boolean;
  in_use: boolean;
};
export type DevicesProbe = { cameras: CameraProbe[]; pico_port: string | null; configured_index: number; note: string | null };

export type PrintFileKind = "board" | "tags" | "mask";
export type PrintFile = {
  name: string;
  kind: PrintFileKind;
  title: string;
  description: string;
  url: string;
  preview_url: string | null;
  bytes: number;
};
export type Printables = { dir: string; generated: string | null; paper: Paper; files: PrintFile[] };
export type PrintCheck = {
  measured_mm: number;
  error_pct: number;
  ok: boolean;
  scale: number;
  verdict: string;
  can_apply: boolean;
  when: string;
};

// ------------------------------------------------------------------ calibration session
/** capturing: stereo / lasers / check take views. armed -> recording: the IMU step (hold still, then rotate). */
export type CalibState = "idle" | "capturing" | "armed" | "recording" | "solving" | "result" | "saved";

export type CalibView = {
  index: number;
  t: number;
  auto: boolean;
  corners_left: number;
  corners_right: number | null;
  distance_mm: number | null;
  tilt_deg: number | null;
  lines: { vertical: number; horizontal: number } | null; // laser points on the page margin
  thumb_url: string;
  error_px: number | null; // after a solve
  bad: boolean; // after a solve
  warnings: string[];
};

export type CalibMetric = { label: string; value: string; ok: boolean | null; help: string };
export type CalibAdviceAction =
  | "add_views"
  | "remove_bad_views"
  | "start_over"
  | "recalibrate_stereo"
  | "reprint"
  | "check_board";
export type CalibAdvice = { text: string; action: CalibAdviceAction | null };
export type CalibGrade = "excellent" | "good" | "usable" | "poor" | "failed";

export type CalibResult = {
  kind: CalibKind;
  ok: boolean;
  grade: CalibGrade;
  headline: string; // e.g. "Excellent stereo calibration"
  verdict: string; // one or two plain sentences: what happened and what it means
  metrics: CalibMetric[]; // plain-language rows
  advice: CalibAdvice[];
  per_view: { index: number; error_px: number; bad: boolean }[];
  sim_truth: CalibMetric[] | null; // simulator only: the estimate against the true calibration
  can_save: boolean; // false for "check" and for "failed"
  report_url: string;
};

export type CalibSession = {
  state: CalibState;
  kind: CalibKind | null;
  target: CalibTarget;
  id: string | null;
  started: string | null;
  auto: boolean; // auto-capture on (default)
  views: CalibView[];
  target_views: [number, number];
  min_views: number;
  guidance: string; // the one-line how-to for this kind
  emitters: string; // e.g. "Line lasers on: Class 3R, never look into the beams"
  job_id: string | null; // the solve job
  result: CalibResult | null;
  recording: string | null; // folder of the captured views
  saved: { path: string; backup: string | null; notes: string[] } | null;
};

export type CalibHintTilt = "left" | "right" | "up" | "down" | "more";
export type CalibHint = {
  text: string; // "Move the board to the top-left corner"
  region: [number, number, number, number] | null; // x0, y0, x1, y1 as fractions of the image
  tilt: CalibHintTilt | null;
  distance: "closer" | "farther" | null;
};

export type CalibCoverage = {
  cols: number;
  rows: number;
  left: number[]; // views per cell, row-major
  right: number[] | null;
  left_pct: number;
  right_pct: number | null;
};

export type CalibDiversity = {
  flat: number;
  tilt_left: number;
  tilt_right: number;
  tilt_up: number;
  tilt_down: number;
  near: number;
  mid: number;
  far: number;
}; // captured views per pose bin

export type ImuPhase = "armed" | "still" | "rotate" | "done";
export type CalibImuLive = {
  phase: ImuPhase;
  elapsed_s: number;
  still_s: number;
  min_s: number;
  max_s: number;
  gyro_dps: number;
  board_pct: number;
};

export type CalibLines = { vertical: number; horizontal: number; vertical_uv: number[]; horizontal_uv: number[] };

/** The `calib` WebSocket message, about 10 Hz while a session is active. */
export type CalibLive = {
  type: "calib";
  t: number;
  kind: CalibKind;
  state: CalibState;
  image_size: [number, number]; // full-resolution size of one camera image; every coordinate below is in it
  fps: number; // processed frames per second
  left: { corners: number[] } | null; // flattened [u0, v0, u1, v1, ...]
  right: { corners: number[] } | null; // stereo only
  common: number | null; // stereo: corners seen by both cameras
  board_found: boolean;
  distance_mm: number | null;
  tilt_deg: number | null;
  outline: number[] | null; // board outline, 4 points
  page: number[] | null; // lasers / check: page outline, 4 points
  lines: CalibLines | null;
  ready: boolean; // a capture now would be accepted
  reason: string | null; // why not
  steady: number; // 0..1: progress of the auto-capture hold
  auto: boolean;
  hint: CalibHint | null;
  coverage: CalibCoverage | null;
  diversity: CalibDiversity | null;
  views: number;
  target_views: [number, number];
  imu: CalibImuLive | null;
};

// ------------------------------------------------------------------ M2 request / reply bodies
export type CameraSettingsBody = { index?: number; swap_lr?: boolean };
export type OpenFolderReply = { opened: boolean; dir: string };
export type CaptureReply = { view: CalibView; session: CalibSession };
export type SaveReply = { session: CalibSession; setup: SetupState };

// ================================================================== M3 phase 2: processing studio
// APP_API.md "M3 phase 2: processing studio". Adds to M1 and M2; changes nothing in them.

/** The stages of a scan's processing; each ends in a new base mesh. */
export type StageId = "track" | "straighten" | "fuse" | "spacetime" | "refine";
export type StageState = "none" | "running" | "done" | "failed" | "skipped";

export type MeshOp = "remove_floaters" | "fill_holes" | "smooth" | "decimate" | "delete_faces" | "keep_faces";
/** The edits a recipe can hold (a selection only fits the scan it was made on). */
export type RecipeMeshOp = Exclude<MeshOp, "delete_faces" | "keep_faces">;

export type RemoveFloatersParams = { min_triangles?: number; min_size_mm?: number; keep_largest_only?: boolean };
/** `max_hole_mm`: the largest hole perimeter to fill; `watertight`: the outer edge too. */
export type FillHolesParams = { max_hole_mm: number; watertight?: boolean };
export type SmoothMethod = "taubin" | "bilateral";
/** `strength` 0..1; `within_sigma`: no vertex moves more than twice its sigma. */
export type SmoothParams = { method: SmoothMethod; strength: number; keep_edges?: boolean; within_sigma?: boolean };
/** A target (one of the two) and / or a tolerance in mm. */
export type DecimateParams = { target_triangles?: number; ratio?: number; max_error_mm?: number };
/** base64 of little-endian uint32 face indices of the CURRENT mesh. */
export type FacesParams = { faces: string };

export type MeshStats = {
  triangles: number;
  vertices: number;
  components: number;
  holes: number; // boundary loops except the outer border (the longest loop)
  largest_hole_mm: number | null; // perimeter of the largest hole
  border_mm: number | null; // perimeter of the outer border
  boundary_edges: number;
  non_manifold_edges: number;
  watertight: boolean;
  bbox_mm: [number, number, number];
  area_cm2: number;
  volume_cm3: number | null; // only when watertight
  /** The confidence, when the mesh has per-vertex sigmas (the PLY `quality`); shares of the surface. */
  sigma_mm?: SigmaStats | null;
  /** Area made by hole filling, not measured (faces with the PLY face property `filled`). */
  filled_cm2?: number;
};

export type SigmaStats = {
  median: number; // mm
  p95: number; // mm
  within_0_1: number; // share of the surface with sigma <= 0.1 mm
  within_0_3: number;
  not_measured: number; // filled holes (sigma 50 mm)
};

export type EditEntry = {
  id: string;
  op: MeshOp;
  params: Record<string, unknown>;
  label: string; // "Fill holes up to 12 mm"
  created: string;
  triangles_after: number;
  ms: number;
};

export type StageInfo = { state: StageState; summary: string | null; when: string | null; job_id: string | null };
export type TrackStage = StageInfo & { quality: ProcessQuality | null; tracked_pct: number | null };
export type StraightenStage = StageInfo & {
  loops: number | null;
  correction_mm: number | null;
  correction_deg: number | null;
  /** Laser crossings the poses were lined up on (null: not adjusted). */
  crossings?: number | null;
};
export type FuseStage = StageInfo & { voxel_mm: number | null };
/** "Refine from the images" (a trial build): the base stage's mesh measured against the recording's frames. */
export type SpacetimeStage = StageInfo & {
  source: StageId | null; // the stage whose mesh was refined
  frames: number | null; // frames read
  patches: number | null; // patches on the surface
  kept: number | null; // patches with dots in enough frames, and a clear match
  covered_pct: number | null; // share of the surface refined, %
  moved_mm: number | null; // how far it moved (95th percentile), mm
  device: "gpu" | "cpu" | null;
};
/** "Refine with lasers": the base stage's mesh pulled onto its laser points. */
export type RefineStage = StageInfo & {
  source: StageId | null; // the stage whose mesh was refined
  points: number | null; // laser points used
  outliers: number | null; // laser points that disagreed with the surface (glints, reflections)
  moved_pct: number | null; // share of the surface moved, %
  moved_mm: number | null; // how far it moved (95th percentile), mm
  residual_before_mm: number | null; // median distance of the laser points from the surface, before / after
  residual_after_mm: number | null;
};

export type ProcessState = {
  project_id: string;
  scan_id: string;
  stages: { track: TrackStage; straighten: StraightenStage; fuse: FuseStage; spacetime: SpacetimeStage; refine: RefineStage };
  base: { triangles: number; vertices: number; stage: StageId } | null;
  edits: EditEntry[];
  cursor: number; // edits[0:cursor] are applied
  can_undo: boolean;
  can_redo: boolean;
  current: {
    version: number; // increases on every change: reload the mesh when it does
    mesh_url: string; // result/mesh.ply of this scan
    stats: MeshStats;
  } | null;
  busy: string | null; // e.g. "Filling holes…" while an edit or stage job runs
  job: Job | null; // the running job of this scan, if any
};

export type RecipeSettings = { quality: ProcessQuality; straighten: "auto" | "off"; refine?: "auto" | "off"; voxel_mm: number | null };
export type RecipeOp = { op: RecipeMeshOp; params: Record<string, unknown> };
export type Recipe = {
  id: string;
  name: string;
  description: string;
  builtin: boolean;
  settings: RecipeSettings;
  ops: RecipeOp[];
};
/** `edits`: replace the edits with the recipe's ops. `all`: run the stages with its settings, then its ops. */
export type RecipeScope = "edits" | "all";

/** A hole of the current mesh: `points` is the closed loop, flattened xyz (mm, world frame). */
export type HoleOutline = { perimeter_mm: number; n_edges: number; points: number[] };
/** Camera positions (mm, world frame), subsampled; `loops` are index pairs into `raw`. */
export type TrajectoryView = { raw: number[][]; straightened: number[][] | null; loops: [number, number][] };

// ------------------------------------------------------------------ M3 request bodies
export type EditBody = { op: MeshOp; params: Record<string, unknown> };
export type NewRecipeBody =
  | { name: string; description?: string; from_scan: { project_id: string; scan_id: string } }
  | { name: string; description?: string; settings: RecipeSettings; ops: RecipeOp[] };
