/**
 * The demo backend's disk use (R1, `GET /api/projects/{pid}/disk`) and what a scan keeps (R2,
 * `GET/POST .../keep`). Sizes are made up from each scan's frame count (about 1.2 MB a frame, as
 * VALIDATION.md measured for simulated frames); the rules and the refusals follow `ohs/server/keep.py`
 * so the project page and its dialog can be tried with `?mock=1`.
 */
import type {
  KeepKept,
  KeepLoss,
  KeepMode,
  KeepPart,
  Project,
  ProjectDiskUse,
  ProjectKeepPlan,
  ProjectKeepResult,
  Scan,
  ScanDiskUse,
  ScanKeepPlan,
} from "../types";
import { HttpError, nowIso } from "./host.ts";

const MB = 1024 * 1024;
const RANK: Record<KeepMode, number> = { full: 0, engineering: 1, standard: 2 };
const KEPT_REC = 48 * 1024; // calibration.json and meta.json
const STATE_SHRINK = 2048; // what the processing record loses with the history

type Parts = { recording: number; frames: number; n_frame_files: number; stages: Record<string, number>; undo: number; result: number; other: number };
type Refusal = { detail: string; hint: string };

export class MockKeep {
  private disk = new Map<string, Parts>();

  private key(p: Project, s: Scan) {
    return `${p.id}/${s.id}`;
  }

  private partsOf(p: Project, s: Scan): Parts {
    const k = this.key(p, s);
    let d = this.disk.get(k);
    if (!d) {
      const recorded = s.status !== "recording" && !s.merged_from?.length;
      const frames = recorded ? Math.round(s.n_frames * 1.2 * MB) : 0;
      const made = s.status === "processed";
      d = {
        recording: frames ? frames + 3 * MB + KEPT_REC : 0,
        frames,
        n_frame_files: recorded ? s.n_frames : 0,
        stages: made ? { track: 21 * MB + 7, straighten: 23 * MB + 11 } : {},
        undo: made ? 34 * MB + 5 : 0,
        result: made ? 63 * MB + 3 : 0,
        other: made ? Math.round(0.6 * MB) : 0,
      };
      this.disk.set(k, d);
    }
    return d;
  }

  /** The stages and an undo step are on disk again (a mesh was made, or a stage ran). */
  remade(p: Project, s: Scan) {
    const d = this.partsOf(p, s);
    d.stages = { track: 21 * MB + 7, straighten: 23 * MB + 11 };
    d.undo = Math.max(d.undo, 21 * MB);
    if (!d.result) d.result = 63 * MB + 3;
  }

  /** Why an action that needs what was removed cannot run (null when it can). */
  refusal(p: Project, s: Scan, what: "remake" | "stage"): HttpError | null {
    const d = this.partsOf(p, s);
    if (s.keep === "standard" && what === "remake")
      return new HttpError(
        409,
        "this scan's recording was removed to free space (it keeps its result only), so its mesh cannot be made again from the frames",
        "the mesh can still be edited, measured and exported; to make it again, scan the part again",
      );
    if (what === "stage" && (s.keep === "engineering" || s.keep === "standard") && !Object.keys(d.stages).length)
      return new HttpError(
        409,
        `the stage files this needs were removed to free space (this scan keeps ${s.keep === "standard" ? "its result only" : "its recording and its result"})`,
        s.keep === "standard"
          ? "the mesh can still be edited, measured and exported; the stage files can only be made again from a recording, which this scan no longer has"
          : "make the mesh again (Make mesh, or Track first) to rebuild them from the recording",
      );
    return null;
  }

  scanDisk(p: Project, s: Scan): ScanDiskUse {
    const d = this.partsOf(p, s);
    const stages_bytes = Object.values(d.stages).reduce((a, b) => a + b, 0);
    return {
      id: s.id,
      name: s.name,
      n_frames: s.n_frames,
      duration_s: s.duration_s,
      bytes: d.recording + stages_bytes + d.undo + d.result + d.other,
      recording: d.recording,
      frames: d.frames,
      n_frame_files: d.n_frame_files,
      stages: { ...d.stages },
      stages_bytes,
      undo: d.undo,
      result: d.result,
      other: d.other,
    };
  }

  projectDisk(p: Project): ProjectDiskUse {
    const scans = p.scans.map((s) => this.scanDisk(p, s));
    const sum = (f: (x: ScanDiskUse) => number) => scans.reduce((a, x) => a + f(x), 0);
    const other = 2048;
    return {
      project_id: p.id,
      bytes: sum((x) => x.bytes) + other,
      scans,
      parts: { recording: sum((x) => x.recording), stages: sum((x) => x.stages_bytes), undo: sum((x) => x.undo), result: sum((x) => x.result) },
      references: 0,
      other,
      trash: { bytes: 0, projects: 0 },
      seconds: 0.004,
    };
  }

