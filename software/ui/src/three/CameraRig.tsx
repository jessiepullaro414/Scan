/**
 * Smooth camera framing for OrbitControls: "frame the model" and "look along an axis",
 * driven by `viewBus` commands, plus an optional automatic framing whenever `autoFrameKey`
 * changes (e.g. a newly loaded model). Any user drag cancels a running transition.
 */
import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useRef } from "react";
import { MathUtils, Vector3, type PerspectiveCamera, type Sphere } from "three";
import { viewBus } from "./viewBus";

type Controls = {
  target: Vector3;
  update: () => void;
  addEventListener: (type: string, fn: () => void) => void;
  removeEventListener: (type: string, fn: () => void) => void;
};

type Tween = {
  t: number;
  dur: number;
  q0: Vector3;
  q1: Vector3;
  d0: Vector3;
  d1: Vector3;
  r0: number;
  r1: number;
};

const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

/** Camera distance that fits a sphere of `radius` in the view with some margin. */
export function fitDistance(radius: number, camera: PerspectiveCamera, margin = 1.18): number {
  const vfov = MathUtils.degToRad(camera.fov);
  const hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
  const fov = Math.min(vfov, hfov);
  return Math.max(30, (radius * margin) / Math.sin(fov / 2));
}

export function CameraRig({
  getBounds,
  defaultDirection = [0.55, 0.6, 1],
  autoFrameKey,
}: {
  /** Bounds to frame, in three.js world coordinates. */
  getBounds: () => Sphere | null;
  defaultDirection?: [number, number, number];
  autoFrameKey?: string | number | null;
}) {
  const camera = useThree((s) => s.camera) as PerspectiveCamera;
  const controls = useThree((s) => s.controls) as unknown as Controls | null;
  const invalidate = useThree((s) => s.invalidate);
  const tween = useRef<Tween | null>(null);
  const boundsRef = useRef(getBounds);
  boundsRef.current = getBounds;
  const dirRef = useRef(defaultDirection);
  dirRef.current = defaultDirection;
  const startRef = useRef<((target: Vector3, dir: Vector3, dist: number, animate: boolean) => void) | null>(null);

  useEffect(() => {
    if (!controls) return;
    const start = (target: Vector3, dir: Vector3, dist: number, animate: boolean) => {
      const off = camera.position.clone().sub(controls.target);
      const r0 = off.length() || dist;
      const d0 = r0 > 1e-6 ? off.divideScalar(r0) : dir.clone();
      if (!animate) {
        controls.target.copy(target);
        camera.position.copy(target).addScaledVector(dir, dist);
        camera.near = Math.max(0.5, dist / 2000);
        camera.far = Math.max(20000, dist * 40);
        camera.updateProjectionMatrix();
        controls.update();
        tween.current = null;
        invalidate();
        return;
      }
      tween.current = { t: 0, dur: 650, q0: controls.target.clone(), q1: target.clone(), d0, d1: dir.clone(), r0, r1: dist };
      invalidate();
    };
    startRef.current = start;
    const off = viewBus.on((cmd) => {
      const s = boundsRef.current();
      if (cmd.type === "frame") {
        const center = cmd.sphere ? new Vector3(...cmd.sphere.center) : s?.center.clone();
        const radius = cmd.sphere ? cmd.sphere.radius : s?.radius;
        if (!center || radius === undefined) return;
        let dir = cmd.direction ? new Vector3(...cmd.direction) : camera.position.clone().sub(controls.target);
        if (dir.lengthSq() < 1e-9) dir = new Vector3(...dirRef.current);
        dir.normalize();
        start(center, dir, fitDistance(radius, camera, cmd.margin ?? 1.18), cmd.animate !== false);
      } else if (cmd.type === "view") {
        const target = s ? s.center.clone() : controls.target.clone();
        const dist = s ? fitDistance(s.radius, camera) : camera.position.distanceTo(controls.target);
        start(target, new Vector3(...cmd.direction).normalize(), dist, true);
      }
    });
    const cancel = () => {
      tween.current = null;
    };
    controls.addEventListener("start", cancel);
    return () => {
      off();
      startRef.current = null;
      controls.removeEventListener("start", cancel);
    };
  }, [controls, camera, invalidate]);

  // Automatic framing (no animation) when the key changes and the controls exist.
  useEffect(() => {
    if (autoFrameKey === undefined || autoFrameKey === null || !controls || !startRef.current) return;
    const s = boundsRef.current();
    if (!s) return;
    startRef.current(s.center.clone(), new Vector3(...dirRef.current).normalize(), fitDistance(s.radius, camera), false);
  }, [autoFrameKey, controls, camera]);

  useFrame((_, dt) => {
    const tw = tween.current;
    if (!tw || !controls) return;
    tw.t = Math.min(1, tw.t + (Math.min(dt, 0.05) * 1000) / tw.dur);
    const k = ease(tw.t);
    controls.target.lerpVectors(tw.q0, tw.q1, k);
    const dir = new Vector3().lerpVectors(tw.d0, tw.d1, k);
    if (dir.lengthSq() < 1e-6) dir.copy(tw.d1);
    dir.normalize();
    const r = MathUtils.lerp(tw.r0, tw.r1, k);
    camera.position.copy(controls.target).addScaledVector(dir, r);
    controls.update();
    invalidate();
    if (tw.t >= 1) tween.current = null;
  });

  return null;
}
