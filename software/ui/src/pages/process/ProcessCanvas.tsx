/**
 * The Process page's 3D view: the current mesh (or the one before the last change, or both),
 * the face selection and its highlight, hole outlines with their size on hover, and the
 * camera trajectory. It renders on demand. The selection tools listen to DOM pointer events
 * and work on typed arrays (see selection.ts): a mouse move never re-renders React.
 */
import { Bvh, Line, OrbitControls } from "@react-three/drei";
import { Canvas, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import {
  Color,
  DoubleSide,
  Matrix4,
  Mesh,
  MeshBasicMaterial,
  MOUSE,
  NeutralToneMapping,
  Scene,
  ShaderMaterial,
  Sphere,
  Vector3,
  WebGLRenderTarget,
  type BufferGeometry,
  type InterleavedBufferAttribute,
  type PerspectiveCamera,
  type WebGLRenderer,
} from "three";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import { LineSegments2 } from "three/examples/jsm/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/examples/jsm/lines/LineSegmentsGeometry.js";
import type { HoleOutline, TrajectoryView } from "../../api/types";
import { CameraRig } from "../../three/CameraRig";
import { CV_TO_THREE_ROTATION, cvToThree } from "../../three/coords";
import { deviationOf } from "../../three/deviation";
import { AxisGizmo, SceneGrid, StudioEnvironment, StudioLights, useThemeColors } from "../../three/Studio";
import { BVH_SPLIT_CENTER, DeviationProbe, Model, VIEW_DIR, type Shading } from "../viewer/ViewerCanvas";
import { highlightOf, type LoadedMesh } from "./mesh";
import { buildGrid, buildScreenMap, polygonMask, type DepthImage, type ScreenMap, type SelectMode } from "./selection";

export type CompareMode = "now" | "before" | "overlay";
export type SelectTool = "navigate" | "box" | "lasso" | "brush" | "connected";

/** World frame (OpenCV) → three.js: 180° about X, as the scene group. */
const CV_MATRIX = new Matrix4().makeRotationX(Math.PI);

const HOLE_RGB = [0.95, 0.34, 0.29]; // #f2564b
const FILL_RGB = [0.25, 0.73, 0.31]; // #3fb950: holes the fill would close
const HOVER_RGB = [1, 0.82, 0.4]; // #ffd166

// ------------------------------------------------------------------ depth probe (occlusion for "visible only")
const DEPTH_VERT = /* glsl */ `
varying float vDepth;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vDepth = -mv.z;
  gl_Position = projectionMatrix * mv;
}`;
const DEPTH_FRAG = /* glsl */ `
uniform float uNear;
uniform float uFar;
varying float vDepth;
void main() {
  float d = clamp((vDepth - uNear) / (uFar - uNear), 0.0, 1.0) * 16777215.0;
  float r = floor(d / 65536.0);
  float g = floor((d - r * 65536.0) / 256.0);
  float b = floor(d - r * 65536.0 - g * 256.0);
  gl_FragColor = vec4(r / 255.0, g / 255.0, b / 255.0, 1.0);
}`;

/** Renders the mesh's view depth into a render target and reads it back (24-bit depth in RGB). */
class DepthProbe {
  private rt: WebGLRenderTarget | null = null;
  private readonly scene = new Scene();
  private readonly material = new ShaderMaterial({
    uniforms: { uNear: { value: 1 }, uFar: { value: 1000 } },
    vertexShader: DEPTH_VERT,
    fragmentShader: DEPTH_FRAG,
    side: DoubleSide,
  });
  private readonly mesh = new Mesh(undefined, this.material);
  private readonly tmp = { c: new Vector3(), p: new Vector3(), f: new Vector3(), color: new Color() };

  constructor() {
    this.mesh.matrixAutoUpdate = false;
    this.mesh.matrix.copy(CV_MATRIX);
    this.mesh.frustumCulled = false;
    this.scene.add(this.mesh);
  }

  read(gl: WebGLRenderer, camera: PerspectiveCamera, geometry: BufferGeometry, W: number, H: number): DepthImage {
    if (!this.rt || this.rt.width !== W || this.rt.height !== H) {
      this.rt?.dispose();
      this.rt = new WebGLRenderTarget(W, H, { depthBuffer: true, stencilBuffer: false });
    }
    this.mesh.geometry = geometry;
    this.mesh.matrixWorldNeedsUpdate = true;
    const s = geometry.boundingSphere!;
    const { c, p, f, color } = this.tmp;
    cvToThree(s.center, c);
    camera.getWorldPosition(p);
    camera.getWorldDirection(f);
    const d = c.sub(p).dot(f);
    const near = Math.max(0.01, d - s.radius - 2);
    const far = Math.max(near + 1, d + s.radius + 2);
    this.material.uniforms.uNear.value = near;
    this.material.uniforms.uFar.value = far;

    const prevTarget = gl.getRenderTarget();
    gl.getClearColor(color);
    const prevAlpha = gl.getClearAlpha();
    gl.setRenderTarget(this.rt);
    gl.setClearColor(0x000000, 0);
    gl.clear(true, true, false);
    gl.render(this.scene, camera);
    const rgba = new Uint8Array(W * H * 4);
    gl.readRenderTargetPixels(this.rt, 0, 0, W, H, rgba);
    gl.setRenderTarget(prevTarget);
    gl.setClearColor(color, prevAlpha);
    const pixelAngle = (2 * Math.tan((camera.fov * Math.PI) / 360)) / H;
    return { rgba, W, H, near, far, pixelAngle };
  }

  dispose() {
    this.rt?.dispose();
    this.material.dispose();
  }
}

// ------------------------------------------------------------------ highlight, ghost
/** The selected faces, drawn over the surface; redrawn at most every 40 ms while a brush paints. */
function Highlight({ mesh, color }: { mesh: LoadedMesh; color: string }) {
  const invalidate = useThree((s) => s.invalidate);
  const ref = useRef<Mesh>(null);
  const mat = useMemo(
    () =>
      new MeshBasicMaterial({
        transparent: true,
        opacity: 0.55,
        depthWrite: false,
        side: DoubleSide,
        polygonOffset: true,
        polygonOffsetFactor: -1,
        polygonOffsetUnits: -4,
      }),
    [],
  );
  useEffect(() => () => mat.dispose(), [mat]);
  useEffect(() => {
    mat.color.set(color);
    invalidate();
  }, [mat, color, invalidate]);

  useEffect(() => {
    const sel = mesh.selection;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let last = 0;
    const draw = () => {
      timer = null;
      last = performance.now();
      const obj = ref.current;
      if (!obj) return;
      if (!sel.count) {
        obj.visible = false;
        invalidate();
        return;
      }
      const g = highlightOf(mesh);
      const attr = g.getIndex()!;
      const n = sel.fillHighlight(attr.array as Uint32Array);
      g.setDrawRange(0, n);
      attr.clearUpdateRanges();
      attr.addUpdateRange(0, n);
      attr.needsUpdate = true;
      if (obj.geometry !== g) obj.geometry = g;
      obj.visible = true;
      invalidate();
    };
    draw();
    const off = sel.onChange(() => {
      if (timer) return;
      timer = setTimeout(draw, Math.max(0, 40 - (performance.now() - last)));
    });
    return () => {
      off();
      if (timer) clearTimeout(timer);
    };
  }, [mesh, invalidate]);

  return <mesh ref={ref} material={mat} visible={false} renderOrder={2} raycast={() => null} />;
}

/** The previous version as a translucent ghost behind the current surface: removed parts show. */
function Ghost({ geometry }: { geometry: BufferGeometry }) {
  const mat = useMemo(
    () =>
      new MeshBasicMaterial({
        color: "#ff9a3c",
        transparent: true,
        opacity: 0.4,
        depthWrite: false,
        side: DoubleSide,
        polygonOffset: true,
        polygonOffsetFactor: 2,
        polygonOffsetUnits: 8,
      }),
    [],
  );
  useEffect(() => () => mat.dispose(), [mat]);
  return <mesh geometry={geometry} material={mat} renderOrder={1} raycast={() => null} />;
}

// ------------------------------------------------------------------ holes
function HoleLines({ holes, fillUpTo, hovered }: { holes: HoleOutline[]; fillUpTo: number | null; hovered: number | null }) {
  const size = useThree((s) => s.size);
  const invalidate = useThree((s) => s.invalidate);
  const line = useMemo(() => {
    let n = 0;
    for (const h of holes) n += h.points.length / 3;
    const pos = new Float32Array(n * 6);
    let o = 0;
    for (const h of holes) {
      const p = h.points;
      const k = p.length / 3;
      for (let i = 0; i < k; i++) {
        const j = (i + 1) % k;
        pos[o++] = p[i * 3];
        pos[o++] = p[i * 3 + 1];
        pos[o++] = p[i * 3 + 2];
        pos[o++] = p[j * 3];
        pos[o++] = p[j * 3 + 1];
        pos[o++] = p[j * 3 + 2];
      }
    }
    const geom = new LineSegmentsGeometry();
    geom.setPositions(pos);
    geom.setColors(new Float32Array(n * 6));
    const mat = new LineMaterial({ linewidth: 2.6, vertexColors: true, worldUnits: false });
    const l = new LineSegments2(geom, mat);
    l.renderOrder = 3;
    l.raycast = () => {};
    return l;
  }, [holes]);
  useEffect(
    () => () => {
      line.geometry.dispose();
      line.material.dispose();
    },
    [line],
  );
  useEffect(() => {
    line.material.resolution.set(size.width, size.height);
    invalidate();
  }, [line, size.width, size.height, invalidate]);

  // Colours: red, green where the fill would close the hole, amber under the pointer.
  useEffect(() => {
    const attr = line.geometry.getAttribute("instanceColorStart") as InterleavedBufferAttribute | undefined;
    if (!attr) return;
    const arr = attr.data.array as Float32Array;
    let o = 0;
    holes.forEach((h, i) => {
      const rgb = i === hovered ? HOVER_RGB : fillUpTo !== null && h.perimeter_mm <= fillUpTo ? FILL_RGB : HOLE_RGB;
      const k = h.points.length / 3;
      for (let s = 0; s < k * 2; s++) {
        arr[o++] = rgb[0];
        arr[o++] = rgb[1];
        arr[o++] = rgb[2];
      }
    });
    attr.data.needsUpdate = true;
    line.material.linewidth = 2.6;
    invalidate();
  }, [line, holes, fillUpTo, hovered, invalidate]);

  return <primitive object={line} />;
}

/** Size labels on hover: the nearest hole edge within 9 px of the pointer. */
function HoleHover({ holes, label, onHover }: { holes: HoleOutline[]; label: RefObject<HTMLDivElement | null>; onHover: (i: number | null) => void }) {
  const gl = useThree((s) => s.gl);
  const camera = useThree((s) => s.camera);
  const hoverRef = useRef(onHover);
  hoverRef.current = onHover;

  useEffect(() => {
    const el = gl.domElement;
    const m = new Matrix4();
    let raf = 0;
    let last: PointerEvent | null = null;
    let current: number | null = null;
    const show = (i: number | null, x: number, y: number) => {
      const box = label.current;
      if (box) {
        if (i === null) box.style.display = "none";
        else {
          const h = holes[i];
          box.textContent = `Hole: ${h.perimeter_mm.toFixed(1)} mm around · ${h.n_edges} edges`;
          box.style.display = "block";
          // beside the pointer, flipped to stay inside the view
          const pw = box.parentElement?.clientWidth ?? Infinity;
          const ph = box.parentElement?.clientHeight ?? Infinity;
          const lx = x + 14 + box.offsetWidth > pw - 8 ? x - 14 - box.offsetWidth : x + 14;
          const ly = y + 16 + box.offsetHeight > ph - 8 ? y - 16 - box.offsetHeight : y + 16;
          box.style.transform = `translate(${Math.round(lx)}px, ${Math.round(ly)}px)`;
        }
      }
      if (i !== current) {
        current = i;
        hoverRef.current(i);
      }
    };
    const run = () => {
      raf = 0;
      const e = last;
      if (!e) return;
      const r = el.getBoundingClientRect();
      const x = e.clientX - r.left,
        y = e.clientY - r.top;
      if (e.buttons !== 0) return show(null, x, y);
      camera.updateMatrixWorld();
      m.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse).multiply(CV_MATRIX);
      const k = m.elements;
      const W = r.width,
        H = r.height;
      let best: number | null = null;
      let bestD = 81; // 9 px squared
      for (let i = 0; i < holes.length; i++) {
        const p = holes[i].points;
        const n = p.length / 3;
        let px = 0,
          py = 0,
          pv = false;
        for (let j = 0; j <= n; j++) {
          const q = (j % n) * 3; // j = n closes the loop
          const X = p[q],
            Y = p[q + 1],
            Z = p[q + 2];
          const w = k[3] * X + k[7] * Y + k[11] * Z + k[15];
          let sx = 0,
            sy = 0;
          const v = w > 1e-6;
          if (v) {
            sx = ((k[0] * X + k[4] * Y + k[8] * Z + k[12]) / w + 1) * (W / 2);
            sy = (1 - (k[1] * X + k[5] * Y + k[9] * Z + k[13]) / w) * (H / 2);
          }
          if (j > 0 && v && pv) {
            const dx = sx - px,
              dy = sy - py;
            const l2 = dx * dx + dy * dy;
            const t = l2 > 0 ? Math.max(0, Math.min(1, ((x - px) * dx + (y - py) * dy) / l2)) : 0;
            const ex = px + t * dx - x,
              ey = py + t * dy - y;
            const d2 = ex * ex + ey * ey;
            if (d2 < bestD) {
              bestD = d2;
              best = i;
            }
          }
          px = sx;
          py = sy;
          pv = v;
        }
      }
      show(best, x, y);
    };
    const onMove = (e: PointerEvent) => {
      last = e;
      if (!raf) raf = requestAnimationFrame(run);
    };
    const onLeave = () => {
      last = null;
      show(null, 0, 0);
    };
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerleave", onLeave);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerleave", onLeave);
      if (label.current) label.current.style.display = "none";
      if (current !== null) hoverRef.current(null);
    };
  }, [gl, camera, holes, label]);
  return null;
}

