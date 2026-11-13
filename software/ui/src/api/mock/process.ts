/**
 * The demo engine's processing studio (APP_API.md "M3 phase 2"). Per scan: the three stages
 * (track, straighten, fuse), the base mesh they end in, the edit list with its cursor (undo /
 * redo: the last steps are kept in memory, older ones are replayed), the current mesh served
 * as the scan's mesh.ply / .stl / .obj, its stats and hole outlines, and the camera trajectory
 * with its loops. Plus the recipes (built-ins and saved ones), and "Make mesh", which now runs
 * the Default recipe. And the M5 fitted shapes (fit.ts) on the current mesh.
 *
 * Jobs do the real (mock) work in small steps (WorkJobSpec), so progress is honest and the page
 * stays responsive even at a million triangles.
 */
import type {
  EditEntry,
  FitResult,
  FuseStage,
  Job,
  MeshOp,
  ProcessQuality,
  ProcessState,
  Project,
  Recipe,
  RecipeOp,
  RecipeScope,
  RecipeSettings,
  RefineStage,
  SpacetimeStage,
  Scan,
  StageId,
  StraightenStage,
  TrackStage,
} from "../types";
import { encodeFaces } from "../../lib/fits";
import { normalizeQuality } from "../../lib/quality";
import { mockFit } from "./fit";
import { stlMesh } from "./geom";
import { HttpError, json, nowIso, uid, type MockHost, type WorkStep } from "./host";
import {
  analyse,
  decimate,
  decodeFaces,
  deleteFaces,
  encodeObj,
  encodePly,
  fillHoles,
  meshBytes,
  removeFloaters,
  runSync,
  smooth,
  type TriMesh,
  type Work,
} from "./meshops";
import { mockTrajectory, scanMesh } from "./scanmesh";

type Stages = { track: TrackStage; straighten: StraightenStage; fuse: FuseStage; spacetime: SpacetimeStage; refine: RefineStage };

type Studio = {
  pid: string;
  sid: string;
  stages: Stages;
  base: { mesh: TriMesh; stage: StageId } | null;
  /** How the base was fused: voxel, quality, and whether the poses are straightened. */
  voxel: number;
  quality: ProcessQuality;
  straightened: boolean;
  edits: EditEntry[];
  cursor: number;
  /** Meshes after n edits (LRU); n = 0 is the base. */
  snaps: Map<number, TriMesh>;
  /** The faces of delete / keep edits, for replays. */
  faces: Map<string, Uint32Array>;
  current: TriMesh | null;
  version: number;
  busy: string | null;
  jobId: string | null;
  files: { version: number; ply?: ArrayBuffer; stl?: ArrayBuffer; obj?: ArrayBuffer };
};

type Op = { op: MeshOp; params: Record<string, unknown> };
type Commit = () => Record<string, unknown> | null;
type JobWork = Generator<WorkStep, Commit, void>;

const SNAP_BUDGET = 200 * 1024 * 1024; // bytes of meshes kept for instant undo / redo

const BUILTINS: Recipe[] = [
  {
    id: "default",
    name: "Default",
    description: "What “Make mesh” runs: standard quality, straightened, loose bits removed and small holes filled.",
    builtin: true,
    settings: { quality: "standard", straighten: "auto", voxel_mm: null },
    ops: [
      { op: "remove_floaters", params: { min_size_mm: 10 } },
      { op: "fill_holes", params: { max_hole_mm: 10 } },
    ],
  },
  {
    id: "body-panel",
    name: "Body panel",
    description: "For large curved panels: smoother, keeps only the main piece and fills larger holes.",
    builtin: true,
    settings: { quality: "standard", straighten: "auto", voxel_mm: 1 },
    ops: [
      { op: "remove_floaters", params: { keep_largest_only: true } },
      { op: "fill_holes", params: { max_hole_mm: 40 } },
      { op: "smooth", params: { method: "taubin", strength: 0.6, keep_edges: false } },
    ],
  },
  {
    id: "bracket",
    name: "Bracket / mechanical",
    description: "Keeps sharp edges and flat faces: light, edge-aware smoothing, only small holes filled.",
    builtin: true,
    settings: { quality: "standard", straighten: "auto", voxel_mm: 1 },
    ops: [
      { op: "remove_floaters", params: { min_size_mm: 5 } },
      { op: "fill_holes", params: { max_hole_mm: 8 } },
      { op: "smooth", params: { method: "bilateral", strength: 0.3, keep_edges: true } },
    ],
  },
  {
    id: "quick-preview",
    name: "Quick preview",
    description: "Fast and light: preview quality, no straightening, simplified to half.",
    builtin: true,
    settings: { quality: "preview", straighten: "off", voxel_mm: 2 },
    ops: [
      { op: "remove_floaters", params: { keep_largest_only: true } },
      { op: "decimate", params: { ratio: 0.5 } },
    ],
  },
];

const OPS: MeshOp[] = ["remove_floaters", "fill_holes", "smooth", "decimate", "delete_faces", "keep_faces"];
const BUSY: Record<MeshOp, string> = {
  remove_floaters: "Removing floaters…",
  fill_holes: "Filling holes…",
  smooth: "Smoothing…",
  decimate: "Simplifying…",
  delete_faces: "Deleting the selection…",
  keep_faces: "Cropping to the selection…",
};
const doing = (op: MeshOp) => BUSY[op].replace("…", "");

const int = (n: number) => Math.round(n).toLocaleString("en-US");
const num = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const mm = (v: number) => (v < 10 ? String(Math.round(v * 10) / 10) : String(Math.round(v)));

function seedOf(sid: string): number {
  let h = 2166136261;
  for (let i = 0; i < sid.length; i++) h = Math.imul(h ^ sid.charCodeAt(i), 16777619);
  return (h >>> 0) % 997;
}

const noiseFor = (q: ProcessQuality, straightened: boolean) => (q === "preview" ? (straightened ? 0.14 : 0.2) : straightened ? 0.09 : 0.14);
const voxelFor = (q: ProcessQuality) => (q === "preview" ? 2 : 1);
/** Mock job length (s): Best processes every frame, about twice the wait of Standard. */
const secondsFor = (q: ProcessQuality) => (q === "preview" ? 4.5 : q === "best" ? 18 : 9);

