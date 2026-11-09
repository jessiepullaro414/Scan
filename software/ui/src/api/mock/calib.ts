/**
 * Mock calibration session (APP_API.md "M2"). A simulated person holds the printed board in
 * front of the virtual scanner and follows the wizard's own hints: travel to the next spot,
 * hold still (steady rises, auto-capture fires), occasionally slip (the board leaves one
 * camera's view, or a laser line misses the white margin) and recover. Everything the UI gets
 * is computed from that motion with the pinhole model in board.ts: corners in both raw images,
 * the board outline, the page outline and laser lines (lasers / check), coverage, diversity,
 * hints, `steady`, readiness and its reason. The IMU step holds still, then rotates with a
 * synthesized gyro rate. Solving is a job with plausible plain-language results (with advice
 * and, on the simulator, the comparison against the true calibration); saving merges into the
 * target's calibration like `ohs calibrate`.
 */
import type {
  CalibAdvice,
  CalibCoverage,
  CalibDiversity,
  CalibGrade,
  CalibHint,
  CalibHintTilt,
  CalibImuLive,
  CalibKind,
  CalibLive,
  CalibMetric,
  CalibResult,
  CalibSession,
  CalibTarget,
  CalibView,
} from "../types";
import {
  CORNERS,
  CalibRenderer,
  IMG_H,
  IMG_W,
  LENS_RAW_L,
  LENS_RAW_R,
  LENS_RECT,
  PAPER_MM,
  PREV_H,
  PREV_W,
  angleBetween,
  boardCentre,
  distanceOf,
  laserCross,
  laserLines,
  mul3,
  outlineUv,
  pageUv,
  poseAt,
  project,
  rightPose,
  rotX,
  rotY,
  rotZ,
  tiltOf,
  toJpeg,
  turnCamera,
  visibleCorners,
  type BoardPose,
  type Lens,
  type LineInfo,
} from "./board";
import { rng } from "./geom";
import { HttpError, json, nowIso, uid, type MockHost } from "./host";
import type { MockSetup } from "./setup";

const TARGET: Record<CalibKind, [number, number]> = { stereo: [15, 25], lasers: [6, 10], imu: [1, 1], check: [3, 5] };
const MIN_VIEWS: Record<CalibKind, number> = { stereo: 4, lasers: 3, imu: 1, check: 1 };
const RANGE: Record<CalibKind, [number, number]> = { stereo: [200, 500], lasers: [150, 450], imu: [150, 600], check: [150, 450] };
const GUIDANCE: Record<CalibKind, string> = {
  stereo: "15–25 views, 200–500 mm away, tilt the board 20–45° in different directions, cover the image corners and edges",
  lasers: "6–10 poses, 150–450 mm away, tilt up to ±30°, both laser lines crossing the white page margin",
  imu: "Hold still 2 s, then rotate about all three axes for 20–60 s, keeping the board in view",
  check: "3–5 views at different distances, both laser lines on the white margin",
};
const REGION_NAMES = [
  "top-left corner",
  "top edge",
  "top-right corner",
  "left edge",
  "centre",
  "right edge",
  "bottom-left corner",
  "bottom edge",
  "bottom-right corner",
];
/** Emptiest first; ties in this order (start in the middle, then the corners, then the edges). */
const REGION_ORDER = [4, 0, 2, 8, 6, 1, 5, 7, 3];
const GRID = 8;
const LINE_MIN = 15;
const STEADY_S = 0.5;

type Spot = { u: number; v: number; dist: number; yaw: number; pitch: number; roll: number };
type TiltBin = "flat" | "tilt_left" | "tilt_right" | "tilt_up" | "tilt_down";
type DistBin = "near" | "mid" | "far";
const TILT_DIRS = ["left", "right", "up", "down"] as const;

type ViewRec = {
  view: CalibView;
  R: number[];
  cellsL: number[];
  cellsR: number[];
  centre: [number, number];
  distance: number;
  tiltBin: TiltBin;
  distBin: DistBin;
  region: number;
  blurred: boolean;
  /** The demo's one view with glare: flagged by the solve until it is deleted. */
  glare: boolean;
};

type Frame = {
  lens: Lens;
  pose: BoardPose;
  spot: Spot;
  left: { uv: number[]; seen: Uint8Array };
  right: { uv: number[]; seen: Uint8Array } | null;
  common: number | null;
  found: boolean;
  distance: number | null;
  tilt: number | null;
  centre: [number, number] | null;
  lines: { vertical: LineInfo; horizontal: LineInfo } | null;
};

type Plan = { text: string; region: number | null; tilt: CalibHintTilt | null; distance: "closer" | "farther" | null; bin: DistBin | null };

const idleSession = (target: CalibTarget): CalibSession => ({
  state: "idle",
  kind: null,
  target,
  id: null,
  started: null,
  auto: true,
  views: [],
  target_views: [0, 0],
  min_views: 0,
  guidance: "",
  emitters: "",
  job_id: null,
  result: null,
  recording: null,
  saved: null,
});

const smooth = (x: number) => x * x * (3 - 2 * x);
const r1 = (x: number) => Math.round(x * 10) / 10;
const r2 = (x: number) => Math.round(x * 100) / 100;
const START: Spot = { u: 660, v: 420, dist: 340, yaw: 4, pitch: -3, roll: 2 };
const IMU_SPOT: Spot = { u: 650, v: 410, dist: 390, yaw: 9, pitch: -7, roll: 3 };
const I3 = [1, 0, 0, 0, 1, 0, 0, 0, 1];

function regionOf(u: number, v: number): number {
  const rx = Math.min(2, Math.max(0, Math.floor((u / IMG_W) * 3)));
  const ry = Math.min(2, Math.max(0, Math.floor((v / IMG_H) * 3)));
  return ry * 3 + rx;
}
const regionRect = (i: number): [number, number, number, number] => {
  const rx = i % 3,
    ry = Math.floor(i / 3);
  return [r2(rx / 3), r2(ry / 3), r2((rx + 1) / 3), r2((ry + 1) / 3)];
};
function cellsOf(uv: number[]): number[] {
  const s = new Set<number>();
  for (let i = 0; i < uv.length; i += 2) {
    const cx = Math.min(GRID - 1, Math.max(0, Math.floor((uv[i] / IMG_W) * GRID)));
    const cy = Math.min(GRID - 1, Math.max(0, Math.floor((uv[i + 1] / IMG_H) * GRID)));
    s.add(cy * GRID + cx);
  }
  return [...s];
}
function tiltBinOf(s: Spot): TiltBin {
  if (Math.hypot(s.yaw, s.pitch) < 12) return "flat";
  if (Math.abs(s.yaw) >= Math.abs(s.pitch)) return s.yaw > 0 ? "tilt_right" : "tilt_left";
  return s.pitch < 0 ? "tilt_up" : "tilt_down";
}
function distBinOf(kind: CalibKind, d: number): DistBin {
  if (kind === "stereo") return d < 290 ? "near" : d < 390 ? "mid" : "far";
  return d < 220 ? "near" : d < 330 ? "mid" : "far";
}

export class MockCalib {
  private host: MockHost;
  private setup: MockSetup;
  session: CalibSession = idleSession("device");
  private recs: ViewRec[] = [];
  private nextIndex = 0;
  private frame: Frame | null = null;
  private prevPos: Float32Array | null = null;
  private still = 0;
  private motion = 99;
  private cooldown = 0;
  private clock = 0;
  private op = { phase: "dwell" as "travel" | "dwell", from: START, to: START, t: 0, dur: 1.4, dwell: 2.4, moves: 0, capturedAt: -1, mistake: false };
  private imu = { recording: false, done: false, elapsed: 0, gyro: 0, seen: 0, frames: 0, prevRc: I3 };
  private renderL = new CalibRenderer(3);
  private renderR = new CalibRenderer(7);
  private rendering = false;
  private liveAcc = 0;
  private previewAcc = 0;
  private rand = rng(99);
  private report: Record<string, unknown> | null = null;
  private lastStep = performance.now();
  private autoSolveSeq = 0;

