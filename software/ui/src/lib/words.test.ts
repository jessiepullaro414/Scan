/**
 * One name for each thing (UX_PLAN.md, the table "One name for each thing"; `npm test`): the names the glossary
 * retired do not come back in what the pages say. Reads the page sources as text (not run): every string literal and
 * every piece of JSX text, without comments, imports and bare identifiers (a word with no space and no capital, like a
 * key or a step id). The server has the same guard (`tests/test_server_error_words.py`).
 *
 * The demo (`api/mock`) is left out: where it copies the engine's own sentences (the calibration's health, the
 * comparison's verdicts, the preset cards) it says what the engine says, old words and all, until the engine changes.
 * So are the API's types (`api/types.ts`): their strings are the JSON values the pages compare against.
 */
const fs = (await import(["node", "fs"].join(":"))) as {
  readFileSync: (u: URL, enc: "utf8") => string;
  readdirSync: (u: URL, o: { withFileTypes: true }) => { name: string; isDirectory: () => boolean }[];
};

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`words.test: ${what}`);
}

/** The retired names, with the name that took their place. */
export const RETIRED: [RegExp, string][] = [
  [/\bstereo\b/i, "the cameras (the Cameras step, the camera calibration)"],
  [/still holds|2-minute check|launch check/i, "Calibration check / Calibration valid / start-up check"],
  [/\bsigma\b|(?<!1)σ/i, "scatter (the number) or confidence (the map); 1σ only where it says what a ± is"],
  [/loose pieces|floaters?\b/i, "loose bits"],
  [/\b(part's|scan's|scan's own|reference's) frame\b|\bcoordinate system\b|\b(first|raw) camera frame/i, "the part's axes / the scan's axes"],
  [/\breferences?\b(?! parts)/i, "the CAD model"],
  [/(?<!on a |for a |\(|RPI_|Raspberry Pi )\bPico\b(?! or ESP32|\))/, "the control board (Pico or ESP32)"],
  [/\bemitters?\b|\bdot projector\b/i, "the lasers and dots"],
  [/\blaser planes?\b/i, "laser lines"],
  [/\bvoxels?\b/i, "mesh detail"],
  [/\bremake\b|\brebuild the mesh\b|\bre-run\b|\bstep by step\b|\bfine-tune\b|\bProcess page\b|\bin Process\b|\bOpen Process\b/i, "Make mesh / Make mesh again / Edit mesh"],
  [/\bRun Track\b|\bTrack (first|again|stage|quality)\b|\bfinished Track\b/, "Follow the camera"],
  [/\bbase mesh\b|\btracked mesh\b|\blive model\b/i, "the starting mesh / the live preview"],
  [/\bgyro\b/i, "the motion sensor"],
  [/\bIMU\b(?!\))/, "the motion sensor (IMU)"],
  [/\bRMS\b/, "typical / typically"],
  [/\balign two scans\b|\bfused again\b/i, "Align and merge"],
  [/\bsandbox\b|\bsimulator demo\b/i, "the simulator"],
  [/\bkept fits?\b|\bCheck answer\b|\bCheck panel\b/, "saved fitted shapes / the Scan check"],
];

/** `s` without its template placeholders (`${...}`, nested ones too). */
function unplaced(s: string): string {
  let out = "";
  let depth = 0;
  for (let i = 0; i < s.length; i++) {
    if (depth === 0 && s[i] === "$" && s[i + 1] === "{") {
      depth = 1;
      i++;
    } else if (depth > 0) {
      if (s[i] === "{") depth++;
      else if (s[i] === "}") depth--;
    } else out += s[i];
  }
  return out;
}

/**
 * The strings and JSX text of a source, with their line numbers (rough, but enough for words). Left out: a string the
 * code matches against (`.includes("…")`, `.test(…)`) and an object's key (`{ "dot projector": … }`).
 */