/** The step the engine would use: Standard processes 30 frames a second of scanning, the others every frame. */
export function mockStep(s: Pick<Scan, "n_frames" | "duration_s">, q: ProcessQuality): number {
  const fps = s.duration_s > 0 ? s.n_frames / s.duration_s : 30;
  return q === "standard" ? Math.max(1, Math.round(fps / 30)) : 1;
}

function label(op: MeshOp, p: Record<string, unknown>, faces?: number): string {
  switch (op) {
    case "remove_floaters":
      if (p.keep_largest_only) return "Keep the largest piece";
      if (num(p.min_size_mm) !== undefined) return `Remove floaters smaller than ${mm(num(p.min_size_mm)!)} mm`;
      return `Remove floaters under ${int(num(p.min_triangles) ?? 0)} triangles`;
    case "fill_holes":
      return p.watertight === true
        ? `Make watertight: close every opening up to ${mm(num(p.max_hole_mm) ?? 0)} mm`
        : `Fill holes up to ${mm(num(p.max_hole_mm) ?? 0)} mm`;
    case "smooth": {
      const pct = `${Math.round((num(p.strength) ?? 0.5) * 100)} %${p.within_sigma === true ? ", within 2σ" : ""}`;
      return p.method === "bilateral" ? `Smooth, keep edges (${pct})` : `Smooth (Taubin, ${pct})`;
    }
    case "decimate": {
      const e = num(p.max_error_mm);
      const base =
        num(p.ratio) !== undefined
          ? `Simplify to ${Math.round(num(p.ratio)! * 100)} %`
          : num(p.target_triangles) !== undefined
            ? `Simplify to ${int(num(p.target_triangles)!)} triangles`
            : "";
      return e === undefined ? base : base ? `${base} (within ${e} mm)` : `Simplify to within ${e} mm`;
    }
    case "delete_faces":
      return `Delete ${int(faces ?? 0)} faces`;
    case "keep_faces":
      return `Keep ${int(faces ?? 0)} faces`;
  }
}

/** Steps that only take time (paced by the job's clock). */
function* tick(a: number, b: number, n: number, message: (k: number) => string): Generator<WorkStep, void, void> {
  for (let k = 0; k <= n; k++) yield { p: a + ((b - a) * k) / n, message: message(k) };
}

/** Real work, its progress mapped into [a, b]. */
function* run<T>(g: Work<T>, a: number, b: number, message: string): Generator<WorkStep, T, void> {
  for (;;) {
    const r = g.next();
    if (r.done) return r.value;
    yield { p: a + (b - a) * r.value, message };
  }
}

const freshStages = (): Stages => ({
  track: { state: "none", summary: null, when: null, job_id: null, quality: null, tracked_pct: null },
  straighten: { state: "none", summary: null, when: null, job_id: null, loops: null, correction_mm: null, correction_deg: null, crossings: null },
  fuse: { state: "none", summary: null, when: null, job_id: null, voxel_mm: null },
  spacetime: { state: "none", summary: null, when: null, job_id: null, source: null, frames: null, patches: null, kept: null, covered_pct: null, moved_mm: null, device: null },
  refine: {
    state: "none", summary: null, when: null, job_id: null, source: null, points: null, outliers: null,
    moved_pct: null, moved_mm: null, residual_before_mm: null, residual_after_mm: null,
  },
});

function patchStage(st: Studio, id: StageId, p: { state?: TrackStage["state"]; summary?: string | null; when?: string | null; job_id?: string | null }) {
  if (id === "track") st.stages.track = { ...st.stages.track, ...p };
  else if (id === "straighten") st.stages.straighten = { ...st.stages.straighten, ...p };
  else if (id === "refine") st.stages.refine = { ...st.stages.refine, ...p };
  else if (id === "spacetime") st.stages.spacetime = { ...st.stages.spacetime, ...p };
  else st.stages.fuse = { ...st.stages.fuse, ...p };
}

export type ScanLookup = (pid: string, sid: string) => { project: Project; scan: Scan } | null;

export class MockProcess {
  private host: MockHost;
  private find: ScanLookup;
  private studios = new Map<string, Studio>();
  private recipes: Recipe[] = BUILTINS.map((r) => ({ ...r }));

  constructor(host: MockHost, find: ScanLookup) {
    this.host = host;
    this.find = find;
  }

  // ---------------------------------------------------------------- routes
  /** `/api/projects/{pid}/scans/{sid}/process[/...]`; `rest` is what follows "process". */
  route(method: string, p: Project, s: Scan, rest: string[], body: Record<string, unknown>): Response | undefined {
    const [a] = rest;
    const is = (m: string, n: number) => method === m && rest.length === n;
    if (is("GET", 0)) return json(200, this.state(this.ensure(p, s)));
    if (a === "track" && is("POST", 1)) {
      const q = normalizeQuality(body.quality ?? "standard");
      if (!q) this.bad(`Unknown quality “${String(body.quality)}”`, "Use standard, best or preview.");
      return json(200, this.startTrack(p, s, q));
    }
    if (a === "straighten" && is("POST", 1)) return json(200, this.startStraighten(p, s));
    if (a === "refine" && is("POST", 1)) return json(200, this.startRefine(p, s));
    if (a === "spacetime" && is("POST", 1)) return json(200, this.startSpacetime(p, s));
    if (a === "fuse" && is("POST", 1)) return json(200, this.startFuse(p, s, body.voxel_mm));
    if (a === "edits" && is("POST", 1)) return json(200, this.startEdit(p, s, body));
    if (a === "undo" && is("POST", 1)) return json(200, this.step(p, s, -1));
    if (a === "redo" && is("POST", 1)) return json(200, this.step(p, s, 1));
    if (a === "recipe" && is("POST", 1)) return json(200, this.startRecipe(p, s, body));
    if (a === "holes.json" && is("GET", 1)) {
      const st = this.ensure(p, s);
      if (!st.current) throw new HttpError(404, "No mesh yet, so no holes", "Run Track first.");
      return json(200, analyse(st.current).holes);
    }
    if (a === "trajectory.json" && is("GET", 1)) {
      const st = this.ensure(p, s);
      if (st.stages.track.state !== "done") throw new HttpError(404, "No trajectory yet", "Run Track first: it follows the scanner through the recording.");
      const t = mockTrajectory(s.duration_s, seedOf(s.id));
      return json(200, { raw: t.raw, straightened: st.straightened ? t.straightened : null, loops: st.stages.straighten.state === "done" ? t.loops : [] });
    }
    return undefined;
  }

