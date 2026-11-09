/** Materials: a procedural clay matcap (no texture download) and the live-mesh material. */
import { CanvasTexture, DoubleSide, MeshStandardMaterial, SRGBColorSpace } from "three";

let clay: CanvasTexture | null = null;

/** A neutral "clay" matcap drawn on a canvas: soft key light top-left, cool rim bottom-right. */
export function clayMatcap(): CanvasTexture {
  if (clay) return clay;
  const S = 256;
  const r = S / 2;
  const c = document.createElement("canvas");
  c.width = c.height = S;
  const g = c.getContext("2d")!;
  g.fillStyle = "#2b2a28";
  g.fillRect(0, 0, S, S);
  g.save();
  g.beginPath();
  g.arc(r, r, r, 0, Math.PI * 2);
  g.clip();

  let grad = g.createRadialGradient(r * 0.7, r * 0.58, r * 0.04, r * 0.95, r * 0.95, r * 1.08);
  grad.addColorStop(0, "#f3efe8");
  grad.addColorStop(0.28, "#d6d0c6");
  grad.addColorStop(0.62, "#9a958c");
  grad.addColorStop(0.88, "#5a5751");
  grad.addColorStop(1, "#3a3835");
  g.fillStyle = grad;
  g.fillRect(0, 0, S, S);

  // cool rim light from the lower right
  grad = g.createRadialGradient(r * 0.62, r * 0.6, r * 0.7, r, r, r * 1.02);
  grad.addColorStop(0, "rgba(150,180,225,0)");
  grad.addColorStop(0.82, "rgba(150,180,225,0)");
  grad.addColorStop(1, "rgba(150,180,225,0.38)");
  g.fillStyle = grad;
  g.fillRect(0, 0, S, S);

  // soft specular
  grad = g.createRadialGradient(r * 0.66, r * 0.52, 0, r * 0.66, r * 0.52, r * 0.32);
  grad.addColorStop(0, "rgba(255,255,255,0.5)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, S, S);
  g.restore();

  clay = new CanvasTexture(c);
  clay.colorSpace = SRGBColorSpace;
  return clay;
}

/**
 * The live mesh: quality vertex colours, smooth normals, two-sided (open scans), with back
 * faces darkened so the inside of a partial surface reads as "inside".
 */
export function liveMeshMaterial(): MeshStandardMaterial {
  const m = new MeshStandardMaterial({
    vertexColors: true,
    roughness: 0.58,
    metalness: 0.0,
    side: DoubleSide,
    envMapIntensity: 0.9,
  });
  m.onBeforeCompile = (shader) => {
    shader.fragmentShader = shader.fragmentShader.replace(
      "#include <color_fragment>",
      [
        "#include <color_fragment>",
        // quality colours as a tint over light clay: the shape stays readable, the colour still says good / thin
        "  diffuseColor.rgb = mix(vec3(0.5), diffuseColor.rgb, 0.78);",
        "  if (!gl_FrontFacing) diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.16, 0.17, 0.19), 0.72);",
      ].join("\n"),
    );
  };
  m.customProgramCacheKey = () => "ohs-live-mesh";
  return m;
}

/** sRGB byte → linear byte (vertex colours are interpreted as linear by three.js). */
export const SRGB_TO_LINEAR = (() => {
  const t = new Uint8Array(256);
  for (let i = 0; i < 256; i++) {
    const c = i / 255;
    const l = c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    t[i] = Math.round(l * 255);
  }
  return t;
})();
