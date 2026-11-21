/**
 * Mock of the M2 setup endpoints: SetupState per target (the real scanner, "device", and the
 * simulator's sandbox, "sim"), the self-test job with its timed prompts, the devices probe and
 * camera setting, the printables (canvas previews, real PDFs at 100 % scale), the print scale
 * check, and the launch check after each connect.
 */
import type {
  BoardInfo,
  CalibKind,
  CalibResult,
  CalibTarget,
  CameraProbe,
  DevicesProbe,
  LaunchCheck,
  Paper,
  PartInfo,
  Printables,
  PrintCheck,
  PrintFile,
  SelfTestCheck,
  SelfTestResult,
  SetupState,
  SetupStep,
  StepId,
} from "../types";
import { boardSheet, maskSheet, previewDataUrl, sheetPdf, tagsSheet, type Sheet } from "./board";
import { HttpError, json, nowIso, type MockHost } from "./host";
import { mockLaserNames } from "./calib";
import { mockSetupHealth } from "./health";
import { laserFamily, laserWords } from "../../lib/laserSet";

type TargetData = {
  path: string;
  exists: boolean;
  modified: string | null;
  backup: string | null;
  baseline: number | null;
  parts: { stereo: PartInfo | null; lasers: PartInfo | null; imu: PartInfo | null };
  /** The lasers with a calibrated plane. */
  planes: string[];
  selftest: SelfTestResult | null;
  printCheck: PrintCheck | null;
  lastCheck: { when: string; ok: boolean; verdict: string } | null;
  dismissed: boolean;
};

const fresh = (path: string): TargetData => ({
  path,
  exists: false,
  modified: null,
  backup: null,
  baseline: null,
  parts: { stereo: null, lasers: null, imu: null },
  planes: [],
  selftest: null,
  printCheck: null,
  lastCheck: null,
  dismissed: false,
});

export const DEVICE_CALIB_PATH = "~/.ohs/calibration.json";
const SIM_CALIB_PATH = "~/.ohs/studio/sim/calibration.json";
const PRINT_DIR = "~/.ohs/studio/printables";

const CAMERAS: Omit<CameraProbe, "configured" | "in_use">[] = [
  { index: 0, width: 1280, height: 720, fps: 30, fourcc: "YUY2", name: "Integrated Webcam", stereo_like: false },
  { index: 1, width: 2560, height: 800, fps: 60, fourcc: "MJPG", name: "USB3 Stereo Camera", stereo_like: true },
];
export const cameraByIndex = (i: number) => CAMERAS.find((c) => c.index === i) ?? null;

type FileSpec = { file: PrintFile; make: () => Sheet | null; text?: string };

export class MockSetup {
  private host: MockHost;
  private targets: Record<CalibTarget, TargetData> = {
    device: fresh(DEVICE_CALIB_PATH),
    sim: fresh(SIM_CALIB_PATH),
  };
  private printables: Printables = { dir: PRINT_DIR, generated: null, paper: "letter", files: [] };
  private files = new Map<string, FileSpec>();
  private selftestRuns = 0;
  private selftestJob: string | null = null;
  private printJob: string | null = null;
  scale = 1;
  launch: LaunchCheck | null = null;
  /** The camera index the connected camera was opened with. */
  connectedIndex: number | null = null;
  readonly noPico: boolean;

  constructor(host: MockHost, opts: { noPico: boolean }) {
    this.host = host;
    this.noPico = opts.noPico;
  }

  target(): CalibTarget {
    const d = this.host.device();
    return d.connected && d.source === "sim" ? "sim" : "device";
  }

  private data(t: CalibTarget = this.target()) {
    return this.targets[t];
  }

  hasStereo(t: CalibTarget = this.target()) {
    return !!this.data(t).parts.stereo;
  }

  /** What `ohs` would load for the device target, as DeviceState.calibration. */
  deviceCalibration() {
    const d = this.targets.device;
    return {
      loaded: !!d.parts.stereo,
      path: d.path,
      image_size: d.parts.stereo ? ([1280, 800] as [number, number]) : null,
      baseline_mm: d.parts.stereo ? d.baseline : null,
      lasers: d.parts.lasers ? [...d.planes].sort() : [],
      imu: !!d.parts.imu,
    };
  }

  /** The lasers of the current target with a calibrated plane. */
  planes(t: CalibTarget = this.target()): string[] {
    const d = this.data(t);
    return d.parts.lasers ? [...d.planes] : [];
  }

  /** The configured lasers (settings `lasers.lines`). */
  private configured(): string[] {
    return mockLaserNames(this.host.settings);
  }

