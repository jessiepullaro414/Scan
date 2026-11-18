/**
 * The demo engine's "align two scans and merge them" (APP_API.md "M4 first slice"): the two routes with
 * the server's rules and its error replies (`ohs/server/aligning.py`), and made-up answers.
 *
 * The demo's scans are all the same scene in the same frame (scanmesh.ts), so the pose that fits is
 * "leave the second scan where it is" (the identity), and nothing is measured:
 *
 * - an alignment from the shape alone of scans whose part was moved (`same_setup: false`, no pairs) is
 *   NOT accepted when one of the two was turned over (its name says "underside", "bottom" or "turned
 *   over", `turnedOver`): the demo part (a ball and a block on a table) "fits in more than one
 *   position", and the pose returned for showing is such a wrong one (the constructor's `wrongPose`);
 * - with picked pairs, or with `same_setup: true` (the shared table pins the rest), it is accepted, with
 *   plausible figures and the engine's sentence;
 * - `pairs_rms_mm` is real in spirit: how far the distances between the picked points differ on the two
 *   scans (a rigid motion keeps them), so a pair that does not match shows as a large value;
 * - a merge counts the frames of both recordings, can be cancelled (nothing is added then) and adds a
 *   new processed scan with `merged_from` to the project. A merged scan has no recording: it can be
 *   aligned to, not merged again.
 *
 * Several scans at once (APP_API.md "M4 second slice", `alignAll` and the merge's `scans` form): every
 * pair is aligned by the rules above (a pair with picked points is accepted); the scans are placed from
 * the first one through the accepted pairs of most overlap (the chain); the other accepted pairs check
 * it ("loop", disagreeing by a few tenths of a mm, or by their picked points' spread when that is more)
 * or, with picked points that disagree by more than 5 mm, are left out. A scan no accepted pair reaches
 * is not placed (`T` null). So with all three seeded bracket scans the part staying where it was places
 * all three, and with the part moved "Bracket underside" is placed only through picked points.
 *
 * No imports at run time (types only), so `npm test` runs it with Node: the mock engine hands in what it
 * needs (`AlignHost`).
 */
import type { AlignAllPair, AlignAllResult, AlignPose, AlignResult, Job, MergeResult, MergeTable, PairUse, PointPair, ProcessQuality, Project, Scan } from "../types";
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
/** Several scans at once: from two to this many. */
export const MAX_SCANS = 8;
/** Picked points that disagree by more than this (mm) make a pair that does not fit: left out of a loop. */
const PAIRS_BAD_MM = 5;
/** A pair that disagrees by more than this (mm) with the placement adds a warning to the verdict. */
const LINK_WARN_MM = 2;

/** The demo pretends a scan whose name says so was turned over between the scans. */
export const turnedOver = (s: Pick<Scan, "name">): boolean => /underside|bottom|turned over/i.test(s.name);

/** The demo's rule for an alignment from the shape alone: the shared table settles it, else neither may be turned over. */
export const fitsFromShape = (a: Pick<Scan, "name">, b: Pick<Scan, "name">, sameSetup: boolean): boolean => sameSetup || (!turnedOver(a) && !turnedOver(b));

