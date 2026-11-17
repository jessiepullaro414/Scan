/**
 * The Compare panel's picked area in the 3D view: its faces lit over the mesh (as the Check panel's, in the side's
 * colour: the scale's outward or inward end), a label at its centre with its number and mean deviation, and the camera
 * move to it. As CheckMark.tsx, the move and the label wait for the view's first drawn frame.
 */
import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useState } from "react";
import type { BufferGeometry } from "three";
import { DEVIATION_RAMPS } from "../../lib/deviation";
import { viewBus, type FrameSphere } from "../../three/viewBus";
import { overlayMaterial, RegionOverlay } from "./FitTool";

/** The area lit on the mesh. */
export type ReferenceLit = {
  /** Its place in the list, from 0. */
  index: number;
  side: "above" | "below";
  /** The mesh it belongs to (its geometry's uuid). */
  meshId: string;
  tris: Uint32Array;
  /** Where its label goes (mm, the scan's frame). */
  center: [number, number, number];
  label: string;
  view: { sphere: FrameSphere; direction?: [number, number, number] };
};

/** An area's colour: the scale's outward end above, its inward end below. */
export const sideColor = (side: "above" | "below") => (side === "above" ? DEVIATION_RAMPS.dark[4] : DEVIATION_RAMPS.dark[0]);

const flown = new WeakSet<ReferenceLit>();

export function ReferenceMark({ lit, geometry }: { lit: ReferenceLit; geometry: BufferGeometry }) {
  const color = sideColor(lit.side);
  const material = useMemo(() => overlayMaterial(color, 0.5), [color]);
  useEffect(() => () => material.dispose(), [material]);
  const [live, setLive] = useState(false);
  useFrame(() => {
    if (!live) setLive(true);
  });
  useEffect(() => {
    if (!live || flown.has(lit)) return;
    flown.add(lit);
    viewBus.emit({ type: "frame", animate: true, sphere: lit.view.sphere, direction: lit.view.direction, margin: 1.7 });
  }, [live, lit]);
  if (lit.meshId !== geometry.uuid) return null;
  return (
    <group>
      <RegionOverlay tris={lit.tris} base={geometry} material={material} />
      {live ? (
        <Html position={lit.center} center zIndexRange={[30, 0]} className="html-label" style={{ pointerEvents: "none" }}>
          <div className="flex items-center gap-1.5 whitespace-nowrap rounded-full border bg-[rgb(17_20_25/0.9)] py-1 pl-1 pr-2.5 text-[12.5px] font-semibold text-white shadow-lg" style={{ borderColor: color }}>
            <span className="grid h-[18px] min-w-[18px] place-items-center rounded-full px-1 font-mono text-[10.5px] font-bold text-white" style={{ background: color }}>
              {lit.index + 1}
            </span>
            <span className="font-mono font-normal tnum">{lit.label}</span>
          </div>
        </Html>
      ) : null}
    </group>
  );
}