  /** `/api/recipes[/{id}]`. */
  routeRecipes(method: string, seg: string[], body: Record<string, unknown>): Response | undefined {
    const [a, b] = seg;
    if (a !== "recipes") return undefined;
    if (method === "GET" && seg.length === 1) return json(200, this.recipes);
    if (method === "POST" && seg.length === 1) return json(200, this.createRecipe(body));
    if (method === "DELETE" && seg.length === 2) {
      const r = this.recipes.find((x) => x.id === b);
      if (!r) throw new HttpError(404, "Recipe not found", "It may have been deleted already.");
      if (r.builtin) throw new HttpError(409, `“${r.name}” is built in and can't be deleted`, "Save a copy under another name to change it.");
      this.recipes = this.recipes.filter((x) => x.id !== b);
      return json(204, null);
    }
    return undefined;
  }

  /** M1 "Make mesh": the Default recipe at the requested quality (job kind "process", as before). */
  makeMesh(p: Project, s: Scan, quality: ProcessQuality): Job {
    const st = this.ensure(p, s);
    this.guard(st, s, "This scan is already being processed");
    const recipe = this.recipes.find((r) => r.id === "default") ?? BUILTINS[0];
    const prev = s.status;
    s.status = "processing";
    p.modified = nowIso();
    return this.allJob(st, p, s, "process", { ...recipe.settings, quality }, recipe.ops, "Making the mesh…", prev, secondsFor(quality), () => ({
      project_id: p.id,
      scan_id: s.id,
      quality,
      mesh: this.meshUrl(st),
    }));
  }

  /** M5 `POST .../fit`: a plane, cylinder or hole at the clicked face of the current mesh (read-only, it
   * does not wait for a running edit: it uses the current version and says which; 409 when the click's
   * `version` is no longer current). */
  fit(p: Project, s: Scan, body: Record<string, unknown>): FitResult {
    const st = this.ensure(p, s);
    const m = st.current;
    if (!m) throw new HttpError(409, "This scan has no mesh yet, so nothing to fit", "Make the mesh first.");
    const v = body.version;
    if (v !== undefined && v !== null && !(typeof v === "number" && Number.isInteger(v))) this.bad("version: must be a whole number", "Send the current.version the click was made on, or leave it out.");
    if (typeof v === "number" && v !== st.version) throw new HttpError(409, "The mesh changed since this click", "Reload the mesh and click again.");
    const r = mockFit(m, body, () => {
      const a = analyse(m);
      return a.loops.filter((l, i) => i !== a.border && l.closed).map((l) => l.verts);
    });
    if (!r.ok) throw new HttpError(422, r.detail, r.hint);
    return {
      kind: r.kind,
      shape: r.shape,
      faces: encodeFaces(r.faces),
      n_faces: r.faces.length,
      tolerance_mm: r.tolerance,
      mean_sigma_mm: null, // the demo meshes have no per-vertex sigma
      warnings: r.warnings,
      version: st.version,
    };
  }

  /** The current mesh as a file (mesh.ply / mesh.stl / mesh.obj), or null when there is none. */
  meshFile(p: Project, s: Scan, name: string): ArrayBuffer | null {
    const st = this.ensure(p, s);
    const m = st.current;
    if (!m) return null;
    if (st.files.version !== st.version) st.files = { version: st.version };
    if (name === "mesh.ply") return (st.files.ply ??= encodePly(m));
    if (name === "mesh.stl") return (st.files.stl ??= stlMesh(m));
    if (name === "mesh.obj") return (st.files.obj ??= encodeObj(m));
    return null;
  }

  // ---------------------------------------------------------------- state
  private meshUrl(st: Studio) {
    return `/api/projects/${encodeURIComponent(st.pid)}/scans/${encodeURIComponent(st.sid)}/files/mesh.ply`;
  }

  private ensure(p: Project, s: Scan): Studio {
    const k = `${p.id}/${s.id}`;
    let st = this.studios.get(k);
    if (st) return st;
    st = {
      pid: p.id,
      sid: s.id,
      stages: freshStages(),
      base: null,
      voxel: 1,
      quality: "standard",
      straightened: false,
      edits: [],
      cursor: 0,
      snaps: new Map(),
      faces: new Map(),
      current: null,
      version: 0,
      busy: null,
      jobId: null,
      files: { version: -1 },
    };
    this.studios.set(k, st);
    if (s.status === "processed") this.seedProcessed(st, s);
    else if (s.status === "failed")
      patchStage(st, "track", { state: "failed", summary: "Tracking failed on 83 % of the frames (low_fitness). Rescan with more overlap, or try markers mode.", when: s.created });
    return st;
  }

  /** A scan meshed before this session: as if "Make mesh" (the Default recipe) had run then. */
  private seedProcessed(st: Studio, s: Scan) {
    const q: ProcessQuality = normalizeQuality((s as Scan & { quality?: ProcessQuality }).quality) ?? "standard";
    const commit = runSync(this.pipeline(st, s, { ...BUILTINS[0].settings, quality: q }, BUILTINS[0].ops, null));
    commit();
    for (const id of ["track", "straighten", "fuse", "spacetime", "refine"] as const) if (st.stages[id].when) patchStage(st, id, { when: s.created });
    for (const e of st.edits) e.created = s.created;
  }

  private state(st: Studio): ProcessState {
    const job = st.jobId ? (this.host.job(st.jobId) ?? null) : null;
    return {
      project_id: st.pid,
      scan_id: st.sid,
      stages: { track: { ...st.stages.track }, straighten: { ...st.stages.straighten }, fuse: { ...st.stages.fuse }, spacetime: { ...st.stages.spacetime }, refine: { ...st.stages.refine } },
      base: st.base ? { triangles: st.base.mesh.indices.length / 3, vertices: st.base.mesh.positions.length / 3, stage: st.base.stage } : null,
      edits: st.edits.map((e) => ({ ...e })),
      cursor: st.cursor,
      can_undo: st.cursor > 0,
      can_redo: st.cursor < st.edits.length,
      current: st.current ? { version: st.version, mesh_url: this.meshUrl(st), stats: analyse(st.current).stats } : null,
      busy: st.busy,
      job: job ? { ...job } : null,
    };
  }

