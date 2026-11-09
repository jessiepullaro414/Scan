/**
 * The result viewer's 3D view: the processed mesh (clay matcap, studio PBR or normals),
 * an optional wireframe overlay and laser point cloud, and the two-click distance tool.
 * Renders on demand (no work while the view is still).
 */
import { Bvh, Html, Line, OrbitControls } from "@react-three/drei";
import { Canvas, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import {
  DoubleSide,
  MeshBasicMaterial,
  MeshMatcapMaterial,
  MeshNormalMaterial,
  MeshStandardMaterial,
  NeutralToneMapping,
  Raycaster,
  Sphere,
  Vector2,
  Vector3,
  type BufferGeometry,
  type Material,
  type Mesh,
} from "three";
import { CameraRig } from "../../three/CameraRig";
import { CV_TO_THREE_ROTATION, cvToThree } from "../../three/coords";
import { clayMatcap } from "../../three/materials";
import { AxisGizmo, SceneGrid, StudioEnvironment, StudioLights, useThemeColors } from "../../three/Studio";

export type Shading = "clay" | "studio" | "normals";
export type Vec3 = [number, number, number];
export type Measurement = { id: number; a: Vec3; b: Vec3 };

const VIEW_DIR: [number, number, number] = [0.42, 0.5, 1];

const tuple = (v: Vector3): Vec3 => [v.x, v.y, v.z];

function useMaterial(shading: Shading): Material {
  const mat = useMemo(() => {
    const common = { side: DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 };
    if (shading === "normals") return new MeshNormalMaterial(common);
    if (shading === "studio") return new MeshStandardMaterial({ ...common, color: "#a9afb8", roughness: 0.5, metalness: 0.08, envMapIntensity: 0.85 });
    return new MeshMatcapMaterial({ ...common, matcap: clayMatcap() });
  }, [shading]);
  useEffect(() => () => mat.dispose(), [mat]);
  return mat;
}

function Model({ geometry, shading, wireframe, meshRef }: { geometry: BufferGeometry; shading: Shading; wireframe: boolean; meshRef: RefObject<Mesh | null> }) {
  const mat = useMaterial(shading);
  // Dark lines over the light surface ("flat lines"), lighter over the normals view.
  const wireColor = shading === "normals" ? "#ffffff" : "#141a22";
  const wire = useMemo(
    () => new MeshBasicMaterial({ wireframe: true, color: wireColor, transparent: true, opacity: shading === "normals" ? 0.22 : 0.3, depthWrite: false }),
    [wireColor, shading],
  );
  useEffect(() => () => wire.dispose(), [wire]);
  return (
    <>
      <mesh ref={meshRef} geometry={geometry} material={mat} />
      {wireframe ? <mesh geometry={geometry} material={wire} raycast={() => null} renderOrder={1} /> : null}
    </>
  );
}

function LaserCloud({ geometry, size }: { geometry: BufferGeometry; size: number }) {
  const hasColor = !!geometry.getAttribute("color");
  return (
    <points geometry={geometry} raycast={() => null} renderOrder={3}>
      <pointsMaterial size={size} sizeAttenuation={false} vertexColors={hasColor} color={hasColor ? "#ffffff" : "#ff3d6a"} depthWrite={false} transparent opacity={0.8} />
    </points>
  );
}

/** Picking (click without dragging) and hover feedback for the distance tool. */
function MeasureTool({
  enabled,
  target,
  onPick,
  pending,
  measurements,
  markerRadius,
}: {
  enabled: boolean;
  target: RefObject<Mesh | null>;
  onPick: (p: Vec3) => void;
  pending: Vec3 | null;
  measurements: Measurement[];
  markerRadius: number;
}) {
  const gl = useThree((s) => s.gl);
  const camera = useThree((s) => s.camera);
  const invalidate = useThree((s) => s.invalidate);
  const accent = useThemeColors().accent;
  const [hover, setHover] = useState<Vec3 | null>(null);
  const pickRef = useRef(onPick);
  pickRef.current = onPick;

  useEffect(() => {
    const el = gl.domElement;
    if (!enabled) {
      el.style.cursor = "";
      setHover(null);
      return;
    }
    el.style.cursor = "crosshair";
    const ray = new Raycaster() as Raycaster & { firstHitOnly?: boolean };
    ray.firstHitOnly = true;
    const ndc = new Vector2();
    const hit = (ev: PointerEvent): Vec3 | null => {
      const mesh = target.current;
      if (!mesh) return null;
      const r = el.getBoundingClientRect();
      ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      const h = ray.intersectObject(mesh, false)[0];
      if (!h) return null;
      const p = mesh.worldToLocal(h.point.clone()); // world frame (OpenCV, mm)
      return [p.x, p.y, p.z];
    };
    let down: { x: number; y: number; t: number } | null = null;
    let raf = 0;
    let lastMove: PointerEvent | null = null;
    const onDown = (e: PointerEvent) => {
      if (e.button === 0) down = { x: e.clientX, y: e.clientY, t: performance.now() };
    };
    const onUp = (e: PointerEvent) => {
      if (!down || e.button !== 0) return;
      const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
      const dt = performance.now() - down.t;
      down = null;
      if (moved > 5 || dt > 700) return;
      const p = hit(e);
      if (p) pickRef.current(p);
    };
    const onMove = (e: PointerEvent) => {
      if (down && (e.buttons & 1) === 1) return; // orbiting
      lastMove = e;
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        if (!lastMove) return;
        setHover(hit(lastMove));
        invalidate();
      });
    };
    const onLeave = () => {
      setHover(null);
      invalidate();
    };
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerleave", onLeave);
    return () => {
      el.style.cursor = "";
      if (raf) cancelAnimationFrame(raf);
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerleave", onLeave);
    };
  }, [enabled, gl, camera, target, invalidate]);

  const r = markerRadius;
  return (
    <>
      {measurements.map((m) => {
        const a = new Vector3(...m.a);
        const b = new Vector3(...m.b);
        const d = a.distanceTo(b);
        const mid = a.clone().add(b).multiplyScalar(0.5);
        return (
          <group key={m.id}>
            <Line points={[m.a, m.b]} color={accent} lineWidth={2.2} depthTest={false} renderOrder={10} transparent />
            {[m.a, m.b].map((p, i) => (
              <mesh key={i} position={p} renderOrder={11}>
                <sphereGeometry args={[r, 18, 14]} />
                <meshBasicMaterial color="#ffffff" depthTest={false} transparent />
              </mesh>
            ))}
            <Html position={tuple(mid)} center zIndexRange={[30, 0]} className="html-label">
              <div className="rounded-full border border-white/15 bg-[rgb(17_20_25/0.9)] px-2.5 py-1 font-mono text-[12.5px] font-semibold text-white shadow-lg tnum">
                {d.toFixed(2)} mm
              </div>
            </Html>
          </group>
        );
      })}
      {pending ? (
        <group>
          <mesh position={pending} renderOrder={11}>
            <sphereGeometry args={[r, 18, 14]} />
            <meshBasicMaterial color={accent} depthTest={false} transparent />
          </mesh>
          {hover ? <Line points={[pending, hover]} color={accent} lineWidth={1.5} dashed dashSize={r * 2} gapSize={r * 1.5} depthTest={false} renderOrder={10} transparent opacity={0.8} /> : null}
          {hover ? (
            <Html position={tuple(new Vector3(...pending).add(new Vector3(...hover)).multiplyScalar(0.5))} center className="html-label">
              <div className="rounded-full bg-[rgb(17_20_25/0.75)] px-2 py-0.5 font-mono text-[11.5px] text-white/90 tnum">
                {new Vector3(...pending).distanceTo(new Vector3(...hover)).toFixed(2)} mm
              </div>
            </Html>
          ) : null}
        </group>
      ) : null}
      {enabled && hover ? (
        <mesh position={hover} renderOrder={12}>
          <sphereGeometry args={[r * 0.8, 14, 10]} />
          <meshBasicMaterial color={accent} depthTest={false} transparent opacity={0.85} />
        </mesh>
      ) : null}
    </>
  );
}

