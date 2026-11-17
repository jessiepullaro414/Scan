/**
 * The demo engine's kept features (APP_API.md "Kept features"), like the engine's
 * `ohs/server/features.py`: fits, two-point measurements and relations kept per scan (in memory
 * here), renamed and deleted; each fit and measurement made on another mesh version than the
 * current one is marked `stale`, and "fit again" refits the stale fits from their saved clicks
 * on the current mesh (the seed's face found again as the face nearest the saved point). A fit
 * that can't be found again stays as it was, still marked. Nothing is refitted on its own.
 *
 * The fits themselves are the demo's fit route (`MockProcess.fit`), passed in as a `FeatureEngine`,
 * so this file has no mesh code and can be checked on its own (features.test.ts). Where the engine
 * can say where a fit's region lies (`extent`, extent.ts) a kept fit carries it, its outline is
 * served by `.../features/{id}/outline`, and "fit again" also measures the fits kept without one. A kept cylinder
 * says whether it is a boss or a bore (`form`, from the engine's `form`: its mock cylinders read it from their faces).
 *
 * Relations (`relation` on each kept relation, `GET .../features/relate?a=&b=`) are lib/fits.ts's
 * `relate` in the engine's form (`relationRecord`): the demo has no engine, and those values equal
 * the engine's (lib/relate.test.ts); their ± are the viewer's older ones.
 *
 * The part's frame (`.../features/frame`, frame.test.ts): built from three kept fits with the simplified rule of
 * frame.ts, kept per scan, shown with whether it is still current (a fit it was built from deleted or changed),
 * removed; the current mesh in it comes from the engine (`partMesh`).
 *
 * The kept fits for CAD (`.../features/cad`, cad.ts, cad.test.ts): the summary as the engine gives it, the STEP
 * file and the FreeCAD macro as small fixed texts that say they come from the demo.
 */
import { relationRecord } from "../../lib/fits.ts";
import type {
  ApprovedNominal,
  CylinderForm,
  FeatureSet,
  FitExtent,
  FitResult,
  KeptFeature,
  KeptMeasurement,
  KeptRelation,
  NominalAnswer,
  NominalValueName,
  PairRelation,
  PartFrameRecord,
} from "../types";
import { cadBoundary, cadMeasuredOnly, mockCadFile, mockCadSummary } from "./cad.ts";
import { outlineAnswer, type MockOutline } from "./extent.ts";
import { frameState, mockPartFrame, type FrameInput } from "./frame.ts";
import { HttpError, json } from "./host.ts";
import { badNominal, fitValue, mockOffer, nominalView, relationValues, type StoredNominal } from "./nominal.ts";

/** What a scan's kept features need from the demo engine. */
export type FeatureEngine = {
  /** The fit route's answer for this body (throws HttpError as the route does). */
  fit: (body: Record<string, unknown>) => FitResult;
  /** The current mesh's version, null before there is a mesh. */
  version: () => number | null;
  /** The part's (project's) and the scan's names, for the CAD files' names (cad.ts); "scan" without it. */
  names?: () => { part: string; scan: string };
  /** The current mesh's face nearest a point (to find a saved click again after an edit). */
  nearestFace: (point: number[]) => number;
  /** The current mesh with its vertices and normals turned by `matrix` (4x4 rows), as `mesh.ply` or `mesh.stl`;
   * null before there is a mesh. */
  partMesh?: (matrix: number[][], name: "mesh.ply" | "mesh.stl") => ArrayBuffer | null;
  /** Where a fit's region lies (extent.ts `mockExtent`) and its outline; without it kept fits have no extent. */
  extent?: (r: FitResult) => { extent: FitExtent; outline: MockOutline } | null;
  /** Whether a cylinder just fitted is a boss or a bore (extent.ts `mockForm`); without it kept fits have form null. */
  form?: (r: FitResult) => CylinderForm | null;
  /** The current mesh with each vertex's sigma, for the sections (section.ts); null before there is a mesh. */
  mesh?: () => { positions: ArrayLike<number>; indices: ArrayLike<number>; sigma: ArrayLike<number> | null } | null;
};

