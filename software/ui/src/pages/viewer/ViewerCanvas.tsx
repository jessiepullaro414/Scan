/**
 * The result viewer's 3D view: the processed mesh (clay matcap, studio PBR, normals or the
 * confidence map with the sigma under the cursor, the deviation map with the move under it), an optional wireframe overlay and laser point
 * cloud, the two-click distance tool and the fitted shapes (FitTool.tsx). Renders on demand (no work
 * while the view is still).
 */
import { Bvh, Html, Line, OrbitControls } from "@react-three/drei";
import { Canvas, useThree } from "@react-three/fiber";
import { memo, useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
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
import type { FitKind } from "../../api/types";
import { deviationReadout, interpolateMove } from "../../lib/deviation";
import { applyConfidenceColors, confidenceMaterial, filledGeometry, filledMaterial, SIGMA_NONE_MM, sigmaOf } from "../../three/confidence";
import { CV_TO_THREE_ROTATION, cvToThree } from "../../three/coords";
import { applyDeviationColors, deviationOf } from "../../three/deviation";
import { clayMatcap } from "../../three/materials";
import { AxisGizmo, SceneGrid, StudioEnvironment, StudioLights, useThemeColors } from "../../three/Studio";
import { FitTool, type FitItem } from "./FitTool";
import { useSurfacePick } from "./picking";

export type Shading = "clay" | "studio" | "normals" | "confidence" | "deviation";
export type Vec3 = [number, number, number];
export type Measurement = { id: number; a: Vec3; b: Vec3 };

/** The default view direction of the result views (three.js coordinates). */
export const VIEW_DIR: [number, number, number] = [0.42, 0.5, 1];

const tuple = (v: Vector3): Vec3 => [v.x, v.y, v.z];

/**
 * How the raycast tree splits its boxes: three-mesh-bvh's CENTER (the value of its constant, the
 * library is not a direct dependency). drei's default, SAH, takes about five times as long to
 * build and froze the page for seconds on a large mesh; the hits and the time per ray are the same.
 * The tree is built `indirect` (a list of the triangles of its own): the mesh's index keeps the
 * file's order, so a hit's `faceIndex` is the file's triangle, which the Fit tool sends to the
 * engine, and the fits' regions index the same triangles. Measured on 845k triangles: the same build
 * time (about 420 ms in Node), 4 bytes per triangle, about 1 µs more per ray.
 */
export const BVH_SPLIT_CENTER = 0;

/** The surface material of the result views (also used by the Process page). */
export function useMaterial(shading: Shading): Material {
  const mat = useMemo(() => {
    const common = { side: DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 };
    if (shading === "normals") return new MeshNormalMaterial(common);
    if (shading === "studio") return new MeshStandardMaterial({ ...common, color: "#a9afb8", roughness: 0.5, metalness: 0.08, envMapIntensity: 0.85 });
    if (shading === "confidence" || shading === "deviation") return confidenceMaterial(); // vertex colours, no-value parts striped
    return new MeshMatcapMaterial({ ...common, matcap: clayMatcap() });
  }, [shading]);
  useEffect(() => () => mat.dispose(), [mat]);
  return mat;
}

/** "light" / "dark": the confidence and deviation maps' steps follow the theme. */
export function useTheme(): "light" | "dark" {
  return useUi((s) => s.theme) === "light" ? "light" : "dark";
}

/** The mesh with the chosen shading and an optional wireframe overlay. */
export function Model({ geometry, shading, wireframe, meshRef }: { geometry: BufferGeometry; shading: Shading; wireframe: boolean; meshRef?: RefObject<Mesh | null> }) {
  const theme = useTheme();
  const limit = useUi((s) => s.deviationLimit);
  // the confidence map needs the mesh's sigmas, the deviation map its moves; a mesh without them shows clay
  const map = useMemo(
    () => (shading === "confidence" ? applyConfidenceColors(geometry, theme) : shading === "deviation" ? applyDeviationColors(geometry, theme, limit) : false),
    [geometry, shading, theme, limit],
  );
  const mat = useMaterial((shading === "confidence" || shading === "deviation") && !map ? "clay" : shading);
  const invalidate = useThree((s) => s.invalidate);
  useEffect(() => {
    if (map) invalidate(); // new colours (the theme or the limit changed) with the same material and geometry
  }, [map, theme, limit, invalidate]);
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
 * The readout of a per-vertex map under the cursor: `value` is the value there (null: nothing to say,
 * "Not measured" / "new surface"), found by the caller from the triangle's corners and the weights.
 * Hidden while dragging.
 */
export function MapProbe({
  target,
  valueAt,
  text,
  deps,
}: {
  target: RefObject<Mesh | null>;
  /** The map's value at barycentric weights `w` of the triangle with corners a, b, c (null: none there). */
  valueAt: (a: number, b: number, c: number, w: number[]) => number | null;
  text: (v: number | null) => string;
  /** Reset the readout when this changes (the map's data). */
  deps: unknown;
}) {
  const gl = useThree((s) => s.gl);
  const camera = useThree((s) => s.camera);
  const invalidate = useThree((s) => s.invalidate);
  const [hit, setHit] = useState<{ p: Vec3; s: number | null } | null>(null);
  const valueRef = useRef(valueAt);
  valueRef.current = valueAt;

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
      show({ p: [p.x, p.y, p.z], s: valueRef.current(a, b, c, [bary.x, bary.y, bary.z]) });
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
  }, [gl, camera, target, deps, invalidate]);

  if (!hit) return null;
  return (
    <Html position={hit.p} zIndexRange={[25, 0]} className="html-label" style={{ pointerEvents: "none" }}>
      <div className="ml-3 -translate-y-1/2 whitespace-nowrap rounded-full border border-white/15 bg-[rgb(17_20_25/0.88)] px-2 py-0.5 font-mono text-[11.5px] text-white shadow-lg tnum">
        {text(hit.s)}
      </div>
    </Html>
  );
}

