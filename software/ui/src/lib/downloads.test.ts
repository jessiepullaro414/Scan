/**
 * Checks for the one download list (UX_PLAN U42, `npm test`): the same files, in the same order and
 * words, on the project page, in the viewer and on the Process page; OBJ is offered, and when the
 * engine wrote none the app says so instead of inventing one.
 */
import { MESH_DOWNLOADS, meshDownloads, OBJ_MISSING } from "./downloads.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`check failed: ${what}`);
}

check(MESH_DOWNLOADS.map((d) => d.label).join(" | ") === "Mesh (STL) | Mesh (PLY) | Mesh (OBJ) | Laser points (PLY)", "one list, OBJ included");
check(MESH_DOWNLOADS.map((d) => d.hint).join(" | ") === "for CAD and slicers | with normals | for Blender and most 3D apps | the laser lines' own points", "the hints");
const all = meshDownloads({ stl: "/s", ply: "/p", obj: "/o", laser: "/l" }, "Part-Scan_1");
check(all.length === 4 && all[2].file === "Part-Scan_1.obj" && all[2].url === "/o" && all[2].check, "OBJ is looked for before it is saved");
check(all[3].file === "Part-Scan_1-laser.ply", "laser points file name");
const noLaser = meshDownloads({ stl: "/s", ply: "/p", obj: "/o", laser: null }, "x");
check(noLaser.map((d) => d.key).join() === "stl,ply,obj", "no laser points: not listed");
check(OBJ_MISSING.title === "No OBJ for this scan" && /only when OBJ export is turned on/.test(OBJ_MISSING.detail), "the honest answer when there is no OBJ");

console.log(`downloads.test: ${checks} checks passed`);
