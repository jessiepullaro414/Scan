/**
 * The demo's built-in recipes (UX_PLAN U13 / U31). No imports but types, so `npm test` can compare them
 * with the engine's `BUILTIN_RECIPES` (recipes.test.ts).
 */
import type { Recipe } from "../types";

/**
 * The built-in recipes, as the engine's `BUILTIN_RECIPES` (ohs/server/processing.py) has them
 * (loose bits under 5 mm, holes up to 5 / 15 mm around, never "largest piece only").
 */
export const BUILTINS: Recipe[] = [
  {
    id: "default",
    name: "Default",
    description:
      "What Make mesh does: refines the surface with the laser lines, removes loose bits and closes pin holes up to 5 mm. No smoothing and no simplification, so measurements stay true to the scan.",
    builtin: true,
    settings: { quality: "standard", straighten: "auto", refine: "auto", voxel_mm: null },
    ops: [
      { op: "remove_floaters", params: { min_size_mm: 5, min_fraction: 0.01 } },
      { op: "fill_holes", params: { max_hole_mm: 5 } },
    ],
  },
  {
    id: "body-panel",
    name: "Body panel",
    description: "For smooth sheet metal: closes holes up to 15 mm and smooths the surface while keeping creases and edges.",
    builtin: true,
    settings: { quality: "standard", straighten: "auto", refine: "auto", voxel_mm: null },
    ops: [
      { op: "remove_floaters", params: { min_size_mm: 5, min_fraction: 0.01 } },
      { op: "fill_holes", params: { max_hole_mm: 15 } },
      { op: "smooth", params: { method: "bilateral", strength: 0.5, keep_edges: true, away_from_edges: true } },
    ],
  },
  {
    id: "bracket",
    name: "Bracket / mechanical",
    description: "For brackets and machined parts: keeps sharp edges, light smoothing, closes pin holes up to 5 mm.",
    builtin: true,
    settings: { quality: "standard", straighten: "auto", refine: "auto", voxel_mm: null },
    ops: [
      { op: "remove_floaters", params: { min_size_mm: 5, min_fraction: 0.01 } },
      { op: "fill_holes", params: { max_hole_mm: 5 } },
      { op: "smooth", params: { method: "bilateral", strength: 0.25, keep_edges: true, away_from_edges: true } },
    ],
  },
  {
    id: "quick-preview",
    name: "Quick preview",
    description: "Fast look at a scan: preview quality, no straightening, simplified to about 200,000 triangles. Not for measuring.",
    builtin: true,
    settings: { quality: "preview", straighten: "off", refine: "off", voxel_mm: null },
    ops: [
      { op: "remove_floaters", params: { min_size_mm: 5, min_fraction: 0.01 } },
      { op: "decimate", params: { target_triangles: 200000 } },
    ],
  },
];
