/**
 * Checks for `api.fetchBinary` and for the per-vertex maps (`api.deviation`, `api.lasers`) (`npm test`)
 * with a fake `Response`. Plain TypeScript that Node runs as it is (type stripping), no test framework: a
 * failed check throws.
 */
import { ApiError, api, setFetch } from "./http.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`http.test: ${what}`);
}

const chunks = [new Uint8Array([1, 2, 3]), new Uint8Array([4, 5]), new Uint8Array([6, 7, 8, 9])];
const all = [1, 2, 3, 4, 5, 6, 7, 8, 9];
const same = (b: ArrayBuffer, v: number[]) => b.byteLength === v.length && Array.from(new Uint8Array(b)).every((x, i) => x === v[i]);

function streamOf(parts: Uint8Array[]): ReadableStream<Uint8Array> {
  let i = 0;
  return new ReadableStream({
    pull(c) {
      if (i < parts.length) c.enqueue(parts[i++]);
      else c.close();
    },
  });
}
const respond =
  (headers: Record<string, string> = {}, status = 200) =>
  async () =>
    new Response(streamOf(chunks), { status, headers });

async function failure(): Promise<unknown> {
  try {
    await api.fetchBinary("/f");
  } catch (e) {
    return e;
  }
  return undefined;
}

// known length: progress after each chunk, with the total
{
  setFetch(respond({ "content-length": "9" }));
  const calls: [number, number | null][] = [];
  const buf = await api.fetchBinary("/f", (l, t) => calls.push([l, t]));
  check(same(buf, all), "known length: all bytes, in order");
  check(JSON.stringify(calls) === "[[3,9],[5,9],[9,9]]", `known length: progress ${JSON.stringify(calls)}`);
}
// unknown length: the total is null
{
  setFetch(respond());
  const calls: [number, number | null][] = [];
  const buf = await api.fetchBinary("/f", (l, t) => calls.push([l, t]));
  check(same(buf, all), "unknown length: all bytes, in order");
  check(JSON.stringify(calls) === "[[3,null],[5,null],[9,null]]", `unknown length: progress ${JSON.stringify(calls)}`);
}
// a content-length of 0 counts as unknown
{
  setFetch(respond({ "content-length": "0" }));
  let total: number | null | undefined;
  await api.fetchBinary("/f", (_l, t) => (total = t));
  check(total === null, "content-length 0: total is null");
}
// a content-length that is too small (a compressed transfer): still all bytes
{
  setFetch(respond({ "content-length": "4" }));
  const calls: [number, number | null][] = [];
  const buf = await api.fetchBinary("/f", (l, t) => calls.push([l, t]));
  check(same(buf, all), "content-length too small: all bytes, in order");
  check(JSON.stringify(calls) === "[[3,4],[5,4],[9,4]]", `content-length too small: progress ${JSON.stringify(calls)}`);
}
// a content-length that is too large: only the bytes that came
{
  setFetch(respond({ "content-length": "20" }));
  const buf = await api.fetchBinary("/f", () => {});
  check(same(buf, all), "content-length too large: 9 bytes, in order");
}
// a chunk that ends exactly at the content-length, then more
{
  setFetch(respond({ "content-length": "5" }));
  check(same(await api.fetchBinary("/f", () => {}), all), "content-length at a chunk border: all bytes");
}
// no progress callback: the body is read in one go
{
  setFetch(respond({ "content-length": "9" }));
  check(same(await api.fetchBinary("/f"), all), "no callback: all bytes");
}
// no body stream: one progress call at the end
{
  setFetch(async () => ({ ok: true, status: 200, body: null, headers: new Headers(), arrayBuffer: async () => new Uint8Array(all).buffer }) as unknown as Response);
  const calls: [number, number | null][] = [];
  const buf = await api.fetchBinary("/f", (l, t) => calls.push([l, t]));
  check(same(buf, all) && JSON.stringify(calls) === "[[9,9]]", "no body stream: one final progress call");
}
// the request gets the abort signal
{
  let seen: AbortSignal | null | undefined;
  setFetch(async (_u, init) => {
    seen = init?.signal;
    return new Response(new Uint8Array(all));
  });
  const ac = new AbortController();
  await api.fetchBinary("/f", undefined, ac.signal);
  check(seen === ac.signal, "the abort signal is passed to fetch");
}
// an abort passes through as it is
{
  setFetch(async () => {
    throw new DOMException("aborted", "AbortError");
  });
  const err = await failure();
  check(err instanceof Error && err.name === "AbortError", "AbortError is rethrown");
}
// a network failure becomes an ApiError that says the engine is unreachable
{
  setFetch(async () => {
    throw new TypeError("failed to fetch");
  });
  const err = await failure();
  check(err instanceof ApiError && err.unreachable && err.status === 0, "network failure: unreachable ApiError");
}
// an HTTP error becomes an ApiError with the engine's detail and hint
{
  setFetch(async () => new Response(JSON.stringify({ detail: "no such file", hint: "scan again" }), { status: 404 }));
  const err = await failure();
  check(err instanceof ApiError && err.status === 404 && err.detail === "no such file" && err.hint === "scan again", "HTTP error: ApiError with detail and hint");
}

