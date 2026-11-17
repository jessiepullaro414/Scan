/**
 * The Section tool's state (FEATURE_PLAN.md I1): how the cutting plane is chosen (a kept plane moved along its normal,
 * an axis of the part's frame at a value, or three points clicked on the surface), the plane itself (worked out here
 * by the engine's rule, lib/section.ts, so the 3D view moves the plane and the clipping at once), and the cut, asked
 * from the engine as the plane changes: while an offset is dragged at most one request runs and only the newest
 * waiting one goes next (lib/latest.ts), so the cut follows one round trip behind and requests never pile up. A kept
 * section can be opened: it is cut again on the current mesh with its own stored plane until a control is changed.
 * Leaving the tool drops what is running and clears the cut.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../../api/http";
import type { KeptSection, PartFrameView, SectionAnswer, SectionPlane, SectionPlaneDef } from "../../api/types";
import { latestOnly } from "../../lib/latest";
import { planeFromDef, type PlaneFit, type Vec3 } from "../../lib/section";

export type SectionMode = "fit" | "frame" | "points";
export type SectionAxis = "X" | "Y" | "Z";
type Source = { def: SectionPlaneDef } | { kept: string };

/** The mesh's bounding box in the scan's frame (for the sliders' ranges). */
export type Bounds = { min: Vec3; max: Vec3 };

export type SectionTool = {
  mode: SectionMode;
  setMode: (m: SectionMode) => void;
  /** The kept plane cut along (mode "fit"). */
  fitId: string;
  setFitId: (id: string) => void;
  offset: number;
  setOffset: (mm: number) => void;
  axis: SectionAxis;
  setAxis: (a: SectionAxis) => void;
  value: number;
  setValue: (mm: number) => void;
  /** The points clicked so far (mode "points"). */
  points: Vec3[];
  addPoint: (p: Vec3) => void;
  clearPoints: () => void;
  /** The 3D view takes clicks for the three points. */
  picking: boolean;
  /** The kept section opened (cut with its own plane), or null. */
  keptId: string | null;
  openKept: (s: KeptSection) => void;
  /** The plane as chosen, null until it is complete. */
  def: SectionPlaneDef | null;
  /** The cutting plane to draw (worked out here at once; a kept section's from its answer). */
  plane: SectionPlane | null;
  /** Why the chosen plane is not one (the engine's words), or null. */
  planeError: string | null;
  /** The newest cut shown, null while there is none. */
  answer: SectionAnswer | null;
  error: ApiError | null;
  busy: boolean;
  /** Hide the part of the model in front of the plane (on the side its normal points to). */
  hideFront: boolean;
  setHideFront: (on: boolean) => void;
  /** The sliders' ranges (mm): along the kept plane's normal, along the frame's axis. */
  ranges: { offset: [number, number]; value: [number, number] };
};

/** The lowest and highest of the box's corners along a direction from a point, rounded out to whole mm. */
function span(b: Bounds | null, from: ArrayLike<number>, dir: ArrayLike<number>): [number, number] {
  if (!b) return [-50, 50];
  let lo = Infinity,
    hi = -Infinity;
  for (let k = 0; k < 8; k++) {
    const c = [k & 1 ? b.max[0] : b.min[0], k & 2 ? b.max[1] : b.min[1], k & 4 ? b.max[2] : b.min[2]];
    const t = (c[0] - from[0]) * dir[0] + (c[1] - from[1]) * dir[1] + (c[2] - from[2]) * dir[2];
    lo = Math.min(lo, t);
    hi = Math.max(hi, t);
  }
  return [Math.floor(lo), Math.ceil(hi)];
}

