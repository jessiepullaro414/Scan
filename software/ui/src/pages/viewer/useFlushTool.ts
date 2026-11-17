/**
 * The Flush tool's state (FEATURE_PLAN.md I4): two clicks on the surface, one on each panel near the seam (the first
 * is panel A, the second panel B), and the flush along the seam between them, asked from the engine once both are in
 * (and again when the mesh changes). A kept seam can be opened: it is measured again on the current mesh from its own
 * clicks until it is picked again. Leaving the tool drops what is running and clears the clicks and the answer, so
 * the view comes back as it was.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../../api/http";
import type { FlushAnswer, FlushRequest, KeptSeam } from "../../api/types";

type Vec3 = [number, number, number];

export type FlushTool = {
  /** The clicks so far (0, 1 or 2). */
  clicks: Vec3[];
  addClick: (p: Vec3) => void;
  /** Pick both clicks again. */
  clear: () => void;
  /** The 3D view takes clicks (fewer than two so far, and no kept seam opened). */
  picking: boolean;
  /** The two clicks as the request, once both are in. */
  request: FlushRequest | null;
  /** The kept seam opened, or null. */
  keptId: string | null;
  openKept: (s: KeptSeam) => void;
  answer: FlushAnswer | null;
  error: ApiError | null;
  busy: boolean;
};

export function useFlushTool({ pid, sid, on, meshVersion }: { pid: string; sid: string; on: boolean; meshVersion: number | null }): FlushTool {
  const [clicks, setClicks] = useState<Vec3[]>([]);
  const [keptId, setKeptId] = useState<string | null>(null);
  const [answer, setAnswer] = useState<FlushAnswer | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  /** Each request's number: an answer is shown only when it is the newest one's. */
  const ask = useRef(0);

  const addClick = useCallback((p: Vec3) => {
    setKeptId(null);
    setClicks((cs) => (cs.length >= 2 ? [p] : [...cs, p]));
  }, []);
  const clear = useCallback(() => {
    setKeptId(null);
    setClicks([]);
  }, []);
  const openKept = useCallback((s: KeptSeam) => {
    setClicks([s.a, s.b]);
    setKeptId(s.id);
  }, []);

  // leaving the tool: nothing kept on screen
  useEffect(() => {
    if (!on) {
      ask.current++;
      setClicks([]);
      setKeptId(null);
      setAnswer(null);
      setError(null);
      setBusy(false);
    }
  }, [on]);

  const request: FlushRequest | null = clicks.length === 2 ? { a: clicks[0], b: clicks[1] } : null;
  const key = keptId ? `kept:${keptId}` : request ? JSON.stringify(request) : "";
  useEffect(() => {
    if (!on || !key) {
      setAnswer(null);
      setError(null);
      return;
    }
    const n = ++ask.current;
    setBusy(true);
    (keptId ? api.keptSeam(pid, sid, keptId) : api.flush(pid, sid, request!))
      .then((a) => {
        if (n !== ask.current) return;
        setAnswer(a);
        setError(null);
      })
      .catch((e: unknown) => {
        if (n !== ask.current) return;
        setAnswer(null);
        setError(e instanceof ApiError ? e : new ApiError(0, String(e), null, "flush"));
      })
      .finally(() => {
        if (n === ask.current) setBusy(false);
      });
  }, [on, key, meshVersion, pid, sid]); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    clicks,
    addClick,
    clear,
    picking: on && !keptId && clicks.length < 2,
    request,
    keptId,
    openKept,
    answer: on ? answer : null,
    error: on ? error : null,
    busy: on && busy,
  };
}
