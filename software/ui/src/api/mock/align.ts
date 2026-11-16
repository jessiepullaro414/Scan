/**
 * The demo engine's "align two scans and merge them" (APP_API.md "M4 first slice"): the two routes with
 * the server's rules and its error replies (`ohs/server/aligning.py`), and made-up answers.
 *
 * The demo's scans are all the same scene in the same frame (scanmesh.ts), so the pose that fits is
 * "leave the second scan where it is" (the identity), and nothing is measured:
 *
 * - an alignment from the shape alone of scans whose part was moved (`same_setup: false`, no pairs) is
 *   NOT accepted: the demo part (a ball and a block on a table) "fits in more than one position", and
 *   the pose returned for showing is such a wrong one (the constructor's `wrongPose`);
 * - with picked pairs, or with `same_setup: true` (the shared table pins the rest), it is accepted, with
 *   plausible figures and the engine's sentence;
 * - `pairs_rms_mm` is real in spirit: how far the distances between the picked points differ on the two
 *   scans (a rigid motion keeps them), so a pair that does not match shows as a large value;
 * - a merge counts the frames of both recordings, can be cancelled (nothing is added then) and adds a
 *   new processed scan with `merged_from` to the project. A merged scan has no recording: it can be
 *   aligned to, not merged again.
 *
 * No imports at run time (types only), so `npm test` runs it with Node: the mock engine hands in what it
 * needs (`AlignHost`).
 */
import type { AlignResult, Job, MergeResult, PointPair, ProcessQuality, Project, Scan } from "../types";
import type { WorkJobSpec, WorkStep } from "./host";

export type AlignHost = {
  startWorkJob(kind: string, spec: WorkJobSpec): Job;
  log(level: "info" | "warn" | "error", message: string): void;
  /** Answer the request with the API's error reply (`{detail, hint}`). */
  fail(status: number, detail: string, hint: string): never;
  uid(prefix: string): string;
  now(): string;
};

export const MIN_PAIRS = 3;

/** The engine's reason when the shape alone leaves more than one answer (`ohs.align.pairwise`). */
export const AMBIGUOUS_REASON =
  "the part fits in more than one position about equally well (a symmetric shape, a repeated feature, or a surface that can slide on itself): " +
  "give a rough start by picking matching points, or scan with more of the part's distinct features in both";

const IDENTITY: number[][] = [
  [1, 0, 0, 0],
  [0, 1, 0, 0],
  [0, 0, 1, 0],
  [0, 0, 0, 1],
];

/** A wrong pose to show when none is handed in: half a turn about the z axis. */
const HALF_TURN: number[][] = [
  [-1, 0, 0, 0],
  [0, -1, 0, 0],
  [0, 0, 1, 0],
  [0, 0, 0, 1],
];

/** The engine's sentence for an accepted alignment (`align_pair`). */
export function alignedVerdict(overlap: number, rmse_mm: number, start: "features" | "given", agree: number, candidates: number): string {
  const sure = start === "features" ? `${agree} of ${candidates} starting guesses agree` : "from the start you gave";
  return `Aligned: ${(100 * overlap).toFixed(0)} % of the smaller scan's part lies within 1 mm of the other scan, ${rmse_mm.toFixed(2)} mm RMS; ${sure}.`;
}

type V3 = [number, number, number];

const isPoint = (v: unknown): v is V3 => Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === "number" && Number.isFinite(x));

/** The three eigenvalues of a symmetric 3x3 matrix (a, b, c on the diagonal; d = xy, e = xz, f = yz), largest first. */
function eigenvalues3(a: number, b: number, c: number, d: number, e: number, f: number): V3 {
  const off = d * d + e * e + f * f;
  const q = (a + b + c) / 3;
  const p2 = (a - q) ** 2 + (b - q) ** 2 + (c - q) ** 2 + 2 * off;
  if (p2 < 1e-18) return [q, q, q];
  const p = Math.sqrt(p2 / 6);
  const A = (a - q) / p,
    B = (b - q) / p,
    C = (c - q) / p,
    D = d / p,
    E = e / p,
    F = f / p;
  const det = A * (B * C - F * F) - D * (D * C - F * E) + E * (D * F - B * E);
  const phi = Math.acos(Math.max(-1, Math.min(1, det / 2))) / 3;
  const l1 = q + 2 * p * Math.cos(phi);
  const l3 = q + 2 * p * Math.cos(phi + (2 * Math.PI) / 3);
  return [l1, 3 * q - l1 - l3, l3];
}