export function useSectionTool({
  pid,
  sid,
  on,
  fits,
  frame,
  meshVersion,
  bounds,
}: {
  pid: string;
  sid: string;
  on: boolean;
  /** The kept planes (others are not offered). */
  fits: PlaneFit[];
  /** The part's frame when this version can read it. */
  frame: PartFrameView | null;
  meshVersion: number | null;
  bounds: Bounds | null;
}): SectionTool {
  const [mode, setModeRaw] = useState<SectionMode>("fit");
  const [fitId, setFitIdRaw] = useState("");
  const [offset, setOffsetRaw] = useState(0);
  const [axis, setAxisRaw] = useState<SectionAxis>("Z");
  const [value, setValueRaw] = useState(0);
  const [points, setPoints] = useState<Vec3[]>([]);
  const [keptId, setKeptId] = useState<string | null>(null);
  const [hideFront, setHideFront] = useState(false);
  const [answer, setAnswer] = useState<SectionAnswer | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  // a control changed: the cut is the live one again (a kept section opened is left)
  const live = <T,>(set: (v: T) => void) => (v: T) => {
    setKeptId(null);
    set(v);
  };
  const setMode = useCallback(live(setModeRaw), []); // eslint-disable-line react-hooks/exhaustive-deps
  const setFitId = useCallback(live(setFitIdRaw), []); // eslint-disable-line react-hooks/exhaustive-deps
  const setOffset = useCallback(live(setOffsetRaw), []); // eslint-disable-line react-hooks/exhaustive-deps
  const setAxis = useCallback(live(setAxisRaw), []); // eslint-disable-line react-hooks/exhaustive-deps
  const setValue = useCallback(live(setValueRaw), []); // eslint-disable-line react-hooks/exhaustive-deps
  const addPoint = useCallback((p: Vec3) => {
    setKeptId(null);
    setPoints((ps) => (ps.length >= 3 ? [p] : [...ps, p]));
  }, []);
  const clearPoints = useCallback(() => {
    setKeptId(null);
    setPoints([]);
  }, []);

  // turning the tool on: a sensible first plane (the first kept plane, else the part's Z, else three points)
  const wasOn = useRef(false);
  useEffect(() => {
    if (on && !wasOn.current && !keptId) {
      const have = fits.some((f) => f.id === fitId);
      if (mode === "fit" && !have) {
        if (fits.length) setFitIdRaw(fits[0].id);
        else setModeRaw(frame ? "frame" : "points");
      }
      if (mode === "frame" && !frame) setModeRaw(fits.length ? "fit" : "points");
    }
    wasOn.current = on;
  }, [on]); // eslint-disable-line react-hooks/exhaustive-deps

  // the kept-plane mode always has a plane while there is one (the one chosen gone, or none chosen yet: the first)
  useEffect(() => {
    if (mode === "fit" && fits.length && !fits.some((f) => f.id === fitId)) setFitIdRaw(fits[0].id);
  }, [mode, fits, fitId]);

  const def = useMemo<SectionPlaneDef | null>(() => {
    if (mode === "fit") return fits.some((f) => f.id === fitId) ? { kind: "fit", fit: fitId, offset_mm: offset } : null;
    if (mode === "frame") return frame ? { kind: "frame", axis, value_mm: value } : null;
    return points.length === 3 ? { kind: "points", points: points.map((p) => [...p]) } : null;
  }, [mode, fits, fitId, offset, frame, axis, value, points]);

  const local = useMemo<{ plane: SectionPlane | null; error: string | null }>(() => {
    if (!def) return { plane: null, error: null };
    try {
      return { plane: planeFromDef(def, fits, frame), error: null };
    } catch (e) {
      return { plane: null, error: (e as Error).message };
    }
  }, [def, fits, frame]);

  // the cut: one request at a time, the newest waiting one next, an answer never shown after a newer one
  const cut = useMemo(
    () =>
      latestOnly<Source, SectionAnswer>(
        (src) => {
          setBusy(true);
          return "kept" in src ? api.keptSection(pid, sid, src.kept) : api.section(pid, sid, { ...src.def, compact: true });
        },
        (a) => {
          setAnswer(a);
          setError(null);
        },
        (e) => setError(e instanceof ApiError ? e : new ApiError(0, String(e), null, "section")),
        () => setBusy(false),
      ),
    [pid, sid],
  );

  const source: Source | null = keptId ? { kept: keptId } : def && !local.error ? { def } : null;
  const sourceKey = source ? JSON.stringify(source) : "";
  useEffect(() => {
    if (!on || !source) {
      cut.cancel();
      setAnswer(null);
      setError(null);
      return;
    }
    cut.ask(source);
  }, [on, sourceKey, meshVersion, cut]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => cut.cancel(), [cut]);

  const openKept = useCallback(
    (s: KeptSection) => {
      // the controls show how it was named (the cut stays the kept plane's until one is changed)
      const d = s.definition;
      if (d.kind === "fit" && fits.some((f) => f.id === d.fit)) {
        setModeRaw("fit");
        setFitIdRaw(d.fit);
        setOffsetRaw(d.offset_mm ?? 0);
      } else if (d.kind === "frame" && frame) {
        setModeRaw("frame");
        setAxisRaw(d.axis.replace("-", "") as SectionAxis);
        setValueRaw(d.value_mm);
      } else if (d.kind === "points") {
        setModeRaw("points");
        setPoints(d.points.map((p) => [p[0], p[1], p[2]] as Vec3));
      }
      setKeptId(s.id);
    },
    [fits, frame],
  );

  const fit = fits.find((f) => f.id === fitId);
  const fitShape = fit?.shape as { point?: number[]; normal?: number[] } | undefined;
  const ranges = useMemo(() => {
    const offsetRange: [number, number] = fitShape?.point && fitShape.normal ? span(bounds, fitShape.point, unit(fitShape.normal)) : [-50, 50];
    let valueRange: [number, number] = [-50, 50];
    if (frame) {
      const i = ["X", "Y", "Z"].indexOf(axis);
      const M = frame.matrix;
      const O = [0, 1, 2].map((j) => -(M[0][j] * M[0][3] + M[1][j] * M[1][3] + M[2][j] * M[2][3]));
      valueRange = span(bounds, O, M[i].slice(0, 3));
    }
    return { offset: offsetRange, value: valueRange };
  }, [bounds, fitShape, frame, axis]);

  const plane = keptId ? (answer?.kept?.id === keptId ? answer.plane : null) : local.plane;
  return {
    mode,
    setMode,
    fitId,
    setFitId,
    offset,
    setOffset,
    axis,
    setAxis,
    value,
    setValue,
    points,
    addPoint,
    clearPoints,
    picking: on && !keptId && mode === "points" && points.length < 3,
    keptId,
    openKept,
    def,
    plane,
    planeError: local.error,
    answer: on ? answer : null,
    error: on ? error : null,
    busy: on && busy,
    hideFront,
    setHideFront,
    ranges,
  };
}

function unit(v: number[]): number[] {
  const l = Math.hypot(v[0], v[1], v[2]) || 1;
  return v.map((x) => x / l);
}
