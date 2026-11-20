/**
 * The demo engine's Check (APP_API.md "M5 third slice: areas that need another pass"): a stand-in for
 * `ohs.mesh.weak_areas` that follows its rule, on the mock's meshes.
 *
 * - A face fails when it is filled or one of its vertices has no usable sigma (NaN, zero or less, 50 mm
 *   and above): "not measured"; or when `k` times the mean sigma of its three vertices is above the
 *   tolerance: "uncertain".
 * - Failing faces that share an edge form an area (two patches touching at one vertex stay two). An area
 *   is "not measured" when most of its surface is.
 * - Areas under the minimum size (4 mm², and never fewer than 10 median faces) are left out of the list
 *   but count in the shares, which are by surface area.
 *
 * The demo meshes carry no measured sigma, so {@link madeUpSigma} makes one up from where each vertex
 * lies. Imports nothing at run time, so `npm test` checks it with plain node.
 */
import type { CheckKind, CheckPlanPose, CheckPose, CheckReason, CheckView, CheckWhy, CheckWhyArea } from "../types";

/** A vertex sigma of 50 mm (and above) means "nothing measured it" (the engine's `SIGMA_NONE_MM`). */
export const SIGMA_NONE_MM = 50;
/** The smallest area worth listing, whatever the mesh (mm²): a 2 x 2 mm spot. */
export const MIN_AREA_FLOOR_MM2 = 4;
/** ... and never fewer than this many median faces. */
export const MIN_AREA_FACES = 10;
export const MAX_AREAS = 50;

export type WeakArea = {
  kind: CheckKind;
  area_mm2: number;
  share: number;
  center: [number, number, number];
  extent_mm: number;
  radius_mm: number;
  n_faces: number;
  sigma_median_mm: number | null;
  sigma_worst_mm: number | null;
  /** Sorted face indices. */
  faces: Uint32Array;
};
export type WeakAreas = {
  has_sigma: boolean;
  tolerance_mm: number;
  k: number;
  min_area_mm2: number;
  area_mm2: number;
  share_ok: number | null;
  share_uncertain: number | null;
  share_not_measured: number | null;
  n_areas: number;
  areas: WeakArea[];
};
export type WeakOptions = { k?: number; minAreaMm2?: number | null; maxAreas?: number };

const measured = (s: number) => Number.isFinite(s) && s > 0 && s < SIGMA_NONE_MM;

/**
 * The areas of the mesh that miss `toleranceMm`. `sigma`: one value per vertex (mm), or null for a mesh
 * without confidence (then only the `filled` faces fail and `has_sigma` is false). `filled`: one flag
 * per face, or null. Throws on a bad tolerance or arrays of the wrong length, as the engine does.
 */