/** The engine's test (`pose_from_point_pairs`): points in one line leave the turn about that line open. */
function spansTriangle(pts: V3[]): boolean {
  const n = pts.length;
  const m: V3 = [0, 0, 0];
  for (const p of pts) for (let k = 0; k < 3; k++) m[k] += p[k] / n;
  let xx = 0,
    yy = 0,
    zz = 0,
    xy = 0,
    xz = 0,
    yz = 0;
  for (const p of pts) {
    const x = p[0] - m[0],
      y = p[1] - m[1],
      z = p[2] - m[2];
    xx += x * x;
    yy += y * y;
    zz += z * z;
    xy += x * y;
    xz += x * z;
    yz += y * z;
  }
  const l = eigenvalues3(xx, yy, zz, xy, xz, yz);
  const s0 = Math.sqrt(Math.max(l[0], 0)),
    s1 = Math.sqrt(Math.max(l[1], 0));
  return !(s1 < 0.05 * Math.max(s0, 1e-9) || s0 < 1.0);
}

/**
 * The picked pairs, checked as the engine does: `{ rms }` (how well they agree with each other, mm) or
 * `{ error }` in the engine's words. The demo's `rms` is the RMS difference of the distances between the
 * points on the two scans (the engine's is the RMS distance of the pairs after their rigid fit).
 */
export function checkPairs(pairs: readonly PointPair[]): { rms: number } | { error: string } {
  if (pairs.length < MIN_PAIRS) return { error: `three or more pairs of points are needed (${pairs.length} and ${pairs.length} given)` };
  const src: V3[] = [];
  const dst: V3[] = [];
  for (const p of pairs) {
    if (!p || !isPoint(p.source) || !isPoint(p.target)) return { error: "a picked point is not a number" };
    src.push(p.source);
    dst.push(p.target);
  }
  if (!spansTriangle(src) || !spansTriangle(dst)) return { error: "the picked points lie in one line or on one spot: pick points that span a triangle" };
  const dist = (a: V3, b: V3) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
  let sum = 0,
    n = 0;
  for (let i = 0; i < src.length; i++)
    for (let j = i + 1; j < src.length; j++) {
      const d = dist(src[i], src[j]) - dist(dst[i], dst[j]);
      sum += d * d;
      n++;
    }
  return { rms: Math.sqrt(sum / n) };
}

/** Is `T` a 4x4 matrix of numbers (the engine's first check of a merge)? */
export function isPose(T: unknown): T is number[][] {
  return Array.isArray(T) && T.length === 4 && T.every((r) => Array.isArray(r) && r.length === 4 && r.every((x) => typeof x === "number"));
}

/** The engine's second check: finite, a rotation (R Rᵀ = 1 within 1e-6, det > 0) and a last row of 0 0 0 1. */
export function isRigid(T: number[][]): boolean {
  for (const r of T) for (const x of r) if (!Number.isFinite(x)) return false;
  for (let i = 0; i < 3; i++)
    for (let j = 0; j < 3; j++) {
      const v = T[i][0] * T[j][0] + T[i][1] * T[j][1] + T[i][2] * T[j][2];
      if (Math.abs(v - (i === j ? 1 : 0)) > 1e-6) return false;
    }
  const det =
    T[0][0] * (T[1][1] * T[2][2] - T[1][2] * T[2][1]) - T[0][1] * (T[1][0] * T[2][2] - T[1][2] * T[2][0]) + T[0][2] * (T[1][0] * T[2][1] - T[1][1] * T[2][0]);
  return det > 0 && Math.abs(T[3][0]) <= 1e-9 && Math.abs(T[3][1]) <= 1e-9 && Math.abs(T[3][2]) <= 1e-9 && Math.abs(T[3][3] - 1) <= 1e-9;
}