  private broadcast(st: Studio) {
    this.host.send({ type: "process", ...this.state(st) });
  }

  private bad(detail: string, hint: string | null = null): never {
    throw new HttpError(422, detail, hint);
  }

  /** 409 while the scan records or another job of it runs. */
  private guard(st: Studio, s: Scan, running = "Another job of this scan is running") {
    if (s.status === "recording") throw new HttpError(409, "This scan is still recording", "Stop the scan first.");
    if (st.jobId) {
      const j = this.host.job(st.jobId);
      if (j && (j.state === "running" || j.state === "queued")) throw new HttpError(409, running, "Wait for it to finish, or cancel it.");
      st.jobId = null;
      st.busy = null;
    }
  }

  private setCurrent(st: Studio, m: TriMesh | null) {
    st.current = m;
    st.version += 1;
  }

  private remember(st: Studio, n: number, m: TriMesh) {
    st.snaps.delete(n);
    st.snaps.set(n, m);
    let total = 0;
    for (const x of st.snaps.values()) total += meshBytes(x);
    for (const [k, x] of st.snaps) {
      if (total <= SNAP_BUDGET || st.snaps.size <= 2) break;
      if (k === st.cursor || k === n) continue;
      st.snaps.delete(k);
      total -= meshBytes(x);
    }
  }

  /** The mesh after n edits: kept, or replayed from the nearest kept step. */
  private meshAt(st: Studio, n: number): TriMesh {
    if (!st.base) throw new HttpError(409, "No mesh yet", "Run Track first.");
    if (n === 0) return st.base.mesh;
    const hit = st.snaps.get(n);
    if (hit) return hit;
    let k = n - 1;
    while (k > 0 && !st.snaps.has(k)) k--;
    let m = k === 0 ? st.base.mesh : st.snaps.get(k)!;
    for (let i = k; i < n; i++) {
      const e = st.edits[i];
      m = runSync(this.applyOp(m, e, st.faces.get(e.id)));
      this.remember(st, i + 1, m);
    }
    return m;
  }

  // ---------------------------------------------------------------- edits
  private applyOp(m: TriMesh, e: Op, faces?: Uint32Array): Work<TriMesh> {
    const p = e.params;
    switch (e.op) {
      case "remove_floaters":
        return removeFloaters(m, { min_size_mm: num(p.min_size_mm), min_triangles: num(p.min_triangles), keep_largest_only: p.keep_largest_only === true });
      case "fill_holes":
        return fillHoles(m, num(p.max_hole_mm) ?? 10);
      case "smooth":
        return smooth(m, p.method === "bilateral" ? "bilateral" : "taubin", Math.max(0, Math.min(1, num(p.strength) ?? 0.5)), p.keep_edges !== false);
      case "decimate": {
        const F = m.indices.length / 3;
        // the mock has no tolerance: a looser one keeps fewer triangles (0.02 mm about half)
        const e = num(p.max_error_mm);
        const byTol = e !== undefined ? Math.round(F * Math.max(0.1, Math.min(0.9, 0.6 - 3 * e))) : F;
        const target = num(p.ratio) !== undefined ? Math.round(F * num(p.ratio)!) : (num(p.target_triangles) ?? byTol);
        return decimate(m, Math.max(4, Math.min(target, byTol)));
      }
      case "delete_faces":
      case "keep_faces": {
        const list = faces ?? decodeFaces(String(p.faces ?? "")) ?? new Uint32Array();
        const keep = e.op === "keep_faces";
        return (function* () {
          yield 0.5;
          return deleteFaces(m, list, keep);
        })();
      }
    }
  }

  /** Check an edit's params; 422 with the plain reason when they can't work. Returns the decoded faces. */
  private validate(st: Studio, op: MeshOp, p: Record<string, unknown>): Uint32Array | undefined {
    const F = st.current ? st.current.indices.length / 3 : 0;
    switch (op) {
      case "remove_floaters": {
        const a = num(p.min_size_mm),
          b = num(p.min_triangles);
        if (p.keep_largest_only !== true && a === undefined && b === undefined)
          this.bad("Say which floaters to remove", "Give min_size_mm, min_triangles or keep_largest_only.");
        if ((a !== undefined && a <= 0) || (b !== undefined && b < 1)) this.bad("The size must be above 0");
        return undefined;
      }
      case "fill_holes": {
        const v = num(p.max_hole_mm);
        if (v === undefined || v <= 0) this.bad("Give the largest hole to fill, in mm around (max_hole_mm above 0)");
        return undefined;
      }
      case "smooth": {
        if (p.method !== "taubin" && p.method !== "bilateral") this.bad(`Unknown smoothing method “${String(p.method)}”`, "Use taubin or bilateral.");
        const v = num(p.strength);
        if (v === undefined || v < 0 || v > 1) this.bad("The strength must be between 0 and 1");
        return undefined;
      }
      case "decimate": {
        const r = num(p.ratio),
          t = num(p.target_triangles),
          e = num(p.max_error_mm);
        if (r === undefined && t === undefined && e === undefined) this.bad("Give a target (ratio or target_triangles) or a tolerance (max_error_mm)");
        if (e !== undefined && !(e > 0)) this.bad("The tolerance must be above 0 mm");
        if (r !== undefined && (r <= 0 || r >= 1)) this.bad("The ratio must be between 0 and 1", "0.5 keeps half the triangles.");
        if (t !== undefined && (t < 4 || t >= F)) this.bad(t >= F ? `The mesh already has ${int(F)} triangles` : "Keep at least a few triangles", "Choose a smaller target.");
        return undefined;
      }
      case "delete_faces":
      case "keep_faces": {
        const faces = decodeFaces(String(p.faces ?? ""));
        if (!faces) return this.bad("The face list isn't valid base64 of 32-bit integers");
        if (!faces.length) this.bad("Select some faces first");
        for (let i = 0; i < faces.length; i++)
          if (faces[i] >= F) this.bad(`Face ${faces[i]} is out of range: the mesh has ${int(F)} triangles`, "The mesh changed since the selection was made: select again.");
        return faces;
      }
    }
  }

