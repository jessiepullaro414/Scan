/**
 * Checks for which mesh the Scan page shows during "Make mesh" (`npm test`). Plain TypeScript that
 * Node runs as it is (type stripping), no test framework: a failed check throws.
 */
import type { Job, ProcessState } from "../api/types.ts";
import { scanMeshStage, versionedUrl } from "./scanMesh.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`scanMesh.test: ${what}`);
}

const job = (state: Job["state"], id = "j1"): Pick<Job, "id" | "state"> => ({ id, state });
const proc = (trackState: string, jobId: string | null, hasMesh = true) =>
  ({
    stages: { track: { state: trackState, summary: null, when: null, job_id: jobId } },
    current: hasMesh ? { version: 3, mesh_url: "/api/projects/p/scans/s/files/mesh.ply", stats: {} } : null,
  }) as unknown as Pick<ProcessState, "stages" | "current">;

check(scanMeshStage(null, proc("done", "j1")) === "live", "no job: the live model");
check(scanMeshStage(job("queued"), null) === "live", "queued, nothing known yet");
check(scanMeshStage(job("running"), proc("running", "j1")) === "live", "tracking still runs");
check(scanMeshStage(job("running"), proc("done", "j1")) === "tracked", "this job's Track stage is done: the tracked mesh");
check(scanMeshStage(job("running"), proc("done", "j0")) === "live", "a mesh an earlier job tracked is not this job's");
check(scanMeshStage(job("running"), proc("done", null)) === "live", "a Track stage with no job on record");
check(scanMeshStage(job("running"), proc("done", "j1", false)) === "live", "no mesh to show yet");
check(scanMeshStage(job("done"), proc("done", "j1")) === "finished", "done: the finished mesh");
check(scanMeshStage(job("done"), null) === "finished", "done, whatever else is known");
check(scanMeshStage(job("cancelled"), proc("done", "j1")) === "tracked", "cancelled after tracking: the tracked mesh stays");
check(scanMeshStage(job("failed"), proc("done", "j1")) === "tracked", "failed after tracking: the tracked mesh stays");
check(scanMeshStage(job("cancelled"), proc("running", "j1")) === "live", "cancelled while tracking: the live model stays");
check(scanMeshStage(job("failed"), proc("failed", "j1")) === "live", "tracking failed: the live model stays");

check(versionedUrl("/files/mesh.ply", 7) === "/files/mesh.ply?v=7", "the version in the address");
check(versionedUrl("/files/mesh.ply?x=1", 7) === "/files/mesh.ply?x=1&v=7", "after another parameter");
check(versionedUrl("/files/mesh.ply", null) === "/files/mesh.ply", "no version known: the plain address");

console.log(`scanMesh.test: ${checks} checks passed`);
