/**
 * Types of the OpenHandScan Studio app API (software/docs/APP_API.md: milestones 1 and 2, M3
 * phase 2, the processing studio, and the M5 fitted shapes). JSON is snake_case; lengths are mm,
 * times are seconds, timestamps are ISO 8601.
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
  /** U21: which calibration it is (absent from an older engine): the simulator demo's own true one, the simulator's
   * own made in Setup, a replayed recording's own, or the scanner's file. */
  source?: "demo" | "simulator" | "recording" | "scanner";
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
  busy: string | null; // e.g. "Preparing the simulator…"
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
  /** M4: present on a merged scan: the scans it was made from. A merged scan has no recording of its own. */
  merged_from?: { id: string; name: string }[];
  /** R2: what the user chose to keep on disk ("full" until space was freed); says what was removed. */
  keep?: KeepMode;
  /** R2: each time the scan's space was freed. */
  freed?: { when: string; keep: KeepMode; bytes: number; thin?: boolean }[];
  /** R2: whether the recording's frames are on disk now (false for a merged scan or one that keeps its result only). */
  has_recording?: boolean;
  /** R3: present once the recording holds only the frames Standard quality uses (`fps` of them a second). */
  thinned?: ThinnedRecording & { when: string };
  /** P2: the scan's preset and the values changed by hand for it; absent on a scan made before presets (General). */
  preset?: ScanPresetKept;
  /** N2: present on a second pass: the scan it belongs to, what was asked for, and its steps after Stop. */
  continues?: PassLink;
};

/** R3: what a recording thinned to the frames Standard quality uses holds, and held before. */
export type ThinnedRecording = { fps: number; fps_before: number; step: number; frames: number; frames_before: number };

/** R2: what a scan keeps on disk, the lightest last. */
export type KeepMode = "full" | "engineering" | "standard";
/** R2: what freeing a scan's space would remove (sizes as R1 counts them). */
export type KeepPart = { part: "recording" | "frames" | "undo" | "stages"; bytes: number; files: number };
/** R2: what can no longer be done once the space is freed. */
export type KeepLoss = "undo" | "stages" | "remake" | "merge" | "refine" | "frames" | "best";
/** R2: what stays on disk. */
export type KeepKept = "recording" | "result" | "laser_points" | "trajectory" | "report" | "calibration" | "features";

/** R2: `GET /api/projects/{pid}/scans/{sid}/keep?mode=`: what moving a scan to `to` would free and cost. */
export type ScanKeepPlan = {
  scan_id: string;
  name: string;
  keep: KeepMode;
  to: KeepMode;
  ok: boolean;
  refusal: { detail: string; hint: string } | null;
  bytes: number;
  frees_bytes: number;
  /** What goes, gross per part. */
  remove: KeepPart[];
  /** What the move writes (signed bytes): `frees_bytes` = the sum of `remove` minus the sum of `writes`. */
  writes: { what: "state" | "frames_list" | "meta"; bytes: number }[];
  /** The current step's cache file that stays as the base (only when the result does not hold it); not freed. */
  kept_step_bytes: number;
  loses: KeepLoss[];
  keeps: KeepKept[];
  /** Standard removes the recording, thinning removes frames: ask once more, then send `confirm: true`. */
  confirm: boolean;
  /** R3: the plan keeps only the frames Standard quality uses (`thin=true`, with "engineering"). */
  thin?: boolean;
  /** R3: with `thin`, what the recording holds now and would hold afterwards (null when refused). */
  thinned?: ThinnedRecording | null;
};

/** R2: `GET /api/projects/{pid}/keep?mode=`. */
export type ProjectKeepPlan = { project_id: string; to: KeepMode; thin?: boolean; scans: ScanKeepPlan[]; frees_bytes: number; n_scans: number; confirm: boolean };
export type ScanKeepResult = { scan: Scan; freed_bytes: number; planned_bytes: number };
export type ProjectKeepResult = {
  project_id: string;
  to: KeepMode;
  thin?: boolean;
  results: { scan_id: string; name: string; ok: boolean; freed_bytes: number; error?: { detail: string; hint: string | null } }[];
  freed_bytes: number;
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

/** R1: one scan's folder on disk, in bytes (`GET /api/projects/{pid}/disk`). The parts add up to `bytes`. */
export type ScanDiskUse = {
  id: string;
  name: string;
  n_frames: number;
  duration_s: number;
  bytes: number;
  recording: number;
  /** The frame images alone (inside `recording`). */
  frames: number;
  n_frame_files: number;
  /** Each folder under `process/` but the undo cache: track, straighten, fuse, spacetime, refine. */
  stages: Record<string, number>;
  stages_bytes: number;
  /** `process/cache` and `process/selections`. */
  undo: number;
  result: number;
  other: number;
};

/** R1: a project's folder on disk; the trash is counted on its own, not in `bytes`. */
export type ProjectDiskUse = {
  project_id: string;
  bytes: number;
  scans: ScanDiskUse[];
  parts: { recording: number; stages: number; undo: number; result: number };
  references: number;
  other: number;
  /** `scans`: deleted scans (since the scan trash, U7; absent from an older engine). */
  trash: { bytes: number; projects: number; scans?: number };
  seconds: number;
};

/** U7: a deleted project or scan in the trash (APP_API.md "Rename a scan, the trash"). `id` is its only name. */
export type TrashItem = {
  id: string;
  kind: "project" | "scan";
  name: string;
  deleted: string | null;
  bytes: number;
  n_scans: number | null;
  project_id: string | null;
  project_name: string | null;
  project_exists: boolean | null;
};
export type TrashList = { items: TrashItem[]; bytes: number };
export type TrashRestored = { kind: "project" | "scan"; project_id: string; scan_id: string | null; name: string; renamed: boolean };
export type TrashEmptied = { removed: string[]; left: string[]; bytes: number };

export type SessionPhase = "idle" | "scanning" | "paused" | "stopping";
export type SessionState = {
  state: SessionPhase;
  project_id: string | null;
  scan_id: string | null;
  mode: ScanMode;
  elapsed_s: number;
  frames: number;
  /** N2: while a second pass records, the scan it belongs to (absent for a scan that continues nothing). */
  continues?: { scan_id: string; scan_name: string };
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
  /** Extra (UX_PLAN R2; `process_scan`, and `process_recipe` with scope "all"): the scan's earlier mesh is still its
   * own: true while the job is queued or running and after it ended cancelled or failed, when the scan had a mesh
   * before; false once the job is done (the new mesh took its place) and when the scan had no mesh before. */
  mesh_kept?: boolean;
  /** Extra (recipes): "all" or "edits". */
  scope?: string | null;
};

/** A job's frame pass: which stage, frames done of total, and its rate since the first frame (null until known). */
export type JobFrames = { stage: string; done: number; total: number; fps: number | null; elapsed_s: number };

export type Health = { app: string; version: string; engine: { opencv?: string; open3d?: string } & Record<string, unknown> };

/** Who chose the compute path (`health.engine.compute.preference_from`, UX_PLAN R20): the environment of this run, the
 * app's settings, or nobody (so without a graphics card none was found). */
export type ComputePreferenceFrom = "environment" | "settings" | "auto";

export type Settings = Record<string, unknown>;

// ------------------------------------------------------------------ request bodies
export type ConnectBody = { source: ConnectSource; recording?: string; loop?: boolean };
/** `preset` / `preset_values` (P2): the preset chosen on the Scan page and the values changed by hand there. */
export type StartBody = { project_id: string; mode?: ScanMode; name?: string; preset?: string; preset_values?: PresetValues; continues?: PassStart };
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
  /** Frames waiting to be written to disk (0 when idle). */
  record_backlog: number;
  /** Once a GPU stage failed in the running scan: stage ("depth", "track_filter") -> reason; it now runs on the CPU. */
  gpu_fallback: Record<string, string> | null;
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
  // the configured line lasers (config [lasers] lines) and those of them without a plane yet;
  // optional: an older server leaves them out (two lasers)
  lasers_configured?: string[];
  lasers_missing?: string[];
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
  last_check: { when: string; ok: boolean; verdict: string; parts?: HealthPart[] } | null;
  /** H3: the calibration's status (null without a calibration; absent from an older engine). It warns, never blocks. */
  calibration_health?: CalibHealth | null;
};

// ------------------------------------------------------------------ calibration health (H3, H4)
export type HealthState = "valid" | "check" | "recalibrate";
export type HealthPart = "cameras" | "lasers" | "imu";
/** What to do next; `step` is a SetupStep id (a wizard page). */
export type HealthAction = { step: "check" | "stereo" | "lasers" | "imu"; text: string };
export type HealthReason = {
  state: HealthState;
  source: "scans" | "launch_check" | "board_check" | "board_check_age" | "scan";
  part: HealthPart | null;
  reason: string;
  action: HealthAction | null;
  then: HealthAction | null;
  kind: string;
  value: number | string[] | null;
};
export type CalibHealth = {
  version: number;
  state: HealthState;
  label: string; // "Valid" | "Check recommended" | "Recalibration required"
  part: HealthPart | null; // null: the calibration as a whole (its age), or nothing (valid)
  part_label: string | null;
  reason: string; // one plain sentence
  action: HealthAction | null;
  then: HealthAction | null; // what to try if that does not mend it
  reasons: HealthReason[]; // every finding that is not valid, worst first
  parts: Record<HealthPart, HealthState | null>; // null: not judged
  not_judged: HealthPart[];
  note: string | null; // why something could not be judged
  provisional: boolean; // the limits come from the simulator until the real scanner is measured (H5)
  source: "setup" | "scan";
  scans?: { used: number; window: number; agree: number };
  signs?: Record<string, { value: number | null; unit: string; check: number; recalibrate: number; state: HealthState | null; missing?: boolean }>;
};
/** H4: a scan's own status in short, as the scan list keeps it (`Scan.result.report.health_status`). */
export type ScanHealthSummary = { state: HealthState; part: HealthPart | null; reason: string | null };

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
  lines: { vertical: number; horizontal: number } | null; // laser points on the page margin, per family
  /** Per configured laser: margin points; null while its line cannot be named yet (several lasers in a family). */
  line_points?: Record<string, number | null> | null;
  /** Per family: margin points of each line found, largest first. */
  lines_found?: Record<string, number[]> | null;
  thumb_url: string;
  /** lasers, one at a time: the lasers that each had a frame of their own for this view. */
  lit?: string[] | null;
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
  /** lasers only: one row per configured laser (missing from an older server). */
  planes?: CalibPlaneRow[];
};

