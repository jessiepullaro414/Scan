/**
 * The Check panel (APP_API.md "M5 third slice: areas that need another pass"), the pure part: the
 * tolerance the user types, the answer in words, the sizes and shares as text, and the direction to
 * look at an area from.
 *
 * Imports nothing at run time, so `npm test` checks it with plain node.
 */
import type { CheckKind, CheckResult } from "../api/types";

/** What the panel starts with: a common tolerance for a part that has to fit (mm). */
export const DEFAULT_TOLERANCE_MM = 0.3;
/** The field's range (the API takes anything above 0, up to 100 mm). */
export const TOLERANCE_MIN_MM = 0.01;
export const TOLERANCE_MAX_MM = 100;

/** A tolerance the API takes: within the range, to a thousandth of a mm; the default for anything else. */
export function validTolerance(x: unknown): number {
  if (typeof x !== "number" || !Number.isFinite(x) || x <= 0) return DEFAULT_TOLERANCE_MM;
  return Math.round(Math.min(TOLERANCE_MAX_MM, Math.max(TOLERANCE_MIN_MM, x)) * 1000) / 1000;
}

/** "0.3", "0.25", "1": as short as it is exact (to a thousandth). */
export function fmtTolerance(mm: number): string {
  return String(Math.round(mm * 1000) / 1000);
}

/** A share of the surface: "92 %", "99.6 %", "0.4 %", "2 %"; never "100 %" or "0 %" for a share that isn't. */
export function fmtShare(x: number | null | undefined): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return "–";
  if (x <= 0) return "0 %";
  if (x >= 1) return "100 %";
  if (x < 0.001) return "under 0.1 %";
  if (x > 0.999) return "over 99.9 %";
  return x < 0.1 || x > 0.99 ? `${(x * 100).toFixed(1).replace(/.0$/, "")} %` : `${Math.round(x * 100)} %`;
}

/** An area: "3.2 mm²", "42 mm²", "1.4 cm²", "125 cm²". */
export function fmtAreaSize(mm2: number): string {
  if (mm2 < 100) return `${mm2 < 10 ? mm2.toFixed(1) : Math.round(mm2)} mm²`;
  const cm2 = mm2 / 100;
  return `${cm2 < 10 ? cm2.toFixed(1) : Math.round(cm2)} cm²`;
}

/** How far an area reaches: "4.5 mm", "23 mm". */
export function fmtExtent(mm: number): string {
  return `${mm < 10 ? mm.toFixed(1) : Math.round(mm)} mm`;
}

export function kindName(kind: CheckKind): string {
  return kind === "not_measured" ? "Not measured" : "Uncertain";
}

/** What a kind means, for the user: one sentence. `k` is the engine's factor (2: the 2σ band). */
export function kindMeaning(kind: CheckKind, toleranceMm: number, k = 2): string {
  const band = `${fmtTolerance(k)}σ`;
  return kind === "not_measured"
    ? "Hole filling made this surface up: nothing was measured here."
    : `The scan's own ${band} here is larger than ± ${fmtTolerance(toleranceMm)} mm.`;
}

/**
 * The share of the surface that misses the tolerance outside the listed areas: spots under the minimum
 * size and, when there are more areas than the list holds, those. Null without an answer to count from.
 */
export function unlistedShare(r: CheckResult): number | null {
  if (r.share_ok === null) return null;
  let listed = 0;
  for (const a of r.areas) listed += a.share;
  const rest = 1 - r.share_ok - listed;
  return rest > 1e-9 ? rest : 0;
}

/** Whether the answer says anything: the mesh has sigmas, and some of its surface was measured. */
export function hasAnswer(r: CheckResult): boolean {
  return r.has_sigma && r.share_ok !== null && !((r.share_not_measured ?? 0) > 0.9995);
}

/** The answer in one sentence: "92 % of the surface meets ± 0.1 mm; 3 areas need another pass." */
export function checkSentence(r: CheckResult): string {
  const tol = `± ${fmtTolerance(r.tolerance_mm)} mm`;
  const ok = r.share_ok ?? 0;
  const n = r.n_areas;
  if (n === 0) {
    if (ok >= 1) return `All of the surface meets ${tol}; no area needs another pass.`;
    return `${fmtShare(ok)} of the surface meets ${tol}; no area is large enough to list.`;
  }
  const lead = ok <= 0 ? "None" : fmtShare(ok);
  return `${lead} of the surface meets ${tol}; ${n === 1 ? "1 area needs" : `${n} areas need`} another pass.`;
}

