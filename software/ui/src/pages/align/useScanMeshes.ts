/**
 * The meshes of the scans the Align page lists (two, or up to eight): each is downloaded and read once
 * and kept while its scan stays in the list (so swapping scans, picking on another pair of them, or
 * switching between the views reads nothing again), and disposed when its scan leaves the list or the
 * page is left.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { BufferGeometry } from "three";
import { api, describeError } from "../../api/http";
import { disposeMeshGeometry, plyGeometry, readPly } from "../../three/ply";

export type MeshLoad =
  | { phase: "loading"; loaded: number; total: number | null }
  | { phase: "ready"; geometry: BufferGeometry }
  | { phase: "error"; detail: string; hint: string | null };

type Entry = { url: string; ctrl: AbortController; geometry: BufferGeometry | null };

export function useScanMeshes(wanted: { id: string; url: string }[]): { loads: Record<string, MeshLoad | undefined>; retry: (id: string) => void } {
  const [loads, setLoads] = useState<Record<string, MeshLoad | undefined>>({});
  const [retryKey, setRetryKey] = useState(0);
  const entries = useRef(new Map<string, Entry>());
  const wantedRef = useRef(wanted);
  wantedRef.current = wanted;
  const key = wanted.map((w) => `${w.id} ${w.url}`).join("\n");

  useEffect(() => {
    const map = entries.current;
    const want = wantedRef.current;
    const drop = (id: string, e: Entry) => {
      e.ctrl.abort();
      disposeMeshGeometry(e.geometry);
      map.delete(id);
    };
    const gone: string[] = [];
    for (const [id, e] of map) {
      const w = want.find((x) => x.id === id);
      if (!w || w.url !== e.url) {
        drop(id, e);
        gone.push(id);
      }
    }
    if (gone.length)
      setLoads((l) => {
        const next = { ...l };
        for (const id of gone) delete next[id];
        return next;
      });
    for (const w of want) {
      if (map.has(w.id)) continue;
      const e: Entry = { url: w.url, ctrl: new AbortController(), geometry: null };
      map.set(w.id, e);
      const put = (load: MeshLoad) => {
        if (map.get(w.id) === e) setLoads((l) => ({ ...l, [w.id]: load }));
      };
      put({ phase: "loading", loaded: 0, total: null });
      let last = 0;
      api
        .fetchBinary(
          w.url,
          (loaded, total) => {
            const now = performance.now();
            if (now - last > 120 || loaded === total) {
              last = now;
              put({ phase: "loading", loaded, total });
            }
          },
          e.ctrl.signal,
        )
        .then(
          (buf) =>
            new Promise<void>((resolve) => {
              // let the last "loading" state paint before the synchronous read
              setTimeout(() => {
                if (map.get(w.id) === e && !e.ctrl.signal.aborted) {
                  try {
                    e.geometry = plyGeometry(readPly(buf));
                    put({ phase: "ready", geometry: e.geometry });
                  } catch (err) {
                    put({ phase: "error", detail: `The mesh file couldn't be read: ${err instanceof Error ? err.message : String(err)}`, hint: "Make this scan's mesh again on the project page." });
                  }
                }
                resolve();
              }, 30);
            }),
        )
        .catch((err: unknown) => {
          if ((err as { name?: string }).name === "AbortError") return;
          put({ phase: "error", ...describeError(err) });
        });
    }
  }, [key, retryKey]);

  // leaving the page: stop the downloads and free the meshes
  useEffect(() => {
    const map = entries.current;
    return () => {
      for (const e of map.values()) {
        e.ctrl.abort();
        disposeMeshGeometry(e.geometry);
      }
      map.clear();
    };
  }, []);

  const retry = useCallback((id: string) => {
    const e = entries.current.get(id);
    if (e) {
      e.ctrl.abort();
      disposeMeshGeometry(e.geometry);
      entries.current.delete(id);
    }
    setRetryKey((k) => k + 1);
  }, []);

  return { loads, retry };
}
