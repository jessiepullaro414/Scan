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
| `POST /api/projects/{pid}/scans/{sid}/process` | `{quality:"preview"\|"full"}` | `Job` |
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
- `status`: `tracking.code` (machine-readable: `no_depth`, `few_inliers`, `low_overlap`, `degenerate`, `rewound`, ...), `laser_points_frame`, `latency_ms` (frame captured to result published), `process_ms` (pipeline time of the frame), `dropped_frames` (frames the live pipeline skipped; every frame is still recorded).
- `DeviceState`: `error` (the last device failure), `warnings` (strings), `imu_stream` (bool), `camera.info.measured_fps` / `frames` / `frame_size`, `pico.imu` / `port` / `error`.
- `SessionState`: `name` (the scan name) while a scan is active. The `POST /api/scan/rewind` reply also has `rewind: {t0, t1, seconds, dropped_frames, kept_frames, rebuilt_keyframes, rebuild_s}`.
- `Scan`: `thumbnail_url` (or null), `error` (after a failed processing), `quality` (of the last processing). `ProjectSummary.thumbnail_url` is the newest scan's thumbnail.
- `Job`: `project_id`, `scan_id` and `quality` for `process_scan`. `result` is `{project_id, scan_id, scan}` for `process_scan` and `{path, frames, image_size}` for `generate_demo`.
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
  - `lasers` is done with both planes and a warning with only one.
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

---

## M3 phase 2: processing studio

The Process page of a scan (see the M3 plan in [`DESKTOP_APP_PLAN.md`](DESKTOP_APP_PLAN.md)). It adds to M1 and M2 and changes nothing in them.
- `POST .../process` ("Make mesh") keeps its contract. It now runs the **Default** recipe at the requested quality.
- `health.api` and `hello.version` become `"0.3.0"`.

**Model.**
- A scan's processing is three **stages**: `track`, `straighten`, `fuse`. Each ends in a fused **base mesh**.
- An ordered list of **edits** applies to the base mesh. `cursor` is how many of them are active; undo and redo move it.
- The **current mesh** is the base plus the active edits. It is always written to the scan's `result/mesh.ply` (and `mesh.stl`, plus `mesh.obj` when configured), so the M1 viewer and downloads show the current version.
- Re-running a stage replaces the base and **clears the edits**: face indices no longer match. The UI confirms first.
- Files live in `scans/<sid>/process/`; their layout is the backend's choice. It records it under "API deviations (M3)".

### Types

```ts
type StageState = "none" | "running" | "done" | "failed" | "skipped";
type MeshOp = "remove_floaters" | "fill_holes" | "smooth" | "decimate" | "delete_faces" | "keep_faces";
// params per op:
//   remove_floaters { min_triangles?: number; min_size_mm?: number; keep_largest_only?: boolean }
//   fill_holes      { max_hole_mm: number }                       // largest hole perimeter to fill
//   smooth          { method: "taubin" | "bilateral"; strength: number /* 0..1 */; keep_edges?: boolean }
//   decimate        { target_triangles?: number; ratio?: number }  // one of the two
//   delete_faces / keep_faces { faces: string }                    // base64 of little-endian uint32 face indices
//                                                                  // of the CURRENT mesh
type MeshStats = {
  triangles: number; vertices: number; components: number;
  holes: number;                      // boundary loops except the outer border (the longest loop)
  largest_hole_mm: number | null;     // perimeter of the largest hole
  border_mm: number | null;           // perimeter of the outer border
  boundary_edges: number; non_manifold_edges: number; watertight: boolean;
  bbox_mm: [number, number, number]; area_cm2: number; volume_cm3: number | null;   // volume only when watertight
};
type EditEntry = { id: string; op: MeshOp; params: Record<string, unknown>;
                   label: string;               // "Fill holes up to 12 mm"
                   created: string; triangles_after: number; ms: number };
type StageInfo = { state: StageState; summary: string | null; when: string | null; job_id: string | null };
type ProcessState = {
  project_id: string; scan_id: string;
  stages: {
    track: StageInfo & { quality: "preview" | "full" | null; tracked_pct: number | null };
    straighten: StageInfo & { loops: number | null; correction_mm: number | null; correction_deg: number | null };
    fuse: StageInfo & { voxel_mm: number | null };
  };
  base: { triangles: number; vertices: number; stage: "track" | "straighten" | "fuse" } | null;
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
  settings: { quality: "preview" | "full"; straighten: "auto" | "off"; voxel_mm: number | null };
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

# ohs/track/posegraph.py (new)
straighten_trajectory(rec_path, calib, trajectory, *, params=None, depth_params=None, progress=None,
                      cancelled=None) -> tuple[dict[int, np.ndarray], dict]
#   trajectory: [(frame_index, t_host, ok, T_wc 4x4)] as process_recording writes trajectory.csv
#   returns the corrected T_wc for every tracked frame and a report:
#   {keyframes, loop_candidates, loops_accepted, correction_mm, correction_deg, loops: [(i, j)],
#    verdict (plain words), ground_truth: {before, after} when the recording has true poses}

# ohs/app/process.py: process_recording(..., poses: dict[int, np.ndarray] | None = None)
#   fuses at the given poses (no tracking) when poses are passed
```

## API deviations (M3)

The backend lists here what it had to decide.
