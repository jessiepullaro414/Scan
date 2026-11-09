/**
 * Coordinates. The engine's world frame is the camera frame of the first tracked frame,
 * OpenCV axes (x right, y down, z forward), in mm. three.js is y-up: everything in the world
 * frame goes under a group rotated 180° about X (x, y, z) → (x, −y, −z), so poses and meshes
 * are used unchanged inside it (see ARCHITECTURE.md §2 and APP_API.md "Coordinates").
 */
import { Box3, Matrix4, Sphere, Vector3 } from "three";

/** Euler rotation of the world-frame group: 180° about X. */
export const CV_TO_THREE_ROTATION: [number, number, number] = [Math.PI, 0, 0];

/** A world-frame (OpenCV) point in three.js world coordinates. */
export function cvToThree(v: Vector3, out = new Vector3()): Vector3 {
  return out.set(v.x, -v.y, -v.z);
}

export function threeToCv(v: Vector3, out = new Vector3()): Vector3 {
  return out.set(v.x, -v.y, -v.z);
}

/** `T_wc` (16 numbers, row-major) into a Matrix4. */
export function matrixFromRowMajor(T: ArrayLike<number>, out = new Matrix4()): Matrix4 {
  return out.set(T[0], T[1], T[2], T[3], T[4], T[5], T[6], T[7], T[8], T[9], T[10], T[11], T[12], T[13], T[14], T[15]);
}

/** Axis-aligned bounds of the first `count` points of an xyz array. */
export function boundsOf(pos: ArrayLike<number>, count: number): { box: Box3; sphere: Sphere } | null {
  if (count <= 0) return null;
  let minX = Infinity,
    minY = Infinity,
    minZ = Infinity,
    maxX = -Infinity,
    maxY = -Infinity,
    maxZ = -Infinity;
  for (let i = 0, n = count * 3; i < n; i += 3) {
    const x = pos[i],
      y = pos[i + 1],
      z = pos[i + 2];
    if (!(x === x && y === y && z === z)) continue; // skip NaN
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (z < minZ) minZ = z;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
    if (z > maxZ) maxZ = z;
  }
  if (minX === Infinity) return null;
  const box = new Box3(new Vector3(minX, minY, minZ), new Vector3(maxX, maxY, maxZ));
  const center = box.getCenter(new Vector3());
  // Tight radius: the farthest point from the box centre.
  let r2 = 0;
  for (let i = 0, n = count * 3; i < n; i += 3) {
    const dx = pos[i] - center.x,
      dy = pos[i + 1] - center.y,
      dz = pos[i + 2] - center.z;
    const d = dx * dx + dy * dy + dz * dz;
    if (d > r2) r2 = d;
  }
  return { box, sphere: new Sphere(center, Math.sqrt(r2)) };
}

/** A world-frame sphere in three.js coordinates. */
export function sphereToThree(s: Sphere): Sphere {
  return new Sphere(cvToThree(s.center), s.radius);
}

/** Nominal scanner optics (ARCHITECTURE.md §7): fx ≈ 720 at 1280×800 → tan of the half angles. */
export const FRUSTUM_TAN = { x: 640 / 720, y: 400 / 720 };
