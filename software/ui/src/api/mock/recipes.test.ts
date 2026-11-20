/**
 * The demo's built-in recipes against the engine's `BUILTIN_RECIPES` (UX_PLAN U13 / U31, `npm test`):
 * the same recipes, the same edits in the same order, the same sizes. Reads the engine's file as text
 * (it is not run).
 */
import { BUILTINS } from "./recipes.ts";

// node's fs without node's types (the UI's tsconfig has none): a plain dynamic import
const fs = (await import(["node", "fs"].join(":"))) as { readFileSync: (u: URL, enc: "utf8") => string };

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`check failed: ${what}`);
}

const py = fs.readFileSync(new URL("../../../../ohs/server/processing.py", import.meta.url), "utf8");
const start = py.indexOf("BUILTIN_RECIPES: list[dict] = [");
check(start >= 0, "the engine's BUILTIN_RECIPES is where it was");
const end = py.indexOf("\n]\n", start);
const src = py.slice(start, end);
const blocks = src.split(/\{"id": "/).slice(1);
type EngineOp = { op: string; params: string };
const engine: { id: string; ops: EngineOp[]; quality: string | undefined }[] = blocks.map((b: string) => ({
  id: b.slice(0, b.indexOf('"')),
  ops: [...b.matchAll(/\{"op": "(\w+)", "params": \{([^}]*)\}/g)].map((m) => ({ op: m[1], params: m[2] })),
  quality: /"quality": "(\w+)"/.exec(b)?.[1],
}));

check(engine.length === BUILTINS.length, `as many recipes as the engine (${engine.length})`);
const num = (params: string, key: string) => {
  const m = new RegExp(`"${key}": ([\\d._]+)`).exec(params);
  return m ? Number(m[1].replace(/_/g, "")) : undefined;
};
for (const e of engine) {
  const d = BUILTINS.find((r) => r.id === e.id);
  check(!!d, `the demo has "${e.id}"`);
  if (!d) continue;
  check(d.settings.quality === e.quality, `${e.id}: quality ${e.quality}`);
  check(d.ops.map((o) => o.op).join() === e.ops.map((o) => o.op).join(), `${e.id}: the same edits in the same order`);
  e.ops.forEach((o, i) => {
    const p = d.ops[i]?.params ?? {};
    for (const key of ["min_size_mm", "min_fraction", "max_hole_mm", "strength", "target_triangles", "ratio"]) {
      const want = num(o.params, key);
      check(want === undefined ? p[key] === undefined : p[key] === want, `${e.id}, ${o.op}: ${key} ${want}`);
    }
    check(!!p.keep_largest_only === /"keep_largest_only": True/.test(o.params), `${e.id}, ${o.op}: largest piece only as the engine`);
  });
}

console.log(`recipes.test: ${checks} checks passed`);
