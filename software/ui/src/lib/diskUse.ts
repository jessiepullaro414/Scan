/**
 * The words for what a project and its scans take on disk (FEATURE_PLAN R1,
 * `GET /api/projects/{pid}/disk`). It informs only: nothing here removes anything.
 */
import type { ProjectDiskUse, ScanDiskUse } from "../api/types";
import { fmtBytes } from "./format.ts";

export type DiskPart = { key: "recording" | "stages" | "undo" | "result" | "other"; label: string; bytes: number; detail?: string };

const frames = (n: number) => `${n} frame${n === 1 ? "" : "s"}`;

/** A scan's non-empty parts, in the order the page shows them; they add up to `bytes`. */
export function diskParts(s: ScanDiskUse): DiskPart[] {
  const stages = Object.entries(s.stages).filter(([, b]) => b > 0);
  const all: DiskPart[] = [
    { key: "recording", label: "Recording", bytes: s.recording, detail: s.n_frame_files ? frames(s.n_frame_files) : undefined },
    {
      key: "stages",
      label: "Processing stages",
      bytes: s.stages_bytes,
      detail: stages.length ? stages.map(([name, b]) => `${name} ${fmtBytes(b)}`).join(", ") : undefined,
    },
    { key: "undo", label: "Undo history", bytes: s.undo },
    { key: "result", label: "Result", bytes: s.result },
    { key: "other", label: "Other files", bytes: s.other },
  ];
  return all.filter((p) => p.bytes > 0);
}

/** The hover text of a scan's size: the total, then one line per part. */
export function scanDiskTitle(s: ScanDiskUse): string {
  if (!s.bytes) return "Nothing on disk yet";
  const lines = diskParts(s).map((p) => `${p.label}: ${fmtBytes(p.bytes)}${p.detail ? ` (${p.detail})` : ""}`);
  return [`${fmtBytes(s.bytes)} on disk`, ...lines].join("\n");
}

/** One line for the whole project: the total and its main parts. */
export function projectDiskText(d: ProjectDiskUse): string {
  if (!d.bytes) return "Nothing on disk yet";
  const parts = [
    ["recordings", d.parts.recording],
    ["processing stages", d.parts.stages],
    ["undo history", d.parts.undo],
    ["results", d.parts.result],
    ["reference models", d.references],
  ] as const;
  const shown = parts.filter(([, b]) => b > 0).map(([label, b]) => `${label} ${fmtBytes(b)}`);
  return `${fmtBytes(d.bytes)} on disk${shown.length ? `: ${shown.join(", ")}` : ""}`;
}

/** What the trash holds (it is not part of the project), or null when it is empty. */
export function trashText(d: ProjectDiskUse): string | null {
  if (!d.trash.bytes) return null;
  const n = d.trash.projects;
  return `${n} deleted project${n === 1 ? "" : "s"} in the .trash folder take${n === 1 ? "s" : ""} ${fmtBytes(d.trash.bytes)}. The app never empties it.`;
}
