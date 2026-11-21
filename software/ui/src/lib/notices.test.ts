/**
 * Checks for the engine's notices (UX_PLAN R14, `npm test`): a notice's own sentence stands as the toast's title, with
 * no "Engine error" or "Scanner problem" above it, and no page adds such a title. Plain TypeScript run by Node.
 */
import { engineNoticeToast } from "./notices.ts";

const fs = (await import(["node", "fs"].join(":"))) as {
  readFileSync: (u: URL, enc: "utf8") => string;
  readdirSync: (u: URL, o: { withFileTypes: true }) => { name: string; isDirectory: () => boolean }[];
};

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`notices.test: ${what}`);
}

const GONE = "The recording could not be read any more (its folder was moved or deleted). The scan was stopped and saved.";
const t = engineNoticeToast("error", GONE);
check(!!t && t.kind === "error" && t.title === GONE && !t.detail, `the sentence is the title: ${JSON.stringify(t)}`);
check(engineNoticeToast("error", "  ") === null, "an empty notice: nothing");
check(engineNoticeToast("info", "Mesh ready: “Bracket”.") === null && engineNoticeToast("warn", "Tracking lost") === null, "only errors become a toast, as before");
// the same notice twice replaces itself instead of stacking
check(engineNoticeToast("error", GONE)!.key === t!.key, "a key per sentence");

// no page, store or wiring adds such a title (the mock's own texts aside: it has none either)
const banned = /["'`](Engine error|Scanner problem|Engine problem)\b/;
const bad: string[] = [];
function walk(dir: URL): void {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.isDirectory()) walk(new URL(`${e.name}/`, dir));
    else if (/\.tsx?$/.test(e.name) && !/\.test\.ts$/.test(e.name)) {
      const text = fs.readFileSync(new URL(e.name, dir), "utf8");
      if (banned.test(text)) bad.push(`${dir.pathname.replace(/^.*\/src\//, "")}${e.name}`);
    }
  }
}
walk(new URL("../", import.meta.url));
check(bad.length === 0, `a title above the engine's sentence: ${bad.join(", ")}`);

console.log(`notices.test: ${checks} checks passed`);
