/**
 * In-browser mock of the ohs engine (`?mock=1` / `VITE_MOCK=1`): the same REST routes and the
 * same WebSocket stream (text JSON plus the binary preview / mesh frames, encoded exactly as in
 * APP_API.md), so the whole UI can be demoed and tested with no engine and no hardware.
 *
 * - a fake device (camera without calibration, replay, and the simulator, whose first start
 *   builds a "demo recording" through a 202 job);
 * - projects with seeded scans; delete "moves to the trash";
 * - a live scan: a hand-held sweep around a sphere and a gauge block, tracking ok → weak →
 *   lost (ghost pose) → ok, distance in and out of range, previews ray-cast on a canvas, and a
 *   mesh that grows as surfaces are seen (green = well observed, yellow = thin); rewind restores
 *   the coverage of 2 s ago;
 * - meshing jobs with progress, cancel and failure, and result files (PLY / STL / laser points /
 *   report.json) generated on demand;
 * - M2: the setup wizard (setup.ts: self-test, devices, printables, print check, launch check)
 *   and the calibration session (calib.ts: a simulated person moving the board, live overlays,
 *   previews, auto-capture, IMU recording, solving, saving);
 * - M3: the processing studio (process.ts): stages, edits with undo / redo, stats, holes, the
 *   trajectory with loops, recipes; "Make mesh" runs the Default recipe. The meshes are real
 *   (scanmesh.ts, meshops.ts): about 250k triangles at 1 mm and 1M at 0.5 mm.
 *
 * Options in the URL: `empty=1` (no projects), `fast=1` (short jobs), `drift=1` (the launch check
 * after a connect finds drift), `nopico=1` (the camera connects without the Pico).
 */
import type { MockOptions } from "../backend";
import { encodeBinary } from "../binary";
import type { FetchLike } from "../http";
import type { SocketFactory, SocketLike } from "../socket";
import type {
  CalibrationInfo,
  ConnectBody,
  DeviceState,
  Emitters,
  Job,
  ProcessQuality,
  Project,
  ProjectSummary,
  Scan,
  ScanMode,
  SessionState,
  StatusMsg,
  Tracking,
} from "../types";
import { MockCalib } from "./calib";
import { HttpError, json, nowIso, uid, type MockHost, type MockSettings, type TimedJobSpec, type WorkJobSpec, type WorkStep } from "./host";
import { normalizeQuality } from "../../lib/quality";
import { MockProcess, mockStep } from "./process";
import { cameraByIndex, MockSetup } from "./setup";
import {
  apply,
  applyInv,
  axisAngle,
  compose,
  dot,
  invert,
  laserPoints,
  normalize,
  occluded,
  plyPoints,
  rng,
  sceneMesh,
  sub,
  toRowMajor16,
  trajectory,
  transformMesh,
  type MeshData,
  type Pose,
  type V3,
} from "./geom";
import { DEPTH_H, DEPTH_W, LEFT_H, LEFT_W, PreviewRenderer } from "./render";

const VERSION = "0.3.0";

/** A binary body delivered in chunks, so download progress shows like over the network. */
function streamed(buf: ArrayBuffer, type: string, totalMs: number): Response {
  const u8 = new Uint8Array(buf);
  const chunk = Math.max(64 * 1024, Math.ceil(u8.length / 24));
  const n = Math.max(1, Math.ceil(u8.length / chunk));
  const delay = totalMs / n;
  let off = 0;
  const stream = new ReadableStream<Uint8Array>({
    pull(ctrl) {
      return new Promise((resolve) =>
        setTimeout(() => {
          if (off >= u8.length) ctrl.close();
          else {
            const end = Math.min(u8.length, off + chunk);
            ctrl.enqueue(u8.slice(off, end));
            off = end;
          }
          resolve();
        }, delay),
      );
    },
  });
  return new Response(stream, { status: 200, headers: { "content-type": type, "content-length": String(u8.length) } });
}

// ------------------------------------------------------------------ fake socket
class MockSocket implements SocketLike {
  binaryType: BinaryType = "blob";
  readyState = 0;
  onopen: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  /** What this client subscribed to (APP_API.md: both on by default). */
  wantPreview = true;
  wantMesh = true;
  private backend: MockBackend;

  constructor(backend: MockBackend) {
    this.backend = backend;
    setTimeout(() => {
      if (this.readyState !== 0) return;
      this.readyState = 1;
      this.onopen?.(new Event("open"));
      backend.attach(this);
    }, 40);
  }
  send(data: string): void {
    this.backend.onClientMessage(this, data);
  }
  close(): void {
    if (this.readyState >= 2) return;
    this.readyState = 3;
    this.backend.detach(this);
    this.onclose?.(new CloseEvent("close", { code: 1000 }));
  }
  deliver(data: string | ArrayBuffer): void {
    if (this.readyState === 1) this.onmessage?.(new MessageEvent("message", { data }));
  }
}

// ------------------------------------------------------------------ the mock engine
type ScanSim = {
  scan: Scan;
  projectId: string;
  mode: ScanMode;
  T: number; // scanning time
  trajT: number; // trajectory clock (frozen while lost)
  lostAt: number | null;
  counts: Float32Array;
  laser: number;
  snapshots: { T: number; counts: Float32Array; laser: number }[];
  lastSnap: number;
  meshSeq: number;
  meshDirty: boolean;
  lastMeshAt: number;
  ghost: number[] | null;
  lastGhostAt: number;
  pose: Pose; // true camera pose (scene frame)
  tracking: Tracking;
};

const SIM_CALIB: CalibrationInfo = {
  loaded: true,
  path: "~/.ohs/sim/demo/calibration.json",
  image_size: [1280, 800],
  baseline_mm: 60.0,
  lasers: ["horizontal", "vertical"],
  imu: true,
};
const NO_CALIB: CalibrationInfo = { loaded: false, path: null, image_size: null, baseline_mm: null, lasers: [], imu: false };

const TRACK_CYCLE = 32;

type JobMeta =
  | { kind: "sim" }
  | { kind: "timed"; spec: TimedJobSpec; dur: number; t: number }
  | {
      kind: "work";
      spec: WorkJobSpec;
      dur: number;
      t: number;
      p: number;
      message: string;
      commit: (() => Record<string, unknown> | null) | null;
      finished: boolean;
      /** The frame pass of the last step (null between passes) and the job clock at its first frame. */
      frames?: { stage: string; done: number; total: number; t0: number } | null;
    };

