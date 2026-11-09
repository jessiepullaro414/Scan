/**
 * What the current mesh is, in plain words (every `MeshStats` field), and its downloads
 * (STL / PLY / OBJ through the scan's files).
 */
import { Box, Download, FileBox } from "lucide-react";
import { useState } from "react";
import { MOCK } from "../../api/backend";
import { api } from "../../api/http";
import type { MeshStats } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { Skeleton } from "../../components/ui/Feedback";
import { KeyValue } from "../../components/ui/Layout";
import { downloadFile } from "../../lib/download";
import { fmtFixed, fmtInt } from "../../lib/format";
import { toast } from "../../store/toasts";
import { Help, Panel } from "./bits";

const num = (n: number | null | undefined, digits: number) => <span className="font-mono text-xs tnum">{fmtFixed(n, digits)}</span>;

export function StatsPanel({ stats, loading }: { stats: MeshStats | null; loading: boolean }) {
  return (
    <Panel title="Mesh" icon={Box}>
      {stats ? (
        <>
          <div className="grid grid-cols-3 gap-2" aria-label="Size along the scan's axes">
            {(["x", "y", "z"] as const).map((k, i) => (
              <div key={k} className="rounded-lg border border-line bg-surface-2/60 px-2.5 py-2">
                <div className="text-[10.5px] font-semibold uppercase text-subtle">{k}</div>
                <div className="font-mono text-sm font-semibold text-fg tnum">{fmtFixed(stats.bbox_mm[i], 1)}</div>
                <div className="text-[10px] text-subtle">mm</div>
              </div>
            ))}
          </div>
          <KeyValue
            dense
            className="mt-3"
            rows={[
              ["Triangles", <span className="font-mono text-xs tnum">{fmtInt(stats.triangles)}</span>],
              ["Vertices", <span className="font-mono text-xs tnum">{fmtInt(stats.vertices)}</span>],
              [
                "Pieces",
                stats.components <= 1 ? (
                  "One piece"
                ) : (
                  <span>
                    <span className="font-mono text-xs tnum">{fmtInt(stats.components)}</span> separate pieces
                  </span>
                ),
              ],
              [
                "Holes",
                stats.holes === 0 ? (
                  "None"
                ) : (
                  <span>
                    <span className="font-mono text-xs tnum">{fmtInt(stats.holes)}</span>, the largest {num(stats.largest_hole_mm, 1)} mm around
                  </span>
                ),
              ],
              ["Outer edge", stats.border_mm === null ? "None: the surface is closed" : <span>{num(stats.border_mm, 0)} mm around</span>],
              [
                "Watertight",
                stats.watertight ? (
                  <span className="text-ok">Yes: fully closed</span>
                ) : (
                  <span>
                    No
                    {stats.boundary_edges ? (
                      <span className="text-muted">
                        : <span className="font-mono text-xs tnum">{fmtInt(stats.boundary_edges)}</span> open edges
                      </span>
                    ) : null}
                  </span>
                ),
              ],
              stats.non_manifold_edges
                ? [
                    "Tangled edges",
                    <span className="text-warn">
                      <span className="font-mono text-xs tnum">{fmtInt(stats.non_manifold_edges)}</span> shared by 3+ triangles
                    </span>,
                  ]
                : null,
              ["Area", <span>{num(stats.area_cm2, 1)} cm²</span>],
              [
                "Volume",
                stats.volume_cm3 !== null ? <span>{num(stats.volume_cm3, 1)} cm³</span> : <span className="text-muted">Only for a watertight mesh</span>,
              ],
            ]}
          />
          <Help className="mt-2">Size is the bounding box along the scan's axes (the first camera frame).</Help>
        </>
      ) : loading ? (
        <div className="space-y-2" aria-busy="true">
          <div className="grid grid-cols-3 gap-2">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-[62px]" />
            ))}
          </div>
          <Skeleton className="h-24" />
        </div>
      ) : (
        <div className="text-sm text-muted">Shown once there is a mesh.</div>
      )}
    </Panel>
  );
}

/** STL / PLY / OBJ of the current mesh. OBJ is written only when the engine's config exports it. */
export function DownloadPanel({ pid, sid, stem, ready }: { pid: string; sid: string; stem: string; ready: boolean }) {
  const [checking, setChecking] = useState(false);
  const url = (name: string) => api.fileUrl(pid, sid, name);
  const obj = async () => {
    if (!MOCK) {
      setChecking(true);
      const there = await api.exists(url("mesh.obj"));
      setChecking(false);
      if (!there) {
        toast.error("No OBJ for this scan", {
          detail: "The engine writes mesh.obj only when OBJ export is turned on.",
          hint: "Turn on OBJ export in the engine's config, then make any change to the mesh. STL and PLY are always there.",
        });
        return;
      }
    }
    await downloadFile(url("mesh.obj"), `${stem}.obj`);
  };
  return (
    <Panel title="Download" icon={Download}>
      {ready ? (
        <>
          <div className="grid grid-cols-3 gap-2">
            <Button size="sm" icon={Download} onClick={() => void downloadFile(url("mesh.stl"), `${stem}.stl`)} title="For CAD and slicers">
              STL
            </Button>
            <Button size="sm" icon={Download} onClick={() => void downloadFile(url("mesh.ply"), `${stem}.ply`)} title="With normals">
              PLY
            </Button>
            <Button size="sm" icon={FileBox} loading={checking} onClick={() => void obj()} title="For Blender and most 3D apps">
              OBJ
            </Button>
          </div>
          <Help className="mt-2">The current version, with every active edit. Units: mm.</Help>
        </>
      ) : (
        <div className="text-sm text-muted">Make the mesh first.</div>
      )}
    </Panel>
  );
}
