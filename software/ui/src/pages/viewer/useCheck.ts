/**
 * The Check panel's state (CheckPanel.tsx shows it, CheckMark.tsx lights the picked area): asks the
 * engine which areas of the mesh on screen miss the tolerance, again whenever the tolerance or the
 * mesh changes once the user has asked, and turns a click on an area into its triangles (decoded
 * once, on that first click) and where the camera should go to look at it (CheckMark.tsx sends it there).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { BufferGeometry } from "three";
import { api } from "../../api/http";
import type { CheckArea, CheckKind, CheckPose, CheckResult, CheckWhy } from "../../api/types";
import { meanNormal, viewDirection } from "../../lib/check";
import { poseFrame, whyFits } from "../../lib/checkWhy";
import { decodeFaces, regionTriangles } from "../../lib/fits";
import { useCheckPrefs } from "../../store/check";
import type { FrameSphere } from "../../three/viewBus";

/** The colour of a lit area, on the mesh and in the list (apart from the fits' blue and amber). */
export const CHECK_COLOR = "#ff6b3d";

/** The area lit on the mesh. */
export type CheckLit = {
  /** Its place in the list, from 0. */
  index: number;
  kind: CheckKind;
  /** The mesh it belongs to (its geometry's uuid). */
  meshId: string;
  /** Its triangles: corner triples into that mesh's vertices. */
  tris: Uint32Array;
  /** Where its label goes (mm, the mesh's frame). */
  center: [number, number, number];
  areaMm2: number;
  /** Where the camera goes to look at it, in the 3D view's axes; no direction: the view keeps its own. */
  view: { sphere: FrameSphere; direction?: [number, number, number] };
  /** Where to hold the scanner to scan it again (drawn in the 3D view), when there is a pose. */
  pose: CheckPose | null;
};

/** The reasons and poses of the answer on screen (FEATURE_PLAN N1), asked for after it. */
export type CheckWhyState = {
  why: CheckWhy | null;
  loading: boolean;
  /** They could not be had: the panel shows the areas without them. */
  failed: boolean;
};

/**
 * The side an open scan was made from: the direction the whole mesh faces (null for one scanned all
 * round). One pass over the mesh's triangles, on the first click of an area, then kept with the geometry.
 */
function scanSideOf(g: BufferGeometry): [number, number, number] | null {
  const kept = g.userData.scanSide as [number, number, number] | null | undefined;
  if (kept !== undefined) return kept;
  const side = g.index ? meanNormal(g.index.array, g.getAttribute("position").array, g.getAttribute("normal")?.array ?? null) : null;
  g.userData.scanSide = side;
  return side;
}

/** `changed`: the engine answered for another version of the mesh than the one on screen. */
export type CheckError = { kind: "failed"; error: unknown } | { kind: "changed" };

export type CheckState = {
  tolerance: number;
  setTolerance: (mm: number) => void;
  /** The user has asked on this page: a new tolerance or a new mesh asks again by itself. */
  on: boolean;
  /** Ask (again). */
  run: () => void;
  loading: boolean;
  /** The last answer for the mesh on screen; while loading it may be for the tolerance before. */
  result: CheckResult | null;
  error: CheckError | null;
  lit: CheckLit | null;
  /**
   * Light an area of the list (the 3D view then flies the camera to it); the lit one again turns it off.
   * `pose`: draw this pose instead of the area's own (a place of the plan); `showPose`: fly to show the
   * scanner beside the area rather than the area alone (and keep it lit).
   */
  select: (index: number, opts?: { pose?: CheckPose | null; showPose?: boolean }) => void;
  /** Why each area is weak and where to scan it from (FEATURE_PLAN N1). */
  why: CheckWhyState;
  /** Read the mesh again (after `changed`). */
  reload: () => void;
};

/**
 * `version`: the version of the mesh on screen, when known (an answer for another one is refused).
 * `reload`: read the mesh again, keeping the camera.
 */
