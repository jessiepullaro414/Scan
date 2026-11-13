/**
 * A fast reader for binary PLY meshes, straight into typed arrays (three's PLYLoader builds
 * a JS object per vertex and face, which takes seconds at a million triangles).
 *
 * Face order is kept exactly as in the file: the processing studio sends face indices of the
 * current mesh back to the engine. Faces with more than three corners are split into fans;
 * `faceOfTri` then maps each triangle back to its face in the file.
 *
 * Returns null for anything it doesn't handle (ASCII PLY, no faces…): use PLYLoader then
 * ({@link readPly} does both). {@link plyGeometry} makes the three.js geometry of a mesh, with
 * its sigmas (the `quality`) in `userData.sigma` for the confidence map.
 */
import { BufferAttribute, BufferGeometry } from "three";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { boundsOf } from "./coords";
import { parsePly, type PlyMesh } from "./plyParse";

export { parsePly };
export type { PlyMesh };

/** PLY bytes into typed arrays; the fast reader first, three's PLYLoader for anything else. */
export function readPly(buf: ArrayBuffer): PlyMesh {
  const fast = parsePly(buf);
  if (fast) return fast;
  const g = new PLYLoader().parse(buf);
  const pos = g.getAttribute("position");
  if (!pos || !g.index) throw new Error("the file has no triangles");
  const positions = pos.array instanceof Float32Array ? pos.array : Float32Array.from(pos.array as ArrayLike<number>);
  const idx = g.index.array;
  const indices = idx instanceof Uint32Array ? idx : Uint32Array.from(idx as ArrayLike<number>);
  const n = g.getAttribute("normal");
  const normals = n ? (n.array instanceof Float32Array ? n.array : Float32Array.from(n.array as ArrayLike<number>)) : null;
  g.dispose();
  return { positions, normals, quality: null, filled: null, indices, faceOfTri: null, faceCount: indices.length / 3 };
}

function hasNormals(n: Float32Array | null): n is Float32Array {
  if (!n || !n.length) return false;
  const step = Math.max(3, Math.floor(n.length / 3 / 64) * 3);
  for (let i = 0; i < n.length; i += step) if (n[i] !== 0 || n[i + 1] !== 0 || n[i + 2] !== 0) return true;
  return false;
}

/**
 * The geometry of a mesh: positions, normals (computed when the file has none), index, bounds; sigmas in
 * `userData.sigma`, the file's face of each triangle in `userData.faceOfTri` (null: one triangle per face)
 * and the file's face count in `userData.faceCount`.
 */
export function plyGeometry(ply: PlyMesh): BufferGeometry {
  const g = new BufferGeometry();
  g.setAttribute("position", new BufferAttribute(ply.positions, 3));
  g.setAttribute("normal", new BufferAttribute(hasNormals(ply.normals) ? ply.normals : vertexNormals(ply.positions, ply.indices), 3));
  g.setIndex(new BufferAttribute(ply.indices, 1));
  const b = boundsOf(ply.positions, ply.positions.length / 3);
  if (b) {
    g.boundingBox = b.box;
    g.boundingSphere = b.sphere;
  } else {
    g.computeBoundingBox();
    g.computeBoundingSphere();
  }
  if (ply.quality && ply.quality.length * 3 === ply.positions.length) g.userData.sigma = ply.quality;
  // the file's faces, for face indices sent to the engine and back (the fitted shapes' regions)
  g.userData.faceOfTri = ply.faceOfTri;
  g.userData.faceCount = ply.faceCount;
  if (ply.filled && ply.filled.length * 3 === ply.indices.length) {
    // Taken now, in the file's triangle order: a raycast BVH may reorder the index later, so the
    // filled faces are kept as their own index and as keys of their (sorted) corners.
    const f = ply.filled;
    let n = 0;
    for (let t = 0; t < f.length; t++) n += f[t];
    const idx = new Uint32Array(n * 3);
    const keys = new Set<string>();
    for (let t = 0, w = 0; t < f.length; t++)
      if (f[t]) {
        const a = ply.indices[t * 3],
          b = ply.indices[t * 3 + 1],
          c = ply.indices[t * 3 + 2];
        idx[w++] = a;
        idx[w++] = b;
        idx[w++] = c;
        keys.add(faceKey(a, b, c));
      }
    g.userData.filledIndex = idx;
    g.userData.filledKeys = keys;
  }
  return g;
}

/** A triangle's key from its corners, in any order. */
export function faceKey(a: number, b: number, c: number): string {
  const [x, y, z] = [a, b, c].sort((p, q) => p - q);
  return `${x},${y},${z}`;
}

/** Dispose a mesh geometry and the overlays made from it (they share its positions). */
export function disposeMeshGeometry(g: BufferGeometry | null | undefined): void {
  if (!g) return;
  (g.userData.filledGeometry as BufferGeometry | undefined)?.dispose();
  g.dispose();
}

/** Area-weighted vertex normals. */
export function vertexNormals(p: Float32Array, f: Uint32Array, out = new Float32Array(p.length)): Float32Array {
  out.fill(0);
  for (let t = 0, T = f.length; t < T; t += 3) {
    const a = f[t] * 3,
      b = f[t + 1] * 3,
      c = f[t + 2] * 3;
    const ux = p[b] - p[a],
      uy = p[b + 1] - p[a + 1],
      uz = p[b + 2] - p[a + 2];
    const vx = p[c] - p[a],
      vy = p[c + 1] - p[a + 1],
      vz = p[c + 2] - p[a + 2];
    const nx = uy * vz - uz * vy,
      ny = uz * vx - ux * vz,
      nz = ux * vy - uy * vx;
    out[a] += nx;
    out[a + 1] += ny;
    out[a + 2] += nz;
    out[b] += nx;
    out[b + 1] += ny;
    out[b + 2] += nz;
    out[c] += nx;
    out[c + 1] += ny;
    out[c + 2] += nz;
  }
  for (let i = 0; i < out.length; i += 3) {
    const l = Math.hypot(out[i], out[i + 1], out[i + 2]) || 1;
    out[i] /= l;
    out[i + 1] /= l;
    out[i + 2] /= l;
  }
  return out;
}