  /** Is a calibration under way? (Set by the mock server: the calibration mock is made after this one.) */
  calibrating: () => boolean = () => false;

  // ---------------------------------------------------------------- state
  private board(): BoardInfo {
    const paper = (this.host.settings.board.paper === "a4" ? "a4" : "letter") as Paper;
    return {
      squares_x: 11,
      squares_y: 8,
      square_mm: 20,
      marker_mm: 15,
      dictionary: "DICT_5X5_100", // kept in the data, never shown (UX_PLAN R12)
      paper,
      scale: this.scale,
      // the engine's words without the markers' dictionary (ohs/server/setup.py board_info, R12)
      describe: `11 x 8 squares of 20 mm (markers 15 mm), printed on ${paper === "a4" ? "A4" : "Letter"}`,
    };
  }

  private steps(t: CalibTarget): SetupStep[] {
    const d = this.data(t);
    const dev = this.host.device();
    const sim = t === "sim";
    const step = (id: StepId, state: SetupStep["state"], summary: string, when: string | null = null): SetupStep => ({ id, state, summary, when });
    const out: SetupStep[] = [];

    if (sim) out.push(step("connect", "done", "The simulator is connected"));
    else if (dev.connected && dev.source === "camera") {
      const cam = cameraByIndex(this.connectedIndex ?? 1);
      if (cam && !cam.stereo_like) out.push(step("connect", "warn", `Camera ${cam.index} isn't the scanner's cameras: pick another`));
      else if (dev.pico?.connected) out.push(step("connect", "done", `Cameras and control board (${dev.pico.firmware ?? "firmware ?"}) on ${dev.pico.port ?? "USB"}`));
      else out.push(step("connect", "warn", "Cameras connected, but no control board"));
    } else if (dev.connected && dev.source === "replay") out.push(step("connect", "todo", "A replay is connected: connect the scanner"));
    else out.push(step("connect", "todo", "Not connected yet"));

    const st = d.selftest;
    if (!st) out.push(step("selftest", "todo", "Not run yet"));
    else {
      const warns = st.checks.filter((c) => c.status === "WARN").length;
      const fails = st.checks.filter((c) => c.status === "FAIL").length;
      if (st.overall === "PASS") out.push(step("selftest", "done", "Every check passed", st.when));
      else
        out.push(
          step(
            "selftest",
            "warn",
            [fails ? `${fails} failed` : "", warns ? `${warns} warning${warns === 1 ? "" : "s"}` : ""].filter(Boolean).join(", "),
            st.when,
          ),
        );
    }

    if (sim) out.push(step("print", "optional", "The simulator's board is virtual: nothing to print"));
    else if (d.printCheck?.ok) out.push(step("print", "done", `${this.board().paper === "a4" ? "A4" : "Letter"}, scale bar ${d.printCheck.measured_mm.toFixed(1)} mm`, d.printCheck.when));
    else if (d.printCheck)
      out.push(step("print", "warn", `The print is ${d.printCheck.error_pct > 0 ? "+" : "−"}${Math.abs(d.printCheck.error_pct).toFixed(1)} % off`, d.printCheck.when));
    else if (this.printables.generated) out.push(step("print", "todo", "Printed? Check the 100 mm scale bar", this.printables.generated));
    else out.push(step("print", "todo", "Not printed yet"));

    const part = (id: "stereo" | "lasers" | "imu") => d.parts[id];
    const ps = part("stereo");
    out.push(ps ? step("stereo", "done", ps.summary, ps.when) : step("stereo", "todo", "Not calibrated yet"));
    const pl = part("lasers");
    const conf = this.configured();
    const missing = pl ? conf.filter((n) => !d.planes.includes(n)) : conf;
    if (pl && missing.length)
      out.push(
        step(
          "lasers",
          "warn",
          `${conf.length - missing.length} of ${conf.length} lasers calibrated: calibrate the lasers again for the ${missing.map(laserWords).join(" and the ")} laser${missing.length > 1 ? "s" : ""}`,
          pl.when,
        ),
      );
    else out.push(pl ? step("lasers", "done", pl.summary, pl.when) : step("lasers", "todo", ps ? "Not calibrated yet" : "Calibrate the cameras first"));
    const pi = part("imu");
    const noImu = !sim && dev.connected && dev.source === "camera" && !dev.pico?.connected;
    out.push(
      pi
        ? step("imu", "done", pi.summary, pi.when)
        : noImu
          ? step("imu", "optional", "Needs the control board's motion sensor")
          : step("imu", "todo", ps ? "Not calibrated yet" : "Calibrate the cameras first"),
    );
    // the step's own state, as the engine gives it (ohs/server/setup.py): the last 2-minute check, never the launch
    // check's drift, which the calibration's status says (H4)
    const lc = this.lastCheckOf(d);
    out.push(
      lc
        ? step("check", lc.ok ? "done" : "warn", lc.verdict, lc.when)
        : step("check", "optional", ps ? "Run it any time, e.g. before an important scan" : "Needs a calibration"),
    );
    return out;
  }

