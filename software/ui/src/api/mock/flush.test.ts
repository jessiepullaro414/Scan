/**
 * Checks for the demo engine's flush along a seam (`npm test`; `flush.ts`): its simple reading on two made-up plates (a
 * flush of 1 mm reads 1 mm, the other click first reads −1 mm, a stretch of the second plate missing is not trusted),
 * and the routes as the engine answers them: the measurement with the gap not offered, the stations as CSV with a
 * byte-order mark, keep, list, rename, delete, a kept seam measured again with "the mesh has changed", the refusals.
 * Plain TypeScript that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { FlushAnswer, KeptSeam, KeptSeams } from "../types";
import { HttpError } from "./host.ts";
import { MockFlush, mockFlush, type FlushEngine } from "./flush.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`flush.test (mock): ${what}`);
}

/** Two plates along x (−60 to 60 mm): the first at z = 0 for y from −30 to −2, the second at z = 1 for y from 2 to 30,
 * 1 mm apart; `gone`: the second plate's vertices with |x| below it are left out. */
function plates(gone = 0) {
  const P: number[] = [];
  const I: number[] = [];
  const sheet = (y0: number, y1: number, z: number, skip: (x: number) => boolean) => {
    const base = P.length / 3;
    const xs: number[] = [];
    for (let x = -60; x <= 60; x++) xs.push(x);
    const ys: number[] = [];
    for (let y = y0; y <= y1; y++) ys.push(y);
    const idx = new Map<string, number>();
    let n = 0;
    for (const x of xs)
      for (const y of ys) {
        if (skip(x)) continue;
        idx.set(`${x},${y}`, base + n++);
        P.push(x, y, z);
      }
    for (let i = 0; i + 1 < xs.length; i++)
      for (let j = 0; j + 1 < ys.length; j++) {
        const q = [idx.get(`${xs[i]},${ys[j]}`), idx.get(`${xs[i + 1]},${ys[j]}`), idx.get(`${xs[i + 1]},${ys[j + 1]}`), idx.get(`${xs[i]},${ys[j + 1]}`)];
        if (q.some((v) => v === undefined)) continue;
        I.push(q[0]!, q[1]!, q[2]!, q[0]!, q[2]!, q[3]!);
      }
  };
  sheet(-30, -2, 0, () => false);
  sheet(2, 30, 1, (x) => Math.abs(x) < gone);
  return { positions: new Float32Array(P), indices: new Uint32Array(I), sigma: new Float32Array(P.length / 3).fill(0.05) };
}

const A: [number, number, number] = [30, -10, 0];
const B: [number, number, number] = [30, 10, 1];

// ---------------------------------------------------------------- the reading
{
  const r = mockFlush(plates(), A, B);
  check(r.stations.length >= 35, `stations along the seam: ${r.stations.length}`);
  const pos = r.stations.map((s) => s.position_mm);
  check(pos.every((p, i) => i === 0 || p > pos[i - 1]), "in order along the seam");
  check(Math.abs(r.flush.mean! - 1) < 1e-6 && r.flush.n_trusted === r.stations.length, `flush 1 mm, all trusted: ${r.flush.mean}`);
  check(r.gap_offered === false && r.gap_note.length > 20 && r.stations.every((s) => s.gap_mm === null), "the gap is not offered");
  check(r.stations.every((s) => s.flush_sigma_mm !== null && s.flush_sigma_mm > 0), "each station has a ±");
  const back = mockFlush(plates(), B, A);
  check(Math.abs(back.flush.mean! + 1) < 1e-6, `the other click first: ${back.flush.mean}`);
  const holed = mockFlush(plates(8), A, B);
  const missing = holed.stations.filter((s) => Math.abs(s.point[0]) < 6);
  check(missing.length > 0 && missing.every((s) => !s.flush_trusted && s.side_b === "missing" && s.notes.length > 0), "the second plate missing: not trusted, said why");
  check(holed.flush.n_trusted < holed.flush.n_stations, "fewer trusted");
  let refused = "";
  try {
    mockFlush(plates(), [0, 0, 50], B);
  } catch (e) {
    refused = (e as HttpError).detail;
  }
  check(refused.includes("mm from the mesh"), `a click off the mesh: ${refused}`);
}

