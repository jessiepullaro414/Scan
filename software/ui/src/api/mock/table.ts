/**
 * The demo engine's "Remove the table" (FEATURE_PLAN X1). The engine finds the plane a part stands
 * on from the scan itself and grows the table over the faces joined to it (ohs/mesh/support.py);
 * the demo knows where its table is (the scene's y = 0, moved into the scan's world by `T_WS`), so
 * its rule is simpler: a face is the table's when it faces the table's way (its normal within 45°
 * of the table's) and its centre lies at most the margin above the plane (never less than the
 * 0.3 mm noise band), or when all its corners lie more than the band behind the plane.
 *
 * Thin parts lying flat: faces that lie flat (within 10° of the table) more than 0.2 mm above it
 * are joined through shared corners into raised areas (at least 25 mm²). One at 0.5 mm or more is a
 * thin part and stays ("kept"); a lower one goes with the table but is drawn and listed ("warned"),
 * unless a "Keep this" point lies within 3 mm of one of its faces. 0.2 mm and less goes unsaid: the
 * demo's own limit (its meshes are noisier than the engine's, up to 0.2 mm). No faces are grown or
 * joined and no loose crumbs are looked for. When less than 15 % of the surface is the table (the
 * table removed already, or cut away by hand) it refuses, in the engine's words.
 */
import type { TablePreview, TableRaised } from "../types";

export const MOCK_MARGIN_MM = 1;
const BAND_MM = 0.3;
const FACING_COS = Math.cos((45 * Math.PI) / 180);
const FLAT_COS = Math.cos((10 * Math.PI) / 180);
const FIND_MM = 0.2;
const THIN_MM = 0.5;
const MIN_SHARE = 0.15;
const KEEP_REACH_MM = 3;

/** A raised area drawn on the model, with its faces (the preview encodes them). */
export type MockArea = { verdict: "kept" | "warned"; faces: Uint32Array };

export type TableAnswer =
  | { ok: true; faces: Uint32Array; areas: MockArea[]; info: Omit<TablePreview, "version" | "triangles" | "faces"> }
  | { ok: false; detail: string; hint: string };

/** The table's plane in the scan's world: `n . x + d = 0`, n towards the parts. */
export type Plane = { n: [number, number, number]; d: number };

/** The demo's table plane (the scene's y = 0, y down: the parts lie at y < 0) moved by a pose (scene to world; `R` row-major). */
export function tablePlane(pose: { R: ArrayLike<number>; t: ArrayLike<number> }): Plane {
  const { R, t } = pose;
  const n: [number, number, number] = [-R[1], -R[4], -R[7]]; // R (0, -1, 0)
  return { n, d: -(n[0] * t[0] + n[1] * t[1] + n[2] * t[2]) };
}

const HINT = "if the part does stand on a table in this scan, select the table with the selection tools and delete it";

/** Faces joined through shared corners. */
function groupsOf(indices: Uint32Array, faces: number[]): number[][] {
  const parent = new Map<number, number>();
  const find = (x: number): number => {
    let r = x;
    while (parent.get(r) !== r) r = parent.get(r)!;
    parent.set(x, r);
    return r;
  };
  for (const f of faces) for (let k = 0; k < 3; k++) if (!parent.has(indices[f * 3 + k])) parent.set(indices[f * 3 + k], indices[f * 3 + k]);
  for (const f of faces) {
    const a = find(indices[f * 3]);
    for (let k = 1; k < 3; k++) {
      const b = find(indices[f * 3 + k]);
      if (a !== b) parent.set(b, a);
    }
  }
  const groups = new Map<number, number[]>();
  for (const f of faces) {
    const g = find(indices[f * 3]);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g)!.push(f);
  }
  return [...groups.values()];
}