export type CalibPlaneRow = {
  name: string; // "vertical", "vertical_2", ...
  label: string; // "Vertical", "Second vertical"
  calibrated: boolean;
  median_mm: number | null; // median distance of its points from the plane
  p95_mm: number | null;
  views: number | null; // board poses it was fitted from
  points: number | null;
  where: "margin" | "both" | null; // "both": the margin alone had too few points
  reason: string | null; // why it was not calibrated
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
  lasers?: string[]; // the configured line lasers (config [lasers] lines)
  /** lasers: each view also gets a frame per laser with only that laser lit (each laser on its own wire). */
  one_at_a_time?: boolean;
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

/** Laser lines in the live view. `vertical` / `horizontal` are per family (every line of it); the
 * rest is per configured laser and is missing from an older server. */
export type CalibLines = {
  vertical: number; // margin points of the family
  horizontal: number;
  vertical_uv: number[]; // image points of the family, flattened
  horizontal_uv: number[];
  points?: Record<string, number | null>; // per laser; null: not named yet (a first calibration with two lasers in a family)
  found?: Record<string, number[]>; // per family: margin points of each line found, largest first
  line_uv?: Record<string, number[]>; // image points per named laser of a family with several lasers
  meet_mm?: Record<string, number>; // a family with two lasers: where its two lines meet, mm from the scanner
};

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
/** `[process] thin_walls`: keep thin walls and edges from swelling in the mesh. "auto" turns it on for scans
 * without markers and leaves marker mode as it was; "on" and "off" force it. */
export type ThinWalls = "auto" | "on" | "off";
export type ProcessSettingsBody = { thin_walls?: ThinWalls };
/** `[lasers]`, any of: `lines`, the build's line lasers, e.g. ["vertical", "horizontal", "vertical_2", "horizontal_2"];
 * `own_channels`, each laser on its own Pico channel (firmware 1.2.0+); `brightness`, the whole map of laser name ->
 * percent of its family's level (a laser left out gets 100). */
export type LaserSettingsBody = { lines?: string[]; own_channels?: boolean; brightness?: Record<string, number> };
/** `lasers` of `Settings`; `own_channels` and `brightness` are missing from an older server. */
export type LaserSettings = { lines: string[]; own_channels?: boolean; brightness?: Record<string, number> };
export type OpenFolderReply = { opened: boolean; dir: string };
export type CaptureReply = { view: CalibView; session: CalibSession };
export type SaveReply = { session: CalibSession; setup: SetupState };

// ================================================================== M3 phase 2: processing studio
// APP_API.md "M3 phase 2: processing studio". Adds to M1 and M2; changes nothing in them.

/** The stages of a scan's processing; each ends in a new base mesh. */
export type StageId = "track" | "straighten" | "fuse" | "spacetime" | "refine";
/** A stage a base mesh can come from: the five, and Make mesh's "Make only the thin walls finer" (ENGINE_PLAN X5 step 2,
 * a choice per scan; in `ProcessState.stages` only once it ran). */
export type BaseStageId = StageId | "fine_walls";
export type StageState = "none" | "running" | "done" | "failed" | "skipped";

export type MeshOp = "remove_floaters" | "fill_holes" | "smooth" | "decimate" | "delete_faces" | "keep_faces" | "remove_table";
/** The edits a recipe can hold (a selection only fits the scan it was made on). "Remove the table" is in no built-in recipe. */
export type RecipeMeshOp = Exclude<MeshOp, "delete_faces" | "keep_faces">;

export type RemoveFloatersParams = { min_triangles?: number; min_size_mm?: number; keep_largest_only?: boolean };
/** `max_hole_mm`: the largest hole perimeter to fill; `watertight`: the outer edge too. */
export type FillHolesParams = { max_hole_mm: number; watertight?: boolean };
export type SmoothMethod = "taubin" | "bilateral";
/**
 * `strength` 0..1; `within_sigma`: no vertex moves more than twice its sigma; `flat_only`: only flat
 * areas move; `away_from_edges`: nothing near a sharp edge moves (one of the two at most).
 */
export type SmoothParams = {
  method: SmoothMethod;
  strength: number;
  keep_edges?: boolean;
  within_sigma?: boolean;
  flat_only?: boolean;
  away_from_edges?: boolean;
};
/** A target (one of the two) and / or a tolerance in mm. */
export type DecimateParams = { target_triangles?: number; ratio?: number; max_error_mm?: number };
/** base64 of little-endian uint32 face indices of the CURRENT mesh. */
export type FacesParams = { faces: string };
/**
 * "Remove the table" (FEATURE_PLAN X1): how far above the table's plane a face that faces the
 * table's way still counts as the table, mm (0 to 20; the engine's default 1 mm).
 */
export type RemoveTableParams = { margin_mm?: number; keep?: number[][] }; // keep: a point (mm) on each raised area to keep

/**
 * `POST .../process/remove-table/preview`: the faces "Remove the table" would remove from the
 * current mesh, and what the engine found; nothing changes. A 422 answers the engine's refusal
 * in its own words (no table to stand behind).
 */
/**
 * A raised area within the margin. `kept`: a thin part lying flat, it stays (or the user kept it:
 * `by_user`); `warned`: it may be a thin part and goes with the table unless kept; `uneven`: the
 * table's own unevenness there (said once, not drawn).
 */
export type TableRaised = {
  centre: [number, number, number]; // mm, world frame
  size_mm: number; // across
  area_mm2: number;
  height_mm: number; // its level above the table round it
  scatter_mm: number; // how much the table round it scatters (what it is told from)
  tilt_deg?: number; // its top against the table
  verdict: "kept" | "warned" | "uneven";
  kept: boolean;
  by_user?: boolean;
  n_faces: number;
  faces?: string | null; // base64 of little-endian uint32 face indices (kept and warned areas, in the preview)
};
export type TablePreview = {
  version: number; // the mesh version it was found on
  triangles: number; // of the current mesh
  faces: string; // base64 of little-endian uint32 face indices, ascending
  n_faces: number;
  area_mm2: number; // removed
  area_left_mm2: number;
  normal: [number, number, number]; // the table's plane n . x + d = 0, n towards the part (mm, world frame)
  d: number;
  share: number; // of the scan on the plane, 0..1
  front_share: number; // in front of it (the part)
  behind_share: number; // behind it
  flatness_mm: number; // RMS of the table about its plane
  band_mm: number; // the noise band: the faces of the plane itself
  margin_mm: number;
  facing_deg: number; // a face turned less than this from the plane's normal faces the table's way
  lowest_mm: number | null; // the lowest corner of what is left, above the plane
  loose_removed: number; // crumbs of table removed with it
  loose_left: number; // loose pieces left besides the part (Remove floaters takes them)
  loose_left_mm2: number;
  skipped: string[]; // larger flat surfaces set aside first, and why
  /** Raised areas within the margin: a thin part lying flat (kept) or one too close to the table's unevenness to tell (goes, with a warning). */
  raised: TableRaised[];
  thin_limit_mm?: number | null; // this scan's own limit: thinner than this a part lying flat cannot be told from the table
  thin_keep_mm?: number | null; // from this it stays, where the table round it is as flat as the table
  limit?: string; // the same, in words
  warnings: string[];
  rule: string; // the rule, in words
  summary: string; // "Found the table (95 % of the scan, …)"
  ms: number;
};

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
  /** What the edit did, in plain words (the engine's summary). */
  summary?: string | null;
  /** U14: what "Remove loose bits" took (absent on other edits, and on one made before). R23: the rule that took the
   * largest piece, and its triangles (absent on an edit made before). */
  removed?: { pieces: number; triangles: number; largest_mm: number; largest_rule?: LooseBitsRule | null; largest_triangles?: number };
};

/** Which limit of "Remove loose bits" took its largest removed piece (UX_PLAN R23). */
export type LooseBitsRule = "size" | "share" | "triangles" | "largest_only";

/** `started`: when it last started (ISO 8601); `seconds`: how long it ran (null while it runs and before; R17, R18). */
export type StageInfo = { state: StageState; summary: string | null; when: string | null; job_id: string | null; started?: string | null; seconds?: number | null };

/** A part of a Make mesh and the span of the job's progress (0–1) it takes (UX_PLAN R18). */
export type MakingPart = { stage: "track" | "straighten" | "fine_walls" | "spacetime" | "refine" | "edits"; from: number; to: number };

/** The run "Make mesh again" (or a recipe on everything) is building beside the scan's mesh (UX_PLAN R2, R18). */
export type MakingState = {
  stages: Partial<Record<BaseStageId, { state: StageState; summary: string | null; started?: string | null; seconds?: number | null }>>;
  plan?: MakingPart[] | null;
  /** How long the whole job has run, s. */
  elapsed_s?: number;
  /** The time left for the whole job at this pace, s; null under 3 %. */
  left_s?: number | null;
};

/** The Make mesh (or recipe on everything) that made the current base, with how long the whole job took (UX_PLAN R17). */
export type MadeInfo = {
  job_id: string;
  kind: string;
  when: string;
  seconds: number;
  stages: Partial<Record<string, number>>;
  /** ENGINE_PLAN X5: what Make mesh found that a finer mesh detail could help (absent from an older engine). */
  thin_parts?: ThinPartsHint | null;
  /** ENGINE_PLAN X5 step 2: what "Make only the thin walls finer" did in this Make mesh; absent when the scan did not
   * choose it (and from an older engine). */
  fine_walls?: FineWallsMade | null;
};
/** Why "Make only the thin walls finer" did not act (`FineWallsMade.why`). */
export type FineWallsWhy =
  | "no_thin_walls"
  | "no_kept_depth"
  | "no_graphics_card"
  | "no_finer_surface"
  | "mesh_detail"
  | "preview"
  | "disk"
  | "failed"
  | "made_fine";
