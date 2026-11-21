/**
 * Checks for the disk-use words of the project page (FEATURE_PLAN R1; `npm test`). Plain TypeScript
 * that Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { ProjectDiskUse, ScanDiskUse } from "../api/types.ts";
import { diskParts, projectDiskText, scanDiskTitle, trashText } from "./diskUse.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`diskUse.test: ${what}`);
}

const MB = 1024 * 1024;
const GB = 1024 * MB;

const scan = (over: Partial<ScanDiskUse> = {}): ScanDiskUse => ({
  id: "scan-1",
  name: "Scan 1",
  n_frames: 734,
  duration_s: 24.5,
  bytes: 0,
  recording: 0,
  frames: 0,
  n_frame_files: 0,
  stages: {},
  stages_bytes: 0,
  undo: 0,
  result: 0,
  other: 0,
  ...over,
});

const full = scan({
  bytes: Math.round(1.25 * GB) + 120 * MB + 76 * MB + 65 * MB + 3 * MB,
  recording: Math.round(1.25 * GB),
  frames: Math.round(1.24 * GB),
  n_frame_files: 734,
  stages: { track: 23 * MB, straighten: 97 * MB },
  stages_bytes: 120 * MB,
  undo: 76 * MB,
  result: 65 * MB,
  other: 3 * MB,
});

// the parts, in the order the page shows them, and they add up
const parts = diskParts(full);
check(parts.map((p) => p.key).join(",") === "recording,stages,undo,result,other", "parts in order");
check(parts.reduce((a, p) => a + p.bytes, 0) === full.bytes, "parts add up to the scan");
check(parts[0].label === "Recording" && parts[1].label === "Processing stages" && parts[2].label === "Undo history", "labels");
check(parts[1].detail === "track 23.0\u202fMB, straighten 97.0\u202fMB", `stage detail: ${parts[1].detail}`);
check(parts[0].detail === "734 frames", `recording detail: ${parts[0].detail}`);

// empty parts are left out; a scan with nothing on disk has no parts
check(diskParts(scan({ bytes: 10, result: 10 })).map((p) => p.key).join(",") === "result", "only the non-empty parts");
check(diskParts(scan()).length === 0, "nothing on disk");

// the hover text: one line per part, plain words
const title = scanDiskTitle(full);
const lines = title.split("\n");
check(lines[0] === "1.51\u202fGB on disk", `first line: ${lines[0]}`);
check(lines[1] === "Recording: 1.25\u202fGB (734 frames)", `recording line: ${lines[1]}`);
check(lines[2] === "Processing stages: 120.0\u202fMB (track 23.0\u202fMB, straighten 97.0\u202fMB)", `stages line: ${lines[2]}`);
check(lines[3] === "Undo history: 76.0\u202fMB", `undo line: ${lines[3]}`);
check(lines[4] === "Result: 65.0\u202fMB", `result line: ${lines[4]}`);
check(lines[5] === "Other files: 3.0\u202fMB", `other line: ${lines[5]}`);
check(scanDiskTitle(scan()) === "Nothing on disk yet", "empty scan title");
check(scanDiskTitle(scan({ bytes: 1, recording: 1, n_frame_files: 1 })).includes("(1 frame)"), "one frame, singular");

// the project's line
const project: ProjectDiskUse = {
  project_id: "p",
  bytes: full.bytes + 5 * MB,
  scans: [full, scan({ id: "scan-2" })],
  parts: { recording: full.recording, stages: full.stages_bytes, undo: full.undo, result: full.result },
  references: 4 * MB,
  other: 1 * MB,
  trash: { bytes: 0, projects: 0 },
  seconds: 0.05,
};
const text = projectDiskText(project);
check(text === "1.51\u202fGB on disk: recordings 1.25\u202fGB, processing stages 120.0\u202fMB, undo history 76.0\u202fMB, results 65.0\u202fMB, CAD models 4.0\u202fMB", `project text: ${text}`);
check(projectDiskText({ ...project, bytes: 0, parts: { recording: 0, stages: 0, undo: 0, result: 0 }, references: 0, other: 0 }) === "Nothing on disk yet", "empty project");

// the trash, only when it holds something
check(trashText(project) === null, "an empty trash says nothing");
check(trashText({ ...project, trash: { bytes: 2 * GB, projects: 1 } }) === "1 deleted project in the trash takes 2.00\u202fGB. Nothing in it is deleted until you empty it (Trash, on Home).", "one project in the trash");
check(trashText({ ...project, trash: { bytes: 512 * MB, projects: 3 } })!.startsWith("3 deleted projects"), "plural");
// U7: deleted scans are in the trash too
check(trashText({ ...project, trash: { bytes: 512 * MB, projects: 1, scans: 2 } })!.startsWith("1 deleted project and 2 deleted scans in the trash take 512.0\u202fMB."), "projects and scans");
check(trashText({ ...project, trash: { bytes: 512 * MB, projects: 0, scans: 1 } })!.startsWith("1 deleted scan in the trash takes"), "one scan");

console.log(`diskUse.test: ${checks} checks passed`);
