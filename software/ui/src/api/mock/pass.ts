/**
 * A second pass in the demo (FEATURE_PLAN N2, APP_API.md "A second pass"): `continues` at Start, the steps
 * after Stop (the demo's Make mesh, align and merge jobs, one after the other, moved on by the mock's tick),
 * `GET .../pass`, `POST .../pass/run` and `POST .../pass/compare`, with the engine's refusals and words.
 *
 * The demo's meshes are made up and every scan gets the same one, so its before and after is made up too:
 * the merged scan's share is the first scan's plus what the asked areas add, and the asked areas pass (the
 * largest partly). The demo's alignment refuses a pass named "underside" (or "bottom") when the part was
 * moved, as the Align page's demo does.
 */
import type { CheckResult, Job, PassChain, PassCompare, PassLink, PassView, Project, Scan } from "../types";
import { HttpError } from "./host.ts";

export const MERGE_NOTE =
  "A merge adds coverage: it fills in surface the first scan did not measure well enough. It does not yet make the surface the first scan already saw well more accurate: where both scans saw a place, the two are averaged.";
const DOING = { process: "making the pass's mesh", align: "aligning the pass to the first scan", merge: "merging the two" } as const;
type Step = keyof typeof DOING;

export type PassHost = {
  makeMesh(p: Project, s: Scan): Job;
  align(p: Project, body: Record<string, unknown>): Job;
  merge(p: Project, body: Record<string, unknown>): Job;
  check(p: Project, s: Scan, tolerance: number): CheckResult;
  job(jid: string): Job | undefined;
  log(level: string, message: string): void;
};

const ACTIVE = new Set(["processing", "aligning", "merging"]);

export class MockPasses {
  private host: PassHost;
  /** The asked areas' faces on the first mesh (base64), by pass scan id. */
  private faces = new Map<string, string[]>();

  constructor(host: PassHost) {
    this.host = host;
  }

  /** `continues` of `POST /api/scan/start`, checked as the engine checks it; the refusals in its words. */
  link(p: Project, body: unknown): { link: PassLink; faces: string[] } {
    if (!body || typeof body !== "object") throw new HttpError(422, "continues must be an object", "send {scan_id, tolerance_mm, areas}");
    const c = body as Record<string, unknown>;
    const first = p.scans.find((x) => x.id === c.scan_id);
    if (!first) throw new HttpError(404, `no scan ${JSON.stringify(c.scan_id)} in project ${JSON.stringify(p.id)}`, "pick a scan from the project's scan list");
    const tol = c.tolerance_mm;
    if (typeof tol !== "number" || !Number.isFinite(tol) || tol <= 0 || tol > 100)
      throw new HttpError(422, "The tolerance must be a number of mm above 0 (at most 100).", "Give the tolerance the Scan check used, for example 0.3.");
    if (first.status === "recording" || first.status === "processing")
      throw new HttpError(409, `the scan "${first.name}" is busy`, "wait until it has finished, then scan its areas again");
    if (!first.result) throw new HttpError(409, `the scan "${first.name}" has no mesh yet, so there is nothing to join a pass to`, "make its mesh first; then Check shows which areas to scan again");
    const areas = Array.isArray(c.areas) ? (c.areas as unknown[]) : [];
    if (!areas.every((a) => Number.isInteger(a))) throw new HttpError(422, "The areas to scan again that the page sent cannot be read.", "Open the Scan check of the first scan again, then press Scan these areas again.");
    if (new Set(areas).size !== areas.length) throw new HttpError(422, "an area is asked for twice", "send each area once");
    if (c.same_setup !== undefined && typeof c.same_setup !== "boolean") throw new HttpError(422, "same_setup must be true or false", "true when the part was not moved since the first scan");
    const r = this.host.check(p, first, tol);
    if (typeof c.version === "number" && c.version !== r.version)
      throw new HttpError(409, `The mesh of "${first.name}" changed since its Scan check was shown.`, "Run the Scan check again, then press Scan these areas again.");
    const bad = (areas as number[]).find((a) => a < 0 || a >= r.areas.length);
    if (bad !== undefined) throw new HttpError(422, `Area ${bad + 1} is not in the Scan check of "${first.name}" at ± ${tol} mm (${r.areas.length} areas).`, "Run the Scan check again, then press Scan these areas again.");
    const picked = (areas as number[]).map((i) => ({ i, a: r.areas[i] }));
    const poses = (Array.isArray(c.poses) ? (c.poses as PassLink["poses"]) : []).slice(0, 12);
    const noRec = first.merged_from?.length ? null : first.has_recording === false ? `the recording of "${first.name}" was removed to free space, and a merge fuses its frames again` : null;
    const link: PassLink = {
      scan_id: first.id,
      scan_name: first.name,
      tolerance_mm: tol,
      version: r.version,
      areas: picked.map(({ i, a }) => ({ index: i, kind: a.kind, area_mm2: a.area_mm2, center: a.center, n_faces: a.n_faces })),
      poses,
      same_setup: c.same_setup !== false,
      setup_words:
        c.same_setup !== false
          ? "The part was not moved since the first scan: the table helps to align the two, and the merge keeps it."
          : "The part was moved since the first scan: the alignment uses the part alone, and each scan's table is left out of the merge.",
      merge_problem: noRec,
      preset_from_first: false,
      preset: "General",
      chain: { state: "waiting", step: null, jobs: {}, message: "Waiting for Stop: then the pass is made into a mesh, aligned to the first scan and merged with it." },
    };
    return { link, faces: picked.map(({ a }) => a.faces) };
  }