  constructor(host: MockHost, setup: MockSetup) {
    this.host = host;
    this.setup = setup;
    setInterval(() => this.step(), 50);
  }

  active() {
    return this.session.state !== "idle" && this.session.state !== "saved";
  }
  private streaming() {
    const s = this.session.state;
    return s === "capturing" || s === "armed" || s === "recording";
  }
  private kind(): CalibKind {
    return this.session.kind ?? "stereo";
  }
  private paper(): [number, number] {
    return this.host.settings.board.paper === "a4" ? PAPER_MM.a4 : PAPER_MM.letter;
  }
  private imuTimes() {
    return { still: 2, min: this.host.fast ? 5 : 20, max: this.host.fast ? 12 : 60 };
  }

  broadcast() {
    this.host.send({ type: "calib_session", ...this.session });
  }
  private setSession(p: Partial<CalibSession>) {
    this.session = { ...this.session, ...p };
    this.broadcast();
  }
  private syncViews() {
    this.session = { ...this.session, views: this.recs.map((r) => r.view) };
  }

  // ---------------------------------------------------------------- routes
  route(method: string, seg: string[], body: Record<string, unknown>): Response | undefined {
    const [a, b, c, d] = seg;
    if (a !== "calibration") return undefined;
    const is = (m: string, n: number) => method === m && seg.length === n;
    if (is("GET", 1)) return json(200, this.session);
    if (b === "start" && is("POST", 2)) return json(200, this.start(body.kind));
    if (b === "capture" && is("POST", 2)) {
      const view = this.manualCapture();
      return json(200, { view, session: this.session });
    }
    if (b === "views" && c && is("DELETE", 3)) return json(200, this.deleteView(Number(c)));
    if (b === "views" && c && d === "thumb.jpg" && is("GET", 4))
      throw new HttpError(404, "In the demo, view thumbnails are inline images", "Use the view's thumb_url.");
    if (b === "auto" && is("POST", 2)) {
      this.setSession({ auto: !!body.enabled });
      return json(200, this.session);
    }
    if (b === "imu" && c === "start" && is("POST", 3)) return json(200, this.imuStart());
    if (b === "imu" && c === "stop" && is("POST", 3)) {
      if (this.session.state !== "recording" || this.kind() !== "imu")
        throw new HttpError(409, "The IMU recording isn't running", "Press Start first.");
      this.stopImu(false);
      return json(200, this.session);
    }
    if (b === "solve" && is("POST", 2)) return json(200, this.solve());
    if (b === "continue" && is("POST", 2)) return json(200, this.continueCapture());
    if (b === "save" && is("POST", 2)) return json(200, this.save(body.force === true));
    if (b === "cancel" && is("POST", 2)) return json(200, this.cancel());
    if (b === "report.json" && is("GET", 2)) {
      if (!this.report) throw new HttpError(404, "There's no calibration report yet", "Finish & calibrate first.");
      return new Response(JSON.stringify(this.report, null, 1), { status: 200, headers: { "content-type": "application/json" } });
    }
    return undefined;
  }

  // ---------------------------------------------------------------- session control
  private start(kindIn: unknown): CalibSession {
    const kind = kindIn as CalibKind;
    if (!["stereo", "lasers", "imu", "check"].includes(kind))
      throw new HttpError(422, `Unknown calibration kind “${String(kindIn)}”`, "Use stereo, lasers, imu or check.");
    const dev = this.host.device();
    if (!dev.connected) throw new HttpError(409, "No device is connected", "Connect the scanner or the simulator in the Connect step first.");
    if (dev.source === "replay")
      throw new HttpError(409, "Calibration needs the camera or the simulator, not a replay", "Connect the camera or the simulator in the Connect step.");
    if (this.host.scanning()) throw new HttpError(409, "A scan is running", "Stop the scan on the Scan page first: a scan and a calibration never run together.");
    if (this.active()) {
      if (this.session.kind === kind) return this.session;
      throw new HttpError(409, `A ${this.session.kind} calibration is in progress`, "Finish it or cancel it first.");
    }
    const target = this.setup.target();
    if (dev.source === "camera") {
      const idx = this.setup.connectedIndex;
      if (idx === 0)
        throw new HttpError(409, "This camera doesn't deliver side-by-side stereo images", "In the Connect step, choose the camera marked “looks like the stereo camera”, then reconnect.");
    }
    if (kind !== "stereo" && !this.setup.hasStereo(target))
      throw new HttpError(409, `The ${kind === "check" ? "check" : kind === "imu" ? "IMU step" : "laser step"} needs the stereo calibration`, "Finish the Stereo step first, then come back.");
    const pico = dev.source === "camera" && !!dev.pico?.connected;
    if (kind === "imu" && dev.source === "camera" && !pico)
      throw new HttpError(409, "The IMU step needs the Pico's IMU", "Connect the Pico (see the Connect step), or skip this step: tracking works without it.");
    let emitters = "Emitters off";
    if (kind === "lasers" || kind === "check") {
      if (dev.source === "sim") emitters = "Simulated line lasers on (the real ones are Class 3R: never look into the beams)";
      else if (pico) emitters = "Line lasers on: Class 3R, never look into the beams";
      else {
        emitters = "No Pico: the line lasers stay off";
        this.host.log("warn", "No Pico: the line lasers can't be switched on for the laser calibration");
      }
    }
    if (pico) {
      const prof = (this.host.settings.emitters as { profiles?: Record<string, number[]> } | undefined)?.profiles?.[kind] ?? [0, 0, 0];
      this.host.patchDevice({ pico: { ...dev.pico!, emitters: { vertical: prof[0], horizontal: prof[1], dots: prof[2] } } });
    }
    if (dev.source === "sim") this.host.patchDevice({ sim_scene: `calib_${kind}` });
    this.recs = [];
    this.nextIndex = 0;
    this.clock = 0;
    this.still = 0;
    this.cooldown = 0;
    this.prevPos = null;
    this.frame = null;
    this.op = { phase: "dwell", from: START, to: START, t: 0, dur: 1.4, dwell: 1.2, moves: 0, capturedAt: -1, mistake: false };
    this.imu = { recording: false, done: false, elapsed: 0, gyro: 0, seen: 0, frames: 0, prevRc: I3 };
    const id = uid("cal-");
    this.session = {
      state: kind === "imu" ? "armed" : "capturing",
      kind,
      target,
      id,
      started: nowIso(),
      auto: true,
      views: [],
      target_views: TARGET[kind],
      min_views: MIN_VIEWS[kind],
      guidance: GUIDANCE[kind],
      emitters,
      job_id: null,
      result: null,
      recording: `~/OpenHandScan/scans/calibration/${kind}-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "")}`,
      saved: null,
    };
    this.broadcast();
    return this.session;
  }

  private endSession() {
    const dev = this.host.device();
    if (dev.connected && dev.source === "sim") this.host.patchDevice({ sim_scene: "demo" });
    if (dev.connected && dev.source === "camera" && dev.pico?.connected)
      this.host.patchDevice({ pico: { ...dev.pico, emitters: { vertical: 0, horizontal: 0, dots: 0 } } });
  }

  private cancel(): CalibSession {
    const had = this.active();
    this.endSession();
    this.session = idleSession(this.setup.target());
    this.recs = [];
    this.broadcast();
    if (had) this.host.log("info", "Calibration cancelled: the captured views are kept on disk");
    return this.session;
  }

  /** An idle session names the current target (the simulator connected or left). */
  broadcastTarget(t: CalibTarget) {
    if (this.session.state === "idle" && this.session.target !== t) {
      this.session = idleSession(t);
      this.broadcast();
    }
  }