type Stored = {
  features: Omit<KeptFeature, "stale">[];
  measurements: Omit<KeptMeasurement, "stale">[];
  relations: KeptRelation[];
  /** Each fit's region, base64 as the fit route gives it. */
  faces: Map<string, string>;
  /** Each plane fit's outline (the outline route's answer, for the mesh version the fit was made on). */
  outlines: Map<string, NonNullable<MockOutline>>;
  /** The part's frame, as `features.json` keeps it. */
  frame: PartFrameRecord | null;
};

const NAME_MAX = 80;
const nowIso = () => new Date().toISOString().slice(0, 19);
const newId = (p: string) => `${p}-${Math.random().toString(16).slice(2, 12).padEnd(10, "0")}`;
const bad = (detail: string, hint = "Check the request body against APP_API.md."): never => {
  throw new HttpError(422, detail, hint);
};
const isVec3 = (v: unknown): v is [number, number, number] => Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === "number" && Number.isFinite(x));

/** "Fit 4" after "Fit 1" and "Fit 3": one more than the highest such name and than the number of items. */
export function nextName(items: { name: string }[], stem: string): string {
  let top = items.length;
  for (const it of items) {
    const m = new RegExp(`^${stem} (\\d+)$`).exec(it.name.trim());
    if (m) top = Math.max(top, Number(m[1]));
  }
  return `${stem} ${top + 1}`;
}

function cleanName(v: unknown, fallback?: string): string {
  if (v === undefined || v === null) {
    if (fallback !== undefined) return fallback;
    return bad("name: required", `Give a name of 1 to ${NAME_MAX} characters.`);
  }
  if (typeof v !== "string") return bad("name: must be text");
  const n = v.trim();
  if (!n) return bad("name must not be empty", `Give a name of 1 to ${NAME_MAX} characters.`);
  if (n.length > NAME_MAX) return bad(`name: at most ${NAME_MAX} characters`, `Give a name of 1 to ${NAME_MAX} characters.`);
  return n;
}

export class MockFeatures {
  private sets = new Map<string, Stored>();

  private kept(key: string): Stored {
    let s = this.sets.get(key);
    if (!s) this.sets.set(key, (s = { features: [], measurements: [], relations: [], faces: new Map(), outlines: new Map(), frame: null }));
    return s;
  }

  /** A scan's kept fits and part's frame as kept (for the sections, section.ts). */
  keptSet(key: string): { features: Omit<KeptFeature, "stale">[]; frame: PartFrameRecord | null } {
    const s = this.kept(key);
    return { features: s.features, frame: s.frame };
  }

  private view(s: Stored, version: number | null): FeatureSet {
    const stale = (v: number | null) => version === null || v !== version;
    return {
      format_version: 1,
      frame: this.frameView(s),
      mesh_version: version,
      features: s.features.map((f) => ({ ...f, stale: stale(f.mesh_version), nominals: this.nominalViews(s, f.id) })),
      measurements: s.measurements.map((m) => ({ ...m, stale: stale(m.mesh_version) })),
      relations: s.relations.map((r) => ({ ...this.relationView(s, r, version), nominals: this.nominalViews(s, r.id) })),
    };
  }

  // ------------------------------------------------------------------ nominal values (nominal.ts: plain ±2σ rounding)
  /** A kept fit or relation by id, for its nominal values. */
  private nominalItem(s: Stored, id: string): { list: "features" | "relations"; item: { id: string; name: string; nominals?: StoredNominal[] } } {
    const f = s.features.find((x) => x.id === id);
    if (f) return { list: "features", item: f as { id: string; name: string; nominals?: StoredNominal[] } };
    const r = s.relations.find((x) => x.id === id);
    if (r) return { list: "relations", item: r as { id: string; name: string; nominals?: StoredNominal[] } };
    throw new HttpError(404, `No kept fit or relation “${id}”`, "Read the list again.");
  }

