/**
 * Saving a scan's mesh files, the same everywhere (UX_PLAN U42; the list and its words are in lib/downloads.ts).
 * The project page shows them as a menu, the viewer and the Process page as buttons.
 */
import { Download, FileBox } from "lucide-react";
import { useState } from "react";
import { MOCK } from "../api/backend";
import { api } from "../api/http";
import type { Scan } from "../api/types";
import { downloadFile } from "../lib/download";
import { meshDownloads, OBJ_MISSING, type MeshDownload } from "../lib/downloads";
import { cn } from "../lib/util";
import { toast } from "../store/toasts";
import { Button } from "./ui/Button";

/** The addresses of a scan's mesh files (the result's own when it gives them). */
export function meshUrls(pid: string, sid: string, result: Scan["result"] | null | undefined) {
  return {
    stl: result?.stl_url || api.fileUrl(pid, sid, "mesh.stl"),
    ply: result?.mesh_url || api.fileUrl(pid, sid, "mesh.ply"),
    obj: api.fileUrl(pid, sid, "mesh.obj"),
    laser: result?.laser_points_url ?? null,
  };
}

/** Save one file; OBJ is looked for first, and when the engine wrote none the app says so. */
export async function saveMeshFile(d: MeshDownload): Promise<void> {
  if (d.check && !MOCK && !(await api.exists(d.url))) {
    toast.error(OBJ_MISSING.title, { detail: OBJ_MISSING.detail, hint: OBJ_MISSING.hint });
    return;
  }
  await downloadFile(d.url, d.file);
}

/** The menu items of the list (the project page's Download menu, its More menu). */
export function meshDownloadItems(urls: Parameters<typeof meshDownloads>[0], stem: string) {
  return meshDownloads(urls, stem).map((d) => ({ label: d.label, icon: d.key === "obj" ? FileBox : Download, hint: d.hint, onSelect: () => void saveMeshFile(d) }));
}

/** The list as buttons, two to a row. */
export function MeshDownloadButtons({ urls, stem, className }: { urls: Parameters<typeof meshDownloads>[0]; stem: string; className?: string }) {
  const [busy, setBusy] = useState<string | null>(null);
  const list = meshDownloads(urls, stem);
  return (
    <div className={cn("grid grid-cols-2 gap-2", className)}>
      {list.map((d, i) => (
        <Button
          key={d.key}
          size="sm"
          icon={d.key === "obj" ? FileBox : Download}
          loading={busy === d.key}
          title={`${d.label}: ${d.hint}`}
          className={list.length % 2 && i === list.length - 1 ? "col-span-2" : undefined}
          onClick={async () => {
            setBusy(d.key);
            await saveMeshFile(d);
            setBusy(null);
          }}
        >
          {d.short}
        </Button>
      ))}
    </div>
  );
}