/** A group of faces as a raised area: where, how large, how high; and whether a keep point lies on it. */
function describe(positions: Float32Array, indices: Uint32Array, fs: number[], h: (v: number) => number, keep: number[][]) {
  let area = 0;
  let hs = 0;
  let touched = false;
  const c = [0, 0, 0];
  const lo = [Infinity, Infinity, Infinity];
  const hi = [-Infinity, -Infinity, -Infinity];
  for (const f of fs) {
    const v = [indices[f * 3], indices[f * 3 + 1], indices[f * 3 + 2]];
    const p = v.map((i) => [positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]]);
    const u = [0, 1, 2].map((k) => p[1][k] - p[0][k]);
    const w = [0, 1, 2].map((k) => p[2][k] - p[0][k]);
    const A = Math.hypot(u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]) / 2;
    area += A;
    hs += (A * (h(v[0]) + h(v[1]) + h(v[2]))) / 3;
    const fc = [0, 1, 2].map((k) => (p[0][k] + p[1][k] + p[2][k]) / 3);
    for (const q of keep) if (Math.hypot(q[0] - fc[0], q[1] - fc[1], q[2] - fc[2]) <= KEEP_REACH_MM) touched = true;
    for (let k = 0; k < 3; k++) {
      c[k] += A * fc[k];
      for (const q of p) {
        lo[k] = Math.min(lo[k], q[k]);
        hi[k] = Math.max(hi[k], q[k]);
      }
    }
  }
  const ext = [0, 1, 2].map((k) => hi[k] - lo[k]).sort((a, b) => b - a);
  return { area, height: hs / Math.max(area, 1e-12), centre: [c[0] / area, c[1] / area, c[2] / area] as [number, number, number], size: ext[0], touched };
}