  /** The device went away: the session is cancelled, the views stay on disk. */
  onDeviceLost() {
    if (!this.active()) {
      if (this.session.state === "saved") this.session = idleSession("device");
      return;
    }
    this.session = idleSession("device");
    this.recs = [];
    this.broadcast();
    this.host.log("warn", "The device disconnected: the calibration session was cancelled. The captured views are kept on disk.");
  }

  private continueCapture(): CalibSession {
    const s = this.session.state;
    if (s !== "result" && !(s === "capturing" && this.session.result)) {
      if (s === "capturing") return this.session;
      throw new HttpError(409, "There's no result to go back from", "Capture views, then Finish & calibrate.");
    }
    if (this.kind() === "imu") {
      this.imu = { recording: false, done: false, elapsed: 0, gyro: 0, seen: 0, frames: 0, prevRc: I3 };
      this.setSession({ state: "armed", result: null });
    } else this.setSession({ state: "capturing", result: null });
    this.cooldown = 0.6;
    return this.session;
  }

  private deleteView(index: number): CalibSession {
    if (this.session.state !== "capturing") throw new HttpError(409, "Views can only be deleted while capturing", "Press “Add more views” first.");
    const i = this.recs.findIndex((r) => r.view.index === index);
    if (i < 0) throw new HttpError(404, `There's no view ${index + 1}`, "It may be deleted already.");
    this.recs.splice(i, 1);
    this.syncViews();
    this.broadcast();
    return this.session;
  }

  private save(force: boolean) {
    const s = this.session;
    if (s.state !== "result" || !s.result) throw new HttpError(409, "There's nothing to save yet", "Finish & calibrate first.");
    const r = s.result;
    if (r.kind === "check") throw new HttpError(409, "A check only reports: there's nothing to save", "Its verdict is kept in Setup.");
    if (!r.can_save) throw new HttpError(409, "This calibration failed: there's nothing to save", "Add views and calibrate again.");
    if (!r.ok && !force) throw new HttpError(409, "This calibration is below the quality bar", "Save it anyway (force), or add views and calibrate again.");
    const saved = this.setup.recordSave(r.kind, r, s.id);
    this.endSession();
    this.setSession({ state: "saved", saved });
    return { session: this.session, setup: this.setup.state() };
  }

  // ---------------------------------------------------------------- capture
  private manualCapture(): CalibView {
    if (this.session.state !== "capturing" || this.kind() === "imu")
      throw new HttpError(409, "Not capturing right now", this.kind() === "imu" ? "The IMU step records continuously: use Start and Stop." : "Start the step first.");
    const f = this.frame;
    if (!f) throw new HttpError(409, "No camera image yet", "Wait a moment for the camera.");
    const rd = this.readiness(f);
    if (!rd.ready) throw new HttpError(409, rd.reason ?? "This view can't be used", rd.hint);
    return this.capture(false);
  }

  private capture(auto: boolean): CalibView {
    const f = this.frame!;
    const kind = this.kind();
    const steady = Math.min(1, this.still / STEADY_S);
    const [lo, hi] = RANGE[kind];
    const warnings: string[] = [];
    const dist = f.distance ?? 0;
    if (dist < lo || dist > hi) warnings.push(`Board at ${Math.round(dist)} mm (aim for ${lo}–${hi} mm)`);
    if (!auto && steady < 0.6) warnings.push("Captured while moving: it may be blurred");
    if (f.lines) {
      const miss = (["vertical", "horizontal"] as const).filter((n) => f.lines![n].count < LINE_MIN);
      if (miss.length) warnings.push(`The ${miss.join(" and ")} line${miss.length > 1 ? "s miss" : " misses"} the white margin`);
    }
    // a fresh picture for the thumbnail
    this.renderL.draw({ lens: f.lens, pose: f.pose, paper: this.paper(), lasers: !!f.lines });
    const view: CalibView = {
      index: this.nextIndex++,
      t: r2(this.clock),
      auto,
      corners_left: f.left.uv.length / 2,
      corners_right: f.right ? f.right.uv.length / 2 : null,
      distance_mm: f.distance !== null ? r1(f.distance) : null,
      tilt_deg: f.tilt !== null ? r1(f.tilt) : null,
      lines: f.lines ? { vertical: f.lines.vertical.count, horizontal: f.lines.horizontal.count } : null,
      thumb_url: this.renderL.thumbnail(),
      error_px: null,
      bad: false,
      warnings,
    };
    const centre = f.centre ?? [IMG_W / 2, IMG_H / 2];
    this.recs.push({
      view,
      R: f.pose.R,
      cellsL: cellsOf(f.left.uv),
      cellsR: f.right ? cellsOf(f.right.uv) : [],
      centre,
      distance: dist,
      tiltBin: tiltBinOf(f.spot),
      distBin: distBinOf(kind, dist),
      region: regionOf(centre[0], centre[1]),
      blurred: steady < 0.6,
      glare: kind === "stereo" && view.index === 6,
    });
    this.cooldown = 1.0;
    this.op.capturedAt = this.clock;
    this.syncViews();
    this.broadcast();
    return view;
  }

  private readiness(f: Frame): { ready: boolean; reason: string | null; hint: string | null } {
    const kind = this.kind();
    if (kind === "imu") return { ready: false, reason: null, hint: null };
    if (!f.found)
      return kind === "stereo"
        ? { ready: false, reason: "Show the board to both cameras", hint: "Hold it 200–500 mm in front of the scanner." }
        : { ready: false, reason: "Show the whole board to the camera", hint: "Hold it 150–450 mm in front of the scanner." };
    if (kind === "stereo" && (f.common ?? 0) < 8)
      return { ready: false, reason: `Show the whole board to both cameras (${f.common} common corners, need 8)`, hint: "Move it towards the middle of the image." };
    if (f.lines) {
      const v = f.lines.vertical.count >= LINE_MIN,
        h = f.lines.horizontal.count >= LINE_MIN;
      if (!v || !h)
        return {
          ready: false,
          reason: !v && !h ? "Both laser lines miss the white margin" : `The ${!v ? "vertical" : "horizontal"} laser line misses the white margin`,
          hint: "Each line must cross the white paper around the squares.",
        };
    }
    if (this.motion > 6) return { ready: false, reason: "Hold still: the board is moving", hint: "Wait until the ring fills, or keep auto-capture on." };
    if (this.cooldown > 0 && this.recs.length)
      return { ready: false, reason: `View ${this.recs[this.recs.length - 1].view.index + 1} captured: move the board to a new spot`, hint: null };
    const sim = this.similarTo(f);
    if (sim) return { ready: false, reason: `Too close to view ${sim.view.index + 1}: move, tilt or change the distance`, hint: "Each view should add something new." };
    return { ready: true, reason: null, hint: null };
  }

  private similarTo(f: Frame): ViewRec | null {
    if (!f.centre || f.distance === null) return null;
    for (const r of this.recs) {
      const moved = Math.hypot(f.centre[0] - r.centre[0], f.centre[1] - r.centre[1]) > 0.12 * IMG_W;
      const dd = Math.abs(f.distance - r.distance) / Math.max(1, r.distance) > 0.1;
      const turned = angleBetween(f.pose.R, r.R) > 8;
      if (!moved && !dd && !turned) return r;
    }
    return null;
  }

