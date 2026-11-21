/**
 * "Show what was removed" for any "Remove loose bits" entry of the history, at any time (UX_PLAN R23; APP_API.md
 * `GET .../process/edits/{id}/before.ply` and `after.ply`): the mesh before the edit as a ghost, beside the mesh after
 * it, or beside the mesh on screen when the edit is the last applied one. The two meshes are loaded only when asked,
 * freed when the view is closed (or the edit leaves the history), and not loaded at all when they would be too big
 * (lib/looseBits.ts `GHOST_LIMIT_TRIANGLES`).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { BufferGeometry } from "three";
import { ApiError, api } from "../../api/http";
import type { ProcessState } from "../../api/types";
import { ghostTooBigText, historyRemovedLine, removedGhost } from "../../lib/looseBits";
import { disposeMeshGeometry, plyGeometry, readPly } from "../../three/ply";

export type RemovedView =
  | { editId: string; label: string; line: string; phase: "loading" }
  | { editId: string; label: string; line: string; phase: "ready"; ghost: BufferGeometry; after: BufferGeometry | null }
  | { editId: string; label: string; line: string; phase: "error" | "too-big"; message: string };

export function useRemovedView(pid: string, sid: string, state: ProcessState | null) {
  const [view, setView] = useState<RemovedView | null>(null);
  const ctrl = useRef<AbortController | null>(null);
  const held = useRef<BufferGeometry[]>([]);
  /** The version of the mesh on screen when the view was asked for. */
  const shownAt = useRef<number | null>(null);

  const free = useCallback(() => {
    ctrl.current?.abort();
    ctrl.current = null;
    for (const g of held.current) disposeMeshGeometry(g);
    held.current = [];
  }, []);

  const close = useCallback(() => {
    free();
    setView(null);
  }, [free]);

  const show = useCallback(
    (editId: string) => {
      if (!state) return;
      const g = removedGhost(state, editId);
      const entry = state.edits.find((e) => e.id === editId);
      if (!g || !entry) return;
      free();
      shownAt.current = state.current?.version ?? null;
      const base = { editId, label: entry.label, line: historyRemovedLine(entry) ?? "" };
      if (g.tooBig) {
        setView({ ...base, phase: "too-big", message: ghostTooBigText(g.triangles) });
        return;
      }
      setView({ ...base, phase: "loading" });
      const c = new AbortController();
      ctrl.current = c;
      const load = (which: "before" | "after") =>
        api.fetchBinary(api.editStepUrl(pid, sid, editId, which), undefined, c.signal).then((buf) => plyGeometry(readPly(buf)));
      void Promise.all([load("before"), g.afterIsCurrent ? Promise.resolve(null) : load("after")])
        .then(([ghost, after]) => {
          if (c.signal.aborted) {
            disposeMeshGeometry(ghost);
            disposeMeshGeometry(after);
            return;
          }
          held.current = after ? [ghost, after] : [ghost];
          setView({ ...base, phase: "ready", ghost, after });
        })
        .catch((e: unknown) => {
          if (c.signal.aborted || (e as { name?: string }).name === "AbortError") return;
          setView({ ...base, phase: "error", message: e instanceof ApiError ? [e.detail, e.hint].filter(Boolean).join(" ") : "The meshes of this step could not be read." });
        });
    },
    [pid, sid, state, free],
  );

  // the edit left the history (a new Make mesh, a stage or a recipe cleared it): close; and free on leaving the page
  // or the mesh on screen changed (an undo, a redo, an edit): the pair beside it would no longer be the one asked for
  const editIds = state?.edits.map((e) => e.id).join(",") ?? "";
  const version = state?.current?.version ?? null;
  useEffect(() => {
    if (view && (!editIds.split(",").includes(view.editId) || version !== shownAt.current)) close();
  }, [editIds, version]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => free, [free]);

  return { view, show, close };
}