  private startEdit(p: Project, s: Scan, body: Record<string, unknown>): Job {
    const st = this.ensure(p, s);
    this.guard(st, s);
    const op = body.op as MeshOp;
    if (!OPS.includes(op)) this.bad(`Unknown edit “${String(body.op)}”`, `Edits: ${OPS.join(", ")}.`);
    const params = body.params && typeof body.params === "object" ? (body.params as Record<string, unknown>) : {};
    const start = st.current;
    if (!start || !st.base) throw new HttpError(409, "Edits need a mesh", "Run Track first (or Make mesh).");
    const faces = this.validate(st, op, params);
    const id = uid("e-");
    const entryLabel = label(op, params, faces?.length);
    const t0 = performance.now();
    const self = this;
    const work = (function* (): JobWork {
      yield { p: 0.02, message: doing(op) };
      const out = yield* run(self.applyOp(start, { op, params }, faces), 0.05, 0.9, doing(op));
      if (out.indices.length === 0) throw new Error("That would leave no triangles, so nothing was changed. Select less, or use Keep instead of Delete.");
      yield* tick(0.9, 1, 3, () => "Writing PLY and STL");
      return () => {
        for (let i = st.cursor; i < st.edits.length; i++) st.faces.delete(st.edits[i].id);
        st.edits = st.edits.slice(0, st.cursor);
        for (const k of [...st.snaps.keys()]) if (k > st.cursor) st.snaps.delete(k);
        if (faces) st.faces.set(id, faces);
        st.edits.push({
          id,
          op,
          // the face list itself stays on the engine (it can be megabytes)
          params: faces ? { faces: `${faces.length} faces` } : { ...params },
          label: entryLabel,
          created: nowIso(),
          triangles_after: out.indices.length / 3,
          ms: Math.round(performance.now() - t0),
        });
        st.cursor = st.edits.length;
        self.remember(st, st.cursor, out);
        self.setCurrent(st, out);
        return { project_id: p.id, scan_id: s.id, version: st.version, triangles: out.indices.length / 3 };
      };
    })();
    const minSeconds = op === "smooth" ? 2 : op === "fill_holes" ? 1.6 : op === "decimate" ? 1.8 : op === "remove_floaters" ? 1.2 : 0.9;
    return this.start(st, s, "mesh_edit", BUSY[op], minSeconds, work, null);
  }

  /** Undo (-1) or redo (+1): instant for the steps kept in memory. */
  private step(p: Project, s: Scan, dir: -1 | 1): ProcessState {
    const st = this.ensure(p, s);
    this.guard(st, s, "A job of this scan is running");
    const to = st.cursor + dir;
    if (to < 0) throw new HttpError(409, "Nothing to undo", "You're at the base mesh.");
    if (to > st.edits.length) throw new HttpError(409, "Nothing to redo", "You're at the latest step.");
    const m = this.meshAt(st, to);
    st.cursor = to;
    if (to > 0) this.remember(st, to, m);
    this.setCurrent(st, m);
    this.broadcast(st);
    return this.state(st);
  }

  // ---------------------------------------------------------------- stages
  /** Tracking (fake frames), then a fused mesh unless a later stage replaces it. */
  private *trackWork(s: Scan, quality: ProcessQuality, withMesh: boolean, a: number, b: number): Generator<WorkStep, { mesh: TriMesh | null; tracked: number }, void> {
    // the frames the engine would process (Standard: 30 a second of scanning), with the job's frame counter
    const n = Math.max(1, Math.ceil(s.n_frames / mockStep(s, quality)));
    const span = b - a;
    const end = withMesh ? 0.55 : 1;
    yield* tick(a, a + span * 0.05, 3, () => "Loading the recording");
    const fail = /fail/i.test(s.name) || s.n_frames < 5;
    for (let k = 0; k <= 40; k++) {
      if (fail && k > 15) throw new Error("Tracking failed on 83 % of the frames (low_fitness). Rescan with more overlap, or try markers mode.");
      const done = Math.max(1, Math.round((n * k) / 40));
      yield {
        p: a + span * (0.05 + ((end - 0.05) * k) / 40),
        message: done < n ? `Tracking: frame ${done} of ${n}` : `Tracking: all ${n} frames done, making the mesh`,
        frames: { stage: "track", done, total: n },
      };
    }
    const tracked = quality !== "preview" ? (s.mode === "markers" ? 95 : 93) : 91;
    if (!withMesh) return { mesh: null, tracked };
    const mesh = yield* run(scanMesh({ voxel: voxelFor(quality), noise: noiseFor(quality, false), seed: seedOf(s.id) }), a + span * 0.55, b, "Fusing depth (TSDF) and extracting the mesh");
    return { mesh, tracked };
  }

  /** Keyframes, revisits, the pose graph; then a fuse at the corrected poses (unless no loops, or a later fuse replaces it). */
  private *straightenWork(
    s: Scan,
    voxel: number,
    quality: ProcessQuality,
    withMesh: boolean,
    a: number,
    b: number,
  ): Generator<WorkStep, { loops: number; mm: number; deg: number; mesh: TriMesh | null }, void> {
    const span = b - a;
    yield* tick(a, a + span * 0.15, 4, () => "Picking keyframes along the trajectory");
    const t = mockTrajectory(s.duration_s, seedOf(s.id));
    yield* tick(a + span * 0.15, a + span * 0.4, 8, (k) => `Checking revisits with ICP (${Math.min(k, 8)} of 8)`);
    yield* tick(a + span * 0.4, a + span * 0.5, 3, () => "Optimising the pose graph");
    const r = { loops: t.loops.length, mm: t.correction_mm, deg: t.correction_deg };
    if (!t.loops.length || !withMesh) {
      yield* tick(a + span * 0.5, b, 2, () => (t.loops.length ? "Poses corrected" : "No loops: nothing to correct"));
      return { ...r, mesh: null };
    }
    const mesh = yield* run(scanMesh({ voxel, noise: noiseFor(quality, true), seed: seedOf(s.id) }), a + span * 0.5, b, "Fusing again at the corrected poses");
    return { ...r, mesh };
  }

  private trackDone(st: Studio, s: Scan, quality: ProcessQuality, pct: number, when: string) {
    const n = Math.max(1, s.n_frames);
    const ok = Math.round((n * pct) / 100);
    st.stages.track = {
      state: "done",
      summary: `Tracked ${int(ok)} of ${int(n)} frames; ${int(n - ok)} were skipped (fast motion or too few features).`,
      when,
      job_id: null,
      quality,
      tracked_pct: pct,
    };
  }