  started(s: Scan, faces: string[]): void {
    this.faces.set(s.id, faces);
  }

  private chainOf(s: Scan): PassLink {
    if (!s.continues) throw new HttpError(409, `"${s.name}" is not a second pass of another scan.`, "Start one from the Scan check of the first scan: Scan these areas again.");
    return s.continues;
  }

  private set(s: Scan, c: Partial<PassChain>): void {
    const link = this.chainOf(s);
    link.chain = { ...link.chain, ...c, jobs: { ...link.chain.jobs, ...(c.jobs ?? {}) } };
  }

  afterStop(p: Project, s: Scan): void {
    if (!s.continues) return;
    if (!s.n_frames) {
      this.set(s, { state: "failed", step: "process", message: "The pass recorded no frames, so there is nothing to join. Scan the areas again." });
      return;
    }
    this.start(p, s, "process");
  }

  private start(p: Project, s: Scan, step: Step, T?: unknown): void {
    const link = this.chainOf(s);
    const first = p.scans.find((x) => x.id === link.scan_id);
    if (!first) {
      this.set(s, { state: "failed", step, message: `The first scan "${link.scan_name}" is no longer in the project. The pass is kept as its own scan.` });
      return;
    }
    if (step === "process") {
      const job = this.host.makeMesh(p, s);
      this.set(s, { state: "processing", step, jobs: { process: job.id }, merged_scan_id: null, align: null, message: "Making the pass's mesh." });
    } else if (step === "align") {
      const job = this.host.align(p, { target: first.id, source: s.id, same_setup: link.same_setup });
      this.set(s, { state: "aligning", step, jobs: { align: job.id }, message: `Aligning the pass to "${first.name}".` });
    } else {
      const job = this.host.merge(p, { target: first.id, source: s.id, T, same_setup: link.same_setup, name: `${first.name} + ${s.name}` });
      this.set(s, { state: "merging", step, jobs: { merge: job.id }, message: `Merging the pass with "${first.name}" into a new scan.` });
    }
  }

  /** Moves every chain on whose job ended (the mock's tick). */
  tick(projects: Iterable<Project>): void {
    for (const p of projects)
      for (const s of p.scans) {
        const c = s.continues?.chain;
        if (!c || !ACTIVE.has(c.state) || !c.step) continue;
        const job = c.jobs[c.step] ? this.host.job(c.jobs[c.step]!) : undefined;
        if (!job || job.state === "running" || job.state === "queued") continue;
        try {
          this.finished(p, s, c.step, job);
        } catch (e) {
          this.set(s, { state: "failed", message: `Stopped after ${DOING[c.step]}: ${e instanceof HttpError ? e.detail : String(e)}. The pass is kept as its own scan.` });
        }
      }
  }

  private finished(p: Project, s: Scan, step: Step, job: Job): void {
    const link = this.chainOf(s);
    if (job.state === "cancelled") {
      this.set(s, { state: "cancelled", message: `Cancelled while ${DOING[step]}. The pass is kept as its own scan; Continue runs the rest.` });
      return;
    }
    if (job.state === "failed") {
      this.set(s, { state: "failed", message: `Failed while ${DOING[step]}: ${job.error ?? "unknown error"}. The pass is kept as its own scan.` });
      this.host.log("error", `Second pass: failed while ${DOING[step]}. The pass is kept as its own scan.`);
      return;
    }
    const r = (job.result ?? {}) as Record<string, unknown>;
    if (step === "process") this.start(p, s, "align");
    else if (step === "align") {
      const align = {
        accepted: r.accepted === true,
        verdict: String(r.verdict ?? ""),
        overlap: (r.overlap as number) ?? null,
        rms_mm: (r.rmse_mm as number) ?? null,
        little_shared: (r.little_shared as boolean | undefined) ?? null,
        caution: (r.caution as string | undefined) ?? null,
      };
      if (!align.accepted) {
        this.set(s, {
          state: "not_aligned",
          align,
          message: `${align.verdict} Nothing was merged: the pass is kept as its own scan. Align it to "${link.scan_name}" on the Align page, where matching points can be picked.`,
        });
        this.host.log("warn", `Second pass not merged: the alignment to "${link.scan_name}" was not accepted. Use the Align page.`);
      } else if (link.merge_problem) {
        this.set(s, { state: "not_merged", align, message: `Aligned, but not merged: ${link.merge_problem}. The pass is kept as its own scan.` });
      } else {
        this.set(s, { align });
        this.start(p, s, "merge", r.T);
      }
    } else {
      const merged = r.scan as Scan | undefined;
      // the merge's own check of how much the two share: said here too, as the engine's chain does
      const caution = typeof r.caution === "string" && r.caution ? ` ${r.caution}` : "";
      this.set(s, { state: "merged", merged_scan_id: String(r.scan_id), message: `Merged with "${link.scan_name}" into "${merged?.name ?? r.scan_id}". "${link.scan_name}" itself is unchanged.${caution}` });
      this.host.log("info", `Second pass merged with "${link.scan_name}"`);
    }
  }