  // ---------------------------------------------------------------- the simulated person
  private plan(): Plan {
    const kind = this.kind();
    const n = this.recs.length;
    const [tmin] = TARGET[kind];
    const plan = (text: string, p: Partial<Plan> = {}): Plan => ({ text, region: null, tilt: null, distance: null, bin: null, ...p });
    const div = this.diversity();
    const leastRegion = () => {
      const counts = new Array(9).fill(0);
      for (const r of this.recs) counts[r.region]++;
      let best = REGION_ORDER[0];
      for (const i of REGION_ORDER) if (counts[i] < counts[best]) best = i;
      return { best, counts };
    };
    if (kind === "stereo") {
      const { best, counts } = leastRegion();
      if (counts[best] === 0) return plan(`Move the board to the ${REGION_NAMES[best]}`, { region: best });
      for (const dir of TILT_DIRS) if (div[`tilt_${dir}` as const] === 0) return plan(`Tilt the board ${dir}, 20–45°`, { tilt: dir, region: best });
      if (div.near === 0) return plan("Bring the board closer: about 230 mm", { distance: "closer" });
      if (div.far === 0) return plan("Move the board farther away: about 450 mm", { distance: "farther" });
      if (n < tmin) return plan(`Good: ${tmin - n} more view${tmin - n === 1 ? "" : "s"}, at new spots and tilts`, { region: best });
      return plan("That's plenty: press Enter to calibrate");
    }
    // lasers / check
    if (div.near === 0) return plan("Bring the board closer: under 220 mm", { distance: "closer", bin: "near" });
    if (div.far === 0) return plan("Move the board farther away: over 330 mm", { distance: "farther", bin: "far" });
    if (div.mid === 0) return plan("Hold the board at a middle distance: about 280 mm", { bin: "mid" });
    if (kind === "lasers") for (const dir of TILT_DIRS) if (div[`tilt_${dir}` as const] === 0) return plan(`Tilt the board ${dir}, up to 30°`, { tilt: dir });
    if (n < tmin) return plan(`Good: ${tmin - n} more pose${tmin - n === 1 ? "" : "s"} at other distances or tilts`);
    return plan(kind === "check" ? "That's enough: press Enter to run the check" : "That's enough: press Enter to calibrate");
  }

  private hint(f: Frame): CalibHint {
    const kind = this.kind();
    const centreRect = regionRect(4);
    if (!f.found) return { text: kind === "stereo" ? "Show the board to the cameras" : "Show the whole board to the camera", region: centreRect, tilt: null, distance: null };
    if (kind === "stereo" && (f.common ?? 0) < 8) return { text: "Bring the whole board back into view of both cameras", region: centreRect, tilt: null, distance: null };
    if (f.lines && f.distance !== null && f.centre) {
      const [lu, lv] = laserCross(f.distance);
      if (f.lines.vertical.count < LINE_MIN)
        return { text: `The vertical line misses the white margin: move the board ${f.centre[0] < lu ? "right" : "left"}`, region: null, tilt: null, distance: null };
      if (f.lines.horizontal.count < LINE_MIN)
        return { text: `The horizontal line misses the white margin: move the board ${f.centre[1] < lv ? "down" : "up"}`, region: null, tilt: null, distance: null };
    }
    const p = this.plan();
    return { text: p.text, region: p.region !== null ? regionRect(p.region) : null, tilt: p.tilt, distance: p.distance };
  }

  private planNext() {
    const o = this.op;
    const kind = this.kind();
    const r = this.rand;
    const p = this.plan();
    o.from = this.currentSpot();
    o.moves++;
    o.capturedAt = -1;
    o.t = 0;
    o.phase = "travel";
    o.mistake = false;
    const tiltDir: CalibHintTilt | null = p.tilt ?? (r() < 0.35 ? TILT_DIRS[Math.floor(r() * 4)] : null);
    let yaw = (r() - 0.5) * 12,
      pitch = (r() - 0.5) * 12;
    const amount = kind === "stereo" ? 25 + r() * 13 : 18 + r() * 9;
    if (tiltDir === "left") yaw = -amount;
    else if (tiltDir === "right") yaw = amount;
    else if (tiltDir === "up") pitch = -amount;
    else if (tiltDir === "down") pitch = amount;
    else if (tiltDir === "more") yaw = (yaw < 0 ? -1 : 1) * amount;
    let to: Spot;
    if (kind === "stereo") {
      const region = p.region ?? REGION_ORDER[Math.floor(r() * 9)];
      const rx = region % 3,
        ry = Math.floor(region / 3);
      let dist = p.distance === "closer" ? 215 + r() * 40 : p.distance === "farther" ? 430 + r() * 50 : 290 + r() * 130;
      if (region !== 4 && !p.distance) dist = Math.max(dist, 360 + r() * 90);
      to = {
        u: ((rx + 0.5) / 3) * IMG_W + (r() - 0.5) * 110,
        v: ((ry + 0.5) / 3) * IMG_H + (r() - 0.5) * 60,
        dist,
        yaw,
        pitch,
        roll: (r() - 0.5) * 16,
      };
      if (this.recs.length >= 3 && o.moves % 6 === 5) {
        // a slip: too far to one side, so the right camera loses most of the board
        o.mistake = true;
        to = { ...to, u: r() < 0.5 ? 70 : IMG_W - 40, v: IMG_H / 2, dist: 255, yaw: 0, pitch: 0 };
      }
    } else {
      const bins: DistBin[] = ["near", "mid", "far"];
      const bin = p.bin ?? bins[Math.floor(r() * 3)];
      const dist = bin === "near" ? 178 + r() * 36 : bin === "mid" ? 238 + r() * 80 : 345 + r() * 75;
      const [lu, lv] = laserCross(dist);
      to = { u: lu + (r() - 0.5) * 110, v: lv + (r() - 0.5) * 70, dist, yaw, pitch, roll: (r() - 0.5) * 10 };
      if (this.recs.length >= 2 && o.moves % 5 === 4) {
        // a slip: the board drifts left, the vertical line falls off the page
        o.mistake = true;
        to = { ...to, u: to.u - 390 * (300 / dist), yaw: 0, pitch: 0 };
      }
    }
    o.to = to;
    const span = Math.hypot(to.u - o.from.u, to.v - o.from.v) / IMG_W + Math.abs(to.dist - o.from.dist) / 300;
    o.dur = 0.9 + span * 1.1 + r() * 0.4;
  }

  private currentSpot(): Spot {
    const o = this.op;
    if (o.phase === "travel") {
      const s = smooth(Math.min(1, o.t / o.dur));
      const arc = Math.sin(s * Math.PI);
      const lerp = (a: number, b: number) => a + (b - a) * s;
      return {
        u: lerp(o.from.u, o.to.u),
        v: lerp(o.from.v, o.to.v) - 26 * arc,
        dist: lerp(o.from.dist, o.to.dist) + 18 * arc,
        yaw: lerp(o.from.yaw, o.to.yaw),
        pitch: lerp(o.from.pitch, o.to.pitch),
        roll: lerp(o.from.roll, o.to.roll) + 3 * arc,
      };
    }
    // a hand is never perfectly still
    const t = this.clock;
    return {
      u: o.to.u + 0.25 * Math.sin(t * 7.3),
      v: o.to.v + 0.2 * Math.sin(t * 6.1 + 1),
      dist: o.to.dist + 0.05 * Math.sin(t * 5.2),
      yaw: o.to.yaw + 0.012 * Math.sin(t * 4.4),
      pitch: o.to.pitch + 0.012 * Math.sin(t * 3.7 + 2),
      roll: o.to.roll + 0.01 * Math.sin(t * 5.9),
    };
  }

  private stepOperator(dt: number) {
    const o = this.op;
    o.t += dt;
    if (o.phase === "travel") {
      if (o.t >= o.dur) {
        o.phase = "dwell";
        o.t = 0;
        o.dwell = o.mistake ? 1.9 : this.session.auto ? 2.6 : 4.2;
      }
      return;
    }
    const sinceCapture = o.capturedAt >= 0 ? this.clock - o.capturedAt : -1;
    if (sinceCapture > 0.55 || o.t >= o.dwell) this.planNext();
  }

