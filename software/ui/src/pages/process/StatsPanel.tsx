/**
 * What the current mesh is, in plain words (every `MeshStats` field), and its downloads
 * (STL / PLY / OBJ through the scan's files).
 */
import { Box, Download } from "lucide-react";
import { api } from "../../api/http";
import type { MeshStats } from "../../api/types";
import { MeshDownloadButtons } from "../../components/MeshDownloads";
import { Skeleton } from "../../components/ui/Feedback";
import { KeyValue } from "../../components/ui/Layout";
import { fmtFixed, fmtInt, fmtNum } from "../../lib/format";
import { fmtArea } from "../../lib/extent";
import { Help, Panel } from "./bits";

// R19: thousands grouped ("5,206 mm around"), and one area unit, mm², as everywhere else (U22)
const num = (n: number | null | undefined, digits: number) => <span className="font-mono text-xs tnum">{fmtNum(n, digits)}</span>;
const mm2 = (cm2: number) => <span className="font-mono text-xs tnum">{fmtArea(cm2 * 100)}</span>;

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
                    <span className="font-mono text-xs tnum">{fmtInt(stats.holes)}</span>, the largest {num(stats.largest_hole_mm, 1)}&#8239;mm around
                  </span>
                ),
              ],
              ["Outer edge", stats.border_mm === null ? "None: the surface is closed" : <span>{num(stats.border_mm, 0)}&#8239;mm around</span>],
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
              ["Area", mm2(stats.area_cm2)],
              stats.filled_cm2
                ? [
                    "Filled",
                    <span title="Made by hole filling: not measured, so measuring skips it (striped grey in the view)">
                      {mm2(stats.filled_cm2)} <span className="text-muted">not measured</span>
                    </span>,
                  ]
                : null,
              stats.sigma_mm
                ? [
                    "Confidence",
                    <span title="Share of the surface whose confidence (each point's ±) is at most 0.1&#8239;mm">
                      {Math.round(stats.sigma_mm.within_0_1 * 100)}&#8239;% within 0.1&#8239;mm <span className="text-muted">(median ±{num(stats.sigma_mm.median, 2)}&#8239;mm)</span>
                    </span>,
                  ]
                : null,
              [
                "Volume",
                stats.volume_cm3 !== null ? <span>{num(stats.volume_cm3, 1)}&#8239;cm³</span> : <span className="text-muted">Only for a watertight mesh</span>,
              ],
            ]}
          />
          <Help className="mt-2">Size along the scan's own directions.</Help>
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
  const url = (name: string) => api.fileUrl(pid, sid, name);
  return (
    <Panel title="Download" icon={Download}>
      {ready ? (
        <>
          {/* U42: the one download list; the current mesh has no laser points of its own here */}
          <MeshDownloadButtons urls={{ stl: url("mesh.stl"), ply: url("mesh.ply"), obj: url("mesh.obj"), laser: null }} stem={stem} />
          <Help className="mt-2">The current version, with every active edit. Units: mm.</Help>
        </>
      ) : (
        <div className="text-sm text-muted">Make the mesh first.</div>
      )}
    </Panel>
  );
}
