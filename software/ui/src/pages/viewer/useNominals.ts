/**
 * The nominal values offered for the kept fits and relations on screen (APP_API.md "Nominal values"): the engine's
 * offers for each kept cylinder and hole (its diameter) and each kept relation (its values), read again whenever the
 * kept set changes (a fit kept, fitted again, an approval made) and when the bias switch is turned (`withBias`: also
 * the values a known bias alone allows; off by default, a viewer preference in store/ui.ts). Planes alone have none
 * and are not asked. While a read is on its way the last answer stays, so the chips do not flicker.
 *
 * Which offers the user ignored is kept in this browser only (lib/nominal.ts, "Ignored offers"), per scan, fit or
 * relation and value, with the measured value: a new measurement shows the offer again.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api/http";
import type { FeatureSet, NominalAnswer, NominalValueName } from "../../api/types";
import { ignoreKey, isIgnored, localIgnoreStore, setIgnored } from "../../lib/nominal";

export type NominalAnswers = Record<string, NominalAnswer | undefined>;

export function useNominalOffers(pid: string, sid: string, kept: FeatureSet | null, withBias = false): NominalAnswers {
  const [answers, setAnswers] = useState<NominalAnswers>({});
  useEffect(() => {
    if (!kept) return;
    let live = true;
    const ids = [...kept.features.filter((f) => f.kind !== "plane").map((f) => f.id), ...kept.relations.map((r) => r.id)];
    setAnswers((cur) => Object.fromEntries(ids.map((id) => [id, cur[id]])));
    for (const id of ids)
      api.nominal(pid, sid, id, undefined, withBias).then(
        (a) => live && setAnswers((cur) => ({ ...cur, [id]: a })),
        () => {
          /* no offers shown for it: "Other value…" still works */
        },
      );
    return () => {
      live = false;
    };
  }, [pid, sid, kept, withBias]);
  return answers;
}

/** Ignored offers of this scan: whether one is ignored, and ignore one (until its value is measured again). */
export function useIgnoredOffers(pid: string, sid: string) {
  const store = useMemo(() => localIgnoreStore(), []);
  const [, bump] = useState(0);
  const scan = `${pid}/${sid}`;
  const ignored = useCallback((id: string, value: NominalValueName, measured: number) => isIgnored(store, ignoreKey(scan, id, value), measured), [store, scan]);
  const ignore = useCallback(
    (id: string, value: NominalValueName, measured: number) => {
      setIgnored(store, ignoreKey(scan, id, value), measured);
      bump((x) => x + 1);
    },
    [store, scan],
  );
  return { ignored, ignore };
}
