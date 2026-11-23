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