class MockBackend {
  private opts: MockOptions;
  private clients = new Set<MockSocket>();
  private device: DeviceState = {
    source: "none",
    connected: false,
    busy: null,
    camera: null,
    pico: null,
    calibration: { ...NO_CALIB },
    replay: null,
    launch_check: null,
    sim_scene: null,
    warnings: [],
  };
  private simReady = false;
  private simJobId: string | null = null;
  private projects = new Map<string, Project>();
  private jobs = new Map<string, Job>();
  private jobMeta = new Map<string, JobMeta>();
  private setup: MockSetup;
  private calib: MockCalib;
  private studio: MockProcess;
  private previewSeqs: Record<string, number> = {};
  private session: SessionState = { state: "idle", project_id: null, scan_id: null, mode: "markerless", elapsed_s: 0, frames: 0 };
  private sim: ScanSim | null = null;
  private hoverClock = 0;
  private lastTick = performance.now();
  private statusAcc = 0;
  private previewAcc = 0;
  private jobAcc = 0;
  private rendering = false;
  private renderer: PreviewRenderer | null = null;
  private previewSeq = 0;
  private liveScene: MeshData;
  private T_ws: Pose; // world (first camera frame of a scan) from scene
  private liveWorld: { positions: Float32Array; normals: Float32Array };
  private files = new Map<string, ArrayBuffer>();
  private rand = rng(7);
  private emitters: Emitters = { vertical: 0, horizontal: 0, dots: 0 };
  /** Scan clock speed (dev handle: 0 freezes a moment, e.g. "tracking lost", for inspection). */
  timeScale = 1;

  constructor(opts: MockOptions) {
    this.opts = opts;
    this.liveScene = sceneMesh(1.0);
    this.T_ws = invert(trajectory(0));
    this.liveWorld = transformMesh(this.liveScene, this.T_ws);
    if (!opts.empty) this.seed();
    const host: MockHost = {
      fast: opts.fast,
      drift: opts.drift,
      send: (m) => this.send(m),
      sendPreview: (camera, jpeg, w, h) => {
        const seq = (this.previewSeqs[camera] = (this.previewSeqs[camera] ?? 0) + 1);
        this.sendBinary(encodeBinary({ type: "preview", camera, format: "jpeg", w, h, seq }, [jpeg]), "preview");
      },
      wantsPreviews: () => [...this.clients].some((c) => c.wantPreview),
      device: () => this.device,
      patchDevice: (p) => {
        this.device = { ...this.device, ...p };
        this.send({ type: "device", ...this.device });
      },
      scanning: () => !!this.sim,
      startJob: (kind, spec) => this.startTimedJob(kind, spec),
      startWorkJob: (kind, spec) => this.startWorkJob(kind, spec),
      job: (jid) => this.jobs.get(jid),
      log: (level, message) => this.send({ type: "log", level, message }),
      settings: SETTINGS as unknown as MockSettings,
    };
    this.setup = new MockSetup(host, { noPico: opts.nopico });
    this.calib = new MockCalib(host, this.setup);
    this.studio = new MockProcess(host, (pid, sid) => {
      const project = this.projects.get(pid);
      const scan = project?.scans.find((x) => x.id === sid);
      return project && scan ? { project, scan } : null;
    });
    setInterval(() => this.tick(), 66);
  }

  private startTimedJob(kind: string, spec: TimedJobSpec): Job {
    const job: Job = { id: uid("j-"), kind, state: "running", progress: 0, message: spec.message(0), result: null, error: null, created: nowIso() };
    this.jobs.set(job.id, job);
    this.jobMeta.set(job.id, { kind: "timed", spec, dur: spec.seconds * (this.opts.fast ? 0.35 : 1), t: 0 });
    this.send({ type: "job", ...job });
    return job;
  }

  private startWorkJob(kind: string, spec: WorkJobSpec): Job {
    const job: Job = { id: uid("j-"), kind, state: "running", progress: 0, message: "Starting…", result: null, error: null, created: nowIso(), ...(spec.extra ?? {}) };
    this.jobs.set(job.id, job);
    this.jobMeta.set(job.id, { kind: "work", spec, dur: spec.minSeconds * (this.opts.fast ? 0.3 : 1), t: 0, p: 0, message: "Starting…", commit: null, finished: false });
    this.send({ type: "job", ...job });
    return job;
  }

  // ---------------------------------------------------------------- transport
  fetch: FetchLike = async (input, init) => {
    const url = new URL(input, location.origin);
    const method = (init?.method ?? "GET").toUpperCase();
    let body: Record<string, unknown> = {};
    if (typeof init?.body === "string" && init.body) {
      try {
        body = JSON.parse(init.body) as Record<string, unknown>;
      } catch {
        return json(422, { detail: "The request body isn't valid JSON" });
      }
    }
    if (!url.pathname.includes("/files/") && !this.opts.fast) await new Promise((r) => setTimeout(r, 70 + Math.random() * 120));
    try {
      return await this.route(method, url.pathname, body);
    } catch (e) {
      if (e instanceof HttpError) return json(e.status, e.hint ? { detail: e.detail, hint: e.hint } : { detail: e.detail });
      console.error("mock backend error", e);
      return json(500, { detail: `Mock engine error: ${(e as Error).message}`, hint: "This is a bug in the demo backend." });
    }
  };

  socketFactory: SocketFactory = () => new MockSocket(this);

  attach(s: MockSocket) {
    this.clients.add(s);
    s.deliver(JSON.stringify({ type: "hello", version: VERSION, app: "openhandscan", app_version: VERSION }));
    s.deliver(JSON.stringify({ type: "device", ...this.device }));
    s.deliver(JSON.stringify({ type: "session", ...this.session }));
    s.deliver(JSON.stringify({ type: "setup", ...this.setup.state() }));
    s.deliver(JSON.stringify({ type: "calib_session", ...this.calib.session }));
    for (const j of this.jobs.values()) if (j.state === "queued" || j.state === "running") s.deliver(JSON.stringify({ type: "job", ...j }));
    if (this.sim) this.sim.meshDirty = true;
  }
  detach(s: MockSocket) {
    this.clients.delete(s);
  }
  onClientMessage(s: MockSocket, data: string) {
    try {
      const m = JSON.parse(data) as { type?: string; preview?: unknown; mesh?: unknown };
      if (m.type !== "subscribe") return;
      const meshBefore = s.wantMesh;
      if (typeof m.preview === "boolean") s.wantPreview = m.preview;
      if (typeof m.mesh === "boolean") s.wantMesh = m.mesh;
      // Coming back to the Scan page: send the current live model again.
      if (s.wantMesh && !meshBefore && this.sim) this.sim.meshDirty = true;
    } catch {
      /* not JSON: ignore, like the engine */
    }
  }
  private send(msg: Record<string, unknown>) {
    const text = JSON.stringify(msg);
    for (const c of this.clients) c.deliver(text);
  }
  private sendBinary(buf: ArrayBuffer, kind: "preview" | "mesh") {
    for (const c of this.clients) if (kind === "preview" ? c.wantPreview : c.wantMesh) c.deliver(buf);
  }