  // ---------------------------------------------------------------- IMU
  private imuRc(): number[] {
    const im = this.imu;
    const { still } = this.imuTimes();
    const t = this.clock;
    if (!im.recording) return mul3(rotX(0.5 * Math.sin(t * 0.9)), rotY(0.7 * Math.sin(t * 0.7 + 1)));
    const tau = im.elapsed - still;
    if (tau <= 0) return mul3(rotX(0.03 * Math.sin(t * 9)), rotY(0.03 * Math.sin(t * 7)));
    const ramp = Math.min(1, tau / 1.5);
    const w = 2 * Math.PI;
    return mul3(rotX(ramp * 24 * Math.sin(w * 0.42 * tau)), mul3(rotY(ramp * 20 * Math.sin(w * 0.55 * tau + 1)), rotZ(ramp * 16 * Math.sin(w * 0.33 * tau + 2))));
  }

  private stepImu(dt: number) {
    const im = this.imu;
    if (im.recording) {
      im.elapsed += dt;
      const Rc = this.imuRc();
      const inst = angleBetween(im.prevRc, Rc) / Math.max(1e-3, dt) + this.rand() * 0.4;
      im.gyro = 0.65 * im.gyro + 0.35 * inst;
      im.prevRc = Rc;
      const { still, max } = this.imuTimes();
      if (im.elapsed - still >= max) this.stopImu(true);
    } else {
      im.prevRc = this.imuRc();
      im.gyro = 0.65 * im.gyro + 0.35 * (1.5 + this.rand() * 1.5);
    }
  }

  private imuStart(): CalibSession {
    if (this.kind() !== "imu" || (this.session.state !== "armed" && this.session.state !== "capturing"))
      throw new HttpError(409, "The IMU step isn't ready", "Start the IMU step first.");
    this.imu = { recording: true, done: false, elapsed: 0, gyro: this.imu.gyro, seen: 0, frames: 0, prevRc: this.imuRc() };
    this.setSession({ state: "recording", result: null });
    return this.session;
  }

  private stopImu(auto: boolean) {
    const im = this.imu;
    im.recording = false;
    im.done = true;
    const { still } = this.imuTimes();
    if (auto) this.host.log("info", `IMU recording stopped by itself after ${Math.round(im.elapsed - still)} s of rotation`);
    this.setSession({ state: "capturing" });
  }

  private imuLive(): CalibImuLive {
    const im = this.imu;
    const { still, min, max } = this.imuTimes();
    const phase = this.session.state === "armed" ? "armed" : im.recording ? (im.elapsed < still ? "still" : "rotate") : im.done ? "done" : "armed";
    return {
      phase,
      elapsed_s: r1(im.elapsed),
      still_s: still,
      // like the engine: seconds since the first recorded frame (the hold-still time included)
      min_s: still + min,
      max_s: still + max,
      gyro_dps: r1(im.gyro),
      board_pct: im.frames ? Math.round((100 * im.seen) / im.frames) : 0,
    };
  }

  // ---------------------------------------------------------------- the frame loop
  private step() {
    const now = performance.now();
    const dt = Math.min(0.2, (now - this.lastStep) / 1000);
    this.lastStep = now;
    if (!this.streaming()) return;
    this.clock += dt;
    if (this.cooldown > 0) this.cooldown -= dt;
    const kind = this.kind();
    if (kind === "imu") this.stepImu(dt);
    else this.stepOperator(dt);
    const f = this.computeFrame();
    this.frame = f;
    this.updateMotion(dt);
    if (kind === "imu" && this.imu.recording) {
      this.imu.frames++;
      if (f.found) this.imu.seen++;
    }
    if (kind !== "imu" && this.session.auto && this.session.state === "capturing" && this.recs.length < TARGET[kind][1]) {
      if (this.readiness(f).ready && this.still >= STEADY_S) this.capture(true);
    }
    if (!this.streaming()) return; // auto-stop of the IMU recording
    this.liveAcc += dt;
    if (this.liveAcc >= 0.1) {
      this.liveAcc = 0;
      this.host.send(this.live(f) as unknown as Record<string, unknown>);
    }
    this.previewAcc += dt;
    if (this.previewAcc >= 0.1 && !this.rendering) {
      this.previewAcc = 0;
      void this.sendPreviews(f);
    }
  }

  private computeFrame(): Frame {
    const kind = this.kind();
    const spot = kind === "imu" ? IMU_SPOT : this.currentSpot();
    const lens = kind === "stereo" ? LENS_RAW_L : LENS_RECT;
    let pose = poseAt(lens, spot.u, spot.v, spot.dist, spot.yaw, spot.pitch, spot.roll);
    if (kind === "imu") pose = turnCamera(pose, this.imuRc());
    const left = visibleCorners(lens, pose);
    const right = kind === "stereo" ? visibleCorners(LENS_RAW_R, rightPose(pose)) : null;
    let common: number | null = null;
    if (right) {
      common = 0;
      for (let k = 0; k < left.seen.length; k++) if (left.seen[k] && right.seen[k]) common++;
    }
    const found = left.uv.length / 2 >= 6;
    const c = project(lens, boardCentre(pose));
    const lines = kind === "lasers" || kind === "check" ? laserLines(pose, this.paper()) : null;
    return {
      lens,
      pose,
      spot,
      left,
      right,
      common,
      found,
      distance: found ? distanceOf(pose) : null,
      tilt: found ? tiltOf(pose) : null,
      centre: c,
      lines,
    };
  }

  private updateMotion(dt: number) {
    const f = this.frame!;
    const pos = new Float32Array(CORNERS.length * 2).fill(NaN);
    let j = 0;
    for (let k = 0; k < f.left.seen.length; k++) {
      if (!f.left.seen[k]) continue;
      pos[k * 2] = f.left.uv[j * 2];
      pos[k * 2 + 1] = f.left.uv[j * 2 + 1];
      j++;
    }
    let maxD = 0,
      n = 0;
    const prev = this.prevPos;
    if (prev) {
      for (let k = 0; k < CORNERS.length; k++) {
        const a = pos[k * 2],
          b = prev[k * 2];
        if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
        maxD = Math.max(maxD, Math.hypot(a - b, pos[k * 2 + 1] - prev[k * 2 + 1]));
        n++;
      }
    }
    this.motion = n >= 4 ? maxD : 99;
    this.prevPos = pos;
    this.still = this.motion < 1 ? this.still + dt : 0;
  }

  private coverage(): CalibCoverage {
    const stereo = this.kind() === "stereo";
    const left = new Array<number>(GRID * GRID).fill(0);
    const right = stereo ? new Array<number>(GRID * GRID).fill(0) : null;
    for (const r of this.recs) {
      for (const c of r.cellsL) left[c]++;
      if (right) for (const c of r.cellsR) right[c]++;
    }
    const pct = (a: number[]) => Math.round((100 * a.filter((x) => x > 0).length) / a.length);
    return { cols: GRID, rows: GRID, left, right, left_pct: pct(left), right_pct: right ? pct(right) : null };
  }

  private diversity(): CalibDiversity {
    const d: CalibDiversity = { flat: 0, tilt_left: 0, tilt_right: 0, tilt_up: 0, tilt_down: 0, near: 0, mid: 0, far: 0 };
    for (const r of this.recs) {
      d[r.tiltBin]++;
      d[r.distBin]++;
    }
    return d;
  }

