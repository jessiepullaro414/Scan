/**
 * Checks for the demo engine's fitted shapes (`npm test`) on synthetic meshes with a known truth: a
 * plane next to a step, a cylinder, a ball, an annulus (a plate with a round hole), and the API's
 * errors. Plain TypeScript that Node runs as it is (type stripping), no test framework: a failed
 * check throws.
 */
import type { CircleShape, CylinderShape, PlaneShape } from "../types";
import { eigSym3, mockFit, type FitMesh } from "./fit.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`fit.test: ${what}`);
}

/** A seeded uniform random number in [-1, 1). */
function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return (s / 2 ** 32) * 2 - 1;
  };
}

function normals(P: Float32Array, I: Uint32Array): Float32Array {
  const N = new Float32Array(P.length);
  for (let t = 0; t < I.length; t += 3) {
    const a = I[t] * 3,
      b = I[t + 1] * 3,
      c = I[t + 2] * 3;
    const ux = P[b] - P[a],
      uy = P[b + 1] - P[a + 1],
      uz = P[b + 2] - P[a + 2];
    const vx = P[c] - P[a],
      vy = P[c + 1] - P[a + 1],
      vz = P[c + 2] - P[a + 2];
    const n = [uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx];
    for (const k of [a, b, c]) for (let j = 0; j < 3; j++) N[k + j] += n[j];
  }
  for (let i = 0; i < N.length; i += 3) {
    const l = Math.hypot(N[i], N[i + 1], N[i + 2]) || 1;
    N[i] /= l;
    N[i + 1] /= l;
    N[i + 2] /= l;
  }
  return N;
}

/** A grid of (nu + 1) x (nv + 1) vertices placed by `at(i, j)`, two triangles per cell; `wrapU` closes it round. */
function gridMesh(nu: number, nv: number, at: (i: number, j: number) => number[], wrapU = false): FitMesh {
  const cols = wrapU ? nu : nu + 1;
  const P: number[] = [];
  for (let j = 0; j <= nv; j++) for (let i = 0; i < cols; i++) P.push(...at(i, j));
  const I: number[] = [];
  for (let j = 0; j < nv; j++)
    for (let i = 0; i < nu; i++) {
      const a = j * cols + i,
        b = j * cols + ((i + 1) % cols),
        c = a + cols,
        d = b + cols;
      I.push(a, b, d, a, d, c);
    }
  const positions = Float32Array.from(P);
  const indices = Uint32Array.from(I);
  return { positions, indices, normals: normals(positions, indices) };
}
const faceNear = (m: FitMesh, p: number[]) => {
  let best = 0,
    bd = Infinity;
  for (let f = 0; f < m.indices.length / 3; f++) {
    let d = 0;
    for (let k = 0; k < 3; k++) {
      const v = m.indices[f * 3 + k] * 3;
      d += Math.hypot(m.positions[v] - p[0], m.positions[v + 1] - p[1], m.positions[v + 2] - p[2]);
    }
    if (d < bd) {
      bd = d;
      best = f;
    }
  }
  return best;
};
const noHoles = () => [];

// ------------------------------------------------------------------ eigen solver
{
  const e = eigSym3([
    [2, 1, 0],
    [1, 2, 0],
    [0, 0, 5],
  ]);
  check(Math.abs(e.values[0] - 1) < 1e-12 && Math.abs(e.values[1] - 3) < 1e-12 && Math.abs(e.values[2] - 5) < 1e-12, "eigenvalues 1, 3, 5");
  check(Math.abs(Math.abs(e.vectors[0][0]) - Math.SQRT1_2) < 1e-9 && Math.abs(e.vectors[0][2]) < 1e-9, "eigenvector of 1 is (1, -1, 0)/√2");
}

// ------------------------------------------------------------------ plane next to a step
{
  // 80 x 60 mm at 1 mm, 0.02 mm noise; for x > 50 the surface steps up 5 mm (another face)
  const rand = rng(7);
  const m = gridMesh(80, 60, (i, j) => [i, j, (i > 50 ? 5 : 0) + 0.02 * rand()]);
  const r = mockFit(m, { kind: "plane", face: faceNear(m, [20, 30, 0]), point: [20, 30, 0] }, noHoles);
  check(r.ok, "plane: fitted");
  if (r.ok) {
    const s = r.shape as PlaneShape;
    check(s.kind === "plane" && r.kind === "plane", "plane: kind and shape");
    check(Math.abs(s.normal[2]) > 0.99999, "plane: normal along z");
    check(s.point[0] < 30 && s.n > 2500 && s.n < 51 * 61 + 61, "plane: the low face only, not the step");
    check(s.flatness_mm < 0.05 && s.rms_mm < 0.02, "plane: flat within the noise");
    check(s.offset_sigma_mm > 0 && s.offset_sigma_mm < 0.001 && s.normal_sigma_deg > 0 && s.normal_sigma_deg < 0.01, "plane: small ± for 3000 points");
    check(r.faces.every((f, i) => i === 0 || f > r.faces[i - 1]), "plane: faces sorted, no duplicates");
    check(r.tolerance !== null && r.tolerance >= 0.05 && r.tolerance <= 1 && r.warnings.length === 0, "plane: tolerance chosen, no warnings");
  }
  const t = mockFit(m, { kind: "plane", face: faceNear(m, [20, 30, 0]), point: [20, 30, 0], tolerance_mm: 0.5 }, noHoles);
  check(t.ok && t.tolerance === 0.5, "plane: a given tolerance is used");
}