  /** `?health=failed`: a 2-minute check that did not pass two days ago, until a check is run in the demo. */
  private readonly failedCheck = {
    when: new Date(Date.now() - 2 * 86_400_000).toISOString().replace(/\.\d+Z$/, "+00:00"),
    ok: false,
    verdict: "The cameras have moved: redo the camera calibration",
  };

  private lastCheckOf(d: TargetData) {
    if (d.lastCheck || this.host.health !== "failed") return d.lastCheck;
    // as the engine: a check from before the last save does not count
    return d.modified && Date.parse(d.modified) > Date.parse(this.failedCheck.when) ? null : this.failedCheck;
  }

  state(): SetupState {
    const t = this.target();
    const d = this.data(t);
    return {
      target: t,
      first_run: !d.parts.stereo && !d.dismissed,
      dismissed: d.dismissed,
      steps: this.steps(t),
      calibration: {
        path: d.path,
        exists: d.exists,
        modified: d.modified,
        image_size: d.parts.stereo ? [1280, 800] : null,
        baseline_mm: d.parts.stereo ? d.baseline : null,
        stereo: !!d.parts.stereo,
        lasers: this.planes(t).sort(),
        imu: !!d.parts.imu,
        parts: { ...d.parts },
        backup: d.backup,
        lasers_configured: this.configured(),
        lasers_missing: this.configured().filter((n) => !this.planes(t).includes(n)),
      },
      selftest: d.selftest,
      print_check: t === "sim" ? null : d.printCheck,
      board: this.board(),
      launch_check: this.launch,
      last_check: this.lastCheckOf(d),
      // H3: the launch check counts on the camera and the simulator, never on a replay (it is null then). The mock's
      // simulator scans with its true calibration before its wizard was run, so its status shows from the start.
      calibration_health: mockSetupHealth({
        stereo: !!d.parts.stereo || (t === "sim" && this.host.device().calibration.loaded),
        option: this.host.health,
        launch: this.launch,
        lastCheck: this.lastCheckOf(d),
      }),
    };
  }

  broadcast() {
    this.host.send({ type: "setup", ...this.state() });
  }

  // ---------------------------------------------------------------- routes
  /** Handles /api/setup/*, /api/settings/camera and /api/settings/process; `undefined` when the path isn't one of them. */
  route(method: string, seg: string[], body: Record<string, unknown>): Response | Promise<Response> | undefined {
    const [a, b, c, d] = seg;
    const is = (m: string, n: number) => method === m && seg.length === n;
    if (a === "settings" && b === "camera" && is("POST", 2)) return json(200, this.setCamera(body));
    if (a === "settings" && b === "process" && is("POST", 2)) return json(200, this.setProcess(body));
    if (a === "settings" && b === "lasers" && is("POST", 2)) return json(200, this.setLasers(body));
    if (a !== "setup") return undefined;
    if (is("GET", 1)) return json(200, this.state());
    if (b === "dismiss" && is("POST", 2)) {
      this.data().dismissed = !!body.dismissed;
      this.broadcast();
      return json(200, this.state());
    }
    if (b === "selftest" && is("POST", 2)) return json(200, this.startSelftest());
    if (b === "devices" && is("GET", 2)) return json(200, this.probe());
    if (b === "printables") {
      if (is("GET", 2)) return json(200, this.printables);
      if (is("POST", 2)) return json(200, this.generate(body.paper));
      if (c === "open" && is("POST", 3)) return json(200, { opened: false, dir: this.printables.dir });
      if (c === "files" && d && is("GET", 4)) return this.file(d);
    }
    if (b === "print-check" && is("POST", 2)) return json(200, this.printCheck(body));
    return undefined;
  }

  // ---------------------------------------------------------------- devices and settings
  probe(): DevicesProbe {
    const dev = this.host.device();
    const idx = this.host.settings.camera.index;
    const inUse = dev.connected && dev.source === "camera" ? this.connectedIndex : null;
    return {
      cameras: CAMERAS.map((c) => ({ ...c, configured: c.index === idx, in_use: c.index === inUse })),
      pico_port: this.noPico ? null : "COM5",
      configured_index: idx,
      note: this.noPico ? "The control board (Pico or ESP32) was not found: connect its data USB cable (some cables only charge) and flash the firmware (firmware/README.md)." : null,
    };
  }

