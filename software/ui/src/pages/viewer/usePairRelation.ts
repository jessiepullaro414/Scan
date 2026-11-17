/**
 * The relation of the two fits picked in the Fits panel, from the engine (APP_API.md "Kept features"): a kept
 * relation of that pair already carries it (`KeptRelation.relation`); for any other pair it is asked for with
 * `GET .../features/relate`, again whenever one of the two fits changes (fitted again, or the set read again).
 */
import { useEffect, useState } from "react";
import { ApiError, api } from "../../api/http";
import type { FeatureSet, RelationRecord } from "../../api/types";

export type PairRelationState = {
  record: RelationRecord | null;
  /** The engine could not relate them, or the request failed: its words. */
  error: string | null;
  loading: boolean;
};

const NONE: PairRelationState = { record: null, error: null, loading: false };

export function usePairRelation(pid: string, sid: string, a: string | undefined, b: string | undefined, set: FeatureSet | null): PairRelationState {
  const kept = a && b && set ? (set.relations.find((r) => (r.a === a && r.b === b) || (r.a === b && r.b === a)) ?? null) : null;
  // the engine's record is for the order the relation was kept in; the same values either way round
  const fa = a ? set?.features.find((f) => f.id === a) : undefined;
  const fb = b ? set?.features.find((f) => f.id === b) : undefined;
  // ask again when either fit's shape changes (fitted again); a rename does not change it
  const key = fa && fb && !kept?.relation ? JSON.stringify([fa.id, fa.shape, fa.mesh_version, fb.id, fb.shape, fb.mesh_version]) : null;
  const [asked, setAsked] = useState<{ key: string; state: PairRelationState } | null>(null);
  useEffect(() => {
    if (!key || !a || !b) return;
    let live = true;
    setAsked({ key, state: { ...NONE, loading: true } });
    api
      .relateFeatures(pid, sid, a, b)
      .then((r) => live && setAsked({ key, state: { record: r.relation, error: r.error ?? null, loading: false } }))
      .catch((e) => live && setAsked({ key, state: { record: null, error: e instanceof ApiError ? e.message : String(e), loading: false } }));
    return () => {
      live = false;
    };
  }, [key, pid, sid, a, b]);
  if (kept?.relation) return { record: kept.relation, error: null, loading: false };
  if (kept?.error) return { record: null, error: kept.error, loading: false };
  if (!key) return NONE;
  return asked?.key === key ? asked.state : { ...NONE, loading: true };
}