type MockScan = Scan & { quality?: ProcessQuality };

/** The frames a merge fuses of one scan: as the target's quality steps through them (Standard: 30 a second of scanning). */
function fusedFrames(s: Scan, quality: ProcessQuality): number {
  const fps = s.duration_s > 0 ? s.n_frames / s.duration_s : 30;
  const step = quality === "standard" ? Math.max(1, Math.round(fps / 30)) : 1;
  return Math.max(1, Math.ceil(s.n_frames / step));
}

const r3 = (x: number) => Math.round(x * 1000) / 1000;

export class MockAlign {
  private host: AlignHost;
  private wrongPose: number[][];

  /** `wrongPose`: the pose returned (for showing) by an alignment that is not accepted. */
  constructor(host: AlignHost, wrongPose: number[][] = HALF_TURN) {
    this.host = host;
    this.wrongPose = wrongPose;
  }

  // ---------------------------------------------------------------- checks
  /** The scan, or the 404 / 409 that says what is missing (the server's `_scan`). */
  private scan(p: Project, sid: unknown, what: "first" | "second", needRecording: boolean): MockScan {
    const s = p.scans.find((x) => x.id === sid);
    if (!s) return this.host.fail(404, `no scan '${String(sid)}' in project '${p.id}'`, "pick a scan from the project's scan list");
    if (s.status === "recording" || s.status === "processing") this.host.fail(409, `the ${what} scan "${s.name}" is busy`, "wait until it has finished, then try again");
    if (s.status !== "processed" || !s.result) this.host.fail(409, `the ${what} scan "${s.name}" has no mesh yet`, "make its mesh on the project page first");
    if (needRecording && s.merged_from?.length)
      this.host.fail(409, `the ${what} scan "${s.name}" has no recording (a merged scan, or its recording was removed)`, "merge scans that still have their recordings");
    return s;
  }

  private pair(p: Project, body: Record<string, unknown>, needRecording: boolean): [MockScan, MockScan] {
    const { target, source } = body;
    if (typeof target !== "string" || typeof source !== "string") this.host.fail(422, "target and source: give the two scans' ids", "pick two scans of the project");
    if (target === source) this.host.fail(409, "a scan cannot be aligned to itself", "pick two different scans");
    return [this.scan(p, target, "first", needRecording), this.scan(p, source, "second", needRecording)];
  }

  // ---------------------------------------------------------------- align
  /** `POST /api/projects/{pid}/align`: a job (kind `align`) whose result is an `AlignResult`. Writes nothing. */
  align(p: Project, body: Record<string, unknown>): Job {
    const [a, b] = this.pair(p, body, false);
    const sameSetup = body.same_setup !== false;
    const pairs = Array.isArray(body.pairs) ? (body.pairs as PointPair[]) : [];
    let pairsRms: number | null = null;
    if (pairs.length) {
      if (pairs.length < MIN_PAIRS) this.host.fail(422, `${pairs.length} pair(s) of points are too few`, "pick at least three matching points on each scan");
      const c = checkPairs(pairs);
      if ("error" in c) this.host.fail(422, `the picked points do not give a pose: ${c.error}`, "pick three or more matching points that span a triangle");
      else pairsRms = r3(c.rms);
    }
    const given = pairsRms !== null;
    const accepted = given || sameSetup;
    const t0 = Date.now();
    const wrongPose = this.wrongPose;
    const work = (function* (): Generator<WorkStep, () => Record<string, unknown>, void> {
      yield { p: 0.05, message: `Reading "${a.name}"` };
      yield { p: 0.25, message: `Reading "${b.name}"` };
      yield { p: 0.45, message: "Aligning the two scans" };
      yield { p: 0.95, message: "Aligning the two scans" };
      return () => {
        const overlap = accepted ? (sameSetup ? 0.76 : 0.71) : 0.43;
        const rmse = accepted ? 0.25 : 0.41;
        const candidates = given ? 1 : 9;
        const agree = given ? 1 : accepted ? 8 : 4;
        const verdict = accepted ? alignedVerdict(overlap, rmse, given ? "given" : "features", agree, candidates) : `Not aligned: ${AMBIGUOUS_REASON}.`;
        const result: AlignResult = {
          project_id: p.id,
          target: a.id,
          source: b.id,
          accepted,
          verdict,
          reason: accepted ? null : AMBIGUOUS_REASON,
          T: (accepted ? IDENTITY : wrongPose).map((r) => [...r]),
          start: given ? "given" : "features",
          overlap,
          rmse_mm: rmse,
          fit_mm: accepted ? 0.21 : 0.38,
          candidates,
          agree,
          spread_mm: accepted ? 0.12 : 0.31,
          dof: 6,
          same_setup: sameSetup,
          used_support: sameSetup,
          support: { target: true, source: true },
          pairs_rms_mm: pairsRms,
          n_matched: accepted ? 18240 : 10310,
          ms: Date.now() - t0,
          message: verdict,
        };
        return result;
      };
    })();
    return this.host.startWorkJob("align", { minSeconds: 2.5, work, extra: { project_id: p.id, target: a.id, source: b.id } });
  }