export function useCheck(pid: string, sid: string, geometry: BufferGeometry | null, version: number | null, reload: () => void): CheckState {
  const tolerance = useCheckPrefs((s) => s.tolerance);
  const setTolerance = useCheckPrefs((s) => s.setTolerance);
  const [on, setOn] = useState(false);
  const [again, setAgain] = useState(0);
  const [answer, setAnswer] = useState<{ meshId: string; result: CheckResult } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<CheckError | null>(null);
  const [lit, setLit] = useState<CheckLit | null>(null);
  /** Each area's triangles, once it has been clicked (the areas go with their answer). */
  const trisOf = useRef(new WeakMap<CheckArea, Uint32Array>());

  // another scan in the same page: start over
  useEffect(() => {
    setOn(false);
    setAnswer(null);
    setError(null);
    setLit(null);
  }, [pid, sid]);

  useEffect(() => {
    if (!on || !geometry) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    // a short wait, so stepping the tolerance with the arrow keys asks once
    const timer = setTimeout(() => {
      api
        .check(pid, sid, tolerance)
        .then(
          (r) => {
            if (cancelled) return;
            setLit(null);
            if (version !== null && r.version !== version) {
              setAnswer(null);
              setError({ kind: "changed" });
            } else setAnswer({ meshId: geometry.uuid, result: r });
          },
          (e: unknown) => {
            if (!cancelled) setError({ kind: "failed", error: e });
          },
        )
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [on, again, pid, sid, geometry, version, tolerance]);

  const meshId = geometry?.uuid ?? null;
  const result = answer && answer.meshId === meshId ? answer.result : null;
  const shownLit = lit && lit.meshId === meshId ? lit : null;

  // the reasons and poses of the answer on screen: asked once it is there, so the areas show first
  const [whyAnswer, setWhyAnswer] = useState<{ result: CheckResult; why: CheckWhy | null; failed: boolean } | null>(null);
  useEffect(() => {
    if (!result || !hasAreas(result)) return;
    let cancelled = false;
    api.checkWhy(pid, sid, result.tolerance_mm, result.version).then(
      (w) => {
        if (!cancelled) setWhyAnswer({ result, why: whyFits(w, result) ? w : null, failed: !whyFits(w, result) });
      },
      () => {
        if (!cancelled) setWhyAnswer({ result, why: null, failed: true });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [pid, sid, result]);
  const whyNow = whyAnswer && whyAnswer.result === result ? whyAnswer : null;
  const why = useMemo<CheckWhyState>(
    () => ({ why: whyNow?.why ?? null, loading: !!result && hasAreas(result) && !whyNow, failed: !!whyNow?.failed }),
    [whyNow, result],
  );

  const run = useCallback(() => {
    setOn(true);
    setAgain((n) => n + 1);
  }, []);

  const select = useCallback(
    (index: number, opts: { pose?: CheckPose | null; showPose?: boolean } = {}) => {
      const area = result?.areas[index];
      if (!geometry || !area) return;
      const ownPose = why.why?.areas[index]?.view.pose ?? null;
      const pose = opts.pose !== undefined ? opts.pose : ownPose;
      if (shownLit?.index === index && !opts.showPose && opts.pose === undefined) {
        setLit(null);
        return;
      }
      let tris = trisOf.current.get(area);
      if (!tris) {
        try {
          if (!geometry.index) throw new Error("the mesh on screen has no triangle list");
          tris = regionTriangles(
            decodeFaces(area.faces),
            geometry.index.array,
            (geometry.userData.faceOfTri as Uint32Array | null | undefined) ?? null,
            geometry.userData.faceCount as number | undefined,
          );
        } catch {
          // faces that are not in the mesh on screen: the answer is for another version of it
          setLit(null);
          setAnswer(null);
          setError({ kind: "changed" });
          return;
        }
        trisOf.current.set(area, tris);
      }
      const normal = meanNormal(tris, geometry.getAttribute("position").array, geometry.getAttribute("normal")?.array ?? null);
      const c = area.center;
      setLit({
        index,
        kind: area.kind,
        meshId: geometry.uuid,
        tris,
        center: c,
        areaMm2: area.area_mm2,
        view:
          opts.showPose && pose
            ? poseFrame(pose, normal, area.radius_mm) // the scanner beside its area
            : {
                sphere: { center: [c[0], -c[1], -c[2]], radius: Math.max(area.radius_mm, 6) }, // the view's axes: y up
                // an area that faces every way has no direction: the view keeps its own
                direction: normal ? viewDirection(normal, scanSideOf(geometry)) : undefined,
              },
        pose,
      });
    },
    [geometry, result, shownLit, why],
  );

  return useMemo(
    () => ({ tolerance, setTolerance, on, run, loading, result, error, lit: shownLit, select, reload, why }),
    [tolerance, setTolerance, on, run, loading, result, error, shownLit, select, reload, why],
  );
}

/** An answer worth asking the reasons of: it lists areas. */
function hasAreas(r: CheckResult): boolean {
  return r.has_sigma && r.areas.length > 0;
}