  private setCamera(body: Record<string, unknown>) {
    const cam = this.host.settings.camera;
    if (body.index !== undefined) {
      const i = Number(body.index);
      if (!Number.isInteger(i) || !cameraByIndex(i))
        throw new HttpError(404, `There is no camera ${String(body.index)}`, "Pick one of the cameras in the list (refresh it after plugging one in).");
      cam.index = i;
    }
    if (body.swap_lr !== undefined) cam.swap_lr = !!body.swap_lr;
    this.host.log("info", "Camera settings saved to ~/.ohs/config.toml: they take effect on the next connect");
    return this.host.settings;
  }

  private setProcess(body: Record<string, unknown>) {
    if (body.thin_walls !== undefined) {
      const v = String(body.thin_walls);
      if (v !== "auto" && v !== "on" && v !== "off") throw new HttpError(422, `thin_walls cannot be “${v}”`, "Use auto, on or off.");
      (this.host.settings.process as Record<string, unknown>).thin_walls = v;
      this.host.log("info", "Meshing settings saved to ~/.ohs/config.toml: the next mesh uses them");
    }
    return this.host.settings;
  }

  /** Any of `lasers.lines`, `own_channels` and `brightness`, checked as the engine's config checks them. Planes
   * already calibrated are kept. The brightness alone may change during a scan, like the emitter sliders. */
  private setLasers(body: Record<string, unknown>) {
    const cur = (this.host.settings.lasers ?? {}) as { lines?: string[]; own_channels?: boolean; brightness?: Record<string, number> };
    const curLines = cur.lines ?? ["vertical", "horizontal"];
    const curBright = cur.brightness ?? {};
    if (body.lines === undefined && body.own_channels === undefined && body.brightness === undefined)
      throw new HttpError(422, "nothing to change", "Send lines, own_channels or brightness.");
    const lines = body.lines === undefined ? curLines : body.lines;
    if (!Array.isArray(lines) || !lines.every((x) => typeof x === "string"))
      throw new HttpError(422, "lines must be a list of laser names", 'Send for example {"lines": ["vertical", "horizontal"]}.');
    if (body.own_channels !== undefined && typeof body.own_channels !== "boolean")
      throw new HttpError(422, "own_channels must be true or false", "Send true when each laser has its own wire.");
    const own = typeof body.own_channels === "boolean" ? body.own_channels : (cur.own_channels ?? true);
    const given = body.brightness;
    if (given !== undefined && (given === null || typeof given !== "object" || Array.isArray(given)))
      throw new HttpError(422, "brightness must be a table of laser name -> percent", 'Send for example {"brightness": {"vertical_2": 85}}.');
    const hint = 'List every line laser once, each name starting with "vertical" or "horizontal" (for example ["vertical", "horizontal", "vertical_2", "horizontal_2"]).';
    const bad = lines.filter((n) => !laserFamily(n));
    if (bad.length) throw new HttpError(422, `${JSON.stringify(bad)} do not start with a laser family (vertical, horizontal)`, hint);
    const twice = [...new Set(lines.filter((n, i) => lines.indexOf(n) !== i))];
    if (twice.length) throw new HttpError(422, `${JSON.stringify(twice)} named twice`, hint);
    for (const f of ["vertical", "horizontal"] as const)
      if (!lines.some((n) => laserFamily(n) === f)) throw new HttpError(422, `lasers.lines needs at least one ${f} line`, hint);
    // a change of lines without a brightness drops the brightness of the lasers that are gone
    const bright: Record<string, number> = {};
    for (const [k, v] of Object.entries((given as Record<string, unknown> | undefined) ?? curBright))
      if (given !== undefined || lines.includes(k)) bright[k] = Number(v);
    const bHint = 'Give each laser\'s brightness as a percent from 0 to 100, by the names in lasers.lines (for example {"vertical_2": 85}).';
    const unknown = Object.keys(bright).filter((k) => !lines.includes(k));
    if (unknown.length) throw new HttpError(422, `lasers.brightness: ${JSON.stringify(unknown.sort())} are not in lasers.lines`, bHint);
    for (const [k, v] of Object.entries(bright))
      if (!Number.isFinite(v) || v < 0 || v > 100) throw new HttpError(422, `lasers.brightness.${k} must be a percent in 0..100 (got ${String(v)})`, bHint);
    const wiring = body.lines !== undefined || body.own_channels !== undefined;
    const what = wiring ? "the line lasers" : "the laser brightness";
    if (wiring && this.host.scanning()) throw new HttpError(409, "A scan is running", `Stop the scan before changing ${what}.`);
    if (this.calibrating()) throw new HttpError(409, "A calibration is running", `Finish or cancel it before changing ${what}.`);
    const linesChanged = lines.join() !== curLines.join();
    const ownChanged = own !== (cur.own_channels ?? true);
    this.host.settings.lasers = { lines: [...lines], own_channels: own, brightness: bright };
    if (linesChanged) {
      const missing = this.state().calibration.lasers_missing ?? [];
      this.host.log("info", `Line lasers saved: ${lines.length} lasers${missing.length && this.hasStereo() ? "; calibrate the lasers (setup step 5) for the new ones" : ""}`);
    } else if (ownChanged) this.host.log("info", `Laser wiring saved: ${own ? "each laser has its own wire" : "each pair of lasers shares one wire"}`);
    else if (JSON.stringify(bright) !== JSON.stringify(curBright)) this.host.log("info", "Laser brightness saved");
    this.broadcast();
    return this.host.settings;
  }

