/**
 * A fast reader for binary PLY meshes, straight into typed arrays (three's PLYLoader builds
 * a JS object per vertex and face, which takes seconds at a million triangles).
 *
 * Face order is kept exactly as in the file: the processing studio sends face indices of the
 * current mesh back to the engine. Faces with more than three corners are split into fans;
 * `faceOfTri` then maps each triangle back to its face in the file.
 *
 * Returns null for anything it doesn't handle (ASCII PLY, no faces…): use PLYLoader then.
 */

export type PlyMesh = {
  positions: Float32Array;
  /** Per-vertex normals when the file has them. */
  normals: Float32Array | null;
  indices: Uint32Array;
  /** The file's face of each triangle, or null when every face is a triangle (1:1). */
  faceOfTri: Uint32Array | null;
  /** Faces in the file. */
  faceCount: number;
};

type Scalar = "i8" | "u8" | "i16" | "u16" | "i32" | "u32" | "f32" | "f64";
type Prop = { name: string; type: Scalar; list: null | { count: Scalar } };
type Element = { name: string; count: number; props: Prop[] };

const TYPES: Record<string, Scalar> = {
  char: "i8", int8: "i8", uchar: "u8", uint8: "u8",
  short: "i16", int16: "i16", ushort: "u16", uint16: "u16",
  int: "i32", int32: "i32", uint: "u32", uint32: "u32",
  float: "f32", float32: "f32", double: "f64", float64: "f64",
};
const SIZE: Record<Scalar, number> = { i8: 1, u8: 1, i16: 2, u16: 2, i32: 4, u32: 4, f32: 4, f64: 8 };

function reader(dv: DataView, t: Scalar, le: boolean): (o: number) => number {
  switch (t) {
    case "i8": return (o) => dv.getInt8(o);
    case "u8": return (o) => dv.getUint8(o);
    case "i16": return (o) => dv.getInt16(o, le);
    case "u16": return (o) => dv.getUint16(o, le);
    case "i32": return (o) => dv.getInt32(o, le);
    case "u32": return (o) => dv.getUint32(o, le);
    case "f32": return (o) => dv.getFloat32(o, le);
    case "f64": return (o) => dv.getFloat64(o, le);
  }
}

function parseHeader(buf: ArrayBuffer): { elements: Element[]; le: boolean; start: number } | null {
  const head = new Uint8Array(buf, 0, Math.min(buf.byteLength, 1 << 16));
  const text = new TextDecoder("latin1").decode(head);
  if (!text.startsWith("ply")) return null;
  const end = text.indexOf("end_header");
  if (end < 0) return null;
  let start = end + "end_header".length;
  if (text[start] === "\r") start++;
  if (text[start] === "\n") start++;
  const elements: Element[] = [];
  let le: boolean | null = null;
  for (const raw of text.slice(0, end).split(/\r?\n/)) {
    const w = raw.trim().split(/\s+/);
    if (w[0] === "format") {
      if (w[1] === "binary_little_endian") le = true;
      else if (w[1] === "binary_big_endian") le = false;
      else return null; // ASCII: PLYLoader
    } else if (w[0] === "element") {
      elements.push({ name: w[1], count: Number(w[2]) | 0, props: [] });
    } else if (w[0] === "property" && elements.length) {
      const el = elements[elements.length - 1];
      if (w[1] === "list") {
        const count = TYPES[w[2]];
        const type = TYPES[w[3]];
        if (!count || !type) return null;
        el.props.push({ name: w[4], type, list: { count } });
      } else {
        const type = TYPES[w[1]];
        if (!type) return null;
        el.props.push({ name: w[2], type, list: null });
      }
    }
  }
  if (le === null) return null;
  return { elements, le, start };
}

/** Size in bytes of one record of an element without list properties, else null. */
function fixedStride(el: Element): number | null {
  let s = 0;
  for (const p of el.props) {
    if (p.list) return null;
    s += SIZE[p.type];
  }
  return s;
}