/** What the sentence leaves out, each in a sentence: how the rest splits, the list's cut, the small spots. */
export function checkNotes(r: CheckResult): string[] {
  const out: string[] = [];
  const unc = r.share_uncertain ?? 0;
  const nm = r.share_not_measured ?? 0;
  if (unc > 0 && nm > 0) out.push(`Of the rest, ${fmtShare(unc)} of the surface is uncertain and ${fmtShare(nm)} was not measured (filled holes).`);
  else if (nm > 0) out.push("All of the rest was not measured (filled holes).");
  const cut = r.n_areas > r.areas.length;
  if (cut) out.push(`The ${r.areas.length} largest of ${r.n_areas} areas are listed.`);
  const rest = unlistedShare(r);
  if (rest !== null && rest >= 0.00005) {
    const spots = `spots under ${fmtAreaSize(r.min_area_mm2)}`;
    const share = fmtShare(rest);
    const lead = share.charAt(0).toUpperCase() + share.slice(1); // "Under 0.1 %"
    out.push(
      cut
        ? `${lead} of the surface also misses it, in the areas not listed and in ${spots}.`
        : `${lead} of the surface also misses it, in ${spots}: too small to list, but counted.`,
    );
  }
  return out;
}

/**
 * The direction an area faces, to look at it from: the mean of its corners' normals, weighted by each
 * triangle's area (unit length). Null when it faces every way (a closed or strongly curved area) or
 * has no surface: then the view keeps its direction. One pass over the area's triangles.
 */
export function meanNormal(tris: ArrayLike<number>, positions: ArrayLike<number>, normals: ArrayLike<number> | null): [number, number, number] | null {
  let nx = 0,
    ny = 0,
    nz = 0,
    total = 0;
  for (let t = 0; t + 2 < tris.length; t += 3) {
    const a = tris[t] * 3,
      b = tris[t + 1] * 3,
      c = tris[t + 2] * 3;
    const ux = positions[b] - positions[a],
      uy = positions[b + 1] - positions[a + 1],
      uz = positions[b + 2] - positions[a + 2];
    const vx = positions[c] - positions[a],
      vy = positions[c + 1] - positions[a + 1],
      vz = positions[c + 2] - positions[a + 2];
    const cx = uy * vz - uz * vy,
      cy = uz * vx - ux * vz,
      cz = ux * vy - uy * vx;
    const w = Math.hypot(cx, cy, cz) / 2;
    if (!(w > 0)) continue;
    total += w;
    if (normals) {
      nx += (w * (normals[a] + normals[b] + normals[c])) / 3;
      ny += (w * (normals[a + 1] + normals[b + 1] + normals[c + 1])) / 3;
      nz += (w * (normals[a + 2] + normals[b + 2] + normals[c + 2])) / 3;
    } else {
      nx += cx / 2;
      ny += cy / 2;
      nz += cz / 2;
    }
  }
  const l = Math.hypot(nx, ny, nz);
  if (!(total > 0) || !(l > 0.3 * total)) return null;
  return [nx / l, ny / l, nz / l];
}

/** The least a view direction keeps of the side the scan was made from (the cosine: about 15° above its horizon). */
const MIN_SCAN_SIDE = 0.25;

/**
 * Where the camera goes to look at an area: along its normal, in the 3D view's axes (the mesh's frame is
 * OpenCV's, the view's is y up: y and z change sign).
 *
 * `scanSide`: the direction the whole mesh faces (its own mean normal), for an open scan such as a part
 * on a table or a panel; null for one scanned all round. An area that faces away from that side (the
 * foot of a ball on the table faces down) is looked at from the scan's side at a flat angle instead:
 * along its normal the camera would end up behind the scanned surface.
 *
 * A direction that points nearly straight up or down is leaned a little to the front, so the view
 * never sits on the pole, where turning it is awkward.
 */
export function viewDirection(normal: ArrayLike<number>, scanSide: ArrayLike<number> | null = null, lean: [number, number] = [0.42, 1]): [number, number, number] {
  const l = Math.hypot(normal[0], normal[1], normal[2]) || 1;
  let n = [normal[0] / l, normal[1] / l, normal[2] / l];
  if (scanSide) {
    const sl = Math.hypot(scanSide[0], scanSide[1], scanSide[2]);
    const s = sl > 0 ? [scanSide[0] / sl, scanSide[1] / sl, scanSide[2] / sl] : null;
    const d = s ? n[0] * s[0] + n[1] * s[1] + n[2] * s[2] : 1;
    if (s && d < MIN_SCAN_SIDE) {
      // the normal's part across the scan side, turned up to the least share of that side
      const across = [n[0] - d * s[0], n[1] - d * s[1], n[2] - d * s[2]];
      const al = Math.hypot(across[0], across[1], across[2]);
      if (al < 1e-6) n = s; // it faces straight away: from the scan's side
      else {
        const k = Math.sqrt(1 - MIN_SCAN_SIDE * MIN_SCAN_SIDE) / al;
        n = [MIN_SCAN_SIDE * s[0] + k * across[0], MIN_SCAN_SIDE * s[1] + k * across[1], MIN_SCAN_SIDE * s[2] + k * across[2]];
      }
    }
  }
  let x = n[0],
    y = -n[1],
    z = -n[2];
  if (Math.abs(y) > 0.94) {
    const h = Math.hypot(lean[0], lean[1]) || 1;
    x += (0.35 * lean[0]) / h;
    z += (0.35 * lean[1]) / h;
    const m = Math.hypot(x, y, z);
    x /= m;
    y /= m;
    z /= m;
  }
  return [x, y, z];
}