  // ---------------------------------------------------------------- self-test
  private startSelftest() {
    const dev = this.host.device();
    if (!dev.connected) throw new HttpError(409, "No device is connected", "Connect the scanner (or the simulator) in the Connect step first.");
    if (dev.source === "replay") throw new HttpError(409, "The self-test needs the scanner or the simulator, not a replay", "Connect the camera or the simulator in the Connect step.");
    if (this.host.scanning()) throw new HttpError(409, "A scan is running", "Stop the scan first.");
    if (this.selftestJob) throw new HttpError(409, "The self-test is already running", "Follow the prompts on screen.");
    const sim = dev.source === "sim";
    const pico = !!dev.pico?.connected;
    const run = ++this.selftestRuns;
    const target = this.target();
    const phases: [number, string][] = sim
      ? [
          [0.18, "Opening the simulated cameras…"],
          [0.62, "Keep the scanner still for 2 s while the cameras and the motion sensor are measured"],
          [0.9, "Skipping the lasers (simulator)"],
          [1, "Writing the results…"],
        ]
      : [
          [0.07, `Opening camera ${this.connectedIndex ?? 1} (2560×800 MJPG at 60 fps)…`],
          [0.12, pico ? "Connecting to the control board…" : "Looking for the control board…"],
          [0.4, "Keep the scanner still for 3 s while the cameras and the motion sensor are measured"],
          ...(pico
            ? ([
                [0.47, "Point the scanner at a wall 200–400 mm away. The lasers are Class 3R: never look into them"],
                [0.55, "Vertical line laser on for 1 s: look for the line on the wall"],
                [0.63, "Horizontal line laser on for 1 s: look for the line on the wall"],
                [0.71, "Dots on for 1 s: look for the dots on the wall"],
                [0.94, "Press and release the trigger now"],
              ] as [number, string][])
            : []),
          [1, "Writing the results…"],
        ];
    const job = this.host.startJob("selftest", {
      seconds: sim ? 6 : pico ? 13 : 6,
      message: (p) => (phases.find(([end]) => p < end) ?? phases[phases.length - 1])[1],
      done: () => {
        this.selftestJob = null;
        const res = this.selftestResult(sim, pico, run);
        this.targets[target].selftest = res;
        this.broadcast();
        return res as unknown as Record<string, unknown>;
      },
    });
    this.selftestJob = job.id;
    return job;
  }

  /** Clears a cancelled self-test job so another can start. */
  jobCancelled(jid: string) {
    if (this.selftestJob === jid) this.selftestJob = null;
    if (this.printJob === jid) this.printJob = null;
  }