  run(p: Project, s: Scan): PassView {
    const link = this.chainOf(s);
    if (ACTIVE.has(link.chain.state)) throw new HttpError(409, "the pass's steps are running already", "follow them, or cancel the running one");
    if (link.chain.state === "merged") throw new HttpError(409, "the pass is merged already", "open the merged scan, or scan the areas again");
    this.start(p, s, s.result ? "align" : "process");
    return this.view(p, s);
  }

  view(p: Project, s: Scan): PassView {
    const link = this.chainOf(s);
    const first = p.scans.find((x) => x.id === link.scan_id);
    const jid = link.chain.step ? link.chain.jobs[link.chain.step] : undefined;
    const { chain, ...rest } = link;
    return {
      ...rest,
      pass_id: s.id,
      pass_name: s.name,
      chain: { ...chain },
      job: jid ? (this.host.job(jid) ?? null) : null,
      steps: ["process", "align", "merge"],
      note: MERGE_NOTE,
      first: { id: link.scan_id, name: first?.name ?? link.scan_name, mesh_url: first?.result?.mesh_url ?? null, has_mesh: !!first?.result },
      areas_faces: this.faces.get(s.id) ?? null,
      areas_faces_why_not: this.faces.has(s.id) ? null : "what the areas were is not kept with this pass",
    };
  }

  compare(p: Project, s: Scan, body: Record<string, unknown>): PassCompare {
    const link = this.chainOf(s);
    const mid = link.chain.merged_scan_id;
    const merged = mid ? p.scans.find((x) => x.id === mid) : undefined;
    if (link.chain.state !== "merged" || !merged) throw new HttpError(409, "the pass is not merged with the first scan", link.chain.message || "wait for the pass's steps to finish");
    const first = p.scans.find((x) => x.id === link.scan_id);
    if (!first) throw new HttpError(409, "the first scan is no longer in the project", "scan the areas again");
    const tol = typeof body.tolerance_mm === "number" ? body.tolerance_mm : link.tolerance_mm;
    const a = this.host.check(p, first, tol);
    // made up (module docstring): the asked areas pass now, the largest only partly
    const asked = link.areas.reduce((sum, x) => sum + x.area_mm2, 0) / Math.max(1, a.area_mm2);
    const okA = a.share_ok ?? 0;
    const gain = Math.min(asked * 0.85, Math.max(0, 1 - okA));
    const shares = (x: CheckResult, id: string, name: string, extra = 0, fewer = 0) => ({
      id,
      name,
      share_ok: x.share_ok === null ? null : Math.min(1, x.share_ok + extra),
      share_uncertain: x.share_uncertain === null ? null : Math.max(0, x.share_uncertain - extra),
      share_not_measured: x.share_not_measured,
      n_areas: Math.max(0, x.n_areas - fewer),
      area_mm2: x.area_mm2,
      has_sigma: x.has_sigma,
    });
    const biggest = link.areas.reduce((k, x, i) => (x.area_mm2 > link.areas[k].area_mm2 ? i : k), 0);
    return {
      tolerance_mm: tol,
      note: MERGE_NOTE,
      rule: { match_mm: 2.5, match_angle_deg: 60, passes_share: 0.9 },
      first: shares(a, first.id, first.name),
      merged: shares(a, merged.id, merged.name, gain, Math.max(0, link.areas.length - 1)),
      areas: link.areas.map((x, i) => ({ ...x, share_ok: i === biggest ? 0.74 : 0.97, found_share: 1, state: i === biggest ? ("partly" as const) : ("passes" as const) })),
      areas_note: tol === link.tolerance_mm ? null : `The areas were asked for at ± ${link.tolerance_mm} mm; here they are judged at ± ${tol} mm.`,
      // on the surface both hold (made up as above); the part moved: the tables are not in the merge (the demo's
      // table is about four fifths of a scan's surface)
      common: { found_share: link.same_setup ? 0.995 : 0.2, before_ok: okA, after_ok: Math.min(1, okA + gain) },
      missing: link.same_setup
        ? { share: 0.005, area_mm2: 0.005 * a.area_mm2, why: null }
        : { share: 0.8, area_mm2: 0.8 * a.area_mm2, why: "the part was moved, so each scan's table was left out of the merge" },
      whole_note: "Each scan's whole mesh, as its own Check says it: the shares are of different surfaces when the merged scan holds less or more than the first.",
    };
  }
}
