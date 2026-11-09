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
 *   report.json) generated on demand.
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
  plyMesh,
  plyPoints,
  rng,
  sceneMesh,
  stlMesh,
  sub,
  toRowMajor16,
  trajectory,
  transformMesh,
  type MeshData,
  type Pose,
  type V3,
} from "./geom";
import { DEPTH_H, DEPTH_W, LEFT_H, LEFT_W, PreviewRenderer } from "./render";

const VERSION = "0.1.0";
const nowIso = (offsetS = 0) => new Date(Date.now() + offsetS * 1000).toISOString();
const uid = (p: string) => `${p}${Date.now().toString(36).slice(-4)}${Math.random().toString(36).slice(2, 7)}`;

class HttpError extends Error {
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

function json(status: number, body: unknown): Response {
  if (status === 204) return new Response(null, { status });
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

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
    this.backend.onClientMessage(data);
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
  };
  private simReady = false;
  private simJobId: string | null = null;
  private projects = new Map<string, Project>();
  private jobs = new Map<string, Job>();
  private jobMeta = new Map<string, { kind: "sim" } | { kind: "process"; pid: string; sid: string; quality: ProcessQuality; prev: Scan["status"]; dur: number; t: number; fail: boolean }>();
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
    setInterval(() => this.tick(), 66);
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
    s.deliver(JSON.stringify({ type: "hello", version: VERSION }));
    s.deliver(JSON.stringify({ type: "device", ...this.device }));
    s.deliver(JSON.stringify({ type: "session", ...this.session }));
    for (const j of this.jobs.values()) if (j.state === "queued" || j.state === "running") s.deliver(JSON.stringify({ type: "job", ...j }));
    if (this.sim) this.sim.meshDirty = true;
  }
  detach(s: MockSocket) {
    this.clients.delete(s);
  }
  onClientMessage(_data: string) {
    /* subscribe: everything is always on in the mock */
  }
  private send(msg: Record<string, unknown>) {
    const text = JSON.stringify(msg);
    for (const c of this.clients) c.deliver(text);
  }
  private sendBinary(buf: ArrayBuffer) {
    for (const c of this.clients) c.deliver(buf);
  }

  // ---------------------------------------------------------------- REST
  private async route(method: string, path: string, body: Record<string, unknown>): Promise<Response> {
    const seg = path.replace(/^\/api\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
    const [a, b, c, d, e, f] = seg;
    const is = (m: string, n: number) => method === m && seg.length === n;

    if (a === "health" && is("GET", 1)) return json(200, { app: "openhandscan", version: VERSION, engine: { opencv: "5.0.0", open3d: "0.20.0", mode: "demo" } });
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
        if (e === "process" && is("POST", 5)) return json(200, this.process(p, s, (body.quality as ProcessQuality) === "preview" ? "preview" : "full"));
        if (e === "files" && f && is("GET", 6)) return this.file(p, s, f);
      }
    }

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

    throw new HttpError(404, `No such endpoint: ${method} ${path}`, "The demo backend implements APP_API.md v1.");
  }

  private listProjects(): ProjectSummary[] {
    return [...this.projects.values()]
      .map((p) => ({ id: p.id, name: p.name, created: p.created, modified: p.modified, n_scans: p.scans.length, thumbnail_url: p.thumbnail_url }))
      .sort((x, y) => (x.modified < y.modified ? 1 : -1));
  }

  // ---------------------------------------------------------------- device
  private connect(body: ConnectBody): Response {
    if (this.sim) throw new HttpError(409, "A scan is running", "Stop the scan before changing the device.");
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
        job = { id: uid("j-"), kind: "sim_demo", state: "running", progress: 0, message: "Rendering frame 0 / 120", result: null, error: null, created: nowIso() };
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
    if (source === "camera") {
      this.emitters = { vertical: 0, horizontal: 0, dots: 0 };
      this.device = {
        source,
        connected: true,
        busy: null,
        camera: { connected: true, info: { name: "ELP USB3 stereo (demo)", width: 2560, height: 800, fps: 60, backend: "dshow", fourcc: "MJPG" } },
        pico: { connected: true, firmware: "ohs-fw 0.3.1", emitters: { ...this.emitters } },
        calibration: { ...NO_CALIB },
        replay: null,
      };
    } else if (source === "replay") {
      const path = String(body?.recording ?? "").trim();
      this.device = {
        source,
        connected: true,
        busy: null,
        camera: { connected: true, info: { width: 1280, height: 800, fps: 30, frames: 1800 } },
        pico: null,
        calibration: { ...SIM_CALIB, path: `${path.replace(/[\\/]+$/, "")}/calibration.json` },
        replay: { path, loop: body?.loop ?? true },
      };
    } else {
      this.device = {
        source,
        connected: true,
        busy: null,
        camera: { connected: true, info: { width: 1280, height: 800, fps: 30, scene: "sphere_on_plane" } },
        pico: null,
        calibration: { ...SIM_CALIB },
        replay: null,
      };
    }
    this.send({ type: "device", ...this.device });
  }

  private disconnect() {
    this.emitters = { vertical: 0, horizontal: 0, dots: 0 };
    this.device = { source: "none", connected: false, busy: this.device.busy, camera: null, pico: null, calibration: { ...NO_CALIB }, replay: null };
    this.send({ type: "device", ...this.device });
  }

  // ---------------------------------------------------------------- scan session
  private start(body: Record<string, unknown>): SessionState {
    if (!this.device.connected) throw new HttpError(409, "No device is connected", "Connect the camera, a replay or the simulator on the Device page.");
    if (!this.device.calibration.loaded)
      throw new HttpError(409, "No calibration is loaded", "Run calibration (coming in Studio M2; use `ohs calibrate` for now), then reconnect.");
    if (this.sim) throw new HttpError(409, "A scan is already running", "Stop it first.");
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
  private process(p: Project, s: Scan, quality: ProcessQuality): Job {
    if (s.status === "recording") throw new HttpError(409, "This scan is still recording", "Stop the scan first.");
    for (const [jid, m] of this.jobMeta) {
      const j = this.jobs.get(jid);
      if (m.kind === "process" && m.sid === s.id && j && (j.state === "running" || j.state === "queued"))
        throw new HttpError(409, "This scan is already being meshed", "Wait for it to finish, or cancel it in Jobs.");
    }
    const job: Job = { id: uid("j-"), kind: "process", state: "running", progress: 0, message: "Loading the recording", result: null, error: null, created: nowIso() };
    const dur = (quality === "preview" ? 4.5 : 9) * (this.opts.fast ? 0.25 : 1);
    this.jobs.set(job.id, job);
    this.jobMeta.set(job.id, { kind: "process", pid: p.id, sid: s.id, quality, prev: s.status === "processing" ? "recorded" : s.status, dur, t: 0, fail: /fail/i.test(s.name) || s.n_frames < 5 });
    s.status = "processing";
    p.modified = nowIso();
    this.send({ type: "job", ...job });
    return job;
  }

  private cancel(j: Job): Job {
    if (j.state !== "running" && j.state !== "queued") return j;
    const m = this.jobMeta.get(j.id);
    j.state = "cancelled";
    j.message = "Cancelled";
    if (m?.kind === "process") {
      const s = this.projects.get(m.pid)?.scans.find((x) => x.id === m.sid);
      if (s) s.status = m.prev === "processing" ? "recorded" : m.prev;
    }
    if (m?.kind === "sim") {
      this.simJobId = null;
      this.device = { ...this.device, busy: null };
      this.send({ type: "device", ...this.device });
    }
    this.send({ type: "job", ...j });
    return j;
  }

  private stepJobs(dt: number, broadcast: boolean) {
    for (const [jid, m] of this.jobMeta) {
      const j = this.jobs.get(jid);
      if (!j || j.state !== "running") continue;
      if (m.kind === "sim") {
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
        m.t += dt;
        const pr = Math.min(1, m.t / m.dur);
        const s = this.projects.get(m.pid)?.scans.find((x) => x.id === m.sid);
        if (!s) {
          j.state = "failed";
          j.error = "The scan was removed";
          continue;
        }
        j.progress = pr;
        const n = Math.max(1, s.n_frames);
        j.message =
          pr < 0.05
            ? "Loading the recording"
            : pr < 0.7
              ? `Tracking frame ${Math.min(n, Math.round(((pr - 0.05) / 0.65) * n))} / ${n}`
              : pr < 0.84
                ? "Fusing depth (TSDF)"
                : pr < 0.92
                  ? "Extracting the mesh"
                  : pr < 0.97
                    ? "Cleaning up small pieces"
                    : "Writing PLY and STL";
        if (m.fail && pr > 0.42) {
          j.state = "failed";
          j.error = "Tracking failed on 83 % of the frames (low_fitness). Rescan with more overlap, or try markers mode.";
          j.message = "Failed";
          s.status = "failed";
        } else if (pr >= 1) {
          const base = `/api/projects/${encodeURIComponent(m.pid)}/scans/${encodeURIComponent(m.sid)}/files`;
          j.state = "done";
          j.message = "Mesh ready";
          j.result = { project_id: m.pid, scan_id: m.sid, quality: m.quality, mesh: `${base}/mesh.ply` };
          s.status = "processed";
          s.result = {
            mesh_url: `${base}/mesh.ply`,
            stl_url: `${base}/mesh.stl`,
            laser_points_url: `${base}/laser_points.ply`,
            report: this.reportSummary(s, m.quality),
          };
          for (const k of [...this.files.keys()]) if (k.startsWith(m.sid)) this.files.delete(k);
          (s as Scan & { quality?: ProcessQuality }).quality = m.quality;
          const p = this.projects.get(m.pid);
          if (p) p.modified = nowIso();
        }
      }
      if (broadcast || j.state !== "running") this.send({ type: "job", ...j });
    }
  }

  // ---------------------------------------------------------------- files
  private file(p: Project, s: Scan, name: string): Response {
    if (s.status !== "processed" || !s.result) throw new HttpError(404, `${name} isn't there yet`, "Make the mesh first.");
    const quality: ProcessQuality = (s as Scan & { quality?: ProcessQuality }).quality ?? "full";
    const key = `${s.id}:${quality}:${name}`;
    let buf = this.files.get(key);
    const mesh = () => {
      const m = sceneMesh(quality === "full" ? 2.6 : 1.3, quality === "full" ? 0.03 : 0.06, s.id.length + s.name.length);
      return transformMesh(m, this.T_ws);
    };
    if (name === "mesh.ply") buf ??= plyMesh(mesh());
    else if (name === "mesh.stl") buf ??= stlMesh(mesh());
    else if (name === "laser_points.ply") {
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
    } else throw new HttpError(404, `No file called ${name}`, "Files: mesh.ply, mesh.stl, laser_points.ply, report.json.");
    this.files.set(key, buf);
    return streamed(buf, "application/octet-stream", this.opts.fast ? 30 : 450);
  }

  private reportSummary(s: Scan, quality: ProcessQuality): Record<string, unknown> {
    return { mode: s.mode, quality, frames: { processed: s.n_frames, tracked: Math.round(s.n_frames * 0.93) } };
  }

  private report(p: Project, s: Scan, quality: ProcessQuality): Record<string, unknown> {
    const n = Math.max(1, s.n_frames);
    const tracked = Math.round(n * 0.93);
    return {
      recording: `~/OpenHandScan/projects/${p.id}/scans/${s.id}/recording`,
      source: this.device.source === "camera" ? "camera" : "sim",
      scene: "sphere_on_plane",
      calibration: "recording",
      mode: s.mode,
      scale: quality === "full" ? 1.0 : 0.5,
      frames: { in_recording: n, processed: n, tracked, tracked_fraction: tracked / n, start: 0, stop: n, step: 1 },
      failures: s.mode === "markers" ? { few_tags: Math.round((n - tracked) * 0.6), no_tags: n - tracked - Math.round((n - tracked) * 0.6) } : { low_fitness: Math.round((n - tracked) * 0.55), model_not_visible: n - tracked - Math.round((n - tracked) * 0.55) },
      imu: { samples: Math.round(s.duration_s * 416), gyro_prior: true, frames_with_prior: tracked },
      mesh: { method: "tsdf", voxel_mm: quality === "full" ? 1.0 : 2.0, empty: false },
      lasers: { planes: ["horizontal", "vertical"], n_points_exported: 41250, voxel_mm: 0.2, frames_with_points: tracked },
      ground_truth: { n: tracked, rms_trans_mm: 0.41, final_trans_mm: 0.62, trans_mm: { mean: 0.36, median: 0.33, p95: 0.78, max: 1.12 }, rot_deg: { mean: 0.08, median: 0.07, p95: 0.17, max: 0.24 } },
      wall_time_s: quality === "full" ? 74.2 : 21.8,
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
    if (this.device.connected) {
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
      this.sendBinary(encodeBinary({ type: "preview", camera: "left", format: "jpeg", w: LEFT_W, h: LEFT_H, seq }, [left]));
      this.sendBinary(encodeBinary({ type: "preview", camera: "depth", format: "jpeg", w: DEPTH_W, h: DEPTH_H, seq }, [depth]));
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
          s.quality = x.quality ?? "full";
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
  camera: { index: 0, backend: "auto", width: 2560, height: 800, fps: 60.0, fourcc: "MJPG", exposure: null, gain: null, swap_lr: false, second_index: null },
  pico: { port: null, baud: 115200, imu_rate_hz: 416, heartbeat_s: 0.4 },
  emitters: {
    markers_dot_level: 25.0,
    profiles: { off: [0, 0, 0], scan: [100, 100, 100], lines: [100, 100, 0], dots: [0, 0, 100], stereo: [0, 0, 0], lasers: [100, 100, 0], imu: [0, 0, 0], check: [100, 100, 0] },
  },
  live: { scale: 0.5, depth_min_mm: 150.0, depth_max_mm: 450.0, voxel_mm: null },
  process: { voxel_mm: null, frame_step: 1, poisson: false, export: ["ply", "stl"] },
  board: { squares_x: 11, squares_y: 8, square_mm: 20.0, marker_mm: 15.0, dictionary: "DICT_5X5_100", paper: "letter" },
  markers: { tag_mm: 20.0 },
};

export function createMockBackend(opts: MockOptions): { fetch: FetchLike; socket: SocketFactory } {
  const b = new MockBackend(opts);
  console.info("%cOpenHandScan demo mode%c: an in-browser mock engine (no hardware, no server).", "font-weight:bold", "");
  if (import.meta.env.DEV) (window as unknown as { __ohsMock: MockBackend }).__ohsMock = b;
  return { fetch: b.fetch, socket: b.socketFactory };
}
