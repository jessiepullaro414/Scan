/**
 * The one list of a scan's mesh files (UX_PLAN U42): the project page's Download menu, the viewer's
 * Download panel and the Process page's Download panel all show these, in this order and words.
 * The engine writes `mesh.obj` only when OBJ export is turned on, so OBJ is looked for before it is
 * saved, and the app says so when there is none (it never makes one up).
 *
 * Plain TypeScript (no React) so `npm test` can check it.
 */
export type MeshFileKey = "stl" | "ply" | "obj" | "laser";

export const MESH_DOWNLOADS: { key: MeshFileKey; label: string; short: string; hint: string }[] = [
  { key: "stl", label: "Mesh (STL)", short: "STL", hint: "for CAD and slicers" },
  { key: "ply", label: "Mesh (PLY)", short: "PLY", hint: "with normals" },
  { key: "obj", label: "Mesh (OBJ)", short: "OBJ", hint: "for Blender and most 3D apps" },
  { key: "laser", label: "Laser points (PLY)", short: "Laser points", hint: "the laser lines' own points" },
];

/** What the app says when the engine wrote no OBJ for the scan. */
export const OBJ_MISSING = {
  title: "No OBJ for this scan",
  detail: "The engine writes mesh.obj only when OBJ export is turned on.",
  hint: 'To turn it on, add "obj" to export under [process] in the settings file (export = ["ply", "stl", "obj"]), then make the mesh again (or make any change to it). STL and PLY are always there.',
};

export type MeshDownload = (typeof MESH_DOWNLOADS)[number] & { url: string; file: string; check: boolean };

/** The files to offer, with their addresses and file names; laser points only when the scan has them. */
export function meshDownloads(urls: { stl: string; ply: string; obj: string; laser: string | null }, stem: string): MeshDownload[] {
  const name: Record<MeshFileKey, string> = { stl: `${stem}.stl`, ply: `${stem}.ply`, obj: `${stem}.obj`, laser: `${stem}-laser.ply` };
  return MESH_DOWNLOADS.flatMap((d) => {
    const url = urls[d.key];
    return url ? [{ ...d, url, file: name[d.key], check: d.key === "obj" }] : [];
  });
}

/** The viewer's "For CAD" line: what the STEP file and the FreeCAD macro hold (ohs/cad/step.py: no solids, no
 * free-form surfaces), and where the surface is. */
export const CAD_FILES_LINE =
  "The shapes you fitted and saved (planes, cylinders, holes and their axes), as exact geometry to model against. Not the scanned surface: for that, use STL.";

/** A 3D print needs a closed mesh: shown when the mesh is not watertight. */
export const PRINT_CLOSED_LINE = "For a 3D print the mesh must be closed: use “Make it watertight” under Holes on the Edit mesh page.";

/**
 * Which axes STL and PLY are along, and what to do about it. `axes`: "current" (the part's axes are set: the
 * "along the part's axes" buttons are there), "panel" (being set), "offer" ("Set the part's axes…" is shown) or
 * "hidden" (no fitted plane saved yet). `outOfDate`: the part's axes were set on an earlier mesh.
 */
export function meshAxesLine(axes: "current" | "panel" | "offer" | "hidden", outOfDate: boolean): string {
  if (axes === "current") return `STL and PLY are along the scan's axes unless “along the part's axes”.${outOfDate ? " The part's axes are out of date." : ""}`;
  if (axes === "panel") return "STL and PLY are along the scan's axes until the part's axes are set, in “The part's axes” under Measure.";
  const how = axes === "offer" ? "press “Set the part's axes…”" : "save a fitted plane, then press “Set the part's axes…”";
  return `STL and PLY are along the scan's axes. For the part upright at its own origin, ${how} under Measure.`;
}
