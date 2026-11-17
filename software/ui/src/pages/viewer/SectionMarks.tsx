/**
 * The Section tool in the 3D view (FEATURE_PLAN.md I1): the cutting plane drawn faintly (a square round the model,
 * with its edge), the cut drawn as a line on the surface (measured stretches solid, not-measured ones dashed red, and
 * a faint copy that shows through the model where the cut is hidden), and, while three points are being picked, the
 * clicks on the surface. Hiding the part in front of the plane is the model's own clipping (ViewerCanvas `Model`), so
 * nothing here touches the mesh's geometry: the line is its own small buffer, made once per answer.
 */
import { Html, Line } from "@react-three/drei";
import { useThree } from "@react-three/fiber";
import { memo, useEffect, useMemo, type RefObject } from "react";
import { BufferAttribute, BufferGeometry, DoubleSide, type LineSegments, type Mesh } from "three";
import type { SectionAnswer, SectionPlane } from "../../api/types";
import { profileOf, to3d, type Vec3 } from "../../lib/section";
import { useThemeColors } from "../../three/Studio";
import { useSurfacePick } from "./picking";

const NOT_MEASURED = "#e5484d";

/** The cut's segments in 3D, measured and not measured apart (two LineSegments buffers). */
function cutGeometries(answer: SectionAnswer): { measured: BufferGeometry; filled: BufferGeometry } {
  const meas: number[] = [],
    fill: number[] = [];
  const pl = answer.plane;
  for (const ln of profileOf(answer)) {
    const P = ln.pts;
    for (const [a, b, filled] of ln.stretches) {
      const out = filled ? fill : meas;
      for (let i = a; i < b; i++) {
        const j = (i + 1) % ln.n;
        out.push(...to3d(pl, P[2 * i], P[2 * i + 1]), ...to3d(pl, P[2 * j], P[2 * j + 1]));
      }
    }
  }
  const make = (xs: number[]) => {
    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(new Float32Array(xs), 3));
    return g;
  };
  return { measured: make(meas), filled: make(fill) };
}

/** The plane as a square of half-side `half` round the point of the plane nearest `centre`. */
function planeSquare(plane: SectionPlane, centre: Vec3, half: number): Vec3[] {
  const n = plane.normal,
    p = plane.point;
  const d = (centre[0] - p[0]) * n[0] + (centre[1] - p[1]) * n[1] + (centre[2] - p[2]) * n[2];
  const c = [centre[0] - d * n[0], centre[1] - d * n[1], centre[2] - d * n[2]];
  const { u, v } = plane;
  const at = (a: number, b: number): Vec3 => [c[0] + a * u[0] + b * v[0], c[1] + a * u[1] + b * v[1], c[2] + a * u[2] + b * v[2]];
  return [at(-half, -half), at(half, -half), at(half, half), at(-half, half)];
}

const SectionLine = memo(function SectionLine({ answer, color }: { answer: SectionAnswer; color: string }) {
  const g = useMemo(() => cutGeometries(answer), [answer]);
  useEffect(
    () => () => {
      g.measured.dispose();
      g.filled.dispose();
    },
    [g],
  );
  const invalidate = useThree((s) => s.invalidate);
  useEffect(() => invalidate(), [g, invalidate]);
  return (
    <group>
      {/* on the surface: depth-tested (the model's own polygon offset keeps it in front of the faces it lies on) */}
      <lineSegments geometry={g.measured} renderOrder={6} raycast={() => null}>
        <lineBasicMaterial color={color} />
      </lineSegments>
      <lineSegments geometry={g.filled} renderOrder={6} raycast={() => null} ref={(o: LineSegments | null) => void o?.computeLineDistances()}>
        <lineDashedMaterial color={NOT_MEASURED} dashSize={1.2} gapSize={0.8} />
      </lineSegments>
      {/* where the model hides it: faintly, through the surface */}
      <lineSegments geometry={g.measured} renderOrder={7} raycast={() => null}>
        <lineBasicMaterial color={color} transparent opacity={0.28} depthTest={false} depthWrite={false} />
      </lineSegments>
    </group>
  );
});

export function SectionMarks({
  plane,
  answer,
  centre,
  radius,
  picking,
  points,
  onPickPoint,
  target,
  markerRadius,
}: {
  plane: SectionPlane | null;
  answer: SectionAnswer | null;
  /** The model's centre and radius (the scan's frame): the plane is drawn as a square round it. */
  centre: Vec3;
  radius: number;
  /** Clicks on the surface pick the three points. */
  picking: boolean;
  points: Vec3[];
  onPickPoint: (p: Vec3) => void;
  target: RefObject<Mesh | null>;
  markerRadius: number;
}) {
  const accent = useThemeColors().accent;
  const { hover, overFilled } = useSurfacePick(picking, target, (h) => onPickPoint(h.p));
  const square = useMemo(() => (plane ? planeSquare(plane, centre, radius * 1.15) : null), [plane, centre, radius]);
  const quad = useMemo(() => {
    if (!square) return null;
    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(new Float32Array(square.flat()), 3));
    g.setIndex([0, 1, 2, 0, 2, 3]);
    return g;
  }, [square]);
  useEffect(() => () => quad?.dispose(), [quad]);
  const invalidate = useThree((s) => s.invalidate);
  useEffect(() => invalidate(), [quad, points, invalidate]);
  const r = markerRadius;
  return (
    <group>
      {quad && square ? (
        <>
          <mesh geometry={quad} renderOrder={4} raycast={() => null}>
            <meshBasicMaterial color={accent} transparent opacity={0.07} side={DoubleSide} depthWrite={false} />
          </mesh>
          <Line points={[...square, square[0]]} color={accent} lineWidth={1} transparent opacity={0.45} depthWrite={false} renderOrder={4} />
        </>
      ) : null}
      {answer ? <SectionLine answer={answer} color={accent} /> : null}
      {points.map((p, i) => (
        <mesh key={i} position={p} renderOrder={11}>
          <sphereGeometry args={[r, 18, 14]} />
          <meshBasicMaterial color={accent} depthTest={false} transparent />
        </mesh>
      ))}
      {points.length >= 2 ? <Line points={points.length === 3 ? [...points, points[0]] : points} color={accent} lineWidth={1.5} dashed dashSize={r * 2} gapSize={r * 1.5} depthTest={false} renderOrder={10} transparent opacity={0.8} /> : null}
      {picking && hover ? (
        <mesh position={hover} renderOrder={12}>
          <sphereGeometry args={[r * 0.8, 14, 10]} />
          <meshBasicMaterial color={overFilled ? "#8a8781" : accent} depthTest={false} transparent opacity={0.85} />
        </mesh>
      ) : null}
      {picking && hover && overFilled ? (
        <Html position={hover} zIndexRange={[25, 0]} className="html-label" style={{ pointerEvents: "none" }}>
          <div className="ml-3 -translate-y-1/2 whitespace-nowrap rounded-full border border-white/15 bg-[rgb(17_20_25/0.88)] px-2 py-0.5 text-[11.5px] text-white shadow-lg">
            Filled, not measured: pick a scanned spot
          </div>
        </Html>
      ) : null}
    </group>
  );
}
