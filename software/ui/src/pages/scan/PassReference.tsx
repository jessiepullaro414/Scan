/**
 * The first scan, for reference, on the Scan page of a second pass (FEATURE_PLAN N2): its mesh drawn faintly
 * in a small view of its own, the areas asked for lit, and the scanner at each place to scan from.
 *
 * On purpose a picture beside the live view, not an overlay on it: each scan starts its own tracking, so the
 * live model's frame is not the first scan's until the two are aligned after Stop (and the alignment is
 * accepted). Drawing the first model into the live view before that would look lined up when it is not.
 */
import { OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { useEffect, useMemo } from "react";
import { DoubleSide, MeshStandardMaterial, NeutralToneMapping, Sphere, Vector3, type BufferGeometry } from "three";
import type { CheckPose } from "../../api/types";
import { Spinner } from "../../components/ui/Feedback";
import { decodeFaces, regionTriangles } from "../../lib/fits";
import { CameraRig } from "../../three/CameraRig";
import { CV_TO_THREE_ROTATION, cvToThree } from "../../three/coords";
import { StudioEnvironment, StudioLights } from "../../three/Studio";
import { useScanMeshes } from "../align/useScanMeshes";
import { overlayMaterial, RegionOverlay } from "../viewer/FitTool";
import { CHECK_COLOR } from "../viewer/useCheck";
import { VIEW_DIR } from "../viewer/ViewerCanvas";
import { ScannerAt } from "../viewer/CheckMark";

function Faint({ geometry }: { geometry: BufferGeometry }) {
  const mat = useMemo(() => new MeshStandardMaterial({ color: "#9aa3ad", roughness: 0.7, metalness: 0, side: DoubleSide, transparent: true, opacity: 0.38, depthWrite: false }), []);
  useEffect(() => () => mat.dispose(), [mat]);
  return <mesh geometry={geometry} material={mat} raycast={() => null} dispose={null} />;
}

function Lit({ geometry, faces }: { geometry: BufferGeometry; faces: string[] }) {
  const mat = useMemo(() => overlayMaterial(CHECK_COLOR, 0.85), []);
  useEffect(() => () => mat.dispose(), [mat]);
  const tris = useMemo(() => {
    if (!geometry.index) return null;
    try {
      const all = faces.flatMap((f) => Array.from(decodeFaces(f)));
      return regionTriangles(all, geometry.index.array, (geometry.userData.faceOfTri as Uint32Array | null | undefined) ?? null, geometry.userData.faceCount as number | undefined);
    } catch {
      return null; // faces of another version of the mesh: not lit (said under the view)
    }
  }, [geometry, faces]);
  return tris ? <RegionOverlay tris={tris} base={geometry} material={mat} /> : null;
}

export function PassReference({ meshUrl, firstId, faces, poses, label }: { meshUrl: string; firstId: string; faces: string[] | null; poses: CheckPose[]; label: string }) {
  const { loads, retry } = useScanMeshes(meshUrl ? [{ id: firstId, url: meshUrl }] : []);
  const load = loads[firstId];
  const geometry = load?.phase === "ready" ? load.geometry : null;
  const bounds = useMemo(() => {
    if (!geometry) return new Sphere();
    if (!geometry.boundingSphere) geometry.computeBoundingSphere();
    const s = geometry.boundingSphere!;
    const out = new Sphere(cvToThree(s.center), s.radius);
    // the places to scan from in the picture too (they lie a working distance off the part)
    for (const p of poses) out.expandByPoint(cvToThree(new Vector3(...p.position_mm)));
    return out;
  }, [geometry, poses]);
  return (
    <div className="relative h-[210px] overflow-hidden rounded-lg border border-line viewport-bg" role="img" aria-label={label}>
      {geometry ? (
        <Canvas
          frameloop="demand"
          dpr={[1, 2]}
          gl={{ antialias: true, alpha: true }}
          camera={{ fov: 35, near: 1, far: 60000, position: [0, 200, 600] }}
          onCreated={({ gl }) => {
            gl.toneMapping = NeutralToneMapping;
            gl.setClearColor(0x000000, 0);
          }}
        >
          <StudioEnvironment intensity={0.45} />
          <StudioLights strength={0.65} />
          <OrbitControls makeDefault enableDamping dampingFactor={0.12} screenSpacePanning minDistance={5} maxDistance={20000} />
          <CameraRig getBounds={() => bounds} defaultDirection={VIEW_DIR} autoFrameKey={geometry.uuid} />
          <group rotation={CV_TO_THREE_ROTATION}>
            <Faint geometry={geometry} />
            {faces?.length ? <Lit geometry={geometry} faces={faces} /> : null}
            {poses.map((p, i) => (
              <ScannerAt key={i} pose={p} label={false} />
            ))}
          </group>
        </Canvas>
      ) : load?.phase === "error" ? (
        <div className="grid h-full place-items-center p-3 text-center text-[11.5px] text-muted">
          <div>
            {load.detail}
            <button type="button" className="mt-1 block w-full text-accent underline" onClick={() => retry(firstId)}>
              Try again
            </button>
          </div>
        </div>
      ) : (
        <div className="grid h-full place-items-center text-[11.5px] text-muted">
          <span className="inline-flex items-center gap-2">
            <Spinner size={12} /> Loading the first scan…
          </span>
        </div>
      )}
    </div>
  );
}
