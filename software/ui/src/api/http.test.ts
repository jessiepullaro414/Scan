/**
 * Checks for `api.fetchBinary` (`npm test`) with a fake `Response`. Plain TypeScript that Node
 * runs as it is (type stripping), no test framework: a failed check throws.
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

console.log(`http.test: ${checks} checks passed`);
