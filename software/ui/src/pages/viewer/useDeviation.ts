/**
 * Reads the deviation map of the mesh on screen when the Deviation shading is on (APP_API.md "M5 second
 * slice"): one read per mesh version, kept on the geometry (`userData.deviation`, see three/deviation.ts),
 * so choosing the shading again, or a new theme or limit, costs nothing. A new mesh version brings a new
 * geometry, which is read again while the shading stays on.
 */
import { useEffect, useState } from "react";
import type { BufferGeometry } from "three";
import { ApiError, api, describeError } from "../../api/http";
import type { Deviation } from "../../api/types";
import { deviationData, setDeviation } from "../../three/deviation";

export type DeviationState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "ready"; data: Deviation }
  /** Nothing to show, and why in a sentence (no edits, the mesh changed, a failed read). */
  | { phase: "unavailable"; message: string };

const IDLE: DeviationState = { phase: "idle" };
const LOADING: DeviationState = { phase: "loading" };

/**
 * `version`: the mesh version of `geometry`, or null when not known (then only the vertex count can
 * tell a wrong array). `enabled`: the shading is on and the scan has edits.
 */
export function useDeviation(pid: string, sid: string, geometry: BufferGeometry | null, version: number | null, enabled: boolean): DeviationState {
  const [state, setState] = useState<DeviationState>(IDLE);

  useEffect(() => {
    if (!enabled || !geometry) {
      setState(IDLE);
      return;
    }
    const kept = deviationData(geometry);
    if (kept && (version === null || kept.info.version === version)) {
      setState({ phase: "ready", data: kept });
      return;
    }
    const ctrl = new AbortController();
    setState(LOADING);
    api
      .deviation(pid, sid, ctrl.signal)
      .then((d) => {
        if (ctrl.signal.aborted) return;
        if (d.values.length !== geometry.getAttribute("position").count) {
          // not this mesh (it was changed or made again since it was loaded): not painted
          setState({ phase: "unavailable", message: "The deviation doesn't fit the mesh on screen: it changed since it was loaded." });
        } else if (version !== null && d.info.version !== version) {
          setState({ phase: "unavailable", message: "The mesh changed since it was loaded, so the deviation is not for this one." });
        } else if (d.info.edits === 0) {
          setState({ phase: "unavailable", message: "No edits yet: the mesh is as measured" });
        } else {
          setDeviation(geometry, d);
          setState({ phase: "ready", data: d });
        }
      })
      .catch((e: unknown) => {
        if ((e as { name?: string }).name === "AbortError" || ctrl.signal.aborted) return;
        const detail = e instanceof ApiError && e.status === 409 ? e.detail : describeError(e).detail;
        setState({ phase: "unavailable", message: `Couldn't read the deviation. ${detail}` });
      });
    return () => ctrl.abort();
  }, [pid, sid, geometry, version, enabled]);

  // for the render before the effect above has run on a new geometry
  if (state.phase === "ready" && (!enabled || !geometry || deviationData(geometry) !== state.data)) return enabled ? LOADING : IDLE;
  return state;
}
