/**
 * The result viewer's 3D view: the processed mesh (clay matcap, studio PBR, normals or the
 * confidence map with the sigma under the cursor), an optional wireframe overlay and laser point
 * cloud, and the two-click distance tool. Renders on demand (no work while the view is still).
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
  Triangle,
  Vector2,
  Vector3,
  type BufferGeometry,
  type Material,
  type Mesh,
} from "three";
import { useUi } from "../../store/ui";
import { CameraRig } from "../../three/CameraRig";
import {
  applyConfidenceColors,
  confidenceMaterial,
  filledGeometry,
  filledMaterial,
  isFilledFace,
  SIGMA_NONE_MM,
  sigmaOf,
} from "../../three/confidence";
import { CV_TO_THREE_ROTATION, cvToThree } from "../../three/coords";
import { clayMatcap } from "../../three/materials";
import { AxisGizmo, SceneGrid, StudioEnvironment, StudioLights, useThemeColors } from "../../three/Studio";

export type Shading = "clay" | "studio" | "normals" | "confidence";
export type Vec3 = [number, number, number];
export type Measurement = { id: number; a: Vec3; b: Vec3 };

/** The default view direction of the result views (three.js coordinates). */
export const VIEW_DIR: [number, number, number] = [0.42, 0.5, 1];

const tuple = (v: Vector3): Vec3 => [v.x, v.y, v.z];

/** The surface material of the result views (also used by the Process page). */
export function useMaterial(shading: Shading): Material {
  const mat = useMemo(() => {
    const common = { side: DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 };
    if (shading === "normals") return new MeshNormalMaterial(common);
    if (shading === "studio") return new MeshStandardMaterial({ ...common, color: "#a9afb8", roughness: 0.5, metalness: 0.08, envMapIntensity: 0.85 });
    if (shading === "confidence") return confidenceMaterial();
    return new MeshMatcapMaterial({ ...common, matcap: clayMatcap() });
  }, [shading]);
  useEffect(() => () => mat.dispose(), [mat]);
  return mat;
}

/** "light" / "dark": the confidence map's steps follow the theme. */
export function useTheme(): "light" | "dark" {
  return useUi((s) => s.theme) === "light" ? "light" : "dark";
}

/** The mesh with the chosen shading and an optional wireframe overlay. */
export function Model({ geometry, shading, wireframe, meshRef }: { geometry: BufferGeometry; shading: Shading; wireframe: boolean; meshRef?: RefObject<Mesh | null> }) {
  const theme = useTheme();
  // the confidence map needs the mesh's sigmas; a mesh without them shows clay
  const map = useMemo(() => shading === "confidence" && applyConfidenceColors(geometry, theme), [geometry, shading, theme]);
  const mat = useMaterial(shading === "confidence" && !map ? "clay" : shading);
  const invalidate = useThree((s) => s.invalidate);
  useEffect(() => {
    if (map) invalidate(); // new colours (the theme changed) with the same material and geometry
  }, [map, theme, invalidate]);
  // faces made by hole filling: striped grey on top, whatever the shading (they were not measured)
  const filledG = useMemo(() => filledGeometry(geometry), [geometry]);
  const filledMat = useMemo(() => filledMaterial(theme), [theme]);
  useEffect(() => () => filledMat.dispose(), [filledMat]);
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
      {filledG ? <mesh geometry={filledG} material={filledMat} raycast={() => null} renderOrder={1} /> : null}
      {wireframe ? <mesh geometry={geometry} material={wire} raycast={() => null} renderOrder={1} /> : null}
    </>
  );
}

/** A sigma in words: "±0.042 mm" (more digits for the small ones). */
export function fmtSigma(s: number): string {
  return `±${s < 0.1 ? s.toFixed(3) : s < 1 ? s.toFixed(2) : s.toFixed(1)} mm`;
}

/**
 * The confidence map's readout: the sigma under the cursor, interpolated between the corners of
 * the triangle hit (the measured ones), or "Not measured" (a filled hole). Hidden while dragging.
 */