/** Names in the engine's sentences: "A", "A" and "B", "A", "B" and "C". */
export function quotedNames(names: readonly string[]): string {
  const q = names.map((n) => `"${n}"`);
  return q.length <= 1 ? (q[0] ?? "") : `${q.slice(0, -1).join(", ")} and ${q[q.length - 1]}`;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** The engine's sentence for several scans (`align_all`). */
export function alignAllVerdict(o: { n: number; chain: number; loops: number; leftOut: number; worst: number; unplaced: string[] }): string {
  let v: string;
  if (o.unplaced.length)
    v = `Not all placed: ${quotedNames(o.unplaced)} could not be aligned to any placed scan. Pick matching points between each of them and a placed scan, or scan with more overlap.`;
  else {
    v = `All ${o.n} scans placed through ${plural(o.chain, "link")}`;
    if (o.loops) v += `; ${o.loops} more link${o.loops === 1 ? "" : "s"} checked the chain, the worst disagrees by ${o.worst.toFixed(2)} mm`;
    v += ".";
  }
  if (o.leftOut) v += ` ${plural(o.leftOut, "link")} did not fit the others and ${o.leftOut === 1 ? "was" : "were"} left out.`;
  if (o.worst > LINK_WARN_MM) v += " That is a lot: look at the scans together before merging.";
  return v;
}

/** The engine's reason when the shape alone leaves more than one answer (`ohs.align.pairwise`, MAINTENANCE_PLAN F11: "not sure"). */
export const AMBIGUOUS_REASON =
  "not sure which way it goes: it fits about as well turned round (98 % as well, 61 mm away; a symmetric shape, a repeated feature, or a surface that can slide on itself): " +
  "pick three or more matching points on both scans, or choose one of the poses";

/** The poses a "not sure" answer carries: the engine's best (`best`, where it put the scan) and the same turned round. */
function notSurePoses(best: number[][], other: number[][]): AlignPose[] {
  const row = (T: number[][], chosen: boolean, pick: number, reached: number, from: string[], apart: number): AlignPose => ({
    T: T.map((r) => [...r]),
    chosen,
    pick,
    overlap: pick,
    rmse_mm: 0.41,
    fit_mm: 0.38,
    reached,
    from,
    apart_mm: apart,
  });
  return [row(best, true, 0.43, 4, ["the first guesses"], 0), row(other, false, 0.42, 2, ["turned round"], 61)];
}

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
  private scan(p: Project, sid: unknown, what: "first" | "second" | null, needRecording: boolean): MockScan {
    const s = p.scans.find((x) => x.id === sid);
    if (!s) return this.host.fail(404, `no scan '${String(sid)}' in project '${p.id}'`, "pick a scan from the project's scan list");
    const the = what ? `the ${what} scan` : "the scan";
    if (s.status === "recording" || s.status === "processing") this.host.fail(409, `${the} "${s.name}" is busy`, "wait until it has finished, then try again");
    if (s.status !== "processed" || !s.result) this.host.fail(409, `${the} "${s.name}" has no mesh yet`, "make its mesh on the project page first");
    if (needRecording && s.merged_from?.length)
      this.host.fail(409, `${the} "${s.name}" has no recording (a merged scan, or its recording was removed)`, "merge scans that still have their recordings");
    return s;
  }

  /** 2..8 different scans with a mesh (the server's checks of a list): 422 for the count, 409 for one listed twice. */
  private list(p: Project, ids: unknown, needRecording: boolean): MockScan[] {
    if (!Array.isArray(ids) || ids.some((x) => typeof x !== "string"))
      return this.host.fail(422, "scans: give the ids of 2 to 8 scans", "pick the scans from the project's scan list");
    if (ids.length < 2 || ids.length > MAX_SCANS)
      this.host.fail(422, `${ids.length} scan(s) given: 2 to ${MAX_SCANS} are aligned at once`, ids.length < 2 ? "add a second scan" : "take at most eight scans at a time");
    const seen = new Set<string>();
    for (const id of ids as string[]) {
      if (seen.has(id)) {
        const name = p.scans.find((x) => x.id === id)?.name;
        this.host.fail(409, `the scan ${name ? `"${name}"` : `'${id}'`} is listed twice`, "list each scan once");
      }
      seen.add(id);
    }
    return (ids as string[]).map((id) => this.scan(p, id, null, needRecording));
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
    const accepted = given || fitsFromShape(a, b, sameSetup);
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
          sure: accepted,
          doubt: accepted ? null : "turned",
          poses: accepted ? [] : notSurePoses(wrongPose, IDENTITY),
          turned: 8,
          reached: accepted ? agree : 4,
        };
        return result;
      };
    })();
    return this.host.startWorkJob("align", { minSeconds: 2.5, work, extra: { project_id: p.id, target: a.id, source: b.id } });
  }

  // ---------------------------------------------------------------- several scans
  /** `POST /api/projects/{pid}/align_all`: a job (kind `align_all`) whose result is an `AlignAllResult`. Writes nothing. */
  alignAll(p: Project, body: Record<string, unknown>): Job {
    const list = this.list(p, body.scans, false);
    const ids = list.map((s) => s.id);
    const nameOf = (id: string) => list.find((s) => s.id === id)?.name ?? id;
    const sameSetup = body.same_setup !== false;

    // picked points, checked as for two scans; by pair, either way round
    const picked = new Map<string, number>(); // pair key -> how well its points agree (mm)
    const key = (x: string, y: string) => (x < y ? `${x}\n${y}` : `${y}\n${x}`);
    if (body.picked !== undefined && !Array.isArray(body.picked)) this.host.fail(422, "picked: give a list of {a, b, pairs}", "pick matching points on two listed scans");
    for (const e of (body.picked as unknown[] | undefined) ?? []) {
      const { a, b, pairs } = (e ?? {}) as { a?: unknown; b?: unknown; pairs?: unknown };
      for (const x of [a, b])
        if (typeof x !== "string" || !ids.includes(x)) this.host.fail(422, `picked points name a scan that is not in the list ('${String(x)}')`, "pick points only on the listed scans");
      if (a === b) this.host.fail(422, "picked points must be between two different scans", "pick the points on two different scans");
      const between = `"${nameOf(a as string)}" and "${nameOf(b as string)}"`;
      const pts = Array.isArray(pairs) ? (pairs as PointPair[]) : [];
      if (pts.length < MIN_PAIRS) this.host.fail(422, `${pts.length} pair(s) of points between ${between} are too few`, "pick at least three matching points on each scan");
      const c = checkPairs(pts);
      if ("error" in c) this.host.fail(422, `the points picked between ${between} do not give a pose: ${c.error}`, "pick three or more matching points that span a triangle");
      else picked.set(key(a as string, b as string), r3(c.rms));
    }

    // every pair, by the two-scan rules
    type Link = AlignAllPair & { rms: number | null };
    const links: Link[] = [];
    let k = 0;
    for (let i = 0; i < list.length; i++)
      for (let j = i + 1; j < list.length; j++, k++) {
        const A = list[i],
          B = list[j];
        const pairsRms = picked.get(key(A.id, B.id)) ?? null;
        const given = pairsRms !== null;
        const accepted = given || fitsFromShape(A, B, sameSetup);
        const overlap = accepted ? r3(Math.max(0.4, (sameSetup ? 0.76 : 0.71) - 0.03 * k)) : 0.43;
        const rmse = accepted ? 0.25 : 0.41;
        const candidates = given ? 1 : 9;
        const agree = given ? 1 : accepted ? 8 : 4;
        const verdict = accepted ? alignedVerdict(overlap, rmse, given ? "given" : "features", agree, candidates) : `Not aligned: ${AMBIGUOUS_REASON}.`;
        links.push({
          project_id: p.id,
          target: A.id,
          source: B.id,
          a: A.id,
          b: B.id,
          used: "",
          accepted,
          verdict,
          reason: accepted ? null : AMBIGUOUS_REASON,
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
          ms: 0,
          message: verdict,
          rms: pairsRms,
        });
      }

    const t0 = Date.now();
    const work = (function* (): Generator<WorkStep, () => Record<string, unknown>, void> {
      for (let i = 0; i < links.length; i++) {
        const l = links[i];
        yield { p: 0.03 + (0.9 * i) / links.length, message: `Aligning "${nameOf(l.a)}" and "${nameOf(l.b)}" (pair ${i + 1} of ${links.length})` };
      }
      yield { p: 0.95, message: "Placing the scans" };
      return () => {
        // the chain: from the first scan, always the accepted pair of most overlap that reaches a new scan
        const placed = new Set<string>([ids[0]]);
        for (;;) {
          let best: Link | null = null;
          for (const l of links)
            if (l.accepted && l.used === "" && placed.has(l.a) !== placed.has(l.b) && (!best || l.overlap > best.overlap)) best = l;
          if (!best) break;
          best.used = "chain";
          placed.add(best.a);
          placed.add(best.b);
        }
        // the other accepted pairs between placed scans check the chain, or do not fit and are left out
        const SPREAD = [0.27, 0.19, 0.33, 0.22, 0.3, 0.24, 0.21];
        let loops = 0;
        let checked = 0;
        let leftOut = 0;
        for (const l of links) {
          if (!l.accepted || l.used !== "" || !placed.has(l.a) || !placed.has(l.b)) continue;
          loops++;
          if (l.rms !== null && l.rms > PAIRS_BAD_MM) {
            l.used = "left out";
            l.disagree_mm = r3(l.rms);
            leftOut++;
          } else {
            l.used = "loop";
            l.disagree_mm = r3(Math.max(SPREAD[checked % SPREAD.length], l.rms ?? 0));
            checked++;
          }
        }
        const graph = loops > 0;
        if (graph) for (const l of links) if (l.used === "chain") l.disagree_mm = 0.14;
        const worst = links.reduce((w, l) => (l.used === "chain" || l.used === "loop" ? Math.max(w, l.disagree_mm ?? 0) : w), 0);
        const unplaced = ids.filter((id) => !placed.has(id));
        const T: Record<string, number[][] | null> = {};
        for (const id of ids) T[id] = placed.has(id) ? IDENTITY.map((r) => [...r]) : null;
        const verdict = alignAllVerdict({ n: ids.length, chain: links.filter((l) => l.used === "chain").length, loops: checked, leftOut, worst, unplaced: unplaced.map(nameOf) });
        const result: AlignAllResult = {
          project_id: p.id,
          scans: [...ids],
          accepted: unplaced.length === 0,
          T,
          placed: ids.filter((id) => placed.has(id)),
          unplaced,
          pairs: links.map(({ rms: _rms, ...l }) => ({ ...l, used: l.used as PairUse })),
          loops,
          graph,
          worst_link_mm: r3(worst),
          support: Object.fromEntries(ids.map((id) => [id, true])),
          same_setup: sameSetup,
          ms: Date.now() - t0,
          verdict,
          message: verdict,
        };
        return result;
      };
    })();
    return this.host.startWorkJob("align_all", { minSeconds: Math.min(10, 1.5 + 1.2 * links.length), work, extra: { project_id: p.id, scans: [...ids] } });
  }

  // ---------------------------------------------------------------- merge
  /**
   * `POST /api/projects/{pid}/merge`: a job (kind `merge`) that adds the merged scan; its result is a
   * `MergeResult`. Either form: two scans (`target`, `source`, `T`) or several (`scans: [{id, T}]`).
   */
  merge(p: Project, body: Record<string, unknown>): Job {
    const several = body.scans !== undefined;
    const two = body.target !== undefined || body.source !== undefined || body.T !== undefined;
    if (several && two) this.host.fail(422, "give either target, source and T, or scans, not both", "send one form of the merge");
    if (!several && !two) this.host.fail(422, "nothing to merge: give target, source and T, or scans", "send the scans and the poses the alignment returned");
    const sameSetup = body.same_setup !== false;
    const name = typeof body.name === "string" ? body.name.trim() : "";
    if (!several) {
      const T = body.T;
      if (!isPose(T)) return this.host.fail(422, "the pose must be a 4x4 matrix", "send the pose the alignment returned");
      if (!isRigid(T)) this.host.fail(422, "the pose is not a rigid motion", "send the pose the alignment returned");
      const [a, b] = this.pair(p, body, true);
      return this.startMerge(p, [a, b], sameSetup, name, { target: a.id, source: b.id });
    }
    const entries = body.scans;
    if (!Array.isArray(entries) || entries.some((e) => !e || typeof e !== "object" || typeof (e as { id?: unknown }).id !== "string"))
      return this.host.fail(422, "scans: give each scan as {id, T}", "send the scans and the poses the alignment returned");
    const list = this.list(
      p,
      entries.map((e) => (e as { id: string }).id),
      true,
    );
    entries.forEach((e, i) => {
      const T = (e as { T?: unknown }).T;
      if (!isPose(T)) this.host.fail(422, `the pose of "${list[i].name}" must be a 4x4 matrix`, "send the poses the alignment returned");
      else if (!isRigid(T)) this.host.fail(422, `the pose of "${list[i].name}" is not a rigid motion`, "send the poses the alignment returned");
    });
    return this.startMerge(p, list, sameSetup, name, list.length === 2 ? { target: list[0].id, source: list[1].id } : {});
  }

  /** The merge job of 2..8 checked scans (the first one's quality); `name` empty: their names joined by " + ". */
  private startMerge(p: Project, list: MockScan[], sameSetup: boolean, name: string, meta: Record<string, unknown>): Job {
    const title = name || list.map((s) => s.name).join(" + ");
    const quality: ProcessQuality = list[0].quality ?? "standard";
    const n = list.reduce((sum, s) => sum + fusedFrames(s, quality), 0);
    const unchanged = list.length > 2 ? "The scans are unchanged." : "The two scans are unchanged.";
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
          merged_from: list.map((x) => ({ id: x.id, name: x.name })),
          quality,
        };
        p.scans.push(scan);
        p.n_scans = p.scans.length;
        p.modified = host.now();
        host.log("info", `Merged scan ready: ${title}`);
        // the part was moved: each scan's table found on its own mesh and left out (the engine's words); the
        // first scan's table has a raised area the engine cannot tell from a thin part: kept, and said
        const tables: MergeTable[] = sameSetup
          ? []
          : list.map((s, i) => {
              const warnings =
                i === 0
                  ? [
                      `"${s.name}": A raised area 14 mm across, 0.27 mm above the table round it, at (62, -40, 0) mm, may be a thin part: it is kept in the merged scan (if it is the table's own, a patch of table that size is left there).`,
                    ]
                  : [];
              const area = 2404 - 180 * i;
              return {
                id: s.id,
                name: s.name,
                rule: "map" as const,
                summary: `Its table is left out (${area.toLocaleString("en-US")}.0 cm² of its mesh, flat to 0.08 mm); the part is kept right down to it${i === 0 ? "; 1 raised area that may be a thin part is kept too" : ""}. What the frames left of it next to the part, ${(812 + 97 * i).toLocaleString("en-US")} triangles (${35 + 4 * i} mm²), was taken out of the merged scan.`,
                warnings,
              };
            });
        const result: MergeResult = {
          project_id: p.id,
          scan_id: id,
          scan: { ...scan },
          merged_from: list.map((x) => x.id),
          message: `Merged into "${title}"`,
          tables,
          warnings: tables.flatMap((t) => t.warnings),
          cleaning: sameSetup ? null : "2 small loose pieces (14 mm²) were left out as too small (under 1 % of the triangles each); 1 small piece over a thin part was kept.",
        };
        return result;
      };
    })();
    return host.startWorkJob("merge", {
      minSeconds: Math.min(12, 4 + n / 200),
      work,
      onFail: (error) => host.log("error", `Merging failed: ${error}. ${unchanged}`),
      extra: { project_id: p.id, scans: list.map((x) => x.id), ...meta },
    });
  }
}
