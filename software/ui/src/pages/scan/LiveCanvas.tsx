/**
 * The live 3D view. The mesh snapshot is copied into one growing BufferGeometry (capacity
 * doubles only when needed; otherwise the attributes are updated in place with update
 * ranges), so the scene graph is never rebuilt. Pose updates are read in the render loop
 * and smoothed; nothing here re-renders React per frame.
 */
import { Html, Line, OrbitControls } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { RotateCcw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Box3,
  BufferAttribute,
  BufferGeometry,
  DynamicDrawUsage,
  Line as ThreeLine,
  LineBasicMaterial,
  Matrix4,
  Mesh,
  NeutralToneMapping,
  Quaternion,
  Sphere,
  Vector3,
  type Group,
  type Material,
} from "three";
import type { DecodedMesh } from "../../api/binary";
import { distanceZone } from "../../lib/tracking";
import { useLive } from "../../store/live";
import { useSession } from "../../store/session";
import { CameraRig } from "../../three/CameraRig";
import { boundsOf, CV_TO_THREE_ROTATION, cvToThree, matrixFromRowMajor } from "../../three/coords";
import { liveMeshMaterial, SRGB_TO_LINEAR } from "../../three/materials";
import { ScannerGlyph } from "../../three/ScannerGlyph";
import { AxisGizmo, SceneGrid, StudioEnvironment, StudioLights, useThemeColors } from "../../three/Studio";
import { viewBus, type FrameSphere } from "../../three/viewBus";

/** Bounds of the current live model, shared with the camera rig (three.js coordinates). */
const liveModel: { sphere: Sphere | null; boxCv: Box3 | null } = { sphere: null, boxCv: null };

/** Default view before any data: behind and above the first scanner pose, looking at the working range. */
const HOME_TARGET = new Vector3(0, 0, -260);
const HOME_TARGET_ARR: [number, number, number] = [0, 0, -260];
const HOME_SPHERE = new Sphere(HOME_TARGET.clone(), 230);
const HOME_DIR: [number, number, number] = [0.55, 0.62, 1];

// ------------------------------------------------------------------ geometry
class LiveGeometry {
  geometry = new BufferGeometry();
  private vCap = 0;
  private iCap = 0;

  update(m: DecodedMesh): BufferGeometry {
    const V = m.header.vertices;
    const T = m.header.triangles;
    if (V > this.vCap || T * 3 > this.iCap) {
      const old = this.geometry;
      this.vCap = Math.ceil(Math.max(V, 4096) * 1.6);
      this.iCap = Math.ceil(Math.max(T * 3, 12288) * 1.6);
      const g = new BufferGeometry();
      g.setAttribute("position", new BufferAttribute(new Float32Array(this.vCap * 3), 3).setUsage(DynamicDrawUsage));
      g.setAttribute("normal", new BufferAttribute(new Float32Array(this.vCap * 3), 3).setUsage(DynamicDrawUsage));
      g.setAttribute("color", new BufferAttribute(new Uint8Array(this.vCap * 4), 4, true).setUsage(DynamicDrawUsage));
      g.setIndex(new BufferAttribute(new Uint32Array(this.iCap), 1).setUsage(DynamicDrawUsage));
      this.geometry = g;
      old.dispose(); // frees the old GPU buffers
    }
    const g = this.geometry;
    const pos = g.getAttribute("position") as BufferAttribute;
    const nrm = g.getAttribute("normal") as BufferAttribute;
    const col = g.getAttribute("color") as BufferAttribute;
    const idx = g.getIndex() as BufferAttribute;

    (pos.array as Float32Array).set(m.positions);
    const nArr = nrm.array as Float32Array;
    if (hasNormals(m.normals, V)) nArr.set(m.normals);
    else computeNormals(m.positions, m.indices, V, T, nArr);
    const cArr = col.array as Uint8Array;
    const src = m.colors;
    for (let i = 0, n = V * 4; i < n; i += 4) {
      cArr[i] = SRGB_TO_LINEAR[src[i]];
      cArr[i + 1] = SRGB_TO_LINEAR[src[i + 1]];
      cArr[i + 2] = SRGB_TO_LINEAR[src[i + 2]];
      cArr[i + 3] = src[i + 3];
    }
    (idx.array as Uint32Array).set(m.indices);

    for (const [a, n] of [
      [pos, V * 3],
      [nrm, V * 3],
      [col, V * 4],
      [idx, T * 3],
    ] as const) {
      a.clearUpdateRanges();
      a.addUpdateRange(0, n);
      a.needsUpdate = true;
    }
    g.setDrawRange(0, T * 3);
    const b = boundsOf(m.positions, V);
    g.boundingBox = b ? b.box : new Box3();
    g.boundingSphere = b ? b.sphere : new Sphere();
    return g;
  }
}