function SigmaProbe({ target, sigma }: { target: RefObject<Mesh | null>; sigma: Float32Array }) {
  const gl = useThree((s) => s.gl);
  const camera = useThree((s) => s.camera);
  const invalidate = useThree((s) => s.invalidate);
  const [hit, setHit] = useState<{ p: Vec3; s: number | null } | null>(null);

  useEffect(() => {
    const el = gl.domElement;
    const ray = new Raycaster() as Raycaster & { firstHitOnly?: boolean };
    ray.firstHitOnly = true;
    const ndc = new Vector2();
    const tri = new Triangle();
    const bary = new Vector3();
    let raf = 0;
    let last: PointerEvent | null = null;
    const show = (h: { p: Vec3; s: number | null } | null) => {
      setHit(h);
      invalidate();
    };
    const probe = () => {
      raf = 0;
      const mesh = target.current;
      if (!last || !mesh) return;
      if (last.buttons) return show(null); // orbiting or panning
      const r = el.getBoundingClientRect();
      ndc.set(((last.clientX - r.left) / r.width) * 2 - 1, -((last.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      const h = ray.intersectObject(mesh, false)[0];
      if (!h?.face) return show(null);
      const p = mesh.worldToLocal(h.point.clone()); // world frame (OpenCV, mm)
      const pos = mesh.geometry.getAttribute("position");
      const { a, b, c } = h.face;
      tri.setFromAttributeAndIndices(pos, a, b, c).getBarycoord(p, bary);
      const w = [bary.x, bary.y, bary.z];
      const s = [sigma[a], sigma[b], sigma[c]];
      const near = w.indexOf(Math.max(...w));
      let sum = 0;
      let ws = 0;
      for (let k = 0; k < 3; k++)
        if (s[k] < SIGMA_NONE_MM) {
          sum += Math.max(w[k], 0) * s[k];
          ws += Math.max(w[k], 0);
        }
      show({ p: [p.x, p.y, p.z], s: s[near] < SIGMA_NONE_MM && ws > 0 ? sum / ws : null });
    };
    const onMove = (e: PointerEvent) => {
      last = e;
      if (!raf) raf = requestAnimationFrame(probe);
    };
    const onLeave = () => {
      last = null;
      show(null);
    };
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerleave", onLeave);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerleave", onLeave);
      setHit(null);
    };
  }, [gl, camera, target, sigma, invalidate]);

  if (!hit) return null;
  return (
    <Html position={hit.p} zIndexRange={[25, 0]} className="html-label" style={{ pointerEvents: "none" }}>
      <div className="ml-3 -translate-y-1/2 whitespace-nowrap rounded-full border border-white/15 bg-[rgb(17_20_25/0.88)] px-2 py-0.5 font-mono text-[11.5px] text-white shadow-lg tnum">
        {hit.s === null ? "Not measured" : fmtSigma(hit.s)}
      </div>
    </Html>
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
  /** The cursor is over filled surface (not measured): no point can be picked there. */
  const [overFilled, setOverFilled] = useState(false);
  const pickRef = useRef(onPick);
  pickRef.current = onPick;

  useEffect(() => {
    const el = gl.domElement;
    if (!enabled) {
      el.style.cursor = "";
      setHover(null);
      setOverFilled(false);
      return;
    }
    el.style.cursor = "crosshair";
    const ray = new Raycaster() as Raycaster & { firstHitOnly?: boolean };
    ray.firstHitOnly = true;
    const ndc = new Vector2();
    const hit = (ev: PointerEvent): { p: Vec3; filled: boolean } | null => {
      const mesh = target.current;
      if (!mesh) return null;
      const r = el.getBoundingClientRect();
      ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      const h = ray.intersectObject(mesh, false)[0];
      if (!h) return null;
      const p = mesh.worldToLocal(h.point.clone()); // world frame (OpenCV, mm)
      return { p: [p.x, p.y, p.z], filled: !!h.face && isFilledFace(mesh.geometry, h.face.a, h.face.b, h.face.c) };
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
      const h = hit(e);
      if (h && !h.filled) pickRef.current(h.p); // filled surface was not measured: no point there
    };
    const onMove = (e: PointerEvent) => {
      if (down && (e.buttons & 1) === 1) return; // orbiting
      lastMove = e;
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        if (!lastMove) return;
        const h = hit(lastMove);
        setHover(h ? h.p : null);
        setOverFilled(!!h?.filled);
        invalidate();
      });
    };
    const onLeave = () => {
      setHover(null);
      setOverFilled(false);
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
          <meshBasicMaterial color={overFilled ? "#8a8781" : accent} depthTest={false} transparent opacity={0.85} />
        </mesh>
      ) : null}
      {enabled && hover && overFilled ? (
        <Html position={hover} zIndexRange={[25, 0]} className="html-label" style={{ pointerEvents: "none" }}>
          <div className="ml-3 -translate-y-1/2 whitespace-nowrap rounded-full border border-white/15 bg-[rgb(17_20_25/0.88)] px-2 py-0.5 text-[11.5px] text-white shadow-lg">
            Filled, not measured: pick a scanned spot
          </div>
        </Html>
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
  const sigma = shading === "confidence" ? sigmaOf(geometry) : null;
  const [bvh, setBvh] = useState(false);
  useEffect(() => {
    if (measure || sigma) setBvh(true); // build the raycast acceleration on first use only
  }, [measure, sigma]);

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
          {sigma ? <SigmaProbe target={meshRef} sigma={sigma} /> : null}
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