// ---------------------------------------------------------------- the routes
{
  const f = new MockFlush();
  let version: number | null = null;
  const engine: FlushEngine = { version: () => version, mesh: () => (version === null ? null : plates()), names: () => ({ part: "Door", scan: "Scan 1" }) };
  const key = "p/s";
  const call = async (method: string, word: "flush" | "seams", rest: string[], body: Record<string, unknown> = {}) => {
    try {
      return f.route(method, key, word, rest, body, engine);
    } catch (e) {
      if (e instanceof HttpError) return new Response(JSON.stringify({ detail: e.detail, hint: e.hint }), { status: e.status });
      throw e;
    }
  };
  const run = async () => {
    check((await call("POST", "flush", [], { a: A, b: B }))!.status === 409, "409 before there is a mesh");
    check((await call("POST", "seams", [], { a: A, b: B }))!.status === 409, "keeping too");
    const none = (await (await call("GET", "seams", []))!.json()) as KeptSeams;
    check(none.mesh_version === null && none.seams.length === 0, "an empty list before a mesh");
    version = 3;
    const r = (await (await call("POST", "flush", [], { a: A, b: B }))!.json()) as FlushAnswer;
    check(Math.abs(r.flush.mean! - 1) < 1e-6 && r.mesh_version === 3 && r.kept === null && r.warnings.length === 0, "the answer");
    check(r.filename === "Scan_1-flush.csv", r.filename);
    const csv = await call("GET", "flush", ["stations.csv"], { a: A.join(","), b: B.join(",") });
    const text = new TextDecoder("utf-8", { ignoreBOM: true }).decode(await csv!.arrayBuffer()); // text() would drop the mark
    check(csv!.status === 200 && text.charCodeAt(0) === 0xfeff, "the CSV starts with a byte-order mark");
    const rows = text.slice(1).trim().split("\r\n");
    check(rows[0].startsWith("position_mm,x_mm,y_mm,z_mm,flush_mm") && !rows[0].includes("gap"), rows[0]);
    check(rows.length === r.stations.length + 1, "one row per station");
    check((await call("POST", "flush", [], { a: A }))!.status === 422, "a missing click: 422");
    check((await call("POST", "flush", [], { a: A, b: [1, 2] }))!.status === 422, "a click of two numbers: 422");
    check((await call("POST", "flush", [], { a: A, b: A }))!.status === 422, "the same click twice: 422");
    // keep, list, open, rename, mark as current, delete
    const s = (await (await call("POST", "seams", [], { a: A, b: B, name: "  Door to wing " }))!.json()) as KeptSeam;
    check(s.id.startsWith("sm-") && s.name === "Door to wing" && s.mesh_version === 3 && !s.mesh_changed, "kept");
    const s2 = (await (await call("POST", "seams", [], { a: B, b: A }))!.json()) as KeptSeam;
    check(s2.name === "Seam 2", s2.name);
    const opened = (await (await call("GET", "seams", [s.id]))!.json()) as FlushAnswer;
    check(opened.kept?.id === s.id && opened.warnings.length === 0 && opened.filename === "Scan_1-flush-Door_to_wing.csv", "opened");
    version = 4;
    const again = (await (await call("GET", "seams", [s.id]))!.json()) as FlushAnswer;
    check(again.kept!.mesh_changed && again.warnings.some((w) => w.includes("changed")), "the mesh has changed: said so");
    const listed = (await (await call("GET", "seams", []))!.json()) as KeptSeams;
    check(listed.seams.every((x) => x.mesh_changed) && listed.mesh_version === 4, "the list marks it");
    const renamed = (await (await call("PATCH", "seams", [s.id], { name: "Bonnet" }))!.json()) as KeptSeam;
    check(renamed.name === "Bonnet", "renamed");
    check((await call("PATCH", "seams", [s.id], { name: "  " }))!.status === 422, "an empty name: 422");
    check((await call("PATCH", "seams", [s.id], {}))!.status === 422, "nothing to change: 422");
    const marked = (await (await call("PATCH", "seams", [s.id], { mark_current: true }))!.json()) as KeptSeam;
    check(marked.mesh_version === 4 && !marked.mesh_changed, "marked as current");
    const kcsv = await call("GET", "seams", [s.id, "stations.csv"]);
    check(kcsv!.status === 200 && (kcsv!.headers.get("content-disposition") ?? "").includes("Bonnet"), "a kept seam's CSV");
    check((await call("DELETE", "seams", [s.id]))!.status === 204, "deleted");
    check((await call("GET", "seams", [s.id]))!.status === 404, "gone: 404");
    check((await call("DELETE", "seams", [s.id]))!.status === 404, "again: 404");
    check((await call("DELETE", "seams", [s2.id]))!.status === 204, "the other deleted");
  };
  await run();
}

console.log(`flush.test (mock): ${checks} checks passed`);