/** The confidence map's readout: the sigma under the cursor, interpolated between the measured corners, or "Not measured" (a filled hole). */
function SigmaProbe({ target, sigma }: { target: RefObject<Mesh | null>; sigma: Float32Array }) {
  const valueAt = useCallback(
    (a: number, b: number, c: number, w: number[]) => {
      const s = [sigma[a], sigma[b], sigma[c]];
      const near = w.indexOf(Math.max(...w));
      let sum = 0;
      let ws = 0;
      for (let k = 0; k < 3; k++)
        if (s[k] < SIGMA_NONE_MM) {
          sum += Math.max(w[k], 0) * s[k];
          ws += Math.max(w[k], 0);
        }
      return s[near] < SIGMA_NONE_MM && ws > 0 ? sum / ws : null;
    },
    [sigma],
  );
  return <MapProbe target={target} valueAt={valueAt} text={(s) => (s === null ? "Not measured" : fmtSigma(s))} deps={sigma} />;
}

/** The deviation map's readout: "+0.034 mm (outward)", "−0.12 mm (inward)" or "new surface". */
export function DeviationProbe({ target, values }: { target: RefObject<Mesh | null>; values: Float32Array }) {
  const valueAt = useCallback((a: number, b: number, c: number, w: number[]) => interpolateMove(values, a, b, c, w), [values]);
  return <MapProbe target={target} valueAt={valueAt} text={deviationReadout} deps={values} />;
}

function LaserCloud({ geometry, size }: { geometry: BufferGeometry; size: number }) {
  const hasColor = !!geometry.getAttribute("color");
  return (
    <points geometry={geometry} raycast={() => null} renderOrder={3}>
      <pointsMaterial size={size} sizeAttenuation={false} vertexColors={hasColor} color={hasColor ? "#ffffff" : "#ff3d6a"} depthWrite={false} transparent opacity={0.8} />
    </points>
  );
}