// ------------------------------------------------------------------ cylinder, flat, ball
{
  // r = 7 mm, 30 mm long, along z, 0.01 mm noise
  const rand = rng(11);
  const R = 7;
  const m = gridMesh(
    72,
    30,
    (i, j) => {
      const a = (i / 72) * 2 * Math.PI;
      const r = R + 0.01 * rand();
      return [10 + r * Math.cos(a), -4 + r * Math.sin(a), j];
    },
    true,
  );
  const p = [10 + R, -4, 15];
  const r = mockFit(m, { kind: "cylinder", face: faceNear(m, p), point: p }, noHoles);
  check(r.ok, `cylinder: fitted${r.ok ? "" : ` (${r.detail})`}`);
  if (r.ok) {
    const s = r.shape as CylinderShape;
    check(Math.abs(s.diameter_mm - 14) < 0.02, `cylinder: Ø 14 (got ${s.diameter_mm})`);
    check(Math.abs(Math.abs(s.axis[2]) - 1) < 1e-4, "cylinder: axis along z");
    check(Math.hypot(s.point[0] - 10, s.point[1] + 4) < 0.02, "cylinder: axis through (10, -4)");
    check(s.diameter_sigma_mm === 2 * s.radius_sigma_mm && s.radius_sigma_mm > 0 && s.radius_sigma_mm < 0.01, "cylinder: ±");
  }
  const flat = gridMesh(30, 30, (i, j) => [i, j, 0]);
  const f = mockFit(flat, { kind: "cylinder", face: faceNear(flat, [15, 15, 0]), point: [15, 15, 0] }, noHoles);
  check(!f.ok && /flat/.test(f.detail) && /Plane/.test(f.hint), "cylinder on a flat face: refused, the hint says Plane");
  const ball = gridMesh(90, 45, (i, j) => {
    const th = (i / 90) * 2 * Math.PI,
      ph = 0.2 + (j / 45) * (Math.PI - 0.4);
    return [40 * Math.sin(ph) * Math.cos(th), 40 * Math.sin(ph) * Math.sin(th), 40 * Math.cos(ph)];
  }, true);
  const bp = [40, 0, 0];
  const b = mockFit(ball, { kind: "cylinder", face: faceNear(ball, bp), point: bp }, noHoles);
  check(!b.ok && /both ways/.test(b.detail), "cylinder on a ball: refused");
}

// ------------------------------------------------------------------ a plate with a round hole
{
  // an annulus around (5, 5, 2) from r = 6 to r = 30 in the plane z = 2: its inner ring is the hole's edge
  const m = gridMesh(
    96,
    24,
    (i, j) => {
      const a = (i / 96) * 2 * Math.PI,
        r = 6 + j;
      return [5 + r * Math.cos(a), 5 + r * Math.sin(a), 2];
    },
    true,
  );
  const inner = Uint32Array.from({ length: 96 }, (_, i) => i);
  const p = [5 + 8, 5, 2];
  const r = mockFit(m, { kind: "hole", face: faceNear(m, p), point: p }, () => [inner]);
  check(r.ok, `hole: fitted${r.ok ? "" : ` (${r.detail})`}`);
  if (r.ok) {
    const s = r.shape as CircleShape;
    check(r.kind === "hole" && s.kind === "circle", "hole: kind hole, shape circle");
    check(Math.abs(s.diameter_mm - 12) < 0.02 && Math.hypot(s.center[0] - 5, s.center[1] - 5, s.center[2] - 2) < 0.01, "hole: Ø 12 at (5, 5, 2)");
    check(Math.abs(s.normal[2]) > 0.9999 && s.n === 96, "hole: normal along z, 96 edge points");
    check(r.faces.length > 0 && r.faces.length <= 96 * 2, "hole: the faces around the edge");
    check(r.tolerance === null, "hole: no tolerance");
  }
  const far = [5 + 28, 5, 2];
  const n = mockFit(m, { kind: "hole", face: faceNear(m, far), point: far }, () => [inner]);
  check(!n.ok && /No hole edge within 10 mm .*nearest edge is 22 mm.*use Cylinder/.test(n.detail), "hole: too far from an edge is refused, the detail says how far and points to Cylinder");
}

// ------------------------------------------------------------------ errors
{
  const m = gridMesh(4, 4, (i, j) => [i, j, 0]);
  const k = mockFit(m, { kind: "sphere", face: 0, point: [0, 0, 0] }, noHoles);
  check(!k.ok && /Unknown kind/.test(k.detail), "unknown kind");
  const f = mockFit(m, { kind: "plane", face: 32, point: [0, 0, 0] }, noHoles);
  check(!f.ok && /not in the mesh/.test(f.detail), "face outside the mesh");
  const p = mockFit(m, { kind: "plane", face: 0, point: [0, 0] }, noHoles);
  check(!p.ok && /three numbers/.test(p.detail), "a point of two numbers");
  const t = mockFit(m, { kind: "plane", face: 0, point: [0, 0, 0], tolerance_mm: -1 }, noHoles);
  check(!t.ok && /tolerance/.test(t.detail), "a negative tolerance");
}

console.log(`fit.test: ${checks} checks passed`);
