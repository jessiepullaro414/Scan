/**
 * The Flush tool in the 3D view (FEATURE_PLAN.md I4): the two clicks (A and B), the seam's path through the stations,
 * and each station as a dot coloured by its flush (blue low, orange high across the trusted readings; grey where the
 * flush is not trusted). Nothing here touches the mesh's geometry: the path and the dots are small buffers of their
 * own, made once per answer, drawn over the surface so they show where the model would hide them.
 */
import { Html, Line } from "@react-three/drei";
import { useThree } from "@react-three/fiber";
import { memo, useEffect, useMemo, type RefObject } from "react";
import { BufferAttribute, BufferGeometry, type Mesh } from "three";
import type { FlushAnswer } from "../../api/types";
import { flushColor } from "../../lib/flush";
import { useThemeColors } from "../../three/Studio";
import { useSurfacePick } from "./picking";

type Vec3 = [number, number, number];

/** The stations' dots: one small buffer of positions and colours. */
const Stations = memo(function Stations({ answer, size }: { answer: FlushAnswer; size: number }) {
  const g = useMemo(() => {
    const st = answer.stations;
    const lo = answer.flush.lowest ?? 0,
      hi = answer.flush.highest ?? 0;
    const pos = new Float32Array(3 * st.length),
      col = new Float32Array(3 * st.length);
    st.forEach((s, i) => {
      pos.set(s.point.slice(0, 3), 3 * i);
      col.set(flushColor(s.flush_mm, s.flush_trusted, lo, hi), 3 * i);
    });
    const geo = new BufferGeometry();
    geo.setAttribute("position", new BufferAttribute(pos, 3));
    geo.setAttribute("color", new BufferAttribute(col, 3));
    return geo;
  }, [answer]);
  useEffect(() => () => g.dispose(), [g]);
  const invalidate = useThree((s) => s.invalidate);
  useEffect(() => invalidate(), [g, invalidate]);
  return (
    <points geometry={g} raycast={() => null} renderOrder={13}>
      <pointsMaterial size={size} sizeAttenuation={false} vertexColors depthTest={false} transparent />
    </points>
  );
});

export function FlushMarks({
  clicks,
  answer,
  picking,
  onPick,
  target,
  markerRadius,
}: {
  clicks: Vec3[];
  answer: FlushAnswer | null;
  /** Clicks on the surface pick A and B. */
  picking: boolean;
  onPick: (p: Vec3) => void;
  target: RefObject<Mesh | null>;
  markerRadius: number;
}) {
  const accent = useThemeColors().accent;
  const { hover, overFilled } = useSurfacePick(picking, target, (h) => onPick(h.p));
  const path = useMemo(() => (answer && answer.stations.length >= 2 ? answer.stations.map((s) => s.point.slice(0, 3) as Vec3) : null), [answer]);
  const invalidate = useThree((s) => s.invalidate);
  useEffect(() => invalidate(), [clicks, path, invalidate]);
  const r = markerRadius;
  return (
    <group>
      {path ? <Line points={path} color={accent} lineWidth={1.5} transparent opacity={0.75} depthTest={false} renderOrder={12} /> : null}
      {answer ? <Stations answer={answer} size={7} /> : null}
      {clicks.map((p, i) => (
        <group key={i}>
          <mesh position={p} renderOrder={11}>
            <sphereGeometry args={[r, 18, 14]} />
            <meshBasicMaterial color={accent} depthTest={false} transparent />
          </mesh>
          <Html position={p} zIndexRange={[25, 0]} className="html-label" style={{ pointerEvents: "none" }}>
            <div className="ml-2.5 -translate-y-1/2 rounded-full border border-white/15 bg-[rgb(17_20_25/0.88)] px-1.5 text-[11px] font-bold text-white shadow">{i === 0 ? "A" : "B"}</div>
          </Html>
        </group>
      ))}
      {clicks.length === 1 && picking && hover ? (
        <Line points={[clicks[0], hover]} color={accent} lineWidth={1.2} dashed dashSize={r * 2} gapSize={r * 1.5} depthTest={false} renderOrder={10} transparent opacity={0.7} />
      ) : null}
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
