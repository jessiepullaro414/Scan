/**
 * The confidence map: each vertex's sigma (its standard deviation in mm, which the engine writes
 * as the PLY `quality`) as colour on the surface.
 *
 * One blue, light to dark as sigma grows, on a log scale from 0.02 mm (and less) to 1 mm (and
 * more). Each theme has its own steps, checked against its viewport background (both ends clear
 * 2:1, lightness monotone, one hue). A vertex nothing measured (sigma 50 mm: a filled hole) is
 * neutral grey with stripes, so it doesn't rest on colour alone. The clay shading is softened so
 * the colour stays close to the legend's while the shape still reads.
 */
import { BufferAttribute, BufferGeometry, DoubleSide, MeshBasicMaterial, MeshMatcapMaterial } from "three";
import { clayMatcap, SRGB_TO_LINEAR } from "./materials";
import { faceKey } from "./ply";

export type Theme = "light" | "dark";

/** A vertex sigma of 50 mm means "nothing measured it" (and caps the rest). */
export const SIGMA_NONE_MM = 50;
/** The ends of the colour scale (mm). */
export const SIGMA_LO_MM = 0.02;
export const SIGMA_HI_MM = 1;
/** The legend's ticks (mm). */
export const SIGMA_TICKS = [0.02, 0.05, 0.1, 0.3, 1];

/** Light to dark, low to high sigma (blue steps 400-700 on light, 100-550 on dark). */
export const RAMPS: Record<Theme, string[]> = {
  light: ["#3987e5", "#256abf", "#184f95", "#0d366b"],
  dark: ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab"],
};
export const NOT_MEASURED: Record<Theme, string> = { light: "#d9d4cb", dark: "#8a8781" };

/** Where a sigma sits on the scale: 0 at {@link SIGMA_LO_MM}, 1 at {@link SIGMA_HI_MM} (clamped). */
export function sigmaPos(s: number): number {
  if (!(s > SIGMA_LO_MM)) return 0;
  return Math.min(1, Math.log(s / SIGMA_LO_MM) / Math.log(SIGMA_HI_MM / SIGMA_LO_MM));
}

const rgb = (h: string): [number, number, number] => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];

/** The ramp's colour at `t` (0..1) as an sRGB hex. */
export function rampHex(theme: Theme, t: number): string {
  const stops = RAMPS[theme].map(rgb);
  const x = Math.min(Math.max(t, 0), 1) * (stops.length - 1);
  const i = Math.min(Math.floor(x), stops.length - 2);
  const f = x - i;
  return `#${[0, 1, 2].map((c) => Math.round(stops[i][c] + (stops[i + 1][c] - stops[i][c]) * f).toString(16).padStart(2, "0")).join("")}`;
}

/** A CSS gradient of the ramp (left: low sigma). */
export function rampCss(theme: Theme): string {
  return `linear-gradient(90deg, ${RAMPS[theme].join(", ")})`;
}

/** RGBA vertex colours, linear bytes; alpha 0 marks the vertices nothing measured. */
export function confidenceColors(sigma: Float32Array, theme: Theme): Uint8Array {
  const lut = new Uint8Array(256 * 3);
  for (let k = 0; k < 256; k++) {
    const c = rgb(rampHex(theme, k / 255));
    for (let j = 0; j < 3; j++) lut[k * 3 + j] = SRGB_TO_LINEAR[c[j]];
  }
  const none = rgb(NOT_MEASURED[theme]).map((v) => SRGB_TO_LINEAR[v]);
  const lo = Math.log(SIGMA_LO_MM);
  const scale = 255 / (Math.log(SIGMA_HI_MM) - lo);
  const out = new Uint8Array(sigma.length * 4);
  for (let i = 0, o = 0; i < sigma.length; i++, o += 4) {
    const s = sigma[i];
    if (!(s < SIGMA_NONE_MM)) {
      out[o] = none[0];
      out[o + 1] = none[1];
      out[o + 2] = none[2];
      continue; // alpha 0
    }
    const k = s > SIGMA_LO_MM ? Math.min(255, Math.round((Math.log(s) - lo) * scale)) * 3 : 0;
    out[o] = lut[k];
    out[o + 1] = lut[k + 1];
    out[o + 2] = lut[k + 2];
    out[o + 3] = 255;
  }
  return out;
}

/** The sigmas of a mesh geometry (set by the mesh loaders), or null. */
export function sigmaOf(g: BufferGeometry | null | undefined): Float32Array | null {
  const s = g?.userData.sigma as Float32Array | undefined;
  return s && s.length === g!.getAttribute("position")?.count ? s : null;
}

export type SigmaShares = { within_0_1: number; within_0_3: number; not_measured: number };

/**
 * The shares of the surface with sigma at most 0.1 / 0.3 mm and not measured, as the engine
 * counts them (each vertex stands for a third of its triangles' area); cached on the geometry.
 */