/** "Make only the thin walls finer" in a Make mesh (ENGINE_PLAN X5 step 2, APP_API.md "The thin-parts hint"). */
export type FineWallsMade = {
  chosen: true;
  acted: boolean;
  why: FineWallsWhy | null;
  /** The share of the surface made finer (0–1), the wall regions found, how long the step took (s), the kept depth's
   * size on disk while it ran (MB); null when it did not run. */
  share: number | null;
  regions: number | null;
  seconds: number | null;
  kept_mb: number | null;
  /** With `why` "disk": the space it needs and the drive's free space, MB. With "mesh_detail": this mesh's, mm. */
  need_mb?: number;
  free_mb?: number;
  voxel_mm?: number;
  /** The engine's line for the page: what it did, or why not. */
  line: string;
  /** Only when it acted: the action's words ("Make the mesh again without this"). */
  action?: string;
};
/** The hint's first choice, "Make only the thin walls finer" (`ThinPartsHint.words.fine`). */
export type FineWallsOffer = { action: string; intro: string; gain: string[]; cost: string[]; disk_mb: number };
/** The thin-parts hint (ENGINE_PLAN X5 step 1, APP_API.md "The thin-parts hint"): a suggestion only. */
export type ThinPartsHint = {
  suggest: boolean;
  /** no_measured_gain: only a thin plate or small holes were found, where 0.5 mm was not measured to help. */
  why_not: "made_fine" | "nothing_found" | "no_measured_gain" | "empty" | "walls_made_finer" | null;
  found: ("walls" | "plates" | "holes")[];
  voxel_mm: number;
  preset_id: string;
  dismissed: boolean;
  /** The engine's sentences (null when there is nothing to suggest). */
  words: {
    title: string;
    intro: string;
    found: string[];
    gain: string[];
    cost: string[];
    measured: string;
    action: string;
    preset_id: string;
    preset_name: string;
    /** ENGINE_PLAN X5 step 2: "Make only the thin walls finer", offered first where it can run (the graphics card's
     * path, mesh detail 1 mm); null or absent: only the preset, as before. */
    fine?: FineWallsOffer | null;
  } | null;
};
export type TrackStage = StageInfo & {
  quality: ProcessQuality | null;
  tracked_pct: number | null;
  voxel_mm?: number | null;
  /** The camera path's warning (ENGINE_PLAN X0a): sentences; absent or [] when there is nothing to say. */
  warnings?: string[];
};
export type StraightenStage = StageInfo & {
  loops: number | null;
  correction_mm: number | null;
  correction_deg: number | null;
  /** Laser crossings the poses were lined up on (null: not adjusted). */
  crossings?: number | null;
  /** How many AprilTags held the scan (ENGINE_PLAN X8 step 2); null when the tags did not move it. An older engine
   * does not send it: as null. */
  tags?: number | null;
};
export type FuseStage = StageInfo & { voxel_mm: number | null };
/** "Refine from the images" (a trial build): the base stage's mesh measured against the recording's frames. */
export type SpacetimeStage = StageInfo & {
  source: BaseStageId | null; // the stage whose mesh was refined
  frames: number | null; // frames read
  patches: number | null; // patches on the surface
  kept: number | null; // patches with dots in enough frames, and a clear match
  covered_pct: number | null; // share of the surface refined, %
  moved_mm: number | null; // how far it moved (95th percentile), mm
  device: "gpu" | "cpu" | null;
};
/** "Refine with lasers": the base stage's mesh pulled onto its laser points. */
export type RefineStage = StageInfo & {
  source: BaseStageId | null; // the stage whose mesh was refined
  points: number | null; // laser points used
  outliers: number | null; // laser points that disagreed with the surface (glints, reflections)
  moved_pct: number | null; // share of the surface moved, %
  moved_mm: number | null; // how far it moved (95th percentile), mm
  residual_before_mm: number | null; // median distance of the laser points from the surface, before / after
  residual_after_mm: number | null;
};

/** "Make only the thin walls finer" (ENGINE_PLAN X5 step 2): in `ProcessState.stages` only once a Make mesh ran it. */
export type FineWallsStage = StageInfo & {
  source: BaseStageId | null; // the stage whose mesh was made finer
  why: FineWallsWhy | null;
  share_pct: number | null; // share of the surface made finer, %
  regions: number | null; // the wall regions found
  kept_mb: number | null; // the kept depth on disk while it ran, MB
};