  private selftestResult(sim: boolean, pico: boolean, run: number): SelfTestResult {
    const C = (name: string, status: SelfTestCheck["status"], detail: string, fix: string | null = null): SelfTestCheck => ({ name, status, detail, fix });
    let checks: SelfTestCheck[];
    if (sim) {
      checks = [
        C("camera", "PASS", "two pictures of 1,280 × 800 side by side, from the simulator"),
        C("camera image", "PASS", "Rendered board scene, left and right differ"),
        C("pico", "SKIP", "the simulator has no control board"),
        C("imu values", "PASS", "gravity reads 9.81 m/s²"),
        C("emitters", "SKIP", "Simulated lasers and dots are always available"),
        C("trigger", "SKIP", "The simulator has no trigger: use Space instead"),
      ];
    } else {
      const cam = cameraByIndex(this.connectedIndex ?? 1);
      checks = [
        cam && !cam.stereo_like
          ? C(
              "camera",
              "WARN",
              `two pictures of ${(cam.width / 2).toLocaleString("en-US")} × ${cam.height.toLocaleString("en-US")} side by side (${cam.width.toLocaleString("en-US")} × ${cam.height.toLocaleString("en-US")}), MJPG; the settings ask for 2,560 × 800 (is the format MJPG?)`,
              "The camera runs another mode than the settings ask for: use the 2,560 × 800 MJPG mode, or pick the scanner's cameras in step 1 (Connect).",
            )
          : C("camera", "PASS", "two pictures of 1,280 × 800 side by side (2,560 × 800), MJPG"),
        C("camera fps", "PASS", "59.8 fps measured (60 requested)"),
        run === 1
          ? C(
              "camera image",
              "WARN",
              "35 % of the pixels are too bright: shorten the exposure",
              "Move away from the bright lamp or window, or set a shorter camera.exposure in the settings file, then run the self-test again.",
            )
          : C("camera image", "PASS", "average brightness 104 of 255, 0.4 % too bright"),
        ...(pico
          ? [
              C("pico", "PASS", "firmware ohs-fw 1.0.0 on COM5, motion sensor (IMU): LSM6DSOX"),
              C("imu rate", "PASS", "415.8 Hz measured (416 requested)"),
              C("imu values", "PASS", "gravity reads 9.80 m/s², turning noise 0.08°/s, offset 0.21°/s"),
              C("clock sync", "PASS", "round trip 0.6 ms: timestamps good to better than 1 ms"),
              C("vertical line laser", "PASS", "on at 100% for 1 s (the camera saw 3.10% of the pixels light up)"),
              C("horizontal line laser", "PASS", "on at 100% for 1 s (the camera saw 2.84% of the pixels light up)"),
              run === 1
                ? C(
                    "dot projector",
                    "WARN",
                    "on at 100% for 1 s: confirm you saw it; the camera saw no change",
                    "If you did not see it light up: check the laser key switch and the wiring, and aim at a light wall 200–400 mm away. Never look into the beams.",
                  )
                : C("dot projector", "PASS", "on at 100% for 1 s (the camera saw 11.6% of the pixels light up)"),
              C("trigger", "PASS", "press and release seen"),
            ]
          : [
              C(
                "pico",
                "FAIL",
                "the control board was not found",
                "Connect the control board (Pico or ESP32) with a data USB cable (some cables only charge) and flash the OpenHandScan firmware (firmware/README.md); if it still is not found, set its serial port in the settings.",
              ),
              C("imu", "SKIP", "needs the control board"),
              C("emitters", "SKIP", "needs the control board"),
              C("trigger", "SKIP", "needs the control board"),
            ]),
      ];
    }
    const overall = checks.some((c) => c.status === "FAIL") ? "FAIL" : checks.some((c) => c.status === "WARN") ? "WARN" : "PASS";
    return { overall, when: nowIso(), source: sim ? "sim" : "camera", checks };
  }

  // ---------------------------------------------------------------- printables
  private generate(paperIn: unknown) {
    if (this.printJob) throw new HttpError(409, "The printables are being made already", "Wait for that job to finish.");
    if (paperIn !== undefined && paperIn !== "letter" && paperIn !== "a4")
      throw new HttpError(422, `Unknown paper “${String(paperIn)}”`, "Use letter or a4.");
    const paper: Paper = paperIn === "a4" ? "a4" : paperIn === "letter" ? "letter" : this.board().paper;
    if (paper !== this.host.settings.board.paper) {
      this.host.settings.board.paper = paper;
      this.host.log("info", `Paper set to ${paper === "a4" ? "A4" : "Letter"} (board.paper)`);
    }
    const label = paper === "a4" ? "A4" : "Letter";
    const job = this.host.startJob("printables", {
      seconds: 4.5,
      message: (p) =>
        p < 0.25
          ? `Drawing the ChArUco board (${label})…`
          : p < 0.45
            ? "Drawing 48 AprilTag stickers…"
            : p < 0.85
              ? "Rendering the projector mask at 1200 dpi…"
              : "Writing the PDFs…",
      done: () => {
        this.printJob = null;
        this.makeFiles(paper);
        this.broadcast();
        return this.printables as unknown as Record<string, unknown>;
      },
    });
    this.printJob = job.id;
    return job;
  }