// ------------------------------------------------------------------ trajectory
function Trajectory({ view, accent }: { view: TrajectoryView; accent: string }) {
  const raw = useMemo(() => view.raw.filter((p) => p.length >= 3).map((p) => [p[0], p[1], p[2]] as [number, number, number]), [view]);
  const straight = useMemo(
    () => (view.straightened ? view.straightened.filter((p) => p.length >= 3).map((p) => [p[0], p[1], p[2]] as [number, number, number]) : null),
    [view],
  );
  const links = useMemo(() => {
    const pts: [number, number, number][] = [];
    for (const [i, j] of view.loops) {
      const a = raw[i],
        b = raw[j];
      if (a && b) pts.push(a, b);
    }
    return pts;
  }, [view, raw]);
  if (raw.length < 2) return null;
  const r = 2.4;
  return (
    <group>
      <Line points={raw} color="#e5a83a" lineWidth={1.6} transparent opacity={straight ? 0.75 : 0.95} dashed={!!straight} dashSize={6} gapSize={4} />
      {straight && straight.length > 1 ? <Line points={straight} color={accent} lineWidth={2.4} /> : null}
      {links.length ? <Line points={links} segments color="#b77cff" lineWidth={1.8} dashed dashSize={3} gapSize={2.5} /> : null}
      <mesh position={raw[0]}>
        <sphereGeometry args={[r, 16, 12]} />
        <meshBasicMaterial color="#3fb950" />
      </mesh>
      <mesh position={raw[raw.length - 1]}>
        <sphereGeometry args={[r, 16, 12]} />
        <meshBasicMaterial color="#f2564b" />
      </mesh>
    </group>
  );
}

