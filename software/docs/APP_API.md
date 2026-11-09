# OpenHandScan Studio: app API (v1, milestone 1)

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