export function texts(source: string): { line: number; text: string }[] {
  const out: { line: number; text: string }[] = [];
  const re = /"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`|>([^<>{}\n]*[A-Za-z][^<>{}\n]*)</g;
  source.split("\n").forEach((raw, i) => {
    const s = raw.trim();
    if (s.startsWith("//") || s.startsWith("*") || s.startsWith("/*") || s.startsWith("import ")) return;
    const code = raw.replace(/\s\/\/\s.*$/, "");
    // a line of JSX text on its own, between tags on other lines
    if (/^[A-Za-z][^<>{}=;()"'`]*$/.test(s) && /\s/.test(s) && !/^(return|export|const|let|type|case|else|import|from)\b/.test(s) && !/^\w+:\s/.test(s) && !/[,[]$/.test(s))
      out.push({ line: i + 1, text: s });
    // JSX text at the start of a line, before a tag on the same line
    const lead = /^([A-Za-z][^<>{}=;()"'`]*)</.exec(s);
    if (lead && /\s/.test(lead[1].trim())) out.push({ line: i + 1, text: lead[1] });
    for (const m of code.matchAll(re)) {
      const at = m.index ?? 0;
      const before = code.slice(0, at);
      const after = code.slice(at + m[0].length);
      if (m[1] === undefined && /(includes|startsWith|endsWith|indexOf|test)\($/.test(before)) continue;
      if (m[1] === undefined && /(^|[{,])\s*$/.test(before) && /^\s*:/.test(after)) continue;
      const t = unplaced(m[1] ?? m[0].slice(1, -1));
      if (/\s/.test(t.trim()) || /^[A-Z]/.test(t.trim())) out.push({ line: i + 1, text: t });
    }
  });
  return out;
}

export function retired(source: string, file: string): string[] {
  return texts(source).flatMap(({ line, text }) =>
    RETIRED.filter(([rx]) => rx.test(text)).map(([, why]) => `${file}:${line}: "${text.trim().slice(0, 90)}": ${why}`),
  );
}

// the guard itself: it finds the old words (each of these was on a page before the names were settled) and lets the
// new ones through
const OLD = [
  'title: "Stereo",',
  "<b>Run a 2-minute check any time</b>",
  'rows.push(["Median sigma", fmtSigma(x), "1σ"]);',
  '"Removing floaters…"',
  'const where = frame ? "in the part\'s frame" : "in the scan\'s frame";',
  '<Panel title="Compare with a reference" icon={X}>',
  'label: "Pico not found"',
  '{ label: "Dot projector", short: "Dot projector" }',
  'label="Voxel size"',
  'label="Fine-tune in Process"',
  'runLabel={track.state === "none" ? "Run Track" : "Re-run"}',
  'const onScreen = "The tracked mesh stays on screen";',
  'help: "How well the gyro\'s rotations match."',
  'label="IMU temperature"',
  '["Position RMS", x]',
  '{sim ? "Simulator sandbox" : "First run"}',
  'title="The kept fits as a STEP file"',
  "            Open in Process",
  '", within 2σ"',
];
for (const o of OLD) check(retired(o, "old").length > 0, `the guard finds: ${o}`);
const NEW = [
  'title: "Cameras",',
  'label="Motion sensor (IMU)"',
  'help: "± (1σ) is how far the fitted shape could move."',
  'text: "the control board (Pico or ESP32)"',
  'navigate(paths.setup("stereo"))',
  'pm: "measured against reference parts."',
  'label="Mesh detail"',
  'if (rest.includes("voxel size")) return "x";',
  'const L = { "dot projector": "Dots" };',
  'const t = `${sigma === null ? "" : ` ± ${s}`} mm`;',
];
for (const n of NEW) check(retired(n, "new").length === 0, `the guard lets through: ${n} (${retired(n, "new").join("; ")})`);

// ---------------------------------------------------------------- the pages' sources
/** Words that stay on purpose. */
const ALLOWED: RegExp[] = [
  /MicroPython|BOOTSEL|\.uf2|RPI-RP2|lsm6dsox|plug the Pico into USB/, // the firmware steps, for the Pico board itself
  /\bohs calibrate stereo\b/, // the command line keeps its names
  /^lib\/section\.ts:\d+: "Section at/, // the engine's own description of a section (ohs/mesh/section.py)
  /"You can no longer re-run single steps on the Edit mesh page/, // UX_PLAN U37's own words
];

const src = new URL("../", import.meta.url);
function walk(dir: URL, rel = ""): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    if (e.isDirectory()) return e.name === "mock" ? [] : walk(new URL(`${e.name}/`, dir), `${rel}${e.name}/`);
    const f = `${rel}${e.name}`;
    return /\.tsx?$/.test(f) && !/\.test\.ts$/.test(f) && !/\.d\.ts$/.test(f) && f !== "api/types.ts" ? [f] : [];
  });
}
const files = walk(src);
check(files.length > 100, `the pages' sources are found (${files.length})`);
const found = files.flatMap((f) => retired(fs.readFileSync(new URL(f, src), "utf8"), f)).filter((p) => !ALLOWED.some((a) => a.test(p)));
check(found.length === 0, `retired names on the pages (UX_PLAN, One name for each thing):\n${found.join("\n")}`);

console.log(`words.test: ${checks} checks passed`);
