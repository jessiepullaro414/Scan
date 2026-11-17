/**
 * The Compare panel's state (ComparePanel.tsx shows it; FEATURE_PLAN I2): the project's references and the scan's
 * comparison (store/references.ts), the alignment job's progress (the jobs store; the comparison is read again when it
 * ends), the reference's display mesh for the ghost and for picking points, the answer for the tolerance (asked again
 * a moment after the tolerance changes, and whenever the pose or the mesh changes), the deviation array when the
 * "Against the reference" shading is on (kept on the geometry, as the other maps), and a click on an area outside the
 * tolerance turned into its triangles and where the camera should go.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { BufferGeometry } from "three";
import { ApiError, api, describeError } from "../../api/http";
import type { Comparison, ComparisonAlignRequest, Job, ReferenceAlignResult, ReferenceModel, ToleranceAnswer, ToleranceRegion } from "../../api/types";
import { meanNormal, viewDirection } from "../../lib/check";
import { decodeFaces, regionTriangles } from "../../lib/fits";
import { fmtDev, regionSphere, rowMajor } from "../../lib/reference";
import { isActive, useJobs } from "../../store/jobs";
import { comparisonKey, useReferences } from "../../store/references";
import { disposeMeshGeometry, plyGeometry, readPly } from "../../three/ply";
import type { ReferenceLit } from "./ReferenceMark";
import { refDeviationOf, setRefDeviation } from "./referenceShading";

export type RefMapState = { phase: "idle" } | { phase: "loading" } | { phase: "ready" } | { phase: "unavailable"; message: string };

export type ComparisonTool = {
  refs: ReferenceModel[] | null;
  refsError: ApiError | null;
  comparison: Comparison | null;
  loaded: boolean;
  busy: boolean;
  /** The alignment job running for this scan, if any. */
  job: Job | null;
  /** The last finished alignment job's result (it says why one was not kept). */
  lastResult: ReferenceAlignResult | null;
  lastError: string | null;
  upload: (file: File) => Promise<ApiError | null>;
  uploading: boolean;
  choose: (rid: string) => void;
  units: (action: "apply" | "keep") => void;
  align: (body: ComparisonAlignRequest) => void;
  cancelAlign: () => void;
  remove: () => void;
  removeRef: (rid: string) => void;
  renameRef: (rid: string, name: string) => Promise<boolean>;
  /** The reference's display mesh (in its own numbers), once read. */
  refGeometry: BufferGeometry | null;
  refMeshError: string | null;
  /** The reference's numbers into the scan's frame (16, row-major), when aligned. */
  matrix: number[] | null;
  ghost: boolean;
  setGhost: (on: boolean) => void;
  tolerance: number;
  setTolerance: (mm: number) => void;
  answer: ToleranceAnswer | null;
  answerLoading: boolean;
  answerError: string | null;
  map: RefMapState;
  lit: ReferenceLit | null;
  select: (index: number) => void;
};

/** The geometry's side the scan was made from (as useCheck's): cached on it. */
function scanSideOf(g: BufferGeometry): [number, number, number] | null {
  const kept = g.userData.scanSide as [number, number, number] | null | undefined;
  if (kept !== undefined) return kept;
  const side = g.index ? meanNormal(g.index.array, g.getAttribute("position").array, g.getAttribute("normal")?.array ?? null) : null;
  g.userData.scanSide = side;
  return side;
}

/**
 * `geometry` / `version`: the mesh on screen and its version (when known). `mapOn`: the "Against the reference" shading
 * is chosen. `wanted`: the panel is in use (the references are read then).
 */