// ------------------------------------------------------------------ selection tools
type Gesture =
  | { kind: "box"; mode: SelectMode; x0: number; y0: number; x1: number; y1: number }
  | { kind: "lasso"; mode: SelectMode; pts: number[] }
  | { kind: "brush"; subtract: boolean; x: number; y: number; map: ScreenMap }
  | { kind: "click"; mode: SelectMode; x: number; y: number };

type Controls = { addEventListener: (t: string, f: () => void) => void; removeEventListener: (t: string, f: () => void) => void };

/**
 * Box / lasso / brush / connected on the canvas. Shift adds, Ctrl (⌘) subtracts; the brush adds
 * by default. The screen map is cached until the camera moves, so strokes in a row cost nothing.
 */
function SelectionController({
  mesh,
  tool,
  through,
  brushRadius,
  enabled,
  overlay,
  onLive,
  onCommit,
}: {
  mesh: LoadedMesh;
  tool: SelectTool;
  through: boolean;
  brushRadius: number;
  enabled: boolean;
  overlay: RefObject<SVGSVGElement | null>;
  onLive: (count: number) => void;
  onCommit: (count: number) => void;
}) {
  const gl = useThree((s) => s.gl);
  const camera = useThree((s) => s.camera) as PerspectiveCamera;
  const size = useThree((s) => s.size);
  const controls = useThree((s) => s.controls) as unknown as Controls | null;
  const probe = useMemo(() => new DepthProbe(), []);
  useEffect(() => () => probe.dispose(), [probe]);
  const cache = useRef<ScreenMap | null>(null);
  const radius = useRef(brushRadius);
  radius.current = brushRadius;
  const liveRef = useRef(onLive);
  liveRef.current = onLive;
  const commitRef = useRef(onCommit);
  commitRef.current = onCommit;

  // Project again after the camera moves, the canvas resizes, or the mesh / mode changes.
  useEffect(() => {
    cache.current = null;
  }, [mesh, through, size.width, size.height]);
  useEffect(() => {
    if (!controls) return;
    const drop = () => {
      cache.current = null;
    };
    controls.addEventListener("change", drop);
    return () => controls.removeEventListener("change", drop);
  }, [controls]);

  useEffect(() => {
    overlay.current?.querySelector("[data-brush]")?.setAttribute("r", String(brushRadius));
  }, [brushRadius, overlay]);

  useEffect(() => {
    const el = gl.domElement;
    const svg = overlay.current;
    const rect = svg?.querySelector<SVGRectElement>("[data-box]") ?? null;
    const path = svg?.querySelector<SVGPathElement>("[data-lasso]") ?? null;
    const circle = svg?.querySelector<SVGCircleElement>("[data-brush]") ?? null;
    const hide = () => {
      rect?.setAttribute("visibility", "hidden");
      path?.setAttribute("visibility", "hidden");
      circle?.setAttribute("visibility", "hidden");
    };
    hide();
    if (!enabled || tool === "navigate") {
      el.style.cursor = "";
      return;
    }
    el.style.cursor = tool === "brush" ? "none" : tool === "connected" ? "pointer" : "crosshair";
    const sel = mesh.selection;

    const screenMap = (grid: boolean): ScreenMap => {
      let map = cache.current;
      if (!map) {
        const t0 = performance.now();
        camera.updateMatrixWorld();
        const W = Math.max(1, Math.round(size.width));
        const H = Math.max(1, Math.round(size.height));
        const depth = through ? null : probe.read(gl, camera, mesh.geometry, W, H);
        const t1 = performance.now();
        const mvp = new Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse).multiply(CV_MATRIX);
        map = buildScreenMap(sel, mvp.elements, W, H, depth);
        cache.current = map;
        if (import.meta.env.DEV)
          console.debug(
            `ohs selection: ${sel.vertices.toLocaleString()} vertices / ${sel.faces.toLocaleString()} faces projected in ${(performance.now() - t1).toFixed(1)} ms` +
              (depth ? ` (+ depth read-back ${(t1 - t0).toFixed(1)} ms)` : " (through)"),
          );
      }
      if (grid && !map.grid) map.grid = buildGrid(map);
      return map;
    };

    let g: Gesture | null = null;
    let raf = 0;
    let liveRaf = 0;
    const local = (e: PointerEvent): [number, number] => {
      const r = el.getBoundingClientRect();
      return [e.clientX - r.left, e.clientY - r.top];
    };
    const modeOf = (e: PointerEvent, def: SelectMode): SelectMode => (e.ctrlKey || e.metaKey ? "subtract" : e.shiftKey ? "add" : def);
    const live = () => {
      if (!liveRaf)
        liveRaf = requestAnimationFrame(() => {
          liveRaf = 0;
          liveRef.current(sel.count);
        });
    };
    const draw = () => {
      raf = 0;
      if (!g) return;
      if (g.kind === "box" && rect) {
        rect.setAttribute("x", String(Math.min(g.x0, g.x1)));
        rect.setAttribute("y", String(Math.min(g.y0, g.y1)));
        rect.setAttribute("width", String(Math.abs(g.x1 - g.x0)));
        rect.setAttribute("height", String(Math.abs(g.y1 - g.y0)));
        rect.dataset.mode = g.mode;
        rect.setAttribute("visibility", "visible");
      } else if (g.kind === "lasso" && path) {
        const p = g.pts;
        let d = `M${p[0].toFixed(1)} ${p[1].toFixed(1)}`;
        for (let i = 2; i < p.length; i += 2) d += `L${p[i].toFixed(1)} ${p[i + 1].toFixed(1)}`;
        path.setAttribute("d", d + "Z");
        path.dataset.mode = g.mode;
        path.setAttribute("visibility", "visible");
      }
    };
    const schedule = () => {
      if (!raf) raf = requestAnimationFrame(draw);
    };
    const moveCircle = (x: number, y: number, subtract: boolean) => {
      if (!circle) return;
      circle.setAttribute("cx", String(x));
      circle.setAttribute("cy", String(y));
      circle.dataset.mode = subtract ? "subtract" : "add";
      circle.setAttribute("visibility", "visible");
    };

    const onDown = (e: PointerEvent) => {
      if (e.button !== 0 || g) return;
      const [x, y] = local(e);
      e.preventDefault();
      try {
        el.setPointerCapture(e.pointerId);
      } catch {
        /* not capturable: moves outside the canvas just end the gesture */
      }
      if (tool === "box") g = { kind: "box", mode: modeOf(e, "replace"), x0: x, y0: y, x1: x, y1: y };
      else if (tool === "lasso") g = { kind: "lasso", mode: modeOf(e, "replace"), pts: [x, y] };
      else if (tool === "brush") {
        const subtract = e.ctrlKey || e.metaKey;
        const map = screenMap(true);
        g = { kind: "brush", subtract, x, y, map };
        sel.brushSegment(map, x, y, x, y, radius.current, subtract);
        live();
      } else g = { kind: "click", mode: modeOf(e, "replace"), x, y };
      schedule();
    };
    const onMove = (e: PointerEvent) => {
      const [x, y] = local(e);
      if (tool === "brush") moveCircle(x, y, g?.kind === "brush" ? g.subtract : e.ctrlKey || e.metaKey);
      if (!g) return;
      if (g.kind === "box") {
        g.x1 = x;
        g.y1 = y;
        schedule();
      } else if (g.kind === "lasso") {
        const p = g.pts;
        if (Math.hypot(x - p[p.length - 2], y - p[p.length - 1]) >= 2.5) {
          p.push(x, y);
          schedule();
        }
      } else if (g.kind === "brush") {
        if (cache.current !== g.map) g.map = screenMap(true); // the view zoomed mid-stroke
        sel.brushSegment(g.map, g.x, g.y, x, y, radius.current, g.subtract);
        g.x = x;
        g.y = y;
        live();
      }
    };
    const finish = (e: PointerEvent | null, cancel: boolean) => {
      const cur = g;
      g = null;
      if (!cur) return;
      if (e) {
        try {
          el.releasePointerCapture(e.pointerId);
        } catch {
          /* already released */
        }
      }
      rect?.setAttribute("visibility", "hidden");
      path?.setAttribute("visibility", "hidden");
      if (cancel) {
        if (cur.kind === "brush") commitRef.current(sel.count);
        return;
      }
      const t0 = performance.now();
      if (cur.kind === "box") {
        if (Math.abs(cur.x1 - cur.x0) < 3 && Math.abs(cur.y1 - cur.y0) < 3) return;
        sel.applyBox(screenMap(false), cur.x0, cur.y0, cur.x1, cur.y1, cur.mode);
      } else if (cur.kind === "lasso") {
        if (cur.pts.length < 6) return;
        const map = screenMap(false);
        sel.applyMask(map, polygonMask(cur.pts, map.W, map.H), cur.mode);
      } else if (cur.kind === "click") {
        const [x, y] = e ? local(e) : [cur.x, cur.y];
        if (Math.hypot(x - cur.x, y - cur.y) > 5) return;
        const map = screenMap(false);
        const f = sel.pick(map, x, y);
        if (f < 0) return;
        sel.selectConnected(f, cur.mode);
      }
      if (import.meta.env.DEV && cur.kind !== "brush")
        console.debug(`ohs selection: ${cur.kind} → ${sel.count.toLocaleString()} faces in ${(performance.now() - t0).toFixed(1)} ms`);
      commitRef.current(sel.count);
    };
    const onUp = (e: PointerEvent) => {
      if (e.button === 0) finish(e, false);
    };
    const onCancel = (e: PointerEvent) => finish(e, true);
    const onLeave = () => {
      if (!g) circle?.setAttribute("visibility", "hidden");
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && g) {
        e.preventDefault();
        e.stopImmediatePropagation();
        finish(null, true);
      }
    };
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onCancel);
    el.addEventListener("pointerleave", onLeave);
    window.addEventListener("keydown", onKey, true);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      if (liveRaf) cancelAnimationFrame(liveRaf);
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", onCancel);
      el.removeEventListener("pointerleave", onLeave);
      window.removeEventListener("keydown", onKey, true);
      el.style.cursor = "";
      if (g?.kind === "brush") commitRef.current(sel.count);
      g = null;
      hide();
    };
  }, [gl, camera, size.width, size.height, mesh, tool, through, enabled, overlay, probe]);

  return null;
}