  private makeFiles(paper: Paper) {
    const url = (n: string) => `/api/setup/printables/files/${encodeURIComponent(n)}`;
    const specs: FileSpec[] = [
      {
        file: {
          name: "charuco_board.pdf",
          kind: "board",
          title: "Calibration board",
          description: "ChArUco 11 × 8, 20 mm squares, with the 100 mm scale bar. Print on matte paper, then glue it flat to glass.",
          url: url("charuco_board.pdf"),
          preview_url: previewDataUrl(boardSheet(paper, 2)),
          bytes: 186_420,
        },
        make: () => boardSheet(paper, 5),
      },
      {
        file: {
          name: "apriltags.pdf",
          kind: "tags",
          title: "AprilTag stickers",
          description: "48 tags (tag36h11, 20 mm) for marker mode on big flat panels. Print on matte removable label paper and cut along the dashed lines.",
          url: url("apriltags.pdf"),
          preview_url: previewDataUrl(tagsSheet(paper, 2)),
          bytes: 142_875,
        },
        make: () => tagsSheet(paper, 5),
      },
      {
        file: {
          name: "projector_mask_sheet.pdf",
          kind: "mask",
          title: "LED projector mask (option B)",
          description:
            "Another option for the dots: the LED and a printed mask. Print at 100 % on laser transparency film (1200 dpi) and cut out the cleanest of the 48 copies.",
          url: url("projector_mask_sheet.pdf"),
          preview_url: previewDataUrl(maskSheet(paper, 2)),
          bytes: 2_412_090,
        },
        make: () => maskSheet(paper, 6),
      },
      {
        file: {
          name: "projector_mask_film.gbr",
          kind: "mask",
          title: "Projector mask film (Gerber)",
          description: "The same mask for a photoplot service: a sharper film mask for option B.",
          url: url("projector_mask_film.gbr"),
          preview_url: null,
          bytes: 864_312,
        },
        make: () => null,
        text: "G04 OpenHandScan LED projector mask (demo file from the in-browser mock)*\n%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,0.042*%\nD10*\nX0Y0D03*\nM02*\n",
      },
    ];
    this.files = new Map(specs.map((s) => [s.file.name, s]));
    this.printables = { dir: PRINT_DIR, generated: nowIso(), paper, files: specs.map((s) => s.file) };
  }

  private async file(name: string): Promise<Response> {
    const spec = this.files.get(name);
    if (!spec) throw new HttpError(404, `No printable called ${name}`, "Make the printables first.");
    if (spec.text !== undefined) return new Response(spec.text, { status: 200, headers: { "content-type": "application/octet-stream" } });
    const s = spec.make();
    if (!s) throw new HttpError(404, `No printable called ${name}`);
    const pdf = await sheetPdf(s);
    return new Response(pdf, { status: 200, headers: { "content-type": "application/pdf", "content-length": String(pdf.byteLength) } });
  }

  // ---------------------------------------------------------------- print check
  private printCheck(body: Record<string, unknown>): PrintCheck {
    const m = Number(body.measured_mm);
    if (!Number.isFinite(m) || m < 50 || m > 150)
      throw new HttpError(422, "Type the length you measured, in mm (for example 99.6)", "Measure the 100 mm bar under the board with calipers.");
    const err = ((m - 100) / 100) * 100;
    const a = Math.abs(err);
    const sign = err > 0 ? "+" : "−";
    const pct = `${sign}${a.toFixed(1)} %`;
    let pc: PrintCheck;
    if (a <= 0.3)
      pc = { measured_mm: m, error_pct: err, ok: true, scale: m / 100, can_apply: false, when: nowIso(), verdict: `Spot on: the bar measures ${m.toFixed(1)} mm, within ±0.3 %. The board is printed at the right size.` };
    else if (a <= 4)
      pc = {
        measured_mm: m,
        error_pct: err,
        ok: false,
        scale: m / 100,
        can_apply: true,
        when: nowIso(),
        verdict: `The bar measures ${m.toFixed(1)} mm (${pct}): your printer scaled the page. Reprint at 100 % (“actual size”, never “fit to page”), or use your measurement: Studio then scales the board for calibration only.`,
      };
    else
      pc = {
        measured_mm: m,
        error_pct: err,
        ok: false,
        scale: m / 100,
        can_apply: false,
        when: nowIso(),
        verdict: `${m.toFixed(1)} mm is ${pct} off: more than a printer's scaling. Measure the bar again, then reprint at 100 % with “fit to page” off.`,
      };
    if (body.apply === true) {
      if (!pc.can_apply) throw new HttpError(409, "This measurement can't be used", a <= 0.3 ? "The print is exact: there's nothing to correct." : "It's too far off: measure again and reprint.");
      this.scale = m / 100;
      pc = { ...pc, ok: true, can_apply: false, verdict: `Using your measurement: the board counts as ${(this.scale * 100).toFixed(1)} % of its nominal size for calibration. Printing is unchanged.` };
    } else if (pc.ok) this.scale = 1;
    if (this.target() === "device") this.targets.device.printCheck = pc;
    this.broadcast();
    return pc;
  }

