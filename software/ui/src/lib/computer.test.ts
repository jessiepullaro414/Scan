/** Checks for what this computer can do, said before something is missing (UX_PLAN U5; `npm test`). */
import { computerLine, noCheckCallout, noConfidenceText, thinWallsNote } from "./computer.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`check failed: ${what}`);
}

// with a graphics card: its name, and every feature works
const gpu = computerLine({ backend: "gpu", status: "GPU: AMD Radeon 680M (integrated, 4096 MB, AMD Accelerated Parallel Processing)", device: { name: "AMD Radeon 680M", kind: "integrated" } });
// R12: the card's own name (the engine's can be a code name, "gfx1035") only where asked for: Settings' About
check(gpu !== null && gpu.text === "This computer: graphics card found. Every feature works." && gpu.missing.length === 0 && gpu.tone === "ok", JSON.stringify(gpu));
const named = computerLine({ backend: "gpu", status: "GPU: gfx1035", device: { name: "gfx1035" } }, { name: true });
check(named!.text === "This computer: graphics card found (gfx1035). Every feature works.", named!.text);
check(!computerLine({ backend: "gpu", status: "GPU: gfx1035", device: { name: "gfx1035" } })!.text.includes("gfx1035"), "Home's line: no code name");

// without: why, in plain words, and what is not available (checked against the engine since D7)
const cpu = computerLine({ backend: "cpu", status: "no OpenCL GPU was found: everything runs on the CPU", device: null });
check(cpu !== null && cpu.text === "This computer: no graphics card found. Scans still work, with these limits:", JSON.stringify(cpu));
check(cpu!.missing.length === 4 && cpu!.tone === "warn", JSON.stringify(cpu!.missing));
check(cpu!.missing.some((m) => /confidence/.test(m) && /Scan check/.test(m)), "no confidence, so no Scan check");
check(cpu!.missing.some((m) => /Thin walls and edges/.test(m)), "thin walls");
check(cpu!.missing.some((m) => /tracking/i.test(m)), "tracking on plain parts");
check(cpu!.missing.some((m) => /longer/.test(m)), "slower");
const OFF = 'the GPU is turned off (compute.device = "cpu"): everything runs on the CPU';
// R20: who turned it off (`preference_from`), in plain words for each case
const off = computerLine({ backend: "cpu", status: OFF, preference_from: "settings" });
check(off!.text === "This computer: the graphics card is turned off in the app's settings. Scans still work, with these limits:", off!.text);
const offEnv = computerLine({ backend: "cpu", status: OFF, preference_from: "environment" });
check(offEnv!.text === "This computer: the graphics card is turned off for this run, by a setting made outside the app. Scans still work, with these limits:", offEnv!.text);
// an engine from before (no `preference_from`): the old words, which name neither
check(computerLine({ backend: "cpu", status: OFF })!.text.includes("turned off for this app (in its settings, or for this run)"), "an older engine");
// nobody chose (auto) and no card in use: none was found, whatever the status says
const auto = computerLine({ backend: "cpu", status: "no OpenCL GPU was found: everything runs on the CPU", preference_from: "auto" });
check(auto!.text === "This computer: no graphics card found. Scans still work, with these limits:", auto!.text);
// a setting (or this run) asked for a graphics card that was not there: the status says which case
check(computerLine({ backend: "cpu", status: "no GPU matches compute.device = 'RTX': everything runs on the CPU", preference_from: "settings" })!.text.includes("none matches the one named in the app's settings"), "the settings name a card that is not there");
check(computerLine({ backend: "cpu", status: "no GPU matches compute.device = 'RTX': everything runs on the CPU", preference_from: "environment" })!.text.includes("none matches the one named for this run, outside the app"), "this run names a card that is not there");
// with a card in use the source does not matter
check(computerLine({ backend: "gpu", status: "GPU: x", device: { name: "x" }, preference_from: "environment" })!.text === "This computer: graphics card found. Every feature works.", "a card in use");
check(computerLine({ backend: "cpu", status: "PyOpenCL is not installed (pip install pyopencl): everything runs on the CPU" })!.text.includes("support for graphics cards is not installed"), "no PyOpenCL");
check(computerLine({ backend: "cpu", status: "no OpenCL driver was found (x): everything runs on the CPU" })!.text.includes("no driver for a graphics card was found"), "no driver");
check(computerLine({ backend: "cpu", status: "no GPU matches compute.device = 'RTX': everything runs on the CPU" })!.text.includes("none matches the one named in the settings"), "no match");
check(computerLine({ backend: "cpu", status: "no GPU passed the OpenCL check (x): everything runs on the CPU" })!.text.includes("did not pass the app's check"), "failed probe");
// the engine's own words never show: no "GPU", "OpenCL", "pip", "CPU", "compute.device"
for (const c of [cpu, off, offEnv, auto]) for (const s of [c!.text, ...c!.missing]) check(!/GPU|OpenCL|pip |CPU|compute\.|OHS_COMPUTE|environment/.test(s), s);
check(computerLine({ backend: "cpu", status: "not checked yet" })!.text === "This computer: looking for a graphics card…", "still checking");
check(computerLine(null) === null && computerLine("x") === null, "nothing known");