export function ViewerCanvas({
  geometry,
  laser,
  shading,
  wireframe,
  showLaser,
  measure,
  measurements,
  pending,
  onPick,
  frameKey,
}: {
  geometry: BufferGeometry;
  laser: BufferGeometry | null;
  shading: Shading;
  wireframe: boolean;
  showLaser: boolean;
  measure: boolean;
  measurements: Measurement[];
  pending: Vec3 | null;
  onPick: (p: Vec3) => void;
  frameKey: number;
}) {
  const meshRef = useRef<Mesh | null>(null);
  const [bvh, setBvh] = useState(false);
  useEffect(() => {
    if (measure) setBvh(true); // build the raycast acceleration on first use only
  }, [measure]);

  const bounds = useMemo(() => {
    if (!geometry.boundingSphere) geometry.computeBoundingSphere();
    if (!geometry.boundingBox) geometry.computeBoundingBox();
    const s = geometry.boundingSphere!;
    return { sphere: new Sphere(cvToThree(s.center), s.radius), gridY: -geometry.boundingBox!.max.y - 0.5, radius: s.radius };
  }, [geometry]);

  const model = <Model geometry={geometry} shading={shading} wireframe={wireframe} meshRef={meshRef} />;
  return (
    <div className="absolute inset-0" role="img" aria-label="3D view of the scan result">
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
        <StudioEnvironment intensity={shading === "studio" ? 0.55 : 0.4} />
        <StudioLights strength={shading === "studio" ? 0.75 : 0.6} />
        <OrbitControls makeDefault enableDamping dampingFactor={0.12} zoomToCursor screenSpacePanning minDistance={5} maxDistance={20000} />
        <CameraRig getBounds={() => bounds.sphere} defaultDirection={VIEW_DIR} autoFrameKey={frameKey} />
        <group rotation={CV_TO_THREE_ROTATION}>
          {bvh ? <Bvh firstHitOnly>{model}</Bvh> : model}
          {laser && showLaser ? <LaserCloud geometry={laser} size={1.6} /> : null}
          <MeasureTool
            enabled={measure}
            target={meshRef}
            onPick={onPick}
            pending={pending}
            measurements={measurements}
            markerRadius={Math.max(0.6, bounds.radius * 0.007)}
          />
        </group>
        <SceneGrid y={bounds.gridY} />
        <AxisGizmo margin={[62, 62]} />
      </Canvas>
    </div>
  );
}
