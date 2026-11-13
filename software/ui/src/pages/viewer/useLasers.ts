/**
 * Reads the laser comparison of the mesh on screen when the Lasers shading is on (APP_API.md "M5 fourth slice", `GET
 * .../process/lasers.json` and `lasers.bin`): one read per mesh version, kept on the geometry
 * (`userData.lasers`, see three/lasers.ts), so choosing the shading again, or a new theme or limit, costs
 * nothing. A new mesh version brings a new geometry, which is read again while the shading stays on.
 * Built like useDeviation.ts; other than there, a failed read can be asked again (`retry`).
 */
import { useCallback, useEffect, useState } from "react";
import type { BufferGeometry } from "three";
import { ApiError, api } from "../../api/http";
import type { LaserInfo, LaserMap } from "../../api/types";
import { laserFailure } from "../../lib/lasers";
import { laserData, setLasers } from "../../three/lasers";

export type LaserState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "ready"; data: LaserMap }
  /** The scan has no laser points (`available` false): nothing to paint. */
  | { phase: "none"; info: LaserInfo }
  /** The read failed: what happened and what to do. `stale`: the mesh on screen is not the engine's any more, so reading the mesh again is what helps. */
  | { phase: "failed"; message: string; hint: string; stale: boolean };

const IDLE: LaserState = { phase: "idle" };
const LOADING: LaserState = { phase: "loading" };
const STALE: LaserState = {
  phase: "failed",
  message: "The mesh changed since it was loaded, so the comparison would not line up with the one on screen.",
  hint: "Reload the mesh: it is compared again by itself.",
  stale: true,
};

/**
 * `version`: the mesh version of `geometry`, or null when not known (then only the vertex count can
 * tell a wrong array). `enabled`: the shading is on.
 */
export function useLasers(pid: string, sid: string, geometry: BufferGeometry | null, version: number | null, enabled: boolean): { state: LaserState; retry: () => void } {
  const [state, setState] = useState<LaserState>(IDLE);
  const [again, setAgain] = useState(0);
  const retry = useCallback(() => setAgain((n) => n + 1), []);

  useEffect(() => {
    if (!enabled || !geometry) {
      setState(IDLE);
      return;
    }
    const kept = laserData(geometry);
    if (kept && (version === null || kept.info.version === version)) {
      setState(kept.info.available ? { phase: "ready", data: kept } : { phase: "none", info: kept.info });
      return;
    }
    const ctrl = new AbortController();
    setState(LOADING);
    api
      .lasers(pid, sid, ctrl.signal)
      .then((d) => {
        if (ctrl.signal.aborted) return;
        if (d.values.length !== geometry.getAttribute("position").count || (version !== null && d.info.version !== version)) {
          setState(STALE); // not this mesh (it was changed or made again since it was loaded): not painted
          return;
        }
        setLasers(geometry, d);
        setState(d.info.available ? { phase: "ready", data: d } : { phase: "none", info: d.info });
      })
      .catch((e: unknown) => {
        if ((e as { name?: string }).name === "AbortError" || ctrl.signal.aborted) return;
        const f = e instanceof ApiError ? laserFailure(e.status, e.detail, e.hint) : laserFailure(null, e instanceof Error ? e.message : String(e), null);
        setState({ phase: "failed", ...f, stale: false });
      });
    return () => ctrl.abort();
  }, [pid, sid, geometry, version, enabled, again]);

  // for the render before the effect above has run on a new geometry
  if (state.phase === "ready" && (!enabled || !geometry || laserData(geometry) !== state.data)) return { state: enabled ? LOADING : IDLE, retry };
  if (state.phase === "none" && (!enabled || !geometry || laserData(geometry)?.info !== state.info)) return { state: enabled ? LOADING : IDLE, retry };
  return { state, retry };
}
