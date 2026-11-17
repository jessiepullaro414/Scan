/**
 * Changes to a scan's kept set (`FeatureSet`, APP_API.md "Kept features") as the engine makes them,
 * applied to the copy this window holds, so the panels follow an add, rename or delete without
 * reading the whole set again. Pure functions (store/features.ts uses them; featureSet.test.ts
 * checks them).
 */
import type { FeatureSet, KeptFeature, KeptFit, KeptMeasurement, KeptRelation, PartFrameView } from "../api/types.ts";

/** A fit just kept, its region left out (the set never holds regions). */
export function withFeature(set: FeatureSet, f: KeptFit | KeptFeature): FeatureSet {
  const { faces: _faces, ...rest } = f as KeptFit;
  const feature = rest as KeptFeature;
  const at = set.features.findIndex((x) => x.id === feature.id);
  const features = at >= 0 ? set.features.map((x, i) => (i === at ? feature : x)) : [...set.features, feature];
  return { ...set, features };
}

export function withMeasurement(set: FeatureSet, m: KeptMeasurement): FeatureSet {
  return { ...set, measurements: [...set.measurements.filter((x) => x.id !== m.id), m] };
}

export function withRelation(set: FeatureSet, r: KeptRelation): FeatureSet {
  return { ...set, relations: [...set.relations.filter((x) => x.id !== r.id), r] };
}

/** The item with this id (a fit, measurement or relation) under its new name; the others untouched. */
export function withName(set: FeatureSet, id: string, name: string): FeatureSet {
  const rename = <T extends { id: string; name: string }>(xs: T[]): T[] => (xs.some((x) => x.id === id) ? xs.map((x) => (x.id === id ? { ...x, name } : x)) : xs);
  return { ...set, features: rename(set.features), measurements: rename(set.measurements), relations: rename(set.relations) };
}

/** Without the item of this id; a fit takes the relations that use it along. */
export function without(set: FeatureSet, id: string): FeatureSet {
  return {
    ...set,
    features: set.features.filter((x) => x.id !== id),
    measurements: set.measurements.filter((x) => x.id !== id),
    relations: set.relations.filter((x) => x.id !== id && x.a !== id && x.b !== id),
  };
}

/** How many fits were made on an earlier mesh. */
export function staleFits(set: FeatureSet | null): number {
  return set ? set.features.filter((f) => f.stale).length : 0;
}

/** The set with the part's frame just built (or none: removed); the fits untouched. */
export function withFrame(set: FeatureSet, frame: PartFrameView | null): FeatureSet {
  return { ...set, frame: frame as FeatureSet["frame"] };
}

/** Whether the kept frame was built from this fit (deleting it makes the frame out of date: read the set again). */
export function frameUses(set: FeatureSet | null, id: string): boolean {
  const built = (set?.frame as { built_from?: unknown } | null | undefined)?.built_from;
  return Array.isArray(built) && built.some((s) => !!s && typeof s === "object" && (s as { id?: unknown }).id === id);
}
