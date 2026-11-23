/**
 * Checks for the one download list (UX_PLAN U42, `npm test`): the same files, in the same order and
 * words, on the project page, in the viewer and on the Process page; OBJ is offered, and when the
 * engine wrote none the app says so instead of inventing one.
 */
import { CAD_FILES_LINE, PRINT_CLOSED_LINE, meshAxesLine, MESH_DOWNLOADS, meshDownloads, OBJ_MISSING } from "./downloads.ts";

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
// the viewer's Export group says what each download is for (the guide's to-cad.md): the CAD files hold the fitted
// shapes, not the surface; the mesh's axes and what to do about them; a 3D print needs a closed mesh
check(CAD_FILES_LINE === "The shapes you fitted and saved (planes, cylinders, holes and their axes), as exact geometry to model against. Not the scanned surface: for that, use STL.", CAD_FILES_LINE);
check(!/solid|surface model/i.test(CAD_FILES_LINE.replace("scanned surface", "")), "promises no solids");
check(meshAxesLine("current", false) === "STL and PLY are along the scan's axes unless “along the part's axes”.", "axes set: as before");
check(meshAxesLine("current", true) === "STL and PLY are along the scan's axes unless “along the part's axes”. The part's axes are out of date.", "axes out of date: as before");
check(meshAxesLine("offer", false) === "STL and PLY are along the scan's axes. For the part upright at its own origin, press “Set the part's axes…” under Measure.", meshAxesLine("offer", false));
check(meshAxesLine("hidden", false) === "STL and PLY are along the scan's axes. For the part upright at its own origin, save a fitted plane, then press “Set the part's axes…” under Measure.", meshAxesLine("hidden", false));
check(meshAxesLine("panel", false) === "STL and PLY are along the scan's axes until the part's axes are set, in “The part's axes” under Measure.", meshAxesLine("panel", false));
check(PRINT_CLOSED_LINE === "For a 3D print the mesh must be closed: use “Make it watertight” under Holes on the Edit mesh page.", PRINT_CLOSED_LINE);
for (const l of [CAD_FILES_LINE, PRINT_CLOSED_LINE, meshAxesLine("hidden", false)]) check(!/[—–]/.test(l), `no dashes: ${l}`);

// no screen has the switch: the hint names the settings file's key (ohs/app/config.py, [process] export)
check(OBJ_MISSING.hint.includes('add "obj" to export under [process] in the settings file'), OBJ_MISSING.hint);

console.log(`downloads.test: ${checks} checks passed`);
