/**
 * Checks for the binary WebSocket frames (`npm test`). Plain TypeScript that Node runs as it
 * is (type stripping), no test framework: a failed check throws.
 */
import { decodeBinary, encodeBinary, meshLayout } from "./binary.ts";
import type { DecodedMesh, DecodedPreview } from "./binary.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`binary.test: ${what}`);
}
function throws(f: () => unknown): boolean {
  try {
    f();
  } catch {
    return true;
  }
  return false;
}
const same = (a: ArrayLike<number>, b: ArrayLike<number>) => a.length === b.length && Array.from(a).every((v, i) => v === b[i]);

const positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0.5]);
const normals = new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1]);
const colors = new Uint8Array([255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 9, 8, 7, 6]);
const indices = new Uint32Array([0, 1, 2, 2, 1, 3]);
const meshHeader = { type: "mesh", vertices: 4, triangles: 2 };

function checkMesh(m: DecodedMesh, what: string): void {
  check(m.type === "mesh", `${what}: type`);
  check(m.header.vertices === 4 && m.header.triangles === 2, `${what}: header`);
  check(same(m.positions, positions), `${what}: positions`);
  check(same(m.normals, normals), `${what}: normals`);
  check(same(m.colors, colors), `${what}: colors`);
  check(same(m.indices, indices), `${what}: indices`);
}

/** A mesh message whose header is the JSON plus `pad` filler bytes, so the payload starts at 4 + header length. */
function meshWithHeaderPad(pad: number, fill: number): ArrayBuffer {
  const hb = new TextEncoder().encode(JSON.stringify(meshHeader));
  const header = new Uint8Array(hb.length + pad);
  header.set(hb);
  header.fill(fill, hb.length);
  const whole = new Uint8Array(encodeBinary(meshHeader, [positions, normals, colors, indices]));
  const pl = whole.subarray(whole.length - meshLayout(4, 2).size);
  const msg = new Uint8Array(4 + header.length + pl.length);
  new DataView(msg.buffer).setUint32(0, header.length, true);
  msg.set(header, 4);
  msg.set(pl, 4 + header.length);
  return msg.buffer;
}

// mesh round trip
{
  const buf = encodeBinary(meshHeader, [positions, normals, colors, indices]);
  check(buf.byteLength % 4 === 0, "the encoded message is a multiple of 4 bytes");
  const hlen = new DataView(buf).getUint32(0, true);
  check((4 + hlen) % 4 === 0, "the header is padded so the payload starts on 4 bytes");
  check(buf.byteLength - 4 - hlen === meshLayout(4, 2).size, "mesh: payload size is the layout size");
  const d = decodeBinary(buf);
  check(d.type === "mesh", "mesh: decoded as mesh");
  checkMesh(d as DecodedMesh, "mesh round trip");
}

// layout: sections start on 4-byte boundaries
{
  const l = meshLayout(3, 1);
  check(same(l.offsets, [0, 36, 72, 84]) && l.size === 96, "meshLayout offsets and size");
  check(meshLayout(0, 0).size === 0, "meshLayout of nothing");
}

// an unaligned header: the payload starts at a byte that is not a multiple of 4
for (const pad of [0, 1, 2, 3]) {
  const buf = meshWithHeaderPad(pad, 0x20);
  checkMesh(decodeBinary(buf) as DecodedMesh, `header padded with ${pad} spaces`);
}
{
  const buf = meshWithHeaderPad(3, 0);
  checkMesh(decodeBinary(buf) as DecodedMesh, "header padded with NULs");
}

// preview: the JPEG bytes follow the header (also with up to 3 padding bytes before FF D8)
{
  const jpeg = new Uint8Array([0xff, 0xd8, 1, 2, 3, 4, 5, 0xff, 0xd9]);
  const d = decodeBinary(encodeBinary({ type: "preview", frame: 7 }, [jpeg])) as DecodedPreview;
  check(d.type === "preview" && same(d.jpeg, jpeg), "preview round trip");
  const hb = new TextEncoder().encode(JSON.stringify({ type: "preview" }));
  const msg = new Uint8Array(4 + hb.length + 2 + jpeg.length);
  new DataView(msg.buffer).setUint32(0, hb.length, true);
  msg.set(hb, 4);
  msg.set(jpeg, 4 + hb.length + 2);
  const p = decodeBinary(msg.buffer) as DecodedPreview;
  check(same(p.jpeg, jpeg), "preview: 2 padding bytes before the JPEG are skipped");
}

// any other type: only the header
{
  const d = decodeBinary(encodeBinary({ type: "hello", n: 3 }, []));
  check(d.type === "other" && d.header.n === 3, "other type: header only");
}

// bad input throws
check(throws(() => decodeBinary(new ArrayBuffer(2))), "shorter than the length prefix");
{
  const b = new ArrayBuffer(8);
  new DataView(b).setUint32(0, 100, true);
  check(throws(() => decodeBinary(b)), "header length past the message");
}
{
  const buf = encodeBinary(meshHeader, [positions, normals, colors, indices]);
  check(throws(() => decodeBinary(buf.slice(0, buf.byteLength - 4))), "mesh payload too short");
}
check(throws(() => decodeBinary(encodeBinary({ type: "mesh", vertices: -1, triangles: 0 }, []))), "negative mesh size");

console.log(`binary.test: ${checks} checks passed`);
