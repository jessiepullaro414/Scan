/**
 * High-rate live data: scanner pose, ghost pose, the latest mesh snapshot and camera previews.
 *
 * The 3D view reads `pose` / `mesh` with `getState()` inside its render loop (no React
 * re-render per frame). Preview JPEGs become object URLs; old ones are revoked once the
 * image element can no longer be showing them.
 */
import { create } from "zustand";
import type { DecodedMesh } from "../api/binary";
import type { PreviewHeader } from "../api/types";

export type PreviewFrame = { url: string; w: number; h: number; seq: number; at: number };
export type PoseSample = { T: number[]; ok: boolean; at: number };
/** The scan previews (the calibration previews live in store/calib.ts). */
type PreviewCamera = "left" | "depth";

type LiveStore = {
  pose: PoseSample | null;
  ghost: { T: number[]; at: number } | null;
  mesh: DecodedMesh | null;
  meshAt: number;
  /** Meshes received since the last reset (0 = nothing to frame yet). */
  meshCount: number;
  previews: Record<PreviewCamera, PreviewFrame | null>;
  setPose: (T: number[], ok: boolean) => void;
  setGhost: (T: number[] | null) => void;
  setMesh: (m: DecodedMesh) => void;
  pushPreview: (h: PreviewHeader, jpeg: Uint8Array) => void;
  /** Forget pose, ghost and model (a new scan starts). */
  resetScan: () => void;
  clearPreviews: () => void;
};

/** 16 finite numbers, row-major (a nested 4x4 list is accepted too). */
function flat16(T: unknown): number[] | null {
  if (!Array.isArray(T)) return null;
  const flat = (Array.isArray(T[0]) ? (T as unknown[][]).flat() : T) as unknown[];
  if (flat.length !== 16 || !flat.every((x) => typeof x === "number" && Number.isFinite(x))) return null;
  return flat as number[];
}

// URLs per camera, newest last; the two newest stay alive (current + the one <img> may still show).
const urlHistory: Record<PreviewCamera, string[]> = { left: [], depth: [] };

function retire(camera: PreviewCamera, url: string) {
  const h = urlHistory[camera];
  h.push(url);
  while (h.length > 3) {
    const old = h.shift();
    if (old) URL.revokeObjectURL(old);
  }
}

export const useLive = create<LiveStore>((set) => ({
  pose: null,
  ghost: null,
  mesh: null,
  meshAt: 0,
  meshCount: 0,
  previews: { left: null, depth: null },

  setPose: (T, ok) => {
    const flat = flat16(T);
    if (!flat) return;
    set({ pose: { T: flat, ok, at: performance.now() } });
  },
  setGhost: (T) => {
    const flat = T ? flat16(T) : null;
    set({ ghost: flat ? { T: flat, at: performance.now() } : null });
  },
  setMesh: (mesh) => set((s) => ({ mesh, meshAt: performance.now(), meshCount: s.meshCount + 1 })),
  pushPreview: (h, jpeg) => {
    if (h.camera === "calib_left" || h.camera === "calib_right") return; // see store/calib.ts
    const camera: PreviewCamera = h.camera === "depth" ? "depth" : "left";
    const url = URL.createObjectURL(new Blob([jpeg as BlobPart], { type: "image/jpeg" }));
    retire(camera, url);
    set((s) => ({
      previews: { ...s.previews, [camera]: { url, w: h.w, h: h.h, seq: h.seq, at: performance.now() } },
    }));
  },
  resetScan: () => set({ pose: null, ghost: null, mesh: null, meshAt: 0, meshCount: 0 }),
  clearPreviews: () => {
    for (const cam of ["left", "depth"] as const) {
      for (const u of urlHistory[cam]) URL.revokeObjectURL(u);
      urlHistory[cam] = [];
    }
    set({ previews: { left: null, depth: null } });
  },
}));
