# OpenHandScan Studio: app API (v2, milestones 1 and 2)

Milestone 1 defined everything up to [M2: setup and calibration](#m2-setup-and-calibration), which adds to it without changing it.

This is the contract between the backend (`software/ohs/server/`, FastAPI) and the UI (`software/ui/`, React).

- Server: `http://127.0.0.1:8765`. REST is under `/api`, the WebSocket at `/api/ws/live`, and the built UI is served at `/`.
- JSON uses snake_case.
- Lengths are in **mm**, times in seconds, timestamps in ISO 8601.

**Errors:** 4xx/5xx responses carry JSON `{"detail": "what happened", "hint": "what to do"}`. `hint` is optional. Each is a full sentence in plain words (a capital, a full stop: `ohs.server.protocol.sentence`); neither carries an exception's words, an id, a route or a command line (UX_PLAN U9, guarded by `tests/test_server_error_words.py`). The cause of an unexpected error is in the log. A failed job's `error` is its refusal's detail and hint as two sentences, or, for an unexpected error, fixed words for its kind (`ohs.server.jobs.FAILURE_TEXT`).

## Types

```ts
type Emitters = { vertical: number; horizontal: number; dots: number };          // percent 0..100
type DeviceState = {
  source: "none" | "camera" | "replay" | "sim";
  connected: boolean;
  busy: string | null;                                   // e.g. "Preparing the simulator…"
  camera: { connected: boolean; info: Record<string, unknown> } | null;
  pico: { connected: boolean; firmware: string | null; emitters: Emitters } | null;
  calibration: { loaded: boolean; path: string | null; image_size: [number, number] | null;
                 baseline_mm: number | null; lasers: string[]; imu: boolean;
                 // *extra*, since 2026-11-20 (UX_PLAN U21): which calibration it is. "demo": the simulator demo's
                 // own true calibration (its recording's); "simulator": the simulator's own, made in Setup
                 // (<data_dir>/sim, used by the calibration board scenes); "recording": a replayed recording's own;
                 // "scanner": the scanner's file (paths.calibration), and always when nothing is connected
                 source: "demo" | "simulator" | "recording" | "scanner" };
  replay: { path: string; loop: boolean } | null;
};
type ScanStatus = "recording" | "recorded" | "processing" | "processed" | "failed";
type Scan = { id: string; name: string; created: string; mode: "markerless" | "markers";
              n_frames: number; duration_s: number; status: ScanStatus;
              result: { mesh_url: string; stl_url: string; laser_points_url: string | null;
                        report: Record<string, unknown> } | null };
type ProjectSummary = { id: string; name: string; created: string; modified: string;
                        n_scans: number; thumbnail_url: string | null };
type Project = ProjectSummary & { scans: Scan[] };
type SessionState = { state: "idle" | "scanning" | "paused" | "stopping";
                      project_id: string | null; scan_id: string | null;
                      mode: "markerless" | "markers"; elapsed_s: number; frames: number };
type Job = { id: string; kind: string; state: "queued" | "running" | "done" | "failed" | "cancelled";
             progress: number; message: string; result: Record<string, unknown> | null;
             error: string | null; created: string };
```

## REST

| Method and path | Body | Returns |
|---|---|---|
| `GET /api/health` | | `{app:"openhandscan", version, engine:{opencv, open3d}}`. *extra*: `api`, and `engine.compute` = `ohs.compute.describe()`: `{backend: "gpu"\|"cpu", status, device: {name, vendor, platform, kind: "discrete"\|"integrated", memory_mb, max_alloc_mb, opencl} \| null, preference, preference_from: "environment" \| "settings" \| "auto"}` (*since 2026-11-21*, UX_PLAN R20: who chose: the `OHS_COMPUTE` environment variable, the settings' `[compute] device`, or nobody, so with `backend: "cpu"` no graphics card was found). The pages say from it what this computer can do (UX_PLAN U5), in their own words ("graphics card"); `status` is the engine's and is not shown. |
| `GET /api/device` | | `DeviceState` |
| `POST /api/device/connect` | `{source:"camera"\|"replay"\|"sim", recording?:string, loop?:bool}` | `DeviceState`. For `sim`, the first time a job builds the demo recording: returns **202** with `{job: Job}`, and the device connects itself when the job finishes. |
| `POST /api/device/disconnect` | | `DeviceState` |
| `POST /api/device/emitters` | `Emitters` (partial allowed) | `DeviceState`. Ignored for replay and sim. |
| `GET /api/projects` | | `ProjectSummary[]`, newest first |
| `POST /api/projects` | `{name}` | `Project` |
| `GET /api/projects/{pid}` | | `Project` |
| `PATCH /api/projects/{pid}` | `{name}` | `Project` |
| `DELETE /api/projects/{pid}` | | `204`. The folder moves to `<projects>/.trash/` and is never hard-deleted. |
| `PATCH /api/projects/{pid}/scans/{sid}` | `{name}` | `Scan` (*since 2026-11-20*, see "Rename a scan, the trash") |
| `DELETE /api/projects/{pid}/scans/{sid}?dependents_ok=true` | | `{item: TrashItem}`. The scan's folder moves to `<projects>/.trash/` (see "Rename a scan, the trash") |
| `POST /api/open-folder` | `{what: "settings"\|"logs"\|"project", project_id?}` | `{opened: true, what}` (*since 2026-11-20*, see "Folders and the log") |
| `GET /api/log/file` | | the app's log as a text file to save (see "Folders and the log") |
| `GET /api/trash` | | `{items: TrashItem[], bytes}` |
| `POST /api/trash/{item}/restore` | | `{kind, project_id, scan_id, name, renamed}` |
| `POST /api/trash/empty` | `{confirm: true, items: string[]}` | `{removed: string[], left: string[], bytes}` |
| `GET /api/scan` | | `SessionState` |
| `POST /api/scan/start` | `{project_id, mode?, name?, preset?, preset_values?, continues?}` | `SessionState`. `409` if no device is connected, no calibration is loaded, or a scan is already running. `preset` / `preset_values`: see "Presets" (404 for an unknown preset, 422 for a value the app does not take; nothing is made then). `continues`: the scan this one is a second pass of, see "A second pass that joins the first". |
| `POST /api/scan/pause` / `resume` | | `SessionState` |
| `POST /api/scan/rewind` | `{seconds: 2.0}` | `SessionState`. Drops the last N s from the live model and records a `cut` event, which offline processing skips. |
| `POST /api/scan/stop` | | `{scan: Scan}` (the recording is finalised) |
| `POST /api/projects/{pid}/scans/{sid}/process` | `{quality:"standard"\|"best"\|"preview", fine_walls?: boolean}` (default: the scan's preset's quality, else `"standard"`; `"full"` = `"standard"`; `fine_walls`: "The thin-parts hint", since 2026-11-22) | `Job` |
| `GET /api/jobs` / `GET /api/jobs/{jid}` | | `Job[]` / `Job` |
| `POST /api/jobs/{jid}/cancel` | | `Job` |
| `GET /api/projects/{pid}/scans/{sid}/files/{name}` | | result files: `mesh.ply`, `mesh.stl`, `laser_points.ply`, `report.json` |
| `GET /api/settings` | | merged config as JSON (read-only in M1) |

## WebSocket `/api/ws/live`

**Client → server (text JSON):** `{"type":"subscribe","preview":true,"mesh":true,"max_preview_hz":15}`. Every field is optional, and preview and mesh are on by default.

**Server → client, text JSON messages:**
- `{"type":"hello","version":"0.1.0"}` on connect, followed by the current `device`, `session` and active `job` messages.
- `{"type":"status","t":…,"session":SessionState,"tracking":{"state":"idle"|"ok"|"weak"|"lost","reason":string|null,"fitness":number|null,"rmse_mm":number|null},"distance_mm":number|null,"fps":{"camera":number,"pipeline":number},"laser_points":number,"model":{"vertices":number,"triangles":number},"emitters":Emitters|null}` at about 10 Hz.
- `{"type":"pose","T_wc":[16 numbers, row-major],"ok":bool}` with each processed frame, and `{"type":"ghost","T_wc":[…]}` when tracking is lost (the last good pose).
- `{"type":"device", …DeviceState}`, `{"type":"job", …Job}`, `{"type":"log","level":"info"|"warn"|"error","message":…}` whenever they change.

**Server → client, binary messages:** a `uint32` little-endian header length, then the UTF-8 JSON header, then the payload.
- `{"type":"preview","camera":"left"|"depth","format":"jpeg","w":…,"h":…,"seq":…}`. The payload is a JPEG. `left` is the rectified left image with the laser overlay; `depth` is a colourised depth map.
- `{"type":"mesh","seq":…,"vertices":V,"triangles":T,"units":"mm","frame":"world"}`. The payload is four sections, each starting on a 4-byte boundary:
  1. `positions` Float32[V·3]
  2. `normals` Float32[V·3]
  3. `colors` Uint8[V·4] (RGBA quality colours: green = well observed, yellow = thin)
  4. `indices` Uint32[T·3]
  
  It's sent every ~1.5 s while scanning, decimated to at most 300k triangles.

**Backpressure:** the server keeps only the latest message of each binary type per client, so a slow client never stalls the scanner.

## Coordinates
- The world frame is the camera frame of the first tracked frame, with OpenCV axes (x right, y down, z forward), in mm.
- The UI converts to three.js (y up) by rotating 180° about X. It then frames the model automatically.

## Project folders
- Location: `<projects_dir>/<project_id>/` (default `~/OpenHandScan/projects`).
- Contents: `project.json`, plus `scans/<scan_id>/recording/` (the raw recording format from ARCHITECTURE.md) and `scans/<scan_id>/result/` (the output of `process_recording`).
- `project.json` holds `{id, name, created, modified, scans:[{id, name, created, mode, status}]}`.

## API deviations (backend, M1)

Everything above is implemented as written. Where the contract was silent or ambiguous, the backend does this. Fields marked *extra* are additions that a UI can ignore.

**Clarifications**
- Binary framing: the JSON header is padded with spaces so the payload starts on a 4-byte boundary *of the whole message* (`(4 + header length) % 4 == 0`), so `Float32Array`/`Uint32Array` views can be made directly on the received `ArrayBuffer`. Mesh sections follow back to back (their sizes are always multiples of 4).
- `Job.progress` is a fraction from 0 to 1. A job's final `job` message comes after its side effects (scan status updated in the store, the simulator device connected).
- `GET /api/projects` is sorted by `created`, newest first. `POST /api/projects` returns 200 with the `Project`.
- `replay.loop` defaults to `false`. A non-looping replay disconnects at its end (a running scan is stopped and saved, and a `log` message says so). The sim source always loops; its demo is a seamless back-and-forth sweep, so tracking continues across passes.
- `status.laser_points` is the running total of the live model (a rewind lowers it). `status.model` counts the live mesh before decimation.
- Every scan start first sends an empty `mesh` (`seq` 0, 0 vertices) so the UI can clear the previous model. The ghost is the last good pose; the UI hides it when a `pose` arrives with `ok: true`.
- Quality colours: the proxy is the number of tracked frames that saw each 8 mm cell of the world (not the TSDF weight, which a rewind rebuild would distort). Yellow at 4 views or fewer, green at 12 or more, blended in between.
- Pause: frames are neither recorded nor integrated, and the emitters are off. IMU samples and events are still recorded so that offline tracking can bridge the pause with the gyro. `events.csv` gets `session,pause` / `session,resume` rows.
- Rewind cuts the frame-clock interval `[t1 - seconds, t1]`, where `t1` is the newest recorded frame; paused time counts. Offline processing skips cut frames (`report.json` has `frames.cut` and `cuts`).
- `POST /api/device/emitters`: during a scan the levels are kept for the rest of it. While paused they are only stored, and the emitters stay off until Resume. Without a Pico (replay, sim, or a camera with no Pico) the call is ignored and returns the `DeviceState`.
- Status codes besides 409: 400 for an empty name or a replay without `recording`; 404 for a replay path that is not a recording; 422 for an invalid body; 503 when the camera or Pico cannot be opened or the Pico refuses a command; 403 from the local guard. The local guard refuses a `Host` other than localhost or an IP address, and refuses mutating requests or WebSockets whose `Origin` is neither this server nor the Vite dev server on port 5173.
- Pico trigger: a short press pauses or resumes a running scan, or starts a scan in the last-used project when idle. A long press (1 s or more) stops the scan.

**Extra fields**
- `hello`: `app`, `app_version`. `log`: `t`. `health`: `api`.
- `status`: `tracking.code` (machine-readable: `no_depth`, `few_inliers`, `low_overlap`, `degenerate`, `rewound`, ...), `laser_points_frame`, `latency_ms` (frame captured to result published), `process_ms` (pipeline time of the frame), `dropped_frames` (frames the live pipeline skipped; every frame is still recorded), `record_stopped` (since 2026-11-24: null, or the recorder's sentence when the recording stopped by itself because the disk could not keep up, e.g. "Recording stopped: the disk could not keep up (1.1 GB of frames were waiting to be written). The 812 frames recorded up to then are kept."; the scan then stops by itself, a `log` message of level error carries the same sentence, and the saved scan's `error` is "recording problem: " + that sentence), `record_backlog` (frames waiting to be written to disk: the recorder's queue plus unwritten PNGs; 0 when idle; the recorder logs one warning past 120), `gpu_fallback` (`null`, or `{stage: reason}` once a GPU stage failed in the running scan and went on on the CPU, e.g. `{"depth": "out of memory"}`, stages `depth` and `track_filter`; kept until the scan stops; the server also sends one `log` message with level `warn` when it first happens).
- `status.tracking.weak` (M3): what the surface can't pin down in this frame, `"slide"` and/or `"turn"` (a flat panel, a sphere's axis).
  - `degenerate` shows with a matching hint when 4 of the last 6 frames have it.
  - Sliding counts even with the gyro, which holds rotations only; turning counts only without the gyro.
  - The processing report has the share of tracked frames with each, in `frames.weak`.
- `DeviceState`: `error` (the last device failure), `warnings` (strings), `imu_stream` (bool), `camera.info.measured_fps` / `frames` / `frame_size`, `pico.imu` / `port` / `error`.
- `DeviceState.imu_temp_c` (number or null): the IMU chip's temperature in °C, rounded to 0.1. It is live from firmware 1.1+ (about once a second while the IMU streams), or as recorded for a replay or the simulator, and null when there has been no reading for 10 s. A `device` message goes out when it has moved 0.5 °C. Recordings store every reading as a `temperature` event, and a scan starts with the latest one.
- `SessionState`: `name` (the scan name) while a scan is active. The `POST /api/scan/rewind` reply also has `rewind: {t0, t1, seconds, dropped_frames, kept_frames, rebuilt_keyframes, rebuild_s}`.
- `Scan`: `thumbnail_url` (or null), `error` (after a failed processing), `quality` (of the last processing). `ProjectSummary.thumbnail_url` is the newest scan's thumbnail.
- `Job`: `project_id`, `scan_id` and `quality` for `process_scan`. `result` is `{project_id, scan_id, scan}` for `process_scan` and `{path, frames, image_size}` for `generate_demo`.
- `Job.frames` (optional): `{stage, done, total, fps, elapsed_s} | absent`, present only while a job goes through the recording's frames (the track pass, and the fuse passes of straighten and fuse, in the processing jobs).
  - `stage` is `"track"`, `"straighten"` or `"fuse"`; `done` counts frames from 1 to `total` (the frames this pass processes, after Standard's frame limit); `fps` is the rate since the first frame was done (null until the second), and `elapsed_s` the time since then. The rate leaves out loading the recording and the first frame's warm-up.
  - The last frame always goes out. From then until the pass ends (`done == total`) the mesh is made from the frames, cleaned and saved; the message then reads like "Tracking: all 412 frames done, making the mesh".
  - The field disappears when the pass ends and is never in a finished job. A job can have several passes; each starts its own count. The UI turns it into "About 3 min left for tracking" (`ui/src/lib/estimate.ts`). An engine without it simply sends no `frames`.
- `mesh` header: `decimated` (bool).
- `GET .../files/{name}` also serves `trajectory.csv`, `mesh.obj` (when exported) and `thumbnail.jpg` (the left camera view saved at stop).

---

## M2: setup and calibration

The first-run wizard and the calibration screens (see the M2 plan in [`DESKTOP_APP_PLAN.md`](DESKTOP_APP_PLAN.md#m2-plan-setup-and-calibration-wizard)). Everything here adds to the M1 contract; nothing in M1 changes. `health.api` and `hello.version` become `"0.2.0"`.

**Targets.** The wizard calibrates one *target*:
- `"device"`: the real scanner's calibration file (`paths.calibration`).
- `"sim"`: a sandbox file for the simulator (`<data_dir>/sim/calibration.json`). Calibrating the simulator never touches the real calibration, and the simulator's scan demo keeps its own true calibration.

The target is `"sim"` while the simulator is connected, and `"device"` otherwise. `data_dir` defaults to `~/.ohs/studio`.

### Types

```ts
type CalibKind = "stereo" | "lasers" | "imu" | "check";
type CalibTarget = "device" | "sim";
type SimScene = "demo" | "calib_stereo" | "calib_lasers" | "calib_imu" | "calib_check";

// DeviceState gains (M2):
//   launch_check: LaunchCheck | null   the silent drift check run after each connect
//   sim_scene: SimScene | null         what the simulator shows (null unless source is "sim")
type LaunchCheck = { state: "running" | "ok" | "drift" | "unknown"; dv_px: number | null;   // median rectified row error, full-res px
                     matches: number; message: string | null; when: string };

// ---- setup
type StepId = "connect" | "selftest" | "print" | "stereo" | "lasers" | "imu" | "check";
type SetupStep = { id: StepId; state: "done" | "todo" | "warn" | "optional"; summary: string; when: string | null };
type PartInfo = { when: string; summary: string; session: string | null };   // recorded by Studio when it saved that part
type SetupState = {
  target: CalibTarget;
  first_run: boolean;      // the target has no stereo calibration and the wizard was not dismissed
  dismissed: boolean;
  steps: SetupStep[];      // always all seven, in the StepId order above
  calibration: { path: string; exists: boolean; modified: string | null; image_size: [number, number] | null;
                 baseline_mm: number | null; stereo: boolean; lasers: string[]; imu: boolean;
                 parts: { stereo: PartInfo | null; lasers: PartInfo | null; imu: PartInfo | null };
                 backup: string | null };            // the previous version (calibration.json.bak), if any
  selftest: SelfTestResult | null;                    // the last self-test for this target
  print_check: PrintCheck | null;                     // null for the sim target (its board is virtual and exact)
  board: BoardInfo;
  launch_check: LaunchCheck | null;
  last_check: { when: string; ok: boolean; verdict: string;
                parts?: ("cameras" | "lasers" | "imu")[] } | null;   // parts: what it found off (since H3; older checks have none)
  calibration_health: CalibHealth | null;   // since H3; null without a calibration
};

// ---- calibration health (FEATURE_PLAN H3, ohs.calib.health): warns, never blocks a scan
type HealthState = "valid" | "check" | "recalibrate";
type HealthPart = "cameras" | "lasers" | "imu";
type HealthAction = { step: "check" | "stereo" | "lasers" | "imu"; text: string };   // step: a SetupStep id
type HealthReason = { state: HealthState; source: "scans" | "launch_check" | "board_check" | "board_check_age" | "scan";
                      part: HealthPart | null; reason: string; action: HealthAction | null; then: HealthAction | null;
                      kind: string; value: number | string[] | null };
type CalibHealth = {
  version: 1;
  state: HealthState;
  label: "Calibration valid" | "Check recommended" | "Recalibration required";
  part: HealthPart | null;        // null: the calibration as a whole (its age), or nothing (valid)
  part_label: "Cameras" | "Lasers" | "IMU" | null;
  reason: string;                 // one plain sentence: what was seen and what it may mean
  action: HealthAction | null;    // what to do next, and the wizard step it is (null when valid)
  then: HealthAction | null;      // what to try if that does not mend it (e.g. a camera roll reads as a vertical-laser fault)
  reasons: HealthReason[];        // every finding that is not valid, worst first (the first is the one above)
  parts: { cameras: HealthState | null; lasers: HealthState | null; imu: HealthState | null };   // null: not judged
  not_judged: HealthPart[];
  note: string | null;            // why a part could not be judged: a short scan, or one processed without a graphics card by an earlier version, at half resolution or with one of that path's settings turned off
  provisional: true;              // the limits come from the simulator (H2) until the real scanner is measured (H5)
  source: "setup" | "scan";
  scans?: { used: number; window: number; agree: number };   // setup only: scans looked at, the window (3), how many must agree (2)
  signs?: { [name: string]: { value: number | null; unit: string; check: number; recalibrate: number;
                              state: HealthState | null; missing?: boolean } };   // scan only: "row_error", "laser:<line>", "gyro_jump"
};
type BoardInfo = { squares_x: number; squares_y: number; square_mm: number; marker_mm: number;   // as printed (config [board])
                   dictionary: string; paper: "letter" | "a4"; scale: number;                    // measured print scale used for calibration (1 = exact)
                   describe: string };

type CheckStatus = "PASS" | "WARN" | "FAIL" | "SKIP" | "INFO";
type SelfTestCheck = { name: string; status: CheckStatus; detail: string; fix: string | null };   // fix: what to do, in plain words
type SelfTestResult = { overall: "PASS" | "WARN" | "FAIL"; when: string; source: "camera" | "replay" | "sim"; checks: SelfTestCheck[] };

type CameraProbe = { index: number; width: number; height: number; fps: number; fourcc: string; name: string | null;
                     stereo_like: boolean; configured: boolean; in_use: boolean };
type DevicesProbe = { cameras: CameraProbe[]; pico_port: string | null; configured_index: number; note: string | null };

type PrintFile = { name: string; kind: "board" | "tags" | "mask"; title: string; description: string;
                   url: string; preview_url: string | null; bytes: number };
type Printables = { dir: string; generated: string | null; paper: "letter" | "a4"; files: PrintFile[] };
type PrintCheck = { measured_mm: number; error_pct: number; ok: boolean; scale: number; verdict: string;
                    can_apply: boolean; when: string };

// ---- calibration session
type CalibState = "idle" | "capturing" | "armed" | "recording" | "solving" | "result" | "saved";
// capturing: stereo / lasers / check take views. armed -> recording: the IMU step (hold still, then rotate).
type CalibView = { index: number; t: number; auto: boolean; corners_left: number; corners_right: number | null;
                   distance_mm: number | null; tilt_deg: number | null;
                   lines: { vertical: number; horizontal: number } | null;     // laser points on the page margin
                   thumb_url: string; error_px: number | null; bad: boolean;   // error and bad: after a solve
                   warnings: string[] };
type CalibMetric = { label: string; value: string; ok: boolean | null; help: string };
type CalibAdvice = { text: string;
                     action: "add_views" | "remove_bad_views" | "start_over" | "recalibrate_stereo" | "reprint" | "check_board" | null };
type CalibResult = {
  kind: CalibKind; ok: boolean; grade: "excellent" | "good" | "usable" | "poor" | "failed";
  headline: string;        // e.g. "Excellent camera calibration"
  verdict: string;         // one or two plain sentences: what happened and what it means
  metrics: CalibMetric[];  // plain-language rows: "How well the corners fit 0.21 px (want < 0.5 px)" (before 2026-11-21: "Reprojection error")
  advice: CalibAdvice[];
  per_view: { index: number; error_px: number; bad: boolean }[];
  sim_truth: CalibMetric[] | null;   // simulator only: the estimate against the virtual scanner's true calibration
  can_save: boolean;                 // false for "check" and for "failed"
  report_url: string;
};
type CalibSession = {
  state: CalibState; kind: CalibKind | null; target: CalibTarget; id: string | null; started: string | null;
  auto: boolean;                     // auto-capture on (default)
  views: CalibView[]; target_views: [number, number]; min_views: number;
  guidance: string;                  // the one-line how-to for this kind
  emitters: string;                  // e.g. "Line lasers on: Class 3R, never look into the beams"
  job_id: string | null;             // the solve job
  result: CalibResult | null;
  recording: string | null;          // folder of the captured views
  saved: { path: string; backup: string | null; notes: string[] } | null;
};
type CalibHint = { text: string;                                   // "Move the board to the top-left corner"
                   region: [number, number, number, number] | null; // x0, y0, x1, y1 as fractions of the image: where to put the board
                   tilt: "left" | "right" | "up" | "down" | "more" | null;
                   distance: "closer" | "farther" | null };
type CalibLive = {
  type: "calib"; t: number; kind: CalibKind; state: CalibState;
  image_size: [number, number];      // full-resolution size of one camera image; every coordinate below is in it
  fps: number;                       // processed frames per second
  left: { corners: number[] } | null;      // flattened [u0, v0, u1, v1, ...]
  right: { corners: number[] } | null;     // stereo only
  common: number | null;                   // stereo: corners seen by both cameras
  board_found: boolean; distance_mm: number | null; tilt_deg: number | null;
  outline: number[] | null;                // board outline, 4 points
  page: number[] | null;                   // lasers / check: page outline, 4 points
  lines: { vertical: number; horizontal: number; vertical_uv: number[]; horizontal_uv: number[] } | null;
  ready: boolean;                          // a capture now would be accepted
  reason: string | null;                   // why not: "Show the whole board to both cameras", "Hold still", "Too close to view 3"
  steady: number;                          // 0..1: progress of the auto-capture hold
  auto: boolean;
  hint: CalibHint | null;
  coverage: { cols: number; rows: number; left: number[]; right: number[] | null;   // views per cell, row-major
              left_pct: number; right_pct: number | null } | null;
  diversity: { flat: number; tilt_left: number; tilt_right: number; tilt_up: number; tilt_down: number;
               near: number; mid: number; far: number } | null;                       // captured views per pose bin
  views: number; target_views: [number, number];
  imu: { phase: "armed" | "still" | "rotate" | "done"; elapsed_s: number; still_s: number; min_s: number; max_s: number;
         gyro_dps: number; board_pct: number } | null;
};
```

### REST

| Method and path | Body | Returns |
|---|---|---|
| `GET /api/setup` | | `SetupState` |
| `POST /api/setup/dismiss` | `{dismissed: bool}` | `SetupState` |
| `POST /api/setup/selftest` | | `Job` (kind `selftest`, result `SelfTestResult`). While it runs, `job.message` is the instruction for the user (e.g. "Press and release the trigger now"). |
| `GET /api/setup/devices` | | `DevicesProbe` (cameras found, the Pico's serial port) |
| `POST /api/settings/camera` | `{index?: int, swap_lr?: bool}` | `Settings`. Written to the config file; takes effect on the next connect. |
| `POST /api/settings/process` | `{thin_walls?: "auto" \| "on" \| "off"}` | `Settings`. Written to the config file (`[process] thin_walls`); the next mesh uses it. |
| `POST /api/settings/lasers` | `{lines?: string[], own_channels?: boolean, brightness?: Record<string, number>}` (at least one) | `Settings`. The build's line lasers, their wiring and each laser's brightness, written to the config file (`[lasers] lines`, `own_channels`, `brightness`). See "Line lasers" below. |
| `GET /api/setup/printables` | | `Printables` (what was generated last; `files` empty before) |
| `POST /api/setup/printables` | `{paper?: "letter" \| "a4"}` | `Job` (kind `printables`, result `Printables`). A paper change is saved to `board.paper`. |
| `GET /api/setup/printables/files/{name}` | | a generated file (PDF, PNG, Gerber); 404 for anything else |
| `POST /api/setup/printables/open` | | `{opened: bool, dir: string}`: opens the folder in the system file manager |
| `POST /api/setup/print-check` | `{measured_mm: number, apply?: bool}` | `PrintCheck` |
| `GET /api/calibration` | | `CalibSession` (`state: "idle"` when none) |
| `POST /api/calibration/start` | `{kind}` | `CalibSession` |
| `POST /api/calibration/capture` | | `{view: CalibView, session: CalibSession}`. 409 with the plain reason when the current view can't be used. |
| `DELETE /api/calibration/views/{index}` | | `CalibSession` |
| `POST /api/calibration/auto` | `{enabled: bool}` | `CalibSession` |
| `POST /api/calibration/imu/start` | | `CalibSession` (armed -> recording) |
| `POST /api/calibration/imu/stop` | | `CalibSession` (recording -> capturing, ready to solve) |
| `POST /api/calibration/solve` | | `Job` (kind `calibrate`). The session goes to `solving`, then `result`. A failed solve returns the session to `capturing` with a `result` of grade `failed` that says why. |
| `POST /api/calibration/continue` | | `CalibSession`: back to `capturing` with the views kept ("add more views") |
| `POST /api/calibration/save` | `{force?: bool}` | `{session: CalibSession, setup: SetupState}`. `force` is needed when `result.ok` is false. 409 for `check` and `failed` results. |
| `POST /api/calibration/cancel` | | `CalibSession` (idle). The captured views stay on disk. |
| `GET /api/calibration/views/{index}/thumb.jpg` | | small JPEG of the view's left image |
| `GET /api/calibration/report.json` | | the engine's full report of the last solve |

### WebSocket additions
- Text `{"type":"calib", ...CalibLive}` at about 10 Hz while a calibration session is active (latest-only).
- Text `{"type":"calib_session", ...CalibSession}` whenever the session changes. New clients get it in the seed.
- Text `{"type":"setup", ...SetupState}` whenever it changes (self-test, print check, save, launch check, connect).
- Binary `preview` with `camera` `"calib_left"` or `"calib_right"`: the calibration views, JPEG, downscaled (about 640 px wide). The `preview` subscription flag gates them like the other previews.
  - stereo: raw (unrectified) left and right images;
  - lasers / check / imu: the rectified left image only.
  
  `CalibLive` coordinates are full-resolution pixels of the image that preview shows; the UI scales them by `w / image_size[0]`.

### Rules
- **One activity at a time:** a scan and a calibration session never run together (409 either way). Calibration needs the camera or the simulator. A replay is refused with 409.
- **Preconditions:** lasers, imu and check need a stereo calibration in the target (409 with a hint otherwise). imu also needs IMU data: the Pico's IMU, or the simulator. With a camera, lasers and check need the Pico for the emitters; without it they start with a warning.
- **Device lost:** if the device disconnects during a session, the session is cancelled (the views stay on disk) and a `log` says so.
- **Trigger:**
  - stereo / lasers / check: a short press captures a view; a long press solves (once there are enough views).
  - imu: a short press starts and stops the recording.
- **Emitters:** each kind uses its profile from the config (`[emitters.profiles]` `stereo` / `lasers` / `imu` / `check`) while the session runs, and they are off afterwards.
- **Simulator:** `start` switches the simulator to that kind's virtual board (`DeviceState.sim_scene`). Cancel and save switch it back to `"demo"`. Results carry `sim_truth`.
- **Saving** merges like `ohs calibrate`:
  - A new stereo calibration drops the laser planes and the IMU calibration, and `saved.notes` says so.
  - The previous file is kept as `calibration.json.bak`.
  - *Since 2026-11-18 (calibration health, FEATURE_PLAN H1)* each save stamps the file with four optional keys: `id` (12 hex characters, new on every save, whichever step: a scan's report names the exact calibration it was processed with), `created` (ISO 8601 with the UTC offset, the time of that save), `temperature_c` (the IMU chip during the step in degC: the mean of the first and last reading of the step's recording when it has temperatures, as an IMU recording does, else the device's latest reading; null without one) and `steps`: `{stereo?, lasers?, imu?: {created, temperature_c}}`, each part as it was calibrated last. Redoing one step changes `id`, `created` and `temperature_c` and that step's entry; the other parts keep theirs, except that a new stereo calibration drops the laser planes and the IMU calibration, and their entries with them. A file from before these keys loads as before and saves without them until its next step is saved; its older parts then have no entry in `steps`. `ohs calibrate` stamps the same way, with the temperature of the step's recording only.
  - After saving to the device target, a connected camera reloads the calibration (`device` message).
- **Launch check:**
  - After each connect with a calibration, the device compares the rectified rows of left and right features on a few idle frames.
  - It stays silent unless they disagree (`launch_check.state: "drift"`). Then a `warn` log and a `DeviceState.warnings` entry suggest running the check.
  - `"unknown"` (too few features, e.g. a blank wall) is also silent.
- **Calibration health** (*since H3*, `SetupState.calibration_health`, `ohs.calib.health.status`): one of Valid / Check recommended / Recalibration required for the target's calibration, with the part, one sentence, what to do next and the wizard step it is. The app warns with it; it never blocks a scan. It joins, in this order:
  - the **last scans** made with this calibration (matched by the calibration's `id`, or by its numbers for a file without one): Studio keeps each scan's health signs after its Track stage (`setup.json`, `scan_health`, the newest 20, whatever their calibration). The newest 3 are looked at; a part moves the state only when 2 of them agree (one hard surface can read high with a right calibration). One scan reads the cameras' row error first, then the laser lines (both off alike: the cameras; one: its laser, with "redo the cameras" as what to try next), then the IMU. Scans processed without a GPU judge the cameras only, and `note` says the lasers and the IMU cannot be judged on this computer.
  - the **launch check**, when it ran after the last save and not on a replay (`setup.json` keeps `launch_source`): its `"drift"` (over 0.6 px, the alert as before) is Recalibration required for the cameras, and an `"ok"` from 0.3 px (`dv_px`) is Check recommended; both are to confirm with the 2-minute check.
  - the **2-minute check**, when it ran after the last save: failed is Recalibration required for the parts it found off (`last_check.parts`); passed (or failed on other parts only) answers an earlier launch drift and earlier row-error "check" readings. With no check nor calibration step for 14 days: Check recommended for the calibration as a whole.
  - The worst wins; on a tie the cameras first (redoing them redoes the rest). The limits are provisional (`provisional: true`) until the real scanner is measured (H5). `null` without a calibration.

## API deviations (M2)

Where the backend had to decide something the M2 contract left open, it is listed here.

Everything in the M2 section is implemented as written, with these clarifications. Fields marked *extra* are additions that a UI can ignore.

**Setup state**
- `setup.json` lives in `data_dir` (config key `paths.data_dir`). The dismissed flag is kept per target. The printables manifest is shared by both targets.
- The target is `"sim"` while the simulator is connected, in any scene, and `"device"` otherwise (also with a replay or nothing connected).
- Step rules:
  - `connect` is done once a camera (device target) or the simulator (sim target) has connected. Replays don't count. The summary reads like "Camera (2560x800) and Pico (firmware ohs-fw 1.0) connected, today".
  - `selftest` is done on PASS. It is a warning on WARN or FAIL, and its summary names the checks.
  - `print` (device) is done within ±0.3 % and a warning otherwise, even after "use my measurement".
  - `lasers` is done with both planes and a warning with only one. With another laser set (see "Line lasers"), it is done when every configured laser has a plane and a warning when some have.
  - `check` is done or a warning only when the last check is newer than the last save. The last save is Studio's part info, else the file's modification time. Otherwise the step is to do.
- `when` is the time behind the summary, or null.
- `calibration.parts.<part>` is set only for parts that are in the file and were saved by Studio. A file from `ohs calibrate` gives null.

**Print check**
- `error_pct` is `measured_mm - 100`, signed. `measured_mm` must be greater than 0 and less than 1000, else 422.
- Each check replaces the previous one:
  - within ±0.3 %: scale 1;
  - up to ±4 %: `measured / 100` with `apply: true`, else 1;
  - beyond ±4 %: always 1, and `apply` is ignored.
- The print is physical, so a print check made while the simulator is connected is stored for the device. The sim's `SetupState.print_check` stays null.
- The scale only changes the board spec used to calibrate the device (`board.scale`; `board.describe` mentions it). Capture recordings store that scaled spec as `board`, plus `board_scale`.

**Self-test**
- With nothing connected, the self-test runs on the configured camera and Pico and leaves the device disconnected.
- With the camera connected, the device is busy ("Running the self-test…") during the test and is reconnected afterwards.
- `run_selftest` has one duration for its measurement and its trigger window: 8 s. The measurement therefore also takes 8 s, and the whole test about 25 s.
- `job.message` shows the instructions, then "Camera frame rate: passed" and so on after each check.
- The simulator and replay stream checks take 3 s.
- Refused with 409 also while a self-test is running or the device is busy.
- *extra*: `SelfTestCheck.id` is the engine's check name (e.g. `camera fps`); `name` is the readable one ("Camera frame rate"). The job result also carries `message`, which the stored `setup.selftest` does not.

**Devices probe and camera settings**
- The probe opens indices 0–7 with the configured `camera.backend` and reports each camera's default mode. `name` is only available on Linux.
- The connected camera is never opened again. Its entry (`in_use: true`) comes from the running camera, with the side-by-side size.
- `pico_port` falls back to the connected Pico's port.
- `POST /api/settings/camera` returns the same object as `GET /api/settings`.
- `POST /api/settings/process` (added 2026-11-14) returns it too. `thin_walls` is the mesh model's short band behind the surface (`VALIDATION.md`, "A band that is short only behind the surface"): `"auto"` turns it on for scans without markers and leaves marker mode as it was, `"on"` and `"off"` force it. A job reads the setting when it starts, so a mesh that is being made is not affected. An engine without this route answers 404 or 405, and its `settings.process` has no `thin_walls`: the UI then hides the control.
  - Errors: 422 for any other value; 500 when the config file cannot be written.
  - Errors: 422 for an index outside 0–63; 409 during a scan or a calibration.
  - It updates the running config too and takes effect on the next connect.
  - The file written is `Settings.effective.config_path` (*extra*, along with `data_dir`, `printables_dir` and `sim_calibration`). When there is no file, the commented template is written first. Comments and other lines are kept.

**Printables**
- The paper is saved before the job starts. The job result is `Printables` plus `message`.
- Errors: 409 while a printables job runs; 422 for an unknown paper.
- A new run first removes the previous run's files (only whitelisted names).
- File names:
  - board: `charuco_board.pdf` / `.png`;
  - tags: `apriltags.pdf` / `.png` (`apriltags_p<k>.png` for several sheets);
  - mask: `projector_mask_sheet.pdf` / `.png` and `projector_mask_film.gbr` / `.svg`.
- `preview_url`:
  - board: `charuco_board.png`;
  - tags: the sheet's PNG;
  - mask: `projector_mask_preview.png`, one mask enlarged. It is served but not listed, because the 1200 dpi sheet shows nothing at screen size.
- The file route serves only those names, from the last run, with `X-Content-Type-Options: nosniff`.
- `POST .../open` creates the folder if it is missing.
- The 1200 dpi mask sheet takes about 20 s.

**Simulator scenes**
- `sim_scene` is `"demo"` for the scan demo and `"calib_<kind>"` during a calibration.
- *extra*: `DeviceState.sim_demo_ready` is true when the simulator's demo recording exists, so connecting the simulator is instant and needs no `generate_demo` job.
- A scene swap is not a reconnect: there is no "Connected" log and no launch check.
- During a calibration scene, `DeviceState.replay` is null and `DeviceState.calibration` is the sandbox calibration.
- A scene that cannot start returns 503, and the demo comes back.

**Launch check**
- It runs after every fresh connect with a calibration, including the simulator demo.
- `launch_check` is `{state: "running", dv_px: null, matches: 0, …}` until 3 idle frames, at least 0.3 s apart in frame time, are in. While a scan or a calibration takes the frames, it waits.
- `dv_px` is rounded to 0.001 px.
- `"unknown"` also covers a camera image size that differs from the calibration.
- The result is recorded for the target: `sim` for the simulator, else `device`.
- The drift warning reads "The cameras may have moved since the calibration: left/right height mismatch … (want under 0.6 px: the start-up check looks at three pictures only, so its limit is wider than the calibration check's). Run the calibration check in Setup."

**Calibration session**
- **Views:**
  - `CalibView.index` is a stable id: 0, 1, 2… in capture order, never reused. Deleting a view leaves a gap, and the next capture continues the count. Show `index + 1` as the view number: "Too close to view 3" means index 2.
  - The snapshot recording uses the same numbers for its frames. A delete rewrites `frames.csv` and `meta.json` and removes the PNG.
  - `CalibView.t` is seconds since the session's first processed frame (frame clock).
  - `CalibView.lines` (lasers / check) counts the laser points on the blank page margin, at least 5 mm clear of ink and of the paper edge: the points the laser fit uses. A view needs ≥ 15 per line.
- **Recordings:**
  - Views are written by Studio's own writer, in the recording format. `RecordingWriter` keeps `frames.csv` open and cannot drop a row.
  - The IMU uses `ohs.app.live.Recorder` and keeps the last second of IMU history (not on the simulator).
  - `recording` is null until the first capture or `imu/start`.
  - A solve writes `calib_report_<kind>.json` into the recording.
  - Cancelled recordings stay on disk, including a partial IMU recording.
- **imu:** `views` is `[]`, `target_views` is `[0, 0]` and `min_views` is 0. States run `armed` → `recording` → `capturing` (recorded, ready to solve) → `solving` → `result`.
- **State rules:**
  - Solve is allowed from `capturing` or `result`, so you can delete views and solve again.
  - Capture only in `capturing`; delete in `capturing` or `result`.
  - `continue` clears `result`: back to `capturing`, or `armed` for imu, to record again. The view errors stay until the next solve.
  - After a save, the state is `saved`, and `GET /api/calibration` shows the saved session until the next start or a cancel.
- **Auto-capture:**
  - Steady means the 90th percentile of the common corners' motion is under 1 px (both cameras for stereo) for 0.5 s.
  - New means the board centre moved more than 12 % of the width, or the distance changed more than 10 %, or the board normal turned more than 8°.
  - It stops at the upper target view count (25 / 10 / 5) with the reason "Enough views: press Finish & calibrate".
- **IMU timing:** `elapsed_s`, `still_s` (2), `min_s` (22, which is 2 s still plus 20 s of rotation) and `max_s` (62) are seconds of frame time since the first recorded frame.
  - `imu/stop` before `min_s` returns 409 "Keep rotating: N s more" (a trigger press logs the same).
  - Recording stops by itself at `max_s`, with an `info` log.
  - On the simulator, `imu/start` restarts the script and drops the one frame rendered before the restart.
- **Emitters:**
  - The kind's profile is on while capturing. The emitters are off while solving and while the result is shown, on again after `continue` or a failed solve, and off after save, cancel or a lost device.
  - `CalibSession.emitters` reads, for example, "Line lasers on: Class 3R, never look into the beams", "Lasers and dots off: room light only", "No control board (Pico or ESP32): the line lasers stay off, …" or "Simulated line lasers on".
- **More 409 rules:**
  - lasers, imu and check also need the camera's image size to match the target's stereo calibration;
  - connect is refused during a calibration, and disconnect cancels it first (the views are kept);
  - `POST /api/setup/selftest` and `POST /api/settings/camera` are refused during a calibration.
- **Device lost:** losing the camera cancels the session. A lost Pico cancels only the IMU step; the other kinds continue with a warning that the lasers are off.
- **Trigger:**
  - imu: a short press starts or stops the recording; after recording, a long press solves.
  - Refusals are sent as `warn` logs in plain words.
- **Thumbnails** are JPEGs 240 px wide.
- *extra*: `CalibLive.frame_t` is the frame time of the processed frame. `CalibLive.t` is wall time, like `status.t`.

**CalibLive**
- Coordinates are full-resolution pixels of the image that the preview shows: raw for stereo, rectified left otherwise.
- `vertical_uv` / `horizontal_uv` are thinned to at most 200 points per line, rounded to 0.1 px.
- `page` is the paper outline.
- `ready` means a manual capture would be accepted now.
- `reason` is the first unmet auto-capture condition. It can also be "View N captured: move the board to a new pose", "Calibrating…" or "Press Continue to add more views".
- `coverage.*_pct` is the percentage (0–100) of the 64 cells that hold at least one view.
- Tilt bins say where the printed face points:
  - `left`: turned toward the image's left side;
  - `up`: tipped back, with the top edge farther away;
  - `flat`: under 15°.
- Distance bins:
  - stereo: near < 300 mm ≤ mid < 400 mm ≤ far;
  - lasers / check: near < 220 mm ≤ mid < 330 mm ≤ far.
- Hints:
  - `region` boxes are thirds of the image: the centre first, then the corners, then the edges.
  - `distance` is relative to the board's current distance, and null without a board.
- `calib` messages are sent about 10 Hz while frames are processed.
- The `calib_left` / `calib_right` previews are the plain images, at most 640 px wide and about 9 Hz (less if `max_preview_hz` asks). They have no overlays: the UI draws them from `calib`.

**Solve and results**
- `job.result` is `{kind, session, result: CalibResult, message}`. *extra* job fields: `calib_kind`, `calib_session`.
- A failed flow ends the job in `failed`, with `error` = the plain reason and the hint. The session is back in `capturing` with the failed result.
- Progress messages read like "Looking at view 3 of 8". The stereo solve itself reports no progress, for up to a minute.
- The IMU flow uses at most about 600 frames (it steps through longer recordings).
- `ok` means a grade of excellent or good. `can_save` is false for `check` and `failed`. Saving anything below good needs `force`.
- Grades:
  - **stereo:**
    - excellent: rms < 0.3 px, coverage > 60 % in both cameras, row error < 0.2 px, scale error < 0.3 %, ≥ 12 views and a tilt of ≥ 20°;
    - good: rms < 0.5 px, coverage > 60 % and a passing rectification check (< 0.3 px, < 0.5 %);
    - usable: rms < 1 px, coverage > 40 % and row error < 0.5 px;
    - else poor.
    - A positive `T_x` (swapped cameras) is `failed`, with the advice to turn on "Swap left and right" (Setup, Connect) and start over.
  - **lasers:**
    - excellent: both planes fitted on the margin, median residual < 0.05 mm, ≥ 6 views;
    - good: both on the margin, < 0.15 mm;
    - usable: both, < 0.3 mm (a plane may also have needed the pattern);
    - poor: one plane only;
    - failed: none.
    - "Both" means every configured laser: with four lasers, a missing plane makes the result poor.
  - **imu:**
    - excellent: the engine's verdict is good, residual < 0.1° and the board seen in ≥ 90 % of the frames;
    - good: the engine's verdict is good;
    - usable: residual < 0.5° and < 20 % outliers;
    - else poor.
  - **check:**
    - good (excellent with wide margins) when it holds, else poor.
    - On the simulator the engine's IMU part never decides it. The simulator's check moves the board, and that part assumes a still board; a check recording has no IMU samples anyway.
- `per_view` covers the views the solver looked at.
  - `error_px`: for stereo, the larger stereo rms of the view; for lasers, the board-pose rms.
  - It is null for a view the solver rejected, which is `bad: true`.
  - Views are also bad above max(2 × median, 0.5 px) (stereo), or with fewer than half of their laser points as inliers (lasers).
- Metric values include their target, e.g. "0.21 px (want < 0.5 px)".
- `sim_truth` compares planes and `R_ci` in the raw left camera frame.
- *extra*: `sim_truth_values` holds the numbers behind `sim_truth`.
- `GET /api/calibration/report.json`:
  - the engine report of the last solve, plus a `studio` block with kind, session, target, grade and views;
  - after a failed flow, `{kind, error, recording}`;
  - 404 before any solve. It is kept after a cancel.

**Save**
- `saved.notes` is in plain words: the dropped parts, a laser plane that kept its previous value, and the backup's name.
- A new stereo calibration also clears the dropped parts' info.
- The response's `setup` is the target's state after saving.

**WebSocket**
- New clients also get `setup` in the seed.

**Line lasers** (added 2026-11-15)

The scanner's line lasers are a setting: `[lasers] lines`, default `["vertical", "horizontal"]`. A laser's name is its family, `"vertical"` or `"horizontal"` (how its line runs in the image), optionally followed by `_` and anything. The 4-laser build is `["vertical", "horizontal", "vertical_2", "horizontal_2"]`: `vertical_2` on the left of the cameras, `horizontal_2` low on the right. The emitters API stays three levels (`vertical`, `horizontal`, `dots`); with `[lasers] own_channels` (firmware 1.2.0 or later) each laser has its own Pico channel and gets its family's level times its brightness share, otherwise the two lasers of a family share one channel.

With the two standard lasers every response keeps its fields and values; the fields below are added next to them, so an app that does not know them still works.

```ts
// added fields (all optional for a client: an older engine leaves them out)
// SetupState.calibration:
//   lasers_configured: string[]            the configured lasers, in the config's order
//   lasers_missing: string[]               configured lasers without a plane in the calibration file
// CalibSession:
//   lasers: string[]                       the configured lasers (also when idle)
//   one_at_a_time: boolean                 lasers: each view also gets a frame per laser with only it lit (2026-11-16)
// CalibView (lasers / check):
//   line_points: Record<string, number | null> | null   margin points per laser; null: the line cannot be named yet
//   lines_found: Record<string, number[]> | null        per family: margin points of each line found, largest first
//   lit: string[] | null                                one at a time: the lasers that had a frame of their own
// CalibLive.lines (lasers / check):
//   points: Record<string, number | null>  per laser, as line_points
//   found: Record<string, number[]>        per family, as lines_found
//   line_uv: Record<string, number[]>      image points per named laser of a family with several lasers (flattened)
//   meet_mm: Record<string, number>        a family with two lasers: how far ahead its two lines meet, when known and within 150–450 mm
// CalibResult (lasers only):
//   planes: CalibPlaneRow[]                one row per configured laser
type CalibPlaneRow = { name: string; label: string;          // "vertical_2", "Second vertical"
                       calibrated: boolean; median_mm: number | null; p95_mm: number | null;   // distance of its points from the plane
                       views: number | null; points: number | null;
                       where: "margin" | "both" | null;      // "both": the margin alone had too few points
                       reason: string | null };              // why it was not calibrated
```

- **Setting:** `POST /api/settings/lasers` with `{lines: [...]}` is checked as the config checks it: every name starts with a family, none twice, at least one of each family. Anything else is 422 with the problem in plain words, and nothing is written. 409 during a scan or a calibration. 500 when the config file cannot be written. It returns `Settings` (its `lasers.lines` is the new list) and publishes `setup` and `calib_session`.
- **Planes are never deleted** by a change of the setting. `calibration.lasers` still lists the planes in the file; `lasers_missing` lists the configured lasers without one, and the `lasers` step says which, for example "2 of 4 lasers calibrated: calibrate the lasers again for the second vertical and the second horizontal lasers". Extra planes in the file stay there and do not count as missing.
- **Counting the lines** (lasers / check): `CalibView.lines` and `CalibLive.lines.vertical` / `horizontal` stay per family: the margin points of every line of that family. A family with one laser counts as before. A family with several lasers is split into lines (straight lines in the rectified image). When the calibration in use already has the family's planes, each point is named after the nearest plane (within 5 mm), so `points` has a number per laser and `line_uv` holds each laser's points. A first laser calibration has no planes: the lines are counted (`found`) but not named, and `points` is null for those lasers.
- **Usable view:** each laser that can be named needs ≥ 15 margin points; a family that cannot be named needs as many lines found as it has lasers, each with ≥ 15. The `reason` names the line: "The second horizontal laser line misses the white page margin (3 points, need 15)", or "Only 1 of the 2 horizontal laser lines crosses the white page margin (need 15 points on each)". With two lasers the words are as before.
- **Where two lines meet:** the two sheets of a family can meet at some distance. When that distance is inside the range of the laser views (150–450 mm), `meet_mm` reports it: on a board held there the two lines run into each other and are not measured where they do, and the capture panel says to hold the board a little nearer or farther if a line goes missing. In the 4-laser build the two vertical sheets meet about 254 mm out; the two horizontal sheets meet about 640 mm out, beyond the range, so `meet_mm` has no entry for them. `meet_mm` comes from the calibrated planes when the calibration in use has them; otherwise it is estimated from the captured views (the gap between the two lines on the board shrinks to zero there; it needs three views that measured the gap). The guide does not ask for views on both sides of that distance, and the results do not check for them: the fit finds and names the planes from views on one side (`VALIDATION.md`, "Calibration of four planes"). What the accuracy follows is the spread of distances, which the hints ask for with any set of lasers.
- **Solve and save:** the solve fits one plane per configured laser; `planes`, `metrics`, `advice` and `sim_truth` follow the configured set ("Second vertical laser: fit"). Saving replaces the fitted planes and keeps the others; `saved.notes` names each configured laser that could not be calibrated.
- **Simulator:** the virtual calibration board renders the configured lasers when the set is the two standard lasers or the 4-laser build (in any order). For any other set it shows the two standard lasers and logs a warning. The scan demo keeps its two lasers.

**Wiring and brightness** (added 2026-11-16). `Settings.lasers` also has `own_channels` (boolean, default true: each laser on its own Pico channel; false: each pair of a family wired to one channel) and `brightness` (laser name -> percent of its family's level, 0–100; a laser left out gets 100; with `own_channels` false only the first laser of a pair counts). `POST /api/settings/lasers` takes any of `lines`, `own_channels` and `brightness` (an empty body is 422); `brightness` is the whole map and replaces the old one. The result is checked as the config checks it: a `brightness` name that is not in `lines`, or a value outside 0–100, is 422 and nothing is written. A change of `lines` without `brightness` drops the brightness of the lasers no longer listed. Changing `lines` or `own_channels` is 409 during a scan or a calibration; changing only `brightness` is allowed during a scan (like the emitter sliders) and 409 during a calibration. A new `brightness` or `own_channels` reaches the connected Pico at once: lasers that are on change brightness without waiting for the next emitter change. An older engine leaves both fields out of `Settings`.

**One laser at a time** (added 2026-11-16). With four lasers, `own_channels` true and the simulator or a Pico with firmware 1.2.0 or later, the lasers step lights the lasers one at a time for each view: after the frame the guide judged (all lasers on) it lights each laser alone, waits 3 frames (1 on the simulator) and keeps the next one, then lights them all again; the board must not move by more than 2 px meanwhile, or nothing is captured ("Not captured: the board moved while the lasers took turns"). A manual `capture` waits for these frames (at most 8 s). `CalibSession.one_at_a_time` says so, its `guidance` adds "hold the board still until the view is captured", and each `CalibView.lit` lists the lasers that had a frame. The snapshot recording keeps the one-laser frames (indices from 100000) and `meta.json` `lit` (frame index -> `{laser, view}`); deleting a view deletes them too. The solve uses only those frames, fits each laser on its own, and `CalibResult.per_view` has one row per view. With older firmware the lasers are calibrated together as before, with a warning that says why.

---

## M3 phase 2: processing studio

The Process page of a scan (see the M3 plan in [`DESKTOP_APP_PLAN.md`](DESKTOP_APP_PLAN.md)). It adds to M1 and M2 and changes nothing in them.
- `POST .../process` ("Make mesh") keeps its contract. It now runs the **Default** recipe at the requested quality.
- **Quality levels** (maintenance plan F2 step 3): `"standard"` (the default) processes at full resolution at most `[process] standard_frame_rate` frames per second of scanning (30: every 2nd frame of the 60 fps camera, every frame of a 30 fps recording); `"best"` processes every frame (less noise on smooth surfaces, about twice the wait); `"preview"` as before. `"full"`, the old name, is accepted everywhere and read as `"standard"`, also in stored state and saved recipes; responses and new state say `"standard"` / `"best"`. Stage summaries name the level ("Tracked 540 of 540 frames (100 %) at standard quality"). The report's `frames` has the effective `step`, `recorded_fps` and `max_frame_rate`.
- `health.api` and `hello.version` become `"0.3.0"`.

**Model.**
- A scan's processing is five **stages**: `track`, `straighten`, `fuse`, `spacetime` (M3 phase 3, a trial build: "Refine from the images") and `refine` (M3 phase 3: "Refine with lasers"). Each ends in a new **base mesh**.
- An ordered list of **edits** applies to the base mesh. `cursor` is how many of them are active; undo and redo move it.
- The **current mesh** is the base plus the active edits. It is always written to the scan's `result/mesh.ply` (and `mesh.stl`, plus `mesh.obj` when configured), so the M1 viewer and downloads show the current version.
- Re-running a stage replaces the base and **clears the edits**: face indices no longer match. The UI confirms first.
- Files live in `scans/<sid>/process/`; their layout is the backend's choice. It records it under "API deviations (M3)".

### Types

```ts
type StageState = "none" | "running" | "done" | "failed" | "skipped";
type MeshOp = "remove_floaters" | "fill_holes" | "smooth" | "decimate" | "delete_faces" | "keep_faces"
            | "remove_table";                                       // "Remove the table" (its own section, below)
// params per op:
//   remove_floaters { min_triangles?: number; min_size_mm?: number; keep_largest_only?: boolean }
//   fill_holes      { max_hole_mm: number; watertight?: boolean }  // largest hole perimeter to fill; the outer edge too
//   smooth          { method: "taubin" | "bilateral"; strength: number /* 0..1 */; keep_edges?: boolean;
//                     within_sigma?: boolean;                      // no vertex moves more than 2 sigma
//                     flat_only?: boolean;                         // smooth flat areas only (2026-11-16)
//                     away_from_edges?: boolean }                  // or: leave sharp edges as scanned (2026-11-17)
//   decimate        { target_triangles?: number; ratio?: number;   // at most one of the two, and / or
//                     max_error_mm?: number }                      // a tolerance (0.001-10 mm)
//   delete_faces / keep_faces { faces: string }                    // base64 of little-endian uint32 face indices
//                                                                  // of the CURRENT mesh
type MeshStats = {
  triangles: number; vertices: number; components: number;
  holes: number;                      // boundary loops except the outer border (the longest loop)
  largest_hole_mm: number | null;     // perimeter of the largest hole
  border_mm: number | null;           // perimeter of the outer border
  boundary_edges: number; non_manifold_edges: number; watertight: boolean;
  bbox_mm: [number, number, number]; area_cm2: number; volume_cm3: number | null;   // volume only when watertight
  sigma_mm: SigmaStats | null;        // the confidence, when the mesh has per-vertex sigmas
  filled_cm2: number;                 // area made by hole filling (not measured)
};
type SigmaStats = {                   // each vertex's sigma (1 standard deviation, mm), shares of the surface
  median: number; p95: number;        // mm
  within_0_1: number; within_0_3: number;   // share with sigma <= 0.1 / 0.3 mm
  not_measured: number;               // share nothing measured (sigma 50 mm: filled holes)
};
type EditEntry = { id: string; op: MeshOp; params: Record<string, unknown>;
                   label: string;               // "Fill holes up to 12 mm around (about Ø 4 mm)"
                   created: string; triangles_after: number; ms: number;
                   summary?: string;                         // what it did, in plain words
                   removed?: { pieces: number; triangles: number; largest_mm: number } };  // remove_floaters (U14)
type StageInfo = { state: StageState; summary: string | null; when: string | null; job_id: string | null };
type ProcessState = {
  project_id: string; scan_id: string;
  stages: {
    track: StageInfo & { quality: "standard" | "best" | "preview" | null; tracked_pct: number | null;
                         warnings?: string[] };   // the camera path's warning (since 2026-11-20, below)
    straighten: StageInfo & { loops: number | null; correction_mm: number | null; correction_deg: number | null;
                              crossings?: number | null;     // laser crossings used (M3 phase 3)
                              tags?: number | null };        // tags that held the scan (ENGINE_PLAN X8 step 2,
                                                             // 2026-11-23); null when none did, missing before then
    fuse: StageInfo & { voxel_mm: number | null };
    spacetime: StageInfo & { source: "track" | "straighten" | "fuse" | null;   // the stage whose mesh was refined
                             frames: number | null; patches: number | null;   // frames read; patches on the surface
                             kept: number | null;                             // patches with dots in enough frames
                             covered_pct: number | null; moved_mm: number | null;  // % refined; p95 move (mm)
                             device: "gpu" | "cpu" | null };
    refine: StageInfo & { source: "track" | "straighten" | "fuse" | "spacetime" | null;   // the stage whose mesh was refined
                          points: number | null; outliers: number | null;  // laser points used / left out
                          moved_pct: number | null; moved_mm: number | null;  // % of the surface moved; p95 offset (mm)
                          residual_before_mm: number | null; residual_after_mm: number | null };  // median, mm
  };
  base: { triangles: number; vertices: number; stage: "track" | "straighten" | "fuse" | "spacetime" | "refine" } | null;
  edits: EditEntry[]; cursor: number;       // edits[0:cursor] are applied
  can_undo: boolean; can_redo: boolean;
  current: { version: number;             // increases on every change: use it to reload the mesh
             mesh_url: string;            // result/mesh.ply of this scan
             stats: MeshStats } | null;
  busy: string | null;                    // e.g. "Filling holes…" while an edit or stage job runs
  job: Job | null;                        // the running job of this scan, if any
};
type Recipe = {
  id: string; name: string; description: string; builtin: boolean;
  settings: { quality: "standard" | "best" | "preview"; straighten: "auto" | "off"; refine: "auto" | "off"; voxel_mm: number | null };
  ops: { op: Exclude<MeshOp, "delete_faces" | "keep_faces">; params: Record<string, unknown> }[];
};
type HoleOutline = { perimeter_mm: number; n_edges: number; points: number[] };   // flattened xyz, closed loop
type TrajectoryView = { raw: number[][]; straightened: number[][] | null;          // camera positions (mm), subsampled
                        loops: [number, number][] };                             // index pairs into raw
```

### REST
Paths are relative to `/api/projects/{pid}/scans/{sid}`.

| Method and path | Body | Returns |
|---|---|---|
| `GET .../process` | | `ProcessState` |
| `POST .../process/track` | `{quality}` | `Job` (kind `process_track`) |
| `POST .../process/straighten` | `{}` | `Job` (kind `process_straighten`): loop closure, then a fuse at the corrected poses |
| `POST .../process/fuse` | `{voxel_mm}` | `Job` (kind `process_fuse`) |
| `POST .../process/spacetime` | `{}` | `Job` (kind `process_spacetime`): "Refine from the images", see below |
| `POST .../process/refine` | `{}` | `Job` (kind `process_refine`): "Refine with lasers", see below |
| `POST .../process/edits` | `{op, params}` | `Job` (kind `mesh_edit`). Any redo steps after the cursor are dropped first. |
| `POST .../process/undo` | | `ProcessState` (instant for the last 10 steps) |
| `POST .../process/redo` | | `ProcessState` |
| `POST .../process/recipe` | `{recipe_id, scope: "edits" \| "all"}` | `Job`. `edits`: replace the edits with the recipe's ops. `all`: run the stages with the recipe's settings, then its ops. |
| `GET .../process/holes.json` | | `HoleOutline[]` of the current mesh |
| `GET .../process/trajectory.json` | | `TrajectoryView` |
| `GET /api/recipes` | | `Recipe[]` (built-ins first) |
| `POST /api/recipes` | `{name, description?, from_scan: {project_id, scan_id}}` or `{name, description?, settings, ops}` | `Recipe` |
| `DELETE /api/recipes/{id}` | | `204` (user recipes only; 409 for a built-in) |

Rules:
- 409 while the scan is recording, or while another stage or edit job of this scan runs.
- `straighten` and `fuse` need a finished `track`.
- Edits need a base mesh.
- A failed edit leaves the mesh as it was, with a plain reason.

### WebSocket
`{"type":"process", ...ProcessState}` whenever a scan's processing state changes (keyed per scan). Jobs keep their M1 `job` messages.

### Engine interfaces (for the agents building this)
```python
# ohs/mesh/ (new): pure functions on (V float64 (N,3), F int64 (M,3)) -> (V, F); no I/O
remove_floaters(V, F, min_triangles=None, min_size_mm=None, keep_largest_only=False)
fill_holes(V, F, max_hole_mm)                     # fair (smooth) fill, not flat fans
smooth(V, F, method="taubin"|"bilateral", strength=0.5, keep_edges=True)
decimate(V, F, target_triangles=None, ratio=None)
delete_faces(V, F, faces) / keep_faces(V, F, faces)
mesh_stats(V, F) -> dict (MeshStats)              ;  hole_outlines(V, F) -> list[dict]
sigma_stats(S, V, F) -> dict (SigmaStats) | None  ;  carry_sigma(op, V, F, S, V2, faces=None) -> S2  # ohs/mesh/confidence.py
anchor_to_points(V, F, P, params=AnchorParams()) -> (V2, delta, report)   # ohs/mesh/anchor.py: Refine with lasers
refine_from_images(V, F, left, right, T_wc, K, baseline, params=SpacetimeParams()) -> (V2, delta, report)
frames_from_recording(rec, calib, poses, max_frames=120) -> (left, right, T_wc, K, baseline)   # ohs/track/spacetime.py

# ohs/track/posegraph.py (new)
straighten_trajectory(rec_path, calib, trajectory, *, params=None, depth_params=None, progress=None,
                      cancelled=None) -> tuple[dict[int, np.ndarray], dict]
#   trajectory: [(frame_index, t_host, ok, T_wc 4x4)] as process_recording writes trajectory.csv
#   returns the corrected T_wc for every tracked frame and a report:
#   {keyframes, loop_candidates, loops_accepted, correction_mm, correction_deg, loops: [(i, j)],
#    verdict (plain words), ground_truth: {before, after} when the recording has true poses,
#    drift_scale (M3 phase 3: the drift model scaled to the loops), candidates[]: {..., large, via}}
#   large drift (M3 phase 3 item 6): a candidate ICP can't verify from the tracked pose is checked
#   again with wider bounds, from the tracked pose or a global FPFH + FGR guess (global_match)

# ohs/track/curve_ba.py (M3 phase 3): the laser lines' crossings line up the poses (research plan 7.3)
laser_curve_adjustment(rec_path, calib, trajectory, *, reference=None, params=None, progress=None,
                       cancelled=None) -> tuple[dict[int, np.ndarray], dict]
#   report: {frames, crossings, miss_mm: {before, after}, holdout: {before_mm, after_mm, ratio, by, every, fit, test,
#            ratios}, moved_mm, moved_deg, changed, reason (adjusted | no_lasers | few_crossings | not_better | off),
#            verdict, warning (since 2026-11-21: null, or when the poses changed a sentence for the user), ...}
#   CurveParams (since 2026-11-21, ENGINE_PLAN X0a, E-D2): holdout "frames" (the default since 2026-11-21, the owner's
#   decision: every holdout_every-th frame held out, each split in turn, the worst split's ratio decides; holdout gets
#   by, every, fit, test, ratios; too few test crossings (holdout_min_test): reason few_crossings) | "crossings" (the
#   default until 2026-11-21, a setting: half the crossings at random; holdout has before_mm, after_mm, ratio only),
#   enabled (False: off)

# ohs/app/process.py: process_recording(..., poses: dict[int, np.ndarray] | None = None)
#   fuses at the given poses (no tracking) when poses are passed
```

## API deviations (M3)

The backend lists here what it had to decide.

Everything in the M3 phase 2 section is implemented as written, with these clarifications. Fields marked *extra* are additions that a UI can ignore.

**Files** (`scans/<sid>/process/`)
- `state.json`: stages, base, edits, cursor, version, and the stats of every step.
- `track/`, `straighten/`, `fuse/`: each stage's `process_recording` output (`mesh.ply`, `trajectory.csv`, `report.json`, `laser_points.ply`, `marker_map.json` in marker mode). `straighten/` also holds `straighten.json`; `process/straighten.json` is the last loop-closure report, also when the stage was skipped.
- `cache/base.npz` and `cache/<edit id>.npz`: the base and the mesh after each edit within 10 steps of the cursor (V float32, F int32, vertex normals float16; about 21 MB per step at 1M triangles).
- `selections/<edit id>.npy`: the face indices of a delete / keep edit, to replay it.
- A new base also copies the stage's `report.json`, `trajectory.csv`, `laser_points.ply` and `marker_map.json` into `result/`, and updates the scan's `report` summary and `quality`.
- The current mesh in `result/` is written to a temporary file and renamed, so a download never sees half a file. `mesh.ply` carries area-weighted vertex normals and, when the mesh has them, each vertex's sigma (`quality`, see "Confidence" below).
- **A scan processed before M3** (`result/mesh.ply` but no `state.json`) is taken over on first access: its result becomes the `track` stage (done) and the base, version 1.

**ProcessState**
- `current.mesh_url` is `/api/projects/{pid}/scans/{sid}/files/mesh.ply`, the M1 file. The files route sends `Cache-Control: no-cache` and ignores a query, so `?v=<version>` can be appended.
- `current.version` starts at 1 with the first base. It goes up by 1 on every change of the current mesh: a new base, an edit, an undo, a redo, and the clearing of the edits by a recipe.
- `busy` reads "Following the camera…", "Straightening…", "Building the surface…", "Refining from the images…", "Refining with the laser lines…", "Making the mesh…", "Applying the recipe Body panel…", "Removing loose bits…", "Filling holes…", "Smoothing…", "Simplifying…", "Deleting the selection…" or "Keeping the selection…".
- `job` is the scan's running processing job, or null. A `process` message is also sent with every update of that job (at most 5 per second), so its `job.progress` is current.
- Stages:
  - `when` is when the stage last finished; `job_id` is the job that ran it (for a recipe or Make mesh, that job's id).
  - `track.tracked_pct` is 0–100. *extra*: `track.voxel_mm`, the voxel size a recipe asked for (null = the quality's default).
  - `straighten.loops` is the engine's `loops_accepted`; `correction_mm` / `correction_deg` are its largest change of a camera position / orientation, by the loops and the tags together.
  - **Tags as anchors** (since 2026-11-23; setting `[process] straighten_tags`, off; `VALIDATION.md`, "Tags as anchors on a large part"): with the setting on and AprilTags in view, Straighten also holds the scan to the tags. `straighten.tags` is then the number of tags that held it, the stage's `summary` ends with what they did ("17 tags held the scan: the end of the scan moved 7.9 mm into place.", any tag left out and why, or why they changed nothing), and the stage is fused again as after closed loops. `loops` can be 0 on a stage the tags changed. `straighten.json` gains `tags: {acted, reason, used, left_out, moved_mm, ...}`; its `reason` stays the loops' own outcome and `changed` means the poses changed, by the loops or the tags. A scan without tags in view, or with the setting off, is unchanged.
  - `fuse.voxel_mm` is the voxel size actually used.
  - A new track resets straighten, fuse and both refinements to `none`, a new straighten resets fuse and the refinements (they built on the old poses), a new fuse resets the refinements, and a new `spacetime` resets `refine`.
- A cancelled stage returns to its previous state, and its files are untouched. A failed stage is `failed` with the reason as its `summary`; the base and the edits stay.
- *Since 2026-11-21* (UX_PLAN R17, R18): a `StageInfo` written since then has *extra* `started` (ISO 8601, when it last started; null before) and `seconds` (how long it ran, 0.1 s; null while it runs and before); a stage of a scan processed earlier has neither key, so a page reads both as optional. `ProcessState` has *extra* `made: {job_id, kind, when, seconds, stages: {<stage>: seconds}} | null`: the Make mesh (or recipe on everything) that made the current base, with how long the whole job took, every stage and edit; null for a scan made before, and once a single stage on the scan replaced that base. The HTML report's "Processing time" reads it ("26 min 03 s for the whole Make mesh, every stage and edit"); without it the report says the time is the last stage's alone ("7 min 27 s for the last stage that made this mesh (the stages before it and the edits not counted)"); `Scan.result.report.wall_time_s` stays that last stage's time.
- *Since 2026-11-21* (UX_PLAN R22): Straighten skipped after closing loops that changed nothing says so: "Closed 48 loops, but there was nothing to straighten: the passes already line up (within 0.12 mm), so the scan is unchanged." (the engine's "Closed 48 loops: the passes already line up …" before). The server never has two stages `running` at once: during a Make mesh the scan's own `stages` keep their states and the run's are in `making.stages`; the page shows a stage as running only from those states, never from the job's kind or message.

**Stages**
- **The base keeps every piece.** The M1 filter that dropped pieces under 1 % of the triangles is no longer part of tracking, so removing floaters is an edit you can see and undo. The Default recipe removes the same pieces (see Recipes).
- `POST .../process/track`: `quality` defaults to `"standard"`; anything but `"standard"`, `"best"`, `"preview"` and `"full"` is 422.
- `POST .../process/straighten`: the body is optional. It runs `ohs.track.posegraph.straighten_trajectory` on the track stage's trajectory, then fuses at the corrected poses (`process_recording(poses=...)`). The stage is `skipped`, and the base and edits stay, when:
  - the engine's loop closure is missing ("Straightening is not available in this version of the engine, so the scan was left as it is") or fails ("Straightening could not finish, so the scan was left as it is"; the cause is in the log);
  - it accepts no loop;
  - its loops show that the passes already line up (report `changed: false`).

  The `summary` is then the engine's verdict, e.g. "No loops found: to let Studio straighten a scan, pass over an earlier part again.". When straightening worked, the stage is `done` and its summary is the verdict, e.g. "Closed 4 loops: the end moved 1.8 mm into place.".
  - **Laser crossings** (M3 phase 3, research plan 7.3): after the loops, Straighten lines the poses up where the laser lines of different frames cross (`ohs.track.curve_ba.laser_curve_adjustment`, with the loops' fuse, or the track's mesh without loops, as the reference surface). It changes the poses only when, fitted on part of the crossings, the ones it did not see meet clearly better (since 2026-11-21, ENGINE_PLAN E-D2: whole frames held out, every second frame, each half in turn; until then half the crossings at random, still a setting); then it fuses again. So the stage can be `done` without loops: "No loops found, but the laser lines cross: lined the scan up on 597 laser crossings (median miss 0.10 -> 0.04 mm, the camera moved by up to 1.1 mm)". With loops, that part follows the loops' verdict after "; then". *extra*: `straighten.crossings`, the crossings used (null when they changed nothing). `straighten.json` gets a `laser_curves` section with the full numbers.
    - *Since 2026-11-21* (ENGINE_PLAN X0a): no held-out test yet tells thin standing walls seen from both sides from real drift (the old test was fooled by the walls even at the true poses; the frames test refuses them but passes on walls with drift added), so when the crossings change the poses the stage's summary ends with the engine's sentence (`laser_curves.warning`: "Straighten moved the camera path by up to 1.2 mm to make the laser lines meet where they cross. On thin walls seen from both sides this can make a wall read thinner or thicker than it is, so check the thickness of thin walls another way before you rely on it."), and the stage's `report.json` (so `result/report.json` and `Scan.result.report`) has it in `warnings`, after the camera path's. Nothing new is said when the crossings change nothing.
- `POST .../process/fuse`:
  - `voxel_mm` must be 0.25–10 (else 422); null means the track's voxel size.
  - It fuses at the track stage's resolution (its quality), at the given poses, so exactly the frames the track processed.
  - The poses are the straightened ones when straighten is `done`, else the track's tracked frames.
- `POST .../process/refine` ("Refine with lasers", M3 phase 3): the body is optional; 409 without a finished Track.
  - It takes the mesh of the stage that made the base (`source`; when the base is already refined, the mesh it refined) and that stage's `laser_points.ply`, and moves the vertices along their normals by a smooth correction where the laser lines disagree with the surface (`ohs.mesh.anchor_to_points`). Laser points off the surface (more than 2 mm, or rejected by the robust fit) are `outliers`.
  - It is `skipped`, and the base and edits stay, when the scan has fewer than 200 laser points ("This scan has no laser points (the lasers were off or not calibrated), so the mesh was left as it is") or when less than 1 % of the surface would move ("The mesh already agrees with the laser lines (0.08 mm median difference over 65,643 points), so nothing was changed").
  - When it moves the mesh, it is `done`, the refined mesh becomes the base (each vertex keeps its sigma), and the summary says by how much, e.g. "Pulled 9.7 % of the surface onto 65,589 laser points, by up to 0.50 mm (95 %); the median difference fell from 0.41 to 0.07 mm". `report.json` gets a `refine` section with the full numbers.
  - A new track, straighten (when it changes the poses) or fuse resets it. Recipes run it after Straighten when `settings.refine` is `"auto"` (the default; "Quick preview" has `"off"`), so Make mesh refines too; when it fails there, the recipe goes on with the fused mesh.
- `POST .../process/spacetime` ("Refine from the images", M3 phase 3 item 5, a **trial build**): the body is optional; 409 without a finished Track.
  - It takes the mesh of the stage that made the base (`source`: when the base is refined by either refinement, the mesh before it) and up to 120 frames of the recording with a pose (the straightened poses when Straighten is `done`), rectified at full resolution, and measures the surface against them (`ohs.track.spacetime.refine_from_images`, research plan 7.2): small patches of the surface are searched along their normals for where the left and right images of every frame agree best. Where a patch holds dots in at least 16 frames and its match is clear, the vertices around it move onto the measured surface; elsewhere the mesh stays as fused.
  - It runs on the GPU when there is one (about 10 s for 36 frames on a Radeon 680M); without one the same search runs on the CPU, much more slowly.
  - It is `skipped`, and the base and edits stay, when fewer than 50 patches (or 1 % of the surface) qualify ("Too few parts of the surface hold dots in enough frames (0 of 9,875 patches), so the mesh was left as it is").
  - When it moves the mesh, it is `done`, the refined mesh becomes the base (each vertex keeps its sigma), and the summary says how much, e.g. "Measured 45 % of the surface against 36 frames (20,338 patches, on the GPU); it moved by up to 0.12 mm (95 %)". `report.json` gets a `spacetime` section with the full numbers.
  - *since 2026-11-22* (ENGINE_PLAN X6): the search on the graphics card runs in short launches, and checks itself at the end. When the check fails (the graphics driver was reset part way) the job fails with **503** "The graphics driver seems to have been reset during Refine from the images (its results changed when checked), so the mesh was left unchanged." and what to do "Run “Refine from the images” again. If it happens again, close other programs that use the graphics card."; nothing is written and the stage keeps its earlier state. `report.json`'s `spacetime` section gets `launches` (how many, the longest and their total in seconds).
  - *since 2026-11-22* (ENGINE_PLAN X6, a setting, **off**): with `[process] refine_from_images = true` in the config, Make mesh runs this stage itself, after Straighten and before Refine with lasers (not at Preview quality; without a graphics card the stage is `skipped` and says so; a failed self-check marks the stage failed and Make mesh carries on with the mesh as it was). Make mesh runs it with a guard (a patch only where the mesh turns by at most 30°, no free edge within 3 mm, no other side of a wall within 2 mm; no point moves more than 0.3 mm); the stage's state then has `guard` (what was used and what was left, by reason) and its summary names them. The mesh edit itself runs unguarded, as before; run after such a Make mesh it starts from the unrefined mesh. `ProcessState.making.plan` then has a part with `stage: "spacetime"`.
  - It resets `refine`: run it before Refine with lasers. Recipes and Make mesh don't run it (a trial build).
- **Laser points** are also checked per frame against that frame's dense depth (`ohs.vision.laser.depth_check`: within 4 sigma, at least 1 mm, at the point's pixel or 6 px to a side). The report counts the dropped ones as `lasers.n_rejected_by_depth`.
- **Calibration health** (*extra*, since 2026-11-18, FEATURE_PLAN H1): every stage's `report.json` (and so `result/report.json`) has a `health` block, what the scan saw of its calibration (`ohs.calib.scan_health`; lengths mm, angles degrees, pixels full resolution). It only measures: the mesh, the poses and the laser points are the same without it. The calibration's status reads it (H3: `health_status` below, and `SetupState.calibration_health`).
  - `version`: 1.
  - `calibration`: `{id, created, temperature_c, steps}` of the calibration the scan was processed with (null for a file from before these keys), and `source` (`"recording"`: the recording's own copy).
  - `temperature_c`: `scan` (the recording's `meta.json` summary `{first, last, min, max, n}`, or null), `calibration` (its `temperature_c`), `change` (`{min, max}`: the scan's minus the calibration's, or null).
  - `row_error`: left and right features on different rows of the rectified pair (as the launch check), measured on every 30th processed frame from the first, in a thread beside the processing: `every`, `frames`, `matches`, `median_px` and `p90_px` of the absolute row error, `signed_px` (median), and `per_frame` `[{index, matches, median_px, signed_px}]` (20 rows for 600 processed frames).
  - `laser_depth`: `sign` (in words) and `per_line` `{<laser>: {frames, points, with_depth, median_mm, abs_median_mm, p05_mm, p95_mm, rejected}}`: each laser point's depth minus the dense depth around it (the median of the depth check's five samples; + = the laser point is farther), for every point the depth check looks at, before it drops any; `with_depth` counts those with dense depth around them, `rejected` is `lasers.n_rejected_by_depth`.
  - `laser_mesh`: the laser cloud against the stage's mesh: `all` (as `LaserInfo`: `gate_mm`, `points`, `stats`) and `per_line` `{<laser>: {points, used, stray, median_mm, mean_mm, abs_p95_mm}}` (signed along the normal, + = the lasers see the surface further out).
  - `tracking`: `frames` (tracked), `rmse_mm` (the ICP residual of the tracked frames) and `gyro_jump_deg` (where the gyro gave the prior, the angle between the tracker's rotation and the gyro's; each frame's is also `per_frame[i].jump_deg`), each `{n, median, p95, max}` (`{n: 0}` when there is none, e.g. at given poses).
  - `straighten`: what Straighten had to correct, from `straighten.json`: `{reason, changed, keyframes, loop_candidates, loops_accepted, correction_mm, correction_deg, residual_mm: {before_max, after_max} | null, drift_scale, laser_curves: {changed, reason, crossings, miss_mm, moved_mm, moved_deg} | null}`, and `tags: {acted, reason, used, moved_mm}` when Straighten had a tags step (2026-11-23). Null in the track's report; the straighten and fuse stages' reports have it, and when Straighten is skipped (nothing to correct) `result/report.json` gets it.
  - `ms`: what the block cost the processing (its share on the processing thread).
  - Should the block fail, the scan is still processed and `health` is `{version, error}`.
- **The scan's calibration status** (*extra*, since H3): every stage's `report.json` with a `health` block (not a failed one) also has `health_status`, a `CalibHealth` (`source: "scan"`, with `signs`) of what this scan alone saw (`ohs.calib.health.scan_status`): Valid, Check recommended or Recalibration required, the part, one sentence and the step to redo. One scan is enough here; the setup state's status waits for 2 of the last 3. A scan processed without a GPU judges the cameras only (`note`). At given poses (the straighten and fuse stages) nothing is tracked, so the IMU is not judged there.
  - *Since 2026-11-21* (UX_PLAN R5) a stage at given poses reads its status on the path that made its poses: its `report.json` has *extra* `health_path: {tracking_model, cpu_path, from: "track"}` (the track stage's `params.compute.tracking_model` and `ohs.calib.health.cpu_path_case`), and its `health_status` is read on it. Before, such a stage's own `tracking_model` (null) made a scan processed on the graphics card say "processed without a graphics card". The refinements copy the report they refine, and so the path. A scan made without a graphics card at full resolution with the CPU path's settings has no note: its lasers and motion sensor are judged too (D7); at half resolution, from before those settings, or with one turned off, the note says why only the cameras were judged, in every stage alike. It warns only: the scan is processed as before.
  - *Since H4* the scan's summary in `project.json` (`Scan.result.report`, which the project page's scan list reads) keeps it in short: `health_status: {state: HealthState, part: HealthPart | null, reason: string}`, written with the rest of the summary when a stage gives the scan a new base. A scan whose summary was written before H4 has none (nothing is shown for it); the full status stays in `result/report.json`.
- **The camera path's warning** (*extra*, since 2026-11-20, ENGINE_PLAN X0a): the track stage's `report.json` has `path_check`, the engine's check of the tracked camera path for a jump no hand makes (`ohs.track.path_check`): `{version, window_s, limit_mm, frames, jump_mm, frame, state: "ok" | "warn" | null, warning: string | null}` (`state` null: too short to judge), and `warnings`, a list of sentences for the user (empty when there is nothing to say; with `state: "warn"` it holds the check's sentence: tracking may have slid near that frame, so a thin part can come out thinner or partly missing). It warns only: the mesh is the same without it.
  - A stage fused at given poses (straighten, fuse) takes the track stage's `path_check` and `warnings` into its own `report.json`, since its poses come from that path; the refinements keep those of the report they refine. So `result/report.json` always has the check of the path the current mesh was built from.
  - The track stage's `ProcessState.stages.track.warnings` is that list, in the app's words ("graphics card": the engine says so itself since UX_PLAN U51, and the "GPU" in the report of a scan processed before is turned into it); `[]` when there is nothing to say.
  - The scan's summary (`Scan.result.report`) keeps `warnings` (the same words) and `path_check: {state, jump_mm, frame}` in short. The report page shows them as the row "Camera path" of the calibration part, and the warnings again in its list at the top.
  - A scan processed before the check has neither key (in its summary, its `report.json` and its stage) and nothing is shown for it.
- **Scan status:** stage jobs and recipe `all` jobs set the scan to `processing`. Afterwards it is `processed` when it has a mesh, else `failed` (with `error`) or `recorded`. Edit jobs leave the status alone.
- **Make mesh** (`POST .../process`) is the Default recipe with scope `all` at the requested quality. Its job kind (`process_scan`), result `{project_id, scan_id, scan, message: "Mesh ready"}` and statuses are M1's.
  - **Built beside the result** (since 2026-11-21, UX_PLAN R2). Make mesh and a recipe with scope `all` build the new mesh in a folder of their own, `scans/<sid>/.remake-<job id>/` (its own `process/` and `result/`; the recording is the scan's), and put it in the place of the scan's `process/` and `result/` only once every stage and edit of the run has finished. Until then the scan's mesh, stages, edits and undo history, `report.json`, laser points, `ProcessState` and the scan entry in `project.json` are not touched: the viewer, the downloads and the report keep showing the earlier mesh. A cancelled or failed run removes its folder, and the scan is byte for byte as it was, its `status` and `error` too.
    - *extra* job field `mesh_kept` (kinds `process_scan`, and `process_recipe` with scope `all`): `true` while the job is queued or running and after it ended `cancelled` or `failed`, when the scan had a mesh before: that earlier mesh is still the scan's, with its stages, edits, report and laser points. `false` once the job is `done` (the new mesh took its place) and when the scan had no mesh before. The page reads it for its words after a cancel or a failure ("The earlier mesh is kept as it was.").
    - *extra* `ProcessState.making`: `{stages: {track | straighten | fuse | spacetime | refine: {state: StageState, summary: string | null, started: string | null, seconds: number | null}}, plan: {stage: "track" | "straighten" | "spacetime" | "refine" | "edits", from: number, to: number}[] | null, elapsed_s?: number, left_s?: number | null}` of the run being built, while such a job runs; else `null`. **Time left** (*since 2026-11-21*, UX_PLAN R18): `plan` is the span of the job's `progress` (0–1) each part takes, in order: the parts get their measured shares of a Make mesh's time (Follow the camera 47 %, Straighten 37 %, Refine with lasers 3 %, the edits 13 %, from three simulated scans of 180 to 240 frames: `processing.MAKE_MESH_SHARES`; the parts that do not run are left out and the rest scaled; before, Follow the camera had 72 % and Straighten 12 %). Inside Straighten, a part that does not run (a fuse without loops) is passed over, so the percentage jumps ahead, never back. `elapsed_s` is how long the job has run; `left_s` is the time left for the whole job at this pace, `elapsed_s × (1 − progress) / progress`, rounded to 1 s, null under 3 %. The page says "About N min left" from `left_s` for the whole job (not the stage's), and names the running stage from `making.stages` (the one `running`). `ProcessState.stages`, `base`, `edits` and `current` stay those of the scan's current mesh until the run takes its place (then `current.version` is higher than before, and a `process` message goes out).
    - The swap is three renames (the old `process/` and `result/` move into the run's folder, the new ones move in); a cancel arriving then is too late and the job ends `done`. Should a file of the scan be held open by another program longer than 30 s, the swap is undone and the job fails: "The new mesh could not take the place of the earlier one, because a file of this scan is open in another program. The earlier mesh is kept as it was." (hint "Close the program that has the file open, then press Make mesh again.").
    - An app that stops during a run leaves the folder; at the next start a run that had not finished is removed (the scan was never touched), a swap that stopped half way is undone, and one whose new folders were all moved in is finished (its scan entry written).
    - **Disk space:** while it runs, the scan holds two results, the earlier one and the one being built (about the size of `process/` and `result/` together). `GET .../disk` counts the run's folder in the scan's `other` until the old result goes.
    - The notices: "Making the mesh again was cancelled. The earlier mesh is kept as it was, with its edits." and "Making the mesh again failed. … The earlier mesh is kept as it was, with its edits; your recording is safe."
    - The single stages (`process/track`, `straighten`, `fuse`, `refine`, `spacetime`) still work on the scan itself, as before.
  - Second Make mesh on the same scan: 409 "This scan's mesh is already being made."
  - Any other processing job running on the scan: 409 "This scan is busy: …" (hint "Wait for it to finish, or press Cancel.").

**Jobs**
- Kinds: `process_track`, `process_straighten`, `process_fuse`, `process_spacetime`, `process_refine`, `mesh_edit`, `process_recipe` (the contract leaves the recipe kind open) and `process_scan`.
- *extra* job fields: `project_id`, `scan_id`, plus:
  - `quality` (track, Make mesh);
  - `op` (edits);
  - `voxel_mm` (fuse);
  - `recipe_id`, `recipe_name`, `scope` (recipes).
  - `mesh_kept` (Make mesh, and recipes with scope `all`; since 2026-11-21, UX_PLAN R2): whether the scan's earlier mesh is still its mesh (see Make mesh).
- Results:
  - stage jobs and recipe `all`: `{project_id, scan_id, scan, message}`, where `message` is the stage's summary;
  - `mesh_edit`: `{project_id, scan_id, edit: EditEntry, message}`;
  - recipe `edits`: `{project_id, scan_id, recipe_id, message}`.
- Progress messages read like "Following the camera: frame 1,212 of 1,321" (since 2026-11-21 with thousands separators and without the device: UX_PLAN R12), "Fill holes up to 12 mm around (about Ø 4 mm): Filling hole 3 of 40" or "Simplify to 200,000 triangles: Simplifying: 412,000 triangles".

**Edits**
- `params` in an `EditEntry` are normalised (defaults filled in). For `delete_faces` / `keep_faces` they are `{"count": n}`: the indices themselves are kept in `selections/`.
- *extra* `EditEntry` fields:
  - `summary`: what the edit did, e.g. "Removed 12 loose bits (3,410 triangles)", "Filled 3 holes (412 new triangles)", "Moved the surface by 0.012 mm on average and 0.08 mm at most", "1,204,311 -> 200,000 triangles";
  - `job_id`.
- Labels:
  - "Remove loose bits smaller than 5 mm or under 1 % of the mesh";
  - "Keep the largest piece only";
  - "Fill holes up to 12 mm around (about Ø 4 mm)" (the size is the length round the hole; the diameter is that over π, UX_PLAN U13);
  - "Smooth 50 % (keeping the shape, sharp edges kept)" (bilateral; Taubin is "evenly"; since 2026-11-21, UX_PLAN R12, the methods' names are no longer in the label; before: "Smooth (bilateral, 50 %, edges kept)");
  - "Simplify to 200,000 triangles", "Simplify to 50 % of the triangles" or "Simplify to within 0.02 mm";
  - "Delete 1,234 triangles" or "Keep 1,234 triangles".
- `remove_floaters`:
  - A piece is a set of faces connected through shared edges; the largest piece is the one with the most triangles. A lone triangle or a flap that touches the surface at one vertex only is a piece of its own (since 2026-11-13; before, pieces were connected through shared vertices and such a flap counted as surface).
  - `min_size_mm` compares the diagonal of the piece's bounding box. *extra* `min_fraction` (0–1) compares the piece's share of all triangles.
  - The given limits combine with "or".
  - `{}` means `{min_size_mm: 5}`, and `keep_largest_only` ignores the other limits.
  - The largest piece is never removed.
  - *Since 2026-11-20* (UX_PLAN U14) the edit says what it took: its `summary` is "Removed 3 loose bits, the largest 4.2 mm across (1,234 triangles)" (one bit: "Removed 1 loose bit, 4.2 mm across (12 triangles)"), and the entry has `removed: {pieces, triangles, largest_mm}` (`largest_mm`: the largest removed piece's bounding-box diagonal, 0.1 mm), absent when nothing was removed and on an edit made before. A piece under 1 % of the triangles goes however large it is, so a large `largest_mm` is the sign to look: the page offers "Show what was removed" (the mesh before the edit as a ghost over the mesh after it).
  - *Since 2026-11-21* (UX_PLAN R23) the summary names the rule that took the largest removed piece, after its size: "Removed 3 loose bits, the largest 4.2 mm across, smaller than 5 mm (1,234 triangles)" (the size limit), "Removed 297 loose bits, the largest 40.8 mm across but under 1 % of the mesh (12,345 triangles)" (the share), "… but under 100 triangles (…)" (`min_triangles`), and for Keep the largest piece only "Kept the largest piece only: removed 3 other pieces, the largest 40.8 mm across (1,234 triangles)". `removed` gets *extra* `largest_rule: "size" | "share" | "triangles" | "largest_only" | null` and `largest_triangles: number` (absent on an edit made before). The limits are tried in the label's order: size, triangles, share.
  - **The mesh before any edit** (*since 2026-11-21*, UX_PLAN R23): `GET .../process/edits/{edit_id}/before.ply` answers the mesh that edit was applied to (the step before it: the starting mesh for the first edit), as a binary little-endian PLY of vertices (`float x, y, z`) and triangles (`list uchar int vertex_indices`) only: no normals, no ± and no filled flags. It works for every edit of `ProcessState.edits`, also the ones before the cursor's neighbour and the undone ones after the cursor, at any later time; it reads the undo cache or rebuilds the step from the nearest cached one as an undo does (the last 10 steps are instant; an older one takes as long as replaying its edits). Headers: `X-OHS-Step` (the step's number: the edit's index in `edits`, 0 = the starting mesh) and `X-OHS-Mesh-Version` (the current mesh's `version`), `Cache-Control: no-cache`. 404 "This edit is no longer in the scan's history." (hint "Reload the page to see the history as it is now.") for an id the history does not have: a new Make mesh, a stage, a recipe or Free space cleared the history. It never changes the mesh. `GET .../process/edits/{edit_id}/after.ply` is the same for the mesh that edit made (`X-OHS-Step` one more). For "Show what was removed" on any edit of the history, without moving the cursor: load `before.ply` of that edit as the ghost and `after.ply` of it as the mesh beside it (or the viewer's current mesh when the edit is the last active one, `k + 1 == cursor`); what the edit took is the part of the ghost the mesh after it does not cover. The page offers it for every "Remove loose bits" entry with `removed` (and may for any edit), not only right after the edit.
- `fill_holes`:
  - `max_hole_mm` is the hole's perimeter.
  - Outer borders are left open (see MeshStats), unless `watertight` (M3 phase 3): then every opening up to `max_hole_mm` is closed, the outer edge too ("Make watertight: close every opening up to 60 mm"). The summary says whether the mesh is now watertight, or how many openings are still larger than the limit.
  - New vertices are added only where the hole is wider than the mesh around it, so pin holes get a few triangles and larger holes a smooth patch. A smooth patch that would leave the hole (more than 1.25 hole radii from its centre) becomes a flat "soap film" instead.
  - **Filled faces are flagged** (M3 phase 3): `mesh.ply` gets the face property `uchar filled` (1 = made by hole filling, not measured), the flags follow every later edit, and `MeshStats.filled_cm2` adds up their area. The viewer and the Process page draw them striped grey, and the viewer's distance tool won't put a point on them.
- `smooth`:
  - `keep_edges` defaults to true; `strength` must be 0–1.
  - Strength sets the work: Taubin `1 + 49 s` iterations; bilateral `2 + 18 s` normal and `5 + 15 s` vertex iterations.
  - `within_sigma` (M3 phase 3; until 2026-11-17 the Process page sent it by default when the mesh has sigmas and the Body panel and Bracket recipes used it; since then, the owner's decision, the page's switch is off by default and the recipes send `away_from_edges` instead): no vertex moves more than twice its own sigma; a longer move is shortened. Without sigmas it does nothing.
  - `flat_only` (2026-11-16, default false; the Process page's "Where" choice "Flat only"; no recipe uses it yet): each vertex keeps its move only where the surface around it is flat (`ohs.mesh.smoothing`, "Flat surface only"), so edges, corners and small round features stay as scanned. The label says "flat areas only".
  - `away_from_edges` (2026-11-17, default false; the "Where" choice "Off edges"; the Body panel and Bracket recipes use it): nothing within 1.5 mm of a sharp edge (over 25°) moves, and everything from 3 mm on is smoothed in full, round features too (`ohs.mesh.smoothing`, "Away from edges"). The label says "off edges" (before 2026-11-21: "away from edges"). Sent with `flat_only` it is a 422.
- `decimate`:
  - Send at most one of `target_triangles` (at least 4) or `ratio` (above 0, at most 1), and / or `max_error_mm` (0.001–10). A target at or above the current count changes nothing.
  - `max_error_mm` (M3 phase 3) simplifies only while the surface stays within that tolerance, with the outer edge and folds held; with a target too, whichever stops first.
  - The summary measures how far every old vertex now lies from the new surface: "855,246 -> 473,148 triangles; the surface moved at most 0.062 mm (0.016 mm for 99.9 % of it)".
- `delete_faces` / `keep_faces`:
  - `faces` is base64 of little-endian uint32; duplicates are ignored.
  - They are checked against the current mesh when posted. An index the mesh does not have, bad base64, a length that is not a multiple of 4, or an empty selection is 422.
  - The edits body may carry a top-level `version`: the `current.version` the selection was made on. When it is given and is not the current version, any edit is refused with 409 "the mesh changed since you selected (version N, now M)", hint "select again on the current mesh".
- **Failed and empty edits:**
  - An edit that would leave no triangle fails ("this would remove the whole mesh"), and the mesh stays as it was.
  - The redo steps are dropped only when the new edit succeeds.
  - An edit that matches nothing is still recorded, with a summary saying so, and its version counts.
- Parameter errors are 422 naming the edit and the setting in words, e.g. "Fill holes: the hole size is missing.", "Remove loose bits: the page sent a setting this edit does not have." (hint "Reload the page and try again."; the parameter's own name is in the log).
- **Repeatable edits** (`MAINTENANCE_PLAN.md` F15, the owner's decision D9; the config's `[process] repeatable_edits`, off by default):
  - On, `remove_floaters`, `fill_holes`, `smooth`, `decimate` and `remove_table` give the same result for the same mesh whatever order it lists its vertices and faces in (the GPU path lists them in another order on every run): where two choices are equally good (which diagonals close a hole, which of two equal pieces is the largest, the order of equal-cost collapses) the vertices' positions decide, not their numbers (`ohs.mesh.order`). Nothing measured changes.
  - The server adds `by_position: true` to such an edit's `params` (`EditEntry.params`) when it runs it; it is not a parameter a client sends (it is a 422 like any unknown one). Undo, redo and a step rebuilt from the cache replay an edit as it recorded it, whatever the setting is now. Off, the params are exactly as before.
  - A recipe saved from a scan (`from_scan`) leaves `by_position` out: the setting when the recipe runs decides.
  - Selections (`delete_faces`, `keep_faces`) are face numbers of one mesh and are not affected.

**Undo / redo**
- The last 10 steps around the cursor are cached on disk, with their normals, so undo and redo only load the step and write `mesh.ply` + `mesh.stl`: about 0.3 s per step at 1.3M triangles.
- A step outside the cache is rebuilt in the request by replaying the edits from the nearest cached step, which takes as long as those edits.
- Repeated calls are safe: each moves one step and answers with the new state, and past either end it is 409 "nothing to undo" / "nothing to redo" with nothing changed. Also 409 while any processing job of the scan runs.

**MeshStats**
- `components` counts the pieces with faces (faces connected through shared vertices).
- **Outer borders:**
  - Every open piece's longest boundary loop is its outer border, not a hole.
  - A *nearly closed* piece has no outer border: one whose longest loop could be capped with less than a fifth of the piece's area (perimeter² / 4π < 0.2 × area). All its loops are holes, so a part scanned all round can have its last hole filled.
  - Small loose pieces therefore never count as holes.
- `border_mm` is the longest outer border.
- Where two loops touch at one vertex, they are separated by walking around that vertex.
- A boundary chain that cannot be closed (inconsistently oriented faces) counts as a hole but is never filled.
- Rounding: perimeters and `bbox_mm` to 0.01 mm, `area_cm2` to 0.01 cm², `volume_cm3` to 0.001 cm³ (the absolute volume).

**Confidence (per-vertex sigma)**
- A mesh made by the confidence reconstruction (the default with a GPU; report `mesh.reconstruction`) gives each vertex its standard deviation in mm. `mesh.ply` carries it as the vertex property `float quality`, which MeshLab reads too; STL and OBJ have no place for it. A sigma of 50 mm means "nothing measured it", and caps the rest.
- Every step keeps it: smoothing moves the vertices, which keep their sigma; filled holes are 50 mm; deleting or keeping faces keeps the vertices left; removing floaters and simplifying take the nearest vertex's sigma from the step before. The cache stores it with each step.
- `MeshStats.sigma_mm` sums it up, area-weighted (each vertex stands for a third of its triangles' area). It is `null` for meshes without sigmas: made without a GPU, by Poisson, or before this version (run Track again to get them).
- The processing report has the same summary for the fused mesh, as `mesh.sigma_mm`.

**Recipes**
- Built-ins, in this order:
  - `default`: standard quality, straighten `auto`, then remove loose pieces under 5 mm or 1 % of the mesh and fill holes up to 5 mm. No smoothing and no simplification.
  - `body-panel`: the same, then holes up to 15 mm and bilateral smoothing at 0.5 with `keep_edges` and `away_from_edges`.
  - `bracket`: the same as `default`, then bilateral smoothing at 0.25 with `keep_edges` and `away_from_edges`.
  - `quick-preview`: preview quality, straighten `off`, remove loose pieces, simplify to 200,000 triangles.
- User recipes are `<data_dir>/recipes/<id>.json`, where the id is a slug of the name plus 4 hex digits (*extra* `created` in the file). They are listed after the built-ins, sorted by name.
- `POST /api/recipes`:
  - `name` is required (400 when empty, at most 80 characters); `description` is optional (at most 500 characters).
  - With `from_scan`:
    - `quality` is the track stage's;
    - `straighten` is `auto` when the straighten stage ran (done or skipped), else `off`;
    - `voxel_mm` is the fuse stage's when the base came from fuse, else the track's;
    - `ops` are the active edits, without the selections.
    - 409 if the scan has no mesh.
  - Otherwise `settings` and `ops` are both required (422).
  - Ops are validated like edits, at most 30. `delete_faces` / `keep_faces` are refused (422): a selection belongs to one scan.
- A recipe's `voxel_mm` is used by its track (and straighten) stage; a recipe never runs a separate fuse stage.
- `POST .../process/recipe`:
  - `edits` first clears the edits, then applies each op as an edit of its own. Undo steps back through them; the edits from before the recipe are gone. If an op fails, the ones before it stay.
  - `all` runs track at the recipe's quality, straighten when `auto`, then the ops.
  - 404 for an unknown recipe.
- `DELETE /api/recipes/{id}`: 204, 409 for a built-in, 404 when unknown.

**Overlays**
- `holes.json` (409 before there is a mesh):
  - the holes of `MeshStats.holes`: outer borders are left out;
  - the 500 largest holes, largest first;
  - `points` has `n_edges + 1` points, the first repeated at the end, so it draws as a closed line with any line primitive;
  - coordinates are rounded to 0.01 mm.
- `trajectory.json` (404 before a track):
  - `raw` is the track stage's tracked camera positions: every k-th, at most 2000, the last always included.
  - `straightened` holds the same frames after straightening, or null unless straighten is `done`.
  - `loops` are the engine's loop frame pairs, mapped to the nearest `raw` entries.
  - *extra* `frames`: the frame index of each `raw` point.

**WebSocket**
- `process` messages are latest-only per scan.
- New clients get the `process` state of every scan with a running processing job.
- `hello.version` and `health.api` are `"0.3.0"`.

**Errors besides 409:** 404 for an unknown project, scan or recipe; 422 for invalid bodies and parameters; 500 when the process folder lost its base mesh (run the stage again).

**Speed** of the mesh operations on 1.3M triangles (Ryzen 7 7735U, CPU only):

| Operation | Time |
|---|---|
| Stats | 1.3 s |
| Remove floaters | 0.3 s (about 1.1 s on 1.7 million triangles since pieces are counted by shared edges, 2026-11-13; 0.45 s before) |
| Fill holes up to 5 mm / 80 mm | 1.5 s / 3.4 s |
| Smooth: Taubin 0.5 (with `keep_edges`) | 1.8 s (3.7 s) |
| Smooth: bilateral 0.5 / 1.0 | 5.0 s / 9.1 s |
| Simplify to 200k | 9.2 s |
| Delete / keep a selection | 0.05 s |
| Undo / redo | 0.3 s |

An edit job adds about 2 s at this size: stats, normals, the cached step and the result files.

## M5 first slice: fitted shapes
The plan is in [`DESKTOP_APP_PLAN.md`](DESKTOP_APP_PLAN.md), "M5 plan, first slice". The fits work on the
scan's current mesh, the one the viewer shows (`result/mesh.ply`, `ProcessState.current.version`).

### Types
```ts
type FitKind = "plane" | "cylinder" | "hole";
type FitRequest = {
  kind: FitKind;
  face: number;                        // the clicked face of result/mesh.ply
  point: [number, number, number];     // the clicked point on it (mm, the mesh's frame)
  tolerance_mm?: number | null;        // null = chosen from the seed's measured scatter (and sigma, when it agrees)
  version?: number;                    // the mesh version the click was made on: 409 when it is no longer current
};
// All ± values are 1σ, statistical only (calibration and scale errors come on top). A plane's and a cylinder's
// count that neighbouring points err together (correlated_points below); they do not know the scan's biases.
type PlaneShape = { kind: "plane"; point: number[]; normal: number[]; rms_mm: number; flatness_mm: number; n: number;
                    normal_sigma_deg: number; offset_sigma_mm: number };
type CylinderShape = { kind: "cylinder"; point: number[]; axis: number[]; radius_mm: number; diameter_mm: number;
                       rms_mm: number; n: number; radius_sigma_mm: number; diameter_sigma_mm: number;
                       axis_sigma_deg: number; point_sigma_mm: number };
type CircleShape = { kind: "circle"; center: number[]; normal: number[]; radius_mm: number; diameter_mm: number;
                     rms_mm: number; n: number; radius_sigma_mm: number; diameter_sigma_mm: number; center_sigma_mm: number };
type FitResult = {
  kind: FitKind;                        // as asked; a hole's shape is a circle
  shape: PlaneShape | CylinderShape | CircleShape;
  faces: string;                        // the region: base64 of little-endian uint32 face indices, sorted
  n_faces: number;
  tolerance_mm: number | null;          // the tolerance used (null for a hole)
  mean_sigma_mm: number | null;         // mean sigma of the fitted points (null when the mesh has none)
  correlated_points: number | null;     // plane, cylinder: how many points err together with each one (1 =
                                        // independent); the shape's ± are already widened by its square root.
                                        // null for a hole (its circle is not widened)
  warnings: string[];                   // plain language, e.g. "small region: 23 points"
  version: number;                      // the mesh version it was fitted on
};
```

### REST
Paths are relative to `/api/projects/{pid}/scans/{sid}`.

| Method and path | Body | Returns |
|---|---|---|
| `POST .../fit` | `FitRequest` | `FitResult` |
| `GET .../wall` | none | `{available: boolean, why: string \| null}`: whether the wall-thickness reading is offered for the scan (since 2026-11-26, ENGINE_PLAN X16) |
| `POST .../wall` | `{face: number, point: [x, y, z], version?: number}` | `WallReading` (below); 409 when the mesh changed; 422 with a plain `detail` and a `hint` when not offered or refused |
| `GET .../hole-images` | none | `{available: boolean, why: string \| null}`: whether the hole reading from the camera images is offered for the scan (since 2026-11-26, ENGINE_PLAN X15 step 2) |
| `POST .../hole-images` | `{face: number, point: [x, y, z], version?: number}` | `HoleImagesReading` (below); 409 when the mesh changed; 422 with a plain `detail` and a `hint` when not offered or refused |

Rules:
- 409 before the scan has a mesh (as `holes.json`).
- 422 for an unknown kind, a face outside the mesh, or when no such shape is found there; the
  detail says why and the hint what to try (e.g. "click on the wall of the bore and choose
  Cylinder").
- 409 when `version` is given and the mesh has changed since (reload it and click again).
- A hole is always a circle; a click on a scanned bore wall answers 422 with the hint to use Cylinder.
- Face indices are those of `result/mesh.ply` (polygon faces as in the file, like `delete_faces`).
- Read-only and quick (well under a second on a 1M-triangle mesh); it does not wait for a running
  edit job of the scan: it uses the current version and says which in `version`.

## Kept features: fits and measurements saved with the scan
[`FEATURE_PLAN.md`](FEATURE_PLAN.md), K1 and K2. Fits, two-point measurements and relations are kept in
`scans/<sid>/features.json` (`ohs.measure.features`, `format_version` 1), each fit's region in
`scans/<sid>/features/<id>.npy` (sorted uint32 face indices, valid on the mesh version it was fitted on).
They have names the user can change, and come back when the viewer opens the scan again. Each fit also
carries where its region lies on its shape (its extent, `ohs.measure.region_extent`); a plane's outline
loops are kept beside the region in `scans/<sid>/features/<id>.outline.npz`. The relations shown are the
engine's (`ohs.measure.relate`), worked out when shown.

### Types
```ts
type FeatureSeed = { kind: FitKind; face: number;          // the click the fit was grown from: enough to fit
                     point: [number, number, number];      // it again (face: on the mesh version it was fitted on)
                     tolerance_mm: number | null };        // as asked (null = chosen from the seed's scatter)
type KeptFeature = {
  id: string;                           // "f-…": letters, digits, "-" and "_"
  name: string;                         // "Fit N" unless the user named it; 1 to 80 characters
  kind: FitKind;
  shape: PlaneShape | CylinderShape | CircleShape;   // exactly as POST .../fit returned it, with its ± values
  seed: FeatureSeed;
  n_faces: number; tolerance_mm: number | null; mean_sigma_mm: number | null;
  correlated_points: number | null; warnings: string[];  // as in FitResult
  mesh_version: number;                 // the mesh version it was fitted on
  extent: FitExtent | null;             // where its region lies; null: kept before extents existed ("fit again")
  form?: "boss" | "bore" | null;        // a cylinder: boss or bore (below); null for a plane or a hole, or when not
                                        // clear; left out for a fit kept before forms existed
  nominals: ApprovedNominal[];          // approved nominal values ("Nominal values" below); in GET .../features and
                                        // the refit answer only (the other answers keep their keys)
  created: string;
  stale: boolean;                       // fitted on another mesh version than the current one ("fitted on an earlier mesh")
};
type KeptMeasurement = { id: string;    // "m-…"
                         name: string;  // "Distance N" unless the user named it
                         a: [number, number, number]; b: [number, number, number];
                         mesh_version: number | null; created: string; stale: boolean };
// Where a fit's region lies on its shape, in mm, the scan's frame. Only this small part is kept in features.json
// (it is read and sent on every GET .../features); a plane's outline loops are served by .../outline.
type PlaneExtent = { kind: "plane";
                     origin: number[]; u: number[]; v: number[];   // the plane's 2D basis: origin + x u + y v
                     rectangle: { center: number[];                 // the smallest rectangle round the outline
                                  axes: number[][];                 // its two sides' directions, the longer first
                                  half_sizes_mm: [number, number] };
                     area_mm2: number;                 // the outline's area (its outer loops' less its holes')
                     n_outer: number; n_holes: number; // loops of the outline (specks of a few faces left out)
                     n_points: number };               // points in those loops
type CylinderExtent = { kind: "cylinder";
                        along_mm: [number, number];    // the region along the axis, from the shape's point
                        length_mm: number;
                        zero: number[];                // unit, square to the axis: angles are taken from it,
                        start_deg: number;             // turning right-handed about the axis; the region covers
                        arc_deg: number };             // start_deg through start_deg + arc_deg (360: all round)
type HoleExtent = { kind: "hole"; n_edge: number;      // points on the hole's edge
                    edge: number[][] | null };         // the edge in order round it, when at most 128 points; else null
type FitExtent = PlaneExtent | CylinderExtent | HoleExtent;
type FeatureOutline =                                  // GET .../features/{id}/outline
  | { id: string; mesh_version: number; kind: "plane"; origin: number[]; u: number[]; v: number[];
      outer: number[][][];                             // outer loops, counter-clockwise seen from the normal's side
      holes: number[][][] }                            // loops inside them, clockwise; 2D points [x, y] in the basis
  | { id: string; mesh_version: number; kind: "hole"; edge: number[][] };   // 3D points
// What applies between two fits: ohs.measure.relate's Relation.to_dict(). Each value's sigma is its 1σ from both
// fits' ± (statistical only; calibration, scale and errors a whole region shares come on top); null where the fits
// give none (an angle with a hole's axis: a hole's direction has no ±).
type RelationValue = { name: "angle" | "distance" | "centre_distance"; label: string;   // "Angle (parallel)", ...
                       value: number; sigma: number | null; unit: "deg" | "mm" };
type RelationRecord = { kinds: [FitKind, FitKind]; parallel: boolean; values: RelationValue[];
                        notes: string[] };             // why a distance is missing, in plain words
type KeptRelation = { id: string;       // "r-…"
                      name: string;     // "Relation N" unless the user named it
                      a: string; b: string;   // two kept fits; the numbers are worked out when shown, not stored
                      created: string;
                      stale: boolean;   // one of the two fits was fitted on another mesh version than the current one
                      relation: RelationRecord | null;   // the engine's values for the two kept shapes
                      error?: string;   // only when relation is null: why the engine could not relate them
                      nominals: ApprovedNominal[];       // as a fit's (GET .../features and the refit answer)
                      move?: RelationMove };             // one of its fits moves in the CAD export ("Nominal values"); left out: listed only
type PairRelation = { a: string; b: string; stale: boolean; relation: RelationRecord | null; error?: string };
type FeatureSet = {
  format_version: 1;
  frame: PartFrameView | null;          // the part's coordinate system ("The part's frame" below)
  mesh_version: number | null;          // the current mesh's version (null before there is a mesh)
  features: KeptFeature[]; measurements: KeptMeasurement[]; relations: KeptRelation[];   // oldest first
};
type NewFeature = FitRequest & { name?: string };
type KeptFit = KeptFeature & { faces: string };     // the region, base64 as in FitResult
type NewMeasurement = { a: [number, number, number]; b: [number, number, number]; version?: number; name?: string };
type NewRelation = { a: string; b: string; name?: string };
type FeatureFaces = { id: string; mesh_version: number; n_faces: number; faces: string };
type RefitResult = { features: FeatureSet; refitted: string[];
                     failed: { id: string; detail: string; hint: string | null }[] };
```
In `features.json` the records are the same without `stale` (and `FeatureSet` without `mesh_version`); a
relation keeps only `id`, `name`, `a`, `b` and `created`. A fit or relation with approved nominal values
keeps them under `nominals` as approved, without the `measured_now`, `sigma_now`, `check_again` and
`check_reason` that reading adds; one without any has no `nominals` key, as before. A relation whose move
was chosen keeps `move` as shown; one listed only has no `move` key, as before.

### REST
Paths are relative to `/api/projects/{pid}/scans/{sid}`.

| Method and path | Body | Returns |
|---|---|---|
| `GET .../features` | | `FeatureSet` |
| `POST .../features` | `NewFeature` | `KeptFit` |
| `POST .../features/measurements` | `NewMeasurement` | `KeptMeasurement` |
| `POST .../features/relations` | `NewRelation` | `KeptRelation` |
| `PATCH .../features/{id}` | `{name}` | the renamed `KeptFeature`, `KeptMeasurement` or `KeptRelation` |
| `DELETE .../features/{id}` | | 204 |
| `GET .../features/{id}/faces` | | `FeatureFaces` |
| `GET .../features/{id}/outline` | | `FeatureOutline` |
| `GET .../features/relate?a={id}&b={id}` | | `PairRelation` |
| `POST .../features/refit` | `{ids?: string[]}` | `RefitResult` |

Rules:
- `GET` reads only: a scan without kept features answers an empty set (and writes nothing).
- **The hole from the camera images** (since 2026-11-26; `[process] hole_from_images`, on; `VALIDATION.md`, "Holes
  from the camera images in the app"): the diameter of a hole's entrance rim in the clicked face's plane (not the
  bore below, not a chamfer's outer edge), read from up to 20 of the scan's camera frames at a click on the face
  beside the hole (`ohs.measure.rim.read_hole`). `HoleImagesReading` is `{available: true, diameter_mm, pm_mm
  (1 sigma), centre, normal, frames_used, frames_tried, cover_deg, warnings: string[], words: string[], seconds,
  version}`. Show `words` as they come. The diameter does not depend on the camera path; the centre does, and the
  words say so when the path check warns or the frames place the centre apart. It is read-only and takes 5 to
  15 s; one reading runs at a time, sharing the wall reading's lock and frame provider, and the frames are kept
  for the next click. A refusal is a 422 whose `detail` is the engine's words and whose `hint` says what to do,
  with `refused: true, frames_used, frames_tried, seconds` and never a diameter. Not offered (`available: false`
  with `why`; a POST then answers 422): the setting off, a merged scan, a scan tracked by markers, no graphics
  card, a scan made at Preview quality, a recording under 1280 px wide, or the recording, the calibration, the
  depth range or the camera path gone. The viewer asks for it after every Hole click, also when the mesh's own
  fit answered 422.
- **The wall-thickness reading** (since 2026-11-26; `[process] wall_reading`, on; `VALIDATION.md`, "The
  wall-thickness reading in the app"): a thin wall's thickness read from the scan's camera frames at a click on
  one of its faces. `WallReading` is `{available: true, at_most_mm, reading_mm, pm_mm, mesh_mm, frames: [a, b],
  points: [a, b], tilt_deg, seconds, words: string[], timings_s, version}`. Show `words` and `at_most_mm`; they
  are one-sided on purpose ("At most 1.63 mm thick here ...", "The scan cannot rule out that the wall is up to
  about 1 mm thinner ..."). Never show `reading_mm ± pm_mm`: the reading's own scatter does not hold the camera
  path's error. It is read-only, takes 10 to 20 s, and one reading runs at a time. Not offered (`available:
  false` with `why`; a POST then answers 422): the setting off, no graphics card, a scan tracked by markers, a
  merged scan, or the recording, the calibration, the depth range or the camera path gone. Refused at a click
  (422): no wall seen from both sides, one side seen by too few frames or points, the two sides more than 5
  degrees from parallel, a side's points scattering more than 0.7 mm, a wall thicker than about 3 mm.
- `POST .../features` fits exactly as `POST .../fit` (the same 409 and 422 answers) and keeps the
  answer, the click, the mesh version and the extent in one step. `POST .../fit` itself answers as
  before, without the extent.
- **Extent:** `features.json` keeps only its small part (`FitExtent`): a scan's plane outline can be
  tens of thousands of points in hundreds of loops (a plane on a simulated scan of a door on a table:
  61,403 points in 750 loops, 2.5 MB as JSON, against about 1.5 kB for the whole kept feature). A
  plane's loops, and a hole's edge of more than 128 points, are in `features/<id>.outline.npz` (each
  loop's points one after the other and each loop's length, so the loops stay apart; 1 MB there),
  valid on the fit's `mesh_version`, written and removed with the region file. `GET
  .../features/{id}/outline` serves them as plain JSON; 404 for a cylinder (its span and arc are all
  of its extent) and for a fit without an extent.
- **Form:** a kept cylinder says whether it is a `"boss"` (its surface faces away from its axis:
  material inside, as a shaft or a pin) or a `"bore"` (a hole's wall, the surface faces its axis).
  The engine decides it from the region itself (`ohs.measure.region.cylinder_form`): each face's
  normal against the direction from the axis to it, weighed by the face's area; 80 % of the area must
  agree, else `form` is null and the fit carries a warning that says so ("whether this cylinder is a
  boss or a bore is not clear: …"). A plane or a hole has `form` null. It is set when a fit is kept
  and when it is fitted again; a fit kept before forms existed has no `form` and loads as it is (it
  is not fitted again for it). `features.json` may hold only `"boss"`, `"bore"` or null, and null for
  a plane or a hole. The suggested nominal sizes use it (`ohs.cad.nominal.suggest_fit` reads it from
  the kept record): bosses and bores are biased differently. `POST .../fit` itself answers as before,
  without `form`.
- A measurement's `version` is the mesh it was picked on (the current one when left out).
- A relation needs two different kept fits (422 otherwise). Deleting a fit deletes its relations,
  its region file and its outline file.
- **Relations:** every kept relation shown (in `GET .../features`, and the answers of `POST
  .../features/relations` and of a rename) carries `relation`, `ohs.measure.relate` on the two kept
  shapes, and `stale` when one of them was fitted on another mesh version than the current one (its
  numbers are then that mesh's). `GET .../features/relate?a=&b=` answers the same for any two kept fits
  without keeping anything: 404 for an id that names no kept fit, 422 for one fit twice or a missing
  `a` or `b`.
- **Stale:** a fit or measurement made on another mesh version than the current one (after an edit,
  undo / redo, or the mesh made again) is marked `stale`. Nothing is refitted on its own.
- **Fit again** (`refit`) fits the stale fits (all, or those in `ids`) again on the current mesh from
  their seeds: face numbers change with an edit, so the seed's face is found again as the measured
  face nearest the saved point. When that face is farther from the point than 2 mm and 1.5 of its
  own longest edge, the surface under the fit is gone (its region was cut away) and the fit is not
  fitted again on another surface. A refitted fit keeps its id, name and click; its shape, counts,
  warnings, region, extent, outline and `mesh_version` are the new mesh's. One that cannot be fitted
  again stays as it was, still marked, and is listed in `failed` with the fit's detail and hint. A fit
  kept before extents existed (`extent` null) is fitted again too: when it is not stale, from its own
  clicked face on its own mesh, so its shape stays the same and it gains its extent. 409 before there
  is a mesh.
- A region file (`faces`) and an outline are for the `mesh_version` they state: draw them only on that mesh.
- 404 for an unknown project, scan or id; 422 for invalid bodies (a name that is empty or longer than
  80 characters, a point that is not three numbers); 500 when `features.json` cannot be read (the
  file is left as it is).

### The part's frame
[`FEATURE_PLAN.md`](FEATURE_PLAN.md), K3. The part's own coordinate system, built from three kept fits by
the 3-2-1 rule (`ohs.measure.frame.part_frame`; its module docstring has every rule and refusal) and
kept under `frame` in `features.json` as `PartFrame.to_dict()`. Until one is built nothing changes:
`frame` is null, and the scan's files and downloads stay in the scan's frame.

```ts
type AxisName = "X" | "Y" | "Z";
type FrameSlot = { role: "primary" | "secondary" | "origin";
                   id: string | null;           // the kept fit's id; null for a point given as the origin
                   name: string | null;         // the fit's name when the frame was built
                   kind: "plane" | "cylinder" | "circle" | "point";
                   sets: string;                // in words, e.g. "the Z axis (its normal) and Z = 0 (the plane)"
                   fit: object };               // the shape it was built from, as it was then
type FrameSigma = { angle_deg: number | null;   // 1σ of the whole rotation (RMS); null where a fit has no ±
                    tilt_deg: number | null; turn_deg: number | null;   // about the in-plane axes / the first axis
                    origin_mm: number | null;   // 1σ of the origin's place (RMS)
                    origin_axes_mm: (number | null)[] };                // per part axis X, Y, Z
type PartFrameRecord = {                        // what features.json keeps under "frame"
  format_version: 1; units: "mm";
  matrix: number[][];                           // 4x4: x_part = R x_scan + t; R's rows are the part's X, Y, Z in the scan's frame
  axes: { primary: AxisName; secondary: AxisName; third: AxisName };
  flip_primary: boolean; flip_secondary: boolean; secondary_zero: boolean;
  built_from: FrameSlot[];                      // primary, secondary, origin
  sigma: FrameSigma; warnings: string[];
};
type PartFrameView = PartFrameRecord & {        // FeatureSet.frame as GET .../features shows it
  readable: boolean;                            // false: this version cannot read it (then only the stored keys and the reason)
  out_of_date: boolean;                         // a fit it was built from was deleted, fitted again or changed since
  out_of_date_reasons: string[];                // e.g. 'the origin "Hole" was fitted again or changed since'
};
type FramePoint = { point: [number, number, number]; sigma_mm?: number | null };   // the scan's frame, mm
type NewFrame = { primary: string;              // a kept plane
                  secondary: string;            // a kept plane, cylinder or hole
                  origin: string | FramePoint;  // a kept hole, cylinder or plane, or a point
                  axes?: string;                // the primary's and the secondary's axis names, default "ZX"
                  flip_primary?: boolean; flip_secondary?: boolean;
                  secondary_zero?: boolean };   // strict 3-2-1
```

| Method and path | Body | Returns |
|---|---|---|
| `POST .../features/frame` | `NewFrame` | `PartFrameView` |
| `DELETE .../features/frame` | | 204 |
| `GET .../features/frame/mesh.ply` | | the current mesh in the part's frame (binary PLY) |
| `GET .../features/frame/mesh.stl` | | the same as binary STL |

Rules:
- **Build** (`POST`): each fit goes to `part_frame` as its id, name and shape; the record replaces a frame
  kept before. A refusal of the engine (a wrong kind of fit for a slot, a secondary that cannot set the
  turn, an origin that cannot be found, bad `axes`) is 422 with the engine's message as the `detail`. 409
  before there is a mesh, and for a fit made on an earlier mesh (fit it again first: its numbers are not
  this mesh's). 422 for an id that names no kept fit.
- **Out of date** (on reading): the shapes kept in `built_from` are compared with the kept fits of the
  same id; a fit deleted, or whose shape is not the one the frame was built from (fitted again, changed),
  makes it out of date, with a reason per slot. A rename is not a change; a mesh edit alone is not either
  (the fits are then `stale`, their shapes unchanged). Nothing is rebuilt on its own: "Build again" is a
  `POST` with the same three ids and options.
- A `frame` this version cannot read (a later `format_version`, a damaged matrix) does not stop the
  features from loading: it is shown with `readable: false`, `out_of_date: true` and the reason, kept in
  the file as it is, and replaced by the next build or removed.
- **Remove** (`DELETE`): `frame` goes back to null (204 also when there was none). The fits stay.
- **The mesh in the part's frame**: the scan's current `result/mesh.ply` with its vertices moved and its
  normals turned by the frame's matrix (the PLY keeps each vertex's `quality` and each face's `filled`),
  made on request; `Content-Disposition` names it `mesh-part-frame.ply` / `.stl`, and
  `X-OHS-Mesh-Version` is the mesh version read before it. An out-of-date frame is used as it is. 404
  without a frame or a mesh, 409 for a frame this version cannot read, 404 for another file name. The
  scan's own files (`.../files/...`) and everything on disk stay exactly as they are.
- Sizes, distances, angles and ± values are the same in every frame; the viewer shows positions and
  directions in the part's frame on request and keeps its 3D view in the scan's frame.

### Nominal values
[`FEATURE_PLAN.md`](FEATURE_PLAN.md), C3. Beside a measured size the engine offers a round one
(`ohs.cad.nominal`: Ø 14.02 ± 0.03 → "Ø 14?", 89.7° → "90?", two planes 0.05° apart → "parallel?"); the
engineer accepts it, enters another value, or ignores it. Approved values are kept with the scan beside the
measured ones and used in the CAD files ("For CAD" below). **Nothing is rounded silently:** the measured
values in `features.json` never change.

**Changed after C3 (the owner's decision):** an offer that only a known bias allows is no longer made by
default. "A Ø 14.3 boss may well be a Ø 14.3 boss": by default a value is offered only where the ± alone
allows it; `with_bias=true` asks for the bias-only ones too, marked as before (`allowed_by: "bias"`, the
sentence naming the bias). Before, they were always offered. An approved relation may also move one of its
two fits in the CAD export, when the user chooses so (`move`, below).

```ts
type NominalValueName = "diameter" | "radius" | "angle" | "distance" | "centre_distance";
type NominalSuggestion = {                      // ohs.cad.nominal Suggestion.to_dict()
  nominal: number;                              // mm, or degrees
  difference: number;                           // nominal − measured
  step: number;                                 // 1, 0.5, 0.1 mm; 90 (0°/90°), 15 (30°/45°/60°) or 1 degree
  allowed_by: "2 sigma" | "bias";               // "bias": offered only because of a known bias (simulator numbers)
  bias: object | null;                          // that bias's row, with the VALIDATION.md section it rests on
  word: string | null;                          // "parallel", "perpendicular", "in one plane", "coaxial"
  directions: string | null;                    // for 0° / 90°: whether the two directions are parallel or square
  sentence: string };                           // why it is offered, for the app to show
type NominalOffer = {                           // ohs.cad.nominal Offer.to_dict(): one measured value
  value: NominalValueName; label: string; unit: "mm" | "deg";
  measured: number; sigma: number | null;       // 1σ, the spread from scan to scan
  suggestions: NominalSuggestion[];             // nearest first; none: reason_code and reason say why
  reason_code: "already_round" | "no_candidate" | "too_many" | "no_sigma" | "warning" | null;
  reason: string | null;
  bias_only: number[] };                        // without with_bias: the values a known bias alone would allow,
                                                // left out (the reason names them when nothing is offered)
type ApprovedNominal = {
  value: NominalValueName;
  nominal: number; unit: "mm" | "deg";
  word: string | null;                          // what the nominal means for a relation (an angle of 0: "parallel")
  source: "suggested" | "entered";              // the value offered, or one typed
  measured: number; sigma: number | null;       // the measurement it was approved against
  with_bias?: true;                             // approved with with_bias: "check again" counts the known bias for it
                                                // (left out: by the ± alone)
  approved: string;                             // when (local time, to the second)
  measured_now: number | null; sigma_now: number | null;   // the value measured now (null: the relation has
                                                // no such value any more)
  check_again: boolean;                         // measured again, and the new value no longer allows it
  check_reason: string | null };                // then: the old and the new measured value, and why
type NominalAnswer = {                          // GET .../features/{id}/nominal, and the answer of POST
  id: string; name: string; kind: "fit" | "relation";
  form: "boss" | "bore" | null;                 // a cylinder's, from its record (what the offers used)
  poses: "tracked" | "true";                    // which rows of the bias table apply (below)
  poses_reason: string;                         // how that was decided, in words
  with_bias: boolean;                           // whether the offers include those a known bias alone allows
  move: RelationMove | null;                    // a relation's move (null: listed only; always null for a fit)
  offers: NominalOffer[];                       // a fit: its diameter (or its radius); a relation: each value
  nominals: ApprovedNominal[] };
type NewNominal = { value: NominalValueName; nominal: number;
                    source?: "suggested" | "entered";     // default "entered"
                    with_bias?: boolean;                  // default false: the rule the value is approved under
                    move?: "listed" | "a" | "b" };        // a relation only: its move, chosen with the approval
type RelationMove = { shape: "a" | "b";         // the relation's first or second fit moves to suit it
                      order: number;            // its place in the order the moves are applied (1, 2, ...)
                      chosen: string };         // when (local time, to the second)
```

| Method and path | Body or query | Returns |
|---|---|---|
| `GET .../features/{id}/nominal` | `value?: "diameter" \| "radius"` (a fit), `with_bias?: boolean` | `NominalAnswer` |
| `POST .../features/{id}/nominal` | `NewNominal` | `NominalAnswer` |
| `DELETE .../features/{id}/nominal/{value}` | | 204 |
| `PUT .../features/{id}/move` | `{move: "listed" \| "a" \| "b"}` | the `KeptRelation` |

Rules:
- **Offers** are `ohs.cad.nominal.suggest_fit` on the kept record (a cylinder's `form` from its record: bosses
  and bores are biased differently; a hole's from its warnings) and `suggest_relation` on the engine's
  relation of the two kept shapes, with both fits' warnings. A plane alone has none (an empty list, not an
  error); a relation's `form` "wall" is not used (the app does not know that two planes are one wall's two
  faces). Each sentence says why the value is offered and, for an offer made only because of a known bias,
  that it is a simulator number. 404 for an id that is no kept fit or relation, 422 for a `value` other than
  the diameter or the radius of a fit (a relation's offers are always all its values).
- **A known bias alone** makes an offer only with `with_bias=true` (`ohs.cad.nominal`, `with_bias`).
  Without it the search runs as if no bias were known: a value only a bias allows is not a candidate, an
  offer that would have had both kinds keeps the ± ones, and a finer step can then offer a value the ±
  allows (a Ø 14.45 ± 0.05 boss: 14.5, where the bias would offer 15). `bias_only` lists what the bias
  would add. The viewer asks for it only while its switch "Offers a known bias allows" is on.
- **The poses** (which bias rows apply) come from the scan's processing report, `result/report.json` (the
  stage the current mesh was made by): "true" only when it says `"poses": "given"` and its ground truth (a
  simulated recording) shows those poses equal the true ones (every frame within 0.001 mm and 0.001°: the
  scan was fused at its true poses); otherwise "tracked": no report, a report that cannot be read, tracked
  poses, or poses given by the app's own straighten and fuse stages, which start from the tracked ones.
  `poses_reason` says which.
- **Approve** (`POST`): `source` "suggested" approves a value offered now (422 when it is not); "entered" any
  number that makes sense for that kind of value: a diameter more than 0 and at most 10,000 mm, a radius
  more than 0 and at most 5,000 mm, an angle 0 to 90°, a distance or centre distance 0 to 10,000 mm (422
  otherwise, and for a value the item does not have: a fit's angle, a plane's size, a relation without a
  centre distance). It is kept with the value measured now (`measured`, `sigma`) and replaces an approval
  of the same value; a fit keeps one size, so a diameter replaces a radius and the other way round. An
  entered value needs no offer. A suggested value must be offered under the rule asked for: one only a
  bias allows needs `with_bias: true`, which is then kept with the approval (`with_bias: true`; left out
  when false). `move` (a relation only; 422 for a fit) chooses its move with the approval.
- **A relation's move** (`PUT .../move`, or `move` with an approval): `"listed"` (the default, as every
  relation was before) names the approved values in the CAD files' notes only; `"a"` or `"b"` moves that
  fit of the relation to suit them in the CAD export ("For CAD", `ohs.cad.moves`). Kept on the relation as
  `move` (`RelationMove`); choosing the shape already chosen keeps its place in the order, another choice
  is placed last; `"listed"` removes it. 422 for a fit's id or another value, 404 for an unknown id. The
  measured fits are not touched. A move chosen before any value is approved does nothing until one is.
- **Remove** (`DELETE`): 204; 404 when there is none. A record left without approvals loses its `nominals`
  key. Deleting a fit or a relation deletes its approvals (and a fit's relations with theirs). A rename
  keeps them.
- **Measured again:** "fit again" keeps the approvals as they were. Reading (`GET .../features`, `GET
  .../nominal`) marks one `check_again` when the measurement has changed since it was approved and the new
  one no longer allows the nominal by the engine's own rule (`ohs.cad.nominal.check_fit_nominal`,
  `check_relation_nominal`: within twice the ±, within the window a known bias widens, or what the value
  already reads as); `check_reason` gives the old and the new measured value. The rule is the one the
  approval was made under: the known bias counts only for one kept with `with_bias: true`, whatever the
  viewer's switch is now. An approval kept before the choice existed has no `with_bias` and is judged by the
  ± alone. Nothing is approved again or dropped on its own: the engineer approves again or removes it. A
  measurement that has not changed is never marked (a value entered by hand outside the rule stays as the
  engineer chose it).
- **Ignore** is the viewer's: it hides an offer in that browser until the value is measured again, and
  sends nothing.

### For CAD
[`FEATURE_PLAN.md`](FEATURE_PLAN.md), C2 (and C3, with the owner's decisions after it). The scan's kept fits as a STEP file (`ohs.cad.step`) and as a
FreeCAD macro (`ohs.cad.fcmacro`, a short Python text that builds the same shapes as named objects in a new
document), both made from `ohs.cad.shapes.cad_shapes` of the kept fits as `features.json` holds them, in the
part's frame when the scan has one (`ohs/server/cad.py`). Each plane is a flat face, each cylinder a
cylindrical face over its span and arc, each hole a circle; an axis line for every cylinder and hole; with a
frame the part's X, Y and Z axes and its origin. Every shape is named after its fit.

```ts
type CadBoundary = "rectangle" | "outline";     // a plane's edge: its rectangle (four clean edges), or its real
                                                // outline (largest outer loop and real holes, simplified)
type CadSummary = {                             // GET .../features/cad: what the two files would hold
  boundary: CadBoundary;
  n_fits: number;                               // the kept fits exported (all of them)
  counts: { plane: number; cylinder: number; hole: number; point: number };
  frame: "part" | "scan";                       // the frame the files are in
  frame_label: string;                          // "the part's axes" | "the scan's own axes"
  notes: string[];                              // the writer's (cad_shapes): a fit kept with no size, what an
                                                // outline left out, "no part frame: ...", in plain words
  warnings: string[];                           // fits made on an earlier mesh, a frame out of date or not readable
  stale: string[];                              // the ids of the fits made on an earlier mesh
  outline: { planes: number;                    // with boundary "outline": the planes written with their outline,
             points: number;                    // the points their loops hold,
             holes_kept: number;                // the holes kept in them, and the outlines' holes left out
             holes_left_out: number } | null;   // (pits of the scan, and holes that touch); null for "rectangle"
  step_bytes: number | null; macro_bytes: number | null;   // the files' sizes (null without kept fits)
  filenames: { step: string; macro: string };   // the download names, from the scan's name
  mesh_version: number | null;                  // the current mesh's version
  reason: string | null;                        // why nothing can be exported (no kept fits), else null
  nominal: {                                    // the approved nominal values used (C3)
    measured_only: boolean;                     // asked for the measured values only: none used
    sizes_used: number;                         // approved sizes written in place of the measured ones
    sizes: { id: string; name: string; value: "diameter" | "radius"; nominal: number;
             measured: number | null; sigma: number | null; check_again: boolean }[];
    relations_listed: number;                   // approved relation values listed in the notes, not applied
    relations_applied: number;                  // approved relation values a move applied
    relations: { id: string; name: string; value: NominalValueName; nominal: number; word: string | null;
                 check_again: boolean;
                 move: "listed" | "a" | "b";    // the relation's move as chosen
                 applied: boolean }[];          // whether that move was applied
    relations_note: string | null;              // with any listed: that they are not applied to the geometry
    moves: CadMove[] };                         // each relation with a move chosen and a value approved, in order
};
type CadMove = {                                // ohs.cad.moves Move.to_dict()
  id: string; name: string;                     // the relation
  shape: "a" | "b";                             // which of its fits was to move
  moved_id: string | null; moved: string | null;          // that fit (id, name)
  fixed_id: string | null; to_suit: string | null;        // the other one, which stays where it is
  applied: boolean;
  turn_deg: number;                             // how far the moved fit was turned about its own point
  slide_mm: number; slide: number[];            // how far it was slid, and the slide as a vector (the scan's frame)
  values: { value: NominalValueName; nominal: number; word: string | null;
            exported: number | null }[];        // each approved value as the exported shapes read it
  holds: boolean;                               // they all read their nominal to 1e-9 after every move
  check_again: boolean;                         // an approval of it is marked "check again"
  reason: string | null;                        // why it was not applied
  warnings: string[]; sentence: string };       // "Bottom turned 0.0500 deg about its own point and slid ..."
```

| Method and path | Query | Returns |
|---|---|---|
| `GET .../features/cad` | `boundary?: CadBoundary`, `measured_only?: boolean` | `CadSummary` |
| `GET .../features/cad/fits.step` | `boundary?: CadBoundary`, `measured_only?: boolean` | the STEP file (AP214, mm, ASCII; `model/step`) |
| `GET .../features/cad/fits.FCMacro` | `boundary?: CadBoundary`, `measured_only?: boolean` | the FreeCAD macro (ASCII Python; `text/x-python`) |

Rules:
- **What goes in:** every kept fit, in its order, with its measured values or its approved size (below);
  each plane's stored outline with `boundary=outline` (only an outline kept for the fit's own
  mesh version: a plane without one is written with its rectangle, with a note); the part's frame when the
  scan has one this version can read. `boundary` defaults to `rectangle`; another value is 422.
- **Inside**, the files are named after the part (the project's name: the STEP file's root product, the
  macro's document) and each shape after its fit, a name taken twice getting " (2)". **Outside**,
  `Content-Disposition` names them after the scan (`<scan>-fits.step`, `<scan>-fits.FCMacro`; the app saves them as
  `<project>-<scan>-fits.step` and `<project>-<scan>-fits.FCMacro`), so the two
  files, and the files of two scans, cannot be mistaken for each other. `X-OHS-Mesh-Version` is the mesh
  version read with the fits.
- **Kept as kept:** a fit made on an earlier mesh is exported as it was kept, and `warnings` says so by its
  name; an out-of-date frame is used as it is (as for the mesh in the part's frame), with a warning; a frame
  this version cannot read is not used (the files are in the scan's own frame), with a warning. The writer's
  notes are also in the files: in the STEP file's `FILE_DESCRIPTION`, in the macro as comments at the top.
- **Approved values** ("Nominal values" above): an approved **size** (a cylinder's or a hole's diameter, or
  its radius) is written in place of the measured one, in the STEP file and the macro alike
  (`ohs.cad.nominal.apply_nominal` on a copy of the shape), and the notes list each one as "Boss: nominal
  diameter 14 mm (measured 14.02 +/- 0.03 mm)" (the value measured now; plain ASCII, so the macro's comments
  read as they are). An approval marked "check again" is used too, with a warning that names it.
- **Approved relations, listed only** (a relation's `move` not chosen: the default) move no shape. Each is
  listed in the notes ("Top to bottom (Top and Bottom): approved parallel (measured angle 0.05 +/- 0.07
  deg); not applied to the geometry"), and `nominal.relations_note` says so. A scan whose relations are all
  listed only gets byte for byte the files it got before moves existed.
- **Approved relations that move a shape** (`move` "a" or "b", "Nominal values" above): a copy of that fit,
  after its approved size is in, is turned about its own point and slid to suit the approved values
  (`ohs.cad.moves.apply_moves`; its docstring has every rule): an angle (or "parallel", "perpendicular")
  turns its direction the least, in the plane of the two directions; a distance slides it along the
  direction the distance is measured in (two planes' mean normal, a plane's normal for a plane and an
  axis, across two parallel axes, along the line square to two skew ones), keeping its side; two holes'
  centre distance slides it along the line of the centres (with the distance across the axes approved too,
  along the axes after it); "coaxial" and "in one plane" are both, the turn first. A moved plane takes its
  rectangle and outline with it. The pair as exported must read every approved value to 1e-9, else nothing
  moves. **Order:** the moves are applied in the order they were chosen (`RelationMove.order`); a fit is
  moved by one relation only, and a fit an applied relation was measured against stays where it is (a later
  relation that would move it is refused: it would undo the earlier one). A move that cannot be applied
  moves nothing: the relation stays listed, "not applied to the geometry: <why>", and `warnings` says so. A
  move whose approval is marked "check again" is applied, with a warning. The notes say "applied by moving
  Bottom" for each value and, in a note of their own, "Top to bottom: Bottom turned 0.0500 deg about its own
  point and slid 0.0000 mm, to suit Top (in the export only; the measured fit is unchanged)"; a listed
  relation one of whose fits another relation moved gets a note with what it reads in the export.
  `nominal.moves` gives the same per move. The part's frame stays the one built from the measured fits.
- `measured_only=true` leaves every approval and every move out: the files are then byte for byte those of
  the same fits with nothing approved, as are the files of a scan with no approvals. The measured shapes in
  `features.json` are never changed.
- **The macro** is plain ASCII. Each name is a Python string literal (`ascii()`), and every comment one line
  of printable ASCII, so no name or note can run as code. In FreeCAD (checked with 1.1.4) it builds the
  objects in the groups "Planes", "Cylinders", "Holes", "Axes", "Points" and "Part frame", each equal to what
  FreeCAD reads from the STEP file of the same shapes (to 1e-13 mm on the sample files); unlike FreeCAD's
  STEP import it shows the part's origin as a point. The group names are taken: a fit named "Planes" is
  "Planes (2)" in the macro (and "Planes" in the STEP file).
- **No kept fits:** the files answer 409 ("This scan has no fitted shapes to export yet.", with a hint), never an
  empty file; the summary answers 200 with `reason`, the counts 0 and the sizes null. A kept fit the writer
  cannot export (a damaged shape) is 409 with its words.
- The same kept fits give the same bytes. Made on request in memory from what is read: nothing is written,
  and the scan's files and every other download stay exactly as they are. 404 for an unknown project or
  scan, and for another file name.

## Sections
[`FEATURE_PLAN.md`](FEATURE_PLAN.md), I1. The scan's current mesh cut by a plane (`ohs.mesh.section.section`
on the same arrays the fits use: each vertex's sigma, each face's filled flag), as the 2D profile an
engineer measures and traces, its DXF (`ohs.cad.dxf`, R12) and SVG (`ohs.cad.svg`) at 1:1 in mm, and the
sections a user keeps with the scan (`ohs/server/sections.py`). The plane's drawing axes follow the
engine's rule (`ohs/mesh/section.py`): the cut seen from the side the normal points to, "up" is Z turned
into the plane (Y for a plane within 45° of horizontal), never mirrored, in the part's axes when the scan
has a part's frame this version can read.

```ts
type SectionPlaneDef =                          // how the plane is named; points and normals in the scan's frame, mm
  | { kind: "fit"; fit: string; offset_mm?: number }        // a kept plane moved along its normal (default 0)
  | { kind: "frame"; axis: string; value_mm: number }       // "X" | "Y" | "Z" = value in the part's frame;
                                                            // "-X" ... sees it from the other side
  | { kind: "point_normal"; point: number[]; normal: number[] }
  | { kind: "points"; points: number[][] };                 // three points; normal (p2 − p1) x (p3 − p1)
type SectionDefinition = SectionPlaneDef & {    // as understood (and kept)
  fit_name?: string; fit_mesh_version?: number };           // "fit": the fit's name and the mesh it was fitted on
type SectionPlane = {                           // ohs.mesh.section SectionPlane.to_dict()
  point: number[];                              // the drawing's (0, 0): where the frame's origin falls on the plane
  normal: number[]; u: number[]; v: number[];   // unit; (x, y) in the drawing is point + x u + y v
  description: string;                          // "Section on Top, moved +2 mm along its normal"
  in_part_frame: boolean };
type SectionStretch = [number, number, boolean];  // segments [first, end) over measured (false) or filled (true)
                                                  // faces; segment i runs from point i to i + 1 (a closed line's
                                                  // last one back to point 0)
type SectionLineFull = {                        // ohs.mesh.section SectionLine.to_dict(), with its stretches
  closed: boolean; length_mm: number;
  area_mm2: number | null;                      // signed: + an outline (counter-clockwise), − a hole; null if open
  bbox: number[];                               // [umin, vmin, umax, vmax]
  points: number[][]; points3d: number[][];     // 2D in the drawing, 3D in the scan's frame
  sigma: number[] | null;                       // each point's 1σ (null: the mesh has none)
  filled: boolean[]; faces: number[];           // per segment
  stretches: SectionStretch[] };
type SectionLineCompact = {                     // compact: what the viewer asks for
  closed: boolean; length_mm: number; area_mm2: number | null; bbox: number[];   // rounded like the points
  n_points: number;
  points: number[];                             // [x0, y0, x1, y1, ...], to 0.001 mm
  sigma_mm: { mean: number | null; max: number | null } | null;   // over its measured points
  stretches: SectionStretch[] };
type SectionAnswer = {
  format_version: 1; units: "mm";
  plane: SectionPlane;
  lines: (SectionLineFull | SectionLineCompact)[];   // longest first
  length_mm: number; n_points: number;          // the whole cut's, also when lines were left out
  bbox: number[] | null;                        // null: the plane misses the scan (lines empty)
  n_lines: number; n_closed: number;
  mesh_version: number;                         // the mesh version cut
  definition: SectionDefinition;
  stale_fit: boolean;                           // named from a fit made on an earlier mesh (its plane is used)
  warnings: string[];                           // that fit; a frame out of date or not readable; the mesh changed
                                                // since a kept section was kept
  compact: boolean;
  truncated: { lines_shown: number; points_shown: number; lines: number; points: number } | null;
  filenames: { dxf: string; svg: string };      // the download names
  kept: KeptSection | null };                   // GET .../sections/{id}: the kept record; else null
type SectionRequest = SectionPlaneDef & { compact?: boolean; max_points?: number };
type KeptSection = {
  id: string;                                   // "s-…"
  name: string;                                 // "Section N" unless the user named it; 1 to 80 characters
  plane: SectionPlane;                          // kept as numbers, with its drawing axes
  definition: SectionDefinition;
  mesh_version: number;                         // the mesh version it was last cut on (kept, or marked current)
  created: string;
  mesh_changed: boolean };                      // the current mesh is another one (not stored)
type KeptSections = { mesh_version: number | null; sections: KeptSection[] };   // oldest first
type NewSection = SectionPlaneDef & { name?: string };
```

| Method and path | Body or query | Returns |
|---|---|---|
| `POST .../section` | `SectionRequest` | `SectionAnswer` |
| `GET .../section/section.dxf` | the `SectionPlaneDef` as a query (below) | the DXF (R12, ASCII, mm; `application/dxf`) |
| `GET .../section/section.svg` | the same | the SVG (1:1, mm, UTF-8; `image/svg+xml`) |
| `GET .../sections` | | `KeptSections` |
| `POST .../sections` | `NewSection` | `KeptSection` |
| `GET .../sections/{id}` | `compact?: boolean`, `max_points?: number` | `SectionAnswer` (with `kept`) |
| `GET .../sections/{id}/section.dxf`, `.../section.svg` | | the kept section's files, cut on the current mesh |
| `PATCH .../sections/{id}` | `{name?: string, recut?: boolean}` | `KeptSection` |
| `DELETE .../sections/{id}` | | 204 |

Rules:
- **The cut** is the engine's, number for number (full detail is `Section.to_dict()` with each line's
  `stretches` added). A fit made on an earlier mesh is used for its plane (a plane is a plane), with
  `stale_fit` and a warning that names it. With a part's frame this version can read, the drawing is in the
  part's axes for every kind of plane; an out-of-date frame is used as it is (with a warning for `"frame"`);
  one that cannot be read leaves the drawing in the scan's axes, with a warning, and is 409 for `"frame"`.
- **Compact** (`compact: true`): the 2D points as one flat list rounded to 0.001 mm (the 3D points are
  `plane.point + x u + y v`), each line's numbers rounded alike, no per-segment `filled` and `faces` (the
  stretches say the same), the sigma as each line's mean and largest over its measured points; and at most
  `max_points` points (20,000 by default): the lines, longest first, are sent whole while they fit (the
  first always), `truncated` says how many were sent of how many; the totals are the whole cut's. Measured
  on the ball-on-a-table scan (804,372 triangles): upright through the ball, 4 lines of 1,118 points, 134 kB
  in full, 18 kB compact, cut in 33 ms and made into JSON in 1 to 3 ms; 0.3 mm off the table's fitted plane,
  657 lines of 13,394 points, 1.8 MB in full and 353 kB compact (cut 74 ms, JSON 65 ms); 0.2 mm off, 1,356
  lines of 32,243 points, 4.3 MB in full, 401 kB compact with the 289 longest lines (cut 145 ms, JSON 59
  ms); in the fitted plane itself, 3,218 lines of 211,086 points, 27 MB in full, 260 kB compact (the cut
  itself takes 0.86 s there). `max_points` below 1 is 422. Without `compact` nothing is left out unless
  `max_points` is given.
- **The query** of the two downloads holds the same names: `kind`, `fit`, `offset_mm`, `axis`,
  `value_mm`, `point` and `normal` as `x,y,z`, `points` as nine numbers `x1,y1,z1,x2,...`. The files are
  `dxf_text` / `svg_text` of the very cut `POST .../section` gives for the same plane, at full precision and
  with every line; `Content-Disposition` names them after the scan and the section
  (`<scan>-section-<fit>-offset-2mm.dxf`, `<scan>-section-Z12mm.svg`, `<scan>-section.dxf`; a kept one
  `<scan>-section-<name>.dxf`); `X-OHS-Mesh-Version` is the mesh version cut.
- **Keep** (`POST .../sections`): the plane as numbers (`SectionPlane`, with its drawing axes), how it was
  named (`definition`, a fit with its name and mesh version), the current mesh version and when; kept under
  `sections` in `features.json` (`ohs.measure.features`, "Kept sections"). Nothing is cut. The lines are not
  stored. `GET .../features` answers as before: sections are not in it.
- **Open** (`GET .../sections/{id}`): cut again on the current mesh with the stored plane; when the mesh is
  not the version it was kept on, `kept.mesh_changed` is true and a warning says so. Reading writes nothing;
  `PATCH` with `recut: true` marks it as cut on the current mesh (409 before there is a mesh). A rename keeps
  everything else. `PATCH` with neither is 422.
- **Kept as numbers:** deleting the fit a section was named from does not delete the section, and changing or
  removing the part's frame does not move it: its plane and axes are stored, `definition` only says how it
  was named.
- `sections` is left out of `features.json` until a section is kept and again when the last one is deleted, so
  a file written before sections existed loads, and is written, as it was. Section ids are unique with the
  other kept items' ids.
- 409 before there is a mesh (a cut, a download, keeping); 404 for an unknown project, scan, section or fit
  (`"fit"`), and for another file name; 422 for a body that names no plane (an unknown `kind`, a missing
  value), three points on a line, a zero normal, a kept fit that is not a plane, `"frame"` without a part's
  frame, an axis other than X, Y or Z, an empty or too long name. A cut and the downloads write nothing.

## References and the comparison with one
[`FEATURE_PLAN.md`](FEATURE_PLAN.md), I2. The part as it should be (an STL, OBJ or PLY from CAD, or a scan
of a good part) is added to a project, and a scan of the part as it is is compared with it: "87 % within
± 0.3 mm", and where the rest is (`ohs/server/reference.py`; the engine is `ohs/inspect/reference.py`, on
the same arrays the fits use: each vertex's sigma, each face's filled flag). A **reference** belongs to a
project (`<project>/references/`: the file as it came, `<id>.stl` / `.obj` / `.ply`, a display copy
`<id>.view.ply`, and `index.json`); a scan keeps at most one **comparison** (`scans/<sid>/comparison.json`:
which reference, the units decision, the alignment with its pose and the mesh version it was made on, the
tolerance; not the arrays).

```ts
type ReferenceModel = {                         // as the engine read it, in its own numbers (no units factor)
  id: string;                                   // "ref-…"
  name: string;                                 // the file's name (its last part only); renamable, 1 to 120 characters
  format: "stl" | "obj" | "ply";
  bytes: number; triangles: number; vertices: number;   // after welding and dropping faces without area
  extent_mm: number[];                          // the sides of its box along its principal axes, largest first
  size_mm: number;                              // that box's diagonal
  area_mm2: number;
  bounds: number[][];                           // [min, max] in its own frame
  closed: boolean;                              // every piece closed (a solid)
  orientation: string;                          // what the engine did about its winding, in words
  dropped_faces: number; warnings: string[];
  display: { triangles: number; decimated: boolean };   // the copy served by .../mesh.ply
  added: string };
type UnitsFinding = {                           // ohs.inspect.reference UnitsFinding.to_dict()
  status: "same" | "pending" | "applied" | "kept";
  ratio: number | null;                         // the scan's part's size over the reference's
  factor: number | null; unit: string | null;   // mm per unit, when one fits: "inch", "cm", "m", "µm"
  candidates: { unit: string; factor: number }[];
  reference_size_mm: number | null; scan_size_mm: number | null;
  message: string };
type ReferenceAlignment = {                     // ReferenceAlignment.to_dict(): T takes the scan's points into the
  format_version: 1;                            // reference's frame (mm, after the units factor)
  method: "shape" | "points" | "points+shape" | "datums" | "given";
  described: string; accepted: boolean; verdict: string; T: number[][];
  pair: object | null;                          // the alignment engine's own numbers (overlap, RMS, guesses, ...)
  points_rms_mm: number | null; n_points: number;
  frame: object | null;                         // "datums": the part frame used
  fit: { robust_mm: number; moved_mm: number | null; taken: boolean; note: string } | null;
  turn_check: object | null;
  vs_datums: { rms_mm: number; max_mm: number; shift_mm: number; angle_deg: number; note: string } | null;
  warnings: string[]; ms: number };
type Comparison = {
  format_version: 1;
  reference_id: string;
  reference: ReferenceModel | null;             // null when its reference is gone
  units: UnitsFinding;
  scale: number;                                // the units factor applied (1 otherwise)
  ready: boolean;                               // false while units.status is "pending"
  alignment: ReferenceAlignment | null;         // null before it is aligned (and after the units change)
  aligned_mesh_version: number | null;
  mesh_version: number | null;                  // the current mesh
  mesh_changed: boolean;                        // aligned on another mesh version: the pose is kept
  view_matrix: number[][] | null;               // the reference's file numbers into the scan's frame:
                                                // inv(T) · diag(scale), for drawing it over the scan
  tolerance_mm: number;                         // the last tolerance asked (default 0.3)
  k: number;                                    // 2: the scan's own uncertainty is k sigma
  created: string; modified: string;
  aligning: string | null };                    // the id of the alignment job running for this scan
type ComparisonAlignRequest =
  | { method: "shape" }
  | { method: "datums" }
  | { method: "points"; pairs: { scan: number[]; reference: number[] }[]; refine?: boolean };   // default true
type ReferenceAlignResult = {                   // the result of a finished job of kind "reference_align"
  alignment: ReferenceAlignment; kept: boolean; comparison: Comparison | null; mesh_version: number;
  message: string };                            // = alignment.verdict
type RefDeviationInfo = Deviation.to_dict() & { // ohs.inspect.reference Deviation.to_dict(): counts, areas, shares,
  version: number; vertices: number;            // the rule; with the mesh version and the vertices of mesh.ply
  reference_id: string; scale: number;
  aligned_mesh_version: number | null; mesh_changed: boolean;
  note: string | null;                          // when the mesh has changed since it was aligned
  layout: string;
  states: ["compared", "not measured", "not on the reference"] };   // by state byte
type ToleranceRegion = DeviationRegion.to_dict() & {   // side, area_mm2, share, center (the reference's frame),
  center_scan: number[];                        // extent_mm, radius_mm, n_faces, mean_mm, worst_mm,
  faces: string };                              // sigma_median_mm, exceeds_uncertainty, share_beyond_uncertainty;
                                                // the centre in the scan's frame, the faces as base64 uint32
type ToleranceAnswer = ToleranceSummary.to_dict() & {  // shares by area, worst values, stats, regions, sentence
  regions: ToleranceRegion[];
  version: number; mesh_changed: boolean; aligned_mesh_version: number | null; method: string;
  coverage: { scan_area_mm2: number; compared_area_mm2: number; off_reference_share: number;
              not_measured_share: number; uncovered_share: number | null; uncovered_area_mm2: number | null;
              reference_area_mm2: number } };
```

| Method and path | Body or query | Returns |
|---|---|---|
| `GET /api/projects/{pid}/references` | | `{references: ReferenceModel[]}` (oldest first) |
| `POST /api/projects/{pid}/references` | `?name=<file name>`, the file's bytes as the body | `ReferenceModel` |
| `PATCH /api/projects/{pid}/references/{rid}` | `{name}` | `ReferenceModel` |
| `DELETE /api/projects/{pid}/references/{rid}` | | 204 |
| `GET /api/projects/{pid}/references/{rid}/mesh.ply` | | the display copy (binary PLY) |
| `GET .../comparison` | | `{comparison: Comparison \| null, mesh_version}` |
| `POST .../comparison` | `{reference_id, tolerance_mm?}` | `Comparison` |
| `POST .../comparison/units` | `{action: "apply" \| "keep", factor?}` | `Comparison` |
| `POST .../comparison/align` | `ComparisonAlignRequest` | `{job: Job, comparison: null}` or `{job: null, comparison: Comparison}` |
| `GET .../comparison/deviation.json` | | `RefDeviationInfo` |
| `GET .../comparison/deviation.bin` | | float32 per vertex, then a state byte per vertex |
| `POST .../comparison/tolerance` | `{tolerance_mm, k?}` | `ToleranceAnswer` |
| `DELETE .../comparison` | | 204 |

Rules:
- **Upload without multipart.** FastAPI's multipart parser needs a package that is not installed, so the file
  is the raw request body (`Content-Type: application/octet-stream`) and its name is in the query. Only the
  name's last part is kept (`../../x.stl`, `C:\x.stl` and `/x.stl` are all `x.stl`); it is a label and its
  extension says the format; the file is stored under the reference's id. Another format is 422 in the
  engine's words, before anything is written ("a reference must be an STL, OBJ or PLY mesh; part.step is
  .step (export a STEP or other CAD file as STL first)"), as are a missing name, an empty body and a file the
  engine cannot read or check (a point cloud, faces without area, a size no unit makes a part of). **The
  limit is 100 MB** (413 above it, with or without a `Content-Length`): a binary STL of about two million
  triangles, several times a fine CAD export of a hand-sized part. The body is held in memory once and then
  read and checked by the engine; a 715,143-triangle PLY (19.4 MB) takes 6.1 s in all on this PC, its display
  copy decimated to 250,086 triangles (4.8 MB) included. A body over the limit is read to its end and dropped,
  so the client is not cut off mid-send.
- **The display copy** is the reference's triangles as the engine wound them, as a binary PLY (the form the
  viewer reads for scan meshes), decimated (`ohs.mesh.decimate`) to 250,000 triangles when there are more.
  It is in the file's own numbers: `view_matrix` puts it over the scan.
- **Units** (`POST .../comparison`): the engine's finding (`check_units`) for the reference's size and the
  scan's part (its table set aside). A `"pending"` finding is answered as it is; the alignment, the deviation
  and the tolerance answer are then 409 until `POST .../comparison/units` applies the factor (the finding's,
  or `factor`; 422 when neither is there) or keeps the units. Changing the units drops the alignment.
- **Aligning.** `"shape"` (a best fit, `align_by_shape`) and `"points"` with `refine` (`align_by_points`
  refined on the shapes) are jobs of kind `reference_align` with progress and cancel (a cancelled job keeps
  nothing); `"points"` without `refine` and `"datums"` (`align_by_datums` with the scan's part frame) are
  answered at once. A best fit made while the scan has a current part frame carries `vs_datums` (how far it
  lies from the datum pose). An alignment the engine does not accept is answered (`kept: false` in the job's
  result) and not kept: the one kept before stays. 409 while another alignment of the scan runs; `"datums"`
  is 422 without a part frame and 409 with one that is out of date or cannot be read; `"points"` is 422 with
  fewer than three pairs or pairs in one line.
- **The deviation** is the engine's `deviation_from_reference` on the current mesh at the kept pose. The
  binary is one little-endian float32 per vertex of `result/mesh.ply` (mm, + outside the reference; NaN where
  not compared), then one byte per vertex: 0 compared, 1 not measured (a filled face, no usable sigma), 2 not
  on the reference (a table, a clamp, surface more than 3 mm off); `X-OHS-Mesh-Version` is the mesh version.
  Made once per mesh version and pose and kept in memory. On the simulated bracket scan (715,226 triangles,
  362,330 vertices): 0.32 s, 1,811,650 bytes, `deviation.json` 1.8 kB.
- **The answer for a tolerance** is the engine's `tolerance_summary` on that deviation (20 to 30 ms at that
  size; 13 to 56 kB with 17 to 65 regions at ± 0.5 to ± 0.1 mm); the tolerance asked is kept with the
  comparison. Each region adds its centre in the scan's frame and its faces, as the Check areas.
- **When the mesh changes** (an edit, Make mesh again) the scan's frame is the same, so the kept pose stays:
  the deviation is worked out again on the current mesh, and `mesh_changed` (with `note` in
  `deviation.json`) says so.
- **Removing** the comparison removes `comparison.json`; removing a reference removes its files and the
  comparisons of the project's scans that use it. Nothing else on disk changes: the scan's other files stay
  byte for byte as they were.
- 404 for an unknown project, scan or reference, and for a comparison that is not there (`GET .../comparison`
  answers `{comparison: null}`); 409 before there is a mesh, while the units wait, and before it is aligned
  (the deviation and the tolerance); 422 for bad bodies.

## Flush along a seam
[`FEATURE_PLAN.md`](FEATURE_PLAN.md), I4. The step between two panels along their seam, read on the scan's
current mesh from two clicks, one on each panel near the seam (`ohs/server/flush.py`; the engine is
`ohs.inspect.gapflush.measure_gap_flush`, on the same arrays the fits use: each vertex's sigma, each face's
filled flag). The first click is **panel A**, the second **panel B**; the flush is + where B stands above A,
square to A. **Flush is offered, gap is not** (`VALIDATION.md`, "Gap and flush between two panels"): the gap
is worked out for the record, and `gap_offered` is false with the engine's reason in `gap_note`.

```ts
type FlushSide = "measured" | "filled" | "bridged" | "missing";   // what a side of a section saw
type FlushStation = {                           // GapFlushStation.to_dict(): one section across the seam
  position_mm: number;                          // along the seam from the clicks
  point: number[]; direction: number[]; normal: number[];   // the seam's middle on A's surface line; the seam's
                                                // direction; A's normal (the scan's frame)
  gap_mm: number | null; gap_sigma_mm: number | null;   // for the record only: not offered
  flush_mm: number | null; flush_sigma_mm: number | null;   // + : B above A; 1σ, the spread from scan to scan
  edge_a: number[] | null; edge_b: number[] | null;
  side_a: FlushSide; side_b: FlushSide; bridged: boolean;
  gap_trusted: boolean; flush_trusted: boolean;
  notes: string[];                              // why a reading is not trusted, in words
  gap_by_rule: Record<string, number | null>;
  n_points_a: number; n_points_b: number; rms_a_mm: number | null; rms_b_mm: number | null };
type SeamStats = {                              // over the trusted stations; null when none is trusted
  lowest: number | null; lowest_at_mm: number | null; mean: number | null;
  highest: number | null; highest_at_mm: number | null; n_trusted: number; n_stations: number };
type FlushRecord = {                            // GapFlush.to_dict(), as it is
  format_version: 1; units: "mm"; rule: string; step_mm: number; flush_sign: string;
  gap: SeamStats;                               // for the record only
  flush: SeamStats;
  gap_offered: boolean; gap_note: string;       // false, and why, in the engine's words
  correlated_points: number; clicks: number[][]; stations: FlushStation[] };   // stations in order along the seam
type FlushAnswer = FlushRecord & {
  mesh_version: number;                         // the mesh version measured
  warnings: string[];                           // the mesh changed since a kept seam was kept; stations not trusted
  filename: string;                             // the stations CSV's download name
  kept: KeptSeam | null };                      // GET .../seams/{id}: the kept record; else null
type FlushRequest = { a: number[]; b: number[] };   // the two clicks, the scan's frame, mm
type KeptSeam = {
  id: string;                                   // "sm-…"
  name: string;                                 // "Seam N" unless the user named it; 1 to 80 characters
  a: number[]; b: number[];                     // the two clicks, kept as numbers
  mesh_version: number;                         // the mesh version it was last measured on
  created: string;
  mesh_changed: boolean };                      // the current mesh is another one (not stored)
type KeptSeams = { mesh_version: number | null; seams: KeptSeam[] };   // oldest first
type NewSeam = FlushRequest & { name?: string };
```

| Method and path | Body or query | Returns |
|---|---|---|
| `POST .../flush` | `FlushRequest` | `FlushAnswer` |
| `GET .../flush/stations.csv` | `?a=x,y,z&b=x,y,z` | the stations as CSV (UTF-8 with a byte-order mark) |
| `GET .../seams` | | `KeptSeams` |
| `POST .../seams` | `NewSeam` | `KeptSeam` |
| `GET .../seams/{id}` | | `FlushAnswer` (with `kept`) |
| `GET .../seams/{id}/stations.csv` | | the kept seam's stations, measured on the current mesh |
| `PATCH .../seams/{id}` | `{name?: string, mark_current?: boolean}` | `KeptSeam` |
| `DELETE .../seams/{id}` | | 204 |

Rules:
- **The answer** is the engine's record key for key, with the mesh version, the warnings and the download
  name beside it. A click the engine refuses (more than 3 mm from the mesh, the two clicks closer than 2 mm,
  one above the other, no seam between them) is 422 with the engine's own words as the detail. On the
  gap-and-flush study's tracked scan with a flush of 2 mm (190,522 triangles): 0.66 to 0.73 s, 86 stations
  (78 trusted), 85 kB of JSON, a CSV of 23 kB; the mean flush 2.023 mm, the study's own reading for that scan
  (the truth 1.999 mm).
- **The CSV** has one row per station, in order along the seam: `position_mm`, `x_mm`, `y_mm`, `z_mm` (the
  seam's middle), `flush_mm`, `flush_sigma_mm`, `flush_trusted` (`yes` / `no`), `side_a`, `side_b`,
  `bridged`, `notes` (joined by " | "); numbers in full, empty where there is none; text a spreadsheet could
  take for a formula starts with an apostrophe. No gap column: the gap is not offered.
  `Content-Disposition` names it `<scan>-flush.csv` (a kept seam `<scan>-flush-<name>.csv`);
  `X-OHS-Mesh-Version` is the mesh version measured.
- **Keep** (`POST .../seams`): the two clicks, a name, the current mesh version and when; kept under `seams`
  in `features.json` (`ohs.measure.features`, "Kept seams"). Nothing is measured, and the stations are never
  stored. `GET .../features` answers as before: seams are not in it.
- **Open** (`GET .../seams/{id}`): measured again on the current mesh from the kept clicks; when the mesh is
  not the version it was kept on, `kept.mesh_changed` is true and a warning says so. Reading writes nothing;
  `PATCH` with `mark_current: true` marks it as measured on the current mesh (409 before there is a mesh). A
  rename keeps everything else; `PATCH` with neither is 422.
- `seams` is left out of `features.json` until a seam is kept and again when the last one is deleted, so a
  file written before seams existed loads, and is written, as it was. Seam ids are unique with the other
  kept items' ids.
- 409 before there is a mesh (a measurement, a download, keeping); 404 for an unknown project, scan or seam
  and for another file name; 422 for a click that is not three finite numbers, a click the engine refuses,
  an empty or too long name.

## The report
[`FEATURE_PLAN.md`](FEATURE_PLAN.md), I3. A scan's data collected on the server (`ohs/server/report.py`), each
part from its own manager, into the plain structure `ohs.report.data` documents, and answered as one
self-contained HTML page that prints to PDF from any browser (`ohs.report.build_html`), the kept features as
CSV (`ohs.report.tables.features_csv`) and as JSON (`features_json`). Built on request, written nowhere.

```ts
type ReportFile = "report.html" | "features.csv" | "features.json";
type ReportPicture = {
  title: string;                                // 1 to 120 characters
  caption?: string | null;                      // at most 500 characters
  data: string };                               // the picture's bytes in base64: PNG (JPEG, GIF, WebP taken too)
type ReportRequest = {                          // every key optional
  tolerance_mm?: number | null;                 // the Check answer at this tolerance (0 < t <= 100); none: left out
  numbers_in_part_frame?: boolean;              // the fits' positions and directions in the part's frame
  seam?: string | null;                         // the kept seam whose flush is shown (default: the first that
                                                // the engine can measure)
  pictures?: ReportPicture[] };                 // views of the model, in order (the page only)
```

| Method and path | Body | Returns |
|---|---|---|
| `POST .../report/report.html` | `ReportRequest` | the page (`text/html; charset=utf-8`), `<scan>-report.html` |
| `POST .../report/features.csv` | `ReportRequest` | the kept fits and relations (`text/csv`, UTF-8 with a byte-order mark), `<scan>-features.csv` |
| `POST .../report/features.json` | `ReportRequest` | the kept features (`application/json`), `<scan>-features.json` |

Rules:
- **Collected, only what exists** (a part the scan does not have is left out of the page, never shown
  empty): the project's and the scan's names, the date (read once per request), the app's version; how the
  scan was made (`result/report.json`, its known keys); the calibration the scan was made with (below; until 2026-11-21 the setup state's calibration
  part and last board check); its health, *since H4*, is the scan's own status, `result/report.json`'s
  `health_status`: state, part, reason, what was not judged, the note and that the limits are provisional; a
  scan processed before H3 has none and its health reads "Not assessed"; beside it, *since 2026-11-20*, the
  row "Camera path": the camera path's warnings, "No jump found" or "Not judged", from `result/report.json`'s
  `path_check` and `warnings`, left out for a scan processed before the check); the Check answer at `tolerance_mm`
  (`POST .../check`'s, before there is a mesh left out); the kept features **as `GET .../features` shows
  them** (stale marks, relations with the engine's record, approvals with "check again" and its reason, the
  frame's state) and the CAD export's moves when a relation has one; every kept section cut again on the
  current mesh (with "the mesh has changed" as its warning when it has); the kept comparison with a
  reference (the reference, its units, the alignment and, once aligned, the deviation of the current mesh and
  the answer at the comparison's kept tolerance and `k`; the mesh-changed note goes with the alignment's
  warnings); one kept seam's flush, measured on the current mesh; the pictures. Every part's warnings are
  shown in it and again in the list near the top. The CSV holds the features only; the JSON the features and
  the sections' numbers (not their drawings).
- **The calibration it was made with** (*since 2026-11-21*, UX_PLAN R6): the Calibration part describes the copy of the calibration in the scan's own recording (`recording/calibration.json`, the file the scan was recorded and processed with), not Setup's current target. Its first row, "Made with", says which: "The simulator's own calibration, the one its demo recording was made with." (the recording's `meta.json` says `device: "sim"`, or without `device` `source: "sim"`), "The replayed recording's own calibration, the one it was recorded with." (`device: "replay"`) or "The scanner's calibration, as it was when this scan was recorded.". The rows Cameras, Laser lines ("Calibrated: the horizontal, vertical laser lines") and Motion sensor (IMU) come from that file and its `steps`; "Calibration file" says when it was saved and how far apart the cameras are. Only when it is still Setup's scanner calibration (the same `calibration_key`) do Setup's words for its steps, the row "Calibration check" (Setup's last calibration check: its verdict, or Passed / Not passed) and the laser lines this build has but the file lacks (in the Laser lines row and the warnings) come beside it. A recording without a calibration file says so. The rows "Missing" and "Last board check" and the word "baseline" are gone.
- **A merged scan** (*since 2026-11-21*, UX_PLAN R8): "How the scan was made" opens with "Merged from" (the names of the scans, "aligned and merged into one mesh; it has no recording of its own") and "What the merge left out" (each scan's table, "<name>: its table is left out (…)", and what the merge's cleaning took, from its `report.json`'s `scans[].table.summary` and `mesh.cleaning.summary`); the merge's `warnings` are in the warnings list, under that part. Its Calibration part says that each scan it was merged from was made with its own calibration, and its Health "Not judged for the merged scan itself: a merge has no recording of its own, so there is nothing new to judge the calibration by. The reports of the scans it was merged from have it." (never "processed before the app did that").
- **Pictures:** at most 6 (422 above), each at most 3 MB decoded and 12 MB in all (413, with the limit in
  words), base64 text (422 otherwise). A picture that is not a PNG, JPEG, GIF or WebP image is left out of the
  page with a note naming it. The viewer makes them from the 3D view, at most 1600 pixels across.
- **Nothing on disk changes:** a report reads through the managers, which write nothing on a read (the
  comparison's answer at its tolerance is worked out here, not through `POST .../comparison/tolerance`, which
  keeps the tolerance it is given). On the ball-on-a-table scan (804,372 triangles, one kept plane, a kept
  section 40 mm off it): the page in 0.4 s with the mesh in memory (22 kB with the Check answer at ± 0.3 mm),
  the CSV in 0.01 s, the JSON in 0.04 s.
- **The page holds one seam** (`ohs.report.data` takes one `gap_flush`): with several kept seams, `seam` picks
  which; a kept seam the engine cannot measure on the current mesh is passed over.
- 404 for an unknown project, scan, seam (`seam`) or file name; 422 for a bad body.

## M5 second slice: the deviation map
The plan is in [`DESKTOP_APP_PLAN.md`](DESKTOP_APP_PLAN.md), "M5 plan, second slice". It compares the scan's
current mesh with its base mesh: the last stage's result, before any edit.

```ts
type DeviationInfo = {
  version: number;                      // the mesh version it was made for
  vertices: number;                     // values in deviation.bin = vertices of result/mesh.ply
  edits: number;                        // the cursor: 0 = no edits, every value is 0
  reference: { stage: string; triangles: number; vertices: number };   // the base mesh
  stats: { n: number; new: number;      // compared vertices; vertices of filled faces only (no value)
           rms_mm: number | null; mean_mm: number | null;               // mean: signed
           p50_mm: number | null; p95_mm: number | null; p99_mm: number | null; max_mm: number | null;  // of |move|
           moved: number | null };      // share moved by more than 0.01 mm; the numbers are null when n = 0
};
```

Paths are relative to `/api/projects/{pid}/scans/{sid}`.

| Method and path | Returns |
|---|---|
| `GET .../process/deviation.json` | `DeviationInfo` |
| `GET .../process/deviation.bin` | one little-endian float32 per vertex of `result/mesh.ply`, in its order: the signed distance to the base mesh in mm (+ outward along the base's normal, - inward), NaN for a vertex that only filled faces use. The header `X-OHS-Mesh-Version` has the version. |

Rules:
- 409 before the scan has a mesh.
- Made once per mesh version (about 1.5 s for 1M vertices against 2M triangles), then kept; a second
  request during the first waits for it.

## M5 fourth slice: where the lasers and the mesh disagree
[`ROADMAP.md`](ROADMAP.md), section 3, item 4. The laser lines are triangulated on their calibrated planes,
independently of the stereo depth that makes the mesh. This map shows how far the two are apart on the scan's
current mesh (`ohs.mesh.laser_agreement`). It moves nothing: "Refine with lasers" does that.

```ts
type LaserInfo = {
  version: number;                      // the mesh version it was made for
  vertices: number;                     // values in lasers.bin = vertices of result/mesh.ply
  available: boolean;                   // false: this scan has no laser points (the lasers were off or not calibrated)
  gate_mm: number;                      // laser points farther than this from the surface are left out as stray (3)
  points: { total: number;              // laser points of the scan (result/laser_points.ply)
            on_surface: number;         // those that fall on the mesh (not past an edge or over a hole)
            used: number;               // those within gate_mm: they make the map
            stray: number };            // on the surface but farther than gate_mm (glints, reflections)
  stats: { n: number;                   // vertices with a value
           covered: number | null;      // share of the surface (by area) that has a value, 0..1
           mean_mm: number | null;      // signed mean
           rms_mm: number | null; p50_mm: number | null; p95_mm: number | null; max_mm: number | null;  // of |distance|
           within_0_1: number | null;   // share of the vertices with a value that are within 0.1 mm
           within_0_3: number | null }; // ... within 0.3 mm; the numbers are null when n = 0
};
```

Paths are relative to `/api/projects/{pid}/scans/{sid}`.

| Method and path | Returns |
|---|---|
| `GET .../process/lasers.json` | `LaserInfo` |
| `GET .../process/lasers.bin` | one little-endian float32 per vertex of `result/mesh.ply`, in its order: the mean signed distance in mm from the mesh to the laser points nearest that vertex, along the vertex normal (+ = the lasers see the surface further out than the mesh, - = further in); NaN where no laser point came near. The header `X-OHS-Mesh-Version` has the version. |

Rules:
- 409 before the scan has a mesh. With `available: false` every value is NaN.
- A laser point belongs to its nearest vertex. One that projects more than 1.5 median edges from that vertex is
  off the mesh and ignored.
- Made once per mesh version (1.2–2.1 s for 0.6–0.8 million vertices and 150,000 laser points), then kept; a
  second request during the first waits for it.
- On simulated scans at Standard quality the lines cover 8–9 % of the surface, and there the mesh and the lasers
  agree within 0.1 mm for 62–69 % of the vertices and within 0.3 mm for 91–97 % (median 0.06–0.07 mm).

## Remove the table (an edit)
[`FEATURE_PLAN.md`](FEATURE_PLAN.md), X1. A single scan keeps the table the part stood on; this edit finds
that surface and removes it (`ohs.mesh.support`), as an edit like the others: undo, redo, in a user's recipe,
never in a built-in one. The Process page shows the faces it would remove first (the preview below).

```ts
type MeshOp = ... | "remove_table";     // added to the edits of "M3 phase 2"
//   remove_table { margin_mm?: number;   // 0-20 mm, default 1: how far above the table's plane a face that
//                                        // faces the table's way still counts as the table
//                  keep?: number[][] }   // "Keep this": a point [x, y, z] (mm) on each raised area to keep (at most 50)
type TablePreview = {
  version: number; triangles: number;   // the mesh it was found on
  faces: string;                        // base64 of little-endian uint32 face indices of result/mesh.ply, sorted
  n_faces: number; area_mm2: number; area_left_mm2: number;
  normal: [number, number, number]; d: number;   // the table's plane n . x + d = 0, n towards the part (mm)
  share: number;                        // of the scan's surface on the plane, 0..1
  front_share: number; behind_share: number;     // ... in front of it (the part) and behind it
  flatness_mm: number;                  // RMS of the table about its plane
  band_mm: number;                      // the noise band (4 x flatness, 0.3-1.5 mm): the plane's own faces
  margin_mm: number; facing_deg: number;          // 45: a face turned less than this faces the table's way
  lowest_mm: number | null;             // the lowest corner of what is left, above the plane
  loose_removed: number;                // crumbs of table removed with it
  loose_left: number; loose_left_mm2: number;     // other loose pieces, left for Remove floaters
  skipped: string[];                    // larger flat surfaces set aside first (a box's top), and why
  raised: { centre: [number, number, number]; size_mm: number; area_mm2: number;   // raised areas within the margin
            height_mm: number;                        // its rise above the table round it
            scatter_mm: number;                       // how much the table round it scatters (what it is told from)
            tilt_deg: number; top_mm: number;         // its top against the table; how flat its top is
            verdict: "kept" | "warned" | "uneven";    // stays | goes, drawn and listed | the table's own unevenness, said once
            kept: boolean; by_user: boolean;          // by_user: kept by a keep point
            neighbours?: number;                      // other raised areas within 80 mm
            n_faces: number;
            faces: string | null }[];                 // base64 uint32 face indices (kept and warned areas; preview only)
  thin_limit_mm: number; thin_keep_mm: number;    // this scan's own limit for thin parts lying flat (2.5 and 4 x RMS)
  limit: string;                                  // the same in words
  warnings: string[]; rule: string;     // the rule in words
  summary: string; ms: number;
};
```

Paths are relative to `/api/projects/{pid}/scans/{sid}`.

| Method and path | Body | Returns |
|---|---|---|
| `POST .../process/remove-table/preview` | `{margin_mm?, version?, keep?}` | `TablePreview`; nothing changes |
| `POST .../process/edits` | `{op: "remove_table", params: {margin_mm?, keep?}}` | `Job` (kind `mesh_edit`), as every edit |

Rules:
- **The plane** is found as the alignment finds its support plane (`ohs.align.pairwise.scan_cloud`), from
  measured surface only (filled holes are left out), holding at least 15 % of the scan; it is the table only
  when something stands on it, no more of the scan lies behind it than in front, and the part does not go on
  behind it next to what stands on it (a part's own face). A plane that fails is set aside and the next largest
  is tried (three at most): a box whose top is larger than the table seen round it keeps its top.
- **The faces**: behind the plane (all corners further than the band) everything goes; above it, a face goes when
  it faces the table's way (within 45°), lies at most `margin_mm` above the plane and is joined through such
  faces to the plane's own surface. Faces turned further away (the part's walls) stay at any height. Loose
  pieces left wholly within the margin plus the band are crumbs of table and go too.
- **Thin parts lying flat** (a shim, a gasket, a sheet): a raised area within the margin, of steady height, not
  joined to a wall that rises above the margin, is judged against the table round it (3-10 mm away): its rise
  above that table's level, against that table's scatter. From 2.5 times the scatter it is listed: kept (from 4
  times, top parallel to the table within 0.25°, 20 mm across or more, the table round it flat to 0.1 mm), else
  `warned`: it goes with the table, drawn and listed, and `keep` (a point on it) keeps it, through the edit, undo
  and redo. Where the table round it scatters more than 0.1 mm, or two other raised areas lie within 80 mm, it is
  the table's own unevenness (`uneven`): it goes and one warning says how thin a part cannot be told there. The
  scan's own limit is in `thin_limit_mm` / `thin_keep_mm` / `limit`. A table with only thin parts on it is a
  table; with nothing on it, it is refused (and the refusal names a raised area it could not tell).
- The edit's label says the areas kept: `Remove the table (12,345 faces, 1 area kept)`; its `params` hold the keep
  points (to 0.01 mm). Each drawn area of the preview comes with its faces.
- **No table** (none of the planes passes, or there is too little flat surface): the preview answers 422 with the
  engine's sentence ("No table found: …. Nothing was removed; …") and a hint to select the table by hand; the edit's
  job fails with the same sentence and the mesh is as it was. In a recipe the step is skipped, in the same words,
  and the recipe goes on ("Remove the table: skipped (…)").
- The edit's label is `Remove the table (12,345 faces)`; its `params` are `{"margin_mm": m}` (the default written
  down). The faces it removed are kept in `selections/` like a deleted selection, so a step rebuilt for undo /
  redo removes exactly the same faces; each vertex's sigma and each face's filled flag follow as for a deleted
  selection.
- The preview is read-only and kept per mesh version and margin: the edit that follows on the same version and
  margin removes exactly its faces without looking again. `version`: the mesh version the page shows (409 when it
  changed); 422 for a margin out of range; 409 before the scan has a mesh.
- 1.8–2.3 s on simulated scans of 0.7–0.8 million triangles (the other edits: 0.4–2.4 s). On the simulated bracket
  seen from three sides it leaves 0.5–3.3 mm² of table near the part and takes 17–19 mm² of the part's rounded
  foot, all within 1 mm of the table (a 1 mm slab: 50–70 mm²; the merge's 2 mm: 118–171 mm²).

## M5 third slice: areas that need another pass
The plan is in [`DESKTOP_APP_PLAN.md`](DESKTOP_APP_PLAN.md), "M5 plan, third slice". It works on the scan's
current mesh.

```ts
type CheckArea = {
  kind: "uncertain" | "not_measured";  // 2 sigma above the tolerance | made up by hole filling (or no sigma there)
  area_mm2: number; share: number;      // share of the whole surface, 0..1
  center: [number, number, number];     // area-weighted, mm
  extent_mm: number; radius_mm: number; // largest side of its bounding box; farthest vertex from the centre
  n_faces: number;
  sigma_median_mm: number | null; sigma_worst_mm: number | null;   // over its measured vertices
  faces: string;                        // base64 of little-endian uint32 face indices of result/mesh.ply, sorted
};
type CheckResult = {
  version: number; tolerance_mm: number; k: number;                 // k = 2: a face fails when k x its mean sigma > tolerance
  has_sigma: boolean;                   // false: this mesh has no confidence values (made without a GPU)
  area_mm2: number; min_area_mm2: number;                           // the whole surface; smaller areas are not listed
  share_ok: number | null; share_uncertain: number | null;          // null when has_sigma is false
  share_not_measured: number | null;
  n_areas: number;                      // all areas above the minimum
  areas: CheckArea[];                   // largest first, at most 50
};
```

| Method and path | Body | Returns |
|---|---|---|
| `POST /api/projects/{pid}/scans/{sid}/check` | `{tolerance_mm}` (above 0, at most 100) | `CheckResult` |

Rules: 409 before the scan has a mesh; 422 for a bad tolerance. Read-only and quick (about 0.5 s on a
2M-triangle mesh). The answer rests on each vertex's sigma: statistical, and so far checked on
simulated scans only (`VALIDATION.md`).

### Why an area is weak, and where to scan it from (FEATURE_PLAN N1)
A second route, asked after `POST .../check` for the same tolerance: the plain answer stays as fast as it was
and shows first; this one explains its areas in a few seconds (`ohs.mesh.check.add_reasons`,
`ohs.guide.views`).

```ts
type CheckReason = "not_seen" | "steep" | "far" | "few_looks" | "edge" | "unknown";   // the first that holds
type CheckPose = {
  T_wc: number[][];                     // 4x4: camera C (the left camera; x right, y down, z forward) in the mesh's frame
  position_mm: [number, number, number];        // the scanner's centre (between its cameras)
  camera_position_mm: [number, number, number];
  look_dir: [number, number, number];   // unit, from the scanner to the area
  up: [number, number, number];         // the scanner's up (its images' up)
  target_mm: [number, number, number];  // the area's centre
  distance_mm: number;                  // the working distance (where the calibrated laser sheets cross)
  tilt_deg: number; azimuth_deg: number; // from the area's normal (0: square on)
};
type CheckView = {                      // where to scan the area from
  ok: boolean; pose: CheckPose | null;  // false: no pose, reason says why ("cannot be seen from outside the part: ...",
  share_seen: number | null;            //   "no place to scan from: on an edge ...", "no place to scan from: <why there is no camera path>")
  border_seen: number | null;           // share of the unseen edge beside it the pose looks past
  candidates: number; missed: {blocked: number; facing_away: number; steep: number; outside: number} | null;
  reason: string | null; text: string;  // text: one sentence for the user
};
type CheckWhyArea = {
  index: number;                        // its place in CheckResult.areas (the same areas, in the same order)
  kind: "uncertain" | "not_measured"; area_mm2: number; n_faces: number; center: [number, number, number];
  normal: [number, number, number];     // mean of its vertices' normals
  reason: CheckReason; reasons: CheckReason[]; reason_text: string;
  evidence: { looks: number | null; looks_passing: number | null; view_angle_deg: number | null; distance_mm: number | null;
              in_view_share: number | null; edge_share: number | null; border_share: number; outward: number[] | null };
  unknown_because: string[];            // "steep, far: ...", "few_looks: ...", "edge: ...": what was missing to tell
  view: CheckView;
};
type CheckWhy = {
  version: number; tolerance_mm: number; n_areas: number;
  areas: CheckWhyArea[];
  plan: { area: number; kind: "area" | "more"; pose: CheckPose; share_seen: number; border_seen: number | null;
          also_sees: { area: number; share: number }[] }[];        // at most 3 places to scan from
  given: { looks: boolean; views: boolean; edges: boolean; working_mm: number | null; steep_deg: number; far_mm: number | null; ... };
  camera_path: { available: boolean; why: string | null;           // why: in words, when there is none
                 scans: { name: string; frames: number; trajectory: string }[];
                 frames_total: number; frames_used: number; frames_cap: number | null };
  ms: number;
};
```

| Method and path | Body | Returns |
|---|---|---|
| `POST /api/projects/{pid}/scans/{sid}/check/why` | `{tolerance_mm, version?}` | `CheckWhy` |

Rules:
- The areas are those `POST .../check` gives for the same tolerance and mesh version (the server keeps that
  answer and explains it without finding the areas again); `version`: the mesh version of the answer on screen,
  409 when it changed. 409 before the scan has a mesh, 422 for a bad tolerance. Read-only.
- What it rests on: each vertex's count of looks (`mesh.ply` `looks`, carried through every edit; none on the CPU
  path or in older files), the voxel size (`report.json` `mesh.voxel_mm`), and the camera path. A scan's camera path
  is the trajectory of its base stage (`process/<stage>/trajectory.csv`, else `result/trajectory.csv`, else the
  track stage's), the calibration in its recording and the depth range in its report (`params.depth`). A merged
  scan uses each scan it was made from (`merged_from`, with the `T` its report lists) and that scan's own files;
  when one of them is gone or its camera path changed since the merge (another number of frames), none is used and
  `camera_path.why` names it. What is missing makes the reasons that need it `unknown`, with `unknown_because`
  in words, and there is then no pose: nothing is guessed.
- The camera path's rules look at `frames_cap` (120) frames at most, evenly spread over the scan (a merge: each
  scan its share), so the time does not grow with the length of the scan; `frames_used` says how many.
- Made once per mesh version and tolerance; the parts every check of a mesh version shares (the ray-casting
  scene, the open border, the curvature term) are kept for the next tolerance. On a scan of 0.8 million
  triangles and 355 frames at 0.1 mm: see `VALIDATION.md`.

## A second pass that joins the first (FEATURE_PLAN N2)

"Scan these areas" in the Check panel starts a scan that continues the scan the Check was made on: the new scan
knows which scan it belongs to and which areas were asked for, and after Stop it is made into a mesh, aligned to
that scan and merged with it, as a chain of ordinary jobs (`ohs/server/passes.py`; the engine of the Align page,
its acceptance rule and its merge). The first scan is never changed; the merge is a new scan (`merged_from`).
A merge adds coverage; it does not yet make the surface the first scan saw well more accurate (`VALIDATION.md`,
"Aligning and merging two scans").

```ts
type PassStart = {                       // POST /api/scan/start: continues
  scan_id: string;                       // the first scan (it must have a mesh)
  tolerance_mm: number;                  // the Check's tolerance
  version?: number;                      // the first mesh's version the Check answer was made on (409 when it changed)
  areas?: number[];                      // places in that Check answer, from 0
  poses?: { area: number | null; kind: string; text: string | null; pose: CheckPose }[];  // at most 12, as the Check panel gave them
  same_setup?: boolean;                  // default true; false: the part was moved since the first scan
};
type PassChain = {
  state: "waiting" | "processing" | "aligning" | "merging" | "merged" | "not_aligned" | "not_merged" | "failed" | "cancelled";
  step: "process" | "align" | "merge" | null;
  jobs: { process?: string; align?: string; merge?: string };   // the steps' job ids (the last of each)
  message: string;                       // what happened, in words
  merged_scan_id?: string | null;
  align?: { accepted: boolean; verdict: string; overlap: number | null; agree: number | null; rms_mm: number | null;
            sure: boolean | null; doubt: string | null; start: string | null; T: number[][] | null;
            little_shared: boolean | null; caution: string | null } | null;   // since 2026-11-23 (M4 first slice, "Little
                                         // shared"); a merged pass's `message` ends with the merge's caution when there is one
};
type PassLink = {                        // Scan.continues (the pass's scan entry), and meta.json "continues" without chain
  scan_id: string; scan_name: string; tolerance_mm: number; version: number;
  areas: { index: number; kind: "uncertain" | "not_measured"; area_mm2: number; center: number[]; n_faces: number }[];
  poses: PassStart["poses"]; same_setup: boolean;
  setup_words: string;                   // same_setup in words: "The part was not moved since the first scan: ..." / "The part was moved ..."
  merge_problem: string | null;          // why the pass could be aligned but not merged (the first scan's recording is gone ...)
  preset_from_first: boolean; preset: string;   // the pass took the first scan's preset (none was sent); its name
  chain: PassChain;
};
type PassView = Omit<PassLink, "chain"> & {
  pass_id: string; pass_name: string; chain: PassChain; job: Job | null;   // the step's job
  steps: ["process", "align", "merge"]; note: string;                         // note: what a merge does and does not do
  first: { id: string; name: string; mesh_url: string | null; has_mesh: boolean };
  areas_faces: string[] | null;          // per asked area, base64 uint32 face indices on the first mesh (while it is that version)
  areas_faces_why_not: string | null;
};
type PassCompare = {
  tolerance_mm: number; note: string;
  rule: { match_mm: number; match_angle_deg: number; passes_share: number };
  first: PassShares; merged: PassShares;  // {id, name, share_ok, share_uncertain, share_not_measured, n_areas, area_mm2, has_sigma}
  areas: (PassLink["areas"][number] & { share_ok: number | null; found_share: number;
           state: "passes" | "partly" | "weak" | "not_found" | "unknown" })[];
  areas_note: string | null;             // when judged at another tolerance than asked
  common: { found_share: number; before_ok: number | null; after_ok: number | null };  // on the surface both hold
  missing: { share: number; area_mm2: number; why: string | null };  // of the first scan's surface, not in the merge
  whole_note: string;                    // first / merged are each whole mesh, as its own Check says it
};
```

| Method and path | Body | Returns |
|---|---|---|
| `POST /api/scan/start` | `{..., continues: PassStart}` | `SessionState` with `continues: {scan_id, scan_name}` |
| `GET /api/projects/{pid}/scans/{sid}/pass` | | `PassView` (`sid`: the pass) |
| `POST /api/projects/{pid}/scans/{sid}/pass/run` | | `PassView`: runs what is left of the chain |
| `POST /api/projects/{pid}/scans/{sid}/pass/compare` | `{tolerance_mm?}` (default the pass's) | `PassCompare` |

Rules:
- **Start.** `continues` is checked before anything is made (404 unknown scan; 409 the first scan is recording or
  processing, "has no mesh yet, so there is nothing to join a second pass to", or its mesh "changed since its Scan
  check was shown"; 422 a bad tolerance, an area "not in the Scan check", an area asked for twice, a pose without three
  numbers, `same_setup` not true or false). The areas are taken from the first scan's Check answer at that
  tolerance (`ohs.mesh.check.weak_areas`, as `POST .../check` gives it), and their faces' centres, normals and
  areas are kept next to the pass (`scans/<pass>/pass_areas.npz`), so an edit of the first scan later does not
  change what was asked for. The pass's entry gets `continues` (`PassLink`), its recording's `meta.json`
  `continues` (the link without `chain`); while it records, `GET /api/scan`, the status messages and the start's
  answer carry `continues: {scan_id, scan_name}`. **No `preset` and no `preset_values`: the first scan's preset**
  (with its hand values; General when it has none or its preset is gone), said in `preset_from_first` and `preset`.
  A scan without `continues` starts exactly as before: none of these fields anywhere.
- **After Stop** the chain runs by itself: `process_scan` (the pass's preset's quality), then `align` (target the
  first scan, source the pass, `same_setup`, tags as the Align page uses them), then, only when the alignment is
  accepted, `merge` into a new scan "<first> + <pass>". Each is an ordinary job (`GET /api/jobs`, the `job`
  messages) and can be cancelled: the chain then stops (`cancelled`), the pass is kept as its own scan, and
  `.../pass/run` goes on (the mesh again if it has none, else the alignment). An alignment that is not accepted
  merges nothing: `not_aligned`, with the aligner's verdict, "Nothing was merged: the pass is kept as its own scan"
  and the Align page (where matching points can be picked); a log message says the same. `not_merged`: aligned,
  but the merge cannot be made (`merge_problem`: the first scan's recording was removed to free space, a scan it
  was merged from is gone or its camera path changed, more than 8 scans). A pass of a merged scan merges again
  from the scans that one was made from, each at its place in its report (`scans[i].T`), with the pass at the
  aligned pose: one merge of all of them.
- **Run** (`.../pass/run`): 409 "is not a second pass" for a scan without `continues`, "running already" while a
  step runs, "merged already" once merged, the first scan's refusals as at Start.
- **Compare** (`.../pass/compare`, read-only): 409 until the chain is `merged`. The Check answer of the first scan
  and of the merged scan at the same tolerance (`share_ok`, `n_areas`, ... of each), and each asked area judged on
  the merged mesh (`ohs.guide.passes.area_progress`): each of its faces is found on the merged mesh as the face
  whose centre lies within `match_mm` (2.5 mm), whose normal is within `match_angle_deg` (60°), and whose plane
  passes nearest; it passes as the Check decides (measured, not filled, 2 sigma within the tolerance). `share_ok`
  is the share of the area that passes now (it passed nowhere before: it was listed), `found_share` the share
  found at all; `state` `passes` from `passes_share` (0.9) up, `partly`, `weak`, `not_found` (not in the merged
  scan), `unknown` (no sigma). A plate 0.3 mm thick on its table, or seen from both sides, is judged by the side
  asked for (`tests/test_guide_passes.py`); where the merged surface does not reach as far as the first one did, the
  surface under it can stand for it up to 2.5 mm from its rim.
- **The same surface before and after** (since review, 2026-11-20): `first` and `merged` are each scan's whole mesh,
  and they need not hold the same surface (a part that was moved: each scan's table is left out of the merge). So
  the change is said on the surface both hold: every face of the first mesh is looked for on the merged one by the
  rule above (`ohs.guide.passes.common_surface`); `common.found_share` is the share of the first scan's surface found,
  `before_ok` / `after_ok` the share of that surface that passes on each. `missing` is the rest, with `why` when
  it is known (the merged scan's report lists the tables it left out: "the part was moved, so each scan's table was
  left out of the merge"). The app never calls a difference of the whole meshes a fall.
- **The setup in words:** `setup_words` says what `same_setup` the pass was started with, on the Scan page while it
  records and on the card after Stop, so a switch sent the wrong way round shows.
- **The live view is not the first scan's.** Each scan starts its own tracking; until the pass is aligned after
  Stop, nothing places it in the first scan's frame, so the app shows the first scan beside the live view as a
  reference picture (faint, the asked areas lit, the places to scan from drawn), never laid over it.

## M4 first slice: align two scans and merge them

Two processed scans of one part, in one project, are aligned to each other and merged into a new scan (`ohs/server/aligning.py`; the engine: `ohs.align`, measured in `VALIDATION.md`, "Aligning and merging two scans"). The *target* scan keeps its place; the *source* scan is moved onto it. A scan's mesh is in the scan's own world (see Coordinates), so points picked on a mesh are in that scan's frame, in mm.

### Types

```ts
type PointPair = { source: [number, number, number]; target: [number, number, number] };

type AlignRequest = {
  target: string;            // scan id: stays where it is
  source: string;            // scan id: is moved onto the target
  same_setup?: boolean;      // default true: the part was not moved on its table between the two scans.
                             // false: it was turned over or moved (each scan then has its own table)
  pairs?: PointPair[];       // optional: 3 or more matching points picked on the two meshes, as a rough start
  use_tags?: boolean;        // default true: without pairs, start from the AprilTags both scans saw (see Rules)
  hold_tags?: boolean;       // since 2026-11-27: hold the tags' pose for this alignment only (true or false); left out:
                             // [process] align_hold_tags decides. No effect with pairs (the start is not from tags)
};

type TagStart = {            // what the tags did (null: the two scans share no tag, or tags were not looked for)
  shared: number[];          // tag ids both scans saw
  used: number[];            // those the start was made from
  left_out: number[];        // shared tags that did not fit the others (moved between the scans)
  rms_mm: number | null;     // how well the used tags' corners agree
  start: string;             // "accepted", or why the tags gave no accepted start (then the shape's own guesses were used)
                             // since 2026-11-24, only with [process] align_hold_tags on: also "held" (the tags' pose kept)
                             // and "overruled" (the shapes overruled the tags); Rules, "The tag pose held"
};

// the `result` of a finished job of kind "align"
type AlignResult = {
  project_id: string; target: string; source: string;
  accepted: boolean;         // false: not aligned; `reason` and `verdict` say why and what to do
  verdict: string;           // one sentence for the user ("Aligned: 76 % of ... agree." / "Not aligned: ... .")
  reason: string | null;
  T: number[][];             // 4x4, row-major rows: takes points of the source's mesh into the target's frame
  start: "features" | "given" | "tags";   // from the shape alone, from the picked pairs, or from the tags
  tags: TagStart | null;
  overlap: number;           // 0..1: share of the smaller scan's part within 1 mm of the other scan
  rmse_mm: number | null;    // RMS along the normals of what matches
  fit_mm: number | null;     // robust spread of the residuals within 3 mm
  candidates: number; agree: number;   // starting guesses tried, and how many ended at the chosen pose
  spread_mm: number;
  dof: number;               // directions of motion the overlap pins down (6 = all)
  same_setup: boolean;
  used_support: boolean;     // the table was used for height and tilt
  support: { target: boolean; source: boolean };   // a table was found in each scan
  pairs_rms_mm: number | null;   // how well the picked pairs agree with each other (a large value: two pairs do not match)
  n_matched: number; ms: number; message: string;  // message = verdict
  // since 2026-11-17 (MAINTENANCE_PLAN F11; Rules, "Sure or not sure"):
  sure: boolean;             // = accepted: the evidence is there
  doubt: AlignDoubt | null;  // why not, in one word (null when sure)
  poses: AlignPose[];        // the chosen pose first (= T), then the others that fit best; at most 4
  turned: number;            // starts tried turned round
  reached: number;           // starts that reached the chosen pose independently (first guesses; turned starts only when they found it from elsewhere)
  // since 2026-11-23 (Rules, "Little shared"):
  little_shared: boolean;    // accepted, but overlap under 0.70: the scans share little
  caution: string | null;    // with little_shared: what it means and what helps, in words ("Little is shared: 46 % of the
                             // smaller scan's part lies within 1 mm of the other. The two scans fit where they meet, but ...");
                             // null otherwise. Nothing else changes: accepted, T and the verdict are as without it
  // since 2026-11-27 (Rules, "The tag pose held"): what the page says and offers about holding the tags' pose; null:
  // nothing to say. AlignAllResult carries the same block, one for the whole result
  hold: {
    state: "offered" | "held" | "not_held";
    asked: boolean | null;     // the request's hold_tags; null: the setting decided
    words: string;             // the sentence(s) to show
    caution: string | null;    // held only: the shapes, starting at the tags, would move a held scan more than 1.0 mm
                               // (ohs.server.aligning.HOLD_CAUTION_MM)
    links: { a: string; b: string; tags: number[]; shape_moves_mm: number | null }[];   // b placed on a
  } | null;
  // since 2026-11-24 (ENGINE_PLAN X19; Rules, "The tag pose held"): present only with the hold on (the setting, or since
  // 2026-11-27 hold_tags: true), for an
  // alignment that started from tags and shares under 0.70; absent otherwise (the result is then exactly as before)
  held_by_tags?: boolean;        // true: T is the tags' pose; the shapes only checked it
  tags_used?: number[];          // the shared tags the pose came from (those agreeing within 0.5 mm when 3 or more do)
  tag_rms_mm?: number | null;    // how well their corners agree (RMS, mm)
  tag_pose_sigma_mm?: number;    // the tag pose's own 1-sigma displacement from the corners' scatter (mm); the scanner's
                                 // calibration, which tags and surface share, is not in it
  shape_moves_mm?: number | null;    // how far the refinement that started at the tags moved the scan from the tags' pose
                                     // (RMS over its part)
  overruled?: string | null;     // why the tags were not held (the refinement from them moved the scan more than the
                                 // larger of 2 mm and 4 tag_pose_sigma_mm); null otherwise
  overlap_at_shape?: number;     // the overlap where that refinement ended (reported only)
  not_held?: string | null;      // why the hold did not act although weighed: too few tags, tags that agree too loosely,
                                 // tags lying on the table of a part that was moved (same_setup false), or too little
                                 // overlap at the tags' pose; null when held or overruled
};

// "rival", "turned", "one_guess", "points": the engine had a pose in mind but is not sure (poses hold it);
// "part", "guess", "overlap", "fit": no usable pose (too little of the part, no starting guess, too little shared, a loose fit)
type AlignDoubt = "rival" | "turned" | "one_guess" | "points" | "part" | "guess" | "overlap" | "fit";

type AlignPose = {
  T: number[][];             // as AlignResult.T
  chosen: boolean;           // the engine's own answer (the first)
  pick: number;              // the engine's score: the share of matching surface (the smaller of the two scans' shares)
  overlap: number; rmse_mm: number | null; fit_mm: number | null;
  reached: number;           // starts that ended here
  from: string[];            // "your points", "the tags", "the first guesses", "turned round"
  apart_mm: number;          // how far from the chosen pose (RMS over the source's part)
};

type MergeRequest = {
  target: string; source: string;
  T: number[][];             // the pose to merge at (an AlignResult's T)
  same_setup?: boolean;      // default true. false: each scan's table is left out of the merged model (Rules)
  name?: string;             // default "<target name> + <source name>", or the next free "… (2)" (UX_PLAN R21)
};

// the `result` of a finished job of kind "merge"
type MergeResult = {
  project_id: string; scan_id: string; scan: Scan; merged_from: [string, string]; message: string;
  tables: MergeTable[];      // since 2026-11-17: one per scan when the part was moved (same_setup false), else []
  warnings: string[];        // since 2026-11-17: what to look at, each naming its scan; [] when the part stayed
                             // (since 2026-11-23 also one line per pair of scans that share little, part moved or not)
  cleaning: string | null;   // since 2026-11-17: in words, the small loose pieces the merge left out (and, the part moved,
                             // the small pieces over a thin part it kept) (report.json: mesh.cleaning). Since 2026-11-21
                             // (UX_PLAN R7) also when the part was not moved: "No loose bit was left out as too small (under
                             // 1 % of the triangles each)." or "12 small loose bits (3,400 mm²) were left out as too small
                             // (under 1 % of the triangles each)."; null only when the merge cleans nothing
                             // ([process] clean_min_component 0)
  // since 2026-11-23 (Rules, "Little shared"): the merge is given poses only, so it measures the alignment's
  // overlap itself, on the scans' meshes at those poses
  shared: MergeShared | null;   // null when it could not be measured (the merge goes on)
  caution: string | null;       // shared.warnings in one text (they are in `warnings` too); null when enough is shared
  // since 2026-11-24 (ENGINE_PLAN X19): present only when the merge's poses are a held alignment's (an align or align_all
  // job of this server run whose result had held_by_tags true, at the same pose within 1e-6)
  held_by_tags?: { a: string; b: string; tags_used: number[]; tag_rms_mm: number | null; tag_pose_sigma_mm: number | null;
                   shape_moves_mm: number | null; words: string }[];   // b was placed on a by the tags; also in the
                                                                         // merged scan's report.json (`held_by_tags`).
                                                                         // Information, not a warning: not in `warnings`
};
type MergeShared = {
  pairs: { a: string; b: string; overlap: number }[];          // every pair of the merged scans (scan ids)
  little_shared: { a: string; b: string; overlap: number }[];  // the links that hold the scans together (the chain of
                                                               // best-shared pairs) and share less than `limit`
  limit: number;             // 0.70
  warnings: string[];        // one per link of little_shared: "\"Top\" and \"Back\" share little where they were merged:
                             // 46 % of the smaller one's part lies within 1 mm of the other. They fit where they meet, but ..."
  s: number;                 // the seconds it took (2 to 3 for two scans of half a million vertices)
};
// what a merge of a moved part left out of one scan (Rules, "Same setup or not")
type MergeTable = {
  id: string; name: string;
  rule: "map" | "slab" | "none";  // map: its table found on its mesh; slab: none found, the old 2 mm slab; none: nothing left out
  summary: string;                // in words: what was left out and kept
  warnings: string[];             // this scan's, also in MergeResult.warnings
};

// Scan gains:
//   merged_from?: { id: string; name: string }[]   // present on a merged scan: the scans it was made from
```

### REST

| Method and path | Body | Returns |
|---|---|---|
| `POST /api/projects/{pid}/align` | `AlignRequest` | `Job` (kind `"align"`, meta `project_id`, `target`, `source`); its `result` is an `AlignResult` |
| `POST /api/projects/{pid}/merge` | `MergeRequest` | `Job` (kind `"merge"`, same meta); its `result` is a `MergeResult` |

### Rules

- **Align writes nothing.** It reads the two meshes (`result/mesh.ply`) and answers in a few seconds. A pose that is not accepted is still returned in `T`, for showing; do not merge at it without the user's say.
- **Not accepted is an answer, not an error:** the job ends `done` with `accepted: false` and a verdict such as "Not aligned: not sure which way it goes: it fits about as well turned round (93 % as well, 55 mm away; ...): pick three or more matching points on both scans, or choose one of the poses." The app then offers picking points.
- **Sure or not sure** (since 2026-11-17, MAINTENANCE_PLAN F11): an alignment from the shape is accepted (`sure: true`) only when enough of the part is shared (overlap at least 15 %, the residuals within 0.7 mm), the chosen pose beats every other pose by a clear margin (the next scores less than 0.9 of it), and at least two different starts reached it. The engine also tries the chosen pose turned half round about each main axis of both scans' parts (and a quarter round about the thinnest direction), because a wrong pose that scores well is most often the right one turned round. When the evidence is not there the answer is "not sure": `doubt` says why in one word, the verdict in words, and `poses` holds the poses it had in mind, the engine's best first (`T`). The page shows them laid over the first scan; the user chooses one or picks points. **Do not merge at a pose the engine was not sure of unless the user chose it or picked the points.** Picked points are checked against the shapes: when another pose fits clearly better than theirs (theirs scores less than 0.8 of it) or the refinement pulls the scan more than 10 mm from where the points put it, the answer is "not sure" with `doubt: "points"`. A start from the tags is not overruled by a better-fitting pose (a sphere turned round always overlaps more of itself somewhere). Before the scans are matched, a large plane is taken for the table only when something stands on it and the part does not go on behind it (else it is kept as a face of the part), and stray bits left on the table are dropped.
- **Little shared** (since 2026-11-23; `VALIDATION.md`, "Two scans that share little"): where two scans share little of the part they fit where they meet, and the position can be a millimetre or more off elsewhere; merges at such poses came out far worse than either scan. So an accepted alignment whose `overlap` is under 0.70 (`ohs.align.pairwise.CAUTION_OVERLAP`, measured) has `little_shared: true` and a `caution` in words, from the shape, from picked points and from tags alike. It is a caution, not a refusal: `accepted`, `T`, `poses` and the verdict are as before, and Merge stays possible without a further question. The page shows it under the verdict ("The scans share little"). A merge measures the same share itself at the poses it is given ("Checking how much the scans share", 2 to 3 s; `MergeResult.shared`) and puts one line per pair that shares little into `warnings`, `caution` and the merged scan's `report.json` (`warnings`, `shared`), whether the part was moved or not. For more than two scans only the links that hold the scans together count (the chain of best-shared pairs): two sides that share little with each other but much with a third scan get no line. A check of the merged mesh itself was measured and cannot tell a ruined merge from a sound one, so there is none.
- **Picked points:** each pair is one spot on the part, clicked on both meshes. Three pairs that span a triangle are enough; a millimetre or two of clicking error is fine, the alignment refines it. With pairs the result's `start` is `"given"` and no second guess has to confirm it, so check `pairs_rms_mm` (above about 5 mm two pairs probably do not match).
- **Tags** (since 2026-11-16; `ohs.align.tags`): without picked points, the job looks for AprilTags (`[markers]` dictionary and `tag_mm`) in both scans: a marker-mode scan's own map (`result/marker_map.json`), or in its recording at the poses of its mesh (up to 120 frames, about 1 s per 60 frames at 640x400). When the two share tags that agree, the alignment starts from them (`start: "tags"`, verdict "... from the tags both scans saw.") and keeps that start along the directions the shape pins only weakly: a sphere, a cylinder or a plate is then turned as the tags say. If that start is not accepted, the shape's own guesses are tried as without tags (`tags.start` says why). Tags on the table help only when the part was not moved on it (same setup); for a part that was moved, only tags on the part. A merged scan has no recording, so no tags of its own.
- **The tag pose held** (since 2026-11-24, ENGINE_PLAN X19; `[process] align_hold_tags`, **off by default**: with it off every alignment, merge and response is exactly as before; `VALIDATION.md`, "A pose from tags held where two scans share little"). Where two scans share little, their own fit has its minimum where their surfaces' biases put it, 0.5 to 1 mm from the truth on the simulated bracket's opposite sides and 3 to 4 mm on two bent sheet scans, and a refinement started from the tags walks there every time. With the setting on, an alignment that started from tags and whose refined pose shares under 0.70 keeps the tags' pose when 3 or more shared tags agree within 0.5 mm (a tag that does not fit the others is left out first): `T` is the tags' pose, `held_by_tags: true`, `tags.start: "held"`, the verdict "Aligned from the 5 tags both scans saw (their corners agree within 0.12 mm). The scans share little of the part (44 %), so the tags decide the position; the shapes, starting at the tags, would move it 0.9 mm." and the caution "The scans share little, but the tags hold their position (to about 0.1 mm, one standard deviation). A wall seen from both sides can still come out a little too thick or too thin." (`little_shared` stays true). The tags are not held when the refinement that started at them moved the scan by more than the larger of 2 mm (8 mm until 2026-11-26) and 4 times the tag pose's sigma (a tag map wrong as a whole: a table moved with its tags, or the part nudged against tags on the table). The answer is then exactly the one without the hold (pose and numbers), `overruled` says how far, `tags.start: "overruled"`, and the verdict adds "The tags did not hold this position: the shapes, starting at the tags, moved the scan 8.7 mm from where the tags put it (more than 2.0 mm). A tag may have moved, or the part was moved with its table: check the tags." (since 2026-11-26; there is no search from the shapes alone, so a part that fits about as well turned round does not overrule right tags). With fewer than 3 shared tags (or tags that agree only loosely) the alignment is the one without the hold, and its verdict adds "Only 2 tags were seen by both scans, too few to hold the position; the shapes refined it, starting at the tags." At 0.70 shared or more nothing changes. The hold weighs only a pose the refinement from the tags accepted: it never turns a refusal into an acceptance (a tag map wrong by 20 mm is refused by that refinement and the shapes' own guesses decide, as without the hold). Tags lying on the table (within 3 mm of the target scan's table plane) do not hold a part that was moved (`same_setup: false`): they say where the table is, not the part (`not_held`, and the verdict says so). A merge at a held pose (the same server run) records it in `held_by_tags` and in the merged scan's report.json, as information, not among `warnings`. Since 2026-11-26 that link's "share little where they were merged" line is left out of `warnings` and `shared.warnings`, and `caution` is null when no other link shares little; `shared.pairs` and `shared.little_shared` still give the measured share. **Offered per alignment (since 2026-11-27).** The owner keeps `[process] align_hold_tags` off; the Align page offers the hold through `hold_tags` in the body of `align` and `align_all`: true or false for that alignment only, left out the setting decides. Picked points are not a start from tags, so it has no effect on a pair that has them. Both results carry a `hold` block the server writes, so the page has no rules of its own. `"offered"`: the hold was off, and a link that places a scan was accepted from tags, shares little and had 3 or more tags (`hold_min_tags`) that do not lie on the table of a moved part; its words name the tags and say when holding is right ("Both scans saw tags 0, 1, 2 and 3. Where two scans share little, these tags can place the scan more exactly than the shapes, as long as no tag moved against the part between the two scans (tags on the part, or tags on the table and the part not touched)."), with no number for that pair. `"held"`: it names the held links ("The tags hold \"Back\" on \"Top\", as asked for this alignment.", or "..., as the settings ask."), with `caution` when the shapes, starting at the tags, would move a held scan more than 1.0 mm: how far, that a small disagreement is normal, that a part nudged against tags on the table looks the same, and to let the shapes decide if that may be so. The caution advises only: counted on the study's alignments it shows on 10 of 45 right holds and not on 9 of 23 harmful small nudges (`VALIDATION.md`, "The tag hold offered per alignment"). `"not_held"`: the hold was on and weighed and no link was held; the words give the engine's reason ("Holding the tags' position was asked for, but it was not applied: only 2 tags were seen by both scans, too few to hold the position."). `null`: no start from tags, 0.70 shared or more, not accepted, picked points, `use_tags: false`. An alignment of a pair without the hold forgets that pair's earlier held pose, so a later merge is not recorded as placed by the tags. With `hold_tags` left out and the setting off every other field is exactly as before (`tests/test_server_align_hold_offer.py`).
- **Same setup or not:** with `same_setup: true` the table both scans share pins height and tilt. If the two tables turn out more than 5° or 5 mm apart after the part's fit, they are not used (`used_support: false`). With `false` only the part counts, and a merge leaves each scan's table out, and nothing else (since 2026-11-17; before, everything up to 2 mm in front of each table went, with the bottom 2 mm of the part and any part thinner than that lying flat). The job ("Finding what the part lay on") finds each scan's table on its own mesh as "Remove the table" does (`ohs.mesh.support`, module docstring, 4.), and turns it into a map on the table's plane: where the table is bare, a frame's depth and laser points are left out up to 2 mm above it; where the part, a thin part lying flat, or a raised area that may be one stands, nothing above the table is left out. What the frames leave of the table (a ring at the part's foot about 2 mm wide, crumbs) is then taken out of the merged mesh by the single-scan edit's rule (faces facing the table's way within 1 mm of it, where that scan found its table, not on a thin part; loose crumbs within that height), and laser points within 1 mm of it where the part does not stand. Raised areas the single-scan edit only warns about are **kept** in a merge (a patch of table left is better than a part lost) and said; where the table is uneven itself (near a scan's border) its raised areas go with it, said once; a part thinner than the scan's own limit (about 0.2 mm on the simulated tables) cannot be told from the table and goes with it, which the scan's `limit` says. A scan whose table cannot be found on its mesh is clipped as before by the alignment's support plane (2 mm, with the part's bottom edge), or not at all when there is none, and a warning names it. `MergeResult.tables` and `warnings` say all this per scan; the merged scan's `report.json` has it in full (`scans[i].table`: `rule`, `plane`, `flatness_mm`, `table_area_mm2`, `stands_mm2`, `thin_parts` with `verdict` and `in_merge`, `uneven`, `thin_limit_mm`, `limit`, `left_in_mesh` {`faces`, `area_mm2`, `pieces`}, `laser_points_removed`, `summary`, `warnings`; top-level `warnings`; `mesh.n_faces_table_removed`, `lasers.n_points_table_removed`). With `same_setup: true` the merge is unchanged: a plain fusion of the frames, its mesh bit for bit as before, `tables` empty and `warnings` empty but for the lines of "Little shared".
- **Merge** fuses the raw frames of both recordings again at the given pose, with the target scan's quality, and takes about as long as processing both scans. **Presets** (since 2026-11-20): the merge makes its model with the first (target) scan's preset values that act on meshing, `voxel_mm` and `thin_walls`, as a processing stage takes them (`ohs.server.processing.preset_meshing`); the merged scan's entry gets that scan's `preset`, its `report.json` a `preset` block (`used`: the values the model was made with; `from_scan`), and a scan of the merge whose preset acts with other values is named in `warnings` ("\"Side\" was made with the app's mesh detail; the merged scan is made with mesh detail 0.5 mm, as \"Top\" (the scan that stays in place) was."). With General everywhere nothing is added and the merge is exactly as before (`tests/test_server_merge_preset.py`). The job reports "Merging: frame k of n" and can be cancelled; nothing is added to the project then. On success a new scan appears (`status: "processed"`, `mode: "markerless"`, `n_frames` = frames fused, `merged_from`), with `mesh.ply`, `mesh.stl`, `laser_points.ply` and `report.json` in its result, and a notification "Merged scan ready: <name>". The two scans are unchanged.
- **Scans of different calibrations merge too** (since 2026-11-16: a session after a recalibration, another camera mode or another scanner): each scan's frames are processed with its own recording's calibration and fused into one model, whose grid is the finest of the scans' own unless `[process] voxel_mm` sets it. The merged scan's `report.json` says which calibration each scan had (`scans[i].calibration`, `calibrations`).
- **A merged scan has no recording.** It can be viewed, edited, measured and exported, and another scan can be aligned to it; it cannot be tracked or merged again (409 "has no recording").
- **Errors** (`{detail, hint}`): 404 unknown project or scan; 409 the two ids are the same, a scan is recording or processing, a scan has no mesh yet, and for a merge a scan without recording or without the camera path of its mesh; 422 fewer than three pairs, pairs that lie in one line or on one spot, a `T` that is not a 4x4 rigid motion.
- **Limits of this slice:** two scans at a time (the second slice below takes up to eight); the alignment is as good as the two meshes (about 0.2–0.5 mm on the simulated bracket at 1280x800); a merged model is not yet more accurate than the better of its two scans, it holds more of the part.

## M4 second slice: several scans at once

Two to eight scans of one part, in one project, placed in the world of the first one and merged into one new scan. Every pair is aligned as above; each scan is placed through the chain of best links that reaches it, so two scans that share too little to align to each other (opposite sides) can both be placed through a third. Where the links form a loop, they are solved together and a link that does not fit is left out (`VALIDATION.md`, "Several scans at once").

### Types

```ts
type PickedPoints = { a: string; b: string; pairs: PointPair[] };   // `target` points on scan a, `source` points on scan b

type AlignAllRequest = {
  scans: string[];           // 2..8 scan ids; the first stays where it is
  same_setup?: boolean;      // default true, as for two scans
  picked?: PickedPoints[];   // optional rough starts for some pairs
  use_tags?: boolean;        // default true: pairs without picked points start from the tags they share
  hold_tags?: boolean;       // since 2026-11-27: as AlignRequest's, for every pair of this alignment that starts from tags
};

// the `result` of a finished job of kind "align_all"
type AlignAllResult = {
  project_id: string; scans: string[];
  accepted: boolean;                      // every scan placed
  T: Record<string, number[][] | null>;   // per scan: 4x4 into the first scan's frame; null = not placed
  placed: string[]; unplaced: string[];
  pairs: {                                // every pair, as AlignResult without T, plus:
    a: string; b: string;                 // b was aligned onto a
    used: "chain" | "loop" | "left out" | "";   // how the placement used it ("" = not accepted)
    disagree_mm?: number;                 // after placing: how far this link disagrees with the poses (RMS over the part)
    accepted: boolean; verdict: string; overlap: number; rmse_mm: number | null; start: "features" | "given" | "tags"; agree: number;
    tags?: TagStart;                      // present when the two scans share tags
    link_weight?: "tags" | "overlap";     // since 2026-11-24, only with [process] align_hold_tags on and a start from tags
                                          // that shares under 0.70 (the entry then also has AlignResult's hold fields): a
                                          // held link places its scan first in the chain, and in a loop its information
                                          // is the tags' corners at their own scatter
  }[];
  tags: Record<string, number[]>;         // per scan: the tag ids found in it ([] none, or tags not looked for)
  loops: number;             // accepted links beyond the chain
  graph: boolean;            // the loops were solved together
  worst_link_mm: number;
  support: Record<string, boolean>;
  same_setup: boolean; ms: number;
  verdict: string;           // "All 3 scans placed through 2 links; 1 more link checked the chain, the worst disagrees by 0.27 mm."
                             // or "Not all placed: \"Back\" could not be aligned to any placed scan. Pick matching points ..."
  message: string;           // = verdict
  // since 2026-11-23 (first slice, Rules, "Little shared"); each entry of `pairs` also has little_shared and caution
  little_shared: { a: string; b: string }[];   // the links used to place scans ("chain" or "loop") that share little; the
                                               // verdict ends " Little is shared between \"Back\" and \"Top\" (46 %)."
  caution: string | null;    // what that means and what helps (no names); null when every link used shares enough
  hold: AlignResult["hold"]; // since 2026-11-27: one block over the links that place a scan ("chain" or "loop")
};

// MergeRequest gains a second form (give one or the other):
//   { scans: { id: string; T: number[][] }[]; same_setup?: boolean; name?: string }   // 2..8 scans, T from AlignAllResult
```

### REST

| Method and path | Body | Returns |
|---|---|---|
| `POST /api/projects/{pid}/align_all` | `AlignAllRequest` | `Job` (kind `"align_all"`, meta `project_id`, `scans`); its `result` is an `AlignAllResult` |
| `POST /api/projects/{pid}/merge` | `MergeRequest` in either form | `Job` (kind `"merge"`, meta `project_id`, `scans`, and `target` / `source` for two scans) |

### Rules

- **Time:** every pair is aligned, 1–5 s each: 3 pairs for three scans, 28 for eight.
- **Not all placed is an answer:** the job ends `done` with `accepted: false`, the poses of the scans that were placed, and a verdict that names the others by their names. Picked points between an unplaced scan and any placed one (`picked`) are the way forward.
- **Sure or not sure** (since 2026-11-17): only pairs the engine is sure of are links; each pair carries `sure`, `doubt` and `poses` as for two scans (there `T` takes scan `b` into scan `a`). Where a scan is not placed because a pair was "not sure", the verdict says so in brackets ("... could not be aligned to any placed scan (not sure of scan 3 with scan 1: the shapes fit more than one way). Pick matching points ...").
- **A link that disagrees:** `worst_link_mm` above 2 mm adds "That is a lot: look at the scans together before merging." to the verdict.
- **Merging several:** as for two scans; the name defaults to the scans' names joined by " + "; `merged_from` lists all of them in order.
- **Errors:** 422 for fewer than two or more than eight scans, picked points that name a scan not in the list or are fewer than three pairs or in one line, both merge forms or neither; 409 for a scan listed twice, and as for two scans.

## Disk use of a project

What a project's folder takes on disk, per scan and in total (FEATURE_PLAN R1, `ohs/server/disk.py`). It informs only: nothing is removed.

### Types

```ts
type ScanDiskUse = {
  id: string; name: string; n_frames: number; duration_s: number;   // as in Scan
  bytes: number;            // the whole scan folder
  recording: number;        // recording/
  frames: number;           // recording/frames/ alone (inside recording)
  n_frame_files: number;    // files in recording/frames/
  stages: Record<string, number>;  // each folder under process/ but cache and selections: track, straighten, fuse, spacetime, refine
  stages_bytes: number;     // their sum
  undo: number;             // process/cache/ and process/selections/
  result: number;           // result/
  other: number;            // everything else (process/state.json, thumbnail, features, comparison, ...)
};                          // recording + stages_bytes + undo + result + other == bytes

type ProjectDiskUse = {
  project_id: string;
  bytes: number;            // the whole project folder
  scans: ScanDiskUse[];     // in project.json order; a scan without a folder has zeros
  parts: { recording: number; stages: number; undo: number; result: number };  // sums over the scans
  references: number;       // references/
  other: number;            // project.json and folders project.json does not list
  trash: { bytes: number; projects: number;    // <projects>/.trash: all deleted projects, not part of this one
           scans: number };                    // and deleted scans (since 2026-11-20, "Rename a scan, the trash")
  seconds: number;          // how long the walk took
};
```

### REST

| Method and path | Body | Returns |
|---|---|---|
| `GET /api/projects/{pid}/disk` | | `ProjectDiskUse`; 404 for an unknown project |

### Rules

- **Sizes** are the files' sizes in bytes (`st_size`), all integers, not the space the file system allocates. Links and junctions are not followed.
- **Cost:** one walk of the project folder and the trash on every request, nothing cached (so never stale); about 50 to 150 ms for 4,700 files. A plain function, so it runs in the server's thread pool and never holds up the event loop. `GET /api/projects/{pid}` does not include it: the project page asks for it after it has painted.

## Rename a scan, the trash

Since 2026-11-20 (UX_PLAN U7 with U50; `ohs/server/projects.py`, `ohs/server/trash.py`). A scan can be renamed and moved to the trash as a project can; the trash lists both, restores them and, asked twice, deletes them for good.

```ts
type TrashItem = {
  id: string;                   // the folder's name inside <projects>/.trash: the only way to name an item
  kind: "project" | "scan";
  name: string;                 // the project's or the scan's name
  deleted: string | null;       // when it was moved to the trash (ISO 8601)
  bytes: number;                // its folder's size
  n_scans: number | null;       // a project: how many scans it holds
  project_id: string | null;    // a scan: the project it was in, and that project's name then
  project_name: string | null;
  project_exists: boolean | null;  // a scan: whether that project is there (else restore the project first)
};
```

### REST

| Method and path | Body | Returns |
|---|---|---|
| `PATCH /api/projects/{pid}/scans/{sid}` | `{name}` | `Scan`. The id and the folder stay. An empty name is 400, one over 120 characters 400; 404 for an unknown scan. |
| `DELETE /api/projects/{pid}/scans/{sid}` | | `{item: TrashItem}`. Query `dependents_ok=true`: see below. |
| `GET /api/trash` | | `{items: TrashItem[], bytes}`, the newest first |
| `POST /api/trash/{item}/restore` | | `{kind, project_id, scan_id (null for a project), name, renamed}` |
| `POST /api/trash/empty` | `{confirm: true, items: [TrashItem.id, ...]}` | `{removed: [ids], left: [ids], bytes}` |

### Rules

- **Never deleted on the way:** a scan's folder moves to `<projects>/.trash/<project_id>--<scan_id>-<time>/` with `trashed-scan.json` beside its files (the project, the project's name, the scan's `project.json` entry, when), then it leaves the project's list. A project moves as before (`<project_id>-<time>`). Nothing is deleted until the trash is emptied.
- **Refused, in a plain sentence (409), and nothing moves:** the scan is recording, or a second pass of it is recording; a job makes its mesh, edits it, aligns or merges it (any running job whose `project_id` is the project's and whose `scan_id`, `target`, `source` or `scans` names it).
- **What depends on it:** a scan that other scans were merged from (`merged_from`) or that a second pass continues (`continues.scan_id`) is refused with 409 naming them ("Other scans depend on this one: “Top merged” was merged from this scan; “Top, second pass” is a second pass of it."), until the request says `dependents_ok=true` (the page asks the user first, naming them). They keep their own meshes; they cannot be made again from it while it is in the trash.
- **Names (U50):** a new scan without a name is "Scan <n>": the count of the project's scans plus one, or the next number no scan of the project has (so no two scans "Scan 3" after one went to the trash).
- **No two scans of a project share a name** (*since 2026-11-21*, UX_PLAN R21): a rename to a name another scan of the project has (the same letters, case and extra spaces aside) is 409 "Another scan of this project is already called “Bracket”.", hint "Pick another name, for example “Bracket (2)”.", with *extra* `suggestion: string` in the error's body (the first free one of "<name> (2)", "<name> (3)", …): the page can offer it as a button. Renaming a scan to its own name is fine. A merge's default name (no `name` given) takes the next free one too: "Top + Side", then "Top + Side (2)", …. A merge with a `name` that another scan of the project has is refused the same way as a rename (409 with `suggestion`), before the merge starts.
- **Restore** puts the folder back where it was and the scan back on its project's list (its entry as it was). Nothing is overwritten: an id that is taken gets a new one (a scan `<id>-r`, a project `<name>-<4 hex>`), a name that is taken gets " (restored)" (then " (restored 2)" ...), and `renamed` says so. A scan whose project is not there (in the trash too, or deleted) is 409: restore the project first.
- **Empty the trash** deletes for good, so it needs `confirm: true` and `items`, the ids the page showed (400 otherwise): an item moved to the trash after the page listed it stays. An id that is not an item of the trash is left out. `left` lists items that could not be deleted completely (a file held open).
- **Ids only:** `{item}` must be a folder name of the ids rule (letters, digits, `.`, `_`, `-`, no `..`), directly inside the trash, holding a project (`project.json`) or a scan (`trashed-scan.json`); anything else is 404, and a folder the user put into the trash is never listed, restored or deleted. No route takes a path.
- The routes move or remove folders: plain functions, run in the server's thread pool.

## Folders and the log

Since 2026-11-20 (UX_PLAN U39, U12, U34; `ohs/server/folders.py`). The pages open the app's own folders by what they are, never by a path, and save the app's log for a bug report.

| Method and path | Body | Returns |
|---|---|---|
| `POST /api/open-folder` | `{what, project_id?}` | `{opened: true, what}` |
| `GET /api/log/file` | | `text/plain; charset=utf-8`, saved as `openhandscan-log-<YYYYMMDD-HHMM>.txt` |

Rules:
- `what`: `"settings"` is the folder of the settings file (`config_path`'s folder), `"logs"` the app's log folder (`<data_dir>/logs`), `"project"` the folder of the project `project_id` (the ids rule; 404 for a project that is not there, 422 without `project_id`). Anything else is 422. The folder is shown in the system file manager as the printables folder is (`POST /api/setup/printables/open`); a missing settings or log folder is created first. No field of the body is a path, and any other field is ignored.
- A file manager that does not start is 409 "The folder could not be opened." (hint: try again).
- **The log:** while the app runs, everything the engine logs at "info" and above also goes to `<data_dir>/logs/studio.log` (2 MB, then `studio.log.1` to `.3`). The terminal shows what it showed before (`ohs app -v`). `GET /api/log/file` is a header (the app's version, the computer, the compute device) and the newest 4 MB of the log files, oldest first; "Nothing was logged yet." when there is none. It is built on request; nothing is written.

## What a scan keeps

Freeing a scan's space on purpose (FEATURE_PLAN R2, `ohs/server/keep.py`). Only the user moves a scan, per scan or for a whole project, after the app showed what is freed and what can no longer be done; nothing is removed on a timer or as a side effect. New scans keep everything (`full`), the owner's default (D4). Removal is real removal (not the trash): the point is to free space.

| Mode | Keeps | Removes |
|---|---|---|
| `full` | everything | nothing |
| `engineering` | `recording/`, `result/`, everything else | every folder under `process/` but `cache/` (the stages, and left-over `*.tmp-*` / `*.old-*` folders), `process/selections/`, `process/cache/` (the current step's file stays only when `result/mesh.ply` does not hold that step bit for bit) |
| `standard` | `result/` (mesh with its sigma, STL, OBJ, laser points, `trajectory.csv`, `report.json`, `marker_map.json`), `recording/calibration.json` and `recording/meta.json`, the kept features, the thumbnail, `process/state.json`, `process/straighten.json`, every other file | what `engineering` removes, and the rest of `recording/` (frames, `frames.csv`, `imu.csv`, `events.csv`, `ground_truth/`). Cannot be undone |

Both lighter modes make the current mesh the base (`base.in_result` in `state.json`: it is read from `result/mesh.ply`, which stores the vertices, normals and sigmas as float32 and the filled flags as uchar, so it holds the step bit for bit; the first new edit writes its cache file before the result changes. When the result does not equal the step's cache file, as for a scan taken over from before the studio, that file stays and `base.key` names it. `base.applied` lists the edits that made it): its version, its sigmas and its filled flags do not change, so kept features stay current; edits and their undo work from there; undo stops there.

### Types

```ts
type KeepMode = "full" | "engineering" | "standard";
// Scan gains:  keep: KeepMode ("full" until freed); freed?: {when, keep, bytes}[]; has_recording: boolean (frames.csv on disk now)

type ScanKeepPlan = {
  scan_id: string; name: string;
  keep: KeepMode;           // what it keeps now
  to: KeepMode;             // what was asked
  ok: boolean;
  refusal: { detail: string; hint: string } | null;   // why it cannot move (busy, no recording, no mesh, nothing to free, a heavier mode)
  bytes: number;            // the scan folder now
  frees_bytes: number;      // the net drop of the scan folder: its bytes now (the row's number) minus after the move; sum(remove) - sum(writes); 0 when refused
  remove: { part: "recording" | "undo" | "stages"; bytes: number; files: number }[];   // what goes, gross per non-empty part
  loses: ("undo" | "stages" | "remake" | "merge" | "refine" | "frames")[];  // what can no longer be done afterwards
  keeps: ("recording" | "result" | "laser_points" | "trajectory" | "report" | "calibration" | "features")[];  // what stays, from disk
  confirm: boolean;         // the move removes a recording: ask once more, then send confirm: true
};
type ScanKeepResult = { scan: Scan; freed_bytes: number; planned_bytes: number };   // freed: the folder before minus after
type ProjectKeepPlan = { project_id: string; to: KeepMode; scans: ScanKeepPlan[]; frees_bytes: number;  // over the scans that are ok
                         n_scans: number; confirm: boolean };                          // scans that free something; any removes a recording
type ProjectKeepResult = { project_id: string; to: KeepMode; freed_bytes: number;
                           results: { scan_id: string; name: string; ok: boolean; freed_bytes: number; error?: { detail: string; hint: string } }[] };
```

### REST

| Method and path | Body | Returns |
|---|---|---|
| `GET /api/projects/{pid}/scans/{sid}/keep?mode=&thin=` | | `ScanKeepPlan`; writes nothing (no processing state is made or taken over, no cache file is rebuilt); 422 for an unknown mode, 404 for an unknown scan |
| `POST /api/projects/{pid}/scans/{sid}/keep` | `{mode, confirm?: bool, thin?: bool}` | `ScanKeepResult`; 409 with the plan's refusal, 409 "Removing this scan's recording cannot be undone." without `confirm` when the plan says `confirm`, 409 "N files could not be removed; ... the scan still opens." when some files were held open |
| `GET /api/projects/{pid}/keep?mode=&thin=` | | `ProjectKeepPlan` |
| `POST /api/projects/{pid}/keep` | `{mode, confirm?: bool, scans?: string[], thin?: bool}` | `ProjectKeepResult`: every scan that can move moves; the others are listed with the reason; 409 without `confirm` when one would lose its recording |

### Rules

- **Busy:** a scan that is recording, processing (its status), or named by any queued or running job (its processing, an align, a merge, a comparison) is refused ("This scan is busy: ..."), and its cache is not touched.
- **Only lighter:** `full` is never a target ("What was removed to free space cannot be brought back from here."); `standard` to `engineering` is refused; the same mode again is allowed when something came back (a mesh made again, new undo steps). A net of zero or less is refused: "There is nothing to free: …". `engineering` needs a recording (a merged scan: "This scan has no recording of its own …", choose the result only); `standard` needs a mesh.
- **Order:** the base is kept (`state.json`), the mode goes into `project.json`, then the recording (`frames.csv` first), the undo files, the stage folders. A file held open is retried for a second, then left: the answer is a 409 with what was freed, the scan opens, and freeing again removes the rest. Links and junctions are neither followed nor counted.
- **After freeing:** edits, undo of new edits, fits, the Check answer, sections, the comparison, the laser map, the report and the downloads work as before; the deviation map starts again from the kept mesh; `process/trajectory.json` reads `result/trajectory.csv` when the track stage's is gone (its `raw` is then the result's path, `straightened` null). Refusals, in plain words: a stage on its own (Straighten, Fuse, Refine with lasers, Refine from the images) without its stage files: 409 "The stage files this needs were removed to free space …" (`engineering`: hint "Press Make mesh again (or run Track first) to make them again from the recording."); everything that reads the frames (Make mesh, Track, Straighten, Fuse, Refine from the images, a recipe with scope `all`) without the recording: 409 "This scan's recording was removed to free space …"; Merge: 409 "The scan "…" cannot be merged: its recording was removed to free space …"; undo past the kept mesh: 409 "There is nothing to undo: the earlier steps were removed to free space.". Align still works (from the meshes; without a recording, a markerless scan's tags are not looked for).
- **Made again:** a scan freed to `engineering` and then processed has stage folders and undo history again. `keep` still says what was removed and `freed` when; what is on disk now is `GET .../disk` and `has_recording`.
- **"Refine with lasers"** needs no recording: only the base stage's mesh and laser points (a merged scan can be refined).

### Only the frames Standard quality uses (R3)

`thin=true` on the four routes above, with `mode=engineering` only (any other mode: 422 "Keeping only the frames Standard quality uses goes with keeping the recording and the result."). It removes what `engineering` removes and the camera frames Standard quality skips: with the default `process.standard_frame_rate = 30`, every 2nd frame of a 60 frames a second recording. The mode stays `engineering` (every rule above for it holds: Make mesh, Track and the stages work as there).

- **What stays:** the frames Standard picks (positions `0, step, 2 step, ...`, the same as `ohs.app.process` picks them, cuts included) with their own `index`, `t_host` and file name; `imu.csv`, `events.csv`, `calibration.json` and `ground_truth/` byte for byte. `frames.csv` is written again with exactly those rows (the same bytes per row); `meta.json` gets `n_frames` and `thinned: {fps, fps_before, step, frames, frames_before}`; the scan entry gets the same as `thinned` (with `when`) and its `n_frames`, and its `freed` record `thin: true`.
- **The promise:** Standard quality on the thinner recording (its rate is now about 30, so its step is 1) processes the same frames with the same motion sensor samples and gives the same mesh, every value bit for bit (every vertex's position, normal, sigma and looks, and every face); the camera path and the laser points are the same files byte for byte. `mesh.ply` may list the vertices in another order: two runs of the same full recording do too, on both compute paths. Best quality still runs, from the frames that stay, and then gives the Standard mesh; the dialog says beforehand what that costs (`loses` has `"best"`; the numbers are in `VALIDATION.md`, "Keeping only the frames Standard uses").
- **Refused** (the plan's `refusal`, 409 on `POST`), because Standard would pick other frames from the thinner recording: frames picked by motion (`process.frame_motion_deg` > 0), `process.frame_step` other than 1, `process.standard_frame_rate = 0`, or a thinner recording whose rate would not give a step of 1. A recording at 30 frames a second or fewer: "Standard quality uses every one of them, so there are no frames to remove". A scan that keeps its result only: as for `engineering`.
- **Asked twice:** the plan's `confirm` is true when frames would go; without `confirm: true` the move answers 409 "removing the frames Standard quality skips cannot be undone".
- **Order:** the base and the mode (and `thinned`) as for `engineering`, then the shorter `frames.csv` (temporary file + rename: from then on the recording holds the frames that stay), then `meta.json` the same way, then the frame files, the undo files and the stage folders. A frame file left behind (held open) is no longer listed, so nothing reads it; the same move again removes it (`remove` then lists only those files) and does not write `meta.json` again.
- **Thinned already:** with `thin=true` again, nothing more goes from the recording (`confirm` false; stage files that came back go as for `engineering`). `mode=full` stays refused.

```ts
// ScanKeepPlan gains: thin: boolean; thinned: { fps, fps_before, step, frames, frames_before } | null  (what the recording
//   holds now and would hold afterwards; null when refused or without thin);
//   remove[].part may be "frames"; writes[].what may be "frames_list" (negative) or "meta"; loses may have "best".
// ProjectKeepPlan and ProjectKeepResult gain: thin: boolean.
// Scan gains: thinned?: { when, fps, fps_before, step, frames, frames_before }; freed[].thin?: true.
```

## Presets

FEATURE_PLAN P2 and P3. A preset is a named bundle of values the app already lets the user set; "What are you scanning?" on the Scan page picks one. **A value a preset does not name is General's, and General names none**: a scan with General, a scan made before presets and a scan whose preset is gone all run exactly as before presets (`ohs.app.presets`, `ohs.server.presets`).

Built in (from P1's measurements, `VALIDATION.md` "Presets: which settings matter, measured"): `general` ("General", the default) and `thin-sheet-small-parts` ("Thin sheet metal and small parts": `voxel_mm: 0.5`, nothing else). The user's own are JSON files in `<data_dir>/presets/`, kept like recipes.

### Types

```ts
type PresetKey = "mode" | "quality" | "voxel_mm" | "thin_walls" | "recipe" | "check_tolerance_mm";
type PresetValues = {            // each optional; checked as the app checks it elsewhere
  mode?: "markerless" | "markers";            // the Scan page's tracking mode
  quality?: "standard" | "best" | "preview";   // Make mesh / Track when the request names none ("full" = standard)
  voxel_mm?: number;                           // 0.25 to 10: the mesh detail of Track (and the stages after it)
  thin_walls?: "auto" | "on" | "off";          // in place of [process] thin_walls, for this scan
  recipe?: string;                             // a recipe's id: Make mesh runs it in place of Default
  check_tolerance_mm?: number;                 // above 0, at most 100: what the Check panel starts with
};
type Preset = { id: string; name: string; builtin: boolean; description: string;
                card: { changes: string; why: string; costs: string | null } | null;   // built-ins: the hover card
                values: PresetValues; created?: string | null };
type ScanPresetKept = { id: string; name: string; hand: PresetValues };   // Scan.preset (absent: made before presets)
type ScanPreset = {               // GET .../preset
  id: string; name: string;
  preset_values: PresetValues;    // what the preset names (empty for General and for a preset that is gone)
  hand: PresetValues;             // values changed by hand for this scan
  changed: PresetKey[];           // hand values that differ from the preset's
  values: Record<PresetKey, value | null>;   // every value it resolves to; null = General's (see `general`)
  note: string | null;            // why the scan is made as General: made before presets, or its preset is gone
  before_presets: boolean; missing: boolean;
  general: Required<PresetValues>;           // General's value of each key for this scan
  labels: Record<PresetKey, string>; keys: PresetKey[];
};
```

### REST

| Route | Body | Reply |
|---|---|---|
| `GET /api/presets` | | `Preset[]` (built-ins first, then the user's by name) |
| `POST /api/presets` | `{name, description?, values}` | `Preset`. `422` for a value the app does not take (an unknown key, the depth range or the gap guard, a value out of range, an unknown recipe), in plain words; `409` for a built-in's name; `400` for an empty or too long name. |
| `DELETE /api/presets/{id}` | | `204` (user presets only; `409` for a built-in, `404` when unknown). Scans that carry it are made as General from then on, and say so. |
| `GET /api/projects/{pid}/scans/{sid}/preset` | | `ScanPreset` |
| `PUT /api/projects/{pid}/scans/{sid}/preset` | `{preset_id?, values?}` | `ScanPreset`. Another preset (its own values; the hand changes start over) and / or values changed by hand (`null`: back to the preset's; the preset's own value is no change). The next Make mesh and Track use them. `409` while the scan records or is being processed; `422` for `mode` (it cannot change after scanning) and for a value the app does not take; `404` for an unknown preset or scan. |

### Rules

- **Start** (`POST /api/scan/start`): `preset` (default `general`) and `preset_values` (the values changed by hand on the Scan page). A start `mode` other than the preset's `mode` is a hand change. The recording's `meta.json` gets `preset: {id, name, preset_values, hand, changed, values, note}` (every value it resolved to; `null` = General's, decided when the mesh is made); the scan entry gets `preset: {id, name, hand}`.
- **Processing:** the values that act are the preset's named values with the hand changes on top, looked up again when each stage runs. `voxel_mm` acts on Track when the request names none (Straighten and Fuse follow the track's voxel, as before; Fuse with a `voxel_mm` still wins); `thin_walls` on every stage that fuses; `quality` when the Make mesh or Track request names none; `recipe` on Make mesh (a recipe that is gone: Default, with a warning). A value a request names is a choice by hand and wins.
- **Report:** every stage's `report.json` (and so `result/report.json`) gets `preset: {id, name, preset_values, hand, changed, values, note, used}`; `used` holds the values this mesh was made with (`mode`, `quality`, `voxel_mm` from the mesh, `thin_walls`, and the `recipe` and `check_tolerance_mm` Make mesh and the Check panel take for this scan). The HTML report shows it as "Preset" under how the scan was made.
- The depth range is not a preset value: the app does not let the user set it.

## Found on the real engine: the server's side (UX_PLAN R2 to R23, 2026-11-21)

What the walk on the real engine changed in the API, item by item; each is written out where it belongs.

- **R2, Make mesh again builds beside the result:** "API deviations (M3)", Make mesh: `.remake-<job id>/`, the job's *extra* `mesh_kept`, `ProcessState.making`. A job's `state` now reads `running` until its completion handler has run (the scan's status, the notices and `mesh_kept` are settled when a page sees it ended).
- **R5, the graphics card's path in every stage:** "Calibration health", `health_path`.
- **R6, R8, the report's calibration and a merged scan:** "The report", "The calibration it was made with" and "A merged scan".
- **R7, a merge's cleaning always said:** `MergeResult.cleaning`.
- **R9:** while a calibration result waits in Setup to be saved, every refusal that said "A calibration is running." (a scan start, a connect, the camera settings, the lasers' settings, the self-test) says "A calibration result is waiting in Setup: save it or close it first.", hint "Open Setup to review the result, then save it or close it.".
- **R14:** a replayed recording (or the simulator's) that cannot be read any more ends the connection with the notice "The recording could not be read any more (its folder was moved or deleted). The scan was stopped and saved." (the last sentence when a scan ran). No notice is titled "Scanner problem:" any more; a camera that stops still says "The camera stopped sending pictures. Check its USB cable, then press Connect again.".
- **R17, R18:** `StageInfo.started`, `StageInfo.seconds`, `ProcessState.made`, and the shares of a Make mesh (below).
- **R20:** a scan started from a replay without a calibration is refused with "This recording has no calibration, so it cannot be scanned from.", hint "Replay a recording that was made with a calibration, or connect the scanner or the simulator." (not "Calibrate it in Setup").
- **R21:** the rename refusal with `suggestion`, and a merge's default name ("Rename a scan, the trash").
- **R22:** Straighten's wording when it closed loops and changed nothing; one running stage at a time.
- **R23:** `EditEntry.removed.largest_rule`, the summary's rule, and `GET .../process/edits/{edit_id}/before.ply` and `after.ply` ("Edits", `remove_floaters`).
- **R12, R19, R20, texts the server and the report make** (the pages' copies of them, in `api/mock`, follow):

| Where | Before | Now |
|---|---|---|
| Progress of a stage | "Following the camera: frame 23 of 1321 on the GPU (gfx1035)" | "Following the camera: frame 23 of 1,321" |
| Progress of a stage | "…: all 1321 frames done, making the mesh" | "…: all 1,321 frames done, making the mesh" |
| Straighten's progress | "Looking for loops in the trajectory", "Straightening the trajectory" | "Looking for loops in the camera path", "Straightening the camera path" |
| Refine with lasers' progress | "Refining: building the correction field", "Refining: fitting the correction field" | "Refining: working out where the surface must move", "Refining: fitting the surface to the laser lines" |
| Refine from the images | "…, on the GPU); …", "(… of 5, on the CPU: this takes minutes)", "reading frame 3 of 120" | "…, on the graphics card); …", "(… of 5, without a graphics card: this takes minutes)", "reading frame 3 of 120" with separators |
| Smooth's label | "Smooth (bilateral, 50 %, edges kept, away from edges)" | "Smooth 50 % (keeping the shape, sharp edges kept, off edges)"; Taubin: "evenly" |
| Remove the table, a merge's table, the table found | "(12.3 cm², …)", "(123.4 cm² of its mesh, …)" | "(1,234 mm², …)", "(12,340 mm² of its mesh, …)" |
| The cameras' result | "Reprojection error", "Baseline vs truth", "Principal point vs truth", "100% of the image" | "How well the corners fit", "Camera distance vs truth", "Image centre vs truth", "100 % of the image" |
| The cameras' guide | "Hold still for half a second at each pose." | "… at each view." |
| Self-test, camera | "2560x800 (2 x 1280x800) from the simulator" | "two pictures of 1,280 × 800 side by side, from the simulator" |
| Self-test, picture | "mean 28 DN, 0.1% saturated", "35% of the pixels are saturated" | "average brightness 28 of 255, 0.1 % too bright", "35 % of the pixels are too bright" |
| Self-test, motion sensor | "\|accel\| 9.79 m/s^2" (… "(expected about 9.81 m/s^2)") | "gravity reads 9.79 m/s²" (… "(about 9.81 m/s² at rest)") |
| Setup's camera note | "(2560x800)" | "(2,560 × 800)" |
| Report, calibration | "Missing: warning no calibration for …", "Last board check: passed: The check passed: …", "baseline 60.012 mm", "last changed …" | the Laser lines row's "Not calibrated: the … laser line of this scanner.", "Calibration check: The check passed: …", "the cameras 60.01 mm apart", "saved …" |
| Report, how it was made | "Built from a given camera path (straightened, or rebuilt from the tracked path)" | "Built from the camera path that Follow the camera found (straightened, when Straighten corrected it)" |
| Report, Scan check | "Not measured: less than 0.1 %: filled in, nothing measured it" | "less than 0.1 % (filled in: nothing measured it)"; "Measured too loosely" likewise |
| Report, areas | "278857 mm²" | "278,857 mm²" |
| Report, processing time | "Processing time 7 min 27 s" | "… for the whole Make mesh, every stage and edit" (or "… for the last stage that made this mesh (the stages before it and the edits not counted)") |

## The thin-parts hint (ENGINE_PLAN X5, since 2026-11-22)

At the end of Make mesh (and of a recipe run on everything) the server looks at the finished
mesh for thin walls seen from both sides, a thin plate lying flat, and small round holes
(`ohs.mesh.thin_parts.find_thin_parts`). It only reads; a finder that fails is logged and
leaves the field out.

```ts
type ThinPartsHint = {
  suggest: boolean;            // true only when thin walls are found and the mesh was not already made fine
  why_not: "made_fine" | "nothing_found" | "no_measured_gain" | "empty" | null;
  found: ("walls" | "plates" | "holes")[];
  voxel_mm: number; preset_id: string; dismissed: boolean;
  words: { title: string; intro: string; found: string[]; gain: string[]; cost: string[]; measured: string;
           action: string; preset_id: string; preset_name: string } | null;   // null when not suggested
};
// ProcessState.made.thin_parts?: ThinPartsHint | null
```

- **The rule:** the preset "Thin sheet metal and small parts" is suggested only for thin walls,
  where a gain was measured. With only a plate or holes found: `suggest: false`,
  `why_not: "no_measured_gain"`, `words: null`. With walls and a plate or holes, `words.title`
  is "Thin walls found" and the plate's and holes' lines follow the walls'.
- `result/report.json` gets `thin_parts`, the whole finding: `version, voxel_mm, preset, limits,
  found, suggest, why_not, walls {found, area_mm2, share, reads_mm, reads_range_mm, estimate_mm,
  n_regions, regions[], all_readings_mm2, rays}, plates {found, table, plates[{centre, size_mm,
  area_mm2, height_mm, scatter_mm, share_of_part, table_limit_mm}], part_area_mm2, top_area_mm2,
  h99_mm}, holes {found, count, smallest_mm, largest_mm, features[{centre, diameter_mm, pm_mm,
  points}], wall_points}, bricks {size_mm, n, keys, complete}, ms`.

| Route | Body | Returns |
|---|---|---|
| `POST /api/projects/{pid}/scans/{sid}/process/hints/thin-parts/dismiss` | none | `ProcessState`: the hint is put away for this scan; the scan's entry keeps `hints_dismissed: ["thin_parts"]`, so it stays away after Make mesh again. 404 for an unknown scan. |

- The preset's action is `PUT .../preset {preset_id: "thin-sheet-small-parts"}` and then
  `POST .../process` at the same quality. Nothing happens by itself.

### "Make only the thin walls finer" (ENGINE_PLAN X5 step 2, a choice per scan, since 2026-11-22)

- `POST /api/projects/{pid}/scans/{sid}/process` takes `fine_walls?: boolean`. When given it
  is stored in the scan's entry (`fine_walls`) once the request is taken, and used by this and
  every later Make mesh and recipe on everything; absent, the stored choice stays.
  `[process] fine_walls` (bool, default false) is the default for scans without a stored
  choice.
- **When it runs:** only with the mesh model on the graphics card, at mesh detail exactly
  1 mm, not at Preview, and when the scan's drive has the kept depth's estimate (the frames
  Follow the camera will process x 8 bytes a pixel, about 7.8 MB a frame at 1280 x 800) and
  2 GB more free. Otherwise it is skipped before Follow the camera, with the reason. It never
  fails a Make mesh; the kept depth (`track/fused_depth/` in the run's folder) is removed in
  every case.
- The hint: `words.fine?: {action, intro, gain: string[], cost: string[], disk_mb: number} |
  null` is the first choice ("Make only the thin walls finer"); null: the preset alone, as
  before. The page's action for it is `POST .../process {quality, fine_walls: true}`.
  `ThinPartsHint.why_not` gains `"walls_made_finer"`: such a mesh is not suggested again, and
  its `walls.estimate_mm` is null.
- `ProcessState.made.fine_walls?: {chosen: true, acted: boolean, why, share, regions, seconds,
  kept_mb, need_mb?, free_mb?, voxel_mm?, line: string, action?: string}`, only when the scan
  chose it. `why` (when it did not act): `"no_thin_walls" | "no_kept_depth" |
  "no_graphics_card" | "no_finer_surface" | "mesh_detail" | "preview" | "disk" | "failed"`.
  `line` is what the page shows in the hint's place; `action` ("Make the mesh again without
  this", only when it acted) is `POST .../process {quality, fine_walls: false}`.
- `ProcessState.stages.fine_walls?: StageInfo & {source, why, share_pct, regions, kept_mb}`
  and `making.stages.fine_walls`, only once it ran; `making.plan` may hold a `fine_walls`
  part; `base.stage` may be `"fine_walls"`, and so may the `source` of `refine` and
  `spacetime`. `made.stages` holds its seconds.
- `result/report.json` gets `fine_walls`: the engine's block (`walls, bricks, frames,
  faces_in, share, triangles_before, triangles_after, vertices, moved_mm, seam_mm, ms`) with
  the fields of `made.fine_walls` (without `line` and `action`).