  // ---------------------------------------------------------------- REST
  private async route(method: string, path: string, body: Record<string, unknown>): Promise<Response> {
    const seg = path.replace(/^\/api\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
    const [a, b, c, d, e, f] = seg;
    const is = (m: string, n: number) => method === m && seg.length === n;

    if (a === "health" && is("GET", 1))
      return json(200, { app: "openhandscan", version: VERSION, api: VERSION, engine: { opencv: "5.0.0", open3d: "0.20.0", mode: "demo" } });
    if (a === "settings" && is("GET", 1)) return json(200, SETTINGS);

    if (a === "device") {
      if (is("GET", 1)) return json(200, this.device);
      if (b === "connect" && is("POST", 2)) return this.connect(body as unknown as ConnectBody);
      if (b === "disconnect" && is("POST", 2)) {
        if (this.sim) throw new HttpError(409, "A scan is running", "Stop the scan before disconnecting the device.");
        this.disconnect();
        return json(200, this.device);
      }
      if (b === "emitters" && is("POST", 2)) {
        if (this.device.source === "camera" && this.device.pico?.connected) {
          for (const k of ["vertical", "horizontal", "dots"] as const) {
            const v = body[k];
            if (typeof v === "number") this.emitters[k] = Math.max(0, Math.min(100, v));
          }
          this.device = { ...this.device, pico: { ...this.device.pico, emitters: { ...this.emitters } } };
          this.send({ type: "device", ...this.device });
        }
        return json(200, this.device);
      }
    }

    if (a === "projects") {
      if (is("GET", 1)) return json(200, this.listProjects());
      if (is("POST", 1)) {
        const name = String(body.name ?? "").trim();
        if (!name) throw new HttpError(422, "The project needs a name", "Type a name, e.g. “Intake bracket”.");
        if ([...this.projects.values()].some((p) => p.name.toLowerCase() === name.toLowerCase()))
          throw new HttpError(409, `A project called “${name}” already exists`, "Pick another name, or open the existing project.");
        const p: Project = { id: uid("p-"), name, created: nowIso(), modified: nowIso(), n_scans: 0, thumbnail_url: null, scans: [] };
        this.projects.set(p.id, p);
        return json(200, p);
      }
      const p = b ? this.projects.get(b) : undefined;
      if (b && !p) throw new HttpError(404, "Project not found", "It may have been moved to the trash.");
      if (p && is("GET", 2)) return json(200, p);
      if (p && is("PATCH", 2)) {
        const name = String(body.name ?? "").trim();
        if (!name) throw new HttpError(422, "The project needs a name");
        p.name = name;
        p.modified = nowIso();
        return json(200, p);
      }
      if (p && is("DELETE", 2)) {
        if (this.sim?.projectId === p.id) throw new HttpError(409, "This project is being scanned", "Stop the scan first.");
        this.projects.delete(p.id);
        return json(204, null);
      }
      if (p && c === "scans" && d) {
        const s = p.scans.find((x) => x.id === d);
        if (!s) throw new HttpError(404, "Scan not found");
        // M1 "Make mesh" (now the Default recipe), then the M3 processing studio
        if (e === "process" && is("POST", 5)) return json(200, this.studio.makeMesh(p, s, normalizeQuality(body.quality) ?? "standard"));
        if (e === "process") {
          const r = this.studio.route(method, p, s, seg.slice(5), body);
          if (r) return r;
        }
        if (e === "files" && f && (is("GET", 6) || is("HEAD", 6))) return this.file(p, s, f, method === "HEAD");
      }
    }

    const m3 = this.studio.routeRecipes(method, seg, body);
    if (m3) return m3;

    if (a === "scan") {
      if (is("GET", 1)) return json(200, this.session);
      if (b === "start" && is("POST", 2)) return json(200, this.start(body));
      if (b === "pause" && is("POST", 2)) {
        if (this.session.state !== "scanning") throw new HttpError(409, "No scan is running", "Start a scan first.");
        this.session = { ...this.session, state: "paused" };
        this.send({ type: "session", ...this.session });
        return json(200, this.session);
      }
      if (b === "resume" && is("POST", 2)) {
        if (this.session.state !== "paused") throw new HttpError(409, "The scan isn't paused");
        this.session = { ...this.session, state: "scanning" };
        this.send({ type: "session", ...this.session });
        return json(200, this.session);
      }
      if (b === "rewind" && is("POST", 2)) return json(200, this.rewind(Number(body.seconds) || 2));
      if (b === "stop" && is("POST", 2)) return json(200, await this.stop());
    }

    if (a === "jobs") {
      if (is("GET", 1)) return json(200, [...this.jobs.values()].sort((x, y) => (x.created < y.created ? 1 : -1)));
      const j = b ? this.jobs.get(b) : undefined;
      if (b && !j) throw new HttpError(404, "Job not found");
      if (j && is("GET", 2)) return json(200, j);
      if (j && c === "cancel" && is("POST", 3)) return json(200, this.cancel(j));
    }

    const m2 = this.setup.route(method, seg, body) ?? this.calib.route(method, seg, body);
    if (m2) return m2;

    throw new HttpError(404, `No such endpoint: ${method} ${path}`, "The demo backend implements APP_API.md up to M3 phase 2 (the processing studio).");
  }

  private listProjects(): ProjectSummary[] {
    return [...this.projects.values()]
      .map((p) => ({ id: p.id, name: p.name, created: p.created, modified: p.modified, n_scans: p.scans.length, thumbnail_url: p.thumbnail_url }))
      .sort((x, y) => (x.modified < y.modified ? 1 : -1));
  }

  // ---------------------------------------------------------------- device
  private connect(body: ConnectBody): Response {
    if (this.sim) throw new HttpError(409, "A scan is running", "Stop the scan before changing the device.");
    if (this.calib.active()) this.calib.onDeviceLost(); // a (re)connect ends the calibration session, like a lost device
    const source = body.source;
    if (source !== "camera" && source !== "replay" && source !== "sim")
      throw new HttpError(422, `Unknown source “${String(source)}”`, "Use camera, replay or sim.");
    if (source === "replay") {
      const path = String(body.recording ?? "").trim();
      if (!path) throw new HttpError(422, "Enter the recording folder to replay", "It's the folder with meta.json and frames.csv.");
      if (/missing|nope|does-not-exist/i.test(path))
        throw new HttpError(404, `No recording at ${path}`, "Check the path: the folder must contain meta.json and frames.csv.");
    }
    if (this.device.connected && this.device.source !== source) this.disconnect();

    if (source === "sim" && !this.simReady) {
      let job = this.simJobId ? this.jobs.get(this.simJobId) : undefined;
      if (!job || (job.state !== "running" && job.state !== "queued")) {
        job = { id: uid("j-"), kind: "generate_demo", state: "running", progress: 0, message: "Rendering frame 0 / 120", result: null, error: null, created: nowIso() };
        this.jobs.set(job.id, job);
        this.jobMeta.set(job.id, { kind: "sim" });
        this.simJobId = job.id;
        this.device = { ...this.device, busy: "Generating simulator demo…" };
        this.send({ type: "device", ...this.device });
        this.send({ type: "job", ...job });
      }
      return json(202, { job });
    }
    this.connectNow(source, body);
    return json(200, this.device);
  }

  private connectNow(source: "camera" | "replay" | "sim", body?: ConnectBody) {
    const base = { busy: null, launch_check: null, sim_scene: null, error: null, warnings: [] as string[], imu_stream: false, imu_temp_c: null };
    if (source === "camera") {
      this.emitters = { vertical: 0, horizontal: 0, dots: 0 };
      const idx = SETTINGS.camera.index;
      const cam = cameraByIndex(idx) ?? cameraByIndex(1)!;
      const pico = !this.opts.nopico;
      const warnings: string[] = [];
      if (!cam.stereo_like)
        warnings.push(
          `camera ${cam.index} delivers ${cam.width}x${cam.height} images, not side-by-side stereo (2560x800): choose the stereo camera in Setup, Connect step`,
        );
      if (!pico) warnings.push("no Pico found: the emitters, the trigger and the IMU are unavailable (flash the firmware? see Setup, Connect step)");
      if (!this.setup.deviceCalibration().loaded) warnings.push(`no calibration at ~/.ohs/calibration.json: run the setup wizard (Setup)`);
      this.setup.connectedIndex = cam.index;
      this.device = {
        ...base,
        source,
        connected: true,
        camera: {
          connected: true,
          info: {
            name: cam.name ?? `Camera ${cam.index}`,
            index: cam.index,
            width: cam.width,
            height: cam.height,
            fps: cam.fps,
            backend: "dshow",
            fourcc: cam.fourcc,
            swap_lr: SETTINGS.camera.swap_lr,
          },
        },
        pico: pico
          ? { connected: true, firmware: "ohs-fw 1.1.0", emitters: { ...this.emitters }, imu: "LSM6DSOX", port: "COM5" }
          : { connected: false, firmware: null, emitters: { ...this.emitters }, imu: null, port: null, error: "no Pico found on any serial port" },
        calibration: this.setup.deviceCalibration(),
        replay: null,
        warnings,
        imu_stream: pico,
        imu_temp_c: pico ? 29.4 : null,
      };
    } else if (source === "replay") {
      const path = String(body?.recording ?? "").trim();
      this.device = {
        ...base,
        source,
        connected: true,
        camera: { connected: true, info: { width: 1280, height: 800, fps: 30, frames: 1800 } },
        pico: null,
        calibration: { ...SIM_CALIB, path: `${path.replace(/[\\/]+$/, "")}/calibration.json` },
        replay: { path, loop: body?.loop ?? true },
      };
    } else {
      this.device = {
        ...base,
        source,
        connected: true,
        camera: { connected: true, info: { width: 1280, height: 800, fps: 30, scene: "sphere_on_plane" } },
        pico: null,
        calibration: { ...SIM_CALIB },
        replay: { path: "~/.ohs/studio/sim/demo", loop: true },
        sim_scene: "demo",
        imu_stream: true,
        imu_temp_c: 27.3,
      };
    }
    this.send({ type: "device", ...this.device });
    this.setup.runLaunchCheck();
    this.setup.broadcast();
    this.calib.broadcastTarget(this.setup.target());
  }

  private disconnect() {
    this.emitters = { vertical: 0, horizontal: 0, dots: 0 };
    this.calib.onDeviceLost();
    this.setup.launch = null;
    this.setup.connectedIndex = null;
    this.device = {
      source: "none",
      connected: false,
      busy: this.device.busy,
      camera: null,
      pico: null,
      calibration: this.setup.deviceCalibration(),
      replay: null,
      launch_check: null,
      sim_scene: null,
      warnings: [],
    };
    this.send({ type: "device", ...this.device });
    this.setup.broadcast();
    this.calib.broadcastTarget(this.setup.target());
  }

  // ---------------------------------------------------------------- scan session
  private start(body: Record<string, unknown>): SessionState {
    if (!this.device.connected) throw new HttpError(409, "No device is connected", "Connect the camera, a replay or the simulator on the Device page.");
    if (!this.device.calibration.loaded)
      throw new HttpError(409, "No calibration is loaded", "Calibrate the scanner in Setup (the Stereo step), then start the scan again.");
    if (this.sim) throw new HttpError(409, "A scan is already running", "Stop it first.");
    if (this.calib.active())
      throw new HttpError(409, "A calibration session is running", "Finish or cancel it in Setup first: a scan and a calibration never run together.");
    const pid = String(body.project_id ?? "");
    const p = this.projects.get(pid);
    if (!p) throw new HttpError(404, "Project not found", "Choose another project, or create one.");
    const mode: ScanMode = body.mode === "markers" ? "markers" : "markerless";
    const name = typeof body.name === "string" && body.name.trim() ? body.name.trim() : `Scan ${p.scans.length + 1}`;
    const scan: Scan = { id: uid("s-"), name, created: nowIso(), mode, n_frames: 0, duration_s: 0, status: "recording", result: null };
    p.scans.push(scan);
    p.modified = nowIso();
    this.sim = {
      scan,
      projectId: p.id,
      mode,
      T: 0,
      trajT: 0,
      lostAt: null,
      counts: new Float32Array(this.liveScene.part.length),
      laser: 0,
      snapshots: [],
      lastSnap: -1,
      meshSeq: 0,
      meshDirty: false,
      lastMeshAt: 0,
      ghost: null,
      lastGhostAt: 0,
      pose: trajectory(0),
      tracking: { state: "ok", reason: null, fitness: 0.9, rmse_mm: 0.5 },
    };
    if (this.device.source === "camera") {
      this.emitters = mode === "markers" ? { vertical: 100, horizontal: 100, dots: 25 } : { vertical: 100, horizontal: 100, dots: 100 };
      this.device = { ...this.device, pico: this.device.pico ? { ...this.device.pico, emitters: { ...this.emitters } } : null };
      this.send({ type: "device", ...this.device });
    }
    this.session = { state: "scanning", project_id: p.id, scan_id: scan.id, mode, elapsed_s: 0, frames: 0 };
    this.send({ type: "session", ...this.session });
    return this.session;
  }

  private rewind(seconds: number): SessionState {
    const s = this.sim;
    if (!s) throw new HttpError(409, "No scan is running", "Rewind works while scanning or paused.");
    const target = s.T - seconds;
    let snap = null;
    for (const x of s.snapshots) if (x.T <= target) snap = x;
    if (snap) {
      s.counts.set(snap.counts);
      s.laser = snap.laser;
    } else {
      s.counts.fill(0);
      s.laser = 0;
    }
    s.snapshots = s.snapshots.filter((x) => x.T <= target);
    s.meshDirty = true;
    this.send({ type: "log", level: "info", message: `Rewound ${seconds} s: the last stretch is cut from the model` });
    return this.session;
  }

  private async stop(): Promise<{ scan: Scan }> {
    const s = this.sim;
    if (!s) throw new HttpError(409, "No scan is running", "Start a scan first.");
    this.session = { ...this.session, state: "stopping" };
    this.send({ type: "session", ...this.session });
    await new Promise((r) => setTimeout(r, this.opts.fast ? 50 : 650));
    s.scan.n_frames = this.session.frames;
    s.scan.duration_s = Math.round(s.T * 10) / 10;
    s.scan.status = "recorded";
    const p = this.projects.get(s.projectId);
    if (p) p.modified = nowIso();
    // last mesh of the session stays on screen
    this.sendMesh(s);
    this.sim = null;
    this.session = { state: "idle", project_id: s.projectId, scan_id: s.scan.id, mode: s.mode, elapsed_s: s.scan.duration_s, frames: s.scan.n_frames };
    this.send({ type: "session", ...this.session });
    if (this.device.source === "camera") {
      this.emitters = { vertical: 0, horizontal: 0, dots: 0 };
      this.device = { ...this.device, pico: this.device.pico ? { ...this.device.pico, emitters: { ...this.emitters } } : null };
      this.send({ type: "device", ...this.device });
    }
    return { scan: { ...s.scan } };
  }

  // ---------------------------------------------------------------- jobs
  private cancel(j: Job): Job {
    if (j.state !== "running" && j.state !== "queued") return j;
    const m = this.jobMeta.get(j.id);
    j.state = "cancelled";
    j.message = "Cancelled";
    delete j.frames;
    if (m?.kind === "work") m.spec.onCancel?.();
    if (m?.kind === "sim") {
      this.simJobId = null;
      this.device = { ...this.device, busy: null };
      this.send({ type: "device", ...this.device });
    }
    if (m?.kind === "timed") {
      this.setup.jobCancelled(j.id);
      this.calib.jobCancelled(j.id);
    }
    this.send({ type: "job", ...j });
    return j;
  }

  private stepJobs(dt: number, broadcast: boolean) {
    for (const [jid, m] of this.jobMeta) {
      const j = this.jobs.get(jid);
      if (!j || j.state !== "running") continue;
      if (m.kind === "timed") {
        m.t += dt;
        j.progress = Math.min(1, m.t / m.dur);
        j.message = m.spec.message(j.progress);
        if (j.progress >= 1) {
          try {
            j.result = m.spec.done();
            j.state = "done";
            j.message = "Finished";
          } catch (e) {
            j.state = "failed";
            j.error = e instanceof Error ? e.message : String(e);
            j.message = "Failed";
          }
        }
      } else if (m.kind === "sim") {
        j.progress = Math.min(1, j.progress + dt / (this.opts.fast ? 1.2 : 5));
        j.message = j.progress < 0.92 ? `Rendering frame ${Math.round(j.progress * 120)} / 120` : "Writing the demo recording";
        if (j.progress >= 1) {
          j.state = "done";
          j.message = "Demo recording ready";
          j.result = { recording: "~/.ohs/sim/demo" };
          this.simReady = true;
          this.simJobId = null;
          this.connectNow("sim");
        }
      } else {
        this.stepWork(j, m, dt);
      }
      if (broadcast || j.state !== "running") this.send({ type: "job", ...j });
    }
  }

  /**
   * One tick of a work job: run its steps for up to ~22 ms, never far ahead of its clock
   * (progress = the lesser of work done and time over its minimum duration). When both are
   * done, its commit applies the side effects; then the final job message goes out.
   */
  private stepWork(j: Job, m: Extract<JobMeta, { kind: "work" }>, dt: number) {
    m.t += dt;
    const pTime = Math.min(1, m.t / m.dur);
    const deadline = performance.now() + 22;
    // A plain Error is a job's plain-words failure; anything else is a bug in the demo engine.
    const fail = (e: unknown) => {
      if (!(e instanceof Error) || e.name !== "Error") console.error("mock job crashed", e);
      j.state = "failed";
      j.error = e instanceof Error ? e.message : String(e);
      j.message = "Failed";
      delete j.frames;
      m.spec.onFail?.(j.error);
    };
    try {
      while (!m.finished && m.p <= pTime + 0.03 && performance.now() < deadline) {
        const r = m.spec.work.next();
        if (r.done) {
          m.finished = true;
          m.commit = r.value;
        } else {
          const step: WorkStep = r.value;
          m.p = Math.max(m.p, Math.min(1, step.p));
          m.message = step.message;
          const f = step.frames;
          if (!f) m.frames = null;
          else if (!m.frames || m.frames.stage !== f.stage || f.done < m.frames.done) m.frames = { ...f, t0: m.t };
          else m.frames = { ...m.frames, done: f.done, total: f.total };
        }
      }
    } catch (e) {
      fail(e);
      return;
    }
    j.progress = Math.max(j.progress, Math.min(pTime, m.finished ? 1 : m.p));
    j.message = m.message;
    // the job's `frames` (APP_API.md, as the engine reports it): the rate since the first frame
    if (m.frames && !m.finished) {
      const elapsed = m.t - m.frames.t0;
      const fps = m.frames.done > 1 && elapsed > 0 ? (m.frames.done - 1) / elapsed : null;
      j.frames = { stage: m.frames.stage, done: m.frames.done, total: m.frames.total, fps: fps === null ? null : Math.round(fps * 100) / 100, elapsed_s: Math.round(elapsed * 100) / 100 };
    } else delete j.frames;
    if (m.finished && m.t >= m.dur) {
      try {
        j.result = m.commit ? m.commit() : null;
        j.state = "done";
        j.progress = 1;
      } catch (e) {
        fail(e);
      }
    }
  }

  // ---------------------------------------------------------------- files
  private file(p: Project, s: Scan, name: string, head = false): Response {
    if (name === "mesh.ply" || name === "mesh.stl" || name === "mesh.obj") {
      // the current version of the processing studio's mesh
      const buf = this.studio.meshFile(p, s, name);
      if (!buf) throw new HttpError(404, `${name} isn't there yet`, "Make the mesh first.");
      if (head) return new Response(null, { status: 200, headers: { "content-length": String(buf.byteLength) } });
      return streamed(buf, name.endsWith(".obj") ? "text/plain" : "application/octet-stream", this.opts.fast ? 30 : Math.min(900, 200 + buf.byteLength / 60000));
    }
    if (s.status !== "processed" || !s.result) throw new HttpError(404, `${name} isn't there yet`, "Make the mesh first.");
    const quality: ProcessQuality = normalizeQuality((s as Scan & { quality?: ProcessQuality }).quality) ?? "standard";
    const key = `${s.id}:${quality}:${name}`;
    let buf = this.files.get(key);
    if (head) return new Response(null, { status: name === "laser_points.ply" || name === "report.json" ? 200 : 404 });
    if (name === "laser_points.ply") {
      if (!buf) {
        const r = rng(11);
        const pts: number[] = [];
        for (let k = 0; k < 56; k++) laserPoints(trajectory(k * 1.1), 280, r, pts);
        const w = new Float32Array(pts.length);
        for (let i = 0; i < pts.length; i += 3) {
          const x = apply(this.T_ws, [pts[i], pts[i + 1], pts[i + 2]]);
          w[i] = x[0];
          w[i + 1] = x[1];
          w[i + 2] = x[2];
        }
        buf = plyPoints(w);
      }
    } else if (name === "report.json") {
      buf ??= new TextEncoder().encode(JSON.stringify(this.report(p, s, quality), null, 1)).buffer as ArrayBuffer;
      this.files.set(key, buf);
      return new Response(buf, { status: 200, headers: { "content-type": "application/json" } });
    } else throw new HttpError(404, `No file called ${name}`, "Files: mesh.ply, mesh.stl, mesh.obj, laser_points.ply, report.json.");
    this.files.set(key, buf);
    return streamed(buf, "application/octet-stream", this.opts.fast ? 30 : 450);
  }

  private reportSummary(s: Scan, quality: ProcessQuality): Record<string, unknown> {
    const n = Math.ceil(s.n_frames / mockStep(s, quality));
    return { mode: s.mode, quality, frames: { processed: n, tracked: Math.round(n * 0.93) } };
  }

  private report(p: Project, s: Scan, quality: ProcessQuality): Record<string, unknown> {
    const step = mockStep(s, quality);
    const fps = s.duration_s > 0 ? s.n_frames / s.duration_s : 30;
    const n = Math.max(1, Math.ceil(s.n_frames / step));
    const tracked = Math.round(n * 0.93);
    const full = quality !== "preview";
    return {
      recording: `~/OpenHandScan/projects/${p.id}/scans/${s.id}/recording`,
      source: this.device.source === "camera" ? "camera" : "sim",
      scene: "sphere_on_plane",
      calibration: "recording",
      mode: s.mode,
      scale: full ? 1.0 : 0.5,
      frames: {
        in_recording: s.n_frames, processed: n, tracked, tracked_fraction: tracked / n, start: 0, stop: s.n_frames, step,
        recorded_fps: Math.round(fps * 10) / 10, max_frame_rate: quality === "standard" ? 30 : 0,
      },
      failures: s.mode === "markers" ? { few_tags: Math.round((n - tracked) * 0.6), no_tags: n - tracked - Math.round((n - tracked) * 0.6) } : { low_fitness: Math.round((n - tracked) * 0.55), model_not_visible: n - tracked - Math.round((n - tracked) * 0.55) },
      imu: { samples: Math.round(s.duration_s * 416), gyro_prior: true, frames_with_prior: tracked },
      mesh: { method: "tsdf", voxel_mm: full ? 1.0 : 2.0, empty: false },
      lasers: { planes: ["horizontal", "vertical"], n_points_exported: 41250, voxel_mm: 0.2, frames_with_points: tracked },
      ground_truth: { n: tracked, rms_trans_mm: 0.41, final_trans_mm: 0.62, trans_mm: { mean: 0.36, median: 0.33, p95: 0.78, max: 1.12 }, rot_deg: { mean: 0.08, median: 0.07, p95: 0.17, max: 0.24 } },
      wall_time_s: quality === "best" ? 148.4 : quality === "standard" ? 74.2 : 21.8,
    };
  }

  // ---------------------------------------------------------------- simulation
  private tick() {
    const now = performance.now();
    const dt = Math.min(0.25, (now - this.lastTick) / 1000);
    this.lastTick = now;
    this.jobAcc += dt;
    const jobBroadcast = this.jobAcc >= 0.2;
    if (jobBroadcast) this.jobAcc = 0;
    this.stepJobs(dt, jobBroadcast);

    const s = this.sim;
    if (s && this.session.state === "scanning") this.stepScan(s, dt * this.timeScale);
    if (!s) this.hoverClock += dt;

    this.statusAcc += dt;
    if (this.statusAcc >= 0.1) {
      this.statusAcc = 0;
      this.send(this.status());
    }
    // The scan previews; during a calibration session the camera shows the calibration views instead.
    if (this.device.connected && !this.calib.active() && [...this.clients].some((c) => c.wantPreview)) {
      this.previewAcc += dt;
      if (this.previewAcc >= 0.1 && !this.rendering) {
        this.previewAcc = 0;
        void this.sendPreviews();
      }
    }
    if (s && (s.meshDirty || (this.session.state === "scanning" && now - s.lastMeshAt > 1500))) this.sendMesh(s);
  }

  private trackingAt(T: number, mode: ScanMode): Tracking {
    const ph = T % TRACK_CYCLE;
    const wob = Math.sin(T * 1.7);
    if (ph >= 10 && ph < 13.5)
      return { state: "weak", reason: mode === "markers" ? "few_tags" : "low_fitness", fitness: 0.44 + 0.05 * wob, rmse_mm: 1.45 + 0.15 * wob };
    if (ph >= 13.5 && ph < 18) return { state: "lost", reason: mode === "markers" ? "no_tags" : "model_not_visible", fitness: null, rmse_mm: null };
    return { state: "ok", reason: null, fitness: 0.88 + 0.05 * wob, rmse_mm: 0.52 + 0.08 * wob };
  }

  private stepScan(s: ScanSim, dt: number) {
    s.T += dt;
    const tr = this.trackingAt(s.T, s.mode);
    if (tr.state !== s.tracking.state) {
      if (tr.state === "lost") this.send({ type: "log", level: "warn", message: `Tracking lost (${tr.reason})` });
      if (s.tracking.state === "lost" && tr.state !== "lost") this.send({ type: "log", level: "info", message: "Tracking recovered" });
    }
    s.tracking = tr;
    const lost = tr.state === "lost";
    if (lost) {
      if (s.lostAt === null) {
        s.lostAt = s.T;
        s.ghost = toRowMajor16(compose(this.T_ws, trajectory(s.trajT)));
        s.lastGhostAt = -1;
      }
      // The operator swings away, then comes back to the last good spot.
      const k = Math.sin(Math.min(1, (s.T - s.lostAt) / 4.5) * Math.PI);
      const base = trajectory(s.trajT);
      s.pose = compose(base, { R: axisAngle([0, 1, 0], 0.65 * k), t: [70 * k, -25 * k, -30 * k] });
      if (s.T - s.lastGhostAt > 1 && s.ghost) {
        s.lastGhostAt = s.T;
        this.send({ type: "ghost", T_wc: s.ghost });
      }
    } else {
      s.lostAt = null;
      s.ghost = null;
      s.trajT += dt;
      s.pose = trajectory(s.trajT);
      this.integrate(s, tr.state === "weak" ? 0.45 : 1);
      s.laser += Math.round((tr.state === "weak" ? 150 : 330) * (0.85 + this.rand() * 0.3) * (dt / 0.066));
    }
    const fps = this.device.source === "camera" ? 60 : 30;
    this.session = { ...this.session, elapsed_s: s.T, frames: Math.floor(s.T * fps) };
    const Tw = compose(this.T_ws, lost ? trajectory(s.trajT) : s.pose);
    this.send({ type: "pose", T_wc: toRowMajor16(Tw), ok: !lost });
    if (s.T - s.lastSnap >= 0.5) {
      s.lastSnap = s.T;
      s.snapshots.push({ T: s.T, counts: s.counts.slice(), laser: s.laser });
      if (s.snapshots.length > 24) s.snapshots.shift();
    }
  }

  /** Accumulate how well each live-mesh vertex is seen from the current pose. */
  private integrate(s: ScanSim, weight: number) {
    const P = s.pose;
    const { positions, normals, part } = this.liveScene;
    const cam = P.t;
    for (let i = 0, n = part.length; i < n; i++) {
      const p: V3 = [positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]];
      const pc = applyInv(P, p);
      const z = pc[2];
      if (z < 150 || z > 450) continue;
      const ux = pc[0] / z,
        uy = pc[1] / z;
      if (Math.abs(ux) > 0.85 || Math.abs(uy) > 0.53) continue;
      const toCam = normalize(sub(cam, p));
      const facing = dot([normals[i * 3], normals[i * 3 + 1], normals[i * 3 + 2]], toCam);
      if (facing < 0.22) continue;
      if (i % 2 === 0 || s.counts[i] < 0.35) {
        if (occluded(p, cam, part[i])) continue;
      }
      const centre = 1 - 0.6 * Math.max(Math.abs(ux) / 0.85, Math.abs(uy) / 0.53);
      s.counts[i] = Math.min(60, s.counts[i] + weight * facing * centre * 0.7);
    }
  }

