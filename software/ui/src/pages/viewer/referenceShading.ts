/**
 * The "Against the reference" shading on a mesh geometry: the comparison's deviation and state per vertex
 * (`GET .../comparison/deviation.bin`) kept in `userData.refDeviation`, and its colours (lib/reference.ts) written into
 * the same `color` attribute the other maps use (made once, updated in place when the limit, the theme or the data
 * change; a new colour array, never a new geometry; nothing runs per frame). Not measured is drawn striped by the
 * surface material (alpha 0), not on the reference a plain grey.
 *
 * The other maps (three/confidence.ts, deviation.ts, lasers.ts) do not know this one, so whether the colour attribute
 * still shows this map is told by the attribute itself: the one painted and its version then.
 */
import { BufferAttribute, type BufferGeometry } from "three";
import type { RefDeviation } from "../../api/types";
import type { DeviationTheme } from "../../lib/deviation";
import { referenceColors } from "../../lib/reference";
import { NOT_MEASURED } from "../../three/confidence";
import { SRGB_TO_LINEAR } from "../../three/materials";

type Paint = { values: Float32Array; theme: DeviationTheme; limit: number; attr: BufferAttribute; version: number };

/** The comparison read for this geometry's mesh, or null (not read yet, or it does not fit the vertices). */
export function refDeviationOf(g: BufferGeometry | null | undefined): RefDeviation | null {
  const d = g?.userData.refDeviation as RefDeviation | undefined;
  return d && d.values.length === g!.getAttribute("position")?.count ? d : null;
}

/** Keep a comparison on the geometry it was made for (it lives and goes with it). */
export function setRefDeviation(g: BufferGeometry, d: RefDeviation | null): void {
  g.userData.refDeviation = d ?? undefined;
  g.userData.refPaint = null;
}

/** Give `g` the comparison's colours at `limit` mm (the strongest colour at ± it). False when it has none. */
export function applyReferenceColors(g: BufferGeometry, theme: DeviationTheme, limit: number): boolean {
  const d = refDeviationOf(g);
  if (!d) return false;
  let attr = g.getAttribute("color") as BufferAttribute | undefined;
  const paint = g.userData.refPaint as Paint | null | undefined;
  if (attr && paint && paint.attr === attr && paint.version === attr.version && paint.values === d.values && paint.theme === theme && paint.limit === limit) return true;
  if (attr && attr.itemSize === 4 && attr.array.length === d.values.length * 4) {
    referenceColors(d.values, d.states, limit, theme, SRGB_TO_LINEAR, NOT_MEASURED[theme], attr.array as Uint8Array);
    attr.needsUpdate = true;
  } else {
    attr = new BufferAttribute(referenceColors(d.values, d.states, limit, theme, SRGB_TO_LINEAR, NOT_MEASURED[theme]), 4, true);
    g.setAttribute("color", attr);
  }
  g.userData.refPaint = { values: d.values, theme, limit, attr, version: attr.version } satisfies Paint;
  g.userData.confidenceTheme = null; // the shared colours are no longer the confidence map's
  g.userData.deviationPaint = null; // nor the deviation map's
  g.userData.laserPaint = null; // nor the Lasers shading's
  return true;
}
