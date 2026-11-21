/** Checks for what this computer can do, said before something is missing (UX_PLAN U5; `npm test`). */
import { computerLine, noConfidenceText } from "./computer.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`check failed: ${what}`);
}

// with a graphics card: its name, and every feature works
const gpu = computerLine({ backend: "gpu", status: "GPU: AMD Radeon 680M (integrated, 4096 MB, AMD Accelerated Parallel Processing)", device: { name: "AMD Radeon 680M", kind: "integrated" } });
check(gpu !== null && gpu.text === "This computer: graphics card found (AMD Radeon 680M). Every feature works." && gpu.missing.length === 0 && gpu.tone === "ok", JSON.stringify(gpu));

// without: why, in plain words, and what is not available (checked against the engine since D7)
const cpu = computerLine({ backend: "cpu", status: "no OpenCL GPU was found: everything runs on the CPU", device: null });
check(cpu !== null && cpu.text === "This computer: no graphics card found. Scans work; without one:", JSON.stringify(cpu));
check(cpu!.missing.length === 4 && cpu!.tone === "warn", JSON.stringify(cpu!.missing));
check(cpu!.missing.some((m) => /confidence/.test(m) && /Scan check/.test(m)), "no confidence, so no Scan check");
check(cpu!.missing.some((m) => /Thin walls and edges/.test(m)), "thin walls");
check(cpu!.missing.some((m) => /tracking/i.test(m)), "tracking on plain parts");
check(cpu!.missing.some((m) => /longer/.test(m)), "slower");
const off = computerLine({ backend: "cpu", status: 'the GPU is turned off (compute.device = "cpu"): everything runs on the CPU' });
check(off!.text === "This computer: the graphics card is turned off in the settings. Scans work; without one:", off!.text);
check(computerLine({ backend: "cpu", status: "PyOpenCL is not installed (pip install pyopencl): everything runs on the CPU" })!.text.includes("support for graphics cards is not installed"), "no PyOpenCL");
check(computerLine({ backend: "cpu", status: "no OpenCL driver was found (x): everything runs on the CPU" })!.text.includes("no driver for a graphics card was found"), "no driver");
check(computerLine({ backend: "cpu", status: "no GPU matches compute.device = 'RTX': everything runs on the CPU" })!.text.includes("none matches the one named in the settings"), "no match");
check(computerLine({ backend: "cpu", status: "no GPU passed the OpenCL check (x): everything runs on the CPU" })!.text.includes("did not pass the app's check"), "failed probe");
// the engine's own words never show: no "GPU", "OpenCL", "pip", "CPU", "compute.device"
for (const c of [cpu, off]) for (const s of [c!.text, ...c!.missing]) check(!/GPU|OpenCL|pip |CPU|compute\./.test(s), s);
check(computerLine({ backend: "cpu", status: "not checked yet" })!.text === "This computer: looking for a graphics card…", "still checking");
check(computerLine(null) === null && computerLine("x") === null, "nothing known");

// "make it again" only where it would help: on this computer only when it has a graphics card now
check(noConfidenceText(true, "viewer") === "This mesh has no confidence data: it was made without a graphics card or by an earlier version. Make the mesh again to get it.", noConfidenceText(true, "viewer"));
check(noConfidenceText(false, "viewer") === "This mesh has no confidence data: it was made without a graphics card or by an earlier version. This computer has none, so making it again here does not add it.", noConfidenceText(false, "viewer"));
check(noConfidenceText(true, "process").endsWith("Run “Follow the camera” again to get it."), "Edit mesh's words");
check(noConfidenceText(null, "viewer").endsWith("Make the mesh again on a computer with a graphics card to get it."), "unknown");

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

console.log(`computer.test (pages): ${checks} checks passed`);