function hasNormals(n: Float32Array, V: number): boolean {
  const step = Math.max(1, Math.floor(V / 64));
  for (let i = 0; i < V; i += step) {
    if (n[i * 3] !== 0 || n[i * 3 + 1] !== 0 || n[i * 3 + 2] !== 0) return true;
  }
  return false;
}

function computeNormals(p: Float32Array, f: Uint32Array, V: number, T: number, out: Float32Array) {
  out.fill(0, 0, V * 3);
  for (let t = 0; t < T; t++) {
    const a = f[t * 3] * 3,
      b = f[t * 3 + 1] * 3,
      c = f[t * 3 + 2] * 3;
    const ux = p[b] - p[a],
      uy = p[b + 1] - p[a + 1],
      uz = p[b + 2] - p[a + 2];
    const vx = p[c] - p[a],
      vy = p[c + 1] - p[a + 1],
      vz = p[c + 2] - p[a + 2];
    const nx = uy * vz - uz * vy,
      ny = uz * vx - ux * vz,
      nz = ux * vy - uy * vx;
    for (const k of [a, b, c]) {
      out[k] += nx;
      out[k + 1] += ny;
      out[k + 2] += nz;
    }
  }
  for (let i = 0; i < V * 3; i += 3) {
    const l = Math.hypot(out[i], out[i + 1], out[i + 2]) || 1;
    out[i] /= l;
    out[i + 1] /= l;
    out[i + 2] /= l;
  }
}

// ------------------------------------------------------------------ live mesh + auto framing
function LiveMesh({ onBounds }: { onBounds: (boxCv: Box3 | null) => void }) {
  const mesh = useRef<Mesh>(null);
  const geo = useMemo(() => new LiveGeometry(), []);
  const mat = useMemo(() => liveMeshMaterial(), []);
  const controls = useThree((s) => s.controls) as unknown as { addEventListener: (t: string, f: () => void) => void; removeEventListener: (t: string, f: () => void) => void } | null;
  const onBoundsRef = useRef(onBounds);
  onBoundsRef.current = onBounds;

  useEffect(() => {
    let framed = false;
    let follow = true;
    let lastR = 0;
    // Auto-framing follows the growing model until the user actually moves the view
    // (a plain click on the canvas doesn't count).
    let dragging = false;
    const onStart = () => {
      dragging = true;
    };
    const onChange = () => {
      if (dragging) follow = false;
    };
    const onEnd = () => {
      dragging = false;
    };
    controls?.addEventListener("start", onStart);
    controls?.addEventListener("change", onChange);
    controls?.addEventListener("end", onEnd);
    /** Model bounds grown to include the scanner, so the operator sees both. */
    const withScanner = (s: Sphere): FrameSphere => {
      const pose = useLive.getState().pose;
      const c = cvToThree(s.center);
      if (!pose) return { center: [c.x, c.y, c.z], radius: s.radius };
      const p = new Vector3(pose.T[3], -pose.T[7], -pose.T[11]);
      const d = p.distanceTo(c);
      if (d + 60 <= s.radius) return { center: [c.x, c.y, c.z], radius: s.radius };
      const r = (d + 60 + s.radius) / 2;
      const center = c.clone().add(p.sub(c).setLength(r - s.radius));
      return { center: [center.x, center.y, center.z], radius: r };
    };

    const apply = (m: DecodedMesh | null, count: number) => {
      const obj = mesh.current;
      if (!obj) return;
      if (!m || m.header.triangles === 0 || count === 0) {
        obj.visible = false;
        if (count === 0) {
          liveModel.sphere = null;
          liveModel.boxCv = null;
          framed = false;
          follow = true;
          lastR = 0;
          onBoundsRef.current(null);
        }
        return;
      }
      const g = geo.update(m);
      if (obj.geometry !== g) obj.geometry = g;
      obj.visible = true;
      const s = g.boundingSphere!;
      liveModel.sphere = new Sphere(cvToThree(s.center), s.radius);
      liveModel.boxCv = g.boundingBox!.clone();
      onBoundsRef.current(liveModel.boxCv);
      if (!framed) {
        framed = true;
        lastR = s.radius;
        viewBus.emit({ type: "frame", animate: true, direction: HOME_DIR, sphere: withScanner(s), margin: 0.92 });
      } else if (follow && s.radius > lastR * 1.25) {
        lastR = s.radius;
        viewBus.emit({ type: "frame", animate: true, sphere: withScanner(s), margin: 0.92 });
      }
    };
    const st = useLive.getState();
    apply(st.mesh, st.meshCount);
    const unsub = useLive.subscribe((s, p) => {
      if (s.mesh !== p.mesh || s.meshCount !== p.meshCount) apply(s.mesh, s.meshCount);
    });
    return () => {
      unsub();
      controls?.removeEventListener("start", onStart);
      controls?.removeEventListener("change", onChange);
      controls?.removeEventListener("end", onEnd);
    };
  }, [geo, controls]);

  useEffect(
    () => () => {
      geo.geometry.dispose();
      mat.dispose();
    },
    [geo, mat],
  );

  return <mesh ref={mesh} material={mat} visible={false} />;
}

