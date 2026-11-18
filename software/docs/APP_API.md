# OpenHandScan Studio: app API (v2, milestones 1 and 2)

Milestone 1 defined everything up to [M2: setup and calibration](#m2-setup-and-calibration), which adds to it without changing it.

This is the contract between the backend (`software/ohs/server/`, FastAPI) and the UI (`software/ui/`, React).

- Server: `http://127.0.0.1:8765`. REST is under `/api`, the WebSocket at `/api/ws/live`, and the built UI is served at `/`.
- JSON uses snake_case.
- Lengths are in **mm**, times in seconds, timestamps in ISO 8601.

**Errors:** 4xx/5xx responses carry JSON `{"detail": "what happened", "hint": "what to do"}`. `hint` is optional.

## Types

```ts
type Emitters = { vertical: number; horizontal: number; dots: number };          // percent 0..100
type DeviceState = {
  source: "none" | "camera" | "replay" | "sim";
  connected: boolean;
  busy: string | null;                                   // e.g. "Generating simulator demo…"
  camera: { connected: boolean; info: Record<string, unknown> } | null;
  pico: { connected: boolean; firmware: string | null; emitters: Emitters } | null;
  calibration: { loaded: boolean; path: string | null; image_size: [number, number] | null;
                 baseline_mm: number | null; lasers: string[]; imu: boolean };
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
| `GET /api/health` | | `{app:"openhandscan", version, engine:{opencv, open3d}}` |
| `GET /api/device` | | `DeviceState` |
| `POST /api/device/connect` | `{source:"camera"\|"replay"\|"sim", recording?:string, loop?:bool}` | `DeviceState`. For `sim`, the first time a job builds the demo recording: returns **202** with `{job: Job}`, and the device connects itself when the job finishes. |
| `POST /api/device/disconnect` | | `DeviceState` |
| `POST /api/device/emitters` | `Emitters` (partial allowed) | `DeviceState`. Ignored for replay and sim. |
| `GET /api/projects` | | `ProjectSummary[]`, newest first |
| `POST /api/projects` | `{name}` | `Project` |
| `GET /api/projects/{pid}` | | `Project` |
| `PATCH /api/projects/{pid}` | `{name}` | `Project` |
| `DELETE /api/projects/{pid}` | | `204`. The folder moves to `<projects>/.trash/` and is never hard-deleted. |
| `GET /api/scan` | | `SessionState` |
| `POST /api/scan/start` | `{project_id, mode?, name?}` | `SessionState`. `409` if no device is connected, no calibration is loaded, or a scan is already running. |
| `POST /api/scan/pause` / `resume` | | `SessionState` |
| `POST /api/scan/rewind` | `{seconds: 2.0}` | `SessionState`. Drops the last N s from the live model and records a `cut` event, which offline processing skips. |
| `POST /api/scan/stop` | | `{scan: Scan}` (the recording is finalised) |
| `POST /api/projects/{pid}/scans/{sid}/process` | `{quality:"standard"\|"best"\|"preview"}` (default `"standard"`; `"full"` = `"standard"`) | `Job` |
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
- `status`: `tracking.code` (machine-readable: `no_depth`, `few_inliers`, `low_overlap`, `degenerate`, `rewound`, ...), `laser_points_frame`, `latency_ms` (frame captured to result published), `process_ms` (pipeline time of the frame), `dropped_frames` (frames the live pipeline skipped; every frame is still recorded), `record_backlog` (frames waiting to be written to disk: the recorder's queue plus unwritten PNGs; 0 when idle; the recorder logs one warning past 120), `gpu_fallback` (`null`, or `{stage: reason}` once a GPU stage failed in the running scan and went on on the CPU, e.g. `{"depth": "out of memory"}`, stages `depth` and `track_filter`; kept until the scan stops; the server also sends one `log` message with level `warn` when it first happens).
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
  last_check: { when: string; ok: boolean; verdict: string } | null;
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
  headline: string;        // e.g. "Excellent stereo calibration"
  verdict: string;         // one or two plain sentences: what happened and what it means
  metrics: CalibMetric[];  // plain-language rows: "Reprojection error 0.21 px (want < 0.5 px)"
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
  - After saving to the device target, a connected camera reloads the calibration (`device` message).
- **Launch check:**
  - After each connect with a calibration, the device compares the rectified rows of left and right features on a few idle frames.
  - It stays silent unless they disagree (`launch_check.state: "drift"`). Then a `warn` log and a `DeviceState.warnings` entry suggest running the check.
  - `"unknown"` (too few features, e.g. a blank wall) is also silent.

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
- The drift warning reads "The cameras may have moved since the calibration … Run the 2-minute check (Setup, step 7)."

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
  - `CalibSession.emitters` reads, for example, "Line lasers on: Class 3R, never look into the beams", "Emitters off: ambient light only", "No Pico: the line lasers stay off, …" or "Simulated line lasers on".
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
    - A positive `T_x` (swapped cameras) is `failed`, with the advice to turn on swap left/right and start over.
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
                   label: string;               // "Fill holes up to 12 mm"
                   created: string; triangles_after: number; ms: number };
type StageInfo = { state: StageState; summary: string | null; when: string | null; job_id: string | null };
type ProcessState = {
  project_id: string; scan_id: string;
  stages: {
    track: StageInfo & { quality: "standard" | "best" | "preview" | null; tracked_pct: number | null };
    straighten: StageInfo & { loops: number | null; correction_mm: number | null; correction_deg: number | null;
                              crossings?: number | null };   // laser crossings used (M3 phase 3)
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
#   report: {frames, crossings, miss_mm: {before, after}, holdout: {before_mm, after_mm, ratio}, moved_mm,
#            moved_deg, changed, reason (adjusted | no_lasers | few_crossings | not_better), verdict, ...}

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
- `busy` reads "Tracking the scan…", "Straightening…", "Fusing…", "Refining from the images…", "Refining with the laser lines…", "Making the mesh…", "Applying the recipe Body panel…", "Removing floaters…", "Filling holes…", "Smoothing…", "Simplifying…", "Deleting the selection…" or "Keeping the selection…".
- `job` is the scan's running processing job, or null. A `process` message is also sent with every update of that job (at most 5 per second), so its `job.progress` is current.
- Stages:
  - `when` is when the stage last finished; `job_id` is the job that ran it (for a recipe or Make mesh, that job's id).
  - `track.tracked_pct` is 0–100. *extra*: `track.voxel_mm`, the voxel size a recipe asked for (null = the quality's default).
  - `straighten.loops` is the engine's `loops_accepted`; `correction_mm` / `correction_deg` are its largest change of a camera position / orientation.
  - `fuse.voxel_mm` is the voxel size actually used.
  - A new track resets straighten, fuse and both refinements to `none`, a new straighten resets fuse and the refinements (they built on the old poses), a new fuse resets the refinements, and a new `spacetime` resets `refine`.
- A cancelled stage returns to its previous state, and its files are untouched. A failed stage is `failed` with the reason as its `summary`; the base and the edits stay.

**Stages**
- **The base keeps every piece.** The M1 filter that dropped pieces under 1 % of the triangles is no longer part of tracking, so removing floaters is an edit you can see and undo. The Default recipe removes the same pieces (see Recipes).
- `POST .../process/track`: `quality` defaults to `"standard"`; anything but `"standard"`, `"best"`, `"preview"` and `"full"` is 422.
- `POST .../process/straighten`: the body is optional. It runs `ohs.track.posegraph.straighten_trajectory` on the track stage's trajectory, then fuses at the corrected poses (`process_recording(poses=...)`). The stage is `skipped`, and the base and edits stay, when:
  - the engine's loop closure is missing ("Straightening is not available in this version of the engine, so the scan was left as it is") or fails ("Straightening failed (…), so the scan was left as it is");
  - it accepts no loop;
  - its loops show that the passes already line up (report `changed: false`).

  The `summary` is then the engine's verdict, e.g. "No loops found: to let Studio straighten a scan, pass over an earlier part again.". When straightening worked, the stage is `done` and its summary is the verdict, e.g. "Closed 4 loops: the end moved 1.8 mm into place.".
  - **Laser crossings** (M3 phase 3, research plan 7.3): after the loops, Straighten lines the poses up where the laser lines of different frames cross (`ohs.track.curve_ba.laser_curve_adjustment`, with the loops' fuse, or the track's mesh without loops, as the reference surface). It changes the poses only when, fitted on half the crossings, the other half meet clearly better; then it fuses again. So the stage can be `done` without loops: "No loops found, but the laser lines cross: lined the scan up on 597 laser crossings (median miss 0.10 -> 0.04 mm, the camera moved by up to 1.1 mm)". With loops, that part follows the loops' verdict after "; then". *extra*: `straighten.crossings`, the crossings used (null when they changed nothing). `straighten.json` gets a `laser_curves` section with the full numbers.
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
  - It resets `refine`: run it before Refine with lasers. Recipes and Make mesh don't run it (a trial build).
- **Laser points** are also checked per frame against that frame's dense depth (`ohs.vision.laser.depth_check`: within 4 sigma, at least 1 mm, at the point's pixel or 6 px to a side). The report counts the dropped ones as `lasers.n_rejected_by_depth`.
- **Scan status:** stage jobs and recipe `all` jobs set the scan to `processing`. Afterwards it is `processed` when it has a mesh, else `failed` (with `error`) or `recorded`. Edit jobs leave the status alone.
- **Make mesh** (`POST .../process`) is the Default recipe with scope `all` at the requested quality. Its job kind (`process_scan`), result `{project_id, scan_id, scan, message: "Mesh ready"}` and statuses are M1's.
  - Second Make mesh on the same scan: 409 "this scan is already being processed".
  - Any other processing job running on the scan: 409 "this scan is busy (…)".

**Jobs**
- Kinds: `process_track`, `process_straighten`, `process_fuse`, `process_spacetime`, `process_refine`, `mesh_edit`, `process_recipe` (the contract leaves the recipe kind open) and `process_scan`.
- *extra* job fields: `project_id`, `scan_id`, plus:
  - `quality` (track, Make mesh);
  - `op` (edits);
  - `voxel_mm` (fuse);
  - `recipe_id`, `recipe_name`, `scope` (recipes).
- Results:
  - stage jobs and recipe `all`: `{project_id, scan_id, scan, message}`, where `message` is the stage's summary;
  - `mesh_edit`: `{project_id, scan_id, edit: EditEntry, message}`;
  - recipe `edits`: `{project_id, scan_id, recipe_id, message}`.
- Progress messages read like "Tracking: frame 12 of 696 on the GPU (…)", "Fill holes up to 12 mm: Filling hole 3 of 40" or "Simplify to 200,000 triangles: Simplifying: 412,000 triangles".

**Edits**
- `params` in an `EditEntry` are normalised (defaults filled in). For `delete_faces` / `keep_faces` they are `{"count": n}`: the indices themselves are kept in `selections/`.
- *extra* `EditEntry` fields:
  - `summary`: what the edit did, e.g. "Removed 12 loose pieces (3,410 triangles)", "Filled 3 holes (412 new triangles)", "Moved the surface by 0.012 mm on average and 0.08 mm at most", "1,204,311 -> 200,000 triangles";
  - `job_id`.
- Labels:
  - "Remove loose pieces smaller than 5 mm or under 1 % of the mesh";
  - "Keep the largest piece only";
  - "Fill holes up to 12 mm";
  - "Smooth (bilateral, 50 %, edges kept)";
  - "Simplify to 200,000 triangles", "Simplify to 50 % of the triangles" or "Simplify to within 0.02 mm";
  - "Delete 1,234 triangles" or "Keep 1,234 triangles".
- `remove_floaters`:
  - A piece is a set of faces connected through shared edges; the largest piece is the one with the most triangles. A lone triangle or a flap that touches the surface at one vertex only is a piece of its own (since 2026-11-13; before, pieces were connected through shared vertices and such a flap counted as surface).
  - `min_size_mm` compares the diagonal of the piece's bounding box. *extra* `min_fraction` (0–1) compares the piece's share of all triangles.
  - The given limits combine with "or".
  - `{}` means `{min_size_mm: 5}`, and `keep_largest_only` ignores the other limits.
  - The largest piece is never removed.
- `fill_holes`:
  - `max_hole_mm` is the hole's perimeter.
  - Outer borders are left open (see MeshStats), unless `watertight` (M3 phase 3): then every opening up to `max_hole_mm` is closed, the outer edge too ("Make watertight: close every opening up to 60 mm"). The summary says whether the mesh is now watertight, or how many openings are still larger than the limit.
  - New vertices are added only where the hole is wider than the mesh around it, so pin holes get a few triangles and larger holes a smooth patch. A smooth patch that would leave the hole (more than 1.25 hole radii from its centre) becomes a flat "soap film" instead.
  - **Filled faces are flagged** (M3 phase 3): `mesh.ply` gets the face property `uchar filled` (1 = made by hole filling, not measured), the flags follow every later edit, and `MeshStats.filled_cm2` adds up their area. The viewer and the Process page draw them striped grey, and the viewer's distance tool won't put a point on them.
- `smooth`:
  - `keep_edges` defaults to true; `strength` must be 0–1.
  - Strength sets the work: Taubin `1 + 49 s` iterations; bilateral `2 + 18 s` normal and `5 + 15 s` vertex iterations.
  - `within_sigma` (M3 phase 3; the Process page sends it by default when the mesh has sigmas, and the Body panel and Bracket recipes use it): no vertex moves more than twice its own sigma; a longer move is shortened. Without sigmas it does nothing.
  - `flat_only` (2026-11-16, default false; the Process page's "Where" choice "Flat only"; no recipe uses it yet): each vertex keeps its move only where the surface around it is flat (`ohs.mesh.smoothing`, "Flat surface only"), so edges, corners and small round features stay as scanned. The label says "flat areas only".
  - `away_from_edges` (2026-11-17, default false; the "Where" choice "Off edges"; no recipe uses it): nothing within 1.5 mm of a sharp edge (over 25°) moves, and everything from 3 mm on is smoothed in full, round features too (`ohs.mesh.smoothing`, "Away from edges"). The label says "away from edges". Sent with `flat_only` it is a 422.
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
- Parameter errors are 422 naming the parameter, e.g. "fill_holes: max_hole_mm is missing", "remove_floaters: unknown parameter 'size'".

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
  - `body-panel`: the same, then holes up to 15 mm and bilateral smoothing at 0.5 with `keep_edges`.
  - `bracket`: the same as `default`, then bilateral smoothing at 0.25 with `keep_edges`.
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
  frame_label: string;                          // "the part's frame" | "the scan's own frame"
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
  `Content-Disposition` names them after the scan (`<scan>-fits.step`, `<scan>-fits.FCMacro`), so the two
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
- **No kept fits:** the files answer 409 ("this scan has no kept fits to export", with a hint), never an
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
  scan was made (`result/report.json`, its known keys); the calibration (the setup state's calibration part
  and last board check; its health is "not assessed" until H3); the Check answer at `tolerance_mm`
  (`POST .../check`'s, before there is a mesh left out); the kept features **as `GET .../features` shows
  them** (stale marks, relations with the engine's record, approvals with "check again" and its reason, the
  frame's state) and the CAD export's moves when a relation has one; every kept section cut again on the
  current mesh (with "the mesh has changed" as its warning when it has); the kept comparison with a
  reference (the reference, its units, the alignment and, once aligned, the deviation of the current mesh and
  the answer at the comparison's kept tolerance and `k`; the mesh-changed note goes with the alignment's
  warnings); one kept seam's flush, measured on the current mesh; the pictures. Every part's warnings are
  shown in it and again in the list near the top. The CSV holds the features only; the JSON the features and
  the sections' numbers (not their drawings).
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
};

type TagStart = {            // what the tags did (null: the two scans share no tag, or tags were not looked for)
  shared: number[];          // tag ids both scans saw
  used: number[];            // those the start was made from
  left_out: number[];        // shared tags that did not fit the others (moved between the scans)
  rms_mm: number | null;     // how well the used tags' corners agree
  start: string;             // "accepted", or why the tags gave no accepted start (then the shape's own guesses were used)
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
  name?: string;             // default "<target name> + <source name>"
};

// the `result` of a finished job of kind "merge"
type MergeResult = {
  project_id: string; scan_id: string; scan: Scan; merged_from: [string, string]; message: string;
  tables: MergeTable[];      // since 2026-11-17: one per scan when the part was moved (same_setup false), else []
  warnings: string[];        // since 2026-11-17: what to look at, each naming its scan; [] when the part stayed
  cleaning: string | null;   // since 2026-11-17: the part was moved: in words, the small loose pieces the merge left out
                             // and the small pieces over a thin part it kept (report.json: mesh.cleaning); null otherwise
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
- **Picked points:** each pair is one spot on the part, clicked on both meshes. Three pairs that span a triangle are enough; a millimetre or two of clicking error is fine, the alignment refines it. With pairs the result's `start` is `"given"` and no second guess has to confirm it, so check `pairs_rms_mm` (above about 5 mm two pairs probably do not match).
- **Tags** (since 2026-11-16; `ohs.align.tags`): without picked points, the job looks for AprilTags (`[markers]` dictionary and `tag_mm`) in both scans: a marker-mode scan's own map (`result/marker_map.json`), or in its recording at the poses of its mesh (up to 120 frames, about 1 s per 60 frames at 640x400). When the two share tags that agree, the alignment starts from them (`start: "tags"`, verdict "... from the tags both scans saw.") and keeps that start along the directions the shape pins only weakly: a sphere, a cylinder or a plate is then turned as the tags say. If that start is not accepted, the shape's own guesses are tried as without tags (`tags.start` says why). Tags on the table help only when the part was not moved on it (same setup); for a part that was moved, only tags on the part. A merged scan has no recording, so no tags of its own.
- **Same setup or not:** with `same_setup: true` the table both scans share pins height and tilt. If the two tables turn out more than 5° or 5 mm apart after the part's fit, they are not used (`used_support: false`). With `false` only the part counts, and a merge leaves each scan's table out, and nothing else (since 2026-11-17; before, everything up to 2 mm in front of each table went, with the bottom 2 mm of the part and any part thinner than that lying flat). The job ("Finding what the part lay on") finds each scan's table on its own mesh as "Remove the table" does (`ohs.mesh.support`, module docstring, 4.), and turns it into a map on the table's plane: where the table is bare, a frame's depth and laser points are left out up to 2 mm above it; where the part, a thin part lying flat, or a raised area that may be one stands, nothing above the table is left out. What the frames leave of the table (a ring at the part's foot about 2 mm wide, crumbs) is then taken out of the merged mesh by the single-scan edit's rule (faces facing the table's way within 1 mm of it, where that scan found its table, not on a thin part; loose crumbs within that height), and laser points within 1 mm of it where the part does not stand. Raised areas the single-scan edit only warns about are **kept** in a merge (a patch of table left is better than a part lost) and said; where the table is uneven itself (near a scan's border) its raised areas go with it, said once; a part thinner than the scan's own limit (about 0.2 mm on the simulated tables) cannot be told from the table and goes with it, which the scan's `limit` says. A scan whose table cannot be found on its mesh is clipped as before by the alignment's support plane (2 mm, with the part's bottom edge), or not at all when there is none, and a warning names it. `MergeResult.tables` and `warnings` say all this per scan; the merged scan's `report.json` has it in full (`scans[i].table`: `rule`, `plane`, `flatness_mm`, `table_area_mm2`, `stands_mm2`, `thin_parts` with `verdict` and `in_merge`, `uneven`, `thin_limit_mm`, `limit`, `left_in_mesh` {`faces`, `area_mm2`, `pieces`}, `laser_points_removed`, `summary`, `warnings`; top-level `warnings`; `mesh.n_faces_table_removed`, `lasers.n_points_table_removed`). With `same_setup: true` the merge is unchanged: a plain fusion of the frames, its mesh bit for bit as before, `tables` and `warnings` empty.
- **Merge** fuses the raw frames of both recordings again at the given pose, with the target scan's quality, and takes about as long as processing both scans. The job reports "Merging: frame k of n" and can be cancelled; nothing is added to the project then. On success a new scan appears (`status: "processed"`, `mode: "markerless"`, `n_frames` = frames fused, `merged_from`), with `mesh.ply`, `mesh.stl`, `laser_points.ply` and `report.json` in its result, and a notification "Merged scan ready: <name>". The two scans are unchanged.
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