  // ---------------------------------------------------------------- merge
  /** `POST /api/projects/{pid}/merge`: a job (kind `merge`) that adds the merged scan; its result is a `MergeResult`. */
  merge(p: Project, body: Record<string, unknown>): Job {
    const T = body.T;
    if (!isPose(T)) return this.host.fail(422, "the pose must be a 4x4 matrix", "send the pose the alignment returned");
    if (!isRigid(T)) this.host.fail(422, "the pose is not a rigid motion", "send the pose the alignment returned");
    const [a, b] = this.pair(p, body, true);
    const sameSetup = body.same_setup !== false;
    const title = (typeof body.name === "string" ? body.name.trim() : "") || `${a.name} + ${b.name}`;
    const quality: ProcessQuality = a.quality ?? "standard";
    const n = fusedFrames(a, quality) + fusedFrames(b, quality);
    const host = this.host;
    const work = (function* (): Generator<WorkStep, () => Record<string, unknown>, void> {
      if (!sameSetup) yield { p: 0.02, message: "Finding what the part lay on" };
      const steps = Math.min(n, 80);
      for (let i = 1; i <= steps; i++) {
        const k = Math.max(1, Math.round((n * i) / steps));
        yield { p: 0.05 + (0.85 * k) / n, message: `Merging: frame ${k} of ${n}` };
      }
      yield { p: 0.95, message: "Adding the merged scan to the project" };
      return () => {
        const id = host.uid("s-");
        const base = `/api/projects/${encodeURIComponent(p.id)}/scans/${encodeURIComponent(id)}/files`;
        const scan: MockScan = {
          id,
          name: title,
          created: host.now(),
          mode: "markerless",
          n_frames: n,
          duration_s: 0,
          status: "processed",
          result: {
            mesh_url: `${base}/mesh.ply`,
            stl_url: `${base}/mesh.stl`,
            laser_points_url: `${base}/laser_points.ply`,
            report: { mode: "markerless", quality, frames: { processed: n, tracked: n } },
          },
          merged_from: [
            { id: a.id, name: a.name },
            { id: b.id, name: b.name },
          ],
          quality,
        };
        p.scans.push(scan);
        p.n_scans = p.scans.length;
        p.modified = host.now();
        host.log("info", `Merged scan ready: ${title}`);
        const result: MergeResult = { project_id: p.id, scan_id: id, scan: { ...scan }, merged_from: [a.id, b.id], message: `Merged into "${title}"` };
        return result;
      };
    })();
    return host.startWorkJob("merge", {
      minSeconds: Math.min(12, 4 + n / 200),
      work,
      onFail: (error) => host.log("error", `Merging failed: ${error}. The two scans are unchanged.`),
      extra: { project_id: p.id, target: a.id, source: b.id },
    });
  }
}