  /** The values of a kept fit (its diameter or radius) or relation, measured now. */
  private nominalValues(s: Stored, id: string, size: "diameter" | "radius" = "diameter") {
    const f = s.features.find((x) => x.id === id);
    if (f) {
      const v = fitValue(f.shape, size);
      return v ? [v] : [];
    }
    const r = s.relations.find((x) => x.id === id);
    const rec = r ? this.relationView(s, r, null).relation : null;
    return rec ? relationValues(rec) : [];
  }

  private nominalViews(s: Stored, id: string): ApprovedNominal[] {
    const { item } = this.nominalItem(s, id);
    return (item.nominals ?? []).map((n) => {
      const now = this.nominalValues(s, id, n.value === "radius" ? "radius" : "diameter").find((v) => v.value === n.value) ?? null;
      return nominalView(n, now);
    });
  }

  private nominalAnswer(s: Stored, id: string, size?: "diameter" | "radius", withBias = false): NominalAnswer {
    const { list, item } = this.nominalItem(s, id);
    const f = list === "features" ? s.features.find((x) => x.id === id)! : null;
    return {
      id,
      name: item.name,
      kind: list === "features" ? "fit" : "relation",
      form: f?.form ?? null,
      poses: "tracked",
      poses_reason: "the demo engine reads no processing report: the poses are taken as tracked",
      with_bias: withBias, // the demo has no bias table: the offers are the same either way
      move: list === "relations" ? ((item as KeptRelation).move ?? null) : null,
      offers: this.nominalValues(s, id, size).map(mockOffer),
      nominals: this.nominalViews(s, id),
    };
  }

  /** A relation's move (APP_API.md "Nominal values", "A relation's move"): "listed" takes it away; "a" or "b" keeps
   * it, placed last unless the same shape was chosen already. */
  private chooseMove(s: Stored, id: string, move: unknown): KeptRelation {
    const { list, item } = this.nominalItem(s, id);
    if (list !== "relations") bad("a move is chosen for a relation: a fit alone does not move", "Choose the move on a kept relation.");
    if (move !== "listed" && move !== "a" && move !== "b") bad(`move must be 'listed', 'a' or 'b', got ${String(move)}`);
    const r = item as KeptRelation;
    if (move === "listed") delete r.move;
    else if (r.move?.shape !== move) {
      const last = Math.max(0, ...s.relations.map((x) => x.move?.order ?? 0));
      r.move = { shape: move as "a" | "b", order: last + 1, chosen: nowIso() };
    }
    return r;
  }

  private approveNominal(s: Stored, id: string, body: Record<string, unknown>): NominalAnswer {
    const { list, item } = this.nominalItem(s, id);
    if (body.move !== undefined && body.move !== null && list !== "relations") bad("a move is chosen for a relation: a fit alone does not move", "Choose the move on a kept relation.");
    const value = body.value as NominalValueName;
    const allowed: NominalValueName[] = list === "features" ? ["diameter", "radius"] : ["angle", "distance", "centre_distance"];
    if (list === "features" && s.features.find((x) => x.id === id)!.kind === "plane") bad("a plane alone has no size to approve", "Approve a relation between two planes instead.");
    if (!allowed.includes(value)) bad(`a ${list === "features" ? "fit" : "relation"}'s nominal value is one of ${allowed.join(", ")}, not ${String(value)}`, "Pick one of those.");
    const source = body.source ?? "entered";
    if (source !== "suggested" && source !== "entered") bad(`source must be 'suggested' or 'entered', got ${String(source)}`);
    const why = badNominal(value, body.nominal);
    if (why) bad(why, "Enter a number that makes sense for this value.");
    const nominal = body.nominal as number;
    const offer = this.nominalValues(s, id, value === "radius" ? "radius" : "diameter")
      .map(mockOffer)
      .find((o) => o.value === value);
    if (!offer) return bad(`this relation has no ${value.replace("_", " ")} to approve`, "Pick one of the values it shows.");
    const hit = offer.suggestions.find((x) => Math.abs(x.nominal - nominal) <= 1e-9);
    if (source === "suggested" && !hit) bad(`${nominal} is not offered now for this ${value.replace("_", " ")}`, "Approve one of the values offered, or send it as 'entered'.");
    const word = hit?.word ?? (offer.unit === "deg" ? ({ 0: "parallel", 90: "perpendicular" } as Record<number, string>)[nominal] ?? null : null);
    const withBias = body.with_bias === true;
    const entry: StoredNominal = { value, nominal, unit: offer.unit, word, source: source as "suggested" | "entered", measured: offer.measured, sigma: offer.sigma, ...(withBias ? { with_bias: true } : {}), approved: nowIso() };
    const same: string[] = list === "features" ? ["diameter", "radius"] : [value];
    item.nominals = [...(item.nominals ?? []).filter((n) => !same.includes(n.value)), entry];
    if (body.move !== undefined && body.move !== null) this.chooseMove(s, id, body.move);
    return this.nominalAnswer(s, id, list === "features" ? (value as "diameter" | "radius") : undefined, withBias);
  }

