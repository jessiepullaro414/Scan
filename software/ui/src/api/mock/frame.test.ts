/**
 * Checks for the demo engine's part's frame (`npm test`; `features.ts` routes, `frame.ts` the simplified rule):
 * build from three kept fits, read back with whether it is current, refusals in words (422), a fit made on an
 * earlier mesh (409), out of date after a fit goes and current again after "Build again", removal, and the mesh
 * in the part's frame. Plain TypeScript that Node runs as it is (type stripping), no test framework.
 */
import type { FeatureSet, FitResult, FitShape, KeptFit, PartFrameView } from "../types";
import { pointToPart, readFrame } from "../../lib/partFrame.ts";
import { MockFeatures, type FeatureEngine } from "./features.ts";
import { HttpError } from "./host.ts";

let checks = 0;
function check(ok: boolean, what: string): void {
  checks++;
  if (!ok) throw new Error(`frame.test: ${what}`);
}
const near = (a: ArrayLike<number>, b: ArrayLike<number>, tol = 1e-9) => a.length === b.length && Array.from(a).every((x, i) => Math.abs(x - b[i]) <= tol);

/** A box's features by the clicked face: 1 its top, 2 a side, 3 a hole in the top, 4 its bottom, 5 a second hole. */
const SHAPES: Record<number, FitShape> = {
  1: { kind: "plane", point: [0, 0, 10], normal: [0, 0, 1], rms_mm: 0.01, flatness_mm: 0.05, n: 900, normal_sigma_deg: 0.03, offset_sigma_mm: 0.004 },
  2: { kind: "plane", point: [10, 0, 0], normal: [1, 0, 0], rms_mm: 0.01, flatness_mm: 0.05, n: 900, normal_sigma_deg: 0.03, offset_sigma_mm: 0.004 },
  3: { kind: "circle", center: [3, -2, 10], normal: [0, 0, 1], radius_mm: 3, diameter_mm: 6, rms_mm: 0.01, n: 80, radius_sigma_mm: 0.003, diameter_sigma_mm: 0.006, center_sigma_mm: 0.004 },
  4: { kind: "plane", point: [0, 0, -10], normal: [0, 0, 1], rms_mm: 0.01, flatness_mm: 0.05, n: 900, normal_sigma_deg: 0.03, offset_sigma_mm: 0.004 },
  5: { kind: "circle", center: [-5, 4, 10], normal: [0, 0, 1], radius_mm: 2, diameter_mm: 4, rms_mm: 0.01, n: 80, radius_sigma_mm: 0.003, diameter_sigma_mm: 0.006, center_sigma_mm: 0.004 },
};
let version: number | null = 3;
const mesh = { positions: new Float32Array([3, -2, 10, 13, -2, 10, 3, 8, 10]), normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]), indices: new Uint32Array([0, 1, 2]) };
let turned: { positions: Float32Array; normals: Float32Array } | null = null;
const engine: FeatureEngine = {
  version: () => version,
  nearestFace: () => 1,
  fit: (body): FitResult => {
    if (version === null) throw new HttpError(409, "This scan has no mesh yet", "Make the mesh first.");
    const shape = SHAPES[Number(body.face)];
    return { kind: shape.kind === "circle" ? "hole" : (shape.kind as "plane" | "cylinder"), shape, faces: "AQAAAA==", n_faces: 1, tolerance_mm: null, mean_sigma_mm: null, correlated_points: null, warnings: [], version: version! };
  },
  partMesh: (matrix, name) => {
    const P = new Float32Array(mesh.positions.length),
      N = new Float32Array(mesh.normals.length);
    for (let i = 0; i < P.length; i += 3)
      for (let r = 0; r < 3; r++) {
        P[i + r] = matrix[r][0] * mesh.positions[i] + matrix[r][1] * mesh.positions[i + 1] + matrix[r][2] * mesh.positions[i + 2] + matrix[r][3];
        N[i + r] = matrix[r][0] * mesh.normals[i] + matrix[r][1] * mesh.normals[i + 1] + matrix[r][2] * mesh.normals[i + 2];
      }
    turned = { positions: P, normals: N };
    return new TextEncoder().encode(name).buffer as ArrayBuffer;
  },
};