  private live(f: Frame): CalibLive {
    const kind = this.kind();
    const imu = kind === "imu";
    const rd = this.readiness(f);
    return {
      type: "calib",
      t: r2(this.clock),
      kind,
      state: this.session.state,
      image_size: [IMG_W, IMG_H],
      fps: r1(19.4 + this.rand() * 0.8),
      left: { corners: f.left.uv },
      right: f.right ? { corners: f.right.uv } : null,
      common: f.common,
      board_found: f.found,
      distance_mm: f.distance !== null ? r1(f.distance) : null,
      tilt_deg: f.tilt !== null ? r1(f.tilt) : null,
      outline: f.found ? outlineUv(f.lens, f.pose) : null,
      page: f.lines && f.found ? pageUv(f.lens, f.pose) : null,
      lines: f.lines
        ? { vertical: f.lines.vertical.count, horizontal: f.lines.horizontal.count, vertical_uv: f.lines.vertical.uv, horizontal_uv: f.lines.horizontal.uv }
        : null,
      ready: rd.ready,
      reason: imu ? (this.session.state === "armed" ? "Press Start, then hold still for 2 s" : null) : rd.reason,
      steady: !imu && rd.ready ? r2(Math.min(1, this.still / STEADY_S)) : 0,
      auto: this.session.auto,
      hint: imu ? null : this.hint(f),
      coverage: imu ? null : this.coverage(),
      diversity: imu ? null : this.diversity(),
      views: this.recs.length,
      target_views: TARGET[kind],
      imu: imu ? this.imuLive() : null,
    };
  }

  private async sendPreviews(f: Frame) {
    if (!this.host.wantsPreviews()) return;
    this.rendering = true;
    try {
      const kind = this.kind();
      const paper = this.paper();
      this.renderL.draw({ lens: f.lens, pose: f.pose, paper, lasers: kind === "lasers" || kind === "check" });
      const jobs = [toJpeg(this.renderL.canvas, 0.8)];
      if (kind === "stereo") {
        this.renderR.draw({ lens: LENS_RAW_R, pose: rightPose(f.pose), paper, lasers: false });
        jobs.push(toJpeg(this.renderR.canvas, 0.8));
      }
      const [l, r] = await Promise.all(jobs);
      if (!this.streaming()) return;
      this.host.sendPreview("calib_left", l, PREV_W, PREV_H);
      if (r) this.host.sendPreview("calib_right", r, PREV_W, PREV_H);
    } catch (e) {
      console.warn("mock calibration previews failed", e);
    } finally {
      this.rendering = false;
    }
  }

  // ---------------------------------------------------------------- solving
  private solve() {
    const s = this.session;
    const kind = this.kind();
    if (s.state === "solving") throw new HttpError(409, "Already calibrating", "Wait for the result.");
    if (s.state !== "capturing") throw new HttpError(409, "Nothing to calibrate yet", "Start the step and capture views first.");
    if (kind === "imu") {
      if (!this.imu.done) throw new HttpError(409, "Record the motion first", "Press Start, hold still for 2 s, then rotate the scanner.");
    } else if (this.recs.length < MIN_VIEWS[kind]) {
      throw new HttpError(409, `Needs at least ${MIN_VIEWS[kind]} views (${this.recs.length} so far)`, `Aim for ${TARGET[kind][0]}–${TARGET[kind][1]}.`);
    }
    const steps: [number, string][] =
      kind === "stereo"
        ? [
            [0.2, `Detecting corners in ${this.recs.length} views…`],
            [0.45, "Calibrating the left lens…"],
            [0.65, "Calibrating the right lens…"],
            [0.85, "Solving the stereo pair…"],
            [1, "Checking the rectification…"],
          ]
        : kind === "lasers"
          ? [
              [0.3, `Finding the laser lines in ${this.recs.length} poses…`],
              [0.7, "Fitting the two laser planes…"],
              [1, "Checking the fit against the board…"],
            ]
          : kind === "imu"
            ? [
                [0.25, "Reading the gyro samples…"],
                [0.6, "Estimating the gyro bias and the camera–IMU rotation…"],
                [1, "Estimating the clock offset…"],
              ]
            : [
                [0.4, "Measuring the rectified row error…"],
                [0.8, "Checking the laser planes against the board…"],
                [1, "Writing the verdict…"],
              ];
    const seq = ++this.autoSolveSeq;
    const job = this.host.startJob("calibrate", {
      seconds: kind === "stereo" ? 5 : kind === "check" ? 3 : 4,
      message: (p) => (steps.find(([e]) => p < e) ?? steps[steps.length - 1])[1],
      done: () => {
        if (seq !== this.autoSolveSeq || this.session.state !== "solving") return { cancelled: true };
        const result = this.result(kind);
        // per-view errors on the views themselves
        for (const pv of result.per_view) {
          const rec = this.recs.find((r) => r.view.index === pv.index);
          if (rec) rec.view = { ...rec.view, error_px: pv.error_px, bad: pv.bad };
        }
        this.syncViews();
        this.report = {
          kind,
          when: nowIso(),
          engine: "OpenHandScan demo (in-browser mock)",
          target: this.session.target,
          recording: this.session.recording,
          views: this.recs.map((r) => ({ ...r.view, thumb_url: undefined })),
          result: { ...result, report_url: undefined },
        };
        if (kind === "check") this.setup.recordCheck(result);
        if (result.grade === "failed") this.setSession({ state: kind === "imu" ? "armed" : "capturing", result, job_id: null });
        else this.setSession({ state: "result", result, job_id: null });
        return { kind, grade: result.grade, ok: result.ok };
      },
    });
    this.setSession({ state: "solving", job_id: job.id, result: null });
    return job;
  }

  /** The solve job was cancelled: back to capturing with the views kept. */
  jobCancelled(jid: string) {
    if (this.session.job_id !== jid) return;
    this.autoSolveSeq++;
    this.setSession({ state: "capturing", job_id: null });
  }

  private result(kind: CalibKind): CalibResult {
    const base = { per_view: [] as CalibResult["per_view"], sim_truth: null as CalibMetric[] | null, report_url: "/api/calibration/report.json" };
    const r = kind === "stereo" ? this.stereoResult() : kind === "lasers" ? this.lasersResult() : kind === "imu" ? this.imuResult() : this.checkResult();
    return { ...base, ...r, kind };
  }

  private sim() {
    return this.session.target === "sim";
  }

