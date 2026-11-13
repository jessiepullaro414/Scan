/**
 * Checks for the demo engine's laser comparison (`npm test`): the made-up lines on a plate, the patch
 * where the lasers and the mesh differ, the numbers counted from the array, and a scan without laser
 * points. Plain TypeScript that Node runs as it is (type stripping), no test framework: a failed check
 * throws.
 */
import { laserStats, MOCK_GATE_MM, MOCK_LASER_POINTS, MOCK_STRAY_POINTS, mockLasers, mockLaserValues } from "./lasers.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`lasers.test (mock): ${what}`);
}
const near = (a: number | null, b: number, tol = 1e-6) => a !== null && Math.abs(a - b) <= tol;

/** A flat plate of nx x ny vertices, `step` mm apart, two triangles per cell (z = 0). */
function grid(nx: number, ny: number, step: number): { P: Float32Array; I: Uint32Array } {
  const P = new Float32Array(nx * ny * 3);
  for (let j = 0; j < ny; j++)
    for (let i = 0; i < nx; i++) {
      P[(j * nx + i) * 3] = i * step;
      P[(j * nx + i) * 3 + 1] = j * step;
    }
  const I = new Uint32Array((nx - 1) * (ny - 1) * 6);
  let o = 0;
  for (let j = 0; j < ny - 1; j++)
    for (let i = 0; i < nx - 1; i++) {
      const a = j * nx + i,
        b = a + 1,
        c = a + nx,
        d = c + 1;
      I.set([a, b, d, a, d, c], o);
      o += 6;
    }
  return { P, I };
}

// the lines on a plate of 120 x 90 mm at 1 mm
{
  const nx = 121,
    ny = 91;
  const { P, I } = grid(nx, ny, 1);
  const v = mockLaserValues(P, I);
  check(v.length === nx * ny, "one value per vertex");
  const at = (i: number, j: number) => v[j * nx + i];
  // the first set: a line every 12 mm along x, a vertex wide here
  check(!Number.isNaN(at(0, 40)) && !Number.isNaN(at(12, 40)) && !Number.isNaN(at(72, 40)), "a line every 12 mm along x");
  let on = 0;
  for (let i = 0; i < v.length; i++) if (!Number.isNaN(v[i])) on++;
  const share = on / v.length;
  check(share > 0.05 && share < 0.3, `the lines cover a part of the plate, not most of it (${(share * 100).toFixed(1)} %)`);
  check(Number.isNaN(at(3, 3)) && Number.isNaN(at(7, 8)), "no value between the lines");
  let max = 0;
  for (let i = 0; i < v.length; i++) if (!Number.isNaN(v[i])) max = Math.max(max, Math.abs(v[i]));
  check(max < 0.2, `small differences without a patch (largest ${max.toFixed(3)} mm)`);
  let pos = 0,
    neg = 0;
  for (let i = 0; i < v.length; i++) if (v[i] > 0.01) pos++;
    else if (v[i] < -0.01) neg++;
  check(pos > 50 && neg > 50, "both signs show");
  // the same every time
  check(mockLaserValues(P, I).join() === v.join(), "the same values on the same mesh");

  // a patch where the lasers read 0.4 mm further out
  const w = mockLaserValues(P, I, [
    { c: [60, 44, 0], r: 13, mm: 0.5 },
    { c: [12, 10, 0], r: 6, mm: -0.3 },
  ]);
  check(Math.abs(w[44 * nx + 60] - v[44 * nx + 60] - 0.5) < 1e-6, "0.5 mm more at the first patch's centre");
  check(w[44 * nx + 60] > 0.3, "that centre is beyond 0.3 mm");
  check(Math.abs(w[10 * nx + 12] - v[10 * nx + 12] + 0.3) < 1e-6, "0.3 mm less at the second patch's centre");
  check(w[80 * nx + 0] === v[80 * nx + 0] && Number.isNaN(w[3 * nx + 3]), "nothing changes outside the patches, and no new values");
  let far = 0;
  for (let i = 0; i < w.length; i++) if (Math.abs(w[i]) > 0.3) far++;
  check(far > 3 && far < on * 0.2, `a patch, not the whole plate (${far} of ${on} vertices beyond 0.3 mm)`);

  // a coarser mesh still gets its lines (they are as wide as its edges need)
  const coarse = grid(41, 31, 3);
  const c = mockLaserValues(coarse.P, coarse.I);
  let con = 0;
  for (let i = 0; i < c.length; i++) if (!Number.isNaN(c[i])) con++;
  check(con > c.length * 0.1, "lines on a 3 mm mesh too");
  check(!Number.isNaN(c[15 * 41 + 4]) && !Number.isNaN(c[15 * 41 + 8]), "its lines along x are whole: every vertex on x = 12 and 24 mm");
}

