/**
 * What the Process page's Smooth tab sends (pages/process/EditsPanel.tsx): the choices, their defaults,
 * and the request and its label made from them. Pure functions; `smoothEdit.test.ts` checks them.
 */
import type { EditBody, SmoothMethod } from "../api/types.ts";

/** Where to smooth: everywhere, away from sharp edges, or flat surface only. */
export type SmoothWhere = "all" | "edges" | "flat";

export type SmoothChoice = {
  method: SmoothMethod;
  strength: number; // 0..1
  keepEdges: boolean; // Taubin's "Protect sharp edges" (bilateral always keeps them)
  withinSigma: boolean; // "Stay within each point's uncertainty": no point moves more than 2σ
  where: SmoothWhere;
};

/**
 * The Smooth tab as it opens. "Within 2σ" is off unless the person turns it on (the owner's decision of
 * 2026-11-17; on until then): measured, the limit holds back only well-measured surface, where smoothing
 * helps, and protects no edge (VALIDATION.md, "Smoothing's "within 2σ" limit on today's engine").
 */
export const SMOOTH_DEFAULTS: SmoothChoice = { method: "taubin", strength: 0.5, keepEdges: true, withinSigma: false, where: "all" };

/** The edit to send, its label for the history, and why it cannot be sent (or null). `hasSigma`: the mesh has its confidence. */
export function smoothEdit(c: SmoothChoice, hasSigma: boolean): { body: EditBody; label: string; invalid: string | null } {
  const within = c.withinSigma && hasSigma;
  const keep = c.method === "bilateral" ? true : c.keepEdges;
  // the engine's label in words (UX_PLAN R12; APP_API.md "Edits"): "Smooth 50 % (keeping the shape, sharp edges kept,
  // off edges)", Taubin "evenly"; no method names
  const whereText = c.where === "flat" ? ", flat areas only" : c.where === "edges" ? ", off edges" : "";
  const how = c.method === "bilateral" ? "keeping the shape" : "evenly";
  const label = `Smooth ${Math.round(c.strength * 100)}\u202f% (${how}${keep ? ", sharp edges kept" : ""}${whereText}${within ? ", within the scatter" : ""})`;
  return {
    body: {
      op: "smooth",
      params: {
        method: c.method,
        strength: c.strength,
        keep_edges: keep,
        ...(c.where === "flat" ? { flat_only: true } : c.where === "edges" ? { away_from_edges: true } : {}),
        ...(within ? { within_sigma: true } : {}),
      },
    },
    label,
    invalid: c.strength <= 0 ? "Choose a strength above 0" : null,
  };
}