  private straightenDone(st: Studio, r: { loops: number; mm: number; deg: number }, when: string) {
    st.stages.straighten = {
      state: "done",
      summary: r.loops
        ? `Checked ${r.loops + 3} revisits with ICP and kept ${r.loops}; the pose graph spread the correction along the whole scan.`
        : "No loops found: the scan never came back to a place it had already seen, so there was nothing to correct and the mesh is unchanged.",
      when,
      job_id: null,
      loops: r.loops,
      correction_mm: r.loops ? r.mm : 0,
      correction_deg: r.loops ? r.deg : 0,
    };
  }

  private fuseDone(st: Studio, voxel: number, mesh: TriMesh, when: string) {
    st.stages.fuse = { state: "done", summary: `${mm(voxel)} mm voxels: ${int(mesh.indices.length / 3)} triangles.`, when, job_id: null, voxel_mm: voxel };
  }

  /** A new base mesh: the edits start over. */
  private newBase(st: Studio, mesh: TriMesh, stageId: StageId) {
    st.base = { mesh, stage: stageId };
    st.edits = [];
    st.cursor = 0;
    st.snaps.clear();
    st.faces.clear();
    this.setCurrent(st, mesh);
  }

  private startTrack(p: Project, s: Scan, quality: ProcessQuality): Job {
    const st = this.ensure(p, s);
    this.guard(st, s);
    const self = this;
    const work = (function* (): JobWork {
      const r = yield* self.trackWork(s, quality, true, 0, 0.97);
      yield* tick(0.97, 1, 2, () => "Writing PLY and STL");
      return () => {
        st.quality = quality;
        st.voxel = voxelFor(quality);
        st.straightened = false;
        const fresh = freshStages();
        st.stages.straighten = fresh.straighten;
        st.stages.fuse = fresh.fuse;
        st.stages.spacetime = fresh.spacetime;
        st.stages.refine = fresh.refine;
        self.trackDone(st, s, quality, r.tracked, nowIso());
        self.newBase(st, r.mesh!, "track");
        self.markProcessed(p, s, quality);
        return { project_id: p.id, scan_id: s.id, version: st.version };
      };
    })();
    return this.start(st, s, "process_track", "Tracking…", secondsFor(quality), work, "track");
  }

  private startStraighten(p: Project, s: Scan): Job {
    const st = this.ensure(p, s);
    this.guard(st, s);
    if (st.stages.track.state !== "done") throw new HttpError(409, "Straighten needs a finished Track", "Run Track first.");
    const self = this;
    const work = (function* (): JobWork {
      const r = yield* self.straightenWork(s, st.voxel, st.quality, true, 0, 0.97);
      yield* tick(0.97, 1, 2, () => (r.mesh ? "Writing PLY and STL" : "Done"));
      return () => {
        self.straightenDone(st, r, nowIso());
        if (r.mesh) {
          st.straightened = true;
          self.newBase(st, r.mesh, "straighten");
        }
        return { project_id: p.id, scan_id: s.id, version: st.version, loops: r.loops };
      };
    })();
    return this.start(st, s, "process_straighten", "Straightening…", 4, work, "straighten");
  }

  /** The mock's meshes have no laser cloud of their own: refining finds they already agree. */
  private startRefine(p: Project, s: Scan): Job {
    const st = this.ensure(p, s);
    this.guard(st, s);
    if (st.stages.track.state !== "done" || !st.base) throw new HttpError(409, "Refine with lasers needs a finished Track", "Run Track first.");
    const work = (function* (): JobWork {
      yield* tick(0, 0.3, 2, () => "Reading the laser points");
      yield* tick(0.3, 1, 4, () => "Refining: fitting the correction field");
      return () => {
        const points = 18_000 + (seedOf(s.id) % 9000);
        st.stages.refine = {
          ...st.stages.refine, state: "skipped", when: nowIso(), job_id: null, source: st.base!.stage,
          summary: `The mesh already agrees with the laser lines (0.06 mm median difference over ${points.toLocaleString("en-US")} points), so nothing was changed`,
          points, outliers: 41, moved_pct: 0, moved_mm: null, residual_before_mm: 0.06, residual_after_mm: 0.06,
        };
        return { project_id: p.id, scan_id: s.id, version: st.version };
      };
    })();
    return this.start(st, s, "process_refine", "Refining with the laser lines…", 2.5, work, "refine");
  }

  /** The mock has no frames to measure against: the stage reports plausible numbers and keeps the mesh. */
  private startSpacetime(p: Project, s: Scan): Job {
    const st = this.ensure(p, s);
    this.guard(st, s);
    if (st.stages.track.state !== "done" || !st.base) throw new HttpError(409, "Refine from the images needs a finished Track", "Run Track first.");
    const self = this;
    const work = (function* (): JobWork {
      yield* tick(0, 0.15, 2, () => "Reading the frames");
      yield* tick(0.15, 0.95, 5, (f) => `Refining: searching along the normals (${Math.min(3, 1 + Math.floor(f * 3))} of 3)`);
      yield* tick(0.95, 1, 1, () => "Saving the mesh");
      return () => {
        const source = st.base!.stage === "spacetime" ? (st.stages.spacetime.source ?? "track") : st.base!.stage === "refine" ? (st.stages.refine.source ?? "track") : st.base!.stage;
        const frames = 120;
        const covered = 38 + (seedOf(s.id) % 20);
        st.stages.spacetime = {
          ...st.stages.spacetime, state: "done", when: nowIso(), job_id: null, source, frames, patches: 41_000, kept: 16_500,
          covered_pct: covered, moved_mm: 0.11, device: "gpu",
          summary: `Measured ${covered} % of the surface against ${frames} frames (16,500 patches, on the GPU); it moved by up to 0.11 mm (95 %)`,
        };
        st.stages.refine = freshStages().refine;
        self.newBase(st, st.base!.mesh, "spacetime");
        return { project_id: p.id, scan_id: s.id, version: st.version };
      };
    })();
    return this.start(st, s, "process_spacetime", "Refining from the images…", 3, work, "spacetime");
  }