export function sigmaShares(g: BufferGeometry | null | undefined): SigmaShares | null {
  const sigma = sigmaOf(g);
  if (!g || !sigma || !g.index) return null;
  const cached = g.userData.sigmaShares as SigmaShares | undefined;
  if (cached) return cached;
  const p = g.getAttribute("position").array as ArrayLike<number>;
  const f = g.index.array as ArrayLike<number>;
  let a1 = 0,
    a3 = 0,
    an = 0,
    at = 0;
  const add = (i: number, w: number) => {
    const s = sigma[i];
    at += w;
    if (s <= 0.1) a1 += w;
    if (s <= 0.3) a3 += w;
    if (!(s < SIGMA_NONE_MM)) an += w;
  };
  for (let t = 0; t < f.length; t += 3) {
    const ia = f[t] * 3,
      ib = f[t + 1] * 3,
      ic = f[t + 2] * 3;
    const ux = p[ib] - p[ia],
      uy = p[ib + 1] - p[ia + 1],
      uz = p[ib + 2] - p[ia + 2];
    const vx = p[ic] - p[ia],
      vy = p[ic + 1] - p[ia + 1],
      vz = p[ic + 2] - p[ia + 2];
    const w = Math.hypot(uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx) / 6; // a third of the area
    add(f[t], w);
    add(f[t + 1], w);
    add(f[t + 2], w);
  }
  const out = at > 0 ? { within_0_1: a1 / at, within_0_3: a3 / at, not_measured: an / at } : null;
  if (out) g.userData.sigmaShares = out;
  return out;
}

/**
 * Give `g` the confidence colours of its sigmas (a `color` attribute, made once and updated in
 * place when the theme changes, so it lives and goes with the geometry). False when it has none.
 */
export function applyConfidenceColors(g: BufferGeometry, theme: Theme): boolean {
  const sigma = sigmaOf(g);
  if (!sigma) return false;
  const attr = g.getAttribute("color") as BufferAttribute | undefined;
  if (attr && g.userData.confidenceTheme === theme) return true;
  const colors = confidenceColors(sigma, theme);
  if (attr && attr.itemSize === 4 && attr.array.length === colors.length) {
    (attr.array as Uint8Array).set(colors);
    attr.needsUpdate = true;
  } else g.setAttribute("color", new BufferAttribute(colors, 4, true));
  g.userData.confidenceTheme = theme;
  g.userData.deviationPaint = null; // the shared colours are no longer the deviation map's
  return true;
}

/** The confidence map's surface material (vertex colours from {@link applyConfidenceColors}). */
export function confidenceMaterial(): MeshMatcapMaterial {
  const m = new MeshMatcapMaterial({
    matcap: clayMatcap(),
    vertexColors: true,
    side: DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
    toneMapped: false, // the colours as in the legend
  });
  m.onBeforeCompile = (shader) => {
    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <color_fragment>",
        [
          "#include <color_fragment>",
          // alpha 0: nothing measured this part (a filled hole): diagonal stripes
          "  if (vColor.a < 0.5) diffuseColor.rgb *= mix(1.0, 0.8, step(0.5, fract((gl_FragCoord.x + gl_FragCoord.y) / 10.0)));",
          "  diffuseColor.a = opacity;",
        ].join("\n"),
      )
      .replace(
        "vec3 outgoingLight = diffuseColor.rgb * matcapColor.rgb;",
        // softened shading: the shape reads, the colour stays near the legend's
        "vec3 outgoingLight = diffuseColor.rgb * mix(0.55, 1.05, dot(matcapColor.rgb, vec3(0.2126, 0.7152, 0.0722)));",
      );
  };
  m.customProgramCacheKey = () => "ohs-confidence";
  return m;
}

/**
 * Whether the triangle with corners `a`, `b`, `c` was made by hole filling (not measured). By
 * its corners, not its position in the index: a raycast BVH reorders the index (`plyGeometry`
 * keeps the filled faces' keys from the file's order).
 */
export function isFilledFace(g: BufferGeometry | null | undefined, a: number, b: number, c: number): boolean {
  const keys = g?.userData.filledKeys as Set<string> | undefined;
  return !!keys && keys.has(faceKey(a, b, c));
}

/**
 * The filled faces of `g` as their own geometry (sharing its positions; made once and kept in
 * `userData.filledGeometry`, disposed with it by `disposeMeshGeometry`), or null without any.
 */
export function filledGeometry(g: BufferGeometry): BufferGeometry | null {
  const idx = g.userData.filledIndex as Uint32Array | undefined;
  if (!idx || !idx.length) return null;
  const cached = g.userData.filledGeometry as BufferGeometry | undefined;
  if (cached) return cached;
  const fg = new BufferGeometry();
  fg.setAttribute("position", g.getAttribute("position"));
  fg.setIndex(new BufferAttribute(idx, 1));
  fg.boundingBox = g.boundingBox;
  fg.boundingSphere = g.boundingSphere;
  g.userData.filledGeometry = fg;
  return fg;
}

/** The look of filled faces over any shading: the "not measured" grey, striped, drawn on top. */
export function filledMaterial(theme: Theme): MeshBasicMaterial {
  const m = new MeshBasicMaterial({
    color: NOT_MEASURED[theme],
    side: DoubleSide,
    transparent: true,
    opacity: 0.9,
    depthWrite: false,
    polygonOffset: true,
    polygonOffsetFactor: -1,
    polygonOffsetUnits: -1,
    toneMapped: false,
  });
  m.onBeforeCompile = (shader) => {
    shader.fragmentShader = shader.fragmentShader.replace(
      "#include <color_fragment>",
      ["#include <color_fragment>", "  diffuseColor.rgb *= mix(1.0, 0.8, step(0.5, fract((gl_FragCoord.x + gl_FragCoord.y) / 10.0)));"].join("\n"),
    );
  };
  m.customProgramCacheKey = () => "ohs-filled";
  return m;
}
