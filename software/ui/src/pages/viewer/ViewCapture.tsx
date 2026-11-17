/**
 * A picture of the 3D view for the report (FEATURE_PLAN.md I3): the view as it is on screen, with the fitted shapes'
 * numbers and names marked. The canvas keeps no drawing buffer (that would cost on every frame of a large mesh), so a
 * picture is made by drawing one frame and copying the canvas into a 2D canvas in the same task, before the browser
 * clears it; the copy is scaled down to at most MAX_SIDE_PX across and saved as a PNG. The fits' labels on screen are
 * HTML over the canvas (not in it), so they are drawn onto the copy here, at their places in the view.
 *
 * `ViewCapture` sits inside the Canvas and registers itself; `captureView()` asks it from anywhere on the page.
 */
import { useThree } from "@react-three/fiber";
import { useEffect, useRef } from "react";
import { Vector3 } from "three";
import { dataUrlBase64, MAX_SIDE_PX, viewSize } from "../../lib/report";
import { cvToThree } from "../../three/coords";

type Vec3 = [number, number, number];
export type CaptureMark = { p: Vec3; text: string };
export type Shot = { data: string; width: number; height: number; url: string };

let current: (() => Shot) | null = null;

/** A picture of the 3D view as it is now (module docstring); null when no view is on the page. Throws when the
 * browser refuses to read the canvas. */
export function captureView(): Shot | null {
  return current ? current() : null;
}

function label(ctx: CanvasRenderingContext2D, x: number, y: number, text: string, scale: number) {
  const f = Math.round(12 * scale);
  ctx.font = `600 ${f}px system-ui, -apple-system, "Segoe UI", sans-serif`;
  const w = ctx.measureText(text).width + 12 * scale,
    h = f + 8 * scale;
  ctx.fillStyle = "rgba(17, 20, 25, 0.88)";
  ctx.beginPath();
  ctx.roundRect(x - w / 2, y - h / 2, w, h, h / 2);
  ctx.fill();
  ctx.fillStyle = "#ffffff";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, x, y + 0.5);
}

export function ViewCapture({ marks }: { marks: CaptureMark[] }) {
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  const camera = useThree((s) => s.camera);
  const marksRef = useRef(marks);
  marksRef.current = marks;
  useEffect(() => {
    const shoot = (): Shot => {
      gl.render(scene, camera); // one frame now: the drawing buffer holds it until this task ends
      const src = gl.domElement;
      const { width, height } = viewSize(src.width, src.height, MAX_SIDE_PX);
      const out = document.createElement("canvas");
      out.width = width;
      out.height = height;
      const ctx = out.getContext("2d");
      if (!ctx) throw new Error("this browser cannot make a picture of the view");
      ctx.drawImage(src, 0, 0, width, height);
      const scale = width / Math.max(1, src.clientWidth || src.width);
      const v = new Vector3();
      const p = new Vector3();
      for (const m of marksRef.current) {
        cvToThree(p.set(m.p[0], m.p[1], m.p[2]), v).project(camera);
        if (v.z < -1 || v.z > 1 || Math.abs(v.x) > 1 || Math.abs(v.y) > 1) continue; // behind the camera or off the view
        label(ctx, ((v.x + 1) / 2) * width, ((1 - v.y) / 2) * height, m.text, scale);
      }
      const url = out.toDataURL("image/png");
      const data = dataUrlBase64(url);
      if (!data) throw new Error("this browser cannot make a PNG of the view");
      return { data, width, height, url };
    };
    current = shoot;
    return () => {
      if (current === shoot) current = null;
    };
  }, [gl, scene, camera]);
  return null;
}