/** The faces of the mesh that are the table (module docstring); `keep`: "Keep this" points (mm). */
export function mockTable(positions: Float32Array, indices: Uint32Array, plane: Plane, margin = MOCK_MARGIN_MM, keep: number[][] = []): TableAnswer {
  const t0 = performance.now();
  const [nx, ny, nz] = plane.n;
  const F = indices.length / 3;
  const top = Math.max(margin, BAND_MM);
  const mark = new Uint8Array(F);
  const faceArea = new Float64Array(F);
  let area = 0;
  let tableArea = 0;
  const flat: number[] = [];
  const h = (v: number) => nx * positions[v * 3] + ny * positions[v * 3 + 1] + nz * positions[v * 3 + 2] + plane.d;
  for (let f = 0; f < F; f++) {
    const a = indices[f * 3],
      b = indices[f * 3 + 1],
      c = indices[f * 3 + 2];
    const ax = positions[a * 3],
      ay = positions[a * 3 + 1],
      az = positions[a * 3 + 2];
    const ux = positions[b * 3] - ax,
      uy = positions[b * 3 + 1] - ay,
      uz = positions[b * 3 + 2] - az;
    const vx = positions[c * 3] - ax,
      vy = positions[c * 3 + 1] - ay,
      vz = positions[c * 3 + 2] - az;
    const cx = uy * vz - uz * vy,
      cy = uz * vx - ux * vz,
      cz = ux * vy - uy * vx;
    const len = Math.hypot(cx, cy, cz);
    faceArea[f] = len / 2;
    area += len / 2;
    const ha = h(a),
      hb = h(b),
      hc = h(c);
    const centre = (ha + hb + hc) / 3;
    const cosn = len > 0 ? (cx * nx + cy * ny + cz * nz) / len : 0;
    const facing = cosn >= FACING_COS;
    if (facing && Math.abs(centre) <= BAND_MM) tableArea += len / 2;
    if (Math.max(ha, hb, hc) < -BAND_MM || (facing && centre <= top)) mark[f] = 1;
    if (cosn >= FLAT_COS && centre > FIND_MM && centre <= top) flat.push(f); // a raised area's top: judged below
  }
  const share = area > 0 ? tableArea / area : 0;
  if (share < MIN_SHARE)
    return {
      ok: false,
      detail: `No table found: the largest flat surface holds ${Math.round(share * 100)} % of the scan, and a table takes at least ${Math.round(MIN_SHARE * 100)} %. Nothing was removed; if the part does stand on a table here, select the table and delete it.`,
      hint: HINT,
    };
  const raised: TableRaised[] = [];
  const areas: MockArea[] = [];
  for (const fs of groupsOf(indices, flat)) {
    const r = describe(positions, indices, fs, h, keep);
    if (r.area < 25) continue;
    const thin = r.height >= THIN_MM;
    const kept = thin || r.touched;
    if (kept) for (const f of fs) mark[f] = 0;
    raised.push({
      centre: r.centre,
      size_mm: r.size,
      area_mm2: r.area,
      height_mm: r.height,
      scatter_mm: 0.1,
      verdict: kept ? "kept" : "warned",
      kept,
      by_user: !thin && r.touched,
      n_faces: fs.length,
    });
    areas.push({ verdict: kept ? "kept" : "warned", faces: Uint32Array.from(fs).sort() });
  }
  let n = 0;
  let removed = 0;
  let lowest = Infinity;
  for (let f = 0; f < F; f++) {
    if (mark[f]) {
      n++;
      removed += faceArea[f];
    } else for (let k = 0; k < 3; k++) lowest = Math.min(lowest, h(indices[f * 3 + k]));
  }
  const faces = new Uint32Array(n);
  for (let f = 0, k = 0; f < F; f++) if (mark[f]) faces[k++] = f;
  const kept = Number.isFinite(lowest) ? lowest : null;
  const low = kept === null ? "" : kept <= BAND_MM ? "; the part is kept right down to it" : `; the part is kept from ${kept.toFixed(2)} mm above it`;
  const at = (r: TableRaised) => `at (${r.centre.map((x) => x.toFixed(0)).join(", ")}) mm`;
  const warnings = raised.map((r) =>
    r.kept
      ? `A thin part lying flat is ${r.by_user ? "kept, as you chose" : "kept"}: ${r.size_mm.toFixed(0)} mm across, ${r.height_mm.toFixed(2)} mm above the table, ${at(r)}. It is a piece of its own now: Remove floaters may take it if it is small.`
      : `A raised area ${r.size_mm.toFixed(0)} mm across, ${r.height_mm.toFixed(2)} mm above the table round it, ${at(r)}, goes with the table: it may be a thin part. To keep it, choose Keep this, or select it by hand.`,
  );
  return {
    ok: true,
    faces,
    areas,
    info: {
      n_faces: n,
      area_mm2: removed,
      area_left_mm2: area - removed,
      normal: [nx, ny, nz],
      d: plane.d,
      share,
      front_share: area > 0 ? (area - removed) / area : 0,
      behind_share: 0,
      flatness_mm: 0.1,
      band_mm: BAND_MM,
      margin_mm: margin,
      facing_deg: 45,
      lowest_mm: kept,
      loose_removed: 0,
      loose_left: 0,
      loose_left_mm2: 0,
      skipped: [],
      raised,
      thin_limit_mm: FIND_MM,
      thin_keep_mm: THIN_MM,
      limit: `On this table a part lying flat that is thinner than ${FIND_MM.toFixed(2)} mm cannot be told from it. From ${THIN_MM.toFixed(2)} mm it stays; between the two it goes with the table, drawn and listed, unless you keep it.`,
      warnings,
      rule:
        "The demo knows where its table is. A face is removed when it faces the table's way (within 45° of its normal) and lies at most " +
        `${margin} mm above it, or lies wholly behind it. Faces turned further away (the part's walls) stay at any height, and so do flat ` +
        `areas ${THIN_MM} mm or more above the table (a thin part lying flat); flat areas between ${FIND_MM} and ${THIN_MM} mm go, drawn and listed.`,
      summary: `Found the table (${Math.round(share * 100)} % of the scan, flat to 0.10 mm RMS): ${n.toLocaleString("en-US")} faces (${(removed / 100).toFixed(1)} cm²) are the table${low}`,
      ms: Math.round(performance.now() - t0),
    },
  };
}
