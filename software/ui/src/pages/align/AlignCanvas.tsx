/**
 * The Align page's 3D views (they render on demand, like the result viewer):
 *
 * - `PickView`: one scan's mesh, orbitable; a click on the part (without dragging) adds a numbered
 *   marker. The point is in the mesh's own frame (OpenCV, mm), which is what the API's pairs take.
 *   Its raycast tree is built when the pointer first comes over the view, not before.
 * - `TogetherView`: the meshes in the first scan's frame, each other one moved by its pose (for two
 *   scans the alignment's `T`, for several each scan's `T` of `align_all`), each in its own colour, so
 *   the fit can be judged by eye. One view (one canvas) for any number of scans.
 *
 * The geometries are the page's (useScanMeshes.ts): the views share them and dispose nothing but their
 * own materials.
 */
import { Bvh, Html, OrbitControls } from "@react-three/drei";
import { Canvas, useThree } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { DoubleSide, MeshStandardMaterial, NeutralToneMapping, Sphere, Vector3, type BufferGeometry, type Group, type Mesh } from "three";
import { applyPose, SCAN_PALETTE, type Vec3 } from "../../lib/align";
import { CameraRig } from "../../three/CameraRig";
import { CV_TO_THREE_ROTATION, cvToThree, matrixFromRowMajor } from "../../three/coords";
import { AxisGizmo, SceneGrid, StudioEnvironment, StudioLights } from "../../three/Studio";
import { useSurfacePick } from "../viewer/picking";
import { BVH_SPLIT_CENTER, Model, VIEW_DIR } from "../viewer/ViewerCanvas";

/** The two scans' colours, in every view and list: the one that stays, and the one that is moved. */
export const SCAN_COLOR = { target: SCAN_PALETTE[0], source: SCAN_PALETTE[1] } as const;

function Stage({
  label,
  bounds,
  gridY,
  frameKey,
  onPointerEnter,
  children,
}: {
  label: string;
  bounds: Sphere;
  gridY: number;
  frameKey: string | number;
  onPointerEnter?: () => void;
  children: ReactNode;
}) {
  return (
    <div className="absolute inset-0" role="img" aria-label={label} onPointerEnter={onPointerEnter}>
      <Canvas
        frameloop="demand"
        dpr={[1, 2]}
        gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}
        camera={{ fov: 35, near: 1, far: 60000, position: [0, 200, 600] }}
        onCreated={({ gl }) => {
          gl.toneMapping = NeutralToneMapping;
          gl.setClearColor(0x000000, 0);
        }}
      >
        <StudioEnvironment intensity={0.45} />
        <StudioLights strength={0.65} />
        <OrbitControls makeDefault enableDamping dampingFactor={0.12} zoomToCursor screenSpacePanning minDistance={5} maxDistance={20000} />
        <CameraRig getBounds={() => bounds} defaultDirection={VIEW_DIR} autoFrameKey={frameKey} />
        <group rotation={CV_TO_THREE_ROTATION}>{children}</group>
        <SceneGrid y={gridY} />
        <AxisGizmo margin={[54, 54]} />
      </Canvas>
    </div>
  );
}

function boundsOf(geometry: BufferGeometry): { sphere: Sphere; gridY: number } {
  if (!geometry.boundingSphere) geometry.computeBoundingSphere();
  if (!geometry.boundingBox) geometry.computeBoundingBox();
  const s = geometry.boundingSphere!;
  return { sphere: new Sphere(cvToThree(s.center), s.radius), gridY: -geometry.boundingBox!.max.y - 0.5 };
}

// ------------------------------------------------------------------ picking
/** A picked point: a dot on the surface and its number. Neither the label nor drei's wrapper around it takes clicks. */
function PickMark({ p, n, color, r }: { p: Vec3; n: number; color: string; r: number }) {
  return (
    <group position={p}>
      <mesh renderOrder={11}>
        <sphereGeometry args={[r, 18, 14]} />
        <meshBasicMaterial color={color} depthTest={false} transparent />
      </mesh>
      <Html center zIndexRange={[30, 0]} wrapperClass="html-label" className="html-label" style={{ pointerEvents: "none" }}>
        <div
          className="grid h-5 min-w-5 -translate-y-[18px] place-items-center rounded-full border border-white/40 px-1 font-mono text-[11.5px] font-semibold text-white shadow-lg tnum"
          style={{ background: color }}
        >
          {n}
        </div>
      </Html>
    </group>
  );
}