export function weakAreas(
  positions: ArrayLike<number>,
  indices: ArrayLike<number>,
  sigma: ArrayLike<number> | null,
  filled: ArrayLike<number | boolean> | null,
  toleranceMm: number,
  options: WeakOptions = {},
): WeakAreas {
  const k = options.k ?? 2;
  const maxAreas = options.maxAreas ?? MAX_AREAS;
  if (!Number.isFinite(toleranceMm) || toleranceMm <= 0) throw new Error(`the tolerance must be above 0 mm, got ${toleranceMm}`);
  if (!Number.isFinite(k) || k <= 0) throw new Error(`k must be above 0, got ${k}`);
  const givenMin = options.minAreaMm2 ?? null;
  if (givenMin !== null && (!Number.isFinite(givenMin) || givenMin < 0)) throw new Error(`the minimum area must be 0 mm^2 or more, got ${givenMin}`);
  if (maxAreas < 0) throw new Error(`max_areas must be 0 or more, got ${maxAreas}`);
  const P = positions;
  const I = indices;
  const V = Math.floor(P.length / 3);
  const F = Math.floor(I.length / 3);
  if (sigma && sigma.length !== V) throw new Error(`${sigma.length} sigma values for ${V} vertices`);
  if (filled && filled.length !== F) throw new Error(`${filled.length} filled flags for ${F} faces`);

  // each face's area, the whole surface, and the minimum size of a listed area
  const area = new Float64Array(F);
  let total = 0;
  for (let f = 0; f < F; f++) {
    const a = I[f * 3] * 3,
      b = I[f * 3 + 1] * 3,
      c = I[f * 3 + 2] * 3;
    const ux = P[b] - P[a],
      uy = P[b + 1] - P[a + 1],
      uz = P[b + 2] - P[a + 2];
    const vx = P[c] - P[a],
      vy = P[c + 1] - P[a + 1],
      vz = P[c + 2] - P[a + 2];
    area[f] = Math.hypot(uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx) / 2;
    total += area[f];
  }
  let minArea = givenMin;
  if (minArea === null) {
    let med = 0;
    if (F) {
      const sorted = area.slice().sort();
      med = F % 2 ? sorted[(F - 1) / 2] : (sorted[F / 2 - 1] + sorted[F / 2]) / 2;
    }
    minArea = Math.max(MIN_AREA_FLOOR_MM2, MIN_AREA_FACES * med);
  }
  const answer = (ok: number | null, unc: number | null, nm: number | null, nAreas: number, areas: WeakArea[]): WeakAreas => ({
    has_sigma: sigma !== null,
    tolerance_mm: toleranceMm,
    k,
    min_area_mm2: minArea as number,
    area_mm2: total,
    share_ok: ok,
    share_uncertain: unc,
    share_not_measured: nm,
    n_areas: nAreas,
    areas,
  });
  if (F === 0 || total <= 0) return answer(null, null, null, 0, []);

  // per face: 0 passes, 1 uncertain, 2 not measured
  const state = new Uint8Array(F);
  let nmArea = 0,
    uncArea = 0,
    failing = 0;
  for (let f = 0; f < F; f++) {
    let st = filled && filled[f] ? 2 : 0;
    if (!st && sigma) {
      const sa = sigma[I[f * 3]],
        sb = sigma[I[f * 3 + 1]],
        sc = sigma[I[f * 3 + 2]];
      if (!(measured(sa) && measured(sb) && measured(sc))) st = 2;
      else if ((k * (sa + sb + sc)) / 3 > toleranceMm) st = 1;
    }
    if (!st) continue;
    state[f] = st;
    failing++;
    if (st === 2) nmArea += area[f];
    else uncArea += area[f];
  }
  const nmShare = nmArea / total;
  const uncShare = sigma ? uncArea / total : null;
  const okShare = sigma ? Math.max(0, 1 - nmShare - (uncShare as number)) : null;
  if (!failing) return answer(okShare, uncShare, nmShare, 0, []);

  // the failing faces, joined over shared edges: every edge goes under its lower vertex, and the faces
  // of two equal edges there are united
  const fidx = new Uint32Array(failing);
  for (let f = 0, n = 0; f < F; f++) if (state[f]) fidx[n++] = f;
  const start = new Uint32Array(V + 1);
  for (let j = 0; j < failing; j++) {
    const o = fidx[j] * 3;
    for (let e = 0; e < 3; e++) start[Math.min(I[o + e], I[o + ((e + 1) % 3)]) + 1]++;
  }
  for (let v = 0; v < V; v++) start[v + 1] += start[v];
  const fill = start.slice(0, V);
  const heHigh = new Uint32Array(failing * 3);
  const heFace = new Uint32Array(failing * 3);
  for (let j = 0; j < failing; j++) {
    const o = fidx[j] * 3;
    for (let e = 0; e < 3; e++) {
      const a = I[o + e],
        b = I[o + ((e + 1) % 3)];
      const at = fill[Math.min(a, b)]++;
      heHigh[at] = Math.max(a, b);
      heFace[at] = j;
    }
  }
  const parent = new Int32Array(failing);
  for (let j = 0; j < failing; j++) parent[j] = j;
  const find = (x: number) => {
    while (parent[x] !== x) {
      parent[x] = parent[parent[x]];
      x = parent[x];
    }
    return x;
  };
  for (let v = 0; v < V; v++)
    for (let i = start[v]; i < start[v + 1]; i++)
      for (let j = i + 1; j < start[v + 1]; j++)
        if (heHigh[i] === heHigh[j]) {
          const ra = find(heFace[i]),
            rb = find(heFace[j]);
          if (ra !== rb) parent[ra < rb ? rb : ra] = ra < rb ? ra : rb;
        }

  // groups, numbered by their first face; their area and how much of it was not measured
  const group = new Int32Array(failing);
  const label = new Int32Array(failing).fill(-1);
  let nGroups = 0;
  for (let j = 0; j < failing; j++) {
    const r = find(j);
    if (label[r] < 0) label[r] = nGroups++;
    group[j] = label[r];
  }
  const gArea = new Float64Array(nGroups);
  const gNm = new Float64Array(nGroups);
  const gFaces = new Uint32Array(nGroups);
  for (let j = 0; j < failing; j++) {
    const g = group[j];
    gArea[g] += area[fidx[j]];
    if (state[fidx[j]] === 2) gNm[g] += area[fidx[j]];
    gFaces[g]++;
  }
  const listed: number[] = [];
  for (let g = 0; g < nGroups; g++) if (gArea[g] >= minArea) listed.push(g);
  listed.sort((a, b) => gArea[b] - gArea[a] || a - b); // largest first, ties in face order
  const nAreas = listed.length;
  listed.length = Math.min(nAreas, maxAreas);

  // the listed areas' faces (ascending, as the failing faces are)
  const place = new Int32Array(nGroups).fill(-1);
  const lists = listed.map((g, i) => {
    place[g] = i;
    return new Uint32Array(gFaces[g]);
  });
  const filledTo = new Uint32Array(listed.length);
  for (let j = 0; j < failing; j++) {
    const i = place[group[j]];
    if (i >= 0) lists[i][filledTo[i]++] = fidx[j];
  }

  const stamp = new Int32Array(V).fill(-1);
  const areas = listed.map((g, i): WeakArea => {
    const faces = lists[i];
    let cx = 0,
      cy = 0,
      cz = 0;
    for (let n = 0; n < faces.length; n++) {
      const f = faces[n];
      const a = I[f * 3] * 3,
        b = I[f * 3 + 1] * 3,
        c = I[f * 3 + 2] * 3;
      const w = area[f] / 3;
      cx += w * (P[a] + P[b] + P[c]);
      cy += w * (P[a + 1] + P[b + 1] + P[c + 1]);
      cz += w * (P[a + 2] + P[b + 2] + P[c + 2]);
    }
    cx /= gArea[g];
    cy /= gArea[g];
    cz /= gArea[g];
    let r2 = 0;
    const lo = [Infinity, Infinity, Infinity];
    const hi = [-Infinity, -Infinity, -Infinity];
    const good: number[] = [];
    for (let n = 0; n < faces.length; n++)
      for (let e = 0; e < 3; e++) {
        const v = I[faces[n] * 3 + e];
        if (stamp[v] === i) continue;
        stamp[v] = i;
        const x = P[v * 3],
          y = P[v * 3 + 1],
          z = P[v * 3 + 2];
        r2 = Math.max(r2, (x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2);
        lo[0] = Math.min(lo[0], x);
        lo[1] = Math.min(lo[1], y);
        lo[2] = Math.min(lo[2], z);
        hi[0] = Math.max(hi[0], x);
        hi[1] = Math.max(hi[1], y);
        hi[2] = Math.max(hi[2], z);
        if (sigma && measured(sigma[v])) good.push(sigma[v]);
      }
    let median: number | null = null;
    let worst: number | null = null;
    if (good.length) {
      good.sort((a, b) => a - b);
      const h = good.length >> 1;
      median = good.length % 2 ? good[h] : (good[h - 1] + good[h]) / 2;
      worst = good[good.length - 1];
    }
    return {
      kind: 2 * gNm[g] > gArea[g] ? "not_measured" : "uncertain",
      area_mm2: gArea[g],
      share: gArea[g] / total,
      center: [cx, cy, cz],
      extent_mm: Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]),
      radius_mm: Math.sqrt(r2),
      n_faces: faces.length,
      sigma_median_mm: median,
      sigma_worst_mm: worst,
      faces,
    };
  });
  return answer(okShare, uncShare, nmShare, nAreas, areas);
}