// ------------------------------------------------------------------ canvas
export function ProcessCanvas({
  mesh,
  previous,
  compare,
  shading,
  wireframe,
  holes,
  fillUpTo,
  trajectory,
  tool,
  through,
  brushRadius,
  selectEnabled,
  overlay,
  holeLabel,
  onLive,
  onCommit,
  frameKey,
}: {
  mesh: LoadedMesh;
  previous: LoadedMesh | null;
  compare: CompareMode;
  shading: Shading;
  wireframe: boolean;
  /** Hole outlines to draw (null hides them). */
  holes: HoleOutline[] | null;
  /** Fill preview: holes up to this perimeter show green. */
  fillUpTo: number | null;
  trajectory: TrajectoryView | null;
  tool: SelectTool;
  through: boolean;
  brushRadius: number;
  selectEnabled: boolean;
  overlay: RefObject<SVGSVGElement | null>;
  holeLabel: RefObject<HTMLDivElement | null>;
  onLive: (count: number) => void;
  onCommit: (count: number) => void;
  frameKey: number;
}) {
  const colors = useThemeColors();
  const [hoverHole, setHoverHole] = useState<number | null>(null);
  const before = compare === "before" && previous ? previous : null;
  const shown = before ?? mesh;
  const selecting = selectEnabled && !before && tool !== "navigate";

  const bounds = useMemo(() => {
    const g = mesh.geometry;
    const s = g.boundingSphere!;
    return { sphere: new Sphere(cvToThree(s.center), s.radius) };
  }, [mesh]);
  const gridY = useMemo(() => -(shown.geometry.boundingBox?.max.y ?? 0) - 0.5, [shown]);

  // The deviation map's readout under the cursor needs raycasting: the tree is built on first use only
  // (indirect, so the mesh's triangle order, which the selection relies on, stays).
  const moves = shading === "deviation" ? deviationOf(shown.geometry) : null;
  const [bvh, setBvh] = useState(false);
  useEffect(() => {
    if (moves) setBvh(true);
  }, [moves]);
  const meshRef = useRef<Mesh | null>(null);
  const model = <Model geometry={shown.geometry} shading={shading} wireframe={wireframe} meshRef={meshRef} />;

  return (
    <div className="absolute inset-0" role="img" aria-label="3D view of the mesh being processed">
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
        <OrbitControls
          makeDefault
          enableDamping
          dampingFactor={0.12}
          zoomToCursor
          screenSpacePanning
          minDistance={5}
          maxDistance={20000}
          // A selection tool takes the left button: then the right button orbits and the middle pans.
          mouseButtons={selecting ? { LEFT: -1 as MOUSE, MIDDLE: MOUSE.PAN, RIGHT: MOUSE.ROTATE } : { LEFT: MOUSE.ROTATE, MIDDLE: MOUSE.DOLLY, RIGHT: MOUSE.PAN }}
        />
        <CameraRig getBounds={() => bounds.sphere} defaultDirection={VIEW_DIR} autoFrameKey={frameKey} />
        <group rotation={CV_TO_THREE_ROTATION}>
          {bvh ? (
            <Bvh firstHitOnly strategy={BVH_SPLIT_CENTER} indirect>
              {model}
            </Bvh>
          ) : (
            model
          )}
          {moves ? <DeviationProbe target={meshRef} values={moves} /> : null}
          {compare === "overlay" && previous ? <Ghost geometry={previous.geometry} /> : null}
          {!before ? <Highlight mesh={mesh} color={colors.accent} /> : null}
          {holes && !before ? <HoleLines holes={holes} fillUpTo={fillUpTo} hovered={hoverHole} /> : null}
          {trajectory ? <Trajectory view={trajectory} accent={colors.accent} /> : null}
        </group>
        {holes && holes.length && !before ? <HoleHover holes={holes} label={holeLabel} onHover={setHoverHole} /> : null}
        <SelectionController
          mesh={mesh}
          tool={tool}
          through={through}
          brushRadius={brushRadius}
          enabled={selecting}
          overlay={overlay}
          onLive={onLive}
          onCommit={onCommit}
        />
        <SceneGrid y={gridY} />
        <AxisGizmo margin={[62, 62]} />
      </Canvas>
    </div>
  );
}