  private stereoResult(): Omit<CalibResult, "kind"> {
    const n = this.recs.length;
    const cov = this.coverage();
    const covMin = Math.min(cov.left_pct, cov.right_pct ?? cov.left_pct);
    const div = this.diversity();
    const seenTilts = TILT_DIRS.filter((d) => div[`tilt_${d}` as const] > 0);
    const missing = TILT_DIRS.filter((d) => div[`tilt_${d}` as const] === 0);
    const r = this.rand;
    const fmtList = (a: readonly string[]) => (a.length <= 1 ? a.join("") : `${a.slice(0, -1).join(", ")} and ${a[a.length - 1]}`);
    if (n < 5 || covMin < 12) {
      return {
        ok: false,
        grade: "failed",
        headline: "Not yet: the views can't pin down the lenses",
        verdict: `${n} views covering ${covMin} % of the image aren't enough to tell the focal length from the lens distortion. Your views are kept: add views at the image corners and with tilts, then calibrate again.`,
        metrics: [
          { label: "Views", value: `${n} (aim for 15–25)`, ok: false, help: "Each view adds corners at new places and angles. Fewer than about 10 leaves the lens model guessing." },
          { label: "Coverage", value: `left ${cov.left_pct} % · right ${cov.right_pct ?? 0} %`, ok: false, help: "The share of each image that had board corners in at least one view. Aim for more than 60 %." },
        ],
        advice: [{ text: "Add views with the board in the corners and along the edges of the image, tilted 20–45°.", action: "add_views" }],
        per_view: [],
        sim_truth: null,
        can_save: false,
        report_url: "/api/calibration/report.json",
      };
    }
    const base = 0.15 + 0.9 / n + Math.max(0, 60 - covMin) / 220 + (seenTilts.length < 2 ? 0.1 : 0) + r() * 0.03;
    const errs = this.recs.map((rec) => (rec.blurred ? base * 3.1 + 0.3 : rec.glare && n >= 10 ? base * 2.9 + 0.12 : base * (0.65 + 0.7 * r())));
    const rms = Math.sqrt(errs.reduce((a, e) => a + e * e, 0) / errs.length);
    const sorted = [...errs].sort((a, b) => a - b);
    const median = sorted[Math.floor(sorted.length / 2)];
    const perView = this.recs.map((rec, i) => ({ index: rec.view.index, error_px: r2(errs[i]), bad: errs[i] > Math.max(0.6, 2.3 * median) }));
    const bad = perView.filter((p) => p.bad);
    const grade: CalibGrade = rms < 0.3 && covMin >= 60 && n >= 12 ? "excellent" : rms < 0.5 && covMin >= 45 ? "good" : rms < 0.8 ? "usable" : "poor";
    const baseline = 60.04 + (r() - 0.5) * 0.05 * (1 + rms);
    const rowErr = rms * 0.42;
    const metrics: CalibMetric[] = [
      {
        label: "Reprojection error",
        value: `${rms.toFixed(2)} px (want < 0.5 px)`,
        ok: rms < 0.5,
        help: "How far, on average, the detected corners are from where the calibration puts them. Under 0.5 px is good, under 0.3 px excellent.",
      },
      {
        label: "Coverage",
        value: `left ${cov.left_pct} % · right ${cov.right_pct ?? 0} % (want > 60 %)`,
        ok: covMin >= 60,
        help: "The share of each image that had board corners in at least one view. The lens edges distort the most, so they need views too.",
      },
      {
        label: "Views",
        value: `${n} (aim for 15–25)`,
        ok: n >= 15 ? true : n >= 10 ? null : false,
        help: "More varied views average out detection noise. Past about 25, extra views add little.",
      },
      {
        label: "Tilts",
        value: missing.length ? `${seenTilts.length ? fmtList(seenTilts) : "none"}; missing ${fmtList(missing)}` : "left, right, up and down",
        ok: seenTilts.length >= 3,
        help: "Tilted views pin down the focal length and the principal point. Without them the scale of depth can drift.",
      },
      {
        label: "Baseline",
        value: `${baseline.toFixed(2)} mm`,
        ok: null,
        help: "The distance between the two lens centres, as measured. It sets the scale of every depth measurement. The design value is about 60 mm.",
      },
      {
        label: "Rectified row error",
        value: `${rowErr.toFixed(2)} px (want < 0.3 px)`,
        ok: rowErr < 0.3,
        help: "After rectification a point must sit on the same image row in both cameras, or stereo matching gets worse. This is what the launch check watches.",
      },
    ];
    const advice: CalibAdvice[] = [];
    if (bad.length)
      advice.push({
        text: `${bad.length === 1 ? `View ${bad[0].index + 1} is` : `Views ${fmtList(bad.map((b) => String(b.index + 1)))} are`} off by ${fmtList(bad.map((b) => `${b.error_px.toFixed(2)} px`))}: probably motion blur or glare. Drop ${bad.length === 1 ? "it" : "them"} and calibrate again.`,
        action: "remove_bad_views",
      });
    if (covMin < 60) advice.push({ text: `Coverage is ${covMin} %: add views with the board in the image corners and along the edges.`, action: "add_views" });
    if (seenTilts.length < 3) advice.push({ text: `Add views tilted ${fmtList(missing)} (20–45°): tilts pin down the focal length.`, action: "add_views" });
    if (grade === "poor") {
      advice.push({ text: "If it stays poor, check that the board is glued perfectly flat and printed at 100 %.", action: "check_board" });
      advice.push({ text: "Or start over with fresh views, holding still for each one.", action: "start_over" });
    }
    if (!this.sim() && !this.setup.state().print_check?.ok)
      advice.push({ text: "The print scale check isn't done: a print that is slightly off-size scales every measurement.", action: "reprint" });
    const verdicts: Record<CalibGrade, string> = {
      excellent: `${rms.toFixed(2)} px average error over ${n} views, with ${covMin} % of each image covered. Depth will be accurate to about 0.1 mm at 250 mm.`,
      good: `${rms.toFixed(2)} px average error over ${n} views. Scans will be accurate; more views in the corners would make it excellent.`,
      usable: `${rms.toFixed(2)} px average error. It works, but scans near the image edges will be less accurate. Follow the advice, then calibrate again.`,
      poor: `${rms.toFixed(2)} px average error is high: scans would be visibly distorted. Follow the advice below, then calibrate again.`,
      failed: "",
    };
    return {
      ok: grade !== "poor",
      grade,
      headline: `${grade === "excellent" ? "Excellent" : grade === "good" ? "Good" : grade === "usable" ? "Usable" : "Poor"} stereo calibration`,
      verdict: verdicts[grade],
      metrics,
      advice,
      per_view: perView,
      sim_truth: this.sim()
        ? [
            { label: "Focal length", value: `${(722.4 * (1 + (r() - 0.5) * 0.004 * rms)).toFixed(2)} px (true 722.40)`, ok: true, help: "The estimated focal length of the left lens against the virtual scanner's true one." },
            { label: "Principal point", value: `${(0.2 + rms * 1.1).toFixed(1)} px from the truth`, ok: rms * 1.1 < 1.5, help: "Where the optical axis meets the image. Under 1.5 px off is fine." },
            { label: "Baseline", value: `${baseline.toFixed(3)} mm (true 60.040)`, ok: Math.abs(baseline - 60.04) < 0.1, help: "The estimated lens spacing against the true 60.04 mm." },
            { label: "Distortion k1", value: `${(-0.061 + (r() - 0.5) * 0.004).toFixed(4)} (true −0.0610)`, ok: true, help: "The main radial distortion term of the left lens." },
          ]
        : null,
      can_save: true,
      report_url: "/api/calibration/report.json",
    };
  }

  private lasersResult(): Omit<CalibResult, "kind"> {
    const n = this.recs.length;
    const both = this.recs.filter((r) => r.view.lines && Math.min(r.view.lines.vertical, r.view.lines.horizontal) >= LINE_MIN).length;
    const dists = this.recs.map((r) => r.distance);
    const lo = Math.round(Math.min(...dists)),
      hi = Math.round(Math.max(...dists));
    const spread = hi - lo;
    const div = this.diversity();
    const r = this.rand;
    const report = "/api/calibration/report.json";
    if (n < 3 || both < 3) {
      return {
        ok: false,
        grade: "failed",
        headline: "Not yet: too few poses with both lines on the margin",
        verdict: `Only ${both} of ${n} poses had both laser lines on the white margin; the planes need at least 3. Your poses are kept: add poses where both lines cross the paper around the squares.`,
        metrics: [{ label: "Poses with both lines", value: `${both} of ${n}`, ok: false, help: "Lines on the black-and-white squares are biased, so only the white margin counts." }],
        advice: [{ text: "Add poses with both laser lines crossing the white margin.", action: "add_views" }],
        per_view: [],
        sim_truth: null,
        can_save: false,
        report_url: report,
      };
    }
    const rmsV = 0.028 + 0.09 / n + r() * 0.01,
      rmsH = 0.033 + 0.09 / n + r() * 0.01;
    const grade: CalibGrade = spread >= 150 && n >= 6 ? "excellent" : spread >= 90 ? "good" : spread >= 50 ? "usable" : "poor";
    const advice: CalibAdvice[] = [];
    if (div.near === 0) advice.push({ text: "Add a pose closer than 220 mm: near and far poses pin down the plane angles.", action: "add_views" });
    if (div.far === 0) advice.push({ text: "Add a pose farther than 330 mm.", action: "add_views" });
    if (grade === "poor") advice.push({ text: "All poses are at a similar distance: start over and spread them from 150 to 450 mm.", action: "start_over" });
    return {
      ok: grade !== "poor",
      grade,
      headline: `${grade === "excellent" ? "Excellent" : grade === "good" ? "Good" : grade === "usable" ? "Usable" : "Poor"} laser calibration`,
      verdict:
        grade === "poor"
          ? `The poses span only ${spread} mm of distance, so the plane angles are uncertain. Laser points far from that range would be off.`
          : `Both laser planes fit the board within ${Math.max(rmsV, rmsH).toFixed(3)} mm over ${n} poses from ${lo} to ${hi} mm. Laser points will be accurate across the scanning range.`,
      metrics: [
        { label: "Vertical plane fit", value: `${rmsV.toFixed(3)} mm RMS (want < 0.1 mm)`, ok: rmsV < 0.1, help: "How far the vertical line's points are from the fitted plane, on average." },
        { label: "Horizontal plane fit", value: `${rmsH.toFixed(3)} mm RMS (want < 0.1 mm)`, ok: rmsH < 0.1, help: "How far the horizontal line's points are from the fitted plane, on average." },
        { label: "Poses", value: `${n} (aim for 6–10)`, ok: n >= 6 ? true : null, help: "Each pose adds two line segments on the margin." },
        { label: "Distance spread", value: `${lo}–${hi} mm (want 150 mm or more)`, ok: spread >= 150 ? true : spread >= 90 ? null : false, help: "Poses near and far pin down the angle of each plane." },
        { label: "Both lines on the margin", value: `${both} of ${n} poses`, ok: both === n ? true : null, help: "Poses where a line missed the margin only help the other plane." },
      ],
      advice,
      per_view: this.recs.map((rec) => ({ index: rec.view.index, error_px: r2(0.18 + r() * 0.2), bad: false })),
      sim_truth: this.sim()
        ? [
            { label: "Vertical plane", value: `${(0.02 + r() * 0.04).toFixed(2)}° and ${(0.01 + r() * 0.03).toFixed(2)} mm from the truth`, ok: true, help: "The fitted plane against the virtual laser's true plane." },
            { label: "Horizontal plane", value: `${(0.02 + r() * 0.05).toFixed(2)}° and ${(0.01 + r() * 0.03).toFixed(2)} mm from the truth`, ok: true, help: "The fitted plane against the virtual laser's true plane." },
          ]
        : null,
      can_save: true,
      report_url: report,
    };
  }