  private removeNominal(s: Stored, id: string, value: string): void {
    const { item } = this.nominalItem(s, id);
    const rest = (item.nominals ?? []).filter((n) => n.value !== value);
    if (rest.length === (item.nominals ?? []).length) throw new HttpError(404, `No approved ${value.replace("_", " ")} for “${item.name}”`, "Read the list again.");
    if (rest.length) item.nominals = rest;
    else delete item.nominals;
  }

  /** A kept relation as the engine shows it: its two fits' relation and whether one was fitted on an earlier mesh. */
  private relationView(s: Stored, r: Pick<KeptRelation, "a" | "b">, version: number | null) {
    const fa = s.features.find((f) => f.id === r.a),
      fb = s.features.find((f) => f.id === r.b);
    const stale = [fa, fb].some((f) => !f || version === null || f.mesh_version !== version);
    const relation = fa && fb ? relationRecord(fa, fb) : null;
    return { ...r, stale, relation } as KeptRelation & { stale: boolean };
  }

  /** The extent and outline of a fit just made (none without the engine's `extent`). */
  private measure(s: Stored, id: string, r: FitResult, engine: FeatureEngine): FitExtent | null {
    const m = engine.extent?.(r) ?? null;
    if (m?.outline) s.outlines.set(id, m.outline);
    else s.outlines.delete(id);
    return m?.extent ?? null;
  }

  private frameView(s: Stored): FeatureSet["frame"] {
    return s.frame ? { ...s.frame, ...frameState(s.frame, s.features) } : null;
  }

  private find(s: Stored, id: string): { list: "features" | "measurements" | "relations"; i: number } {
    for (const list of ["features", "measurements", "relations"] as const) {
      const i = (s[list] as { id: string }[]).findIndex((x) => x.id === id);
      if (i >= 0) return { list, i };
    }
    throw new HttpError(404, `No kept feature, measurement or relation “${id}”`, "Read the list again.");
  }