// ---------------------------------------------------------------- the per-vertex maps: deviation and lasers
// both are read as {name}.json then {name}.bin, and must be of one mesh version
for (const name of ["deviation", "lasers"] as const) {
  const read = (signal?: AbortSignal) => (name === "deviation" ? api.deviation("p 1", "s1", signal) : api.lasers("p 1", "s1", signal));
  const fail = async (signal?: AbortSignal): Promise<unknown> => {
    try {
      await read(signal);
    } catch (e) {
      return e;
    }
    return undefined;
  };
  const base = `/api/projects/p%201/scans/s1/process/${name}`;
  const values = new Float32Array([0.25, NaN, -0.5]);
  const jsonOf = (version: number, vertices = 3) => new Response(JSON.stringify({ version, vertices }), { status: 200 });
  const binOf = (version: number | null, v = values) => new Response(v.buffer.slice(0) as ArrayBuffer, { status: 200, headers: version === null ? {} : { "X-OHS-Mesh-Version": String(version) } });

  // both files of one version: the statistics and the values, one read of each
  {
    const asked: string[] = [];
    setFetch(async (u) => {
      asked.push(u);
      return u.endsWith(".json") ? jsonOf(7) : binOf(7);
    });
    const d = await read();
    check(JSON.stringify(asked) === JSON.stringify([`${base}.json`, `${base}.bin`]), `${name}: the json, then the bin (${asked.join(", ")})`);
    check(d.info.version === 7 && d.info.vertices === 3, `${name}: the statistics`);
    check(d.values.length === 3 && d.values[0] === 0.25 && Number.isNaN(d.values[1]) && d.values[2] === -0.5, `${name}: the values as float32`);
  }
  // the bin is of a newer version the first time: both are read once more
  {
    let n = 0;
    setFetch(async (u) => {
      n++;
      return u.endsWith(".json") ? jsonOf(n <= 2 ? 7 : 8) : binOf(8);
    });
    const d = await read();
    check(n === 4 && d.info.version === 8, `${name}: a version mismatch is read again (${n} requests)`);
  }
  // still not one version the second time: a 409 that says so
  {
    let n = 0;
    setFetch(async (u) => {
      n++;
      return u.endsWith(".json") ? jsonOf(7) : binOf(8);
    });
    const err = await fail();
    check(n === 4 && err instanceof ApiError && err.status === 409 && /mesh changed/.test(err.detail) && err.hint === "Try again in a moment.", `${name}: a second mismatch is a 409`);
  }
  // an array of another length than the statistics say, or no version header: a mismatch too
  {
    setFetch(async (u) => (u.endsWith(".json") ? jsonOf(7, 5) : binOf(7)));
    const err = await fail();
    check(err instanceof ApiError && err.status === 409, `${name}: another length is a mismatch`);
    setFetch(async (u) => (u.endsWith(".json") ? jsonOf(7) : binOf(null)));
    const err2 = await fail();
    check(err2 instanceof ApiError && err2.status === 409, `${name}: no version header is a mismatch`);
  }
  // an engine that does not know the request: the 404 comes through as it is
  {
    setFetch(async () => new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 }));
    const err = await fail();
    check(err instanceof ApiError && err.status === 404 && err.detail === "Not Found", `${name}: a 404 is an ApiError 404`);
  }
  // a 409 for the bin (no mesh yet) keeps the engine's words
  {
    setFetch(async (u) => (u.endsWith(".json") ? jsonOf(7) : new Response(JSON.stringify({ detail: "no mesh yet", hint: "make it" }), { status: 409 })));
    const err = await fail();
    check(err instanceof ApiError && err.status === 409 && err.detail === "no mesh yet" && err.hint === "make it", `${name}: the bin's error keeps its detail and hint`);
  }
  // the bin's request gets the abort signal; an abort passes through
  {
    let seen: AbortSignal | null | undefined;
    setFetch(async (u, init) => {
      if (u.endsWith(".json")) return jsonOf(7);
      seen = init?.signal;
      throw new DOMException("aborted", "AbortError");
    });
    const ac = new AbortController();
    const err = await fail(ac.signal);
    check(seen === ac.signal && err instanceof Error && err.name === "AbortError", `${name}: the abort signal is passed on and an abort is rethrown`);
  }
  // the engine is gone between the two files: unreachable
  {
    setFetch(async (u) => {
      if (u.endsWith(".json")) return jsonOf(7);
      throw new TypeError("failed to fetch");
    });
    const err = await fail();
    check(err instanceof ApiError && err.unreachable, `${name}: a network failure is an unreachable ApiError`);
  }
}

console.log(`http.test: ${checks} checks passed`);