// ------------------------------------------------------------------ made-up reasons and poses (FEATURE_PLAN N1)
/** The demo scanner's working distance (mm): where the nominal scanner's laser sheets cross. */
export const MOCK_WORKING_MM = 254;
/** The demo's camera path: as many frames as a short scan, all of them looked at. */
const MOCK_FRAMES = 96;

type V3 = [number, number, number];
const norm3 = (a: V3): V3 => {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
};
const cross3 = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

/** An area's normal: the mean of its corners' normals, weighted by each face's area (0 0 0 when it faces every way). */
function areaNormal(P: ArrayLike<number>, N: ArrayLike<number> | null, I: ArrayLike<number>, faces: ArrayLike<number>): V3 {
  let x = 0,
    y = 0,
    z = 0,
    total = 0;
  for (let n = 0; n < faces.length; n++) {
    const o = faces[n] * 3;
    const a = I[o] * 3,
      b = I[o + 1] * 3,
      c = I[o + 2] * 3;
    const ux = P[b] - P[a],
      uy = P[b + 1] - P[a + 1],
      uz = P[b + 2] - P[a + 2];
    const vx = P[c] - P[a],
      vy = P[c + 1] - P[a + 1],
      vz = P[c + 2] - P[a + 2];
    const cx = uy * vz - uz * vy,
      cy = uz * vx - ux * vz,
      cz = ux * vy - uy * vx;
    const w = Math.hypot(cx, cy, cz) / 2;
    total += w;
    if (N) {
      x += (w * (N[a] + N[b] + N[c])) / 3;
      y += (w * (N[a + 1] + N[b + 1] + N[c + 1])) / 3;
      z += (w * (N[a + 2] + N[b + 2] + N[c + 2])) / 3;
    } else {
      x += cx / 2;
      y += cy / 2;
      z += cz / 2;
    }
  }
  const l = Math.hypot(x, y, z);
  return total > 0 && l > 0.3 * total ? [x / l, y / l, z / l] : [0, 0, 0];
}

