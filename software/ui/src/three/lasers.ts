/**
 * The Lasers shading on a mesh geometry: the engine's signed distance from each vertex to the laser
 * points near it (`GET .../process/lasers.bin`) kept in `userData.lasers`, and its colours written into
 * the same `color` attribute the confidence and deviation maps use (made once, updated in place when
 * the limit, the theme or the data change; nothing runs per frame). The colours are the deviation
 * map's; a vertex with no value gets a plain grey (lib/lasers.ts `LASER_NO_VALUE`) at full alpha, so the
 * surface material draws it without the "not measured" stripes: no laser line there says nothing
 * against the surface.
 */
import { BufferAttribute, type BufferGeometry } from "three";
import type { LaserMap } from "../api/types";
import { deviationColors } from "../lib/deviation";
import { LASER_NO_VALUE, type LaserTheme } from "../lib/lasers";
import { SRGB_TO_LINEAR } from "./materials";

/** The laser comparison read for this geometry's mesh version, or null (not read yet, or it does not fit the vertices). */
export function laserData(g: BufferGeometry | null | undefined): LaserMap | null {
  const d = g?.userData.lasers as LaserMap | undefined;
  return d && d.values.length === g!.getAttribute("position")?.count ? d : null;
}

/** The per-vertex distances of `g` to its laser points (mm, NaN = none near), or null. */
export function laserValuesOf(g: BufferGeometry | null | undefined): Float32Array | null {
  return laserData(g)?.values ?? null;
}

/** Keep a laser comparison on the geometry it was made for (it lives and goes with it). */
export function setLasers(g: BufferGeometry, d: LaserMap): void {
  g.userData.lasers = d;
  g.userData.laserPaint = null; // colours of an earlier array, if any
}

/**
 * Give `g` the Lasers colours at `limit` mm. False when it has no laser comparison. Does nothing when
 * the colour attribute already shows this array at this limit and theme.
 */
export function applyLaserColors(g: BufferGeometry, theme: LaserTheme, limit: number): boolean {
  const values = laserValuesOf(g);
  if (!values) return false;
  let attr = g.getAttribute("color") as BufferAttribute | undefined;
  const paint = g.userData.laserPaint as { values: Float32Array; theme: LaserTheme; limit: number } | null | undefined;
  if (attr && paint && paint.values === values && paint.theme === theme && paint.limit === limit) return true;
  if (attr && attr.itemSize === 4 && attr.array.length === values.length * 4) {
    deviationColors(values, limit, theme, SRGB_TO_LINEAR, LASER_NO_VALUE[theme], attr.array as Uint8Array, 255);
    attr.needsUpdate = true;
  } else {
    attr = new BufferAttribute(deviationColors(values, limit, theme, SRGB_TO_LINEAR, LASER_NO_VALUE[theme], undefined, 255), 4, true);
    g.setAttribute("color", attr);
  }
  g.userData.laserPaint = { values, theme, limit };
  g.userData.confidenceTheme = null; // the shared colours are no longer the confidence map's
  g.userData.deviationPaint = null; // nor the deviation map's
  return true;
}