  private startFuse(p: Project, s: Scan, voxelIn: unknown): Job {
    const st = this.ensure(p, s);
    const voxel = num(voxelIn);
    if (voxel === undefined || voxel < 0.25 || voxel > 10) return this.bad("The voxel size must be between 0.25 and 10 mm", "0.5, 1 or 2 mm are the usual sizes.");
    this.guard(st, s);
    if (st.stages.track.state !== "done") throw new HttpError(409, "Fuse needs a finished Track", "Run Track first.");
    const self = this;
    const work = (function* (): JobWork {
      yield* tick(0, 0.05, 2, () => "Loading the poses");
      const mesh = yield* run(scanMesh({ voxel, noise: noiseFor(st.quality, st.straightened), seed: seedOf(s.id) }), 0.05, 0.96, `Fusing at ${mm(voxel)} mm and extracting the mesh`);
      yield* tick(0.96, 1, 2, () => "Writing PLY and STL");
      return () => {
        st.voxel = voxel;
        self.fuseDone(st, voxel, mesh, nowIso());
        self.newBase(st, mesh, "fuse");
        return { project_id: p.id, scan_id: s.id, version: st.version };
      };
    })();
    return this.start(st, s, "process_fuse", "Fusing…", voxel <= 0.5 ? 5 : 3, work, "fuse");
  }

  // ---------------------------------------------------------------- recipes
  private startRecipe(p: Project, s: Scan, body: Record<string, unknown>): Job {
    const st = this.ensure(p, s);
    const recipe = this.recipes.find((x) => x.id === body.recipe_id);
    if (!recipe) throw new HttpError(404, `No recipe “${String(body.recipe_id)}”`, "It may have been deleted: reload the list.");
    const scope = body.scope as RecipeScope;
    if (scope !== "edits" && scope !== "all") this.bad(`Unknown scope “${String(body.scope)}”`, "Use edits or all.");
    this.guard(st, s);
    const busy = `Applying “${recipe.name}”…`;
    if (scope === "all") return this.allJob(st, p, s, "process_recipe", recipe.settings, recipe.ops, busy, null, secondsFor(normalizeQuality(recipe.settings.quality) ?? "standard") + 1, null);
    if (!st.base) throw new HttpError(409, "There's no mesh to edit yet", "Apply “Everything”, or run Track first.");
    const base = st.base.mesh;
    const self = this;
    const work = (function* (): JobWork {
      const r = yield* self.opsWork(base, recipe.ops, 0.02, 0.96);
      yield* tick(0.96, 1, 2, () => "Writing PLY and STL");
      return () => {
        self.commitOps(st, r);
        return { project_id: p.id, scan_id: s.id, version: st.version };
      };
    })();
    return this.start(st, s, "process_recipe", busy, 1 + 0.8 * recipe.ops.length, work, null);
  }

  private *opsWork(start: TriMesh, ops: RecipeOp[], a: number, b: number): Generator<WorkStep, { entries: EditEntry[]; meshes: TriMesh[] }, void> {
    let m = start;
    const entries: EditEntry[] = [];
    const meshes: TriMesh[] = [];
    for (let i = 0; i < ops.length; i++) {
      const o = ops[i];
      const t0 = performance.now();
      const next = yield* run(this.applyOp(m, o), a + ((b - a) * i) / ops.length, a + ((b - a) * (i + 1)) / ops.length, doing(o.op));
      if (!next.indices.length) throw new Error(`“${label(o.op, o.params)}” would leave no triangles, so nothing was changed.`);
      entries.push({ id: uid("e-"), op: o.op, params: { ...o.params }, label: label(o.op, o.params), created: nowIso(), triangles_after: next.indices.length / 3, ms: Math.round(performance.now() - t0) });
      meshes.push(next);
      m = next;
    }
    return { entries, meshes };
  }

  private commitOps(st: Studio, r: { entries: EditEntry[]; meshes: TriMesh[] }) {
    st.edits = r.entries;
    st.faces.clear();
    st.snaps.clear();
    st.cursor = r.entries.length;
    r.meshes.forEach((m, i) => this.remember(st, i + 1, m));
    this.setCurrent(st, r.meshes.length ? r.meshes[r.meshes.length - 1] : st.base!.mesh);
  }

  /**
   * Track → straighten → fuse with a recipe's settings, then its edits. A stage's mesh is only
   * made when no later stage replaces it. `jobId` marks each stage running as it starts.
   */
  private *pipeline(st: Studio, s: Scan, settings: RecipeSettings, ops: RecipeOp[], jobId: string | null): Generator<WorkStep, () => void, void> {
    const q = settings.quality;
    const mark = (id: StageId) => {
      if (!jobId) return;
      patchStage(st, id, { state: "running", job_id: jobId });
      this.broadcast(st);
    };
    const willStraighten = settings.straighten === "auto" && mockTrajectory(s.duration_s, seedOf(s.id)).loops.length > 0;
    let voxel = voxelFor(q);
    const willFuse = settings.voxel_mm !== null && settings.voxel_mm !== voxel;
    mark("track");
    const tr = yield* this.trackWork(s, q, !willStraighten && !willFuse, 0, 0.45);
    const trackWhen = nowIso();
    let mesh = tr.mesh;
    let baseStage: StageId = "track";
    let str: { loops: number; mm: number; deg: number } | null = null;
    if (settings.straighten === "auto") {
      mark("straighten");
      const r = yield* this.straightenWork(s, voxel, q, !willFuse, 0.45, 0.62);
      str = r;
      if (r.mesh) {
        mesh = r.mesh;
        baseStage = "straighten";
      }
    }
    const straightened = !!str && str.loops > 0;
    if (willFuse) {
      mark("fuse");
      voxel = settings.voxel_mm!;
      mesh = yield* run(scanMesh({ voxel, noise: noiseFor(q, straightened), seed: seedOf(s.id) }), 0.62, 0.8, `Fusing at ${mm(voxel)} mm`);
      baseStage = "fuse";
    }
    const base = mesh!;
    const r = yield* this.opsWork(base, ops, 0.8, 0.97);
    yield* tick(0.97, 1, 2, () => "Writing PLY and STL");
    return () => {
      const when = nowIso();
      st.quality = q;
      st.voxel = voxel;
      st.straightened = straightened;
      this.trackDone(st, s, q, tr.tracked, trackWhen);
      if (str) this.straightenDone(st, str, when);
      else st.stages.straighten = { state: "skipped", summary: "Skipped by the recipe: the poses from Track are used as they are.", when, job_id: null, loops: null, correction_mm: null, correction_deg: null };
      if (willFuse) this.fuseDone(st, voxel, base, when);
      else st.stages.fuse = freshStages().fuse;
      st.stages.spacetime = freshStages().spacetime;
      st.base = { mesh: base, stage: baseStage };
      this.commitOps(st, r);
    };
  }

