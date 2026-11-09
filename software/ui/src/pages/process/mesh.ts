/**
 * The Process page's meshes: the current version of the scan's mesh, downloaded again whenever
 * `current.version` changes, and the version before it (for before / after). Each carries its
 * face selection and, once something is selected, the highlight geometry, which shares the
 * mesh's positions on the GPU.
 */
import { useEffect, useRef, useState } from "react";
import { BufferAttribute, BufferGeometry, DynamicDrawUsage } from "three";
import { ApiError, api } from "../../api/http";
import { plyGeometry, readPly, type PlyMesh } from "../../three/ply";
import { MeshSelection } from "./selection";

export type LoadedMesh = {
  version: number;
  geometry: BufferGeometry;
  selection: MeshSelection;
  bytes: number;
  /** Selected faces, drawn over the mesh (made on first use). */
  highlight: BufferGeometry | null;
};

export type MeshLoad =
  | { phase: "idle" }
  | { phase: "download"; loaded: number; total: number | null; version: number }
  | { phase: "parse"; version: number }
  | { phase: "error"; error: ApiError | Error; version: number };

export function makeLoadedMesh(ply: PlyMesh, version: number, bytes: number): LoadedMesh {
  return { version, geometry: plyGeometry(ply), selection: new MeshSelection(version, ply.positions, ply.indices, ply.faceOfTri), bytes, highlight: null };
}

/** The highlight geometry of a mesh: its positions, and an index of the selected faces. */
export function highlightOf(m: LoadedMesh): BufferGeometry {
  if (m.highlight) return m.highlight;
  const g = new BufferGeometry();
  g.setAttribute("position", m.geometry.getAttribute("position"));
  g.setIndex(new BufferAttribute(new Uint32Array(Math.max(3, m.selection.faces * 3)), 1).setUsage(DynamicDrawUsage));
  g.setDrawRange(0, 0);
  g.boundingBox = m.geometry.boundingBox;
  g.boundingSphere = m.geometry.boundingSphere;
  m.highlight = g;
  return g;
}

export function disposeLoadedMesh(m: LoadedMesh | null | undefined): void {
  if (!m) return;
  m.highlight?.dispose();
  m.geometry.dispose();
}

/** The address of one version: the same file, a new query, so no cache serves an old copy. */
const versionUrl = (url: string, version: number) => `${url}${url.includes("?") ? "&" : "?"}v=${version}`;

/**
 * Load `url` at `version`, and again whenever the version changes. The previous version is
 * kept (for before / after); a failed load keeps the mesh on screen and reports the error.
 */
export function useVersionedMesh(url: string | null, version: number | null) {
  const [current, setCurrent] = useState<LoadedMesh | null>(null);
  const [previous, setPrevious] = useState<LoadedMesh | null>(null);
  const [load, setLoad] = useState<MeshLoad>({ phase: "idle" });
  const [retry, setRetry] = useState(0);
  const currentRef = useRef<LoadedMesh | null>(null);
  currentRef.current = current;

  useEffect(() => {
    if (!url || version === null) {
      setLoad({ phase: "idle" });
      return;
    }
    if (currentRef.current?.version === version) return;
    const ctrl = new AbortController();
    let last = 0;
    setLoad({ phase: "download", loaded: 0, total: null, version });
    api
      .fetchBinary(
        versionUrl(url, version),
        (loaded, total) => {
          const now = performance.now();
          if (now - last > 80 || loaded === total) {
            last = now;
            setLoad({ phase: "download", loaded, total, version });
          }
        },
        ctrl.signal,
      )
      .then(
        (buf) =>
          new Promise<void>((resolve) => {
            if (ctrl.signal.aborted) return resolve();
            setLoad({ phase: "parse", version });
            // Let "Preparing" paint before the synchronous parse.
            setTimeout(() => {
              if (ctrl.signal.aborted) return resolve();
              try {
                const t0 = performance.now();
                const mesh = makeLoadedMesh(readPly(buf), version, buf.byteLength);
                if (import.meta.env.DEV)
                  console.debug(`ohs process: mesh v${version} (${mesh.selection.faces.toLocaleString()} triangles) read in ${(performance.now() - t0).toFixed(0)} ms`);
                const old = currentRef.current;
                currentRef.current = mesh;
                setCurrent(mesh);
                if (old) setPrevious(old);
                setLoad({ phase: "idle" });
              } catch (e) {
                setLoad({ phase: "error", error: e instanceof Error ? e : new Error(String(e)), version });
              }
              resolve();
            }, 30);
          }),
      )
      .catch((e: unknown) => {
        if ((e as { name?: string }).name === "AbortError" || ctrl.signal.aborted) return;
        setLoad({ phase: "error", error: e instanceof Error ? e : new Error(String(e)), version });
      });
    return () => ctrl.abort();
  }, [url, version, retry]);

  // A mesh leaves memory when it stops being the previous one, and both on unmount.
  useEffect(() => () => disposeLoadedMesh(previous), [previous]);
  useEffect(() => () => disposeLoadedMesh(currentRef.current), []);

  return { current, previous, load, retry: () => setRetry((k) => k + 1), dropPrevious: () => setPrevious(null) };
}