export function useComparison(pid: string, sid: string, geometry: BufferGeometry | null, version: number | null, mapOn: boolean): ComparisonTool {
  const key = comparisonKey(pid, sid);
  const list = useReferences((s) => s.lists[pid]);
  const entry = useReferences((s) => s.comparisons[key]);
  const comparison = entry?.comparison ?? null;
  const jobId = comparison?.aligning ?? null;
  const job = useJobs((s) => (jobId ? (s.jobs[jobId] ?? null) : null));
  const [lastResult, setLastResult] = useState<ReferenceAlignResult | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  // read when the scan opens, and the comparison again with each mesh version (whether the mesh has changed since)
  useEffect(() => {
    void useReferences.getState().loadRefs(pid);
  }, [pid]);
  useEffect(() => {
    void useReferences.getState().loadComparison(pid, sid);
  }, [pid, sid, version]);

  // the alignment job ends: its result, and the comparison read again
  const seen = useRef<string | null>(null);
  useEffect(() => {
    if (!job || isActive(job) || seen.current === job.id) return;
    seen.current = job.id;
    if (job.state === "done") {
      setLastResult(job.result as unknown as ReferenceAlignResult);
      setLastError(null);
    } else if (job.state === "failed") setLastError(job.error ?? "The alignment failed.");
    else setLastError(null);
    void useReferences.getState().loadComparison(pid, sid);
  }, [job, pid, sid]);

  // ---------------------------------------------------------------- the reference's display mesh
  const rid = comparison?.reference_id ?? null;
  const [refMesh, setRefMesh] = useState<{ rid: string; g: BufferGeometry } | null>(null);
  const [refMeshError, setRefMeshError] = useState<string | null>(null);
  const refMeshRef = useRef<BufferGeometry | null>(null);
  useEffect(() => {
    if (!rid) return;
    const ctrl = new AbortController();
    setRefMeshError(null);
    api
      .fetchBinary(api.referenceMeshUrl(pid, rid), undefined, ctrl.signal)
      .then((buf) => {
        if (ctrl.signal.aborted) return;
        const g = plyGeometry(readPly(buf));
        disposeMeshGeometry(refMeshRef.current);
        refMeshRef.current = g;
        setRefMesh({ rid, g });
      })
      .catch((e: unknown) => {
        if ((e as { name?: string }).name !== "AbortError") setRefMeshError(describeError(e).detail);
      });
    return () => ctrl.abort();
  }, [pid, rid]);
  useEffect(() => () => disposeMeshGeometry(refMeshRef.current), []);
  const refGeometry = refMesh && refMesh.rid === rid ? refMesh.g : null;
  const matrix = useMemo(() => rowMajor(comparison?.view_matrix ?? null), [comparison?.view_matrix]);
  const [ghost, setGhost] = useState(true);

  // ---------------------------------------------------------------- the answer for the tolerance
  const [tolerance, setTol] = useState<number | null>(null);
  const tol = tolerance ?? comparison?.tolerance_mm ?? 0.3;
  useEffect(() => setTol(null), [key]);
  const aligned = !!comparison?.alignment && comparison.ready;
  const pose = comparison?.alignment ? JSON.stringify(comparison.alignment.T) : null;
  const [answer, setAnswer] = useState<{ key: string; a: ToleranceAnswer } | null>(null);
  const [answerLoading, setAnswerLoading] = useState(false);
  const [answerError, setAnswerError] = useState<string | null>(null);
  const answerKey = `${key}|${pose}|${version}|${comparison?.scale}`;
  useEffect(() => {
    if (!aligned) {
      setAnswerLoading(false);
      return;
    }
    let cancelled = false;
    setAnswerLoading(true);
    setAnswerError(null);
    // a short wait, so typing a tolerance or stepping it with the arrow keys asks once
    const timer = setTimeout(() => {
      api
        .comparisonTolerance(pid, sid, tol)
        .then(
          (a) => {
            if (!cancelled) setAnswer({ key: answerKey, a });
          },
          (e: unknown) => {
            if (!cancelled) setAnswerError(describeError(e).detail);
          },
        )
        .finally(() => {
          if (!cancelled) setAnswerLoading(false);
        });
    }, 180);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [aligned, answerKey, pid, sid, tol]);
  const shownAnswer = answer && answer.key === answerKey ? answer.a : null;

  // ---------------------------------------------------------------- the deviation array (the shading)
  const [map, setMap] = useState<RefMapState>({ phase: "idle" });
  useEffect(() => {
    if (!mapOn || !geometry || !aligned) {
      setMap({ phase: "idle" });
      return;
    }
    const kept = refDeviationOf(geometry);
    if (kept && kept.info.version === (version ?? kept.info.version) && geometry.userData.refPose === answerKey) {
      setMap({ phase: "ready" });
      return;
    }
    const ctrl = new AbortController();
    setMap({ phase: "loading" });
    api
      .comparisonDeviation(pid, sid, ctrl.signal)
      .then((d) => {
        if (ctrl.signal.aborted) return;
        if (d.values.length !== geometry.getAttribute("position").count || (version !== null && d.info.version !== version)) {
          setMap({ phase: "unavailable", message: "The mesh changed since it was loaded, so the comparison is not for this one" });
          return;
        }
        setRefDeviation(geometry, d);
        geometry.userData.refPose = answerKey;
        setMap({ phase: "ready" });
      })
      .catch((e: unknown) => {
        if ((e as { name?: string }).name === "AbortError" || ctrl.signal.aborted) return;
        setMap({ phase: "unavailable", message: `Couldn't read the comparison. ${describeError(e).detail}` });
      });
    return () => ctrl.abort();
  }, [mapOn, geometry, aligned, answerKey, pid, sid, version]);

  // ---------------------------------------------------------------- an area picked in the list
  const [lit, setLit] = useState<ReferenceLit | null>(null);
  const trisOf = useRef(new WeakMap<ToleranceRegion, Uint32Array>());
  useEffect(() => setLit(null), [shownAnswer]);
  const select = useCallback(
    (index: number) => {
      const r = shownAnswer?.regions[index];
      if (!geometry || !r) return;
      if (lit?.index === index && lit.meshId === geometry.uuid) {
        setLit(null);
        return;
      }
      let tris = trisOf.current.get(r);
      if (!tris) {
        try {
          if (!geometry.index) throw new Error("the mesh on screen has no triangle list");
          tris = regionTriangles(decodeFaces(r.faces), geometry.index.array, (geometry.userData.faceOfTri as Uint32Array | null | undefined) ?? null, geometry.userData.faceCount as number | undefined);
        } catch {
          setAnswerError("The areas are for another version of the mesh: reload it.");
          return;
        }
        trisOf.current.set(r, tris);
      }
      const normal = meanNormal(tris, geometry.getAttribute("position").array, geometry.getAttribute("normal")?.array ?? null);
      setLit({
        index,
        side: r.side,
        meshId: geometry.uuid,
        tris,
        center: r.center_scan,
        label: `${r.side === "above" ? "outside" : "inside"} ${fmtDev(r.mean_mm, false)}`,
        view: { sphere: regionSphere(r), direction: normal ? viewDirection(normal, scanSideOf(geometry)) : undefined },
      });
    },
    [geometry, shownAnswer, lit],
  );

  // ---------------------------------------------------------------- actions
  const st = useReferences.getState;
  const upload = useCallback(
    async (file: File) => {
      setUploading(true);
      const r = await st().upload(pid, file, file.name);
      setUploading(false);
      if (r instanceof ApiError) return r;
      void st().start(pid, sid, r.id, tol);
      return null;
    },
    [pid, sid, st, tol],
  );

  return {
    refs: list?.refs ?? null,
    refsError: list?.error ?? null,
    comparison,
    loaded: !!entry?.loaded,
    busy: !!entry?.busy,
    job: job && isActive(job) ? job : null,
    lastResult: lastResult && comparison && lastResult.alignment ? lastResult : null,
    lastError,
    upload,
    uploading,
    choose: (r) => {
      setLastResult(null);
      void st().start(pid, sid, r, tol);
    },
    units: (action) => void st().units(pid, sid, action),
    align: (body) => {
      setLastResult(null);
      setLastError(null);
      void st().align(pid, sid, body);
    },
    cancelAlign: () => {
      if (jobId) void useJobs.getState().cancel(jobId);
    },
    remove: () => {
      setLastResult(null);
      void st().remove(pid, sid);
    },
    removeRef: (r) => void st().removeRef(pid, r),
    renameRef: (r, name) => st().rename(pid, r, name),
    refGeometry,
    refMeshError,
    matrix,
    ghost,
    setGhost,
    tolerance: tol,
    setTolerance: (mm) => setTol(mm),
    answer: shownAnswer,
    answerLoading,
    answerError,
    map,
    lit: lit && geometry && lit.meshId === geometry.uuid ? lit : null,
    select,
  };
}
