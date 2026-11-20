/**
 * The Check panel's picked area in the 3D view: its faces lit over the mesh (the Fit tool's region
 * overlay, in the panel's colour), a label at its centre with its number in the list, and the camera
 * move to it. When the area has a place to scan it from (FEATURE_PLAN N1), the scanner is drawn there:
 * a box about the size of its housing, a short cone in the direction it looks, and a dashed line of
 * sight to the area, with a label of how far and at what angle.
 *
 * The move and the label wait for the view's first drawn frame: an area can be picked in the panel
 * while a large mesh is still on its way to the screen, and a camera command sent before the view
 * listens would be lost.
 */
import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useState } from "react";
import { BufferAttribute, BufferGeometry, LineBasicMaterial, LineDashedMaterial, LineSegments } from "three";
import type { CheckPose } from "../../api/types";
import { fmtAreaSize, kindName } from "../../lib/check";
import { poseShort, scannerLines } from "../../lib/checkWhy";
import { viewBus } from "../../three/viewBus";
import { overlayMaterial, RegionOverlay } from "./FitTool";
import { CHECK_COLOR, type CheckLit } from "./useCheck";

/** The picks the camera has flown to: a pick is flown to once, also when its mark is shown again. */
const flown = new WeakSet<CheckLit>();

/** The scanner at its pose, in the mesh's frame (this sits inside the view's mesh group). */
export function ScannerAt({ pose, label = true }: { pose: CheckPose; label?: boolean }) {
  const parts = useMemo(() => {
    const l = scannerLines(pose);
    const make = (a: Float32Array, dashed: boolean) => {
      const g = new BufferGeometry();
      g.setAttribute("position", new BufferAttribute(a, 3));
      const m = dashed
        ? new LineDashedMaterial({ color: CHECK_COLOR, dashSize: 6, gapSize: 4, transparent: true, opacity: 0.9, depthTest: false })
        : new LineBasicMaterial({ color: CHECK_COLOR, transparent: true, opacity: 0.95, depthTest: false });
      const s = new LineSegments(g, m);
      if (dashed) s.computeLineDistances();
      s.renderOrder = 20;
      return s;
    };
    return [make(l.body, false), make(l.cone, false), make(l.sight, true)];
  }, [pose]);
  useEffect(
    () => () => {
      for (const p of parts) {
        p.geometry.dispose();
        (p.material as LineBasicMaterial).dispose();
      }
    },
    [parts],
  );
  return (
    <group>
      {parts.map((p, i) => (
        <primitive key={i} object={p} />
      ))}
      {label ? (
        <Html position={pose.position_mm} center zIndexRange={[30, 0]} className="html-label" style={{ pointerEvents: "none" }}>
          <div className="whitespace-nowrap rounded-full border bg-[rgb(17_20_25/0.9)] px-2.5 py-1 text-[12px] font-semibold text-white shadow-lg" style={{ borderColor: CHECK_COLOR }}>
            Hold the scanner here
            <span className="font-normal text-white/75"> · {poseShort(pose)}</span>
          </div>
        </Html>
      ) : null}
    </group>
  );
}

export function CheckMark({ lit, geometry }: { lit: CheckLit; geometry: BufferGeometry }) {
  const material = useMemo(() => overlayMaterial(CHECK_COLOR, 0.55), []);
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
  return (
    <group>
      <RegionOverlay tris={lit.tris} base={geometry} material={material} />
      {live && lit.pose ? <ScannerAt pose={lit.pose} /> : null}
      {live ? (
        <Html position={lit.center} center zIndexRange={[30, 0]} className="html-label" style={{ pointerEvents: "none" }}>
          <div
            className="flex items-center gap-1.5 whitespace-nowrap rounded-full border bg-[rgb(17_20_25/0.9)] py-1 pl-1 pr-2.5 text-[12.5px] font-semibold text-white shadow-lg"
            style={{ borderColor: CHECK_COLOR }}
          >
            <span className="grid h-[18px] min-w-[18px] place-items-center rounded-full px-1 font-mono text-[10.5px] font-bold text-[#0b0d11]" style={{ background: CHECK_COLOR }}>
              {lit.index + 1}
            </span>
            {kindName(lit.kind)}
            <span className="font-mono font-normal text-white/75 tnum">{fmtAreaSize(lit.areaMm2)}</span>
          </div>
        </Html>
      ) : null}
    </group>
  );
}