/** The pose `tiltDeg` off the normal `n` (towards `side`) at the working distance, its cameras' centre line on `target`. */
function mockPose(target: V3, n: V3, side: V3, tiltDeg: number, azimuthDeg: number): CheckPose {
  const t = (tiltDeg * Math.PI) / 180;
  const d = norm3([Math.cos(t) * n[0] + Math.sin(t) * side[0], Math.cos(t) * n[1] + Math.sin(t) * side[1], Math.cos(t) * n[2] + Math.sin(t) * side[2]]);
  const fwd: V3 = [-d[0], -d[1], -d[2]];
  // image up as close to the part's up as the view allows (the demo meshes are OpenCV's: up is -y)
  let up: V3 = [0, -1, 0];
  const k = up[0] * fwd[0] + up[1] * fwd[1] + up[2] * fwd[2];
  up = Math.abs(k) > 0.95 ? norm3(cross3(fwd, [1, 0, 0])) : norm3([up[0] - k * fwd[0], up[1] - k * fwd[1], up[2] - k * fwd[2]]);
  const right = norm3(cross3(fwd, up));
  const centre: V3 = [target[0] + MOCK_WORKING_MM * d[0], target[1] + MOCK_WORKING_MM * d[1], target[2] + MOCK_WORKING_MM * d[2]];
  const cam: V3 = [centre[0] - 30 * right[0], centre[1] - 30 * right[1], centre[2] - 30 * right[2]];
  return {
    T_wc: [
      [right[0], -up[0], fwd[0], cam[0]],
      [right[1], -up[1], fwd[1], cam[1]],
      [right[2], -up[2], fwd[2], cam[2]],
      [0, 0, 0, 1],
    ],
    position_mm: centre,
    camera_position_mm: cam,
    look_dir: fwd,
    up,
    target_mm: target,
    distance_mm: MOCK_WORKING_MM,
    tilt_deg: tiltDeg,
    azimuth_deg: azimuthDeg,
  };
}

const NO_EDGE_POSE = "no pose: on an edge or a tight curve another pass alone does not lower the uncertainty";

/**
 * The demo engine's `POST .../check/why`: a reason for each area of `r` and a pose to scan it from, made up
 * from where it lies (the demo has no camera path): a filled hole is "not seen", a small area "on an edge",
 * the others in turn seen only at a steep angle, too few times or from too far. Each pose stands at the
 * working distance, square on, or 20 degrees off for a steep one; the plan takes the largest areas' poses,
 * an area another pose already sees for the most part getting none of its own.
 */