  /** `/api/projects/{pid}/scans/{sid}/features[/...]`; `key` is "pid/sid", `rest` what follows "features". */
  route(method: string, key: string, rest: string[], body: Record<string, unknown>, engine: FeatureEngine): Response | undefined {
    const [a, b] = rest;
    const is = (m: string, n: number) => method === m && rest.length === n;
    const s = this.kept(key);
    if (is("GET", 0)) return json(200, this.view(s, engine.version()));
    if (is("POST", 0)) return json(200, this.keepFit(s, body, engine));
    if (a === "measurements" && is("POST", 1)) return json(200, this.keepMeasurement(s, body, engine));
    if (a === "relations" && is("POST", 1)) return json(200, this.relationView(s, this.keepRelation(s, body), engine.version()));
    if (a === "relate" && is("GET", 1)) return json(200, this.relate(s, body, engine));
    if (a === "refit" && is("POST", 1)) return json(200, this.refit(s, body, engine));
    if (a === "frame" && is("POST", 1)) return json(200, this.buildFrame(s, body, engine));
    if (a === "frame" && is("DELETE", 1)) {
      s.frame = null;
      return json(204, null);
    }
    if (a === "frame" && b && is("GET", 2)) return this.frameMesh(s, b, engine);
    // the kept fits for CAD (cad.ts: the summary as the engine's, the files small fixed texts)
    if (a === "cad" && is("GET", 1))
      return json(200, mockCadSummary(s, cadBoundary(body.boundary), engine.version(), engine.names?.().scan ?? "scan", cadMeasuredOnly(body.measured_only)));
    if (a === "cad" && b && is("GET", 2)) return mockCadFile(s, b, cadBoundary(body.boundary), engine.version(), engine.names?.().scan ?? "scan");
    if (a && is("PATCH", 1)) {
      const name = cleanName(body.name);
      const { list, i } = this.find(s, a);
      const items = s[list] as { name: string; mesh_version?: number | null }[];
      items[i] = { ...items[i], name };
      if (list === "relations") return json(200, { ...this.relationView(s, items[i] as KeptRelation, engine.version()) });
      const v = engine.version();
      return json(200, { ...items[i], stale: v === null || items[i].mesh_version !== v });
    }
    if (a && is("DELETE", 1)) {
      const { list, i } = this.find(s, a);
      s[list].splice(i, 1);
      if (list === "features") {
        s.relations = s.relations.filter((r) => r.a !== a && r.b !== a);
        s.faces.delete(a);
        s.outlines.delete(a);
      }
      return json(204, null);
    }
    // nominal values (nominal.ts)
    if (a && b === "nominal" && is("GET", 2)) {
      const size = body.value === undefined || body.value === null ? undefined : body.value;
      if (size !== undefined && size !== "diameter" && size !== "radius") bad(`a fit's nominal value is its diameter or its radius, not ${String(size)}`);
      const withBias = body.with_bias === true || body.with_bias === "true";
      return json(200, this.nominalAnswer(s, a, size as "diameter" | "radius" | undefined, withBias));
    }
    if (a && b === "nominal" && is("POST", 2)) return json(200, this.approveNominal(s, a, body));
    if (a && b === "move" && is("PUT", 2)) {
      const r = this.chooseMove(s, a, body.move);
      return json(200, { ...this.relationView(s, r, engine.version()), nominals: this.nominalViews(s, a) });
    }
    if (a && b === "nominal" && rest[2] && is("DELETE", 3)) {
      this.removeNominal(s, a, rest[2]);
      return json(204, null);
    }
    if (a && b === "faces" && is("GET", 2)) {
      const f = s.features.find((x) => x.id === a);
      const faces = s.faces.get(a);
      if (!f || faces === undefined) throw new HttpError(404, `No kept region for “${a}”`, "Read the list again.");
      return json(200, { id: a, mesh_version: f.mesh_version, n_faces: f.n_faces, faces });
    }
    if (a && b === "outline" && is("GET", 2)) {
      const f = s.features.find((x) => x.id === a);
      if (!f) throw new HttpError(404, `No kept fit “${a}”`, "Read the list again.");
      const o = s.outlines.get(a);
      if (o) return json(200, outlineAnswer(a, f.mesh_version, o));
      if (f.extent?.kind === "hole" && f.extent.edge) return json(200, { id: a, mesh_version: f.mesh_version, kind: "hole", edge: f.extent.edge });
      throw new HttpError(404, `No outline kept for “${a}”`, f.extent ? "A cylinder has none: its span and arc are in its extent." : "Fit it again.");
    }
    return undefined;
  }