  private sendMesh(s: ScanSim) {
    s.meshDirty = false;
    s.lastMeshAt = performance.now();
    const counts = s.counts;
    const Vall = counts.length;
    const remap = new Int32Array(Vall).fill(-1);
    let V = 0;
    for (let i = 0; i < Vall; i++) if (counts[i] >= 0.35) remap[i] = V++;
    const idx = this.liveScene.indices;
    const tris: number[] = [];
    for (let t = 0; t < idx.length; t += 3) {
      const a = remap[idx[t]],
        b = remap[idx[t + 1]],
        c = remap[idx[t + 2]];
      if (a >= 0 && b >= 0 && c >= 0) tris.push(a, b, c);
    }
    const P = new Float32Array(V * 3);
    const N = new Float32Array(V * 3);
    const C = new Uint8Array(V * 4);
    const W = this.liveWorld;
    for (let i = 0; i < Vall; i++) {
      const j = remap[i];
      if (j < 0) continue;
      P.set(W.positions.subarray(i * 3, i * 3 + 3), j * 3);
      N.set(W.normals.subarray(i * 3, i * 3 + 3), j * 3);
      const q = Math.min(1, counts[i] / 7);
      // yellow (thin) → green (well observed)
      C[j * 4] = Math.round(236 + (52 - 236) * q);
      C[j * 4 + 1] = Math.round(196 + (178 - 196) * q);
      C[j * 4 + 2] = Math.round(64 + (84 - 64) * q);
      C[j * 4 + 3] = 255;
    }
    const I = new Uint32Array(tris);
    s.meshSeq += 1;
    this.sendBinary(
      encodeBinary({ type: "mesh", seq: s.meshSeq, vertices: V, triangles: I.length / 3, units: "mm", frame: "world" }, [P, N, C, I]),
      "mesh",
    );
  }

