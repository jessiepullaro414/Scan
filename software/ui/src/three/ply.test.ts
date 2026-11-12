/**
 * Checks for the binary PLY reader (`npm test`). Plain TypeScript that Node runs as it is (type
 * stripping), no test framework: a failed check throws.
 */
import { parsePly } from "./plyParse.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`ply.test: ${what}`);
}
const same = (a: ArrayLike<number> | null, b: number[]) => !!a && a.length === b.length && b.every((v, i) => a[i] === v);

type Face = { corners: number[]; filled?: number };
type Opts = { le?: boolean; filled?: boolean; ascii?: boolean };

/** A PLY file: float x y z vertices, then faces (list uchar int vertex_indices [+ uchar filled]). */
function makePly(verts: number[][], faces: Face[], o: Opts = {}): ArrayBuffer {
  const le = o.le ?? true;
  const head =
    "ply\n" +
    `format ${o.ascii ? "ascii" : le ? "binary_little_endian" : "binary_big_endian"} 1.0\n` +
    `element vertex ${verts.length}\nproperty float x\nproperty float y\nproperty float z\n` +
    `element face ${faces.length}\nproperty list uchar int vertex_indices\n` +
    (o.filled ? "property uchar filled\n" : "") +
    "end_header\n";
  if (o.ascii) {
    const body =
      verts.map((v) => v.join(" ")).join("\n") +
      "\n" +
      faces.map((f) => `${f.corners.length} ${f.corners.join(" ")}${o.filled ? ` ${f.filled ?? 0}` : ""}`).join("\n") +
      "\n";
    return new TextEncoder().encode(head + body).buffer as ArrayBuffer;
  }
  const hb = new TextEncoder().encode(head);
  let size = hb.length + verts.length * 12;
  for (const f of faces) size += 1 + f.corners.length * 4 + (o.filled ? 1 : 0);
  const out = new Uint8Array(size);
  out.set(hb);
  const dv = new DataView(out.buffer);
  let p = hb.length;
  for (const v of verts)
    for (const c of v) {
      dv.setFloat32(p, c, le);
      p += 4;
    }
  for (const f of faces) {
    dv.setUint8(p++, f.corners.length);
    for (const c of f.corners) {
      dv.setInt32(p, c, le);
      p += 4;
    }
    if (o.filled) dv.setUint8(p++, f.filled ?? 0);
  }
  return out.buffer;
}

const V6 = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [2, 0, 0], [2, 1, 0]];

// all triangles: indices as written, no face map, faceCount = faces
{
  const m = parsePly(makePly(V6, [{ corners: [0, 1, 2] }, { corners: [0, 2, 3] }, { corners: [1, 4, 5] }]));
  check(m !== null, "all triangles: parsed");
  check(same(m!.positions, V6.flat()), "all triangles: positions");
  check(same(m!.indices, [0, 1, 2, 0, 2, 3, 1, 4, 5]), "all triangles: indices");
  check(m!.faceOfTri === null, "all triangles: no face map (1:1)");
  check(m!.faceCount === 3, "all triangles: faceCount");
  check(m!.filled === null && m!.normals === null && m!.quality === null, "all triangles: no filled / normals / quality");
}

// a quad is split into a fan of two triangles that both point to their face
{
  const m = parsePly(makePly(V6, [{ corners: [0, 1, 2] }, { corners: [0, 1, 2, 3] }, { corners: [1, 4, 5] }]));
  check(m !== null, "quad: parsed");
  check(same(m!.indices, [0, 1, 2, 0, 1, 2, 0, 2, 3, 1, 4, 5]), "quad: indices (fan from the first corner)");
  check(same(m!.faceOfTri, [0, 1, 1, 2]), "quad: face of each triangle");
  check(m!.faceCount === 3, "quad: faceCount counts faces, not triangles");
}
// a pentagon gives three triangles; a quad first in the file works too
{
  const m = parsePly(makePly(V6, [{ corners: [0, 1, 2, 3] }, { corners: [0, 1, 2, 3, 4] }, { corners: [1, 4, 5] }]));
  check(m !== null, "quad + pentagon: parsed");
  check(same(m!.faceOfTri, [0, 0, 1, 1, 1, 2]), "quad + pentagon: face of each triangle");
  check(m!.indices.length === 18, "quad + pentagon: 6 triangles");
}

