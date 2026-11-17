/**
 * Checks for the demo engine's "Remove the table" (`npm test`): a box standing on a table, its
 * table removed and its walls and top kept; the margin; a mesh without its table refused in the
 * engine's words. Plain TypeScript that Node runs as it is (type stripping), no test framework: a
 * failed check throws.
 */
import { mockTable, tablePlane, type Plane } from "./table.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`table.test (mock): ${what}`);
}

type Mesh = { P: number[]; I: number[]; kind: string[] };

/** A grid of quads over a rectangle: corner `o`, edges `u` and `v`, `n` cells each way; wound so that u × v is the normal. */
function quad(m: Mesh, o: number[], u: number[], v: number[], n: number, kind: string) {
  const base = m.P.length / 3;
  for (let j = 0; j <= n; j++)
    for (let i = 0; i <= n; i++) for (let k = 0; k < 3; k++) m.P.push(o[k] + (u[k] * i) / n + (v[k] * j) / n);
  for (let j = 0; j < n; j++)
    for (let i = 0; i < n; i++) {
      const a = base + j * (n + 1) + i;
      m.I.push(a, a + 1, a + n + 2, a, a + n + 2, a + n + 1);
      m.kind.push(kind, kind);
    }
}

/** A 40 × 40 × 20 mm box on a 200 × 200 mm table (z = 0, z up), and a ramp leaning on the table at 30°. */
function scene(withTable = true): Mesh {
  const m: Mesh = { P: [], I: [], kind: [] };
  if (withTable) quad(m, [-100, -100, 0], [200, 0, 0], [0, 200, 0], 100, "table");
  quad(m, [-20, -20, 20], [40, 0, 0], [0, 40, 0], 20, "top");
  quad(m, [-20, -20, 0], [40, 0, 0], [0, 0, 20], 20, "wall"); // facing -y
  quad(m, [20, 20, 0], [-40, 0, 0], [0, 0, 20], 20, "wall"); // facing +y
  quad(m, [20, -20, 0], [0, 40, 0], [0, 0, 20], 20, "wall"); // facing +x
  quad(m, [-20, 20, 0], [0, -40, 0], [0, 0, 20], 20, "wall"); // facing -x
  const c = Math.cos(Math.PI / 6),
    s = Math.sin(Math.PI / 6);
  quad(m, [50, -10, 0], [20 * c, 0, 20 * s], [0, 20, 0], 20, "ramp"); // rises 10 mm over 17 mm, faces 30° from up
  quad(m, [-80, -80, 0.6], [30, 0, 0], [0, 30, 0], 15, "shim"); // a shim 0.6 mm thick lying flat
  quad(m, [-80, 40, 0.15], [30, 0, 0], [0, 30, 0], 15, "foil"); // a foil 0.15 mm thick: below the demo's limit, unsaid
  quad(m, [30, 50, 0.35], [20, 0, 0], [0, 20, 0], 10, "sticker"); // a sticker 0.35 mm thick: drawn and listed
  return m;
}

const up: Plane = { n: [0, 0, 1], d: 0 };

{
  const m = scene();
  const r = mockTable(new Float32Array(m.P), new Uint32Array(m.I), up);
  check(r.ok, "a box on a table: the table is found");
  if (r.ok) {
    const gone = new Set(r.faces);
    const count = (k: string, g: boolean) => m.kind.filter((x, f) => x === k && gone.has(f) === g).length;
    check(count("table", false) === 0, `${count("table", false)} faces of the table left`);
    check(count("wall", true) === 0 && count("top", true) === 0, "the box's walls and top stay, right down to the table");
    // the ramp faces the table's way: its first 1 mm goes (the margin), the rest stays
    const P = m.P;
    const rampGone = m.kind.map((k, f) => (k === "ramp" && gone.has(f) ? f : -1)).filter((f) => f >= 0);
    const maxH = Math.max(...rampGone.map((f) => (P[m.I[f * 3] * 3 + 2] + P[m.I[f * 3 + 1] * 3 + 2] + P[m.I[f * 3 + 2] * 3 + 2]) / 3));
    check(rampGone.length > 0 && maxH <= 1, `the ramp is cut at the margin (up to ${maxH.toFixed(2)} mm)`);
    check(r.info.margin_mm === 1 && r.info.n_faces === r.faces.length && r.info.share > 0.5, "the numbers");
    check(r.info.lowest_mm !== null && r.info.lowest_mm <= 0.3, `the part is kept down to the table (${r.info.lowest_mm})`);
    // a thin part lying flat stays, and is said; one within the band goes, and is said too
    check(count("shim", true) === 0, `${count("shim", true)} faces of the shim removed`);
    check(count("foil", false) === 0, "the foil, within the demo's band, goes with the table");
    const kept = r.info.raised.filter((x) => x.kept);
    check(kept.length === 1 && Math.abs(kept[0].height_mm - 0.6) < 0.01 && Math.abs(kept[0].size_mm - 30) < 5, JSON.stringify(r.info.raised));
    check(r.info.warnings.some((w) => w.startsWith("A thin part lying flat is kept")), r.info.warnings.join(" | "));
    // the sticker goes with the table, but is a warned area with its faces; a point on it keeps exactly it
    const warned = r.info.raised.filter((x) => x.verdict === "warned");
    check(warned.length === 1 && Math.abs(warned[0].height_mm - 0.35) < 0.01 && count("sticker", false) === 0, JSON.stringify(warned));
    check(r.areas.length === 2 && r.areas.some((a) => a.verdict === "warned" && a.faces.length === 200), "the drawn areas carry their faces");
    const k = mockTable(new Float32Array(m.P), new Uint32Array(m.I), up, 1, [[40, 60, 0.35]]);
    check(k.ok && k.faces.length === r.faces.length - 200 && m.kind.every((x, f) => (x === "sticker") === (gone.has(f) && !new Set(k.ok ? k.faces : []).has(f))), "Keep this keeps the sticker and nothing else");
    check(k.ok && k.info.raised.some((x) => x.by_user && x.verdict === "kept"), "said as kept by the user");
    check(typeof r.info.thin_limit_mm === "number" && !!r.info.limit, "the demo's own limit is said");
    check(r.faces.every((f, i) => i === 0 || f > r.faces[i - 1]), "the faces ascend");
    const r3 = mockTable(new Float32Array(m.P), new Uint32Array(m.I), up, 3);
    check(r3.ok && r3.faces.length > r.faces.length, "a larger margin takes more of the ramp");
  }
}

{
  const m = scene(false);
  const r = mockTable(new Float32Array(m.P), new Uint32Array(m.I), up);
  check(!r.ok && r.detail.startsWith("No table found") && r.detail.includes("Nothing was removed") && r.hint.includes("select"), "no table: refused in words");
}

{
  const p = tablePlane({ R: [1, 0, 0, 0, 1, 0, 0, 0, 1], t: [0, 0, 0] });
  check(p.n[0] === -0 && p.n[1] === -1 && p.n[2] === -0 && p.d === 0, "the scene's table: y = 0, the parts at y < 0");
  const q = tablePlane({ R: [1, 0, 0, 0, 0, -1, 0, 1, 0], t: [0, 0, 100] });
  check(q.n[0] === 0 && q.n[1] === 0 && q.n[2] === -1, "turned with the pose");
  check(Math.abs(q.n[0] * 0 + q.n[1] * 0 + q.n[2] * 100 + q.d) < 1e-9, "through the moved origin");
}

console.log(`table.test (mock): ${checks} checks passed`);