  private imuResult(): Omit<CalibResult, "kind"> {
    const im = this.imu;
    const { still, min } = this.imuTimes();
    const rot = Math.max(0, im.elapsed - still);
    const seen = im.frames ? Math.round((100 * im.seen) / im.frames) : 0;
    const r = this.rand;
    const report = "/api/calibration/report.json";
    if (rot < Math.min(8, min)) {
      return {
        ok: false,
        grade: "failed",
        headline: "Not yet: too little rotation",
        verdict: `Only ${Math.round(rot)} s of rotation were recorded: the camera–IMU rotation needs at least ${min} s. Record again and keep rotating until the timer passes ${min} s.`,
        metrics: [{ label: "Rotation recorded", value: `${Math.round(rot)} s (want ${min}–60 s)`, ok: false, help: "Rotation about all three axes is what the fit uses." }],
        advice: [{ text: "Record again: hold still for 2 s, then rotate slowly about all three axes.", action: "start_over" }],
        per_view: [],
        sim_truth: null,
        can_save: false,
        report_url: report,
      };
    }
    const grade: CalibGrade = seen < 60 ? "poor" : rot >= min && seen >= 90 ? "excellent" : rot >= min && seen >= 80 ? "good" : "usable";
    const residual = 0.12 + (100 - seen) / 300 + r() * 0.05;
    const advice: CalibAdvice[] = [];
    if (seen < 80) advice.push({ text: "Keep the board in view while rotating: smaller, slower turns. Then record again.", action: "start_over" });
    return {
      ok: grade !== "poor",
      grade,
      headline: `${grade === "excellent" ? "Excellent" : grade === "good" ? "Good" : grade === "usable" ? "Usable" : "Poor"} IMU calibration`,
      verdict:
        grade === "poor"
          ? `The board was in view in only ${seen} % of the frames, so the camera–IMU rotation is uncertain.`
          : `${Math.round(rot)} s of rotation with the board in view ${seen} % of the time. The gyro will steady tracking on fast sweeps and plain surfaces.`,
      metrics: [
        { label: "Gyro bias", value: `${(0.18 + r() * 0.08).toFixed(2)} °/s`, ok: true, help: "The gyro's reading at rest. It is subtracted from every sample." },
        { label: "Camera–IMU rotation", value: `fit residual ${residual.toFixed(2)}° (want < 0.5°)`, ok: residual < 0.5, help: "How well the gyro's rotations match the camera's, after the fit. It tells tracking how the IMU sits in the scanner." },
        { label: "Clock offset", value: `+${(3.6 + r() * 1.4).toFixed(1)} ms`, ok: null, help: "The delay between the camera and the IMU timestamps, measured and corrected." },
        { label: "Rotation recorded", value: `${Math.round(rot)} s (want ${min}–60 s)`, ok: rot >= min, help: "Rotation about all three axes is what the fit uses." },
        { label: "Board seen", value: `${seen} % of frames (want > 80 %)`, ok: seen >= 80, help: "The board's pose gives the camera's rotation to compare the gyro with." },
      ],
      advice,
      per_view: [],
      sim_truth: this.sim()
        ? [
            { label: "Camera–IMU rotation", value: `${(0.06 + r() * 0.1).toFixed(2)}° from the truth`, ok: true, help: "Against the virtual scanner's true IMU mounting." },
            { label: "Clock offset", value: `${(0.1 + r() * 0.4).toFixed(1)} ms from the truth`, ok: true, help: "Against the simulated timestamp delay." },
          ]
        : null,
      can_save: true,
      report_url: report,
    };
  }

  private checkResult(): Omit<CalibResult, "kind"> {
    const n = this.recs.length;
    const r = this.rand;
    const drift = this.setup.launch?.state === "drift";
    const dv = drift ? 1.31 + r() * 0.1 : 0.13 + r() * 0.08;
    const lasers = this.setup.state().calibration.lasers.length > 0;
    const dl = drift ? 0.31 : 0.035 + r() * 0.02;
    const ok = dv < 0.6;
    const grade: CalibGrade = dv < 0.3 ? "excellent" : dv < 0.6 ? "good" : "poor";
    return {
      ok,
      grade,
      headline: ok ? "Your calibration still holds" : "Your calibration has drifted",
      verdict: ok
        ? `Left and right rows match to ${dv.toFixed(2)} px${lasers ? ` and the laser planes fit the board within ${dl.toFixed(2)} mm` : ""}. Nothing to do: scan away.`
        : `Left and right rows differ by ${dv.toFixed(1)} px: something moved, a knock or heat. Redo the stereo calibration, then the lasers and the IMU.`,
      metrics: [
        { label: "Row error after rectification", value: `${dv.toFixed(2)} px (want < 0.6 px)`, ok, help: "A point must sit on the same image row in both cameras. Drift shows up here first." },
        { label: "Reprojection error", value: `${(0.22 + r() * 0.08 + (drift ? 0.5 : 0)).toFixed(2)} px`, ok: !drift, help: "How well the saved lens model explains the board corners in these views." },
        lasers
          ? { label: "Laser planes", value: `${dl.toFixed(2)} mm from the board (want < 0.2 mm)`, ok: dl < 0.2, help: "How far the laser points on the margin are from where the saved planes put them." }
          : { label: "Laser planes", value: "not calibrated", ok: null, help: "Run the Lasers step to check them too." },
        { label: "Views", value: `${n} (aim for 3–5)`, ok: n >= 3 ? true : null, help: "Views at different distances make the check more reliable." },
      ],
      advice: ok ? [] : [{ text: "Redo the stereo calibration (about 5 minutes), then the lasers and the IMU.", action: "recalibrate_stereo" }],
      per_view: this.recs.map((rec) => ({ index: rec.view.index, error_px: r2(dv * (0.8 + r() * 0.4)), bad: false })),
      sim_truth: null,
      can_save: false,
      report_url: "/api/calibration/report.json",
    };
  }
}