  private keepFit(s: Stored, body: Record<string, unknown>, engine: FeatureEngine) {
    const face = body.face;
    if (typeof face !== "number" || !Number.isInteger(face) || face < 0) bad("face: must be a whole number of at least 0");
    if (!isVec3(body.point)) bad("point: must be three numbers");
    const tol = body.tolerance_mm;
    if (tol !== undefined && tol !== null && !(typeof tol === "number" && tol > 0 && tol <= 10)) bad("tolerance_mm: must be above 0 and at most 10");
    const given = body.name === undefined || body.name === null ? undefined : cleanName(body.name);
    const r = engine.fit(body);
    const id = newId("f");
    const f: Omit<KeptFeature, "stale"> = {
      id,
      name: given ?? nextName(s.features, "Fit"),
      kind: r.kind,
      shape: r.shape,
      seed: { kind: r.kind, face: face as number, point: [...(body.point as [number, number, number])], tolerance_mm: typeof tol === "number" ? tol : null },
      n_faces: r.n_faces,
      tolerance_mm: r.tolerance_mm,
      mean_sigma_mm: r.mean_sigma_mm,
      correlated_points: r.correlated_points ?? null,
      warnings: [...r.warnings],
      mesh_version: r.version,
      extent: this.measure(s, id, r, engine),
      form: engine.form?.(r) ?? null,
      created: nowIso(),
    };
    s.features.push(f);
    s.faces.set(id, r.faces);
    return { ...f, stale: false, faces: r.faces };
  }

  private keepMeasurement(s: Stored, body: Record<string, unknown>, engine: FeatureEngine): KeptMeasurement {
    if (!isVec3(body.a)) bad("a: must be three numbers");
    if (!isVec3(body.b)) bad("b: must be three numbers");
    const v = body.version;
    if (v !== undefined && v !== null && !(typeof v === "number" && Number.isInteger(v) && v >= 0)) bad("version: must be a whole number");
    const current = engine.version();
    const m: Omit<KeptMeasurement, "stale"> = {
      id: newId("m"),
      name: cleanName(body.name, nextName(s.measurements, "Distance")),
      a: [...(body.a as [number, number, number])],
      b: [...(body.b as [number, number, number])],
      mesh_version: typeof v === "number" ? v : current,
      created: nowIso(),
    };
    s.measurements.push(m);
    return { ...m, stale: current === null || m.mesh_version !== current };
  }

  private keepRelation(s: Stored, body: Record<string, unknown>): KeptRelation {
    const { a, b } = body;
    if (typeof a !== "string" || typeof b !== "string") return bad("a, b: the ids of two kept fits");
    for (const ref of [a, b]) if (!s.features.some((f) => f.id === ref)) bad(`No kept fit “${ref}” to relate`, "Keep both fits first, then relate them.");
    if (a === b) bad("A fit can't be related to itself", "Pick two different fits.");
    const r: KeptRelation = { id: newId("r"), name: cleanName(body.name, nextName(s.relations, "Relation")), a, b, created: nowIso() };
    s.relations.push(r);
    return r;
  }

  /** `GET .../features/relate?a=&b=`: two kept fits' relation, not kept. */
  private relate(s: Stored, body: Record<string, unknown>, engine: FeatureEngine): PairRelation {
    const { a, b } = body;
    if (typeof a !== "string" || typeof b !== "string") return bad("a, b: the ids of two kept fits (?a=…&b=…)");
    for (const ref of [a, b]) if (!s.features.some((f) => f.id === ref)) throw new HttpError(404, `No kept fit “${ref}”`, "Read the list again.");
    if (a === b) bad("A fit can't be related to itself", "Pick two different fits.");
    const v = this.relationView(s, { a, b }, engine.version());
    return { a, b, stale: v.stale, relation: v.relation ?? null };
  }