// "make it again" only where it would help: on this computer only when it has a graphics card now
check(noConfidenceText(true, "viewer") === "This mesh has no confidence data: it was made without a graphics card or by an earlier version. Make the mesh again to get it.", noConfidenceText(true, "viewer"));
check(noConfidenceText(false, "viewer") === "This mesh has no confidence data: it was made without a graphics card or by an earlier version. This computer has none, so making it again here does not add it.", noConfidenceText(false, "viewer"));
check(noConfidenceText(true, "process").endsWith("Run “Follow the camera” again to get it."), "Edit mesh's words");
check(noConfidenceText(null, "viewer").endsWith("Make the mesh again on a computer with a graphics card to get it."), "unknown");

// UX_PLAN R16: the Scan check panel without confidence: its name, the reason on the page, no button that cannot help
const none = noCheckCallout(false);
check(none.title === "Scan check can't be used on this mesh", none.title);
check(!none.offerRemake && /does not add it/.test(none.text) && /Confidence shading/.test(none.text), none.text);
check(noCheckCallout(true).offerRemake && noCheckCallout(null).offerRemake, "a computer with a card (or not known): the button stays");
check(!/\bCheck can't\b/.test(none.title.replace(/^Scan check/, "")), "never the old name alone");
check(thinWallsNote(false) === "“Thin walls and edges” has no effect on this computer: it needs a graphics card.", "thin walls, said");
check(thinWallsNote(true) === null && thinWallsNote(null) === null, "with a card: nothing to say");

console.log(`computer.test: ${checks} checks passed`);

// U5: "graphics card" only, in what the pages say (the leftovers the second wave noted: Settings, two tooltips)
{
  const fs = (await import(["node", "fs"].join(":"))) as { readFileSync: (u: URL, enc: "utf8") => string };
  for (const page of ["../pages/SettingsPage.tsx", "../pages/ViewerPage.tsx", "../pages/ProcessPage.tsx", "../pages/process/EditsPanel.tsx", "../pages/viewer/CheckPanel.tsx", "../pages/HomePage.tsx"]) {
    const said = fs
      .readFileSync(new URL(page, import.meta.url), "utf8")
      .split("\n")
      .filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l))
      .map((l) => l.replace(/\s\/\/\s.*$/, "").replace(/\{\/\*.*?\*\/\}/g, ""));
    const bad = said.filter((l) => /\bGPU\b/.test(l));
    check(bad.length === 0, `${page} says "GPU": ${bad.join(" | ")}`);
  }
}

// R16: the Scan check panel takes its words from noCheckCallout (no "Check can't be used" of its own)
{
  const fs = (await import(["node", "fs"].join(":"))) as { readFileSync: (u: URL, enc: "utf8") => string };
  const panel = fs.readFileSync(new URL("../pages/viewer/CheckPanel.tsx", import.meta.url), "utf8");
  check(!/Check can't be used/.test(panel) && /noCheckCallout\(/.test(panel), "CheckPanel says “Scan check can't be used…” from noCheckCallout");
}

console.log(`computer.test (pages): ${checks} checks passed`);