const mock = new MockFeatures();
const call = async <T>(method: string, rest: string[], body: Record<string, unknown> = {}): Promise<{ status: number; data: T; headers?: Headers }> => {
  let r: Response | undefined;
  try {
    r = mock.route(method, "p1/s1", rest, body, engine);
  } catch (e) {
    if (e instanceof HttpError) return { status: e.status, data: { detail: e.detail, hint: e.hint } as T };
    throw e;
  }
  if (!r) return { status: 404, data: undefined as T };
  const type = r.headers.get("content-type") ?? "";
  return { status: r.status, headers: r.headers, data: r.status === 204 ? (undefined as T) : type.includes("json") ? ((await r.json()) as T) : ((await r.arrayBuffer()) as T) };
};
const keep = async (face: number, name: string) => (await call<KeptFit>("POST", [], { kind: "plane", face, point: [face, 0, 0], name })).data.id;
const detail = (d: unknown) => (d as { detail: string }).detail;

const top = await keep(1, "Top"),
  side = await keep(2, "Side"),
  hole = await keep(3, "Hole"),
  bottom = await keep(4, "Bottom"),
  dowel = await keep(5, "Dowel");
check((await call<FeatureSet>("GET", [])).data.frame === null, "no frame to start with");
check((await call("GET", ["frame", "mesh.ply"])).status === 404, "no frame: no mesh in it");

// ------------------------------------------------------------------ build, read back
{
  const r = await call<PartFrameView>("POST", ["frame"], { primary: top, secondary: side, origin: hole });
  check(r.status === 200 && r.data.out_of_date === false && r.data.readable === true, "built, current");
  const f = readFrame(r.data);
  check(!!f, "a frame the viewer can read");
  check(near(pointToPart(f!, [3, -2, 10]), [0, 0, 0]), "the hole's centre is the origin");
  check(near(pointToPart(f!, [10, 5, 3]).slice(0, 1), [-7]), "the side face 7 mm from the hole, X pointing from it into the part");
  check(near(pointToPart(f!, [3, -2, 0]), [0, 0, 10]), "Z points from the top face into the part");
  check(f!.built_from.map((s) => s.id).join() === [top, side, hole].join() && f!.built_from[2].name === "Hole", "built from the three fits");
  check(f!.axes.primary === "Z" && f!.axes.secondary === "X" && f!.axes.third === "Y", "ZX by default");
  const s = (await call<FeatureSet>("GET", [])).data;
  check(JSON.stringify(s.frame) === JSON.stringify(r.data), "read back the same");
  const flipped = await call<PartFrameView>("POST", ["frame"], { primary: top, secondary: side, origin: hole, flip_primary: true, axes: "XY" });
  const g = readFrame(flipped.data)!;
  check(flipped.status === 200 && g.flip_primary && g.axes.primary === "X" && g.axes.third === "Z", "the options are kept");
  const det =
    g.matrix[0][0] * (g.matrix[1][1] * g.matrix[2][2] - g.matrix[1][2] * g.matrix[2][1]) -
    g.matrix[0][1] * (g.matrix[1][0] * g.matrix[2][2] - g.matrix[1][2] * g.matrix[2][0]) +
    g.matrix[0][2] * (g.matrix[1][0] * g.matrix[2][1] - g.matrix[1][1] * g.matrix[2][0]);
  check(Math.abs(det - 1) < 1e-9, "still right-handed");
  const pt = await call<PartFrameView>("POST", ["frame"], { primary: top, secondary: side, origin: { point: [-8, -8, 10], sigma_mm: 0.01 } });
  check(pt.status === 200 && pt.data.built_from[2].id === null && near(pointToPart(pt.data, [-8, -8, 10]), [0, 0, 0]), "a point as the origin");
}

