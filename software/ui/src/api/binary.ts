/**
 * Binary WebSocket messages (software/docs/APP_API.md):
 *
 *   uint32 LE header length | UTF-8 JSON header | payload
 *
 * - `preview`: the payload is a JPEG.
 * - `mesh`: four sections, each starting on a 4-byte boundary: positions Float32[V*3],
 *   normals Float32[V*3], colors Uint8[V*4] (RGBA), indices Uint32[T*3].
 *
 * The contract does not say whether "4-byte boundary" is counted from the message start or
 * from the payload start. Every section size is a multiple of 4, so the only possible
 * padding is between the header and the payload; the decoder finds the payload from the end
 * of the message, which handles both readings. Typed arrays are zero-copy views when the
 * payload is aligned in the buffer, otherwise the payload is copied once.
 */
import type { MeshHeader, PreviewHeader } from "./types";

export type DecodedPreview = { type: "preview"; header: PreviewHeader; jpeg: Uint8Array };
export type DecodedMesh = {
  type: "mesh";
  header: MeshHeader;
  positions: Float32Array;
  normals: Float32Array;
  colors: Uint8Array;
  indices: Uint32Array;
};
export type DecodedOther = { type: "other"; header: Record<string, unknown> };
export type DecodedBinary = DecodedPreview | DecodedMesh | DecodedOther;

const utf8Decoder = new TextDecoder();
const utf8Encoder = new TextEncoder();

const align4 = (n: number) => (n + 3) & ~3;

function readHeader(buf: ArrayBuffer): { header: Record<string, unknown>; afterHeader: number } {
  if (buf.byteLength < 4) throw new Error("binary message shorter than its length prefix");
  const hlen = new DataView(buf).getUint32(0, true);
  if (4 + hlen > buf.byteLength) throw new Error(`binary header length ${hlen} exceeds the message (${buf.byteLength} bytes)`);
  // Senders may pad the header with spaces or NULs to align the payload.
  const text = utf8Decoder.decode(new Uint8Array(buf, 4, hlen)).replace(/[\u0000\s]+$/, "");
  const header = JSON.parse(text) as Record<string, unknown>;
  if (!header || typeof header !== "object") throw new Error("binary header is not a JSON object");
  return { header, afterHeader: 4 + hlen };
}

/** Byte offsets of the mesh sections relative to the payload start, and the payload size. */
export function meshLayout(V: number, T: number): { offsets: [number, number, number, number]; size: number } {
  const sizes = [V * 12, V * 12, V * 4, T * 12];
  const offsets: number[] = [];
  let o = 0;
  for (const s of sizes) {
    o = align4(o);
    offsets.push(o);
    o += s;
  }
  return { offsets: offsets as [number, number, number, number], size: o };
}

export function decodeBinary(buf: ArrayBuffer): DecodedBinary {
  const { header, afterHeader } = readHeader(buf);

  if (header.type === "preview") {
    const u8 = new Uint8Array(buf);
    let start = afterHeader;
    // Tolerate up to 3 padding bytes before the JPEG start-of-image marker (FF D8).
    for (let k = 0; k < 4 && start + k + 1 < u8.length; k++) {
      if (u8[start + k] === 0xff && u8[start + k + 1] === 0xd8) {
        start += k;
        break;
      }
    }
    return { type: "preview", header: header as unknown as PreviewHeader, jpeg: new Uint8Array(buf, start) };
  }

  if (header.type === "mesh") {
    const h = header as unknown as MeshHeader;
    const V = Number(h.vertices) | 0;
    const T = Number(h.triangles) | 0;
    if (V < 0 || T < 0) throw new Error("mesh header has a negative size");
    const { offsets, size } = meshLayout(V, T);
    const fromEnd = buf.byteLength - size;
    const candidates = [fromEnd, align4(afterHeader), afterHeader];
    const start = candidates.find((c) => c >= afterHeader && c - afterHeader <= 3 && c + size <= buf.byteLength);
    if (start === undefined) {
      throw new Error(
        `mesh payload is ${buf.byteLength - afterHeader} bytes but ${V} vertices / ${T} triangles need ${size}`,
      );
    }
    let base = buf;
    let b0 = start;
    if (start % 4 !== 0) {
      base = buf.slice(start, start + size); // unaligned in this buffer: one copy
      b0 = 0;
    }
    return {
      type: "mesh",
      header: h,
      positions: new Float32Array(base, b0 + offsets[0], V * 3),
      normals: new Float32Array(base, b0 + offsets[1], V * 3),
      colors: new Uint8Array(base, b0 + offsets[2], V * 4),
      indices: new Uint32Array(base, b0 + offsets[3], T * 3),
    };
  }

  return { type: "other", header };
}

/**
 * Build a binary message (used by the mock backend, and handy for tests). The header is
 * padded with spaces so the payload starts 4-byte aligned; each section is aligned too.
 */
export function encodeBinary(header: Record<string, unknown>, sections: ArrayBufferView[]): ArrayBuffer {
  let hbytes = utf8Encoder.encode(JSON.stringify(header));
  const pad = (4 - ((4 + hbytes.length) % 4)) % 4;
  if (pad) {
    const padded = new Uint8Array(hbytes.length + pad);
    padded.set(hbytes);
    padded.fill(0x20, hbytes.length);
    hbytes = padded;
  }
  let payload = 0;
  for (const s of sections) payload = align4(payload) + s.byteLength;
  const out = new Uint8Array(4 + hbytes.length + payload);
  new DataView(out.buffer).setUint32(0, hbytes.length, true);
  out.set(hbytes, 4);
  let o = 4 + hbytes.length;
  for (const s of sections) {
    o = 4 + hbytes.length + align4(o - 4 - hbytes.length);
    out.set(new Uint8Array(s.buffer, s.byteOffset, s.byteLength), o);
    o += s.byteLength;
  }
  return out.buffer;
}