  private hoverPose(): Pose {
    // Before a scan the operator aims around the start of the sweep.
    const tau = 2.4 - 2.4 * Math.cos(this.hoverClock * 0.35);
    return trajectory(tau);
  }

  private status(): StatusMsg {
    const s = this.sim;
    const connected = this.device.connected;
    const live = !!s;
    const tr: Tracking = live && s ? s.tracking : { state: "idle", reason: null, fitness: null, rmse_mm: null };
    let triangles = 0,
      vertices = 0;
    if (s) {
      for (let i = 0; i < s.counts.length; i++) if (s.counts[i] >= 0.35) vertices++;
      triangles = Math.round(vertices * 1.86);
    }
    const scanning = this.session.state === "scanning";
    return {
      type: "status",
      t: performance.now() / 1000,
      session: this.session,
      tracking: tr,
      distance_mm: connected ? this.lastDistance : null,
      fps: connected
        ? {
            camera: (this.device.source === "camera" ? 60 : 30) - this.rand() * 0.6,
            pipeline: scanning || !live ? 14.2 + this.rand() * 1.6 : 0,
          }
        : { camera: 0, pipeline: 0 },
      laser_points: s ? s.laser : 0,
      model: { vertices, triangles },
      emitters: this.device.source === "camera" ? { ...this.emitters } : null,
    };
  }