/**
 * One finished measurement: the line, its two end points and the distance label. Memoised, and the
 * line's points are kept between renders: the tool above renders again on every move of the cursor,
 * and drei's Line builds a new geometry whenever it is handed a new points array.
 */
const MeasurementMark = memo(function MeasurementMark({ m, color, r }: { m: Measurement; color: string; r: number }) {
  const { points, mid, d } = useMemo(() => {
    const a = new Vector3(...m.a);
    const b = new Vector3(...m.b);
    return { points: [m.a, m.b], mid: tuple(a.clone().add(b).multiplyScalar(0.5)), d: a.distanceTo(b) };
  }, [m.a, m.b]);
  return (
    <group>
      <Line points={points} color={color} lineWidth={2.2} depthTest={false} renderOrder={10} transparent />
      {points.map((p, i) => (
        <mesh key={i} position={p} renderOrder={11}>
          <sphereGeometry args={[r, 18, 14]} />
          <meshBasicMaterial color="#ffffff" depthTest={false} transparent />
        </mesh>
      ))}
      <Html position={mid} center zIndexRange={[30, 0]} className="html-label">
        <div className="rounded-full border border-white/15 bg-[rgb(17_20_25/0.9)] px-2.5 py-1 font-mono text-[12.5px] font-semibold text-white shadow-lg tnum">
          {d.toFixed(2)} mm
        </div>
      </Html>
    </group>
  );
});

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
  const accent = useThemeColors().accent;
  const { hover, overFilled } = useSurfacePick(enabled, target, (h) => onPick(h.p));

  const r = markerRadius;
  return (
    <>
      {measurements.map((m) => (
        <MeasurementMark key={m.id} m={m} color={accent} r={r} />
      ))}
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
  fit,
  fits,
  fitBusy,
  pickedFits,
  onFitPick,
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
  /** The Fit tool is on. */
  fit: boolean;
  /** Fits made on this mesh. */
  fits: FitItem[];
  fitBusy: { p: Vec3; kind: FitKind } | null;
  pickedFits: number[];
  onFitPick: (p: Vec3, face: number) => void;
  frameKey: number;
}) {
  const meshRef = useRef<Mesh | null>(null);
  const sigma = shading === "confidence" ? sigmaOf(geometry) : null;
  const moves = shading === "deviation" ? deviationOf(geometry) : null;
  const [bvh, setBvh] = useState(false);
  useEffect(() => {
    if (measure || fit || sigma || moves) setBvh(true); // build the raycast acceleration on first use only
  }, [measure, fit, sigma, moves]);

  const bounds = useMemo(() => {
    if (!geometry.boundingSphere) geometry.computeBoundingSphere();
    if (!geometry.boundingBox) geometry.computeBoundingBox();
    const s = geometry.boundingSphere!;
    return { sphere: new Sphere(cvToThree(s.center), s.radius), gridY: -geometry.boundingBox!.max.y - 0.5, radius: s.radius };
  }, [geometry]);

  const markerRadius = Math.max(0.6, bounds.radius * 0.007);
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
          {bvh ? (
            <Bvh firstHitOnly strategy={BVH_SPLIT_CENTER} indirect>
              {model}
            </Bvh>
          ) : (
            model
          )}
          {laser && showLaser ? <LaserCloud geometry={laser} size={1.6} /> : null}
          {sigma ? <SigmaProbe target={meshRef} sigma={sigma} /> : null}
          {moves ? <DeviationProbe target={meshRef} values={moves} /> : null}
          <MeasureTool enabled={measure} target={meshRef} onPick={onPick} pending={pending} measurements={measurements} markerRadius={markerRadius} />
          <FitTool enabled={fit} busy={fitBusy} target={meshRef} geometry={geometry} fits={fits} picked={pickedFits} markerRadius={markerRadius} onPick={onFitPick} />
        </group>
        <SceneGrid y={bounds.gridY} />
        <AxisGizmo margin={[62, 62]} />
      </Canvas>
    </div>
  );
}