  // ---------------------------------------------------------------- calibration results
  /** Saving a result merges it like `ohs calibrate`; returns where it went and the notes. */
  recordSave(kind: CalibKind, result: CalibResult, session: string | null): { path: string; backup: string | null; notes: string[] } {
    const t = this.target();
    const d = this.data(t);
    const when = nowIso();
    const value = (i: number) => (result.metrics[i]?.value ?? "").replace(/\s*\(.*\)\s*$/, "");
    const head = value(0);
    const notes: string[] = [];
    const info = (summary: string): PartInfo => ({ when, summary, session });
    if (kind === "stereo") {
      const dropped = [d.parts.lasers ? "laser calibration" : "", d.parts.imu ? "motion sensor calibration" : ""].filter(Boolean);
      if (dropped.length)
        notes.push(
          `The new camera calibration replaces the old one, so the ${dropped.join(" and the ")} ${dropped.length > 1 ? "were" : "was"} removed. Redo the ${[
            d.parts.lasers ? "Lasers" : "",
            d.parts.imu ? "Motion sensor (IMU)" : "",
          ]
            .filter(Boolean)
            .join(" and ")} step${dropped.length > 1 ? "s" : ""}.`,
        );
      d.parts = { stereo: info(`${head} · ${result.per_view.length} views`), lasers: null, imu: null };
      d.planes = [];
      d.baseline = 60.03 + Math.random() * 0.03;
      d.lastCheck = null;
    } else if (kind === "lasers") {
      // like merge_calibration: fitted planes replace those of the same name, the others are kept
      const fitted = result.planes?.filter((p) => p.calibrated).map((p) => p.name) ?? ["vertical", "horizontal"];
      const kept = d.parts.lasers ? d.planes.filter((n) => !fitted.includes(n)) : [];
      d.planes = [...fitted, ...kept];
      d.parts.lasers = info(`${head} · ${fitted.length === 2 ? "both planes" : `all ${fitted.length} planes`}`);
    }
    else if (kind === "imu") d.parts.imu = info(`${value(3)} of rotation · ${value(1)}`);
    d.backup = d.exists ? `${d.path}.bak` : null;
    d.exists = true;
    d.modified = when;
    if (t === "device") {
      const dev = this.host.device();
      if (dev.connected && dev.source === "camera") {
        this.host.patchDevice({ calibration: this.deviceCalibration() });
        this.host.log("info", "The camera reloaded the new calibration");
      }
    }
    this.broadcast();
    return { path: d.path, backup: d.backup, notes };
  }

  recordCheck(result: CalibResult) {
    const d = this.data();
    d.lastCheck = { when: nowIso(), ok: result.ok, verdict: result.headline };
    if (result.ok && this.launch?.state === "drift") {
      this.launch = { ...this.launch, state: "ok", message: null };
      this.host.patchDevice({ launch_check: this.launch, warnings: [] });
    }
    this.broadcast();
  }

  // ---------------------------------------------------------------- launch check
  runLaunchCheck() {
    const dev = this.host.device();
    if (!dev.connected || !dev.calibration.loaded || dev.source === "replay") {
      this.launch = null;
      return;
    }
    this.launch = { state: "running", dv_px: null, matches: 0, message: null, when: nowIso() };
    this.host.patchDevice({ launch_check: this.launch });
    const drift = this.host.drift;
    setTimeout(() => {
      const d = this.host.device();
      if (!d.connected) return;
      this.launch = drift
        ? {
            state: "drift",
            dv_px: 1.38,
            matches: 287,
            message: "Left/right height mismatch 1.4 px: the calibration may have drifted (a knock or a temperature change). Run the calibration check.",
            when: nowIso(),
          }
        : { state: "ok", dv_px: 0.17, matches: 412, message: "Left/right height mismatch 0.17 px: the calibration is valid.", when: nowIso() };
      const warnings = drift
        ? [
            "The cameras may have moved since the calibration: left/right height mismatch 1.4 px (want under 0.6 px: the start-up check looks at three pictures only, so its limit is wider than the calibration check's). Run the calibration check in Setup.",
          ]
        : [];
      this.host.patchDevice({ launch_check: this.launch, warnings });
      if (drift) this.host.log("warn", warnings[0]);
      this.broadcast();
    }, this.host.fast ? 300 : 1600);
  }
}