// the numbers, counted from an array
{
  const { P, I } = grid(3, 3, 10); // 9 vertices, 8 triangles of 50 mm^2
  const v = new Float32Array([0.05, NaN, NaN, -0.2, 0.4, NaN, NaN, NaN, NaN]);
  const s = laserStats(v, P, I);
  check(s.n === 3, "three vertices with a value");
  check(near(s.mean_mm, (0.05 - 0.2 + 0.4) / 3), "the mean keeps the sign");
  check(near(s.rms_mm, Math.sqrt((0.05 ** 2 + 0.2 ** 2 + 0.4 ** 2) / 3)), "rms");
  check(near(s.p50_mm, 0.2) && near(s.max_mm, 0.4) && near(s.p95_mm, 0.4), "median, 95th percentile and largest, without the sign");
  check(near(s.within_0_1, 1 / 3) && near(s.within_0_3, 2 / 3), "the shares within 0.1 and 0.3 mm are of the vertices with a value");
  // by area: vertex 0 is a corner of 2 triangles, vertex 3 of 3, vertex 4 (the middle) of 6; 24 corners in all
  check(near(s.covered, (2 + 3 + 6) / 24), `the covered share is by area (${s.covered})`);
  const all = laserStats(new Float32Array(9).fill(0.01), P, I);
  check(near(all.covered, 1) && all.within_0_1 === 1 && all.n === 9, "a value everywhere: all covered");
  const none = laserStats(new Float32Array(9).fill(NaN), P, I);
  check(none.n === 0 && none.covered === null && none.mean_mm === null && none.rms_mm === null && none.p50_mm === null && none.p95_mm === null && none.max_mm === null, "no value: the numbers are null");
  check(none.within_0_1 === null && none.within_0_3 === null, "no value: the shares are null");
}

// the whole answer
{
  const { P, I } = grid(61, 46, 1);
  const r = mockLasers(5, P, I, true, [{ c: [24, 20, 0], r: 8, mm: 0.5 }]);
  check(r.info.version === 5 && r.info.vertices === 61 * 46 && r.values.length === r.info.vertices, "the version and one value per vertex");
  check(r.info.available && r.info.gate_mm === MOCK_GATE_MM, "available, with the gate");
  const p = r.info.points;
  check(p.total === MOCK_LASER_POINTS && p.stray === MOCK_STRAY_POINTS && p.used + p.stray === p.on_surface && p.on_surface < p.total, "the points add up");
  const s = r.info.stats;
  check(s.n > 0 && s.covered !== null && s.covered > 0.05 && s.covered < 0.3, "lines over a part of the surface");
  check(s.max_mm !== null && s.max_mm > 0.3 && s.within_0_3 !== null && s.within_0_3 < 1 && s.within_0_1 !== null && s.within_0_1 > 0.5 && s.within_0_1 < 1, "most agree, the patch does not");
  check(s.p50_mm !== null && s.p50_mm > 0.01 && s.p50_mm < 0.1, `a small typical difference (${s.p50_mm})`);

  // a scan without laser points
  const off = mockLasers(2, P, I, false);
  check(!off.info.available && off.info.version === 2 && off.values.length === 61 * 46, "not available: still one value per vertex");
  check(off.values.every((x) => Number.isNaN(x)), "not available: no value anywhere");
  check(off.info.stats.n === 0 && off.info.stats.covered === null, "not available: no numbers");
  check(off.info.points.total === 0 && off.info.points.used === 0 && off.info.points.stray === 0, "not available: no points");
}

console.log(`lasers.test (mock): ${checks} checks passed`);
