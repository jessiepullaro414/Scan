/**
 * The scanner drawn in its own camera frame (OpenCV axes: x right, y down, z forward, mm):
 * a short viewing frustum from the left camera plus the body outline. Place it with T_wc
 * inside the world-frame group.
 */
import { Line } from "@react-three/drei";
import { forwardRef, useMemo } from "react";
import { DoubleSide, type Group } from "three";
import { FRUSTUM_TAN } from "./coords";

type P = [number, number, number];

function frustumSegments(depth: number): P[] {
  const x = depth * FRUSTUM_TAN.x;
  const y = depth * FRUSTUM_TAN.y;
  const c: P[] = [
    [-x, -y, depth],
    [x, -y, depth],
    [x, y, depth],
    [-x, y, depth],
  ];
  const o: P = [0, 0, 0];
  return [o, c[0], o, c[1], o, c[2], o, c[3], c[0], c[1], c[1], c[2], c[2], c[3], c[3], c[0]];
}

// Body: about 116 x 36 x 40 mm, cameras at x = 0 (left) and x = 60 (right), lens plane at z = 0.
const BODY = { x0: -28, x1: 88, y0: -18, y1: 18, z0: -42, z1: -2 };

function boxSegments(): P[] {
  const { x0, x1, y0, y1, z0, z1 } = BODY;
  const v: P[] = [
    [x0, y0, z0],
    [x1, y0, z0],
    [x1, y1, z0],
    [x0, y1, z0],
    [x0, y0, z1],
    [x1, y0, z1],
    [x1, y1, z1],
    [x0, y1, z1],
  ];
  const e = [0, 1, 1, 2, 2, 3, 3, 0, 4, 5, 5, 6, 6, 7, 7, 4, 0, 4, 1, 5, 2, 6, 3, 7];
  return e.map((i) => v[i]);
}

export type ScannerGlyphProps = {
  color: string;
  opacity?: number;
  depth?: number;
  dashed?: boolean;
  lineWidth?: number;
  fillOpacity?: number;
};

export const ScannerGlyph = forwardRef<Group, ScannerGlyphProps>(function ScannerGlyph(
  { color, opacity = 1, depth = 62, dashed = false, lineWidth = 1.3, fillOpacity = 0.06 },
  ref,
) {
  const frustum = useMemo(() => frustumSegments(depth), [depth]);
  const body = useMemo(() => boxSegments(), []);
  const w = 2 * depth * FRUSTUM_TAN.x;
  const h = 2 * depth * FRUSTUM_TAN.y;
  return (
    <group ref={ref}>
      <Line
        segments
        points={frustum}
        color={color}
        lineWidth={lineWidth}
        transparent
        opacity={opacity}
        dashed={dashed}
        dashSize={5}
        gapSize={4}
        depthWrite={false}
      />
      <Line segments points={body} color={color} lineWidth={lineWidth * 0.85} transparent opacity={opacity * 0.85} dashed={dashed} dashSize={5} gapSize={4} depthWrite={false} />
      <mesh position={[0, 0, depth]} renderOrder={2}>
        <planeGeometry args={[w, h]} />
        <meshBasicMaterial color={color} transparent opacity={fillOpacity * opacity} side={DoubleSide} depthWrite={false} />
      </mesh>
      <mesh position={[(BODY.x0 + BODY.x1) / 2, 0, (BODY.z0 + BODY.z1) / 2]} renderOrder={2}>
        <boxGeometry args={[BODY.x1 - BODY.x0, BODY.y1 - BODY.y0, BODY.z1 - BODY.z0]} />
        <meshBasicMaterial color={color} transparent opacity={0.07 * opacity} depthWrite={false} />
      </mesh>
      {/* lenses */}
      <mesh position={[0, 0, -1]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[6, 6, 2, 20]} />
        <meshBasicMaterial color={color} transparent opacity={0.55 * opacity} depthWrite={false} />
      </mesh>
      <mesh position={[60, 0, -1]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[6, 6, 2, 20]} />
        <meshBasicMaterial color={color} transparent opacity={0.55 * opacity} depthWrite={false} />
      </mesh>
    </group>
  );
});