// ------------------------------------------------------------------ scanner, aim line, ghost, trail
const ZONE_COLOR = { none: "#8a929e", "too-close": "#f2564b", close: "#e5a83a", good: "#3fb950", far: "#e5a83a", "too-far": "#f2564b" } as const;

function LiveScanner() {
  const group = useRef<Group>(null);
  const aim = useRef<Group>(null);
  const dot = useRef<Mesh>(null);
  const c = useThemeColors();
  const weak = useSession((s) => s.status?.tracking.state === "weak");
  const zone = useSession((s) => distanceZone(s.status?.distance_mm));
  const tmp = useMemo(() => ({ m: new Matrix4(), p: new Vector3(), q: new Quaternion(), s: new Vector3() }), []);
  const cur = useMemo(() => ({ p: new Vector3(), q: new Quaternion(), init: false }), []);

  useFrame((_, dt) => {
    const g = group.current;
    if (!g) return;
    const { pose } = useLive.getState();
    const session = useSession.getState();
    const live = session.session?.state === "scanning" || session.session?.state === "paused";
    if (!pose || !pose.ok || !live) {
      g.visible = false;
      if (!pose) cur.init = false;
      return;
    }
    matrixFromRowMajor(pose.T, tmp.m).decompose(tmp.p, tmp.q, tmp.s);
    if (!cur.init || cur.p.distanceTo(tmp.p) > 350) {
      cur.p.copy(tmp.p);
      cur.q.copy(tmp.q);
      cur.init = true;
    } else {
      const a = 1 - Math.exp(-dt * 18);
      cur.p.lerp(tmp.p, a);
      cur.q.slerp(tmp.q, a);
    }
    g.position.copy(cur.p);
    g.quaternion.copy(cur.q);
    g.visible = true;
    const d = session.status?.distance_mm;
    if (aim.current && dot.current) {
      const ok = d !== null && d !== undefined && d > 0 && d < 1500;
      aim.current.visible = ok;
      dot.current.visible = ok;
      if (ok) {
        aim.current.scale.set(1, 1, d);
        dot.current.position.set(0, 0, d);
      }
    }
  });

  const color = weak ? "#e5a83a" : c.accent;
  return (
    <group ref={group} visible={false}>
      <ScannerGlyph color={color} opacity={0.95} />
      <group ref={aim}>
        <Line points={[[0, 0, 0], [0, 0, 1]]} color={ZONE_COLOR[zone]} lineWidth={1.4} transparent opacity={0.75} dashed dashSize={0.02} gapSize={0.015} />
      </group>
      <mesh ref={dot}>
        <sphereGeometry args={[3.2, 16, 12]} />
        <meshBasicMaterial color={ZONE_COLOR[zone]} />
      </mesh>
    </group>
  );
}

function GhostScanner() {
  const ghost = useLive((s) => s.ghost);
  const lost = useSession((s) => s.status?.tracking.state === "lost");
  const live = useSession((s) => s.session?.state === "scanning" || s.session?.state === "paused");
  const group = useRef<Group>(null);
  const mats = useRef<Array<Material & { opacity: number }>>([]);
  const c = useThemeColors();
  const matrix = useMemo(() => (ghost ? matrixFromRowMajor(ghost.T) : null), [ghost]);
  const visible = !!matrix && lost && live;

  useEffect(() => {
    mats.current = [];
    group.current?.traverse((o) => {
      const m = (o as unknown as { material?: Material & { opacity: number } }).material;
      if (m && "opacity" in m) {
        (m as Material & { userData: { base?: number } }).userData.base ??= m.opacity;
        mats.current.push(m);
      }
    });
  }, [visible]);

  useFrame(({ clock }) => {
    if (!visible) return;
    const k = 0.6 + 0.4 * Math.sin(clock.elapsedTime * 4);
    for (const m of mats.current) m.opacity = ((m.userData as { base?: number }).base ?? 1) * k;
  });

  if (!visible || !matrix) return null;
  return (
    <group ref={group} matrix={matrix} matrixAutoUpdate={false}>
      <ScannerGlyph color={c.ghost} opacity={0.9} dashed fillOpacity={0.12} />
      <Html position={[30, -58, 0]} center zIndexRange={[20, 0]} className="html-label">
        <div className="flex animate-pop-in items-center gap-1.5 rounded-full border border-white/20 bg-[rgb(242_86_75/0.92)] px-3 py-1 text-xs font-semibold text-white shadow-lg">
          <RotateCcw size={13} strokeWidth={2.4} aria-hidden /> Return here
        </div>
      </Html>
    </group>
  );
}