// a `filled` property: one flag per triangle (per face when all are triangles)
{
  const m = parsePly(makePly(V6, [{ corners: [0, 1, 2], filled: 0 }, { corners: [0, 2, 3], filled: 1 }, { corners: [1, 4, 5], filled: 0 }], { filled: true }));
  check(m !== null && same(m.filled, [0, 1, 0]), "filled: per triangle");
  check(m!.faceOfTri === null, "filled: still 1:1");
  const q = parsePly(makePly(V6, [{ corners: [0, 1, 2, 3], filled: 1 }, { corners: [1, 4, 5], filled: 0 }], { filled: true }));
  check(q !== null && same(q.filled, [1, 1, 0]), "filled with a quad: the face's flag goes to both triangles");
  const none = parsePly(makePly(V6, [{ corners: [0, 1, 2], filled: 0 }, { corners: [0, 2, 3], filled: 0 }], { filled: true }));
  check(none !== null && none.filled === null, "filled: all zero reads as no filled faces");
}

// big-endian gives the same mesh as little-endian
{
  const faces = [{ corners: [0, 1, 2], filled: 1 }, { corners: [0, 1, 2, 3], filled: 0 }, { corners: [1, 4, 5], filled: 0 }];
  const le = parsePly(makePly(V6, faces, { filled: true }));
  const be = parsePly(makePly(V6, faces, { filled: true, le: false }));
  check(le !== null && be !== null, "big-endian: parsed");
  check(same(be!.positions, V6.flat()), "big-endian: positions");
  check(same(be!.indices, Array.from(le!.indices)), "big-endian: indices");
  check(same(be!.faceOfTri, Array.from(le!.faceOfTri!)), "big-endian: face map");
  check(same(be!.filled, Array.from(le!.filled!)), "big-endian: filled");
}

// ASCII, a file that is not PLY, a cut file, an index past the vertices: null (readPly then uses PLYLoader)
{
  check(parsePly(makePly(V6, [{ corners: [0, 1, 2] }], { ascii: true })) === null, "ASCII: null");
  check(parsePly(new TextEncoder().encode("solid stl\n").buffer as ArrayBuffer) === null, "not a PLY: null");
  const full = makePly(V6, [{ corners: [0, 1, 2] }, { corners: [1, 4, 5] }]);
  check(parsePly(full.slice(0, full.byteLength - 6)) === null, "cut off in the faces: null");
  check(parsePly(makePly(V6, [{ corners: [0, 1, 9] }])) === null, "an index past the vertices: null");
  const bare = parsePly(makePly(V6, []));
  check(bare !== null && bare.indices.length === 0 && bare.faceCount === 0, "a face element with no faces: an empty mesh");
}

// A degenerate face (fewer than 3 corners) writes no triangle.
{
  // After a polygon the face map exists and the degenerate face is skipped correctly.
  const m = parsePly(makePly(V6, [{ corners: [0, 1, 2, 3] }, { corners: [0, 1] }, { corners: [1, 4, 5] }]));
  check(m !== null, "degenerate after a quad: parsed");
  check(same(m!.indices, [0, 1, 2, 0, 2, 3, 1, 4, 5]), "degenerate after a quad: no triangle for it");
  check(same(m!.faceOfTri, [0, 0, 2]), "degenerate after a quad: the last triangle belongs to face 2");
  check(m!.faceCount === 3, "degenerate after a quad: faceCount still counts it");
}
{
  // Triangle, degenerate face, triangle: no polygon, so the face map is still null.
  const m = parsePly(makePly(V6, [{ corners: [0, 1, 2], filled: 0 }, { corners: [0, 1], filled: 1 }, { corners: [1, 4, 5], filled: 0 }], { filled: true }));
  check(m !== null, "degenerate between triangles: parsed");
  check(same(m!.indices, [0, 1, 2, 1, 4, 5]), "degenerate between triangles: two triangles");
  check(m!.faceCount === 3, "degenerate between triangles: faceCount 3");
  // B9 (known broken, not fixed yet): the right answer is faceOfTri = [0, 2] (triangle 1 comes
  // from face 2) and filled = [0, 0] (one flag per triangle). Today faceOfTri is null, which
  // says "triangle t is face t", and filled is the per-face array [0, 1, 0] (3 flags for 2
  // triangles). When B9 is fixed, replace these two checks with the right ones.
  check(m!.faceOfTri === null, "B9 (current, wrong): faceOfTri is null, should be [0, 2]");
  check(same(m!.filled, [0, 1, 0]), "B9 (current, wrong): filled has 3 flags for 2 triangles, should be [0, 0]");
}

console.log(`ply.test: ${checks} checks passed`);