  plan(p: Project, s: Scan, mode: KeepMode): ScanKeepPlan {
    const d = this.partsOf(p, s);
    const cur: KeepMode = s.keep ?? "full";
    const hasRec = d.frames > 0;
    const hasMesh = s.status === "processed" && !!s.result;
    const remove: KeepPart[] = [];
    if (mode === "standard" && d.recording > KEPT_REC) remove.push({ part: "recording", bytes: d.recording - KEPT_REC, files: d.n_frame_files + 4 });
    if (mode !== "full" && d.undo > 0) remove.push({ part: "undo", bytes: d.undo, files: 3 }); // result/mesh.ply serves as the base
    const stages = Object.values(d.stages).reduce((a, b) => a + b, 0);
    if (mode !== "full" && stages > 0) remove.push({ part: "stages", bytes: stages, files: 5 * Object.keys(d.stages).length });
    // the processing record is written again without the history (as the engine: frees = removed - written)
    const writes: ScanKeepPlan["writes"] = mode !== "full" && d.undo > 0 ? [{ what: "state", bytes: -STATE_SHRINK }] : [];
    const frees = remove.reduce((a, r) => a + r.bytes, 0) - writes.reduce((a, w) => a + w.bytes, 0);
    let refusal: Refusal | null = null;
    if (s.status === "processing" || s.status === "recording")
      refusal = { detail: `this scan is busy: it is being ${s.status === "recording" ? "recorded" : "processed"}`, hint: "wait until it has finished, then free its space" };
    else if (mode === "full")
      refusal =
        cur === "full"
          ? { detail: "this scan keeps everything already", hint: "choose what to remove: keep the recording and the result, or the result only" }
          : { detail: "what was removed to free space cannot be brought back from here", hint: "the stage files come back when you make the mesh again" };
    else if (RANK[mode] < RANK[cur])
      refusal = { detail: "this scan cannot keep its recording again: it was removed to free space", hint: "it keeps its result only; it can still be edited, measured and exported" };
    else if (mode === "engineering" && !hasRec)
      refusal = {
        detail: `this scan has no recording ${s.merged_from?.length ? "of its own (it was merged from other scans)" : "on disk"}, so it cannot keep one`,
        hint: "choose the result only to free its processing files",
      };
    else if (mode === "standard" && !hasMesh)
      refusal = { detail: "this scan has no mesh yet: keeping its result only would leave nothing of it", hint: "make its mesh first, or keep the recording and the result" };
    else if (frees <= 0) refusal = { detail: "nothing to free: this scan would not take less space afterwards", hint: "it has none of what this choice removes on disk" };
    const loses: KeepLoss[] = [];
    if (d.undo > 0) loses.push("undo");
    if (mode === "engineering" && stages) loses.push("stages");
    if (mode === "standard") {
      if (hasRec) loses.push("remake", "merge", "frames");
      if (hasRec || stages) loses.push("refine");
    }
    const keeps: KeepKept[] = [];
    if (hasRec && mode !== "standard") keeps.push("recording");
    if (hasMesh) keeps.push("result", "laser_points", "trajectory", "report");
    if (d.recording > 0) keeps.push("calibration");
    return {
      scan_id: s.id,
      name: s.name,
      keep: cur,
      to: mode,
      ok: !refusal,
      refusal,
      bytes: this.scanDisk(p, s).bytes,
      frees_bytes: refusal ? 0 : frees,
      remove: refusal ? [] : remove,
      writes: refusal ? [] : writes,
      kept_step_bytes: 0,
      loses,
      keeps,
      confirm: mode === "standard" && remove.some((r) => r.part === "recording"),
    };
  }

  apply(p: Project, s: Scan, mode: KeepMode, confirm: boolean): { scan: Scan; freed_bytes: number; planned_bytes: number } {
    const plan = this.plan(p, s, mode);
    if (plan.refusal) throw new HttpError(409, plan.refusal.detail, plan.refusal.hint);
    if (plan.confirm && !confirm) throw new HttpError(409, "removing this scan's recording cannot be undone", "ask once more, then send confirm: true");
    const d = this.partsOf(p, s);
    if (mode === "standard") {
      d.recording = Math.min(d.recording, KEPT_REC);
      d.frames = 0;
      d.n_frame_files = 0;
    }
    if (d.undo > 0) d.other -= STATE_SHRINK;
    d.undo = 0;
    d.stages = {};
    s.keep = mode;
    s.freed = [...(s.freed ?? []), { when: nowIso(), keep: mode, bytes: plan.frees_bytes }];
    s.has_recording = d.frames > 0;
    p.modified = nowIso();
    return { scan: s, freed_bytes: plan.frees_bytes, planned_bytes: plan.frees_bytes };
  }

  projectPlan(p: Project, mode: KeepMode): ProjectKeepPlan {
    const scans = p.scans.map((s) => this.plan(p, s, mode));
    const ok = scans.filter((x) => x.ok);
    return {
      project_id: p.id,
      to: mode,
      scans,
      frees_bytes: ok.reduce((a, x) => a + x.frees_bytes, 0),
      n_scans: ok.filter((x) => x.frees_bytes > 0).length,
      confirm: ok.some((x) => x.confirm),
    };
  }

  projectApply(p: Project, mode: KeepMode, confirm: boolean): ProjectKeepResult {
    const plan = this.projectPlan(p, mode);
    if (plan.confirm && !confirm) throw new HttpError(409, "removing these scans' recordings cannot be undone", "ask once more, then send confirm: true");
    const results: ProjectKeepResult["results"] = [];
    let freed = 0;
    for (const x of plan.scans) {
      const s = p.scans.find((q) => q.id === x.scan_id)!;
      if (!x.ok || (x.frees_bytes === 0 && x.keep === mode)) {
        results.push({ scan_id: x.scan_id, name: x.name, ok: false, freed_bytes: 0, error: x.refusal ?? { detail: "nothing to free", hint: "nothing to do" } });
        continue;
      }
      const r = this.apply(p, s, mode, true);
      freed += r.freed_bytes;
      results.push({ scan_id: x.scan_id, name: x.name, ok: true, freed_bytes: r.freed_bytes });
    }
    return { project_id: p.id, to: mode, results, freed_bytes: freed };
  }
}

/** A mode from a request, or the API's 422. */
export function keepModeArg(v: unknown): KeepMode {
  if (v === "full" || v === "engineering" || v === "standard") return v;
  throw new HttpError(422, `unknown mode ${JSON.stringify(v)}: a scan keeps full, engineering or standard`, 'send mode: "engineering" (the recording and the result) or "standard" (the result only)');
}