function Trail({ visible }: { visible: boolean }) {
  const N = 360;
  const color = useThemeColors().accent;
  const line = useMemo(() => {
    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(new Float32Array(N * 3), 3).setUsage(DynamicDrawUsage));
    g.setAttribute("color", new BufferAttribute(new Float32Array(N * 4), 4).setUsage(DynamicDrawUsage));
    g.setDrawRange(0, 0);
    const m = new LineBasicMaterial({ vertexColors: true, transparent: true, depthWrite: false });
    return new ThreeLine(g, m);
  }, []);
  const pts = useRef<number[]>([]);
  const lastAt = useRef(0);
  const rgb = useMemo(() => {
    const v = parseInt(color.slice(1), 16);
    return [((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255];
  }, [color]);

  useEffect(
    () =>
      useLive.subscribe((s, p) => {
        if (s.meshCount === 0 && p.meshCount !== 0) {
          pts.current = [];
          line.geometry.setDrawRange(0, 0);
        }
      }),
    [line],
  );
  useEffect(
    () => () => {
      line.geometry.dispose();
      (line.material as Material).dispose();
    },
    [line],
  );

  useFrame(() => {
    const pose = useLive.getState().pose;
    if (!pose) {
      if (pts.current.length) {
        pts.current = [];
        line.geometry.setDrawRange(0, 0);
      }
      return;
    }
    if (!pose.ok || pose.at === lastAt.current) return;
    lastAt.current = pose.at;
    const x = pose.T[3],
      y = pose.T[7],
      z = pose.T[11];
    const P = pts.current;
    const n = P.length;
    if (n >= 3) {
      const dx = x - P[n - 3],
        dy = y - P[n - 2],
        dz = z - P[n - 1];
      if (dx * dx + dy * dy + dz * dz < 9) return; // < 3 mm: skip
    }
    P.push(x, y, z);
    if (P.length > N * 3) P.splice(0, P.length - N * 3);
    const count = P.length / 3;
    const pos = line.geometry.getAttribute("position") as BufferAttribute;
    const col = line.geometry.getAttribute("color") as BufferAttribute;
    (pos.array as Float32Array).set(P);
    const ca = col.array as Float32Array;
    for (let i = 0; i < count; i++) {
      ca[i * 4] = rgb[0];
      ca[i * 4 + 1] = rgb[1];
      ca[i * 4 + 2] = rgb[2];
      ca[i * 4 + 3] = Math.pow(i / Math.max(1, count - 1), 1.6) * 0.75;
    }
    pos.needsUpdate = true;
    col.needsUpdate = true;
    line.geometry.setDrawRange(0, count);
    line.geometry.computeBoundingSphere();
  });

  return <primitive object={line} visible={visible} />;
}

// ------------------------------------------------------------------ canvas
function Scene({ showGrid, showTrail }: { showGrid: boolean; showTrail: boolean }) {
  const [gridY, setGridY] = useState(-110);
  const lastGrid = useRef(-110);
  const onBounds = (boxCv: Box3 | null) => {
    // Grid just under the model: three.js min y = −(OpenCV max y).
    const y = boxCv ? -boxCv.max.y - 1 : -110;
    if (Math.abs(y - lastGrid.current) > 3) {
      lastGrid.current = y;
      setGridY(y);
    }
  };
  return (
    <>
      <StudioEnvironment intensity={0.5} />
      <StudioLights />
      <OrbitControls
        makeDefault
        target={HOME_TARGET_ARR}
        enableDamping
        dampingFactor={0.12}
        zoomToCursor
        screenSpacePanning
        minDistance={20}
        maxDistance={9000}
      />
      <CameraRig getBounds={() => liveModel.sphere ?? HOME_SPHERE} defaultDirection={HOME_DIR} />
      <group rotation={CV_TO_THREE_ROTATION}>
        <LiveMesh onBounds={onBounds} />
        <LiveScanner />
        <GhostScanner />
        <Trail visible={showTrail} />
      </group>
      {showGrid ? <SceneGrid y={gridY} /> : null}
      <AxisGizmo margin={[62, 62]} />
    </>
  );
}

export function LiveCanvas({ showGrid, showTrail }: { showGrid: boolean; showTrail: boolean }) {
  return (
    <div className="absolute inset-0" role="img" aria-label="Live 3D model of the scan">
      <Canvas
        dpr={[1, 2]}
        gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}
        camera={{ fov: 40, near: 2, far: 60000, position: [330, 380, 340] }}
        onCreated={({ gl }) => {
          gl.toneMapping = NeutralToneMapping;
          gl.setClearColor(0x000000, 0);
        }}
      >
        <Scene showGrid={showGrid} showTrail={showTrail} />
      </Canvas>
    </div>
  );
}
