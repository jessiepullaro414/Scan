/**
 * The demo engine's kept features (APP_API.md "Kept features"), like the engine's
 * `ohs/server/features.py`: fits, two-point measurements and relations kept per scan (in memory
 * here), renamed and deleted; each fit and measurement made on another mesh version than the
 * current one is marked `stale`, and "fit again" refits the stale fits from their saved clicks
 * on the current mesh (the seed's face found again as the face nearest the saved point). A fit
 * that can't be found again stays as it was, still marked. Nothing is refitted on its own.
 *
 * The fits themselves are the demo's fit route (`MockProcess.fit`), passed in as a `FeatureEngine`,
 * so this file has no mesh code and can be checked on its own (features.test.ts).
 *
 * The part's frame (`.../features/frame`, frame.test.ts): built from three kept fits with the simplified rule of
 * frame.ts, kept per scan, shown with whether it is still current (a fit it was built from deleted or changed),
 * removed; the current mesh in it comes from the engine (`partMesh`).
 */
import type { FeatureSet, FitResult, KeptFeature, KeptMeasurement, KeptRelation, PartFrameRecord } from "../types";
import { frameState, mockPartFrame, type FrameInput } from "./frame.ts";
import { HttpError, json } from "./host.ts";

/** What a scan's kept features need from the demo engine. */
export type FeatureEngine = {
  /** The fit route's answer for this body (throws HttpError as the route does). */
  fit: (body: Record<string, unknown>) => FitResult;
  /** The current mesh's version, null before there is a mesh. */
  version: () => number | null;
  /** The current mesh's face nearest a point (to find a saved click again after an edit). */
  nearestFace: (point: number[]) => number;
  /** The current mesh with its vertices and normals turned by `matrix` (4x4 rows), as `mesh.ply` or `mesh.stl`;
   * null before there is a mesh. */
  partMesh?: (matrix: number[][], name: "mesh.ply" | "mesh.stl") => ArrayBuffer | null;
};

type Stored = {
  features: Omit<KeptFeature, "stale">[];
  measurements: Omit<KeptMeasurement, "stale">[];
  relations: KeptRelation[];
  /** Each fit's region, base64 as the fit route gives it. */
  faces: Map<string, string>;
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
    if (!s) this.sets.set(key, (s = { features: [], measurements: [], relations: [], faces: new Map(), frame: null }));
    return s;
  }

  private view(s: Stored, version: number | null): FeatureSet {
    const stale = (v: number | null) => version === null || v !== version;
    return {
      format_version: 1,
      frame: this.frameView(s),
      mesh_version: version,
      features: s.features.map((f) => ({ ...f, stale: stale(f.mesh_version) })),
      measurements: s.measurements.map((m) => ({ ...m, stale: stale(m.mesh_version) })),
      relations: s.relations.map((r) => ({ ...r })),
    };
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
    if (a === "relations" && is("POST", 1)) return json(200, this.keepRelation(s, body));
    if (a === "refit" && is("POST", 1)) return json(200, this.refit(s, body, engine));
    if (a === "frame" && is("POST", 1)) return json(200, this.buildFrame(s, body, engine));
    if (a === "frame" && is("DELETE", 1)) {
      s.frame = null;
      return json(204, null);
    }
    if (a === "frame" && b && is("GET", 2)) return this.frameMesh(s, b, engine);
    if (a && is("PATCH", 1)) {
      const name = cleanName(body.name);
      const { list, i } = this.find(s, a);
      const items = s[list] as { name: string; mesh_version?: number | null }[];
      items[i] = { ...items[i], name };
      if (list === "relations") return json(200, items[i]);
      const v = engine.version();
      return json(200, { ...items[i], stale: v === null || items[i].mesh_version !== v });
    }
    if (a && is("DELETE", 1)) {
      const { list, i } = this.find(s, a);
      s[list].splice(i, 1);
      if (list === "features") {
        s.relations = s.relations.filter((r) => r.a !== a && r.b !== a);
        s.faces.delete(a);
      }
      return json(204, null);
    }
    if (a && b === "faces" && is("GET", 2)) {
      const f = s.features.find((x) => x.id === a);
      const faces = s.faces.get(a);
      if (!f || faces === undefined) throw new HttpError(404, `No kept region for “${a}”`, "Read the list again.");
      return json(200, { id: a, mesh_version: f.mesh_version, n_faces: f.n_faces, faces });
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
      extent: null,
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
      if (f.mesh_version === version || (wanted && !wanted.includes(f.id))) return;
      const face = engine.nearestFace(f.seed.point);
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
        extent: null,
      };
      s.faces.set(f.id, r.faces);
      refitted.push(f.id);
    });
    return { features: this.view(s, version), refitted, failed };
  }
}
