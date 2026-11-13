/**
 * Vertex normals from triangles. Its own small module: the Scan page's live mesh needs it without
 * the PLY reader.
 */

/** Area-weighted vertex normals, into `out` when given (it may be a view of a larger array). */
export function vertexNormals(p: Float32Array, f: Uint32Array): Float32Array<ArrayBuffer>;
export function vertexNormals<O extends Float32Array>(p: Float32Array, f: Uint32Array, out: O): O;
export function vertexNormals(p: Float32Array, f: Uint32Array, out: Float32Array = new Float32Array(p.length)): Float32Array {
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
