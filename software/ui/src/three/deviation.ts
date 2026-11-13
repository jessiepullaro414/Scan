/**
 * The deviation map on a mesh geometry: the engine's signed move of each vertex from the base mesh
 * (`GET .../process/deviation.bin`) kept in `userData.deviation`, and its colours (lib/deviation.ts)
 * written into the same `color` attribute the confidence map uses (made once, updated in place when
 * the limit, the theme or the data change; nothing runs per frame). The surface material is the
 * confidence map's: a vertex with no value (alpha 0) is drawn striped grey, the "not measured" look.
 */
import { BufferAttribute, type BufferGeometry } from "three";
import type { Deviation } from "../api/types";
import { deviationColors, type DeviationTheme } from "../lib/deviation";
import { NOT_MEASURED } from "./confidence";
import { SRGB_TO_LINEAR } from "./materials";

/** The deviation read for this geometry's mesh version, or null (not read yet, or it does not fit the vertices). */
export function deviationData(g: BufferGeometry | null | undefined): Deviation | null {
  const d = g?.userData.deviation as Deviation | undefined;
  return d && d.values.length === g!.getAttribute("position")?.count ? d : null;
}

/** The per-vertex moves of `g` (mm, NaN = new surface), or null. */
export function deviationOf(g: BufferGeometry | null | undefined): Float32Array | null {
  return deviationData(g)?.values ?? null;
}

/** Keep a deviation on the geometry it was made for (it lives and goes with it). */
export function setDeviation(g: BufferGeometry, d: Deviation): void {
  g.userData.deviation = d;
  g.userData.deviationPaint = null; // colours of an earlier array, if any
}

/**
 * Give `g` the deviation colours at `limit` mm. False when it has no deviation. Does nothing when the
 * colour attribute already shows this array at this limit and theme.
 */
export function applyDeviationColors(g: BufferGeometry, theme: DeviationTheme, limit: number): boolean {
  const values = deviationOf(g);
  if (!values) return false;
  let attr = g.getAttribute("color") as BufferAttribute | undefined;
  const paint = g.userData.deviationPaint as { values: Float32Array; theme: DeviationTheme; limit: number } | null | undefined;
  if (attr && paint && paint.values === values && paint.theme === theme && paint.limit === limit) return true;
  if (attr && attr.itemSize === 4 && attr.array.length === values.length * 4) {
    deviationColors(values, limit, theme, SRGB_TO_LINEAR, NOT_MEASURED[theme], attr.array as Uint8Array);
    attr.needsUpdate = true;
  } else {
    attr = new BufferAttribute(deviationColors(values, limit, theme, SRGB_TO_LINEAR, NOT_MEASURED[theme]), 4, true);
    g.setAttribute("color", attr);
  }
  g.userData.deviationPaint = { values, theme, limit };
  g.userData.confidenceTheme = null; // the shared colours are no longer the confidence map's
  return true;
}