export type ProcessState = {
  project_id: string;
  scan_id: string;
  stages: { track: TrackStage; straighten: StraightenStage; fuse: FuseStage; spacetime: SpacetimeStage; refine: RefineStage; fine_walls?: FineWallsStage };
  base: { triangles: number; vertices: number; stage: BaseStageId } | null;
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
  /** Extra (UX_PLAN R2): the stages of the run "Make mesh again" is building, while it runs; `stages` above stay the
   * current mesh's until the run takes its place. Absent or null otherwise (and from an older engine). */
  making?: MakingState | null;
  /** Extra (UX_PLAN R17): the Make mesh that made the current base; null for a scan made before, or once a single
   * stage replaced that base. */
  made?: MadeInfo | null;
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

// ================================================================== M5 first slice: fitted shapes
// APP_API.md "M5 first slice: fitted shapes". Adds to M1-M3; changes nothing in them.

export type FitKind = "plane" | "cylinder" | "hole";
export type FitRequest = {
  kind: FitKind;
  face: number; // the clicked face of result/mesh.ply
  point: [number, number, number]; // the clicked point on it (mm, the mesh's frame)
  tolerance_mm?: number | null; // null = chosen from the seed's measured scatter (and sigma, when it agrees)
  version?: number; // the mesh version the click was made on: 409 when it is no longer current
};
// All ± values are 1σ, statistical only (calibration and scale errors come on top). A direction
// (normal, axis) or a position across one (an axis point, a circle's centre) has two components:
// its ± is the RMS of the total error.
export type PlaneShape = {
  kind: "plane";
  point: number[];
  normal: number[];
  rms_mm: number;
  flatness_mm: number;
  n: number;
  normal_sigma_deg: number;
  offset_sigma_mm: number;
};
export type CylinderShape = {
  kind: "cylinder";
  point: number[];
  axis: number[];
  radius_mm: number;
  diameter_mm: number;
  rms_mm: number;
  n: number;
  radius_sigma_mm: number;
  diameter_sigma_mm: number;
  axis_sigma_deg: number;
  point_sigma_mm: number;
};
export type CircleShape = {
  kind: "circle";
  center: number[];
  normal: number[];
  radius_mm: number;
  diameter_mm: number;
  rms_mm: number;
  n: number;
  radius_sigma_mm: number;
  diameter_sigma_mm: number;
  center_sigma_mm: number;
};
export type FitShape = PlaneShape | CylinderShape | CircleShape;
// ENGINE_PLAN X16: a thin wall's thickness read from the scan's camera frames at a click (one-sided: "at most")
export type WallOffer = {
  available: boolean;
  why: string | null; // why it is not offered (the setting, a scan tracked by its markers, a merge, files gone)
};
export type WallRequest = {
  face: number; // the clicked face of result/mesh.ply
  point: [number, number, number]; // the clicked point on it (mm, the mesh's frame)
  version?: number; // the mesh version the click was made on: 409 when it is no longer current
};
export type WallReading = {
  available: true;
  at_most_mm: number; // what the user is told: the wall is at most this thick here
  reading_mm: number; // the frames' reading itself (for the record; not shown as "t ± s")
  pm_mm: number; // its scatter over the frames (1 SD; not shown)
  mesh_mm: number; // the mesh's own thickness there
  frames: [number, number]; // camera frames read on each side
  points: [number, number];
  tilt_deg: number; // how far the two sides' planes are from parallel
  seconds: number;
  words: string[]; // the engine's sentences, shown as they come
  timings_s: Record<string, number>;
  version: number;
};
// ENGINE_PLAN X15 step 2: the Hole tool's second answer, the hole's entrance rim read from the scan's camera images
export type HoleImagesOffer = {
  available: boolean;
  why: string | null; // why it is not offered (the setting, a merge, markers, Preview quality, no graphics card, files gone)
};
export type HoleImagesRequest = {
  face: number; // the clicked face of result/mesh.ply (on the face beside the hole)
  point: [number, number, number]; // the clicked point on it (mm, the mesh's frame)
  version?: number; // the mesh version the click was made on: 409 when it is no longer current
};
export type HoleImagesReading = {
  available: true;
  diameter_mm: number; // the entrance rim in the face's plane (not the bore below, not a chamfer's outer edge)
  pm_mm: number; // 1σ: the frames' scatter and a part they share
  centre: [number, number, number]; // mm, the mesh's frame (depends on the camera path; the diameter does not)
  normal: [number, number, number];
  frames_used: number;
  frames_tried: number;
  cover_deg: number | null; // how much of the rim's round was found (median over frames)
  warnings: string[];
  words: string[]; // the engine's sentences, shown as they come
  seconds: number;
  version: number;
};
/** A refusal's 422 body: the engine's words as `detail`, what to do as `hint`, no diameter. */
export type HoleImagesRefusal = { detail: string; hint?: string; refused?: boolean; frames_used?: number; frames_tried?: number; seconds?: number };

export type FitResult = {
  kind: FitKind; // as asked; a hole's shape is a circle
  shape: FitShape;
  faces: string; // the region: base64 of little-endian uint32 face indices, sorted
  n_faces: number;
  tolerance_mm: number | null; // the tolerance used (null for a hole)
  mean_sigma_mm: number | null; // mean sigma of the fitted points (null when the mesh has none)
  // plane, cylinder: how many points err together with each one (1 = independent); the shape's ± are already
  // widened by its square root. null for a hole, and from engines older than 2026-11-16
  correlated_points?: number | null;
  warnings: string[]; // plain language, e.g. "small region: 23 points"
  version: number; // the mesh version it was fitted on
};

// ------------------------------------------------------------------ kept features (FEATURE_PLAN.md K1)
// APP_API.md "Kept features": fits, measurements and relations saved with the scan (features.json).

/** The click a fit was grown from: enough to fit it again on a later mesh. */
export type FeatureSeed = {
  kind: FitKind;
  face: number; // the clicked face on the mesh version it was fitted on
  point: [number, number, number];
  tolerance_mm: number | null; // as asked (null = chosen from the seed's scatter)
};
export type KeptFeature = {
  id: string; // "f-…"
  name: string; // "Fit 1" unless the user named it
  kind: FitKind;
  shape: FitShape; // exactly as the fit route returned it, with its ± values
  seed: FeatureSeed;
  n_faces: number;
  tolerance_mm: number | null;
  mean_sigma_mm: number | null;
  correlated_points: number | null;
  warnings: string[];
  mesh_version: number; // the mesh version it was fitted on
  extent: FitExtent | null; // where its region lies (null: kept before extents existed; "fit again" measures it)
  // a cylinder: a boss (faces away from its axis) or a bore (a hole's wall); null for a plane or a hole, or when the
  // engine could not tell; left out for a fit kept before forms existed
  form?: CylinderForm | null;
  // approved nominal values (FEATURE_PLAN C3), as `GET .../features` shows them; left out by older servers
  nominals?: ApprovedNominal[];
  created: string;
  stale: boolean; // fitted on an earlier mesh than the current one
};

// ------------------------------------------------------------------ nominal values (FEATURE_PLAN.md C3)
// APP_API.md "Kept features", "Nominal values": round values offered beside measured ones (ohs/cad/nominal.py), and
// the ones an engineer approved, kept with the scan. The measured values never change.

/** Which value a nominal is for: a fit's size, or one of a relation's values. */
export type NominalValueName = "diameter" | "radius" | "angle" | "distance" | "centre_distance";
export type NominalSource = "suggested" | "entered";
/** One round value offered (ohs.cad.nominal `Suggestion.to_dict()`). */
export type NominalSuggestion = {
  nominal: number; // mm, or degrees
  difference: number; // nominal − measured
  step: number;
  allowed_by: "2 sigma" | "bias"; // "bias": offered only because of a known bias (simulator numbers)
  bias: Record<string, unknown> | null;
  word: string | null; // "parallel", "perpendicular", "in one plane", "coaxial"
  directions: string | null;
  sentence: string; // why it is offered, in the engine's words
};
/** What is offered for one measured value, or nothing and why (ohs.cad.nominal `Offer.to_dict()`). */
export type NominalOffer = {
  value: NominalValueName;
  label: string;
  unit: "mm" | "deg";
  measured: number;
  sigma: number | null;
  suggestions: NominalSuggestion[];
  reason_code: string | null; // when there are none: "already_round", "no_candidate", "too_many", "no_sigma", "warning"
  reason: string | null;
  // the round values a known bias alone would allow, left out because they were not asked for (with_bias off);
  // left out by older servers
  bias_only?: number[];
};
/** An approved nominal value as kept, with the measurement now and whether to check it again. */
export type ApprovedNominal = {
  value: NominalValueName;
  nominal: number;
  unit: "mm" | "deg";
  word: string | null;
  source: NominalSource;
  measured: number; // what it was approved against
  sigma: number | null;
  with_bias?: boolean; // approved with the offers a known bias alone allows shown: "check again" counts the bias for it
  approved: string;
  measured_now: number | null; // the value measured now (null: the relation has no such value any more)
  sigma_now: number | null;
  check_again: boolean; // measured again, and the new measurement no longer allows it
  check_reason: string | null;
};
/** `GET .../features/{id}/nominal` (and the answer of POST): the offers and the approvals of a kept fit or relation. */
export type NominalAnswer = {
  id: string;
  name: string;
  kind: "fit" | "relation";
  form: CylinderForm | null;
  poses: "tracked" | "true"; // which biases apply, from the scan's processing report
  poses_reason: string;
  with_bias?: boolean; // whether the offers include those a known bias alone allows (asked with `with_bias`)
  move?: RelationMove | null; // a relation's move in the CAD export (null: listed only; always null for a fit)
  offers: NominalOffer[];
  nominals: ApprovedNominal[];
};
export type NewNominal = {
  value: NominalValueName;
  nominal: number;
  source: NominalSource;
  with_bias?: boolean; // the offers were asked with the known biases (kept as the approval's rule)
  move?: MoveChoice; // a relation: which fit moves in the CAD export, chosen with the approval
};
/** Which of a kept relation's two fits moves to suit its approved values in the CAD export; "listed": none (the default). */
export type MoveChoice = "listed" | "a" | "b";
/** A relation's move as kept: the fit that moves, and its place in the order the moves are applied. */
export type RelationMove = { shape: "a" | "b"; order: number; chosen: string };
/** What a kept cylinder is (ohs.measure.region `cylinder_form`). */
export type CylinderForm = "boss" | "bore";
// Where a kept fit's region lies on its shape (ohs.measure.region_extent; the small part features.json keeps,
// ohs/measure/features.py "The extent"). mm, the scan's frame.
export type PlaneExtent = {
  kind: "plane";
  origin: number[]; // the plane's 2D basis: a point is origin + x u + y v
  u: number[];
  v: number[];
  // the smallest rectangle round the outline: its centre, its axes (the longer side's first) and half sizes
  rectangle: { center: number[]; axes: number[][]; half_sizes_mm: [number, number] };
  area_mm2: number; // the outline's area (its outer loops' less its holes')
  n_outer: number; // loops of the outline (the loops themselves: GET .../features/{id}/outline)
  n_holes: number;
  n_points: number;
};
export type CylinderExtent = {
  kind: "cylinder";
  along_mm: [number, number]; // the region along the axis, from the shape's point
  length_mm: number;
  zero: number[]; // unit, square to the axis: angles are taken from it, turning right-handed about the axis
  start_deg: number;
  arc_deg: number; // how much of the round the region covers (360: all of it)
};
export type HoleExtent = {
  kind: "hole";
  n_edge: number; // points on its edge
  edge: number[][] | null; // the edge in order round the loop when short; else null (GET .../features/{id}/outline)
};
export type FitExtent = PlaneExtent | CylinderExtent | HoleExtent;
/** `GET .../features/{id}/outline`: a plane's outline loops (2D in its basis) or a hole's edge (3D). */
export type FeatureOutline =
  | { id: string; mesh_version: number; kind: "plane"; origin: number[]; u: number[]; v: number[]; outer: number[][][]; holes: number[][][] }
  | { id: string; mesh_version: number; kind: "hole"; edge: number[][] };
/** One value of a relation as the engine gives it (ohs.measure.relate `Quantity`). */
export type RelationValue = {
  name: "angle" | "distance" | "centre_distance";
  label: string; // "Angle (parallel)", "Distance between the axes", ...
  value: number;
  sigma: number | null; // 1σ from both fits' ±; null where the fits give none (an angle with a hole's axis)
  unit: "deg" | "mm";
};
/** What applies between two fits (ohs.measure.relate `Relation.to_dict()`). */
export type RelationRecord = { kinds: [FitKind, FitKind]; parallel: boolean; values: RelationValue[]; notes: string[] };
/** `GET .../features/relate?a=&b=`: two kept fits' relation, not kept. */
export type PairRelation = { a: string; b: string; stale: boolean; relation: RelationRecord | null; error?: string };
export type KeptMeasurement = {
  id: string; // "m-…"
  name: string; // "Distance 1" unless the user named it
  a: [number, number, number];
  b: [number, number, number];
  mesh_version: number | null; // the mesh version the points were picked on
  created: string;
  stale: boolean;
};
/** Two kept fits the user asked to relate; the numbers are worked out when shown (by the engine: `relation`). */
export type KeptRelation = {
  id: string;
  name: string;
  a: string;
  b: string;
  created: string;
  stale?: boolean; // one of the two fits was fitted on an earlier mesh
  relation?: RelationRecord | null; // the engine's values for the two kept shapes (null: it could not relate them, see error)
  error?: string;
  nominals?: ApprovedNominal[]; // approved nominal values, as for a fit
  move?: RelationMove; // one of its fits moves to suit it in the CAD export; left out: listed only
};
export type FeatureSet = {
  format_version: number;
  // the part's coordinate system: a `PartFrameView` when this version can read it (lib/partFrame.ts `readFrame`),
  // else what the file holds, with `readable: false`; null without one
  frame: Record<string, unknown> | null;
  mesh_version: number | null; // the current mesh's version (null before there is a mesh)
  features: KeptFeature[];
  measurements: KeptMeasurement[];
  relations: KeptRelation[];
};
export type NewFeature = FitRequest & { name?: string };
/** A fit just kept: the feature with its region (base64, as in `FitResult`). */
export type KeptFit = KeptFeature & { faces: string };
export type NewMeasurement = { a: [number, number, number]; b: [number, number, number]; version?: number; name?: string };
export type NewRelation = { a: string; b: string; name?: string };
export type FeatureFaces = { id: string; mesh_version: number; n_faces: number; faces: string };
export type RefitResult = {
  features: FeatureSet;
  refitted: string[];
  failed: { id: string; detail: string; hint: string | null }[]; // stay as they were, still marked
};

// ------------------------------------------------------------------ the part's frame (FEATURE_PLAN.md K3)
// APP_API.md "Kept features", "The part's frame": built from three kept fits by the 3-2-1 rule (ohs/measure/frame.py).

export type AxisName = "X" | "Y" | "Z";
export type FrameRole = "primary" | "secondary" | "origin";
/** One slot of a frame: the feature it was built from, as it was then. */
export type FrameSlot = {
  role: FrameRole;
  id: string | null; // the kept fit's id; null for a point given as the origin
  name: string | null; // the fit's name when the frame was built
  kind: "plane" | "cylinder" | "circle" | "point";
  sets: string; // in words, what it sets, e.g. "the Z axis (its normal) and Z = 0 (the plane)"
  fit: Record<string, unknown>; // the shape it was built from (a point: {kind: "point", point, sigma_mm?})
};
/** The frame's 1σ (null where a fit gives no ±; a warning says which). */
export type FrameSigma = {
  angle_deg: number | null; // the whole rotation, RMS
  tilt_deg: number | null; // its part about the two in-plane axes
  turn_deg: number | null; // about the first axis
  origin_mm: number | null; // the origin's displacement, RMS
  origin_axes_mm: (number | null)[]; // per part axis X, Y, Z
};
/** `PartFrame.to_dict()`: what features.json keeps under `frame`. */
export type PartFrameRecord = {
  format_version: 1;
  units: "mm";
  matrix: number[][]; // 4x4, rows: x_part = R x_scan + t (R's rows are the part's X, Y, Z in the scan's frame)
  axes: { primary: AxisName; secondary: AxisName; third: AxisName };
  flip_primary: boolean;
  flip_secondary: boolean;
  secondary_zero: boolean;
  built_from: FrameSlot[]; // primary, secondary, origin
  sigma: FrameSigma;
  warnings: string[];
};
/** The frame as `GET .../features` shows it. */
export type PartFrameView = PartFrameRecord & {
  readable: true;
  out_of_date: boolean; // a fit it was built from was deleted, fitted again or changed since: "Build again"
  out_of_date_reasons: string[];
};
/** A point as the origin, in the scan's frame (mm); `sigma_mm` its 1σ when known. */
export type FramePoint = { point: [number, number, number]; sigma_mm?: number | null };
export type NewFrame = {
  primary: string; // a kept plane
  secondary: string; // a kept plane, cylinder or hole
  origin: string | FramePoint; // a kept hole, cylinder or plane, or a point
  axes?: string; // the primary's and the secondary's axis names, default "ZX"
  flip_primary?: boolean;
  flip_secondary?: boolean;
  secondary_zero?: boolean; // strict 3-2-1
};

// ------------------------------------------------------------------ kept fits for CAD (FEATURE_PLAN C2)
// APP_API.md "For CAD": the kept fits as a STEP file and a FreeCAD macro, in the part's frame when there is one.

/** A plane's edge in the CAD files: its rectangle (four clean edges), or its real outline (simplified). */
export type CadBoundary = "rectangle" | "outline";
export type CadFile = "fits.step" | "fits.FCMacro";
/** `GET .../features/cad`: what the two files would hold. */
export type CadSummary = {
  boundary: CadBoundary;
  n_fits: number; // the kept fits exported (all of them)
  counts: { plane: number; cylinder: number; hole: number; point: number };
  frame: "part" | "scan"; // the frame the files are in
  frame_label: string; // "the part's axes" | "the scan's own axes"
  notes: string[]; // the writer's, in plain words (a fit kept with no size, what an outline left out, ...)
  warnings: string[]; // fits made on an earlier mesh, a frame out of date or not readable
  stale: string[]; // ids of the fits made on an earlier mesh
  /** With boundary "outline": the planes written with their outline, their points, the holes kept and the outlines'
   * holes left out (pits of the scan, holes that touch); null for "rectangle". */
  outline: { planes: number; points: number; holes_kept: number; holes_left_out: number } | null;
  step_bytes: number | null; // null without kept fits
  macro_bytes: number | null;
  filenames: { step: string; macro: string }; // the download names, from the scan's name
  mesh_version: number | null;
  reason: string | null; // why nothing can be exported (no kept fits), else null
  nominal?: CadNominal; // the approved values used (FEATURE_PLAN C3); left out by older servers
};
/** What the CAD files do with the approved nominal values. */
export type CadNominal = {
  measured_only: boolean; // asked for the measured values only: no approval is used
  sizes_used: number; // approved sizes put into the shapes
  sizes: { id: string; name: string; value: "diameter" | "radius"; nominal: number; measured: number | null; sigma: number | null; check_again: boolean }[];
  relations_listed: number; // approved relation values only listed in the notes, not applied to the geometry
  relations_applied?: number; // approved relation values a move applied (left out by older servers)
  relations: {
    id: string;
    name: string;
    value: NominalValueName;
    nominal: number;
    word: string | null;
    check_again: boolean;
    move?: MoveChoice;
    applied?: boolean;
  }[];
  relations_note: string | null;
  moves?: CadMove[]; // each relation with a move chosen, in the order applied
};
/** What became of one relation's move in the CAD files (ohs.cad.moves `Move.to_dict()`). */
export type CadMove = {
  id: string;
  name: string;
  shape: "a" | "b";
  moved_id: string | null;
  moved: string | null; // the fit moved
  fixed_id: string | null;
  to_suit: string | null; // the fit it was moved to suit
  applied: boolean;
  turn_deg: number; // turned about its own point
  slide_mm: number;
  slide: number[];
  values: { value: NominalValueName; nominal: number; word: string | null; exported: number | null }[];
  holds: boolean;
  check_again: boolean;
  reason: string | null; // why it was not applied
  warnings: string[];
  sentence: string;
};

// ------------------------------------------------------------------ sections (FEATURE_PLAN I1)
// APP_API.md "Sections": the current mesh cut by a plane, its 2D profile, its DXF and SVG; sections kept with the scan.

/** How the cutting plane is named. Points and normals are in the scan's frame, mm. */
export type SectionPlaneDef =
  | { kind: "fit"; fit: string; offset_mm?: number } // a kept plane moved along its normal
  | { kind: "frame"; axis: string; value_mm: number } // "X" | "Y" | "Z" (or "-X" ...) = value in the part's frame
  | { kind: "point_normal"; point: number[]; normal: number[] }
  | { kind: "points"; points: number[][] }; // three points; the normal by the right-hand rule
/** The definition as the engine understood it (and keeps it): a fit's name and the mesh version it was fitted on. */
export type SectionDefinition = SectionPlaneDef & { fit_name?: string; fit_mesh_version?: number };
/** The plane and its drawing's axes (ohs.mesh.section SectionPlane.to_dict()): a drawing point (x, y) is
 * `point + x u + y v` in the scan's frame; the drawing is seen from the side `normal` points to. */
export type SectionPlane = { point: number[]; normal: number[]; u: number[]; v: number[]; description: string; in_part_frame: boolean };
/** A run of a line's segments over measured or filled (not measured) faces: [first, end (exclusive), filled].
 * Segment i runs from point i to point i + 1 (the last of a closed line back to point 0). */
export type SectionStretch = [number, number, boolean];
/** A line in full (the engine's SectionLine.to_dict(), with its stretches). */
export type SectionLineFull = {
  closed: boolean;
  length_mm: number;
  area_mm2: number | null; // signed: + an outline (counter-clockwise), − a hole; null for an open line
  bbox: number[]; // [umin, vmin, umax, vmax]
  points: number[][];
  points3d: number[][];
  sigma: number[] | null;
  filled: boolean[];
  faces: number[];
  stretches: SectionStretch[];
};
/** A line in the compact answer (what the viewer asks for): 2D points as one flat list, to the micrometre. */
export type SectionLineCompact = {
  closed: boolean;
  length_mm: number;
  area_mm2: number | null;
  bbox: number[];
  n_points: number;
  points: number[]; // [x0, y0, x1, y1, ...]
  sigma_mm: { mean: number | null; max: number | null } | null; // over its measured points; null: the mesh has none
  stretches: SectionStretch[];
};
export type SectionAnswer<L = SectionLineCompact> = {
  format_version: number;
  units: "mm";
  plane: SectionPlane;
  lines: L[]; // longest first
  length_mm: number; // the whole cut's
  n_points: number;
  bbox: number[] | null; // null: the plane misses the scan
  n_lines: number;
  n_closed: number;
  mesh_version: number;
  definition: SectionDefinition;
  stale_fit: boolean; // named from a fit made on an earlier mesh (its plane is used as kept)
  warnings: string[];
  compact: boolean;
  /** Only the longest lines were sent (a compact answer holds at most `max_points`); the totals are the whole cut's. */
  truncated: { lines_shown: number; points_shown: number; lines: number; points: number } | null;
  filenames: { dxf: string; svg: string };
  kept: KeptSection | null; // a kept section's record (GET .../sections/{id}), else null
};
export type SectionRequest = SectionPlaneDef & { compact?: boolean; max_points?: number };
export type KeptSection = {
  id: string; // "s-…"
  name: string; // "Section N" unless the user named it
  plane: SectionPlane; // kept as numbers: a fit or frame changed or deleted later does not move it
  definition: SectionDefinition;
  mesh_version: number; // the mesh version it was last cut on
  created: string;
  mesh_changed: boolean; // the current mesh is not that one
};
export type KeptSections = { mesh_version: number | null; sections: KeptSection[] };
export type NewSection = SectionPlaneDef & { name?: string };
export type SectionFile = "section.dxf" | "section.svg";

// ------------------------------------------------------------------ flush along a seam (FEATURE_PLAN I4)
// APP_API.md "Flush along a seam": the step between two panels from two clicks, one on each panel near the seam.

/** Where a side of a station's section was read: on measured surface up to its edge, into filled (not measured)
 * surface, across the gap (the scan has closed it there), or no surface at all. */
export type FlushSide = "measured" | "filled" | "bridged" | "missing";
/** One section across the seam (ohs.inspect.gapflush GapFlushStation.to_dict()); lengths in mm, the scan's frame. */
export type FlushStation = {
  position_mm: number; // along the seam from the clicks
  point: number[]; // the seam's middle on the first panel's surface line
  direction: number[]; // the seam's direction here
  normal: number[]; // the first panel's normal here
  gap_mm: number | null; // worked out for the record; not offered (see gap_offered)
  gap_sigma_mm: number | null;
  flush_mm: number | null; // + : the second panel stands above the first
  flush_sigma_mm: number | null; // 1σ, the spread from scan to scan
  edge_a: number[] | null;
  edge_b: number[] | null;
  side_a: FlushSide;
  side_b: FlushSide;
  bridged: boolean;
  gap_trusted: boolean;
  flush_trusted: boolean;
  notes: string[]; // why a reading is not trusted, in words
  gap_by_rule: Record<string, number | null>;
  n_points_a: number;
  n_points_b: number;
  rms_a_mm: number | null;
  rms_b_mm: number | null;
};
/** Lowest, mean and highest over the trusted stations, with where along the seam (null: none trusted). */
export type SeamStats = {
  lowest: number | null;
  lowest_at_mm: number | null;
  mean: number | null;
  highest: number | null;
  highest_at_mm: number | null;
  n_trusted: number;
  n_stations: number;
};
/** ohs.inspect.gapflush GapFlush.to_dict(): the engine's record. */
export type FlushRecord = {
  format_version: number;
  units: "mm";
  rule: string;
  step_mm: number;
  flush_sign: string;
  gap: SeamStats; // for the record only
  flush: SeamStats;
  gap_offered: boolean; // false: the app shows the flush alone and says why (gap_note)
  gap_note: string;
  correlated_points: number;
  clicks: number[][];
  stations: FlushStation[];
};
export type FlushAnswer = FlushRecord & {
  mesh_version: number;
  warnings: string[]; // the mesh changed since a kept seam was kept; stations not trusted
  filename: string; // the stations CSV's download name
  kept: KeptSeam | null; // GET .../seams/{id}: the kept record; else null
};
export type FlushRequest = { a: [number, number, number]; b: [number, number, number] };
export type KeptSeam = {
  id: string; // "sm-…"
  name: string; // "Seam N" unless the user named it
  a: [number, number, number]; // the click on the first panel (panel A)
  b: [number, number, number]; // the click on the second panel
  mesh_version: number; // the mesh version it was last measured on
  created: string;
  mesh_changed: boolean; // the current mesh is not that one
};
export type KeptSeams = { mesh_version: number | null; seams: KeptSeam[] };
export type NewSeam = FlushRequest & { name?: string };

// ------------------------------------------------------------------ the report (FEATURE_PLAN I3)
// APP_API.md "The report": the scan's data as one self-contained HTML page, the kept features as CSV and JSON.

export type ReportFile = "report.html" | "features.csv" | "features.json";
/** A view of the model for the report: PNG bytes in base64 (no "data:" prefix). */
export type ReportPicture = { title: string; caption?: string | null; data: string };
export type ReportRequest = {
  tolerance_mm?: number | null; // the Check answer at this tolerance; left out without one
  numbers_in_part_frame?: boolean;
  seam?: string | null; // the kept seam shown (default: the first)
  pictures?: ReportPicture[];
};

// ------------------------------------------------------------------ M5 second slice: the deviation map
// APP_API.md "M5 second slice: the deviation map". Compares the current mesh with the base mesh.

export type DeviationInfo = {
  version: number; // the mesh version it was made for
  vertices: number; // values in deviation.bin = vertices of result/mesh.ply
  edits: number; // the cursor: 0 = no edits, every value is 0
  reference: { stage: string; triangles: number; vertices: number }; // the base mesh
  stats: {
    n: number; // compared vertices
    new: number; // vertices of filled faces only (no value)
    rms_mm: number | null;
    mean_mm: number | null; // signed
    p50_mm: number | null; // of |move|
    p95_mm: number | null;
    p99_mm: number | null;
    max_mm: number | null;
    moved: number | null; // share moved by more than 0.01 mm; the numbers are null when n = 0
  };
};

/** The deviation of the current mesh: one signed move in mm per vertex (NaN = new surface), in the PLY's order. */
export type Deviation = { info: DeviationInfo; values: Float32Array };

// ------------------------------------------------------------------ M5 fourth slice: where the lasers and the mesh disagree
// APP_API.md "M5 fourth slice: where the lasers and the mesh disagree". Compares the current mesh (made from
// the stereo depth) with the scan's laser points; the Lasers shading shows it.

export type LaserInfo = {
  version: number; // the mesh version it was made for
  vertices: number; // values in lasers.bin = vertices of result/mesh.ply
  available: boolean; // false: this scan has no laser points (the lasers were off or not calibrated)
  gate_mm: number; // laser points farther than this from the surface were left out as stray points
  points: {
    total: number; // laser points of the scan
    on_surface: number; // those that fall on the mesh (not past an edge or over a hole)
    used: number; // those within gate_mm: they make the map
    stray: number; // on the surface but farther than gate_mm (glints, reflections)
  };
  stats: {
    n: number; // vertices with a value
    covered: number | null; // share of the surface (by area) that has a value, 0..1
    mean_mm: number | null; // signed
    rms_mm: number | null; // this and the next three: of |distance|
    p50_mm: number | null;
    p95_mm: number | null;
    max_mm: number | null;
    within_0_1: number | null; // share of the vertices with a value that are within 0.1 mm
    within_0_3: number | null; // ... within 0.3 mm; the numbers are null when n = 0
  };
};

/**
 * The laser comparison of the current mesh: per vertex, the signed distance in mm from the mesh to the
 * laser points near it, along the vertex normal (+ = the lasers see the surface further out than the
 * mesh, - = further in); NaN where no laser point came near. In the PLY's order.
 */
export type LaserMap = { info: LaserInfo; values: Float32Array };

// ------------------------------------------------------------------ M5 third slice: areas that need another pass
// APP_API.md "M5 third slice: areas that need another pass". Works on the scan's current mesh.

export type CheckKind = "uncertain" | "not_measured"; // 2 sigma above the tolerance | made up by hole filling (or no sigma there)
export type CheckArea = {
  kind: CheckKind;
  area_mm2: number;
  share: number; // of the whole surface, 0..1
  center: [number, number, number]; // area-weighted, mm
  extent_mm: number; // largest side of its bounding box
  radius_mm: number; // farthest vertex from the centre
  n_faces: number;
  sigma_median_mm: number | null; // over its measured vertices (null when it has none)
  sigma_worst_mm: number | null;
  faces: string; // base64 of little-endian uint32 face indices of result/mesh.ply, sorted
};
export type CheckResult = {
  version: number; // the mesh version it was made on
  tolerance_mm: number;
  k: number; // 2: a face fails when k x the mean sigma of its vertices is above the tolerance
  has_sigma: boolean; // false: this mesh has no confidence values (made without a GPU)
  area_mm2: number; // the whole surface
  min_area_mm2: number; // smaller areas are not listed (they still count in the shares)
  share_ok: number | null; // null when has_sigma is false (and for an empty mesh)
  share_uncertain: number | null;
  share_not_measured: number | null; // null for an empty mesh only
  n_areas: number; // all areas above the minimum
  areas: CheckArea[]; // largest first, at most 50
};

// FEATURE_PLAN N1: why each area is weak, and where to scan it from (APP_API.md "POST .../check/why").
// The first that holds: not seen at all, steep, far, too few looks, on an edge; else unknown (and why it can't be told).
export type CheckReason = "not_seen" | "steep" | "far" | "few_looks" | "edge" | "unknown";
/** Where to hold the scanner: camera C of the scanner (the left camera), in the mesh's frame (mm). */
export type CheckPose = {
  T_wc: number[][]; // 4x4, camera C (x right, y down, z forward)
  position_mm: [number, number, number]; // the scanner's centre (between its two cameras)
  camera_position_mm: [number, number, number];
  look_dir: [number, number, number]; // unit, from the scanner to the area
  up: [number, number, number]; // the scanner's up (its images' up)
  target_mm: [number, number, number]; // the area's centre
  distance_mm: number; // the working distance
  tilt_deg: number; // from the area's normal (0: square on)
  azimuth_deg: number;
};
export type CheckView = {
  ok: boolean; // false: no pose, `reason` says why in words
  pose: CheckPose | null;
  share_seen: number | null; // of the area, from the pose
  border_seen: number | null; // of the unseen edge beside it, looked past
  candidates: number;
  missed: { blocked: number; facing_away: number; steep: number; outside: number } | null;
  reason: string | null;
  text: string; // one sentence for the user
};
export type CheckWhyArea = {
  index: number; // its place in the Check answer's list, from 0
  kind: CheckKind;
  area_mm2: number;
  n_faces: number;
  center: [number, number, number];
  normal: [number, number, number]; // the mean of its vertices' normals (0 0 0: it faces every way)
  reason: CheckReason;
  reasons: CheckReason[]; // every one that holds, in that order
  reason_text: string;
  evidence: {
    looks: number | null;
    looks_passing: number | null;
    view_angle_deg: number | null;
    distance_mm: number | null;
    in_view_share: number | null;
    edge_share: number | null;
    border_share: number;
    outward: [number, number, number] | null;
  };
  unknown_because: string[]; // "steep, far: ...", "few_looks: ...", "edge: ...": what was missing to tell
  view: CheckView;
};
export type CheckPlanPose = {
  area: number; // the area it is for (index)
  kind: "area" | "more"; // the area's own pose, or another direction for a large area
  pose: CheckPose;
  share_seen: number;
  border_seen: number | null;
  also_sees: { area: number; share: number }[];
};
export type CheckWhy = {
  version: number;
  tolerance_mm: number;
  n_areas: number;
  areas: CheckWhyArea[]; // the Check answer's areas, in its order
  plan: CheckPlanPose[]; // at most 3
  given: { looks: boolean; views: boolean; edges: boolean; working_mm: number | null; steep_deg: number; far_mm: number | null; [k: string]: unknown };
  camera_path: {
    available: boolean;
    why: string | null; // why there is none, in words
    scans: { name: string; frames: number; trajectory: string }[];
    frames_total: number;
    frames_used: number; // at most frames_cap, evenly spread
    frames_cap: number | null;
  };
  ms: number;
};

// ------------------------------------------------------------------ N2: a second pass that joins the first
// APP_API.md "A second pass". "Scan these areas" in the Check panel starts a scan with `continues`; after
// Stop the pass is made into a mesh, aligned to the first scan and merged with it, as jobs.

/** A place to scan from, as the Check panel gave it: the pose and its sentence. */
export type PassPose = { area: number | null; kind: string; text: string | null; pose: Partial<CheckPose> & Pick<CheckPose, "target_mm" | "look_dir"> };
/** What `POST /api/scan/start` takes as `continues`. */
export type PassStart = {
  scan_id: string;
  tolerance_mm: number;
  version?: number; // the first mesh's version the Check answer was made on (409 when it changed)
  areas: number[]; // places in that Check answer, from 0
  poses?: { area: number | null; kind: string; text: string | null; pose: CheckPose }[];
  same_setup?: boolean; // false: the part was moved since the first scan
};
export type PassChainState = "waiting" | "processing" | "aligning" | "merging" | "merged" | "not_aligned" | "not_merged" | "failed" | "cancelled";
export type PassStep = "process" | "align" | "merge";
export type PassChain = {
  state: PassChainState;
  step: PassStep | null;
  jobs: Partial<Record<PassStep, string>>;
  message: string;
  merged_scan_id?: string | null;
  align?: { accepted: boolean; verdict: string; overlap?: number | null; rms_mm?: number | null; little_shared?: boolean | null; caution?: string | null } | null;
};
export type PassArea = { index: number; kind: CheckKind; area_mm2: number; center: [number, number, number]; n_faces: number };
/** The link a pass's scan entry keeps (`Scan.continues`). */
export type PassLink = {
  scan_id: string;
  scan_name: string;
  tolerance_mm: number;
  version: number;
  areas: PassArea[];
  poses: PassPose[];
  same_setup: boolean;
  /** `same_setup` in words, as the engine got it ("The part was not moved since the first scan: ..."); older passes lack it. */
  setup_words?: string;
  /** Why the pass could be aligned but not merged (the first scan's recording is gone...); null when it can be. */
  merge_problem: string | null;
  preset_from_first: boolean;
  preset: string; // the preset's name, as the pass got it
  chain: PassChain;
};
/** `GET /api/projects/{pid}/scans/{sid}/pass`. */
export type PassView = Omit<PassLink, "chain"> & {
  pass_id: string;
  pass_name: string;
  chain: PassChain;
  job: Job | null; // the step's job (running or last)
  steps: PassStep[];
  note: string; // a merge adds coverage, not accuracy
  first: { id: string; name: string; mesh_url: string | null; has_mesh: boolean };
  /** The asked areas' faces on the first mesh (base64 uint32, one per area), while that mesh is the version they were asked on. */
  areas_faces: string[] | null;
  areas_faces_why_not: string | null;
};
export type PassShares = {
  id: string;
  name: string;
  share_ok: number | null;
  share_uncertain: number | null;
  share_not_measured: number | null;
  n_areas: number;
  area_mm2: number;
  has_sigma: boolean;
};
export type PassAreaState = "passes" | "partly" | "weak" | "not_found" | "unknown";
/** `POST /api/projects/{pid}/scans/{sid}/pass/compare`: before and after at the same tolerance. */
export type PassCompare = {
  tolerance_mm: number;
  note: string;
  rule: { match_mm: number; match_angle_deg: number; passes_share: number };
  first: PassShares;
  merged: PassShares;
  areas: (PassArea & { share_ok: number | null; found_share: number; state: PassAreaState })[];
  areas_note: string | null;
  /** On the surface both meshes hold (the first scan's surface found on the merged one): the share that passes before and after. */
  common?: { found_share: number; before_ok: number | null; after_ok: number | null };
  /** The first scan's surface that is not in the merged scan, and why when it is known. */
  missing?: { share: number; area_mm2: number; why: string | null };
  /** What `first` and `merged` are: each whole mesh, as its own Check says it. */
  whole_note?: string;
};

// ------------------------------------------------------------------ M4 first slice: align two scans and merge them
// APP_API.md "M4 first slice: align two scans and merge them". The target scan keeps its place; the
// source scan is moved onto it. Points picked on a mesh are in that scan's own frame, in mm.

/** One spot on the part, clicked on both meshes. */
export type PointPair = { source: [number, number, number]; target: [number, number, number] };

export type AlignRequest = {
  target: string; // scan id: stays where it is
  source: string; // scan id: is moved onto the target
  same_setup?: boolean; // default true: the part was not moved on its table between the two scans. false: it was turned over or moved
  pairs?: PointPair[]; // optional: 3 or more matching points picked on the two meshes, as a rough start
  use_tags?: boolean; // default true: without pairs, start from the AprilTags both scans saw
};

/** What the tags two scans share did for their alignment (APP_API.md, "TagStart"). */
export type TagStart = {
  shared: number[]; // tag ids both scans saw
  used: number[]; // those the start was made from
  left_out: number[]; // shared tags that did not fit the others
  rms_mm: number | null; // how well the used tags' corners agree
  start: string; // "accepted", or why the tags gave no accepted start (the shape's own guesses were then used)
};

/** The `result` of a finished job of kind "align". */
export type AlignResult = {
  project_id: string;
  target: string;
  source: string;
  accepted: boolean; // false: not aligned; `reason` and `verdict` say why and what to do
  verdict: string; // one sentence for the user
  reason: string | null;
  T: number[][]; // 4x4, row-major rows: takes points of the source's mesh into the target's frame
  start: "features" | "given" | "tags"; // from the shape alone, from the picked pairs, or from the tags
  tags?: TagStart | null; // null: no shared tags, or tags not looked for
  overlap: number; // 0..1: share of the smaller scan's part within 1 mm of the other scan
  rmse_mm: number | null; // RMS along the normals of what matches
  fit_mm: number | null; // robust spread of the residuals within 3 mm
  candidates: number; // starting guesses tried
  agree: number; // how many ended at the chosen pose
  spread_mm: number;
  dof: number; // directions of motion the overlap pins down (6 = all)
  same_setup: boolean;
  used_support: boolean; // the table was used for height and tilt
  support: { target: boolean; source: boolean }; // a table was found in each scan
  pairs_rms_mm: number | null; // how well the picked pairs agree with each other (a large value: two pairs do not match)
  n_matched: number;
  ms: number;
  message: string; // = verdict
  // MAINTENANCE_PLAN F11 (left out by older servers):
  sure?: boolean; // = accepted: the evidence is there. false with poses: "not sure", the poses it had in mind
  doubt?: AlignDoubt | null; // why not, in one word
  poses?: AlignPose[]; // the chosen pose first, then the others that fit best (at most 4)
  turned?: number; // starts tried turned round
  reached?: number; // starts that reached the chosen pose independently (first guesses; turned starts only when they found it from elsewhere)
  // Little shared (left out by older servers): accepted, but the two scans share little (overlap under the measured
  // limit); `caution` says what that means and what helps. Nothing else about the alignment changes; Merge stays possible.
  little_shared?: boolean;
  caution?: string | null;
};

/**
 * Why an alignment is not accepted (APP_API.md, "Sure or not sure"). "rival", "turned", "one_guess" and
 * "points": the engine had a pose in mind but is not sure of it (its `poses` hold it and the others); the
 * rest: no usable pose ("part": too little of the part besides its table; "guess": no starting guess;
 * "overlap": too little shared; "fit": the surfaces do not match closely).
 */
export type AlignDoubt = "rival" | "turned" | "one_guess" | "points" | "part" | "guess" | "overlap" | "fit";

/** One pose the engine had in mind: `T` as `AlignResult.T`, and how well the shapes fit there. */
export type AlignPose = {
  T: number[][];
  chosen: boolean; // the engine's own answer (the first)
  pick: number; // the engine's score: the share of matching surface (the smaller of the two scans' shares)
  overlap: number;
  rmse_mm: number | null;
  fit_mm: number | null;
  reached: number; // starts that ended here
  from: string[]; // which starts: "your points", "the tags", "the first guesses", "turned round"
  apart_mm: number; // how far it lies from the chosen pose (RMS over the second scan's part)
};

/** The first form of a merge: two scans, the second at the pose of an `AlignResult`. */
export type MergeTwoRequest = {
  target: string;
  source: string;
  T: number[][]; // the pose to merge at (an AlignResult's T)
  same_setup?: boolean; // default true. false: each scan's table is left out of the merged model (MergeResult.tables says how)
  name?: string; // default "<target name> + <source name>"
};

/** The second form of a merge (M4 second slice): 2..8 scans, each at its `T` from an `AlignAllResult`. */
export type MergeScansRequest = {
  scans: { id: string; T: number[][] }[];
  same_setup?: boolean;
  name?: string; // default: the scans' names joined by " + "
};

/** A merge: one form or the other, never both. */
export type MergeRequest = MergeTwoRequest | MergeScansRequest;

/**
 * What a merge left out of one scan when the part was moved (`same_setup: false`): its table, found on
 * the scan's own mesh and left out place by place ("map": the part kept down to the table, thin parts
 * lying flat kept), or, where no table could be found on the mesh, as before ("slab": everything within
 * 2 mm of the alignment's support plane, with the part's bottom edge) or nothing ("none").
 */
export type MergeTable = {
  id: string;
  name: string;
  rule: "map" | "slab" | "none";
  summary: string; // in words, the engine's
  warnings: string[]; // this scan's, as in `MergeResult.warnings`
};

/**
 * The `result` of a finished job of kind "merge". `merged_from`: the scans' ids, in order. `tables`: one
 * per scan when the part was moved (empty when it stayed); `warnings`: what to look at, each naming its
 * scan (a raised area kept that may be the table's own, a scan whose table could not be found...). Both
 * left out by older servers.
 */
export type MergeResult = {
  project_id: string;
  scan_id: string;
  scan: Scan;
  merged_from: string[];
  message: string;
  tables?: MergeTable[];
  warnings?: string[];
  /** The part was moved: what the merge's small-piece cleaning left out, and the small thin parts it kept (null otherwise). */
  cleaning?: string | null;
  /** How much the scans share at the poses they were merged at (the alignment's `overlap`, measured by the merge itself); null when it could not be measured. */
  shared?: MergeShared | null;
  /** The warnings of `shared` in one text (also in `warnings`), null when every link shares enough. */
  caution?: string | null;
};

/** `MergeResult.shared`: every pair's overlap, the links of the chain that hold the scans together and share little, the limit, and the seconds it took. */
export type MergeShared = {
  pairs: { a: string; b: string; overlap: number }[];
  little_shared: { a: string; b: string; overlap: number }[];
  limit: number;
  warnings: string[];
  s: number;
};

// ------------------------------------------------------------------ M4 second slice: several scans at once
// APP_API.md "M4 second slice: several scans at once". The first scan stays where it is; each other one is
// placed in its frame through the chain of best links that reaches it, or is not placed.

/** Picked points between two scans: `target` points on scan `a`, `source` points on scan `b`. */
export type PickedPoints = { a: string; b: string; pairs: PointPair[] };

export type AlignAllRequest = {
  scans: string[]; // 2..8 scan ids; the first stays where it is
  same_setup?: boolean; // default true, as for two scans
  picked?: PickedPoints[]; // optional rough starts for some pairs
  use_tags?: boolean; // default true: pairs without picked points start from the tags they share
};

/** How the placement used one pair: in the chain, as a check of it (a loop), left out (does not fit), or not ("": not accepted). */
export type PairUse = "chain" | "loop" | "left out" | "";

/** One pair of an `AlignAllResult`: as an `AlignResult` without `T` (b was aligned onto a), plus how it was used. */
export type AlignAllPair = Partial<Omit<AlignResult, "T">> & {
  a: string;
  b: string;
  used: PairUse;
  disagree_mm?: number; // after placing: how far this link disagrees with the poses (RMS over the part)
  accepted: boolean;
  verdict: string;
  overlap: number;
  rmse_mm: number | null;
  start: "features" | "given" | "tags";
  agree: number;
  tags?: TagStart; // present when the two scans share tags
};

/** The `result` of a finished job of kind "align_all". */
export type AlignAllResult = {
  project_id: string;
  scans: string[];
  accepted: boolean; // every scan placed
  T: Record<string, number[][] | null>; // per scan: 4x4 into the first scan's frame; null = not placed
  placed: string[];
  unplaced: string[];
  pairs: AlignAllPair[];
  loops: number; // accepted links beyond the chain
  graph: boolean; // the loops were solved together
  worst_link_mm: number;
  support: Record<string, boolean>;
  same_setup: boolean;
  ms: number;
  verdict: string;
  message: string; // = verdict
  /** Links that place scans although the two share little (named in the verdict too); left out by older servers. */
  little_shared?: { a: string; b: string }[];
  /** What sharing little means and what helps (no names); null when every link used shares enough. */
  caution?: string | null;
};

// ------------------------------------------------------------------ FEATURE_PLAN I2: a reference model and the comparison with it
// APP_API.md "References and the comparison with one". A reference (the part as it should be: STL, OBJ or PLY)
// belongs to a project; a scan keeps at most one comparison with one of them (`comparison.json`).

/** A reference model of a project, as the engine read it (in its own numbers: before any units factor). */
export type ReferenceModel = {
  id: string;
  name: string;
  format: "stl" | "obj" | "ply";
  bytes: number;
  triangles: number;
  vertices: number;
  extent_mm: [number, number, number]; // the sides of its box along its principal axes, largest first
  size_mm: number; // the diagonal of that box
  area_mm2: number;
  bounds: [[number, number, number], [number, number, number]];
  closed: boolean; // every piece closed (a solid)
  orientation: string; // what the engine did about its winding, in words
  dropped_faces: number;
  warnings: string[];
  /** The copy served for display (`.../references/{id}/mesh.ply`): decimated when the file has more triangles. */
  display: { triangles: number; decimated: boolean };
  added: string;
};

export type ReferenceList = { references: ReferenceModel[] };

/** The engine's units finding (`UnitsFinding.to_dict()`): "pending" waits for the user's answer. */
export type UnitsFinding = {
  status: "same" | "pending" | "applied" | "kept";
  ratio: number | null; // the scan's part's size over the reference's, as read
  factor: number | null; // mm per unit of the reference, when a unit fits
  unit: string | null; // "inch", "cm", "m", "µm"
  candidates: { unit: string; factor: number }[];
  reference_size_mm: number | null;
  scan_size_mm: number | null;
  message: string;
};

/** How far a best fit lies from the alignment on the datums (the scan has a part frame). */
export type VsDatums = { rms_mm: number; max_mm: number; shift_mm: number; angle_deg: number; note: string };

/** The engine's alignment record (`ReferenceAlignment.to_dict()`): `T` takes the scan's points into the reference's frame. */
export type ReferenceAlignment = {
  format_version: number;
  method: "shape" | "points" | "points+shape" | "datums" | "given";
  described: string; // "best fit on the shapes", ...
  accepted: boolean;
  verdict: string;
  T: number[][];
  pair: Record<string, unknown> | null; // the alignment engine's own numbers, when it ran
  points_rms_mm: number | null;
  n_points: number;
  frame: Record<string, unknown> | null;
  fit: { robust_mm: number; moved_mm: number | null; taken: boolean; note: string } | null;
  turn_check: Record<string, unknown> | null;
  vs_datums: VsDatums | null;
  warnings: string[];
  ms: number;
};

/** A scan's comparison with a reference, as kept and shown. */
export type Comparison = {
  format_version: number;
  reference_id: string;
  reference: ReferenceModel | null; // null when the reference is gone
  units: UnitsFinding;
  scale: number; // what the reference's numbers are multiplied by (the units factor applied, else 1)
  ready: boolean; // false while the units finding waits
  alignment: ReferenceAlignment | null;
  aligned_mesh_version: number | null;
  mesh_version: number | null;
  mesh_changed: boolean; // aligned on an earlier mesh: the pose is kept, the deviation worked out again
  /** The reference's file numbers into the scan's frame (4 x 4, for drawing it over the scan); null before it is aligned. */
  view_matrix: number[][] | null;
  tolerance_mm: number;
  k: number;
  created: string;
  modified: string;
  aligning: string | null; // the id of the alignment job running for this scan
};

export type ComparisonState = { comparison: Comparison | null; mesh_version: number | null };

export type PickedPair = { scan: [number, number, number]; reference: [number, number, number] };

export type ComparisonAlignRequest = { method: "shape" } | { method: "datums" } | { method: "points"; pairs: PickedPair[]; refine?: boolean };

/** A best fit is a job (kind "reference_align"); what needs no fitting is answered at once. */
export type ComparisonAlignAnswer = { job: Job; comparison: null } | { job: null; comparison: Comparison };

/** The `result` of a finished job of kind "reference_align". `kept` is false for an alignment the engine did not accept. */
export type ReferenceAlignResult = { alignment: ReferenceAlignment; kept: boolean; comparison: Comparison | null; mesh_version: number; message: string };

/** `deviation.json` of a comparison: the engine's `Deviation.to_dict()` with the mesh version and how the array is laid out. */
export type RefDeviationInfo = {
  version: number;
  vertices: number;
  reference_id: string;
  scale: number;
  aligned_mesh_version: number | null;
  mesh_changed: boolean;
  note: string | null;
  layout: string;
  states: string[]; // by state byte: "compared", "not measured", "not on the reference"
  method: string;
  n_vertices: number;
  n_compared: number;
  n_not_measured: number;
  n_off_reference: number;
  n_along_normal: number;
  scan_area_mm2: number | null;
  compared_area_mm2: number | null;
  off_reference_area_mm2: number | null;
  off_reference_share: number | null;
  not_measured_area_mm2: number | null;
  not_measured_share: number | null;
  reference_area_mm2: number | null;
  uncovered_share: number | null;
  uncovered_area_mm2: number | null;
  near_mm: number;
  snap_mm: number;
  normal_deg: number;
  cover_mm: number | null;
  has_sigma: boolean;
  rule: string;
  notes: string[];
};

/** Per vertex of mesh.ply: the deviation (mm, + outside the reference, NaN where not compared) and its state (0 compared, 1 not measured, 2 not on the reference). */
export type RefDeviation = { info: RefDeviationInfo; values: Float32Array; states: Uint8Array };

/** One area outside the tolerance (the engine's `DeviationRegion`), with its centre in the scan's frame and its faces. */
export type ToleranceRegion = {
  side: "above" | "below";
  area_mm2: number;
  share: number; // of the compared area
  center: [number, number, number]; // the reference's frame
  center_scan: [number, number, number]; // the scan's frame (where the viewer draws it)
  extent_mm: number;
  radius_mm: number;
  n_faces: number;
  mean_mm: number;
  worst_mm: number;
  sigma_median_mm: number | null;
  exceeds_uncertainty: boolean | null; // |mean| above k times the median sigma
  share_beyond_uncertainty: number | null;
  faces: string; // base64 uint32 face indices of mesh.ply
};

/** The coverage beside an answer: what of the scan was not compared, and what of the reference the scan did not cover. */
export type ComparisonCoverage = {
  scan_area_mm2: number | null;
  compared_area_mm2: number | null;
  off_reference_share: number | null;
  not_measured_share: number | null;
  uncovered_share: number | null;
  uncovered_area_mm2: number | null;
  reference_area_mm2: number | null;
};

/** The answer for a tolerance (the engine's `ToleranceSummary.to_dict()`, with the regions' extras and the coverage). */
export type ToleranceAnswer = {
  format_version: number;
  tolerance_mm: number;
  k: number;
  compared_area_mm2: number | null;
  share_within: number | null;
  share_above: number | null;
  share_below: number | null;
  share_uncertain: number | null; // compared area where k sigma is wider than the tolerance itself
  worst_above_mm: number | null;
  worst_below_mm: number | null;
  stats: Record<string, number | null>;
  min_area_mm2: number;
  n_regions: number;
  regions: ToleranceRegion[];
  sentence: string;
  version: number;
  mesh_changed: boolean;
  aligned_mesh_version: number | null;
  method: string;
  coverage: ComparisonCoverage;
};

// ================================================================== P2: presets by object type
/** The values a preset can name (APP_API.md "Presets"); a value it does not name is General's. */
export type PresetKey = "mode" | "quality" | "voxel_mm" | "thin_walls" | "recipe" | "check_tolerance_mm";
export type PresetValues = {
  mode?: ScanMode;
  quality?: ProcessQuality;
  voxel_mm?: number;
  thin_walls?: ThinWalls;
  recipe?: string;
  check_tolerance_mm?: number;
};
/** The hover card of a built-in preset: what it changes, why (measured), what it costs (null: nothing). */
export type PresetCard = { changes: string; why: string; costs: string | null };
export type Preset = {
  id: string;
  name: string;
  builtin: boolean;
  description: string;
  card: PresetCard | null;
  values: PresetValues;
  created?: string | null;
};
export type NewPresetBody = { name: string; description?: string; values: PresetValues };
/** What the scan entry keeps: the preset and the values changed by hand for this scan. */
export type ScanPresetKept = { id: string; name: string; hand: PresetValues };
/** `GET .../preset`: the scan's preset now. `values`: every value it resolves to (null = General's, see `general`). */
export type ScanPreset = {
  id: string;
  name: string;
  preset_values: PresetValues;
  hand: PresetValues;
  /** Values changed by hand that differ from the preset's. */
  changed: PresetKey[];
  values: { [K in PresetKey]: PresetValues[K] | null };
  /** Why the scan is made as General (made before presets, or its preset is gone), else null. */
  note: string | null;
  before_presets: boolean;
  missing: boolean;
  /** General's value of each key for this scan. */
  general: Required<PresetValues>;
  labels: Record<PresetKey, string>;
  keys: PresetKey[];
};
/** `PUT .../preset`: another preset (the hand changes start over) and / or values changed by hand (null: the preset's). */
export type ScanPresetBody = { preset_id?: string; values?: { [K in PresetKey]?: PresetValues[K] | null } };
