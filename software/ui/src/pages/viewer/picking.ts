/**
 * Picking a point on the result mesh, shared by the Measure and Fit tools: a click without dragging
 * (orbiting starts with the same button), and the point under the cursor, read at most once a frame.
 * Points are in the mesh's frame (OpenCV, mm); `tri` is the triangle hit in the file's order (the
 * viewer's raycast tree is built `indirect`, so it never reorders the index).
 */
import { useThree } from "@react-three/fiber";
import { useEffect, useRef, useState, type RefObject } from "react";
import { Raycaster, Vector2, type Mesh } from "three";
import { isFilledFace } from "../../three/confidence";

export type SurfaceHit = { p: [number, number, number]; tri: number; filled: boolean };

export function useSurfacePick(
  enabled: boolean,
  target: RefObject<Mesh | null>,
  onPick: (h: SurfaceHit) => void,
  cursor = "crosshair",
): { hover: [number, number, number] | null; overFilled: boolean } {
  const gl = useThree((s) => s.gl);
  const camera = useThree((s) => s.camera);
  const invalidate = useThree((s) => s.invalidate);
  const [hover, setHover] = useState<[number, number, number] | null>(null);
  /** The cursor is over filled surface (not measured): nothing can be picked there. */
  const [overFilled, setOverFilled] = useState(false);
  const pickRef = useRef(onPick);
  pickRef.current = onPick;

  // (the cursor is reset by the cleanup below, so a tool turned off never undoes the one turned on)
  useEffect(() => {
    if (enabled) gl.domElement.style.cursor = cursor;
  }, [enabled, cursor, gl]);

  useEffect(() => {
    const el = gl.domElement;
    if (!enabled) {
      setHover(null);
      setOverFilled(false);
      return;
    }
    const ray = new Raycaster() as Raycaster & { firstHitOnly?: boolean };
    ray.firstHitOnly = true;
    const ndc = new Vector2();
    const hit = (ev: PointerEvent): SurfaceHit | null => {
      const mesh = target.current;
      if (!mesh) return null;
      const r = el.getBoundingClientRect();
      ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      const h = ray.intersectObject(mesh, false)[0];
      if (!h) return null;
      const p = mesh.worldToLocal(h.point.clone()); // world frame (OpenCV, mm)
      return { p: [p.x, p.y, p.z], tri: h.faceIndex ?? -1, filled: !!h.face && isFilledFace(mesh.geometry, h.face.a, h.face.b, h.face.c) };
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
      if (h && !h.filled) pickRef.current(h); // filled surface was not measured: nothing there
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

  return { hover, overFilled };
}