  private lastDistance: number | null = null;

  private async sendPreviews() {
    if (!this.clients.size) return;
    this.rendering = true;
    try {
      this.renderer ??= new PreviewRenderer();
      const s = this.sim;
      const pose = s ? s.pose : this.hoverPose();
      const lines = this.device.source === "camera" ? Math.max(this.emitters.vertical, this.emitters.horizontal) : 100;
      const dots = this.device.source === "camera" ? this.emitters.dots : 100;
      const d = this.renderer.render(pose, { dots, lines });
      this.lastDistance = s?.tracking.state === "lost" && s.lostAt !== null && s.T - s.lostAt > 0.8 && s.T - s.lostAt < 3.8 ? null : d;
      const { left, depth } = await this.renderer.encode();
      const seq = ++this.previewSeq;
      this.sendBinary(encodeBinary({ type: "preview", camera: "left", format: "jpeg", w: LEFT_W, h: LEFT_H, seq }, [left]), "preview");
      this.sendBinary(encodeBinary({ type: "preview", camera: "depth", format: "jpeg", w: DEPTH_W, h: DEPTH_H, seq }, [depth]), "preview");
    } catch (e) {
      console.warn("mock previews failed", e);
    } finally {
      this.rendering = false;
    }
  }

  // ---------------------------------------------------------------- seed data
  private seed() {
    const mk = (name: string, ageH: number, scans: Array<Partial<Scan> & { name: string; quality?: ProcessQuality }>): Project => {
      const created = nowIso(-ageH * 3600 - 7200);
      const p: Project = { id: uid("p-"), name, created, modified: nowIso(-ageH * 3600), n_scans: scans.length, thumbnail_url: null, scans: [] };
      scans.forEach((x, i) => {
        const s: Scan & { quality?: ProcessQuality } = {
          id: uid("s-"),
          name: x.name,
          created: nowIso(-ageH * 3600 - (scans.length - i) * 1800),
          mode: x.mode ?? "markerless",
          n_frames: x.n_frames ?? 540,
          duration_s: x.duration_s ?? 18,
          status: x.status ?? "recorded",
          result: null,
        };
        if (s.status === "processed") {
          const base = `/api/projects/${encodeURIComponent(p.id)}/scans/${encodeURIComponent(s.id)}/files`;
          s.quality = x.quality ?? "standard";
          s.result = { mesh_url: `${base}/mesh.ply`, stl_url: `${base}/mesh.stl`, laser_points_url: `${base}/laser_points.ply`, report: this.reportSummary(s, s.quality) };
        }
        p.scans.push(s);
      });
      this.projects.set(p.id, p);
      return p;
    };
    mk("Intake manifold bracket", 2, [
      { name: "Bracket top", status: "processed", n_frames: 864, duration_s: 28.8 },
      { name: "Bracket underside", status: "recorded", n_frames: 612, duration_s: 20.4 },
      { name: "Quick test (fail)", status: "failed", n_frames: 96, duration_s: 3.2 },
    ]);
    mk("Fender flare, left", 27, [{ name: "Wheel arch", status: "processed", quality: "preview", mode: "markers", n_frames: 1530, duration_s: 51 }]);
    mk("Coffee can demo", 96, []);
  }
}

