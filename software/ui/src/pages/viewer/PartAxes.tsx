/**
 * The part's coordinate system in the 3D view: its X, Y and Z axes drawn from its origin and labelled with
 * their names, in the scan's frame like everything else in the view (lib/partFrame.ts `axesInScan`). Drawn
 * over the mesh so they show through it; the colours are the view's axis gizmo's.
 */
import { Html, Line } from "@react-three/drei";
import { memo, useMemo } from "react";
import type { PartFrameView } from "../../api/types";
import { axesInScan } from "../../lib/partFrame";
import type { Vec3 } from "./ViewerCanvas";

const AXIS_COLOR = { X: "#f0605a", Y: "#5fcf6a", Z: "#5a9cff" } as const;

export const PartAxes = memo(function PartAxes({ frame, length, outOfDate }: { frame: PartFrameView; length: number; outOfDate: boolean }) {
  const { origin, axes } = useMemo(() => axesInScan(frame), [frame]);
  const lines = useMemo(
    () => axes.map((a) => ({ ...a, tip: [origin[0] + a.dir[0] * length, origin[1] + a.dir[1] * length, origin[2] + a.dir[2] * length] as Vec3 })),
    [axes, origin, length],
  );
  return (
    <group>
      {lines.map((a) => (
        <group key={a.name}>
          <Line points={[origin, a.tip]} color={AXIS_COLOR[a.name]} lineWidth={2.6} depthTest={false} renderOrder={14} transparent opacity={outOfDate ? 0.55 : 1} dashed={outOfDate} dashSize={length / 12} gapSize={length / 20} />
          <Html position={a.tip} center zIndexRange={[28, 0]} className="html-label" style={{ pointerEvents: "none" }}>
            <div className="rounded-full px-1.5 font-mono text-[12px] font-bold shadow" style={{ background: AXIS_COLOR[a.name], color: "#0b0d11" }}>
              {a.name}
            </div>
          </Html>
        </group>
      ))}
      <mesh position={origin} renderOrder={15}>
        <sphereGeometry args={[length * 0.03, 16, 12]} />
        <meshBasicMaterial color="#ffffff" depthTest={false} transparent />
      </mesh>
    </group>
  );
});