function PickScene({
  geometry,
  picks,
  color,
  radius,
  enabled,
  listen = false,
  bvh,
  onPick,
}: {
  geometry: BufferGeometry;
  picks: Vec3[];
  color: string;
  radius: number;
  enabled: boolean;
  /** Take clicks while not `enabled` too (the page decides whose turn it is); the marker under the cursor stays off. */
  listen?: boolean;
  /** Build the raycast tree (three-mesh-bvh, as the viewer does): a click then costs microseconds. */
  bvh: boolean;
  onPick: (p: Vec3) => void;
}) {
  const meshRef = useRef<Mesh | null>(null);
  const { hover, overFilled } = useSurfacePick(enabled || listen, meshRef, (h) => onPick(h.p));
  const r = Math.max(0.6, radius * 0.009);
  const model = <Model geometry={geometry} shading="clay" wireframe={false} meshRef={meshRef} />;
  return (
    <>
      {bvh ? (
        // keyed: drei builds the tree once, for the geometry it is mounted with
        <Bvh key={geometry.uuid} firstHitOnly strategy={BVH_SPLIT_CENTER} indirect>
          {model}
        </Bvh>
      ) : (
        model
      )}
      {picks.map((p, i) => (
        <PickMark key={i} p={p} n={i + 1} color={color} r={r} />
      ))}
      {enabled && hover ? (
        <mesh position={hover} renderOrder={12}>
          <sphereGeometry args={[r * 0.8, 14, 10]} />
          <meshBasicMaterial color={overFilled ? "#8a8781" : color} depthTest={false} transparent opacity={0.85} />
        </mesh>
      ) : null}
      {enabled && hover && overFilled ? (
        <Html position={hover} zIndexRange={[25, 0]} wrapperClass="html-label" className="html-label" style={{ pointerEvents: "none" }}>
          <div className="ml-3 -translate-y-1/2 whitespace-nowrap rounded-full border border-white/15 bg-[rgb(17_20_25/0.88)] px-2 py-0.5 text-[11.5px] text-white shadow-lg">
            Filled, not measured: pick a scanned spot
          </div>
        </Html>
      ) : null}
    </>
  );
}

/** One scan's mesh with its numbered picked points; a click on the part adds the next one. */
export function PickView({
  geometry,
  picks,
  color,
  label,
  enabled,
  listen = false,
  onPick,
}: {
  geometry: BufferGeometry;
  picks: Vec3[];
  color: string;
  /** What the view shows, for screen readers. */
  label: string;
  enabled: boolean;
  /** Clicks are passed on while not `enabled` too: a click just after the turn changes is not lost. */
  listen?: boolean;
  onPick: (p: Vec3) => void;
}) {
  const b = useMemo(() => boundsOf(geometry), [geometry]);
  const [armed, setArmed] = useState(false);
  return (
    <Stage label={label} bounds={b.sphere} gridY={b.gridY} frameKey={geometry.uuid} onPointerEnter={armed ? undefined : () => setArmed(true)}>
      <PickScene geometry={geometry} picks={picks} color={color} radius={b.sphere.radius} enabled={enabled} listen={listen} bvh={armed} onPick={onPick} />
    </Stage>
  );
}

// ------------------------------------------------------------------ both together
function useScanMaterial(color: string): MeshStandardMaterial {
  const mat = useMemo(() => new MeshStandardMaterial({ color, roughness: 0.62, metalness: 0.04, side: DoubleSide, envMapIntensity: 0.8 }), [color]);
  useEffect(() => () => mat.dispose(), [mat]);
  return mat;
}

/** Its children moved by `pose` (16 numbers, row-major): a scan in the first (target) scan's frame. */
function Moved({ pose, children }: { pose: number[]; children: ReactNode }) {
  const ref = useRef<Group | null>(null);
  const invalidate = useThree((s) => s.invalidate);
  useLayoutEffect(() => {
    const g = ref.current;
    if (!g) return;
    matrixFromRowMajor(pose, g.matrix);
    g.matrixWorldNeedsUpdate = true;
    invalidate();
  }, [pose, invalidate]);
  return (
    <group ref={ref} matrixAutoUpdate={false}>
      {children}
    </group>
  );
}

/** One scan in the first scan's frame: its mesh, moved by `pose` unless it is the first, in its own colour. */
function ScanInFrame({ geometry, pose, color }: { geometry: BufferGeometry; pose: number[] | null; color: string }) {
  const mat = useScanMaterial(color);
  // dispose={null}: the geometry is the page's, the material this view's own (disposed in useScanMaterial)
  const mesh = <mesh geometry={geometry} material={mat} raycast={() => null} dispose={null} />;
  return pose ? <Moved pose={pose}>{mesh}</Moved> : mesh;
}

/** A scan to show together with the others: `pose` (16 numbers, row-major) into the first scan's frame; null for the first one. */
export type PlacedMesh = { id: string; geometry: BufferGeometry; pose: number[] | null; color: string };

/**
 * The scans in the first one's frame, each other one moved by its pose: two scans (the source moved by
 * the alignment's `T`) or several (each placed scan by its `T` of `align_all`). The grid sits under the
 * first scan (the one with no pose).
 */
export function TogetherView({ items, label, frameKey }: { items: PlacedMesh[]; label: string; frameKey: string | number }) {
  const b = useMemo(() => {
    let sphere: Sphere | null = null;
    let gridY = 0;
    for (const it of items) {
      const g = it.geometry;
      if (!g.boundingSphere) g.computeBoundingSphere();
      if (!it.pose) {
        const a = boundsOf(g);
        gridY = a.gridY;
        sphere = sphere ? sphere.union(a.sphere) : a.sphere.clone();
        continue;
      }
      const s = g.boundingSphere!;
      const c = applyPose(it.pose, [s.center.x, s.center.y, s.center.z]);
      const moved = new Sphere(cvToThree(new Vector3(c[0], c[1], c[2])), s.radius);
      sphere = sphere ? sphere.union(moved) : moved;
    }
    return { sphere: sphere ?? new Sphere(new Vector3(), 100), gridY };
  }, [items]);
  return (
    <Stage label={label} bounds={b.sphere} gridY={b.gridY} frameKey={frameKey}>
      {items.map((it) => (
        <ScanInFrame key={it.id} geometry={it.geometry} pose={it.pose} color={it.color} />
      ))}
    </Stage>
  );
}
