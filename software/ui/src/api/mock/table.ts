/**
 * The demo engine's "Remove the table" (FEATURE_PLAN X1). The engine finds the plane a part stands
 * on from the scan itself and grows the table over the faces joined to it (ohs/mesh/support.py);
 * the demo knows where its table is (the scene's y = 0, moved into the scan's world by `T_WS`), so
 * its rule is simpler: a face is the table's when it faces the table's way (its normal within 45°
 * of the table's) and its centre lies at most the margin above the plane (never less than the
 * 0.3 mm noise band), or when all its corners lie more than the band behind the plane. No faces
 * are grown or joined and no loose crumbs are looked for. When less than 15 % of the surface is
 * the table (the table removed already, or cut away by hand) it refuses, in the engine's words.
 */
import type { TablePreview } from "../types";

export const MOCK_MARGIN_MM = 1;
const BAND_MM = 0.3;
const FACING_COS = Math.cos((45 * Math.PI) / 180);
const MIN_SHARE = 0.15;

export type TableAnswer =
  | { ok: true; faces: Uint32Array; info: Omit<TablePreview, "version" | "triangles" | "faces"> }
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

/** The faces of the mesh that are the table (module docstring). */
export function mockTable(positions: Float32Array, indices: Uint32Array, plane: Plane, margin = MOCK_MARGIN_MM): TableAnswer {
  const t0 = performance.now();
  const [nx, ny, nz] = plane.n;
  const F = indices.length / 3;
  const top = Math.max(margin, BAND_MM);
  const mark = new Uint8Array(F);
  let area = 0;
  let removed = 0;
  let tableArea = 0;
  let lowest = Infinity;
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
    const A = len / 2;
    area += A;
    const ha = h(a),
      hb = h(b),
      hc = h(c);
    const centre = (ha + hb + hc) / 3;
    const facing = len > 0 && (cx * nx + cy * ny + cz * nz) / len >= FACING_COS;
    if (facing && Math.abs(centre) <= BAND_MM) tableArea += A;
    if (Math.max(ha, hb, hc) < -BAND_MM || (facing && centre <= top)) {
      mark[f] = 1;
      removed += A;
    } else lowest = Math.min(lowest, ha, hb, hc);
  }
  const share = area > 0 ? tableArea / area : 0;
  if (share < MIN_SHARE)
    return {
      ok: false,
      detail: `No table found: the largest flat surface holds ${Math.round(share * 100)} % of the scan, and a table takes at least ${Math.round(MIN_SHARE * 100)} %. Nothing was removed; if the part does stand on a table here, select the table and delete it.`,
      hint: HINT,
    };
  let n = 0;
  for (let f = 0; f < F; f++) n += mark[f];
  const faces = new Uint32Array(n);
  for (let f = 0, k = 0; f < F; f++) if (mark[f]) faces[k++] = f;
  const kept = Number.isFinite(lowest) ? lowest : null;
  const low = kept === null ? "" : kept <= BAND_MM ? "; the part is kept right down to it" : `; the part is kept from ${kept.toFixed(2)} mm above it`;
  return {
    ok: true,
    faces,
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
      warnings: [],
      rule:
        "The demo knows where its table is. A face is removed when it faces the table's way (within 45° of its normal) and lies at most " +
        `${margin} mm above it, or lies wholly behind it. Faces turned further away (the part's walls) stay at any height.`,
      summary: `Found the table (${Math.round(share * 100)} % of the scan, flat to 0.10 mm RMS): ${n.toLocaleString("en-US")} faces (${(removed / 100).toFixed(1)} cm²) are the table${low}`,
      ms: Math.round(performance.now() - t0),
    },
  };
}