// ------------------------------------------------------------------ refusals
{
  const notPlane = await call("POST", ["frame"], { primary: hole, secondary: side, origin: hole });
  check(notPlane.status === 422 && detail(notPlane.data).includes("primary must be a plane"), "the primary must be a plane, in words");
  const parallel = await call("POST", ["frame"], { primary: top, secondary: bottom, origin: hole });
  check(parallel.status === 422 && detail(parallel.data).includes("parallel"), "a parallel secondary face");
  const across = await call("POST", ["frame"], { primary: top, secondary: dowel, origin: side });
  check(across.status === 422 && detail(across.data).includes("origin"), "a hole across the face with a face as the origin");
  check((await call("POST", ["frame"], { primary: top, secondary: dowel, origin: hole })).status === 200, "plane, hole, hole");
  check((await call("POST", ["frame"], { primary: top, secondary: side, origin: "f-nope" })).status === 422, "an unknown fit");
  check((await call("POST", ["frame"], { primary: top, secondary: side })).status === 422, "a missing slot");
  check((await call("POST", ["frame"], { primary: top, secondary: side, origin: hole, axes: "XX" })).status === 422, "axes named twice");
}

// ------------------------------------------------------------------ out of date, build again, the mesh, remove
{
  await call("POST", ["frame"], { primary: top, secondary: side, origin: hole });
  await call("PATCH", [hole], { name: "Dowel hole" });
  check((await call<FeatureSet>("GET", [])).data.frame!.out_of_date === false, "a rename changes nothing");
  check((await call("DELETE", [side])).status === 204, "a fit it was built from is deleted");
  const s = (await call<FeatureSet>("GET", [])).data.frame as unknown as PartFrameView;
  check(s.out_of_date === true && s.out_of_date_reasons.length === 1 && s.out_of_date_reasons[0].includes("Side") && s.out_of_date_reasons[0].includes("deleted"), "out of date, and why");
  const side2 = await keep(2, "Side again");
  const again = await call<PartFrameView>("POST", ["frame"], { primary: top, secondary: side2, origin: hole });
  check(again.status === 200 && again.data.out_of_date === false, "built again: current");

  const ply = await call<ArrayBuffer>("GET", ["frame", "mesh.ply"]);
  check(ply.status === 200 && new TextDecoder().decode(ply.data) === "mesh.ply", "the PLY in the part's frame");
  check((ply.headers?.get("content-disposition") ?? "").includes("part-frame"), "named so it isn't taken for the scan's file");
  const t = turned as { positions: Float32Array; normals: Float32Array } | null; // set by partMesh above
  check(!!t && near(t.positions.slice(0, 3), [0, 0, 0], 1e-5) && near(t.normals.slice(0, 3), [0, 0, -1], 1e-6), "vertices and normals turned into it");
  check((await call("GET", ["frame", "mesh.stl"])).status === 200 && (await call("GET", ["frame", "mesh.obj"])).status === 404, "STL too, nothing else");

  version = 4; // an edit: the fits are now stale
  const stale = await call("POST", ["frame"], { primary: top, secondary: side2, origin: hole });
  check(stale.status === 409 && detail(stale.data).includes("earlier mesh"), "a fit made on an earlier mesh: 409");
  check((await call<FeatureSet>("GET", [])).data.frame!.out_of_date === false, "the mesh changing alone doesn't make it out of date");

  check((await call("DELETE", ["frame"])).status === 204 && (await call<FeatureSet>("GET", [])).data.frame === null, "removed");
  check((await call("DELETE", ["frame"])).status === 204, "removing twice is fine");
  check((await call<FeatureSet>("GET", [])).data.features.length === 5, "the fits stay");
}

console.log(`frame.test: ${checks} checks passed`);