export function madeUpWhy(
  positions: ArrayLike<number>,
  normals: ArrayLike<number> | null,
  indices: ArrayLike<number>,
  r: WeakAreas,
  version: number,
): CheckWhy {
  const ev = (o: Partial<CheckWhyArea["evidence"]>): CheckWhyArea["evidence"] => ({
    looks: 11,
    looks_passing: 12,
    view_angle_deg: 24,
    distance_mm: 248,
    in_view_share: 1,
    edge_share: 0.1,
    border_share: 0,
    outward: null,
    ...o,
  });
  let turn = 0;
  const areas = r.areas.map((a, i): CheckWhyArea => {
    const normal = areaNormal(positions, normals, indices, a.faces);
    const faces = normal[0] === 0 && normal[1] === 0 && normal[2] === 0;
    let reason: CheckReason;
    let evidence: CheckWhyArea["evidence"];
    if (a.kind === "not_measured") {
      reason = "not_seen";
      evidence = ev({ looks: null, view_angle_deg: null, distance_mm: null, in_view_share: 0.2, edge_share: null });
    } else if (a.extent_mm < 6 || faces) {
      reason = "edge";
      evidence = ev({ edge_share: 0.8 });
    } else {
      reason = (["steep", "few_looks", "far"] as const)[turn++ % 3];
      evidence =
        reason === "steep"
          ? ev({ view_angle_deg: 64 + (i % 5) * 2 })
          : reason === "few_looks"
            ? ev({ looks: 4 + (i % 2), looks_passing: 12 })
            : ev({ distance_mm: 385 + (i % 4) * 8 });
    }
    const text = {
      not_seen: "Not seen at all: nothing measured this surface (a hole or a shadow).",
      steep: `Seen only at a steep angle: even the squarest look was ${evidence.view_angle_deg}° off the surface's normal.`,
      far: `Seen from too far: the nearest look was ${evidence.distance_mm} mm away (hold the scanner at about ${MOCK_WORKING_MM} mm).`,
      few_looks: `Seen too few times: ${evidence.looks} independent looks, where the surface that passes had 12 (go over it again, more slowly).`,
      edge: "On an edge, a tight curve or an uneven surface: the mesh's own uncertainty there is above the tolerance, and another pass alone does not lower it.",
      unknown: "No reason the data can tell.",
    }[reason];
    let view: CheckView;
    if (reason === "edge") {
      view = { ok: false, pose: null, share_seen: null, border_seen: null, candidates: 0, missed: null, reason: NO_EDGE_POSE, text: "No pose: on an edge or a tight curve another pass alone does not lower the uncertainty." };
    } else {
      const n: V3 = faces ? [0, -1, 0] : normal;
      const side = norm3(cross3(n, Math.abs(n[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0]));
      const tilt = reason === "steep" ? 20 : 0;
      const pose = mockPose(a.center, n, side, tilt, 0);
      const share = 0.9 + 0.08 * Math.abs(Math.sin(i + 1));
      view = {
        ok: true,
        pose,
        share_seen: share,
        border_seen: null,
        candidates: 25,
        missed: { blocked: 0, facing_away: 0, steep: 0, outside: 1 - share },
        reason: null,
        text: `Hold the scanner about ${MOCK_WORKING_MM} mm from the area${tilt ? `, ${tilt}° off square` : ", square on to it"}: it would see ${Math.round(100 * share)} % of it.`,
      };
    }
    return {
      index: i,
      kind: a.kind,
      area_mm2: a.area_mm2,
      n_faces: a.n_faces,
      center: a.center,
      normal,
      reason,
      reasons: reason === "steep" && i % 2 ? ["steep", "few_looks"] : [reason],
      reason_text: text,
      evidence,
      unknown_because: [],
      view,
    };
  });
  // the plan: the largest areas' poses, skipping an area within reach of a pose already chosen
  const plan: CheckPlanPose[] = [];
  for (const a of areas) {
    if (plan.length >= 3) break;
    if (!a.view.ok || !a.view.pose) continue;
    const near = (p: CheckPlanPose, b: CheckWhyArea) => Math.hypot(...([0, 1, 2].map((k) => b.center[k] - p.pose.target_mm[k]) as V3)) < 25;
    if (plan.some((p) => near(p, a))) continue;
    const entry: CheckPlanPose = { area: a.index, kind: "area", pose: a.view.pose, share_seen: a.view.share_seen ?? 0, border_seen: null, also_sees: [] };
    entry.also_sees = areas.filter((b) => b.index !== a.index && near(entry, b)).map((b) => ({ area: b.index, share: 0.7 }));
    plan.push(entry);
  }
  return {
    version,
    tolerance_mm: r.tolerance_mm,
    n_areas: r.n_areas,
    areas,
    plan,
    given: { looks: true, views: true, edges: true, working_mm: MOCK_WORKING_MM, steep_deg: 59.6, far_mm: Math.round(MOCK_WORKING_MM * Math.SQRT2) },
    camera_path: { available: true, why: null, scans: [{ name: "Demo scan", frames: MOCK_FRAMES, trajectory: "result/trajectory.csv" }], frames_total: MOCK_FRAMES, frames_used: MOCK_FRAMES, frames_cap: 120 },
    ms: 0,
  };
}

// ------------------------------------------------------------------ a made-up sigma for the demo meshes
/** A place the scan is less sure of: an ellipsoid (centre, radii, mm). `none`: nothing was measured inside it. */
export type SigmaSpot = { c: [number, number, number]; r: [number, number, number]; gain: number; none?: boolean };
/** Where the made-up sigma comes from: the spots, and the middle and half size (x, z) of what the scan covers. */
export type SigmaScene = { spots: SigmaSpot[]; centre: [number, number]; half: [number, number] };

/** A vertex inside this share of a `none` spot's radii has no sigma: hole filling made it. */
const NONE_INSIDE = 0.85;

/**
 * A believable sigma (mm) for each vertex, from its position alone, so it needs no bookkeeping through
 * the mock's edits: a few hundredths of a mm over most of the surface, more towards the edge of what
 * the scan covers and around each spot, a noisy vertex now and then (specks), and 50 mm ("not
 * measured") inside the `none` spots: the mock's scan has no surface there, so a vertex there was made
 * by hole filling.
 */
export function madeUpSigma(positions: ArrayLike<number>, scene: SigmaScene): Float32Array {
  const V = Math.floor(positions.length / 3);
  const out = new Float32Array(V);
  for (let i = 0; i < V; i++) {
    const x = positions[i * 3],
      y = positions[i * 3 + 1],
      z = positions[i * 3 + 2];
    let s = 0.04 + 0.012 * Math.sin(x * 0.045 + 0.6) * Math.cos(z * 0.038 - 0.4) + 0.006 * Math.sin((x + z) * 0.11);
    // towards the edge of the scanned patch the views are few and flat
    const u = (x - scene.centre[0]) / scene.half[0],
      w = (z - scene.centre[1]) / scene.half[1];
    const t = Math.min(1, Math.max(0, (u * u * u * u + w * w * w * w - 0.45) / 0.45));
    s += 0.07 * t * t * (3 - 2 * t);
    let none = false;
    for (const spot of scene.spots) {
      const dx = (x - spot.c[0]) / spot.r[0],
        dy = (y - spot.c[1]) / spot.r[1],
        dz = (z - spot.c[2]) / spot.r[2];
      const d2 = dx * dx + dy * dy + dz * dz;
      if (d2 > 16) continue;
      const d = Math.sqrt(d2);
      if (spot.none && d < NONE_INSIDE) {
        none = true;
        break;
      }
      s += spot.gain * (d <= 1 ? 1 : Math.exp(-(((d - 1) / 0.9) ** 2)));
    }
    if (none) {
      out[i] = SIGMA_NONE_MM;
      continue;
    }
    // about one vertex in 2500 is noisy: a speck too small to list
    const h = Math.sin(x * 12.9898 + y * 78.233 + z * 37.719) * 43758.5453;
    if (h - Math.floor(h) < 0.0004) s += 0.45;
    out[i] = s;
  }
  return out;
}