const SETTINGS = {
  projects_dir: "~/OpenHandScan/projects",
  config_file: "~/.ohs/config.toml",
  paths: { calibration: "~/.ohs/calibration.json", scans_dir: "~/OpenHandScan/scans" },
  camera: { index: 1, backend: "auto", width: 2560, height: 800, fps: 60.0, fourcc: "MJPG", exposure: null, gain: null, swap_lr: false, second_index: null },
  pico: { port: null, baud: 115200, imu_rate_hz: 416, heartbeat_s: 0.4 },
  emitters: {
    markers_dot_level: 25.0,
    profiles: { off: [0, 0, 0], scan: [100, 100, 100], lines: [100, 100, 0], dots: [0, 0, 100], stereo: [0, 0, 0], lasers: [100, 100, 0], imu: [0, 0, 0], check: [100, 100, 0] },
  },
  live: { scale: 0.5, depth_min_mm: 150.0, depth_max_mm: 450.0, voxel_mm: null },
  process: { voxel_mm: null, frame_step: 1, frame_motion_deg: 0.0, frame_max_gap: 4, poisson: false, export: ["ply", "stl"] },
  board: { squares_x: 11, squares_y: 8, square_mm: 20.0, marker_mm: 15.0, dictionary: "DICT_5X5_100", paper: "letter" },
  markers: { tag_mm: 20.0 },
};

export function createMockBackend(opts: MockOptions): { fetch: FetchLike; socket: SocketFactory } {
  const b = new MockBackend(opts);
  console.info("%cOpenHandScan demo mode%c: an in-browser mock engine (no hardware, no server).", "font-weight:bold", "");
  if (import.meta.env.DEV) (window as unknown as { __ohsMock: MockBackend }).__ohsMock = b;
  return { fetch: b.fetch, socket: b.socketFactory };
}
