/**
 * The demo engine's copies of the engine's texts for edits and progress (`ohs/server/processing.py`; APP_API.md
 * "Edits" and the before/now table of "Found on the real engine: the server's side"): the edit labels, the summary of
 * Remove loose bits with the rule that took its largest piece (UX_PLAN R23), and the progress lines with thousands
 * grouped and no device (R12, R19). No imports but types, so `npm test` checks them (engineWords.test.ts).
 */
import type { LooseBitsRule, MeshOp } from "../types";

const int = (n: number) => Math.round(n).toLocaleString("en-US");
/** Python's `f"{x:g}"` for the sizes the labels show. */
const g = (x: number) => (Math.abs(x) >= 1 || x === 0 ? String(Number(x.toPrecision(6))) : String(Number(x.toPrecision(2))));
const num = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);

/** The engine's default limits of Remove loose bits (`DEFAULT_FLOATERS`). */
export const DEFAULT_FLOATERS = { min_size_mm: 5, min_fraction: 0.01 };

/** "15 mm around (about Ø 5 mm)" (`holes_mm`). */
export function holesMm(around: number): string {
  const d = around / Math.PI;
  const dia = d >= 3 ? d.toFixed(0) : d >= 0.1 ? d.toFixed(1) : String(Number(d.toPrecision(2)));
  return `${g(around)} mm around (about Ø ${dia} mm)`;
}

/** The limits an edit's params use: its own, or the defaults when it names none. */
function limits(p: Record<string, unknown>): Record<string, unknown> {
  return ["min_size_mm", "min_fraction", "min_triangles"].some((k) => k in p && p[k] !== undefined) ? p : DEFAULT_FLOATERS;
}

/** An edit's label, as the engine writes it (`normalize_op`). `faces`: a selection's triangles. */
export function editLabel(op: MeshOp, p: Record<string, unknown>, faces?: number): string {
  switch (op) {
    case "remove_floaters": {
      if (p.keep_largest_only === true) return "Keep the largest piece only";
      const q = limits(p);
      const parts: string[] = [];
      if (num(q.min_size_mm) !== undefined) parts.push(`smaller than ${g(num(q.min_size_mm)!)} mm`);
      if (num(q.min_triangles) !== undefined) parts.push(`under ${int(num(q.min_triangles)!)} triangles`);
      if (num(q.min_fraction) !== undefined) parts.push(`under ${g(100 * num(q.min_fraction)!)} % of the mesh`);
      return `Remove loose bits ${parts.join(" or ")}`;
    }
    case "fill_holes": {
      const m = num(p.max_hole_mm) ?? 10;
      return p.watertight === true ? `Make watertight: close every opening up to ${holesMm(m)}` : `Fill holes up to ${holesMm(m)}`;
    }
    case "smooth": {
      const s = num(p.strength) ?? 0.5;
      const keep = p.keep_edges !== false;
      const where = p.flat_only === true ? ", flat areas only" : p.away_from_edges === true ? ", off edges" : "";
      const how = p.method === "bilateral" ? "keeping the shape" : "evenly";
      return `Smooth ${Math.round(100 * s)} % (${how}${keep ? ", sharp edges kept" : ""}${where}${p.within_sigma === true ? ", within the scatter" : ""})`;
    }
    case "decimate": {
      const t = num(p.target_triangles);
      const r = num(p.ratio);
      const e = num(p.max_error_mm);
      const base = t !== undefined ? `Simplify to ${int(t)} triangles` : r !== undefined ? `Simplify to ${g(100 * r)} % of the triangles` : "";
      return e === undefined ? base : base ? `${base} (within ${g(e)} mm)` : `Simplify to within ${g(e)} mm`;
    }
    case "delete_faces":
      return `Delete ${int(faces ?? 0)} triangles`;
    case "keep_faces":
      return `Keep ${int(faces ?? 0)} triangles`;
    case "remove_table":
      return "Remove the table";
  }
}

/** Which limit took a piece `diagMm` across of `triangles` triangles, in a mesh of `nFaces` (`_floater_rule`). */
export function floatersRule(p: Record<string, unknown>, diagMm: number, triangles: number, nFaces: number): LooseBitsRule | null {
  if (p.keep_largest_only === true) return "largest_only";
  const q = limits(p);
  if (num(q.min_size_mm) !== undefined && diagMm < num(q.min_size_mm)!) return "size";
  if (num(q.min_triangles) !== undefined && triangles < num(q.min_triangles)!) return "triangles";
  if (num(q.min_fraction) !== undefined && triangles < num(q.min_fraction)! * nFaces) return "share";
  return null;
}

/** The summary of a removal, naming the rule that took the largest piece (`_summary`, `_rule_words`). */
export function floatersSummary(r: { pieces: number; triangles: number; largest_mm: number; largest_rule?: LooseBitsRule | null }, p: Record<string, unknown>): string {
  const n = r.pieces;
  const size = `${r.largest_mm.toFixed(1)} mm across`;
  if (r.largest_rule === "largest_only")
    return `Kept the largest piece only: removed ${int(n)} other piece${n !== 1 ? "s" : ""}, ${n !== 1 ? "the largest " : ""}${size} (${int(r.triangles)} triangles)`;
  const q = limits(p);
  const rule =
    r.largest_rule === "size"
      ? `, smaller than ${g(num(q.min_size_mm)!)} mm`
      : r.largest_rule === "triangles"
        ? ` but under ${int(num(q.min_triangles)!)} triangles`
        : r.largest_rule === "share"
          ? ` but under ${g(100 * num(q.min_fraction)!)} % of the mesh`
          : "";
  return `Removed ${int(n)} loose bit${n !== 1 ? "s" : ""}, ${n !== 1 ? "the largest " : ""}${size}${rule} (${int(r.triangles)} triangles)`;
}

/** A tracking pass's progress line (`_fuse_into`). */
export function trackProgress(done: number, total: number): string {
  return done < total ? `Following the camera: frame ${int(done)} of ${int(total)}` : `Following the camera: all ${int(total)} frames done, making the mesh`;
}