  private allJob(
    st: Studio,
    p: Project,
    s: Scan,
    kind: string,
    settings: RecipeSettings,
    ops: RecipeOp[],
    busy: string,
    prevStatus: Scan["status"] | null,
    minSeconds: number,
    result: (() => Record<string, unknown>) | null,
  ): Job {
    const holder: { id: string | null } = { id: null };
    const self = this;
    const work = (function* (): JobWork {
      const commit = yield* self.pipeline(st, s, settings, ops, holder.id);
      return () => {
        commit();
        self.markProcessed(p, s, settings.quality);
        return result ? result() : { project_id: p.id, scan_id: s.id, version: st.version };
      };
    })();
    const job = this.start(st, s, kind, busy, minSeconds, work, null, prevStatus);
    holder.id = job.id; // before the first tick runs the work
    return job;
  }

  /** The scan has a mesh: its M1 result points at the current files. */
  private markProcessed(p: Project, s: Scan, quality: ProcessQuality) {
    const base = `/api/projects/${encodeURIComponent(p.id)}/scans/${encodeURIComponent(s.id)}/files`;
    s.status = "processed";
    s.result = {
      mesh_url: `${base}/mesh.ply`,
      stl_url: `${base}/mesh.stl`,
      laser_points_url: `${base}/laser_points.ply`,
      report: { mode: s.mode, quality, frames: { processed: s.n_frames, tracked: Math.round(s.n_frames * 0.93) } },
    };
    (s as Scan & { quality?: ProcessQuality }).quality = quality;
    p.modified = nowIso();
  }

  // ---------------------------------------------------------------- jobs
  /** Start a job of this scan: busy, the stage running, and on failure / cancel everything as it was. */
  private start(st: Studio, s: Scan, kind: string, busy: string, minSeconds: number, work: JobWork, stageId: StageId | null, prevStatus: Scan["status"] | null = null): Job {
    const before: Stages = { track: { ...st.stages.track }, straighten: { ...st.stages.straighten }, fuse: { ...st.stages.fuse }, spacetime: { ...st.stages.spacetime }, refine: { ...st.stages.refine } };
    let restore = prevStatus;
    if (!st.current && s.status !== "processing") {
      restore = s.status;
      s.status = "processing";
    }
    const self = this;
    const end = () => {
      st.busy = null;
      st.jobId = null;
    };
    const job = this.host.startWorkJob(kind, {
      minSeconds,
      work: (function* (): JobWork {
        const commit = yield* work;
        return () => {
          const r = commit();
          end();
          self.broadcast(st);
          return r;
        };
      })(),
      onFail: (error) => {
        end();
        st.stages = before;
        const when = nowIso();
        if (stageId) patchStage(st, stageId, { state: "failed", summary: error, when, job_id: null });
        if (!st.current && (kind === "process" || kind === "process_recipe" || stageId === "track")) {
          patchStage(st, "track", { state: "failed", summary: error, when, job_id: null });
          s.status = "failed";
        } else if (restore) s.status = restore;
        this.broadcast(st);
      },
      onCancel: () => {
        end();
        st.stages = before;
        if (restore) s.status = restore;
        this.broadcast(st);
      },
      extra: { project_id: st.pid, scan_id: st.sid },
    });
    st.busy = busy;
    st.jobId = job.id;
    if (stageId) patchStage(st, stageId, { state: "running", job_id: job.id });
    this.broadcast(st);
    return job;
  }

  private createRecipe(body: Record<string, unknown>): Recipe {
    const name = typeof body.name === "string" ? body.name.trim() : "";
    if (!name) this.bad("The recipe needs a name", "e.g. “Shiny brackets”.");
    if (this.recipes.some((r) => r.name.toLowerCase() === name.toLowerCase()))
      throw new HttpError(409, `A recipe called “${name}” already exists`, "Pick another name, or delete the old one first.");
    const description = typeof body.description === "string" ? body.description.trim() : "";
    let settings: RecipeSettings;
    let ops: RecipeOp[];
    const from = body.from_scan as { project_id?: unknown; scan_id?: unknown } | undefined;
    if (from && typeof from === "object") {
      const hit = this.find(String(from.project_id ?? ""), String(from.scan_id ?? ""));
      if (!hit) throw new HttpError(404, "That scan doesn't exist", "Save the recipe from a scan in a project.");
      const st = this.ensure(hit.project, hit.scan);
      settings = {
        quality: normalizeQuality(st.stages.track.quality) ?? "standard",
        straighten: st.stages.straighten.state === "skipped" ? "off" : "auto",
        voxel_mm: st.stages.fuse.state === "done" ? st.stages.fuse.voxel_mm : null,
      };
      ops = st.edits
        .slice(0, st.cursor)
        .filter((e) => e.op !== "delete_faces" && e.op !== "keep_faces")
        .map((e) => ({ op: e.op as RecipeOp["op"], params: { ...e.params } }));
    } else {
      const set = body.settings as Partial<RecipeSettings> | undefined;
      const quality = normalizeQuality(set?.quality);
      if (!quality) return this.bad("Give the recipe's settings (quality standard, best or preview)");
      settings = { quality, straighten: set?.straighten === "off" ? "off" : "auto", voxel_mm: typeof set?.voxel_mm === "number" ? set.voxel_mm : null };
      if (!Array.isArray(body.ops)) return this.bad("Give the recipe's ops (a list)");
      ops = (body.ops as RecipeOp[]).map((o) => {
        if (!OPS.includes(o.op) || (o.op as MeshOp) === "delete_faces" || (o.op as MeshOp) === "keep_faces")
          this.bad(`A recipe can't hold “${String(o.op)}”`, "Selections only fit one scan.");
        return { op: o.op, params: { ...(o.params ?? {}) } };
      });
    }
    const r: Recipe = { id: uid("r-"), name, description, builtin: false, settings, ops };
    this.recipes.push(r);
    return r;
  }
}