export function parsePly(buf: ArrayBuffer): PlyMesh | null {
  const h = parseHeader(buf);
  if (!h) return null;
  const { elements, le } = h;
  const dv = new DataView(buf);
  let o = h.start;

  let positions: Float32Array | null = null;
  let normals: Float32Array | null = null;
  let indices: Uint32Array | null = null;
  let faceOfTri: Uint32Array | null = null;
  let faceCount = 0;

  for (const el of elements) {
    if (el.name === "vertex") {
      const stride = fixedStride(el);
      if (stride === null) return null;
      const off: Record<string, { at: number; type: Scalar; read: (o: number) => number }> = {};
      let at = 0;
      for (const p of el.props) {
        off[p.name] = { at, type: p.type, read: reader(dv, p.type, le) };
        at += SIZE[p.type];
      }
      const x = off.x, y = off.y, z = off.z;
      if (!x || !y || !z) return null;
      const V = el.count;
      if (o + V * stride > buf.byteLength) return null;
      positions = new Float32Array(V * 3);
      const nx = off.nx, ny = off.ny, nz = off.nz;
      const hasN = !!(nx && ny && nz);
      if (hasN) normals = new Float32Array(V * 3);
      const f32 = (p: { type: Scalar } | undefined) => p?.type === "f32";
      if (le && x.at === 0 && y.at === 4 && z.at === 8 && f32(x) && f32(y) && f32(z) && (o & 3) === 0 && (stride & 3) === 0) {
        // Common case (Open3D, trimesh): aligned float32 records; read through one typed view.
        const s4 = stride >> 2;
        const all = new Float32Array(buf, o, V * s4);
        const n0 = hasN && f32(nx) && f32(ny) && f32(nz) && nx.at % 4 === 0 && ny.at === nx.at + 4 && nz.at === nx.at + 8 ? nx.at >> 2 : -1;
        for (let i = 0, r = 0, w = 0; i < V; i++, r += s4, w += 3) {
          positions[w] = all[r];
          positions[w + 1] = all[r + 1];
          positions[w + 2] = all[r + 2];
          if (n0 >= 0) {
            normals![w] = all[r + n0];
            normals![w + 1] = all[r + n0 + 1];
            normals![w + 2] = all[r + n0 + 2];
          }
        }
        if (hasN && n0 < 0) {
          for (let i = 0, r = o, w = 0; i < V; i++, r += stride, w += 3) {
            normals![w] = nx!.read(r + nx!.at);
            normals![w + 1] = ny!.read(r + ny!.at);
            normals![w + 2] = nz!.read(r + nz!.at);
          }
        }
      } else {
        for (let i = 0, r = o, w = 0; i < V; i++, r += stride, w += 3) {
          positions[w] = x.read(r + x.at);
          positions[w + 1] = y.read(r + y.at);
          positions[w + 2] = z.read(r + z.at);
          if (hasN) {
            normals![w] = nx!.read(r + nx!.at);
            normals![w + 1] = ny!.read(r + ny!.at);
            normals![w + 2] = nz!.read(r + nz!.at);
          }
        }
      }
      o += V * stride;
    } else if (el.name === "face") {
      const li = el.props.findIndex((p) => p.list && (p.name === "vertex_indices" || p.name === "vertex_index"));
      if (li < 0) return null;
      const F = el.count;
      faceCount = F;
      let idx: Uint32Array = new Uint32Array(F * 3);
      let map: Uint32Array | null = null;
      let n = 0; // triangles written
      const readers = el.props.map((p) => ({
        size: SIZE[p.type],
        count: p.list ? reader(dv, p.list.count, le) : null,
        countSize: p.list ? SIZE[p.list.count] : 0,
        item: reader(dv, p.type, le),
      }));
      const end = buf.byteLength;
      for (let f = 0; f < F; f++) {
        for (let k = 0; k < readers.length; k++) {
          const r = readers[k];
          if (!r.count) {
            o += r.size;
            continue;
          }
          if (o + r.countSize > end) return null;
          const c = r.count(o);
          o += r.countSize;
          if (o + c * r.size > end) return null;
          if (k !== li) {
            o += c * r.size;
            continue;
          }
          if (c === 3 && !map) {
            idx[n * 3] = r.item(o);
            idx[n * 3 + 1] = r.item(o + r.size);
            idx[n * 3 + 2] = r.item(o + 2 * r.size);
            n++;
          } else if (c >= 3) {
            // A polygon: fan triangles, remembering the face each came from.
            let m: Uint32Array;
            if (map) m = map;
            else {
              m = new Uint32Array(Math.max(16, idx.length / 3));
              for (let t = 0; t < n; t++) m[t] = t;
            }
            const a = r.item(o);
            for (let j = 1; j + 1 < c; j++) {
              if ((n + 1) * 3 > idx.length) {
                const grownIdx: Uint32Array = new Uint32Array(idx.length * 2);
                grownIdx.set(idx);
                idx = grownIdx;
              }
              if (n + 1 > m.length) {
                const grownMap: Uint32Array = new Uint32Array(m.length * 2);
                grownMap.set(m);
                m = grownMap;
              }
              idx[n * 3] = a;
              idx[n * 3 + 1] = r.item(o + j * r.size);
              idx[n * 3 + 2] = r.item(o + (j + 1) * r.size);
              m[n] = f;
              n++;
            }
            map = m;
          }
          o += c * r.size;
        }
      }
      indices = n * 3 === idx.length ? idx : idx.slice(0, n * 3);
      faceOfTri = map ? map.slice(0, n) : null;
    } else {
      // Anything else (edges, materials…): skip its records.
      const stride = fixedStride(el);
      if (stride !== null) o += el.count * stride;
      else {
        for (let i = 0; i < el.count; i++)
          for (const p of el.props) {
            if (p.list) {
              const c = reader(dv, p.list.count, le)(o);
              o += SIZE[p.list.count] + c * SIZE[p.type];
            } else o += SIZE[p.type];
          }
      }
    }
  }
  if (!positions || !indices) return null;
  const V = positions.length / 3;
  for (let i = 0; i < indices.length; i++) if (indices[i] >= V) return null;
  return { positions, normals, indices, faceOfTri, faceCount };
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
