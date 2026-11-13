/**
 * Which mesh the Scan page shows after a stop, while and after "Make mesh" runs (ScanPage's
 * LiveCanvas and PostScanCard):
 *
 * - `live`: the live model (no job yet, or its Track stage is not done);
 * - `tracked`: the tracked mesh, as soon as this job's Track stage is done. The later stages
 *   (straighten, refine with lasers, the recipe's edits) improve it. It stays when the job is
 *   then cancelled or fails: it is the scan's saved mesh;
 * - `finished`: the job is done.
 *
 * Imports nothing at run time, so `npm test` checks it with plain node.
 */
import type { Job, ProcessState } from "../api/types";

export type ScanMeshStage = "live" | "tracked" | "finished";

export function scanMeshStage(
  job: Pick<Job, "id" | "state"> | null | undefined,
  state: Pick<ProcessState, "stages" | "current"> | null | undefined,
): ScanMeshStage {
  if (!job) return "live";
  if (job.state === "done") return "finished";
  const track = state?.stages?.track;
  // the Track stage records the job that ran it: an earlier job's mesh is not this one's
  return track && track.state === "done" && track.job_id === job.id && state?.current ? "tracked" : "live";
}

/** The mesh file's address for one version of the mesh, so a remade mesh is never read from the browser's cache. */
export function versionedUrl(url: string, version: number | null | undefined): string {
  return version === null || version === undefined ? url : `${url}${url.includes("?") ? "&" : "?"}v=${version}`;
}