  private buildFrame(s: Stored, body: Record<string, unknown>, engine: FeatureEngine) {
    const version = engine.version();
    if (version === null) throw new HttpError(409, "This scan has no mesh yet", "Make the mesh, fit the features, then build the frame.");
    const inputs = (["primary", "secondary", "origin"] as const).map((role): FrameInput => {
      const ref = body[role];
      if (role === "origin" && ref && typeof ref === "object" && !Array.isArray(ref)) {
        const pt = ref as Record<string, unknown>;
        if (!isVec3(pt.point)) bad("origin.point: must be three numbers");
        const sg = pt.sigma_mm;
        if (sg !== undefined && sg !== null && !(typeof sg === "number" && sg >= 0)) bad("origin.sigma_mm: must be 0 or more");
        return { point: [...(pt.point as [number, number, number])], sigma_mm: typeof sg === "number" ? sg : null };
      }
      if (typeof ref !== "string") return bad(`${role}: the id of a kept fit`);
      const f = s.features.find((x) => x.id === ref);
      if (!f) return bad(`No kept fit “${ref}” for the ${role}`, "Pick one of the kept fits.");
      if (f.mesh_version !== version)
        throw new HttpError(409, `The ${role} “${f.name}” was fitted on an earlier mesh (version ${f.mesh_version}, the mesh is now version ${version})`, "Fit it again first, then build the frame.");
      return { feature: f };
    }) as [FrameInput, FrameInput, FrameInput];
    for (const k of ["flip_primary", "flip_secondary", "secondary_zero"])
      if (body[k] !== undefined && typeof body[k] !== "boolean") bad(`${k}: must be true or false`);
    if (body.axes !== undefined && typeof body.axes !== "string") bad("axes: must be text, e.g. \"ZX\"");
    s.frame = mockPartFrame(inputs, body as { axes?: string; flip_primary?: boolean; flip_secondary?: boolean; secondary_zero?: boolean });
    return this.frameView(s);
  }

  private frameMesh(s: Stored, name: string, engine: FeatureEngine): Response {
    if (name !== "mesh.ply" && name !== "mesh.stl") throw new HttpError(404, `No file “${name}” in the part's frame`, "Available: mesh.ply, mesh.stl.");
    if (!s.frame) throw new HttpError(404, "This scan has no part's frame", "Build one first.");
    const buf = engine.partMesh?.(s.frame.matrix, name) ?? null;
    if (!buf) throw new HttpError(404, `${name} isn't there yet`, "Make the mesh first.");
    return new Response(buf, {
      status: 200,
      headers: { "content-type": "application/octet-stream", "content-disposition": `attachment; filename="mesh-part-frame.${name.slice(5)}"` },
    });
  }

  private refit(s: Stored, body: Record<string, unknown>, engine: FeatureEngine) {
    const ids = body.ids;
    if (ids !== undefined && ids !== null && !(Array.isArray(ids) && ids.every((x) => typeof x === "string"))) bad("ids: must be a list of ids");
    const wanted = Array.isArray(ids) ? (ids as string[]) : null;
    for (const id of wanted ?? []) if (!s.features.some((f) => f.id === id)) throw new HttpError(404, `No kept fit “${id}”`, "Read the list again.");
    const version = engine.version();
    if (version === null) throw new HttpError(409, "This scan has no mesh yet", "Make the mesh first.");
    const refitted: string[] = [];
    const failed: { id: string; detail: string; hint: string | null }[] = [];
    s.features.forEach((f, i) => {
      // stale, or (where the engine measures extents) kept without one: then from its own click on its own mesh
      const unmeasured = !!engine.extent && f.extent === null && f.mesh_version === version;
      if ((f.mesh_version === version && !unmeasured) || (wanted && !wanted.includes(f.id))) return;
      const face = unmeasured ? f.seed.face : engine.nearestFace(f.seed.point);
      let r: FitResult;
      try {
        r = engine.fit({ kind: f.kind, face, point: f.seed.point, tolerance_mm: f.seed.tolerance_mm, version });
      } catch (e) {
        if (!(e instanceof HttpError)) throw e;
        failed.push({ id: f.id, detail: e.detail, hint: e.hint });
        return;
      }
      s.features[i] = {
        ...f,
        shape: r.shape,
        seed: { ...f.seed, face },
        n_faces: r.n_faces,
        tolerance_mm: r.tolerance_mm,
        mean_sigma_mm: r.mean_sigma_mm,
        correlated_points: r.correlated_points ?? null,
        warnings: [...r.warnings],
        mesh_version: r.version,
        extent: this.measure(s, f.id, r, engine),
        form: engine.form?.(r) ?? null,
      };
      s.faces.set(f.id, r.faces);
      refitted.push(f.id);
    });
    return { features: this.view(s, version), refitted, failed };
  }
}
